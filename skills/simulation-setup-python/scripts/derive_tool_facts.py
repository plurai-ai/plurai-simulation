#!/usr/bin/env python3
"""Type each mocked tool's return, and its arguments' nullability, from the tool's real source.

    python derive_tool_facts.py plurai_sim/plurai.config.yaml --root . --write
    python derive_tool_facts.py plurai_sim/plurai.config.yaml --root . --check
    python derive_tool_facts.py plurai_sim/plurai.config.yaml --root . --check --json   # the facts as JSON

For every `mode: mock` tool, the script finds `descriptor.original_function` (a function, or
`Class.method` for a class-based tool) in the file that `descriptor.original_module` names, under
`--root` first and then on the agent interpreter's import path, so a library tool is read from its
installed source. The agent interpreter is the SDK child's: `PLURAI_CHILD_PYTHON` when set, else the
interpreter running this script. It reads the function's `return` statements with `ast`
(nothing is imported), following same-module helpers and `self.` methods up to three calls deep,
through `await` and `asyncio.to_thread(helper, ...)`; a LangGraph `Command(update={"messages":
[ToolMessage(...)]})` is typed by that ToolMessage's content. It writes these keys and nothing else:

  returns.type            str, json, json[list], json[dict], dict, list, bool, int, float, null,
                          a `|` union of those, or unknown. This is the closed vocabulary of
                          the backend's `descriptor_return_types` contract block.
  returns.type_source     code | annotation | declared_unknown (the script's own sources)
  returns.evidence        [file:line, ...]: the returns (or the def) the type was read from
  returns.unknown_reason  why an `unknown` could not be typed, e.g. the external call it passes through
  args.<a>.nullable       true for Optional[...], X | None, Union[..., None] or a `None` default
  returns.fields.<f>.fields, .item_fields, .optional, .wire_name   the field's nested shape, where a pydantic model, TypedDict or
                          dataclass of the agent's own code types it (or the whole return: then `returns.fields` or, for a
                          list of them, `returns.item_fields`, with the fields the config omits added). Nested keys are the keys
                          the model accepts; `optional: true` is a field with a default; `wire_name` names the only key accepted
                          where the config's own key is not; `truncated` says where a cycle or the size bound stopped the reading
  returns.fields.<f>.evidence   the file:line where a returned dict literal, TypedDict, dataclass or model names
                          the field, for a field the config lists and the code names; a field without one is still `declared`
  returns.fields.<f>.type, .type_source   the field's type read from code: `annotation` for an annotated TypedDict,
                          dataclass or model field (a `Literal[...]` keeps its values), `code` for a returned dict
                          literal whose value the AST types. A written spelling that is the code's type or a
                          refinement of it the AST cannot express (`json[...]` over `str`, `list[dict]` over `list`)
                          stays, with the code's source; a coarser one (`str` over `json`) is replaced. A written
                          `Literal[...]` whose values have the code's kinds stays as written, source included: the
                          code does not prove its members. A field the code cannot type gets no source: its
                          type is a guess the traces may replace. A field typed by a source that outranks the code
                          (`plugin`) is left as it is
  error_channel, error_channel_source   on the tool entry, beside `descriptor:`, where the tool's own body decides it:
                          `returns` when an `except` handler of its own returns and no raise of its own escapes, `raises`
                          when a raise escapes (outside every `try` body, or re-raised from a handler) and no handler
                          returns, with source `inferred`. Written over an `inferred` or absent source only. A body with
                          both or neither is left as written; helpers and decorators are not followed
  args.<a>.description, returns.description   only where the config leaves one empty and the tool's declaration
                          documents it: a Sphinx `:param <a>:` / `:return:` field or a Google `Args:` / `Returns:` entry, in
                          the function's docstring, then in `descriptor.description` (where a scan copies a `__doc__` the
                          code assigns at runtime). `description_source: declared` is written beside each. Text already
                          written is never replaced, and `descriptor.description` is left as it is. Leave a description you
                          have nothing beyond the declaration for EMPTY: a placeholder is text, so the declaration never fills it.
  scanned_at              at the top of the file: the scan time

`Model.model_validate(data)` and `Model(...)` on such a class return that class: `dict`, with its shape. A class is read
only from the agent's own files, never from an installed library. Source is found under `--root`, on the agent
interpreter's path, through an editable install's finder, then by the one file under `--root` that spells the module
(two or more segments, and never a choice between two). A module spelled `<module>.<Class>` beside a method is read as
`<module>` and `<Class>.<method>`, with a `!` note. A tool whose source is not found always prints its `?` line and is
counted in a summary line, even when a written `returns` block is kept.

The edit is spliced in at the YAML nodes' own positions, so the file's comments, ordering and the
scan's descriptions all survive. A return annotation fills an `unknown`, and where the code is typed
it must agree with it: a disagreement is reported and the code's type is kept.

`type_source` names the evidence behind the whole `returns` block, ranked best first (`_RANK`): `plugin` (a value
the plugin decided, which a re-scan keeps), then `code` and `annotation` (one rung), then `trace`, then `declared`, then `stub_run`, then `inferred`, then `declared_unknown`.
A source the model wrote (`trace`, `declared`, `stub_run`, `inferred`) is kept only while this script's derivation
does not outrank it. A typed derivation outranks all four: it replaces them, and a `trace` or `declared` kind it
contradicts (neither type refines the other) is reported as a conflict. A written type strictly finer than the code's,
a refinement the AST cannot express (`json[...]` over `str`, `json[list]` over `json`, `list[dict]` over `list`, one
alternative per wire kind on each side), keeps its spelling and takes the code's source and evidence, beside the
file:line citations it already had. A coarser one (`str` over `json`) is replaced by the code's type, without a
conflict. A written `Literal[...]` whose values have the code's kinds stays as written. A kept `declared` must cite a
file:line that exists on disk, and a kept `trace` the trace brief.

Facts it prints and never writes (also in the `--json` dump):
  declaration_sites   where the tool declares itself: its registered name as a string literal or YAML/JSON
                      scalar in the agent's non-test files (or its installed package), and the patched
                      function's docstring. Printed for a tool that derives `unknown`, a function two or
                      more tools share, or a `declared` source. At most five per tool are printed.

`returns.fields` or `returns.item_fields` is rewritten as one block when its nested shape changes: comments inside that
block are not kept, and a block holding a YAML anchor or alias is left as written, with a `!` note. A `plugin` field or
`returns` block is left as it is. `--check` counts a nested shape `--write` would change as a difference.

`--check` also names each description the declaration documents and the config leaves empty, as a `!` note: what
`--write` would fill. It never changes the exit code.

  row_sources         for a `seam: tool_body` read tool (no `writes_to`): [file:line, ...], each call rooted in a
                      non-stdlib import (a name it binds, or a value derived from one) whose result the tool, or a
                      helper it reaches, iterates (a `for` or comprehension source) or returns: the calls that
                      produce the rows it returns. A library default may narrow them (caldav `todos()` hides
                      completed tasks), so each line is answered by a `business_rules` `(source: <that line>)`
                      naming the stored values it keeps, or by a typed `skipped_code` entry `{cite: <that line>,
                      returns: every_row, evidence: <path>:<line>}` whose evidence is a line inside the called
                      function in the installed library; a range answers none. verify_config's
                      `row_source_uncited` names the rest (:func:`row_source_answer`).

Every line is advisory: the model chooses the seam and the evidence; nothing here is a gate. A static walk
cannot tell a call that leaves the process from an in-process library accessor, so `row_sources` lists
both kinds and the model reads the tool's code for the rest of its external calls. The last line counts the
tools whose return shape is read from code only (`inferred` or `declared_unknown`), the count the scan's
trace recommendation names.

A `plugin` value the code contradicts (a `returns` block or a field) is printed as a `!` note and kept: the
plugin decided it, so it never fails the run; so is a `plugin` error channel the code decides differently, while a
`declared` or `trace` one is a conflict. A `returns.fields` block several tools share through a YAML
alias is left as written, with a note naming the tools.

Exit codes: 0 when done; 1 when --check finds a declaration that differs from the derivation (a field's
`type` / `type_source` included, the same keys --write sets, and a written `error_channel` over an `inferred` or absent
source that the code decides differently; a tool that declares none is verify_config's `error_channel_untyped`), or
when either mode meets a conflict (an annotation or a model-written source that contradicts the code, a `declared`
citation that does not exist, an `original_function` that names a class); 2 on a usage error or an agent interpreter
that cannot report its import path.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

try:
    import yaml
except ImportError:
    print(f'ERROR: pyyaml is required to run derive_tool_facts.py; install it into {sys.executable}', file=sys.stderr)
    sys.exit(2)

MAX_HOPS = 3
_UNKNOWN = 'unknown:'
_FuncDef = Union[ast.FunctionDef, ast.AsyncFunctionDef]  # an alias is evaluated at runtime: `|` on types is 3.10+
_JSON_OF = {frozenset({'list'}): 'json[list]', frozenset({'dict'}): 'json[dict]'}
_STR_METHODS = frozenset({'join', 'format', 'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'replace', 'title', 'capitalize'})
_CONSTANT_KIND = {type(None): 'null', bool: 'bool', str: 'str', int: 'int', float: 'float'}
_CALL_KIND = {'str': 'str', 'repr': 'str', 'dict': 'dict', 'list': 'list', 'sorted': 'list', 'tuple': 'list', 'bool': 'bool', 'int': 'int', 'len': 'int', 'float': 'float'}
#: `returns.type_source` by evidence rank, best first: the ladder of the backend's `grounding_ledger` contract block.
#: `plugin` (a value the plugin decided) is above them all; `code` and `annotation` share a rung, since the AST derives both.
_RANK = {'plugin': 0, 'code': 1, 'annotation': 1, 'trace': 2, 'declared': 3, 'stub_run': 4, 'inferred': 5, 'declared_unknown': 6}
#: Sources whose replacement by a clashing code type is reported: a reading of a description or of production
#: traces that the code contradicts is evidence someone should see. A stub run or an inference is just replaced.
_REPORTED_WHEN_REPLACED = frozenset({'trace', 'declared'})
#: The sources this script derives itself, so it rewrites them whenever it re-derives.
_OWN_SOURCES = frozenset({'code', 'annotation', 'declared_unknown'})
_SITE_CAP = 5
_SKIP_DIRS = frozenset({'node_modules', '__pycache__', 'site-packages', 'dist', 'build', 'plurai_sim', 'tests', 'test', 'venv', 'env'})
_DECLARATION_KEY = re.compile(r'(?i)\b(description|returns?|schema|output)\b')
#: How deep, and over how many nodes per tool, a nested shape is read. Past either the block says `truncated`.
_SHAPE_DEPTH = 6
_SHAPE_NODES = 400
_SHAPE_BASES = frozenset({'BaseModel', 'TypedDict'})
_ENUM_BASES = frozenset({'Enum', 'StrEnum', 'IntEnum'})
_VALIDATE_METHODS = frozenset({'model_validate', 'model_validate_json', 'parse_obj'})
#: Annotations whose JSON form is a string.
_JSON_STR = frozenset({'datetime', 'AwareDatetime', 'NaiveDatetime', 'date', 'time', 'timedelta', 'UUID', 'Decimal', 'Path', 'AnyUrl', 'HttpUrl', 'EmailStr', 'SecretStr', 'bytes'})
#: The keys a nested reading owns on a `returns.fields` entry, so a re-derivation can drop what no longer holds.
_NESTED_KEYS = ('optional', 'wire_name', 'fields', 'item_fields', 'truncated')
_ANNOTATION_KIND = {
    'str': 'str', 'dict': 'dict', 'Dict': 'dict', 'Mapping': 'dict', 'list': 'list', 'List': 'list', 'Sequence': 'list',
    'tuple': 'list', 'Tuple': 'list', 'bool': 'bool', 'int': 'int', 'float': 'float', 'None': 'null', 'null': 'null', 'json': 'json',
}


# --- the derivation ---------------------------------------------------------------------------


def _own_nodes(fn: _FuncDef) -> list[ast.AST]:
    """Every node in `fn`'s own scope: nested defs, lambdas and classes are someone else's returns."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


_TRY = tuple({ast.Try, getattr(ast, 'TryStar', ast.Try)})  # `try ... except*` is 3.11+


def _error_channel(fn: _FuncDef) -> tuple[str | None, int | None]:
    """(channel, line): how the tool's OWN body reports a failure, where its structure alone decides it. `returns` when an
    `except` handler of its own returns and no raise of its own escapes; `raises` when one escapes (outside every `try`
    body, or re-raised from a handler) and no handler returns; None for both or neither. A raise inside a `try` body counts
    as caught. Helpers and decorators are not followed: a mock replaces the body under the decorators."""
    handler_return: int | None = None
    escaping_raise: int | None = None
    stack: list[tuple[ast.AST, bool, bool]] = [(s, False, False) for s in fn.body]  # (node, in a try body, in a handler)
    while stack:
        node, in_try, in_handler = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return) and in_handler:
            handler_return = min(node.lineno, handler_return or node.lineno)
        if isinstance(node, ast.Raise) and not in_try:
            escaping_raise = min(node.lineno, escaping_raise or node.lineno)
        if isinstance(node, _TRY):
            stack += [(s, True, in_handler) for s in node.body]
            stack += [(s, in_try, True) for h in node.handlers for s in h.body]
            stack += [(s, in_try, in_handler) for s in node.orelse + node.finalbody]
        else:
            stack += [(c, in_try, in_handler) for c in ast.iter_child_nodes(node)]
    if handler_return is not None and escaping_raise is None:
        return 'returns', handler_return
    if escaping_raise is not None and handler_return is None:
        return 'raises', escaping_raise
    return None, None


def _terminates(body: list[ast.stmt]) -> bool:
    """Whether control can NOT fall off the end of `body` (an approximation that errs towards `null`)."""
    last = body[-1] if body else None
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and bool(last.orelse) and _terminates(last.orelse)
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _terminates(last.body)
    if isinstance(last, ast.Try):
        return _terminates(last.orelse or last.body) and all(_terminates(h.body) for h in last.handlers)
    return isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value is True


class _Deriver:
    def __init__(self, functions: dict[str, _FuncDef], rel: str, shapes: _Shapes | None = None, path: Path | None = None) -> None:
        self.functions = functions
        self.rel = rel
        self.shapes = shapes
        self.path = path
        self.validated: list[ast.expr] = []  # the classes a returned `Model.model_validate(...)` / `Model(...)` names
        self.evidence: list[str] = []
        self.field_sites: dict[str, int] = {}
        self.field_kinds: dict[str, set[str]] = {}

    def _note_fields(self, expr: ast.expr, fn: _FuncDef, hops: int) -> None:
        """Where a returned dict literal names each of its keys (the line a field's citation points at), and the kinds of its values."""
        for literal in _dict_literals(expr, fn):
            for key, value in zip(literal.keys, literal.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.field_sites.setdefault(key.value, key.lineno)
                    # A fresh deriver: what typing the value walks is not this tool's return evidence.
                    self.field_kinds.setdefault(key.value, set()).update(_Deriver(self.functions, self.rel, self.shapes, self.path).kinds_of(value, fn, hops, frozenset()))

    def field_types(self) -> dict[str, tuple[str, str]]:
        """`{field: (type, 'code')}` for every field whose returned values the AST types; a field with an untyped value is left out."""
        return {name: ('|'.join(sorted(kinds)), 'code') for name, kinds in self.field_kinds.items() if kinds and not any(k.startswith(_UNKNOWN) for k in kinds)}

    def returns_of(self, fn: _FuncDef, hops: int) -> set[str]:
        kinds: set[str] = set()
        for node in _own_nodes(fn):
            if isinstance(node, ast.Return):
                self.evidence.append(f'{self.rel}:{node.lineno}')
                if node.value is not None:
                    self._note_fields(node.value, fn, hops)
                kinds |= {'null'} if node.value is None else self.kinds_of(node.value, fn, hops, frozenset())
        if not _terminates(fn.body):
            self.evidence.append(f'{self.rel}:{fn.end_lineno}')
            kinds.add('null')
        return kinds or {f'{_UNKNOWN}{fn.name}() never returns'}

    def _call(self, name: str, expr: ast.expr, hops: int) -> set[str]:
        helper = self.functions.get(name)
        if helper is None:
            return {f'{_UNKNOWN}passthrough: {ast.unparse(expr)[:120]}'}
        if hops >= MAX_HOPS:
            return {f'{_UNKNOWN}{name}() is more than {MAX_HOPS} calls deep'}
        return self.returns_of(helper, hops + 1)

    def _local(self, name: str, fn: _FuncDef, hops: int, seen: frozenset[str]) -> set[str]:
        values = _assigned(name, fn)
        if not values or name in seen:
            return {f'{_UNKNOWN}unparsed: {name}'}
        return set().union(*(self.kinds_of(v, fn, hops, seen | {name}) for v in values))

    def _command(self, expr: ast.Call, fn: _FuncDef, hops: int, seen: frozenset[str]) -> set[str]:
        """A LangGraph `Command`: the model reads the content of the ToolMessage in `update["messages"]`."""
        update = next((k.value for k in expr.keywords if k.arg == 'update'), None)
        contents: list[ast.expr] = []
        for mapping in _values(update, fn):
            for key, value in zip(mapping.keys, mapping.values) if isinstance(mapping, ast.Dict) else ():
                if isinstance(key, ast.Constant) and key.value == 'messages':
                    for messages in _values(value, fn):
                        for message in messages.elts if isinstance(messages, (ast.List, ast.Tuple)) else ():
                            if isinstance(message, ast.Call) and _last(message.func) == 'ToolMessage':
                                content = next((k.value for k in message.keywords if k.arg == 'content'), message.args[0] if message.args else None)
                                contents += [content] if content is not None else []
        if not contents:
            return {f'{_UNKNOWN}a Command with no ToolMessage content: {ast.unparse(expr)[:120]}'}
        return set().union(*(self.kinds_of(c, fn, hops, seen) for c in contents))

    def kinds_of(self, expr: ast.expr, fn: _FuncDef, hops: int, seen: frozenset[str]) -> set[str]:  # noqa: PLR0911, PLR0912
        if isinstance(expr, ast.Await):
            return self.kinds_of(expr.value, fn, hops, seen)
        if isinstance(expr, ast.Constant):
            kind = _CONSTANT_KIND.get(type(expr.value))
            return {kind} if kind else {f'{_UNKNOWN}unparsed: {ast.unparse(expr)[:120]}'}
        if isinstance(expr, ast.JoinedStr):
            return {'str'}
        if isinstance(expr, (ast.Dict, ast.DictComp)):
            return {'dict'}
        if isinstance(expr, (ast.List, ast.ListComp, ast.Tuple)):
            return {'list'}
        if isinstance(expr, ast.Compare) or (isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not)):
            return {'bool'}
        if isinstance(expr, ast.IfExp):
            return self.kinds_of(expr.body, fn, hops, seen) | self.kinds_of(expr.orelse, fn, hops, seen)
        if isinstance(expr, ast.BoolOp):
            return set().union(*(self.kinds_of(v, fn, hops, seen) for v in expr.values))
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Add, ast.Mod)):
            left = self.kinds_of(expr.left, fn, hops, seen)
            if left == {'str'} or (isinstance(expr.op, ast.Add) and left == self.kinds_of(expr.right, fn, hops, seen)):
                return left
        if isinstance(expr, ast.Name):
            return self._local(expr.id, fn, hops, seen)
        if isinstance(expr, ast.Call):
            func = expr.func
            if _last(func) == 'Command':
                return self._command(expr, fn, hops, seen)
            model = func.value if isinstance(func, ast.Attribute) and func.attr in _VALIDATE_METHODS else func if isinstance(func, ast.Name) else None
            if model is not None and self.shapes is not None and self.path is not None and self.shapes.shape_class(model, self.path) is not None:
                self.validated.append(model)
                return {'dict'}
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'self':
                return self._call(f'self.{func.attr}', expr, hops)
            if isinstance(func, ast.Attribute):
                if func.attr == 'dumps' and ast.unparse(func.value) == 'json':
                    inner = self.kinds_of(expr.args[0], fn, hops, seen) if expr.args else set()
                    return {_JSON_OF.get(frozenset(inner), 'json')}
                if func.attr in _STR_METHODS:
                    return {'str'}
                target = expr.args[0] if func.attr == 'to_thread' and expr.args else (expr.args[1] if func.attr == 'run_in_executor' and len(expr.args) > 1 else None)
                if isinstance(target, ast.Name):
                    return self._call(target.id, expr, hops)
            if isinstance(func, ast.Name):
                if func.id in _CALL_KIND and func.id not in self.functions:
                    return {_CALL_KIND[func.id]}
                return self._call(func.id, expr, hops)
            return {f'{_UNKNOWN}passthrough: {ast.unparse(expr)[:120]}'}
        return {f'{_UNKNOWN}unparsed: {ast.unparse(expr)[:120]}'}


def _dict_literals(expr: ast.expr, fn: _FuncDef) -> list[ast.Dict]:
    """The dict literals a returned expression is, or is built from: itself, `json.dumps(<dict>)`, either arm of a
    conditional, or the literal a local name was assigned."""
    if isinstance(expr, ast.Dict):
        return [expr]
    if isinstance(expr, ast.Await):
        return _dict_literals(expr.value, fn)
    if isinstance(expr, ast.IfExp):
        return _dict_literals(expr.body, fn) + _dict_literals(expr.orelse, fn)
    if isinstance(expr, ast.Call) and expr.args and isinstance(expr.func, ast.Attribute) and expr.func.attr == 'dumps' and ast.unparse(expr.func.value) == 'json':
        return _dict_literals(expr.args[0], fn)
    if isinstance(expr, ast.Name):
        return [value for value in _assigned(expr.id, fn) if isinstance(value, ast.Dict)]
    return []


def _class_fields(tree: ast.Module, annotation: ast.expr | None) -> dict[str, tuple[int, ast.expr]]:
    """`{field: (line, annotation)}` of the class a return annotation names (a TypedDict, dataclass or model), or of the class it wraps in `list[...]`/`Optional[...]`."""
    names: list[str] = []
    stack = [annotation] if annotation is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Subscript):
            stack += list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        elif isinstance(node, ast.BinOp):
            stack += [node.left, node.right]
    out: dict[str, int] = {}
    for top in tree.body:
        if isinstance(top, ast.ClassDef) and top.name in names:
            for stmt in top.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    out.setdefault(stmt.target.id, (stmt.lineno, stmt.annotation))
    return out


def _field_type(node: ast.expr | None, classes: Any = None) -> str | None:
    """The type a field annotation states in the vocabulary, a `Literal[...]` as written (its values are the field's members); None when it names something outside."""
    if isinstance(node, ast.Subscript) and _last(node.value) == 'Literal':
        return ast.unparse(node)
    kinds = _annotation_kinds(node, classes)
    return '|'.join(sorted(kinds)) if kinds else None


def _spelled_kinds(text: Any) -> frozenset[str] | None:
    """The vocabulary kinds a written field type names (`list[dict]` is list, `str | None` is null|str, `json[...]` is str); None when it names something outside."""
    try:
        kinds = _annotation_kinds(ast.parse(str(text), mode='eval').body)
    except SyntaxError:
        return None
    return frozenset('str' if k.startswith('json') else k for k in kinds) if kinds is not None else None


def _last(node: ast.expr) -> str:
    return ast.unparse(node).rsplit('.', 1)[-1]


def _assigned(name: str, fn: _FuncDef) -> list[ast.expr]:
    """Every value `name` is assigned in `fn`'s own scope."""
    values: list[ast.expr] = []
    for node in _own_nodes(fn):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            values.append(node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name) and node.target.id == name:
            values.append(node.value)
    return values


def _values(expr: ast.expr | None, fn: _FuncDef) -> list[ast.expr]:
    """`expr` itself, or what a local name holding it was assigned."""
    if isinstance(expr, ast.Name):
        return _assigned(expr.id, fn)
    return [] if expr is None else [expr]


def _annotation_kinds(node: ast.expr | None, classes: Any = None) -> set[str] | None:
    """The vocabulary kinds an annotation names, or None when it names something outside it. `classes(node)` says whether
    a name is a shape class of the agent's own code, which is a `dict` on the wire."""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        if node.value is None:
            return {'null'}
        return _annotation_kinds(ast.parse(node.value, mode='eval').body, classes) if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _annotation_kinds(node.left, classes), _annotation_kinds(node.right, classes)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.Subscript):
        base = ast.unparse(node.value).rsplit('.', 1)[-1]
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base in ('Optional', 'Union'):
            parts = [p for p in (_annotation_kinds(m, classes) for m in members) if p is not None]
            if len(parts) != len(members):
                return None
            return set().union(*parts) | ({'null'} if base == 'Optional' else set())
        node = node.value
    kind = _ANNOTATION_KIND.get(ast.unparse(node).rsplit('.', 1)[-1])
    if not kind and classes is not None and classes(node):
        kind = 'dict'
    return {kind} if kind else None


def _admits_none(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return node.value is None or (isinstance(node.value, str) and _admits_none(ast.parse(node.value, mode='eval').body))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _admits_none(node.left) or _admits_none(node.right)
    if isinstance(node, ast.Subscript):
        base = ast.unparse(node.value).rsplit('.', 1)[-1]
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        return base == 'Optional' or (base == 'Union' and any(_admits_none(m) for m in members))
    return False


def _nullable_args(fn: _FuncDef) -> dict[str, bool]:
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = [*zip(positional, defaults), *zip(args.kwonlyargs, args.kw_defaults)]
    return {
        a.arg: _admits_none(a.annotation) or (isinstance(d, ast.Constant) and d.value is None)
        for a, d in pairs if a.arg not in ('self', 'cls')
    }


_SPHINX_ARG = re.compile(r':(?:param|parameter|arg|argument|key|keyword)\s+(?:[^:]*\s)?\**(\w+)\s*:(.*)')
_SPHINX_RETURN = re.compile(r':returns?\s*:(.*)')
_SPHINX_FIELD = re.compile(r':\w[^:]*:.*')
_GOOGLE_HEADER = re.compile(r'(Args|Arguments|Parameters|Params|Keyword Args|Keyword Arguments|Returns?)\s*:(.*)')
_GOOGLE_ENTRY = re.compile(r'\**(\w+)\s*(?:\([^)]*\))?\s*:(.*)')
_SECTION = re.compile(r'[A-Z]\w*(?: \w+)?\s*:')
_RETURN_KEY = ':return'  # not an identifier, so no argument shares it


def declared_descriptions(text: Any) -> tuple[dict[str, str], str | None]:
    """(per-argument text, return text) a declaration states in Sphinx `:param <a>:` / `:return:` fields or Google
    `Args:` / `Returns:` blocks. A continuation line joins its entry; a blank line, another field, another section
    header or a Google block's dedent ends it."""
    entries: dict[str, list[str]] = {}
    current: list[str] | None = None
    section, header_indent, entry_indent = None, 0, None
    for raw in str(text or '').splitlines():
        line, indent = raw.strip(), len(raw) - len(raw.lstrip())
        if not line or (section is not None and indent <= header_indent):
            current = section = None
            if not line:
                continue
        arg, returned, header = _SPHINX_ARG.fullmatch(line), _SPHINX_RETURN.fullmatch(line), _GOOGLE_HEADER.fullmatch(line)
        entry = _GOOGLE_ENTRY.fullmatch(line) if section == 'args' and indent == (indent if entry_indent is None else entry_indent) else None
        if arg or returned:
            section, current = None, entries.setdefault(arg[1] if arg else _RETURN_KEY, [])
            current.append(arg[2] if arg else returned[1])  # type: ignore[index]
        elif header and section is None:
            section, header_indent, entry_indent = ('returns' if header[1].startswith('Return') else 'args'), indent, None
            current = entries.setdefault(_RETURN_KEY, []) if section == 'returns' else None
            if current is not None:
                current.append(header[2])
        elif _SPHINX_FIELD.fullmatch(line) or (section is None and _SECTION.fullmatch(line)):
            current = None
        elif entry:
            entry_indent, current = indent, entries.setdefault(entry[1], [])
            current.append(entry[2])
        elif current is not None:
            current.append(line)
    joined = {key: ' '.join(p.strip() for p in parts if p.strip()) for key, parts in entries.items()}
    return {k: v for k, v in joined.items() if v and k != _RETURN_KEY}, joined.get(_RETURN_KEY) or None


def _empty(text: Any) -> bool:
    return text is None or (isinstance(text, str) and not text.strip())


def description_fills(descriptor: dict[str, Any], docstring: str | None) -> tuple[dict[str, str], str | None]:
    """(arg -> text, returns text) the declaration documents where the config leaves the description empty. The
    function's docstring is read first, then `descriptor.description`; a non-empty description is never replaced."""
    (doc_args, doc_return), (desc_args, desc_return) = declared_descriptions(docstring), declared_descriptions(descriptor.get('description'))
    documented, returned = desc_args | doc_args, doc_return or desc_return
    args = descriptor.get('args') if isinstance(descriptor.get('args'), dict) else {}
    returns = descriptor.get('returns')
    fills = {a: text for a, text in documented.items() if isinstance(args.get(a), dict) and _empty(args[a].get('description'))}
    empty_returns = returns is None or (isinstance(returns, dict) and _empty(returns.get('description')))
    return fills, (returned if empty_returns else None)


def _is_tool_decorated(fn: _FuncDef) -> bool:
    for deco in fn.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if ast.unparse(target).rsplit('.', 1)[-1].endswith('tool'):
            return True
    return False


def _agent_python() -> str:
    """The interpreter the agent runs under: the SDK child's rule (`plurai/_subprocess.py`)."""
    return os.environ.get('PLURAI_CHILD_PYTHON') or sys.executable


@functools.cache
def _import_path(python: str) -> tuple[Path, ...]:
    """`python`'s sys.path — where the agent's installed libraries live. Asked, never assumed."""
    if python == sys.executable:
        paths = sys.path
    else:
        try:
            done = subprocess.run([python, '-c', 'import json,sys;print(json.dumps(sys.path))'], capture_output=True, text=True, timeout=60, check=True, stdin=subprocess.DEVNULL)
            paths = json.loads(done.stdout.strip().splitlines()[-1])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
            raise RuntimeError(f'PLURAI_CHILD_PYTHON={python} could not report its import path: {exc}') from exc
    return tuple(Path(p) for p in paths if p and Path(p).is_dir())


@functools.cache
def _editable_map(python: str) -> dict[str, Path]:
    """Package -> source directory of every editable install on `python`'s path: the `MAPPING` literal of its
    `__editable__*finder.py`, read with `ast`, never imported."""
    mapping: dict[str, Path] = {}
    for directory in _import_path(python):
        for finder in sorted(directory.glob('__editable__*finder.py')):
            try:
                tree = ast.parse(finder.read_text(encoding='utf-8'))
            except (OSError, SyntaxError, ValueError):
                continue
            for node in tree.body:
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target if isinstance(node, ast.AnnAssign) else None
                if isinstance(target, ast.Name) and target.id == 'MAPPING' and isinstance(node.value, ast.Dict):
                    for key, value in zip(node.value.keys, node.value.values):
                        if isinstance(key, ast.Constant) and isinstance(value, ast.Constant) and isinstance(key.value, str) and isinstance(value.value, str):
                            mapping.setdefault(key.value, Path(value.value))
    return mapping


def _under(root: Path, file: Path, otherwise: Path) -> tuple[Path, Path]:
    """(file, base): `root` when the file lies under it, so its citations read like the rest of the agent's tree."""
    return (file, root) if root.resolve() in file.resolve().parents else (file, otherwise)


def _editable_file(root: Path, dotted: str) -> tuple[Path, Path] | None:
    top, *rest = dotted.split('.')
    mapped = _editable_map(_agent_python()).get(top)
    if mapped is None:
        return None
    if mapped.suffix == '.py':
        return _under(root, mapped, mapped.parent) if not rest and mapped.is_file() else None
    for candidate in (mapped.joinpath(*rest).with_suffix('.py') if rest else None, mapped.joinpath(*rest, '__init__.py')):
        if candidate is not None and candidate.is_file():
            return _under(root, candidate, mapped.parent)
    return None


@functools.cache
def _package_parents(root: Path, package: str) -> tuple[Path, ...]:
    """Every directory within five levels of `root` that holds a directory `package`, skipping what `_source_files` skips."""
    found: list[Path] = []
    start = len(root.parts)
    for here, subdirs, _files in os.walk(root):
        subdirs[:] = [d for d in subdirs if not d.startswith('.') and d not in _SKIP_DIRS]
        if len(Path(here).parts) - start >= 5:
            subdirs[:] = []
        elif package in subdirs:
            found.append(Path(here))
    return tuple(found)


def _walked_file(root: Path, dotted: str) -> tuple[Path, Path] | None:
    """The one file under `root` that spells `dotted`, for a package below the repo root. Never a choice between two;
    a one-segment module could name any stray file, so it is not looked for."""
    parts = dotted.split('.')
    if len(parts) < 2:
        return None
    candidates = [c for parent in _package_parents(root, parts[0]) for c in (parent.joinpath(*parts).with_suffix('.py'), parent.joinpath(*parts, '__init__.py')) if c.is_file()]
    return (candidates[0], root) if len(candidates) == 1 else None


def _module_file(root: Path, dotted: str) -> tuple[Path, Path] | None:
    """(file, the base it was found under): the agent's tree first, then the agent interpreter's
    path — where a library tool's installed source lives — then an editable install's finder, then the one
    file under `root` that spells the module. Found by path, never imported."""
    if not all(dotted.split('.')):
        return None  # `.`, `a..b`, a trailing dot: no module path names a file
    rel = Path(*dotted.split('.'))
    bases = [root, root / 'src', *_import_path(_agent_python())]
    for base in bases:
        for candidate in (base / rel.with_suffix('.py'), base / rel / '__init__.py'):
            if candidate.is_file():
                return candidate, base
    return _editable_file(root, dotted) or _walked_file(root, dotted)


def located(root: Path, name: str, descriptor: dict[str, Any]) -> tuple[tuple[Path, Path] | None, str, str, str | None]:
    """(where the tool's module resolves, the module, the function, a note). A module spelled `<module>.<Class>` beside a
    method is read as `<module>` and `<Class>.<method>`, and the note says so."""
    module, function = descriptor.get('original_module'), str(descriptor.get('original_function') or name)
    if not isinstance(module, str) or not module:
        return None, str(module or ''), function, None
    found = _module_file(root, module)
    parent, _, tail = module.rpartition('.')
    if found is not None or not parent or '.' in function:
        return found, module, function, None
    above = _module_file(root, parent)
    if above is None:
        return None, module, function, None
    try:
        tree = ast.parse(above[0].read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return None, module, function, None
    if not any(isinstance(n, ast.ClassDef) and n.name == tail for n in tree.body):
        return None, module, function, None
    return above, parent, f'{tail}.{function}', f'original_module {module!r} names a class: read as module {parent!r}, function {tail}.{function}'


# --- nested response shapes ---------------------------------------------------------------------


class _Field:
    def __init__(self, name: str, line: int, annotation: ast.expr, value: ast.expr | None, path: Path, total: bool) -> None:
        self.name, self.line, self.annotation, self.value, self.path, self.total = name, line, annotation, value, path, total

    def field_call(self) -> ast.Call | None:
        """The `Field(...)` that configures it: its default value, or `Annotated[X, Field(...)]`."""
        held = [self.value]
        if isinstance(self.annotation, ast.Subscript) and _last(self.annotation.value) == 'Annotated' and isinstance(self.annotation.slice, ast.Tuple):
            held += self.annotation.slice.elts[1:]
        return next((c for c in held if isinstance(c, ast.Call) and _last(c.func) in ('Field', 'field')), None)

    def optional(self) -> bool:
        """A field the model does not require: a default, `NotRequired`, or a TypedDict with `total=False`."""
        if isinstance(self.annotation, ast.Subscript) and _last(self.annotation.value) == 'NotRequired':
            return True
        if not self.total:
            return True
        call = self.field_call()
        if call is not None:
            given = any(k.arg in ('default', 'default_factory') for k in call.keywords)
            return given or bool(call.args and not (isinstance(call.args[0], ast.Constant) and call.args[0].value is Ellipsis))
        return self.value is not None


def _top_statements(tree: ast.Module) -> list[ast.stmt]:
    """A module's top-level statements, those under a top-level `if` or `try` (a `TYPE_CHECKING` import) too."""
    out: list[ast.stmt] = []
    for node in tree.body:
        out.append(node)
        if isinstance(node, ast.If):
            out += node.body + node.orelse
        elif isinstance(node, ast.Try):
            out += node.body
    return out


class _Shapes:
    """The agent's own pydantic models, TypedDicts and dataclasses, read with `ast` across its import graph."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.trees: dict[Path, ast.Module | None] = {}
        self.budget = _SHAPE_NODES

    def _tree(self, path: Path) -> ast.Module | None:
        if path not in self.trees:
            try:
                self.trees[path] = ast.parse(path.read_text(encoding='utf-8'))
            except (OSError, SyntaxError, ValueError):
                self.trees[path] = None
        return self.trees[path]

    def _owned(self, path: Path) -> bool:
        """The agent's own file: under its root or an editable install, and never an installed library's."""
        if _INSTALLED & set(path.parts):
            return False
        resolved = path.resolve()
        mapped = _editable_map(_agent_python()).values()
        return self.root.resolve() in resolved.parents or any(m.resolve() in resolved.parents or m.resolve() == resolved for m in mapped)

    def _module(self, dotted: str, level: int, path: Path) -> Path | None:
        """The file a module reference from `path` names, when it is the agent's own."""
        if level:
            base = path.parents[level - 1].joinpath(*(dotted.split('.') if dotted else ()))
            file = next((c for c in (base.with_suffix('.py'), base / '__init__.py') if c.is_file()), None)
        else:
            found = _module_file(self.root, dotted) if dotted else None
            file = found[0] if found else None
        return file if file is not None and self._owned(file) else None

    def _lookup(self, name: str, path: Path, hops: int = 0) -> tuple[ast.AST, Path] | None:
        """What `name` is at the top of the module at `path`: a class, the value of an assignment, or what an import binds."""
        tree = self._tree(path)
        if tree is None or hops > MAX_HOPS:
            return None
        for node in _top_statements(tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node, path
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value, path
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name and node.value is not None:
                return node.value, path
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        target = self._module(node.module or '', node.level, path)
                        return self._lookup(alias.name, target, hops + 1) if target is not None else None
        return None

    def _module_named(self, name: str, path: Path) -> Path | None:
        """The agent's module a dotted name in `path` refers to: `import a.b`, `import a.b as m`, `from a import b`."""
        tree = self._tree(path)
        for node in _top_statements(tree) if tree is not None else ():
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        return self._module(alias.name, 0, path)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        return self._module('.'.join(p for p in (node.module, alias.name) if p), node.level, path)
        return None

    def _resolve(self, node: ast.AST, path: Path, hops: int = 0) -> tuple[ast.AST, Path] | None:
        """What an annotation's name refers to: a class, or the value of a module-level alias."""
        if hops > MAX_HOPS:
            return None
        hit: tuple[ast.AST, Path] | None = None
        if isinstance(node, ast.Name):
            hit = self._lookup(node.id, path)
        elif isinstance(node, ast.Attribute):
            module = self._module_named(ast.unparse(node.value), path)
            hit = self._lookup(node.attr, module) if module is not None else None
        elif isinstance(node, ast.Subscript):
            return self._resolve(node.value, path, hops + 1)
        if hit is not None and isinstance(hit[0], (ast.Name, ast.Attribute)):  # `Foo = Bar`
            return self._resolve(hit[0], hit[1], hops + 1) or hit
        return hit

    def _recognized(self, cls: ast.ClassDef, path: Path, seen: frozenset[str] = frozenset()) -> bool:
        if any(_last(d.func if isinstance(d, ast.Call) else d) == 'dataclass' for d in cls.decorator_list):
            return True
        for base in cls.bases:
            named = base.value if isinstance(base, ast.Subscript) else base
            if _last(named) in _SHAPE_BASES:
                return True
            hit = self._resolve(named, path)
            if hit is not None and isinstance(hit[0], ast.ClassDef) and hit[0].name not in seen and self._recognized(hit[0], hit[1], seen | {cls.name}):
                return True
        return False

    def shape_class(self, node: ast.AST, path: Path) -> tuple[ast.ClassDef, Path] | None:
        hit = self._resolve(node, path)
        return (hit[0], hit[1]) if hit is not None and isinstance(hit[0], ast.ClassDef) and self._owned(hit[1]) and self._recognized(hit[0], hit[1]) else None

    def fields_of(self, cls: ast.ClassDef, path: Path, seen: frozenset[str] = frozenset()) -> dict[str, _Field]:
        """The class's fields, inherited ones first."""
        out: dict[str, _Field] = {}
        for base in cls.bases:
            hit = self.shape_class(base, path)
            if hit is not None and hit[0].name not in seen and hit[0].name != cls.name:
                out |= self.fields_of(hit[0], hit[1], seen | {cls.name})
        total = not any(k.arg == 'total' and isinstance(k.value, ast.Constant) and k.value.value is False for k in cls.keywords)
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                name = stmt.target.id
                wraps_classvar = _last(stmt.annotation.value if isinstance(stmt.annotation, ast.Subscript) else stmt.annotation) == 'ClassVar'
                if not name.startswith('_') and name != 'model_config' and not wraps_classvar:
                    out[name] = _Field(name, stmt.lineno, stmt.annotation, stmt.value, path, total)
        return out

    def _alias_rule(self, cls: ast.ClassDef, path: Path, seen: frozenset[str] = frozenset()) -> tuple[bool, bool]:
        """(a camelCase alias generator, the python name is accepted too), from the class's `model_config` or a base's."""
        for stmt in cls.body:
            value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
            target = stmt.targets[0] if isinstance(stmt, ast.Assign) else stmt.target if isinstance(stmt, ast.AnnAssign) else None
            if isinstance(target, ast.Name) and target.id == 'model_config' and value is not None:
                if isinstance(value, ast.Name):
                    hit = self._lookup(value.id, path)
                    value = hit[0] if hit is not None and isinstance(hit[0], ast.expr) else value
                words = {k.arg: k.value for k in value.keywords} if isinstance(value, ast.Call) else {}
                generator = words.get('alias_generator')
                camel = generator is not None and _last(generator) == 'to_camel'
                by_name = any(isinstance(words.get(k), ast.Constant) and words[k].value is True for k in ('populate_by_name', 'validate_by_name'))
                return camel, by_name
        for base in cls.bases:
            hit = self.shape_class(base, path)
            if hit is not None and hit[0].name not in seen:
                return self._alias_rule(hit[0], hit[1], seen | {cls.name})
        return False, False

    def spellings(self, cls: ast.ClassDef, path: Path, field: _Field) -> tuple[str, str | None]:
        """(the key to emit, the alias when there is one): the python name wherever the model takes it, which is the name
        the world's rows and the mock's own readers use; else the alias."""
        camel, by_name = self._alias_rule(cls, path)
        alias = None
        call = field.field_call()
        given = next((k.value for k in call.keywords if k.arg == 'alias'), None) if call is not None else None
        alias = given.value if isinstance(given, ast.Constant) and isinstance(given.value, str) else None
        alias = alias or (re.sub(r'_+([0-9A-Za-z])', lambda m: m[1].upper(), field.name) if camel else None)
        if alias is None or alias == field.name:
            return field.name, None
        return (field.name if by_name else alias), alias

    def _class_spec(self, cls: ast.ClassDef, path: Path, stack: frozenset[str], depth: int) -> dict[str, Any]:
        if cls.name in stack:
            return {'type': 'dict', 'truncated': f'recursive {cls.name}'}
        fields: dict[str, Any] = {}
        for field in self.fields_of(cls, path).values():
            spec = self.spec(field.annotation, field.path, stack | {cls.name}, depth + 1)
            fields[self.spellings(cls, path, field)[0]] = {**spec, **({'optional': True} if field.optional() else {})}
        return {'type': 'dict', 'fields': fields}

    def _union(self, members: list[ast.expr], path: Path, stack: frozenset[str], depth: int) -> dict[str, Any]:
        specs = [self.spec(m, path, stack, depth) for m in members]
        real = [s for s in specs if s.get('type') != 'null']
        nullable = len(real) < len(specs)
        if len(real) == 1:
            return {**real[0], 'type': f'{real[0]["type"]}|null' if nullable else real[0]['type']}
        kinds = sorted({str(s['type']) for s in real} | ({'null'} if nullable else set()))
        return {'type': '|'.join(kinds)}

    def spec(self, node: ast.expr | None, path: Path, stack: frozenset[str] = frozenset(), depth: int = 0) -> dict[str, Any]:  # noqa: PLR0911, PLR0912
        """The nested spec an annotation states. `{type: any}` for what the AST cannot open: the mock check still reads it."""
        self.budget -= 1
        if self.budget < 0 or depth > _SHAPE_DEPTH:
            return {'type': 'dict', 'truncated': 'too large'}
        if node is None:
            return {'type': 'any'}
        if isinstance(node, ast.Constant):
            if node.value is None:
                return {'type': 'null'}
            try:
                return self.spec(ast.parse(node.value, mode='eval').body, path, stack, depth) if isinstance(node.value, str) else {'type': 'any'}
            except SyntaxError:
                return {'type': 'any'}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self._union([node.left, node.right], path, stack, depth)
        if isinstance(node, ast.Subscript):
            base = _last(node.value)
            members = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            if base == 'Literal':
                return {'type': ast.unparse(node)}
            if base in ('Annotated', 'NotRequired', 'Required', 'ReadOnly', 'Final'):
                return self.spec(members[0], path, stack, depth)
            if base == 'Optional':
                return self._union([members[0], ast.Constant(None)], path, stack, depth)
            if base == 'Union':
                return self._union(members, path, stack, depth)
            kind = _ANNOTATION_KIND.get(base)
            if kind == 'list':
                item = self.spec(members[0], path, stack, depth + 1)
                if 'fields' in item:
                    return {'type': 'list', 'item_fields': item['fields']}
                return {'type': f'list[{item["type"]}]'} if item.get('type') != 'any' else {'type': 'list'}
            if kind == 'dict':
                return {'type': 'dict'}
        name = _last(node) if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)) else ''
        if name in _ANNOTATION_KIND and not isinstance(node, ast.Subscript):
            return {'type': _ANNOTATION_KIND[name]}
        if name in _JSON_STR:
            return {'type': 'str'}
        hit = self._resolve(node, path)
        if hit is None:
            return {'type': 'any'}
        target, where = hit
        if isinstance(target, ast.ClassDef):
            if self._recognized(target, where):
                return self._class_spec(target, where, stack, depth)
            if {_last(b) for b in target.bases} & _ENUM_BASES:
                values = [s.value.value for s in target.body if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)]
                return {'type': f'Literal[{", ".join(map(repr, values))}]'} if values else {'type': 'any'}
            return {'type': 'any'}
        return self.spec(target, where, stack, depth + 1) if isinstance(target, ast.expr) else {'type': 'any'}

    def _unwrapped(self, node: ast.expr | None, path: Path, listed: bool = False) -> tuple[str, ast.ClassDef, Path] | None:
        """(`fields` or `item_fields`, the class) a return annotation carries: through `Optional`, `| None` and one `list`."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self._unwrapped(ast.parse(node.value, mode='eval').body, path, listed)
            except SyntaxError:
                return None
        members: list[ast.expr] = []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            members = [node.left, node.right]
        elif isinstance(node, ast.Subscript) and _last(node.value) in ('Optional', 'Union', 'Annotated'):
            members = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
            members = members[:1] if _last(node.value) == 'Annotated' else members
        if members:
            real = [m for m in members if not (isinstance(m, ast.Constant) and m.value is None) and ast.unparse(m) != 'None']
            return self._unwrapped(real[0], path, listed) if len(real) == 1 else None
        if isinstance(node, ast.Subscript) and _ANNOTATION_KIND.get(_last(node.value)) == 'list' and not listed:
            inner = node.slice.elts[0] if isinstance(node.slice, ast.Tuple) and node.slice.elts else node.slice
            return self._unwrapped(inner, path, True)
        hit = self.shape_class(node, path) if node is not None else None
        return ('item_fields' if listed else 'fields', hit[0], hit[1]) if hit is not None else None

    def _rel(self, path: Path) -> str:
        resolved = path.resolve()
        if self.root.resolve() in resolved.parents:
            return resolved.relative_to(self.root.resolve()).as_posix()
        top = resolved.parent
        while (top / '__init__.py').is_file() and top.parent != top:
            top = top.parent
        return resolved.relative_to(top).as_posix()

    def response(self, annotation: ast.expr | None, validated: list[ast.expr], path: Path) -> dict[str, Any] | None:
        """The nested shape of a tool's return: `{block, annotation, fields: {name: {key, alias, site, type, spec}}}`, read from
        the annotation, else from the class a returned `Model.model_validate(...)` names. None when neither is the agent's own class."""
        for node in [annotation, *validated]:
            unwrapped = self._unwrapped(node, path)
            if unwrapped is None or node is None:
                continue
            block, cls, where = unwrapped
            self.budget = _SHAPE_NODES
            fields: dict[str, dict[str, Any]] = {}
            for field in self.fields_of(cls, where).values():
                key, alias = self.spellings(cls, where, field)
                spec = self.spec(field.annotation, field.path, frozenset({cls.name}), 1)
                fields[field.name] = {'key': key, 'alias': alias, 'site': f'{self._rel(field.path)}:{field.line}',
                                      'spec': {**spec, **({'optional': True} if field.optional() else {})},
                                      'type': _field_type(field.annotation, lambda n, p=field.path: self.shape_class(n, p) is not None)}
            annotation_text = node.value if isinstance(node, ast.Constant) else ast.unparse(node)
            return {'block': block, 'annotation': annotation_text, 'fields': fields}
        return None


def _render(kinds: set[str]) -> tuple[str, str | None]:
    reasons = sorted({k.removeprefix(_UNKNOWN) for k in kinds if k.startswith(_UNKNOWN)})
    if reasons:
        return 'unknown', '; '.join(reasons)
    return '|'.join(sorted(kinds)), None


def _tool_def(tree: ast.Module, function: str) -> tuple[ast.stmt, _FuncDef] | None:
    """(the top-level statement holding the tool's def, the def): a `Class.method` tool inside that class, else the
    function by name (a def a tool decorator marks first), at any depth: tools are often nested in a factory."""
    owner, _, method = function.rpartition('.')
    holders = [
        (top, n) for top in tree.body for n in ast.walk(top)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == method
        and (not owner or (isinstance(top, ast.ClassDef) and top.name == owner and n in top.body))
    ]
    return next(((t, f) for t, f in holders if _is_tool_decorated(f)), holders[0]) if holders else None


def _helpers(tree: ast.Module, top: ast.stmt, fn: _FuncDef, owner: str) -> dict[str, _FuncDef]:
    """The same-module functions a tool may call: module-level defs, and its class's sibling methods (`self.<name>`) or
    the defs beside it in its factory."""
    functions: dict[str, _FuncDef] = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if owner:
        functions |= {f'self.{n.name}': n for n in top.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn}  # type: ignore[attr-defined]
    else:
        functions |= {n.name: n for n in ast.walk(top) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not fn}
    return functions


#: Accessors on a value already in hand (a mapping's, a response's): never the call that fetched the rows.
_ACCESSORS = frozenset({'get', 'json', 'items', 'keys', 'values', 'copy'})


def _root_name(node: ast.expr) -> str | None:
    """The name a call chain starts from: `ncSync` for `ncSync.cal.principal()`, `Calendar` for `Calendar(x)`."""
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript, ast.Await)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


@functools.lru_cache(maxsize=None)
def _stdlib(package: str) -> bool:
    """Whether `package` is the standard library's (`sys.stdlib_module_names`; before 3.10, where its spec's file lives)."""
    names = getattr(sys, 'stdlib_module_names', None)
    if names is not None:
        return package in names
    import importlib.util  # noqa: PLC0415
    import sysconfig  # noqa: PLC0415

    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return False
    origin = getattr(spec, 'origin', None) or ''
    return package in sys.builtin_module_names or (origin.startswith(sysconfig.get_paths()['stdlib']) and 'site-packages' not in origin)


def _library_names(tree: ast.Module, bases: tuple[Path, ...]) -> set[str]:
    """The names the module's top-level imports bind from a non-stdlib package that is not the agent's own (a relative
    import, or a top package found under one of `bases`)."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            pairs = [(alias.name, alias.asname or alias.name.partition('.')[0]) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            pairs = [(node.module, alias.asname or alias.name) for alias in node.names if alias.name != '*']
        else:
            continue
        for module, bound in pairs:
            package = module.partition('.')[0]
            own = any((b / package).is_dir() or (b / f'{package}.py').is_file() for b in bases)
            if not own and not _stdlib(package):
                names.add(bound)
    return names


def _bound_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _taint(nodes: list[ast.AST], seeds: set[str]) -> set[str]:
    """`seeds` and every name bound, in `nodes`, from a value that reads one of them (an assignment, a loop or
    comprehension target, a `with ... as`, a walrus), to a fixed point."""
    tainted = set(seeds)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            pairs: list[tuple[ast.AST, ast.AST | None]] = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                pairs = [(node.target, node.value)]
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                pairs = [(node.target, node.iter)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                pairs = [(node.optional_vars, node.context_expr)]
            for target, value in pairs:
                new = _bound_names(target) - tainted if value is not None and _bound_names(value) & tainted else set()
                if new:
                    tainted |= new
                    changed = True
    return tainted


def _external_params(fn: _FuncDef, library: set[str]) -> set[str]:
    """`fn`'s parameters annotated with a library type (`nc: AsyncNextcloudApp`)."""
    params = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    return {a.arg for a in params if a.annotation is not None and _bound_names(a.annotation) & library}


def _row_source_lines(tree: ast.Module, top: ast.stmt, fn: _FuncDef, owner: str, bases: tuple[Path, ...]) -> list[int]:
    """The lines of the library calls whose result `fn` (or a same-module helper it reaches, through `to_thread` /
    `run_in_executor` too, up to MAX_HOPS deep) iterates or returns."""
    library = _library_names(tree, bases)
    functions = _helpers(tree, top, fn, owner)
    closure = set(library)
    if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)) and top is not fn:
        closure = _taint(_own_nodes(top), library | _external_params(top, library))
    found: set[int] = set()
    seen: set[int] = set()

    def visit(f: _FuncDef, hops: int) -> None:
        if id(f) in seen:
            return
        seen.add(id(f))
        own = {a.arg for a in [*f.args.posonlyargs, *f.args.args, *f.args.kwonlyargs]}
        nodes = _own_nodes(f)
        tainted = _taint(nodes, (closure - own) | _external_params(f, library))
        bound: dict[str, ast.Call] = {}
        for node in nodes:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value.value if isinstance(node.value, ast.Await) else node.value
            if isinstance(value, ast.Call) and _root_name(value.func) in tainted:
                bound |= {t.id: value for t in node.targets if isinstance(t, ast.Name)}
        sources = [n.iter for n in nodes if isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension))]
        sources += [n.value for n in nodes if isinstance(n, ast.Return) and n.value is not None]
        for expr in sources:
            for x in ast.walk(expr):
                if isinstance(x, ast.Call) and _root_name(x.func) in tainted and not (isinstance(x.func, ast.Attribute) and x.func.attr in _ACCESSORS):
                    found.add(x.lineno)
                elif isinstance(x, ast.Name) and x.id in bound:
                    found.add(bound[x.id].lineno)
        for node in nodes:
            if not isinstance(node, ast.Call) or hops >= MAX_HOPS:
                continue
            func, target = node.func, None
            if isinstance(func, ast.Attribute) and func.attr in ('to_thread', 'run_in_executor'):
                arg = node.args[0] if func.attr == 'to_thread' and node.args else (node.args[1] if len(node.args) > 1 else None)
                target = ast.unparse(arg) if isinstance(arg, (ast.Name, ast.Attribute)) else None
            elif isinstance(func, (ast.Name, ast.Attribute)):
                target = ast.unparse(func)
            if target in functions:
                visit(functions[target], hops + 1)

    visit(fn, 0)
    return sorted(found)


def row_source_tool(entry: Any) -> bool:
    """A `mode: mock`, `seam: tool_body` tool that writes nothing (`descriptor.writes_to` empty): one whose row sources are units."""
    if not isinstance(entry, dict) or entry.get('mode', 'mock') != 'mock' or entry.get('seam') != 'tool_body':
        return False
    writes_to = (entry.get('descriptor') or {}).get('writes_to') if isinstance(entry.get('descriptor'), dict) else None
    return not (isinstance(writes_to, str) and writes_to.strip().lower() not in ('', 'none', 'null'))


def row_sources(root: Path, name: str, descriptor: dict[str, Any]) -> list[str] | None:
    """`[<file>:<line>]` of the tool's row sources (:func:`_row_source_lines`), the file relative to the base its module
    resolved under; None when its source is not found."""
    found, module, function, _note = located(root, name, descriptor)
    if found is None:
        return None
    path, base = found
    tree = ast.parse(path.read_text(encoding='utf-8'))
    held = _tool_def(tree, function)
    if held is None:
        return None
    rel = path.relative_to(base).as_posix()
    # The agent's own packages: under its root (a module found on the interpreter's path is a library's, as are its imports),
    # and the folder that holds a package below the root.
    depth = module.count('.') + (2 if path.name == '__init__.py' else 1)
    own = (root, root / 'src', *([path.parents[depth - 1]] if base == root and len(path.parents) >= depth else []))
    return [f'{rel}:{line}' for line in _row_source_lines(tree, held[0], held[1], function.rpartition('.')[0], own)]


def first_row_sources(bases: list[Path], name: str, descriptor: dict[str, Any]) -> list[str]:
    """:func:`row_sources` under the first of `bases` its module resolves under; none when no base holds it."""
    return next((units for units in (row_sources(base, name, descriptor) for base in bases) if units is not None), [])


#: verify_config's `_RULE_SOURCE` and `_CODE_CITE`, restated (this script runs alone): a rule's closing citation, a code cite.
_RULE_SOURCE = re.compile(r'\(source:\s*([^\s:()]+):(\d+)(?:-(\d+))?\)\s*\.?\s*$')
_CODE_CITE = re.compile(r'^([^\s:]+):(\d+)(?:-(\d+))?$')


#: The `returns:` value of a typed `skipped_code` entry that answers one row source: it returns every row it gets.
EVERY_ROW = 'every_row'
#: Directories that hold installed libraries: a file under one is a library's, even inside the agent's checkout (a `.venv`).
_INSTALLED = frozenset({'site-packages', 'dist-packages'})


def skipped_cite(item: Any) -> str | None:
    """A `skipped_code` entry's `<path>:<line>[-<end>]`: a plain entry itself, a typed one's `cite`."""
    cite = item.get('cite') if isinstance(item, dict) else item
    return cite if isinstance(cite, str) else None


def _same_file(cite: str, path: str) -> bool:
    """One path a suffix of the other: a cite may be agent-root relative, a unit relative to where its module resolved."""
    return cite == path or cite.endswith(f'/{path}') or path.endswith(f'/{cite}')


def _span(match: re.Match[str] | None) -> tuple[str, int, int] | None:
    return None if match is None else (match.group(1), int(match.group(2)), int(match.group(3) or match.group(2)))


def _called_names(source: Path, line: int) -> set[str]:
    """The names called on `line` of `source`: `todos` for `cal.todos()`, `Calendar` for `Calendar(x)`."""
    tree = ast.parse(source.read_text(encoding='utf-8'))
    calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call) and n.lineno == line]
    return {f.attr if isinstance(f, ast.Attribute) else f.id for f in calls if isinstance(f, (ast.Attribute, ast.Name))}


def _library_evidence(evidence: str, called: set[str], agent_bases: list[Path]) -> str | None:
    """Why `evidence` (`<path>:<line>`) is not a line inside the called library function; None when it is.

    The path is absolute, or relative to an entry of the agent interpreter's import path (`caldav/collection.py`); the
    file must exist, sit outside the agent's own tree (a `site-packages` under it is a library's), and the line must fall
    inside a `def` or `class` named as a call on the row-source line."""
    path, _, line = evidence.rpartition(':')
    if not path or not line.isdigit():
        return f'evidence {evidence!r} is not `<path>:<line>`'
    rel = Path(path)
    candidates = [rel] if rel.is_absolute() else [base / rel for base in (*_import_path(_agent_python()), *agent_bases)]
    found = next((c.resolve() for c in candidates if c.is_file()), None)
    if found is None:
        return f'evidence file {path!r} is not on the agent interpreter\'s import path'
    for base in (b.resolve() for b in agent_bases):
        if base in found.parents and not _INSTALLED & set(found.relative_to(base).parts):
            return f'evidence {evidence!r} is the agent\'s own code; cite the installed library\'s source'
    number = int(line)
    try:
        tree = ast.parse(found.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return f'evidence file {path!r} does not parse'
    enclosing = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and n.lineno <= number <= (n.end_lineno or n.lineno)}
    if not enclosing & called:
        return f'evidence {evidence!r} is not inside the called {" / ".join(sorted(called)) or "function"}'
    return None


def row_source_answer(unit: str, entry: dict[str, Any], agent_bases: list[Path]) -> str | None:
    """Why the row source `unit` (`<file>:<line>`) is unanswered; None when it is answered.

    It is answered by a `descriptor.business_rules` entry whose closing `(source: …)` names exactly that line (the rule
    states the stored values or state the rows narrow to), or by a typed `skipped_code` entry for exactly that line,
    `{cite: <file>:<line>, returns: every_row, evidence: <path>:<line>}`, the evidence a line inside the called library
    function's installed source (:func:`_library_evidence`). A range or a plain `skipped_code` line answers no row
    source: a `tool_body` seam already lists the body it skips."""
    path, _, line = unit.rpartition(':')
    number = int(line)
    descriptor = entry.get('descriptor') if isinstance(entry.get('descriptor'), dict) else {}
    rules = [_span(_RULE_SOURCE.search(r)) for r in descriptor.get('business_rules') or [] if isinstance(r, str)]
    if any(s and s[1] == s[2] == number and _same_file(s[0], path) for s in rules):
        return None
    typed = [i for i in entry.get('skipped_code') or [] if isinstance(i, dict)
             and (s := _span(_CODE_CITE.match(skipped_cite(i) or ''))) and s[1] == s[2] == number and _same_file(s[0], path)]
    if not typed:
        spans = [_span(_CODE_CITE.match(skipped_cite(i) or '')) for i in entry.get('skipped_code') or []] + rules
        if any(s and s[1] <= number <= s[2] and _same_file(s[0], path) for s in spans):
            return 'only a range or a plain skipped_code line covers it'
        return 'nothing cites it'
    reasons = []
    for item in typed:
        evidence = item.get('evidence')
        if item.get('returns') != EVERY_ROW:
            reasons.append(f'its skipped_code entry says `returns: {item.get("returns")!r}`, not `{EVERY_ROW}`')
        elif not isinstance(evidence, str) or not evidence.strip():
            reasons.append('its `returns: every_row` carries no `evidence: <path>:<line>` in the called library function')
        else:
            agent_file = next((b / path for b in agent_bases if (b / path).is_file()), None)
            reason = _library_evidence(evidence.strip(), _called_names(agent_file, number) if agent_file else set(), agent_bases)
            if reason is None:
                return None
            reasons.append(reason)
    return '; '.join(reasons)


def derive_tool(root: Path, name: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    """The derived facts for one tool: {type, type_source, evidence, unknown_reason, nullable, conflict, docstring_site}.
    `docstring_site` is the patched function's (or its class's) docstring, a declaration site of its own."""
    found, module, function, respelled = located(root, name, descriptor)
    facts: dict[str, Any] = {'evidence': [], 'unknown_reason': None, 'nullable': {}, 'conflict': None, 'docstring_site': None, 'docstring': None, 'field_sites': {},
                             'field_types': {}, 'error_channel': None, 'error_channel_site': None, 'respelled': respelled, 'source_missing': False, 'shape': None, 'module': module}
    if found is None:
        said = descriptor.get('original_module')
        return facts | {'type': 'unknown', 'type_source': 'declared_unknown', 'source_missing': True, 'unknown_reason': f'no source for {said!r} under the agent root or on the agent interpreter\'s path'}
    path, base = found
    tree = ast.parse(path.read_text(encoding='utf-8'))
    rel = path.relative_to(base).as_posix()
    owner, _, method = function.rpartition('.')
    # Tools are often nested in a factory; the top-level statement holding the def is its scope. A
    # `Class.method` tool is looked up inside that class, and its sibling methods are `self.` helpers.
    held = _tool_def(tree, function)
    if held is None:
        if not owner and any(isinstance(n, ast.ClassDef) and n.name == method for n in tree.body):
            facts['conflict'] = f'original_function {function!r} names a class in {rel}: declare the method the tool runs, {function}.<method>'
        return facts | {'type': 'unknown', 'type_source': 'declared_unknown', 'source_missing': True, 'unknown_reason': f'no def {function}() in {rel}'}
    top, fn = held
    documented = next((n for n in ((fn, top) if owner else (fn,)) if ast.get_docstring(n) is not None), None)  # type: ignore[arg-type]
    if documented is not None:
        docstring = documented.body[0]  # type: ignore[union-attr]
        facts['docstring_site'] = {'site': f'{rel}:{docstring.lineno}', 'end': docstring.end_lineno or docstring.lineno, 'scalar': False, 'keyed': True}
        facts['docstring'] = ast.get_docstring(documented)  # type: ignore[arg-type]
    functions = _helpers(tree, top, fn, owner)
    channel, channel_line = _error_channel(fn)
    if channel is not None:
        facts['error_channel'], facts['error_channel_site'] = channel, f'{rel}:{channel_line}'
    shapes = _Shapes(root)
    deriver = _Deriver(functions, rel, shapes, path)
    type_, reason = _render(deriver.returns_of(fn, 0))
    classes = lambda node: shapes.shape_class(node, path) is not None  # noqa: E731
    facts['nullable'] = _nullable_args(fn)
    facts['evidence'] = list(dict.fromkeys(deriver.evidence))
    class_fields = _class_fields(tree, fn.returns)
    facts['field_sites'] = {name: f'{rel}:{line}' for name, line in {**{n: line for n, (line, _) in class_fields.items()}, **deriver.field_sites}.items()}
    annotated = {name: (typed, 'annotation') for name, (_, node) in class_fields.items() if (typed := _field_type(node, classes)) is not None}
    facts['field_types'] = {**deriver.field_types(), **annotated}
    facts['shape'] = shapes.response(fn.returns, deriver.validated, path)
    for field, read in (facts['shape'] or {'fields': {}})['fields'].items():
        typed = read['type'] or (read['spec']['type'] if read['spec']['type'] != 'any' else None)
        for spelling in {field, read['alias'] or field}:
            facts['field_sites'].setdefault(spelling, read['site'])
            if typed is not None:
                facts['field_types'][spelling] = (typed, 'annotation')
    declared = _annotation_kinds(fn.returns, classes)
    if type_ != 'unknown':
        as_python = {'str' if k.startswith('json') else k for k in type_.split('|')}
        if declared is not None and declared != as_python:
            facts['conflict'] = f'the code returns {type_} but the annotation says {"|".join(sorted(declared))} ({rel}:{fn.lineno})'
        return facts | {'type': type_, 'type_source': 'code'}
    if declared is not None:
        return facts | {'type': '|'.join(sorted(declared)), 'type_source': 'annotation', 'evidence': [f'{rel}:{fn.lineno}']}
    return facts | {'type': 'unknown', 'type_source': 'declared_unknown', 'unknown_reason': reason}


def response_type(root: Path, name: str, descriptor: dict[str, Any]) -> dict[str, str] | None:
    """`{module, annotation}`: where to resolve the annotation of the agent's own class that types this tool's answer, or None."""
    facts = derive_tool(root, name, descriptor)
    return {'module': facts['module'], 'annotation': facts['shape']['annotation']} if facts['shape'] else None


# --- the in-place YAML edit -------------------------------------------------------------------


def _inline(value: Any) -> str:
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=1 << 30, sort_keys=False).removesuffix('\n').removesuffix('\n...')


class _Block(dict):
    """A nested shape block: written in block style, with one-line leaves, so a reader can follow it."""


class _BlockDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


_BlockDumper.add_representer(_Block, lambda dumper, value: dumper.represent_dict(value.items()))


def _rendered_block(value: dict[str, Any], key_col: int) -> str:
    """`value` as the indented lines under a key at `key_col`, opening with the line break."""
    text = yaml.dump(value, Dumper=_BlockDumper, default_flow_style=None, sort_keys=False, allow_unicode=True, width=1 << 30)
    return ''.join(f'\n{" " * (key_col + 2)}{line}' for line in text.rstrip('\n').split('\n'))


def _holds_block(value: Any) -> bool:
    return isinstance(value, _Block) or (isinstance(value, dict) and any(isinstance(v, _Block) for v in value.values()))


def _trimmed_end(text: str, end: int) -> int:
    """`end` back over blanks: a block scalar's end mark runs past its text, into what follows it."""
    while end > 0 and text[end - 1] in ' \t\r\n':
        end -= 1
    return end


def _block_end(text: str, node: yaml.Node) -> int:
    """Where a block's rewrite ends: its last text, and a comment beside it, which described a line the rewrite no longer has."""
    end = _trimmed_end(text, _end(node))
    line_end = text.find('\n', end)
    line_end = len(text) if line_end < 0 else line_end
    return line_end if text[end:line_end].lstrip().startswith('#') else end


def _pair(node: yaml.Node | None, key: str) -> tuple[yaml.Node, yaml.Node] | None:
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if isinstance(k, yaml.ScalarNode) and k.value == key:
                return k, v
    return None


def _precise(node: yaml.Node) -> bool:
    return (isinstance(node, yaml.ScalarNode) and node.style not in ('|', '>')) or bool(getattr(node, 'flow_style', False))


def _end(node: yaml.Node) -> int:
    if _precise(node) or not isinstance(node, (yaml.MappingNode, yaml.SequenceNode)):
        return node.end_mark.index
    children = [c for pair in node.value for c in pair] if isinstance(node, yaml.MappingNode) else list(node.value)
    return max(_end(c) for c in children)


def _replace(text: str, node: yaml.Node, value: Any) -> tuple[int, int, str]:
    start = node.start_mark.index
    colon = text.rfind(':', 0, start)
    if _holds_block(value) and colon >= 0 and not text[colon + 1:start].strip():
        line = text[text.rfind('\n', 0, colon) + 1:colon]
        return colon + 1, _block_end(text, node), _rendered_block(value, len(line) - len(line.lstrip()))
    if not _precise(node) and colon >= 0 and not text[colon + 1:start].strip():
        start = colon + 1  # a block value starts on the next line: the inline one goes back beside its key
    return start, _end(node), (' ' if text[start - 1] == ':' else '') + _inline(value)


def _set_keys(text: str, node: yaml.Node, current: Any, updates: dict[str, Any], drop: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """The splices that make mapping `node` carry `updates` and lose `drop`, leaving every other byte."""
    if not (isinstance(node, yaml.MappingNode) and not node.flow_style and node.value):
        merged = dict(current) if isinstance(current, dict) else ({'description': current} if isinstance(current, str) else {})
        merged = {k: v for k, v in (merged | updates).items() if k not in drop}
        return [_replace(text, node, merged)]
    edits: list[tuple[int, int, str]] = []
    col = node.value[0][0].start_mark.column
    # New keys go on their own line after the last value whose end is exact (a block scalar's end
    # mark runs past its text); with none, before the first key, keeping that key's indentation.
    anchor = next((_end(v) for _k, v in reversed(node.value) if _precise(v)), None)
    newline = text.find('\n', anchor) if anchor is not None else -1
    inserted = ''
    for key, value in updates.items():
        found = _pair(node, key)
        if found is None:
            spelled = f'{key}:{_rendered_block(value, col)}' if _holds_block(value) else f'{key}: {_inline(value)}'
            inserted += f'{" " * col}{spelled}\n' if anchor is not None else f'{spelled}\n{" " * col}'
        elif (current or {}).get(key) != value:
            edits.append(_replace(text, found[1], value))
    for key in drop:
        found = _pair(node, key)
        if found and text[found[0].start_mark.index - found[0].start_mark.column:found[0].start_mark.index].strip() == '':
            line_end = text.find('\n', _end(found[1]))
            edits.append((found[0].start_mark.index - found[0].start_mark.column, len(text) if line_end < 0 else line_end + 1, ''))
    if inserted and anchor is None:
        edits.append((node.value[0][0].start_mark.index, node.value[0][0].start_mark.index, inserted))
    elif inserted:
        edits.append((len(text), len(text), '\n' + inserted) if newline < 0 else (newline + 1, newline + 1, inserted))
    return edits


def _field_edits(text: str, rnode: yaml.Node, current: Any, field_updates: dict[str, tuple[dict[str, Any], tuple[str, ...]]]) -> list[tuple[int, int, str]]:
    """The splices that put each field's citation in its own spec, leaving the rest of `fields:` alone."""
    fields_pair = _pair(rnode, 'fields')
    fields = current.get('fields') if isinstance(current, dict) else None
    if fields_pair is None or not isinstance(fields_pair[1], yaml.MappingNode) or not isinstance(fields, dict):
        return []
    edits: list[tuple[int, int, str]] = []
    for field, (updates, drop) in field_updates.items():
        spec_pair = _pair(fields_pair[1], field)
        spec = fields.get(field)
        if spec_pair is not None and isinstance(spec, dict):
            edits += _set_keys(text, spec_pair[1], spec, updates, drop)
    return edits


def _replaced_block(text: str, value_node: yaml.Node, value: dict[str, Any]) -> tuple[int, int, str]:
    """The splice that makes a block a `_Block`, from the colon of its key to its last text."""
    colon = text.rfind(':', 0, value_node.start_mark.index)
    line = text[text.rfind('\n', 0, colon) + 1:colon]
    return colon + 1, _block_end(text, value_node), _rendered_block(value, len(line) - len(line.lstrip()))


def _with_fields(returns: Any, field_updates: dict[str, tuple[dict[str, Any], tuple[str, ...]]], block: str = 'fields') -> dict[str, Any]:
    """`returns` with each field's updates applied, for a `returns` node that is rewritten as a whole."""
    merged = dict(returns) if isinstance(returns, dict) else {}
    fields = merged.get(block)
    if isinstance(fields, dict):
        merged[block] = {
            name: ({k: v for k, v in ({**spec, **field_updates[name][0]}).items() if k not in field_updates[name][1]} if name in field_updates and isinstance(spec, dict) else spec)
            for name, spec in fields.items()
        }
    return merged


def patch_config(text: str, derived: dict[str, dict[str, Any]], top: dict[str, Any] | None = None) -> str:
    config = yaml.safe_load(text)
    root = yaml.compose(text)
    edits: list[tuple[int, int, str]] = []
    if top:
        if not (isinstance(root, yaml.MappingNode) and not root.flow_style and root.value):
            raise ValueError('the config root is not a block mapping, so scanned_at cannot be added in place')
        edits += _set_keys(text, root, config, top, ())
    tools_pair = _pair(root, 'tools')
    seen: set[int] = set()
    for name, facts in derived.items():
        tool_pair = _pair(tools_pair[1], name) if tools_pair else None
        desc_pair = _pair(tool_pair[1], 'descriptor') if tool_pair else None
        if desc_pair is None:
            continue
        # A YAML alias makes two tools share one node; one splice would then type both.
        nodes = [desc_pair[1]] + [p[1] for p in (_pair(desc_pair[1], 'returns'), _pair(desc_pair[1], 'args')) if p]
        if any(id(n) in seen for n in nodes):
            raise ValueError(f'tool {name!r} shares a descriptor node with another tool through a YAML alias; expand the alias first')
        seen |= {id(n) for n in nodes}
        if facts.get('channel_updates'):  # the error channel sits on the tool entry, a sibling of `descriptor:`
            edits += _set_keys(text, tool_pair[1], config['tools'][name], facts['channel_updates'], ())  # type: ignore[index]
        descriptor = config['tools'][name]['descriptor']
        field_updates = facts.get('field_updates') or {}
        spelling = facts.get('spelling') or {}  # a finer written spelling of the code's type, and its evidence
        evidence = spelling.get('evidence', facts['evidence'])
        updates: dict[str, Any] = {'type': spelling.get('type', facts['type']), 'type_source': facts['type_source']}
        if evidence:
            updates['evidence'] = evidence
        if facts['unknown_reason']:
            updates['unknown_reason'] = facts['unknown_reason']
        drop = (() if facts['unknown_reason'] else ('unknown_reason',)) + (() if evidence else ('evidence',))
        returns_pair = _pair(desc_pair[1], 'returns')
        returns_node = returns_pair[1] if returns_pair else None
        whole = not (isinstance(returns_node, yaml.MappingNode) and not returns_node.flow_style and returns_node.value)
        current_returns = _with_fields(descriptor.get('returns'), field_updates) if whole and field_updates else descriptor.get('returns')
        described = {'description': facts['returns_description'], 'description_source': 'declared'} if facts.get('returns_description') else {}
        # A nested shape rewrites its block whole; the field splices it already carries would overlap the rewrite.
        rewrite = facts.get('block_rewrite')
        block_name = rewrite[0] if rewrite else None
        block_pair = _pair(returns_node, block_name) if rewrite and not whole else None
        nested = {block_name: _Block(rewrite[1])} if rewrite and (whole or block_pair is None) else {}
        if block_pair is not None:
            edits.append(_replaced_block(text, block_pair[1], _Block(rewrite[1])))
        splices = not (rewrite and block_name == 'fields')
        kept_fields = {'fields': current_returns['fields']} if whole and field_updates and splices else {}
        if facts.get('keep_returns'):
            # A source the model wrote from evidence this derivation does not outrank: its own keys stay byte-identical.
            if returns_pair is not None:
                edits += _set_keys(text, returns_node, current_returns, kept_fields | described | nested, ())
                if not whole and splices:
                    edits += _field_edits(text, returns_node, descriptor.get('returns'), field_updates)
        elif returns_pair is None:
            edits += _set_keys(text, desc_pair[1], descriptor, {'returns': updates | described | nested}, ())
        else:
            edits += _set_keys(text, returns_node, current_returns, updates | kept_fields | described | nested, drop)
            if not whole and splices:
                edits += _field_edits(text, returns_node, descriptor.get('returns'), field_updates)
        args_pair = _pair(desc_pair[1], 'args')
        arg_descriptions = facts.get('arg_descriptions') or {}
        for arg in dict.fromkeys([*facts['nullable'], *arg_descriptions]):
            arg_pair = _pair(args_pair[1], arg) if args_pair else None
            current = (descriptor.get('args') or {}).get(arg)
            if arg_pair is None or not isinstance(current, dict):
                continue
            nullable = facts['nullable'].get(arg)
            arg_updates: dict[str, Any] = {'nullable': nullable} if nullable or (nullable is not None and 'nullable' in current) else {}
            if arg in arg_descriptions:
                arg_updates |= {'description': arg_descriptions[arg], 'description_source': 'declared'}
            if arg_updates:
                edits += _set_keys(text, arg_pair[1], current, arg_updates, ())
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + new + text[end:]
    return text


# --- facts the model reads: declaration sites ----------------------------------------------


def _agent_base(root: Path, base: Path) -> bool:
    return base in (root, root / 'src')


def _search_dirs(root: Path, modules: set[str]) -> list[tuple[Path, Path]]:
    """(directory to walk, the base its paths are relative to): the agent's tree, plus the package folder
    of each tool module found outside it — a library tool declares itself beside its own code."""
    dirs = [(root, root)]
    for module in sorted(modules):
        found = _module_file(root, module)
        if found is not None and not _agent_base(root, found[1]) and (found[0].parent, found[1]) not in dirs:
            dirs.append((found[0].parent, found[1]))  # the tool's own package folder, not the whole distribution
    return dirs


def _source_files(start: Path, exclude: Path) -> list[Path]:
    """The agent's non-test source and data files under `start`; `exclude` is the config being checked."""
    out: list[Path] = []
    for here, subdirs, files in os.walk(start):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith('.') and d not in _SKIP_DIRS)
        for f in sorted(files):
            path = Path(here) / f
            if f.endswith(('.py', '.yaml', '.yml', '.json')) and not (f.startswith('test_') or f.endswith('_test.py') or f == 'conftest.py') and path.resolve() != exclude:
                out.append(path)
    return out


def _yaml_hits(node: yaml.Node, names: set[str], hits: list[tuple[str, int, int]]) -> None:
    """(name, line, end line of the entry that holds it) for each scalar equal to a tool name."""
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            for scalar, holder in ((key, value), (value, node)):
                if isinstance(scalar, yaml.ScalarNode) and scalar.value in names:
                    hits.append((scalar.value, scalar.start_mark.line + 1, holder.end_mark.line + 1))
            _yaml_hits(value, names, hits)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            if isinstance(item, yaml.ScalarNode) and item.value in names:
                hits.append((item.value, item.start_mark.line + 1, item.end_mark.line + 1))
            _yaml_hits(item, names, hits)


def declaration_sites(dirs: list[tuple[Path, Path]], names: set[str], config: Path) -> dict[str, list[dict[str, Any]]]:
    """Per tool name, where the agent's files declare it: `{site: 'rel:line', end: line, scalar, keyed}`,
    best first — a YAML/JSON scalar before a code literal, a line beside a description/returns/schema key
    before one that is not. Found by reading, never by importing."""
    sites: dict[str, list[dict[str, Any]]] = {n: [] for n in names}
    for start, base in dirs:
        for path in _source_files(start, config.resolve()):
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            if len(text) > 1_000_000 or not any(n in text for n in names):
                continue
            hits: list[tuple[str, int, int]] = []
            try:
                if path.suffix == '.py':
                    tree = ast.parse(text)
                    tops = {n: top for top in tree.body for n in ast.walk(top)}
                    hits = [(n.value, n.lineno, getattr(tops.get(n), 'end_lineno', n.lineno) or n.lineno)
                            for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names]
                else:
                    for doc in yaml.compose_all(text):
                        if doc is not None:
                            _yaml_hits(doc, names, hits)
            except (SyntaxError, ValueError, yaml.YAMLError):
                continue
            lines = text.splitlines()
            rel = path.relative_to(base).as_posix()
            for name, line, end in hits:
                near = ' '.join(lines[max(0, line - 4):line + 3])
                sites[name].append({'site': f'{rel}:{line}', 'end': max(end, line), 'scalar': path.suffix != '.py', 'keyed': bool(_DECLARATION_KEY.search(near))})
    for name in sites:
        unique = {s['site']: s for s in sites[name]}.values()
        sites[name] = sorted(unique, key=lambda s: (not s['scalar'], not s['keyed'], s['site']))
    return sites


def _capped(sites: list[str]) -> str:
    shown = ', '.join(sites[:_SITE_CAP])
    return shown + (f' (+{len(sites) - _SITE_CAP} more)' if len(sites) > _SITE_CAP else '')


def _members(declared: Any) -> frozenset[str]:
    return frozenset(m.strip() for m in str(declared).split('|')) if declared is not None else frozenset()


def _alternatives(node: ast.expr) -> frozenset[tuple[str, str, str | None]] | None:
    """A written type's alternatives as `(wire kind, kind, parameter)`: `json[list]` is ('str', 'json', 'list'),
    `List[dict]` is ('list', 'list', 'dict'), `Optional[X]` adds null. None when it names something outside the vocabulary."""
    if isinstance(node, ast.Constant) and node.value is None:
        return frozenset({('null', 'null', None)})
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _alternatives(node.left), _alternatives(node.right)
        return None if left is None or right is None else left | right
    param = None
    if isinstance(node, ast.Subscript):
        members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if _last(node.value) in ('Optional', 'Union'):
            parts = [p for p in (_alternatives(m) for m in members) if p is not None]
            if len(parts) != len(members):
                return None
            null = frozenset({('null', 'null', None)}) if _last(node.value) == 'Optional' else frozenset()
            return null.union(*parts)
        param, node = ast.unparse(node.slice), node.value
    kind = _ANNOTATION_KIND.get(_last(node))
    return frozenset({('str' if kind == 'json' else kind, kind, param)}) if kind else None


def _refines_one(written: tuple[str, str, str | None], code: tuple[str, str, str | None]) -> bool:
    """One written alternative is the code's, or a parameterised spelling of it: `json[...]`/`json` over `str`, `json[X]` over `json`, `list[...]` over `list`, `dict[...]` over `dict`."""
    if written == code:
        return True
    (wire, kind, param), (code_wire, code_kind, code_param) = written, code
    if wire != code_wire or code_param is not None:
        return False
    return (code_kind == 'str' and kind == 'json') or (kind == code_kind and code_kind in ('json', 'list', 'dict') and param is not None)


def _refines(written: Any, code: Any, *, loose: bool = False) -> bool:
    """The written type is the code's type or a refinement of it the AST cannot express. Each side's wire kinds must be
    the same, and each wire kind must hold exactly one alternative on each side (`json` does not refine `json|str`).
    `loose` drops that last condition: it only asks whether the two types contradict each other."""
    if written is None or code is None:
        return False
    if str(written).strip() == str(code).strip():
        return True
    try:
        w, c = _alternatives(ast.parse(str(written), mode='eval').body), _alternatives(ast.parse(str(code), mode='eval').body)
    except SyntaxError:
        return False
    if w is None or c is None:
        return False
    if w == c:
        return True
    if not (all(any(_refines_one(a, b) for a in w) for b in c) and all(any(_refines_one(a, b) for b in c) for a in w)):
        return False
    return loose or all(sum(a[0] == k for a in w) == 1 == sum(b[0] == k for b in c) for k in {b[0] for b in c})


def _literal_agrees(written: Any, code: Any) -> bool:
    """The written type is a `Literal[...]` whose values have exactly the code's kinds: the code proves the kind, not the members."""
    try:
        node = ast.parse(str(written), mode='eval').body
    except SyntaxError:
        return False
    if not (isinstance(node, ast.Subscript) and _last(node.value) == 'Literal'):
        return False
    values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    kinds = {_CONSTANT_KIND.get(type(v.value)) if isinstance(v, ast.Constant) else None for v in values}
    return None not in kinds and kinds == _spelled_kinds(code)


def _contradicts(written: Any, code: Any) -> bool:
    """Neither type refines the other: a coarser or finer spelling of one wire type is no contradiction."""
    return not (_refines(written, code, loose=True) or _refines(code, written, loose=True) or _literal_agrees(written, code))


def _kept(returns: dict[str, Any], facts: dict[str, Any]) -> tuple[bool, str | None]:
    """(keep the config's returns, the conflict to report). A source the model wrote is kept unless this
    script's derivation STRICTLY outranks it. A typed derivation outranks every model-written source, so the
    block is written with the code's type and source (`main` keeps a finer written spelling), and a written
    type it contradicts is reported. A written `Literal[...]` with the code's kinds stays as written."""
    source = returns.get('type_source')
    if source not in _RANK or source in _OWN_SOURCES:
        return False, None
    declared = _members(returns.get('type'))
    typed = facts['type'] != 'unknown' and bool(declared) and 'unknown' not in declared
    outranked = _RANK[facts['type_source']] < _RANK[source]
    clash = typed and _contradicts(returns.get('type'), facts['type'])
    said = f'type_source {source} says {returns.get("type")!r} but the code returns {facts["type"]!r} ({", ".join(facts["evidence"][:3])})'
    if not outranked:
        return True, (said if clash else None)
    if not typed:
        return False, None
    if _literal_agrees(returns.get('type'), facts['type']):
        return True, None
    if not clash or source not in _REPORTED_WHEN_REPLACED:
        return False, None
    return False, f'{said}: the code outranks {source}, so its type replaces it'


def _resolves(cite: str, bases: list[Path]) -> bool:
    """`<path>:<line>` names a line that exists in a file under one of `bases`."""
    match = re.fullmatch(r'(.+?):(\d+)(?:-\d+)?', cite.strip())
    if match is None:
        return False
    for base in bases:
        path = base / match.group(1)
        if path.is_file():
            try:
                return 1 <= int(match.group(2)) <= len(path.read_text(encoding='utf-8', errors='replace').splitlines())
            except OSError:
                return False
    return False


def _citation_findings(name: str, returns: dict[str, Any], sites: list[dict[str, Any]], bases: list[Path]) -> tuple[list[str], list[str]]:
    """(conflicts, advisories) for a kept `declared` / `trace` source. A `declared` evidence line must exist on
    disk; one outside every printed declaration site is only named (a store's schema declares no tool name)."""
    source, evidence = returns.get('type_source'), [str(e) for e in returns.get('evidence') or []]
    if source == 'trace':
        bad = [e for e in evidence if not e.startswith('trace_brief:')]
        return ([f'{name}: type_source trace cites {", ".join(bad) or "nothing"}, not the trace brief (trace_brief:<workspace>#tools.<tool>)'] if bad or not evidence else []), []
    if source != 'declared':
        return [], []
    missing = [e for e in evidence if not _resolves(e, bases)]
    if missing or not evidence:
        return [f'{name}: declared evidence does not resolve to a file:line on disk: {", ".join(missing) or "no evidence"}'], []

    def at_site(cite: str) -> bool:
        path, _, line = cite.partition(':')
        number = int(re.match(r'\d+', line).group()) if re.match(r'\d+', line) else 0  # type: ignore[union-attr]
        for site in sites:
            site_path, _, site_line = site['site'].rpartition(':')
            if site_path == path and int(site_line) <= number <= site['end']:
                return True
        return False
    outside = [e for e in evidence if not at_site(e)]
    return [], ([f'{name}: declared evidence {", ".join(outside)} is at none of the tool\'s declaration sites; confirm it declares this tool\'s output'] if outside else [])


def _cites(evidence: Any) -> list[str]:
    """A `returns.evidence` value, a list or one string, as a list of citations."""
    return [str(c) for c in (evidence if isinstance(evidence, list) else [evidence] if evidence else [])]


def _field_type_keys(spec: dict[str, Any], derived: tuple[str, str] | None) -> tuple[dict[str, Any], tuple[str, ...]]:
    """(keys to set, keys to drop) for a field's `type` and `type_source`. A source that outranks the code stays; a code
    type is written with its source, keeping a written spelling that is the code's type or a refinement of it; a written
    `Literal[...]` with the code's kinds stays as written; a field the code no longer types loses the source an earlier
    scan wrote, so its type reads as a guess again."""
    source = spec.get('type_source')
    if source in _RANK and source not in _OWN_SOURCES and derived is not None and _RANK[source] < _RANK[derived[1]]:
        return {}, ()
    if derived is None:
        return {}, ('type_source',) if source in _OWN_SOURCES else ()
    typed, by = derived
    if _refines(spec.get('type'), typed):
        return {'type_source': by}, ()
    if source not in _OWN_SOURCES and _literal_agrees(spec.get('type'), typed):
        return {}, ()
    return {'type': typed, 'type_source': by}, ()


def _field_updates(fields: Any, sites: dict[str, str], types: dict[str, tuple[str, str]] | None = None) -> dict[str, tuple[dict[str, Any], tuple[str, ...]]]:
    """Per `returns.fields` entry: the citation the code gives it when it cites nothing, and (with `types`) the type the code gives it."""
    updates: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {}
    for field, spec in (fields.items() if isinstance(fields, dict) else ()):
        if not isinstance(spec, dict):
            continue
        set_keys: dict[str, Any] = {}
        if not _cites(spec.get('evidence')) and field in sites:
            set_keys['evidence'] = sites[field]
        typed, type_drop = _field_type_keys(spec, types.get(field)) if types is not None else ({}, ())
        set_keys.update(typed)
        if set_keys or type_drop:
            updates[field] = (set_keys, type_drop)
    return updates


def shared_fields(text: str, names: Any) -> dict[str, list[str]]:
    """Per tool whose `returns.fields` block a YAML alias shares with other tools: those other tools. One splice
    into a shared block would give every sharing tool the last tool's field types, so such a block is left as written."""
    root = yaml.compose(text)
    tools_pair = _pair(root, 'tools')
    owners: dict[int, list[str]] = {}
    for name in names:
        tool_pair = _pair(tools_pair[1], name) if tools_pair else None
        desc_pair = _pair(tool_pair[1], 'descriptor') if tool_pair else None
        returns_pair = _pair(desc_pair[1], 'returns') if desc_pair else None
        fields_pair = _pair(returns_pair[1], 'fields') if returns_pair else None
        if fields_pair is not None and isinstance(fields_pair[1], yaml.MappingNode):
            owners.setdefault(id(fields_pair[1]), []).append(name)
    return {n: [o for o in group if o != n] for group in owners.values() if len(group) > 1 for n in group}


def _field_type_diffs(name: str, fields: Any, updates: dict[str, tuple[dict[str, Any], tuple[str, ...]]]) -> list[str]:
    """`--check`'s field findings: a field whose `type` or `type_source` `--write` would change."""
    diffs = []
    for field, (set_keys, drop) in updates.items():
        spec = fields.get(field) if isinstance(fields, dict) else None
        spec = spec if isinstance(spec, dict) else {}
        changed = {k: v for k, v in set_keys.items() if k in ('type', 'type_source') and spec.get(k) != v}
        if changed or ('type_source' in drop and 'type_source' in spec):
            want = f'{set_keys.get("type", spec.get("type"))!r} ({set_keys.get("type_source") or "no type_source"})'
            diffs.append(f'{name}.fields.{field}: declared {spec.get("type")!r} ({spec.get("type_source") or "no type_source"}), the code gives {want}')
    return diffs


def _plugin_field_notes(name: str, fields: Any, types: dict[str, tuple[str, str]]) -> list[str]:
    """A field type the plugin decided that the code contradicts: named, never changed."""
    notes = []
    for field, spec in (fields.items() if isinstance(fields, dict) else ()):
        derived = types.get(field)
        if isinstance(spec, dict) and spec.get('type_source') == 'plugin' and derived is not None and _spelled_kinds(spec.get('type')) != _spelled_kinds(derived[0]):
            notes.append(f'{name}.fields.{field}: type_source plugin says {spec.get("type")!r} but the code gives {derived[0]!r}; the plugin decided it, so it stays')
    return notes


def _anchored(text: str, name: str, block: str) -> bool:
    """A YAML anchor or alias inside the tool's `returns.<block>`: rewriting it would strand or lose a reference."""
    tools_pair = _pair(yaml.compose(text), 'tools')
    tool_pair = _pair(tools_pair[1], name) if tools_pair else None
    desc_pair = _pair(tool_pair[1], 'descriptor') if tool_pair else None
    returns_pair = _pair(desc_pair[1], 'returns') if desc_pair else None
    block_pair = _pair(returns_pair[1], block) if returns_pair else None
    if block_pair is None:
        return False
    # By indentation: an alias's node carries the marks of its anchor, somewhere else in the file.
    start = block_pair[0].start_mark.index
    col = block_pair[0].start_mark.column
    end, at = len(text), text.find('\n', start)
    while at >= 0:
        line = text[at + 1:text.find('\n', at + 1) if text.find('\n', at + 1) >= 0 else len(text)]
        if line.strip() and len(line) - len(line.lstrip()) <= col:
            end = at
            break
        at = text.find('\n', at + 1)
    return any(isinstance(t, (yaml.AnchorToken, yaml.AliasToken)) and start <= t.start_mark.index < end for t in yaml.scan(text))


def _over_written(written: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """The code's nested keys over what is written, at every level: a key the code does not state (`description`, `enum`)
    stays. A class the AST could not open keeps what is written."""
    opaque = spec['type'] == 'any'
    merged = {k: v for k, v in written.items() if k not in _NESTED_KEYS or (opaque and k not in ('optional', 'wire_name'))}
    merged |= {k: spec[k] for k in _NESTED_KEYS if k in spec}
    for block in ('fields', 'item_fields'):
        if block in spec:  # a list's item fields may be written under `fields`, as `_block_of` keeps them at the top
            held = written.get(block) or written.get('item_fields' if block == 'fields' else 'fields')
            merged[block] = {name: _nested_entry(held.get(name) if isinstance(held, dict) else None, entry) for name, entry in spec[block].items()}
    return merged


def _nested_entry(written: Any, code: dict[str, Any]) -> dict[str, Any]:
    """One entry below the top of a block: as `_over_written`, and the code's type unless the written one refines it. A type
    the code replaces takes its written source and citation with it. A `plugin` entry stays."""
    if not isinstance(written, dict):
        return code
    if written.get('type_source') == 'plugin':
        return written
    merged = _over_written(written, code)
    if code['type'] == 'any':
        return {**merged, 'type': written.get('type', 'any')}
    if _refines(written.get('type'), code['type']) or _literal_agrees(written.get('type'), code['type']):
        return merged
    return {**{k: v for k, v in merged.items() if k not in ('type_source', 'evidence')}, 'type': code['type']}


def _nested_field(current: dict[str, Any], read: dict[str, Any], own_key: str, field: str) -> dict[str, Any]:
    """`current` with the nested reading in place of what an earlier one, or a model, wrote. A field whose class the AST
    could not open keeps what is written. The config's own key stays; `wire_name` says the key to emit where the model does not take it."""
    merged = _over_written(current, read['spec'])
    if own_key == field and read['key'] != field:
        merged['wire_name'] = read['key']
    return merged


def _new_field(read: dict[str, Any]) -> dict[str, Any]:
    spec = read['spec']
    typed = read['type'] or (spec['type'] if spec['type'] != 'any' else None)
    return {**({'type': typed, 'type_source': 'annotation'} if typed else {}), 'evidence': read['site'], **{k: spec[k] for k in _NESTED_KEYS if k in spec}}


def _block_of(returns: dict[str, Any], shape: dict[str, Any]) -> str:
    """The block a shape goes in: a list's item fields a config already lists under `fields` stay there."""
    return 'fields' if shape['block'] == 'item_fields' and isinstance(returns.get('fields'), dict) and not returns.get('item_fields') else shape['block']


def nested_block(returns: dict[str, Any], shape: dict[str, Any] | None, updates: dict[str, tuple[dict[str, Any], tuple[str, ...]]]) -> dict[str, Any] | None:
    """The `returns.<block>` the code's nested shape asks for, or None when the block already says it. A `plugin` block or
    field stays. `updates` are the field splices the block already owes: they alone never rewrite it."""
    if shape is None or returns.get('type_source') == 'plugin':
        return None
    block = _block_of(returns, shape)
    written = returns.get(block)
    written = written if isinstance(written, dict) else {}
    applied = _with_fields({block: written}, updates, block)[block]
    merged = dict(applied)
    for field, read in shape['fields'].items():
        own = next((k for k in merged if k in {field, read['alias']}), None)
        if own is None:
            merged[read['key']] = _new_field(read)
        elif isinstance(merged[own], dict) and merged[own].get('type_source') != 'plugin':
            merged[own] = _nested_field(merged[own], read, own, field)
    # `returns.fields` has its own field splices; nothing else splices into `item_fields`, so what it owes is a rewrite.
    return None if merged == (applied if block == 'fields' else written) else merged


def _scan_header(scanned_at: str) -> dict[str, Any]:
    return {'scanned_at': scanned_at}


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(description='Derive mocked tools\' return types and nullable args from their source.')
    parser.add_argument('config', type=Path, help='path to plurai.config.yaml')
    parser.add_argument('--root', type=Path, default=Path('.'), help="the agent's repo root, where original_module resolves (default: .)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true', help='patch the derived keys into the config in place')
    mode.add_argument('--check', action='store_true', help='compare the config against the derivation; exit 1 on any difference')
    parser.add_argument('--scanned-at', default=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), help='the scan time written as scanned_at, UTC ISO 8601 (default: now)')
    parser.add_argument('--json', action='store_true', help='print the facts as one JSON document on stdout (the report goes to stderr)')
    args = parser.parse_args(argv)
    out = sys.stderr if args.json else sys.stdout
    if not args.config.is_file() or not args.root.is_dir():
        print(f'derive_tool_facts: need a config file and a root directory (got {args.config}, {args.root})', file=sys.stderr)
        return 2
    text = args.config.read_text(encoding='utf-8')
    tools = {n: e for n, e in ((yaml.safe_load(text) or {}).get('tools') or {}).items() if isinstance(e, dict) and isinstance(e.get('descriptor'), dict)}
    root = args.root.resolve()
    try:
        _import_path(_agent_python())
    except RuntimeError as exc:
        print(f'derive_tool_facts: {exc}', file=sys.stderr)
        return 2
    print(f'derive_tool_facts: library sources read on {_agent_python()} ({"PLURAI_CHILD_PYTHON" if os.environ.get("PLURAI_CHILD_PYTHON") else "this interpreter"})', file=out)
    derived = {name: derive_tool(root, name, entry['descriptor']) for name, entry in tools.items() if entry.get('mode') == 'mock'}
    for name, facts in derived.items():
        facts['arg_descriptions'], facts['returns_description'] = description_fills(tools[name]['descriptor'], facts['docstring'])
    configured = {name: tools[name]['descriptor'].get('returns') if isinstance(tools[name]['descriptor'].get('returns'), dict) else {} for name in derived}
    kept: dict[str, bool] = {}
    conflicts = [f'{name}: {facts["conflict"]}' for name, facts in derived.items() if facts['conflict']]
    advisories: list[str] = []
    for name, facts in derived.items():
        kept[name], clash = _kept(configured[name], facts)
        if clash and configured[name].get('type_source') == 'plugin':
            advisories.append(f'{name}: {clash}; the plugin decided it, so it stays')  # the plugin is the admin: a note, never a failure
        elif clash:
            conflicts.append(f'{name}: {clash}')
    for name, facts in derived.items():
        # Whatever the written source (the script's own included, so a second --write is a fixed point), a written type
        # strictly finer than the code's keeps its spelling; its file:line citations stay beside the code's evidence.
        written = configured[name].get('type')
        if not kept[name] and facts['type'] != 'unknown' and _refines(written, facts['type']) and not _refines(facts['type'], written):
            cites = [c for c in _cites(configured[name].get('evidence')) if re.fullmatch(r'.+?:\d+(?:-\d+)?', c.strip())]
            facts['spelling'] = {'type': written, 'evidence': list(dict.fromkeys([*facts['evidence'], *cites]))}
    # Declaration sites: for a tool the code cannot type, a function several tools share, or a `declared` source.
    shared: dict[tuple[Any, Any], int] = {}
    for name in derived:
        key = (tools[name]['descriptor'].get('original_module'), tools[name]['descriptor'].get('original_function'))
        shared[key] = shared.get(key, 0) + 1
    wanted = {name for name, facts in derived.items()
              if facts['type'] == 'unknown' or configured[name].get('type_source') == 'declared'
              or shared[(tools[name]['descriptor'].get('original_module'), tools[name]['descriptor'].get('original_function'))] > 1}
    modules = {str(tools[n]['descriptor'].get('original_module')) for n in wanted if tools[n]['descriptor'].get('original_module')}
    found_sites = declaration_sites(_search_dirs(root, modules), wanted, args.config) if wanted else {}
    sites: dict[str, list[dict[str, Any]]] = {}
    for name in wanted:
        doc = derived[name]['docstring_site']
        sites[name] = ([doc] if doc else []) + [s for s in found_sites.get(name, []) if not doc or s['site'] != doc['site']]
    bases = [root, root / 'src', args.config.resolve().parent, *_import_path(_agent_python())]
    notes: list[str] = []
    for name in derived:
        if kept[name]:
            bad, unsited = _citation_findings(name, configured[name], sites.get(name, []), bases)
            conflicts += bad
            notes += unsited
    for line in notes:
        print(f'  ! evidence: {line}', file=out)
    for name in sorted(derived):
        if derived[name]['respelled']:
            print(f'  ! {name}: {derived[name]["respelled"]}', file=out)
        if derived[name]['type'] == 'unknown' and (not kept[name] or derived[name]['source_missing']):
            held = '; the written returns block is kept, and nothing read it against the code' if kept[name] else ''
            print(f'  ? {name}: unknown ({derived[name]["unknown_reason"]}){held}', file=out)
        if sites.get(name):
            print(f'    {name} declared at: {_capped([s["site"] for s in sites[name]])}', file=out)
    aliased = shared_fields(text, derived)
    for name, facts in derived.items():
        if name in aliased:
            facts['field_updates'] = {}
            continue
        facts['field_updates'] = _field_updates(configured[name].get('fields'), facts['field_sites'], types=facts['field_types'])
        advisories += _plugin_field_notes(name, configured[name].get('fields'), facts['field_types'])
        shape = facts['shape']
        block = _block_of(configured[name], shape) if shape else 'fields'
        owed = facts['field_updates'] if block == 'fields' else _field_updates(configured[name].get(block), facts['field_sites'], types=facts['field_types'])
        rewritten = nested_block(configured[name], shape, owed)
        if rewritten is not None and _anchored(text, name, block):
            advisories.append(f'{name}: returns.{block} holds a YAML anchor or alias, so its nested shape was not written (expand it to write one)')
        elif rewritten is not None:
            facts['block_rewrite'] = (block, rewritten)
    for name in sorted(aliased):
        advisories.append(f'{name}: shares one returns.fields block with {", ".join(sorted(aliased[name]))} through a YAML alias; '
                          'its field types and citations are left as written (expand the alias to type each tool\'s fields)')
    channel_diffs: list[str] = []
    for name, facts in derived.items():
        # The error channel the tool's own body decides: written over an `inferred` or absent source only.
        code, entry = facts['error_channel'], tools[name]
        written, source = entry.get('error_channel'), entry.get('error_channel_source')
        if code is None:
            continue
        if source in (None, 'inferred'):
            if (written, source) != (code, 'inferred'):
                facts['channel_updates'] = {'error_channel': code, 'error_channel_source': 'inferred'}
            if written is not None and written != code:
                channel_diffs.append(f'{name}.error_channel: declared {written!r} ({source or "no error_channel_source"}), the code gives {code!r} '
                                     f'(inferred, {facts["error_channel_site"]})')
        elif written is not None and written != code and source in ('plugin', 'trace', 'declared'):
            said = f'{name}: error_channel_source {source} says {written!r} but the code gives {code!r} ({facts["error_channel_site"]})'
            if source == 'plugin':
                advisories.append(f'{said}; the plugin decided it, so it stays')  # the plugin is the admin: a note, never a failure
            else:
                conflicts.append(said)
    for line in advisories:
        print(f'  ! {line}', file=out)
    rows = {name: row_sources(root, name, tools[name]['descriptor']) or [] for name in derived if row_source_tool(tools[name])}
    print(f'derive_tool_facts: row sources of tool_body read tools: {sum(map(len, rows.values()))} line(s) on {sum(map(bool, rows.values()))} of {len(rows)} tool(s)'
          + ' (answer each: a business rule on that line naming the values it keeps, or skipped_code {cite, returns: every_row, evidence})', file=out)
    exit_code = 0
    if args.check:
        diffs = []
        for name, facts in derived.items():
            descriptor = tools[name]['descriptor']
            returns = configured[name]
            spelled = (facts.get('spelling') or {}).get('type')
            if not kept[name] and (_members(returns.get('type')) != _members(spelled or facts['type']) or returns.get('type_source') != facts['type_source']):
                why = ('; the written spelling refines it and stays, type_source becomes ' + facts['type_source'] if spelled
                       else "; the code's type is finer" if _refines(facts['type'], returns.get('type')) and not _refines(returns.get('type'), facts['type']) else '')
                diffs.append(f'{name}: declared {returns.get("type")!r} ({returns.get("type_source") or "no type_source"}), the code gives {facts["type"]!r} ({facts["type_source"]}){why}')
            declared_args = descriptor.get('args') if isinstance(descriptor.get('args'), dict) else {}
            for arg, nullable in facts['nullable'].items():
                spec = declared_args.get(arg)
                if isinstance(spec, dict) and bool(spec.get('nullable')) != nullable:
                    diffs.append(f'{name}.{arg}: declared nullable={bool(spec.get("nullable"))}, the code gives {nullable}')
            diffs += _field_type_diffs(name, returns.get('fields'), facts['field_updates'])
            if facts.get('block_rewrite'):
                diffs.append(f'{name}.returns.{facts["block_rewrite"][0]}: the code gives a nested shape the config does not state')
        diffs += channel_diffs
        for name in sorted(derived):
            documented = [*derived[name]['arg_descriptions'], *(['returns'] if derived[name]['returns_description'] else [])]
            if documented:
                print(f'  ! {name}: undescribed in the config, documented by the declaration: {", ".join(documented)}', file=out)
        for line in diffs + conflicts:
            print(f'  ✗ {line}', file=out)
        print(f'derive_tool_facts: {len(derived)} mocked tool(s) compared, {len(diffs)} difference(s), {len(conflicts)} conflict(s)', file=out)
        exit_code = 1 if diffs or conflicts else 0
        effective = {name: configured[name].get('type_source') for name in derived}
    else:
        try:
            patched = patch_config(text, {name: facts | {'keep_returns': kept[name]} for name, facts in derived.items()}, _scan_header(args.scanned_at))
        except ValueError as exc:
            print(f'derive_tool_facts: {exc}', file=sys.stderr)
            return 2
        args.config.write_text(patched, encoding='utf-8')
        for line in conflicts:
            print(f'  ✗ {line}', file=out)
        exit_code = 1 if conflicts else 0
        effective = {name: configured[name].get('type_source') if kept[name] else derived[name]['type_source'] for name in derived}
    decided = [facts['error_channel'] for facts in derived.values()]
    print(f'derive_tool_facts: error_channel the code decides: returns {decided.count("returns")} · raises {decided.count("raises")} · neither {decided.count(None)}'
          + f'; {"wrote" if args.write else "would write"} {sum(bool(f.get("channel_updates")) for f in derived.values())}', file=out)
    filled = [name for name, facts in derived.items() if facts['arg_descriptions'] or facts['returns_description']]
    print(f'derive_tool_facts: descriptions the declaration documents and the config leaves empty: args {sum(len(derived[n]["arg_descriptions"]) for n in filled)}'
          + f' · returns {sum(bool(derived[n]["returns_description"]) for n in filled)} on {len(filled)} tool(s); {"filled" if args.write else "--write fills them"}', file=out)
    by_source = {s: sorted(n for n, src in effective.items() if src == s) for s in _RANK}
    code_only = sorted(by_source['inferred'] + by_source['declared_unknown'])
    print(f'derive_tool_facts: {"wrote" if args.write else "read"} {len(derived)} mocked tool(s) — returns by source: '
          + ' · '.join(f'{s} {len(by_source[s])}' for s in _RANK), file=out)
    print(f'derive_tool_facts: {len(code_only)} tool return shape(s) read from code only (inferred or declared_unknown)'
          + (f': {", ".join(code_only)}' if code_only else ''), file=out)
    if missing := sorted(name for name, facts in derived.items() if facts['source_missing']):
        print(f'derive_tool_facts: source not found for {len(missing)} tool(s): {", ".join(missing)}. Fix original_module, original_function or --root; '
              'a type_source written over them does not make the code read', file=out)
    if args.json:
        print(json.dumps({
            'tools': {name: {
                'mode': tools[name].get('mode'),
                'type_source': effective.get(name),
                'derived': {k: derived[name][k] for k in ('type', 'type_source', 'evidence', 'unknown_reason', 'error_channel', 'error_channel_site')} if name in derived else None,
                'declaration_sites': [s['site'] for s in sites.get(name, [])],
                **({'row_sources': rows[name]} if name in rows else {}),
            } for name in derived},
            'by_source': {s: len(v) for s, v in by_source.items()},
            'code_only': code_only,
            'conflicts': conflicts,
            'evidence_notes': notes,
            'advisories': advisories,
        }, indent=2, sort_keys=True))
    return exit_code


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
