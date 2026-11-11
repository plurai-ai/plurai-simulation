<!-- generated from simulation_prd_template.md of the simulation-setup skill; edit the source and run sync_shared_text.py --write -->
<!-- This file belongs to you. Plurai generated it from your agent as a starting point; review it, change it and keep it with your code. -->
# simulation_prd.md template

Write `plurai_sim/simulation_prd.md` using the structure below — populate every
section from the agent code, docstrings, comments, README, and any repo docs.
The PRD should be thorough enough that someone who has never seen the codebase
could understand how the agent is supposed to behave. Everything below the line
is the fill-in template; replace every `{placeholder}` and delete guidance that
doesn't apply.

## Sourcing — where PRD facts may come from, and who wins a disagreement

Every claim in this PRD must trace to the repo (code, docstrings, README, docs),
the knowledge corpus, or production traces. Never fill a gap from your own
training knowledge of the product or vendor — a plausible capability the sources
don't state enters the PRD as a fact generation builds scenarios on and judges
enforce, and afterwards nobody can trace where it came from.

Two kinds of fact live here, with different authorities:

- **Product contract** — tool I/O contracts, numeric caps read from code, closed
  identifier lists, state machines. The PRD is authoritative; copy from code/config.
  Within this tier, **tool BEHAVIOR claims** (what matching a search performs, what an
  invalid argument does, which fields a call returns) carry their own two markers:
  `(observed — probe_report.md)` when setup's Step 8c.2 exercised the call, or
  `(from code — unprobed)` when the claim rests on reading alone. Code-as-text cannot
  distinguish the configured path from a dormant one, so an unprobed behavioral claim is
  a weaker fact than an observed one — generation and judges treat it conservatively,
  and it is the first suspect when a scenario chases behavior the agent lacks. These
  markers are distinct from the corpus-sourcing markers below and never feed corpus
  reconciliation.
- **Agent knowledge** — what the agent's docs/FAQ/guides say (feature lists,
  "the Share tab offers …"). When the agent is corpus-backed (its docs are — or
  will be — ingested by `/build-rag-corpus`), the CORPUS is authoritative for
  these: the backend's grounding audit backs scenarios with corpus passages, so
  a PRD claim the corpus contradicts makes generation and audit pull in opposite
  directions until someone reconciles them. For every agent-knowledge claim
  block, name its source document — `(source: sharing_guide.md)` — and write
  `(unverified — not found in corpus)` instead of confident prose for anything
  you could not find in the sources. Generation treats a marked claim with
  caution; it treats confident prose as truth.
  When there is NO corpus (the agent owns no retrieval tool — its knowledge is
  inlined in its prompt, or its docs simply were not ingested), the agent's OWN
  doc set is authoritative in the corpus's place: cite the repo path
  (`(source: assets/docs/sharing.md)`), verify each claim against that file, and
  write `(unverified — not found in the source docs)` for anything you could not
  find there. Do NOT write that the corpus is authoritative when none exists —
  the sentence is false, and it licensed a ground-truth block that no source
  backed (observed live: a corpus-less agent whose PRD claimed corpus authority
  shipped an incomplete enumeration, flagged by a reviewer as an input defect).

**When generation or audit contradicts this PRD, reconcile at the source —
never argue inside the document.** Generators and auditors read every sentence
here as ground truth, with no memory of previous runs. A post-mortem note aimed
at a past failure — "Timestamped links ARE a documented feature — never treat
them as out of scope", "the agent was right and the scenario was wrong" — is not
a correction; it is new asserted world-state stacked on the old, and the next
run grounds itself on both sides of the argument. Instead: open the corpus
chunk (or repo doc) behind the claim and repair whichever side is wrong — a
wrong PRD claim gets rewritten (not annotated), a wrong corpus fact gets fixed
at its source. If the PRD was already right, the failure is a generation defect
to escalate, not a sentence to bold. The procedure lives in
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/references/prd-corpus-reconciliation.md`.

---

# Simulation PRD: {agent_name}

## 1. Agent Purpose

{One paragraph: what does this agent do? What business process does it automate?
Who are the users? What is the expected conversation flow?}

## Scenario design constraints

{Rules for the GENERATORS about how to build scenarios — never facts about the
agent or its world. Scenario families and their anchors; topics an opening must
not name ("family A opens without mentioning links or search"); combinations
known to be unsatisfiable; steering notes ("family B starts mid-task"). Every entry is IMPERATIVE and names a family; the
measurement that motivates a rule stays in the fact sections — write the
instruction here and the number there.
Declare the families themselves as `- Families: (A) <one line>; (B) <one line>` or one
`- Family <name>: …` entry each — the backend reads the family list from those entries only, and
only when the item STARTS with `Families` / `Family <name>` (a lead-in before it is not read).
**Every refusal, failure or non-default lifecycle branch (a wait or an approval the world can
hold) the error paths and business rules state that a user can reach — by what they ask, or by
what the world holds — belongs to a declared family; outcomes with the same trigger share one.** An outcome no family names is one no scenario draw can deal.

Nothing downstream validates this section — the routing that keeps it out of the
auditor's fact surface keeps it out of every check too — so satisfy yourself BY HAND
that each entry is true before you write it. Keep the entry imperative even so: the
measurement stays in the fact sections, and a rule you cannot substantiate is dropped
rather than softened.

Cover whichever of these apply; say which do not:
- **Reachability** — keep every family's CLOSING condition inside the agent's declared
  capability surface, whatever declares it: a tool inventory, or the instructions alone
  for an agent whose outcomes are conversational. Do not give a persona an exit the
  agent has no way to reach.
- **Seed fidelity** — where the harness seeds opening state from profiled data, draw it
  from a real row rather than composing one, and never from a fixture or demo module.
  Where nothing is profiled, say what the opening state is and why it is plausible.
- **Recall separability** — when a family tests memory, put turns between the anchor
  and the question that do not restate it, so the probe tests recall and not the
  agent's own restatement one turn earlier.
- **Evidence grounding** — when a family asks a knowledge question, keep the
  supporting passages on the entity the question names. A question about one thing
  whose evidence describes three others grades as noise. This applies wherever the
  agent retrieves — a corpus, an index, an inlined doc set — and is independent of
  whether this PRD carries a verbatim appendix.}

This heading is a ROUTING CONTRACT — the backend routes on the heading itself.
The WORDING is what is strict: write `Scenario design constraints`. (The backend
also accepts a different heading level, a single-level section number, a hyphen
join, the singular and a trailing colon — but use the canonical `##` form.) The
section under a matching heading goes to the generator as directives and is held
OUT of the grounding auditor's fact surface. A REWORDED heading routes nowhere,
and the section then lands in the fact surface, where "family A never mentions
links" is read as "links do not exist" and a scenario that legitimately touches
links deadlocks against the audit. Only the FIRST such section routes, so keep
the directives in one place. The auditor also tries to recognize design-shaped
statements elsewhere, but the heading is the only GUARANTEED signal.

**A fact written under this heading is UNENFORCEABLE by the grounding audit.**
The routing runs both ways: nothing in this section reaches the auditor as
ground truth, so a world fact parked here grounds no audit. Eval generation,
however, still reads the whole PRD — so the same fact can leak into a judge
prompt with no audit standing behind it. Worst of both. Facts belong in the fact
sections; this one carries directives only.

Keep entries imperative and scoped ("In family A, open without naming a
document"), never phrased as facts ("There is no document search"). A real
capability limit is a fact — it belongs in §5 (capability boundaries), not
here. And these rules live here ONLY: do not copy them into
`scenario_variables.yaml` complications (a complication describes what happens
to the user in the scenario; a design rule pasted there re-enters the
fact-grounding channel this section exists to keep it out of).

> **Only genuine DESIGN steering belongs here — never law derived from this
> agent's own data or tool surface.** Target-shape rules ("never author a target
> requiring `<field>`", "`<field>` is authorable via `<tool>`"), grading rules
> ("grade the decline as correct"), and authoring bans or licences read off the
> data profile are FORBIDDEN in this section and everywhere else in this
> document. They are the backend's generic invariant re-stated in one agent's
> vocabulary by a process that can state it either way round — the same repo
> scanned twice has produced opposite law. Write the measurement instead, as a
> typed fact under `## Agent facts`; the auditor already enforces the invariant
> that consumes it, on every agent.

> **This section sits second for human readability only** — the rules a reader wants before
> the world facts. The backend finds it by heading, never by position.
>
> **There is no length target for this document.** Content earns its place by relevance: keep
> what changes what the harness, the scenarios, the corpus build or the evals DO, and leave out
> what does not. Detail that IS consumed is never cut to make the document shorter, and bulk
> detail that comes from a real source file belongs in the verbatim appendix as a byte-exact
> `cat` (final section), never re-typed into prose. Check the structure — the routed heading
> above, the appendix parse, and a map of where the prose mass sits — at any time:
> `python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/verify_prd_appendix.py" plurai_sim/simulation_prd.md`
>
> **Machine-computed INVENTORIES belong in `artifacts/data_profile.md`, not here.** The backend
> grounds generation on that profile directly, so per-field null tables, full value
> distributions, row counts and the goal-facet satisfiability census are not transcribed into
> PRD prose. What this document keeps is narrative, business rules, tool contracts, steering,
> and a COMPACT FACT SUMMARY — the named closed sets plus the rules that depend on them. Keep
> that summary: the dimension-graph flow extraction reads the PRD, not the profile. A profiled
> fact that CONSTRAINS what the suite can test — "`age_range` is null on every row" — is carried
> as a typed fact in the section below, never as a prohibition written in prose.

## Agent facts

{The typed channel for this agent's specifics. One fenced `yaml` block, every entry
carrying `kind` / `subject` / `predicate` / `evidence` / `source`. Full contract +
the closed vocabulary: `${CLAUDE_PLUGIN_ROOT}/skills/simulation-scan-agent/references/agent_facts.md`.}

```yaml
instruction_sources:            # the files holding the agent's own instructions (system prompt, task/agent configs)
  - path: path/to/prompts.py    # every instruction line in `lines` is a `rule` fact below, or declined here
    lines: 1-40
    unrowed: []                 # {lines: LO-HI, class: not-an-instruction | duplicate-of-<rule subject>}
                                # {lines: LO-HI, class: not-loaded, source: <path:LINE of the load site reading other instructions>}
facts:
  # DELETE THIS ENTRY. It is a SHAPE, not a fact: `subject` and `evidence` are placeholders, and
  # `kind` / `predicate` are only spelled out because the vocabulary is closed. Everything left
  # in this section is read as GROUND TRUTH by eval and scenario generation, so a placeholder
  # that survives the copy is a FABRICATED MEASUREMENT steering the whole build — worse than an
  # absent fact, which merely thins the suite. Replace it with facts you measured. Keep the block
  # and its `instruction_sources` even when the agent has no tool surface, no profiled data set
  # and no quoted rule: the lint opens a finding on a missing block and on a block without
  # `instruction_sources`, and an agent with no instruction text accepts the latter with that reason.
  - kind: data_profile          # data_profile | tool_surface | seedability | rule
    subject: "<entity>.<field>" # <entity>.<field> | <tool> | <tool>.<arg> | <agent_context field>
    predicate: null_on_all_rows # closed per kind — see the reference
    evidence: "<the measurement with its number, or the quoted bytes — one line, no instruction>"
    source: artifacts/data_profile.md   # or artifacts/probe_report.md | path/to/file.py:LINE | plurai.config.yaml | corpus:<doc>
```

> A fact is a MEASUREMENT or a QUOTATION — it re-derives to the same value next scan, which is
> exactly what prose law does not. State the number and where it was computed; state nothing
> about what generation or a judge should DO with it (no "author", "never author", "grade",
> "is correct", "is authorable"). The backend holds the generic invariant that consumes these:
> a target must ground in the licensed tool surface, and an honest decline grades correct where
> it does not. This section is inside the auditor's fact surface, so what you write here
> grounds; what you write under `## Scenario design constraints` deliberately does not.

## 2. Entity Schemas

{For EVERY entity type the agent works with, document the full schema with constraints.}

### {EntityType} (e.g., User)

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| id | string | yes | UUID format | Primary key |
| name | string | yes | max 200 chars | Display name |
| plan | string | yes | enum: [free, pro, enterprise] | Subscription tier |
| balance | float | yes | min: 0.0, max: 999999.99 | Account balance in USD |
| status | string | yes | enum: [active, suspended, deleted] | Account status |
| created_at | datetime | yes | ISO 8601 | Creation timestamp |

**Invariants:**
- Balance can never go negative
- Deleted users cannot make purchases
- {any other rules that must always hold}

### {EntityType} (e.g., Order)

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| ... | ... | ... | ... | ... |

**State machine:**
```
pending → paid → shipped → delivered
pending → cancelled
paid → refunded (only within 30 days)
shipped → returned → refunded
```

**Invariants:**
- {rules specific to this entity}

> **A STATE DIAGRAM IS GROUND TRUTH, NOT AN ILLUSTRATION.** Every arrow you draw is consumed as a
> documented transition and every arrow you omit as an impossible one — exactly like the closed-set
> rule for enumerations below, but easier to get wrong, because a diagram invites you to sketch the
> shape rather than state the facts. A wrong arrow is the worse half: scenario generation will
> author a target around a transition your product does not have, and the resulting scenario grades
> the agent against a false claim. Observed live: a `Private → (share direct link) → Unlisted` arrow
> — drawn to show how sharing works — was read as "sharing a link changes the privacy state", and
> the scenario built on it asserted a product behaviour the docs contradict. It was caught by the
> backend auditor, but only after it had consumed a scenario slot. Draw ONLY transitions your
> sources document, label the trigger for each, and if a transition is conditional say so on the
> arrow (`paid → refunded (only within 30 days)`). If you are unsure whether a transition exists,
> leave it out and note the uncertainty in prose — an omitted arrow costs coverage, an invented one
> costs correctness.

## 3. Tool Specifications

{For EVERY tool, document the exact behavior contract. This is what the backend uses
to generate mock implementations.}

### {tool_name} (e.g., process_refund)

**Purpose:** {what it does}
**Side effects:** {what one call writes/modifies, AND the condition under which it does. Read the
write site and its ENCLOSING GUARD, not just the call: `if <guard>: <write>` means the write is
conditional, and "always writes" is then false. An unconditional line asserts the tool ALWAYS
writes, so if a call can succeed without writing — nothing matched, zero rows changed, an early
return — say so here. `none (read-only)` is a claim like any other. Cite the line you read it from; a write
you did not read is `not read — see ## Agent facts` and gets no typed fact either. For any tool
with a non-null `descriptor.writes_to`, this claim ALSO goes to `## Agent facts` as a typed
`tool_surface` / `writes` fact — a probe can never confirm it, so the source is all it has.}
**Observed behavior:** {optional — for retrieval/matching tools especially: the matching
semantics that actually run under the current config ("literal token overlap over
title/category; no synonyms, no reranker"), what an out-of-enum argument does, which
fields real calls return. Each claim marked `(observed — probe_report.md)` or
`(from code — unprobed)`. Omit the block only for tools with no behavioral surface
worth claiming.}

**Input contract:**
| Arg | Type | Required | Constraints | Example |
|-----|------|----------|-------------|---------|
| order_id | string | yes | must exist in orders | "order_456" |
| reason | string | yes | max 500 chars | "damaged item" |
| amount | float | no | default: full order amount, min: 0.01, max: order.total | 89.99 |

**Output contract:** {every literal string a caller can see — status text, error text, result
messages — is COPIED from the code that emits it, with the citation of the emitting line. Never
compose one to look plausible: a judge grades the agent against the string written here, so an
invented one fails the agent for saying the true thing. Copy it UNTRANSLATED, in its original
script — a translated message is an invented one, and on a non-English agent nothing downstream
will notice. A tool the harness MOCKS has no emitter in
the agent's code to copy from — its shapes come from a descriptor a human wrote, so mark the block
`(mocked — shapes asked of the mock, not emitted by the agent)` and never cite `plurai.config.yaml`
as evidence of agent behaviour.}
```json
// Success:                                         tools/refunds.py:88
{ "refund_id": "ref_xxx", "amount": 89.99, "status": "processed" }

// Errors — one entry per raise site, message verbatim, IN THE ORDER THE CODE CHECKS THEM.
// Order is contract, not presentation: where checks run in sequence and each returns early, a
// precondition checked first is the only refusal its caller sees and every error below it is
// UNREACHABLE for them. Say so when that is the shape; a validator that COLLECTS violations and
// returns them together is the other shape, and it is worth one word to say which this is.
// Number them, and say plainly which is checked first (e.g. "#1 is checked before any data is
// read, so a caller missing it never reaches #2-#4"). A scenario authored against an
// unreachable error path grades an outcome the agent cannot produce.
{ "error": "order_not_found",      "message": "..." }   // tools/refunds.py:101
{ "error": "already_refunded",     "message": "..." }   // tools/refunds.py:110
{ "error": "refund_window_expired","message": "..." }   // tools/refunds.py:119
```

**Business rules:**
1. Refund amount cannot exceed original order total `tools/refunds.py:96`
2. Refund window is 30 days from delivery date `config/limits.yaml:12`
3. Only orders in "delivered" or "shipped" status can be refunded (inferred)

**State transitions:** {what ONE call changes. Where the tool also has something to say about id
stability, ordering or idempotency, that belongs in `## Agent facts` as a typed `tool_surface`
fact carrying a mandatory source — not as an extra bullet here. Never guess one: an absent fact
costs a thinner suite, a wrong one costs a scored run.}
- `order.status`: delivered → refunded, **only on a FULL refund** (a partial one leaves the
  status unchanged) `tools/refunds.py:140`
- `user.balance`: += refund_amount `tools/refunds.py:144`
- Creates: new `transaction` entity with type="refund" `tools/refunds.py:151`

**How the agent uses the result:**
- Checks `status` field to confirm success
- Reads `amount` to tell the user how much was refunded
- On error, reads `error` field to explain the issue to the user

### {next_tool...}

## 4. Agent Conversation Patterns

{Document the typical conversation flows the agent handles.}

### Happy path: {scenario_name}
```
User: "I want to refund my last order"
Agent: [calls get_user_profile] → gets user info
Agent: [calls get_orders(user_id, status=delivered)] → gets order list
Agent: "I found order #456 for $89.99. Would you like a refund?"
User: "Yes"
Agent: [calls process_refund(order_id=order_456, reason="customer request")]
Agent: "Done! $89.99 has been refunded to your account."
```

### Error path: {scenario_name}
```
User: "Refund order #999"
Agent: [calls get_order(order_id=999)] → order not found
Agent: "I couldn't find order #999. Can you check the order number?"
```

### Edge case: {scenario_name}
```
{document unusual but valid scenarios}
```

## 5. Business Rules & Constraints

{Global rules that span multiple tools and entities.}

> **CAPABILITY BOUNDARIES & HARD LIMITS — capture these explicitly (scenario generation depends on it).**
> The scenario generator grounds its targets in this PRD. If a real limit is missing here, it will
> author scenarios the agent **cannot** satisfy — e.g. a target of "generate 100 samples" when the
> agent caps at 50, or "commit the optimizer's exact prompt" when no tool persists a supplied prompt
> verbatim — and the rubric then scores correct behavior as failure. List every hard limit and
> capability the agent does NOT have, as concrete facts:
> - **Numeric caps / quotas** with the actual number (e.g. `max_total_samples = 50`, "≤ 3 revert steps", page size 20).
> - **Things no tool can do** (e.g. "no tool persists a user-supplied or optimized prompt verbatim", "cannot edit a committed revision in place — only append", "cannot delete a user").
> - **A built-in the simulation switches off is an absent tool, not an absent capability.** Switching off `WebFetch`/`WebSearch` does not close a shell's network: state the agent's network reach only as the map (`agent_topology.md`) says it, with its conditions (a confined `Bash`: network open as in your terminal, `PLURAI_SANDBOX=strict` closes it); with no map, state no network limit. Never "cannot reach the web" unless the map says the shell is absent, switched off or closed.
> - **Mode caveats** that change semantics (e.g. "`process_refund` is `mode:mock`: it returns a fixed confirmation and does NOT move money").
> Read tool bodies / config for the real numbers — do not guess. A cap you can't confirm is better
> omitted than invented (the generator is told not to fabricate limits).

> **ENUMERATIONS ARE READ AS CLOSED SETS — finish every list you start.** The mirror of the rule
> above: a limit you omit invents a failure the agent didn't commit, and a list you leave INCOMPLETE
> does the same. Any enumeration in this PRD (entry points, statuses, channels, supported languages,
> allowed actions, valid categories) is consumed by generated judges as a strict allowlist, so a real
> capability you simply forgot to type becomes "not in the allowed set" — the agent does the right
> thing and is marked wrong. Observed live: one entry point missing from a list made a judge flag
> genuinely grounded behaviour. Before you move on, re-read each list against the code and ask *is
> this ALL of them?* If a list is illustrative rather than complete, say so inline — `(examples, not
> exhaustive)` — so the judge is not built as a closed world. Note that these lists are frozen
> into judge prompts at generation time: editing the PRD later does NOT update already-generated
> evals, which have to be regenerated or amended with `/edit-evals`.
> **A complete list still has to say what it is complete OVER.** Write it as
> `<N> <things>, closed over <predicate>` — the predicate is what the judge will enforce, and it is
> almost never "everything of this kind". *"the 3 links the tool is licensed to CONSTRUCT"* is a
> different set from *"the links a correct answer may CONTAIN"*: the first is closed, the second is
> not, and a judge handed the second fails the agent for quoting the customer's own documentation.
> Where both sets exist, give the closed one its predicate and say plainly that others occur outside
> it. If you cannot state the predicate in one clause, the list is illustrative — mark it so.
> For corpus-backed agents, verify each list against the corpus per the Sourcing contract above
> before freezing it.

> **ENUMERABLE GROUND TRUTH — copy it VERBATIM, never paraphrase.** If the agent is checked
> against a fixed reference list — an asset/URL catalog, allowed menus, canned redirect URLs,
> a value enum — paste the literal list here (actual URLs/values). This PRD is the only eval-generation
> input carrying the agent's domain ground truth (a list absent here is absent from
> eval authoring): a paraphrase ("the image URLs are placeholders") produces judges that
> check membership in a list nobody has, which fail the agent for using the catalog's own
> entries. **The same rule covers VERBATIM PROCEDURES**: a documented step sequence the agent
> is graded on (select the item(s) → choose Publish → pick Published → Save) is ground truth
> exactly like a URL list — copy the steps in order with the docs' own control/menu names,
> never a summary. Observed live: a summarized-away publishing sequence made the grounding
> judge fail the agent for using the docs' exact control names as "undocumented specifics".
> **Scope: identifier-shaped lists + graded step sequences, cap ~40 lines / ~2 KB per item —
> NEVER the agent's knowledge/doc prose at large (that is corpus material, summarized as
> usual).** Over the cap → say the list/procedure exists and point at the corpus; never
> silently summarize it away.

1. **Capability boundaries / hard limits:** {numeric caps + things the agent CANNOT do — see callout above}
2. **Authentication:** {what permissions does the agent check? role-based? plan-based?}
3. **Rate limits:** {any limits on operations per user/session?}
4. **Currency:** {what currency? how are amounts formatted? decimal precision?}
5. **Timezone:** {what timezone for dates?}
6. **ID formats:** {UUID? sequential? prefixed like "order_"?}
6. **Pagination:** {how are list results paginated? max results?}
7. **Soft delete vs hard delete:** {are entities deleted or just marked?}
8. **Concurrent access:** {can two sessions modify the same entity?}
9. **{domain-specific rules}**

## 6. Tool Dependency Graph

```
get_user_profile ──reads──→ [user]
                                ↑
process_refund ───writes──→ [user] (balance += amount)
       │
       ├──────reads──→ [order] (checks status, total)
       ├──────writes─→ [order] (status → refunded, full refunds only — per §3)
       └──────creates→ [transaction] (type=refund)
                                ↑
get_transactions ──reads──→ [transaction]
```

{Draw this graph FROM the `Side effects` lines you already wrote in §3 — every write arrow here
must be one of those lines, and a tool whose §3 Side effects say it writes nothing gets NO write
arrow. Do not re-derive it from the code: a second independent authoring of the same fact is where
the two halves disagree, and nothing downstream can tell which half it read. Read arrows may be
added freely. The backend uses this to ensure mock consistency, so a graph that contradicts §3
makes the mock contradict the spec.}

## 7. External Service Contracts

{For each external service the agent interacts with (that we're mocking),
document the API contract as the agent sees it.}

> **A MOCKED service's documented contract tables are copied VERBATIM here, every row.** Where the
> service's own docs carry a per-variant output-format table (one layout per kind / variant / mode
> it returns) or a request-validation / error table (which input is refused, with which status and
> code), paste both as written — no cap, no summary, no corpus pointer. The mock is built from this
> PRD alone: a paraphrased format table makes it invent a layout per call, and dropped validation
> rules make it accept what the real service refuses. **Never write `(see <doc> §n)` in place of the
> content** — env-gen receives only `plurai.config.yaml`, this PRD and `test_mock_contract.py`, never
> the cited file. A service whose docs carry no such table gets none invented here.

### {Service} (e.g., Stripe)
- **Used by tool:** process_refund
- **Operation:** Create refund via Stripe API
- **What the agent sees:** The tool wraps the Stripe API and returns a simplified result
- **Latency:** ~500ms typical
- **Error modes:** card_declined, insufficient_funds, network_timeout
- **Idempotency:** Stripe refund is idempotent by refund_id
- **Output formats (verbatim from the service docs, one row per variant — omit when the docs have none):**
  {the docs' table, copied}
- **Validation / error rules (verbatim from the service docs — omit when the docs have none):**
  {the docs' table, copied}

## Verbatim source appendix — SKELETON (not live until unfenced)

{This template section is deliberately NON-LIVE. The backend matches a bare
`## VERBATIM SOURCE APPENDIX` heading anywhere outside a fence — so a live
template heading kept with no entries would make the backend silently drop
the section from the derived text, with only the linter left to catch it.
The REAL heading therefore lives only inside the fenced skeleton below.
**Unfence and fill the skeleton when Step 9b of the setup skill applies** —
the agent's knowledge is prompt-inlined or a small enumerable doc set, and
the agent is NOT corpus-backed; **DELETE this whole section otherwise** (and
replace this section's heading with the skeleton's when unfencing — the
placeholder heading above must not survive into the shipped PRD). The
unfenced appendix MUST be the LAST `##` section of the PRD: the backend
parser runs the appendix from its heading to the next same-or-shallower
heading (or EOF), so any section you add after it is swallowed into the
appendix and lost as prose.}

This is the byte-exact channel: every entry registers as an
authoritative SOURCE document (judge authority), where the prose above
registers only as derived provenance. The backend parses the appendix
mechanically — format errors don't degrade, they silently DROP documents.

**Knowledge documents only — never source code.** An entry is a document the
agent answers FROM (guide, FAQ, policy, product page). Never an implementation
file, never a system-prompt module, never "(whole file)" of either. This section
is uploaded with the PRD, so pasting implementation ships the customer's IP off
their machine to prove what a `path:line` citation in §3 already proves; and a
code entry registers as a source document no scenario will ever cite, which gets
the groundedness judge skipped set-wide. No document corpus → no appendix.

**Format** — each entry is a sub-heading DEEPER than the appendix's `##`
(use `###`), holding exactly the file's label, followed by one fenced block
holding the file's bytes. The skeleton (unfence + fill, per above):

`````markdown
## VERBATIM SOURCE APPENDIX (COMPLETE)

### faq.md

````
...the exact bytes of faq.md...
````

### refund_policy.md

````
...the exact bytes of refund_policy.md...
````
`````

**The fence-length rule.** A fenced block closes on the first bare run of the
SAME character that is AT LEAST as long as the opener — so an inner fence in
the document's own bytes that is ≥ your wrapper closes the wrapper early: the
rest of the file is cut off and rescanned as appendix structure (its own
`###` headings become phantom labels), and the entry is LOST. Before wrapping
a file, check its longest backtick run and use a wrapper one longer —
minimum 4 backticks whenever the document contains ``` at all.

**The four ways an entry gets lost** (the parser accounts for each and
cancels `(COMPLETE)` on any of them):

1. a sub-heading with NO fenced block after it (the label registers nothing);
2. a fenced block that is never closed (runs to EOF, entry dropped);
3. an inner fence ≥ the wrapper closing it early (the truncated tail is lost
   or misread as new entries);
4. stray unfenced text between entries (the visible half of a cut-short
   document).

**A PARTIAL entry is labelled an EXCERPT.** When the entry holds only part of
a file — the decisive lines of an unprobed node's source, one section of a
long doc — say so in the label (`### pricing.py — EXCERPT (lines 40-118)`),
never a bare path. Under `(COMPLETE)` a whole-file label grants judges silence
authority over the bytes you left out, so they may conclude "the sources do
not mention X" about the half that is missing.

**`(COMPLETE)` semantics.** Adding `(COMPLETE)` to the heading asserts the
entries below are the WHOLE knowledge base — it is what licenses judges to
assert "the sources do not mention X" (negative authority). A file missing
from an appendix marked COMPLETE therefore manufactures false negatives: the
agent answers correctly from the missing file and a judge fails it for
inventing facts. Write `(COMPLETE)` only when the enumerated files really are
all of them; any lost entry cancels it server-side, but do not rely on that.

**Corpus-backed agents: emit BOTH, and keep the token.** A corpus does not
replace this appendix — they are different channels. The corpus is where planted
QUESTIONS come from; the appendix is what gives judges authority over the
customer's documents. A corpus passage always registers open (sampled, never
closed) and it does cancel the REGISTRY-WIDE closed-set assertion — but the
appendix entries keep a PER-DOCUMENT closed scope that survives that
cancellation, and `(COMPLETE)` is what sets it. The result is a third licence
tier, MIXED COVERAGE: judges may say "the sources do not mention X" about the
documents you enumerated here, and nowhere else.

Drop the appendix or the token on a corpus-backed agent and you lose that scope
entirely — every groundedness judge falls back to PARTIAL, where fabrication is
only catchable when the invented text CONTRADICTS an included excerpt. Observed
live: a set whose primary groundedness judge caught one fabricated UI element
and missed six invented procedures. So write `(COMPLETE)` whenever the
enumerated files really are all of them, corpus or no corpus.
