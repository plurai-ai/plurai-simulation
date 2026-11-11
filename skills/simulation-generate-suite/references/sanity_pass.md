# The sanity-pass reader (suite Step 2a)

ONE reader, medium effort, report-only, at most 3 minutes: it stops at its budget and reports what it has. It reads
the harness, the world and how each scenario starts. Nothing about evals, verdicts, scenario diversity or agent
quality: none exist yet, and none are its question.

**Its input, and nothing else:** each scenario's `transcript.json` and `tool_events.json` under `$ART/sanity/1/`, and
each scenario's `agent_context.yaml` and `initial_world_state.yaml` (the `files` of its `get_scenario` JSON in
`$ART/sanity/worlds/`). It opens `simulate.py`, `plurai.config.yaml` or the mock only to cite the line behind a
finding it already has, never to review them. `$ART/sanity/1/signals.txt` is a list of typed facts, never verdicts.

**It answers five questions**, each finding with `scenario:turn` or `path:line` evidence and a
`surface: harness | env | scenario | input`:
1. Does the first agent reply restart or refuse a flow the scenario's start state says is done, or cannot happen?
2. Is every seeded or handed-over precondition (documents, ids, thread state) visible to the agent?
3. Does each mock answer in turns 1–3 agree with the seeds and with what the agent asked for and expects back
   (shape, filters, time windows, ids)?
4. Does the harness show the agent faithfully: no tool-call text or reasoning in replies, one trail row per real
   call, inputs kept apart from outputs?
5. Does any harness code on the path decide an outcome the agent should decide?

A question with nothing found is answered `none`. The reader edits nothing and asks nothing.

## The typed lines (`signals.txt`)

| line | read from |
|---|---|
| `harness_fault` | `error.json` with a typed harness `kind` |
| `init_error` | an untyped `error.json` at Init or before any turn |
| `run_error` | any other `error.json` |
| `undelivered_seed` | `run_summary.json` `ended_early_scenarios` |
| `tool_error` | `tool_events.json` `events[]` with `status: error` — may be the agent's own input (a bad id); never a repair on its own |
| `unseen_write` | an `ok` call whose declared `post_conditions` write a row of the harness-owned store that the scenario's `final_world_state.json` does not hold — what the agent saved, the checks cannot see |
| `measurement_unclean`, `tool_trail_results_missing`, `adapter_degraded`, `aborted` | `run.json` |
