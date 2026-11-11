#!/usr/bin/env python3
"""Write `agent.native_tools` into `plurai.config.yaml`, computed from the approved map.

The native tools are the live tools on `agent_topology.json` that no plurai lane wraps (no
`tools:` entry in the config) — a Claude Agent SDK agent's built-ins. Any other runtime's config
is left untouched: its lanes observe its tools. The SDK reads the key to report the tool channel unread when
such a harness returns no `tool_trail`. The map's consent set (`scripts/host_effects.py`) is
printed beside them; both are computed from the map's typed facts, never typed here.

Comments survive: the one `native_tools:` line is spliced as TEXT under `agent:` with a marker
comment above it, replacing the previous managed line or any hand-written `native_tools:` entry.

Usage:
    record_native_tools.py --config <plurai.config.yaml> --topology <agent_topology.json>

Exit codes: 0 written (or already current) · 2 an input is unreadable, or the config has no
block-style `agent:` mapping · 4 the config could not be written.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_effects import consent_required, recorded_native_tools, runtime_of  # noqa: E402 - the plugin's one host-effects table

MARKER = '# plurai-simulation native tools (managed — rewritten by /simulation-scan-agent)'
_AGENT = re.compile(r'^agent:[ \t]*(#.*)?$')


def splice(text: str, key: str, entry: list[str], marker: str) -> str:
    """`text` with one managed `<key>:` entry (``entry``: its lines, unindented, the first `<key>: …`) as the first
    entry of the `agent:` block, under ``marker``."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _AGENT.match(line.rstrip('\n'))), None)
    if start is None:
        raise ValueError(f'no block-style top-level `agent:` mapping to record {key} under')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() and not lines[i][0].isspace() and not lines[i].startswith('#')), len(lines))
    body = lines[start + 1 : end]
    indent = next((line[: len(line) - len(line.lstrip())] for line in body if line.strip() and not line.lstrip().startswith('#')), '  ')
    kept, skipping = [], False
    for line in body:
        stripped = line.strip()
        if stripped == marker:
            continue
        if line.startswith(indent) and not line[len(indent) :][:1].isspace() and stripped.startswith(f'{key}:'):
            skipping = True  # a prior entry, flow or block style
            continue
        if skipping and (stripped.startswith('- ') or (line[: len(indent) + 1].isspace() and stripped and not stripped.startswith('#'))):
            continue
        skipping = False
        kept.append(line)
    managed = [f'{indent}{line}\n' for line in (marker, *entry)]
    return ''.join(lines[: start + 1] + managed + kept + lines[end:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--config', required=True, type=Path)
    ap.add_argument('--topology', required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        topology = json.loads(args.topology.read_text(encoding='utf-8'))
        original = args.config.read_text(encoding='utf-8')
        config = yaml.safe_load(original) or {}
        names = recorded_native_tools(topology, config)
        if names is None:
            print(f'agent.native_tools: not recorded — agent.framework is not a runtime no plurai lane sees; {args.config} is unchanged')
            return 0
        needs_consent = consent_required(topology, runtime=runtime_of(topology, config))
        updated = splice(original, 'native_tools', [f'native_tools: {json.dumps(names)}'], MARKER)
        if (yaml.safe_load(updated) or {}).get('agent', {}).get('native_tools') != names:
            raise ValueError('the spliced config does not read back the computed native_tools')
    except (OSError, ValueError, yaml.YAMLError, AttributeError) as exc:
        print(f'record_native_tools: {exc}; {args.config} is UNCHANGED.', file=sys.stderr)
        return 2
    if updated != original:
        try:
            args.config.write_text(updated, encoding='utf-8')
        except OSError as exc:
            print(f'record_native_tools: cannot write {args.config} ({exc}).', file=sys.stderr)
            return 4
    print(f'agent.native_tools: {names}')
    print(f'host effects (need consent): {[n for n in needs_consent if n in names]}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
