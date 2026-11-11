#!/usr/bin/env python3
"""Compare two runs of the same suite — the TOOL TRAIL first, then every cell that moved.

    python3 compare_runs.py <before>/run.json <after>/run.json \
        [--floor <run.json> --floor <run.json> ...] [--regressions-dir DIR] [--json] [--out FILE]

A run directory is accepted anywhere a `run.json` is; the file inside it is read.

REGRESSIONS come last, read off their own replays (`run.json` → `regressions`): likely fixed only when
the after replays caught it 0 times over the clean replays the before asks for at the 80% decision (95%
on both sides for a rating that predates it); still caught on any red; intermittent when the before is
too rare to decide within one step; otherwise not yet shown, with the clean replays still needed. A
regression the before run did not replay takes its record-time rate (`<regressions-dir>/<folder>/replays.json`)
as its before.

WHY THIS EXISTS. `/simulation-fixes --verify` re-runs a suite after a repair and reads the
result as the agent's response to it: "each previously-failing scenario now passes AND every
previously-passing scenario still passes". That reading is only sound if the run-to-run variance
of this suite is known, and nothing in the product measured it. Measured on three runs of one
bench — two of them byte-identically configured — a cell flipped with an IDENTICAL tool plan, and
a tool plan moved without any cell noticing. Either one, read as a repair's effect, is a wrong
attribution, and the second is invisible without the trail.

SO THE TRAIL LABEL COMES FIRST, AND EVERY CELL ROW CARRIES IT. Five labels, and they are a
NOISE-SCALE vocabulary:

* `same_trail` — the two runs dispatched the same normalized plan, in the same order.
* `within_trail_noise` — the plan moved no further than this suite's own same-configuration runs
  move it. A cell delta here is not evidence about the change.
* `beyond_trail_noise` — the plan moved further than that. The agent did something different;
  whether the change caused it is still a question, but the delta is at least real.
* `trail_noise_unknown` — **no floor**. Fewer than two same-configuration runs were supplied for
  this scenario, so there is no scale to read the distance against. NOTHING ON THE SCALE is
  labelled from a floorless comparison, `same_trail` included: the label vocabulary is the scale,
  and publishing the one member of it that reads as "nothing moved" would license exactly the
  attribution the missing floor cannot support. The one label a floorless comparison does still
  produce is `no_trail`, below, which is not a point on that scale.
* `no_trail` — both runs dispatched nothing at all. That is a fact no floor changes, so it is
  decided before the floor is consulted.

THE FLOOR IS N ≥ 2 SAME-CONFIGURATION WHOLE-SET RUNS, and nothing else. In particular it is NOT
the runner's stability retry: that repeat is conditioned on FAILURE (only `invalid_run`,
`incomplete` or `eval_failed` earn one, capped at half the suite), so it is structurally
unavailable on the clean runs a reader most wants it for — and measured on the bench above it
reports 0 for a scenario whose true independent repeat distance is 1. A floor that is
selection-biased and demonstrably zero where the truth is one is worse than no floor: it licenses
discounting a real delta. The repeat IS printed, as evidence, with its selection named.

Stdlib only. Writes nothing unless `--out` says where.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))

import _confine  # noqa: E402 - after the sys.path lines it depends on
from build_run_json import TRAIL_SIGNATURE_MODE, policy_name, trail_distance  # noqa: E402

#: The verdicts a scenario row can be in, derived from the row's own name lists. `pass` is the
#: residue — a policy the row grades and does not name anywhere is one that passed — so it is
#: only ever claimed for a row that reached a verdict at all.
#: `not_applicable` is its OWN verdict here for the reason the docstring below gives: this module
#: has no cell map to fall back on, so a premise-gated cell filed under `inconclusive` is a judge
#: that could not decide in every run-to-run delta, with no reason channel that could ever recover
#: it. A run where a premise stopped arising and one where a judge started wobbling are different
#: findings; they were the same row.
CELL_FAIL, CELL_NOT_APPLICABLE, CELL_INCONCLUSIVE, CELL_SKIPPED, CELL_PASS = 'fail', 'not_applicable', 'inconclusive', 'skipped', 'pass'
#: Graded under the eval it defers to (`deferred_evals`): its owner's verdict carries the case.
CELL_DEFERRED = 'deferred'

LABEL_SAME = 'same_trail'
LABEL_WITHIN = 'within_trail_noise'
LABEL_BEYOND = 'beyond_trail_noise'
LABEL_UNKNOWN = 'trail_noise_unknown'
LABEL_NO_TRAIL = 'no_trail'

#: How many same-configuration runs a floor needs. Two, because one run has no distance in it at
#: all: a floor is a distance between repeats, and with a single sample there is nothing to
#: subtract. Three or more simply take the widest pair.
FLOOR_MIN_RUNS = 2


def load_run(path: Path) -> dict:
    """One run's `run.json` — from the file, or from the run directory that holds it.

    Every way this can fail leaves by the same door: a `SystemExit` naming the path and the
    reason. A missing or unparsable file escaped as a traceback instead, one line above a clean
    refusal for the not-an-object case — so the operator read two different diagnoses for two
    spellings of "this file is not usable".
    """
    target = path / 'run.json' if path.is_dir() else path
    try:
        raw = target.read_text(encoding='utf-8')
    except OSError as exc:
        raise SystemExit(f'{target}: could not be read ({type(exc).__name__}: {exc})') from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f'{target}: is not valid JSON ({exc})') from exc
    if not isinstance(doc, dict):
        raise SystemExit(f'{target} is not a run.json object')
    doc['_path'] = str(target)
    return doc


def rows_by_id(run: dict) -> dict[str, dict]:
    return {str(row['id']): row for row in (run.get('scenarios') or []) if isinstance(row, dict) and row.get('id')}


def signature_of(row: dict) -> list[str] | None:
    """The row's normalized plan, or None when the row was built before this field existed.

    None is NOT an empty plan: a run.json from an older plugin carries no signature and the plan
    cannot be recovered from a count, so every comparison involving it is unknown. A row that
    carries `[]` really did dispatch nothing, and that is a measurement.
    """
    calls = row.get('trail_signature')
    return [str(c) for c in calls] if isinstance(calls, list) else None


def modes_of(*runs: dict) -> set[str]:
    return {str(row['trail_signature_mode']) for run in runs for row in rows_by_id(run).values()
            if row.get('trail_signature_mode')}


def cells_of(row: dict) -> dict[str, str]:
    """`{policy: verdict}` for the policies this row says anything about.

    Derived from the row's verdict name lists rather than from a cell map, because `run.json` carries
    no per-scenario cell map — the lists ARE the record. A policy the row grades and names nowhere
    passed, and that residue is only claimed for a row that reached a verdict: on an errored row
    nothing was decided, so nothing is called a pass.
    """
    out: dict[str, str] = {}
    for key, verdict in (('skipped_evals', CELL_SKIPPED), ('not_applicable_evals', CELL_NOT_APPLICABLE),
                         ('inconclusive_evals', CELL_INCONCLUSIVE), ('deferred_evals', CELL_DEFERRED),
                         ('failing_evals', CELL_FAIL)):
        for entry in (row.get(key) or []):
            out[policy_name(entry)] = verdict
    return out


def graded(row: dict) -> bool:
    return str(row.get('result') or '') in ('pass', 'fail')


def cell_deltas(before: dict, after: dict, universe: set[str]) -> list[dict]:
    """Every policy whose verdict is not the same on the two rows.

    `universe` is the eval set both runs were scored against (the union of the two runs' own
    `evals[].id`), so a policy named on neither row is compared as pass-vs-pass and drops out,
    while a policy that LEFT one run's set is reported as `absent` on that side rather than
    silently read as a pass.
    """
    a, b = cells_of(before), cells_of(after)
    out = []
    for name in sorted(universe | set(a) | set(b)):
        old = a.get(name, CELL_PASS if graded(before) else None)
        new = b.get(name, CELL_PASS if graded(after) else None)
        if old != new:
            out.append({'policy': name, 'before': old or 'absent', 'after': new or 'absent'})
    return out


def floor_for(scenario: str, floor_runs: list[dict]) -> tuple[int | None, list[str]]:
    """`(the widest same-configuration distance for this scenario, the runs it was measured on)`.

    None when fewer than `FLOOR_MIN_RUNS` of the supplied runs carry a signature for this
    scenario — a floor is a distance between repeats, and a scenario missing from all but one of
    them has no repeat to measure. WIDEST, not mean: the floor is what this suite has been
    OBSERVED to do to itself, and a mean would put half the observed variance above the line.
    """
    have = [(str(run.get('run_name') or run.get('_path')), sig)
            for run in floor_runs
            if (sig := signature_of(rows_by_id(run).get(scenario) or {})) is not None]
    if len(have) < FLOOR_MIN_RUNS:
        return None, [name for name, _ in have]
    widest = 0
    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            d = trail_distance(have[i][1], have[j][1])
            widest = max(widest, d['added'] + d['dropped'])
    return widest, [name for name, _ in have]


def label_for(before: list[str] | None, after: list[str] | None, floor: int | None, distance: int) -> str:
    """The trail label — in the one order the five states may be decided in.

    A row with NO SIGNATURE first: `None` is not an empty plan (`signature_of`), so a run.json
    written before the field existed cannot be told from one that dispatched nothing, and every
    comparison involving it is unknown. Then `no_trail` — two runs that both dispatched nothing
    are not a noise judgement at all, and no floor can change that, which is why it is decided
    BEFORE the floor is consulted. Then the missing floor, because everything below it is a
    statement on a scale that does not exist. Only then the scale itself, and `same_trail` sits
    inside it deliberately: it is the label that most strongly licenses reading a cell delta as
    the change's effect, so it is exactly the one a floorless comparison must not produce.
    """
    if before is None or after is None:
        return LABEL_UNKNOWN
    if not before and not after:
        return LABEL_NO_TRAIL
    if floor is None:
        return LABEL_UNKNOWN
    if distance == 0 and before == after:
        return LABEL_SAME
    return LABEL_WITHIN if distance <= floor else LABEL_BEYOND


def repeat_of(row: dict) -> dict | None:
    """The runner's own second attempt at this scenario, as evidence — never as a threshold."""
    repeat = row.get('trail_repeat_distance')
    if not isinstance(repeat, dict):
        return None
    return {'added': int(repeat.get('added', 0)), 'dropped': int(repeat.get('dropped', 0)),
            'selection': str(row.get('trail_repeat_selection') or 'unknown')}


def compare(before: dict, after: dict, floor_runs: list[dict], regressions_dir: Path | None = None) -> dict:
    a_rows, b_rows = rows_by_id(before), rows_by_id(after)
    universe = {str(e['id']) for run in (before, after) for e in (run.get('evals') or [])
                if isinstance(e, dict) and e.get('id')}
    out = []
    for scenario in sorted(set(a_rows) | set(b_rows)):
        a, b = a_rows.get(scenario, {}), b_rows.get(scenario, {})
        sig_a, sig_b = signature_of(a), signature_of(b)
        dist = trail_distance(sig_a or [], sig_b or []) if sig_a is not None and sig_b is not None else None
        floor, floor_from = floor_for(scenario, floor_runs)
        label = label_for(sig_a, sig_b, floor, (dist['added'] + dist['dropped']) if dist else 0)
        args_a, args_b = a.get('trail_signature_args'), b.get('trail_signature_args')
        row = {
            'id': scenario,
            'trail_label': label,
            'trail_distance': dist,
            # The strictest reading, beside the mode's own: when this moves and `trail_distance`
            # does not, only argument VALUES changed — a booking code, a date the agent computed,
            # a case variant. That is a property of the dialog, and a reader who cannot see the
            # two numbers apart cannot tell it from a different tool plan.
            'trail_args_distance': (trail_distance([str(x) for x in args_a], [str(x) for x in args_b])
                                    if isinstance(args_a, list) and isinstance(args_b, list) else None),
            'trail_noise_floor': floor,
            'trail_noise_floor_from': floor_from,
            'trail_source': {'before': a.get('trail_source'), 'after': b.get('trail_source')},
            'trail_signature_recovered': {'before': a.get('trail_signature_recovered'),
                                          'after': b.get('trail_signature_recovered')},
            'trail_repeat_distance': {'before': repeat_of(a), 'after': repeat_of(b)},
            'result': {'before': a.get('result'), 'after': b.get('result')},
            'score': {'before': a.get('score'), 'after': b.get('score')},
            # EVERY cell row carries the scenario's trail label. A cell delta read without it is
            # the defect this whole comparison exists to remove, and a label printed once at the
            # top of a section is a label the eye skips on the row that matters.
            'cell_deltas': [delta | {'trail_label': label} for delta in cell_deltas(a, b, universe)],
        }
        out.append(row)
    modes = sorted(modes_of(before, after, *floor_runs))
    return {
        'before': before.get('_path'), 'after': after.get('_path'),
        'trail_signature_mode': modes[0] if len(modes) == 1 else None,
        # Two runs normalized under different rules are not comparable and the comparison says so
        # rather than picking one. This is why the mode is written onto every row in the first
        # place: without it the mismatch is invisible and the labels below are nonsense nobody
        # can see is nonsense.
        'trail_signature_modes_seen': modes,
        'floor_runs': [str(run.get('run_name') or run.get('_path')) for run in floor_runs],
        'floor_usable': len(floor_runs) >= FLOOR_MIN_RUNS,
        'scenarios': out,
        # The regressions are read off their own replays, not off the cells above: a replay batch is K
        # samples of one cut, and its before is the earlier batch or the record-time rate.
        **compare_regressions(before, after, regressions_dir),
    }


#: What a regression's replays say across the two runs.
REG_FIXED, REG_STILL_CAUGHT, REG_NOT_YET_SHOWN, REG_NOT_PINNED = 'fixed', 'still_caught', 'not_yet_shown', 'not_pinned'
#: Caught before the fix, too rarely for one check to show a drop: the after only shows it did not get worse.
REG_INTERMITTENT = 'intermittent'
#: How many replays one regression-only step runs at most; a before that needs more is said, not chased.
REPLAYS_MAX = 10
#: A record-time rate's schema (`regressions.py confirm`), and the band of a folder that was never sampled.
CONFIRM_SCHEMA, BAND_SEEN_ONCE = 'regression-confirm-v1', 'seen_once'
#: The SDK's decision pair (80%) and its published / retirement pair (95%), by level. A rating without the
#: 80% pair (an older SDK) is decided at 95% on BOTH sides — the two levels are never mixed.
DECISION_PAIR, PUBLISHED_PAIR = ('wilson80_failed', 'clean_replays_to_decide'), ('wilson95_failed', 'clean_replays_to_clear')
LEVEL_DECISION, LEVEL_PUBLISHED = 80, 95


def _rating(source: dict) -> dict:
    """Both rating pairs of an SDK cell; the 80% pair only when the SDK wrote it (its absence picks the 95% level)."""
    return {key: source.get(key) for key in PUBLISHED_PAIR} | {key: source[key] for key in DECISION_PAIR if key in source}


def _at_cut(cell: object, row: dict | None = None) -> dict | None:
    """`{failed, measured, <both rating pairs>, clear_axis, replay_mode}` of one clear-axis cell, or None."""
    if not isinstance(cell, dict):
        return None
    # A pass whose premise never arose (`trigger_absent`, inside `passed`) measured nothing: the run.json rule (`measured_replays`).
    failed, passed = int(cell.get('failed') or 0), int(cell.get('passed') or 0) - int(cell.get('trigger_absent') or 0)
    return {'failed': failed, 'measured': failed + passed, **_rating(cell), 'source': 'replays',
            'clear_axis': (row or {}).get('clear_axis'), 'replay_mode': (row or {}).get('replay_mode')}


def _recorded_before(regressions_dir: Path | None, folder: str | None) -> dict | None:
    """The folder's record-time rate (`replays.json`) as a before — the agent was sampled before any fix. Its rating
    pairs come from the SDK's record kept under `rate`, else the record's top level."""
    path = regressions_dir / folder / 'replays.json' if regressions_dir is not None and folder else None
    try:
        doc = json.loads(path.read_text(encoding='utf-8')) if path is not None and path.is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get('schema') != CONFIRM_SCHEMA:
        return None
    rate = doc.get('rate') if isinstance(doc.get('rate'), dict) else {}
    return {'failed': doc.get('k') or 0, 'measured': doc.get('n') or 0, **_rating(doc | rate), 'source': 'record', 'band': doc.get('band')}


def decision_level(before: dict | None, after: dict | None) -> int:
    """80 when both sides carry the SDK's 80% pair (an after that measured nothing counts as carrying it), else 95."""
    def carries(side: dict | None) -> bool:
        return isinstance(side, dict) and DECISION_PAIR[1] in side

    after_ok = after is None or carries(after) or not after.get('measured')
    return LEVEL_DECISION if carries(before) and after_ok else LEVEL_PUBLISHED


def decision_count(before: dict | None, after: dict | None) -> tuple[int | None, int]:
    """`(clean draws the before asks for, level)` — read at the one level both sides share."""
    level = decision_level(before, after)
    count = (before or {}).get(DECISION_PAIR[1] if level == LEVEL_DECISION else PUBLISHED_PAIR[1])
    return (count if isinstance(count, int) and not isinstance(count, bool) else None), level


def _apart(a: object, b: object) -> bool | None:
    """Whether two Wilson ranges do not overlap; None when either is missing."""
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == 2):
        return None
    return a[1] < b[0] or b[1] < a[0]


def regression_verdict(before: dict | None, after: dict | None) -> dict:
    """The fixed / not-fixed decision, read at ONE level (`decision_count`; the row says which as `level`):
    `still_caught` — after caught it at least once; `fixed` — after caught 0 and measured at least the clean
    draws the before asks for (the SDK's count already carries its floor of 3); `not_pinned` — a folder recorded
    seen once, caught 0 of 5+ in its first batch; `intermittent` — caught before, but more clean draws than one
    step runs (or none) would show a drop, so the after only shows it did not get worse; else `not_yet_shown`,
    with how many more clean draws are needed."""
    count, level = decision_count(before, after)
    if after is None or not after.get('measured'):
        return {'verdict': REG_NOT_YET_SHOWN, 'replays_needed': None, 'level': level, 'why': 'no run after the fix reached this check'}
    if after['failed'] >= 1:
        return {'verdict': REG_STILL_CAUGHT, 'replays_needed': None, 'level': level}
    if count is not None and after['measured'] >= count:
        return {'verdict': REG_FIXED, 'replays_needed': 0, 'level': level}
    if (before or {}).get('source') == 'record' and before.get('band') == BAND_SEEN_ONCE and after['measured'] >= 5:
        return {'verdict': REG_NOT_PINNED, 'replays_needed': None, 'level': level, 'why': 'seen once at record and not caught in its first batch'}
    if before is None:
        return {'verdict': REG_NOT_YET_SHOWN, 'replays_needed': None, 'level': level, 'why': 'nothing before the fix measured it'}
    if before['failed'] >= 1 and (count is None or count > REPLAYS_MAX):
        return {'verdict': REG_INTERMITTENT, 'replays_needed': None, 'level': level, 'why': 'checked only for not getting worse'}
    if before['failed'] == 0 or count is None:
        return {'verdict': REG_NOT_YET_SHOWN, 'replays_needed': None, 'level': level, 'why': 'the runs before the fix never caught it'}
    return {'verdict': REG_NOT_YET_SHOWN, 'replays_needed': max(0, count - after['measured']), 'level': level}


def compare_regressions(before: dict, after: dict, regressions_dir: Path | None) -> dict:
    """The regressions before → after, by folder, and the issue types' rates before → after."""
    def by_folder(run: dict) -> dict[str, dict]:
        return {str(r.get('folder') or r.get('scenario')): r for r in run.get('regressions') or [] if isinstance(r, dict)}

    def clear_cell(row: dict) -> object:
        # The cell the SDK names for the clear verdict: the cut, or the whole replay for a folder played from its opener.
        axis = row.get('clear_axis')
        return row.get(axis if axis in ('at_cut', 'anywhere') else 'at_cut')

    def owner_cell(run: dict, row: dict, owner: str) -> object:
        # The after run's cell for ANOTHER check than its row's owner: the SDK block lists every policy, so a before read on
        # one check is never compared with an after read on another (a probe that passed before any fix would say "fixed").
        entry = (((run.get('regression_replays') or {}).get('regressions') or {}).get(str(row.get('scenario') or row.get('folder'))) or {})
        cells = (entry.get('policies') or {}).get(owner)
        axis = row.get('clear_axis') if row.get('clear_axis') in ('at_cut', 'anywhere') else 'at_cut'
        return cells.get(axis) if isinstance(cells, dict) else None

    a, b = by_folder(before), by_folder(after)
    rows = []
    for folder in sorted(set(a) | set(b)):
        prior = _at_cut(clear_cell(a.get(folder, {})), a.get(folder)) or _recorded_before(regressions_dir, folder)
        was, now_owner = (a.get(folder) or {}).get('owner'), (b.get(folder) or {}).get('owner')
        now = _at_cut(clear_cell(b.get(folder, {})) if not (was and now_owner and was != now_owner) else owner_cell(after, b[folder], was),
                      b.get(folder))
        rows.append({'folder': folder, 'title': (b.get(folder) or a.get(folder) or {}).get('title'),
                     'owner': was or now_owner, 'before': prior, 'after': now,
                     **regression_verdict(prior, now)})

    def rates(run: dict) -> dict[str, dict]:
        # The issue types live in the operator file beside run.json (never in run.json); a dict handed in
        # with its own `issue_types` is read as is.
        entries = run.get('issue_types')
        if entries is None and run.get('_path'):
            try:
                side = json.loads((Path(run['_path']).parent / 'regressions_detail.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                side = {}
            entries = side.get('issue_types') if isinstance(side, dict) and side.get('schema') == 'regressions-detail-v1' else None
        return {str(e.get('policy')): e.get('rate') or {} for e in entries or [] if isinstance(e, dict)}

    ra, rb = rates(before), rates(after)
    issue_types = [{'policy': p, 'before': ra.get(p), 'after': rb.get(p),
                    'apart': _apart((ra.get(p) or {}).get('wilson95_failed'), (rb.get(p) or {}).get('wilson95_failed'))}
                   for p in sorted(set(ra) | set(rb))]
    return {'regressions': rows, 'issue_types': issue_types}


def _rate_cell(rate: object) -> str:
    if not isinstance(rate, dict):
        return '—'
    failed, passed = int(rate.get('failed') or 0), int(rate.get('passed') or 0)
    span = rate.get('wilson95_failed')
    tail = f' ({round(100 * span[0])}–{round(100 * span[1])}%)' if isinstance(span, list) and len(span) == 2 else ''
    return f'{failed}/{failed + passed}{tail}'


#: The plain label of each decision, as fixes.md and the comparisons print it.
VERDICT_LABELS = {REG_FIXED: 'likely fixed', REG_STILL_CAUGHT: 'still failing', REG_NOT_YET_SHOWN: 'not yet shown',
                  REG_INTERMITTENT: 'intermittent', REG_NOT_PINNED: 'not pinned'}


def verdict_label(row: dict) -> str:
    """A row's plain label: `likely fixed` at the 80% decision, `fixed` on a row decided at 95% (an older rating)."""
    if row.get('verdict') == REG_FIXED and row.get('level') == LEVEL_PUBLISHED:
        return 'fixed'
    return VERDICT_LABELS.get(str(row.get('verdict')), str(row.get('verdict')))


def _k_of_n(side: dict | None) -> str:
    """`k of n` with the 95% range in brackets, or — when nothing was measured."""
    if not side:
        return '—'
    span = side.get('wilson95_failed')
    tail = f' ({round(100 * span[0])}–{round(100 * span[1])}%)' if isinstance(span, list) and len(span) == 2 else ''
    return f'{side["failed"]} of {side["measured"]}{tail}'


def render_regressions(section: dict) -> list[str]:
    rows, types = section.get('regressions') or [], section.get('issue_types') or []
    L: list[str] = []
    if rows:
        L += ['## Regressions — before → after', '',
              '| Regression | Before | After | Verdict |', '|---|---|---|---|']
        for row in rows:
            before, after = row.get('before'), row.get('after')
            b = _k_of_n(before) + (' (measured at record)' if before and before.get('source') == 'record' else '')
            verdict = verdict_label(row)
            if row['verdict'] == REG_NOT_YET_SHOWN and row.get('replays_needed') is not None:
                verdict += f' — {row["replays_needed"]} more clean replay(s) needed'
            if row.get('why'):
                verdict += f' ({row["why"]})'
            if row['verdict'] == REG_NOT_PINNED:
                verdict += ' — moves to Recurring issue types'
            L.append(f'| `{row["folder"]}` | {b} | {_k_of_n(after)} | {verdict} |')
        L += ['', '_Likely fixed means caught in none of the after replays, over enough of them to say so with 80% confidence '
                  '(rows rated without that level are decided at 95% and read "fixed"); the ranges in brackets are 95%._', '']
    if types:
        L += ['## Recurring issue types — before → after', '', '| Check | Before | After | Ranges apart |', '|---|---|---|---|']
        for entry in types:
            apart = {True: 'yes', False: 'no', None: '—'}[entry['apart']]
            L.append(f'| `{entry["policy"]}` | {_rate_cell(entry["before"])} | {_rate_cell(entry["after"])} | {apart} |')
        L.append('')
    return L


_LABEL_PROSE = {
    LABEL_SAME: 'the same tool plan, in the same order',
    LABEL_WITHIN: "the plan moved no further than this suite's own same-configuration runs move it",
    LABEL_BEYOND: "the plan moved FURTHER than this suite's own same-configuration runs move it",
    LABEL_UNKNOWN: 'NO FLOOR — fewer than two same-configuration runs carry this scenario, so nothing is labelled',
    LABEL_NO_TRAIL: 'neither run dispatched a tool',
}


def render(doc: dict) -> str:
    L = ['# Run comparison — the tool trail first', '',
         f'* before: `{doc["before"]}`', f'* after: `{doc["after"]}`']
    if doc['floor_runs']:
        L.append(f'* noise floor measured on: {", ".join(f"`{n}`" for n in doc["floor_runs"])}')
    else:
        L.append('* noise floor measured on: **nothing** — no `--floor` run was supplied')
    if not doc['floor_usable']:
        L += ['', f'> **No floor.** A floor needs {FLOOR_MIN_RUNS} same-configuration whole-set runs and '
                  f'{len(doc["floor_runs"])} was supplied, so every scenario below that dispatched anything '
                  f'is `{LABEL_UNKNOWN}` (a scenario where NEITHER run dispatched a tool is `{LABEL_NO_TRAIL}` '
                  '— a fact no floor changes, decided before the floor is consulted). '
                  'NOTHING here licenses a claim about the change — not "verified", not "no regressions", '
                  'and not "unchanged". Re-run the suite a second time under the BEFORE configuration and '
                  'pass both with `--floor`.']
    if len(doc['trail_signature_modes_seen']) > 1:
        L += ['', f'> **Two normalizations.** The runs carry {doc["trail_signature_modes_seen"]} — they were '
                  'built by different plugin versions and their signatures are not comparable. Rebuild both '
                  f'`run.json` with one version (this one normalizes as `{TRAIL_SIGNATURE_MODE}`).']
    L += ['', '## Per scenario', '',
          '| Scenario | Trail | Distance | Floor | Repeat (evidence) | Result | Cells moved |',
          '|---|---|---|---|---|---|---|']
    for row in doc['scenarios']:
        dist = row['trail_distance']
        dist_cell = f'+{dist["added"]}/−{dist["dropped"]}' if dist else '—'
        args = row['trail_args_distance']
        if args and dist and (args['added'] + args['dropped']) > (dist['added'] + dist['dropped']):
            dist_cell += f' (values only: +{args["added"]}/−{args["dropped"]})'
        floor = row['trail_noise_floor']
        repeats = [f'{side} +{r["added"]}/−{r["dropped"]} ({r["selection"]})'
                   for side, r in row['trail_repeat_distance'].items() if r]
        result = f'{row["result"]["before"]} → {row["result"]["after"]}'
        L.append(f'| `{row["id"]}` | **{row["trail_label"]}** | {dist_cell} | '
                 f'{"—" if floor is None else floor} | {", ".join(repeats) or "—"} | '
                 f'{result} | {len(row["cell_deltas"])} |')
    L += ['', f'_Trail labels: ' + ' · '.join(f'**{k}** — {v}' for k, v in _LABEL_PROSE.items()) + '._',
          '', '_**Repeat** is the runner\'s own second attempt at a scenario, printed as evidence. It is NOT '
              'the floor: the runner retries only scenarios that failed, so the population is conditioned on '
              'failure and a clean run produces none at all._', '']
    moved = [row for row in doc['scenarios'] if row['cell_deltas']]
    if moved:
        L += ['## Cells that moved', '',
              '| Scenario | Trail | Policy | Before | After |', '|---|---|---|---|---|']
        for row in moved:
            for delta in row['cell_deltas']:
                L.append(f'| `{row["id"]}` | **{delta["trail_label"]}** | `{delta["policy"]}` | '
                         f'{delta["before"]} | {delta["after"]} |')
        L += ['', '_Read the Trail column BEFORE the cell. A cell that moved on a `within_trail_noise` or '
                  '`trail_noise_unknown` row is not evidence about the change — on the runs this rule was '
                  'measured on, one cell flipped between two byte-identically configured runs whose tool '
                  'plans were identical._', '']
    else:
        L += ['_No cell moved._', '']
    L += render_regressions(doc)
    return '\n'.join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('before', type=Path, help='the baseline run.json (or its run directory)')
    ap.add_argument('after', type=Path, help='the run.json to compare against it')
    ap.add_argument('--floor', type=Path, action='append', default=[],
                    help=f'a same-configuration run the noise floor is measured on; pass at least {FLOOR_MIN_RUNS}')
    ap.add_argument('--regressions-dir', type=Path,
                    help="the workdir's regressions/: a regression with no replay in the before run takes its "
                         'record-time rate (replays.json) as its before')
    ap.add_argument('--json', action='store_true', help='emit the typed comparison instead of the markdown')
    ap.add_argument('--out', type=Path, help='write the output here as well as to stdout')
    _confine.add_flag(ap)
    args = ap.parse_args(argv)
    if args.out and (why := _confine.refusal(args.out, allow_outside=args.allow_outside_workdir)):
        print(f'compare_runs: {why}', file=sys.stderr)
        return 2

    doc = compare(load_run(args.before), load_run(args.after), [load_run(p) for p in args.floor], args.regressions_dir)
    if not doc['floor_usable']:
        print(f'note: {len(doc["floor_runs"])} --floor run(s) supplied and {FLOOR_MIN_RUNS} are needed, so every '
              f'scenario that dispatched anything is `{LABEL_UNKNOWN}` (one where neither run dispatched a '
              f'tool is `{LABEL_NO_TRAIL}`) — no cell delta below may be attributed to the change.',
              file=sys.stderr)
    text = json.dumps(doc, indent=2) if args.json else render(doc)
    print(text)
    if args.out:
        args.out.write_text(text + ('' if text.endswith('\n') else '\n'), encoding='utf-8')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
