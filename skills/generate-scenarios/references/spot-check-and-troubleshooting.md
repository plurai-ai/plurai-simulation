# Spot-check the set, failure modes, and glossary

Read this after Step 7 — before scaling to a full `/simulation-run`
batch. It carries the spot-check walkthrough (inspect the dimension graph → run
the spot-check → read what failed), the failure-mode recovery table, and the
glossary.

## Recommended next step: spot-check the set before scaling up

Right after `/generate-scenarios` completes, BEFORE handing off to
`/simulation-run` for the full batch, recommend the user
spot-check the new set against the customer's actual agent.

### Step 1: Inspect the dimension graph for rejection / boundary complications

The dimension-graph walker generates complication scenarios that are
DESIGNED to make the agent fail (e.g. a `missing_task` flow that
passes `task_definition: null` to test the agent's rejection
behaviour). When the agent correctly raises on these, the runner
records the raise as a scenario error — that's the agent behaving
as INTENDED, not a bug. The runner does not early-stop on failures; a
deliberate rejection writes its own `error.json` and the rest of the
batch keeps going, so a single expected failure never hides the others.

Look at the `detail.dimension_graph.flows` list from the done Job envelope
returned by the final `job_status` call, and classify each flow by the rule
below. Count how many scenarios target the complication flows
(`scenario.target_flows` intersects the complication-flow names) — that's your
expected-failure count for the batch, i.e. how many `error.json` files are
EXPECTED rather than surprising.

#### What counts as a complication flow — the ONE definition

**A flow is a complication when its `description` says it exercises a
deliberate-failure, boundary, or rejection path** — the agent is supposed to
refuse, error, or run into a limit there, so a scenario targeting it is DESIGNED
to fail. Judge what the description says the flow DOES; the id is not the
contract. This is the single definition of the term — every consumer resolves
here, including `/simulation-generate-suite`'s `core components exercised`
denominator (its Step 4), which is the count of the NON-complication flows.

Id prefixes — `reject`, `missing_`, `invalid_`, `empty_`, `boundary_` — are **a
hint that often but not always matches**; generator naming varies. One agent's
sole complication flow was named `unsupported_request_flow`: it matches no
prefix, so a name-keyed reading called all 8 of its flows core and computed `core
components exercised` against the wrong denominator. Where name and description
disagree, the description wins.

The `memory_dependent_reference` complication (injected when
`memory_challenge='elliptical'` was passed — always explicit; the server
default is `'off'`) is the opposite kind: a
**capability probe** the agent should PASS, not a deliberate-failure flow. Do
NOT add it to the expected-failure tally — a failure there is a genuine
memory gap (graded by `conversation_memory_judge`, which `/generate-evals`
emits only when the set actually plants probes), not a correct rejection.

### Step 2: Run the spot-check

The runner takes scenarios **by id** and runs the ones you name
(`--scenario-id`, repeatable). First **enumerate the set's scenario
ids**: call the `get_artifact_meta(<new_id>)` MCP
tool (it returns a Job envelope) and collect each `detail.scenarios[].name`.
For a spot-check, pick a small
subset of those ids (the ones you most want to sanity-check); pass every
id to run the whole set.

```bash
SCENARIO_SET_ID=<new_id>
AGENT_PATH=<customer-simulate.py>
OUT_DIR=/tmp/plurai-spotcheck-$(date +%Y%m%dT%H%M%S)

# Orchestration server URL: the plugin's canonical resolver
# (refuses a non-loopback URL when no token is available).
# Both resolvers must be sourced in THIS block — shell state does not persist between
# tool calls, and an unset $PLURAI_DIR silently reads /.mock_service_url (empty, no error).
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1

# Mock service URL: explicit --mock-service-url else the saved .mock_service_url.
MOCK_URL="${MOCK_URL:-$(cat "$PLURAI_DIR/.mock_service_url" 2>/dev/null || true)}"

# Eval set (optional): explicit --eval-set-id wins, else the saved .eval_set_id.
EVAL_SET_ID="${EVAL_SET_ID:-$(cat "$PLURAI_DIR/.eval_set_id" 2>/dev/null || true)}"

# SCENARIO_ID_FLAGS = one --scenario-id per name from get_artifact_meta, e.g.
#   --scenario-id generated_dataset_scenario_01 --scenario-id generated_dataset_scenario_02
# Write the flags LITERALLY — one --scenario-id per name from get_artifact_meta; do NOT
# expand a variable like $SCENARIO_ID_FLAGS (zsh does not word-split it — one glued
# argument). Append --eval-set-id "$EVAL_SET_ID" literally only when it resolved. NEVER
# pass the bearer token as an argument — the runner resolves the token itself.
plurai-run-simulation \
    --scenario-set-id "$SCENARIO_SET_ID" \
    --scenario-id <set>_scenario_01 --scenario-id <set>_scenario_02 \
    --agent-module-path "$AGENT_PATH" \
    --mock-service-url "$MOCK_URL" \
    --out "$OUT_DIR"
```

The runner is **concurrent** — a bounded `asyncio.gather`, capped by
`--concurrency` (default 4; pass `--concurrency 1` for serial). It does
not early-stop on failures: every named scenario runs, and each FAILED
scenario writes `<out>/<scenario_id>/error.json` while passing scenarios
produce none (per-scenario failures are isolated, so a crash never
poisons its siblings). So there's no failure-threshold to tune — you
triage from the `error.json` set after the run:
- **No complications expected** (pure happy-path batch): ANY `error.json`
  is a surprise — investigate before scaling.
- **Complications present**: expect roughly `<expected_failures>`
  `error.json` files from the deliberate rejections (Step 1); any
  EXTRA ones beyond that count are the real signal to chase down.

To keep the spot-check cheap, just pass a small subset of
`--scenario-id`s rather than the whole set. Use a FRESH timestamped
`--out` per run so the failure scan only sees this run's `error.json`
files.

### Step 3: Read what failed + why before scaling

For each failed scenario, read `<out>/<scenario_id>/error.json`
(`plurai-run-simulation` writes one per FAILED scenario). Categorize:

- **Deliberate failure**: the scenario's flow is a rejection complication
  and the agent raised correctly. ✅ expected; not a defect.
- **Unintended failure**: the scenario's flow is happy-path but the
  agent crashed / produced wrong labels / etc. ❌ block before scaling.

Why: the new `label_correctness_judge` policy (when emitted for a
classification-task agent) scores each sample's gold label against the
scenario's stated disambiguation rules. The spot-check surfaces
gold-label issues in seconds — BEFORE the customer pays for a
full-batch run + downstream signal built on bad labels.

When `detail.gate_authority.findings` on the done `job_status` envelope carries
coverage or task-definition findings, read them YOURSELF before
recommending the spot-check — they flag known-bad inputs the spot-check
will confirm in seconds, so use them to steer which scenarios you check.
Fix what you can from here (e.g. regenerate dropped/under-covered items)
without narrating; if a finding materially limits the set and can't be
fixed from here, state the impact in ONE plain sentence (no internal
jargon, no finding text); otherwise don't mention it.

**`plurai-run-simulation` selects scenarios BY ID.** `--scenario-id`
(required, repeatable) IS the per-scenario selector: enumerate the
set's ids via `get_artifact_meta(<id>)` (a Job envelope;
read `detail.scenarios[].name`), then pass
one `--scenario-id` per name. To run the whole set, pass every id; to
spot-check or re-run failures, pass just the subset you want — there's
NO separate run-all shortcut and no `--scenario-name` / `--filter`.
There is **no local-directory mode**. Scenarios are resolved by `--scenario-set-id` +
`--scenario-id` from the backend, never from a local folder, so you
cannot point the runner at a directory on the customer's machine. The
runner is concurrent (bounded `asyncio.gather`); the concurrency knob is
`--concurrency` (default 4, `1` = serial). There is no `--max-failures`.
Don't invent flags that don't exist.

## Failure modes + recovery

This table is CLASSIFICATION material for you, not text to relay — the user sees nothing (self-healed), a plain-terms ask, or, when nothing this table names fixes it, run the contact hand-off (`stop_message.py contact --done`)):

| Symptom | Likely cause | What YOU do |
|---|---|---|
| `generate_scenarios_start` raises `ValueError("scenario_storage_root is not configured")` | Operator's backend isn't scenario-gen-enabled. | No repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| `generate_scenarios_start` raises `ValueError("count N exceeds scenario_job_max_count=M")` | Backend caps single-batch size. | Lower `count` to M or below and retry silently; if the user genuinely needs a larger set, tell them to ask for a cap raise at support@plurai.ai or on Discord (https://discord.gg/bg34gh76uJ); a request is not the contact hand-off. |
| `status='failed'` with `error='scenario generation failed in <phase>'` | LLM-side issue or PRD parse failure. | Read the error yourself. Transient stall → retry once silently. Parse failure → describe in plain product terms what in the PRD to fix (don't paste the error). |
| `status='failed'` with `error='all N scenarios failed during evolution'` | LLM stall on every scenario (rate limit / thinking-mode stall on the backend's model). | Retry once silently; if it fails again, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`). |
| Stale `scenario_set_id` (`status='failed'` with "not found") | Server restarted and lost the in-memory entry, OR the customer's plurai_sim/.scenario_set_id points at a deleted set. | Start a new job silently; no recovery for the old one. |

## Glossary

- **scenario set** — one batch of generated scenarios identified by
  `scenario_set_id`. Persists on the backend as canonical GT.
- **dimension graph** — flow nodes + weighted edges derived from the
  PRD; the source of walks the scenarios are stratified over.
- **`/list-scenarios`** — sibling skill that renders per-scenario
  metadata for a set without running anything.
- **`/simulation-run`** — downstream skill that consumes
  the scenario_set_id and runs `plurai-run-simulation
  --scenario-set-id <id> --scenario-id <name>...` against the
  customer's agent.
