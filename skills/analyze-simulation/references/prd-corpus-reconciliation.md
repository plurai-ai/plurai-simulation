<!-- generated from prd-corpus-reconciliation.md of the build-rag-corpus skill; edit the source and run sync_shared_text.py --write -->
# PRD ↔ corpus reconciliation

`simulation_prd.md` and the knowledge corpus are both read downstream as ground
truth — the PRD by every generation prompt (and, for the product contract, the
grounding audit); the corpus by the same audit as the authority on
agent-knowledge facts. When the two disagree on such a fact, generation and
audit pull in opposite directions: whatever answer a scenario prescribes
contradicts one of the sources. The backend does not fail scenarios on this —
it ships them following the corpus and emits a `grounding_sources_conflict`
finding quoting both sides — but every later generation re-reads the same
conflicted inputs, and eval generation freezes PRD lists into judge prompts.
Reconciling the two documents takes minutes; one unreconciled claim can stall
a whole generation.

Run this:

1. **right after a corpus build** (`/build-rag-corpus` Step 6.5) — the PRD was
   authored from the repo before the corpus existed, so this is the first
   moment the check is possible;
2. **when a generation job carries a `grounding_sources_conflict` finding**
   (a `notice.actionable` entry in `job_status(id=<id>).detail.gate_authority.findings`,
   with `evidence.family` set to it) — it quotes the PRD claim and
   the corpus passage that disagree, so the work is already located;
3. **when the suite-quality critic reports an `input`-surface finding**
   (`/analyze-simulation`).

## Procedure

1. **Collect the PRD's agent-knowledge claims.** Every claim block about what
   the agent's docs/guides say: feature lists lifted from docs, "the X tab
   offers…" enumerations, coverage statements, §5.x "documented facts"
   sections. Skip pure product-contract material (tool I/O read from code,
   numeric caps, closed identifier lists) — the PRD is authoritative there and
   the corpus does not vote. When reacting to a conflict finding, start from
   the quoted claim.
2. **Find each claim in the corpus.** Grep the locally ingested chunks:
   `plurai_sim/rag_corpus/chunks.json` (a JSON array of chunk payloads —
   search case-insensitively, and try the claim's key nouns AND their synonyms
   / UI names before concluding absence). No local copy (fresh clone)? Read
   the backend's: `get_artifact_meta(corpus_id)` →
   `fetch_artifact(corpus_id, paths=[...])`.
   **No corpus at all (a local-file-knowledge agent — guides/docs the agent
   reads directly, nothing ingested)?** The same reconciliation applies with
   the agent's OWN knowledge files as the authority: grep the doc files the
   agent loads (its prompt/config names them) exactly as you would the chunks,
   and run the same three outcomes below. A knowledge claim in the PRD that the
   agent's own guides contradict ships a scenario whose judge inherits the
   error and blesses the agent's matching mistake (observed live: a
   channel-visibility rule stated backwards).
3. **Fix whichever side is wrong:**
   - corpus **supports** the claim → keep it; add or refresh its
     `(source: <doc>)` citation;
   - corpus **contradicts** it → open the chunk and the repo doc it came from
     and decide which is right. Wrong PRD → REWRITE the claim to say what the
     source says. Rewriting, not annotating, is the whole rule (the template's
     "reconcile at the source"): emphasis notes and rebuttals become facts the
     next run grounds on — both sides of the argument at once. Wrong or stale
     corpus → fix the source documents and rebuild, or patch via
     `/edit-env` (`target_kind='corpus'`);
   - corpus is **silent** → mark the claim `(unverified — not found in
     corpus)`. Don't delete it (the repo may know things the ingested subset
     doesn't) and don't leave it confident (generation builds on it and the
     audit can't back it).

   **A claim you DELETE outright — wrong, not merely mis-valued — gets one more
   check: did `corpus_meta.json`'s task description inherit it?** Read it back
   (`fetch_artifact(<corpus_id>)`) and, if the same claim is there, fix it via
   `/edit-env` (`target_kind='corpus'`) — a metadata patch, no resynth. That
   description is read at every later build and nothing else re-checks it, so a
   fact deleted only from the PRD survives there and re-enters generation.
4. **Sweep design prose out of the fact channels.** A sentence that steers
   generation ("never mention links in family A openings", "combining these is
   unsatisfiable") is policy, not fact — move it under the PRD's
   `## Scenario design constraints` section, and delete any copy of it from
   `scenario_variables.yaml` complications.
5. **Regenerate once, downstream-only, only what read the wrong side.** PRD
   fixed → re-run `/generate-scenarios`; also re-run `/generate-evals` when the
   fix touched §5 facts or a closed list (those are frozen into judge prompts
   at generation time — editing the PRD later does not update existing evals).
   Corpus-only fix under an otherwise-sound set → keep the set. Narrower still:
   when the reconciliation corrected ONE fact and the only stale artifact is a
   scenario's grading GT (its planted `expected_answer`/evidences quote the old
   text), fix it in place with `edit_scenario_gt`
   (the `edit-scenarios` skill) instead of regenerating anything. Never
   re-roll generation with unreconciled inputs to "see if it passes now" — the
   same inputs reproduce the same conflict.

Tell the user about a PRD edit in one plain product sentence ("corrected the
PRD: share options now match the sharing guide"). The finding text itself is
internal — react to it; don't paste it into customer-facing output.
