<!-- generated from generate-scenarios-call.md of the generate-scenarios skill; edit the source and run sync_shared_text.py --write -->
# The `generate_scenarios_start` contract — ONE copy, for every skill that composes the call

This file is the single place the full `generate_scenarios_start(` signature is written for a
reader outside `/generate-scenarios`. `/generate-scenarios`' own SKILL.md carries the same
parameter list inline, because that is where the call is composed on the common path and a
required rule must not sit behind a Read; every OTHER skill that materializes a scenario set
(`/refine-scenarios`, and anything that grows the need later) composes it from HERE instead of
keeping a copy of its own. Before this file there were four drifting copies and no way to tell
which one was current.

Two things follow from that:

- **The parameter list below and the one in the `generate-scenarios` skill are the same
  list.** They are kept BYTE-IDENTICAL; if you edit one, edit the other in
  the same change.
- **The value rules stay where the reader is standing.** `shape`, `memory_challenge`, `count` and
  `intervention_point` are decided on EVERY call, so their rules live in
  `/generate-scenarios`' SKILL.md body and are not restated here. The conditional ones — the
  arguments whose rule is "if the marker is absent, omit it" — are stated in full below.

## The call

```
generate_scenarios_start(
    variables_upload_id=<$VARS_ID echoed by the stash above>,  # STASH_FAILED → variables_yaml=<text> inline instead
    prd_upload_id=<$PRD_ID echoed by the stash above, or None>,  # STASH_FAILED → prd_markdown=<text or None>
    plurai_config_upload_id=<$CONFIG_ID echoed by the stash above>,  # STASH_FAILED → plurai_config_yaml=<text> inline instead.
                                                                    # This is what DISPATCHES the shape (see Step 4)
    data_profile_upload_id=<$PROFILE_ID echoed by the stash above>,  # ABSENT → omit both this and data_profile_markdown;
                                                                    # STASH_FAILED → data_profile_markdown=<text>
    scenario_state_schema_yaml=<text or None>,
    inputs_schema_yaml=<text or None>,   # written by /extract-scenario-variables when simulate.py's hooks are Pydantic-typed;
    outputs_schema_yaml=<text or None>,  # they pin exact field names/shapes so the backend doesn't re-infer them from prose (refine forwards them too)
    count=<int>,
    interest=<str or None>,
    steering_demands=<list[dict] or None>,  # the CHECKABLE half of the same steer (see below); omit when nothing you asked for is mechanical
    random_seed=<int or None>,
    document_generation=<bool>,      # True iff .document_fields exists or the schema declares a generated_document field (see below)
    document_fields=<list[dict] or None>,  # the universal document trigger from plurai_sim/.document_fields (see below)
    doc_examples_set_id=<str or None>,     # THIS env's doc set (env manifest doc_examples_set_id, else plurai_sim/.doc_examples_set_id); None if absent
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
    scenario_bundle_id=<str or None>,      # set_id from plurai_sim/.scenario_bundle_id (see below); None if absent
    additional_bundle_id=<str or None>,    # the merged REGRESSIONS + TRACE SCENARIOS bundle, authored BESIDE the walk into the same set (see below); None on every batch that is not the deliverable
    augmentation_level=<int>,              # from plurai_sim/.scenario_bundle_augmentation (default 0); only meaningful with a bundle (see below)
    artifact_feedback=<str or None>,       # free-text feedback on a PRIOR batch's artifacts/documents (see below); None on a first pass
    intervention_point=False,
    # NO `shape=`: the config above declares it and the backend dispatches on the declaration.
    # Passing one that contradicts the bench is refused; passing one that agrees says nothing new.
    knowledge_bases=<list[dict] or None>, # the agent's knowledge base(s) (see below)
    knowledge_turns=<int>,           # optional; CAP on knowledge-query turns per KB per scenario (default 4, and 4 is also the MAXIMUM — larger values are silently clamped to it, since the walk visits at most 4 nodes). The walk decides which scenarios carry knowledge and how many turns each — knowledge rides a minority of the batch (the set reports it as `dataset_metadata.knowledge_slots`), so this bounds, never fixes, the number of planted questions
    memory_challenge=<'elliptical' or 'off'>,  # REQUIRED — always pass explicitly; omitted, the backend defaults BY SHAPE ('dialog' → 'elliptical', 'pipeline' → 'off'), so the value is inherited instead of stated (see below)
    happy_path_min_ratio=<float or OMIT>,  # the FLOOR on fulfilment-polarity targets in the batch, 0.0-1.0. The composer reads it and the job WARNS on a shortfall; omitted, the backend's own default decides and the recipe steers polarity not at all. Pass it when a batch must not come back all-decline: a batch composed mostly of decline-by-design walks puts the graded target behind a decline beat, where losing it costs the whole scenario
    mandated_flows=<list[str] or OMIT>,     # flow ids the batch MUST cover, by id. The way a coverage_shortfall from a previous audit is carried into the NEXT build: an uncovered flow named here is demanded rather than hoped for. Omitted, nothing forces it and the shortfall recurs
    redraws=<0, or OMIT>,            # omit on a batch you intend to DELIVER (a failed slot is redrawn once). Pass 0 on every QUALIFICATION batch — first and loop regenerations alike — which must SHOW failures rather than replace them (see below)
    previous_scenario_set_id=<id or OMIT>,  # the set this one supersedes; keeps flow ids stable across the graph rebuild so the eval set stays scoped (see below). Pass it on every regeneration for an agent that already has a set
    eval_set_id=<contents of plurai_sim/.eval_set_id, or OMIT>,  # the set that will GRADE these scenarios — pass it whenever the file exists (see below)
    env_id=<contents of $WORKDIR/.env_id, or OMIT>,  # the mock env this set is authored for; the backend drops the faults that env does not inject. Pass it whenever the marker exists and the tool list names it (see below)
    scan_contract_upload_id=<$CONTRACT_ID echoed by the stash above>,  # ABSENT/STASH_FAILED → omit (see below)
    world_reference_rows=<the list in $WORKDIR/.world_reference_rows.json, or OMIT>,  # the real rows of each declared reference table; ABSENT → omit (see below)
    ledger_version=<the pin, read above>,  # REQUIRED when the tool list names it; omit otherwise
)
```

## The refine lane (`/refine-scenarios`, materializing an adapted request)

Same call, three declared differences — a lane, not a second contract:

- **`adapt_request=<adapt_request from end_scenarios_session>` is ADDED.** It carries the
  accumulated refinement request that the session produced.
- **`interest` is OMITTED.** `interest` and `adapt_request` are **mutually exclusive** — the
  backend ignores `interest` entirely when `adapt_request` is present. Do not pass both.
- **The upload ids are re-stashed from disk first** (the same `plurai stash-doc` block, with its
  runner check, its `echo` and its marker-removal and failure handling, that `/refine-scenarios` runs before
  the call). **That
  stash is pre-authorized by the invocation — never ask consent for it**: it egresses the PRD and
  the variables exactly as the generation call it feeds does, and the harness's tool-approval
  prompt is the consent chokepoint. A
  session's shell state does not survive to the MCP call, so the ids you pass are the ones the
  re-stash just echoed — never ids you remember from an earlier turn. On `STASH_FAILED`, take
  that document's inline lane (`variables_yaml=`, `prd_markdown=`,
  `data_profile_markdown=`) exactly as the comments above prescribe.

Everything else — which arguments are required, how each value is chosen, what an absent marker
means — is the same list above. There is no second signature. The non-determinism caveat on the
adaptation itself (the materialized graph may differ from the preview the user approved) stays in
`/refine-scenarios`' own body, next to the preview it is about.

## `ledger_version` — the pin, and what to do when it is refused

Every call that composes this contract carries the pin of the settled grounding job. How it is read, the
refusal causes and what each asks of you are written ONCE, in `/generate-scenarios`' SKILL.md (Step 4, the
`ledger_version` paragraph and the `ledger_version` refusals table). A lane that composes the call from here
reads the pin the same way and answers a refusal the same way; it never retries without the argument.

## Conditional arguments in full

These are the arguments whose rule is "the marker decides whether it is passed at all". The body
of `/generate-scenarios` carries the decision (pass / omit) and the consequence of getting it
wrong; the states are enumerated here.

### `env_id` — the env the set is authored for

Read `$WORKDIR/.env_id` (`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; cat "$WORKDIR/.env_id" 2>/dev/null || echo ABSENT`)
and pass the id exactly as printed, on every call that composes this contract, whenever the tool list names
`env_id`. The backend then drops the failure-recovery premises and observed-fault lines whose tool and fault family
the env does not inject. `ABSENT` means no env exists for this build yet: omit the parameter, and the batch is not
filtered. The refine lane sends it on the materializing call like any other; the rule and the refusal are written in
`/generate-scenarios`' SKILL.md, next to the call.

### `world_reference_rows` — the real rows of each declared reference table

The JSON list in `$WORKDIR/.world_reference_rows.json` (`/simulation-generate-suite` Step 0 stashes each
`reference_rows:` file and writes it), passed verbatim. `ABSENT` → omit. A backend whose tool list does not name it is
called without it (its MCP server drops an argument it does not declare); the worlds then keep authored rows for those
types. Advisory throughout: nothing about it stops a call.

### `scan_contract_upload_id` — the scan's VERIFIED conclusions, and its absence is a statement

The contract carries the facts checked against the customer's own corpus, the derived URL
allowlist, and the typed ABSENCE facts the backend deals as probe scenarios. Pass it whenever the
scan wrote one; the backend materializes it as this job's `inputs/scan_contract.json`.

- `CONTRACT_ID=ABSENT` — the scan never produced a contract for this env (it did not run, or it
  swept its own verdict). Omit the parameter, and **say in the summary that the batch was
  generated on UNVERIFIED premises** — absence of the file is not a clean check.
- `CONTRACT_ID=STASH_FAILED` — classify on stderr like any other stash: a line ending `fall back to the MCP stash_input_doc tool` → the MCP
  `stash_input_doc(text=<the file's contents>, label='scan_contract')` fallback; transport failure
  → omit and say the same UNVERIFIED line.
- A backend that rejects the parameter is an older box: drop it, re-call, and say the same line.
  The absence-probe scenarios then do not exist in this set — they are not silently covered.
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
  be parsed. **Any non-zero count goes in your summary line, named**: the scenario batch was built on a scan whose
  premises include N nobody tested is a different artifact from one built on a scan where every
  premise was checked, and `proceed: true` is identical in both. The report itself names WHICH
  facts, under `provisional_facts` in `scan_contract.json`.
