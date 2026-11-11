#!/usr/bin/env python3
"""An accepted fix read k of K on the scenarios that exposed it — the `/simulation-fixes --verify` verdict.

    python3 verify_exposing.py trials  --before RUN [--before RUN ...] --pin SCENARIO[:POLICY] [--pin ...]
    python3 verify_exposing.py verdict --before RUN [...] --after RUN [...] --pin ... [--json] [--out FILE]

A RUN is a run directory, or its `run.json` / `run_summary.json`. A PIN is one exposing scenario (the
scenario directory's name; a leading `trial_<k>/` is dropped) and the eval that caught the issue there;
a pin without a policy reads the scenario's goal verdict.

WHY. Most red cells vary across trials, so one re-run of the exposing scenario reads an unfixed
intermittent defect as gone as often as the defect is absent. The before and the after are therefore
each a COUNT: per pin, the cell is pooled over every run given on that side — a run with a `trials`
block contributes each valid trial, a one-sample run its single row — and rated with the SDK's own
`plurai.trials.rated_cell` (the Wilson interval on the failed rate and the clean runs its lower bound
asks for), the rating the regression replays already use. The verdict is `compare_runs.regression_verdict`,
decided on the SDK's 80% pair (`clean_replays_to_decide`), or at 95% when the SDK predates it:

* `fixed` — the after caught it 0 times over at least the clean runs the before asks for ("likely fixed");
* `still_caught` — the after caught it at least once;
* `intermittent` — the before caught it, too rarely to show a drop within one verify's runs: the after
  only shows it did not get worse, and it is never `fixed`;
* `not_yet_shown` — neither, with how many more clean runs the before needs.

`trials` prints K: the most clean runs any pin's before needs, clamped to `VERIFY_TRIALS_MIN`..`REPLAYS_MAX`;
a pin that needs more than `REPLAYS_MAX` does not raise K (no affordable K shows it), and neither does a
`regression_*` scenario — it is the fix's replay, read off its own replays (a warning says so).

Runs with the SDK importable (`plurai.trials.rated_cell`); exits 2 naming what is missing otherwise. Writes
nothing unless `--out` says where.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_runs import (  # noqa: E402
    CELL_DEFERRED,
    CELL_FAIL,
    CELL_INCONCLUSIVE,
    CELL_NOT_APPLICABLE,
    CELL_PASS,
    CELL_SKIPPED,
    DECISION_PAIR,
    PUBLISHED_PAIR,
    REG_NOT_YET_SHOWN,
    REPLAYS_MAX,
    cells_of,
    decision_count,
    graded,
    regression_verdict,
    rows_by_id,
    verdict_label,
)

import _confine  # noqa: E402 - on sys.path once compare_runs has been imported

#: The fewest trials one exposing batch runs — the floor the regression replays use.
VERIFY_TRIALS_MIN = 5
#: The trials block's schema (SDK `plurai.trials.TRIALS_SCHEMA`).
TRIALS_SCHEMA = 'trials-v1'
#: `verdict --json`'s schema tag.
VERIFY_EXPOSING_SCHEMA = 'verify-exposing-v1'
_OUTCOME_KEYS = ('passed', 'failed', 'na', 'inconclusive', 'deferred')
_TRIAL_PREFIX = re.compile(r'^trial_\d+/')
#: A one-sample row's cell, by `compare_runs.cells_of` verdict.
_ROW_OUTCOME = {CELL_FAIL: 'failed', CELL_PASS: 'passed', CELL_NOT_APPLICABLE: 'na', CELL_SKIPPED: 'na', CELL_INCONCLUSIVE: 'inconclusive',
                CELL_DEFERRED: 'deferred'}

Rater = Callable[[dict[str, Any]], dict[str, Any]]


class Refused(Exception):
    """Input this script cannot read; the CLI exits 2 with the message."""


def parse_pin(text: str) -> tuple[str, str | None]:
    """`(scenario, policy)` from `SCENARIO[:POLICY]`; a leading `trial_<k>/` is not part of the scenario."""
    scenario, _, policy = text.strip().partition(':')
    scenario = _TRIAL_PREFIX.sub('', scenario.strip())
    if not scenario:
        raise Refused(f'--pin {text!r} names no scenario')
    return scenario, policy.strip() or None


def load(path: Path) -> dict[str, Any]:
    """One run: a run directory's `run.json` (else its `run_summary.json`), or the named file."""
    target = path
    if path.is_dir():
        target = path / 'run.json' if (path / 'run.json').is_file() else path / 'run_summary.json'
    try:
        doc = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{target}: not a readable run ({type(exc).__name__})') from exc
    if not isinstance(doc, dict):
        raise Refused(f'{target}: not a run object')
    doc['_path'] = str(target)
    return doc


def _zero() -> dict[str, int]:
    return dict.fromkeys(_OUTCOME_KEYS, 0)


def cell_counts(run: dict[str, Any], scenario: str, policy: str | None) -> tuple[dict[str, int] | None, str | None]:
    """`(counts, why None)` of one pin on one run, or `(None, why)` when the run does not measure it.

    A `trials` block is read as published (its valid trials; a withdrawn scenario measures nothing). A run
    without one is one sample: its row's verdict, a graded row's unnamed policy being a pass (the residue
    rule `compare_runs` reads cells by).
    """
    block = run.get('trials')
    if isinstance(block, dict) and block.get('schema') == TRIALS_SCHEMA:
        row = (block.get('scenarios') or {}).get(scenario)
        if not isinstance(row, dict):
            return None, 'not in this run'
        if row.get('withdrawn'):
            return None, f'withdrawn ({row["withdrawn"]})'
        cell = row.get('goal') if policy is None else (row.get('policies') or {}).get(policy)
        if not isinstance(cell, dict):
            return None, 'not graded in this run'
        return {key: int(cell.get(key) or 0) for key in _OUTCOME_KEYS}, None
    if not isinstance(run.get('scenarios'), list):
        return None, 'one sample with no run.json beside it'
    row = rows_by_id(run).get(scenario)
    if row is None:
        return None, 'not in this run'
    if policy is None:
        verdict = {'pass': CELL_PASS, 'fail': CELL_FAIL}.get(str(row.get('result') or ''), CELL_INCONCLUSIVE)
    else:
        verdict = cells_of(row).get(policy, CELL_PASS if graded(row) else CELL_INCONCLUSIVE)
    counts = _zero()
    counts[_ROW_OUTCOME[verdict]] += 1
    return counts, None


def pooled(runs: Sequence[dict[str, Any]], scenario: str, policy: str | None) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """The pin's counts summed over `runs` (independent samples of one configuration), and per run what it gave."""
    total = _zero()
    sources: list[dict[str, Any]] = []
    for run in runs:
        counts, why = cell_counts(run, scenario, policy)
        sources.append({'run': run.get('_path'), 'counts': counts, **({'why': why} if why else {})})
        for key in _OUTCOME_KEYS:
            total[key] += (counts or {}).get(key, 0)
    return total, sources


def rated(counts: dict[str, int], rate: Rater) -> dict[str, Any]:
    """The pooled counts rated by the SDK, in the shape `regression_verdict` reads."""
    cell = rate(dict(counts))
    return {
        'failed': counts['failed'],
        'measured': counts['failed'] + counts['passed'],
        'counts': dict(counts),
        **{key: cell.get(key) for key in PUBLISHED_PAIR},
        # The decision pair only when the SDK wrote it: its absence decides at 95% (`compare_runs.decision_level`).
        **{key: cell[key] for key in DECISION_PAIR if key in cell},
        'source': 'runs',
    }


def clean_runs_needed(before: dict[str, Any]) -> int | None:
    """The clean runs the before asks for at the decision level (80%, else 95% on an older SDK's rating)."""
    return decision_count(before, None)[0]


def clears_within_one_verify(before: dict[str, Any]) -> bool:
    needed = clean_runs_needed(before)
    return needed is not None and needed <= REPLAYS_MAX


def verify_trials(befores: Sequence[dict[str, Any]]) -> int:
    """K for the exposing batch: the most clean runs a pin's before needs, within VERIFY_TRIALS_MIN..REPLAYS_MAX.
    A pin no K up to REPLAYS_MAX can clear does not raise it."""
    needed = [n for b in befores if clears_within_one_verify(b) and (n := clean_runs_needed(b)) is not None]
    return max(VERIFY_TRIALS_MIN, *needed) if needed else VERIFY_TRIALS_MIN


def exposing_verdict(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    """`regression_verdict` over one pin, its `why` said for re-runs of an exposing scenario."""
    measured_after = after if after is not None and after['measured'] >= 1 else None
    row = regression_verdict(before, measured_after)
    level = row.get('level')
    if row['verdict'] != REG_NOT_YET_SHOWN:
        return {'verdict': row['verdict'], 'replays_needed': row.get('replays_needed'), 'level': level, **({'why': row['why']} if row.get('why') else {})}
    if before['measured'] == 0:
        why = 'no before run measured it'
    elif measured_after is None:
        why = 'no after run measured it'
    elif before['failed'] == 0:
        why = 'the before runs never caught it'
    else:
        why = f'{row["replays_needed"]} more clean run(s) needed'
    return {'verdict': REG_NOT_YET_SHOWN, 'replays_needed': row.get('replays_needed'), 'level': level, 'why': why}


def verdicts(pins: Sequence[tuple[str, str | None]], befores: Sequence[dict[str, Any]], afters: Sequence[dict[str, Any]], rate: Rater) -> dict[str, Any]:
    rows = []
    for scenario, policy in pins:
        b_counts, b_sources = pooled(befores, scenario, policy)
        a_counts, a_sources = pooled(afters, scenario, policy)
        before, after = rated(b_counts, rate), rated(a_counts, rate)
        rows.append({'scenario': scenario, 'policy': policy, 'before': before, 'after': after,
                     'before_runs': b_sources, 'after_runs': a_sources, **exposing_verdict(before, after)})
    return {'schema': VERIFY_EXPOSING_SCHEMA, 'pins': rows}


def _rate_text(side: dict[str, Any]) -> str:
    span = side.get('wilson95_failed')
    tail = f' ({round(100 * span[0])}–{round(100 * span[1])}%)' if isinstance(span, list) and len(span) == 2 else ''
    return f'{side["failed"]} of {side["measured"]}{tail}'


def render(doc: dict[str, Any]) -> str:
    lines = ['## Exposing scenarios — before → after, k of K', '', '| Scenario | Check | Before | After | Verdict |', '|---|---|---|---|---|']
    for row in doc['pins']:
        verdict = verdict_label(row) + (f' — {row["why"]}' if row.get('why') else '')
        lines.append(f'| `{row["scenario"]}` | `{row["policy"] or "goal"}` | {_rate_text(row["before"])} | {_rate_text(row["after"])} | {verdict} |')
    lines += ['', '_Likely fixed means it failed in none of the after runs, over enough of them to say so with 80% confidence; '
                  'still failing means it failed at least once after the fix; intermittent means it failed too rarely before '
                  'the fix to show a drop in one check, so the after only shows it did not get worse. The ranges in brackets are 95%._', '']
    return '\n'.join(lines)


def is_regression_pin(scenario: str) -> bool:
    """Whether a pin names a recorded regression folder (`regression_<slug>`), never an exposing scenario."""
    return scenario.startswith('regression_')


def sdk_rater() -> Rater:
    try:
        from plurai.trials import rated_cell  # noqa: PLC0415 - the SDK's arithmetic, never a copy of it
    except ImportError as exc:
        raise Refused(f'the SDK rating (plurai.trials.rated_cell) is not importable by {sys.executable}: {exc}') from exc
    return rated_cell


def main(argv: list[str] | None = None, *, rate: Rater | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('trials', 'verdict'))
    ap.add_argument('--before', type=Path, action='append', default=[], help='a run of the configuration BEFORE the fix (repeatable; pooled)')
    ap.add_argument('--after', type=Path, action='append', default=[], help='a run of the configuration AFTER the fix (repeatable; pooled)')
    ap.add_argument('--pin', action='append', default=[], help='SCENARIO[:POLICY] — an exposing scenario and the eval that caught the issue')
    ap.add_argument('--json', action='store_true', help='emit the typed verdicts instead of the markdown')
    ap.add_argument('--out', type=Path, help='write the output here as well as to stdout')
    _confine.add_flag(ap)
    args = ap.parse_args(argv)
    if args.out and (why := _confine.refusal(args.out, allow_outside=args.allow_outside_workdir)):
        print(f'verify_exposing: {why}', file=sys.stderr)
        return 2
    try:
        if not args.pin or not args.before:
            raise Refused('at least one --pin and one --before are needed')
        if args.command == 'verdict' and not args.after:
            raise Refused('verdict needs at least one --after run')
        rater = rate or sdk_rater()
        pins = [parse_pin(text) for text in args.pin]
        befores = [load(path) for path in args.before]
        if args.command == 'trials':
            rated_befores = []
            warned: set[str] = set()
            for scenario, policy in pins:
                if is_regression_pin(scenario):
                    if scenario not in warned:
                        warned.add(scenario)
                        print(f'warning: {scenario} is a recorded regression, not an exposing scenario — it is the fix\'s replay, '
                              'read off its own replays; it does not size K', file=sys.stderr)
                    continue
                counts, sources = pooled(befores, scenario, policy)
                before = rated(counts, rater)
                rated_befores.append(before)
                unread = '; '.join(f'{s["run"]}: {s["why"]}' for s in sources if s.get('why'))
                print(f'{scenario}:{policy or "goal"} before {_rate_text(before)}, clean runs needed: {clean_runs_needed(before)}'
                      + (f' (not measured by {unread})' if unread else ''), file=sys.stderr)
            print(verify_trials(rated_befores))
            return 0
        doc = verdicts(pins, befores, [load(path) for path in args.after], rater)
    except Refused as exc:
        print(f'verify_exposing: {exc}', file=sys.stderr)
        return 2
    text = json.dumps(doc, indent=2) if args.json else render(doc)
    print(text)
    if args.out:
        args.out.write_text(text + ('' if text.endswith('\n') else '\n'), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
