#!/usr/bin/env python3
"""Every place in the agent's code that writes, defaults or reads each name the PRD's claims are about.

A PRD claim about WHO writes a field or WHAT feeds a check ("set only by X", "Y does not count") is
true or false at sites the claim does not name: a second writer, a shared builder's `else 0.0`
default, the line that adds rows to the counted list. The author reads one caller, and the line
it cites is real. The independent claim audit (simulation-setup Step 9c) is the reader; this script
only makes its list of sites COMPLETE, so that reader opens every one, not the one the author chose.

The names are the DECLARED surface, never a guess: every entity field (`plurai.config.yaml`
`world_state.entity_types`, `scenario_state_schema.yaml` `entities`), every `agent_context` field,
every tool, the subject of every `hard_limit` / `writes` / `run_setting` fact, and every
identifier-shaped back-ticked name in the PRD before its appendix (`min_samples_per_label`).

Python is read with `ast`: a keyword argument (`kwarg`, or `kwarg-default` when the value is a
conditional default such as `x if x is not None else 0.0`), an assignment (`assign` /
`assign-default`), an attribute or subscript store, a dict-literal key, a parameter, a definition, a
read inside a comparison (`compare`) and any other read. Other languages fall back to a
word-boundary grep (`mention`). Writes sort first, and each name keeps at most `MAX_SITES` sites
with its true total beside them.

Usage:
    claim_sites.py --repo-root . --config plurai_sim/plurai.config.yaml [--prd plurai_sim/simulation_prd.md] [--out ...]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import _confine  # noqa: E402
import lint_prd  # noqa: E402 - the one reader of the facts block and the appendix boundary

MAX_SITES = 40
#: Strongest first: the order the sites are listed in, and the kind kept when a list is truncated.
KIND_ORDER = ('def', 'assign-default', 'kwarg-default', 'assign', 'kwarg', 'dict-key', 'param', 'compare', 'read', 'string', 'mention')
SKIP_DIRS = {'plurai_sim', 'node_modules', 'venv', '.venv', 'env', 'site-packages', '__pycache__', 'dist', 'build'}
GREP_SUFFIXES = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.go', '.java', '.kt', '.rb', '.rs', '.cs', '.php', '.swift', '.scala', '.cls', '.apex'}
#: A back-ticked PRD token that names code: snake_case or camelCase, never a bare word.
_PRD_NAME = re.compile(r'`([A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|[a-z]+[A-Z][A-Za-z0-9]*)`')
_FACT_SETTING = ('hard_limit', 'writes', 'harness_differs')


def declared_names(config: dict, schema: dict, prd_text: str) -> dict[str, list[str]]:
    """name -> how it was declared (`field:dataset.score`, `tool:x`, `fact:hard_limit:x.y`, `prd`)."""
    out: dict[str, list[str]] = {}

    def add(name: object, how: str) -> None:
        if isinstance(name, str) and len(name) >= 3 and name.isidentifier():
            out.setdefault(name, []).append(how)

    def fields(entity: str, spec: dict) -> None:
        for name, field in spec.items():
            add(name, f'field:{entity}.{name}')
            if isinstance(field, dict) and isinstance(field.get('item_specs'), dict):
                fields(f'{entity}.{name}[]', field['item_specs'])

    types = (config.get('world_state') or {}).get('entity_types') or {}
    for entity, spec in {**(schema.get('entities') or {}), **types}.items():
        if isinstance(spec, dict):
            fields(entity, spec.get('fields') or spec.get('field_specs') or {})
    for name in ((config.get('agent_context') or {}).get('fields') or {}):
        add(name, f'context:{name}')
    for name in (config.get('tools') or {}):
        add(name, f'tool:{name}')
    facts, _ = lint_prd._facts_entries(prd_text)
    for fact in facts or []:
        if isinstance(fact, dict) and fact.get('predicate') in _FACT_SETTING:
            for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', str(fact.get('subject') or '')):
                add(token, f'fact:{fact["predicate"]}:{fact.get("subject")}')
    body = lint_prd._without_appendix(prd_text).splitlines()
    for line, fenced in zip(body, lint_prd._fenced(body)):
        for token in [] if fenced else _PRD_NAME.findall(line):
            if 'prd' not in out.get(token, []):
                add(token, 'prd')
    return out


def _python_sites(tree: ast.AST, names: set[str]) -> list[tuple[str, int, str]]:
    """(name, line, kind) for every touch of a declared name in one module."""
    found: list[tuple[str, int, str]] = []
    compared = {id(sub) for node in ast.walk(tree) if isinstance(node, ast.Compare) for sub in ast.walk(node)}
    defaulted: set[int] = set()
    for node in ast.walk(tree):
        value = getattr(node, 'value', None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_default(value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defaulted |= {id(sub) for t in targets for sub in ast.walk(t)}
    for node in ast.walk(tree):
        name, kind = None, None
        if isinstance(node, ast.keyword) and node.arg in names:
            name, kind = node.arg, 'kwarg-default' if _is_default(node.value) else 'kwarg'
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            name, kind = node.name, 'def'
        elif isinstance(node, ast.arg) and node.arg in names:
            name, kind = node.arg, 'param'
        elif isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else _const(node.slice)
            if name in names:
                store = isinstance(node.ctx, (ast.Store, ast.Del))
                kind = ('assign-default' if id(node) in defaulted else 'assign') if store else 'compare' if id(node) in compared else 'read'
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if _const(key) in names:
                    found.append((_const(key), key.lineno, 'dict-key'))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args and _const(node.args[0]) in names:
            name, kind = _const(node.args[0]), 'read' if node.func.attr in ('get', 'getattr') else 'string'
        elif isinstance(node, ast.Constant) and node.value in names:
            name, kind = node.value, 'string'
        if name in names and kind:
            found.append((name, node.lineno, kind))
    return found


def _is_default(value: object) -> bool:
    return isinstance(value, ast.IfExp) or (isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or))


def _const(node: object) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


#: A test file: its sites say what a test sets up, not what the agent does, so they rank after every agent site.
_TEST_PATH = re.compile(r'(?:^|/)(?:tests?|__tests__)/|(?:^|/)test_[^/]*\.py$|_test\.py$|\.(?:test|spec)\.[jt]sx?$')


def is_test(rel: str) -> bool:
    return bool(_TEST_PATH.search(rel))


def collect(repo_root: Path, declared: dict[str, list[str]]) -> dict[str, dict]:
    """name -> {declared_as, total, truncated, sites: [{at, kinds, text}]}, sites strongest-first."""
    names = set(declared)
    grep = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(sorted(map(re.escape, names), key=len, reverse=True)) + r')(?![A-Za-z0-9_])') if names else None
    hits: dict[str, dict[tuple[str, int], set[str]]] = {n: {} for n in names}
    lines_of: dict[str, list[str]] = {}
    for base, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for file in sorted(files):
            path = Path(base) / file
            try:  # a dangling symlink or an unreadable file has no sites to list
                if path.suffix not in GREP_SUFFIXES | {'.py'} or path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            rel = path.relative_to(repo_root).as_posix()
            try:
                sites = _python_sites(ast.parse(text), names) if path.suffix == '.py' else None
            except SyntaxError:
                sites = None
            if sites is None and grep:
                sites = [(m.group(1), i, 'mention') for i, line in enumerate(text.splitlines(), 1) for m in grep.finditer(line)]
            if sites:
                lines_of[rel] = text.splitlines()
            for name, line, kind in sites or []:
                hits[name].setdefault((rel, line), set()).add(kind)
    out: dict[str, dict] = {}
    for name in sorted(names):
        ranked = sorted(hits[name].items(), key=lambda item: (is_test(item[0][0]), min(KIND_ORDER.index(k) for k in item[1]), item[0]))
        out[name] = {
            'declared_as': declared[name],
            'total': len(ranked),
            'truncated': len(ranked) > MAX_SITES,
            'sites': [
                {'at': f'{rel}:{line}', 'kinds': sorted(kinds, key=KIND_ORDER.index), 'text': lines_of[rel][line - 1].strip()[:160]}
                for (rel, line), kinds in ranked[:MAX_SITES]
            ],
        }
    return out


def _yaml(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
    return loaded if isinstance(loaded, dict) else {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--repo-root', type=Path, default=Path('.'))
    ap.add_argument('--config', type=Path, required=True, help='plurai.config.yaml')
    ap.add_argument('--schema', type=Path, help='scenario_state_schema.yaml (default: beside the config)')
    ap.add_argument('--prd', type=Path, help='simulation_prd.md (default: beside the config)')
    ap.add_argument('--out', type=Path, help='default: artifacts/claim_sites.json beside the config')
    _confine.add_flag(ap)
    args = ap.parse_args(argv)
    workdir = args.config.parent
    out = args.out or workdir / 'artifacts' / 'claim_sites.json'
    if why := _confine.refusal(out, allow_outside=args.allow_outside_workdir):
        print(f'claim_sites: {why}', file=sys.stderr)
        return 2
    if not args.config.is_file():
        print(f'claim_sites: --config {args.config} does not exist; it declares the names whose sites are listed.', file=sys.stderr)
        return 2
    prd =args.prd or workdir / 'simulation_prd.md'
    prd_bytes = prd.read_bytes() if prd.is_file() else b''
    declared = declared_names(_yaml(args.config), _yaml(args.schema or workdir / 'scenario_state_schema.yaml'), prd_bytes.decode('utf-8', 'replace'))
    names = collect(args.repo_root.resolve(), declared)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {'prd_sha256': hashlib.sha256(prd_bytes).hexdigest() if prd_bytes else None, 'max_sites': MAX_SITES, 'names': names}
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    untouched = sorted(n for n, entry in names.items() if not entry['total'])
    print(f'claim_sites: {len(names)} declared names, {sum(e["total"] for e in names.values())} sites, '
          f'{len(untouched)} touched nowhere in the code -> {out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
