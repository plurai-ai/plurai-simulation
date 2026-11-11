#!/usr/bin/env python3
"""List the typed faults of the unscored sanity pass (suite Step 2a): one line each, read off files that already exist.

    "$PLURAI_PY" scripts/sanity_signals.py "$ART/sanity/1" --workdir "$WORKDIR"

Run after `build_run_json.py` built the dir's `run.json`. Reads, never judges:

* each scenario's `error.json` — `harness_fault` (a typed harness `kind`), else `init_error` (raised at Init or before any
  turn), else `run_error`;
* `run_summary.json` `ended_early_scenarios` — `undelivered_seed` (the seed did not read back at Init);
* each scenario's `tool_events.json` `events` — `tool_error` (a call whose dispatch failed, as `run.json` counts its
  `tool_errors`: tool, count, first turn). It may be the agent's own input (a bad id), so it is a typed line for the
  reader, never a repair on its own;
* with `--workdir`, `unseen_write`: a call with status `ok` to a tool whose `plurai.config.yaml` descriptor declares a
  `post_conditions` row on a type of the harness-owned store (`world_state.owner: agent`, `owned_types`), whose row
  the scenario's `final_world_state.json` does not hold — the world the checks read never saw what the agent saved.
  The row id is the call's argument `with_fields` maps the type's `id_field` to, else its `id_field`, `id` or
  `<type>_id` argument; with none, a creation (a `+` `count_change`) fires only when the world holds no row of that type
  at all, and an update proves nothing (it touched one row the call does not name). An id a later
  `ok` call to a declared remover (a negative `count_change`) touched is not owed;
* `run.json` — `run_integrity.measurement_unclean_because` (e.g. `declared_lane_unread`), `tool_trail_results_missing`,
  a row that ended `adapter_degraded`, and `aborted`. Two channels the pass's own shape raises are no line when the run's
  typed record says that shape is all that raised them (:func:`_own_shape_channels`): `invalid_trials` on an unscored
  run whose every invalid trial is a dialog that landed `not_graded` (no eval set), and `user_sim_integrity` whose every
  breached row ended on the turn cap with only beats the cap left owed.

Writes the lines to `<run_dir>/signals.txt` and prints them. Exit 0 always: it warns, never blocks — a missing or
half-written dir reads as `no run`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-run' / 'scripts'))

import build_run_json  # noqa: E402 - the run.json builder: its one dispatch-error predicate and its run vocabulary
import verdicts_file  # noqa: E402
import writer_readers  # noqa: E402

OUTPUT = 'signals.txt'
#: `run_summary.json` `ended_early_scenarios` reason for a seed the Init did not read back (SDK `plurai.world.readback`).
SEED_NOT_LANDED = 'seed_not_landed'
DEGRADED = 'adapter_degraded'
#: The pass's own shape, as the run types it: no eval set (a landed dialog's status cause), and `--max-turns` (the
#: `ended_early` reason, and the user simulator's beats the turn budget left owed).
TURN_CAP = 'turn_cap_exhausted'
USER_SIM = 'user_sim_integrity'
_CAP_ANOMALIES = frozenset({'beats_unplayed_at_budget', 'premise_beats_undelivered'})
_BUDGET = 'budget'
_BLOCKED_BY = 'blocked_by:'
_CAP = 200


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _short(text: object) -> str:
    line = ' '.join(str(text or '').split())
    return line if len(line) <= _CAP else line[: _CAP - 1] + '…'


def _error_line(name: str, record: dict) -> str:
    kind = str(record.get('kind') or '')
    what = _short(f"{record.get('exception') or 'error'}: {record.get('error') or ''}")
    if kind.startswith(build_run_json.HARNESS_KIND_PREFIX):
        return f'{name} · harness_fault · {kind} · {what}'
    if record.get('raised_at') == 'init' or record.get('envelope_type') == 'init' or record.get('turns_reached') == 0:
        return f'{name} · init_error · {what}'
    return f'{name} · run_error · {what}'


def _events(d: Path) -> list[dict]:
    """The scenario's `tool_events.json` events (`[]` when unreadable)."""
    doc = _read_json(d / 'tool_events.json')
    events = doc.get('events') if isinstance(doc, dict) else doc
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def _tool_name(event: dict) -> str:
    return str(event.get('tool') or event.get('tool_name') or '')


def _tool_lines(name: str, events: list[dict]) -> list[str]:
    errored: dict[str, list[object]] = {}
    for e in events:
        if build_run_json._dispatch_error(e):  # noqa: SLF001 - the one predicate run.json's `tool_errors` counts with
            errored.setdefault(_tool_name(e) or '?', []).append(e.get('turn'))
    return [f'{name} · tool_error · {tool} ×{len(turns)}' + (f' · first at turn {turns[0]}' if turns[0] is not None else '')
            + ' · tool error (may be the agent\'s own input)' for tool, turns in sorted(errored.items())]


def _load_yaml(path: Path) -> dict | None:
    try:
        import yaml  # noqa: PLC0415 - deferred: the other lines need no YAML reader
    except ImportError:
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


class _Saves:
    """The bench's typed write declarations into the harness-owned store: per tool, its `(type, with_fields, removes,
    creates)` post-conditions, and per type its `id_field`."""

    def __init__(self, workdir: Path) -> None:
        config = _load_yaml(workdir / 'plurai.config.yaml') or {}
        store = writer_readers.local_store_types(config)
        world = config.get('world_state') if isinstance(config.get('world_state'), dict) else {}
        declared = world.get('entity_types') if isinstance(world.get('entity_types'), dict) else {}
        schema = (_load_yaml(workdir / 'scenario_state_schema.yaml') or {}).get('entities')
        self.id_field: dict[str, str] = {}
        for source in (schema if isinstance(schema, dict) else {}, declared):
            for name, spec in source.items():
                if isinstance(spec, dict) and isinstance(spec.get('id_field'), str) and spec['id_field'].strip():
                    self.id_field[str(name)] = spec['id_field'].strip()
        self.by_tool: dict[str, list[tuple[str, dict, bool, bool]]] = {}
        for tool, entry in writer_readers._tools(config).items() if store != frozenset() else ():  # noqa: SLF001
            post = writer_readers._descriptor(entry).get('post_conditions')  # noqa: SLF001
            for cond in post if isinstance(post, list) else []:
                kind = cond.get('entity_type') if isinstance(cond, dict) else None
                if isinstance(kind, str) and kind.strip() and (store is None or kind.strip() in store):
                    fields = cond.get('with_fields') if isinstance(cond.get('with_fields'), dict) else {}
                    change = str(cond.get('count_change') or '').strip()
                    moves = change[:1] in ('+', '-') and change[1:].strip() not in ('', '0')  # `+0` / `-0` moves no row
                    self.by_tool.setdefault(str(tool), []).append((kind.strip(), fields, moves and change[0] == '-', moves and change[0] == '+'))

    def row_id(self, kind: str, fields: dict, args: dict) -> str | None:
        """The id the call names for its row: the argument `with_fields` maps the id field to, else the id field, `id`
        or `<type>_id` argument. A `with_fields` value that names no argument of this call is a literal, never a key."""
        id_field = self.id_field.get(kind)
        named = fields.get(id_field) if id_field else None
        for key in ([named] if isinstance(named, str) and named in args else []) + [id_field, 'id', f'{kind}_id']:
            value = args.get(key) if key else None
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                return str(value).strip()
        return None

    def held(self, kind: str, row_id: str | None, entities: dict) -> bool:
        """A row of `kind` the call names: by key, `id` or id field — or, for a file-like name (`report.md`), by the
        call's path ending in it (`/out/report.md`). A short id never matches a path's tail (`1` vs `/x/1`)."""
        id_field = self.id_field.get(kind)
        for key, row in entities.items():
            if isinstance(row, dict) and row.get('_entity_type') == kind:
                names = {str(key), str(row.get('id')), str(row.get(id_field)) if id_field else ''} - {'', 'None'}
                if row_id is None or any(row_id == n or ('.' in n and row_id.endswith('/' + n)) for n in names):
                    return True
        return False


def _unseen_write_lines(name: str, d: Path, events: list[dict], saves: _Saves) -> list[str]:
    world = _read_json(d / 'final_world_state.json')
    entities = world.get('entities') if isinstance(world, dict) else None
    if not isinstance(entities, dict):
        return []
    owed: dict[tuple[str, str | None], tuple[str, object]] = {}
    tools_of: dict[str, list[str]] = {}
    for e in events:
        if str(e.get('status') or '').lower() != 'ok':
            continue
        tool = _tool_name(e)
        args: Any = e.get('args') if isinstance(e.get('args'), dict) else {}
        for kind, fields, removes, creates in saves.by_tool.get(tool, ()):
            row_id = saves.row_id(kind, fields, args)
            if removes:
                if row_id is not None:
                    owed.pop((kind, row_id), None)
                continue
            if row_id is None and not creates:
                continue
            owed.setdefault((kind, row_id), (tool, e.get('turn')))
            if tool not in tools_of.setdefault(kind, []):
                tools_of[kind].append(tool)
    unseen: dict[str, list[tuple[str | None, object]]] = {}
    for (kind, row_id), (_tool, turn) in owed.items():
        if not saves.held(kind, row_id, entities):
            unseen.setdefault(kind, []).append((row_id, turn))
    lines = []
    for kind, rows in sorted(unseen.items()):
        named = [(row_id, turn) for row_id, turn in rows if row_id is not None]
        first_id, first_turn = (named or rows)[0]
        what = f"{kind} ×{len(rows)}" + (f" '{_short(first_id)}'" if first_id is not None else '')
        lines.append(f"{name} · unseen_write · {what} · by {', '.join(tools_of[kind])} · first at turn {first_turn}"
                     ' · a call returned ok and the world the checks read holds no such row')
    return lines


def _landed_unscored(d: Path, ended: object) -> bool:
    """A dialog that landed and was only left ungraded: verdicts `harness_error` / `not_graded`, no error record, and
    no `ended_early` reason but the turn cap."""
    try:
        status = verdicts_file.status(verdicts_file.read(d))
    except verdicts_file.VerdictsError:
        return False
    cause = status['cause'] or {}
    return (status['value'] == build_run_json.HARNESS_ERROR and cause.get('kind') == build_run_json.NOT_GRADED and not status['cut_by']
            and not (d / 'error.json').exists() and ended in (None, TURN_CAP))


def _owed_by_the_cap(row: dict) -> bool:
    """A breached user-sim row whose every anomaly is a beat the turn budget left owed: `beats_unplayed_at_budget`, and
    `premise_beats_undelivered` whose every reason is `budget` or `blocked_by:` a beat that resolves to it."""
    anomalies = row.get('anomalies')
    if row.get('run_valid') is False or not isinstance(anomalies, dict) or not anomalies:
        return False
    reasons: dict[str, str] = {}
    undelivered = row.get('undelivered_beats') if isinstance(row.get('undelivered_beats'), dict) else {}
    for beat, entry in undelivered.items():
        if isinstance(entry, dict) and isinstance(entry.get('reason'), str):
            reasons[str(beat)] = entry['reason']
    owed: list[str] = []
    for block in anomalies.values():
        if not isinstance(block, dict) or not block or not set(block) <= _CAP_ANOMALIES:
            return False
        premise = block.get('premise_beats_undelivered', {})
        if not isinstance(premise, dict):
            return False
        for beat, reason in premise.items():
            reasons.setdefault(str(beat), str(reason))
            owed.append(str(reason))

    def budget(reason: str, seen: frozenset[str]) -> bool:
        if reason == _BUDGET:
            return True
        beat = reason[len(_BLOCKED_BY):] if reason.startswith(_BLOCKED_BY) else None
        return beat is not None and beat not in seen and beat in reasons and budget(reasons[beat], seen | {beat})

    return all(budget(reason, frozenset()) for reason in owed)


def _own_shape_channels(run_dir: Path, summary: object) -> set[str]:
    """The `measurement_unclean` channels the sanity pass's own shape (unscored, turn-capped) raised and nothing else
    did, read off typed facts the run recorded — never the channel's name alone. Any other cause keeps the line."""
    if not isinstance(summary, dict):
        return set()
    ended = summary.get('ended_early_scenarios') if isinstance(summary.get('ended_early_scenarios'), dict) else {}
    own: set[str] = set()
    totals = summary.get('totals') if isinstance(summary.get('totals'), dict) else {}
    rows = totals.get('scenarios') if isinstance(totals.get('scenarios'), dict) else {}
    invalid = [sid for sid, row in rows.items() for trial in (row.get('trials') if isinstance(row, dict) else None) or []
               if isinstance(trial, dict) and trial.get('validity') != 'valid']
    if (summary.get('eval_set_attached') is False and invalid and len(invalid) == totals.get('invalid_trials')
            and all(_landed_unscored(run_dir / sid, ended.get(sid)) for sid in invalid)):
        own.add(build_run_json.INVALID_TRIALS_CHANNEL)
    user_sim = summary.get(USER_SIM) if isinstance(summary.get(USER_SIM), dict) else {}
    breached = {sid: row for sid, row in user_sim.items()
                if isinstance(row, dict) and row.get('clean') is not True and row.get('state') != 'unchecked'}
    if breached and all(ended.get(sid) == TURN_CAP and _owed_by_the_cap(row) for sid, row in breached.items()):
        own.add(USER_SIM)
    return own


def signals(run_dir: Path, workdir: Path | None = None) -> list[str] | None:
    """The fault lines, or None when the dir holds no run."""
    summary = _read_json(run_dir / 'run_summary.json')
    dirs = sorted(d for d in run_dir.iterdir() if d.is_dir() and not d.name.startswith(('.', 'trial_'))) if run_dir.is_dir() else []
    if not isinstance(summary, dict) and not dirs:
        return None
    lines: list[str] = []
    saves = _Saves(workdir) if workdir is not None else None
    for d in dirs:
        record = _read_json(d / 'error.json')
        if isinstance(record, dict):
            lines.append(_error_line(d.name, record))
        events = _events(d)
        lines.extend(_tool_lines(d.name, events))
        if saves is not None and saves.by_tool:
            lines.extend(_unseen_write_lines(d.name, d, events, saves))
    reasons = summary.get('ended_early_scenarios') if isinstance(summary, dict) else None
    for scenario, reason in sorted(reasons.items()) if isinstance(reasons, dict) else ():
        if reason == SEED_NOT_LANDED:
            lines.append(f'{scenario} · undelivered_seed · the seed did not read back as seeded at Init; not driven')
    built = _read_json(run_dir / 'run.json')
    built = built if isinstance(built, dict) else {}
    integrity = built.get('run_integrity') if isinstance(built.get('run_integrity'), dict) else {}
    channels = integrity.get('measurement_unclean_because')
    own = _own_shape_channels(run_dir, summary)
    for channel in dict.fromkeys(str(c) for c in channels) if isinstance(channels, list) else ():
        if channel not in own:
            lines.append(f'* · measurement_unclean · {channel}')
    if missing := integrity.get('tool_trail_results_missing'):
        lines.append(f'* · tool_trail_results_missing · {_short(json.dumps(missing))}')
    rows = built.get('scenarios')
    for row in rows if isinstance(rows, list) else []:
        early = row.get('ended_early') if isinstance(row, dict) else None
        if (early.get('reason') if isinstance(early, dict) else early) == DEGRADED:
            lines.append(f"{row.get('id')} · {DEGRADED} · the harness emptied every turn")
    if built.get('aborted'):
        lines.append(f"* · aborted · {_short(json.dumps(built['aborted']))}")
    return lines


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    workdir: Path | None = None
    if len(args) == 3 and args[1] == '--workdir':
        workdir = Path(args.pop(2))
        args.pop(1)
    if len(args) != 1:
        print('usage: sanity_signals.py <sanity run dir> [--workdir <workdir>]', file=sys.stderr)
        return 0
    run_dir = Path(args[0])
    try:
        lines = signals(run_dir, workdir)
        if lines is not None:
            (run_dir / OUTPUT).write_text(''.join(f'{line}\n' for line in lines), encoding='utf-8')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f'sanity: {run_dir} could not be read ({type(exc).__name__}) — go on to the scored qualification run')
        return 0
    if lines is None:
        print(f'sanity: no run in {run_dir} — go on to the scored qualification run')
        return 0
    print(f'sanity: {len(lines)} typed line(s)' + ('' if lines else ' — clean'))
    for line in lines:
        print(f'- {line}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
