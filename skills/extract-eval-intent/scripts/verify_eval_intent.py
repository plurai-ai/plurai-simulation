#!/usr/bin/env python3
"""Check `eval_intent.yaml` against `plurai-eval-intent/2` before anything consumes it.

    verify_eval_intent.py <eval_intent.yaml>

The backend reads this file as YAML and, on a parse failure, degrades silently: every intent-coverage
check is skipped. Nothing else on the path validates it, so this does, naming each defect by its field:

* it parses, and is a mapping with `schema: plurai-eval-intent/2`;
* every key is one `references/eval_intent_schema.json` declares — an invented key is refused, never dropped;
* every axis has `id`, `axis` and at least one failure mode; every failure mode has `id`, `describes` and
  at least one `traced_to` with a legal `source` and a `ref`; `prd` and `corpus` sources carry a `quote`;
* the ids — axes and failure modes together — are ONE space: none repeats.

Exit codes: 0 clean (one summary line) · 2 the file has defects (one line each, naming the field) ·
3 the check could not run (PyYAML missing from this interpreter, or the schema file unreadable) — the
document was not read, so it is not the thing to change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # the INTERPRETER is the problem, not the document: exit 3, never a defect
    yaml = None

SCHEMA = Path(__file__).resolve().parent.parent / 'references' / 'eval_intent_schema.json'
VERSION = 'plurai-eval-intent/2'


def _keys(node: dict) -> set[str]:
    return set((node.get('properties') or {}).keys())


def problems(doc: object, schema: dict) -> list[str]:
    """One line per defect, each naming its field path; `[]` for a clean document."""
    if not isinstance(doc, dict):
        return ['the document is not a mapping (expected `schema:` and `axes:` at the top level)']
    out: list[str] = []
    if doc.get('schema') != VERSION:
        out.append(f'`schema`: must be `{VERSION}`, got {doc.get("schema")!r}')
    out += [f'`{key}`: not a key of {VERSION}' for key in sorted(set(doc) - _keys(schema))]
    axis_schema = schema['properties']['axes']['items']
    mode_schema = axis_schema['properties']['failure_modes']['items']
    trace_schema = mode_schema['properties']['traced_to']['items']
    sources = trace_schema['properties']['source']['enum']
    surfaces = axis_schema['properties']['surface']['enum']
    prefs = doc.get('preferences')
    if prefs is not None:
        if not isinstance(prefs, dict):
            out.append('`preferences`: must be a mapping')
        else:
            out += [f'`preferences.{key}`: not a key of {VERSION}' for key in sorted(set(prefs) - _keys(schema['properties']['preferences']))]
    axes = doc.get('axes')
    if not isinstance(axes, list) or not axes:
        return out + ['`axes`: at least one axis is required']
    seen: dict[str, str] = {}

    def _id(value: object, where: str) -> None:
        if not isinstance(value, str) or not value.strip():
            out.append(f'`{where}.id`: required')
        elif value in seen:
            out.append(f'`{where}.id`: {value!r} repeats `{seen[value]}.id` — ids are ONE space across the file')
        else:
            seen[value] = where

    for i, axis in enumerate(axes):
        here = f'axes[{i}]'
        if not isinstance(axis, dict):
            out.append(f'`{here}`: must be a mapping')
            continue
        out += [f'`{here}.{key}`: not a key of an axis' for key in sorted(set(axis) - _keys(axis_schema))]
        _id(axis.get('id'), here)
        if not str(axis.get('axis') or '').strip():
            out.append(f'`{here}.axis`: required')
        if 'surface' in axis and axis['surface'] not in surfaces:
            out.append(f'`{here}.surface`: {axis["surface"]!r} is not one of {", ".join(surfaces)}')
        modes = axis.get('failure_modes')
        if not isinstance(modes, list) or not modes:
            out.append(f'`{here}.failure_modes`: at least one failure mode is required')
            continue
        for j, mode in enumerate(modes):
            where = f'{here}.failure_modes[{j}]'
            if not isinstance(mode, dict):
                out.append(f'`{where}`: must be a mapping')
                continue
            out += [f'`{where}.{key}`: not a key of a failure mode' for key in sorted(set(mode) - _keys(mode_schema))]
            _id(mode.get('id'), where)
            if not str(mode.get('describes') or '').strip():
                out.append(f'`{where}.describes`: required')
            traces = mode.get('traced_to')
            if not isinstance(traces, list) or not traces:
                out.append(f'`{where}.traced_to`: REQUIRED — name where this failure mode came from')
                continue
            for k, trace in enumerate(traces):
                at = f'{where}.traced_to[{k}]'
                if not isinstance(trace, dict):
                    out.append(f'`{at}`: must be a mapping')
                    continue
                out += [f'`{at}.{key}`: not a key of a trace' for key in sorted(set(trace) - _keys(trace_schema))]
                if trace.get('source') not in sources:
                    out.append(f'`{at}.source`: {trace.get("source")!r} is not one of {", ".join(sources)}')
                if not str(trace.get('ref') or '').strip():
                    out.append(f'`{at}.ref`: required')
                if trace.get('source') in ('prd', 'corpus') and not str(trace.get('quote') or '').strip():
                    out.append(f'`{at}.quote`: REQUIRED for source {trace.get("source")} — quote it verbatim')
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('usage: verify_eval_intent.py <eval_intent.yaml>', file=sys.stderr)
        return 3
    if yaml is None:
        print(f'verify_eval_intent: this interpreter ({Path(sys.executable).name}) has no PyYAML — install pyyaml into it, or source '
              'resolve-python.sh and run under "$PLURAI_PY"; the document was NOT checked', file=sys.stderr)
        return 3
    try:
        schema = json.loads(SCHEMA.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'verify_eval_intent: the schema {SCHEMA.name} could not be read ({type(exc).__name__}); the document was NOT checked', file=sys.stderr)
        return 3
    path = Path(args[0])
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError as exc:
        print(f'{path.name}: cannot be read ({type(exc).__name__}) — write it first', file=sys.stderr)
        return 2
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f' at line {mark.line + 1}' if mark is not None else ''
        print(f'{path.name}: not valid YAML{where} — quote the WHOLE value or none of it', file=sys.stderr)
        return 2
    found = problems(doc, schema)
    if found:
        print('\n'.join(found))
        return 2
    axes = doc['axes']
    print(f'{path.name} OK — {len(axes)} axes, {sum(len(a["failure_modes"]) for a in axes)} failure modes')
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
