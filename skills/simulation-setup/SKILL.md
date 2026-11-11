---
name: simulation-setup
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Analyze an agent codebase and generate its simulation wrapper (simulate.py, plurai.config.yaml, simulation_prd.md). Use for "set up simulation", "set up plurai", "generate simulate.py". Internal building block; prefer /simulation-scan-agent.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Plurai Simulation Setup

**PRD facts contract.** (1) Write the PRD without an `## Agent facts` block and run `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scaffold_rule_facts.py --profile artifacts/data_profile.md` before you type any fact: it adds the block and writes the `instruction_sources`, one `rule` fact per instruction, a line or the lines one wrapped sentence spans (`subject` = `source` = `path:LO-HI`, `evidence` = the agent's own bytes), the quoted block, and the measured facts the profiled files settle (closed URL sets, URLs outside them, topics no document covers); confirm or drop each. `--source` is the text the model is given as instructions: prompts, task and agent configs, each skill or rules file (`.claude/skills/*/SKILL.md` for a Claude Agent SDK agent), each live sub-agent's own prompt, and what fills each template `{slot}`: follow it to the code, declare the file or range that fills it with instructions, decline a data slot. The lint reports a loaded file you neither declared nor declined. Never one fact per file, never a paraphrase, never a slug for a subject. (2) Add only facts you measured: `kind`, `subject`, `predicate`, `evidence` (a number or the quoted bytes), `source`; cite code as repo-root-relative `path:LINE` or mark the claim `(inferred)`. (3) Run `lint_prd.py` until it exits `0`: exit `4` is open work (a measured fact you never typed counts), and a finding stays only with `--accept <fp> --reason`. (4) Write the prose from the agent's own instructions and code, never as a summary and never by copying a corpus document (the verbatim appendix carries those; summarise a corpus procedure in one line with its `path:LO-HI` citation): it states the agent's hard limits (context and history handling, how the prompt is built, model and env defaults, size caps), every closed vocabulary or catalog the agent stays inside with all its members, its absences (what it cannot do), the procedures its own instructions state as numbered steps, and two or three example exchanges that show the expected reply shape; `lint_prd.py` prints a hint, not a finding, when the prose shows too few of these or repeats appendix lines. The worked block and the required prose content are in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/prd_authoring_contract.md`.

Generate a `simulate.py` file that lets the developer run Plurai's simulated user + mock tools + evaluators against their agent locally, without production infrastructure. An agent whose code is not Python keeps running in its own runtime: `simulate.py` drives it through a bridge child process (Node today). A hosted agent — one that runs on a vendor's servers rather than in the developer's process — gets the same `simulate.py` hook shape over a remote session instead of a local import; the Agentforce sub-skill is the worked case.

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

## Step 0 (FIRST): Check for an existing setup — recommend it, but NEVER assume it

Before anything else, check whether a Plurai setup already exists:
- the working tree (`<agent>/plurai_sim/` — `simulate.py`, `plurai.config.yaml`, `_mocks.py`, `_storage.py`, …), **and**
- git HEAD / history (it may be committed even if deleted in the working tree).

**If one exists, you MUST NOT silently adopt it.** A new simulation trigger does not mean "re-use whatever's on disk or in git" — the user may want to test a **different component**, a **different mock strategy** (e.g. which tools run real vs mocked), or different scenarios. Reusing a prior config without asking has bitten us: an agent reached into git HEAD for a deleted-in-working-tree bench and assumed its prior real-vs-mocked split, when the user may have wanted something else entirely.

Instead:
1. **Surface** what you found, concisely: which agent/component it targets, the mock strategy (which tools real vs mocked + why, from the config/`_mocks.py` docstrings), and whether it's in the working tree, git HEAD, or both.
2. **Recommend** reusing it *if* it plausibly matches the current request — frame it as a recommendation, with the one-line rationale.
3. **ASK and WAIT.** Explicitly ask whether to (a) reuse the existing config as-is, (b) reuse it but target a different component / change the mock strategy, or (c) set up fresh. Do **not** proceed past this point until the user answers.

**Reached from `/simulation-scan-agent`, its Step-1 choice is the answer** — do not ask again; its
re-analyze is (c) with the saved PRD as the rescan diff's old side.

Only when there is **no** prior setup anywhere, OR the user has explicitly chosen to reuse/adapt one, do you continue to Step 0a. (This gate is about the *config/design* decision; it's separate from the autonomous downstream steps like eval-gen, which intentionally don't pause.)

## Step 0a: Detect Agent Shape (do this BEFORE asking the user any clarifying questions)

Plurai supports **FOUR** first-class agent shapes. Pick one based on what the customer's code actually is — do NOT push back, ask "how should I proceed," or suggest the customer rewrite their agent into a different shape.

| Shape | Signal | Hook to emit |
|---|---|---|
| **Dialog / tool-using** (common) | Multi-turn user↔agent loop; agent calls tools (DB, mutations, APIs); LangGraph / LangChain / CrewAI / Mastra / OpenAI Agents SDK | `invoke_agent(payload, config) -> {'messages': [...]}` |
| **One-shot LLM pipeline** | ONE LLM call, no user dialogue, no tools (or just text-in / text-out). Pure-function: structured inputs → structured outputs. Examples: labeled-data generators, classification heads, summarization wrappers, structured-output extractors. | `invoke_pipeline(inputs: dict) -> dict` |
| **Claude Code skill/command** ("skill agent") | The "agent" is a Claude Code plugin command or skill: a markdown workflow (`commands/*.md`, `skills/*/SKILL.md` with frontmatter) executed by Claude Code, calling MCP tools against a backend service. Repo signals: `.claude-plugin/plugin.json`, `commands/`+`skills/` dirs, `.mcp.json` — and NO agent entry point of its own (Python, Node, …). User asks "test my skill / my command / my plugin". | `invoke_agent(payload, config) -> {'messages': [...]}` — `simulate.py` hosts a persistent nested `claude-agent-sdk` session whose system prompt is the rendered command markdown and whose only tools are the skill's (mocked) tool surface |
| **Hosted vendor agent** (Salesforce Agentforce) | The agent runs on the vendor's servers; the repo holds only its metadata. Signals: `sfdx-project.json`, a `force-app/` tree, `**/aiAuthoringBundles/*/*.agent` — and no importable agent loop. User asks "test my Agentforce agent". | `invoke_agent(payload, config) -> {'messages': [...]}` — `simulate.py` opens ONE remote Agent API session per scenario and drives a rewired COPY of the agent whose mockable actions call out to the mock service |

**The `invoke_agent` payload contract (every dialog-shape harness):** per turn, `payload['messages']` carries THIS turn's user side and nothing else — never the conversation so far. That is ONE message on an ordinary turn, `[]` on the opening call at a bench that declares `agent.io: {may_open: true}` (the agent opens, so turn 0 has no user side at all), and on a turn carrying an attachment it is still one message whose `content` is a list of parts rather than a string — and `**agent_context` is spread in on TURN 1 only. History is the HARNESS's job, keyed on `config['configurable']['thread_id']` (stable per scenario): an agent on a graph framework compiles with that framework's saver; any other harness keeps a thread-keyed store of its own. A harness that rebuilds history from `payload['messages']` runs stateless and nothing flags it — the run completes and every memory/journey/continuity verdict is meaningless (observed live for a full qualification pass).

**Skill agents are dialog-shape at the protocol level** — the SDK/orchestrator/server see a normal `invoke_agent` dialog agent. What differs is everything inside `simulate.py` (the nested session) and the tool-surface extraction (from the command markdown's named tool patterns, not from importable functions). One bench per command/skill — a plugin with many commands gets one env per command under test, not one merged env.

**Hosted agents are dialog-shape too, and the mocking seam moves.** Nothing local
imports the agent, so no import-time patch can reach its tools. The seam is the
agent's own outbound callout: the sub-skill generates a rewired COPY whose mockable
actions call the mock service, deploys the copy, and drives that. The customer's agent
is never edited, and the copy is deployed against ONE mock env — so env generation runs
before the harness works at all, not after it.

**Pipeline-shape agents are first-class, NOT a degraded case.** If the customer's agent is a single-call LLM pipeline with no tools and no user-facing dialog, that IS supported — generate the `invoke_pipeline`-only `simulate.py` and proceed. Do NOT emit "Approach" warnings of the form *"X is a single-call LLM pipeline (no user dialogue, no external tools, no DB/API to mock). Plurai simulation is designed for conversational, tool-using agents — it doesn't naturally apply here. How should I proceed?"* — Plurai DOES apply, via the `invoke_pipeline` hook driven by ordinary generated scenarios: the bridge hands the scenario's `initial_agent_context` to the hook AS the typed input dict, which is the one supported route in. (There is no `pipeline_io.yaml` to author — nothing generates that file, so a bench that waits for one waits forever.)

**For pipeline shape, simulate.py is ~10 lines and SKIPS Steps 2-4 of the Analysis Protocol below** (no tool mocks, no context-loading replacement, usually no production dependencies to stub beyond the LLM client):

```python
import plurai
from customer.pipeline_module import pipeline_function

def invoke_pipeline(inputs: dict) -> dict:
    return pipeline_function(**inputs)
```

The language sub-skill (Step 10) has the full template + variations. Eval policies for pipeline benches reference `pipeline_inputs` / `pipeline_outputs` directly — see `generate-evals` SKILL Step 3d.

## Step 0: Detect Language

Before anything else, set one value, `LANG`, from the agent's own code:

```
Python indicators:
  - pyproject.toml, requirements.txt, setup.py, Pipfile
  - .py files in src/ or agent/
  - imports: langgraph, langchain, crewai, openai (Python SDK)

Node indicators (TypeScript or JavaScript):
  - package.json, tsconfig.json, yarn.lock, pnpm-lock.yaml
  - .ts, .tsx, .js or .mjs files in src/ or agent/
  - imports: @langchain/langgraph, @openai/agents, @mastra/core, ai (Vercel AI SDK)

Claude-skill indicators (override the two above when present):
  - .claude-plugin/plugin.json or .claude-plugin/marketplace.json
  - commands/*.md or skills/*/SKILL.md with command frontmatter (name, allowed-tools)
  - .mcp.json wiring plugin MCP servers
  - package.json exists but only to ship the plugin/MCP server, NOT an agent loop

Agentforce indicators (override all three above when present):
  - sfdx-project.json — a Salesforce DX project
  - **/aiAuthoringBundles/*/*.agent — the retrieved Agent Script bundle (the decisive one)
  - force-app/main/default/ with classes/, flows/, permissionsets/
  - .py files exist but only as an Agent API / preview transport, NOT an agent loop
```

`LANG` is one of `python | node | claude-skill | agentforce`, or the name of any other runtime the agent's
code is written in (Go, Java, Ruby, …). Every harness is a `simulate.py`; the row decides how it reaches the
agent and which sub-skill Step 10 reads:

| `LANG` | harness | Step 10 runs |
|---|---|---|
| `python` | in-process `simulate.py` | `simulation-setup-python` |
| `node` | `simulate.py` + the bridge, `child: node` | `simulation-setup-bridge` |
| `claude-skill` | nested-session `simulate.py` | `simulation-setup-skill` |
| `agentforce` | hosted `simulate.py` over the Agent API | `simulation-setup-agentforce` |
| any other runtime | **none** | stop |

**Any other runtime is a stop, never an improvised harness.** Tell the user "no bridge child ships for `<LANG>`
yet" and that the bridge contract of the `simulation-setup-bridge` skill is what a child implements;
write nothing further. (A claude-skill repo often *contains* JS for its MCP servers — the plugin manifest +
command markdown is what decides.)

**The Agent Script bundle is what decides `agentforce`, not `sfdx-project.json` alone.** With a
`sfdx-project.json` present, first run
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/agent_kind.py" --project-dir <agent root> --flow-dir "$FLOWDIR" --art "$ART"`.
Exit 3 (`kind: builder`) is an Agent Builder agent, which has no Agent Script bundle to retrieve:
show its `message` as written and stop (the typed stop is already filed). Its one remedy is to upgrade
the agent to the new Agentforce Builder (Setup → Agentforce Agents → the agent's menu → Upgrade to New
Builder), retrieve the upgraded agent's Agent Script bundle, and scan again — never a retrieve of the
legacy agent.
On `kind: no_agent`, a DX
project with no `aiAuthoringBundles/` holds Salesforce metadata and no agent — say so and stop,
rather than setting up a bench for an agent that is not there. A bundle that exists in the org but
has not been retrieved is the same situation from this side: ask for the retrieve. An Agentforce
repo that ALSO ships an agent loop of its own (any runtime) is two agents; ask which one is
under test rather than assuming the hosted one.

**SDK to use:**
- Python: install the SDK as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints → `import plurai`
- Node agent: the same Python SDK, plus Node ≥ 22.15 and the bridge runtime (`simulation-setup-bridge`).
- Agentforce: the Python SDK above plus `requests` — the harness is Python and reaches the agent over the Agent API. The org side needs a logged-in `sf` CLI, which the customer already has.

## What You're Building

`simulate.py` does four things (on the bridge, Phase 1 stubs become loader swaps declared in `bridge/bridge.map.json`):

1. **STUBS** (Phase 1): Replace production dependencies with MagicMock so the agent module imports without real DB/Redis/API connections. Stubs are dumb — they accept any call without crashing but never return useful data.
2. **Context replacement** (Phase 3): Replace context-loading nodes (that read user profiles, sessions from DB) with versions that read from the scenario.
3. **MOCKS** (Phase 4): Register mock functions via `@plurai.mock`. Each mock has TWO parts: a dumb inline function (just WorldState lookup, enough to not crash) and a rich descriptor (detailed metadata about the tool for the backend to generate real mocks later). The descriptor captures: what the tool reads/writes, return format, business rules, dependencies, error cases.
4. **Run** (Phase 4-5): Import the agent (it picks up mocked tools automatically) and run with Plurai simulation.

**Descriptor `args`/`returns` — prefer the structured dict form.** `ToolDescriptor` accepts a bare
description string (e.g. `returns: "List of books ranked by rating"`) and coerces it to
`{description: <str>}` rather than rejecting the upload — so this won't abort `generate_env_start`.
But the structured form (`returns: {type, fields: {...}}`, `args: {arg_name: {type, description}}`)
is what feeds richer backend mock generation (e.g. state-bearing return fields for stateful tools);
a bare string silently loses that. `verify_config.py` flags bare strings as a `warn` finding for
this reason — treat it as a nudge to use the richer form, not a hard requirement. See
`references/example_plurai_config.yaml` for the shape.

**A mocked tool's `returns` (type, fields, description) is written from the best evidence available, and
`returns.type_source` names which.** Best first:
- `plugin`: a value the plugin decided (an edit session or a loop edit). Never change it. Where the code disagrees,
  derive_tool_facts prints a `!` note, keeps the value and exits 0: the note is advisory, not a violation to fix.
- `code` and `annotation`: written by derive_tool_facts from the tool's own source, never by hand. They outrank every
  source below, and the script re-derives them on each run, so an edit to a `returns` the code types does not stick.
- `trace`: the trace brief's observed output (`evidence: [trace_brief:<workspace>#tools.<tool>]`). When a trace brief
  exists for this workspace (`get_trace_brief_meta`), read its `tools.md` first.
- `declared`: the tool's own declaration on disk (its docstring, its tool-definition entry, an output schema, or the
  schema of the store its edge reads), with that file:line as `evidence`. Read it at the `declaration_sites`
  derive_tool_facts prints. An API's public documentation you remember is not on disk, so it is not `declared`.
- `stub_run`: a read-only probe with the external edge stubbed (Step 8c.2).
- `inferred`: your reading of the code, with the lines you read as `evidence`.
- `declared_unknown`: nothing types it, written with its `unknown_reason`.

Never run a write-capable tool to learn its shape. A shape you inferred is `inferred`, never `declared_unknown` with
a description or fields; `declared_unknown` is `type: unknown` with its `unknown_reason` and no fields (never a union
guess). For a Python agent, once the config is written, run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/derive_tool_facts.py" plurai_sim/plurai.config.yaml --root . --write`:
it writes `returns.type`, `type_source` and `evidence` and `args.<a>.nullable` from the code, and keeps a source you
wrote unless its own derivation strictly outranks it. It also writes `type` and `type_source` on each `returns.fields` entry
the code types (`annotation` for a TypedDict, dataclass or model field, `code` for a returned dict literal); a field it
cannot type keeps no source, so production traces may replace its type. `verify_config.py` warns on a type without provenance.
The same run writes `scanned_at` at the top of the file and a citation on each `returns.fields` entry the code names
(a field without a citation is still `declared`).
Where the agent's own pydantic model, TypedDict or dataclass types a return or a field, the same run writes its nested shape
(`fields`, `item_fields`, `optional`, `wire_name`, `truncated`), and adds the fields the config omits; never hand-edit it.
A `?` line or `source not found` summary says the code was not read: correct `original_module`, `original_function` or `--root`, never relabel the block.
Leave `args.<a>.description` and `returns.description` empty when you have nothing beyond the tool's declaration; derive_tool_facts
fills them from it (`description_source: declared`). A placeholder is text, so it is never filled.
`returns.fields` lists every field the documented response carries (ids and timestamps included), not only the ones the agent reads today.
Where a declaration and the code differ, the code wins: an arg's accepted values, an entity's state list (`typical_states`) and `returns.fields`
are read off the producing line, following a library call into its source on the agent's interpreter, where a default argument that narrows a read is behaviour.
A value the declaration accepts that no code path returns is a cited `tool_surface` `omits` fact (`references/agent_facts.md`), and the arg's description says so.
A field the code or provider truncates carries `max_chars` on its `returns.fields` entry, citing the truncating line in `evidence`; the mock serves it cut to that length.

**Stubs vs Mocks — the critical distinction:**
- Stubs exist so the code LOADS (Phase 1, import time). They're MagicMock. They return garbage.
- Mocks exist so the simulation WORKS (Phase 4, runtime). They return real scenario data.
- The tool function body holds a reference to a stub (from the closure) but mocks intercept BEFORE the body runs, so the stub is never touched.

DO NOT modify the customer's source files. Everything happens in `simulate.py`.

**An agent with file or shell access is simulated inside a sandbox or container.** Its file and shell
tools act on the machine the simulation runs on, and what they read is recorded as it is.

## CRITICAL RULE: Never Change the Agent Architecture

**You are wrapping the agent, not rewriting it.**

The customer's agent might use LangGraph, old LangChain (Chain, AgentExecutor, LLMSingleActionAgent),
custom classes, CrewAI, OpenAI Agents SDK, Mastra, or raw Python/TypeScript with no framework.
You must simulate the agent AS IT IS. Never convert it to a different framework or architecture.

**WRONG — rewriting the agent:**
```
The original agent is a custom LangChain Chain with LLMSingleActionAgent.
That shape is not compatible with MockToolNode. We rebuild the agent as a
minimal ReAct LangGraph agent...
```
This is FORBIDDEN. You just destroyed what you're testing.

**RIGHT — mock the tools, import the agent unchanged:**
```
The original agent is a custom LangChain Chain with LLMSingleActionAgent.
We use plurai.apply_mocks() to patch the tool functions in their module
BEFORE importing the agent. The agent imports the already-patched tools.
It runs exactly as the customer wrote it — same Chain, same OutputParser,
same AgentExecutor. We never touch its architecture.
```

**The approach is the same for ALL frameworks:**
```
1. Stubs (prevent import crashes)
2. plurai.apply_mocks(tools_module, {...})  ← patch tools BEFORE agent import
3. import agent  ← agent picks up mocked tools through normal imports
4. agent.invoke(...)  ← run with agent's own entry point
```

This works for LangGraph (ToolNode gets patched tool references), LangChain (AgentExecutor
calls patched functions), CrewAI (agents call patched functions), and everything else.

No graph rebuild. No MockToolNode. No architecture conversion. Ever.

If the agent architecture makes mocking genuinely impossible (extremely rare), TELL THE USER
and ask how they want to proceed. Never silently rewrite their agent.

## Reference Files

Before starting, read the relevant reference files. After the dispatcher / language-sub-skill split, language-specific templates live under each sub-skill's `references/` directory; shared YAML examples stay under this dispatcher's `references/`.

**Python (read when LANG = "python"):**
- **the `simulation-setup-python` skill** carries the complete simulate.py template: all phases, including observe mode and dumb mocks with rich descriptors. It names the template as yours when you run it (Step 10).
- **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py`** — the simulate.py wiring contract, run from the plugin at the Python sub-skill's Step 9e and never copied into the agent's `plurai_sim/` (a copy answers for the contract of the day it was made). POSITIONAL argument, never bare:
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py" plurai_sim/simulate.py`
- **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_config.py`** — the `plurai.config.yaml` descriptor contract. Lives DISPATCHER-side, not under the Python sub-skill, because the config is language-agnostic — a bridge agent's descriptors are checked by the same script. (Keep it at exactly one path: a second copy under the same name would be ignored by the reference check.) Also positional:
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_config.py" plurai_sim/plurai.config.yaml --simulate plurai_sim/simulate.py`
- **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/audit_state_preconditions.py`** — the state-precondition audit: walks the agent's AST and lists every literal-string key read from `state` / `agent_context` / `ctx` / `payload` / `config.configurable`, marking the ones the schema does not declare. A review gate, not a hard blocker:
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/audit_state_preconditions.py" <module_root> [--prd plurai_sim/simulation_prd.md] [--schema plurai_sim/scenario_state_schema.yaml] [--exclude GLOB]`

**Bridge (read when LANG = "node"):**
- **the `simulation-setup-bridge` skill** — the bridge generation phase: seam discovery, the per-agent map and entry, the runtime install, the `simulate.py` template, and the protocol contract between `simulate.py` and the bridge child. You run it at Step 10.

**Hosted Agentforce (read when LANG = "agentforce"):**
- **the `simulation-setup-agentforce` skill** carries the complete harness template (emitted VERBATIM, only `_COPY_NAME` and `_CONTEXT_VARIABLES` are filled in; it opens one Agent API session per scenario against the rewired copy and binds the session id into every mocked action) and the customer-facing `AGENTFORCE_MOCK_PATH.md` template (bring the rewired copy up, run the suite, tear it down; placeholders only, never a real URL or token). You run it at Step 10.
- **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/rewire_agentforce.py`** — builds the rewired copy's metadata from the Agent Script bundle. Copied UNCHANGED into the agent's workdir (unlike the Step 9e / 9f verifiers, which run from the plugin), so it keeps working with the plugin absent.
- **`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-agentforce/scripts/agentforce_mock_path.sh`** — the `up` / `down` / `clean` lifecycle for the rewired copy. Copied UNCHANGED into the workdir for the same reason: teardown has to still work months later, in a checkout with no Claude Code installed.

**Shared (all shapes — live alongside this dispatcher SKILL.md):**
- **`references/example_plurai_config.yaml`** — A complete plurai.config.yaml with WorldState entity types, tool relationships, observer definitions, stubs documentation. This is the main output the backend reads. Be thorough.
- **`references/example_plurai_mocks.yaml`** — Example of what the SDK exports after running (auto-generated from mock descriptors). Shows the descriptor format.

## Analysis Protocol

### Step 1: Read the agent code

Read the agent's main module (the path the user provides) and ALL files it imports from the project. Recursively follow imports. Build a complete picture of:

- Where is the agent graph/chain defined?
- What is the compiled app variable?
- What tools are registered?
- What checkpointer is used?
- What happens at import time (module-level code)?
- What happens at runtime in each node?
- What is the model told: where each system prompt (the agent's and each live sub-agent's) is rendered, and every file and range its text comes from, template slots included?

Use Grep and Glob to find all relevant files. Patterns differ by language — use the set that matches Step 0's `LANG`:

Grep for the symbols of the framework Step 0 identified, not for every framework at once
— a hit on a name the repo does not use costs a file read and teaches you nothing.

**Python:**
```
Glob for: **/tools.py, **/agent*.py, **/graph*.py, **/nodes.py, **/crew*.py, **/workflow*.py
Grep by framework:
  LangGraph          StateGraph, ToolNode, @tool, bind_tools, PostgresSaver, RedisSaver
  CrewAI             Crew, Agent, Task, @agent, @task, max_iter
  OpenAI Agents SDK  Agent(, Runner.run, function_tool, RunConfig, max_turns
  no framework       the module that owns the LLM client call, and the dict it threads through
```

**Node (TypeScript / JavaScript):**
```
Glob for: **/tools.ts, **/agent*.ts, **/graph*.ts, **/nodes.ts, **/workflow*.ts, **/*.mts, **/*.mjs
Grep by framework:
  LangGraph.js       StateGraph, ToolNode, tool\(, bindTools, PostgresSaver, RedisSaver, @langchain/langgraph
  Mastra             @mastra/core, createTool, Agent, Workflow
  OpenAI Agents JS   @openai/agents, Runner, run\(
  Vercel AI SDK      streamText, generateText, tools:\s*\{
```

**Agentforce:**
```
Glob for: **/aiAuthoringBundles/*/*.agent, sfdx-project.json,
          force-app/main/default/classes/*.cls,
          force-app/main/default/{namedCredentials,externalCredentials,permissionsets}/*
Grep in the .agent bundle:
  actions:            at indent 4 = the action DEFINITIONS; deeper = references from a step
  target:             the scheme that decides mockable-vs-real (apex, apexRest, api,
                      externalService, flow are rewirable; everything else stays real)
  variables:          existing `visibility: "External"` variables — the session channel
  access:             default_agent_user, needed by the rewired copy
```
There is no local agent loop to read here. The `.agent` bundle IS the agent, and any `.py`
in the repo is a transport (Agent API calls, preview scripts), not the thing under test —
do not follow its imports looking for tools.

A framework absent from these lists is not out of scope — read its quickstart for the three
things every list above names: where tools are declared, where the agent is constructed, and
where per-run state is held.

### Step 2: Identify production dependencies

Scan all imports and module-level code. Categorize every external dependency.

**Must stub (crash without production infra):**
- Database: `sqlalchemy`, `psycopg2`, `asyncpg`, `pymongo`, `prisma`, `django.db`, `sqlmodel`, `supabase`
- Cache: `redis`, `aioredis`, `memcache`
- External APIs: `stripe`, `twilio`, `sendgrid`, `slack_sdk`, `google.auth`, `boto3` (non-AI)
- Custom internal modules that import these

**Keep real (agent needs these to function):**
- AI/LLM clients: `langchain_openai`, `langchain_anthropic`, `openai`, `anthropic`, `litellm`
- Agent frameworks: `langgraph`, `langchain`, `crewai`

For each module to stub, note WHY it needs stubbing and what import-time side effect it causes.

**Run settings come from production's caller, read along the whole path.** The entry the harness
drives (a library, SDK or embedded client) may default a step cap, timeout, model or token limit
differently from what the product sends. Find the caller(s) of that entry in the deployment the
PRD describes (its server route, UI request, CLI, deploy config), never a bench runner or the
declared `harness.entry_wrapper`. Read each setting from the caller's request through any
server-side clamp or deploy-config maximum to the entry, citing each hop's line.
- One resulting value: the harness passes it, with the cited line as a comment beside it.
- Shipped callers disagree, or the value is set at deploy time where the repo cannot show it: the
  harness keeps the driven entry's own default. Write one `run_setting` fact (`harness_differs`,
  `references/agent_facts.md`) listing every value with its line, and judge in the PRD which
  deployment it describes. Never pick the larger value silently.
- PRD prose states the value the harness passes and where it comes from, never a client default
  as a business rule.
- Every setting where the harness passes something other than the driven entry's own default goes
  into the Step 7 plan and the topology approval, with both values and the cited line: a raised
  cap changes cost and behaviour, so the operator sees it before any file is written.

**Mock the backend, never the tool set.** Every capability switch (tool categories, toolsets, flags,
MCP config) keeps its production default; disabling one needs an operator answer naming it
(`harness.capability_overrides`), never `harness_differs` (`production_tool_dropped`).

### Step 3: Identify import-time side effects

Find module-level code that executes on import:

```python
# DANGEROUS — executes on import, crashes without infra:
db = get_db_session()                           # DB connection
redis_client = Redis(host="prod-redis")         # Redis connection
payment = PaymentService(api_key=os.environ["STRIPE_KEY"])  # Missing env var
config = db.query(SystemConfig).first()         # DB query at import time

# SAFE — just definitions, don't execute:
def get_user_profile(user_id): ...              # Function definition
class BillingAgent: ...                         # Class definition
```

### Step 4: Identify context-loading nodes

Find graph nodes that read from DB/Redis/services to populate agent state:

```python
# Pattern 1: Node queries DB
def fetch_user_context(state):
    user = db.query(User).filter(User.id == state["user_id"]).first()
    state["user_profile"] = {"name": user.name, "plan": user.plan}
    return state

# Pattern 2: Node reads session from Redis
def load_session(state):
    data = redis.get(f"session:{state['thread_id']}")
    state["session"] = json.loads(data)
    return state

# Pattern 3: Node calls internal service
def check_auth(state):
    perms = auth_service.get_permissions(state["user_id"])
    state["permissions"] = perms
    return state
```

For each, note: node name, what fields it populates, what data source it reads from.

**Content the agent puts into its OWN model input is reported on `input_context`.** When the agent's code adds
content to its model input each turn, outside the user message and the tool calls (a recalled memory, a fresh
snapshot of its own settings or state, pre-retrieved documents, the current date), the harness wraps that
injection call, a READ and never a write, and returns its return value exactly as the agent received it beside
`messages` as `input_context`: a list of `{source, content}` entries, `source` naming the read and `content` the
inserted text. Never call the source a second time to fill it: a re-read can differ from what the agent was given,
and judges would credit claims the agent had no grounds for. **Take the entries from what the model call RECEIVED,
never from a list of the injections you found:** wrap the agent's model call and report every message of its input
that the conversation you handed the agent does not carry (Python: `plurai.model_input_context(model_input,
conversation, source=...)`; any other language, the same difference), because an injection the agent function makes
itself on a condition (a fresh-state message only when its settings changed) is the one a hand-written list misses.
The judges read it as context the agent had, never as
something it said; without it, a reply grounded in that content reads as invented. An agent that injects nothing
returns no `input_context`. **The current date is also PINNED, not only recorded:** every read of "now" the agent
makes outside a mock (its own code, or a `mode: real` tool's body) is rebound in `seed_state` to the scenario's
`initial_agent_context['current_date']` at the module that does the read (the name it imported: `datetime`, `date`,
`time`, or its own clock helper), never process-wide; a timestamped `current_date` keeps ticking from the scenario's
time, while a bare date stays on its day; a read with no imported name to rebind (an inline `new Date()`) is recorded only (Python: §now of `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/seed-state-contract.md`).

#### Step 4b: Identify STARTUP PRECONDITIONS — state produced by a prior in-graph flow (CRITICAL)

The patterns above catch state loaded from **external services**. They MISS the most common
source of unfaithful simulations: **state a tool requires that is produced by the agent's own
PRIOR flow** (a one-time calibration / onboarding / setup phase that runs before the main loop).
If you only seed the external world and not this state, the main agent boots into a phase the
scenario did not ask for: a tool errors on the missing state, or a router sends every scenario back
to its first phase, and every "agent failure" downstream is a simulation artifact. State the agent's
own earlier turns write and its entry routes on is a prior flow even within one thread: declare it,
though a fresh start is also valid production.

Detect it two ways:

1. **Multi-phase architecture.** Look for a SEPARATE graph/flow that runs once before the main
   agent (signals: a second `StateGraph`/`.compile()`, an entry node / module named
   `*_flow` / `calibration` / `onboarding` / `initial` / `setup`, or a custom agent class with a
   setup→run split where one graph's output state feeds another). **Read that flow's code** —
   note every state field it PRODUCES.

2. **Tool-entry state prerequisites.** Grep the main agent's tools for a field read at entry that
   gates the tool — `x = state.get("X")` (or `runtime.state.get("X")`) immediately followed by a
   falsy guard that raises/early-returns an error:
   ```python
   prefs = runtime.state.get("onboarding_answers") or []   # ← example field name; use the agent's real one
   if not prefs:
       return error("onboarding step required in state")   # ← X is a PRECONDITION
   ```
   Any such `X` is a mandatory startup precondition.

For each precondition field, record: the field name, the flow/node that produces it, and — by
reading that producer's code — **what VALID values look like** (e.g. "the onboarding summary the
setup flow produces from the user's request; non-empty, consistent with the task"). You will stamp
these with `precondition: true` + that description in Step 8 so
scenario generation knows to populate them coherently (an empty value is hard-rejected at
generation time).

> **HARD RULE — never paper over a precondition with a default.** A gating read (tool errors /
> refuses when the field is missing or empty) MUST be `precondition: true`. Do **NOT** resolve it
> with `required: false`, `default: []`, or `default: ""`. An empty default re-creates the exact
> production-impossible boot state the agent's first tool errors on — every scenario then opens in a
> dead state, the agent thrashes, and the run shows a high tool-error rate that *looks* like an agent
> fault but is a harness defect. `required: true` alone is also insufficient (an empty `[]` satisfies
> presence). Only a genuinely OPTIONAL field — the tool works correctly when it is absent — may take a
> default. The `audit_state_preconditions.py` run (Python tail) lists every key the agent reads, for this review:
> `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/audit_state_preconditions.py" <module_root>`.

**State the prior flow DERIVES is not declared** — an id it resolves from a field you do declare, a record it loads by
one. On every start that skips that flow (a declared flag such as "already onboarded"), the harness computes it before
the first turn by calling the agent's own function that produces it — its resolver, its loader — never a value of its
own. The Step 9e verifier warns `start_mode_state_unseeded` when such a start leaves state the agent refuses to run
without.

**Declare each one in `plurai.config.yaml` `agent_context.fields` now (Step 8)**, and write it where the agent reads it
in `seed_state`. The scenario state schema does not exist yet: the env build writes it later, and it carries only the
`agent_context` fields this config declares.

### Step 5: Identify the checkpointer

```python
app = graph.compile(checkpointer=PostgresSaver(...))   # → replace with MemorySaver
app = graph.compile(checkpointer=RedisSaver(...))      # → replace with MemorySaver
app = graph.compile()                                   # → no change needed
```

### Step 6: Identify tools and the ToolNode

Find ALL tools and how they're registered:

```python
# LangGraph ToolNode
graph.add_node("tools", ToolNode([tool1, tool2, tool3]))

# Tools bound to LLM
llm_with_tools = llm.bind_tools([tool1, tool2, tool3])

# Tool decorators
@tool
def get_user_profile(user_id: str): ...
```

Find the exact node name used for tools (could be "tools", "tool_executor", "action", etc.).

A Claude Agent SDK agent (`ClaudeSDKClient` / `ClaudeAgentOptions`) has no such list: its tools are CLI built-ins, and `allowed_tools` is permission posture, not the surface. Read the surface as the "Claude Agent SDK" section says (`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/claude-agent-sdk-surface.md`).

**Mock at the lowest call whose arguments are plain typed values, not an encoded wire payload:** the agent's own code
and the library logic above that call keep running. `client` (the SDK, driver or wrapper method whose call path names
the operation) is the default, and an installed tool class's `_run` is that call when every call below it carries an
encoded payload; `transport` (the HTTP / DB wire) when the agent's own code makes the wire call with no client package,
or when `payload_codecs` declares every encoded field; else `entity` (the store's rows behind an adapter), and
`tool_body` only when none is reachable. Read each tool's code for the calls that leave the process (network,
subprocess, database). `payload_codecs: [{at: args.<path> | returns.<path>, codec: [<codec>, …]}]` (beside `seam:`;
`base64 | base64url | json | mime | form_urlencoded`, decoded in list order, `mime` last, `[*]` for each list item)
names a field the wire carries encoded; `descriptor.args` and `returns` then describe the DECODED shape: `base64` /
`base64url` → the bytes as text, `json` → the parsed value, `form_urlencoded` → a flat `{key: value}` mapping, `mime` →
`{headers: {Name: value}, body: <text>, html?: <html>}` (a reply carries no `attachments`).
Declare it on every `mode: mock` entry, beside `descriptor:` — `seam: transport | client | entity | tool_body`;
`tool_body` adds `seam_reason: inline_external_call | hosted_body | unpatchable_write` and `skipped_code: [<path:line>]`,
the agent code the mock skips (each becomes a manifest `coverage_holes[]` row). For each row source
`derive_tool_facts.py --check --json` lists on a `tool_body` read tool (`row_sources`), open the called function in the
agent's installed library and read what it returns by default; if it hides rows (a status, archived, deleted, an owner, a
page limit), write a business rule naming the stored values with `(source: <that line>)`, and when that makes a documented
argument value unreachable (a filter for a hidden state), record a `potential` agent finding (Step 9); otherwise add
`{cite: <that line>, returns: every_row, evidence: <library file>:<line inside that function>}` to `skipped_code`. A range
answers no row source (`row_source_uncited`). A write-capable tool moves below the
tool only when every external call it can reach is patched. A seam mocks BOTH directions of its store: reads of a
store a `client` / `entity` mock writes go to the mock too. The mock returns the structured result the agent's
guards read (status, artifacts, state updates, message metadata), not only the model-visible text. Declare every
middleware or guard that reads a tool's result or a store under root `guards:` —
`{name, cite, watches: [<tool>], reads: store, via: <dotted reader>}` or `{…, reads: result, fields: [<path>]}`.
`verify_config.py` refuses an undeclared seam (`tool_seam_invalid`), a `transport` mock with no `payload_codecs` under an installed tool class (`seam_below_installed_tool`), codecs the harness cannot apply (`payload_codec_invalid`) and a guard blind to the mock (`guard_sees_mock`).
A response mocked at an external call that no trace and no on-disk declaration
describes is `type_source: inferred`. Mock or keep live per external system, not per tool: a tool left
real that reaches a system you mock goes through the same seam.
**A seam mock that a real tool calls through names that tool.** Traces carry the real tool's name, never the
seam's, so on the seam entry declare `serves: [<real tool>, …]` and `serves_cite: <path:line>`, the line of the
served tool's code that makes the call (a `StockQuote` transport mock for the HTTP call `alphavantage_global_quote`
makes: `serves: [alphavantage_global_quote]`, `serves_cite: agent/quotes.py:12`). The backend records the link and opens
the served tool's error-channel slot, so that tool's trace facts (failure texts, channel, and traced values of its own
declared args) reach the mock; the seam's own args and channel stay under the seam. A link without a cite is unused.
A mock named for the tool it replaces needs none.
`verify_config.py` refuses a `serves:` on an entry that is not a `transport` / `client` / `entity` mock, a name
that is no other tool of the config or is mocked itself, and a missing or malformed `serves_cite`
(`tool_seam_invalid`).
**The harness patches exactly the `original_function` the config names.**

For each tool, determine THREE things:
- Name and arguments
- What it does internally (read? write? both? what data?)
- Which mock mode it needs:

**Mode 1 — Generic mock ("mock"):**
Simple read-only tools where a flat entity from WorldState is sufficient.
- `get_config()` → returns static config
- `get_user_profile(user_id)` → returns user entity from WorldState
- `search_docs(query)` → returns stub search results

**Mode 2 — Custom mock function (you write it):**
Tools that MODIFY state, or that are part of a chain where tool B reads what tool A wrote. If the agent creates something and later reads it back, both tools need custom mocks operating on the same WorldState.
- `create_item(data)` → writes to WorldState, returns created object
- `list_items()` → reads from WorldState, returns list of what was created
- `update_status(id, status)` → modifies entity in WorldState
- `delete_record(id)` → removes entity from WorldState
- Any tool where the agent checks the result and makes decisions based on it

**Mode 3 — Observe ("observe"):**
Real execution AND WorldState capture. The tool runs normally against real infrastructure, but its return value is captured into WorldState via a registered observer. Downstream mocked tools can then read the captured data.

Use when: a real tool produces data that other mocked tools need. For example, `rag_search` runs for real against a test index, returns documents. `summarize_document` is mocked but needs to know what documents were found. Observer captures the search results into WorldState so the mock can read them.

Also use when: a real tool WRITES data (creates a ticket, sends a payment) and other mocked tools need to see that write. The observer captures the write result into WorldState.

```python
@plurai.observe("rag_search")
def observe_rag_results(args, result):
    ctx = plurai.get_simulation_context()
    for i, doc in enumerate(result):
        ctx.world_state.entities[f"search_result_{i}"] = {
            "_entity_type": "search_result",
            "query": args.get("query", ""),
            "title": doc.get("title", ""),
            "content": doc.get("content", ""),
        }
```

**Then, once across the tool set: the TOOL-CONTENT ENVELOPE.** Read what the agent's own
tools hand back to the model — the return value as the model SEES it, after any wrapper the
agent puts between its tools and its loop. Many agents have a house shape (`{"status",
"content"}`, `{"ok", "data"}`, a bare string); a mocked tool that returns raw JSON into such
an agent hands the model a payload shape its real tools never produce, and the agent is then
tested against a contract it does not have.

Record it in `plurai.config.yaml` under `agent.tool_content_envelope`, the same way the
claude-skill path records `return_envelope` (see
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/code_surface_sdk_shape.yaml`):

```yaml
agent:
  tool_content_envelope:
    kind: json_field           # json | json_field | text
    template: {status: success, content: null}   # json_field only: the result goes at the null
    error_template: {status: error, content: null}   # json_field only: the FAILURE shape
```

- **`json`** — the default and the fallback: the result is rendered as JSON, nothing wrapped
  around it. Omit the key entirely for this; an agent with no convention of its own gets
  today's behaviour unchanged.
- **`json_field`** — the agent's tools return a wrapper object with the payload in one field.
  `template` is that object with `null` marking where the result goes.
- **`text`** — the agent's tools return a plain string.
- **`error_template`** (`json_field` only) — the shape the agent's real tools return on FAILURE,
  again with `null` marking where the message goes. `_render_error_content` builds it, so a
  mocked tool's error reaches the model in the agent's own shape rather than as a bare
  diagnostic string. Read it off the same real tools; when they signal failure some other way
  (an exception, an HTTP status), omit the key and the failure renders as text.

Determine it from the agent's REAL tools, never from what looks tidy: this is a description
of an existing convention, not a design decision. If the agent's tools disagree with each
other, there is no envelope — omit the key.

**Reading scenario state inside a `@plurai.mock` handler** uses one of three
canonical APIs — never `isinstance(ctx.world_state.entities, dict)` (always
False; the verifier rejects it):

```python
user = ctx.world_state.get_entity(user_id)                       # single
orders = ctx.world_state.query("order", user_id=user_id)          # filtered
state = plurai.world_state_snapshot()                              # full dict
```

See the `simulation-setup-python` skill
for the full pattern reference and the verifier code
`entities_proxy_isinstance_dict_misuse`.

**Mode 4 — Real ("real"):**
Real tool body runs unchanged: its validation, branching and error messages are the agent's own and always run. The store under it is served by exactly one of the three routes below — never by a storage class the harness writes.

Use `mode: real` when:
- The tool's logic is what you want to test, and the store it reaches is served by route (a), (b) or (c) below.
- You want to exercise a RAG / vector DB / external read service for real — set `read_strategy: real` and leave the backend untouched.

Do NOT use `mode: real` when:
- The tool has destructive production side effects (sends real SMS, charges real money) that no seam below it can catch. Use `mode: mock` for these, OR `mode: real` with `write_strategy: stub` if you want the tool body to run but the write to be a no-op.

**ONE ROUTE PER STORE — every store a tool reaches is served by exactly one of:**
- **(a) the real thing** — a granted real read, or a real engine the harness runs per conversation (a database, SQLite, a folder, a cache), declared as a confirmed store when the harness seeds and resets it (`world_state.owner: agent`, its types in `owned_types`, a `world_state.confirmation` entry, a harness `reset()` — the declaration the consistency rule below defines). A write into that store is not a live write: it runs for real with no grant.
- **(b) the agent's own in-tree implementation**, when it is the run path's ONLY implementation of that store, used unmodified. An implementation the run path never selects — nothing the bench configures (a setting, an environment variable, a flag) picks it — is not a second one. When the tree can also reach a remote system for the same store (selected by a URL or a setting), the in-tree one is a test double: mock the system at the seam (c) and cite the stand-in as env-gen's behavioural source (record the case with the `egress` discovery and a `path:line` cite of the construction).
- **(c) a `mode: mock` at the lowest call whose arguments are plain typed values**, with `serves:` naming the real tools that call through it (`serves_cite` each), and those real tools declaring no `reads_from` / `writes_to` (`observation_lane: tool_trail`). `seam: tool_body` only where no seam is reachable.

**The harness never implements store behaviour.** Code in `plurai_sim/` (or a bridge `replace`) between the agent and a store may only *translate* (names, types, error classes), *delegate* (to a declared mock, to the original or in-tree implementation, or — for a declared `read_through` — to `plurai.world_state.get` / `query` by equality), or raise `HarnessFault`. It never decides an outcome from row values: no validation, status transitions, naming or numbering, pricing or totals, derived reports, non-equality filtering, ordering or paging. It may seed, reset and snapshot an (a) or (b) store. A behaviour (a) or (b) lacks is a cited coverage hole, never harness code; a behaviour a seam mock must have (versioning, HEAD, numbering) is its contract, pinned by its contract tests. `verify_config.py` FAILs a `mode: real` tool on an effective `read_strategy: mocked` or an explicit `write_strategy: mocked` that no mock serves (`world_served_without_mock`, advisory: re-route it, or overrule it with the reason on a config written before this rule); `verify_simulate.py` WARNs on a harness object installed over agent code that reads or writes `plurai.world_state` itself (`harness_store_serves_world`).

**Document-reading tools (vision / OCR / file ingest) — exercise them for REAL; do NOT scope them out.** When a tool's INPUT is a document the user would supply — it ingests a file / PDF / image / scan, runs OCR or **vision**, or takes a `raw_text` / `file_bytes` / `content_type` argument (an invoice / receipt / boarding-pass / ticket / form reader) — that input CAN be supplied in simulation: scenario generation **authors a realistic document per scenario** and delivers it to the tool. Text is folded inline; an **image / PDF is rendered and delivered as a base64 data URI the harness decodes to `(bytes, content_type)`** for the real vision call (binary delivery is shipped — Phase 1b). So:
- Set the document-reading tool **`mode: real`** — you want its real extraction / vision logic exercised, NOT a canned mock that hands back pre-filled fields (that silently skips the agent's core capability).
- Plan a **`document_fields`** entry for the field it reads — `format: "txt"` for a text extractor, `format: "image"` / `"pdf"` for a vision/OCR tool, `format: "docx"` / `"xlsx"` / `"pptx"` for an office-document reader (a memo/report/spreadsheet/deck the agent parses) — configured at the post-setup document-generation ask (the marker `plurai_sim/.document_fields`). Any OTHER format the agent's own extraction expects is also supported: the backend authors it directly, validated by a generic read-back check. An agent that accepts more than one format for the same field can declare `formats: [...]` instead of a single `format`, and the scenario generator picks one per scenario (mix formats across a suite rather than fixing one for the whole agent).
- **Delivery shape — a ONE-SHOT extractor is a PIPELINE component; a MID-DIALOG one is a HANDOVER.** For a genuinely one-shot extractor the delivery is the `invoke_pipeline(inputs)` hook: the generated document arrives in `inputs[<field>]` and the generated harness decodes it straight into the real extraction call (`extract_ticket(file_bytes, content_type)`). A document/vision extraction is naturally pipeline-shaped (document in → structured fields out), so bench it as its OWN pipeline component (`plurai_sim_<component>/` exporting `invoke_pipeline`) — even when the agent ALSO has a conversational surface for its other tools (that stays a separate dialog bench). **When the extraction only fires MID-CONVERSATION, that is the handover lane, not hand-wiring:** the document reaches the harness by BOTH arms of Step 6.5's `owner: user` rule — `seed_state` param 1 / the turn-1 payload by default, a `file` content part on a scheduled handover beat — so serve both (declare `agent.io.input: [text, file]` — required, Step 8) and let Step 9f's smoke measure whether `invoke_agent` serves such a turn; it fails naming any planted document the agent could not quote back.
- **Do NOT recommend "the vision/image path is out of scope (text scenarios only)"** — that guidance is obsolete. The only real limit is a tool needing a SPECIFIC real-world scanned asset the generator can't plausibly synthesize; there, ground the generation with a real sample via `/upload-doc-examples`.

**Per-direction storage strategies:** `descriptor.read_strategy` and `descriptor.write_strategy` each take one of:

| Strategy | What env-generator does | When to use |
|---|---|---|
| `mocked` (default if `reads_from`/`writes_to` is non-null) | On a `mode: mock` tool: the mock's own world. Inert under `mode: real`: a real tool reaching a faked store is served via a seam mock's `serves:` (route (c)), never through this label. | A mocked tool's store. Never declared on a real tool; a legacy real tool carrying it keeps running and is flagged `world_served_without_mock`. |
| `real` | Generates **nothing**. The customer's tool calls the real backend. | RAG / vector DBs / public reference data, and any store route (a) or (b) serves. A `mode: real` writer whose store the harness owns (`world_state.owner: agent` / `external`) leaves `write_strategy` UNSET and types the write `world` on the map: its own code writes the store and `world_snapshot()` observes the result (a write into a confirmed store is never live; outside it the write is live and runs only on a consent grant). |
| `stub` | `sys.modules[...] = MagicMock()` from `simulate.py`'s Phase 1; no adapter. | Write-only fire-and-forget: logging, analytics, notifications. The call needs to not crash; the result doesn't matter. |

Set `read_strategy: real` on a real tool whose store routes (a) or (b) serve. A real tool over a faked store declares no strategy and no `reads_from` / `writes_to`; the seam mock that serves it names it in `serves:`. The essential-storage checks (`essential_storage_*`) fire only on a legacy real reader still labelled `mocked`. RAG agents with all-real reads need no stubs at all.

**`descriptor.role: identity` — the identity surface.** Set it on the tool a session is established through (sign-in, token exchange, tenant resolve, consent gate) and on nothing else. Identity is MOCKED like every other dependency: the session a scenario starts from is seeded state and the token the agent holds is derived from a seeded user entity, so a run never needs a real credential. A marked descriptor therefore needs a mock behind it — `mode: mock`, or `mode: real` named in the `serves:` of the seam mock its sign-in call goes through — and `verify_config.py` REFUSES one left on the live provider by name: `mode: observe` (the real call runs, the capture happens after the credential was required), or `mode: real` with `read_strategy: real` / `stub`. The strategies are read only under `mode: real`, so `mode: mock` with either of them is ACCEPTED — the handler answers the call and no strategy applies. The repair is one word here and a dead generated set later: without a mock the sign-in fails for want of a credential nobody can supply, every scenario dies on turn 1, and the run reports an agent that refuses to work. The marker is a DECLARATION, never a guess from a name — an undeclared tool called `login` is an ordinary tool, and a declared `resolve_depot_badge` is the identity surface. `identity` is the only accepted value, compared EXACTLY (the backend dispatches on that spelling): `role: Identity` is refused as an unknown role rather than silently ignored.

```yaml
tools:
  resolve_depot_badge:
    mode: real                 # the TOOL ENTRY — its own sign-in logic runs…
    observation_lane: tool_trail
    descriptor:
      role: identity           # HERE — on the DESCRIPTOR
  badge_service_exchange:
    mode: mock                 # …and the call it makes is answered by this seam mock
    seam: client
    serves: [resolve_depot_badge]
    serves_cite: depot/auth.py:41
stubs:
  modules:
    - module: our_backend.db
      role: storage            # a DIFFERENT key on a different object — the stub-entry role
                               # (Step 8: "Declare a `role:` on every `stubs.modules` entry"),
                               # matched case-insensitively there, exact here.
```

**Coherence guarantee:** every read and every write THROUGH PLURAI — whether through a `@plurai.mock` handler, an `@plurai.observe` capture, or a seam mock a `mode: real` tool calls through (`serves:`) — lands on the same plurai world-state. Tools with `read_strategy: real` are intentionally outside this guarantee: their data comes from the real backend, by design — except a reader that declares `read_through` (below), the declared bridge that overlays the world on its real store.

**CRITICAL CONSISTENCY RULE — a write and every read of it sit on the same side of the mock.** Declare each
writer's readers on `tool_graph` — `{source: <write tool>, target: <tool that reads what it writes>, shared_entity,
relationship}`, the agent's real tools included (an edge to another writer — an editor, a lister — declares no reader), or `{source: <write tool>, target: null, relationship: <why nothing
reads it back>}` — and keep each pair on one side where you decide both: mocked with mocked; real with real; an
`observe` write with either. **A store is real or simulated as a whole**: its writes and its reads are mocked, and each
scenario seeds it, unless `world_state.confirmation` names it — a list of `{answer: harness_created | disposable_copy,
target}` entries, one per store, recorded by the scan (`harness_created` for a store the harness creates per conversation:
`in-process: <object>`, `mkdtemp: <prefix>`, `run-dir: <path>`; `disposable_copy` only on the user's Yes; `agent_bundled`,
`bundled: <path>`, for files the agent ships and only reads, never data it reads as records or documents: their reads
run real, never a write). A writer whose
`writes_to` names a confirmed `target` and whose types `owned_types` holds is NOT a live write, so it runs for real with no
grant, only its outside calls mocked. `verify_config.py` FAILs, hard, a split with a real reader (`writer_reader_mode_split`;
a `read_through` is no cure for it), a malformed confirmation (`world_confirmation_invalid`), and a `mode: real` tool that
reads a store no confirmed `target` is named in (`real_read_store_unconfirmed`), corpus tools aside; a `client` / `entity`
seam whose store no tool reads is advisory (`writer_seam_store_unread`). It WARNS on a
mocked writer with no edge to a reader (`write_tool_reader_undeclared`:
declare it in one step if the reader is obvious, else proceed); env-gen's read-your-writes tests run on these pairs.
When a split puts a real reader on a store a mocked write lands in, fix it in this order:
1. **Mock the store as a whole** (the default): every tool that reads it `mode: mock`, or real and named in the `serves:` of the seam mock for that store. **Mock lower** only for a confirmed store: declare it — `world_state.owner: agent`, the types it holds in `world_state.owned_types`, its `world_state.confirmation` entry, and a harness `reset()` that clears it before every conversation (the runner calls it, and holds an `agent` world without one) — set the writer `mode: real` with `write_strategy` unset (`read_strategy: real` where it also reads the store), name the entry's `target` in its `writes_to` / `reads_from`, type it `writes: world`, `effects: ["none"]` on the map, mock only its outside calls, and observe that store from `world_snapshot()` if scoring needs it (`scripts/writer_readers.py` `local_store_writers`). A write to the customer's own files in place or to any system the run does not reset stays a live write, mocked by default.
2. **`read_through` resolves no split**: a reader that declares it still reads its real store. It serves only a reader of a confirmed store that must also see the mock world's rows: declare it on the reader and rebind each `via` accessor in the harness. Writers and world type come from the `tool_graph` edges into it; `read_strategy` stays `real`:
   ```yaml
   search_tickets:
     mode: real
     read_through: {via: [helpdesk.store.TicketStore.get, helpdesk.store.TicketStore.query, helpdesk.store.TicketStore.search]}   # beside descriptor:, never inside
     descriptor: {reads_from: "helpdesk ticket store", read_strategy: real, ...}
   tool_graph:
     - {source: create_ticket, target: search_tickets, shared_entity: ticket, relationship: "a new ticket is listed by the search"}
   ```
   `via` lists EVERY accessor the reader's tools reach, one per read shape, and each is overlaid for its shape:
   - **point** (by key): serve the world's entity of the linked type when the world holds that key (a tombstone state reads as not found), else call the real one;
   - **list / query** (exact-field filters): the real rows, minus the keys the world holds, plus `plurai.world_state.query(<type>, **filters)` rendered as the store's row type;
   - **search** (a pattern, full text, a ranking — any filter that is not an exact field): the world side never matches; it hands the world's rows of the type to the real accessor's own code — laid into this conversation's copy of the store the real accessor reads, or asked of a mocked reader that serves the type — and the real code matches;
   - **aggregate** (free slots, counts): overlay the accessor UNDER the computation, never the tool's return.

   The overlay's world side only looks up by key or by exact field match (or calls a mock reader); it never computes filters, rankings or aggregates itself — the real accessor's own code does any computing. Outside a scenario (`plurai.active_session_id() is None`, tested directly, never by catching an exception) it calls the real accessor. Inside one it never skips silently: a world row it owns but cannot read, place or render raises `plurai.HarnessFault` naming the accessor and the row — no `except Exception: return`/`continue`, no fallback to the real store — so the turn is the harness's, and the real-reader probe names the cause. Any containment or equality test on a key the store canonicalises — a path above all — compares canonical forms on BOTH sides (`os.path.realpath` / `Path.resolve()` on the harness's own value too): the store may resolve a symlink the harness's value keeps (macOS `/var` → `/private/var`), and a mixed comparison fails on every row; a world file that still cannot be placed raises `plurai.HarnessFault`, never `continue`. A reader sees the mock's writes only when that probe witnesses it: never say so to the customer on the declaration alone. `verify_config.py` FAILs a `read_through` on a non-real tool (`read_through_invalid`), with no mock-world writer edge into it (`read_through_unlinked`), or whose accessor the harness never rebinds (`read_through_unbound`), and WARNs on an overlay that catches every exception and raises nothing (`read_through_swallowed`); env-gen then pairs it for the real-reader probe.
3. **When the effect comes from a consumer the run never executes** (a queue worker, a webhook, a cron), the mock's `post_conditions` write the outcome entities and the reader reads through them; otherwise name the reader as a risk. `target: null` is not honest when its own relationship text names a reader.

**How to decide:** Read how the agent USES each tool's return value. If the agent just acknowledges ("done, status ok") → generic mock is fine. If the agent parses the result, iterates on it, or passes it to another tool → needs mock or observe. Then check the tool_graph for shared entities and apply the consistency rule.

### Bridge Adaptations (Steps 2-6)

If `LANG = "node"`, Steps 2–6 are the seam discovery of the `simulation-setup-bridge` skill (Step B2): a stub becomes a loader swap declared in `bridge/bridge.map.json`, and the checkpointer becomes a declared `replace`. Record the same findings; the bridge skill says where each lands.

### Step 6.5: State-surfaces audit

**THE WIRE, STATED ONCE** — every rule below and in the language sub-skills defers to this; three versions of it disagreed across three repos, and a harness cannot be right against two. `seed_state(initial_agent_context, initial_world_state, resume_seed)` takes three parameters and they are not interchangeable:

- **Param 2, `initial_world_state`, is the STORE** — the entity rows the backend seeded for this scenario (`plurai.start_session(thread_id, initial_world_state, …)` serves the same rows to mock tools). READ it; apply it VERBATIM when non-empty; RAISE if you cannot serve it. Never synthesise entities instead — the next tool to read world_state then sees zero records. Param 2 is empty only when the bench declares no world type — tool mode never makes it empty. Route-M rows (types outside `world_state.owned_types`) are also already in the mock world (`start_session`). Route-H rows (the harness-owned types: `owned_types`, or every type under `owner: agent`) are this harness's to apply: lay each row over the base store (a row for an existing id replaces those fields; a new id adds the record), and raise only for a row it cannot apply. Each row also carries keys that are not fields of your store: `_entity_type`, and an `id` copy of the row's id whenever its type keys by another `id_field` (the backend adds it for by-id readers). Both are reserved: route on the row, but build each store record with `plurai.store_record(row)` (the row minus both keys, judged against the declared `world_state.entity_types`), never from the row's raw keys — a store that validates its records (a config schema that forbids extra keys) drops one with an unknown key, and the agent meets a world without it. `world_snapshot()` returns each seeded row under the id it was seeded with, carrying its fields and the row's `id` copy, because that read is how the run checks the rows landed (a row that did not is an `undelivered_seed` breach that voids the scenario). (Python: the Step 9e verifier of the `simulation-setup-python` skill catches this as `seed_state_ignores_world_state`; other languages need the equivalent gate.)
- **Param 1, `initial_agent_context`, carries the `request` values** — the `agent_context_fields` declaring `inject: request`. They ALSO arrive spread into the `invoke_agent` payload on TURN 1, so the agent already sees them through whatever in-process mechanism your framework uses (LangGraph: `state["<field_name>"]`; CrewAI: task inputs; Vercel AI: message history; OpenAI Agents SDK: `Agent.run()` args; custom: however your agent reads the payload). Seed from param 1 ONLY where your agent reads that value from a storage surface instead of the payload — `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/seed-state-contract.md`. Field names are agent-specific: read them from the scenario's `agent_context.yaml` or from `agent_context_fields`.
- **Param 3, `resume_seed`, is the prior history** — unchanged; contract in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/resumed-seed-state.md`.

**On a one-shot pipeline bench (`shape: pipeline` / `invoke_pipeline`) the same three parameters hold, with no turn 1 to point at:** `seed_state` is still called ONCE at init, before the single call, with param 1 = the very dict that becomes the hop's `inputs` (so the `request` values arrive as that call's kwargs rather than in a turn-1 payload), param 2 = `{}` unless the bench declares a world (then its rows, applied as above), and param 3 = `None`, because a hop has no prior conversation to replay. Define `seed_state` on such a bench only where the hop reads a value from a storage surface instead of its inputs; when you do define it, the arity and the read rules above apply unchanged.

A document the simulated USER hands over (a `.document_fields` entry, or a `generated_document` field marked `owner: user`) has ONE wire shape and ONE route: a **`file` content part** `{'filename': '<field>.<ext>', 'file_data': 'data:<mime>;base64,…'}` in the user message of the turn she hands it over, beside the `text` part carrying what she says — the OPENER when she hands it over with her first line, a later turn when the scenario schedules it at a later beat of its plan. It is never in the turn-1 payload, never an `agent_context` key, and never in `seed_state`'s param 1 (only a scenario with no opener at all — a pipeline set — keeps its document as the hop's input). The filename carries the document's own extension and the URI its media type, for every format (text, csv, json, pdf, image, office); a served document too large to inline is folded to that `data:` URI by the SDK first. Which turn is per-scenario and authored backend-side, so a bench that can be handed a document MUST declare `agent.io: {input: [text, file]}` (Step 8; otherwise the SDK fails the scenario as `harness_undeclared_input`) and read the part on EVERY turn, the opener included: Step 9e refuses a harness that reads a `.document_fields` field off the payload or `seed_state` (`document_read_off_payload`), and Step 9f's smoke sends the part on the opener and on a later turn to measure whether `invoke_agent` serves both. Never on the store — decode it with `turn_documents` and hand it to the agent the way production hands it a user's file (Step 8's two handovers: its upload channel, or a planted file named by path).

**Essential vs non-essential storage** is the critical distinction here. Treat a state surface as **essential flow data** when both hold:

1. At least one tool's `descriptor.reads_from` references the surface, AND
2. The agent's behavior depends on previously-written data (cross-tool read-after-write, multi-turn coherence).

Essential surfaces are served by ONE of Mode 4's three routes: a seam mock that names the tools reading them in `serves:` (the mock world, `plurai.world_state`, seeded by the Plurai backend through `start_session`), or a declared harness-owned store (`world_state.owner: agent` / `owned_types`: a real engine the harness runs, or the agent's own in-tree implementation) seeded by `seed_state` from param 2. That gives you one coherent world: a `mode: mock` tool's write and a served `mode: real` tool's read agree, regardless of whether the world-state service is the in-process `LocalMockService` (Phase 1) or a deployed backend (Phase 2 with `the backend-env-generator`). Where the agent's storage interface must be swapped to reach the seam (a factory, a backend class), the harness installs a pass-through that only translates and delegates to the seam mock (Python: `_storage.py`; see Step 8.4 of the `simulation-setup-python` skill) — it never stores rows or computes outcomes itself.

Non-essential surfaces (analytics emitters, write-only loggers, feature-flag clients accessed at import-time, observability SDKs, telemetry, anything imported only to prevent crashes) stay as `sys.modules["..."] = MagicMock()` in Phase 1 of `simulate.py`. Don't world-state-back what no tool reads — it's wasted code that drifts from prod schema with no behavioral payoff.

**For each tool, and each non-tool node that runs before tools** (`fetch_context`, auth init, etc.), enumerate every external read. For each surface, decide:
- Is it the mock-env world state (already seeded by `start_session`)?
- Is it the agent's primary in-process state (already in the per-turn `invoke_agent` payload)?
- Is it an **essential** storage surface (a tool reads from it)? → pick its route (Mode 4): a seam mock at the lowest call whose arguments are plain typed values, with `serves:` naming the real tools that read it (the harness swaps the agent's storage for a pass-through to that mock only where the seam needs it; the backend seeds the mock world), or a harness-owned store (a real engine, or the agent's own in-tree implementation) that `seed_state` seeds before turn 1 (Python: see Step 8.5 of the `simulation-setup-python` skill for the contract + worked example. Bridge: the storage module is a `modules.<path>.proxy` in `bridge/bridge.map.json`, its calls mapped to the mock tools; a `replace` holds only persistence no tool reads, never a store a tool reads).
- Is it **non-essential** (no tool reads)? → leave it as a Phase 1 `MagicMock()` stub. Don't waste cycles faking state nothing depends on.

Param 2 is filled by the backend's per-scenario extraction session: every per-scenario entity and the history between them, under `initial_world_state['entities']`. Use param 1 for session-scoped fields that are not entity attributes (an active account id, a configured policy text, a selected template).

Without seeding an essential surface, the first tool call hits empty/default data, strict validation raises, and the agent's retry loop runs to its iteration budget (e.g. LangGraph's `GraphRecursionError` after 25 turns; other frameworks have analogous caps or none — a custom agent might just hang).


## Generation

### Step 7: Confirm decisions with the user

STOP before generating. Present your analysis and ASK the user to confirm or adjust.

**HEADLESS RUN DRIVER (CI, a nightly suite, a remote agent — nobody at the terminal):** the
run driver is DECLARED, `workspace.run_driver: interactive | headless` in
`plurai.config.yaml` (Step 8). `PLURAI_SIM_NONINTERACTIVE=1` is an accepted alias for
`headless`; an explicit `workspace.run_driver: interactive` **wins over it**, so a CI image that
exports the variable globally cannot un-interactive a human's run. Absent ⇒ `interactive`.

Resolve it once, here:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_run_driver.py" mode   # -> "<mode> <source>"
```

Under `headless`, do NOT stop and do NOT ask the user: proceed with the plan you
analyzed — keep the per-tool modes you inferred (default any genuinely-ambiguous tool to
`mock`), print the plan to stdout for the run log, and continue straight to Step 8.
Confirmation is for interactive runs only. Every other gate in every command follows the
same rule mechanically (`hooks/ask_guard.py` answers it, or files a typed
`run_driver_input_required` finding) — this step states it because it runs before any window is
armed.

Show:
```
I've analyzed your agent. Here's my plan:

STUBS (Phase 1 — prevent import crashes):
  ✓ our_backend.db — SQLAlchemy, connects at import time
  ✓ our_backend.redis — Redis client, connects at import time
  ✓ our_backend.services.StripeClient — API client, needs STRIPE_KEY
  
CONTEXT NODES (Phase 3 — replace with scenario readers):
  ✓ fetch_user_context — reads user profile from DB → will read from scenario
  ✓ load_session — reads session from Redis → will read from scenario

TOOLS — mode for each:
  get_user_profile:  MOCK — reads from Postgres, agent accesses name/plan/balance
  process_refund:    MOCK — calls Stripe + writes Postgres
  get_transactions:  MOCK — reads from Postgres
  rag_search:        ??? — calls your RAG index. Should this be:
                       • mock (return fake results from WorldState)?
                       • real (hit the real index)?
                       • observe (hit real index + capture results for downstream mocks)?
  send_notification: MOCK — calls Twilio, agent just checks status

WORLDSTATE ENTITIES (initial scenario data):
  I'll create entities for: user, transactions

WHAT THE USER SEES (world_state.user_surfaces):
  transaction_history — screen "Transactions": transaction rows, newest first
  sms_inbox           — text_message: user_delivery rows sent by send_notification
  
TOOL CONNECTIONS:
  get_user_profile → process_refund (refund reads user balance)
  process_refund → get_transactions (transactions shows the refund)
  rag_search → ??? (does any mocked tool use search results?)

STATE SURFACES AUDIT (Step 6.5 — list the agent-specific surfaces you found,
                     or say "none" if the agent has no state beyond the per-turn invoke_agent payload):
  Storage:    <name + entry-point methods if applicable; else "none">
  Cache:      <name + read methods if applicable; else "none">
  Repository: <name + read methods if applicable; else "none">
  Other:      <env-driven HTTP / file reads / any other state surface a tool reads>
  →  PLAN:    <one of:
                "Essential — route (c): a seam mock with serves: [<tools>] (Python:
                  _storage.py pass-through to it, if the agent's storage must be swapped
                  to reach the seam) — or route (a)/(b): a harness-owned store seed_state seeds",
                "Non-essential — Phase 1 MagicMock stub only (no tool reads from it)",
                "None — no extra surfaces">

(Hypothetical example for a stateful agent — substitute YOUR agent's identifiers:
  Storage:    RecordStorage — get_record, create_record (strict validation: parent record must exist)
  →  PLAN:    Essential, route (c). The records service the storage calls is mocked at its
              `client` seam (`records_api_get`, `records_api_create`, … with `serves:
              [update_record, compare_records, restore_record]`). `RecordStorage` is swapped
              for a pass-through whose methods call those seam mocks and convert their
              dicts to the production Pydantic return types — nothing else. The Plurai backend seeds
              the mock world through `start_session`; the record history (HEAD, revision
              numbering) is the seam mock's contract, pinned by its contract tests.
)

RUN SETTINGS (Step 2 — only where the harness departs from the driven entry's default):
  max_steps: entry default 50 → harness passes 150 (server clamp, server/config.py:40)

ENTRY WRAPPER (Step 8 — only when simulate.py drives the agent through a bench runner):
  ../run_bench.py — the bench's own runner (its docstring says so); imports the agent package

CONSISTENCY CHECK:
  ✓ get_user_profile (mock) → process_refund (mock) — both WorldState ✓
  ✓ process_refund (mock) → get_transactions (mock) — both WorldState ✓
  ⚠ rag_search (???) → if any mocked tool needs its results:
      • observe = run real + capture to WorldState (recommended)
      • mock = fake results from WorldState (no real index needed)
      • real = ⚠ downstream mocks can't see results (BROKEN if connected)

Questions:
  1. For rag_search — mock, real, or observe? Does any other tool use its results?
  2. Does send_notification need to see any data from other tools?
  3. Are there business rules I should know about? (refund limits, plan restrictions, etc.)
  4. Any tools I missed?
  5. For observe-mode tools — what data should be captured from the result?
  6. STATE SURFACES — does my audit above match? Any storage / cache / repository
     I missed? (Look for tools that call get_*, fetch_*, or similar against an
     external source before reading from the per-turn invoke_agent payload —
     those are the surfaces that need seeding before turn 1.)
```

**Anonymization posture — do NOT ask here.** Setup uploads nothing, so a posture asked at
this point is a consent question about data that may never leave the machine — it fired on
repos whose only "personal data" was a PII-detection *sample* shipped in the source tree.
Leave the `anonymize:` key OUT of `plurai.config.yaml` entirely (Step 8 writes it only if it
is already present). Each ingest skill (`/build-rag-corpus`, `/upload-doc-examples`,
`/upload-scenario-bundle`, `/analyze-traces`) asks it once, on its first run, when real data
is actually about to be uploaded — and writes the answer, so it is asked once per env and
never re-asked. Ingestion can either scrub PII locally before upload (**anonymized**) or
upload as-is (**raw**, the default). Once written it is read-never-overridden; changing it on
an env that has already uploaded artifacts is refused fail-closed (mixed-posture) — start a
fresh env instead. Step 8c's `artifacts/data_profile.md` is a stashed generation input like the
PRD — it carries real values (enum members, sample values, distributions), so it leaves the
machine under the SAME env posture as the PRD, and writing it here still uploads nothing.

Wait for the user to answer. Adjust your plan based on their responses.

IMPORTANT: Before proceeding, apply the consistency rule (Step 6): every write tool's readers are on `tool_graph`, each on the writer's side of the mock.

Then proceed to generation.

#### Step 7 (claude-skill only): infer the `action_channel`

**Only when `LANG = "claude-skill"`** (Step 0), determine the **`action_channel`** (`tools` | `code`, and for `code` the SDK-shadow vs CLI-shadow sub-mode) — *how* the skill acts during the bench, orthogonal to each operation's mock mode. **Read `references/claude-skill-action-channel.md`** for the full inference procedure, the `tools`-vs-`code` comparison table, the FAIL-CLOSED-on-ambiguity rule, and how to record it. Non-claude-skill agents skip this step.

The decision is mechanical, read off the S3 tool surface (do NOT ask the operator, never default to `tools`): operations carry a vendor-SDK `original_module`/`original_function` surface → `code` SDK sub-mode; else a vendored-CLI `original_command`/`http_mapping` surface → `code` CLI sub-mode; both → FAIL CLOSED; neither → `tools`. Record as `agent.action_channel` in `plurai.config.yaml` (Step 8).

**When to ask vs. when to proceed:**

ASK when:
- A tool could reasonably be mock, real, or observe — you can't tell from the code alone
- You're unsure which tools feed data to which other tools
- You found business rules in comments/docstrings but aren't sure they're current
- The agent has complex control flow and you're not sure which paths to mock
- You see tools that interact with each other but aren't sure of the relationship
- **Consistency violation detected**: connected tools are in different worlds (one mock, one real). Always flag this — the developer must choose.

PROCEED when:
- A tool clearly hits a DB/API and should be mocked (no ambiguity)
- A tool is a pure computation with no external deps (keep real)
- The dependency chain is obvious from the code



### Step 8: Generate plurai.config.yaml

This file is the complete simulation environment definition. The backend reads it to generate real mocks. Be thorough.

**The two-tier mock model — what this descriptor is actually for.**

Plurai uses two layers of mock implementations:

1. **Dumb inline mock** (the `@plurai.mock` body in `simulate.py`) — minimal stub
   whose only job is to satisfy the shape contract so the agent's React loop
   doesn't crash. NOT responsible for state coherence across turns. NOT
   responsible for enforcing business rules.

2. **Rich backend mock** — generated from `plurai.config.yaml` + `simulation_prd.md`
   by the Plurai backend. Implements actual business rules, maintains state
   coherence across turns, enforces invariants. This is what runs in production
   simulations against your agent.

`test_mock_contract.py` is the **acceptance criteria the rich backend mock must
satisfy**. The dumb local mock will fail behavior tests there — that's expected.

**The descriptor's `pre_conditions` / `post_conditions` / `error_cases` are the
machine-readable spec the backend reads.** Write them precisely — vague specs
produce vague backend mocks. Pick the right variant per intent:

**A render-action agent declares its surface typed, beside `tools: {}`.** Client-side verbs the
agent emits that are not tool calls (UI renders, navigation, a hand-off) go under top-level
`render_actions: [...]` — the list is the surface `lint_prd.py` checks the PRD against, and Step 9
closes it with the `RENDER_ACTIONS` fact (`references/agent_facts.md`); a surface declared
nowhere typed is one no completeness check can see. Content the product's UI shows during a turn
that is not in the returned messages — a streamed status line, a card, a message pushed over the
agent's event or state channel — is a render act too: return it on `agent_actions` as
`{'name': <verb>, 'args': {<what is shown>}, 'status': 'ok'}` and list the verb under
`render_actions:`; words the UI shows as the assistant's go in `messages`. A harness that drains
the agent's event stream keeps every event the front end renders. **Content the product's own code
produces, outside the agent's model call, carries its source.** An act it makes (a progress card
built from a state diff) adds `'source': 'product'` to that `agent_actions` entry. A tool call the product's
own code makes on a fixed path, never chosen by the model (a fetch the harness runs before the model
starts), is such an act: declare it on `agent_actions` with `'source': 'product'`, never as the model's
call on the tool trail (a trail row for it carries `'source': 'product'` too). Text it adds to the
assistant's reply after the model's output (a tool-failure warning, a footer) stays in `messages`
exactly as the user saw it, and is also declared beside `messages` as
`appended_output: [{'source': 'product', 'site': '<file:line>', 'text': <the added text, verbatim>}]`,
`site` being the product line that adds it. The judges label both as made by the product, not the
agent. Never declare words the agent's model wrote.

**Every config declares `render_actions:`, `[]` when the agent renders nothing.** An absent key leaves the callable surface open, so a call the config does not declare is never charged. With it declared the surface is closed: every call the trail carries must be declared, and a framework-internal call the runtime emits on the agent's behalf (a structured-output finalizer, a planner act) is a `tools` entry under its trail name like any other.

**Three action names are RESERVED, and they are how an agent says the dialog is over.** The same
per-turn action channel carries them, so a harness that already returns render verbs needs no new
field: an agent that ENDS the conversation itself returns an `end_conversation` action, one that
TRANSFERS it to another party returns `handoff`, and one that PAUSES for a human decision returns
`interrupt` (its `args` carry the `prompt`, any `choices`, and the `actions` it asks to run as
`[{name, args}]`, which is what the simulated user decides on, and `approve`: the one choice that
RUNS those actions — without it a denied call cannot be told from an executed one; a LangChain
`HumanInTheLoopMiddleware` payload is read natively; the answering turn carries the typed `payload['interrupt_choice']` beside the words — map
the choice, never the words; declare what the resume does with those words as `agent.io.choice_text`, Step 8). The server reads them off that turn and records `ended_early: agent_ended` /
`handed_off` (an older backend passes the action through as a plain render verb and
leaves `ended_early` unset — the reading is missing, nothing breaks), so the run report and the
judges can tell a dialog the agent CLOSED from one a budget cut — without it, a kiosk that hangs up
after the transaction is scored as an agent that stopped answering, and the closing turn is pushed
into a session that is already over. Emit them only when the agent really does end, transfer, or
pause; every other verb stays a plain render action, and these three are never a substitute for the
agent's reply text. Being reserved does not put them outside the declaration above: an agent that
emits one MAY list it in `render_actions:` like any other verb, and listing it is what licenses it
against the `RENDER_ACTIONS` fact.

**`pre_conditions` — what must be true BEFORE the tool runs.** Four variants:

| Variant | Shape | Use for |
|---|---|---|
| `FieldPresence` | `{field: <arg_name>, must_be_present: true}` | "this arg must be set (and truthy)" — never on a `nullable` arg |
| `FieldSatisfies` | `{field: <arg_name>, must_satisfy: "<bool expr>"}` | "this arg must satisfy a boolean expression" |
| `EntityExistence` | `{entity_type: X, must_exist_with_id: <arg_name>}` | "an entity of type X must exist at the arg-referenced id" |
| `EntityCollectionSatisfies` | `{entity_type: X, must_satisfy: "<bool expr>"}` | "the COLLECTION of entities of type X must satisfy a predicate (e.g. `len > 0`, `min_per_label >= 4`)" |

**`post_conditions` — what changes AFTER the tool runs.** Five variants:

| Variant | Shape | Use for |
|---|---|---|
| `EntityFieldChange` | `{entity_type: X, field: F, change: "<arith expr>"}` | "field F on entity X changes by an arithmetic delta (e.g. `-amount`, `+1`)" |
| `EntityFieldSetter` | `{entity_type: X, field: F, set_to: <value>}` | "field F on entity X is set to a literal / arg value" |
| `EntityCountChange` | `{entity_type: X, count_change: "<arith expr>", with_fields: {...}}` | "N new entities of type X are created (with optional seed fields)" |
| `StateFieldSetter` | `{state_field: F, set_to: <value>}` | "return-value field F is set to a literal / arg value" |
| `StateFieldChange` | `{state_field: F, change: "<prose description>"}` | "return-value field F changes non-deterministically — use when `set_to` can't capture the change (list-mutation, LLM-driven rewrites, conditional updates)" |

**Common mistakes to avoid** (the verifier rejects these):

- `{entity_type: X, must_satisfy: ...}` belongs in `pre_conditions` as `EntityCollectionSatisfies` (a PRE-condition predicate over a collection), NOT in `post_conditions`.
- `{state_field: F, change: ...}` is `StateFieldChange` and lives in `post_conditions`. Don't mix `state_field` with `entity_type` / `field` in the same dict.
- For state-field counts that GROW deterministically (e.g. `records` grows by `count`), prefer `EntityCountChange` if the field is also an entity type, else `StateFieldChange` with a prose description.

Example for a write-tool exercising the variants:

```yaml
process_refund:
  descriptor:
    writes_to: "Stripe + Postgres"
    stateful: true
    pre_conditions:
      - {field: user_id, must_be_present: true}
      - {entity_type: user, must_exist_with_id: user_id}
      - {field: amount, must_satisfy: "amount > 0"}
      - {entity_type: transaction, must_satisfy: "len(refunds_today) <= 2"}  # collection predicate
    post_conditions:
      - {entity_type: user, field: balance, change: "-amount"}
      - {entity_type: transaction, count_change: "+1", with_fields: {type: refund}}
      - {state_field: status, set_to: approved}
      - {state_field: messages, change: "append confirmation ToolMessage with refund_id"}  # non-deterministic
    error_cases:
      - condition: "user not found"
        returns: {status: rejected, rejection_reason: "User not found"}
```

The contract test generator reads `post_conditions` and emits real assertions:
"after `process_refund`, the user's balance must be `before - amount` and one
new transaction entity must exist." Backend mocks that don't maintain these
post_conditions fail their acceptance tests.

**Rule of thumb:** if `writes_to` is non-null AND `write_strategy: mocked` (the default), `post_conditions` MUST be non-empty — the backend needs structured state transitions to model the write. The verifier (Step 9e) rejects write-tools that violate this.

**When the writes are genuinely dynamic (`DataManipulationAgent`-style tools that produce any-shape output based on the user's NL request), DO NOT leave `post_conditions: []` with the default strategy.** Set `write_strategy: stub` instead — that tells the env-generator "this write is fire-and-forget; no faithful adapter, no post-condition enumeration required." The verifier then skips the post_conditions requirement for this tool.

**A mocked tool that writes agent state declares it in `state_updates:`.** When a `mode: mock` tool's real body returns state writes besides its message (a LangGraph `Command(update={...})`, an OpenAI Agents SDK `ctx.context.<attr> = ...`, a returned state dict), list its SUCCESS-path keys other than `messages` under the tool entry's `state_updates:` (a sibling of `descriptor:`), each as `{value: <literal>}`, `{from_result: <returns field>}` or `{omit: <reason>}`, reading any helper that builds the returned object; `descriptor.returns` declares what the MODEL sees (the real tool's return — a message string is `type: str`), so a `from_result` key the model never sees goes under `returns.state_fields`, not `returns.fields`; the dispatch wrapper lands them (`verify_simulate.py` fails `wrapper_drops_state_updates` when it does not). Drive the agent from its real entry so its own router runs; when `simulate.py` must drive a sub-graph below a router that reads one of these keys, call the router's own node function for the gate decision AND for its reply, and when that node needs anything the harness cannot call, raise `plurai.HarnessFault` naming the unreachable turn. Never write the refusal text yourself.

**A tool the framework gates on the user's confirmation declares `requires_user_confirmation: true` on its tool entry** — a sibling of `mode:`, never inside `descriptor:` (whose schema is closed). Set it ONLY from a gate the framework enforces, read off its declaration, and record the declaring `<path>:<line>` in `generation_trace.md`: Salesforce Agent Script `require_user_confirmation: True` on every definition of the action (the agentforce route's `--plan` prints them; a name whose definitions disagree stays unset and is a finding); LangGraph `interrupt_before=[<node>]` or HITL-middleware `interrupt_on` → the tools routed to that node; OpenAI Agents SDK `@function_tool(needs_approval=True)`. Never from description prose, a tool's name or effect, or an MCP `destructiveHint`. Absent means not declared; the backend states the list to every judge, so the ONE prompt such a tool takes is not graded as a re-ask.

**A tool the agent's own code calls on every run declares `invocation: fixed` on its tool entry** — a sibling of `mode:`, never inside `descriptor:`. Set it ONLY when the harness or pipeline calls the tool unconditionally, whatever the model or the data decide: a flow's fixed first step, a fetch the entry point makes before any model runs. Read it off the call site and record that `<path>:<line>` in `generation_trace.md`. A tool a model selects, or one code calls only on some branch (`if breach: send_alert(...)`), is `agent` — leave the key out, since absent means `agent`. Never set it from a tool's name, its effect (a send, a cancel or a hand-off is still the model's choice), or how often it fired in a run. The backend reads it so that a check which only asserts a fixed step fired, and is therefore true on every run, is not counted as grading the agent.

**Every mocked tool declares `error_channel: raises | returns` with its `error_channel_source`** on its tool entry, beside `mode:`: how the REAL tool reports the failure the mock models. Read it in evidence order: a production trace shows the failure (`trace`), the tool's docstring, schema or return annotation says it (`declared`), else its code (`inferred`, a suspect the quality loop re-checks); for a Python tool, `derive_tool_facts.py --write` writes the `inferred` channel wherever the tool's own body decides it, over an `inferred` or absent source only. Never execute a write to find out. The SDK's `mock_wrap` raises for `raises`; for `returns` the harness passes the real tool's failure value. A split world — the harness's own store holds some entity types, the mock the rest — declares those types as `world_state.owned_types` beside `owner`.

**A tool the agent offers only in some of its scopes declares `exposed_in: [<scope>, …]` on its tool entry** — a sibling of `mode:`, never inside `descriptor:`. A scope is a unit of the agent that holds its own tool list (a subagent, topic or graph node); list every scope whose tool list includes the tool, read off the source. Declare it only when the source scopes tools that way; a single tool loop leaves it out. The backend reads it to tell which flows run with a knowledge-base tool at hand.

**A sub-agent the agent invokes as a CALL is on its callable surface** (agent-as-tool, a delegation tool, a dispatcher call). Declare it as a `tools.<call name>` entry under the EXACT name the call carries on the tool trail, read off the framework's call site (prefix included), list it on the map's `tools` typed like any tool, and name it as that sub-agent's `call` on the map. Its `mode` follows the map's status for it. A sub-agent reached without a call (a graph edge, a router node) declares none and its `call` is `null`. `verify_config.py` fails a `call` that is not a `tools` key (`subagent_call_undeclared`).

**A called sub-agent that is itself a tool-using agent has its tool calls on the trail too.** List the tools it calls as `subagents[].tools` on the map, and give each tool entry an `exposed_in` that names the sub-agent's `call` (beside any other scope offering it): that is how scoring tells an unrecorded inner call from a miss (`subagent_tools_unstaged`). The harness attaches its recorder to each sub-agent as well as to the orchestrator (the framework's hook or callback on the wrapped agent), so a call made inside a delegation lands on the same trail, and it records the enclosing call's id on every inner row as `parent_call_id` (the delegation row's `call_id`; the per-framework recipe is in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/calls-inside-a-delegation.md`). Prove it with one turn that makes the agent delegate, run through the same child interpreter: an owned tool's call on `agent_state.tool_trail` carrying that `parent_call_id` → `inner_calls: linked` on the map; on the trail without it → `inner_calls: recorded` (`verify_simulate.py` warns `subagent_inner_calls_unlinked`); none → `inner_calls: unrecorded`, and each owned `mode: real` tool declares `observation_lane: none` (abstain) on its tool entry. Re-render the map (scan Step 2b). `verify_simulate.py` fails owned live tools that are neither proven nor abstaining (`subagent_inner_calls_unproven`).

**The agent's own tools are on its callable surface even when the mock sits BELOW them** (an HTTP, egress or DB adapter; a bridged agent). The mocked endpoints are not what its model calls: declare each of its own tools as `tools.<name>` under the exact name the trail carries, `mode: real` with `observation_lane: tool_trail` (siblings of `descriptor:`), and a descriptor with its description and args and no `reads_from` / `writes_to` — its I/O is the mocked tools' below it, and each mocked tool below names the own tools that call it in `serves:` with a `serves_cite` (Step 6). `serves` names only own tools whose code reaches the cited call: a seam call the product's own code makes on every turn outside every own tool (a route prelude, an entry-point fetch) declares `invocation: fixed` and no `serves`, and an endpoint reached both ways keeps `serves` and no `invocation`. `mode: real` generates nothing; scenario variables give it `reads: "world_state"`. `verify_config.py` fails a map tool that is neither a `tools` key, in `render_actions`, nor an `agent.native_tools` entry (`agent_tool_undeclared`). A Claude Agent SDK agent's CLI built-ins are not its own tools: `record_native_tools.py` records them as `agent.native_tools` (verify warns until it has); never declare them under `tools:`.

**Schema enforcement.** The exact grammar of every descriptor field
(`pre_conditions`, `post_conditions`, `error_cases`, etc.) is pinned by
the Pydantic model `ToolDescriptor` — import it as `from plurai import
ToolDescriptor` (it lives in `plurai_mockenv.descriptor_schema`; the module
`plurai.descriptor_schema` was deleted before v0.3.0 and raises
`ModuleNotFoundError`). It forbids unknown keys, so a field you invent is a
hard `extra_forbidden` error, not an ignored hint. The verifier
(Step 9e) runs `ToolDescriptor.model_validate(...)` on each tool's
descriptor and emits `descriptor_schema_invalid` (fail) on any shape
mismatch — typos like `change_too` or `set_too` are caught at generation
time, not silently ignored by the backend. The same schema is the backend
mock generator's parsing contract.

**Write `plurai.config.yaml` using the complete annotated example at `references/example_plurai_config.yaml` as your template** (keep the ownership header at its top: the comment saying the file belongs to the user; do the same for `simulation_prd.md`, below) — it carries the full structure (`agent`, `agent_context` with `precondition` fields, `tools` with mock/observe/real descriptors, `world_state.entity_types`, `tool_graph`, `stubs`). Fill every `{placeholder}` from your Step 1–6 analysis; keep the descriptor `pre_conditions`/`post_conditions`/`error_cases` precise so the backend mock is precise. The descriptor grammar is pinned by `ToolDescriptor` (`from plurai import ToolDescriptor`; the Step 9e verifier validates against it, and it rejects unknown keys).

**Every `world_state.entity_types.<type>.fields.<field>` carries `required: true|false`** — `true` on every field the agent or harness reads without a default (its ids, what the harness's storage reads with a fail-closed guard or a bare `row["f"]`, the record model's non-defaulted fields), `false` on the rest.

**A data invariant the agent's real rows always hold is a typed `checks:` entry on its type, copied, never written.** Run `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/measured_candidates.py" <agent root> --profile plurai_sim/artifacts/data_profile.md --data <each file of real rows: seed output, fixtures>`. It proposes, per list of rows, only checks that hold on every row: a field fixed while a flag holds, an id set copied from another file's list, a number computed from the row. Copy a proposal verbatim under `world_state.entity_types.<the type those rows are>.checks` when its fields are that type's fields. Skip one that only states how the data was generated: a combination the agent's own code tests for (it can occur), a value the agent's write tools produce, an `in` set whose list is a type the world seeds (a `references:` field). The backend refuses a seeded world that breaks a check, and the Step 9e verifier refuses a check whose literal or computed rule a real row of its cited file breaks, or one that cites the PRD (`world_check_refused`), and warns when an `in` set no longer matches its store's list or a real row's value falls outside it (`world_check_set_stale`: refresh the set). **A world type the run simulates whose rows are copied from one list of the agent's data that the agent only reads (no tool writes the type: a catalog, a code table) declares it: `reference_rows: <file>[#<list>]` on the type** — a judgement from the code (what the reader loads), never from field names. An id list in a reference store — such a table, or a file under a root `.scan_corpus_roots` or `artifacts/corpus_roots.json` records (content the agent reads live), or one you pass with `--reference-roots` — is proposed up to 2000 members for the other types that hold its ids: copy that set whole, except onto a `references:` field (the world seeds what it references). The verifier runs that part of the sweep itself and warns with the YAML to copy when no check cites a store (`world_check_unswept`), when a long id list no store holds could be one (`world_check_stores_unclassified`), or when a `reference_rows:` names no readable list (`world_reference_rows_invalid`).

**A `world_state` field that holds rows (`list[dict]`) types them under `item_specs:`** — every field a row carries, with its `type` and `required`, read from the record model the harness's read path builds from each row. The backend writes those types onto the world schema; a row field left in prose is typed from its name. The Step 9e verifier warns on an untyped row list. An `agent_context` field that an `agent.io.channels` entry of kind `structured_context` names is typed the same way: `item_specs` carry every key its value holds, with its `type` and `required`, and an object or list-of-object key nests its own `item_specs`, read from the schema or type the request is parsed with on its way in, found by following the code at the channel's `source` back to that parse, never from the keys one reader touches. When the code treats null, absent and empty differently for an `agent_context` field (`is None`, `.get(k, default)`, an empty map or list), its `description` states what each one means, citing that `<path>:<line>` (e.g. `{}` grants nothing while null grants everything).

**Never pin a computed result in a world entity** (no outcome or result table keyed by the request). When the request shapes what the service computes, the world holds the DATA: rows a tool returns are entities already, and rows no tool returns are stated as held by the entity that owns them (`dataset`: 'the rows the platform computes over'). The backend computes the result. A value keyed by id alone (a score, a rate) is a field. An outside party's decision (an approval, an outage) is seeded on that party's entity, never as a per-request outcome row.

**A failure the service computes is a rule, not prose:** each run-time failure code the source names (a job that ends `FAILED` with a code, a check that refuses at run time) becomes one `business_rules` entry on the tool whose call triggers it, naming the code and its condition, even when a later poll is what reports it. A rule that names only some values of a closed set the request carries (one request variant of several) ends `(source: <path>:<LINE>[-<END>])`, citing the span that states that scope and names both the value and the rule's code; a test, example or remedy hint that exercises one variant is not a scope, so without such a span the rule names no variant (Step 9e checks it: `rule_scope_uncited`). A map whose values come from a closed set the source names (column → `TEXT`|`INTEGER`|`REAL`) is typed `dict[str, Literal[...]]`, never a bare `dict`; the backend reads that type. A code left only in a description or an enum is one the backend cannot compute. An `error_cases` entry that quotes an error text ends its `condition` with the same `(source: …)`, citing the line that words that text; an error the code does not word quotes no text.

**A type a `references:` field names declares `id_field:`**, and so does a type a scenario field grounds from (`ground_from: {entity_type: <type>}`) — the declared field that holds a row's own id, when it is spelled neither `id` nor `<type>_id` (`id_field: pnr`, `id_field: bookingId`); `id_field: null` when the type has none. The backend keys every seeded row by it, so a reference holding the row's real id resolves; without it the rows are keyed `<type>_1`, and a grounded pin is judged by key names only. The Step 9e verifier refuses the config until it is decided.

**Declare a `role:` on every `stubs.modules` entry.** (A different key from
`descriptor.role` — the identity marker beside the storage strategies above; that one takes a
declared vocabulary and is compared exactly, this one is matched case-insensitively.) The stub of a
store's module gets `role: storage` (on a legacy real reader still labelled `mocked`, the Step 9e verifier
then requires its monkey-patch, Phase 3b); a stub that is NOT a store gets its own role (`role: telemetry`,
`role: cache`, `role: client`). Nothing else in the config names a storage module that sits in
a SIBLING package (`our_backend.db` beside `our_backend.agents.customer.tools`), so without
the declaration the verifier can only warn that it does not know what the module is.

**Declare the shape — `shape: dialog` or `shape: pipeline`, for BOTH shapes, from Step 0a.** One
root key, beside `workspace:` / `project:` (see the example). It names the hook the runner drives:
`dialog` → `invoke_agent`, `pipeline` → `invoke_pipeline`; a **skill agent declares `dialog`**,
because that is what it is at the protocol level. Write it even when it looks obvious — it is the
bench's own statement of its shape, and the only alternative is every reader guessing again from
the hooks, separately (a harness that exports both hooks cannot be guessed at all). Step 9e's
verifier refuses a declaration that contradicts the hooks `simulate.py` binds, so a wrong one is
caught here rather than on the first paid run.

**Declare the variables the agent reads from its environment — `agent.env:`.** The agent runs in a child
process that never inherits the launching shell whole. It receives only an allow-list — what any process needs
(search paths, interpreter, home and temp folders, locale, time zone, TLS roots, proxies), the SDK's own settings,
the sign-in variables of the agent's CLI — and the backend URL and token the runner passes. Every other variable
the agent or its libraries read — a model provider's key (`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `AZURE_OPENAI_API_KEY`),
its endpoint, deployment or API version, the agent's own service settings — reaches it only when named under the
ROOT key `agent.env:` as a list of names, never values: `agent.env: [AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT]`.
Read the names off the agent's code (`os.environ` / `getenv` reads, settings classes, the provider client's own
variables). A declared name the launching shell does not set stays unset; the backend token is never declared (the
SDK refuses it). A smoke that fails while such a variable is set in your shell names it on an `~ agent.env:` line.

**Declare what the agent can be HANDED — `agent.io:`.** Optional for a text-only bench and REQUIRED
for a bench that is handed documents (the paragraph after the subkeys). One ROOT key beside `shape:` /
`render_actions:` (see the example), with six subkeys and no others:

- **`input:`** — the MESSAGE CONTENT PART kinds this agent accepts, spelled as OpenAI spells a
  content part's `type`, which is the spelling that goes on the turn and the one the SDK's reader
  carries: `text`, `image_url`, `file`. `text` belongs in the list on essentially every bench —
  every scenario turn is a simulated user SPEAKING — but it declares the CONTENT PARTS only: the
  SDK never refuses a plain string turn on a declaration (`plurai.child._turn_messages`), so
  `input: []` is a legal declaration and the runner still speaks to that bench. What omitting
  `text` costs is that the setup smoke plans no `text` probe, leaving the channel every scenario
  uses confirmed by nothing — which is why the verifier WARNS rather than refusing. Add
  `image_url` / `file` whenever the agent really reads an attachment handed to the turn or a file the
  user names (the rule below); either dispatches the post-setup document ask NOW, because Step 9e refuses it with no
  `.document_fields` entry.
- **`channels:`** — each input taken OUTSIDE the message content, `{name, kind, field, source,
  handover?}`: `file_by_reference` (an id into a store), `structured_context` (a request field),
  `between_turn_action` (a UI change between turns), `host_injected_message`. `field` is the agent's
  request key, `source` the `path:line` reading it.
- **`may_open:`** — `true` only when this agent OPENS the conversation: `invoke_agent` serves a
  payload with an empty `messages` list, greets, and waits. Default `false`, and false is what
  almost every bench is.
- **`opens: always`** — only when production's HOST opens EVERY conversation (a page render, a
  greeting, a flow's initial turn) before the user types. The runner then drives the empty opener
  as turn 0 and sends the scenario's `first_talk` as turn 1; implies `may_open: true`. Omit it when
  the user speaks first — a `may_open` bench with a `first_talk` then runs user-first. With it, the
  harness serves its host context (the page render) on the EMPTY opener, never fused into turn 1.
- **`choice_text:` + `choice_text_source:`** — only for an agent that emits `interrupt`. Keyed by
  each choice exactly as the agent spells it in `choices`: what the harness's RESUME path does with
  the words the user types beside that choice — `delivered` when they reach the agent, `dropped`
  when the resume discards them (e.g. an approval that resumes with `new_input=None`). Read it off
  the resume code and cite that line: `choice_text: {approve: dropped, reject: delivered}`,
  `choice_text_source: simulate.py:142`. Never infer it from tool or choice names, and never write
  `dropped` for a branch you did not read; if you cannot read the resume code, omit both keys
  (absent = every answer is delivered).

**A bench that can be handed a document MUST declare `file`.** Put `file` in `input:` (and
`image_url` when the agent reads image parts) whenever the scenario set hands the agent a document
(a `.document_fields` entry, a `generated_document` field marked `owner: user`, Step 6.5's handover
arm) or the agent reads an attachment from a turn. The SDK drives a harness with no declaration as
`input: [text]` and fails every scenario whose user line carries another part kind, as
`harness_undeclared_input`: the twin runs, the document scenarios fail, and the set has already been
paid for. Step 9e refuses the gap (`handover_kind_not_declared`) and Step 9f's smoke fails on it without
sending the part, because the SDK refuses an undeclared part before `invoke_agent` runs. Declaring is half the job: `invoke_agent` must READ the part. Copy
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/document_input.py` beside
`simulate.py` as `_documents.py` and decode through it: `turn_documents(content)` on EVERY turn, the
opener included, yields each handed-over document as `Document(filename, bytes, media_type)` — the
filename carries the document's own extension (`uploads.md`, `file.txt`, `statement.pdf`) and the
media type comes off the `data:` URI, whatever the format (text, csv, json, pdf, image, office). That
is the ONLY route a handed-over document takes, so the harness reads no `agent_context` key or
`seed_state` value for a `.document_fields` field (Step 9e refuses it: `document_read_off_payload`) and
never invents a name. Hand the agent exactly that
`Document`; a harness that renames it or re-guesses its type hands the agent a different file.
Only the ROOT key is read: `agent: {io: …}` declares nothing, and Step 9e refuses it
(`agent_io_declared_not_at_root`).

**Two handovers; `invoke_agent` uses the one production uses** (the template's `HANDOVER`).
`content_part`: the agent has an upload channel (a vision block, an `attachments` argument), and the
decoded part goes into it. `by_path`: the agent takes text and reads the files a user names from disk
— a file-read tool it calls on paths from the message, instructions or docs about input files, a
CLI/SDK agent taking a path. That agent IS handed documents: declare `file`, write the
`.document_fields` entry, and have `invoke_agent` write each document (turn-1 field or part) into the scenario's `inputs/`
(inside the area the agent may read) and name its path in the message as its users do
(`[attached file: <path>]`). Step 9e refuses such an agent with neither (`input_file_channel_undeclared`).
A `file_by_reference` channel whose store the agent reads documents from (a thread's uploads) hands it documents
too: it dispatches the post-setup document ask, which writes the `.document_fields` entry and adds `file`.

**Write it from READING THE AGENT, exactly as you wrote `render_actions:` — not from the hook
signature.** Every dialog harness in existence has the same one
(`invoke_agent(payload: dict, config: dict | None = None) -> dict`), so the signature carries none
of this. What the agent's own code carries does: whether its model client is given multimodal
content, whether anything downstream of `invoke_agent` decodes an attachment or reads a file the user names, whether the graph has
an entry that runs with no human message. For `opens: always` read the HOST, not the graph: a
server, UI or flow that invokes the agent before the user types (a session/page-load call, an
initial turn on thread creation) means the host opens. `invoke_agent`'s BODY gives one negative hint — a harness
that reaches straight into `payload['messages'][-1]` cannot serve an empty opener, so it is not
`may_open: true` until it is changed.

**Model every input the production agent accepts.** `bench_manifest.py init` copies each one
`agent_inventory.py` reads off the source into `surfaces.inputs`: `modelled` (its `input:` part or
`channels:` entry) or `waived: {because, cite, detail}` — why the AGENT never receives it; what the
harness happens to drive is never a reason (`input_channel_unaccounted`). A task-type/provider
selector is a capability switch: cover each production value. A turn-1 precondition stays under
`agent_context:`; `render_actions:` is the output declaration.

**Omit the whole block when the agent takes plain text turns, is handed no document (by part or by path) and the user speaks first** — that is
the default, it needs no key, and a declaration copied from the template that this agent does not
satisfy is worse than none. **On a `shape: pipeline` bench it is inert** — a one-shot hop has no
conversation to open and no message parts to carry — so Step 9e's verifier WARNS on that pair
rather than refusing it: a harness may export both hooks, and `shape` names the route this run
takes. Write it only for the dialog route it describes. And
Step 9f's `--turn` smoke CONFIRMS every remaining declaration against the real harness — one probe
turn per declared non-`text` kind, plus the empty opener — so a declaration this agent cannot honour
is refused at setup, by name, rather than on the first paid run. **A declared part ARRIVES as
standard message content** — a content part of the user message on the turn's own `messages`
list, the OpenAI shape, so `content` is a list of `{'type': …}` parts instead of a string on that
turn and never a channel of its own — which is what leaves the harness's own words as the only
proof of arrival visible from outside it. **Have `invoke_agent` SAY what it
received** when it takes a non-text part: a harness that silently drops an attachment is otherwise
indistinguishable from one that read it, and the probe's file carries a marker precisely so an
agent that read it can prove so.

**Declare what the user SEES — `world_state.user_surfaces`.** Read the PRODUCT's code (its UI
views, templates, and the code that sends mail, texts or notifications), not the agent's tools, and
list each place the user sees the world outside the chat (see the example):

- `name` (lower_snake_case, unique), `kind` — `screen` (a view in the product) or a delivery
  (`email`, `text_message`, `notification`, `shared_with_me`) — and `title`, the product's own name for it.
- `rows` — `<type>` for entity rows, or `<type>[<selector>].<list_field>` for rows held in one
  entity's list field (the rows of the one parent entity the selector picks), which also needs
  `row_key`, the row field that keeps a row's identity across turns. The type is one declared under `entity_types`.
- `shown_fields` — the fields the view displays, in its order; `order` — `stored` or
  `newest_first`, the product's default order (a product that reorders by a heuristic declares its
  default); `numbered: true` when the product shows a running row number; `hidden_when` — row
  values the product hides a row on (a soft delete, an archived card).
- `recipient_field` — on every delivery, the row field holding the address it is sent to (a single
  address or a list of them); on a screen, only when the view shows the user's own rows.
- `actions` — the controls the user can use on a row: `name`, `label`, the `fields` it sets with
  their allowed values (`null` for any value), `when` it shows, and `values_from` for a field whose
  choices the product fills from its own data (`<field>: "<type>[<selector>].<list_field>"`, a list
  field declared `list[str]`). On rows inside a list field an action sets and tests only `shown_fields`, and it runs
  ONLY through `simulate.py`'s `apply_user_action(act: dict)` (`act` carries `surface`, `action`,
  `entity_id` = the parent row, `inline_row` = `{list_field, key_field, key}` and `updates`), which
  writes the edit the way the product does. A product that versions its
  rows makes one act one new version: the hook writes it the way the product's own save path does,
  attributed to the user, through the harness's own storage code — never an in-place rewrite of the
  current version. `verify_config.py` refuses such actions when `simulate.py` exports no `apply_user_action`.
- `joins` — a shown field holding another row's id that the product displays by name: `field`, the
  `target` (`<type>`, or `<type>.<list_field>` for items inside a list field), the target field to
  `match` and the one to `show`, and `within` (`{<target field>: <row field>}`) when the id is only
  unique inside a parent row. One join per field; declare one only when the product shows the name.
- Every field a surface names (`shown_fields`, `recipient_field`, `hidden_when`, `joins`,
  `actions`, `values_from`) is a field declared under that type's `entity_types` fields, read from
  the agent's code or storage. Declare it there first (a soft-delete flag such as `deleted`
  included); when the code gives no such field, leave the clause out.
- Two limits, stated to the customer as they are: a product that reorders rows by a heuristic is
  shown in its declared default `order`, and an act of the simulated user lands between the
  agent's turns, never during one.

A delivery's rows are a type the send writes: when the product stores none of its own, declare
`user_delivery` (`channel`, `to`, `subject`, `body`) under `entity_types`, and give the sending tool
a post_condition that creates that row; `verify_config.py` refuses a delivery surface no simulated
tool writes. When simulate.py exports `world_snapshot()`, the run reads the world from it, whoever
owns it (on `world_state.owner: agent | external` it is the only route): return the delivery's rows
from it, each stamped with its `_entity_type`; rows a mocked tool writes reach the run when
`world_state.owned_types` (the types the harness's own store holds) leaves their type to the mock. Omit the key when the scan has not established what the user sees; write
`user_surfaces: []` when nothing reaches the simulated user outside the chat. Once declared, the simulated user
sees the assistant's words, the render actions and these surfaces, and no longer the raw tool
calls, so declare every view the simulated user sees. Never on a `shape: pipeline` bench, which has no simulated user.
Guard: a surface is what a PERSON sees in the product or receives; a row the agent reads back is not one, and a wait the agent does inside one turn cannot be answered by the simulated user.

**Never author the managed egress block** (`# BEGIN plurai-simulation egress rev …` to `# END plurai-simulation egress`): `scripts/record_egress.py` owns it (scan Step 3d). When re-writing an existing `plurai.config.yaml`, copy that span over verbatim — its `allow_hosts` is the operator's waiver.

Do NOT write an `anonymize:` key. Setup uploads nothing, so it has no posture to record; the first ingest skill to run asks and writes it (see Step 7). The key is optional in `plurai.config.yaml` — absent means "not yet decided", which is what makes the ingest-time ask fire exactly once. Its placement, when written, is alongside `workspace:`/`project:` — see `references/example_plurai_config.yaml`.

When re-writing an existing `plurai.config.yaml`, carry `anonymize:`, `knowledge_out_of_audience:` and `approve_harness_edits:` over verbatim; never author any of them.

**`harness.entry_wrapper`** — write it only when `simulate.py` drives the agent through a runner
that ships with the bench rather than the product: a file `simulate.py` imports that in turn
imports the agent's package. Judge it from the file itself (its docstring, what it wires, where
it sits); the agent's package, its server route, CLI or `main` is product code and never a
wrapper. Block form, path relative to `plurai_sim/`:

```yaml
harness:
  entry_wrapper: ../run_bench.py
```

It is shown in the Step 7 plan and the topology approval: a declared runner is a harness file the
quality loop may later ask to edit. After writing the config, run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_fingerprint.py" --workdir "$WORKDIR" --entry-wrapper`;
when it prints findings, remove the declaration and say why in one line of the summary. The harness
fingerprint covers the declared file, so adding the key to an existing bench marks its earlier runs
stale once. On a re-write, carry an existing declaration over.

**Listen ports, accounts and `harness.single_instance`.** Read every port the agent or its runner listens on, the
production default too (a framework component's default binds even when no config names a port), and every store
account or key the harness would fix. Hand each child its own (PHASE 1c of the Python template): a port through the
config key, env var or argument that sets it — when a runner you may not edit passes the config inline, wrap the call
it makes — and a store account or namespace keyed by `plurai.active_session_id()`. Trials then stay parallel.
`harness.single_instance` (`["<short cite>"]`) is only for what no config, env, argument or wrapped call can hand out
per child — a device, a global lock; every conversation then waits for it. The Init smoke warns when siblings collide
(`sibling_init_failed`, `sibling_cross_talk`) or a declared resource never did (`exclusive_unneeded`).
`verify_config.py` refuses a value that is not a list of non-blank strings (`harness_single_instance_invalid`).

Do NOT write `workspace.run_driver` either, unless the user declared it (Step 7) or asked for
it. Absent means `interactive`, which is what every human run is; a pipeline that wants the
headless dispatch declares `workspace: {name: …, run_driver: headless}` itself. Step 9e's verifier
warns on an unknown `workspace.*` sub-key, so a typo'd `operator` is caught here rather than
discovered as a stalled CI job.


### Step 8c: Profile the data + probe the tools — verify claims BEFORE writing them

Reading code tells you what a tool COULD do; only running it tells you what it DOES under
the current config. A dormant code path (an unconfigured search backend, an unused
reranker) reads exactly like the live one, and a spec written from it sends every
downstream consumer — generation, judges, the quality loop — chasing behavior the agent
does not have. So write Step 9 from OBSERVED facts: profile the DATA the harness reads, and
spend a few seconds exercising the read-only tool layer.

**Two independent halves.** Profiling only READS files that already sit in the customer's repo — no
call, no external service, no tokens — so it runs on every setup. Probes invoke the agent's own
local or mocked tools with no network; an earlier scan's explicit "No" (`declined`) skips them, and
`data_profile.md` is written either way.

#### Step 8c.1: Data profiling (always runs — no consent involved)

**Profile EVERY file the harness reads as data, seed-state sources included.** Not "the
dataset": the main catalog, every lookup/reference table, AND every file the mock or the agent
loads as the source of SEEDED OR INITIAL state — the page/session/conversation bootstrap that
decides what the first turn already knows. **A file you NAME in the profile but do not profile
is the gap to close**: seed state is where an opening's vocabulary lives, so a closed set hiding
there costs a scored run to find.

Every derived claim in the profile is **computed, not narrated**: it is the output of profiling
code you actually ran, and the check that produced it is recorded beside it. A claim you
reasoned your way to but did not compute does not go in the file — a derived assertion stated
from intuition can be exactly inverted and still read as authoritative.

Profile at minimum:

- **Per-field null counts and row/entity counts**, per file.
- **Every low-cardinality string field, INCLUDING values nested inside list/array fields**
  — breadcrumb trails, tag arrays, label lists. A field whose distinct values across the
  whole dataset number in the low tens is a CLOSED SET; write it out as one. Nested values
  are the ones that get missed: a `category` column gets profiled and the breadcrumb trail
  carrying the same vocabulary does not.
- **Distributions, not just membership** — "`in_stock` 669 / `out_of_stock` 122, the other
  three schema-valid statuses absent" tells a scenario author which states are realistic;
  the bare enum does not.
- **Cross-file joins.** When two files describe the same logical entity, join them and check
  for FIELD-LEVEL disagreement (a differing price, status, name, or id format). Downstream
  reads one of the two and grades against the other.
- **A goal-facet satisfiability census.** Cross-tabulate the facets a scenario goal can bind at
  once — category × price band × availability, and whatever the equivalent is here — and emit
  the count of REAL rows per combination, naming the ZERO-row combinations explicitly. A goal
  that binds a zero-row combination is impossible for any compliant agent, and the grounding
  audit rejects the scenario after it has consumed a slot.

- **A tool-interaction hazard table.** Read the tool inventory once as a set of writes and
  reads, and record every pair where one tool's write CHANGES WHAT ANOTHER TOOL'S RESULT
  MEANS — not merely touches the same entity, but re-derives, invalidates, or re-labels
  something the other treats as fixed ground truth. Emit one row per pair: the writer, the
  reader, and the field or assumption that moves. These are derivable from the tool contracts
  alone, and they decide which scenario goals are authorable at all: chaining such a pair into
  one graded scenario asks the agent to satisfy a target that the first step has already
  redefined, which no compliant behaviour can achieve. The graph in the PRD's tool-dependency
  section records who touches what; this records who INVALIDATES what, which is a different
  question and the one scenario authoring needs answered.

**SWEEP EVERY UNIVERSAL NEGATIVE BEFORE IT SHIPS.** A claim of the form "exactly N", "the only
X", "any other X is constructed/forbidden" is a statement about EVERY registered source, but it
is computed from ONE — so before writing it, grep the member class across all of them (the
catalog files, the prompt files, and every document in the knowledge set) and reconcile what
comes back. On a hit: enumerate the found member, or narrow the claim to the source it was
actually computed from ("the CATALOG holds exactly 3" is true; "no other host exists" was not).

This is the cheapest defect in the whole pipeline to prevent and one of the most expensive to
find. **Guard: if the claim contains "only", "any other", "never", or an exact count, you must
have grepped the member class across EVERY registered source — the catalog, the prompts, and
every knowledge document — before writing it.** A universal negative computed from the catalog
alone ("`<field>` → `<value>` 3/3; any other value in a reply is constructed") is refuted by the
first knowledge document that tells users otherwise, and that counterexample is routinely a few
lines from the claim in a file the same profile already read. Eval generation consumes the claim
faithfully, and the judge then reds the agent for quoting the customer's own documentation.

The same rule governs the `closed_set` and `absent_from_source` predicates under `## Agent
facts`: a closed set is a promise about the whole registered surface, and `absent_from_source`
is a universal negative by definition. Sweep, then write.

**An `absent_from_source` fact states what the SOURCE lacks, never what the agent may not do.**
Whether the agent obtained the thing another way (a retrieval's citation, a tool's response, its
own prompt) is graded at run time against what it actually received, so never write the fact as
a boundary or a "must not". `terms` are the literals you swept; write `check.probe_ask` as the
question a user would ask, in their own words. **Guard: if the fact's `evidence` or `probe_ask`
says what the agent "must not" or "cannot" give, or `probe_ask` contains a search literal (a URL
scheme, a code identifier), rewrite it before you write it.**

**Record the SWEEP, not only its result.** Every fact you decided by reading the corpus carries
the optional `check:` block (`references/agent_facts.md`) — the literals and scope you actually
searched — so the pre-generation contract re-runs it against those documents instead of trusting
the sentence. A fact with no `check:` is reported as unverified, which is the honest outcome; a
`check:` whose terms you did not sweep is a claim you never made.

Write it to `plurai_sim/artifacts/data_profile.md`. It is evidence later phases reuse instead
of re-deriving, and the generation skills stash it to the backend as a grounding input beside
the PRD — so it is authored to be READ, not as scratch notes.

The profile states facts; it never diagnoses. A fact here that will NARROW what the suite tests
becomes both a prohibition and a `potential` agent finding when Step 9 writes the PRD.

#### Step 8c.2: Behavioral probes (no consent, no network, non-blocking)

1. **Probe when the topology's `probes` list is non-empty**, unless the manifest's
   `consent.probes.state` is `declined` (an explicit "No" from an earlier scan, imported from
   `plurai_sim/.probe_consent`): then skip, and Step 9 marks behavioral claims
   `(from code — unprobed)`. Never ask here: tool probes need no consent, because they call only
   local or mocked surfaces and the runner denies them the network.
2. **Derive final eligibility from `plurai.config.yaml` and INTERSECT with the `probes` list.**
   **When `tools:` is empty, eligibility is the topology's own `probes` list** — those names are
   the agent's internal read-only functions, not tool descriptors, so an eligibility rule keyed on
   `tools.<name>` intersects to nothing and a render-action agent silently probes nothing.
   Eligibility is read off declared config facts, all but the last under `tools.<name>`:
   - **Never probed:** a writer (non-null `descriptor.writes_to`, or `descriptor.stateful: true`);
     a tool the map types with a service (`backend` other than `none`; its live check, Step 8c.3, covers it);
     a tool the config runs against a live external system (`mode: real|observe` whose
     `descriptor.reads_from` names an external service read for real — `descriptor.read_strategy: real` —
     or that carries a tool-level `credentials:` block, sibling of `descriptor`); any module the
     top-level `stubs:` list marks import-dangerous.
   - **Probed, no question:** a local-data tool (in-repo fixtures, a bundled catalog, a local index),
     and a `mode: mock` tool (or a `mode: real` tool a seam mock `serves:`) with its
     external client stubbed in-process, which exercises the tool's own code around the mock seam.
   The paths matter: a mis-named key silently matches nothing and the gate passes everything.
   **Never invoke the agent graph or any LLM-bearing path** — probes target the tool/data layer
   only, cost zero tokens, and finish in seconds.
3. **Author a short per-agent probe script** — you just read the code; you know the
   imports and constructors (there is deliberately no generic runner). Save it under
   `$WORKDIR/probes/`, declare its check (step 6), and run it ONLY through
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reverify --only <check id>`,
   never as a heredoc of your own: the first run and every re-run then get the same network deny,
   so a stub the script forgot fails the call instead of reaching the service, and the claim stays
   `(from code — unprobed)`. A machine with no network sandbox leaves the probe `not_run`; a refused
   manifest (exit 2) names what to fix first. Never run the script outside the runner. Skip this step when
   `LANG = "node"`: the bridge that swaps the agent's clients does not exist yet, so a probe would
   run them unswapped; the claims stay `(from code — unprobed)` until the bridge skill's
   `verify_bridge.py`. Skip it for the claude-skill shape too: its tool surface has no separately
   callable layer. ANY exception downgrades the affected claims to `(from code — unprobed)` and
   the flow continues — a probe is an accuracy upgrade, never a precondition. Never
   fabricate an observation.
4. **Four probes cover the claim classes that have shipped false:**
   - **Matching semantics** — one synonym/paraphrase query vs one literal-token query
     against the retrieval layer. If the paraphrase returns nothing relevant, the spec
     says literal matching — whatever richer machinery the code carries.
   - **Argument constraints** — one out-of-enum / out-of-range value; record the observed
     result verbatim (e.g. "unknown category → empty result list, no error").
   - **Returned fields** — one real call; the observed row IS the field list the spec
     declares. A field the model defines but no tool returns is not part of the contract.
     An envelope observed with the external call stubbed is recorded as `returns.type_source: stub_run`.
   - **Limits** — for each numeric parameter with a default or a documented range on a tool
     ALREADY in this eligible set, one call ABOVE the assumed maximum (`top_k=50` against a
     default of 5), plus a degenerate one where it is free (`limit=0`, empty-string filter).
     Record what actually happened — silent clamp, truncation, or refusal — as a
     `tool_surface` / `hard_limit` fact. **Probing at or below the configured value proves
     nothing about the limit**: a probe that confirms `top_k: 5` and then tries `top_k=1` has
     tested the documented path twice, and the cap above it stays unrecorded until a later
     critic asks for 50, gets 20 back, and invalidates whatever pool size the PRD claimed.
     Code citation does not surface a clamp; only the boundary does.
     Keep magnitudes modest — a small multiple of the default, never a stress test: a probe
     is a few read-only calls, not load. The step-2 exclusions are
     unchanged, so a WRITE-capable tool is never probed this way; its limits ship as
     code-cited `hard_limit` facts or `(inferred)`. An agent with no numeric parameters skips
     this class entirely.
5. **Write `plurai_sim/artifacts/probe_report.md`**: each exact call, its observed output
   (trimmed), and this caveat verbatim: "A probe covers the call probed, not its siblings
   on the same adapter, and says nothing about how the AGENT behaves — it verifies the
   spec, never the behavior."

6. **The probe SCRIPT is the deliverable, not just its output — save it under
   `$WORKDIR/probes/` and declare it in `$WORKDIR/bench_manifest.json`.** Until this rule existed, probes were
   written to the session's scratchpad and died with the session: a bench kept a probe report
   whose probes no longer existed anywhere, and the next scan reused the report because nothing
   could tell that its evidence had no source. A persisted, hashed script is what makes "reuse"
   an honest word.

   Add one entry per probe to `$WORKDIR/bench_manifest.json`'s `checks[]` (the scan's Step 2a
   created the file; `bench_manifest.py validate` refuses a declared check whose script is missing):

   ```json
   {"id": "probe_load_flow", "kind": "probe", "verifies": ["surfaces.knowledge[<name>]"],
    "runner": "node", "script": "probes/p1_load_flow.mjs", "requires": ["node"],
    "policy": "on_change", "derived_from": {"paths": ["src/flows/schema.ts"]},
    "feeds": ["<the typed fact ids this observation supports>"]}
   ```

   `derived_from.paths` are the agent files the probe reads: the runner re-runs it when they move
   and reuses the recorded pass when they do not, so nobody decides that again by hand. `verifies`
   names each probed surface as `surfaces.<its map bank>[<name>]`, and `sources` for a rule probe —
   a field no check names is reported `declared_only`, which is the honest state.

7. **Declare the SURFACE assertion in the same `checks[]` whenever the agent has a surface that is
   enumerable at run time.**
   `kind: surface`, a script that prints the agent's LIVE surface as a JSON array, and
   `expect.equals` naming the declaration it must equal — `surfaces.render_actions[*].name`, or
   the bound tool list. This is Step 9f.4 generalised: "declared == bound" is vacuous on a bench
   whose `tools:` is empty, and that is exactly the bench that shipped an invented verb into a
   graded run with nothing checking.

#### Step 8c.3: Live credential check (every tool that stays real, before generation)

A tool the bench runs for real must be shown to reach its service with the credentials this machine has. One check
per service the map's `backend` names (the tools that share a client share it):

1. **Author `$WORKDIR/live_checks/<service>.py`** (`.mjs` / `.sh` in the agent's own language): ONE read-only call
   the tool itself makes — through the tool's function, or the client call it wraps when the tool catches errors —
   the smallest there is: a count, `LIMIT 1`, a list of one, or the client's own auth / whoami call. Never a write,
   never a seeded id (the service holds real data, not the scenario world). Print the result; let any exception
   propagate (no try/except).
2. **Declare it** in `bench_manifest.json` `checks[]` and run it ONLY through the runner — it adds the network and the
   harness `.env`, and reads the exit status:
   ```json
   {"id": "live_<service>", "kind": "live", "runner": "python", "script": "live_checks/<service>.py",
    "verifies": ["surfaces.tools[<each real tool of this service>]"], "credentials": ["<ENV names its client signs in with>"],
    "policy": "always", "blocking": true}
   ```
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reverify --only live_<service>`.
   `credentials` names only the variables with no working default; `validate` refuses a real tool with a service and
   no live check, and a probe that claims one.
3. **Read the result.** `verified` → done. `failed` (cause `live:failed`, its `evidence` the check's own last error
   line, verbatim) or `not_run`: when that error is the call's arguments or the interpreter, fix it (never the call
   target, never a catch) and re-run once. Any other failure, or one that survived that one repair, is the user's:
   **STOP and ask** (sanctioned — `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker`), once for
   every failing service, in plain language: "<tools> run for real but could not read <service>
   <(NAME is not set)>: "<evidence>"." Options: **I've fixed it — check again** (re-run the check once; still
   failing → ask again with only the last two options) · **Mock these tools** · **Stop the scan**. Headless or
   unanswered: mock.
4. **Mock these tools** = `status: mocked` on the map (`why`: "its service does not accept this machine's
   credentials; mocked at your answer"), `mode: mock` in `plurai.config.yaml`, drop them from the check's
   `verifies` (the check goes when it verifies none), re-render 2b and re-run `bench_manifest.py init`. Name each
   mocked tool in the scan's summary.

### Step 9: Generate simulation_prd.md

**The PRD facts contract at the top of this file governs this step**; `references/prd_authoring_contract.md` holds its worked block (granular, cited facts). The order of work: prose, then the scaffold script, then your measured
facts, then the lint until it exits `0`. A long file you must read to its end prints in numbered, footed sections:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/skill_section.py" <file> "<heading text>"` ends with `END` or `CONTINUES`; line
numbers that jump before the footer, or no footer, mean the output was cut.

This is the domain knowledge document. The backend reads it to understand the business domain
deeply enough to generate realistic, consistent mock behavior. Without this, the backend
only has tool names and types — not enough to simulate real business logic. Two channels live
in this one file with different jobs: the prose ground-truth lists (below) feed GENERATION,
while the VERBATIM SOURCE APPENDIX (Step 9b) feeds JUDGE AUTHORITY — keep both; neither
substitutes for the other.

**Read the agent code, docstrings, comments, README, and any docs in the repo.** Extract
everything about the business domain. The PRD should be thorough enough that someone who
has never seen the codebase could understand how the agent is supposed to behave.

**Sources are the repo, the corpus, and traces — never your own knowledge of the product.**
A plausible capability the sources don't state enters the PRD as a fact generation builds on
and judges enforce, untraceable afterwards. Follow the template's **Sourcing contract**: for a
corpus-backed agent, agent-knowledge claims carry a `(source: <doc>)` citation or an explicit
`(unverified — not found in corpus)` marker (the corpus is authoritative on those facts — after
`/build-rag-corpus`, its Step 6.5 reconciles this PRD against the ingested chunks). Rules that
steer scenario BUILDING (families, "never mention X") go only in the template's
`## Scenario design constraints` section — the backend ROUTES on that heading, keeping the
section out of the auditor's fact surface. Use that exact wording; the same sentence under a
heading the backend does not match is read as a world fact.

**Sweep the finished draft for ABSENCE CLAIMS.** Every product kind, capability, and enum member
the draft NAMES gets verified against the data profile and the corpus before the PRD goes
anywhere: search for it, and a claim with ZERO hits is removed or corrected AT THE SOURCE, never
softened in place. An invented capability propagates into the corpus build and the dimension
graph, where nothing later re-checks it — from there on it reads as ground truth everywhere.

**Write `plurai_sim/simulation_prd.md` using the structure in `references/simulation_prd_template.md`** — it carries the full fill-in template (Agent Purpose, Scenario design constraints, the typed `## Agent facts` block, Entity Schemas + invariants + state machines, per-tool Specifications, Conversation Patterns, Business Rules including the load-bearing CAPABILITY BOUNDARIES & HARD LIMITS callout that scenario generation depends on, the Tool Dependency Graph, and External Service Contracts). Populate every section from the agent code.

**Required, before you hand-author any fact: scaffold the agent's own instructions.** Write the prose sections first, with
no `## Agent facts` block, so the file exists; the script adds the block: the `instruction_sources` and `rule` facts, the
quoted-instructions block and the `RENDER_ACTIONS` closing fact (one you wrote earlier is replaced). It copies the agent's
bytes; you never retype them.

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scaffold_rule_facts.py" --repo-root . \
  --source <system-prompt-file>[:LO-HI] [--source <task-or-agent-config>] \
  --config "$WORKDIR/plurai.config.yaml" --profile "$ART/data_profile.md" [--corpus <knowledge-docs-dir>] --into "$WORKDIR/simulation_prd.md"
```

`--source` is the text the model is given as instructions at run time: system prompts, task and agent configs, every
skill or rules file (`.claude/skills/*/SKILL.md` for a Claude Agent SDK agent whose options set `skills=` or
`setting_sources`) and each live sub-agent's own prompt, never a tool description (that is a `tool_surface` fact). A
prompt rendered from a template is the template plus what fills each slot: follow every `{slot}` to the code that fills
it, pass the file or range that fills it with instructions as its own `--source`, and decline a slot filled with data (the
conversation, a name, a date) `=data-slot@<the line that fills it>`. The scaffold names a loaded
skill file you did not pass, and the lint reports one the PRD neither declares nor declines whole (`=not-loaded@<load site>`). It prints a numbered list of the units it rowed. Read it: a unit that is
not an instruction (a persona line, an example) is declined, not rowed — re-run with
`--decline <path>:<LO>[-<HI>]=not-an-instruction` (or `=duplicate-of-<subject>`, or `=not-loaded@<path>:<LINE>`). In a
markdown source, the front matter, fenced code blocks and table rows are declined `not-an-instruction` already; one that
is an instruction goes back with `--row <path>:<LO>[-<HI>]`. A line of template slots only stays open until you decline
it `=filled-by@<the range that fills it>` (passed as `--source`) or `=data-slot@<the line that fills it>`.
With `--profile` it also writes the measured facts the profiled files settle (a closed set per structured URL field, a URL the instructions name that no set holds, topics the instructions call out of scope that no document mentions), each stating what it counted: check each against the source it cites, drop one with `--skip-measured <subject>`, and add the measured facts it cannot see. The lint reports one the PRD never types.
Everything between its `scaffold:` markers is the script's: re-running replaces it, so add your own facts (tool
surfaces, data profiles) outside the markers, and choose the `RENDER_ACTIONS` sweep verbs with `--omit-terms a,b,c`. Then lint. Read
`references/prd_authoring_contract.md` for what the rest of the facts block must look like.

These rules keep the Tool Specifications section TRUE, not merely sourced:

- **Effective-path rule.** A tool spec describes what runs under the CURRENT config/env —
  trace which implementation is actually instantiated, not which classes exist. A code
  path that is present but unconfigured is omitted, or named as exactly that ("an
  Elasticsearch + reranker path exists in the code but is not configured; the live path
  is literal token overlap"). Class inventory is not behavior.
- **Profile→contract coupling — route each fact, and leave the BULK where it was computed.**
  `data_profile.md` is itself a grounding input: the generation skills stash it and the backend
  reads it beside the PRD, so machine-computed INVENTORIES stay there and are NOT transcribed
  into PRD prose — per-field null tables, full value distributions, row counts, the
  satisfiability census. What the PRD keeps is narrative, business rules, tool contracts, a
  COMPACT FACT SUMMARY (the named closed sets and the rules that depend on them) and steering.
  The summary is not optional: the dimension-graph flow extraction reads the PRD, not the
  profile, so a closed set that shapes a flow still needs its one line here.
  **Route each fact by who it constrains**: a closed set the TOOL validates is a tool-spec
  argument constraint, while a closed set that bounds what the DATA actually contains — the
  page/breadcrumb/category values that occur, which states are real — is a typed
  `data_profile` / `closed_set` (or `distribution`) fact under `## Agent facts`. Both are facts
  and both belong in the fact surface; neither becomes a rule about what may be authored. A
  scenario authored against a value the data never contains is unsatisfiable, and that costs a
  full regeneration round to discover — which is what the fact prevents, without anyone having
  to write the prohibition down.
- **A field that is null on every row is a typed FACT, and you must write it as one.** Emit it
  under `## Agent facts` — `data_profile` / `product.material` / `null_on_all_rows` / the
  measurement — never as a rule about what may be authored or how a decline is graded. Every
  null-on-every-row field, every never-returned field, every capability the tools do not
  implement, and every field the harness cannot seed earns one entry. This is the single
  highest-yield thing the data profile buys you, and it is cheap here and expensive later.
  The full contract + the closed `kind`/`predicate` vocabulary:
  `references/agent_facts.md`.
- **NEVER author a rule derived from THIS agent's data or tool surface.** Forbidden anywhere in
  the PRD or the variables file: target-shape rules ("do not author a target requiring
  `<field>`", "`<field>` is authorable via `<tool>`"), grading rules ("grade the decline as
  correct", "a blanket refusal is not correct behaviour"), and authoring bans or licences read
  off the profile. Such a rule is semantically nondeterministic — the same repo scanned twice has
  produced same-sized PRDs carrying OPPOSITE law, and nothing downstream can tell which roll it
  read. The backend already holds the generic invariant, agent-agnostically: a target must ground
  in the licensed tool surface, and an honest decline grades correct where it does not. Your job
  is the facts it consumes, not a restatement of it in this agent's vocabulary. Genuine design
  steering — families, anchors, what an opening may not name, unsatisfiable combinations — is
  unaffected and stays in `## Scenario design constraints`.
- **NO SILENT CARVE-OUT — every fact that NARROWS what the suite can test also earns a
  `potential` entry in `$ART/agent_findings.md`,** naming the carve-out it bought. A fact
  recorded only in the PRD ships a suite scoped around a suspicion nobody outside the PRD can
  see. These are **potential**, not verdicts: a null column may be exactly what the owner
  intends. A raise you reproduced by running the agent's own code is `confirmed`, with the
  exact repro; `potential` is only for carve-outs.
- **A `source_unbound` fact is a defect hypothesis, not a carve-out, and its entry says so:**
  status `potential`, finding id `agent.<tool>_source_unbound`, severity **high** when
  `empty_means: none` and **medium** when `unknown`, naming the empty binding and its line, "how to
  confirm: check the platform's knowledge-source setting for `<tool>`"
  (`references/agent_facts.md` § `source_unbound`).
- **Before you record a null field, check whether the value is RECOVERABLE from free text.** When
  a field is null on every row but the row carries a title, description or notes blob, grep a
  census of how often the value appears in that text and put the fraction in the entry — "the
  catalogue does not have this" and "the pipeline is dropping this" are different findings.
- **A field the DATA holds but no tool RETURNS is the same class**, and there it is the agent's
  serializer to name, not the catalogue.
- **Create the findings file if it is absent; otherwise APPEND to it.** Never truncate it: a
  re-scan meets a file whose confirmed entries its arm has just retired as tombstones, and whose
  `potential` entries — the previous scan's and the last build's — nothing re-derives. Read it first, update in place any section whose
  root cause you are re-finding, and number new ones from the highest number the file has carried, tombstones included.
  Resolve the path in the SAME Bash call as the write — the harness resets the shell between
  calls, so an unset `$ART` would land the file at `/agent_findings.md`:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; mkdir -p "$ART"
  cat "$ART/agent_findings.md" 2>/dev/null   # existing sections + the highest number in use
  ```

  ```markdown
  # Agent findings — what the suite caught in the agent

  <!-- Appended as the suite is built and qualified. Evidence for /simulation-fixes.
       Never fed back into a critic. -->

  <!-- DELETE THIS ENTRY. It is a SHAPE, not a finding: every `<…>`/`{…}` span below is a
       placeholder. `/simulation-fixes` reads this file as evidence about the AGENT, so a
       placeholder that survives the copy is a FABRICATED MEASUREMENT it will rank and act on. -->
  ## 1. Cannot state what a product is made of
  - **status:** potential — may be intended
  - **severity if real:** medium
  - **finding id:** `agent.material_not_stated`
  - **found during:** scan
  - **evidence:** `<entity>.<field>` null on {<the count you computed>, over <population>};
    present in the free-text `<other field>` on {<the recoverable count>}.
  - **why it might be intended:** the owner may have retired the field deliberately.
  - **what the suite does about it:** typed PRD fact — `data_profile` / `<entity>.<field>` /
    `null_on_all_rows`; the backend's target-grounding invariant consumes it.
  - **how to confirm:** ask the owner whether `<path/to/file.ext:LINE>` should be parsing it out
    of that free-text field.
  ```

  Then record its typed fields, in the same Bash call — the prune decides on these, not on the
  prose: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/record_finding.py" "$ART/agent_findings.md" --id
  <finding id> --title "<its section title>" --status <confirmed|potential> --found-during scan --severity <high|medium|low>`,
  and again whenever its status changes. A limit whose cause is in `plurai_sim/` (the harness, a mock, the seeded data)
  is not the agent's: seed the precondition, and when it cannot be, add `--cause test_setup` (moved to the internal file).

  One section per ROOT CAUSE — six fields dropped by one scraper is ONE entry naming six
  fields, not six entries. Continue the numbering from the highest already in the file and never
  renumber: `/generate-evals` and the quality gate append their own sections here too, and
  `/simulation-fixes` reads every tier out of it.
- **Examples must satisfy the enums the PRD itself declares.** Before finishing, re-read every
  worked example, sample payload and JSON block and check each value against the closed sets
  written above it. A PRD that declares a two-value breadcrumb set and then illustrates it with
  a third, invented trail teaches generation the opposite of the rule it just stated.
- **Behavioral markers.** Every behavioral claim in the Tool Specifications carries
  `(observed — probe_report.md)` when Step 8c.2 exercised it, or `(from code — unprobed)`
  when it did not. These are DISTINCT from the corpus-sourcing markers
  (`(source: …)` / `(unverified — …)`), which keep their existing meaning and
  reconciliation machinery.
- **An unprobed surface CITES its code; it never paraphrases it — and never pastes it.** For a
  node no probe exercised, name the decisive lines as `path/to/file.py:LINE` in the tool spec
  and keep the `(from code — unprobed)` marker. A paraphrase of a contract nothing exercised is
  the shape that ships wrong in several places at once, and the citation is what a later reader
  checks it against. Quote at most a short identifier-level fragment inline (a constant, an enum,
  a raised message) where the exact bytes ARE the contract; anything longer is a citation.
  **Source code never goes in the verbatim appendix** — that section is for the customer's
  knowledge documents, and Step 9b carries the rule and its reasons.
- **The agent's OWN INSTRUCTION TEXT is quoted, never restated.** Its system-prompt rules,
  routing and refusal wording, and tool descriptions are contract surfaces exactly like code:
  a rule the PRD restates in its own words becomes the rule the suite is authored and graded
  against, whether or not it is what the agent was told. Quote the sentence and cite where it
  lives. Declare the files holding the agent's instructions as `instruction_sources:`, and give
  every instruction in them a typed `rule` fact or a declined `unrowed` range
  (`references/agent_facts.md`); `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scaffold_rule_facts.py` writes both, byte for byte, so
  run it instead of typing them. A tool description is a `tool_surface` fact, never a `rule`.
- **Mark an inference as an inference.** When a behaviour is DERIVED from the wording rather
  than stated by it — what a limit applies to, whether a rule is per-turn or per-message,
  what a mandate implies about a case it does not name — write it with an explicit
  `(inferred)` marker beside the quote it came from. An inference presented as a contract is
  indistinguishable from a fact downstream, and it is the generator, the auditor, and every
  judge that act on it. If the inference matters enough to author scenarios around, it is
  worth a behavioral probe (Step 8c.2) or a question to the customer instead.
- **The same rule covers a claim about what the CODE DOES** — id stability, mutation
  semantics, ordering, idempotency. Either CITE where you read it (`path/to/file.py:LINE`, or
  the appendix block holding the bytes) or mark it `(inferred)`. Those four classes are read
  as contracts by the generator and by every judge, so a wrong one is authored into every
  scenario built on it — and closed-source behaviour, which has no location to cite, is
  exactly what the marker is for. **Where the agent HAS such a surface, the claim also gets a
  typed fact** — `tool_surface` / `writes` · `id_stability` · `ordering` · `idempotency`. These
  are the claims Step 8c.2 may never probe (it excludes every non-null `descriptor.writes_to`),
  so for a mutating tool the typed `source` is usually the only evidence they will ever carry,
  and prose can be reworded on the next scan where a typed predicate cannot come back inverted.
  An agent with no such surface writes no such facts; see `references/agent_facts.md`.

**Never summarize away enumerable ground truth — short CLOSED LISTS of literal values are
copied verbatim, never paraphrased.** The PRD is the only eval-generation input carrying the
agent's domain ground truth, and a paraphrased list produces judges that check membership in a
list nobody has (observed live: a judge failed the agent for using the catalog's own URLs).
Scope: identifier-shaped lists (URLs, ids, names, enum members) AND verbatim procedures —
a documented step sequence the agent is graded on is copied as ordered steps with the docs'
own control names (a summarized sequence makes the grounding judge fail the agent's correct,
doc-verbatim steps). Cap ~40 lines / ~2 KB per item; the agent's knowledge/doc PROSE at large
is corpus material, summarized as usual; over the cap, say the list/procedure exists and
point at the corpus. The template's ENUMERABLE GROUND TRUTH callout carries the full rule.
**The scope also covers a MOCKED service's contract tables**: its documented per-variant output
formats (one layout per kind/variant/mode the service returns) and its request-validation /
error rules (which input is refused, with which status and code) are copied VERBATIM, every row,
into §7 External Service Contracts. They are what the mock is built from: paraphrased, the mock
invents a different layout on every call and accepts requests the real service refuses. No cap
and no corpus pointer for these — the corpus never reaches the mock. **Never cite a file in place
of its content** (`see <doc> §n`): env-gen receives only `plurai.config.yaml`,
`simulation_prd.md` and `test_mock_contract.py`, so a rule that lives only in a cited file never
reaches the mock. A service with no such documented table gets none invented.

**A re-scan OVERWRITES — diff before you write.** When `$WORKDIR/simulation_prd.md` already
exists, nothing carries its rules forward: this roll of the same authoring either states them
again or loses them. So when the file exists, write the new one to
`$ART/simulation_prd.draft.md` (and the variables, if you re-authored them, to
`$ART/scenario_variables.draft.yaml`) and run the guard before moving either into place:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/rescan_diff.py" \
  --old "$WORKDIR/simulation_prd.md" --new "$ART/simulation_prd.draft.md" \
  --old-vars "$WORKDIR/scenario_variables.yaml" --new-vars "$ART/scenario_variables.draft.yaml"
```

Drop the `--*-vars` pair unless BOTH files exist. Exit 0 → move the draft into place and carry
on. Exit 1 → it lists what this overwrite would DROP or INVERT; **stop and ask** (sanctioned ask
— first run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction rescan-diff-approval`), batching
every item into that ONE call, in plain product language and never file names. An approved item
lands as drafted; a rejected one keeps the OLD sentence inside the new document.
**Under `run_driver: headless` the declared default for `rescan-diff-approval` is `decline`**: every
listed item keeps its OLD sentence and the flow continues — the two sides are the same authoring
rolled twice, so without an answer nothing the old document said is dropped.

`CONTRADICTED` notes print alongside but are **ADVISORY — they do not gate, and they are not part
of the ask.** Each marks a place the new document may state one subject two ways (a rule against
its own verbatim appendix, or against another section). Read them, reconcile the ones you agree
with, and move on; the tier reads polarity out of prose, so it is a prompt to look, never a
verdict, and it must never be the reason a flow stops.

**Then lint the draft.** Several of the ways a PRD goes wrong are decidable from the files on
disk, so check them here rather than letting a scored run and a review pass find them:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/lint_prd.py" \
  "$WORKDIR/simulation_prd.md" --config "$WORKDIR/plurai.config.yaml"
```

It reports a world fact parked in `## Scenario design constraints`, where the auditor reads
steering rather than facts so nothing grounds it and no judge can cite it; declared tools
and `render_actions:` verbs the PRD names nowhere, and a declared render surface the
`RENDER_ACTIONS` fact does not close (or contradicts); a link/URL `absent_from_source` fact on an agent whose retrieval
declares `observation_lane: tool_trail_cited` (its citations carry links); a `` `path:line` `` citation that does
not resolve (missing file, or a line past the end of one — existence only; whether the bytes
say what the claim says is a reading, not a lint); a target-shape or grading rule derived from
THIS agent, which belongs under `## Agent facts` as the measurement instead; a family list
whose head or entries the backend's family parser does not read (`- Families: (A) …; (B) …`
is the shape it reads); the facts block itself against its closed `kind`/`predicate`
vocabulary; and a line in a declared `instruction_sources:` range that no `rule` fact cites and
no `unrowed` range declines, or a `rule` fact citing outside every range (row it or decline it).
**Every finding is open work, not a note.** The lint exits `4` while any finding remains, the bench manifest then
records `lint_prd` as `not_verified` (never `verified`), and the integrity line carries `PRD lint: N open`. Fix the
PRD and re-run until it exits `0`. A PRD with no `## Agent facts` block is one open finding (it records nothing typed), and so is a facts block with no `instruction_sources`, or a `rule` fact that quotes a whole file as one rule.
Keep a finding only when you verified it does not apply (a tool documented as out of scope; a quote the lint cannot
match byte for byte because the source file wraps it): accept it with the `[fp:xxxxxxxx]` that ends its line and the
reason you verified, `lint_prd.py "$WORKDIR/simulation_prd.md" --config "$WORKDIR/plurai.config.yaml" --accept <fp> --reason "<what you verified, citing file:line>"`.
Accept several in ONE call: repeat the `--accept <fp> --reason "…"` pair, or write one `<fp> <reason>` line per
finding to a file and pass `--accept-from <file>`; each still needs its own reason, and a refused line is named while
the rest are recorded.
The acceptance lands in the gate ledger and lapses when the finding's text changes; it is never a way to silence a
finding you did not read, and the hand-off does not mention it.

No acceptance clears an error (exit `1`): **the `## Agent facts` block placed in a
section the auditor does not read (nested under the design constraints, or after the verbatim
appendix) is an ERROR naming the section — move it; no disposition makes an unread fact
ground anything.** **So is a fact repeating another's `subject` + `predicate` (`agent_facts.md`,
"One fact per pair") — merge the two it names.** So is a facts block that is not YAML.
A tool documented as out of scope is a valid answer; a census stranded in the directives
section is not.

**It also checks the three claim shapes that NAME their own falsifier**, and those are worth
fixing before you move on because each one that survives is paid for later at scenario-generation
prices: a fenced block declared a **byte-exact** copy of a named file whose bytes are not in it
(the shape that ships is a RENDERED view of a manifest, one field short of it); a **whole-file
negative** — "no `try/except` anywhere in the file" — the file itself refutes, reported with the
lines that refute it; and **one counted noun phrase given two different counts** in the same
document. A claim you cannot state checkably carries `(inferred)` instead. These three were all
false in one shipped PRD, were found by a critic three rounds later, and cost ~45 minutes of
mid-loop authoring plus the input-edit round that forfeited scenario adoption.

### Step 9b: VERBATIM SOURCE APPENDIX — register the knowledge base byte-exactly

**WHEN to emit.** Append a `## VERBATIM SOURCE APPENDIX` section (the template's final
section carries the full format contract as a NON-LIVE fenced skeleton — unfence and fill
it; the live heading must never ship empty) when the agent's knowledge is a **closed,
enumerable doc set** — which covers both:

- knowledge **prompt-inlined** into the agent's system prompt (a closed doc set, an FAQ,
  product guides pasted at construction time), detected from the PRD/config (knowledge docs
  named as prompt-injected, a `knowledge_extraction_policy`, a closed doc list) — the same
  predicate `/build-rag-corpus` Step 1 treats as a first-class case; **or**
- a small enumerable doc set behind a retrieval tool (roughly ≤10 files / ≤100KB total).

Skip it only when the knowledge is genuinely open-ended (a large or growing corpus nobody can
enumerate). Then delete the template section.

**Knowledge documents ONLY — never source code**: an entry is a document the agent answers FROM,
never an implementation file or a system-prompt module. The template's appendix section carries
the two reasons (it uploads with the PRD; each entry registers as a gradeable `source` unit).
An agent with no document corpus emits NO appendix — a correct and common outcome, not a gap to
fill.

**Emitting no appendix does NOT mean the agent has no knowledge surface, and it never
reduces how closely you read the code.** An agent whose knowledge is an open corpus, an
index, or a database still answers from something — it still needs its retrieval semantics
profiled, its evidence-grounding directive written, and every behavioral claim cited to
`path:line`. What the appendix decides is which DOCUMENTS judges get silence authority over;
it decides nothing about how much of the implementation you read or how many facts you
record. Reading less because you are pasting less is the one way this rule makes a PRD worse.

**A corpus does NOT replace the appendix — they are different channels with different
authority.** The corpus is where planted QUESTIONS come from; the appendix is what gives judges
authority over the customer's documents. Concretely: the backend registers appendix entries as
`source` units and, when the heading carries `(COMPLETE)`, marks each of them closed — the ONLY
thing that licenses a judge to say *"the sources do not mention X"*. A corpus always registers
open, because a corpus is sampled, never closed, and one sampled passage cancels the
REGISTRY-WIDE closed-set flag for good. The appendix entries keep their per-document closed
scope through that cancellation, which is what makes the mixed case work: judges get silence
authority over the documents you enumerated and nowhere else. So on a corpus-backed agent with
an enumerable doc set, emitting BOTH is correct and omitting the
appendix silently downgrades every groundedness judge to PARTIAL coverage, where fabrication is
only catchable when the invented text CONTRADICTS an included excerpt — an invented procedure the
docs simply never mention sails through. Observed live: a set whose primary groundedness judge
caught one fabricated UI element and missed six invented procedures.

**HOW to emit — append MECHANICALLY, never retype.** Byte-exactness is the whole point of
the appendix: a hand-retyped paragraph is exactly the paraphrase-as-authority failure the
channel exists to kill. **Every entry is a byte-exact `cat` of one of the customer's REAL
source files** — operator-authored prose does not belong here at all; if there is no real
file behind the text, it stays in the PRD prose. Build it with `cat`, e.g.:

```bash
{ printf '\n## VERBATIM SOURCE APPENDIX (COMPLETE)\n'; for f in docs/faq.md docs/refund_policy.md; do printf '\n### %s\n\n````\n' "$(basename "$f")"; cat "$f"; [ -n "$(tail -c1 "$f")" ] && printf '\n'; printf '````\n'; done; } >> plurai_sim/simulation_prd.md
```

(The `tail -c1` guard adds a newline before the closing fence ONLY when the file lacks a
trailing one — an unconditional `\n` would append a blank line to every already
newline-terminated file, so the registered bytes would differ from the file's bytes.)

Adjust the fence per file (the template's fence-length rule: one longer than the file's
longest backtick run, minimum 4 when the file contains ```), and keep the appendix the
LAST `##` section of the PRD. Sizing note — **this is the judge-time INJECTION channel, not
the auditor's grounding materials; the two are unrelated and these numbers are live**: the
backend registers a doc ≤8KB as ONE all-or-nothing unit and splits a bigger doc at its own
markdown headings; at judge time 24KB is the CUMULATIVE injection budget across ALL units —
units are included in order until the budget is spent, and any unit that doesn't fit is
skipped whole (never truncated) — prefer several small real files over one concatenated blob.

**`(COMPLETE)` only on operator confirmation.** Keep the `(COMPLETE)` token only when the
operator confirms the enumerated files ARE the whole knowledge base; otherwise drop it
from the heading. It licenses judges to assert "the sources do not mention X", so a file
missing under COMPLETE manufactures false negatives.

That token is load-bearing and its effect is invisible from here, so know what it sets: the
backend derives `declared_closed_set` from `(COMPLETE)` **and** a clean parse — every `###`
entry must register, or the flag is cancelled (a shrunken appendix is not a closed world). At
judge time there are THREE tiers, not two:

- `declared_closed_set: true` plus full coverage renders `COVERAGE: … COMPLETE` — silence
  claims licensed over the whole registry.
- A corpus (or any other outside `source` unit) cancels the registry-wide flag, but the
  appendix documents keep their own closed scope: the `COVERAGE:` line still reads `PARTIAL`
  while a separate **MIXED COVERAGE** licence paragraph grants silence claims over exactly
  the named appendix documents. This is the normal shape for a corpus-backed agent that also
  declared its doc set — do not read the `PARTIAL` on the coverage line as "no licence".
- No token, or a lost entry, and there is no closed scope anywhere: plain `PARTIAL`, judges
  forbidden to conclude the sources are silent on anything.

A dropped token, a missing fence, or one fact left outside the appendix each turn the switch
off with no error anywhere.

**A PARTIAL entry is labelled an EXCERPT — a bare file path claims the whole file.** When the
entry carries only part of a file (the decisive lines of an unprobed node's source, one section
of a long doc), label it as what it is — `### pricing.py — EXCERPT (lines 40-118)` — and never as
the bare path. Under a `(COMPLETE)` appendix a whole-file label grants judges silence authority
over the half you did not include, so they may conclude "the sources do not mention X" about the
bytes you left out.

**If you ever remove or shorten an appendix entry, delete the `(COMPLETE)` token YOURSELF —
nothing will do it for you.** The parser cancels the closed-set assertion only when an entry
fails to PARSE; deleting a whole entry, or shortening one inside its fence, parses clean, so
the assertion survives over a set that no longer contains the document — and judges keep full
authority to say "the sources do not mention X" about it. That is an invisible manufactured
false negative. Either drop `(COMPLETE)` from the heading, or do not cut here.

**Verify before upload — ALWAYS, not just when this step applied.** Run the client-side
parse check on every PRD before it is uploaded anywhere:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_prd_appendix.py" plurai_sim/simulation_prd.md
```

It ports the backend parser's behavior (heading, entries, fence closing, the four
lost-label causes) and exits 1 on any lost label — including an appendix heading with
zero entries, which is exactly what a leftover template heading (kept live by mistake
instead of deleted/unfenced) looks like. A PRD with no appendix heading exits 0, so the
unconditional run costs nothing when Step 9b did not apply.

It also checks the `## Scenario design constraints` heading the backend ROUTES on, and exits
1 only on a heading the backend will NOT route — a reworded one, or one numbered at more than
one level (`3.1`) — since such a section routes nowhere and its directives are then judged as
world facts. Forms the backend does tolerate (any heading level, a single-level section number,
a hyphen join, singular, a trailing colon, any case) route fine and are reported as a nudge, not
a failure. No such section at all is fine (exit 0).
And it prints a largest-first map of the prose sections, for orientation; there is no size
target to author toward.

### Scan progress fills — the page moves while the setup is built

At the start of Steps 9c, 10 and 11 fill the overview page with that step's milestone, so a customer watching the local
page sees the scan advance. It never ends the turn, and it fills only inside a /simulate run (run on its own, it skips):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step <milestone> --flowdir "$FLOWDIR"
```

A milestone never publishes: publish nothing and run no `done`, whatever the line says. Then run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" "$WORKDIR" --flowdir "$FLOWDIR"`
and say what it prints, verbatim (report doctrine, item 1). Ignore every field of the line; a failed fill moves on: no
retry, no word — the next fill catches the page up.

### Step 9c: PRD claim audit — an independent reader checks write-path and count-path claims

**Progress fill** `scan-described` ("Scan progress fills", above) first.

A claim about which code writes a field or what a limit counts is decided at sites the claim does not name (a
second writer, a shared default, a second list added to the counted one), so the lint cannot judge it and the
author's own reading is the one in question. Once the PRD is final, list every site of every declared name:

```bash
cd "<agent-folder>"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/claim_sites.py" --repo-root . --config "$WORKDIR/plurai.config.yaml"
```

It writes `$ART/claim_sites.json`. Then spawn ONE fresh, report-only `general-purpose` sub-agent with the prompt in
`references/prd_claim_audit.md` and wait for it; it writes `$ART/prd_claim_audit.jsonl`. Give it the three paths
only, never your notes or any sub-agent's summary of the code; it reads no other file under `plurai_sim/`. Re-run `lint_prd.py`: each refuted claim is an open
finding naming the sites that refute it. One repair round: correct each one from those sites, or accept it with
the file:line you verified, re-run the lint once and move on. Never re-run the audit in a loop, and never hold the
flow on it. Where no sub-agent can be spawned the status line reads `claim audit: not run`, and the scan continues.


### Step 10: Run the language-specific generation sub-skill

**Progress fill** `scan-harness` ("Scan progress fills", above) first.

Steps 8 and 9 above produced `plurai.config.yaml` and `simulation_prd.md` —
the language-agnostic outputs. Now take the row for `LANG` from Step 0, run
that sub-skill, and follow it to completion:

| `LANG` | run the skill | it generates |
|---|---|---|
| `python` | `simulation-setup-python` | `simulate.py`, the `test_mock_contract.py` acceptance tests, the contract verifier (`verify_simulate.py`), the smoke loop |
| `node` | `simulation-setup-bridge` | `simulate.py` + `bridge/` (map, entry, runtime), the route probe, then the Python sub-skill's Step 9 checks and smoke |
| `claude-skill` | `simulation-setup-skill` | the command under test rendered into `skill_prompt.md`, the nested-session `simulate.py` + `_skill_tools.py`, the smoke loop |
| `agentforce` | `simulation-setup-agentforce` | the rewired COPY of the agent, `simulate.py`, the customer-facing run instructions, an Apex health-callout smoke |
| any other runtime | none: Step 0 already stopped | — |

> **Settings and the harness's model provider (all shapes).** Before asking the user for a setting or key, run
> `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" env-check <NAMES> --providers`: a name it reports
> found is never asked about. When the harness makes the model call itself (the agent's own route cannot run off its
> server), use a provider it reports found (default credential files count), the agent's own first; ask only when
> none is found, naming where it looked. A declared `harness.env_file` is the file the harness loads.

> **Harness-fidelity rule (all shapes): never inject guardrails for behaviors the
> evals measure.** Whatever the harness puts in the agent-under-test's system
> prompt (the rendered `skill_prompt.md` for skills; the system prompt built in
> `simulate.py` for dialog/pipeline agents) must be FAITHFUL to the production
> agent. Do not add "grounding"/"only report what tools returned", privacy/
> anonymization, honesty, or routing instructions the production agent does not
> already carry — those are exactly what groundedness/privacy/faithfulness judges
> score, and pre-instructing them teaches the agent to the test and inflates the
> result. If such a rule is genuinely needed, it belongs in the production agent,
> not the harness.

**`claude-skill`: thread the `action_channel`** inferred at Step 7
(`agent.action_channel` in `plurai.config.yaml` — no default; the value was
determined from the SDK-call surface) into the sub-skill — its **S4** selects
the matching `simulate.py` variant (`tools` = hermetic in-process MCP; `code` =
Bash-enabled SDK snippets intercepted by the `plurai` import-hook), and **S3**
records the `(original_module, original_function)` map +
`original_positional_args` the `code` channel needs. **Every other row: do NOT
thread an `action_channel`** — that key is claude-skill-only; Step 7 was skipped
and `agent.action_channel` must be absent from `plurai.config.yaml`.

Each sub-skill ends with a "Return to dispatcher" pointer back to this file —
when the sub-skill finishes successfully, continue here at Step 11 below
(the chained-skills section).

The sub-skills assume `plurai.config.yaml` and `simulation_prd.md` already
exist (they read PRD Section 5 to encode constraints in `test_mock_contract.py`,
for instance) — Steps 8 and 9 satisfy that precondition.


### Step 11: Continue with `extract-scenario-variables` (chained by default)

After Step 10 succeeds (the language sub-skill returned, its smoke passed), proceed automatically to the next skill in the pipeline.

**Skip this step if** the user passed `--no-extract-vars` (or said "skip extract-scenario-variables", "no scenario variables", "without extract-scenario-variables") in their original invocation. Otherwise:

1. Print to the user: "Setup complete. Continuing with the scenario variables (scenario_variables.yaml)…"
   **Progress fill** `scan-users` ("Scan progress fills", above).
2. Run the `extract-scenario-variables` skill.
3. Follow it to completion against the SAME agent path the user gave for `/simulation-scan-agent`. The skill's preconditions (`plurai.config.yaml` and `simulation_prd.md` must exist) are already satisfied by Step 8 / Step 9.
4. The skill ends by running its bundled `${CLAUDE_PLUGIN_ROOT}/skills/extract-scenario-variables/scripts/verify.py` (its Step 5) and iterating on validation errors. Do not stop until `scenario_variables.yaml` validates.

After this step, `<agent>/plurai_sim/scenario_variables.yaml` exists and is valid.

### Step 12: Continue with `generate-evals` (chained by default)

**Skip this step if** the user passed `--no-generate-evals` (or said "skip evals", "no evals", "without generate-evals"). Otherwise:

1. Print to the user: "Continuing with the evals (policies_config.yaml)…"
2. Run the `generate-evals` skill.
3. Follow it against the same agent path. Preconditions (`scenario_variables.yaml` from Step 11) are satisfied.
4. Author the policies, validate via `${CLAUDE_PLUGIN_ROOT}/skills/generate-evals/scripts/verify_policies.py`, iterate up to 3 attempts.

After this step, `<agent>/plurai_sim/policies_config.yaml` exists and is valid.

### Step 13: Final summary

Print the full set of artifacts the agent now has:

```
<agent>/plurai_sim/
├── plurai.config.yaml         (Step 8)
├── simulation_prd.md          (Step 9)
├── simulate.py                (sub-skill Step 8 — Plurai-orchestrated entry point)
├── test_mock_contract.py      (sub-skill Step 9d; skipped when no tool is mocked — tools: {} agents have no mock to accept)
├── scenario_variables.yaml    (Step 11; skipped if --no-extract-vars)
├── policies_config.yaml       (Step 12; skipped if --no-generate-evals)
└── artifacts/
    ├── data_profile.md        (Step 8c.1 — row counts, enum members, null-on-every-row fields,
    │                           the satisfiability census; independent of the probes, and
    │                           absent only when the harness reads no local data at all)
    ├── probe_report.md        (Step 8c.2; only when a tool was eligible and probes were not declined)
    └── agent_findings.md      (Step 9 — potential agent issues the scan recorded while writing
                                the PRD's prohibitions; the quality gate appends confirmed
                                defects to the same file later)
```

The Step 9e / 9f verifiers are not in that list and are never copied there: they run from
`${CLAUDE_PLUGIN_ROOT}` so each run uses the verifier the loaded plugin ships, not one frozen at
the version that set the bench up.

**When `agent_findings.md` holds sections, say so in ONE line** — before the next-steps list, and
in the customer's own language rather than as a file report. Print its link — never type the path,
which opens on no surface the reader has, and never publish the file (the overview page is the only
published page); the line is the file's own link, a path or a `file://` URL on Copilot and Codex:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Potential agent issues →" "$ART/agent_findings.md"
```

> While profiling I recorded N potential issues in the agent — things it cannot currently do that
> the suite had to design around, and that may be intended. &lt;the link line `report_link.py link` printed&gt;

Do not rank them, quote the evidence, or propose fixes here; the scan has run no scenarios and
proved nothing.

**Gate before the next-steps list.** Before pointing the user at the suite build, confirm the Python
sub-skill's **Step 9f** (`verify_smoke.py` — child-subprocess Init smoke) was run AND exited 0 (or 3/4: only a failure the run measures), and that the
live tool set matches the declared tools (9f.4). That is the whole local gate. A bench whose manifest declares
`harness.tool_interception.at: agent_host` has no 9f: its local gate is the declared `route_smoke`, which the suite runs
once a world exists.

There is deliberately **no local end-to-end run during setup** — the runner drives a Plurai backend that
does not exist on the setup machine, and no backend scenarios exist yet. The end-to-end gate is the FIRST
REAL RUN: `/simulation-run` then `/simulation-fixes`, after the suite build (`/simulation-generate-suite`).
Do NOT block setup on it, and do not ask the user to run a local end-to-end script.

Only after Step 9f passes (or the user has explicitly acknowledged proceeding without it), surface the **post-setup next-steps sequence** to the user. Setup is done; this is the workflow they run on their own to actually evaluate the agent.

**Branch on agent shape (from Step 0a):**

- **Dialog / tool-using shape** (`simulate.py` exports `invoke_agent`): present the full 4-step sequence below — UNLESS the run-mode line below prints `run_mode: mockless` (a tool-less dialog agent, e.g. a single-LLM conversational flow, or one whose every tool runs live). First run `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-mode` — it derives the posture from `plurai.config.yaml` and writes the `.run_mode` marker `/simulation-generate-suite` Step 0c and `/simulation-run` require. On `mockless`, **SKIP step 1** (the mock-env build) exactly like the pipeline shape: there are no mocks to build and no seeded world the agent could read. Say so in ONE line and proceed to steps 2 → 3 → 4. Do NOT ask the user whether to "deploy an empty env anyway", and NEVER phrase the skip as skipping "the backend" — `/generate-scenarios` and `/generate-evals` are independent backend jobs that the customer still needs for every agent shape; only the mock-env deploy is moot.
- **One-shot pipeline shape** (`simulate.py` exports `invoke_pipeline` only): **SKIP step 1** (the mock-env build) — and run the same run-mode line as the tool-less branch above, which records the posture. Pipeline agents have no tools to mock and no mock environment to deploy — there is literally nothing for env-gen to do. The pipeline-shape sequence is just steps 2 → 3 → 4: `/simulation-generate-suite` → enable LangSmith → `/simulation-run`. The Step 9f smoke is the only pre-flight check available before running the experiment: on a pipeline bench `smoke_pipeline` calls `invoke_pipeline` once per declared `agent.smoke_inputs` world on the local mock — **it proves the hop runs without raising and validates no pipeline output**. Say so rather than implying a shape check happened. Do NOT suggest a mock-env build to a pipeline-shape customer — there is no signal it can produce.
- **Hosted Agentforce agent** (`LANG = "agentforce"`): this bullet WINS over the dialog-shape one above. An Agentforce agent is a dialog shape too and matches both, but its `simulate.py` drives a hosted agent through the Agent API rather than an in-repo one, so the extra step below is part of its sequence and the dialog bullet's tool-less branch never applies (an Agentforce bench always has mockable actions or it would not have been rewired). Present the full 4-step sequence, and say that `/simulation-generate-suite` runs the declared `route_smoke` itself once it has deployed the world, before its first paid turn — `bash <workdir>/agentforce_mock_path.sh up`, with the resolved `$WORKDIR` substituted for `<workdir>` before you print it (a named-env bench keeps the script under `plurai_sim/envs/<name>` rather than `plurai_sim/`). The scan planned the rewrite and generated no org metadata on purpose: the rewired copy's Named Credential URL carries the mock env's id, and that id only exists once `/simulation-generate-suite` has deployed the world. `up` generates the copy against that env, deploys it, pushes the credential and runs the Apex health callout; `/simulation-run` then drives the copy. It needs a logged-in `sf` CLI and it touches the customer's org, so this skill never runs it. `AGENTFORCE_MOCK_PATH.md`, beside that script in the same workdir, is the written form of the same three commands (`up` / `down` / `clean`).

**When retrieval OR prompt-injected knowledge is involved, SAY SO in the hand-off — do not ask, and do not build.** If the tool audit (Steps 5–6) classified ANY tool as a RAG / retrieval / vector-search or text2SQL tool — *regardless of the mock mode you chose for it* (`real`, `observe`, or `mock`) — OR the agent carries prompt-injected knowledge (a closed doc set / FAQ / guides inlined into its system prompt; build-rag-corpus Step 1 treats this no-tool case as first-class) — AND no corpus has been built yet (no `plurai_sim/.corpus_id` on disk), tell the user this agent needs one and that **`/simulation-generate-suite` will ask about it and build it** at its grounding gate.

**The corpus decision has exactly ONE owner, and it is not this skill.** A corpus's scope decides what the whole suite can test, and the suite's gate is where that decision sits beside the other grounding inputs (the trace brief, the doc examples) with the answerability and scope checks around it. Asking here also cannot work when this skill runs DRIVEN — `/simulation-scan-agent` calls it inside a flow window, where `ask_guard` denies any ask this step has no sanction for.

State it once, with the consequence, and move on. e.g.:

> I detected a retrieval/text2SQL tool (`<tool-name>`) — or, for a tool-less knowledge agent: I detected prompt-inlined knowledge (`<doc set / FAQ>`) — and no knowledge corpus has been built yet. The corpus is the **ground truth scenario generation builds the knowledge scenarios from** — grounded questions with known answers + real entities seeded into world-state. **Without it, scenario generation produces 0 RAG scenarios and the knowledge base is never tested — even though the tool (when there is one) still executes at runtime.** `/simulation-generate-suite` will ask you about it before it generates anything (extraction is local — your creds never leave the machine).

If NEITHER a retrieval/text2SQL tool NOR prompt-injected knowledge was detected — a pure transactional agent — OMIT this entirely.

**When the agent is HANDED DOCUMENTS, ASK the user — same discipline as the RAG prompt.** It dispatches on a declaration — when `agent.io.input` names `file` or `image_url` (Step 8), a `mode: real` tool reads a document from an `agent_context` field (the `document_fields` entry Step 6 planned for it), a pipeline hop's `invoke_pipeline` input carries a document's content (Step 0a, e.g. `raw_text`), or `agent.io.channels` declares a `file_by_reference` channel whose store the agent reads documents from (Step 8, e.g. a thread's uploads) — AND no document trigger is configured yet (no `plurai_sim/.document_fields` on disk), you **MUST ASK the user** (sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction document-generation`) — as a direct question, BEFORE the post-setup handoff — whether scenario generation should author a realistic document per scenario for that field. This fires for **BOTH shapes**, and crucially for the **pipeline** shape: a one-shot extractor (an invoice / receipt / ticket reader) SKIPS `/generate-mock-env`, so **this is the only place its document generation can be configured**. Without it, that field is filled with generic LLM placeholder text and the extraction is tested against unrealistic input. Phrase it with the consequence stated, e.g.:

> I detected a document-extraction agent — the `<field>` field holds the document content (tool/pipeline `<name>`). Want scenario generation to author a realistic document for each scenario (a generated invoice / form / receipt / ticket / memo / spreadsheet / slide deck, with per-scenario variety) instead of generic placeholder text? It makes the extraction test far more faithful. (**Text**, **binary** (image/PDF), and **office** documents are all supported: a text extractor receives the document's extracted text; a **vision/OCR** tool that reads a real **image or PDF** receives the rendered binary delivered as base64 the harness decodes to bytes; a reader of **Word/Excel/PowerPoint** (or any other format your extraction expects) receives a real, library-openable file the same way — so a ticket-image / scanned-form / office-document agent's real reading path IS exercised.)

If the user says **yes**, write the trigger to `plurai_sim/.document_fields` — a JSON list, one entry per document field, each `{field, format, brief}`:

```json
[{"field": "raw_text", "format": "txt", "brief": "the plain text of a single vendor invoice — header, line items, totals"}]
```
- **`field`** — the EXACT field name the agent reads. For a **pipeline** agent take it from the `invoke_pipeline` inputs you discovered building the harness (e.g. `raw_text`); for a **stateful/dialog** agent take it from the document-ingesting tool's input (or the `agent_context` field). If the field name only becomes concrete after `/generate-mock-env` produces the schema, `/generate-mock-env` Step 6.4b confirms/writes this marker then.
- **`format`** — pick by what the agent's extraction actually reads: `"txt"` for a text extractor (it receives the document's extracted text), `"image"` / `"pdf"` for a **vision/OCR** tool (the backend renders the document and delivers it as a base64 data URI the harness decodes to `(bytes, content_type)` for the real vision call), or `"docx"` / `"xlsx"` / `"pptx"` for a reader of Word/Excel/PowerPoint (the backend authors a real, library-openable file directly). For a ticket-image / boarding-pass / scanned-form agent, use `"image"`. Any OTHER extension your agent's extraction expects also works — the backend routes it through a generic authoring+validation path, no closed list to match against.
- **`brief`** — one line naming the document type + the fields it should contain. The backend also passes the scenario prose, so per-scenario variety comes for free.
- **`formats`** (plural, optional) — when the agent accepts more than one format for the same field, declare `"formats": ["pdf", "docx", "xlsx"]` instead of a single `"format"`; the scenario generator then picks one per scenario, so one suite can mix real `.pdf`/`.docx`/`.xlsx` handovers rather than fixing one format for the whole agent.

On a dialog bench the same pass adds `file` to `agent.io.input` (Step 8): the entry without that declaration fails Step 9e as `handover_kind_not_declared`, and `invoke_agent` reads the document with `turn_documents` on every turn, never off the payload (`document_read_off_payload`).

`/generate-scenarios` reads `.document_fields` and turns document generation on for those fields (the UNIVERSAL trigger — no `scenario_state_schema` needed, so it works for a schema-less pipeline agent). The generated value reaches the harness exactly as Step 6.5's wire rule says, and never on the store. If the user **declines**, write nothing — generation stays off, byte-identical — and drop `file` / `image_url` from `agent.io.input`. **Under `run_driver: headless` the declared default for `document-generation` is `enable`**: write the entry for the declared field, never drop the declaration. If neither declaration names a document, OMIT this entirely. (Requires the backend's document-generation support.)

**Optional follow-on when `.document_fields` was configured:** if the user just agreed to enable document generation (i.e. `.document_fields` was written above), mention that they can also run `/upload-doc-examples` to make those generated documents more realistic. The backend templates generated documents from real examples when they are available — without them it uses only the brief for generation. Phrase it as a light optional offer, e.g.:

> If you have real example documents of the type the agent reads (invoices, receipts, forms, etc.), you can run `/upload-doc-examples` at any time before `/simulation-generate-suite`. The backend will use them as few-shot examples when authoring scenario documents, making the generated inputs closely resemble your actual production documents. Skip it if you don't have examples handy — the brief-only generation still works.

Only show this when `.document_fields` was configured in the step above. Omit it entirely for non-document-extraction agents.

**Present the hand-off exactly as
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/references/post-setup-handoff.md` writes it — the
order carries strict dependencies.** Read that file before you write the message. The steps are
not independent (the suite must run before observability, and observability before the scored
run), so a hand-off reassembled from memory sends the customer down a sequence that cannot
complete; and a corpus named there is named, never asked for — `/simulation-generate-suite` will
ask about it and build it at its grounding gate.

**MCP timeouts for long backend turns — a HARNESS-START setting.** The MCP client reads
`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` (ms; default `300000` for HTTP MCP servers — an IDLE window
that fires on silence, and a backend-Claude turn is silent while it thinks) and
`MCP_TOOL_TIMEOUT` (ms; the separate wall-clock ceiling) when Claude Code STARTS, from the `env`
block of settings.json or the launching shell — an `export` inside a Bash call mutates a
throwaway subshell and changes nothing about an already-connected server. Long edit-session
turns (`/edit-evals`, `/edit-env`) routinely outlive the 300 s default, so recommend raising the
idle window (e.g. `900000`) there BEFORE those sessions. The `edit-evals` skill carries the aborted-call semantics and the
landed-but-aborted protocol. The durable server-side fix (turn retrieval) is tracked separately.

**Why we chain by default:** the three skills have a strict linear dependency (extract-scenario-variables reads simulation_prd.md + plurai.config.yaml; generate-evals reads scenario_variables.yaml). Running them separately in three rounds wastes operator time and risks the user forgetting a step. The opt-out flags exist for cases where the user has their own scenarios (skip extract) or plans to hand-author policies (skip generate-evals).

**Why the corpus is NAMED here but decided by the suite:** the corpus is a distinct job with its own lifecycle (local extraction of customer data → backend KG build + testset synthesis → a persisted `corpus_id`), and its SCOPE decides what the whole suite can test. That decision belongs at `/simulation-generate-suite`'s grounding gate, where it sits beside the other grounding inputs and inside the answerability, scope-reconciliation and coverage checks — and after `/analyze-traces` has had its turn. A corpus decided anywhere else answers the gate's question before the user is asked it. It is NOT made optional by a `mode: real` tool: running retrieval `real` executes it at runtime but still yields **0 RAG scenarios** without a corpus (the generator has no knowledge flow + no ground-truth answers to author from), which is why Step 0 still SAYS the agent needs one — it just does not act.

