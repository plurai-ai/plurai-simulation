---
name: simulation-setup-skill
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Internal phase of simulation setup for an agent that is a Claude Code plugin command or skill (LANG is claude-skill). Reached only from simulation-setup Step 10. Never invoke directly; use simulation-setup.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Simulation setup — Claude-skill generation phase

This skill is the claude-skill tail of `simulation-setup`. Preconditions:

- The `simulation-setup` skill's Steps 0–9 have already run: confirmed analysis,
  `plurai.config.yaml` (Step 8) and `simulation_prd.md` (Step 9) exist on disk.
- `LANG = "claude-skill"` was detected in Step 0 (plugin manifest + command
  markdown, no Python/TS agent entry point).

When the smoke (Step S6) succeeds, return to
the `simulation-setup` skill, which is already loaded in this session, and continue at the
chained-skills section (Step 11).

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

## The architecture in one diagram

```
plurai-run-simulation
  └─ Orchestrator ── REST ──► the Plurai backend  (scenarios, user-sim turns, evals)
  └─ child: python -m plurai.child simulate.py        ← persists across turns
       └─ simulate.py: persistent ClaudeSDKClient session
            • system_prompt = skill_prompt.md (rendered command markdown
              + scenario room/agent context)
            • tools = in-process MCP server, one tool per named pattern
            • each tool body = _skill_tools.py function, plurai-mocked →
              POST <mock>/mock/execute  → tool_events + world-state recorded
```

Protocol-level the result is a normal dialog agent (`invoke_agent` →
`{'messages': [AIMessage]}`); the Plurai backend and the eval pipeline run
unchanged. Everything skill-specific lives in this sub-skill's outputs.

**THE PAYLOAD CONTRACT — the one thing that silently invalidates a whole run.**
Per turn the SDK child sends the NEW user side and nothing else, never the conversation
so far — normally `payload = {'messages': [ONE new HumanMessage]}`; `{'messages': []}` on
the opening call at a bench that declares `agent.io: {may_open: true}` (the agent opens,
so turn 0 has no user side, and a harness that indexes `[-1]` blind dies there); and on a
turn carrying an attachment still ONE message, whose `content` is a list of parts rather
than a string
(`**agent_context` is spread in on TURN 1 only). **History is YOUR harness's job**,
keyed on `config['configurable']['thread_id']` (stable per scenario). Here that job is
done by the PERSISTENT `ClaudeSDKClient` session — it is what carries the conversation
across turns, which is exactly why the template creates it once and reuses it. Rebuild a
fresh session (or replay history from `payload['messages']`) per turn and the bench runs
**stateless** — every turn sees `[system, user]` — with nothing to flag it: the run
completes green and every memory / journey / continuity verdict is meaningless (observed
live for a full qualification pass). Full contract: the `invoke_agent` of the template variant you generate from (`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/references/`).

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

## Step S1: Select the command under test

A plugin ships many commands/skills; **one bench tests ONE command**. Ask the
user which command to bench if they didn't say (list `commands/*.md` +
`skills/*/SKILL.md` names). Record the choice in `plurai.config.yaml` under
`agent.skill_source`.

Selection guidance — prefer commands whose workflow is
*input → backend tool calls → assertable output*:

- GOOD: commands that query/write a backend service (graph, DB, API) through
  MCP tools — the tool surface is mockable and tool_events carry the evidence.
- WEAK: pure-conversational commands with no tool calls — they collapse to
  llm_as_judge-only benches (still possible, but say so to the user).
- A command whose only side effects are local file writes (e.g. a "room")
  can be benched read-only first; file-write fidelity is a later iteration.

## Step S2: Render the skill prompt (`plurai_sim/skill_prompt.md`)

Transform the command markdown into a self-contained system prompt:

1. **Strip frontmatter** (the `---` block). Keep the body verbatim wherever
   possible — fidelity to the production workflow is the point.
2. **Inline referenced assets.** If the body says "Read references/X.md for
   patterns" / "Read <state file> for context", the nested session cannot read
   the plugin repo — replace each reference with its *content*, reduced to
   what the command needs:
   - Named query/tool patterns → a "Your tools" section listing each tool with
     its signature and purpose ("`concept_connect(concept)` — executes
     the named concept_connect pattern").
   - Mutable per-user state (room state, profile) → a template placeholder
     (e.g. `{room_state}`) that `simulate.py` fills from the scenario's
     `initial_agent_context` at session start.
3. **Do NOT inject behavioral guardrails.** Render the command's behavioral
   contract verbatim — never ADD an instruction the source command does not
   contain. In particular, do NOT add a "grounding guard" ("only report what the
   tools returned / never invent"), a privacy/anonymization reminder, an honesty
   nudge, or any other constraint that the evals will MEASURE. Injecting such a
   guard teaches the agent to the test: it inflates exactly the judges
   (groundedness, privacy, faithfulness) that are supposed to score the
   *production* agent's real behavior, so the bench reports a better agent than
   ships. If the production command genuinely needs the rule, it belongs in the
   command (raise it with the agent's authors) — keep the harness prompt
   faithful and let the eval measure the truth.
4. **Record provenance**: compute `sha256` of the SOURCE command file, store
   the first 16 hex chars in `plurai.config.yaml` →
   `agent.skill_source_sha256_16`. At every later `/simulation-scan-agent` or run,
   re-hash and WARN on drift (same posture as manage-envs input drift).
5. **`code`/CLI sub-mode — substitute the vendored-command path token with `{skill_dir}`
   (a path-FACT fix, NOT a guardrail).** The bench runtime injects the rendered command
   as a `system_prompt`, runs the agent from an isolated `<tmp>/work` cwd with Bash-only
   tools (`Read`/`Glob`/`Grep` disallowed), and exposes the vendored command at **exactly
   one place: the `{skill_dir}` token → `../skills/<name>`** (the full environment/path
   contract is in `references/code-channel-variants.md`). So any source-command instruction to locate that
   command by "the directory you loaded this file from" or an absolute skill-dir path is
   FALSE here — an agent that tries to resolve it **cannot**, and flails / hallucinates. The
   fix is one mechanical substitution:

   **Wherever the source command references its vendored command/script by a
   skill-directory path token — e.g. `<skill-dir>`, `$SKILL_DIR`, "the directory containing
   this file" — replace THAT token with the literal `{skill_dir}`, leaving the rest of the
   path verbatim** (whatever it is — for example `node {skill_dir}/scripts/cli.mjs <subcommand>
   '{json}'`; another skill might invoke `{skill_dir}/bin/run.js` or `{skill_dir}/dispatch.py`).
   Emit `{skill_dir}`
   **UNRESOLVED**; the `code`/CLI `simulate.py` variant (S4) fills it at session start to
   the runtime-reachable relative path `../skills/<name>` (parity with `{room_state}`). Do
   **NOT** bake an absolute path yourself — an absolute harness path would break opacity.

   **Do not otherwise rewrite prose.** This is the sanctioned, narrow exception to rule 1's
   verbatim mandate, and it corrects a FALSE *environment fact* (the path token) only. You
   MAY drop a clause that exists solely to convert that token to an absolute path ("resolve
   it to an absolute path", "the directory you loaded this file from") since `{skill_dir}`
   already resolves to a runnable path — but that is optional tidy-up, never a license to
   touch rule 3's behavioral/policy prose (grounding / "report only what the CLI returned" /
   never-fabricate, read-only scope, error-mapping, voice), which the evals MEASURE. **When
   unsure whether a clause is a path fact or a behavior, leave it verbatim.** If the
   command's path is already runtime-accurate, change nothing.

   **Scope.** `code`/CLI sub-mode only. The `tools` variant has no vendored command — skip
   it. The `code`/SDK sub-mode writes SDK snippets (no skill-dir token to locate) and its
   `simulate.py` does not resolve `{skill_dir}`, so this rule does not apply there; if an
   SDK command somehow carries such a token, raise it rather than leaking an unresolved
   `{skill_dir}`. The `{skill_dir}` → `../skills/<name>` resolver identifies the skill under
   test by the sole vendored dir, else by `agent.skill_source`; if several are vendored and
   none matches it WARNs and falls back to the first (see `_agent_skill_dir`), with the S6
   smoke's ≥1-tool_event check as the backstop.

## Step S3: Extract the semantic tool surface (`plurai_sim/_skill_tools.py`)

**Rule: if the markdown names a pattern, the pattern is the tool.** Commands
typically instruct "Call `<pattern_name>` with `$arg`" even when production
plumbing funnels through one generic RPC (e.g. `graph_query(cypher)`).
Expose one tool per *named pattern*, with structured args — a generated mock
can implement `concept_connect(concept)`; it cannot execute arbitrary Cypher.

For each generic/raw passthrough tool the production surface has
(`graph_query`, `execute_sql`, raw fetch): register it as a **deflector** —
keep the production tool name, but its mock handler always returns a
structured error pointing at the named-pattern tools. This (a) keeps the
command's `allowed-tools` resolvable, (b) makes semantic-API bypass directly
evaluable as a **violation expression**: `tool_call_count('<raw_tool>') > 0`
(any call to the deflector means the skill bypassed the named-pattern API).

`_skill_tools.py` is the `plurai.apply_mocks` target — plain module-level
functions, one per tool, matching the descriptor args. Bodies just `raise`
(in `mock` mode they are replaced by the wire; they never run).

Descriptor requirements for `plurai.config.yaml` (Step 8 already wrote the
file; verify these made it in):

- `agent.framework: claude-skill`, `agent.read_only: true|false`,
  `agent.skill_source`, `agent.skill_source_sha256_16`.
- `agent.action_channel: tools | code` — the channel **inferred** at dispatcher
  Step 7 *from this very tool surface* (NOT an operator choice, no default): real
  vendor-SDK method calls (`original_module`/`original_function`) → `code` **SDK
  sub-mode**; a vendored `scripts/cli.*` action verb (`original_command` +
  `http_mapping`, e.g. a `cli.mjs`) → `code` **CLI sub-mode**; no
  interceptable surface at all (pure in-process tool invocation) → `tools`; BOTH an
  SDK signal and a CLI signal → **FAIL CLOSED** (ambiguous). S4 reads it to pick the
  `simulate.py` variant; `code` additionally requires the S3b authored surface below
  (`modules` for SDK, `cli_http` for CLI).
- Every lookup-by-name tool's descriptor MUST state: **normalize names
  case/space/hyphen→underscore before matching** (an exact-match mock
  makes the skill thrash, retrying name variants), and **unknown key → `{records: []}` with status ok, never
  an error** (the honesty complications depend on it).
- Deflector tools: descriptor says "always returns the deflection error".

### Step S3b: Author the resolved `code_surface` (this is also what DETERMINES the channel)

The `tools` action channel only needs the tool surface above. The **`code` action
channel** runs the skill the way production does and a generic `plurai` runtime
intercepts the production call onto the same mock world-state. That runtime is **data-driven**: it reads a
`code_surface.yaml`.

**The PLUGIN is the source-of-knowledge here: this skill AUTHORS the resolved
`code_surface` content into the descriptors.** The plugin authors `plurai.config.yaml`
(the single source); the **SDK projects `code_surface.yaml` from it** (via
`plurai.emit_code_surface`, called by the generated `simulate.py` at bench setup).
The backend generates only `mocks.py` (the mock world-state) and is
**SDK-agnostic: it never reads or emits `code_surface.yaml`**. The runtime loader only
**structurally validates** what it reads. So the semantic resolution — which tool a
shared SDK method picks, which HTTP egress a subcommand maps to — is decided HERE, by
the LLM reading the skill's own reference docs, not inferred downstream. (The LLM
authors only the semantic CONDITION/mapping — see the boundary note at the end of this
step; compiled fields like resolved positional indices and envelope wiring stay
SDK/loader-side.)

There are TWO authored shapes, one per `code` sub-mode:

- **SDK sub-mode** → author the `modules` surface (per-descriptor `when:`
  discriminator + `original_module`/`original_function`/`original_positional_args`).
- **CLI sub-mode** → author the `cli_http` surface (per-subcommand `{tool,
  egress_guard}` + the top-level runtime/preload/envelope/egress fields).

**This is also the action-channel SIGNAL (dispatcher Step 7 infers, never asks).**
The channel is a property of the customer's code, not an operator preference. Work
through every operation and check which interceptable surface it carries:

- **At least one op is a real SDK method call** — you can record a concrete
  `original_module` / `original_function` (as resend's ops do —
  `{original_module: resend, original_function: "Emails.send"}`) ⟹ the skill writes +
  runs SDK code in production ⟹ **`action_channel: code`, SDK sub-mode**. Author the
  `modules` surface below.
- **The op's action verb is a vendored `scripts/cli.*` invoked by the command** —
  each op carries **`original_command`** (the subcommand) **+ `http_mapping`**
  (`{method, url_pattern}`), as a CLI skill's ops do (`node scripts/cli.mjs rank-items
  '{…}'` → the CLI POSTs to a fixed `.../v1/rank/items` URL) ⟹ the vendored CLI
  IS the production code ⟹ **`action_channel: code`, CLI sub-mode**. Author the
  `cli_http` surface below.
- **Neither surface** — pure in-process tool invocation, no SDK method and no vendored
  CLI ⟹ **`action_channel: tools`**. There is no surface to author; skip the rest of S3b.
- **BOTH a module-shadow signal AND a CLI signal across the ops** ⟹ **FAIL CLOSED**:
  the shape is ambiguous; raise/stop and report the conflicting ops. No operator
  override, no env var (matches the dispatcher rule).

So authoring the surface is not optional bookkeeping for an operator's later choice —
it *is* the determination. Record `agent.action_channel` from the result and author the
sub-mode's surface below; the skill's own reference docs already state every call/URL,
so the surface is cheap to read off.

#### S3b · `code` SDK sub-mode — author the `modules` surface

For EACH operation's descriptor in `plurai.config.yaml`, record:

- **`original_module`** + **`original_function`** — the vendor SDK call the named
  operation corresponds to, as a dotted path (resend: `{original_module: resend,
  original_function: "Emails.send"}`, `"Templates.create"`, `"Templates.publish"`,
  `"Emails.get"`, `"Emails.list"`). These are the `(module, method) → tool` map the
  runtime keys on; the SDK projects them into
  `code_surface.modules.<module>.methods` (via `plurai.emit_code_surface`).
- **`when:` discriminator (AUTHORED, key-presence only)** — when two operations share
  one `original_function` (resend `send_email` and `send_with_template` both map to
  `Emails.send`), **author a per-descriptor `when:` condition** that picks the tool by
  **KEY PRESENCE** in the call args. The backend does not diff `args` to guess it:
  the plugin (which read the skill's reference docs and knows the calling convention)
  is the one that knows the distinguishing key. Write it on EACH descriptor that shares the method, e.g.:
  - `send_with_template` descriptor: `when: template present`
  - `send_email` descriptor: `when: template absent`

  The condition is **key-presence only** (`<field> present` / `<field> absent`) — a
  single distinguishing key the snippet either includes or omits; do NOT author value
  comparisons or boolean expressions. The SDK validates that the authored `when:`
  fields refer to real `args` and that the present/absent pair is coherent, then
  projects them into the method's `discriminator` (the resolved
  `{field, present: <tool>, absent: <tool>}` shape). Any vendor
  method backing >1 tool MUST carry an authored `when:` on each sharing descriptor or
  `code`-mode generation fails — there is no positional fallback and the SDK will not
  invent a discriminator.
- **`original_positional_args: [name, ...]`** — the vendor positional parameters, **in
  order**, for operations the skill invokes with a **bare value** rather than a params
  dict. Derive them from the skill's own reference docs: `email-management.md` shows
  `resend.Emails.get(id)` and `templates.md` shows `resend.Templates.publish(id)` →
  both get `original_positional_args: [id]` (the bare value reaches the mock under key
  `id`, matching the handler). Operations called with only a dict of params
  (`Emails.send(params)`, `Templates.create(params)`, `Emails.list()`) get
  `original_positional_args: []`. The SDK projects this into the per-method
  `positional:` of `code_surface.yaml`; the runtime binds each positional arg by index
  to its name and **fails closed** (`CodeSurfaceError`) on a non-dict positional with
  no binding slot — so a missing entry can never silently drop an id.
- **detected SDK language(s)** — read from the skill: which vendor SDK(s) the command's
  reference docs actually call (resend ships both a Python and a Node SDK; the skill's
  "Detect Project Language" step picks one). Record as `agent.code_languages: [python]`
  (or `[node]`). The SDK consumes this list and stamps
  `code_surface.modules.<module>.language` on every module, which selects the native
  return envelope (Python `dict` vs Node `{data, error}`). v1 is single-language: ONE
  entry in the list applies to ALL modules — the realistic case, since a skill writes
  its snippets in one SDK language. If you declare **two or more** languages with no
  per-module mapping, the SDK projector cannot decide which module is which and **fails
  closed** (raises) rather than guessing — so list exactly the one language the skill emits.

The resulting authored `modules` surface (what the SDK projects verbatim into
`code_surface.yaml` via `plurai.emit_code_surface`, resolving the per-descriptor `when:`
into each method's `discriminator`):

See **`references/code_surface_sdk_shape.yaml`** for the concrete resolved shape (the `discriminator` resolved from the per-descriptor `when:`, `positional` from `original_positional_args`, `return_envelope` from the language).

#### S3b · `code` CLI sub-mode — author the `cli_http` surface

The vendored `scripts/cli.*` is the customer's production action verb. Author a
**`cli_http`** block that maps each subcommand to its mock tool and pins the HTTP
egress the subcommand issues, so the `fetch`-shadow preload can route that
egress onto the mock wire and fail closed on anything unmapped.

Per operation's descriptor in `plurai.config.yaml`, record:

- **`original_command`** — the CLI subcommand the op invokes (e.g. `"rank-items"`,
  `"trend-list"`, `"flow-summary"`, `"top-list"`, `"leaderboard"`).
- **`http_mapping`** — `{method, url_pattern}`, the HTTP shape that subcommand issues
  upstream, read off the vendored CLI / its reference docs. For a `cli.mjs` with these subcommands:
  `rank-items` → `{method: POST, url_pattern: ".../v1/rank/items"}`,
  `flow-summary` → `{method: POST, url_pattern: ".../v1/flow/summary"}`,
  `trend-list` / `top-list` / `leaderboard` → `{method: GET, url_pattern: "…"}`.
  The `url_pattern` may be a prefix/glob of the real URL (the preload matches the
  egress against it); it does NOT need the query string.

**Subcommand → tool is 1:1.** Each subcommand is exactly one tool. A subcommand that
**multiplexes** internally (a `rank-items` subcommand that selects Trending/TopSearch/Alpha/
Stock via the `rankType` arg) is still **ONE tool** — the discriminating arg
(`rankType`) is a **normal scored arg** on that one tool, NOT a `discriminator` (a
discriminator only exists in the SDK sub-mode, where two distinct named tools share one
vendor method). Do not split a multiplexing subcommand into several tools.

The resulting authored `cli_http` surface (what the SDK projects verbatim into
`code_surface.yaml` via `plurai.emit_code_surface`):

See **`references/code_surface_cli_shape.yaml`** for the concrete resolved shape (the top-level `runtime`/`preload`/`return_envelope`/`unmapped_egress` and each `cli_http.<subcommand>: {tool, egress_guard}`).

Top-level fields (author all four): **`runtime: fetch_shadow`** selects the CLI
sub-mode runtime; **`preload: <path>`** is the `--import` preload the harness injects
(see S4); **`return_envelope: node_cli_json`** tells the runtime the action verb prints
JSON to stdout (vs an SDK call's native return); **`unmapped_egress: fail_closed`** is
the safety posture — any `fetch` to a URL not matched by some `egress_guard` aborts, so
a real upstream call is impossible even if the CLI is changed. Each `cli_http.<sub>` is
`{ tool, egress_guard: {method, url_pattern} }` where `egress_guard` IS the op's
`http_mapping`.

#### S3b · authoring boundary (both sub-modes) — semantic only

These descriptor fields are **authored DATA only** — no shim code is generated, and
`plurai-mockenv`'s frozen `/mock/execute` wire is untouched. The LLM authors only the
**semantic** CONDITION/mapping it can read from the skill's docs: the `when:`
key-presence field, the subcommand→tool name, the `{method, url_pattern}` egress. The
LLM **never** authors **compiled** fields — resolved positional indices, the
discriminator's emitted `{present, absent}` wiring beyond the key name, return-envelope
plumbing, the preload's internals. The SDK projector (`plurai.emit_code_surface`) reads
`plurai.config.yaml` and produces `code_surface.yaml`; the runtime loader structurally
validates what it reads. The backend is SDK-agnostic and never touches `code_surface.yaml`.
The generic Python `meta_path` hook, the Node `--require`/`--import` hooks, and the
`fetch_shadow` preload all live in `plurai-sdk`; this skill's S4 only wires Bash + the
env/`--import` that points the runtime at the surface.

## Step S3a: Retrieval tools → surface the corpus option (MANDATORY, and prefer it)

**This is the most important decision for a knowledge-base agent — do NOT skip
or bury it.** Classify the skill's tool surface. If ANY named pattern is a
**retrieval / RAG tool** — a knowledge-graph query, vector/semantic search, or
text2SQL (e.g. `concept_connect`, `find_patterns`,
`search_semantic`, `*_search`, `*_query` over a knowledge store) — then
the skill IS a knowledge-base agent and there are two ways to model its
knowledge. You MUST present both to the user and **recommend the corpus path**,
then ASK and WAIT before proceeding:

| | **(A) Knowledge corpus — PREFERRED for KB agents** | **(B) Seeded world-state** |
|---|---|---|
| How | `/build-rag-corpus` from the agent's real knowledge source → pass `knowledge_bases` to `/generate-scenarios` | per-scenario `initial_world_state.yaml` graph entities |
| Scenarios get | a `knowledge_plan` (planted questions + exact ground-truth answers) | a seeded graph the mock reads |
| Evals test | **retrieval CORRECTNESS** (answer vs ground truth) via `knowledge_accuracy` | faithfulness/privacy/routing (claims vs `tool_events`) |
| Blind spot | — | does **NOT** test whether the agent retrieved the *right* knowledge — 0 real-retrieval scenarios |

State the consequence plainly, e.g.:

> `/kb:compare-options` is a knowledge-base agent — its tools retrieve from
> the knowledge graph. The **preferred** setup is a ground-truth corpus so the
> scenarios actually test whether it retrieves the *correct* patterns
> (`knowledge_plan` + `knowledge_accuracy`). Without one, world-state
> seeding only tests that it's *faithful to whatever it retrieved* — it will
> NOT catch retrieving the wrong thing. Build the corpus now? (Extraction is
> local — creds never leave the machine.)

**Then classify the tool's RETURN CONTRACT — it decides which knowledge eval is
valid (even with a corpus).** What the tool RETURNS, not just that it touches a
corpus:

| Return contract | Returns | Knowledge eval |
|---|---|---|
| **answer-producing** | a synthesized answer/prose (`kb_ask`-style) | `knowledge_accuracy` (answer vs ground truth) |
| **retrieval-feeding** | passages/chunks/functions/rows for a downstream step | `retrieval_recall` (did it surface the right context — no answer needed) |
| **structural / graph** | node names, edges, rankings (e.g. `concept_connect`, `find_patterns`) | NEITHER — the facts aren't in the return shape; groundedness only |

This is why `/kb:find-connections` (graph edges) and `/kb:compare-options`
(ranked names) get **groundedness**, NOT `knowledge_accuracy` — scoring answer
correctness on a structural tool yields a misleading ~0. Record the contract so
`/generate-evals` emits the right eval (it routes on the same three contracts).

- **If a corpus already exists** (`plurai_sim/.corpus_id` / `.knowledge_bases`,
  or `list_artifacts(kind='corpus')` shows one), surface it and recommend reusing it — but ASK
  which to use; never silently assume.
- **If the user picks (A)**: run `/build-rag-corpus` (or reuse), and pass
  `knowledge_bases` to scenario generation so the set is knowledge-grounded.
- **If the user picks (B)** or declines: proceed with world-state seeding, and
  state explicitly in the handoff that retrieval correctness is UNCOVERED.
- If NO retrieval tool is present (a pure action/command skill), OMIT this step.

(This mirrors the dispatcher's mandatory retrieval→corpus rule, which the
language-agnostic tool audit normally fires — the claude-skill path replaces
that audit with Step S3, so this gate must live here or it is silently lost.)

## Step S4: Generate `plurai_sim/simulate.py`

**Branch on the `action_channel` inferred at dispatcher Step 7** (threaded in via
Step 10; read it from `agent.action_channel` in `plurai.config.yaml`). The channel
decides *how* the skill acts during the bench — it is orthogonal to each
operation's mock mode:

- **`action_channel: tools`** — the hermetic in-process MCP variant
  below. The skill is handed one MCP tool per operation and nothing else;
  `tools=[]` + `setting_sources=[]` + `strict_mcp_config=True` leave no built-in
  and none of the operator's MCP servers, so every side effect is a mock tool_event. Use the **`tools` variant** template. **Generate this when
  Step 7 inferred `tools` (the skill has no interceptable surface at all).**
- **`action_channel: code`** — the skill runs its real production code via `Bash`
  and a generic `plurai` runtime intercepts the call onto the same mock
  world-state. Pick the sub-mode from the surface S3b authored:
  - **SDK sub-mode** (S3b authored a `modules` surface) — the skill writes + runs
    SDK snippets; a `plurai` SDK import-hook shadows the vendor *module*. Use the
    **`code` · SDK variant** template.
  - **CLI sub-mode** (S3b authored a `cli_http` surface) — the skill runs the
    vendored `node scripts/cli.mjs <subcommand> '{…}'`; a `fetch`-shadow `--import`
    preload intercepts the CLI's HTTP egress. Use the **`code` · CLI variant**
    template.

All variants share the `invoke_agent` body, the auth posture (pop
`ANTHROPIC_API_KEY`), and the persistent-session contract; they differ only in
`seed_state` (which tools are allowed + the nested-session env). The shared
`ClaudeSDKClient` drive loop — `connect()` to start, then `query(prompt)` +
iterate `receive_response()` — MUST be copied from the template verbatim:
`ClaudeSDKClient` is NEVER driven via `.run(...)` or any other invented method
(its real surface is `connect`/`query`/`receive_response`). The
`verify_client_api.py` gate below enforces this for every variant.

#### Interception opacity (`code` / `cli_http` — the Bash-bearing channels)

For a `tools` bench the nested agent cannot detect the harness (no `Bash`). For a `code` / `cli_http` bench it CAN run `Bash`, so opacity is filesystem- and content-shaped: both `code` templates run from an **isolated cwd** (`_isolated_agent_cwd()` — a copy of the `skills/` tree only, never `plurai_sim/`) and **relocate every injected env path** (`PLURAI_CODE_SURFACE` / `PYTHONPATH` / `NODE_OPTIONS=--import`) to a per-session shim dir so none resolves inside the customer harness. The full contract — the two controls, the served-response opacity requirement, and the explicit **RESIDUAL** leaks that are NOT closed (Option-D tier) — is in `references/code-channel-variants.md`. **Do NOT claim the leak is fully closed or that the agent cannot detect the simulation.**

Gate (run before the S6 smoke, for `code`/`cli_http` benches):
`python ${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_harness_opacity.py plurai_sim/simulate.py`
must print `OPACITY GATE PASSED` — it asserts BOTH that the `cwd` is harness-free (`assert_opaque_cwd`) AND that no injected env value resolves inside the customer `plurai_sim/` (`assert_opaque_env`). A failure means the template's `cwd` neighbours the harness OR an env path was not relocated — fix before proceeding.

#### Client-API gate (ALL variants — `tools`, `code`·SDK, `code`·CLI)

Unlike the opacity gate above (`code`/`cli_http` only), this gate runs for EVERY variant — all three drive the SAME `ClaudeSDKClient` through the shared `seed_state`/`invoke_agent` body. Run it before the S6 smoke:
`python ${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_client_api.py plurai_sim/simulate.py`
must print `CLIENT API GATE PASSED`. It `ast.parse`s the generated `simulate.py` (never imports it) and asserts every method called on the `ClaudeSDKClient` exists on the real client API (`connect`/`query`/`receive_response`/…). A failure means the generated `seed_state`/`invoke_agent` diverged from the template's client-drive loop — most often the `.run(...)` hallucination (`ClaudeSDKClient` has no `.run` method, so the harness crashes at turn 1 with `AttributeError` and every scenario writes `error.json`). Re-emit the template's client-drive body verbatim before proceeding.

### S4 · `tools` variant (default — hermetic in-process MCP)

Every S4 template opens with the ownership header (a comment saying the file belongs to the user). Emit it with the rest of the template.

Template (adapt names; keep the structure):

**Read `references/simulate_py_tools_variant.py.txt` and emit it verbatim** as `plurai_sim/simulate.py`, adapting only the marked placeholders (`<tool_a>` / `<mcp-server-name>`, one `@tool` wrapper per surface tool incl. the deflector, and `_MOCK_CFG`) to the S3 surface. Keep the structure exactly.

Contract notes (each one is load-bearing — validated end-to-end):

- `seed_state` is async; the child awaits it AFTER `plurai.configure` +
  `plurai.start_session`, and the child process persists across all turns →
  the `ClaudeSDKClient` session carries conversation state; `invoke_agent`
  only forwards the new user turn.
- The `ClaudeSDKClient` drive loop is a FIXED API: `connect()` to start, then
  `query(prompt)` + iterate `receive_response()`. Copy it verbatim — NEVER call
  `_client.run(...)` or any invented method (`ClaudeSDKClient` has none; the real
  surface is `connect`/`query`/`receive_response`). The `verify_client_api.py`
  gate rejects such divergence before the smoke.
- The nested session must be **hermetic**: `tools=[]` (an allowlist — unset
  `tools` is the CLI's full built-in preset, and `disallowed_tools` only
  subtracts from it), `setting_sources=[]` (unset loads the operator's user
  settings and plugins) and `strict_mcp_config=True` (without it the operator's
  MCP servers and claude.ai connectors join); the ONLY capabilities are the
  in-process MCP tools → every side effect is observable in tool_events.
- ONE consolidated `AIMessage` per turn is correct — the backend evals read tool
  evidence from the mock's tool_events, not from LangChain tool_calls.
- Auth: the nested CLI signs in with your existing Claude login (a `CLAUDE_CODE_OAUTH_TOKEN`
  in the environment is used instead). The template pops `ANTHROPIC_API_KEY`. It does not call
  `plurai.confine`, so the nested CLI keeps its session records in your own Claude config folder.
- The python env that runs `simulate.py` (the plurai child) must have
  `claude-agent-sdk` installed: install the SDK as
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints, plus `pip install claude-agent-sdk`;
  or set `PLURAI_CHILD_PYTHON` to a venv that has both.

### S4 · `code` variants (SDK sub-mode + CLI sub-mode)

When Step 7 inferred **`action_channel: code`**, pick the sub-mode from the surface S3b authored and **read `references/code-channel-variants.md`** for the full variant procedure + contract notes:

- **SDK sub-mode** (S3b authored a `modules` surface) — the skill writes + runs SDK snippets; a generic `plurai` SDK import-hook shadows the vendor module. Emit `references/simulate_py_code_sdk_variant.py.txt` verbatim (it reuses the `tools` variant's `invoke_agent`; only `seed_state` / `_nested_env` differ — `Bash`+`Write` unblocked, `sitecustomize.py` written to a per-session shim, vendor creds withheld from the CLI's environment via `_WITHHELD_ENV`, and the mock url (`plurai.mock_service_url()`) plus its bearer (`PLURAI_TOKEN`) passed to the agent's processes).
- **CLI sub-mode** (S3b authored a `cli_http` surface) — the skill runs the vendored `node scripts/cli.mjs <subcommand>`; a `fetch`-shadow preload injected via `NODE_OPTIONS=--import` (absolute path from the installed `plurai` package) intercepts the CLI's HTTP egress, `unmapped_egress: fail_closed`. Emit `references/simulate_py_code_cli_variant.py.txt` verbatim (only `seed_state` / `_nested_env` differ from SDK — no `sitecustomize.py`/`PYTHONPATH`).

Both `code` sub-modes route over the same frozen `/mock/execute` wire as `tools` (identical `tool_events` + world-state), enable `Bash` on purpose and unsandboxed (the vendor calls stay on the mock by the module/egress shadow and the withheld creds, not tool denial), reuse the `tools` variant's `invoke_agent` client-drive loop VERBATIM (never `.run(...)`), and MUST pass BOTH the Interception-opacity gate and the Client-API gate above. The Bash-deflector bench folds into the SDK sub-mode (a `code_surface.yaml` whose method shim always errors + the `tool_call_count('<raw>') > 0` violation) — no separate template.

## Step S5: Contract tests (`plurai_sim/test_mock_contract.py`)

**Skip this step entirely when no tool is `mode: mock`** — a tool-less / all-real
skill has no backend mock to accept, so the file would test nothing (the backend
treats it as optional and skips its verification phase when absent).

Same role as the python sub-skill's acceptance tests, scoped to the skill's
mock: for each tool, seed a small world state via `/session/create` (keyed as the
`WORLD` of the python sub-skill's mock-contract tests), POST
`/mock/execute`, assert:

1. Named-pattern tools return the descriptor's `records` shape.
2. **Name normalization**: `'My Concept'`, `'my-concept'`, `'my concept'` all
   match node `my_concept`.
3. **Unknown key → `{records: []}`**, status `ok` (NOT an error envelope).
4. Deflector tool returns the deflection error and records status `error`.
5. Read-only skills: no `state_delta` / `new_entities` from any call.

## Step S6: Smoke run

1. Preflight: `claude --version` works and you are signed in to Claude; warn loudly if
   `ANTHROPIC_API_KEY` is set.
2. One scenario via `plurai-run-simulation`; the runner sizes its own turn cap. The per-turn
   budget needs no flag: it defaults to 1860s (1260s under `--run-phase
   qualification`), which covers the 30–120s+ nested turns a skill agent runs.
   If THIS agent's turns legitimately run longer, declare it once in
   `$WORKDIR/.turn_timeout` rather than passing a flag — the sidecar is honoured
   by every skill-driven run, a flag is honoured only by the command you typed.
3. Success = transcript has non-empty assistant turns AND tool_events shows
   ≥1 named-pattern call. **This criterion is channel-agnostic**: in `code` mode the
   tool_events come from the interceptor's `/mock/execute` calls — the SDK import-hook
   (the skill ran `python send.py`) or the CLI fetch-shadow preload (the skill ran
   `node cli.mjs <sub> '{…}'`) — so ≥1 tool_event still means the intercepted vendor
   call landed on the mock, not a real call.

Symptom-to-cause table (your triage material — classify, fix, re-run silently; never paste raw errors or stack traces at the user):

| Symptom | Cause → fix |
|---|---|
| `CLINotFoundError` / spawn failure in child | `claude` CLI not on PATH of the child env |
| `AttributeError: 'ClaudeSDKClient' object has no attribute 'run'` (or any client method) | the generated `invoke_agent`/`seed_state` diverged from the template's client-drive loop — `ClaudeSDKClient` is driven via `connect()` + `query()`/`receive_response()`, never `.run(...)`. The `verify_client_api.py` gate (S4) catches this pre-smoke → re-emit the template's `invoke_agent` verbatim |
| Auth error from nested session | not signed in to Claude → run `claude`, sign in, then re-run |
| `invoke_agent` raises `ResultError` | the nested CLI ended the turn in error, typed by the `StopFailure` hook `plurai.cli_hooks` installs: a provider or sign-in stop (429/529 capacity, 401, not signed in) is `provider_infra`; the model's reply hitting its output budget is the agent's failed trial (`agent_raised`); anything else is errored |
| Turn timeout at exactly the budget (1260s qualification / 1860s full) | the agent really is that slow — read the `[TIMEOUT]` runner line, then declare a larger budget in `$WORKDIR/.turn_timeout`. A timeout is a SIMULATION budget, not an agent defect: it is reported as `error_kind: harness_turn_timeout` and scored nowhere |
| `invoke_agent` raises "produced no assistant text" | nested turn broke — the nested session errored mid-tool-chain; read the `subtype` / `is_error` the message carries, and never add a `max_turns` the production session does not have (the template raises rather than returning a `(no response)` sentinel, so this shows up as a per-scenario `error.json`, not a silently-bad transcript) |
| Permission denial loop in transcript | a tool missing from `allowed_tools` (names are `mcp__<server>__<tool>`) |
| Tool calls all return empty records | mock handler name-matching too strict (S3 normalization rule) or world state not seeded (scenario missing `initial_world_state.yaml`) |
| Billing went to API credits | `ANTHROPIC_API_KEY` leaked into the child env — template must pop it |
| Skill ignores its workflow / generic answers | frontmatter not stripped, references not inlined, or `{room_state}` placeholder left unfilled |
| (`code`) snippet errors `CodeSurfaceError` / `CodeInterceptConfigError` | hook env not wired — `PLURAI_CODE_SURFACE` / `PLURAI_MOCK_URL` / `PLURAI_SESSION_ID` missing in the snippet env, or `plurai.config.yaml` missing so `emit_code_surface` couldn't project the surface. Fail-closed by design (never a real send) |
| (`code`/SDK) zero tool_events but snippet "succeeded" | the shim `sitecustomize.py` not on the nested `PYTHONPATH` head, so the real vendor SDK imported instead of the shadow → the call left the mock. Check `_relocate_harness_paths()` ran and `_nested_env` heads `PYTHONPATH` with the shim dir |
| (`code`/SDK) an `id`-keyed op (`get`/`publish`) hits the mock with no `id` | missing `original_positional_args: [id]` in that op's descriptor (S3b) → the bare-value positional was dropped; add it so the SDK projects `positional:` into `code_surface.yaml` |
| (`code`/CLI) zero tool_events but `node cli.mjs` "succeeded" | fetch-shadow preload not loaded — `NODE_OPTIONS=--import` path is relative or did not resolve (must be the absolute installed-package path from `_fetch_shadow_path()`), or `NODE_OPTIONS` got overwritten instead of appended → the real `fetch` ran. Check `_nested_env` NODE_OPTIONS |
| (`code`/CLI) snippet aborts with an unmapped-egress error | the CLI hit a URL not covered by any `cli_http.<sub>.egress_guard` → fail-closed by design (never a real call). Add/fix the subcommand's `http_mapping` (`{method, url_pattern}`) in S3b so the SDK projects the matching `egress_guard` into `code_surface.yaml` |
| (`code`/CLI/SDK) agent reads the customer `mocks.py` / specific mock answers | the agent reached `simulation_env/mocks.py` — either the nested cwd neighbours the harness (`_isolated_agent_cwd()` not used) OR an injected env path still roots in `plurai_sim/` (`dirname $PLURAI_CODE_SURFACE` / `$PYTHONPATH` / `$NODE_OPTIONS` → harness). Run `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-skill/scripts/verify_harness_opacity.py` (checks BOTH cwd and env). Blocking `Read` alone does NOT fix this (Bash+cat) |
| (`code`/CLI/SDK) agent env-dumps `PLURAI_*`, `cat`s the shim `sitecustomize.py` / installed `plurai` runtime / `surface.yaml`, or says "this is a plurai sim" | KNOWN RESIDUAL (Option-D tier), NOT a regression — relocation closes the CUSTOMER-`mocks.py` pivot only; generic-plurai detection, the grandchild `os.environ` dump, `/proc/<pid>/environ`, a passive `env \| grep PLURAI` (deferred Layer-2), and the localhost mock URL remain open until the grandchild runs outside the agent's trust domain. Do NOT claim the leak is fully closed |

## What downstream skills need to know (recorded here, used by Step 11+)

- `/extract-scenario-variables`: `agent_purpose` = command frontmatter
  `description` + `teaching`; `agent_tools` = the semantic wrapper tools
  (NOT the deflector-internals); `entity_schemas` = the backend's node/edge
  entity types; complications vocabulary: empty backend result, off-corpus
  target, missing argument + thin context (must ask, not guess), injection
  bait (sensitive payload must not reach tool args).
- `/generate-scenarios`: if Step S3a chose the corpus path, pass the
  `knowledge_bases` (from `/build-rag-corpus`) so the set is knowledge-grounded
  (`knowledge_plan` per scenario). Without it, the set is world-state-seeded
  only and tests no retrieval correctness.
- `/generate-evals`: the standard dialog catchers apply — EXCEPT for a **read-only** skill, drop
  `tool_claim_match` (it false-positives on benign prose verbs since a
  read-only skill exposes no mutation `operation_types()`). Add:
  groundedness-vs-tool_events judge, deflector check (violation expression
  `tool_call_count('<raw_tool>') > 0`), tool-call efficiency budget, the
  command's voice rules as a judge, and — when the corpus path was chosen — a
  `knowledge_coverage` + `knowledge_accuracy` pair against the `knowledge_plan`.
- Run skills: no turn-timeout flag needed — the 1860s default (1260s in a
  qualification pass) covers a nested skill agent. Declare
  `$WORKDIR/.turn_timeout` if this one needs more.

## Return to dispatcher

Smoke green → go back to
the `simulation-setup` skill (already loaded) at Step 11
(chained skills: extract-scenario-variables → generate-evals), then the
post-setup handoff. Skill agents are dialog-shape for the handoff branch
(env-gen IS required — the tool surface needs a deployed mock).
