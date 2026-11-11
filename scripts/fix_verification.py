#!/usr/bin/env python3
"""Write `fixes.md`'s typed parts from the runs, so no count or verdict in it is retyped by hand.

    fix_verification.py table <fixes.md> --compare C [--verdict V] --fix "N=scenario/policy[,scenario/policy…]"... [--verify-run NAME]
    fix_verification.py header <fixes.md> --run <run.json>
    fix_verification.py counts <fixes.md> --workdir W

`table` writes (or replaces) the section `## Verification — runs/<verify run>`: one row per accepted fix,
in the column grammar the overview page parses — `| # | Fix | Applied | Verified | Trail | Evidence |`.
C is `compare_runs.py … --json --out C` (the before → verify comparison; its `scenarios[].trail_label`),
V is `verify_exposing.py verdict … --json --out V` (each pin read k of K). A `--fix` names the fix's number
in the plan and its exposing pins; a pin is `scenario/policy`, `scenario:policy`, or a bare scenario (its goal).

The fix's replay is the first `regression_<slug>` on its `- **Regression:**` line. Its reading is the
comparison's `regressions` row for that folder, taken only when both sides are the run's own replays of one
instrument (a before replayed in the earlier run, the same `clear_axis` and `replay_mode`) — at the recorded
turn where it failed, never the record-time sample.

The Verified cell, per fix — the trail is the WORST label over the fix's pinned scenarios:
  ✅ every pin `fixed`, OR every pin that is not is `intermittent` and the replay reading is `fixed`; and the
     trail is `same_trail`, `within_trail_noise` or `no_trail` — `✅ likely fixed (weak signal)` (decided at 80%),
     `✅ fixed` when every reading was decided at 95% (a rating that predates the 80% pair);
  ❌ a pin or the replay `still_caught`, and the trail is neither `beyond_trail_noise` nor `trail_noise_unknown`;
  —  otherwise: `not yet shown` (N more clean runs), `intermittent` (checked only for not getting worse), or
     `not measured` with the reason (`REASONS`). A row is never ✅ off an unmeasured noise floor, a plan that
     moved beyond it, a scenario the comparison does not hold, or a pin with no k-of-K verdict.
The Evidence cell opens each reading with its label and its before → after counts.

`header` rewrites the plan's first line, `# Fix plan — run <name>  (<counts>)`, from `run.json` `totals`:
`goals_achieved` of `goals_measurable` goals met; `scenarios_with_critical_failures` of `critical_reported`
(left out when none failed); `scenarios_with_high_severity_failures` of `high_severity_reported`, and
`high_severity_unreported` when present and non-zero. Those totals are trial 1's, so a run with trials
(`run.json` `trials.n > 1`) is counted over every trial instead (`trial_counts`): `G of M passed on every
valid trial (U without a measurable goal) · K of S scenarios failed a critical or required check on
some trial · N trials`. When that run is the scored run, `header` also writes the `- **Symptom:**` line of each issue
seeded from a finding (its `- **Finding:**` id) from the run's cells (`scored_findings.symptom`); a seeded issue the run
does not read, and every one of a plan for another run, keep theirs (said on stderr).

`counts` prints the fix plan's count for the fixes anchor (:func:`issue_counts`): the page's tiers, as the suite hand-off
says them, plus the plan's issues with no `Finding:` line; a `WARNING:` line on stderr names each finding the plan and the
page disagree on (exit 0 either way).

Exit codes: 0 written · 2 refused (an input unreadable or inconsistent; the message names it).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LABEL_SAME, LABEL_WITHIN, LABEL_BEYOND, LABEL_UNKNOWN, LABEL_NO_TRAIL = (
    'same_trail', 'within_trail_noise', 'beyond_trail_noise', 'trail_noise_unknown', 'no_trail')
#: The trail labels a ✅ may stand on.
QUIET = (LABEL_SAME, LABEL_WITHIN, LABEL_NO_TRAIL)
#: Worse is larger. A scenario the comparison does not hold ranks above every label: nothing was read.
NOT_COMPARED = 'not compared'
_RANK = {LABEL_SAME: 0, LABEL_NO_TRAIL: 0, LABEL_WITHIN: 1, LABEL_BEYOND: 2, LABEL_UNKNOWN: 3, NOT_COMPARED: 4}
FIXED, STILL_CAUGHT, NOT_YET_SHOWN, INTERMITTENT = 'fixed', 'still_caught', 'not_yet_shown', 'intermittent'
VERDICT_SCHEMA = 'verify-exposing-v1'
#: The level a reading was decided at when it says none (`compare_runs.regression_verdict` writes `level`).
LEVEL_PUBLISHED = 95
#: Why an exposing verdict measured nothing after the fix (`verify_exposing.exposing_verdict`).
NO_AFTER = 'no after run measured it'

#: The fixed reason of a — cell, first match wins.
REASONS = {
    'no_verdict': 'not measured: no verdict for {pins}',
    'not_compared': 'not measured: {scenarios} not in the before → verify comparison',
    'unknown': 'not measured: no noise floor measured — nothing on this row is verified',
    'beyond': 'the agent ran a materially different plan (`beyond_trail_noise`) — read this row on its own',
    'not_measured': 'not measured: no run after the fix reached this check',
    'not_yet_shown': 'not yet shown: {why}',
    'intermittent': 'intermittent: checked only for not getting worse',
}
FOOTNOTE = ('_Likely fixed means it did not come back after the fix often enough to say so with 80% confidence; '
            'its recorded replay keeps running in later suite runs._')
HEADER = '| # | Fix | Applied | Verified | Trail | Evidence |'
RULE = '|---|---|---|---|---|---|'
SECTION = '## Verification — runs/{run}'

_FIX_HEADING = re.compile(r'^##\s+(?:issue\s+)?#?(?P<number>\d+)[.):]?\s+(?P<title>.+?)(?:\s+[·|]\s+.*)?$', re.I)
_PLAN_HEADER = re.compile(r'^#\s*Fix plan\s*[—-]+\s*run\s+(?P<run>\S+)(?P<verify>\s*(?:→|->)\s*verify run\s+\S+)?')
_TRIAL = re.compile(r'^trial_\d+/')
_VERIFY_SECTION = re.compile(r'^## Verification\b.*?(?=^## |\Z)', re.M | re.S)
_REGRESSION_LINE = re.compile(r'^\s*-\s*\*\*Regression:\*\*(?P<rest>.*)$')
_REGRESSION_FOLDER = re.compile(r'`(regression_[\w-]+)`')
_FINDING_LINE = re.compile(r'^\s*-\s*\*\*Finding:\*\*(?P<rest>.*)$')
_FINDING_ID = re.compile(r'`(agent\.[\w.:-]+)`')


class Refused(Exception):
    """Input this script will not write from; the CLI exits 2 with the message."""


def parse_pin(text: str) -> tuple[str, str | None]:
    """`(scenario, policy or None)` from `scenario/policy`, `scenario:policy` or a bare scenario."""
    text = _TRIAL.sub('', text.strip())
    sep = ':' if ':' in text else '/'
    scenario, _, policy = text.rpartition(sep) if sep in text else (text, '', '')
    scenario, policy = scenario.strip(), policy.strip()
    if not scenario:
        raise Refused(f'pin {text!r} names no scenario')
    return scenario, policy or None


def parse_fix(text: str) -> tuple[int, list[tuple[str, str | None]]]:
    number, sep, pins = text.partition('=')
    if not sep or not number.strip().isdigit() or not pins.strip():
        raise Refused(f'--fix {text!r} is not "N=scenario/policy[,scenario/policy…]"')
    return int(number), [parse_pin(pin) for pin in pins.split(',') if pin.strip()]


def _load(path: Path, what: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{what} {path.name} is not readable JSON ({type(exc).__name__}) — write it with --json --out') from exc
    if not isinstance(doc, dict):
        raise Refused(f'{what} {path.name} is not a JSON object')
    return doc


def fix_titles(text: str) -> dict[int, str]:
    """`{number: title}` for every `## N. <title> · …` issue heading of a fix plan."""
    titles: dict[int, str] = {}
    for line in text.splitlines():
        match = _FIX_HEADING.match(line.strip())
        if match:
            titles.setdefault(int(match.group('number')), match.group('title').strip())
    return titles


def fix_replays(text: str) -> dict[int, str]:
    """`{number: folder}` — the first `regression_<slug>` on each issue's `- **Regression:**` line."""
    replays: dict[int, str] = {}
    number = None
    for line in text.splitlines():
        heading = _FIX_HEADING.match(line.strip())
        if heading or line.startswith('## '):
            number = int(heading.group('number')) if heading else None
            continue
        match = _REGRESSION_LINE.match(line)
        folder = _REGRESSION_FOLDER.search(match.group('rest')) if match and number is not None else None
        if folder and number is not None:
            replays.setdefault(number, folder.group(1))
    return replays


def replay_reading(compare: dict, folder: str | None) -> tuple[dict | None, str | None]:
    """`(row, None)` — the folder's regression row when both sides are the run's own replays of one instrument, or when
    the replay caught it after the fix however each side was played — else `(None, why)`."""
    if folder is None:
        return None, None
    row = next((r for r in compare.get('regressions') or [] if isinstance(r, dict) and r.get('folder') == folder), None)
    before, after = (row or {}).get('before') or {}, (row or {}).get('after') or {}
    if row is None or before.get('source') != 'replays':
        return None, 'no replay of it before the fix'
    if not after:
        return None, 'not replayed after the fix'
    # Played another way, a clean after says nothing against the before; a catch after the fix is a catch however it was played.
    if any(before.get(key) != after.get(key) for key in ('clear_axis', 'replay_mode')) and not int(after.get('failed') or 0):
        return None, 'its replay before the fix was played another way, so the two are not compared'
    return row, None


def fix_findings(text: str) -> dict[int, list[str]]:
    """`{number: [finding ids]}` — the `agent.<id>`s on each issue's `- **Finding:**` line; `[]` for an issue with none
    (one this diagnosis found, with no finding behind it)."""
    found: dict[int, list[str]] = {}
    number = None
    for line in text.splitlines():
        heading = _FIX_HEADING.match(line.strip())
        if heading or line.startswith('## '):
            number = int(heading.group('number')) if heading else None
            if number is not None:
                found.setdefault(number, [])
            continue
        match = _FINDING_LINE.match(line)
        if match and number is not None:
            found[number] += [i for i in _FINDING_ID.findall(match.group('rest')) if i not in found[number]]
    return found


def issue_counts(text: str, workdir: Path, *, handled: bool = False) -> tuple[str, list[str]]:
    """`(the fix plan's count, warnings)`. The count is `scored_findings.count_line` — the page's tiers, the one count the
    suite hand-off says too — plus the plan's issues with no `Finding:` line. A warning names each confirmed finding the
    page shows that no issue carries, and each `Finding:` id the page does not show as confirmed (warn only: the count
    stands on the page). ``handled``: the fixes anchor's count also says the findings the scored run showed handled."""
    import scored_findings  # noqa: PLC0415 - sibling, stdlib on this path: THE page tiers and their count

    tiers = scored_findings.page_counts(workdir)
    issues = fix_findings(text)
    folds = scored_findings.page_folds(workdir)
    named = {folds.get(fid, fid) for ids in issues.values() for fid in ids}
    shown = {f['id'] for tier in scored_findings.TIERS[:2] for f in tiers[tier]}
    warnings = []
    if missing := sorted(shown - named):
        warnings.append(f'no issue of the plan carries {", ".join(missing)}, which the page shows as confirmed')
    if extra := sorted(named - shown):
        warnings.append(f'the plan names {", ".join(extra)}, which the page does not show as confirmed')
    return scored_findings.count_line(tiers, sum(1 for ids in issues.values() if not ids), handled=handled), warnings


def _cell(text: str) -> str:
    return ' '.join(text.replace('|', '/').split())


def _pin_name(scenario: str, policy: str | None) -> str:
    return f'`{scenario}` · `{policy or "goal"}`'


def _counts(side: dict) -> tuple[int, int]:
    return int(side.get('failed') or 0), int(side.get('measured') or 0)


def _short(row: dict) -> int | None:
    """The clean runs a `not_yet_shown` reading is short by, or None when no count says."""
    needed = row.get('replays_needed')
    return needed if isinstance(needed, int) and not isinstance(needed, bool) and needed > 0 else None


def reading_text(row: dict, unit: str = 'runs') -> str:
    """One reading's plain label and its counts — what the Evidence cell says of a pin or a replay."""
    (b, b_of), (a, a_of) = _counts(row.get('before') or {}), _counts(row.get('after') or {})
    state = row.get('verdict')
    if state == FIXED:
        label = 'Fixed' if row.get('level') == LEVEL_PUBLISHED else 'Likely fixed'
        return f'{label}: it failed in {b} of {b_of} {unit} before the fix and in none of {a_of} after'
    if state == STILL_CAUGHT:
        return f'Still failing: it came back in {a} of {a_of} {unit} after the fix ({b} of {b_of} before)'
    if state == INTERMITTENT:
        return (f'Intermittent: it failed in {b} of {b_of} {unit} before the fix and in none of {a_of} after. It fails too rarely '
                'to show a drop in one check, so this only shows it did not get worse')
    if a_of == 0:
        return f'Not measured: no run after the fix reached this check ({b} of {b_of} {unit} before)'
    if (short := _short(row)) is not None:
        return (f'Not yet shown: none of {a_of} {unit} after the fix failed so far; {short} more clean {unit} are needed to show '
                f'it is fixed ({b} of {b_of} before)')
    return f'Not yet shown: {row.get("why") or "not decided"} ({b} of {b_of} {unit} before, {a} of {a_of} after)'


def row_for(number: int, title: str, pins: list[tuple[str, str | None]], trails: dict[str, str], verdicts: dict[tuple[str, str | None], dict],
            replay: dict | None = None, replay_why: str | None = None) -> dict:
    """One fix's row: `{number, title, verified, trail, evidence}` — `verified` opens with ✅, ❌ or —. `replay` is
    the fix's own replay reading (:func:`replay_reading`); it decides only a fix whose unfixed pins are intermittent."""
    scenario_labels = {scenario: trails.get(scenario) if trails.get(scenario) in _RANK else NOT_COMPARED for scenario, _ in pins}
    worst = max(scenario_labels.values(), key=_RANK.__getitem__)
    rows = [verdicts.get(pin) for pin in pins]
    states = [str(row.get('verdict')) if row else None for row in rows]
    evidence = [f'{_pin_name(*pin)}: ' + (reading_text(row) if row else 'Not measured: no verdict') for pin, row in zip(pins, rows)]
    leaning = INTERMITTENT in states and all(state in (FIXED, INTERMITTENT) for state in states)
    if replay is not None:
        where = 'replayed whole' if (replay.get('before') or {}).get('clear_axis') == 'anywhere' else 'at the recorded turn where it failed'
        evidence.append(f'its recorded replay `{replay["folder"]}`, {where}: ' + reading_text(replay, 'replays'))
    elif leaning and replay_why:
        evidence.append(f'its recorded replay: {replay_why}')
    replay_state = (replay or {}).get('verdict')
    readings = [row for row, state in zip(rows, states) if state == FIXED] + ([replay] if replay is not None and leaning else [])
    if (STILL_CAUGHT in states or replay_state == STILL_CAUGHT) and worst in QUIET:
        verified = '❌ still failing'
    elif states and worst in QUIET and (all(state == FIXED for state in states) or (leaning and replay_state == FIXED)):
        decided_at_95 = all(row is not None and row.get('level') == LEVEL_PUBLISHED for row in readings)
        verified = '✅ fixed' if decided_at_95 else '✅ likely fixed (weak signal)'
    elif None in states:
        verified = '— ' + REASONS['no_verdict'].format(pins=', '.join(_pin_name(*pin) for pin, s in zip(pins, states) if s is None))
    elif worst == NOT_COMPARED:
        verified = '— ' + REASONS['not_compared'].format(scenarios=', '.join(f'`{s}`' for s, label in scenario_labels.items() if label == NOT_COMPARED))
    elif worst == LABEL_UNKNOWN:
        verified = '— ' + REASONS['unknown']
    elif worst == LABEL_BEYOND:
        verified = '— ' + REASONS['beyond']
    elif any(row and row.get('why') == NO_AFTER for row in rows):
        verified = '— ' + REASONS['not_measured']
    elif NOT_YET_SHOWN in states:
        shorts = [n for row in rows if row and row.get('verdict') == NOT_YET_SHOWN and (n := _short(row)) is not None]
        why = (f'{max(shorts)} more clean runs needed' if shorts
               else next(str(row.get('why') or 'not decided') for row in rows if row and row.get('verdict') == NOT_YET_SHOWN))
        verified = '— ' + REASONS['not_yet_shown'].format(why=why)
    else:
        verified = '— ' + REASONS['intermittent']
    return {'number': number, 'title': title, 'verified': verified, 'trail': worst, 'evidence': '; '.join(evidence)}


def render_table(run: str, rows: list[dict]) -> str:
    lines = [SECTION.format(run=run), '', HEADER, RULE]
    for row in rows:
        lines.append(f'| {row["number"]} | {_cell(row["title"])} | yes | {_cell(row["verified"])} | `{row["trail"]}` | {_cell(row["evidence"])} |')
    if any('likely fixed' in row['verified'] for row in rows):
        lines += ['', FOOTNOTE]
    return '\n'.join(lines) + '\n'


def place_section(text: str, section: str) -> str:
    """`text` with its `## Verification` section replaced by `section`, or `section` appended."""
    if _VERIFY_SECTION.search(text):
        return _VERIFY_SECTION.sub(lambda _: section + '\n', text, count=1).rstrip('\n') + '\n'
    return text.rstrip('\n') + '\n\n' + section


def table(args: argparse.Namespace) -> str:
    text = args.fixes.read_text(encoding='utf-8')
    compare = _load(args.compare, 'the comparison')
    if not isinstance(compare.get('scenarios'), list):
        raise Refused(f'the comparison {args.compare.name} has no `scenarios` list — write it with compare_runs.py --json --out')
    trails = {str(row.get('id')): str(row.get('trail_label')) for row in compare['scenarios'] if isinstance(row, dict) and row.get('id')}
    verdicts: dict[tuple[str, str | None], dict] = {}
    if args.verdict is not None:
        doc = _load(args.verdict, 'the k-of-K verdict')
        if doc.get('schema') != VERDICT_SCHEMA or not isinstance(doc.get('pins'), list):
            raise Refused(f'the k-of-K verdict {args.verdict.name} is not `{VERDICT_SCHEMA}` — write it with verify_exposing.py verdict --json --out')
        verdicts = {(_TRIAL.sub('', str(p.get('scenario'))), p.get('policy') or None): p for p in doc['pins'] if isinstance(p, dict)}
    titles = fix_titles(text)
    fixes = [parse_fix(item) for item in args.fix]
    unknown = [n for n, _ in fixes if n not in titles]
    if unknown:
        raise Refused(f'fix number(s) {unknown} have no `## N. <title>` issue heading in {args.fixes.name}')
    run = args.verify_run or Path(str(compare.get('after') or '')).parent.name
    if not run or not re.match(r'^[A-Za-z0-9][\w.-]*$', run):
        raise Refused('the verify run is not named: pass --verify-run <run folder>')
    folders = fix_replays(text)
    rows = [row_for(n, titles[n], pins, trails, verdicts, *replay_reading(compare, folders.get(n))) for n, pins in fixes]
    args.fixes.write_text(place_section(text, render_table(run, rows)), encoding='utf-8')
    labels = {'fixed': '✅', 'still failing': '❌', 'not yet shown': '— not yet shown', 'intermittent': '— intermittent'}
    tally = {label: sum(1 for row in rows if row['verified'].startswith(prefix)) for label, prefix in labels.items()}
    tally['not measured or other'] = len(rows) - sum(tally.values())
    return f'fix_verification: wrote {SECTION.format(run=run)} — {len(rows)} fix(es): ' + ', '.join(f'{n} {label}' for label, n in tally.items())


def _count(totals: dict, key: str) -> int | None:
    value = totals.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def counts(totals: dict, passes: str | None = None) -> str:
    """The header's parenthesised counts from `run.json` `totals`; `passes` (the run's pass line, :func:`pass_line`) leads
    in place of the goals count when the run carries one."""
    achieved, measurable = _count(totals, 'goals_achieved'), _count(totals, 'goals_measurable')
    if passes is None and (achieved is None or measurable is None):
        raise Refused('run.json `totals` carries no `goals_achieved` / `goals_measurable` — rebuild it with build_run_json.py')
    parts = [passes or f'{achieved} of {measurable} goals met']
    critical, critical_of = _count(totals, 'scenarios_with_critical_failures'), _count(totals, 'critical_reported')
    if critical and critical_of:
        parts.append(f'{critical} of {critical_of} scenarios failed a critical check')
    high, high_of, unreported = (_count(totals, k) for k in ('scenarios_with_high_severity_failures', 'high_severity_reported', 'high_severity_unreported'))
    high_part = [f'{high or 0} of {high_of} scenarios failed a required check'] if high_of else []
    if unreported:
        high_part.append(f'required checks not reported for {unreported} scenario{"" if unreported == 1 else "s"}')
    if high_part:
        parts.append(', '.join(high_part))
    return ' · '.join(parts)


#: The SDK's scores block (`run.json` → `scores`, copied from `run_summary.json` by build_run_json) and the conversation
#: fail reasons that name a failed check (a critical one, or a failing-tier one — the backend's high-severity reds).
SCORES_KEY, SCORES_SCHEMA = 'scores', 'scores-v1'
CHECK_FAIL_REASONS = frozenset({'critical_failure', 'failing_tier'})


def check_failed_scenarios(scores: object) -> set[str] | None:
    """The scenarios the scores block says FAILED on a critical or failing-tier check on some trial (`result: fail`, a
    reason in :data:`CHECK_FAIL_REASONS`) — read, never recomputed. None when there is no block this version reads."""
    if not isinstance(scores, dict) or scores.get('schema') != SCORES_SCHEMA or not isinstance(scores.get('scenarios'), dict):
        return None
    return {str(sid) for sid, entry in scores['scenarios'].items()
            if isinstance(entry, dict) and entry.get('result') == 'fail' and CHECK_FAIL_REASONS & set(entry.get('fail_reasons') or [])}


def pass_line(doc: dict) -> str | None:
    """THE pass line every surface pastes (`run_numbers.pass_line`), or None for a run.json with no C7 (an older run)."""
    import run_numbers  # noqa: PLC0415 - sibling script

    try:
        return run_numbers.pass_line(doc)
    except run_numbers.C7Missing:
        return None


def trial_counts(block: object, scores: object = None, passes: str | None = None) -> str | None:
    """The header's counts over EVERY trial, from run.json `trials` (`run_summary.json`'s block, refolded for
    rescores): the run's pass line (`passes`, :func:`pass_line`) — or, for a run.json with none,
    `trials.run.goal_all_trials`, the scenario-verdict axis — and, from the run's scores block
    (:func:`check_failed_scenarios`), the scenarios that failed a critical or required check on some trial; said
    unknown when the run carries no block. None for a one-trial run or a withdrawn `trials.run`: the totals stand."""
    if not isinstance(block, dict) or not isinstance(block.get('run'), dict):
        return None
    n, run = _count(block, 'n'), block['run']
    goal = run.get('goal_all_trials')
    if n is None or n < 2 or not isinstance(goal, dict):
        return None
    passed, of, unmeasured, scenarios = _count(goal, 'passed'), _count(goal, 'of'), _count(goal, 'unmeasured'), _count(run, 'scenarios')
    if passed is None or of is None or scenarios is None:
        return None
    failed = check_failed_scenarios(scores)
    first = passes or f'{passed} of {of} passed on every valid trial' + (f' ({unmeasured} without a measurable goal)' if unmeasured else '')
    checks = (f'{len(failed)} of {scenarios} scenarios failed a critical or required check on some trial' if failed is not None
              else 'critical or required-check failures unknown (the run carries no scores block)')
    return ' · '.join([first, checks, f'{n} trials'])


_SYMPTOM_LINE = re.compile(r'^([ \t]*-[ \t]*\*\*Symptom:\*\*)[^\n]*(?:\n[ \t]+\S[^\n]*)*', re.M)


def seeded_symptoms(text: str, run_dir: Path) -> tuple[str, str | None]:
    """`(text, a note for stderr or None)`: each seeded issue's Symptom line written from the scored run's cells
    (`scored_findings.symptom`) — only when `run_dir` IS the scored run, as a Symptom names the plan's run's scenarios.
    Every line left as written is named in the note. Never raises: anything unreadable leaves every line as written."""
    sections = re.split(r'(?m)^(?=## )', text)
    # Each section's first `Finding:` id, by the plan's one Finding-line reader (:func:`fix_findings`).
    seeded = [next((ids[0] for ids in fix_findings(s).values() if ids), None) for s in sections]
    if not any(seeded):
        return text, None
    try:
        import record_finding  # noqa: PLC0415 - siblings: the findings store and its scored-run reading
        import scored_findings  # noqa: PLC0415

        workdir = run_dir.resolve().parents[2]  # <workdir>/artifacts/runs/<run>
        scored = scored_findings.scored_run_dir(workdir)
        if scored is None or scored.resolve() != run_dir.resolve():
            return text, f'Symptom lines left as written: runs/{run_dir.name} is not the scored run ({scored.name if scored else "none"})'
        rows = record_finding.read_rows(workdir / 'artifacts' / scored_findings.FINDINGS_ROWS)
        found = scored_findings.project(rows, run_dir)
        kept = []
        for n, (section, fid) in enumerate(zip(sections, seeded)):
            line = scored_findings.symptom(found, fid, rows) if fid else None
            if line and _SYMPTOM_LINE.search(section):
                sections[n] = _SYMPTOM_LINE.sub(lambda m, line=line: f'{m.group(1)} {line}', section, count=1)
            elif fid:
                kept.append(fid)
    except Exception as exc:  # noqa: BLE001 - the counts are written either way; this rewrite never blocks them
        return text, f'Symptom lines left as written ({type(exc).__name__}: {exc})'
    note = (f'Symptom lines left as written for {", ".join(kept)}: runs/{run_dir.name} does not read them (no reader covered them, '
            'or no Symptom line)') if kept else None
    return ''.join(sections), note


def header(args: argparse.Namespace) -> str:
    text = args.fixes.read_text(encoding='utf-8')
    run_path = args.run / 'run.json' if args.run.is_dir() else args.run
    doc = _load(run_path, 'the run')
    totals = doc.get('totals')
    if not isinstance(totals, dict):
        raise Refused(f'{run_path.name} has no `totals`')
    names = {str(doc.get('run_name') or ''), run_path.parent.name} - {''}
    passes = pass_line(doc)
    line = f'({trial_counts(doc.get("trials"), doc.get(SCORES_KEY), passes) or counts(totals, passes)})'
    lines = text.splitlines(keepends=True)
    index = next((i for i, raw in enumerate(lines) if raw.strip()), None)
    match = _PLAN_HEADER.match(lines[index].strip()) if index is not None else None
    if match:
        if match.group('run') not in names:
            raise Refused(f'the plan is for run {match.group("run")!r}, and {run_path} is run {sorted(names)} — pass that run\'s run.json')
        lines[index] = f'# Fix plan — run {match.group("run")}{match.group("verify") or ""}  {line}\n'
    else:
        run = str(doc.get('run_name') or run_path.parent.name)
        lines.insert(0, f'# Fix plan — run {run}  {line}\n\n')
    text, note = seeded_symptoms(''.join(lines), run_path.parent)
    if note:
        print(f'fix_verification: {note}', file=sys.stderr)
    args.fixes.write_text(text, encoding='utf-8')
    return f'fix_verification: header counts {line}'


def counts_cli(args: argparse.Namespace) -> str:
    line, warnings = issue_counts(args.fixes.read_text(encoding='utf-8'), args.workdir, handled=True)
    for warning in warnings:
        print(f'WARNING: {warning}', file=sys.stderr)
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p_table = sub.add_parser('table', help='write the ## Verification table')
    p_table.add_argument('fixes', type=Path)
    p_table.add_argument('--compare', type=Path, required=True, help='compare_runs.py --json output (before → verify run)')
    p_table.add_argument('--verdict', type=Path, help='verify_exposing.py verdict --json output (each pin k of K)')
    p_table.add_argument('--fix', action='append', required=True, help='N=scenario/policy[,scenario/policy…] — one per accepted fix')
    p_table.add_argument('--verify-run', help='the verify run folder name (default: the comparison\'s after run)')
    p_table.set_defaults(func=table)
    p_header = sub.add_parser('header', help='rewrite the plan header\'s counts from run.json totals')
    p_header.add_argument('fixes', type=Path)
    p_header.add_argument('--run', type=Path, required=True, help='the planned run\'s run.json (or its folder)')
    p_header.set_defaults(func=header)
    p_counts = sub.add_parser('counts', help='the fix plan\'s count for the fixes anchor, from the page\'s tiers')
    p_counts.add_argument('fixes', type=Path)
    p_counts.add_argument('--workdir', type=Path, required=True)
    p_counts.set_defaults(func=counts_cli)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if not args.fixes.is_file():
            raise Refused(f'{args.fixes} does not exist — write the fix plan first')
        print(args.func(args))
    except Refused as exc:
        print(f'fix_verification: {exc}', file=sys.stderr)
        return 2
    except OSError as exc:
        print(f'fix_verification: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
