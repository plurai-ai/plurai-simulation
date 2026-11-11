#!/usr/bin/env python3
"""The run's customer numbers, read off the run summary (C7) and nothing else.

C7 is `run.json` `totals`: `valid_trials`, `invalid_trials`, `passed_valid` and `invalid_by_cause`
(`ours_infra`, `harness_fault`, `provider_infra`, `agent_crash`) beside the counts it always carried. The
headline is over the VALID trials. An invalid trial is said by whose side it failed on: ours (`ours_infra`,
`harness_fault`: "could not run on our side"), the agent's model provider (`provider_infra`: a rate
limit or outage — "stopped on the agent's model provider, rerun once it recovers"; an SDK before 0.28.12
writes no such key and reads as 0) or the agent's (`agent_crash`: its process exited on its own — "could not
start on the agent's side"). An agent that RAISES
inside its turn is a valid trial that failed. Every customer surface (the chat, analytics, the banners,
the report page) reads its numbers through this module, so no renderer recounts rows.

    run_numbers.py chat <run.json>    # the chat hand-off's headline, Scenarios, Run integrity, Agent and Suite lines, as JSON
    run_numbers.py covered <run.json> # the complications the run's conversations put to the agent (JSON), null when unknown

The Scenarios line counts the scenarios of a multi-trial run by the SDK's goal label (`goal.label` of each
`trials.scenarios` row): reliable (`consistent_pass`), flaky (`inconsistent`), broken (`consistent_fail`) and unmeasured
(`insufficient`, `not_applicable`), the same buckets the report's Verdict section names, over the same standing
scenarios (a rescore-withdrawn row is out), plus `unlabelled` for a row with no known label so the buckets sum to the total.
Null on a single-trial run and wherever the Verdict withholds the buckets: `trials.run.withdrawn`, or
`trials.stability_comparable` false.

The Suite line says the run's own rung (`release_verdict.json` beside `run.json`) when it differs from the rung the suite
hand-off landed on (`suite_anchor.json`, two levels up). Two gates read two runs, so the rung can move: the suite's
coverage audit judged the suite before this run, and this run's release gate judged the suite on what it saw. Null when the
two agree, or either is unknown.

The Agent line (`Agent: Ship / Don't ship / No verdict`) is the SDK's verdict on the agent, said by
`release_verdict.agent_line` off the scores block `run.json` carries (`scores`); null when it carries none.

THE pass line (:func:`pass_line`) is the one pass count every surface pastes — the chat headline, the closing, the
fixes.md header and the suite hand-off: `N of M scenarios passed · K of T trials passed`, the scenario pair off the SDK's
scores block (`scores.scenarios[*].result`, the unit the Agent verdict and the report page count) and the conversation
pair off C7. A single-trial run says one clause (the two count the same conversations); a run with no scores block
says the C7 clause alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: The C7 fields every customer number is read from.
C7_FIELDS = ('valid_trials', 'invalid_trials', 'passed_valid', 'invalid_by_cause')
#: The typed causes of an invalid trial: `invalid_trials` is their sum.
INVALID_CAUSES = ('ours_infra', 'harness_fault', 'provider_infra', 'agent_crash')
#: The causes that make a trial one that could not run on our side; `agent_crash` is the agent's side.
OURS = ('ours_infra', 'harness_fault')


class C7Missing(ValueError):
    """The run summary carries no (or a malformed) C7 block: nothing customer-facing may be counted from it."""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def c7(run: dict) -> dict:
    """The C7 counts of `run` (a parsed `run.json`), validated: ints, and the causes typed. Raises :class:`C7Missing`."""
    totals = run.get('totals') if isinstance(run, dict) else None
    if not isinstance(totals, dict):
        raise C7Missing('run.json carries no totals')
    missing = [k for k in C7_FIELDS if k not in totals]
    if missing:
        raise C7Missing(f'run.json totals carry no {", ".join(missing)} (C7)')
    out = {k: _int(totals[k]) for k in ('valid_trials', 'invalid_trials', 'passed_valid')}
    causes = totals['invalid_by_cause']
    if any(v is None for v in out.values()) or not isinstance(causes, dict):
        raise C7Missing('run.json C7 counts are not non-negative integers')
    by_cause = {k: _int(causes.get(k, 0)) for k in INVALID_CAUSES}
    if any(v is None for v in by_cause.values()):
        raise C7Missing('run.json totals.invalid_by_cause counts are not non-negative integers')
    return {
        **out,
        'invalid_by_cause': by_cause,
        'could_not_run': sum(by_cause[k] for k in OURS),
        'provider_outage': by_cause['provider_infra'],
        'agent_side': by_cause['agent_crash'],
    }


def trials_each(run: dict) -> int:
    """How many trials each scenario drew (1 on a single-trial run)."""
    n = _int((run.get('totals') or {}).get('trials'))
    return n if n else 1


def noun(run: dict) -> str:
    """What the headline counts: trials on a multi-trial run, scenarios on a single-trial one."""
    return 'trials' if trials_each(run) > 1 else 'scenarios'


def drawn(run: dict) -> int:
    """Every trial the run drew: the valid ones and the ones that could not run."""
    counts = c7(run)
    return counts['valid_trials'] + counts['invalid_trials']


#: The pass line of a run that decided nothing: said plainly, beside the run's own crash and could-not-run lines.
NOTHING_DECIDED = 'no conversation reached a pass or fail'


def scenario_passes(run: dict) -> tuple[int, int] | None:
    """`(passed, scored)` over the SDK scores block's scenarios (`result` `pass` / not `not_scored`); None without one."""
    scores = run.get('scores') if isinstance(run, dict) else None
    rows = scores.get('scenarios') if isinstance(scores, dict) else None
    if not isinstance(rows, dict):
        return None
    results = [row.get('result') for row in rows.values() if isinstance(row, dict)]
    return sum(r == 'pass' for r in results), sum(r not in (None, 'not_scored') for r in results)


def pass_line(run: dict) -> str:
    """THE pass count (module docstring): `N of M scenarios passed · K of T trials passed`, one clause on a single-trial
    run or without a scores block. Raises :class:`C7Missing` without C7."""
    counts = c7(run)
    if not counts['valid_trials']:  # nothing was decided (every trial lost or crashed): no "0 of 0" pass count
        return NOTHING_DECIDED
    conversations = f'{counts["passed_valid"]} of {counts["valid_trials"]} {noun(run)} passed'
    scenarios = scenario_passes(run)
    if scenarios is None or trials_each(run) == 1:
        return conversations
    return f'{scenarios[0]} of {scenarios[1]} scenarios passed · {conversations}'


def headline(run: dict) -> str:
    """The `**Run complete** — …` line: scenarios, the pass line (:func:`pass_line`), then the critical / high-severity counts."""
    counts, totals = c7(run), run['totals']
    # The SDK's own `totals.scenarios` is the per-scenario trial map; the plugin's is the count.
    scenarios = len(totals['scenarios']) if isinstance(totals.get('scenarios'), dict) else _int(totals.get('scenarios')) or 0
    each = trials_each(run)
    scope = f'{scenarios} scenarios ({each} trials each)' if each > 1 else f'{scenarios} scenarios'
    passes = pass_line(run)
    parts = [passes if passes == NOTHING_DECIDED else f'✅ {passes}']
    if critical := _int(totals.get('scenarios_with_critical_failures')):
        parts.append(f'❌ {critical} of {_int(totals.get("critical_reported")) or counts["valid_trials"]} failed a critical check')
    # An unserved high-severity list is said as not reported, never folded into a zero.
    unreported = _int(totals.get('high_severity_unreported'))
    if high := _int(totals.get('scenarios_with_high_severity_failures')):
        parts.append(f'⚠ {high} of {_int(totals.get("high_severity_reported")) or counts["valid_trials"]} failed a high-severity check'
                     + (f' (not reported for {unreported} more)' if unreported else ''))
    elif unreported:
        parts.append(f'⚠ high-severity checks not reported for {unreported} {noun(run)}')
    return f'**Run complete** — {scope}: ' + ' · '.join(parts) + '.'


def invalid_parts(run: dict) -> list[str]:
    """What did not run, by whose side, each "N of M <noun> …" — `[]` when every trial ran."""
    counts, of = c7(run), f'{drawn(run)} {noun(run)}'
    return ([f'{counts["could_not_run"]} of {of} could not run on our side'] if counts['could_not_run'] else []) + \
        ([f"{counts['provider_outage']} of {of} stopped on the agent's model provider (a rate limit or outage), rerun them once it recovers"]
         if counts['provider_outage'] else []) + \
        ([f'{counts["agent_side"]} of {of} could not start on the agent\'s side (its process exited)']
         if counts['agent_side'] else [])


def user_sim_part(run: dict, run_dir: Path | None = None) -> str | None:
    """"the simulated user broke character in K of N conversations" when some trial's measurement is unclean on the
    simulated user (`user_sim_integrity` in `run_integrity.measurement_unclean_because`, or in any trial's `because`
    of `trials.measurement`), counted by :func:`user_sim_breaks`; None otherwise, and None when no break is counted."""
    block = run.get('run_integrity') if isinstance(run.get('run_integrity'), dict) else {}
    trials = run.get('trials') if isinstance(run.get('trials'), dict) else {}
    because = [*(block.get('measurement_unclean_because') or [])]
    because += [c for t in trials.get('measurement') or [] if isinstance(t, dict) for c in t.get('because') or []]
    if 'user_sim_integrity' not in because:
        return None
    broke, measured = user_sim_breaks(run, run_dir)
    return f'the simulated user broke character in {broke} of {measured} conversations' if 0 < broke <= measured else None


#: The undelivered-beat reason of a beat correctly withheld: its contingency never came up (the closed ledger vocabulary).
WITHHELD = 'contingent_never_met'


def _soft_turns(conv_dir: Path) -> set[str] | None:
    """The anomaly turns of one conversation that are no break: turns judged only `evaluator_voice` that the Plurai backend typed as
    padding (`padded_turns`), or that are the SDK's closing row (`turns[-1].closing_turn`, no `ended_early`, no beat
    delivered on it) of a plan whose every undelivered beat was withheld (:data:`WITHHELD`). ``None`` when the
    transcript cannot be read."""
    doc = _json_object(conv_dir / 'transcript.json')
    record = doc.get('finalize_record') if doc is not None else None
    sim = record.get('user_sim_integrity') if isinstance(record, dict) else None
    if not isinstance(sim, dict) or not isinstance(sim.get('anomalies'), dict):
        return None
    padded = {str(t) for t in sim.get('padded_turns') or [] if _int(t) is not None}
    outcome = record.get('dialog_outcome')
    turns = doc.get('turns')
    last = turns[-1] if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else {}
    beats = sim.get('undelivered_beats')
    withheld = isinstance(beats, dict) and all(isinstance(b, dict) and b.get('reason') == WITHHELD for b in beats.values())
    delivered = sim.get('delivered_beats') if isinstance(sim.get('delivered_beats'), dict) else {}
    scripted = any(isinstance(b, dict) and b.get('turn') == last.get('turn_idx') for b in delivered.values())
    closing = (str(last['turn_idx']) if last.get('closing_turn') is True and _int(last.get('turn_idx')) is not None and withheld
               and not scripted and isinstance(outcome, dict) and outcome.get('ended_early') is None else None)
    return {key for key, kinds in sim['anomalies'].items()
            if isinstance(kinds, dict) and set(kinds) == {'evaluator_voice'} and (key in padded or key == closing)}


def user_sim_breaks(run: dict, run_dir: Path | None = None) -> tuple[int, int]:
    """``(broke, measured)`` over every trial's `user_sim_integrity` (`run` itself, then each other `trials.dirs` entry's
    `run.json` in `run_dir`): a conversation broke character when one of its `anomaly_turns` is not soft (:func:`_soft_turns`,
    read off `run_dir/[<trial dir>/]<conversation>/transcript.json`). A trial whose transcripts cannot all be read counts
    every conversation with an anomaly turn, as does every trial when `run_dir` is None."""
    trials = run.get('trials') if isinstance(run.get('trials'), dict) else {}
    dirs = [d for d in trials.get('dirs') or [] if isinstance(d, str) and d] if run_dir is not None else []
    runs = [(run, run_dir), *((_json_object(run_dir / d / 'run.json') or {}, run_dir / d) for d in dirs)] if run_dir is not None else [(run, None)]
    broke = measured = 0
    for one, here in runs:
        sim = one.get('user_sim_integrity') if isinstance(one.get('user_sim_integrity'), dict) else {}
        flagged = sim['anomaly_turns'] if isinstance(sim.get('anomaly_turns'), dict) else {}
        soft = {conv: _soft_turns(here / conv) for conv in flagged} if here is not None else {}
        if here is None or any(v is None for v in soft.values()):
            broke += len(flagged)
        else:
            broke += sum(1 for conv, keys in flagged.items() if not isinstance(keys, list) or {str(k) for k in keys} - soft[conv])
        measured += _int(sim.get('measured')) or 0
    return broke, measured


def integrity_line(run: dict, run_dir: Path | None = None) -> str | None:
    """The `Run integrity` line for trials that did not run (either side) and a simulated user that broke character
    (:func:`user_sim_part`), or None when neither applies."""
    parts = invalid_parts(run)
    sim = user_sim_part(run, run_dir)
    if not parts:
        return f'Run integrity — {sim}.' if sim else None
    return f'Run integrity — {"; ".join([*parts, *([sim] if sim else [])])}. The results above cover the {c7(run)["valid_trials"]} that ran.'


def page_rows(run: dict) -> dict:
    """`run` without the rows the vendored page cannot type, a copy (`run` is not touched). The page parses a run.json
    outside `scenarios`/`evals` as ONE value, so a single row it rejects drops the whole file. Dropped: a `regressions`
    row with no `folder` (no folder on disk for that replay, hence no owner and no cell; the page joins a row to its
    kept regression by `scenario ?? folder` and requires `folder`). The run.json record keeps it."""
    out = dict(run)
    if isinstance(run.get('regressions'), list):
        out['regressions'] = [r for r in run['regressions'] if isinstance(r, dict) and isinstance(r.get('folder'), str)]
    return out


#: The goal labels each reliability bucket of the Scenarios line holds, in the order the line names them. The report's
#: Verdict section buckets the same labels (`render_run_report._GOAL_BUCKETS`).
GOAL_BUCKETS = (
    ('reliable', ('consistent_pass',)),
    ('flaky', ('inconsistent',)),
    ('broken', ('consistent_fail',)),
    ('unmeasured', ('insufficient', 'not_applicable')),
)


def standing_rows(rows: object) -> dict:
    """The scenario rows of `trials.scenarios` that stand: dict rows the rescore did not withdraw. The report's Verdict
    counts exactly these, and so does the Scenarios line."""
    return {sid: row for sid, row in sorted(rows.items()) if isinstance(row, dict) and not row.get('withdrawn')} if isinstance(rows, dict) else {}


def buckets_withheld(block: dict) -> bool:
    """True when the report's Verdict names no reliability bucket: the cross-trial counts were withdrawn after a rescore
    (`trials.run.withdrawn`), or the trials ran on each other's writes (`trials.stability_comparable` is false), where no
    bucket is a reading of the agent."""
    outcome = block.get('run') if isinstance(block.get('run'), dict) else {}
    return bool(outcome.get('withdrawn')) or block.get('stability_comparable') is False


def scenarios_line(run: dict) -> str | None:
    """`Scenarios — 1 reliable · 6 broken (of 7, 3 trials each)` off each standing scenario's `goal.label`. A scenario
    whose label is missing or outside :data:`GOAL_BUCKETS` is counted as `unlabelled`, so the buckets sum to the total.
    `None` on a single-trial run, one whose rows carry no label (an SDK before it), and wherever the report's Verdict
    withholds the buckets (:func:`buckets_withheld`)."""
    block = run.get('trials') if isinstance(run.get('trials'), dict) else None
    if block is None or trials_each(run) < 2 or buckets_withheld(block):
        return None
    rows = standing_rows(block.get('scenarios'))
    labels = [(row.get('goal') or {}).get('label') if isinstance(row.get('goal'), dict) else None for row in rows.values()]
    if not any(isinstance(label, str) for label in labels):
        return None
    known = {label for _, members in GOAL_BUCKETS for label in members}
    buckets = [(name, sum(1 for label in labels if label in members)) for name, members in GOAL_BUCKETS]
    buckets.append(('unlabelled', sum(1 for label in labels if label not in known)))
    parts = [f'{n} {name}' for name, n in buckets if n]
    return f'Scenarios — {" · ".join(parts)} (of {len(rows)}, {trials_each(run)} trials each)'


def _json_object(path: Path) -> dict | None:
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def suite_line(run_dir: Path) -> str | None:
    """The run's own `Suite:` rung, said beside the rung the suite hand-off landed on, when the two differ; else `None`."""
    verdict = _json_object(run_dir / 'release_verdict.json')
    landed = _json_object(run_dir.parent.parent / 'suite_anchor.json') if run_dir.parent.name == 'runs' else None
    rung, anchor = (verdict or {}).get('rung'), (landed or {}).get('anchor')
    if not (isinstance(rung, str) and rung and isinstance(anchor, str) and anchor):
        return None
    landed_on = anchor.strip('*')
    if rung == landed_on.partition(' (')[0]:  # `Suite: qualified (ungated set)` is the rung `Suite: qualified`, qualified
        return None
    return f"{rung} — this run's own release gate reads the suite differently from the suite hand-off ({landed_on})."


def _enactment_rows(run_dir: Path) -> list | None:
    """The backend's `detail.complication_enactment` rows for this run: its own `suite_diagnostics.json`, else the suite's
    `suite_diagnostics_final.json` when that report's `run_upload_id` is this run's (`red_cell_audit_upload.json`)."""
    own = _json_object(run_dir / 'suite_diagnostics.json')
    detail = (own or {}).get('detail')
    if not isinstance(detail, dict):
        receipt = _json_object(run_dir / 'red_cell_audit_upload.json') or {}
        final = _json_object(run_dir.parent.parent / 'suite_diagnostics_final.json') or {}
        detail = final.get('detail')
        if not (isinstance(detail, dict) and receipt.get('run_upload_id') and detail.get('run_upload_id') == receipt['run_upload_id']):
            return None
    rows = detail.get('complication_enactment')
    return rows if isinstance(rows, list) and rows else None


def covered(run_dir: Path) -> list[dict] | None:
    """The complications the run's conversations put to the agent: `[{complication, enacted, of}]`, one per complication
    enacted at least once (`enacted` summed over its scenarios, `of` every outcome counted for it), in first-seen order.
    None when the backend recorded no enactment for this run: nothing is known, so nothing is listed."""
    rows = _enactment_rows(run_dir)
    if rows is None:
        return None
    sums: dict[str, list[int]] = {}
    for row in rows:
        outcomes = row.get('outcomes') if isinstance(row, dict) else None
        name = row.get('complication') if isinstance(row, dict) else None
        if not (isinstance(name, str) and name and isinstance(outcomes, dict)):
            continue
        counts = [n for n in (_int(v) for v in outcomes.values()) if n is not None]
        total = sums.setdefault(name, [0, 0])
        total[0] += _int(outcomes.get('enacted')) or 0
        total[1] += sum(counts)
    return [{'complication': name, 'enacted': e, 'of': of} for name, (e, of) in sums.items() if e >= 1]


def setup_line(run: dict) -> str | None:
    """The `Real tools` line for tools the bench runs for real whose every call failed, each quoting its own error as the
    call streamed it, or None (`build_run_json` `setup_failed_tools`: `{tool, failed, calls, error}`). An error can be the
    system's own designed answer, so the line states both readings and prescribes a fix only for the other one."""
    rows = run.get('setup_failed_tools')
    rows = [e for e in rows if isinstance(e, dict) and e.get('tool')] if isinstance(rows, list) else []
    if not rows:
        return None
    parts = '; '.join(f'`{e["tool"]}` failed all {e.get("calls")} calls: "{str(e.get("error") or "")[:240]}"' for e in rows)
    return (f'Tools that run for real never succeeded in this run: {parts}. If an error is your system\'s own answer to '
            'what the conversation asked of it (a permission rule, an item that is not there), nothing needs fixing; '
            'otherwise fix what it names (a credential, a service to start) or mock it with `/simulation-scan-agent`, '
            'and run again.')


def chat_lines(run: dict, run_dir: Path | None = None) -> dict:
    """What the chat hand-off pastes verbatim: `{headline, passes, scenarios, run_integrity, agent, suite, setup}` (`passes` the
    pass line, :func:`pass_line`; `scenarios` null on a
    single-trial run, `run_integrity` and `setup` null when none applies, `agent` null when the run carries no scores block, `suite`
    null unless the run's rung differs from the suite hand-off's; `run_dir` is where the rung is read)."""
    import release_verdict  # noqa: PLC0415 - sibling script: the one place the Agent line is worded

    return {
        'headline': headline(run),
        'passes': pass_line(run),
        'scenarios': scenarios_line(run),
        'run_integrity': integrity_line(run, run_dir),
        'agent': release_verdict.agent_line(run.get('scores'), release_verdict.policy_display_names(release_verdict.run_workdir(run_dir)),
                                            stage=run.get('stage')),
        'suite': suite_line(run_dir) if run_dir is not None else None,
        'setup': setup_line(run),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    chat = sub.add_parser('chat', help="print the chat hand-off's headline and Run integrity lines (JSON)")
    chat.add_argument('run', type=Path, help='the run dir run.json')
    cover = sub.add_parser('covered', help='print the complications the run put to the agent (JSON list, or null)')
    cover.add_argument('run', type=Path, help='the run dir run.json')
    args = parser.parse_args(argv)
    if args.cmd == 'covered':
        print(json.dumps(covered(args.run.parent), ensure_ascii=False))
        return 0
    try:
        run = json.loads(args.run.read_text(encoding='utf-8'))
        print(json.dumps(chat_lines(run, args.run.parent), ensure_ascii=False))
    except (OSError, ValueError) as exc:
        print(f'run_numbers: {exc} — rebuild run.json (build_run_json.py) before rendering any number', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio  # noqa: PLC0415 - sibling script: UTF-8 standard streams on Windows

    utf8_stdio()
    raise SystemExit(main())
