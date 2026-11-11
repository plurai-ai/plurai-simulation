---
name: simulation-run
description: >-
  Score the agent: a simulated user plays every scenario against it on the mocked world and all evals are scored, optionally recorded as an experiment in LangSmith, Langfuse, Arize or Braintrust. Stage 4 of /simulate. It scores but does not diagnose (use /simulation-fixes).
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Run — score the agent at scale

**Usage:** `/simulation-run [--agent-module-path <path>] [--experiment-name <name>] [--max-concurrent <n>] [--agent-version <v>]`

You are the LOCAL Claude Code agent. This is **command 4 of 5**:

```
scan → traces → suite → run → fixes
 map     learn   build   score  repair
                         ▲ you are here
```

`/simulation-run` runs every scenario against the customer's agent on the deployed
mock and scores all evals. It is a thin orchestrator over the **`plurai-run-simulation`**
runner (an SDK CLI) — do not reimplement the run loop. Your one piece of judgement
is the **observability opt-in** (Step 1). It does **not** diagnose failures — that's
`/simulation-fixes`.

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

## Step 0 — args, preconditions, dirs

Resolve `$WORKDIR` (named-env aware) and `$ART="$WORKDIR/artifacts"` exactly as
`/simulation-scan-agent` Step 0 does — `cd "<agent dir>"` AND `export PLURAI_AGENT_PATH="<agent dir>"` in the SAME Bash call
as the `source … resolve-workdir.sh`, and again in every later call that re-sources it (the shell
resets between calls). The `cd` SELECTS the root; the export only NARROWS which roots may be
adopted, so a foreign `plurai_sim/` above a reset cwd is refused rather than adopted.

**Open the flow window** (flow-fidelity) — always run this; the script itself
decides whether a driver keeps the window:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm run
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready run
```

(overwrite unless a DRIVER owns it — `/simulation-generate-suite` and `/analyze-simulation`
invoke this command as a sub-step, so their marker survives; any OTHER name is stale and
gets stamped over). While it exists the hooks
deny unsanctioned asks and style-validate the final summary; this command's sanctioned
asks (the observability opt-in, a missing provider/platform key) each write the one-shot
marker first. The window closes itself when Step 5's validated summary renders; early
exits close it own-marker-guarded:

<!-- BEGIN step0: generated from ${CLAUDE_PLUGIN_ROOT}/docs/step0-block.md; edit there -->
**If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

**1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

- A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close run`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
- A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /simulation-run` (where the harness takes no arguments: "you run it for /simulation-run"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
- A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff run` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

**2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close run`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

- Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
- Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

**3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready run --no-backend-tools` and act on the line it prints as above.
<!-- END step0 -->

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close run
```

**Runner and runtime files — the first Bash call after the Step 0 block above.** The window is armed and the readiness
line, the backend check and the tool-list check have passed, so a refused plugin never reaches the runner. Run nothing
of the runner or the SDK before them.

```bash
cd "<agent dir>" && export PLURAI_AGENT_PATH="<agent dir>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
AGENT_MODULE_PATH="$WORKDIR/simulate.py"   # or the --agent-module-path argument
AGENT_MODULE_PATH="$(python3 -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$AGENT_MODULE_PATH")"   # absolute, `~` expanded: the runner never gets a literal `~`
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the client pin REFUSED this runner — never fall back
[ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER="$(command -v plurai-run-simulation || true)"
HARNESS_DIR="${AGENT_MODULE_PATH%/*}"
MISSING="$(
  [ -n "$RUNNER" ] || echo "MISSING runner plurai-run-simulation"
  [ -f "$AGENT_MODULE_PATH" ] || echo "MISSING $AGENT_MODULE_PATH"
  grep -oE "\(_DIR / '[^']+'\)\.read_text" "$AGENT_MODULE_PATH" 2>/dev/null | cut -d"'" -f2 | while read -r f; do [ -f "$HARNESS_DIR/$f" ] || echo "MISSING $HARNESS_DIR/$f"; done
  grep -q "_DIR.parent / 'skills'" "$AGENT_MODULE_PATH" 2>/dev/null && { [ -d "$HARNESS_DIR/../skills" ] || echo "MISSING $HARNESS_DIR/../skills"; }
)"
if [ -n "$MISSING" ]; then
  printf '%s\n' "$MISSING"
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close run
  exit 1
fi
echo "preflight ok: $RUNNER"
```

Any `MISSING` line → stop here and end with one or two lines, nothing else:
- runner: "The Plurai simulation runner is not installed in this project. Install it with
  `<line>`, then run /simulation-run again." —
  `<line>` is what `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints.
- a harness file: "`<path>` is missing, so the agent cannot start. Run /simulation-scan-agent to
  regenerate the harness, then run /simulation-run again."

**Bridge runtime — the plugin installs its own, without asking, headless included.** One unconditional
call; on a bench with no `bridge/bridge.map.json` it checks nothing and prints `runtime: null`:

```bash
cd "<agent dir>" && export PLURAI_AGENT_PATH="<agent dir>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/bridge_preflight.py" "$WORKDIR" --install
[ -f "$ART/verification.json" ] && echo "VERIFICATION_RECORD=present" || echo "VERIFICATION_RECORD=absent"
```

Read only `runtime` and `installed_now` from the preflight's JSON line and ignore its exit code (the
same call runs the agent-dependency checks, which never stop this step). The runtime is the plugin's
own prerequisite under `plurai_sim/`, never the user's install and never a run-gate install block.
`installed_now: true` with `VERIFICATION_RECORD=present` → right after the client-pin check below,
re-run the bridge smokes once: `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify
--mode reuse` (after `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"`), then continue; the
gate below reads its result. A `runtime` that is still a `bridge_runtime_*` code is one line quoting
the finding's reason, then continue.

**Check the client pin before anything is spent** — the plugin + SDK commits this workdir's
runs must be made with (`$WORKDIR/.client_pin.json`, armed by `/simulation-generate-suite` Step 0
or written by an operator):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/client_pin.py" check || exit 1
```

Exit 3 names the half that is not at the pinned commit — STOP; a run made now would look valid
and exercise the wrong code. **A scored comparison (a run meant to test specific client builds) MUST have the
pin written before this step** (`client_pin.py write --plugin-checkout … --sdk-checkout …`);
"no client pin" is the customer default and is fine for anything else. `resolve_plurai.py`
re-checks the pin on every runner resolution, so this step is the early, named stop, not the
only one.

**Then check what this step needs** — exit 3 prints one line: print it verbatim as the whole hand-off, close
the window (above), and stop:

```bash
cd "<agent dir>" && export PLURAI_AGENT_PATH="<agent dir>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" require run "$WORKDIR"
```

Read the handles from `$WORKDIR`:
- `.scenario_set_id` — **required** (the `require` line above checks it, and also refuses a suite build that stopped before it handed off: its scenarios are that build's draft).
- `.mock_service_url` — **required + healthy, UNLESS `.run_mode` contains `mockless`** (or the
  legacy `local` — written when the mock-env deploy was skipped for a tool-less or all-real agent —
  no mockable tools, nothing to
  intercept, no mock URL exists; run without `--mock-service-url`. The runner now infers the mock
  posture from the `.run_mode` sidecar `bench_manifest.py run-mode` writes — precedence: explicit `--mock-mode` flag >
  env > `.run_mode` sidecar > default `connect` — so a sidecar-bearing workdir runs mockless without
  any flag; passing `--mock-mode mockless` explicitly remains the override and is always safe).
  **`mockless` and `local` are different claims** — `local` means a mocked world hosted in-process,
  `mockless` means nothing is mocked. An agent with nothing to mock is `mockless`. When
  `.run_mode` is absent, run
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-mode`
  — it writes `mockless` when nothing needs a mock. Still absent, the two shapes need different answers. **No `.mock_service_url` at all** —
  check for a stored env id first (`.env_id`, `.mock_session_id`, or the env manifest). If one
  exists the world only needs re-activating, and the LAYOUT decides who does it: with named envs
  (`plurai_sim/envs/` exists) tell the user to switch to the env, which re-deploys it by id in
  seconds; in the bare layout there is no env to switch to — call `deploy_env(env_id=<id>)`
  yourself, once (idempotent, LLM-free — it revives the same stable `/mock/<id>/` URL), then
  join the returned server-relative path to `SERVER_URL`
  (`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1`, then
  `${SERVER_URL%/}` + that path — the whole base, its path prefix included, never a rebuild from
  scheme+host) and rewrite `.mock_service_url` (the `/simulation-generate-suite`
  Step 0c rule). Only when there is
  NO id anywhere has the world never been stood up — then
  say so and offer `/simulate`, which stands the world up as it builds the suite. Do not send
  someone with an existing scenario set to the suite just to recover a URL: the suite regenerates
  the set at its final step, replacing the very scenarios this run was about to score. **The URL exists but returns 410
  `needs_redeploy`** (or is unreachable) — the definition is fine and only the deployment lapsed:
  recover it the same way — switch to the env when `plurai_sim/envs/` exists, else one
  `deploy_env(env_id=<id>)` + the URL rewrite above.
  **A STALE `.mock_service_url` is now FATAL, not invisible.** From plurai-sdk 0.4.0 the runner
  reads the sidecar as a PIN and pre-flights it: a pinned URL that refuses every connection
  refuses the whole run (exit 65 REFUSED, no scenarios, no dataset upload) instead of falling back to
  the `http://localhost:8199` default and reporting the agent for a mock nobody deployed. So
  the recovery above is no longer optional hygiene — **the fix is always to rewrite the
  sidecar**, never to pass a URL on the command line and leave a stale file behind for the next
  run. Read the refusal literally: it names the URL it probed, and that URL is what `$WORKDIR`
  pins. (The pre-flight runs only in `connect` posture; `local` / `mockless` host their own
  loopback stub, so a pinned URL there is a token rather than an address and is not probed.)
  **And a backend or pinned mock that answers 401 to the run's own credentials is refused up
  front** — the runner measures it before anything runs, so the token must be
  in the runner's environment (never passed as an argument; see Step 3).
  **A `mockless` workdir needs an SDK that understands it (>= 0.3.2) — STOP if the resolved
  runner is older.** Below that, the sidecar value is filtered out entirely, resolves to the
  `connect` default, and the runner dials a mock that was never deployed: EVERY scenario fails
  with a mock-unreachable error that names a URL nobody configured. `resolve_plurai.py` refuses a
  runner that reports a version below the SDK floor; one that reports none gets only a note — say
  it plainly and have the user reinstall the SDK
  (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints) before
  the run. Writing `local` instead would run, but it re-asserts the mocked world this
  posture exists to deny.
- **The scan's harness verdict — CHECK BEFORE ANY SPEND.**
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-gate; echo "RUN_GATE_EXIT=$?"`
  — `1` ⇒ do not run yet: each line is a finding you decide on without asking (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`) — apply the remedy it names, or overrule an advisory `finding:`, and re-run the gate; a failure the run measures (the agent raised, a turn came back silent) never stops it. A remedy only the user holds (a key, a grant) is an ask for that material, never for a verdict (sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`); a `live_check_failed` line (a tool that runs for real cannot log in to or reach its service) is asked with its error quoted, options **I've fixed it — check again** (re-run the gate) or **Stop**: mocking that tool is a re-scan. A `host toolchain:` line is a `plugin.run_gate.host_gap` finding (a declared program or module measured missing, broken or failing where the agent runs it): repair the host, or, holding evidence no scenario reaches it, overrule that finding in the plugin's ledger and re-run the gate; that run grades every row; never tell the user about the overrule. A `plugin.run_gate.host_unverified` finding (nothing measured) holds nothing. An install block the gate prints (`# install on this machine…`) is the user's to run: hand it on verbatim as the prerequisite ask; never run it yourself. The runner holds the same gap again as its own `host.requirement_gap` finding: overrule that one too, with the same reason. `0` / `3` ⇒ continue.
- **Agent provider key — required, CHECK BEFORE ANY SPEND.** The run invokes the customer's real
  agent, which needs its own LLM credentials. Infer the provider from the agent's code/imports or
  its `.env` (the scan report may note the model) — e.g. OpenAI → `OPENAI_API_KEY` or an
  `OPENAI_BASE_URL` gateway; Anthropic → `ANTHROPIC_API_KEY`; Azure → `AZURE_OPENAI__*`. Check it with
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" provider-key; echo "PROVIDER_KEY_EXIT=$?"`
  — never a test of the env var in your own shell: the run reads the module's gitignored `.env`
  too, and the check decides. `0` ⇒ present, continue.
  `3` ⇒ the bench names no provider: infer it as above and look in the env and the `.env` for its
  names (names only, never print a value). `1` ⇒ missing: STOP and ASK the user to add it to the gitignored `.env` the line names, saying
  where it looked (the environment Claude Code passes to commands, and that file); a `0` is never asked about
  (sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction provider-key`) — the
  same way Step 1a asks for observability keys. Do NOT
  start the run and let the first scenario fail N turns in.
- `.eval_set_id` — optional (scores the eval set if present). **This is what gets scored** —
  the runner reads the BACKEND set via `--eval-set-id`. `plurai_sim/policies_config.yaml` is a
  read-only mirror and is NOT read at scoring time, so a local hand-edit to it changes nothing
  about this run's results. To change what scores, use `/edit-evals` (which mutates the backend).
- `--agent-module-path` → the `simulate.py` (default: `$WORKDIR/simulate.py`). Each block that passes it to the runner sets it and makes it absolute with `~` expanded, so a path the user typed as `~/…` or relative reaches the runner whole.
- MCP server (`plurai`) reachable for scenario enumeration.

**Keep the previous run's `run.json` as the regressions' "before"** — this run moves `.last_run`, and the
replay plan, Step 3b and the retirement after Step 5 read the earlier batch:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PREV="$(cat "$WORKDIR/.last_run" 2>/dev/null || true)"
if [ -n "$PREV" ] && [ -f "$PREV/run.json" ]; then cp "$PREV/run.json" "$ART/regression_before.json"; else rm -f "$ART/regression_before.json"; fi
```

## Step 0 — preflight: is the backend actually connected?

**Do this before anything else, every time.** Check that the `plurai` backend tools are present in your
tool list. If they are NOT, the readiness check above already covers it: run it with `--no-backend-tools`, close the
window, tell the user its one line, and stop. Do not fall back to running the heavy jobs locally, and do not proceed
hoping a later step reconnects: it cannot.

One narrow exception, for READS only: when the backend is demonstrably healthy and it is the
REGISTRY that is empty (the MCP server list is happy, the URL answers), a single status or artifact
read can go over plain HTTP — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status
'{"id": "<id>"}'`. That is for finishing a poll or reading a result, never for driving a flow: a
generation flow restarts into a session that has the tools.

## Step 1 — observability opt-in (branch on who is deciding)

The SDK already records runs to an observability platform; it's **off unless
enabled**. Who chooses is the FIRST branch, before any ask:

- **Driven** — a driving command's flow window is open (`.plurai_flow_active` holds a
  driver's name, e.g. `generate-suite` / `analyze`): do NOT ask. The driver decides the
  observability posture; apply its decision and continue to Step 1a (or 1b/2 when it
  chose none).
- **Direct** — no driving flow window: the choice is made HERE, by the sanctioned ask
  below.

A sink is turned ON by the `<PLATFORM>__ENABLED` / key env vars in Step 1a. **Turning one
OFF takes a flag, not their absence** — the SDK loads a `.env` discovered by walking UP from
the cwd, so a customer's `LANGSMITH__ENABLED=true` stays in force with nothing exported (the
runner prints the correction itself: "enabled via …/.env; pass --no-observability to turn it
off"). Off, strongest first: **`--no-observability`** (the runner's own flag — disables every
sink whatever env or `.env` says) > `PLURAI_DISABLE_DOTENV=1` (skips `.env` discovery) >
`export <PLATFORM>__ENABLED=false` (real env outranks `.env`). Any OTHER flag you invent is
still fatal: the runner parses with strict `argparse.parse_args`, so an unknown flag prints a
usage error and **exits 64 (USAGE) before the run starts**. On the DIRECT branch, ask the user (sanctioned ask — first run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction observability-opt-in`) — plain language,
recommend experiment:

> Record this run in your observability platform?
> 1. **As a tracked experiment** *(recommended)* — one record per scenario, eval
>    scores attached as typed feedback, comparable across runs.
> 2. **Just traces** — emit the per-scenario traces without the dataset-backed
>    experiment grouping.
> 3. **No observability** — run and score locally only.

## Step 1a — VERIFY the platform keys before running (don't let it fail silently)

If the user chose experiment or traces, the run **must** have valid creds for the
chosen platform. This skill exports `<PLATFORM>__ENABLED=true`, and the SDK
**requires the key when a sink is enabled** — e.g. `LangSmithSinkConfig` raises
`ValueError('LangSmith is enabled but LANGSMITH__API_KEY is missing')` at startup, so
a missing key makes the run **crash before scoring** (a hard abort, not a silent
no-op). Check presence up front so the user fixes it before a wasted run. (Pick the
platform the user has creds for; at most ONE sink may be enabled — the SDK errors on >1.)

**Required keys** (non-empty in the env the runner inherits OR in the `.env` the SDK loads):

| Platform | Required | Optional |
|---|---|---|
| **LangSmith** | `LANGSMITH__API_KEY` | `LANGSMITH__PROJECT` |
| **Langfuse** | `LANGFUSE__SECRET_KEY` **and** `LANGFUSE__PUBLIC_KEY` | `LANGFUSE__HOST` (self-hosted; defaults to cloud) |
| **Arize** | `ARIZE__API_KEY` **and** `ARIZE__SPACE_ID` | |
| **Braintrust** | `BRAINTRUST__API_KEY` | |

Arize and Braintrust need their SDK extra: run the line `resolve_plurai.py --install-line --extra arize` (or `--extra braintrust`) prints before the run.

**A missing key goes in the agent folder's gitignored `.env`, never an export.** The check decides: it
reads the environment Claude Code passes to commands (what the runner inherits) and the `.env` the SDK
loads itself, and a key it finds is never asked about. A key exported in the user's terminal may not reach
those commands, so the `.env` is the route that works on every host.

Check with the script, from the agent dir (the runner's cwd), under the agent venv's python —
never with `[ -n "$LANGSMITH__API_KEY" ]`, which misses the `.env`:

```bash
cd "<agent dir>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/sink_keys.py" "$PLATFORM" --from "$PWD"; echo "SINK_KEYS_EXIT=$?"
```

It checks the env AND the `.env` the SDK will load (`PLURAI_ENV_FILE`, else the nearest `.env` up from
the agent dir), names only — with the SDK's own resolver and parser when that python has them — and on
a miss prints the file to put the key in and whether git ignores it.

- **`0` (present)** → proceed to Step 1b.
- **`2`** → `$PLATFORM` is not `langsmith`, `langfuse`, `arize` or `braintrust`: set it from the user's choice and re-run.
- **`1` (missing)** → **STOP and ask the user to set the key** (sanctioned ask — first run
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction platform-key`) — plain language, name the
  exact var(s), where to create one, and the file from the script's `put … in <file>` line, e.g.:

  > To record this run in **LangSmith** I need a `LANGSMITH__API_KEY`. Create one at
  > smith.langchain.com → Settings → API Keys, then add the line `LANGSMITH__API_KEY=<your key>`
  > to `<agent dir>/.env` (it's gitignored) — tell me when it's there. I looked in the environment
  > this session's commands get and in that file.

  (Langfuse: a **secret + public** key from cloud.langfuse.com → Project Settings →
  API Keys, both in the same `.env`, plus `LANGFUSE__HOST` if self-hosted.) If the script
  says the file is NOT gitignored, have the user add `.env` to `.gitignore` first. Never
  write the key yourself and never echo it.

  Then **re-check** with the script. Only if the user can't or won't provide a key, offer to run with
  **no observability** (option 3) so the run still scores locally — never block the run
  on it, but never silently pretend it was recorded either.

> **Phase 2 — getting a key when the user has none / lacks org permissions** is not
> built yet. For now: verify → prompt → fall back to local-only. See the "Getting a
> key" note at the end of this SKILL for the intended phase-2 behaviour.

## Step 1b — dataset name vs run name (stable series + unique run)

Repeated runs of the **same scenario set** must land under **one dataset** so they're
comparable, while **each run** is its own experiment row under it. Two distinct names:

- **Dataset name — meaningful AND stable per scenario set.** Derive it deterministically
  so every run of the same set resolves to the SAME name (and different suites don't
  collide):

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
  AGENT_NAME=$(…from plurai.config.yaml…)          # meaningful
  SID=$(cat "$WORKDIR/.scenario_set_id")
  DATASET_NAME="${AGENT_NAME} · sim-${SID:0:8}"     # stable (sid) + unique across suites
  ```

  For **LangSmith**, export `LANGSMITH__DATASET="$DATASET_NAME"` — it takes precedence
  over the backend's generic set name (the reason you currently see "generated
  dataset"). Same name every run ⇒ runs accumulate under one dataset.

  If the default project name is already taken by a project not linked to this dataset,
  current SDKs auto-create and use `<name> · exp-<dataset-id8>` so runs still land on
  the Dataset Experiments tab (older SDKs only warn). The derivation fires only on the
  dataset-name fallback — either an explicit `LANGSMITH__PROJECT` or an explicit
  experiment name (`PLURAI_EXPERIMENT_NAME` / `--experiment-name`) disables it and
  keeps the warning.

- **Run name — UNIQUE per run.** Pass `--run-name "<experiment-name>-<ts>"` when the user
  gave `--experiment-name`, else **omit `--run-name`** and let the runner auto-generate a
  unique one (`run-<dataset>-<ts>`). Never reuse a run name (it collides an experiment row).

- **Langfuse caveat (flag, don't fake).** Langfuse has **no run-time dataset-name
  override** — its dataset name comes from the scenario set's stored name, so it'll show
  the generic name until that's set meaningfully at generation (backend) or a
  `LANGFUSE__DATASET` override is added (SDK). Tell the user; it's phase-2 (see notes).

**Map the choice to the runner** (env for the subprocess + flags):
- **Experiment** → export `<PLATFORM>__ENABLED=true` (+ `LANGSMITH__DATASET` for
  LangSmith), pass `--upload-datasets` + the unique `--run-name`. Uploading the scenarios
  this run covers as the dataset is what makes it a comparable, dataset-backed experiment.
- **Traces** → export `<PLATFORM>__ENABLED=true` (+ `LANGSMITH__DATASET`), pass the
  unique `--run-name` but **NOT** `--upload-datasets`.
- **None** → pass `--no-observability`. Not enabling a sink is not enough: the customer's
  `.env` can enable one behind your back (see Step 1).

**Then WRITE the decision down, in the same turn you make it — from plurai-sdk 0.4.0 this is
REQUIRED, not bookkeeping.** The posture is a fact about this workdir, not about this
invocation, and a posture kept only in the flags of one command would be re-derived by every later run
from the environment — which is discovered by walking UP from the cwd to the nearest `.env`.
A directory the customer owns could therefore enable a production sink for a run nobody pointed
at it. From SDK 0.4.0 the default is **isolated** — recording is something somebody SAYS. So the
live risk is the converse: **a suite that does not declare `recording` silently produces no
experiments at all.**

**TWO BLOCKS, ONE PER BRANCH — copy the one your Step-1a choice names.** They differ in exactly
one token, which is precisely why a single block did not work: a driver copying it verbatim on
the Experiment branch wrote the other branch's token and the suite silently produced no
experiments, the failure the paragraph above warns about. A copyable block that needs an
un-signposted edit is the defect, not the cure.

- **Experiment** or **Traces** → write `recording`:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
printf %s "recording" > "$WORKDIR/.observability"           # isolated | recording
```

- **None** → write `isolated`:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
printf %s "isolated" > "$WORKDIR/.observability"            # isolated | recording
```

One token, in the same place and the same shape as `.run_mode` — the SDK reads both with one
reader, beside `simulate.py`. The runner's precedence is `--observability` /
`--no-observability` > `$PLURAI_OBSERVABILITY` > this sidecar > the default `isolated`, so an
absent or unreadable file means OFF, never "whatever the environment says".

The sidecar is a fact about the WORKDIR. Step 3's runner block carries the same decision as a
fact about the INVOCATION (`--no-observability` on the None branch, `PLURAI_OBSERVABILITY` on
the recording one) so that a block copied without this step still runs the posture you chose —
see the keep/delete table there, which is also where the `--observability` flag is ruled out.

> **Best-effort, always.** Even with keys present, observability is wrapped so any
> push failure is logged and swallowed — it NEVER aborts the run or changes the
> result. (The key check above is the one thing worth surfacing *before* the run,
> because a missing key fails invisibly rather than loudly.)

## Step 2 — enumerate scenarios

Call `get_artifact_meta(scenario_set_id=<.scenario_set_id>)` to list scenario
ids (the runner needs each `--scenario-id`). A retired regression stays in the set until the next build and is never
played again: the ids Step 3 passes are the ones this prints.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
printf '%s\n' <the scenario ids from the meta call, one per line> \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" runnable --retired-dir "$ART/retired_regressions"
```

**`--scenario-id` is OPTIONAL (append-style, one flag per scenario). Omitting it entirely runs
the WHOLE set** — the runner enumerates the scenarios from the server. Still prefer writing ONE
literal `--scenario-id <id>` flag per id from the meta call (see Step 3 — never a variable
expansion): the explicit list is auditable in the transcript, and it is the same mechanism the
cheap subset re-run uses (pass only the ids you need — one scenario is the cheapest partial
check there is).

**Then check the recorded regressions against this set — WARN, never fail.** Each folder under
`plurai_sim/regressions/` replays one confirmed agent issue, and a folder reaches a set only
through the FINAL batch of a `/simulation-generate-suite`. So a set generated before the folder
was recorded simply does not carry it, which is normal and not a defect in anything. What is worth
saying out loud is that scoring this set proves nothing about those issues.

Feed the set's own scenario names in; the shipped name of a regression scenario is its folder
name:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
printf '%s\n' <the scenario ids from the meta call, one per line> \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" list "$WORKDIR/regressions" --shipped-from -
```

`No regressions under …` or exit 0 — say nothing and run. Exit 1 lists the folders this set does
not carry: **relay it as one line and keep going** — "N recorded regression(s) are not in this
scenario set; a new suite build is what picks them up." Exit 2 names a folder with neither its pin
nor its opener: relay that as its own line — "`regression_<slug>` is unfinished; finish or delete
it before the next suite build" — and still run; a half-written record is not a fault in this set.
Never block the run on it, never record one from here, and never delete a folder to make the line
go away.

**Then check the trace scenarios against this set the same way — WARN, never fail.** Each folder
under `plurai_sim/trace_scenarios/` continues one production conversation cut just before a trace
failure, and it reaches a set only through the delivered batch of a `/simulation-generate-suite`,
exactly like a regression:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
HELD_FROM=()   # an array, so an absent stage record adds no argument in bash or zsh
[ -f "$ART/final_bundle.json" ] && HELD_FROM=(--held-back-from "$ART/final_bundle.json")
printf '%s\n' <the scenario ids from the meta call, one per line> \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" list "$WORKDIR/trace_scenarios" --shipped-from - "${HELD_FROM[@]}"
```

`$ART/final_bundle.json` is the last suite build's stage record; when it exists, each folder that
build held back carries its `held_back` reason and is listed under `held_back` instead of
`not_in_set`. It prints JSON; relay from it, one line per group, and keep going:

- `not_in_set` non-empty (exit 1) — "N trace scenario(s) are not in this scenario set; the next
  suite build carries them." These folders were written after the set was built, or the build
  that made it never staged them; neither says anything about the harness.
- `held_back` entries, by `reason`: `harness_cannot_replay` — "N trace scenario(s) wait for the
  harness to replay a recorded conversation; a new suite build alone does not carry them."
  `over_cap` — "N trace scenario(s) did not fit under the scenario cap at the last suite build."
  `anonymized_posture` — "N trace scenario(s) are held back because this env is anonymized, and a
  trace scenario carries a production conversation verbatim."
  `unfinished` — "`trace_<slug>` is unfinished; finish or delete it before the next suite build."
- Exit 2 names a folder with no pin (`shape: incomplete`) that no stage record covers: relay it
  as "`trace_<slug>` is unfinished; finish or delete it before the next suite build", and still run.

Say nothing when every group is empty. Never block the run on any of this, never convert or write
a folder from here, and never delete one to make the line go away.

## Step 2b — the eval set's own precondition (branch BEFORE the scored run)

**Resolve `$STAGE` FIRST — both this step and Step 4c branch on it, and there is no `RUN_PHASE`
in scope.** `--run-phase` is set 200 lines below from WHO is driving; the same driver check
decides the stage here, and the shell resets between calls, so read the marker rather than a
variable:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
DRIVER="$(cat "$FLOWDIR/.plurai_flow_active" 2>/dev/null || true)"
case "${FINAL_RUN:-}:$DRIVER" in
  1:*|:run|:) STAGE=final ;;
  0:*|:generate-suite|:analyze) STAGE=qualification ;;
  ?*:*) STAGE=qualification
     echo "[stage] FINAL_RUN='$FINAL_RUN' is neither 1 nor 0 — DEMOTED to qualification; declare FINAL_RUN=1 or FINAL_RUN=0" ;;
  *) STAGE=qualification
     echo "[stage] unrecognised flow marker '$DRIVER' — DEMOTED to qualification (a final-stage run inside somebody else's armed window wedges the gate)" ;;
esac
echo "STAGE=$STAGE (flow marker: ${DRIVER:-none})"
```

DRIVEN (`generate-suite` / `analyze` own the window) is `qualification`; DIRECT — the marker
holds `run`, or nothing — is `final`. A declared `FINAL_RUN` (`1` or `0`) outranks the marker. Getting this backwards is not a nuance: reading unset as
FINAL makes the qualification pass STOP inside a driver's armed window, where `ask_guard` denies
the ask it would need, and the gate wedges.

**The default runs the SAFE way, and says when it fired.** `final` is reserved for the two cases
that are certainly direct — the marker holds `run`, or there is no marker at all. Anything else
is a window this command did not open, so it resolves to `qualification`. A catch-all `*) final`
would close an OPEN list against a marker that grows: `/simulation-fixes` re-runs through Step 3 with
`fixes` in the marker, which took the `*` branch and put a FINAL-stage run inside an armed
window — the wedge this very paragraph warns about, arriving through its own default. A demotion
that is wrong costs one qualification-stage run; a promotion that is wrong costs the gate. The
echo is not decoration: a silent safe default is indistinguishable from a recognised marker, and
the marker name is the only thing that says which new driver needs adding to the list above.

A scored run publishes the eval set's verdicts as facts about the customer's agent. An eval that
has never been shown able to FAIL carries no information in its greens, and one nothing can
satisfy carries none in its reds. Ask before spending, not after:

```
eval_coverage_audit(eval_set_id=<.eval_set_id>, scenario_set_id=<.scenario_set_id>, run_stage=<$STAGE, resolved above>)
```

Read-only, no LLM, seconds. Run it whenever `.eval_set_id` exists; with no eval set nothing is
scored and there is nothing to gate.

**`run_stage` is an ARGUMENT to this call, not a reading of its result.** The parameter defaults
to `qualification`, and a qualification verdict is ADVISORY: omit it on a direct run and the gate
answers `blocking: false` over the very findings it would block on, in the words "this *would*
block a final scored run". Same set, same numbers, and only the bound call is about the run you
are one step away from paying for. Pass the resolved `$STAGE` variable — never a literal stage,
which would arm a driven qualification pass inside a window this command did not open (above).

**THE EVAL SET'S FITNESS IS LOOP-INTERNAL. It is never raised with the customer** — not as a
stop, not as a menu of options, not as a caveat, not as a mention. The moment the scenarios are
ready they are presented as good; the customer is not made an adjudicator of the eval set's
internals, and "I would rather tell you than spend a run" is deliberation, not a product
statement. You read this audit, you RESOLVE what it found, and you score. Every decision here —
what the audit said, what you changed, why — goes in `$ART/loop_log.md` and nowhere else:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind note --lane eval \
  --finding "<what the audit established, and what you did about it>"
```

What a blocking value does:

| stage | what a blocking value does |
|---|---|
| `$STAGE=final` | resolve it per the coverage table below, then score. There is no refusal here and nothing to ask |
| `$STAGE=qualification` | proceed — blocking mid-repair is the loop working |

**The COVERAGE axis is a SECOND gate, and it is not about the evals at all.** `guard_coverage_*` asks whether the scenario set SEATS the
premises its judges are guarded on. Pre-run, branch on the audit's `release_gate.blocking` — the
`guard_coverage_gate['blocking']` join; never re-derive it. `blocking`
comes back true only at `$STAGE=final`, which is the other half of passing the stage into the
call. A status that is not `pass` is not itself a refusal: `status: unknown` with
`blocking: false` means the set carries no premise-bound judge at this gate's severity — nothing
was measured short, and there is nothing to do.

| what the coverage axis established | what it is, and what you do |
|---|---|
| the policy is listed in `guard_findings` (`kind: dead_guard` or `dead_flow_scope`) | **an AUTHORING defect, not a draw** — the judge grades NOTHING against this set, so no batch size and no seed seats its premise. It OVERRIDES the `under_planted` row below for the same policy: re-planting there cannot clear it. Dispatch on `cause`, never on the policy's name: `population` → `/edit-evals`, re-key the guard onto a predicate this population can satisfy (an observable one), or replace it with `untriggered_verdict: not_applicable` and state the untriggered case in the rubric. `unsatisfiable_at_scope` → re-scope the policy; NO scenario set cures it. `unclassified` → the audit did not decide which: **read the `finding` text and do not infer either** — the two repairs above are opposite, so guessing one is a scenario-vocabulary fix for a defect that may have nothing to do with the scenarios. `kind: dead_flow_scope` is the worse half — the policy can report a vacuous PASS rather than a visible SKIP, and the gate carries no row for it at all, so `guard_findings` is the only place it appears |
| `blocking: true` and the policy is listed in `under_planted` | **the DRAW came up short, not the judge** — unless the policy is also listed in `guard_findings`, in which case the row above governs and this one does not. Its guard needs a premise this set never plants, so the repair is the SET — and never a regeneration here. The gate's `remedy` is `replant_premise` (the policy declares a complication to steer on, for `planting_basis` `demanded` and `floor_of_one` alike) → the **bounded re-plant** below, then score. Any other remedy → log it and score; the run's banner names the repair |
| `blocking: false` at `$STAGE=final`, and the gate still lists a non-empty `replant` | a situation this set could not seat, named as a "not tested here" risk (`untested_premise`), not a block: the check has no result either way. Still the **bounded re-plant** below, once, then score; a claim that exits non-zero → score the set as it is |
| `blocking: true` on a policy NOT listed in `under_planted` | the flat-floor class: the policy was admitted widely enough to be measured and still landed under the floor. That is an eval to re-scope: an authoring task for `/edit-evals`, never a draw |

**The bounded re-plant (once per run).** It runs on a DIRECT run only — the stage block's flow marker holds `run` or
nothing. Under the suite's window (`generate-suite`) its final run scores the set as it is: the `untested_premise` risk
names what the set could not seat, and the suite carries the premise to the next build's draw. Pre-authorized by the invocation — never asked. Name the run now
(Step 3 runs into this same `$OUT`), save the audit and claim the run's one repair:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$ART/runs/<run-name>"
mkdir -p "$OUT" && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" eval_coverage_audit '{"eval_set_id": "<.eval_set_id>", "scenario_set_id": "<.scenario_set_id>", "run_stage": "final"}' > "$OUT/coverage_audit.json" \
  && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bounded_repair.py" claim --out "$OUT" --report "$OUT/coverage_audit.json"
```

A non-zero exit → score the set as it is. Exit 0 → top it up ONCE, in `/simulation-generate-suite`'s top-up form ("A
top-up is the MISSING SLOTS"): every input byte-identical to the call that generated the set, plus
`adopt_from_scenario_set_id` and `previous_scenario_set_id` = `<.scenario_set_id>`, `eval_set_id`, `count` = the set's
size + the sum of the claim's `replant[].shortfall`, and `steering_demands` = one
`{'kind': 'complication_dealt', 'subject': <one of the row's complications>, 'slots': <its shortfall>}` per `replant` row.
It adopts every scenario of the set and authors only the missing ones.

A refusal, or a demand delivered short (`detail.dataset_metadata.steering_demands`), ends the re-plant: log it and score
the set as it is. Otherwise point everything at the new set:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase final_scenarios --status landed --set scenario_set=<the new set id>
printf '%s\n' "<the new set id>" > "$WORKDIR/.scenario_set_id"
```

Then `relink_eval_scenario_set(eval_set_id=<.eval_set_id>, scenario_set_id=<the new set id>)`, re-audit
(`run_stage=<$STAGE>`), and repeat Step 2's `get_artifact_meta(scenario_set_id=<the new set id>)` listing so Step 3's
`--scenario-id` flags carry the new scenario(s). Then score — whatever the audit says.

**The other flags are read the same way and for the same reason.** `population_readable: false`
collapses every set-dependent audit at once; `targets_audited` / `ledger_audited` / `axes_audited`
/ `intent_audited` false mean the corresponding empty list is an absent measurement, not a clean
one. Any `uncovered_target` / `uncovered_obligation` finding is LOGGED, never a stop and never
said to the customer: the run still measures what it measures, and what it does not is the same
eval-set fitness the rule above keeps loop-internal.

**Read the regressions' owners from the eval set's ledger.** The ledger names the check that owns each
recorded regression, and a folder's `ROLES.md` probe can name a policy the set no longer carries. Refresh
`$ART/regression_owners.json` from the ledger before every run:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$(cat "$WORKDIR/.eval_set_id")\"}" \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" ledger-owners - > "$ART/regression_owners.tmp" \
  && mv "$ART/regression_owners.tmp" "$ART/regression_owners.json"
```

A folder recorded after the eval set was generated has no ledger row and takes its probe, which is correct at record
time. Never delete a saved file to fall back to the probes.

## Step 3 — run

Out-dir lives under artifacts (gitignored, discoverable by `/simulation-fixes`):
`OUT="$ART/runs/<run-name>"` (use the experiment name or `run-<ts>` — but when Step 2b claimed a re-plant, the SAME
`<run-name>` it named, so the run's one repair stays claimed).

**Resolve the runner's REST server URL via the canonical resolver — do NOT inline your
own derivation.** The runner hits the backend's orchestration API (`/threads`,
`/user-turn`, `/finalize`). That API lives at the same base URL as the diagnostic
`/mcp` endpoint — one server serves every surface. The resolver derives the base and refuses a non-loopback URL when
there is no saved login token:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1   # sets SERVER_URL; refuses tokenless remote URLs
```

**Backend-identity assertion (30 seconds that save 45 minutes).** Before invoking the runner,
prove the run target is the backend the MCP server is bound to — a URL split (MCP bound to one
backend at session start, the shell resolving another at bash time) otherwise
surfaces only after the runner spins up:

```bash
# Sources resolve-workdir.sh + resolve-plurai-urls.sh for you ($PLURAI_DIR, $SERVER_URL) and
# compares this shell's backend against the one the MCP server is bound to, field by field.
# Call the read-only backend_identity() MCP tool and paste its object here verbatim — bash
# cannot see the MCP server's URL, so an omitted argument only downgrades this to a warning.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
```

The `STOP(backend-identity)` line names both sides. Explain it to the user in plain product
terms (the run target and the generation backend disagree); a `STOP` on a tenant or store field is a
client config split, never a backend fault. One whose only finding is
`plugin.backend_identity.deployment_differs` is one tenant and store on another build (a rolling
deploy): re-run once it settles, or overrule it per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`.
A different `hostname` alone is a replica and never stops.

**The runner proves the target HOLDS the set** (SDK `0.12.0` or newer; an older runner
surfaces the same split as the 400 described below). Before it starts any scenario, it fetches
the scenario set from its target. A 404 there stops the run with exit 65 (REFUSED), before any spend, and
the message names the base URL and the set. That 404 is the same split, one step later: the runner
points at a different backend than the one that generated the set. The shell and the session
point at different backends: run the `harness-setup` skill, tell the user to start a new session, then
retry; never present it as a backend fault.

> **A 400 naming another backend is a CLIENT config split, not a server fault.** If thread
> creation dies with `No scenario '<id>' in scenario set '<set>' on <host>… your runner is
> pointing at a different backend`, believe it verbatim: the shell aims runs somewhere
> other than the backend that generated the set. The shell and the session point at different
> backends: run the `harness-setup` skill, tell the user to start a new session, and re-run. The sets are intact on the generating backend — do NOT
> conclude a backend store fault, regenerate anything, or run the contact hand-off (`stop_message.py contact --done`) over > this message.

Invoke the runner with the observability env from Step 1 exported:

**Resolve the runner through the client's venv, never bare PATH.** A PATH-found
`plurai-run-simulation` can belong to a different install than the `plurai` the agent's
venv carries (stale version, wrong interpreter). Resolve it off the SDK resolver's
`plurai` binary:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — the client pin lives there; re-source in THIS block
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never fall back
[ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER=plurai-run-simulation
```

(Resolver failure — empty output on an old SDK lacking the ingest CLI — or a resolved bin
dir without `plurai-run-simulation` beside it (pipx shim) falls back to the PATH-found
runner, the pre-existing behavior, rather than dying on a broken `/plurai-run-simulation`
path.) **Exit 3 is the one failure that must NOT fall back:** a user who wrote
`$WORKDIR/.client_pin.json` (`scripts/client_pin.py write`, once, before a comparison) declared the
plugin + SDK COMMITS this workdir's runs must be made with, and the resolver verified — by commit,
not version string — that the loaded plugin or the resolved SDK is not that; its stderr names both
commits. A run made on another runner would look valid and exercise the wrong code, so stop there.

then invoke `"$RUNNER"`. This is INDEPENDENT of the `PLURAI_CHILD_PYTHON` note (see Notes,
"Interpreter shadowing"): the resolver picks the RUNNER's interpreter; `PLURAI_CHILD_PYTHON`
picks the agent CHILD's — two different processes, set separately.

**The launch block starts the run and returns in seconds; the run plays on its own.** A shell call the harness
tracks has a wall-clock limit (Claude Code's background time limit, for one), and a run can take longer, so the run is
not the call. Run the block as an ordinary foreground Bash call: its last
command, `scripts/run_start.py` (the runner follows its `--`), starts the run in a process of its own, tied to this
session, and prints its pid. The run stops within seconds when the session ends, never when a call does. **There is no
`$OUT/runner.log`.** The runner's printed output goes to `$ART/runs/.<run-name>.runner_output`, beside the run dir;
progress shows as the per-scenario artifacts APPEARING under `$OUT/` (`<scenario>/transcript.json`,
`tool_events.json`, and `run_summary.json` at the end). Reading for a log file that nothing creates is how a healthy
run reads as a silent one.

**Then wait on it with `run_wait.sh`**, its poll:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_wait.sh" "$ART/runs/<run-name>"` as a wait in the background, re-run on exit 2
until it exits 0 (the runner exited 0) or 1 (it failed; 5 is no run; 6 stopped from outside: Step 3's exit `6` rule). When it exits 0 or 1 it prints the runner's
output file and its last lines; continue with the next step in that same turn. It only reads files, so starting it
again after any stop (a time limit, a reap) is always correct, whatever the stop notice says. A background task's
status and exit code are its shell's, not the runner's: whatever the notification says, read the run with
`run_wait.sh` first, and go by its exit code. The main thread launches
the run and waits on it: the run outlives any turn. Stop a run
with `kill "$(cat "$OUT/.runner_pid")"`. The block run again with the same flags attaches to the run playing in `$OUT`;
with other flags it refuses and starts nothing.

**`run_start.py` plays the runner through `scripts/run_stage.sh`**, which gives back the runner's exit code (in
`$OUT/.runner_exit`) and, where the overview page is a local file (Copilot and Codex have no Artifact tool), refills
the page while the run plays, with nothing for you to arm; it never prints a line for you to follow.

**The live page — nothing to arm.** On Claude Code `run_stage.sh` starts `run_tick.sh --loop` beside the runner: it
refreshes `flow_progress.json` and the local page within seconds of each new conversation, with no model in the chain,
and stops itself when the run ends. The page is published at the hand-off alone (inside /simulate, once, at the run's
end), so arm no tick: a `run_tick.sh` armed without `--loop` only says to arm no more ticks. Say nothing about it.

You know every value at authoring time (the ids came from Step 2), so **write the command
with LITERAL flags — no runtime shell expansion.** Do NOT stuff ids into a variable and
expand it (`$IDS`, `for id in $IDS`, `${VAR:+--flag "$VAR"}`): zsh does not word-split
unquoted expansions, so the whole list arrives as ONE argument and argparse's error echoes
it back. **NEVER pass the bearer token as an argument** — the runner reads
the token from its environment natively; a secret in argv leaks through
error output and `ps`.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"                 # $WORKDIR — re-source in THIS block
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1  # $SERVER_URL — likewise; an unset one runs against ""
AGENT_MODULE_PATH="$WORKDIR/simulate.py"                                 # or the --agent-module-path argument, as in the first block
AGENT_MODULE_PATH="$(python3 -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$AGENT_MODULE_PATH")"   # absolute, `~` expanded: the runner never gets a literal `~`
OUT="$ART/runs/<run-name>"                                               # Step 2b's <run-name> when it claimed a re-plant; else this block CREATES the run and names the dir (no .last_run yet)
MOCK_URL=$([ -f "$WORKDIR/.mock_service_url" ] && cat "$WORKDIR/.mock_service_url" || true)  # absent in .run_mode=mockless runs
[ -n "$MOCK_URL" ] || { echo "no \$WORKDIR/.mock_service_url — deploy the env, or DELETE this line together with the --mock-service-url one for a mockless run. An EMPTY --mock-service-url is worse than no flag: it OUTRANKS the sidecar." >&2; exit 1; }
EVAL_SET_ID=$([ -f "$WORKDIR/.eval_set_id" ] && cat "$WORKDIR/.eval_set_id" || true)         # absent when nothing is scored
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never fall back
[ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER=plurai-run-simulation
export PLURAI_OBSERVABILITY=recording   # Step 1a chose Experiment or Traces — delete this line on the None branch
source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-stage-flags.sh"             # $RUN_FLAGS — this stage's flags, from typed state; never type them by hand
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_start.py" -- \
"$RUNNER" \
  --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" \
  --scenario-id <id_1> --scenario-id <id_2> \
  --agent-module-path "$AGENT_MODULE_PATH" \
  --out "$OUT" \
  --mock-service-url "$MOCK_URL" \
  --eval-set-id "$EVAL_SET_ID" \
  --no-observability \
  --concurrency "${MAX_CONCURRENT:-4}" "${RUN_FLAGS[@]}"
```

**Those lines are the APPEND-WHEN-RESOLVED set — DELETE the whole line when its value did not
resolve, never conditional-expand it.** The flag lines sit above the always-present
`--concurrency` line precisely so deleting one cannot strand a trailing `\`. Decide each at
AUTHORING time, from the facts Steps 1/1a already established:

| line | keep it when | delete it when |
|---|---|---|
| `MOCK_URL=…` **and** `--mock-service-url "$MOCK_URL"` (they go together) | `$MOCK_URL` resolved (Step 1's `.mock_service_url` precondition) | `.run_mode` says `mockless` — the runner infers the posture from the sidecar. `--mock-mode mockless` is an explicit override, which wins over env and sidecar alike |
| `--eval-set-id "$EVAL_SET_ID"` | `$WORKDIR/.eval_set_id` exists — **this is what gets scored** | no eval set exists yet (the run measures behaviour only) |
| `--no-observability` | Step 1a chose **None** | Step 1a chose Experiment or Traces |
| `export PLURAI_OBSERVABILITY=recording` | Step 1a chose **Experiment** or **Traces** | Step 1a chose None |

The last two are **mutually exclusive and jointly exhaustive** — exactly one survives, because
each branch needs its own IN-BLOCK carrier. `--no-observability` was already one; the recording
branch had none, so it depended on the driver having run Step 1a's separate Bash block, and a
skipped Step 1a meant SDK 0.4.0 defaulted to isolated and the suite silently produced no
experiments. `PLURAI_OBSERVABILITY` sits above the sidecar in the runner's precedence, so it
carries the decision on its own; an unknown environment variable cannot be a usage error on an old runner,
which is what makes it the version-safe carrier. Left un-edited the block ships both, and the
flag wins — isolated, the safe default.

**Do NOT reach for `--observability` instead.** That flag exists only from 0.4.0, `argparse` is
strict, and an unknown flag exits 64 **before a single scenario runs** — the same trap
`--run-phase` is probed for. `PLURAI_OBSERVABILITY` and the `.observability` sidecar are the
declaration channels that work on every version: on 0.4.0+ they enable recording, and on an
older SDK they are ignored while recording still happens by discovery, which is the behaviour
those versions already had. `--no-observability` is the exception and stays in the block on the
None branch — it has existed all along, and it is the one instruction that must survive a stale
SDK.

`--no-observability` is not value-bearing and is **not optional on the None branch**: turning a
sink OFF takes the flag, because the customer's `.env` can enable one behind your back (Step 1).
Deleting it there is the exact leak the off-switch section closes — a block copied without the
line records a scoreless run into whatever production sink that `.env` names, which is the
discovery failure Step 1a's write exists to end. A template that omits a mandatory flag IS the
instruction that runs; prose ninety lines below it demonstrably is not.

**And an EMPTY value is worse than a deleted line** — which is why `MOCK_URL` is guarded on the
line beside its assignment. `$([ -f … ] && cat … || true)` yields `""` when the marker is absent,
the SDK guards the pin on `is not None`, and `""` is not None: it OUTRANKS both the
`.mock_service_url` sidecar and `$NETWORK__MOCK_SERVICE_URL`, disabling the very pin Step 1
calls fatal-when-stale. The run then pre-flights `'/health'` and raises
`httpx.UnsupportedProtocol`, which the pre-flight's `except (ConnectError, ConnectTimeout)` does
not catch — an uncaught traceback before any scenario runs. Stop at the assignment instead, where
the message can say what to do.

`--eval-set-id "$EVAL_SET_ID"` deliberately carries NO such guard, and the difference is the
SDK's own reading: it branches on `if not cfg.eval_set_id`, so `""` and "absent" are the same
thing to it — an unscored run, which the runner announces as `NOT SCORED: no --eval-set-id was
passed`. Empty is loud and correct there; empty is silent and wrong for the mock url, which
outranks a pin. Do not add a guard here for symmetry — a suite that has generated no evals yet
is a legitimate branch, and stopping it would refuse a run the operator meant to make.

`--agent-version <v>`, `--run-name <name>` and `--upload-datasets` are the other
append-when-resolved flags (same rule, same place in the block) — see Step 1a for which of them
this run earned.

**`"${RUN_FLAGS[@]}"` is every stage-derived flag, and `runner-stage-flags.sh` builds it — never write those flags
by hand, and never drop the `source` line or the array as "empty at this stage".** A relaunch, a merge re-run and a
copy of an earlier launch line all re-source the script; it reads only typed state (`FINAL_RUN`, the flow marker,
`RUN_PHASE`, `PLURAI_REPLAY`, `$OUT/run_summary.json`, `$WORKDIR/regressions`, the scan markers, the ledger) and
what `"$RUNNER" --help` advertises, because an older installed SDK does not know a newer flag and `argparse` is
strict: an unknown flag exits 64 **before a single scenario runs**, and the resulting "rc not 0 or 1 with no
error.json" reads under Step 4 as a recording failure on a run that never started. It prints a
`[run-flags] stage=<final|qualification>: <flags>` line, plus a `[run-phase]` / `[trials]` / `[stability]` /
`[replay-plan]` line whenever a flag it would add is missing, and why. A `[run-flags]` line that names missing scripts is
for you, not the user. The array holds the phase
(`--run-phase`), the trials (`--trials`), the stability retry, the replay plan, the drift flags and the overrules.

**The FINAL scored run is 3 independent trials per scenario at the full budget** (`--run-phase=full
--trials=3`, run in parallel unless the runner's `[trials]` line says in turn): a DIRECT run (the window holds `run`, or nothing) is final, and a
driver's run of its FINAL set declares it with `FINAL_RUN=1` at the top of this block and Step 2b's. Every other
run — the qualification pass, a verify re-run — is one sample.

**The runner decides how many children of one harness run at once and prints it** (`[world]` / `[trials]`
lines): `--concurrency` × `--trials`. An external world, `slots` or `harness.single_instance` is leased: the trials stay
side by side and the agent processes take turns on each copy (the `[trials]` line says how many can be alive; a row
waiting for its copy prints `[wait]`). Only when the sandbox's memory cannot hold them do the trials run one after
another, and the run takes about `--trials` times as long. An Init failure
that only appears with sibling trials is a harness fix, not lower concurrency: a scenario that errors at Init
beside its siblings and Inits clean alone goes to the harness repair, under the Python lane's seed-state rule "Safe under
parallel children". Never lower `MAX_CONCURRENT` for it: the trials still run as siblings.

The `[run-phase]` line is the point of that probe, not politeness. An omitted flag degrades **silently and in the
wrong direction**: qualification quietly runs at the 31-minute budget instead of 21, so the fail-fast
gate stops failing fast and nothing says why. Say it out loud, and only when it actually costs
something — a `full` run omitting the flag resolves to the same budget either way.

**A `warning:` line from the knowledge copy is said once, as is**, before the run starts (`runner-stage-flags.sh`
prints it): the agent's knowledge changed in its code after the suite was built, so this run grades answers against
the earlier copy. It is milliseconds, the only check it adds, and it never stops the run.

**`--replay-plan` whenever the workdir has regressions.** A recorded regression is a measurement at its cut: the
plan, not you, says how each one plays — only its issue turn, or the whole conversation (one played from its opener,
one pinned on `anywhere`, an owner that needs whole conversations, a harness that met the cut with less state than
the recording had). Its trials are its samples. Never edit the plan.

**`--overrule` when the plugin's ledger exists.** The runner reads the ledger itself and applies
only its own gates' overrules (the `plugin.` lines are the plugin's); with no ledger there is
nothing to lift, so the flag is omitted.

**`--stability-retry` on a FULL run.** It asks for one second sample per failed scenario, and on a
full run that sample is a **judge replay** (`sample_kind: judge_replay`): the first attempt's own
transcript re-judged by the same eval set — judge tokens only, no agent re-run. A red the judge
reproduces on the same output is corroborated, a decided green it grades both ways is withdrawn
as a judge contradiction, and a replay never reads `stable` (only an agent re-run can license a
redraw). Qualification's second sample is an **agent re-run** (`sample_kind: agent_rerun`), the
redraw question. Current runners take both by default, so the flag is a no-op there; an older
runner defaults it OFF on a full run, which is why this line still passes it. The first attempt
stays authoritative and a passing second sample never rescues a failing verdict. Analytics renders
the result under **Run integrity → Failure stability**.

**`--scan-contract` when a baseline exists.** Both flags or neither — a baseline with no
`--scan-corpus` root re-hashes nothing and the runner records `NOT CHECKED`, which is what two
shipped runs published while a corpus-drift finding sat in `pre_generation_findings.md`. The roots
come from `$WORKDIR/.scan_corpus_roots`, written by `scan_contract.sh` at scan time; when it is
absent (an older scan) the flags are omitted and the run says NOT CHECKED honestly rather than
guessing at roots.

**Set `--run-phase` from WHO is driving, not from habit.** `qualification` when this run is
the gate inside `/simulation-generate-suite` or `/analyze-simulation` (the same
`.plurai_flow_active` driver check that decides the observability posture in Step 1a), which the
driver declares as `RUN_PHASE=qualification` at the top of the launch block; `full` for a direct scored run. It picks BOTH budgets — the per-turn one (**1260s / 21 min
qualification, 1860s / 31 min full**) and the per-scenario TIME budget below (**1500s / 25 min
qualification, 2400s / 40 min full**) — and in qualification a stalled turn does NOT get
re-driven, so the 21-minute wall is really 21 minutes rather than two attempts at it. The gate
exists to expose a stalled agent on a 3-scenario sample; taking an hour to do it defeats the
point.

**A developer whose agent legitimately needs longer turns declares it once, in the
workdir** — `$WORKDIR/.turn_timeout`, a bare number of seconds. Same precedence shape as
`.run_mode`: `--agent-turn-timeout` flag > `NETWORK__AGENT_TURN_TIMEOUT` > `.turn_timeout`
sidecar > the phase default. Because it outranks the phase, a declared budget holds in
qualification too. Do NOT pass `--agent-turn-timeout` from this skill — a flag would
outrank the developer's own declaration, which is exactly the footgun the phase flag exists
to avoid.

**A whole scenario is separately bounded** by `--scenario-timeout` — **the per-scenario TIME
BUDGET**, and the phase picks its default too: **1500s (25 min) qualification, 2400s (40 min)
full**. Running out of it no longer kills the scenario. It **LANDS** it: the dialog ends between
turns, the closing turn is still delivered, `/finalize` runs, the judges grade the turns that
were reached, and the row comes back `result: incomplete` with
`ended_early: time_budget_exhausted` (Step 4b). So the budget expiring costs you the REST of
that conversation, never the whole scenario — and the run still gets a transcript, cells and a
turn count to read it by.

Underneath it a derived hard backstop still cancels a genuine hang (a turn or the finalize stuck
past every stage bound), and that one still produces `harness_scenario_timeout` with no
transcript. Different fact, different owner — see the `error_kind` table in Step 4b.

**Do NOT pass `--scenario-timeout` from this skill.** Same posture, for the same reason, as the
`--agent-turn-timeout` ban above: a flag passed from here outranks the operator's own
declaration, and extending a time budget is a decision about how long the customer is willing to
wait — theirs, not this skill's. The phase default is what this run uses; an operator who wants
more says so on the command line themselves.

**The runner sizes the turn cap; this block passes no `--max-turns`.** It derives the cap from the
scenarios it plays — each one's `estimated_min_turns` floor plus headroom for its `complexity_level`,
one more turn when the harness declares the agent opens, never below 12 — and records the cap and its
source (`max_turns_source`) in `run_summary.json`. A flag outranks that derivation, so it belongs only
on a targeted re-run of a scenario that ended `turn_cap_exhausted` (the TRUNCATION check below), above
the cap that run recorded.

The append-when-resolved flags — `--mock-service-url "$MOCK_URL"`, `--eval-set-id
"$EVAL_SET_ID"`, `--no-observability`, `--agent-version <v>`, `--run-name <name>`,
`--upload-datasets` — **are already lines in the runner block above, with the keep/delete rule
beside each.** They are not a separate step to remember here: this paragraph exists only to say
that the rule is DELETE THE LINE, never conditional-expand it, and that the block is where the
decision is made. Do not re-derive them from this paragraph — copy the block.

**Capture the runner's exit code AND stderr** (`rc` is the code `run_wait.sh` names, also in `$OUT/.runner_exit`;
stderr is in the runner's output file). The exit codes are the SDK's table
(the runner's README): `0` every policy held · `1` the run completed and a scenario failed a
policy (artifacts valid: a finding about the agent) · `64` a bad command line · `65` the run was
refused (a held gate, an unusable target or set) · `11` a hosted mock could not answer and the run was
stopped · `20` the run was stopped before it measured everything: with `aborted.reason: credit` the backend
refused work for credit, with `aborted.reason: capacity` the backend could not take the run, and with `aborted.reason: unavailable` it could not be reached (any other `20` follows Step 4) · `69` transport, notably a dataset-upload /
recording failure · `70` an SDK defect · `77` no usable credential · `10` the backend refuses this
client version (`run_wait.sh` shows it as its exit `7`, below). The runner signals some failures via the **exit code with no `error.json`** —
notably a dataset-upload / recording failure (`69`) and the loud enabled-without-key error from
Step 1a. So the exit code is part of the result, not just `error.json`. This capture is for YOUR OWN classification (Step 5) — never
paste raw stderr into the chat.

**Exit `11` is a stopped run, not a result.** A hosted mock answered with a cause no retry changes (`auth`,
`not_deployed` or `not_a_mock`), so the runner started no further scenario, cancelled the running ones and scored
nothing. `$OUT/run_summary.json` carries an `aborted` block (`reason`, `cause`, `scenario`, `tool`,
`status_code`, `remedy`); it is `null` in every other summary. Report `aborted.remedy` in plain terms and STOP:
no scores, no analysis, no report, no dataset or experiment upload, and no `.last_run` (the runner writes none).
`build_run_json.py` builds no `run.json` from such a summary and exits 11, and it removes an earlier run's render in
the same `$OUT`. After the user fixes what the remedy names (the token, the mock env to redeploy, the url that is
not a mock), run again.

**Exit `20` with `aborted.reason: credit`, `capacity` or `unavailable` is a stopped run, not a result.** The backend refused work for the
organization's credit standing, could not take the run, or could not be reached after the runner's own waits, so the runner started no further scenario and cancelled the running ones. The
scenarios that finished keep their rows in `$OUT/run_summary.json`, but a partial run is no result: no scores, no
analysis, no report, no dataset or experiment upload, no call to `build_run_json.py`,
`render_run_report.py`, `release_verdict.py` or `runs_index.py` (`build_run_json.py` builds nothing from it and exits
20). `.last_run` is not moved: it still names the previous run, never the stopped one, so name the stopped run by its
`$OUT` and never by `$(cat "$WORKDIR/.last_run")`. `run_wait.sh` shows it as "the runner exited 20" with exit 1: read
the summary before reading that as a failure.
`aborted` is `{reason: credit, code, resets_at?, scenario, remedy}` (or `{reason: capacity, code: capacity_exhausted,
resets_at?, reset_source?, scenario, remedy}`, or `{reason: unavailable, cause, scenario, remedy}` with no `code`)
and `not_run_scenarios` lists what did not run. Stop, and say what this prints, verbatim and alone (it is the credit,
capacity or unavailable refusal rule above, so an unavailable-stopped run is a measured stop, never a run-level failure):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $ART — re-source in THIS block
OUT="$ART/runs/<run-name>"                                   # the run that stopped: .last_run does not name it
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never fall back
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" refusal --run-summary "$OUT/run_summary.json" \
  --continue "/simulation-run" --plurai "$PLURAI_BIN"
```

The script reads the kind from `aborted` and writes the first done line itself, from the runner's own count
("4 of 8 scenarios finished, in `$OUT`"). Add a `--done` only for an outcome of this flow that exists outside the run
(an id); never one about the run, which that line already states. Use `/simulate` as the continue command when that
flow drives this run. Never re-run a scenario to see whether it clears,
never read the error as the agent's, the credentials' or the provider's, and never ask for a key or a login: the kind in
`aborted.code` picks the sentence, and the sentence is the whole answer. Exit `20` with any other `aborted.reason`, or
none, is a run-level failure: apply the rc rule in Step 4.

**Exit `7` from `run_wait.sh` is the backend refusing this plugin or its SDK, not a result** (the runner exited `10`). The
call printed the update text and none of the runner's own output: say that text verbatim and alone, the way the error
posture says a version refusal, and stop. No scores, no analysis, no report, no dataset or experiment upload, and no
second run: every call is refused until the plugin is updated. `.last_run` still names the previous run.

**Exit `6` from `run_wait.sh` is a stopped run, not a result.** Something outside this session ended the runner (a
`kill`, a closed shell, the system), or its call is gone with no exit code. The user may have stopped it to save
credits, so never start it again on your own: not under a fresh run name, not as a retry, not to finish the flow. It
is not a transient failure, and no retry rule covers it. As with a credit stop: no scores, no analysis, no report, no
dataset or experiment upload, and `.last_run` still names the previous run. Sanction one question with
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` and ask it in plain words: the run stopped
before it finished, because something outside the run ended it; it scored nothing; and running it again starts from
the beginning and spends credits again. Put `Leave it stopped` first
and `Run it again` second. On `Run it again`, launch it again under a fresh run name. On `Leave it stopped`, or no
answer (a headless driver's `skip`), end on the failure hand-off and name the command the user typed (`/simulation-run`,
or `/simulate` when that flow drives this run) as the way to run it later.

**Re-run hygiene (same `$OUT`).** Before each run the runner clears the scenario dir's TOP
LEVEL — so a stale `error.json` cannot shadow a fresh pass — but it DISPLACES rather than
deletes: only `error.json` is unlinked by name; `transcript.json`, `tool_events.json` and
`final_world_state.json` MOVE into `$OUT/<scenario>/previous_attempt/`, named on the fresh
attempt's `previous_attempt` metadata key. Read a scenario by its TOP-LEVEL files —
`previous_attempt/` is the superseded run, never the current one — and never "clean" a
scenario dir by deleting it, which throws that measurement away.
A runner older than plurai-sdk 0.3.3 UNLINKS those three instead, and nothing here can detect
which you have — so a BARE scenario dir is never evidence that no earlier attempt existed; on
an older runner a re-run that died took the measurement with it. Check `plurai --version`
before reading one as a first attempt.
A scenario's `verdicts.json` is one of the runner's own artifacts: a re-run moves it into
`previous_attempt/` with the transcript and writes a fresh one beside the fresh transcript, so
there is nothing to delete before re-running into the same `$OUT`.
A **whole-set** re-run into an `$OUT` that already holds a finished run is now **REFUSED** by
the runner (exit 65, nothing deleted): that run's `run_summary.json` is the only record of a run
already paid for. Run into a fresh `--out`, name the ids with `--scenario-id` (a targeted re-run
MERGES into the existing summary), or pass `--replace` to discard that run on purpose. The
invocation above passes none of those, so a second whole-set run into the same `$OUT` stops at
exit 65 — that is the refusal, not a run failure.

**Exit 65 with `ERROR: held by gate <gate_id> …` lines is a hold, not a failure:** nothing was
spent. Each line names the finding's authority, remedy and fingerprint, and `$OUT/gate_authority.json`
carries the full findings (`held` lists the fingerprints). Decide each per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`: repair a `security` or `invariant` one; an advisory
one you hold evidence against (the declared `source` line redirected to the mock, a reset the agent
calls itself, read-only tools at file:line) is overruled in the plugin's ledger exactly as a
plugin-local finding is, then this block re-runs. Two egress holds read differently:
`egress.deny_list_incomplete` (the `egress:` block could not be read whole; `evidence` names the
`key`, `index`, `code` and `value`) — fix the declaration when it is the agent's config
(`record_egress.py` owns that block), otherwise overrule with what you verified;
`egress.unguarded_unmeasured` (the declaration load or the guard's fallback probe crashed;
`evidence.phase`, `evidence.exc_type`) — re-run first, and overrule only with a verified reason that
nothing reaches a production host. Never tell the user about the hold or the overrule.
A run that exits 0 can still list fingerprints under `held`: findings the runner filed per scenario
while it ran (a dialog the harness cut holds that one trial's cell, which the row reads as
`incomplete`). The run itself was not refused: route each such row by its `ended_early`
(the table below), never by `held`.

## Step 3b — replay the carried regressions (only when Step 2 listed one as `carried`)

The main run played each carried regression `--trials` times by its replay plan; a fix clears only after more clean
replays than that. This step tops them up to K, into their own directory outside `runs/`, and never moves `.last_run`
or records anything on a platform. Skip it when Step 2's `regressions.py list --shipped-from` printed no `carried`
folder. The same plan decides each folder's shape; there is no second pass.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"    # $PLURAI_PY — replay-plan reads policies_config.yaml through PyYAML
AGENT_MODULE_PATH="$WORKDIR/simulate.py"   # or the --agent-module-path argument, as in the first block
AGENT_MODULE_PATH="$(python3 -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$AGENT_MODULE_PATH")"   # absolute, `~` expanded: the runner never gets a literal `~`
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }
[ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER=plurai-run-simulation
"$RUNNER" --help 2>/dev/null | grep -q -- '--no-run-pointer' || { echo "[regressions] this runner predates --no-run-pointer — the carried regressions are not replayed this run"; exit 0; }
printf '%s\n' <the carried folder names, one per line> > "$ART/regression_carried.txt"
K="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" replay-trials "$WORKDIR/regressions" \
  --carried-from "$ART/regression_carried.txt" --before "$ART/regression_before.json")"
PLAN_FLAGS=()   # a runner without --replay-plan replays every carried folder whole
"$RUNNER" --help 2>/dev/null | grep -q -- '--replay-plan' && \
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" replay-plan "$WORKDIR/regressions" \
    --carried-from "$ART/regression_carried.txt" --owners "$ART/regression_owners.json" --policies "$WORKDIR/policies_config.yaml" \
    --before "$ART/regression_before.json" --seed-fidelity "$ART/seed_resume_fidelity.json" \
    --out "$ART/regression_replay_plan.json" >/dev/null && PLAN_FLAGS=(--replay-plan="$ART/regression_replay_plan.json")
REPLAYS="$ART/regression_replays/<run-name>"
MOCK_URL=$([ -f "$WORKDIR/.mock_service_url" ] && cat "$WORKDIR/.mock_service_url" || true)
source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-gate-flags.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$RUNNER" \
  --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" \
  --scenario-id <carried_1> --scenario-id <carried_2> \
  --agent-module-path "$AGENT_MODULE_PATH" \
  --out "$REPLAYS" \
  --mock-service-url "$MOCK_URL" \
  --eval-set-id "$(cat "$WORKDIR/.eval_set_id")" \
  --trials="$K" --no-run-pointer --no-observability \
  --concurrency "${MAX_CONCURRENT:-4}" "${PLAN_FLAGS[@]}" "${OVERRULE_FLAGS[@]}"
```

- **Exit 7** is the backend refusing this plugin or its SDK: the command printed the update text and the replays did not run. Say it verbatim and alone, then stop; it is not a recording failure and nothing below reads it as one.
- **K is 5, up to 10** — `replay-trials` takes the most clean replays any carried folder's before
  needs (the earlier batch in `regression_before.json`, else the folder's record-time `replays.json`).
- **Always a fresh `$REPLAYS`.** A `--scenario-id` run into an existing directory MERGES, so a retry of this step
  first removes it by that explicit path.
- `--mock-service-url` follows Step 3's rule exactly: delete the line with `MOCK_URL` on a mockless run.
- `--no-run-pointer` keeps `.last_run` on the main run, which `/simulation-fixes` reads; `--no-observability`
  keeps the replays out of every platform. Never drop either.

## Step 4 — collect results

- **A FINAL run's trials:** when `$OUT/run_summary.json` carries `trials`, `$OUT` is trial 1 and each
  non-empty `trials.dirs` entry is a complete run dir — run every read below (the `error.json` sweep,
  the Scores) in `$OUT` and in each listed `$OUT/<dir>`. Only the listed dirs: never glob `trial_*`.
- **Per-scenario failures** = the `verdicts.json` files whose `status.value` is `agent_error` or
  `harness_error` (`status.cause.kind` is the typed kind); the `error.json` beside one holds its
  remedy prose. A re-run moves a previous attempt's files into `previous_attempt/`, so nothing
  beside a fresh transcript is a leftover.
- **Recording / run-level failure** = an `rc` other than `0`, `1`, `11`, or `20` with `aborted.reason: credit`, `capacity` or `unavailable` from the
  runner (`1` is a completed run with a failing scenario, not a fault; `11` and a credit, capacity or unavailable `20` are stopped runs, handled above,
  and so is a signal's 128+n, which `run_wait.sh` reports as exit `6`). If `rc` is neither but there
  are **no** `error.json` files, the scenarios scored but **recording failed** (upload
  error, expired key, etc.) — treat it as a recording failure (Step 5), do NOT report a
  successful experiment.
- **Scores** = each `$OUT/<scenario>/verdicts.json` — the ONLY place a verdict is read from
  (`${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md`; in Python, `scripts/verdicts_file.py`). The transcript
  is the record of the conversation and holds no verdicts; its run facts ride `finalize_record`.
  **This is the FULL cell shape** — a
  `score_run(..., summary=true)` result does NOT carry the `turns` array described below
  (it collapses to `turns_summary` and drops `findings`), so reading a summary-mode result
  against this shape reports zero violated turns on a run that had them. Shape table:
  `${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md`.
  - `status.value` is the conversation's status, decided once: `scored`, `agent_error` (a fail,
    scored 0), `harness_error` (re-run it; out of the score), `incomplete`, `goal_undecided`.
    There are no derived numbers in the file: no `pass_rate`, no name lists.
  - Per-eval verdicts live at `verdicts["evals"]` — a **DICT keyed by policy name** (not a
    list; iterating it yields name strings, not objects — the `.name`/attribute-access parse
    raises AttributeError). Each value is `{state, reason, cell}`: `state` is `pass` | `fail` |
    `not_applicable` | `ungraded`, `reason` names which N/A or ungraded kind, and `cell` is the
    judge's own cell (null for a policy guard-skipped on every turn), carrying
    `status` (`success` | `error` | `inconclusive`), `violated`, `confidence`,
    `agreement`, `reasoning` — **plus, for `turn_based` policies, `scope: "turn"` and a
    `turns` list (one verdict PER GRADED TURN, ascending `turn_idx`). `skipped_turn_idxs`
    is present ONLY when non-empty** — the healthy run omits the key entirely, so read it
    with `.get("skipped_turn_idxs", [])`, never `cell["skipped_turn_idxs"]`.
    - Each `turns` entry is `{turn_idx, value, type, reasoning, violated, status}`
      (+ optional `agreement`, `findings`). **`turn_idx` is 0-BASED** — turn_idx 0 is the
      first exchange. Say "turn 1 (0-based)" or convert, but never render a raw index as
      an ordinal. `len(turns)` can exceed the number of user turns by one: `/finalize`
      also grades the closing exchange.
    - **Counting violations: filter on `t["violated"] and t["status"] == "success"`.**
      `turns` deliberately carries hedged entries with `violated: true` +
      `status: "inconclusive"`; counting those as violations contradicts the entry's own
      `state` in the same file.
    - The top-level cell's `value`/`reasoning`/`confidence`/`status` come from ONE
      REPRESENTATIVE turn — first violated turn, else the first hedged violated-lean turn
      (`violated` + inconclusive), else the last turn. `violated` is NOT representative:
      the server ORs it across all graded turns, so the top cell tells you *that* a policy
      failed and the representative fields tell you only *where it looked*. Reading it
      alone collapses a per-turn policy to one turn's verdict (observed live: language
      mirroring on a 4-turn Hebrew scenario "graded" on the goodbye alone, when the server
      had graded every turn). When `turns` is present, diagnose FROM IT: name which turns
      violated, not just that the policy did.
    - `skipped_turn_idxs` = turns the **applicability guard** gated off: never judged, no
      spend, NOT an error. The error surface is separate — `judging.turn_eval_errors`, keyed by
      STRINGIFIED turn index, meaning every turn_based policy has a hole at that turn. A policy
      gated off on EVERY turn is `not_applicable` / `guard_skipped` with `cell: null` — a parser
      that walks only the cells reports 100% pass and never mentions it.
  - **`verdicts["judging"]["scenario_success_results"]` is the DETERMINISTIC channel.**
    Its own namespace — never folded into `evals`, so it moves no `pass_rate`,
    no policy count, and no eval row. Shape:
    `{declared_criteria: [...], checks: {deterministic: {status: passed|failed|error, label,
    detail}}}`; `null` or absent when the scenario declares no criterion. `status: "error"`
    means the CHECK is broken (an unrunnable expression), never that the agent failed it.
    A scenario can fail its one mechanical assertion while every policy passes, so read it
    per scenario and carry it into `run.json` (Step 4b).
  - **Conversation content:** `chatbot_messages` is the FULL interleaved transcript —
    human and ai entries alternate, so filter on each entry's role/type before treating an
    element as an agent reply. `user_messages` holds DICTS (message objects), not strings —
    `re`/string ops on an element raise `TypeError: expected string or bytes-like object,
    got 'dict'` (observed live); take the content field first. Warn any sub-agent you point
    at `transcript.json` about both shapes.
  - **Scoring contract:** `pass_rate` counts an inconclusive policy as a NON-pass —
    reconcile the headline number against BOTH the `ungraded` evals and the
    `violated` cells; when reporting a scenario to the customer, an inconclusive judge
    whose reasoning leans toward a real defect is surfaced as **"needs review"**, never
    silently absorbed into pass/fail. **Any rendering of `pass_rate` LISTS the
    inconclusive and skipped policies BY NAME** (with the cell's `agreement` vote split
    for inconclusives): the same 2/3 judge split dents the number in one scenario and
    passes silently in another purely on which way the third vote fell, and a headline
    that moves for unstated reasons reads as noise (observed live: inconclusives lowered
    pass_rate with no finding anywhere in the report).
- **TRUNCATION check — a scenario that ran out of turns is not a verdict.**
  The typed ending decides it: a row whose `ended_early` is `turn_cap_exhausted` had its arc
  cut by the budget, so its evals graded a conversation the budget cut off, not the agent.
  Name those scenarios and re-run each alone at a higher `--max-turns` before reading its
  result. Any other ending routes by the `ended_early` table below — a last `target_complete:
  false` under `plan_exhausted` (a push-back on the closing turn) is not a cut.
  `transcript["per_turn_user_step_info"]` is a MAP keyed by the STRINGIFIED, 0-based turn
  index — not a sequence. Read the highest turn as `info[str(max(int(k) for k in info))]`;
  `info[-1]` raises and bare `max(info)` sorts lexicographically (`'9' > '10'`). Parse
  defensively (`.get()` + type checks — the entry shape varies by user-agent class): an ABSENT
  map (older backends omit the key), a highest key below `turn_count - 1` (partial coverage —
  the last turn is not in there), or a non-bool `target_complete` is UNKNOWN, never "complete".
- **`raw_reply` — the simulator's own structured answer for that turn, verbatim.** Present on
  every turn that drew a model reply, INCLUDING one whose output did not parse: that turn carries
  `raw_reply.parse_error` instead of the fields (an absent map there would read as a turn nobody
  looked at). Absent only on a scripted opener. It carries what the model SENT — `played_beat`, `give_up`, `give_up_cause`,
  `target_complete`, `pushed_back` — beside the two raw fields that hold a value the schema could
  not take (`played_beat_raw`, `give_up_cause_raw`), and an `omitted` list naming which required
  keys the provider left OUT of the reply entirely.
  That last one is the distinction to read for, because nothing else records it: **a key the model
  answered `null` and a key the model never sent are the same bytes once a default fills them in.**
  When a delivery ledger and the transcript disagree — the words are in the dialog and no beat was
  credited — `raw_reply` is what says whether the persona reported nothing or the provider dropped
  the field, and those are a scenario finding and a backend finding respectively. Check it before
  attributing an unreported delivery to the simulator's judgement; a run without it cannot tell the
  two apart, which is why the key exists.
- The `Dataset:` / `Experiment:` / `Trace:` link lines, if the runner emitted them, are at the end
  of its stdout — capture them for Step 5.
- `$WORKDIR/.last_run` ALREADY names this run — nothing to do here. `plurai-run-simulation`
  republishes that pointer itself, beside the `simulate.py` it was handed, on any invocation
  that finalised rows; a refusal that ran nothing leaves the previous one alone. One producer,
  and it is not this block: a second copy here is exactly how the pointer went stale, and then
  every `$(cat "$WORKDIR/.last_run")` consumer below — `build_run_json.py`, the report render,
  `ingest-run` — agreed, coherently, about an older run. The behaviour needs the SDK floor
  `scripts/resolve_plurai.py` pins; under an older runner there is no pointer at all and the
  upload refuses the empty value instead of archiving the current directory.

**Spot-check the dominant metric before it becomes the headline.** Whichever policy accounts
for most of the failures is the number the customer will remember, so it is the one number you
verify by hand — a judge's aggregate is evidence of a judge's opinion, not of agent behaviour.
Take the **top failing policy**, pull **2-3 flagged quotes** from it (the cells' `findings`, or
the quoted span inside `reasoning` when `findings` is absent), and check each against the
grounding sources the suite was built from — the corpus, `inputs/knowledge_grounding.md`,
`simulation_prd.md`. Then:

- **They hold up** → report the aggregate normally.
- **They don't** → do NOT lead with the aggregate. Report the count the spot-check confirmed as
  the result, log the discrepancy (`append_loop_log.py --kind note --lane eval`), and hand it to
  `/simulation-fixes` (which does this check per-finding) rather than resolving it here.

Observed live: an operator reported **"ungrounded claims in 6 of 7 conversations"** straight
off `pass_rate`. The real figure was **3 of 7** — half the violations were judge false
positives with no evidence behind them — and the number had to be retracted to the customer.
Two minutes of grepping quotes is cheaper than a retraction.

**Spot-check an eval that measured NOTHING, by the same rule.** `$OUT/eval_signal.md`'s table
classifies every eval from the run's own verdicts: `discriminates`, `insufficient
sample`, `premise-gated`, `graded under owner` (the eval it defers to carries its verdict), `all pass`, `all fail`, `never conclusive`, `never scored`, `judge errored`. Everything but
the first means the eval never came out two ways on this run — one verdict everywhere, or none at
all — so it is not evidence the agent passed: it is equally consistent with a flawless agent, a
broken judge, and a judge that graded nothing, and this run cannot tell those apart. The last three
are never reportable as failures — a judge that hedged, never ran, or crashed says nothing about the
agent in either direction, and `judge errored` is a repair job, not a finding. `insufficient sample`
is the one that is not about the eval at all: under three DECIDED verdicts, coming out one way is
what a small sample looks like when everything works, and the answer is more scenarios — never
loosening the judge until one of the transcripts you already have fails it, which fits the eval to
this sample. Take ONE eval that reached the floor and still came out one way (`all pass` / `all
fail`) and hand-check it exactly as above: read its judge and pull the cell it returned on the
scenario most likely to violate it. **If NO eval reached the floor** — the ordinary shape of a
3-scenario qualification run — there is nothing to hand-check: log the `insufficient sample` rows
(`append_loop_log.py --kind note --lane eval`) and move on. The round belongs to widening the run, not to the
judges.

- **The verdict is right** → it is a real eval this sample never exercised. Log it the same way
  and move on — eval-set fitness never reaches the Run integrity line (Step 5).
- **The verdict is wrong or vacuous** (grading the wrong field, a guard that never opens, a
  rubric no scenario can fail) → that is a BLOCKING eval defect. Do not report the suite's
  aggregate as a pass; route it to `/edit-evals`.

A judge that cannot separate good behaviour from bad is exactly as broken as one that reds a
compliant agent, and it is the failure this pass exists to catch.

## Step 4b — local analytics report (when NO platform results were shipped)

If the run was **not** recorded to a usable platform experiment — observability was
**none**, the key was missing/declined, or the platform is **Langfuse** (no auto-charts,
per the PRD) — generate the local analytics report so the user still gets the analysis
that the platform UI would otherwise give them. (For a successful **LangSmith experiment**,
skip this — its Monitor tab auto-charts the same numbers — unless the user asks for it.)

This step is conditional, but skipping it when it IS owed is not free: Step 4c's
`assert-run-report.sh` refuses to publish a verdict when this step was owed and never ran, so a
missed report stops the run there instead of shipping a gate result with no readable half.

**Define `result` before you count anything — the rule is FIVE-STATE and there is no sixth.**
The attempt's `verdicts.json` `status` decides first; for a `scored` one the verdict is the backend's,
READ off the file's `goal` (the backend's `scenario_verdict`) and carried on each row as `goal` —
**never** re-derived from `pass_rate`. "Did every policy pass" and "did the
agent achieve the scenario's goal" are different questions, and the gap ran both ways: one flaky
style judge failed a scenario that did exactly what was asked, and a scenario whose judges all
died left no failing cell to fail on. For each scenario:

| `result` | condition |
|---|---|
| `error` | its `verdicts.json` status is `harness_error` (our setup or infrastructure failed: re-run it, out of the score), or it was **never scored and carries no verdict** — see below |
| `incomplete` | its status is `incomplete` — a time or turn budget cut the dialog and it LANDED (see below) |
| `not_evaluated` | `goal.status` is `not_evaluated` (nothing owned the goal, its owner reached no conclusion, or the situation the scenario tests never came up), `unclean` (the backend says the measurement did not hold) or `unknown` (no verdict block — an older backend). Neither a pass nor a failure, and in **no rate anywhere** |
| `pass` | `goal.status` is `passed` — the goal was achieved and the server failed the run on nothing |
| `fail` | its status is `agent_error` (the agent crashed anywhere: a fail, scored 0), **or** `goal.status` is `failed` — the goal was missed, **or** `goal.decisive_violations` is non-empty |

A **standard red does not make a row `fail`.** A scenario can be rude and still do what it was
asked; the red is reported per row (`failing_evals`), in the eval table, and in
`policy_violations` — read those, never the row result, to count misbehaviour.

**`passed` is NOT the only field a row branches on**, and reading it alone is its own false
green. The backend publishes TWO axes: `passed` answers "did this scenario achieve its goal", and
`decisive_violations` is every violation `/finalize` FAILED the run on — the catastrophic set
UNION the `terminate_values` blocking set. They diverge in both directions, and each direction
is a non-failing `passed` on a run the server marked failed:

* a **turn-scoped `terminate_values` floor** — its author declared a conversation STOP, not a
  scenario verdict, so the tier derivation never reaches it and `passed` can be `true` with
  every eval cell clean;
* a **catastrophic red beside a `null` goal axis** — `passed` propagates the `null`, because a
  scenario whose goal nobody measured has no verdict to give.

So a non-empty `decisive_violations` is a `fail`, always — and never collapse the two into each
other: `goal_achieved`/`passed` stay the goal answer on the row.

An `error` or `not_evaluated` row additionally carries **`error_kind`** when the failure has a
type, and every row carries **`goal`** — `{status, goal_achieved, passed, goal_source, score,
catastrophic_violations, decisive_violations, measurement_clean, reason}`. `reason` is the one
line that says WHY a row reached its status, and the reasons need opposite repairs: add a
goal-owning eval, fix the judge that died, upgrade the backend, or read the named STOP floor.
It is rendered in the Result cell whenever the rest of the table cannot say it — a `➖` row, or
a `❌` whose violation appears in no eval column.

**`goal_source: none` is narrower than it reads.** It means the eval set declared NO owner at
all. An owner that existed and ABSTAINED is `target_judge` with `goal_achieved: null` — still
measurable, so it stays in the N/M denominator and reads as a shortfall, not as a scenario
nothing was asked of.

The block also carries `polarity_untyped` / `target_polarity` / `polarity_gated` /
`unclassified_violations`. None of them is a verdict — counting one as a red would manufacture a
failure — and the two that say a measurement was incomplete (`unclassified_violations`,
`polarity_untyped`) are named in `goal.reason` rather than scored.

**`incomplete` is not a softer `fail`, and never a pass.** The per-scenario time budget no
longer kills a scenario: the dialog ends between turns, the closing turn is still delivered,
`/finalize` runs, and the judges grade the turns that were actually reached. So the row has a
transcript, real cells, a real score and its true turn count — but over a CENSORED
conversation. `pass` would certify work the dialog never got to; `fail` would publish the agent
failing obligations its conversation never reached. It goes in **no rate denominator** —
`totals.passed`, `totals.failed` and the scores all exclude it, `totals.incomplete` counts it
(absent, never zeroed) — and the report renders it `⏳ incomplete` with the turn it reached.

**Never GRADED is never failed — and never `incomplete` either.** A scenario can finalize with
no conclusive verdict at all — the judge job crashed, `/finalize` returned before scoring.
Reading that as `pass_rate` 0.0 renders it as a FAILING scenario and lands it in
`totals.failed`, reporting the agent failing work the harness never judged. It is `error` (no
score, no eval cells counted, named on stderr). The two states are decided by whether VERDICTS
EXIST, never by the reason alone.

<!-- BEGIN shared:run-vocabulary owner -->
<a id="error-kind-vocabulary"></a>
**THE `error_kind` VOCABULARY — stated once, here.** `/simulation-fixes` and `/analyze-simulation` answer from this same table; they do not restate it. Three enumerations that must stay in sync is how two of them ended up naming two kinds while the aggregator counted three.

| `error_kind` | what actually expired | the knob that fixes it |
|---|---|---|
| `harness_turn_timeout` | one turn outran the per-turn budget | `$WORKDIR/.turn_timeout` — only when `error.json` shows the agent's own work, not a pending simulated tool, used the turn |
| `harness_scenario_timeout` | the DERIVED hard backstop fired — a turn or the finalize hung past every stage bound the run declares. **Not the scenario time budget**, which lands the scenario `incomplete` instead | nothing on the clock — read `error.json`; something genuinely hung |
| `harness_finalize_poll_timeout` | the eval fan-out outran its poll wall — the TURNS were fine, the JUDGES were slow | `NETWORK__FINALIZE_POLL_MAX_WALLCLOCK_SECONDS` |
| `harness_seed_state_contract` | nothing expired: the harness's `seed_state` hook could not be called as the seed contract requires (missing on a trace-resumed scenario, or a provably wrong arity) — a TEMPLATE defect, never the agent. A return value that is not a mapping voids nothing: a fresh scenario never reads it, and a resumed one records it as a reduced restore | `simulate.py`'s `seed_state(initial_agent_context, initial_world_state, resume_seed=None)`; run `verify_simulate.py` |
| `harness_fault` | nothing expired: the harness voided its own row — it raised `plurai.HarnessFault`, or made a world write the mock refused (a key no entity holds, or another type's entity) — never the agent | the remedy `error.json`'s `error` ends with (after its ` — `): one that names the plugin, plurai or claude-agent-sdk is an operator step — never edit `simulate.py` for it; otherwise fix the harness write or raise it names, in `simulate.py` or its mock storage: an existing entity is written at the key `plurai.world_state.entities` holds for it, a new one carries its `_entity_type`; then re-run |
| `harness_mock_runtime` | nothing expired: the backend's mock runtime could not generate a mocked tool's answer — the BACKEND's, never the agent's or the harness code's. An `error.json` that carries `credit` is the `backend_credit` row, not this one | `error.json`'s `transient`: `true` → re-run the scenario; `false` → re-running will not help, it is the backend's to fix (`cause` says why). A `false` void BLOCKS the final run's `release_gate` even when another trial of the scenario completed (a deterministic fault leaves a biased survivor sample) with `remedy: fix_env_rerun_scenarios`: fix the env per the run's `scenario_run_failed` finding (its `suggested_action`), then re-run only those scenarios — Step 4c's bounded repair, never a re-run alone. This row wins over any `remedy: rerun` or re-run banner |
| `harness_mock_unreachable` | nothing expired: the runner could not reach the deployed mock (at scenario setup, or while capturing the final world) — never the agent's | depends on the SDK's own typed `raised_at` on `error.json`, never `turns_reached` (a pipeline-shape hop never increments it, so it cannot tell the two raise sites apart): `raised_at: 'init'`: re-run the scenario; if it repeats, recover the deployment as Step 0 does for an unreachable URL (switch to the env, else `deploy_env(env_id=<id>)` + the URL rewrite); an agent with nothing to mock runs `--mock-mode mockless`.<br>`raised_at: 'finalize'`: the mock stopped answering after the run started; re-run the scenario; if it repeats, recover the deployment the same way. No mockless-posture check here: the agent already dialed mocked tools |
| `no_applicable_policies` | nothing expired: the guards gated off every policy in the set (minted by the aggregator, not the runner) | the GUARDS — `/edit-evals` |
| `agent_raised` | nothing expired: a raise in the AGENT's own code, including its own client's error status and its own turn budget. The row is `fail`, never `error`; the provider (401/403/404, 429, 5xx), a transport failure or the model runtime failing the call is `provider_infra`, and a raise from the harness (`simulate.py` and the helpers beside it) or plurai, or a graph recursion, stays untyped | the agent code `error.json`'s `error` names |
| `backend_http` | nothing expired: the Plurai backend returned an error, so this scenario is not counted (`error`, never `fail`) | `error.json`'s `transient`: `true` → re-run the scenario; `false` → the report names the `status` and the backend's detail: a 401/403 is the Plurai credentials to check, a 404 opening a thread the scenario or eval set ids this run was given, any other (a 404 on a thread the backend created included) a matter for Plurai's channels, support@plurai.ai or Discord (send them the run dir) |
| `backend_credit` | nothing expired: the backend refused work for credit (`error.json` carries `credit: {code, resets_at?}`) — the organization's credit standing, never the agent, the credentials or the backend's fault. A 402 that carries a `credit` block is never `backend_http`, and a mock runtime fault whose `error.json` carries one is never `harness_mock_runtime`'s "re-run" or "the backend's to fix" | none: the run stops at the first one (exit `20`, `aborted.reason: credit`) and the stop message says what the user does next. Never re-run the scenario |
| `provider_infra` | nothing expired: the agent's LLM PROVIDER or model runtime refused or failed its call (a 429, a 5xx, a dropped connection, a rejected key or model, the CLI missing), so this scenario is not counted (`error`, never `fail`) | the failure `error.json`'s `error` names: fix the agent's key, model or runtime if it was rejected or missing, then re-run the scenario |

**A turn failure after the dialog started is LANDED, not errored, on a runner that lands it:** the row has a transcript and verdicts, `ended_early` names the reason (`agent_raised`, `agent_recursion_budget`, `provider_infra`, `turn_budget_exhausted` — see the `ended_early` table), and `turn_failure` (`{turn_idx, closing, kind, exception, error}`) names the failed turn; `closing: true` means the dialog was graded one reply short. Likewise a scenario whose tool calls the mock never answered is graded on such a runner, and run integrity names it under `mock_unanswered_scenarios` instead of an `error_kind`.

The three timeout `harness_*` kinds all mean a SIMULATION budget expired, so they are reported as such and never as an agent defect; they stay inside `errored` and out of every rate, because they still scored nothing. **All three cancel the attempt before a transcript exists** — `transcript.json` is written from the finalize result, so the finalize-poll case has no transcript either, even though its turns completed. There is nothing to read and nothing to reproduce; a finding about what the agent did there would be invented.

**The `harness_*` namespace is OPEN and MUST NOT be prefix-matched.** The runner reads each kind's slug off a class attribute, so it grows whenever a new failure class declares one. Match the three NAMES above, plus `harness_seed_state_contract` and `harness_fault` — harness faults of a different shape (nothing expired: the template's `seed_state` hook could not be called as the seed contract requires, or the harness voided its own row). A prefix rule would launder a future harness kind that is not a budget at all — a dead mock, say — into "hit the turn budget" and hand the reader a stopwatch to fix it; `build_run_json.py` implements exactly this rule, counting the three and reporting any other `harness_*` by name as an untyped harness fault.

`no_applicable_policies` is the one the aggregator mints: the scenario's `verdicts.json` has its guards gating off EVERY policy in the set, so there was nothing left to judge. **Nothing crashed and there is no `error.json`**; the row carries the gated policy names in `skipped_evals`, which on this path is the only record they exist at all (the live run predates the guards, so the transcript names them nowhere). The report renders it `🚫 no evals apply`, not `💥 error`. If this is not what you intended, the fix is in the GUARDS — /edit-evals — never in the agent.

`error_kind` is absent when the failure has no type: an older runner wrote no `kind`, so absent means UNKNOWN, not "not a harness fault".

<a id="ended-early"></a>
**`ended_early` — a DIFFERENT axis, and never an `error_kind`.** A row (of any `result`) carries `ended_early` when the dialog stopped for something other than the simulated user reaching its goal. That scenario finalized, scored, and has a transcript — nothing errored. What it does NOT have is the rest of the conversation, so every obligation the dialog never reached scores as unmet, and reading that as the agent's failure is exactly the misreading this field exists to stop.

**No reason decides a `result` here.** Whether a cut makes the row `incomplete` is its `verdicts.json` status, decided once by the writer; every reason says only how to READ the row — which is why this axis rides the row rather than the counters.

**ONE fact, TWO producers — read BOTH, because neither is complete.** The SERVER writes `finalize_record.dialog_outcome` = `{ended_early, turn_count, max_turns}` inside every transcript; `ended_early` is **present and `null`** on a normal ending (so a missing block means "producer too old", not "ended normally"), and only this side can report `policy_terminated`. The CLIENT writes `metadata.ended_early` **only when non-null**, and only this side can know `time_budget_exhausted` and `adapter_degraded` — on the all-degraded case it deliberately supersedes whatever the server said. `build_run_json.py` takes the client's reason where it has one, the server's otherwise, the server's `turn_count`/`max_turns` always, and SAYS so on stderr when the two disagree.

**The vocabulary is OPEN** — two producers, independent release cycles, each relaying a string it did not mint — so name what you see; never filter on a fixed list, and never map an unknown reason onto a known one's remedy.

**Each row's `ended_early.route` (`owner`, `remedy`) is the runner's one routing for that row — state it; the table below explains the reasons.** A route overrides the table (a voiding harness fault, a push-back).

| `ended_early` | who it routes to |
|---|---|
| `user_gave_up` | the SCENARIO (or what the agent refused) — the simulated user declared its goal unreachable and stopped trying |
| `turn_cap_exhausted` | the run budget — raise `--max-turns` |
| `time_budget_exhausted` | **the OPERATOR's decision, not a defect** — the per-scenario time budget ran out, the dialog LANDED between turns and was graded to the turn it reached. The row is `result: incomplete`. Extending the budget is `--scenario-timeout`, and it is the user's call |
| `turn_budget_exhausted` | **the run's budget, not the agent** — one agent turn ran past the per-turn wall; the dialog LANDED and was graded to the turn it reached. The row is `result: incomplete`. Raise `$WORKDIR/.turn_timeout` only when a turn of this agent is legitimately that slow |
| `provider_infra` | **infrastructure, not the agent** — the agent's model provider refused or failed a call mid-dialog; the dialog LANDED and was graded to the turn it reached. The row is `result: incomplete`. Check the provider key, quota and status, then re-run the scenario |
| `agent_raised` | **the AGENT** — its own code raised on a turn mid-dialog and produced no reply; the dialog LANDED and the judges graded the turns before it. The row's `turn_failure` names the exception |
| `agent_recursion_budget` | **the AGENT, unless its tools were silent** — its graph hit its recursion/step limit mid-dialog; the dialog LANDED and was graded to the turn it reached. `turn_failure.pre_crash_tool_activity` says whether its tools were answering before the loop: an empty mock can loop a graph too, and that is the ENV's |
| `adapter_degraded` | the RUNNER emptied every turn, so nothing about the agent was measured. Three owners look like this: the agent said nothing, `invoke_agent` returns its reply where the SDK does not read it, or the SDK could not read a shape `invoke_agent` DOES return (`surface: sdk` — file it, do not edit `simulate.py` around it). An agent whose answer is a render verb is the third case, not the first |
| `policy_terminated` | the EVAL SET — a policy's `terminate_values` stopped the run; intended, or a threshold for `/edit-evals` |
| `sim_degenerate` | **the backend, not the agent** — the simulated user produced no turn (its draw and its one re-draw came back unusable), so nothing about the agent was measured. Re-run the scenario; if it repeats, look at the scenario's persona and declared language |
| `plan_exhausted` | **nobody, by default — this one is not a truncation.** The SCENARIO's schedule was fully delivered (every premise beat played, every planted question asked) and the server stopped rather than pad to the cap; the designed-arc floor still wins, so a plan spent below `min_turns` keeps running. Do NOT raise `--max-turns` for it. If a policy went unmet, ask whether the dialog ever ASKED for it — an obligation nobody requested is a SCENARIO coverage finding, not an agent failure. A row whose `ended_early` carries `disputed_turn` ended on the simulated user pushing back: route it to the AGENT — read that 0-based agent turn and its answer to the push-back |
| `agent_ended` | **the SCENARIO's design, not a defect** — the AGENT itself ended the conversation, by returning an `end_conversation` action on its turn. Intended for a kiosk / IVR / support flow that closes after a transaction; the dialog had everything it was going to get, so ask whether this scenario EXPECTED the close (or whether the agent hung up on an unfinished task) before reading its unmet obligations as agent failures — except the scenario target when the close came BEFORE it arose: that row FAILS (`goal_source: agent_closed`) and routes to the agent. Not a budget — do NOT raise `--max-turns` |
| `handed_off` | **the boundary of what this bench measures** — the AGENT transferred the conversation to another party (a `handoff` action on its turn). Everything after the handoff happened somewhere the simulation cannot see, so the measurement stops where the handoff did; obligations the transcript never reaches belong to the party that took it — except the scenario target when the close came BEFORE it arose: that row FAILS (`goal_source: agent_closed`) and routes to the agent |
| `replay_cut` | **the run's own choice, not a finding** — a single-hop regression replay (its replay plan, `--replay-plan`) stops by design after the turn under test; its end-of-run checks were graded on that one turn, and a check that needed later turns says so instead of scoring |
| anything else | say the reason verbatim and read the transcript; the list above is what is known TODAY |

`run.json` carries `{reason, user_turns?, max_turns?}` per row and rolls the reasons up under a top-level `ended_early` block; `analytics.md` renders an **Ended early** column and one headline line per distinct reason. Both are absent when nothing ended early. **`user_turns` is the server's /user-turn CYCLE count** — one LOWER than the Turns column whenever a closing-turn reply was delivered (that reply is in `turns` but is not a `/user-turn` cycle), which is why it is labelled rather than printed as "turns"; do not report the difference as a defect. `run_summary.json`'s `ended_early_scenarios` is the runner's own third copy and covers rows whose artifacts are missing.

<!-- END shared:run-vocabulary -->

<a id="user-world"></a>
**What the simulated user could see — `dialog_outcome.user_world`.** Every transcript's `finalize_record.dialog_outcome` also carries `user_world` (`observed`: the simulated user saw the surfaces `world_state.user_surfaces` declares; `not_declared`: the bench declares none; `unobservable`: a turn's world could not be captured), `user_world_reason`, `delivery_needed` (the scenario needs the simulated user to receive something), `user_actions` (`applied` / `refused` / `unexecutable`: the acts the simulated user took on the screen) and `wrong_recipient`. A scenario that NEEDS the user's world, and whose world could not be shown or whose act could not be carried out, is not scored: its `user_sim_integrity.run_valid` is `false`. Report it as not measured and quote `user_sim_integrity.invalid_reason` as written: it names what was missing and the one remedy, so never state another.
`dialog_outcome.wrong_recipient` is not a void: the agent sent the needed value to the wrong address. Route it to `/simulation-fixes` as an agent defect.
Guard: `unobservable` on a scenario that needed nothing leaves its verdicts standing.

`totals.passed` is the count of `pass`. Strict by design: `pass_rate` already counts an
inconclusive policy as a non-pass, so a "pass" means every policy cleared.

**Because the rule is strict, the headline is never allowed to travel alone.** A low passed
fraction beside a high mean eval score is technically true, reads as catastrophic, and
conveys total failure. The totals line always carries the mean score and the violation count alongside it
(the renderer enforces this); never quote the passed fraction on its own, in chat or in a
report.

**ONE denominator convention, both tables: a rate divides by the VERDICTS THE SERVER
REACHED, and everything it did not reach is reported beside the number, never inside it.**
An eval's rate is over its conclusive cells (`passed + failed`); a scenario rate is over the
scenarios that scored a WHOLE dialog (`scenarios − errored − incomplete`). `status` per cell
and the presence of `eval` per scenario are what decide it — nothing here re-reads a rubric or
a reasoning text to classify a result.

**First, drop the scoring-time COVERAGE ROLLUP beside the artifacts if you have one.** Whenever
a `score_run` result carried `coverage_debt` (the backend's determination that a policy graded
NOTHING across the set — a guard false on every scenario, a suppressed class, a probe nothing
grades), write that result — or just its `coverage_debt` list — to `$OUT/.coverage_debt.json`
before building. The aggregator copies it into `run.json` and the report renders a **Graded
nothing** panel from it: policy, the backend's own reason, and what would make it gradeable.
Skip the file when no rollup exists — the panel is then absent, which is the honest state, and
never re-derive the list from the Eval-signal table (that answers a different question: whether a
judge DISCRIMINATED on this sample, not whether it graded anything at all).

**First, re-pin a regression whose owner cannot decide at its cut.** `repin` reads each summary that
replayed regressions (`$OUT/run_summary.json`, and Step 3b's `$REPLAYS` / `$FULL` ones when they ran) and
pins such a folder on `anywhere` in its `replays.json`; the aggregator and every later replay clear it there:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" repin "$(cat "$WORKDIR/.last_run")/run_summary.json" \
  --regressions-dir "$WORKDIR/regressions" --owners "$ART/regression_owners.json"
```

Build `$OUT/run.json` with the shipped aggregator — run it, don't hand-roll it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $PLURAI_DIR / $WORKDIR — re-source in THIS block
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"    # $PLURAI_PY — the aggregator reads plurai.config.yaml's tool declaration through PyYAML
OUT="$(cat "$WORKDIR/.last_run")"                             # likewise; the resolver does not export it
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/build_run_json.py" "$OUT" \
  --run-name "<run name>" --dataset-name "<DATASET_NAME>" --workdir "$PLURAI_DIR" \
  --eval-set-id "$(cat "$PLURAI_DIR/.eval_set_id")" --gt-revision <N> \
  --agent-version "<version>" --stage "<the STAGE Step 2b printed>" --local-only && \
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/runs_index.py" write "$WORKDIR" && \
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/artifact_deps.py" record "$WORKDIR" --run "$(basename "$OUT")"   # indexes this run, then records the harness, env and corpus it ran on
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" retire "$OUT/run.json" --before "$ART/regression_before.json" \
  --regressions-dir "$WORKDIR/regressions" --retired-dir "$ART/retired_regressions" --ledger "$ART/regression_clean_draws.json"
```

**`retire` follows every `run.json`.** A regression retires when its replays cleared on the SDK's own clear verdict —
nothing caught in at least the clean replays its before asks for, gathered across scored runs — whether or not a fix
was applied; there is no age-out and no cap. Each retired folder moves to `$ART/retired_regressions/` with a
`retired.json`; to undo one, move the folder back and delete its `retired.json`. First, every folder `run.json` marks
`hollow` (its replays could not measure its issue) moves to `$ART/dropped_regressions/` with a `dropped.json`: it
frees its slot, its clean draws are cleared, and its finding can be recorded again under the same name, starting at
zero; the run report says it was removed, never a result for it. A run stopped early or missing scenarios drops nothing.

**When Step 3b ran, add `--regression-replays "$ART/regression_replays/<run-name>"`** to that call (top-level
`run.json` only, never a trial dir): `run.json` then carries `regressions` — each carried folder's owner and
its caught counts at the cut. The checks behind them and their rates go to `$OUT/regressions_detail.json`,
a local file nothing indexes or uploads; `run.json` never carries them.

**`operator_identity_exposed` is the plugin's own check, graded by this build** (`scripts/operator_identity.py`; the
markers `run_stage.sh` captured into `$OUT/.operator_identity.json`): an agent failure, never an eval to edit. Its
findings name the KIND of marker; never copy a marker value into any artifact. A path inside the scenario's own working
folder or shell home (the run's `host_effects.cwd` / `.shell_home`) is the agent's workspace, not an exposure.

**Exit 6 is a refusal, not a crash:** `$OUT` is the recording the eval lane's pin names and the
harness that produced it has since changed, so the script writes `run_stale.json` (both
fingerprints) instead of `run.json` — re-run the scenarios; nothing in that dir is evidence about
the current harness.

**`--workdir` IS NOT OPTIONAL, AND `--run-mode` IS NOT A SUBSTITUTE FOR IT.** It carries TWO
independent facts, and dropping it loses the second one silently even when `--run-mode` supplies
the first:

1. the MOCK POSTURE (`.run_mode`), which `--run-mode` can indeed state instead; and
2. the agent's TOOL DECLARATION (`plurai.config.yaml → tools:`), which nothing else can. Without
   it every flow family that saw no tool activity is attributed `unobserved` and the report tells
   the reader to "have the harness report `agent_state.tool_trail`" — on an agent that declares
   `tools: {}` that is an instruction to instrument something which does not exist.

The build now says so on stderr ("no --workdir — the tool declaration is UNKNOWN") and stamps
`declared_tools_unknown` into `run.json`, so the renderer reports "the declaration was never
read" rather than accusing the harness. **Read that note.** Passing `--workdir` is what makes it
go away; passing `--run-mode` is not.

(Resolve `$OUT`/`$PLURAI_DIR` in the SAME Bash call — the harness resets the shell between
calls. `--workdir` is also where the eval lane's pin lives — see exit 6 above; without it a LOCAL run — where every tool call is reconstructed from
the transcript because there is no mock to log it — renders a "the agent bypassed the mock"
alarm. **Nothing ever WRITES `connect`** — the marker exists only for the postures
`bench_manifest.py run-mode` stamps (`mockless`; `local` on older workdirs) — so a normal deployed-mock run has no
marker at all and the script's absent-marker note on stderr is that normal state, not a fault.
On a run that used the DEPLOYED mock, add `--run-mode connect` and the posture is stated rather
than inferred from absence; `--run-mode local|connect` is equally how you stamp a run that did
not come from this workdir. Never pass it against a `mockless` marker — the flag WINS over the
marker, and a stamped `connect` on a no-mock run re-arms the bypass alarm. With neither, the
key is omitted rather than guessed. It is ORTHOGONAL to `--local-only`, which is
about the observability platform. Drop `--local-only` when a platform DID record the run. Pass the `eval_set_id` +
`gt_revision` pair the run was actually scored against — they stamp the render, so a later
rescore (its `verdicts.json` `judged_against` carries the same pair) can be compared against it; without them a
re-scored run shows its pre-edit verdicts with nothing to reveal that it is stale, and
`--gt-revision 0` is a real revision, not "absent".)

**Scenario discovery can only see scenarios that WROTE something**, so one that died before
its first artifact is invisible to it: a 7-scenario run that lost 2 would report "5 scenarios,
5 passed" — a clean 100% on an incomplete run. The aggregator therefore also reads the runner's
own roster, `$OUT/run_summary.json` (`total` + the per-scenario names, written from the same
counters the runner's exit code comes from), and on a shortfall it shouts on stderr AND records
`totals.missing_scenarios`, which the render names on its headline. Pass
`--expected-scenarios N` only when that file is absent or you know better — never invent an N.

The aggregator reads each `$OUT/<scenario>/` dir — its verdicts from `verdicts.json` only, the
conversation from `transcript.json` / `tool_events.json`, remedy prose from `error.json` — and
writes `$OUT/run.json`:

- `totals` — `scenarios`; the GOAL axis `goals_achieved` / `goals_measurable` /
  `goals_unverdicted` (always present, because a `0` there is a real "we looked and nothing
  owned a goal"). **That axis is counted off each row's `goal.goal_status`, which reads
  `goal_achieved` and nothing else; the row's own `result` is the CONVERSATION verdict, the SDK's
  (below). They diverge exactly where it matters — a scenario that did what it was asked AND
  failed a critical or required check is `1` on the goal axis and `fail` on the row. Never read
  the goals fraction off `result`, or one red (including a false one) zeroes a number the report
  presents as independent.**
  Then `passed`/`failed`/`errored`/`not_evaluated` (the five-state rule above);
  **the scores, the SDK's and never the plugin's**: the aggregator first runs `plurai scores refresh`
  (the run's `run_summary.json` → `scores`, recomputed from every `verdicts.json`), and each row's
  `result`, `score` (0-100) and `fail_reasons` (`low_score` · `critical_failure` · `failing_tier` ·
  `goal_missed` · `crashed`) are its attempt's entry, copied; `totals.suite_score`,
  `totals.agent_verdict` (`ship` · `dont_ship` · `no_verdict`) and `totals.scenarios_failed` come
  from the same block, which `run.json` also carries whole under `scores`. A `harness_error`
  attempt is out of the score and re-run; every other status is scored as the SDK states it.
  Without a block (no SDK offers `scores`) no row is scored — never a score computed in its place;
  `missing_scenarios` (present only when the run declared more scenarios than
  wrote artifacts — see the roster note above), `harness_timeouts` (present only when at
  least one scenario carried a timeout `error_kind`; a subset of `errored`, never added to
  or subtracted from any denominator), `policy_violations` (conclusively violated cells ONLY —
  `violated and status == "success"`; inconclusive and errored cells are non-passes but NOT
  violations), and the tool counters from `tool_events.json`: `tool_calls`, `tool_errors`
  (dispatch failures — the call never went through — or a request it made failed, charged once to the
  call it served), `tool_refusals` (ok dispatch, an
  error-shaped payload envelope — a DIFFERENT finding: a tool that correctly refused is not
  a broken dispatch), `zero_result_tool_calls` (ok calls whose payload is an empty/zero-row
  shape), and `tool_calls_recovered` (`_recovered: true` records reconstructed from the
  transcript rather than logged by `/mock/execute` — on a CONNECT run, one dominated by these
  is a run whose tools bypassed the mock; on a LOCAL run it is simply how the in-process mock
  works, which is what `run_mode` tells the renderer). All the tool rates share the one denominator (`tool_calls`), per
  the convention above, and each count is reported beside the others, never inside them. The one exception is the
  bypass headline: when `plurai.config.yaml` declares a tool `mode: real`, it divides `tool_calls_mocked_recovered`
  by `tool_calls_mocked` (the calls to the other tools — a real tool never passes through the mock). A tool whose
  config entry declares `serves:` is a backend request the agent's own code makes, never the agent's choice: its rows
  leave every count above (a failed one is one `tool_errors` on the call it served, per turn) and are recorded in
  `tool_calls_backend` (internal, never rendered); `tool_calls_mocked`
  and the dead-tool reading still count them.
- `evals` — ONE row per policy in the EVAL SET, not one per policy that happened to be
  judged (a policy gated off on every scenario still gets a zero-verdict row — without it
  the eval simply vanishes from the report, and a judge that graded nothing is precisely
  what the Eval-signal table exists to surface). The cell states are counted apart —
  `passed`, `failed`, `inconclusive`, `errored`, `skipped`, plus `not_applicable` and
  `not_measured` (present only when non-zero), routed by the backend's vendored `verdict_axis`
  block — and `total`/`pass_rate` divide by CONCLUSIVE verdicts (`passed + failed`) only —
  `pass_rate` is **omitted** on a row that decided nothing, since a 0 there reads (and sorts)
  as a judge that failed everything.
- `scenarios` — one row per scenario: `result`/`goal`/`score`/`turns`, the tool counters (incl.
  `tool_calls_recovered` — the report annotates a PARTIALLY observed cell, and headlines the
  run when they dominate, except on a declared local run), `tool_events_unread` (present ONLY
  when the runner wrote a literal `null` `tool_events.json` for that row — the tool channel was
  DECLARED unread, so that row's `tool_calls: 0` is an absence of observation and NOT a measured
  zero; the report renders the cell as `0 (channel unread)` with a legend naming the rows, and
  keeps the Tool-calls column even when every count is zero), the
  `deterministic` verdict verbatim (its own channel — it never moves `pass_rate`),
  `failing_evals` (conclusively violated only, turn-annotated for `turn_based` policies:
  `"language_mirroring (turns 1,3 · 0-based)"`), `inconclusive_evals` (with `agreement`
  vote splits), `not_applicable_evals` (the cells on the `not_applicable` / `not_measured`
  axes, reason-annotated), `deferred_evals` (graded under the eval each `defers_to`; never
  inconclusive) and `skipped_evals` named per the doctrine, and `needs_review` — a
  lens on the `inconclusive_evals` and `not_applicable_evals` cells whose majority leaned
  violated, never a further state.
- `scenarios[].trail_signature` — **the scenario's TOOL PLAN, and the one fact a cell delta may
  never be read without.** Each dispatch as `tool(argument keys)` in order, with
  `trail_signature_mode` naming the normalization (a run.json outlives the version that built
  it), `trail_signature_args` carrying the same list with full argument values, and
  `trail_signature_recovered` / `trail_source` saying whether the plan was OBSERVED at the mock
  or reconstructed from the transcript. Where the runner took a second attempt,
  `trail_repeat_distance` + `trail_repeat_selection` record how far that repeat moved the plan —
  **evidence, never a noise floor**: the runner retries only scenarios that failed, so a clean
  run produces none. The report renders the digest in a `Tool trail` column. Comparing two runs
  is a separate entry point — `${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/compare_runs.py
  <before>/run.json <after>/run.json --floor <run.json> --floor <run.json>` — because a floor
  needs at least two
  same-configuration runs and one run.json cannot carry a label about a run that does not exist
  yet.
- `user_sim_integrity` — **scenario fidelity, not a verdict about the agent.** `{measured,
  clean, anomaly_turns, anomaly_kinds}` plus, on a backend that publishes the table,
  `self_report_turns` / `self_report_kinds`, folded from each transcript's
  `finalize_record.user_sim_integrity`. It says whether the USER SIMULATOR kept to
  the scenario's own plan; when it did not (beats unplayed at the turn budget, a clamp
  released with beats unplayed), the transcript does not enact the scenario it was drawn
  from, and every turn-indexed judgment over the affected turns graded a dialog nobody
  specified — **no eval edit repairs that**, so read it BEFORE reading any rate, and treat an
  unclean scenario as a re-run/regenerate finding rather than an agent defect. The
  `self_report_*` keys are different: the simulator's own delivery LEDGER disagreed with
  itself (a beat counted past what the premise declares) while the words stayed on
  schedule — `clean` is untouched and no verdict is discounted; read those turns (especially
  when the dialog ended on or right after one), do not re-run for them. Read them THROUGH
  `raw_reply` on the turn named (see the per-turn reader above): it carries the model's own
  structured answer plus the `omitted` list, which is the only thing that separates "the persona
  reported nothing" from "the provider left the key out" — a scenario finding and a backend
  finding, and the delivery ledger alone cannot tell them apart. `self_report_unreadable`
  names scenarios whose published table could not be parsed — neither cleared nor flagged. The key is
  OMITTED entirely when no transcript carried the block (an older backend): "clean" and
  "nobody looked" must not render alike. It also carries `scripted_delivery` — per scenario,
  every planted beat as `{beat, kind, landed, reason}`, so `turns 2,3,4` becomes "`plan-1`, a
  premise beat, was never voiced, because its precondition never arose". `beat` is the beat's own
  stable ID and `kind` is `premise` / `knowledge` / `absence`; `landed` is the turn credited with
  it, `null` when nothing was, and `reason` is then the backend's typed cause — rendered verbatim,
  because that vocabulary is the backend's and it grows. **There is no `due` turn and no `early`,
  in run.json or on the page.** Release is by plan ORDER and precondition, so a beat credited
  after another did not run late and no beat can be early; what a reader needs instead is WHY an
  uncredited beat went unvoiced, since a precondition the dialog never reached is a scenario
  behaving correctly and a beat the turn budget cut off is not. Joined here from the two halves
  the backend publishes under the transcript's own `finalize_record.user_sim_integrity` —
  `delivered_beats` (`{id: {turn, kind}}`) and `undelivered_beats` (`{id: {kind, reason}}`). A
  STORED run written before those keys carries the index-keyed
  `scripted_delivery_schedule` / `scripted_delivery_turns` pair instead and still renders: its
  beats keep the index as their id, have no `kind`, and an uncredited one reads
  `reason: "unknown"` rather than a cause that run never measured. The run.json key is omitted
  when no scenario carried a ledger in either spelling, and the headline then says NOT COMPARED —
  a `0 undelivered` there would clear a plan nothing read. The renderer headlines the block
  whenever `clean < measured`.
- `aborted` — the RUNNER's `aborted` block, COPIED from `run_summary.json` beside `run_integrity` and present only
  when it is non-null. A run with the block is NO RUN: `build_run_json.py` builds none from it, and a renderer that
  finds the block in a `run.json` renders nothing and prints its `remedy`.
- `run_integrity` — the RUNNER's own integrity signals, COPIED from `run_summary.json`, never
  recomputed: `measurement_clean` (three-valued) with `measurement_unclean_because` naming which
  channel drove a `false` — without it the flag reads as a contradiction of the per-scenario map
  beside it, where every row can be clean — and `measurement_unchecked_because` naming which
  channel could NOT be measured on some row (a `null`, not a breach: the evaluator-voice judge
  did not answer for those turns, so they are undecided) — plus
  `scan_contract_drift`, `egress`, per-scenario
  `stability` (`stable` / `flaky` / `unmeasured`) and `family_stability`, plus two keys the script
  COMPUTES rather than copies: `tool_count_floor` (`{tool: rows the union could not verify}`), the
  receipt that every tool number in the report is a lower bound for those tools, and
  `unclassified_dispatch_outcomes` (`{tool: rows whose outcome nothing decided}`) — a trail row
  with no harness `status` whose prose is in a script the English-keyed transport detector cannot
  read, counted as neither an error nor a clean call. Both also ride the per-scenario rows.
  The copied keys are OMITTED whole when `run_summary.json` is unreadable or carries none of them
  (an older runner); the report then renders the section anyway, saying nobody looked, so "clean"
  and "nobody looked" do not read alike.
  - **`untrustworthy_scenarios`** — the rows the backend SCORED but whose world state it declined
    to certify. Three states, kept apart: named rows (each with its reason), an empty map (the
    runner looked and every scored row was certified), and an ABSENT key (nobody looked — not the
    same as clean). A row whose agent crashed mid-run is one of them: the backend scores it with
    the world unobserved, so its world checks abstain and its transcript checks grade, and it
    fails only on a decisive violation. The cells on those rows are real measurements and no rate
    on the page is dented by the doubt, so the row keeps its verdict and carries a
    `world_state_trustworthy: false` mark instead; the report stamps it `⚠️ world uncertified` beside that row's result.
    The doubt is about the WORLD the verdict was reached over, and it belongs next to the verdict
    rather than in a list of names three sections away.
- `outcome_attribution` + `dead_tools` + `unproven_tools` + `invented_verbs` — for every flow family that scored
  **0**, whether it was even testable. `dead_tools` are the tools that FIRED in this run and never
  once returned; a family every scenario of which called one is `environment`-attributed and
  renders as a report RED. It is NOT an acquittal — it says the family was not cleanly measured,
  not that the agent would have passed — and it is absent when no family was zeroed.
  - **`invented_verbs`** (+ `invented_verb_calls`, `{verb: dispatches}`) — the names the run
    called that `plurai.config.yaml` declares under NEITHER `tools:` nor `render_actions:` AND that
    the harness refused by name (`not_dispatchable`) on every call. Those
    were never a capability this environment could block, so they are struck out of `dead_tools`
    BEFORE the attribution — which is what flips their families from `environment` to `agent`.
    An undeclared name the run DISPATCHED is struck out the same way but is not invented: it prints
    one advisory `plugin.run.verb_surface_unmatched` finding and never reaches the report.
    **The report MUST name them**, and does: the family reading says whose the finding is, and
    only this key says what the agent DID to earn it. Repair the agent (or declare the verb),
    never the environment. Requires `--workdir`: with the declaration unread the surface is
    UNKNOWN, nothing may be called invented, and the report says so rather than reverting
    silently to `dead_tools` + `environment` (`verb_surface_unknown`, below).
  **`disputed` (⚖️)** — every scenario of the zeroed family failed because the deterministic check
  failed what the target judge passed (`goal.goal_disagreement` = `{deterministic: fail,
  target_judge: pass}`). The suite's own graders dispute that zero, so it is rendered beside the
  table, not in it, and is never summarised as a finding about the agent — resolve the check first.
  A deterministic pass beside a judge fail is NOT disputed: the check is only a necessary condition,
  so that row reads as the agent's like any other failure.
  Two further states, both of which exist to stop a NOT-MEASURED family reading as a measured one:
  - **`incomplete` (⏳)** — the family holds any row that is neither a pass nor a fail (errored,
    never executed, landed incomplete). "Not fully measured" is its whole claim: it is not a
    capability finding about the agent, and it must never be summarised as one. Note the table
    holds only families that scored **0**, so a family with any pass is not in it — `incomplete`
    is about a ZEROED family whose population was never fully measured, e.g. two fails and one
    scenario that crashed.
  - **`unproven_tools`** — a tool that FAILED every dispatch but made fewer than the minimum
    number of them. Too few calls to call it dead, too few successes to call it working, so its
    family is capped at `mixed` rather than attributed to the `agent`. The minimum is TWO, so
    `unproven` is the n=1 tool: attributing a family to the agent off a single failed dispatch is
    a verdict the sample cannot carry. At two failed dispatches the tool is `dead` and its family
    is `environment`. A companion `unproven_tools_floored` names the subset for which even "the
    run never asked twice" is unearned, because the union suppressed a row that would have
    crossed the minimum.
  - **Dead needs the environment's own fact** — an engine / runtime / SDK-stub marker, a harness
    `unavailable` / `not_dispatchable` stamp, or transport-failure prose on every failed call. Only
    the engine's typed "arguments invalid for the declared input" charges the agent (`refused_tools`,
    run-wide and on the family row). Any other failure — an error envelope included — is UNOWNED
    (`unproven_tools_unowned`) and its family caps at `mixed`.
- `local_only: true`, plus `run_name`, `agent_version`, `dataset_name`, and `run_mode`
  (`connect` | `local` | `mockless`, from `--workdir`'s `.run_mode` or `--run-mode`; OMITTED when unknown —
  the renderer reads absence as connect, the conservative direction). A `-dirty` `--agent-version`
  is written as its commit, with `agent_source` (internal) saying whether the agent's own source differs from it.

**An EMPTY `tool_events.json` has two causes, and they are not the same finding** (the file
is written for EVERY scenario — a missing one means the scenario never finalized, and the
script says so on stderr):

1. **A tool-less agent** — nothing to dispatch. Expected; the counters are genuinely 0.
2. **An agent whose tools bypass the mock** — `tool_events.json` only records calls through
   the `/mock/execute` endpoint (`mode: mock`). A `mode: real` tool going through a storage
   adapter leaves no event, so an agent that called tools all run long can still show 0.
   Reporting that as "no tool calls" is a **false negative on a tool-USING agent**.

The aggregator closes case 2 for a harness that self-reports: it UNIONS the event log with the
agent's own `agent_state.tool_trail`, **deduped by CALL IDENTITY (`call_id`), event side
authoritative** — so a `mode: real` dispatch is counted, its failures included, and an agent that
called the same tool five times is counted five times. Name-level dedupe is the FALLBACK, used
only when ids are absent on one side. It reports a FLOOR in exactly two states — an id on one lane
and not the other, or more trail rows than logged events — and there the counts are a lower bound,
published per scenario as `tool_count_floor` and run-wide under `run_integrity.tool_count_floor`,
and rendered as such. Where NEITHER holds (no id anywhere, and the trail fits inside the log) the
name rule is the documented reconciliation, no note is printed, and the counts stand as totals: a
floor stamped on every ordinary run is noise that hides the real ones. It has NO message lane, so what it cannot
see is any `mode: real` tool whose harness reports no trail — including one whose tool structure
IS in the transcript, which the Plurai backend recovers and this script does not. Expect local
`tool_calls` to under-report the backend's on that topology; the counters read 0 on a run that
dispatched. Disambiguate by the run's own config, not by the empty file: if the agent has
any `mode: real` tools and the trail is absent, recover the count from the transcript's
`chatbot_messages` before concluding zero, and fix the harness to emit `agent_state.tool_trail`
(`[{tool, args, call_id, result, status}]` per turn) so the next run measures itself. Only when
the agent genuinely has no tools — and no row DECLARED its channel unread — does the report omit
the Tool calls column, because a column of zeros reads as an agent that had tools and never
used them.

**If you must hand-roll instead** (no python3 on the box, or probing a changed artifact
shape): every shape trap this aggregation has hit live — the `events` nesting (there is NO
top-level `tool_events` key), the transcript's absent `agent_tool_calls`, the verdicts read
from each attempt's `verdicts.json` (the transcript carries none), the four-state cell split, the skipped-policy union, the
conclusive-violation filters, the refusal-vs-error split, the annotation rules (no `|` or
backtick; annotations never in `evals[].id`) — lives as a comment in `build_run_json.py`
at the line that implements it. Read those, not your memory of them.

Render (stdlib-only):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
SUITE_ARGS=(); [ -f "$ART/suite.json" ] && SUITE_ARGS=(--suite "$ART/suite.json")
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/render_run_report.py" \
  "$OUT/run.json" --out-dir "$OUT" "${SUITE_ARGS[@]}"
```

`--suite` adds Knowledge coverage from the suite's `knowledge` block (verdicts per scenario, never per document).

**A run with trials:** run both commands on each non-empty `trials.dirs` entry of
`$OUT/run_summary.json` too (`$OUT/<dir>` in place of `$OUT`, same flags). The top-level `run.json`
copies `trials` verbatim, and its `analytics.md` leads with them and links each trial's own report.
`$OUT/run_summary.json` is the run-end snapshot and is never rewritten. When a rescore or a correction
changed a trial's `verdicts.json`, `run.json`'s `trials` is recomputed by the SDK's aggregator over the
current files (`trials.rescored` names the scenarios), so `run.json` carries the current numbers.

It writes `$OUT/analytics.md` (summary cards + per-eval pass-rate + per-scenario
table, with the deterministic verdict and the inconclusive/skipped policy names as their own
columns) + `$OUT/charts/{run_summary,evals_passrate}.svg` — all under the gitignored run dir —
and `$OUT/eval_signal.md`, which is loop material and never handed to the operator.
See `references/example_run.json` for the spec shape.

## Step 4c — attribution + the release gate (before anything is presented)

The run produced numbers. This step decides whose numbers they are and whether they are fit to
publish — both mechanically, both in seconds, and neither optional on a FINAL run.

**Upload the run once.** Pre-authorized by the invocation — announce it in product terms and
never ask consent for it; say exactly *"Uploading this run's saved artifacts so the backend can
attribute the failures and check the release gate."* The backend-identity preflight is part of the
SAME call (the shell resets between calls, and it catches an upload aimed at a backend other than
the one MCP is bound to):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $PLURAI_DIR / $WORKDIR — re-source in THIS block
# $OUT is not exported by the resolver and the shell resets between calls — read the pointer the run wrote.
OUT="$(cat "$WORKDIR/.last_run")"
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-run-report.sh" "$OUT" "$WORKDIR" || exit 1
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-run)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-run --run-dir "$OUT" --workspace-root "$PLURAI_DIR"
```

A `WARNING (disclosure: home_named)` line from `ingest-run` (or `ingest-run-report`, below) is printed before
any byte leaves: relay it to the operator in that step's own message, naming the files it lists. It does not
stop the upload.

`assert-run-report.sh` refuses only ONE state: this run is about to publish a verdict, the
workspace recorded nothing to an observability platform (`.observability` is `isolated`, or
absent — which means the same thing), and there is neither a `run.json` nor a `run_stale.json`.
That is "the customer's analysis is nowhere, and nothing says so" — a run that ends there has a
release verdict and no readable half. It is NOT "run.json must exist": a `recording` run is
silent here, because Step 4b skips the local report by design and the platform experiment is the
analysis. A `run_stale.json` is also silent — the aggregator already refused the run and said
why, and the guard echoes that reason instead of adding a second one.

Feed the printed manifest to `upload_run_artifacts_start(manifest=...)` (lift `provenance` out and
pass its members as separate arguments — `/simulation-generate-suite` Step 3 has the full rule),
then:

```
suite_diagnostics_start(run_upload_id=<the upload>, eval_set_id=<.eval_set_id>,
                        scenario_set_id=<.scenario_set_id>,
                        run_stage=<$STAGE, resolved in Step 2b>,
                        current_harness_fingerprint=<HARNESS_FP>)
```

(`<HARNESS_FP>`: the bare hex `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_fingerprint.py" --quiet` prints after
sourcing `resolve-workdir.sh`; pass the same value on every re-call and `release_verdict_recompute`.)

Deterministic and zero-LLM; poll `job_status(id)`. The post-upload checks do not wait on each other: start `suite_diagnostics_start` and `lane_pin.py relink` back-to-back (and, on a build that runs one, the post-final solvability check), and poll them together. A holding finding on the done job's `detail.gate_authority.findings` (a critical check never shown able to fail holds a final gate) is yours to decide per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`: with evidence it is wrong for this agent, call `suite_diagnostics_start` again with the same arguments plus `overrules=[{gate_id, fingerprint, reason}]` and read THAT job — its `release_gate` already has the overrule applied; never tell the user about it. An `attribution.*` finding on a policy a confirmed agent finding names is overruled by script: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/red_cells.py" attribution --report <the saved envelope> --findings "$ART/agent_findings.md"`, and when its `new` is non-zero, its `overrules` join that call.

**Cells a gate set aside** (`score.*` and `attribution.*` findings, here and on `score_run`) keep the
judge's raw verdict; read the cited transcript yourself, with judgement, and decide:

- `score.absence_after_turn_cap` (reason `absence_after_turn_cap`): the turn cap ended the dialog before
  the thing the judge found absent. The agent looped, stalled or never tried ⇒ overrule to restore the
  red. The dialog was still on track when the cap ended it ⇒ leave it set aside.
- `score.unreproduced_across_attempts`: a pass the second sample graded red. Overrule to keep the pass
  only when the first attempt really passed; otherwise leave it withdrawn.
- `attribution.*`: a red filed to the eval or the environment. Run the check its remedy names; when it
  fails, overrule to file the red as the agent's.

The restore path is in `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`. The report shows the effective
verdict and never the overrule.

**`run_stage` is a CLOSED vocabulary —
`'qualification'` or `'final'`, lowercase, refused at the tool boundary.** The gate binds on
`run_stage == 'final'`, so any other spelling would produce a silently NON-binding gate on the one
run that must be gated.

1. **Attribution — `owning_layer` and the `attribution.*` findings are evidence.** Every failed or
   invalid scenario comes back with an `owning_layer`. `policy@eval_set`, `harness@client`,
   `gt@scenario` and `facts@scenario` are NOT agent failures unless you overrule the attribution
   (above): keep them out of the "What failed" bullet, which is about the agent, and out of `/simulation-fixes`' inbox. On a final run this is
   **advisory + escalation** — you repair nothing here; you report the attribution and name
   `/simulation-generate-suite` as where a suite-side defect is fixed.
2. **`release_gate` is the ONE value to branch on — never re-derive the join yourself.** `reasons`
   names each unmet precondition, in order.

   **Its `status` has THREE values, and there is no unknown.** A gate that could not read its
   coverage still REACHES a decision — it blocks a final run — so an unknown INPUT is a `reason`,
   never a status.

   | `release_gate` | the operator sees | the internal record |
   |---|---|---|
   | `status: pass` | — (the numbers stand; the banner below names any check they could not prove) | — |
   | `status: advisory` (any qualification run) | — (the numbers stand) | each reason → `append_loop_log.py --kind note --lane eval` |
   | `status: block` (final runs only) | Step 5's cause dispatch: the agent's own crash when the run shows one, else the BLOCK hand-off and the banner below — verbatim | each reason, with `coverage_status` → the loop log |
   | **`null`**, or absent with no line from `render` | — (the ordinary Step 5 skeleton; `/simulation-fixes` attributes each failure) | NOT GATED, and why → the loop log. The job did not reach a release decision, and that is never read as a pass |

   The reasons name internals and their remedy is the loop's, never the operator's.
   `remedy: rerun` means no void that held a scenario unscored was `transient: false` (each was transient or untyped). A
   scenario voided by a `transient: false` run error blocks with `fix_env_rerun_scenarios` (or
   `replant_premise` beside an under-planted premise) — a bare re-run reproduces the fault, so its
   repair is the [`error_kind` table](#error-kind-vocabulary)'s row for that kind, then a re-run of
   only those scenarios: the bounded repair below.

   Read `coverage_status` off the same object to record WHICH axis
   failed; `coverage_status: unknown` means no eval produced a coverage row.
3. **The `checks` ledger travels with all of it.** `ran` / `flagged` / `blocked`, and only `ran`
   licenses "clean". A `blocked` check names the input it lacked; that is a coverage hole in the
   report, not silence.

**The `analytics.md` banner.** Render it on every run once the job is done, unless Step 5 hands off the agent's crash: it writes
the one banner this verdict licenses into every trial's `analytics.md`, and on a block the line it prints is the BLOCK skeleton's. Never write the banner by hand:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status '{"id": "<the suite_diagnostics job id>"}' > "$OUT/suite_diagnostics.json" && \
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/release_verdict.py" render --report "$OUT/suite_diagnostics.json" --out "$OUT"
```

Exit 11 means the job is not `done` yet: poll it again and re-run this block; exit 12 means the job failed. Either way the render has removed any older verdict from `$OUT`, so no stale verdict sits beside this run. Any other non-zero exit means the `job_status` call, the save or the render failed (its stderr names which). On either failure the step did not run (below).
On a report with no `release_gate` key (an older backend), a line `render` prints takes the `status: block` row.
On a final run with no uploaded red-cell audit the render writes the verdict `provisional` (stderr `PROVISIONAL`): it has not landed, so never present it as the result — continue to Step 4d, whose fresh `suite_diagnostics` and re-render land it (`score_run` says `provisional` the same way).

**If the upload or the job fails, this step did NOT run.** When `ingest-run` exits non-zero, relay its one `error:`
line to the operator in that step's own message. Record NOT GATED and why in the loop
log, and present the ordinary Step 5 skeleton without any claim about whose failures they are.
Never let the absence stand in for a clean result, never send the run another way, and never skip the local
red-cell audit, its corrections or the report: they need no upload.

**Attach the report to the upload — last, after the banner.** Pre-authorized by the invocation —
never ask consent for it. Skip it only when there is no upload to attach to:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-run-report)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-run-report --run-dir "$OUT" --workspace-root "$PLURAI_DIR"
```

It prints either an upload manifest or `{"absent_reason": …}`; pass it as
`attach_run_report(run_upload_id=<the upload>, manifest=<it>)` or
`attach_run_report(run_upload_id=<the upload>, absent_reason=<its value>)`. The report is not part
of the score: exit 7 is a version refusal (its output is the update text: say it as the error posture says, and stop); if the block fails otherwise, or the backend has no `attach_run_report`, record
`report not attached: <why>` with `append_loop_log.py --kind note --lane eval` and go on.

**Bounded repair (once), then present.** A final `status: block` whose `remedy` is `rerun`, `fix_env_rerun_scenarios` or
`replant_premise` — or a final pass that still publishes a non-empty `replant` (a situation named "not tested here") — is
repaired in place ONE time — pre-authorized by the invocation, never asked. Under the suite's window a `replant_premise`
remedy, or a `replant` alone, is not repaired here (Step 2b's direct-run rule): Step 5 with the gate as it stands.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bounded_repair.py" claim --out "$OUT" --report "$OUT/suite_diagnostics.json"
```

Exit 3 (this run already took its repair — Step 2b's re-plant counts) or 4 (not a bounded remedy) → Step 5 with the gate
as it stands. Exit 0 → in this order, logging each step (`append_loop_log.py --kind note --lane eval`), then STOP:

1. The claim's `replant` is non-empty on a direct run → Step 2b's top-up call itself (no second claim), then its re-point and relink.
   The new ids are the new set's scenarios the old one did not hold. A refused or short top-up ends the repair here.
2. Its `deterministic_losses` is non-empty → ONE `/edit-env` session fed with each lost scenario's recorded error (the
   report's `scenario_run_failed` findings); it redeploys on finish. Not fixed in that session → the repair ends here.
   A `transient_losses` scenario never goes to `/edit-env`: the re-run below is its repair.
3. ONE targeted re-run of the claim's `rerun_scenarios` plus the new ids into the SAME `$OUT`, with
   `MAX_CONCURRENT=<the claim's rerun_concurrency>` when it is not null: Step 3's block with `OUT="$(cat "$WORKDIR/.last_run")"` in place of the mint, one
   literal `--scenario-id` per id; the stage-flags script keeps the run's own trials (it reads `$OUT/run_summary.json`,
   and a different N is refused). The runner merges those ids into the run.
4. Steps 4–4d on the merged `$OUT` — after a top-up, add `--scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")"` to
   `ingest-run` — then present whatever that gate says. A second claim exits 3.

## Step 4d — correct this official run's false reds (final runs only, before Step 5)

Every way out of Step 4c reaches Step 5 through this step. It runs ONLY on an official run: Step 2b printed
`STAGE=final` AND the flow marker holds `run` or nothing. Inside `/simulation-generate-suite` or `/analyze-simulation`
(any other marker) the suite runs the same corrections in its own step, so this step never runs twice on one run:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
STAGE="<the STAGE Step 2b printed>"
DRIVER="$(cat "$FLOWDIR/.plurai_flow_active" 2>/dev/null || true)"
case "$STAGE:$DRIVER" in
  final:run|final:) RUN="$(basename "$OUT")"
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/red_cells.py" list --run-dir "$OUT" --greens 24 --workdir "$WORKDIR" \
      --patterns "$ART/audit_patterns.json" --split-dir "$ART/red_cells_$RUN" --out "$ART/red_cells_$RUN.json" && \
      python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print("red_units=%d green_units=%d" % (d["red_units"], d["green_units"]))' "$ART/red_cells_$RUN.json" ;;
  *) echo "step 4d skipped (STAGE=$STAGE, flow marker: ${DRIVER:-none})" ;;
esac
```

Skipped → Step 5. `red_units=0 green_units=0` → there is nothing to rule, and the audit is still owed: save `[]` (`red_cell_audit.py save --run-dir "$OUT" --stage final --rows -` fed `[]`), run the upload block of `final-run-corrections.md` step 1, call `suite_diagnostics_start` again with Step 4c's arguments, then Step 4c's banner block on that job, then Step 5. Otherwise follow
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/references/final-run-corrections.md` in full, on this run only: the audit's
rows are saved to this run's own audit (`red_cell_audit.py save --run-dir "$OUT" --stage final`), the one every command on this run reads; the labels take
Step 4c's upload as `run_upload_id`; the re-render re-runs Step 4c's banner and attach blocks. This step REPAIRS
NOTHING: no `/edit-env`, no `edit_scenario_gt`, no `score_run`, no re-run. An `ENV` row, or a `scenario` or `env`
`FALSE RED`, is recorded for the next build and never repaired here; repairs belong to `/simulation-generate-suite`.
Step 5 presents the corrected numbers. This adds one red-cell audit to every official run that has a red or a sampled green.

## Step 5 — present (short, link-forward) + publish the dataset/experiment link

**Recording-failure gate FIRST (C3).** If the user chose experiment/traces and `rc` is none of `0`, `1`, `11`, or `20` with `aborted.reason: credit`, `capacity` or `unavailable`
with no `error.json` (Step 4), the run **scored but the recording failed** (upload error,
expired/again-missing key, partial). Do **NOT** present an experiment link. Read the
runner's stderr tail YOURSELF to classify — never quote it at the user: a local config
issue (wrong dataset name, malformed flag) → fix it silently; a missing/expired key →
re-run Step 1a's `sink_keys.py` check and ask for it in plain terms exactly as Step 1a does
(incl. its sanction write); anything backend-side → nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`).
In every case say plainly that recording failed but the traces/scores are safe, and fall
through to the local analytics report (Step 4b) as the deliverable. Never tell the user
to "open the experiment" when none was created — that's the exact lie Step 1a forbids.

**Not recorded at all** — the user chose experiment/traces but the run went out with no sink
(Step 1a found no key and fell back, e.g. a headless `platform-key` default of `skip`). Say it in
one line, then give the fix as the `.env` route from the `sink_keys.py` output — never "export the
key": the `.env` is what both the check and the runner read, and an export in the user's terminal may
not reach the commands Claude Code runs. E.g.:

> This run was **not recorded** in LangSmith: no `LANGSMITH__API_KEY` reached it. Add the line
> `LANGSMITH__API_KEY=<your key>` to `<agent dir>/.env` (gitignored) and run `/simulation-run` again.

**Otherwise, publish the links.** The runner prints them at the end of its summary (SDK 0.28.15+;
an older SDK prints only the `Dataset:` line):

```
Dataset: <url>
Experiment: <url>
Trace: <scenario_folder> <url>
```

`Experiment:` is this run's view (LangSmith: the dataset's compare view of this run; Langfuse: the
dataset run). `Trace:` is one line per recorded scenario whose trace link resolved. Under `--trials N` each trial's
block carries its own `Experiment:` / `Trace:` lines. Read them from the runner's output (the file
`run_wait.sh` names when the run ends), never by building a URL yourself. Surface the **Experiment** link as
the headline (the **Dataset** link when there is no Experiment line), then the trace links as a
compact list, one per scenario — `scenario_02 — <url>` — failed scenarios first. They do not count
against the four content lines below. If the runner emitted no link at all, surface the **dataset
name** + exactly where to open it — and don't invent a URL:
- **LangSmith** → *Datasets & Experiments → `<DATASET_NAME>`* (your run is the newest
  experiment under it).
- **Langfuse** → the dataset's *Experiments* tab.

**The dataset includes the scenarios this run covered**, not the whole suite: the runner uploads only
the scenarios it runs, and runs of other scenarios add theirs to the same dataset. The
`[dataset-upload]` line says `N of M scenario(s) in the set`; when it does, say so in a few words if
the user would expect the whole suite there. (An SDK before 0.28.15 uploads the whole set.)

**If a local analytics report was generated (Step 4b)** — i.e. no platform results were
shipped — that report **is** the deliverable: lead with it instead of a platform link.

**When Step 4b wrote `$OUT/analytics.md`, print its link** — never type its path, which opens on no
surface the reader has, and never publish the file (the overview page is the only published page). The
line is the file's own link: its path, or a `file://` URL on Copilot and Codex:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_customer_numbers.py" "$OUT"   # exit 1: a number is not C7's — never link it
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "analytics.md" "$OUT/analytics.md"
```

Then ≤4 lines of content, plain language — counts, what failed, the link/report +
run dir (+ the optional `Agent`, `Run integrity` and `Trace scenarios` lines below, which do not count
against the four). If any
scenario **failed** (a goal not met, a critical check failed, or a high-severity check failed), `/simulation-fixes` is the next step (it diagnoses the AGENT's failures). If the run is
**green**, say so, and don't manufacture a next step. Read `suite_anchor.json`'s rung and this
run's `release_gate`; a suite-side cause (`owning_layer` ≠ agent) goes to `/simulation-generate-suite`.
The skeleton:

> &lt;`headline`, verbatim&gt;
>
> &lt;`scenarios`, verbatim — its own line; only when it is not null&gt;
>
> &lt;`agent`, verbatim — its own line; only when it is not null&gt;
>
> &lt;`suite`, verbatim — its own line; only when it is not null&gt;
>
> - **What failed** — <the shared behaviour behind the failures, in the customer's domain language,
>   quoting the agent's own words; which evals fired and on how many conversations; ending with the
>   scenario id(s) it is drawn from. Omit when P = V and C = 0 and H = 0.>
> - **Real tools** — <`setup`, verbatim — only when it is not null>
> - **Run integrity** — <only when one applies; see below>
> - **Trace scenarios** — <only when this run ran one; see below>
> - **Results** — Analysis → &lt;the analytics link line&gt; ·
>   run dir `<path>` <or, when recorded: experiment **`<DATASET_NAME>`** →
>   <url / LangSmith → Datasets & Experiments>>
>
> **Next (when P < V or C > 0 or H > 0): `/simulation-fixes`** — diagnoses each failure in **your agent** and hands you a ranked fix plan with evidence and exact code locations.

Every number in the hand-off comes from the run summary (C7), printed by one script — never counted off rows,
`run_summary.json` or the transcripts:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_numbers.py" chat "$OUT/run.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_numbers.py" covered "$OUT/run.json"
```

The second call lists the situations this run's conversations put to the agent; name a situation only when it is listed
(`null`: name no situations as covered). The first prints `{headline, scenarios, run_integrity, agent, suite, setup}`: a non-null `setup` (tools that run for real never succeeded) is pasted as the `Real tools` line; it leads Next — fix what it names, then **`/simulation-run`** — ahead of `/simulation-fixes` only when the error it quotes plainly names a credential or a service to start, never when it is the system's own answer to the conversation. Paste `headline` as the first line, `scenarios` (how many
scenarios were reliable, flaky or broken across the trials) as its own line when it is not null, `agent` (the SDK's ship verdict on the
agent, with its reasons — never reworded, never merged into the headline) as its own line when it is not null, `suite` (this run's own rung, said only
when it differs from the one the suite hand-off landed on) as its own line when it is not null, and, when `run_integrity` is not null, paste it
as the `Run integrity` line. `P` of `V` are the headline's ✅ counts; `C` and `H` its ❌ and ⚠ counts (0 when absent). Exit 3 means run.json carries no run summary: rebuild it (Step 4) and run it again.

**When Step 4c says BLOCK, the cause picks the hand-off** — a rebuild cannot fix a crashing agent:

| cause (typed) | the hand-off |
|---|---|
| the agent raised — a run.json row carries `error_kind: agent_raised`, or a scenario's `transcript.json` carries `pipeline_io.error` (`goal.goal_source: pipeline_error` on its run.json row) | the Run complete skeleton above, its What failed bullet stating the crash (the line below), Next `/simulation-fixes`; no banner, and the block goes to the loop log |
| any other cause | the BLOCK skeleton below, and the banner |

The crash line, filled from that row's `error.json` `caused_by.error` (`agent_raised`) or that transcript's `pipeline_io.error`:
`What failed — the agent raised <exception>: <message>, and the conversation ended there (<their scenario_NN>).`

**The BLOCK skeleton** — verbatim; the counts lead, as in every other hand-off, and the line `release_verdict.py render` printed follows them (the same `run_numbers.py chat` call prints `headline`, `scenarios` and `suite`):

> &lt;`headline`, verbatim&gt;
>
> &lt;`scenarios`, verbatim — its own line; only when it is not null&gt;
>
> &lt;`suite`, verbatim — its own line; only when it is not null&gt;
>
> &lt;the line `release_verdict.py render` printed&gt;
>
> - **Run integrity** — &lt;only when one applies; see below&gt;
> - **Results** — Analysis → &lt;the analytics link line&gt; · run dir `<path>`

**Render it with that structure — headline, bullets, next step when the skeleton has one — and keep the blank lines.**
Markdown joins consecutive lines into a single paragraph, so a hand-off written as four
plain lines reaches the customer as one unbroken block of text with the counts, the caveat,
the link and the next command all run together. Every bullet stays ONE bullet: the failure
bullet is a couple of sentences, not a nested list of scenarios.

**Every defect bullet ends with the scenario id(s) it is drawn from** — a defect bullet being the
skeleton's `What failed`, the one about the agent's own behaviour; `Run integrity`, gating and other
suite-level bullets are about the run, have no row to cite and are out of scope. Cite
`scenarios[].id` off `run.json` by its leading `scenario_NN` (the report's name; never the folder id), and a
behaviour seen in two conversations cites both ids rather than splitting into two bullets. When one
bullet merges several behaviours, each behaviour names its own rows inline (`"too vague" on
scenario_01, the relabel on scenario_07`) and the pre-flight below joins per BEHAVIOUR, not per
bullet — one union id list at the end would fail the join for every policy but one. Before presenting, re-read
those rows' `failing_evals` and confirm the behaviour the bullet describes is among them — the ROW,
never a remembered eval name (evals get renamed mid-round, and a row may spell one
`reply_length_budget (turns 0,3 · 0-based)`). A bullet merges conversations by hand, so an uncited
one is uncheckable: a behaviour in no row it cites sits on the wrong scenario, and whoever opens
that scenario to reproduce it finds nothing there. Fix the id — never ship the bullet over it.

**The `Run integrity` line is optional and doctrine item 4** — include it ONLY when one of these
is true, and then state it flatly in one line, no cause and no repair story: scenarios that were
never executed (the counts above cover only what ran), trials that **did not run**, on our side or
the agent's (the `run_integrity` line `run_numbers.py` printed, verbatim — an agent that raised in its turn failed, never this), scenarios that **landed incomplete** (`totals.incomplete` — they ran out of the
scenario time budget, were graded to the turn each reached, and are in neither the passed nor the
failed count), a model that was substituted or overridden for the run, or a suite that shipped
short of its target count. It is a RESULT — it tells the reader
how far the numbers reach. It is not a coverage list, not an apology,
and not an opening to doubt the suite. Examples:
`Run integrity — 7 of 14 scenarios were never executed; the scores cover the 7 that ran.` ·
`Run integrity — 2 of 7 scenarios could not run on our side. The results above cover the 5 that ran.` ·
`Run integrity — 1 scenario landed incomplete on the 40-minute scenario time budget; it was graded
to the turn it reached and counts as neither a pass nor a fail.`

**Every item on that list is about the RUN. The eval set is not on it, and cannot be added to
it.** "2 of 9 evals were never proven able to fail" is eval-set fitness, and Step 2b's rule is
absolute: the loop resolves that itself and says nothing about it here. A run-integrity line is
the licensed exception to doctrine item 3's ban on suite-gap narration, so a fitness caveat
smuggled in under its anchor is the one phrasing that would reach the customer.

**The `Pinned regressions` lines are optional** — paste them when Step 3b ran, as they print:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/render_run_report.py" "$OUT/run.json" --customer-regressions
```

It prints nothing when no regression was replayed. Each line says how often the recorded issue was
caught again at its turn; `analytics.md` carries the same lines and nothing more. The owners, the rates
and the recurring issue types stay out of the report: they are written to `$OUT/regressions_detail.md`,
which no report index lists and nothing publishes — never paste or link it here.

**The `Trace scenarios` line is optional** — include it ONLY when this run ran at least one trace
scenario: a folder under `plurai_sim/trace_scenarios/`, a production conversation cut just before
a trace failure. It says, per trace scenario, whether the failure production showed happened
again. Read it off `run.json` — the aggregator's (Step 4b). A run that skipped Step 4b builds it
with that same command first; the aggregator touches nothing outside the run directory.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
OUT="$(cat "$WORKDIR/.last_run")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" reproduced "$OUT/run.json" --trace-dir "$WORKDIR/trace_scenarios"
```

It prints `{trace_scenarios: [{folder, scenario, verdict, goal_status, goal_source,
goal_achieved}], reproduced, did_not_reproduce, ungraded_on_target, no_verdict}`. Each verdict is
the row's own `goal.goal_status`, never re-derived. `trace_scenarios` empty — leave the line out.
Otherwise ONE line of counts, naming only the groups that occur, in this order:
`Trace scenarios — 2 of 3 reproduced the production failure; 1 did not reproduce.` How to speak
of each verdict:

- **`reproduced`** — one of this run's failures like any other: it belongs in `What failed`, with
  its scenario id, and `/simulation-fixes` diagnoses it.
- **`did not reproduce`** — report it and nothing more: never a defect in the suite or the agent,
  and never proof that a fix worked.
- **`ungraded on its target`** — eval-set fitness, which stays operator-side: on the customer line
  count it with `no verdict` as "N reached no verdict", and never say why. Record it for the
  operator instead, naming every folder in `ungraded_on_target` — the same entry the suite build
  writes:

  ```bash
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
    --kind note --finding "ungraded on target: <the names> — no llm_as_judge policy declares grades_scenario_target"
  ```
- **`no verdict`** — counted as it stands; the row's Result cell says which status.

A trace scenario carries no blame: say "the production failure", never "the agent's bug", unless
its row is also in `What failed`.

**The ONE typed exception to "no cause": a simulation timeout.** When `run.json` carries
`totals.harness_timeouts` — the runner tagged those scenarios with one of the three
`harness_*` kinds at the raise site (the vocabulary table above; read the row's own
`error_kind` rather than assuming which) — name it, because it is
the one failure the suite can state a cause for without speculating: a budget expired, and
the budget is ours. Say which budget expired, nothing more — never a knob to raise and never its
length: the turn may have been waiting on the simulated tools, which no budget fixes, and the
release gate's bounded re-run is what repairs the loss. A timed-out scenario has NO transcript,
so any story about what the agent did there would be invented.
`Run integrity — 1 scenario hit the simulation turn budget and scored nothing; it is not a verdict
about the agent.`
This does NOT license the general form: an untyped `errored` scenario still gets the flat
line with no cause, and "maybe the scenario is wrong" stays out of the report entirely.

**Ending a turn while the run is still going** — a mid-flow message is style-validated too,
so lead it with the in-progress anchor: `Run in progress`. It answers exactly one question,
does this turn owe the hand-off skeleton; it never closes the window, it is valid only while harness-tracked work is actually in flight (a wait in the background, a spawned subagent — the harness records it and re-invokes you when it finishes; a detached `nohup` does not count and the guard blocks the anchor), and it exempts the message from nothing else (no cost figures, no question-shaped ending).

**State the outcome about the AGENT: the suite found issues in it, and `/simulation-fixes`
will pin them down.** Never suggest the tests might be the problem ("test-setup artifacts",
"maybe the scenario is wrong") — a suite-side cause is one the run's `release_gate` and
`owning_layer` name, never a guess — and never speculate about
which fix will matter most — that's the next command's job. The single exception is a
scenario the runner TYPED as a simulation timeout (`error_kind: harness_*`): that one is not
a finding about the agent at all, it goes on the `Run integrity` line as above, and it never
appears among the failures.

(Only recommend commands that exist.) The summary presents the RESULTS — never the
run's internal recoveries (retries, transient errors you absorbed) and never spend.

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
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check run --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step run --flowdir "$FLOWDIR"
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step run --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

**The window closes itself on the validated hand-off** (report_guard removes the marker
when the summary passes with the `Run complete` anchor or the failure hand-off — never
`rm` before rendering). Early exits close it own-marker-guarded (not an exit that ends on the contact hand-off: render that hand-off and leave the window to its check):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close run
```

## Notes

- **Reuse, don't reimplement** — `plurai-run-simulation` owns the run loop and all
  observability; you only set env/flags + summarize.
- **Observability is opt-in + best-effort** — never enable a sink the user didn't
  choose; never let a push failure affect the run outcome.
- **No diagnosis here** — `/simulation-run` scores; `/simulation-fixes` diagnoses and
  proposes fixes. Don't engage the dual-agent diagnostic protocol in this command.
- **Named-env aware**; run output under the gitignored `$ART/runs/`.
- **Interpreter shadowing → `PLURAI_CHILD_PYTHON`.** The runner spawns the agent in a CHILD
  process, and by default that child uses the runner's interpreter. When the agent's deps
  can't coexist with the runner's (a legacy langchain vs `langchain>=1.0` is the usual one),
  the symptom at RUN time is an import error in the child, or — worse — a tool the agent
  silently dropped, so it free-hands an answer it should have tooled and the run still
  "passes". Point the child at the agent's own venv (which needs the `plurai` SDK plus the
  agent's deps; the parent↔child wire is plain JSON over stdin/stdout, so the two stacks may
  differ): `PLURAI_CHILD_PYTHON=/path/to/agent-venv/bin/python plurai-run-simulation …`.
  Full diagnosis + the silent-tool-drop symptom table:
  Step 9f of the `simulation-setup-python` skill.

## Getting a key when the user has none (PHASE 2 — not implemented)

Today: if the chosen platform's key is missing, Step 1a prompts the user to put it in the
agent folder's `.env`, else falls back to a local-only run. The richer handling below is **deferred**:

- **Self-serve (most users).** Both platforms have a free tier with self-service
  keys — LangSmith (smith.langchain.com → Settings → API Keys) and Langfuse
  (cloud.langfuse.com → Project Settings → public + secret keys). Phase 2 could offer
  to open the signup/key page and accept a pasted key.
- **No permissions (org-managed / SSO / enterprise).** The user may not be allowed to
  mint keys. Common practice: (a) ask a workspace **admin for a scoped, write/ingest-
  only key or a service account**; (b) use a **personal or sandbox project** for the
  simulation, kept separate from prod; (c) for self-hosted Langfuse, the admin
  supplies keys + `LANGFUSE__BASE_URL`.
- **Bring-your-own-key, don't persist secrets.** If phase 2 accepts a pasted key,
  write it only to a **gitignored `.env`** (with consent) — an export does not reach the
  runner under Claude Code — never commit it, never echo it back.
- **Graceful no-key value.** If no key is obtainable, the run still scores locally; per
  the PRD, for Langfuse (no auto-charts) we should **generate a local analytics report**
  alongside the run so the user gets the analysis without the platform. That local
  report is the recommended phase-2 fallback deliverable.

## Dataset naming + experiment link (PHASE 2 — gaps to close in the SDK)

Plugin-side now: a meaningful, stable LangSmith dataset name via `LANGSMITH__DATASET`,
and the run surfaces the dataset name/URL. One gap best closed in the SDK/backend:

- **Langfuse dataset name** — no run-time override exists (only LangSmith has `dataset`
  precedence, `langsmith.py`'s `cfg.dataset or dataset_name or …`). Either set a
  meaningful `dataset_name` on the scenario set at generation (backend — the set name is
  what Langfuse uses) or add a `LANGFUSE__DATASET` override field to the Langfuse sink.
