---
name: trace-to-scenarios
description: >-
  Turn conversation exports into scenarios that resume where the trace ended. You never read or parse a raw trace file. `--from-brief` (chained by /analyze-traces) turns a trace brief's failures into scenarios and reads the converter's own rendering of a trace, to choose the cut.
---

# Trace to Scenarios

You are the LOCAL Claude Code agent. The customer has real production conversations
(thread exports sitting on disk) and wants scenarios that **pick up where those
conversations left off** — not scenarios *about* them. A resumed scenario hands the
agent its own real transcript and real state at turn 0, then lets the Plurai backend user-sim
drive the conversation forward from that point.

`/ground-scenarios` routes here when the customer's material is conversations to
continue; arriving directly works the same.

This skill is **pure orchestration**. It runs three things in order and checks the
results. It does **not** read, parse, summarise, or reason about trace content — that
knowledge lives entirely in the SDK converter, which is deterministic code precisely
because an LLM anywhere in the verbatim path would break the fidelity bar this whole
lane exists to hold. The brief-driven mode reads one thing more, and only to choose where
to cut: the converter's own **rendering** of a candidate trace (`transcript.md` from
`convert-trace --render-only`). Choosing a cut writes no byte of the pin.

The split that matters:

```
┌── customer's machine (you, via plurai-sdk) ─────────────────────────┐
│  CONVERT (local, deterministic) → SCAN → UPLOAD.                    │
│  1. Gate: simulate.py's seed_state must be resumed-aware            │
│       (3rd `resume_seed` param) — else every resumed scenario       │
│       runs on a BLANK thread and looks like an agent failure.       │
│  2. Locate the thread exports (you never open them).                │
│  3. Shell: plurai convert-trace --export <file> --out-dir <dir>     │
│       --source-format <name> unless the export is LangSmith's       │
│       one thread export → one BUNDLE folder, each carrying          │
│       resume_pin.json (the pin — generation turns it into the       │
│       scenario's resume_seed.yaml; they are NOT the same file).     │
│       REFUSES under `anonymized` posture — relay verbatim, stop.    │
│  4. Shell: plurai ingest-bundle --bundle-dir <dir> …                │
│       (the SAME bundle lane /upload-scenario-bundle uses)           │
│       → prints the single-archive MANIFEST to stdout                │
│  5. MCP: upload_scenario_bundle_start(manifest=…)                   │
│       → UploadFinalizeResult { set_id, status: 'done' }             │
│  6. MCP: get_upload_meta(set_id) → 'done'.                          │
│  7. Write plurai_sim/.scenario_bundle_id (no newline) +             │
│       .scenario_bundle_augmentation + .scenario_bundle_intent.      │
│  8. Hand off to /generate-scenarios, then VERIFY resumed-ness.      │
└─────────────────────────────────────────────────────────────────────┘
                                   │ MCP
┌── the Plurai backend ────────────┴──────────────────────────────────┐
│  Same bundle import as /upload-scenario-bundle — no new route.      │
│  A folder carrying a resume pin generates a RESUMED scenario:       │
│    the pin's content is merged write-site-authoritatively, and      │
│    the two "the opener starts a brand-new conversation" guards      │
│    are disarmed for that scenario. Pin PRESENCE is the sole         │
│    marker — there is no job-level resumed flag.                     │
│  A folder WITHOUT a pin is a regular bundle import, unchanged,      │
│    so mixed bundles work for free.                                  │
│  A MALFORMED pin fails THAT FOLDER CLOSED — no LLM fallback.        │
│    Sibling folders proceed. Surfaced on the job.                    │
│  The backend stays format-ignorant end to end: it validates the     │
│    envelope and passes content through byte-preserved.              │
└─────────────────────────────────────────────────────────────────────┘
```

**Why the conversion runs locally.** The trace exports are the customer's raw production
conversations — they contain whatever their real users said. Converting locally means the
transform is versioned with the client the customer already runs, is tested in SDK CI, and
keeps format knowledge out of the backend entirely. The backend never learns which platform
produced the export.

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

**Use this** when the customer has thread exports on disk and wants the agent
evaluated on **continuing** real conversations — recovering a stalled thread, handling the
follow-up after a real escalation, picking up a multi-turn task mid-flight. These are the
scenarios that are hardest to write by hand and easiest to get subtly wrong, because the
interesting state is the state the conversation had already accumulated.

Four things you MUST tell the user up front:

- **Resumed scenarios need a resumed-aware `simulate.py`.** The transcript is replayed into
  the agent's own history by the customer's `seed_state` hook — the SDK hands it over
  and never interprets it. A two-parameter `seed_state` simply never receives the seed, and
  the scenario then runs on a blank thread against an opener that references a conversation
  the agent has no memory of. It scores badly and reads like an agent defect. **This is the
  #1 failure mode of this lane and Step 1 gates on it.**
- **One thread export = one scenario.** The fan-out is `exports × (1 + augmentation_level)`.
  `augmentation_level=0` (faithful) is the right default here — the whole point is the real
  conversation. Raising it asks the generation LLM to invent variations *around* a real
  resume point, which is legitimate but is no longer verbatim.
- **The dimension-graph WALK is bypassed** (this is the bundle path). `count`, `interest`,
  and dimension diversity are inert. If the agent also needs broad flow coverage, that is a
  SEPARATE, non-bundle run.
- **Raw only, for now.** The converter refuses under `anonymized` posture and there is no
  override in this skill. See Step 3.

**Do NOT use this** to *analyse* traces for behavioural patterns, common failures, or a
grounding brief — that is `/analyze-traces`, a completely different lane that does not
touch this one. And do not use it to ground a precondition in a real *record* (not a real
*conversation*) — that is `/upload-scenario-bundle`.

**If the export is not a format the converter already knows**, this lane cannot start: the
conversion is deterministic and reads only the formats the SDK ships — the built-in readers
are LangSmith's thread export (`DEFAULT_SOURCE_FORMAT` in the SDK's `plurai/trace_convert.py`)
and OpenInference span trees (`--source-format openinference`: the spans an
OpenInference-instrumented agent emits, in a TRAIL/Patronus `{trace_id, spans}` envelope OR as
a bare JSON list of spans — the reader identifies the format by the per-span
`openinference.span.kind` attribute, not by the envelope — reconstructed from its LLM and TOOL
spans). A ToolCallingAgent closes each turn with a `final_answer` call whose answer the reader
folds into the agent's closing message, so such a capture ends on a completed agent turn and
needs no mid-turn trim. For any other format, run `/author-trace-adapter` first. It
diagnoses the artifact on the customer's own machine and writes a small mapping into their
workspace, after which this lane runs against it — with ONE difference that is on you to
carry: nothing detects a workspace adapter. Every `convert-trace` call below must name it
with `--source-format`, or the LangSmith reader gets the file and refuses it as malformed.
Come back here once that mapping is committed, carrying its `format:` name.

## Entry points

Three ways in. The default is the whole lane; the second exists so one set can hold both kinds
of material without either lane restating the other's steps; the third turns a trace-analysis
brief into trace scenarios.

- **Full lane** (the default — direct invocation, or `/ground-scenarios` routing here).
  Step 0 through Step Verify, staging into this lane's own `trace_scenario_bundle`. Every
  precondition below applies.
- **Convert-only** (`/ground-scenarios` both-lanes route). Steps 0 through 3 ONLY, staging into
  the shared dir the router names instead of `trace_scenario_bundle` — then **stop and
  return**. Do not ingest, do not call any MCP tool, do not write the markers, do not hand
  off to `/generate-scenarios`: the folders you just wrote are half of a set the record lane
  publishes, and ingesting here would publish that half alone as its own set. Preconditions
  1 (MCP reachable) does not gate this entry — it makes no MCP calls — but **every other
  precondition still does, the `seed_state` gate above all.** A resumed folder converted
  without that gate runs blank whatever is published beside it, and the lane that publishes
  it cannot check for you.
- **Brief-driven** (`--from-brief`; `/analyze-traces` chains it once its brief is published).
  B0 through B9 in *Brief-driven mode* below ONLY — none of Steps 1-7 and no Step Verify. It
  turns the brief's trace failures into trace scenarios under `plurai_sim/trace_scenarios/`,
  then **stops**: no ingest, no upload, no generation. Its `seed_state` check is a WARNING, never
  a stop — the final batch holds trace scenarios back while the harness cannot replay a seed.

Everything else in this file — the never-read-a-raw-trace rule, the `anonymized` refusal, the
per-export failure posture, the hard rules — holds identically in all three.

## Preconditions

1. **`plurai` MCP server reachable** (auto-registered via the plugin manifest;
   the backend is the configured one, the one the saved Plurai login names.
   A remote backend requires that login. Bundle tools must be enabled —
   **there is no preflight for this**, and do not invent one: `upload_scenario_bundle_start`
   raises its own `"scenario-bundle tools are disabled — no scenario_bundle_storage_root"`
   ValueError at Step 4 if they are not. That is a backend-side setting
   and unfixable from here: run the contact hand-off (`stop_message.py contact --done`).
2. **`/simulation-scan-agent` has run** — `plurai_sim/plurai.config.yaml` exists.
3. **Thread exports are locally readable** — one file per thread. You need their paths,
   nothing more.
4. **`plurai` SDK installed in the client's venv**, at a version that HAS the converter verb
   (Step 0 checks this; install it as
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints).
5. **This env's anonymization posture is declared raw** (`anonymize: false` in
   `plurai.config.yaml`): the upload refuses an absent key. If the key is ABSENT, ask once, as
   `/upload-scenario-bundle` does, and write only the answer the operator gave; if it is `true`,
   stop at Step 3 and relay the refusal. Under `run_driver: headless` there is no default for
   `anonymization-posture`: the run stops until `anonymize:` is declared.
6. **A resumed-aware `seed_state`** in `plurai_sim/simulate.py` — Step 1 gates on it and
   fixes it if missing. The brief-driven mode only warns about it (B1-B2).

## The flow

**Hard rule: never open a file under `$WORKDIR/traces/`.** Those files are raw customer data, and
this skill never reads one, in any mode. To see a trace, use `plurai convert-trace --render-only`
(the converter's own rendering), the trace-analysis brief, or `get_trace_failures`.

### Step 0: Preflight — does this SDK have the converter?

The converter is a recent SDK verb. Check before doing anything else, so a missing verb
surfaces as one clear sentence rather than a confusing shell error four steps in:

```bash
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
"$PLURAI_BIN" capabilities
#   stdout → {"version": "...", "commands": ["capabilities", "convert-trace", "ingest-bundle", ...]}
#   the command list is read off the live parser, so it can never drift from what is registered
```

`convert-trace` must be in `commands`. If it is not, the client's SDK predates this lane:
upgrade it in the client's venv (Precondition 4) and re-run. Do not attempt the flow with a
verb that isn't there.

Then read `"$PLURAI_BIN" convert-trace --help` and **follow it** for exact flag names. The
shapes shown below are the contract this skill drives; the CLI's own help is authoritative
if they ever disagree, and a flag mismatch is a fix you make silently.

### Step 1: Gate on a resumed-aware `seed_state` (MANDATORY — do this FIRST)

The brief-driven mode does not run this step: it warns in B1-B2 and continues.

Read `plurai_sim/simulate.py` and find the module-level `seed_state`. Check its parameters.

- **Three positionals** (`initial_agent_context, initial_world_state, resume_seed=None`) with a
  body that handles a non-`None` `resume_seed` → good, continue. Three positionals whose body
  ignores the seed is the case below, minus the signature work.
- **Two positionals** → this harness is broken for *every* scenario, not just resumed ones: the
  child passes three arguments unconditionally, so it raises `TypeError` → `InitError` at init.
  **Fix it now, before converting anything.** Read
  `${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/references/resumed-seed-state.md` and
  **append** the resume branch to the existing body — keep every line of the current world-state
  seeding verbatim, do not retype it from the template. The branch writes the seed into whatever
  holds this thread's history, keyed on `plurai.active_session_id()`, then asserts the write
  landed. On LangGraph that spells out as `convert_to_messages(resume_seed['messages'])` +
  `await graph.aupdate_state(config, values, as_node=<terminal node>)` on the graph that owns the
  saver, then the `next == ()` tripwire, then the seed's optional `resume_point`; that reference
  gives the same steps for a harness with its own store and for one that constructs its session.
  It also lists the ways this write silently half-succeeds (a default-less third parameter, a
  write to something other than the store `invoke_agent` reads, a container too narrow for
  `agent_state`, a resume left mid-step, a start node nothing ever starts at).
- **No `seed_state` at all** → write the base hook from
  `${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/references/seed-state-contract.md`
  FIRST, then add the resume branch. The child auto-discovers it as a module attribute — there is
  no wiring step.

Then re-run the Python verifier. There is no resume flag to pass — it gates three things, in
every lane, unconditionally:

- `seed_state_bad_signature` fails a hook with fewer than three positionals (a two-parameter
  `seed_state`).
- `seed_state_ignores_resume_seed` fails a hook that declares the third parameter and never
  reads it. This is a flat failure everywhere, not a lane-specific one: `convert-trace` writes a
  resume pin into every bundle it converts and no config key declares an environment fresh-only,
  so there is nothing to opt into here.
- `seed_state_ignores_resume_point` fails a hook that takes the seed and never names its
  optional `resume_point` key. Same argument one level down: the key travels uninterpreted from
  `--start-node` to this hook, so a harness that drops it starts at the graph's entry point and
  the run scores a full replay as the pinned resume. Honouring it clears the rule; so does
  refusing it. Only accept-and-drop trips.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py" \
    plurai_sim/simulate.py
```

**A green verifier is still not proof this fix is complete**, and in this lane it is weaker than
it looks. It knows the body *touches* `resume_seed`, not what it does with it — and the rule is
satisfied by a hook that merely **refuses** the seed (`if resume_seed: raise ...`). That is the
right answer for a harness that cannot resume and the wrong one here, where it would kill every
scenario you are about to generate. Read the branch and confirm it REPLAYS. The verifier also
cannot see whether the third parameter carries a default, nor whether the replay reaches the
store `invoke_agent` actually reads. Writing to a handle nothing reads, a container too narrow
to hold `agent_state`, and a resume left mid-step all read the parameter and still leave the
history empty — `resumed-seed-state.md` enumerates them. Re-read the signature and the resume
branch yourself; the verifier is the floor, not the check.

**Do not skip this gate because the customer "just wants to see it work".** A run that
skips it produces green plumbing and meaningless scores — the exact shape of failure that
is invisible from the outside.

> **Skill-lane Python agents** (the harness `/simulation-setup-skill` generates — its
> `simulate.py` drives a `ClaudeSDKClient` rather than a graph app): resumed scenarios
> ARE supported, at messages fidelity only — the session is constructed
> *inside* `seed_state`, and the templates fold the pinned transcript into the context the
> session opens on, at construction. Two caveats to pass on plainly: a seed carrying a
> non-empty `agent_state` is refused at init (the nested agent has no state outside its own
> conversation to restore it into — do not convert traces whose pin carries real state for
> this lane), and the shortfall is REPORTED rather than implied: the templates return a
> reduced-fidelity report from `seed_state`, which the run record carries as
> `resume_fidelity`. Read it before comparing a skill-lane score against a lane that
> restores state as well.
> You are in this lane if `plurai_sim/simulate.py` imports `claude_agent_sdk` / constructs a
> `ClaudeSDKClient`, or if `plurai.config.yaml` was written by the skill sub-skill.

### Step 2: Locate the exports + agree the scope

Ask the user which exports to use and how many. **Do not open the files** — you need paths
and a count, not content. One export becomes one scenario, so this is a direct choice about
suite size and cost.

If they point you at a directory, list its export files (`.json`, `.jsonl`, and their `.gz`) and confirm the count with them
before converting. A directory of 400 threads is not a suite; a hand-picked 10–30 covering
the flows they care about is.

### Step 3: Convert each export into a bundle folder

```bash
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR

"$PLURAI_BIN" convert-trace \
    --workspace-root "$PLURAI_DIR" \
    --export <path/to/thread-export.json> \
    --source-format <langsmith|openinference|adapter-name> \
    --out-dir "$PLURAI_DIR/trace_scenario_bundle"
#   --source-format picks the reader. Two built-ins read production traces: `langsmith` and `openinference`
#     (the SDK's third, `plurai-run`, reads a finished simulation run and belongs to /record-regression).
#     There is NO detection — the flag defaults to `langsmith`, so a span tree needs
#     `--source-format openinference` spelled out, and an export in a shape neither reader
#     knows needs the `format:` name of the workspace adapter /author-trace-adapter wrote.
#     Without the right name the LangSmith reader gets the file and refuses it as malformed.
#     Drop the flag only for a genuine LangSmith export.
#   --workspace-root is the plurai_sim dir — that is where the CLI resolves this env's
#     anonymization posture from (plurai.config.yaml). Pass the SAME spelling every time:
#     "." and the plurai_sim dir do NOT share .plurai/ state.
#   --out-dir is the bundle staging dir Step 4 will ingest. One invocation adds ONE
#     BUNDLE folder to it, carrying the folder materials + resume_pin.json (the pin).
#     NOT resume_seed.yaml — that is generation's downstream output, see the Glossary.
#   Repeat once per export. Deterministic — no LLM, no network, no backend call.
```

**`--start-node <node>` pins where turn 1 begins, and it is off by default.** Without it the
resumed agent starts at its graph's normal entry point with the transcript already restored,
which is what a resume means and what almost every export wants. Add the flag only when the
user asks for the continuation to begin at a specific step — an approval node, a checkout node,
whatever the conversation was about to reach:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
"$PLURAI_BIN" convert-trace \
    --workspace-root "$PLURAI_DIR" \
    --export <path/to/thread-export.json> \
    --source-format <langsmith|openinference|adapter-name> \
    --out-dir "$PLURAI_DIR/trace_scenario_bundle" \
    --start-node <node>
#   One value for the WHOLE invocation: point --export at a directory and every folder it
#     writes carries the same node. Pin different nodes by converting in separate runs.
#   The value must be a TOP-LEVEL node of the harness's compiled graph, spelled exactly as
#     the graph declares it. Nothing here can check that — the converter never sees the
#     graph, and neither does the backend. The name is validated at run time by simulate.py,
#     which refuses an unknown one. Ask the user for the spelling; do not infer it.
#   Do NOT use this to skip work the trace already did. The transcript restores that.
```

Two things follow from the pin travelling uninterpreted. It reaches `seed_state` as the seed's
optional `resume_point` key, so **the harness must be one that can honour it** — a graph
harness starts turn 1 there, and a harness with no graph node to start from (the skill lane,
anything driving a `ClaudeSDKClient`) REFUSES the scenario at init, naming the node. And the
scenario is pinned for its whole life: re-converting without the flag is the only way to unpin
it, since nothing downstream can drop the key on its own.

**On the full lane, use this lane's OWN staging dir — `trace_scenario_bundle`, never
`scenario_bundle`.** `/upload-scenario-bundle` stages hand-authored folders in
`$PLURAI_DIR/scenario_bundle`, and helping yourself to its dir does two silent kinds of
damage: `ingest-bundle` rglobs the whole tree, so leftover folders from an earlier run of the
other lane upload as extra scenarios; and `convert_trace` **rewrites `BUNDLE.md`
unconditionally** from the folders it finds, so a hand-authored overview is replaced with a
generated one at exit code 0, with nothing printed. Start from an EMPTY dir — if it already
holds folders from an earlier run, clear it first, or every one of them lands in the new set.

**Sharing a dir on purpose is a different thing, and it is the both-lanes route.** On the
convert-only entry the router names a shared dir already holding the hand-authored folders of
the same set, and converting into it is correct: the overview `convert_trace` rewrites NAMES
those folders rather than describing a set that excludes them, which is why the router runs
this lane last. The folder boundary still holds by DEFAULT: the converter refuses to write
INTO an existing pinless folder of the same name, and renaming either side to get past that
refusal is not the fix. What pairs a conversation with a folder of records is `--into`.

**`--into <folder>` pairs ONE conversation with ONE folder of records.** Use it when the
transcript being converted refers to the very records an authored folder holds, so the
scenario must BOTH continue that conversation AND stay grounded in them:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
"$PLURAI_BIN" convert-trace \
    --workspace-root "$PLURAI_DIR" \
    --export <path/to/thread-export.json> \
    --source-format <langsmith|openinference|adapter-name> \
    --out-dir "$PLURAI_DIR/grounded_scenario_bundle" \
    --into refund-dispute
#   --into names a FOLDER under --out-dir, never a path. That folder must already exist and
#     already hold a ROLES.md — the router's first leg authored both.
#   --start-node composes with this, and here the "one value per invocation" rule costs
#     nothing: --into converts exactly one export, so the node is that pairing's own.
```

The pin and transcript land inside that folder, and its `ROLES.md` is APPENDED to: the
hand-authored description stays first, a resume section follows it. Because the flag pairs one
export with one folder it takes a single `--export`, not a directory — run it once per pairing,
and convert the unpaired exports over the same dir without it. Re-running the same pairing is
idempotent: the resume section is written once, and a second run leaves every byte in place.
**Re-running WITHOUT `--into` is not.** The same conversation then lands a second time, in a
`thread-<name>` folder of its own, and the set carries it twice with nothing printed. Re-run a
shared dir with the same flags you used the first time.

The refusal to watch for on that entry is a **hand-authored folder with no readable
`ROLES.md`**: the converter names it and refuses the whole run before writing anything. That
is the other lane's folder to fix, not yours to paper over with a `ROLES.md` of your own
invention — say which folder, and let it be authored properly.

**Under `anonymized` posture this refuses, and that refusal is final here.** The bundle lane
bypasses the SDK's ingest posture gate, and `anonymize: true` is a once-per-env human
consent act stating that the backend never holds verbatim content — which a resume pin, by
construction, is. **Relay the converter's refusal to the user verbatim** ("trace-to-scenario
is raw-only for now") rather than paraphrasing it, and stop. Do not offer a workaround, do
not edit `plurai.config.yaml` to flip the posture, and do not fall back to a non-resumed
bundle: changing posture on an env that has already uploaded artifacts is refused fail-closed
anyway, and silently downgrading to fresh scenarios delivers the opposite of what was asked.
Scrubbed bundles are recorded future work, not a thing you can assemble here.

**Read the stdout summary, never the stderr lines.** Every invocation prints one JSON summary
on stdout, whatever its outcome: `scenarios` (the folders written), `skipped`
(`[{source_file, gate_id}]`, each export the converter could not carry faithfully) and
`gate_findings` (every finding — each skip's, and the advisories on the folders written). Each
export converts or is skipped on its own, so one malformed or truncated export never costs the
others; exit 2 means nothing was written. No summary at all means either the invocation itself
was refused (the posture, a flag) or the thread was refused by an SDK too old to report
per-thread skips — upgrade the SDK in the client's venv (Precondition 4) and convert again. A skipped export and its finding are yours to decide on, alone
(`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`): repair at the source when the remedy is in
reach — a re-export, the right `--source-format` — else leave that export out and carry on. The
customer hears that a conversation was not converted, never the finding. Never hand-repair a
trace file to get it through, and never hand-author a `resume_pin.json`. If you find yourself
editing trace JSON, you have left this skill's contract.

**A `trace.messages_channel_inferred` finding is about the harness.** The conversation was read
from a state channel other than `messages`, named in the finding's `evidence.channel`. The
resumed agent keeps its history there, so the harness must write the seed into that channel:
set `_CONVERSATION_CHANNEL` in `plurai_sim/simulate.py`'s replay to it
(`${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/references/resumed-seed-state.md`). A
harness scaffolded before that constant existed writes a literal `"messages"` key: add the
module-level `_CONVERSATION_CHANNEL` beside `_pending_goto` and use it in both places the replay
names the channel (the `agent_state` filter and the written values), as that reference does. Then
re-run Step 1's verifier. Left on `messages`, every resumed scenario of that agent opens on an
empty history.

### Step 3b: Check every pin resumes, before the bundle costs anything

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/scripts/check_resume_pins.py" \
    "$PLURAI_DIR/trace_scenario_bundle"
#   Reads the staged resume_pin.json files and applies the backend's own rule: a trailing
#   user message is lifted into the opener, and what remains must end on a COMPLETED agent
#   turn — an agent message, or, under a lifted opener, the results of the agent's last call.
#   Then the whole seed: every tool call has exactly one result after it, and at least one
#   message carries text or a tool call. Message ids are unique across the whole pin.
#   Deterministic, offline, structural output only — no trace text is printed.
#   Exit 0 all clean · 2 at least one folder the backend will refuse.
```

**Run this before Step 4 and act on what it says.** Every verdict here is one the backend
reaches anyway, but it reaches it *after* the upload, inside a generation job: a mid-turn pin
comes back as a folder that generated nothing, in a report the customer has already paid for.

- **REFUSED** — the thread was exported mid-turn, most often the moment a tool returned and
  before the agent replied, with no user message after it, or while an agent's tool call was
  still waiting on its result. (A turn that closes on tool results answering every call of the
  agent message before them, followed by the user's next message, is complete and passes.)
  Resuming there drops the agent's own turn, so the conversation reads as if it ignored its own
  tool result and the judges score that as agent behaviour.
  **Stop and tell the user which threads, in the checker's words.** The fix is at the source:
  re-export the thread so it ends on a completed agent turn, or drop it from this run and convert
  the rest. **Never edit a pin, and never trim a transcript to get one through** — the pin is
  the record of what the conversation was, and a trim made here changes that record with
  nothing on file to say so. If the customer does want a shorter conversation, they shorten
  the EXPORT and you convert again.

If a folder is dropped, delete the whole folder from the staging dir before Step 4 — the
ingest rglobs the tree, so a folder left behind uploads whatever else it holds.

### Step 4: Ingest + start the set

Same lane, same CLI, same MCP tool as `/upload-scenario-bundle` Step 4 — the converter's folders
are ordinary bundle folders that happen to carry a pin. One difference: there, `BUNDLE.md` is
optional and hand-authored, so `overview_md` is optional. Here the converter always writes one,
so passing it is **mandatory** (see below).

```bash
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-bundle)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# Preflight: bash cannot see which backend the MCP server is bound to. Call the read-only
# backend_identity() MCP tool first and paste its object here verbatim.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-bundle \
    --workspace-root "$PLURAI_DIR" \
    --bundle-dir "$PLURAI_DIR/trace_scenario_bundle"
#   stdout → the single-archive manifest JSON (capture it verbatim, change nothing)
#   stderr → the resolved TARGET line, then the disclosure preview. READ THE TARGET LINE:
#            it is the authoritative statement of which backend this upload reached.
```

A `WARNING (disclosure: home_named)` line from `ingest-bundle` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

Then read the staged `BUNDLE.md` and pass its content as `overview_md`. This is the ONE file in
the staging dir you open, and the finalize call is incomplete without it — it does not fail, it
succeeds with the overview missing:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$PLURAI_DIR/trace_scenario_bundle/BUNDLE.md"
```

**Under anonymized posture, when `ingest-bundle` printed `overview_md: send the content of
<path>`, pass THAT file instead**: it is `BUNDLE.md` anonymized like the archive (folder names
pseudonymized, content scrubbed), and the original would send what the scrub removed.

```
upload_scenario_bundle_start(
    manifest=<the captured stdout manifest, opaque>,
    overview_md=<raw posture: the BUNDLE.md content, verbatim; anonymized posture: the content of $PLURAI_DIR/.plurai/uploads/overview_md.md, the file ingest-bundle named>,   # MANDATORY on this path
)   →  UploadFinalizeResult { set_id, status: 'done', ... }
```

**Why this one file is yours to read, and why omitting it breaks the lane.** `BUNDLE.md` is the
set-level overview; `ingest-bundle` deliberately EXCLUDES it from the uploaded objects (it is not
a per-folder material, it rides the start call inline), so if you don't pass it here it never
reaches the backend at all. The converter writes real steering into it — that the pin content is
authoritative and must be carried through unchanged, and that the authored opener must address
what REMAINS of the arc rather than what already happened. That is precisely the property Step
Verify item 2 checks (`first_talk` reads as a continuation): drop the instruction and you are
testing for an effect whose cause you removed. Reading it does not breach this skill's
never-read-a-raw-trace rule — the converter writes only folder names, source filenames, message
counts and the final message *type* into it, never trace content.

A non-zero `ingest-bundle` exit is a **HARD STOP** — nothing was uploaded. Fix the local
environment silently and re-run. Retrying with the SAME manifest is idempotent; a fresh
`ingest-bundle` run always mints a NEW set.

Confirm the folder count matches the number of exports you converted. A short count means a
conversion failed in Step 3 and you missed it.

### Step 5: Confirm the set is done

```
get_upload_meta(upload_id=<set_id>)   →  status: 'done'
```

Synchronous — one confirming read, no polling.

### Step 6: Write the markers

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR
printf %s "<set_id>" > "$PLURAI_DIR/.scenario_bundle_id"
printf %s "0"        > "$PLURAI_DIR/.scenario_bundle_augmentation"
printf '%s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "<one line: what this bundle is FOR — e.g. resume <n> exported conversations at their real resume points>" \
  > "$PLURAI_DIR/.scenario_bundle_intent"
```

`0` is the right default for this lane — faithful reproduction of the real resume point. The
intent line is not optional: `/generate-scenarios` takes the bundle path only when
`.scenario_bundle_intent` explains the marker it finds, and with the file absent it skips the
bundle and generates a fresh graph-walk set — which is exactly the "no `resume_seed.yaml`"
failure Step Verify below would then report.

### Step 7: Hand off to the suite build

Tell the user to run `/simulation-generate-suite`. Its scenario build reads the markers and takes the
bundle path automatically; there is nothing extra to pass for resumed-ness, because pin presence is the
only marker. It extracts `scenario_variables.yaml` itself when the scan skipped it. For a
STATEFUL agent it stands the mocked world up first (its Step 0c), so the resumed scenarios seed a
real `initial_world_state`: generated with no `scenario_state_schema.yaml`, they run against an
empty world.

Warn them about one thing in the generated output: **`estimated_min_turns` on a resumed
scenario counts the REMAINING conversation only**, not the replayed history. A resumed
scenario with `estimated_min_turns: 3` is a 3-turn continuation of a 20-turn conversation,
not a 3-turn conversation.

### Step Verify: confirm the scenarios are actually resumed (MANDATORY)

Generation succeeding is not proof of resume. Check, on at least two generated scenarios:

1. **The GENERATED scenario folder carries `resume_seed.yaml`** with both `messages:` and
   `agent_state:` keys. This is generation's output, not the `resume_pin.json` you uploaded
   — you are checking that the pin was consumed, not that it survived. Its absence means the
   folder generated fresh: the pin was dropped or rejected.
2. **`first_talk` reads as a continuation, not an opening.** A resumed scenario's opener is
   scripted from the trace tail; if it reads like a stranger starting a new conversation,
   the pin did not take.
3. **The job reported no failed folders.** A malformed pin fails its folder closed and is
   surfaced on the job — check for it rather than assuming a clean count.

Then run one scenario end to end (`/simulation-run`) and confirm from the transcript that
the agent's first reply demonstrates knowledge of the replayed conversation. That is the
only check that exercises the whole chain, and it is the one that catches a `seed_state`
that ran but wrote nothing.

## Brief-driven mode (`--from-brief`)

`/analyze-traces` chains here once its brief is published, and an operator can run it directly.
It turns the brief's **trace failures** into **trace scenarios**: for each chosen trace failure,
one production conversation from the local traces, **cut** just before the user turn whose agent
reply shows the failure, written to `plurai_sim/trace_scenarios/trace_<slug>/`. **It writes
folders and stops** — no ingest, no upload, no generation. A trace scenario reaches a scenario
set only through the suite's final batch, and it carries no blame: a regression is recorded from
its run only when that run shows the failure and `/simulation-fixes` confirms an agent issue.

```
/trace-to-scenarios --from-brief [--workspace-id W] [--max-new N] [--max-total N] [--failure "<name>"]… [--trace <source_file> --cut-turn K]
```

| Flag | Meaning |
|---|---|
| `--workspace-id W` | The workspace the brief was published under. Default: this bench's id (B3). |
| `--max-new N` | The per-brief cap for this run: preselect at most N top-ranked trace failures. The 16 one confirmation lists can lower it. Not with `--failure`. |
| `--max-total N` | The total cap for this run: at most N `trace_*` folders on disk. |
| `--failure "<name>"` | Take this trace failure, by its exact name in the brief, instead of the top-ranked ones. Repeatable. Named ones are limited by the 16, the total cap and a full final batch, not by the per-brief cap. |
| `--trace <source_file> --cut-turn K` | The operator's hand pick, with exactly one `--failure`: convert this trace (a file name as the brief names it, relative to `$WORKDIR/traces/traces/`), cut before user turn K. B6 does not search. Refused when the total cap is reached. |

**Two caps bound the directory.** The per-brief cap limits how many trace failures one run takes;
the total cap limits how many `trace_*` folders sit on disk, counting every one already there.
Each is its flag, else 5 and 10. The final batch's room applies under both. A cap is a whole number of 1 or more; any other value is refused (exit 2).
**A full total writes nothing and deletes nothing:** the run reports the trace failures it left
out, with no question, and the operator deletes folders or raises the cap. Never delete or
replace a folder to make space.

Run B0 to B9 below and nothing else in this file. From the rest of it this mode keeps the hard
rules, the error posture, and Step 3's conversion rules: never hand-author or edit a pin, and
relay the converter's `anonymized` refusal verbatim.

### B0: Check the installed SDK can render and cut

```bash
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
HELP="$("$PLURAI_BIN" convert-trace --help 2>&1)"
printf '%s\n' "$HELP" | grep -q -- '--render-only' \
  && printf '%s\n' "$HELP" | grep -q -- '--cut-turn' \
  && printf '%s\n' "$HELP" | grep -q -- '--into' \
  && echo 'all present' \
  || echo 'MISSING: convert-trace lacks --render-only, --cut-turn or --into'
```

Each flag is checked on its own, because each is its own capability. `all present` is the only
answer that clears this step. Anything else is a blocker: say that the installed `plurai` cannot
render a trace to choose a cut (it needs 0.9.0 or later), and stop.

### B1-B2: Preflight — posture, local traces, and the harness

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" preflight "$WORKDIR" --plurai "$PLURAI_BIN"
```

It prints `source_kind`: the sink the traces were ingested from, which is also the
`--source-format` every conversion of them names (B8). Note it.

**Exit 2 is a stop**, with one of three causes. Say it in product terms:

- `anonymize: true` in `plurai.config.yaml` — relay `trace-to-scenario is raw-only for now`
  verbatim, and nothing else changes: the brief stands.
- Traces from a source the installed SDK has no reader for — say the installed plurai has no
  reader for these traces. The brief stands, and its walk scenarios still target every trace
  failure.
- No local traces (`$WORKDIR/traces/`, its `manifest.json` or its `traces/` is missing) — the
  traces the brief was built from are not on this machine. Re-ingest with `plurai ingest-traces`
  (the `/analyze-traces` export) into `$WORKDIR/traces`, then run this again.

**`seed_state_warning` is a warning, never a stop.** When it is not `null` — no `simulate.py`
yet, or a `seed_state` that refuses or drops the seed — carry it into the report and continue.
The folders convert without a harness and are correct; the final batch holds every trace
scenario back until `seed_state` replays a seed, and `/simulation-setup-python` (or
`/simulation-setup-bridge` on a bridge harness) wires it. Do not run Step 1
and do not edit `simulate.py` here.

### B3: Read the brief's trace failures

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"; echo "$ART"   # → <bench workspace id>, then the artifacts dir
```

Chained from `/analyze-traces`, its Step 7b has just made this call and written this file: use
`$ART/trace_failures.json` as it is, and do not call `get_trace_failures` again. Otherwise write the
answer of `get_trace_failures(workspace_id=<bench workspace id>)` —
`{ present, workspace_id, generated_at, source, parsed_from, warning, failures: [...] }` — to that
file whole, with the operator's `--workspace-id` value in place of the printed id when one was given.
Always pass a `workspace_id`: a call without one reads the shared `default` slot another bench can overwrite.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" get_trace_failures '{"workspace_id": "<bench workspace id>"}' > "$ART/.trace_failures.json.tmp" \
  && mv "$ART/.trace_failures.json.tmp" "$ART/trace_failures.json" || rm -f "$ART/.trace_failures.json.tmp"
```

Every later step reads that file; never retype, trim or hand-write it.

- `present: false` — no brief is published for this workspace. Say so, point at
  `/analyze-traces`, and stop.
- `failures` empty — the brief observed no trace failure. Say so and stop.
- `warning` not empty — the brief could not say which traces showed some failures, so those
  come back "not located". Carry one line about it into the report.
- The backend has no `get_trace_failures` — a missing server-side capability: run the contact hand-off (`stop_message.py contact --done`).

### B4: Choose the trace failures

When `$ART/grounding_candidates.json` exists, read it first (`${CLAUDE_PLUGIN_ROOT}/docs/grounding-findings.md`
says how it is written). Each candidate is a losing value the findings left as it is: a production value a trace showed against a code fact on a mocked tool, or the other side of any other value conflict (its `observed_tier` says which):
its `tool`, `key`, both values and tiers, and the trace `units` that showed it. A trace failure of the brief that
shows the candidate's tool is one to take: name it with `--failure "<name>"` (below), beside the loudest ones you would
take anyway, because `--failure` replaces the ranked default. A candidate no trace failure of the brief shows is left as it is. It blames nothing: a run that fails decides what
the value means.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" select "$ART/trace_failures.json" \
  --workdir "$WORKDIR" --out "$ART/trace_selection.json"
#   add --max-total N when the operator gave it; add --max-new N, or each --failure "<name>" (and --trace <source_file> --cut-turn K), exactly as the operator gave them
```

It ranks the trace failures by `unit_count`, loudest first, skips the ones a folder on disk or
the `RETIRED` list already records, and preselects up to the per-brief cap. Relay what it prints:

- `selected` — the trace failures B5 renders. Empty: skip to B9.
- `not_selected`, `skipped_existing`, `skipped_retired`, `unusable` — carried to the report, each
  with its reason as printed.
- `cap_line` — at a full total, the one report line naming the trace failures left out; `null`
  otherwise.
- Exit 2 — relay the refusal; nothing was chosen.

### B5: Render the candidate traces

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" candidates "$ART/trace_selection.json" \
  --workdir "$WORKDIR" --plurai "$PLURAI_BIN" \
  --out-dir "$ART/trace_candidates" --out "$ART/trace_candidates.json"
```

Per selected trace failure it keeps the sampled units that name exactly one file present under
`$WORKDIR/traces/traces/` — the brief names each trace relative to it — and renders each through
`convert-trace --render-only` into `$ART/trace_candidates/<slug>/`, git-ignored scratch cleared
on every run. The script never opens a trace; the converter does. A trace the converter skips
is dropped with the finding it was skipped under (`dropped[].finding`, its `remedy` as the
`reason`) — read off the converter's stdout summary, yours to decide on, never the customer's. `candidates` is the **reading order**:
traces with a turn 1 to cut at, fewest user turns first, then single-turn traces
(`cuttable: false`), which can only show a failure on turn 0. Each row carries `export`, the
absolute path B8 converts, and `overlaps`, every folder already converted from the same trace
with its `cut_turn`. Stderr lists each rendering's path in reading order.

### B6: Find the cut in the renderings

For each trace failure, read its renderings in `candidates` order — **at most three** — and take
the first one where the failure is visible at a turn K of 1 or later. When the failure shows at
several turns of that rendering, K is the earliest such turn ≥ 1.

- A rendering heads each user message `### human · turn K`, numbered from 0 exactly as
  `--cut-turn` counts, and fences every message body. Text inside a fence is conversation, never
  a heading.
- **K is the number on the user turn whose agent reply shows the failure.** The cut seeds turns
  0 to K-1 with their replies, and the user message of turn K opens the live leg, so the agent
  meets that moment again, live.
- The hints are the trace failure's `evidence` (what it looked like, possibly truncated),
  `caught_by_check` (the check that caught it) and `revealing_marker` (the signal that reveals
  it). Match the situation, not the exact words.
- For a located trace, note K and one short quote, at most 20 words, of the failing agent reply,
  for B7's question when it asks one.

Each trace failure ends in exactly one of three outcomes:

- **located** — the first rendering that shows it at K ≥ 1.
- **turn 0, covered by the walk** — no rendering read shows it at K ≥ 1, and one shows it on turn
  0. No folder: a turn-0 failure has no history to resume, and the brief's walk scenarios already
  target it.
- **not located** — no rendering read shows it, or none was rendered. No folder. The operator
  can hand-pick a trace and a cut: `--failure "<name>" --trace <source_file> --cut-turn K`.

A hand pick skips the search: its K is the operator's. Read its rendering at turn K only to
quote the agent reply for B7.

**`plan` (B7) weighs the located trace's `overlaps`, not you.** A folder there with the same
`cut_turn` as K already carries this conversation: `plan` reports the trace failure as skipped
(existing), with that folder, and nothing is written. A folder at another cut is an overlap, not
a duplicate — the same trace showing a second failure at another turn — and `plan` asks the
operator about it.

**Read a rendering to choose K and to quote the failing reply in B7, and for nothing else.**
Never open a file under `$WORKDIR/traces/`. Never summarise a rendering, and never copy its text
into a folder: the pin, the transcript and the world facts are the converter's to write.

### B7: Plan the writes, and ask only when a decision is needed

Skip this step and B8 when nothing is located. Otherwise pass every located trace failure to
`plan`:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" plan "$ART/trace_candidates.json" \
  --workdir "$WORKDIR" --out "$ART/trace_plan.json" \
  --located "<slug>" "<source_file>" <K>
#   one --located per located trace failure: its slug, the rendering's source_file, and K
#   add --max-total N when the operator gave it
```

It reads the folders on disk, the final batch's room and the total cap again, and prints, in
rank order: `write` (written without asking); `ask`, `needs_confirmation` and `reasons` — each
`overlap` (the trace already backs a folder at another cut) or `over_room` (more located than
the room holds, `pick_at_most`); `default` (every candidate within the room, the headless
answer); and `skipped_existing`, `skipped_retired`, `not_written` and `cap_line`, carried to the
report as printed. Exit 2 — relay the refusal.

The script decides whether to ask; never ask when it says no, and never skip the question when
it says yes.

**`needs_confirmation: false` is the default path: do not ask.** B8 writes every `write`
candidate, and B9 names each folder with its trace and cut, so the operator can review them and
retire one they do not want.

**`needs_confirmation: true`: ask one question.** Sanction it immediately before the ask:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction trace-scenario-selection
```

and ask ONCE — one ask call, `multiSelect`. One sanction buys one call, not one
question, so everything below rides that single call:

- **The question text** says why it asks, in one line per reason: for `overlap`, that
  `<source_file>` already backs `trace_<x>` cut before turn K', and that writing the candidate
  adds a second folder from it cut before turn K; for `over_room`, that the final batch has room
  for `pick_at_most` more, so choose at most that many. Then what is written without asking (the
  `write` folders), where the folders go (`plurai_sim/trace_scenarios/`, one each, joining the
  suite at its next final batch), and how to pick another trace or cut: re-run with
  `--failure "<name>" --trace <source_file> --cut-turn K`.
- **One option per `ask` candidate, and none for a `write` one.** Label: its folder name,
  `trace_<slug>`. Description:
  `<failure name> · seen in <unit_count> units · <source_file> cut before turn K · agent: "<quote>"`,
  plus `· also backs trace_<x> at turn K'` for each of its `overlaps`.
- **At most four options per question.** With more than four candidates, spread them evenly over
  the fewest questions of the same call that hold them (5 → 3 + 2, 9 → 3 + 3 + 3). Only a single
  candidate needs a second option, `Skip`, since a question takes at least two.

B8 writes the `write` candidates and the ones the operator selected. Under `over_room` it writes
at most `pick_at_most` of the selected ones, the first in rank order; report the rest as not
selected. Under `run_driver: headless` the declared default for `trace-scenario-selection` is
`all`: B8 writes the plan's `default` — every candidate within the room, in rank order. That holds
run directly as well as chained: with no window armed, the ask guard still answers the sanctioned
question from that default and records it, so a headless run never waits on a question nobody
sees.

### B8: Write each planned trace scenario

For each candidate B7 writes, in rank order, with the `slug`, `failure`, `source_file`,
`export` and `cut_turn` its plan row carries:

**1. Write the folder with its paragraph.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" write "$WORKDIR/trace_scenarios" \
  --slug "<slug>" \
  --from-failures "$ART/trace_failures.json" --failure "<failure name>" \
  --trace "<source_file>" --cut-turn <K> \
  --at-cut "<one line: what the agent must do at the cut>"
#   add --hand-picked when the plan row's hand_picked is true, and --max-total N when the operator gave it
```

It composes `ROLES.md`'s opening `## Trace failure: <name>` paragraph from the brief and the cut:
the check that caught it, the brief's evidence quoted and marked as possibly truncated, how many
units showed it, the fault class and origin labelled as inferred by the analysis and not a
diagnosis, the cut, your `--at-cut` line, and the other sampled traces as evidence. It assigns
no blame.

**`--at-cut` decides what the trace scenario is graded on.** Every build's authoring session
derives the scenario's `scenario_target` from it, as it does from a regression's Probe line. Say
plainly what the agent must do in its reply to turn K — for example `The agent must give a
refund date only when the refund tool returns one.` Write it from the trace failure and the
situation at turn K; state the conduct expected, never whose fault the production failure was,
and copy no conversation text into it. `--trace` must be one of the traces the brief sampled for
this trace failure; a hand pick passes `--hand-picked`, and the paragraph then says the operator
chose it. Exit 2 names what to fix. A folder of that name, or a folder carrying the same trace at
the same cut, means the trace failure is covered: report it as skipped (existing) and go on. A
refusal at the total cap means the folders filled up since B7: report the candidate as not
written, total cap, and write no further candidate.

**2. Convert the trace into it, cut at K.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
"$PLURAI_BIN" convert-trace \
    --workspace-root "$WORKDIR" \
    --export "<the export path candidates printed>" \
    --source-format "<the source_kind B1-B2 printed>" \
    --out-dir "$WORKDIR/trace_scenarios" \
    --into "trace_<slug>" \
    --cut-turn <K>
#   --export is the candidate row's `export`, verbatim; never build the path from the brief's name.
#   --into appends the converter's resume section below the paragraph, ending with the world
#     facts ("World the live leg needs": the tool calls and results the trace recorded after the
#     cut), and writes resume_pin.json and transcript.md beside ROLES.md.
```

**A non-zero exit means nothing was written, and the folder holding only `ROLES.md` must not
survive.** Delete it — `rm -r "$WORKDIR/trace_scenarios/trace_<slug>"` — then read why from the
converter's stdout summary: `skipped` names the trace and `gate_findings` carries its finding
(no summary means the thread was refused by an SDK too old to report per-thread skips: upgrade
the SDK in the client's venv, Precondition 4). Decide on the finding as Step 3 does,
report the trace failure as not converted, and go on with the next candidate. The final batch
would hold such a folder back, and every listing refuses it. On a zero exit, a
`trace.messages_channel_inferred` finding in the summary is Step 3's harness note.

**3. Once every candidate is done, check the pins, rewrite the overview, stage, log.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/scripts/check_resume_pins.py" "$WORKDIR/trace_scenarios"
```

Exit 2: the backend will refuse a folder. Delete each REFUSED folder written in this run and say
why in the checker's words; a refused folder from an earlier run is reported, not deleted.
Never edit a pin.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" overview "$WORKDIR/trace_scenarios"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" trace_scenarios
```

The converter rewrote `BUNDLE.md` for converted traces; `overview` rewrites it from what is on
disk. The directory is tracked in git, like `regressions/`: stage it, never commit it. Then log
each folder written:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind trace-scenario \
  --finding "wrote trace_<slug> from <source_file> cut before turn <K> — <failure name>"
```

### B9: Report

One line per outcome, each only when it has entries:

- **Written** — one line per folder: `trace_<slug>` — `<failure name>` · trace `<source_file>` cut before turn K.
  They join the scenario set at the next `/simulation-generate-suite` final batch. Then, once,
  the `Privacy:` line `stage-inputs.sh` printed in B8, verbatim, without its `stage-inputs: `
  prefix. When it printed none (no git repository, or staging failed), say instead that the
  folders hold production conversations verbatim and are not tracked in git.
- **Turn 0, covered by the walk** — the trace failures.
- **Not located** — the trace failures, and how to hand-pick one.
- **Skipped (existing)** — each trace failure with the folder that already covers it: a folder
  recording the same trace failure, or the same trace at the same cut.
- **Skipped: retired** — each trace failure in `select`'s or `plan`'s `skipped_retired`, with
  the folder name the retired list holds and its date. To let the chain write one again, delete
  its line from `trace_scenarios/RETIRED`.
- **Not written, no room** — the plan's `not_written` whose reason is the final batch's room:
  each trace failure located after the final batch filled up. It waits for a run with room.
- **Not written, total cap** — the plan's `not_written` whose reason is the total cap: each
  trace failure past it. When nothing was written because the total is full, print the
  `cap_line` that `select` or `plan` returned, verbatim, instead of this line.

Then, when present: the trace failures not selected (over the per-brief cap, the room or the
16, or left unselected by the operator; the ones a printed `cap_line` already names are left out), each trace failure whose conversion the converter skipped (named, never with its finding), the
`seed_state` warning, and the brief's `warning`. Run directly, these lines are the report.
Chained from `/analyze-traces`, print them in the flow before its hand-off — never at the top of
the final message, which opens on the brief's anchor — and the hand-off carries one
`Trace scenarios:` line (its Step 7b gives the shape).

### Retiring a trace scenario

A trace scenario the operator no longer wants is **retired, never just deleted**. Each
`/analyze-traces` chain reads the same brief again, so a folder that is only gone comes back at
the next chain. Retiring removes the folder and adds one line — slug, source trace, cut, date and
trace failure — to `plurai_sim/trace_scenarios/RETIRED`, which the chain reads in B4 and B7:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" retire "$WORKDIR/trace_scenarios" --slug "<slug>"
```

It removes the folder with a staged `git rm -r` (a plain `rm` leaves the files in the index, so
they would still be committed) and stages `RETIRED` with it; the operator commits both. Git
history keeps every copy already committed. It refuses, removing nothing, when the folder holds
unstaged edits or untracked files, is ignored by git, or is a symlink.

**A folder already deleted by hand** is recorded with `--record "<slug>"` instead. It reads the
trace and the cut from the pin git still holds (the index after a plain `rm`, or `HEAD`), drops
what the index holds of the folder, and appends the line. When git holds no pin, pass
`--source-file <trace> --cut-turn K`; the loop log's `wrote trace_<slug> from …` entry names both.

`/simulation-fixes` retires a trace scenario the same way when a regression replaces it, and that
retirement is listed in `RETIRED` too.

## Last step — pull the grounding mirror

Run this once, after the last backend call above and before the hand-off. It copies the latest
grounding ledger version to `$WORKDIR/grounding_ledger.yaml`, so the next skill and the next commit see
it; `${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md` is the canonical source. It never fails the skill and
never goes in a report.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

Carry on whatever `MIRROR=` line it prints: `unverified` and `ungrounded` change nothing here. No `MIRROR=`
line means the client pin or the wrong-backend guard stopped the block; run the contact hand-off (`stop_message.py contact --done`). Never write, patch or "fix" `grounding_ledger.yaml` yourself: the pull replaces it whole, and
a fact is changed in the agent's descriptions.

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

**The converter's `anonymized` refusal is the one exception to "never relay raw output"** — it
is a consent statement, not a diagnostic, and paraphrasing it loses the reason. Relay it verbatim.

## Hard rules

- **Never read or parse a raw trace file. The brief-driven mode may read the converter's own
  rendering (`transcript.md` from `--render-only`) to choose a cut, and for nothing else. Every
  byte of the pin stays converter output.** Not to "sanity check" an export, not to name a
  scenario, not to summarise a conversation for the user. The converter owns every byte of
  format knowledge, and an LLM reading a trace is precisely what this path excludes. You handle
  paths and exit codes.
- **Never hand-author or hand-edit a `resume_pin.json`.** It is converter output, and it
  carries a `provenance` block naming the trace it came from. A hand-written pin is an
  invented conversation wearing a real one's provenance.
- **Never work around the `anonymized` refusal.** Not by flipping the posture key, not by
  converting elsewhere, not by falling back to fresh scenarios. Relay verbatim and stop.
- **The `seed_state` gate is not optional** (Step 1) on the full lane and the convert-only
  entry. Without it every resumed scenario runs blank and scores as an agent failure. The
  brief-driven mode warns instead (B1-B2) and never stops on it: it uploads nothing, and the
  final batch holds every trace scenario back until the harness replays a seed.
- **Message `id`s are load-bearing.** Nothing in this skill touches them, and nothing should
  — `add_messages` reconciles by id, which is what makes re-seeding idempotent.
- **A malformed pin fails its folder closed — let it.** Do not repair the input to force a
  folder through. Sibling folders proceed; report the skipped ones.
- **Don't re-run the ingest→start flow for a set that's already stored.** A fresh manifest
  always creates a NEW set; only retrying with the SAME manifest is idempotent.
- **`augmentation_level=0` unless the user asks otherwise.** Above 0 the LLM invents
  variations around the real resume point — legitimate, but no longer verbatim, and it
  multiplies the suite.

## Failure modes + recovery

| Symptom | Likely cause | What to do |
|---|---|---|
| `plurai capabilities` output has no `convert-trace` | Client SDK predates this lane | Upgrade the SDK in the client's venv (Precondition 4), re-run Step 0. Don't proceed without it. |
| `resolve_plurai.py` exits non-zero | No usable `plurai` in the client's venv (activated or repo `.venv`) or PATH | Install/upgrade in the client's venv as the resolver's stderr says (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run. |
| `resolve_plurai.py` exits 0 but stderr says `NOTE: shadows …` | A SECOND `plurai` install is on PATH ahead of the one used | Not an error, but a FINDING: carry it into the hand-off verbatim. It is why the next command on this machine can behave differently. |
| `convert-trace` refuses, naming `anonymized` posture | `anonymize: true` in this env's `plurai.config.yaml` | Relay the refusal VERBATIM and stop. There is no workaround in this skill. |
| `convert-trace` skips EVERY export, each finding's remedy calling it unparseable JSON or not a LangSmith run dict | `--source-format` names the wrong reader — usually omitted, so `langsmith` read an in-house export | Check the flag before believing the diagnosis. A whole run failing this way is the flag, not the traces: pass `--source-format openinference` for an OpenInference span tree, name the workspace adapter's `format:` (`plurai_sim/plurai_adapters/<name>.yaml`) for anything else, or run `/author-trace-adapter` if there is none. Nothing was written. |
| The exports are OpenInference span trees — spans carrying `openinference.span.kind`, in a `{trace_id, spans}` envelope or a bare JSON list | A built-in reader owns this format | Route them with `--source-format openinference`. Never send a span tree to `/author-trace-adapter`: the mapping language cannot traverse a tree, so that lane refuses it. |
| `convert-trace --source-format openinference` skips a tree, its finding naming a span and what it lacks | The reader recognises the format and diagnoses THIS capture | Act on the cause the finding's `remedy` names — usually a capture to redo with the instrumentation that records structured messages, or a re-export. Leave that export out until then, and never author an adapter to work around it. |
| `convert-trace` skips one export (`skipped` in its summary) | Malformed / truncated / non-thread export | The rest were written. Decide on its finding (Step 3), report the conversation as not converted, and carry on. Never hand-repair the file. |
| `convert-trace` refuses the whole run, naming a folder that holds neither a `resume_pin.json` nor a `ROLES.md` | A hand-authored folder in the shared dir is missing its description file (both-lanes route only) | Nothing was written — the refusal precedes every write. Name the folder to whoever authored it (the record lane's Step 3) and re-run the conversion once it has one. Do NOT hand-write a `ROLES.md` for another lane's folder: it is the fidelity channel for a record you never read. |
| `convert-trace --into` refuses, saying the folder does not exist | `--into` names a folder nobody authored, or the name is misspelled | `--into` merges into an ALREADY authored folder; it never creates one. Check the name against the shared dir's listing. To write a new `thread-<name>` folder instead, drop the flag. Nothing was written. |
| `convert-trace --into` refuses, naming the number of exports it found | `--export` points at a directory while `--into` names one folder | One pairing, one export. Point `--export` at the single export that belongs in that folder, then convert the rest over the same dir without `--into`. |
| Folder count from `upload_scenario_bundle_start` < exports converted | A Step 3 conversion failed unnoticed | Re-check Step 3 output per export, re-convert the missing ones, re-run Step 4 (new manifest → new set). |
| `upload_scenario_bundle_start` raises "missing its required ROLES.md" | Converter output landed in an unexpected shape, or the staging dir has stray folders | Clear the staging dir, re-convert from scratch. Do not hand-add a `ROLES.md` to a converter folder. |
| Generated scenarios have no `resume_seed.yaml` | Pin dropped or rejected at generation | Check the job for failed folders; if folders succeeded but carry no seed, the pin you uploaded was not consumed — this is a backend-side issue with no repair step here: run the contact hand-off (`stop_message.py contact --done`). |
| `first_talk` opens like a brand-new conversation | The folder generated fresh, not resumed | Same as above — the pin did not take. Do not "fix" it by editing `first_talk`. |
| Run transcript shows the agent with no memory of the replayed conversation | `seed_state` ran but wrote nothing — the classic half-success | Work `resumed-seed-state.md`'s silent-misfire causes in order: default-less third param, a write to a handle `invoke_agent` never reads, a container too narrow for `agent_state`, a thread left mid-step (on LangGraph, an `as_node` with live successors), stripped or regenerated message `id`s. |
| A `--start-node` run's transcript shows the agent redoing the steps the trace already took | The harness dropped `resume_point` — it restored the history and started at the graph's entry point anyway | The seed carried the node and nothing honoured it. Run `/simulation-setup-python`'s `verify_simulate.py`: `seed_state_ignores_resume_point` names this exactly. The fix is in `simulate.py` (`resumed-seed-state.md`, step 5), not in the pin. Nothing about the run's own artifacts distinguishes this from a correct resume, so trust the check over the transcript. |
| A `--start-node` scenario refuses at init, naming the node | The harness cannot start at a graph node, or the node is not one this graph has | Both are correct refusals and neither is fixed in the bundle. A skill-lane / `ClaudeSDKClient` harness has no node to start from: re-convert without `--start-node`, or point the export at a graph harness. If it IS a graph harness, the name is wrong — the refusal lists the nodes it does have, and a subgraph's interior node is not one of them. |
| `generate_scenarios_start` raises "would generate N scenarios … exceeding scenario_job_max_count" | `exports × (1 + augmentation_level)` is over the backend cap | Convert fewer exports, or keep `augmentation_level` at 0. |
| `generate_scenarios_start` raises "bundle not found or not finalized" | A stale `.scenario_bundle_id` | Re-run this skill to upload + start a fresh bundle. |

## Glossary

- **thread export** — one exported file holding one production conversation. The
  converter's input; never yours to open.
- **trace failure** — one failure mode the trace-analysis brief observed in production traces,
  grouped across the traces that showed it. Observed, never diagnosed: its fault class and origin
  are labels the analysis inferred, not a cause anyone confirmed.
- **trace scenario** — a scenario folder converted from a production trace, so its conversation
  resumes from what a real user and the agent already said. It is cut just before a trace
  failure's turn and carries the failure paragraph and the **world facts**. It carries no blame: a
  regression is recorded from its run only when the run shows the failure and `/simulation-fixes`
  confirms an agent issue. Like a regression, its presence in `plurai_sim/trace_scenarios/` is its
  status, and it reaches a scenario set only through the final batch of `/simulation-generate-suite`.
- **cut** — the conversion-time split of a recorded conversation into the seeded history and the
  live leg: `--cut-turn K` seeds user turns 0 to K-1 with their agent replies, and the user message
  of turn K opens the live leg.
- **world facts** — the tool calls and results the production trace recorded after the cut. They
  are evidence of what production's tools returned, which the world author reads as a constraint;
  they are not a frozen world.
- **rendering** — the converter's own text rendering of a trace (`convert-trace --render-only`).
  It is the one text derived from a trace that you may read, in the brief-driven mode, to choose a
  cut.
- **resume pin** — `resume_pin.json`, written by `convert-trace` into a **bundle** folder.
  Holds `messages:` (the transcript as LangChain-native minimal dicts),
  `agent_state:` (the rest of the `update_state` values dict, opaque to the backend),
  `resume_point:` (see below), `provenance:` (source file, thread id, trace timestamps) and
  `schema_version:`. Its PRESENCE in a bundle folder is the sole marker that the folder
  generates a resumed scenario. **This is the file Step 3 produces and Step 4 uploads.**
- **resume point** — `resume_point:`, the pin key `--start-node` writes: `{"node": "<name>"}`,
  or an explicit `null` when the flag was not passed. It names the graph node turn 1 starts at
  instead of the agent's entry point. Every stage between the converter and `simulate.py`
  ferries it without reading it, which is why a wrong node surfaces at run time and not here.
  Pins carry `schema_version: 2`, and the reader refuses a v1 pin outright rather than reading
  its missing key as `null` — a folder staged under the older schema is re-converted, not
  upgraded.
- **resume seed** — `resume_seed.yaml`, written by the backend **generation** into the
  **scenario** folder it authors. `messages:` and `agent_state:` byte-preserved from the pin,
  plus `resume_point:` when the pin carried a non-null one. Delivered at runtime on
  `CreateThreadResponse.resume_seed` and handed to `seed_state`'s third parameter verbatim.
- ⚠️ **Pin and seed share the MESSAGE schema and differ in their ENVELOPE — two files at two
  stages, not one file with two names.** The message dicts inside them are the same shape,
  pinned once for both, which is what lets generation byte-preserve them from
  one into the other. The envelopes around those messages are not the same: the pin adds
  `provenance` and `schema_version`, and those have nowhere to live in the seed's top level —
  the structural reason the seed cannot simply BE the pin. `resume_point` is the one envelope
  key that DOES cross, because the harness is what spends it. The pipeline
  is `trace export → convert-trace → bundle folder (resume_pin.json) → generation →
  scenario folder (resume_seed.yaml) → runtime`. Nothing in this skill ever writes or reads
  a `resume_seed.yaml` — you produce pins and verify seeds.
- **resumed scenario** — a scenario whose agent begins turn 1 with the trace's transcript
  and state already restored into it, and whose `first_talk` is a scripted continuation
  authored from the trace tail rather than a cold opening.
- **grounded resumed folder** — one bundle folder holding BOTH kinds: a resume pin beside
  hand-authored materials, produced by `convert-trace --into`. Its scenario continues the
  pinned conversation AND references the records that conversation is about. Its `ROLES.md`
  carries the hand-authored description first and an appended resume section after it. The set
  overview counts it ONCE, among the resumed folders, and names it again under a subsection for
  the materials it also holds.
- **fresh scenario** — every other scenario. A bundle can mix both; the absence of a pin is
  all it takes.
- **scenario bundle** — one finalized set (`scenario_bundle_id`) of per-scenario folders,
  shared with `/upload-scenario-bundle`. Persists until `delete_upload`.
- **single-archive manifest** — the opaque JSON `plurai ingest-bundle` prints to stdout,
  handed verbatim to `upload_scenario_bundle_start(manifest=…)`.
