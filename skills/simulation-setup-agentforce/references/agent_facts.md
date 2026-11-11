<!-- generated from agent_facts.md of the simulation-setup skill; edit the source and run sync_shared_text.py --write -->
# Agent facts — the typed channel for agent specifics

## The division

**Rules are generic and already exist; agent specifics are FACTS and are written here.**

A rule DERIVED from the agent's own data is semantically nondeterministic — the same repo,
scanned twice, yields same-sized documents carrying opposite law ("never author a target
requiring `<field>`; grade the decline as correct" vs "`<field>` IS authorable via
`<tool>(<arg>=[...])`; a blanket decline is not correct"), and nothing downstream can tell which
roll it read. The backend already holds the invariant both rolls were
reaching for — a target must ground in the licensed tool surface, and an honest decline grades
correct where it does not — and it holds it agent-agnostically, on every agent and every run.
So the scan's job is not to restate that invariant in this agent's vocabulary. Its job is to
supply the facts the invariant consumes.

A fact is a MEASUREMENT or a QUOTATION. It re-derives to the same value on the next scan, so
there is no prose for a re-scan to invert.

## Where the facts live, and why

They live in `simulation_prd.md`, in one `## Agent facts` section, placed before the verbatim
appendix (which must stay the last `##` section).

Two mechanical reasons, both worth knowing before someone proposes moving them:

- The grounding auditor's fact surface is **the PRD minus the routed `## Scenario design
  constraints` span**. A fact has to be inside that surface to ground anything at all; the
  directives section is routed out of it by design.
- `scenario_variables.yaml` is validated by a model that ALLOWS unknown keys. A new key there
  passes validation and is read by nothing — it would be an inert file section that looks
  authored. `scenario_variables.yaml` keeps its existing typed fields and gains none.

## Format

One fenced `yaml` block under the heading. Every entry carries all five keys:

Three examples, deliberately from three different verticals — the vocabulary is the same
whatever the agent does, and a fact set that reads like one domain's is a fact set someone will
pattern-match instead of measure:

```yaml
facts:
  - kind: data_profile
    subject: claim.adjuster_notes
    predicate: null_on_all_rows
    evidence: "null on 4,102/4,102 claim rows; the text appears in the free-text `summary` on 3,118/3,540"
    source: artifacts/data_profile.md

  - kind: tool_surface
    subject: list_policies.coverage_tier
    predicate: undocumented_to_model
    evidence: "accepts `legacy_plus`; the value is absent from the tool description the agent is given"
    source: policies/tools.py:214

  - kind: seedability
    subject: delivery_eta
    predicate: not_seedable
    evidence: "written only by the live carrier webhook; no mock write path and no schema field"
    source: plurai.config.yaml
```

**An agent with NO tool surface, no profiled data set and no quoted rule writes NO facts.** The
section is a channel for MEASUREMENTS, not a quota: an empty `facts:` list from an agent that has
nothing to measure is honest. The lint still opens one finding on it (no `instruction_sources`),
which you accept with the reason that the agent has no instruction text. What is not honest is
filling it from the code you happened to read — a fact you did not measure steers generation
exactly as hard as one you did.

- **`kind`** — closed: `data_profile` · `tool_surface` · `seedability` · `rule`.
- **`subject`** — the machine-derived name the fact is about: `<entity>.<field>`, `<tool>`,
  `<tool>.<arg>`, or an `agent_context` field. Copy it from the config / profile; never invent a
  label for it.
- **`predicate`** — closed per kind, from the table below. This is where the polarity lives, and
  typing it is the whole point: a closed token cannot come back inverted next scan.
- **One fact per pair** — two facts cannot share a `subject` and a `predicate`: the pair is the id
  the contract reports its verdict under, and a repeat renders two verdicts under one id. A second citation
  for the same claim goes in the first fact's `evidence`, never in a second fact.
- **`evidence`** — the measurement, with its number, or the quoted bytes. One line.
- **`source`** — where it was computed or read: `artifacts/data_profile.md`,
  `artifacts/probe_report.md`, `path/to/file.py:LINE`, `plurai.config.yaml`, or `corpus:<doc>`.
  **The pre-generation contract RESOLVES it, and exactly two outcomes BLOCK.** A path that does
  not exist under any scanned tree blocks, and so does a `path:LINE` citation whose `subject` sits
  somewhere ELSE in that same file — the finding names the line it is actually on. A citation off
  by eleven lines resolves perfectly against a file that exists, and the claim standing on it was
  never checked by anyone; one shipped, and cost an input-edit round. `path:41-42`, `path:41:5`
  and `path#L41` are read the same way, on the first line named.

  Everything else is REPORTED and does not stop the build, which is a contract worth knowing
  because it decides how much a `source:` is worth:

  - **A fact with NO source is blocking.** A source that is not a path and not `corpus:<doc>` —
    a sentence like `the probe report` — is *not*: it is filed as "not a filesystem citation, so
    nothing here could resolve it", and it establishes exactly nothing. Prose is not a cheaper
    way to satisfy this field; it is a way to leave it unsatisfied without a finding. Write the
    path.
  - A cited file this machine cannot read (undecodable, unreadable, larger than a few MB) leaves
    the LINE unchecked, and says so. It is never counted as a resolved citation.
  - A fact whose `subject` is absent, or whose leaf is shorter than three characters, cannot have
    its line checked at all — and when that is true of every fact, the whole citation check is
    reported BLOCKED rather than clean.
  - The `subject` occurring NOWHERE in the cited file is reported, not blocked: an absence-shaped
    predicate (`undocumented_to_model`, `omits`, `rejects`, `absent_from_source`) cites precisely
    the place its subject is not, so that is the CORRECT citation for it.
- **`fields`** — OPTIONAL, `tool_surface` only, and **the one key that makes a fact ACT**. A list
  of the tool's declared `output_fields` that this fact puts out of the model's reach. Write it
  whenever the `subject` is a `<tool>` or a `<tool>.<arg>` and the fact bears on specific outputs.

  The backend derives its answerability fence from `output_fields`, and subtracts a field only on
  the **typed** name — never by reading your `evidence` prose, because prose states what a tool
  CAN do as readily as what it cannot ("a status can be reported, a unit count cannot"), so
  matching a field name inside it deletes whichever one the sentence mentions. A fact whose
  subject names an argument therefore has no way to say which OUTPUT that argument gates, and is
  reported to the author instead of removed. In practice every subject is a tool or an argument, so a fence is rarely removed.

  What that costs: the fence keeps listing the fields the tool RETURNS while your own fact says
  the model is never told how to ask for them. The knowledge planner then plants evidence on that
  surface, the backend auditor flags the scenario `impossible_success_criteria` — correctly — and
  the authoring session overrules the auditor by citing the fence. Every one of those planted
  evidences becomes a manufactured red against an agent behaving correctly.

  ```yaml
  - kind: tool_surface
    subject: get_account.fields                # an ARGUMENT — cannot name an output by itself
    predicate: undocumented_to_model
    evidence: "caller-supplied names reach getattr with no allow-list, so 'billing_history' and 'audit_log' are retrievable; the tool description the model is given lists neither"
    source: accounts/tools.py:88
    fields: [billing_history, audit_log]       # ← the outputs this fact puts out of reach
  ```

  Rules: every entry must be a declared `output_field` of the subject's tool (one that is not is
  reported and ignored); the backend never empties a tool's surface, so a fact naming every field
  leaves one standing; and when `fields:` is present the subject token is never used — an argument
  whose name collides with an output field (a tool declaring `fields` as both an argument and a
  returned column is ordinary) would otherwise subtract the wrong one.

- **`check`** — OPTIONAL, and **the key that makes a fact RE-RUNNABLE**. The sweep you already
  performed to write the fact, written down as data, so the pre-generation contract can execute
  it against the corpus instead of trusting the sentence. Write it for any fact you decided by
  reading the corpus — every universal negative, every closed set counted out of the documents.

  ```yaml
  - kind: data_profile
    subject: adjacent_service_areas
    predicate: absent_from_source
    evidence: "roadside assistance, rentals and towing appear in none of the guides, the policy table or either prompt file"
    source: assets/docs/
    check:
      kind: absence           # absence | existence | count — the polarity, typed
      terms: [roadside, rental, towing, "courtesy car"]
      match: any              # any (one term is enough) | all (every term, in the SAME unit)
      scope: ["docs/*"]       # OPTIONAL fnmatch globs over corpus unit ids; omit = whole corpus
      field: policies.*.coverage_url  # OPTIONAL — match only inside a flattened structured field
      expected_count: 3       # REQUIRED for kind: count, ignored otherwise
  ```

  Rules: `terms` are the LITERALS you actually searched for, never a paraphrase of `evidence` —
  the contract reports its verdict in your name, so a term you did not sweep is a claim you did
  not make. They are never words a user must say: an absence fact's optional `probe_ask` is the
  question a user would ask for the missing thing, in their own words.

  **`terms` are written in the CORPUS'S language, not in yours.** The check is a literal match
  over the corpus units; an English sweep across a Hebrew, Japanese or Spanish corpus matches
  nothing and returns a clean ABSENCE verdict for a claim nobody tested — the same
  checked-versus-never-looked collapse the whole `check:` block exists to remove, arriving through
  the block itself. Write the terms as the documents spell them, and add the English spellings
  beside them (with `match: any`) only when the corpus really is mixed.

  `scope` globs match the unit ids the contract prints (`<root-dir>/<relative-path>`
  when several corpus roots are passed); a scope that addresses no unit is reported as a
  contract defect, not as a pass. A fact with no `check` is carried as **asserted, unverified —
  no static route to check this**, named in the report AND in its headline, which makes the
  PROCEED provisional over it. Nothing is ever inferred from your `evidence` prose, so an
  asserted fact stays a sentence: honest, and still untested. Write the `check` wherever reading
  the corpus settles the claim — an untested premise steers generation exactly as hard as a
  tested one.

| kind | predicates |
|---|---|
| `data_profile` | `null_on_all_rows` · `closed_set` · `distribution` · `recoverable_from_text` · `absent_from_source` · `conflicts_across_sources` |
| `tool_surface` | `returns` · `omits` · `accepts` · `rejects` · `undocumented_to_model` · `matching_semantics` · `hard_limit` · `writes` · `id_stability` · `ordering` · `idempotency` · `source_unbound` |
| `seedability` | `seedable` · `not_seedable` |
| `rule` | `instructs` |
| `run_setting` | `harness_differs` |

`not_seedable` never applies to a field of the agent's own state class (`agent.state_class`): the harness writes it. Nor to a world type the mock holds, or its fields: the scenario seeds its rows. A harness-owned world type (`owned_types`, or every type under `owner: agent` / `external`) is either delivered (an entity block, so every scenario seeds it) or declined by one `not_seedable` fact whose `subject` is the type and whose `source` cites why: the `plurai_sim/` harness line (`path:LINE`) that makes it unseedable, or the declared lane that carries it (`.document_fields`). An uncited decline is refused.

`conflicts_across_sources`: two of the customer's inputs state one value so that no behaviour satisfies both. A source that is silent on, or narrower than, a case another source covers is not a conflict; when that source is the agent's own instruction or tool set, it is an agent finding (file it there), and the other source stays the rule.

### `rule` — the agent's own rules, quoted

Declare the text the model is given as instructions (system prompt, task and agent configs, the
file or range that fills a template slot with instructions, each live sub-agent's prompt)
under `instruction_sources:`, beside `facts:`. Every instruction line in them is either cited by
one `rule` fact per instruction — a sentence wrapped across lines cites its range, `path:LO-HI`, and
its `evidence` quotes the whole range — or declined by an
`unrowed` range whose `class` is `not-an-instruction` (heading, persona, example, table),
`duplicate-of-<subject>` (naming the `rule` fact that already rows it) or `not-loaded` (an
instruction the agent never reads; `source: path:LINE` cites the load site that reads other
instructions instead, and the lint checks it resolves). A line of template slots only takes neither
a `rule` nor `not-an-instruction`: decline it `filled-by` (`source:` the declared `path:LO-HI`
range or ranges that fill it with instructions) or `data-slot` (`source: path:LINE`, the line that
fills it with data and names each slot). The backend asks the eval
set to own or decline each `rule` fact with a row; the lint reports a line with neither (`⚠`). A tool's
description is a `tool_surface` fact, never a `rule`.

```yaml
instruction_sources:
  - path: agent/prompts.py
    lines: 3-168
    unrowed:
      - {lines: 3-6, class: not-an-instruction}
facts:
  - kind: rule
    subject: agent/prompts.py:14       # the rule's own location — one fact per rule
    predicate: instructs
    evidence: "Use tools efficiently: in most cases 1-3 tool calls are enough."
    source: agent/prompts.py:14
```

`evidence` is the sentence the PRD already quotes from the agent, byte for byte (the lint checks
it). It is the one `evidence` allowed to be an instruction: it states what the agent was TOLD,
not what anyone downstream should do.

### `run_setting` — a run setting the harness cannot take from production

A run setting (a step cap, a timeout, the model, a token limit) the harness passes with a value
other than the one production's caller sends, or where shipped callers send different values
(`simulation-setup` Step 2). `subject` is the setting as spelled on the production line, `source`
is that line, and `evidence` gives every value with its line: each caller's, any server-side clamp
or deploy-config maximum, the driven entry's default, and what the harness passes. A harness that
already passes production's single value writes no fact; its cited line is the record.

```yaml
  - kind: run_setting
    subject: max_steps
    predicate: harness_differs
    evidence: "web client sends 200 (web/src/api.ts:88), clamped to 150 by the server (server/config.py:40); the CLI sends none, so the entry default 50 applies (cli/main.py:12, agent/client.py:31); the harness passes the entry default"
    source: web/src/api.ts:88
```

A `harness_differs` fact explains an agent red only when the transcript shows that setting's
mechanism firing: the cap reached, the timeout hit, the routed model failing the call. A red
where the setting never fired is judged as if the fact were absent.

### `source_unbound` — a retrieval source the agent's own config leaves empty

A retrieval or knowledge tool (a `grounding.surfaces` tool) that takes its source through a
binding — an index or collection name, a knowledge-base or RAG-config id, a file path — is
`source_unbound` when the value that REACHES the retrieval call is empty after every default the
agent's own code applies. Trace the binding from the config to the call: a `cfg.x or "default"`
fallback binds it. Write it only from what the repo carries:

- **A value supplied at deploy time is unknown, never unbound.** An env var, a secret, an org or
  console setting the repo does not hold: write no fact for a value you cannot read.
- **Cite the binding line.** `subject` is `<tool>.<input>`, and `source` is the line that binds
  that input, so the citation check finds the subject there; the empty value and any related
  setting go in `evidence` with their own lines. Citing the config line instead would BLOCK
  (the subject is not on it).
- **Type what the empty value means: `empty_means`**, required, cited from the platform's docs or
  code in `evidence`. `none` — nothing is searched; `platform_default` — the platform searches a
  default source, so the binding is bound and you write NO fact; `unknown` — neither docs nor code
  says.
- **`empty_means: none` is a defect hypothesis, not a boundary.** No `check:` block, and never a
  licence to excuse a decline: the backend answers it with an answer-delivery eval that fails on a
  decline. `unknown` carries no such eval.
- **It earns an `agent_findings.md` entry**: status `potential`, finding id
  `agent.<tool>_source_unbound`, severity **high** for `none` and **medium** for `unknown`, "how to
  confirm: check the platform's knowledge-source setting for `<tool>`". The delivery eval is its
  probe; a red there promotes it to `confirmed`.

```yaml
  - kind: tool_surface
    subject: search_policy_docs.knowledgeBaseId
    predicate: source_unbound
    empty_means: none
    evidence: 'config/agent.yaml sets knowledge_base_id: "" (line 12) and agent/tools.py applies no fallback; the search API returns no results for an empty id (docs/search_api.md:40)'
    source: agent/tools.py:88
```

### `closed_set` carries a `detector:` block

A `closed_set` fact says the sources enumerate this field COMPLETELY. That licenses a judge to
treat a value the agent produces that is not on the list as one the sources never declared — a
powerful licence, and until the detector ladder existed only URLs had anything in code behind it.
Every other closed class was a sentence in a judge's prompt with nothing checking it, and nobody
had to say so.

So a `closed_set` fact declares whether a deterministic check is REQUIRED, and supplies what proves
the check works:

```yaml
- kind: data_profile
  subject: leaders.name
  predicate: closed_set
  evidence: "4 rows in assets/leaders.yaml; the roster is the whole leadership per the About page"
  source: assets/leaders.yaml:1
  detector:
    required: false                                          # a real boolean, never "false"
    negatives: ["Rowan Okoroo", "Chief Executive Office"]     # >=1 literal the detector MUST catch

- kind: data_profile
  subject: orders.code
  predicate: closed_set
  evidence: "the 8 codes in assets/orders.yaml are every order code the agent may cite"
  source: assets/orders.yaml:1
  detector:
    required: true
    negatives: ["AB-9999"]
    candidate:                                               # OPTIONAL — what the scan lifted, with provenance
      rung: induced
      pattern: "^[A-Z]{2}-\\d{4}$"                            # must match EVERY member, or unanimity refuses it
      source: app/models.py:41
```

The roster above declares `required: false` on purpose, and it is the common case: **a detector
over human names is refused, always.** One edit from a name is its plural, its genitive or its
possessive — ordinary correct language — so a distance rule over `Rowan Okoro` reds a reply that
says `Rowan Okoro's`. Only members carrying a DIGIT (order codes, SKUs, ticket refs, employee ids)
can be checked that way, because an identifier inflects in no language.

- **`required: true`** — the SCAN derives a detector from the members, runs six gates over it
  (unanimity, sample floor, specificity measured over your own prose, script agreement, silence on
  the sources, and a positive control against your `negatives`), and REFUSES to proceed if none
  passes. The refusal names the ladder's whole descent and what to do about it. The backend then
  re-runs the same vendored ladder over the registry it builds, which holds different bytes — the
  PRD's verbatim appendix and the sampled corpus passages, not your whole source tree.
- **`required: false`** — closed, judge-only, by declaration. A legitimate answer, and reported as
  info so the choice is visible in `pre_generation_findings.md`. It does not turn detection OFF:
  the ladder still runs, and if it activates the class IS code-checked (and the report says so).
  What `required: false` changes is whether a refusal blocks the scan.
- **no `detector:` block** — advisory: "say `required: true|false`". This is where every PRD
  written before the key existed sits, and naming it is what gets the declaration added.
- **`negatives` are mandatory when `required` is true.** Specificity proves the detector fires on
  nothing wrong; these prove it fires on something right. Without one the requirement cannot fail,
  which is a rubber stamp. Write values that are NOT members and that a wrong answer would look
  like — a corrupted name, a plausible-but-absent code — not obvious nonsense.
- **`candidate` is a PROPOSAL, not an active detector.** The scan can lift one from the customer's
  own code (a schema `Literal[...]`, a validation regex, a link constant, a constant collection of
  codes). An `induced` candidate's `pattern` is run through all six gates, exactly as a config
  override is — a candidate lifted from an enum passes them easily; one induced from three product
  codes will not, and should not. A `shape` or `near_miss` candidate names a RUNG, and the rung is
  chosen by the members, so only the pattern is executable.

Expect classes to be REFUSED. Names, job titles, departments and short ids fail outright, and that
is the correct outcome — a licence with nothing behind it is worse than no licence. When one is
refused and you know the format, declare it in `plurai.config.yaml` under `closed_class_detectors:`;
it is validated by the same six gates, never trusted, and it reaches BOTH the scan gate and the
detector the run is scored against.

One asymmetry worth knowing: a pattern YOU declare answers the pre-validated sample floor (3
members), not the induction floor (8 members over 2 record families). Inducing a skeleton from a
handful of strings is a coincidence; declaring one you read off your own code is not, so the floor
that exists to justify the inference does not apply to it. Every other gate runs unchanged, and
specificity — does this fire on your own prose? — is the one that usually decides.

The last four `tool_surface` predicates cover what a call CHANGES and what survives it. They earn
a place because a tool that changes state is the one a probe may never exercise — Step 8c.2
excludes any tool with a non-null `descriptor.writes_to` — so for a mutating tool the typed
`source` is often the only evidence these claims will ever carry. (`ordering` is the exception:
on a read-only tool a probe CAN establish it, and then `source: artifacts/probe_report.md` is the
stronger evidence. Use it when you have it.) Record one only where the agent has the surface for
it; a tool with nothing to say under a predicate gets no entry, not an entry saying nothing.

- **`writes`** — what one call changes, and **under what condition**. An unconditional line asserts
  the tool ALWAYS writes; if a call can succeed without writing, that path belongs in the evidence.
  Record it for a tool that can write. A read-only surface needs one entry at most, not one per
  tool restating that nothing changes.
- **`id_stability`** — whether a handle the agent hands out survives the operations that touch
  it, and which ones mint a new one.
- **`ordering`** — the order a call returns or applies things in, when anything downstream depends
  on it.
- **`idempotency`** — what a second identical call does.

When a tool refuses for more than one reason, the `rejects` fact says which precondition
fires **FIRST**, because that decides which refusal a scenario actually meets. A tool
documented as "needs A, B and C" whose check for A runs before the others will never show
the operator a B-failure or a C-failure, so a scenario authored against the B error path
grades an outcome the agent cannot produce. Read the order off the body and state it.

A claim in these four classes with no `source` is the single most expensive kind of error this
document can carry: it is read as a contract by the generator and by every judge, so a wrong one is
authored into every scenario built on it. If you did not read it, do not write the fact — an absent
fact costs a thinner suite, a wrong one costs a scored run.

The vocabulary is closed on purpose. A fact you cannot express in it is either a rule wearing a
fact's clothes, or a genuine gap in the vocabulary — say which in your hand-off; do not widen the
list in one agent's PRD, where nothing else will ever read the new token.

## The render / action surface — `render_actions:` and the `RENDER_ACTIONS` fact

An agent whose actions are client-side RENDERS (`render_chart`, `render_card`, navigation, a
hand-off verb) has no tool to mock, so its `plurai.config.yaml` says `tools: {}` by design — a
surface that is declared nowhere typed is one no completeness check can see, and a render the
agent invents on it is graded by nobody. The surface is typed in two halves, and the lint reads
both:

- **What the agent HAS** — the config, top-level `render_actions:` (a list of verb names; `[]`
  means "none, by declaration"; omit the key when the agent has no such surface). Written at
  `/simulation-scan-agent` Step 8 beside `tools:`. `verify_config.py` shape-checks it as a
  PRE-FLIGHT for a real consumer, not as a plugin convention — see its `_KNOWN_ROOT_KEYS` note
  (`skills/simulation-setup/scripts/verify_config.py`) for who reads the key and how they refuse
  a malformed one.
- **What the agent has NOT** — ONE fact in this section under the reserved subject
  `RENDER_ACTIONS`, kind `tool_surface`, predicate `omits`, whose `check:` is the absence sweep
  the scan contract executes. Its `terms` are the verbs the agent does NOT have — the ones an
  agent in this domain would plausibly invent — never a verb the config lists:

  ```yaml
  - kind: tool_surface
    subject: RENDER_ACTIONS
    predicate: omits
    evidence: "the render surface is render_chart, render_card, open_panel; no video, audio or download render exists anywhere in the action dispatcher"
    source: app/actions.py:12
    check:
      kind: absence
      terms: [render_video, play_audio, download_file]
      match: any
  ```

`lint_prd.py` reports a declared `render_actions:` verb the PRD never names (coverage), a
declared surface with no closing fact, a closing fact over an undeclared surface, a closing fact
with no `check:`, and a sweep that names a verb the declaration says the agent has.

## Placement is section-typed

This section has exactly one legal place: its own H2, outside the sections the auditor routes
out of its fact surface (`## Scenario design constraints`) and BEFORE the verbatim appendix
(where the lint and the scan contract stop reading). A facts block anywhere else is a lint
ERROR (exit 1) naming the section it sits in — nested under the design heading, or after the
appendix — because a typed fact the auditor cannot read grounds nothing, and no disposition an
author can write makes it one. The prose tier (a census stated as a sentence in the directives
section) is an open finding like any other (exit 4); its remedy is this section.

## What makes a line a fact

Three tests, all cheap:

1. **Name the check that produced it.** If you cannot say which command, probe or file read
   yields this value, it is not a fact — it is a reading, and it belongs in prose with an
   `(inferred)` marker.
2. **It survives re-derivation.** Run the check again next scan and the same `predicate` comes
   back. A sentence whose truth depends on how you weighed two considerations does not.
3. **It says nothing about what anyone should DO.** No "author", "never author", "grade",
   "is correct", "is authorable". Those verbs are the tell.

## Forbidden: agent-derived law

Do not write, anywhere in the PRD or the variables file:

- **target-shape rules** — "never author a target requiring `<field>`", "`<field>` is authorable
  via `<tool>`";
- **grading rules** — "grade the decline as correct", "a blanket refusal is not correct
  behaviour", "counts as a pass";
- **authoring bans or licences derived from this agent's data or tool surface** — the same
  sentence in either polarity.

Each of those is the generic invariant, re-stated in one agent's vocabulary, by a process that
can state it either way round. Write the measurement instead and let the invariant consume it:
`claim.adjuster_notes` / `null_on_all_rows` / "4,102/4,102" says everything the prohibition
said, and it cannot come back saying the opposite.

Genuine DESIGN steering is unaffected and stays in `## Scenario design constraints`: scenario
families and their anchors, what an opening may not name, combinations known to be unsatisfiable,
"family B starts mid-task". Those are decisions about how to shape a test set, not readings of
the agent's data.

## Checking it

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/lint_prd.py" \
  plurai_sim/simulation_prd.md --config plurai_sim/plurai.config.yaml
```

It parses the block, names an unknown `kind`/`predicate`, a fact missing its `source`, and
`evidence` written as an instruction — plus the forbidden classes above wherever they appear
outside the verbatim appendix. A finding stays open (exit 4) until the PRD is fixed or the finding is
accepted with a recorded reason (`--accept <fp> --reason "…"`); a repeated pair, above, is an error (exit 1).

The `check:` grammar is validated by its only consumer,
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/scan_facts.py` (run at
`/simulation-scan-agent` Step 3c), which names every declared check that does not compile and
forwards it as written; the contract drops it with an advisory item and decides the rest. A
dropped fact decides nothing, so fix every one it names.
