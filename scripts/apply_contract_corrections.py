#!/usr/bin/env python3
"""Write an env edit's `contract_corrections` into the customer's `plurai.config.yaml`.

When an /edit-env repair makes the mock follow the real system and that contradicts a tool's INFERRED
contract (`returns.type_source` inferred or declared_unknown), the session corrects the contract and
`end_edit_session` returns `contract_corrections: [{tool, field, before, after}]`. The backend staged the
corrected config on the env only; this writes each `after` into `tools.<tool>.descriptor.<field>` here, so
the mock and its contract agree everywhere.

Comments survive: each field is spliced as text with the config writer `derive_tool_facts.py --write`
uses (`_set_keys`), leaving every other byte. Nothing is written when any correction is refused:
- a DECLARED contract (any other `returns.type_source`, or none): the backend never corrects one;
- a tool or field the config does not carry, or a field that is not `post_conditions` / `business_rules`;
- a field that no longer reads `before` (changed since the session read it), unless it already reads `after`.

Usage:
    apply_contract_corrections.py --config <plurai.config.yaml> --result <end_edit_session result.json>

Exit codes: 0 written, already current, or nothing to write · 2 an input is unreadable or malformed ·
3 a correction is refused (above) · 4 the config could not be written.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup-python' / 'scripts'))
from derive_tool_facts import _pair, _set_keys  # type: ignore[import-not-found]  # noqa: E402 - the plugin's in-place config writer

#: Mirrors the backend's `INFERRED_CONTRACT_TIERS` / `CORRECTABLE_CONTRACT_FIELDS`.
INFERRED_TIERS = frozenset({'plugin', 'inferred', 'declared_unknown'})
FIELDS = ('post_conditions', 'business_rules')
#: The outcomes whose commit staged the corrected config (`closed_failed`: committed, the redeploy failed).
COMMITTED = frozenset({'committed', 'closed_failed'})


class Refused(ValueError):
    """A correction this writer will not apply; nothing is written."""


def _entries(value: Any) -> list[Any]:
    """A descriptor field as the backend reads it: None is none, a bare entry is one."""
    return [] if value is None else list(value) if isinstance(value, list) else [value]


def plan(config: Any, corrections: list[Any]) -> dict[str, dict[str, list[Any]]]:
    """`{tool: {field: after}}` to write, without the ones already current. Raises `Refused` on any refusal."""
    tools = config.get('tools') if isinstance(config, dict) else None
    tools = tools if isinstance(tools, dict) else {}
    updates: dict[str, dict[str, list[Any]]] = {}
    for c in corrections:
        if not isinstance(c, dict) or not isinstance(c.get('after'), list):
            raise ValueError(f'a correction must be a mapping with a list `after`, got {c!r}')
        tool, field = str(c.get('tool') or ''), str(c.get('field') or '')
        if field not in FIELDS:
            raise Refused(f'`{tool}.{field}`: only {", ".join(FIELDS)} are corrected from an edit')
        entry = tools.get(tool)
        descriptor = entry.get('descriptor') if isinstance(entry, dict) else None
        if not isinstance(descriptor, dict):
            raise Refused(f'`{tool}` is not a tool with a descriptor in the config')
        if any(other is not entry and isinstance(other, dict) and other.get('descriptor') is descriptor for other in tools.values()):
            raise ValueError(f'tool {tool!r} shares a descriptor node with another tool through a YAML alias; expand the alias first')
        returns = descriptor.get('returns')
        tier = returns.get('type_source') if isinstance(returns, dict) else None
        if tier not in INFERRED_TIERS:
            raise Refused(f'`{tool}`’s contract is DECLARED (returns.type_source: {tier or "none"}); an edit never corrects it')
        current = _entries(descriptor.get(field))
        if current == c['after']:
            continue
        if current != _entries(c.get('before')):
            raise Refused(f'`{tool}.{field}` changed since the session read it; re-open /edit-env with the current config')
        updates.setdefault(tool, {})[field] = c['after']
    return updates


def splice(text: str, updates: dict[str, dict[str, list[Any]]]) -> str:
    """`text` with each tool's descriptor carrying its corrected fields, every other byte kept."""
    config = yaml.safe_load(text)
    tools = _pair(yaml.compose(text), 'tools')
    edits: list[tuple[int, int, str]] = []
    for tool, fields in updates.items():
        descriptor = _pair(_pair(tools[1], tool)[1], 'descriptor')[1]  # type: ignore[index]  # plan() proved both exist
        edits += _set_keys(text, descriptor, config['tools'][tool]['descriptor'], fields, ())
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + new + text[end:]
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--result', required=True, type=Path, help='the end_edit_session result, as JSON')
    args = ap.parse_args(argv)
    try:
        result = json.loads(args.result.read_text(encoding='utf-8'))
        original = args.config.read_text(encoding='utf-8')
        config = yaml.safe_load(original) or {}
        if not isinstance(result, dict):
            raise ValueError('the result is not a JSON object')
        corrections = result.get('contract_corrections') or []
        if corrections and result.get('outcome') not in COMMITTED:
            raise ValueError(f'outcome {result.get("outcome")!r} committed nothing, so its contract_corrections are not applied')
        updates = plan(config, corrections)
        updated = splice(original, updates) if updates else original
        expected = copy.deepcopy(config)
        for tool, fields in updates.items():
            expected['tools'][tool]['descriptor'].update(fields)
        if yaml.safe_load(updated) != expected:
            raise ValueError('the spliced config does not read back the corrected contracts, and only them')
    except Refused as exc:
        print(f'apply_contract_corrections: REFUSED — {exc}; {args.config} is UNCHANGED.', file=sys.stderr)
        return 3
    except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
        print(f'apply_contract_corrections: {exc}; {args.config} is UNCHANGED.', file=sys.stderr)
        return 2
    if updated != original:
        temp = args.config.with_suffix(args.config.suffix + '.contract-tmp')
        try:
            temp.write_text(updated, encoding='utf-8')
            temp.replace(args.config)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            print(f'apply_contract_corrections: cannot write {args.config} ({exc}).', file=sys.stderr)
            return 4
    written = [f'{t}.{f}' for t, fields in updates.items() for f in fields]
    print(f'contract_corrections: wrote {len(written)}' + (f' ({", ".join(written)})' if written else '') + f' of {len(corrections)}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
