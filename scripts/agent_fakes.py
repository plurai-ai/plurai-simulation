"""A vendor double the agent's own repo ships, served per conversation as that vendor's simulation.

A vendor the scan would mock from a seeded row (a carrier's price, a validator's verdict) is better simulated by the
double the agent ships when that double COMPUTES the answer from the request and the agent's own data. The scan records
it with no question in `world_state.confirmation`:

    {answer: agent_fake, target: "fake: <entry .py in the agent's repo>", env: <the vendor's endpoint variable>,
     computes: "<file>:<line>[-<line>]", data_env: <the variable naming the folder it loads its inputs from>,
     inputs: {<file name>: <shipped path> | {world: <entity type>, shipped: <the shipped file it replaces>}},
     state_env: [<a variable naming where it keeps state>, ...]}

The harness serves it with `references/agent_fake_serve.py`'s `_serve_agent_fake` (verify_simulate holds it to that
reference), so the double runs in the conversation's own process, its inputs and state in fresh temp folders, the world
types it reads written from the scenario. This entry never confirms a store a tool names (`writer_readers` drops it from
every store-confirmation reader): it only routes one endpoint variable.

Mechanical, never trusted: :func:`shape_errors` reads the entry; a miss keeps the vendor mocked. Stdlib only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath
from typing import Any

ANSWER = 'agent_fake'
PREFIX = 'fake:'
#: The one serve helper (`references/agent_fake_serve.py`), copied verbatim into the harness.
SERVE = '_serve_agent_fake'
REFERENCE = Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup-python' / 'references' / 'agent_fake_serve.py'
#: The formats the helper writes a world type's rows in.
WORLD_FORMATS = ('.csv', '.csv.gz', '.json', '.json.gz', '.jsonl', '.jsonl.gz')
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_CITE = re.compile(r'^(?P<path>[^:\s]+):\d+(?:-\d+)?$')


def _rel(value: Any) -> PurePosixPath | None:
    """A path inside the agent's repo, outside the harness folder: relative, no `..`, not under `plurai_sim/`."""
    if not isinstance(value, str) or not value.strip() or '://' in value:
        return None
    path = PurePosixPath(value.strip())
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] == 'plurai_sim':
        return None
    return path


def entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The raw `agent_fake` entries of `world_state.confirmation`."""
    world_state = config.get('world_state') if isinstance(config, dict) else None
    block = world_state.get('confirmation') if isinstance(world_state, dict) else None
    return [e for e in block if isinstance(e, dict) and e.get('answer') == ANSWER] if isinstance(block, list) else []


def target_path(entry: dict[str, Any]) -> PurePosixPath | None:
    target = entry.get('target')
    if not isinstance(target, str) or not target.strip().startswith(PREFIX):
        return None
    path = _rel(target.strip()[len(PREFIX):])
    return path if path is not None and path.suffix == '.py' and len(target.split()) == 2 else None


def shape_errors(index: int, entry: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Why this `agent_fake` entry routes nothing (empty when its shape holds)."""
    path = target_path(entry)
    if path is None:
        return [f'entry {index}: an `agent_fake` target is `fake: <the double\'s entry .py>`, one path in the agent\'s repo outside '
                f'`plurai_sim/` ({entry.get("target")!r})']
    errors = []
    folder = path.parent
    for key in ('env', 'data_env'):
        if not isinstance(entry.get(key), str) or not _IDENT.match(entry[key]):
            errors.append(f'entry {index}: `{key}` names no variable ({entry.get(key)!r})')
    egress = config.get('egress') if isinstance(config.get('egress'), dict) else {}
    rows = egress.get('production_hosts') if isinstance(egress.get('production_hosts'), list) else []
    if isinstance(entry.get('env'), str) and entry['env'] not in {r.get('env') for r in rows if isinstance(r, dict)}:
        errors.append(f'entry {index}: `env` {entry["env"]!r} is no `egress.production_hosts` `env` row, so the harness is never '
                      'held to pin it: declare the vendor\'s endpoint variable there')
    state = entry.get('state_env', [])
    if not isinstance(state, list) or not all(isinstance(v, str) and _IDENT.match(v) for v in state):
        errors.append(f'entry {index}: `state_env` is not a list of variable names ({state!r})')
    cite = _CITE.match(str(entry.get('computes') or ''))
    if not cite or (cited := _rel(cite['path'])) is None or cited.parts[:len(folder.parts)] != folder.parts:
        errors.append(f'entry {index}: `computes` is not a `<file>:<line>` cite inside {folder} ({entry.get("computes")!r})')
    inputs = entry.get('inputs')
    types = (config.get('world_state') or {}).get('entity_types') if isinstance(config.get('world_state'), dict) else None
    shipped_outside = False
    for name, source in inputs.items() if isinstance(inputs, dict) and inputs else [(None, None)]:
        if name is None:
            errors.append(f'entry {index}: `inputs` lists no file the double loads')
            break
        world = source.get('world') if isinstance(source, dict) else None
        shipped = _rel(source.get('shipped') if isinstance(source, dict) else source)
        if not str(name).strip() or '/' in str(name) or str(name).startswith('.') or shipped is None:
            errors.append(f'entry {index}: input {name!r} is not `<file name>: <shipped path in the repo>`')
        elif isinstance(source, dict) and (not isinstance(types, dict) or world not in types or not str(name).endswith(WORLD_FORMATS)):
            errors.append(f'entry {index}: input {name!r} writes `{world}`, which is not a `world_state.entity_types` type in '
                          f'{"/".join(WORLD_FORMATS)}')
        elif isinstance(source, str) and shipped.parts[:len(folder.parts)] != folder.parts:
            shipped_outside = True
    if not errors and not shipped_outside:
        errors.append(f'entry {index}: the double loads no data file the agent ships outside {folder}: it computes nothing '
                      'from the agent\'s own data, so a per-scenario seeded mock simulates that vendor better')
    return errors


def fakes(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`{env: entry}` for every `agent_fake` entry whose shape holds."""
    return {e['env']: e for i, e in enumerate(entries(config)) if not shape_errors(i, e, config)}


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    return next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name), None)


def helper_is_reference(tree: ast.AST) -> bool:
    """The harness's `_serve_agent_fake` is the plugin's reference, AST for AST."""
    mine = _function(tree, SERVE)
    ref = _function(ast.parse(REFERENCE.read_text(encoding='utf-8')), SERVE)
    return mine is not None and ref is not None and ast.dump(mine) == ast.dump(ref)


def serves(value: ast.AST, entry: dict[str, Any]) -> bool:
    """`value` is `_serve_agent_fake("<this entry's target>", initial_world_state)`."""
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == SERVE and len(value.args) == 2
            and isinstance(value.args[0], ast.Constant) and value.args[0].value == entry['target']
            and isinstance(value.args[1], ast.Name) and value.args[1].id == 'initial_world_state')


def in_seed_state(tree: ast.AST, line: int) -> bool:
    """`line` sits in the harness's `seed_state`, where this scenario's world is known."""
    seed = _function(tree, 'seed_state')
    return seed is not None and seed.lineno <= line <= (seed.end_lineno or seed.lineno)
