---
name: extract-eval-intent
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: >-
  Write eval_intent.yaml, what the customer cares about, from the PRD and scenario variables after 3-4 questions. Optional. Run standalone before a suite build, never inside /simulation-generate-suite.
---

# Extract Eval Intent

You produce a single file: `plurai_sim/eval_intent.yaml`. This is
the **primary input** to `/generate-evals` — it tells the backend
Claude what the customer actually cares about evaluating, in their
own words.

Why bother (vs. letting the backend infer from PRD alone): the PRD
describes what the agent *does*. The intent describes what the
customer wants to *catch when it goes wrong*. Those are different
documents — the intent is sharper and produces sharper evals.

<!-- BEGIN backend-check: generated from ${CLAUDE_PLUGIN_ROOT}/docs/backend-check-block.md; edit there -->
## Check the backend first

Before anything else in this skill, run the backend check. It needs no backend tool and no SDK, and it comes before the
tool-list step of the error posture:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"
```

- Exit 0: go on. A line it printed is an update notice: say it once, as printed, first; it stops nothing.
- Exit 7 is a version refusal: follow the error posture's version refusal rule. Say what the check printed, verbatim and
  alone, with nothing before it: not the exit code, not that it is a refusal. Then stop. No tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line (no usable login; this skill has no readiness step, so the check is its login
  test): run the `harness-setup` skill from the command the user typed (`from /<command>`) and show the user nothing from
  the line. That skill checks the backend after its login, so go on when it reports Plurai set up, and stop when it stops.
  Any other line, such as a `NOT READY` line for hooks Codex has not trusted: tell the user in plain words what it says
  to fix, and stop.
<!-- END backend-check -->

## Step 1: Confirm preconditions

Verify these files exist in the agent's plurai directory:

- `simulation_prd.md` — produced by `/simulation-scan-agent`
- `scenario_variables.yaml` — produced by `/extract-scenario-variables`

If either is missing, tell the user the relevant prior command to
run, then stop.

## Step 2: Read the schema reference

Read `references/eval_intent_schema.md` — the OWNER of the intent's rules, whoever authors the
document, and the explanation of `references/eval_intent_schema.json` (`$id:
plurai-eval-intent/2`), which is the normative shape. **Author `plurai-eval-intent/2`, from that
file, never from memory of what an intent looks like.** v1 (`goals` / `must_catch` /
`custom_metrics`) is still READ by the backend and is no longer AUTHORED — if the user already
has a v1 file, the schema doc's migration note says how each key becomes an axis.

Read `references/eval_intent.example.yaml` — a hand-authored example
the user can also inspect / hand-edit if they prefer that path.

## Step 3: Read the agent's plurai artifacts

Read these to ground your questions in the agent's specific surface:

- `plurai_sim/simulation_prd.md` — agent purpose, entity schemas,
  business rules, success criteria.
- `plurai_sim/scenario_variables.yaml` — `agent_purpose`,
  `agent_tools`, `typical_complications`, `success_criteria_hints`,
  `typical_user_profiles`.

Use these to phrase questions in the user's domain (reference real
tool names, real failure modes from `typical_complications`) rather
than generic eval-engineering language.

**Read them for `traced_to` as well as for phrasing.** Every failure mode you write has to name
where it came from, and the PRD is where most of them come from — so as you read, keep the
section HEADING and the VERBATIM sentence for each rule you expect to grade. Recovering those
after the interview means reading the PRD twice.

## Step 4: Ask the user 3-4 targeted questions

Phrase questions in the user's domain. Pick from this menu — ask
the 3-4 that apply best to the agent's surface (skip ones the
artifacts already answer):

1. **Headline failures.** "What are the 2-3 outcomes you'd be unhappy
   with if you saw them in production?" — each answer is an **axis**
   (`axis:`, the one-sentence statement of what a violation IS).

2. **Failure modes you've seen.** "Looking at `typical_complications`
   in scenario_variables, which of these have you ACTUALLY seen happen
   (vs. theoretical)? Anything missing from that list?" — each answer is
   a **failure mode** under the axis it belongs to, and one the customer
   has actually SEEN is a `describes:` you can write concretely.

3. **Agent-specific behaviours.** "Are there agent-specific
   behaviors that don't fit a generic eval — e.g. 'the agent should
   always cite the tool that produced a number', 'the agent shouldn't
   propose state changes without showing a preview first'?" — these are
   axes too, usually with one failure mode each.

4. **Preferences.** "Anything special about how evals should *behave*
   on edge cases — e.g. score zero on inapplicable scenarios rather
   than vacuously passing? Prefer continuous metrics over boolean
   ones for distribution-style measurements?" — drives the
   `preferences:` block.

Ask sequentially (one question per turn — let the user think). If a
user response is vague, follow up with a concrete instance ("can
you give me one example?") before recording.

## Step 5: Synthesize the YAML

Combine the user's answers + what you inferred from the artifacts
into `plurai-eval-intent/2` — the shape `references/eval_intent_schema.md` owns and
`references/eval_intent_schema.json` types. Nothing outside it: both files declare
`additionalProperties: false`, so an invented key is dropped, not merged.

**A term this document SHARES with the PRD is quoted from the PRD, verbatim.** When a word
decides a judge's verdict ("resolved", "in scope", "confirmed", "complete"), copy the PRD's own
definition word for word and cite its section — never re-derive it in your own phrasing. Two
documents defining the same term differently makes the eval unscorable: the judge fires on a
scenario the PRD says is correct, and neither document is wrong by itself.

**The SHAPE rules are not repeated here.** Which fields exist, which are required, the id space,
the length floors, the legal `surface` and `source` values, and when `quote` stops being
optional — all of that lives in `references/eval_intent_schema.md`, and only there, so that the
suite orchestrator (the other author of this document) reads the same rules you do. Have it open
while you write. What follows is only how an INTERVIEW maps onto that shape:

- **One answer is not one axis.** Two answers that fail the same way on the same surface fold
  into one axis with two failure modes — one judge carrying two obligations. Coverage is
  credited per (policy, failure-mode) row, so folding loses nothing, and it is what stops a
  dozen absolutes co-binding every reply. Splitting is the expensive direction.

- **What the customer has SEEN and what they FEAR are both legal, and they trace differently.**
  A failure they can describe from a real incident usually traces to a PRD rule or a scan
  finding; one they only worry about, that no input document states, is `source: customer` —
  declared as such, never dressed up as a PRD reference. The backend's provenance tier decides
  on exactly that value, so disguising it is the one edit that makes the document lie.

- **Use the section headings + verbatim sentences you collected at Step 3.** Reaching the end of
  the interview with no quotes in hand means reading the PRD a second time, and a paraphrase
  written from memory is how a term the judge grades comes to mean two things.

- For `preferences.rejection_handling`, use "score zero on inapplicable
  scenarios, don't vacuously pass" when the user wants strict scoring
  on edge cases. The backend framework already supports this via the
  inconclusive-result mechanism; calling it out explicitly nudges
  the backend toward continuous metrics.

## Step 6: Write the file

Write `plurai_sim/eval_intent.yaml`. Add a one-line top comment
naming the document + the timestamp.

**Then check what you just wrote, before telling the user anything** (nothing else in the chain
validates it):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"    # $PLURAI_PY — one that HAS pyyaml
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/extract-eval-intent/scripts/verify_eval_intent.py" "$WORKDIR/eval_intent.yaml"
```

Exit 0 prints the counts. **Exit 2 is a defect in the file:** each line names a field (a missing
`schema`, a key the schema does not declare, a repeated id, a failure mode with no `traced_to`, a
`prd`/`corpus` trace with no `quote`, YAML that does not parse) — fix those and re-run until it is
clean. **Exit 3 is the interpreter, not the document** (no PyYAML): install `pyyaml` into the
interpreter it names (or point `$PLURAI_PYTHON_BIN` at one that has it) and re-run. Never edit a
valid file to quiet a check that could not run.

Fixing what it names is not a formality: the backend
consumes this file as YAML, and on a parse failure it degrades **silently** — every
intent-coverage check (declined items, unmapped items, oversubscribed axes) is skipped and the
job's own accounting cannot say so, so a file that was invalid from the moment it was authored
runs a whole eval generation with intent coverage dark and surfaces hours later, if at all. The
usual cause is prose that starts with a quote and continues unquoted — `- "A URL" the agent
constructed itself` — which YAML reads as a malformed scalar. Quote the WHOLE value or none of it.

Tell the user:
- Path written.
- Counts: how many axes, and how many failure modes across them.
- Next step (the `/simulation-generate-suite` flow runs it): eval generation
  authors `policies_config.yaml` using this intent.

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

## Precedence: who is driving decides whether you ask

The asking rule below is for a DIRECT invocation. When another skill is driving —
`/simulation-generate-suite` (including its quality-gate loop) or any orchestrator that
has already stated its own interactivity posture — **the caller's posture wins and you
do not prompt.** Infer from the PRD + scenario_variables, note in one line what you
would have asked, and continue. Prompting inside an automated loop stalls it at a point
the user is not watching, and `/generate-evals` explicitly says not to pause for this
file — a standalone invocation there would deadlock the gate. Same rule in reverse: a
direct `/extract-eval-intent` DOES ask, because nobody else is deciding.

## What NOT to do

- Do not author the YAML without asking the user any questions **when invoked
  directly** (see Precedence above). Even
  if you can infer everything from PRD + scenario_variables, asking
  3-4 questions catches the surprises ("oh actually we care about X")
  that pure inference misses.
- Do not invent failure modes the user didn't endorse. The intent
  should reflect what the customer *wants caught*, not every
  failure-mode buzzword you know.
- Do not skip the schema reference. Authoring without it produces
  fields the backend SKILL doesn't know how to interpret — wasted
  backend spend.
- Do not author v1 (`goals` / `must_catch` / `custom_metrics`). It is still READ, so a file the
  customer already has keeps working; what it is not is a shape you write. The two are never
  mixed in one file.
- Do not write a `describes` with abstract phrasing like "the agent should be helpful" — that
  produces vacuous evals. Push for concrete, observable failure shapes.
- Do not leave a failure mode untraced, and do not invent a `prd` reference for something the
  customer asked for out of the blue. `source: customer` is the honest value and the backend
  tiers on it; a fabricated `ref` reads as verified and is not.
- Do not block on the user's silence. If they respond "I don't have
  a strong preference" to question 4, record `preferences:` as an
  empty block and move on.
