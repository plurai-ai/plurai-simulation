# Error posture — canonical source

This file is the one place to edit the error posture. Every `skills/*/SKILL.md` carries a
generated copy of the block below, between `<!-- BEGIN error-posture … -->` and
`<!-- END error-posture -->` markers.

**Why a copy and not a pointer.** Claude Code loads only the invoked SKILL.md; there is no include
mechanism. A pointer to this file binds only if the model chooses to Read it at the moment a job
fails, and a skill that skips that Read would re-run a paid job or relay a raw error. So each
skill carries the rule itself, and the copies are generated, never hand-edited. The block is
written for a Claude 5-series model: rules only, one idea per bullet. Rationale and worked
examples live below the block, in this file only.

This file is the one source of the block. The plugin's development repository regenerates every copy from it
and fails when a copy differs, when a skill lost its markers, or when a job-starting skill has no copy. Skill-specific
scoping notes go after the END marker, inside the same section.

<!-- BEGIN CANONICAL BLOCK -->
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
<!-- END CANONICAL BLOCK -->

## Rationale and examples (maintainers only; not copied into skills)

- **Why only transient failures are retried.** A retry re-sends the same inputs to the same
  backend, so it helps only when the failure came from neither. The backend retries provider
  timeouts and 5xx inside the job; one that reaches the skill has spent those retries, and a
  second run doubles the wait for the same result.
- **Why a transport failure waits 45 seconds and ends on the unavailable stop.** A reset, a timeout or a bare
  `502` / `503` / `504` is the service being out of reach, not a fault Plurai must look at, and a call made at once
  usually meets the same outage. One wait, then one re-call; a second failure tells the customer the service is
  unavailable and to try again, which is true and needs no one at Plurai. The contact hand-off is reserved for typed faults.
- **Why a fixed re-run comes before the hand-off.** An error that names something this side can change (a parameter,
  a stale local artifact, the corpus's shape) is not a fault on Plurai's side, and handing it off wastes a customer's
  report. One fixed re-run bounds the cost: a second failure of the same call is not ours to fix by trying again.
- **Why a `watchdog` stop is retried once.** The watchdog stops a job that went silent, not one
  whose inputs failed, so the same inputs usually run clean. A second stop of the same step is
  final, so a job that really hangs costs at most one extra watchdog window.
- **Why a lost `*_start` is re-called once, and no other write is.** A start lost in transport
  may still have started its job, and a blind second call would be a second paid run. Two things
  make the repeat safe. The backend merges an identical start that arrives while the first is
  still in its handler (no window after it finishes, since a deliberate identical repeat must
  start fresh). The stdio bridge (Copilot, Codex) also keys every call, and re-sends a cut one
  under the same key only for a tool the backend lists with `idempotentHint: true`, so the backend
  returns the first call's result; the model's own re-call carries a fresh key. The one case left
  is a reply lost after the start finished with no hinted re-send: rare, and bounded to one extra
  run. A refusal naming an already-open session is the
  backend saying the first call ran. Every other write is unkeyed: a re-sent edit turn is a second
  edit, a re-sent `end_edit_session` commits twice, `score_run` re-spends judges,
  `diagnose_mock(session_id=None)` opens a second session, and `edit_scenario_gt` applies twice.
- **A skill's error table may add a retried case** only with the reason a second run can differ:
  an artifact drawn fresh each run (a generated mock, a fresh mock process), or a stall across a
  whole batch.
- **Provider refusals, as the backend relays them:** `SDK turn failed (HTTP 403): This model
  version is not available for this account. Contact AWS Sales to request access.`,
  `SDK turn failed (HTTP 403): Model not permitted`, `SDK turn failed (HTTP 403):
  AccessDeniedException … is not authorized to perform: bedrock:InvokeModel`. Not refusals: an input refusal quoting the
  customer's flows (`field credit_limit missing`), a job-inputs refusal (`run-artifacts set …
  yielded no analysable scenario`), and a provider rejection of a request the backend built
  (`SDK turn failed (HTTP 400): prompt is too long`, `SDK turn failed (HTTP 401): invalid bearer
  token`). The development repository's tests run this reading over those
  cases. A typed backend field for this cause would replace this reading.
- **Why a credit refusal stops the flow and is not handed to Plurai.** The refusal is the customer's credit
  standing (or, for `credit-unavailable` and `credit-refused`, a fault Plurai is alerted to by the refusal itself),
  so nothing the skill can do changes it: a retry spends the same credit again, a new login or key is not the
  cause, and another model is a backend decision. The words come from one table that the SDK's terminal output
  shares (`scripts/stop_message.py` mirrors `plurai/credit.py`, compared by a parity test), so the customer reads the
  same sentence from the runner and from the assistant. Only the kinds a payment fixes name the billing page, and only
  they print a continue line: `credit-unavailable` is the contact text alone, since the customer has nothing to pay or
  wait for. Its halt is still the liftable one: the credit-state read is transient, so the customer's next message runs
  the stopped step again.
- **Why the AWS prefix is dropped.** The backend's CLI prints `AWS authentication failed ·
  refresh your AWS credentials` before the provider's text. It describes the backend's own
  credentials; the user has nothing to refresh.
- **Why no skill carries a mode that unlocks raw backend output.** A prompt rule cannot hide data the
  client already received: the data sits in the user's transcript and artifact files either way. The
  backend sends customer-safe payloads only, and staff read raw detail server-side by job id.
- **Why a blocking ask is required.** Autonomy covers decisions, not material only the user can
  supply. Spending the rest of a flow to hand over something the user cannot use is the worse
  failure.

## Run stage and the scored run (maintainers only; not copied into skills)

Every `run.json` carries its `stage` (`qualification`, `final`, `verify` or `repeat`), stamped by
`build_run_json.py --stage` and kept on a rebuild. `scripts/runs_index.py write "$WORKDIR"` writes
`artifacts/runs/index.json` after every run build, and its `scored_run` is the one run a report, a
verdict or a fix plan scores: the last `final`, or none. A stage is never read off a folder name or
guessed from a run's shape; the fix plan names its verify and repeat runs with `--add NAME=STAGE`,
which wins over the stage the run step stamped. A run with no known stage is indexed with `stage:
null` and is never the scored run.
