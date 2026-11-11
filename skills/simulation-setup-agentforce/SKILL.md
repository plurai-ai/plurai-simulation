---
name: simulation-setup-agentforce
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Internal phase of simulation setup for a hosted Salesforce Agentforce agent (LANG is agentforce). Reached only from simulation-setup Step 10. Never invoke directly; use simulation-setup.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Simulation setup — hosted Agentforce generation phase

This skill is the Agentforce tail of `simulation-setup`. Preconditions:

- The `simulation-setup` skill's Steps 0–9 have already run: confirmed analysis,
  `plurai.config.yaml` (Step 8) and `simulation_prd.md` (Step 9) exist on disk.
- `LANG = "agentforce"` was detected in Step 0 (a `sfdx-project.json` DX project
  whose source tree holds an `aiAuthoringBundles/` directory).
- A mock env is NOT a precondition. This skill plans the rewrite and writes no
  org metadata, so it runs before a world exists. The rewired copy IS deployed
  against ONE mock env — that is the bring-up (Step A7), and it is the only step
  that needs the env id on disk as `.env_id` in the workdir.

When the smoke (Step A7) succeeds — or defers itself, which is what a first scan
does — return to the `simulation-setup` skill, which is already loaded in this session, and
continue at the chained-skills section (Step 11).

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

## Responsibility split — read this before writing anything

The agent reasons and acts on Salesforce's servers. Nothing outside the org can
intercept an action call, and the customer's repo is not where the simulation
lives. So the split is:

- **The customer's repo holds what a customer holds**: the org metadata under
  its DX package directory, whatever transport they wrote, and the ordinary
  Agentforce dev loop
  (`retrieve`, `deploy`, `validate`, `publish`, `activate`). It carries no
  simulation recipe, no rewrite script and no mock-path lifecycle of its own.
- **Everything the simulation needs is plugin output**, written into the
  customer's working directory when this skill runs.

Four of those outputs are FILES COPIED VERBATIM out of this skill, not
templates you fill in:

| Plugin source | Copy lands at |
|---|---|
| `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/rewire_agentforce.py` | `$WORKDIR/rewire_agentforce.py` |
| `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/agentforce_mock_path.sh` | `$WORKDIR/agentforce_mock_path.sh` |
| `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/export_agentforce_knowledge.py` | `$WORKDIR/export_agentforce_knowledge.py` |
| `${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh` | `$WORKDIR/resolve-plurai-urls.sh` |

All four are generic and config-driven — nothing in them is customer-shaped,
they read the agent name out of the bundle and the org alias out of the
environment. Between them they use only the standard library and the `sf`
CLI, and none of them reads `CLAUDE_PLUGIN_ROOT`. **Copy them; never point
the customer at a path inside the plugin.** Teardown is the reason: `clean` has
to still work months later, in a checkout with no Claude Code installed and no
plugin on disk. This follows the same verbatim-copy convention as
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py`.

`$WORKDIR` is the simulation workdir the plugin already resolves — `plurai_sim/`
in the bare layout, `plurai_sim/envs/<name>` when `envs/` and `.active-env` both
exist. Ask for it rather than assuming, and SOURCE the resolver rather than
running it — it exports `WORKDIR` into the calling shell and prints nothing, and
the harness gives every Bash call a fresh shell, so the `source` and the use of
`$WORKDIR` have to sit in one snippet:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "$WORKDIR"
```

Every path below is relative to it, and the copied files resolve it the same way
through `python3 rewire_agentforce.py --paths`.

## The architecture in one diagram

```
plurai-run-simulation
  └─ Orchestrator ── REST ──► the Plurai backend  (scenarios, user-sim turns, evals)
  └─ child: python -m plurai.child simulate.py      ← persists across turns
       └─ simulate.py: ONE Agent API session per scenario
            • drives the REWIRED COPY `<Agent>_Plurai`, never the customer's agent
            • opens the session with plurai_session_id = plurai.active_session_id()

Salesforce org  (brought up by `bash $WORKDIR/agentforce_mock_path.sh up`)
  └─ <Agent>_Plurai
       └─ each rewired action → generated Apex → callout:Plurai_Mock/mock/execute
            └─ POST <Plurai backend REST base>/mock/<env id>/mock/execute
                 {session_id, tool, args} → tool_events + world state
```

Protocol-level the result is a normal dialog agent (`invoke_agent` →
`{'messages': [AIMessage]}`); the Plurai backend and the eval pipeline run
unchanged. Everything Agentforce-specific lives in this sub-skill's outputs.

**Three facts decide the whole design.**

1. **The agent is never edited.** The rewrite emits a SECOND agent — the copy
   carries the `_Plurai` suffix and its own developer name. The customer's agent
   keeps running against real org data.
2. **`plurai_session_id` is the ONLY session channel.** The credential URL
   carries `/mock/<env id>` and is env-scoped, not session-scoped, and an
   `@InvocableMethod` receives only declared invocable variables. So the rewrite
   declares one Agent Script variable, marks it `visibility: "External"` so the
   Agent API can set it at session start, and binds it into every rewired
   action. Without it, concurrent runs read and write each other's world state.
3. **Actions are recorded for you, and shown to the per-turn judges by the harness.**
   Every rewired action posts to `/mock/execute` from inside Salesforce, which is
   the same wire every other lane's tool calls travel. The calls are already this
   session's `tool_events`, and that is what the end-to-end evals read. The
   per-turn judges read the turn's MESSAGE CHAIN instead, so the harness returns
   each mock-recorded call of the turn as an `AIMessage` carrying `tool_calls`
   plus the `ToolMessage` the mock answered, ahead of the reply (`_turn_chain`).
   A chain that is only the reply makes every per-turn judge blind to tool
   results. It is not a second count: the calls carry no id (the mock's log
   declares none), so the SDK reconciles them with `tool_events` by tool name,
   count for count.

**THE PAYLOAD CONTRACT.** Per turn the SDK child sends
`payload = {'messages': [...]}` carrying THIS turn's user side and nothing else —
never the conversation so far (`**agent_context` is spread in on TURN 1 only).
That is ONE message on an ordinary turn, and `[]` on the opening call at a bench
that declares `agent.io: {may_open: true}`, so never index it blindly.
**History is YOUR harness's job.** Here that job is done by
the org: the Agent API session holds the conversation, which is why the template
opens it once in `seed_state` and only forwards the new turn in `invoke_agent`.
Full contract: the `invoke_agent` of
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/references/simulate_py_agentforce.py.txt`.

## Metering — the standing constraint on every step below

Agent turns cost Flex Credits, and they are the customer's credits. Nothing in
this skill runs an agent turn: not `sf agent preview`, not `sf agent test run`,
not a chat loop. The smoke is an **Apex health callout**, which is not a turn.
The scenario run that does spend credits is the customer's own command, named in
the run instructions Step A6 emits, with the scenarios spelled out — never a
whole-set run this skill starts on their behalf. A turn that hands the agent a
file adds one in-org prompt-template call, when its reader action reads it.

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

## Step A1: Read the retrieved Agent Script bundle

The bundle is the agent. Everything below is read off it, and nothing else in
the repo is authoritative about the tool surface.

```
<package dir>/main/default/aiAuthoringBundles/<agent>/<agent>.agent
```

Glob for `**/aiAuthoringBundles/*/*.agent`. Exactly one bundle is the normal
case; more than one means the customer names the one under test.

**No bundle on disk is a user-actionable input problem, not a failure to work
around** — unless `agent_kind.py` (beside this skill's other scripts; the
dispatcher runs it first) exits 3: that is an Agent Builder agent, with no
bundle to retrieve, so show its `message` and stop. Its one remedy: upgrade the
agent to the new Agentforce Builder (Setup → Agentforce Agents → the agent's
menu → Upgrade to New Builder), retrieve the upgraded agent's bundle, scan again.
Otherwise the bundle is retrieved from the org, so ask for the retrieve and
stop — never author a bundle, and never read the org-generated runtime types
(`Bot`, `BotVersion`, `GenAiPlannerBundle`) instead. The customer's own retrieve
recipe is the first thing to offer; the raw form is
`sf project retrieve start -o <org> --metadata AiAuthoringBundle:<name>`.

Read out of it, and record for the steps below:

| What | Where in the bundle | Used by |
|---|---|---|
| `developer_name` | the agent's variable block | the copy's name, `_COPY_NAME` in the harness |
| `agent_label` | the same block | the copy's label |
| `access.default_agent_user` | the access block | the permission-set grant in the lifecycle |
| action **definitions** | `actions:` at indent 4 | the tool surface, one entry per name |
| action **references** | `actions:` nested under a topic or step | which actions the agent can actually reach |
| each action's `target:` scheme | `target: "<scheme>://…"` | whether it is rewirable |
| each action's `inputs:` / `outputs:` | under the definition | the descriptor's argument and return shape |
| existing `variables:` marked `visibility: "External"` | the variable block | `_CONTEXT_VARIABLES` in the harness |
| `knowledge:` block (`rag_feature_config_id`, `citations_enabled`) | top level; inputs bound to `@knowledge.*` | the source binding of the knowledge action — `--plan` prints its `knowledge source:` line; an empty id bound into an action is a `source_unbound` fact cited at the binding line, its `empty_means` typed from the platform's documentation for that setting (`unknown` when it says neither; `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/references/agent_facts.md`); the articles it searches are exported in Step A3 |

The DSL is indentation-based, four spaces per step, spaces only. The two blocks
spelled `actions:` are told apart by indent alone: indent 4 holds the
definitions, deeper holds the references. Read it; do not reformat it. The
rewrite copies lines verbatim, quoting and all, because the file quotes some
names and not others and re-serializing would silently rewrite the schema the
model sees.

## Step A2: Build the tool surface in `plurai.config.yaml`

One tool entry per action NAME. Several definitions may share a name; they
collapse to one tool, because one Apex class serves the name.

Four declarations are Agentforce-specific:

- **`agent.framework: agentforce`.** The field is free-form and this is the
  value downstream skills match on.
- **No `agent.action_channel`.** That field belongs to claude-skill agents only,
  where it picks between an in-process MCP surface and intercepted Bash. Here
  there is exactly one channel — an Apex callout out of the org — and it is not
  a choice. Setting the field would make the dispatcher's Step 7 inference look
  like it ran when it did not.
- **`mode:` per tool** — `mock` for the actions the rewrite can repoint,
  `real` for the ones it cannot.
- **`world_state.user_surfaces`, always declared.** The Agent API is a text-only channel: the user
  sees the agent's replies and nothing else. Write `user_surfaces: []` under `world_state:`, and list
  a surface only for a delivery the user really receives outside the chat (a send the rewrite mocks,
  below). Left out, the simulated user also reads the agent's backend calls, their arguments and
  their results (a case record, an authentication key), which no customer saw; `verify_config.py`
  refuses a config without it (`agent_host_user_surfaces_undeclared`).
- **`render_actions: []`, always declared.** A top-level key beside `tools:`. An Agentforce agent reached
  over the Agent API renders nothing to the user except its text replies, so its render surface is
  empty by declaration. The persona's view of the agent's typed actions is filtered only when a scenario
  declares `render_actions`; left out, nothing is classifiable and nothing is filtered.
  `verify_config.py` refuses a config without it (`agent_host_render_actions_undeclared`).

**Ask the rewrite which is which rather than deciding it yourself.** It owns the
classification, and a second opinion here is a second opinion that drifts:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/rewire_agentforce.py" \
    --project-dir <agent root> --plan
```

`--plan` reads the bundle, classifies every action and prints the copy's name,
the rewired actions, and every action left real with the reason. It writes
nothing and needs no mock env id, so it runs on a repo where nothing has been
deployed yet. The classification depends on the bundle alone, which is why this
report and the one the bring-up prints are the same text. The reasons, and what
each means for the surface:

| Reason | What it means | `mode:` |
|---|---|---|
| the scheme runs inside the org and has no callout seam | the action's `target:` is not one of `apex`, `apexRest`, `api`, `externalService`, `flow` | `real` |
| the Apex class name exceeds 40 characters | the platform rejects the name; shorten the action name or leave it real | `real` |
| its definitions declare different inputs, outputs or targets | one Apex class cannot serve them; split the action or leave it real | `real` |
| its schema declares a composite type Apex cannot carry | only under `--skip-complex-types`; without the flag the value travels as JSON text | `real` |
| listed under `denied` (any left-real action whose target is not a known read-only kind; today only the Knowledge search `standardInvocableAction://streamKnowledgeSearch` is) | it runs in the customer's org and nothing shows it only reads, so the copy cannot call it until `.host_effects_consent` grants it (then `--plan` lists it under `granted live`); the scan's `writes:` typing can add a denial, never lift one | `real` |
| listed under `renamed` (the action is still rewired) | an input or output name is an Apex reserved word (`date`, `dateTime`, `limit`, ...), which cannot name an invocable variable, so the copy declares that parameter under `<name>Value` in its schema and at every `with`/`@outputs.` use; the Apex stand-in still sends and reads the DECLARED name, so the mock tool, the config and the world state keep the bundle's name. An action like `FinalizeReservation` (input `dateTime`) is therefore `mode: mock`, never left real | `mock` |
| listed under `retyped` (the action is still rewired) | the copy declares that input or output as text, so the planner fills and reads it as text, not as its real type (a list becomes one value). A record input's description asks for the Id of a record an earlier action returned. The platform enforces no such rule: production fills a record slot from the conversation too. So an ungrounded fill is an agent defect on both, and on the copy it shows as an Id. Tell the user with the left-real list | `mock` |

Everything else is `mode: mock`.

Every `mode: mock` action declares its C12 seam beside `descriptor:`: the Apex stand-in replaces the
action's whole org-side body, so `seam: tool_body`, `seam_reason: hosted_body`, and `skipped_code:
[<the action's target:, e.g. flow://…>]`. Guard: `verify_config` refuses a mocked action with no `seam`.

**`--plan`'s report also states each action's `observation_lane`** (`tool_events` for every rewired
action; appended per line for every `left real` / `denied` one). None of the left-real/denied reasons
puts a run through the one interception point that populates `tool_events`, so such a line says
`none`, unless the harness reports that action's calls itself. The line then names the lane it
reports on (`tool_trail_cited`) and the `granularity` its evidence resolves to, and says
`self-reported by the harness`. A denied action is never called, so it is always `none`. Copy the
lane (and any `granularity`) the report prints; never derive either from `mode:` or the name. **Write
`observation_lane:` and `granularity:` on the TOOL ENTRY, beside `mode:` — SIBLINGS of `descriptor:`,
never inside it**:

```yaml
tools:
  <the Knowledge-search action>:    # left real, self-reported by the harness
    mode: real
    observation_lane: tool_trail_cited
    granularity: article
    descriptor:
      ...
  <another left-real action>:
    mode: real
    observation_lane: none
    descriptor:
      ...
```

This is the same placement `state_updates:` and `requires_user_confirmation:` already use, and for the
identical reason: the installed `plurai-mockenv`'s `ToolDescriptor` schema (`extra: forbid`) does not
declare `observation_lane` — putting it INSIDE `descriptor:` would make Step 9e's `verify_config.py`
hard-fail the scan (`severity: fail`, every descriptor is validated against that exact schema). On the
entry it is outside that schema's reach, exactly like its two siblings, and `verify_config.py` passes
clean. Do not move it into `descriptor:` even if it looks tidier there.

The plan's `confirmation-gated` block lists each action every definition of which declares
`require_user_confirmation: True`, with its lines: write `requires_user_confirmation: true` on that
tool's entry, beside `mode:`, and cite those lines in `generation_trace.md`. A name under
`confirmation disagreement` stays unset; record it as a finding.

The plan's `recorded acts` block lists each reply type the harness records on `agent_actions`, the
verb it files it under, and the bundle's aliases that emit it. Such an alias is no `tools:` entry, and
the PRD states it as recorded under that verb on `agent_actions`, never as unreported.

**A `real` tool is a live org action during the run.** It reads and writes the
customer's actual org data and it is not recorded in `tool_events` — write the
`observation_lane` `--plan` prints for it on its entry (`none`, unless the line names a
self-report lane; see above). Say so to
the user when the list is not empty — this is the one place where a scenario can
have a side effect outside the mock.

**One `real` tool is the exception, and it must still get a full descriptor.**
The Knowledge-search action — the one whose `target:` is
`standardInvocableAction://streamKnowledgeSearch`, whatever the bundle names it —
has no callout seam, so `--plan` always lists it under `left real` — but when the
bundle's `knowledge:` block sets `citations_enabled: True`, its calls that cited
an article ARE observable: the harness self-reports each one, with the article(s)
it cited, from `citedReferences` on the Agent API reply (see Step A5), and
`--plan` prints `observation_lane: tool_trail_cited, granularity: article` on its
line (each citation names one whole article, never a passage). With `citations_enabled: True` a cited answer carries the article's link, so never write a link/URL `absent_from_source` fact over the snapshot for that agent. A search that
cites nothing is not seen, so that lane is a lower bound on the calls: none
observed means unknown, never "not called". The harness reports those calls under
the name of the tool entry that declares that lane, so the entry keeps the
bundle's action name. Give it a
descriptor like any other tool — `mode: real`, `description`/`label` from the
bundle, `args` (its `query` input), `returns` — never skip it because the
paragraph above says a `real` tool goes unrecorded. Omitting its descriptor
silently drops it from eval-gen's retrieval-tool candidates, and the set's
`retrieval_recall`/`retrieval_precision` evals never get authored.

Descriptor content per tool comes from the bundle, not from guesswork: the
action's description and label, its declared inputs and outputs with their
types, and the entity types those outputs name. The rest of the descriptor
contract is the dispatcher's Step 8 (the `simulation-setup` skill, already loaded).
Each tool's `returns` is `type: dict` with `type_source: annotation` and
`evidence: [<bundle path>:<line of the action's outputs:>]`, its `fields` the
declared outputs. A `real` tool runs against the live org, so it declares
`read_strategy: real` when it reads and `write_strategy: real` when it writes; a writer is also
`writes: live` on the scan's map, which is what offers its grant. Every tool also declares
`observation_lane:` on its ENTRY, exactly as `--plan` prints it (`tool_events` for `mock`; for `real`,
`none` or the self-report lane its line names), plus the `granularity:` a self-report line names. `up`
refuses a config whose `observation_lane` or `granularity` entries differ from what `--plan` prints
(`none` included, on every real action's entry), and names the entry and the line to write.
`python3 "$WORKDIR/rewire_agentforce.py" --write-lanes` writes every lane and granularity the plan
prints, and each action's `exposed_in:` (the subagents whose reasoning references it), onto the
existing entries and touches nothing else; run it once the entries exist, and again when `up` names a
missing or wrong lane or `exposed_in` (`up` refuses a scoped entry whose `exposed_in` is not the bundle's). `up` also refuses a `$WORKDIR/simulate.py` whose Knowledge-search footing differs from the
template's; re-emit it (Step A5).

**A send action writes what the user receives.** An action whose own
description or declared outputs say it delivers a message or a code to the end
user is a send, whatever it is named. A send the rewrite mocks gets the
dispatcher's Step 8 delivery: the `user_delivery` type, a delivery surface
listing it, and a post_condition that creates the row,
`{entity_type: user_delivery, count_change: "+1", with_fields: {channel: <email|sms>, to: <the input holding her address>}}`,
with `state_transitions.creates` naming its `subject` and a `body` that
carries the delivered value. When no input holds her address (the send takes a
record id), leave `to` out of `with_fields` and name in
`state_transitions.creates` the declared field of the record the send reads
that it comes from (`to` = `<type>.email`). When another action checks that
value (the code she types back), declare the record field it is checked against
(`<type>.code`) under `world_state.entity_types`, keep that action `mode: mock`,
and have the send's post_conditions create that record too: the mock compares
the typed-back input with the field. A send the rewrite leaves real delivers in
the org, outside the simulated world: give it no `user_delivery` post_condition
and list no surface for it. The simulated user cannot see its message, so a
scenario that needs its value is recorded as a finding, never faked. An action
that only returns a message to the agent is not a send; the user sees only what
a surface lists.

**Every sObject type declares `id_field: Id`.** A Salesforce record's own id is its `Id`
field, on every sObject: write `id_field: Id` on each `world_state.entity_types` type that
stands for one (Contact, Case, Order, ...), beside its `fields`. The backend keys the seeded
rows by it, so a `ContactId` holding the contact's `Id` resolves to that contact. A type that
is not an sObject (a pending verification, a `user_delivery`) keeps `id`, or declares
`id_field: null`.

**Then run the scan-time config check**, the one the Python route runs at its
Step 9e:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_config.py" "$WORKDIR/plurai.config.yaml"
```

It must exit 0. Fix each refusal as its message says, `user_surfaces_invalid`
and `delivery_surface_no_writer` included, and re-run. Pass no `--simulate`:
the tools are wired in the org, not in simulate.py, so its `harness_unknowable`
warning is expected on this route, and so is a `live_write_ungranted` warning per
action the bring-up will deny: each ungranted typed writer, and each left-real action not known to only
read (the same names `--plan` lists under `denied`). The topology declares that as
`agent.tool_interception: {"at": "agent_host", "why": "...", "route_smoke": "bash \"$WORKDIR/agentforce_mock_path.sh\" up"}`;
if the map lacks it, add it and re-run `bench_manifest.py init`.

## Step A3: Copy the rewrite into the workdir and plan from there

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/rewire_agentforce.py" "$WORKDIR/"
cp "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" "$WORKDIR/"
cp "${CLAUDE_PLUGIN_ROOT}/scripts/trusted-python.sh" "$WORKDIR/"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_backend_origin.py" "$WORKDIR"
cd <agent root> && python3 "$WORKDIR/rewire_agentforce.py" --plan
```

Copy both unchanged. Neither is a template and neither has placeholders; editing
a copy forks the logic away from the tests that cover it. The URL resolver
(and `trusted-python.sh`, its interpreter lookup on Windows) travels with them because the lifecycle sources it, and teardown has to run in a
checkout with no plugin on disk. Outside the plugin the copy sends the bearer only
to loopback, or the origin `record_backend_origin.py` writes to
`$WORKDIR/.plurai_backend_origin` once the plugin's guard accepts it for this
session; it also writes `$WORKDIR/.plurai_client`, the plugin version the copy names to the
backend. Run it on every scan. A non-zero exit means the session is not bound to
the Plurai backend the environment names: relay it, never write the file by hand.

**When the plan prints a `knowledge source:` line, export the org's articles.** The agent
answers from Knowledge articles that live in the org, and the corpus is built from files. The
export reads the org through the customer's own `sf` login — no agent turn, nothing written to the
org, nothing metered. An article published to a channel the agent's audience reads is already
public to that audience, so for those the scan they started is the consent: announce it in product
terms ("reading your org's published Knowledge articles, so the test corpus is exactly what your
agent searches"), do not ask. An article not known to be published to that audience (for a
customer-facing agent: one only in the internal app, or one whose channels cannot be read) stays in
the org unless the customer says otherwise, below.
`SF_ORG_ALIAS` names the org, as for the lifecycle.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/export_agentforce_knowledge.py" "$WORKDIR/"
cd <agent root> && python3 "$WORKDIR/export_agentforce_knowledge.py"; echo "EXPORT_EXIT=$?"
python3 "$WORKDIR/rewire_agentforce.py" --plan
```

It writes one Markdown file per exported article under `$WORKDIR/knowledge_snapshot/` (title,
summary, the rich-text body converted from HTML) and `knowledge_snapshot.INDEX.json` beside it
(ids, version, publish date, channel visibility, sha256 per file), records the folder in
`grounding.sources` and in the bench manifest's corpus roots, and stamps each file's sha256 into
`$WORKDIR/.corpus_scope`. The plan then prints `knowledge snapshot: <N> article(s) from <source>`
under `knowledge source:`; relay both lines. Never edit the snapshot; re-run the export when the
org's articles change.

The export also prints a `knowledge audience:` line: who the agent answers, read off the org's
record of the agent (its template when that record cannot be read), and how many articles are not
known to be published to that audience. They are left out. **When that line ends
`(knowledge_out_of_audience: undecided)`, ask once** — sanction it first with
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction knowledge-audience` — naming the count,
the audience and the consequence ("N Knowledge articles are not published to the <audience> this
agent answers. Include them in the test corpus? They would be uploaded to build it.").
Write the answer at the top level of `$WORKDIR/plurai.config.yaml` as
`knowledge_out_of_audience: include` or `knowledge_out_of_audience: exclude`, and on `include` run
the export again. **Under `run_driver: headless` the declared default for `knowledge-audience` is
`decline`**: the articles stay out and nothing is written, so the question stays open for the customer.

Each `finding:` line names its owner. `owner: agent` is the org's or the bundle's configuration;
`owner: instrument` is a limit of the export itself, which the customer's agent cannot fix.

| `EXPORT_EXIT` | meaning | what you do |
|---|---|---|
| `0` | the snapshot is written | an `owner: agent` finding becomes a `potential` entry in `$ART/agent_findings.md` with its id, severity, evidence and how to confirm, and its row in the same Bash call: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/record_finding.py" "$ART/agent_findings.md" --id <finding id> --status potential --found-during scan --severity <high\|medium\|low> --title "<the heading, without its number>"`; an `owner: instrument` finding is relayed beside the snapshot line in product terms and never enters `agent_findings.md` |
| `3` | no snapshot — the `finding:` line says why (no Knowledge object, no rich-text body field, no Online article, no article inside the agent's audience, or a library built from files or web pages) | record each finding by its owner the same way; the corpus source stays unrecorded. A library built from files or web pages is grounded by `/build-rag-corpus` over those files |
| `1` | the org or the bundle could not be read, or `plurai.config.yaml` sets `knowledge_out_of_audience` more than once | say what to fix in product terms (usually: log the `sf` CLI in to the org; keep the one answer line), then continue the scan without a snapshot |

**Run `--plan` from the copied path on every scan**, including a re-scan where
`.env_id` is already on disk. That run is what proves the copied module works
where it now lives, against this bundle — and it is a plan, not a generation, so
running it twice costs nothing and leaves nothing behind. `up` owns the tree and
regenerates it from scratch against whichever mock env is live then.

`--base-url` and the mock env id are bring-up inputs, not scan inputs.
`--base-url` is the Plurai backend REST base the callout reaches, its own path kept —
the mock reverse proxy is mounted beside the REST routes on the same origin, so
the endpoint is `<base>/mock/<env id>`. `resolve-plurai-urls.sh` is the one
place that base is derived, and the lifecycle script sources it; there is no
environment fallback inside the rewrite, because a guessed base builds a callout
that silently leaves the org and reaches nothing. The mock env id comes from
`.env_id` under the workdir. On the generate path both are refused when absent
rather than guessed, which is the reason the scan plans instead of generating: a
placeholder callout URL in org metadata ships on the next manual deploy.

The callout points at the configured backend: the plugin's own `PLURAI_URL` and `PLURAI_TOKEN`
(or the saved login the resolver exports) are already in the shell, and the token is the org
bearer the credential principal carries.

What `up` writes, all of it gitignored and rebuilt on every bring-up — none of
it exists after a scan:

```
<package dir>/generated/aiAuthoringBundles/<Agent>_Plurai/   the rewired copy
<package dir>/generated/classes/                             one Apex class per rewired action, plus the shared callout
<package dir>/generated/namedCredentials/Plurai_Mock         URL <Plurai backend REST base>/mock/<env id>
<package dir>/generated/externalCredentials/Plurai_Mock_Auth the credential SHAPE; no secret
<package dir>/generated/permissionsets/Plurai_Mock           access to the classes and the credential
<DX root>/generated/manifest/                                package.xml + destructiveChanges.xml
<DX root>/generated/apex/plurai_mock_health.apex             the smoke script
```

`<DX root>` is the directory holding `sfdx-project.json`, which is the agent
root in a standard checkout and one directory below it when the DX project is
one part of a larger repo. `<package dir>` is what that manifest declares as its
default `packageDirectories` entry. The rewrite resolves both and prints them
with `--paths`; nothing else in the mock path spells a layout of its own.

**No secret is generated and none is written.** The External Credential
describes only the shape of the credential. The org token reaches its principal
at deploy time over the Connect REST API, on stdin, from the environment — never
a command-line argument, never a file, never a tracked file. Do not put
`PLURAI_TOKEN` anywhere but the shell.

Add the generated trees and the copied files to the agent repo's ignore rules
alongside `plurai_sim/`. They are written against one mock env and one scenario
set, so a tracked copy goes stale the moment either is regenerated.

## Step A4: Copy the mock-path lifecycle into the workdir

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cp "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/agentforce_mock_path.sh" "$WORKDIR/"
```

Three verbs, and the customer owns all three:

| Verb | What it does | What it leaves |
|---|---|---|
| `up [rewire args…]` | regenerate, warm the Plurai backend, health-check the mock env, push the org token to the credential principal, deploy classes and permission sets, grant them to the CLI user and the agent user, run the Apex health callout, deploy/validate/publish/activate the copy, record its id | a live rewired copy and `$WORKDIR/.mock_agent_id` |
| `down` | deactivate the copy | everything deployed; `up` brings it straight back |
| `clean` | deactivate, retrieve `Agent:<copy>` into a scratch dir, destructive-deploy what `publish` minted (pre) and the generated manifest (post) in one deploy, delete the generated tree and the recorded id | only retrieved records it cannot delete (namespaced), which it names on screen |

It asks `rewire_agentforce.py --paths` for the agent root, the DX root, the
package directory and the workdir it records the agent id at, so both DX layouts
and both workdir layouts are resolved in one place and the shell spells none of
them. It sources the copied `resolve-plurai-urls.sh` for the callout base,
which is why that file travels with it.

`SF_ORG_ALIAS` names the org. Unset, the `sf` CLI's own default target org is
used, which is what a single-org project relies on.

## Step A5: Emit the harness (`$WORKDIR/simulate.py`)

**Read
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/references/simulate_py_agentforce.py.txt`
and emit it verbatim** as `$WORKDIR/simulate.py`, ownership header included. Keep the structure exactly.
Four things are filled in, and nothing else:

1. **`_COPY_NAME`** — the ONE placeholder, `<rewired-copy-developer-name>`. Its
   value is the bundle's `developer_name` plus `_Plurai`, which is also the
   `copy=` line printed by
   `python3 "$WORKDIR/rewire_agentforce.py" --names`. Read it from there rather
   than assembling it by hand.
2. **`_CONTEXT_VARIABLES`** — `{scenario agent_context key: Agent Script
   variable}`, and empty unless the customer's own bundle declares variables
   marked `visibility: "External"`. A variable without that marking cannot be
   set at session start, so listing it here silently drops the value. The
   rewrite marks the session id and nothing else.
3. **`_FILE_VARIABLE`** — empty unless the agent takes a document from the user
   (a `.document_fields` entry, or a reader action whose input is a file). Then it
   is the bundle's `visibility: "External"` Text variable that reader action's file
   input is bound from (`with <input> = @variables.<name>`). A variable the bundle
   does not mark External is dropped from a message without an error, so the
   harness refuses a file turn on one; leave item 3 empty for such an agent and
   say why in the run instructions.
4. **`_ENV_FILE`** — `None`, unless the bench declares `harness.env_file` (a file the
   user named for the agent's settings): then that value.

**File input.** The Agent API carries no attachment, so a handed-over document
reaches the agent as a file the harness uploads to the org, its ContentDocumentId
set on `_FILE_VARIABLE`. When item 3 is filled:

- Write `agent.io: {input: [text, file]}` at the ROOT of `$WORKDIR/plurai.config.yaml`.
- Copy `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/references/document_input.py`
  beside `simulate.py` as `_documents.py`, unchanged; the harness reads every
  turn's parts through it.
- The reader action (a `generatePromptResponse://` target) stays real, so
  `--plan` lists it under `denied` until it is granted. On the scan's map type it
  `"writes": "none"`, `"effects": ["none"]`, `"runs": "provider_hosted"`; `--write-lanes` gives
  its entry `observation_lane: none`.
- `.host_effects_consent` must grant both the reader action and `file_uploads`,
  and only the scan's host-effects ask or the `/simulate` start answer writes them (deny by default): each file
  is written into the customer's org and deleted when its conversation ends
  (Salesforce keeps a deleted file in the org's Recycle Bin for 15 days).
  The Agent API client's run-as user needs create and delete access to files and
  the right to share them: a file is private to its uploader, so the harness
  shares each one with the bundle's `access.default_agent_user`, the user the
  reader runs as.

Contract notes, each one load-bearing:

- `seed_state` opens the org session; `invoke_agent` only forwards the new user
  turn. The conversation lives in the org, keyed by its own session id, so
  `config['configurable']['thread_id']` is unused here on purpose.
- `plurai.active_session_id()` is read INSIDE `seed_state`, never at import:
  `plurai.configure` and `plurai.start_session` run first and the accessor is
  `None` outside an active session.
- `initial_world_state` is untouched. The mock service was already seeded with
  this scenario's world before the hook ran, and the agent holds no state of its
  own outside the org.
- `seed_state` returns `None` — full fidelity for a fresh scenario. It REFUSES a
  `resume_seed`: an Agent API session opens on an empty conversation, so
  replaying pinned turns would re-run and re-meter their actions and still
  produce a different conversation wearing the trace's words. Declare it in
  `bench_manifest.json`: `"harness": {"resume": {"mode": "unsupported", "reason":
  "history_not_writable", "evidence": "simulate.py:<line of the raise>"}}`.
- The agent id comes from `SF_MOCK_AGENT_ID`, else `$WORKDIR/.mock_agent_id`
  written by the bring-up. There is deliberately no fallback to `SF_AGENT_ID`:
  that names the customer's OWN agent, whose actions never reach the mock
  service, so the run would be metered and ungradeable.
- The turn's chain is each mock-recorded call (`AIMessage` with `tool_calls`, no
  id) + its `ToolMessage` result, then ONE reply `AIMessage` (`_turn_chain`) whose
  content carries the reply's `citedReferences` as `citation` parts — the sources
  the user sees with it (`_shown_with_sources`).
  End-to-end tool evidence still comes from `tool_events`; the chain is what the
  per-turn judges read, and the SDK counts a call once across both. Do not add
  ids to those calls: the mock's log has none, and an id on one side only stamps
  every mock tool's count as a lower bound.
- The Knowledge-search tool name is not filled in: `seed_state` reads it from
  `plurai.config.yaml`, found the way the SDK finds every declaration beside a
  harness (beside `simulate.py`, or at the repo root above it): the one tool
  entry declaring `observation_lane: tool_trail_cited`. It files each cited reply under it with
  empty `args` (the search's query is not observed). A config
  with no such entry gives an empty trail; a missing config, one with two such
  entries, or two configs that disagree, raises before the org session opens.
- An empty reply is returned as the silence it is, never a `(no response)`
  sentinel, and the raw reply is recorded in `$WORKDIR/agent_api_replies.jsonl`.
- A turn handing over a file uploads it as a ContentVersion, shares its
  ContentDocument with the agent user (a viewer link; none when the run-as user
  is that user) and sends its ContentDocumentId as `_FILE_VARIABLE`, then
  re-sends that id on every later turn. It raises `plurai.HarnessFault` before
  any upload when `_FILE_VARIABLE` is empty or not an External string of the
  rewired copy, the turn carries more than one file, `file_uploads` is not
  granted, or the agent user is not a user of the org; and after one when the
  upload or the share fails for any reason. Each id goes on
  `$WORKDIR/uploaded_files.jsonl` the moment it exists (the version id, then
  its document id), before the message is sent; the session deletes its files
  at close, and `seed_state` deletes any a finished scenario left behind.
- Every refusal these notes name, and a reply carrying the callout class's own
  `mock service returned …` text, raises `plurai.HarnessFault` (the row is
  `not_valid`, not the agent's); a `Failure` reply about the agent's turn
  stays a plain raise.
- The org connection settings — `SF_MY_DOMAIN_URL`, `SF_CLIENT_ID`,
  `SF_CLIENT_SECRET` — are read from the environment Claude Code passes to
  commands, then from `_ENV_FILE`, else the `.env` at the Agentforce PROJECT root
  (the template walks up from itself to find it, and stops at that root). An
  exported one is enough: no `.env` is needed. Before saying anything about them, run
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" env-check SF_MY_DOMAIN_URL SF_CLIENT_ID SF_CLIENT_SECRET`;
  ask only for a name it reports missing, naming where it looked, and never
  about creating a file. When it reports one found in a file outside the
  project root, write that file's absolute path into the manifest's
  `harness.env_file` (and so `_ENV_FILE`), or the run would not read it. The
  template names any still missing at run time.

The python env that runs `simulate.py` needs `plurai` and `requests`: install
the SDK as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line`
prints, plus `pip install requests`;
or set `PLURAI_CHILD_PYTHON` to a venv that has both.

## Step A6: Emit the run instructions (`$WORKDIR/AGENTFORCE_MOCK_PATH.md`)

**Read
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/references/run_instructions.md.txt`
and emit it** as `$WORKDIR/AGENTFORCE_MOCK_PATH.md`, replacing every placeholder:

| Placeholder | Value |
|---|---|
| `<agent-root>` | the Agentforce project root, the directory holding the DX project and `plurai_sim/` |
| `<workdir>` | `$WORKDIR`, written relative to the agent root |
| `<agent-developer-name>` | the bundle's `developer_name` |
| `<rewired-copy-developer-name>` | the same plus `_Plurai`, from `--names` |
| `<org-alias>` | the org alias the customer uses, or the one they name |
| `<Plurai backend URL>` / `<Plurai backend token>` | leave both as the descriptions they are — **never write a real URL or token into this file** |

The file is the deliverable that makes the mock path the customer's rather than
the skill's: bring-up, the run, teardown, and what each failure means, in order,
in one place. **A customer with no Claude Code installed must be able to follow
it**, so keep both run forms — `/simulation-run` and the direct SDK runner — and
never replace a command with "ask the skill to do it".

Fill the runner's `--scenario-id` lines from the scenario set once it exists.
Until then leave the two example ids: an instruction file that runs the whole
set by default is an instruction file that spends the customer's credits by
default.

## Step A7: Smoke — the Apex health callout

The smoke is the bring-up, and its verdict is the health callout: Apex running
inside the org, reaching the Plurai backend through the named credential, over
exactly the path a rewired action takes.

**Branch on the mock env id before anything else:**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
[ -n "$(tr -d '[:space:]' < "$WORKDIR/.env_id" 2>/dev/null)" ] && echo present || echo absent
```

**Absent → the smoke is deferred, and on a first scan that is the expected
outcome, not a failure.** The copy is deployed against ONE mock env and no env
exists yet. Print one line, record the smoke as deferred in what you report to
the user, and return to the dispatcher. Do not ask, do not deploy, do not
generate — an ask here is denied outright when this skill runs headless, and the
setup it would block is otherwise complete:

> The Agentforce mock path is set up but not brought up yet: the rewired copy's
> callout URL carries a mock env id, and `/simulation-generate-suite` is what
> produces one. After the suite, run
> `bash <workdir>/agentforce_mock_path.sh up` — it generates the copy against
> that env, deploys it, and runs the health callout.

Put the resolved `$WORKDIR` in place of `<workdir>` before printing it. The
customer runs that line in their own shell, where `$WORKDIR` is unset, and a
named-env bench keeps the script under `plurai_sim/envs/<name>` rather than
`plurai_sim/`.

**Present → ask, then run.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
bash "$WORKDIR/agentforce_mock_path.sh" up
```

**This is the only step that touches the org, and it needs a logged-in `sf` CLI.
Ask before running it, unless the `/simulate` start answered it.** It deploys metadata and pushes a credential; that is
the customer's org, not yours. `up` runs only when `.host_effects_consent` grants
`route_smoke`, and exits 3 naming that remedy otherwise. The same ask names each
action `--plan` lists under `denied`: the copy will not call it, so a scenario that
needs it shows the agent unable to take that step, unless the customer grants it too.

Success is `sf apex run` completing on `generated/apex/plurai_mock_health.apex`
and the run ending with the copy active against the mock env. The script throws
on anything but a 200 from the callout, so a failed health check stops the
bring-up rather than activating an agent that cannot reach the mock service.
Report the result — the callout's verdict, the actions left real, and the copy's
recorded id — rather than a bare "up succeeded".

The health callout is Apex, not an agent turn, so it costs no Flex Credits.
**Do NOT reach for `sf agent preview` or `sf agent test run` to prove the copy
works.** Those are metered agent turns, and the first real scenario run proves
the same thing while producing evidence the eval pipeline can read.

Symptom-to-cause table (your triage material — classify, fix, re-run silently;
never paste raw errors or stack traces at the user):

| Symptom | Cause → fix |
|---|---|
| `no mock env id; run the plugin's env generation` | `.env_id` is missing under the workdir. Expected on the first scan, where Step A7 defers the smoke instead of reaching this. Reaching it from `up` means no world has been deployed → run `/simulation-generate-suite`, then `up` |
| `the mock env has no live deployment` (410) | the env id resolves but nothing is deployed for it → regenerate the env |
| `the Plurai backend never answered 200 on /healthz` | `PLURAI_URL` points somewhere else, or at a base nothing serves; a 503 that never clears means the server is down |
| `PLURAI_TOKEN is empty` | the token is unset → it is shell-only, and the credential would otherwise be created with no secret |
| `is not an absolute URL` / non-HTTPS refusal | the base URL has no scheme or host, or is plain HTTP off loopback → a callout on such a URL leaves the org and reaches nothing |
| the org refuses the credential POST **and** the PUT | the External Credential deployed but the principal did not, or the running user cannot manage it |
| the Apex health callout returns a non-200 | the permission-set grant did not reach the running user, or the named credential's URL is not the current env's |
| `expected exactly one retrieved bundle` | several bundles under `aiAuthoringBundles/` → pass `--bundle-dir` naming the one under test |
| `the bundle declares no access.default_agent_user` | the agent's own user cannot be granted the permission set, so its actions would be refused at run time → the bundle must declare it |
| `NoVersionsAvailableError` on activate | the copy is already active; the bring-up absorbs this one and says so |
| the copy activates but a run produces no tool events | the harness drove the customer's own agent → `SF_MOCK_AGENT_ID` is pinned to the wrong id, or `.mock_agent_id` is stale from a previous `clean` |
| an action the customer expected to be mocked is listed as left real | read the reason the rewrite printed (Step A2's table); the fix is in the bundle, not here |

## What downstream skills need to know (recorded here, used by Step 11+)

- `/extract-scenario-variables`: `agent_purpose` = the bundle's agent description
  and its topic instructions; `agent_tools` = the action names, mocked and real
  alike, less the ones `up` denies; `entity_schemas` = the Salesforce objects the actions' inputs and
  outputs name. Complications vocabulary: an action left real (a live org side
  effect), an empty result from a mocked action, a missing required input the
  agent must ask for rather than invent.
- `/generate-evals`: the standard dialog catchers apply. The agent reasons
  server-side, so what it did is judged against the lane each tool's entry
  declares: `tool_events` for a mocked action; the cited-call lower bound for the
  Knowledge search, where no entry is not evidence of no call; and nothing for a
  tool on `none`, whose calls and effects no record shows, so no judge fails a
  scenario for their absence.
- Run skills need no new flag: mock-mode precedence already lands on `connect`,
  and the org bearer is environment-only. The per-turn budget default covers
  an Agent API turn, which the platform caps at 120 s.
- The handoff branch is dialog-shape, and env generation IS required: the
  rewired copy's callout URL carries the mock env's id, so nothing is deployed
  until a world exists. The scan only plans. `/simulation-generate-suite` runs
  the declared `route_smoke` (`up`) once the world exists, before its first
  paid turn.

## Return to dispatcher

Smoke green, or deferred because Step A7 found no `.env_id` → go back to
the `simulation-setup` skill (already loaded) at Step 11
(chained skills: extract-scenario-variables → generate-evals), then the
post-setup handoff.
