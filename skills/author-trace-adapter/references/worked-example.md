# Worked example: an in-house support export

One complete pass, start to finish, on a format nobody has ever written an adapter for. The
command lines and output below are what a real pass prints; the export lives in the customer's
repository, here under `exports/`.

The artifact is `acme_support_export.json`: one JSON document per conversation, holding an
`events` list that interleaves conversation turns with the router's own bookkeeping.

---

## Step 1 — Triage

The customer says it came from their own support-platform exporter. Not LangSmith, not a span
tree, records are a flat list. So: an adapter, and the format name is `acme_support_export` —
named for the exporter, not for any platform.

## Step 3 — Survey

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/author-trace-adapter/scripts/survey_artifact.py" \
  exports/acme_support_export.json --records events
```

The records are nested under `events`, so `--records` points at them. Here is the report, with
whole fields cut where they repeat something already shown (marked `…`); every retained line is
verbatim:

```
ARTIFACT  exports/acme_support_export.json
container: json   records: events   count: 8

5 distinct record shape(s). Each needs a message rule or an explicit discard — the executor
leaves any record neither accounts for out of the conversation.

── shape 1: 3 record(s) — {actor, event_id, kind, seq, text}
   first at index 0
    actor                                        str
        values: "customer"×2, "policy"×1
    kind                                         str
        values: "turn"×3
    seq                                          int
        values: 1×1, 2×1, 8×1
    text                                         str
        values: "REDACTED"×3
    …

── shape 2: 2 record(s) — {actor, event_id, invocations, kind, seq, text}
   first at index 3
    actor                                        str
        values: "agent"×2
    invocations                                  list  [1/2]
    invocations.*.invocation_id                  str  [1/2]
        values: "8eda51d1-0000-0000-0000-000000000005"×1
    invocations.*.params                         str  [1/2]
        values: "{\"order_id\": \"REDACTED\"}"×1
    invocations.*.tool                           str  [1/2]
        values: "lookup_order"×1
    text                                         null|str
        values: null×1, "REDACTED"×1
    …

── shape 3: 1 record(s) — {confidence, kind, queue, seq}
   first at index 2
    kind                                         str
        values: "routing_decision"×1
    queue                                        str
        values: "fulfilment"×1
    …

── shape 4: 1 record(s) — {event_id, invocation_id, kind, payload, seq}
   first at index 4
    kind                                         str
        values: "tool_result"×1
    …

── shape 5: 1 record(s) — {kind, reason, seq, target}
   first at index 6
    kind                                         str
        values: "escalation"×1
    …

order: strictly increasing wherever it resolves — legal only if every record MISSING it is a discard:
       event_id  (resolves on 6/8)
       seq  (resolves on 8/8)
id:    distinct and non-blank wherever it resolves — usable on a rule that matches those records:
       event_id  (resolves on 6/8)
```

Five things this tells you:

- **`kind` is the primary discriminator**, and `actor` splits the `turn` shape three ways.
- **Shape 2's `text` is `null|str`.** The tool-calling turn says nothing alongside its call. That
  is what `missing: ''` is for, and knowing it now saves a round trip.
- **`invocations.*` shows `[1/2]`** — present on one of the two records in that shape. The other
  agent turn has `invocations: []`, which is why `invocations.0: {present: true}` separates them.
- **`params` is a JSON string.** No declaration needed; the shared projection parses it.
- **Two `order:` candidates, and only one is right.** `event_id` is strictly increasing here only
  because the fixture's redacted uuids are sequential — a property of this file, not of the
  format. Real exports carry random uuids. Pick `seq`. **The survey nominates; you decide.**
And one caveat about the report itself: **`text` and `payload` print their values here only
because this fixture is redacted.** They hold `"REDACTED"`, which reads as a short repeated label,
so the value sampler treats them as enumerable. In a real export those fields hold conversation,
and the report would name the path and its type while withholding the content. Do not read
"values are shown" as a property of the format — it is a property of this file.

## Step 4 — Author

The judgment calls, in the order they come up:

| Record | Decision | Why |
|---|---|---|
| `actor: policy` | `speak: system` | It IS the system prompt this conversation ran under. An honest mapping, not a nearest fit. |
| `actor: customer` | `speak: human` | |
| `actor: agent` + invocations | `speak: ai` + `tool_calls` | Must come FIRST — see below. |
| `actor: agent`, no invocations | `speak: ai` | |
| `kind: tool_result` | `speak: tool` + `tool_call_id` | `invocation_id` pairs it with the call. |
| `kind: routing_decision` | `discard` | Nobody said it. |
| `kind: escalation` | `discard` | A platform handoff marker, not speech. |

Note the contrast with the Claude Code adapter, where a record *typed* `system` is discarded
because there it is the harness narrating itself. **The name is not the evidence; the role is.**

## Step 5 — Converge

The first draft got two things wrong, and the coverage pass found both plus a consequence:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/author-trace-adapter/scripts/survey_artifact.py" \
  exports/acme_support_export.json --adapter exports/acme_support_draft.yaml
```

```
8 records → 6 projected, 1 discarded, 1 UNACCOUNTED

UNACCOUNTED — 1 shape(s) no rule matches. `plurai convert-trace` leaves these
records out of the conversation and only counts them, so a turn among them is missing from every pin.
Every one of them needs a message rule or an explicit discard:

── 1 record(s) at [6] — {kind, reason, seq, target}
    kind                                         str
        values: "escalation"×1
    …

FIELDS THAT DO NOT RESOLVE — 1. The rule matched and then could not read what it
declared. Fix the path, or declare `missing:` if the source legitimately omits it:
    acme_support_export.json record[3]: content found no value at 'text' (the path is absent or null). If this field is legitimately absent for some records, declare what stands in for it with `missing:`; otherwise the path is wrong.

RULES THAT NEVER MATCHED on THIS artifact. Two very different causes, and only you can tell them
apart: an earlier, broader rule shadows this one (rules are first-match-wins, so they go narrowest
first) — a real defect; or this artifact simply holds no such record, which is ordinary in a
heterogeneous corpus and no reason to delete a rule. Check the shadowing reading against a second
artifact before changing anything:
    messages[1] speak: ai  when: {"kind": "turn", "actor": "agent", "invocations.0": {"present": true}}

order: 'seq' resolved on 6 of 8 records.
```

Exit status 1. All three problems in one pass — the tool does not stop at the first.

Read that carefully, because **the last two entries are one defect seen from two sides.** The
draft put the plain `actor: agent` rule before the tool-calling one. So:

- the plain rule claimed record[3] — the tool-calling turn — and then could not read `text`,
  because on that record `text` is `null`. That is the "does not resolve" line.
- the tool-calling rule, which declares `missing: ''` and would have read it fine, never got the
  chance. That is the "never matched" line.

Swapping the two rules fixes both at once. It also matters that this was a shadow rather than an
absent record kind: had the artifact simply contained no tool-calling turn, the same "never
matched" line would have been ordinary and deleting the rule would have been the wrong response.
The `content` failure on the *same* record is what distinguishes the two readings.

The third gap is independent: `escalation` needed its own `discard` with a `because:`.

After both fixes:

```
8 records → 6 projected, 2 discarded, 0 UNACCOUNTED

CLEAN — every record is accounted for and every declared field resolves. Run `plurai convert-trace`.

order: 'seq' resolved on 6 of 8 records.
```

`6 of 8` is correct, and reading it wrongly is easy. Every record in this artifact carries `seq` —
the survey said so in Step 3 with `seq (resolves on 8/8)`. Coverage mode reports 6 because it
gathers ordinals only for records that become messages; the two discards are skipped before the
ordinal is read. So the two numbers disagree by design, and the drop from 8 to 6 is the two
discards, not two gaps.

The rule of thumb: in coverage mode, `order:` should resolve on exactly the projected count. If it
resolves on *fewer*, some record you are keeping has no ordinal, and that is the case the Step 3
warning was about — "legal only if every record MISSING it is a discard".

## Step 6 / 7 — Persist and convert

```bash
plurai convert-trace --export traces/acme_support_export.json \
  --source-format acme_support_export --out-dir out --workspace-root .
```

```
plurai convert-trace: wrote 1 scenario folder(s) to out; publish with `plurai ingest-bundle --bundle-dir out`
{"out_dir": "out", "source_format": "acme_support_export", "scenario_count": 1, "scenarios":
 [{"folder": "thread-acme_support_export", "source_file": "acme_support_export.json",
   "thread_id": "acme_support_export", "message_count": 6, "final_message_type": "human",
   "agent_state_keys": [], "pin_bytes": 1217}], "gate_findings": [], "skipped": []}
```

`resume_pin.json`, with the four middle messages cut:

```json
{
  "schema_version": 2,
  "messages": [
    { "content": "REDACTED", "id": "8eda51d1-…-0002", "type": "system" },
    …
    {
      "content": "",
      "id": "8eda51d1-…-0004",
      "type": "ai",
      "tool_calls": [
        { "name": "lookup_order", "args": { "order_id": "REDACTED" }, "id": "8eda51d1-…-0005" }
      ]
    },
    …
  ],
  "agent_state": {},
  "resume_point": null,
  "provenance": {
    "source_file": "acme_support_export.json",
    "thread_id": "acme_support_export",
    "conversation_id": "8eda51d1-…-0001",
    "export_version": "2.4",
    "adapter": "acme_support_export",
    "adapter_file": "acme_support_export.yaml",
    "record_count": 8,
    "discarded_record_count": 2,
    "minted_message_ids": 0,
    "ordinal_resolved_count": 6,
    "tool_call_rule_matches": 1,
    "tool_call_resolved_count": 1,
    "converted_by": "plurai 0.3.3"
  }
}
```

Four things to check here, and they are the same four on any adapter you write:

- **`minted_message_ids: 0`** — the source recorded real ids and the adapter used them. Omitting
  `id:` would also have "worked": six uuid5s would have been minted and this counter would read
  `6`, silently discarding identifiers the source actually had. A non-zero count is not an error,
  but it should be a decision, and this is where you see which one you made.
- **`record_count: 8` and `discarded_record_count: 2`** reconcile against the six messages. That
  arithmetic is the whole coverage claim, restated after the fact by the executor rather than by
  the survey.
- **`args` is an object**, parsed from the JSON *string* in `params` with nothing declared for it.
- **`agent_state: {}`** — the ordinary answer for a transcript, not a degraded one. It does mean
  this is a messages-only resume; see the resume-fidelity section of `SKILL.md` for what that is
  honest to promise.

Note `pin_bytes: 1217` is the canonical serialization the pin hashes over, not the size of the
pretty-printed file on disk (1554 bytes here). Compare pins with `diff`, not with byte counts.

## Step Verify — the model-free replay

```bash
plurai convert-trace --export traces/acme_support_export.json \
  --source-format acme_support_export --out-dir out2 --workspace-root .
diff out/thread-acme_support_export/resume_pin.json out2/thread-acme_support_export/resume_pin.json
```

No output: byte-identical, with no model anywhere in the second run. That property — not the
first conversion — is what the persisted adapter exists to give.
