#!/usr/bin/env python3
"""Render a local analytics report for a simulation run — the fallback the PRD
recommends when no observability platform recorded the run (no sink configured /
keys missing / Langfuse, which has no auto-charts).

The skill (local Claude) builds ONE `run.json` summary from the per-scenario files
the runner wrote (`<out>/<scenario>/transcript.json` / `error.json`); this script
lays it out deterministically — same split as the other renderers.

    python render_run_report.py run.json --out-dir plurai_sim/artifacts/runs/<name>

Writes:
    analytics.md
    eval_signal.md              (eval-set fitness — loop material, never the operator's page)
    regressions_detail.md     (LOCAL ONLY: regression owners, rates, recurring issue types — read from
                                 build_run_json's regressions_detail.json beside run.json; never indexed)
    charts/run_summary.svg      (passed % · suite score · tool-error %)
    charts/evals_passrate.svg   (per-eval pass-rate, ranked)
    run.json                    (canonical copy)
    report_files.json           (the files above this render wrote — what the report upload packs)

Stdlib-only. Anything the spec omits is left out — never shown blank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

# The backend's own constants, vendored — the same channel `build_run_json` reads, so the two ends of
# one contract are not kept in step by a docstring. Each literal below stays in place as the DOCUMENTED DEFAULT ON ABSENCE: a box runs a pinned image digest, so a plugin talking
# to a backend that predates a block is a live deployment state rather than a hypothetical.
_REPO_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_REPO_SCRIPTS))
try:
    import plurai_contracts as _contracts
except ImportError:  # this file COPIED out of the plugin — the module docstring invites that
    _contracts = None
try:
    import release_verdict as _release_verdict
    from release_verdict import shared_world_sentence as _shared_world_sentence
except ImportError:  # copied out of the plugin: the runner's own sentence stands in
    _release_verdict = None

    def _shared_world_sentence(risk: dict) -> str:
        return " ".join(str(risk.get("detail") or "Conversations share one world with no reset between conversations.").split())
import run_numbers  # noqa: E402 - the C7 reader: every customer count on this page comes from it


def _not_comparable(reason: object) -> str:
    """How a stability or agreement line reads when the runner marked it `stability_comparable: false`."""
    return f'not comparable ({"shared world" if reason == "shared_world" else _cell(str(reason or "no reason recorded"))})'


# The ONE name parser for run.json's annotated name lists, owned beside the annotations it inverts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_run_json import (  # noqa: E402
    measured_replays,
    REGRESSIONS_DETAIL_JSON,
    REGRESSIONS_DETAIL_SCHEMA,
    BACKEND_HTTP,
    EXIT_MEASURED_STOP,
    EXIT_RUN_ABORTED,
    HARNESS_MOCK_UNREACHABLE,
    MEASURED_STOP_REASONS,
    PROVIDER_INFRA,
    PROVIDER_INFRA_REMEDY,
    TRIALS_SCHEMA,
    backend_http_remedy,
    mock_runtime_remedy,
    mock_unreachable_remedy,
    policy_name,
)


def _contract(name: str, default: object) -> object:
    """The backend's value for `name`, or `default`, the literal fallback when the contract cannot supply it."""
    if _contracts is None:
        return default
    return _contracts.contract(name, default=default)

BG = "#0d1117"; PANEL = "#161b22"; STROKE = "#30363d"; TRACK = "#1b2230"
TEXT = "#e6edf3"; MUTED = "#6e7681"; LABEL = "#9aa5b1"
GREEN = "#3fb950"; GOLD = "#e3b341"; RED = "#ff7b72"; BLUE = "#4493f8"; CYAN = "#39c5cf"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SANS = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"


def _svg(w: int, h: int, body: str, *, rx: int = 0) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{SANS}"><rect width="{w}" height="{h}" rx="{rx}" fill="{BG}"/>{body}</svg>')


def _rate_color(r: float) -> str:
    return GREEN if r >= 0.8 else (GOLD if r >= 0.5 else RED)


def _elide(s: str, n: int) -> str:
    """`s` shortened to `n` characters by dropping its MIDDLE, keeping both ends.

    Tail truncation would render two policies that share a 33-character prefix as visually
    identical bars, and a reader comparing them would be comparing the same string. Long STRUCTURED
    names are exactly what the eval recipe produces (`knowledge_source_fidelity_turn_scoped_v2`
    beside `knowledge_source_fidelity_turn_scoped_v3`), and those differ at the END, which is the
    half tail truncation throws away.
    """
    if len(s) <= n:
        return s
    keep = n - 1                     # the ellipsis costs one character
    head = (keep + 1) // 2
    return s[:head] + "…" + s[len(s) - (keep - head):]


#: The three runner kinds that mean a BUDGET expired, each paired with what the reader does about IT.
#: Kept as an explicit map rather than a prefix match so a future harness kind cannot inherit
#: a stopwatch remedy that has nothing to do with it. Its KEYS are also :data:`_TIMEOUT_KINDS`
#: (the ⏱ cell), so a kind stays here even when its line names no knob. The reader-facing copy of
#: this list is `/simulation-run`'s `error_kind` vocabulary table; move them together.
_BUDGET_ADVICE = {
    # NO KNOB. A turn that outran its budget may have been waiting on the simulated tools — a mock
    # job that never finished — which no turn budget fixes, and the record cannot say which it was.
    # The release gate's bounded re-run repairs the loss; error.json is where the cause is read.
    "harness_turn_timeout": "the per-turn budget expired; error.json says what the turn was waiting on",
    # NOT the scenario time budget. Running out of THAT lands the scenario
    # (`result: "incomplete"`, reason `time_budget_exhausted`) with a transcript and real
    # verdicts. This kind is the DERIVED hard backstop underneath it, and it only fires when a
    # turn or the finalize hung past every stage bound the run declares — so the whole attempt
    # was cancelled and there is no transcript. Advising "--scenario-timeout" here sends the
    # reader to raise a budget that did not expire, on the one row where something genuinely hung.
    "harness_scenario_timeout": "the hard backstop fired — a turn or finalize hung past every stage bound; read error.json, not the clock",
    # Not the agent's clock at all — the judges'. A slow eval set blows this one while the
    # dialog itself was fine, so pointing the reader at a turn budget would be doubly wrong.
    "harness_finalize_poll_timeout": "the eval fan-out outran its poll wall — raise NETWORK__FINALIZE_POLL_MAX_WALLCLOCK_SECONDS",
}
_TIMEOUT_KINDS = frozenset(_BUDGET_ADVICE)

#: A row that ended early is routed by the RUNNER's one resolver: `run_summary.json`
#: `ended_early_routes` → `run.json` `ended_early.route` (`{owner, remedy, qualifier?}`), which the
#: console prints too. This page renders it verbatim and keeps no remedy table of its own — two
#: tables could drift (a harness remedy worded differently on each side, a push-back ending routed
#: to a different owner than the console's).
_NO_ROUTE = "the runner recorded no route for this ending — read the transcript and run_summary.json"


#: The SDK's per-policy reproduction marks (`PolicyReproduction`, run_summary.json
#: `stability[<scenario>].policy_reproduction`), rendered beside the scenario verdict. A scenario
#: mark of `stable` alone hides WHICH red reproduced: a scenario can read `stable` while the
#: red an agent finding is written from did NOT reproduce. Open vocabulary — an unknown mark
#: renders verbatim with `?`.
_REPRODUCTION_MARK = {
    "reproduced": "✔ reproduced",
    "not_reproduced": "✘ not reproduced on retry",
    "retry_only": "＋ retry-only",
    "unmeasured": "? unmeasured",
}

#: A FINAL run's trials (`selection: unconditional`): trials 2..N are trial 1's comparison sample, taken
#: for every scenario, so a mark compares trials rather than a retry.
_UNCONDITIONAL = "unconditional"
_TRIAL_MARK = {"not_reproduced": "✘ not reproduced in other trials", "retry_only": "＋ other trials only"}
_TRIAL_DETERMINISTIC = "deterministic check, re-run on the other trials' transcripts"


def _mark_text(mark: object, selection: object) -> str:
    return (_TRIAL_MARK.get(str(mark)) if selection == _UNCONDITIONAL else None) or _REPRODUCTION_MARK.get(str(mark), f"? {mark}")


def _against_trials(rows: object) -> bool:
    """Were these stability rows compared with the other trials (every one `selection: unconditional`), not a retry?"""
    dicts = [row for row in rows if isinstance(row, dict)] if isinstance(rows, (list, tuple)) else []
    return bool(dicts) and all(row.get("selection") == _UNCONDITIONAL for row in dicts)


#: The SDK's evidence token for a red no SAMPLING was done on — a `code_eval`
#: (run_summary.json `stability[<scenario>].policy_evidence`, `run_simulation.DETERMINISTIC_EVIDENCE`).
#: On a JUDGE REPLAY (`sample_kind: judge_replay`) its retry re-runs the same expression over the
#: same saved shape, so the second attempt cannot disagree with the first: the mark is a tautology,
#: and it blessed a deterministic FALSE red as `stable` on the night pass-2 RC1 run. On an AGENT
#: re-run the check grades a NEW transcript, and its mark is a real measurement.
_DETERMINISTIC_EVIDENCE = "deterministic"

#: How the replay case reads in the cell. It REPLACES the mark rather than riding beside it:
#: `✔ reproduced` is what a reader takes for corroboration, and there is none here to report.
_DETERMINISTIC_CELL = "= deterministic (re-run of the same expression — no second opinion)"

#: The stability row's `sample_kind` whose deterministic mark is that tautology.
_JUDGE_REPLAY = "judge_replay"

#: What a deterministic mark is evidence of, by the row's `sample_kind`; an unknown or absent kind keeps the bare label.
_DETERMINISTIC_ON = {"agent_rerun": "deterministic check, re-run on the retry's transcript"}

#: WHY a wanted stability retry was NOT spent (the SDK's `ShedReason`, typed on the row as
#: `shed_reason`). Unmeasured rows are GROUPED BY THIS VALUE and never by their `detail`
#: sentence: two producers' wordings of one reason are one group, and a sentence asserting "one infrastructure event" is not evidence of one. BOTH
#: mass-failure readings are completed from the row's measured `shed_signals` (see
#: `_SHED_MEASURED`); the retry-pool reading makes NO causal claim, because a dry pool says
#: nothing about why scenarios failed. The vocabulary is the SDK's `ShedReason`; a value this build does not know renders as unknown.
_SHED_READING = {
    "infrastructure": "the mass-failure shed engaged on infrastructure signals the rows carried",
    "wholesale_reds": "the mass-failure shed engaged on the CROSS-ROW reading alone — every graded scenario red on every "
                      "policy it was graded on, identically — which is a reading about the AGENT as often as about the apparatus",
    "retry_pool": "the run-level retry pool ran dry — a budget fact; nothing here says why the scenarios failed",
    "disabled": "stability retries were disabled for this run (--no-stability-retry)",
    "replay": "PLURAI_REPLAY makes a second sample a replay of the first",
    "no_budget": "the run was given no stability-retry budget",
}
#: The two shed readings the SDK stamps `shed_signals` on — the two the mass-failure shed decides
#: (`_RetryRefusal(..., signals=budget.signal_counts() if why in ('infrastructure', 'wholesale_reds'))`).
#: Rendering the counts under `infrastructure` alone would drop the measurement from the sharper of
#: the two: `wholesale_reds` is the reading that says the reds may be the AGENT'S, and it arrives
#: with the same per-signal counts.
_SHED_MEASURED = ("infrastructure", "wholesale_reds")

#: The bucket for an unmeasured row that carries no typed `shed_reason` AND no evidence a retry
#: ran. Its `detail` sentence is deliberately NOT surfaced as the reason: a sentence is what the
#: typed field supersedes.
#:
#: IT NAMES NO PRODUCER. "The runner recorded no `shed_reason` (older SDK)" would be a
#: diagnosis of the PRODUCER inferred purely from the absence of a field the producer writes only
#: on a DIFFERENT code path (a REFUSED retry), and nothing on the row carries a runner version to
#: check it against; it would also blame a current SDK over a row whose `stability_retry/` is on
#: disk. An absence is reported as an absence.
_SHED_UNTYPED = ("untyped — the row records no `shed_reason`; nothing here says why — "
                 "its own sentence is in run_summary.json `stability[<scenario>].detail`")

#: The marks that COMPARE two attempts — the SDK's `_COMPARING_MARKS`, mirrored in
#: `build_run_json._COMPARING_MARKS` and DERIVED here from this file's own vocabulary rather than
#: typed a third time: every reproduction mark except `unmeasured`, which compares nothing.
#: it matches `build_run_json`'s.
_COMPARING_MARKS = frozenset(_REPRODUCTION_MARK) - {"unmeasured"}

#: WHY an `unmeasured` row's SPENT retry settled nothing (the SDK's `UnmeasuredReason`, typed on
#: the row as `unmeasured_reason`). The twin of `_SHED_READING` on the other side of the fork: one
#: names why a wanted retry was NOT spent, this one why a retry that RAN decided nothing. A row
#: never carries both, and every artifact written before the SDK typed this carries neither —
#: which is why the reader DERIVES the state below and never requires this field.
_UNMEASURED_READING = {
    "no_decidable_reproduction": "the second sample was graded and nothing that COULD have disagreed reproduced — the reds "
                                 "that repeated are deterministic checks (a deterministic check that repeats is no second "
                                 "opinion), or the sample was a judge replay (`sample_kind: judge_replay`) whose judge agreed "
                                 "with itself on the SAME output, which vouches for the judge, not the failure",
    "retry_measured_nothing": "the retry ran but measured none of the first attempt's reds — it was not graded, or it "
                              "ended for a reason of its own",
    "retry_failed": "the retry FAILED before it was graded — a fault in the simulation, not a verdict on the agent — so "
                    "it measured nothing",
    "first_attempt_invalid": "the first attempt was never a measurement, so the retry that replaced it had nothing to compare against",
}
_TRIAL_UNMEASURED_READING = {
    "retry_measured_nothing": "the other trials ran but measured none of the first attempt's reds — they were not graded, or "
                              "they ended for reasons of their own",
}


def _retry_was_spent(row: dict) -> bool:
    """Did this `unmeasured` row's scenario actually get a SECOND SAMPLE?

    `unmeasured` is the RESIDUE bucket, not a synonym for n=1: the SDK reaches it from a refused
    retry AND from three paths on which the retry ran and settled nothing. `shed_reason` is
    written only on the REFUSED one, so its absence is not evidence that no retry ran — reading it
    that way would publish "charged on a single attempt … untyped … older SDK" over a scenario
    whose retry ran, was graded, and whose judge did not reproduce its own red.

    THREE signals, in order of directness, and the last two are BOTH needed: the SDK's typed
    reason where a current runner wrote one; `second_eval_revision`, which exists only because the
    retry was GRADED; and a comparing mark, which exists only because two attempts were compared —
    the shape of the prev-build rows, recorded before revision stamping.
    """
    if row.get("unmeasured_reason"):
        return True
    if row.get("second_eval_revision"):
        return True
    marks = row.get("policy_reproduction")
    return isinstance(marks, dict) and bool({str(mark) for mark in marks.values()} & _COMPARING_MARKS)


def _unmeasured_reason_reading(row: dict) -> str:
    """The SDK's typed `unmeasured_reason`, read BY VALUE the way `shed_reason` is — never by
    sentence. Empty for a row that carries none, which is every row written before the SDK typed
    it: the derivation below is what makes this fix retroactive."""
    reason = str(row.get("unmeasured_reason") or "")
    if not reason:
        return ""
    unknown = f"reason `{_cell(reason)}` not known to this build — read run_summary.json"
    reading = _TRIAL_UNMEASURED_READING.get(reason) if _against_trials([row]) else None
    # A failed retry's typed cause, by value — never its message.
    failure = row.get("retry_failure") if isinstance(row.get("retry_failure"), dict) else {}
    cause = failure.get("kind") or failure.get("exception")
    named = f" (`{_cell(str(cause))}`)" if cause else ""
    return f"`{_cell(reason)}` — {reading or _UNMEASURED_READING.get(reason, unknown)}{named}"


def _undecided_reading(row: dict) -> str:
    """What the SPENT retry MEASURED, off the row's own per-policy record — empty when it has
    nothing anybody vouches for.

    THROUGH `valid_marks`, like every other per-policy reader on this page. `marks_disowned` is
    the predicate spelled once precisely because the run-wide tally, the per-scenario table and
    the WITHDRAWN note are three readers that must not disagree about which rows are countable;
    reading the two maps RAW would make this a fourth that does: on a row whose own producer
    stamped `marks_valid: False` the page would say "nothing can vouch for a comparison between
    their two attempts" and then republish `agreement 1.00 → 0.60` three lines below it — the
    self-contradiction this bucket's split exists to remove.

    `_retry_was_spent` deliberately keeps reading them raw: a disowned mark still proves a second
    attempt HAPPENED, which is a different question from whether its comparison is valid.

    The row's `detail` is not read here for the same reason `_SHED_UNTYPED` refuses to surface
    one: a sentence is what the typed fields replaced. The deterministic re-runs are COUNTED
    rather than listed, because their whole reading is that they measured nothing.
    """
    marks = valid_marks(row, "policy_reproduction") or {}
    evidence = valid_marks(row, "policy_evidence") or {}
    judged = [f"`{_cell(str(name))}` {_cell(str(evidence.get(name)))}" for name in sorted(marks)
              if str(evidence.get(name)) != _DETERMINISTIC_EVIDENCE]
    deterministic = sum(1 for name in marks if str(evidence.get(name)) == _DETERMINISTIC_EVIDENCE)
    parts = []
    if judged:
        parts.append("judged reproduction " + " · ".join(judged))
    if deterministic:
        parts.append(f"{deterministic} deterministic re-run(s) NOT counted (a deterministic check that repeats is no "
                     "second opinion)")
    return "; ".join(parts)


def _retried_row_reading(row: dict) -> str:
    """One retried row's line: the typed reason where the runner wrote one, what the retry
    measured where anything vouches for it, and — ONLY when the row says neither — why not.

    The fallback is last and exclusive: appending "the retry graded none of the first attempt's
    reds" to a typed reason states the opposite of the reason it follows (`no_decidable_reproduction`
    is a retry that DID grade them), and it is reachable on any row whose marks were withdrawn.
    A withdrawn row gets the withdrawal's own reading instead of the never-graded one, because
    those marks were graded — they are simply not comparable, which the ⚠️ line above says in full.
    """
    parts = [part for part in (_unmeasured_reason_reading(row), _undecided_reading(row)) if part]
    if parts:
        return "; ".join(parts)
    if marks_disowned(row):
        return "its per-policy marks are WITHDRAWN above — nothing here says what reproduced"
    return f"{'the other trials' if _against_trials([row]) else 'the retry'} graded none of the first attempt's reds"


def _environment_pointer(dead_tools: list | None) -> str:
    """The ENVIRONMENT as a further place to look, beside any route, when some tool fired and never
    once returned in this run — a fact about the RUN the runner's route cannot know. Without it every
    line names the agent or the scenario even when a dead endpoint drove the give-ups."""
    if not dead_tools:
        return ""
    named = ", ".join(f"`{_cell(str(t))}`" for t in dead_tools)
    return f" — and at the ENVIRONMENT: {named} fired and never once returned in this run"


def _ended_early_parts(cut: dict, dead_tools: list | None) -> list[str]:
    """One headline part per (reason, route) group, commonest reason first, each naming its rows.

    The route is the runner's (`cut["routes"]`); a row it did not route says so rather than borrowing
    another reason's remedy. A route narrower than its reason (`qualifier`: a stated cause, a
    push-back, a voiding breach) is labelled with it, so two rows of one reason with different
    owners never share a line.
    """
    rows = cut.get("scenarios") or {}
    routes = raw if isinstance(raw := cut.get("routes"), dict) else {}
    env = _environment_pointer(dead_tools)
    parts = []
    for reason, _count in sorted((cut.get("reasons") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        groups: dict[tuple[str, str], list[str]] = {}
        for name in sorted(n for n, r in rows.items() if r == reason):
            route = found if isinstance(found := routes.get(name), dict) else {}
            groups.setdefault((str(route.get("qualifier") or ""), str(route.get("remedy") or _NO_ROUTE)), []).append(name)
        for (qualifier, remedy), names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            label = f"{reason} ({qualifier})" if qualifier else reason
            parts.append(f'{len(names)} scenario(s) ended early — {label}: {remedy}{env} [{", ".join(names)}]')
    return parts


def _ended_early_cell(block: object) -> str:
    """`reason (user-turn N of M)` for one row, or an em dash.

    The budget context is what makes the reason readable: "gave up at 4 of 13" and "gave up at
    13 of 13" are different findings with different owners. It is labelled **user-turn** because
    it is the SERVER's /user-turn CYCLE count, which is one lower than the Turns column whenever
    a closing-turn reply was delivered — silently printing it as "turns" invites a reader to call
    the mismatch a bug.

    The reason is rendered VERBATIM: an open vocabulary is not re-worded into a friendlier label
    here, because the same string is what the runner printed, what `run_summary.json` carries and
    what a reader greps for."""
    if not isinstance(block, dict) or not block.get("reason"):
        return "—"
    cell = _cell(str(block["reason"]))
    # The stated cause rides the cell as `reason[cause]`: it is what routes a give-up, and a
    # reader scanning the column should see the harness-shaped one without opening the headline.
    if block.get("cause"):
        cell += f'[{_cell(str(block["cause"]))}]'
    used, cap = block.get("user_turns"), block.get("max_turns")
    if isinstance(used, int) and isinstance(cap, int):
        cell += f" (user-turn {used} of {cap})"
    return cell


#: The `error_kind` for a scenario every policy was GATED OFF for. It scored nothing, so it is
#: an `error` row like the others — but nothing crashed, and 💥 sends the reader to look for a
#: traceback and an `error.json` that do not exist. Its `skipped_evals` column says which guards
#: fired. Same reasoning as the ⏱ cell above: both are "scored nothing", and they point at
#: different people.
_NO_APPLICABLE_POLICIES = "no_applicable_policies"

#: The harness's `seed_state` hook could not be called as the seed contract requires (missing on a
#: trace-resumed scenario, or a wrong arity). A TEMPLATE defect: not a budget, so it
#: earns no ⏱ and no advice to raise one, and not the agent, so it must not be 💥 — that cell sends
#: the reader to look for an agent traceback that does not exist. The fix is simulate.py.
_HARNESS_SEED_STATE_CONTRACT = "harness_seed_state_contract"
#: The harness voided its own row (``plurai.HarnessFault``, or a world write the mock refused).
_HARNESS_FAULT = "harness_fault"
#: The backend's mock runtime could not answer a mocked tool: the row's `mock_runtime` block says whether to re-run.
_HARNESS_MOCK_RUNTIME = "harness_mock_runtime"
#: The agent raised in its own turn: a FAILED row whose goal block names the raise (build_run_json.AGENT_RAISED).
_AGENT_RAISED = "agent_raised"
#: How a row's `fail_reasons` (the SDK's scores block, `plurai.scoring`) read in the Result column.
_FAIL_REASON_WORDS = {"low_score": "score below the bar", "critical_failure": "a critical check failed",
                      "failing_tier": "a required check failed", "goal_missed": "goal not met", "crashed": "the agent crashed"}


def scored_scenarios(t: dict) -> int:
    """The scenario-side denominator: the scenarios that produced a WHOLE-DIALOG verdict.

    THE convention, shared with the eval table (``_conclusive``): a rate divides by what the
    server decided, and what it did not decide is reported beside the number. An errored
    scenario has no ``eval`` block at all, so counting it as a non-pass reports the agent
    failing work nobody judged — the same conflation the eval rows refuse for inconclusive
    cells, one level up. ``errored`` defaults to 0, so an older run.json that predates the
    field renders exactly as it did.

    ``incomplete`` comes out for the same reason one level along: that scenario DID score, but
    over a conversation the scenario time budget censored. It is in neither ``passed`` nor
    ``failed``, so leaving it in the denominator would count it as a non-pass — publishing the
    agent failing obligations its dialog never reached, which is precisely the reading the state
    exists to stop. Absent on an older run.json, which then renders exactly as it did.

    ``not_evaluated`` comes out on the same rule: the backend reached no GOAL verdict on that row
    (nothing owned the goal, the measurement did not hold, or the run predates the block), so it
    is neither a pass nor a failure.
    """
    total = counted_over(t)[0]
    return max(0, total - (t.get("errored") or 0) - (t.get("incomplete") or 0) - (t.get("not_evaluated") or 0))


def counted_over(t: dict) -> tuple[int, str]:
    """``(n, noun)`` every pooled count in ``totals`` is over: a run.json pooled over trials
    (``build_run_json.pool_trials``) counts ``conversations``, a single-trial one ``scenarios``."""
    return (t["conversations"], "conversations") if t.get("conversations") else (t.get("scenarios", 0), "scenarios")


def pooled_scope(t: dict) -> str | None:
    """"N conversations (S scenarios × T trials)" for a run.json pooled over trials, else None — said once
    beside the pooled counts so none of them is read against the scenario count. Rows that replayed one recorded
    turn of a regression (`single_hop_replays`) are counted as replays, never as conversations."""
    if not (t.get("conversations") and t.get("trials")):
        return None
    scope = f'{t.get("scenarios", 0)} scenarios × {t["trials"]} trials'
    hops = int(t.get("single_hop_replays") or 0)
    if 0 < hops <= t["conversations"]:
        return f'{t["conversations"] - hops} conversations and {hops} replays of a recorded turn ({scope})'
    return f'{t["conversations"]} conversations ({scope})'


def pooled_rows_noun(t: dict, noun: str) -> str:
    """The noun a pooled per-row count reads under (`N of M <noun> failed …`): ``conversations and replays`` when the
    rows include single-hop replays (`single_hop_replays`, as :func:`pooled_scope` splits them), else ``noun``."""
    hops = int(t.get("single_hop_replays") or 0)
    return f"{noun} and replays" if noun == "conversations" and 0 < hops <= (t.get("conversations") or 0) else noun


def goals_line(t: dict) -> tuple[str, str]:
    """``(value, label)`` for the headline: "N of M goals achieved".

    M is the scenarios something OWNED a goal for (``goals_measurable``), not the scenario
    count: a customer whose eval set merely predates the goal owner would otherwise read
    ``0 of 7`` on every report — the suite looking catastrophic because nothing was asked of it.
    With M == 0 there is no fraction to print, so the value is an em dash, the same convention
    ``suite_score`` uses for an absent score.

    The zero-M label does NOT name a cause. M is 0 for at least three different reasons — no
    eval owns a goal, the backend publishes no verdict block at all, and every row errored or
    ran out of clock — and each sends the reader to a different repair. The per-row `goal.reason`
    knows which; a headline that guesses would send most readers to the wrong one.

    A run.json built before ``goals_measurable`` existed carries no goal data at all, so the
    caller must not reach this — see ``render_summary_svg``.
    """
    achieved, measurable = t.get("goals_achieved") or 0, t.get("goals_measurable") or 0
    if not measurable:
        n, noun = counted_over(t)
        return "—", f'goals achieved — no goal was owned and measured on any of {n} {noun}'
    return f"{achieved} of {measurable}", "goals achieved"


def render_summary_svg(t: dict, floored: bool = False) -> str:
    """The headline cards — FOUR on the goal axis (goals achieved · not evaluated · suite score
    (0-100, the SDK's, copied by build_run_json) · tool-error rate), three on the legacy axis. ``floored`` says the tool counts are a
    LOWER BOUND.

    The tool-error rate divides by ``tool_calls``, and the union that produced that number knows
    when it had to drop a row it could not verify — a fact stderr alone does not carry,
    so without it the card would publish a percentage over a denominator nobody was told was short. It rides as
    a parameter rather than being read off ``totals`` because the receipt lives in
    ``run_integrity.tool_count_floor``, where the Run-integrity section explains it at length;
    this card only has to stop presenting the number as exact.
    """
    tool_calls = t.get("tool_calls", 0) or 0
    tool_err = t.get("tool_errors", 0) or 0
    # An absent (or null) `suite_score` means the SDK scored no scenario — a card showing 0
    # would read as "everything scored zero", so absence renders as an em dash.
    suite = t.get("suite_score")
    # THE HEADLINE CARD is "N of M goals achieved" whenever the run.json carries the goal axis
    #, with a second card counting what nobody evaluated. A percentage of "scored
    # scenarios" answers a different question — did every policy pass — and a suite with one
    # flaky style judge would publish a red headline for an agent that did exactly what was asked.
    # A run.json built before the axis existed has no goal data to read, so it keeps the percentage
    # card rather than rendering an em dash that reads as a broken feature.
    if "goals_measurable" in t:
        value, label = goals_line(t)
        unevaluated = t.get("not_evaluated") or 0
        # NEUTRAL whenever part of the denominator went unmeasured. `goals_unverdicted` counts
        # the rows that HAD a goal owner and reached no verdict — they are inside M and can never
        # be inside N, so colouring the fraction red would report a broken judge as an agent
        # failure, on the one card a reader looks at first. Absent on a run.json written before
        # the key existed.
        unverdicted = t.get("goals_unverdicted") or 0
        measurable = t.get("goals_measurable") or 0
        colour = _rate_color((t.get("goals_achieved") or 0) / measurable) if measurable and not unverdicted else BLUE
        cards = [
            (value, label, colour),
            # A count, never a rate: "3 scenarios reached no verdict" is the finding, and a
            # percentage of a denominator that is itself in question says nothing.
            (str(unevaluated), "not evaluated — no goal verdict" if unevaluated else "not evaluated", GOLD if unevaluated else BLUE),
        ]
    else:
        # ZERO scored scenarios is not a 0% pass rate. With every row errored or cut short there
        # is no denominator at all, and `passed / 1` would publish "0% of scored scenarios passed"
        # for a run that scored none — the same lie the `suite_score` card below refuses to tell.
        # Absence renders as an em dash there; it renders as one here for the same reason.
        n = scored_scenarios(t)
        rate = t.get("passed", 0) / n if n else None
        cards = [(f'{round(100 * rate)}%' if rate is not None else "—", f"of scored {counted_over(t)[1]} passed",
                  _rate_color(rate) if rate is not None else BLUE)]
    cards += [
        (f'{suite:.1f}' if isinstance(suite, (int, float)) and not isinstance(suite, bool) else "—", "suite score (of 100)", BLUE),
        # ZERO observed tool calls is not a 0% tool-error rate, for the same reason zero scored
        # scenarios is not a 0% pass rate: there is no denominator. `0%` is the HEALTHIEST number
        # this card can print, and it would be printed over runs that watched no dispatch at all —
        # green-coloured, beside a `suite_score` card that refuses to tell that exact lie
        # about the same run. Absence renders as an em dash here too, and the label says which
        # absence it is, because "—" alone reads as "the renderer could not compute it".
        (f'{round(100 * tool_err / tool_calls)}%' if tool_calls else "—",
         ("tool-error rate (floor)" if floored else "tool-error rate") if tool_calls else "tool-error rate — no tool calls measured",
         _rate_color(1 - tool_err / tool_calls) if tool_calls else BLUE),
    ]
    # The card WIDTH divides a fixed banner width, rather than the banner growing with the card
    # count: every other chart in this report is 860 wide, and a summary 322px wider than its
    # neighbours is scaled down by the markdown viewer — so the headline would render smaller
    # than the chart below it precisely on the runs that carry an extra card.
    gap, pad, w = 34, 16, 860
    cw = (w - pad * 2 - (len(cards) - 1) * gap) // len(cards)
    value_size = 40 if cw >= 240 else 30
    p = []
    for i, (val, lbl, accent) in enumerate(cards):
        x = pad + i * (cw + gap)
        p.append(f'<rect x="{x}" y="16" width="{cw}" height="100" rx="14" fill="{PANEL}" stroke="{STROKE}"/>')
        p.append(f'<circle cx="{x + 26}" cy="42" r="3.5" fill="{accent}"/>')
        p.append(f'<text x="{x + 26}" y="80" font-size="{value_size}" font-weight="600" font-family="{MONO}" fill="{TEXT}">{escape(val)}</text>')
        p.append(f'<text x="{x + 28}" y="102" font-size="12.5" fill="{LABEL}">{escape(lbl)}</text>')
    return _svg(w, 132, "".join(p))


def _conclusive(e: dict) -> tuple[int, int]:
    """``(passed, conclusive)`` — the chart's denominator is decided verdicts, like the table's."""
    passed, failed = eval_counts(e)[:2]
    return passed, passed + failed


def render_evals_svg(evals: list[dict]) -> str:
    # An eval that decided nothing has no pass-rate to draw — a 0% bar would read as "the
    # agent failed it". It is reported by name in the Eval-signal table instead.
    rows = [e for e in evals if _conclusive(e)[1]]
    rows.sort(key=lambda e: _conclusive(e)[0] / _conclusive(e)[1])
    if not rows:
        # NOT a blank canvas. A chart gated off by its own precondition must render the
        # precondition — an empty 860×50 rectangle is indistinguishable from a broken renderer,
        # and from a run where every eval passed. Same rule as the em-dash cards above.
        return _svg(860, 56, f'<text x="26" y="34" font-size="12.5" fill="{LABEL}">'
                             "no eval reached a verdict on this run — there is no pass-rate to rank</text>")
    top, rh, bx, bw = 56, 34, 320, 360
    w, h = 860, top + len(rows) * rh + 12
    p = [f'<text x="26" y="30" font-size="13" font-weight="700" fill="{TEXT}" letter-spacing="0.04em">EVAL PASS-RATE</text>',
         f'<text x="170" y="30" font-size="11.5" fill="{MUTED}">share of DECIDED verdicts each eval passed</text>']
    for i, e in enumerate(rows):
        y = top + i * rh
        passed, conclusive = _conclusive(e)
        r = passed / conclusive
        accent = _rate_color(r)
        p.append(f'<text x="306" y="{y + 13}" text-anchor="end" font-size="11.5" fill="{LABEL}">{escape(_elide(e["id"], 34))}</text>')
        p.append(f'<rect x="{bx}" y="{y}" width="{bw}" height="16" rx="4" fill="{TRACK}"/>')
        p.append(f'<rect x="{bx}" y="{y}" width="{max(4, bw * r):.0f}" height="16" rx="4" fill="{accent}"/>')
        p.append(f'<text x="{bx + bw + 10}" y="{y + 13}" font-size="11.5" font-weight="700" fill="{TEXT}" font-family="{MONO}">{round(100 * r)}% ({passed}/{conclusive})</text>')
    return _svg(w, h, "".join(p))


def eval_counts(e: dict) -> tuple[int, int, int, int, int]:
    """``(passed, failed, inconclusive, errored, skipped)`` for one eval row.

    CONCLUSIVE verdicts are `passed` + `failed` — the only cells that say anything about the
    agent. An inconclusive cell is the judge declining to decide and a skipped one is the
    judge never running, so neither belongs in either column.

    An older run.json carries only `{passed, total}`, where `total` was every cell the policy
    returned — inconclusive ones included. Nothing can recover the split from that shape, so
    the remainder is read as failures.
    """
    passed = e.get("passed") or 0
    # THE INCLUSIVE undecided count, which is what every reader below is written against.
    # `build_run_json` publishes the nothing-was-measured cells on
    # their OWN counters and leaves only the hedges in `inconclusive`, so they are added back here
    # — once, in the one accessor — rather than at each of the five call sites. Adding them at the
    # call sites instead deletes the `n/a` from the report: `_reason_count` clamps the
    # histogram at this number, so a smaller one zeroes it, `_verdicts_cell` renders
    # `inconclusive - n_a`, and `eval_signal`'s premise-gated branch tests equality against it.
    #
    # An OLDER run.json carries neither counter and its `inconclusive` is already inclusive, so
    # the two absent keys add nothing.
    inconclusive = (e.get("inconclusive") or 0) + (e.get("not_applicable") or 0) + (e.get("not_measured") or 0)
    errored = e.get("errored") or 0
    skipped = e.get("skipped") or 0
    failed = e.get("failed")
    if failed is None:
        failed = max(0, (e.get("total") or 0) - passed - inconclusive - errored)
    return passed, failed, inconclusive, errored, skipped


#: `inconclusive_reason` values meaning NOTHING WAS MEASURED, read off the backend's `verdict_axis`
#: block through the same vendored channel `build_run_json` reads — not mirrored from it: copies held
#: together by a docstring and a test comparing them to EACH OTHER would stay in step with one
#: another while both drifted from the backend. The literals here remain the documented default on absence, for an older backend.
#:
#: Either the premise never AROSE, or the measurement was left unmade (`not_measured` — a censored
#: dialog, a contested delivery, a helper that abstained). Every other reason means the judge saw a
#: live premise and could not decide — a different finding with a different fix, and the only one
#: worth a round's repair budget.
_PREMISE_ABSENT_REASONS = frozenset(_contract("verdict_axis", default={}).get("not_applicable_reasons")
                                    or {"not_applicable", "mixed_not_applicable"})

#: The UNMEASURED half of that class, kept apart so the union below is derived rather than
#: retyped. It does not word the signal note: one token covers several backend causes and a
#: helper abstention declares none, so a note phrased off this set would name a cause nobody
#: wrote. Only the cell's own reasoning knows, and that is what the row prints beside it.
_NOT_MEASURED_REASONS = frozenset(_contract("verdict_axis", default={}).get("not_measured_reasons") or {"not_measured"})
#: The union, DERIVED — naming `not_measured` in both would let the two drift into disagreeing
#: about whether a censored cell measured anything. It is the set this page renders under one
#: word (`n/a`); the two halves get their own COUNTERS in run.json because a truncated suite and
#: a premise-gated one are different repairs, and that split is a fact for the typed row to carry,
#: not a second word for the reader to parse.
_NOT_APPLICABLE_REASONS = _PREMISE_ABSENT_REASONS | _NOT_MEASURED_REASONS


def _reason_count(e: dict, reasons: frozenset[str]) -> int:
    """How many of the eval's inconclusive cells carry one of ``reasons``.

    An older run.json carries no `inconclusive_reasons` histogram and nothing can recover the
    split from what it does carry, so it reads 0 and every such eval keeps the label it had.
    Capped at the `inconclusive` count: a histogram that outran its own column would let a
    malformed artifact retitle a judge that genuinely hedged.
    """
    hist = e.get("inconclusive_reasons")
    if not isinstance(hist, dict):
        return 0
    n = sum(v for k, v in hist.items() if k in reasons and isinstance(v, int))
    return min(n, eval_counts(e)[2])


def not_applicable(e: dict) -> int:
    """How many of the eval's inconclusive cells measured nothing — premise-absent or cut off.

    The TYPED COUNTERS first, because they are the channel `build_run_json` writes; the histogram
    is a record of the reasons, and deriving the partition from it a second time is how two
    readings of one row come to disagree. Both are consulted (each is emitted only when non-zero,
    so a row with premise-absent cells and no censored ones carries just the one key).

    An older run.json carries neither, and there the histogram IS the only record — read exactly
    as before, clamp included.
    """
    typed = [e.get("not_applicable"), e.get("not_measured")]
    if any(isinstance(n, int) for n in typed):
        return sum(n for n in typed if isinstance(n, int))
    return _reason_count(e, _NOT_APPLICABLE_REASONS)


def decided_units(e: dict) -> tuple[int, int, str]:
    """``(passed, decided, unit)`` at the grain the POLICY DECLARED it grades at.

    A policy whose `evaluation_scope` is turn-based decides one cell per TURN, and its scenario
    row is a MONOTONE roll-up of those cells — one failing turn reds the whole scenario. So the
    scenario counts can only move a turn-scoped judge toward one-sidedness, and the two tests
    below that ask whether the judge ever came out the other way have to ask its own grain or
    they answer a question the artifacts contradict: a judge that decided 20 turns and
    passed 3 of them would roll to 0 of 3 scenarios and publish "no evidence it can pass" on the
    same page that names the turns it passed.

    `build_run_json` writes `decided_turns`/`passed_turns` for exactly the policies whose cells
    declared that scope. Their ABSENCE is the scenario grain and needs no other reading — an
    end-to-end policy, a turn-scoped policy that decided no turn, and a run.json built before
    this field existed all render at the scenario grain.

    Clamped like `_reason_count`: a malformed artifact whose passed count outran its decided one
    must not manufacture a two-sided judge out of arithmetic.
    """
    decided = e.get("decided_turns")
    if isinstance(decided, int) and decided > 0:
        return min(e.get("passed_turns") or 0, decided), decided, "turn"
    passed, failed = eval_counts(e)[:2]
    return passed, passed + failed, "scenario"


#: DECIDED verdicts a one-sided eval needs before "it never came out the other way" is a claim
#: about the EVAL rather than about the sample. Counted in decided cells, never in scenarios: a
#: 7-scenario run whose guard admitted 2 cells is as thin as a 2-scenario one. On a 3-scenario
#: qualification sample one direction is the expected outcome for nearly every policy, so an
#: unfloored warning fires on most rows of it and carries nothing.
_SAMPLE_FLOOR = 3

#: The label for a one-sided eval under that floor. It is not a finding about the eval, and it is
#: not evidence the agent passed either — the run simply did not ask the question enough times.
_INSUFFICIENT = "insufficient sample"
#: The label for an eval whose judge itself failed on every scenario it ran: a broken eval to repair.
_JUDGE_ERRORED = "judge errored"
#: The label for an eval every case of which was graded under the eval it defers to: decided, and carried by its owner.
_DEFERRED_SIGNAL = "graded under owner"


def eval_signal(e: dict, scenarios: int, noun: str = "scenarios") -> tuple[str, str]:
    """(label, note) describing whether this eval SEPARATED good behaviour from bad.

    Every other gate in the plugin checks an eval for correctness — schema, polarity,
    guard coverage, dead guards. None of them asks the question that decides whether the
    suite is worth anything: did this judge ever come out differently on different
    scenarios? A judge that returns the same verdict everywhere is consistent with the
    agent being flawless, with the judge being broken, and with the judge grading nothing
    at all, and correctness checks cannot tell those apart.

    The classification reads CONCLUSIVE verdicts only. Counting inconclusive cells as
    failures made a judge that decided NOTHING render as "all fail — no evidence it can
    pass" — a claim about the agent, drawn from cells that say nothing about it, and
    indistinguishable in the report from a judge that conclusively failed twice.

    One-sidedness is readable only from ``_SAMPLE_FLOOR`` decided verdicts up. Below it the same
    shape is what a small sample looks like when everything works, so it is labelled as the thin
    sample it is rather than as a warning the reader would have to answer by loosening a judge.

    Both of those tests are asked of the DECIDED UNIT (``decided_units``) — the turns of a
    turn-scoped policy, the scenarios of an end-to-end one. The scenario row of a turn-scoped
    judge is a monotone roll-up of its turn cells, so asking it there can only move that judge
    toward "all fail", and did: see ``decided_units``. The branches ABOVE stay at the scenario
    grain, where they belong — whether anything was decided at all, and why not, is a question
    about the cells the run produced, not about the grain they were graded at.

    A row POOLED over trials (`build_run_json.pool_trials`) counts conversations, and `scenarios` / `noun` say so; its
    one-sided floor is asked of the DISTINCT scenarios decided (`decided_scenarios`) at either grain: three trials of
    one scenario agreeing are not three scenarios.

    Computed from run.json alone, so it costs no extra call and no judgment.
    """
    passed, failed, inconclusive, errored, skipped = eval_counts(e)
    conclusive = passed + failed
    if conclusive == 0:
        # A judge that CRASHED everywhere and one that ran and hedged everywhere are different
        # findings with different fixes — the first is a broken eval to repair, the second a
        # rubric or an evidence gap. Folding them together sends the reader to the wrong one.
        if (deferred := int(e.get("deferred") or 0)) and not inconclusive and not errored:
            # Every case it decided was graded under the eval it defers to: the owner's verdict carries it.
            return (_DEFERRED_SIGNAL, f"graded under {_owners_phrase(e)} on all {deferred} scenario(s) it decided")
        if errored and not inconclusive:
            return (_JUDGE_ERRORED,
                    f"the judge itself failed on all {errored} scenario(s) it ran — a broken eval to repair, "
                    "not a verdict about the agent")
        # A judge whose premise never arose measured nothing because there was nothing to
        # measure — a correct gate, not an unproven eval. Only when EVERY undecided cell says
        # so: one hedge of any other kind, or one crash, and the doubt is real again.
        if inconclusive and not errored and not_applicable(e) == inconclusive:
            # The CLASS, never a cause. `not_measured` is one token over several causes and for a
            # helper abstention carries none on the wire, so a note saying "the dialog
            # was cut" would stand over cells that were never cut, beside each cell's own
            # reasoning, which is the only thing that knows. Say the class and point at that.
            return ("premise-gated",
                    f"it measured nothing on the {inconclusive} scenario(s) it ran — premise absent, "
                    "or the cell left unmeasured (its own reason says which)")
        if inconclusive or errored:
            return ("never conclusive",
                    f"ran on {inconclusive + errored} scenario(s) and reached a verdict on none — it is unproven, "
                    "and the cells it left say nothing about the agent")
        return ("never scored", "gated off on every scenario — it graded nothing, so it is unproven, not passing")
    # …and from here the question is one-sidedness, which is asked of the DECIDED UNIT: the turns
    # for a turn-scoped policy, the scenarios for an end-to-end one (`decided_units`). Both tests
    # below count "decided cells" rather than scenarios; for a judge whose cells are turns, the
    # decided cells are turns.
    unit_passed, unit_decided, unit = decided_units(e)
    if unit_passed == unit_decided or unit_passed == 0:
        # One-sided, and the DECIDED count decides what that means. Under the floor the claim
        # "no evidence it can fail" is unearned, and it is worse than merely wrong: the only way
        # to make a single-dimension judge come out two ways on three transcripts is to loosen it
        # until one of THOSE three fails, which fits the eval to the qualification sample.
        one_sided, opposite = ("pass", "fail") if unit_passed else ("fail", "pass")
        distinct = e.get("decided_scenarios")
        if isinstance(distinct, int) and distinct < _SAMPLE_FLOOR:
            return (_INSUFFICIENT,
                    f"below sample floor, n={distinct}<{_SAMPLE_FLOOR} — {unit_decided} verdict(s) on {distinct} scenario(s), all "
                    f"{one_sided}; repeats of the same scenarios are not a wider sample")
        if unit_decided < _SAMPLE_FLOOR:
            # The floor's NOT-ENGAGED state, stated as an arithmetic a reader can check. A gate
            # that silently withholds a claim is indistinguishable from a gate that looked and
            # found nothing, and on a `count: 3` qualification run this gate holds most rows.
            return (_INSUFFICIENT,
                    f"below sample floor, n={unit_decided}<{_SAMPLE_FLOOR} — {unit_decided} verdict(s), all "
                    f"{one_sided}; under {_SAMPLE_FLOOR} decided cells one direction "
                    "is the shape of a small sample, not a property of the eval")
        # The COUNT the claim rests on, beside the grain it was counted at. The under-floor note
        # above carries its `n`, and this branch needs one too: the counts column is the sample only
        # at the scenario grain, and a sample can be turns: a row can read `2/2 passed` in the column and rest on twelve decided turns, and
        # a reader given only those two numbers has to guess which one the verdict used. Same
        # argument the `discriminates` note below is written for, applied to its own branch.
        if unit == "scenario" and isinstance(distinct, int):
            unit = "conversation"  # pooled over trials: the cells are conversations, the floor above their scenarios
        return (f"all {one_sided}",
                f"same verdict on every {unit} it decided ({unit_passed}/{unit_decided}) — "
                f"no evidence it can {opposite}")
    note = ""
    if scenarios and conclusive < scenarios:
        rest = "graded under the eval it defers to, premise-gated or hedged" if e.get("deferred") else "premise-gated or hedged"
        note = f"decided {conclusive} of {scenarios} {noun} ({rest} on the rest)"
    if unit == "turn":
        # The counts COLUMN is the scenario roll-up, so on a turn-scoped judge it can read `0/3
        # passed` beside a verdict that says it discriminates — both true, and a reader handed
        # only the two of them has to guess which one is wrong. Say the arithmetic instead.
        grain = (f"{unit_passed}/{unit_decided} turns passed — this policy grades per TURN, and a scenario "
                 "reds on one failing turn")
        note = f"{grain}; {note}" if note else grain
    return ("discriminates", note)


def across_trials(e: dict, label: str, note: str, trials: object) -> tuple[str, str]:
    """Trial 1's (label, note), corrected by the SDK's across-trial count (`trials.run.policies`) only
    TOWARD `discriminates`: a pass and a fail on any trials separate good from bad; verdicts only
    outside an undecided trial 1 are `insufficient sample`. Same-direction repeats of the same
    scenarios are not a wider sample, so they only add their counts — never a one-sided claim."""
    if "decided_scenarios" in e:  # the row is already pooled over every trial (`build_run_json.pool_trials`)
        return label, note
    run = trials.get("run") if isinstance(trials, dict) and trials.get("schema") == TRIALS_SCHEMA else None
    cell = (run.get("policies") or {}).get(e.get("id")) if isinstance(run, dict) else None
    passed, failed = (int(cell.get("passed") or 0), int(cell.get("failed") or 0)) if isinstance(cell, dict) else (0, 0)
    if label in ("discriminates", _JUDGE_ERRORED) or not passed + failed:  # a broken eval stays visible
        return label, note
    counts = f'across {trials.get("n")} trials: {passed} passed · {failed} failed'
    if passed and failed:
        return "discriminates", f"{counts} (trial 1 alone: {label})"
    if not sum(eval_counts(e)[:2]):
        return _INSUFFICIENT, f"decided only outside trial 1 — {counts} (trial 1 alone: {label})"
    return label, f"{note}; {counts}" if note else counts


# Ordered by what a reader should act on first — this dict's order IS the table's sort order, so
# a label cannot be ranked in one place and iconned in another. `all fail` outranks `all pass` because a judge that
# reds a whole sample is the live over-constraint candidate; the under-floor rows sort BELOW the
# healthy ones because they are an absence of measurement, not a finding, and on a small sample
# they are the majority — heading the table with them buries every row a reader can act on.
_SIGNALS = {_JUDGE_ERRORED: "💥", "never scored": "🚫", "never conclusive": "🚫", "all fail": "⚠️", "all pass": "⚠️",
            "premise-gated": "➖", _DEFERRED_SIGNAL: "↪️", "discriminates": "✅", _INSUFFICIENT: "❔"}
_SIGNAL_ORDER = {label: i for i, label in enumerate(_SIGNALS)}


def _verdicts_cell(e: dict) -> str:
    """The counts behind the signal — conclusive first, then what was NOT decided.

    The unscored cells are shown, never folded into a denominator: a reader who sees only
    "0/2 passed" cannot tell a judge that failed twice from one that decided nothing — or a
    judge whose premise simply never arose from one that saw it and hedged.
    """
    passed, failed, inconclusive, errored, skipped = eval_counts(e)
    n_a = not_applicable(e)
    conclusive = passed + failed
    bits = [f"{passed}/{conclusive} passed" if conclusive else "none decided"]
    # `n/a` — the premise never arose — is counted APART from the cells where the judge saw a
    # live premise and declined: the first is the gate working, the second is a rubric to fix.
    bits += [f"{n} {word}" for n, word in ((n_a, "n/a"), (int(e.get("deferred") or 0), f"graded under {_owners_phrase(e)}"),
                                           (inconclusive - n_a, "inconclusive"), (errored, "errored"), (skipped, "skipped")) if n]
    return " · ".join(bits)


def _owners_phrase(cell: dict) -> str:
    """The evals a deferred cell was graded under (`deferred_to`), as code spans; `another eval` when none is named."""
    owners = [str(o) for o in cell.get("deferred_to") or [] if o]
    return ", ".join(f"`{_cell(o)}`" for o in owners) or "another eval"


def render_signal_md(spec: dict) -> list[str]:
    """The eval-power table. Empty list when the run scored no evals at all."""
    evals = spec.get("evals", [])
    if not evals:
        return []
    totals = spec.get("totals") or {}
    # A pooled run.json counts every trial's conversations; the denominator and its noun follow.
    scenarios, noun = counted_over(totals)
    rows = [(e, *across_trials(e, *eval_signal(e, scenarios, noun), spec.get("trials"))) for e in evals]
    scope = pooled_scope(totals)
    L = ["## Eval signal", "",
         "_Whether each eval SEPARATED good behaviour from bad on this run. An eval with one "
         "verdict everywhere measured nothing here, however correct it is; an eval that reached "
         "no verdict at all measured nothing about the agent either way._", "",
         "_Denominator: verdicts the judge REACHED (passed + failed). Cells it left "
         "n/a, inconclusive, errored or skipped are listed beside the rate, never inside it — "
         "and `n/a` (the premise never arose) is not the judge failing to decide._", "",
         *([f"_Verdicts pool every trial: {scope}._", ""] if scope else []),
         f"_One-sidedness is a finding only from {_SAMPLE_FLOOR} decided verdicts up"
         + (f", on at least {_SAMPLE_FLOOR} distinct scenarios" if any("decided_scenarios" in e for e in evals) else "")
         + ". Below that the row says how thin the sample was, and nothing about the eval._", "",
         "| Eval | Verdicts | Signal | Reading |", "|---|---|---|---|"]
    for e, label, note in sorted(rows, key=lambda r: (_SIGNAL_ORDER.get(r[1], 9), r[0].get("id", ""))):
        L.append(f'| `{e.get("id", "?")}` | {_verdicts_cell(e)} | {_SIGNALS.get(label, "")} {label} | {note or "—"} |')
    # Three PARTITIONS of the same rows, each read by one footer line below, so no footer can
    # count something other than what its own sentence claims.
    thin = [r for r in rows if r[1] == _INSUFFICIENT]
    discriminating = [r for r in rows if r[1] == "discriminates"]
    # The under-floor rows are kept OUT of the line below: their remedy is a wider sample, not the eval.
    weak = [r for r in rows if r[1] not in (_INSUFFICIENT, "discriminates")]
    L += ["", f'_{len(discriminating)} of {len(rows)} evals discriminated on this run._', ""]
    if weak:
        L += [f"_{len(weak)} did not discriminate on this run._", ""]
    if thin:
        L += [f'_{len(thin)} came out one way on fewer than {_SAMPLE_FLOOR} decided verdicts, so this run cannot say '
              "whether they can go the other way — that is a sample size, not an eval to repair: widen the run "
              'before touching the judge._', ""]
    return L


_DET_ICON = {"passed": "✅", "failed": "❌", "error": "💥"}


def _turns_cell(s: dict) -> str:
    """The Turns cell, plus the turns on it that measured NOTHING.

    The backend files a `not_measured` cell per turn_based policy for a turn the RUNNER emptied,
    rather than invoking judges over an utterance that does not exist — graded as REDs, such
    turns would read as findings about an agent nobody observed. This cell reads
    "3 turns unmeasured (harness)" instead.

    The OWNER is printed only when run.json resolved one. An unattributed hole is honest; one
    attributed to the harness on a run whose cause the wire did not carry is not, and "the dialog
    was cut" and "the runner emptied this turn" are two different repairs.
    """
    turns = str(s.get("turns", "—"))
    if isinstance(failure := s.get("turn_failure"), dict):
        # The dialog LANDED on a failed turn: graded to the turn it reached, or (the closing call) one reply short.
        exc = _cell(str(failure.get("exception") or "unknown"))
        idx = failure.get("turn_idx")
        turns += (f' · closing reply lost ({exc})' if failure.get("closing")
                  else f' · turn {idx if isinstance(idx, int) and not isinstance(idx, bool) else "?"} failed ({exc})')
    idxs = s.get("unmeasured_turns")
    if not isinstance(idxs, list) or not idxs:
        return turns
    owner = s.get("unmeasured_turns_owner")
    suffix = f' ({owner})' if isinstance(owner, str) and owner else ""
    return f'{turns} · {len(idxs)} turn{"s" if len(idxs) != 1 else ""} unmeasured{suffix}'


#: What each debt kind means in one operator phrase, and what would make it gradeable. The
#: enum is closed (the coverage-debt contract); an unknown kind renders its raw token rather
#: than being dropped — a kind we do not recognise is still a policy grading nothing.
_DEBT_REMEDY = {
    "never_scored_policy": "give it a scenario its rubric can actually judge, or drop it from the set",
    "set_wide_skipped_guard": "widen the applicability guard, or author a scenario that opens it",
    "polarity_unmeasured": "run the polarity self-test outside the guard's scope",
    "pinned_probe_ungraded": "add a policy that grades the probe, or unpin it",
    "knowledge_turns_unrealized": "re-plant the knowledge flow, or record the gap deliberately",
    "source_fidelity_ungraded": "add a judge that grades source fidelity over the registered sources",
}


#: What each `measurement_clean: false` channel means, and whose it is. The flag alone reads as
#: a contradiction of the per-scenario map beside it — every row `clean: true` under a top-level
#: `false` — so the channel is what makes it actionable.
_UNCLEAN_CHANNEL = {
    "measurement_integrity": "a judge, guard or target check broke while scoring — the verdicts it should have produced are missing",
    # THREE owners look identical from here — the agent, the harness, the runner — and this
    # sentence must not name only the customer's file: an SDK reply-projection defect would send
    # the reader to rewrite simulate.py. So the sentence ROUTES rather than blames.
    "degraded_scenarios": ("the runner emptied N turns and the backend graded them — if invoke_agent returns a message list "
                           "whose reply is visibly in it, this is an SDK defect (surface: sdk), not a simulate.py fix"),
    "user_sim_integrity": "the simulated user departed from its scenario's schedule, so turn-indexed verdicts over those turns grade a dialog nobody specified",
    # Routes, like the row above it: the runner cannot tell a blind reader from a record that
    # genuinely owns nothing of what was asked, so the sentence names the check that does.
    "world_unread_scenarios": ("a tool call whose every argument names ONE seeded record came back with zero rows — the mock env's "
                               "handlers (or simulate.py's seed_state) most likely never read the world the scenario was seeded with, "
                               "so verdicts resting on those reads grade the environment; if that record truly owns no such rows, "
                               "the read was honest — the per-row map in run_summary.json names the tools"),
    # The mock never answered some tool calls, so the agent read the runner's stand-in answers. The
    # per-call record (and what would get each answered) is a runner gate finding, internal — not this page's.
    "mock_unanswered_scenarios": ("the deployed mock never answered some tool calls, so the agent was served the runner's stand-in "
                                  "answers and the verdicts on those rows grade a world the mock did not produce — the "
                                  "per-row map in run_summary.json counts the calls per scenario"),
}

#: What each `measurement_unchecked_because` channel means. NOT a breach: the runner could not
#: MEASURE the channel on some row, so the verdict is `unknown` rather than `NOT clean`.
_UNCHECKED_CHANNEL = {
    "user_sim_integrity": "the evaluator-voice judge did not answer for some user turns, "
                          "so whether the simulator stayed in persona there was not decided — nothing departed, nothing was cleared; "
                          "re-run those scenarios to measure them",
    "tool_channel": "the agent's own tools (`agent.native_tools`) ran where no plurai lane sees them and no turn returned the "
                    "trail `plurai.confine` records, so every check on those tools abstained — nothing was called or cleared; "
                    "return that trail as `tool_trail` beside `host_effects`",
}

#: What either table above says about a channel this build has never heard of. The vocabulary is
#: the runner's and will grow, so an unknown channel is named and pointed at the file rather than
#: silently dropped.
_UNKNOWN_CHANNEL = "reason not known to this build — read run_summary.json"

#: How to read each outcome attribution. `environment` is NOT an acquittal, and the wording says
#: so: it means the family was not cleanly measured, never that the agent would have passed.
_ATTRIBUTION_READING = {
    # ADDITIVE, and it leads: a family holding a row that reached no verdict was not fully
    # measured, so no reading below it can be earned over that population. `incomplete` is the
    # same first-class row state the Per-scenario table marks ⏳ — kept iconned alike, because a
    # reader who has learned one mark should not have to learn it twice.
    "incomplete": "some scenarios here never reached a verdict — this family was not fully measured and is not a capability finding",
    # The deterministic check failed what the target judge passed on every scenario here (never the
    # reverse: a check that is only a necessary condition passing beside a judge's fail is the agent's). Rendered OUTSIDE the table below: the zero is the suite's to resolve.
    "disputed": "the deterministic check failed what the target judge passed on every scenario here — a suite finding to resolve before any reading of the agent",
    # From the DECLARATION (`tools: {}` in plurai.config.yaml, carried as run.json `declared_tools`),
    # never from the absence of activity: a render-action agent has no tools to observe, and
    # its families would otherwise read as a measurement gap asking for a `tool_trail`.
    "no_tools": "the agent declares no tools (`tools: {}`), so nothing here could be blocked by the environment — "
                "no tools to observe; this zero is the agent's or the suite's",
    "unobserved": "no tool activity was observed for ANY scenario here — this run cannot say whether the environment was the cause",
    "environment": "every scenario in this family called a tool that never once succeeded, so the family was never testable",
    "mixed": "some scenarios here called a tool that never once succeeded — only partly measured",
    "agent": "no scenario here was blocked by a dead tool — this is a finding about the agent",
    # ABSENCE, which is not one of the four verdicts and must not borrow one. A row that carries
    # no `attribution` key at all — an older producer, a hand-assembled spec, a build_run_json
    # that could not decide — must not render as `agent`, which would accuse the agent on the
    # strength of a missing key.
    "": "the row declared no attribution — this build cannot say who owns this zero",
}
_ATTRIBUTION_ICON = {"incomplete": "⏳", "disputed": "⚖️", "no_tools": "∅", "unobserved": "❔", "environment": "⛔", "mixed": "⚠️", "agent": "❌"}


#: The section a run with NO integrity signals gets. `run_integrity` is omitted whole when the
#: runner wrote none of these keys, and the producer's own docstring gives the reason: "so
#: 'clean' and 'nobody looked' do not render alike". Rendering nothing would make them alike — in a report whose every other absence (`—` on the rate cards, "no eval reached
#: a verdict" on the chart) is rendered as the reading it is, a missing section reads as a clean
#: one to every reader who does not know the section can be missing.
_NOBODY_LOOKED = ["_Nothing to report — and that is NOT a clean bill of health. This run recorded no "
                  "integrity signals at all (`run_summary.json` is unreadable, or predates them), so "
                  "whether it measured what it claims to was never asked. Re-run on a current runner to "
                  "find out._", ""]


def render_closed_class_detectors_md(spec: dict) -> list[str]:
    """Which value sets this run treated as CLOSED, and which of them anything checked in code.

    A closed class licenses a judge to red a value that is not on its list. That licence is
    powerful and, before the detector ladder, only URLs had anything behind it: every other class
    was a sentence in a judge's prompt with nothing checking it, and nobody had to say so. Two
    false failures came out of that.

    So the section states BOTH halves. A class with an active detector names the rule that fired;
    a class without one names the gate that refused it — because "not checked" is a prompt to act
    and the action (add members, declare a pattern, accept judge-only) depends entirely on which
    gate it was. Omitted whole when the run closes nothing, which is the common and honest case:
    an empty table here would invite the reading that something was missing.
    """
    block = spec.get('closed_class_detectors')
    if not isinstance(block, dict) or not block.get('classes'):
        return []
    total, checked = block.get('total') or 0, block.get('checked') or 0
    L = ['## Closed fact classes', '',
         f'_{checked} of {total} value set(s) this run treated as EXHAUSTIVE have a detector that decides membership '
         'IN CODE. For the rest the judge decided alone — legitimate, and the thing to read a red in those classes '
         'against._', '',
         '| Class | Values | Checked in code by |', '|---|---:|---|']
    for row in block['classes']:
        status = str(row.get('status') or '')
        if status == 'active':
            # THROUGH `_cell`, like every other cell here. A rule is `pattern <regex>`, and a
            # customer's own `closed_class_detectors` regex routinely carries an alternation —
            # `^(INV|CRN)-[0-9]{6}$` emitted a literal `|` and broke the row into four columns
            # against a three-column header.
            verdict = f'✅ `{_cell(str(row.get("rule") or "?"))}`'
        elif status == 'inconsistent':
            # Every scenario of a set is grounded on one registry, so a class cannot honestly
            # disagree with itself. It means the rows were scored against two — worth a loud line
            # rather than a collapse to whichever verdict happened to be read first.
            spread = ', '.join(f'`{k}` on {len(v)}' for k, v in sorted((row.get('inconsistent_across') or {}).items()))
            verdict = f'⚠️ INCONSISTENT across scenarios ({spread}) — this set was scored against more than one registry'
        else:
            verdict = f'❌ not checked — {_cell(str(row.get("reason") or status))}'
        name = _cell(str(row.get('alias') or row.get('class') or '?'))
        L.append(f'| `{name}` | {row.get("members") if row.get("members") is not None else "—"} | {verdict} |')
    return L + ['']


#: The SDK's home-disclosure keys on `host_effects` (`plurai.run_simulation._home_disclosure`), plus the
#: `identity_*` keys SDK 0.27.1-0.27.2 also wrote there: neither alone is a host-effects record, and the
#: identity ones are not rendered.
_DISCLOSURE_KEYS = ("disclosure", "disclosed_home_paths", "disclosure_scenarios",
                    "identity_disclosure", "disclosed_identity_tokens", "identity_disclosure_scenarios", "identity_spans")


def _home_disclosure_line(effects: object) -> str | None:
    """The `disclosure` bullet: `clean`, or `home_named` with its count and scenarios; `None` when the
    record carries no verdict (an older SDK)."""
    verdict = effects.get("disclosure") if isinstance(effects, dict) else None
    if verdict == "clean":
        return "- **Home folder** — ✅ `clean`: no saved artifact names your home folder outside the scenario"
    if verdict != "home_named":
        return None
    count = effects.get("disclosed_home_paths")
    rows = effects.get("disclosure_scenarios")
    named = ", ".join(f"`{_cell(str(s))}`" for s in rows) if isinstance(rows, list) and rows else ""
    return (f'- **Home folder** — ⚠️ `home_named`: {count if isinstance(count, int) else "some"} path(s) naming your home '
            "folder outside the scenario are in this run's saved artifacts" + (f" ({named})" if named else "")
            + ". The upload sends them as they are.")


def _customer(spec: dict) -> bool:
    """A FINAL run's analytics is the customer's (the page links it): how sure we are of our own measurement — the
    measurement verdict, a floored tool count, the simulator's fidelity — stays in run.json / run_summary.json, never here."""
    return spec.get("stage") == "final"


def _voided_trials(spec: dict, sid: str) -> tuple[int, int]:
    """`(trials voided, trials)` of scenario `sid`: a trial is voided when its row reached no verdict because the
    measurement was unclean (`not_evaluated` with goal status `unclean`). Its trial rows, else its one scenario row."""
    rows = _mapping(spec.get("trial_rows")).get(sid)
    if not isinstance(rows, list):
        rows = [r for r in spec.get("scenarios") or [] if isinstance(r, dict) and str(r.get("id")) == sid]
    rows = [r for r in rows if isinstance(r, dict)]
    voided = sum(1 for r in rows if r.get("result") == "not_evaluated" and _mapping(r.get("goal")).get("goal_status") == "unclean")
    return voided, len(rows)


def render_run_integrity_md(spec: dict) -> list[str]:
    """The runner's own integrity signals — written to `run_summary.json`; without this section
    a report says nothing about a `measurement_clean: false`, a
    corpus-drift check that never ran, an undeclared egress posture, or what actually
    ran on the HOST for real this run — a write into the operator's own project directory, or a
    binary proven to run fully outside the sandbox.

    Every other bullet here renders only when its key is present, and the World-state bullet
    deliberately does NOT: its absent state is a finding (nobody asked), and it is the one signal
    whose silence a reader cannot recover from the scores, because the backend grades a distrusted
    world and the verdicts look exactly like any other. That it is unconditional also means a
    non-empty block never renders as a bare heading — the state this section's own
    `_NOBODY_LOOKED` note exists to prevent, which a block carrying only keys this build does not
    render would otherwise reach."""
    block = spec.get("run_integrity")
    L = ["## Run integrity", "",
         "_Whether this run measured what it claims to. Separate from the scores: a run can be green and "
         "still not be a measurement._", ""]
    if not isinstance(block, dict) or not block:
        return L + _NOBODY_LOOKED
    if "measurement_clean" in block and not _customer(spec):
        clean = block["measurement_clean"] if isinstance(block["measurement_clean"], bool) else None
        L.append("- **Measurement** — " + {True: "✅ clean", False: "❌ NOT clean", None: "❔ unknown"}[clean])
        for channel in block.get("measurement_unclean_because") or []:
            # The rows, when the runner's per-row map was copied beside the verdict: which scenarios, by name.
            rows = block.get(channel) if isinstance(block.get(channel), dict) else {}
            named = "; ".join(f'`{_cell(str(sid))}`' + (f' ({", ".join(_cell(str(n)) for n in row["requirements"])})'
                                                         if isinstance(row, dict) and isinstance(row.get("requirements"), list) else "")
                              for sid, row in sorted(rows.items()))
            L.append(f"  - `{channel}` — {_UNCLEAN_CHANNEL.get(channel, _UNKNOWN_CHANNEL)}" + (f" — on {named}" if named else ""))
        for channel in block.get("measurement_unchecked_because") or []:
            L.append(f"  - `{channel}` NOT MEASURED — {_UNCHECKED_CHANNEL.get(channel, _UNKNOWN_CHANNEL)}")
        if clean is False and not block.get("measurement_unclean_because"):
            # Each channel IS a per-row map in run_summary.json, so the table above names them — a
            # hand-typed list here would miss any channel added later.
            maps = ", ".join(f"`{channel}`" for channel in _UNCLEAN_CHANNEL)
            L.append(f"  - the runner did not say which channel decided it — read `run_summary.json`'s per-row maps ({maps})")
    # THE SET ITSELF, off the set record the runner copied (`scenario_set`): shipped short of what was asked,
    # or a steering demand delivered short. No row for a full set, or from a runner that writes no record;
    # a record the runner could not read (`{}`), or one naming no requested count, says which.
    shape = block.get("scenario_set") if isinstance(block.get("scenario_set"), dict) else {}
    requested, shipped = shape.get("requested"), shape.get("shipped")
    short_demands = [d for d in shape.get("undelivered_demands") or () if isinstance(d, dict)]
    gaps = [f"shipped {shipped} of the {requested} scenarios asked for"] if isinstance(requested, int) and isinstance(shipped, int) and shipped < requested else []
    if short_demands:
        gaps.append("steering demand(s) delivered short: " + ", ".join(
            f'`{_cell(str(d.get("subject") or d.get("kind")))}` ({d.get("delivered")} of {d.get("slots")})' for d in short_demands))
    if gaps:
        L.append("- **Scenario set** — ❌ " + "; ".join(gaps))
    elif "scenario_set" in block and not shape:
        L.append("- **Scenario set** — ❔ shape not read: this run could not read the set's own record, so whether "
                 "it shipped every scenario and steering demand asked for is not shown here")
    elif "scenario_set" in block and not isinstance(requested, int):
        L.append("- **Scenario set** — ❔ the set's record names no requested count, so whether it shipped every "
                 "scenario asked for is not shown here")
    # WHOSE WORLD THE BACKEND WOULD NOT VOUCH FOR. The backend populates every cell on a row it did
    # not certify the world state for and SCORES it, so the row is graded, its reds are real, and
    # nothing else on this page is dented by the doubt — which is exactly why it has to be said
    # here or nowhere. THREE STATES, and the last two are the ones that must not merge: an empty
    # map is a runner that LOOKED, an absent key is a runner that never said, and rendering the
    # second as the first is the silent all-clear this whole section exists to remove.
    distrusted = block.get("untrustworthy_scenarios")
    if not isinstance(distrusted, dict):
        L.append("- **World state** — ❔ this run did not say whether any row was scored over a world state the "
                 "backend declined to certify; the rows below are silent on it, not cleared of it")
    elif not distrusted:
        L.append("- **World state** — ✅ every scored row ran against a world state the backend certified")
    else:
        # An uncertified world can VOID a trial (no verdict) rather than score it: said apart, because a voided trial
        # still sits in the pass count's denominator.
        trials = {name: _voided_trials(spec, str(name)) for name in distrusted}

        def named(names: list) -> str:
            return " · ".join(f'`{_cell(str(name))}`' + (f" ({_cell(str(distrusted[name]))})" if distrusted[name] else "") for name in names)

        voided = sum(v for v, _ in trials.values())
        if void := sorted(name for name, (v, _) in trials.items() if v):
            L.append(f'- **World state** — ❌ {len(void)} scenario(s) ran over a world state the backend did NOT '
                     f'certify, and {voided} of their trial(s) were NOT scored: {named(void)}. Those trials reached no '
                     'verdict: they count in the pass rate\'s denominator and never as a pass, so read that shortfall as '
                     'unmeasured, not as the agent failing.')
        if scored := sorted(name for name, (v, n) in trials.items() if n > v):
            L.append(f'- **World state** — ❌ {len(scored)} scenario(s) were SCORED over a world state the backend '
                     f'did NOT certify: {named(scored)}. Those verdicts are measurements — the cells landed — but they stand '
                     'over a world nothing corroborated' + ('' if voided else ', and no rate on this page is dented by that')
                     + '. Those rows are MARKED `⚠️ world uncertified` in Per scenario below — read them there, against '
                     'the world snapshot, before acting on one.')
    drift = block.get("scan_contract_drift")
    if isinstance(drift, dict):
        L.append(f'- **Corpus source drift** — {"✅" if drift.get("checked") else "❔"} '
                 f'{_cell(str(drift.get("summary") or ("checked" if drift.get("checked") else "NOT checked")))}')
    egress = block.get("egress")
    if isinstance(egress, dict) and not egress:
        # `{}` is the SDK's deliberate "no probe ran" (`plurai/run_simulation.py` writes the empty
        # record, and it is the default), and it took the branch below — which announces a probe
        # and reports that it said nothing. Two different findings: one is a probe that ran and
        # produced no verdict (fix the probe), the other is a run that never asked (turn it on).
        L.append("- **Egress isolation** — ❔ no egress probe ran on this run, so nothing is known about "
                 "what this agent talked to")
    elif isinstance(egress, dict):
        # A block WITHOUT a status is not the same as no block: it means the probe ran and said
        # nothing, and rendering it identically to silence is the shape this section exists to
        # remove. Same three-valued reading its `scan_contract_drift` neighbour already gets.
        status = f'`{_cell(str(egress["status"]))}`' if egress.get("status") else "❔ the probe reported no status"
        # A `guarded` verdict whose guard holds NO host by name (`deny_hosts: []`) blocks nothing and only
        # records. Read as `monitored`, never as guarded. Only when the SDK carried the list: absent, it says nothing.
        monitored = egress.get("status") == "guarded" and isinstance(egress.get("deny_hosts"), list) and not egress["deny_hosts"]
        if monitored:
            status = '`monitored`'
        # `breached` is the preflight's REACHABILITY verdict (a declared production host answered the probe and
        # nothing blocks it), not an observed leak: said as such, with the run-time coverage gap beside it.
        reachable = egress.get("status") == "breached"
        if reachable:
            status = '`reachable, not blocked`'
        # The counts render only when the KEYS are there. `.get(key, 0)` would MANUFACTURE the zero the
        # SDK deliberately omits, and the SDK says why (`plurai/run_simulation.py`): "the synthetic
        # records (an unusable declaration, a crashed preflight) have no host lists at all, and
        # writing `declared_hosts: 0` there states a measurement — 'we looked and found none
        # declared' — that nothing performed." So a crashed preflight would render as a clean sweep, in
        # the customer-facing section that exists SO THAT absence does not read as good
        # news. Each key is read independently: a probe that counted one side and not the other
        # says the one it counted, and nothing about the other.
        counts = [f'{egress[key]} {word}' for key, word
                  in (("declared_hosts", "declared"), ("undeclared_hosts", "undeclared")) if key in egress]
        # THE SWEEP IS THE FINDING under `swept_clean`, and "0 declared" is the least informative
        # true thing this line can say about it. `not_declared` and `swept_clean` both declare
        # nothing and mean opposite things — nobody looked, versus the scan read a NAMED roster and
        # found nothing to declare — so the roster's size leads. Read off `discovery.scanned`, the
        # artifact `record_egress.py` writes so "an absent key cannot say 'we looked'"; the SDK
        # carries it onto every record, and ignoring it would print the word
        # `not_declared` over a config that did sweep files.
        discovery = egress.get("discovery")
        scanned = discovery.get("scanned") if isinstance(discovery, dict) else None
        if egress.get("status") == "swept_clean" and isinstance(scanned, list) and scanned:
            counts.insert(0, f'swept {len(scanned)} file(s), nothing found')
        elif egress.get("status") == "not_determinable" and egress.get("not_determinable_cause") == "agent_runtime":
            counts.insert(0, 'the config does not say where the agent runs (`egress.agent_runtime`; re-run /simulation-scan-agent to '
                             'record it), so declared hosts were not probed and network isolation could NOT be confirmed')
        elif egress.get("status") == "not_determinable":
            # The scan could NOT DECIDE — a different fact from nobody looking (`not_declared`).
            why = discovery.get("detail") if isinstance(discovery, dict) else None
            counts.insert(0, 'the scan could not decide' + (f': {_cell(str(why))}' if why else '') + ' — NOT a clean sweep')
        elif monitored:
            # "Nothing was blocked" only when the run-time record agrees: an env-sourced endpoint set in the
            # agent child is still held, and its row below says `blocked`.
            rt = egress.get("runtime") if isinstance(egress.get("runtime"), dict) else {}
            held = bool(rt.get("blocked")) or any(isinstance(r, dict) and r.get("blocked") for r in rt.get("env_hosts") or ())
            counts.insert(0, 'no host is declared to block by name'
                             + ('; only env-sourced endpoints set in the agent process were blocked' if held else ', so nothing was blocked')
                             + '; every host the agent process reached is recorded below')
        elif reachable:
            rt = egress.get("runtime") if isinstance(egress.get("runtime"), dict) else {}
            coverage = rt.get("coverage") if isinstance(rt.get("coverage"), dict) else {}
            gaps = [str(c) for c in coverage.get("not_covered") or () if c]
            seen = ('whether the agent reached it is in the run-time record below'
                    + (f', which does not cover {", ".join(f"`{_cell(g)}`" for g in gaps)}' if gaps else '')
                    if egress.get("runtime") is not None else 'nothing recorded whether the agent reached it')
            counts.insert(0, 'a declared production host answered the reachability probe and nothing blocks it for this agent: '
                             f'route it through the mock or block it here; {seen}')
        elif egress.get("status") == "guarded" and isinstance(egress.get("runtime"), dict) and egress["runtime"].get("warning"):
            # The run-time record contradicts the claim (see its ⚠️ below), so the headline must not read fully guarded.
            counts.insert(0, 'NOT fully guarded: declared hosts were not probed, and the run-time record below names what the guard did not cover')
        elif egress.get("status") == "guarded":
            counts.insert(0, 'declared hosts blocked inside the agent process, not probed')
        L.append(f'- **Egress isolation** — {status}'
                 + (f' ({" / ".join(counts)})' if counts else " — the probe reported no host counts"))
        if egress.get("status") == "guarded" and egress.get("guard_scope"):
            L.append(f'  - Guard coverage: {_cell(str(egress["guard_scope"]))}')
        if isinstance(egress.get("live_by_consent"), list) and egress["live_by_consent"]:
            L.append('  - Live by consent (`egress.allow_hosts`), not blocked: '
                     + ', '.join(f'`{_cell(str(h))}`' for h in egress["live_by_consent"]))
        # What the agent process actually reached for, recorded by the SDK's in-process guard.
        runtime = egress.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("not_covered"), str):
            # No process in this run could observe anything (a non-Python harness): not "0 hosts".
            L.append(f'- **Egress observed at run time** — not covered ({_cell(runtime["not_covered"])})')
        elif isinstance(runtime, dict):
            hosts = runtime.get("observed_hosts") if isinstance(runtime.get("observed_hosts"), list) else []
            parts = [f'{len(hosts)} host(s)' + (': ' + ', '.join(f'`{_cell(str(h))}`' for h in hosts) if hosts else '')]
            blocked = [r for r in runtime.get("blocked") or () if isinstance(r, dict)]
            if blocked:
                parts.append(f'{len(blocked)} BLOCKED: '
                             + ', '.join(f'`{_cell(str(r.get("host")))}` at `{_cell(str(r.get("callsite")))}`' for r in blocked)
                             + (f' — {_cell(str(runtime["remedy"]))}' if runtime.get("remedy") else ''))
            coverage = runtime.get("coverage") if isinstance(runtime.get("coverage"), dict) else {}
            applied = [f'{scope} ×{coverage[scope]}' for scope in ("in-process", "python-subprocess")
                       if isinstance(coverage.get(scope), int) and coverage[scope]]
            if applied:
                parts.append('coverage: ' + ', '.join(applied))
            if isinstance(coverage.get("not_covered"), list) and coverage["not_covered"]:
                parts.append('not covered: ' + ', '.join(f'`{_cell(str(c))}`' for c in coverage["not_covered"]))
            for row in runtime.get("env_hosts") or ():
                if isinstance(row, dict):
                    parts.append(f'`{_cell(str(row.get("env")))}` → `{_cell(str(row.get("host")))}` '
                                 + ('blocked' if row.get("blocked") else 'not blocked'))
            if runtime.get("warning") or runtime.get("error"):
                parts.append(f'⚠️ {_cell(str(runtime.get("warning") or runtime.get("error")))}')
            L.append('- **Egress observed at run time** — ' + ' · '.join(parts))
            # Hosts nothing named (not declared, not allow-listed, not an env endpoint) — a library's own
            # fetch included. Their own line, so they are not lost in the observed list above.
            undeclared = runtime.get("undeclared_observed")
            if isinstance(undeclared, list) and undeclared:
                L.append(f'  - Undeclared hosts reached ({len(undeclared)}): '
                         + ', '.join(f'`{_cell(str(h))}`' for h in undeclared)
                         + ' — not in the egress declaration; reached, not blocked')
        # A harness note for qualification runs; a final run's analytics is the customer's page.
        L.extend([] if _customer(spec) else _payload_codec_lines(runtime))
    # THE COUNTS ABOVE AND BELOW ARE A FLOOR. The union that builds every tool number in this
    # report reports the rows it could not verify, and stderr alone does not carry that receipt —
    # so the Summary card's tool-error rate, the per-scenario Tool-calls column and `dead_tools`
    # (hence the whole outcome attribution) were all computed from a lower bound presented as a
    # measurement. Absent on the ordinary run, where the union verified everything.
    floor = block.get("tool_count_floor")
    if isinstance(floor, dict) and floor and not _customer(spec):
        named = " · ".join(f'`{_cell(str(name))}` ×{n}' for name, n in sorted(floor.items()))
        L.append(f'- **Tool counts** — ⚠️ a FLOOR, not a measurement: {sum(v for v in floor.values() if isinstance(v, int))} '
                 f'self-reported call(s) could not be matched to the mock event log by `call_id` and were reconciled on the '
                 f'TOOL NAME ({named}). Nothing can tell a duplicate of a logged call from a retry the mock never saw, so '
                 "every tool number in this report — the error rate, the per-scenario column, the dead/unproven split — is a "
                 "lower bound. Emit `call_id` on both lanes to count them.")
    # NOT AN ERROR AND NOT A CLEAN CALL. A trail row with no harness status stamp whose prose is
    # in a script the English-keyed transport vocabulary cannot read had its outcome decided by
    # nothing at all — and scored `errors: 0`, which is what a working tool scores. Reported
    # beside the floor because it is the same class of fact: a number above that is not the
    # measurement it looks like.
    unread = block.get("unclassified_dispatch_outcomes")
    if isinstance(unread, dict) and unread:
        named = " · ".join(f'`{_cell(str(name))}` ×{n}' for name, n in sorted(unread.items()))
        L.append(f'- **Dispatch outcomes** — ❔ {sum(v for v in unread.values() if isinstance(v, int))} dispatch outcome(s) '
                 f'in a script the transport vocabulary cannot read ({named}): the harness stamped no status and the '
                 "failure detector is English-keyed, so these calls are NOT counted as errors and NOT counted as clean. "
                 "Read the tool numbers above as silent about them. Emit a `status` on the trail to state the outcome.")
    # A HARNESS fault, not a count caveat: no tool result reached a judge (`build_run_json.tool_trail_results_missing`).
    blind = block.get("tool_trail_results_missing")
    if isinstance(blind, dict) and blind.get("entries"):
        L.append(f'- **Tool results** — ❌ harness: all {_cell(str(blind["entries"]))} tool-trail entries whose dispatch did not '
                 "fail carry no `result`, so every judge graded the tool calls without what they returned. Record each "
                 "tool's returned value as the entry's `result` at the tool wrapper, then re-run.")
    # What this host gave the agent of what its own files declare it needs, resolved by the runner BEFORE the run.
    # Each gap's remedy is the resolver's own, printed as given (a record from an older SDK carries none).
    gaps = [r for r in _list(_mapping(block.get("host_toolchain")).get("requirements")) if isinstance(r, dict) and r.get("status") != "present"]
    if gaps:
        named = "; ".join(f'`{_cell(str(r.get("name")))}` {_cell(str(r.get("status")).replace("_", " "))} — {_cell(str(r.get("detail") or "no detail"))}'
                          + (f' — {_cell(str(r["remedy"]))}' if r.get("remedy") else "")
                          for r in gaps)
        L.append(f'- **Host toolchain** — ❌ {named}.')
    # WHAT RAN FOR REAL ON THIS MACHINE: the SDK's record (`plurai.run_simulation._host_effects_summary`
    # / `_union_host_effects`), copied from `run_summary.json`. `writes` / `writes_observed` are READ,
    # never recomputed: the SDK already reads a nonzero `cli_own` as `escaped`, and a program the
    # operator let out of the Bash sandbox (`unsandboxed_grant`) keeps writes out of `confined` and
    # reads out of `scoped` (`reads_not_confined` names it as `bash:<program>`). The
    # counts are rendered beside the verdict, because the word alone hides where a write landed.
    #
    # The Bash-sandbox grant is rendered as what was DECLARED and GRANTED, never as a cause: nothing
    # on the run can tell that a program failed because of the sandbox, so no line claims it did.
    #
    # Every key is read with `.get()`: a record from an older SDK lacks `cli_own` or the grant keys,
    # and each such sub-bullet is silent rather than a manufactured `0` / `[]`.
    effects = block.get("host_effects")
    # WHAT THE SAVED ARTIFACTS NAME OF THE OPERATOR'S HOME (`disclosure`, `disclosed_home_paths`,
    # `disclosure_scenarios`): read by the SDK off the files that ship, for every agent shape, harness
    # or not — so these keys alone are no host-effects record. Its own axis: a path named is not a
    # path read. A warning, never a gate; an older SDK's record carries none and renders nothing.
    disclosure = _home_disclosure_line(effects)
    if disclosure:
        L.append(disclosure)
    if isinstance(effects, dict):
        effects = {k: v for k, v in effects.items() if k not in _DISCLOSURE_KEYS}
    copy = _agent_host_copy_sentence(block.get("agent_host_copy"))
    if isinstance(effects, dict) and not effects:
        L.append(f"- **Host effects** — ❔ {copy}" if copy else "- **Host effects** — ❔ no host-effects record on this run, so nothing is known "
                 "about what ran for real on this machine")
    elif isinstance(effects, dict):
        writes = effects.get("writes")
        icon = {"confined": "✅", "escaped": "❌", "unobserved": "❔"}.get(str(writes), "❔")
        writes_observed = effects.get("writes_observed")
        observed: dict = writes_observed if isinstance(writes_observed, dict) else {}
        counts = [f'{observed[key]} {key}' for key in ("scenario_cwd", "scenario_shell_home", "cli_own", "outside") if key in observed]
        L.append(f'- **Host effects — writes** — {icon} `{_cell(str(writes or "unknown"))}`'
                 + (' (' + ' · '.join(counts) + ')' if counts else ''))
        if copy:
            L.append(f"  - {copy}")
        # independent of the headline word above: the COUNT says what happened and where
        if isinstance(observed.get("cli_own"), int) and observed["cli_own"]:
            L.append(f'  - ⚠️ the CLI itself wrote {observed["cli_own"]} session record(s) into your real '
                     "`~/.claude/projects/…` during this run — outside the scenario folders. This is its own "
                     "runtime bookkeeping, not a tool call the agent issued, and it is what the writes verdict "
                     "above is reading off.")
        grant = effects.get("unsandboxed_grant")
        let_out = grant.get("names") if isinstance(grant, dict) else None
        if isinstance(let_out, list) and let_out:
            L.append("  - ⚠️ run outside the Bash sandbox by your grant, with full read, write and network access to "
                     "this machine: "
                     + ", ".join(f'`{_cell(str(c))}`' for c in let_out)
                     + " (any arguments, including ones that make it run other commands; only a Bash command "
                     "made up entirely of them leaves the sandbox), "
                     "so what they read and wrote is not observed")
        sandboxed = effects.get("sandboxed_executables")
        if isinstance(sandboxed, list) and sandboxed:
            L.append("  - declared by your agent, ran inside the Bash sandbox without a grant: "
                     + ", ".join(f'`{_cell(str(c))}`' for c in sandboxed)
                     + " — a program that needs OS services the sandbox withholds fails there, sometimes "
                     "silently; to let one out, add it to `consent.unsandboxed` in `plurai_sim/bench_manifest.json`")
        granted = effects.get("granted")
        if isinstance(granted, list) and granted:
            L.append("  - Host-effect tools granted LIVE this run (operator consented): "
                     + ", ".join(f'`{_cell(str(g))}`' for g in granted))
        reads = effects.get("reads")
        surfaces = effects.get("reads_not_confined")
        if isinstance(reads, str) and reads != "scoped":
            named = (" — not confined: " + ", ".join(f'`{_cell(str(n))}`' for n in surfaces)
                     if isinstance(surfaces, list) and surfaces else "")
            L.append(f'  - **Reads** — ❌ `{_cell(reads)}`: not confined to the scenario folder{named}')
        mcp = effects.get("mcp")
        if isinstance(mcp, str) and mcp != "declared_only":
            L.append(f'  - **MCP** — ❌ `{_cell(mcp)}`: may have loaded servers beyond what the agent declared')
    for risk in [r for r in block.get("named_risks") or [] if isinstance(r, dict) and r.get("kind") == "shared_world"]:
        L.append(f"- **Shared world** — ⚠️ {_shared_world_sentence(risk)}")
    for risk in [r for r in block.get("named_risks") or [] if isinstance(r, dict) and r.get("kind") == "unobserved_seed"]:
        n = len(risk.get("scenarios") or [])
        L.append(f"- **Starting state** — ⚠️ not verified on {n} scenario(s): the harness applies the scenario's starting rows "
                 "to its own store, and exports no `world_snapshot` to read them back, so whether they landed was not checked.")
    stability = block.get("stability")
    if block.get("stability_comparable") is False:
        # A retry ran on other conversations' writes, so it does not reproduce or clear anything.
        L.append(f'- **Failure stability** — {_not_comparable(block.get("stability_comparable_reason"))}')
    elif isinstance(stability, dict) and stability:
        marks = Counter(str(row.get("stability") or "unknown") for row in stability.values())
        L.append("- **Failure stability** — " + " · ".join(f"{n} {_cell(k)}" for k, n in sorted(marks.items())))
        # The per-POLICY grain, run-wide: which reds reproduced, which did not, which the retry
        # added. The scenario marks above are derived from these and cannot say which red they
        # describe; the per-scenario table shows the same marks beside each failing eval.
        # The mapping guard comes FIRST: a list-valued (or otherwise malformed) `policy_reproduction`
        # in a hand-edited row raised on `.values()` before the guard ran and killed the render.
        # `valid_marks` is that guard AND the validity one — a mark whose own producer stamped
        # `marks_valid: false` is not a measurement, and counting it here would publish a run-wide
        # reproduction tally over comparisons the runner has already disproved.
        per_policy = Counter(
            str(mark)
            for row in stability.values()
            for mark in (valid_marks(row, "policy_reproduction") or {}).values()
        )
        if per_policy:
            # In the vocabulary's order (reproduced first), then any mark this build does not know.
            order = {mark: i for i, mark in enumerate(_REPRODUCTION_MARK)}
            selections = {row.get("selection") for row in stability.values() if isinstance(row, dict)}
            selection = next(iter(selections)) if len(selections) == 1 else None
            L.append("  - per policy: " + " · ".join(
                f"{n} {_cell(_mark_text(k, selection))}"
                for k, n in sorted(per_policy.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))))
        # WITHDRAWN marks are NAMED, never merely absent. A row whose per-policy comparison was
        # computed across a rescore boundary has had it removed by `build_run_json`, and silence
        # there is indistinguishable from a runner that never measured reproduction at all — the
        # difference being that here somebody DID retry and the answer is not readable.
        withdrawn = sorted(name for name, row in stability.items() if isinstance(row, dict) and marks_disowned(row)
                           and row.get("marks_withdrawn") != _REFOLDED_ONE_REVISION)
        # Every trial judged at ONE revision: the counts compare, only the run-time marks were not re-derived — no
        # remedy is owed, so none is named.
        if refolded := sorted(name for name, row in stability.items() if isinstance(row, dict) and row.get("marks_withdrawn") == _REFOLDED_ONE_REVISION):
            L.append(f'  - per-policy marks not re-derived on {len(refolded)} scenario(s) '
                     f'({" · ".join(f"`{_cell(str(n))}`" for n in refolded)}): every trial is judged at one eval revision, '
                     "so the counts above compare one eval set; the per-policy reproduction marks the run computed before "
                     "a re-score or correction changed them are left out.")
        if withdrawn:
            L.append(f'  - ⚠️ per-policy marks WITHDRAWN on {len(withdrawn)} scenario(s) '
                     f'({" · ".join(f"`{_cell(str(n))}`" for n in withdrawn)}): nothing can vouch for a comparison '
                     "between their two attempts — the two were scored under different eval revisions, or one of them "
                     "carries none. The first attempt's verdicts above are current; nothing here says what reproduced. "
                     + ("Re-run the scenario to compare its trials again."
                        if all(stability[n].get("selection") == _UNCONDITIONAL for n in withdrawn) else
                        "Score the retry (`stability_retry/`) under the SAME eval revision as the first attempt and the "
                        "marks are recomputed; otherwise re-run the scenario to decide it."))
        # THE OPPOSITE NEWS, and it is named for the same reason the withdrawal is: a restored
        # comparison is PARTIAL by construction — `score_run` re-runs a deterministic check over
        # the same saved shape, so those marks are tautologies — and a reader who takes the table
        # for a full one reads a `code_eval`'s agreement with itself as corroboration.
        recomputed = sorted(name for name, row in stability.items()
                            if isinstance(row, dict) and row.get("marks_recomputed"))
        if recomputed:
            revisions = sorted({str(stability[n]["marks_recomputed"]) for n in recomputed})
            L.append(f'  - ♻️ per-policy marks RECOMPUTED on {len(recomputed)} scenario(s) '
                     f'({" · ".join(f"`{_cell(str(n))}`" for n in recomputed)}) from both attempts\' rescores at '
                     f'{"one eval revision" if len(revisions) == 1 else "their own eval revisions"} '
                     f'({" · ".join(f"`{_cell(r)}`" for r in revisions)}): the comparison holds again. PARTIAL — the '
                     "reds marked `deterministic` re-run a fixed expression over a saved shape and corroborate nothing.")
        # The retry's own goal verdict, typed by the runner (`goal_reproduction`). An annotation:
        # the first attempt stays the one reported, and a flip is not one more added red.
        flipped = sorted(name for name, row in stability.items()
                         if isinstance(row, dict) and row.get("goal_reproduction") == "retry_flipped")
        if flipped:
            L.append(f'  - goal verdict disagreed across attempts on {len(flipped)} scenario(s) '
                     f'({" · ".join(f"`{_cell(str(n))}`" for n in flipped)}): '
                     f'{"another trial" if _against_trials([stability[n] for n in flipped]) else "the retry"} reached the other goal '
                     "verdict; the first attempt's stays the one reported.")
        unmeasured = [row for row in stability.values() if row.get("stability") == "unmeasured"]
        if unmeasured:
            # GROUPED BY THE TYPED REASON, never by sentence. Grouping by `detail` rendered one
            # producer's wording as evidence — "3 of 3 completed scenarios failed — a suite failing
            # wholesale is one infrastructure event" — on a run where every failure was a judge's
            # and nothing had measured an infrastructure cause. `shed_reason` is the fact; the
            # infrastructure shed additionally states the SIGNALS it was decided on.
            #
            # AND SPLIT FIRST on whether the retry was SPENT (`_retry_was_spent`). "charged on a
            # single attempt" would be wrong over EVERY unmeasured row: the SDK reaches the
            # verdict from three paths on which the retry ran — so the line directly above,
            # "1 ✘ not reproduced on retry · 1 ＋ retry-only", would contradict it on the same page.
            retried = [row for row in unmeasured if _retry_was_spent(row)]
            charged = [row for row in unmeasured if not _retry_was_spent(row)]
            if retried:
                # The header states only what the whole group shares — the retry was SPENT and the
                # row is still undecided. "nothing decidable reproduced" is true of
                # `no_decidable_reproduction` and overstates the other two (`first_attempt_invalid`
                # had nothing to compare against; `retry_measured_nothing` measured none of it), so
                # WHICH it was is left to each row's own line below.
                L.append(f"  - {len(retried)} failure(s) unmeasured across the other trials — they ran and did not settle them:"
                         if _against_trials(retried) else
                         f"  - {len(retried)} failure(s) unmeasured AFTER a retry — the retry ran and did not settle them:")
                for row in retried:
                    L.append(f"    - {_retried_row_reading(row)}")
            groups: dict[str, list[dict]] = {}
            for row in charged:
                groups.setdefault(str(row.get("shed_reason") or ""), []).append(row)
            if charged:
                L.append(f"  - {len(charged)} failure(s) charged on a single attempt:")
            for reason, rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                unknown = f"shed reason `{_cell(reason)}` not known to this build — read run_summary.json"
                reading = _SHED_READING.get(reason, _SHED_UNTYPED if not reason else unknown)
                line = f"    - {len(rows)}× {_cell(reason) if reason else 'untyped'}: {reading}"
                if reason in _SHED_MEASURED:
                    # `shed_signals` is the RUN-level per-signal count the shed was decided on,
                    # stamped on every row it refused — so the rows are merged (max), never summed:
                    # two refused rows carrying `errored: 2` describe the same two errored rows.
                    signals: dict[str, int] = {}
                    for row in rows:
                        if isinstance(row.get("shed_signals"), dict):
                            for k, v in row["shed_signals"].items():
                                if isinstance(v, int):
                                    signals[str(k)] = max(signals.get(str(k), 0), v)
                    line += (" — measured: " + ", ".join(f"{_cell(k)} ×{n}" for k, n in sorted(signals.items()))
                             if signals else " — the rows carry no `shed_signals`; the signals the shed engaged on are not recorded")
                L.append(line)
    # Per FAMILY, because that is the grain a failure is actionable at: three stable failures in
    # one family is a defect with an address, three `unmeasured` across three families is a run
    # that never asked whether any of them reproduces.
    #
    # FAILURES AND DOUBT ARE SEPARATE COLUMNS. The runner files a stability row for every scenario
    # that earned a retry, typed `eval_failed` / `incomplete` / `invalid_run` — and this column
    # counted all three as failures, so a family whose scenarios ran out of TIME read as a family
    # whose scenarios failed, which is the opposite remedy. The split arrives ALREADY MADE from
    # `build_run_json.family_stability` (`failures` / `in_doubt` / `also_in_doubt`), so this
    # renderer never names the reason that means "graded and lost" — that string has one owner.
    by_family = block.get("family_stability")
    against_trials = _against_trials(list(stability.values()) if isinstance(stability, dict) else [])
    if isinstance(by_family, list) and by_family:
        L += ["", f'| Flow family | Failures | Also in doubt | First-vs-{"other trials" if against_trials else "retry"} |', "|---|---|---|---|"]
        for row in by_family:
            marks = " · ".join(f"{n} {_cell(k)}" for k, n in (row.get("stability") or {}).items())
            # `?` when the key is ABSENT (a run.json from a build that predates the split) and `—`
            # when it is present and empty: this renderer cannot tell "no other reason" from
            # "never written", and rendering the second as the first invents a measurement.
            rest = row.get("also_in_doubt")
            if not isinstance(rest, dict):
                other = "?"
            else:
                other = " · ".join(f"{n} `{_cell(k)}`" for k, n in sorted(rest.items())) if rest else "—"
            L.append(f'| `{_cell(str(row.get("family", "?")))}` | {row.get("failures", 0)} | {other} | {marks} |')
        L.append("")
        L.append("A scenario that CRASHED earns no stability row ("
                 + ("trial 1 has no result to compare with the other trials" if against_trials else "a retry is offered against a result, and there is none")
                 + "), so it is absent from this table rather than counted in it — read the per-scenario table for those. "
                 f"`Also in doubt` is the scenarios {'whose trial 1 was in doubt' if against_trials else 'that earned a retry'} WITHOUT being graded down: `incomplete` is the "
                 "per-scenario time budget cutting the dialog, `invalid_run` a first attempt that measured nothing.")
    return [*L, ""]


def render_user_sim_fidelity_md(spec: dict) -> list[str]:
    """Per scenario, every beat the plan carried: the turn that delivered it, or why nothing did.

    The headline says how many scenarios kept faith with their plan. This says, per beat, WHAT is
    missing from the dialog and WHY — a beat whose precondition the conversation never reached is
    a scenario that behaved correctly, a beat the turn budget cut off is a run that stopped short,
    and a turn list alone can show neither. `build_run_json` joins the two halves; this only
    formats them.

    Rendered for CLEAN scenarios too, unlike the headline: an all-delivered table is the evidence
    the ledger was read, and "every beat was delivered" must not look like "nobody compared".
    Absent whole when no scenario carried a beat ledger — there is nothing to compare then, and an
    empty table would read as a finding."""
    usi = spec.get("user_sim_integrity")
    if not isinstance(usi, dict) or not usi.get("measured"):
        return []
    delivery = usi.get("scripted_delivery")
    if not isinstance(delivery, dict) or not delivery:
        return []
    L = ["## User-sim fidelity", "",
         "_Every planted beat, as `beat → turn`. A ⚠️ beat was never voiced, and the reason beside "
         "it says whether that is the scenario behaving correctly (a precondition the dialog never "
         "reached) or the run losing coverage it was supposed to have — which no eval edit "
         "repairs._", "",
         "| Scenario | Beats (beat → turn) | Undelivered — why | Verdict |", "|---|---|---|---|"]
    for name in sorted(delivery):
        beats = [b for b in (delivery.get(name) or []) if isinstance(b, dict)]
        # NOT `_names`: it adds the code spans, and `_beat_cell` already carries its own.
        cells = ", ".join(_beat_cell(beat) for beat in beats)
        L.append(f'| `{_cell(str(name))}` | {cells or "—"} | {_beat_reasons_cell(beats)} | {_beat_verdict(beats)} |')
    return [*L, ""]


def _beat_cell(beat: dict) -> str:
    """One beat as `id → turn`, carrying its KIND, and marked when nothing delivered it.

    The kind rides the id because the three fail for different reasons and cost different things:
    a missing premise beat is the user's own arc going unplayed, a missing knowledge question is
    retrieval never exercised, a missing absence beat is a verified-absence probe never asked."""
    name = f'`{_cell(str(beat.get("beat")))}`' + (f' ({_cell(str(kind))})' if (kind := beat.get("kind")) else "")
    landed = beat.get("landed")
    return f'{name} → `{_cell(str(landed))}`' if landed is not None else f'⚠️ {name} → never'


#: What an uncredited beat reads as when the row names no reason — a stored run, whose ledger is
#: credit-only, or a hand-authored run.json. Said out loud rather than left blank: an empty reason
#: column reads as "nothing to say about this", which is the opposite of what it means.
_UNRECORDED_REASON = "unknown"


def _beat_reasons_cell(beats: list[dict]) -> str:
    """Why each uncredited beat was never voiced, beat by beat.

    The reason is the PRODUCER's token, rendered verbatim rather than translated through a table
    here: the vocabulary is the backend's and it grows, and a reason this side has not learned yet
    must reach the page as itself rather than as no sentence at all."""
    missing = [b for b in beats if b.get("landed") is None]
    return ", ".join(f'`{_cell(str(b.get("beat")))}`: `{_beat_reason(b)}`' for b in missing) or "—"


def _beat_reason(beat: dict) -> str:
    """One row's reason, table-safe. Shared by the column and the counts so a beat cannot be
    listed under one token in the cell and another in the verdict beside it."""
    return _cell(str(beat.get("reason") or _UNRECORDED_REASON))


def _beat_verdict(beats: list[dict]) -> str:
    """What the row amounts to — and NEVER "early". Release is by plan order and precondition, so
    a beat has no due turn to miss; the only question a delivery ledger answers is whether each
    beat was voiced at all, and the reasons say what a bare count cannot."""
    # NOTHING RECORDED IS NOT A CLEAN LEDGER. An empty beat list would reach the clean branch and
    # publish a green verdict beside an em-dash cell — over a comparison that has no rows to
    # make. The row still renders (a scenario whose plan produced no beats is itself worth
    # seeing), but it renders as the unknown it is.
    if not beats:
        return "❔ no scripted beats recorded"
    missing = [b for b in beats if b.get("landed") is None]
    if not missing:
        return f'✅ {len(beats)} beat(s) delivered, none undelivered'
    return f'⚠️ {len(missing)} of {len(beats)} beat(s) undelivered ({_counted_reasons(missing)})'


def _counted_reasons(beats: list[dict]) -> str:
    """`reason×n`, the same shape `anomaly_kinds` renders in on the headline."""
    counted: dict[str, int] = {}
    for beat in beats:
        reason = _beat_reason(beat)
        counted[reason] = counted.get(reason, 0) + 1
    return ", ".join(f'{reason}×{n}' for reason, n in sorted(counted.items()))


def _undelivered_pointer(delivery: object) -> str:
    """The fidelity headline's tail: how much of the plan never reached the dialog, run-wide.

    THREE STATES, and the middle one is why this is not a bare count. No ledger at all is NOT
    "0 undelivered" — nobody compared, and a zero there would clear a plan nothing read. A ledger
    with every beat credited says so. A ledger with holes names them with the producer's own
    reasons, because a reader deciding whether to re-run needs to know which kind of hole it is
    before opening the table.
    """
    if not isinstance(delivery, dict) or not delivery:
        return " — no per-beat delivery ledger was published, so no beat is compared"
    missing = [beat for beats in delivery.values() for beat in (beats if isinstance(beats, list) else [])
               if isinstance(beat, dict) and beat.get("landed") is None]
    if not missing:
        return " — User-sim fidelity below has the per-beat ledger; no beat is recorded undelivered"
    return f' — User-sim fidelity below: {len(missing)} beat(s) undelivered ({_counted_reasons(missing)})'


#: What would actually close the verb-surface gap, per `build_run_json.SURFACE_*` state. Six
#: states reach this section and only two of them are closed by `--workdir`; the page told all
#: six to "Re-run with `--workdir`", which on a run that already passed it — over a config that
#: was read and declares neither key — is advice already taken, attached to a sentence ("the
#: agent's declaration was not read") that is false of that run.
_SURFACE_UNKNOWN_REMEDY = {
    "no_workdir": "This render was given no `--workdir`, so nothing pointed at the config: re-run it with "
                  "`--workdir` and the names above resolve.",
    "no_config": "No `plurai.config.yaml` was found under the `--workdir` this render was given — point it at the "
                 "directory the run used.",
    "no_reader": "`pyyaml` is not importable in the interpreter that built this run, so the config was never "
                 "parsed. Install it and re-build; `--workdir` is not what is missing.",
    "unreadable": "The config IS present and could not be parsed — the build note names the error. Fix the file "
                  "and re-build; re-running with `--workdir` changes nothing.",
    "not_a_mapping": "The config IS present and does not parse to a mapping — the build note names what it parsed "
                     "as. Fix the file and re-build; re-running with `--workdir` changes nothing.",
    "declares_neither": "The config WAS read and declares neither `tools:` nor `render_actions:`, so there is no "
                        "declaration for these names to be checked against. Re-running changes nothing: declare "
                        "the agent's verbs in `plurai.config.yaml`.",
}


def _surface_unknown_remedy(reason: object) -> str:
    """The remedy clause for one no-surface state — silence for a run.json that names none.

    A build predating the typed reason carries no key, and the honest thing to print then is
    nothing: the previous behaviour was to name a remedy for a state nobody had recorded.
    """
    return _SURFACE_UNKNOWN_REMEDY.get(str(reason), "")


#: The same six states, read for the `tools:` DECLARATION rather than the verb surface — the two
#: readings share a file and a set of failure modes but not a remedy sentence, and one map worded
#: for verbs ("the names above resolve") is wrong on a page that named no names.
_DECLARED_TOOLS_UNKNOWN_REMEDY = {
    "no_workdir": "This build was given no `--workdir`, so `plurai.config.yaml` was never opened: re-build with "
                  "`--workdir` and these rows decide between `no_tools` and a real gap.",
    "no_config": "No `plurai.config.yaml` was found under the `--workdir` this build was given — point it at the "
                 "directory the run used.",
    "no_reader": "`pyyaml` is not importable in the interpreter that built this run, so the config was never parsed. "
                 "Install it and re-build; `--workdir` is not what is missing.",
    "unreadable": "The config IS present and could not be parsed — the build note names the error. Fix the file and "
                  "re-build; re-running with `--workdir` changes nothing.",
    # TWO states share this token: a config that does not parse to a mapping at all, and a `tools:`
    # key carrying something that is not one. Worded for the second alone, it sent an operator whose
    # config has no `tools:` key to go and fix a key that is not there. The build note on stderr is
    # what distinguishes them, so the remedy points at it rather than guessing which one happened —
    # the same shape as its `verb_surface` sibling one map up.
    "not_a_mapping": "The config IS present and did not parse into the shape this read needs — the build note on "
                     "stderr names what it found. Fix the file and re-build; re-running with `--workdir` "
                     "changes nothing.",
    "no_key": "The config WAS read and declares no `tools:` key at all, so nothing states whether this agent has a "
              "tool surface. Write `tools: {}` to declare an empty one; re-running changes nothing.",
}


def _declared_tools_unknown_remedy(reason: object) -> str:
    """The remedy for one unknown-declaration state — silence for a build that named none."""
    return _DECLARED_TOOLS_UNKNOWN_REMEDY.get(str(reason), "")


def render_outcome_attribution_md(spec: dict) -> list[str]:
    """Per ZEROED flow family, whether it was even testable.

    A family at 0/3 reads as a capability finding. It reads the same way when the flow's only
    data source was dead for the whole run, and then it is a fact about the environment's design
    (a dead endpoint zeroes every family that reads it, and without this section the report would
    tell the reader to look at the scenario or at what the agent refused).

    …and it is where `invented_verbs` lands, because that is the finding on the OTHER side of the
    same subtraction. A verb the declaration does not carry is struck out of `dead_tools` before
    the attribution runs, which is what flips a family from `environment` to `agent` — so a
    reader who never sees the names is handed the flipped verdict and none of its evidence: families
    read `❌ agent — no scenario here was blocked by a dead tool` over an agent that called verbs it
    does not have, with no name anywhere on the page."""
    rows = spec.get("outcome_attribution")
    rows = rows if isinstance(rows, list) else []
    # A DISPUTED family is not a capability zero of any reading the table offers, so it is named
    # beside the table rather than in it.
    disputed = [row for row in rows if isinstance(row, dict) and row.get("attribution") == "disputed"]
    rows = [row for row in rows if row not in disputed]
    invented = [str(v) for v in (spec.get("invented_verbs") or [])]
    # The other side of the same coin: with no readable declaration NOTHING may be called
    # invented, so these names stay in `dead_tools` and their families read `environment`.
    # Stderr alone is not the artifact the customer reads, so the page says the surface was
    # never looked at instead of carrying a confident environment finding with no trace of it.
    unknown = [str(v) for v in (spec.get("verb_surface_unknown") or [])]
    # Rendered even with NO zeroed family. `invented_verbs` is a fact about the agent's own
    # behaviour, not about a family's outcome, and it survives a run where every family passed —
    # gating the section on the table alone would drop it on exactly the run that has nothing
    # else wrong with it.
    refused = [str(v) for v in (spec.get("refused_tools") or [])]
    # A real tool whose every call failed, with its own error: said first and plainly, family or not.
    setup = run_numbers.setup_line(spec)
    setup_tools = {str(e.get("tool")) for e in spec.get("setup_failed_tools") or [] if isinstance(e, dict)}
    if not rows and not disputed and not invented and not unknown and not refused and not setup:
        return []
    dead = spec.get("dead_tools") or []
    L = ["## Outcome attribution", ""] + ([f"_{setup}_", ""] if setup else [])
    if disputed:
        named = ", ".join(f'`{_cell(str(row.get("family", "?")))}`' for row in disputed)
        L += [f'_{_ATTRIBUTION_ICON["disputed"]} {len(disputed)} famil(y/ies) scored 0 where {_ATTRIBUTION_READING["disputed"]}: '
              f"{named}. Read each scenario's `goal.goal_disagreement`; repair the deterministic check if it is "
              "wrong about the target, else the target judge._", ""]
    if not rows and not invented and not unknown and not refused:
        return L
    if rows:
        L += ["_Every flow family that scored 0. A family nothing could have passed is not a finding about the "
              "agent, and half an outcome distribution can belong to the environment's design._", ""]
    if invented:
        calls = spec.get("invented_verb_calls")
        calls = calls if isinstance(calls, dict) else {}
        named = ", ".join(f'`{_cell(v)}`' + (f' ×{calls[v]}' if isinstance(calls.get(v), int) and calls[v] else "")
                          for v in invented)
        L += [f'_Invented by the agent — {named}. `plurai.config.yaml` declares {"this verb" if len(invented) == 1 else "these verbs"} '
              "under neither `tools:` nor `render_actions:`, so nothing here was a capability the environment "
              "could have blocked: the agent reached for something it does not have and the engine refused it by "
              "name. Struck out of `dead_tools` BEFORE the attribution below — which is why those families read "
              "`agent` — and it is what the agent DID that they are attributing. Repair the agent (or declare the "
              "verb), never the environment._", ""]
    if unknown:
        said = [f'**Verb surface UNKNOWN** — no verb declaration was available, so none of '
                f'{", ".join(f"`{_cell(v)}`" for v in unknown)} could be checked against one. Any of them may be a '
                "verb the agent invented rather than a capability the environment failed to provide, and the "
                "attribution below reads `environment` for all of them because that is the only reading available "
                "without the declaration. This is a gap in what was measured, not a finding about the environment."]
        # The remedy is the one clause that DIFFERS between the six states, so it is appended
        # rather than baked in — and omitted entirely when the run.json names no state.
        said += [r for r in (_surface_unknown_remedy(spec.get("verb_surface_unknown_reason")),) if r]
        L += [f'_{" ".join(said)}_', ""]
    if dead:
        L += [f'_Dead in this run — fired repeatedly and never once returned: {", ".join(f"`{_cell(t)}`" for t in dead)}._', ""]
    # The dead-tool floor's NOT-ENGAGED state, rendered rather than left blank. A tool the run
    # dispatched once, which failed, is under `_DEAD_TOOL_MIN_CALLS`; left unreported it would roll
    # onto the healthy side, and its family's zero would be charged to the agent off n=1.
    unproven = spec.get("unproven_tools") or []
    # OWNERSHIP, not sample: dispatched enough, never worked, and nothing typed says the environment
    # left the calls unanswered — so "never asked twice" would be false of them.
    unowned = [t for t in (spec.get("unproven_tools_unowned") or []) if t in unproven and t not in setup_tools]
    if thin := [t for t in unproven if t not in unowned and t not in setup_tools]:
        L += [f'_Unproven — failed every dispatch, but the run never asked twice, so "dead" is not a claim it '
              f'earned: {", ".join(f"`{_cell(t)}`" for t in thin)}. A family these alone blocked caps at '
              "`mixed`; re-run it with the tool exercised more than once to decide._", ""]
    if unowned:
        L += [f'_Unowned — failed every dispatch, and nothing in the run says whose the failures were: '
              f'{", ".join(f"`{_cell(t)}`" for t in unowned)}. An error answer can be the tool refusing the arguments '
              "the agent sent or the mock failing on its own, and this run types neither. A family these alone "
              "blocked caps at `mixed`; read one call's arguments beside the mock's answer to decide._", ""]
    if refused:
        L += [f'_Rejected arguments — {", ".join(f"`{_cell(t)}`" for t in refused)}: the mock engine found the arguments '
              "the agent sent invalid for the tool's declared input on every call, so a family that called it reads "
              "`agent`. Read what the agent sent._", ""]
    # …and the ones for which even THAT is unearned. `unproven` claims the run never asked twice;
    # where the union suppressed a row that would have crossed the floor, the run may have asked
    # twice and nothing here can tell. The tool keeps the conservative attribution and loses the
    # claim about the sample, which is the only half a floored count can take away.
    on_a_floor = [] if _customer(spec) else [t for t in (spec.get("unproven_tools_floored") or []) if t in unproven]
    if on_a_floor:
        L += [f'_…and for {", ".join(f"`{_cell(t)}`" for t in on_a_floor)} even that is not established: the two '
              "recovery lanes could not be matched by `call_id`, so the dispatch count above is a FLOOR and the "
              "run may in fact have asked twice. Emit `call_id` on both lanes to decide it._", ""]
    coverage = spec.get("outcome_attribution_coverage")
    if isinstance(coverage, dict) and coverage.get("reported", 0) < coverage.get("population", 0):
        rest, unreadable = coverage["population"] - coverage["reported"], coverage.get("unreadable")
        why = ("produced no readable transcript, so they declare" if unreadable is None or unreadable >= rest else "declare")
        L += [f'_Computed over {coverage["reported"]} of {coverage["population"]} scenarios — the other {rest} {why} no flow family'
              + (f' ({unreadable} of them produced no readable transcript)' if unreadable and unreadable < rest else "")
              + ", so they are in none of these denominators._", ""]
    L += ["| Flow family | Scenarios | Passed | Reading |", "|---|---|---|---|"]
    for row in rows:
        # `or ""`, NOT `or "agent"`. An attribution this build does not know renders as UNKNOWN,
        # never as an accusatory default — and ABSENCE is the case such a
        # default gets wrong: a row with no `attribution` key, or with
        # an empty one, would render `❌ agent — no scenario here was blocked by a dead tool — this is
        # a finding about the agent`, a manufactured red over a producer that said nothing. The
        # empty string is a reading of its own below, so `_ATTRIBUTION_ICON.get(kind, "❔")` and
        # the reading map decide every case from here, absence included.
        kind = str(row.get("attribution") or "")
        # A disputed member is set aside from the family's reading, and named beside it.
        aside = [str(n) for n in (row.get("disputed_scenarios") or [])]
        beside = (f'; {_ATTRIBUTION_ICON["disputed"]} read without {", ".join(f"`{_cell(n)}`" for n in aside)}, where the deterministic '
                  'check failed what the target judge passed' if aside else '')
        L.append(f'| `{_cell(str(row.get("family", "?")))}` | {row.get("scenarios", 0)} | {row.get("passed", 0)} | '
                 f'{_ATTRIBUTION_ICON.get(kind, "❔")} {_cell(kind)} — '
                 f'{_ATTRIBUTION_READING.get(kind, "attribution not known to this build — read run.json")}{beside} |')
    unfinished = [row for row in rows if row.get("attribution") == "incomplete"]
    if unfinished:
        named = sorted({n for row in unfinished for n in (row.get("unmeasured_scenarios") or [])})
        # THREE ways to reach no verdict, and the sentence names all three: the clock, a crash,
        # and a scenario the backend reached and did not evaluate (nothing owned
        # its goal, or the measurement did not hold). Naming only the first two sent the reader
        # of an eval set that predates the goal owner hunting a budget and a traceback that do
        # not exist, on EVERY family of the run.
        L += ["", f"_⏳ {len(unfinished)} famil(y/ies) hold a scenario that never reached a verdict — cut by a budget, "
                  "errored, or evaluated against no goal. Their population was not measured through, so the zero "
                  "beside them is not a capability finding in either direction; read those rows' own reason before "
                  "reading it as one."
                  + (f' Unmeasured: {", ".join(f"`{_cell(n)}`" for n in named)}.' if named else "") + "_"]
    tool_less = [row for row in rows if row.get("attribution") == "no_tools"]
    if tool_less:
        L += ["", f"_∅ {len(tool_less)} famil(y/ies) have no tools to observe: `plurai.config.yaml` declares an empty tool surface. "
                  "That is the declaration, not an inference from silence — there is no environment behind these rows "
                  "to blame, so their zero reads as the agent's (or the suite's) without a `tool_trail`._"]
    unobserved = [row for row in rows if row.get("attribution") == "unobserved"]
    if unobserved:
        # WHOSE GAP IT IS depends on a fact this page may not have. `unobserved` is reached two
        # ways: a DECLARED tool surface that produced nothing (a real harness gap — instrument it),
        # and a declaration nobody read, where the agent may have no tools at all and there is
        # nothing to instrument. Printing "have the harness report `agent_state.tool_trail`" for both
        # would tell the reader to instrument something that does not exist, on an agent whose
        # `plurai.config.yaml` says `tools: {}` when the run was simply built without `--workdir`.
        why_unknown = spec.get("declared_tools_unknown")
        if why_unknown is not None or "declared_tools" not in spec:
            said = [f"_❔ {len(unobserved)} famil(y/ies) had NO observed tool activity — and THE DECLARATION WAS NEVER "
                    "READ, so this build cannot say whether that is a measurement gap or an agent with no tools at "
                    "all. Do not read it as either, and do not instrument a harness on the strength of it."]
            said += [r for r in (_declared_tools_unknown_remedy(why_unknown),) if r]
            L += ["", " ".join(said) + "_"]
        else:
            L += ["", f"_❔ {len(unobserved)} famil(y/ies) had NO observed tool activity at all, over an agent that "
                      f"DECLARES {spec['declared_tools']} tool(s). On an agent that runs its own tool loop this is a "
                      "measurement gap, not a clean environment — have the harness report `agent_state.tool_trail` "
                      "before reading these rows either way._"]
    env = [row for row in rows if row.get("attribution") == "environment"]
    if env:
        L += ["", f'_⛔ {len(env)} flow famil(y/ies) were zeroed by the environment, not by the agent. Their scenarios '
                  "are not evidence about capability — repair the environment and re-run before reading them as one._"]
    return [*L, ""]


def render_graded_nothing_md(spec: dict) -> list[str]:
    """The panel for policies that measured NOTHING set-wide, rendered from the backend's rollup.

    Distinct from the Eval-signal table above it, which classifies each eval from THIS run's
    verdicts — "did the judge discriminate on this sample". This one is the backend's
    scoring-time determination that a policy graded nothing at all, and it is copied verbatim
    (`detail`), never recomputed here: a second construction of the same finding drifts from the
    one the backend recorded.

    Empty list when the rollup is absent (an older backend) or carries nothing — a fully-grading
    run renders no panel. Six of eighteen policies graded nothing on a 25-66-minute run and the
    report said nothing at all; that silence is what this closes.
    """
    items = [i for i in (spec.get("coverage_debt") or []) if isinstance(i, dict)]
    if not items:
        return []
    blocking = spec.get("coverage_debt_blocking")
    L = ["## Graded nothing", "",
         "_Policies the backend recorded as measuring nothing across this set — not a verdict "
         "about the agent in either direction. Each line is the backend's own; nothing here is "
         "re-derived from the table above._", ""]
    L += ["| Policy / subject | Why it graded nothing | What would make it gradeable |", "|---|---|---|"]
    for item in items:
        kind = str(item.get("kind") or "?")
        subject = _cell(str(item.get("subject") or "?"))
        why = _cell(str(item.get("detail") or kind))
        remedy = _DEBT_REMEDY.get(kind, f"`{_cell(kind)}` — see the eval set")
        L.append(f"| `{subject}` | {why} | {remedy} |")
    # Scoped to the delivery set on purpose. The rollup's own rule 2 puts the verdict effect on
    # FINAL sets only: a qualification pass is a small sample that legitimately grades less, and
    # a demotion that fires on every qualification run demotes nothing.
    # Whether the rollup blocks is the backend's own bit (`coverage_debt_blocking`), and the cap is the
    # ROLLUP's, never each row's: a waived row does not block, and a waiver is the operator's decision and
    # never customer report content, so neither the waiver nor a per-row cap is said here.
    if blocking is not False:
        L += ["", "_On a FINAL/delivery set, this rollup caps the suite at "
              "`qualified-with-gaps`, never `validated`; on a qualification pass it is a reading, not "
              "a demotion — the rule lives in `/simulation-generate-suite` Step 6._", ""]
    else:
        L += [""]
    return L


def _names(values: list | None) -> str:
    """A list of already-table-safe values as code spans, or an em dash when there are none."""
    return ", ".join(f'`{v}`' for v in (values or [])) or "—"


#: `build_run_json.STABILITY_REFOLDED`: the row's every trial is judged at one eval revision.
_REFOLDED_ONE_REVISION = "refolded_one_revision"


def marks_disowned(row: dict) -> bool:
    """Does this stability row's OWN producer say its per-policy marks compare nothing?

    THE PREDICATE, spelled once, because this page counts the marks run-wide, prints them per
    scenario AND names the rows that lost them — three readers that must not disagree about which
    rows are countable.

    Two disqualifications, from two writers, and neither was being read here:

    * `marks_withdrawn` — `build_run_json` removed the marks because a rescore overtook them.
      Already safe, since the marks are gone with it; read anyway so the rule is stated once and
      not implied by an absence.
    * `marks_valid: False` — the RUNNER's own disproof (plurai `_marks_comparability`): the two
      attempts' finalizes named different eval revisions, or one named none. It rides a row whose
      marks are still THERE, so without this rule every one of them would be counted into the run-wide tally and printed
      beside a failing eval — a comparison the producer has already said compares nothing. `is
      False` only: an absent key is a runner too old to say, which is not a disproof.
    """
    return bool(row.get("marks_withdrawn")) or row.get("marks_valid") is False


def valid_marks(row: object, key: str) -> dict | None:
    """One stability row's `policy_reproduction` / `policy_evidence`, or None when nobody vouches."""
    if not isinstance(row, dict) or marks_disowned(row):
        return None
    value = row.get(key)
    return value if isinstance(value, dict) else None


def _reproduction_cell(mark: object, evidence: object, *, sample_kind: object, selection: object) -> str:
    """One red's reproduction mark, and WHAT it is evidence of.

    A mark on its own is not a measurement, and the two kinds of policy need opposite readings:

    * a `code_eval` on a judge replay re-runs one expression over one saved shape, so it cannot
      disagree: the evidence token REPLACES the mark. On an agent re-run (or an unstated kind)
      the check graded a second transcript, so the mark stays and says it is a deterministic one.
    * a judge samples K times per attempt and reports the majority fraction, so the pair rides
      the mark: `✔ reproduced` alone is P(red)², and a judge that catches a real defect 60% of
      the time reads "not reproduced" on 40% of the runs where it is right.

    An older runner records no `policy_evidence` at all, and the mark then renders without an
    evidence suffix — absence is not a claim that the policy was deterministic.
    """
    rendered = _cell(_mark_text(mark, selection))
    if not isinstance(evidence, str) or not evidence:
        return rendered
    if evidence == _DETERMINISTIC_EVIDENCE:
        if sample_kind == _JUDGE_REPLAY:
            return _cell(_DETERMINISTIC_CELL)
        on = _TRIAL_DETERMINISTIC if selection == _UNCONDITIONAL else _DETERMINISTIC_ON.get(str(sample_kind), 'deterministic check')
        return f"{rendered} ({_cell(on)})"
    return f"{rendered} ({_cell(evidence)})"


def _failing_evals_cell(names: list | None, reproduction: dict | None, evidence: dict | None, *, sample_kind: object,
                        selection: object) -> str:
    """The failing evals, each with its reproduction mark when the runner measured one.

    The mark rides the POLICY, beside the scenario verdict: a `stable` row mark says a red
    reproduced without saying which, and the one an agent finding is written from may be the
    one that did not. A `retry_only` red is not in `failing_evals` (that list is the
    first attempt's) and is appended so the retry's own finding is not lost.

    The marks are keyed by the BARE policy name and the list's entries may be annotated, so the
    join reads each entry through `policy_name`.
    """
    marks = reproduction if isinstance(reproduction, dict) else {}
    proof = evidence if isinstance(evidence, dict) else {}
    shown = {policy_name(v) for v in (names or [])}
    cells = [f'`{v}`' + (f' {_reproduction_cell(marks[n], proof.get(n), sample_kind=sample_kind, selection=selection)}'
                         if (n := policy_name(v)) in marks else "")
             for v in (names or [])]
    cells += [f'`{_cell(str(name))}` {_reproduction_cell("retry_only", proof.get(name), sample_kind=sample_kind, selection=selection)}'
              for name, mark in sorted(marks.items()) if mark == "retry_only" and name not in shown]
    return ", ".join(cells) or "—"


def _cell(value: str) -> str:
    """One free string in a markdown table cell. `|` ends the row and a backtick ends the code
    span, so neither may survive — the same rule `build_run_json._table_safe` applies on the
    way in, repeated here because run.json is hand-authorable and this side cannot assume it."""
    return value.replace("|", "/").replace("`", "'")


#: How much of one judge's own reason for a rooted charge reaches the row. The bound is
#: `build_run_json._REASONING_EXCERPT`'s, for the same reason and one worse: several of these
#: ride a SINGLE markdown table cell, so the unbounded form is a row the reader scrolls past.
_ROOTED_WHY = 220

#: How much of the quoted sentence reaches the Utterance column. Generous, because the quote IS
#: the finding here and a reader who cannot recognise the sentence cannot act on the row — but
#: bounded, because a judge quoting a turn sometimes quotes a whole tool payload with it.
_ROOTED_QUOTE = 300


def _charged_by_cell(charges: object) -> str:
    """The evals charging one utterance, each with its OWN reason.

    The reasons ride the row rather than being collapsed into one sentence: three judges
    charging one utterance charge it for three different things, and a root that printed only
    the names would read as "two of these are redundant". They are not — the finding is that one
    sentence broke three rules, and each rule's own words are what say which three.
    """
    parts = []
    for charge in charges if isinstance(charges, list) else []:
        if not isinstance(charge, dict):
            continue
        name = _cell(str(charge.get("eval", "?")))
        why = " ".join(str(charge["why"]).split()) if isinstance(charge.get("why"), str) else ""
        parts.append(f'`{name}` — {_cell(_elide(why, _ROOTED_WHY))}' if why else f'`{name}`')
    return " · ".join(parts) or "—"


def render_rooted_findings_md(spec: dict) -> list[str]:
    """Every charged agent utterance ONCE, with all the evals that charged it.

    `Failing evals` in the table above is a list of POLICIES, which is the right unit for
    "which evals are red" and the wrong one for "what did the agent do". When one sentence
    breaks three rules, three judges each file their own finding against it and the row reads as
    three failures — the reader counts three mistakes, and a repair round is scoped to three.
    This section is the other axis: the agent's own sentences, each printed once, with the
    charges against it underneath.

    ABSENT on a run whose rows carry no `rooted_findings` — an older `run.json` predates the
    field and a clean run earns no roots, and in both cases an empty section would assert this
    run was examined for something it was not.
    """
    rows = [(scenario, root)
            for scenario in (spec.get("scenarios") or []) if isinstance(scenario, dict)
            for root in (scenario.get("rooted_findings") or []) if isinstance(root, dict)]
    if not rows:
        return []
    charges = sum(len(root.get("charged_by") or []) for _s, root in rows)
    shared = sum(1 for _s, root in rows if len(root.get("charged_by") or []) > 1)
    L = ["## Rooted findings", "",
         "_Every agent utterance a judge charged, printed ONCE, with every eval that charged it. "
         "A single sentence can break several policies at once and each judge files its own "
         "finding against it, so one mistake arrives in the table above as N failing evals — N "
         "rows to a reader counting failures, and one sentence to the agent. NO VERDICT MOVES "
         "here: every eval keeps its own, `Failing evals` still names them all, and the violation "
         "count is still a count of cells. This is the index from those cells back to what the "
         "agent actually said._", "",
         "_A charge whose judge quoted no sentence gets its own row and is NEVER merged — with no "
         "quote there is no evidence it is about the same utterance as anything else on that turn. "
         "A violated eval that filed no finding at all (a `code_eval` over world state, say) named "
         "no utterance, so it has no row here and appears only in the table above._", "",
         # THE COUNT IS A CEILING, and a reader who is told a number treats it as exact. Two judges
         # quoting one sentence with different amounts of its neighbours are charging one slip, and
         # this table splits them — so the honest headline is "at most this many mistakes", never
         # "this many". Stated where the number is read, not only in the code that computes it.
         "_The utterance count is a CEILING on distinct mistakes. Two judges are read as charging "
         "one sentence when they quote it identically (whitespace and case aside); a judge that "
         "quotes a SPAN of what another quoted still gets its own row, so a reply charged that way "
         "counts more than once. Read a row's charges, not the total, before scoping a repair._", ""]
    # The Scenario column earns its place only when the roots come from more than one row;
    # on a single-scenario run it is the same string repeated down the page.
    named = {str(scenario.get("id")) for scenario, _root in rows}
    head = (["Scenario"] if len(named) > 1 else []) + ["Turn", "Utterance", "Charged by"]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for scenario, root in rows:
        quote = root.get("quote")
        L.append("| " + " | ".join([
            # A CODE SPAN, unlike the Per-scenario table's bare id: these are identifiers
            # beside quoted prose here, and a second table whose rows open with the same
            # bare `| <scenario id> |` is one a reader — and a line-scoped assertion —
            # cannot tell from the first.
            *([f'`{_cell(str(scenario.get("id", "?")))}`'] if len(named) > 1 else []),
            # 0-BASED, said on the header, because `failing_evals` says it too and a turn
            # number off by one sends the reader to the wrong exchange.
            str(root.get("turn_idx")),
            f'`{_cell(_elide(" ".join(str(quote).split()), _ROOTED_QUOTE))}`' if isinstance(quote, str) and quote
            else "— _the judge named this turn and quoted nothing_",
            _charged_by_cell(root.get("charged_by")),
        ]) + " |")
    # THE ARITHMETIC, stated rather than left to the reader, because the two numbers it relates
    # are three sections apart and the whole finding lives in the gap between them.
    L += ["", f'_Turn numbers are 0-BASED. {charges} charge(s) rooted on {len(rows)} utterance(s)'
          + (f' — {shared} utterance(s) were charged by more than one eval, so {charges - len(rows)} '
             'of those charges re-charge a sentence already on this table rather than name a further '
             'thing the agent did._'
             if shared else
             ' — each was charged by exactly one eval, so nothing here collapses: the charges and the '
             'utterances are the same count._'), ""]
    # …and the other half of the gap. A conclusively violated cell that filed NO finding is in
    # `Failing evals` and NOT here, so the two counts differ by a number this table cannot show.
    # Counted, never name-matched: `failing_evals` entries are ANNOTATED strings (`name (turns
    # 1,3 · 0-based)`), and a substring join against them is how a policy whose name is another's
    # prefix goes missing. Every charged eval is conclusively violated by construction, so it is
    # in that list, and the counts subtract cleanly.
    unrooted = sum(max(0, len(scenario.get("failing_evals") or [])
                       - len({charge.get("eval") for root in (scenario.get("rooted_findings") or [])
                              if isinstance(root, dict)
                              for charge in (root.get("charged_by") or []) if isinstance(charge, dict)}))
                   for scenario in (spec.get("scenarios") or []) if isinstance(scenario, dict))
    if unrooted:
        L += [f'_{unrooted} further failing eval(s) named NO utterance — a check over world state or a '
              'count has no sentence to quote, and a charge that quotes only the simulated user or tool data (a '
              'record or dataset row) names no agent sentence, so it is in `Failing evals` above and has no row here. '
              'That is the whole of the difference between the two counts._', ""]
    return L


#: Share of a run's tool calls that may be RECONSTRUCTED before the headline says so. Half is
#: the point at which the run's tool evidence is mostly synthetic rather than observed — below
#: it the per-scenario annotation still names every one, so nothing is hidden either way.
_RECOVERED_HEADLINE_SHARE = 0.5


def _tools_cell(s: dict, show_refusals: bool, show_recovered: bool) -> str:
    """The Tool calls cell: the count, annotated with dispatch errors and — when the run.json
    carries the field at all — application-level refusals and RECONSTRUCTED calls. The three
    annotations stay apart: an error is a call that never went through, a refusal is a call
    that went through and answered with an error-shaped payload, and a reconstructed call is
    one the mock never saw at all (recovered from the transcript, so its status and state
    effects are synthetic — it is evidence about the harness, not about the agent).

    A fourth annotation is about the COUNT rather than about the calls: `tool_events_unread`
    says the runner declared this scenario's tool channel unread, so the number beside it is
    not a measurement at all. `build_run_json` carries that on the row precisely so a reader
    of this page — and not only whoever was watching stderr — can tell it from a measured 0."""
    parts = []
    if s.get("tool_errors"):
        parts.append(f'{s["tool_errors"]} err')
    if show_refusals and s.get("tool_refusals"):
        parts.append(f'{s["tool_refusals"]} refused')
    if show_recovered and s.get("tool_calls_recovered"):
        parts.append(f'{s["tool_calls_recovered"]} reconstructed')
    # NOT A MEASURED ZERO. The runner wrote a literal `null` `tool_events.json` for this
    # scenario — it DECLARED the channel unread — so `0` here is an absence of observation and
    # not an observation of absence, which is the one reading a bare `0` in this column gives.
    if s.get("tool_events_unread"):
        parts.append("channel unread")
    return f'{s.get("tool_calls", 0)}' + (f' ({", ".join(parts)})' if parts else "")


#: How many hex characters of the signature digest the cell shows. Long enough that two different
#: tool plans in one run do not collide by eye, short enough to sit in a table cell. It is an
#: EQUALITY handle, never an ordering: a reader compares two of them, and the comparison script
#: compares the call lists themselves.
_TRAIL_DIGEST_CHARS = 8


def trail_digest(scenario: dict) -> str | None:
    """A short handle for this scenario's normalized tool plan, or None when the row carries none.

    Digested over the MODE as well as the calls, so two rows normalized under different rules can
    never render the same handle — the mode is on the row precisely because the answer depends on
    it, and a digest that ignored it would hide exactly the mismatch it was added to reveal.
    """
    calls = scenario.get("trail_signature")
    if not isinstance(calls, list):
        return None
    payload = str(scenario.get("trail_signature_mode") or "?") + "\n" + "\n".join(str(c) for c in calls)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_TRAIL_DIGEST_CHARS]


#: `build_run_json.TRAIL_SOURCE_RECOVERED` — the value stamped on a row whose whole tool plan was
#: rebuilt from the transcript instead of observed at the mock. RESTATED, not imported: this file
#: is stdlib-only by design so it can run beside a copied-out `run.json`. A test asserts it equals the producer's
#: constant, so a rename over there fails loudly instead of silently deleting the `reconstructed`
#: caveat from every row that earned it.
_TRAIL_SOURCE_RECOVERED = "recovered"


def _trail_cell(s: dict) -> str:
    """The Tool trail cell: the plan's digest, whether it was OBSERVED, and the runner's repeat.

    Three facts, kept apart because they answer three different questions. The digest says whether
    this scenario's tool plan is the same one another run of the same suite produced — the
    question a reader has to answer BEFORE reading a cell delta as the agent's response to a fix.
    `recovered` says the whole plan was rebuilt from the transcript rather than observed at the
    mock, so comparing two of them partly measures transcript variance. And the repeat distance is
    the runner's own second sample of this scenario — EVIDENCE, with the selection that produced
    it named, and never a noise floor: the runner retries only failing scenarios, so the
    population is conditioned on failure and a clean run produces none at all.
    """
    digest = trail_digest(s)
    if digest is None:
        return "—"
    if not s.get("trail_signature"):
        return "— _no calls_"
    parts = [f'`{digest}`']
    if s.get("trail_source") == _TRAIL_SOURCE_RECOVERED:
        parts.append("reconstructed")
    repeat = s.get("trail_repeat_distance")
    if isinstance(repeat, dict):
        selection = str(s.get("trail_repeat_selection") or "unknown")
        parts.append(f'repeat +{repeat.get("added", 0)}/−{repeat.get("dropped", 0)} ({_cell(selection)})')
    return " · ".join(parts)


def _det_cell(det: dict | None) -> str:
    """The scenario's DETERMINISTIC criterion — the check that never touches pass_rate.

    ``—`` means the scenario declared no such criterion. That is not a pass: nothing was
    asserted. ``error`` is the CHECK being broken (a bad expression), never the agent failing
    it, so it is iconned apart from a genuine ``failed``.
    """
    if not isinstance(det, dict) or not det.get("status"):
        return "—"
    status = str(det["status"])
    label = str(det.get("label") or "").strip().replace("|", "\\|")
    text = "check broken" if status == "error" else status
    # ❔, not a blank: a status this build has no icon for is an UNKNOWN verdict, and an
    # unmarked cell in a column of ✅/❌ reads as nothing worth flagging.
    return f'{_DET_ICON.get(status, "❔")} {text}' + (f' `{label}`' if label else "")


def _declared_tool_count(spec: dict) -> int | None:
    """The tool DECLARATION as a count, or None when it was never read.

    Three states ride `run.json` and `spec.get("declared_tools") == 0` collapsed them into two,
    wrongly, in three separate ways:

    * an ABSENT key is not a zero. It means nobody read `plurai.config.yaml` (the build ran
      without `--workdir`, the file did not parse, it declares no `tools:` key) — and
      `declared_tools_unknown` carries which. Reading it as zero let the report state a property
      of the agent that nothing had looked at.
    * `False == 0` is True in Python, so a producer that ever wrote a flag into this field read
      as an agent with an empty tool surface. A bool is not a count.
    * anything else non-int (a string count off a hand-edited run.json) reads as unknown rather
      than crashing the whole render or silently missing the branch.

    The unknown state is the same one `render_outcome_attribution_md` already keys on a few
    blocks up, and it is read here the same way so the two sections cannot disagree.
    """
    if spec.get("declared_tools_unknown") is not None:
        return None
    declared = spec.get("declared_tools")
    if isinstance(declared, bool) or not isinstance(declared, int):
        return None
    return declared


def _observed_tool_activity(spec: dict) -> bool:
    """Did ANY tool call reach this run's evidence at all?

    The other half of the question the declaration cannot answer. A declaration of zero
    contradicted by recorded calls is not a tool-less agent — it is a stale or unread config over
    a run that plainly called something, and it is the shape that would make the tool-less
    sentence print "no tool calls were recorded" directly above a table listing ten.

    Totals AND rows, because a spec can carry either: `show_tools` derives its column from the
    rows, so the same three counters are read here rather than a fourth definition of "has
    tools".
    """
    keys = ("tool_calls", "tool_errors", "tool_refusals")
    totals = spec.get("totals") or {}
    if isinstance(totals, dict) and any(totals.get(k) for k in keys):
        return True
    rows = spec.get("scenarios") or []
    return any(any(row.get(k) for k in keys) for row in rows if isinstance(row, dict))


#: analytics.md is the operator's page; eval-set fitness is loop material.
def render_eval_signal_md(spec: dict) -> str:
    """The eval-signal and graded-nothing sections, for `eval_signal.md`; empty when neither renders."""
    body = render_signal_md(spec) + render_graded_nothing_md(spec)
    return "\n".join([f'# Eval signal — {spec.get("run_name", "run")}', "", *body]) if body else ""


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _undecided_aside(cell: dict) -> str:
    """` (k not applicable, j inconclusive)` for the trials that did NOT decide the cell, or `""`."""
    undecided = [f'{int(cell.get(k) or 0)} {word}' for k, word in (("na", "not applicable"), ("deferred", f"graded under {_owners_phrase(cell)}"),
                                                                   ("inconclusive", "inconclusive"))
                 if int(cell.get(k) or 0)]
    return f' ({", ".join(undecided)})' if undecided else ""


def _trial_counts(cell: object) -> str:
    """`k/m passed` over the trials that DECIDED the cell, with the undecided ones named beside it."""
    if not isinstance(cell, dict):
        return "—"
    passed, failed = int(cell.get("passed") or 0), int(cell.get("failed") or 0)
    return f'{passed}/{passed + failed} passed' + _undecided_aside(cell)


def _trial_failures(hit: dict, rows: dict) -> str:
    """`failed k of m trials` over the trials that DECIDED the cell, with the undecided ones named beside it."""
    cell = _mapping(_mapping(_mapping(rows.get(hit.get("scenario"))).get("policies")).get(hit.get("policy")))
    if not isinstance(cell.get("failed"), int):
        return f'failed {hit.get("failed")} of {hit.get("of")} trials'
    failed, passed = int(cell.get("failed") or 0), int(cell.get("passed") or 0)
    return f'failed {failed} of {passed + failed} trials' + _undecided_aside(cell)


#: One trial's outcome on one cell (the SDK's `by_trial`); `None` is a trial that measured nothing.
_TRIAL_DOT = {"passed": "🟢", "failed": "🔴", "inconclusive": "🟡", "na": "◻️", "deferred": "↪️", None: "—"}

#: The SDK's goal labels, bucketed for the Verdict — named and counted off `goal.label` alone. The pass axis
#: (goal reached, no decisive or high-severity failure); "flaky" says the trials disagreed, not whose variance it was.
_GOAL_BUCKETS = (
    ("🟢 **Reliable** — passed on every trial that decided it", ("consistent_pass",)),
    ("🟠 **Flaky** — passed on some trials, failed on others", ("inconsistent",)),
    ("🔴 **Broken** — passed on no trial", ("consistent_fail",)),
    ("❔ **Unmeasured** — fewer than two trials decided it", ("insufficient", "not_applicable")),
)

#: Worst first: the order scenarios are listed in, by the same `goal.label`.
_WORST_FIRST = {"consistent_fail": 0, "inconsistent": 1, "insufficient": 2, "not_applicable": 2, "consistent_pass": 3}

_SEVERITY_RANK = {"critical": 0, "high": 1}


def _goal_owners(spec: dict) -> list[str] | None:
    """The evals that own the goal, off the backend's `target_judge_owners` on the rows of every trial; `None` when
    no row carries the key (a backend before it), so nothing is set apart by guessing a name."""
    rows = [r for r in spec.get("scenarios") or [] if isinstance(r, dict)]
    rows += [r for trial in _mapping(spec.get("trial_rows")).values() if isinstance(trial, list) for r in trial if isinstance(r, dict)]
    lists = [owners for r in rows if isinstance(owners := _mapping(r.get("goal")).get("target_judge_owners"), list)]
    return sorted({str(name) for owners in lists for name in owners}) if lists else None


def _by_trial(cell: object, n: int) -> list | None:
    """The cell's `by_trial` when the SDK wrote it (one entry per trial); `None` on an SDK before it."""
    trials = _mapping(cell).get("by_trial")
    return trials if isinstance(trials, list) and len(trials) == n else None


def _dots(trials: list) -> str:
    return "".join(_TRIAL_DOT.get(outcome, "🟡") for outcome in trials)


def _systematic(cell: dict, n: object) -> bool:
    """A cell that failed on EVERY trial that decided it, and was decided on enough of them to say so: the SDK's
    `consistent_fail` over at least `min(n, _SAMPLE_FLOOR)` decided trials. Two decided of three is not systematic."""
    trials = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 1
    decided = int(cell.get("passed") or 0) + int(cell.get("failed") or 0)
    return cell.get("label") == "consistent_fail" and decided >= min(trials, _SAMPLE_FLOOR)


def scored_failures(spec: dict) -> list[dict]:
    """Each eval behind a critical or a failing-tier failure on some trial, per scenario, AS THE SDK'S SCORES BLOCK STATES
    IT (`run.json` → `scores` → `agent`: `blocking_critical`, and `non_blocking.failing_tier`): `{scenario, policy,
    severity, failed, of}` — `critical` for a blocking critical failure, `high` for a failed failing-tier check (what
    the backend served as `high_severity_violations`); `failed` the trials it is listed on, `of` the scenario's conversations.
    Read, never derived; `[]` when the run carries no scores block."""
    scores = _mapping(spec.get("scores"))
    agent, scenarios = _mapping(scores.get("agent")), _mapping(scores.get("scenarios"))
    found: dict[tuple[str, str], dict] = {}
    for level, listed in (("critical", agent.get("blocking_critical")), ("high", _mapping(agent.get("non_blocking")).get("failing_tier"))):
        for entry in _list(listed):
            entry = _mapping(entry)
            key = (str(entry.get("scenario")), str(entry.get("eval")))
            hit = found.setdefault(key, {"scenario": key[0], "policy": key[1], "severity": level, "trials": set(),
                                         "of": len(_list(_mapping(scenarios.get(key[0])).get("conversations")))})
            if _SEVERITY_RANK[level] < _SEVERITY_RANK[hit["severity"]]:
                hit["severity"] = level
            hit["trials"].add(entry.get("trial"))
    return [{**{k: v for k, v in hit.items() if k != "trials"}, "failed": len(hit["trials"])} for _key, hit in sorted(found.items())]


def _issues(block: dict, owners: list[str], hits: list[dict]) -> list[dict]:
    """One entry per eval that failed on some trial, the goal's own evals aside: counts from `run.policies`,
    severity from the scores block (`hits`, :func:`scored_failures`), systematic where a scenario's cell is
    :func:`_systematic`. Ranked by severity, then systematic, then failures."""
    run, rows = _mapping(block.get("run")), _mapping(block.get("scenarios"))
    severity: dict[str, str] = {}
    for hit in hits:
        level, name = hit["severity"], hit["policy"]
        if _SEVERITY_RANK[level] < _SEVERITY_RANK.get(severity.get(name, ""), 9):
            severity[name] = level
    issues = []
    for name, total in sorted(_mapping(run.get("policies")).items()):
        if name in owners or not int(_mapping(total).get("failed") or 0):
            continue
        cells = {sid: _mapping(_mapping(row.get("policies")).get(name)) for sid, row in sorted(rows.items())
                 if isinstance(row, dict) and name in _mapping(row.get("policies"))}
        always = [sid for sid, c in cells.items() if _systematic(c, block.get("n"))]
        issues.append({"name": name, "total": total, "severity": severity.get(name), "cells": cells, "always": always,
                       "some": [sid for sid, c in cells.items() if c.get("failed") and sid not in always]})
    issues.sort(key=lambda i: (_SEVERITY_RANK.get(i["severity"], 9), not i["always"], -int(i["total"].get("failed") or 0), i["name"]))
    return issues


def _failed_over_decided(total: dict) -> str:
    """`failed/decided`, the inconclusive ones beside: ONE denominator, the conclusive verdicts."""
    failed, passed, undecided = (int(total.get(k) or 0) for k in ("failed", "passed", "inconclusive"))
    return f'{failed}/{passed + failed}' + (f' ({undecided} inconclusive)' if undecided else "")


def _findings(spec: dict, sid: str) -> list[tuple[int, dict, dict]]:
    """`(trial, root, charge)` for every rooted charge on scenario `sid`, trial by trial, off `trial_rows`."""
    trials = _mapping(spec.get("trial_rows")).get(sid)
    return [(k, root, charge) for k, row in enumerate(trials if isinstance(trials, list) else [], start=1) if isinstance(row, dict)
            for root in row.get("rooted_findings") or [] if isinstance(root, dict)
            for charge in root.get("charged_by") or [] if isinstance(charge, dict)]


#: The quote's share of one finding line here: several findings stack per scenario, so tighter than the table's.
_TRIAL_QUOTE = 160


def _finding_lines(k: int, root: dict, charge: dict) -> list[str]:
    quote = root.get("quote")
    said = f'“{escape(_elide(" ".join(quote.split()), _TRIAL_QUOTE))}”' if isinstance(quote, str) and quote else "_(no sentence quoted)_"
    why = " ".join(str(charge["why"]).split()) if isinstance(charge.get("why"), str) else ""
    return [f'- T{k} · turn {root.get("turn_idx")}: {said}' + ("  " if why else ""), *([f'  <sub>{escape(_elide(why, _ROOTED_WHY))}</sub>'] if why else [])]


#: The goal owner `build_run_json` appends to a trial's goal reason (`goal not achieved (owner: target_judge)`): the
#: scoring backend's internal id, which the page never prints.
_GOAL_OWNER_TAG = re.compile(r'\s*\(owner: [^)]*\)')


def _customer_goal_reason(reason: str) -> str:
    return _GOAL_OWNER_TAG.sub("", reason).strip()


def _trial_score(trial: dict) -> str:
    return f'{trial["score"]:.2f}' if isinstance(trial.get("score"), (int, float)) else "—"


def _severity_mark(level: str | None) -> str:
    return f' ⚠️ {level}' if level else ""


#: The verdict's one pass row, read off C7 (`run_numbers`): passed over the VALID trials, what could not run beside it.
_PASSED_TITLE = "**Passed**"


def _c7_rows(spec: dict) -> list[str]:
    """The verdict's pass row from C7 — the same numbers the chat headline prints, never re-tallied here. A run.json
    without C7 says so in the row's place."""
    try:
        counts = run_numbers.c7(spec)
    except run_numbers.C7Missing as exc:
        return [f'| {_PASSED_TITLE} | not counted — {_cell(str(exc))}; rebuild run.json before sharing this page |']
    beside = "".join(f'; {part}' for part in run_numbers.invalid_parts(spec))
    return [f'| {_PASSED_TITLE} | **{counts["passed_valid"]} of {counts["valid_trials"]}** {run_numbers.noun(spec)}{beside} |']


#: A scenario id's own number: `scenario_03_<set>_scenario_03` → 3.
_SCENARIO_NUMBER = re.compile(r'scenario_0*(\d+)(?:_|$)')


def short_labels(ids: list[str]) -> dict[str, str]:
    """`{id: label}` for the trials tables. A label is the id's OWN number (`scenario_03_…` → `S3`), so the chat's
    `scenario_03` and this page's `S3` name one row; an id with no number is `R1`, `R2`, … in id order. Two ids with
    one number fall back to every id said in full."""
    numbered = {sid: f'S{int(m.group(1))}' for sid in ids if (m := _SCENARIO_NUMBER.match(sid))}
    if len(set(numbered.values())) < len(numbered):
        return {sid: sid for sid in ids}
    rest = sorted(sid for sid in ids if sid not in numbered)
    return {**numbered, **{sid: f'R{i}' for i, sid in enumerate(rest, start=1)}}


def render_trials_md(spec: dict) -> list[str]:
    """The FINAL run's N trials, issue-first, printed from the SDK's `trials` block (per-trial outcomes from each
    cell's `by_trial`) and `trial_rows` (each trial's own score, turns and rooted findings) — never re-derived.
    It LEADS the report because it is the result; everything after it is trial 1 and says so. Absent block → nothing."""
    block = spec.get("trials")
    if not isinstance(block, dict) or block.get("schema") != TRIALS_SCHEMA:
        return []
    n = count if isinstance(count := block.get("n"), int) else 0
    run, rows = _mapping(block.get("run")), _mapping(block.get("scenarios"))
    standing = run_numbers.standing_rows(rows)
    short = short_labels(sorted(rows))
    named = owners if (owners := _goal_owners(spec)) is not None else []
    scored_hits = scored_failures(spec)
    issues = [] if run.get("withdrawn") else _issues(block, named, scored_hits)
    trial_rows = _mapping(spec.get("trial_rows"))
    order = sorted(rows, key=lambda sid: (_WORST_FIRST.get(str(_mapping(_mapping(rows[sid]).get("goal")).get("label")), 2), sid))
    L = [f'## Trials — {n} independent trials per scenario', "", "### 1 · Verdict", ""]
    dirs = _list(block.get("dirs"))
    for entry in [] if _customer(spec) else _list(block.get("measurement")):
        k = entry.get("trial") if isinstance(entry, dict) else None
        if not isinstance(k, int) or entry.get("clean") is True:
            continue
        sub = dirs[k - 1] if 0 < k <= len(dirs) and isinstance(dirs[k - 1], str) else ""
        where = f'[trial {k}]({sub}/analytics.md)' if sub else "Run integrity below"
        if entry.get("clean") is False:
            why = ", ".join(f'`{_cell(str(c))}`' for c in entry.get("because") or []) or "no channel named"
            L += [f'**Measurement NOT clean on trial {k}** ({why}) — read {where}.', ""]
        else:
            L += [f'**Measurement NOT MEASURED on trial {k}** — nothing recorded whether it was clean; read {where}.', ""]
    rescored = _mapping(block.get("rescored"))
    if _list(rescored.get("scenarios")):
        names = ", ".join(f'`{_cell(str(s))}`' for s in _list(rescored.get("scenarios")))
        revisions = ", ".join(f'`{_cell(str(r))}`' for r in _list(rescored.get("eval_revisions"))) or "an unrecorded revision"
        L += [f'**Re-scored after the run** — the counts for {names} include their rescore (eval revision {revisions}).', ""]
    if run.get("withdrawn"):
        names = ", ".join(f'`{_cell(str(s))}`' for s in run.get("withdrawn_scenarios") or [])
        L += [f'**The cross-trial counts were withdrawn** — {names} re-scored after the run. Re-run the scenarios to '
              'refresh them.', ""]
        if scored_hits:
            L += ['**Critical or high-severity failures on any trial** — '
                  + "; ".join(f'`{_cell(str(h.get("policy")))}` on `{_cell(str(h.get("scenario")))}` ({h.get("severity")}): '
                              f'{_trial_failures(h, rows)}' for h in scored_hits) + ".", ""]
    else:
        L += ["| | |", "|---|---|", *_c7_rows(spec)]
        # Trial agreement compares trials that ran on each other's writes when the world is shared: no bucket is a reading of the agent.
        comparable = block.get("stability_comparable") is not False
        if not comparable:
            L.append(f'| **Trial agreement** | {_not_comparable(block.get("stability_comparable_reason"))} |')
        for title, labels in _GOAL_BUCKETS if comparable else ():
            members = [sid for sid, row in standing.items() if _mapping(row.get("goal")).get("label") in labels]
            if members or labels != ("insufficient", "not_applicable"):
                L.append(f'| {title} | {len(members)} of {len(standing)} scenarios' + (f': {", ".join(short[s] for s in members)}' if members else "") + " |")
        ran = [name for name, total in _mapping(run.get("policies")).items() if name not in named
               and any(int(_mapping(total).get(k) or 0) for k in ("passed", "failed", "inconclusive"))]
        severe = sum(1 for issue in issues if issue["severity"])
        L.append(f'| **Evals that failed on some trial** | {len(issues)} of {len(ran)} that ran — {severe} high or critical |')
        L.append(f'| **Goal evals** | {", ".join(f"`{_cell(o)}`" for o in named) or "none declared"} — the goal itself, set apart from the evals above |'
                 if owners is not None else '| **Goal evals** | not named by the scoring backend — none is set apart below |')
        L.append("")
    invalid = [(name, bad) for name, row in sorted(rows.items()) if isinstance(row, dict)
               for bad in row.get("invalid") or [] if isinstance(bad, dict)]
    if invalid:
        # The COUNTS are C7's, by whose side, never a tally of this list; the list only says where.
        try:
            said = "; ".join(run_numbers.invalid_parts(spec))
        except run_numbers.C7Missing:
            said = "Did not run"
        head = f'**{said or "Did not run"}; outside every count above** — '
        L += [head + "; ".join(f'`{_cell(str(name))}` trial {bad.get("trial")}' for name, bad in invalid) + ".", ""]

    L += ["### 2 · Issues, ranked", ""]
    if run.get("withdrawn"):
        L += ["_Withdrawn with the cross-trial counts above._", ""]
    elif not issues:
        L += ["_No eval failed on any trial._", ""]
    else:
        L += ["_One row per eval that failed on some trial, the goal's own evals aside. **Failed** is over the trials that "
              "decided it, the inconclusive ones beside; **systematic** means it failed on every trial that decided it on "
              f"at least one scenario, and at least {_SAMPLE_FLOOR} trials (or every trial) decided it there._", "",
              "| # | Eval | Severity | Failed | Pattern | Where |", "|---|---|---|---|---|---|"]
        for i, issue in enumerate(issues, start=1):
            where = ([f'every trial of {", ".join(short[s] for s in issue["always"])}'] if issue["always"] else []) + (
                [f'sometimes in {", ".join(short[s] for s in issue["some"])}'] if issue["some"] else [])
            L.append(f'| {i} | `{_cell(issue["name"])}` | {issue["severity"] or "—"} | {_failed_over_decided(issue["total"])} | '
                     f'{"🔴 systematic" if issue["always"] else "🟠 intermittent"} | {"; ".join(where)} |')
        L += ["", "Example of each (turn numbers are 0-based):", ""]
        for i, issue in enumerate(issues, start=1):
            example = next(((sid, k, root, charge) for sid in sorted(rows) for k, root, charge in _findings(spec, sid)
                            if charge.get("eval") == issue["name"]), None)
            if example is None:
                L.append(f'{i}. `{_cell(issue["name"])}` — no rooted finding (it quoted no agent sentence).')
                continue
            sid, k, root, charge = example
            finding = _finding_lines(k, root, charge)
            L.append(f'{i}. `{_cell(issue["name"])}` — {short[sid]} · {finding[0].removeprefix("- ")}')
            L += [f'   {line.strip()}' for line in finding[1:]]
        L.append("")

    L += ["### 3 · Scenarios, worst first", "",
          "| | Scenario | " + " | ".join(f'Trial {k}' for k in range(1, n + 1)) + " | Passed | Failing evals |",
          "|---|---|" + "---|" * n + "---|---|"]
    for sid in order:
        row = _mapping(rows[sid])
        if row.get("withdrawn"):
            L.append(f'| {short[sid]} | `{_cell(str(sid))}` | withdrawn — re-scored after the run |' + " — |" * (n + 1))
            continue
        goal_trials = _by_trial(row.get("goal"), n)
        cells = []
        for k in range(n):
            own = _list(trial_rows.get(sid))
            trial = _mapping(own[k] if k < len(own) else None)
            score = _trial_score(trial)
            cells.append(f'{_TRIAL_DOT.get(goal_trials[k], "🟡")} {score}' if goal_trials else score)
        passed = _trial_counts(row.get("goal"))
        valid = row.get("valid")
        if isinstance(valid, int) and valid < n:
            passed += f' · {valid} of {n} trials valid'
        # The cell is the goal; a critical/high failure beside it is named, never folded in (the single-run rule).
        for h in scored_hits:
            if h["scenario"] == str(sid) and h["policy"] not in named:
                passed += f' · ⚠ {h["severity"]} `{_cell(h["policy"])}` {_trial_failures(h, rows)}'
        failing = sum(1 for name, cell in _mapping(row.get("policies")).items() if name not in named and _mapping(cell).get("failed"))
        L.append(f'| {short[sid]} | `{_cell(str(sid))}` | ' + " | ".join(cells) + f' | {passed} | {failing} |')
    L.append("")

    L += ["### 4 · Where each issue shows up", ""]
    if issues:
        dots = all(_by_trial(c, n) for issue in issues for c in issue["cells"].values())
        L += [f'_Each cell is trials 1→{n}: 🟢 pass · 🔴 fail · 🟡 inconclusive · ◻️ not applicable · ↪️ graded under the eval it defers to · — invalid trial; '
              '`·` = the eval never applied to that scenario._' if dots else
              "_Each cell is failed/decided over that scenario's trials (this SDK names no trial per outcome); "
              "`·` = the eval never applied to that scenario._", "",
              "| Eval | " + " | ".join(short[s] for s in sorted(rows)) + " |", "|---|" + ":---:|" * len(rows)]
        for issue in issues:
            row_cells = []
            for sid in sorted(rows):
                cell = issue["cells"].get(sid)
                trials = _by_trial(cell, n)
                if cell is None or not any(int(cell.get(k) or 0) for k in ("passed", "failed", "inconclusive", "deferred")):
                    row_cells.append("·")
                elif dots and trials:
                    row_cells.append(_dots(trials))
                else:
                    row_cells.append(f'{int(cell.get("failed") or 0)}/{int(cell.get("passed") or 0) + int(cell.get("failed") or 0)}')
            L.append(f'| `{_cell(issue["name"])}`{_severity_mark(issue["severity"])} | ' + " | ".join(row_cells) + " |")
        L.append("")
    elif run.get("withdrawn"):
        L += ["_Withdrawn with the cross-trial counts above._", ""]
    else:
        L += ["_No eval failed on any trial._", ""]
    if not run.get("withdrawn"):
        totals = {name: _mapping(t) for name, t in sorted(_mapping(run.get("policies")).items()) if name not in named}
        for title, pick in (("Never failed", lambda t: not t.get("failed") and t.get("passed")),
                            ("Never decided (inconclusive on every trial it ran)", lambda t: not t.get("failed") and not t.get("passed") and t.get("inconclusive")),
                            ("Graded under the eval it defers to on every trial", lambda t: not any(t.get(k) for k in ("passed", "failed", "inconclusive")) and t.get("deferred")),
                            ("Never applied in this run", lambda t: not any(t.get(k) for k in ("passed", "failed", "inconclusive", "deferred")))):
            if picked := [name for name, t in totals.items() if pick(t)]:
                L += [f'**{title} ({len(picked)}):** ' + ", ".join(f'`{_cell(p)}`' for p in picked) + ".", ""]

    L += ["### 5 · Scenario details", "",
          "_Findings grouped by eval, each with the trials it hit; why a trial did not pass (the goal's own evals, or its "
          "verdict's reason) comes first. Turn numbers are 0-based._", ""]
    rank = {issue["name"]: i for i, issue in enumerate(issues)}
    severity = {issue["name"]: issue["severity"] for issue in issues}
    for sid in order:
        row = _mapping(rows[sid])
        if row.get("withdrawn"):
            continue
        goal_trials = _by_trial(row.get("goal"), n) or []
        trials = _list(trial_rows.get(sid))
        # Which trial each outcome was is the SDK's `by_trial`; an SDK before it leaves those columns out.
        known = bool(goal_trials) and all(_by_trial(cell, n) for cell in _mapping(row.get("policies")).values())
        L += [f'<details><summary><b>{short[sid]} · {escape(str(sid))}</b> &nbsp; {_dots(goal_trials)}</summary>', "",
              "| Trial | " + ("Passed | " if known else "") + "Score | Turns |" + (" Failing evals |" if known else ""),
              "|---|" + "---|" * (4 if known else 2)]
        for k in range(1, n + 1):
            trial = _mapping(trials[k - 1] if k <= len(trials) else None)
            failed = sorted((name for name, cell in _mapping(row.get("policies")).items()
                             if name not in named and (_by_trial(cell, n) or [None] * n)[k - 1] == "failed"),
                            key=lambda name: (rank.get(name, len(rank)), name))
            score = _trial_score(trial)
            turns = trial.get("turns") if trial.get("turns") is not None else "—"
            L.append(f'| {k} | ' + (f'{_TRIAL_DOT.get(goal_trials[k - 1], "🟡")} | ' if known else "") + f'{score} | {turns} |'
                     + (" " + (", ".join(f'`{_cell(name)}`' for name in failed) or "—") + " |" if known else ""))
        L.append("")
        found = _findings(spec, sid)
        missed = [k for k in range(1, n + 1) if k <= len(goal_trials) and goal_trials[k - 1] == "failed"]
        if missed:
            L += [f'**🎯 Why it did not pass** — trial {", ".join(map(str, missed))}', ""]
            for k in missed:
                owned = [(t, root, charge) for t, root, charge in found if t == k and charge.get("eval") in named]
                for t, root, charge in owned:
                    L += _finding_lines(t, root, charge)
                trial = _mapping(trials[k - 1] if k <= len(trials) else None)
                goal, check = _mapping(trial.get("goal")), _mapping(trial.get("deterministic"))
                if not owned and goal.get("reason"):
                    L.append(f'- T{k}: {_cell(_customer_goal_reason(str(goal["reason"])))}'
                             + (f' — `{_cell(str(check.get("label")))}` {check.get("status")}' if check.get("status") == "failed" else ""))
                    if check.get("status") == "failed" and isinstance(check.get("detail"), str):
                        # Head, not middle: a check's detail states what it asserts first.
                        detail = " ".join(check["detail"].split())
                        L.append(f'  <sub>{escape(detail if len(detail) <= _ROOTED_WHY else detail[:_ROOTED_WHY - 1] + "…")}</sub>')
            L.append("")
        by_eval: dict[str, list[tuple[int, dict, dict]]] = {}
        for t, root, charge in found:
            if charge.get("eval") not in named:
                by_eval.setdefault(str(charge.get("eval")), []).append((t, root, charge))
        for name in sorted(by_eval, key=lambda name: (rank.get(name, len(rank)), name)):
            hits = by_eval[name]
            # The scores block's severity for this scenario's eval (`issues` is empty on a withdrawn run).
            level = next((h["severity"] for h in scored_hits if h["scenario"] == str(sid) and h["policy"] == name), None) or severity.get(name)
            L += [f'**`{_cell(name)}`**{_severity_mark(level)} — trial {", ".join(map(str, sorted({t for t, _r, _c in hits})))}', ""]
            for t, root, charge in hits:
                L += _finding_lines(t, root, charge)
            L.append("")
        L += ["</details>", ""]

    links = " · ".join(f'[{sub.replace("_", " ")}]({sub}/analytics.md)' for sub in dirs if isinstance(sub, str) and sub)
    L += ["## Trial 1 detail", "", "Every section below reads trial 1 only; the exceptions are its headline counts and the eval table, "
          "which pool every trial, and Run integrity, which compares trial 1 with the other trials."
          + (f' The other trials: {links}.' if links else ""), ""]
    return L


def _pct(value: object) -> str:
    return f'{round(100 * float(value))}%' if isinstance(value, (int, float)) else "?"


def _range(interval: object) -> tuple[str, str] | None:
    return (_pct(interval[0]), _pct(interval[1])) if isinstance(interval, list) and len(interval) == 2 else None


def _regression_label(row: dict) -> str:
    return str(row.get("title") or row.get("folder") or row.get("scenario") or "a recorded issue")


def _replays_drawn(row: dict, cell: dict) -> int:
    """Every replay the row drew: its valid ones (the cell's own outcomes when the row names none) and its invalid ones."""
    valid = row.get("valid")
    if not isinstance(valid, int):
        valid = sum(int(cell.get(k) or 0) for k in ("passed", "failed", "inconclusive", "na", "deferred"))
    return valid + len(_list(row.get("invalid")))


def _scored_sentence(scored: dict | None, *, single_hop: bool = False) -> str:
    """What the regression's own scenario showed in the scored run, off its owner's cell there: came back in f of d
    conversations, or did not come back — the latter only when the owner PASSED every conversation it decided. A
    scenario the run played single-hop counts replays of its recorded turn, not conversations."""
    cell = _mapping(scored)
    failed, passed = int(cell.get("failed") or 0), int(cell.get("passed") or 0)
    if not failed + passed:
        return ""
    unit = "replays of that turn" if single_hop else "conversations"
    if failed:
        return f' In the scored run it came back in {failed} of {failed + passed} {unit}.'
    return f' In the scored run it did not come back ({passed} of {passed} {unit}).'


def customer_regression_line(row: dict, scored: dict | None = None, *, scored_single_hop: bool = False) -> str | None:
    """One pinned regression in the customer's words — or None when it says nothing.

    The replays first: caught k of k; caught k of n with the plain-percent range; not caught at that turn but later in
    the conversation; not caught in n (the owner PASSED every replay that decided) with the ceiling the range leaves;
    once (one replay drawn); NOT DECIDED when no more than half the replays drawn reached a result (invalid or
    undecided replays). Then `scored`, the owner's cell on the regression's own scenario in the scored run
    (:func:`_scored_sentence`). Never a word about how the replays were scored or what they cost.
    A single-hop row (`replay_mode: single_hop`) replayed only the issue turn, so it has no "later" to speak of; a row
    read over the whole replay (`clear_axis: anywhere`, a regression played from its opener) has no "that turn"."""
    whole = row.get("clear_axis") == "anywhere"
    cell = _mapping(row.get("anywhere" if whole else "at_cut"))
    failed = int(cell.get("failed") or 0)
    measured = measured_replays(cell)  # a pass whose premise never arose measured nothing
    label, span = cell.get("label"), _range(cell.get("wilson95_failed"))
    later = _mapping(row.get("anywhere"))
    later_failed = int(later.get("failed") or 0)
    drawn = _replays_drawn(row, cell)
    # A reading over the scored run's own replays (`reading.population: run`) already IS what the scored run showed.
    own = _mapping(row.get("reading")).get("population") == "run"
    scored_said = "" if own else _scored_sentence(scored, single_hop=scored_single_hop)
    if row.get("hollow"):  # `regressions.hollow_reason`: its replays cannot measure its issue, so the folder is dropped after this run
        return f'- **{_regression_label(row)}** — {HOLLOW_LINE}'
    if row.get("status") == "measures_nothing":  # the replays ran and never met the issue's situation: one wording on every surface
        return f'- **{_regression_label(row)}** — Recorded, not yet measured: no replay reached its situation.{scored_said}'
    if drawn and 2 * measured <= drawn and drawn > 1:
        if not measured and not scored_said:
            return None
        return f'- **{_regression_label(row)}** — Not decided: too few replays reached a result.{scored_said}'
    if not whole and row.get("replay_mode") != "single_hop" and measured and not failed and later_failed:
        # Not at the recorded turn, but the same issue came back further on: said as such, never as a clean pass.
        later_measured = later_failed + int(later.get("passed") or 0)
        return (f'- **{_regression_label(row)}** — Not caught at that turn in {measured} replays, but it happened later '
                f'in the conversation in {later_failed} of {later_measured}.{scored_said}')
    if label == "consistent_fail":
        said = f'Caught again in {failed} of {measured} replays.'
    elif label == "inconsistent" and span:
        said = f'Caught again in {failed} of {measured} replays (between {span[0]} and {span[1]} of the time).'
    elif label == "consistent_pass" and span:
        said = f'Not caught in {measured} replays. If it still happens, it is under {span[1]} of the time.'
    elif measured == 1 and drawn <= 1:
        said = f'Replayed once: {"caught" if failed else "not caught"}.'
    else:
        return None
    return f'- **{_regression_label(row)}** — {said}{scored_said}'


#: The customer line of a hollow regression row (``run.json`` ``hollow``): never a result, said as removed. ``build_run_json``
#: marks a row hollow only on a run that measured the set whole, the run whose ``retire`` drops it.
HOLLOW_LINE = ('Removed from the kept replays: its replay could not reach the moment the issue happened, so it could not show '
               'whether the issue is still there. Its issue can be recorded again.')


def _owner_scored(spec: dict, row: dict) -> dict | None:
    """The regression owner's cell on the regression's own scenario in the scored run (`trials.scenarios`), or None."""
    scenario = _mapping(_mapping(_mapping(spec.get("trials")).get("scenarios")).get(str(row.get("scenario"))))
    return _mapping(scenario.get("policies")).get(str(row.get("owner"))) if scenario else None


def customer_regression_lines(spec: dict) -> list[str]:
    """The customer report's "Pinned regressions" lines. Pinned rows only: a folder whose replays no longer
    pin it, and every per-check rate, are internal and never reach this list. A folder whose check judged no
    replay passes through: its line says only what the scored run showed, else nothing."""
    hops = {str(name) for name in _list(spec.get("single_hop_scenarios"))}
    lines = [line for row in _list(spec.get("regressions"))
             if isinstance(row, dict) and row.get("status", "pinned") in ("pinned", "measures_nothing")
             and (line := customer_regression_line(row, _owner_scored(spec, row),
                                                   scored_single_hop=str(row.get("scenario")) in hops))]
    return ["**Pinned regressions**", *lines] if lines else []


def _cell_counts(cell: object) -> str:
    cell = _mapping(cell)
    if not cell:
        return "—"
    failed, passed = int(cell.get("failed") or 0), int(cell.get("passed") or 0)
    span = _range(cell.get("wilson95_failed"))
    cut = int(cell.get("cut_abstained") or 0)
    cut_aside = f' · {cut} cut-abstained' if cut else ""
    return f'{failed}/{failed + passed} caught' + (f' ({span[0]}–{span[1]})' if span else "") + _undecided_aside(cell) + cut_aside


def _issue_type_line(entry: dict) -> str:
    rate = _mapping(entry.get("rate"))
    unit = "conversations" if rate.get("unit") == "conversation" else "replies"
    titles = [str(b.get("title") or b.get("folder")) for b in _list(entry.get("behind")) if isinstance(b, dict)]
    check = f'`{_cell(str(entry.get("policy")))}`'
    behind = f' — behind {", ".join(f"*{_cell(t)}*" for t in titles)}' if titles else ""
    failed, passed = int(rate.get("failed") or 0), int(rate.get("passed") or 0)
    if not failed + passed:
        return f'- The check {check} judged no {unit} in this run{behind}.'
    span = _range(rate.get("wilson95_failed"))
    between = f' (between {span[0]} and {span[1]})' if span else ""
    return f'- The check {check} failed on {round(100 * failed / (failed + passed))}% of {unit}{between}: {failed} of {failed + passed}{_undecided_aside(rate)}{behind}.'


#: The LOCAL-ONLY regressions page, written beside `analytics.md` and never indexed in `report_files.json`:
#: nothing uploads, inlines or publishes it. `analytics.md` is the customer's deliverable when no platform
#: recorded the run, so the per-check rates and the scoring detail live here and nowhere it reaches.
REGRESSIONS_DETAIL = "regressions_detail.md"

#: How a row's owner came to be, in plain words.
_OWNER_SOURCE = {
    "linked": "linked — the check its audited reds were traced to",
    "ledger": "ledger",
    "probe_unowned": "probe (recorded) — the ledger named no owner",
    "probe_no_ledger": "probe (recorded)",
}


def _agent_host_copy_sentence(copy: object) -> str:
    """What ran on the agent's own host, from `run_integrity.agent_host_copy`; '' when the agent ran in the harness."""
    if not isinstance(copy, dict) or not copy.get("route_smoke"):
        return ""
    granted = [str(g) for g in _list(copy.get("granted"))]
    tools = (" The tools granted to run live inside that copy: " + ", ".join(f'`{_cell(g)}`' for g in granted) + ".") if granted else ""
    return (f'the agent ran as a copy on its own host, which its route smoke (`{_cell(str(copy["route_smoke"]))}`) brought up.'
            f'{tools} This run\'s host record does not observe that host.')


def render_pinned_regressions_md(spec: dict) -> list[str]:
    """The customer-safe regressions section of `analytics.md`: exactly the lines the customer report pastes."""
    lines = customer_regression_lines(spec)
    return [f'## {lines[0].strip("*")}', "", *lines[1:], ""] if lines else []


def render_regressions_detail_md(spec: dict) -> list[str]:
    """Detailed view of the regressions (`regressions_detail.md`, never `analytics.md`): each pinned folder's
    owner and where it came from, its at-cut / anywhere counts, the measured-at-record rate and the clean
    replays still needed; then the recurring issue types — each check behind a regression, at its red rate over
    every turn it judged, with the re-cut candidates of a folder that did not recur at its cut. Absent blocks →
    nothing."""
    rows = [r for r in _list(spec.get("regressions")) if isinstance(r, dict)]
    issue_types = [e for e in _list(spec.get("issue_types")) if isinstance(e, dict)]
    L: list[str] = []
    if rows:
        L += ["## Pinned regressions", "",
              "| Regression | Check (owner from) | At the cut | Anywhere | Measured at record | Clean replays to clear |",
              "|---|---|---|---|---|---|"]
        for row in rows:
            recorded = _mapping(row.get("recorded"))
            at_record = (f'{recorded.get("k")}/{recorded.get("n")} caught ({recorded.get("band")})' if recorded.get("n")
                         else f'seen once ({_cell(str(recorded.get("reason") or "no samples"))})' if recorded else "—")
            whole = row.get("clear_axis") == "anywhere"
            clear = _mapping(row.get("anywhere" if whole else "at_cut")).get("clean_replays_to_clear")
            clear_cell = ("cannot clear over the whole replay" if whole else "cannot clear at this turn — re-cut") if clear is None \
                else f'{clear} (whole replay)' if whole else str(clear)
            moved = {"not_pinned": " — **not pinned at its cut: moved to Recurring issue types**",
                     "measures_nothing": " — **measures nothing: no replay measured its check; re-cut it or retire it**",
                     }.get(str(row.get("status")), "")
            if row.get("hollow"):
                moved = f' — **dropped: hollow ({_cell(str(row["hollow"]))})**'
            invalid = f' · {len(_list(row.get("invalid")))} invalid replay(s)' if _list(row.get("invalid")) else ""
            invalid += " · not audited" if row.get("reading") and not _mapping(row.get("reading")).get("audited") else ""
            single_hop = row.get("replay_mode") == "single_hop"
            mode = " · single hop" if single_hop else " · full replays" if row.get("replay_mode") == "full" else ""
            anywhere = "— (single hop: the issue turn only)" if single_hop else _cell_counts(row.get("anywhere"))
            caveat = _mapping(row.get("replay_caveat"))
            jobs = ", ".join(f'`{_cell(str(j))}`' for j in _list(caveat.get("jobs")))
            job_open = (f' · **job open at the cut** ({jobs}): started before the cut and unfinished there; the replay starts without it'
                        if caveat.get("reason") == "job_open_at_cut" else "")
            reduced = int(row.get("restore_reduced") or 0)
            dropped = (f' · **resumed state dropped** on {reduced} replay(s): the harness did not restore it, so nothing was measured at the cut'
                       if reduced else "")
            L.append(f'| `{_cell(str(row.get("folder") or row.get("scenario")))}`{moved}{invalid}{mode}{job_open}{dropped} | '
                     f'`{_cell(str(row.get("owner") or "?"))}` ({_OWNER_SOURCE.get(str(row.get("owner_source")), "none")}) | {_cell_counts(row.get("at_cut"))} | '
                     f'{anywhere} | {at_record} | {clear_cell} |')
        L += ["", "_At the cut reads the owner on the turn the folder was recorded on; Anywhere reads it on the whole replay. "
                  "A folder replayed from its recorded opener clears on the whole replay. "
                  "The measured-at-record rate came from the fixture scorer on the local mock, these from the live run._", ""]
    if issue_types:
        L += ["## Recurring issue types", ""]
        for entry in issue_types:
            L.append(_issue_type_line(entry))
            for b in _list(entry.get("behind")):
                cuts = _list(_mapping(b).get("recut_candidates"))
                if cuts:
                    where = ", ".join(f'`{_cell(str(c.get("scenario")))}` turn {c.get("turn")}' for c in cuts[:5] if isinstance(c, dict))
                    L.append(f'  - `{_cell(str(b.get("folder")))}` did not recur at its cut; re-cut candidates: {where}.')
        L += ["", "_A check's rate counts every red of that check, which can be a different failure than the one recorded._", ""]
    return L


#: The SDK's payload-codec note keys on `egress.runtime` and the fields of each row (plurai `run_simulation._PAYLOAD_CODEC_NOTES`),
#: pinned against a record the SDK wrote (`scripts/tests/fixtures/egress_records/run_summary_egress_codecs.json`).
#: The guess the SDK gives a payload no codec in the vocabulary decodes (plurai `codecs.UNKNOWN_BINARY`); every other
#: guess is a chain verify_config accepts.
PAYLOAD_CODEC_UNKNOWN_BINARY = 'unknown binary'
PAYLOAD_CODEC_NOTE_FIELDS = {
    'undeclared_encoded_payloads': ('tool', 'path', 'guess'),
    'payload_codec_failures': ('tool', 'path', 'direction', 'reason'),
    'payload_codec_passthroughs': ('tool', 'path'),
}


def _payload_codec_lines(runtime: object) -> list[str]:
    """The SDK's payload-codec notes on `run_integrity.egress.runtime` (plurai >= 0.28.29), each a WARNING, never a failure.

    `undeclared_encoded_payloads` — an argument field the mock read encoded that no `tools.<name>.payload_codecs` row
    declares (its guess names the codec chain); `payload_codec_failures` — a declared codec that could not decode or
    encode, so the raw value went through; `payload_codec_passthroughs` — a reply already a string at a declared return
    path, passed through unencoded. Each row names a tool and a path, never the value. Present on a non-Python harness
    too, since the harness's Python child writes them."""
    if not isinstance(runtime, dict):
        return []

    def rows(key: str) -> list[dict]:
        value = runtime.get(key)
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    def at(row: dict) -> str:
        return f'`{_cell(str(row.get("tool")))}` `{_cell(str(row.get("path")))}`'

    parts: list[str] = []
    undeclared = rows("undeclared_encoded_payloads")
    if chains := [r for r in undeclared if r.get("guess") != PAYLOAD_CODEC_UNKNOWN_BINARY]:
        parts.append(f'  - {len(chains)} argument field(s) looked encoded and no `payload_codecs` row declares them, so the mock '
                     'read them encoded: ' + ', '.join(f'{at(r)} ({_cell(str(r.get("guess")))})' for r in chains)
                     + '. If a field is encoded, declare `tools.<tool>.payload_codecs: [{at: <path>, codec: [...]}]` with the '
                     'chain the guess names, and verify the config again')
    if binary := [r for r in undeclared if r.get("guess") == PAYLOAD_CODEC_UNKNOWN_BINARY]:
        parts.append(f'  - {len(binary)} argument field(s) carry a binary payload no codec decodes: ' + ', '.join(at(r) for r in binary)
                     + '. Mock that tool at `seam: client`, where its arguments are plain values')
    if failed := rows("payload_codec_failures"):
        parts.append(f'  - {len(failed)} declared codec(s) did not apply, so the raw value went through: '
                     + ', '.join(f'{at(r)} ({_cell(str(r.get("direction")))}: {_cell(str(r.get("reason")))})' for r in failed))
    if passed := rows("payload_codec_passthroughs"):
        parts.append(f'  - {len(passed)} reply field(s) were already a string at a declared return path and went through unencoded: '
                     + ', '.join(at(r) for r in passed))
    if not parts:
        return []
    return ['- **Payload codecs** — ⚠️ warning, not a failure: what the mock read or answered was not in the declared shape', *parts]


def render_md(spec: dict) -> str:
    t = spec.get("totals", {})
    # A run.json pooled over trials counts CONVERSATIONS (`totals.conversations`); every headline count below is over them.
    n, noun = counted_over(t)
    unit = noun.removesuffix("s")
    # The declared mock posture (build_run_json reads `<workdir>/.run_mode`). ABSENT means
    # unknown, which reads as connect — the conservative direction, since the alarm below is
    # about a run that was SUPPOSED to go through a mock.
    #
    # TWO questions, which `local` alone cannot answer at once:
    #   `unobserved` — did any mock WATCH these tool calls? No, under either local or mockless,
    #                  so the evidence is reconstructed from the transcript and the mock-bypass
    #                  alarm below is meaningless. This is what every suppression here wants.
    #   `mockless`   — was anything HELD CONSTANT? A local run holds a world constant in-process;
    #                  a mockless run holds nothing — every tool hit the real service. Declaring
    #                  the second as "local" would tell the reader the opposite of what happened.
    posture = spec.get("run_mode")
    mockless = posture == "mockless"
    unobserved = posture in ("local", "mockless")
    L = [f'# Run analytics — {spec.get("run_name", "run")}', ""]
    bits = []
    if spec.get("agent_version"):
        # The commit only: a `-dirty` suffix (an older run.json) is provenance for us (`agent_source`), not the customer's.
        bits.append(f'agent `{str(spec["agent_version"]).removesuffix("-dirty")}`')
    if spec.get("dataset_name"):
        bits.append(f'dataset `{spec["dataset_name"]}`')
    # The eval set and its revision are run.json's (`eval_set_id`, `gt_revision`): internal ids, never on the customer's head.
    if bits:
        L += ["  ·  ".join(bits), ""]
    # Said at the top as well as under Run integrity: it is the one line the operator acts on before sharing.
    home = _home_disclosure_line(_mapping(spec.get("run_integrity")).get("host_effects"))
    if home and "home_named" in home:
        L += [home.removeprefix("- "), ""]
    L += render_trials_md(spec)
    L += render_pinned_regressions_md(spec)
    if unobserved:
        # WHY the tool evidence looks the way it does, said once. Neither posture routes calls
        # through a deployed mock, so `/mock/execute` logs nothing and every call is rebuilt from
        # the transcript. Orthogonal to `local_only` below, which is about the platform.
        #
        # The SECOND sentence differs, because the postures differ in what they let a reader
        # conclude. A local run had a held-constant world and is reproducible; a mockless run
        # reached real services, so its results depend on live state nobody pinned. That is the
        # single most important thing about a mockless run, so it must not read as "local".
        #
        # And "every tool ran" is a claim about tools THIS AGENT HAS. On an agent whose config
        # declares none it would assert live traffic that could not have happened, so the declaration
        # (already on `run.json`) is consulted here.
        #
        # But the DECLARATION alone answers none of these sentences either: `declared_tools == 0`
        # alone would print "no tool calls were recorded" directly above a table of ten of them AND
        # suppress the mockless warning on the run that most needs it. It takes BOTH halves — what the config declared and what
        # the run actually recorded — and each of the four cells below reads differently. Neither
        # half is allowed to invent the other: an unread declaration is not a zero, and silence
        # is not evidence of a tool-less agent.
        declared = _declared_tool_count(spec)
        observed_tools = _observed_tool_activity(spec)
        if mockless and declared == 0 and not observed_tools:
            # The COHERENT tool-less run, and the only one this sentence describes: the
            # declaration was read, it says zero, and the run recorded nothing. Both halves are
            # required — `declared_tools == 0` alone would print it over a run with ten recorded
            # calls, and take the mockless warning down with it.
            L += ["_Tool-less agent — nothing to mock; no tool calls were recorded._", ""]
        elif mockless:
            # The reproducibility warning is INVARIANT here — no mocked surface stood up, so
            # nothing was held constant whatever the declaration says. Only the evidence clause
            # varies, and it never states more than this build looked at.
            if observed_tools and spec.get("world_owner") == "agent":
                # A harness-owned world IS held constant per scenario — the harness builds it and
                # seeds the scenario's rows — so "nothing was held constant" would be false here.
                evidence = ("every tool ran for real over the world the harness builds and seeds for each "
                            "conversation. Tool calls were recorded from the transcript; no mock observed them.")
            elif observed_tools:
                evidence = ("every tool ran against the real service, so nothing was held constant and this "
                            "run is not reproducible from its inputs alone. Tool calls were recorded from the "
                            "transcript; no mock observed them.")
            elif declared is None:
                # Neither claim is available: no call was recorded AND the declaration was never
                # read. "every tool ran against the real service" over a run where nothing ran is
                # the same manufactured fact as the tool-less verdict, pointing the other way.
                evidence = ("nothing was held constant here, so this run is not reproducible from its inputs "
                            "alone. NO tool call was recorded, and the declaration was never read — this build "
                            "cannot say whether that is an agent with no tools or a harness that reported none. "
                            "Do not read it as either.")
            else:
                evidence = ("nothing was held constant here, so this run is not reproducible from its inputs "
                            f"alone. NO tool call was recorded, over an agent that DECLARES {declared} tool(s) — "
                            "read that as a measurement gap, not as a clean tool surface.")
            L += [f"_No mocked surfaces — {evidence}_", ""]
        elif observed_tools:
            L += ["_Tool calls were recorded from the transcript — local run, no mock observed them. "
                  "State effects behind them were never independently verified._", ""]
        else:
            # Same false positive one branch over: "Tool calls were recorded" is a claim about
            # traffic, and a quiet local run has none to claim.
            L += ["_No tool call was recorded on this local run — no mock observed anything, and the "
                  "transcript reconstruction had nothing to rebuild._", ""]
    # WHO ran this. Absent ⇒ a human at the terminal. Present on a HEADLESS round ⇒ nobody was there, so
    # the reader (and the critic reading this file at the quality gate) is told WHICH decisions the
    # flow took on the run driver's behalf — and which prerequisites it refused to invent an answer
    # for, because an invented one would shape the whole suite unseen. Present on an INTERACTIVE round ⇒ a gate put to a person came back with no
    # answer; the person may have answered every other gate, so that is all this block says.
    # A final run's analytics is the customer's (`_customer`): the flow's own gates stay in run.json.
    run_driver = None if _customer(spec) else spec.get("run_driver")
    if isinstance(run_driver, dict) and run_driver.get("mode"):
        applied = [i for i in (run_driver.get("defaults_applied") or []) if isinstance(i, dict)]
        pending = [i for i in (run_driver.get("input_required") or []) if isinstance(i, dict)]
        # The INTERACTIVE half: a gate that WAS put to a person and came back with no answer.
        # Stated only when there is one — a `0 asked` on every headless round would assert that
        # this phase looked for asks it cannot have, since headless denies them all.
        unasked = [i for i in (run_driver.get("asks_unanswered") or []) if isinstance(i, dict)]
        # A gate reached with no run driver's answer whose own phase then landed: what was APPLIED.
        resolved = [i for i in (run_driver.get("gates_resolved") or []) if isinstance(i, dict)]
        # A stop whose gate's config key was present at a gate reached after it: no longer open, and
        # nothing attests who wrote the key.
        settled = [i for i in (run_driver.get("stops_settled_by_config") or []) if isinstance(i, dict)]
        # A record read only in part: what is named below is what COULD be read, never the whole record.
        partial = run_driver.get("record") == "partial"
        gaps = [i for i in (run_driver.get("unreadable") or []) if isinstance(i, dict)]
        who = f"**{run_driver['mode']}**"  # the setting that declared it is run.json's (`run_driver.declared_by`), never named here
        if applied or pending:
            counts = (f"{len(applied)} took a declared default, {len(pending)} had none and were "
                      "left unanswered (named below)")
            if unasked:
                counts += f", {len(unasked)} gate(s) were asked and no answer came back"
            if resolved:
                counts += f", {len(resolved)} were then decided by their own phase"
            L += [f"_Run driver: {who} — gates on this record: {counts}._", ""]
        elif unasked:
            # The two headless counts are NOT zero here, they are unobserved: both kinds are filed
            # only by a headless deny, so on an interactive round nothing could have recorded one.
            # And the answered asks are not projected into this block, so it cannot count them.
            L += [f"_Run driver: {who} — {len(unasked)} gate(s) were asked and no answer came back (named "
                  "below). This record does not count the gates that were answered, nor what any gate "
                  "decided._", ""]
        elif resolved:
            L += [f"_Run driver: {who} — {len(resolved)} gate(s) reached with no run driver's answer were then decided by "
                  "their own phase (named below)._", ""]
        elif settled:
            L += [f"_Run driver: {who} — {len(settled)} stop(s) were settled by a config key (named below)._", ""]
        elif partial:
            L += [f"_Run driver: {who} — no gate finding on this record could be read (named below)._", ""]
        else:
            # SCOPE and EVIDENCE, never the universal sentence. This block is built inside the RUN
            # phase from a findings file every arm sweeps, so an empty record is "no gate finding
            # reached this phase" — not "the flow answered every gate this round had". The gates
            # that shaped the suite (grounding-gate, which-kb) fire in an
            # earlier phase and cannot appear here at all; saying so is the difference between a
            # record and an attestation.
            L += [f"_Run driver: {who} — no gate findings were recorded for this phase (findings read from: "
                  "this run's `run.json` `run_driver` block, written in the run phase). What the earlier "
                  "phases' gates reached, and what they answered, is not in this file._", ""]
        if partial:
            L += [f"_This run driver record is PARTIAL — {len(gaps) or 'some'} entr(ies) could not be read, so the gates "
                  "named here are the ones that could be, not every gate this round reached:_", ""]
            L += [f"- `{str(g.get('base') or 'unknown')}` "
                  + (f"line {g['line']}" if g.get("line") else "(the whole file)") + f" — {str(g.get('why') or 'unstated')}"
                  for g in gaps]
            L += [""]
        # WHICH ones, by name and by the answer they took. A reader told only that defaults "were
        # resolved" cannot check any of them; the list is the whole point of recording it.
        if applied:
            L += [f"_{len(applied)} gate(s) took a declared default instead of a run driver's answer:_", ""]
            L += [f"- `{str(item.get('gate') or 'unknown')}` → `{str(item.get('default') or 'unstated')}`"
                  for item in applied]
            L += [""]
        if pending:
            L += [f"_{len(pending)} gate(s) had NO declared default and were NOT guessed — the run needs "
                  "a run driver for:_", ""]
            L += [f"- `{str(item.get('gate') or 'unknown')}` — {str(item.get('needs') or 'unstated')}"
                  for item in pending]
            L += [""]
        if resolved:
            L += [f"_{len(resolved)} gate(s) reached with no run driver's answer — what their own phase then applied:_", ""]
            L += [f"- `{str(item.get('gate') or 'unknown')}` → `{str(item.get('applied') or 'unstated')}` "
                  "(recorded after the no-ask default or stop)" for item in resolved]
            L += [""]
        if settled:
            L += [f"_{len(settled)} stop(s) are no longer open because the gate's config key was present when the "
                  "gate was reached again. The writer is not attested: the run driver's edit and a key the flow wrote "
                  "itself leave the same trace, so this is not recorded as the run driver's answer:_", ""]
            L += [f"- `{str(item.get('gate') or 'unknown')}` — stopped in the "
                  f"`{str(item.get('command') or 'unknown')}` flow: {str(item.get('needs') or 'unstated')}"
                  for item in settled]
            L += [""]
        # …and the gates a person WAS asked about and never answered. Named, and no further: the
        # hook that files this one sees the call come back empty, not what the flow then applied —
        # so this block says the question went unanswered, never which option was taken.
        if unasked:
            L += [f"_{len(unasked)} gate(s) were ASKED and no answer came back — what happened at them "
                  "next is not recorded, and nothing here attests that a run driver chose it:_", ""]
            L += [f"- `{str(item.get('gate') or 'unknown')}` — asked in the "
                  f"`{str(item.get('command') or 'unknown')}` flow; no answer was returned"
                  for item in unasked]
            L += [""]
    if spec.get("local_only"):
        L += ["_Generated locally — no observability platform recorded this run "
              "(none configured / key missing / Langfuse has no auto-charts). "
              "This is the analysis you'd otherwise read on the platform._", ""]
    L += ["## Summary", "", "![Run summary](charts/run_summary.svg)", ""]
    # Whenever there ARE evals — not only when one of them decided something. `render_evals_svg`
    # renders its own precondition ("no eval reached a verdict on this run"), and gating the call
    # on `any(_conclusive(...))` would make that branch unreachable: an all-inconclusive eval set
    # would skip the chart entirely instead of rendering the reason there is no bar to draw.
    if spec.get("evals"):
        L += ["## Eval pass-rate", "", "![Eval pass-rate](charts/evals_passrate.svg)", ""]
    # BEFORE the per-scenario table on purpose: both sections say how to READ the rows below —
    # whether this run measured what it claims to, and which zeroed families nothing could have
    # passed. A reader who meets them after the table has already read the table wrong.
    L += render_run_integrity_md(spec)
    # Beside run integrity and for the same reason: it says how to READ a red below. A
    # membership verdict on a class nothing checked is a judge's reading, not a measurement.
    L += render_closed_class_detectors_md(spec)
    L += [] if _customer(spec) else render_user_sim_fidelity_md(spec)
    L += render_outcome_attribution_md(spec)
    # A TOOL-LESS agent (pure-LLM / prompt-inlined knowledge) deploys no mock and writes no
    # tool_events.json, so every row would read "0" under "Tool calls" — indistinguishable
    # from an agent that HAD tools and never called them, which reads as a defect. Omit the
    # column when the whole run has none: absent means "not applicable", zeros mean "it did
    # nothing".
    # …and a row whose channel was DECLARED unread keeps the column at zero. Omitting it there
    # renders the strongest form of the finding as "not applicable": the run did have a tool
    # channel, nothing read it, and that is a fact about the RUN rather than about the agent.
    _scenarios = spec.get("scenarios", [])
    show_tools = any(s.get("tool_calls") or s.get("tool_errors") or s.get("tool_refusals")
                     or s.get("tool_events_unread") for s in _scenarios)
    # REFUSALS are application-level: the dispatch succeeded and the tool answered with an
    # error-shaped payload envelope. A different finding from `tool_errors` (framework/dispatch
    # failures), so it renders beside it, never merged into it. Gated on FIELD PRESENCE: an
    # older run.json predates the field and the count cannot be recovered from what it does
    # carry, so the annotation is simply absent — the same refusal-to-fabricate posture as
    # `needs_review` / `policy_violations`, never a silent zero.
    show_refusals = any("tool_refusals" in s for s in _scenarios)
    # RECONSTRUCTED calls are records the aggregator rebuilt from the transcript because
    # `/mock/execute` never logged them — the tool bypassed the mock, so its status and state
    # effect are synthetic. The count is in run.json; without consulting
    # it the report would say nothing when most calls were reconstructions.
    # Gated on FIELD PRESENCE like `show_refusals` — an older run.json predates the per-scenario
    # field and the count cannot be recovered from what it carries, so the annotation is absent
    # rather than a silent zero — and on the declared POSTURE (below).
    # …and suppressed on a declared UNOBSERVED run (local or mockless). With no mock watching,
    # every call is reconstructed by construction, so annotating every row says nothing and reads
    # as a per-scenario fault; the provenance line states it once instead.
    #
    # NOT gated on partial observation: in a CONNECT run where ONE scenario's tools all bypassed
    # the mock (10/10 on that row, 10/100 run-wide — under the headline share) a gate would
    # render that row identically to a clean one. A fully bypassed scenario is the strongest
    # form of the finding this annotation exists to make.
    show_recovered = not unobserved and any("tool_calls_recovered" in s for s in _scenarios)
    # The DETERMINISTIC criterion is judged outside the policy set — it never moves pass_rate,
    # so a failed check would be invisible in this report beside a 0.95 pass_rate. Shown as its own column, absent only when no scenario declares one (a column
    # of dashes would read as a broken feature, not as "not applicable").
    # THE TOOL TRAIL, beside the count. Gated on FIELD PRESENCE like the two annotations above:
    # an older run.json predates the signature entirely and the plan cannot be recovered from a
    # count, so the column is absent rather than a column of dashes. Independent of `show_tools`,
    # because a run whose every scenario dispatched NOTHING still has a trail fact worth stating
    # — "no calls" is the one reading a cell delta may never be attributed against.
    show_trail = any("trail_signature_mode" in s for s in _scenarios)
    show_det = any(isinstance(s.get("deterministic"), dict) for s in _scenarios)
    # The doctrine's own rule: any rendering of pass_rate names the inconclusive and skipped
    # policies. Both dent (or silently spare) a scenario's number without producing a finding,
    # so a reader who cannot see them reads the score as noise.
    show_hedged = any(s.get("inconclusive_evals") for s in _scenarios)
    # N/A IS ITS OWN COLUMN, not a share of the one beside it. The premise never arose (or the
    # dialog that would have carried it was cut), so the gate worked — while `Inconclusive` means a
    # judge saw a live premise and could not decide, which is a rubric to fix. Merged, the column
    # would contradict its own caption, since most cells under it are the gate working. Absent
    # when no scenario has one, like every other conditional column here: a column of dashes reads
    # as a broken feature, and an older run.json predates the field.
    show_not_applicable = any(s.get("not_applicable_evals") for s in _scenarios)
    # GRADED UNDER: an eval that decided and handed the case to the eval it defers to (its owner's verdict carries it).
    # Its own column, never `Inconclusive`: the judge decided.
    show_deferred = any(s.get("deferred_evals") for s in _scenarios)
    show_skipped = any(s.get("skipped_evals") for s in _scenarios)
    # NEEDS REVIEW is the cells that reached no verdict (Inconclusive or Not applicable) whose
    # majority leaned VIOLATED — a lens on those two columns, not a further state. Its own column because a vote split is a magnitude with no
    # direction: without it a 2/3 majority that the agent lied and a 2/3 majority that it did
    # not render as the same cell. An older run.json has no such field and the lean cannot be
    # recovered from a name list, so the column is simply absent — the same refusal as
    # `policy_violations` below, never a silent zero.
    show_review = any(s.get("needs_review") for s in _scenarios)
    # ENDED EARLY — the dialog stopped for something other than the user reaching its goal.
    # Its own column, right after Turns, because it is what makes the turn count and the score
    # beside it readable: a scenario cut at turn 6 of a 20-turn budget scores low for every
    # obligation it never reached, and nothing else in this table says the conversation was cut.
    # Absent when NO scenario carries one — a column of dashes reads as a broken feature, and an
    # older run.json predates the field entirely.
    show_early = any(s.get("ended_early") for s in _scenarios)
    head = ["Scenario", "Goal", *(["Deterministic"] if show_det else []), "Score", "Turns",
            *(["Ended early"] if show_early else []),
            *(["Tool calls"] if show_tools else []),
            *(["Tool trail"] if show_trail else []), "Failing evals",
            *(["Needs review"] if show_review else []),
            *(["Not applicable"] if show_not_applicable else []),
            *(["Graded under another eval"] if show_deferred else []),
            *(["Inconclusive"] if show_hedged else []), *(["Not scored"] if show_skipped else [])]
    L += ["## Per scenario", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    # ⏳ is its own mark for its own state, never folded into ❌ or 💥: the scenario ran, was
    # graded, and simply did not get to finish. A ❌ would report the agent failing a
    # conversation it never had; a 💥 would send the reader hunting an error.json that
    # does not exist. The Ended-early column beside it says which budget ran out.
    # ➖ is its own mark for its own state, never folded into ❌ or 💥: the backend reached no GOAL
    # verdict on that row — nothing owned the goal, the measurement did not hold, or the run
    # predates the block. A ❌ would report the agent failing something nobody asked of it.
    icon = {"pass": "✅ goal met", "fail": "❌ goal not met", "error": "💥 error", "incomplete": "⏳ incomplete",
            "not_evaluated": "➖ not evaluated"}
    # The runner's per-scenario stability rows, keyed by scenario — the per-policy reproduction
    # marks live there and are rendered beside each failing eval below.
    stability_rows = (spec.get("run_integrity") or {}).get("stability") if isinstance(spec.get("run_integrity"), dict) else None
    stability_rows = stability_rows if isinstance(stability_rows, dict) else {}
    for s in _scenarios:
        score = f'{s["score"]:.2f}' if s.get("score") is not None else "—"
        # A SIMULATION budget expiring gets its own cell rather than the generic crash icon.
        # Both are "scored nothing", but they point at different people: 💥 sends the reader
        # to the agent, ⏱ sends them to the run budget. Collapsing them is how a harness
        # timeout gets written up as an agent defect.
        #
        # Gated on the row states that can HAVE a kind, not on `error` alone: an all-guards-
        # gated scenario is `not_evaluated` (nothing crashed) and carries
        # `no_applicable_policies`, which is a different repair from "no eval owns a goal". Still gated: run.json is a hand-authorable artifact (the
        # shipped example is copied, not read), and an error_kind that strayed onto a PASSING
        # row would otherwise render a green scenario as a timeout while every total counted it
        # as passed.
        kind = s.get("error_kind") if s.get("result") in ("error", "not_evaluated") else None
        if kind in _TIMEOUT_KINDS:
            result_cell = "⏱ timeout"
        elif kind == _NO_APPLICABLE_POLICIES:
            result_cell = "🚫 no evals apply"
        elif kind == _HARNESS_SEED_STATE_CONTRACT:
            result_cell = "🧩 harness seed_state"
        elif kind == _HARNESS_FAULT:
            result_cell = "🧩 harness fault — fix the harness code its error.json names"
        elif kind == _HARNESS_MOCK_RUNTIME:
            result_cell = f"🧩 mock runtime — {_cell(mock_runtime_remedy(s.get('mock_runtime') or {}))}"
        elif kind == HARNESS_MOCK_UNREACHABLE:
            result_cell = f"🧩 {mock_unreachable_remedy(s.get('mock_unreachable') or {})}"
        elif kind == BACKEND_HTTP:
            result_cell = f"🌐 {_cell(backend_http_remedy(s.get('backend_http') or {}))}"
        elif kind == PROVIDER_INFRA:
            result_cell = f"🌐 {PROVIDER_INFRA_REMEDY}"
        else:
            result_cell = icon.get(s.get("result"), s.get("result", "?"))
        # WHY, on the row itself, in the two cases where the rest of the table cannot say it.
        #
        # A bare ➖ in a column of ✅/❌ reads as a rendering gap, and "nothing owned the goal"
        # vs "the measurement did not hold" send the reader to two different repairs.
        #
        # And a ❌ whose violation appears NOWHERE ELSE on the row. `decisive_violations` is what
        # `/finalize` failed the run on, and it is a SUPERSET of the eval cells: a turn-scoped
        # `terminate_values` STOP floor fires with every cell clean, so the row renders a red
        # beside `Failing evals: —` — a failure with no evidence anywhere a reader can see. Only
        # the names the eval column does not already carry, so an ordinary red is not printed
        # twice.
        goal = s.get("goal") if isinstance(s.get("goal"), dict) else {}
        high_veto: list = []
        if s.get("result") == "fail" and s.get("error_kind") == _AGENT_RAISED:
            # No check ran: the agent raised, and its goal block says so (owned or not, never a critical-check veto).
            result_cell = f'❌ {_cell(str(goal.get("reason") or "the agent raised in its own turn"))}'
        # A `fail` row the SDK scored says WHY, in its own reasons (`fail_reasons`, copied by build_run_json from the
        # scores block): the score, the checks, the goal, a crash — never re-derived here.
        elif s.get("result") == "fail" and isinstance(s.get("fail_reasons"), list) and s["fail_reasons"]:
            said = " · ".join(_FAIL_REASON_WORDS.get(str(r), str(r)) for r in s["fail_reasons"])
            row_score = s.get("score")  # not `score`: that name holds this row's Score cell, rendered above
            shown = f" (score {row_score:.1f})" if isinstance(row_score, (int, float)) and not isinstance(row_score, bool) else ""
            met = " · goal met" if goal.get("goal_status") == "passed" else ""
            # The required checks it failed are named by the reasons: the high-severity count below would say it twice.
            high_veto = list(s.get("high_severity_failed") or []) if "failing_tier" in s["fail_reasons"] else []
            # The checks behind those reasons that the eval column does not show (a red with no cell) are named here.
            in_column = {policy_name(f) for f in (s.get("failing_evals") or [])}
            behind = [*(s.get("critical_failed") or [] if "critical_failure" in s["fail_reasons"] else []), *high_veto]
            unnamed = [str(v) for v in dict.fromkeys(behind) if str(v) not in in_column]
            named = f' ({", ".join(f"`{_cell(v)}`" for v in unnamed)})' if unnamed else ""
            result_cell = f"❌ {said}{named}{shown}{met}"
        # A `fail` row the goal did not fail was vetoed by a critical check — or, with none, by a high-severity
        # one: name it, and say what the goal says (a row built without the scores block).
        elif s.get("result") == "fail" and goal.get("goal_status") in ("passed", "not_evaluated", "unclean", "unknown"):
            vetoed_by = s.get("critical_failed") or []
            high_veto = [] if vetoed_by else list(s.get("high_severity_failed") or [])
            kind = "a high-severity check" if high_veto else "a critical check"
            named = f' ({", ".join(f"`{_cell(str(v))}`" for v in vetoed_by or high_veto)})' if vetoed_by or high_veto else ""
            result_cell = (f"❌ goal met, but {kind} failed{named}" if goal.get("goal_status") == "passed"
                           else f"❌ {kind} failed{named} · goal not measured")
        unshown = [n for n in (goal.get("decisive_violations") or [])
                   if str(n) not in {policy_name(f) for f in (s.get("failing_evals") or [])}]
        if goal.get("reason") and (s.get("result") == "not_evaluated" or unshown):
            result_cell += f' — {_cell(str(goal["reason"]))}'
        # A high-severity failure beside a critical veto (or a passing row from an older backend) is counted here.
        if (high := s.get("high_severity_failed")) and not high_veto:
            result_cell += f' · ⚠ {len(high)} high-severity check{"s" if len(high) != 1 else ""} failed'
        elif "high_severity_failed" in s and s["high_severity_failed"] is None:
            result_cell += " · high-severity checks not reported"
        # …and WHOSE WORLD THE BACKEND WOULD NOT VOUCH FOR, on the row itself. `build_run_json`
        # stamps `world_state_trustworthy: False` per named row and NOTHING read it: the finding
        # reached the reader only as a run-integrity bullet naming the rows and telling them to
        # "read those rows against the world snapshot" — a name-by-name hand-join against a table
        # that carried no mark at all. The doubt is about THIS row's verdict, so it belongs beside
        # THIS row's verdict. `is False` only: an absent key is a runner that never said, and a
        # mark on it would put a measurement on a row nobody made one for.
        if s.get("world_state_trustworthy") is False:
            result_cell += " ⚠️ world uncertified"
        # Bound once: the two mark reads below are one row's, and `.get` on a stringified id twice
        # in one expression is the kind of restatement that lets the halves drift apart.
        stability_row = stability_rows.get(str(s.get("id")))
        cells = [
            s["id"],
            result_cell,
            *([_det_cell(s.get("deterministic"))] if show_det else []),
            score,
            _turns_cell(s),
            *([_ended_early_cell(s.get("ended_early"))] if show_early else []),
            *([_tools_cell(s, show_refusals, show_recovered)] if show_tools else []),
            *([_trail_cell(s)] if show_trail else []),
            _failing_evals_cell(s.get("failing_evals"),
                                valid_marks(stability_row, "policy_reproduction"),
                                valid_marks(stability_row, "policy_evidence"),
                                sample_kind=stability_row.get("sample_kind") if isinstance(stability_row, dict) else None,
                                selection=stability_row.get("selection") if isinstance(stability_row, dict) else None),
            *([_names(s.get("needs_review"))] if show_review else []),
            *([_names(s.get("not_applicable_evals"))] if show_not_applicable else []),
            *([_names(s.get("deferred_evals"))] if show_deferred else []),
            *([_names(s.get("inconclusive_evals"))] if show_hedged else []),
            *([_names(s.get("skipped_evals"))] if show_skipped else []),
        ]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    if any(s.get("unmeasured_turns") for s in _scenarios):
        L += ["_**turns unmeasured** — on those turns nothing about the AGENT was measured, so no "
              "verdict there is a finding about it. `(harness)` means the runner emptied the turn: "
              "no agent speech reached the backend and no action was declared, and the judges were "
              "never invoked rather than grading an utterance that does not exist. Unlabelled means "
              "the run did not say whose it was — read the cells before attributing it._", ""]
    # The mark's legend, and the REASON the run-integrity bullet holds — printed once, beneath the
    # rows that carry the mark, rather than joined by hand from a list of names three sections up.
    if distrusted := [s for s in _scenarios if s.get("world_state_trustworthy") is False]:
        why = " · ".join(f'`{_cell(str(s["id"]))}` — {_cell(str(s["world_state_untrustworthy_reason"]))}'
                         for s in distrusted if s.get("world_state_untrustworthy_reason"))
        voided = [s for s in distrusted if _voided_trials(spec, str(s.get("id")))[0]]
        scored = [s for s in distrusted if s not in voided]
        said = ([f'{len(voided)} row(s) above were NOT scored: their world could not be certified, so their trials '
                 "reached no verdict and count in the pass rate's denominator as unmeasured."] if voided else []) + (
            [f'{len(scored)} row(s) above were SCORED over a world state the backend declined to certify. The cells '
             'landed and the verdicts are measurements' + ('' if voided else ', and no rate on this page is dented by the doubt')
             + " — what is in doubt is the WORLD those verdicts were reached over, so check the row's world snapshot "
             'before acting on one of its findings.'] if scored else [])
        L += ['_**⚠️ world uncertified** — ' + ' '.join(said)
              + (f' {why}.' if why else " The runner named no reason.") + "_", ""]
    # The cell annotation's legend, printed once beneath the rows that carry it — the same
    # treatment the floor and the unclassified outcomes get in Run integrity, for the same class
    # of fact: a number in the table above that is not the measurement it looks like.
    if unread := [s for s in _scenarios if s.get("tool_events_unread")]:
        named = " · ".join('`' + _cell(str(s.get("id") or "?")) + '`' for s in unread)
        L += [f'_**channel unread** — {len(unread)} row(s) above ({named}) '
              "DECLARED their tool channel unread: the runner wrote a literal `null` `tool_events.json` for "
              "them, so their `0` is an absence of observation and NOT a measured zero — nothing watched "
              "those scenarios' tools at all. Read every tool number on those rows as silent about them, "
              "never as a tool-less agent, and fix the runner's channel before reading a dead-tool finding "
              "off one._", ""]
    if show_trail:
        modes = sorted({str(s["trail_signature_mode"]) for s in _scenarios if s.get("trail_signature_mode")})
        L += [f'_**Tool trail** — a digest of the scenario\'s tool plan under `{", ".join(modes)}` '
              "(each dispatch as `tool(argument keys)`, in order). It is an EQUALITY handle: two runs of the "
              "same suite whose digests match ran the same plan. Two that differ moved the plan, and how far "
              "that is from this suite's own run-to-run variance is a question ONE run cannot answer — "
              "`skills/simulation-run/scripts/compare_runs.py` answers it from two runs plus the "
              "same-configuration runs a floor is measured on. A `repeat` figure here is the runner's own "
              "second attempt at that scenario, and it is EVIDENCE, not that floor: the runner retries only "
              "scenarios that failed, so a clean run produces no repeat at all and a repeat-derived floor is "
              "the floor of the failures._", ""]
    if show_review:
        L += ["_**Needs review** — the judge did not reach a verdict, but its majority leaned "
              "VIOLATED. Not counted as a failure anywhere in this report; read the cell._", ""]
    # …and the OTHER axis on the same failures: the agent's own sentences, each printed once
    # with every eval that charged it. Directly under Per scenario because it is that table's
    # drill-down — the reader arrives from a `Failing evals` cell wanting to know how many
    # distinct things the agent actually did.
    L += render_rooted_findings_md(spec)
    # The goals fraction NEVER ships alone. `pass` is the backend's GOAL verdict, not
    # `pass_rate == 1.0`, so a run can honestly read `2 of 4 goals achieved` while averaging
    # 0.83 across its policies — and an operator reading the fraction on its own would take that
    # shape as catastrophic. Mean score + violation
    # count are the context that makes the headline honest, so they are part of the line, not
    # options.
    # `policy_violations` counts CONCLUSIVELY violated (scenario, policy) cells only. An
    # older run.json predates the field and its shape CANNOT reconstruct it:
    # `failing_evals` is every NON-PASSING cell, so inconclusive and errored cells are in
    # it too, and summing `len(failing_evals)` would print that inflated total
    # under the label "policy violations" — the exact conflation Step 4b forbids, because
    # it overstates the customer's agent's misbehaviour. Omit the figure rather than print
    # a wrong one; the mean score still keeps the passed fraction from travelling alone.
    # Denominator: `goals_measurable` on the goal axis (matching the summary card), and the
    # SCORED scenarios on the legacy axis. Either way an errored scenario produced no verdict,
    # so it is named beside the rate and never buried in it.
    violations = t.get("policy_violations")
    # The per-row counts below run over every row, single-hop replays included: named as such, never as conversations.
    rows_noun = pooled_rows_noun(t, noun)
    # "N of M goals achieved" — the backend's own verdict, not "did every policy pass".
    # M counts the scenarios something OWNED a goal for; the rows nobody evaluated are named
    # beside the rate, never inside it, the same convention `errored` and `incomplete` follow.
    # An older run.json carries no goal axis at all, and takes the passed-of-scored line below.
    if "goals_measurable" in t:
        if t.get("goals_measurable"):
            value, _label = goals_line(t)
            parts = [f'{value} goals achieved']
        else:
            parts = [f'no goal was owned AND measured on any of the {n} {rows_noun} — read the per-row reasons below '
                     'to see which it was: no eval owns a goal, or nothing published a verdict']
        # The rows INSIDE the fraction that reached no verdict. Said explicitly, because they are
        # the difference between "the agent missed 2 of 4 goals" and "the agent missed 1 and one
        # judge died" — arithmetically identical in the fraction, and opposite findings.
        if t.get("goals_unverdicted"):
            parts.append(
                f'{t["goals_unverdicted"]} of those {t["goals_measurable"]} owned goals reached NO verdict — they are '
                'inside the denominator and can never be inside the numerator, so read that shortfall as unmeasured, '
                'not as the agent failing'
            )
        # …and the rows OUTSIDE it, for the opposite reason: nothing owned their goal at all.
        # Derived ON ONE AXIS, never as `not_evaluated - goals_unverdicted`: those are
        # counts of two different things — `not_evaluated` is a row VERDICT (the scenario axis),
        # `goals_unverdicted` is a GOAL-axis count, and the two are not subsets of each
        # other because the goal axis is uncoupled from the row verdict. The
        # difference could go negative, and the `> 0` guard below would silently delete this
        # line — the "nothing asked anything of these rows" finding vanishing exactly on the runs
        # that have one. The denominator's own complement has no such problem: a row is outside
        # the fraction because it errored, because the clock censored it, or because nothing owned
        # its goal, and those three are disjoint and exhaustive over the scenario count.
        # A single-hop replay row is the fourth disjoint reason (`single_hop_goal_rows`): its goal is not graded by design.
        hop_goals = t.get("single_hop_goal_rows") or 0
        unowned = (n or 0) - (t.get("goals_measurable") or 0) \
            - (t.get("errored") or 0) - (t.get("incomplete") or 0) - hop_goals
        if hop_goals:
            parts.append(f'{hop_goals} replay{"" if hop_goals == 1 else "s"} of a recorded turn '
                         f'{"is" if hop_goals == 1 else "are"} out of the fraction — a replay grades that turn, never the goal')
        if unowned > 0:
            parts.append(
                f'{unowned} of {n} had no goal owner at all — out of the fraction entirely, because a {unit} '
                'nothing asked anything of did not fail'
            )
    else:
        parts = [f'{t.get("passed", 0)}/{scored_scenarios(t)} scored {noun} passed']
    # Beside the goals, never inside them, and FIRST: a met goal can still fail the run on a critical check.
    if critical := t.get("scenarios_with_critical_failures"):
        parts.append(f'{critical} of {t["critical_reported"]} {rows_noun} failed a critical check')
    # Beside the goals, never inside them: a met goal can still carry a high-severity failure.
    unreported = t.get("high_severity_unreported")
    if (high := t.get("scenarios_with_high_severity_failures")) is not None:
        parts.append(f'{high} of {t.get("high_severity_reported", n)} {rows_noun} failed at least one high-severity check'
                     + (f' (high-severity checks not reported for {unreported} more)' if unreported else ''))
    elif unreported:
        parts.append(f'high-severity checks not reported for {unreported} of {n} {rows_noun}')
    # What did not run is C7's count, by whose side: an agent that raised in its turn is a scored failure, never here.
    try:
        parts += run_numbers.invalid_parts(spec)
    except run_numbers.C7Missing as exc:
        parts.append(f'not counted — {exc}')
    # INCOMPLETE. Beside the rate, never inside it — the same convention as `errored`, for the
    # opposite reason: these DID score. What they did not do is finish, so the verdicts are over
    # a conversation the clock cut, and they belong to neither column of a pass rate. Said on the
    # headline because the alternative is a reader doing the arithmetic and concluding the run
    # lost scenarios it did not lose.
    if t.get("incomplete"):
        parts.append(
            f'{t["incomplete"]} of {n} were cut by the harness (the scenario TIME budget, the per-turn wall or the agent\'s '
            'model provider) and landed INCOMPLETE — graded to the turn each one reached, so they neither passed nor '
            'failed and are in no rate here'
        )
    # Scenarios that wrote NO artifact at all are not rows in this report and not in `n` —
    # they are the run's silent losses, so they are named on the headline. Without this a
    # 7-scenario run that lost 2 reads as "5/5 passed", a clean 100% on an incomplete run.
    # Beside the rate, never inside it — the same convention as `errored`.
    if t.get("missing_scenarios"):
        parts.append(f'{t["missing_scenarios"]} {unit}(s) produced NO artifacts — this report is not the whole run')
    # The runner says the evals never ran, so every score below is a placeholder 0.0 meaning
    # "not measured". Said FIRST-CLASS on the headline: a rate computed over placeholders is
    # the one number in this report that describes nothing at all.
    if t.get("unscored_run"):
        parts.append('this run was NEVER SCORED — the verdicts below are placeholders, not measurements')
    # A PARTLY graded run says how much of it was measured, and never borrows the banner above.
    # `scored` is `graded >= scenarios`, so ONE ungraded row would render the whole report as
    # placeholders. The rows that graded are real measurements; this says how many.
    elif (graded := t.get("graded_scenarios")) is not None and graded < t.get("scenario_total", 0):
        parts.append(f'{graded} of {t["scenario_total"]} {unit}(s) graded — the rest reached no verdict, '
                     'so every rate here is over the graded rows only')
    # A simulation budget is called out BY NAME. It is already inside `errored` (it scored
    # nothing, so every denominator still excludes it) — this line only says WHOSE fault the
    # zero is, because the default reading of an errored scenario is that the agent crashed.
    if t.get("harness_timeouts"):
        count = t["harness_timeouts"]
        # Name the budget that ACTUALLY expired. The per-turn, whole-scenario and finalize-poll
        # ceilings mean different things, so a single generic sentence hands half these runs a
        # remediation that does not touch the budget they hit. The per-turn line names NO knob:
        # the turn may have been waiting on the simulated tools, which no budget fixes.
        kinds = {s.get("error_kind") for s in _scenarios if s.get("result") == "error"}
        advice = sorted(_BUDGET_ADVICE[k] for k in kinds & _TIMEOUT_KINDS)
        parts.append(
            f'{count} {unit}(s) hit a SIMULATION budget — not scored, and not a verdict '
            f'about the agent ({"; ".join(advice)})' if advice else
            f'{count} {unit}(s) hit a SIMULATION budget — not scored, and not a verdict about the agent'
        )
    # RECONSTRUCTED calls, when they DOMINATE. The per-scenario column already names every one;
    # this line exists because a run whose tool evidence is mostly synthetic is not the run the
    # reader thinks they are reading — the mock logged none of those calls, so their arguments
    # and results are the agent's own account of them. It does NOT follow that the state effects
    # went unseen: the state_update lane and the world snapshot are written by the writes
    # themselves, so a world-state eval over them is authoritative and the wording must not
    # invite a reader to discount it. An older run.json carries no such total, so the line is
    # simply absent rather than a claimed 0%.
    # …but NEVER on a declared UNOBSERVED run (local or mockless): there is no deployed mock to
    # bypass, so the same ratio that means "the agent went around the mock" on a connect run is
    # simply how those postures work.
    # Rendering it there would send an operator hunting a fault that is not one.
    # `tool_calls_mocked` / `_mocked_recovered`: the calls to tools not declared `mode: real` — the only ones a mock could have seen.
    mocked = t.get("tool_calls_mocked")
    if isinstance(mocked, int):
        recovered, calls, noun = t.get("tool_calls_mocked_recovered"), mocked, "calls to mocked tools"
    else:
        recovered, calls, noun = t.get("tool_calls_recovered"), t.get("tool_calls") or 0, "tool calls"
    if not unobserved and isinstance(recovered, int) and calls and recovered / calls >= _RECOVERED_HEADLINE_SHARE:
        parts.append(
            f'{recovered}/{calls} {noun} were RECONSTRUCTED from the transcript — those tools '
            "bypassed the mock, so their arguments and results are the agent's own account of "
            "them; the world snapshot still records what actually changed"
        )
    # ENDED EARLY. Not a verdict about the agent either — a statement about whether the dialog
    # got to finish. It belongs on the headline for the same reason `unscored_run` does: a
    # scenario the simulated user abandoned, or one the turn budget cut, scores low for every
    # obligation its conversation never reached, and with nothing said the report and the suite
    # critic both read that as the agent's failure. ONE LINE PER DISTINCT REASON, each with its
    # own remedy — a single line built from a count would have to offer every remedy at once,
    # and a run whose scenarios all ended because the user gave up would be told to raise its
    # turn budget. Absent when nothing ended early (the aggregator omits the block).
    cut = spec.get("ended_early")
    if isinstance(cut, dict) and cut.get("reasons"):
        parts.extend(_ended_early_parts(cut, spec.get("dead_tools")))
    # USER-SIM FIDELITY. Not a verdict about the agent — a statement about whether the
    # TRANSCRIPTS enact the scenarios they were drawn from. It belongs on the headline for the
    # same reason `unscored_run` does: when the simulator broke its own schedule, the turns
    # every turn-indexed judge graded are not the turns the scenario specified, and no eval fix
    # repairs that. Printed only when something was MEASURED and something was unclean —
    # an absent block means an older backend that wrote none, not a clean run.
    usi = None if _customer(spec) else spec.get("user_sim_integrity")
    unchecked = [str(n) for n in (usi.get("unchecked") or [])] if isinstance(usi, dict) else []
    if unchecked:
        # NOT a simulator finding: the evaluator-voice judge did not answer for these transcripts,
        # so the scenario was neither cleared nor faulted on that axis. Said in its own sentence so it cannot be mistaken for the
        # departure below, and so `clean` short of `measured` is never a silent number.
        parts.append(
            f'user-sim fidelity NOT MEASURED on {len(unchecked)}/{usi["measured"]} scenario(s) — the evaluator-voice '
            f'judge did not answer ({", ".join(unchecked)}); nothing departed, nothing was cleared'
        )
    departed = isinstance(usi, dict) and usi.get("measured") and (usi.get("clean", 0) + len(unchecked)) != usi.get("measured")
    if departed:
        kinds = ", ".join(f'{k}×{n}' for k, n in (usi.get("anomaly_kinds") or {}).items())
        where = "; ".join(f'{name} turns {",".join(str(t) for t in turns)}'
                          for name, turns in (usi.get("anomaly_turns") or {}).items())
        parts.append(
            f'user-sim fidelity {usi["clean"]}/{usi["measured"] - len(unchecked)} measured scenario(s) clean — the simulator '
            f'departed from the scenario schedule ({kinds or "unnamed anomalies"}'
            + (f'; {where}' if where else "")
            + '), so turn-indexed verdicts on those turns grade a dialog the scenario did not specify'
            + _undelivered_pointer(usi.get("scripted_delivery"))
        )
    unreadable_ledger = usi.get("self_report_unreadable") if isinstance(usi, dict) else None
    if isinstance(unreadable_ledger, list) and unreadable_ledger:
        parts.append(
            f'user-sim ledger UNREADABLE on {len(unreadable_ledger)} scenario(s) ({", ".join(str(n) for n in unreadable_ledger)}) — '
            'the backend published a self-report table this report cannot parse; neither cleared nor flagged'
        )
    noted = usi.get("self_report_turns") if isinstance(usi, dict) else None
    if isinstance(noted, dict) and noted:
        # NOT a departure: the simulator's own delivery ledger disagreed with itself (an over-counted
        # beat), while the words on the transcript stayed on schedule — so `clean` is untouched and
        # no verdict is discounted. Named so the turns get read.
        kinds = ", ".join(f'{k}×{n}' for k, n in (usi.get("self_report_kinds") or {}).items())
        where = "; ".join(f'{name} turns {",".join(str(t) for t in turns)}' for name, turns in noted.items())
        parts.append(
            f'user-sim ledger disagreement on {len(noted)} scenario(s) ({kinds or "unnamed"}; {where}) — the transcript stayed '
            'on schedule and no verdict is discounted; read those turns before acting on one'
        )
    suite = t.get("suite_score")
    # The passed fraction never travels alone (below); when there is no suite score to pair it with,
    # say THAT rather than pair it with a fabricated 0.
    parts.append(f'suite score {suite:.1f} of 100' if isinstance(suite, (int, float)) and not isinstance(suite, bool) else 'no scores in this run')
    if violations is not None:
        parts.append(f'{violations} policy violation{"" if violations == 1 else "s"}')
    # The deterministic verdicts belong in the headline for the same reason they get a column:
    # they sit outside pass_rate entirely, so a scenario can fail its one mechanical assertion
    # while every number on this line moves in the other direction.
    checks = [s.get("deterministic") for s in _scenarios if isinstance(s.get("deterministic"), dict)]
    if checks:
        broken = sum(1 for c in checks if c.get("status") == "error")
        line = f'{sum(1 for c in checks if c.get("status") == "failed")}/{len(checks)} deterministic checks failed'
        parts.append(line + (f' ({broken} broken)' if broken else ""))
    # What every pooled count above is over, FIRST: "24 of 27 goals" beside a 10-scenario suite reads as a misprint.
    if scope := pooled_scope(t):
        parts.insert(0, scope)
    L += [f'_{" · ".join(parts)}._', ""]
    return "\n".join(L)


#: The verdict axes a knowledge cell is counted on, in print order.
_KNOWLEDGE_AXES = ("passed", "failed", "na", "deferred", "inconclusive", "skipped")
#: How an axis reads in the Knowledge-coverage cell; an axis absent here reads as its own name.
_KNOWLEDGE_WORDS = {"na": "n/a", "deferred": "graded under its owner eval"}


def _knowledge_cells(spec: dict, sid: str, grader: str) -> Counter:
    """One scenario's verdicts for one grader: every trial's when the run has trials, else trial 1's name lists."""
    trials = spec.get("trials")
    if isinstance(trials, dict) and trials.get("schema") == TRIALS_SCHEMA:
        cell = _mapping(_mapping(_mapping(_mapping(trials.get("scenarios")).get(sid)).get("policies")).get(grader))
        return Counter({axis: cell[axis] for axis in _KNOWLEDGE_AXES if isinstance(cell.get(axis), int) and cell[axis]})
    row = next((r for r in spec.get("scenarios") or [] if isinstance(r, dict) and r.get("id") == sid), None)
    if row is None or row.get("result") not in ("pass", "fail"):
        return Counter()
    for key, axis in (("failing_evals", "failed"), ("not_applicable_evals", "na"), ("inconclusive_evals", "inconclusive"),
                      ("deferred_evals", "deferred"), ("skipped_evals", "skipped")):
        if any(policy_name(entry) == grader for entry in row.get(key) or []):
            return Counter({axis: 1})
    return Counter({"passed": 1})


def _knowledge_verdicts(counts: Counter) -> str:
    return " · ".join(f"{counts[a]} {_KNOWLEDGE_WORDS.get(a, a)}" for a in _KNOWLEDGE_AXES if counts[a]) or "no verdict"


def _planted_chunks(reach: dict) -> str:
    planted, corpus = reach.get("chunks_planted"), reach.get("corpus_chunks")
    if not isinstance(planted, (int, float)):
        return "—"
    return f"{planted:g} of {corpus:g}" if isinstance(corpus, (int, float)) else f"{planted:g}"


def render_knowledge_md(spec: dict, suite: dict) -> list[str]:
    """Which corpus documents the scenarios plant knowledge questions on, then the verdicts of the evals that grade
    those questions — per SCENARIO, the unit those evals decide.

    Joined from the suite's typed `knowledge` block (`knowledge_reach.by_document` scenarios + `graders`) to this
    run's cells. No document row carries a verdict: a scenario that plants several documents is graded once, and
    copying that verdict onto each of its documents would report per-document results nothing decided."""
    L = ["## Knowledge coverage", ""]
    block = suite.get("knowledge")
    if not isinstance(block, dict) or not block:
        missing = suite.get("knowledge_missing")
        if isinstance(missing, dict):  # the map says the agent answers from a knowledge base, and none was built
            why = ", because the document reader could not be installed" if missing.get("reader_missing") is True else ""
            return L + [f"_Your agent answers from a knowledge base, but none was built for this suite{why}, so no answer in this "
                        "run was checked against it._", ""]
        return L + ["_This suite has no knowledge base, so no answer in this run was checked against documents._", ""]
    by_doc = _mapping(_mapping(block.get("knowledge_reach")).get("by_document"))
    graders = [str(g) for g in block.get("graders") or []]
    trials = spec.get("trials")
    over = f' across {trials.get("n")} trials' if isinstance(trials, dict) and trials.get("schema") == TRIALS_SCHEMA else ""
    documents: dict[str, list[str]] = {}
    for doc, reach in sorted(by_doc.items()):
        for sid in _mapping(reach).get("scenarios") or []:
            documents.setdefault(str(sid), []).append(str(doc))
    planting = sorted(documents)
    ran = [str(s.get("id")) for s in spec.get("scenarios") or [] if isinstance(s, dict) and s.get("id")]
    short = short_labels(sorted(set(ran) | set(planting)))

    def label(sid: str) -> str:
        return f"`{_cell(sid)}`" if short[sid] == sid else short[sid]

    L += ["_Each document of the corpus, the chunks of it the scenarios' knowledge questions are drawn from, and the "
          "scenarios that ask them._", "", "| Document | Chunks planted | Planted in |", "|---|---|---|"]
    for doc, reach in sorted(by_doc.items()):
        sids = [str(x) for x in _mapping(reach).get("scenarios") or []]
        L.append(f"| `{_cell(str(doc))}` | {_planted_chunks(_mapping(reach))} | {', '.join(label(s) for s in sids) or '—'} |")
    if planting:
        L += ["", f"_The verdicts{over} of the evals that grade those questions. Each grades a scenario's answers as a whole, "
              "so a scenario that plants several documents has one verdict for all of them._", "",
              "| Scenario | Documents | " + " | ".join(f"`{_cell(g)}`" for g in graders) + " |", "|---|---|" + "---|" * len(graders)]
        for sid in planting:
            cells = [_knowledge_verdicts(_knowledge_cells(spec, sid, g)) for g in graders]
            docs = ", ".join(f"`{_cell(d)}`" for d in documents[sid])
            head = label(sid) if short[sid] == sid else f"{short[sid]} · `{_cell(sid)}`"
            L.append(f"| {head} | {docs} | " + " | ".join(cells) + " |")
    total = [f"`{g}` {_knowledge_verdicts(sum((_knowledge_cells(spec, sid, g) for sid in planting), Counter()))}" for g in graders]
    L += ["", f"_Over the {len(planting)} scenario(s) that plant any document: {'; '.join(total) or 'no grader named'}._", ""]
    return L


#: The files this render wrote, relative to its out dir — what `plurai ingest-run-report` uploads, and nothing else.
REPORT_INDEX = "report_files.json"


def write_report_index(out: Path, written: list[Path]) -> None:
    """Index exactly the files this render wrote. A file an earlier render left behind is not in it."""
    files = sorted(p.relative_to(out).as_posix() for p in written)
    (out / REPORT_INDEX).write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a local run-analytics report.")
    ap.add_argument("run", help="path to run.json")
    ap.add_argument("--out-dir", help="run dir (gitignored); report + charts go here (required unless --customer-regressions)")
    ap.add_argument("--suite", help="the suite's suite.json: adds Knowledge coverage (the scenarios planting each document, verdicts per scenario)")
    ap.add_argument("--customer-regressions", action="store_true",
                    help="print only the customer report's Pinned regressions lines (nothing when there are none) and write nothing")
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.run).read_text(encoding="utf-8"))
    if isinstance(aborted := spec.get("aborted"), dict) and aborted:
        # The runner stopped this run before scoring anything: there is no run to render, and a report built from
        # its empty roster would read as a suite that passed nothing.
        print(f"ABORTED {aborted.get('reason', 'aborted')}: no report was rendered. {aborted.get('remedy') or ''}".rstrip(), file=sys.stderr)
        return EXIT_MEASURED_STOP if aborted.get("reason") in MEASURED_STOP_REASONS else EXIT_RUN_ABORTED
    if args.customer_regressions:
        if lines := customer_regression_lines(spec):
            print("\n".join(lines))
        return 0
    if not args.out_dir:
        ap.error("--out-dir is required")
    out = Path(args.out_dir); charts = out / "charts"
    charts.mkdir(parents=True, exist_ok=True)

    floored = bool((spec.get("run_integrity") or {}).get("tool_count_floor"))
    written = [charts / "run_summary.svg", out / "analytics.md", out / "run.json"]
    (charts / "run_summary.svg").write_text(render_summary_svg(spec.get("totals", {}), floored), encoding="utf-8")
    # Same gate as the markdown link above, and for the same reason: the chart's own "no eval
    # reached a verdict" precondition is only reachable if the chart is drawn at all.
    if spec.get("evals"):
        (charts / "evals_passrate.svg").write_text(render_evals_svg(spec["evals"]), encoding="utf-8")
        written.append(charts / "evals_passrate.svg")
    report = render_md(spec)
    if args.suite:
        report += "\n" + "\n".join(render_knowledge_md(spec, json.loads(Path(args.suite).read_text(encoding="utf-8"))))
    # A final run rendered before its red-cell audit landed reads as provisional, under the release verdict's own marker
    # and customer sentence, so the verdict's later render replaces this line rather than stacking a second banner.
    rv = _release_verdict
    if rv is not None and spec.get("stage") == "final" and rv.audit_state(out) != rv.AUDIT_LANDED:
        report = f"{rv.MARKER}\n> **{rv.PROVISIONAL_TEXT[1]}**\n\n" + report
    (out / "analytics.md").write_text(report, encoding="utf-8")
    # Operator-only, and deliberately NOT in `written`: the report index is what gets uploaded and published.
    # The per-check rates and issue types come from build_run_json's operator file, never from run.json.
    try:
        side = json.loads((Path(args.run).parent / REGRESSIONS_DETAIL_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        side = {}
    side = side if isinstance(side, dict) and side.get("schema") == REGRESSIONS_DETAIL_SCHEMA else {}
    if operator := render_regressions_detail_md({**spec, "issue_types": side.get("issue_types") or []}):
        (out / REGRESSIONS_DETAIL).write_text(f'# Regressions — detail — {spec.get("run_name", "run")}\n\n' + "\n".join(operator), encoding="utf-8")
    else:
        (out / REGRESSIONS_DETAIL).unlink(missing_ok=True)
    if signal := render_eval_signal_md(spec):
        (out / "eval_signal.md").write_text(signal, encoding="utf-8")
        written.append(out / "eval_signal.md")
    else:  # a previous render's table must not stand beside this one
        (out / "eval_signal.md").unlink(missing_ok=True)
    (out / "run.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report_index(out, written)
    print(f"wrote {out / 'analytics.md'}")
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
