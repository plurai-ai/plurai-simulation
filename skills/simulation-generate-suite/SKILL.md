---
name: simulation-generate-suite
description: >-
  Build the suite: mocked world, scenarios and evals, then iterate on quality until the simulation gives sharp signal, and render a report. Stage 3 of /simulate; run after /simulation-scan-agent.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Generate suite — build the test suite + report

**Usage:** `/simulation-generate-suite [context] [--no-auto-regressions]` — the context is a one-line prompt, a path to a PRD/operating-policy `.md`, or `--zero-shot`. `--no-auto-regressions` keeps the gate's confirmed findings from being recorded as regressions (the gate-exit record step).

You are the LOCAL Claude Code agent. This is **command 3 of 5**:

```
scan → traces → suite → run → fixes
 map     learn   build   score  repair
                 ▲ you are here
```

`/simulation-generate-suite` generates **scenarios and their evals together**,
then renders a reviewable report. Orchestrator only — reuse the existing skills;
your new work is authoring the title and summary of any scenario that has no backend scenario card, which
`build_suite_json.py` writes into `suite.json` beside what the backend returns, and rendering it.

```
┌── /simulation-generate-suite [context] ───────────────────────────────────┐
│  0c. STAND UP THE WORLD → deploy_env by id, else generate-mock-env         │
│       .env_id · .mock_service_url · scenario_state_schema.yaml             │
│  1. generate-scenarios  → .scenario_set_id   (QUALIFICATION: count 3)      │
│  2. generate-evals      → policies_config.yaml, .eval_set_id               │
│  3. QUALITY LOOP — own it, until top quality:                              │
│       qualification run (/simulation-run) → /analyze-simulation drives it: │
│       ONE fresh independent critic → you fix its BLOCKING findings, once   │
│       → verify mechanically → red-cell audit → land (a 2nd full critic     │
│       only when the run itself did not measure the agent)                  │
│     gate exit: record ≤6 confirmed findings as regressions/ folders        │
│  3b. FINAL generation — same inputs, count=7 (flat) →                      │
│       overwrite .scenario_set_id (this set is the deliverable)             │
│       relink evals → audit vs the final suite → close gaps additively      │
│  4. fetch meta + build suite.json (coverage, complexity, evals, schema)    │
│  5. render → scenarios.md, evals.md, charts/*.svg, scenarios/*.md          │
│  6. present the VALIDATED suite (short, link-forward) → /simulation-run    │
└────────────────────────────────────────────────────────────────────────────┘
```

**Steps 3b, 4, 5, 6 and 7 are NOT in this file.** They live in `references/deliverable.md` (full
path in the "Step 3b onward" section below), which you load in full once the gate converges. Every
reference to one of those steps in the pages between here and there points into THAT file — they
are marked *(deliverable file)* — and reaching one before the file loads means opening it at that
section, not reconstructing the rule from memory.

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

**Scoping note — what item 2's blocking-input ask looks like here.** The blocker for this command
is material only the user holds that the qualification run needs: the agent-provider key unset or
expired, a run target only they can point you at. Step 0's run-prerequisite check sees what it can
up front, but a prerequisite can break after it passes — and this command's whole value is the
run, so discovering it at the finish line wastes the entire generation spend. The moment you learn
of it:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker
```

then ask in plain product language, offering both exits — supply it now and continue, or drop to a
static-only suite (build + review, no run, no "validated" badge). Apply the answer and resume
autonomously; this is a one-shot ask at the point of discovery, not a new checkpoint habit.

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

### Progress fills — the page moves while the suite builds

At six milestones (Steps 1, 2, 3, the 3b pointer and the final run's steps 1 and 2 in the deliverable reference name
them) fill the overview page, so a customer watching the local page sees the suite advance (the page is published once,
at the run's end). It never ends the turn, and the chat says only its progress line:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step <milestone> --flowdir "$FLOWDIR"
```

A milestone never publishes: publish nothing and run no `done`, whatever the line says. Then run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" "$WORKDIR" --flowdir "$FLOWDIR"`
and say what it prints, verbatim (report doctrine, item 1). Ignore every field of the line; a failed fill moves on: no
retry, no word — the next fill catches the page up.

## Context budget — the binding constraint on every suite

A suite window grows to hundreds of thousands of tokens, and per-call latency tracks context size,
so most of a suite's model time is round-trip LATENCY, not deliberation and not waiting on jobs.
Four rules, and they cost nothing to follow:

1. **Route bulk payloads to FILES and read excerpts.** Job envelopes, critic reports (tens of KB
   each), edit-session responses and promoted YAML all land in context by default and stay
   there. Write them under `$ART/` and read the part you need:
   `mcp_call.py … > "$ART/job_<id>.json"` then `grep`/`Read` with an offset; the critic's report
   arrives as a task notification you can save before quoting; `plurai fetch policies`
   already writes the YAML to disk rather than through the window. **Never `cat` a whole
   artifact to see one field.**
2. **COMPACT deliberately before the critic phase.** The critic phase is where a window dies:
   it is the deepest point of the run and the most expensive per call. Compact BEFORE spawning
   the pass, not when the harness forces it — a forced compaction mid-loop sends the model back
   to re-read this skill through the shell and regrows the context.
3. **Re-read this skill BY SECTION, never whole.** A rule about the SECOND read and after: when
   you need a rule again, `Read` with an offset around the step you are in, or around the section
   of the deliverable file that owns Steps 3b-7. Re-reading either file whole to recover one
   sentence — this one is ~155 KB, the deliverable ~105 KB — is the single cheapest thing to
   stop doing. It does not touch the two FIRST reads, which are both in
   full and both mandatory — this file at invocation, the deliverable file once at Step 3b —
   because a step reconstructed from memory is a different and more expensive failure.
4. **One account window runs ONE suite.** Two suites started eleven seconds apart in one window
   both died on the same reset; the suite that ran alone consumed MORE and survived. Claim the
   window at Step 0 (below) so the condition is at least visible:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" window claim; echo "claim exit=$?"
   ```

   **The `; echo` is the Step-0 resume shape, for the same reason** — this is the last command in
   its block and it exits 4 in exactly the condition it exists to detect, so a bare call fails the
   step on a working claim, while `|| true` would hide the code the next sentence tells you to
   read. Exit 4 names the other suite's workdir and the time it started — or, in its third reading,
   says the claim file could not be parsed (ours is still written, and the line says so; an
   unreadable claim is never read as `free`). That is a REPORT, not a
   blocker — whether the window has room is the operator's call — so record it in the loop log
   (`append_loop_log.py --kind note --finding "second suite in this account window: <other>"`),
   say one line about it in the next progress message, and continue. The claim is released when this
   flow's window closes; a claim expires after 8 hours.

## Step 0 — args + preconditions + dirs

- **RESUME FIRST — a suite that died has a phase record, and re-entering from the top is how a
  landed set gets abandoned.** Before anything else:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase resume; echo "resume exit=$?"
  ```

  **`; echo "resume exit=$?"` is deliberate, and `|| true` is NOT the same thing.** This command
  REPORTS with its exit code, and `|| true` erases the very code the paragraph below tells you to
  read — measured: 0, 2 and 4 all arrive as `$? = 0` behind it, so every reading looks like
  agreement. The `echo` keeps the code visible AND makes the block's own status 0, which is what
  stops the Bash tool reporting a tool failure on a perfectly working resume. **This is the shape
  for every command in this flow whose exit code you are told to branch on** — the window claim
  above. `0` is the only reading
  that may be taken as agreement — the record and the markers agree. `4` means they disagree and
  the lines below say how. `2` is different in kind and is NOT "nothing recorded": it means
  `artifacts/suite_state.json` exists and this build must not touch it — truncated, unreadable,
  or written to a schema this plugin build does not know, and the message says which. Read the
  file, then either move it aside deliberately or run a build that reads its schema; do NOT
  record over it. **`5`** means the last build handed off without files the report page reads: each
  `deliverable_gap: <path> — <step>` line names one and the step that writes it, and
  `resume_phase: render` says where the repair starts. **Re-run Steps 4–6 only** (Step 4's meta, Step 5's
  bundle, build, render and `render landed`, then Step 6), on the printed ids — no generation, no run, no
  critic — unless this invocation asks for a NEW suite, which is a fresh build as under `resume_phase: none`.
  A `DIVERGED` / `MARKER-*` line printed beside it is read first, as under `4`. `render landed` and
  `report landed` themselves exit 2 and name each missing file while one is absent: write it with the
  named step and record the phase again.

  `resume_phase: none` is a fresh build — **but only when the command also exited 0.** Anything
  else names the phase
  to re-enter, the round counters, and the ids each phase landed (`id[scenario_set]`,
  `id[eval_set]`, `id[eval_revision]`); resume THERE with those, not from Step 0c. A `resume_job:`
  line names a backend job that phase started; its step's job lines (below) adopt it. A `DIVERGED`
  line means a marker file and the phase record disagree about an id — read both before acting:
  one measured session died at 21:50 with its final scenario batch landing at 21:57, so the
  marker named the superseded set and the record named the one that actually shipped. A
  `MARKER-MISSING` line is the same disagreement pointing the other way. A **`MARKER-ONLY`** line
  is the third shape and the likeliest of the three — an id sitting on disk that NO phase claims,
  which is what a session leaves when it dies between the job returning (the marker is written
  then) and the `phase record` call (a separate later one). Read the backend's own job list for
  that id before regenerating anything: it may be the deliverable. `resume_phase: none` under a
  `MARKER-ONLY` line is not a fresh build — it is a workdir carrying ids from a suite that
  recorded nothing.

  **Every phase records itself as it lands, written by the step that produced the id** — that is
  what makes the resume above possible, and each step below says where its record goes:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
    --phase <scan|corpus|world|scenarios|evals|qualification|critic|edit|final_scenarios|final_evals|render|report> \
    --status <started|landed|failed> [--round N] [--set <kind>=<id>] [--eval-revision <r>] [--job <id> [--key <k>]]
  ```

  **Both `source` lines belong in every block that runs it** — the harness resets shell state
  between Bash calls, so an unsourced `$PLURAI_PY` expands to nothing and the record is never
  written, which is the failure the record exists to prevent.

  **A phase that starts a backend job never pays for it twice.** The corpus build, eval generation,
  env generation and suite diagnostics each say "Step 0's job lines, `--phase <p>`" at their start.
  A phase that starts several jobs passes `--key <k>` on both lines (one corpus per knowledge base:
  the tool name). Before the FIRST start call of the phase (or of that key) after a resume:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase job --phase <p> [--key <k>]; echo "job exit=$?"
  ```

  Act on EACH line: `adopt:` names a job already started that is running or landed — make no start
  call for it; wait on it, or read its result, and continue from there. `start:` — start that job.
  (`0`: everything was adopted; `3`: something to start.) A DELIBERATE repeat call — a re-call with
  overrules or probes, "call step 3 again" — skips the check and starts fresh. Every start call is
  recorded the moment it returns, before any wait:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase <p> --status started --job <the returned id> [--key <k>]
  ```

- **`[context]`** (optional): a one-line prompt, a path to a PRD/policy `.md`, or
  `--zero-shot`. With context, build scenarios per the decisions it implies; with
  `--zero-shot` (or nothing), generate from the domain knowledge already in the
  shared library. Pass it through to generate-scenarios.
- **Preconditions:** `/simulation-scan-agent` has run — `$WORKDIR/plurai.config.yaml`,
  `scenario_variables.yaml`, `simulation_prd.md` exist. If `plurai.config.yaml` /
  `simulation_prd.md` is missing, tell the user (plain language) to run
  `/simulation-scan-agent` first.
- **ARM the client pin — the plugin + SDK commits every run in this loop must be made with:**

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/client_pin.py" arm --by simulation-generate-suite || exit 1
  ```

  It prints both commits (or, when a pin already exists, verifies the loaded client against it and
  exits 3 on a mismatch — STOP there). During a live `/simulate` run, a pin this loop armed where
  only the plugin moved is re-armed to the loaded plugin instead (`re-armed …`, exit 0): continue. From here on `resolve_plurai.py` refuses a runner from any
  other commit, so a plugin update or a re-synced venv mid-loop cannot mix clients into one suite.
  **A comparison loop — one meant to test specific client builds — reads the two printed commits against those
  builds before spending anything, and STOPS on a difference or on the `WARNING` that a half is
  unidentifiable** (that loop's runs cannot be verified by commit). Step 5 (deliverable file) releases the pin this
  step armed; a pin the user wrote by hand (`client_pin.py write`) outlives the loop.
- **The mock world is NOT a precondition — this command stands it up itself (Step 0c).** Scan
  maps the agent and writes the harness; the world is deployed HERE, immediately before the
  generation that consumes its state schema. So a missing `.env_id` / `.mock_service_url` is the
  NORMAL state of a freshly-scanned agent and is **never** a reason to send the user back to
  scan — scan can no longer produce them, and bouncing on them is a loop the user cannot break.
- **The RUN MODE is the script's, because Step 0c branches on it.** Run
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-mode`
  — it derives the posture from `plurai.config.yaml` and makes `$WORKDIR/.run_mode` say it; never
  write or decide it yourself. On `run_mode: mockless`, say so in one line ("no mockable tools —
  nothing is mocked in this run"). **Never call this posture "local"**: `local` means a mocked
  world hosted in-process. Step 0c then skips
  entirely: there is no mock to build; a harness-owned world is still seeded per scenario by `seed_state`. If ONLY `scenario_variables.yaml`
  is missing, do NOT send them back to scan — a workdir an older scan set up has none;
  run the **extract-scenario-variables skill**
  yourself (local, no backend job) and continue — before the grounding gate, whose corpus build reads it.
- Resolve `$WORKDIR` (named-env aware) and `$ART="$WORKDIR/artifacts"` exactly as
  `/simulation-scan-agent` Step 0 does — `cd "<agent dir>"` AND
  `export PLURAI_AGENT_PATH="<agent dir>"` in the SAME Bash call as the
  `source … resolve-workdir.sh`, repeated in every later call that re-sources it (the shell
  resets between calls). The `cd` SELECTS the root; the export only NARROWS it.
  `$ART` is already gitignored.
- **Open the flow window** (flow-fidelity contract): this command runs
  autonomously end-to-end, and the plugin's hooks enforce that mechanically — while the
  marker below exists, asking the user is DENIED unless the flow itself sanctions the
  ask, and your final message is style-validated against the report skeleton (Step 6).

  ```bash
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm generate-suite
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready generate-suite
  ```

  Arming clears the stale ask/report residue AND prunes `$ART/agent_findings.md` back to its
  `potential` sections and the scan's own — new build, new evidence epoch (a phase record ending short of `report
  landed` is an unfinished build, and resuming it keeps them; one ending on a `reset` — a rescan, or feedback that
  redoes the suite — is a new build). That prune is a **correctness** rule, not
  tidying: the quality loop appends every agent defect it catches to that file (Step 3), Step 6 (deliverable file)
  presents it, and a leftover file from an earlier build would present LAST build's findings as
  this one's evidence, against a run that no longer exists. **If the arm line says it cleared
  confirmed findings**, say so in one plain line before Step 1
  ("2 agent findings from the previous build are being cleared — they'll re-surface if they're
  still there"). Usually they do re-surface, and re-finding one is the honest proof it is still
  there — but not always: a static-only build never runs the agent at all, and the qualification
  sample is 3 diverse scenarios, not a re-run of the last three. Silent deletion is what makes
  that difference invisible. Say nothing about the `potential` sections: they survive the prune
  untouched, so nothing about them is changing. Before it prunes, the arm lists in `$ART/carried_criticals.json`
  each pruned confirmed finding on a `catastrophic` check (or `severity: critical`) that no `regressions/` folder
  replays; Step 3's pinned probes plant them. The file is internal: never present it.

  <!-- BEGIN step0: generated from ${CLAUDE_PLUGIN_ROOT}/docs/step0-block.md; edit there -->
  **If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

  Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

  **1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

  - A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close generate-suite`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
  - A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /simulation-generate-suite` (where the harness takes no arguments: "you run it for /simulation-generate-suite"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
  - A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff generate-suite` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

  **2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close generate-suite`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

  - Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
  - Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
  - Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
  - Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

  **3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready generate-suite --no-backend-tools` and act on the line it prints as above.
  <!-- END step0 -->

  Overwrite: nothing drives this command as a sub-step, so any other name already in the
  marker is a stale window from an earlier flow — stamp over it (that leak is what left a
  `scan-agent` marker validating a later run's report).

  **Then check what this step needs** — exit 3 prints one line: print it verbatim as the whole
  hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close generate-suite`, and stop:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" require suite "$WORKDIR"
  ```

  The window closes itself when Step 6's validated hand-off renders (see Step 6, deliverable file); early
  exits close it own-marker-guarded. The Step-0 `grounding-gate` is the flow's sanctioned ask —
  it writes the one-shot sanction marker immediately before its question; nothing else in this
  command may ask, except the two named exceptions in the decision table (a blocker per
  analyze-simulation's naming test, and a run prerequisite only the user holds) and the
  gates a driven sub-skill legitimately owns (`/build-rag-corpus`'s which-KB, extraction
  scope, anonymization posture and delete-confirm, which run inside THIS window). **A sanction buys one ask CALL, not
  one question** — `ask_guard` consumes it on the call, whatever it carries — so BATCH
  every question that gate needs into that single call; a second call is denied, silently,
  and a denial is not a stop.
- **One backend for the whole suite.** The MCP tools and the shell side resolve one configured
  backend. If a readiness or backend-identity check says they differ, STOP and resolve it NOW: the
  MCP tools would generate the sets on one backend while the runner targets the other, and the
  mismatch surfaces only as a 400 at Step 3 — after all the generation spend. The run step
  re-asserts this: the runner fetches the scenario set before any spend and stops with exit 65 on a
  404 (see `/simulation-run`). That 404 is the split, never a backend store fault.
- **Bridge runtime — the plugin installs its own, without asking, headless included, before any
  check below reads the bench.** One unconditional call; on a bench with no `bridge/bridge.map.json`
  it checks nothing and prints `runtime: null`:

  ```bash
  cd "<agent dir>" && export PLURAI_AGENT_PATH="<agent dir>"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/bridge_preflight.py" "$WORKDIR" --install
  [ -f "$ART/verification.json" ] && echo "VERIFICATION_RECORD=present" || echo "VERIFICATION_RECORD=absent"
  ```

  Read only `runtime` and `installed_now` from the preflight's JSON line and ignore its exit code (the
  same call runs the agent-dependency checks, which never stop this step). The runtime is the plugin's own
  prerequisite under `plurai_sim/`, never the user's install and never a run-gate install block.
  `installed_now: true` with `VERIFICATION_RECORD=present` → re-run the bridge smokes once before the
  gates below: `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse`
  (after `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"`), then continue; the gates below
  read its result. A `runtime` that is still a `bridge_runtime_*` code is one line quoting the
  finding's reason, then continue.
- **Grounding gate (before generating — grounding can't be added afterward without regenerating).**
  The knowledge corpus is a **REQUIRED input for a knowledge-bearing agent**, and this gate is
  where it is asked for. It is not one of the optional, non-blocking inputs below (traces, real
  records, document examples): those make an existing suite better, while the corpus decides
  whether retrieval is covered AT ALL — without one the suite contains 0 RAG scenarios. So the ASK
  is mandatory whenever the agent has a knowledge surface; the ANSWER stays the user's.
  - **RAG corpus — THIS command owns the corpus decision, and it is the only place that asks.**
    Does this agent have a knowledge surface? Read `grounding.needs_corpus` from
    `$ART/agent_topology.json` — scan RECORDED that verdict rather than building anything, so you
    are reading a fact, not re-deriving a judgement. If the block is absent (a topology written
    before scan recorded it), derive it here the way scan does: the agent owns a retrieval /
    vector-search / text2SQL tool (see the tool modes in `$WORKDIR/plurai.config.yaml`), **or** has
    prompt-injected knowledge (a closed doc set / FAQ / guides inlined into its system prompt —
    `/build-rag-corpus` Step 1's first-class no-tool case). No knowledge surface ⇒ skip this whole
    bullet (a pure transactional agent).

    <!-- BEGIN shared:stale-handle-test owner -->
    **STALE-HANDLE TEST — a recorded corpus id must RESOLVE on this backend before anything reads
    it.** List the recorded ids
    (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corpus_coordinate.py"`, plus the env manifest's when
    this repo uses named envs) and call `job_status(id=<id>)` on each: an id
    resolves iff `kind == "corpus"` and `status == "done"`. Branch on those two typed fields only —
    a deleted id and a failed build are both `status: failed`, so never on `status` alone, the
    `error` text or an empty `detail`. An id that does not resolve is a stale handle, not a
    recording: keep it as a `.corpus_scope` gloss line and clear it from the live markers and, under
    named envs, from the env manifest's copy through its own writer — that copy wins the resolution
    order. Never mark it `knowledge_planning: "off"`: the `ground_from` branch passes `off` bases.

    ```bash
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
    PLURAI_PY_HINT="${WORKDIR%%/plurai_sim*}/.venv/bin"   # the agent's interpreter, from any cwd inside it
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    "$PLURAI_PY" - "$WORKDIR" "${PLURAI_URL:-}" "${CLAUDE_PLUGIN_ROOT}" '<stale id>' <<'PY'
    import json, sys, time
    from pathlib import Path
    from urllib.parse import urlsplit
    workdir, url, plugin, stale = Path(sys.argv[1]), sys.argv[2], sys.argv[3], set(sys.argv[4:])
    def live_bases(bases):
        kept = []
        for base in bases:
            ids = base.get('corpus_ids') or []
            live = [i for i in ids if i not in stale]
            if ids and not live:
                continue  # every corpus this base named is stale: the entry goes, not 'off'
            if live != ids:
                base['corpus_ids'] = live
            kept.append(base)
        return kept
    kb = workdir / '.knowledge_bases'
    if kb.is_file():
        kept = live_bases(json.loads(kb.read_text(encoding='utf-8')))
        kb.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding='utf-8')
    for name in ('.corpus_ids', '.corpus_id'):
        path = workdir / name
        if path.is_file():
            live = [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                    if line.strip() and line.strip() not in stale]
            if live:
                path.write_text('\n'.join(live), encoding='utf-8')
            else:
                path.unlink()
    host, day = urlsplit(url).hostname or 'an unknown backend', time.strftime('%Y-%m-%d', time.gmtime())
    with open(workdir / '.corpus_scope', 'a', encoding='utf-8') as scope:
        for corpus_id in sorted(stale):
            scope.write(f'stale: {corpus_id} ({day}) — does not resolve on {host}\n')
    if (workdir / 'plurai.env.yaml').is_file():  # named envs: filter the manifest's OWN ids, keep its live ones
        sys.path.insert(0, f'{plugin}/skills/manage-envs/scripts')
        import env_manifest
        m = env_manifest.read_manifest(workdir)
        env_manifest.write_manifest(
            workdir, name=m.get('name') or workdir.name,
            corpus_id='' if m.get('corpus_id') in stale else None,
            corpus_ids=[i for i in m['corpus_ids'] if i not in stale] if m.get('corpus_ids') else None,
            knowledge_bases=live_bases(m['knowledge_bases']) if m.get('knowledge_bases') else None)
    PY
    ```
    <!-- END shared:stale-handle-test -->

    Fix it silently and say it in one line — a stale handle is yours, not the user's. The ladder
    below then reaches **No corpus recorded** by its own key.

    **Run the CORPUS-ANSWERABILITY CHECK below FIRST, and run it whether or not a corpus already
    exists.** It decides whether a planted knowledge question can be answered through any tool this
    agent actually has — so an EXISTING corpus needs it just as much as one you are about to build.
    Never make it conditional on the corpus being absent.

    - **CORPUS-ANSWERABILITY CHECK (run this FIRST, corpus or no corpus) — match the corpus's FACTS to the tool's fields, never
      "prose to fields".** A planted knowledge question is answerable when its ANSWER can
      surface through some declared `output_field` of a record-returning tool — and a
      corpus's facts are very often ATTRIBUTES, not prose: a hotel-inventory corpus knows
      nightly rates, availability, room types and ids, and scalar `output_fields`
      (`room_id, nightly_rate, availability, room_type`) carry those answers perfectly.
      "A cheaper room under X a night", "which of these two is free this weekend",
      "compare A and B" are fully gradeable retrieval questions on a scalar-only tool.
      Do NOT equate "the corpus was built from documents" with "the corpus's facts are
      document text".
      Read `output_fields` for each record-returning tool in
      `$WORKDIR/scenario_variables.yaml` (the same field the backend's knowledge planner
      reads); trust the CODE over the descriptor if you can see the serializer — a declared
      field the return shape does not carry is not a retrievable field (see
      extract-scenario-variables Step 2's verification rule).
      **A field the MODEL cannot ask for is not retrievable either.** That correction runs the
      other way: the field IS returned, but the documented contract never tells the model how to
      get it — a typed `## Agent facts` entry with predicate `undocumented_to_model` (or an
      `omits` fact on the serving tool) says exactly this. Read the PRD's facts block alongside
      `output_fields` and **discount any field a fact puts out of the model's documented reach**,
      per fact-kind, exactly as you would a field the serializer does not carry. A route the same
      sentence admits is undocumented does not establish `match`: the honest verdict is
      `degrades`, whose branch below already prunes unretrievable evidence per question and keeps
      decline probes — which is the outcome the backend's own scenario auditor prescribes when it
      flags `impossible_success_criteria`.
      **Guard: a `match` may not rest on a route the same sentence calls undocumented.** A line
      of the shape "`match` … retrievable through `<tool>(<arg>)` — the one door, undocumented to
      the model" is a `degrades`, and recording it as `match` plants answers only that door
      reaches: the agent's honest decline is then graded INCORRECT on every one of them, and the
      auditor that flags it gets overruled by citing the very fence the gap produced.
      Then classify the CORPUS'S FACT MIX, not the tool alone — and classify it from the RECORD, never from a fresh
      impression: read `.corpus_scope` and the corpus's own recorded shape (chunk kinds /
      field planner output). **First read `.corpus_scope` for an `answerability:` line — a
      verdict a previous run already reached and wrote down (the write is below). When one is
      there, that IS the verdict: state it and act on it, do not re-derive it.** A judgement
      re-made from scratch each time is a judgement that changes each time, which is how the
      same corpus got a different answer on consecutive runs. Only when no `answerability:`
      line exists do you classify, and then you write one.

      **A cached line is stale the moment one of its INPUTS moves, and only then.** The verdict is
      a function of three things — the knowledge bases in play, the tools' retrievable fields, and
      the corpus's own facts — so re-derive (and APPEND a new line, never edit the old one) when
      any of these changed since the line was written, and otherwise do not:
      - a `knowledge_bases` entry was **added or removed** — the set being classified is not the
        set that was classified;
      - a tool's **`output_fields` changed** in `scenario_variables.yaml`, or a `## Agent facts`
        entry newly puts a field out of the model's documented reach — the fence the verdict was
        computed against moved;
      - the **corpus was rebuilt** (a new `corpus_id`, or `/edit-env` re-authored answer-affecting
        content) — the facts being matched are not the facts that were matched;
      - the `dropped` × `ground_from:` re-check below fires, which is the one case that changes the
        verdict without changing any of the above.
      Anything else — a new operator, a fresh session, a differently-worded request — is not an
      invalidation. Say which of these you checked; "the cached line still holds" is only a
      statement if you looked.
      **If neither the line nor the record says anything, the mismatch is not established:
      proceed and say nothing** — a gate whose trigger is a judgement you re-derive each
      time is the shape that fired four spurious asks on the subagent gate:
      - **Attribute-heavy corpus (catalog, inventory, records)** × any tool that returns
        those attributes ⇒ **no mismatch — do not warn, do not ask, do not offer to drop
        `knowledge_bases`.** The backend's planner targets answerable facts per-question.
      - **Prose-heavy corpus (FAQ/guides/policy documents — the facts live in free text)**
        × tools with NO free-text `output_field` (`description`, `body`, `content`, …) ⇒
        the prose-only subset of questions degrades; the backend prunes unretrievable
        evidence per-question and keeps policy-prescribed decline probes, so the effect is
        FEWER knowledge turns reshaped toward attribute facts and declines — not zero, and
        not a dead corpus. Decide it yourself, without asking: proceed (the planner plans what
        is answerable), and also route the prose knowledge through the PRD when the prose
        facts are ones the agent is expected to state. Frame it as a scope note, never as
        "knowledge can only test declines".
      The judgment is per-FACT-KIND, never a wholesale per-tool verdict — do not
      re-broaden this check to condemn a tool contract.
      **Then ask the sharper question the fact-mix test does not: what does the corpus carry
      that the TOOLS DO NOT ALREADY RETURN?** Answerability and usefulness are different
      properties. A corpus whose every retrievable field is also an `output_field` of an
      ordinary tool adds no knowledge surface at all — a planted question is answerable by
      just calling the tool, so it tests retrieval of nothing — while the fields that WOULD be
      unique (free-text `description`, `body`, policy prose) are exactly the ones no tool
      returns. When that intersection is empty, knowledge turns are structurally unrealizable
      here no matter how the questions are planned: say so, plan none, and put the corpus's
      facts into the PRD instead. This is decidable right now from `output_fields` and the
      corpus's own field list — it costs nothing here, and a scored run plus a review pass to
      discover later.

      **RECORD THE VERDICT — every verdict, not only a drop.** The check above is the most
      consequential judgement in this step and it left no trace at all: the next run re-derived
      it from a fresh impression, or (with nothing in `.corpus_scope` to read) skipped it
      silently on the "not established" branch. One line into `$WORKDIR/.corpus_scope`, beside
      the `declared_units:` / `scope:` lines `/build-rag-corpus` wrote there — same plain
      `key: value` shape, and the file already travels with the repo as the human gloss on the
      corpus id:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
      # one of: match | degrades | dropped | grounding_only ; then the date, then one line of WHY
      printf 'answerability: %s (%s) — %s\n' '<verdict>' "$(date -u +%Y-%m-%d)" '<why, in one line>' \
        >> "$WORKDIR/.corpus_scope"
      ```

      | verdict | means | what the next run does with it |
      |---|---|---|
      | `match` | attribute-heavy corpus × tools that return those attributes, or prose × a free-text `output_field` | pass `knowledge_bases` without ceremony; no ask |
      | `degrades` | prose-heavy corpus × no free-text `output_field` | pass them, and repeat the scope note; the decision was already taken — do not re-decide |
      | `dropped` | the corpus adds nothing the tools do not already return — nothing is realizable | plan no knowledge turns; `.knowledge_bases` carries the machine-readable half (below) |
      | `grounding_only` | as `dropped`, AND `scenario_state_schema.yaml` declares `ground_from` | the corpus is still the anchor's only honest source: pass the bases with `knowledge_turns=0` — never detach them |

      **Grep `scenario_state_schema.yaml` for `ground_from:` whenever the answer is `dropped` —
      when you RECORD one and when you READ one back.** If it is there the verdict is
      `grounding_only`. Detaching a corpus the schema pins to leaves every pinned field authored
      from no source (the anchor is an invented record, and each one is a gate finding on the
      job), so a recorded `dropped` — written by an earlier run or an older plugin, and otherwise
      acted on verbatim — is a standing defect every later run inherits until it is read as
      `grounding_only`.

      APPEND, never rewrite the file — `declared_units:` is what the shortfall check at
      `/build-rag-corpus` Step 4 compares against, and it must survive. Re-running the check later is fine and the newest line wins;
      what must not happen is a run that reaches a verdict and writes none.

      **A drop ALSO needs the machine-readable half, because the consumer reads the manifest, not
      the gloss — so WRITE THAT DOWN TOO; do not just plan none this once.** `.knowledge_bases` is the manifest every later regeneration resolves from, and it
      still says the base is usable; the next `/generate-scenarios` (this session's Step 3b, a
      re-run next week, another operator) reads it, plants knowledge turns again, and buys the
      same structurally-unanswerable slots a second time. A verdict recorded only in the
      conversation is a verdict that expires with the turn. Mark the entry instead of deleting
      it — the corpus is a real backend artifact other flows (`/edit-env`) still reference:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
      python3 - "$WORKDIR/.knowledge_bases" '<kb_id>' '<one line: why nothing is realizable>' <<'PY'
      import json, sys
      path, kb_id, reason = sys.argv[1], sys.argv[2], sys.argv[3]
      bases = json.load(open(path))
      for base in bases:
          if base.get('kb_id') == kb_id:
              # 'off' — consumers must not pass this base. For a grounding_only verdict write
              # 'grounding_only' instead: consumers DO pass it, with knowledge_turns=0.
              base['knowledge_planning'] = 'off'
              base['dropped_reason'] = reason
      json.dump(bases, open(path, 'w'), indent=2, ensure_ascii=False)
      PY
      ```

      `knowledge_planning` / `dropped_reason` are bookkeeping keys like `kb_id` and `tool` — the
      backend consumes only `corpus_ids` + `label` and discards the rest, so they are inert on
      the wire and load-bearing on disk. Say the drop and its reason in the round's notes too,
      but the manifest is what the next run actually reads. Re-record the same entries into the
      env manifest when this repo uses named envs (the `env_manifest.py write
      --knowledge-bases` call in Step 1's corpus wait), or the env copy keeps the stale
      claim and wins the resolution order.

    Then, with the answerability verdict in hand:
    - **No corpus recorded** (no `corpus_id` / `knowledge_bases` on the env manifest, no
      `plurai_sim/.corpus_id`) → **ASK, then BUILD IT HERE** (sanctioned ask — first run
      `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction grounding-gate`). Put the
      consequence in the question itself, with no text before the call (under `/simulate` the flow
      answers this gate from the map's recorded sources, and a lead-in line would be left standing):
      without a corpus this suite contains **0 RAG scenarios** and the
      knowledge base is never tested, even though the tool (when there is one) still runs at sim
      time. When the stale-handle test just cleared an id, name it in the ask. On approval, run
      the `build-rag-corpus` skill as a sub-step of THIS flow and
      continue when it finishes — it never arms a window of its own, so its own
      sanctioned asks (which-KB, extraction scope, anonymization posture, delete-confirm) run
      inside this one. Do not send the user away to run it and come back. Chunk extraction runs
      **locally** — the customer's data-source credentials never leave the machine — and the
      skill delivers the corpus as a part of the grounding package and returns once the delivery is
      accepted; the backend builds the enriched graph + testset while the grounding job settles.
      **Before the next check, wait for that job to end and record the build** with Step 1's corpus wait
      (the recorded `.grounding_job`, `poll_job.sh` until the build ends it, then `grounding_corpus.py record`): the answerability
      verdict and the thin-corpus check below read the corpus it records, in `$WORKDIR/.corpus_id` and
      `$WORKDIR/.knowledge_bases`. A build that failed or came out degenerate records nothing: say so
      in product terms and apply the same two outcomes as a thin corpus (a smaller `--chunk-size`
      and a new delivery, or knowledge UNCOVERED). The declared default `build-from-recorded-sources`
      builds over exactly `grounding.sources`, read-only; a STOP names the gap. If the user declines,
      say knowledge is UNCOVERED; record `--answer proceed-without-corpus` in place of `--set`.
    - **A corpus IS recorded** → say which one, and what `.corpus_scope` says it covers. You still
      owe the answerability verdict above. Refresh its read-only display copy (every knowledge base in
      `.knowledge_bases`, for `artifacts/knowledge.md` and the page) in one call, and say nothing about it:
      `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_pull.py" knowledge "$WORKDIR"`
      — the same call after you mark a base `off` / `grounding_only` above.
    - **THIN CORPUS IS A DECISION, AND IT IS MADE HERE — before the draw, never after it.** The
      answerability check above asks whether the corpus's facts MATCH the tool surface. This asks
      the other question, and a corpus can pass the first and fail this one: is there ENOUGH of it
      to plant the knowledge turns you are about to request? The walk needs 2 hops per planted
      question and the planter dedupes near-identical questions, so **fewer than ~3 graph nodes per
      requested knowledge turn** is a corpus that cannot ground the ask. Take the
      node count from the recorder's line (`CORPUS … recorded corpus_id=… nodes=142 edges=318`) or the
      build's entry in the grounding job's `corpus_builds` — **never from `.corpus_scope`**, whose `declared_units` counts SOURCE
      DOCUMENTS and differs from node count by design (several chunks per file); substituting it
      STOPS every directory corpus as too thin. Unknown beats the nearest number. Requested turns
      are `knowledge_turns` × the EXPECTED knowledge slots — the last set's
      `detail.dataset_metadata.knowledge_slots`, or 1 per knowledge base before any set exists —
      never × the scenario count (knowledge rides a minority of the walks). The set's
      `dataset_metadata.knowledge_reach` (chunks planted of corpus chunks) is the suite's knowledge reach;
      the corpus build's testset spread is not.

      Two outcomes, both taken NOW: **lower `knowledge_turns`** to what the corpus can carry, or
      **enrich the corpus** (`/edit-env`, or a wider ingest) before drawing. Say which, in one
      line. What is NOT an outcome is drawing anyway and reading the result — a corpus too thin to
      ground the ask produces knowledge turns that come back "unrealized (EMPTY plan)" on every
      slot, and each rediscovery costs a full batch of wall clock and spend.

      **Then COUNT THE KNOWLEDGE SCENARIOS.** A knowledge slot whose plan came back empty is DEMOTED
      to a normal scenario so the batch ships: it looks full, the knowledge judges score N/A
      everywhere, and the set is quietly weaker than you asked for. Compare the knowledge scenarios
      in the shipped set with the knowledge slots you asked for — a shortfall is a SHORT BATCH,
      which this loop knows how to carry.
    - **Either way, record the id once the gate settles** — the adopted one as readily as a
      freshly built one, since `resume` cannot tell an id it never saw from one that does not
      exist:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
      "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
        --phase corpus --status landed --set corpus=<the corpus id>
      ```

      **Then re-bind the scan contract to that corpus** when this gate built it (or adopted one
      the scan's contract was not run over): the contract ran before the corpus existed, so the
      gate below reads it STALE until it runs over the built scope. The after-edit re-verify (the
      contract, the mock's contract and the smokes):

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
      "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse --after-edit
      ```
  - **Pre-generation contract gate (decide before spending — generation on a refuted premise
    spends the whole loop rediscovering it).** Read the contract through the gate, which decides from the
    RECORDED state — never from whether `$ART/scan_contract.json` happens to exist:

    ```bash
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" contract-gate; echo "GATE_EXIT=$?"
    ```

    | `GATE_EXIT` | meaning | what you do |
    |---|---|---|
    | `0` | PROCEED — `report.proceed` is true for the current `simulation_prd.md` | continue |
    | `1` | FIX FIRST — a finding holds: a typed fact the customer's corpus contradicts, a `required: true` closed set with no detector, or a corpus root that admitted no file | **Do not spend yet** — the decision below |
    | `2` | NOT VERIFIED — the last scan recorded the contract `not_verified` (it refused the bench's own inputs — a PRD it cannot project) or killed with no artifact, so no verdict exists | **Do not spend yet** — fix what the gate's line names and re-run `/simulation-scan-agent`; when nothing local can fix it, continue and carry the line into `$ART/loop_log.md`. Never a NOT-RUN line |
    | `3` | NOT RUN — no contract ran over this env (no checker, a crash, an invocation refusal, or never scanned) | one line saying so, then continue |
    | `4` | STALE — `$WORKDIR/.scan_contract_stale` quarantines the artifact, or `simulation_prd.md` changed after the contract (or its recorded result) was produced | re-run the scan's contract line (`bench_manifest.py verify --mode reuse` — it also re-runs `smoke_turn`, real paid turns, where a provider key is set), then the gate once more and follow its row; still `4` ⇒ treat it as NOT RUN, quoting the gate's line. Never read the artifact underneath |

    On `1`, the held findings are `report.gate_findings` in `$ART/scan_contract.json` — each with
    its `gate_id`, `fingerprint`, `evidence` (a contradicted fact's matched excerpts, a closed set's
    detector descent, an empty root's skipped-suffix census) and `remedy`. Decide each one alone,
    per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`; never ask the user:

    - **Repair what is in reach** — re-author the fact the excerpts refute (a negation-matched
      absence becomes an existence fact), declare the detector, widen `--suffixes` — and re-run
      the `verify --mode reuse --after-edit` line above, then the gate.
    - **Overrule it** when the evidence shows it is wrong for THIS agent (the excerpt does not say
      what the fact denies; the closed set is judge-only by design), then re-run that line and the
      gate. The checker reads the ledger itself, so the gate then reads `0`. An overrule releases the
      hold only: the fact stays contradicted and grounds nothing.

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
      "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/gate_ledger.py" overrule --art "$ART" --gate-id <gate_id> \
        --fingerprint <fingerprint> --reason "<what you verified, citing the corpus file, PRD section or agent file:line>"
      ```

    Re-running the contract re-fires its live smoke probes when `.live_probe_consent` grants them.
    An overrule that comes back `stale` (`$ART/gate_authority.json`) means the evidence moved:
    decide again on the new finding. No `report.gate_findings` at all means the installed checker
    predates overrules: read the fix-first items in `$ART/pre_generation_findings.md`, repair what
    is in reach, and continue over the rest you hold evidence against.

    The quarantine marker is the scan's own — written when it could not clear a previous verdict
    (an artifacts directory it may not write) — and it lifts by itself on the next scan that
    writes a verdict.
  - **Reference-store world checks — sweep again before Step 0c builds the env.** The corpus roots
    are recorded by now (the contract re-projection writes them), and the scan's own world-check
    sweep may have run before they existed. The sweep reads them itself:

    ```bash
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/measured_candidates.py" "${WORKDIR%%/plurai_sim*}" \
      --profile "$ART/data_profile.md" --workdir "$WORKDIR"
    ```

    Copy into `plurai.config.yaml` each `in` check whose `(members: …)` list sits under a reference
    store, or is a reference table, its stderr line names and that no declared check cites yet, under
    `world_state.entity_types.<the type its rows are>.checks`; skip a list no world type holds, and
    copy nothing else here. Nothing proposed ⇒ go on. The scan's `verify_config.py` line reports
    the same sweep as warnings (`world_check_unswept`, `world_check_stores_unclassified`), never a
    stop. A copied check reaches the scenario author through the env's pinned schema: Step 0c
    generating the env picks it up; when Step 0c deploys an existing env by id, re-pin it with
    `/edit-env` first.
  - **World reference rows — stash each declared `reference_rows:` table, right after that sweep.**
    The real rows of a simulated type the agent only reads become that type's rows in every world;
    the file is stashed, never inlined. The stash is pre-authorized by the invocation; never ask consent for it:

    ```bash
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require stash-doc)" || { [ $? -eq 3 ] && exit 1; }
    source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/world_reference_rows.py" --workdir "$WORKDIR" \
      --agent-root "${WORKDIR%%/plurai_sim*}" --plurai-bin "$PLURAI_BIN"
    ```

    It writes `$WORKDIR/.world_reference_rows.json`, which every scenario call passes as
    `world_reference_rows` (`/generate-scenarios`). `WORLD_REFERENCE_ROWS=NONE` ⇒ no type declares
    one, or none could be sent: go on. Each `WARNING(world_reference_rows):` line is a type whose
    worlds keep authored rows: one line in the loop log (`append_loop_log.py --kind note`), never a stop.
  - **Bench-verification gate (read what the scan actually executed, before spending).** First the
    one program that decides whether a run on this bench can produce signal, from the scan's
    recorded harness checks:

    ```bash
    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-gate; echo "RUN_GATE_EXIT=$?"
    ```

    | `RUN_GATE_EXIT` | meaning | what you do |
    |---|---|---|
    | `0` | RUNNABLE — no recorded check that verifies the harness failed or ran without a verdict, and no measured host gap left un-overruled | continue; a `shared world` reason is the user's opt-in, carried as a named risk that caps the suite at qualified |
    | `1` | NOT RUNNABLE — a smoke or `verify_*` check failed, a blocking smoke RAN and reached no verdict (a wrong agent interpreter, a timeout, an SDK that cannot observe the channel), the declared route smoke is not granted, or a `host toolchain:` line names a declared program or module measured missing, broken or failing where the agent runs it; each line names the fix and each `finding:` line the gate behind it, so a qualification run would spend a loop on a crash or grade what the host lacks. A failure the run measures (the agent raised, a turn came back silent) never lands here | **Do not spend yet** — each line is a finding you decide on (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`): apply the remedy it names, or overrule an advisory `finding:`, and re-run the gate; a remedy only the user holds (a key, a grant) is the run-prerequisite ask below. A `host toolchain:` gap you hold evidence no scenario reaches is overruled in the plugin's ledger (its `plugin.run_gate.host_gap` finding). The runner holds the same gap again as its own `host.requirement_gap` finding: overrule that one too, with the same reason. An install block the gate prints is the user's to run: hand it on verbatim as that ask; never run it yourself. |
    | `3` | NOT RECORDED — no `verification.json` (the bench predates the manifest) | one line saying so, then continue |

    Then read `$ART/verification.json` — the scan's own record of which checks ran, which were
    reused and which could not run:
    - `smoke_init`, `smoke_turn`, `smoke_pipeline`, the surface check or any probe `not_run` (or
      `not_verified`) ⇒ **one line quoting the reason**, then
      continue. A probe `failed` gets the same line, quoting its evidence: a non-blocking probe's
      failure leaves the exit code 0. Those are prerequisites the operator can fix (a provider key, a built `dist/`), and
      naming them here is the difference between a suite built on an ungated bench and one whose
      owner chose to build it anyway.
    - Any `fields` entry `declared_only` ⇒ nothing is blocked, but the suite is being built over
      claims this scan did not test. Carry the count into the run-integrity line of your own
      hand-off.
  - **Run prerequisites (the quality gate NEEDS a live run).** This command's whole
    value is the qualification run (Step 3); a suite that can't run gets only a static review and no
    "validated" badge. Check BOTH run preconditions NOW, before any generation spend:
    1. **Agent provider key** — verify it exactly as `/simulation-run` Step 0 does: run
       `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" provider-key; echo "PROVIDER_KEY_EXIT=$?"`
       (`0` present; `1` missing; `3` the bench names no provider, so infer it from the agent's
       code/imports or its `.env`). It reads the environment and the agent's `.env`; never test the
       variable in your own shell instead. The catch HERE is timing: a key present during the setup smoke does
       NOT persist across sessions — check NOW, before any generation spend.
    2. **Run target — OBTAINABLE now, LIVE after Step 0c.** A freshly-scanned agent has no
       deployed mock by design: Step 0c below deploys it. So what is checkable HERE is whether one
       can be stood up at all — either `.run_mode` says `mockless` (the run-mode line above;
       nothing to deploy), or the backend preflight below passes AND `plurai.config.yaml` +
       `simulation_prd.md` are on disk (env-gen's only inputs). A missing INPUT is the scan bounce
       above, not this gate. **The liveness check — `.mock_service_url` exists and answers — is
       re-asserted at the END of Step 0c**, which is still before any generation spend.
    If either is missing — or a `run-gate` line's remedy is something only the user holds (a `live_check_failed`
    line: a tool that runs for real cannot log in to or reach its service; quote its error, offer "fixed — check
    again" or stop, mocking it is a re-scan) — ASK the
    user (sanctioned ask — first run
    `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`; plain language, like the
    observability-key ask in `/simulation-run` Step 1a): provide it now, or explicitly choose a
    static-only suite (build + review, no run, no "validated" badge). Ask for the material, never
    for a verdict on a gate's line. NEVER silently build the full suite and discover at the
    end that it can't be scored — the user finds out at the finish line, after the spend.
  - **State the wall-clock shape UP FRONT (whether or not a gate question fires).**
    Before Step 1 begins, tell the user in one or two sentences what this command costs in
    TIME (never in dollars — spend is internal telemetry): on the FIRST run it stands up the
    agent's mocked world (**~10–60 min**, longer for a very large set of tools; a re-run re-deploys the existing world in seconds and
    skips that entirely), then generation runs at **3–10 minutes
    per scenario**, and the flow is two-phase — a 3-scenario qualification batch
    (~10–30 min), then the quality loop: a scored run, one independent review pass, one
    repair round and a scoped audit of what is still red (**~40–70 min** — usually the
    longest phase of the whole command), then the FINAL batch of 7 scenarios (~20–70 min).
    The whole command is measured in hours, not minutes, and runs autonomously — it asks no
    questions. An operator who first learns the shape at
    hour two was the live complaint this line exists to prevent.
  - **Traces / real records / documents** (optional, non-blocking) — mention any that apply and haven't
    run: production traces (`/analyze-traces`), a grounded scenario bundle (`/ground-scenarios`, when
    scenarios should start from a selected/current record or continue a real conversation), or
    document/artifact examples (`/upload-doc-examples`, whenever a scenario involves a document — not
    only extraction agents). The suite is more faithful with them; don't block on them.

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

## Step 0b — wait for the grounding, pull it and pin it

**This runs in every mode, a mockless agent included, after the preflight and before Step 0c** (Step 0c's
mockless skip never reaches it). The grounding ledger is the backend's record of what the agent's own
files, its traces and its corpus say about it (`${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md`). One suite
is built on ONE settled version of it, pinned here, so the env, the scenarios and the evals cannot be built
on different versions. The scan delivered the agent's descriptions; `/analyze-traces` and `/build-rag-corpus`
delivered more. Only your own edits deliver during this build and move the pin: an `/edit-env` session, and an input
edit of the loop (the PRD, the variables or the config), which delivers and re-pins by item 6. A trace or corpus
delivery, or a corpus build finishing, never moves it.

1. **Is there a delivery to wait for, and is a pin already on disk?**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "JOB=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
echo "WORKSPACE=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" workspace --workdir "$WORKDIR")"
echo "PIN=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pinned --workdir "$WORKDIR")"
echo "SEND=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" send --workdir "$WORKDIR")"
```

   `JOB=` is the grounding job the last delivery recorded on this machine. **Empty** means nothing was
   delivered from here (a fresh clone, a teammate's delivery), so there is nothing to wait for: skip item 2. With
   `WORKSPACE=` known, items 3 to 4 still run: the pull asks the backend for the newest grounding version of that
   workspace by its id and records the job it found, so item 3b and Step 1's corpus wait read that job, and the pin takes the version
   when it is settled. With `WORKSPACE=` empty too, nothing was ever delivered under this bench: skip items 2 to 4. A pin
   already on disk (a committed one, from a teammate or an earlier build) stands. `SEND=` is the one rule for what a
   generator call does with `ledger_version` (`SEND <id>`, `JOB <id>`, `OMIT waived until <expiry>` or `OMIT unpinned`);
   item 5 acts on it. With `PIN=` empty and no version pulled, go on to Step 0c: the generators make the unpinned call
   (item 5), and the backend says whether the setup is recorded. Never start a delivery from here.

2. **Wait for that job's version to settle**, not for the job to end: the knowledge-graph build of a corpus part
   runs on after the version settles, Step 0c's env generation starts beside it on the pinned version, and the
   scenarios and evals wait for it in Step 1's corpus wait. A job that is queued behind another delivery of the
   same workspace is waited for the same way. Read the job and ask whether it has settled:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "SETTLED=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.grounding_status.json.tmp" \
  && mv "$ART/.grounding_status.json.tmp" "$ART/.grounding_status.json" \
  || { rm -f "$ART/.grounding_status.json.tmp"; echo "SETTLED=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" settled --status "$ART/.grounding_status.json"; echo "grounding settle exit=$?"
```

   `0`: the wait is over: the version settled, or the job ended unsettled (a hold item 3b handles). `4`: still
   processing: wait in the foreground with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 60` and run the block again,
   in this turn. `SETTLED=unread` or exit `3`: read once more; a second: run the contact hand-off (`stop_message.py contact --done`). `1`: the job FAILED; it made no version, the mirror stays on the
   one before it, and a failed delivery is never retried from here. Read its `error` yourself with
   `job_status`; say nothing about it unless it names a description cause (Step 4b's table in
   `/simulation-scan-agent`), and then in those product terms.

3. **Pull the latest version into the mirror** — the pull block, whole (with no recorded job it asks for the workspace's
   newest version by its id):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

   Pin only after `MIRROR=pulled`. Any other line, or none, means the mirror may be behind the backend:
   pull once more, and if it is still not `pulled`, skip item 4, log it
   (`append_loop_log.py --kind note --finding "grounding mirror not pulled"`, no ids) and go on. A pin
   already on disk stands. Never edit the mirror and never read a version out of it by hand.

3b. **Handle the grounding's findings** — `${CLAUDE_PLUGIN_ROOT}/docs/grounding-findings.md` is the canonical
   handler; read it the first time. It runs after a `MIRROR=pulled` line and before the pin, in a fresh build
   only (a RESUMED build skips it, as it skips item 4). The agent decides alone: never ask the user about a
   finding or an overrule, and never put one in a hand-off or a report.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "FINDINGS=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.grounding_status.json.tmp" \
  && mv "$ART/.grounding_status.json.tmp" "$ART/.grounding_status.json" \
  || { rm -f "$ART/.grounding_status.json.tmp"; echo "FINDINGS=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" show --status "$ART/.grounding_status.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" log --status "$ART/.grounding_status.json" --art "$ART"
```

   Act on each finding by its `handle=`, holding ones first, as that document says. A trace part never holds and never
   stops this step: one whose analysis failed is `handle=accept`, the suite goes on without it, and the report carries one
   run-integrity line for it (`references/deliverable.md`, "The missing-traces line"). Only an evidence fault in a corpus
   part holds: remove a failed part (`handle=remove_part`) and deliver again (Step 4b items 2, 3, 4 and 5, never 2b, of `/simulation-scan-agent`). A value conflict never holds. It is
   `handle=accept` whatever its tiers: make no description edit and no delivery for it, however its remedy reads; the losing
   description keeps its value and `log` keeps the losing value as a candidate scenario. A code-versus-trace finding is `handle=accept`: the backend decides it, the mock keeps the code value, and `log`
   keeps the production value as a candidate trace scenario. After each delivery record it, repeat items 2 and 3,
   and read again. Three rounds that leave a holding finding end it: pin nothing and carry on to item 4.

3c. **The knowledge bases are recorded later**, in Step 1's corpus wait, before the first scenario call: the build
   runs on after the version settles, and nothing before Step 1 reads a corpus id. A failed build is reported there,
   and the suite decides whether to go on without that base.

4. **Pin the settled version.** On a RESUMED build (`resume_phase:` named a phase above) the pin on disk is
   this build's: skip this item. On a fresh build:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pin --workdir "$WORKDIR"; echo "pin exit=$?"
```

   The command takes no version: it pins the mirror's own, and only when that version is `settled`. It
   writes `$WORKDIR/.ledger_version` and, under a named env, the manifest's `ledger_version`.

   | exit | meaning | do |
   |---|---|---|
   | 0 `PINNED=<id>` | this build is pinned | go on |
   | 4 `UNSETTLED` | the latest version is not settled: a part is unprocessed, or a finding holds it | read `job_status(id=<the job>)` and log `detail.held_by`, `detail.waiting_on` and `detail.queued_behind` as counts: `append_loop_log.py --kind note --finding "grounding not settled: held_by=<n> waiting_on=<n>"`. **Pin nothing and go on; a pin already on disk stands.** A held version was item 3b's to handle, never a question for the user and never a line in the report; one still held after its three rounds: run the contact hand-off (`stop_message.py contact --done`). |
   | 5 `UNGROUNDED` | no mirror on disk although a job is recorded | pull again once; a second exit 5: run the contact hand-off (`stop_message.py contact --done`) |
   | 3 | the mirror is not a ledger version | pull again once; a second exit 3: run the contact hand-off (`stop_message.py contact --done`) |

5. **What the pin means for the generators.** Every generator call of THIS build, `generate_env_start`,
   `generate_scenarios_start` and `generate_evals_start`, carries the one id that item 4 pinned, as
   `ledger_version`, read by the skill that makes the call with `grounding_mirror.py pinned`. That
   parameter exists once a backend generator reads the ledger: a generator whose parameters in your tool
   list do not name `ledger_version` is called as before, with nothing added. A generator that names it
   and has no pin to send acts on `SEND=` (re-read with `grounding_mirror.py send`): `OMIT waived until <expiry>` sends the call
   without the argument; `OMIT unpinned` (no pin, no waiver marker and no recorded job: a fresh clone of a waived workspace
   looks like this, because the marker is machine-local) is the unpinned call, made without the argument, and the backend
   decides: it runs a waived workspace ungrounded and refuses any other with `ledger_version refused (missing)`, on which
   the build stops and the user is told once, in product terms, that `/simulation-scan-agent` records the agent's setup. A call that
   starts and reports `detail.grounding_waived` keeps the waiver in its marker (the generator skill's own step). `JOB <id>`
   is a delivery whose version is not pinned (unsettled or failed): that generator is NOT called, as item 4 says. No skill in this build
   re-reads the mirror to choose a version between generator calls, and a mirror pull in the middle of the
   build (every skill ends with one) changes the mirror and never the pin. No generator call delivers.

6. **An input edit delivers and re-pins.** When the loop changes the PRD, `scenario_variables.yaml` or
   `plurai.config.yaml` by hand, the generators read the edit only from the version it makes, so it is delivered and
   pinned before the next generator call. The plugin decides the value, so mark each key you changed (a deleted key or
   fact needs no mark: it leaves the next version): a returns block takes `type_source: plugin`, an error channel
   `error_channel_source: plugin`, and any other spec puts `plugin:<step>` first in its `evidence` (a PRD fact in its
   `source`), with `<step>` naming the loop step, e.g. `plugin:fact-retirement`. Then deliver through
   `/simulation-scan-agent` Step 4b items 2, 3, 4 and 5 (never 2b), wait for the job (item 2), pull (item 3), and
   re-pin as `/edit-env` does after its delivery:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pin --workdir "$WORKDIR"; echo "pin exit=$?"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_adopt.py" record --workdir "$WORKDIR" --edit-pin
```

   Read the pin exit by item 4's table. A disagreement the edit raises (the code or a trace says otherwise) is
   advisory: the edit's value is kept, so handle it by item 3b and deliver nothing more for it.

## Step 0c — stand up the world (the mock env)

**This runs before Step 1, always.** `/generate-scenarios` reads `$WORKDIR/scenario_state_schema.yaml`
— an ENV-GEN OUTPUT — and the backend seeds each scenario's world from it. Generate scenarios before
the world exists and a stateful agent gets EMPTY-world scenarios: `plurai-run-simulation` hard-fails
them with `ScenarioEmptyWorldError`, or runs them to impossible-request "failures" that read as agent
bugs and cost a full regeneration to diagnose. Standing the world up HERE also means it is generated
against the inputs as they stand NOW — `/build-rag-corpus` rewrites `simulation_prd.md` (its Step 6.5
reconciliation), and any published trace brief is auto-consumed by env-gen.

**Branch on what the workdir already holds. Reuse beats regeneration**, and regeneration without a
teardown LEAKS a deployed mock and its port:

1. **`$WORKDIR/.run_mode` says `mockless`** (Step 0's `run-mode` wrote it) → **skip this entire step.** A tool-less / all-real agent has no
   mock to build; a harness-owned world is still seeded per scenario by `seed_state`. One plain line, then Step 1.
2. **An env id is already on disk** → **DEPLOY it; do NOT regenerate.** Look in all three places, in
   order — `$WORKDIR/.env_id`, then `$WORKDIR/.mock_session_id` (older setups carry the SAME value
   there; `.env_id` is new, so a workdir set up before it existed has only this one), then the
   active env manifest's `env_id`:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   # env_manifest.py needs pyyaml (it RAISES without it, it does not degrade), and a bare
   # python3 is typically the system one that lacks it — prefer the agent's interpreter.
   # `${WORKDIR%%/plurai_sim*}` is the agent root under BOTH layouts (plurai_sim/ and
   # plurai_sim/envs/<name>/).
   PLURAI_PY_HINT="${WORKDIR%%/plurai_sim*}/.venv/bin"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   cat "$WORKDIR/.env_id" 2>/dev/null || cat "$WORKDIR/.mock_session_id" 2>/dev/null \
     || "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" read "$WORKDIR"
   ```

   **Deploy only an env this project generated:** the id must come from those three places. Never
   take one from `list_artifacts(kind="env")` by schema match — that workspace is shared, and a
   matching env belongs to another project or run. With no id on disk, generate (branch 3).

   Call `deploy_env(env_id=<id>)`: idempotent — it returns the live
   URL if the mock is already up, else re-spins the stored `simulation_env/` under the SAME stable
   `/mock/<env_id>/` path. No generation job, seconds not minutes. This is the normal path on every
   re-run, and it is the whole reason the id is persisted.
   - **Resolve and save the URL** by the `/generate-mock-env` Step 5.5 rule, restated here
     because this branch never opens that skill. `deploy_env` returns the same server-RELATIVE
     `/mock/<id>/` path env-gen does. Join it to `SERVER_URL`:
     `source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1`
     (it sources the resolver for you), then `${SERVER_URL%/}` + that path — the whole base, its
     path prefix included, never a rebuild from scheme+host. Write the ABSOLUTE result to
     `.mock_service_url`. **Call `backend_identity()` and paste its object**: this URL is read
     back as fact by `/simulation-run` and `/simulation-diagnose`, so an ambient host becomes a
     persisted one here, and the same call arms `$PLURAI_DIR/.backend_identity` for the session.
   - **The schema already on disk still needs the document annotation — this branch writes no
     schema, so nothing else on it will.** When `$WORKDIR/.document_fields` names a field that
     `$WORKDIR/scenario_state_schema.yaml` does not annotate, apply the document-field annotation
     rule — the section "Applying the
     document-field annotation rule" at the end of this step — before entering Step 1. It is local, free and needs no job.
     Already annotated, or no marker naming a field of this schema: byte-identical, say nothing.
   - Found the id anywhere other than `.env_id`? Write it there too (self-resolving:
     `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; printf %s "<id>" > "$WORKDIR/.env_id"`),
     so the next run takes the cheap branch directly.
   - **Check the world is not STALE before trusting it.** The env was generated from
     `plurai.config.yaml` + `simulation_prd.md`, and both move: `/build-rag-corpus` Step 6.5 rewrites
     the PRD, and the quality loop treats inputs as fix surfaces. Run
     `PLURAI_PY_HINT="${WORKDIR%%/plurai_sim*}/.venv/bin"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh";`
     `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" check "$WORKDIR"`
     (resolve `$PLURAI_PY` in the SAME Bash call — the harness resets the shell between calls)
     (same detector `/manage-envs` USE runs before the same call); if it reports `mock` stale,
     `undeploy_env(env_id=<id>)` FIRST and fall through to (3); if its answer has
     `generation_in_flight: true`, also `delete_artifact(id=<id>)`, because an undeploy does not
     stop a running generation and it would deploy the stale world when it finishes. Re-deploying a world built from a
     superseded PRD is how scenarios end up exercising behaviour the mock never implemented — every
     one fails, and the critic reads it as an agent defect.
   - **Check what moved in the grounding.** Right after that check, with the same block USE runs:

     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
     PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed, then stop. 1 = no runner: PLURAI_BIN stays empty and the check runs offline
     [ -n "$PLURAI_BIN" ] && source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" ""
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
     "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_staleness.py" --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
     ```

     Only `STALENESS=moved` matters. Log it as counts, no ids (`append_loop_log.py --kind note --finding
     "grounding moved: <entries_added> added <entries_changed> changed <findings_added> findings"`; `detail=unavailable`
     logs "grounding moved, diff unavailable"), and carry on: this suite stays on its pin, and a newer version is adopted
     at a stage boundary through the edit path, never inside a build. It never undeploys the world and never reaches the report.
   - **`deploy_env` errored?** Two different causes, two different answers. *The definition is gone*
     (deleted, or minted against a different backend) — clear the stale handle
     (`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; rm -f "$WORKDIR/.env_id" "$WORKDIR/.mock_session_id"`,
     and clear the manifest's `env_id` if that is where it came from) and fall through to (3).
     *The definition exists but the service did not come up* — `deploy_env` starts a fresh mock
     process from the stored definition, so a startup race or port clash need not recur: retry it
     ONCE; still
     failing, `undeploy_env(env_id=<id>)` BEFORE clearing anything, then fall through. Deleting the
     handle to a live definition without undeploying first leaks the very mock this branch protects.
     Fix both silently — a stale handle is yours, not the user's.
3. **No env id anywhere** → **generate one.** Run the **generate-mock-env skill**:
   it uploads `plurai.config.yaml` + `simulation_prd.md`
   (+ `test_mock_contract.py` when present), polls the env-gen job on a MINUTES cadence, and writes
   the markers below. That skill deploys and proves the world alive; it does not qualify anything —
   this command's Step 3 is the one quality gate. Its `generate_env_start` goes through Step 0's job
   lines, `--phase world`.
   - **Never start a second env-gen while a definition exists.** If the stored env is genuinely
     wrong for the current inputs, call `undeploy_env(env_id=<old>)` FIRST, then generate. When its
     answer has `generation_in_flight: true`, the old generation is still running and will deploy
     when it finishes: cancel it with `delete_artifact(id=<old>)` before generating. Nothing
     dedupes generation client-side: a second `generate_env_start` leaves the first mock deployed,
     holding its port, forever.
   - **Tell it to SKIP its Step-5.7 dimension-graph prewarm.** That call exists to overlap the
     graph build with a customer's reading pause, and this flow takes none — Step 1 follows
     immediately, so the prewarm is normally still building when the generation that would have
     used its cache starts, and the build runs twice. Nothing is lost by skipping it: `building_graph`
     is paid inside `generate_scenarios_start` exactly as it was before the cache existed.

**A world the env-contract gate withheld feeds the world's ONE `/edit-env` round; the mock gate below
shares it and never opens a second. The world's gates have one round, whichever skill opens it** (on branch 3,
`/generate-mock-env` opens it at its terminal poll). The world reads `smoke_contract_verdict ==
'blocking'`: the env job's `detail` with `deployed == false` (branch 3), or the `deploy_env` result,
which brought it up as-is (branch 2). When its failed checks name a mock defect, open that round with
those check lines: `detail.last_deploy_error` while the world is down, else the `checks[]` entries with `status: "failed"` in
`simulation_env/env_smoke_contract.json` (fetched with `fetch_env_file.py`, like the witness files
below). Its commit redeploys; with no commit, `deploy_env(env_id=<id>)` brings the world up as-is.
Either way the checks still failing are a named risk on the release verdict (internal), never a stop;
never loop, never ask the user. The customer line names the tools those checks name (the customer's
own tool names) as not yet served, never as answering; check ids and `last_deploy_error` stay internal.

**When this step ends, these must be on disk. A missing one is a defect, not a warning** — unless
branch 1 skipped the step, in which case a `mockless` bench legitimately has none of them:

- **`$WORKDIR/.env_id`** — the env id, one line, no trailing newline.
- **`$WORKDIR/.mock_service_url`** — the RESOLVED ABSOLUTE URL, per `/generate-mock-env` Step 5.5's
  rule (`detail.mock_service_url` is a server-RELATIVE `/mock/<id>/` path joined as
  `${SERVER_URL%/}` + that path, `SERVER_URL` coming from
  `${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh`, path prefix included).
- **`$WORKDIR/scenario_state_schema.yaml`** — whenever the env has one. On the REUSE branch it is
  normally already there from the generation that minted the id; on a fresh clone or a cleaned
  workdir it is NOT, and its absence is silent in the worst way (the empty-world failure above).
  Re-read it from the env job — `job_status(id=<env_id>)` → `detail.scenario_state_schema_yaml` —
  and write it verbatim, **then apply the document-field annotation rule to what you just wrote**
  (the section "Applying the document-field annotation rule" at the end
  of this step; the stored envelope keeps the UNANNOTATED bytes forever, so
  every re-materialisation re-applies it — idempotent, byte-identical when there is nothing to add).
  A null value means a stateless agent whose tools self-create their
  entities: correct, skip the save. **Never enter Step 1 with a stateful agent and no schema.**
- **The env manifest**, when this repo uses named envs (`plurai_sim/envs/` exists), so the world
  stays re-activatable and committable.

**Then record the phase**, whichever branch stood the world up (branch 1's `mockless` skip is the
one case with no env id to record — say so and move on). The marker is what the next STEP reads;
the record is what the next SESSION reads, and only one of the two says which phase produced the
id:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
  --phase world --status landed --set env="$(cat "$WORKDIR/.env_id")"
```

When the world's `verification` (the `deploy_env` result, or `job_status` `detail`) is not `passed`, add `--note "<verification_detail>"` to that record.

**Then run the seed check (accepted + read back), on either branch** — `/generate-mock-env` Step 6.6. A refusal
(exit 1, `seed_world_refused`) on that schema-built world is recorded (below) and never repaired, sent to `/edit-env`
or overruled: the sanity pass (Step 2a) Inits the REAL qualification seeds, and an Init refusal there is the repair. A
read-back gap is advisory: Step 6.6 routes it to the harness lane for one round.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase world --status landed --set env="$(cat "$WORKDIR/.env_id")" --note "seed check: <the verdict line>"
```

**Then the mock gate, on either branch — ONCE; it warns and never blocks.**
Fetch the world's witness tests into `$WORKDIR/mock_witness/` with the script, never through `fetch_artifact` (it
pages files over the wire cap; a `NOT_FETCHED` line is a recorded coverage gap the gate reads as no_verdict, never a
stop), then:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_env_file.py" --env "$(cat "$WORKDIR/.env_id")" --out-dir "$WORKDIR/mock_witness" \
  --path simulation_env/tests/test_mock_witness.py --path simulation_env/tests/_mock_witness.py \
  --path simulation_env/tests/mock_witness.json
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_env_file.py" --env "$(cat "$WORKDIR/.env_id")" --out-dir "$WORKDIR/mock_witness/backend_run" \
  --path contract_report.json --path contract_answers.json --path simulation_env/tests/test_mock_contract.py
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/tool_python.py" mock_contract "${CLAUDE_PLUGIN_ROOT}/scripts/mock_contract_check.py" \
  preflight "$WORKDIR/mock_witness/test_mock_witness.py"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse --only mock_contract
"${PLURAI_CHILD_PYTHON:-$PLURAI_PY}" "${CLAUDE_PLUGIN_ROOT}/scripts/real_reader_probe.py" --workdir "$WORKDIR"
"${PLURAI_CHILD_PYTHON:-$PLURAI_PY}" "${CLAUDE_PLUGIN_ROOT}/scripts/mock_shape_probe.py" --workdir "$WORKDIR"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" mock-gate
```

The second fetch brings the backend's own last run of this world; the gate reads its per-test outcomes instead of
re-running them when that run measured exactly the deployed mock, and otherwise re-runs (a `NOT_FETCHED` there is no gap).

A `mock_shape_mismatch` WARN says the mock's answer does not fit the agent's own response type, naming the tool and the
field. It is a mock defect unless the tool's pass-through in `simulate.py` reshapes the answer or the named test's row
lacks a required field: read both first.

`MOCK_GATE=READY` — continue. `MOCK_GATE=WARN` — when a `WARN` line (or a preflight line) names a mock defect, open ONE
`/edit-env` round with those lines and re-run the block once on its finalize, unless the env-contract round above
already spent the world's one round (then those lines are named risks); a `mock_target_unresolved` line is no mock
defect — the mock stopped answering: re-run the block once (`deploy_env` first on a 410); then go to Step 1 whatever it reads. A
tool still unwitnessed is a named risk on the release verdict (internal; a real reader the probe did not witness seeing
a mocked write is also said to the customer, as a read-back gap), never a stop; never replace a granted real
reader with a mock to clear it. `MOCK_GATE=NOT_RECORDED` — go to Step 1. Never loop on the gate; never ask the user.
Waivers (`mock-gate --waive <tool> --because no_reader|self_reader|internal_bookkeeping --cite "<…>" --owner
backend|operator`) are optional and internal: never on a customer artifact.

**Then RE-CHECK the run target — the second half of Step 0's run-prerequisite check, and the last moment
before generation spend.** Probe `.mock_service_url` (skip entirely when `.run_mode` is `mockless`).
**Send the bearer** — `/mock/<id>/` sits behind the same auth as the rest of the backend, so a probe
without it gets a `401` from a perfectly healthy world. Do not write the request yourself (a `curl -H
"Authorization: Bearer …"` puts the token in a command line every process can read): the plugin's
prober sends it, from the environment, to the backend this session is bound to and no other host:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/probe_mock.sh" "$(cat "$WORKDIR/.mock_service_url")" --timing 1
```

Its `GET /health -> <status> <seconds>s` line is the answer to dispatch on (exit 1 is a non-2xx).
Then dispatch on what came back, because this command OWNS the world and most of these are
yours to fix, not the user's:

- **`503 restarting`** — the liveness watcher is already bringing it back. Wait and re-probe.
- **`410 needs_redeploy`** — call `deploy_env(env_id=<id>)` once more; it is idempotent.
- **`401`** — your probe, not the world. Re-probe with the token.
- **Any other persistent non-2xx, after one `deploy_env` retry** — this is a backend fault you
  cannot fix from here: the retry above is this skill's repair step, so if the
  probe still fails: run the contact hand-off (`stop_message.py contact --done`) and STOP. Do **not** offer the static-only suite as the escape from a
  world that could not be stood up — that ships seven scenarios seeded against no world, which the
  customer only discovers when every one of them fails at run time.

The run-prerequisite ask (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`)
is for the case it was written for: something only the USER can supply — a missing provider key, a
run target they must point you at. A mock this command deployed is never that. A world discovered
dead at Step 3 costs the entire generation spend; that is why the probe is here and not there.

**A world can answer and still be too slow to run against — measure that here too.** A single
2xx says the mock is alive, not that it is usable: a stalling network path returns healthy
probes while every scored turn pays tens of seconds per state read. Run the same prober with
`--timing 5` (**five sequential authenticated GETs**, one status and time per line, then
`timing: n=5 median=…s max=…s`) and read the distribution: median under 1.5 s and max under 10 s
PASSES. Record the verdict so the whole build agrees on one transport:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo remote > "$WORKDIR/.qual_transport"
```

On FAIL, retry `deploy_env` once (it starts a fresh mock process, so a slow or racing start need
not recur); if it still fails, fall back to serving the world locally
rather than scoring against a stalling path:

1. `fetch_artifact(<env_id>)` and unpack `simulation_env/` (mocks, `run.py`).
   A body that is a placeholder (`<name omitted — reason>`, or `{"schema": "omitted", …}` for JSON) is not that file. A `withheld_files` entry with `sha256` is fetched with `fetch_env_file.py --path <path>`, not written from the body; for any other placeholder, stop and name the file and its reason instead of writing it.
2. Serve it locally and point `.mock_service_url` at `http://127.0.0.1:<port>/`. If the process will not start
   or its port never answers, the world cannot be served at all: stop and say so — that is infeasibility, not a gate.
3. **Parity tier — run it ONCE:** run the mock acceptance tier against the local surface, with
   **`PLURAI_MOCK_URL` pointed at the port you just served on** and `PLURAI_TOKEN`
   exported into the run. Both, and `PLURAI_MOCK_URL` first: unset, the tier falls back to
   `http://localhost:8199`, so anything already listening there answers about a world it never unpacked;
   without the token every test errors at fixture setup. The tier is three files — `test_mock_contract.py`
   plus `conftest.py` and `test_agent_envelope.py` — so run the directory, not one path, and keep its
   output. Then record the result, whatever it is:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" local-parity --exit-code <tier exit> --log <tier output>
   ```

   `0` prints `LOCAL_PARITY=PROVEN`. Any other exit (`3` means it verified nothing) prints
   `LOCAL_PARITY=UNPROVEN` and records `local_world_parity_unproven` with the failing tests' first error
   lines: an internal named risk the release verdict carries (rung `qualified`), never on a customer
   artifact. Either way, go on and score against the local world — never re-run the tier for a green,
   never loop, never ask the user.
4. `echo local-served > "$WORKDIR/.qual_transport"`.

**Every qualification run and re-score in this build uses the transport `.qual_transport`
records — never a mix.** Two transports in one build make verdicts incomparable: a slow one
produces timeout failures that read as agent defects. The record is per BUILD; `/simulation-run`
re-probes for the deliverable run, and if it moves back to the deployed world that is fine —
a transport change on the scored run is disclosed as the report's one run-integrity line.

**Report it as PROGRESS, never as a hand-off.** One line, product language, no ids, no job phases,
no spend — and matched to the branch you took, because the estimate is what the user plans around:
generating (branch 3) is *"Standing up your agent's world — about 10-60 minutes, longer for a very large set of tools."*; re-deploying an
existing world (branch 2) is seconds, so say *"Bringing your agent's world back up."* and do not
quote minutes.

**Wait in the BACKGROUND** — `poll_job.sh` as a wait in the background, which re-invokes you
when the SCRIPT exits. A foreground poll blocks the model for the whole wait, and that is the
single largest measured block of a suite window: 72-86 minutes per suite spent doing nothing but
holding a turn open. There is always something to do with a backgrounded wait — stage the next
step's inputs, read the artifacts already on disk — and the interleaved
lane (Step 3, now the default) exists to have that work ready. The env wait has a fixed job: when
`$WORKDIR/eval_intent.yaml` is absent, author it then, by Step 2's rules ("THE INTENT HAS A SCHEMA"), from the PRD,
the variables, the scan and `[context]`; no scenario is one of its inputs. It is Step 2's input only: Step 1 steers its
draw as it would without it. The turn that ends while the job
runs takes the in-progress anchor `Suite: in progress`, and you continue in the turn the
notification opens. That line carries a minutes figure and says the session resumes by itself —
*"Suite: in progress — about 10-20 minutes; this session picks up again by itself."* A vague
"as soon as it lands" or "a few more minutes" is blocked and re-rendered. It is the ONE status
line per wait: say nothing earlier in the turn that it will repeat. While the quality loop runs (the
qualification run, the critic, the repair round) that line names the work only as calibrating the
simulation — *"Suite: in progress — calibrating the simulation, about 10-20 minutes; this session picks up
again by itself."* — never the runs, their conversations or scores, a reviewer, or what was fixed.
The suite's own final run (after `final_scenarios` landed) is not calibration: its line is *"Suite: in progress —
scoring your agent on the final suite, about 30-60 minutes; this session picks up again by itself."*
**A wait on a RUN takes its minutes from the measured qualification clock**, not those fixed figures:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/runs_index.py" eta "$WORKDIR" --scenarios <N>` prints
them (after `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"`); the fixed figures are for every other wait, and for a run only when it prints nothing.

**The exception is a headless session, or any session that nothing re-invokes** (`claude -p`, `workspace.run_driver: headless`,
`PLURAI_SIM_NONINTERACTIVE=1`, a prompt that says to wait for the job, or a harness whose
wait-in-the-background entry says nothing wakes you). There the session ends with the turn, so no
notification ever re-invokes you, and the line above is false: say no "picks up again by
itself" there. Poll in the FOREGROUND (`timeout: 600000` on the Bash call), re-invoke on exit 14
in the same turn (on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)), and do not end the turn until the job is `done` or `failed`.

**One invocation is NOT one wait, and env-gen is the case that proves it.** The script's budget
caps at 540 s of wall clock, so against the 10-60 minutes this step just estimated it will
usually come back at nine minutes with **exit 14 — budget spent, job still RUNNING, backend
healthy**. That is not a failure and not a stall: **re-invoke it, backgrounded again, on the same
job id**, as many times as it takes. Read the EXIT CODE before anything else — `0` landed, `13`
failed, `14` keep waiting, `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`); every other code means the poll itself could not answer, and the
script's own header says which is which. Treating an exit 14 as "done" is how this step proceeds
against an env that does not exist yet.

*The one case for the foreground:* a wait you expect to be seconds, not minutes — re-deploying an
existing world (branch 2). Then use `poll_job.sh` **with `timeout: 600000` on the Bash call** (the
harness default is 120 000 ms and the script's own budget is 540 s, so a bare call is killed 420 s
inside its budget and reads as a wedged backend). Never for a generation
job: "it might be quick" is how a ten-minute foreground wait gets bought.

**The route smoke — only when the bench declares one.** A bench whose tools are intercepted at the
agent's host declares the command that proves that route as `harness.tool_interception.route_smoke`.
Run it in Step 1, once `generate_scenarios_start` has returned its job id and before you wait on that job: no
scenario reads the route, so it proves the route while the batch generates. Its verdict stands before the first
qualification run; when it stops on something only the user holds, `cancel_job` the scenario job first:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
ROUTE_SMOKE="$(python3 -c 'import json, sys; print(((json.load(open(sys.argv[1])).get("harness") or {}).get("tool_interception") or {}).get("route_smoke") or "")' "$WORKDIR/bench_manifest.json")"
WITNESS="${CLAUDE_PLUGIN_ROOT}/scripts/route_witness.py"
[ -z "$ROUTE_SMOKE" ] || if ! python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_workdir_copies.py" "$WORKDIR"; then echo "ROUTE_SMOKE_EXIT=stale_copies"
elif ! SID="$(python3 "$WITNESS" open "$WORKDIR")"; then echo "ROUTE_SMOKE_EXIT=no_witness_session"
else WORKDIR="$WORKDIR" PLURAI_ROUTE_SMOKE_SESSION="$SID" bash -c "$ROUTE_SMOKE"; rc=$?
  [ "$rc" != 0 ] || python3 "$WITNESS" check "$WORKDIR" "$SID" || rc=unwitnessed
  echo "ROUTE_SMOKE_EXIT=$rc"
  [ "$rc" = 0 ] || bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker; fi
```

Bringing the route up writes the customer's system, so it runs only on the operator's grant:
`route_smoke` in `plurai_sim/.host_effects_consent`, asked at the scan's host-effects question or the `/simulate` start.
A non-zero exit is a missing run prerequisite (exception 2) when its output names something only the
user holds (the grant, a credential): ask with that output as the one remedy; never write the grant
yourself. In a `/simulate` run, a yes to a grant it names is `simulate_plan.py traces "$WORKDIR" --org-access approve:<that name>`. Any other non-zero exit is a route fault to repair (redeploy, re-run) — no ask. Run it again after any env regeneration (the route targets one env
id), and tell the user once that the same script's `down` takes the route down after `/simulation-run`.

The exit alone proves nothing about where the copy's calls go, so the block opens a fresh session on the deployed mock and
hands it to the smoke as `$PLURAI_ROUTE_SMOKE_SESSION`: the smoke drives one call through the copy carrying it as the
copy's mock session, and a 0 exit stands only when the mock recorded that call (`route_witness.py`; a route whose rewrite
plan stands beside the workdir proves its own callout and gets no session). `unwitnessed` is a route fault — the copy is
not calling the mock — to repair, never to ask about; `no_witness_session` means the world is not up: run Step 0c again.

`ROUTE_SMOKE_EXIT=stale_copies` means the route runs from plugin files copied into the workdir, and
this bench's copies differ from this plugin's, so bringing it up would not apply this plugin's rules. The lines
above it are the exact commands that re-sync them: run them unchanged, then run this block again.
It is not a prerequisite to ask about; never edit a copy instead.

### Applying the document-field annotation rule

<!-- BEGIN shared:document-field-annotation-rule copy: generated from the extract-scenario-variables skill; edit there -->
This rule hangs on the ACT, not on this branch: **whenever `<plurai_dir>/scenario_state_schema.yaml`
is written — hand-authored here, or the backend's `detail.scenario_state_schema_yaml` written
VERBATIM by another skill — every field named in `plurai_sim/.document_fields` carries exactly this
block, and the write is not finished until it does.** The writers of that file apply this rule:
`/generate-mock-env` Step 6.4b, `/simulation-generate-suite` Step 0c
(both the deploy branch and the on-disk bullet), `/edit-env`'s schema note.

```yaml
agent_context_fields:
  <field>:                # the marker entry's `field` — whatever the schema calls it
    # ...the field's own keys, untouched...
    generated_document:
      format: <format>    # the marker entry's `format`, copied
      brief: <brief>      # the marker entry's `brief`, copied
    owner: user           # what the user simulator's hold-set reads: WITHOUT this key the
                          # simulator is never told it is carrying the document
    inject: request       # the channel that delivers the value into the turn-1 payload
```

**Idempotent, and scoped to what is declared.** A field that already declares `generated_document`
is left BYTE-IDENTICAL — re-applying the rule is a no-op, and a writer must never reflow bytes it
did not author. Of the other two keys only the ones the field does not already declare are added: a
field that already says `inject: [request, store]` because it also seeds world rows keeps exactly
that. A field the marker does not name is untouched; with no `.document_fields`, or with no schema
field of that name, the file stays byte-identical to what the writer produced. Only the three keys
above are added, and `format` / `brief` are COPIED from the marker entry — never re-worded here, and
never invented for a field the marker does not declare. **One shape is a contradiction, not a
merge**: a field the marker names that already declares an `owner` OTHER than `user` is the marker
and the schema disagreeing about who carries the document — say so and stop; do not overwrite the
authored declaration.

**Three consequences, true the moment a schema carries the block — say them once to the customer.**
The backend unions the two triggers (it collects `generated_document` blocks from the schema, then
lets an explicit `document_fields` entry win by field name), so (1) turning document generation OFF
now means removing the block from the schema AS WELL AS the entry from `.document_fields` —
deleting the marker alone no longer stops it; and (2) renaming the field in the marker without
renaming it in the schema declares TWO document fields, so two documents are generated and billed,
one of them into a field nothing reads. And (3) ownership is read PER SCHEMA, not per field: once
any field says `owner: user`, every user-voiced `agent_context` fact in that scenario is handed to
the user simulator — including host-owned session context that is not printed on the document. A
fact the AGENT must produce is still withheld (that condition is the voicing one, untouched), but
a schema mixing a document with host context hands the simulator more than the document.
<!-- END shared:document-field-annotation-rule -->

## Step 1 — generate scenarios

**The world is already standing (Step 0c).** If `.mock_service_url` is absent and `.run_mode` is
not `mockless`, you skipped Step 0c — go back and run it. Scenarios generated without
`scenario_state_schema.yaml` seed an empty world, and every one of them is wasted.

**The corpus wait — record the knowledge bases before the first scenario call.** The grounding job builds each
corpus part delivered by `/build-rag-corpus` after the version settles, so Step 0c generated the env while it built;
the scenarios and the evals wait for it here, on the same pinned version, and take their corpus ids from the job's
`corpus_builds` (`part`, `label`, `job_id`, `status`, `corpus_id`, `node_count`, `edge_count`, `error`). The build's
end moves no pin. First read how many delivered corpus parts have no outcome yet (the read block `/generate-scenarios`
carries):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "JOB=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" pending --workdir "$WORKDIR"
```

`PENDING=0`: go straight to the record block below. Otherwise wait for the job to end (the build ends it) in the
foreground, with `timeout: 600000` on the Bash call:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <the job id> 540; echo "corpus wait exit=$?"` (exit 14: wait again,
in this turn; exit 13: the job failed and nothing is recorded; on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)). Then record the builds. The recorder applies the corpus
health gate (a graph the knowledge planner cannot walk plants no knowledge question) and writes `.knowledge_bases` and
`.corpus_id` for the healthy builds. A build is handled once, so later suites neither repeat the report nor write back a
base this suite cleared or marked.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "CORPUS=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.grounding_status.json.tmp" \
  && mv "$ART/.grounding_status.json.tmp" "$ART/.grounding_status.json" || { rm -f "$ART/.grounding_status.json.tmp"; echo "CORPUS=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" record --workdir "$WORKDIR" --status "$ART/.grounding_status.json"; echo "corpus record exit=$?"
```

Each build prints one `CORPUS <kb_id> <state>` line and the last line sums them. Read the exit:

| exit | meaning | do |
|---|---|---|
| 0 | every build is recorded or was handled before; no build at all prints only the `RESULT` line | go on |
| 0, no build | only the `RESULT` line, or `CORPUS=none`, while the map's `grounding.needs_corpus` is true | say once, in one plain line, that no knowledge base was built and why — with `$ART/.extractor_missing` present, the why is its `error`: the document reader could not be installed — then go on |
| 1 | a build `failed`, came out `degenerate`, or is `unregistered` | `failed`: read its `error` with `job_status` yourself and classify it (the error posture; never show it), then say in product terms that the knowledge source could not be built and the suite goes on without it. `degenerate`: say the documents are too thin or too flat to ground retrieval scenarios, and that `/build-rag-corpus` with a smaller `--chunk-size` delivers a replacement. `unregistered`: another machine delivered it; say nothing and record nothing. **Nothing is recorded for these**, so the corpus gate below finds no corpus for that base. |
| 4 | a build is still `waiting`: it is running | wait for the job to end (above) and run this block again |
| 3 | the status or a marker cannot be read | run the contact hand-off (`stop_message.py contact --done`); record nothing |

After a recorded build, put the bases where the other consumers read them. Under named envs
(`plurai_sim/envs/`) the active env manifest holds a copy that wins the resolution order:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
[ -f "$PLURAI_DIR/.knowledge_bases" ] && python3 "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" write \
  "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" \
  --knowledge-bases "$(cat "$PLURAI_DIR/.knowledge_bases")"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .knowledge_bases .corpus_id
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_pull.py" knowledge "$WORKDIR"
```

`stage-inputs.sh` names files that must exist: run these lines only after a `recorded` line (the grounding
pull prints one JSON line whose `totals_line` the suite's summary uses; exit 1 or 2 changes nothing, say
nothing about it). The backend is the ground truth: `artifacts/knowledge/` is a display copy that is never
read back for a decision.

Run the **generate-scenarios skill**,
forwarding `[context]`. A bench that declares a route smoke runs it (Step 0c, "The route smoke") as soon as that
skill's `generate_scenarios_start` has returned its job id, before the wait. The skill writes `$WORKDIR/.scenario_set_id`; **record that id into the phase
record too** — a marker file is untyped IPC: an id that only ever reaches disk is an id
`phase resume` reads as MARKER-ONLY, produced by nothing it can name.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
  --phase scenarios --status landed --set scenario_set="$(cat "$WORKDIR/.scenario_set_id")" \
  --landed "<successful_scenarios>" --requested "<total_scenarios>" \
  --lost-kind "<the lost slots' gate ids, when any slot was lost>"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/report_pieces.py" bundle "$WORKDIR" --set-id "$(cat "$WORKDIR/.scenario_set_id")"
```

The last line pulls the landed set onto the page (a cache hit when a wake already pulled it); its exit
code never stops the step — Step 5 pulls again.

**Progress fill** `suite-scenarios` ("Progress fills", above) once the batch has landed.

**Say how many slots actually landed.** `--landed` / `--requested` come from the JOB ENVELOPE you
are already polling — `detail.dataset_metadata.successful_scenarios` / `.total_scenarios`, with
the flatter `detail.scenarios_completed` / `.scenarios_total` carrying the same pair beside them
(there is no `dataset_metadata.yaml` in the workspace to open); `--lost-kind` is the
`evidence.gate_ids` of each lost slot's `scenario.slot_unshipped` finding in
`detail.gate_authority.findings` (e.g. `scenario.schema`) — on a backend that predates gate
findings, the `detail.dataset_metadata.scenario_degradations[].kind` of the lost slot. Pass them
whenever the batch is short, and the status line reads `scenarios landed 2 of 3; 1 lost
(scenario.schema)` instead of a bare `scenarios landed`.

This is not bookkeeping. A short batch is a valid outcome and the loop carries on — but WHICH slot
was lost decides what the rest of the build can still measure: a lost slot can be the only one
planting a probe (memory, absence) that Step 2 then authors a judge against, and that eval set
refuses. The shortfall is in the job envelope either way; unreported, the first anyone hears
of it is a refusal two jobs downstream. A short batch still does not buy another batch here — Step
3 re-measures it — but it must be SAID.

The pass-through
to `generate_scenarios_start` must carry `memory_challenge` **explicitly** —
`'elliptical'` for dialog-shaped agents, `'off'` for pipeline — because an omitted
value is defaulted BY SHAPE by the backend and is therefore
inherited rather than stated: the suite's memory posture is decided by a default nobody
read, and a deliberate `'off'` on a dialog agent is indistinguishable from a forgotten
argument.

**Compose EVERY generation call from the workdir, never from what you remember sending.** This
one and every regeneration after it — Step 3's re-draws and Step 3b's final batch alike.
`previous_scenario_set_id` keeps FLOW IDS stable and inherits **no authoring inputs**, so an input
document you do not re-send is one that batch is generated WITHOUT: the state schema, the PRD, the
variables, the data profile, the scan contract, the knowledge bases, the document fields.

The state schema is the one that bites. Four of those ride stable upload ids you can pass again
(`.prd_upload_id`, `.variables_upload_id`, `.data_profile_upload_id`, `.scan_contract_upload_id`);
the schema has no upload lane at all, so it must be re-read INLINE from
`$WORKDIR/scenario_state_schema.yaml` on every call — which is exactly why it is the one that gets
re-typed from memory and dropped. (`inputs_schema_yaml` / `outputs_schema_yaml` are inline-only
too, but they are ON DISK — re-read `$WORKDIR/inputs_schema.yaml` / `$WORKDIR/outputs_schema.yaml`,
which `/extract-scenario-variables` wrote. Re-deriving them from a fresh Pydantic-IO parse can
produce a schema that differs from the one the earlier call actually sent.) A batch generated
without the schema seeds an EMPTY world for every scenario and the run only shows it days later.

The backend refuses a regeneration that drops a document an earlier set of its `previous_scenario_set_id`
chain was built from (`scenario.input_doc_dropped`), naming each one and where to get it again; the plugin's own
check (`regen_gate`) is the fallback against a backend without it. Treat both as the backstop, not the rule.

**Every scenario call names the world: `env_id` is the content of `$WORKDIR/.env_id`.** Step 0c stood the env
up and left its id in that marker, so the qualification batch, each re-draw and Step 3b's final batch send that
one id and the backend drops the faults the env does not inject (`/generate-scenarios` carries the rule and the
refusal). A `mockless` build skips Step 0c, has no `.env_id` and sends none. `env_id` is a control argument: the
regen gate does not demand it, so only the composed call carries it.

**Keep the batch small + diverse — `count=3`. This is the QUALIFICATION sample, not the
deliverable.** The quality gate (Step 3) iterates on this sample to validate the simulation
*machinery*, which generalizes — so it's fast on a few scenarios, NOT a full-coverage batch.
The final suite the user receives is generated AFTER the gate converges, at
`count = 7` (Step 3b). Steer for **diversity + interesting cases**, not breadth across every
flow — and use the channel that can actually deliver each kind (generate-scenarios
Step 3): challenge-shape diversity (adversarial paths, refusals, premise-confirms, reversals)
lives in the TYPED `typical_complications` pool, which the backend spreads across the batch;
`interest` is flow-weight steering only — a non-flow ask parses to zero deltas. **Ignore the scenario job's `coverage.recommendation` during this
qualification pass** — that raise-count advice is sized for coverage sweeps, and the final
count is a flat 7 either way (Step 3b).

**Steer the draw to PLANT what guarded judges need.** A precondition-guarded judge is verified
only where the loop draws a scenario planting it. Pass two or three as typed `steering_demands`
(`{'kind': 'complication_dealt', 'subject': '<id from THIS run's typical_complications pool>',
'slots': 1}`), from the PRD facts and the scan or the Step-2 intent (never one you drafted during the env wait). An unseatable one warns, never
refuses. An unplanted one is computed, not judged: `python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/scripts/render_suite.py"
--artifacts-dir "$ART" --not-qualified` prints each eval no qualification run decided, with its axis,
and the render records it on the suite (`qualification: not_qualified`). On a draw that happens
anyway (a Step 3 re-draw, Step 3b's final draw; never an extra pass or re-draw for it), seat one
`complication_dealt` demand from the guard of each `not_applicable` one. A `skipped` one (its guard
never armed: no scenario created its precondition) is a draw gap too — seat its demand the same way;
an `errored` or `inconclusive` one is not: log it as a loop finding.

**The PRD's NAMED LIVE DEFECT takes the first slot.** When the PRD calls a behaviour the known
live failure — the thing the customer already knows is broken and wants measured — it is the one
demand this pass exists to seat, and it goes in first. The rest of the list is diversity; that one
is the ASK. Without a priority the slots go to whatever the session finds interesting, and the
named defect competes with picks that are merely plausible: a build that spent all three of its
slots that way had **2 of 3 undelivered** and no slot at all on the defect the PRD named, so the
only judge that could have measured it was guarded on a condition no scenario planted, skipped on
every scenario, and reported as coverage.

Say in `$ART/loop_log.md` which demand you seated for it and why, so the next pass can tell a
defect that was measured and passed from one that was never reached. If the PRD names no live
defect, say THAT — a pass with no named defect is a different state from one that had a named
defect and did not seat it, and the two must not read alike.

**Pass `redraws=0` on EVERY qualification batch — the first one and every loop regeneration.** A
scenario whose residual findings are advisory ships with them attached; a slot whose draft is HELD
on an `invariant` finding is normally REDRAWN: a *different* scenario is authored into the same
graph slot. That is right for a deliverable and wrong for every measurement before it — the
substitute ships, the set looks full, and the defect the pass exists to expose never reaches you.
With `redraws=0` a held slot stays unshipped with its findings and its held draft's `draft_ref`,
and the job's gate findings — on the shipped scenarios and the unshipped slots alike — are the
pass's output: read them and decide alone per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`
(repair through an edit, regenerate the slot, or accept), never asking the user and never
rendering a finding or an overrule to the customer. The batch summary says a shortfall is
configured rather than a spent budget, so do not report it as a generation failure.

It is also the qualification loop's single largest avoidable cost. A redraw is a full authoring
session, and because a slot's verdict only exists once its session has finished, the replacement
starts near the END of the batch — measured on live builds, one redraw took a 15-minute round to
28-32 minutes, and in one of the two the replacement failed as well, so the round bought nothing
but the delay.

**The default applies to any batch run to DELIVER** — three of them, and the third is a page
below, so read all three before concluding this is exhaustive: a regeneration of a batch that
shipped ZERO scenarios wherever it occurs (a redraw is the only thing that can return ANY
scenario, and the loop needs something to judge), the static-only suite generated straight at
`count=7`, and **Step 3b, which must never pass `redraws=0`.**

A TRANSPORT-level interrupted session — a dropped stream, a transient 429 — is unaffected by any
of this and still buys its one replacement, because `redraws` governs content verdicts only. So
the slots reported under `redraws=0` are short because of their scenarios. This is NOT a blanket
guarantee about the batch: a spent provider quota or an exhausted turn budget is refused on its
own grounds and emits its own warning, so read the whole warning list before concluding the box
was healthy. A 429 whose text names `resets <time>` (a session / usage / weekly limit) is NOT the
transient kind: every session until that time is refused identically, the backend declines the
redraw and latches the next `generate_scenarios_start` with the same reset text — so a failed job
whose envelope carries that reset time is waited out to the named time, never re-dispatched into it.

**Step 1 is ONE `generate_scenarios_start` call, and a SHORT batch is a VALID outcome** — carry
it forward to Steps 2/3, where the critic reads it and the repair happens. Regenerate here at
most ONCE, and only when the batch shipped ZERO scenarios. A 1-of-3 or 2-of-3 batch is the
measurement working: re-running it here spends another 10-30 minutes, and the cap and the repair
both live in Step 3. **Fixing what the findings name is right, but the fix does not buy another
batch** — Step 3 re-measures it; re-entering here makes the flag its own trigger and Step 1 never
exits.

**Exception — static-only suite** (the user chose it at the Step-0 run-prerequisite ask, so
the quality loop won't run): generate directly at `count=7` here and skip Step 3b — there
is no qualification loop to keep small for. That batch is the DELIVERABLE, so it keeps the
default redraws; do not pass `redraws=0` on it.

**That batch also carries the bundle a final batch would, because it is the only batch a
static-only suite delivers.** Before the call, run Step 3b's merged-bundle block *(deliverable
file)* — open the deliverable file (its full path is in "Step 3b onward" below) at "Carry the
REGRESSIONS and the TRACE SCENARIOS" and follow it there: stage `regressions/` and
`trace_scenarios/` with `final_bundle.py stage`, upload the staged directory, and pass the id as `additional_bundle_id`.
Everything that block says applies here unchanged — the exit-2 stop, the `held_back` trace
scenarios and their reasons, the cap, the loop-log
entries, and the membership check when the job lands; Step 4 then writes the same `suite.json`
keys. When the stage staged no folder, pass no `additional_bundle_id`. The rest of Step 3b stays
skipped. One difference follows from the order: Step 2 below authors the eval set against this
Step 1 set, which carries the folders, so their scenarios are in the population the evals are
authored for, and it owns one check per regression folder (`regression_bundle_id`), none per trace scenario. This is the ONE Step 1 batch that carries folders: the Step 1 batch of a suite that runs
the quality loop is its qualification batch, and a qualification batch never carries them.

**A static-only suite records no regression.** The folders it carries are the ones already on
disk; the gate-exit record step has no qualification run to cut a conversation from. Its report
still says so, in one line — write it now:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
rm -f "$ART/auto_regressions.json" "$ART/auto_regressions.txt"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-plan "$ART/agent_findings.md" \
  --regressions-dir "$WORKDIR/regressions" --static-only > "$ART/auto_regressions.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-report "$ART/auto_regressions.json" --findings "$ART/agent_findings.md" > "$ART/auto_regressions.txt"
```

## Step 2 — generate evals

Run the **generate-evals skill**, forwarding
`[context]` — its eval-relevant content is folded into the synthesized intent
(the only route the doc reaches eval authoring; do NOT append it to the PRD,
which would fork the PRD between scenario- and eval-generation). The skill
reads `.scenario_set_id` from Step 1 itself — scenarios-then-evals is what
routes corpus ground truth into eval authoring — and detects `shape` itself.
It also passes `$WORKDIR/regressions` as `regression_bundle_id` when folders exist, so the set owns
every recorded regression. Its `generate_evals_start` goes through Step 0's job lines, `--phase evals`.
**The moment `generate_evals_start` returns its job id, before that job's first poll and in the same turn, launch
Step 2a's sanity pass with its launch block below (it returns once the run plays); then poll the eval job.** The record and publish
lines here run when the job lands.
It writes `$WORKDIR/policies_config.yaml` and `$WORKDIR/.eval_set_id` — **record it**:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
  --phase evals --status landed --set eval_set="$(cat "$WORKDIR/.eval_set_id")" \
  --eval-revision "<get_artifact_meta(<eval_set_id>).detail.eval_revision, when reported>"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/report_pieces.py" draft "$WORKDIR"
```

The last line rebuilds the draft suite with the new checks; its exit code never stops the step.

**Progress fill** `suite-evals` ("Progress fills", above) once the eval set has landed.

**THE INTENT HAS A SCHEMA, AND YOU ARE ONE OF ITS AUTHORS.** `/extract-eval-intent` never runs
inside this flow — its several sequential questions are illegal in a flow window — so the intent
that reaches `generate_evals_start` is one you synthesized. That is a legitimate author and it is
bound by the same rules: **read
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/eval_intent_schema.md` and author
against `plurai-eval-intent/2` (`eval_intent_schema.json` beside it), never from memory of what an
intent looks like.**

The document is a list of **axes**; each axis carries the failure modes it fails in; **each
failure mode names its source** (`traced_to`: a PRD section heading with the clause quoted
verbatim, a scan finding id, or a trace-brief section). This flow asks nothing, so it never writes
`source: customer`. A failure mode no input states is not authored here (`/extract-eval-intent` is
where the customer states one). One id space across the whole file.

Two intents authored from the same PRD one night apart carried 4 goals / 7 bare-label
`must_catch` / 10 `custom_metrics` and 7 / 8 / 0, five names in both lists of the first, and only
six policy names shared between the two eval sets that came out — on inputs that were not larger.
That swing is what an untyped one-shot costs; the schema is what removes it. **Write the axes into
`$WORKDIR/eval_intent.yaml` before calling `generate_evals_start`**, so the document the job
validates at intake is the same one you can re-read at Step 3b instead of re-deriving. A draft
from the env wait (Step 0c) is that document: re-read it against the inputs as they now stand and
update what an input edit moved before the call.

## Step 2a — sanity pass (in the eval job's shadow)

**In the same turn `generate_evals_start` is dispatched, before its first poll**, run the qualification set once,
unscored and shallow, against the build's world (the deployed mock, when it has one): Init, the seed read-back and at
most three turns. It is the first execution of the real agent, the harness, its world and the scenario seeds together,
a `mockless` build included. Skip it only on a static-only build. A run on this Step 1 set only: a re-draw never buys
another. `--concurrency 3` is fixed, whatever `MAX_CONCURRENT` says: three agent processes at once, the width every
final run starts (its three trials side by side), so a resource one agent process holds (a fixed port, a lock) fails
here first.

Write each qualification scenario's `get_scenario` JSON to `$ART/sanity/worlds/<scenario_name>.json` in one Bash call,
never through the turn: the names are the Step 1 set's (`scenario_names` in `$ART/.pieces/meta_<set id>.json`), and per name
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" get_scenario "{\"scenario_set_id\": \"$(cat "$WORKDIR/.scenario_set_id")\", \"scenario_name\": \"<name>\"}" > "$ART/sanity/worlds/<name>.json.tmp" && mv "$ART/sanity/worlds/<name>.json.tmp" "$ART/sanity/worlds/<name>.json" || rm -f "$ART/sanity/worlds/<name>.json.tmp"`
(after `mkdir -p "$ART/sanity/worlds"`). Then launch it with the block below, a foreground call that returns once the
run plays (`run_start.py`, as `/simulation-run` launches a run), unscored (no eval set) and one sample. The
per-turn budget is the agent's own (`$WORKDIR/.turn_timeout`, else the qualification default): the launch passes no turn
flag. Its wall clock is bounded by the stop below, 2 minutes after the eval job lands.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
AGENT_MODULE_PATH="$(python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$WORKDIR/simulate.py")"
MOCK_FLAG=(); grep -qwE 'mockless|local' "$WORKDIR/.run_mode" 2>/dev/null || { MOCK_URL="$(cat "$WORKDIR/.mock_service_url")"; [ -n "$MOCK_URL" ] || exit 1; MOCK_FLAG=(--mock-service-url "$MOCK_URL"); }
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }
[ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER=plurai-run-simulation
source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-gate-flags.sh"   # $OVERRULE_FLAGS — the plugin's ledger, when it exists
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_start.py" -- \
"$RUNNER" \
  --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" \
  --agent-module-path "$AGENT_MODULE_PATH" \
  --out "$ART/sanity/1" \
  "${MOCK_FLAG[@]}" \
  --run-phase=qualification --no-stability-retry --max-turns 3 \
  --scenario-timeout 300 \
  --no-run-pointer --no-observability --replace \
  --concurrency 3 "${OVERRULE_FLAGS[@]}"
```

`$ART/sanity/` sits outside `runs/`: it is never uploaded, scored, rendered, pinned or listed. Its wait is
`run_wait.sh "$ART/sanity/1"`, as a wait in the background, re-run on exit 2. Exit 7 is the backend refusing this plugin or
its SDK: the wait printed the update text, so say it verbatim and alone, the way the error posture says a version refusal,
and stop; read nothing below. When its `.runner_exit` lands, read it — seconds, no model:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/build_run_json.py" "$ART/sanity/1" \
  --stage qualification --workdir "$WORKDIR" --local-only
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/sanity_signals.py" "$ART/sanity/1" --workdir "$WORKDIR"
```

The typed lines land in `$ART/sanity/1/signals.txt`. Log ONE note that carries counts only, never a fault:
`append_loop_log.py --kind note --lane harness --finding "sanity pass ran: <N> scenarios, <wall> min, <K> typed lines in sanity/1/signals.txt"`.
The repairs below are logged as their own `harness-fix` / env entries. In the same turn spawn ONE short reader with
the prompt in `references/sanity_pass.md`, as a background wait like the critic.

**ONE repair cycle, inside the eval job's shadow — never a loop, and the pass is never re-run:**
- harness → the harness-fix procedure (deliverable file, "A HARNESS fix"), steps 1–4; its re-run is the scored
  qualification run that follows;
- mock → the Step 3 lane's one `/edit-env` round, spent here, with the `env` findings verbatim and logged as its
  `env-fix` entry, the one Step 3 counts; a patch to
  `scenario_state_schema.yaml` → the existing regeneration rule (+ `relink_eval_scenario_set` once the eval set has landed);
- a scenario start state or input → the routes `/analyze-simulation` gives an input or harness finding.

Every line warns, none blocks; a `tool_error` alone never starts a repair (the agent's own bad input fails a call
too). A pass that could not run (the runner refused, the mock was down) is one loop-log note, then Step 3. When the
eval job lands first: an open repair finishes before Step 2b. A pass whose `.runner_exit` has not landed 2 minutes
after the eval job did is stopped by its pid alone (`kill "$(cat "$ART/sanity/1/.runner_pid")"`), logged as the note
`sanity pass: no run`, and Step 2b goes on. A reader not back when the eval job lands is not waited for: log
`sanity reader: abandoned`, go on, and drop its late report (the scored run's critic covers its questions).

## Step 2b — deterministic pre-run gate (no run, no critic, no LLM review)

Most of what a review pass finds in a fresh eval set is decidable from the files on disk.
Clear that class here, where a fix costs one batched edit — after the run it costs a review
pass and a re-score as well. Three checks, all mechanical:

1. **The BACKEND's own validation — do NOT run a local verifier.** Every lint that JUDGES a
   policy body — the class the client-side `verify_policies.py` owned — now runs inside the
   backend, on both of the two surfaces that can
   act on it: the generation job validates what it authored, and `/edit-evals`' in-turn gate
   validates every draft before the turn returns — parse, judge polarity, `code_eval` and guard
   EXECUTION, heading placement (a requirement under a "what is NOT a violation" heading, which
   a judge reads as an excuse and returns a confident PASS on), rubric paragraphs two judges
   share verbatim, expressions whose bindings are empty at their own `evaluation_scope`, and
   typed field placement (`grades_source_fidelity` on a `code_eval` is a parse error there, not
   a refusal a run later). A turn that would fail any of them comes back `awaiting_local` with
   `REPAIR REQUIRED` naming the policy and the defect; send that straight back into the same
   session.

   **Scope — a JUDGEMENT is retired here; a LOAD CHECK is not, and belongs to another step.**
   The local script is a LOAD CHECK wherever it runs — whether the LOCAL toolchain, at ITS schema
   version, loads the bytes that just landed in the customer's repo — never a second opinion on
   the policies. It runs where `/generate-evals` and `/optimize-eval` say so. **Inside THIS step
   you add nothing**: the file on disk is the one
   `/generate-evals` already fetched and checked, no client has rewritten it since, and every
   judgement about it is already computed.

   **This is a deletion, not a relocation of the same cost.** Four readers of one YAML — the
   client's lints, the coverage audit's ledger, the score-time runtime, and the backend's commit
   gate — cost 20-35 `verify_policies.py` invocations per suite window and seven of one night's
   eighteen edit turns: a heading nit twice, a quoted literal that broke the file, and
   `grades_source_fidelity` refused at score time a whole run after the session that could have
   fixed it in one line had closed. The reader that WROTE the draft is the only one that can
   refuse it before it ships, so it is the only one left.
2. **The generation-audit findings** the backend already returned — the repair-trigger
   families listed in `/generate-evals` Step 5.
3. **The coverage diff against the qualification set.** The same check Step 3b runs against
   the final set, run here against the set you are about to score: read each scenario's
   `current_elements` / `scenario_target` (`get_artifact_meta`, `get_scenario`) and compare
   them with what the policies actually grade. A dimension the scenarios exercise that no
   policy measures is a hole; a policy whose premise no scenario carries will grade nothing.

A `beat_resolution` row reading `not_applicable` on every scenario is a measurement the
backend reports about that run, not a policy that grades nothing: beats are minted and
delivered by the backend's user simulator, so nothing in the installed SDK is evidence
about them, and only a scored run on the backend you are on — with the beat shown as
delivered in its ledger — can say whether the type executes.

**That is the special case of one rule: the evidence for "does this policy bind, and can this
type execute?" is measured by the backend** — `relink_eval_scenario_set`, `eval_coverage_audit`,
and `score_run` are the three answers, and a scored run is the only one of them that can say a
type EXECUTED. A local `grep`, a reading of the installed SDK, a hand-evaluated expression, an
inference from a policy's name or from a `not_applicable` cell: none of these is evidence about
a server-side mechanism, and each one is a premise you minted rather than read. Evidence from
the CUSTOMER's repo, agent, PRD or run is different: it is what an overrule of a finding cites
(`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`).

Fix everything the three surface in **ONE** `/edit-evals` session. No `score_run` is
needed — nothing has run yet — and **nothing local re-checks the result**: what confirms the
round landed is `end_edit_session` returning without a REFUSAL, because the commit gate re-runs
the same in-turn checks on the reconciled bytes before it promotes them. Read `turn_count` and
`final_policy_names` off that result; a verdict you recompute afterwards is the fourth reader
this step just deleted.

## Step 3 — quality-iteration loop (OWN THIS: the suite isn't done until it's top quality)

A generated suite is a **DRAFT**. A suite that merely runs green is NOT validated — green
proves the scenarios execute, not that they're diverse, grounded, and measured by sound
evals (trivial scenarios, hallucinated state the agent's tools can't see, or a
harness-coherence bug only *some* scenarios trip can leave evals **green-but-hollow**).
**This command owns the quality gate — drive the suite to TOP QUALITY autonomously; never
hand a hollow / low-signal suite forward, and never dress up a broken suite as validated.**
Keep the sample **small (≈3) and fast** — this validates the *machinery*, it is NOT a
coverage sweep. Fix everything yourself.

**Decisions in this flow are pre-authorized.** The user's invocation IS the authorization
for every decision below — asking any of them is a flow defect (and the ask_guard hook
denies the tool while the flow marker exists). The rule at every branch:

| Branch | The rule (apply it, never ask) |
|---|---|
| Lane structure | read `$WORKDIR/.loop_mode` once per interval — absent or `interleaved` → the two-lane structure below; `serial` → the single-timeline ordering (opt OUT with `echo serial > "$WORKDIR/.loop_mode"`). It is a marker, never a judgement: don't infer it from how the round is going, and don't rewrite it mid-interval |
| Continue vs stop the loop | ONE full critic, always (the red-cell audit runs beside it). Its BLOCKING findings are repaired in ONE round; the round is verified mechanically (`score_run` full-set diff, `suite_diagnostics`) and the delta RED-CELL AUDIT reads what the edit changed; then LAND. A second full critic is bought ONLY when critic 1 reports `RUN INVALID` — the harness or the mock world broke the transcripts, so nothing it read was a measurement of the agent. Never for a regeneration, an input edit, an eval repair, or caution. Hard cap 2 full |
| After the red-cell audit | LANDING (see below): repair the false reds it named, land what is proven, record what is not, proceed to Step 3b (deliverable file) |
| Eval-only fix round | rescore via score_run, skip the re-run; it buys no critic — the red-cell audit reads its result |
| Regenerate vs skip | a scenario-surface defect → regenerate within the current pass; never "skip ahead" |
| Order around a REGENERATION | the redraw is bought only AFTER the defect has been classified (deal / authoring / GT / schedule) and the classifier has answered. No redraw runs while that answer is outstanding, and none runs before the round's relink + ledger state is on the deferral queue — a measured regeneration unscoped two judges and made the ledger stale for the rest of the suite, which cost two more ledger turns and three re-reports. Classify, queue, then draw |
| Backend-flagged, client-unfixable | a per-scenario backend finding that RECURS after ONE regeneration (same text, different scenario) is NOT a scenario-surface defect: stop regenerating, record it under "Flag — needs a backend fix" with the verbatim finding line + scenario ids, and proceed with the set you have |
| A scenario landed `incomplete` | it ran out of the per-scenario TIME budget and was graded to the turn it reached — **it is not a failure and it never blocks convergence**. Triage it per analyze-simulation's three causes and LOG the verdict before the next round: `append_loop_log.py --kind incomplete-triage --cause {agent\|simulation\|latency}` (the script refuses a triage with no cause). `agent` → an `agent_findings.md` entry, like any agent finding; `simulation` → a suite-internal fix this round; `latency` → nothing but the record — never a defect, never an ask. The ONE case that acts: a flow the first `latency` projection puts over the 40-minute final budget (`exceeds-final-budget`) → remove it, per "Removing a flow" in Step 3b — an ACTION due HERE, in Step 3, under a rule that lives in a file this step has not loaded: open `references/deliverable.md` AT that section and follow it there. Do not act from memory, and do not defer the removal to Step 3b — and tell the user with the licensed run-integrity line |
| Qualification count | 3 by default, and `coverage.recommendation` is still ignored — but the SLOT PLAN's typed refusal is not. `generate_scenarios_start` returns `refused: unsatisfiable at count 3; satisfiable at N` when the eval set's steering demands cannot all be seated in 3 slots. That is arithmetic, not advice: re-issue at `count=N` when N is within one or two of 3, otherwise DROP the demand it cannot seat (and say which, in the loop log, `--kind note`). Never re-issue the same count and hope — the draw is refused, so a retry buys a round and the same refusal |
| A relink / audit finding | goes on the DEFERRAL QUEUE (below) and is consumed by the next step that can act on it. Listed once, fixed once, never re-reported by a later critic |
| Final count | **7, always**, at Step 3b (deliverable file) once the gate converges; an explicit user budget wins. `coverage.recommendation` never raises it |
| Spend / cost | internal telemetry: never shown, never a decision input, never a reason to stop |
| Blocker (exception 1) | per analyze-simulation's definition only — sanction the ask (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`), escalate with the exact question, then continue or land per the answer |
| Missing run prerequisite (exception 2) | a key / live mock / credential the run needs and only the USER can supply — never a decision, never fixable from here: sanction `blocker`, ask in product language, offer supply-now vs static-only, then resume (error posture item 2) |

**THE DEFERRAL QUEUE — `$ART/deferrals.md`, and the reason a round stops paying for the same
finding twice.** A relink audit and a coverage audit each return findings that
are MECHANICAL — a guard to add, a dead flow scope, an unverified fixture — and nothing owned
them: one measured relink listed two unscoped judges, the orchestrator fixed one, and critic pass
2 re-found the other twenty minutes later; the same finding was re-reported by every
critic pass until it finally ran. Both are the same defect: a finding
with no queue is a finding whose only memory is the next reader.

So, whenever a relink, a coverage audit or a `suite_diagnostics` report returns a
mechanical finding, write EVERY one of them to `$ART/deferrals.md` immediately — one line each,
`- [ ] <source> · <finding> · <the step that will consume it>` — before fixing any. Then:

* **EVERY FRACTION NAMES WHAT IT COUNTS** — `guard_coverage_gate` counts scenarios a guard ADMITS
  the policy on, `suite_diagnostics` counts scenarios it reached a VERDICT on. Write `admits 1/3`,
  `reached 0/3`, never the bare ratio, or one policy carries two unrelated numbers.
* **The next step that can act on a queued finding consumes it**, ticks the box (`- [x]`) and
  names the round it landed in. A queue entry is closed by a FIX, never by a re-reading.
* **An open entry is not a new finding when a critic re-reports it.** Dispose of it as
  "already queued for `<step>`" and move on; the queue is the record that it was seen.
* **The queue must be EMPTY before Step 3b's landing.** (Step 3b is in the deliverable file.) An entry nobody could consume becomes an
  ACCEPTED gap with its follow-up recorded, never a silently dropped line.
* **An accepted HARNESS gap (agent code the harness does not run) is a coverage hole:**
  `bench_manifest.py coverage-hole --mechanism <what> --source <path:line> --reason <why>` — internal, never a loop-log
  note or a customer line. A blind guard (`guard_sees_mock`) or a mode split is fixed, never accepted.

1. **Qualification run.** **OPEN the phase before you launch it** — a `started` with no matching
   `landed` is the ONLY record that says a session died INSIDE a phase. **FOUR phases open, not
   eleven** (`qualification`, `critic`, `edit`, `final_scenarios`); the other seven record
   `landed` only, deliberately, apart from Step 0's job record — deliverable file, "Which phases open, and why only four":

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase qualification --status started --round <N>
   ```

   **BOTH OF THESE HAPPEN BEFORE THE RUN, never after it** — seconds each, and a run launched
   without either has to be bought twice:

   **(a) Check the eval link.** A run whose eval set points at a DIFFERENT scenario set
   scores nothing and has to be repeated: read `detail.scenario_set_id` from
   `job_status(id=<eval_set_id>)` and compare it with `$WORKDIR/.scenario_set_id`. On a
   mismatch call `relink_eval_scenario_set` before running, and act on everything it returns
   by Step 3b's relink rules (deliverable file) — `guard_coverage`, `dead_flow_scope` and a premise warning
   carrying a concrete guard are all mechanical there, and they are no less mechanical here.
   It is one read plus one disk-and-AST call — seconds against the minutes a mis-linked run
   wastes.
   **(b) Set `RUN_PHASE=qualification`** at the top of the launch block of the run you are about to
   launch; the script turns it into the flag (never write `--run-phase` by hand). It
   drops the per-turn budget to 21 minutes and stops a stalled turn being re-driven, so a
   hung agent costs this gate 21 minutes instead of an open-ended wait on a sample whose
   whole purpose is to fail fast. A developer who declared `$WORKDIR/.turn_timeout` still
   gets their own budget — the phase only sets a default. **Write that sidecar YOURSELF,
   before the first run, for a generation-heavy agent** — one whose tools run their own LLM
   pipeline, so a single turn legitimately outlives ten minutes. Declaring the budget the
   agent needs costs nothing; discovering it through killed turns costs the round, and the
   kills read as agent failures. **Run it autonomously: observability
   OFF (skip simulation-run's observability opt-in ask)** — its LLM-credit cost (real
   agent-under-test calls) is inherent to the quality gate the user already invoked. Do NOT
   report its LLM spend to the user (backend cost figures are internal telemetry); never
   gate the gate on cost.
   A scenario whose seed the Init does not read back is not driven: it is reported unmeasured (`undelivered_seed`)
   and the rest of the run goes on. Route it to the harness lane of `/analyze-simulation` (its one round).

   This scored run is the verification of any Step 2a repair; the sanity pass is never re-run.

   **THEN run the generated set** with the **simulation-run skill**
   against the transport `.qual_transport` records — this
   produces the per-scenario run artifacts the analysis reads.
   **Record the phase** once the artifacts are uploaded, so a session that dies in the critic
   wait resumes here:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase qualification --status landed --round <N>
   ```

   **Progress fill** `suite-qual` ("Progress fills", above) — round 1 only.

   **START THE SOLVABILITY CHECK, then go straight on to the critic — do not wait for it.** It
   writes each seed's `reference_path.json`. `author_missing_paths=True` is NOT optional; why, the
   result fields and the one class that cannot be authored are in the deliverable file, Step 3b
   "Solvability".

   ```
   solvability_check_start(run_upload_id=<the qual-1 upload>, eval_set_id=<.eval_set_id>,
                           scenario_set_id=<.scenario_set_id>, author_missing_paths=True)
   ```

   Nothing the critic reads depends on it, so it runs under the critic wait. **Poll it before you
   TRIAGE the critic's report** (item 2 below) — the first step that reads it — with the exit
   code by the Step-0c rules above (`0` landed, `13` failed, `14` re-invoke):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <the solvability job id>; echo "solvability exit=$?"
   ```

   **`14` is an expected read here, and it is NOT a failure** — this check can outrun the script's
   budget, so re-invoke on the same job id until `0`. The `; echo` is Step 0's shape and is
   what stops a healthy job rendering as a tool failure (`|| true` would erase the code) —
   deliverable file, "Why the solvability poll carries an `echo`".
2. **Analyze + iterate — `/analyze-simulation` owns this.** **Open the phase before spawning
   the critic, and close it when the report is in hand** — a session that dies in the critic
   wait with nothing recorded resumes at `qualification` and buys the whole run again:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase critic --status started --round <N>
   # …the critic pass runs…
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase critic --status landed --round <N>
   ```

   **The same pair wraps each repair round — here it is, because a phase described in prose is a
   phase nobody records.** The repairs below are the longest unwitnessed stretch of the loop, and
   an `edit` that never landed is what tells the next session the repairs were in flight:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase edit --status started --round <N>
   # …the repair rounds this pass calls for run…
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase edit --status landed --round <N> \
     --eval-revision "<the last end_edit_session(...).eval_revision this round, when reported>"
   ```

   `<N>` is the round the critic just ran, so the two records pair up. `resume` names
   `final_scenarios` as what follows a landed `edit`, and says out loud that a `RUN INVALID`
   critic re-enters `qualification` instead, so the round counter is what makes that line readable.

   Run the **analyze-simulation
   skill**. Do **not** analyze the suite inline here:
   you are steering toward a passing suite, so you're the wrong judge — that's how a hollow
   suite gets waved through. Instead analyze-simulation spawns **ONE fresh, independent,
   report-only critic sub-agent** (it has no stake in the outcome, and never sees
   "gate" / "converge" / "validated"). **Triage its report before repairing anything**:
   BLOCKING (the suite would mislead or not run) is repaired in this ONE round; ACCEPTABLE
   (it would merely be better) is recorded for the Step 3b audit and never bought a repair;
   `surface: backend` / `sdk` / `plugin` goes to "Flag — needs a backend fix"; a defect that
   genuinely needs real data is the one escalation. No second full critic re-checks the
   repairs — the mechanical verification and the red-cell audit in item 3 do — unless the
   critic reported **`RUN INVALID`**, the one case that buys a re-run AND a second full
   critic, because nothing the first one read measured the agent. Fixes may touch **any
   surface**:
   - **scenarios** → regenerate / `/refine-scenarios` to fix a **degenerate sample** (all
     walking one flow, trivial, or not gradable) — **re-steer for DIVERSITY + interesting
     cases** — a missing challenge SHAPE (all premise-corrections, no refusals) is fixed by
     adding/renaming archetypes in `typical_complications` (the typed, batch-spread channel);
     a missing FLOW is fixed via `interest` weight deltas — **log the new steering**
     (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind re-steer --finding "<the interest/adapt text you used>"`),
     because Step 3b generates the deliverable with it and cannot read your memory; and raise the
     difficulty/memory/knowledge dials. Keep it **small (≈3)** — this is a fast quality
     check, not a coverage sweep, so don't inflate `count` to chase every flow. Regenerate
     ONLY for defects in the scenarios themselves (never hand-patch a scenario file — that
     masks the generation defect). Exception: judge-only grading GT (a wrong
     `expected_answer`, stale evidence quotes) is corrected in place via the backend-gated
     `edit_scenario_gt` tool (the `edit-scenarios` skill) and verified in the same
     `score_run` inner loop below (knowledge/retrieval GT only — a deterministic-check or
     scenario_target edit settles on the next live run, not a re-score) — never by hand,
     never for behaviour-shaping fields. For an
     eval- or harness-rooted defect, keep the existing set and fix that surface instead;
   - **evals** → `/edit-evals` (scope, fields, thresholds) — but never loosen an eval to
     match an incoherent scenario; fix the scenario's grounding instead. A coverage finding
     (dimension 4b: `uncovered_target` / `uncovered_obligation`) routes here too, as an
     **ADD** rather than an edit — the same session authors a new policy. Two rules before
     adding: read every existing `AXIS:` first, and if the gap's axis already has an owner the
     fix is to RE-SCOPE that owner, never a second policy on the same axis (two judges on one
     axis score one agent slip as two failures); and a `duplicate_axis` finding is ALWAYS a
     re-scope, never an add;
   - **the mock env / harness** → `/edit-env` (or a harness regen / a `simulate.py` fix)
     when a coherence or harness defect is the root cause.

   **A batch that came back SHORT — Step 1's or a loop regeneration's — is repaired HERE.** Each
   unshipped slot's findings (the gates that held its draft, with the draft's `draft_ref`) usually
   name an INPUT defect, so fix that artifact (the input-artifacts surface in
   analyze-simulation step 3) and regenerate once — passing `redraws=0` like every other
   qualification batch, so the repair is measured rather than papered over by a substitute
   scenario, **and `previous_scenario_set_id=$(cat "$WORKDIR/.scenario_set_id")`** so the rebuilt
   dimension graph keeps its flow ids and the eval set stays scoped (this is the step where the
   graph actually rebuilds — an input edit is what invalidates it). A Step 3 regeneration measures even
   when it returns ZERO — the zero-batch carve-out belongs to Step 1, which has nothing else to
   judge; here the previous batch is still there. **After TWO consecutive short regenerations, stop regenerating**, proceed with the
   scenarios you have, and record the shortfall as the report's ONE run-integrity line
   (`Run integrity — the suite shipped N of the M scenarios requested.`); the findings themselves
   stay out of the customer's report.

   **A HELD slot earns a full RE-DRAW only after an input edit.** Read the unshipped slot's
   findings first, because they decide whether another draw can help:
   - **Held on an `invariant` finding** (its draft broke a rule the loader enforces): a
     regeneration with unchanged inputs is a redraw the backend already bought and lost on that
     slot. Read the held draft (`get_scenario` with `draft=True`) to see what broke; the input
     edit (a file artifact, or the logged re-steer text the request carries) is the
     regeneration's PRECONDITION, not a preference. When no input this loop can edit licenses the
     failure — the defect is in the draw/deal itself, or in the backend's own gates — take ONE
     unchanged-inputs top-up for the missing slots, then proceed with what you have: flag the
     finding as `generator@backend` and record the shortfall as the run-integrity line.
   - **Infra** (`[audit_unavailable]` throttle storms, a crashed job, a severed budget):
     nothing was lost on content — ONE unchanged-inputs top-up is the designed heal, and the
     content-addressed graph cache makes it cheap. One; then land short.

     **A top-up is the MISSING SLOTS, not the batch.** The backend has a real slot top-up path and
     it is what a top-up means in EITHER bullet: ask for exactly the shortfall and adopt what
     already shipped —

     ```
     generate_scenarios_start(
       count=<the ORIGINAL total>,                  # the TOTAL, never the shortfall — see below
       adopt_from_scenario_set_id=<the short set>,  # the landed scenarios come across as they are
       ...                                          # every OTHER field BYTE-IDENTICAL — world or not
     )
     ```

     **`count` is the TOTAL you want to end up with, not the shortfall**, and getting this wrong
     is refused every time rather than occasionally: the backend raises when
     `count <= len(adoptable)` — "count={count} must EXCEED the {n} adoptable scenario(s) — a
     top-up always generates at least one fresh scenario".
     Six landed out of seven means `count=7`, not `count=1`; `count=1` is below the six adoptable
     and is refused before a single scenario is drawn.

     **Do NOT take the refusal's own advice literally.** When the byte-identity diff refuses, the
     message ends "Generate without the parameter (all {count} scenarios fresh)". Dropping
     `adopt_from_scenario_set_id` does clear the refusal — and throws away every scenario that
     already landed and was already judged, which is the exact loss the top-up path exists to
     prevent. Fix the differing field the refusal names instead.

     **`steering_demands` is TYPED, not prose.** It is on the exemption list, so a re-steer is a
     legal top-up repair — but only in its own shape: it takes three typed kinds and *an unknown
     `kind` is REFUSED at the call* (see the call contract in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/generate-scenarios-call.md`). A sentence of guidance
     belongs in `interest`, which is exempt too. A prose `steering_demands` is refused on SHAPE,
     with the legality reasoning above entirely correct and entirely beside the point.

     **`env_id` is not on that list.** A top-up sends the same `env_id` as the short set it adopts from; a
     different env is a different world and the backend refuses the adoption naming `env_id`.

     **Record what the top-up CARRIED.** Its phase record takes the same `--landed` / `--requested`
     as any batch plus `--adopted <n>` — the count in the job's own `top-up: adopted N
     quality-verified scenario(s) from set '<id>'` warning — so the line reads `scenarios landed 3
     of 3; 2 adopted, 1 newly drawn` instead of a clean 3-of-3 that reads like a fresh batch. It is
     the same fact the short batch above records: a resumed session, and Step 3's run-integrity
     line, must be able to tell a slot that was measured this round from one carried across.

     It is a qualification batch like any other here, so the redraw rule above governs it unchanged
     — nothing extra to pass. **The backend diffs the WHOLE re-drive contract and refuses on ANY
     field that differs and is not on its exemption list.** It never asks whether the field
     describes the world, so "I only changed a label / a ratio / one doc id" is refused exactly
     like a PRD rewrite — and with the same message ("its scenarios describe a different world"),
     which reads as a world defect and is not one. The exemption list is the backend's re-drive contract and it is these SEVENTEEN fields —
     plus `prd_markdown`, which is not exempt but is compared differently, described just below:

     `count` · `dataset_name` · `dataset_description` · `redraws` · `random_seed` ·
     `effective_seed` · `interest` · `adapt_request` · `steering_demands` · `overrules` ·
     `artifact_feedback` · `happy_path_min_ratio` · `mandated_flows` · `eval_set_id` ·
     `adopt_from_scenario_set_id` · `previous_scenario_set_id` · `trace_failures_markdown`

     `prd_markdown` is the one field compared on something other than its bytes: the diff runs
     over its FACTS PROJECTION (the document minus its `## Scenario design constraints` span), so
     a steering-only PRD edit passes and any new world fact does not. Nothing in this repo checks
     the list above against the backend's, so when a refusal disagrees with it the REFUSAL wins —
     it names every field it diffed on; read those names rather than re-reading this paragraph.

     A top-up is therefore legal after an infra flag AND after a content flag whose repair is a
     re-steer, an eval-set link, or a PRD constraints-span edit; it is illegal the moment the
     repair touches ANY field off that list — a WORLD input (PRD facts, variables, schema, corpus,
     bundle) most obviously, but a non-world field the very same way.
     Re-drawing the whole count instead pays again for the slots that already landed, and — worse
     than the money — re-rolls scenarios the loop has already judged, so the round's findings no
     longer describe the set in front of you. One top-up; if it also lands short, land short and
     record the run-integrity line.

   **Two cheap reads decide whether a scenario failure is re-draw EVIDENCE at all.**
   - **Attribution** — the round's `suite_diagnostics` report owns the layer. Citing a failure it
     attributes to the eval set or the harness as the defect a re-draw repairs buys a new scenario
     for someone else's bug. No report this round ⇒ the failure is UNATTRIBUTED, and the log entry
     says that rather than naming a cause.
   - **Stability** — the qualification phase re-runs a failed scenario into
     `<scenario>/stability_retry/` (the first attempt stays authoritative at the top level). A
     failure the retry did NOT reproduce is FLAKY: not re-draw evidence, not an agent finding, one
     run-integrity fact. **No `stability_retry/` directory means stability was NOT MEASURED** —
     never that the failure was stable.

   **Log the re-draw BEFORE launching it — `generate_scenarios_start` is DENIED until you
   have.** Every re-draw states its class and cites the flag it repairs:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
     --kind regeneration --flag-class <content|infra|coverage> \
     --defect "<backend flag|finding:id>" --finding "<one line>"
   ```

   An accepted entry mints the one-shot token `regen_gate` spends; a refused one (an uncited
   defect class) mints nothing and the tool call stays denied, with the refusal naming what it
   wants. A `coverage` re-draw at qualification is written with a finding on its entry — decide
   on it per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`. When the last draw hit no defect you
   can cite, do not re-draw: land short and flag the finding as `generator@backend`.

   **A defect in the AGENT is COLLECTED here, never fixed here.** The surfaces above — plus the
   INPUT artifacts, per analyze-simulation step 3 — are the only ones this loop repairs. When
   the critic's fix-locus is `the agent` — the real store CAN produce the answer and the agent
   still whiffed — analyze-simulation appends it to `$ART/agent_findings.md` and keeps going; a
   faithful sim plus a failing agent is the CORRECT result, not a gate failure, so it never
   blocks convergence (an agent finding alone is never a reason to spend another pass).
   Two consequences, both load-bearing: never re-seed the mock or loosen an eval to turn one
   green (that hides the exact bug the suite exists to find), and never let the loop "fix" the
   customer's source — that is `/simulation-fixes`, which Step 6 hands the file to.

   **Findings route to a CHANNEL — and the report is one somebody has to START.**
   `/analyze-simulation` starts `suite_diagnostics_start(run_stage='qualification')` at the top of
   every pass, off the round's own run upload; that job's finished `detail` IS the
   `suite_diagnostics.yaml` this section reads. **A round with no report is UNATTRIBUTED, not
   clean** — start it yourself before routing anything:

   ```
   suite_diagnostics_start(run_upload_id=<the round's upload, below>, eval_set_id=<.eval_set_id>,
                           scenario_set_id=<.scenario_set_id>, run_stage='qualification',
                           current_harness_fingerprint=<HARNESS_FP>)
   ```

   (`<HARNESS_FP>`: `/analyze-simulation` defines it; once the round's red-cell audit is uploaded, the report it made stale
   is re-derived with `release_verdict_recompute` before anything routes on it — `/analyze-simulation` step 6.)

   Deterministic, zero-LLM, seconds — one upload per round serves it and every `score_run` below.
   This pass's first `suite_diagnostics_start` goes through Step 0's job lines, `--phase critic`; a
   deliberate re-call (probes, the verifying re-run) starts fresh and is recorded.
   Attribution is OPERATIONAL here, not advisory: a failure the report attributes to
   `policy@eval_set` or `harness@client` is repaired on that surface and is neither an agent
   finding nor the defect a re-draw repairs.
   The analysis diagnoses and reports; this flow is the only thing that changes anything, through
   the gated channels below. Each typed finding carries an `owning_layer` (the backend deliberately
   emits no channel name — the mapping is ours) and a stable `id` that survives across runs, which
   is what makes the next report's `prior_findings` section a regression check rather than a
   restatement.

   | `owning_layer` | Channel |
   |---|---|
   | `element@variables` | edit the element's realization in `scenario_variables.yaml`, regenerate the affected slots |
   | `policy@eval_set` | `/edit-evals` — batched, one session per round (the inner loop below) |
   | `gt@scenario`, `facts@scenario` | `edit_scenario_gt` (gated, one call per field) |
   | `harness@client` | client-side template / `simulate.py` edit — the CUSTOMER's file, repaired without asking through the harness procedure below, and only after the file-owner test: a defect in the SDK, this plugin or the backend is `sdk` / `plugin` / `backend` and never reaches this row |
   | `generator@backend` | a structured defect report in the loop log under "Flag — needs a backend fix", which the user can report to support@plurai.ai or on Discord: https://discord.gg/bg34gh76uJ (quote the scenario set id). There is no MCP channel; recording the flag IS the channel. A red-cell `target_clause` FALSE RED is ALSO carried to the final draw as a `target_rulings` row (LANDING) |
   | `product_truth@prd` | a PRD edit — the ONLY layer that may touch the PRD |

   A finding whose `audience` is `needs_external_truth` STOPS and escalates — that value exists
   precisely because no channel here can invent the truth it needs.

   **Read the CHECK LEDGER beside the findings — `checks:` in the same file — and never a
   findings list alone.** Every check records exactly one of three values, and only one of them
   licenses the word "clean":

   * `ran` — the check looked and found nothing. The only value that means clean.
   * `flagged` — it emitted findings; those are the rows the table above routes.
   * `blocked` — it could not run, and its `detail` names the INPUT that was missing (no
     analysable scenario, no `success_criteria`, no `scenario_facts` ledger, no `code_eval` in
     the set). That is a COVERAGE HOLE, not a pass.

   A suite where every check silently no-opped renders byte-identically to a clean one, which is
   why the backend splits the three rather than reporting findings only. So: when nothing is
   `flagged`, say so **with the blocked list beside it** — name which checks were dark and why,
   fix the input when it is one of ours (a scenario that uploaded no transcript, an eval set that
   declares nothing to execute), and never report a round as converged on the strength of a
   findings list that is empty because nothing ran. `canary.passed: false` is the same signal one
   level up: the checks missed a defect the backend seeded on purpose, so that report's silence is
   evidence about the checks, not about the suite — treat the round as unverified and say so.

   **Repair discipline — four rules, and the first one is why this table exists.**
   1. **A CLASS defect is REPORTED, never patched into a shared input.** When the defect is in a
      generator, the only writable surface within reach is a shared input, so it is tempting to
      amend the PRD and move on. That does not work and it costs twice: a PRD amended with a
      verbatim prohibition of a bad scenario construction was violated verbatim by the next batch,
      because the planner still demanded the shape the prose forbade — and the amendment's one
      concrete alternative then steers EVERY future batch toward that single construction, the
      diversity-collapse vector this stack has already been bitten by. Flag it as
      `generator@backend` and carry on with the suite you have.
   2. **An INSTANCE repair is slot-scoped.** One scenario's defect gets a per-slot constraint on
      that slot's regeneration request — never a global edit for a single slot's problem.
   3. **Compile-then-delete.** When a constraint becomes a backend gate, the PRD prose that
      carried it is REMOVED. A gate plus lingering prose is double-steering, and accumulated
      stacked patches are what make a PRD contradict the element definitions the generator reads.
   4. **The PRD holds product truth.** Anything in it that is a patch rather than a fact
      about the product belongs in a gate or a ticket.

   **Ordering — `$WORKDIR/.loop_mode` decides which rule you are under.** Read it once per
   interval; absent reads as `interleaved`. **LOG the mode you resolved, once per interval** —
   `append_loop_log.py --kind note --finding "loop_mode: <mode> (<marker present|absent>)"` — and
   name it from the marker you just read, never from what the last interval did, so the loop log
   shows which rules each interval ran under.

   *`serial` — the opt-out branch, preserved verbatim.* Apply regeneration-channel findings LAST: regenerating a slot invalidates that
   slot's `gt@`/`facts@` findings, so applying those first spends the round on artifacts about to
   be replaced. Batch same-channel findings into one pass. When the quota is the binding
   constraint, order by repair COST class, not severity alone.

   *`interleaved` — the default.* The lanes run against each other, so "last" names no moment — the same
   invalidation is handled by PARTITIONING the findings before either lane starts:
   1. **Compute the scenario lane's regeneration slot set FIRST.** Which slots this interval will
      regenerate is the input to everything below, so it is decided before a single fix is applied.
   2. **PARK every `gt@scenario` / `facts@scenario` finding whose slot is in that set**, and log
      it — `--kind parked --lane scenario`, naming the finding id and the slot. Parked is a third
      state: neither fixed nor outstanding. Without the entry the next critic reads it as unfixed,
      and the interval re-buys a repair to an artifact that no longer exists.
   3. **Partition the remainder by the finding's typed `VerifiableVia`** — the backend says which
      LAYER owns a defect and leaves the channel mapping to us, so this is the same division of
      labour the `owning_layer` table above already runs on:

      | `VerifiableVia` | Lane |
      |---|---|
      | `score_run` | eval lane |
      | `rerun` | scenario / harness lane |
      | `next_generation` | regeneration — the scenario lane's batch |

      `generator@backend` still goes to the flag channel and `needs_external_truth` still
      escalates; neither is verifiable from here at all, which is why they are not lanes.
      **On a backend that emits no `VerifiableVia`**, derive it from `owning_layer` through the
      channel table: `policy@eval_set` and a knowledge/retrieval `gt@scenario` are `score_run`;
      `harness@client` and `element@variables` are `rerun`; a scenario-surface defect is
      `next_generation`. Batch same-lane findings into one pass; within a lane, severity order.

   **Contested.** Disagreeing with a finding is legitimate — record the contest against its id and
   move on. The next report echoes it with the contest attached; it re-fires once and then requires
   NEW evidence. Do not re-litigate a contested finding within a round.

   **Precedence while the legacy critic still runs**, on contradiction: deterministic finding >
   high-confidence LLM finding > low-confidence LLM finding > critic prose. A heuristic finding
   (`confidence: heuristic`) does not outrank the critic; confirm it in the transcript before acting on it.

   **Pinned probes — a confirmed agent defect must keep something that exercises it.**
   `prior_findings` re-checks what the suite APPLIED, and a confirmed AGENT defect is never
   "applied", so nothing verifies its probe survived the next regeneration. Recorded: a
   qual-1-confirmed tool-inversion was exercised by nothing in qual-2, so the following report could
   not say whether it was fixed or merely unlooked-for. So: every entry you add to
   `$ART/agent_findings.md` carries its finding id AND the probe that exercises it (the scenario
   premise element, or the eval policy). An eval policy exercises a pin through its key, description,
   rubric or expression — never through `display_name` or `prompt_summary`, the backend's presentation copy. Carry those into `eval_intent.yaml` before Step 3b
   regenerates — **as a typed axis** (`plurai-eval-intent/2`), whose failure mode traces to the
   finding id that produced it (`source: trace` or `source: scan`, `ref: <the finding id>`); that
   is the channel the eval recipe already compiles a coverage row for, and the trace is what lets
   a later reader tell a probe the suite chose from one a critic demanded. A pinned id the next build cannot exercise is itself a suite defect (`NOT-EXERCISED`), not
   a pass.
   **A confirmed defect is ALSO pinned as a scenario demand.** The final draw's
   `generate_scenarios_start` carries one `complication_dealt` slot per confirmed finding, ahead of
   the diversity demands, naming the `typical_complications` entry whose description IS the
   finding's `**trigger:**` (the user move the agent failed on) — a coarser entry the draw can meet
   without that move does not count. When no entry forces it, declare a NEW top-level
   `typical_complications` entry whose id names the move — never a variant of an existing entry: the demand
   and the census carry ids, and the draw may pick any variant — in
   `scenario_variables.yaml` before Step 3b, deliver and re-pin by Step 0b item 6, and log it `--kind input-edit`. Each
   `$ART/carried_criticals.json` entry is pinned the same way, off its `trigger`; when the final run reproduces it, record
   it as a confirmed finding on that run, so the post-final re-plan cuts its folder. A pinned demand the
   ledger shows `delivered < slots` is that finding's `NOT-EXERCISED`, stated in the run-integrity
   line (3b.9) — never an accepted gap. Stated only while the finding's `agent_findings.jsonl` row reads `scored_run.reproduced: 0` (`scored_findings.py record`; no `scored_run` reads the same); once the scored run reproduced it, the line says instead that it reproduced in k of N conversations (`reproduced` of `conversations`) while its planted situation was set up on d of s (`delivered` of `slots`).

   **Work the surfaces in THIS order: evals first, cheaply; scenario/harness second, expensively.**
   Most defects a fresh critic reports are eval-side, an eval fix is quick to make, and quick to VERIFY: the backend's `score_run` MCP tool re-scores a
   scenario's SAVED run artifacts against the current eval set, judge tokens only, no agent re-run,
   so an eval round-trip does not cost a full scored run. The inner loop:

   1. **Batch every eval-surface finding into ONE `/edit-evals` session** — not one session per
      finding. Its cross-policy reconciliation rule then runs across the whole batch, which is how
      you avoid shipping two judges that contradict each other after a fix. **One session, several
      MESSAGES inside it**: `/edit-evals` asks you to split fixes across short turns so a long one
      cannot hit the MCP idle abort, and that is a different lever from the session count — it does
      not contradict this rule. Sessions: one. Messages within it: as many as keeps each turn short. The batch carries the concurrent red-cell audit's `rubric` FALSE REDs beside the critic's eval-lane findings, its `harness` and `type` ones with them, and its `prompt` ones under the session's `prompt_only` — except a policy whose FALSE REDs are all `prompt`, which before the final run uploads labels and gets no edit (`/analyze-simulation` step 6). Apply scenario-GT
      findings in the same round via `edit_scenario_gt` (one call per field, cited source
      required — the `edit-scenarios` skill), so the single re-score in the next step
      verifies the whole batch.

      **How you WORD each fix decides what ships** — `/analyze-simulation`'s eval channel
      carries the two rules (a worked example ships as a rubric literal; a reworked judge needs
      exact text, not intent).

      **Every single-policy SEMANTIC edit is checked inside the session, before step 2's
      `score_run`** — `/edit-evals` step 3b carries the check and what it may claim.
   2. **Re-score every scenario in the qualification run** — upload the run ONCE with the
      SDK CLI. **Stamp the harness first**, in the SAME call, so the upload records
      which harness produced the artifacts it carries — a `simulate.py` edit leaves no mark on
      the run artifacts, the eval set, or any backend id, so a replay against a stale transcript
      keeps returning verdicts that read as current:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      HARNESS_FP="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_fingerprint.py" --write --quiet)"
      ```

      Then ingest, telling the CLI what this run is a recording of — it stamps the four values
      (plus its own `sdk_version`) into the manifest for you:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $PLURAI_DIR / $WORKDIR — re-source in THIS block
      OUT="$(cat "$WORKDIR/.last_run")"                                 # likewise; the resolver does not export it
      source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
      PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-run)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed
      python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-run --run-dir "$OUT" --workspace-root "$PLURAI_DIR" \
        --eval-set-id "$(cat "$WORKDIR/.eval_set_id")" \
        --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" \
        --harness-fingerprint "$HARNESS_FP"
      ```

      **`--agent-version` is deliberately NOT passed** — `ingest-run` reads it off the run's own
      transcripts, and warns if you override it with something they contradict.

      A `WARNING (disclosure: home_named)` line it prints comes before any byte leaves: relay it to
      the operator, naming the files it lists. It does not stop the upload.

      If `ingest-run` exits 7, the backend refused the version: its output is the update text. Say it as the
      error posture says a version refusal, and stop. If it exits with any other non-zero code, relay its one `error:` line to the operator in that step's own
      message, log it (`append_loop_log.py --kind note --finding "run not uploaded: <that line>"`), and
      continue without the steps that need the upload. Never send the run another way, and never skip the
      local red-cell audit, its corrections or the report: they need no upload.

      (the preflight resolves `$PLURAI_DIR` in the SAME call and catches a wrong-backend upload;
      the CLI selects only complete scenario folders, excludes `error.json` failures, and
      computes the checksum — never hand-roll the tar). It prints the lease manifest, and the
      values above ride in it under a nested **`provenance`** key.

      **LIFT `provenance` out and pass it as separate arguments — never as part of `manifest`.**
      The backend's manifest model ignores keys it does not declare, so a manifest handed over
      verbatim is accepted, the nested block is dropped, and the upload records nothing. There is
      no error to notice; the provenance is simply gone. So: read `provenance` from the printed
      manifest, remove that key, and call

      ```
      upload_run_artifacts_start(manifest=<the manifest WITHOUT provenance>,
                                 eval_set_id=…, scenario_set_id=…, harness_fingerprint=…,
                                 agent_version=…, sdk_version=…, sdk_identity=…)
      ```

      (**pre-authorized by the invocation — never
      ask consent for it**; the harness's tool-approval prompt is the chokepoint, and a run's
      artifacts upload as-is whatever the anonymization posture). Those six say WHAT this upload is a
      recording of, and they are the coordinates every later replay is checked against. Absent
      values are OMITTED, never nulled. Send no
      `gt_revision` and no `backend_build_id` — the backend resolves both itself, and a
      client-supplied value would be a claim rather than a record. **`scenario_set_id` must be the
      set the run was actually made against**: one that disagrees with the transcripts' own
      recorded set id REFUSES the import and rolls it back, keeping nothing — so read the marker,
      never assume the current set is the one that ran. **On a backend that rejects
      one of the six** (or a CLI that rejects the new flags), drop it and re-call — same fallback
      as `summary` below; the upload is correct, it just carries less provenance.

      **Then PIN the frame — every upload, immediately, before any replay reads it.** The
      returned `run_upload_id` plus the coordinates above are what these saved artifacts are a
      recording OF, and a replay is honest only inside that frame:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      # $OUT is not exported by the resolver and the shell resets between calls — read it from the
      # pointer the run wrote. `write` REFUSES a blank id rather than pinning one: an empty
      # coordinate compares unequal to everything and would report the frame moved on every later
      # check, stopping the eval lane for the rest of the build.
      OUT="$(cat "$WORKDIR/.last_run")"
      python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" write \
        --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" \
        --eval-set-id "$(cat "$WORKDIR/.eval_set_id")" \
        --run-upload-id "<the returned run_upload_id>" --out-dir "$OUT" \
        --backend-build "<backend_identity().build_id — the FIELD, not the object; '' if you have none>" \
        --backend-source "<backend_identity().source_id — the FIELD; '' if you have none>" \
        --backend-instance "<backend_identity().instance_id — the FIELD; '' if you have none>" \
        --eval-revision "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("eval_revision") or "")' "$OUT/run_summary.json")"
      ```

      (`--gt-revision <n>` too, once a `score_run` in this frame has reported one.) `--eval-revision`
      is the run's `eval_revision` from `run_summary.json` — the content digest of the eval set that
      GRADED it, which the runner copies off finalize (empty on an older backend: the pin then
      carries none and the axis is not checked). `.eval_set_id` survives every in-place edit, so
      without this axis a run scored under a rubric an edit session since replaced reads as
      current; `check --eval-revision <get_artifact_meta(eval_set_id).detail.eval_revision>` names
      it `eval-revision-moved`. `--set-corpus-ids` is the corpus coordinate: a
      rebuilt corpus leaves `.corpus_id` outside the ids the set's own detail records, and a set
      standing on the old corpus would keep driving the critic — `corpus-moved`. `--backend-build`
      is `backend_identity().build_id`, the backend IMAGE these artifacts were generated and judged
      by: a redeploy between qualification and the final generation moves no marker, no fingerprint
      and no revision, so without this axis the two halves of the arc read as one machinery when
      they are not. An empty value records `unknown` and reports `backend-unknown` at every later
      check — never drift. `--backend-source` is the axis `build_id` CANNOT answer: a per-file
      source patch moves `source_id` and leaves `build_id` naming the image it patched, which is
      exactly how three demo boxes reported the broken commit while running the fixed grader —
      `backend-source-moved`. `--backend-instance` is `instance_id`, RECORDED and never compared
      (on E2B it is the sandbox id and moves only when the box is replaced; off E2B it is a
      per-process uuid and moves on every restart); it is what settles "the same running box?"
      when two coordinates disagree. Pass all three: an omitted flag is not asked at all, so the axis
      answers nothing in either direction. The pin is
      advisory for now — nothing refuses on it — but it is the only record of which world a
      rescore describes, and the frame moves silently: a regenerated set, a `simulate.py`
      edit and a GT revision all leave the replay returning verdicts that read as current. Full
      contract: `${CLAUDE_PLUGIN_ROOT}/docs/lane-split.md`. Then **exactly ONE**
      `score_run(eval_set_id, scenario_set_id, scenario_name, run_upload_id=<id>, summary=<mode>,
      current_harness_fingerprint=<HARNESS_FP>)`
      per scenario per round, all issued in ONE batch by `rescore_run.py` (`/edit-evals` Step 5.6; `--summary`
      for `summary=true`, `--harness-fingerprint`, and `--pin "$WORKDIR"` when it applies step 3's results), each
      `rescore='changed'` and writing its own output file; a call cut off client-side is re-read with
      `--resume`, not re-scored — never serially, never three-then-wait — by reference, not 55-68 KB of artifact dicts per call (older
      backends: pass `transcript`/`final_world_state`/`tool_events` inline from
      `$OUT/<scenario>/`). **Pass the fingerprint on EVERY call**: the result's `harness_state`
      is `match` / `mismatch` / `unverifiable`, and it is `unverifiable` precisely when you did
      not say — an answer the round cannot act on. A `mismatch` only WARNS server-side; the local
      pin check below is where it stops a replay. **On a backend that rejects
      `current_harness_fingerprint`, or returns no `harness_state`, drop the argument and re-call**
      — same rule as the upload arguments above. The local `lane_pin.py check` is the enforcing
      half either way, so an older backend costs you the server-side echo and nothing else; do not
      block the round on it. Pick `<mode>` once, up front:
      - **`summary=false`** when this round applies its results (step 3 below — the normal case
        in this loop): the verdicts keep each cell's `reasoning`, which summary mode truncates.
      - **`summary=true`** only when nothing is applied and you just want the verdicts.

      **The client timeout cannot be raised from here.** A policy-dense scenario scores for
      minutes and the backend's own budget is 540 s, while the MCP client aborts a call that has
      been silent for 300 s — observed live: `summary=false` on a 13-policy scenario aborted
      client-side while the server scored it to completion, so the round paid for verdicts nobody
      saw. `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` / `MCP_TOOL_TIMEOUT` are read by the harness when
      it connects to the MCP server, so they only take effect from settings.json's `env` block
      (or the launching shell) BEFORE Claude Code starts — an `export` in a Bash call cannot
      reach the already-connected client. Mid-loop, score FEWER scenarios per call and keep the
      messages small (same lever `/edit-evals` documents for `interact_edit_session`); an aborted
      call still lands server-side, so re-read rather than re-score.

      **Never call both.** Calling `summary=true` for the read and `summary=false` again to
      apply is two full scorings per scenario per round with the first discarded — an operator
      pushed ~500 KB through subagents that way. And `summary=true` is not "truncated prose": it
      **removes the per-turn `turns` array** (→ `turns_summary`) and **drops `findings`** — see
      the shape table in `${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md` before parsing either result. (Backends
      predating `summary` reject the argument — drop it and re-call; the full result is correct,
      only bulkier.) Read
      `changed` across ALL scenarios: the cells you meant to flip flipped, and nothing else
      regressed (an eval fix can regress a sibling scenario — the full-set diff is what
      catches it). Honour `not_scored`: a clean `cells` with a non-empty `not_scored` is a
      PARTIAL verdict (turn_based policies now RE-score per transcript prefix on current
      backends, so `not_scored` normally names only guard-skips).
   3. **Apply each result to the conversation's verdicts — through the script, never by hand.** `rescore_run.py`
      (step 2) does it for every scenario, trial and stability retry: each call carries the item's current cells as
      `baseline_cells` (what `rescore='changed'` carries forward and `changed` diffs against), a stability retry is scored
      in the same round BY REFERENCE as its own upload item (`scenario_name: "<scenario>/stability_retry"`, the run's
      `run_upload_id`), so the run's red-cell audit rules it as it rules its twin — never under the SCENARIO's name with
      that upload, which re-scores the first attempt — and each `summary=false` result goes through
      `${CLAUDE_PLUGIN_ROOT}/scripts/apply_rescore.py` to the SDK's verdicts writer (give `rescore_run.py` `--pin "$WORKDIR"`
      and `--set-corpus-ids "<the set's detail.corpus_ids, comma-joined>"` — the same value as `lane_pin.py check` above).
      A refusal applies nothing and is a finding for the round's report (exit codes: the script's header). On a round
      that will regenerate the scenario set (Step 3b, deliverable file) pass `--summary`: nothing is applied, except
      on a round that extends a judge for a confirmed coverage gap (`/analyze-simulation` step 3's probe rule): apply
      that one, because the gate-exit regression plan cuts from the run's current verdicts. Never edit
      `transcript.json` or recompute a stability verdict by hand — rebuild `run.json` (Step 3a).
   3a. **A rescore INVALIDATES the rendered run report — never leave the stale render standing
      as the evidence.** `analytics.md`, `eval_signal.md`, `run.json` and `charts/*.svg` are a frozen render of the
      ORIGINAL scoring; nothing here refreshes them. An operator re-scored four rounds and the
      report still showed 7 violations against 12 actual, with one scenario at 0.92 where it was
      0.64. So after EVERY rescore round, either rebuild `$OUT/run.json` with
      `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-run/scripts/build_run_json.py" "$OUT" …`
      (after `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"` — it reads the config through PyYAML)
      and re-run `render_run_report.py` (preferred — full command in `/simulation-run` Step 4b;
      run it AFTER the results are applied, passing the post-edit `eval_set_id`/`gt_revision`),
      or prepend a one-line STALE banner to `analytics.md` naming the rescored scenarios and delete
      `$OUT/eval_signal.md`; then re-run `/simulation-run` Step 4c's attach block so the upload
      carries the current report. Current backends
      return `gt_revision` on the `score_run` result — compare it, and the `eval_set_id` you scored
      with, against the `eval_set_id` / `gt_revision` the render itself carries (`run.json`, and the
      header line of `analytics.md`); either differing means the render predates this rescore. (The
      result's `run_upload_id` identifies the uploaded run, not the eval set — it is not part of
      that comparison.) On an older backend that omits them the rule is the same, you just know it
      directly (you rescored, so the render is stale). Full rule:
      `${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md`.
   4. **Mark the pin stale before any full re-run.** A re-run writes each conversation's transcript and verdicts
      afresh; the pin is the one record that the artifacts it froze are superseded (an env fix moves no marker):

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" stale --reason run-superseded
      ```

      The next upload's `lane_pin.py write` re-opens applying; until then the frame is declared gone.
   5. **Stop rule:** an eval finding still failing the same way after two consecutive repair
      re-scores is not converging — carry it to the outer loop's landing triage; don't grind judge
      tokens on it. A commit's `guard` report (a dropped verdict, a named risk) never counts toward it.
   6. **Know what a re-score cannot prove.** It replays FIXED behaviour. An eval fix whose
      correctness depends on *different* agent behaviour (it now requires a tool call this run
      never made) is not validated by a re-score — say so, and let the next full run settle it.

   **Cheap in tokens is not cheap in wall clock: budget ~15–20 minutes per inner round** (one
   `/edit-evals` session, the run upload, one `score_run` per scenario), and the repair round
   gets TWO of them at most — the stop rule above. Nothing here buys a critic: what verifies
   the round is item 3.

3. **Verify the round mechanically, audit what is still red, then LAND — don't go back to the
   user.** No second full critic re-checks the repairs; these three do, and each reads the
   GATE'S output, never your own note about what you fixed:
   1. **`score_run` full-set diff.** Every intended cell flipped and nothing else moved. A repair
      whose cell is absent from `changed` is NOT a repair — it changed nothing, so its finding
      stays open. A cell that flipped which no repair named is a regression: revert that edit
      (one the commit's `guard` reported is decided below, never here). Every repair round's `/edit-evals` commit ships; the plugin reverts each policy whose draft
      silenced an audited TRUE RED and keeps every other verdict the `guard` reports moved — the
      intended narrowing when the audit ruled it FALSE RED / OUT OF SCOPE, else an internal named
      risk (`/edit-evals` Step 5.3). A guard report is never re-edited, and never reverts the round.
   2. **`suite_diagnostics`, re-run on the repaired set** — `suite_diagnostics_start` on the round's
      upload, deterministic, seconds; when the pass measured `request_probes`, the re-run passes the
      same `env_id` and `request_probes`. A `double_charge` the diagnostics now report is a defect the
      round introduced: fix it in the landing round.
   3. **The RED-CELL AUDIT** — `/analyze-simulation` spawns it CONCURRENTLY with the critic, over the
      qualification run's red cells, so its FALSE REDs feed the round's one edit session; here, after the
      repair, a smaller DELTA audit covers only the cells the round changed (flipped or newly red, any re-run's
      fresh cells, edited-policy cells that stay red) plus the critic's `policy: null` cells, plus the list's sampled greens (step 6's `--greens 24` line): a fresh, scoped critic over those cells, medium effort, one question per cell — does this
      reply actually violate this rule as written? It returns TRUE RED / FALSE RED / UNDECIDABLE
      per cell, keyed `(item, policy, turn)` — a `<folder>/stability_retry` is its own item, as is each
      `<scenario>/trial_<k>` (`red_cells.py list`) —
      with the quote that decides it. Each AGENT defect a critic named that no red cell carries
      is a cell too (`policy: null`, `defect_surface: null`: the reply the critic quoted and the contract clause it
      breaks, never the critic's reasoning).
      **Before the final run the delta audit reads only the cells no audit has read** — the critic's `policy: null`
      cells and any re-run's fresh cells — and does not run when there are none: the `score_run` diff (item 1) flags
      every unintended flip, and the final run's audits rule the rest. When `[ "${PLURAI_PREFINAL_DELTA_AUDIT:-}" = 1 ]`,
      run the full delta audit described above instead.
      **Nothing becomes an `agent_findings.md` entry
      until the audit called it TRUE RED** — a red the driver or the first critic filed as an
      agent defect is a claim until then; a `policy: null` cell's TRUE RED is filed confirmed,
      its UNDECIDABLE potential, its FALSE RED dropped. Each filed section gets its row in the same step:
      `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/record_finding.py" "$ART/agent_findings.md" --id <finding id> --title "<its section title>" --status <confirmed|potential> --found-during "quality loop" --severity <high|medium|low> --run <run folder> --evidence "<item>:<turn_idx>:<the audit's verbatim quote>"`
      (`--evidence` once per quote, required on a confirmed row; a refused quote means the evidence is wrong — re-read that transcript).
      `rubric`, `harness` and `type` FALSE REDs, the policies the audit's `eval_rounds.py schedule` names and each
      confirmed coverage gap's rubric extension (`/analyze-simulation` step 3's probe rule) are the landing repair's whole scope, `prompt` ones its `prompt_only` refinement (never a policy whose FALSE REDs are all `prompt`: `schedule --stage qualification` leaves it out),
      `target_clause` ones become rulings (LANDING);
      UNDECIDABLE follows the real-data escalation below. **A TRUE RED agent finding is a regression
      candidate, never recorded from inside a round**: "Gate exit — record regressions from the
      confirmed findings" below records the confirmed ones, at most 6, ranked, once per build, and
      each folder keeps the audited reply, so the next build's evals must fail it.

   Then land (the LANDING below). Fixing the *quality* is THIS command's job — never punt a
   fixable problem, and never "land" by handing a low-signal sample back for the user to
   decide. **The ONLY reason to involve the user is a defect you absolutely cannot fix without
   information only they have** — a grounding mismatch where seeded values the live tools
   can't return must be replaced with real data (real records via `/upload-scenario-bundle`).
   In that one case, surface the SPECIFIC ask and do **not** report the suite as validated.

   **A SCENARIO or HARNESS repair still needs a re-run before the audit** — a `simulate.py` fix
   or a mock patch changes how the agent behaves, and no cheap check observes that — but the
   re-run buys no critic: its artifacts go through the same three checks. **The one exception
   is `RUN INVALID`**: when critic 1 says the qualification run did not measure the agent at
   all (the harness or the mock world broke the transcripts — a `harness@client` fix, an
   `/edit-env` patch, a transport change, an empty or unmutated world), the re-run is
   mandatory and it gets a SECOND full critic, because every finding of the first describes a
   run that no longer exists. That report is triaged and repaired exactly like the first, once;
   then verify, audit, land. Hard cap: two full critics, and nothing else reaches the second.

   **ENV fixes iterate at PROBE speed, and the round shares ONE re-run.** The world is this
   command's own product now (Step 0c), so mock defects are first-class here — and paying a full
   re-run per mock fix would make the loop unaffordable. Work them like the eval loop:
   1. **Patch with `/edit-env`**, which redeploys the mock from the edited `simulation_env/`.
   2. **Verify by QUERYING THE DEPLOYED MOCK directly** — the same live-mock access the critic
      gets. That proves exactly what a probe can prove: the mock now tells the truth (right shape,
      value, status, seeded entity). Seconds; no scenario regeneration, no run.
   3. **Batch the round.** Probe every env fix in this pass, then let **ONE re-run settle the whole
      batch** — never one run per mock defect. **That re-run happens BEFORE the red-cell audit.**
      The probe verifies the patch; the re-run produces the behaviour the audit reads. Auditing
      artifacts recorded against the BROKEN world certifies verdicts on evidence the fix
      invalidated — the eval-only round is the only round that may skip the re-run, because only
      there is the behaviour evidence still true.
   **Know what a probe cannot prove.** It proves the CALL YOU PROBED now returns the expected
   shape/value/status. It does not prove the adapter's sibling calls are coherent (a fix to `get()`
   says nothing about `get_availability()` on the same store — that is the critic's job, on the
   batch's re-run), and it does not prove how the agent behaves
   against the corrected world. When the finding is about agent behaviour, the batch's single
   re-run is what settles it — never report a probe as having validated that. An env fix that
   survives TWO consecutive probe rounds is not converging:
   stop probing and carry it to the landing triage, exactly as the eval loop does. And an env fix
   does not trigger scenario regeneration — `.scenario_set_id` is unchanged by a mock repair — with
   ONE exception: if `/edit-env` reports `scenario_state_schema.yaml` among its `files_patched`, the
   world CONTRACT changed and the existing set is seeded against the old one, so the set must be
   regenerated (`/edit-env`'s schema note). Regenerating the env itself is the last resort — it costs Step 0c's
   minutes again and must `undeploy_env` first.

   **A HARNESS fix has its own procedure, and every step of it is load-bearing** — it is the one
   repair that writes to a file the CUSTOMER owns, and `score_run` cannot observe it. It is applied
   without asking, in every mode. It is a rare path, so it lives in the deliverable file under
   **"A HARNESS fix — the six-step procedure"**: open it AT that section and follow it there. Do not
   act from memory, and do not improvise the file-owner test, the disclosure or the re-run rule —
   all three are load-bearing and all three are easy to skip.

   ---

   **TWO LANES — the DEFAULT.** `$WORKDIR/.loop_mode` absent or `interleaved` runs the two-lane
   structure: an INTERVAL is the unit, and inside one the two surfaces iterate against each other
   instead of in sequence. Writing `serial` runs the single-timeline ordering above, unchanged
   and still correct.

   **Why this is the default and not the opt-in.** Serial ordering is what the foreground-poll
   mandate cost: measured across four suite windows, 72-86 minutes per suite with the model
   blocked on `poll_job.sh`, against 47 for the one suite that overlapped its lanes on its own
   initiative. The work is the same; what serial buys is a single timeline to read, and what it
   costs is every wait spent doing nothing. The ordering rules below make the overlap safe — the regeneration slot set is
   computed BEFORE either lane starts, so nothing is repaired on an artifact about to be
   replaced — which is the property serial was standing in for.

   **The way OUT is one file write, and it takes effect at the NEXT interval.** If the split
   misbehaves on a live build — a lane that will not converge, a pin refusing a replay you
   believe is sound — drop back rather than improvising around it:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   echo serial > "$WORKDIR/.loop_mode"
   ```

   **The way back IN is the same one file write** (an explicit marker also survives a resume,
   where an absent one is only ever read as the current default):

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   echo interleaved > "$WORKDIR/.loop_mode"
   ```

   Nothing needs reinstalling, restarting or re-running; the interval in flight finishes under
   the rules it started with. **Do NOT update the plugin mid-build to change this** — the
   installed plugin is a cached snapshot keyed on version, so an update swaps the loop's own
   instructions underneath a build whose earlier intervals ran on the previous ones, and the
   markers on disk were written by those. Finish the suite, then update.

   - **scenario / harness lane** — regeneration, env fixes, `simulate.py` fixes. It publishes
     snapshots; its long poles are background jobs that re-invoke you when they land.
   - **eval lane** — `/edit-evals` plus `score_run` REPLAY over the SAVED run artifacts, validated
     against the PINNED snapshot rather than against a fresh run.

   The lanes are INTERLEAVED IN ONE SESSION — never parallel sessions, never a subagent per lane.
   Two lanes writing at once would race the flow-window marker, the one-shot ask sanction,
   `report_guard`'s single in-progress anchor and the unlocked marker files, for minutes of gain.
   The whole mechanism is: while a scenario-lane job is in flight, you work the eval lane in that
   window rather than waiting on it.

   **Eval lane — budget 2 edit+rescore rounds per interval.**
   1. **CHECK THE PIN BEFORE EVERY `score_run`, never after.** A replay is only honest inside the
      frame it was pinned in, and a `score_run` against a moved frame still returns confident
      verdicts:

      ```bash
      source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
      python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" check --refusing-only --gt-revision <the last one reported> \
        --eval-revision "<get_artifact_meta(<eval_set_id>).detail.eval_revision, when reported>" \
              --set-corpus-ids "<get_artifact_meta(<scenario_set_id>).detail.corpus_ids, comma-joined, when reported>" \
              --backend-build "<backend_identity().build_id; '' if you have none — that reads backend-unknown, never drift>" \
              --backend-source "<backend_identity().source_id; '' if you have none — same rule>"
      ```

      Exit 0 → replay (a `MOVED (judgement only)` headline — e.g. your own edit moved the revision — is
      still exit 0). Exit 3 → a REFUSING axis moved: STOP the eval lane and wait for the rejoin; the
      reason it prints names what moved. Exit 2 → there is no frame at all, which is not freshness:
      re-upload and re-pin first.
   2. **Then the inner loop exactly as written above** — ONE `/edit-evals` session for the whole
      batch, ONE `score_run` per scenario, each result applied via the script with `--pin`.
   3. **`terminate_values` is the carve-out — such a fix is `rerun`, not `score_run`.** A policy
      declaring `terminate_values` can end the simulation early (a per-turn verdict ORs into the
      run's stop signal), so editing it changes WHERE FUTURE RUNS STOP — and a replay over a saved
      transcript cannot see that: the transcript already stopped where it stopped. Make the fix in
      the eval lane, then hand its VALIDATION to the next scored run and say so in the round's
      record. Nothing else about the stopping point is eval-dependent, so this is the only eval
      finding that leaves the lane.
   4. **Two rounds is the budget, and it is the existing stop rule by another name**: a finding
      still failing after two consecutive repair re-scores is not converging — carry it to the landing
      triage rather than grinding judge tokens on it; a guard report never counts.

   **Scenario / harness lane — ≤1 regeneration batch, ≤1 batched env-fix round with its single
   settling re-run, ≤1 harness-fix batch folded into that same re-run.** Per interval, and the
   caps are the point: this lane is what costs the round.
   - **Launch the long pole, then go work the eval lane.** A regeneration job, a re-run and a
     redeploy all run in the background and re-invoke you when they land. A turn spent waiting on
     one is an eval round nobody ran — that idle window is the entire reason the lanes exist.
   - **The regeneration slot set was fixed at partition time.** Do not grow it mid-interval:
     a slot added after the partition carries `gt@`/`facts@` findings nobody parked, and those
     get applied to artifacts about to be replaced.
   - **One re-run settles the whole lane.** Env fixes are probed and harness fixes go through the
     harness-fix procedure above, then the batch's single re-run produces the behaviour the
     rejoin's verification and red-cell audit read.
   - **A harness fix STOPS the eval lane until the rejoin.** Step 3 of that procedure moves the
     fingerprint, so the eval lane's pin check exits 3 (`harness-fingerprint-changed`) and
     `apply_rescore.py` refuses. That is the interlock working, not a failure: there is nothing left
     for the eval lane to replay against until the re-run lands.

   **REJOIN — the lane merge, and where the round is verified.** TWO triggers, and an
   interval ends at whichever comes first:

   - **The scenario lane LANDS** — its settling qualification re-run COMPLETES. Not when the
     regeneration job finishes: a set nobody has run is not a snapshot.
   - **The eval lane exhausts its budget with the scenario lane idle** — two edit+rescore rounds
     done and nothing regenerating. This is the interval the split exists for, and without a
     trigger of its own it had none: every finding routes to `score_run`, no re-run ever lands, so
     no barrier is reached and LANDING becomes unreachable. On this trigger
     steps 2-5 have no referent — the scenario set did not move, so there is nothing to relink,
     re-upload or re-validate — so run step 1, then step 6 against the EXISTING behaviour
     artifacts and their current verdicts.

   Then, in order:
   1. **Quiesce the eval lane.** Finish the round in flight against the OLD pin, then CLOSE the
      edit session — the backend refuses the relink while a session is held.
   2. **`relink_eval_scenario_set`** — this IS the merge. Work its audit output (guard coverage,
      dead flow scope, a premise warning carrying a concrete guard) in ONE `/edit-evals` round,
      charged to the NEXT interval, by the mechanical rules Step 3b (deliverable file) gives.
   3. **Re-upload the new run artifacts** → a new `run_upload_id`.
   4. **Rewrite the pin (`lane_pin.py write`).**
   5. **Re-validate on the NEW upload** — the eval edits that were committed but never verified
      settle here.
   6. **Verify and audit** — item 3's three checks on the NEW upload: the `score_run` full-set
      diff, the `suite_diagnostics` re-run, then the RED-CELL AUDIT. Log the rejoin
      (`--kind rejoin --lane joint`). A full **barrier critic** stands here ONLY when critic 1
      reported `RUN INVALID`; otherwise the audit is the barrier.

   **In-flight eval work is never abandoned at the barrier.** Committed-and-verified edits carry
   themselves — they live in the backend eval set and survive the relink. Committed-but-unverified
   are verified at step 5. Uncommitted get the bounded finish at step 1.

   **One interval, one rejoin, one audit — there is no per-lane critic and no pass counter.** The
   loop is ONE interval by default: critic 1 opens it, the rejoin verifies it, the landing closes
   it. A second interval exists only behind `RUN INVALID`, and it ends the same way. An eval-only
   interval reaches the rejoin through its own trigger above, so LANDING is always reachable.

   ---

   **The LANDING — after the red-cell audit, not another attempt at perfection.** Once the round
   is verified and the audit has ruled on every red, the objective is to make the simulation
   genuinely WORK and be worth running with what you have — not to clear every finding.
   Concretely:
   - **The triage into two buckets was made on critic 1's report, and only the first was fixed.**
     BLOCKING = the suite would mislead or not run: a scenario that cannot complete, an eval
     that scores the wrong thing, a phantom reference, a probe that tests nothing (green-but-
     hollow). ACCEPTABLE = it would merely be *better*: a persona that could be sharper,
     coverage you would like to widen, a complication you would rather have seen twice.
     A DEGENERATE sample is NOT acceptable — all scenarios walking one flow, trivial, or
     not gradable stays a scenario defect per the fix list above, landing or not.
     **The eval-side mirror holds too: an eval that cannot discriminate, or that scores the
     wrong thing, is BLOCKING — never landable.** A `prompt` false red still firing after its one `prompt_only`
     refinement is NOT an eval-side blocker: it lands labelled, and the report recommends optimizing that eval.
     **A `policy_never_scored` finding is never ACCEPTED.** The final draw demands its premise (a typed demand from
     its `target_complications` / `target_flows`, logged `--kind re-steer` naming the policy); still unscored on the
     final run, it is named untested in the release verdict (deliverable step 4). `eval_rounds.py never-scored` exits 3 otherwise.
     This floor is fair to impose precisely
     because eval fixes are now cheap to verify (`score_run`, no agent re-run): landing with a
     known-broken eval was once the economical choice, and is now just shipping a false
     verdict you could have fixed in minutes.
   - **Do not start a new fix you cannot finish and re-verify in this pass.** A half-applied
     fix is worse than a known gap — it changes behaviour nobody re-checked.
   - **When the verification or the red-cell audit surfaces BLOCKING defects, resolve them by
     SURFACE — however many there are.** Each one left **BLOCKING — UNRESOLVED** below is one
     `append_loop_log.py --kind blocking-unresolved --surface <eval|scenario|harness|env|backend|plugin|sdk>` entry, with
     `--policy` once per check it affects (required on eval and backend; or `--affects-no-check` on backend, for a crashed
     job that leaves no check unmeasured). A mock or world blocker is `--surface env`. The two rules above collide here:
     a blocking defect is never landable, but a new fix is not to be started at the landing. The resolution depends on
     which surface the defect sits on, and the count does not change it.
     - **Eval-side blockers — fix them ALL, here.** They are cheap and self-verifying
       (`/edit-evals` plus one `score_run` rescore, no agent re-run, minutes), so they CAN be
       finished and re-verified inside this pass. Batch the `rubric`, `harness` and `type` ones into the ONE
       `/edit-evals` session as usual, `prompt` rows under its `prompt_only`, and re-score once. A confirmed agent
       finding's coverage gap is an eval-side repair here too (`/analyze-simulation` step 3's probe rule), opening this
       session when nothing else does — never an ACCEPTABLE "coverage you would like to widen", and never an accepted
       gap. **Budget:** this is a bounded repair, not a re-opened loop —
       if the eval-side blockers cannot all be fixed and re-verified in a single edit+rescore
       round-trip, stop batching, land the ones you verified, and record the rest in
       `$ART/loop_log.md` as **BLOCKING — UNRESOLVED**.
     - **A harness blocker whose fix is known gets ONE bounded repair here.** Apply it through the
       harness-fix procedure (no question — a harness repair is never asked), then ONE re-run of the
       final set — the edit moves the harness fingerprint, so every recorded conversation is stale and
       the scored run is that fresh one — and ONE verification and red-cell audit of it. Still failing
       after that one cycle → record it as below; never a second repair.
     - **A scenario blocker, or a mock or harness blocker with no known fix, cannot be fixed here — and
       that is a different landing.** Regenerating scenarios or repairing the mock changes BEHAVIOUR, so
       it needs a fresh run and a fresh audit to verify: exactly the round you are landing out of, and a
       half-applied version of it is worse than a named gap. Do NOT start it. Land with the
       defect recorded in `$ART/loop_log.md` as **BLOCKING — UNRESOLVED**, naming the scenario or
       harness surface and what it invalidates — a mock or world blocker is `--surface env`. The hand-off anchor then
       reads **not yet qualified** (`lane_pin.py anchor` caps it on any scenario, harness or env `blocking-unresolved` entry).
       A harness entry is released, at most to **qualified**, once a `harness-fix` entry logs its repair and the scored
       final run started after both and voided no scenario on a seed that did not land.
     - **A backend blocker on a check only the backend judges** (a `beat_resolution` check — no client edit reaches
       it) is recorded `--surface backend --policy <check>`. When the final run's red-cell audit rules that check's
       failures FALSE RED, the anchor lands it as a named risk at **qualified**; otherwise it caps like any other.
     - Only if an eval-side blocker genuinely cannot be fixed from here (it needs a scenario
       regeneration, or real records only the user has) does it join the same
       **BLOCKING — UNRESOLVED** list, naming the eval and what it fails to measure. An eval entry caps the rung
       only while the final run still carries a wrong or unchecked verdict on that eval (`lane_pin.py anchor`
       re-checks it).

     Never land a blocking defect silently; a suite reported as validated while carrying a judge
     that cannot discriminate is the false verdict this floor exists to prevent.
   - **Target-clause FALSE REDs become rulings, here, before the final draw.** They have no rubric
     for `/edit-evals` to fix, and a fresh draw re-derives the clause. Write every `target_clause`
     row of the qualification run's audit into `scenario_variables.yaml` (validated; a hand-written
     block is refused, never overwritten):
     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
     "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_target_rulings.py" \
       --variables "$PLURAI_DIR/scenario_variables.yaml" --audit "<run dir>/red_cell_audit.json" \
       --scan-contract "$ART/scan_contract.json" \
       --prd "$PLURAI_DIR/simulation_prd.md" --source "red-cell audit, set <scenario_set_id>"
     ```
     Log it `--kind input-edit --scope scenario`, and re-stash the variables so Step 3b passes the new
     `variables_upload_id`. Deliver and re-pin by Step 0b item 6. A ruling travels only this way — never as `artifact_feedback` prose.
   - **A `scan_fact` FALSE RED retires its fact, here, before the final draw — required, not
     optional.** The scan's premise is false for the agent's sources, and every consumer re-derives
     from the PRD fact, so leaving it re-deals the same probe. Delete the PRD `## Agent facts` row
     whose id is the audit row's `fact_id` (its `id:`, else `subject.predicate`) — a `prd_absences.*` fact has no
     row: correct or delete the PRD Absences line its claim quotes, re-stash the PRD, and skip the contract steps —
     then re-run the contract and read the gate:
     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
     "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse --after-edit
     "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" contract-gate; echo "GATE_EXIT=$?"
     grep -qF "<fact_id>" "$ART/scan_contract.json" && echo "STILL PUBLISHED" || echo RETIRED
     ```
     `GATE_EXIT` must be `0` and the contract must no longer name the `fact_id` (`RETIRED`). Then
     re-stash the PRD and the contract (pre-authorized by the invocation — never ask consent for it: `plurai stash-doc <prd> simulation_prd`, `source
     stash_scan_contract.sh`) so Step 3b passes the new `prd_upload_id` and `scan_contract_upload_id`,
     deliver the PRD and re-pin by Step 0b item 6 (the pinned version would otherwise put the fact back into every
     generator's prompt), and log it `--kind input-edit --scope scenario`. Never carry it as a ruling: the writer refuses a
     scan-authored judge typed `target_clause`.
   - **Then land it**: proceed to Step 3b (deliverable file) and generate the final suite. Do not loop again, and
     do not hand the decision back to the user merely because some ACCEPTABLE findings remain.
   - **The real-data escalation still overrides this.** The carve-out above — a grounding
     mismatch only the user can resolve with real records — is BLOCKING by definition and
     cannot be fixed by you, so landing does not apply: surface the specific ask and do NOT
     report the suite as validated. Landing means "stop polishing", never "stop escalating".
   - **Landing is YOUR call, and it is not a critic sign-off.** The critic and the audit decide
     what is wrong; at the landing you are consciously accepting known gaps. Record which in
     `$ART/loop_log.md` — see Step 3b (deliverable file).

   - **An eval that CANNOT FAIL is never an accepted gap.** If the critic or the audit found a
     check to be over-crediting — it greens on evidence it should not accept — and you DECLINE to
     repair it, that check does not land as a known limitation. Drop it, or quarantine it with a
     `never_measured` marker, and write the decision to `$ART/deferrals.md`. The shape that keeps
     getting landed: a check whose match is scoped wider than the thing it grades — a whole-
     transcript search for a term the beat expects at one moment — so an earlier turn satisfies it
     and it cannot go red on a live run. Listing it under "accepted gaps: over-credits" ships it as
     an eval a reader takes for a pass. An accepted gap is something the suite does not COVER; a
     check that always greens is something the suite reports WRONGLY, and the two must not land
     through the same sentence. A confirmed agent finding no judge grades is neither: it is the eval-side
     repair above, and lands as a gap only when no existing judge's axis fits it.
   - **`deferred` and `not_applicable` never render as a pass.** In any report, any summary and any
     hand-off, a cell that was voided or that found no occasion to fire is reported in its own
     words. A scenario-scoped judge voided by an earlier red reads `deferred`, and one that found
     no occasion reads `not_applicable` with its reason — in both the cell measured NOTHING, and
     counting either among the passes reports coverage the run does not have.

   A suite with a couple of named, accepted gaps that RUNS and is worth running is a WIN —
   "sharp signal" stays reserved for a clean critic report, which a landed suite by
   construction does not have. An endless quality loop that never ships is not a win.

**If a qualification run isn't possible**, either the user chose that at the Step-0 run-prerequisite
ask (static-only suite), or a prerequisite broke after Step 0c's re-check passed — an expired provider key, a
mock env that stopped answering, a run target that vanished. **In the second case, ASK; do not
quietly convert the run into a static review** (sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`): say in
product terms what's missing, offer supply-it-now vs static-only, and continue on the answer. Only
once static-only is the user's *choice* do you fall back to a single critic pass over the scenarios
+ evals + harness (findings-only, best-effort — the critic has no run artifacts to reproduce
against, so coherence is a guess), remind the user what to provide to finish scoring, and not report
the suite as validated.

## Gate exit — record regressions from the confirmed findings

**Run this once the gate has landed, and before Step 3b's staging** *(deliverable file)*. Each
`confirmed` section the quality loop wrote to `$ART/agent_findings.md` is an agent defect a critic
reproduced against the real ground truth, in a conversation that exists only in the qualification
run's artifacts. The final batch regenerates every scenario, so a finding left unrecorded ships
with nothing in the delivered set that reproduces it. Recording it here, before the stage, puts
the folder in the same build's bundle, so the delivered set already guards the issue.

**It asks nothing.** The rules below settle every choice, so there is no gate, no sanction and no
question; a headless build runs it the same way. The operator's only control is the invocation:
`/simulation-generate-suite --no-auto-regressions` turns it off for that build, and the plan below
then records nothing and says so.

**The run is the last qualification run the gate validated** — the one the landing's verification
read, which the eval lane's pin names. Never an earlier round whose bench the loop repaired
afterwards, and never the final batch, which runs after this step:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
RUN="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("out_dir") or "")' "$WORKDIR/.eval_lane_pin.json" 2>/dev/null)"
[ -n "$RUN" ] || RUN="$(cat "$WORKDIR/.last_run" 2>/dev/null)"
echo "run: $RUN"
```

An empty answer means no qualification run is on record: treat the suite as one that never
validated a run (the last paragraph of this step).

**The gate's report stands in for `fixes.md` in the clean-run gate.** A regression replays the
agent's own conduct, so a finding is recorded only when no bench issue the gate left open could
have produced it. Read the open ones off the landing — every **BLOCKING — UNRESOLVED** entry, every
accepted gap on a scenario or eval surface (an accepted HARNESS gap is `--accepted-harness` below, not
an open issue), every red the red-cell audit ruled the bench's,
and every unticked line in `$ART/deferrals.md` — and pass each one as a flag (only those four
count: a cell the audit ruled TRUE RED is not bench-touched by a critic's root on the same cell):

- **`--harness-wide "<its title>"`** — an OPEN issue that reaches every scenario: a BLOCKING —
  UNRESOLVED or unticked deferral on `simulate.py`, the harness config or the mock environment, any
  `harness_*` error kind. Any one of these records nothing from this run.

The **not yet qualified** anchor is not an issue of its own: flag each entry that caused it by the
surface it names. An entry that names no bench surface — a backend job that failed or never ran —
touches no conversation and is no flag.

- **`--accepted-harness "<its title>"`** — a harness gap the landing ACCEPTED in a run it kept
  **qualified** (a customer-owned `simulate.py` gap its one bounded repair did not close, say). It is
  listed in the plan and skips nothing: the clean re-run an open issue waits for never comes for an
  accepted gap, and the finding's `why it is the agent:` line was written over it. When the critic
  names a scenario or policy the gap reaches, pass those as the two flags below as well.
- **`--bench-scenario <name>`** — an issue in one scenario's seed, premise or grading ground truth.
  It drops the whole scenario: every policy's finding seen only there is skipped, real agent
  defects included.
- **`--bench-cell <scenario>:<policy>`** — a prompt or rubric false red of ONE policy on a few
  cells (the red-cell audit's `FALSE RED` with surface `prompt` or `rubric`). It drops only that
  policy in that scenario, so a finding on another policy seen in the same scenario still
  records. Repeat it per cell; use it instead of `--bench-scenario` whenever the scenario itself
  is sound. Name the audit row's item (`<scenario>/trial_<N>:<policy>`, trial 1 as `…/trial_1`) when the false red is
  on one trial: a bare scenario drops every trial of it, and so a true red on another trial too.
- **`--bench-policy <name>`** — an issue in one eval: too strict, mis-scoped, or unable to discriminate.

Then plan, from the findings file and the folders already on disk:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
rm -f "$ART/auto_regressions.json" "$ART/auto_regressions.txt"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-plan "$ART/agent_findings.md" \
  --regressions-dir "$WORKDIR/regressions" --run "$RUN" \
  [--harness-wide "<title>"]… [--accepted-harness "<title>"]… [--bench-scenario <name>]… [--bench-cell <scenario>:<policy>]… [--bench-policy <name>]… [--no-auto-regressions] \
  > "$ART/auto_regressions.json"
echo "plan exit=$?"; cat "$ART/auto_regressions.json"
```

The plan takes `confirmed` sections only; a `potential — may be intended` one is never recorded.
It reads the run over every trial. A finding with a typed record of this run (`agent_findings.jsonl`) plans from the
conversations where it owns its probe check (the run's finding attribution), each cut at its own turn; a section with
no typed record plans from its `seen in:` names. A name never crosses runs: a finding seen only in another run is
skipped as not reproduced here. A finding whose conversations hold no user turn (a pipeline's run) is
skipped as `no conversational turn to record`, ahead of any bench reason. It skips a finding whose
probe's policy a bench flag touches, and one with no conversation outside the
`--bench-scenario` flags and the `--bench-cell` cells of its own policy that has that policy violated in the run. It
runs the record's own `pick` on each, so a finding the pick would refuse (a whole-conversation check that names no
turn, replayed resumed) is skipped with that reason and takes no place under the cap. It skips one whose probe names no
eval policy, since the policy is what chooses the conversation and the cut, and a section whose `seen in:` names no
scenario.
A finding whose folder is already on disk is covered — the folder name is the slug the finding id
derives, the same name `/simulation-fixes` looks for when the finding is accepted later. The rest
rank by severity, then confidence, and the first **6** are recorded; the others are named as over
the cap. Folders from earlier builds are not counted against it: they ride under the final batch's own
scenario cap. The folders THIS build wrote are, whatever plan file listed them: each `auto-report` below adds its
written folders to the build's ledger (`$ART/auto_regressions.build.json`, cleared when a new build arms), and
`auto-plan` counts the ledger's folders still on disk. **`record`** lists what to record, in order; **`skipped`** names every other finding
with its reason. The `rm -f` comes first because `$ART` outlives the build and Step 6 copies
whatever line it finds: a file left from an earlier build would report that build's records as
this one's. A non-zero exit means the findings file or the run could not be read: record nothing, log the
refusal in its own words (`append_loop_log.py --kind note`), and go on to Step 3b — with no line
file, Step 6 carries no regressions line.

**Record each one in `record`, in order,** by running the **`record-regression` skill**
with `--run <the run above> --finding <its finding_id> --plan "$ART/auto_regressions.json"`. Its
`pick` reads that row — the finding's conversations, each one's cut and the plan's `bench` block — so it cuts
what the plan counted, and records nothing when none of them holds a conversation to cut. It
owns the rest, and applies the same checks to the gate's report that it applies to `fixes.md`. Each
invocation ends with the folder it wrote or a refusal. A refusal is not retried and does not stop
the build: note its reason in a few words and go on to the next.

**Then write the report line** from the plan and the refusals:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-report "$ART/auto_regressions.json" --findings "$ART/agent_findings.md" \
  [--refused "<finding_id>=<the refusal, in a few words>"]… [--rare "<finding_id>"]… > "$ART/auto_regressions.txt"
cat "$ART/auto_regressions.txt"
```

`--rare` names a record that ended rare at its cut (record-regression Step 6c, exit 3): its folder was
written and then removed, so it is neither recorded nor refused. `auto-report` keeps both in the plan file.

It prints one line; Step 6 *(deliverable file)* re-renders it from the same plan against the findings
file as it stands at hand-off time, so later confirmed findings are counted:

> Regressions: 2 recorded from confirmed findings — `regression_retired_label_left_in_data`,
> `regression_false_balance_claim`. 1 more confirmed issue has no regression yet; it stays in Agent
> findings for `/simulation-fixes`.

It counts the issues `agent_findings.md` reads confirmed now; why each one has no regression goes
to stderr for the log, never into the line.

**Running this step again is safe.** A finding recorded on the first pass is covered by its folder
on the second, so a build that dies between here and Step 3b re-enters without recording twice.

**Plan once more after the final scored run.** The plan above reads the qualification run, so a
finding the loop wrote after it, or one the final run sees again in a clean scenario, has no
folder yet. Once Step 3b's final run and its red-cell audit have landed, and `agent_findings.md`
holds their findings, re-run the same `auto-plan` with `RUN="$(cat "$WORKDIR/.last_run")"` (the final run) and the
same bench flags, then record (`--plan "$ART/auto_regressions.json"`, the new plan) and report whatever it newly
lists. Folders already on disk are covered, so nothing records twice, and the build's ledger counts the gate exit's
folders against the 6, so the build records at most 6 in all. The 6 are filled by severity across the build: when
they are full, a row may name an earlier folder of strictly lower severity under `evicts`, and `auto-report` moves
that folder to `$ART/dropped_regressions/` once the new one is written; its line says so.

**A suite that never validated a qualification run records nothing** — the operator chose
static-only at Step 0, or a prerequisite broke mid-loop and static-only became their choice. Skip
the record and write the line with `auto-plan … --static-only`, then `auto-report` as above.

## Step 3b onward — the deliverable phase (READ IT NOW)

**Progress fill** `suite-final` ("Progress fills", above) first — not for a static-only suite.

The gate has converged. Everything from here — the FINAL generation at `count = 7`, the eval
relink + audit, `$ART/suite.json`, the render and the report skeleton — is in
one file, and it is INSTRUCTION, not reference material:

```
${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/deliverable.md
```

**Read it in full, now, with `Read` — then follow it in order** (Step 3b → 4 → 5 → 6 → 7). It
is ~100 KB and it carries the same authority as every step above; skipping it, skimming it, or
reconstructing the final phase from memory ships a deliverable built by a different procedure
than the one this skill validated.

**Why it is not inline.** Per-call context is the binding constraint on a suite, and latency
tracks it. Pasting this whole skill into the invocation turn would add its full size to every call
before Step 1. The deliverable phase is unreachable until the loop
converges, so carrying it through the loop buys nothing and is charged on every call. Read it
ONCE at this point; do not re-read the whole file to recover one rule (see the re-reading rule
in Step 3).

## Flag — needs a backend fix or the traces sync (do NOT fake)

**A backend finding that survives a regeneration is a backend matter, not another regeneration.** ONE
regeneration is a fair test of "was that draw unlucky?". If the same finding text comes back on a
different scenario, it is structural: nothing the client can edit reaches it, so it recurs at the
same rate however many times you redraw. Record it here with the verbatim text and the scenario
ids, say in the report only that those scenarios ship with a named flag, and move on. The user can
report it to support@plurai.ai or on Discord (https://discord.gg/bg34gh76uJ), quoting the scenario set id.

Guard: do not edit the INPUTS to chase one either — a schema change made on guesswork regresses
the whole batch, and the finding still recurs. Examples of this class:
`ground_from: the narrative does not reference the grounded … entity` and
`pinned_context_mismatch` redraw exhaustion.

Leave the corresponding report sections empty rather than fabricated. These are
informative provenance notes, not errors — don't volunteer backend internals; if the
user asks why a section is empty, explain in one plain sentence (e.g. "this fills in
once your production traces are synced"):

- **Value ranges** (e.g. `records 8..340, cap 5,000`) and **failure/noise rates**
  (`tool_timeout 1.8%`, …) — require the production-traces sync (not available yet).
  `render_suite.py` already supports `environment.bounds` / `environment.noise`;
  populate them once traces exist.
- **"Rare-path / high-complexity coverage"** — per-scenario path probability IS now
  recorded server-side and READ in Step 4; that client-side derivation is retired, so do
  not reinstate it from this section. What is still missing is the SET-level metric: "% of
  high-complexity low-probability valid paths covered" (the third coverage card) needs the
  backend to enumerate valid paths + their probabilities and mark which are covered.
  That's server-side IP — **flag, don't build it client-side.**

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

## Notes

- **Backend objects stay on the backend** — persist only `.scenario_set_id` /
  `.eval_set_id` / `policies_config.yaml` + the rendered artifacts; never dump fetched
  scenario YAML into the repo.
- **Named-env aware**; everything under `$ART` (gitignored).
- Plain language to the user — no `plurai_sim` / yaml file names in questions.
