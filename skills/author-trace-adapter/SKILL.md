---
name: author-trace-adapter
description: >-
  Author a declarative YAML adapter for a trace format the SDK converter does not know (session logs, custom exporter JSON, raw log lines). Saved under plurai_sim/plurai_adapters/. Run before /trace-to-scenarios for traces that are not LangSmith or LangGraph thread exports.
---

# Author a Trace Adapter

You are the LOCAL Claude Code agent. The customer has real production conversations on
disk in a format the SDK converter does not know, and wants them turned into resumed
scenarios. `/trace-to-scenarios` reads exactly one format — a LangSmith thread export
from a LangGraph agent. This skill covers everything else.

Your job is **diagnosis and authorship**, not conversion. You work out what the artifact's
records mean, write a declarative mapping that says so, prove the mapping accounts for
every record, and persist it. The SDK's executor then does the conversion, deterministically,
now and on every later run.

The split that matters:

```
┌── customer's machine (you) ─────────────────────────────────────────┐
│  DIAGNOSE → AUTHOR → CONVERGE → PERSIST. No trace bytes leave.      │
│  1. Triage the FORMAT before opening anything. Some are already     │
│       first-class (LangSmith threads, OpenInference span trees);    │
│       other span trees are refusals the executor can never run,     │
│       and saying so early is the service.                           │
│  2. If plurai_adapters/<name>.yaml already exists: STOP AUTHORING.  │
│       Re-run it. Re-authoring is a deliberate act, never a          │
│       per-run event — the committed file IS the format knowledge.   │
│  3. Shell: survey_artifact.py <artifact>                            │
│       → record SHAPES, matchable literals, order:/id: candidates.   │
│       You never open the artifact yourself.                         │
│  4. Author the MAPPING (YAML). You write paths and matchers.        │
│       You never write, summarise, quote or retype message content.  │
│  5. Shell: survey_artifact.py <artifact> --adapter <draft>          │
│       → EVERY gap at once. Fix. Repeat until it says CLEAN.         │
│  6. Persist to plurai_sim/plurai_adapters/<name>.yaml and have the  │
│       customer REVIEW it. It is committed like plurai.config.yaml.  │
│  7. Shell: plurai convert-trace --source-format <name> …            │
│  8. Hand off to /trace-to-scenarios for ingest and generation.      │
└─────────────────────────────────────────────────────────────────────┘
                                   │  the YAML, and only the YAML
┌── deterministic SDK executor (plurai.trace_adapter) ────────────────┐
│  Loads the adapter with yaml.safe_load into closed pydantic models  │
│  (extra='forbid'). No exec, no eval, no code in an adapter: a path  │
│  language that resolves keys, indices and `*`, and cannot compute.  │
│  It owns every byte. Message content is COPIED from artifact to     │
│  pin — never regenerated, never round-tripped through a model.      │
│  A record no rule accounts for is left out of the pin, COUNTED      │
│  by shape in provenance and named in a finding — never guessed at.  │
│  No closest fit, no catch-all speaker, no silent drop.              │
└─────────────────────────────────────────────────────────────────────┘
```

**Why the model authors a mapping instead of the messages.** Understanding a novel log
format is a judgment task, which is what a model is for. Copying a customer's production
transcript verbatim into a resume pin is a fidelity task, which is what a model is worst
at — a paraphrase, a dropped tool call, or a helpfully-normalised role would corrupt the
one thing this lane exists to preserve. So the division is absolute: you decide what the
fields MEAN, and fixed code moves the bytes.

**Why the adapter is persisted and committed.** A format is a standing fact about the
customer's stack, not a per-conversion discovery. Once the adapter is on disk and reviewed,
the second conversion and the hundredth run the same fixed code over the same reviewed
mapping, with no model anywhere in the path — which is the only way the output is
reproducible. Treat an existing adapter as authoritative: re-authoring one is a deliberate
act the customer asks for, taken when the SOURCE FORMAT changed.

<!-- BEGIN backend-check: generated from ${CLAUDE_PLUGIN_ROOT}/docs/backend-check-block.md; edit there -->
## Check the backend first

Before anything else in this skill, run the backend check. It needs no backend tool and no SDK, and it comes before the
tool-list step of the error posture:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"
```

- Exit 0: go on. A line it printed is an update notice: say it once, as printed, first; it stops nothing.
- Exit 7 is a version refusal: follow the error posture's version refusal rule. Say what the check printed, verbatim and
  alone, with nothing before it: not the exit code, not that it is a refusal. Then stop. No tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line (no usable login; this skill has no readiness step, so the check is its login
  test): run the `harness-setup` skill from the command the user typed (`from /<command>`) and show the user nothing from
  the line. That skill checks the backend after its login, so go on when it reports Plurai set up, and stop when it stops.
  Any other line, such as a `NOT READY` line for hooks Codex has not trusted: tell the user in plain words what it says
  to fix, and stop.
<!-- END backend-check -->

## When this applies — and when it does NOT

**Use this** when the customer has conversation traces on disk that `/trace-to-scenarios`
cannot read: a Claude Code session log (`~/.claude/projects/**/*.jsonl`), a benchmark
episode, an in-house exporter's JSON, a platform export from something other than
LangSmith — anything whose records are a flat list you can point at.

Four things you MUST tell the user up front:

- **This is a format-teaching session, and it happens once.** The output is a reviewed file
  in their repo. It costs a model session now and zero model involvement afterwards. If they
  expected a one-shot conversion, this framing is the difference between "why is it asking
  me to review YAML" and "ah, this is the format definition".
- **You will not read their conversations, and neither will the backend.** You read a
  structural survey — key names, types, and short repeated labels. The survey withholds
  anything that looks like prose. The artifact never leaves the machine.
- **Refusal is a real outcome here.** Some formats cannot be expressed in this mapping
  language at all (see below), and some records inside a readable format are genuinely
  ambiguous. Both end in a diagnosis rather than a best guess, because a plausible-looking
  wrong mapping produces a corrupt pin that reads as an agent defect two steps later.
- **Resumed scenarios still need a resumed-aware `simulate.py`.** Authoring an adapter does
  not change that gate. `/trace-to-scenarios` owns it — see **Resume fidelity** below.

**Do NOT use this** when:

- **The artifact is a LangSmith thread export from a LangGraph agent.** That is first-class.
  Send them to `/trace-to-scenarios`. Authoring an adapter for it would be strictly worse:
  the built-in reader is the one already trusted in production.
- **The artifact is a nested-span export** — anything where the conversation must be
  reconstructed by walking a parent/child span tree. Never an adapter: the path language
  resolves, it does not traverse or reassemble, and stretching it to reach span trees would
  give it exactly the computing power the design withholds. Two cases, told apart by the span
  attributes:
  - **OpenInference spans** (`openinference.span.kind`, `llm.input_messages.*`, `tool.name`) in
    a TRAIL/Patronus `{trace_id, spans}` envelope with nested `child_spans`, or as a bare JSON
    list of those same spans — what an agent instrumented with `openinference-instrumentation-*`
    (smolagents and kin) produces. **The identifying mark is the per-span
    `openinference.span.kind` attribute, not the envelope**, so a document that carries the
    spans in either container is the same format to the reader. **This is
    first-class.** The SDK ships a verified reader: `plurai convert-trace --source-format
    openinference …`, then `/trace-to-scenarios`. It walks the tree in start-instant order and
    refuses by name what it cannot carry — a CodeAgent trace whose tools answer no model tool
    call, a history older than the capture, a side graph, a partly stamped tree. A
    ToolCallingAgent closes each turn with a `final_answer` call whose answer the reader folds
    into the agent's closing message, so such a capture ends on a completed agent turn.
  - **Any other span convention** — Langfuse trace+observation exports, OpenTelemetry GenAI
    semantic conventions (`gen_ai.*`), OpenLLMetry (`llm.request.*`, `traceloop.*`) — **is a
    recognized refusal, not a hard authoring problem.** Reading those trees needs a reader
    verified against a real capture, and none exists. Diagnose it, say so plainly, and stop.
    Do not start authoring against a format the executor can never run.
  *Sharp edge:* the Plurai `ingest-traces` Langfuse extractor emits a span tree, so
  "extraction worked" does not imply "conversion can follow".
- **The user wants trace ANALYSIS** — behavioural patterns, common failures, a grounding
  brief. That is `/analyze-traces`, a different lane that does not touch this one.
- **The records are not a list.** This language reads a flat sequence of records. If the
  conversation is a graph, a diff stream, or a set of files that must be joined, it needs a
  first-class reader. For an OpenInference span tree that reader exists — route it as above.
  For anything else in this shape, the reader is an SDK change and a different ticket.

**The LangSmith trap.** A LangSmith export from a NON-LangGraph agent is a real case and it
lands here, not in `/trace-to-scenarios`. The built-in reader requires a root run's
`outputs` to be a state-channel dict holding the conversation. When the one message list sits in
a channel other than `messages` it reads that channel and says so in a
`trace.messages_channel_inferred` finding; with no message channel, or two, it skips the
thread. No adapter named `langsmith` can rescue such an export — **a built-in always wins the name lookup, so
an adapter called `langsmith` is silently dead code.** Name it for the agent or the exporter
(`acme_support_export`), never for the platform.

## Preconditions

1. **`/simulation-scan-agent` has run** — `plurai_sim/plurai.config.yaml` exists, so there is
   a workspace root to persist into.
2. **`plurai` SDK installed in the client's venv, at a version that HAS the adapter layer.**
   Step 0 checks this. Install it as
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints.
3. **The artifact is locally readable**, and you have its path. One file, or a directory of
   files that share one format.
4. **The records are a flat list** — the document root, or one path that names a list. A
   nested-span export fails this and is not a candidate for authoring: an OpenInference tree
   goes to `--source-format openinference`, every other span convention is a named refusal.
5. **This env's anonymization posture is raw** (`anonymize:` absent or `false`). The converter
   refuses under `anonymized` posture and this skill has no override, exactly as
   `/trace-to-scenarios` does not.

## The flow

### Step 0 — Preflight the SDK

The survey script imports `plurai.trace_adapter`, so it must run under the SAME interpreter that
provides the `plurai` CLI — the customer's project venv, not a bare system `python3`. Establish
that once, here, and use `python` for every script call below:

```bash
python -c "import plurai.trace_adapter; print('adapter layer present')"
python -c "from plurai.trace_convert import TRACE_FORMAT_REGISTRY as r; print(sorted(r))"
```

If the import fails, either the venv is not active (activate it, or call it by path as
`.venv/bin/python`, or `.venv/Scripts/python.exe` on Windows) or the installed SDK predates the adapter executor. For the second, say the
SDK needs updating and give the install line from precondition 2 — do not attempt to author
against a missing executor, and do not reflect the traceback.

The second line prints the built-in readers, and **`openinference` must be among them.** The
adapter layer is older than the span-tree reader, so an SDK that imports fine can still lack it,
and Step 1 routes a tree to a `--source-format` this build cannot resolve. Worse, the refusal
that follows names the very file hard rule 7 forbids
(`plurai_sim/plurai_adapters/openinference.yaml`), so the diagnosis reads as an instruction to
author. If the name is missing, say the SDK needs upgrading, give the install line from
precondition 2, and stop — never author an adapter for a span tree.

### Step 1 — Triage the format before opening anything

Ask what produced the artifact, and check the extension and size. Decide, in this order:

1. **First-class already?** LangSmith thread export from a LangGraph agent → `/trace-to-scenarios`.
2. **Nested spans?** OpenInference/TRAIL → `--source-format openinference`, then
   `/trace-to-scenarios`. Any other span convention → recognized refusal. Say so and stop (see above).
3. **Otherwise** → adapter. Pick a format name now: lowercase, underscore-separated, naming
   the SOURCE (`claude_code`, `tracebench`, `acme_support_export`). It becomes the filename
   stem, the `format:` key, and the `--source-format` argument, and all three must agree.

### Step 2 — Check for an existing adapter FIRST

```bash
python -c "from plurai.trace_adapter import available_adapters; print(available_adapters(workspace_root='.'))"
```

If this format already has an adapter, **you are done authoring.** Skip to Step 5 to confirm
it still covers this artifact, then Step 6. This is the ordinary case after the first session,
and taking it is what makes later conversions model-free. Re-author only when the customer
says the source format itself changed — and then as an edit they review, not a silent rewrite.

### Step 3 — Survey the artifact

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/author-trace-adapter/scripts/survey_artifact.py" <artifact>
```

Add `--container jsonl` for line-delimited files if the extension does not already say so, and
`--records <path>` when the records are a list nested inside a JSON document (`messages`,
`data.events`).

The report gives you, per distinct record SHAPE: the key skeleton, each path's types, the
short repeated values that can serve as `when:` matchers, and nominated `order:` / `id:`
candidates with how often each resolves. **Read the survey, not the artifact.** Values that
look like prose are named by path and withheld by value; that is deliberate, and a withheld
value is never a reason to go open the file yourself.

### Step 4 — Author the mapping

Write the YAML from the survey. The full field reference is
`references/adapter-language.md`; a worked example is `references/worked-example.md`.

The judgment rules, which are the actual content of this step:

- **Every record gets a fate: a message rule, or an explicit `discard:` with a `because:`.**
  A shape you cannot classify is a question for the user, not a record to quietly drop.
- **`speak:` is one of `human`, `ai`, `tool`, `system`, and closest-fit is BANNED.** If a
  source has a fifth speaker kind, that is a refusal with a diagnosis. This inverts the older
  trace-analysis guidance that said to choose the nearest of the four: that advice served
  summarisation, where an approximate role costs a little accuracy. Here the transcript is
  replayed into a real agent, and a mislabelled speaker silently rewrites who said what.
- **An absent field is often the correct answer, not a gap to fill.** If the source records no
  ids, omit `id:` and let the executor mint stable uuid5 ids, which it counts in provenance. If
  it records no ordering field, omit `order:` and let record order stand.
- **Never default a missing value to a plausible one.** No zeroes for absent cost or usage, no
  invented timestamps. An absent value means honestly unmeasured; a fabricated one corrupts
  every roll-up downstream and cannot be told from a real measurement afterwards.
- **`order:` is tri-state and must not be partial.** Declare it when it resolves on every
  projected message and strictly increases, or omit it. A field present on some messages and
  not others is the ambiguous case, and it refuses.
- **Rules are first-match-wins, so they go narrowest first.** The tool-calling variant of a
  speaker must precede the plain one, or the plain rule shadows it.

### Step 5 — Converge

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/author-trace-adapter/scripts/survey_artifact.py" <artifact> --adapter <draft.yaml>
```

With `--adapter` the script also imports `plurai`, so `$PLURAI_PY` has to be the client venv's
python; if the resolver picks another yaml-capable interpreter, point `PLURAI_PYTHON_BIN` at the
venv's python before sourcing.

This runs the executor's own matching and extraction over every record and collects every
gap instead of stopping at one. `plurai convert-trace` drops an unrecognised record — one no
rule accounts for — out of the conversation, counts it in the pin's provenance
(`unrecognised_record_count`, `unrecognised_shapes`) and reports a
`trace.adapter_unrecognised_records` finding, so format drift does not kill a conversion; but a
turn among those records is then missing from the pin. And it refuses a thread on the first
field that does not resolve, so a twelve-shape artifact would cost twelve conversions. Here the
gaps arrive together: unaccounted shapes (with the matcher literals you need), fields that do
not resolve, rules that never matched, and any ordering violation.

Fix and re-run until it prints **CLEAN**. Exit code 0 means clean, 1 means gaps, 2 means the
artifact or environment could not be read.

Two readings need care:

- **"Rules that never matched"** has two very different causes. An earlier broader rule
  shadowing a later one is a real defect. This artifact simply not containing that record kind
  is ordinary in a heterogeneous corpus and is no reason to delete a rule. Check against a
  second artifact before changing anything.
- **A CLEAN run proves coverage of THIS artifact.** If the customer has a corpus, run the
  coverage pass over several files — especially ones of different sizes or dates.

### Step 6 — Persist and have it reviewed

Write the adapter to `plurai_sim/plurai_adapters/<format>.yaml`, beside `plurai.config.yaml`.
The filename stem must equal the `format:` key. Never write two copies under one workspace
root — the loader fails closed when it finds the same format twice, and it is right to.

Then **ask the customer to review it**, in product terms: this file is now their format
definition, it will be reused verbatim on every conversion, and it belongs in version control
next to their config. Keep the comments you wrote in it; the `because:` on each discard is
what makes the review possible at all.

### Step 7 — Convert

```bash
plurai convert-trace --export <file-or-dir> --source-format <format> --out-dir <dir> --workspace-root .
```

One artifact becomes one bundle folder carrying a `resume_pin.json`. The command prints a
JSON summary to stdout on every outcome and human lines to stderr. It runs entirely locally;
nothing is published until the ingest step, which this skill does not perform.

Read the summary, never the stderr lines. `skipped` names each artifact that was NOT written,
with the `gate_id` that kept it out, and `gate_findings` carries every finding — those skips and
the advisories on the folders that were written. Each artifact converts or is skipped on its
own; exit 2 means nothing was written. A skip or a `trace.adapter_unrecognised_records` finding
on this adapter's first conversion is a gap Step 5 missed: re-run coverage over that artifact,
fix the mapping, and convert again. Findings are yours to act on
(`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`); the coverage report is what the customer reviews.

Read the summary's provenance counts back to yourself as a sanity check: a minted-id count
equal to the message count means the source recorded no ids (expected for some formats,
surprising for others), and a projected count far below the record count means the discards
are broader than intended.

### Step 8 — Hand off

Send the user to `/trace-to-scenarios` with **the original export path and the adapter's
`format:` name** — not the `--out-dir` from Step 7. That skill has no already-converted entry:
it converts the export itself, into its own staging dir, and every one of its `convert-trace`
calls takes `--source-format`. Without the name it runs the default LangSmith reader over an
in-house export and refuses it as malformed — a refusal naming shapes the customer has never
heard of, right after the adapter they just authored worked. Step 7's output is a local check
that the adapter converts; the lane re-creates it where it needs it.

That skill owns the `seed_state` gate, `plurai ingest-bundle`, the upload, generation, and the
resumed-ness verification. Do not re-implement any of it here.

### Step Verify

Before you claim this worked:

1. `available_adapters(workspace_root='.')` lists the new format.
2. The coverage pass prints CLEAN, exit 0.
3. `plurai convert-trace` produced one bundle folder per artifact, each containing
   `resume_pin.json`, and its summary's `skipped` is empty and carries no
   `trace.adapter_unrecognised_records` finding.
4. **The model-free replay:** re-run Step 7 on the same input in a fresh shell without
   re-authoring anything. Identical output, no model involved. That is the property the
   persisted adapter exists to give, and it is worth actually checking once.

## Resume fidelity — what to tell the user, and what NOT to promise

**There is no `agent.resume` capability key, and nothing here reads one.** The converter
ALWAYS writes the resume pin, and whether an agent can actually be resumed is enforced at the
RUNTIME seam by the customer's own `simulate.py`, which raises if it is handed a seed it cannot
apply. If you find guidance describing a capability key or a pinless downgrade path, it is
stale — the SDK has no such flow, and inventing one here would mean fabricating a feature.

Three things to say plainly, because each one is currently easy to get wrong:

- **An adapter-tier pin carries `agent_state: {}` unless the source really holds a state
  checkpoint.** That is the ordinary answer for any source that is a conversation rather than
  a graph checkpoint, not a degraded one — but it means the resume is messages-only.
- **A messages-only resume has to SAY so, and the harness is the only party that can.**
  `seed_state`'s return value is the fidelity report: the SDK carries it into the run record
  untouched, and `None` there means a full-fidelity resume. A harness that restores the
  transcript and nothing else returns `{'fidelity': 'reduced', 'restored': ['messages'], 'not_restored': ['agent_state']}` instead. The skill-lane templates do;
  a hand-written harness that resumes at less than full fidelity must too, or its runs are
  scored as though the agent resumed from state it never saw.
- **The Claude-Agent-SDK skill lane resumes at messages fidelity — and only that.** Its
  `seed_state` constructs the session, and the templates fold the pinned transcript into the
  session's opening context at construction; a seed with a non-empty `agent_state`
  is refused loudly, keys named — the nested agent has no state outside its own
  conversation. Say that accurately.

<!-- BEGIN error-posture: generated from ${CLAUDE_PLUGIN_ROOT}/docs/error-posture.md; edit there -->
## Error posture — findings are evidence, internal issues are yours to handle

Backend errors, warnings, diagnostics and gate findings are internal: never show the customer
raw backend output. Read the raw error or finding yourself, always.

**Gate findings are evidence; you decide.** On `gate_findings`, a refusal naming a `gate_id`, or an
overrule outcome of `stale` or `refused` on `gate_authority.overrules`, follow
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (read it the first time one appears). Never ask the
user about a finding or an overrule; never put either in a report.

**Any Plurai tool error.** On an error from a Plurai tool or a `plurai` command, run
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"` once, before anything else, adding `--close <flow>` when this
command's Step 0 armed a flow window. On exit 7, 8 or 3 it explains
the error: say what it printed as the rule for that exit code does (exit 7 the version refusal below, exit 8 the credit
refusal, exit 3 item 6), and never paraphrase the tool error. On exit 0 it does not explain the error: handle the error
with the items below.

**Version refusal.** The software is refused, not the project; nothing here is yours to fix. On exit 7 of
`python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py"`, or of any plugin script that reaches Plurai (`sdk_call.py`,
`poll_job.sh`, `run_wait.sh`, `mcp_call.py`), what it printed is the refusal: stop, and say those lines verbatim and alone,
nothing before them, not even `exit 7`. Their last line is the update command, or the contact hand-off: add nothing after it. Close the flow window
this command's Step 0 armed first and silently: the check run with `--close <flow>` closed it in its own call; after
any other script run the `flow-window.sh close` line of its `NOT READY` stop. Never retry; never run `harness-setup`, ask the user to log in, reconnect or give a key. A later request in this
session gets the same lines: run the check again and say what it prints. Items 1, 2, 3 and 6 below do not apply.

**Credit refusal.** Neither a fault to fix nor a provider refusal. On a line `CREDIT-STOP: <sentence>`, a `plurai`
command that exits 13 on a credit sentence, or a credit block in a tool result or job envelope
(`terminal_reason: credit`, or `code` one of `insufficient-credit`, `new-run-refused`, `free-tier-ceiling`,
`credit-unavailable`, `credit-refused`): stop, whatever the snippet's own fallback says, and say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" refusal (--sentence "<the sentence>" | --refusal <the block's JSON>)
--continue "<the user's command>" --done "<outcome>"` prints, verbatim and alone (with `--refusal`, add
`--plurai "$PLURAI_BIN"` when you hold it). `<the user's command>` is the slash command the user typed to start this
flow (for example `/simulate`), or their request in words; never this skill's name. Exit 5 means it names a skill the
user cannot type: run again with the user's command. For `credit-unavailable` the command prints the contact hand-off with
no continue line: say it. The run stays halted until the user's next message lifts it, as for capacity.

Read `${CLAUDE_PLUGIN_ROOT}/docs/credit-refusal.md` the first time one appears, and when a tool reports a payment
refusal with no block. Say the stop once, then only what is new. When the stop has a continue line, name no other
command or skill, then or later: that line is the one way on. Never retry the refusal; never run
`harness-setup` or ask the user to log in again; never ask for a key; never name or suggest a model; never start
another job. Items 1, 2, 3 and 6 below do not apply.

**SDK install question.** When `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask` exits 3 and its stderr holds
`resolve_plurai: REFUSED` with `header:`, `question:` and `options:` lines, the SDK here is older than this plugin release
needs, or this project's environment holds none: ask the question once, exactly as printed, through the blocker gate. That call
sanctioned it, so the ask tool is your next action, with no text before it; if the lines say to run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` first, run it. On `Install it`, run the
`--install-pin` command the lines name, unchanged, tell the user its last line, then run the command that stopped again from
its start. On `Not now`, or when nobody can answer, close the window as the version refusal rule does, then say the stop
lines that follow the question, verbatim and alone, and run nothing after them. Stop lines with no
question are said the same way. Never run `harness-setup` for it, and never offer another install.

On any other backend error or warning:

1. **Fix it yourself, silently.** Retry a transient failure (retry rule below). When the error names something
   this side can change (a malformed parameter, a stale or missing local artifact, an input the skill's error table
   names, the corpus's shape), change it and re-run the call once with the changed inputs: that fixed re-run is item 1,
   not a retry. At most one fixed re-run per failed call, never a third run. Say no sentence about the fix, the re-run,
   the wait or the re-call, before or after it: the next text the user reads is the flow's normal output. A fixed re-run
   that fails on something the user must supply is item 2; on anything else, item 3.
2. **User-actionable input problem:** say what to change in product terms ("the PRD has no
   refund-policy section"), never the backend's error text, exception or internal field names.
   If the missing input blocks the flow (a provider key unset or expired, credentials, a service
   that stopped answering), ask; never degrade silently. Sanction the ask first with
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction <gate>` (never a raw write to a
   flow marker), ask once, at discovery, in product language, offer this skill's fallback, and
   resume on the answer. A sanction allows one ask call, within 10 minutes: put every question the
   gate needs into it, and never ask twice. Where you cannot ask the user at all, run
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" unasked <gate>` and never ask in prose.
   Where the ask is a plain-text question (no ask tool), ask one question with its options and end
   the turn on it: the next prompt is the reply. Where `request_user_input_async` is the ask tool,
   send exactly `{"questions":[{"title":"<the question>","options":["<option>","<option>"]}]}`
   and no other key (a call with an unknown key is rejected, and the sanction survives it). Never
   call `request_user_input`. A run with nobody at the terminal declares its run driver first
   (`workspace.run_driver: headless`) so the gate takes its default; an undeclared run ends on the
   question and waits for nobody.
   With `workspace.run_driver: headless`, sanction and ask the same way: the guard replies with the
   gate's default or files a `run_driver_input_required` finding for you to report. Never guess.
3. **Unfixable from here** (a misconfigured backend, a failure nothing on this side can change, a fixed re-run that
   failed again, a missing server-side capability, a `security` finding, an overrule outcome of `refused`): when the
   remedy is a grant or credential only the user holds, ask as in item 2. Otherwise stop on the contact hand-off: say
   what `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints (each `--done` an
   outcome that names what exists, with its id), verbatim and alone. The contact hand-off is for typed faults; a
   transport failure with no envelope ends on the unavailable stop (retry rule). A refusal typed `code: capacity_exhausted`, a job envelope whose
   `capacity` is set (any status), an `unavailable` block or a job envelope with `terminal_reason: unavailable` instead
   takes `stop_message.py refusal --refusal <its JSON>`; never quote the provider's own limit text. A typed block wins
   over the fault rule and over `terminal_reason`: a failed job with `terminal_reason: internal_error` that holds an
   `unavailable` block is the unavailable stop, never the contact hand-off.

4. **Cost figures and backend internals stay internal.** `total_cost_usd` and every backend spend
   figure stay out of everything the customer sees, the final message included. So do the
   backend's internal ports (a mock service's loopback port), process ids and filesystem paths;
   a deployed mock's only address is its public `https://<host>/mock/<env_id>/` URL.
5. **Internal quality-loop work is process, not product.** Defects found and fixed during
   generation or qualification stay out of the report.
6. **The endpoint is the configured backend.** The backend is the one the user logged in to with the
   `harness-setup` skill; a project `.env` file is not read. If no backend tool is in your tool list, or a
   call is refused because Plurai is not logged in or not set up, run the `harness-setup` skill from the
   command the user typed (`from /<command>`), then go on from the step you were at. Never show the
   refusal's text. Never tell the user to set, export or unset an environment variable: that is for developers
   of the plugin, never for its users. Never switch to another address to get unblocked, and never send the
   token anywhere but the configured backend.

**Before a stop, say nothing about it.** Say nothing about the error or its cause before a stop (no "credit",
"capacity", "503" or "error" wording, no apology, no status line): the stop text alone says it. Its "Done so far" lines
name what exists, with its id (for example "Scenario set saved: <name>"), never the stop or the failure.

**A stop inside a /simulate run publishes the page once.** After a stop above (the credit refusal's line, the contact
hand-off, the capacity stop or the unavailable stop), run its `stop_message.py` command and say nothing yet. Then run the
end publish, silently:
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh" && source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh" &&
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step end --flowdir "$FLOWDIR"`. When
`publish` is true, publish the file at `report` with the Artifact tool, passing `report` as `file_path` character for
character, with `icon: "report"` and `url` set to `page` when it is not null (never `force`, never `Read` the file),
then run the same command with `done "$WORKDIR" --step end --flowdir "$FLOWDIR" --url "<the URL, or - when the publish
failed>"` in place of `fill …`. When `publish` is false, never fill again and never run `done`. Your final message, the
only one this stop has, is what the stop command printed, verbatim, then the link line `done` printed, or the `say`
lines of a fill that did not publish, if any. Say the stop nowhere else in the turn. Never run `flow-window.sh simulate
end` for the stop itself: the run is stopped, and no job starts until the user's next message. Outside a /simulate run the fill skips and adds
nothing. A typed message after a capacity or unavailable stop lifts it. A go-on continues the stage at once, without
repeating the stop text and never waiting out the time it names; a stop runs `flow-window.sh simulate end`; a question is
answered, then wait for the user.

**Retry rule — only a transient failure is retried, and only once.** Read the failed job's
`terminal_reason` first (Job envelope, beside `status` and `error`). Transient is exactly one of:
- A transport failure with no envelope (connection reset, client timeout, a reply cut in
  transit, a bare `502` / `503` / `504`). Wait first: run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 45 --quiet` as
  one foreground shell call, silently, and never narrate it. Then re-call a read (`job_status`, a fetch) or a `*_start`
  once, with the same arguments: the backend answers a repeated start with the first call's job or session. A refusal naming an already-open session or job from that
  call means the first call ran: continue with it. Never re-call any other call that writes (`interact_edit_session`,
  `end_edit_session`, `score_run`, `diagnose_mock`, `edit_scenario_gt`, …). A re-call that fails again, or a transport
  failure on a write that is never re-called, is the unavailable stop: say what
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" unavailable --done "<outcome>"` prints, verbatim and alone,
  never the contact hand-off. A `poll_job.sh` that exits 69 is this transport failure: run the wait above, re-invoke it
  once, and a second 69 is the same stop.
- `terminal_reason: interrupted` from a restart (not a cancel you or the user asked for), or
  `terminal_reason: watchdog` (the backend stopped a job that made no progress).
- A failure this skill's error table marks as retried once.

Everything else is final, whatever its `terminal_reason` (`refused_inputs`,
`usage_window_exhausted`, `capacity`, `unavailable`, `credit`, `cost_cap`, `internal_error`, or none),
including a provider timeout or 5xx (`504 Provider timed out`): the backend already retries those
inside the job. Final is about re-sending the same inputs; item 1's fixed re-run, with changed inputs, is not a retry.
A retried job that fails again is final, whatever its reason. Hand it off per step 3.

**Provider refusal.** `terminal_reason: refused_inputs` is an input gate (this skill's error table
says what to fix) or the provider refusing the backend's own call. Decide by the error's shape,
never by words anywhere in `error` (a flow named `credit_limit` is not a billing problem). It is a
provider refusal only when `error` contains `SDK turn failed (HTTP 4xx)` (at its start or wrapped,
as in `corpus build failed (RuntimeError: SDK turn failed (HTTP 403): …)`) with status 400 / 401 /
403 / 404 and provider text after the marker about access, permission, the model, the
account, credit or billing. Then stop on the contact text alone, with no lead-in: say what
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stop_message.py" contact --done "<outcome>"` prints, verbatim. No model name,
no retry, and never pass or suggest a `model`: the model is a backend decision. Provider text opening with
`AWS authentication failed · refresh your AWS credentials` is about the backend's credentials:
never repeat it and never ask the user to refresh anything.
<!-- END error-posture -->

Two scoping notes for this skill:

**The converter's `anonymized` refusal is the one exception to "never relay raw output"** — it is
a consent statement, not a diagnostic, and paraphrasing it loses the reason. Relay it verbatim.

**The survey and coverage reports are not backend errors, and you SHOULD show them.** They are
this skill's working surface: the customer is being asked to review and commit the adapter, so
the evidence it is correct — which shapes exist, which rule claimed each one, what is still
unaccounted — is product, not internal detail. Item 1 above still governs everything else: a
traceback from the SDK, a YAML parse error in your own draft, a wrong `--container` guess you
then corrected are all yours to fix silently.

## Hard rules

1. **You never open the trace artifact.** The survey is your only view of it. If the survey
   withheld a value, that is the design working, not an obstacle to route around.
2. **You author the mapping; you never author message content.** No summarising, no
   normalising, no retyping a transcript into YAML. The executor copies bytes.
3. **No closest-fit role mapping.** An unmappable speaker is a refusal with a diagnosis.
4. **A catch-all last rule is legal, and is a claim you must actually believe.** An empty `when:`
   on the FINAL message rule is permitted on purpose — it says "everything left is a human turn",
   which is a statement about the format. Write it only when that is true of the format rather
   than of this artifact, because it also converts every future unknown record into a silently
   mislabelled message. Default to enumerating shapes; a discard with a `because:` is almost
   always the better tool. (An empty `when:` on a `discard:` is refused outright — it would drop
   the whole artifact and report success.)
5. **No fabricated values.** Absent stays absent. No defaulted zeroes, no invented ids where
   the source has real ones, no synthesized timestamps.
6. **An existing adapter is authoritative.** Re-author only on an explicit request, and only
   because the source format changed.
7. **Nested-span formats are never authored as adapters.** An OpenInference tree goes to the
   SDK's built-in reader; every other span convention is a named refusal.
8. **Ship nothing on a non-CLEAN coverage run.** A conversion that "mostly works" produces
   corrupt pins whose damage surfaces two steps downstream, wearing the costume of an agent
   defect.

## Failure modes + recovery

| What you see | What it means | What to do |
|---|---|---|
| `import plurai.trace_adapter` fails | SDK predates the adapter executor | Give the install line; do not author against a missing executor |
| Survey exits 2, "not parseable JSON" | Wrong container guess | Pass `--container jsonl` (or `json`); if it still fails, the file is not JSON at all |
| Survey exits 2, "the records are dict, not a list" | Records are nested | Find the list in the document and pass `--records <path>` |
| Coverage: UNACCOUNTED shapes | No rule matches those records | Use the shape's printed literals to write a rule — or an explicit `discard:` with a `because:` |
| Coverage: field does not resolve | Rule matched, then the path was wrong or absent | Fix the path, or declare `missing:` if the source legitimately omits it |
| Coverage: rule never matched | Shadowed by an earlier rule, OR absent from this artifact | Check rule order first; confirm against a second artifact before deleting |
| Coverage: ordering violation | `order:` is partial or not strictly increasing | Omit `order:` — record order is usually what the source already means |
| Adapter loads but `format:` mismatch | Stem and `format:` key disagree | Rename one so they match; the loader requires it |
| `available_adapters` finds two copies | Same format under two adapter dirs | Delete one; the loader fails closed and will not pick |
| Convert refuses under `anonymized` | Posture gate, no override here | Relay the refusal; do not work around it |
| Convert succeeds, minted ids == message count | Source records no ids | Expected for some formats; confirm it is true of this one rather than a wrong `id:` path |
| Convert writes the folder, and its summary carries `trace.adapter_unrecognised_records` | Records no rule accounts for were left out of the pin (format drift, or a gap coverage was never run on) | Run Step 5 coverage over that artifact: it names their shapes. Add a message rule or a `discard:` with a `because:`, then re-convert |
| User brings an OpenInference / TRAIL span tree | First-class reader | `--source-format openinference`, then `/trace-to-scenarios`; never an adapter |
| Step 0's registry print has no `openinference` | Installed SDK predates the span-tree reader | Upgrade the SDK in the client's venv (precondition 2) and re-run Step 0. Never author the adapter its refusal names — hard rule 7 holds whatever the error says |
| `--source-format openinference` refuses the tree, naming a span and what that span lacks | The reader recognises the format and diagnoses THIS capture | The thread is skipped with an `invariant` finding: act on the cause the finding's `remedy` names — usually a capture to redo with the instrumentation that records structured messages, or a re-export — and tell the user in product terms that the capture needs redoing. Leave that export out until then. Never author an adapter to route around a reader that read the file |
| User asks for a Langfuse / GenAI-semconv / OpenLLMetry span export | Named refusal | Diagnose and stop; needs a reader verified on a real capture, not an adapter |
| A speaker kind maps to none of the four | Unmappable | Refuse with a diagnosis — never pick the nearest |

## Glossary

- **Adapter** — a YAML mapping in `plurai_sim/plurai_adapters/<format>.yaml` that tells the
  deterministic executor how to read one source format. Committed and reviewed like config.
- **Executor** — `plurai.trace_adapter`, the fixed SDK code that runs an adapter. It owns
  every byte of the conversion; the adapter only says where things are.
- **Record** — one element of the artifact's flat list: a line of a `.jsonl`, or an item of
  the list named by `records:`.
- **Shape** — a record's top-level key set. The survey groups by it because it is the identity
  the executor's own refusal message prints.
- **Projection** — the executor's per-record decision: discard it, or turn it into one
  canonical message. A record that gets neither is left out of the pin, counted in its
  provenance (`unrecognised_record_count`) and named in a `trace.adapter_unrecognised_records`
  finding.
- **First-class reader** — a format built into the SDK (today: `langsmith` for LangSmith+LangGraph
  thread exports, `openinference` for OpenInference/TRAIL span trees, and `plurai-run` for a
  finished simulation run's own transcript, which /record-regression uses). Built-ins win the name
  lookup, so an adapter cannot shadow one.
- **Resume pin** (`resume_pin.json`) — what conversion produces: the transcript and state a
  scenario resumes from, plus a `resume_point` naming the graph node the run starts at, which
  is `null` unless the operator asked for one. Distinct from the `resume_seed.yaml` that
  generation later derives.
- **Minted id** — a stable `uuid5` id the executor generates for a message whose source
  recorded none, counted in the pin's provenance so it stays distinguishable from a real one.
