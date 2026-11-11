# Knowledge-challenge scenarios — resolving and passing `knowledge_bases`

Read this before composing `knowledge_bases=` on any call where
`plurai_sim/envs/<active>/plurai.env.yaml` declares a `knowledge_bases` list, or
`plurai_sim/.knowledge_bases` or `plurai_sim/.corpus_id` exists. `.corpus_id` is BACK-COMPAT ONLY
and is always written alongside `.knowledge_bases` (`scripts/corpus_coordinate.py`), so resolve
from the latter — there is no `.corpus_id` branch below because there does not need to be. It is
the declared reader of
`plurai_sim/.knowledge_bases`, and it carries the
whole rule: the answerability precondition, where the list is resolved from, which entries are
skipped, the `ground_from` branch that separates "no questions" from "no corpus", and how each
`label` is derived.

Skipping it does not produce a malformed call — it produces a call with `knowledge_bases` omitted
or mis-built, which ships a set with ZERO knowledge scenarios and, when the schema pins
`ground_from`, grounded fields authored from no source.

**Knowledge-challenge scenarios (optional).** If the repo has built corpora, pass the
agent's knowledge base(s) so the backend adds a dedicated knowledge flow per base to the
dimension graph; scenarios that sample one get a knowledge-grounded, multi-turn question
plan (each question with an exact ground-truth answer) woven in, and the agent's answers
are graded for knowledge-correctness at run time. Knowledge challenges require
`shape='dialog'`.

A **knowledge base** = one distinct RAG/SQL tool/domain (e.g. a personal-memory RAG *and*
a company-docs RAG are TWO bases — each becomes its own subflow, and a conversation may
probe one or span several → cross-KB). Each base is backed by one corpus (fixed/shared)
or many (per-tenant, round-robin per scenario *within that base*).

Before passing `knowledge_bases` at all, apply **simulation-generate-suite Step 0's
corpus-answerability check** (do not restate its criteria here — it is stated once, there):
match the corpus's FACTS to the tools' `output_fields`. An attribute-heavy corpus (catalog,
records) whose facts surface through scalar fields is a full match — pass `knowledge_bases`
without ceremony. Only a prose-heavy corpus facing tools with no free-text `output_field`
gets the scope note (the prose-only question subset degrades toward attribute facts +
decline probes — never to zero), said BEFORE generating rather than surfacing in job
warnings 20-30 minutes later.

Resolve `knowledge_bases` from the canonical manifest: the active env manifest
(`plurai_sim/envs/<active>/plurai.env.yaml`) `knowledge_bases` list, else
`plurai_sim/.knowledge_bases` (JSON list, one entry per RAG/SQL tool, written by
/build-rag-corpus).

**SKIP every entry carrying `knowledge_planning: "off"`** — an entry marked
`"grounding_only"` is PASSED, not skipped (see below). That is a base a previous
answerability verdict DROPPED — nothing it holds is realizable through this agent's tools —
and its `dropped_reason` says why. It is marked rather than deleted because the corpus is a
real backend artifact other flows still reference, which means the entry is still sitting
there looking usable: read past it. Passing it re-plants knowledge turns that were already
established as structurally unanswerable, and buys the same dead slots again.

**But "no knowledge QUESTIONS" is not "no corpus" — check `scenario_state_schema.yaml` for
`ground_from:` before you detach anything.** A corpus has two independent uses: planting
knowledge turns, and GROUNDING the schema's `ground_from` pins against real records. With no
corpus attached the backend still authors a `ground_from` field, from no source, and the job
carries a gate finding naming the field. On a **`precondition: true`** field that makes the whole
dialog false (the scenario opens on an invented record) — read the finding and decide per the
plugin's `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`; attaching the corpus is the repair that keeps the
pin honest.

- **EVERY entry off, and the schema declares `ground_from`** → pass the bases with
  `knowledge_turns=0`, the grounding-only request: pins resolve against real records, no
  question is planted.
- **EVERY entry off, no `ground_from`** → omit `knowledge_bases` entirely (see the last
  sentence of this paragraph) and say knowledge coverage is uncovered by prior decision — do
  not re-litigate the verdict here.
- **SOME entries off, others usable** → pass ALL the bases at the normal `knowledge_turns`.
  The parameter is one number for the whole call, not per base: sending 0 to protect a dropped
  base would silence the usable one too.

On a backend that rejects `knowledge_turns=0` (`must be positive` — it predates the
grounding-only channel), pass the bases with the default and accept the dead knowledge turns:
grounding is the load-bearing half, and a detached corpus leaves every pinned field unsourced.

Pass `knowledge_bases=[{corpus_ids, label, tool}, …]` — one entry per base,
each entry's `corpus_ids` holding 1 (fixed) or N (per-tenant) ids. **Forward the manifest's
`tool` verbatim** when the entry has one, and omit the key when it has none (prompt-injected
knowledge): the backend types the agent's own flows that call that tool as knowledge-answering,
so their scenarios plant corpus questions. Drop only the bookkeeping `kb_id`.

**DERIVE each `label` from the corpus's own metadata — never free-write it, and never forward
the manifest's copy unchecked.** The planner reads the label as the description of what it is
walking, so a label describing more than the corpus holds steers the walk off the material that
exists. Call `get_artifact_meta(<corpus_id>)` per base (or `job_status(id=<corpus_id>)` — same
`detail`) and build the label from what it stores: the corpus's own domain / `task_description`
plus its scope and counts — `"mattress product slice — 20 knowledge-graph nodes"`. Read it
defensively (`.get()`); with neither a description nor counts there, fall back to the manifest's
`label` and say in your status line that it is unverified. A manifest label that DISAGREES with
the meta is a note about intent, not the label the planner gets. The backend injects one knowledge flow per
base, grounds single-base scenarios in that base's corpus(es), and grounds a scenario that
walked ≥2 bases in the **union** (cross-KB). These are id references (not uploaded files). Omit (or pass `None`) when there's
no corpus — the feature is then inert.
