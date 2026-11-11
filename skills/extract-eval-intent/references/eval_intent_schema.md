# eval_intent.yaml schema

This is the document `/extract-eval-intent` produces and `/generate-evals`
consumes. It describes WHAT the customer wants evaluated — distinct
from the PRD (which describes what the agent *does*) and the scenario
variables (which describe the agent's surface).

The backend SKILL reads this as the primary source-of-truth when
authoring `policies_config.yaml`. Sharper input here means sharper
evals.

**THIS FILE IS THE OWNER of the intent's rules, whoever authors the document.** The suite
orchestrator writes the intent inline, in one shot, inside an autonomous flow — that is a
legitimate author, and it is bound by the rules here exactly as a direct `/extract-eval-intent`
invocation is. There is no second copy of them anywhere: a rule stated in the authoring flow
instead of here is a rule the other author never reads.

## v2 — the TYPED intent, and why an untyped one could not hold

Two intents authored from the same PRD one night apart carried **4 goals / 7 bare-label
`must_catch` / 10 `custom_metrics`** and **7 / 8 / 0**; five names appeared in BOTH lists of the
first; only six policy names were shared between the two eval sets that came out. The PRDs were
the same size (659 vs 717 lines). That is one-shot authoring variance on a document with no
shape, and the gate downstream can only bound the damage — the schema is what makes the author
consistent.

The machine-readable form is **`eval_intent_schema.json`** beside this file (`$id:
plurai-eval-intent/2`); it is the normative shape and this document is its explanation. Three
properties are load-bearing:

- **The unit is the AXIS, not the eval.** One axis, one owner, one `AXIS:` line downstream.
  Coverage is credited by (policy, failure-mode) row, so an axis with three failure modes is one
  judge carrying three obligations — folding costs nothing, and it is what stops N absolutes
  co-binding every reply.
- **ONE id space.** An axis id and a failure-mode id are unique across the whole document. The
  night intent named five things in two different lists, and nothing downstream could tell one
  claim from two.
- **Every failure mode names its source.** `traced_to` is required, ≥1, and `source: customer`
  is a legal value — it means the customer asked and no input document says it. Declared, never
  disguised as a PRD reference: the item that shipped flagged *"customer-supplied (not in inputs)
  — never verified"* is the one whose four stable contradicting pairs the census kept re-finding.

```yaml
schema: plurai-eval-intent/2
axes:
  - id: unsupported_figures
    axis: a figure asserted without a tool result behind it
    surface: reply
    failure_modes:
      - id: price_without_lookup
        describes: the reply states a price and tool_events carries no lookup call in that turn
        traced_to:
          - source: prd
            ref: "## Quoting prices"
            quote: "Prices are read back from the catalog; the agent never quotes one from memory."
        catcher: partial_fabrication
preferences:
  rejection_handling: score zero on inapplicable scenarios, don't vacuously pass
```

### Every v2 field, and the constraint the JSON puts on it

Both files declare `additionalProperties: false` at every level: a key that is not in this
table is DROPPED, not merged. Required fields are bold.

| where | field | constraint |
|---|---|---|
| top level | **`schema`** | the literal `plurai-eval-intent/2`. Absent ⇒ the document is read as v1. |
| top level | **`axes`** | ≥ 1. |
| top level | `preferences` | `rejection_handling` and `metric_shapes` only, both free-text one-liners. Unchanged from v1. |
| axis | **`id`** | `^[a-z][a-z0-9_]*$`, unique across the WHOLE document. |
| axis | **`axis`** | What a violation IS, in a few words — the sentence that becomes the policy's `AXIS:` line. No length floor: a short axis in a compact script is as complete as a long one. |
| axis | **`failure_modes`** | ≥ 1. |
| axis | `surface` | where a violation is OBSERVABLE. Exactly one of `reply`, `tool_calls`, `world_state`, `knowledge`, `memory`, `pipeline`. Optional, but it is what lets the recipe fold two axes sharing a guard and a scope, and what lets the co-binding bound count absolutes per reply surface. |
| axis | `notes` | free text for the eval recipe: anything the author wants it to know that is not itself a failure mode. Not a place to restate a mode in prose. |
| failure mode | **`id`** | `^[a-z][a-z0-9_]*$`, unique across the WHOLE document — not merely within its axis. |
| failure mode | **`describes`** | The concrete, observable shape; abstract phrasing here is what produces a vacuous eval. No length floor: concreteness is judged, not counted. |
| failure mode | **`traced_to`** | ≥ 1 entry. |
| failure mode | `catcher` | a canonical catcher tag (the six in the v1 table below) when the shape matches an existing backend recipe, which the backend then lifts verbatim. Omit rather than force-fit. |
| traced_to entry | **`source`** | one of `prd`, `scan`, `trace`, `corpus`, `customer`. |
| traced_to entry | **`ref`** | ≥ 3 characters, and a NAME a reader can resolve: a PRD section heading, a scan finding id, a trace-brief section, a corpus doc id, or the customer's own question. Never a page number, never "the PRD". |
| traced_to entry | `quote` | ≥ 10 characters, verbatim from the source. **REQUIRED when `source` is `prd` or `corpus`** — the JSON enforces this as an `allOf` / `if`-`then` pair, because a paraphrase of a term the judge grades is how one document comes to define "resolved" differently from the other. Optional for `scan`, `trace` and `customer`. |

**A v1 document (`goals` / `must_catch` / `custom_metrics`, described below) is still READ, and
the two shapes are never mixed in one file.** When you are re-authoring one: each `goal` and each
`must_catch` tag becomes an axis; each `custom_metrics` entry's `failure_mode` becomes a failure
mode under the axis it belongs to (which is how the five duplicated names collapse); and each one
needs the `traced_to` it never had — if you cannot name a source for it, that is the finding, not
a formality.

## Top-level shape (v1 — still read, no longer authored)

```yaml
goals: [<string>, ...]               # 2-4 outcomes the customer cares about
must_catch: [<string>, ...]          # named failure modes — see catalog below
custom_metrics:                      # agent-specific evals
  - name: <snake_case>
    description: <one-line>
    failure_mode: <concrete observable failure shape>
preferences:                         # optional global behaviors
  rejection_handling: <one-line>     # how to score inapplicable scenarios
  metric_shapes: <one-line>          # boolean vs. continuous bias
```

All four top-level keys are optional, but `goals` + `must_catch`
should usually appear. A minimal intent (just `goals` + 1-2
`must_catch`) is still better than no intent at all.

## Field-by-field

### `goals` (list[str])

The 2-4 high-level outcomes the customer would be unhappy with in
production. Write them as failure-shaped phrases the backend can
turn into evals.

✅ Good:
```yaml
goals:
  - "Agent must refuse out-of-scope requests rather than hallucinating"
  - "Tool calls must match the user's stated need"
  - "Agent must not commit state changes without explicit approval"
```

❌ Avoid:
```yaml
goals:
  - "Agent should be helpful"               # too abstract → vacuous evals
  - "Quality should be high"                # no observable shape
  - "Make sure everything works"            # not a goal
```

### `must_catch` (list[str])

Named failure modes that MUST be caught. Prefer the canonical tags
when the failure matches an existing recipe — the backend has
hand-tuned implementations for these and lifts them verbatim:

| Tag | Catches |
|---|---|
| `partial_fabrication` | Agent claims an action (added/shuffled/reverted) without a matching tool call firing |
| `non_convergence` | Revert/redo yo-yo (≥2 reverts + many revisions) OR runaway writes (many revisions, zero reverts) |
| `excessive_tool_errors` | >30% of tool calls failed (UI-gate hammering, schema mismatches) |
| `consent_violation` | Agent applied state-changing operations without explicit user approval |
| `unmet_primary_request` | Agent stayed on-topic for 10+ turns but never delivered the user's headline ask |
| `ignored_user_directive` | User gave a clear directive (e.g. "use LLM") 2+ times; agent re-asked / ignored |

Custom tags are accepted for one-off failure modes — the backend will
author a new eval from the description in `custom_metrics`.

### `custom_metrics` (list[dict])

Agent-specific evals that don't fit the catalog. Each entry has:

```yaml
- name: <snake_case>              # becomes the policy_name in policies_config.yaml
  description: <one-line>         # operator-facing context (becomes eval_metric_description)
  failure_mode: <concrete shape>  # what the backend uses to decide eval type
```

The `failure_mode` field is load-bearing. Concrete shapes get good
evals; abstract shapes get vacuous evals.

✅ Concrete failure modes:

```yaml
custom_metrics:
  - name: response_must_cite_source
    description: When the agent quotes data, it must include the tool that produced it
    failure_mode: agent reports a number without a corresponding lookup_x tool call in tool_events

  - name: preview_before_mutate
    description: Agent must show a diff/preview before applying any state mutation
    failure_mode: tool_events shows mutate_x BEFORE an assistant turn containing the word "preview" or "diff"

  - name: label_distribution_diversity
    description: Generated label batches must cover the expected taxonomy with reasonable balance
    failure_mode: coverage * normalized_entropy_balance over generated_samples falls below 0.5
```

❌ Abstract failure modes:

```yaml
custom_metrics:
  - name: be_helpful
    description: The agent should be helpful
    failure_mode: agent is not helpful           # → vacuous eval
```

### `preferences` (dict)

Optional global behaviors. Each field is a one-line free-text hint
the backend uses to bias its authoring. Currently meaningful:

- **`rejection_handling`** — how to score scenarios where the policy
  doesn't apply (e.g. single-label tasks for a diversity policy,
  zero-sample rejection scenarios). The backend framework supports
  marking results as inconclusive rather than vacuously passing;
  setting `"score zero on inapplicable scenarios, don't vacuously pass"`
  nudges the backend toward continuous metrics that surface the
  inapplicability instead of hiding it.

- **`metric_shapes`** — bias for `metric_type: float` continuous
  metrics over `metric_type: boolean` for anything distribution-like
  (diversity, coverage, similarity). `"continuous preferred for
  distribution metrics"` is the standard value.

## What gets dropped

- Anything not in this schema. The backend won't error on extra
  fields, but they'll be silently ignored.
- Implementation details (which `type:` to use, threshold values,
  judge prompts). Those are the backend's job — the intent should
  stay at the "what" level, not the "how".
- Generic eval names without a `failure_mode`. The backend can't
  author a sharp eval from just "we should catch hallucinations" —
  it needs to know what shape the hallucination takes for THIS agent.
