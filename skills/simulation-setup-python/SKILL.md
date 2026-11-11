---
name: simulation-setup-python
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Internal phase of simulation setup for a Python agent (LANG is python): simulate.py template, mock contract tests, smoke run. Reached only from simulation-setup Step 10. Never invoke directly; use /simulation-scan-agent.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Simulation setup — Python generation phase

This skill is the Python tail of `simulation-setup`. Preconditions:

- The `simulation-setup` skill's Steps 0–9 have already run: confirmed analysis
  (Steps 1–7), `plurai.config.yaml` (Step 8), and `simulation_prd.md`
  (Step 9) all exist on disk.
- `LANG = "python"` was detected in Step 0.

Run Steps 8, 9d, 9e, and 10 below in order. When Step 10's smoke succeeds,
return to the `simulation-setup` skill, which is already loaded in this session, and
continue at the chained-skills section (Step 11).

**Note on step labels.** The numbers below (8, 9d, 9e, 10) are this
sub-skill's *internal* labels — the same numbers these sections had in the
pre-split monolithic SKILL.md, kept for continuity of cross-references.
They are scoped to this file and do not collide with the dispatcher's step
labels (the dispatcher's Step 8 is `plurai.config.yaml` generation).

**THE PAYLOAD CONTRACT — the one thing that silently invalidates a whole run.**
Per turn the SDK child sends the NEW user side and nothing else, never the conversation
so far — normally `payload = {'messages': [ONE new HumanMessage]}`; `{'messages': []}` on
the opening call at a bench that declares `agent.io: {may_open: true}` (the agent opens,
so turn 0 has no user side, and a harness that indexes `[-1]` blind dies there); and on a
turn carrying an attachment still ONE message, whose `content` is a list of parts rather
than a string (a `file` part carries `file.file_data`, an `image_url` part `image_url.url`,
each a `data:` string; decode them with `references/document_input.py`, and declare the kind
in `agent.io.input` or the SDK fails the scenario as `harness_undeclared_input`)
(`**agent_context` is spread in on TURN 1 only). **History is YOUR harness's job**,
keyed on `config['configurable']['thread_id']` (stable per scenario): an agent on a
graph framework compiles with that framework's saver, any other harness keeps its own
thread-keyed store. A harness that rebuilds history from `payload['messages']` runs **stateless** —
every turn sees `[system, user]` — and nothing flags it: the run completes green and
every memory / journey / continuity verdict is meaningless (observed live for a full
qualification pass). Full contract, with the sync/async signatures:
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/example_simulate.py`.

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


## Generation — Step 8: Generate simulate.py

**The full annotated template is `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/example_simulate.py` — read it and fill every `{placeholder}` from your Step 1–7 analysis.** Keep the ownership header (the comment at the top of the template saying the file belongs to the user) on every file you write from it. That covers the files from `mock-contract-tests.md` and `large-simulate-split.md` too. It carries all five phases (Phase 1 stubs → Phase 2 configure + `@plurai.mock` descriptors → Phase 3 `apply_mocks` + storage monkey-patch → Phase 4 import-agent-as-is → Phase 5 `invoke_agent` + `seed_state`) with inline comments.

**The agent's settings file.** When `bench_manifest.json` declares `harness.env_file` (a file the user named for the agent's settings), Phase 1 loads that file: `load_dotenv(Path(__file__).resolve().parent.parent / '<value>')`, the value written as declared (an absolute one stays absolute). Before asking for a model-provider key, run `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" env-check <NAMES> --providers`: a name it reports found is never asked about, and when the harness makes the model call itself, a provider it reports found (default credential files count) is used, the agent's own first; ask only when none is found, naming where it looked.

**Key principle: mock the tools, then import the agent.** The agent picks up mocked tools through normal Python import resolution. No graph rebuild. No architecture rewrite. The agent runs exactly as the customer wrote it.

**Gotchas that are NOT obvious from the template (keep these in mind while filling it in):**

- **Async APIs need `AsyncMock`, not `MagicMock`.** `await sys.modules["X"].do_thing()` fails with `TypeError: object MagicMock can't be used in 'await' expression`. Build the stub explicitly: `mod = MagicMock(); mod.do_thing = AsyncMock(return_value=None); sys.modules["X"] = mod`. For `async with X(...)`, monkey-patch the function to a `contextlib.asynccontextmanager` no-op.
- **`isinstance()` against a stubbed-module class needs a REAL empty class.** `isinstance(obj, stubbed_module.SomeClass)` crashes with `TypeError: isinstance() arg 2 must be a type` because the attribute is a MagicMock. Promote the stub: `mod = types.ModuleType("X"); mod.SomeClass = type("SomeClass", (), {}); sys.modules["X"] = mod`.
- **Module-level reassignment of a stubbed FUNCTION doesn't reach `from module import func` consumers.** To neutralize a specific (e.g. async) function in a stubbed module, patching only `stubbed_module.func` is not enough — also re-bind it on EVERY module that did `from stubbed_module import func`: `import stubbed_module, consumer_a, consumer_b; stubbed_module.func = _noop; consumer_a.func = _noop; consumer_b.func = _noop`. Python's import binding doesn't follow module-level reassignment, so each consumer keeps its own copy of the original reference.
- **A harness class with pydantic fields is fully defined at import.** A tool or model subclass you rebind into an agent module (`agent_mod.Tool = SimTool`) is built in the agent's frames, where a string annotation cannot resolve: keep `from __future__ import annotations` out of simulate.py, or call `SimTool.model_rebuild()` right after defining it.
- **Directory naming: use `plurai_sim/`, never `plurai/`.** A `plurai/` directory shadows the SDK package on `sys.path`, so `import plurai; plurai.configure(...)` fails with `AttributeError: module 'plurai' has no attribute 'configure'`.
- **`apply_mocks` runs BEFORE the agent import**, and every tool the agent uses appears in the dict (default `"mock"`; `"observe"` to run + capture; `"real"` for no-external-dep tools). `from tools import func` copies the reference at import time — patch first, import second.
- **`invoke_agent` passes the run settings `simulation-setup` Step 2 resolved from production's caller, step cap included**, citing the line; never raise or add a cap production does not set, because the cap is per request and a turn that hits it here hits it in production. It NEVER wraps with `asyncio.run(...)` (the orchestrator already drives an event loop). Same rule for `seed_state`.
- **`invoke_agent` drives the agent from its real entry, so its own router runs.** When the harness must drive a sub-graph below a router that reads a declared `state_updates:` key, call the router's own node function for the gate decision AND for its reply. When that node needs anything the harness cannot call, raise `plurai.HarnessFault` naming the unreachable turn. Never write the refusal text yourself.
- **Reading scenario state inside a `@plurai.mock` handler:** `ctx.world_state.get_entity(id)` (single), `ctx.world_state.query("type", **filters)` (list), `plurai.world_state_snapshot()` (full snapshot). NEVER `isinstance(ctx.world_state.entities, dict)` — always False; the verifier rejects it.
- **A `@plurai.mock` body gives every parameter a default, and returns a failure the real tool reports: `return {'status': 'error', 'error': '<the real tool's message>'}`.** A call carries only the arguments the agent passed, so a parameter with no default fails every call that leaves it out. A tool declared `error_channel: raises` throws that message to the agent; any other tool gets the failed call as its wrapper reports one (`mock_wrap` hands back the dict, or `failure(e)` when one is given). Never raise `MockToolError` from a mock body: the engine reads any raise there as a handler crash, and the agent sees `handler raised: …` instead of the tool's message. Step 9e warns (`mock_tool_error_raised_in_mock`, and `mock_tool_error_arity` for a `MockToolError(...)` call the installed SDK's signature refuses).
- **Never flip `mock_mode=True` (or any in-tool mock-flag) on the customer's settings as a shortcut** — even if a tool has a built-in `if settings.mock_mode: return canned_data` branch. (1) In-tool mocking ignores the backend mock-env entirely — every scenario sees the same canned data, defeating the cross-scenario evaluation that is the whole point of the backend; (2) the Phase-3 `apply_mocks(...)` wrap layer covers tools that DON'T have a built-in mock branch (third-party tools / helpers / anything you didn't author) — the wrap is uniform, per-tool flags are not; (3) with `apply_mocks(...)` in place the wrap intercepts BEFORE any tool body runs, so a built-in `mock_mode` check becomes dead code at simulation time. Construct the agent with production-style settings.
- **You may leave a `@plurai.mock` handler unimplemented and still get a shape-correct run.** Until you fill a handler in, the SDK substitutes a schema-shaped stub matching the tool's declared return type (`[]` for `list[T]`, `{}` for `dict[K,V]`, a zero-filled Pydantic dump for models). Each handler you fill upgrades that tool from "shape-correct stub" to "scenario-driven response" — no other code change needed. So a partially-implemented mock set is still runnable; fill in the handlers whose data a scenario actually depends on first.
- **A mocked capability the agent switches off at startup is the harness's to catch.** When building the agent runs its own startup check against a service the bench mocks (a prerequisite or health check, a connect, a ping) and switches that capability off when the check fails, build the agent in `seed_state`, once per conversation, then read the agent's own record of what is up for each mocked capability: its status call, or the tools it will offer the model. Decide from the mock's answer to that check, recorded at the seam that routes it, never from the agent's error text. A check the mock answered with a failure is the scenario's world: the agent starts without that capability and is graded on how it handles it, never a `HarnessFault`. Any other mocked capability that is off (the mock answered with success, or had not answered yet) is the simulated service's latency, not the scenario's world: rebuild once, and if it is still off raise `plurai.HarnessFault` naming each capability and the status the agent reported. A conversation never starts with a mocked capability silently switched off.


- **`invoke_agent` returns the turn's whole chain, with each model call's model and usage.** Trace sinks (LangSmith `llm` runs, Langfuse generations) draw one model call per assistant message of the returned `messages`. A LangGraph / LangChain result already carries `tool_calls`, `response_metadata` and `usage_metadata`: return it as it is. Any other framework (Strands, a hand-written OpenAI / Anthropic loop, the OpenAI Agents SDK) builds the chain through `references/model_calls.py`, copied beside `simulate.py` as `_model_calls.py` — assistant messages with their tool calls, the tool results, the final reply, and per message the model and token counts the framework reports (worked turns in `references/framework-patterns.md`). A value the framework does not report stays out: never a configured default or an estimate. Each assistant message's `content` is what the product shows for that message: apply the product's own display transform (cite it) to every assistant message, not only the final one, and give a message whose text the product never shows `''`; its tool calls, model and usage stay, and the trace then shows those steps with no text. A hand-built `{"role": "assistant", "content": reply}` shows the turn as one call with no model and no usage; Step 9e warns (`assistant_message_without_model_metadata`). The sinks draw these model calls from SDK 0.28.15 on; an older runner records the turn without them, so a chain built this way loses nothing there.

- **A server YOUR harness spawns writes its log where YOU can read it afterwards.** The SDK
  launches `simulate.py` and promotes THAT child's stderr; a process `simulate.py` starts is
  outside its observability, so a mid-dialog 500 leaves only the message your harness raises.
  Open ONE append-mode log under `plurai_sim/artifacts/` (create the directory — it is
  gitignored), give the same handle to `stdout` and `stderr` of the spawn, and quote that file's
  tail in the error you raise on a non-2xx reply: the SDK persists that message verbatim into the
  scenario's `error.json`, which is why the status, the body and the URL have to be in it. The
  Step 9e verifier warns on a spawn that discards its output (`spawned_agent_output_lost`) and
  refuses one that leaves its stdout on the SDK's IPC channel (`spawned_stdout_on_ipc_channel`) —
  the verdict table in Step 9e.3 carries what each one costs.

- **A write lands in ONE `world_state.apply` — pointers included.** Everything a single tool call
  changes goes in one call: the new entity AND every pointer that must move with it. A versioned
  write (a new revision / version / commit that becomes the current one) sets `is_head: True` on the
  new entity and `is_head: False` on the previous one **in that same call**, so the world is never
  observable with two entities claiming to be current; a parent's index or pointer field moves in the
  call that creates what it points at. Every read still resolves when this is broken, so nothing
  fails loudly — the agent, the judges and the deterministic checks just see an incoherent world.
  An entity that exists is written at the key `plurai.world_state.entities` holds for it (`query()`
  returns payloads without keys) — never a key computed from its fields — and a new one carries its
  `_entity_type`. The mock refuses any other write, and the row lands `not_valid`.
  (The full invariant set, including parent→child index arrays and single-source-of-truth for
  inline rows, is in `references/large-simulate-split.md`; this one applies at every size.)

- **Code the harness runs inside one of the agent's tool calls (a mock seam, a write mirror) never
  waits for the agent's runtime to go idle** (a join on every pending task). The agent may run one
  model response's tool calls as sibling tasks, and each such wait waits on the others, so the turn
  hangs. Yield with `await asyncio.sleep(0)` instead, or settle after the turn returns.

- **A file in `world_snapshot()` is one `plurai.file_row(path, entity_type=...)` call** (`preview=False`
  for a file handed to the agent), never a document reader of your own.


### Step 8.4: When `simulate.py` grows — split into 3 files

**When `simulate.py` exceeds ~600 lines, split it into `simulate.py` + `_storage.py` + `_mocks.py`.** The full 3-file layout, the storage pass-through a real tool's store swap uses (each method delegates to the seam mock that `serves:` the tool and translates names, types and errors — never stores rows or computes outcomes; a versioned store's HEAD, numbering and snapshots are the seam mock's contract, `references/mock-contract-tests.md`), the document-extraction hook, and the mock-dispatch pattern choice (dispatch-wrapper vs coroutine-swap, and the observability consequence) all live in **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/large-simulate-split.md`** — read it when you cross the threshold. Below it a single `simulate.py` is fine; a long storage class is never a reason to split — it means the harness is implementing the store.

Use the EXACT helper filenames (`_storage.py`, `_mocks.py`) so the Step 9e verifiers auto-discover them; a near-miss name (`mocks.py`, `storage.py`) triggers a `helper_naming_convention_drift` warning and the helper is skipped.


### Step 8.5: `seed_state` hook contract (Python only)

This step is the Python-specific implementation of the **state-surfaces audit** the dispatcher's Step 6.5 told you to do. If that audit found essential state surfaces (storage / cache / repository / env-driven HTTP that ≥1 tool reads from), you MUST seed them through the `seed_state` hook before turn 1. Otherwise tools fail their first read, the React loop retries, and the retries exhaust the framework's step budget (on LangGraph, `GraphRecursionError`). If the audit found nothing beyond mock-env world state + the agent's own framework state, **skip this step** — `seed_state` is optional.

**Read `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/seed-state-contract.md` when you need to implement `seed_state`.** It carries the full contract: the module-level signature + auto-discovery (no wiring step), the honour-both-parameters rule, the one pattern the implementation must get right (`initial_world_state['entities']` verbatim when present, synthesise from `initial_agent_context` only when empty), the empty-HEAD `agent_context`→inline-records reconciliation, the no-`clear()`/no-singleton rationale, and the worked example.

The load-bearing rule to remember here: **apply `initial_world_state['entities']` verbatim as ground truth when present** — synthesising a fresh `__r0` from `initial_agent_context` instead discards the scenario's history and the agent's first read sees zero records → `GraphRecursionError`. The Step 9e verifier catches the violation as `seed_state_ignores_world_state`.

**Every `seed_state` declares a THIRD parameter, `resume_seed: dict | None = None`, and HANDLES it** — not just the trace-driven ones. The SDK calls the hook with three positional arguments unconditionally and passes `None` when the scenario is fresh, so a two-parameter `seed_state` raises `TypeError` in the `Init` handshake and surfaces as a bare `InitError` on every scenario. When the value is non-`None` it carries the transcript + agent state the agent must resume from, and — when the operator converted the trace with `--start-node` — a `resume_point` naming the top-level graph node turn 1 must begin at. There is no fresh-only declaration to make: `plurai convert-trace` writes a resume pin into **every** bundle it converts and no config key says otherwise, so any env may be handed a transcript. **Replay it** — the Step 8.5 worked example does — read `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/resumed-seed-state.md`, which holds the four steps every harness runs, one worked example per harness shape (a graph framework's `convert_to_messages` + `aupdate_state(as_node=…)` and its `next == ()` tripwire, a thread-keyed store of your own, a session constructed per scenario), and the misfires that stay green (writing to a handle `invoke_agent` never reads, a container too narrow to hold `agent_state`, a thread left mid-step). **Replay by default; refuse only under a declared `harness.resume: {mode: unsupported}`** (`seed_state_refuses_every_seed`). Keep the replay LAST in the body and let no branch above it return, or a resumed scenario that takes an early path is seeded fresh with nothing to show for it. Restore the `agent_state` keys the agent has a slot for — a pending question the next message answers is one, even when it was returned as a report — and skip the rest, returning them as `{'resume_state_dropped': [...]}` (a regression replay that dropped any key other than the turn's own report keys (`tool_trail` when the transcript carries the tool exchanges, `host_effects`, `input_context`, `appended_output`, `run_info`) is not measured at its cut) — a seed carries the recorded turn's own report keys and a production trace's channel names, so a hook that raises on an unknown key refuses every replay; raise `plurai.HarnessFault` only when the seed carries no messages. A Claude Code CLI agent (`claude_agent_sdk`) resumes its own recorded session at the cut (`resume`, `resume_session_at`, `fork_session`) — the reference's CLI worked example. What it must never do is accept the seed and run fresh anyway — the agent opens on an EMPTY history, the orchestrator excises turn 1 as already-said, and the run scores a conversation that never happened, green and silent. The Step 9e verifier gates the arity as `seed_state_bad_signature`, flags a body that declares the parameter without ever reading it as `seed_state_ignores_resume_seed`, and flags a body that takes the seed and never names `resume_point` as `seed_state_ignores_resume_point` — **failures** both, since a replay reads what it is handed. The verifier gates the arity but not the **default** — a choice about what this check covers, not a limit on what a checker could see — so the default is on you.

**A vendor double the agent ships (`world_state.confirmation` `answer: agent_fake`) is served by the harness, never mocked.** Copy `_serve_agent_fake` from `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/agent_fake_serve.py` VERBATIM (Step 9e compares it), keep the variable's `*.invalid` pin at import, and in `seed_state`, before the agent's first turn, assign `os.environ["<env>"] = _serve_agent_fake("<target>", initial_world_state)`: it writes the double's inputs (the scenario's rows for each world input) and state into fresh temp folders and serves it in this process for this conversation. Its imports (and the server library) must already resolve in the agent's environment: never install one; when one does not, drop the entry and mock the vendor.

**A refusal the HARNESS makes (a path the bench does not cover) raises `plurai.HarnessFault`; any other raise inside a hook is scored as the agent's.**

**A value the harness was not given is a `HarnessFault`, never a guess.** A context key the scenario schema declares (an `agent_context` field, a required key) that the scenario omitted, or a declared tool the bench has no route for (unwired, unmocked), raises `plurai.HarnessFault` naming it — never a module constant, an earlier turn's value, or the first pending item. A tool the bench routes is the scenario's world when the mock **answered** — an error status, a 'not available', an empty result, an integration the agent then drops — never a `HarnessFault`: the agent is graded on how it handles it, and the harness re-tries only calls that got no answer. A call the mock did not answer is already voided by the runner (`harness_mock_runtime`); the harness raises `HarnessFault` only for a transport it owns that the runner cannot see — a bridge whose client hung up before the answer arrived, or the bridge's own error — deciding from that transport fact, never from the agent's error text. A documented bench fixture the schema does not declare (a default locale, a fixed workspace) stays a fixture. Never adopt the first pending session, channel or request when the scenario named none, whatever the schema says. **The harness logs to raw stderr** (`print(..., file=sys.stderr)`, `console.error` in a Node process it drives), never through the agent's own logger, which the product may silence.

### Step 9d: Generate test_mock_contract.py

**Skip this step entirely when the config's `tools:` is empty (tool-less agent) or no tool is `mode: mock`** — there is no backend mock to accept, so the file would test nothing (the backend already treats it as optional and skips its verification phase when absent). Do not write an empty placeholder.

`test_mock_contract.py` is the **acceptance criteria for the backend's mock environment** — the plugin sees the agent code, the backend does not, so the plugin encodes what each tool must do (inputs, outputs, state transitions, constraints, error cases) as executable tests the backend runs to verify its implementation. These tests call the mock service HTTP API only; they do **not** import the agent code, and they do **not** exercise the orchestrator harness / storage stub / `invoke_agent` / `seed_state` (those surface only in the Step 10 smoke run) — passing them is necessary but not sufficient for a working simulation.

**Read `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/mock-contract-tests.md` to generate this file** — it carries the full annotated `plurai_sim/test_mock_contract.py` template (fixtures + READ/LIST/WRITE tool contracts, tool-chain / enum-constraint / state-machine classes) plus the per-tool-type checklist for filling it in. **Write `plurai_sim/conftest.py` and `plurai_sim/test_agent_envelope.py` too** — the reference's other two blocks. The conftest carries the "did this tier verify anything?" gate (a hook only fires from a conftest: written into the test module it is never called, and the gate would be present and dead). `test_agent_envelope.py` carries the agent-facing half and is LOCAL ONLY — `test_mock_contract.py` is the one file uploaded to the backend, which copies it alone into `simulation_env/tests/` and runs it there without the harness, so an envelope test in there fails on the missing harness and reads as a contract failure: env-gen spends its repair turns on it and withholds the deploy.

**Add, per retrieval / LLM-backed tool** (semantic/vector search, RAG, NL retrieval, ranking — a CATEGORY you recognise from what the tool does, never a field you declare: the backend picks the mock strategy server-side and `ToolDescriptor` rejects any key it does not define), two tests the shape-only template silently misses: (1) a **determinism** test — call twice with identical args against the same seeded session, assert the returned record SET + any `score`/order is identical; (2) an **anti-hallucination** test — seed a small known world, assert every returned identifier is present in the seeded world (the mock must never invent an entity the agent could then "retrieve").

**Then preflight it — exit 1 names a file that does not compile or collect under the gate's own interpreter; fix it
before going on (it ships to the backend, and the mock gate flags every tool it names):**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/tool_python.py" mock_contract "${CLAUDE_PLUGIN_ROOT}/scripts/mock_contract_check.py" \
  preflight "$PLURAI_DIR/test_mock_contract.py"
```


### Step 9e: Verify simulate.py contract compliance (BLOCKING — runs before smoke)

The Step 6.5 audit + Step 8 adapter rules + the seed_state hook are *prose* — and prose-driven generation has variance. Before running the smoke (Step 10), run a static analyzer that converts the contract into a deterministic gate. **Exit 1 means a finding still holds: repair it and re-run, or overrule an `advisory` one the repo shows is wrong (9e.3); do NOT proceed to Step 10 while either verifier exits 1.**

**9e.1 — BOTH verifiers, run out of `${CLAUDE_PLUGIN_ROOT}` and never copied into the bench:**

Do NOT copy a verifier into `plurai_sim/`. A copy is frozen at the plugin version that made it, nothing re-syncs or stamps one, and a verdict from a stale copy is worse than no verdict: a bench carrying a verifier 42 releases behind printed the success line and exited 0 on a config the shipped script refuses. `${CLAUDE_PLUGIN_ROOT}` is set in every session that can run these steps, and `bench_manifest.py` resolves the same scripts the same way.

The two verifiers cover **complementary** surfaces and BOTH must run before Step 10:

- `verify_simulate.py` checks `simulate.py` (wiring contract): is `invoke_agent` defined, is the storage factory wired correctly, no `asyncio.run` in the wrong place, etc.
- `verify_config.py` checks `plurai.config.yaml` (descriptor contract) plus config↔simulate cross-checks: does every tool's descriptor match the Pydantic schema, do write-tools declare `post_conditions`, do `mode: real` tools whose storage is essential get monkey-patched, and — when the config declares mock-needing tools — is at least one tool actually wrapped in `simulate.py` (`tools_not_wrapped`; the config gate means tool-less agents never trip it), etc.

A clean `simulate.py` with a malformed config silently breaks the backend mock generator. A clean config with broken `simulate.py` silently breaks the agent. Both verifiers must exit 0.

**9e.2 — run BOTH verifiers:**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"

# Read agent.tool_content_envelope.kind from plurai.config.yaml first (Step 6). When the
# agent declares one, PASS IT — verify_simulate cannot read the config, so without the flag
# the envelope check never runs and a mocked tool can hand the model a payload shape the
# agent's real tools never produce, silently. Omit the flag only when the key is absent.
# Reads empty when the key is absent, and the `:-json` default then makes the flag a no-op
# (`json` IS the unwrapped behaviour) — so ALWAYS pass it, unconditionally. Do NOT make the
# argument itself conditional (`${KIND:+--tool-content-envelope "$KIND"}`): that expands to a
# SINGLE argument under zsh, which the verifier rejects.
ENVELOPE_KIND=$("$PLURAI_PY" -c "import yaml;a=yaml.safe_load(open('plurai_sim/plurai.config.yaml')).get('agent') or {};print((a.get('tool_content_envelope') or {}).get('kind') or '')" 2>/dev/null)

"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py" plurai_sim/simulate.py \
  --tool-content-envelope "${ENVELOPE_KIND:-json}"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/derive_tool_facts.py" plurai_sim/plurai.config.yaml --root . --check
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_config.py" plurai_sim/plurai.config.yaml --simulate plurai_sim/simulate.py
```

(`.venv` is the client's venv — the one the agent and the `plurai` SDK are
installed in. Install the SDK as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line`
prints, plus `python-dotenv`
(`python-dotenv` because simulate.py must seed `os.environ` itself — the child subprocess
imports simulate.py and nothing else, so anything living only in `.env` is invisible to the
agent and it falls back to code defaults WHILE THE RUN STILL PASSES).)

Each violation prints a `finding: <gate_id> <authority>[ holding] <fingerprint> — <remedy>` line, then the violation by code with the SKILL.md step that documents the fix. Exit 1 means a finding still holds — every `security` / `invariant` one, and every `advisory` one neither repaired nor overruled; a warning (`!`) never holds, and a `!` note on a `type_source: plugin` value is not a violation: leave that value as it is. Exit 2 is no verdict.

**9e.3 — fix every reported violation:**

Each finding maps to one of the rules in Step 6.5 / Step 8. Common verdicts:

| Code | Verifier | What's wrong | Fix |
|---|---|---|---|
| `missing_invoke_hook` | verify_simulate | Neither `invoke_agent` NOR `invoke_pipeline` defined at module scope | Define one based on the bench's mode — Step 8. Dialog bench → `invoke_agent`. One-shot pipeline bench → `invoke_pipeline`. |
| `asyncio_run_in_invoke_pipeline` | verify_simulate | `invoke_pipeline` body calls `asyncio.run(...)` — same running-loop footgun as the dialog hook | Use `async def invoke_pipeline` and await your pipeline call directly — Step 8 |
| `asyncio_run_in_invoke_agent` | verify_simulate | Sync wrapper around an async graph | Make `async def` and `await` — Step 8 |
| `checkpointerless_graph_reuse` | verify_simulate | `invoke_agent` reuses a pre-compiled `.graph` and the module builds no persistence handle for it → cross-turn agent state (a selected record id, cached plan, flags) is dropped between user turns, so a multi-turn agent spuriously fails ("no record selected") and verdicts are corrupted | For a dialog bench, recompile the graph with the saver your framework provides (the test-harness exception to "never add a checkpointer") so the orchestrator's per-scenario `thread_id` persists state, OR carry prior-turn non-message state into the payload — Step 8. Any saver counts, in-memory or on-disk. Single-turn / `invoke_pipeline` benches: ignore (warn only). |
| `asyncio_run_in_seed_state` | verify_simulate | Same footgun, seeding side | Make `async def` and `await` your storage calls |
| `missing_seed_state` | verify_simulate | Storage patched, no `seed_state` defined, and the patched class does not only delegate to declared mocks over a mock-owned world (`start_session` seeds that world) | Define one and apply the scenario's entities (param 2) — Step 6.5 / 8.5 |
| `storage_class_no_world_state_ref` | verify_simulate | Storage patched but no class in the source reaches the mock world: none delegates to a declared mock (a `plurai.mock_wrap(...)` callable or a `mode: mock` tool), none reads `plurai.world_state`. The stub class returns hardcoded defaults; reads bypass the mock service. | Make each method a pass-through to the seam mock that `serves:` the tool (translate, delegate, nothing else) — Step 8.4 |
| `harness_store_serves_world` | verify_simulate | **Warn.** A class or function the harness installs over the agent's code (an attribute assignment onto an imported module or class, `sys.modules[...] =`, `setattr` / `patch`, a storage-factory patch) reads or writes `plurai.world_state` itself, directly or through a helper in the same harness files: the harness implements the store its tools reach. A declared `read_through.via` / `guards[].via` accessor is exempt; a tee (the mock, then the original) and a delegator never touch the world | Put the store under a seam mock that names its tools in `serves:` and make the installed object a pass-through to it, or run the agent's own implementation; what the store does is the mock's contract, pinned by its contract tests — Step 8.4 |
| `world_owner_unreset` | verify_simulate | `plurai.config.yaml` declares `world_state.owner:` other than the mock, so the world this suite scores is the harness's own — and `simulate.py` binds no `reset` at module scope. Nothing clears that world between scenarios, so every scenario after the first runs on its predecessor's writes and the verdicts are about the RUN ORDER, on a run that completes green. This is the $0 preflight for a hold the runner makes anyway: until one of the two remedies, the run is held at start (exit 2, the holding finding `world.unreset_external`) unless that finding is overruled with what was verified (a reset the agent performs itself, read-only tools at file:line). **Warn** on the user's explicit opt-in (`world_state.shared_world_ok: true`): the run proceeds with the named risk `shared_world` | When tools write a live system, mock those writing systems first (`mode: mock`, `status: mocked` on the map). Otherwise EITHER export `def reset() -> None:` at module scope and clear the stores this harness owns — `references/seed-state-contract.md` §reset, `example_simulate.py` PHASE 6c; the child calls it before every scenario, after the mock session opens and before `seed_state` (do NOT clear the mock's own world there — the backend re-seeds it per scenario) — OR, if the deployed mock really owns every record this agent reads, declare `world_state: {owner: mock}` instead — OR, only on the user's explicit opt-in to conversations seeing each other's writes, declare `world_state.shared_world_ok: true`. Step 8. |
| `world_live_writer_unreset` | verify_simulate | **Warn-only.** Tools write a live system (`mode: real`, `write_strategy: real`) and `simulate.py` binds no `reset` — whatever `world_state.owner` says. Each conversation after the first runs on its predecessors' writes, so verdicts depend on the run order. With `world_state.shared_world_ok: true` it names the `shared_world` risk the run carries: the suite caps at qualified, stability and trial agreement read not comparable | Mock the writing systems first (`mode: mock`, `status: mocked` on the map, per system); else export a `reset()` that returns them to the same state before every conversation; keep them live only on the user's explicit opt-in (`world_state.shared_world_ok: true`) — Step 8. |
| `world_owner_unobservable` | verify_simulate | **Warn-only**, and the other half of the same declaration: `world_state.owner:` is other than the mock, so the world this suite scores is the harness's own — and `simulate.py` binds no `world_snapshot` at module scope. Nothing in the run can READ that store, so the only world it could snapshot is the mock's session, which the declaration says is not it. The dialog and every `llm_as_judge` policy are unaffected; every world-reading policy resolves to `world_unobservable` (not-measured) rather than passing vacuously on a store nobody read. Warn, not fail, because the runner also only WARNS (`orchestrator.unobservable_world_warning`, stderr, exit code unchanged) — the run starts either way | EITHER export `def world_snapshot() -> dict:` at module scope returning `{entity_id: {...}}` for the store this harness owns, each file as one `plurai.file_row` call — `references/seed-state-contract.md` §reset, `example_simulate.py` PHASE 6d; the child calls it at finalize; a SPLIT world declares `world_state.owned_types` and returns only its own rows, which the run composes with the mock's rows of every other type — OR, if the deployed mock really owns every record this agent reads, declare `world_state: {owner: mock}` instead. Step 8. |
| `world_owner_unknowable` | verify_simulate | **Warn-only.** The world-owner check did NOT run — no pyyaml on this interpreter, or `plurai.config.yaml` exists and does not parse. Distinct from "nothing declares an owner", which is a real answer: here nothing was read, so an unreset `agent`/`external` bench would NOT have been reported and rc stays 0 on a workspace the runner may refuse | Re-run on an interpreter with pyyaml, against a config that parses — `verify_config.py` names the parse error itself (exit 2). Step 8. |
| `world_owner_conflict` | verify_simulate | `world_state.owner` is declared TWICE and the two disagree. Both rungs reach the runner — the config beside `simulate.py` and the one at the repo root above it — so nothing can say who owns the world, and the run is refused at start (exit 2) rather than letting a discovery order pick the winner | Remove one of the two declarations, or make them agree — Step 8. Name both files when you fix it: the one beside `simulate.py` and the one at the repo root. |
| `world_field_required_undeclared` | verify_config | A declared `world_state` field has no boolean `required:`, so the backend re-authors its requiredness from prose and a field the agent or harness reads without a default can ship optional | Write `required: true` on every field the agent or harness reads without a default (ids, fail-closed storage reads, bare `row["f"]` reads, the record model's non-defaulted fields), `required: false` on the rest — Step 8. **Warn-only**; the warning names the line to write |
| `world_id_field_undecided` | verify_config | A type a `references:` field names declares neither an `id` / `<type>_id` field nor `id_field:`, so the backend keys its seeded rows by position (`<type>_1`): a reference holding a row's real id resolves to nothing, and one holding the positional key never meets the id the agent and the mock join on | Write `id_field: <the declared field that holds the row's own id>` on that `world_state.entity_types.<type>` (`Id`, `pnr`, `bookingId`), or `id_field: null` when it has none — Step 8. **Warn-only**; the warning names the `id_field: null` line that keeps the positional keying |
| `world_id_field_unknown` | verify_config | `world_state.entity_types.<type>.id_field` names a field that type does not declare, so the backend cannot key its rows by it | Name one of the type's declared fields, declare the field, or write `id_field: null` — Step 8 |
| `world_check_refused` | verify_config | A `world_state.entity_types.<type>.checks` entry is malformed, cites the PRD or no data file, reads a field the type does not declare, reads its `in` set off the cited rows themselves, or has a literal or computed rule a real row of the file it cites breaks, so the backend would refuse real-looking worlds (an `in` set that no longer matches its store is `world_check_set_stale`, a warning) | Copy the check verbatim from `measured_candidates.py <agent root> --profile plurai_sim/artifacts/data_profile.md --data <seed or fixture file>`, or drop it — Step 8 |
| `world_rows_untyped` | verify_config | A `world_state` field that holds rows (`list[dict]`) declares no `item_specs`, or a row field in it has no `type`, so the backend types that row field from its name and a seeded row the agent's own record model rejects fails every scenario at Init | Under the field write `item_specs: {<row field>: {type: <type>, required: <bool>}}` for every field a row carries, typed from the record model the harness's read path builds from each row — Step 8. **Warn-only** |
| `structured_context_untyped` | verify_config | An `agent_context` field an `agent.io.channels` entry of kind `structured_context` names is a bare `dict` with no `item_specs`, so a scenario can seed a request object the production parser rejects | Under the field write `item_specs` for every key its value holds, nesting `item_specs` under an object key, read from the schema the request is parsed with on its way in — Step 8. **Warn-only** |
| `world_owner_value` | verify_config | ONE code, TWO shapes, because the runner WALKS to the value and both shapes stop the walk. **(a)** `world_state:` is not a MAPPING — the owner is declared one level in (`world_state: {owner: external}`) because the block also carries `entity_types:`, so a scalar or list there is a key the runner cannot walk into. **(b)** the owner WORD is outside `mock` / `agent` / `external` (`externl`, `harness`, `self`) — the expensive one, and why this fails rather than warns: a typo read as `mock` would reproduce the leak the key exists to end. Either way the run is refused at start (exit 2, before a thread is opened) rather than read as the default owner | **(a)** Make `world_state:` a mapping and put the owner under it. **(b)** Spell the owner as one of `mock` / `agent` / `external` — read the way the runner reads it (`str(raw).strip().lower()`), so `External` and `  mock  ` are fine and `externl` is not. Both — Step 8; `references/seed-state-contract.md` §reset covers what declaring a non-mock owner then obliges the harness to export. |
| `world_slots_value` | verify_config | `world_state.slots` is not a whole number ≥ 1 (`0`, `"3"`, `2.5`, `true`), or sits beside an owner other than `external` / `agent` (undeclared included). The runner refuses the run at start (exit 2) rather than coercing or ignoring it. `slots: null` counts as absent | Write the number of identical store copies the harness provides, beside `owner: external` or `owner: agent`; a store each child holds in its own process needs no `slots` — remove it. Step 8; `references/seed-state-contract.md` §reset. |
| `world_shared_ok_value` | verify_config | `world_state.shared_world_ok` is not a boolean (`"yes"`, `1`). The runner refuses the run at start (exit 2) rather than coercing it, because it decides whether a run on a world nothing resets per conversation is held | Write `true` (the user's explicit opt-in) or remove the key — Step 8. |
| `world_real_writer_unowned` | verify_config | **Warn-only.** A `mode: real` tool resolves to `write_strategy: real` while `world_state.owner` is `mock` or absent: every parallel attempt shares that real store, nothing resets it, and the run scores the mock's world | Mock the writing systems (`mode: mock`, `status: mocked` on the map); else export a per-conversation `reset()`; keep them live only on the user's explicit opt-in (`world_state.shared_world_ok: true`) — Step 8. |
| `storage_class_drops_declared_field` | verify_simulate | The storage class reads world-state but its record projection never reads a field the state schema DECLARES, so a scenario seeding that field writes a premise the agent's own reads cannot return — the agent then ignores it or manufactures it, and the run charges that write to the agent. | Carry the field through the projection, or through the side channel production uses to rehydrate it — Step 8.4. **Warn, and dismissable:** if the schema over-declares a field the agent genuinely does not surface, or the bench carries it by attribute access rather than a string key, note that and proceed. |
| `storage_fidelity_unknowable` | verify_simulate | The check above did not run — no `scenario_state_schema.yaml`, no PyYAML in this interpreter, an unparseable schema, or a schema declaring no list-of-records field. The finding names which. | Warn only, rc unaffected. Install PyYAML into the interpreter you ran (`$PLURAI_PY` resolves the one that has it) or author the schema (`/generate-mock-env`, or `/extract-scenario-variables` Step 4b on the env-gen-skipped branch); then re-run to get the fidelity verdict rather than a silence. |
| `wrapper_drops_response_fields` | verify_simulate | The mock dispatch wrapper — **or a local helper it routes the result through, `_render_content` included** — uses `result.get("message") or str(result)`, dropping every field except `.message` and rendering empty stubs as `'{}'`, which drives the agent into GraphRecursionError cascades. | Render the result's `content` (the real tool's return — the one field the model sees) through `_render_content(result)`, which selects it with `_tool_content` — plus `_plurai_stub_error` detection per Step 8.4 closure-tool dispatch wrapper. |
| `wrapper_drops_state_updates` | verify_simulate | A `mode: mock` tool declares `state_updates:` in `plurai.config.yaml` (the keys its REAL body writes on success, e.g. a `<flag>: True` a later turn gates on) and the mock wrapper never lands them: by the framework's state-write rule its success path writes none of them (a LangGraph `Command(update=...)` that is messages-only; an OpenAI Agents SDK wrapper that never writes onto `ctx.context`), or no `_MOCK_STATE_UPDATES` entry names a declared key. Later turns then run on state production has moved past, and the suite scores turns production never produces. | Copy the declaration into `_MOCK_STATE_UPDATES` and land `_state_update(tool_name, result)` where the real tool writes (LangGraph: `Command(update={**_state_update(tool_name, result), "messages": [...]})`, Step 8.4 REQUIREMENT 4; OpenAI Agents SDK: `setattr(ctx.context, key, value)` per key, framework-patterns.md) |
| `mock_tool_state_writes_undeclared` | verify_simulate | **Warn-only.** A `mode: mock` tool's real body (`descriptor.original_module` / `original_function`, one helper hop deep) writes agent state by its framework's rule — a LangGraph `Command(update=...)` key besides `messages`, an OpenAI Agents SDK `ctx.context.<attr>`, a CrewAI Flow `self.state.<attr>` — and its entry declares no `state_updates:`. The scan never lifted them, so the mock returns its message only and the writes never land | Declare each success-path key under `tools.<name>.state_updates` (`{value}` / `{from_result}`) or `{omit: <reason>}` — Step 8 |
| `state_update_gate_unapplied` | verify_simulate | **Warn-only.** A declared `state_updates:` key is read only by agent modules the harness never imports (a router above the sub-graph `invoke_agent` drives), and the harness never reads it itself. The flag lands and nothing that runs acts on it, so turns production refuses are still scored | Drive the agent from its real entry, or call the router's own node for the gate and its reply; raise `plurai.HarnessFault` when it cannot be called, never a harness-written refusal — Step 8 (`invoke_agent` drives the agent from its real entry) |
| `start_mode_state_unseeded` | verify_simulate | **Warn-only, repaired like a violation.** A boolean `agent_context` field (a start such as "already onboarded") starts the conversation past the agent's own code that sets it, that code also writes a state key, and the agent raises without that key elsewhere — while the harness does not seed it on that start. Every conversation that starts there fails at that read, which reads as an agent fault. | On that start, compute the key before the first turn by calling the agent's own function that produces it (its resolver or loader, over the same mocked stores) and write it with the flag — never a value of the harness's own, and never a new `agent_context` field for it. When the harness already seeds it on that start by a route the check cannot see, say so in one line and change nothing. |
| `tool_state_updates_invalid` | verify_config | `tools.<name>.state_updates` is not a mapping of state key to exactly one of `{value: <literal>}` / `{from_result: <returns field>}` / `{omit: <reason>}`, sits on a tool that is not `mode: mock`, declares `messages`, or reads a `from_result` field neither `descriptor.returns.fields` nor `descriptor.returns.state_fields` (the siblings the model never sees) declares | Fix the entry per Step 8 of the `simulation-setup` skill (`state_updates:`) — Step 8 |
| `wrapper_ignores_tool_content_envelope` | verify_simulate | The agent declares `agent.tool_content_envelope` but the content the wrapper hands back does not implement it: either it SELECTS a field off the result other than `content` via `_tool_content` (any kind — the envelope wraps; `content` is the only selection), or, for `kind: json_field`, it hands back the bare result with nothing wrapped around it. Mocked tools then give the model a payload shape the agent's REAL tools never produce. Checked on the RENDERING, not on the presence of a `_render_content` function — an envelope built inline passes, a `_render_content` that is a bare `json.dumps(result)` does not. Runs only when `--tool-content-envelope <kind>` is passed (this verifier cannot read plurai.config.yaml). | Build the declared shape around the result's `content` — one `_render_content(result)` that places `_tool_content(result)` at the template's `null` — Step 8.4 |
| `envelope_render_not_idempotent` | verify_simulate | `kind: json_field` is declared and the wrapper applies the template UNCONDITIONALLY. A mock handler written against the agent's real tool contract returns the envelope itself, so it is wrapped a second time and the model gets the template nested inside itself — in a run that looks green. The same missing read is why REQUIREMENT 2 cannot see an error the handler put INSIDE the envelope. | Guard the wrap — a result already matching the template shape passes through untouched (`if set(_ENVELOPE_TEMPLATE) <= set(result): ...`) — Step 8.4 |
| `tool_content_envelope_render_indistinguishable` | verify_simulate | Warn-only. `kind: text` is declared but the rendering is the plain JSON/str render — byte-identical to `kind: json`, so nothing in the code distinguishes the declared envelope from the default. That IS what the shipped `text` template emits, so it is a question about the declaration, not a code defect. | Confirm `agent.tool_content_envelope` describes the agent's REAL tool returns; drop the key when it has no convention — Step 8.4 |
| `tool_content_envelope_unverifiable` | verify_simulate | Warn-only. An envelope kind is declared but the wrapper's content rendering could not be traced back to the resolved result, so whether the envelope is applied is unknown. | Route every mock return through one `_render_content(result)` so the rendering is checkable, and confirm the declared kind — Step 8.4 |
| `seed_state_bad_signature` | verify_simulate | `seed_state` doesn't accept `(agent_context, world_state, resume_seed)`. The child calls it with three positional args on EVERY scenario, so a two-parameter hook raises `TypeError` in the `Init` handshake → bare `InitError`. | Change the signature to three positional args, the third defaulted (`resume_seed: dict | None = None`) — Step 6.5 / 8.5. This rule gates the arity only, not the default; omit the default and two-arg callers break instead. |
| `seed_state_ignores_world_state` | verify_simulate | `seed_state` declares `initial_world_state` but never reads it. Synthesises an empty `__r0` revision instead of honouring the scenario's `initial_world_state['entities']`; agent's first read sees 0 records → `GraphRecursionError`. | Apply `initial_world_state['entities']` verbatim when present, fall back to synthesising from `initial_agent_context` only when entities is empty — Step 8.5 |
| `seed_state_ignores_resume_seed` | verify_simulate | `seed_state` declares `resume_seed` but never reads it (or rebinds it first). The converter pins EVERY bundle it writes and no config key declares an env fresh-only, so any env may be handed a transcript: dropping it starts the agent on an EMPTY history while the orchestrator excises turn 1 as already-said, so the run scores a conversation that never happened — green, with nothing in the logs. | Replay the seed into whatever `invoke_agent` reads this thread's history from, then assert the write landed (on LangGraph: `convert_to_messages(resume_seed['messages'])` + `await graph.aupdate_state(config, values, as_node=…)`, then `next == ()`), or — if this agent cannot restore a conversation — fail closed on it (`if resume_seed: raise plurai.HarnessFault(...)`). Both read the parameter; accept-and-drop is the only shape that trips this — references/resumed-seed-state.md |
| `seed_state_ignores_resume_point` | verify_simulate | `seed_state` takes the seed but nothing in the harness names its optional `resume_point`. The key carries the top-level graph node turn 1 must start at, and nobody between `convert-trace --start-node` and this hook interprets it — so dropping it runs the graph from its entry point, the agent redoes the steps the trace already took, and the run scores that as the pinned resume. | On a graph harness HONOUR it: read `resume_seed.get('resume_point')`, refuse a node absent from the compiled graph (an unknown name and a subgraph interior are both SILENT no-ops), and start TURN 1 ONLY at it — `Command(goto=node, update=payload)` as the first invoke, plain payload afterwards. A harness with no graph node to start from REFUSES, naming the node. Suppressed while `seed_state_ignores_resume_seed` fires, and for a blanket `if resume_seed: raise` — references/resumed-seed-state.md |
| `seed_state_refuses_every_seed` | verify_simulate | `seed_state` raises on every resume seed (`if resume_seed: raise`), so no regression replay can run on this harness. **Fail** when `bench_manifest.json` declares `harness.resume` as anything but a typed `unsupported`; **warn** when the manifest predates the field. | Replay the seed (references/resumed-seed-state.md). Only where the conversation lives in a session the harness cannot write, declare `"harness": {"resume": {"mode": "unsupported", "reason": "history_not_writable", "evidence": "simulate.py:<line>"}}` |
| `entities_proxy_isinstance_dict_misuse` | verify_simulate | `isinstance(ctx.world_state.entities, dict)` — always False; the proxy is a Mapping, not a `dict` | Use `world_state.get_entity(id)`, `world_state.query(type)`, or `plurai.world_state_snapshot()` — Step 8 |
| `descriptor_schema_invalid` | verify_config | A `tools.*.descriptor` block doesn't match `ToolDescriptor` (`from plurai import ToolDescriptor`) — typo, unknown key, or wrong variant shape | Fix the field per the schema — Step 8 |
| `write_tool_missing_post_conditions` | verify_config | A `mode: real`/`mock` tool has `writes_to` non-null but `post_conditions: []` | Add explicit post_conditions, OR set `write_strategy: stub` if the writes are dynamic — Step 8 |
| `essential_storage_stub_missing` | verify_config | A legacy `mode: real` reading tool still labelled `read_strategy: mocked` has no storage module in `stubs.modules` | Re-route the store to a seam mock with `serves:` (`world_served_without_mock`), which retires this check; or set `read_strategy: real` if the read source stays real (RAG/vector DB) — Step 8 |
| `essential_storage_not_monkeypatched` | verify_config | A legacy `mode: real` reading tool still labelled `read_strategy: mocked` has its storage declared but `simulate.py` doesn't swap it | Re-route the store to a seam mock with `serves:` and swap in a pass-through to it (Step 8.4); the check then no longer applies — Step 8 |
| `storage_role_undeclared` | verify_config | Warn-only. A stub shares a package root with a `mode: real` reading tool, nothing in the config says whether it is that tool's STORAGE, and `simulate.py` doesn't monkey-patch it — so a real store would pass silently | Declare `role: storage` on the `stubs.modules` entry and monkey-patch it per Phase 3b, or declare its actual role (`role: telemetry`, `role: cache`) — Step 8 |
| `shape_contradicts_hooks` / `shape_value` | verify_config | `plurai.config.yaml` declares `shape:` (Step 8) and `simulate.py` binds the other shape's hook — or the value is neither `dialog` nor `pipeline`. The declaration is what the runner dispatches on, so the bench would be driven through a hook it does not export and die at its first invocation, after generation has been paid for | Fix whichever file is wrong: correct the declaration, or bind the hook the declaration names (a module-scope `def`, an assignment or an import all count) — Step 8. A harness exporting BOTH hooks is fine and the declaration decides. |
| `shape_absent_pipeline` / `shape_declared_not_at_root` | verify_config | `simulate.py` binds `invoke_pipeline` and no `invoke_agent`, and no ROOT `shape:` is declared (or one is declared a level down, under `agent:`, where nothing reads it). The declaration dispatches — the hooks are a hint that never chooses a route — so this bench is driven as a dialog, the turn loop has no hook to call, and every scenario dies on turn 1 after the set has been paid for | Add ONE root key to `plurai.config.yaml`, beside `workspace:`/`project:`: `shape: pipeline`, or move the nested one to the root — Step 8. A bench binding `invoke_agent` needs nothing: absent is still legal there, and a nested declaration that merely restates how the bench is already driven is a `warn`, not a block. |
| `shape_absent_both_hooks` | verify_config | **Warn-only, rc unaffected.** `simulate.py` binds BOTH `invoke_agent` and `invoke_pipeline`, and no ROOT `shape:` is declared. The declaration dispatches — the hooks are a hint that never chooses a route — so the bench takes the default route, the other hook is never called, and every policy scoped to it scores against an empty result: zero verdicts with a clean exit code, so the run is GREEN and nothing in it says the half you meant to test never ran | Add ONE root key to `plurai.config.yaml`, beside `workspace:`/`project:` — `shape: dialog` to keep the route the bench takes today, or `shape: pipeline` to be driven as a one-shot hop — Step 8. Not a refusal: this harness CAN serve the route it is driven on, so the default is defensible and blocking it would be a false red. Both hooks WITH a declaration is the recommended layout and reports nothing. |
| `agent_io_unknown_key` / `agent_io_input_kind` / `agent_io_input_shape` / `agent_io_may_open_shape` / `agent_io_opens_shape` / `agent_io_choice_text_shape` / `agent_io_choice_text_source` | verify_config | The root `agent.io:` block declares something no reader knows: a subkey other than `input`/`may_open`/`opens`/`choice_text`/`choice_text_source` (the EMITTED surface is `render_actions:`, a root key of its own; a value the agent reads from its context belongs under `agent_context:`), a part kind outside `text`/`image_url`/`file` (OpenAI's content-part `type` values, which is what the SDK puts on the turn), a malformed list, a `may_open` that is not a YAML boolean, an `opens` other than `always` (or beside `may_open: false`), or a `choice_text` that is not `{<choice>: delivered|dropped}` with a `choice_text_source: <path>:<line>` beside it. A declaration nothing reads is worse than none: the author believes a channel is covered and no probe ever runs on it | Fix the key, the kind or the type — Step 8. The vocabulary is closed on purpose: the setup smoke sends one probe turn per declared kind, and it has no probe for a kind nothing can send. |
| `agent_io_input_text` | verify_config | **Warn-only.** `agent.io.input` omits `text`. The runner still SPEAKS to the bench — `input:` gates the message CONTENT PARTS and a plain string turn is never refused on a declaration — so nothing dies on a paid run; what it costs is that the setup smoke plans no `text` probe, leaving the one channel every scenario uses confirmed by nothing | Declare `text` beside whatever else the agent accepts — Step 8. Leave it out only for the agent-first bench that is spoken to and handed no parts. |
| `file_input_without_document_field` | verify_config | `agent.io.input` names `file` / `image_url` and there is no `.document_fields` entry beside the config. That entry is the only trigger that has scenario generation author a document, so no scenario can hand the declared one over | Write the entry (the document ask, in the `simulation-setup` skill), or drop the part kind from `input:` — Step 8. |
| `handover_kind_not_declared` | verify_config | A `.document_fields` entry sits beside a dialog-shape config, so scenario generation hands the agent documents as `file` content parts, and `agent.io.input` does not name `file` (or `agent.io:` is absent, which the SDK drives as text only). The SDK fails every scenario whose user line carries a part kind the harness did not declare, as `harness_undeclared_input`, so none of the document scenarios would run | Declare `agent.io: {input: [text, file]}` at the root of `plurai.config.yaml` and make `invoke_agent` read the part (`references/document_input.py`), or remove the `.document_fields` entry if nothing hands this agent a document — Step 8. |
| `document_read_off_payload` | verify_simulate | `invoke_agent`, `seed_state` or a helper handed the payload reads a `.document_fields` field off the turn payload or the `seed_state` context. That key is never there: every document the user hands over arrives as one `file` content part (`<field>.<ext>`, a `data:` URI) on the turn she hands it over, the opener included, so the agent is handed nothing | Read every turn's parts with `turn_documents(content)` (`references/document_input.py`) and hand the agent each `Document(filename, bytes, media_type)` as it came; delete the payload read — Step 8. |
| `input_file_channel_undeclared` | verify_config | A dialog-shape agent reads files the user names — a live file-read built-in on the map, or an `## Agent facts` entry quoting an input-file capability — and neither `file` in `agent.io.input` nor a `.document_fields` entry is declared, so no scenario hands it a file | Declare the by-path handover: `file` in `agent.io.input`, a `.document_fields` entry, and an `invoke_agent` that plants each file in the scenario's `inputs/` and names its path (`references/document_input.py` `plant`). An agent whose reads never take a user's file is overruled in `artifacts/.gate_overrules.jsonl` — Step 8. |
| `agent_io_inert_on_pipeline_shape` | verify_config | **Warn-only.** `shape: pipeline` and `agent.io:` in the same config. A one-shot hop has no conversation — no turn 0 to open, no message parts to carry; its inputs arrive as `invoke_pipeline`'s kwargs — so the block is INERT on that route and the smoke plans no probe for it | Drop `agent.io:` if this bench is only ever a pipeline, or fix `shape:` if that is the wrong one — Step 8. Not a refusal: a harness may export BOTH hooks and `shape` names the route THIS run takes, so a bench driven as a pipeline today can carry a truthful declaration of the dialog route it is also driven on. `shape` is declared and never derived, so nothing downstream reconciles the two for you. |
| `tool_in_config_missing_from_simulate` | verify_config | A tool is declared in `plurai.config.yaml` but no `@plurai.mock("name")` handler exists | Add the decorator-registered handler or remove the config entry — Step 8 |
| `tool_in_simulate_missing_from_config` | verify_config | An `@plurai.mock("name")` decorator exists with no matching `tools.<name>` entry | Add the config entry or remove the decorator — Step 8 |
| `tools_not_wrapped` | verify_config | `plurai.config.yaml` declares at least one mock-needing tool (not `mode: real`) but `simulate.py` contains no `apply_mocks(...)`/`mock_wrap(...)`/`MockToolNode(...)` call and no `<tool>.coroutine = …` override — the agent will call real implementations on turn 1 | Add the Phase 3 `plurai.apply_mocks(<tools_module>, {...})` block (or a coroutine override) — Step 8. Tool-less / all-`mode: real` agents never trip this (the config gate skips them). |
| `no_dotenv_load` | verify_simulate | `simulate.py` never loads a `.env` (no `load_dotenv`/`dotenv_values`) — the child subprocess imports simulate.py and nothing else, so `DEFAULT_LLM__*` / provider keys living only in `.env` are invisible to the agent and it falls back to code defaults WHILE THE RUN STILL PASSES | Call `load_dotenv()` at module import (Phase 1) — `load_dotenv(<harness.env_file>)` when the bench declares one — or export those variables in the environment that launches `plurai-run-simulation`; before asking the user for a key, `bench_manifest.py env-check <NAMES> --providers` says whether it is already set — Step 8. Warn-only: exporting is a legitimate setup. |
| `env_file_not_loaded` | verify_simulate | `bench_manifest.json` declares `harness.env_file` (a file the user named for the agent's settings) and no `load_dotenv`/`dotenv_values` call in `simulate.py` names it, so the agent runs without the file the presence checks found | Load it in Phase 1: `load_dotenv(Path(__file__).resolve().parent.parent / '<value>')`, the value as declared — Step 8. Warn-only. |
| `spawned_agent_output_lost` | verify_simulate | The harness spawns a long-lived process (the agent server) with `stdout`/`stderr` set to `DEVNULL` or to a `PIPE` — warn-only either way. A `DEVNULL` destroys the server's own log, so a mid-dialog 500 leaves the harness's raised message as the only record and its cause is unrecoverable; a `PIPE` nobody keeps draining loses it after the boot wait and blocks the server once the buffer fills, which reads as a hung agent — a warn, because a drained pipe is a legitimate setup this analyzer cannot see | Open one append-mode log under `plurai_sim/artifacts/`, give the same handle to both streams, and quote its tail in the error raised on a non-2xx reply — Step 8. An ATTACHED (not spawned) server says in that message that its log is external |
| `spawned_stdout_on_ipc_channel` | verify_simulate | A long-lived spawn leaves its `stdout` inherited (no kwarg, `None`, or `sys.stdout`), which is simulate.py's own stdout — the SDK's JSON-lines IPC channel. The first line that process prints makes the parent raise "Child wrote non-JSON to stdout" and the scenario dies: worse than a `DEVNULL`, which at least lets the run finish | Redirect `stdout` to the harness's log file (or this process's stderr) — Step 8. An inherited `stderr` is fine: the SDK tees the child's stderr to its own logger |
| `dotenv_import_swallowed` | verify_simulate | The dotenv import sits in `try/except ImportError: pass` — statically it looks covered, but with python-dotenv missing it silently does NOTHING and the agent falls back to code defaults WHILE THE RUN STILL PASSES | Make the except branch LOUD (print/log a warning naming the fallback, as the shipped template does) or drop the try — Step 8. Warn-only. |
| `unknown_root_key` | verify_config | A top-level key neither the backend nor the SDK reads, so whatever it was meant to configure never takes effect. The finding lists every root key that IS read | Rename it to the key you meant, move it under the parent that owns it, or delete it — a YAML comment carries a note for humans without being read as config — Step 8. **Warn-only** |
| `block_shape` | verify_config | A top-level block every reader walks as a mapping (`tools:`, `stubs:`, `agent_context:`, …) is written as something else. Nothing is read off the form that IS there, so the whole block is silently absent rather than partly wrong | Write the block as a mapping of name → declaration — Step 8 |
| `workspace_shape` | verify_config | `workspace:` is neither the historical scalar name nor a mapping. A sub-key written in any other shape declares nothing: `run_driver: headless` under a LIST resolves to `interactive`, which headless automation experiences as a hang | Use the scalar name, or a mapping of `name:` / `operator:` — Step 8 |
| `unknown_workspace_key` | verify_config | **Warn-only.** A `workspace:` sub-key nothing reads. Unknown sub-keys are silently ignored, so a typo'd `run_driver` leaves the run INTERACTIVE — again, a hang to headless automation | Spell it as one of the sub-keys the finding lists (`name`, `run_driver`) — Step 8 |
| `invalid_run_driver` | verify_config | `workspace.run_driver` is a word that is not a run driver. A value nothing recognises is read as NO declaration at all, so a headless pipeline stalls at the first gate that waits for a human | Declare one of the modes the finding lists, or omit the key for `interactive` (the default) — Step 8 |
| `render_actions_shape` | verify_config | `render_actions:` is malformed. `lint_prd.py` reads a malformed declaration as UNDECLARED, so the agent's render surface goes back to being invisible to every check instead of failing loudly | Write it as a list of action names — Step 8 |
| `agent_io_declared_not_at_root` | verify_config | `io:` is nested under a parent key (`agent:` → `io:`) — which the root key's own dotted spelling invites. Every reader looks up the literal root key `agent.io`, so the declaration is read by nobody and the setup smoke plans no probe for it: the author believes a channel is covered and nothing confirms it | Move the block to the root of `plurai.config.yaml`, beside `workspace:` / `project:` / `shape:`, spelled with a literal dot — Step 8 |
| `agent_io_shape` | verify_config | `agent.io:` is not a mapping. It declares what this agent can be HANDED — the message content parts it accepts, and whether it can be driven with no user message at all — so a scalar or a list there declares nothing | Write it as a mapping with `input:` / `may_open:`. A BARE `agent.io:` is legal and means the defaults, declared — Step 8 |
| `descriptor_role_unknown` | verify_config | A tool's `descriptor.role` is a word nothing dispatches on, so the key takes effect nowhere. The finding lists the roles that ARE accepted | Spell it as one of those, or drop the key — Step 8 |
| `identity_surface_not_mocked` | verify_config | A tool declares `descriptor.role: identity` and nothing in this config mocks it. The run would need a REAL credential to get past sign-in — the one dependency a simulation cannot supply — so every scenario dies on turn 1 and the failure reads as an agent defect | Serve identity from the mocked world: `mode: mock` with a `@plurai.mock` handler, or keep `mode: real` and name the tool in the `serves:` of the seam mock its sign-in call goes through, so its own body runs over the mock — Step 8 |
| `closed_class_detectors_shape` | verify_config | `closed_class_detectors:` is not a mapping of `<entity>.<field>` → regex, or a declared regex does not compile. Both scan shells REFUSE a malformed declaration rather than reading it as "no override": an override the customer wrote and the pipeline silently dropped is the worst of the three outcomes | Write it as a mapping of `<entity>.<field>` to a compiling regex — Step 8 |
| `knowledge_block_invalid` | verify_config | **Warn-only.** The optional `knowledge:` block is malformed — a string `"true"`, or a doc set given as one comma-joined string. The block is a RECORD for humans and for the skills that read the config; neither the backend nor the SDK reads it, so this misleads a reader rather than breaking a run | Shape it `{prompt_inlined: bool, closed_doc_set: [paths], notes: str}` — Step 9b |

The rows above are the codes an operator can hit while AUTHORING `plurai.config.yaml`. The rest of
the emitted set — the read/parse-failure family, the descriptor warnings, and the checks that
report they did not run — is one page over, in
**`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/finding-codes.md`**. Together
the two are the WHOLE set: each verifier declares the codes it can emit as a `Code` / `Category`
enum at the top of its own script, and every declared code has a row here or there, and every row here names a code a script declares.

**Whether the harness can actually resume** is `seed_state_refuses_every_seed`'s question: every other code reads only whether the seed is touched.

After fixes, re-run BOTH verifiers. Only proceed to Step 9e.4 when both exit 0.

An `advisory` finding the agent's repo shows is wrong (a wrap the AST walk cannot follow, say) is overruled instead of repaired, per "Plugin-local findings" in `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (`$ART` is `plurai_sim/artifacts`). A `security` or `invariant` finding is never overruled: repair it.

**9e.4 — state preconditions audit (RECOMMENDED — surfaces undeclared state reads):**

`verify_simulate.py` and `verify_config.py` cover the WIRING contract — they don't audit which `state` / `agent_context` / `config.configurable` KEYS the production tool implementations actually READ as preconditions. Those reads are invisible to descriptor-level analysis. When the production code gates execution on a key the scenario hasn't seeded, the tool returns an error like `"Refinement questions required in state"` and the agent's React loop tries to recover via more tool calls, exhausting the recursion budget (`GraphRecursionWrap`).

Run the audit from the plugin (as with the verifiers above, do not copy it into `plurai_sim/`):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"

# Discover every state read in the agent code:
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/audit_state_preconditions.py" \
    <path-to-your-agent-module>
```

(Replace `<path-to-your-agent-module>` with the directory containing your tool implementations, e.g. `agent/` or `.` from your project root.)

The audit walks the agent code AST and lists every literal-string key read from `state` / `agent_context` / `ctx` / `payload` / `config.configurable`. The declaration point is `plurai.config.yaml` `agent_context.fields`: `scenario_state_schema.yaml` does not exist yet, and the env build writes its `agent_context_fields` from this config. (After that build, `--schema plurai_sim/scenario_state_schema.yaml` marks the reads the schema does not declare with `!` and exits 1.)

**Treat this as a "review before scenarios" list, not a hard blocker** — false negatives are tolerated (the audit only catches literal-string keys, not dynamic key-construction). For every key it lists that the config does not declare:

1. **Decide its role**: is it a precondition the SCENARIO must seed (e.g., the account's plan tier, a configured policy text)? Or is it transient runtime state the agent itself writes mid-conversation (e.g., a running total inside a multi-step loop)?
2. **For preconditions**: add the key to `simulation_prd.md`'s `agent_context_fields` section AND to `plurai_sim/plurai.config.yaml` `agent_context.fields` (the env build copies them into `scenario_state_schema.yaml`'s `agent_context_fields`; a key the config omits never reaches it), marked `precondition: true` (which demands a NON-EMPTY value — `required: true` is only JSON-schema presence and is satisfied by `[]`/`{}`). A precondition that names a specific record (an id/sku/code key + descriptive siblings) additionally declares `ground_from: {entity_type: <type>}` when a corpus exists — the corpus row then wins over generator-authored values, per key (see the `ground_from` block in the example schema; prompt wording alone cannot keep an authored title/price consistent with the declared id). **Copy the shape from `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/example_scenario_state_schema.yaml`** — the keys the backend actually reads are a short, closed list, and two natural-looking spellings do nothing at all. Keep the `default_value` too — it is inert in the env-gen loader but LIVE at sim time: `simulation/scenario_loader.py` backfills a missing required field from it (Layer 3, Step 8.5), and its own `ScenarioSchemaMismatchError` asks for one. What does NOT work is JSON-Schema `properties:` — this file is not JSON Schema. An object-typed field gives its sub-fields a contract via `item_specs`, and a `precondition` field that omits one is rejected at load as unsatisfiable — populated it trips `additionalProperties`, empty it fails the precondition. The scenario generator + Layer 3 population in `seed_state` (Step 8.5) then carries the field through.
3. **For transient state**: ignore — the agent will populate it itself. The audit just surfaces what to ignore so you can make an explicit decision.

The single most common failure pattern this catches: an agent's production tool body has `if not state.get("<precondition_field>"): return error`; scenarios don't seed `<precondition_field>`; the agent retries tools trying to recover; `GraphRecursionWrap` at turn N. Declaring that precondition field in `plurai.config.yaml` `agent_context` with a sensible default for the agent's domain breaks the cascade.

### Step 9f: Smoke-import test (BLOCKING — catches what static verifiers can't)

The static verifiers in Step 9e detect contract violations they can see in the AST: missing functions, wrong signatures, malformed descriptors. They can NOT detect **runtime import failures** — the customer's agent often imports production deps (`opentelemetry`, `RestrictedPython`, `supabase`, `redis`, `stripe`, observability libs) that aren't in the Plurai backend venv. Each manifests as `No module named 'X'` only when the child subprocess actually tries to import. Same for **package shadowing** (the customer's `plurai_sim/` directory naming) and **IPC handshake** failures (a stray `print(...)` on the import path).

Step 9f catches these by spawning simulate.py via the same `plurai._subprocess.ChildProcess` the production runner uses, sending one minimal `Init` envelope, and classifying any failure into actionable categories.

**9f.1 — run smoke (from the plugin, never a copy in `plurai_sim/`):**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" plurai_sim/simulate.py
```

**On a DIALOG bench, mocked or mockless, add `--turn` — on a mockless one it is the only live
gate.** It Inits the SDK's local in-process mock, so no deployed mock is needed. Two real turns
(the second checks the harness carries history), one provider call each — plus ONE more per
declaration the run can confirm: the render surface (only with `PLURAI_SMOKE_RENDER_PROBE=1`; by default its SDK channel is checked on turn 1 and the line reads `render_channel: unprobed`), each non-`text` kind in `agent.io.input`, and
`agent.io.may_open`. A bench that declares nothing is charged exactly the two it always was. 9f.4 below asks "declared
== bound" of `tools:`, which is empty on a tool-less or all-real agent, so without `--turn` the
whole local gate of such a bench is an Init handshake against a subprocess: nothing ever calls the
agent. The bench manifest declares this as the `smoke_turn` check with
`mode_rule: agent.dispatch_shape == dialog` and `requires: provider_key`, so it is planned, blocking,
and reported as `not_run` with the reason when no key is present — never silently skipped.
Its world is the bench's first `agent.smoke_inputs` entry (scan Step 2a), else empty. **Never catch
the agent's error in the harness to pass the smoke** — declare the world the turn needs instead. A
harness may mirror only a catch the agent's own production entry makes, citing its `file:line`.

**A PIPELINE bench (`shape: pipeline` / `invoke_pipeline`) takes `--pipeline` instead of `--turn`**
(which sends a user turn such a harness has no hook for): one `invoke_pipeline` per
`agent.smoke_inputs` world in `bench_manifest.json`, each on the local mock. The manifest runs it
as `smoke_pipeline` — it reports verified / failed with the typed exception / not_run, and never
blocks.

The script exits 0 if the child subprocess loaded simulate.py and responded to `Init`, **1 while a
classified finding about the bench holds, 3 when the agent raised and 4 when a turn came back silent with
no owner the smoke could determine (the run measures both), and 2 with NO VERDICT**. Each failure prints as a
`finding:` line; an advisory one you hold evidence against is overruled per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`. The 1/2 split is the toolchain's error
posture and it decides what you do next: a 1 has something in simulate.py to fix, a 2 has a
prerequisite to supply and nothing was measured — `bench_manifest.py` records it as `not_run`, never
as a failure of the customer's agent. Exits 1 with a classified failure on any of these, unless the row says **Exits 2**:

| Category | What it means | Fix |
|---|---|---|
| `agent_interpreter` | `No module named 'X'` where X is the agent's OWN dependency — declared in its `pyproject.toml` / `requirements*.txt` / `uv.lock`, or installed in one of its venvs — or X is `plurai` itself | **Exits 2**, recorded `not_run`: the child ran under the wrong interpreter. Re-run `derive_tool_facts.py --write` and the smoke with the `PLURAI_CHILD_PYTHON=<path>` the hint names (or install the agent's deps into its venv). **Never stub it** — a MagicMock'd agent dependency hollows the agent under test. |
| `missing_stub` | `No module named 'X'` during agent import, X neither declared by the agent nor in any of its venvs | Add `sys.modules.setdefault("X", MagicMock())` to simulate.py's Phase 1 stubs block. Re-run smoke. May surface another stub — iterate until clean. For packages with many submodules, install a meta-path finder (see Step 8.4 prose's opentelemetry example). |
| `package_shadow` | `module 'plurai' has no attribute 'configure'` | Customer's simulation directory is named `plurai/` and shadows the SDK package. Rename to `plurai_sim/` per Step 8.4's "DIRECTORY NAMING" warning. |
| `ipc_handshake` | `Child wrote non-JSON to stdout` | A `print(...)` on the import path broke the IPC channel. Add `file=sys.stderr` to every print fired during module import. |
| `syntax_error` | `simulate.py / helper does not parse` | Run verify_simulate / verify_config first to localize the syntax error, then fix. |
| `input probe` | `--turn` and the harness RAISED on a turn carrying a part kind `agent.io.input` declares (the message content is a parts list, `[{"type": "text", …}, {"type": "file", …}]`) | Fix ONE of the two, and the refusal names both: make `invoke_agent` read a list-valued message content (LangChain carries it as `HumanMessage.content` — take the text parts and hand the rest to whatever attachment channel the agent already has), or drop that kind from the declaration. Every scenario that attaches one would otherwise die on that turn. |
| `may_open probe` | `--turn` and `agent.io.may_open: true`, but the harness raised on — or answered nothing to (exit 4) — the empty opener (`invoke_agent` called with `{'messages': []}`) | Make `invoke_agent` serve an empty `payload['messages']` (greet, then wait), or remove `may_open: true`. That call is turn 0 of EVERY scenario of a bench that declares it, so the alternative is the whole set dying on its first turn. |
| `mock_unreachable` | `Could not reach the deployed mock` | Should not occur from this script (it passes `mock_mode='local'`); if you invoked the child yourself, pass `mock_mode='local'` — smoke tests LOADING, not mock reachability. |
| `other` | unrecognized failure pattern | The script prints the child's full stderr — examine for the root cause. |

**Before any of these, in every mode, exit 1:** `the harness uses plurai.<name>, which the SDK at <path> (<version>) does not have`. Every `plurai.<name>` simulate.py and its helper modules reference is resolved in the child's interpreter before any child is driven (one inside a `try` that catches its absence, or behind `hasattr`, is not required). Upgrade the SDK in the interpreter the line names and re-run — never stub it: the harness was written for an SDK that has the API.

| **NO VERDICT (exit 2)** | What it means | What to do |
|---|---|---|
| not a file / driver crash | `simulate.py` is not at that path, or the smoke driver itself raised | Fix the path, or read the driver's own traceback — nothing about the bench was measured. |
| SDK not importable | the plurai SDK is absent from THIS interpreter (the import is deferred, so an absent SDK is a prerequisite verdict rather than a module-import crash) | Install it, or point at one (`${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py` resolves the runner), and re-run. |
| render channel unobservable | `--turn` on a bench that declares a render surface, against an SDK whose turn envelope carries no `agent_actions` | Upgrade the SDK. The bench's ONLY live channel cannot be observed at all — a render-only turn reaches the backend as an empty reply and grades as the agent saying nothing. This was previously a `⚠` note with exit 0, which the manifest recorded as `smoke_turn: verified` and printed as `smoke: 2-turn` over a turn nothing could see. |
| render surface unreadable | `--turn` and `plurai.config.yaml`'s `render_actions:` could not be READ — malformed (`RenderActionsConfigError`), or an SDK with no render-surface reader | Fix the declaration (`verify_config.py` owns its shape) or upgrade the SDK, then re-run. **"Could not read the surface" is not "there is no surface"**: the probe is skipped either way, so an unreadable declaration cannot be told from a bench that renders nothing — and if this bench renders, the skipped probe was its only live gate. `bench_manifest validate` refuses a config this broken, so this bites on the direct invocation above, where nothing else is looking. Init-only smoke is unaffected (it never probes the render channel) and reports the same fact as a `⚠`. |
| declared input part uncarriable | `--turn` and a declared part (or the empty opener) never reached the harness at all — the child died before reporting, with the traceback inside `plurai` rather than in `simulate.py`. On an older SDK build the turn-0 payload is built OUTSIDE the child's dispatch `try`, so a no-message turn kills the process | Upgrade `plurai` and re-run. The probe is skipped with the reason NAMED — never reported as a pass, and never as a defect of the harness. |
| declared input part uncarriable, or has no probe | `--turn` and `agent.io.input` names a kind the INSTALLED plurai cannot put on a turn (asked through `plurai.agent_io.INPUT_PART_KINDS`, before the turn is sent — sending it anyway produces a failure that looks exactly like a harness defect), or one this build of the plugin has no probe for | Fix the declaration, or upgrade the plugin if the kind is one it should know. Refused BY NAME: a kind is never resolved to the nearest thing, because certifying a file-reading agent on a text turn is a green worse than no check. |
| `agent.io` unreadable | `--turn` and the `agent.io:` declaration could not be READ — malformed, or two configs under one root that disagree | Fix the declaration (`verify_config.py` owns its shape) and re-run. Same rule as the render surface: "could not read it" is not "there is nothing to read", and the Init-only smoke keeps its exit 0 with a `⚠`. |

None of these is a finding about `simulate.py`: there is nothing to stub and nothing to fix there.

**9f.3 — iterate on `missing_stub` reports.** Each smoke failure adds one stub at a time; an `agent_interpreter` verdict ends the loop:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
smoke_out="$(mktemp)"
while :; do
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" plurai_sim/simulate.py > "$smoke_out"
    rc=$?                                              # never `status`: read-only in zsh
    cat "$smoke_out"
    [ "$rc" -eq 0 ] && break                       # smoke is green — stop
    grep -q 'category=agent_interpreter' "$smoke_out" && { echo "agent_interpreter: re-run derive_tool_facts --write and the smoke under the PLURAI_CHILD_PYTHON it names — never stub an agent dependency."; break; }
    { [ "$rc" -eq 3 ] || [ "$rc" -eq 4 ]; } && { echo "the agent raised or a turn came back silent — the run measures it; nothing to stub."; break; }
    if [ "$rc" -ne 1 ]; then                       # 2 = NO VERDICT: nothing to stub
        echo "verify_smoke reached NO VERDICT (exit $rc) — a prerequisite, not a stub."
        echo "Read the NO VERDICT table above and fix THAT; re-running changes nothing."
        break
    fi
    # exit 1 only: read the "add sys.modules.setdefault(...)" line from the output,
    # apply it to plurai_sim/simulate.py's Phase 1 block, then loop.
    # ~3-8 iterations is typical for a real agent with observability
    # + DB clients + sandboxes.
done
```

**The loop iterates on exit 1 only, and the block above is what enforces that** — not this
sentence. On exit 2 there is no stub to add (the verdict is about a prerequisite, not about
`simulate.py`) and the input never changes, so a bare `while ! verify_smoke.py …; do :; done` loop would spin on it
forever with nothing to fix.

Iterating manually is fine. The LLM running `/simulation-scan-agent` should run smoke, read the hint, add the stub, re-run — repeat until smoke exits 0.

**When NOT to iterate further.** If the same stub keeps showing up after you added it, the `sys.modules.setdefault(...)` placement is wrong (likely AFTER the agent module's import, so the stub is too late) — move it BEFORE Phase 2 / Phase 3 imports. If a stub gets through but the agent then crashes with `AttributeError` on a specific symbol, the MagicMock isn't deep enough — use `MagicMock(name="X", return_value=MagicMock(...))` or a custom Fake class. The smoke script will surface these via `other` and the stderr trail.

**9f.4 — verify the agent didn't SILENTLY DROP a tool (BLOCKING).** A clean smoke exit only proves simulate.py imported and answered `Init` — it does NOT prove the agent kept all its tools. The dangerous case: a tool module fails to import (commonly a version skew like `from langchain.tools import StructuredTool`, moved to `langchain_core.tools` in langchain 1.x) and the agent's tool loader catches the `ImportError` and **continues with one fewer tool**. Nothing crashes; the agent just free-hands answers it should have tooled, and every downstream check passes. This is invisible to 9f.

After smoke passes, cross-check the agent's *actual* tool set against the `tools:` declared in `plurai.config.yaml` — and against `surfaces.production_tools`: a surface check on the tool list fails `production_tool_dropped` when a production tool is not live:

- If the agent exposes an enumerable tool list (LangChain `agent.tools`, a LangGraph `ToolNode`, a registry), add a one-shot assertion to `verify_smoke.py` (or a scratch script run via the SAME child interpreter) that prints the bound tool names and compare to `plurai.config.yaml`. Any declared tool missing from the live set is a silent tool drop — find which import the loader swallowed (grep the agent's tool-loader for `except ImportError`). **Record that list — ALWAYS, it is a gate:** build it with every switch at its production default (never the harness's pins), as the SAME child interpreter, and record it as C3's live half: `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/agent_inventory.py" record-live --workdir plurai_sim --from <names.json> --enumerated-by <path>:<line>`, then re-run `bench_manifest.py init`. `validate` refuses an in-harness bench with no live list (`production_inventory_incomplete`: C3 = static ∪ live). **This is an assertion YOU add, not a `verify_smoke` verdict:** the script's own categories are the ones in 9f.1's table, `_classify` returns nothing for a dropped tool, and a clean exit therefore never rules one out. Fix the agent's import, or isolate the dependency stacks via `PLURAI_CHILD_PYTHON` below.
- If the tool set isn't introspectable, watch Step 9g / the first real run: a scenario whose target needs a tool, where `tool_events` is empty, is the symptom.

**Fix options for a silent drop or a hard dependency conflict:**
1. **Fix the agent's import** (best, if you own the agent): pin a compatible version, or update the import to the new module path (e.g. `from langchain_core.tools import StructuredTool`).
2. **Isolate dependencies via `PLURAI_CHILD_PYTHON`** when the agent pins deps that can't coexist with the backend's (e.g. a legacy langchain vs the backend's `langchain>=1.0`): create a separate venv that has the agent's own deps + the `plurai` SDK, and point the child interpreter at it:
   ```bash
   PLURAI_CHILD_PYTHON=/path/to/agent-venv/bin/python plurai-run-simulation ...
   ```
   The parent↔child wire is plain JSON over stdin/stdout, so the runner (the backend's venv) and the agent (its own venv) can run different dependency stacks. The child venv only needs `plurai` + the agent's deps. This is the right tool when "just install everything in one venv" is impossible.

Only proceed to Step 9g when verify_smoke exits 0 (or 3/4: only a failure the run measures) AND the live tool set matches the declared tools (or you've consciously accepted a documented gap).


### Step 9g: End-to-end validation (against the backend, after setup)

Step 9f proved simulate.py imports + answers `Init` locally — and it needs only the SDK, so it stays your local gate. There is **no local end-to-end run during setup**: the runner drives a Plurai backend you don't have on this machine, and no backend scenarios exist yet. The call-time bugs a local end-to-end run would catch —

- a `@plurai.mock` handler that registered but raises when invoked (`KeyError` / `NameError` in the body),
- a storage monkey-patch whose signature doesn't match what the real tool passes,
- a `@plurai.observe` handler crashing on the real tool's output shape,
- a mid-conversation IPC failure (Init works, but turn 2 dies),

— now surface on your **first real run** against the deployed backend:

- After `/simulation-generate-suite` (it stands up the world, then builds scenarios + evals on it), run **`/simulation-run`** then **`/simulation-fixes`**. That run IS the end-to-end gate: the same call-time paths, against the deployed mock + backend scenarios; `/simulation-fixes` diagnoses on failure. (Pipeline-shape benches — `invoke_pipeline` — get one local pass per `agent.smoke_inputs` world from `smoke_pipeline`; no `pipeline_io.json` is validated anywhere today, so read this run's per-scenario outputs and errors directly.)

Don't block setup on an end-to-end run: once 9f is green AND the live tool set matches the declared tools (9f.4), proceed — the world is stood up later, by `/simulation-generate-suite`.

**Hard gate before handing off.** Setup is complete once Step 9f (`verify_smoke.py`) exits 0 (or 3/4) AND the live tool set matches the declared tools (9f.4). The end-to-end behaviour is then validated on the first real run (above) — don't block the hand-off on it.


### Step 10: Run, test, fix, iterate

DO NOT just generate the files and stop. Run the simulation (via `plurai-run-simulation --agent-module-path simulate.py`) and fix every error until the simulation completes successfully. simulate.py is invoked as a child subprocess by the runner — you do NOT execute it directly with `python simulate.py`.

**10a. Write ALL files:**
```
plurai_sim/
├── simulate.py                # runs agent with mocked tools
├── plurai.config.yaml         # tool config, entity schemas, stubs
├── simulation_prd.md          # full domain knowledge document
├── test_mock_contract.py      # backend acceptance tests (only when a tool is mocked — Step 9d skip)
├── conftest.py                # Step 9d gate — "nothing passed" exits 3 (a hook only fires from a conftest)
└── test_agent_envelope.py     # Step 9d — the ToolMessage the agent reads; LOCAL ONLY, never uploaded
```

The Step 9e / 9f verifiers (`verify_simulate.py`, `verify_config.py`, `verify_smoke.py`,
`audit_state_preconditions.py`) are NOT written here: they run from
`${CLAUDE_PLUGIN_ROOT}/skills/…/scripts/` with their invocations as Steps 9e and 9f give them, so
every run uses the verifier THIS plugin ships rather than a copy frozen at setup time.

**10b. Run the import test first (Phases 1-2 only):**
```bash
python -c "
import sys
from unittest.mock import MagicMock
# ... paste Phase 1 stubs here ...
from {agent_module} import {graph_variable}
print('Import OK')
"
```

If this fails, read the traceback. Common fixes:
- Missing stub: add another `sys.modules["..."] = MagicMock()` for the module in the traceback
- Missing env var: add `os.environ.setdefault("...", "plurai-stub")`
- Module-level code calling `len()`, `int()`, `str()` on MagicMock: patch the specific value after import

Fix the stubs, run again. Repeat until "Import OK" prints.

**10c. Run the full simulation (smoke test — blocking; do not proceed if this fails):**

simulate.py is a child program — you don't run it directly. The runner (`plurai-run-simulation`) spawns it as a subprocess and drives the dialog against the Plurai backend. End-to-end runs go through the backend:

- **Customer path:** `/simulation-generate-suite` (world + scenarios + evals), then `/simulation-run` → `/simulation-fixes` (deployed mock + backend scenarios, by id).

You should see `[plurai] starting child: plurai_sim/simulate.py pid=...` on the runner's stderr and per-tool-call `[plurai] MOCK ...` lines from the in-process mocks.

**Pass criterion**: at least one full user → agent → user turn with a non-empty AI message before any `should_terminate`, plus a `transcript.json` written under the run's output directory.

**10d. Read the error, fix, repeat:**

> **Read this before triaging anything:** `GraphRecursionError` (and `GraphRecursionWrap`, `OrchestratorAdapterError: ...GraphRecursionError...`) is **always a symptom, never a root cause.** It's the backend's universal "the agent stopped making progress" signal — emitted when LangGraph's recursion budget runs out. The ACTUAL exception is **always one layer deeper**, in the child subprocess's stderr.
>
> **Diagnostic procedure when you see GraphRecursionError**:
>
> 1. Read the `--- last child stderr ---` block already embedded in the `OrchestratorAdapterError` message — the backend's IPC layer captures the child's last 20 stderr lines for exactly this reason. The real exception is there.
> 2. If the embedded block is missing or empty, open the run's stderr stream directly (the child writes to it before the backend's parent process drains it). On a local run, that's the terminal output of `plurai-run-simulation`. On a backend job, it's in `mock_service.log` next to the workspace.
> 3. The actual error class names below (`TypeError: object MagicMock...`, `TypeError: isinstance() arg 2 must be a type`, schema-field `KeyError`, `IndexError`, etc.) are what to grep for. Match against the rows below using the REAL error string, NOT "GraphRecursionError".
> 4. Only after you can name the deeper error: pick the right row, apply the fix, re-run.
>
> Triaging from "GraphRecursionError" alone is guaranteed to be wrong. Three different root causes produce the same outer symptom (empty seed, broken mock dispatch, multi-step plan failure). Each needs a different fix.

For each error, diagnose and fix. The raw exception strings in these tables are YOUR triage
material, not the user's: consume them yourself, classify against the rows, apply the fix, and
re-run — silently. Never paste exception text, stderr tails, or stack traces into the chat.

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'xxx'` | Missing stub in Phase 1 | Add `sys.modules["xxx"] = MagicMock()` |
| `KeyError: 'SOME_ENV_VAR'` | Missing env var | Add `os.environ.setdefault("SOME_ENV_VAR", "plurai-stub")` |
| `TypeError: object of type 'MagicMock' has no len()` | Module-level code processes a stub result | Patch the value after import: `module.VAR = correct_value` |
| `AttributeError: 'MagicMock' has no attribute 'xxx'` during import | Stub accessed in unexpected way | Usually harmless — check if it's a real crash or just a warning |
| **`TypeError: object MagicMock can't be used in 'await' expression`** | Async API on a stubbed module — `MagicMock().some_method()` returns another `MagicMock`, which can't be `await`-ed. Triggered by `await stubbed_client.do_thing(...)` patterns deep inside the agent. | Use `AsyncMock` for the specific async-callable surface: `from unittest.mock import AsyncMock; mod = MagicMock(); mod.do_thing = AsyncMock(return_value=None); sys.modules["xxx"] = mod`. If the agent code awaits the result of an async context manager (`async with X(...)`), monkey-patch the function ITSELF to a no-op `@contextlib.asynccontextmanager` rather than relying on stub-introspection. |
| **`TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union`** when checking against a class from a stubbed module | `isinstance(obj, stubbed_module.SomeClass)` — `stubbed_module.SomeClass` is a `MagicMock` attribute, not a real type. The `isinstance` builtin rejects it. | Replace the flat `sys.modules["X"] = MagicMock()` with a small module-shaped stub that exports the relevant names as REAL empty classes: `mod = types.ModuleType("X"); mod.SomeClass = type("SomeClass", (), {}); sys.modules["X"] = mod`. The agent's `isinstance(obj, SomeClass)` now evaluates to `False` predictably instead of crashing. |
| **Agent's `isinstance(storage, ProductionStorageClass)` gate silently returns False in simulation** — special-case branch (caching, observability, optimistic-write path) never executes; behaviour silently diverges from production but no error fires | The customer's mock class (e.g. `MockUserExampleStorage`) doesn't subclass the production class the agent's `isinstance` check looks for (e.g. `ExampleSetApiStorage`). `ProductionStorageClass` is REAL (importable, a real type) — different shape from the stubbed-module case above — but the mock isn't a subclass, so `isinstance` returns `False` and the gated code path is dead in sim. No exception fires; the divergence is silent. | Grep the agent's source for `isinstance(.*<Storage|Repository|Client>` BEFORE authoring the mock. Pick the specific production class the agent gates on as the mock's parent: `class MockUserExampleStorage(ExampleSetApiStorage):`. Don't default to a generic base like `NullStorage` unless the agent code never does a specific-class isinstance check. |
| **Child stderr shows a seam mock returning empty `records: []` / `labels: []` on its first read** despite the scenario seeding them; downstream surfaces as `GraphRecursionError` because the agent retries the empty-result tool until the budget exhausts | Schema field-name drift between `scenario_state_schema.yaml` and the seam mock: the seeded rows use one set of names (e.g. `content_labels`, or separate annotation entities keyed by `(dataset_id, revision_index)`), the mock reads another. The deeper-layer signal is the empty-content tool result in child stderr — NOT the outer GraphRecursionError. | A mock defect: route it to `/edit-env` (the mock reads the declared world fields). Never add a normalising read to the harness's pass-through — that is harness code deciding what the store returns. |
| `KeyError` or `AttributeError` on tool response | Agent expects nested structure, mock returns flat dict | Enrich WorldState entities with the structure the agent expects |
| `'NoneType' has no attribute 'xxx'` at runtime | Context-loading node not replaced — reads from stub | Add replacement in Phase 3 for this node |
| Tool call not intercepted (hits real function, crashes) | `apply_mocks` didn't patch the right module, or tool imported before patching | Ensure apply_mocks runs BEFORE importing the agent module. Check the tool is in the correct module. |
| Tool patched but agent still calls real function | Agent imports tool via `from tools import func` before apply_mocks runs | Move apply_mocks BEFORE the agent import. Python resolves `from X import Y` at import time. |

**10e. Symptom → cause table (harness diagnostics):**

If the smoke fails, the table below maps observable failures to the harness mistake that caused them. The agent under test is rarely the culprit — most early failures are misuses of `invoke_agent`, `seed_state`, or the storage stub. This classification is for YOU: read the raw symptom yourself, apply the fix, and re-run without narrating the failure or quoting the error at the user.

| Observed symptom | Proximate cause | Where to fix |
|---|---|---|
| `OrchestratorAdapterError: ...asyncio.run...` | `invoke_agent` or `seed_state` is sync but contains `asyncio.run(...)` | Step 8 — match the hook's sync/async to the wrapped API |
| `OrchestratorAdapterError: ...GraphRecursionError...` | Tool's first read hits an unseeded stub; agent retries until budget exhausted | Step 6.5 — implement `seed_state` that writes the scenario's entities through `plurai.world_state.apply(...)`. If `seed_state` IS defined but the agent still sees empty state, run Step 9e's verifier — `seed_state_ignores_world_state` flags a body that ignores `initial_world_state`. |
| Strict-validation error from a tool's first storage read (e.g., `"Data integrity error: HEAD record exists but has no <required-field>"`) | Stub returns default-empty before any write | Step 6.5 — same fix |
| `seed_state` runs but the agent's tools still see empty data | The storage pass-through's methods return hand-rolled defaults instead of calling the seam mock that serves the tool | Step 8.4 — every method delegates to its seam mock (`plurai.mock_wrap`), so the seeded world is what it returns |
| Tool runs but world state never updates | `MockToolNode` not wired to that tool | Step 6 |
| `AttributeError: 'str' object has no attribute ...` on a scenario field | Scenario YAML stored a list/dict as quoted JSON | Not the harness — a scenario-generation defect on the Plurai side; do not paper over with JSON-decoding. No repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| `✗ turn smoke: the agent delivered neither speech nor an action` | THREE different owners look alike here — the verifier's own message enumerates them in order | Check them in that order and stop at the first that is true. (1) the agent really said nothing → nothing to fix here; (2) `invoke_agent` returns its reply somewhere the SDK does not read → return the message list under `messages`, Step 8; (3) `invoke_agent` returns a message list whose reply is visibly in it and the SDK still read nothing → **an SDK defect**: file it against `plurai-sdk` (`surface: sdk`) and do NOT rewrite `simulate.py` around it. Never emit a placeholder sentence for a silent turn — that manufactures words the agent did not say and every judge grades them as the agent's. |
| `⚠ render probe: this SDK build has no agent_actions on its turn envelope` | The installed plurai predates the typed turn, so a render-only turn cannot be observed at all and reaches the backend as an empty reply | Upgrade `plurai`. **Not** a harness fix: painting the render output into the reply text is the one repair that hides the gap on this bench and manufactures an agent utterance everywhere else. |
| `~ render probe: render_channel: unprobed` | The default: the smoke runs its two conversational turns and does not ask the agent to render; the qualification run reads the action channel | Nothing to fix. `PLURAI_SMOKE_RENDER_PROBE=1` drives the render probe turn again. |
| `⚠ trail results: every tool-trail entry whose dispatch did not fail carries no result` (`trail_result_missing`, a warning: exit 0) | `invoke_agent` returns `tool_trail` entries without the value each tool returned, so every judge grades the tool calls blind (or every tool the smoke called legitimately returns nothing) | Step 8 — set each entry's `result` to the tool's return value, recorded at the tool wrapper when the framework's trace carries no outputs |
| `⚠ tool history: …` (`tool_history_dropped`, a warning: exit 0) | Live: turn 2 could not give back a value the agent's own tool returned on turn 1, so history keeps only user and final assistant messages. Resume: `seed_state` refused a seed holding a tool call and result and accepted it without them | Live: keep every message a turn produces, tool calls and results included (LangGraph: a checkpointer). Resume: pass each seed message whole to the converter, `tool_calls` and `tool_call_id` included (template Step 5d); never rebuild `{role, content}` |
| `✗ trail tool names: the tool trail records <wire name> for the declared <tools key>` (`trail_tool_undeclared`) | The trail row carries the framework's wire name (CrewAI's `event.tool_name` is `sanitize_tool_name(tool.name)`), not the `tools` key, so every lookup by the key misses it | Step 8 — record the declared key as the row's `tool` (`references/framework-patterns.md`, "The trail row's tool name") |
| `⚠ trail tool untyped (TRAIL_TOOL_UNTYPED): the tool trail records <name> run by the framework` (`trail_tool_untyped`, a warning: exit 0) | The framework ran a function the agent never defined itself (a memory tool, a delegation or transfer call) and the config, whose `render_actions` closes the surface, does not declare it, so the backend charges every such call as invented | Type it on the map first (a live tool row with `writes`, `effects` and `runs`, scan Step 2a; re-render, scan Step 2b), then add `tools.<name>: {mode: real, observation_lane: tool_trail}` citing the framework source that adds it (Step 8). If no framework code adds it, the harness records the row `not_dispatchable`, never `ok` |
| `✗ resume seed: seed_state refused a canned type-keyed resume seed` (`canned_resume_refused`) | `seed_state` reads a seed message's speaker from `role` (the seed carries `type`), or restored fewer messages than it was given | `references/resumed-seed-state.md` — read `type` and restore every message. Only where the conversation lives in a session the harness cannot write, declare `harness.resume.mode: unsupported`; a harness that records its session and resumes it is not one |
| Nothing at all in the logs from a server your harness spawned | A harness-spawned subprocess is outside the SDK's observability — the SDK promotes the CHILD's stderr only | Step 8 — route that process's output to a file you control or to this process's stderr. It is a harness decision, not an SDK limitation; diagnosing it as one sends a real defect to the wrong repo. |

If you've verified the table above and the smoke still fails, it may be a code pattern this skill doesn't cover. That has no repair step here: run the contact hand-off (`stop_message.py contact --done`).

**10f. Run the contract tests (optional locally, required for backend):**
```bash
# Both variables are load-bearing. Without PLURAI_MOCK_URL the tier talks to a port
# nothing listens on; without the token the Plurai backend's bearer proxy answers
# 401 on every call and each test grades an auth error instead of the mock. The
# backend resolver exports the token of the configured backend.
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
PLURAI_MOCK_URL="$(cat "$PLURAI_DIR/.mock_service_url" 2>/dev/null)" \
  pytest "$PLURAI_DIR/test_mock_contract.py" "$PLURAI_DIR/test_agent_envelope.py" -v
echo "exit=$?"
```

**Read the exit code, and read WHICH one.** `3` — or the `MOCK CONTRACT TIER DID NOT VERIFY
ANYTHING` banner — means the tier verified nothing: fixture setup could not reach the mock, so
it reported ERRORS rather than failures, and a summary line scanned for "failed" reads that as
a pass. That must be fixed, not noted. `1` is ordinary test failures, which are EXPECTED here:

Locally these tests validate the dumb inline mocks. They will likely fail on business rule
tests (the dumb mocks don't enforce rules). That's expected — the backend will implement
proper mocks that pass all tests. The contract tests are the acceptance criteria the
backend must meet. `test_agent_envelope.py` is the exception: it grades the HARNESS's
rendering, not the mock's business logic, so it must pass locally too.

**10g. Present results to the user:**

Once working, show the user:
1. The complete `simulate.py` with all fixes applied
2. The `plurai.config.yaml`
3. The `simulation_prd.md` — ask if any business rules are missing or wrong
4. The `test_mock_contract.py` — explain these are the acceptance tests for the backend
5. The simulation output showing successful mock interception
6. Any changes that were needed beyond the initial generation — summarized in plain product terms (what the harness now does differently), not as an error-by-error log of the failures you fixed along the way


## Framework-Specific Patterns

**The Universal Pattern (applies to EVERY framework): `apply_mocks` before import.** Patch the tool functions in their module first, then import the agent — it picks up the mocked tools through normal import resolution. No graph rebuild, no architecture change.

```python
sys.modules["our_db"] = MagicMock()          # 1. Stubs
import our_tools
plurai.apply_mocks(our_tools, {"get_user": "mock", "process_refund": "mock"})  # 2. Patch BEFORE import
from our_agent import app                     # 3. Import — gets already-patched tools
```

The three steps above are the universal pattern: stub, patch, then import. **What differs per framework is where the tools are bound, so read `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/framework-patterns.md` for the agent you actually have** — it carries the variants (LangGraph `ToolNode`, LangChain `AgentExecutor`, `bind_tools`, CrewAI, OpenAI Agents SDK, custom no-framework agents, closures over stubbed deps) and the `apply_mocks` table.


## Troubleshooting

**When the Step 10 smoke fails with an import / interception / checkpointer symptom not covered by Step 10's own error-and-fix table, read `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/troubleshooting.md`** — it covers "import fails despite stubs", "NoneType has no attribute", "tool calls not intercepted", module-level DB results, `mode: real` returning MagicMock, tools not intercepted despite `apply_mocks`, and production-checkpointer crashes.

## Validation Checklist

Before presenting to the user, verify:

1. Every `sys.modules` stub has a comment explaining which import-time side effect it prevents
2. `plurai.apply_mocks()` runs BEFORE the agent module is imported
3. Every tool in `apply_mocks` config has the correct mode (mock/real/observe)
4. Real-mode tools' dependencies are NOT stubbed in Phase 1
5. The agent is imported AS-IS — no graph rebuild, no architecture change
6. The agent's native entry point is used (invoke/run/kickoff/handle — whatever it uses)
7. `plurai.config.yaml` lists all tools with descriptions and mode reasons
8. `simulation_prd.md` documents ALL entity schemas with constraints (enums, min/max, required)
9. `simulation_prd.md` documents ALL tool input/output contracts with error cases
10. `simulation_prd.md` documents ALL business rules and state machines
11. `test_mock_contract.py` (when a tool is mocked — items 11-15b are N/A for tools: {} agents, Step 9d skip) tests ONLY the mock service HTTP API — does NOT import agent code (the harness's envelope functions are lifted out of `_mocks.py` / `simulate.py`'s source, never by importing the agent)
12. `test_mock_contract.py` has read/write/error tests per tool
13. `test_mock_contract.py` has tool chain tests for every edge in tool_graph
14. `test_mock_contract.py` has constraint tests (enums, foreign keys, min/max) for those the PRD names, each value from the producing line (`(source: …)`)
15. `test_mock_contract.py` has one test per business rule in PRD Section 5, each expected value from the producing line (`(source: …)`)
15a. `plurai_sim/test_agent_envelope.py` exists and asserts at the ToolMessage level, not on the raw HTTP response — the only level the harness's rendering defects are visible at. It stays OUT of `test_mock_contract.py`, which is the only file uploaded to the backend and is run there against no harness
15b. `test_mock_contract.py` keeps the auth header and the preflight; `plurai_sim/conftest.py` carries the `pytest_sessionfinish` gate (never the test module — the hook would not fire). Every error assertion names a distinctive substring of the mock's real diagnostic rather than `"error" in response`
16. Every test docstring explains GIVEN/WHEN/THEN and WHY it matters for the agent
17. All `{placeholder}` values are filled — no templates left in any output file


## Return to dispatcher

When Step 10's smoke succeeds, return to the `simulation-setup` skill (already
loaded in this session) and continue with Step 11 (chain
`extract-scenario-variables`) — its chained-skills section picks up from here.
