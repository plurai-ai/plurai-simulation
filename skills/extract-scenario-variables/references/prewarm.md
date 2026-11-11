# Step 7 — pre-warming the dimension-graph cache (pipeline benches only)

Read this at Step 7 when `plurai.config.yaml` declares `shape: pipeline`. On any other shape
there is nothing to do here and this file does not apply.

Nothing in it is a correctness requirement: the pre-warm is fire-and-forget, and skipping it
costs 60-90 s on the next `generate_scenarios_start` and nothing else. It is written down because
the fire-and-forget branch (cache HIT vs MISS) is the part that gets read wrong.

## Step 7: Pre-warm the dimension-graph cache (background, fire-and-forget)

When the customer's agent is **shape='pipeline'** (no tools to mock), the next step is `/generate-scenarios` directly — no `/generate-mock-env` runs in between. Inside `generate_scenarios_start` the backend spends ~60-90s on `building_graph` (three sequential LLM calls — flow_extraction + challenge_scoring + edge_inference) that depend on `(variables_yaml, prd_markdown, scenario_state_schema_yaml, inputs_schema_yaml, outputs_schema_yaml)` — ALL FIVE are in the cache key, and `/generate-scenarios` passes the last two whenever `plurai_sim/inputs_schema.yaml` / `outputs_schema.yaml` exist, which on a Pydantic-typed `invoke_pipeline` bench they do. Warm a key built from fewer fields and the generation job looks up a different one, so the 60-90s is paid anyway. Pre-warming this cache NOW — in the background, while the user processes the report from Step 6 — overlaps that cost with their natural pause, and the later `/generate-scenarios` call hits the warm cache and skips the build.

**Only run this step in the pipeline-shape flow.** If `/generate-mock-env` will run next (dialog-shape agents with tools to mock), skip this step — `/generate-mock-env`'s SKILL.md fires the pre-warm itself at Step 5.7 (with the env-gen-produced `scenario_state_schema_yaml` included in the cache key).

**Pre-authorized by the invocation — never ask consent for it.** The pre-warm ships the
variables and the PRD to the backend just as the generation call it warms does; the
harness's tool-approval prompt is the chokepoint, and a fire-and-forget cache warm is never
a decision to put to the user.

Call the MCP tool with **every input `/generate-scenarios` will pass**, or the warmed key is not
the one it looks up:

```
prewarm_dimension_graph_start(
    variables_yaml=<full text of <plurai_dir>/scenario_variables.yaml>,
    prd_markdown=<full text of <plurai_dir>/simulation_prd.md>,
    scenario_state_schema_yaml=None,
    inputs_schema_yaml=<full text of <plurai_dir>/inputs_schema.yaml; None when absent>,   # written by this command when simulate.py's hooks are
    outputs_schema_yaml=<full text of <plurai_dir>/outputs_schema.yaml; None when absent>,  # Pydantic-typed; /generate-scenarios passes both
    workspace_id=<bench workspace id>,  # the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
    ledger_version=<the pin, read as below>,  # REQUIRED when the tool list names it; omit otherwise
)
```

**`ledger_version`: a pre-warm never waits for one.** When `prewarm_dimension_graph_start` names a `ledger_version`
parameter in your tool list, the backend builds the graph over a SETTLED version of the agent's grounding ledger
and requires that id. Read the pin first:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pinned --workdir "$WORKDIR"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" waived --workdir "$WORKDIR"
```

`WAIVED until <date>` printed: the operator waived grounding for this workspace, and the backend runs a waived
workspace ungrounded only on a call that carries no `ledger_version`. Send the call with `ledger_version` omitted (the
one case where the argument is left out) and skip the steps that read a job: `pinned` prints nothing here, and neither
a `.ledger_version` left on disk nor a job recorded for another workspace is a pin.

An id printed is `ledger_version`, exactly as printed. Nothing printed means no suite has pinned one here: read the
job the last delivery recorded (`cat "$WORKDIR/.grounding_job"`) and `job_status(id=<the job id>)`; `status: done`
with `detail.settled: true` → the pin is `detail.ledger_version`, exactly as printed. Anything else (no job on this
machine, still running, not settled, failed, or a `ledger_version refused (<cause>)` answer) → skip the pre-warm and
say nothing: it is an optimization, and `/generate-scenarios` builds the graph itself. Pass the SAME id to
`/generate-scenarios`; the cache key is the content of the inputs, so it finds the warmed graph either way. Never
read the mirror to choose a version, never send "latest" and never drop the argument to get a call through.

This is **fire-and-forget**:
- Return control to the user immediately. Do NOT block on it.
- The tool returns a generic **Job envelope**. A cache MISS spawns the background
  build (`status='queued'`, which transitions `queued → running → done`); a cache HIT is a
  born-terminal Job (`status='done'`,
  `phase='cache_hit'`, `detail.cache_hit=True`, no work spawned). Branch on
  `detail.cache_hit is True` (preferred) or `phase == 'cache_hit'` — `status` is
  `done` for both hit and miss completion, so do NOT key on a `status == 'cache_hit'`
  literal (no such status exists).
- The next `generate_scenarios_start` automatically finds the cache file. No polling needed.

If the call raises (e.g., the backend isn't scenario-storage-configured), log it but DON'T fail the skill — the customer's `/generate-scenarios` will just pay the 60-90s build cost as before. The pre-warm is an optimization, not a correctness requirement.
