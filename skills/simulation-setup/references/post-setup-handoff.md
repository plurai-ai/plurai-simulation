# The post-setup hand-off — the exact message, in the exact order

Read this at Step 13, once Step 9f has passed (or the user has explicitly acknowledged
proceeding without it), BEFORE you write the hand-off. Present the steps exactly as written
below: the order carries strict dependencies, and a hand-off assembled from memory reorders them.

The corpus is NAMED in this message and DECIDED by `/simulation-generate-suite` at its grounding
gate — this skill does not ask for it and does not build one. That ownership rule is stated in
the SKILL.md body, where the branch is taken.

**The handoff message MUST present these in order — they have strict dependencies.**

```
Setup complete. Your next steps to actually evaluate the agent:

  ───────────────────────────────────────────────────────────────
  0. (RAG / text2SQL agents) You need a ground-truth knowledge
     corpus, and step 1 will ask you about it.

     I detected a retrieval/text2SQL tool (<tool-name>). The corpus
     is the GROUND TRUTH scenario generation builds the retrieval
     scenarios FROM — the Plurai backend extracts your data source
     into a hosted knowledge graph + synthesized testset, and steps
     2 + generate-evals use it to author RAG scenarios with grounded
     questions + known answers and to seed real entities into each
     scenario's world-state. This is independent of the tool's mock
     mode: running it `<mode>` executes retrieval at runtime, but
     WITHOUT a corpus scenario generation injects no knowledge flow
     and produces 0 RAG scenarios — retrieval is never actually
     tested.

     Nothing to run now. /simulation-generate-suite asks about the
     corpus at its grounding gate and builds it there — that is
     where its SCOPE is decided, beside the other grounding inputs,
     with the answerability and coverage checks around it.
     Extraction is local (your creds never leave the machine).

  ───────────────────────────────────────────────────────────────
  1. Build the test suite — this stands up the world AND the corpus:

     /simulation-generate-suite

     Its first step deploys the agent's mocked world on the
     the Plurai backend (reading plurai.config.yaml +
     simulation_prd.md, producing simulation_env/ with mocks.py +
     server.py + scenario_state_schema.yaml,
     running the pytest contract repair loop) — re-deploying an
     existing world by id in seconds when there is one. It then
     generates the scenarios and evals on that world and drives one
     quality loop until the suite gives sharp signal.

     The world is deployed HERE, not at scan, so it is built from
     the PRD as it stands now and from any trace brief you have
     published — both of which change after scan.

     NO ANTHROPIC_API_KEY required locally — the backend has it.
     Ask me to list the scenarios to inspect what was generated (read-only).

  ───────────────────────────────────────────────────────────────
  2. Enable LangSmith observability (REQUIRED before running experiments):

     Operating an evaluation batch without observability produces
     JSON dumps on disk and no way to inspect why a scenario passed
     or failed. You need this BEFORE step 4 — not after.

     a. Get a LangSmith API key: https://smith.langchain.com/settings
        (free tier is plenty)
     b. Add to the agent folder's gitignored .env, not to an export —
        the .env is what the run and its key check read, and an export
        in your terminal may not reach the commands Claude Code runs (NOTE: double
        underscore — LANGSMITH__*, NOT bare LANGSMITH_*; the bare form
        configures LangChain's tracer but NOT the backend's per-policy +
        rollup feedback writes):

          LANGSMITH__ENABLED=true
          LANGSMITH__API_KEY=lsv2_pt_...
          LANGSMITH__PROJECT="MyAgent"   # optional

     c. Install the optional `langsmith` extra:

          uv sync --extra langsmith

  ───────────────────────────────────────────────────────────────
  3. Run the experiment:

     /simulation-run

     The skill reads plurai_sim/.mock_service_url (written when
     step 1 stood the world up) and runs plurai-run-simulation against that deployed
     mock service — no manual --mock-service-url needed. On any
     scenario failure, it auto-engages the dual-agent diagnostic
     protocol with the backend Claude that owns the mock-env
     filesystem.

     Each scenario produces a LangSmith trace with:

       • Full transcript (user-simulator + agent + tool calls)
       • Filterable metadata: scenario_name, complexity_level,
         subtlety_level, current_elements
       • Per-policy feedback (one entry per policy in policies_config.yaml)
       • Rollup metrics: a 0/1 pass aggregate (key ends _scenario_passed),
         a tool-call count (_tool_calls) and a final-entity count
         (_final_entities) — chart these across the batch for pass-rate /
         dispatch-rate / state-evolution trends.

  ───────────────────────────────────────────────────────────────
  4. Diagnose what the run found:

     /simulation-fixes

     This reads the run's failures, separates real agent defects
     from test-setup issues, and writes a ranked fix plan with
     evidence. It reads the suite's recorded rung
     (suite_anchor.json) and the run's release_gate; a suite-side
     cause goes back to /simulation-generate-suite.

     A run only proves the scenarios EXECUTE. Whether the suite is
     a GOOD eval — diverse, grounded, discriminative rather than
     green-but-hollow — is decided by the quality loop inside step
     1, which is why it runs there, once, before the scored run.
  ───────────────────────────────────────────────────────────────
```

simulate.py is spawned as a child subprocess by the runner in step 3 — the user does NOT execute it directly. The strict ordering (suite → observability → experiment → fixes) is non-negotiable: each step's output is the next step's input, and the suite stands up the world it then generates against.
