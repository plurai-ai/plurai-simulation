# The three refine MCP tool calls, failure modes, and glossary

Read this for the at-a-glance MCP call surface (the `refine_scenarios_start` →
`interact_with_scenario_request` → `end_scenarios_session` sequence with full signatures +
response shapes), the failure-mode recovery table, and the glossary. The interactive refinement
loop (Steps 1–9) stays in the SKILL.md body. The fourth call, `generate_scenarios_start`, is NOT
written out here — its parameter list has exactly one home,
`${CLAUDE_PLUGIN_ROOT}/skills/refine-scenarios/references/generate-scenarios-call.md`.

## The three refine MCP tool calls at a glance

```
# Turn 1 — open session, initial graph reshape (~60-90s on first call)
refine_scenarios_start(
    variables_yaml=<text>,
    request=<initial_request_string>,
    prd_markdown=<text or None>,
    scenario_state_schema_yaml=<text or None>,
    inputs_schema_yaml=<text or None>,
    outputs_schema_yaml=<text or None>,
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
)
→ {session_id, turn, request_so_far, graph_diff{reweighted:{flow_id:int}, added_flows:[str], dropped_unknown:[str], no_op_reason:str|None}, flows}

# Refinement turns (repeat until happy)
interact_with_scenario_request(
    session_id=<session_id>,
    message=<refinement_message>,
)
→ {session_id, turn, request_so_far, graph_diff{reweighted:{flow_id:int}, added_flows:[str], dropped_unknown:[str], no_op_reason:str|None}, flows}

# Finalize — get the accumulated adapt_request
end_scenarios_session(session_id=<session_id>)
→ {session_id, adapt_request, graph_diff{reweighted:{flow_id:int}, added_flows:[str], dropped_unknown:[str], no_op_reason:str|None}, note}

# Materialize — the fourth call, NOT written out here.
# generate_scenarios_start → Job envelope (poll via job_status until done; terminal
# result carries full summary). Its parameter list lives in exactly one place:
#   ${CLAUDE_PLUGIN_ROOT}/skills/refine-scenarios/references/generate-scenarios-call.md
# (full list + the refine lane's three differences: adapt_request added, interest omitted,
# upload ids re-stashed from disk first).
```

## Failure modes + recovery

This table is CLASSIFICATION material for you, not text to relay — the user sees nothing (self-healed), a plain-terms ask, or, when nothing this table names fixes it, run the contact hand-off (`stop_message.py contact --done`)):

| Symptom | Likely cause | What YOU do |
|---|---|---|
| `refine_scenarios_start` takes >120s | First-call graph build; backend is CPU-bound. | Inform the user this is normal on the first call. Wait. |
| `dropped_unknown` lists the user's flow names | The named flows don't exist in the graph; the backend synthesized equivalents or ignored them. | User-relevant (their requested families were dropped): tell them in plain terms which of their labels didn't match, and suggest describing the flow differently or checking the synthesized nodes. |
| `generate_scenarios_start` raises `ValueError("scenario_storage_root is not configured")` | Backend isn't scenario-gen-enabled. | No repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| `generate_scenarios_start` raises `ValueError("count N exceeds scenario_job_max_count=M")` | Backend caps single-batch size. | Lower `count` to M or below and retry silently. |
| `status='failed'` with `error='scenario generation failed in <phase>'` | LLM-side issue or PRD parse failure. | Read the error yourself. Transient stall → retry once silently. Parse failure → describe in plain product terms what in the PRD to fix (don't paste the error). |
| `status='failed'` with `error='all N scenarios failed during evolution'` | LLM stall (rate limit / thinking-mode stall). | Retry once silently; if it fails again, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`). |

## Glossary

- **session_id** — the backend-side refinement session; carries the accumulated
  request between turns. Expires when `end_scenarios_session` is called.
- **adapt_request** — the finalized accumulated request string returned by
  `end_scenarios_session`; passed verbatim to `generate_scenarios_start` to
  steer the materialization. Note: `generate_scenarios_start` re-runs the
  adaptation as a fresh LLM call — the materialized graph reflects the same
  request but synthesized flow ids / exact weights may differ from the preview.
- **graph_diff** — the delta between the default dimension graph and the
  reshaped one. Exact shape: `{reweighted: {flow_id: int}, added_flows: [flow_id, ...],
  dropped_unknown: [flow_id_or_label, ...], no_op_reason: str|None}`.
  `reweighted` is a `dict[str, int]` — keys are flow ids, values are NEW integer
  weights (1..10); there is no `old_weight` field. `added_flows` is a plain list of
  flow id strings — look up user_goal / priority_weight in the top-level `flows` list.
  `no_op_reason` is non-null when the adaptation produced no change; tell the user
  and ask them to rephrase before proceeding.
- **synthesized flow** — a NEW flow node the backend added to the dimension
  graph in response to the user's request (marked ★). Not present in the
  original PRD-derived graph.
- **dimension graph** — flow nodes + weighted edges derived from the PRD;
  the source of walks the scenarios are stratified over.
- **`/generate-scenarios`** — sibling skill for unsteered generation (default
  flow-weighted sampling without an interactive refinement loop).
- **`/list-scenarios`** — sibling skill that renders per-scenario metadata for
  a set without running anything.
- **`/simulation-run`** — downstream skill that consumes the
  `scenario_set_id` and runs `plurai-run-simulation` against the customer's agent.
