# The adapter language

The complete contract for a `plurai_adapters/<format>.yaml` document, as the SDK's executor
(`plurai.trace_adapter`) actually enforces it. Read this while authoring; the executor is the
authority and this file tracks it.

Two properties shape everything below:

- **It resolves, it does not compute.** There is no arithmetic, no function call, no slicing, no
  recursive descent, no jq. A mapping can say WHERE a value is; it cannot express a transformation
  of it. That is what makes an adapter safe to load from a customer's repo and run unchanged.
- **It is closed.** Every model is `extra='forbid'`, so a key you invent is a named error at load
  rather than a directive silently ignored. If you find yourself wanting a field that is not
  listed here, the answer is that it does not exist.

---

## Document

```yaml
format: claude_code              # REQUIRED. Must equal the file's stem.
description: Claude Code session logs (~/.claude/projects/**/*.jsonl)   # REQUIRED, one line.
container: jsonl                 # json (default) | jsonl
records: null                    # json only: path to the record list. Omit if the doc IS the list.
suffixes: ['.jsonl']             # extensions to glob when pointed at a DIRECTORY. Default ['.json'].
emits_tool_calls: true           # REQUIRED bool. Must agree with the rules below.
order: null                      # optional path to a per-record ordinal.
agent_state: null                # optional path to a state object. Omitted means {}.
provenance: {}                   # extra provenance keys, each a FieldSpec read from the ROOT.
discard: []                      # ordered DiscardRules.
messages: []                     # ordered MessageRules. At least one.
merge: null                      # optional {by: <path>}: records sharing a reply id are one reply.
```

| Field | Rule the executor enforces |
|---|---|
| `format` | Non-blank, and the loader requires it to equal the filename stem. |
| `description` | Non-blank, one line. Printed when a `--source-format` fails to resolve, so make it name the producing tool. |
| `container` | `json` = one artifact is one JSON document. `jsonl` = one line is one record. |
| `records` | **`json` only.** Setting it with `container: jsonl` is refused — every line is already a record, so there is no document to descend into. |
| `suffixes` | Non-empty list, each including the leading dot. Wrong here means a `.jsonl` corpus is reported empty by a `*.json` glob. |
| `emits_tool_calls` | Cross-checked at LOAD against whether any rule maps `tool_calls`, in both directions. Declaring `true` with no mapping, or mapping calls with `false`, is refused with no artifact needed. |
| `order` | Where declared it must resolve on EVERY projected message and strictly increase. See *Ordering* below. |
| `agent_state` | A bare path (not a FieldSpec). Must resolve to exactly one object. Omitting it yields `{}`, which is the ordinary answer for a transcript. |
| `provenance` | Keys in `RESERVED_PROVENANCE_KEYS` are refused, in two groups. The executor's own stamps are evidence ABOUT the adapter (`adapter`, `adapter_file`, `record_count`, `discarded_record_count`, `minted_message_ids`, `minted_id_namespace`, `minted_id_recipe`, `ordinal_resolved_count`, `tool_call_rule_matches`, `tool_call_resolved_count`, `merged_record_count`): a document that could write them could claim to be a different reader than the one that ran. The converter's own keys record what it did to the pin (`source_file`, `converted_by`, `origin`, `cut_turn`, `agent_state_at`, `state_withheld`, `trimmed_mid_turn_tail`, `resume_seed_messages`, `user_turns`, and the normalisation and drop counts `messages_channel`, `normalized_content`, `dropped_invalid_tool_calls`, `dropped_orphan_tool_results`, `reordered_user_turns`, `history_before_capture`, `unrecognised_record_count`, `unrecognised_shapes`, `folded_terminal_answers`): a declared value would claim a cut, a trim, a state, a turn numbering or a drop no step produced. The SDK's `RESERVED_PROVENANCE_KEYS` is the authority when the two lists differ. Identity keys stay declarable — `thread_id`, `trace_id`, `scenario`, `run_name` and the like identify the artifact, not the reader. |
| `messages` | `min_length=1`. |
| `merge` | Optional `{by: <path>}`; `by` is a non-empty dotted path with no empty segment. See *Merging one reply's records* below. Needs an SDK whose `AdapterDocument` has the field (`'merge' in plurai.trace_adapter.AdapterDocument.model_fields`); an older one refuses the key as unknown. |

---

## Paths

Dotted segments. Three kinds, and only three:

| Segment | Means |
|---|---|
| `foo` | dict key `foo` |
| `3`, `-1` | integer index into a list (ASCII digits only) |
| `*` | map over a list — every element |

A path resolves to a LIST of every value it reaches, in document order. Empty means absent.

Two details that decide real mappings:

- **The dict branch is checked before the integer branch.** A format whose keys are digit strings
  (`{"0": ..., "1": ...}`) is read as the mapping it is, not indexed as a list it is not.
- **A `*` path can resolve to many values, and most places refuse that.** `id:` and
  `tool_call_id:` each carry exactly one value, so a `*` path that finds two is a refusal telling
  you to narrow it. `content:` is the one exception: the values it finds are that message's content
  list (see *FieldSpec*). `*` belongs in a `when:` (where it means "any value here matches") or
  inside a `from:` that a `where:` then filters.

There is no way to write "the last element matching X", "the concatenation of", or "the parent of".

---

## Matchers (`when:` and `where:`)

A predicate is a dict of `path: matcher`. All entries are ANDed.

| Form | Means |
|---|---|
| scalar — `type: assistant` | some value at the path equals it |
| list — `type: [tool_use, tool_result]` | some value at the path is one of these |
| `{present: true}` / `{present: false}` | the path resolves to a NON-NULL value / to nothing-or-null |

Three rules that catch people out:

- **Numeric types must agree.** Python's `True == 1 == 1.0` is disabled here. `isMeta: true` does
  not match `isMeta: 1`, and `version: 1` does not match `1.0`. JSON distinguishes them lexically,
  so writing one and meaning the other is a mistake worth a refusal.
- **`{present: true}` does not count a resolved-but-`null` value.** In JSON an absent key and a
  null one both spell "the source did not record this", and this agrees with `missing:` and with
  extraction. The shape that makes this matter is `"tool_calls": null` on a non-calling assistant
  turn: under a truthiness test the rule matched and then the record it had claimed was refused.
- **A `{present: ...}` mapping must be exactly that one key with a bool.** Anything else is refused
  at load rather than interpreted at run time.

An empty predicate is refused everywhere EXCEPT the `when:` of a message rule (see *Rules*).

---

## FieldSpec

Where one pinned field's value lives. A bare string is the common case:

```yaml
id: uuid
content: message.content
```

The mapping form adds a filter, for block-structured formats where the field sits inside one
element of a content list:

```yaml
content: {from: message.content, where: {type: tool_result}, take: content}
```

| Key | Means |
|---|---|
| `from` | REQUIRED path. (Written `from:` in YAML; it is the model's `source` field.) |
| `where` | Keep only the elements of `from` matching this predicate. `from` must then hold a LIST — holding a dict or string is a refusal naming the type it found. |
| `take` | A path read from the selected element, once `where` has found it. |
| `missing` | The stand-in when the path resolves to nothing or to `null`. |

**`missing:` is the only way a field is allowed to be absent, and its PRESENCE is what matters —
`missing: null` is not the same as omitting the key.** This is the fail-closed half of the design:
without it, a path that stops resolving because the format drifted produces a refusal naming the
path, instead of a pin quietly full of empty strings.

**Selecting more than one value is never "the first of several".** On `content:` the selected
values are one message's content LIST, in document order — carried verbatim, never joined — so a
turn recorded as several text blocks keeps every block. On every other field it is a refusal: an
id or a provenance value names one thing, so two candidates is a question for you, not a choice
for the executor.

---

## Rules

### `discard:` — records declared not to be conversation

```yaml
discard:
  - when: {type: summary}
    because: A session-resume summary line, written by the CLI rather than by either party.
```

`when` must name at least one path; an empty predicate would discard the whole artifact and report
success. `because` must be non-blank — a discard is an enumerated, reviewable claim about the
format, which is what makes the document exhaustive rather than a filter that happened to match.

### `messages:` — records that become pinned messages

```yaml
messages:
  - when: {type: assistant, message.content.*.type: tool_use}
    speak: ai
    id: uuid
    content: {from: message.content, where: {type: text}, take: text, missing: ''}
    tool_calls:
      from: message.content
      where: {type: tool_use}
      name: name
      args: input
      id: id
```

| Key | Rule |
|---|---|
| `when` | May be empty ONLY here, and only sensibly on the last rule. See below. |
| `speak` | One of `human`, `ai`, `tool`, `system`, checked against the pin schema's own union. **No default, no nearest-neighbour.** |
| `id` | FieldSpec. Omit when the source records no id, which mints one. |
| `content` | FieldSpec. REQUIRED. |
| `tool_calls` | ToolCallSpec. Only on `speak: ai`. |
| `tool_call_id` | FieldSpec. REQUIRED on `speak: tool`, forbidden on every other speaker. |

**Rules are ordered and the first match wins.** The tool-calling variant of a speaker must precede
the plain one, or the plain rule shadows it.

**A record matched by no discard and no message rule is never guessed at.** It is left out of
the pin, counted in its provenance (`unrecognised_record_count`, `unrecognised_shapes`) and named
by shape in a `trace.adapter_unrecognised_records` finding. Accounting for every record is the
property the whole document exists to have, so coverage mode holds an adapter to it before it
ships.

**On the empty `when:`.** It is legal on the final message rule, deliberately: it says "everything
left is a human turn", which is a claim about the format rather than a silent fallback. It also
means every future unknown record becomes a silently mislabelled message. Write it only when you
believe it of the FORMAT, not of the artifact in front of you.

### ToolCallSpec

| Key | Means |
|---|---|
| `from` | REQUIRED path to the list of calls. Must hold a list. |
| `where` | Optional predicate filtering that list. |
| `name`, `args`, `id` | REQUIRED paths read from within EACH call. Each must name exactly one value per call. |

The three are extracted and handed on **as found**, not validated here: a missing name, an
unparseable `args`, or an id pairing with nothing are refused downstream by `canonical_messages`,
which already says what is wrong and what it breaks.

**There is no "the arguments are a JSON string" switch.** The shared projection parses a string
into the object the pin schema requires, so an OpenAI-shaped `function.arguments` needs no
declaration.

---

## Ordering

`order:` is tri-state, and the middle state is the one to understand:

| What the artifact holds | Verdict |
|---|---|
| the ordinal on no message | **passes** — order is the record list's own |
| the ordinal on every message, strictly increasing | passes |
| the ordinal on SOME messages | refused — a partial ordinal leaves the order ambiguous |
| every message, but duplicate or descending | refused, printing the values |
| every message, but of mixed types | refused, naming the types |

The "declared but resolved nowhere" case is reported rather than judged: `ordinal_resolved_count`
is stamped into every pin, so `0 of 7` is readable afterwards. It is not an error, because whether
a corpus resolves it depends on which files one invocation was pointed at, and a verdict that
turns on how the command was typed is not a claim about the format.

---

## Merging one reply's records

Some logs write each content block of ONE model reply as its own record, every record carrying
the reply's id — a Claude Code session log does, under `message.id`. A reply that speaks and then
calls two tools in parallel is then three agent records, followed by the two results. Projected one
record to one message, that is `ai, ai, ai, tool, tool`: the first call's result does not follow
its call, and the seed-shape rule `tool_results_adjacent` refuses the thread at the write boundary
(a provider refuses the same history). Declare the reply id and the records become the reply they
were:

```yaml
merge: {by: message.id}
```

| What the executor does | Why |
|---|---|
| Merges CONSECUTIVE `ai` messages whose `by` value is equal and non-null | Only adjacent blocks of one reply are one message. |
| A discarded record between them (a reasoning-only block) does not split the reply | A discard produces no message, so the blocks stay adjacent. |
| An UNRECOGNISED record between them ends the reply | It may have been a turn; nothing is merged across it. |
| `tool` and `human` messages never merge, and a result between two blocks ends the reply | Merging across a result would reorder the conversation. |
| Id and ordinal are the first record's; tool calls are concatenated in record order | Lossless: every call keeps its own id. |
| Content: the one string that said something; `''` when only calls were made; otherwise every record's blocks in order | Never joined — a list of blocks when several records spoke. |
| A `by` path that selects two values, or an object, is refused | A record belongs to one reply or none. |

`merged_record_count` is stamped on every pin whose adapter declares `merge` (0 included). A reply
whose blocks the log INTERLEAVES with results (`A A R A R R`) cannot be rejoined by merging adjacent
records; the seed-shape rule still refuses it, by name, at the write boundary — that is the rule
working, not a mapping to loosen.

---

## What has no expression here, by design

- Reconstructing a conversation from a parent/child span tree. A named non-goal for the
  mapping language: OpenInference spans — recognised by the per-span `openinference.span.kind`
  attribute, whether they arrive in a TRAIL/Patronus `{trace_id, spans}` envelope or as a bare
  JSON list — go to the SDK's built-in `--source-format openinference` reader; every other span
  convention (Langfuse, OpenTelemetry `gen_ai.*`, OpenLLMetry) is refused by name, never mapped.
- Joining two files, or reading anything outside the one artifact.
- Computing, formatting, concatenating, or conditionally defaulting a value beyond `missing:`.
- Any escape to code. There is no `eval`, no expression field, and no plugin hook.
