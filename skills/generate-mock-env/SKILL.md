---
name: generate-mock-env
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: >-
  Generate and deploy the agent's mock environment with a backend job, then store its env_id and mock url. Normally invoked by /simulation-generate-suite; run alone to rebuild the world after /simulation-scan-agent.
---

# Generate Mock Environment

You are the LOCAL Claude Code agent. `/simulation-scan-agent` has produced `plurai.config.yaml` + `simulation_prd.md` (+ optionally `test_mock_contract.py`) in the `plurai_sim/` directory. You are normally invoked by `/simulation-generate-suite` Step 0c; a direct call is the standalone (re)build path. Your job is to trigger a backend job that generates the matching `simulation_env/` (mocks + run.py) and deploys it as a running mock service, then save the URL the customer's later `plurai-run-simulation` will point at.

```
┌── customer's machine (you) ──────────────────────────────────┐
│  1. Read inputs from plurai_sim/.                            │
│  2. MCP: generate_env_start(...) → env_session_id (queued)   │
│  3. poll_job.sh <env_session_id>, then job_status once       │
│     → render a plain progress line on each phase change.     │
│  4. On status='done':                                         │
│     → join mock_service_url to $SERVER_URL (the whole base)   │
│       and save it to plurai_sim/.mock_service_url.            │
│     → save the env id to plurai_sim/.env_id.                 │
│     → hand off to /simulation-generate-suite.                │
└─────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ MCP
                                  │
┌── Plurai-hosted backend ──┴───────────────────────────────────┐
│  plurai MCP server:                                           │
│    generate_env_start → starts the env generation job         │
│      (generator + pytest repair loop)                         │
│    job_status → returns current job state                     │
│    undeploy_env → tears down deployed subprocess (explicit)   │
└─────────────────────────────────────────────────────────────┘
```

**Why this lives on the backend.** The generation needs model access that Plurai provides on its backend, so customers never have to provision a key locally. The local Claude here is just orchestrating — no LLM work happens on the customer's machine for this skill.

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

1. **`plurai` MCP server is reachable.** Auto-registered via the plugin manifest (`.claude-plugin/plugin.json` `mcpServers`). The backend is the configured one, the one the saved Plurai login names (a project `.env` is not read). A remote backend is authenticated with the saved login's token. Listing the MCP servers verifies registration; the server has to actually be running.
2. **The backend has env storage configured.** On a Plurai-hosted deployment that's the default. When it is not configured, `generate_env_start` raises a ValueError that names the setting; that is not fixable from here: run the contact hand-off (`stop_message.py contact --done`).
3. **`plurai_sim/plurai.config.yaml` and `plurai_sim/simulation_prd.md` exist.** They're the outputs of `/simulation-scan-agent`. If either is missing, STOP and tell the user to run `/simulation-scan-agent` first.
4. **`plurai_sim/test_mock_contract.py` is optional.** If present, the backend's generator uses it to drive a pytest repair loop. If absent, the generator skips the verification phase.

## Step 0 gate: an env already exists → DEPLOY it, don't build a second one

Before anything else, look for a stored env id — `$WORKDIR/.env_id`, `$WORKDIR/.mock_session_id`, or
the active manifest's `env_id`. If one exists, the mock DEFINITION is already on the backend and
`deploy_env(env_id=<id>)` revives it under the same stable URL in seconds (then Step 6.6). Do that instead of
generating; nothing on this side dedupes generation, so a second `generate_env_start` leaves the
first mock deployed and holding its port forever. Generate a replacement only after
`undeploy_env(env_id=<old>)` — and only when the stored world is genuinely wrong for the current
inputs (its `plurai.config.yaml` / `simulation_prd.md` changed). If its answer has
`generation_in_flight: true`, the undeploy did not stop that generation: cancel it with
`delete_artifact(id=<old>)` first (see the regenerate rule under Hard rules).

**Deploy only an env this project generated.** An env id comes from this workdir's own record
(`$WORKDIR/.env_id`, `$WORKDIR/.mock_session_id`, the active manifest) and nowhere else. An env
that `list_artifacts(kind="env")` shows is NOT such a record: the workspace is shared, and an
env with a matching schema was generated for some other project or run. Never pick one from that
listing, by schema match or any other test, and never `deploy_env` or write `.env_id` for it. With
no record on disk, generate.

One exception to the plain deploy: when `job_status(id=<id>)` reads `detail.serves_previous_commit: true`,
the live mock is the env as it was before its last `/edit-env` commit (that commit's redeploy failed;
`detail.last_deploy_error` says why), and `deploy_env` would hand it back unchanged. Fix that cause (when it
names a slow-starting mock, follow the remedy it quotes), then `undeploy_env(env_id=<id>)` +
`deploy_env(env_id=<id>)`, silently: it is a deploy fault on our side, never a failure of the customer's
agent.

## Step 0 gate: nothing to mock → skip WITHOUT asking

Before anything else, run
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" run-mode`.
It derives the posture from `plurai.config.yaml` and writes the marker itself. On `run_mode: mockless`
— a tool-less agent (single-LLM dialog flow included) or one whose every tool runs live — there is
NOTHING for env-gen to produce: no mocks to build and no `.mock_service_url` to intercept through (a
harness-owned world is still seeded per scenario by `seed_state`); `/simulation-run` accepts `.run_mode` in place of a mock URL.

**Skip this skill silently.** Print ONE line — e.g. *"No mockable tools — skipping mock-env deploy;
scenario generation, evals, and runs are unaffected (they are separate backend jobs)"* — and move on
to the next step of the flow (`/generate-scenarios`).

Do NOT ask the user whether to "deploy an empty env anyway" and NEVER frame skipping env-gen as
skipping "the backend": `/generate-scenarios` and `/generate-evals` are independent backend jobs that
run regardless of whether a mock env exists. An empty-env question reads to the customer as "maybe
the whole backend is optional for my agent," which is false and has caused real confusion — the
backend is REQUIRED for scenario/eval generation for every agent shape, tools or not.

## The flow

### Step 1: Detect the plurai_sim directory

The skill takes one optional argument: the path to `plurai_sim/`. If not provided, resolve it with
the shared resolver — **never** with a hand-rolled `for candidate in plurai_sim …` loop: that loop
yields a RELATIVE `plurai_sim/` and is not named-env aware, so on a repo using named envs it lands
one directory above the real workdir (`plurai_sim/` instead of `plurai_sim/envs/<name>/`), which is
where the SDK reads `.run_mode`, `.env_id` and `.mock_service_url` from.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE, named-env aware $PLURAI_DIR / $WORKDIR / $ART
[ -f "$PLURAI_DIR/plurai.config.yaml" ] && [ -f "$PLURAI_DIR/simulation_prd.md" ] \
  && echo "using $PLURAI_DIR" || echo "no plurai_sim inputs at $PLURAI_DIR"
for f in .env_id .mock_session_id .document_fields test_mock_contract.py envs; do [ -e "$PLURAI_DIR/$f" ] && echo "$f=present" || echo "$f=ABSENT"; done   # ABSENT is normal; never probe with ls
```

If the skill was given an explicit path argument, use it verbatim instead (make it absolute first).

If none match, ask the user where their `plurai_sim/` lives (inside a driving command's flow window, sanction it first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction workdir-location`).

### Step 2: Load the input files

Read each as text:

```
plurai_config_yaml      ← plurai_sim/plurai.config.yaml
simulation_prd_markdown ← plurai_sim/simulation_prd.md
test_source             ← plurai_sim/test_mock_contract.py  (None if missing)
```

**Do NOT pre-process these on the local side.** The backend parses them with the same parser the CLI uses — local preprocessing would mean a divergent code path. Just read them and forward the raw text.

**Stash the scan contract too** — its verified facts constrain the seeded world (an absence fact
the extractor seeds an entity for refutes the suite's own premise before a scenario ever runs):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
# The marker goes first, so a refusal below leaves no earlier run's id to pass for this run's.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .scan_contract_upload_id
# The stash runs through the runner. An empty PLURAI_BIN is not a stop here: the producer then
# reports STASH_FAILED, and the contract is optional for this call (see below).
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require stash-doc)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# The stash uploads the contract; the guard checks the backend before it runs.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
source "${CLAUDE_PLUGIN_ROOT}/scripts/stash_scan_contract.sh" || { [ $? -eq 7 ] && exit 7; }   # $CONTRACT_ID + $CONTRACT_PROCEED + the two counts; 7 = the backend refused the version, any other status goes on
echo "CONTRACT_ID=${CONTRACT_ID:-STASH_FAILED} CONTRACT_PROCEED=$CONTRACT_PROCEED CONTRACT_PROVISIONAL=$CONTRACT_PROVISIONAL CONTRACT_BLOCKED=$CONTRACT_BLOCKED"
```

Exit 7 from that block is the backend's refusal of this plugin or its SDK: the update text is already printed.
Say it verbatim and alone, the way the error posture says a version refusal, and go no further.

### Step 3: Start the job

**`generate_env_start` sends the loaded config/PRD/test-source text straight to the backend over
MCP.** There is no plugin-side preview/consent hook for this call; the harness's normal tool-approval
prompt (naming the tool + its arguments) is the user's consent chokepoint. The disclosure is not
skill-mediated; do not duplicate it in the chat panel.

If the user denies the harness prompt, surface the denial verbatim and stop. Do not retry. Do not
inline the payload into another tool call to bypass approval.

**Driven by `/simulation-generate-suite`, run its Step 0 job lines (`--phase world`) around this call**:
an adopted job replaces the call; a started one is recorded the moment it returns.

**`ledger_version` — the grounded facts the world is built from.** When `generate_env_start` names a
`ledger_version` parameter in your tool list, the backend builds the world's tool, entity and agent facts
from a SETTLED version of the agent's grounding ledger and requires that id: the config's tool facts, the
PRD's tool, entity and agent-fact sections and the trace brief are not read for them. When your tool list does not name
`ledger_version`, the backend generates from the inputs below: pass nothing.

1. **The pin comes first.** `/simulation-generate-suite` Step 0b pins one settled version for the whole build,
   and every generator of the build sends that one; a run of this skill on its own reads the pin an earlier
   suite left. The pin is the only source of the id. Read it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pinned --workdir "$WORKDIR"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" waived --workdir "$WORKDIR"
```

   `WAIVED until <date>` printed: the operator waived grounding for this workspace, and the backend runs a waived
   workspace ungrounded only on a call that carries no `ledger_version`. Send the call with `ledger_version` omitted
   (the one case where the argument is left out, bar an undelivered env below) and skip the steps that read a job: `pinned` prints nothing here, and
   neither a `.ledger_version` left on disk nor a job recorded for another workspace is a pin. Read only `$WORKDIR`'s
   own markers: a job or pin recorded above `envs/` belongs to the bench's own workspace, which this env's call does
   not name.

   An id printed is `ledger_version`, exactly as printed: skip steps 2 to 5, because a pin is always a settled
   version. Nothing printed means no suite has pinned one here: make the pin from the delivery, steps 2 to 5.
   Never read the mirror to choose a version.

2. Read the job the last delivery recorded on this machine:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$WORKDIR/.grounding_job" 2>/dev/null || echo "NO_GROUNDING_JOB"
```

3. `job_status(id=<the job id>)`. `detail.settled: true` → go to step 5, whatever the job's `status`: a
   knowledge-graph build may still be running, and the env never waits for it. `settled: false` while the job is
   `queued` or `running` → wait 60 seconds in the foreground with the `wait.sh` helper (as in the fallback below) and read it
   again; a non-empty `detail.held_by` is the findings handler's (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`),
   never a question for the user. A `failed` job made no version: do not call.
4. `NO_GROUNDING_JOB`: nothing was delivered under this env from this machine. Call the tool WITHOUT `ledger_version`
   and let the backend decide: it runs a workspace the operator waived ungrounded, and refuses any other before
   anything starts. A message that starts `ledger_version refused (missing)` means the agent's setup is not recorded
   yet: stop, and tell the user once, in product terms, that `/simulation-scan-agent` records it. When the call
   starts, read `detail.grounding_waived` on its answer; when it carries `expires`, keep it with `python3
   "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" waiver --workdir "$WORKDIR" --expires
   <detail.grounding_waived.expires>` so the next call omits the version at once.
5. The job is settled: pull the version it made and pin it, so the pin file holds the id this call sends.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pin --workdir "$WORKDIR"; echo "pin exit=$?"
```

   Exit 0 prints `PINNED=<id>`, which equals the job's `detail.ledger_version`: send that id. Any other exit
   (`4` unsettled, `5` no mirror, `3` unreadable, or a pull that was not `MIRROR=pulled`): pull once more, and
   if it is still not exit 0, do not call the tool.

Never send "latest", never read an id out of `grounding_ledger.yaml` or the PRD, and never send an id from another
workspace: the backend refuses all three, and a call with no settled version is not made at all. Never drop the
argument to get a call through.

**What moved in the grounding since the pin (report, never ask).** Once the pin is known and before the call, run
the staleness check. The pin stays: the call sends the pin whatever the answer is, and the mirror is never edited. An
empty runner is allowed here: the check then runs offline.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed, then stop. 1 = no runner: PLURAI_BIN stays empty and the check runs offline
[ -n "$PLURAI_BIN" ] && source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" ""
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_staleness.py" --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

Only `STALENESS=moved` matters; `current`, `ungrounded` and `unverified` say nothing. With counts, say one line in
this skill's output: the world is built on the agent's grounding as pinned, and since then facts were added,
changed or removed (the `entries_*` counts), findings moved (the `findings_*` counts) and parts moved (the
`parts_*` counts); what moved is in `artifacts/ledger_moved.yaml`. With `detail=unavailable` (offline, or the diff
was refused or not served) say only that the mirror's version differs from the pinned one as of the last pull.
Never name a version id, never ask the user what to do, and never hold the call for it: a newer version is
adopted at a stage boundary through the edit path. Driven by `/simulation-generate-suite`, log it instead of
saying it (`append_loop_log.py --kind note --finding "grounding moved: <entries_added> added <entries_changed> changed <findings_added> findings"`).

Call the MCP tool:

```
generate_env_start(
    plurai_config_yaml=<full text of plurai_sim/plurai.config.yaml>,
    simulation_prd_markdown=<full text of plurai_sim/simulation_prd.md>,
    test_source=<full text of plurai_sim/test_mock_contract.py; None when absent>,
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"; scopes the auto-discovered trace brief to THIS bench
    scan_contract_upload_id=<$CONTRACT_ID echoed by the stash above>,  # ABSENT/STASH_FAILED → omit (see below)
    ledger_version=<the pin that `grounding_mirror.py pinned` prints>,  # REQUIRED when the tool list names it; omit otherwise
)
```

**`scan_contract_upload_id` — the corpus claims the seeded world must not contradict.** Pass the
SAME id the later `generate_scenarios_start` / `generate_evals_start` calls use; the ids are
content-addressed, so re-stashing the unchanged file returns it.

- `CONTRACT_ID=ABSENT` — no contract for this env: omit the parameter and **say the world was
  built without the corpus checks**. Not-checked is not checked-and-clean.
- `CONTRACT_ID=STASH_FAILED` — a `stash_doc:` line ending `fall back to the MCP stash_input_doc tool` → the MCP `stash_input_doc(text=<the file's
  contents>, label='scan_contract')` fallback; otherwise (including no usable runner, which the
  resolver's stderr names) omit and say the same line.
- No `CONTRACT_ID=` line, and `client_pin: REFUSED`, `SPLIT BACKENDS` or `refusing unauthenticated
  remote calls` on stderr — the pin or the wrong-backend guard stopped the block before the stash.
  **Stop**: tell the user in the message's own words, and do not start the job; it would send the
  PRD to the backend that guard refused.
- A backend that rejects the parameter is an older box: drop it, re-call, say the same line.
- `CONTRACT_PROCEED=false` — the checker found one of the scan's conclusions FALSE against the
  corpus, and the suite's Step-0 gate was overridden to build anyway. **Still pass the id** (the
  allowlist and the surviving facts are real). It demotes the suite's provenance line;
  `/simulation-generate-suite` Step 6 reads the same file.
- `CONTRACT_PROCEED=unreadable` — the file exists and its verdict cannot be parsed. Treat it as
  ABSENT: an unparseable verdict is not a passed one.
- **`CONTRACT_PROVISIONAL` / `CONTRACT_BLOCKED` — a `true` is not automatically a verified
  scan.** The first counts the typed facts the contract carried ASSERTED and UNVERIFIED (declared
  with no executable check, so nothing tested them); the second counts the checks that could not
  run at all. Both are `none` when there is no contract, and `unknown` when its verdict could not
  be parsed. **Any non-zero count goes in your summary line, named**: the seeded world was built on a scan whose
  premises include N nobody tested is a different artifact from one built on a scan where every
  premise was checked, and `proceed: true` is identical in both. The report itself names WHICH
  facts, under `provisional_facts` in `scan_contract.json`.

The response is a generic **Job envelope** (every backend job kind reports
through this single shape now):

```yaml
id: <uuid>                 # this IS the env_session_id / env_id
status: queued             # queued | running | done | failed
phase: queued              # free-form generation sub-state (see Step 4)
started_at: <float>
completed_at: null
error: null
detail:                    # kind-specific typed payload
  env_id: <uuid>           # == id
```

**Save `id` (the env_session_id).** Every poll + the eventual end call need
it. (`detail.env_id` mirrors `id`.)

If the call raises `ValueError("env_jobs_workspace_root is not configured...")`, the backend doesn't have env-gen enabled — this is not fixable from the customer side: run the contact hand-off (`stop_message.py contact --done`). Don't retry; it won't resolve on its own.

**`ledger_version` refusals.** A message that starts `ledger_version refused (<cause>)` is the backend's answer to
the pin, raised before anything ran. It is not a content problem and it is never retried without the argument: a
world built without the grounded facts is an ungrounded run, and the backend does not allow one.

| cause | what it means | do |
|---|---|---|
| `missing` | the call carried no version: this plugin is older than the backend | update the plugin (the user's step; say so in product terms), then start the build again. Never retry without the argument |
| `no_workspace` | the call named no `workspace_id` | add the bench workspace id (the same call block) and call once more |
| `malformed` | the value is not a version id | re-read the pin from the job as above; never type or shorten an id |
| `grounding_off` | this backend keeps no grounding ledger | not fixable from here: run the contact hand-off (`stop_message.py contact --done`) |
| `unknown` | the backend does not hold that version | re-read the pin from the job as above; the same answer twice: run the contact hand-off (`stop_message.py contact --done`) |
| `other_workspace` | the version belongs to another workspace | the handle on this machine points at another bench: re-run `/simulation-scan-agent`, which records this bench's own |
| `unsettled` | the version is still being made, or a finding holds it | wait for the job as above, then call once more with its `detail.ledger_version` |

If the call raises any other ValueError (e.g., parser rejected the YAML / PRD), it's a content problem — read the error YOURSELF to find what's malformed. If you can fix the input from here (e.g. re-run `/simulation-scan-agent` to regenerate it), do that; otherwise tell the user in plain product terms what in their config/PRD needs to change. Don't paste the parser error.

### Step 4: Poll for progress

Loop until `status` is `done` or `failed`, waiting between reads. The job is minute-scale, so the
cadence is MINUTES, not seconds (see `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` — canonical).

**One blocking call per wait** — the sleep lives inside the script, so the wait cannot overlap the
read:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <env_session_id>
```

**Call this with a Bash `timeout` of `600000`** — the harness default is 120 000 ms, which kills the script 420 s inside its own budget and looks exactly like a wedged backend.

**Poll in the FOREGROUND, and keep the turn open until the job is terminal.** Never run `poll_job.sh` or `wait.sh` in the background, and never end a turn on "I'll check back" while the job is `queued` or `running`: a headless `claude -p` session exits when the turn ends, the background poll dies with it, and the user gets no result. On exit 2 re-invoke in the same turn, until the job is `done` or `failed`.

Env-gen typically outlives one poll budget: **one or two exit-2 re-invocations are the
normal shape of a healthy job, not a stall signal** — re-invoke and keep waiting.

It returns when the job is terminal; then read it ONCE through MCP for the full envelope:

```
job_status(id=env_session_id)   # → render the terminal result
```

Exit codes: `0` done · `13` failed · `15` **an id the backend does not know** (re-check / re-launch
the id — the backend is fine) · `14` budget spent, backend healthy (**slow, not wedged** —
re-invoke) · `12` this backend has no `GET /jobs/{id}` — a 404 with **no job envelope** (fall back to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110` then `job_status(id=…)`, waits kept SERIAL. Run `wait.sh` in the FOREGROUND — its sleep is internal, so the harness's block on a bare foreground `sleep` does not apply and the tool timeout bounds it. It takes a DURATION and nothing else: never hand-roll a loop that exits on a job status (the backend reports success as `done`, and a loop watching for `complete`/`completed` never exits)) · `77` bad token · `69` job state unknown: backend not responding, or up with the job route faulting (the script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) · `7` Plurai refuses this version of the plugin or its SDK: the plugin's update text was printed; say it verbatim and alone, then stop.

`job_status` returns the **Job envelope**. The lifecycle `status` is
one of `queued | running | done | failed`; the env generation sub-state
(`generating` / `verifying` and the other phases) rides the free-form `phase`
field, and the env-specific fields live under `detail`:

```yaml
id: <uuid>                 # the env_session_id / env_id
status: queued | running | done | failed
phase: queued | generating | verifying | deploying | done | failed
started_at: <float>
completed_at: <float | null>
error: <str | null>        # only set on failed
detail:                    # env-specific fields
  env_id: <uuid>           # == id
  iteration: <int>         # pytest repair iteration; 0 during initial generation
  last_pytest_output: <str | null>   # tail of most recent pytest; null if none yet
  mock_service_url: <str | null>      # populated when deployed — a server-RELATIVE /mock/<id>/ path
  deployed: <bool>         # read live off the EnvStore (gated on subprocess liveness)
  last_deploy_error: <str | null>
  smoke_contract_status: <str>         # env-contract report verdict, read fresh off disk on EVERY read — deployed or not
  smoke_contract_verdict: pending | not_blocking | blocking | not_run | missing   # that line as a type; also on the deploy_env result
  verification: passed | partial | failed | not_run   # contract tests; also on the deploy_env result
  verification_detail: <str>          # one line: partial = none failed, some could not run on the backend
  elapsed_seconds: <float>            # wall seconds since the job started
  seconds_since_last_activity: <float>  # seconds since the job last emitted progress
  serves_previous_commit: <bool>      # the live mock is the env BEFORE its last /edit-env commit (that redeploy failed)
  gate_authority: <block | null>      # INTERNAL: the gate findings this env shipped or was held with
```

Read the phase from `phase` (not `status`) for the progress label; the env
fields (`iteration`, `last_pytest_output`, `mock_service_url`, `deployed`,
`last_deploy_error`) all live under `detail`.
A "done but not deployed → redeploy vs regenerate" decision reads
`status == 'done'` + `detail.deployed == false` + `detail.last_deploy_error`.

**`detail.gate_authority` and a withheld deploy are internal; read them, never render them.**
Read `gate_authority.findings` yourself on every terminal poll and dispatch each per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (on `authority` alone). An advisory finding rode an
env that shipped. An invariant one held work back, and `draft_ref` names the env that keeps it:
- `status='done'`, `deployed == false`, and an `invariant` finding whose `subject` is
  `scenario_state_schema.yaml`: the auto-deploy was withheld because scenario generation would
  refuse this env. Follow its `remedy`: repair the schema in an `/edit-env` session (its commit
  redeploys); never `deploy_env` it as-is to move on.
- `status='failed'` with an invariant finding: no per-tool record loaded. Follow its `remedy`: fix
  the tool descriptors its errors point at when they are this repo's input, else regenerate once
  silently; failing again: run the contact hand-off (`stop_message.py contact --done`).

`status='done'`, `deployed == false` and `detail.smoke_contract_verdict == 'blocking'`: the auto-deploy
was withheld because the mock failed env-contract checks, listed in `detail.last_deploy_error`. When
they name a mock defect, they feed the world's ONE `/edit-env` round (`/simulation-generate-suite`
Step 0c): open it with those lines; its commit redeploys. With no commit, `deploy_env(env_id=<id>)`
brings the world up as-is. Either way the checks still failing are a named risk, and the flow goes on
to Step 5; never ask, never loop.

Neither the finding, the withheld reason nor `gate_authority` reaches the customer: the progress
line says only that the mock is still being prepared.

**Even on a successful deploy, check `detail.smoke_contract_status` before trusting the world.**
It is the one automated post-deploy fidelity check this pipeline runs, and it is populated
UNCONDITIONALLY (deployed or not) — a string like `'<N> passed / <N> failed / <N> advisory / <N>
not run'` on a normal run. If it instead starts with `missing:` or `not_run:`, the check never
proved anything about this mock (a report write that silently failed, or a gate that never ran) even
though `deployed == true` and there is no `last_deploy_error`. Don't fail the flow over it — just
don't tell the user the mock was validated when this field says it wasn't; mention it plainly if the
user asks why a downstream run behaved oddly. When `smoke_contract_verdict` is `blocking`, the world
is up over failed checks: never say it is validated or answering for the tools those checks name;
name them (the customer's own tool names) as not yet served. Check ids and `last_deploy_error` stay
internal.

**Slow is NORMAL — do not diagnose a long-running job as stuck.** Env-gen runs
a long multi-turn job on the backend. Model calls can be rate-limited, so the job can legitimately take **many minutes
with the CPU idle** while it waits on the model. This is expected, NOT a failure.

Use the envelope's **`seconds_since_progress`** — the ONE liveness clock — to tell
*slow-but-progressing* from *stalled*. (`detail.seconds_since_last_activity` is a
retired mirror of it, kept only for older clients; never read it instead.)
- **small / recent** (e.g. < ~120s) → the job is alive and advancing — **keep
  polling**, just widen the interval (e.g. 15-30s) so you don't churn. A large
  `elapsed_seconds` ALONE is fine.
- **`generating` and `verifying` are SILENT while a tool runs and for the whole of the
  contract pytest** — nothing reaches the progress clock during either — and `deploying`
  (the env-contract gate; the job is not done until it decides) ticks only once per check,
  so the threshold for those three phases is **30 minutes**, not 15. `poll_job.sh` applies it for you, and
  the envelope's `progress_kind` says what the last tick was (`stream` / `tool_execution`
  / `subprocess`). **A large counter with phase still `generating`/`verifying`/`deploying` and no NOTE
  printed is a HEALTHY job.**
- **past that threshold AND still growing across several polls** → only THEN treat it as
  genuinely stalled.

Do **NOT**, on a slow-but-progressing job: declare it "stuck", "rate-limited",
or "blocked on auth"; kill or restart the backend diagnostic server; or relax
downstream assertions. None of those help — they just lose in-flight work. The
backend owns its own model access; the plugin neither sets it nor needs to reason
about it. Slowness is a throughput property of the backend, not a
bug to fix from the plugin side.

**Render to the user on every status change** (not every poll — that floods the terminal). One line per transition, e.g.:

```
[12:34:11] queued — backend has the job, waiting for an asyncio slot.
[12:34:14] generating — env-gen agent producing simulation_env/ files.
[12:36:02] verifying (iteration 1) — pytest contract tests running.
[12:36:48] verifying (iteration 2) — still hardening the mock, 1 check failing.
[12:38:12] done — mock service at http://backend.example.com:8217
```

(`phase` moves through `generating` → `verifying`; the mock is deployed as part
of reaching `status='done'`, and the URL appears on `detail.mock_service_url`.)

When `detail.last_pytest_output` is populated and changes between polls, read it YOURSELF to track why iterations are happening — pytest/stderr output is internal. Show the user only a passive plain-language progress line (e.g. "still hardening the mock — N/M checks passing"), never the raw tail.

If `status='failed'`, read the `error` field YOURSELF to classify — never show it to the user. Modes and what YOU do:
  - `port allocation failed: ...` — backend's port range is exhausted; not fixable from here, and not a fault Plurai must look at: run the unavailable stop (`stop_message.py unavailable --done`). A job envelope that carries the typed `unavailable` block renders the same stop through `refusal --refusal`.
  - `mock service ... did not pass healthcheck within 30s` — the generated mock crashed on startup; `error` includes the stderr tail. Read the tail yourself to classify; the mock is generated fresh each run, so retry once, silently (undeploy the failed id, then `generate_env_start` again); if it fails again, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`). Don't show the stderr.
  - `RuntimeError: ...` from the generator — usually a provider 5xx or timeout, already retried inside the job: do not retry. No repair step here: run the contact hand-off (`stop_message.py contact --done`).
  - `cancelled before completion` — the generation was stopped before it finished: `delete_artifact` was called on the id, or the backend shut down mid-run. `undeploy_env` never causes this, because it does not touch a running generation. Not a real failure. Say plainly that the job was cancelled and ask whether to start a fresh one.

### Step 5: Save the mock service URL

On `status='done'`, write the RESOLVED absolute URL (Step 5.5) to `<plurai_dir>/.mock_service_url` (one line, no trailing newline). This file is the contract between `/generate-mock-env` and `/simulation-run` (Step 0 in that skill reads it; Step 3 passes it to `plurai-run-simulation`):

```
http://backend.example.com:8217
```

**Don't `git add` it** — it's a per-run artifact tied to one backend job, not project state. Nothing to edit in any `.gitignore`: the repo-root managed block (`/simulation-scan-agent` Step 5) ignores it already, as machine-local runtime state. If the repo's block is missing or its BEGIN line carries an older rev, re-run `/simulation-scan-agent` Step 5.

**Also write the env id — this is the handle everything downstream re-deploys by.** The `env_session_id` the Job envelope returned IS the persistent `env_id`, and the mock DEFINITION lives durably on the backend, so this file is what lets `/simulation-generate-suite` Step 0c re-deploy the existing world in seconds instead of paying for a second generation. Write it self-resolving — the harness resets the shell between Bash calls, so a `$WORKDIR` set in an earlier snippet is EMPTY here and `printf … > "/.env_id"` would land at the filesystem root while this skill reports success:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; printf %s "<env_session_id>" > "$WORKDIR/.env_id"
```

`.mock_session_id` (below) carries the same value and stays for the teardown path in the failure table; **`.env_id` is the canonical handle** — `/simulation-generate-suite` Step 0c re-deploys from it, `/manage-envs` re-materialises it on an env switch, and `/edit-env` and `/analyze-traces` check it before falling back to the manifest / `list_artifacts`.

**Environment manifest (if this repo uses named envs).** The `env_session_id` the backend returned IS the persistent `env_id` — the mock definition now lives durably on the backend and is re-deployable by id (it no longer expires after a TTL; only the live deployment does). If `plurai_sim/envs/` exists (a named-env repo), record the id into the active env's manifest so the env can be re-activated later:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1   # $SERVER_URL — the configured backend
$PY "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" \
    write "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" --env-id <env_session_id> \
    --backend-url "$SERVER_URL"
```

See the `manage-envs` skill for the layout + resolver. If the repo has no `envs/`, skip this — the bare `plurai_sim/` flow is unchanged.

### Step 5.5: Join the mock path to the resolved server base

`detail.mock_service_url` is a server-RELATIVE path: `/mock/<env_session_id>/`.
The backend deliberately never mints a client-reachable absolute URL — behind a
fronting router it cannot know one; YOU hold that knowledge, as the configured
backend (the saved login's url). Resolve before saving:

1. Base = `SERVER_URL`:
   `source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1`.
   It sources the resolver for you, so `SERVER_URL` still comes from
   the configured backend (the saved login's url, minus a trailing `/mcp`). **The `|| exit 1` is load-bearing**: the
   resolver exports `SERVER_URL` BEFORE it refuses a non-loopback base with no
   bearer, so a step that sources it bare saves a URL the run cannot reach and
   hides the remediation the resolver just printed. **Call `backend_identity()`
   and paste its object** — the URL written below is read back as fact by
   `/simulation-run` and `/simulation-diagnose`, so this is the one step where an
   ambient host becomes a persisted one; the same call also arms
   `$PLURAI_DIR/.backend_identity` for every later helper in this session.
2. Save `${SERVER_URL%/}<detail.mock_service_url>` — the WHOLE base, trailing
   slash trimmed, joined with the relative path, **path prefix included**. A base
   is not always just a scheme and a host: behind a fronting router it carries a
   versioned prefix like `/v1/tenants/acme`. Rebuild the URL from scheme+host
   alone and that prefix is gone, so every mock request 404s at the router.
3. If `detail.mock_service_url` is NOT a leading-`/` relative path (e.g. an
   absolute `http(s)://...` URL), the backend predates server-relative mock paths — **surface an
   error and STOP; do not save.** Treat it as a fault on our side: run the contact hand-off (`stop_message.py contact --done`); the backend needs
   an update to a build that returns a relative path; a silently-saved absolute would mask the
   version skew.

The resolved URL rides the backend's `/mock` reverse proxy, so
`plurai-run-simulation` reaches the mock from any machine the diagnostic URL
works from — same-machine and cross-machine alike, no tunnel, no loopback
caveat. On a hosted backend the customer must be logged in
(the saved Plurai login) so mock requests clear the bearer auth (the SDK sends the saved
token automatically); confirm the login if they hit 401s.

**Driven by `/simulation-generate-suite`?** Skip this block entirely — the driver owns the hand-off and reports one progress line of its own. Printing a "next step" here tells the customer to run the command that is already running, and leaks a backend id into a product message. **Standalone**, tell the user the block below (name actions, never backend tool names). `<contract line>` renders the detail's `verification` in plain terms, with the counts and tool names taken from `verification_detail`: `passed` → "Its contract tests passed."; `partial` → "<D> of <M> contract tests passed; <K> could not run because our backend could not reach its model, so <tools> are not yet verified — that is ours to fix; if it persists, reach out at support@plurai.ai or on Discord: https://discord.gg/bg34gh76uJ."; `failed` → "<F> of <M> checks on this mock failed (your contract tests and the checks generated with it, counted together); /edit-env repairs it."; `not_run` → "No contract tests ran against it." Never paste `verification_detail` itself: it names backend settings the customer cannot change.

```
Mock service ready at <url>.

This skill stands the world UP and proves it is alive (the deploy
healthcheck). <contract line>
It does not judge
whether the simulation produces sharp signal: that is decided ONCE, by
/simulation-generate-suite's quality loop, against the scenarios and
evals that actually ship. (The env-gen smoke scenarios from Step 6 are
an inline preview only — they are not run client-side.)

Next step:
  → /simulation-generate-suite — it re-deploys this world by id in
    seconds, then builds and qualifies the suite on it.

When you're done, ask me to remove the deployed environment; that
frees the mock service and its port.
```

Save the `env_session_id` to `<plurai_dir>/.mock_session_id` so a manual teardown call (or a future iteration of this skill) can target the right session. One line, no trailing newline.

### Step 5.7: Pre-warm the dimension-graph cache (background, fire-and-forget)

The next scenario generation — `/simulation-generate-suite` Step 1, or a direct `/generate-scenarios` — spends a minute or more building the dimension graph, whose result depends only on `(variables_yaml, prd_markdown, scenario_state_schema_yaml, inputs_schema_yaml, outputs_schema_yaml)` — all five are the cache key, so send each one the generation will send. Fire it in the background as soon as Step 6.4 has persisted the schema and Step 6.4b's annotation rule has run on it (the schema bytes are part of the key). **Invoked standalone**, the customer's reading time covers the build. **Driven by `/simulation-generate-suite`, SKIP this step entirely** — only the remaining preflight would overlap it (the health probe, the run-readiness re-check, the wall-clock statement), so the prewarm is still building when the generation that would have used its cache starts and the graph builds twice; `generate_scenarios_start` pays the `building_graph` cost itself, exactly as it did before this call existed. Never block on it.

**Pre-authorized by the invocation — never ask consent for it.** The pre-warm ships the
variables, the PRD and the state schema to the backend exactly as the generation it warms
does; the harness's tool-approval prompt is the chokepoint, and a background cache warm is
never a decision to put to the user.

Call the MCP tool:

```
prewarm_dimension_graph_start(
    variables_yaml=<full text of <plurai_dir>/scenario_variables.yaml>,
    prd_markdown=<full text of <plurai_dir>/simulation_prd.md>,
    scenario_state_schema_yaml=<full text of <plurai_dir>/scenario_state_schema.yaml; omit when absent>,
    inputs_schema_yaml=<full text of <plurai_dir>/inputs_schema.yaml; omit when absent>,    # /generate-scenarios passes both
    outputs_schema_yaml=<full text of <plurai_dir>/outputs_schema.yaml; omit when absent>,  # whenever they exist
    workspace_id=<bench workspace id>,  # the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
    ledger_version=<the pin Step 3 sent to generate_env_start>,  # REQUIRED when the tool list names it; omit otherwise
)
```

When `prewarm_dimension_graph_start` names `ledger_version` in your tool list, send the SAME pin Step 3 sent
(the settled grounding job's `detail.ledger_version`): the graph is built over the facts the scenarios are then
generated from. A pre-warm never waits for a pin and never drops the argument — without a pin, skip it; it is an
optimization.

This is **fire-and-forget**:
- Proceed immediately to Step 6 (Tier 2 smoke) and the hand-off. Do NOT block on it.
- The tool returns a generic **Job envelope** immediately. A cache MISS spawns the
  background build (`status='running'`, `phase='preparing'`/`building_graph`). A cache
  HIT is a born-terminal Job: `status='done'`, `phase='cache_hit'`, and
  `detail.cache_hit=True` (no work spawned). Branch on `detail.cache_hit is True`
  (preferred) or `phase == 'cache_hit'` — `status` is `done` for both hit and miss
  completion, so do NOT key on a `status == 'cache_hit'` literal (it no longer exists).
  Read the `prewarm_id` from `Job.id` (or `detail.prewarm_id`, equal).
- The cache file appears on disk when the build completes; the next `generate_scenarios_start` call automatically finds it. No polling needed.
- Polling `job_status(id=prewarm_id)` returns the same Job envelope; it's optional, for diagnostics only.

If the call raises (e.g., the backend isn't scenario-storage-configured), log it but DON'T fail the skill — the customer's later `/generate-scenarios` will just pay the 60-90s build cost as before. The pre-warm is an optimization, not a correctness requirement.

### Step 6: Retrieve env-gen outputs (state schema + smoke preview)

Once the URL is saved, the final **Job envelope** was already returned by the last `job_status(id=env_session_id)` call when `status='done'` — read `detail.scenario_state_schema_yaml`, `detail.smoke_scenarios`, and `detail.smoke_scenarios_dropped` from that response (the env summary fields all ride `detail`). `detail.smoke_scenarios` maps `{scenario_name: {filename: contents}}` for each Tier 2 smoke scenario the backend produced (default: 2 scenarios). The mock subprocess remains deployed until you explicitly call `undeploy_env(env_id=<saved>)` (SIGTERM + grace + SIGKILL, freeing its port) — ending an env no longer auto-undeploys. The env *definition* persists, so the mock is re-deployable any time via `deploy_env(env_id=<saved>)` under the **same stable `/mock/<env_id>/` URL** (idempotent — no regeneration). When you are finished with the mock and want to free its port, call `undeploy_env(env_id=<saved>)`; to re-deploy before `/generate-scenarios` → `/simulation-run`, call `deploy_env(env_id=<saved>)`.

**There is no client-side Tier 2 smoke run.** `detail.smoke_scenarios` are returned INLINE in the done `job_status` Job envelope and are never registered as a backend scenario set — they have no `scenario_set_id`. The `plurai-run-simulation` CLI resolves scenarios ONLY by `--scenario-set-id` from backend shared storage (there is no `--scenarios <local dir>` flag), so the inline smoke scenarios cannot be run. They serve as a human-readable PREVIEW of the kind of stateful flow env-gen could derive; the real qualification run happens in `/simulation-generate-suite` against a generated set, which IS by-id-resolvable.

### Step 6.4: Persist the state schema (REQUIRED for stateful agents)

The done `job_status` Job envelope carries `detail.scenario_state_schema_yaml: str | None` — the entity/agent_context contract env-gen derived for this agent. **When it is non-null, write it verbatim to `<plurai_dir>/scenario_state_schema.yaml`** (no trailing-newline fuss; write the contents as-is).

This is the seam that makes scenario seeding work. `/generate-scenarios` reads `<plurai_dir>/scenario_state_schema.yaml` and passes it as `scenario_state_schema_yaml=` to `generate_scenarios_start`; the backend then seeds each scenario's `initial_world_state.yaml` from it. **Skip this save (or read it off `detail.scenario_state_schema_yaml` as null) and a stateful agent gets empty-world scenarios** — `plurai-run-simulation` will hard-fail them with `ScenarioEmptyWorldError` (the loader refuses to run a stateful agent against an empty world), or, worse on older loaders, run them to impossible-request "failures" that look like agent/eval bugs.

Unlike `.mock_service_url`, this file IS durable project input (it's the agent's state contract, not a per-run handle) — it's fine to leave it tracked. When `scenario_state_schema_yaml` is null, env-gen produced no schema (stateless agent) — skip the save; `/generate-scenarios` proceeds schema-less, which is correct for agents whose tools self-create their entities.

### Step 6.4b: Document generation for a stateful document agent (optional)

Document generation is asked + configured during `/simulation-setup` (it writes the universal trigger `plurai_sim/.document_fields` — see its post-setup section). The marker is the CONTENT trigger and works for any agent shape (a pipeline extractor that skips this whole skill uses the same file). What it cannot carry is OWNERSHIP: `owner: user` — the key that tells the user simulator it is holding the document — and `inject` live in the schema, and Step 6.4 writes the backend's bytes VERBATIM, so on this branch nothing declares them unless this step does.

**So every Step-6.4 write is followed by the document-field annotation rule**, the section "Applying the document-field annotation rule" below Step 7 — apply it. It is idempotent: a field already carrying `generated_document` is left byte-identical, a workdir with no `.document_fields` (or none naming a field of this schema) leaves the file byte-identical to what Step 6.4 wrote, and the same rule re-applies unchanged on every re-run of this skill, which overwrites the file each time. The rule also states the two consequences to pass on to the customer (turning documents off now edits the schema too; a marker rename that leaves the block behind generates a second document).

The case this step has always existed for, unchanged: a **stateful** document agent whose document content lives in an `agent_context_fields` field of the schema you just saved (Step 6.4), where the exact field name only became concrete now. If the user opted into document generation at setup, confirm `plurai_sim/.document_fields` names that field — write/adjust it (a JSON list, one entry per document field):

```json
[{"field": "raw_text", "format": "txt", "brief": "the plain text of a single vendor invoice — header, line items, totals"}]
```

- **`field`** — the `agent_context_fields` field that holds the document content.
- **`format`** — pick by what the agent reads: `"txt"` for a text extractor, `"image"`/`"pdf"` for a vision/OCR tool (the rendered binary is delivered as a base64 data URI the harness decodes to bytes for the real vision call), or `"docx"`/`"xlsx"`/`"pptx"` for a Word/Excel/PowerPoint reader (a real, library-openable file authored directly). Any other extension the agent's own reader expects works too — no closed list to match. `formats` (plural) declares more than one for the same field; the scenario generator then picks one per scenario instead of fixing one for the whole agent.
- **`brief`** — one line naming the document type + the fields it should contain; per-scenario variety comes from the scenario prose.

`/generate-scenarios` reads `.document_fields` (its Step 4) and turns document generation on for those fields; the schema's `generated_document` blocks are the other half of the same trigger, which is why the annotation above and this file must name the SAME fields. If the user did not opt in (no document agent), skip this — nothing changes, byte-identical. (Requires the backend's document-generation support.)

### Step 6.5: Handle dropped-scenario diagnostics (internal — do not paste)

The done `job_status` Job envelope also carries `detail.smoke_scenarios_dropped: list[tuple[name, reason]]` — scenarios the generator wrote on disk but the loader rejected (malformed YAML, missing required fields, oversize files). Read the entries YOURSELF; loader-error lines (`ScenarioLoaderError: ...` and the like) are internal and never go in the chat panel. Remediate silently where possible: the remaining `smoke_scenarios` (if any) plus the contract-test verify phase already prove the mock works for the declared tool surface, so a partial drop usually needs no user action and no mention. Only if the drop materially limits the deliverable (e.g. NO preview scenarios remain) and you can't fix it from here, state the impact in ONE plain sentence — e.g. "the preview scenarios didn't pass validation, so there's no smoke preview this time; the mock itself is verified and the suite build is unaffected" — optionally offering a re-run of `/generate-mock-env` for a fresh preview.

If `smoke_scenarios_dropped` is empty, skip this sub-step entirely.

If `smoke_scenarios` is non-empty, surface them as a read-only PREVIEW — do NOT attempt to run them:

1. Optionally write each entry to `<plurai_dir>/smoke_scenarios/<scenario_name>/<filename>` verbatim so the customer can inspect or hand-edit them. These files are a reference artifact only; the run path is the suite's generated set, not these dirs.
2. Show the user a one-line summary per scenario (read `content:` from each `scenario.yaml`) so they get an early sense of the stateful flows env-gen derived from their tools.
3. If the backend returned FEWER than 2 scenarios, read the "## Smoke scenarios" section of `generation_report.md` YOURSELF and tell the customer in one plain sentence why coverage is limited. Do not paste the report text; do not fabricate the missing scenario locally.

**Why no run here.** `smoke_scenarios` come back inline in the done `job_status` Job envelope and are never registered as a backend scenario set (no `scenario_set_id`). `plurai-run-simulation` resolves scenarios ONLY by `--scenario-set-id` from backend shared storage — there is no `--scenarios <local dir>` flag — so these inline scenarios are not runnable on the SDK-only customer machine. The first real simulation run is `/simulation-generate-suite`'s qualification run, which runs a generated set by id.

If `smoke_scenarios` is empty (env-gen couldn't produce stateful flows from the declared tools), read the "## Smoke scenarios" section of `generation_report.md` YOURSELF and tell the user in plain terms: "no preview scenarios were generated this time — <one-sentence plain reason>. The suite's qualification run is the first real simulation run." Don't paste the report; don't fabricate scenarios locally.

### Step 6.6: Seed accepted + read back (after every deploy)

`detail.seed_probe_world` (from `job_status(id=<env_id>)`) is one world carrying every field the env's
schema declares, each row list holding a row with every declared row field. Feed it through the
harness's own `seed_state`, here and after every `deploy_env` or `/edit-env` finalize, before any
scenario draw:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
# First write detail.seed_probe_world, verbatim JSON (null included), to "$WORKDIR/.seed_probe_world.json".
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" \
  "$WORKDIR/simulate.py" --seed-world "$WORKDIR/.seed_probe_world.json"
```

It prints two separate facts: `✓ seed accepted` (`seed_state` took the world) and `✓ seed read-back OK` (the
Init's own post-seed read returns it as seeded). Never report acceptance alone as a round-trip.

- **exit 0** — accepted and read back, a finding is overruled, or `not_applicable` (no `seed_state`, no world schema, or the bench intercepts its tools
  at the agent's host — its route smoke owns that route). Proceed.
- **exit 1** — `seed_state` refused a world built on the env's own smoke scenario. Record the verdict line —
  `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase world --status landed --set env="$(cat "$WORKDIR/.env_id")" --note "seed check: <the verdict line>"` —
  and proceed: no `/edit-env`, no repair, no overrule. Inside `/simulation-generate-suite` its sanity pass Inits the REAL
  qualification seeds, and an Init refusal there is the repair; run standalone, name it as a risk in the summary.
  Never coerce the value in `simulate.py` to make it pass.
- **exit 1, `plugin.smoke.seed_readback_gap` / `reset_leftover`** — accepted but not read back as seeded (rows
  missing, fields the read never returns, fields changed, or the read raised), or a probe row `reset()` left behind.
  Advisory: route it to the harness lane of `/analyze-simulation` for ONE repair round, then re-run
  `bench_manifest.py verify --mode reuse --after-edit` (its `seed_readback` check). Still standing: overrule it with the
  reason (`gate_ledger.py overrule`) and name it as a risk in the summary ("scenarios that seed `<subject>` will be voided").
- **exit 2** — no verdict: the backend published no world, the harness is not Python, it did not load
  (the Init smoke owns that fix), Init fails on an empty world too (a run prerequisite, not the world),
  or it refused a world it is not trusted to block on — one built from the schema alone, or the grounded
  world's perturbed twin (the schema's own row values), or the read-back has no verdict (`no read path`, or an SDK
  without `plurai.seed_landing`). Record the line, name it in the operator summary, and proceed.

**Dependency: the agent's own imports.** `verify_smoke.py` loads `simulate.py`, so `$PLURAI_PY` must be
able to import what the harness imports (a harness built on the Claude Agent SDK needs
`claude_agent_sdk` installed in that interpreter). A `ModuleNotFoundError` in its output is exit 2:
no verdict about the env, only about the interpreter. Install the missing package into the project
venv and re-run; if that is not possible, take the real-request probe below as the proof and say
the seed check did not run.

**Then witness the mock's per-tool contract.** Fetch the world's witness tests into `$WORKDIR/mock_witness/` with
`fetch_env_file.py` (below; never through `fetch_artifact`: it pages files over the wire cap, and a `NOT_FETCHED`
line is a recorded coverage gap, never a stop). The contract and witness tests run against this mock under the plugin's own pytest
interpreter, with the bearer only for the pinned backend; `mock-gate` then reads each mocked tool witnessed, waived or
WARN, and never blocks: at most one `/edit-env` round for a WARN line that names a mock defect, the
world's one round, shared with an env-contract withhold's, as `/simulation-generate-suite` Step 0c says.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_env_file.py" --env "$(cat "$WORKDIR/.env_id")" --out-dir "$WORKDIR/mock_witness" \
  --path simulation_env/tests/test_mock_witness.py --path simulation_env/tests/_mock_witness.py \
  --path simulation_env/tests/mock_witness.json
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_env_file.py" --env "$(cat "$WORKDIR/.env_id")" --out-dir "$WORKDIR/mock_witness/backend_run" \
  --path contract_report.json --path contract_answers.json --path simulation_env/tests/test_mock_contract.py
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" init
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse --only mock_contract
"${PLURAI_CHILD_PYTHON:-$PLURAI_PY}" "${CLAUDE_PLUGIN_ROOT}/scripts/mock_shape_probe.py" --workdir "$WORKDIR"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" mock-gate
```

`mock_shape_probe.py` reads what the mock answered in those tests against the agent's own response types. A
`mock_shape_mismatch` WARN names the tool and the field; before changing the mock in that round, read the tool's
pass-through in `simulate.py` and the row in the named test, since a pass-through that reshapes the answer, or a test row
missing a required field, is no mock defect. A tool it reads as unverified was not checked, which is not a pass.

### Step 6.7: Real-request probe (the proof the mock answers)

Send the deployed mock real requests with `probe_mock.sh` (it runs `scripts/probe_mock.py`, which speaks
the mock's own wire). The wrapper runs it under the plugin's fixed python and hands it the backend bearer
through the environment, so the token is never in a command line and no `python3` from `PATH` sees it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/probe_mock.sh" "$(cat "$WORKDIR/.mock_service_url")" \
  --tool <a tool name from plurai.config.yaml `tools:`>
```

- **exit 0** — `/health`, `/session/create` and `/mock/execute` all answered. The mock is alive. An
  empty result list from an unseeded world is a normal answer, not a defect.
- **exit 1** — `/health` did not answer `ok`: the mock is not serving. `deploy_env(env_id=<id>)`
  once more; if it still fails, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`).
- **exit 3** — a request the mock should serve failed; the printed line names which one and the
  HTTP status. Classify it yourself per the Error posture.

**Never probe a path you guessed.** The mock serves `GET /health`, `POST /session/create` and
`POST /mock/execute` (the wire the SDK uses). Any other path answers `Unknown: POST /<path>`, and
that answer is the proof only that the path is wrong, never that the mock mounts no handlers. Do
not report a platform or framework defect from guessed-path results. `diagnose_mock` reads a failed
run's facts (scenario name and error class), so a probe has nothing to give it: do not call it for
one.

The probe's output carries the mock's address and internal details: read it, do not relay it. Tell
the user the env is deployed and answering, with the public mock URL from `.mock_service_url` only;
when `smoke_contract_verdict` is `blocking`, name the tools not yet served instead of calling them
answering (the `smoke_contract_status` rule in Step 4).

### Step 7: none — quality is decided ONCE, and not here

This skill's job ends when the world is deployed and PROVEN ALIVE: the deploy healthcheck, the backend's pytest contract loop, Step 6.6's seed check (accepted + read back) and Step 6.7's real-request probe. It does not qualify the simulation. (Step 6's smoke scenarios are an inline PREVIEW — they are never run client-side.)

There is exactly **one** quality gate in the product, and it lives in `/simulation-generate-suite` Step 3: an independent critic judging a live run of the scenarios and evals that actually SHIP. A second ramp here would generate a throwaway 3-scenario set, run it, and re-ask minutes later the question that gate answers on the real machinery — paying twice for one answer inside a command already measured in hours. That gate also owns MOCK defects: it patches the env with `/edit-env`, verifies the patch by querying this deployed mock directly, and batches those fixes into one re-run.

So when this skill finishes, hand off — do not qualify. **Deploy and prove alive here; judge there.**

### Applying the document-field annotation rule

<!-- BEGIN shared:document-field-annotation-rule copy: generated from the extract-scenario-variables skill; edit there -->
This rule hangs on the ACT, not on this branch: **whenever `<plurai_dir>/scenario_state_schema.yaml`
is written — hand-authored here, or the backend's `detail.scenario_state_schema_yaml` written
VERBATIM by another skill — every field named in `plurai_sim/.document_fields` carries exactly this
block, and the write is not finished until it does.** The writers of that file apply this rule:
`/generate-mock-env` Step 6.4b, `/simulation-generate-suite` Step 0c
(both the deploy branch and the on-disk bullet), `/edit-env`'s schema note.

```yaml
agent_context_fields:
  <field>:                # the marker entry's `field` — whatever the schema calls it
    # ...the field's own keys, untouched...
    generated_document:
      format: <format>    # the marker entry's `format`, copied
      brief: <brief>      # the marker entry's `brief`, copied
    owner: user           # what the user simulator's hold-set reads: WITHOUT this key the
                          # simulator is never told it is carrying the document
    inject: request       # the channel that delivers the value into the turn-1 payload
```

**Idempotent, and scoped to what is declared.** A field that already declares `generated_document`
is left BYTE-IDENTICAL — re-applying the rule is a no-op, and a writer must never reflow bytes it
did not author. Of the other two keys only the ones the field does not already declare are added: a
field that already says `inject: [request, store]` because it also seeds world rows keeps exactly
that. A field the marker does not name is untouched; with no `.document_fields`, or with no schema
field of that name, the file stays byte-identical to what the writer produced. Only the three keys
above are added, and `format` / `brief` are COPIED from the marker entry — never re-worded here, and
never invented for a field the marker does not declare. **One shape is a contradiction, not a
merge**: a field the marker names that already declares an `owner` OTHER than `user` is the marker
and the schema disagreeing about who carries the document — say so and stop; do not overwrite the
authored declaration.

**Three consequences, true the moment a schema carries the block — say them once to the customer.**
The backend unions the two triggers (it collects `generated_document` blocks from the schema, then
lets an explicit `document_fields` entry win by field name), so (1) turning document generation OFF
now means removing the block from the schema AS WELL AS the entry from `.document_fields` —
deleting the marker alone no longer stops it; and (2) renaming the field in the marker without
renaming it in the schema declares TWO document fields, so two documents are generated and billed,
one of them into a field nothing reads. And (3) ownership is read PER SCHEMA, not per field: once
any field says `owner: user`, every user-voiced `agent_context` fact in that scenario is handed to
the user simulator — including host-owned session context that is not printed on the document. A
fact the AGENT must produce is still withheld (that condition is the voicing one, untouched), but
a schema mixing a document with host context hands the simulator more than the document.
<!-- END shared:document-field-annotation-rule -->

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

## Hard rules

- **Deploy only an env this project generated.** Never adopt an env from `list_artifacts` — see the Step 0 gate. A schema match proves nothing about who generated it.
- **Never qualify the bench here, and never send the user straight to `/simulation-run`.** A deployed mock is not a qualified simulation: the verify phase proves the mock RESPONDS, nothing more. The next step is always `/simulation-generate-suite`, which re-deploys this world by id in seconds and then qualifies the suite once, on the scenarios and evals that ship. Offering "run the full eval batch" from here puts the customer on an unqualified bench and repeats the same infrastructure failures at N-scenario scale.
- **Poll on a MINUTES cadence (~1-3 min between reads).** The job is
  server-side and minute-scale, so a seconds-cadence loop wastes the backend's
  handler budget and cannot be honoured anyway — Claude Code blocks foreground
  `sleep`. See `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` for the exact shape. What the user
  needs is a rendered update each time you DO read, not a faster loop.
- **Don't re-trigger generate_env_start on an in-flight session.** If the customer asks to "regenerate," call `undeploy_env(env_id=<old>)` first and read its answer before starting a new one. Starting a second job while the first can still deploy leaks a deployed mock service and its port.
  - `outcome` is `torn_down` or `not_deployed`, and `generation_in_flight` is `false` → nothing of the old env is running; start the new generation.
  - `generation_in_flight` is `true` → `undeploy_env` did NOT stop it: the generation keeps running and deploys its mock when it finishes. Call `delete_artifact(id=<old>)` to cancel it, then start the new generation. The customer's request to regenerate is the confirmation here: an in-flight generation has no finished definition to lose.
  - `outcome` is `unknown_env` → this backend holds no env with that id (deleted, expired, or minted against another backend). Clear the stale marker and start the new generation.
- **Don't try to access `.mock_service_url` on the BACKEND filesystem.** The URL is the only handle the customer needs; the workspace dir lives on the backend, not on their machine. There is no file-download path for the generated `simulation_env/` source files. (The dual-agent diagnostic protocol covers per-file inspection via `simulation-diagnose`.)
- **Don't generate the environment locally instead.** Generation needs model credentials that customers should not need to provision. If the backend is unreachable, run the unavailable stop (`stop_message.py unavailable --done`) after the retry rule — never a local fallback.

## Failure modes + recovery

This table is CLASSIFICATION material for you, not text to relay — apply the Error posture section (the user sees nothing, a plain-terms ask, or, when nothing the table names fixes it, run the contact hand-off (`stop_message.py contact --done`)):

| Symptom | Likely cause | What YOU do |
|---|---|---|
| `generate_env_start` raises ValueError("env_jobs_workspace_root is not configured") | The backend isn't env-gen-enabled. | No repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| `generate_env_start` raises ValueError("Failed to parse uploaded inputs: ...") | YAML or PRD doesn't parse. | Read the parser error yourself; if you can regenerate the input (re-run `/simulation-scan-agent`), do it; else describe in plain terms what in the config/PRD to fix. Don't paste the error. |
| `status='failed'` with `error='port allocation failed: ...'` | Backend's port range exhausted (lots of stale deployments). | Not fixable from here, and not a fault Plurai must look at: run the unavailable stop (`stop_message.py unavailable --done`). A job envelope that carries the typed `unavailable` block renders the same stop through `refusal --refusal`. |
| `status='failed'` with `error='mock service ... healthcheck'` | Generated mock crashes on startup. The stderr tail in the error message names which import / config failed. | Read the stderr tail yourself to classify; the mock is generated fresh each run, so retry once silently; if it fails again, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`). Don't show the stderr. |
| `status='failed'` with a provider 5xx or timeout | The backend already retried it inside the job. | Final: do not retry. No repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| Stale `env_session_id` in `.mock_session_id` (`status='failed'` with "not found or expired") | TTL fired or the backend restarted. | Just start a new job silently; no recovery needed for the old one. |

## Glossary

- **env-gen job** — one asynchronous backend task that runs the generator + deploys the mock. Identified by `env_session_id`.
- **mock service** — the running mock the backend deploys for an env and serves under `/mock/<env_id>/`.
- **mock_service_url** — the URL the customer's `plurai-run-simulation --mock-service-url <url>` points at. Stored in `<plurai_dir>/.mock_service_url`.
- **workspace dir** — the per-session directory on the backend filesystem containing `simulation_env/` + `generation_report.md` + the uploaded inputs. The dual-agent diagnostic protocol (`simulation-diagnose`) operates on this same dir when failures originate from this run.
