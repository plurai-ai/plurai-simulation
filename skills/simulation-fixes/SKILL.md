---
name: simulation-fixes
description: >-
  Diagnose a run's failures and write a ranked fix plan and fixes.md with evidence and code locations; never edits the agent. Records an accepted agent fix with /record-regression BEFORE applying the fix. Stage 5 of /simulate; run after /simulation-run. Optional --verify.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Fixes — diagnose → propose → (optionally) prove

**Usage:** `/simulation-fixes [--run <out-dir>] [--verify]`

You are the LOCAL Claude Code agent. This is **command 5 of 5**:

```
scan → traces → suite → run → fixes
 map     learn   build   score  repair
                                ▲ you are here
```

`/simulation-fixes` turns a run's failures into a **conservative, evidence-backed fix
plan**. Your focus here is the **agent's** failures. Read `suite_anchor.json`'s rung and this run's `release_gate`; a suite-side cause (`owning_layer` ≠ agent) goes to `/simulation-generate-suite`. You diagnose root cause,
point to the exact code, and propose the smallest safe change — you do **not** quietly
rewrite the agent.

> ## Be conservative — this is the core requirement
> - **Every claim needs evidence** quoted from the run (a transcript turn, a
>   tool_events entry, the error, the failing policy). No evidence → no recommendation;
>   say "needs investigation" and list what to check.
> - **Carry a confidence level** (high / medium / low) on every root-cause hypothesis.
>   Prefer "likely" over "is"; surface uncertainty rather than hide it.
> - **Separate the three failure homes** and never conflate them:
>   1. **Agent bug** — the agent ran but did the wrong thing → a real defect in the
>      customer's own source (outside `plurai_sim/`).
>   2. **Test-setup issue** — `simulate.py` stubs, `plurai.config.yaml`, the scenario
>      seed, or an over-strict / mis-calibrated eval → NOT the agent's fault.
>   3. **Mock-env issue** — remote: seeded data or a mock implementation on the
>      deployed env → the remote agent owns the fix (via the dual-agent protocol).
> - **Read the analysis before blaming the harness for a stall.** For a run with no scorable
>   trial, or an agent that never leaves its first tool step, read `/analyze-simulation`'s
>   output for this run first (its attribution, with the scenario-set and env ids). When it names
>   a Plurai-side cause, say so in the same words and do not home the stall to `simulate.py` or the
>   harness. When it cannot tell, say "cannot tell" and name the check that was not possible
>   instead of guessing.
> - **Smallest, reversible change.** Propose the minimal edit; never a refactor.
> - **Never auto-edit the agent's source.** Write the plan; apply an edit to the
>   customer's code ONLY after they explicitly accept that specific fix.
> - **Don't weaken the eval to match a phantom.** If a policy is green/red only because the
>   SCENARIO is incoherent (a precondition that doesn't exist in the agent's real ground truth,
>   seeded state its tools can't return), fix the scenario's GROUNDING — regenerate, or when
>   ONLY the grading GT is wrong against verified external truth (a wrong expected_answer,
>   stale evidence quotes) the targeted `edit_scenario_gt` tool
>   (the `edit-scenarios` skill) — do NOT loosen the eval to match a phantom. Loosening
>   an eval is legitimate ONLY when it is genuinely over-strict on a COHERENT scenario.
> - **Don't claim "fixed."** A fix is "proposed" until `--verify` actually re-runs it.

<!-- BEGIN error-posture: generated from ${CLAUDE_PLUGIN_ROOT}/docs/error-posture.md; edit there -->
## Error posture — findings are evidence, internal issues are yours to handle

Backend errors, warnings, diagnostics and gate findings are internal: never show the customer
raw backend output. Read the raw error or finding yourself, always.

**Gate findings are evidence; you decide.** On `gate_findings`, a refusal naming a `gate_id`, or an
overrule outcome of `stale` or `refused` on `gate_authority.overrules`, follow
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (read it the first time one appears). Never ask the
user about a finding or an overrule; never put either in a report.

**Any Plurai tool error.** On an error from a Plurai tool or a `plurai` command, run
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"` once, before anything else, adding `--close <flow>` when this
command's Step 0 armed a flow window. On exit 7, 8 or 3 it explains
the error: say what it printed as the rule for that exit code does (exit 7 the version refusal below, exit 8 the credit
refusal, exit 3 item 6), and never paraphrase the tool error. On exit 0 it does not explain the error: handle the error
with the items below.

**Version refusal.** The software is refused, not the project; nothing here is yours to fix. On exit 7 of
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"`, or of any plugin script that reaches Plurai (`sdk_call.py`,
`poll_job.sh`, `run_wait.sh`, `mcp_call.py`), what it printed is the refusal: stop, and say those lines verbatim and alone,
nothing before them, not even `exit 7`. Their last line is the update command, or the contact hand-off: add nothing after it. Close the flow window
this command's Step 0 armed first and silently: the check run with `--close <flow>` closed it in its own call; after
any other script run the `flow-window.sh close` line of its `NOT READY` stop. Never retry; never run `harness-setup`, ask the user to log in, reconnect or give a key. A later request in this
session gets the same lines: run the check again and say what it prints. Items 1, 2, 3 and 6 below do not apply.

**Credit refusal.** Neither a fault to fix nor a provider refusal. On a line `CREDIT-STOP: <sentence>`, a `plurai`
command that exits 13 on a credit sentence, or a credit block in a tool result or job envelope
(`terminal_reason: credit`, or `code` one of `insufficient-credit`, `new-run-refused`, `free-tier-ceiling`,
`credit-unavailable`, `credit-refused`): stop, whatever the snippet's own fallback says, and say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" refusal (--sentence "<the sentence>" | --refusal <the block's JSON>)
--continue "<the user's command>" --done "<outcome>"` prints, verbatim and alone (with `--refusal`, add
`--plurai "$PLURAI_BIN"` when you hold it). `<the user's command>` is the slash command the user typed to start this
flow (for example `/simulate`), or their request in words; never this skill's name. Exit 5 means it names a skill the
user cannot type: run again with the user's command. For `credit-unavailable` the command prints the contact hand-off with
no continue line: say it. The run stays halted until the user's next message lifts it, as for capacity.

Read `${CLAUDE_PLUGIN_ROOT}/docs/credit-refusal.md` the first time one appears, and when a tool reports a payment
refusal with no block. Say the stop once, then only what is new. When the stop has a continue line, name no other
command or skill, then or later: that line is the one way on. Never retry the refusal; never run
`harness-setup` or ask the user to log in again; never ask for a key; never name or suggest a model; never start
another job. Items 1, 2, 3 and 6 below do not apply.

**SDK install question.** When `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask` exits 3 and its stderr holds
`resolve_plurai: REFUSED` with `header:`, `question:` and `options:` lines, the SDK here is older than this plugin release
needs, or this project's environment holds none: ask the question once, exactly as printed, through the blocker gate. That call
sanctioned it, so the ask tool is your next action, with no text before it; if the lines say to run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` first, run it. On `Install it`, run the
`--install-pin` command the lines name, unchanged, tell the user its last line, then run the command that stopped again from
its start. On `Not now`, or when nobody can answer, close the window as the version refusal rule does, then say the stop
lines that follow the question, verbatim and alone, and run nothing after them. Stop lines with no
question are said the same way. Never run `harness-setup` for it, and never offer another install.

On any other backend error or warning:

1. **Fix it yourself, silently.** Retry a transient failure (retry rule below). When the error names something
   this side can change (a malformed parameter, a stale or missing local artifact, an input the skill's error table
   names, the corpus's shape), change it and re-run the call once with the changed inputs: that fixed re-run is item 1,
   not a retry. At most one fixed re-run per failed call, never a third run. Say no sentence about the fix, the re-run,
   the wait or the re-call, before or after it: the next text the user reads is the flow's normal output. A fixed re-run
   that fails on something the user must supply is item 2; on anything else, item 3.
2. **User-actionable input problem:** say what to change in product terms ("the PRD has no
   refund-policy section"), never the backend's error text, exception or internal field names.
   If the missing input blocks the flow (a provider key unset or expired, credentials, a service
   that stopped answering), ask; never degrade silently. Sanction the ask first with
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction <gate>` (never a raw write to a
   flow marker), ask once, at discovery, in product language, offer this skill's fallback, and
   resume on the answer. A sanction allows one ask call, within 10 minutes: put every question the
   gate needs into it, and never ask twice. Where you cannot ask the user at all, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" unasked <gate>` and never ask in prose.
   Where the ask is a plain-text question (no ask tool), ask one question with its options and end
   the turn on it: the next prompt is the reply. Where `request_user_input_async` is the ask tool,
   send exactly `{"questions":[{"title":"<the question>","options":["<option>","<option>"]}]}`
   and no other key (a call with an unknown key is rejected, and the sanction survives it). Never
   call `request_user_input`. A run with nobody at the terminal declares its run driver first
   (`workspace.run_driver: headless`) so the gate takes its default; an undeclared run ends on the
   question and waits for nobody.
   With `workspace.run_driver: headless`, sanction and ask the same way: the guard replies with the
   gate's default or files a `run_driver_input_required` finding for you to report. Never guess.
3. **Unfixable from here** (a misconfigured backend, a failure nothing on this side can change, a fixed re-run that
   failed again, a missing server-side capability, a `security` finding, an overrule outcome of `refused`): when the
   remedy is a grant or credential only the user holds, ask as in item 2. Otherwise stop on the contact hand-off: say
   what `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints (each `--done` an
   outcome that names what exists, with its id), verbatim and alone. The contact hand-off is for typed faults; a
   transport failure with no envelope ends on the unavailable stop (retry rule). A refusal typed `code: capacity_exhausted`, a job envelope whose
   `capacity` is set (any status), an `unavailable` block or a job envelope with `terminal_reason: unavailable` instead
   takes `stop_message.py refusal --refusal <its JSON>`; never quote the provider's own limit text. A typed block wins
   over the fault rule and over `terminal_reason`: a failed job with `terminal_reason: internal_error` that holds an
   `unavailable` block is the unavailable stop, never the contact hand-off.

4. **Cost figures and backend internals stay internal.** `total_cost_usd` and every backend spend
   figure stay out of everything the customer sees, the final message included. So do the
   backend's internal ports (a mock service's loopback port), process ids and filesystem paths;
   a deployed mock's only address is its public `https://<host>/mock/<env_id>/` URL.
5. **Internal quality-loop work is process, not product.** Defects found and fixed during
   generation or qualification stay out of the report.
6. **The endpoint is the configured backend.** The backend is the one the user logged in to with the
   `harness-setup` skill; a project `.env` file is not read. If no backend tool is in your tool list, or a
   call is refused because Plurai is not logged in or not set up, run the `harness-setup` skill from the
   command the user typed (`from /<command>`), then go on from the step you were at. Never show the
   refusal's text. Never tell the user to set, export or unset an environment variable: that is for developers
   of the plugin, never for its users. Never switch to another address to get unblocked, and never send the
   token anywhere but the configured backend.

**Before a stop, say nothing about it.** Say nothing about the error or its cause before a stop (no "credit",
"capacity", "503" or "error" wording, no apology, no status line): the stop text alone says it. Its "Done so far" lines
name what exists, with its id (for example "Scenario set saved: <name>"), never the stop or the failure.

**A stop inside a /simulate run publishes the page once.** After a stop above (the credit refusal's line, the contact
hand-off, the capacity stop or the unavailable stop), run its `stop_message.py` command and say nothing yet. Then run the
end publish, silently:
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh" && source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh" &&
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step end --flowdir "$FLOWDIR"`. When
`publish` is true, publish the file at `report` with the Artifact tool, passing `report` as `file_path` character for
character, with `icon: "report"` and `url` set to `page` when it is not null (never `force`, never `Read` the file),
then run the same command with `done "$WORKDIR" --step end --flowdir "$FLOWDIR" --url "<the URL, or - when the publish
failed>"` in place of `fill …`. When `publish` is false, never fill again and never run `done`. Your final message, the
only one this stop has, is what the stop command printed, verbatim, then the link line `done` printed, or the `say`
lines of a fill that did not publish, if any. Say the stop nowhere else in the turn. Never run `flow-window.sh simulate
end` for the stop itself: the run is stopped, and no job starts until the user's next message. Outside a /simulate run the fill skips and adds
nothing. A typed message after a capacity or unavailable stop lifts it. A go-on continues the stage at once, without
repeating the stop text and never waiting out the time it names; a stop runs `flow-window.sh simulate end`; a question is
answered, then wait for the user.

**Retry rule — only a transient failure is retried, and only once.** Read the failed job's
`terminal_reason` first (Job envelope, beside `status` and `error`). Transient is exactly one of:
- A transport failure with no envelope (connection reset, client timeout, a reply cut in
  transit, a bare `502` / `503` / `504`). Wait first: run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 45 --quiet` as
  one foreground shell call, silently, and never narrate it. Then re-call a read (`job_status`, a fetch) or a `*_start`
  once, with the same arguments: the backend answers a repeated start with the first call's job or session. A refusal naming an already-open session or job from that
  call means the first call ran: continue with it. Never re-call any other call that writes (`interact_edit_session`,
  `end_edit_session`, `score_run`, `diagnose_mock`, `edit_scenario_gt`, …). A re-call that fails again, or a transport
  failure on a write that is never re-called, is the unavailable stop: say what
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" unavailable --done "<outcome>"` prints, verbatim and alone,
  never the contact hand-off. A `poll_job.sh` that exits 69 is this transport failure: run the wait above, re-invoke it
  once, and a second 69 is the same stop.
- `terminal_reason: interrupted` from a restart (not a cancel you or the user asked for), or
  `terminal_reason: watchdog` (the backend stopped a job that made no progress).
- A failure this skill's error table marks as retried once.

Everything else is final, whatever its `terminal_reason` (`refused_inputs`,
`usage_window_exhausted`, `capacity`, `unavailable`, `credit`, `cost_cap`, `internal_error`, or none),
including a provider timeout or 5xx (`504 Provider timed out`): the backend already retries those
inside the job. Final is about re-sending the same inputs; item 1's fixed re-run, with changed inputs, is not a retry.
A retried job that fails again is final, whatever its reason. Hand it off per step 3.

**Provider refusal.** `terminal_reason: refused_inputs` is an input gate (this skill's error table
says what to fix) or the provider refusing the backend's own call. Decide by the error's shape,
never by words anywhere in `error` (a flow named `credit_limit` is not a billing problem). It is a
provider refusal only when `error` contains `SDK turn failed (HTTP 4xx)` (at its start or wrapped,
as in `corpus build failed (RuntimeError: SDK turn failed (HTTP 403): …)`) with status 400 / 401 /
403 / 404 and provider text after the marker about access, permission, the model, the
account, credit or billing. Then stop on the contact text alone, with no lead-in: say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints, verbatim. No model name,
no retry, and never pass or suggest a `model`: the model is a backend decision. Provider text opening with
`AWS authentication failed · refresh your AWS credentials` is about the backend's credentials:
never repeat it and never ask the user to refresh anything.
<!-- END error-posture -->

This posture applies to **the backend infrastructure** noise (diagnostic-session/MCP failures, backend
job errors, mock-infrastructure faults). It does NOT restrict the evidence rules above: quoting the
CUSTOMER'S OWN run — their transcript turns, tool_events, their agent's errors — IS this command's
deliverable.

## Report doctrine — the final message presents the PRODUCT, not the build

Your final message is the product hand-off, and it follows the skill's report skeleton exactly:

1. **Lead with what was built and what it catches/does**, in the customer's own domain language —
   concrete behaviours, not adjectives, and never what it took to build. A situation the conversations put to the
   agent (a complication such as pressure after a refusal) is named only when `run_numbers.py covered <run.json>` lists
   it for the run you present (it prints `null`: name no situations as covered); checks and flows are named as before.
2. **One next action** — the single command or step the user takes from here.
3. **No SUITE-gap narration.** Accepted gaps, uncovered flows, and known limitations of the SUITE
   are not final-report content — the rendered artifacts (scenarios.md, evals.md, coverage charts)
   carry the full picture for a user who wants it. The report never lists what the suite does not
   cover. That line is about the SUITE, never about findings: the defects the suite found in the
   CUSTOMER'S AGENT are the deliverable of `/simulation-run` and `/simulation-fixes`, and a licensed line in
   `/simulation-generate-suite`'s and `/analyze-simulation`'s hand-offs — rank them,
   name them concretely in domain language, and quote the agent's own words as the evidence.
   `/simulation-scan-agent` caught nothing — it ran no scenarios — but it carries one licensed line
   for the POTENTIAL agent issues the scan recorded because they narrowed what the suite tests:
   named and counted, never ranked, and always marked as possibly intended.
4. **Run-INTEGRITY facts are RESULTS, not gap narration — ONE line, when they apply.** What was
   NOT executed, trials that did not run (the `run_integrity` line `run_numbers.py chat` prints, verbatim —
   never a self-counted `errored`), scenarios that landed INCOMPLETE on the
   per-scenario time budget (graded to the turn each reached, in neither the passed nor the failed
   count), a flow REMOVED because it cannot complete inside that budget, a model that was
   substituted or
   overridden, a regeneration that shipped short of its target count: these change how the numbers
   must be read, so the reader is owed them. State the fact flatly and move on — no cause, no
   repair story, no apology — e.g. `Run integrity — 2 of 27 trials could not run on our side.
   The results above cover the 25 that ran.` This is the ONLY thing item 3 lets in: what the suite does
   not COVER stays out. The removed-flow case is the one line that names a knob, because the
   removal is reversible and only the user can decide to reverse it: `Run integrity — flow <X> was
   removed from the suite: it cannot complete inside the 40-minute scenario time budget; extending
   the budget (--scenario-timeout) is your call.`
5. **The product's confidence in its own result is not report content.** How sure the suite, its
   evals or its checks are of a number — sample sizes, thresholds, audits, release checks — stays in
   `loop_log.md`; the reader gets what the result means and the one action that
   changes it (a suite that must be rebuilt: `/simulation-generate-suite`, then `/simulation-run`).
   One line is licensed against this item and item 3: `/simulation-generate-suite`'s "Evals to optimize —" line,
   pasted as `eval_rounds.py flagged-line` prints it (nothing printed: no line) — it names the evals flagged for
   improvement, never a tally of their results, a cause or a repair story.

Everything you found and fixed along the way is process, not content: no defect chronology, no
iteration/critic history, no internal warnings, no spend. Findings about the CUSTOMER'S AGENT are
the product's value — present them as what the suite caught, never as flaws of the suite.

### While the flow is still running — say nothing

Between the invocation and the hand-off the default is **silence**. The tool stream is the progress
indicator; the user cannot act on a build in flight, so commentary buys them nothing and risks
handing them the build's internal vocabulary. The phase you are in, the loop, a sub-agent's report,
what you assumed, what you got wrong, what you are re-checking — that is process, and process is
never narrated however well it is phrased. That includes a one-line note between tool calls ("Now
…", "Step 4: …", "Writing … now"): text written mid-turn reaches the user before any check can stop it.

Exactly four things may be said before the hand-off:

1. **A sanctioned gate or progress line** this skill names at that step, in its words. The page's progress line is
   one: after each milestone fill, and each time a sub-agent a step names returns, say what
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" "$WORKDIR" --flowdir "$FLOWDIR"` prints,
   verbatim (nothing printed: say nothing; the line the harness hands you when a sub-agent returns is that line, said
   once).
2. **A blocker you cannot clear from here** — error-posture items 2 and 3, in the words those items
   give you. The test is *the flow cannot continue*, not *something went wrong*: anything the
   skill's own recovery covers — a retry, a regeneration, a repair round, an empty batch the loop
   re-runs — is error-posture item 1. Fix it and say nothing.
3. **The in-progress anchor**, on a turn that must end with harness-tracked work in flight.
4. **The hand-off** itself, per the skeleton above.

Findings about the CUSTOMER'S AGENT are the product, and they are delivered in the hand-off (or at
a gate that exists to present them). Mid-flow they are not actionable and read as alarm.

### The rules the report gate checks

A hook reads the last message of every turn while a flow runs, and answers a broken rule with one short correction line.
Write the message to these rules the first time:

- **No spend.** No `$` figure for the build's own cost and no `total_cost_usd`. The agent's own amounts (a price it quoted,
  a charge it made) are evidence and stay.
- **Links open.** A local link has to open for the reader: for a report, paste the line `report_link.py link` printed for
  it; for a code location, link its absolute path (`/abs/file.py:42`). Never hand-type a relative path.
- **No stall.** The flow is pre-authorized. Do not end a turn on a question: decide from the skill's decision table, and ask
  only through a sanctioned gate. Timing, context and budget are never a reason to stop early.
- **A turn ends in one of four states:** it lands the flow with the hand-off; it ends on a sanctioned question to the user
  (or on the run driver, for a prerequisite only the run driver can supply); background work is in flight that wakes the
  session when it finishes (a background command or a spawned reviewer); or the harness moved an MCP call to the background
  because it outran its window. A mid-flow line reports work, it does not start any: start the work, or do the next step now,
  and start a wait in the background (never a detached `nohup`). Where nothing wakes the session after a turn (a headless run,
  or a harness that never does), wait inside the turn, in the foreground: `poll_job.sh` for a backend job and `run_wait.sh`
  for a run, re-run until it is done, and a reviewer subagent spawned in the foreground, never in the background.
- **A mid-flow line with a wait.** When the turn ends on the mid-flow line with background work just started, add one
  sentence: roughly how long this stage usually takes, in minutes, and that this session resumes by itself, with nothing for
  the user to type. The estimate has to hold. A spawned reviewer usually takes 10-20 minutes, so never say "about 5": give a
  range that tops out at 15 minutes or more, or say it can run longer. While the suite calibrates the simulation, say only
  `Suite: in progress — calibrating the simulation, about 10-20 minutes; this session picks up again by itself.` and name no
  calibration run, score, reviewer or fix.
- **Exact lines.** The hand-off opens with one of the skill's anchor lines, verbatim, and while work still runs the mid-flow
  line is used verbatim. Never invent a variant: a claim of completion without an anchor is refused. Two lines are computed by
  a script and appear verbatim: the scan's run-integrity line (`bench_manifest.py integrity-line`) and the suite's rung line
  (`lane_pin.py anchor`).
- **The hand-off presents the product.** No raw backend vocabulary (warnings, tracebacks, job internals), no build chronology
  (iterations, critic passes, cycles), no narration of suite defects you found and fixed, no suite-coverage gaps (run-integrity
  facts keep their one line), no doubt about the suite (failures are findings about the agent), no internal quality caveat,
  and none of our own machinery on a customer surface. `/simulation-run` and `/simulation-fixes` present the findings: what
  the suite caught in the AGENT, concretely, ranked, in the customer's domain language, quoting the agent. The suite stages
  present what was built and what it catches; defects the suite caught in the AGENT are the product working, so keep that
  line, with the AGENT as the subject of each clause ("the agent quotes a refund window the policy does not grant"), never the
  scenario. `/simulation-scan-agent` ran no scenario: it keeps one line for the POTENTIAL agent issues the scan recorded, with
  the AGENT as the subject, unranked and marked as possibly intended. An example the suite hand-off quotes is an audited true
  red of the scored final run, verbatim with its scenario and turn, never a qualification run.
- **Over an unanswered prerequisite** (a gate only the run driver can answer, and no answer came back), write no hand-off:
  end the turn on the run driver and say in product terms what they must supply. If a person is here, ask it again through a
  sanction; their answer releases the hold. The failure hand-off stays available, and it carries no anchor line.

## Step 0 — find the run + dirs

Resolve `$WORKDIR` / `$ART` (named-env aware, as in scan-agent Step 0).

**Open the flow window** (flow-fidelity):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm fixes
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready fixes
```

(the script resolves `$FLOWDIR`, clears the stale ask/report residue and stamps the name —
overwrite, because nothing drives this command as a sub-step, so any other name in the
marker is a stale window from an earlier flow). While it exists the hooks
deny unsanctioned asks and style-validate the final report; the Step-3b selection gate
(`fix-selection`, sanctioned at that step) is this command's ONLY gate — the run pick is
fully automatic (`--run` → pointer → newest), so no gate exists for it and none may be minted. The
window closes itself when the validated report renders; early exits close it
own-marker-guarded:

<!-- BEGIN step0: generated from ${CLAUDE_PLUGIN_ROOT}/docs/step0-block.md; edit there -->
**If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

**1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

- A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close fixes`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
- A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /simulation-fixes` (where the harness takes no arguments: "you run it for /simulation-fixes"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
- A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff fixes` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

**2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close fixes`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

- Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
- Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

**3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready fixes --no-backend-tools` and act on the line it prints as above.
<!-- END step0 -->

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close fixes
```

**Then check what this step needs** — exit 3 prints one line: print it verbatim as the whole hand-off, close
the window (above), and stop:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" require fixes "$WORKDIR"
```

Pick the run: `--run <dir>` if given, else the pointer, else the newest `$ART/runs/*`
(resolve in the same call — `$WORKDIR` from an earlier one is empty and reads `/.last_run`):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$WORKDIR/.last_run" 2>/dev/null || ls -1dt "$ART"/runs/*/ 2>/dev/null | head -1
```

If none exists, tell the user (plain language) to run `/simulation-run` first.

**Then read the findings as the page shows them** — the scan and the quality gate both write
`$ART/agent_findings.md`, one section per root cause, and this prints the page's copy of it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py" page-copy --workdir "$WORKDIR"
```

Read the findings only from that copy, never from the file itself: it holds the findings the page and the suite
hand-off show, each `run:` line naming the scored run's reproduction, in the page's order (tier, then severity). A
section of the file that the copy leaves out is internal and never an issue in the plan. It is why a user can reach this command straight off
`/simulation-generate-suite` without paying for a full scored run first: the qualification run's
artifacts are on disk, `.last_run` already points at them, and the gate's own findings are
largely diagnosed.

**That file carries two tiers, and they are not the same product.** `confirmed` (or no
`status:` line at all — older files) is a reproduced defect and seeds the ranked plan;
`potential — may be intended` is a limitation something recorded because it NARROWED what the
suite tests. Step 1 says what to do with each. `potential — confirmed while calibrating the simulation, not reached in
the scored run` is a confirmed defect this run did not reach: rank it after the reproduced ones, never under the potential section;
a finding whose `run:` line says `seen in 1 of N` is an ordinary issue at the severity its section states, with that
frequency said beside it, in the order `page-copy` prints it (how often breaks ties within a severity only);
`agent.untraced_failed_checks` is failed checks no reader traced, never an issue in the plan.

**Which run you are on changes what the findings can still point at**, so establish it now:

- **Straight from the suite build** (no `/simulation-run` since) — the run IS the qualification
  run the gate analyzed, so every scenario a finding names has a directory right there.
- **After a `/simulation-run` on the final set** — those qualification dirs are gone. The
  findings stand anyway (Step 1).

**A findings file with no run dir is still work**: if the run picker above found nothing but
this file has CONFIRMED sections, do NOT send the user to `/simulation-run` — diagnose from
their own evidence and say the run-level detail is unavailable. That happens on the static-only
path, where the user chose no run and the critic still read the agent's code. A file holding
only `potential` sections is the scan-only path: there is nothing to diagnose there either way,
so report them and stop.

## Step 1 — gather the issues (evidence)

**Seed the list from the page's copy of the findings first, when it has sections.** Each **confirmed**
section there is already diagnosed — by an independent critic that reproduced it against the real
ground truth, or by the scored run's reader from an audited violation — and its `why it is the agent:`
line (what the agent did that is its own) is the classification's evidence — so carry it
in with `home: agent bug`, its severity and its `finding id` intact rather than re-deriving it
from zero. Then pull
the run-dir evidence for whichever scenario it names, and merge: a finding and a failing eval
cell describing the same defect are ONE issue, not two.

**Carry the `potential` sections separately — do not diagnose them and do not rank them** (the calibrating and seen-once ones excepted, above). They
have no run evidence to pull and no verdict to defend; the suite designed around them rather
than testing them. Collect them verbatim for Step 3's trailing section. The one exception is a
potential entry whose subject a failure in THIS run actually exercised: rank THAT issue on this
run's evidence, noting it was flagged during the build. The entry itself stays `potential` in the
file — a run demonstrates the behaviour, it does not settle whether the owner wants it — so do
not restatus it, and do not also repeat it in the trailing section.

**A seeded finding survives a set it can no longer point at.** On the post-`/simulation-run`
path, `/simulation-generate-suite` Step 3b has overwritten `.scenario_set_id` with the final
7-scenario set, and the evals may have been regenerated with it — always for a corpus-backed
agent, and for any agent whose target-coupled judges were tuned to the qualification
scenarios. So a finding may name a scenario with no directory in the current run, and a policy
no longer in `$WORKDIR/policies_config.yaml`. **Neither is grounds for dropping it.** Diagnose
from the finding's own quoted evidence; its `run:` line in the copy says which run it was seen in, so a finding
whose `run:` line names the scored run is never said to come from the build; the
judge's prompt/threshold genuinely is unavailable for a regenerated set (a scored cell carries
`reasoning`/`findings`, never the policy definition), so do not go looking for it. Silently
discarding a defect because its scenario id went stale loses exactly the bug the suite was
built to find.

**A seeded finding keeps `home: agent bug` through Step 2 unless you DISPROVE it.** Each
section carries a `why it is the agent:` line — the critic's reproduction, or the reader's account of
the agent's own conduct in an audited reply — and Step 2's eval-too-strict branch must not quietly re-home a
finding past it. Re-home only by refuting that line against the real source, and when you do,
say so in the report. A finding that entered as an agent bug and left as a judge tweak, with
nothing recording the downgrade, is the laundering the `why it is the agent:` line exists to
prevent.

**Retire what the current run no longer reproduces.** A seeded finding whose defect the
current run shows fixed is reported as RESOLVED, not re-opened as an open issue — otherwise
the user who fixes something sees it again on every subsequent run until the next suite build
prunes the confirmed sections away. This is the one exception to the survives-a-stale-set rule above, and the two
are distinguishable: stale means the evidence's scenario is gone, resolved means the behaviour
changed where the current run can see it. When neither is true, keep it open.

Then walk the run dir and build the rest of the issue list — **read, don't dump**:
- **Errored scenarios** — each `<scenario>/error.json` (`exception`, `error`,
  `thread_id`, and — when the runner typed it — `kind`, `envelope_type`,
  `budget_seconds`). **Read `kind` FIRST**, and read it against the canonical
  `error_kind` vocabulary — the table in
  `${CLAUDE_PLUGIN_ROOT}/skills/simulation-fixes/references/run-vocabulary.md`
  ("THE `error_kind` VOCABULARY"), which names every kind,
  what actually expired in each, and the knob that fixes it. Do not restate it
  here and do not work from memory of it: three of the typed kinds mean a budget
  expired and one is minted by the aggregator with nothing expired at all.
  **Match the kind by NAME — never by the `harness_` prefix.** That namespace is
  OPEN (the runner reads each slug off a class attribute), so a prefix rule
  quietly converts the next harness kind that is not a budget — a dead mock, say
  — into "hit the turn budget", and hands the reader a stopwatch for a fault
  that has nothing to do with time. Home a budget kind to **test-setup**, name
  the budget the table says expired and its knob — never to **agent bug**, and
  never write a root-cause hypothesis about the agent's behaviour from one: a
  timed-out scenario has no transcript, so there is nothing to base it on. A
  `harness_*` kind the table does NOT list is an untyped harness fault: report it
  by name, still test-setup, still no story about the agent. Any OTHER
  `exception` (or no `kind` at all, from an older runner): the agent never
  completed → crash/contract failure, as before. Whether a scenario errored at all is its
  `verdicts.json` `status` (`agent_error` / `harness_error`, the typed kind in `status.cause.kind`),
  never the mere presence of an `error.json`.
- **`run.json` `error_kind: no_applicable_policies` — do NOT go looking for an
  `error.json`; there is none.** The scenario ran, finalized, and reached no
  verdict because its `verdicts.json` shows the guards gating off EVERY policy in the set.
  Nothing crashed, so there is no exception, no `thread_id`, and no transcript
  turn to home a hypothesis to. The row's `skipped_evals` names the gated
  policies — on this path that is the only record they exist, because the live
  run predates the guards. Home it to **test-setup / eval scope**, route the fix
  to `/edit-evals` (widen or remove the guard), and never to **agent bug**: the
  agent was never graded, so this row says nothing about it either way.
- **`run.json` `<scenario>.rooted_findings` — scope a repair round by UTTERANCE, not
  by failing eval.** One agent sentence can break several policies at once, and each
  judge files its own charge against it, so a row's failing evals over-count what the
  agent actually did. Each entry is one utterance (`turn_idx` + `quote`) with the evals
  that charged it under `charged_by`, each carrying its own reason: charges sharing a
  root are ONE symptom with one fix, not N. Read `failing_evals` for WHICH evals are
  red and this for HOW MANY things to repair. Two limits, both of which cost a round if
  you miss them: the utterance count is a CEILING (a judge quoting a span of what
  another quoted still gets its own entry), and a red that named no utterance at all —
  a `code_eval` over a count or world state — is in `failing_evals` and not here, so
  never read this list as the whole of a row's failures. Absent on a run.json written
  before the field; fall back to `failing_evals` and say the count is per-eval.
- **`run.json` `result: incomplete` — the dialog was CUT, and its unmet obligations
  are not agent behaviour.** The harness cut it — the per-scenario TIME budget ran out
  (`ended_early.reason: time_budget_exhausted`), one turn ran past the per-turn wall
  (`turn_budget_exhausted`) or the agent's model provider failed a call (`provider_infra`)
  — so the conversation ended early and the judges graded only the turns that were reached. Unlike a timeout
  there IS a transcript, and unlike a `fail` the row is in no rate — so its failing
  cells are the ones over turns that happened, and everything the arc never got to
  is unmeasured, not violated. Diagnose ONLY what the reached turns show; never home
  a missing obligation from a cut dialog to **agent bug**. (The same rule holds for
  every other `ended_early` reason on a row of any result — the axis, its vocabulary
  and who each reason routes to are in the "`ended_early` — a DIFFERENT axis" section of
  `${CLAUDE_PLUGIN_ROOT}/skills/simulation-fixes/references/run-vocabulary.md`. Do not restate it here.)
- **`run.json` `result: not_evaluated` — the backend reached NO goal verdict, so the row
  says nothing about the agent in either direction.** Read `row.goal.reason`, which
  names which of three it is and each routes elsewhere: nothing owned the scenario's
  goal (`goal_source: none` → `/edit-evals`, add a goal-owning eval — this is an EVAL
  SET gap, not an agent one), the measurement did not hold (`unclean` → the broken
  judge, same as an errored cell), or the backend published no verdict block at all
  (`unknown` → an upgrade, or a verdicts file with no `goal` block). Never home
  one of these to **agent bug**, and never count it as a failure: it is in no rate in
  the report either. Its per-eval reds are still real and still diagnosable — the goal
  axis and the policy axis are different measurements.
- **`user_sim_integrity.run_valid: false` for the user's world — the scenario needed the
  simulated user to see, receive or act on something the run could not show or carry out.**
  Its verdicts say nothing about the agent: home it to **test-setup**, and for its fix
  quote `user_sim_integrity.invalid_reason` as written: it names the one remedy.
  `dialog_outcome.wrong_recipient` is NOT this: the agent sent the needed value to the wrong
  address, which is an **agent bug**.
- **Failed evals** — each `<scenario>/verdicts.json`: the `evals` entries whose `state` is
  `fail` (the judge's cell beside each). The
  agent ran but a behaviour check failed. A `fail` ROW is the backend's goal verdict, not
  this — a scenario can carry a standard red and still `pass`, and the reds are what
  you diagnose either way.
- For each, pull the **evidence**: the relevant transcript turn(s), the tail of
  `tool_events.json` (what the agent actually called + statuses), and the failing
  policy's definition (from `$WORKDIR/policies_config.yaml`).
- **`<scenario>/verdicts.json` OUTRANKS both `analytics.md` and `run.json`.** Those two are
  a render: if the suite arc re-scored or corrected after them, they understate the failure
  surface (a re-scored scenario can read better in them than in the files). So when a `verdicts.json` is newer than `run.json`
  (`deliverables.py check run` names that `run.json`), take the verdicts from the files, treat the
  render as stale — say so in one line rather than quoting a figure out of it — and never
  diagnose against a number the files contradict.
  Full rule: `${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md`.

If there are **no failures and no seeded findings**, there's nothing to diagnose — the agent
passed the suite at the rung `suite_anchor.json` records. Say so plainly, then end with the overview-page block at
the end of this skill and nothing else (inside /simulate it is the run's one publish); don't manufacture work. **A run dir that reads green while `agent_findings.md` has sections is NOT
that case**, at either tier. A critic can classify a defect as `the agent` from the real source
without an eval ever going red (that is what a hollow green IS), and a later loop round may have
re-scored the cells the finding came from; a suite scoped around a `potential` limitation grades
the resulting decline as correct, so that cell is green precisely BECAUSE the limitation is real.
Trust the file's quoted evidence over the dir's headline pass rate. Diagnose the confirmed
sections and report them; for potential-only, skip the diagnosis and render Step 3's potential
section. If
a green result nonetheless looks *hollow* (a scenario that clearly did nothing, an eval that
can't have measured anything), that's a **suite-quality** gap, not an agent fix → route it
back to `/simulation-generate-suite`'s quality gate (`/analyze-simulation`), which owns
re-validating and re-converging the suite.

## Step 2 — diagnose each issue (conservatively)

- **Errored scenario (crash):** the root cause is local (your `simulate.py` / config /
  scenario) or remote (the mock env). **Run the `simulation-diagnose` skill**
  for it — it runs the local verifiers first,
  then engages the dual-agent protocol with the remote Claude that owns the mock
  filesystem, and returns a local-vs-remote classification. Use that classification;
  do not guess local-vs-remote yourself.
- **Failed eval (agent ran):** judge from the transcript + tool_events + the policy:
  - Did the agent genuinely do the wrong thing (wrong tool, fabricated outcome, acted
    without clarifying, ignored a constraint)? → **agent bug** (category 1). Point to
    the agent code path responsible.
  - Or is the **eval** too strict / mis-scoped for what the scenario actually asked →
    **test-setup** (category 2); adjusting the eval (`/edit-evals`) is legitimate *here*.
    But FIRST check coherence: if the policy is green/red only because the **scenario is
    incoherent** (a precondition absent from the agent's real ground truth, seeded state its
    tools can't return, an impossible seed), the fix is the scenario's GROUNDING (regenerate,
    `/upload-scenario-bundle` for a real record, or — when only the grading GT is wrong
    against verified truth — the targeted `edit_scenario_gt` tool,
    the `edit-scenarios` skill) — **NOT** loosening the eval to match a phantom.
  - Unsure which → category "needs investigation" with the checks to run.

Map each issue to ONE home (agent / test-setup / mock-env / investigate) with its
confidence + evidence. When two readings are plausible, present the most likely and
name the alternative. (A seeded finding arrives already homed — see Step 1's rule before
re-homing one.)

**Then read the homes back as one question about the BENCH, because that decides what can be
recorded.** A regression replays the agent's own conduct, so it may only be cut from a run whose
bench was not itself at fault where the issue happened — a seed frozen from a broken rig would
freeze the rig's mistake and grade the agent against it forever. With every home now assigned,
plan the recordings with the planner the suite build uses, so the plan names its own reasons:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-plan "$ART/agent_findings.md" \
  --regressions-dir "$WORKDIR/regressions" --run "<the run dir Step 0 picked>" --acceptance --bench-cells-from-audit \
  [--bench-from "$ART/auto_regressions.json"] [--bench-scenario <name>]… [--bench-cell <scenario>:<policy>]… \
  [--bench-policy <check>]… [--harness-wide "<title>"]… > "$ART/fixes_regressions.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" fixes-lines "$ART/fixes_regressions.json"
```

Pass `--bench-from` only when that file's `"run"` is the picked run's folder name (the suite's own post-final plan of
this run: its bench block is typed, never re-passed by hand); `auto-plan` refuses any other. Add a bench flag only for a
bench home YOU diagnosed in this run (`--harness-wide` with its title, `--bench-scenario`, `--bench-cell`,
`--bench-policy`). `fixes-lines` prints one `<finding id><TAB><line>` per seeded finding. Mark each **agent issue** by it:

- **Recordable** — its line reads `recorded on acceptance` (or `already recorded` / `recorded, not yet measured`: a folder
  covers it, and the second one's replays have not measured it yet).
- **Unreplayable** — `n/a — cannot be replayed …`: the agent runs without a user conversation, so no run ever has a turn
  to cut. Its check tracks it; it is never "blocked".
- **Blocked** — `waits for a re-run with the test setup of … fixed`. The issue is still real and still gets its fix;
  only the recording waits, until a run with that bench issue fixed reproduces it.

Every other line (`n/a — …`, `waits — …`) is said as printed.

**Harness-wide** means the bench issue is not confined to one scenario: `simulate.py`, the
harness config, the mock environment, or any `harness_*` error kind — those describe the rig
every scenario in the run shared. A bench issue in a single scenario's seed or a single policy
blocks only what it touches. A bench issue here is a home some failure in THIS run was diagnosed
to; a harness gap the suite gate accepted, with no failure in this run homed to it, is not one.

An agent issue with no run directory behind it is blocked too, whatever the bench did: recording
cuts a conversation out of a transcript, and a finding carried in from `agent_findings.md` after
the set was regenerated has none. A finding whose `run:` line names the scored run has one there.

## Step 3 — write the plan + `$ART/fixes.md`

**Two findings that show one act are one issue.** When two findings `page-copy` printed show the same act (the same
kind of change to the same kind of record, the same refusal or offer, or the same kind of claim), fold the one ranked
later into the other before writing the plan, then print `page-copy` again and plan from that:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/record_finding.py" "$ART/agent_findings.md" --id <the later finding> --fold-into <the other finding>
```

A different act stays its own issue even when the other issue's fix would also remove it: never fold, and never write an
issue as "covered by issue N", on that ground. A potential and a confirmed finding are never one issue; the script
refuses that fold (exit 2).

Number the issues in the order `page-copy` prints their findings (tier, then severity, then how often the scored run
showed it); an issue this diagnosis adds with no finding behind it goes among the confirmed ones by its severity. Write
`$ART/fixes.md` with one section per AGENT issue, in this shape; the test-setup and mock-env issues go, same
shape, to `$ART/fixes_internal.md`, never published (`customer_text_guard.py` refuses a fixes.md that carries one):

```markdown
# Fix plan — run <name>  (<G of M goals met · C of N scenarios failed a critical check · H of N scenarios failed a required check>)

## 1. <short issue title>   ·   severity: high   ·   confidence: medium
- **In short:** <one plain sentence, about 15 words: what the fix makes the agent do, so the issue stops>
- **Symptom:** <which scenario(s) + the failing eval / error, one line; a scenario named here is a scenario of the picked run's `run.json`; a seeded issue's line is written by `fix_verification.py header`>
- **Finding:** <`agent.<id>` — only on an issue seeded from a confirmed finding in `agent_findings.md`>
- **Evidence:** <quoted transcript turn / tool_events line / error — the proof; never an `audited reason:` line, which is the auditor's note; an issue seeded from a finding leads with that finding's first evidence line in `page-copy`>
- **Root cause (likely):** <hypothesis> — home: **agent bug | test-setup | mock-env**
- **Where:** `path/to/file.py:LINE` (the agent's own source / simulate.py / a policy)
- **Proposed fix:** <the minimal change; what + why>. <If low confidence: "verify by …">
  An agent fix quotes its replacement text (a fenced block, a block quote or a quoted passage), never only a direction.
- **Apply:** <"on your OK I'll edit <file>" for agent/test-setup, or "the remote
  agent patches the mock — re-run to confirm" for mock-env>
- **Regression:** <`regression_<issue_slug>` — recorded on acceptance | `regression_<issue_slug>` —
  recorded on acceptance; retired `trace_<failure_slug>` | `<folder>` — already recorded | waits for a re-run with the test setup
  of <scenario_NN | the `<check>` check | every scenario> fixed | the other line `fixes-lines` printed | n/a — bench issue
  (fixes_internal.md only) | n/a — no run evidence to cut>
...
```

`fixes.md` names what blocks a recording by the scenario or check it touches (every scenario, when
it is harness-wide), never by `fixes_internal.md`, an internal issue number, or the word "bench".

**Every issue carries that last line, including the ones that will never have a folder.** It is
the only place the decision is written down: nothing else records that an agent issue was left
uncovered, and "n/a — bench issue" beside a test-setup fix in `fixes_internal.md` is what tells a reader six weeks later
that the omission was the rule, not an oversight. **A seeded issue takes the line `fixes-lines` printed for its finding,
verbatim**; "recorded on acceptance" comes from there only, never from your own reading. An issue with no `Finding:`
line reads `recorded on acceptance` only when a dry pick returns one — `regressions.py pick "<the run dir>" --policy
"<the failed policy>" --workdir "$WORKDIR"`, no `--out` — and else the line Step 2's bench mark gives, or `n/a — no
conversation in this run can be replayed for it`. On an issue that already has a folder from an earlier
loop (Step 3c maps them), name that folder instead — a relapse is recorded once. When the chat names an issue's
replay state, it quotes that same `fixes-lines` line verbatim — one state, one wording, in fixes.md, the chat and the page.

**`In short:` is what the overview page shows under the issue's name**, for a reader who will not open the
code: what the fix makes the agent do, so the issue stops. Write "Makes the agent check the receipt before it
promises a refund.", never "Patch handler.py to call validate()" or "Add a guard". No file paths, function
names or jargon, never "fix available"; it promises nothing the Proposed fix does not do. The potential items
below carry none: they have no fix yet.

`deliverables.py check fixes` (the overview-page step below) refuses an agent fix with no replacement text and a
Symptom line that names a scenario the run does not have. It prints a `WARN` line, and still passes, for an agent
fix whose `In short:` line is missing or runs past about 20 words: rewrite that line before the
page is filled.

The header counts come from `run.json` `totals`: `goals_achieved` / `goals_measurable`,
`scenarios_with_critical_failures` / `critical_reported` (drop that segment when 0), and
`scenarios_with_high_severity_failures` / `high_severity_reported`. When `high_severity_unreported` is present, the
second half also says `required checks not reported for <that many>` — never a zero. On a run with trials those
totals pool every trial's conversations, and the script reads the per-scenario rollup instead: `<the run's pass line> · K of S scenarios
failed a critical or required check on some trial · N trials` (`run.json` `trials.run`). The pass line is the one every surface
pastes (`run_numbers.py`: `N of M scenarios passed · K of T trials passed`; one clause on a single-trial run, where it also
replaces `goals met`); an older `run.json` with no C7 totals keeps `G of M passed on every valid trial`. Write them with the script,
never by hand, whenever a run was picked:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fix_verification.py" header "$ART/fixes.md" --run "<the picked run dir>/run.json"
```

When the picked run is the scored run, the same call writes each seeded issue's `Symptom:` line from that run's checks.

**The `Finding:` line carries the seeded finding's stable id, verbatim.** It is how the issue maps
onto a folder the suite build recorded from the same finding at gate exit: both name the folder by
the slug the id derives, so the record step finds that folder and records nothing twice.

**Then, when any `potential` sections came through Step 1, a trailing section** — after the
ranked issues, never interleaved with them, because everything above it is evidenced and
everything in it is a question:

```markdown
## Potential agent issues (flagged while building the suite — may be intended)

These narrowed what the suite tests. Nothing here has been confirmed as a defect; each one
needs your call.

### <title>   ·   severity if real: medium
- **What we saw:** <the static evidence, with its numbers>
- **To confirm:** <the one check the owner runs>
```

Keep each block to those two lines (what the suite did about it is internal); the evidence is already in `agent_findings.md`, one
click away.

Then present a **short ranked plan in chat** (one line per issue: title · home ·
severity · confidence) and link the report — link last, no walls of text. Print the link every time a
message links `fixes.md` (it changes after `--verify`) rather than typing a path, which opens on no
surface the reader has, and never publish the file (the overview page is the only published page). The
line is the file's own link: its path, or a `file://` URL on Copilot and Codex:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Open the fix plan →" "$ART/fixes.md"
```

> Diagnosed F failures — N look like agent bugs, M test-setup, K mock-env.
> &lt;the fix-plan link line&gt;

The potential issues are counted once, in the anchor's count; never count them again here.

**Count what you actually diagnosed.** Both the header and that line are written for the
post-run path, where every issue came from a failure. On the fix-first path the run's own
failure count can be 0 while the issue list is not — "Diagnosed 0 failures — 3 look like agent
bugs" is nonsense. Lead with the issue count and name the source: `Diagnosed 3 issues the
suite caught in the agent`, and drop the `(G of M goals met · …)` from the header when the run
did not produce that count.

## Step 3b — ask which fixes to implement (the user chooses)

The plan is a **menu, not a mandate**. After the report link, **ask the user which fixes
to apply** (sanctioned ask — first run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction fix-selection`) with a selection question
(multi-select) — one option per fix — so they pick exactly what they want. **This selection IS the acceptance gate; apply nothing until they
choose.**

Build the choices from the ranked plan:
- **One option per fix**, labelled `<title> · <home> · <confidence>` (e.g. *"Clarify
  before reverting · agent bug · high"*).
- **List the high-confidence agent/test-setup fixes first** (recommended).
- Always include a **"None — keep the report, I'll decide later"** choice.
- **Mock-env fixes are not user-applied** — they're patched by the remote agent during
  diagnosis (Step 2) and confirmed by re-running, so surface them as *informational*, not
  as selectable code-edit targets.

Example shape (adapt to the actual fixes):

> Here's the fix plan — which should I implement?
> ☐ Clarify before reverting (agent bug · high) — edit the agent's graph node
> ☐ Loosen the empathy-eval threshold (test-setup · medium — eval over-strict on a *coherent* scenario) — edit policies_config.yaml
> ☐ None — keep the report, I'll decide later

**The acceptance is also what licenses the recording.** Step 3c records a regression for each
accepted **agent** fix before Step 3d applies it, and needs no further ask: the user has just
said this issue is real and worth fixing, which is the same judgement a regression rests on.
**Say what that costs in the question** when any option is Recordable: "accepting K of the fixes marked Recordable
records min(K, 6) replays now and adds that many conversations × <the run's trials per scenario> to each later run". The
6 are the plan's first six Recordable rows; a row past them reads `waits — …` and waits for the next `/simulation-fixes`,
whether or not an earlier one was declined.

## Step 3c — record a regression for each accepted agent fix (BEFORE the edit)

The conversation that exposed an accepted agent issue exists only in this run's artifacts. The
next suite build regenerates every scenario, so once the fix lands there is nothing left to
reproduce the defect from — and a relapse months later is rediscovered from scratch rather than
caught. So the recording happens **now**, between the acceptance and the edit.

**Which accepted fixes this covers, and no others:**

- **home `agent bug`** — the regression replays the agent's own conduct, so a bench fix has
  nothing to record. Say `n/a — bench issue` in `fixes_internal.md` and move on.
- **marked recordable in Step 2** — not blocked by a bench issue in this run, and with a run
  directory behind it.
- **one regression per issue**, whatever the fix touches. Two accepted fixes for one issue are
  still one folder.

**An existing folder means the issue is covered**, and that includes a folder the suite build
recorded at gate exit, before any fix was accepted. Its `ROLES.md` carries `Recorded at suite
build from a confirmed finding; no fix accepted yet.`; the record step maps the issue onto it by the
finding id on its `Finding:` line and writes nothing. Put that folder's name in the issue's
`Regression:` line. No trace scenario retires for it: a qualification run never carries one.

Nothing else changes. **Do not apply a fix first and record after** — the transcript is the
same either way, but a fix applied under a failed recording leaves the loop with a repaired
agent and no reproducer, which is the exact gap this step closes.

For each accepted agent fix that Step 2 marked recordable, in issue order, **run the
`record-regression` skill** for that issue, passing it the
run directory Step 0 picked and the issue's number in `fixes.md` (`--run` and `--issue`), plus
`--plan "$ART/fixes_regressions.json"` when the issue has a `Finding:` line: its pick is that plan's row, and past the
6th accepted record it refuses with `waits — a fix plan records at most 6 replays; …`, which is then that issue's
`Regression:` line. It owns
the rest — it re-reads the gate off `fixes.md` itself, checks the installed SDK, maps the issue
onto the folders already on disk, chooses the scenario and the cut, writes the folder, stages it
and appends the `regression` loop-log entry. **Do not write any of that here**, and do not write
a second loop-log entry for it.

Each invocation ends with the folder it wrote, or with a refusal naming what stopped it. Both
are results. On a harness whose manifest declares `harness.resume: unsupported`, an issue after the
opening turn is recorded opener-rooted (its recorded opener, replayed as a whole conversation), so that
manifest is never a reason to skip this step:

- **It wrote a folder** — put the folder name in that issue's `Regression:` line in `fixes.md`.
  Once every record is done, check it for a trace scenario to retire (below).
- **It refused** — that is a **blocker to report, never a silent gap**. Say the refusal's reason
  in the `Regression:` line and the chat report, worded by the `fixes.md` rule above (scenario or
  check, never "bench" or an internal issue number), and go on to apply the fix. The
  fix is not held hostage to the recording; what must not happen is the recording failing
  quietly and the reader believing the issue is covered.

**An issue Step 2 marked blocked is not skipped in silence either.** Its `Regression:` line
names the scenario or check whose test setup blocks it, and the report says the recording waits
for the next clean run.

### Retire the trace scenario a regression replaces

**Run this after every accepted agent fix has been through the record step, never between two
records**: two issues recorded from one trace scenario's run both carry its world facts only
while its folder is still on disk. Then run it once for each folder the record step **wrote** in
this Step 3c, in issue order — never for a refusal, a blocked issue, or an issue an existing
folder already covers. Retiring then would drop the only folder that replays the production
conversation.

A regression recorded from a **trace scenario**'s run and cut at the **seed boundary** — the
first live turn, which `pick` reports as `cut_turn: 0` with `resume_seed_messages` above 0 —
replays the production seed and then the same opener: the trace scenario's own conversation.
Keeping both runs that conversation twice in every final batch, so the trace scenario **retires
now**, at acceptance. A later cut seeds live turns the trace scenario never had, and both stay.
The two folders are never compared by bytes: the run reader mints its own id for the live
message, so equal conversations differ on disk. The script decides from the pick; do not decide
it yourself.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" retire "$WORKDIR/trace_scenarios" \
  --pick "$ART/regression_picks/regression_<issue_slug>.json" \
  --regression "$WORKDIR/regressions/regression_<issue_slug>"
```

`$ART/regression_picks/regression_<issue_slug>.json` is the pick `record-regression` saved in its
Step 4 for this folder. Before it deletes anything, the command checks the folders bear the pick
out: the two `resume_pin.json` files carry one conversation (same seed, same opener; only the
live message's id may differ), and the regression carries the trace scenario's world facts. It
removes the pick once it has decided, and prints JSON:

- **`"retired": "trace_<failure_slug>"`** — the folder is gone. `how: "git rm"` means the
  deletion is staged in the customer's repository, to commit with the fix; `how: "deleted"` means
  nothing tracked the folder. `recorded` is the line it added to `trace_scenarios/RETIRED`, so
  the next `/analyze-traces` chain does not write the same trace failure again. Append `fixes_note` to the issue's `Regression:` line, after a
  semicolon: `` `regression_<issue_slug>` — recorded on acceptance; retired `trace_<failure_slug>` ``.
  Then rewrite the directory's overview and log the retirement:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" overview "$WORKDIR/trace_scenarios"
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" trace_scenarios
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind trace-scenario \
    --finding "retired trace_<failure_slug> at acceptance — regression_<issue_slug> replays the same conversation, cut at the seed boundary"
  ```

  The entry is `trace-scenario`, not `regression` or `note`: it records what happened to a folder
  under `trace_scenarios/`, and that kind requires the folder's name, so the log says which one
  left. It is this step's own entry, not a second copy of the record step's `regression` entry.
- **`"retired": null`** — nothing retires: the scenario was not a trace scenario, the cut is
  past the seed boundary (`kept` names the trace scenario that stays), or an earlier issue's
  folder already retired it. Leave the `Regression:` line as it is; write nothing else.
- **Exit 2** — a refusal, and nothing was deleted; the pick stays for a re-run. Say it in the
  chat report in the refusal's own words, which name the cause and what to do, and leave the
  trace scenario in place.

## Step 3d — apply the chosen fixes (conservatively, one at a time)

For each fix the user selected — and ONLY those, **after Step 3c has recorded what it can**:
- **Agent bug** → edit the customer's own source (outside `plurai_sim/`): the minimal,
  reversible change named in the plan. Show the diff; keep it small; don't refactor.
- **Test-setup** → edit `simulate.py` / the scenario, or for an eval change prefer
  **`/edit-evals`** so the backend stays the source of truth.
- **Log each fix once it is applied**, with its number and title in `fixes.md` and the file it changed. The plan
  does not record a choice, so this entry is what makes the `/simulate` closing say to re-run rather than to choose:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind fix-applied --finding "#<N> <title>: <file>"
  ```
- Leave every unselected fix in `fixes.md` for later. Mock-env causes are handled by the
  remote agent (Step 2), not here.
- Re-state that nothing is **"fixed"** until `--verify` (or a re-run) proves it.

## Step 4 — `--verify` (only if asked)

After any accepted fixes are applied, re-run the run Step 0 picked with the command it ran with: `run_stage.sh` recorded
that command (the runner and its flags, and the run's `PLURAI_OBSERVABILITY`) when the run started, and `runner_rerun.py`
prints it for a fresh directory:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/runner_rerun.py" --workdir "$WORKDIR" --run <name> --out "$ART/runs/<name>-verify"
```

Run the lines it prints, as printed: an `export PLURAI_OBSERVABILITY=…` line when the run had one, then the run's
launch (`run_start.py`, which returns at once). Then wait on it with
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_wait.sh" "$ART/runs/<name>-verify"` as a wait in the background, re-run on
exit 2. `runner_rerun.py` refuses in three ways, and on every one you do not re-run here: ask the user to run `/simulation-run`
instead.

- `STOP(stale-ids)`: the scenario set or the eval set the run used is not the one the workdir holds now, because
  `/edit-scenarios`, `/edit-evals` or a regeneration moved it, and the re-run would score the fix against sets that
  are gone.
- `STOP(stale-mock)`: the mock service the run used is not the one the workdir names now (a redeploy), and the re-run
  would talk to a different world.
- Exit 1, no run command: the run has no recording (made before it was recorded), or the recording does not read back.

The subset option below adds `--only <scenario-id>` once per previously-failing scenario, which drops the other
scenarios from the command; an id the run did not have is `STOP(unknown-scenario)` and nothing is printed.

**To claim the fix broke nothing you must re-run the WHOLE set, not just the failures.** A
scenario that was passing and now fails cannot be seen by re-running the failed subset. Two
honest options:
- **Default — re-run the full scenario set.** Then check: each previously-failing scenario
  now passes (its `error.json` gone / eval passes) AND every previously-passing scenario
  still passes. Only this supports "✅ likely fixed, nothing else broke" — which matters because
  Step 3d may have edited the customer's shared agent source.
- **Subset-only (if the user wants it cheaper)** — re-run just the previously-failing
  scenarios and report **only** "these now pass"; state explicitly that the rest of the set
  was NOT checked, and tell the user to run `/simulation-run` for a full pass.

**READ THE TOOL TRAIL BEFORE ANY CELL.** A cell that moved between two runs moved for one of two
reasons — the agent did something different, or this suite does that to itself — and the check
above cannot tell them apart. Measured on three runs of one bench, two of them identically
configured: a cell flipped between the two identical runs over a byte-identical tool plan, and a
tool plan moved by a call with no cell noticing. So run the comparison FIRST and read its label
before you read a single verdict:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/compare_runs.py" \
  "$ART/runs/<name>/run.json" "$ART/runs/<name>-verify/run.json" \
  --floor "$ART/runs/<name>/run.json" --floor "$ART/runs/<name>-repeat/run.json"
```

The floor is **at least two same-configuration whole-set runs** and nothing else — the runner's
own stability retry is not one (it retries only scenarios that FAILED, so a clean run yields
none, and on that bench it reported 0 for a scenario whose true repeat distance is 1). Getting
one costs a second run of the BEFORE configuration; if the user declines it, say so rather than
substituting something cheaper. Index both runs as what they are, never as a scored run (drop the `--add` of a run
not made):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/runs_index.py" write "$WORKDIR" --add "<name>-verify=verify" --add "<name>-repeat=repeat"
```

Then, per scenario:
- `same_trail` / `within_trail_noise` / `no_trail` — the plan did not move further than this
  suite moves it on its own. A cell delta here is readable, and this is the only state that
  supports **"✅ likely fixed, nothing else broke"**.
- `beyond_trail_noise` — the agent ran a materially different plan. Report that row on its own:
  say what moved and whether the fix explains it. It never joins a blanket "nothing else broke", in
  either direction — a previously-passing scenario that still passes here passed a different
  conversation.
- `trail_noise_unknown` — **no floor was measured.** Nothing on that row is verified. Say which
  run is missing; do not stamp ✅, and do not stamp ❌ either.

Update `fixes.md` with a ✅/❌ verified column **and the trail label beside it** — the `table` call below. Never stamp "nothing
else broke" off a subset-only run, and never off a run whose trail label is
`beyond_trail_noise` or `trail_noise_unknown`.

**Each accepted fix is read k of K on the scenarios that exposed it, never off this one sample.** Pin
each accepted agent issue's exposing scenarios as `<scenario directory>:<the eval that caught it>` (a
bare scenario reads its goal; a `regression_*` scenario is never pinned — it is the fix's replay, read below), size K
from the before runs, run only the pinned scenarios K times with
`/simulation-run` Step 3's runner call (one `--scenario-id` per pinned scenario, `--trials="$K"
--no-run-pointer --no-observability`, into a fresh `$ART/verify_trials/<name>`), then read the verdicts:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }
PLURAI_PY_HINT="${PLURAI_BIN%/*}"   # prefer the interpreter that imports plurai
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
BEFORE=(--before "$ART/runs/<name>" --before "$ART/runs/<name>-repeat")   # every before-configuration run on disk
PINS=(--pin "<scenario>:<policy>")
K="$("$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/verify_exposing.py" trials "${BEFORE[@]}" "${PINS[@]}")"
# the runner call, at --trials="$K", into "$ART/verify_trials/<name>"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/verify_exposing.py" verdict "${BEFORE[@]}" \
  --after "$ART/verify_trials/<name>" --after "$ART/runs/<name>-verify" "${PINS[@]}" --json --out "$ART/verify_exposing.json"
```

Each pin reads one of four labels, decided at 80% confidence — say "likely fixed", never "verified":
- **likely fixed** (`fixed`) — it failed in none of the after runs, over as many clean runs as its before asks for;
- **still failing** (`still_caught`) — it failed at least once after the fix;
- **not yet shown** (`not_yet_shown`) — none failed so far, and N more clean runs are needed;
- **intermittent** — it failed too rarely before the fix to show a drop in one check, so the after only shows it
  did not get worse; it is never ✅ on its own runs.

Exit 2 means the runs could not be read k of K: say why, and leave the fix unverified.

**Write the verification table with the script, never by hand** — it applies those rules and the trail
labels per fix, one `--fix` per accepted fix (its number in `fixes.md` = its pins):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/compare_runs.py" \
  "$ART/runs/<name>/run.json" "$ART/runs/<name>-verify/run.json" \
  --floor "$ART/runs/<name>/run.json" --floor "$ART/runs/<name>-repeat/run.json" --json --out "$ART/verify_compare.json" > /dev/null && \
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fix_verification.py" table "$ART/fixes.md" --compare "$ART/verify_compare.json" \
  --verdict "$ART/verify_exposing.json" --fix "<N>=<scenario>/<policy>[,…]" --verify-run "<name>-verify"
```

**The carried regressions are read off their own replays, never off this one sample.** When the set
carries regressions (Step 2 of `/simulation-run` lists them `carried`), run `/simulation-run` Step 3b
for them after the verify run, into `$ART/regression_replays/<name>-verify`, with
`--before "$ART/runs/<name>/run.json" --decide` on its `replay-trials` call. Build the verify run's `run.json`
with `--regression-replays` that dir, and add `--regressions-dir "$WORKDIR/regressions"` to the
comparison above. Its regression section reads each one with the same labels, plus `not_pinned` — seen once
at record and caught in none of its first replays, so it moves to the recurring issue types, and the folder
stays for you to delete. A fix whose `Regression:` line names a folder the scored run replayed takes that
folder's reading at its recorded turn: an intermittent pin plus a likely-fixed replay is ✅. Only such a folder
gives a before; a folder Step 3c just wrote has none, so its pins read on their runs alone.
A regression with no earlier replay takes its record-time rate as its before. Put each verdict in the
✅/❌ column. `fixes.md` never carries the comparison's recurring-issue-type rows; the run's
`regressions_detail.md` holds them.

**Say "broke nothing", not "no regressions".** In this arc a **regression** is a recorded
folder replaying a confirmed agent issue, and the two readings sit three paragraphs apart —
"no regressions" beside a directory of them reads as "none were recorded".

**The set you just re-ran does not carry the regressions.** Everything under
`$WORKDIR/regressions/` reaches a scenario set only through the **final batch** of the next
`/simulation-generate-suite`, so a folder Step 3c wrote minutes ago is in no set yet, and
`--verify` checks the accepted fix through the scenarios that exposed it, k of K (above). Do not go looking for a regression scenario in this run and do not report
one as missing; `/simulation-run` is where a folder absent from the set it is about to run gets
its warning.

### When the accepted fixes included a bench fix

A bench fix changes the rig that every earlier seed was frozen from, so **every regression
already on disk goes under re-verify** — one of them may reproduce a defect the bench induced
rather than the agent's own conduct. List them now, so the obligation is written down where the
next loop iteration will see it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" list "$WORKDIR/regressions"
```

Put that list in `fixes_internal.md` under the bench fix, and say in the report that the folders are
pending re-verify. **Judge them on the next clean run of a set that CARRIES them** — the first
run after a suite build, not this one:

- **It still fires** → the defect is the agent's. Keep the folder; nothing to do.
- **It is silent** → the issue was induced by the bench this fix just repaired. **Delete the
  folder** and say so: it is closed as induced, and git history keeps what it held.

Deleting is how a regression is retired — there is no flag and no ledger — so a folder left in
place after its issue turns out to be the bench's keeps grading the agent for someone else's
mistake, under a name claiming it is the agent's.

**The final report opens with the anchor line** (report_guard validates it) and presents
the AGENT findings as the suite's value — what the suite caught, in plain product
language — never the suite's or tooling's own issues, and never spend:

> **Findings** — &lt;the count&gt; · plan → &lt;the fix-plan link line, re-printed after the verify column landed&gt;.

The count is the line this prints, pasted as printed — never counted by hand. It is the count the page and the suite
hand-off say, plus the plan's issues that carry no `Finding:` line and the findings the scored run showed
handled; a `WARNING:` it prints names a finding the plan
and the page disagree on (fix the plan, then print it again):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fix_verification.py" counts "$ART/fixes.md" --workdir "$WORKDIR"
```

**What Step 3c did goes on its OWN line below the anchor, never inside it.** One line, in
product language, naming the folders — and naming every recording that did not happen and why,
because that is the half a reader cannot see from the folders on disk:

> Recorded 2 regressions — `regression_refund_timing_invented`, `regression_greeting_omits_agent`.
> Issue 3 waits for a clean run: the mock env is returning stale balances on the same scenario.

Say nothing about regressions when none were recorded and none were blocked.

## Last step — pull the grounding mirror

Run this once, after the last backend call above and before the hand-off. It copies the latest
grounding ledger version to `$WORKDIR/grounding_ledger.yaml`, so the next skill and the next commit see
it; `${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md` is the canonical source. It never fails the skill and
never goes in a report.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

Carry on whatever `MIRROR=` line it prints: `unverified` and `ungrounded` change nothing here. No `MIRROR=`
line means the client pin or the wrong-backend guard stopped the block; run the contact hand-off (`stop_message.py contact --done`). Never write, patch or "fix" `grounding_ledger.yaml` yourself: the pull replaces it whole, and
a fact is changed in the agent's descriptions.

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

First check that this stage left every file the page reads:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check fixes --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step fixes --flowdir "$FLOWDIR"
```

It prints one JSON line. When `flow_continues` is true, leave out this hand-off's "Next" line and any offer to
run another command, and end the turn on this hand-off: the flow continues by itself. In limited mode (Step 0 printed
`ready, limited`) it continues through the `flow-window.sh handoff` line Step 0 names instead: run it right after
this hand-off and do what it prints in the same turn. When `run_end` is true this is the run's one publish: when
`retry` is true, repair it once the way the next sentence repairs a hand-off (`fill_refused` or `garbled`: nothing to
rewrite) and fill again; when `retry` is false, never fill again, whatever `skip` says. Otherwise, when `skip` is
`internal_text`, rewrite each line `internal_text` names the same way, then fill again; when `skip` is `numbers`,
a surface disagrees with the run summary: re-render it (`render_run_report.py`, then `release_verdict.py render`) and
fill again once; still `numbers` is a failure hand-off, never a page with those numbers. When `publish` is false,
add nothing and say nothing about it, except that when `say` lists lines, end the hand-off with them, verbatim, and
never run `done`. Otherwise publish the file at `report` with the Artifact tool, passing `report` as `file_path`
character for character (never respelled, never `~`, never relative), with `icon: "report"`, and never
`Read` it. When `page` is not null, pass it as `url`; if that publish is refused because this conversation has
not read the page, `read` it once (do not open the file the read saves) and publish again. If the page belongs to
another account or is gone, run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" forget "$WORKDIR"` and publish once without
`url`. Never pass `force`. Then:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step fixes --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

**Ending a turn while the diagnosis is still running** — a mid-flow message is
style-validated too, so lead it with the in-progress anchor: `Findings: in progress`. It
answers exactly one question, does this turn owe the hand-off skeleton; it never closes the
window, it never closes the window, it is valid only while harness-tracked work is actually in flight (a wait in the background, a spawned subagent — the harness records it and re-invokes you when it finishes; a detached `nohup` does not count and the guard blocks the anchor), and it exempts the message from nothing else (no cost figures, no question-shaped ending).

**The window closes itself on the validated hand-off** (report_guard removes the marker
when the report passes with the `Findings` anchor or the failure hand-off — never `rm`
before rendering). Early exits close it own-marker-guarded (not an exit that ends on the contact hand-off: render that hand-off and leave the window to its check):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close fixes
```

## Notes

- **Reuse, don't reimplement** — `simulation-diagnose` owns the dual-agent root-cause
  protocol; the runner owns re-runs. You synthesize + rank + write the plan.
- **Edits land outside `plurai_sim/`** when the cause is an agent bug (the customer's
  source) — and only with explicit acceptance. Test-setup fixes edit `simulate.py` /
  configs / scenarios; mock-env fixes are remote.
- **Named-env aware**; the re-run output lives under the gitignored `$ART`. `fixes.md` and
  `agent_findings.md` are the exception — the managed block (rev 7) un-ignores them because they
  ARE the deliverable a human opens, so stage them once written:
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" artifacts/fixes.md`. Name ONLY
  `fixes.md` — Step 3 always writes it. `agent_findings.md` is written conditionally, and an
  explicitly named file that is missing is exit 3 by contract ("the write did not land"), so
  naming it would make a successful staging read as total failure; it rides the default set
  (a bare `stage-inputs.sh`), which stages whatever exists.
- **A regression is recorded, never generated here.** Step 3c writes a folder under
  `$WORKDIR/regressions/` and stops. Nothing is uploaded, no scenario is generated, and the
  folder reaches a set only through the final batch of the next `/simulation-generate-suite`.
- **Regressions stay after the fix.** The folder is not deleted when the issue is repaired —
  that is the whole point of it, and a set carrying it is what proves the fix holds on the next
  build. It is deleted in exactly one case: a bench fix turns out to have induced the issue
  (Step 4).
- Plain language to the user; cite file:line as `path:line` so it's clickable.
