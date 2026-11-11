"""AST-based parser for the customer's ``simulate.py`` Pydantic I/O schemas.

When the customer's ``invoke_pipeline`` (or ``invoke_agent``) hook is
type-annotated with Pydantic models, this module extracts the field
shape into structured YAML files (``inputs_schema.yaml`` /
``outputs_schema.yaml``) that the Plurai backend's scenario-gen
extraction LLM consumes as an authoritative contract — eliminating
the shape-drift class of bugs where the LLM had to infer field names
+ types from prose.

Two-stage strategy:

1. **AST-only stage** (no execution): parse ``simulate.py`` to find the
   typed signature of the hook function. Resolve the type-annotation
   class name to a sibling source file via import-statement walking.
   Parse THAT file to find the Pydantic class definition + extract its
   field declarations (name, annotation, default, ``Field(description=)``).

2. **Subprocess fallback** (when AST extraction misses inherited or
   computed fields): spawn a worker Python that imports the model module
   in isolation and calls ``BaseModel.model_fields``. Uses the customer's
   Python interpreter (so their dependencies resolve) but quarantines
   side effects to the subprocess.

The AST stage handles the common case (one Pydantic class, fields
declared inline). The subprocess fallback handles inheritance + plugins
+ unusual field types. When BOTH fail, the parser returns a clean
"no schema extracted; type-annotate ``invoke_pipeline`` and re-run"
message, and the downstream scenario-gen path falls through to today's
prose-only inference.

Usage from the SKILL.md::

    python parse_pydantic_io.py --simulate <path> --out-dir <plurai_dir>

Writes ``inputs_schema.yaml`` and ``outputs_schema.yaml`` to
``<plurai_dir>`` (or nothing + an exit code != 0 when no types were
discoverable).
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Hook names the backend recognizes. The plugin's simulate-setup templates
# emit one of these as the customer's pipeline entry point.
_HOOK_NAMES = ('invoke_pipeline', 'invoke_agent')

# Builtin / typing names that are NOT extractable as customer-defined
# Pydantic classes. When the signature uses one of these, we report
# "no type info" rather than trying (and failing) to resolve an import.
_NON_EXTRACTABLE_ANNOTATIONS = frozenset({
    'dict', 'list', 'tuple', 'set', 'frozenset',
    'str', 'int', 'float', 'bool', 'bytes',
    'None', 'Any', 'object',
    'Dict', 'List', 'Tuple', 'Set', 'Optional', 'Union', 'Literal',
})


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """One Pydantic field extracted from the source."""

    name: str
    type_annotation: str       # Raw annotation source (e.g. "list[str]", "Literal['a','b']")
    required: bool
    description: str | None = None
    default_repr: str | None = None  # Source text of the default literal, when present


@dataclass
class ModelSchema:
    """A Pydantic model's extracted shape."""

    class_name: str
    source_file: str           # Path that defined the class
    fields: list[FieldSpec] = field(default_factory=list)


@dataclass
class ParsedSchemas:
    """Output of :func:`parse_simulate_py_schemas`.

    ``inputs`` / ``outputs`` are populated when extraction succeeded;
    ``messages`` collects per-hook diagnostic lines (always present so
    the calling skill can surface them to the user).
    """

    inputs: ModelSchema | None = None
    outputs: ModelSchema | None = None
    messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _annotation_class_name(annotation: ast.expr | None) -> str | None:
    """Return a class name from a type annotation, or None if it isn't a bare name.

    Handles ``X`` (Name) but NOT ``X | None`` / ``Optional[X]`` / ``dict``
    — those return None so the caller falls through to the bare-dict path.
    The point is to identify a SINGLE customer-defined Pydantic class to
    introspect; union types + builtins are not extractable.
    """
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    # ast.Attribute case (e.g. ``models.SampleGenerationInput``) — take the leaf.
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _find_hook(tree: ast.Module, hook_name: str) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    """Return the FunctionDef for ``hook_name`` at module level, or None."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == hook_name:
            return node
    return None


def _hook_input_type(hook: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """Return the type-annotation class name of the hook's first param, or None.

    The backend hooks take a single positional ``inputs`` parameter (per the
    plugin's simulate.py template). If the customer renamed or polluted
    the signature, we fall back to the first non-self positional arg.
    """
    if not hook.args.args:
        return None
    return _annotation_class_name(hook.args.args[0].annotation)


def _hook_return_type(hook: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """Return the return-type-annotation class name, or None."""
    return _annotation_class_name(hook.returns)


def _resolve_import_path(simulate_path: Path, tree: ast.Module, class_name: str) -> Path | None:
    """Find the source file that defines ``class_name`` based on simulate.py's imports.

    Walks ``from X import Y`` and ``from X import Y as Z`` statements in
    simulate.py looking for an alias matching ``class_name``. Resolves
    the dotted module name to a filesystem path relative to simulate.py.

    Limitations (v1):
    - Doesn't follow re-exports through ``__init__.py`` — if the class is
      exported from a package but defined in a submodule, the parser may
      land in the wrong file. Falls through to subprocess in that case.
    - Doesn't handle absolute / installed-package imports.
    """
    candidate_modules: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None:
            continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            if local_name == class_name:
                candidate_modules.append(node.module)
                # Don't break — the customer might have stale imports.
                # Prefer the LAST matching import (Python semantics).

    if not candidate_modules:
        return None
    module = candidate_modules[-1]

    # Resolve dotted module to a path relative to simulate.py's parent.
    # ``a.b.c`` → ``a/b/c.py`` (or ``a/b/c/__init__.py``).
    parts = module.split('.')
    base = simulate_path.parent
    for parent_steps in range(0, 5):
        # Try resolving relative to simulate.py's dir, then parents (up to 5
        # levels) to handle nested package layouts. The plugin's typical
        # layout has plurai_sim/simulate.py + models in a sibling agent/ pkg.
        candidate_dir = base
        for _ in range(parent_steps):
            candidate_dir = candidate_dir.parent
        candidate = candidate_dir.joinpath(*parts).with_suffix('.py')
        if candidate.is_file():
            return candidate
        # Try as package __init__.py
        candidate = candidate_dir.joinpath(*parts) / '__init__.py'
        if candidate.is_file():
            return candidate

    return None


def _extract_class_definition(tree: ast.Module, class_name: str) -> ast.ClassDef | None:
    """Walk a module AST and return the ``ClassDef`` matching ``class_name``."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def _is_pydantic_field_default(value: ast.expr | None) -> bool:
    """Return True when the default is ``Field(...)`` from pydantic.

    Doesn't check the import resolves to pydantic specifically; the
    caller has already established this is a Pydantic class (subclass of
    BaseModel). False positives (someone's own ``Field`` helper) are
    acceptable — we just over-extract.
    """
    if value is None:
        return False
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'Field':
        return True
    return False


def _field_required_from_default(default: ast.expr | None) -> bool:
    """Pydantic v2 convention: ``Field(...)`` literal sentinel marks required.

    Returns True when:
    - No default (default IS None — bare annotation), OR
    - ``Field(...)`` where the first positional arg is the Ellipsis literal.
    """
    if default is None:
        return True
    if _is_pydantic_field_default(default):
        # type: ignore[union-attr]
        first_arg = default.args[0] if default.args else None  # type: ignore[union-attr]
        if isinstance(first_arg, ast.Constant) and first_arg.value is Ellipsis:
            return True
    return False


def _extract_field_description(default: ast.expr | None) -> str | None:
    """Extract the ``description="..."`` keyword from a ``Field(...)`` call."""
    if not _is_pydantic_field_default(default):
        return None
    for kw in default.keywords:  # type: ignore[union-attr]
        if kw.arg == 'description' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _annotation_source(annotation: ast.expr | None, source_text: str) -> str:
    """Return the source text for an annotation, or '' when absent."""
    if annotation is None:
        return ''
    return ast.get_source_segment(source_text, annotation) or ''


def _default_source(default: ast.expr | None, source_text: str) -> str | None:
    """Return the source text for a default value, or None."""
    if default is None:
        return None
    return ast.get_source_segment(source_text, default)


def _extract_fields_ast(class_def: ast.ClassDef, source_text: str) -> list[FieldSpec]:
    """Walk a Pydantic class body extracting one FieldSpec per typed attribute."""
    fields: list[FieldSpec] = []
    for stmt in class_def.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        if name.startswith('_'):
            # Pydantic + dataclass convention: leading-underscore fields are private.
            continue
        annotation_src = _annotation_source(stmt.annotation, source_text)
        description = _extract_field_description(stmt.value)
        required = _field_required_from_default(stmt.value)
        default_src = _default_source(stmt.value, source_text)
        fields.append(FieldSpec(
            name=name,
            type_annotation=annotation_src,
            required=required,
            description=description,
            default_repr=default_src,
        ))
    return fields


# ---------------------------------------------------------------------------
# Subprocess fallback — Pydantic introspection when AST alone won't do
# ---------------------------------------------------------------------------


_SUBPROCESS_SNIPPET = '''
import importlib, json, sys

module_path = {module_path!r}
class_name = {class_name!r}
sys.path.insert(0, {sys_path_root!r})

try:
    mod = importlib.import_module(module_path)
except Exception as e:
    print(json.dumps({{'error': f'import failed: {{type(e).__name__}}: {{e}}'}}))
    sys.exit(2)

cls = getattr(mod, class_name, None)
if cls is None:
    print(json.dumps({{'error': f'class {{class_name!r}} not found in {{module_path!r}}'}}))
    sys.exit(3)

if not hasattr(cls, 'model_fields'):
    print(json.dumps({{'error': f'class {{class_name!r}} has no model_fields (not a Pydantic v2 model?)'}}))
    sys.exit(4)

out = []
for fname, fld in cls.model_fields.items():
    out.append({{
        'name': fname,
        'type_annotation': str(fld.annotation),
        'required': fld.is_required(),
        'description': fld.description,
        'default_repr': repr(fld.default) if not fld.is_required() else None,
    }})

print(json.dumps({{'fields': out, 'class_name': class_name}}))
'''


def _subprocess_introspect(
    *, module_path: str, class_name: str, sys_path_root: Path, python_exe: str,
) -> tuple[list[FieldSpec] | None, str | None]:
    """Spawn a Python subprocess that imports + introspects a Pydantic class.

    Returns ``(fields, None)`` on success, ``(None, error_message)`` on
    failure. The subprocess crash is contained — if it fails, the caller
    falls through to "no schema" with the error surfaced to the customer.
    """
    snippet = _SUBPROCESS_SNIPPET.format(
        module_path=module_path,
        class_name=class_name,
        sys_path_root=str(sys_path_root.resolve()),
    )
    try:
        proc = subprocess.run(
            [python_exe, '-c', snippet],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f'subprocess failed: {type(e).__name__}: {e}'
    if proc.returncode != 0:
        try:
            payload = json.loads(proc.stdout.strip() or '{}')
            err = payload.get('error') or proc.stderr.strip() or 'unknown failure'
        except json.JSONDecodeError:
            err = proc.stderr.strip() or proc.stdout.strip() or 'unknown failure'
        return None, err

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return None, f'could not parse subprocess output: {e}'

    fields = [
        FieldSpec(
            name=f['name'],
            type_annotation=f['type_annotation'],
            required=f['required'],
            description=f.get('description'),
            default_repr=f.get('default_repr'),
        )
        for f in payload.get('fields', [])
    ]
    return fields, None


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def _hook_kind_label(hook_name: str) -> str:
    return 'inputs' if hook_name in ('invoke_pipeline', 'invoke_agent') else hook_name


def _extract_model_schema(
    *,
    simulate_path: Path,
    simulate_tree: ast.Module,
    class_name: str,
    role: str,
    python_exe: str,
    messages: list[str],
) -> ModelSchema | None:
    """End-to-end extraction for one model class referenced from simulate.py.

    ``role`` is 'inputs' or 'outputs' for diagnostic messages.
    """
    model_path = _resolve_import_path(simulate_path, simulate_tree, class_name)
    if model_path is None:
        messages.append(
            f'{role}: type {class_name!r} referenced but its import could not be '
            f'resolved to a source file (looked at relative paths up to 5 '
            f'parents). Add a clearer import line to simulate.py or use '
            f'a fully-qualified module path.'
        )
        return None

    try:
        model_source = model_path.read_text(encoding='utf-8')
        model_tree = ast.parse(model_source, filename=str(model_path))
    except (OSError, SyntaxError) as e:
        messages.append(
            f'{role}: could not parse {model_path}: {type(e).__name__}: {e}'
        )
        return None

    class_def = _extract_class_definition(model_tree, class_name)
    if class_def is None:
        messages.append(
            f'{role}: {class_name!r} not found as a class in {model_path}'
        )
        return None

    fields = _extract_fields_ast(class_def, model_source)

    # If AST extraction surfaced no fields (e.g., a class with all inherited
    # fields), fall back to subprocess introspection. The customer's
    # interpreter is queried so any third-party Pydantic plugins resolve.
    if not fields:
        # Try to derive the dotted module path from the file location.
        # Heuristic: walk up to the nearest __init__.py-free ancestor and
        # use the path from there.
        module_dotted = _file_to_module_path(model_path)
        if module_dotted is None:
            messages.append(
                f'{role}: AST extraction produced no fields for {class_name!r} '
                f'and could not derive a dotted module path for subprocess '
                f'introspection.'
            )
            return ModelSchema(class_name=class_name, source_file=str(model_path), fields=[])

        sys_path_root = _find_sys_path_root(model_path)
        sub_fields, err = _subprocess_introspect(
            module_path=module_dotted,
            class_name=class_name,
            sys_path_root=sys_path_root,
            python_exe=python_exe,
        )
        if sub_fields is None:
            messages.append(
                f'{role}: AST extraction empty; subprocess fallback also failed: {err}'
            )
            return ModelSchema(class_name=class_name, source_file=str(model_path), fields=[])
        fields = sub_fields

    return ModelSchema(class_name=class_name, source_file=str(model_path), fields=fields)


def _file_to_module_path(path: Path) -> str | None:
    """Walk up the filesystem to find the nearest non-package ancestor.

    Returns a dotted path like ``agent.agents.<component>.models``.
    Returns None when the file isn't inside a package (no ``__init__.py``).
    """
    if path.name == '__init__.py':
        parts: list[str] = []
        cur = path.parent
    else:
        parts = [path.stem]
        cur = path.parent
    while cur.parent != cur:
        if (cur / '__init__.py').is_file():
            parts.insert(0, cur.name)
            cur = cur.parent
        else:
            break
    if not parts:
        return None
    return '.'.join(parts)


def _find_sys_path_root(model_path: Path) -> Path:
    """Find the package root to insert on sys.path for subprocess import.

    Walks up until the first ancestor WITHOUT an __init__.py — that's the
    root that needs to be on sys.path for the dotted module name to resolve.
    """
    cur = model_path.parent
    while cur.parent != cur:
        if (cur / '__init__.py').is_file():
            cur = cur.parent
        else:
            return cur
    return cur


def parse_simulate_py_schemas(
    simulate_path: Path,
    *,
    python_exe: str | None = None,
) -> ParsedSchemas:
    """Top-level entry: parse ``simulate.py`` and emit Pydantic-derived schemas.

    Args:
        simulate_path: absolute path to the customer's ``simulate.py``.
        python_exe: Python interpreter for the subprocess fallback. Defaults
            to ``sys.executable``, which is whatever the plugin shell is
            using — typically the customer's venv when invoked from the
            skill.
    """
    messages: list[str] = []
    if python_exe is None:
        python_exe = sys.executable

    try:
        source = simulate_path.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(simulate_path))
    except (OSError, SyntaxError) as e:
        messages.append(f'simulate.py could not be parsed: {type(e).__name__}: {e}')
        return ParsedSchemas(messages=messages)

    # Find the hook function (invoke_pipeline preferred; falls back to invoke_agent).
    hook: ast.AsyncFunctionDef | ast.FunctionDef | None = None
    hook_name: str | None = None
    for candidate in _HOOK_NAMES:
        hook = _find_hook(tree, candidate)
        if hook is not None:
            hook_name = candidate
            break

    if hook is None or hook_name is None:
        messages.append(
            'simulate.py has neither invoke_pipeline nor invoke_agent at module '
            'level — nothing to introspect.'
        )
        return ParsedSchemas(messages=messages)

    inputs_class = _hook_input_type(hook)
    outputs_class = _hook_return_type(hook)

    # Treat builtin / typing aliases as "no extractable type". Without this
    # filter, ``inputs: dict`` would send the parser hunting for a class
    # ``dict`` in customer imports — guaranteed miss + misleading error.
    if inputs_class in _NON_EXTRACTABLE_ANNOTATIONS:
        inputs_class = None
    if outputs_class in _NON_EXTRACTABLE_ANNOTATIONS:
        outputs_class = None

    # The typed-I/O advice applies ONLY to invoke_pipeline: the dialog
    # contract FIXES invoke_agent's signature as a plain dict of LangChain messages
    # (payload={'messages': [...]}), so 'annotate as a Pydantic model' is unfollowable
    # there — the correct dialog signature is precisely what the hint would otherwise flag.
    if hook_name == 'invoke_agent':
        if inputs_class is None and outputs_class is None:
            messages.append(
                'invoke_agent is dict-typed BY DESIGN (the dialog contract passes '
                'a plain dict of LangChain messages) — no annotation change is '
                'needed or possible. Typed-I/O extraction applies only to '
                'invoke_pipeline benches.'
            )
    else:
        if inputs_class is None:
            messages.append(
                f'{hook_name}\'s first parameter has no class-named type annotation '
                f'(probably bare ``dict`` or a union type). For best scenario-gen '
                f'quality, annotate as a Pydantic model — e.g. '
                f'``async def {hook_name}(inputs: YourInputModel) -> YourOutputModel``.'
            )

        if outputs_class is None:
            messages.append(
                f'{hook_name}\'s return type has no class-named annotation. '
                f'For best deterministic_check generation, annotate the return '
                f'type as a Pydantic model.'
            )

    inputs_schema: ModelSchema | None = None
    outputs_schema: ModelSchema | None = None

    if inputs_class is not None:
        inputs_schema = _extract_model_schema(
            simulate_path=simulate_path,
            simulate_tree=tree,
            class_name=inputs_class,
            role='inputs',
            python_exe=python_exe,
            messages=messages,
        )
    if outputs_class is not None:
        outputs_schema = _extract_model_schema(
            simulate_path=simulate_path,
            simulate_tree=tree,
            class_name=outputs_class,
            role='outputs',
            python_exe=python_exe,
            messages=messages,
        )

    return ParsedSchemas(inputs=inputs_schema, outputs=outputs_schema, messages=messages)


# ---------------------------------------------------------------------------
# YAML rendering
# ---------------------------------------------------------------------------


def render_schema_yaml(schema: ModelSchema | None) -> str:
    """Render a ModelSchema as YAML text.

    Output shape (matches the contract the backend reads)::

        # Source: <model class>.<source_file>
        field_name:
          type: <annotation source>
          required: true | false
          description: <prose>
          default: <repr>  # only when not required
    """
    if schema is None or not schema.fields:
        return ''
    lines: list[str] = [
        f'# Source: {schema.class_name} (from {schema.source_file})',
        '',
    ]
    for fld in schema.fields:
        lines.append(f'{fld.name}:')
        lines.append(f'  type: {_yaml_scalar(fld.type_annotation)}')
        lines.append(f'  required: {"true" if fld.required else "false"}')
        if fld.description:
            lines.append(f'  description: {_yaml_scalar(fld.description)}')
        if not fld.required and fld.default_repr is not None:
            lines.append(f'  default: {_yaml_scalar(fld.default_repr)}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _yaml_scalar(value: str) -> str:
    """Render a scalar safely — double-quote when it contains tricky chars."""
    if not value:
        return "''"
    # If it contains newlines or quotes, use a block scalar.
    if '\n' in value:
        indent = '    '
        body = '\n'.join(indent + line for line in value.splitlines())
        return f'|-\n{body}'
    # Be conservative: quote anything that isn't a plain identifier-ish token.
    if any(c in value for c in ':#&*?!|>\'"%@`{}[],') or value.startswith(' ') or value.endswith(' '):
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')  # outside the f-string: a backslash in one is 3.12+
        return f'"{escaped}"'
    return value


# ---------------------------------------------------------------------------
# CLI entry point — called by the SKILL.md step
# ---------------------------------------------------------------------------


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        '--simulate', required=True, type=Path,
        help='Path to the customer simulate.py.',
    )
    parser.add_argument(
        '--out-dir', required=True, type=Path,
        help='Directory to write inputs_schema.yaml + outputs_schema.yaml.',
    )
    parser.add_argument(
        '--python-exe', default=None,
        help='Python interpreter for the subprocess fallback (defaults to sys.executable).',
    )
    args = parser.parse_args()

    if not args.simulate.is_file():
        print(f'error: simulate.py not found at {args.simulate}', file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    result = parse_simulate_py_schemas(args.simulate, python_exe=args.python_exe)

    # Emit messages on stderr so the calling shell can surface them.
    for msg in result.messages:
        print(f'parse_pydantic_io: {msg}', file=sys.stderr)

    wrote_anything = False
    if result.inputs is not None and result.inputs.fields:
        out = args.out_dir / 'inputs_schema.yaml'
        out.write_text(render_schema_yaml(result.inputs), encoding='utf-8')
        print(f'wrote {out}')
        wrote_anything = True

    if result.outputs is not None and result.outputs.fields:
        out = args.out_dir / 'outputs_schema.yaml'
        out.write_text(render_schema_yaml(result.outputs), encoding='utf-8')
        print(f'wrote {out}')
        wrote_anything = True

    if not wrote_anything:
        # Not an error — the customer's simulate.py simply isn't type-annotated
        # with extractable Pydantic models. The downstream scenario-gen path
        # falls back to the today's prose-only inference. Exit 2 to signal
        # "nothing emitted" without conflating with parse errors (exit 1).
        return 2

    return 0


if __name__ == '__main__':
    sys.exit(_main())
