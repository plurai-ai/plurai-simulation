# The scenario-bundle path — full-scenario IMPORT

You are reading this because `plurai_sim/.scenario_bundle_id` exists. It has no TTL and no
scope, so its mere presence is not permission: this file is the CONFIRMATION procedure, the
intent check that decides whether to take the path at all, the statement you owe the user before
calling, and the list of what goes inert when the dimension-graph walk is bypassed.

Nothing here is optional once the marker is present. Skipping it does not fail loudly — it
silently imports a finished build's bundle into an unrelated run.

**Scenario bundle — full-scenario IMPORT (optional; MUTUALLY EXCLUSIVE with the graph walk).**
If the customer ran `/upload-scenario-bundle`, a `set_id` was written to
`plurai_sim/.scenario_bundle_id` (one-line string) and an integer to
`plurai_sim/.scenario_bundle_augmentation`. Read the id with `strip()` and pass it as
`scenario_bundle_id=<id>`, plus `augmentation_level=<int from .scenario_bundle_augmentation,
default 0>`. If `.scenario_bundle_id` is absent, pass `scenario_bundle_id=None` and leave
`augmentation_level=0`. A bundle names a set of folders — one per scenario — each holding a REAL
record + a `ROLES.md`; the backend builds each scenario FROM its folder. This is how a per-scenario
precondition (a "current page" / "selected record") is grounded in a REAL datum instead of an
LLM-hallucinated one.

**THE BUNDLE PATH IS CONFIRMED, NEVER AMBIENT — do this BEFORE you call.**
`.scenario_bundle_id` has no TTL and no scope. It persists until someone deletes it, and every
`generate_scenarios_start` reads it: a suite build's qualification batch, a regeneration next
week, another operator's run. So a marker left behind by a finished bundle build silently
converts a LATER, unrelated build into a bundle import — with the graph walk off and half its
arguments inert. Before passing `scenario_bundle_id`, say out loud, in the message you are
already writing:

> Taking the SCENARIO BUNDLE path — bundle `<id>`, `<the .scenario_bundle_intent line, verbatim>`.
> The dimension-graph walk is bypassed, so `count`, `interest` and dimension diversity are inert
> this run; the set will be `<folders> × (1 + <aug>)` scenarios.

Then apply this rule, silently, without asking:

- **`.scenario_bundle_intent` is absent, or names a purpose this request is not about** — the
  marker belongs to a build that is over, or the id arrived by CLONE: `.scenario_bundle_id` is
  un-ignored and travels with the repo (`simulation-scan-agent` SKILL.md, the managed
  `.gitignore` block), while the intent marker is machine-local and does not. **Do not take the
  bundle path**, and **do not delete anything** — skipping is this step's decision to make, but
  a tracked file another operator committed on purpose is not. Generate normally and say in one
  line that a bundle marker was present, unexplained, and skipped; name
  `/upload-scenario-bundle` as the way to re-point it. Deleting here is what makes the skip
  irreversible for the person who committed the id, and on a fresh clone it fires on the FIRST
  run, every time.
- **The intent line matches what is being generated now** — take the bundle path, having said
  the statement above.

Skipping is the DEFAULT for an unexplained marker, not the exception: a wrongly-skipped bundle
costs one re-run that the user asked for, and a wrongly-taken one costs an entire suite build
that silently tested the wrong thing. The markers are cleared in ONE place — Step 6, where the
build that wrote them is known to be finished — so that the delete happens where the intent is
known rather than where it is merely absent.

**On the bundle path the dimension-graph walk is BYPASSED**, so these are **ignored / inert**:
`count` (fan-out is `folders × (1 + augmentation_level)` instead), `interest`, dimension
diversity, and the graph's knowledge-FLOW injection. **`knowledge_bases` and `memory_challenge`
are NOT inert — keep passing them.** The bundle path runs the same knowledge prelude the graph
path does: each imported scenario's session wires the KB tools and drives corpus Q&A conditioned
on that folder's real materials (opportunistically — knowledge can demote at runtime here, and
the backend collapses mixed per-KB synthesis modes to one for the whole set, saying so in a
warning), and the elliptical memory probe rides a complication every bundle scenario carries.
Dropping the corpus on a bundle run costs every knowledge turn in the set; same contract as
`/upload-scenario-bundle` → "When this applies". Still honored: `variables_yaml`,
`scenario_state_schema_yaml`, `document_generation` / `document_fields`, `doc_examples_set_id`,
`artifact_feedback`, and `shape`. **Bundle and corpus `ground_from` are mutually exclusive for one
run** — the bundle path does not materialize corpus `agent_context`; if the schema declares
`ground_from`, it is inert when a bundle drives the value. Run the bundle OR a corpus-grounded
graph batch, not both. `scenario_bundle_id` + `augmentation_level` are a plain id/int (no file
content), like `doc_examples_set_id`.
The confirmation statement above IS the one-line status this step owes — it already names the
bundle, its intent and what went inert; add the arithmetic to it: `<n> folders × (1+<aug>) =
<total> scenarios`. `augmentation_level=0` reproduces each folder faithfully
(1 scenario/folder); `N>0` ALSO emits N varied scenarios per folder. Fidelity is LLM-mediated, so
after the run do the bundle VERIFY step (see Step 6) before scaling.
