---
name: simulation-scan-agent
description: >-
  Map the agent and write its harness: decide which surfaces are mocked or real, show a topology diagram for approval, then generate simulate.py and plurai.config.yaml. Stage 1 of /simulate; run it first, with the path to the agent module.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Scan agent — map the agent + write its harness

**Usage:** `/simulation-scan-agent <path-to-agent-module> [--force] [--component "<name + what it does>"] [--restart]`

You are the LOCAL Claude Code agent. This is **command 1 of 5** in the Plurai
simulation lifecycle:

```
scan → traces → suite → run → fixes
 map     learn    build   score  repair
 ▲ you are here
```

The defining move of scan: the **mock-vs-real decision is approved visually**.
You run setup-simulation's analysis up to its decision point, render that
proposed split as a topology diagram, and let the user APPROVE the picture
**before any files are generated**. Only then do you write the harness and
record what grounding the agent will need.

```
┌── /simulation-scan-agent <path> ──────────────────────────────────────────┐
│  1. setup-simulation ANALYSIS only  → propose mock|real per surface        │
│        (read code → deps → tools/sub-agents → mock-vs-real decision)        │
│  2. RENDER TOPOLOGY from the proposal → artifacts/agent_topology.{md,svg}   │
│        give the user a clickable link · they APPROVE the split  ◀── GATE    │
│  3. on approval: setup-simulation GENERATION → plurai.config.yaml,          │
│        simulate.py, test_mock_contract.py · iterate until simulate runs     │
│  4. run-mode marker (mockless agents) · RECORD the grounding requirement    │
│     (the corpus itself is built by /simulation-generate-suite's gate)       │
│  4b. deliver the agent's descriptions → v0 grounded                         │
│  5. ensure artifacts/ is gitignored · summary                              │
└───────────────────────────────────────────────────────────────────────────┘
```

**Reuse, don't reimplement.** Steps 1 and 3 are existing skills. Your only *new* work is
step 2 (the topology gate) and step 5's gitignore housekeeping.

**Scan does not build the knowledge corpus.** It records that one is NEEDED;
`/simulation-generate-suite` asks about it and builds it at its grounding gate. A corpus is an
expensive backend job over the customer's own documents, and its scope decides what the whole
suite can test — so it belongs at the gate that owns every other grounding input, beside the
trace brief and the doc examples, where the user is actually asked.

**Scope boundary.** Scan does NOT generate evals (`generate-evals`); that belongs to a later
command. Pass `--no-generate-evals` to setup-simulation. Scan DOES extract the scenario
variables: they are one of the agent's descriptions, and the grounding delivery at Step 4b
needs their user profiles and complications. Never pass `--no-extract-vars`.

---

**A gate the user did not answer is RE-ASKED.** If they reject the question, interrupt it, or
reply with something other than a choice, you have no answer — never pick its default, never
pick the recommended option, and never state a choice they did not make. Answer whatever they
actually asked, then put the same gate back. (The flow window re-arms the sanction for you, so
the re-ask is not denied.) Inventing an answer here is not a shortcut: a fabricated corpus
scope silently became the entire product universe for every scenario in one live suite.

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

## Step 0 — parse args + resolve directories

**A blocker's answer is not a stop.** An answer that picks a way forward ("continue without it", "use a made-up
value") is applied, and the stage finishes on its hand-off: "continue without installing" skips the install and
nothing else, so the scan still ends with its normal hand-off and the run goes on to the suite. Only the user's own
request to stop ends a `/simulate` run (`simulate end`, which exits 3 until they have typed one), and a stage never runs it.

Parse the user's invocation:

- **`<path-to-agent-module>`** (required) — the agent's code, a folder or one file in it. If absent, ask.
  Every snippet below runs in **`<agent-folder>`**, the folder that path names (a file names its own folder), so the
  saved setup (`plurai_sim/`) is always created beside the agent module: for `src/app/agent.py` it is
  `src/app/plurai_sim/`, never the project root.
  Resolve it once and substitute the absolute path this prints wherever `<agent-folder>` appears:
  `p="<path-to-agent-module>"; [ -d "$p" ] || p="$(dirname -- "$p")"; (cd -- "$p" && pwd)`
- **`--force`** — re-analyze the agent even if a prior setup already exists. A scan of this agent that stopped
  partway still resumes (Step 1).
- **`--component "<name + what it does>"`** — focused testing: set up + test only
  one part of the agent (a sub-agent, tool, or subgraph) instead of the whole
  thing. See Step 1b.
- **`--restart`** — start over: set aside a scan of this agent that stopped partway instead of resuming it.

> **Talking to the user — no jargon.** When you ask the user anything, speak in
> plain product language. Do **not** surface internal artifact names like
> `plurai_sim`, `plurai.config.yaml`, `simulate.py`, "sim", "harness", or
> "mock contract". Say "a saved setup for this agent", "the map of your agent",
> "what's mocked vs. run for real". File names belong in this SKILL (for you),
> never in a question to the user.

**Resolve the client-side working dir (named-env aware, ABSOLUTE).** This is the FIRST
scan of a possibly-fresh agent, so seed the anchor BEFORE sourcing the resolver: with no
`plurai_sim/` anywhere yet the cascade falls through to the git toplevel, and if some OTHER
agent in the repo already has a `plurai_sim/` the resolver REFUSES (it only adopts a
repo-wide sole match that sits at or below your agent path) — `$WORKDIR` then points at an
unwritable sentinel and `$PLURAI_AGENT_RESOLVED=0`. Creating the directory at the agent
path first makes the nearest-ancestor rule win deterministically, so neither branch fires:

```bash
export PLURAI_AGENT_PATH="<agent-folder>"                   # name the agent: every cascade step is bound to it
mkdir -p "<agent-folder>/plurai_sim"                        # seed the anchor at THIS agent (no-op if it exists)
cd "<agent-folder>"                                         # resolve from the agent, not wherever cwd happens to be
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # exports ABSOLUTE $WORKDIR / $PLURAI_DIR / $ART
mkdir -p "$ART"
```

**Repeat that export in EVERY Bash call that sources the resolver** — the harness resets the
shell between calls, so an export in one block is GONE in the next and the later source runs
UNSCOPED, which is exactly the hijack the scope exists to stop. (Same reason the `cd` is repeated
in the later snippets.) With it set, a `plurai_sim/` found ABOVE a reset cwd that is not at or
below it is refused rather than adopted. The resolver prints
`resolve-workdir: WORKDIR=… (via <step>)` only when the root was not found by walking up from the current folder
(a first scan, or a lone `plurai_sim/` below it) — read that line back when it appears.

The script walks UP from `$PWD` to the nearest `plurai_sim/` (then falls back to the repo's
single match *within your agent's subtree*, then the git toplevel), and the exported paths
are ABSOLUTE, so they survive the harness's cwd resets between tool calls — a relative
`plurai_sim/...` in one snippet silently lands elsewhere in the next. Every skill snippet
that reads/writes a `plurai_sim` marker sources this first and uses `$WORKDIR`/`$PLURAI_DIR`
— and **within THIS command every re-source `cd`s to the agent path first** (the later
snippets carry the `cd`): a re-source from the reset cwd re-runs the repo-wide fallback,
which refuses the moment a second `plurai_sim/` exists anywhere in the repo. If a snippet
cannot `cd` (or the resolver still refuses), `export PLURAI_AGENT_PATH="<agent-folder>"`
before sourcing — it scopes the fallback to your agent. Print `$WORKDIR` once after
resolving and sanity-check it sits under the agent path you were given.

ALL topology artifacts (`.md` report + `.svg` + `.json`) live in `$ART` — one
gitignored folder; nothing derived is written to the working-dir root.

---

**Open the flow window** (flow-fidelity) once directories resolve:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm scan-agent
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready scan-agent
```

(the script resolves `$FLOWDIR`, clears the stale ask/report residue and stamps the name —
overwrite, because nothing drives this command as a sub-step, so any other name in the
marker is a stale window from an earlier flow).
While it exists the hooks deny unsanctioned asks and style-validate the final summary.
This command's sanctioned asks each write the one-shot marker first (the saved-setup
gate, the focused-component pick, Step 2's approval gate, and Step 4b's consent screen when the scan found
every source — each carrying its own sanction write). Scan drives no skill that asks: the corpus build, and every question it carries, belongs
to `/simulation-generate-suite`'s grounding gate. The window closes itself
when Step 5's validated summary renders; early exits close it own-marker-guarded:

<!-- BEGIN step0: generated from ${CLAUDE_PLUGIN_ROOT}/docs/step0-block.md; edit there -->
**If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

**1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

- A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close scan-agent`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
- A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /simulation-scan-agent` (where the harness takes no arguments: "you run it for /simulation-scan-agent"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
- A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff scan-agent` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

**2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close scan-agent`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

- Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
- Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

**3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready scan-agent --no-backend-tools` and act on the line it prints as above.
<!-- END step0 -->

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close scan-agent
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

## Step 1 — decide the path (RESUME, else CHECK FOR AN EXISTING SETUP)

**RESUME FIRST.** Before the setup check, ask the scan's own record whether an attempt of this scan stopped
partway. Append ` --restart` to the call when this invocation carried `--restart` or `--component`:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" scan-resume "$WORKDIR"; echo "resume exit=$?"
```

| `resume exit=` | Then |
|---|---|
| `4` | An attempt is open, and the lines name its `resume_step`, `mode` (and `component`), the `verify_mode` Step 3c passes to `verify --mode`, the steps `done`, the files it `wrote` and where to `enter`. Skip the setup check, the saved-setup question and the path decision, and go straight to the place `enter` names, in the recorded mode. The steps `done` lists have landed: read their outputs, never redo them, and never ask their questions again; From there on, keep every file `wrote` lists, re-check each with its own check (`verify_config`, the harness smoke, `lint_prd.py`, the scenario variables' verify) and regenerate only what fails. A `WARNING` line is information for the summary, never a stop. `--force` does not set an open attempt aside; only `--restart` does |
| anything else | Nothing to resume: continue with the setup check below |

**Before any analysis**, check whether a setup already exists for this agent — in BOTH places,
because either alone reports "no setup" for a bench that exists:
- the working tree: `$WORKDIR/plurai.config.yaml` + `simulate.py` present and valid;
- git HEAD / history: `git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git log --all --reflog --oneline -- '*plurai_sim/plurai.config.yaml'`
  — it may be committed even when deleted in the working tree (`--reflog` also reaches a commit whose
  branch was deleted, while its reflog entry survives). Outside a git repository there is no history:
  check the working tree only.

Say which you found (working tree, git HEAD, or both) when you report the path decision.

- **Setup exists, and neither `--force` nor `--component` was passed** — STOP and
  ask (sanctioned ask — first run
  `cd "<agent-folder>"; bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction saved-setup-gate`),
  in plain language (no file names):
  > I found a saved setup for the **`<agent name>`** agent. How do you want to proceed?

  1. **Use the saved setup** *(recommended, fast)* — don't re-read the agent; show the current map and finish setting up.
  2. **Re-verify the saved bench** — don't re-read the agent and generate nothing; re-run every
     check the bench declares (the harness smoke, the agent's live surface, the read-only probes,
     the contract) and report what holds. Use when the agent is unchanged but the bench has not
     been exercised recently — a harness, a provider key and an agent's own surface all move
     without the code changing.
  3. **Re-analyze the agent** — re-read the code and rebuild the map (use if the agent changed).
  4. **Focus on one component** — set up + test a single sub-agent / tool / subgraph; I'll ask which.

  **BATCH the host-effects question into this same call** when the saved
  `$ART/agent_topology.json` carries `consent: "required"` tools or a declared
  `agent.tool_interception` and `plurai_sim/.host_effects_consent` is absent or says `unasked` —
  same wording and same three-state record as Step 2c. This is the reuse branch's ONLY chance to
  ask: it skips the topology gate entirely, so a saved-setup scan could never obtain consent. One
  sanction authorizes one CALL, not one question — batch, never ask twice. Tool probes need no
  question (Step 2c).

  Map flags directly: `--force` → choice 3; `--component "..."` → choice 4.
- **No setup exists** — proceed as choice 3 (full analysis); no question needed.

**Record the attempt** once the path is decided, before anything below runs: `--mode` is the choice's mode
(`reuse` · `reverify` · `reanalyze` for Re-analyze or no setup · `focus`), and the heredoc carries the focused
component's name + what it does, or nothing when not focusing:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" scan-open "$WORKDIR" --mode <mode> --component - <<'COMPONENT'
<the focused component, or nothing>
COMPONENT
```

Step 5's `fingerprint` closes the attempt; a non-zero exit here is not a stop.

Then branch — **honor the choice exactly**:

| Choice | Analyze (1b) | Topology approval gate (2c) | Generate (3) | `verify --mode` (3c) | Ground truth (4) |
|---|---|---|---|---|---|
| **Use the saved setup** | **SKIP** | **SKIP** | **SKIP** (except 3b's PRD re-stash) | `reuse` | run |
| **Re-verify the saved bench** | **SKIP** | **SKIP** | **SKIP** (except 3b's PRD re-stash) | `reverify` | run |
| **Re-analyze** / no prior setup | run | run | run | `reverify` | run |
| **Focus on one component** | run (scoped, 1c) | run | run | `reverify` | run |

The mode is the ONLY thing the choice decides about what executes, and there are exactly two:
`reuse` re-runs everything that is not hash-gated and reuses a probe whose script and sources are
untouched; `reverify` forces those too. After a re-analysis the hashes have moved anyway, so
`reverify` there is belt and braces rather than a third behaviour. Which checks are hash-gated is
the manifest's to say, not this table's — that is the whole point of the file.

> **This is the fix for the reuse complaint:** if the user picks "Use the saved
> setup", do **NOT** re-run the analysis and do **NOT** ask them to approve again.
> Read the `mode` of each tool straight from the existing `plurai.config.yaml`, build
> and show the map as an **FYI** (step 2 — link only, no question), re-stash the PRD
> (step 3b — a free no-op on an unchanged document, and a fresh box holds nothing),
> then step 4: record the run mode (`bench_manifest.py run-mode`) and the
> grounding requirement. Re-analysis + the approval gate happen only when
> you are about to (re)generate files.
>
> **What this branch no longer does is re-deploy the mock.** `/simulation-generate-suite`
> deploys the stored world by id in seconds (its Step 0c), so a reuse scan that deployed
> a mock the user might not run for days was holding a port and a subprocess for nothing.
> If the intent is "just bring my mock back up", that is an env switch, not a scan.

### Step 1b — analyze the agent (re-analyze / focus / no prior setup only)

Run the **`simulation-setup` skill** with
`<path> --no-generate-evals` — but **stop at its decision point**
(its "Step 7: Confirm decisions with the user"). That is: do the full analysis
(read the agent code, identify production dependencies / context nodes /
checkpointer, enumerate tools + sub-agents, and decide each surface's
`mode: mock | real | observe`), but do **NOT** yet generate `plurai.config.yaml`,
`simulate.py`, or run the iterate loop. setup-simulation already pauses here for
confirmation — that pause is your topology gate (step 2). **Skip this entire step
in "use the saved setup" and "re-verify the saved bench" modes** — neither re-reads the agent.

### Step 1c — focused testing on one component

When the user picks a component (via choice 3 or `--component`), narrow the whole
scan to it:

- The **named component runs live** (it is the thing under test), along with only
  the deterministic surfaces it directly needs.
- **Everything outside the focused component is `mocked`** — its
  callers, siblings, and unrelated tools are stubbed so the test isolates the
  component. This is exactly the live/mocked model, scoped down.
- Use the user's free-text definition to resolve which surface they mean (match a
  sub-agent / tool / node name in the code). **The trigger is a COUNT, not a feeling:
  exactly one candidate matches ⇒ take it and say which you took; zero or more than one
  ⇒ ask them to pick**
  (sanctioned ask — first run `cd "<agent-folder>"; bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction component-pick`)
  from the candidates you found — again in plain language.
- Title the topology after the focused component (e.g. agent `name` =
  "BillingSupportAgent › ReplyDrafter (focused)") so the map is clearly
  about the isolated component.

The rest of the flow (topology gate → generate → record the grounding requirement) is
unchanged; it just operates on the narrowed surface set.

---

After this step you hold, for every in-scope surface, a proposed classification:
- **`mocked`** = pinned to a fixed result during simulation, so assertions repeat. (`mode: mock`.)
- **`live`** = run for real against the seeded world / deployed mock. (`mode: real`
  or `observe`; deterministic tools, read-only queries, retrieval.)

**A surface is NOT mocked just because its backing store is "not present."** The
simulation provides a seeded world-state that stands in for the real store, so
deterministic reads/writes against it run **live**. Only mock for one of the four
real reasons below. That stand-in is ONE route per store (simulation-setup Mode 4): a seam mock that names the
live tools in `serves:` (the mock is a mocked surface, the tools stay live), a real engine the harness runs, or
the agent's own in-tree implementation when it is the run path's only one — never a storage class the harness writes. The seeded world is the SCENARIO's world (the rows each scenario seeds), never a
fixture the harness rebuilds identically for every conversation. A harness-owned store (in-process
fixture, work folder, leg-local database) is declared under `world_state` and seeded from the scenario by
`seed_state`, whatever the tools' mode. An external store the run must not write is served through a
declared read-through overlay, or its type is declined with a cited `not_seedable` fact and the
state-dependent families on it are a named coverage gap.

**Every store the agent connects to is simulated as a whole by default** — every tool that writes it (creates, updates,
cancels, refunds, sends, commits, escalates) AND every tool that reads it is `mocked` at its system's seam, and each
scenario seeds that store's rows. A record store's reads are mocked too; corpus/RAG tools (the map's `grounding.surfaces`
and `rag`) stay on the corpus route, unless their hits are records of a type the run simulates (`grounding.records_of`). A store runs live only when `world_state.confirmation` names it: one the harness
creates for each conversation (`harness_created`: an in-process object, a temp or run folder), or a disposable copy the
user confirmed at the store question (`disposable_copy`). **A write into a confirmed store is not a live write**: the
writer is `live` with no grant, only its outside calls mocked, typed `writes: world`, `effects: ["none"]`. Declare the
store (`world_state.owner: agent`, its types in `owned_types`, the confirmation entry, a harness `reset()`) and write the
entry's `target` into the `writes_to` / `reads_from` of every tool on that store: that name is what binds the
confirmation to the store. A write to the customer's own files in place, or to a store that outlives the run, stays a
live write.

## Step 2 — render the topology + APPROVAL GATE  (the new, defining step)

Turn the proposed split into the diagram the user approves. You do the
*judgement* (already done in step 1); the renderer does the *layout*.

### 2a — build the topology spec

> **In "use the saved setup" / "re-verify the saved bench" modes** you did NOT analyze — so read each surface's
> `mode` straight from the existing `$WORKDIR/plurai.config.yaml`
> (`mock` → mocked; `real`/`observe` → live), plus the agent / sub-agent / tool /
> skill names from that same file. Don't re-derive anything. **CARRY OVER `probes` and
> `grounding` verbatim from the existing `$ART/agent_topology.json`** — 2b rewrites that file, and
> both blocks record a Step-1 judgement this branch never makes, so a rewrite without them
> silently destroys the record. Losing `probes` also means setup probes nothing. A live tool with no `backend`
> (a map from before the fact) is the one judgement these modes make: type it from its client code (2a), and declare
> its live check as `simulation-setup` Step 8c.3 does. Then render (2b) and present as an FYI (2c, reuse branch).

**Read the agent's own inventory first** — every input it accepts and every tool it ships at its
production defaults, off its source (deterministic; the map carries every production tool):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/agent_inventory.py" extract --workdir "$WORKDIR" --entry <agent module>
```
`--entry` is the agent's own entry module (never the harness wrapper) until `plurai.config.yaml` names `agent.module`.

Write the spec to `$ART/agent_topology.json`. Group surfaces by kind and give
each a one-line `why` that states the **reason for the decision**, not just what
the surface does.

**Then initialise the bench manifest from it** — the typed file that says what this bench IS and
names the check that proves each claim. Everything downstream reads it; nothing else decides what
a scan runs.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" init
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" traces "$WORKDIR" --env-file   # a settings file the user named → harness.env_file
```

It derives the mechanical half (surfaces, run mode, corpus roots, the checks every bench gets) and
prints a `TODO` for each field it can flag. **Two more are yours anyway and nothing will stop you:
an empty `args` with `args_source: unknown`, and an empty `suffixes`, are both VALID documents.**
Fill all of these before generation, from what you just read — never from a guess:

- `surfaces.inputs[*]` — each input the inventory found: `modelled` (declare its `agent.io` part or
  channel, setup Step 8) or `waived` typed (setup Step 8). A production tool off the map needs the
  operator's answer naming its switch (`harness.capability_overrides`) or a typed
  `surfaces.production_tools.not_loaded` group (`because`: `default_off_switch` / `provider_variant` /
  `separate_surface`, citing the other route / CLI / MCP server). A world precondition is never a reason: seed
  it. An `operator_answer:<id>` waiver needs a recorded question `<id>` naming that input; the map approval waives
  no input, and never drops an `agent.io` kind or a render verb the agent ships (`input_scope_cut`).
- `surfaces.agent_context.finding` — the search that established the agent needs no turn-0 state
  from a caller or a prior flow, quoted, or `null` when it does need one (the honest `ungoverned`).
  An invented finding closes the check with a claim nobody made. `null` with no `agent_context`
  field in `plurai.config.yaml` besides the run clock is refused (`agent_context_undeclared`):
  declare the turn-0 keys there (setup Step 4b) and seed them in `seed_state`.
- `surfaces.render_actions[*].args` + `args_source` — each verb's argument contract, `code` when
  you read it and `observed` when a probe showed it. This is what a visual-correctness judge is
  graded against; `{}` with `unknown` is a declaration that nobody knows.
- `harness.provider` — the provider whose key the agent needs at run time (the NAME only; no key
  is ever read, printed or recorded). Without it the live-turn gate cannot tell a missing key from
  a broken agent, and reports the agent's own crash as a bench defect. Add `harness.env_file` (relative
  to the agent module) when the harness loads its key from a `.env` other than the module's own; a file the user
  named is already there, and the harness's env-file setting loads it. When the harness makes the model call
  itself (the agent's own route cannot run off its server), use a provider
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" env-check --providers` reports found (default
  credential files count), the agent's own first; ask only when none is found, naming where it looked. When the
  agent selects its model from an environment variable, write its name as `harness.model_env`, and write
  `provider` for the default model the code falls back to.
- `agent.smoke_inputs` (pipeline benches; without it `smoke_pipeline` is `not_run`) — a list of
  `{world: {entities: {<id>: {_entity_type, …}}}, inputs: {…}}`, one local-mock `invoke_pipeline`
  pass each (`smoke_pipeline`). **At least one world must be NON-EMPTY**, seeded with the rows the
  pipeline's work branch reads: a pipeline that skips its work on empty input goes green over `{}`.
  **A mocked dialog bench whose turn 1 opens a record by a request key declares one entry too** —
  the minimal world that key resolves in; `smoke_turn` Inits with it (its `inputs` merged over the
  planted request keys) instead of an empty store.
- `sources.suffixes` — extra file suffixes the contract must walk: a document format the agent reads that the
  checker's default set (prose and data) leaves out. Never the agent's code language (`py`, `ts`): corpus ingest
  has no reader for code and refuses a scope that holds it. Knowledge written in the code is `grounding.inline` (Step 2a).

`init` is safe to re-run: a refresh re-derives the mechanical half and keeps every field and check
the analysis authored.

**The `why` must answer "why this choice", concretely:**

- **For a `mocked` surface — name which of the four reasons applies:**
  - **test control** — output is non-deterministic, so it's pinned to a fixed
    result so assertions are repeatable (e.g. an LLM generation step). A result
    the real service computes from data the agent's request shapes (filters,
    columns, parameters, a query) is never test control's pin, even when
    randomized. Pin only what no data decides: an LLM's text, a generated id,
    the clock.
  - **save resources** — real execution is slow/heavy/costly per run (e.g. a
    long optimization loop, a large batch job).
  - **avoid side-effects** — real execution mutates the outside world or calls an
    external system we must not hit in a test (writes to prod, emails, payments).
    Mock or keep live per **external system**, not per tool: a live tool whose system is mocked
    reaches the mock through that system's seam, and the seam's config entry names it in
    `serves: [<tool>]` with `serves_cite: <path:line>` (`simulation-setup` Step 6), so the tool's
    trace facts reach that mock.
  - **isolation** — it writes a world the harness does not return to the same state before each
    conversation, so later conversations would run on earlier ones' writes. Distinct from side
    effects: the world may be a test copy and still carry over. On the user's opt-in to a live
    write, the approval question says the harness must reset that world per conversation, or
    conversations see each other's writes.
  - Then add the specifics. Good: *"pinned — LLM reply drafting is
    non-deterministic; pinned so assertions are repeatable (test control)."*
    Good: *"avoids a real charge through the payment provider; the refund lands in
    world-state instead (side-effect)."* **Bad** (rejected): "store unavailable in
    sim", "reads + writes records (Postgres)" — neither says *why it's mocked*.
- **For a `live` surface — say why it's safe + valuable to run for real:** e.g.
  *"deterministic read against the simulated store — tested faithfully"*,
  *"pure computation over two seeded records; no side-effects"*. A `why` never claims a tool is
  isolated or confined: a tool that writes to the host or reaches the network carries
  `"consent": "required"` instead, and the consent step below decides what it may do.
- **Never claim a real read sees a mocked write — not in a `why`, not in the hand-off.** Whether the agent's own
  reader (a `read_through` tool, a guard) shows what a mock wrote is the real-reader probe's verdict, and that probe
  runs when the suite builds the mocked world, after this scan. Say where the write lands (*"the write lands in the
  simulated thread folder instead"*) and, if the read-back matters, that it is checked when the suite builds the
  mocked world. Never *"so later reads see it"* or *"it sees those writes"*: a failed or missing verdict is said on
  the run's result instead.

Keep each `why` to one line. Schema (see `references/example_topology.json` for a
full worked example):

```json
{
  "agent":     {"name": "...", "framework": "...", "state": "...", "entry": "...", "action_channel": "tools|code|unknown",
                "entry_symbol": {"executes": "in_harness", "ref": "pkg.module:Qual.name"},
                "host_requirements": [{"name": "...", "kind": "executable|python_module|node_module", "evidence": "<path>:<line>", "probe": ["<name or route>", "..."], "probe_evidence": "<path>:<line>", "install": "<the docs' command>", "install_evidence": "<path>:<line>"}]},
  "rag":       [{"name": "...", "status": "live|mocked", "why": "...", "subtitle": "RAG · retrieval", "description": "<copied>"}],
  "knowledge": [{"name": "...", "status": "live|mocked", "why": "...", "subtitle": "knowledge · prompt-inlined"}],
  "tools":     [{"name": "...", "status": "live|mocked", "why": "...", "writes": "live|world|none",
                 "effects": ["shell|files|network|schedule"] or ["none"], "runs": "in_process|provider_hosted|subprocess",
                 "backend": "<service>|none", "description": "<copied>", "consent": "required"}],
  "subagents": [{"name": "...", "status": "live|mocked", "why": "...", "call": "<trail call name>|null",
                 "tools": ["<tool it calls itself>"], "inner_calls": "linked|recorded|unrecorded"}],
  "skills":    [{"name": "...", "status": "live|mocked", "why": "..."}],
  "store":     {"label": "...", "edge": "writes", "owner": "mock|agent|external"},
  "probes":    [{"name": "...", "why_eligible": "..."}],
  "grounding": {"needs_corpus": true, "surfaces": ["..."], "sources": ["..."], "records_of": {"<search tool>": "<entity type>", "<reference tool>": null}, "why": "...",
                "inline": [{"name": "<the knowledge base, in the customer's words>", "tool": "<tool>|null", "source": "<code file>::<variable>"}]}
}
```

**`store.owner`** is the `world_state.owner` you will declare for that store: `agent` when the harness runs it (a real
engine, or the agent's own in-tree implementation) and seeds and resets it per conversation. With nothing mocked, the
map's headline then names that store instead of saying nothing is mocked. A store a seam mock serves is the mock's: `mock`.

**`agent.entry_symbol`** is the function whose start means the agent's turn logic ran — a function,
never a class, a graph attribute or a `simulate.py` symbol — or `{"executes": "external", "why": "..."}`
when it runs outside the harness interpreter (HTTP, a subprocess, another language), plus `"launched_by": "harness"`
when the harness itself starts that process and hands it its environment. Name the
function the agent's production entrypoint (its server route, CLI or `main`) calls first for a turn,
never a node or branch inside it — if the harness does not call it, name it anyway: `not_witnessed`
is the finding. It lives in a module the harness imports at load; write `ref` with the module that
defines it — a re-export reads not_determinable. A background thread that calls it also counts, and
a module first imported inside the turn is not observed on that turn.

**`agent.action_channel`** is written for every Claude Code skill or plugin agent, and for no other: the
channel setup infers at its Step 7 and records in `plurai.config.yaml` (on a re-scan, read it there), or
`unknown` when it is not inferred yet. `code` means the skill runs its own code through `Bash`; the map
says from it what that shell really does, and promises no sandbox for a skill.

**`agent.tool_interception`** is `{"at": "agent_host", "why": "...", "route_smoke": "<command>"}` only
when the mocked tools are intercepted where the agent runs (its actions are rewired there to call
the mock, and `simulate.py` wraps nothing); `route_smoke` is the command that proves that route.
Leave it out when the harness intercepts them. **It is the route for an agent on its own host** — a
container, a workflow or low-code platform (an n8n instance, say), a hosted platform: `executes: external`
— whenever a store it reaches is simulated and the harness cannot launch it as its own child. The run then
drives a simulated COPY of the agent on a host the simulation owns, never the running instance (for a
container, a separate container from the same image, sharing none of the running one's volumes or network).
Every action of the copy that reaches a simulated store is rewired to call the mock — a database node
included, replaced by a call to the mock — or the action's credentials are pointed at the mock's endpoints;
an action that cannot be is named as a coverage gap, never left on the live store. `route_smoke` brings the
copy up against the deployed world and drives one call through it with `$PLURAI_ROUTE_SMOKE_SESSION` as the
copy's mock session: the suite accepts the route only when the mock recorded that call, so a copy still wired
to the live stores fails it. When the world is the mock's (`world_state.owner: mock`, the default), its `simulate.py`
exports no `reset`: the mock re-seeds its own world per scenario. An error a mocked action of the copy answered (the store refused the call) is that
action's result, handed to the agent as the real node hands it; `HarnessFault` is only for a mocked action's call that
never reached the mock. The route needs `why` and `route_smoke` both, and `world_state.user_surfaces`
and `render_actions` declared. Plan it before any store or blocker question: without it a simulated store
leaves the bench `agent_env_unreachable`.

**`agent.host_requirements`** is what the agent's own files say it needs on this host outside its
interpreter's in-process imports: executables its instructions or skills run, and modules its
shell-run scripts import (`kind`: `executable`, `python_module`, `node_module`), each with the
`path:line` that declares it (a README or install note, a dependency-file comment, a skill's
dependency line) — a line that NAMES it; `validate` refuses a citation that does not. A tool offered
as one option among others is not a requirement. It is a map fact, never an `agent_findings.md` entry.
For an `executable`, add `probe`: an invocation a line of the agent's files shows, as an argv (no shell)
headed by the program or by the agent's own wrapper (`{agent}/<path>`, with the line's own words before
it), passing that line's options and its operands — each the line's word or a small fixture you write to
`plurai_sim/host_probes/` — never the program alone; and `probe_evidence`, that line's `path:line`. The run gate runs it where the agent runs the program. A probe that fails is the finding: never remove or weaken it to pass the gate.
With no probe the gate reads `unverified`: nothing shows the program runs, and the run ships with that finding.
A module takes no probe: the gate imports it (`python -c "import x"`, `node -e "require('x')"`), and an interpreter
declared with no probe of its own is proven by a module imported through it. On any kind, add `install` and
`install_evidence` when the agent's own install docs give the command that installs it: that whole command verbatim,
and its line's `path:line` in a file under the agent's folder (`validate` refuses anything but one whole command of
that line — a pipeline whole). Never write one yourself; with none in the docs,
leave both off. While a measured gap holds, run-gate prints these lines as one copy-paste block and stops — nothing installs.

**`consent` is not yours to write:** 2b's `render_topology.py` computes it from the typed facts below
(`scripts/host_effects.py`), so list the resolved tools and leave it off. **Nor is `description`:** Step 4 copies
it from each tool's `descriptor.description` in `plurai.config.yaml`.

**`subagents[].call` is yours, on every sub-agent:** the exact name the delegation call carries on the
tool trail, read off the framework's call site (prefix included) and declared as a `tools` entry, or
`null` when the sub-agent is reached without a call (a graph edge, a router node). The renderer refuses
an entry without it. With a `call`, `tools` lists the tools the sub-agent calls itself (`[]` for none), and
`inner_calls` is written after setup's delegating smoke turn shows whether those calls reach the trail, and
whether each carries the delegation row's `call_id` as `parent_call_id` (`linked`) (`simulation-setup` Step 8, the
sub-agent rules).

**`writes` is yours, on every live tool:** `live` when it writes the customer's real system (an org,
a production database, an external service), `world` when its writes land in simulated storage — a confirmed store
included —, `none` when it writes nothing. The typing never removes a declared write: a `mode: real` tool with a
`writes_to` is live unless its store is confirmed or a seam mock `serves:` it. 2b marks a `live` writer `consent: required`, so the host-effects
question offers its grant; `bench_manifest.py validate` refuses a map whose live writers are not the config's, name for name.

**`effects` and `runs` are yours too, on every live tool — read them off the code and the vendor's
docs, never off the tool's name.** `effects` lists what the tool does beyond reading: `shell` (it runs
commands), `files` (it writes the operator's disk; a store the harness creates per conversation is not), `network` (it reaches a service beyond the agent's
own host and the mock; a call the map sends to the mock is not `network`), `schedule` (it leaves work that outlives the scenario); `["none"]`, alone, when
it does none of these. `network` is a host Step 3d would declare (the customer's own system or a third
party): the model provider the agent or a tool calls to generate text is not `network`, and neither is
the mock; `shell` is starting an OS process, and code the agent `exec`s in its own process is not. `runs` is where it executes: `in_process` (the agent's own process calls it and
the call is in the message history), `provider_hosted` (the model provider or the agent's hosting
platform executes it — a provider's web-search or code-interpreter tool, a hosted agent's org action),
`subprocess` (a built-in of the agent's CLI runtime, such as the Claude Agent SDK's; a tool the agent
serves over MCP is not one). A retrieval tool filed under `rag` carries the same three facts there.
A tool typed to act needs a grant, and a runtime's built-in needs one
unless it is on that runtime's read-only list, whatever you typed. 2b REFUSES a live tool without all
three facts, and a tool the bench runs for real that the map does not classify never runs.

**`backend` is yours too, on every live tool** (and live `rag` entry): the service its code signs in to — a database,
an API, a cloud SDK, the local stack's included, whatever `effects` says — named the same on every tool that shares
that client, or `"none"` when its code opens no client (it computes over its arguments, in-repo files or the harness
world). Each named service gets a live credential check before generation (`simulation-setup` Step 8c.3), and 2b
refuses a typed map with a live tool missing it, or `"none"` on in-process code that reaches the network.

**`grounding` — write it HERE too, from the same Step-1 analysis.** `needs_corpus` is true when
this agent owns a retrieval / vector-search / text2SQL tool, or carries prompt-injected knowledge
(a closed doc set / FAQ / guides inlined into the system prompt — `/build-rag-corpus` Step 1's
first-class no-tool case); `surfaces` names them; `why` is one line. `needs_corpus: false` is a
real answer for a purely transactional agent and carries its `why` too. Both read the MAP. **`grounding.off_map`** lists,
by name, the agent's own search/retrieval tools (production tools at their production defaults) that the map leaves off;
`surfaces` keeps its meaning. The renderer reads each one's reason from the scope proposal or the default-off switch and
renders "no conversation exercises them" in place of `why`, so the map never says the agent has no search while one exists.
**`grounding.inline`** lists knowledge written in the agent's own code: a literal whose values ARE the text the agent
answers from (a dict of help articles a read-only tool returns by key, a list of FAQ answers). One entry per literal;
`source` is the variable the literal is assigned to, inside its function or class (`agent.py::lookup_knowledge_base.kb`)
or at module level (`agent.py::FAQ_ARTICLES`), never a line range; `tool` is the read-only tool returning its values, or
null. Declare answer content only — never conduct rules or a closed code table (a status table, an error-code map),
which stay PRD rules and closed sets. Step 3c copies each into `plurai_sim/knowledge_inline/` and records that folder as
the corpus source, so the code file is never a `sources` entry or a suffix. **Never write a knowledge file by hand**, in
`plurai_sim/` or anywhere a source points: it is nobody's copy of the agent, and nothing checks it. A literal the copy
refuses (built by code, such as an f-string or `dedent()`, or a TypeScript/JavaScript object) stays refused: the summary
says so in the refusal's words, and the suite has no knowledge base for it.

It belongs at 2a and not later because the map is what the user APPROVES: a verdict written after
the gate is a verdict nobody reviewed, and `/simulation-generate-suite` acts on it. The renderer
REFUSES a half-written block (a missing or stringified `needs_corpus`), so a bad one fails here,
before the gate, while you still have the code in front of you.

**`grounding.sources` — the files or directories those surfaces read from, relative to the agent
module directory, when `needs_corpus` is true.** Typed paths, not the prose labels `surfaces` carries: they are what
Step 3c hashes and verifies the PRD's typed facts against, and the ONE scope `/build-rag-corpus`
ingests. Name exactly what the agent loads: when it loads one file of a shared directory, the
source is that file. Each must have a distinct basename. `grounding.endpoint_env` lists the endpoint variables of the
index those surfaces search (a vector store's URL), so the harness leaves them unpinned; with more than one, `grounding.endpoint_tools`
maps each to the surfaces that read through it (`{"<VAR>": ["<tool>", …]}`), so its store question names only those. `grounding.records_of` types what
each surface returns: `{<tool>: <type>}` when each hit is a row of a `world_state.entity_types` type (it carries that row's
identity: contracts, orders), `null` when no world type holds it (a playbook, regulations, runbooks; a reference row merely
keyed by a world id is still `null`). `null` only for reference content; a surface whose hits are rows of a declared type names that type. A search over a type the run simulates is not a surface: map it `mocked` with that
store, served from the seeded rows of the type that carries the text it searches (`index_over_simulated_records`); when that type's rows are copied from a file the agent only reads, the config names it (`reference_rows:`, `/simulation-setup` Step 8). That declared file is grounding for the type's seeded rows (every world's rows of that type are copied from it), not a retrieval surface: never a corpus part, a `.knowledge_bases` entry or a `.scan_corpus_roots` line. Omit these keys when `needs_corpus` is false; a
corpus-needing agent with none recorded renders on the map as an explicit gap, because the
contract then has nothing to check.

**`probes` — write it HERE, from the Step-1 analysis you just did.** One entry per read-only
surface whose data is local (a fixture store, a bundled JSON/CSV catalog, a local index) or whose
external edge the map mocks — never a writer, never a surface the map runs against a live external
system — each with a one-line `why_eligible` in the same style as a surface `why`. This is your judgement, made where
you have the code in front of you; writing it down is what stops it being made again, from
memory, at the gate below. The renderer lists them on the map, so a wrong call is visible and correctable at the
approval gate rather than invisible.

**Include the read-only POST-TOOL and derived nodes** — formatters, rankers, enrichers that run
on a tool's result — not just the tools themselves. Their contracts are otherwise written from
code alone, and an unexercised derived node is exactly where a spec goes wrong in several places
at once. An agent with no eligible surface gets `"probes": []`, which is a real answer.

Every section except `agent` is optional. `status` must be exactly `"live"` or
`"mocked"` and must match the `mode` you proposed in step 1
(`mock` → `mocked`; `real`/`observe` → `live`). A tool whose external call goes to the mock
(`mode: mock` at any `seam`: `transport`, `client`, `entity` or `tool_body`) is `mocked` even when
its own code runs: it never carries `network` and never reaches the host-effects question.

Prompt-inlined knowledge (no retrieval tool) is still a surface the map must show —
an agent whose knowledge rides in its production system prompt has a knowledge base
under test even though nothing retrieves at run time. It goes in `knowledge`, its own
kind: `{"name": "<kb name>", "status": "live", "why": "prompt-inlined — knowledge rides
in the production system prompt", "subtitle": "knowledge · prompt-inlined"}`. It shares
the diagram's grounding band with `rag` (both ground the agent's answers) but the report
names it `knowledge`, not `rag`. Do NOT file it under `rag` with a hand-written subtitle
— that was the workaround before this kind existed, and it rendered prompt-inlined docs
under a RAG heading as `(rag)`, sending the reader looking for a retrieval tool that does
not exist. (Older `agent_topology.json` files still carrying the workaround keep
rendering; the renderer accepts both.)

**Keep `agent.framework` short and legible** — it's the small line under the
agent name. Put the runtime FIRST (e.g. `"LangGraph"` or `"LangGraph · ReAct"`; the part before
any `·` is read as the runtime, which decides what confines an ungranted tool),
and the state in `agent.state`. Don't cram the full agent-construction call into
it ("LangChain v1 create_agent (ReAct) · LangGraph · AgentState" is too
long and unclear). The renderer auto-shrinks an over-long subtitle, but a concise
one reads best.

### 2b — render

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $ART / $WORKDIR — re-source in THIS block
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/scripts/render_topology.py" \
  "$ART/agent_topology.json" --out-dir "$WORKDIR"
```

Stdlib-only (no deps). It writes `$ART/agent_topology.md` (the report — one
summary line + the diagram + the per-surface "why each surface" rationale) and
`$ART/agent_topology.svg` (the GitHub-dark map: blue agent at center, green
run-for-real nodes, dashed-orange mocked nodes). It also re-writes the
canonical `$ART/agent_topology.json`.

**Progress fill `scan-map`** — the local overview page shows the map while the setup is built. It never ends the
turn, and it fills only inside a /simulate run (run on its own, it skips):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step scan-map --flowdir "$FLOWDIR"
```

A milestone never publishes: publish nothing and run no `done`, whatever the line says. Then run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" "$WORKDIR" --flowdir "$FLOWDIR"`
and say what it prints, verbatim (report doctrine, item 1). When `skip` is `internal_text`, rewrite each line `internal_text`
names (a map line: in `$ART/agent_topology.json`, then render again), and fill again once. Ignore every other field; a
failed fill moves on: no retry, no word — the next fill catches the page up.

### 2c — present (gate ONLY when generating)

The rendered report **is** the thing to review — don't re-narrate it in chat. Keep
the message to ≤3 lines, link last, plain language, no pasted lists.

**Print the link every report this skill's hand-offs carry** — the map here,
`$ART/pre_generation_findings.md` in the Step-5 summary when it is linked. Never type the path, which
opens on no surface the reader has; paste the line the resolver prints, verbatim (the file's own
link: its path, or a `file://` URL on Copilot and Codex). The overview page is the only published
page, so publish no report here and `open` nothing; during a running /simulate run the line prints
nothing, because the overview page carries the map (2b's progress fill):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # the shell resets between calls
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Open the agent map →" "$ART/agent_topology.md"
```

**Branch on the Step-1 choice:**

**A. "Use the saved setup" / "Re-verify the saved bench" — NO approval question.** Nothing is being
generated, so don't gate. Show the map as an FYI and continue straight to Step 4:

> Using your saved setup — 🟢 N run for real · 🟡 M mocked.
> &lt;the agent-map link line `report_link.py link` printed&gt;
> Finishing up now…

Then go to **Step 4**. (Do **not** ask the user to approve, and do **not** run Step 3.)

**B. Re-analyze / focus / no prior setup — approval gate.** You are about to generate
files, so the user must approve the split first:

> Mapped your agent — 🟢 N run for real · 🟡 M mocked.
> Open the map to review before I build anything:
> &lt;the agent-map link line `report_link.py link` printed&gt;

Then **immediately ask the approval question** (the ask gate — sanctioned:
first run `cd "<agent-folder>"; bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction topology-approval`) — no
paragraphs between the link and the question. If the user wants a surface flipped
(mock↔real) or a rationale changed, update `$ART/agent_topology.json`, re-run 2b, and
re-present. **Do not proceed to Step 3 until the user approves.** This split is what
every downstream artifact (`plurai.config.yaml`, the deployed mock, the eval
namespaces) is built on — approving the map is approving the contract, including the
local verification setup runs against the agent's code (the harness smoke and the read-only
probes).

**What the simulation leaves out rides the same question, by name.** Before sanctioning, run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scope_proposal.py" write --workdir "$WORKDIR" --input <name>… --switch <name>… --tool <name>…`
for every input you propose not to model and every switch you propose to leave off (with its tools; a setting the
inventory does not list: name its tools with `--tool`), and fold the sentence it prints into the question. Only the
items it names are backed by the approval; a content part (text, image, file, upload) is refused there and stays
modelled. Write it before the sanction even when the gate takes its default:
the default backs exactly those items. No other gate's answer backs a cut — never move one onto `blocker`.

**What the simulated user sees rides the same question.** Fold one sentence naming the surfaces
the analysis found for `world_state.user_surfaces` into the topology-approval question (e.g. "the
simulated user sees the Transactions view and receives text messages"), so the customer reviews that
declaration here; never a separate ask.

**So does a live write the harness does not reset per conversation** (a question batched into the same
call). When the map keeps a tool `writes: live` and nothing returns its world to the same state before each
conversation, the question recommends **Mock the writing systems**; the other options are **The harness
resets them per conversation** (setup writes `reset()`) and **Keep live: conversations will see each other's
writes**. Keep-live is the user's explicit opt-in: setup records `world_state.shared_world_ok: true` in
`plurai.config.yaml`, and the run proceeds with the named risk `shared_world`, capped at qualified. Under
`run_driver: headless` the writes stay mocked.

**So do the harness declarations (`simulation-setup` Steps 2 and 8).** Name the bench runner the
setup will declare as `harness.entry_wrapper`, if any, and list every run setting where the harness
will pass something other than the driven entry's own default, with both values and the cited
line. Approving the map approves both. Under `run_driver: headless` print them to the run log.

**Tool probes need no consent, so ask nothing about them.** The `probes` Step 2a recorded call
only local or mocked surfaces, and the runner runs every probe with no network
(`bench_manifest.py`; a machine with no network sandbox leaves the probe unrun and its claims
unprobed). Write no `.probe_consent`. An earlier scan's `declined` stays as it is: setup then
runs no probe, and nothing asks again.

**Under `run_driver: headless` the declared default for `topology-approval` is `approve-no-probe`**:
the map stands as presented and no consent-gated probe runs. A `/simulate` run takes the same
default. In a `/simulate` run the gate is the call alone, so print the map line — sanctioned progress lines — once the
call is answered without asking and the map holds what that answer leaves (each ungranted tool or store mocked, 2b
re-run), before Step 3: `Mapped your agent — 🟢 N run for real · 🟡 M mocked.`, counted from that map, then the line
this prints (the overview page's Scan stage):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # the shell resets between calls
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Open the agent map →" "$ART/report.html" --anchor scan
```

The map line says nothing about approval or the default; when the link prints nothing, the count line goes alone,
never a typed path. It prints once per scan: a repeat of this gate in the same scan prints nothing.

**Every question below MUST ride the same ask CALL as the gate above.** The one-shot sanction
authorizes one CALL, not one question — ask one separately and the guard DENIES it, and a
denial is not a stop, so the scan proceeds without that answer and nothing says so.
Both branches have exactly one call to batch into: `topology-approval` here, and
`saved-setup-gate` in the reuse branch (Step 1), since a saved-setup scan never reaches this gate.

**Settings and keys — check names, never ask about ones that resolve.** Before any question about a setting,
key or credential the harness needs, run `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" env-check <NAMES>`
(names only). Ask only for the names it reports missing, naming where it looked (its `looked in:`
line), and offer to read them from another file the user names (record a path with
`simulate_plan.py traces "$WORKDIR" --env-file <path>`). Never ask about, or offer to create, a file when every
name is found. Found means the run reads it; never read a value out of a file to pass it on. When
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" state "$WORKDIR"` prints a non-null `env_prompt` (a
project never scanned whose start did not answer it) and this call holds fewer than four questions, it rides the
call verbatim; record the answer with
`traces "$WORKDIR" --env-file default|<path>`; unanswered records nothing.

**Calls sent to the LIVE AGENT are a separate class with their own consent:**
`plurai_sim/.live_probe_consent` (the scan-time asks the contract SENDS TO THE LIVE AGENT;
`scan_contract.sh` forwards it as `--probe-consent`, with the command to send them in
`plurai_sim/.agent_command`), three states: `granted` · `declined` · `unasked`. If you ask for
it, ask explicitly and by name, batched into the same call. Never write it from any other answer.

**When the `/simulate` start answered org access, this question is not asked, on a re-scan too.** The line
`topology-approval` prints then says what the answer granted and what it left out: never ask it on another gate,
including `blocker`, never write `.host_effects_consent`, and the re-ask below does not apply. An item left out stays off. A
rewrite that refuses over an ungranted action the bundle still mentions (`--plan`, `--write-lanes`) is no stop and no reason
to ask here: carry on.

**Host-effects consent — ONE single-select, batched into the same call, on the same terms.** One
question grants tools and programs together. Read the `tools` entries marked `"consent": "required"`
from `$ART/agent_topology.json`; the items it names are:
- each of those tools;
- the item `route_smoke` when the map declares `agent.tool_interception` at `agent_host` (bringing
  the rewired copy up deploys and activates it in the customer's system);
- on a hosted Agentforce agent, the org actions from the rewrite's plan, not only the map: run
  `python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/rewire_agentforce.py" --project-dir <agent root> --plan`
  (it reads the bundle alone and writes nothing) and add every name under `denied (N)`;
- on a hosted Agentforce agent that takes a document from the user, the item `file_uploads`
  ("each document a scenario hands your agent is uploaded into your org as a file, shared with your
  agent's user, and deleted when that conversation ends: Salesforce keeps a deleted file in your org's
  Recycle Bin for 15 days"); ungranted, a scenario that hands one over stops before anything reaches the org;
- each program the map's `agent.host_requirements` declares `kind: executable`, offered as
  "<program> outside the Bash sandbox" — only when the agent runs Claude Code's `Bash` tool (it is
  on the map, and `agent_topology.md` says ungranted ones run sandboxed) and `.host_effects_consent`
  does not already grant `Bash`. Leave out a shell or a program that runs other commands (`bash`,
  `sh`, `env`, `xargs`, `sudo`, … — `scripts/host_effects.py` `UNGRANTABLE_PROGRAMS`; never grantable).

Non-empty ⇒ ASK, naming every item: "May these run for real on this machine during scored runs (install packages, write
outside the scenario folder, reach the network)?" Options: "Grant all N (Recommended)" — covering every item but a
`writes: live` tool, each of which is its own item naming the exact target it writes (the variable, its value, file:line) —
and "Grant none", whose description is what
happens to unselected ones — the "unless you grant them" clause
`agent_topology.md` renders for them, never a sandbox it does not name; a partial grant is the free-text answer naming its items. Where the map says they run confined (an agent its harness runs under `plurai.confine`; never a
skill), that is: "Unselected ones run confined: file edits stay
in the scenario folder, `Bash` runs sandboxed, writing only there and in Claude Code's own temp and
log folders, with its network open as in your terminal (`PLURAI_SANDBOX=strict` closes it; switched
off on a machine with no sandbox), the rest are switched off." For a skill the map says something else; repeat what it says and promise no sandbox. Where it
says they run unconfined (a `code`-channel skill): "This skill runs its own code through them, granted
or not, and nothing sandboxes them: each scenario starts in a scratch folder outside your repo, the
vendor calls the map mocks go to the mock, and your vendor keys are kept out of their environment, but
they read, write and reach the network as you do." When programs are listed: "May these programs run outside the Bash sandbox? A granted program
runs with full read, write and network access to this machine, with any arguments, and a Bash
command leaves the sandbox only when every command in it is a granted program — `cd dir && <program>
…`, or a script that starts the program itself, stays sandboxed. Granting an interpreter (`python3`,
`node`, `ruby`, `perl`) frees every script it runs, and many programs run any command through their own
arguments (`find -exec`, `git -c`, `awk`, a converter's macros or filters): a grant covers whatever its
arguments make it run." When `route_smoke` is listed: "Granting
`route_smoke` deploys a copy of your agent where it runs (on Agentforce, into your org) and activates
it, so the simulation can drive it; the running instance is never touched, `down` stops that copy and
`clean` deletes it." Name the system a `writes: live`
tool would write — "…, or write to <system> for real?" — and it is not callable unless selected.
For the org actions: "May these org actions run for real during simulations? They run in your
Salesforce org, not in the mock: <names>. Unselected ones are removed from the simulated copy." A
left-real action is denied unless its target is known to only read, whatever the map types it.

Record the one answer in one grammar — asked and allowed for exactly these names, asked and refused,
or never asked. Record the answer to `plurai_sim/.host_effects_consent` for the tools and items:
`granted: Bash, Write` · `declined` · `unasked`. Record the answer in the manifest's `consent.unsandboxed` for the
programs: `{"state": "granted", "names": [...], "asked_at": "<now>"}` · `{"state": "declined"}` ·
`{"state": "unasked"}` (no dotfile; `bench_manifest.py init` keeps it across a re-scan, `validate`
refuses a name that is not a declared `kind: executable`, or that is ungrantable, and the operator
may edit it by hand). For both records: Headless or unanswered is `unasked`, and only `granted` grants
anything, so the default is deny and the run still goes ahead. Reuse the file on a re-scan, and the
`consent.unsandboxed` answer, unless one says `unasked` and its list is now non-empty, or `--plan`
lists under `denied (N)` a name its `granted:` line does not cover: then ask again, naming only those names, and write back the prior
grants plus the new answer. An earlier answer never covers an action it was not asked about, and an
unasked denial silently removes a step from every scenario that needs it. The harness reads them;
see `${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/references/claude-agent-sdk-surface.md`.

**Store question — one per existing store the agent reads or writes** (corpora and harness-created stores aside),
batched into the same call; default No, and headless or unanswered is No: "Your agent connects to **`<target>`** (through
`<VAR>`, `<file:line>`). By default the simulation never touches it: every read and write goes to a simulated copy that
each scenario seeds with its own data. Run it LIVE only if `<target>` is a disposable copy that may be written to and wiped
before every conversation. Is it a disposable copy?" Options: "No — simulate it (Recommended)" / "Yes — run it live". A Yes
is recorded as `world_state.confirmation: [{answer: disposable_copy, target: <target>}]`, written only from the user's answer
to that store's own question, never inferred (a map approval, a blocker answer or the stack's description confirms no store;
a question naming several stores offers only "Yes — run them live", for all of them; a partial answer, "run some live" or a
picked store, backs none, so ask each picked store its own question;
`verify_config.py` refuses an entry no recorded Yes backs); a store the harness creates per
conversation is recorded with no question as `{answer: harness_created, target: "in-process: <object>" | "mkdtemp: <prefix>"
| "run-dir: <path>"}`. The files the agent itself ships and only reads (its code, skills, prompts, packaged assets) are
recorded with no question as `{answer: agent_bundled, target: "bundled: <path>"}`, that path the
whole `reads_from` of each tool reading only them (a tool that also reads the harness's in-process store lists both,
`;`-separated): their reads run real; a write to them stays a live write. A folder the agent reads as records or
documents (a knowledge base, a `grounding.sources` folder), even one committed beside its code, is data: never bundled.
**A vendor whose double the agent's own repo ships is served by that double, not a seeded mock, when the double COMPUTES
its answers** from the request and data files the agent ships (a carrier fake that rates from the contract tables; never
one that returns canned or echoed responses): a seeded row cannot re-derive what the real vendor would. Recorded with no
question as `{answer: agent_fake, target: "fake: <its entry .py>", env: <the vendor's endpoint variable>, computes:
"<file>:<line>", data_env: <the variable naming the folder it loads inputs from>, inputs: {<file>: <shipped path> |
{world: <type>, shipped: <the shipped file of that type's rows>}}, state_env: [<each variable naming where it keeps
state>]}`; the vendor then has no `tools:` mock, the types only the double holds leave `world_state.entity_types` (their
evals and regressions are regenerated on that schema), and the harness serves it per conversation (simulation-setup-python
Step 8.5). Only a Python ASGI double that the agent's code never reads records back from, and whose writer also leaves a
world-visible effect, qualifies; any other stays mocked, and `verify_config.py` refuses an entry whose double reads a
variable the helper does not set, holds a fixed path or imports a store, network or process client.
A store whose endpoint also serves read-only reference content the agent reads live (its variable in
`grounding.endpoint_env`: a knowledge index, runbooks, code sets, templates) is still asked, through that `<VAR>`; on a No
those reads stay live, and its question says so in place of "never touches it", naming each tool that reads it live as a
code span (`live_read_unnamed`);
an index over the records that store holds is not reference content.
A re-scan reuses an entry `verify_config.py` finds backed and asks about a store with none or one it refuses. **A grant is not a confirmation:** a No for a store sets every tool on it `mocked`, whatever any tool grant says.
For an agent on its own host, a No is served by the simulated copy (`agent.tool_interception`, Step 2a): no blocker
then offers running that store live in its place. **The simulate option is always the recommended one** — on this
question, on any re-ask of it and on any blocker about a store; a live option is never marked (Recommended), and a Yes
given to a question that recommended live confirms nothing (`verify_config.py` refuses it).

**An agent with no Bash sandbox is not asked.** An agent that does not run Claude Code's `Bash` tool
runs its programs as its own code runs them: nothing plurai applies confines them, whatever the
answer. Record `{"state": "unasked"}`, and say it in the topology-approval question in one sentence:
"The programs your agent runs (<the executables>) run directly on this machine; no sandbox confines them."

**Where the map says unselected ones are replaced by the mock, make it so before Step 3:** nothing on
that runtime confines a live tool, so set each unselected one `"status": "mocked"` on the map (its
`why`: avoid side-effects, not granted) and re-run 2b. A writer into a confirmed store is never on that
list: it needs no grant, so it is neither asked about nor mocked for want of one. `verify_config.py` refuses one left real and
ungranted (`host_effect_ungranted`); a tool that cannot be mocked (a provider-hosted one) needs the
grant or its removal from the agent's options.

**An install or code generation you improvise in the customer's repo is a host write** (`npm ci`, `pip install`, `prisma generate`, starting or seeding its services; a prerequisite a plugin step documents is not one): run it only on the operator's yes, asked as in error-posture item 2 under the `blocker` gate, naming the command and where it writes; headless it does not run, the hand-off names the command, and a check that cannot run without it records `not_run` while the scan continues. The bridge runtime install (bridge skill B4, `bridge_preflight.py "$WORKDIR" --install`) is the plugin's own prerequisite under `plurai_sim/`: run it, headless included; it is not an install in the customer's code.
A local service is up when the endpoint its config or compose file declares accepts a connection, never judged from a container listing; starting or seeding one is offered only when that connection is refused, and seeding is named as overwriting its data.

## Step 3 — generate the harness (setup-simulation, GENERATION)

**PRD facts contract.** (1) Write the PRD without an `## Agent facts` block and run `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scaffold_rule_facts.py --profile artifacts/data_profile.md` before you type any fact: it adds the block and writes the `instruction_sources`, one `rule` fact per instruction, a line or the lines one wrapped sentence spans (`subject` = `source` = `path:LO-HI`, `evidence` = the agent's own bytes), the quoted block, and the measured facts the profiled files settle (closed URL sets, URLs outside them, topics no document covers); confirm or drop each. `--source` is the text the model is given as instructions: prompts, task and agent configs, each skill or rules file (`.claude/skills/*/SKILL.md` for a Claude Agent SDK agent), each live sub-agent's own prompt, and what fills each template `{slot}`: follow it to the code, declare the file or range that fills it with instructions, decline a data slot. The lint reports a loaded file you neither declared nor declined. Never one fact per file, never a paraphrase, never a slug for a subject. (2) Add only facts you measured: `kind`, `subject`, `predicate`, `evidence` (a number or the quoted bytes), `source`; cite code as repo-root-relative `path:LINE` or mark the claim `(inferred)`. (3) Run `lint_prd.py` until it exits `0`: exit `4` is open work (a measured fact you never typed counts), and a finding stays only with `--accept <fp> --reason`. (4) Write the prose from the agent's own instructions and code, never as a summary and never by copying a corpus document (the verbatim appendix carries those; summarise a corpus procedure in one line with its `path:LO-HI` citation): it states the agent's hard limits (context and history handling, how the prompt is built, model and env defaults, size caps), every closed vocabulary or catalog the agent stays inside with all its members, its absences (what it cannot do), the procedures its own instructions state as numbered steps, and two or three example exchanges that show the expected reply shape; `lint_prd.py` prints a hint, not a finding, when the prose shows too few of these or repeats appendix lines. The worked block and the required prose content are in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/references/prd_authoring_contract.md`.

**Skip this entire step in "use the saved setup" and "re-verify the saved bench" modes** — the harness already exists;
go straight to Step 4.

**Re-analyze regenerates `plurai.config.yaml`, `simulation_prd.md` and `simulate.py` even when the
approved split equals the saved one** — the saved files are inputs to setup's rescan diff, never a
substitute for this step. Read setup Steps 8–10 and the language sub-skill to their ends before
writing anything.

**The PRD leaves this step lint-clean.** `lint_prd.py` exits `4` while a finding is open, and the manifest then
records `lint_prd` as `not_verified` and prints `PRD lint: N open` in the integrity line. Fix every finding and
re-run, or accept the ones you verified do not apply with a recorded reason (setup Step 9, "Then lint the draft").
A finding is never dismissed as advisory.

Once approved, resume **setup-simulation** from its generation phase: write
`$WORKDIR/plurai.config.yaml` (its tool modes must match the approved topology),
`simulate.py` (plus `bridge/` on the bridge lane), and — only when at least one tool is `mode: mock` —
`test_mock_contract.py` (a tool-less / all-real agent has no backend mock to accept, so
the file is skipped), then run / iterate until `python simulate.py` completes cleanly.
(Still `--no-generate-evals`. Setup's Step 11 then extracts the scenario variables: say so in product
words ("mapping the kinds of users and situations to test"), never by file name; run that skill's Step 5
verify until it passes, and skip its Step 6 report and Step 7 pre-warm, because this command's hand-off
is the report and the suite starts the pre-warm.)

**Then record the native tools** — the live tools on the map no plurai lane wraps, computed from
it and written to `agent.native_tools` by the script, never typed — and the agent's own fixed error
replies (`agent.error_replies`, read off its source):

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_native_tools.py" \
  --config "$WORKDIR/plurai.config.yaml" --topology "$ART/agent_topology.json"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_error_replies.py" --workdir "$WORKDIR"
```

**Progress fill `scan-checks`** — 2b's progress fill with `--step scan-checks`, except that a skip of any
kind moves on with no rewrite: the next fill catches the page up.

**Then validate the manifest against what generation just wrote — before Step 3c spends anything:**

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" validate
```

It refuses a `TODO` the analysis never answered, a declared check whose script is missing, and —
the one that is easy to miss — a manifest and a `plurai.config.yaml` that name different
surfaces. Two files disagreeing about what runs for real is a defect that only ever surfaced as a
scoring anomaly weeks later. Fix what it names; do not proceed with findings outstanding. An agent
that runs in the harness needs its live tool list recorded first (setup Step 9f.4 `record-live`).

- **Prompt-inlined / enumerable knowledge → the PRD carries a VERBATIM SOURCE APPENDIX**
  (setup Step 9b). When setup detected that shape, the generated `simulation_prd.md` ends
  with the byte-exact appendix, and its `(COMPLETE)` token asserts the enumerated files are
  the WHOLE knowledge base — which licenses judges to say "the sources do not mention X".
  Confirm that closed-set claim with the user as part of the EXISTING Step-2c approval
  interaction (fold one sentence into the topology-approval question — e.g. "the PRD
  registers `faq.md` + `refund_policy.md` as the complete knowledge base; is anything
  missing?"); do not add a separate ask. If the user cannot confirm the set is closed,
  drop `(COMPLETE)` from the appendix heading before proceeding.

### Step 3b — upload the PRD draft to the backend

In the "use the saved setup" and "re-verify the saved bench" modes (which skip Step 3), run the `scan-checks`
progress fill (Step 3) here first; in every other mode Step 3 already ran it.

The PRD now exists in its Step-3 form, and the generation skills pass its id to the backend
explicitly (box-side discovery would take the newest PRD the box holds — another agent's, on a
shared box). Stash it now, from disk (zero context transit), so the id exists before those calls:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # same call — the shell resets between them
# The marker goes first, so no path below (a refusal included) leaves an earlier PRD's id behind.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .prd_upload_id
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require stash-doc)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# No runner is a failed stash, not a failed scan.
[ -n "$PLURAI_BIN" ] || { echo "MISSING runner plurai stash-doc"; echo "PRD_ID=STASH_FAILED"; exit 0; }
# The stash uploads the PRD; the guard checks the backend before it runs.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || { echo "PRD_ID=STASH_FAILED"; exit 0; }
# --out, not $(…): the id goes to the FILE and the diagnostics to stderr, so no redirection a
# caller adds can fold an error message into the marker. A failed stash removes the marker itself.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$PLURAI_DIR/simulation_prd.md" simulation_prd \
  --out "$PLURAI_DIR/.prd_upload_id" || { [ $? -eq 7 ] && exit 7; }
echo "PRD_ID=$(cat "$PLURAI_DIR/.prd_upload_id" 2>/dev/null || echo STASH_FAILED)"
```

- **This stash is pre-authorized by the scan invocation — never ask consent for it.** There is
  no upload gate in this flow or downstream (`/simulation-generate-suite`'s grounding gate
  decides corpus BUILDING, not this stash), so a consent question here has no gate to ride and
  stalls the flow on its own step. Disclosure is the `PRD_ID=` line above and the scan summary —
  a statement, not a question.
- The label `simulation_prd` is load-bearing — it is how the backend recognizes the doc as
  a PRD among everything else in the stash. Do not vary it.
- **If the stash prints `stash_doc: WARNING:` lines, act on them and tell the user in
  product terms** — they are the backend's own account of a document that will not survive
  downstream intact, and this is the one moment the fix is cheap (the same defect otherwise
  surfaces ~35 minutes into a generation job as scenarios failing on facts that ARE in the
  PRD). Say what to fix in product terms; do not paste the raw backend text. The
  `stash_doc: grounding blocks —` line is informational — how the backend split the document
  into grounding blocks, and how big each one came out.
- **If the stash fails, continue the scan** (except on the version refusal above) — including `MISSING runner plurai stash-doc`
  (the runner is absent or too old) and a guard refusal (`SPLIT BACKENDS`, or `refusing
  unauthenticated remote calls`): print one line saying the upload will be retried by the later
  generation skills and move on. The marker removal at the top already cleared any stale marker — a
  leftover id would silently resolve to an OLDER PRD, which is worse than no marker.
- **The backend's refusal of this plugin or its SDK (the block exits 7 and prints the update text, no `PRD_ID=`
  line) ends the scan here.** Say that text verbatim and alone, the way the error posture says a version refusal, and
  run nothing after it: every later call is refused too.
- **A pin refusal (`client_pin: REFUSED` on stderr, no `PRD_ID=` line, the call exits 1) is a
  failed stash too, and the scan continues.** The marker is already gone. Add one line to the
  scan summary, in the refusal's own words: the client pinned for this workdir refuses this
  runner, so the generation skills stop until the pin or the runner changes. The degrade is benign: downstream skills stash for themselves (or fall
  back to inline text).
  Never fail the scan over this.
- This is the **initial draft**: `/build-rag-corpus` Step 6.5 reconciles the PRD against the
  corpus and later skills re-stash from disk before their own calls, so this upload is
  superseded automatically afterwards.
- **Run this in every mode** (including the two where the rest of Step 3 is skipped): the
  saved `simulation_prd.md` exists on disk, but a fresh or cleaned box holds nothing —
  and re-stashing unchanged text is a free no-op returning the same id.

### Step 3c — execute the scan's conclusions (the pre-generation contract)

The scan's conclusions are prose and config until something runs them. This step runs them —
against the corpus they were drawn from, while a wrong one still costs a re-read instead of a
qualification loop. **Run it in BOTH modes**; skip it only when `grounding.needs_corpus` is
false, and say so in the summary rather than omitting the line.

**First, in every mode and before the skip rule, copy the knowledge written in the code** (`grounding.inline`). The
copy reads the literal by its name and writes `plurai_sim/knowledge_inline/<name>.md` (one `#` title, one `##` per
article, each article's exact text), records the folder in `grounding.sources` and the manifest, and stamps
`.corpus_scope`; a declaration that is gone or refused takes its copy with it. It runs under `$PLURAI_PY`, the agent's
interpreter, never a bare `python3`, which may be too old to parse the agent's code:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/extract_inline_knowledge.py" --workdir "$WORKDIR" --when scan
```

Each `warning:` line goes into the scan summary as is (a refused literal; a knowledge file in the test folder no
script copied; an article the chunker splits). A `fix:` line is for you, never the summary. Never answer either by
writing or editing a knowledge file.

**Run it through the bench manifest, which is also what runs every other check.** The contract's
flags are no longer typed here: `--corpus`, `--suffixes` and `--stateless-finding` are read from
the manifest's `sources` and `surfaces.agent_context.finding`, so a forgotten flag is a
validation finding instead of a scan that quietly decided every fact over the wrong corpus.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode <the Step-1 mode>
echo "VERIFY_EXIT=$?"
```

It runs the verifiers, both smokes, the surface assertion, the probes (with no network) and this contract
— each `run`, `reused`, `not_run` or `not_applicable` by the manifest's own policy, never by a
judgement made again here. (The egress sweep is still Step 3d's own call: `kind: egress` exists in
the schema, no builtin runs it yet.) It writes `$ART/verification.json` and prints the
run-integrity line Step 5 must carry verbatim.

| `VERIFY_EXIT` | meaning | what you do |
|---|---|---|
| `0` | no blocking check failed or was blocked — verified, reused, or not applicable here | continue; the line names anything that did not run |
| `1` | a blocking check FAILED, or the bench is NOT RUNNABLE (its `run-gate:` line names the check and the one fix) — something still holds. A failure the run measures (the agent raised, a turn came back silent) is not one | each `finding:` line is a gate finding you decide on (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`): repair what it names, or overrule an advisory one, and re-run. A failed `live_*` check is the credential ask of `simulation-setup` Step 8c.3. An ungranted route smoke is the operator's grant: lead the summary with it |
| `2` | the manifest is invalid — NOTHING ran | fix what stderr names (a `TODO` the analysis never answered, a declared check whose script is missing, a surface the config and the manifest spell differently) and re-run |
| `3` | a blocking check could NOT RUN | the reason names a prerequisite, not a defect — `node`, a built `dist/`, the contract checker, the agent's provider key. Hand the operator the one command that installs it (nothing installs here), or say plainly in the hand-off that this bench was never gated. The bridge runtime (`bridge_runtime_*`, "the bridge runtime is not installed") is not such a prerequisite: it is the plugin's own B4 step — run `python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/bridge_preflight.py" "$WORKDIR" --install` and re-run. On a bench that declares `harness.tool_interception`, the reason names its route smoke instead: that is expected, and the suite runs it |
| `4` | a blocking check is NOT VERIFIED (`not_verified`) — it ran and refused the bench's OWN inputs; for the contract, its exit 2 (a PRD it cannot project) | an input to fix, never a prerequisite: follow the `CONTRACT_EXIT` `2` row below with the evidence the line quotes, then re-run |

The 1/3/4 split is the error posture's: a failure has findings to read, a not-run has something to
install, a not-verified has an input to fix, and telling an operator the wrong one sends them to
the wrong place for an afternoon.

The contract's own verdict is one row of that table's output. Read
`$ART/pre_generation_findings.md` for it, exactly as below.

It projects the PRD's typed facts, verifies them against the corpus, compiles the corpus's URL
allowlist (placeholder templates kept whole), hashes the sources beside `.corpus_id`, and writes
ONE report — `$ART/pre_generation_findings.md` — carrying a PROCEED / FIX-FIRST signal.

`--source-root` is what makes the CHEAP half of a PRD's defects findable here rather than four
hours later: every fact's `source:` is resolved against that tree, and a `path.py:LINE` citation
has to land on a line carrying the fact's own subject. A citation eleven lines off resolves
perfectly against a file that exists, and the claim standing on it is read as a contract by the
generator and by every judge. Pass the agent module you just `cd`'d into — the script adds
`$WORKDIR` alongside it, because `artifacts/…` and `plurai.config.yaml` are documented `source:`
values and live there rather than under the module. Omit the flag entirely and the check reports
itself blocked — the citations are unchecked, not clean.

Every `## Agent facts` entry is projected
(`${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/references/agent_facts.md`), but only the ones
carrying an executable `check:` are DECIDED. The rest land as **asserted, unverified — no static
route to check this**, and the report's headline says so: a PROCEED over them is PROVISIONAL, not
a clean bill. The same names are printed on stderr. **Relay that count and lead with it** — those
are the premises the whole build is about to stand on untested.

The script also cross-references the scan's own outputs, by workdir convention:
`scenario_state_schema.yaml` decides whether `agent_context` is GOVERNED.

**`$STATELESS_FINDING` is the input to that check a SCAN can supply, so fill it whenever
you can.** The state schema is an env-gen OUTPUT written at `/simulation-generate-suite` Step 0c,
*after* this step, and nothing re-runs the contract afterwards — so on the normal path
`agent_context_governance` is permanently blocked and the report says UNGOVERNED, *not*
verified-empty (which is how a suite shipped with an empty precondition on all seven scenarios).
The finding closes it, and Step 1b's analysis is where the answer already is. Establish it, do not
assume it — for a Python agent the AST audit is the mechanical way:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/audit_state_preconditions.py" .
```

It lists every literal-string key read from `state` / `agent_context` / `ctx` / `payload` /
`config.configurable`. **An empty list is the finding** — record it with its evidence, e.g.
`STATELESS_FINDING="the AST audit over <module> found 0 agent_context/state key reads; no tool
gates on a turn-0 precondition"`. A non-empty list means the agent is NOT stateless: leave the
variable empty, and declare the keys a caller or a prior flow supplies in `plurai.config.yaml`
`agent_context` now (setup Step 4b); the env build carries only what the config declares.

**For every other language — and for a Claude-skill agent — establish it BY HAND, and say what you
ran.** The auditor is Python-only, and a non-Python agent (bridge lane) or a plugin/skill agent is first-class in
Step-0's dispatch; with only the AST route stated, those agents leave this finding permanently
blank and `agent_context_governance` stays blocked with nothing saying that is the expected
outcome. The by-hand route is the same question the auditor asks: grep the agent's TOOL AND HANDLER
ENTRY POINTS for reads of the turn-0 state object — `state`, `agent_context`, `ctx`, `payload`,
`config.configurable`, `context`, or whatever the framework calls the object the caller hands in
(a TS graph node's `state` argument, an MCP tool's request context, a skill's frontmatter inputs).
Read the entry points, not the whole tree: a state key read in a helper the tools never call gates
nothing. Then record the finding with the paths you read and the search you ran, e.g.
`STATELESS_FINDING="grepped state/agent_context/ctx/payload across the 6 tool handlers in
src/tools/*.ts (rg 'state\.|agentContext|ctx\.'): 0 turn-0 state reads; every tool takes its
arguments from the call"`. A finding that names no search is a claim nobody can re-run.

Never write the finding to silence the check; **an unearned `stateless` is worse than an honest
`ungoverned`** — the first closes the check with a claim nobody made, the second leaves it open and
visible.

| `CONTRACT_EXIT` | meaning | what you do |
|---|---|---|
| `0` | PROCEED — nothing false, every declared check executed | one summary line naming what was verified |
| `1` | FIX FIRST — a VERDICT, not a failed scan: a finding holds (`report.gate_findings` in `$ART/scan_contract.json`) — a typed fact the corpus contradicts, a `required: true` closed set with no detector, or a corpus root that admitted no file | decide each one alone, as `/simulation-generate-suite` Step 0 does (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`); never ask the user. Repair what is in reach (re-author the fact the excerpts refute, declare the detector, widen `--suffixes`), or overrule it with `gate_ledger.py overrule --art "$ART" --gate-id <gate_id> --fingerprint <fingerprint> --reason "<what you verified>"`; then re-run `bench_manifest.py verify --mode reuse --after-edit`. The summary names what you repaired and, in product terms, what still needs fixing — never an overrule. No `report.gate_findings` at all means the checker predates overrules: repair what is in reach and leave the rest to the suite's Step 0 |
| `2` | NOT VERIFIED — the `## Agent facts` block cannot be read (not YAML, or no `facts:` list), or — on a checker that predates `--overrule` — one `check:` is malformed or two facts repeat an id (stderr names each), a corpus root is unreadable, the contract did not complete, or the previous verdict could not be cleared | error posture — fix what stderr names and re-run it; then one summary line saying nothing was verified until it passes. A `COULD NOT REMOVE` line means the artifacts directory rejected the delete. The script writes `$WORKDIR/.scan_contract_stale` to quarantine the surviving artifact, which the suite's Step-0 gate then refuses to read a verdict out of. Make that directory writable, delete the files it names, and re-scan |
| `3` | NOT RUN — the checker is not on this machine (`plurai-scan-contract`, which ships with the SDK) | one summary line saying NOTHING was verified |
| `5` | NOT RUN — the checker itself FAILED. It crashed rather than reaching a verdict, so it asserted nothing and there are no findings to read | one summary line saying NOTHING was verified — **never** report it as fix-first. "Your scan found something false" and "the checker died" send you to opposite places, and only the first has findings. Re-run it; if it repeats, report the crash with the checker's own output |
| `64` (a) | **THE INVOCATION** — this snippet's own arguments are wrong (most often an unsubstituted `<...>` placeholder). It prints a `usage: scan_contract.sh …` line | fix the invocation and re-run it — never report a verdict |
| `64` (b) | **THE CHECKER** — the invocation was FINE and the checker installed on this machine predates a flag this run requires. It prints `… predates the flag (its \`check\` does not accept --<flag>)` and NO `usage:` line. Two of the four come from the workdir, not from you, so a flawless command hits them: `--probe-consent` (a `.live_probe_consent` reading `granted`) and `--agent-command` (a `.agent_command` file); the other two are flags you passed, `--suffixes` and `--service-indicators` | do NOT edit the invocation — re-running it verbatim gives the same refusal. Either upgrade the SDK, or WITHDRAW what this checker cannot honour: set `.live_probe_consent` to `declined`, remove `.agent_command`, or drop the flag you passed. Then re-run. Report it as NOT RUN, never as fix-first |

Anything other than `0` or `1` leaves NO verdict on disk: the script sweeps the previous scan's
report and artifact, so a stale PROCEED cannot be read as this scan's. **That includes both
readings of `64`, and the script says so on stderr** (`removed the scan artifacts under … (
pre_generation_findings.md scan_contract.json )`) — so `/simulation-generate-suite` Step 0 finds no
verdict and reads WHY from the record (`bench_manifest.py contract-gate`): a `2` is recorded
`not_verified` and stops the suite until the input is fixed; a `3`, `5` or `64` is recorded `not_run` and the suite
carries a NOT-RUN line. Either way, fix what this table names and re-scan, and the verdict comes
back. The one case it cannot
sweep — an artifacts directory it may not write — is quarantined instead, by the marker above;
report that plainly rather than as a passing scan. **Any exit code not in this table** (a killed
script: 137, 143) is read exactly like `2`: no verdict was produced — the manifest records it
`failed` with no artifact, and the suite's Step-0 gate stops on that exactly as on a `2`.

**Quote the report's own findings; never re-diagnose them.** A fix-first item is about the
customer's own documents, and `/simulation-generate-suite` re-reads this verdict at its Step 0
and decides each held finding there (repair or overrule) before it spends.

### Step 3d — declare the agent's PRODUCTION hosts

Isolation here is BY CONFIG: the harness redirects a base URL, so an endpoint the agent holds in
its OWN code still points at the customer's live system and a simulated user would drive a real
write. The SDK blocks each declared production host inside a guarded Python agent's process, and
holds a run over a declared host it can reach when nothing enforces that, until its finding is
overruled — and it can only block or hold what this step declares. **Run it in BOTH modes**; the code may have moved
since a saved setup.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_egress.py" \
  --config "$WORKDIR/plurai.config.yaml" --declaration - <<'YAML'
status: declared                      # declared | none_discovered | not_determinable
agent_runtime: python_child           # python_child | subprocess
scanned: [<every file or dir you read for endpoints>]
hosts:
  - host: <hostname, host:port, or full URL>
    source: <path/to/file.py:LINE>
YAML
echo "EGRESS_EXIT=$?"
```

Leave `allow_hosts` out: the operator's existing waiver is kept (stderr names it); `allow_hosts: []` clears it.
A host in `allow_hosts` is one the operator consented to reach LIVE; every other declared host is
blocked inside the agent process during a run. Never add one on a headless run.

**Declare a `port:` for anything that is not plain HTTP(S).** A bare hostname or a non-web scheme
with no port cannot be shown to be blocked, so it can no longer earn `isolated` — `postgres://db`,
`redis://cache` and a bare `db.internal` all need the port they actually dial
(`db.internal:5432`). A host you leave portless is not a smaller declaration; it is one the
isolation check cannot decide, which reads as unchecked.

`0` recorded it. `2` the declaration is invalid or unreadable — fix what stderr names and re-run.
`3` the config carries an `egress:` key this script does not own, or a corrupted managed block —
relay it and merge by hand. `4` the config could not be read or written. **On anything but `0`
NOTHING was declared, and the summary must say the sweep was not recorded** — an absent key is
exactly what reads as "checked and clean".

- **A host qualifies when the agent could reach the customer's OWN system through it** — an API
  root, a base URL, a webhook, a mail or database endpoint held in code or in a config the
  harness does not redirect. The LLM provider the agent calls to think is not one of them: that
  is the agent under test.
- **`agent_runtime`: `subprocess` when the harness starts the process that runs the agent (a server,
  a CLI, a non-Python runtime), else `python_child`.** Required under every status.
- **A backend URL read from an env var** (`BACKEND_URL`, a `.env` key) is declared as
  `- env: <VAR>` with its `source`; its value is read inside the agent process at run time.
- **Every endpoint variable a store or vendor client reads is an `env` row, loopback included**, and a code fallback
  default (the host used when the variable is unset) is a `host` row; a corpus index the retrieval tools read is not one
  (name its variable in the map's `grounding.endpoint_env`, never pinned),
  nor a variable that also drives something else (a region a model client reads: rebind the store client instead). With a
  store tool on the config, `none_discovered` is refused (`endpoints_undeclared`). The harness ASSIGNS every `env` row a
  literal (`os.environ["<VAR>"] = …`): the mock's URL (`plurai.mock_service_url()`) or an `http://<name>.invalid` host
  for a simulated store or vendor, the entry's `target` for a confirmed one, the served double for an `agent_fake` entry's `env`; a `setdefault`, an inherited or a computed
  value fails (`endpoint_env_unpinned`). An agent the harness does not launch (`entry_symbol.executes: external` with no
  `launched_by: harness`) cannot be pinned, so with a simulated store it is refused (`agent_env_unreachable`): run it as a
  child the harness launches, or as a simulated copy at `agent_host` (Step 2a `agent.tool_interception`), rewired to the
  mock where it runs (the harness still pins its own `env` rows).
- **`scanned` lists what you read inside the agent folder** (the folder holding `plurai_sim/`); the script rewrites each
  path relative to it. A file outside it (a dependency, a repo-root build file) leaves the roster partial and withholds
  a clean verdict — leave it out, or name the wider root in `scan_root`.
- **`source` is required and names a code location.** The SDK prints it in the refusal, so a
  declaration that cannot be traced to a line is a refusal nobody can act on.
- **Found none → `status: none_discovered` with a `why`, never an absent block.** An absent key
  reports as `not_declared`, which reads as "checked and clean" and means "never looked". A
  RECORDED sweep is the opposite fact and now has its own status: the SDK reports `swept_clean`
  and the run report renders "swept N file(s), nothing found". That distinction exists ONLY
  because this block was written — omit it and the sweep you performed is unreportable.
- **`not_determinable` is the third answer — and it must be LOUD in the summary.** Nothing is
  probed; the run reports it as `not_determinable`, never a clean sweep. Name the endpoints you
  could not resolve.
- The script refuses a hand-authored `egress:` block rather than overwriting it; relay that
  refusal and merge by hand.

## Step 4 — record the run mode + the grounding requirement

Scan MAPS the agent and writes its harness; it builds **neither** the mocked world **nor** the
knowledge corpus. `/simulation-generate-suite` builds both — the world at its Step 0c, the corpus
at its Step-0 grounding gate — immediately before the
generation that consumes the env's state schema, so both are built against the
inputs as they stand at suite time (the corpus build rewrites `simulation_prd.md`,
and a trace brief published by `/analyze-traces` is auto-consumed there), and a customer
who scans today and builds the suite next week does not leave a mock deployed in between.

- **Run mode — the script records it.** Run, in one Bash call,
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-mode`
  — it derives the posture from the `plurai.config.yaml` Step 3 wrote, so
  `$WORKDIR/.run_mode` says `mockless` when nothing needs a mock (hard-required downstream:
  `/simulation-generate-suite` Step 0c, `/simulation-run`). A FACT about the approved topology,
  not a deploy. Never write the marker by hand.
- **Tool descriptions — the script copies them.** Run, in one Bash call,
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_tool_descriptions.py" --config "$WORKDIR/plurai.config.yaml" --topology "$ART/agent_topology.json"`
  — it sets each map tool's `description` (the line the report shows beside its calls) to the first sentence of its
  `descriptor.description`, and leaves it off where the config has none or the customer-text check flags it. Never
  write or edit a `description` yourself. A `WARN` line or a non-zero exit never stops the scan.
- **Grounding requirement — already RECORDED at Step 2a, and still not acted on here.** The
  `grounding` block is on `$ART/agent_topology.json` and was on the map the user approved. In
  a mode that did not analyze, carry it (and `probes`) over from the existing file rather than
  dropping it — 2b rewrites that file, and a rewrite without them destroys the record the suite
  and the consent question both read.

  **When `needs_corpus` is true, install the corpus extractor now** — `/build-rag-corpus`'s documented
  prerequisite, never asked, headless included: into the agent's environment, by
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install --extra extract --record "$ART"`
  (a source's driver extra is added at the build, where the source is known). Its last line is the outcome,
  `extractor: installed` or `extractor: NOT installed — <error>`; a failed install is one hand-off line with that
  error, and the scan continues.

  **Do NOT run `/build-rag-corpus` here, and do not offer to.** Three reasons, and the third is
  the one that bites:
  - A corpus is an expensive backend job over the customer's own documents, and its SCOPE decides
    what the whole suite can test. That is the user's call.
  - It belongs beside the other grounding inputs — the trace brief from `/analyze-traces`
    (command 2, which runs AFTER this one) and the doc examples — which the suite's gate already
    owns. A corpus built here cannot see the trace brief at all.
  - The suite's grounding gate keys on whether a corpus is already recorded. Building one here
    answers that question before the user is asked it, and takes the gate — and the scope,
    answerability and health checks it carries — out of the flow entirely.

## Step 4b — deliver the agent's descriptions and ground the workspace

**Run this in every mode**, after Step 4 and before Step 5. The agent's descriptions are three
files, `plurai.config.yaml`, `simulation_prd.md` and `scenario_variables.yaml`. This step builds the
declared part from them (every key they define, with its declared value and tier) for local use, uploads
the descriptions, and starts the backend's grounding for the workspace. The backend reads the same keys
from the descriptions itself, so the part is never uploaded. It makes version 0 of
the grounding ledger, which holds the agent's own declarations and nothing observed. Re-running it on
unchanged files sends nothing and keeps one version.

**This step asks the user nothing and waits for no processing, with one exception: item 2b.** The upload is the SDK's own
disclosure: it lists every part with its size and hash before a byte leaves and records each in the
workdir's upload log. Scan delivers only these files. A trace export or a corpus is delivered by
`/analyze-traces` and `/build-rag-corpus`, each as one more part of the same package. The exception
is a scan that already holds every source: item 2b then shows ONE consent screen listing every part and
delivers them all in one call.

1. **The scenario variables exist.** Setup's Step 11 wrote them in the generation modes. In the two
   modes that skip generation, or when `$WORKDIR/scenario_variables.yaml` is missing, run
   the `extract-scenario-variables` skill and follow it to a passing Step 5
   verify (skip its Step 6 report and Step 7 pre-warm). Never deliver without the file: version 0
   would hold no user-profile or complication keys.

2. **Build the declared part.** It is local, pure code over the three files, and it carries no version id.
   It stays on this machine: the trace export reads its names and the consent check reads that it exists.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/declared_part.py" build --workdir "$WORKDIR"
```

   It prints one JSON line (`entries`, `keys`, `refused`, `collisions`) and writes
   `$ART/declared_part.json`. Exit 3 means a description is REFUSED as a whole: more keys than the cap, a
   file past the size cap, a YAML alias that expands past the node cap, or text that is not YAML. It states
   the cause, writes no part and removes any earlier one, so nothing is half built and nothing is
   delivered. Do not run step 3 or 4. Tell the user, in product terms and once, which file to fix, then
   carry on to Step 5 and say in the hand-off that the setup was not recorded for grounding until it is
   fixed and the scan is run again:

   | cause | say |
   |---|---|
   | `too_large`, `key_cap` | "the agent's setup file is far larger than a real one (thousands of tools or facts); trim it" |
   | `yaml_expansion`, `yaml_recursive_alias`, `yaml_too_deep` | "the setup file reuses one block of YAML inside itself so often that it cannot be read safely; write the repeated parts out" |
   | `unparseable_yaml`, `not_a_mapping`, `not_utf8` | "the setup file is not valid YAML text; fix it where the error line points" |

   Exit 2 (no description at all) is a skipped step, not a refusal: say nothing. When `refused` is
   not empty the rest was still read; name each refused entry once, in product terms ("a tool named
   `off` is read as a yes/no value; quote its name"), and deliver anyway. Never mention `collisions`.

2b. **When the scan found every source, deliver the descriptions, the traces and the corpus behind ONE consent
   screen.** "Every source" is the descriptions (the declared part item 2 built), a connected trace source whose
   credentials are in the environment, and one registered directory knowledge base with its roots and domain file.
   A script decides it from the files on disk; never decide it from memory:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_consent.py" sources --workdir "$WORKDIR"; echo "consent sources exit=$?"
```

   Exit 1 (a `MISSING=` line names each source not found) is the usual case: say nothing and go to item 3, where each
   skill delivers its own part as before. Exit 0 (`FOUND=descriptions,traces,corpus`) means this item replaces items 3
   and 4 and Step 4d, and never runs when the scan has not found all three.

   **Plan every part; nothing is sent.** The script runs each ingest with `--plan-only` (the real archive, so the size,
   posture and content address on the screen are the ones the upload will have; no request and no backend credential)
   and renders the screen from the planned entries:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # same call — the shell resets between them
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-descriptions ingest-traces ingest-corpus)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
[ -n "$PLURAI_BIN" ] || { echo "MISSING runner plurai ingest-descriptions"; exit 0; }
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || { echo "GROUNDING_DELIVERY=BLOCKED"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_consent.py" plan --workdir "$WORKDIR" --plurai "$PLURAI_BIN" --backend "$SERVER_URL"; echo "consent plan exit=$?"
```

   - Exit 4: the runner lacks `plan-only-parts`. Relay the upgrade line it printed, once, in product terms ("the
     installed plurai is too old to show everything before it is sent"), and go to item 3.
   - Exit 3: nothing was sent. The cause is on stderr. Different anonymization settings for the traces and the corpus
     are the error posture's item 2 (one environment holds one setting; `anonymize:` in `plurai.config.yaml`); any
     other cause is yours to fix once, and the plan is run again. When the plan still fails, go to item 3.
   - Exit 7: the backend refused the SDK's version. Stdout is the update text: say it as the error posture says a version
     refusal, and stop.
   - Exit 0: the screen is on stdout, then a `SCREEN=<digest>` line.

   **Ask once, on the screen as printed.** The gate is `grounding-consent` (sanctioned ask — first run
   `cd "<agent-folder>"; bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction grounding-consent`). The
   question holds every line before `SCREEN=` verbatim (copy the parts, sizes and postures; never retype or
   summarize them), then asks whether to send them. Options: **Send all of them (recommended)** / **Not now: deliver
   each part with its own command**. A gate the user did not answer is re-asked, and nothing below runs until they
   chose. **Under `run_driver: headless` the declared default for `grounding-consent` is `decline`**: nobody consented
   to send the customer's content, so the plan stays unsent. A `/simulate` run puts this ask
   to its user too.

   - **Not now**, or the declared default: nothing was sent. Go to item 3.
   - **Send all of them**: record the approval of that screen, then deliver. The script runs each ingest for real
     with the hash its plan printed (`--expect-sha256`), so a part that is not what the screen showed is refused
     before it is disclosed or sent:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # same call — the shell resets between them
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-descriptions ingest-traces ingest-corpus)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
[ -n "$PLURAI_BIN" ] || { echo "MISSING runner plurai ingest-descriptions"; exit 0; }
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || { echo "GROUNDING_DELIVERY=BLOCKED"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_consent.py" approve --workdir "$WORKDIR" --screen "<the SCREEN= digest of the screen the user was shown>" || exit 0
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_consent.py" deliver --workdir "$WORKDIR" --plurai "$PLURAI_BIN"; echo "consent deliver exit=$?"
echo "WORKSPACE_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
echo "PREVIOUS=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" previous --workdir "$WORKDIR")"
```

   - **`CONSENT=DELIVERED`**: the `ADDED=` line is the ONE `added` argument for every part (descriptions, then traces, then
     corpus; the corpus is registered for the suite's Step 0b). Skip item 3 and go to item 4 with it. The delivery carries
     parts that take minutes to hours to process, so skip item 6's wait and Step 4d: the suite's Step 0b waits for the job,
     pulls and runs the findings handler.
   - **Exit 7** (no `CONSENT=` line): the backend refused the SDK's version; nothing more is delivered and the approval is
     spent. Stdout is the update text: say it as the error posture says a version refusal, and stop.
   - **`CONSENT=ABORTED`**: a source changed after the user approved (a trace arrived, a file appeared), so the parts
     are no longer the ones on the screen. Nothing reached `ground_workspace_start` and the approval is spent. Do NOT
     call it. Do not show a second screen in this run: carry on to Step 5 and say in the hand-off, in product terms, that
     the setup was not recorded because something changed after approval and that running the scan again shows a new
     screen. A part delivered before the difference sits unreferenced in storage and is found again by its hash.
   - No `CONSENT=` line, or `MISSING runner` / `GROUNDING_DELIVERY=BLOCKED`: nothing was delivered: run the contact hand-off (`stop_message.py contact --done`).

3. **Upload the descriptions, then read the entries back.** The SDK prints one JSON
   array of part entries on stdout; the script checks that the set it lists is the set of files on disk,
   byte for byte, and prints it as the `added` argument. The previous package version is the one the last
   delivery recorded, empty on the first.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # same call — the shell resets between them
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-descriptions)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
[ -n "$PLURAI_BIN" ] || { echo "MISSING runner plurai ingest-descriptions"; exit 0; }
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || { echo "GROUNDING_DELIVERY=BLOCKED"; exit 0; }
rm -f "$ART/part_entries.json"
SC_ARGS=()
[ -f "$ART/scan_contract.json" ] && SC_ARGS=(--scan-contract "$ART/scan_contract.json")   # the scan's claims travel with the descriptions
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" --refusal-stderr "$PLURAI_BIN" ingest-descriptions --workspace-root "$PLURAI_DIR" --descriptions-dir "$PLURAI_DIR" \
  "${SC_ARGS[@]}" > "$ART/part_entries.json" || { [ $? -eq 7 ] && exit 7; echo "GROUNDING_DELIVERY=UPLOAD_FAILED"; exit 0; }
echo "WORKSPACE_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
echo "PREVIOUS=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" previous --workdir "$WORKDIR")"
echo "ADDED=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" added --workdir "$WORKDIR" --entries "$ART/part_entries.json")"
```

   - A `WARNING (disclosure: home_named)` line from `ingest-descriptions` is printed before any byte leaves:
     relay it to the operator in that step's own message, naming the files it lists. It does not stop the
     upload.
   - **`MISSING runner`, `GROUNDING_DELIVERY=BLOCKED` or `UPLOAD_FAILED`:** nothing was delivered and nothing
     was recorded. A missing or too-old runner and a wrong-backend refusal: say in product terms that the setup is not recorded, with the one fix the line names; with no fix to name, run the contact hand-off (`stop_message.py contact --done`). An upload that
     failed is retried once; a second failure: run the contact hand-off (`stop_message.py contact --done`).
   - **An empty `ADDED=`** means the check failed and its reason is on stderr (a part the user was not shown,
     or a file that changed after it was uploaded). Run this block once more; a second failure: run the contact hand-off (`stop_message.py contact --done`).
   - `ADDED=` lists `scan_contract.json` (`$ART/scan_contract.json`), the scan's claims, whenever that file
     exists: the block passes it to the SDK, which delivers it as one more description right after the
     three descriptions, and the entry is passed on like the rest. A scan that wrote no contract delivers none.
   - Never pass the SDK a file other than the descriptions and `$ART/scan_contract.json`, and never
     choose a model anywhere in this step: that is the backend's decision.

4. **Start the grounding with the printed entries.** (Item 2b's `CONSENT=DELIVERED` delivery joins here: its `ADDED=` array is
   the `added` argument, whole.) Call the backend tool `ground_workspace_start` with
   `workspace_id` = the `WORKSPACE_ID=` line, `previous` = the `PREVIOUS=` line (leave the argument out when
   it is empty), and `added` = the `ADDED=` array exactly as printed. Pass no `removed`, no `overrules` and
   no other argument. It returns at once with the job and the package version.

   - A refusal reads `package refused (<cause>)`. `stale_previous` means the workspace has moved on (a
     teammate scanned, or a fresh clone has no record): read the head from the refusal text, pasted into a
     quoted heredoc so none of its characters reach the shell, and call again ONCE with it as `previous`.

     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" head --refusal - <<'REFUSAL'
     <the refusal text>
     REFUSAL
     ```

      `previous_unknown` means the recorded version belongs to another
     backend: delete `$WORKDIR/.grounding_package` and call again ONCE with no `previous`. Any other cause
     changes nothing on the backend: run step 3 once more, call again once, and on a second refusal, run the contact hand-off (`stop_message.py contact --done`).
   - The tool is idempotent on the same previous version and parts, so a transport failure with no envelope is called again once; a second failure: run the unavailable stop (`stop_message.py unavailable --done`).

5. **Remember the package and the job.** Run this with the object the tool returned (an object, never the
   refusal text):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" record --workdir "$WORKDIR" --workspace-id "<the WORKSPACE_ID= value>" --result - <<'JSON'
<the JSON object ground_workspace_start returned>
JSON
```

   It writes `$WORKDIR/.grounding_package` (the package version the next delivery builds on) and
   `$WORKDIR/.grounding_job` (the job that consolidates it). Both are machine-local handles, ignored by
   the managed block.

6. **Version 0 settles in seconds; wait for that and for nothing else.** (Skip this item after item 2b's `CONSENT=DELIVERED`.) One foreground wait on the
   job, with a `timeout: 600000` Bash call (the recipe in
   `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`; never pipe or capture it):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <the job id> 120
```

   Exit 0: done, nothing more to read. Exit 14: wait again once. Exit 13: the job FAILED. Read its `error` yourself
   with `job_status`: when it states a description cause (the same causes as step 2) say what to fix in product
   terms as there; otherwise run the contact hand-off (`stop_message.py contact --done`). A failed job is final: never deliver again
   to retry it. Every other exit follows the polling doctrine. Whatever the job found about the descriptions stays
   internal: Step 4d handles the findings and none goes in the hand-off.

   The scan does not wait for any processing, because it delivers nothing that needs processing. Pulling the
   ledger to disk, waiting for it to settle and pinning it belong to the suite.

## Step 4c — pull the grounding mirror

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

## Step 4d — handle the grounding's findings

`${CLAUDE_PLUGIN_ROOT}/docs/grounding-findings.md` is the canonical handler; read it the first time. It runs once the pull of Step 4c is done, on the job Step 4b already waited for. Skip this step after Step 4b's item 2b delivered the traces and the corpus: that job is still processing, and the suite's Step 0b waits for it and runs the handler. The agent
decides alone: never ask the user about a finding or an overrule, and never put one in a hand-off or a
report.

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

Act on each finding by its `handle=`, holding ones first, as that document says. Only an evidence fault holds: remove a
failed part (`handle=remove_part`) and deliver again (`/simulation-scan-agent` Step 4b items 2, 3, 4 and 5, never 2b). A value conflict never holds. It is
`handle=accept` whatever its tiers: make no description edit and no delivery for it, however its remedy reads; the losing
description keeps its value and `log` keeps the losing value as a candidate scenario. A code-versus-trace finding is `handle=accept`: the backend decides it, the mock keeps the code value, and `log`
keeps the production value as a candidate trace scenario. After each delivery you make for a finding, record it,
wait for the job, pull the mirror again and read again. Three rounds that leave a holding finding end it: carry on,
because the version stays unsettled and unpinned and the suite's Step 0b reads it again. Whatever the findings say stays
internal.

## Step 5 — gitignore housekeeping + summary

**Ensure the workdir's generated/customer-data content is gitignored — this is a DATA-SAFETY
step, not tidiness.** `plurai_sim/` accumulates locally-extracted CUSTOMER DOCUMENT CONTENT
(`rag_corpus/` holds extracted document/DB chunk text; `traces/` exported production traces;
`doc_examples/` and the three `*scenario_bundle/` dirs real customer records — the two grounding
lanes stage VERBATIM production conversations there, and neither can be anonymized) plus
per-machine state markers
(`.run_mode`, `.last_run`, `.mock_service_url`, the upload sidecars). None of that belongs in
the customer's repo — while the AUTHORED inputs and the ids naming the backend sets they
mirror do, and this step is what puts them there. **Write it as ONE managed block, and
RECONCILE, don't accumulate — an accumulated block spells one rule several ways and the
mirror stops matching:**

1. If the repo `.gitignore` already contains a `# BEGIN plurai-simulation` block, REPLACE
   everything between the markers with the current block below when the content differs OR the
   BEGIN line's `rev` is missing or older than the current rev (never append a second copy; a
   byte-identical block at the current rev is left untouched).
2. Delete legacy plurai lines outside the block — ONLY lines whose text contains `plurai_sim` (never touch anything else in a customer-owned file) — the known stale spellings: bare
   anchored forms (`plurai_sim/artifacts/`, `plurai_sim/rag_corpus/`, …), per-file dotfile
   enumerations (`plurai_sim/.corpus_id` etc.), and any prior `**/plurai_sim/**` line — the
   managed block supersedes all of them.
3. Then ensure the block reads exactly:

```
# BEGIN plurai-simulation rev 11 (managed — edits inside are overwritten by /simulation-scan-agent)
**/plurai_sim/**/artifacts/*
!**/plurai_sim/**/artifacts/data_profile.md
!**/plurai_sim/**/artifacts/scenarios.md
!**/plurai_sim/**/artifacts/evals.md
!**/plurai_sim/**/artifacts/suite.json
!**/plurai_sim/**/artifacts/agent_findings.md
!**/plurai_sim/**/artifacts/fixes.md
!**/plurai_sim/**/artifacts/pre_generation_findings.md
!**/plurai_sim/**/artifacts/scan_contract.json
!**/plurai_sim/**/artifacts/verification.json
**/plurai_sim/**/rag_corpus/
**/plurai_sim/**/traces/
**/plurai_sim/**/doc_examples/
**/plurai_sim/**/scenario_bundle/
**/plurai_sim/**/trace_scenario_bundle/
**/plurai_sim/**/grounded_scenario_bundle/
**/plurai_sim/**/runs/
**/plurai_sim/**/bridge/runtime/node_modules/
**/plurai_sim/**/.*
!**/plurai_sim/**/.scenario_set_id
!**/plurai_sim/**/.eval_set_id
!**/plurai_sim/**/.corpus_id
!**/plurai_sim/**/.corpus_ids
!**/plurai_sim/**/.corpus_scope
!**/plurai_sim/**/.knowledge_bases
!**/plurai_sim/**/.doc_examples_set_id
!**/plurai_sim/**/.scenario_bundle_id
!**/plurai_sim/**/.document_fields
!**/plurai_sim/**/.workspace_id
!**/plurai_sim/**/.ledger_version
# END plurai-simulation
```

4. **Stage what this command wrote.** The block leaves the authored inputs stageable, and
   un-ignoring a file is not tracking it: `git status` collapses an untracked parent to a
   single line, so a workspace can look committed and hold nothing.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh"    # stages what exists; never commits
```

   It stages in the CUSTOMER's repo, so say so in the summary — name the repo it printed,
   say the files are TRACKED but NOT committed, and leave the commit to them. It exits
   non-zero when the workdir is not in a git repository at all: that is a real finding (their
   suite would not survive a clone), so report it rather than moving on. Every LATER command
   stages its own output as it lands — most of these files do not exist yet at scan time.

   **When it prints `NOT STAGED`, the repo's index is read-only in this session** (a sandbox that
   protects `.git`) and nothing was added: the files are NOT tracked. That exits 0 on purpose.
   Do not ask for broader access to stage them, and run no `git add` of your own for any file
   (the `.gitignore` edit included). Name the repo and the printed `git add` command in the
   summary, say the files are untracked, and say the `.gitignore` edit is theirs to commit with
   the rest.

5. **Record the agent's fingerprint** — the last change this step makes, after the `.gitignore` edit and
   staging, so the next `/simulate` can tell whether the agent changed since this scan:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" fingerprint "$WORKDIR"
```

The `**` in the middle matches zero or more directories, so each pattern covers BOTH layouts:
the flat workdir (`plurai_sim/rag_corpus/`, `plurai_sim/.scenario_set_id`) AND the named-env
one (`plurai_sim/envs/<name>/rag_corpus/`, `plurai_sim/envs/<name>/.scenario_set_id`) — in
named-env mode `$WORKDIR` IS `plurai_sim/envs/<name>/` and every marker lands there, so a
flat-only glob silently commits every runtime handle.

**`artifacts/` ignores the EVIDENCE; the negations carve out the INPUT and the READERS.** Two
different reasons, one line each:

- `data_profile.md` is a GENERATION INPUT — scenario and eval generation are grounded on it the
  way they are grounded on `simulation_prd.md` — so it has to survive a clone like every other
  input.
- `scenarios.md`, `evals.md`, `suite.json`, `agent_findings.md`, `fixes.md`,
  `pre_generation_findings.md` are the READER files: the deliverable a human opens to see what
  the suite tests and what it found. A suite whose scenario text lives only on one machine is
  not a suite the customer owns, and these carry that text. `agent_findings.md` is PRUNED PER
  EPOCH (`flow-window.sh arm` clears the confirmed tier), so a commit of it is a snapshot of
  that epoch, not an accumulating log.
- `scan_contract.json` is the DRIFT BASELINE — the source fingerprint a later contract run
  compares against — so a clone without it cannot tell whether the corpus moved since the scan
  that reasoned about it.

Everything else under `artifacts/` — `loop_log.md`, `probe_report.md`,
`scan_facts.yaml` (a projection of the PRD, regenerated on every scan), the
topology render, `charts/`, `runs/` — is local evidence and stays ignored. A customer who does
not want the reader files in their history opts out in `.git/info/exclude` (which is theirs and
untouched), **never** by editing the managed block: this command overwrites it on every scan.

The pattern ends in `/*` rather than `/` for a mechanical reason: git never descends into an
excluded DIRECTORY, so a negation under `artifacts/` would be unreachable.

**The dotfile line ignores the family; the negations carve out the BACKEND-IDENTITY markers.**
The split is what each file is: an id naming ground truth that lives on the backend
(`.scenario_set_id`, `.eval_set_id`, `.corpus_id` / `.corpus_ids`, `.knowledge_bases`,
`.doc_examples_set_id`, `.scenario_bundle_id`, `.ledger_version` — the grounding version the last
suite was pinned to — and `.workspace_id` — the bench's own id, naming
its trace-brief slot) travels with the repo — and `.corpus_scope`
travels with `.corpus_id` because it is the human gloss on it: the id alone says nothing about
WHAT was ingested, and a clone that cannot tell is holding an opaque handle. `.document_fields`
travels for the OTHER reason a file belongs here: it is repo-invariant AUTHORED config — a
property of the customer's own code, the same in every clone, exactly as `shape:` is in
`plurai.config.yaml`. Ignored, a teammate who clones the bench and runs `/generate-scenarios`
gets `document_generation=False` and a byte-different set, with nothing saying why. That is because the committed
mirror beside it — `policies_config.yaml`, the scenario inputs — is unreadable without knowing
WHICH set it mirrors. Everything else is machine-local and stays ignored: `.run_mode`,
`.last_run`, `.active-env`, `.mock_service_url`, `.env_id`, `.scenario_job_id`,
`.ingest_host`, `.plurai/`, and the upload sidecars (`.prd_upload_id`,
`.variables_upload_id`, `.data_profile_upload_id`). A new marker is ignored by default —
re-include it here only when it names something on the backend. `.ingest_host` records which backend this workspace last
ingested to. It is a RECORD, not a gate — the SDK has no fail-closed pin, so
nothing refuses an upload on it; when the backend legitimately moves, the marker is simply
stale. The live wrong-backend check is `scripts/assert-backend-identity.sh`, run before the
upload.

**There is exactly ONE `.gitignore` in play — the repo root's.** Nothing writes a nested
`plurai_sim/.gitignore`, so never instruct the user (or yourself) to edit one; a rule added
there would sit beside the managed block that already covers it. The intentional artifacts
that SHOULD be committed (`plurai.config.yaml`, `simulate.py`, `scenario_variables.yaml`,
`scenario_state_schema.yaml`, `simulation_prd.md`, `eval_intent.yaml`, `policies_config.yaml`,
`envs/**/plurai.env.yaml`, the verify scripts) are not dotfiles and are unaffected — step 4
stages them.

The `**/` prefix is REQUIRED, not decorative: a gitignore pattern with a slash at
the beginning or in the MIDDLE is anchored to the `.gitignore`'s own directory
(a trailing slash is just the directory marker and does not anchor), so the bare form
`plurai_sim/artifacts/` silently fails to match an agent living in a
subdirectory (`<repo>/some-agent/plurai_sim/artifacts/`) and its artifacts get
committed. In a git repository, verify with `git check-ignore -v <path>` when the agent is nested.

Then print a compact summary — the final message opens with the anchor line
(report_guard validates it): what was set up, in product terms, never the build's
internal issues (anything you fixed along the way is process, not content):

> **Environment ready** — <agent name>: N surfaces live · M mocked.

Tool-less / all-real variant — when `.run_mode` is `mockless` (nothing to mock), the
anchor line reads instead:

> **Environment ready** — <agent name>: N surfaces live · 0 mocked — nothing is mocked in this run.

**Never render this posture as "local".** `local` is a real and different posture — a mocked
world hosted in-process — so calling a tool-less or all-real run "local" tells the customer
their run was mocked when every tool reached the live service. Say what is true: nothing
is mocked here.

(Keep the `**Environment ready**` prefix in both variants — report_guard keys on it.)

**The hand-off MUST carry the run-integrity line, verbatim, on its own line.** It is doctrine
item 4's one licensed sentence and it is no longer written from memory — the runner rendered it
from `$ART/verification.json`, and `report_guard` compares it word for word:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" integrity-line
```

Paste what it prints. Never re-word it, never summarise it, never drop the clause naming what did
not run: that clause is the whole reason the line exists. A scan that says "checks 12 of 14
executed (the two-turn agent check: the agent provider key is not set)" is telling the customer their bench was
never driven against their own model — which they can fix in a minute and could not otherwise
know. Rendering it as "everything checks out" is the failure this line was added to end.

**Ending a turn mid-flow** — a status message while the setup is still running is
style-validated too, so lead it with the in-progress anchor: `Environment in progress`. It
answers exactly one question, does this turn owe the hand-off skeleton; it never closes the
window, it never closes the window, it is valid only while harness-tracked work is actually in flight (a wait in the background, a spawned subagent — the harness records it and re-invokes you when it finishes; a detached `nohup` does not count and the guard blocks the anchor), and it exempts the message from nothing else (no cost figures, no question-shaped ending).

- the topology report — the line `report_link.py link "Open the agent map →" "$ART/agent_topology.md"`
  prints, re-run now (this summary is the message the customer keeps, so it carries the same link
  2c handed them, never a hand-typed path)
- in the `.run_mode`=mockless variant, "`.run_mode` = mockless — nothing is mocked; every tool
  runs for real". The mocked world is deployed by the NEXT command, so there is no
  `.mock_service_url` yet, and claiming one is a lie the user discovers at run time.
  **Never report a corpus id here** — scan does not build one, and a scan that claims to have
  built a knowledge base is claiming the customer approved a scope they were never asked about
- **the Step-3c verdict, in EVERY branch** — PROCEED (say what was verified: N facts, the URL
  allowlist, the source hash), FIX FIRST (the findings themselves, ahead of everything else in
  the message, with `$ART/pre_generation_findings.md` linked), NOT VERIFIED, NOT RUN, or
  skipped-no-corpus — said as the map's `## Grounding` line as rendered, never reworded. Silence here is indistinguishable from an all-clear, which is the one thing
  it must never be. The FIX FIRST link is printed exactly as 2c prints the map's — never published:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Pre-generation findings →" "$ART/pre_generation_findings.md"
  ```
- **the grounding line, only when Step 4b did not record the setup** — one line in product terms: which
  setup file must be fixed (Step 4b's table) or that the setup could not be recorded and support@plurai.ai or Discord
  is where to reach Plurai. Say nothing about grounding when Step 4b recorded it: a delivered setup is not news, and a
  package, a job, a version or a finding is never in the hand-off.
- **the Step-3d egress status** — one line: the production hosts declared, that the sweep found
  none and what it read, that it could not be determined, or that nothing was recorded at all.
  Never omit it: an unstated sweep and no sweep look identical.

**Then present the OPTIONAL grounding inputs before the suite.** Each is optional but recommended when it
applies — it grounds the generated scenarios/evals in the agent's REAL data (behavior, knowledge, records,
formats) instead of LLM guesses; skipping them all yields a purely code-derived suite. The traces bullet
is ALWAYS shown; show the other two only when they apply to THIS agent (from the topology you just
built), then the next step:

- **Production traces → `/analyze-traces`** — grounds the mock + scenarios + evals in REAL production
  behavior (real user phrasing, real anomalies, real failures). **Offer it every time, with no test to
  pass first.** Whether traces exist is a fact about the customer's INFRASTRUCTURE, not their code: a
  gateway, a proxy, a sidecar or the model platform's own logging leaves nothing in the source to find,
  so a scan that saw no observability import has learned nothing about whether they have traces. Staying
  silent there costs them the one grounding input that reaches all three of the mock, the scenarios and
  the evals — and only the customer can answer the question, so ask nothing and just name it. When
  derive_tool_facts' last summary line counts tool return shapes read from code only (`inferred` or
  `declared_unknown`), the bullet names them: "N tool return shapes are read from code only: a, b, c;
  production traces would ground them." Take N and the names from that line, never from your own tally.
- **Real material for scenarios → `/ground-scenarios`** — when scenarios should be grounded in the agent's
  REAL material: a real datum ("current page" / "selected record" / "active account") behind a precondition,
  or a real production conversation the agent picks up mid-flight. The front door detects what the customer
  has and routes to the right bundle lane; each scenario is then built FROM its material so the grounding is
  real, not LLM-hallucinated. Recommend whenever the agent operates on a selected/current record, or the
  customer has thread exports on disk.
- **Document / artifact examples → `/upload-doc-examples`** — NOT only for document-extraction agents:
  recommend whenever a scenario involves a document/artifact the agent reads, produces, or references (an
  invoice / form / ticket / report / statement / PDF / image), so the per-scenario generated document is
  templated from REAL formats instead of brief-only placeholder text. OMIT only if no scenario touches a document.

- next step: **`/simulation-generate-suite`** — it stands up the mocked world (~10-60 min the
  first time, seconds on a re-run) and then builds the test suite (scenarios + evals) on it. It
  AUTO-consumes whatever grounding you provided above and the scenario variables this scan wrote.
  **Replace this line when Step 3c came back FIX FIRST**: the single next action is then fixing
  the findings and re-running `/simulation-scan-agent`, because building on a premise the
  documents refute spends the whole qualification loop rediscovering it.

**When Step 2a recorded `grounding.needs_corpus: true`, say what the suite will START by asking —
here, attached to the next step, NEVER as a bullet in the optional list above.** The knowledge
corpus is not a grounding input the user may weigh against the others: it is the ground truth
retrieval scenarios are built FROM, so it decides whether a whole capability is covered at all,
and the other three only make an existing suite better. Filing it beside them invites the user to
skip the one input that cannot be skipped. Say it in these words: "Your agent answers from a knowledge
base, so building the test suite begins by building it from your documents. Without it the suite contains
0 retrieval scenarios and the knowledge base is never tested." (That consequence is the sentence the map already carries under
`## Grounding` — the two surfaces the user sees must not disagree.) **Do not offer to
build it now and do not run `/build-rag-corpus` yourself**: its scope decides what the whole suite
can test, and that decision belongs at the gate that owns it, after `/analyze-traces` has had its
turn.

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

First check that this stage left every file the page reads:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check scan --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step scan --flowdir "$FLOWDIR"
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step scan --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

**The window closes itself on the validated hand-off** (report_guard removes the marker
when the final message passes with this command's anchor or the failure hand-off — never
`rm` it before rendering). On an EARLY exit, close it own-marker-guarded (not an exit that ends on the contact hand-off: render that hand-off and leave the window to its check):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close scan-agent
```

## Notes

- **Approval is mandatory** — never write `plurai.config.yaml` / generate before
  the user approves the topology in step 2.
- **Named-env awareness** — always use `$WORKDIR` / `$ART`; never assume bare
  `plurai_sim/`.
- **File hygiene** — EVERYTHING generated (`.md` + `.svg` + `.json`) goes in the
  gitignored `$ART`; never write topology artifacts to the working-dir root. The
  whole folder is regenerable — re-run step 2 to rebuild it.
- **Backend objects stay on the backend** — persist only id dotfiles + the local
  topology artifacts; never dump fetched scenario/eval/corpus YAMLs into the repo.
- **Re-render only** — to refresh the diagram after editing
  `$ART/agent_topology.json`, just re-run step 2b; steps 1/3/4 don't repeat.
