---
name: simulation-diagnose
user-invocable: false  # internal building block of /simulation-fixes; hidden from the / menu (still model-invocable, run by name from its driver)
description: >-
  Diagnose one already-failed run directory with error.json files, isolating a local cause (simulate.py, scenarios, config) from a remote one (mock service, seed data). Used by /simulation-fixes; not for a general run-and-diagnose request.
---

# Simulation Diagnose

**Note**: this skill is the REACTIVE half of the dual-agent flow. For the common case, run the simulation with `/simulation-run` then diagnose with `/simulation-fixes` — the latter reads the run's `error.json` files and invokes THIS skill on each errored scenario automatically. Use this skill DIRECTLY only when:
  - The customer already has an OLD failed run dir and wants to diagnose it standalone, or
  - You're being invoked as a sub-skill from `/simulation-fixes`.

You're the LOCAL Claude Code agent on the customer's machine. A backend simulation just failed and the customer wants help figuring out why. The failure could be in EITHER:

- **Local side** (your domain): `simulate.py`, scenarios, `plurai.config.yaml`, `policies_config.yaml`. You can read / edit these directly.
- **Remote side** (NOT your domain): the deployed mock service's filesystem — mock implementations, seeded data, descriptors. A **remote Claude agent** owns this and you collaborate with it through an MCP server.

The Plurai backend serves the MCP server `plurai`. You drive the conversation; the remote agent reads/patches mock-env files; together you converge on a fix.

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

## Preconditions

Before invoking this skill, confirm:

1. **`plurai` MCP server is reachable.** It auto-registers via the plugin manifest (`.claude-plugin/plugin.json` `mcpServers`) — no manual MCP registration needed. The backend is the configured one, the one the saved Plurai login names (a project `.env` is not read). Any remote backend needs that login. The server itself must be running. Listing the MCP servers shows whether the registration took effect; `plurai backend probe` probes reachability (it prints the HTTP code of `GET /healthz`, and only a 2xx means the server is up). With no Python runner installed (SDK not installed yet, or too old for `backend probe`), reachability cannot be probed from the shell: say so rather than guessing.

   **Recognition guidance — for YOU, never shown to the user.** If your first `diagnose_mock` call returns `Streamable HTTP error: ... "code":-32600,"message":"Session not found"`, the MCP client is holding a stale `mcp-session-id` (from a previous server lifetime, an earlier connection the server reaped, or a transport that dropped silently and reconnected with the cached id). Recognize and handle this **silently** — re-establish the session; never paste this error text into the chat. The server itself is usually fine — an `initialize` request with no `mcp-session-id` header should return `200 OK` and a fresh id in the response headers; the fastest probe is the `mcp` CLI or whatever raw HTTP client your environment provides. The fix is client-side: the MCP client must renegotiate, which needs a plugin reload — ask the user in plain product terms (e.g. "I need to reconnect to the diagnostic service — please reload the plugin via Claude Code's plugin-management UI, or restart Claude Code"), without quoting the error. Don't retry `diagnose_mock` immediately — the client cache won't refresh until the plugin reloads.

2. **A failed run dir exists.** Look for `error.json` files under `<out_dir>/<scenario>/`. If the user didn't point at a specific dir, search the most recent one under `/tmp/plurai*` or whatever they last invoked.

3. **The user hasn't fixed it themselves already.** Ask once if they want you to diagnose; don't auto-invoke on every failed scenario without confirmation.

## The flow

### Step 0: Local-side static check (BLOCKING gate before engaging remote)

The remote agent cannot read the customer's `simulate.py`, `_storage.py`, scenario YAMLs, or agent source. When the failure's root cause lives in local code (common: seed-state defects, storage-adapter wiring bugs, missing tool wrapping), the remote has no surface to read and will guess from priors.

**Run the static verifiers BEFORE engaging the remote agent.** They're cheap, deterministic, and catch the most common local-side causes in seconds:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py" plurai_sim/simulate.py
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_config.py" plurai_sim/plurai.config.yaml --simulate plurai_sim/simulate.py
```

Run them from `${CLAUDE_PLUGIN_ROOT}`, which is what makes the verdict current: this session may
open weeks after the bench was set up and several plugin versions later, and a verifier copied
into `plurai_sim/` back then still answers for the contract of that day.

If EITHER verifier exits 1 — a finding still holds; its `finding:` line names it — (exit 2 is no verdict: fix what it printed and re-run):

1. Read the violation code the verifier printed and the SKILL.md step it points at. The verifier output is the source of truth for the fix recipe — don't paraphrase or substitute a remembered rule name. An `advisory` finding the agent's repo shows is wrong is overruled instead, per `/simulation-setup-python` Step 9e.3, and the verifier re-run.
2. **Show the violations to the user** — each one's code, message and fix-recipe step; never a `finding:` line, a fingerprint or an overrule.
3. Offer to apply the fix; on user approval, edit the local file.
4. Re-run the failing scenario.
5. **A passing verifier is necessary but not sufficient.** If the scenario still fails after the verifier-suggested fix, re-run the verifier (the fix may have masked one violation and exposed another) — then engage the remote diagnostic protocol only if the verifier remains green AND the scenario still fails.

The gate exists because the remote agent's information surface is structurally incomplete for local-code bugs. Routing a locally-diagnosable bug to a remote that can't see local files leads to confident answers built on priors instead of grounded answers built on evidence.

### Step 1: Collect failure context

(Only reached when Step 0's verifiers exited zero and the scenario still fails — otherwise the bug was local and you've already fixed it.)

For ONE failed scenario — **the pick is decidable, not a matter of taste: if the
invocation named a scenario, use it; if exactly one scenario failed, use it; ONLY when the
invocation named none AND more than one failed do you ask** (inside a driving command's flow window, sanction the ask first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction which-scenario`; otherwise take the most recent failure and say which you took) — assemble a payload:

```yaml
scenario_name: <from the dir name>
error_class: <from error.json>
error_message: <from error.json>
transcript_excerpt: |
  # Last ~5 turns of <scenario>/transcript.json, ~2KB max.
tool_events_excerpt: |
  # Last ~30 events from <scenario>/tool_events.json, ~2KB max.
mock_service_url: <from the run command or plurai_sim/.mock_service_url — a resolved <base>/mock/<env_session_id>/ proxy URL>

# When transcript_excerpt is EMPTY (turn-1 crash, no checkpoint persisted),
# the remote agent is blind without these. Include them up-front so the
# remote isn't forced to guess from the error_message alone:
seed_state_excerpt: |
  # The seed_state function body from plurai_sim/simulate.py (~30 lines).
  # The remote can't read this file but its behaviour is often load-bearing
  # for turn-1 crashes.
initial_world_state_summary:
  entity_types: [...]           # keys from initial_world_state.yaml.entities
  revision_count: N             # how many revisions seeded
  record_count: M               # total records across revisions
  example_set_count: K
agent_context_keys: [...]       # field names from agent_context.yaml (NOT values)

# Required: state your OWN hypothesis. The remote evaluates yours rather than
# inventing one from incomplete data.
local_hypothesis: |
  One short paragraph: what YOU think went wrong, based on what YOU can read.
  Cite specific files and line numbers. Acceptable shapes:
  - "I can't tell from local artifacts; deferring to remote inspection"
    (the durable shape — honest uncertainty is the right answer when the
    local-side evidence is genuinely inconclusive)
  - "seed_state at simulate.py:<line> reads only from one of its two
     parameters; the other carries entity history that never reaches the mock"
  - "the agent's ReAct router has no fallback for an empty tool response;
     turn 1 retries the same tool until the recursion limit"
```

**The local_hypothesis field is required for turn-1 crashes.** Even "I don't know" is acceptable — what's not acceptable is sending zero local-side analysis and asking the remote to diagnose blind. The remote has been instructed to evaluate your hypothesis rather than invent one from scratch.

**Why this matters**: for turn-1 crashes the transcript is empty (the orchestrator raises before checkpointing). Without the seed_state excerpt, the initial_world_state summary, and your local hypothesis, the remote has only the error class to work with — and will fill the gap with plausible-sounding guesses that the local-side evidence would have ruled out.

### Step 2: Open the diagnostic session

**`diagnose_mock` sends `failure_context` (and a `message`) straight to the backend over MCP** —
both the initial turn and every follow-up. There is no plugin-side preview/consent hook for this
call; the harness's normal tool-approval prompt (naming the tool + its arguments) is the user's
consent chokepoint. Do not duplicate a disclosure in the chat panel — just make the call.

Per-call cadence matters regardless: each follow-up turn carries different context (re-run
transcripts, file excerpts), so if the user denies a tool-approval prompt, surface the denial
verbatim and stop. Do not retry. Do not inline the payload into another tool call to bypass approval.

Call the MCP tool:

```
diagnose_mock(
    message="Scenario `<name>` failed with `<error_class>`. Local-side observations: <one paragraph>.",
    failure_context={...},  # the YAML above as a dict
)
```

The response has:
- `session_id`: KEEP THIS — every follow-up call needs it.
- `response`: the remote agent's analysis. Show it to the user verbatim (don't summarize).
- `patches_applied`: files the remote agent edited THIS TURN. If non-empty, log them for the user (e.g., "Remote agent patched `mocks/get_transactions.py` to seed 3 rows.").
- `requested_action`: structured next step. If non-empty, you do this next (see Step 3).
- `status`:
  - `"ongoing"` → remote is still investigating; wait for the user's input or call `diagnose_mock` again with a follow-up observation.
  - `"awaiting_local"` → remote has requested an action (in `requested_action`); execute it and report back.
  - `"done"` → remote is finished; jump to Step 4.

### Step 3: Execute REQUESTED ACTIONs

The `requested_action` field is usually one of:

- **Re-run a scenario**: `"re-run scenario_03 against the mock and send the new transcript"`. Run `plurai-run-simulation --scenario-set-id <id> --scenario-id scenario_03 --out /tmp/diag-<n> …` (full command in the verification block below — `--scenario-id` is the per-scenario selector; no local folder needed) and read the result. Send back a new `diagnose_mock` call with the outcome. The harness's normal tool-approval prompt fires for every follow-up call by design — the user sees the new payload before approving.
  ```
  diagnose_mock(
      session_id=<saved>,
      message="Re-ran scenario_03. Still failing — error: <new error>. Tool events: <new excerpt>.",
  )
  ```
  Or if it passed: `"Re-ran scenario_03. Passed in 2 turns, no errors."`

- **Inspect a local file**: `"send me the contents of simulate.py around line 200"`. Read the file, send the relevant excerpt as a follow-up.

- **Try a local-side fix**: `"on the local side, change ... in simulate.py — can you apply that and re-run?"`. **VERIFY THE PROPOSED FIX BEFORE PROMOTING IT TO THE USER**, not after. The remote agent reasons from incomplete information and can confidently propose patches that don't actually address the root cause.

  **Verification procedure** (do this AS the local agent, before asking the user to accept):

  1. Create a scratch directory ONCE and remember the path for subsequent steps:
     ```bash
     SCRATCH=$(mktemp -d -t plurai-diag-verify.XXXXXX)
     ```
     `$$` does NOT work here — your Bash tool calls each spawn a fresh shell, so `$$` expands to a different PID per call. Capture `$SCRATCH` once and pass the literal path to each follow-up call.
  2. Copy the harness and the config it reads beside itself into scratch, and apply the proposed change there: `cp plurai_sim/simulate.py plurai_sim/plurai.config.yaml "$SCRATCH/"`, then `Edit` the scratch copy.
  3. Re-run ONE failing scenario (by id) against the scratch version, **using the SAME `--mock-service-url` the original failing run used** (read `plurai_sim/.mock_service_url` — running against the default `localhost:8199` falls back to an in-process `LocalMockService` and validates against the wrong backend):
     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
     MOCK_URL=$(cat "$PLURAI_DIR/.mock_service_url")
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1   # sets SERVER_URL; refuses tokenless remote URLs
     SCENARIO_SET_ID=$(cat "$PLURAI_DIR/.scenario_set_id")
     # Resolve the runner off the client's venv, never bare PATH — a PATH-found runner can
     # belong to a different install than the agent's own (see /simulation-run Step 3).
     PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never fall back
     [ -n "$PLURAI_BIN" ] && [ -x "${PLURAI_BIN%/*}/plurai-run-simulation" ] && RUNNER="${PLURAI_BIN%/*}/plurai-run-simulation" || RUNNER=plurai-run-simulation
     source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-gate-flags.sh"   # the plugin's overrules, so this run is held on nothing the run was not
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$RUNNER" \
       --scenario-set-id "$SCENARIO_SET_ID" \
       --scenario-id <scenario_name> \
       --agent-module-path "$SCRATCH/simulate.py" \
       --mock-service-url "$MOCK_URL" \
       --out "$SCRATCH/out" "${OVERRULE_FLAGS[@]}"
     # the runner resolves the token itself — NEVER pass it as an
     # argument (zsh does not word-split ${VAR:+...}, argparse rejects the glued arg, and
     # the error echoes the secret; argv is also ps-visible).
     ```
     Exit `7` from that block is the backend refusing this plugin or its SDK: the update text is printed and none of the runner's own output. Say it verbatim and alone, the way the error posture says a version refusal, and stop the diagnosis there.
  4. Read the new outcome from `$SCRATCH/out/<scenario>/`:
     - **Passed cleanly** (no `error.json` written) → report back to remote with the new `transcript.json` / `tool_events.json` excerpt. Then surface the verified fix to the user and ask whether to apply.
     - **Same failure** (compare `error.json.error_class` and `len(tool_events.json)` against the original) → the patch didn't help. Push back with `diagnose_mock(session_id=<saved>, message="Tried <patch>. Same error_class (<X>); tool_events length unchanged (<N>). The proposed mechanism doesn't match the observed retry pattern.")` — the remote gets concrete counterevidence.
     - **Different failure shape** (different `error_class`, or `tool_events` length changed) → partial progress; report the specific delta back to remote.

  Do NOT ask the user to apply a remote-proposed local fix before this verification round. Unverified patches that don't address the root cause waste developer time and erode protocol trust.

After each follow-up, the cycle continues until `status == "done"`.

### Step 4: End the session

When status is `"done"` (or the user wants to stop early), call:

```
end_session(session_id=<saved>)
```

The response has:
- `summary`: 1-3 sentence write-up of what was diagnosed + fixed.
- `files_patched`: deduped list of mock-env files the remote agent touched.
- `turn_count`: how many `diagnose_mock` calls ran.

Surface ALL of this to the user as the final report.

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

This posture governs backend infrastructure noise (MCP transport errors, backend session faults).
It does NOT restrict the diagnostic conversation itself: the remote agent's `response` shown
verbatim, the evidence trail, and the end-of-session `summary` / `files_patched` report ARE the
user-facing deliverable — keep showing those.

## Hard rules

- **Never invent a session_id.** If `diagnose_mock` returns a `session_id`, use it verbatim. If you get `status="done"` with a "session not found or expired" response on a call you thought was mid-conversation, start a fresh session by calling `diagnose_mock(session_id=None, ...)` again.
- **One scenario per session.** If multiple scenarios failed, do them sequentially — each gets its own `diagnose_mock(session_id=None, ...)` opener + own `end_session`. Cross-scenario context is rarely useful for the remote agent and bloats prompts.
- **Don't try to read the deployed mock files yourself.** They're on a different machine; you'd be reading the wrong files. Trust the remote agent to read its own filesystem.
- **Verify-before-deliver on local-side suggestions.** When the remote agent proposes a local-side fix, run it through the Step 3 verification procedure (scratch-branch patch + scenario re-run) BEFORE surfacing the fix to the user. The remote operates from incomplete information and can confidently propose patches that don't address the root cause. If the verification round shows the patch doesn't help, push back to remote with the new evidence instead of accepting. The user only sees patches that have been independently verified to make at least one failing scenario pass — or, if no patch works, the conversation's full evidence trail so they can make an informed call.
- **Keep MCP messages small.** The remote agent has limited context. If a payload looks like it'll exceed ~4KB, summarize first; if the remote agent needs more, it will ask.

## When to stop

The remote agent might end the session early with `STATUS: done` and a summary like:

- **"Patched + verified"** → the fix worked; re-run + verify locally then close.
- **"Out of scope — local issue"** → the bug is in YOUR domain (simulate.py / scenarios / etc.). Don't expect the remote agent to fix it; make the local edit yourself — a harness repair is applied without asking — and tell the user what changed and why.
- **"Out of scope — template bug"** → the bug is in the env-generator's template, affecting many customers. Nothing here is user-fixable, so there is no repair step: run the contact hand-off (`stop_message.py contact --done`). The template-bug specifics from the remote agent are internal — don't dump them into the chat.

In all three cases, the session ends. Call `end_session` and report the outcome.
