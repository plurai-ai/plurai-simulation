#!/usr/bin/env python3
"""`artifacts/runs/index.json` — every run in the workdir, its stage, and which one is THE scored run.

    scripts/runs_index.py write <workdir> [--add NAME=STAGE]...
    scripts/runs_index.py eta <workdir> --scenarios N [--trials K]

`<workdir>` is the agent's `plurai_sim/` dir ($WORKDIR). The index is rebuilt from disk on every call and
written atomically (keys sorted), so a second call over the same disk writes the same bytes:

    {"schema": "plurai-runs-index/1",
     "scored_run": <name of the LAST `final` run that has its red-cell audit, else null>,
     "runs": [{"name", "stage", "stage_source", "written_at", "eval_set_id", "scenario_set_id", "trials"}, ...]}

`runs[]` covers every top-level `artifacts/runs/<name>/run.json` — never a trial subdir — ordered by
`(written_at, numeric-aware name)`. Each field has ONE source:

    stage            the first of: this call's `--add NAME=STAGE`; a stage an EARLIER `--add` recorded
                     (`stage_source: add` persists); the run's `run.json` `stage`; the stage this index
                     already recorded; else null. Never read off a folder name. An explicit `--add` wins over
                     `run.json` because the fix plan's verify/repeat runs are built by the run step, which
                     stamps `qualification`/`final`: the caller that knows a run is a verify or repeat says so.
    stage_source     `add` | `run_json` | `recorded` | null — which of the above the stage came from.
    written_at       `run_summary.json` `written_at` (normalised to UTC ISO-8601 when it parses), else the
                     run.json mtime as ISO-8601 UTC.
    eval_set_id      `run.json` `eval_set_id`, else null.
    scenario_set_id  the value this index already holds for the run, else `$WORKDIR/.scenario_set_id` at the
                     moment the run is FIRST indexed (the run step indexes right after it builds run.json, so
                     that is the set that ran); never rewritten later. run.json carries no scenario set id.
    trials           `run.json` `trials.n`, else null.

A `final` run with no `red_cell_audit.json` (`red_cell_audit.path`) is never the scored run: its reds are unaudited,
so its verdict is not one a customer reads. The previous audited final stays scored, or none is. The audit lands after
the run step's `write`, so `red_cell_audit.save` rebuilds an existing index itself.

Stages are `build_run_json.RUN_STAGES`: qualification, final, verify, repeat. An `--add` naming a run with
no `run.json` is said on stderr and ignored (a fix plan may have no repeat run); a malformed `--add` exits 2.

`eta` prints the minutes a run of N scenarios (K trials) should take, measured off the LATEST `qualification`
run whose `run_summary.json` carries `scenario_timings`: its wall clock (last `ended_at` − first `started_at`)
over its scenario count, times N — times K only when that run's scenarios never overlapped, or when a row waited for
a leased resource (`lease_wait_seconds`: the trials then queue on the same copies; a one-scenario run counts as
serial, so the figure errs long). The concurrency the run actually got is in that clock; none is taken from the caller. It prints
nothing when no qualification run has timings, and writes nothing.

Exit codes: 0 written (`eta`: printed, or nothing to measure) · 2 bad usage (argparse, or a malformed `--add`) · 3 the workdir has no `artifacts/runs/`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'plurai-runs-index/1'
INDEX = 'index.json'
#: The stages a run may carry — `build_run_json.RUN_STAGES`, the same four.
RUN_STAGES = ('qualification', 'final', 'verify', 'repeat')
#: Where a run's stage came from, in precedence order (`recorded` is any earlier non-`add` value).
STAGE_SOURCES = ('add', 'run_json', 'recorded')
ENTRY_KEYS = ('name', 'stage', 'stage_source', 'written_at', 'eval_set_id', 'scenario_set_id', 'trials')
EXIT_USAGE, EXIT_NO_RUNS = 2, 3


def _read_json(path: Path) -> object:
    """The parsed file, or None when it is absent or does not parse."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _utc(value: object) -> str | None:
    """`value` as ISO-8601 UTC when it parses as an aware timestamp; a naive or unparseable string verbatim."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return value
    return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo is not None else value


def _name_key(name: str) -> list[tuple[int, int, str]]:
    """Numeric-aware: `run-2` before `run-10`."""
    return [(0, int(part), '') if part.isdigit() else (1, 0, part) for part in re.split(r'(\d+)', name) if part]


def _scenario_set_id(workdir: Path) -> str | None:
    try:
        value = (workdir / '.scenario_set_id').read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def _previous(index_path: Path) -> dict[str, dict]:
    """`{name: entry}` from the index already on disk; empty (said on stderr) when it is not ours or unreadable."""
    if not index_path.exists():
        return {}
    doc = _read_json(index_path)
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or not isinstance(doc.get('runs'), list):
        print(f'note: {index_path.name} is not a {SCHEMA} index — rebuilt from disk; recorded stages and scenario sets are not carried over.',
              file=sys.stderr)
        return {}
    return {str(e['name']): e for e in doc['runs'] if isinstance(e, dict) and isinstance(e.get('name'), str)}


def _stage(name: str, run: dict, prior: dict, added: dict[str, str]) -> tuple[str | None, str | None]:
    if name in added:
        return added[name], 'add'
    if prior.get('stage_source') == 'add' and prior.get('stage') in RUN_STAGES:
        return prior['stage'], 'add'
    if run.get('stage') in RUN_STAGES:
        return run['stage'], 'run_json'
    if prior.get('stage') in RUN_STAGES:
        return prior['stage'], 'recorded'
    return None, None


def _audited(run_dir: Path) -> bool:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import red_cell_audit  # noqa: PLC0415 - sibling script: the one audit file name

    return red_cell_audit.path(run_dir).is_file()


def build_index(workdir: Path, added: dict[str, str]) -> dict:
    """The index document for `workdir` — pure over the disk (and the index already on it)."""
    runs_dir = workdir / 'artifacts' / 'runs'
    prior = _previous(runs_dir / INDEX)
    current_set = _scenario_set_id(workdir)
    entries = []
    for run_json in sorted(runs_dir.glob('*/run.json')):
        run_dir = run_json.parent
        name = run_dir.name
        run = _read_json(run_json)
        if not isinstance(run, dict):
            print(f'note: {name}/run.json does not parse to an object — indexed with its stage, eval set and trials unknown.', file=sys.stderr)
            run = {}
        summary = _read_json(run_dir / 'run_summary.json')
        if isinstance(summary, dict) and summary.get('aborted'):
            # The runner stopped this run before it scored anything (a mock could not answer). It is not a run to list or to
            # score against, whatever render an earlier run left in the folder.
            print(f'note: {name} was stopped by the runner (run_summary.json `aborted`) — not indexed.', file=sys.stderr)
            continue
        before = prior.get(name, {})
        stage, source = _stage(name, run, before, added)
        written = _utc(summary.get('written_at')) if isinstance(summary, dict) else None
        if written is None:
            written = datetime.fromtimestamp(run_json.stat().st_mtime, tz=timezone.utc).isoformat()
        trials = run.get('trials')
        n = trials.get('n') if isinstance(trials, dict) else None
        entries.append({
            'name': name,
            'stage': stage,
            'stage_source': source,
            'written_at': written,
            'eval_set_id': run['eval_set_id'] if isinstance(run.get('eval_set_id'), str) else None,
            'scenario_set_id': before['scenario_set_id'] if 'scenario_set_id' in before else current_set,
            'trials': n if isinstance(n, int) and not isinstance(n, bool) else None,
        })
    for name in sorted(set(added) - {e['name'] for e in entries}):
        print(f'note: --add {name}={added[name]} names no runs/{name}/run.json — nothing recorded for it.', file=sys.stderr)
    entries.sort(key=lambda e: (e['written_at'] or '', _name_key(e['name'])))
    finals = [e['name'] for e in entries if e['stage'] == 'final' and _audited(runs_dir / e['name'])]
    return {'schema': SCHEMA, 'scored_run': finals[-1] if finals else None, 'runs': entries}


def write_index(path: Path, doc: dict) -> None:
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _parse_adds(values: list[str]) -> dict[str, str]:
    added: dict[str, str] = {}
    for value in values:
        name, sep, stage = value.partition('=')
        if not sep or not name or '/' in name or name in ('.', '..') or stage not in RUN_STAGES:
            raise ValueError(f'--add {value!r}: expected NAME=STAGE, a run folder name and one of {", ".join(RUN_STAGES)}')
        added[name] = stage
    return added


def cmd_write(args: argparse.Namespace) -> int:
    try:
        added = _parse_adds(args.add or [])
    except ValueError as exc:
        print(f'runs index: {exc}', file=sys.stderr)
        return EXIT_USAGE
    workdir = Path(args.workdir)
    runs_dir = workdir / 'artifacts' / 'runs'
    if not runs_dir.is_dir():
        print(f'runs index: {runs_dir} does not exist — no run to index', file=sys.stderr)
        return EXIT_NO_RUNS
    doc = build_index(workdir, added)
    write_index(runs_dir / INDEX, doc)
    print(f'wrote runs/{INDEX} — {len(doc["runs"])} run(s), scored run: {doc["scored_run"] or "none"}')
    return 0


def _spans(summary: object) -> list[tuple[datetime, datetime]]:
    """`(started_at, ended_at)` per scenario of a `run_summary.json` `scenario_timings`, aware timestamps only."""
    timings = summary.get('scenario_timings') if isinstance(summary, dict) else None
    spans = []
    for row in timings.values() if isinstance(timings, dict) else ():
        try:
            start, end = (datetime.fromisoformat(str(row[k]).replace('Z', '+00:00')) for k in ('started_at', 'ended_at'))
        except (KeyError, TypeError, ValueError):
            continue
        if start.tzinfo is not None and end.tzinfo is not None and end >= start:
            spans.append((start, end))
    return sorted(spans)


def _waited_for_lease(summary: object) -> bool:
    """Whether a row of a `run_summary.json` `scenario_timings` waited for a leased resource (`lease_wait_seconds` > 0)."""
    timings = summary.get('scenario_timings') if isinstance(summary, dict) else None
    rows = timings.values() if isinstance(timings, dict) else ()
    return any(isinstance(row, dict) and isinstance(row.get('lease_wait_seconds'), (int, float)) and row['lease_wait_seconds'] > 0 for row in rows)


def eta_minutes(workdir: Path, scenarios: int, trials: int = 1) -> int | None:
    """Minutes for a run of `scenarios` x `trials`, off the latest qualification run's own clock; None with none."""
    if not (workdir / 'artifacts' / 'runs').is_dir():
        return None
    with contextlib.redirect_stderr(io.StringIO()):  # the index's notes are for `write`
        runs = build_index(workdir, {})['runs']
    for entry in reversed([e for e in runs if e['stage'] == 'qualification']):
        summary = _read_json(workdir / 'artifacts' / 'runs' / entry['name'] / 'run_summary.json')
        spans = _spans(summary)
        if spans:
            break
    else:
        return None
    per = (max(end for _, end in spans) - spans[0][0]).total_seconds() / len(spans)
    serial = _waited_for_lease(summary) or all(start >= before_end for (start, _), (_, before_end) in zip(spans[1:], spans))
    return max(1, math.ceil(per * scenarios * (trials if serial else 1) / 60))


def cmd_eta(args: argparse.Namespace) -> int:
    minutes = eta_minutes(Path(args.workdir), args.scenarios, args.trials)
    if minutes is not None:
        print(minutes)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    write = sub.add_parser('write', help='rebuild artifacts/runs/index.json from disk')
    write.add_argument('workdir', help='the agent workdir (plurai_sim/, $WORKDIR)')
    write.add_argument('--add', action='append', metavar='NAME=STAGE', help='record the stage of a run; wins over its run.json stage')
    write.set_defaults(func=cmd_write)
    eta = sub.add_parser('eta', help='the minutes a run should take, from the latest qualification run\'s clock')
    eta.add_argument('workdir', help='the agent workdir (plurai_sim/, $WORKDIR)')
    eta.add_argument('--scenarios', type=int, required=True, help='the scenarios the run plays')
    eta.add_argument('--trials', type=int, default=1, help='trials per scenario (default 1)')
    eta.set_defaults(func=cmd_eta)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
