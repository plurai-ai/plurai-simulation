---
name: ground-scenarios
description: >-
  Route real material (production conversations, real records, or both) to /trace-to-scenarios or /upload-scenario-bundle, then hand off to /generate-scenarios. Run after /simulation-scan-agent when scenarios should come from real data.
---

# Ground Scenarios

The customer has REAL material and wants generated scenarios grounded in it. Two lanes do
that, and the difference between them is what the material IS, not what the customer wants:

| The material | Lane | What the scenario gets |
|---|---|---|
| Real production CONVERSATIONS (thread exports on disk) | `/trace-to-scenarios` | Scenarios that RESUME each conversation — the agent starts turn 1 with the real transcript and state already restored, and the user-sim continues from there |
| Real RECORDS from the agent's own data source | `/upload-scenario-bundle` | Scenarios whose preconditions ("current page", "selected record", "active account") reference a real datum, not an LLM-hallucinated one |
| BOTH kinds | both lanes, into one shared dir | ONE set holding resumed folders beside hand-authored ones — and, when a conversation and its records belong together, one folder holding both — see "Both kinds" below |

This skill is a ROUTER, not a merge. It picks the lane — or, for a customer with both kinds
of material, the ORDER the two lanes run in and the dir they share — then reads and follows
each lane's skill in full. The lanes keep their own names and contracts, and those contracts
are opposites where it matters: the bundle lane authors its folders by hand and reads every
record it selects, while the trace lane never opens its input files at all (the SDK converter
owns every byte of format knowledge). Nothing in this skill weakens either contract, and
running both does not blend them — it publishes their outputs side by side.

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

## Routing — detect first, ask only when ambiguous

Detect the lane from what the user already said and named:

- Words about CONTINUING real conversations — resuming a stalled thread, "pick up where it
  left off", handling the follow-up after a real escalation — or named export files (thread
  exports from an observability platform sitting on disk) → `/trace-to-scenarios`.
- A named data source (the fixture / index / DB the agent's tools read) or precondition
  fields ("current page", "selected record", "active account") → `/upload-scenario-bundle`.
- BOTH signals, meant for one suite → the both-lanes route below. Both signals is not a conflict
  to resolve; it is its own answer, and picking one of them silently discards material the
  customer told you about.

When neither signal appears, or the customer names both but you cannot tell whether they
want one suite or two, ask ONCE (inside a driving command's flow window, sanction it first:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` — what material
exists is a prerequisite only the user can supply, not a decision you can make for them),
offering exactly these options:

1. **Conversations to continue** — real production dialogs the agent should pick up
   mid-flight → `/trace-to-scenarios`.
2. **Records to start from** — real data a scenario's precondition should reference at
   turn 0 → `/upload-scenario-bundle`.
3. **Both kinds of material** — real conversations AND real records, in one suite → the
   both-lanes route below.
4. **Unsure** — explain the lanes in one line each (resume a real conversation vs. open a
   new conversation on a real record vs. one suite doing both), then route on their pick.

Then run the chosen lane's skill and follow it to completion:

- `/trace-to-scenarios` → the `trace-to-scenarios` skill
- `/upload-scenario-bundle` → the `upload-scenario-bundle` skill

The lane's own preconditions, gates, and error posture apply from that point — including
the trace lane's resumed-aware `seed_state` gate and its `anonymized`-posture refusal
(relayed verbatim, no workaround).

## Both kinds — ONE set

A customer with real conversations AND real records does not have to choose between them,
and does not run this skill twice: one scenario set holds resumed folders and hand-authored
folders side by side, generated together. Say so when they ask for both.

**Say the raw-posture requirement first, and check it before any folder is authored.** A set
with converted folders in it carries resume pins, so it is raw-only exactly as a pure trace
run is — the converter refuses under `anonymized` posture. Read `plurai_sim/plurai.config.yaml` for
`anonymize: true` up front: authoring the hand-written half and hitting that refusal
afterwards throws away real work the customer did with you. If the key is present and
`true`, stop there, relay the lane's refusal **verbatim**, and offer the record lane as a
separate, differently-scoped run. **Never quietly drop the conversations and deliver the
records alone.** A plain-only set answers a question nobody asked, and it looks like success.

The two lanes then run into ONE shared staging dir, in this order, each entered at a point
its own skill defines:

1. **`/upload-scenario-bundle`, author-only entry** — selects the records and authors the
   hand-authored folders into the shared dir, then stops before publishing.
2. **`/trace-to-scenarios`, convert-only entry** — gates on `seed_state`, converts each
   export into the SAME dir, then stops before ingesting. It rewrites the root `BUNDLE.md`
   on every run, covering both kinds of folder; carry that file to leg 3.
3. **`/upload-scenario-bundle`, publish-an-existing-dir entry** — ONE `ingest-bundle` over
   the shared dir, ONE `upload_scenario_bundle_start`, ONE `set_id`, one marker write. Leg 2
   leaves a `BUNDLE.md` at the root of the shared dir; `ingest-bundle` excludes it from the
   archive, so leg 3 MUST read it and pass its content as `overview_md` on the start call.
   That call is its only route to the backend, and dropping it does not fail — it publishes
   a set missing the instruction that the openers and policies address what REMAINS of each
   arc, which is the thing the resumption Verify checks.

**Pairing at the FOLDER level is opt-in, and the converter has to be told.** By default leg 2
writes each export into its own `thread-<name>` folder beside the hand-authored ones, which is
right whenever a conversation and a set of records are separate scenarios. When one
conversation and one folder of records belong to the SAME scenario — the transcript refers to
those very records — leg 2 converts that export with `convert-trace --into <folder>` instead:
the pin and transcript land inside the folder leg 1 authored, and its `ROLES.md` is APPENDED
to, so the description the customer wrote with you stays intact above the resume section. The
flag pairs ONE export with ONE folder that must already exist, so it runs once per pairing and
the unpaired exports convert over the same dir without it. `/trace-to-scenarios` Step 3 owns
the contract; the router's job is to carry the folder name from leg 1 to leg 2.

**A non-LangSmith export needs `--source-format` on every leg-2 call**, naming the workspace
adapter under `plurai_sim/plurai_adapters/`. Nothing detects one, and the default reader is
LangSmith's, so an in-house export converted without the flag is refused as malformed — every
export in the run, which reads as broken traces rather than a missing flag. If the customer
has no adapter for their shape, `/author-trace-adapter` writes one; route there first and
come back with its `format:` name.

Use a staging dir belonging to NEITHER lane — `plurai_sim/grounded_scenario_bundle` — and start
it empty. Both lanes' own defaults (`scenario_bundle`, `trace_scenario_bundle`) may still
hold folders from an earlier run, and ingest takes the whole tree, so reusing one silently
republishes them into this set.

**The order is a constraint, not a preference.** `convert-trace` rewrites the set-level
`BUNDLE.md` unconditionally from the folders it finds on disk, naming both kinds. Running it
LAST is what makes that overview describe the whole set. Author a folder after it and the
overview no longer matches the dir — **re-run the converter** over the same dir and the same
exports to bring it back in step. For the same reason, do NOT hand-author a `BUNDLE.md` here:
the converter owns that file and replaces it without saying so.

Every folder in the shared dir needs a readable `ROLES.md`, the hand-authored ones included.
The converter refuses the run and names the folder that lacks one, and the backend would
refuse the whole upload over it a stage later.

Read both lanes in full before starting; both sets of preconditions apply. Running both waives
nothing — a resumed folder still needs the `seed_state` gate (a blank one scores as an agent
failure whatever sits beside it), and a hand-authored folder still needs its grounding
verified after generation. Each lane's Verify step covers its own folders and neither covers
the other's, so run both.

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

## Hard rules

- **Never generate the scenarios yourself.** Both lanes end by writing the
  `plurai_sim/.scenario_bundle_id` + `.scenario_bundle_augmentation` + `.scenario_bundle_intent`
  markers — all three; the scenario build skips a bundle whose intent line is missing — and
  telling the user to run `/simulation-generate-suite`, whose scenario build reads them. Hand off
  and stop — the user runs it manually.
- **One SET per run.** The `.scenario_bundle_id` marker is last-write-wins: a second run
  replaces the active set, and the next `/generate-scenarios` builds from whichever run
  wrote last. This is why both kinds of material go into ONE set rather than two runs
  — two runs would keep only the second. Deliberately building two sets means generating
  between them.
- **A folder holds both kinds only through `--into`.** A set holds folders of both kinds
  freely, and one folder may hold both — but only when the converter is told to merge.
  `convert-trace --into <folder>` writes the pin and transcript into an existing hand-authored
  folder and APPENDS a resume section to its `ROLES.md`, leaving the description the customer
  wrote with you in place above it. Without the flag the converter refuses to write into a
  pinless folder of the same name, because writing that `ROLES.md` unasked would overwrite it.
  Never assemble the shape by hand: a `resume_pin.json` dropped into an authored folder carries
  no appended resume section, so the set overview ends up describing a folder whose `ROLES.md`
  says nothing about the conversation it now holds.
- **Not this skill:**
  - Analysing traces for behavioural patterns or a grounding brief → `/analyze-traces`
    (trace ANALYSIS, not resumption — a different lane that does not touch this one).
  - Real document FORMATS so generated documents template from them → `/upload-doc-examples`.
  - A knowledge corpus as retrieval ground truth → `/build-rag-corpus`.
