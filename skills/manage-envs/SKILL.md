---
name: manage-envs
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: >-
  Save, switch between and list named simulation environments under plurai_sim/envs/. Triggered by natural language such as "save this as env <name>", "switch to env <name>" and "list envs"; there are no slash commands.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Manage simulation environments

You are the LOCAL Claude Code agent. An **environment** is a committable,
switchable bundle that ties the customer's local input artifacts to the exact
backend-generated env / scenarios / evals they were produced against, so the
customer can version it in git, switch between several, and reproduce later.

```
plurai_sim/
  envs/
    billing-happy/                # local alias (the directory name)
      plurai.env.yaml             # MANIFEST: env_id + set ids + fingerprints (committed)
      plurai.config.yaml          # aligned inputs (committed)
      simulation_prd.md
      scenario_variables.yaml
      scenario_state_schema.yaml
      eval_intent.yaml / policies_config.yaml / simulate.py ...
    refunds-edge/                 # a second coexisting env
      plurai.env.yaml ...
  .active-env                     # one line = active alias (gitignored, local state)
  .mock_service_url               # runtime handle written at activation (gitignored)
```

The **directory name is a local alias**; the manifest's `env_id` (+ the set
ids) is the canonical key. Backend mock-env *definitions* are persistent and
keyed by `env_id` — switching to an env re-deploys it by id (it does NOT need
regeneration). Only the live deployment is ephemeral.

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

> The drift warnings and missing-set warnings in USE / LIST below are NOT internal noise — they
> tell the user THEIR config drifted or a set THEY reference was deleted. Keep them, in plain
> product language.

## The manifest helper

All manifest reads/writes/drift-checks go through the shipped helper — never
hand-edit `plurai.env.yaml`:

```bash
PY=python  # or the project's interpreter
EM="${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py"

# write/refresh (preserves prior ids; recomputes fingerprints)
$PY "$EM" write plurai_sim/envs/<name> --name <name> \
    [--backend-url <url>] [--env-id <id>] [--scenario-set-id <id>] \
    [--eval-set-id <id>] [--corpus-id <id>] [--corpus-ids <id> <id> ...]

# read (JSON)            check drift (JSON: {in_sync, drift:[{file,status,stale_downstream}]})
$PY "$EM" read plurai_sim/envs/<name>
$PY "$EM" check plurai_sim/envs/<name>
```

## The active-env resolver (shared rule)

Every skill that reads/writes artifacts resolves its working dir like this:

1. If `plurai_sim/envs/` exists, the **active env dir** is
   `plurai_sim/envs/$(cat plurai_sim/.active-env)`.
2. If `.active-env` is missing but exactly one env exists, use it (and write
   `.active-env`).
3. If `plurai_sim/envs/` does NOT exist, fall back to bare `plurai_sim/`
   (back-compat for single-env repos that predate this feature).

### The env IS the workspace — its 3 input artifacts are scoped to it

An env owns its **corpus**, **traces (trace brief)**, and **uploaded documents**. Direct to a
specific env by switching to it (the USE operation below); if none is directed the backend uses the **latest**. Each is
kept per-env so switching envs switches all three:

- **Corpus** — `corpus_id` / `knowledge_bases` on the env manifest (already), passed explicitly to
  `generate_scenarios_start`.
- **Docs** — `doc_examples_set_id` on the env manifest (recorded by `/upload-doc-examples`), read
  per-env by `/generate-scenarios`.
- **Trace brief** — the backend stores it per workspace; pass **`workspace_id=<bench workspace id>`**
  (the stdout of `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"`,
  which composes `<bench_id>.<alias>` for the active env) on `ground_workspace_start` (publish), on
  `generate_env_start` / `generate_scenarios_start` / `generate_evals_start` (consume), and on the
  edit/refine openers `refine_scenarios_start` / `edit_session_start` (remembered on the session, so
  its `include_trace_brief` turns stay on this env). Never omit it: an omitted id is the box-wide
  `default` slot any other bench can read or overwrite.

The env's own id travels with them: `generate_scenarios_start` takes `env_id`, read from the active env's `.env_id`
marker, and the set plants only the faults that env injects. Switching envs switches the marker, so the call never
carries another env's id.

---

## Operation: SAVE  (natural language: "save this as env `<name>`")

Snapshot the CURRENT artifacts + backend ids into `plurai_sim/envs/<name>/`.

1. Resolve the current source dir (the active env dir, or bare `plurai_sim/`).
2. Create `plurai_sim/envs/<name>/` and COPY the aligned artifacts that exist:
   `plurai.config.yaml`, `simulation_prd.md`, `scenario_variables.yaml`,
   `scenario_state_schema.yaml`, `eval_intent.yaml`, `policies_config.yaml`,
   `simulate.py`, `bridge/` (never `bridge/runtime/node_modules/`), `test_mock_contract.py`,
   `verify_*.py`.
   Then copy the grounding ledger's mirror and the suite's pin beside them, in the same step and
   BEFORE the manifest is written (the manifest records the pin that sits in the env directory):

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   ENV_DIR="${WORKDIR%%/plurai_sim*}/plurai_sim/envs/<name>"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" save --workdir "$WORKDIR" --env-dir "$ENV_DIR" \
     --workspace-id "$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py" --workdir "$ENV_DIR")"
   ```

   It copies `grounding_ledger.yaml` and `.ledger_version`, byte for byte, and prints
   `GROUNDING=saved …` or `GROUNDING=ungrounded notice=…` (nothing to copy yet: saving is still fine, the inputs are
   the point, but the env has no pin; see below). Both files are committed with the env. The mirror is never edited here; see
   `${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md`.

   A mirror belongs to the workspace it was delivered under, and the env's own workspace is `<bench id>.<name>`: a
   pin from another workspace is refused by every generator (`other_workspace`). Saving the bare workdir's mirror
   under a new name is that case. The command then exits 3 with one line naming both workspaces and copies neither
   file; a bare workdir with no mirror yet prints `GROUNDING=ungrounded notice=…`. Either way the env has no pin and
   would stop at generation with `ledger_version refused (missing)`. SAVE goes on without them, and tells the user
   in one plain line (the script's wording, in product terms): the grounding pin was not saved, why (the saved
   description belongs to another workspace, or none was delivered yet), and the next step, the delivery-only repair
   below, naming no workspace id or file (the repair grounds the env on its description files only: traces and a corpus
   delivered under the bare workspace are not carried over). Offer that repair; do not run it unasked, because it sends the env's description files to the backend. Never
   copy the mirror or the pin by hand.

   **Delivery-only repair** (the user said yes; no scan rerun). Deliver the env's own description files under the
   env's workspace, then pull and pin:

   1. Make the env active (`plurai_sim/.active-env` = `<name>`; the manifest is written in step 4) so `$WORKDIR`
      resolves to the env directory and `workspace_id.py` gives `<bench id>.<name>`.
   2. Run `/simulation-scan-agent` Step 4b items 3, 4 and 5 as written (the `ingest-descriptions` block, then
      `ground_workspace_start` with `PREVIOUS=` from `grounding_delivery.py previous` and `ADDED=` from
      `grounding_delivery.py added`, then `grounding_delivery.py record`), with `ground_workspace_start`'s
      `workspace_id` the env's own. A new workspace has no previous version: leave `previous` out.
   3. Wait for the job (item 6 there), run Step 4c's pull block, then
      `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pin --workdir "$WORKDIR"`. It pins only a settled
      version (exit 4 when it is not): then tell the user the pin comes with the next `/simulation-generate-suite`,
      whose Step 0b waits, pulls and pins.

   The manifest records the pin itself when it exists (`pin` writes `ledger_version` into it); run step 4 first if the repair
   is run before the manifest is written. `pin` exit 3 means the manifest cannot record it: say the pin was not saved.

3. Read the backend handles from the current dotfiles if present
   (`.env_id` — written by `/generate-mock-env`; before the world has been stood up, i.e.
   between `/simulation-scan-agent` and `/simulation-generate-suite`, there is no id yet and
   the manifest records none — saving then is fine, the inputs are the point, but that env
   cannot be switched-to until a suite run mints its world. Plus `.scenario_set_id`,
   `.eval_set_id`, `.corpus_id`, and — for a per-tenant
   multi-corpus setup — `.corpus_ids` (one id per line); pass it via `--corpus-ids`).
4. Write the manifest with those ids + the logged-in backend's url as `backend_url`. It records the
   pinned grounding version as `ledger_version` by itself: it reads the pin copied in step 2.
5. Set `plurai_sim/.active-env` to `<name>`.
6. Tell the user to `git add plurai_sim/envs/<name>/` (the manifest + artifacts +
   the backend-identity markers are meant to be committed; the machine-local
   handles are gitignored — see below).

## Operation: USE  (natural language: "switch to env `<name>`")

Switch the active environment and make sure its mock is live.

1. Resolve the env dir: by directory name `<name>`, OR by scanning
   `plurai_sim/envs/*/plurai.env.yaml` for a manifest whose `env_id` matches.
   If neither matches, list available envs and stop.
2. **Drift check** (warn, don't block): run `env_manifest.py check`. For each
   drift entry, warn the user, e.g.:
   `⚠ plurai.config.yaml changed since this env was generated — its mock,
   scenarios, and evals may be stale. Regenerate them or proceed knowingly.`
3. **Ensure the mock is deployed**: call the MCP tool
   `deploy_env(env_id=<manifest env_id>)`. It is idempotent — returns the
   existing `mock_service_url` if already live, or re-spins it from the stored
   `simulation_env/` at the SAME stable server-RELATIVE `/mock/<env_id>/` path.
   Then run the seed check, accepted + read back (`/generate-mock-env` Step 6.6).
   If `deploy_env` reports the definition doesn't exist (deleted / different
   backend), tell the user in plain terms that this environment's mock is no
   longer available and that `/simulation-generate-suite` stands up a replacement — don't quote
   the raw error text.
4. **Verify the sets still exist** (warn if missing): `list_artifacts(kind='scenarios')` /
   `list_artifacts(kind='evals')` and check the manifest's `scenario_set_id` / `eval_set_id`
   are present; warn if a referenced set was deleted on the backend.
5. Write runtime handles into the active location for back-compat with the run
   skills. `.mock_service_url` holds an ABSOLUTE URL, but `deploy_env` returns a
   server-RELATIVE path, so join it first (the `/generate-mock-env` Step 5.5 rule,
   restated because this operation never opens that skill):
   `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1`, then
   `${SERVER_URL%/}` + that path — the whole base and its path prefix, never a rebuild
   from scheme+host, and the `|| exit 1` is what stops a tokenless remote base from
   saving a URL the run cannot reach. Then the id dotfiles
   (`.env_id`, `.scenario_set_id`, `.eval_set_id`, `.corpus_id`) from the manifest —
   `.env_id` so `/simulation-generate-suite` Step 0c takes its cheap re-deploy branch
   straight after a switch. If the
   manifest carries a `corpus_ids` list, also write `plurai_sim/.corpus_ids` (one id per
   line) so `/generate-scenarios` can pick up the per-tenant corpora — it is one of the three
   shapes `scripts/corpus_coordinate.py` reads, and the only one a per-tenant env has.
6. Set `plurai_sim/.active-env` to the resolved alias.
7. **Restore the grounding.** The env's mirror and pin come back with it. Run this AFTER step 6, so
   `$WORKDIR` is the env directory:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" restore --env-dir "${WORKDIR%%/plurai_sim*}/plurai_sim/envs/<name>" --workdir "$WORKDIR" \
     --workspace-id "$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
   ```

   `GROUNDING=restored …` means the mirror is on disk and the pin is written from the env's marker, else
   from its manifest's `ledger_version`. `GROUNDING=other_workspace mirror_workspace=… workspace=…` means the env was
   saved with a mirror of another workspace: its pin would be refused by every generator, so neither came back. Warn once,
   `⚠ this env's grounding was saved from another workspace — it is read again from the backend`, then run the pull block
   of this skill's last step now. The workdir holds no delivery of this env, so the pull asks the backend for the env's own
   workspace by its id; the pin stays empty until a suite pins the version it builds on. `GROUNDING=ungrounded ungrounded, deliver first` means this env
   was saved without a mirror: warn once, `⚠ ungrounded, deliver first — this env has no grounding
   recorded; /simulation-scan-agent delivers the agent's setup`, and carry on. Switching never moves one
   env's pin into another: an env with no pin leaves the workdir with none.
   **Ledger staleness** (warn, don't block). Run this AFTER the restore above, so the pin and the mirror are
   this env's own. An empty runner is allowed here: the check then runs offline.

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed, then stop. 1 = no runner: PLURAI_BIN stays empty and the check runs offline
   [ -n "$PLURAI_BIN" ] && source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" ""
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_staleness.py" --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
   ```

   `STALENESS=current` or `ungrounded`: say nothing. `unverified`: nothing to compare with, say nothing.
   `STALENESS=moved` with counts: warn once, `⚠ the agent's grounding has moved since this env was built:
   <entries_added> facts added, <entries_changed> changed, <entries_removed> removed, <findings_added> findings
   — what moved is in artifacts/ledger_moved.yaml`; with `detail=unavailable` (offline, or the backend did not
   answer): warn that the mirror's version differs from the pinned one as of the last pull, and that what moved needs
   the backend. The pin stays; adopting the newer version is the edit path's job, never a switch's.
8. Report the active env, the absolute URL just written to `.mock_service_url`, and any
   drift/missing-set/grounding warnings.

## Operation: LIST  (natural language: "list envs")

1. Enumerate `plurai_sim/envs/*/plurai.env.yaml`; read each manifest.
2. Annotate with backend status: `list_artifacts(kind='env')` (deployed? exists?),
   `list_artifacts(kind='scenarios')` / `list_artifacts(kind='evals')` (referenced set still present?).
3. Print a table: alias, env_id (short), deployed?, scenario/eval present?,
   drift? (run `check` per env), and which one is `.active-env`.
   Read-only — does not deploy or generate anything.

---

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

## gitignore

Nothing to write here. The repo root's `.gitignore` carries ONE managed block, authored by
`/simulation-scan-agent` Step 5 — that is the canonical spelling and the only `.gitignore` in
play. It commits `envs/**/plurai.env.yaml`, the aligned artifacts, and the backend-identity
markers (`.scenario_set_id`, `.eval_set_id`, `.corpus_id` / `.corpus_ids`,
`.knowledge_bases`, …); it ignores the machine-local handles (`.active-env`,
`.mock_service_url`, `.env_id`) and every extracted-content directory. If the repo's block is
missing or its BEGIN line carries an older rev, re-run `/simulation-scan-agent` Step 5 rather
than hand-writing rules.

## Notes

- Names are local aliases (unique within a repo, since they're directory
  names). The same alias in two repos refers to two different envs; the
  `env_id` is globally unique.
- Multiple envs coexist in one repo (e.g. one per component under test). A
  repo can also be dedicated to a single env — both work.
- Switching never regenerates: `deploy_env(env_id)` revives the stored mock.
  Regeneration is only needed when drift makes you WANT fresh artifacts.
