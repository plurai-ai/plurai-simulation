#!/usr/bin/env python3
"""The release verdict's two readings — the suite rung and the report banner — from ONE `release_gate`.

WHY ONE MODULE. The hand-off anchor (`lane_pin.licensed_anchor`) and the run's `analytics.md` banner
must agree on whether the suite is qualified, so both read the verdict the backend types on `release_gate`:

    named_risks: [{policy, display_name, kind: 'unproven' | 'coverage_limit' | 'suite_defect' | 'contested_violation'
                         | 'untested_premise' | 'replay_carried',
                   verdict: 'never_fails' | 'never_passes' | 'never_measured' | None, catastrophic, detail,
                   advisory?: 'catastrophic_risk_unprovable', observed_reds?: {counted, cells, not_counted},
                   contested?: [{scenario, trial, agreement}], untested?: [{complication, demanded, planted}]}]
                 — an advisory is named, never blocking (`ADVISORY_TEXT`); a kept `never_fails` risk with
                 `observed_reds.counted > 0` is said as the failure this run recorded (`OBSERVED_FAIL_TEXT`);
                 a `contested_violation` risk (a check that leaned towards a failure by the agent on a
                 conversation without settling) is said with where and by how much (`CONTESTED_TEXT`) and is
                 RUNG-NEUTRAL: it is about the agent, so the rung is what the run otherwise earns;
                 an `untested_premise` risk (a check whose situation this run could not set up on every
                 conversation it needs) is said per situation as "not tested here" (`UNTESTED_TEXT`), never
                 as a pass, and caps the rung at qualified like any other named risk
    remedy:      'rebuild_suite' | 'fix_instrument' | 'attribute_run' | 'accept_risk' | 'rerun'
                 | 'fix_env_rerun_scenarios' | 'replant_premise' | 'fix_agent' | None
    block_causes: [{check, cause}] — a blocked gate's defects, one sentence per cause (`CAUSE_TEXT`)
    deterministic_losses: [scenario] — optional; else inferred (:func:`deterministic_losses`)
    loss_fault_classes: {scenario: 'deterministic' | 'transient' | 'untyped'} — optional; each loss's OWN class, which
                 decides its repair: a `transient` loss is re-run (`TRANSIENT_LOSS_TEXT`), never sent to /edit-env;
                 the run's own `error.json` `transient` / `route_cause` re-class a loss they type (`run_loss_classes`),
                 and the SDK's `trials.repeated_losses` an untyped one every trial lost the same way, once its one
                 bounded re-run lost it again (`repeated`); a record of the backend refusing to LOAD the scenario
                 (`scenario_unloadable:`) classes it `unloadable`, said with that refusal's reason
                 (`SCENARIO_UNLOADABLE_TEXT`) — the generated scenario's, never /edit-env's
    rerun_scenarios: [scenario], replant: [{policy, complications, shortfall}] — optional (an older
                 backend omits them); read by `bounded_repair.py`, not by the banner

    block                  -> `Suite: not yet qualified`; banner = the one remedy for `remedy`
    pass + named risks     -> `Suite: qualified`; banner names them, a catastrophic one first
    pass, nothing named    -> `Suite: validated`; no banner — or, when only `contested_violation`
                              risks are named, the banner naming them (they never move the rung)

The RUNNER names risks about the bench itself, which the gate cannot see, in the run dir's
`run_summary.json` `named_risks` (:data:`RUN_RISK_KINDS`; read by :func:`run_risks`, carried on the gate
under :data:`RUN_RISKS`): `shared_world` — the user opted into live writes on a world nothing resets per
conversation — is said first (`SHARED_WORLD_TEXT`) and caps a clear gate at `Suite: qualified`, typed or not.

TWO SEPARATE LINES. The rung above rates the SUITE (`Suite: validated / qualified / not yet qualified`). The AGENT is
rated apart, by the SDK: `Agent: Ship / Don't ship / No verdict` (:func:`agent_line`) is said off the run's scores block
(`run_summary.json` → `scores` → `agent`, written by `plurai scores refresh`), COPIED with its reasons — every critical
failure that blocks Ship named, every one a review set aside listed, every conversation the block states `agent_error`
(the agent's own code raised) named, failed required checks and undecided goals as notes that do not block, the
unscored scenarios named for No verdict. A line read off a run other than the final scored run names that run and ends
on the final run giving the verdict. This module never weighs or scores anything.
`analytics.md` carries both lines at its head (the Suite line only when a rung is licensed), and `release_verdict.json`
carries `agent` (the block's verdict object, verbatim) and `agent_line`.

A gate WITHOUT those fields (an older backend) reads its `blocking` bit alone — block -> `Suite: not
yet qualified` with the rebuild banner, so the rung and the banner agree — and :func:`typed` reports it
NOT typed rather than guessing named risks into it. A report with no `release_gate` key at all predates
the release gate and is read off the ONE older gate it carries (:func:`report_gate`, the single reader
both the rung and the banner use): a suite_diagnostics report's final coverage `ship_gate`. ANY final gate that blocks reads `Suite: not yet qualified`;
a clear older gate licenses at most `Suite: qualified` — `validated` needs the release gate, which
joins the coverage gate, the catastrophic rung and the run's losses — and a final block is the rebuild banner, anything else none.

    scripts/release_verdict.py render --report <saved suite_diagnostics envelope> [--out <run dir>]

prints the CUSTOMER banner (nothing when the verdict licenses none) and, with `--out`, writes it at the head
of `analytics.md` in the run dir and in every trial dir `run_summary.json` lists — replacing any
banner an earlier render left, so rendering twice changes nothing. The customer reads the verdict and what it
says about their agent, never the suite's own standing (:data:`RISK_AUDIENCE`): `release_verdict.json` keeps the
full reading as `banner` (internal) and the customer's as `customer_banner`. Beside every `analytics.md` it
touches it writes `release_verdict.json` (:func:`verdict_object`, atomically, keys sorted): the typed
verdict the banner line is taken from, for readers that must not parse prose.

A `suite_defect` named risk ("not counted against your agent") is a discount, and the backend decides it once, against
the C5 audit the run uploaded: `release_gate.suite_defects` (`SuiteDefectDecision`). The rung, the internal banner
and the suite anchor read exactly its `discounted` rows (:func:`with_decisions`, applied by :func:`report_gate`): a
`suite_defect` risk no row discounts is withdrawn, and a gate that carries no decisions (an older backend) discounts
nothing — never a recount from the local audit, never `kind` alone. The decisions ride `release_verdict.json`
`suite_defects` (internal: `unproven` rows included). The customer's banner never says a check is not counted
(:data:`RISK_AUDIENCE`), so it cannot contradict the results table.

A FINAL run's verdict lands only on an audit the backend holds: it is `provisional` while the gate says so
(`release_gate.provisional`: the report was computed with no readable audit of the run) or the run's own audit state
(`red_cell_audit.state` — the audit saved AND its upload receipt of the file as it stands) is short of `uploaded`. A
provisional verdict carries `audit` naming the state, no `rung`, and both banners lead with :data:`PROVISIONAL_TEXT`;
the flow continues to the audit, its upload and a fresh `suite_diagnostics` (`/simulation-run` Step 4d), then renders again.

Exit codes: 0 ok · 10 the report could not be read (writes nothing) · 11 the job is still queued/running ·
12 the job failed. On 11 and 12 no verdict exists for this run yet, so with `--out` the render REMOVES any
older `release_verdict.json` and banner from the run dir and its trial dirs: a stale verdict never sits
beside the run. None is a `mcp_call.py` exit, so a chained `job_status` save cannot be read as one.

    scripts/release_verdict.py stale --out <run dir>

STALE (C8). `release_verdict.json` carries `audit_sha`: the audit its gate read (`release_gate.audit_sha`, the backend's
sha of the audit as stored; an older backend names none, and the local file's sha at render time stands in). It is
stale when the upload receipt's `audit_sha` is another, or the local audit changed since its upload (:func:`stale`):
`stale` exits 13 naming which, and the verdict is recomputed — `release_verdict_recompute(suite_diagnostics_id)` over
the uploaded audit (the receipt's `stale_reports` name the ids), then `render` — before anything renders it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stop_message import CONTACT_TEXT  # noqa: E402 - sibling script: the contact hand-off has one home


VALIDATED = 'Suite: validated'
QUALIFIED = 'Suite: qualified'
NOT_QUALIFIED = 'Suite: not yet qualified'

#: The operator's ONE remedy per typed `remedy`. A block carrying no remedy (or one this
#: version does not know) is a backend contract violation, so it takes the contact hand-off.
#:
#: `fix_instrument` (GATEPURITY) is a DIFFERENT repair from `rebuild_suite` on purpose, and its
#: sentence must never point at `/simulation-generate-suite`: the backend emits this remedy only
#: when every remaining coverage violation is a run-state-guarded row whose judge's own K-sample
#: self-consistency could not converge — regenerating scenarios cannot touch a judge's sample count
#: or agreement threshold, so routing this one to a rebuild would send the operator to spend a full
#: regeneration on a repair that provably cannot close it.
REMEDIES: dict[str | None, str] = {
    'rebuild_suite': 'This suite has to be rebuilt before it can score your agent — run `/simulation-generate-suite`, then `/simulation-run` again.',
    'fix_instrument': (
        'One or more safety checks could not agree with themselves across repeated attempts on your agent’s replies — '
        'the checks themselves need tuning, not your suite. Ask me to edit the evals to raise the check’s sample count or '
        'agreement threshold, or to optimize the eval for a steadier version, then run `/simulation-run` again.'
    ),
    # A check that never applied, on a run that does not say whose that is: read before repairing.
    'attribute_run': (
        'One or more safety checks never applied to a conversation in this run, and the run does not say why. Read '
        'one conversation that should have triggered it before changing anything, then `/simulation-run` again.'
    ),
    # Scenarios that never completed a conversation hold the suite: nothing in them was scored.
    'rerun': ('Some scenarios never completed a conversation in this run, so they were not scored. Check the error recorded '
              'for each in the run directory, then `/simulation-run` again.'),
    # Named with the scenarios by `FIX_ENV_TEXT` (:func:`deterministic_losses`); this row is the fallback when none is listed.
    'fix_env_rerun_scenarios': ('A scenario lost a conversation to a fault in the simulated tools that running again reproduces. Fix '
                                'the simulated tools first — run `/edit-env` with the error recorded in the run directory — then '
                                '`/simulation-run` again.'),
    # Named with the checks by `CAUSE_TEXT['under_planted']`; this row is the fallback when no cause is typed. At the
    # final stage a current backend names an under-planted premise as an `untested_premise` risk instead (`UNTESTED_TEXT`).
    'replant_premise': ('One or more safety checks were set up on fewer conversations than the suite asked for. Run '
                        '`/simulation-run` again: it adds the missing conversations to the suite, then scores your agent.'),
    # No scenario completed and every failure is the agent's own raise: the suite is not what failed.
    'fix_agent': ('Your agent stopped on an error of its own in every conversation, so this run could not score it. Fix the '
                  'error recorded in the run directory, then run `/simulation-run` again.'),
    None: CONTACT_TEXT,
}

#: The operator's remedy per typed block CAUSE (`release_gate.block_causes`, one `{check, cause}` per
#: defect): WHICH repair a blocked check needs depends on what is wrong with it, so a block the backend
#: types by cause is said by cause, one sentence per cause naming its checks. `{}` is the checks' names.
#: A cause this version does not know falls back to the one `remedy` sentence (`REMEDIES`), never to
#: a guess. `rebuild_suite` keeps its one sentence: a suite rebuild is the whole repair there.
CAUSE_TEXT: dict[str, str] = {
    'guard_never_armed': ('Safety check {} never applied to a single conversation in this run: the situation it tests never came up the '
                          'way the check expects. Ask me to edit the evals to re-scope the check so it applies when that situation arises, then '
                          'run `/simulation-run` again.'),
    'guard_unarmed_unattributed': ('Safety check {} never applied to a conversation in this run, and nothing in the run says '
                                   'why. Read one conversation that should have triggered it: if your agent never did what '
                                   'the check looks for, that is your agent to fix; if a tool never returned it, the '
                                   'simulated tools; if the check looks for something the run never produces, ask me '
                                   'to edit the evals to re-scope it. Then `/simulation-run` again.'),
    # An OLDER backend's block cause: a current one names a split vote as a `contested_violation` risk, never a block.
    'contested_violation': ('Safety check {} leaned towards a failure by your agent on a conversation but could not settle it. Ask '
                            'me to edit the evals to sharpen the check so it reaches a firm verdict, then run `/simulation-run` again.'),
    'hedge_lost_scenario': ('Safety check {} could not agree with itself on a conversation no other attempt settled. Ask me to edit the evals '
                            'to raise the check’s sample count or sharpen it, or to optimize the eval for a steadier version, then '
                            'run `/simulation-run` again.'),
    'judge_undecided': ('Safety check {} reached no verdict on conversations it applied to. Ask me to edit the evals to fix the check so it '
                        'decides, then run `/simulation-run` again.'),
    'infra_unmeasured': ('Safety check {} could not be measured, because the conversations it applies to did not run to completion. '
                         'Run `/simulation-run` again once the run completes normally.'),
    # Said as the check's own result (none yet) and the customer's repair, never as the suite's coverage (a customer line).
    'gated_off': ('Safety check {} applied to only a few conversations in this run, so it reached no result. Ask me to edit the evals to '
                  'widen when the check applies, then run `/simulation-run` again.'),
    'under_measured': ('Safety check {} decided only a few of the conversations it applied to, so it reached no result. Ask me to edit '
                       'the evals to fix what kept it from deciding, then run `/simulation-run` again.'),
    # An OLDER backend's block cause, and mid-repair only on a current one: at the final stage a current backend names
    # every under-planted premise as an `untested_premise` risk (`UNTESTED_TEXT`), never a block.
    'under_planted': ('Safety check {} was set up on fewer conversations than the suite asked for. Run `/simulation-run` again: '
                      'it adds the missing conversations to the suite, then scores your agent.'),
    'catastrophic_risk_unaccepted': ('Critical safety check {} has never been shown able to catch a failure by this agent. Ask '
                                     'me to edit the evals to fix the check so it can be shown to fail, then run `/simulation-run` again.'),
    'run_incomplete': ('Scenario {} never completed a conversation in this run, so nothing in it was scored. Check the error '
                       'recorded for it in the run directory, then `/simulation-run` again.'),
    # A row short only because its judge did not answer in time (a transient backend fault): a re-run, never a rebuild.
    'judge_unanswered': ('Safety check {} could not be scored on some conversations because the scoring service did not answer '
                         'in time. Run `/simulation-run` again.'),
}

#: The banner for scenarios a NON-transient fault in the simulated tools voided (`remedy` is
#: `fix_env_rerun_scenarios` — or `replant_premise` beside it, or None on an older backend: a re-run
#: reproduces the fault, so neither `rerun` nor the contact hand-off is the next step). `{}` are
#: the scenarios; `{error}` is the recorded error, or nothing when the report carries none.
FIX_ENV_TEXT = ('Scenario {} lost a conversation to a fault in the simulated tools that running again reproduces{error}. Fix the '
                'simulated tools first — run `/edit-env` with that error — then `/simulation-run` again.')
#: The banner for scenarios a fault the runtime typed TRANSIENT lost (`loss_fault_classes`): load or latency, which a
#: re-run answers and /edit-env cannot fix. `{}` are the scenarios; `{error}` is the recorded error, or nothing.
TRANSIENT_LOSS_TEXT = ('Scenario {} lost a conversation because the simulated tools did not answer in time{error}. Run '
                       '`/simulation-run` again.')
#: The banner for scenarios every trial lost to one untyped fault, again after the run's one bounded re-run (`repeated`):
#: running again reproduces it, and nothing says whose it is, so it names no owner and is never sent to /edit-env.
#: `{}` are the scenarios; `{error}` as above. The customer's line gives no instruction: the fix is on our side.
REPEATED_LOSS_TEXT = ('Scenario {} failed the same way in every trial, and again when re-run{error}, so running again reproduces it. '
                      'Fix what the recorded error names, then `/simulation-run` again.')
REPEATED_LOSS_CUSTOMER_TEXT = 'Scenario {} failed the same way in every trial, and again when re-run{error}, so running again reproduces it.'
#: The banner for scenarios the backend refused to LOAD (`UNLOADABLE`): the generated scenario is at fault, so neither
#: /edit-env nor a re-run reaches it. `{}` are the scenarios; `{reason}` the refusal's own reason, never the HTTP error.
SCENARIO_UNLOADABLE_TEXT = ('Scenario {} could not be loaded, so its conversations did not run{reason}. The scenario as generated is '
                            'at fault, not your agent or the simulated tools: rebuild the suite with `/simulation-generate-suite`, '
                            'then `/simulation-run` again.')
#: The report finding that names a voided scenario's recorded error.
RUN_FAILED_FINDING = 'scenario_run_failed'
#: How much of a recorded error the one-line banner quotes.
ERROR_EXCERPT_CHARS = 200
#: A scenario's run-folder id (`<name>_scenarios_<set>_<name>`): the customer's line names it by its `<name>`.
_FOLDER_ID = re.compile(r'(?P<name>[A-Za-z][\w-]*?)_scenarios_[0-9a-f]{8}_(?P=name)')
#: A recorded error's clause that names one of our settings (an upper-case env-var name) or one of our tools' own
#: verbs: the customer's line quotes the error up to it, never from it on.
_INTERNAL_CLAUSE = re.compile(r'\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b|\bdeploy_env\b')

#: How each named risk is said: `(the lead sentence for a catastrophic one, the group sentence)`, keyed
#: on an unproven risk's `verdict` (which half is not evidence), else on its `kind`. `unproven` is an
#: unproven risk with no typed verdict (an older backend): neither half is claimed. `None` says a kind
#: this version does not know — still by name, so a new kind is never silent.
RISK_TEXT: dict[str | None, tuple[str, str]] = {
    'never_fails': ('Unproven safety check: {} has never been shown able to fail on this agent; its passes are not evidence.',
                    'Not yet shown able to fail on this agent, so their passes are not evidence: {}.'),
    'never_passes': ('Unproven safety check: {} has never been shown able to pass on this agent; its failures are not evidence.',
                     'Not yet shown able to pass on this agent, so their failures are not evidence: {}.'),
    'never_measured': ('Unproven safety check: {} has never been measured on this agent; neither its passes nor its failures are evidence.',
                       'Never yet measured on this agent, so neither their passes nor their failures are evidence: {}.'),
    'unproven': ('Unproven safety check: {} is not yet proven on this agent; neither its passes nor its failures are evidence.',
                 'Not yet proven on this agent, so neither their passes nor their failures are evidence: {}.'),
    'coverage_limit': ('Safety check {} was reached by too few conversations to count as a result.',
                       'Reached by too few conversations to count as results: {}.'),
    'suite_defect': ('Not counted against your agent: {} rests on a defect in the simulation itself.',
                     'Not counted against your agent, because each rests on a defect in the simulation itself: {}.'),
    # The fallback for an `untested_premise` risk whose typed `untested` list says nothing readable (`UNTESTED_TEXT`).
    'untested_premise': ('Not tested here: critical safety check {}: this run could not set up the situation it needs; there is no '
                         'result either way.',
                         'Not tested here, because this run could not set up the situation each needs; there is no result either way: {}.'),
    # True of both shapes the backend names (`replay_carried`): a check that cleared its coverage bar only with replays,
    # and one offered nothing but replays that measured no conversation. Internal: never in `customer_banner`.
    'replay_carried': ('Safety check {} was measured on too few full conversations to count as a result; the rest of what '
                       'reached it were replays of one recorded turn.',
                       'Measured on too few full conversations to count as results, the rest being replays of one recorded turn: {}.'),
    None: ('Flagged safety check: {}.', 'Also flagged on this agent: {}.'),
}
#: How a catastrophic named risk's typed `advisory` is said, in place of its `RISK_TEXT` lead: a check no
#: test could stage a violation for never holds the suite, and the sentence claims nothing about it beyond
#: what is true of every such check (it may still be vacuous). An advisory this version does not know falls
#: back to the `RISK_TEXT` lead, never to silence.
ADVISORY_TEXT: dict[str, str] = {
    'catastrophic_risk_unprovable': ('Critical safety check {} could not be proven by a test on this agent; it is still graded on every '
                                     'run and fails if a violation is observed.'),
}
#: How a kept `never_fails` risk is said once the run recorded a counted failure on it (typed
#: `observed_reds.counted > 0`, never read off `detail`): `(the catastrophic lead, the group sentence)`, in
#: place of its `ADVISORY_TEXT` / `RISK_TEXT` sentence — which would say no failure was seen. `{}` is the
#: check's name (lead) or the names with their tallies (group); `{tally}` is "k of N".
OBSERVED_FAIL_TEXT: tuple[str, str] = (
    'Critical safety check {} failed in this run ({tally} scored conversations); that failure stands in the results.',
    'Failed in this run, and each failure stands in the results: {}.',
)
#: How the `contested_violation` risks are said on a pass: `(the lead for one catastrophic check, the group sentence)`.
#: `{}` is the check's name with where it leaned (lead) or the names, each with where (group): the scenario, the
#: trial, read off the typed `contested` list, never `detail`.
CONTESTED_TEXT: tuple[str, str] = (
    'Critical safety check {} leaned towards a failure by your agent but did not settle — a likely defect; read that conversation.',
    'Leaned towards a failure by your agent but did not settle — likely defects; read each conversation: {}.',
)
#: How each situation of an `untested_premise` risk is said, one sentence per `untested` entry, read off the typed
#: list (never `detail`): `(none of it set up, set up on some)`. `{check}` is the check's name (`critical safety check
#: <name>` for a catastrophic one, which leads); `{situation}` the complication, words for underscores; `{k}`/`{n}` the
#: conversations it was set up on / needed. Never a pass, and NOT rung-neutral: an untested situation caps the rung.
UNTESTED_TEXT: tuple[str, str] = (
    'Not tested here: {check}: this run could not set up {situation}; there is no result either way.',
    'Not tested here: {check}: this run could not set up {situation} on every conversation it needs (tested on {k} of {n}); '
    'there is no result either way on the rest.',
)
#: How the checks this run scored on NO conversation are said (internal), off `run.json` `evals`: `{}` is their names.
UNSCORED_TEXT = 'Not tested here, because no conversation in this run reached a result on them; there is no result either way: {}.'
#: Who reads a pass banner's sentences. The customer reads what is about THEIR agent — a failure this run recorded
#: (`OBSERVED_FAIL_TEXT`), a check that leaned towards a failure (`CONTESTED_TEXT`) — and about their own setup
#: (`SHARED_WORLD_TEXT`). A sentence about the suite's own standing — an unproven check, a coverage hole, a suite-defect
#: discount, a situation the run could not set up, an advisory — is internal: it stays in `banner`, never in
#: `customer_banner`. A blocked gate's remedy is an action the customer takes, so it is the customer's either way.
CUSTOMER, INTERNAL = 'customer', 'internal'
#: The named-risk kinds that never move the rung: what they name is about the agent, not the suite.
RUNG_NEUTRAL_KINDS = ('contested_violation',)
#: The named-risk kinds the RUNNER carries in `run_summary.json` `named_risks`: risks about the bench the run was on,
#: which the gate cannot see. None is rung-neutral.
RUN_RISK_KINDS = ('shared_world',)
#: The key a gate carries the run's own named risks under (:func:`report_gate`'s `run_risks`).
RUN_RISKS = 'run_risks'


def gate_risks(gate: dict) -> list:
    """Every risk `gate` names: the backend's `named_risks`, then the runner's (:data:`RUN_RISKS`) — the ONE list the
    anchor's `names N risk(s)` counts and `release_verdict.json` `named_risks` lists. `[]` where the gate carries neither."""
    named, own = gate.get('named_risks'), gate.get(RUN_RISKS)
    return [*(named if isinstance(named, list) else []), *(own if isinstance(own, list) else [])]
#: The bench's own mock-gate gap (`verification.json` `mock_tools`, C10): mocked tools the gate neither witnessed nor saw
#: waived. A runner-side risk like `shared_world` (it caps the rung at `qualified`), but about the suite's own standing:
#: said in the internal banner only, never the customer's, except its `read_back` part (:data:`READ_BACK_TEXT`). The gate
#: warns; this is where its warning lands.
MOCK_GAP_KIND = 'mock_unwitnessed'
MOCK_GAP_TEXT = 'Mock coverage gap: {n} mocked tool(s) the mock gate did not witness ({tools}).'
#: The one part of the mock gap the CUSTOMER reads (:func:`read_back_sentence`): a real reader the real-reader probe did
#: not witness seeing a mocked write. A fact about their setup that changes how results read, like `SHARED_WORLD_TEXT`.
READ_BACK_TEXT = ('Read-back not confirmed in this simulation: {}. A conversation where your agent reads back its own earlier '
                  'write may be judged on what it saw before that write.')
#: The C10 statuses that are no gap.
MOCK_COVERED = ('witnessed', 'waived')
#: The local-served world's unproven parity (`<artifacts>/local_world_parity.json`, written by `bench_manifest.py
#: local-parity` when the contract tier exits non-zero against the world served locally after `deploy_env` failed twice).
#: Read only while `.qual_transport` says `local-served`; like :data:`MOCK_GAP_KIND`, internal banner only, rung `qualified`.
PARITY_RISK_KIND = 'local_world_parity_unproven'
PARITY_FILENAME = 'local_world_parity.json'
PARITY_TEXT = 'Local world parity unproven: the contract tier exited {exit_code} against the locally served mock ({errors}).'
#: How many failing lines a parity record keeps, and how long each may be.
PARITY_MAX_ERRORS, PARITY_MAX_CHARS = 5, 200
#: How a `shared_world` risk is said: `{n}` is how many live writing tools the runner counted (its typed `live_writers`).
SHARED_WORLD_TEXT = ('Conversations share one world: {n} live writing tools with no reset between conversations; verdicts may '
                     'depend on run order.')
#: The typed verdicts an unproven risk carries, each with its own `RISK_TEXT` row.
UNPROVEN_VERDICTS = ('never_fails', 'never_passes', 'never_measured')
#: The banner's last sentence: about the OTHER checks, so it never restates a named one's results.
OTHERS_STAND = "Every other check's results stand."
#: The lead of a FINAL run's banners while its red-cell audit has not landed on the backend: `(internal, customer)`.
PROVISIONAL_TEXT = ('Provisional: this final run\'s red-cell audit is {state}, so this verdict has not landed.',
                    'Provisional result: this run\'s failures are still being reviewed, so these results may change.')

#: Exit codes, disjoint from `mcp_call.py`'s 0-8: Step 4c chains the two, and one number must mean one thing.
EXIT_UNREADABLE, EXIT_NOT_DONE, EXIT_JOB_FAILED, EXIT_STALE = 10, 11, 12, 13
#: The runner stopped the run before it scored anything (`run_summary.json` `aborted`): no release decision exists.
EXIT_RUN_ABORTED = 14

#: The first line of a rendered banner, so a later render finds and replaces it.
MARKER = '<!-- plurai:release-verdict -->'
_BANNER_BLOCK = re.compile(r'\A' + re.escape(MARKER) + r'\n(?:>[^\n]*\n)*\n')
#: The one unmarked banner an earlier plugin wrote by hand; a render replaces it like its own.
LEGACY_BANNER = f"> **{REMEDIES['rebuild_suite']}**\n\n"

#: A trial dir `run_summary.json` may list: `''` is the run dir itself (trial 1).
_TRIAL_DIR = re.compile(r'[A-Za-z0-9_-]*')


#: The key a gate :func:`report_gate` read off a report's OLDER gate carries: which older gate it was.
OLDER_GATE = 'older_gate'
#: The backend's reasons a clear final gate licenses at most `qualified` (`release_gate.rung_holds`: `[{cause, detail,
#: policies}]`, cause `audit_conflict` | `measurement_unclean` | `invalid_trials`). Never blocking, never on the banner.
RUNG_HOLDS = 'rung_holds'
#: How each older gate is named to the operator.
OLDER_GATE_NAMES = {'ship_gate': 'coverage gate (`ship_gate`)'}
#: The key a named risk :func:`report_gate` read carries: how many of its counted reds a final-run correction cleared.
CORRECTED_REDS = 'corrected_reds'


def report_gate(report: dict, corrected: dict[str, int] | None = None, run_risks: list[dict] | None = None) -> dict | None:
    """The release verdict a saved report carries — THE reader, for the suite rung and the banner alike.

    `release_gate` when the report has that key (`null` reached no decision and stays None). A report
    WITHOUT the key predates it, and is read off the one older gate it carries, marked
    :data:`OLDER_GATE` so :func:`rung` caps it: a suite_diagnostics report's final coverage `ship_gate`. Anything else is None.
    `corrected` (:func:`corrected_reds`) marks each named risk with the reds a correction cleared (:data:`CORRECTED_REDS`).
    `run_risks` (:func:`run_risks`) are the runner's own named risks, carried under :data:`RUN_RISKS`, one per kind
    and none of a kind the gate already names.
    Every `suite_defect` risk the gate's own decisions do not discount is withdrawn (:func:`with_decisions`).
    """
    gate = None
    if 'release_gate' in report:
        gate = report.get('release_gate')
        if isinstance(gate, dict) and corrected and isinstance(gate.get('named_risks'), list):
            gate = {**gate, 'named_risks': [{**r, CORRECTED_REDS: corrected[r['policy']]} if isinstance(r, dict) and r.get('policy') in corrected
                                            else r for r in gate['named_risks']]}
        if isinstance(gate, dict):
            gate = with_decisions(gate, decisions_of(gate))
        gate = gate if isinstance(gate, dict) else None
    else:
        coverage = report.get('ship_gate')
        if isinstance(coverage, dict) and coverage.get('stage') == 'final' and isinstance(coverage.get('blocking'), bool):
            blocking = coverage['blocking']
            gate = {'stage': 'final', 'blocking': blocking, 'status': 'block' if blocking else coverage.get('status'), OLDER_GATE: 'ship_gate'}
    if gate is None or not run_risks:
        return gate
    # De-duplicated by kind: a kind the gate already names (or an earlier runner entry) is said once.
    seen = {r.get('kind') for r in gate.get('named_risks') or [] if isinstance(r, dict)}
    own = [r for r in run_risks if r.get('kind') not in seen and not seen.add(r.get('kind'))]
    return {**gate, RUN_RISKS: own} if own else gate


#: The key a gate carries its discount decisions under (`release_gate.suite_defects`; `release_verdict.json` `suite_defects`).
SUITE_DEFECTS = 'suite_defects'
#: Why a `suite_defect` risk on a gate with no decisions is withdrawn: an older backend decided nothing, so nothing is discounted.
UNDECIDED = 'undecided'


def _is_discount(risk: object) -> bool:
    return isinstance(risk, dict) and risk.get('kind') == 'suite_defect'


def _subject(risk: dict) -> str | None:
    subject = risk.get('subject')
    return subject if isinstance(subject, str) and subject else None


def backend_decisions(gate: object) -> list[dict] | None:
    """The gate's decisions (`suite_defects`: `SuiteDefectDecision` rows, or this module's own) as
    `{policy, subject, discounted, because, unproven, true_red, false_red}` — a backend row's `ruling` is `because`, its
    `audit` the tallies — or None when the gate carries none."""
    rows = gate.get(SUITE_DEFECTS) if isinstance(gate, dict) else None
    if not isinstance(rows, list):
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('policy'), str) or not isinstance(row.get('discounted'), bool):
            continue
        audit = row.get('audit')
        tally: dict = row if 'because' in row else audit if isinstance(audit, dict) else {}
        out.append({'policy': row['policy'], 'subject': row.get('subject') if isinstance(row.get('subject'), str) else None,
                    'discounted': row['discounted'], 'because': str(row.get('because', row.get('ruling')) or ''),
                    'unproven': row.get('unproven') is True,
                    'true_red': _int_or_none(tally.get('true_red')) or 0, 'false_red': _int_or_none(tally.get('false_red')) or 0})
    return out


def decisions_of(gate: dict) -> list[dict]:
    """:func:`backend_decisions`, else one :data:`UNDECIDED` (not discounted) row per `suite_defect` risk the gate names."""
    served = backend_decisions(gate)
    if served is not None:
        return served
    named = gate.get('named_risks')
    risks: list = named if isinstance(named, list) else []
    return [{'policy': str(r.get('policy')), 'subject': _subject(r), 'discounted': False, 'because': UNDECIDED, 'unproven': False,
             'true_red': 0, 'false_red': 0} for r in risks if _is_discount(r)]


def with_decisions(gate: dict, decisions: list[dict]) -> dict:
    """`gate` carrying `decisions` (:data:`SUITE_DEFECTS`), with every `suite_defect` risk no `discounted` row names withdrawn;
    `gate` itself when there was nothing to decide."""
    if not decisions:
        return gate
    if not isinstance(gate.get('named_risks'), list):
        return {**gate, SUITE_DEFECTS: decisions}
    kept = {(d['policy'], d['subject']) for d in decisions if d['discounted']}
    return {**gate, SUITE_DEFECTS: decisions,
            'named_risks': [r for r in gate['named_risks'] if not _is_discount(r) or (str(r.get('policy')), _subject(r)) in kept]}


def audit_state(out: Path) -> str:
    """The run's red-cell audit state (`red_cell_audit.state`); an unreadable audit is said and read as `not_uploaded`."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling script: the one audit file and its upload receipt

    try:
        return red_cell_audit.state(out)
    except ValueError as exc:
        print(f'release verdict: {exc} — read as not uploaded', file=sys.stderr)
        return red_cell_audit.NOT_UPLOADED


def audit_sha(out: Path) -> str | None:
    """The sha256 of the run's red-cell audit file as it stands (`red_cell_audit.sha`), None when it has none."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling script: the one audit file

    return red_cell_audit.sha(out)


def stale(out: Path) -> str | None:
    """Why the run's `release_verdict.json` no longer stands on the audit beside it (C8), else None. A run never
    rendered has no verdict to be stale.

    With a backend that names the stored audit (the upload receipt's `audit_sha`), two links: the local file is the one
    uploaded (its sha is the receipt's `audit_sha256`), and the verdict's `audit_sha` — the gate's `release_gate.audit_sha`
    — is the receipt's. Otherwise the verdict's `audit_sha` is the local file's sha at render time, compared with the file
    now; a verdict provisional on an audit that has landed since is stale too."""
    try:
        verdict = json.loads((out / VERDICT_FILE).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    current = audit_sha(out)
    recorded = verdict.get('audit_sha') if isinstance(verdict, dict) else None
    receipt = _receipt(out)
    if isinstance(receipt.get('audit_sha'), str):
        if receipt.get('audit_sha256') != current:
            return 'the audit changed since its upload: upload it, then recompute the verdict'
        if recorded != receipt['audit_sha']:
            return f'{VERDICT_FILE} was computed over another audit than the one the backend holds'
        return None
    if recorded == current:
        landed = isinstance(verdict, dict) and verdict.get('provisional') is True and audit_state(out) == AUDIT_LANDED
        return f'{VERDICT_FILE} is provisional, computed before the audit landed' if landed else None
    if current is None:
        return f'{VERDICT_FILE} names an audit the run no longer has'
    return f'{VERDICT_FILE} was computed {"before the audit" if recorded is None else "over an earlier version of the audit"}'


def _receipt(out: Path) -> dict:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling script: the one upload receipt

    return red_cell_audit.receipt_of(out)


def audit_said(out: Path, state: str | None) -> str | None:
    """`state` as every audit reader says it: a missing audit names its path (`audit missing: <run dir>/red_cell_audit.json`)."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling script: the one audit file's name

    return f'{state}: {red_cell_audit.path(out)}' if state == red_cell_audit.MISSING else state


def run_risks(out: Path) -> list[dict]:
    """The runner's own named risks for the run dir `out`: its `run_summary.json` `named_risks` entries of a kind in
    :data:`RUN_RISK_KINDS`, plus the bench's mock-gate gap (:func:`mock_gap`, read off `<artifacts>/verification.json`
    beside `runs/<run>`), its local-world parity (:func:`parity_risk`) and a one-sample final run (:func:`final_sampling`).
    `[]` when none names one (an older runner writes no key)."""
    art = Path(out).parent.parent
    try:
        risks = json.loads((out / 'run_summary.json').read_text(encoding='utf-8')).get('named_risks')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        risks = None
    own = [r for r in risks if isinstance(r, dict) and r.get('kind') in RUN_RISK_KINDS] if isinstance(risks, list) else []
    return [*own, *[risk for risk in (mock_gap(art), parity_risk(art), final_sampling(out)) if risk]]


def final_sampling(out: Path) -> dict | None:
    """The :data:`FINAL_SAMPLING_KIND` risk for the run dir `out`: its `run.json` says `stage: final` and its
    `run_summary.json` `trials` block says one sample, read by the runner's own rule (`n` of a `trials-v1` block, else 1).
    True by design on a rig's one-trial budget, the Arize experiment sink (it cuts `--trials` to 1) and `PLURAI_REPLAY`
    (one recorded sample): one sample is what ran there. None when either record is unreadable."""
    try:
        stage = json.loads((out / 'run.json').read_text(encoding='utf-8')).get('stage')
        block = json.loads((out / 'run_summary.json').read_text(encoding='utf-8')).get('trials')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    n = block.get('n') if isinstance(block, dict) and block.get('schema') == TRIALS_SCHEMA else 1
    if stage != 'final' or not _is_count(n) or n > 1:
        return None
    return {'kind': FINAL_SAMPLING_KIND, 'display_name': 'final run sampled once', 'trials': n}


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def mock_gap(art: Path) -> dict | None:
    """The :data:`MOCK_GAP_KIND` risk for the bench whose artifacts dir is `art`: `{kind, display_name, tools: {tool:
    status}}` over every mocked tool `verification.json` `mock_tools` records outside :data:`MOCK_COVERED`, else None."""
    try:
        tools = json.loads((Path(art) / 'verification.json').read_text(encoding='utf-8')).get('mock_tools')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    held = {str(t): e for t, e in sorted(tools.items()) if isinstance(e, dict) and e.get('status') not in MOCK_COVERED} if isinstance(tools, dict) else {}
    if not held:
        return None
    risk: dict = {'kind': MOCK_GAP_KIND, 'display_name': 'mock coverage gap', 'tools': {t: str(e.get('status')) for t, e in held.items()}}
    read_back = {t: sorted(str(r) for r, s in e['read_back'].items() if s != 'witnessed')
                 for t, e in held.items() if isinstance(e.get('read_back'), dict)}
    if read_back := {t: readers for t, readers in read_back.items() if readers}:
        risk['read_back'] = read_back
    return risk


def mock_gap_sentence(risk: dict) -> str:
    """A :data:`MOCK_GAP_KIND` risk said with its typed `tools` (internal banner only)."""
    tools = risk.get('tools') if isinstance(risk.get('tools'), dict) else {}
    return MOCK_GAP_TEXT.format(n=len(tools), tools=', '.join(f'{t}: {s}' for t, s in tools.items()) or 'unnamed')


def read_back_sentence(risk: dict) -> str | None:
    """A :data:`MOCK_GAP_KIND` risk's `read_back` (writer -> real readers the probe did not witness seeing its writes),
    said to BOTH audiences: whether the agent's own read sees what it wrote is the probe's verdict, and a failed or absent
    one is the customer's to know (RT55 S2: the scan had told them "it sees those writes"). None when there is none."""
    read_back = risk.get('read_back') if isinstance(risk.get('read_back'), dict) else {}
    pairs = [f'{", ".join(readers)} may not show what {writer} wrote' for writer, readers in read_back.items() if isinstance(readers, list) and readers]
    return READ_BACK_TEXT.format('; '.join(pairs)) if pairs else None


def parity_errors(log: str) -> list[str]:
    """The failing tests' first error lines from a pytest log: its `FAILED` / `ERROR` summary lines, else its `E ` lines,
    else its last line — at most :data:`PARITY_MAX_ERRORS`, each cut to :data:`PARITY_MAX_CHARS`."""
    lines = [line.strip() for line in log.splitlines() if line.strip()]
    picked = [x for x in lines if x.startswith(('FAILED ', 'ERROR '))] or [x for x in lines if x.startswith('E ')] or lines[-1:]
    return [x[:PARITY_MAX_CHARS] for x in picked[:PARITY_MAX_ERRORS]]


def parity_record(exit_code: int, log: str) -> dict:
    """The :data:`PARITY_RISK_KIND` record for a contract-tier run that exited `exit_code` (non-zero) with output `log`."""
    return {'kind': PARITY_RISK_KIND, 'display_name': 'local world parity unproven', 'exit_code': exit_code, 'errors': parity_errors(log)}


def parity_risk(art: Path) -> dict | None:
    """The :data:`PARITY_RISK_KIND` risk for the bench whose artifacts dir is `art`, while its build scores on the
    locally served world (`.qual_transport` is `local-served`); else None."""
    try:
        if (Path(art).parent / '.qual_transport').read_text(encoding='utf-8').strip() != 'local-served':
            return None
        risk = json.loads((Path(art) / PARITY_FILENAME).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return risk if isinstance(risk, dict) and risk.get('kind') == PARITY_RISK_KIND else None


def parity_sentence(risk: dict) -> str:
    """A :data:`PARITY_RISK_KIND` risk said with its exit code and first error lines (internal banner only)."""
    errors = [str(e) for e in risk.get('errors') or []] if isinstance(risk.get('errors'), list) else []
    return PARITY_TEXT.format(exit_code=risk.get('exit_code'), errors='; '.join(errors) or 'no error line')


#: A landing blocker on our own tooling (`--surface plugin|sdk`) whose named checks the final run re-checked clean
#: (`blocking_split`'s :data:`HOLD_TOOLING_RISK`): `{kind, surface, checks, why}`, `checks` by display name.
#: Internal only: a fault that is ours, with nothing for the customer to act on, never reaches `customer_banner`.
TOOLING_RISK_KIND = 'tooling_risk'
TOOLING_RISK_TEXT = 'Blocking defect on our own tooling ({surface}), recorded at landing: {checks} re-checked on the final run.'


def tooling_risk_sentence(risk: dict) -> str:
    """A :data:`TOOLING_RISK_KIND` risk said with its surface and checks (internal banner only)."""
    checks = [str(c) for c in risk.get('checks') or []] if isinstance(risk.get('checks'), list) else []
    return TOOLING_RISK_TEXT.format(surface=risk.get('surface') or 'unnamed', checks=', '.join(checks) or 'no check named')


#: A FINAL run that played each conversation once (:func:`final_sampling`): its verdicts rest on one draw each. The
#: suite's own standing, so internal banner only; like the other run risks it caps the rung at `qualified`.
FINAL_SAMPLING_KIND = 'final_run_sampling'
FINAL_SAMPLING_TEXT = 'The final run played each conversation once (3 trials is the norm), so each of its verdicts rests on one sample.'
#: The runner's `run_summary.json` `trials` block schema (the SDK's `TRIALS_SCHEMA`, mirrored by value).
TRIALS_SCHEMA = 'trials-v1'


def final_sampling_sentence(_risk: dict) -> str:
    """A :data:`FINAL_SAMPLING_KIND` risk said (internal banner only)."""
    return FINAL_SAMPLING_TEXT


#: The suite's own bench risks, each with its sentence: said in the internal banner only, never the customer's.
INTERNAL_RUN_RISKS = {MOCK_GAP_KIND: mock_gap_sentence, PARITY_RISK_KIND: parity_sentence, TOOLING_RISK_KIND: tooling_risk_sentence,
                      FINAL_SAMPLING_KIND: final_sampling_sentence}


#: The run's loop-named risks (`eval_rounds.py never-scored --name-untested`): a check that scored nothing on the final run,
#: named `untested_premise` so the verdict never reads its silence as a pass.
LOOP_RISKS_FILENAME = 'loop_named_risks.json'


def with_loop_risks(gate: dict | None, out: Path | None) -> dict | None:
    """`gate` with the run dir's loop-named risks added to its `named_risks`, one per (kind, policy) it does not name —
    then every named risk with no name of its own named by its policy's display name (:func:`with_display_names`, off
    the run's workdir), so the anchor and the verdict never show an eval's id where the eval has a name."""
    if not isinstance(gate, dict) or out is None or not isinstance(gate.get('named_risks'), list):
        return gate
    try:
        risks = json.loads((out / LOOP_RISKS_FILENAME).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        risks = []
    have = {(r.get('kind'), r.get('policy')) for r in gate['named_risks'] if isinstance(r, dict)}
    own = [r for r in risks if isinstance(r, dict) and (r.get('kind'), r.get('policy')) not in have] if isinstance(risks, list) else []
    merged = {**gate, 'named_risks': [*gate['named_risks'], *own]} if own else gate
    return with_display_names(merged, policy_display_names(run_workdir(out)))


#: The eval set's mirror in the workdir: the display names the report shows are read off it.
POLICIES_CONFIG = 'policies_config.yaml'
#: A label that reads as an eval's id, not its name: one snake_case or dotted token (`absence_probe_RENDER_ACTIONS.omits`).
_ID_SHAPED = re.compile(r'[A-Za-z0-9]+(?:[_.][A-Za-z0-9]+)+')
_CONFIG_KEY = re.compile(r'([A-Za-z0-9_.-]+):[ \t]*(?:#.*)?')
_DISPLAY_KEY = re.compile(r'display_name:(?:[ \t]+(.*))?')
_SINGLE_QUOTED = re.compile(r"'((?:[^']|'')*)'[ \t]*(?:#.*)?")
_DOUBLE_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"[ \t]*(?:#.*)?')


def _is_name(policy: str, label: object) -> bool:
    """Whether `label` names `policy` for a reader: a non-blank string that is neither its id nor id-shaped."""
    return isinstance(label, str) and bool(label.strip()) and label.strip() != policy and not _ID_SHAPED.fullmatch(label.strip())


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(' \t'))


def _display_scalar(parts: list[str]) -> str | None:
    """A `display_name:` value from its lines (the key's rest, then its deeper continuation lines), or None for any value
    this reader cannot read whole — a block scalar, a tag / anchor / alias, a flow collection, an escaped multi-line string."""
    first = parts[0].strip()
    if not first or first[0] in '|>!&*{[%@`':
        return None
    joined = ' '.join(p.strip() for p in parts)  # a flow scalar's line breaks fold to spaces
    if first[0] == "'":
        quoted = _SINGLE_QUOTED.fullmatch(joined)
        return (quoted.group(1).replace("''", "'").strip() or None) if quoted else None
    if first[0] == '"':
        quoted = _DOUBLE_QUOTED.fullmatch(joined)
        if quoted is None or ('\\' in quoted.group(1) and len(parts) > 1):
            return None
        try:
            value = json.loads(f'"{quoted.group(1)}"')
        except ValueError:
            return None
        return (value.strip() or None) if isinstance(value, str) else None
    return re.split(r'[ \t]#', joined, maxsplit=1)[0].strip() or None


def _display_names_by_line(text: str) -> dict[str, str]:
    """`{policy: display_name}` read line by line off the block YAML (the scripts run on a bare interpreter, with no
    PyYAML — `regressions.catastrophic_checks` reads the tier the same way): a top-level key opens a policy, its
    `display_name:` at the policy's own indent names it. A value this reader cannot read whole names nothing, so the
    risk keeps its id rather than a wrong name; a key repeated later wins, as PyYAML's does."""
    names: dict[str, str] = {}
    lines = text.lstrip('﻿').splitlines()
    policy: str | None = None
    indent: int | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line[:1] not in ' \t':
            top = _CONFIG_KEY.fullmatch(line)
            policy, indent = (top.group(1) if top else None), None
            continue
        lead = _indent_of(line)
        indent = lead if indent is None else indent
        key = _DISPLAY_KEY.fullmatch(line[lead:])
        if policy is None or lead != indent or key is None:
            continue
        parts = [key.group(1) or '']
        while i < len(lines) and lines[i].strip() and _indent_of(lines[i]) > lead:
            parts.append(lines[i])
            i += 1
        ahead = next((ln for ln in lines[i:] if ln.strip()), '')
        blank_inside = i < len(lines) and not lines[i].strip() and _indent_of(ahead) > lead
        value = None if blank_inside else _display_scalar(parts)
        if value is None:
            names.pop(policy, None)
        else:
            names[policy] = value
    return names


def policy_display_names(workdir: Path | None) -> dict[str, str]:
    """`{policy: display_name}` off `<workdir>/policies_config.yaml` — the names the report shows for each eval. PyYAML
    when it is importable and the file parses, else :func:`_display_names_by_line`. `{}` when there is no file to read."""
    if workdir is None:
        return {}
    try:
        text = (Path(workdir) / POLICIES_CONFIG).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        import yaml  # noqa: PLC0415 - optional: the scripts run on a bare interpreter too
    except ImportError:
        return _display_names_by_line(text)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return _display_names_by_line(text)
    if not isinstance(doc, dict):
        return {}
    return {str(policy): body['display_name'].strip() for policy, body in doc.items()
            if isinstance(body, dict) and isinstance(body.get('display_name'), str) and body['display_name'].strip()}


def run_workdir(run_dir: Path | None) -> Path | None:
    """The workdir of a run dir `<workdir>/artifacts/runs/<run>`; None for any other path (a trial dir, a bare dir)."""
    if run_dir is None:
        return None
    out = Path(run_dir).resolve()
    return out.parents[2] if out.parent.name == 'runs' and out.parent.parent.name == 'artifacts' else None


#: The name of a `suite_defect` risk keyed on its scenario (the backend files a goal-check dispute with `policy == subject`,
#: the scenario, which no policy owns): its goal check, said by the scenario's name.
GOAL_CHECK_OF = 'the goal check of {}'


def with_display_names(gate: dict | None, names: dict[str, str]) -> dict | None:
    """`gate` with each named risk that carries no name of its own — none, blank, or its policy's id — named by that
    policy's display name (:func:`policy_display_names`). A risk that has a name keeps it; one whose policy has no
    display name keeps its id; a risk with no policy (a mock gap) is left as it is. A `suite_defect` risk whose policy
    IS its subject (a scenario's goal check) is named :data:`GOAL_CHECK_OF` that scenario, never by its folder id."""
    if not isinstance(gate, dict) or not isinstance(gate.get('named_risks'), list):
        return gate

    def named(risk: object) -> object:
        if not isinstance(risk, dict) or not isinstance(policy := risk.get('policy'), str):
            return risk
        label = risk.get('display_name')
        if isinstance(label, str) and label.strip() and label.strip() != policy:
            return risk
        if risk.get('kind') == 'suite_defect' and risk.get('subject') == policy:
            return {**risk, 'display_name': GOAL_CHECK_OF.format(_said(policy, CUSTOMER))}
        name = names.get(policy)
        return {**risk, 'display_name': name} if _is_name(policy, name) else risk

    return {**gate, 'named_risks': [named(r) for r in gate['named_risks']]}


def id_labelled_risks(risks: list, names: dict[str, str]) -> list[str]:
    """The policies of `risks` labelled by an id — their own, or any snake_case / dotted token — although the policy has
    a display name (:func:`policy_display_names`): a label no reader should see. Rows with no policy are skipped."""
    out = []
    for risk in risks:
        if isinstance(risk, dict) and isinstance(policy := risk.get('policy'), str):
            if _is_name(policy, names.get(policy)) and not _is_name(policy, risk.get('display_name')):
                out.append(policy)
    return out


#: The SDK's typed run-fault fields on each lost item's `error.json`: `transient` (bool) and `route_cause` (`transport`
#: is a 502/503/504, a refused or reset connection, a timeout — a re-run answers it). The remedy is read off these,
#: never off the error's prose.
TRANSIENT_ROUTE_CAUSES = ('transport',)
#: The plugin-local loss class of a scenario every trial lost to one untyped fault (SDK `trials.repeated_losses`).
REPEATED = 'repeated'
#: The stable prefix the backend puts on every refusal to LOAD a stored scenario (the backend's
#: `run_attribution.SCENARIO_LOAD_REFUSAL_PREFIX`, raised by `POST /threads`), read the way the backend routes on
#: it: a non-transient `backend_http` record whose `response_body` carries it. Not yet a vendored contract block.
SCENARIO_LOAD_REFUSAL_PREFIX = 'scenario_unloadable:'
#: The plugin-local loss class of a scenario the backend refused to load: the generated scenario is the defect.
UNLOADABLE = 'unloadable'
#: The key a gate carries each refused scenario's recorded reason under (`{scenario: reason}`, :func:`load_refusals`).
LOAD_REFUSALS = 'load_refusals'
#: Which class a scenario's loss takes when its records disagree: a scenario that cannot load outranks a reproducible
#: fault, which outranks a transient one.
_LOSS_RANK = {'transient': 0, 'deterministic': 1, UNLOADABLE: 2}


def load_refusal(record: dict) -> str | None:
    """The reason a backend refusal to LOAD the scenario gives (after :data:`SCENARIO_LOAD_REFUSAL_PREFIX`, off the
    `response_body` or its JSON `detail`), one line; None for any other record."""
    body = record.get('response_body')
    if record.get('kind') != 'backend_http' or record.get('transient') is True or not isinstance(body, str):
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get('detail'), str):
        body = parsed['detail']
    at = body.find(SCENARIO_LOAD_REFUSAL_PREFIX)
    return None if at < 0 else _one_line(body[at + len(SCENARIO_LOAD_REFUSAL_PREFIX):]).strip()


def _loss_records(out: Path) -> list[tuple[str, dict]]:
    """`(scenario, record)` for every readable `error.json` in the run dir and its trial dirs."""
    found = []
    for trial in _trial_dirs(out):
        for record_path in sorted(trial.glob('*/error.json')):
            try:
                record = json.loads(record_path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                found.append((str(record.get('scenario') or record_path.parent.name), record))
    return found


def load_refusals(out: Path) -> dict[str, str]:
    """`{scenario: reason}` for each scenario the backend refused to load in this run (:func:`load_refusal`), first reason."""
    refusals: dict[str, str] = {}
    for scenario, record in _loss_records(out):
        if (reason := load_refusal(record)) is not None:
            refusals.setdefault(scenario, reason)
    return refusals


def run_loss_classes(out: Path) -> dict[str, str]:
    """`{scenario: 'transient' | 'deterministic' | 'unloadable' | 'repeated'}` off the run's own `error.json` records (run
    dir and trial dirs), folded the way the backend folds them: one deterministic item makes the scenario's loss
    deterministic, and one refusal to LOAD it (:func:`load_refusal`) makes it `unloadable` — the backend's own reading of
    that record. A record that types neither field says nothing — except a scenario the SDK names in
    `trials.repeated_losses` that the run's one bounded re-run already re-ran (:func:`repeated_rerun`): that one is
    `repeated`. `{}` when the run holds none."""
    out_classes: dict[str, str] = {}
    for scenario, record in _loss_records(out):
        if load_refusal(record) is not None:
            fault_class = UNLOADABLE
        elif record.get('transient') is True or record.get('route_cause') in TRANSIENT_ROUTE_CAUSES:
            fault_class = 'transient'
        elif record.get('transient') is False:
            fault_class = 'deterministic'
        else:
            continue
        if _LOSS_RANK[fault_class] > _LOSS_RANK.get(out_classes.get(scenario, ''), -1):
            out_classes[scenario] = fault_class
    # Trials run side by side can share one outage: a repeat is trusted only once the bounded re-run lost it again.
    rerun = repeated_rerun(out)
    for scenario in sdk_repeats(out):
        if scenario in rerun:
            out_classes.setdefault(scenario, REPEATED)  # a typed record above still wins
    return out_classes


def sdk_repeats(out: Path) -> list[str]:
    """The scenarios the SDK's composed summary names every trial lost to one untyped fault (`trials.repeated_losses`)."""
    return [str(s) for s in _trials_block(out).get('repeated_losses') or []]


def repeated_rerun(out: Path) -> set[str]:
    """The scenarios the run's one bounded repair (`bounded_repair.json`) already re-ran; empty before it is claimed."""
    try:
        claim = json.loads((out / 'bounded_repair.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    listed = claim.get('rerun_scenarios') if isinstance(claim, dict) else None
    return {str(s) for s in listed} if isinstance(listed, list) else set()


def with_run_losses(gate: dict | None, classes: dict[str, str]) -> dict | None:
    """`gate` with each of its lost scenarios classed by the run's own records (:func:`run_loss_classes`) where they type
    one. Only scenarios the gate already holds as losses are touched; a gate that types no classes is given them only
    when the records cover every loss, so an untyped loss is never silently read as typed."""
    if not isinstance(gate, dict) or not classes:
        return gate
    held = _loss_classes(gate)
    lost = set(held) if held is not None else {str(c.get('check')) for c in gate.get('block_causes') or []
                                               if isinstance(c, dict) and c.get('cause') == 'run_incomplete'}
    own = {name: fault_class for name, fault_class in classes.items() if name in lost}
    if not own or (held is None and set(own) != lost):
        return gate
    return {**gate, 'loss_fault_classes': {**(held or {}), **own}}


#: The key a gate carries this run's scored cells under (:func:`with_scored_cells`): `{policy: decided cells}`.
SCORED_CELLS = 'scored_cells'


def scored_cells(out: Path) -> dict[str, int] | None:
    """`{policy: cells it decided (passed + failed)}` off the run dir's own `run.json` `evals` — the scored verdicts the
    banner's "no result" list is built from. None when run.json is unreadable or carries no `evals`."""
    try:
        evals = json.loads((out / 'run.json').read_text(encoding='utf-8')).get('evals')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    if not isinstance(evals, list):
        return None
    return {str(e['id']): (_int_or_none(e.get('passed')) or 0) + (_int_or_none(e.get('failed')) or 0)
            for e in evals if isinstance(e, dict) and e.get('id')}


def with_scored_cells(gate: dict | None, cells: dict[str, int] | None) -> dict | None:
    """`gate` carrying the run's scored cells (:data:`SCORED_CELLS`); `gate` itself when there are none to carry."""
    return {**gate, SCORED_CELLS: cells} if isinstance(gate, dict) and cells is not None else gate


def _decided(gate: dict, risk: dict) -> bool:
    """Whether this run's scored verdicts decided `risk`'s check at least once: then it has a result, whatever the gate
    said about setting its situation up. Never for a `premise_unconfirmed` risk (`eval_rounds.name_untested`): its cells
    were decided where its planted content could not be confirmed to reach the agent, so they are no result."""
    if risk.get('premise_unconfirmed') is True:
        return False
    return bool((gate.get(SCORED_CELLS) or {}).get(str(risk.get('policy'))))


def _unscored(gate: dict, named: list[dict]) -> list[str]:
    """The checks this run scored on NO conversation (no pass, no fail) that no named risk already says."""
    said = {str(r.get('policy')) for r in named}
    return sorted(p for p, n in (gate.get(SCORED_CELLS) or {}).items() if not n and p not in said)


def typed(gate: dict) -> bool:
    """Whether `gate` carries the typed verdict: both fields present."""
    return 'remedy' in gate and isinstance(gate.get('named_risks'), list)


def rung(gate: dict) -> str:
    """The suite rung a FINAL-stage `release_gate` licenses.

    ANY final gate that blocks is `not yet qualified`, typed, untyped or older alike, so the rung
    agrees with the rebuild banner. A clear older gate (:func:`report_gate`) is `qualified`: it is
    only half of what `validated` claims. So is a clear gate beside a runner risk (:data:`RUN_RISKS`), and one whose
    backend names a hold (:data:`RUNG_HOLDS`)."""
    if gate.get('blocking'):
        return NOT_QUALIFIED
    if gate.get(OLDER_GATE) or gate.get(RUN_RISKS) or gate.get(RUNG_HOLDS):
        return QUALIFIED
    if not typed(gate):
        return VALIDATED
    return QUALIFIED if [r for r in gate['named_risks'] if not _rung_neutral(r)] else VALIDATED


#: What can keep a clear gate from licensing `validated`, read off the workdir rather than the backend's report, and
#: the rung each caps at: a recorded blocking defect that still corrupts the final scores is the floor (the anchor's own
#: rule); one the final run re-checked (:func:`blocking_split`) caps at `qualified` — the suite still ships with a known
#: eval defect, or a named risk on a check only the backend judges; the rest cap at `qualified` — the gate judged the set,
#: but not under a measurement, a judgement or a client that is known to have held.
HOLD_BLOCKING, HOLD_MEASUREMENT, HOLD_CRITIC_STALE, HOLD_NO_CLIENT_PIN = 'blocking_unresolved', 'measurement_unclean', 'critic_stale', 'no_client_pin'
HOLD_VERIFICATION_STALE = 'verification_stale'
HOLD_BLOCKER_CHECKED = 'blocker_checked'
#: A backend entry on a check only the backend judges, which the final run's audit proved wrong (:func:`_backend_recheck`):
#: a named risk on the anchor, never the floor.
HOLD_BACKEND_FALSE_RED = 'backend_false_red'
#: A plugin / SDK entry whose named checks the final run re-checked clean (:func:`_tooling_recheck`): a named risk on our
#: own tooling, internal only (the anchor's `named_risks`, the internal banner, stderr), never the floor.
HOLD_TOOLING_RISK = TOOLING_RISK_KIND
#: A harness entry the scored final run re-ran clean (:func:`_harness_rerun_recheck`): the one repair plus one re-run the
#: skill prescribes, done. It caps at `qualified`: the critic judged the harness before its repair.
HOLD_HARNESS_RERUN = 'harness_rerun'
HOLD_CAPS = {HOLD_BLOCKING: NOT_QUALIFIED, HOLD_MEASUREMENT: QUALIFIED, HOLD_CRITIC_STALE: QUALIFIED, HOLD_NO_CLIENT_PIN: QUALIFIED,
             HOLD_VERIFICATION_STALE: QUALIFIED, HOLD_BLOCKER_CHECKED: QUALIFIED, HOLD_BACKEND_FALSE_RED: QUALIFIED,
             HOLD_TOOLING_RISK: QUALIFIED, HOLD_HARNESS_RERUN: QUALIFIED}
#: The policy types whose judge runs server-side: the config names only a slice, so no client edit reaches the verdict.
SERVER_JUDGED_TYPES = ('beat_resolution',)
#: The one audit cause that is the check's own misjudgement (a right rule, misapplied). Every other cause is a surface the
#: client owns (rubric, ground truth, harness, facts) or the world (`env`).
JUDGE_CAUSES = ('prompt',)
#: The audit cause that says the world itself answered wrong.
WORLD_CAUSES = ('env',)
#: The audit causes on a surface only a rebuild re-checks (the scenario, the harness, a scanned fact): a backend entry
#: held by one asks for a rebuild, never an eval edit, since no client edit reaches a check only the backend judges.
REBUILD_CAUSES = ('scenario', 'harness', 'scan_fact')
#: The loop-log kinds that change what a critic judged: the harness, the simulated environment, the eval set, the
#: scenario set. A critic pass stands only on the final artifacts: any of these after it voids it.
EDIT_KINDS = ('harness-fix', 'env-fix', 'eval-round', 'regeneration')
#: The workdir files a scenario set or an eval revision rewrites; one modified after the critic voids it like an edit.
JUDGED_MARKERS = ('.scenario_set_id', 'policies_config.yaml')
_RUNG_ORDER = (NOT_QUALIFIED, QUALIFIED, VALIDATED)


def _stamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _run_measurement(run_dir: Path) -> str | None:
    """Why the run's measurement is not clean (`run_summary.json` `measurement_clean`, each trial's, any trial that
    could not run: `build_run_json.invalid_trials`, and run.json's own `run_integrity` channels), else None."""
    try:
        summary = json.loads((run_dir / 'run_summary.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    because = [str(b) for b in summary.get('measurement_unclean_because') or []] if summary.get('measurement_clean') is False else []
    trials = summary.get('trials') if isinstance(summary.get('trials'), dict) else {}
    unclean = [e for e in trials.get('measurement') or [] if isinstance(e, dict) and e.get('clean') is False]
    here = Path(__file__).resolve().parent
    sys.path[:0] = [p for p in (str(here), str(here.parent / 'skills' / 'simulation-run' / 'scripts')) if p not in sys.path]
    import build_run_json  # noqa: PLC0415 - sibling script: the one count of trials that could not run

    invalid = [build_run_json.INVALID_TRIALS_CHANNEL] if build_run_json.invalid_trials(summary) else []
    # The gaps run.json's own build found (`run_integrity`: a declared trail lane no conversation carried).
    try:
        built = (json.loads((run_dir / 'run.json').read_text(encoding='utf-8')).get('run_integrity') or {})
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        built = {}
    found = [str(b) for b in built.get('measurement_unclean_because') or []] if isinstance(built, dict) and built.get('measurement_clean') is False else []
    if summary.get('measurement_clean') is not False and not unclean and not invalid and not found:
        return None
    named = sorted({*because, *invalid, *found, *(str(b) for e in unclean for b in e.get('because') or [])})
    return f'the scored run\'s measurement is not clean ({", ".join(named) or "no channel named"})'


def _critic_staleness(workdir: Path, entries: list) -> str | None:
    """Why the latest `critic` entry predates the last edit to what it judged — a typed :data:`EDIT_KINDS` entry, or a
    harness file (the fingerprint's own manifest, the shared config aside) or a :data:`JUDGED_MARKERS` file modified
    after it — else None."""
    critics = [stamp for e in entries if e.kind == 'critic' and (stamp := _stamp(e.stamp)) is not None]
    if not critics:
        return None
    judged = max(critics)
    edits = [f'{e.kind} at {e.stamp}' for e in entries if e.kind in EDIT_KINDS and (s := _stamp(e.stamp)) is not None and s > judged]
    try:
        import harness_fingerprint  # noqa: PLC0415 - sibling script: the one list of harness files

        names = [n for n in harness_fingerprint.harness_files(workdir, harness_fingerprint.detect_lang(workdir)) if n not in harness_fingerprint.SHARED]
    except (ImportError, OSError):
        names = []
    for name in [*names, *JUDGED_MARKERS]:
        try:
            mtime = datetime.fromtimestamp((workdir / name).stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= judged + timedelta(minutes=1):  # the log stamps to the minute
            edits.append(f'{name} modified {mtime:%Y-%m-%d %H:%M} UTC')
    if not edits:
        return None
    return f'the last critic pass ({judged:%Y-%m-%d %H:%M} UTC) predates edits to what it judged: {"; ".join(edits)}'


def _verification_staleness(workdir: Path) -> str | None:
    """Why the recorded harness checks (`bench_manifest.HARNESS_RECHECKS`) do not cover the harness on disk — one is
    recorded `stale`, or recorded passing on a signature the bench no longer has (an edit after the last verify) — else
    None, and None when the bench has no manifest or verification record."""
    try:
        import bench_manifest as bm  # noqa: PLC0415 - sibling script: the one signature rule

        doc, record = bm._read_json(bm.manifest_path(workdir)), bm._read_json(bm.verification_path(workdir))  # noqa: SLF001
        if not isinstance(doc, dict) or not isinstance(record, dict) or not isinstance(doc.get('checks'), list):
            return None
        by_id = {r.get('id'): r for r in record.get('results') or [] if isinstance(r, dict)}
        memo: dict = {}
        moved = [
            str(check['id']) for check in doc['checks']
            if isinstance(check, dict) and check.get('id') in bm.HARNESS_RECHECKS
            and ((status := (by_id.get(check['id']) or {}).get('status')) == bm.STATUS_STALE
                 or (status in bm._PASSING and by_id[check['id']].get('signature') != bm.check_signature(check, workdir, doc, memo)))  # noqa: SLF001
        ]
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return None
    return f'the harness or its config changed after its checks last ran ({", ".join(moved)}), so they have not verified it' if moved else None


def _results(n: int, noun: str) -> str:
    return f'{n} {noun}' + ('' if n == 1 else 's')


def _final_run_recheck(run_dir: Path | None) -> tuple[dict | None, str | None]:
    """`(what the final run says, None)` — its run, audit rows, ruled units and uncorrected units — or `(None, why it
    cannot re-check anything)`."""
    if run_dir is None:
        return None, 'there is no scored run to re-check it against'
    if run_stage(run_dir) != 'final':
        return None, 'the scored run is not a final run, so nothing re-checked it'
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling scripts: the audit, its units and the correction record
    import red_cells  # noqa: PLC0415
    import verdict_corrections  # noqa: PLC0415

    try:
        run = red_cells.Run(run_dir)
        rows = red_cells.audit_rows(run, [])
    except (red_cell_audit.AuditMissing, ValueError, OSError):
        return None, 'the final run has no readable red-cell audit, so nothing re-checked it'
    ruled, _ = red_cells.ruled_units(run, rows)
    return {'run': run, 'run_dir': Path(run_dir), 'rows': rows, 'ruled': ruled, 'uncorrected': verdict_corrections.uncorrected(run, rows)}, None


def _policy_still_wrong(check: dict, policy: str) -> str | None:
    """Why the final run still carries a wrong or unchecked verdict on `policy`, in plain words — else None."""
    import red_cell_audit  # noqa: PLC0415 - loaded by `_final_run_recheck`
    import red_cells  # noqa: PLC0415

    run = check['run']
    if not any(policy in run.cells(item)[0] for item in run.dirs):
        return f'`{policy}` has no result on the final run, so it could not be re-checked'
    if unruled := [u for u in run.red_units() if u[1] == policy and u not in check['ruled']]:
        return f'`{policy}` has {_results(len(unruled), "failure")} on this run that {"was" if len(unruled) == 1 else "were"} not checked'
    if wrong := [u for u in check['uncorrected'] if u.get('policy') == policy]:
        return f'`{policy}` still has {_results(len(wrong), "result")} our check found wrong and could not correct'
    # A FALSE PASS counts while any unit it names still reads pass, whatever the other rulings say: `uncorrected` skips a
    # contested unit (a CONFIRMED PASS / UNDECIDABLE beside it) and a row `ruled_units` drops. A row naming no unit of
    # this run, or a unit that is not there, still counts: unknown never releases.
    if missed := [r for r in check['rows'] if r.get('policy') == policy and red_cell_audit.verdict_of(dict(r)) == red_cells.FALSE_PASS
                  and _still_passes(run, policy, r)]:
        return f'`{policy}` still passes {_results(len(missed), "result")} our check found should fail'
    return None


def _still_passes(run: object, policy: str, row: dict) -> bool:
    """Whether a FALSE PASS `row` on `policy` still reads pass on `run`: a turn it names whose unit `is_pass`, or one whose
    unit cannot be found (`red_cells._row_turns` resolves none, or the turn has no unit) — never a row of an earlier
    attempt of its item (`red_cells.superseded`)."""
    import red_cells  # noqa: PLC0415 - loaded by `_final_run_recheck`

    item = red_cells.normalize_item(str(row.get('item') or ''))
    if red_cells.superseded(row, run.thread(item)):  # type: ignore[attr-defined]
        return False  # it ruled an earlier attempt: the re-run's own rows rule the conversation that counts
    turns, _ = red_cells._row_turns(run, item, policy, row.get('turn'))  # noqa: SLF001 - the audit's one turn resolver
    return turns is None or any((unit := run.unit(item, policy, t)) is None or red_cells.is_pass(unit) for t in turns)  # type: ignore[attr-defined]


#: What a live entry still needs (:func:`blocking_split`'s third field), set where its reason is made, never read from
#: prose: a REBUILD (a floor surface, or the simulated world answering wrong), an EVAL repair of the checks it names
#: (`eval:<check>[,<check>]`: a verdict on them is still wrong or unchecked), or nothing the customer can do (OURS: our
#: tooling or the backend, its checks clean or none named) — the hand-off's Next line reads it.
REMEDY_REBUILD, REMEDY_EVAL, REMEDY_OURS = 'rebuild', 'eval', 'ours'


def _eval_remedy(policies: object) -> str:
    """The :data:`REMEDY_EVAL` remedy naming `policies` (`eval:a,b`), bare `eval` when it names none."""
    names = [str(p) for p in policies] if isinstance(policies, (list, tuple)) else []  # type: ignore[union-attr]
    return f'{REMEDY_EVAL}:{",".join(names)}' if names else REMEDY_EVAL


def _ticked(policies: tuple[str, ...]) -> str:
    return ', '.join(f'`{p}`' for p in policies)


def _eval_recheck(entry: object, check: dict) -> tuple[str | None, str]:
    """`(why the eval entry is still live, its remedy)` or `(None, why it is released)`: on each named eval no red is
    unruled, no audited correction is still counted (`verdict_corrections.uncorrected`) and no FALSE PASS still reads pass.
    The remedy names every named eval still wrong (:func:`_eval_remedy`)."""
    policies = entry.policies  # type: ignore[attr-defined]
    if wrong := [(policy, w) for policy in policies if (w := _policy_still_wrong(check, policy))]:
        return wrong[0][1], _eval_remedy([p for p, _ in wrong])
    return None, f're-checked on the final run: {_ticked(policies)} no longer {"affects" if len(policies) == 1 else "affect"} the results'


#: The unit statuses a check can stand on: a verdict (`success`) or a judge that could not decide (`inconclusive`). Any
#: other (`error`, …) is a result the check never reached.
_REACHED_STATUSES = ('success', 'inconclusive')


#: What a world fault reads where the entry is held by it.
_WORLD_FAULT = 'the final run\'s audit found the simulated world answering wrong, which only a rebuild re-checks'


def _world_fault(check: dict, policies: object) -> bool:
    """Whether the final audit records a world fault on the entry's own checks `policies`: a FALSE RED of a
    :data:`WORLD_CAUSES` cause on one of them (or naming none), or an `ENV` row (`red_cell_audit.env_rows`) whose
    `rests_on` names one of them. `rests_on` lists the red cells whose cited turn uses that answer (a `{policy, turn}`
    dict, or a string whose `@` / `|` tokens hold the policy); `[]` means no red cell does. A row with no list, or an
    entry naming no check the run scored, counts against every check: unknown never releases. By that definition a
    wrong answer under a PASS is not seen here — a backend entry trusts no unsampled pass anyway, and a tooling release
    caps the rung at `qualified`."""
    import red_cell_audit  # noqa: PLC0415 - loaded by `_final_run_recheck`
    import red_cells  # noqa: PLC0415

    own, run = {str(p) for p in policies} if isinstance(policies, (list, tuple)) else set(), check['run']  # type: ignore[union-attr]
    scored = {str(p) for item in run.dirs for p in run.cells(item)[0]}

    def named(entry: object) -> set[str]:
        tokens = [entry.get('policy')] if isinstance(entry, dict) else re.split(r'[@|]', entry) if isinstance(entry, str) else []
        return {str(t).strip() for t in tokens if t} & scored

    def rests_on_own(row: dict) -> bool:
        on = row.get('rests_on')
        return not isinstance(on, list) or any(not (hit := named(e)) or hit & own for e in on)

    if any(red_cell_audit.verdict_of(dict(r)) == red_cells.FALSE_RED and _cause(r) in WORLD_CAUSES and (not r.get('policy') or r['policy'] in own)
           for r in red_cells.current_rows(check['run'], check['rows'])):  # a superseded attempt's FALSE RED rules nothing
        return True
    return any(rests_on_own(r) for r in red_cell_audit.env_rows(check['run_dir']))


def _tooling_recheck(entry: object, check: dict) -> tuple[str | None, str]:
    """`(why the plugin / SDK entry is still live, its remedy)` or `(None, why it is released)` — released only as a
    :data:`HOLD_TOOLING_RISK`, on the same bar as an eval entry (:func:`_eval_recheck`) plus one: each named check reached
    a verdict on the final run — at least one unit `success` and none of a status outside :data:`_REACHED_STATUSES` — and,
    as on a backend entry, the final audit records no world fault on its checks (:func:`_world_fault`). In practice one
    `error` unit keeps the check live, and `inconclusive` units do not once any unit is `success`."""
    import red_cells  # noqa: PLC0415 - loaded by `_final_run_recheck`

    why, said = _eval_recheck(entry, check)
    if why:
        return why, said
    # A world that answered wrong voids what the checks measured (10-20: a broken mock still blocks), as on a backend entry.
    if _world_fault(check, entry.policies):  # type: ignore[attr-defined]
        return _WORLD_FAULT, REMEDY_REBUILD
    run = check['run']
    for policy in entry.policies:  # type: ignore[attr-defined]
        statuses = [u.get('status') for item in sorted(run.dirs) for _, u in red_cells.units(run.cells(item)[0].get(policy))]
        if missing := [s for s in statuses if s not in _REACHED_STATUSES] or ([] if 'success' in statuses else statuses):
            return f'`{policy}` reached no verdict on {_results(len(missing), "result")} of the final run', _eval_remedy([policy])
    return None, said


#: The run's ended-early code for a scenario voided because its seed did not land (`sanity_signals.SEED_NOT_LANDED`,
#: pinned by a test; not imported, since `sanity_signals` loads `build_run_json`).
SEED_NOT_LANDED = 'seed_not_landed'
#: What a harness entry the final run could not release says (the floor surface's own words).
_HARNESS_LIVE = 'it is a problem in the harness, which only a rebuild re-checks'


def _harness_rerun_recheck(entry: object, check: dict) -> tuple[str | None, str]:
    """`(why the harness entry is still live, its remedy)` or `(None, why it is released)` — released only as a
    :data:`HOLD_HARNESS_RERUN`, when the scored final run is the re-run after a logged repair: its earliest scenario start
    is at or after the entry's minute, a `harness-fix` entry of the loop log (`check['entries']`) is stamped from the
    entry's minute to that start's minute, and no scenario of the run, in any trial, ended :data:`SEED_NOT_LANDED`. A
    trial the run lists (`trials.dirs`) with no summary keeps it live; only a `trial_*` dir found on disk and not listed
    may lack one. Anything unreadable keeps it live: unknown never releases."""
    live = (_HARNESS_LIVE, REMEDY_REBUILD)
    when = getattr(entry, 'when', None)
    run_dir = Path(check['run_dir'])
    if when is None:
        return live
    listed = _trial_dirs(run_dir)[1:]
    trials = [run_dir, *listed, *sorted(d for d in run_dir.glob('trial_*') if d.is_dir())]
    starts: list[datetime] = []
    for i, trial in enumerate(dict.fromkeys(trials)):
        try:
            summary = json.loads((trial / 'run_summary.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            if i == 0 or trial in listed:
                return live
            continue
        except (OSError, UnicodeDecodeError, ValueError):
            return live
        if not isinstance(summary, dict):
            return live
        ended = summary.get('ended_early_scenarios') or {}
        if not isinstance(ended, dict) or SEED_NOT_LANDED in ended.values():
            return live
        if i == 0:
            timings = summary.get('scenario_timings')
            if not isinstance(timings, dict) or not timings:
                return live
            for timing in timings.values():
                try:
                    start = datetime.fromisoformat(str(timing['started_at']).replace('Z', '+00:00'))
                except (TypeError, KeyError, ValueError):
                    return live
                starts.append(start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc))
    # Minute-stamped entries cannot order a run start inside the entry's own minute, so a same-minute start counts as
    # after. This is the lenient (fail-open) reading, on purpose: RT70 HA logged its entry at 13:49 and the re-run
    # started at 13:49:47. The loop log's same-minute convention (`append_loop_log`) is the fail-closed reading for
    # adoption and does not apply here. The residual risk, an entry logged up to 59 s after the run started, is
    # narrowed by the logged repair the release also needs.
    started = min(starts).replace(second=0, microsecond=0)
    if started < when:
        return live
    fixes = [e.when for e in check.get('entries') or () if getattr(e, 'kind', None) == 'harness-fix' and getattr(e, 'when', None)]
    if not any(when <= fixed <= started for fixed in fixes):
        return live
    return None, 'the final run re-ran the suite after the logged repair, with every conversation starting from the state it set up'


def _server_judged(body: object) -> bool:
    """Whether a policy body is one only the backend judges: a :data:`SERVER_JUDGED_TYPES` type in its slice shape —
    exactly one of `additional_params.beat_id` / `metric`, and no judge body of its own."""
    if not isinstance(body, dict) or body.get('type') not in SERVER_JUDGED_TYPES or body.get('system_prompt') or body.get('expression'):
        return False
    params = body.get('additional_params')
    return isinstance(params, dict) and bool(params.get('beat_id')) != bool(params.get('metric'))


def _cause(row: dict) -> str:
    return str(row.get('defect_surface') or row.get('cause') or '').strip().lower()


def _backend_recheck(entry: object, check: dict) -> tuple[str | None, str]:
    """`(why the backend entry is still live, its remedy)` or `(None, why it is released)` — released only as an audit-proven
    false red of a check only the backend judges (:data:`HOLD_BACKEND_FALSE_RED`).

    All must hold, read off disk: the final run's audit is uploaded as it stands and records no world fault on the named
    checks (:func:`_world_fault`); every named check is server-judged (:func:`_server_judged`, off
    `policies_config.yaml`); no server-judged check of the set carries a wrong or unchecked verdict
    (:func:`_policy_still_wrong` — they share one resolver); and on each named check no row is UNDECIDABLE, every FALSE RED
    is the judge's own (:data:`JUDGE_CAUSES`), at least one unit is ruled FALSE RED and nothing else (the proof), and every
    pass is either such a corrected unit or ruled CONFIRMED PASS — a broken check's unsampled passes are not trusted."""
    import red_cell_audit  # noqa: PLC0415 - loaded by `_final_run_recheck`
    import red_cells  # noqa: PLC0415
    from build_run_json import eval_set_bodies  # noqa: PLC0415 - the one reader of policies_config.yaml

    policies, run, run_dir = entry.policies, check['run'], check['run_dir']  # type: ignore[attr-defined]
    if red_cell_audit.state(run_dir) != red_cell_audit.UPLOADED:
        return 'the final run\'s red-cell audit is not uploaded as it stands, so its rulings have not landed', REMEDY_OURS
    verdicts = [(red_cell_audit.verdict_of(dict(r)), r) for r in check['rows']]
    if _world_fault(check, policies):
        return _WORLD_FAULT, REMEDY_REBUILD
    bodies = eval_set_bodies(str(run_dir.parents[2]))
    if bodies is None:
        return 'the eval set could not be read, so who judges the named check cannot be established', REMEDY_OURS
    if foreign := [p for p in policies if not _server_judged(bodies.get(p))]:
        said = f'{_ticked(tuple(foreign))} {"is" if len(foreign) == 1 else "are"} not judged by the backend alone, so only a rebuild re-checks it'
        return said, REMEDY_REBUILD
    for policy in [*policies, *sorted(p for p, b in bodies.items() if _server_judged(b) and p not in policies)]:
        if why := _policy_still_wrong(check, policy):
            return why, REMEDY_OURS  # a check only the backend judges: no client edit reaches it
    for policy in policies:
        mine = [(v, r) for v, r in verdicts if r.get('policy') == policy]
        if undecided := [r for v, r in mine if v == red_cells.UNDECIDABLE]:
            return f'`{policy}` has {_results(len(undecided), "result")} our check could not decide', REMEDY_OURS
        if other := sorted({_cause(r) or 'none named' for v, r in mine if v == red_cells.FALSE_RED and _cause(r) not in JUDGE_CAUSES}):
            remedy = REMEDY_REBUILD if set(other) & set(REBUILD_CAUSES) else REMEDY_OURS
            return f'`{policy}` has failures our check found wrong for a reason outside the check itself ({", ".join(other)})', remedy
        units = {u: {v for v, _ in ruled} for u, ruled in check['ruled'].items() if u[1] == policy}
        proof = {u for u, names in units.items() if names == {red_cells.FALSE_RED}}
        if not proof:
            return f'our check found no failure of `{policy}` wrong on the final run, so the defect is not proven', REMEDY_OURS
        unchecked = [(item, policy, t) for item in sorted(run.dirs) for t, u in red_cells.units(run.cells(item)[0].get(policy))
                     if red_cells.is_pass(u) and (item, policy, t) not in proof and red_cells.CONFIRMED_PASS not in units.get((item, policy, t), set())]
        if unchecked:
            passes = '1 pass on this run that was' if len(unchecked) == 1 else f'{len(unchecked)} passes on this run that were'
            return f'`{policy}` has {passes} not checked', REMEDY_OURS
    return None, f'proven wrong on the final run: {_ticked(policies)}, judged by the backend alone, failed replies our check found correct; those results are corrected'


#: The surfaces a final run can re-check, each with how and the hold its released entry caps the rung with. A harness
#: entry is re-checked by the re-run itself, not by a named check. Every other surface (scenario, env, unnamed) changes
#: behaviour, and only a rebuild re-checks it.
RECHECKS = {'eval': (_eval_recheck, HOLD_BLOCKER_CHECKED), 'backend': (_backend_recheck, HOLD_BACKEND_FALSE_RED),
            'plugin': (_tooling_recheck, HOLD_TOOLING_RISK), 'sdk': (_tooling_recheck, HOLD_TOOLING_RISK),
            'harness': (_harness_rerun_recheck, HOLD_HARNESS_RERUN)}
#: The surfaces whose re-check reads the run, not the checks an entry names: no `--policy` is needed to release one.
RUN_RECHECKED_SURFACES = ('harness',)
#: The surfaces that are our own tooling: a released entry on one is a :data:`HOLD_TOOLING_RISK`.
TOOLING_SURFACES = ('plugin', 'sdk')
#: How a floor surface (no :data:`RECHECKS` row) is said in its live reason; another is said by its own name.
SURFACE_WORDS = {'scenario': 'scenarios', 'harness': 'harness', 'env': 'simulated world'}
#: How each release hold is said: `{n}` released entries, `{why}` their reasons.
RELEASED_TEXT = {
    HOLD_BLOCKER_CHECKED: '{n} blocking defect(s) recorded at landing, {why} — the suite still ships with a known eval defect',
    HOLD_BACKEND_FALSE_RED: ('{n} blocking defect(s) recorded at landing sit on a built-in check that failed replies our review found '
                             'correct; those results are corrected, and the fix to that check is ours'),
    # Internal: the hold text reaches the anchor's `why` and stderr, never the customer's banner.
    HOLD_TOOLING_RISK: ('{n} blocking defect(s) recorded at landing sit on our own tooling; their checks were re-checked on the '
                        'final run'),
    HOLD_HARNESS_RERUN: '{n} harness defect(s) recorded at landing were repaired: {why}',
}


def blocking_split(entries: list, run_dir: Path | None) -> tuple[list[tuple[object, str, str]], list[tuple[object, str]]]:
    """`(live, released)` — each `blocking-unresolved` entry of the loop log with why, the ONE owner of whether a
    landing-time blocker still caps the rung.

    `entries` is the whole loop log (a harness re-check reads its `harness-fix` entries). An entry is RELEASED only when
    its surface has a :data:`RECHECKS` row, it names its check(s) (`--policy`; a :data:`RUN_RECHECKED_SURFACES` entry
    needs none), `run_dir` is the scored FINAL run with a readable audit, and that row's re-check clears it. Anything else is LIVE, with the first
    reason it could not be released: unknown never releases. A live entry also carries its remedy (:data:`REMEDY_REBUILD`
    / :data:`REMEDY_EVAL` / :data:`REMEDY_OURS`), set where its reason is made: a floor surface is a rebuild; an eval entry
    is always an eval repair; a backend / tooling entry naming no check, or one the final run could not re-check, is ours."""
    blocks = [e for e in entries if getattr(e, 'kind', None) == 'blocking-unresolved']
    live: list[tuple[object, str, str]] = []
    released: list[tuple[object, str]] = []
    check: dict | None = None
    final_why: str | None = None
    checked = False
    for entry in blocks:
        recheck = RECHECKS.get(entry.surface)
        if recheck is None:
            words = SURFACE_WORDS.get(entry.surface, f'{entry.surface or "unnamed"} surface')
            live.append((entry, f'it is a problem in the {words}, which only a rebuild re-checks', REMEDY_REBUILD))
            continue
        # An eval entry says its eval is wrong, whether or not the final run could re-check it.
        unchecked = _eval_remedy(entry.policies) if entry.surface == 'eval' else REMEDY_OURS
        by_run = entry.surface in RUN_RECHECKED_SURFACES
        if not entry.policies and not by_run:
            live.append((entry, 'it names no check, so it cannot be re-checked against the final run', unchecked))
            continue
        if not checked:
            check, final_why = _final_run_recheck(run_dir)
            # A harness re-check reads the log's own `harness-fix` entries: the repair the release needs.
            check = {**check, 'entries': entries} if check is not None else None
            checked = True
        if check is None:
            # A harness entry nothing re-ran says what it always said: only a rebuild re-checks it.
            live.append((entry, _HARNESS_LIVE, REMEDY_REBUILD) if by_run else (entry, str(final_why), unchecked))
            continue
        why, said = recheck[0](entry, check)
        if why:
            live.append((entry, why, said))
        else:
            released.append((entry, said))
    return live, released


def remedies(live: list[tuple[object, str, str]]) -> str:
    """The live entries' remedies said once each, in the order the hand-off's Next line weighs them: `rebuild`, then
    `eval:<every check named>`, then `ours` (`lane_pin.py scope`'s `remedy …`). `''` when nothing is live."""
    said = [r for _, _, r in live]
    checks = list(dict.fromkeys(c for r in said if r.startswith(REMEDY_EVAL) for c in r.partition(':')[2].split(',') if c))
    out = [REMEDY_REBUILD] if REMEDY_REBUILD in said else []
    if any(r.startswith(REMEDY_EVAL) for r in said):
        out.append(_eval_remedy(checks))
    return ', '.join([*out, *([REMEDY_OURS] if REMEDY_OURS in said else [])])


def released_hold(entry: object) -> str:
    """The hold a released entry caps the rung with (:data:`RECHECKS`)."""
    return RECHECKS[entry.surface][1]  # type: ignore[attr-defined]


def _display(policy: str, names: dict[str, str]) -> str:
    """`policy` said by its display name (:func:`policy_display_names`), its id where it has none."""
    name = names.get(policy)
    return name.strip() if _is_name(policy, name) else policy  # type: ignore[union-attr]


def named_risks(released: list[tuple[object, str]], names: dict[str, str] | None = None) -> list[dict]:
    """The anchor's named risks among released entries: `[{kind, checks, why}]` for each audit-proven false red of a check
    only the backend judges, and `[{kind, surface, checks, why}]` for each tooling entry its checks cleared
    (:data:`HOLD_TOOLING_RISK`, internal only), its `checks` by display name (`names`: :func:`policy_display_names`)."""
    out: list[dict] = []
    for e, why in released:
        hold = released_hold(e)
        if hold == HOLD_BACKEND_FALSE_RED:
            out.append({'kind': HOLD_BACKEND_FALSE_RED, 'checks': list(e.policies), 'why': why})  # type: ignore[attr-defined]
        elif hold == HOLD_TOOLING_RISK:
            out.append({'kind': HOLD_TOOLING_RISK, 'surface': e.surface,  # type: ignore[attr-defined]
                        'checks': [_display(p, names or {}) for p in e.policies], 'why': why})  # type: ignore[attr-defined]
    return out


def tooling_risks(out: Path) -> list[dict]:
    """The :data:`HOLD_TOOLING_RISK` risks of the run dir `out` — its loop log's plugin / SDK entries that `out` (the
    scored final run) re-checked clean — for the internal banner (:data:`INTERNAL_RUN_RISKS`). `[]` when there are none:
    the final run is read only when such an entry names a check."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import append_loop_log  # noqa: PLC0415 - the ONE loop-log parser

    entries = [e for e in append_loop_log.parse_entries(Path(out).parent.parent / 'loop_log.md')
               if e.kind == 'blocking-unresolved' and e.surface in TOOLING_SURFACES and e.policies]
    if not entries:
        return []
    _, released = blocking_split(entries, Path(out))
    return named_risks(released, policy_display_names(run_workdir(Path(out))))


def validation_holds(workdir: Path | None, run_dir: Path | None, *, client_pin_required: bool) -> list[tuple[str, str]]:
    """`[(hold, why)]` — each workdir fact that keeps `validated` from standing (:data:`HOLD_CAPS`).

    Read off disk: the loop log's typed `blocking-unresolved` entries, split by :func:`blocking_split` against the scored
    run (the live ones are the count `lane_pin.py anchor` caps on; the released ones cap at `qualified`), the scored
    run's `measurement_clean`, the latest `critic` entry against the last harness/environment edit, the recorded harness
    checks against the harness on disk, and — where a loop
    arms one (`client_pin_required`) — whether a client pin governed it (`client_pin.in_force` at the run's time)."""
    holds: list[tuple[str, str]] = []
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    if workdir is not None:
        import append_loop_log  # noqa: PLC0415 - the ONE loop-log parser

        art = Path(os.environ.get('ART') or workdir / 'artifacts') if run_dir is None else run_dir.parent.parent
        entries = append_loop_log.parse_entries(art / 'loop_log.md')
        live, released = blocking_split(entries, run_dir)
        if live:
            holds.append((HOLD_BLOCKING, f'the loop log records {len(live)} BLOCKING — UNRESOLVED defect(s): '
                                         + '; '.join(why for _, why, _ in live)))
        for hold in dict.fromkeys(released_hold(e) for e, _ in released):
            mine = [why for e, why in released if released_hold(e) == hold]
            holds.append((hold, RELEASED_TEXT[hold].format(n=len(mine), why='; '.join(mine))))
        if stale := _critic_staleness(workdir, entries):
            holds.append((HOLD_CRITIC_STALE, stale))
        if moved := _verification_staleness(workdir):
            holds.append((HOLD_VERIFICATION_STALE, moved))
    if run_dir is not None and (unclean := _run_measurement(run_dir)):
        holds.append((HOLD_MEASUREMENT, unclean))
    if client_pin_required and workdir is not None:
        import client_pin  # noqa: PLC0415 - sibling script: the pin and its release record

        at = None
        try:
            written = json.loads((run_dir / 'run_summary.json').read_text(encoding='utf-8')).get('written_at') if run_dir else None
            at = datetime.fromisoformat(str(written).replace('Z', '+00:00')) if written else None
        except (OSError, UnicodeDecodeError, ValueError, AttributeError):
            at = None
        if not client_pin.in_force(workdir, at):
            holds.append((HOLD_NO_CLIENT_PIN, 'no client pin governed the loop, so which plugin and SDK commits made these runs is not recorded'))
    return holds


def capped_rung(anchor: str, holds: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """`(the rung once each hold caps it, the holds that moved it)` — a hold never raises a rung."""
    capped, moved = anchor, []
    for hold, why in holds:
        cap = HOLD_CAPS[hold]
        if anchor in _RUNG_ORDER and _RUNG_ORDER.index(cap) < _RUNG_ORDER.index(capped):
            capped = cap
        if anchor in _RUNG_ORDER and _RUNG_ORDER.index(cap) < _RUNG_ORDER.index(anchor):
            moved.append(why)
    return capped, moved


def _rung_neutral(risk: object) -> bool:
    """A named risk that never moves the rung (:data:`RUNG_NEUTRAL_KINDS`)."""
    return isinstance(risk, dict) and risk.get('kind') in RUNG_NEUTRAL_KINDS


def _one_line(value: object) -> str:
    return ' '.join(str(value).split())


def _run_failures(report: dict | None) -> dict[str, list[dict]]:
    """The report's `scenario_run_failed` findings, keyed by the scenario each is a run of (`s/trial_2` -> `s`)."""
    out: dict[str, list[dict]] = {}
    for finding in (report or {}).get('findings') or []:
        if isinstance(finding, dict) and finding.get('class') == RUN_FAILED_FINDING:
            for name in finding.get('scenarios') or [str(finding.get('locus') or '').rsplit('/run', 1)[0]]:
                if name:
                    out.setdefault(str(name).partition('/')[0], []).append(finding)
    return out


#: The remedies a deterministic run loss rides beside (None: an older backend that names no remedy for it).
_LOSS_REMEDIES = (None, 'fix_env_rerun_scenarios', 'replant_premise')


def _loss_classes(gate: dict) -> dict[str, str] | None:
    """The gate's typed `loss_fault_classes` (`{scenario: class}`), or None when it carries none (an older backend)."""
    classes = gate.get('loss_fault_classes')
    return {str(k): str(v) for k, v in classes.items()} if isinstance(classes, dict) else None


def transient_losses(gate: dict) -> list[str]:
    """The scenarios a fault the runtime typed transient lost, off the gate's own per-loss class; `[]` on a gate that types none."""
    return sorted(name for name, fault_class in (_loss_classes(gate) or {}).items() if fault_class == 'transient')


def unloadable_losses(gate: dict) -> list[str]:
    """The scenarios the backend refused to load (:data:`UNLOADABLE`), off the gate's per-loss class; `[]` when none."""
    return sorted(name for name, fault_class in (_loss_classes(gate) or {}).items() if fault_class == UNLOADABLE)


def repeated_losses(gate: dict) -> list[str]:
    """The scenarios every trial lost to one untyped fault (:data:`REPEATED`), off the gate's per-loss class; `[]` when none."""
    return sorted(name for name, fault_class in (_loss_classes(gate) or {}).items() if fault_class == REPEATED)


def deterministic_losses(gate: dict, report: dict | None = None) -> list[str]:
    """The scenarios a non-transient, non-agent run fault voided — a re-run reproduces it.

    Each loss's own class when the gate types it (`loss_fault_classes`) — a transient one is never here. Else the
    gate's own `deterministic_losses` when it carries one. Otherwise read off what the backend publishes
    for that case: a block whose remedy is not `rerun` (the backend withholds it exactly when a loss was
    deterministic) whose `run_incomplete` scenarios the report's `scenario_run_failed` findings name.
    A `rerun` remedy means every loss was transient, so nothing here.
    """
    classes = _loss_classes(gate)
    if classes is not None:
        return sorted(name for name, fault_class in classes.items() if fault_class == 'deterministic')
    listed = gate.get('deterministic_losses')
    if isinstance(listed, list):
        return [str(name) for name in dict.fromkeys(listed)]
    if not gate.get('blocking') or gate.get('remedy') not in _LOSS_REMEDIES:
        return []
    failed = _run_failures(report)
    causes = [c for c in gate.get('block_causes') or [] if isinstance(c, dict) and c.get('cause') == 'run_incomplete']
    return [name for name in dict.fromkeys(str(c.get('check')) for c in causes) if name in failed]


def _said(name: object, audience: str) -> str:
    """A scenario or check name on one line; the customer's line names a run-folder id by its scenario name."""
    line = _one_line(name)
    return _FOLDER_ID.sub(lambda m: m.group('name'), line) if audience == CUSTOMER else line


def _names(names: list[str], audience: str) -> str:
    return ', '.join(dict.fromkeys(_said(n, audience) for n in names))


def _loss_sentence(text: str, names: list[str], report: dict | None, audience: str = INTERNAL) -> str:
    """`text` (`FIX_ENV_TEXT` / `TRANSIENT_LOSS_TEXT` / `REPEATED_LOSS_TEXT` / `REPEATED_LOSS_CUSTOMER_TEXT`) naming `names`,
    with the first recorded error their findings quote.
    The customer's line quotes the error only up to a clause naming our settings (:data:`_INTERNAL_CLAUSE`)."""
    failed = _run_failures(report)
    errors = [_one_line(ev.get('excerpt')) for name in names for f in failed.get(name, [])
              for ev in f.get('evidence') or [] if isinstance(ev, dict) and ev.get('excerpt')]
    error = _excerpt(errors[0] if errors else '', audience)
    return text.format(_names(names, audience), error=f' (recorded error: {error})' if error else '')


def _excerpt(error: str, audience: str) -> str:
    """A recorded error as one banner quotes it: at most :data:`ERROR_EXCERPT_CHARS`, the customer's copy cut before a
    clause naming our settings (:data:`_INTERNAL_CLAUSE`)."""
    if audience == CUSTOMER and (internal := _INTERNAL_CLAUSE.search(error)):
        cut = max(error.rfind(': ', 0, internal.start()), error.rfind('; ', 0, internal.start()))
        error = error[:cut].rstrip(' .,;:') if cut > 0 else ''
    if len(error) > ERROR_EXCERPT_CHARS:
        error = error[: ERROR_EXCERPT_CHARS - 1].rstrip() + '…'
    return error


def _unloadable_sentence(names: list[str], gate: dict, audience: str = INTERNAL) -> str:
    """:data:`SCENARIO_UNLOADABLE_TEXT` naming `names`, quoting the first refusal reason the gate carries
    (:data:`LOAD_REFUSALS`) — the customer's copy only its first sentence, which says what is wrong with the scenario."""
    refusals = gate.get(LOAD_REFUSALS) if isinstance(gate.get(LOAD_REFUSALS), dict) else {}
    reason = next((_one_line(refusals[n]) for n in names if isinstance(refusals.get(n), str) and refusals[n].strip()), '')
    if audience == CUSTOMER and (end := reason.find('. ')) > 0:
        reason = reason[: end + 1]
    reason = _excerpt(reason, audience)
    return SCENARIO_UNLOADABLE_TEXT.format(_names(names, audience), reason=f' (recorded reason: {reason})' if reason else '')


def banner(gate: dict | None, report: dict | None = None) -> str | None:
    """The full reading of the verdict (`release_verdict.json` `banner`, internal), or None when it licenses none.

    Read off :func:`verdict_object` — the same object `release_verdict.json` carries — so the banner and the
    typed file can never disagree. `report` is the saved report the gate came from: its `scenario_run_failed`
    findings name the error behind a scenario a non-transient fault voided (:func:`deterministic_losses`)."""
    return verdict_object(gate, report)['banner']


def customer_banner(gate: dict | None, report: dict | None = None) -> str | None:
    """The line the customer reads at the head of the report (`customer_banner`), or None when it licenses none."""
    return verdict_object(gate, report)['customer_banner']


#: The SDK's scores block in the run's summary (`plurai.scoring`, `plurai scores refresh`): read, never computed.
SCORES_KEY, SCORES_SCHEMA = 'scores', 'scores-v1'
#: How the agent verdict the block states is said. A verdict this version does not know says no line at all.
AGENT_WORDS = {'ship': 'Ship', 'dont_ship': "Don't ship", 'no_verdict': 'No verdict'}
#: A conversation's status in the block when the AGENT's own code raised (`plurai.scoring`: scored 0, `crashed`). The
#: runner types it — a harness or infrastructure fault is `harness_error`, out of the score — so it is read, never inferred.
CRASHED_STATUS = 'agent_error'
#: The Agent line's sentences (customer text: product words only). `{score}` / `{cutoff}` are the block's own numbers.
AGENT_TEXT = {
    'ship': 'the suite scored {score} out of 100 with no critical failure.',
    'suite_below_cutoff': 'the suite scored {score} out of 100, below the {cutoff} it needs.',
    'suite_scored': 'The suite scored {score} out of 100.',
    'critical_failure': 'Critical failure{s}: {names}.',
    # The block's `reproduced_high` (`blocking_high`): a required high-severity check failed on 2+ scored conversations.
    'reproduced_high': 'High-severity check{s} failed in more than one conversation: {names}.',
    'high_count': '{name} ({n} conversations)',
    # The block's `agent_crashed` (`crashed_scenarios`): the agent's own code raised on 2+ attempts of a scenario, or on all.
    'agent_crashed': 'it stopped on an error of its own in {names}, so it fails to run there.',
    'nothing_scored': 'no conversation in this run reached a score. Run `/simulation-run` again.',
    'unscored_scenarios': ('no conversation in {names} reached a score, so a failure there cannot be ruled out. Run '
                           '`/simulation-run` again for {those}.'),
    # Don't ship although scenarios are unscored: the block's best case (`best_case_score`, every unscored scenario at
    # 100) is still below the cutoff (`best_case_below_cutoff`), or a critical failure stands — no result there can
    # change the verdict.
    'unscored_below_cutoff': ('no conversation in {names} reached a score, and even with {those} at 100 the suite would score '
                              'at most {best} out of 100, below the {cutoff} it needs{ran}.'),
    'unscored_ran': ' (the scenarios that ran scored {score})',
    'unscored_decided': 'No conversation in {names} reached a score; no result there can change this verdict.',
    'released': 'Set aside after review: {names}.',
    # A conversation the block states `agent_error`: the agent's own code raised, scored 0 — no check names it.
    'crashed': 'Stopped on an error the agent raised: {n} conversation{s} ({names}).',
    'failing_tier':'{n} conversation{s} failed a required check ({names})',
    'goal_undecided': '{n} conversation{s} ended with the goal undecided',
    # The scores block's `non_blocking.replay_turn_only`: a SUBSET of the undecided (never subtracted), so the total above
    # stays the number the report page and its conversation table show.
    'replay_turn_only_one': '1 of them is a replay of one recorded turn, which has no goal',
    'replay_turn_only': '{k} of them are replays of one recorded turn, which have no goal',
    'not_blocking': 'Counted in the score, not critical: {notes}.',
    # A line read off a run that is not the final scored run (`agent_line`'s `from_run`): its head names that run, and
    # the line ends by saying where the agent's verdict comes from.
    'from_run': 'Agent, from {run} ({n} scenario{s})',
    'from_run_uncounted': 'Agent, from {run}',
    'final_gives_verdict': 'The final run gives the verdict on the full suite.',
}
#: How `agent_line` names the run a `from_run` line was read off, by its stage (`runs_index.RUN_STAGES`). A stage not
#: listed, or none, is said as "an earlier run".
FROM_RUN = {'qualification': 'the qualification run', 'verify': 'the verify run', 'repeat': 'the repeat run'}
#: Why a review set a critical failure aside, by the red-cell audit's typed cause: the check was wrong, or the test
#: setup was. Another cause is said without a why.
RELEASE_WHY = {**dict.fromkeys(('rubric', 'type', 'prompt', 'target_clause'), 'the check misjudged it'),
               **dict.fromkeys(('scenario', 'harness', 'env', 'scan_fact'), 'the test setup caused it')}


def run_scores(out: Path | None) -> dict | None:
    """The run's scores block (`run_summary.json` → `scores`, schema :data:`SCORES_SCHEMA`), or None: no run dir, no
    summary, no block, or one this version cannot read (said on stderr)."""
    if out is None:
        return None
    try:
        doc = json.loads((Path(out) / 'run_summary.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    block = doc.get(SCORES_KEY) if isinstance(doc, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict) or block.get('schema') != SCORES_SCHEMA:
        print('release verdict: run_summary.json carries a scores block this version cannot read — no Agent line', file=sys.stderr)
        return None
    return block


def _score_text(score: object, cutoff: object) -> str:
    """A score as the customer reads it (one decimal), never shown at or above the cutoff it fell below."""
    value = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
    if value is None:
        return '—'
    if isinstance(cutoff, (int, float)) and value < cutoff and round(value, 1) >= cutoff:
        value = int(value * 10) / 10  # display only: 84.96 is said as 84.9, never as the 85.0 it missed
    return f'{value:.1f}'


def _plural(n: int) -> str:
    return '' if n == 1 else 's'


def agent_line(scores: dict | None, display: dict[str, str], *, stage: str | None) -> str | None:
    """`Agent: Ship / Don't ship / No verdict — <reasons>`, said off the scores block's `agent` (and its suite score and
    cutoff), never computed: the scenarios the agent's own raise decided named first (`agent_crashed`), the blocking
    critical failures and reproduced high-severity checks (`blocking_high`) named, the released ones listed, the conversations the block
    states `agent_error` named (:data:`CRASHED_STATUS`), failed required checks and undecided goals as notes that do not block, the unscored scenarios named for No verdict — and for a Don't ship
    they cannot change, with the block's best case when it is the score that decides. Each eval is said by its display
    name (`display`: :func:`policy_display_names`), its key only where it has none. None without a block.

    `stage` is the stage of the run the block was read off. Only the final scored run's verdict is the agent's: any
    other stage, or none known, heads the line with the run it came from (`FROM_RUN`) and its scenario count, and ends
    it by saying the final run gives the verdict, so a hand-off said before the final run never stands as the agent's
    verdict when that run stops early."""
    agent = scores.get('agent') if isinstance(scores, dict) else None
    if not isinstance(agent, dict) or agent.get('verdict') not in AGENT_WORDS:
        return None
    verdict, reasons = agent['verdict'], [r for r in agent.get('reasons') or [] if isinstance(r, str)]
    suite = scores.get('suite') if isinstance(scores.get('suite'), dict) else {}
    cutoff = scores.get('cutoff')
    score = _score_text(suite.get('score'), cutoff)
    rows = [c for s in (scores.get('scenarios') or {}).values() if isinstance(s, dict) for c in s.get('conversations') or [] if isinstance(c, dict)]
    trials = len({c.get('trial') for c in rows}) > 1

    def at(row: dict) -> str:
        trial = row.get('trial') if trials and isinstance(row.get('trial'), int) else None
        return _said(row.get('scenario'), CUSTOMER) + (f', trial {trial}' if trial is not None else '')

    def label(row: dict) -> str:
        key = _one_line(row.get('eval'))
        return _one_line(display[key]) if _is_name(key, display.get(key)) else key

    def named(rows: list) -> str:
        return '; '.join(f'{label(r)} ({at(r)})' for r in rows if isinstance(r, dict))

    blocking = [r for r in agent.get('blocking_critical') or [] if isinstance(r, dict)]
    high = [r for r in agent.get('blocking_high') or [] if isinstance(r, dict)]
    crashed_in = [str(n) for n in agent.get('crashed_scenarios') or []] if 'agent_crashed' in reasons else []
    unscored = [str(n) for n in agent.get('unscored_scenarios') or []]
    names, those = _names(unscored, CUSTOMER), 'it' if len(unscored) == 1 else 'those scenarios'
    rest = [n for n in unscored if n not in crashed_in]  # a crashed scenario is said by the crash lead, not again here
    decided = AGENT_TEXT['unscored_decided'].format(names=_names(rest, CUSTOMER)) if verdict == 'dont_ship' and rest else None
    best = agent.get('best_case_score')
    best = best if isinstance(best, (int, float)) and not isinstance(best, bool) else None
    if crashed_in:
        lead = AGENT_TEXT['agent_crashed'].format(names=_names(crashed_in, CUSTOMER))
    elif verdict == 'no_verdict':
        if 'nothing_scored' in reasons:
            lead = AGENT_TEXT['nothing_scored']
        else:
            lead = AGENT_TEXT['unscored_scenarios'].format(names=names, those=those)
    elif decided and 'best_case_below_cutoff' in reasons:  # the SDK states this reason only beside `best_case_score`
        measured = suite.get('score')
        ran = AGENT_TEXT['unscored_ran'].format(score=score) if best is not None and isinstance(measured, (int, float)) and measured < best else ''
        lead = AGENT_TEXT['unscored_below_cutoff'].format(names=names, those=those, best=_score_text(best, cutoff), ran=ran,
                                                         cutoff=f'{cutoff:g}' if isinstance(cutoff, (int, float)) else cutoff)
        decided = None  # said in the lead
    elif 'suite_below_cutoff' in reasons:
        lead = AGENT_TEXT['suite_below_cutoff'].format(score=score, cutoff=f'{cutoff:g}' if isinstance(cutoff, (int, float)) else cutoff)
    elif verdict == 'ship':
        lead = AGENT_TEXT['ship'].format(score=score)
    else:
        lead = None  # Don't ship on a critical failure alone: the failure leads, the score follows
    critical = AGENT_TEXT['critical_failure'].format(s=_plural(len(blocking)), names=named(blocking)) if blocking else None
    if high:
        times = {key: sum(label(r) == key for r in high) for key in dict.fromkeys(label(r) for r in high)}
        said = '; '.join(AGENT_TEXT['high_count'].format(name=key, n=n) for key, n in times.items())
        critical = ' '.join(filter(None, [critical, AGENT_TEXT['reproduced_high'].format(s=_plural(len(times)), names=said)]))
    if lead is None and critical is None:  # a block naming no reason: its score is all there is to say
        lead = AGENT_TEXT['suite_scored'].format(score=score)
    head = 'Agent'
    if stage != 'final':
        run, counted = FROM_RUN.get(stage or '', 'an earlier run'), len((scores or {}).get('scenarios') or {})
        head = (AGENT_TEXT['from_run'].format(run=run, n=counted, s=_plural(counted)) if counted
                else AGENT_TEXT['from_run_uncounted'].format(run=run))
    if lead is None:
        sentences = [f'{head}: {AGENT_WORDS[verdict]} — {critical}', AGENT_TEXT['suite_scored'].format(score=score)]
    else:
        sentences = [f'{head}: {AGENT_WORDS[verdict]} — {lead}', *([critical] if critical else [])]
        if crashed_in and suite.get('score') is not None:
            sentences.append(AGENT_TEXT['suite_scored'].format(score=score))
    if decided:
        sentences.append(decided)
    released = [r for r in agent.get('released_critical') or [] if isinstance(r, dict)]
    if released:
        said = '; '.join(f'{label(r)} ({at(r)})' + (f' — {RELEASE_WHY[r["cause"]]}' if r.get('cause') in RELEASE_WHY else '')
                         for r in released)
        sentences.append(AGENT_TEXT['released'].format(names=said))
    # The block's own `agent_error` status, named off each scenario's key (a conversation row carries no scenario name).
    crashed = [{'scenario': sid, 'trial': c.get('trial')} for sid, s in (scores.get('scenarios') or {}).items() if isinstance(s, dict)
               and sid not in crashed_in for c in s.get('conversations') or [] if isinstance(c, dict) and c.get('status') == CRASHED_STATUS]
    if crashed:
        sentences.append(AGENT_TEXT['crashed'].format(n=len(crashed), s=_plural(len(crashed)), names='; '.join(at(r) for r in crashed)))
    notes = []
    held = agent.get('non_blocking') if isinstance(agent.get('non_blocking'), dict) else {}
    failing = [r for r in held.get('failing_tier') or [] if isinstance(r, dict)]
    if failing:
        conversations = len({(r.get('scenario'), r.get('trial')) for r in failing})
        notes.append(AGENT_TEXT['failing_tier'].format(n=conversations, s=_plural(conversations),
                                                       names=', '.join(dict.fromkeys(label(r) for r in failing))))
    undecided = held.get('goal_undecided')
    if isinstance(undecided, int) and not isinstance(undecided, bool) and undecided > 0:
        replays = held.get('replay_turn_only')
        replays = replays if isinstance(replays, int) and not isinstance(replays, bool) and 0 < replays <= undecided else 0
        note = AGENT_TEXT['goal_undecided'].format(n=undecided, s=_plural(undecided))
        if replays == 1:
            note += '; ' + AGENT_TEXT['replay_turn_only_one']
        elif replays:
            note += '; ' + AGENT_TEXT['replay_turn_only'].format(k=replays)
        notes.append(note)
    if notes:
        sentences.append(AGENT_TEXT['not_blocking'].format(notes='; '.join(notes)))
    if stage != 'final':
        sentences.append(AGENT_TEXT['final_gives_verdict'])
    return ' '.join(sentences)


def head_lines(verdict: dict) -> list[str]:
    """What `analytics.md` carries at its head, in order and each its own line: `Suite: <rung>` (when a rung is
    licensed), the Agent line (when the run has a scores block), then the customer banner."""
    return [line for line in (verdict.get('rung'), verdict.get('agent_line'), verdict.get('customer_banner')) if isinstance(line, str) and line]


#: `release_verdict.json`'s schema id, and its keys — exactly these, every one always present.
VERDICT_SCHEMA = 'plurai-release-verdict/1'
VERDICT_FILE = 'release_verdict.json'
VERDICT_KEYS = ('schema', 'stage', 'rung', 'banner', 'customer_banner', 'typed', 'older_gate', 'blocking', 'remedy', 'block_causes',
                'named_risks', 'deterministic_losses', 'provisional', 'audit', 'audit_sha', 'suite_defects', 'agent', 'agent_line')
#: The audit state a landed FINAL verdict needs (`red_cell_audit.state`).
AUDIT_LANDED = 'uploaded'
#: One `named_risks[]` entry's keys, and one `observed_reds` object's.
RISK_KEYS = ('policy', 'display_name', 'kind', 'verdict', 'catastrophic', 'advisory', 'observed_reds')
OBSERVED_REDS_KEYS = ('counted', 'cells', 'not_counted')


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _risk_entry(risk: object) -> dict:
    """One named risk as `release_verdict.json` carries it: the typed fields only (`detail` is prose, never
    copied), an absent or mistyped value as null. A bare string entry is a display name, as :func:`banner` reads it."""
    if not isinstance(risk, dict):
        risk = {'display_name': risk}
    reds = risk.get('observed_reds')
    observed = None
    if isinstance(reds, dict):
        observed = {'counted': _int_or_none(reds.get('counted')), 'cells': _int_or_none(reds.get('cells')),
                    'not_counted': reds.get('not_counted') if isinstance(reds.get('not_counted'), dict) else None}
    display = risk.get('display_name')
    return {'policy': _str_or_none(risk.get('policy')), 'display_name': None if display is None else str(display),
            'kind': _str_or_none(risk.get('kind')), 'verdict': _str_or_none(risk.get('verdict')),
            'catastrophic': risk.get('catastrophic') if isinstance(risk.get('catastrophic'), bool) else None,
            'advisory': _str_or_none(risk.get('advisory')), 'observed_reds': observed}


def verdict_object(gate: dict | None, report: dict | None = None, *, stage: str | None = None, audit: str | None = None,
                   audit_sha: str | None = None, scores: dict | None = None, names: dict[str, str] | None = None) -> dict:
    """The release verdict as ONE typed object — what `release_verdict.json` holds and :func:`banner` reads.

    Pure: `gate` is :func:`report_gate`'s reading of `report`, and `stage` is the run's own stage
    (`run.json` `stage` of the run dir; None when unknown). Every key in :data:`VERDICT_KEYS` is always
    present; an absent or mistyped value is null, never a missing key. A list the gate does not carry
    (`block_causes`, or `named_risks` on a gate that is not :func:`typed`) is null, never `[]`: `[]`
    says the backend typed the list and it is empty. `rung` is null unless the run is a FINAL run and
    the gate a final-stage one — the suite rung is licensed by nothing else.

    `audit` is the run's red-cell audit state (`red_cell_audit.state`; None: not read). A FINAL run whose audit is not
    :data:`AUDIT_LANDED` is `provisional`, and both banners lead with :data:`PROVISIONAL_TEXT` (:func:`_provisional`).
    `audit_sha` is the sha256 of the audit file it was computed over (None: none was there); :func:`stale` compares it.
    `suite_defects` is the gate's discount decisions (:func:`suite_defect_decisions`), null when none was decided.
    `agent` is the run's scores block's agent verdict (`scores`: :func:`run_scores`), copied, and `agent_line` the
    customer's line for it (:func:`agent_line`, each eval by its display name in `names`) — both null without a block.
    Neither depends on the gate.
    """
    agent = {'agent': scores.get('agent') if isinstance(scores, dict) and isinstance(scores.get('agent'), dict) else None,
             'agent_line': agent_line(scores, names or {}, stage=stage)}
    if not isinstance(gate, dict):
        return _provisional({'schema': VERDICT_SCHEMA, 'stage': stage, 'rung': None, 'banner': None, 'customer_banner': None, 'typed': False,
                             'older_gate': None, 'blocking': None, 'remedy': None, 'block_causes': None, 'named_risks': None,
                             'deterministic_losses': None, 'suite_defects': None, 'audit_sha': audit_sha, **agent}, audit)
    causes = gate.get('block_causes')
    risks = gate.get('named_risks')
    return _provisional({
        'schema': VERDICT_SCHEMA,
        'stage': stage,
        'rung': rung(gate) if stage == 'final' and gate.get('stage') == 'final' else None,
        'banner': _banner_text(gate, report),
        'customer_banner': _banner_text(gate, report, CUSTOMER),
        'typed': typed(gate),
        'older_gate': _str_or_none(gate.get(OLDER_GATE)),
        'blocking': gate.get('blocking') if isinstance(gate.get('blocking'), bool) else None,
        'remedy': _str_or_none(gate.get('remedy')),
        'block_causes': [{'check': None if c.get('check') is None else str(c.get('check')), 'cause': _str_or_none(c.get('cause'))}
                         for c in causes if isinstance(c, dict)] if isinstance(causes, list) else None,
        'named_risks': [_risk_entry(r) for r in gate_risks(gate)] if typed(gate) and isinstance(risks, list) else None,
        'deterministic_losses': deterministic_losses(gate, report),
        'suite_defects': backend_decisions(gate),
        'audit_sha': audit_sha,
        **agent,
    }, audit, gate.get('provisional') is True)


def _provisional(verdict: dict, audit: str | None, served: bool = False) -> dict:
    """`verdict` with `provisional` / `audit` set: a FINAL run's verdict short of a landed audit — its own (`audit`) or the
    one the gate was computed on (`served`: `release_gate.provisional`) — leads each banner with :data:`PROVISIONAL_TEXT`:
    said, never a silent final verdict on unaudited reds."""
    provisional = verdict['stage'] == 'final' and (served or (audit is not None and audit != AUDIT_LANDED))
    verdict = {**verdict, 'provisional': provisional, 'audit': audit}
    if provisional:
        verdict['rung'] = None  # no rung is licensed by a verdict that has not landed
        state = (audit or 'not read by the gate').removeprefix('audit ').replace('_', ' ')
        internal, customer = PROVISIONAL_TEXT[0].format(state=state), PROVISIONAL_TEXT[1]
        verdict['banner'] = ' '.join(filter(None, [internal, verdict['banner']]))
        verdict['customer_banner'] = ' '.join(filter(None, [customer, verdict['customer_banner']]))
    return verdict


def _banner_text(gate: dict | None, report: dict | None = None, audience: str = INTERNAL) -> str | None:
    """The banner sentence(s) for `gate` — :func:`verdict_object`'s `banner` (the full reading, `INTERNAL`) or
    `customer_banner` (`CUSTOMER`: :data:`CUSTOMER` / :data:`INTERNAL`), never called by a reader directly."""
    if not gate or gate.get('stage') != 'final':
        return None
    if gate.get('blocking'):
        if not typed(gate):
            return REMEDIES['rebuild_suite']
        lost, blips, repeats = deterministic_losses(gate, report), transient_losses(gate), repeated_losses(gate)
        unloadable = unloadable_losses(gate)
        if lost or blips or repeats or unloadable:
            said = {*lost, *blips, *repeats, *unloadable}
            rest = {**gate, 'block_causes': [c for c in gate.get('block_causes') or []
                                             if not (isinstance(c, dict) and c.get('cause') == 'run_incomplete' and str(c.get('check')) in said)]}
            others = _by_cause(rest, audience) if rest['block_causes'] else None
            if not others and gate.get('remedy') == 'replant_premise':
                others = REMEDIES['replant_premise']  # one cycle repairs both: the env, then the premise
            return ' '.join(filter(None, [_unloadable_sentence(unloadable, gate, audience) if unloadable else None,
                                          _loss_sentence(FIX_ENV_TEXT, lost, report, audience) if lost else None,
                                          _loss_sentence(TRANSIENT_LOSS_TEXT, blips, report, audience) if blips else None,
                                          _loss_sentence(REPEATED_LOSS_CUSTOMER_TEXT if audience == CUSTOMER else REPEATED_LOSS_TEXT,
                                                         repeats, report, audience) if repeats else None, others]))
        return _by_cause(gate, audience) or REMEDIES.get(gate.get('remedy')) or REMEDIES[None]
    shared = [shared_world_sentence(r) for r in gate.get(RUN_RISKS) or [] if isinstance(r, dict) and r.get('kind') == 'shared_world']
    if audience == INTERNAL:
        shared += [INTERNAL_RUN_RISKS[r['kind']](r) for r in gate.get(RUN_RISKS) or [] if isinstance(r, dict) and r.get('kind') in INTERNAL_RUN_RISKS]
    shared += [s for r in gate.get(RUN_RISKS) or [] if isinstance(r, dict) and r.get('kind') == MOCK_GAP_KIND and (s := read_back_sentence(r))]
    if not typed(gate):
        return ' '.join(shared) or None
    all_risks = [r if isinstance(r, dict) else {'display_name': r} for r in gate['named_risks']]
    # "No result either way" is said off this run's scored verdicts: a check that decided a cell here has a result.
    all_risks = [r for r in all_risks if not (_key(r) == 'untested_premise' and _decided(gate, r))]
    contested = [r for r in all_risks if _rung_neutral(r)]
    untested = [(r, said) for r in all_risks if _key(r) == 'untested_premise' and (said := _untested_sentences(r))]
    unsaid = {id(r) for r, _ in untested}
    shared += [shared_world_sentence(r) for r in all_risks if r.get('kind') == 'shared_world']
    risks = [r for r in all_risks if not _rung_neutral(r) and id(r) not in unsaid and r.get('kind') != 'shared_world']
    unscored = _unscored(gate, all_risks)
    if audience == CUSTOMER:
        # Only a failure this run recorded on a check is about the agent; the rest is the suite's own standing.
        risks, untested, unscored = [r for r in risks if _observed_tally(r)], [], []
    if not (risks or contested or untested or unscored):
        return ' '.join(shared) or None
    lead = [_lead(r) for r in risks if r.get('catastrophic')] + [s for r, said in untested if r.get('catastrophic') for s in said]
    lead += [CONTESTED_TEXT[0].format(_contested_name(r, audience)) for r in contested if r.get('catastrophic')]
    leaned = [_contested_name(r, audience) for r in contested if not r.get('catastrophic')]
    failed = [f'{_name(r)} ({tally} scored conversations)' for r in risks if not r.get('catastrophic') and (tally := _observed_tally(r))]
    rest = [RISK_TEXT[key][1].format(', '.join(names)) for key in RISK_TEXT
            if (names := [_name(r) for r in risks if not r.get('catastrophic') and not _observed_tally(r) and _key(r) == key])]
    unset = [s for r, said in untested if not r.get('catastrophic') for s in said]
    unset += [UNSCORED_TEXT.format(', '.join(unscored))] if unscored else []
    return ' '.join([*shared, *lead, *unset, *([CONTESTED_TEXT[1].format(', '.join(leaned))] if leaned else []),
                     *([OBSERVED_FAIL_TEXT[1].format(', '.join(failed))] if failed else []), *rest, OTHERS_STAND])


def shared_world_sentence(risk: dict) -> str:
    """A `shared_world` risk said with the count of its typed `live_writers` (never its prose `detail`)."""
    writers = risk.get('live_writers')
    return SHARED_WORLD_TEXT.format(n=len(writers)) if isinstance(writers, list) else SHARED_WORLD_TEXT.replace('{n} ', '')


def _untested_sentences(risk: dict) -> list[str]:
    """One `UNTESTED_TEXT` sentence per readable entry of an `untested_premise` risk's typed `untested` list.

    An entry needs a `complication`; `planted` > 0 below `demanded` is said as "tested on k of n", anything else as not
    set up at all. None readable -> `[]`, and the risk falls back to its `RISK_TEXT` row."""
    check = f'critical safety check {_name(risk)}' if risk.get('catastrophic') else _name(risk)
    out = []
    for cell in risk.get('untested') or []:
        if not isinstance(cell, dict) or not isinstance(cell.get('complication'), str) or not cell['complication'].strip():
            continue
        situation = _one_line(cell['complication'].replace('_', ' '))
        k, n = _int_or_none(cell.get('planted')), _int_or_none(cell.get('demanded'))
        if k is not None and n is not None and 0 < k < n:
            out.append(UNTESTED_TEXT[1].format(check=check, situation=situation, k=k, n=n))
        else:
            out.append(UNTESTED_TEXT[0].format(check=check, situation=situation))
    return out


def _contested_name(risk: dict, audience: str = INTERNAL) -> str:
    """A `contested_violation` risk's name with where it leaned: `name (scenario, trial N; …)`. How the check's votes
    split is never said: a banner names the conversation to read, not the judging behind it.

    Read off the typed `contested` list; a malformed entry says what it can, and a risk with none is its name alone."""
    where = []
    for cell in risk.get('contested') or []:
        if not isinstance(cell, dict) or not isinstance(cell.get('scenario'), str):
            continue
        trial = _int_or_none(cell.get('trial'))
        where.append(_said(cell['scenario'], audience) + (f', trial {trial}' if trial is not None else ''))
    return f'{_name(risk)} ({"; ".join(where)})' if where else _name(risk)


def _by_cause(gate: dict, audience: str = INTERNAL) -> str | None:
    """One `CAUSE_TEXT` sentence per cause in `block_causes`, naming its checks — None when the gate types
    no cause, calls for a rebuild, or names a cause this version does not know."""
    causes = [c for c in gate.get('block_causes') or [] if isinstance(c, dict)]
    if not causes or gate.get('remedy') == 'rebuild_suite' or any(c.get('cause') not in CAUSE_TEXT for c in causes):
        return None
    grouped: dict[str, list[str]] = {}
    for entry in causes:
        grouped.setdefault(entry['cause'], []).append(_said(entry.get('check') or 'an unnamed check', audience))
    return ' '.join(CAUSE_TEXT[cause].format(', '.join(dict.fromkeys(names))) for cause, names in grouped.items())


def _lead(risk: dict) -> str:
    """The lead sentence for a catastrophic `risk`: a failure this run recorded on it (`OBSERVED_FAIL_TEXT`) first,
    else its typed `advisory`'s (`ADVISORY_TEXT`), else its `RISK_TEXT` row's."""
    if tally := _observed_tally(risk):
        return OBSERVED_FAIL_TEXT[0].format(_name(risk), tally=tally)
    advisory = risk.get('advisory')
    text = ADVISORY_TEXT.get(advisory, RISK_TEXT[_key(risk)][0]) if isinstance(advisory, str) else RISK_TEXT[_key(risk)][0]
    return text.format(_name(risk))


def _observed_tally(risk: dict) -> str | None:
    """The "k of N" tally of a kept `never_fails` risk whose typed `observed_reds.counted`, less the reds a
    correction cleared (:data:`CORRECTED_REDS`), is positive, else None.

    Only `counted` decides: a red the backend filed against the check or the simulated tools (`not_counted`)
    is not a failure by the agent. A malformed `cells` still says the failure, without the total."""
    reds = risk.get('observed_reds')
    if _key(risk) != 'never_fails' or not isinstance(reds, dict):
        return None
    counted, cells = reds.get('counted'), reds.get('cells')
    if not isinstance(counted, int) or isinstance(counted, bool):
        return None
    counted -= _int_or_none(risk.get(CORRECTED_REDS)) or 0
    if counted <= 0:
        return None
    return f'{counted} of {cells}' if isinstance(cells, int) and not isinstance(cells, bool) and cells >= counted else f'{counted} of its'


def _key(risk: dict) -> str | None:
    """The `RISK_TEXT` row for `risk`: an unproven one's typed verdict, else its kind."""
    kind = risk.get('kind')
    if kind == 'unproven':
        return risk.get('verdict') if risk.get('verdict') in UNPROVEN_VERDICTS else 'unproven'
    return kind if kind in ('coverage_limit', 'suite_defect', 'untested_premise', 'replay_carried') else None


def _name(risk: dict) -> str:
    """The risk's name on ONE line — a line break would split the banner out of its own block."""
    return ' '.join(str(risk.get('display_name') or risk.get('policy') or 'an unnamed check').split())


def read_report(path: Path) -> tuple[dict | None, int]:
    """`(the report, exit code)` from a saved report or job envelope.

    :data:`EXIT_UNREADABLE`, :data:`EXIT_NOT_DONE` (still `queued`/`running` — its `release_gate` is
    not decided yet), :data:`EXIT_JOB_FAILED` (ended without `done` — no release gate will come).
    """
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, EXIT_UNREADABLE
    if isinstance(doc, dict) and 'status' in doc and doc['status'] != 'done':
        return None, EXIT_NOT_DONE if doc['status'] in ('queued', 'running') else EXIT_JOB_FAILED
    report = doc.get('detail') if isinstance(doc, dict) and isinstance(doc.get('detail'), dict) else doc
    if not isinstance(report, dict):
        return None, EXIT_UNREADABLE
    return report, 0


def read_gate(path: Path) -> tuple[dict | None, int]:
    """`(the gate to read, exit code)` — :func:`read_report`, then :func:`report_gate`'s reading, the same
    one the suite rung takes."""
    report, rc = read_report(path)
    return (report_gate(report) if report is not None else None), rc


def _trials_block(out: Path) -> dict:
    """The run's composed `run_summary.json` → `trials` block (the SDK's), or `{}`."""
    try:
        trials = json.loads((out / 'run_summary.json').read_text(encoding='utf-8')).get('trials')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        trials = None
    return trials if isinstance(trials, dict) else {}


def _trial_dirs(out: Path) -> list[Path]:
    listed = _trials_block(out).get('dirs')
    subs = [d for d in listed or [] if isinstance(d, str) and d and _TRIAL_DIR.fullmatch(d)]
    return [out, *(out / d for d in subs)]


def prepend(analytics: Path, line: str | list[str] | None) -> None:
    """Replace any earlier banner at the head of `analytics` with `line` — or with several lines (:func:`head_lines`),
    each its own quoted paragraph so Markdown never runs them together — or with none."""
    text = _BANNER_BLOCK.sub('', analytics.read_text(encoding='utf-8'), count=1).removeprefix(LEGACY_BANNER)
    lines = [line] if isinstance(line, str) else [entry for entry in line or [] if entry]
    head = f'{MARKER}\n' + '\n>\n'.join(f'> **{entry}**' for entry in lines) + '\n\n' if lines else ''
    analytics.write_text(head + text, encoding='utf-8')


#: The run stages `run.json` `stage` may carry (`build_run_json.RUN_STAGES`); anything else reads as unknown.
RUN_STAGES = ('qualification', 'final', 'verify', 'repeat')


def run_stage(out: Path) -> str | None:
    """The run dir's own stage, off its `run.json` — None when the file, the key or a known value is missing."""
    try:
        stage = json.loads((out / 'run.json').read_text(encoding='utf-8')).get('stage')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    return stage if stage in RUN_STAGES else None


def write_verdict(directory: Path, verdict: dict) -> None:
    """`release_verdict.json` in `directory`, tmp+replace: a reader never sees half a verdict."""
    path = directory / VERDICT_FILE
    tmp = path.with_name(f'.{VERDICT_FILE}.tmp')
    tmp.write_text(json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def remove_verdicts(out: Path) -> list[str]:
    """Remove every older verdict — `release_verdict.json` and the `analytics.md` banner — from the run dir and
    its trial dirs. Returns what was removed, run-dir-relative, for the stderr line."""
    removed = []
    for trial in _trial_dirs(out):
        where = trial.relative_to(out).as_posix()
        stale = trial / VERDICT_FILE
        if stale.is_file():
            stale.unlink()
            removed.append(f'{where}/{VERDICT_FILE}' if where != '.' else VERDICT_FILE)
        analytics = trial / 'analytics.md'
        if analytics.is_file():
            before = analytics.read_text(encoding='utf-8')
            prepend(analytics, None)
            if analytics.read_text(encoding='utf-8') != before:
                removed.append(f'{where}/analytics.md banner' if where != '.' else 'analytics.md banner')
    return removed


def corrected_reds(out: Path) -> dict[str, int]:
    """`{policy: conversations whose red a final-run correction cleared}` over every item of the run dir —
    `build_run_json.corrected_reds`; an unreadable verdicts.json clears none (said on stderr)."""
    here = Path(__file__).resolve().parent
    sys.path[:0] = [str(here), str(here.parent / 'skills' / 'simulation-run' / 'scripts')]
    import build_run_json  # noqa: PLC0415 - sibling scripts, needed only by render
    import red_cells  # noqa: PLC0415
    try:
        return build_run_json.corrected_reds(d for _, d in red_cells.run_items(out))
    except (OSError, ValueError) as exc:
        print(f'release verdict: corrected verdicts not read ({exc}) — the banner counts the run\'s original reds', file=sys.stderr)
        return {}


def run_aborted(out: Path | None) -> dict | None:
    """The `aborted` block of the run dir's `run_summary.json` (`reason`, `cause`, `remedy`, ...), or None for a run that finished."""
    if out is None:
        return None
    try:
        block = json.loads((out / 'run_summary.json').read_text(encoding='utf-8')).get('aborted')
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    return block if isinstance(block, dict) and block else None


def cmd_render(args: argparse.Namespace) -> int:
    if (aborted := run_aborted(Path(args.out) if args.out else None)) is not None:
        # A stopped run has no verdict, and an older one beside it would be read as this run's.
        removed = remove_verdicts(Path(args.out))
        print(f'release verdict: the runner stopped this run ({": ".join(str(aborted[k]) for k in ("reason", "cause") if aborted.get(k))}), so no release decision exists; '
              f'{"removed the older verdict (" + ", ".join(removed) + ")" if removed else "no older verdict was there to remove"}. '
              f'{aborted.get("remedy") or ""}'.rstrip(), file=sys.stderr)
        return EXIT_RUN_ABORTED
    report, rc = read_report(Path(args.report))
    out = Path(args.out) if args.out else None
    gate = report_gate(report, corrected_reds(out) if out else None, run_risks(out) if out else None) if report is not None else None
    gate = with_run_losses(with_loop_risks(gate, out), run_loss_classes(out)) if out is not None else gate
    if isinstance(gate, dict) and out is not None:
        gate = {**gate, LOAD_REFUSALS: load_refusals(out)}  # the reason an `unloadable` loss's banner quotes
        # A released tooling blocker is the suite's own standing: said in the internal banner (INTERNAL_RUN_RISKS), one per entry.
        if tooling := tooling_risks(out):
            gate = {**gate, RUN_RISKS: [*(gate.get(RUN_RISKS) or []), *tooling]}
            for risk in tooling:
                print(f'release verdict: {tooling_risk_sentence(risk)}', file=sys.stderr)
    gate = with_scored_cells(gate, scored_cells(out)) if out is not None else gate
    for d in (backend_decisions(gate) or []) if gate is not None else []:
        if not d['discounted']:
            print(f'release verdict: not discounted — {d["policy"]}: {d["because"]} ({d["true_red"]} ruled true, {d["false_red"]} false)', file=sys.stderr)
    if rc == EXIT_UNREADABLE:
        print(f'release verdict: {args.report} could not be read — no banner rendered', file=sys.stderr)
        return EXIT_UNREADABLE
    if rc in (EXIT_NOT_DONE, EXIT_JOB_FAILED):
        # No verdict exists for this run yet, so none may stand beside it: an older one would be read as this run's.
        removed = remove_verdicts(Path(args.out)) if args.out else []
        cleared = f'removed the older verdict ({", ".join(removed)})' if removed else 'no older verdict was there to remove'
        if rc == EXIT_NOT_DONE:
            print(f'release verdict: the job in {args.report} is not done — poll it again, then render; {cleared}', file=sys.stderr)
        else:
            print(f'release verdict: the job in {args.report} failed, so no release decision exists — the release gate did not run; {cleared}',
                  file=sys.stderr)
        return rc
    if gate is None:
        print('release verdict: none (the report reached no release decision)', file=sys.stderr)
    elif not typed(gate):
        print('release verdict: NOT typed (this backend names no risks or remedy) — read as before', file=sys.stderr)
    stage = run_stage(out) if out is not None else None
    # The audit the gate read (`release_gate.audit_sha`, C8); an older backend names none, so the local file's sha stands in.
    read = gate['audit_sha'] if isinstance(gate, dict) and 'audit_sha' in gate else audit_sha(out) if out is not None else None
    verdict = verdict_object(gate, report, stage=stage, audit=audit_state(out) if out is not None and stage == 'final' else None,
                             audit_sha=read, scores=run_scores(out), names=policy_display_names(run_workdir(out)))
    if verdict['provisional'] and isinstance(gate, dict) and gate.get('provisional'):
        print(f'release verdict: the release gate itself is provisional ({gate.get("provisional_cause")}): re-run suite_diagnostics '
              'once the audit is uploaded', file=sys.stderr)
    if verdict['provisional']:
        said = audit_said(out, verdict['audit']) if out is not None else verdict['audit']
        print(f'release verdict: PROVISIONAL — this final run\'s red-cell audit: {said}; the verdict lands once the audit '
              'is saved and uploaded (/simulation-run Step 4d), then render again', file=sys.stderr)
    if (gated := verdict['rung']) in (QUALIFIED, VALIDATED) and out is not None:
        # The gate licenses the rung; the workdir can still lower it (`validation_holds`), as on the anchor, so the run and
        # the hand-off say one rung. The suite's own pin rule is the anchor's: an official run arms no client pin.
        if out.parent.name == 'runs' and out.parent.parent.name == 'artifacts':
            verdict['rung'], moved = capped_rung(gated, validation_holds(out.parents[2], out, client_pin_required=False))
            for why in moved:
                print(f'release verdict: {verdict["rung"]}, not {gated.removeprefix("Suite: ")} — {why}', file=sys.stderr)
        else:
            print(f'release verdict: {out} is not under <workdir>/artifacts/runs, so what could hold `{gated.removeprefix("Suite: ")}` was not read',
                  file=sys.stderr)
    if verdict['rung'] == QUALIFIED and isinstance(gate, dict):
        for hold in gate.get(RUNG_HOLDS) or []:
            if isinstance(hold, dict):
                print(f'release verdict: qualified, not validated — the backend holds it ({hold.get("cause")}: {_one_line(hold.get("detail") or "")})',
                      file=sys.stderr)
    line = verdict['customer_banner']
    if out is not None:
        for trial in _trial_dirs(out):
            if (trial / 'analytics.md').is_file():
                prepend(trial / 'analytics.md', head_lines(verdict))
                write_verdict(trial, verdict)
            else:  # no report here for a verdict to sit beside — and an older one must not stay
                (trial / VERDICT_FILE).unlink(missing_ok=True)
    if line:
        print(line)
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    why = stale(Path(args.out))
    if why:
        reports = _receipt(Path(args.out)).get('stale_reports') or []
        via = f'release_verdict_recompute({", ".join(reports)})' if reports else 'release_verdict_recompute(<the suite_diagnostics id it came from>)'
        print(f'release verdict: STALE — {why}; {via} over the uploaded audit, then render again', file=sys.stderr)
        return EXIT_STALE
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    render = sub.add_parser('render', help='print the banner; write it, and release_verdict.json, beside every trial analytics.md')
    render.add_argument('--report', required=True, help='the saved suite_diagnostics job envelope (or bare report)')
    render.add_argument('--out', help='the run dir ($OUT); omitted, the banner is only printed and nothing is written or removed')
    render.set_defaults(func=cmd_render)
    check = sub.add_parser('stale', help=f'exit {EXIT_STALE} when the run\'s verdict was computed over another audit than the one beside it')
    check.add_argument('--out', required=True, help='the run dir ($OUT)')
    check.set_defaults(func=cmd_stale)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
