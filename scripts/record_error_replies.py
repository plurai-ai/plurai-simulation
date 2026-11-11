#!/usr/bin/env python3
"""Write `agent.error_replies` into `plurai.config.yaml`, read off the agent's own source.

The error replies are the fixed reply text the agent's error paths hand the user (`agent_inventory.py`
`error_replies[]`, each `{text, match: exact|prefix, cite}`). The backend's `raw_error_reply` check fails a
reply that IS one of them — exactly, or starting with a template's fixed head — so this agent's own error
text is caught without any phrase guessing. An empty list declares none, and the check is unchanged.

Comments survive: the entry is spliced as TEXT under `agent:` with a marker comment above it, replacing the
previous managed entry or any hand-written `error_replies:` entry.

Usage:
    record_error_replies.py --workdir <plurai_sim>

Exit codes: 0 written (or already current) · 2 the source is unreadable, or the config has no block-style
`agent:` mapping · 4 the config could not be written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_inventory  # noqa: E402 - the ONE reader of the agent's source
from record_native_tools import splice  # noqa: E402 - the one `agent:` splicer

MARKER = '# plurai-simulation error replies (managed — rewritten by /simulation-scan-agent)'


def entry_lines(replies: list[dict[str, str]]) -> list[str]:
    """The `error_replies:` entry: a block list of one-line flow mappings, so a diff shows each reply."""
    return ['error_replies: []'] if not replies else ['error_replies:', *(f'- {json.dumps(r, ensure_ascii=False)}' for r in replies)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write `agent.error_replies` into `plurai.config.yaml`, read off the agent's own source.")
    ap.add_argument('--workdir', required=True, type=Path, help='the plurai_sim workdir holding plurai.config.yaml')
    args = ap.parse_args(argv)
    config = args.workdir / 'plurai.config.yaml'
    inventory = agent_inventory.read_artifact(args.workdir) or {}
    if 'error_replies' not in inventory:
        inventory = agent_inventory.extract(args.workdir)
    if 'unavailable' in inventory:
        print(f'record_error_replies: the source is not readable ({inventory["unavailable"]}); {config} is UNCHANGED.', file=sys.stderr)
        return 2
    replies = [{k: str(r[k]) for k in ('text', 'match', 'cite')} for r in inventory.get('error_replies') or []]
    try:
        original = config.read_text(encoding='utf-8')
        updated = splice(original, 'error_replies', entry_lines(replies), MARKER)
        if ((yaml.safe_load(updated) or {}).get('agent') or {}).get('error_replies') != replies:
            raise ValueError('the spliced config does not read back the extracted error_replies')
    except (OSError, ValueError, yaml.YAMLError, AttributeError) as exc:
        print(f'record_error_replies: {exc}; {config} is UNCHANGED.', file=sys.stderr)
        return 2
    if updated != original:
        try:
            config.write_text(updated, encoding='utf-8')
        except OSError as exc:
            print(f'record_error_replies: cannot write {config} ({exc}).', file=sys.stderr)
            return 4
    dropped = int(inventory.get('error_replies_dropped') or 0)
    print(f'agent.error_replies: {len(replies)} fixed error repl{"y" if len(replies) == 1 else "ies"} recorded'
          + (f' ({dropped} lower-confidence one(s) over the cap left out)' if dropped else ''))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
