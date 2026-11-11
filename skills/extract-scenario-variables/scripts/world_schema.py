#!/usr/bin/env python3
"""Write the declared world's entity blocks into `scenario_state_schema.yaml` on a bench env-gen skips.

Usage: python world_schema.py /path/to/<agent>/plurai_sim

Every world type `plurai.config.yaml` declares (`world_state.entity_types`) is either DELIVERED (an entity
block in the scenario schema, so every scenario carries its rows) or DECLINED (one `seedability /
not_seedable` fact in the PRD's `## Agent facts` whose `subject` is the type and whose `source` cites the
line that makes it unseedable). Tool `mode` never decides this.

When a mock exists, env-gen writes the blocks. When none does, this script does — by copying the declared
blocks VERBATIM (`entity_types`, plus `user_surfaces` when declared), never by judgement. It writes the
harness-owned types (route H: `owned_types`, or every type when `owner: agent` names none) minus the
declined ones, sets `consumes_world_state: true`, and keeps every other top-level block of an existing
schema byte-identical (`agent_context_fields` above all). `owner: external` types are never auto-seeded:
the run must not write that store, so each needs a decline (or a declared read-through on a mocked bench).
Re-running it is a no-op.

Exit 0 on success (including "nothing to write"), 2 on an unreadable config.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SCHEMA = 'scenario_state_schema.yaml'
#: The world owners whose store the harness holds (`scripts/bench_manifest.py` `_HARNESS_WORLD_OWNERS`).
HARNESS_OWNERS = ('agent', 'external')
#: The top-level keys this script owns in the schema; every other block is left byte-identical.
_WRITTEN_KEYS = ('entity_types', 'user_surfaces', 'consumes_world_state')


def _world(config: dict[str, Any]) -> tuple[str, dict[str, Any], list[str] | None]:
    world = config.get('world_state') if isinstance(config.get('world_state'), dict) else {}
    types = world.get('entity_types') if isinstance(world.get('entity_types'), dict) else {}
    owned = world.get('owned_types')
    return str(world.get('owner') or '').strip().lower(), types, [str(t) for t in owned] if isinstance(owned, list) else None


def env_gen_skipped(config: dict[str, Any]) -> bool:
    """Step 4b's test: no mock to build — no `tools:`, an empty one, or every tool `mode: real` reading for real.

    The repo's `bench_manifest.mock_needs` when importable (the `.run_mode` writers' test), else the plain mode test."""
    try:
        scripts = str(Path(__file__).resolve().parents[3] / 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from bench_manifest import mock_needs  # noqa: PLC0415 - repo script, optional when copied out of the plugin
    except ImportError:
        tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
        return all(isinstance(spec, dict) and spec.get('mode', 'mock') == 'real' for spec in tools.values())
    return not mock_needs(config)


def harness_types(config: dict[str, Any]) -> list[str]:
    """Route H, in declaration order: `owned_types`, else every type under a harness owner — the types the harness's
    `seed_state` lays over its own store (`lint_prd.check_state_seedability` reads the same split)."""
    owner, types, owned = _world(config)
    if owned is not None:
        return [t for t in types if t in owned]
    return list(types) if owner in HARNESS_OWNERS else []


def _facts(prd_text: str) -> list:
    """The PRD's parsed `facts:` list through `lint_prd`'s one parser (loaded by path), else []."""
    path = Path(__file__).resolve().parents[2] / 'simulation-setup' / 'scripts' / 'lint_prd.py'
    name = '_world_schema_lint_prd'
    try:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        entries, _findings = sys.modules[name]._facts_entries(prd_text)  # noqa: SLF001 - the one facts parser
    except (OSError, ImportError, AttributeError):
        return []
    return entries or []


def declined_types(prd_text: str) -> dict[str, str]:
    """`{type: source}` for each `seedability / not_seedable` fact whose subject is a bare type and which cites a source.
    Whether the cite is acceptable is `lint_prd`'s call (`check_state_seedability`); an uncited decline declines nothing."""
    out: dict[str, str] = {}
    for entry in _facts(prd_text):
        if isinstance(entry, dict) and entry.get('kind') == 'seedability' and entry.get('predicate') == 'not_seedable':
            subject, source = str(entry.get('subject') or '').strip(), str(entry.get('source') or '').strip()
            if subject and '.' not in subject and source:
                out[subject] = source
    return out


def expected_world_blocks(config: dict[str, Any], prd_text: str) -> tuple[list[str], list[str]]:
    """`(deliver, undecided)`: the route-H types this script writes blocks for, and the `owner: external` types that
    carry neither a block nor a decline (never auto-seeded: the run must not write that store)."""
    owner, _types, _owned = _world(config)
    declined = declined_types(prd_text)
    open_types = [t for t in harness_types(config) if t not in declined]
    return ([], open_types) if owner == 'external' else (open_types, [])


def _without_top_level(text: str, keys: tuple[str, ...]) -> str:
    """`text` minus each top-level `key:` block in `keys` (the key line and its indented / blank continuation)."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        top = re.match(r'([A-Za-z_][\w-]*)\s*:', line)
        if top and not line[:1].isspace():
            skipping = top.group(1) in keys
        if not skipping:
            out.append(line)
    return ''.join(out)


def render(existing: str, config: dict[str, Any], deliver: list[str]) -> str:
    """The schema text: `existing` with this script's keys replaced by the declared blocks, copied verbatim."""
    _owner, types, _owned = _world(config)
    world = config.get('world_state') or {}
    block: dict[str, Any] = {'consumes_world_state': True, 'entity_types': {t: types[t] for t in deliver}}
    if world.get('user_surfaces'):
        block['user_surfaces'] = world['user_surfaces']
    kept = _without_top_level(existing, _WRITTEN_KEYS).rstrip('\n')
    written = yaml.safe_dump(block, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f'{kept}\n{written}' if kept else written


def write(plurai_dir: Path) -> str:
    """Write the blocks when this bench's world needs them; return one line saying what was done."""
    config = yaml.safe_load((plurai_dir / 'plurai.config.yaml').read_text(encoding='utf-8')) or {}
    prd = plurai_dir / 'simulation_prd.md'
    prd_text = prd.read_text(encoding='utf-8') if prd.is_file() else ''
    if not env_gen_skipped(config):
        return 'a mock exists: env-gen writes the entity blocks; nothing written.'
    deliver, undecided = expected_world_blocks(config, prd_text)
    note = f' `owner: external` type(s) {", ".join(undecided)} need a cited `not_seedable` decline.' if undecided else ''
    if not deliver:
        return f'no harness-owned world type to deliver; nothing written.{note}'
    path = plurai_dir / SCHEMA
    existing = path.read_text(encoding='utf-8') if path.is_file() else ''
    text = render(existing, config, deliver)
    if text != existing:
        path.write_text(text, encoding='utf-8')
    return f'{SCHEMA}: {len(deliver)} entity block(s) ({", ".join(deliver)}), consumes_world_state: true.{note}'


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ('-h', '--help'):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        print(write(Path(args[0])))
    except (OSError, yaml.YAMLError) as exc:
        print(f'world_schema: cannot read the bench config: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
        from _stdio import utf8_stdio
    except ImportError:  # copied out of the plugin: no scripts/ beside it, so the streams stay as Python opened them
        def utf8_stdio() -> None:
            pass
    utf8_stdio()
    sys.exit(main())
