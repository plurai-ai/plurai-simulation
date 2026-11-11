---
name: simulation-setup-bridge
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Internal phase of simulation setup for an agent not written in Python, run through a bridge (LANG is node). Reached only from simulation-setup Step 10. Never invoke directly; use /simulation-scan-agent.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Simulation setup — bridge generation phase

This skill is the bridge tail of `simulation-setup`. Preconditions: the `simulation-setup` skill's
Steps 0–9 ran (confirmed analysis, `plurai.config.yaml`, `simulation_prd.md`), and Step 0 set
`LANG = "node"`.

**A bridge bench needs two runtimes on the operator's machine:** Python with the plurai SDK venv (the
harness, `simulate.py`) and Node ≥ 22.15 (the agent, in the bridge child). B4's install writes npm's
cache under `~/.npm`, a host write outside the workdir, and is a prerequisite of every bridge bench.
The agent's own dependencies are the customer's install, never ours.

The protocol between the halves is
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/bridge-contract.md`; read it before B2.
Run B1–B6 in order. When B6 is green, return to
the `simulation-setup` skill, which is already loaded in this session, and continue at Step 11.

**THE PAYLOAD CONTRACT — the one thing that silently invalidates a whole run.** Per turn the SDK
sends the NEW user side and nothing else, never the conversation so far. **History is the agent's
own**, keyed on the thread: the template sends `thread_id` and the new message only, and the entry
hands both to the agent the way production's route does. An entry that rebuilds history from the
payload runs stateless, and the run completes green. Full contract: PHASE 4 (`invoke_agent`) of
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/simulate_bridge.py`.

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

## The steps

| Step | What it does | Done when |
|---|---|---|
| B1 | Toolchain preflight | `bridge_preflight.py` exits 0 |
| B2 | Seam discovery and the map: `bridge/bridge.map.json` | every external call the agent makes is one map entry with evidence |
| B3 | The entry: `bridge/entry.mjs`, and any `bridge/replace/*.mjs` | it exports `load` and `turn` (and `seed` exactly when the map says `"resume": "seed"`) |
| B4 | Copy the bridge runtime and the host verbatim, and install the runtime | the preflight passes again, now with the runtime installed |
| B5 | `simulate.py` from the bridge template | `verify_simulate.py` and `verify_config.py` exit 0 |
| B6 | Verify: the static map check, the route probe, then the shared smoke | every mapped entry reached its mock; `verify_smoke.py --turn` exits 0 |

## B1 — Preflight

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/bridge_preflight.py" "$WORKDIR"
echo "PREFLIGHT_EXIT=$?"
```

The last line is JSON. A refusal names its code, the reason and the remedy: show the reason and the
remedy to the user in product terms and stop. A refused toolchain is never worked around: no other
runtime, no build of the customer's code, no install into the customer's repo. A warning (`!`, exit
stays 0) does not stop B1; for `worker_threads`, check at B2 whether a turn the entry drives reaches
the file it names. A `next_runtime_api` warning (B1 with `--entry`, or B4 once the entry exists): apply its
remedy in the map's env (B2) and the entry (B3) before B6.

## B2 — Seam discovery and the map

Write `$WORKDIR/bridge/bridge.map.json`, schema `plurai.bridge.map/1`
(`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/bridge.map.schema.json`). It maps
each external call the agent makes onto a `mode: mock` tool of `plurai.config.yaml`.

**Pick the deepest call boundary below all business logic.** The seam is chosen by rule, per external
system, at the lowest call whose arguments are plain typed values, not an encoded wire payload:

| seam | when | map entry |
|---|---|---|
| a. package | **default**: the agent imports or constructs a client package, and the call path (`client.refunds.create`) names the operation | `packages.<specifier>`: its `exports`, and one `calls.<path>` per operation |
| a′. module facade | one agent module funnels the external calls and holds no branching logic (cite its lines); also the agent's own data-access module in front of a `raw_sql_driver` | `modules.<path>.proxy` |
| b. HTTP | the agent calls with `fetch` / `http` and no client package; or its client is refused below and speaks HTTP | an `http` route by method, host and path, with `match` on the operation (`header.X-Amz-Target`, a GraphQL `body.operationName`, a table in the path) |
| c. framework infra | the agent's own persistence that no tool reads (conversation memory, a checkpointer, a cache) | `modules.<path>.replace` → `replace/<name>.mjs`, in memory |
| d. tool level | never a map entry: when a, a′, b and c are all impossible, name why for each, with evidence, and stop | — |

Record the row on each mocked tool as C12 `seam:` (a, a′ → `client`; b → `transport`; c → `entity`; d → `tool_body`
with `seam_reason` + `skipped_code`). A mocked tool that one of the agent's own (`mode: real`) tools calls through
names it: `serves: [<own tool>]` and `serves_cite: <path:line>` of the call site, so that tool's trace facts reach
the mock (`simulation-setup` Step 6). `serves` names only own tools whose code reaches the cited call: a seam call the
product's own code makes on every turn outside every own tool (a route prelude, an entry-point fetch) declares
`invocation: fixed` and no `serves`, and an endpoint reached both ways keeps `serves` and no `invocation`. Map a
store's reads with its writes: a write-only map of a store the agent also reads is a `writer_reader_mode_split`.
A real reader that declares `read_through: {via: [<module path>.<export>]}` is rebound by a `modules.<path>.replace`
of the accessor's module whose `replace/*.mjs` overlays the mock world on the real read (`verify_config` reads the
map and the replace file for the export's name).

**Refused by name** (contract A.11), never an improvised workaround. A check catches some mechanically;
the rest are your judgement at B2, read off the call sites, because a missed one first shows as a turn
voided by an `unsupported_shape` at run time:

| refusal | what | caught by | the seam instead |
|---|---|---|---|
| `raw_sql_driver` | `pg`, `mysql2`, `postgres`, `better-sqlite3`, …: one call path serves every query, so no path names a tool | the map check, on a `packages` key in `refused_packages` | a′, the agent's own data-access module; with none, the bench is refused |
| `command_dispatch` | `@aws-sdk/*` `send(new XCommand())` | the map check, on an `@aws-sdk/` `packages` key | b, with a `match` on `header.X-Amz-Target` or the path |
| `builder_chain` | a query builder (`knex`, `drizzle-orm`, `kysely`, `@supabase/supabase-js`) or any member access on a mapped call's result | the map check for a listed builder; your judgement for member access on a result | b for a client that speaks HTTP (PostgREST: `match` on the table); else a′ |
| `sync_client_call` | a client method the agent calls without awaiting it | your judgement | a′, or `local` when it is pure |
| `async_iteration` | auto-pagination, streams | your judgement | a′ |
| `subclass` | `class X extends <mapped client>` | your judgement | a′ |
| `multi_instance` | two instances of one mapped client that need different tools | your judgement | a′ |
| `worker_threads` | the agent's calls run in a worker | the runtime, which fails a worker a turn starts loudly; the B1 preflight warns early, naming each file that imports `worker_threads` and constructs a `Worker` | refused |
| `yarn_pnp` | a Yarn Plug'n'Play install | the B1 preflight | refused |

Rules for every entry:

- **Every entry carries `evidence: <agent-relative file>:<line>`** where the agent imports or makes
  the call. A `packages` entry's evidence file contains the specifier.
- **`params`** name the positional arguments as the mock's keyword arguments, in order: `"...opts"`
  spreads an options object, `"*rest"` takes the remaining arguments as a list. The kwargs are the keys
  the call carried (contract A.5: `undefined` is omitted, a spread brings only the object's own keys), so
  give every `@plurai.mock` parameter a default; `verify_bridge` warns on one without
  (`bridge_mock_param_required`).
- **A synchronous member the agent reads is `local`**: `{const}`, `{self}` (a chainable no-op such as
  `$connect`), `{await_all}`, `{callback_self: i}` or `{impl: "replace/x.mjs#name"}` for a value computed
  from the agent's own config. A mapped call is always async.
- **`revive.dates`** names the keys the agent reads as `Date`.
- **`env`** holds inert values for configuration the agent reads at import (a connection string for a
  client the map swaps), applied to the child only, each with its reason and evidence.
- **`passthrough_hosts`** add an LLM gateway or custom provider endpoint beyond the contract's provider
  table; **`sink_hosts`** add a telemetry endpoint beyond its sink table. Neither may be a production
  host of the config's `egress` declaration.
- **`keep_real`** documents a package the agent uses that never leaves the process (`zod`, `date-fns`).
- **`entry.resume`** is `"seed"` when B3's entry exports `seed`, else `{"unsupported": "<why>"}`.

Anything the map does not route fails closed at run time: an unmapped call, a refused shape or an
egress to an undeclared host voids the turn as a harness fault naming it, even when the agent caught
the error.

## B3 — The entry

Write `$WORKDIR/bridge/entry.mjs` from
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/entry.example.mjs` (the contract,
commented). It imports the agent's **construction** module, never its server or main module, and serves
a turn exactly as production's route does: the new user message, production's own run settings (cite
the line), memory keyed on `thread_id`, the `request` values where production's client puts them. A
framework example: `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/examples/mastra_entry.mjs`.

- **`trail` is tri-state.** A list from the framework's own record of the turn's tool dispatches (an
  adapter under `bridge/runtime/adapters/`), or `null` when the entry cannot observe them. Never `[]`
  for "unknown".
- **History the entry keeps** (when production's route keeps it, not the agent's memory) is stored in the
  shape production stores, for example its UI messages. An entry whose stored shape is model messages appends
  every step's messages of the turn (`turnMessages` in `runtime/adapters/ai_sdk.mjs`), never the final step's
  `response.messages` alone, which drops the earlier steps' tool calls and results.
- **`seed`** converts the SDK's resume-seed messages into the agent's own memory for the thread and
  returns the `agent_state` keys it restored. Export it only when the agent's memory can take the
  conversation; a message it cannot convert throws an `Error` with `code: 'PLURAI_SEED_UNSUPPORTED'`.
  It receives the same `request` as `turn`; derive every memory key from the same inputs in both, so the
  resumed thread is the one turn 1 addresses.
- **`replace/*.mjs`** export every name of the module they replace, in memory, a few lines each.

## B4 — Copy the runtime and install it

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
mkdir -p "$WORKDIR/bridge/runtime/adapters"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/host/bridge_host.py" "$WORKDIR/bridge/host.py"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/register.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/child.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/pipe.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/loader.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/proxy.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/egress.mjs" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/adapters/ai_sdk.mjs" "$WORKDIR/bridge/runtime/adapters/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/constants.json" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/package.json" "$WORKDIR/bridge/runtime/"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/node/runtime/package-lock.json" "$WORKDIR/bridge/runtime/"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/bridge_preflight.py" "$WORKDIR" --install
echo "PREFLIGHT_EXIT=$?"
```

Copy them unchanged: `check_workdir_copies.py` refuses a copy that differs from the plugin's and
prints the command that re-syncs it. `--install` runs the runtime's private `npm ci` when
`bridge/runtime/node_modules` is absent or differs from the lock, then re-runs the preflight.

**The bridge runtime install is the plugin's own prerequisite under `plurai_sim/`: run it, headless
included, without asking; it is not an install in the customer's code.** It is private to
`bridge/runtime/`: the customer's `package.json` and `.npmrc` are never read or touched, and
`bridge/runtime/node_modules/` is never staged (the managed `.gitignore` covers it). On
`bridge_runtime_install_failed` the finding carries npm's stderr tail: set the registry or proxy in
`~/.npmrc` (the customer repo's `.npmrc` does not apply under `--prefix`), or run the same `npm ci`
with `--offline` from a warm npm cache, then re-run this command.

## B5 — `simulate.py`

Write `$WORKDIR/simulate.py` from
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/references/simulate_bridge.py` and fill it. Keep the ownership header (the comment at the top of the template saying the file belongs to the user) on every file you write from it. The same goes for `bridge/entry.mjs`.

- **The mocks** follow Step 8 of the Python lane (dumb
  `@plurai.mock` bodies over the world, rich descriptors in the config): one per tool the map routes
  to, named exactly as the config tool, every one listed in `_MOCKS`. See "Using the Python lane" below.
- **`_REQUEST_KEYS`** are the `agent_context` fields the config declares `inject: request`.
- **`MODEL_ENV`** / **`MODEL_DEFAULT`** name where the agent resolves its model (before asking for its key, `bench_manifest.py env-check <NAMES> --providers`; a provider it reports found is used, the agent's own first); **`AGENT_ENV_FILE`** is
  the env file production's start command loads, or `None`; a declared `harness.env_file` (a file the user
  named) replaces it, written as an absolute path.

The rest of the template is fixed. Three declarations follow from the bridge:

- the topology's `agent.entry_symbol` is
  `{"executes": "external", "why": "runs in the bridge child (node), entered through bridge/entry.mjs turn()"}`;
- the scan's egress declaration says `agent_runtime: subprocess`;
- `agent.native_tools` is never written by hand: `record_native_tools.py` owns it.

Then run the Python lane's Step 9d (`test_mock_contract.py`, when a tool is mocked) and Step 9e (both
verifiers) exactly as the `simulation-setup-python` skill says.

**Using the Python lane.** Run the `simulation-setup-python` skill for exactly these parts: the "Key principle"
paragraph and the "Gotchas" list of its Step 8 (the mock rules), Step 9d, Step 9e and (in B6) the Step 9f smoke.
Not the Step 8 paragraph that tells you to fill the Python template (yours is the template above), not Steps 8.4
and 8.5, and not its opening instruction to run Steps 8, 9d, 9e and 10 in order, Steps 9g and 10 or its "Return to
dispatcher" section: B5 and B6 own the verification and B6 returns you to the dispatcher.

## B6 — Verify

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_workdir_copies.py" "$WORKDIR"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-bridge/scripts/verify_bridge.py" "$WORKDIR"
echo "VERIFY_BRIDGE_EXIT=$?"
```

`verify_bridge.py` checks the map against the config and `simulate.py` statically, then runs the
route probe: it starts the child against a local mock session, calls every mapped entry with no model
call, and proves each one reached its `@plurai.mock` and that the egress guard refuses. Exit 0 all
routed, 1 a finding (repair it and re-run), 2 no verdict; the last line is JSON. Then run the Python
lane's Step 9f smoke with `--turn` (`verify_smoke.py`), which drives two real turns through the bridge.

A run reads the bridge's outcomes as the SDK reads any harness: a fail-closed event, a mock dispatch
the harness could not serve, or the child's death is a harness fault (the scenario is void, the log
tail in its error); an error the agent's own code threw is a failed trial; a provider failure is
provider infrastructure. Each scenario process runs one child (about 100–200 MB): cap trial
concurrency by the machine's memory.

## Return to dispatcher

When B6 is green, return to the `simulation-setup` skill (already loaded in this session) and continue
with Step 11 (chain `extract-scenario-variables`).
