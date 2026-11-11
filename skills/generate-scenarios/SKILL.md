---
name: generate-scenarios
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: Generate scenarios on the Plurai-hosted backend (no local LLM credentials required) and persist them as ground truth. The plugin writes only a one-line `plurai_sim/.scenario_set_id` reference; the actual scenarios live on the backend until you delete them.
---

# Generate Scenarios

You are the LOCAL Claude Code agent. The customer just finished
`/simulation-scan-agent` + `/extract-scenario-variables` + (optionally)
`/generate-evals`, so `plurai_sim/scenario_variables.yaml` +
`plurai_sim/simulation_prd.md` exist. They want backend-generated
scenarios — adversarial, evolved, covering the agent's flow space —
without provisioning LLM credentials themselves.

```
┌── customer's machine (you) ──────────────────────────────────┐
│  1. Read inputs from plurai_sim/.                             │
│  2. MCP: generate_scenarios_start(...) → scenario_set_id      │
│  3. MCP: job_status(id=set_id) (poll ~5s)                        │
│     → surface phase + scenarios_completed/total.              │
│  4. On status='done':                                          │
│     → final Job envelope has summary (no separate end call).  │
│     → write plurai_sim/.scenario_set_id (one line).           │
│     → tell the user to /simulation-run.          │
└──────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ MCP
                                  │
┌── Plurai-hosted backend ──┴──────────────────────────────────┐
│  plurai MCP server:                                          │
│    generate_scenarios_start → starts the scenario-generation │
│      job using the BACKEND's own configuration               │
│      (model credentials live there, NEVER on the customer side)│
│    job_status → returns current job state + final summary    │
│                 (scenarios persist on disk                   │
│                  until delete_artifact)                      │
│                                                              │
│  Scenarios are stored on the backend as the canonical        │
│  ground-truth definition of what's being tested.             │
│  They stay until deleted explicitly.                         │
└──────────────────────────────────────────────────────────────┘
```

**Why this lives on the backend.** The scenario-gen LLM is Plurai
tooling, not the agent's runtime stack. Routing it through the backend
means:
- Customers never provision model credentials for the backend.
- Plurai picks a model that actually works for scenario
  generation (some stall under thinking-mode, etc.) without exposing
  that decision to customers.
- Scenarios are ground truth — they belong in one canonical place
  (the backend), not scattered across customer machines.

**LangSmith is unchanged.** LangSmith is observability for runs, not a
GT store. Traces still go to LangSmith from the customer's
plurai-run-simulation invocation. The plugin path skips the LangSmith
*Dataset* upload entirely; scenarios live in the backend instead.

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

## Preconditions

1. **`plurai` MCP server is reachable.** Auto-registered
   via the plugin manifest (`.claude-plugin/plugin.json` `mcpServers`).
   The backend is the configured one, the one the saved Plurai login names (a project `.env` is
   not read). Remote backends refuse tokenless calls, so they need that login.
2. **The backend has scenario generation enabled.** A Plurai-hosted
   deployment does. When it is not enabled,
   `generate_scenarios_start` raises a ValueError saying so (Step 4 lists it);
   that is not fixable from here: run the contact hand-off (`stop_message.py contact --done`).
3. **`plurai_sim/scenario_variables.yaml` exists** — from
   `/extract-scenario-variables`, or extracted by this skill itself in Step 1 when the
   workdir comes from an older scan that did not write it (the suite self-heals the same way).
4. **`plurai_sim/simulation_prd.md` recommended.** Without it the
   dimension graph degenerates to a single generic flow; every
   generated scenario walks the same node and the batch loses its
   diversity. The skill warns when the file is missing but still runs.
5. **`plurai_sim/scenario_state_schema.yaml` optional but recommended.**
   When present, each scenario gets `initial_world_state.yaml` +
   `agent_context.yaml` extracted from the schema, so the agent boots
   with the right state on turn 1. Normally `/generate-mock-env` writes
   it. When env-gen was SKIPPED (an all-real / tool-less / pipeline
   agent) nothing does, so it is hand-authored — copy the shape from
   `${CLAUDE_PLUGIN_ROOT}/skills/generate-scenarios/references/example_scenario_state_schema.yaml`
   rather than guessing: the recognised keys are a short closed list,
   and an object field that omits `item_specs` while marked
   `precondition: true` is rejected at load as unsatisfiable.

## The flow

### Step 1: Detect the plurai_sim directory

The skill takes one optional argument: the path to `plurai_sim/`. If
not provided, search the current working directory:

```bash
# Default candidates, in order.
for candidate in plurai_sim ./plurai_sim "$(pwd)/plurai_sim"; do
  if [ -f "$candidate/plurai.config.yaml" ] || [ -f "$candidate/scenario_variables.yaml" ]; then
    PLURAI_DIR="$candidate"
    break
  fi
done
```

If the directory is found but `scenario_variables.yaml` is missing, do NOT send the user back to
the scan — a workdir an older scan set up has none. Run the extract-scenario-variables
skill yourself — fast, local,
no backend job — exactly as `/simulation-generate-suite` Step 0 does, then continue.

If none match, ask the user where their `plurai_sim/` lives (inside a driving command's flow window, sanction it first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction workdir-location`).

### Step 2: Load the input files

Read each as text:

```
variables_yaml             ← plurai_sim/scenario_variables.yaml
prd_markdown               ← plurai_sim/simulation_prd.md            (None if missing)
data_profile_markdown      ← plurai_sim/artifacts/data_profile.md    (OPTIONAL — omit if missing; see Step 4)
scenario_state_schema_yaml ← plurai_sim/scenario_state_schema.yaml   (None if missing)
inputs_schema_yaml         ← plurai_sim/inputs_schema.yaml            (None if missing)
outputs_schema_yaml        ← plurai_sim/outputs_schema.yaml           (None if missing)
document_fields            ← plurai_sim/.document_fields  (JSON list; None if missing — see Step 4)
doc_examples_set_id        ← plurai_sim/.doc_examples_set_id         (one-line string; None if missing — see Step 4)
scenario_bundle_id         ← plurai_sim/.scenario_bundle_id          (one-line string; None if missing — see Step 4)
scenario_bundle_augmentation← plurai_sim/.scenario_bundle_augmentation (int, default 0; only read when .scenario_bundle_id present — see Step 4)
scenario_bundle_intent     ← plurai_sim/.scenario_bundle_intent      (one line, `<written_at> | <what for>`; ABSENT is normal and is itself a finding — see Step 4)
```

Check which optional files exist in one call, then read the present ones:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
for f in simulation_prd.md artifacts/data_profile.md scenario_state_schema.yaml inputs_schema.yaml outputs_schema.yaml .document_fields .doc_examples_set_id .scenario_bundle_id .scenario_bundle_augmentation .scenario_bundle_intent .knowledge_bases .corpus_id .eval_set_id; do [ -e "$PLURAI_DIR/$f" ] && echo "$f=present" || echo "$f=ABSENT"; done   # ABSENT is normal; never probe with ls
```

Parse `.document_fields` as JSON (a list of `{field, format, brief}` dicts). If it's
absent, leave `document_fields=None`; if it's present but does not parse as a JSON list,
treat it as `None` (don't pass a broken trigger) and tell the user in plain product terms
that document generation will be skipped because its configuration didn't parse —
asking me to set up the agent again regenerates it. Don't paste the parse error.

Read `.scenario_bundle_id` with `strip()` (None if absent). When it is present, also
read `.scenario_bundle_augmentation` with `strip()` + `int(...)`, defaulting to `0` if
the file is absent or unparseable, **and `.scenario_bundle_intent` (whole line, `strip()`;
None if absent)**. These switch on the full-scenario IMPORT path, which is **CONFIRMED, never
ambient** — see Step 4 "Scenario bundle" for the statement you owe before taking it.

If `scenario_state_schema.yaml` is missing, search standard env-gen
locations (the file is written by `/generate-mock-env`):

```
<plurai_dir>/simulation_env/scenario_state_schema.yaml
<plurai_dir>/../simulation_env/scenario_state_schema.yaml
```

If `prd_markdown` is missing, tell the user in plain product terms that
scenario diversity will suffer (every scenario walks the same generic
flow). Offer to abort if they want to write the PRD first.

**Pass each file through BYTE-FOR-BYTE — never re-serialize it, and never RETYPE it.** Use the
exact text `Read` returned and put it straight into the MCP argument. Do
NOT reformat, re-indent, "tidy", or convert YAML between block and flow
style — and do not reconstruct the document from memory or from what it
"says": the validator below runs on the PATH, so anything you send that
differs from the file on disk is unvalidated by construction.

In particular, never collapse a block mapping into a flow mapping:
`scenario_state_schema.yaml` is emitted by env-gen in block style, and a
field like

```yaml
co_occurs_with:
  type: list[string]      # valid block style — leave it exactly like this
```

becomes INVALID if flattened to `{type: list[string], required: true}` —
the unquoted `[` opens a YAML flow sequence and the backend job crashes in
`building_graph`. The backend parses these inputs with the same parser the
CLI uses; any local rewrite creates a divergent, breakable path. Forward
the raw bytes.

**Validate it locally before it is sent** — a malformed schema otherwise fails
deep in the backend's `building_graph` phase with a cryptic parser error, and a
key the backend does not read fails not at all:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/extract-scenario-variables/scripts/verify_state_schema.py" \
    "<the scenario_state_schema.yaml path resolved above>"
```

Exit 2 names the offending field. A `⚠` line is advisory (exit 0; decide per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`). A **"declares no `inject`" `⚠` is NOT a generation
defect** and must not be routed as one: the key postdates most installed schemas, the backend still
defaults it to `request` (so the schema runs). The fix depends on WHICH branch authored the schema: on the
env-gen-skipped branch re-run `/extract-scenario-variables` (its Step 4b authors the key). On the
ENV-GEN branch **no re-run closes it**: Step 4b is skipped by design, and `/generate-mock-env`
re-writes the backend's identical bytes, so a second env-gen job is paid for the same red. Add the
key in place instead (`inject: request`, unless the field also seeds world rows) — the same local,
free overlay the document-field annotation rule applies to a DOCUMENT field after every verbatim
write (the `extract-scenario-variables` skill, Step 4b). That rule
covers the document field only, so on a multi-field env-gen schema the other fields' advisories are yours
to add in place, and they return on every fresh re-materialisation of that schema until env-gen
emits the key itself. For every OTHER exit 2: when YOU authored the file (the env-gen-skipped
branch, extract-scenario-variables Step 4b) fix it there and re-`Read` it; when env-gen emitted it,
a failure is a generation defect to route per the error posture. Either way never hand-fix
or re-serialize the payload on its way to the call — that is exactly what introduces the
flow-style break described above. (The script needs `pyyaml`; on exit 3 re-run with the
customer project's venv interpreter.)

### Step 3: Count (keep it small + diverse, NOT full coverage) + interest

**Default `count=3` — a small, quick sample.** The suite's quality gate iterates on this
set to validate the simulation *machinery* (harness coherence, real grounding, gradable
evals), and those are machine-level properties that generalize — so you check them FAST on
a few scenarios. **Do NOT size `count` to cover every flow** — full-flow coverage is a
separate, later concern (a bigger scoring run if the user wants one), not the quality loop.

- **Optimize the few scenarios for DIVERSITY + interesting cases, not breadth.** Three
  sharp, varied scenarios beat three that walk the same flow, and beat twelve for a quick
  quality check. **Know which channel buys which diversity** — the same typed-vs-
  prose split as the language rule below:
  - **Challenge-shape diversity has a TYPED channel: `typical_complications`.** The backend
    samples each scenario's complications from that pool, so what is IN the pool is what can
    be tested — a decision path with no archetype (confirm-a-correct-premise,
    reverse-an-action, transient-state…) is a decision path that never appears, however
    hard you steer in prose. (Current backends additionally SPREAD the draw across the batch
    so one archetype can't land in four of seven scenarios; older ones draw independently.
    Nothing on the wire reveals which, so don't promise the spread — the pool argument holds
    either way.) The extract-scenario-variables skill's Step-3 decision-path
    vocabulary is the required baseline; a coverage ask from the user lands there, as new
    or renamed archetypes — never in `interest`.
  - **A MECHANICAL demand has a TYPED channel too: `steering_demands`.** `interest` is a weight
    on a walk, so the walk is free not to take it — and nothing compared what you asked for
    against what shipped. Anything you would otherwise write as prose in one of these three
    shapes goes here instead, as `[{kind, subject?, slots?}]` (`slots` defaults to 1):
    - `{'kind': 'flow_walked', 'subject': '<flow id>', 'slots': N}` — at least N shipped
      scenarios must be CREDITED with that flow (post-audit, so a scenario that drifted off the
      flow does not count).
    - `{'kind': 'complication_dealt', 'subject': '<complication name>', 'slots': N}` — at least N
      shipped scenarios must ENACT it (`current_elements` minus the session's dropped ones).
    - `{'kind': 'single_flow_slot', 'slots': N}` — at least N shipped scenarios target exactly one
      flow. No `subject`; supplying one is refused.

    The job publishes the delivered-vs-requested tally on
    `detail.dataset_metadata.steering_demands`. **Keep genuinely prose steering as prose** — only
    these three kinds are typed, and an unknown `kind` is REFUSED at the call rather than ignored,
    because an ignored demand is one you believe is being verified. "Make the personas angrier"
    is not mechanical and belongs in `interest`.
  - **`interest` is FLOW-weight steering only, by construction**: the backend can only turn
    it into priority-weight deltas on the dimension graph's flow nodes — that is the whole
    output shape, nothing else fits in it. An ask that names no flow ("adversarial paths",
    "novice users") is explicitly instructed to be skipped and typically yields no deltas at
    all — documented backend behavior, not a bug. Use it to bias WHICH product flows get walked
    ("stress the error-recovery paths"); surface it to the user but don't require it.
- Only bump `count` above 3 when the user explicitly asks for a larger / fuller run; the
  backend's `scenario_job_max_count` (default 50) is the upper bound.

**Conversation-LANGUAGE steering has a TYPED channel — use it, not prose.**
When the user (or the PRD's design constraints) demands scenarios in a specific
language ("the persona writes exclusively in Hebrew"), set the `language` field in
`scenario_variables.yaml` before generating — that field is the ONLY enforced channel
(the backend's language gate validates persona/first_talk against it and repairs
drift). PRD prose and `interest` are advisory: the authoring session may honor them,
and nothing checks that it did (observed live: a PRD-only exclusively-Hebrew
constraint produced three language-SWITCH scenarios, one mixing scripts inside a
single message). Prose steering is fine for emphasis; a hard language requirement
goes in the typed field.

**A MIXED-language suite is also declared, never left to chance.** `language` accepts a
list (`[he, en]`, even split) or a weight map (`{he: 0.7, en: 0.3}`); the backend
normalizes both, assigns one language per scenario at seed time, and gates each scenario
against its own assignment. **Leaving `language` unset does NOT produce variety** — it
just turns the gate off, so a batch usually comes back single-language. If the user wants
a mixed batch, write the mix down. (A backend that does not accept a list or a map rejects
it with a validation error rather than degrading, so fall back to a single string there.)

**A language SWITCH is a declared complication, never a prose flourish.** Whether a
language complication means "speaks it THROUGHOUT" or
"SWITCHES TO it mid-conversation" is untyped in prose and produces unrequested language-SWITCH scenarios, so it rides the complication's `language_mode` field
(`throughout | switches_to`, mandatory beside `language:` — see
extract-scenario-variables). If scenario_variables.yaml predates the field, expect a
backend warning nudging a re-extract; the backend then falls back to classifying the
prose, whose known false positives can drop a complication from the draw ("asks to
switch products" reading as a language switch) — declaring the mode is what removes
that risk.

`artifact_feedback` is an optional free-text note about how the GENERATED
ARTIFACTS should look — used on a RE-RUN after the user has inspected a
prior batch (e.g. "make the invoices closer to the examples I uploaded",
"shorter line items", "keep the letterhead block"). Unlike `interest`
(which steers the dimension graph / which flows get sampled),
`artifact_feedback` steers ARTIFACT AUTHORING: it is injected into the
scenario-generation SDK session (persona / first_talk / scenario_target /
initial_state) AND, when documents are generated, into the document
sub-agent. It is therefore inert for documents unless `document_generation=True`.
It is a plain string with no file content, exactly like `interest`. Pass `None` on a first pass —
there's nothing to react to yet. When the user says "I saw the output, now
make it closer to my examples", this is the channel; for an iterative loop
prefer driving it through `/refine-scenarios`. Note authoring is
non-deterministic, so this is best-effort steering, not guaranteed convergence.

### Step 4: Start the job

`generate_scenarios_start` sends `variables_yaml` (and the other loaded config text) straight to the
backend over MCP. There is no plugin-side preview/consent hook for this call; the harness's normal
tool-approval prompt (naming the tool + its arguments) is the user's consent chokepoint. The
disclosure is not skill-mediated; do not duplicate it in the chat panel.

If the user denies the harness prompt, surface the denial verbatim and stop. Do not retry.

**The agent shape is a DECLARED FIELD the backend dispatches on — you do not decide it.** Stash `plurai.config.yaml` with the other documents below and pass `plurai_config_upload_id`; **omit `shape`.** `generate_scenarios_start` reads the ROOT `shape:` key and dispatches on it, which is the same fact the runner dispatches on at run time, and refuses a `shape=` that contradicts it. One decision, read twice — never decided twice. **Never derive it from `simulate.py`'s hooks**: they say what a file EXPORTS, never what its author MEANT by exporting it, and nothing in this product routes on them.

Read the declaration for YOUR OWN downstream choices below (`memory_challenge`, the knowledge branch) — never to pass it back:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — never a hardcoded plurai_sim/
# `${WORKDIR%%/plurai_sim*}` is the agent root under BOTH layouts: $WORKDIR is `<root>/plurai_sim`
# on a bare workspace and `<root>/plurai_sim/envs/<name>` under a named env, so `$WORKDIR/../.venv`
# resolves to `<root>/plurai_sim/envs/.venv` there — a path that does not exist.
PLURAI_PY_HINT="${WORKDIR%%/plurai_sim*}/.venv/bin"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" -c "import sys,yaml;print((yaml.safe_load(open(sys.argv[1])) or {}).get('shape') or '')" \
  "$WORKDIR/plurai.config.yaml"
```

An empty result means the bench declares nothing: it is generated and run as a `dialog`, which is what the runner does with it too, so the set and the run agree — except for the one harness the STOP below catches. **A one-shot bench must declare `shape: pipeline` as a ROOT key** (beside `workspace:`/`project:`; one nested under `agent:` is read nowhere) — `/simulation-setup` Step 8 writes it, `verify_config --simulate` fails without it and the runner refuses the bench at `$0`. If a customer's config is missing it, ask for the key; do not answer for them.

- **Undeclared, and `simulate.py` binds `invoke_pipeline` but not `invoke_agent` → STOP: ask for the root `shape: pipeline` key and do NOT generate.** The hooks are read ONLY to refuse, never to route: the runner refuses that bench at `$0`, so a set generated now is paid for and unrunnable. AST-parse its module top level (never import it); a `def`, an assignment or an import each bind a hook. **This stop survives `run_driver: headless`**: escalate it as a `blocker` — the guard files a typed `run_driver_input_required` finding and the round stops.

Call the MCP tool. **The stash is pre-authorized by the invocation — never ask consent for
it**; it egresses the PRD and variables exactly as the `generate_scenarios_start` call
below does, the harness's tool-approval prompt is the consent chokepoint for both, and
disclosure is the `*_ID=` lines, never a question. **Stash the big documents from disk
immediately before the call, every time** — inline text parameters ship the whole document through your own context (a
large PRD times several batches is the session's largest context sink),
while the stash goes disk → backend with zero context transit. Ids are content-addressed,
so re-stashing an unchanged file is a free no-op returning the SAME id, and a file edited
since the last stash (yours, or `/build-rag-corpus` Step 6.5's PRD reconciliation) mints a
new id automatically — stash-before-the-call means there is no marker freshness to reason
about, ever:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
# The markers go first. A stash that cannot run, or a refusal below, then leaves no earlier
# run's id to pass for this run's; each stash that succeeds writes its marker again.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .prd_upload_id .variables_upload_id .plurai_config_upload_id .data_profile_upload_id .scan_contract_upload_id
# What an inline fallback would carry through your context, in bytes.
wc -c "$PLURAI_DIR/simulation_prd.md" "$PLURAI_DIR/scenario_variables.yaml" "$PLURAI_DIR/plurai.config.yaml" "$ART/data_profile.md" 2>/dev/null
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require stash-doc)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
if [ -n "$PLURAI_BIN" ]; then
  # The stashes upload the documents; the guard checks the backend before they run.
  source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
  # --out, not $(…): the id goes to the FILE and the diagnostics to stderr, so no redirection a
  # caller adds can fold an error message into the marker. A failed stash removes the marker itself.
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$PLURAI_DIR/simulation_prd.md" simulation_prd --out "$PLURAI_DIR/.prd_upload_id" || { [ $? -eq 7 ] && exit 7; }
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$PLURAI_DIR/scenario_variables.yaml" variables --out "$PLURAI_DIR/.variables_upload_id" || { [ $? -eq 7 ] && exit 7; }
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$PLURAI_DIR/plurai.config.yaml" plurai_config --out "$PLURAI_DIR/.plurai_config_upload_id" || { [ $? -eq 7 ] && exit 7; }
else
  echo "MISSING runner plurai stash-doc"
fi
# Read them back for the tool call below; empty when the stash failed or never ran.
PRD_ID="$(cat "$PLURAI_DIR/.prd_upload_id" 2>/dev/null || true)"
VARS_ID="$(cat "$PLURAI_DIR/.variables_upload_id" 2>/dev/null || true)"
CONFIG_ID="$(cat "$PLURAI_DIR/.plurai_config_upload_id" 2>/dev/null || true)"
if [ -f "$ART/data_profile.md" ]; then
  [ -z "$PLURAI_BIN" ] || python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$ART/data_profile.md" data_profile --out "$PLURAI_DIR/.data_profile_upload_id" || { [ $? -eq 7 ] && exit 7; }
  PROFILE_ID="$(cat "$PLURAI_DIR/.data_profile_upload_id" 2>/dev/null || echo STASH_FAILED)"
else
  PROFILE_ID=ABSENT
fi
source "${CLAUDE_PLUGIN_ROOT}/scripts/stash_scan_contract.sh" || { [ $? -eq 7 ] && exit 7; }   # $CONTRACT_ID + $CONTRACT_PROCEED + the two counts; 7 = the backend refused the version, any other status goes on
echo "PRD_ID=${PRD_ID:-STASH_FAILED} VARS_ID=${VARS_ID:-STASH_FAILED} CONFIG_ID=${CONFIG_ID:-STASH_FAILED} PROFILE_ID=$PROFILE_ID CONTRACT_ID=${CONTRACT_ID:-STASH_FAILED} CONTRACT_PROCEED=$CONTRACT_PROCEED CONTRACT_PROVISIONAL=$CONTRACT_PROVISIONAL CONTRACT_BLOCKED=$CONTRACT_BLOCKED"
```

**Give this Bash call an explicit long `timeout` (e.g. `timeout=600000`).** The stashes run SERIALLY at up to ~60s each, so it is the SERIES that overruns the Bash tool's 2-minute default — and the kill reads as a transport failure on a stash that was working. Without that timeout, run one stash per Bash call.

**What the block can print besides ids, and what each means:**

- The backend's refusal of this plugin or its SDK (the block exits 7 and prints the update text; no ids line
  follows) → **stop.** Say that text verbatim and alone, the way the error posture says a version refusal,
  and run nothing after it: every later call is refused too, so there is no inline fallback.
- `client_pin: REFUSED …` on stderr (the block exits 1; nothing was stashed and the markers are
  gone) → **stop.** Tell the user, in the refusal's own words, that the client pinned for this
  workdir refuses this runner, and do not inline: the pin is the operator's rule for which client
  runs here.
- `SPLIT BACKENDS …`, or `refusing unauthenticated remote calls` (the block exits 1, the same
  state) → **stop.** Tell the user the shell's backend is not the one the MCP server is bound to,
  or has no token; the message names the hosts. Do not inline: the stop is about which backend
  gets the documents, and inline sends them too.
- `MISSING runner plurai stash-doc` → there is no Python runner here (SDK not installed yet), or
  it is too old for `stash-doc`. Every id is `STASH_FAILED`: continue with
  the inline parameters, and tell the user once: "Installing the Python runner (scripts/resolve_plurai.py --install-line prints the command) sends these documents to the backend without passing them through the model's
  context." The `wc -c` lines are the sizes you would inline; no skill sets a byte limit, so a
  document too large to carry through your context is not inlined — name it, and stop.

**The data profile is OPTIONAL and its two failure states are different.** `PROFILE_ID=ABSENT`
means there is no local data to profile (a tool-less or API-only agent) — omit the parameter and
proceed, silently; nothing is wrong. `PROFILE_ID=STASH_FAILED` is the file existing and the
stash failing, which is classified on stderr exactly like the PRD's below. The marker removal at the top
of the block covers the absent branch too: a marker left from a run when the file existed would
resolve flawlessly to a profile that is no longer on disk.

The trailing `echo` is load-bearing, twice over: shell state does not survive to the MCP
call, so the echoed ids are what you actually read when filling the parameters below —
and a failed stash prints `STASH_FAILED` instead of silently reporting exit 0 because the
other stash succeeded. The marker removal (the `clear-marker.sh` line, and `plurai stash-doc --out` on a failure) is
equally deliberate: a STALE marker from before a PRD edit (e.g. the `/build-rag-corpus` Step 6.5 reconciliation) resolves
flawlessly — content-addressed ids never fail on stale, which is exactly what would make
generating from the outdated text silent — so a failed re-stash must destroy it, never
leave it lying as false authority.

For a `STASH_FAILED` id, classify on that document's `stash_doc:` stderr line — the block runs
every stash with `|| true`, so the sentence, not an exit code, is what you have:

- ends `fall back to the MCP stash_input_doc tool` → the backend predates this stash (no
  `POST /input-docs`, no `/storage`, or an `/input-docs` that ignores the stored object; a small
  file gets this too) → call the MCP `stash_input_doc(text=..., label=...)`;
- `backend refused the document`, `backend would refuse the document`, or `do not send the
  document inline` → the document itself is refused: not UTF-8, blank, over the backend's cap, or
  a 413 from the backend or its proxy. Fix the file; never inline — inline meets the same refusal
  through your context;
- any other `stash_doc:` error (a transport failure, a failed storage upload, no id in the
  answer) → pass that document's inline text parameter instead (benign, just costlier).

**RELAY the stash's `stash_doc: WARNING:` stderr lines — print every one and fix the
document before generating.** They are how the backend tells you a document will not survive
downstream intact, and this is the one moment the fix is cheap: the same defect otherwise
surfaces ~35 minutes into the job, as scenarios failing extraction on facts that are in the
PRD.

**`ledger_version` — the grounded facts the scenarios are built from.** When `generate_scenarios_start` names a
`ledger_version` parameter in your tool list, the backend works from a SETTLED version of the agent's
grounding ledger and requires that id: the config's tool arguments, the PRD's tool, entity and
agent-fact sections, and the variables' tools and entities are not read for the facts they carry (what the
variables say ABOUT a tool, its description, is still read from them, by name), and the trace brief's tools
are not read at all. The variables' user profiles and complications are scenario design and are read as
sent, so an edit to them is dealt on the next draw under the same pin. When
your tool list does not name `ledger_version`, the backend generates from the inputs below: pass nothing.

1. **The pin comes first.** `/simulation-generate-suite` Step 0b pins one settled version for the whole build,
   and every generator of the build sends that one; a run of this skill on its own reads the pin an earlier
   suite left. Read it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pinned --workdir "$WORKDIR"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" waived --workdir "$WORKDIR"
```

   `WAIVED until <date>` printed: the operator waived grounding for this workspace, and the backend runs a waived
   workspace ungrounded only on a call that carries no `ledger_version`. Send the call with `ledger_version` omitted
   (the one case where the argument is left out, bar an undelivered env below) and skip the steps that read a job: `pinned` prints nothing here, and
   neither a `.ledger_version` left on disk nor a job recorded for another workspace is a pin.

   An id printed is `ledger_version`, exactly as printed: skip steps 2 to 4, because a pin is always a settled
   version. Nothing printed means no suite has pinned one here, or the latest was not settled when it looked:
   go on. Never read the mirror to choose a version.

2. Read the job the last delivery recorded on this machine:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$WORKDIR/.grounding_job" 2>/dev/null || echo "NO_GROUNDING_JOB"
```

3. `job_status(id=<the job id>)`. `status: done` with `detail.settled: true` → the pin is `detail.ledger_version`,
   exactly as printed. Still `queued` or `running`, or `settled: false` → wait for it
   (`poll_job.sh <the job id>`, foreground) and read it again; a non-empty `detail.held_by` is the findings
   handler's (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`), never a question for the user. A `failed` job made
   no version: do not call.
4. `NO_GROUNDING_JOB`: nothing was delivered under this env from this machine. Call the tool WITHOUT `ledger_version`
   and let the backend decide: it runs a workspace the operator waived ungrounded, and refuses any other before
   anything starts. A message that starts `ledger_version refused (missing)` means the agent's setup is not recorded
   yet: stop, and tell the user once, in product terms, that `/simulation-scan-agent` records it. When the call
   starts, read `detail.grounding_waived` on its answer; when it carries `expires`, keep it with `python3
   "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" waiver --workdir "$WORKDIR" --expires
   <detail.grounding_waived.expires>` so the next call omits the version at once.

Read only `$WORKDIR`'s own markers: the bench root above `envs/` holds the job of the bench's own workspace, which this
env's call does not name, so its job and its pin are never this env's.

Never send "latest", never read an id out of `grounding_ledger.yaml` or the PRD, and never send an id from another
workspace: the backend refuses all three, and a call with no settled version is not made at all. Never drop the
argument to get a call through. The same id goes to the refinement session (`/refine-scenarios`) and to the graph pre-warm, and a top-up (`adopt_from_scenario_set_id`) carries the id of the set it tops up: a different version is a different world, and the backend refuses the adoption naming `ledger_version`.

```
generate_scenarios_start(
    variables_upload_id=<$VARS_ID echoed by the stash above>,  # STASH_FAILED → variables_yaml=<text> inline instead
    prd_upload_id=<$PRD_ID echoed by the stash above, or None>,  # STASH_FAILED → prd_markdown=<text or None>
    plurai_config_upload_id=<$CONFIG_ID echoed by the stash above>,  # STASH_FAILED → plurai_config_yaml=<text> inline instead.
                                                                    # This is what DISPATCHES the shape (see Step 4)
    data_profile_upload_id=<$PROFILE_ID echoed by the stash above>,  # ABSENT → omit both this and data_profile_markdown;
                                                                    # STASH_FAILED → data_profile_markdown=<text>
    scenario_state_schema_yaml=<text or None>,
    inputs_schema_yaml=<text or None>,   # written by /extract-scenario-variables when simulate.py's hooks are Pydantic-typed;
    outputs_schema_yaml=<text or None>,  # they pin exact field names/shapes so the backend doesn't re-infer them from prose (refine forwards them too)
    count=<int>,
    interest=<str or None>,
    steering_demands=<list[dict] or None>,  # the CHECKABLE half of the same steer (see below); omit when nothing you asked for is mechanical
    random_seed=<int or None>,
    document_generation=<bool>,      # True iff .document_fields exists or the schema declares a generated_document field (see below)
    document_fields=<list[dict] or None>,  # the universal document trigger from plurai_sim/.document_fields (see below)
    doc_examples_set_id=<str or None>,     # THIS env's doc set (env manifest doc_examples_set_id, else plurai_sim/.doc_examples_set_id); None if absent
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
    scenario_bundle_id=<str or None>,      # set_id from plurai_sim/.scenario_bundle_id (see below); None if absent
    additional_bundle_id=<str or None>,    # the merged REGRESSIONS + TRACE SCENARIOS bundle, authored BESIDE the walk into the same set (see below); None on every batch that is not the deliverable
    augmentation_level=<int>,              # from plurai_sim/.scenario_bundle_augmentation (default 0); only meaningful with a bundle (see below)
    artifact_feedback=<str or None>,       # free-text feedback on a PRIOR batch's artifacts/documents (see below); None on a first pass
    intervention_point=False,
    # NO `shape=`: the config above declares it and the backend dispatches on the declaration.
    # Passing one that contradicts the bench is refused; passing one that agrees says nothing new.
    knowledge_bases=<list[dict] or None>, # the agent's knowledge base(s) (see below)
    knowledge_turns=<int>,           # optional; CAP on knowledge-query turns per KB per scenario (default 4, and 4 is also the MAXIMUM — larger values are silently clamped to it, since the walk visits at most 4 nodes). The walk decides which scenarios carry knowledge and how many turns each — knowledge rides a minority of the batch (the set reports it as `dataset_metadata.knowledge_slots`), so this bounds, never fixes, the number of planted questions
    memory_challenge=<'elliptical' or 'off'>,  # REQUIRED — always pass explicitly; omitted, the backend defaults BY SHAPE ('dialog' → 'elliptical', 'pipeline' → 'off'), so the value is inherited instead of stated (see below)
    happy_path_min_ratio=<float or OMIT>,  # the FLOOR on fulfilment-polarity targets in the batch, 0.0-1.0. The composer reads it and the job WARNS on a shortfall; omitted, the backend's own default decides and the recipe steers polarity not at all. Pass it when a batch must not come back all-decline: a batch composed mostly of decline-by-design walks puts the graded target behind a decline beat, where losing it costs the whole scenario
    mandated_flows=<list[str] or OMIT>,     # flow ids the batch MUST cover, by id. The way a coverage_shortfall from a previous audit is carried into the NEXT build: an uncovered flow named here is demanded rather than hoped for. Omitted, nothing forces it and the shortfall recurs
    redraws=<0, or OMIT>,            # omit on a batch you intend to DELIVER (a failed slot is redrawn once). Pass 0 on every QUALIFICATION batch — first and loop regenerations alike — which must SHOW failures rather than replace them (see below)
    previous_scenario_set_id=<id or OMIT>,  # the set this one supersedes; keeps flow ids stable across the graph rebuild so the eval set stays scoped (see below). Pass it on every regeneration for an agent that already has a set
    eval_set_id=<contents of plurai_sim/.eval_set_id, or OMIT>,  # the set that will GRADE these scenarios — pass it whenever the file exists (see below)
    env_id=<contents of $WORKDIR/.env_id, or OMIT>,  # the mock env this set is authored for; the backend drops the faults that env does not inject. Pass it whenever the marker exists and the tool list names it (see below)
    scan_contract_upload_id=<$CONTRACT_ID echoed by the stash above>,  # ABSENT/STASH_FAILED → omit (see below)
    world_reference_rows=<the list in $WORKDIR/.world_reference_rows.json, or OMIT>,  # the real rows of each declared reference table; ABSENT → omit (see below)
    ledger_version=<the pin, read above>,  # REQUIRED when the tool list names it; omit otherwise
)
```

**`eval_set_id` — the deal is BLIND without it.** The backend's slot plan seats the eval set's
steering demands into the scenarios it is about to draw, and it can only do that when it is told
which eval set will grade them. Omit it and the plan is computed against nothing: the demands are
not seated, the typed refusal (`slot plan: refused: unsatisfiable at count N; satisfiable at M`)
can never fire, and the batch is drawn without reference to what it is going to be scored on.

Read it off the marker, and pass it whenever the marker exists:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$WORKDIR/.eval_set_id" 2>/dev/null || echo ABSENT
```

`ABSENT` is a legitimate state and it is not a failure: on a first pass the evals do not exist
yet — omit the parameter and say so in the summary, because that batch was dealt without seeing
its rubrics. On every LATER call — a regeneration, a top-up, the suite's Step 3b deliverable —
the file exists and omitting it is a silent downgrade. A backend that rejects the parameter is an
older box: drop it, re-call, and say the same line.

**The FIRST draw after `/generate-evals` carries `eval_set_id` + `previous_scenario_set_id`.** A
batch dealt without the id is blind to the demands its own eval set derives, so every judge
guarded on a demand-deriving premise channel is left UNPLANTED — and an unplanted judge cannot
clear a `run_stage='final'` guard-coverage gate (`under_planted`, with
`planting_basis: demanded`) however well the agent behaves, because the gate counts plantings and
not verdicts. Regenerate the set with both ids and the demands are seated into the slot plan
while the flow ids the guards are keyed on survive; then score. A judge whose channel derives no
demand at all (`planting_basis: floor_of_one`) is NOT repaired by that draw — `eval_set_id` seats
nothing for it, so its premise is planted through a channel that does derive one
(`steering_demands`, `mandated_flows`, a knowledge turn) or the judge is re-scoped.

**`env_id` — the set plants only the faults the env injects.** When `generate_scenarios_start` names an `env_id`
parameter in your tool list, the backend reads that env's `injected_anomalies.yaml` and keeps a failure-recovery
premise or an `observed fault:` line only when its tool and fault family are ones the env injects. An env that wrote no anomalies file injects nothing, so the set plants no fault. Without
`env_id` nothing is filtered, and a scenario can plant a fault the mock world never raises.

Read it off the marker, and pass it whenever the marker exists:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat "$WORKDIR/.env_id" 2>/dev/null || echo ABSENT
```

An id printed is `env_id`, exactly as printed. `ABSENT` means this build has no env: a mockless agent, or scenarios
drawn before `/generate-mock-env` ran. In both, omit the parameter and say in the summary that the batch was not filtered
against an env's faults. On such an env-less bench, once the job is done, write its `detail.seed_probe_world`
verbatim (`null` included; absent until the backend ships it, then write nothing) to `$WORKDIR/.seed_probe_world.json`
and run `/generate-mock-env` Step 6.6's seed check on it (it carries `completed_fields`). A backend whose tool list does not name `env_id` is called without it. A refusal that the
env is not found means the marker names an env the backend no longer holds: stand the world up again
(`/simulation-generate-suite` Step 0c) rather than dropping the argument to get the call through. Every call of one
build sends the same id, regenerations and top-ups included.

**`scan_contract_upload_id` — the scan's VERIFIED conclusions, and its absence is a statement.**
`CONTRACT_ID=ABSENT` → omit the parameter and **say in the summary that the batch was generated
on UNVERIFIED premises** — absence of the file is not a clean check. On ANY other value, read
`${CLAUDE_PLUGIN_ROOT}/skills/generate-scenarios/references/generate-scenarios-call.md` before
composing the call: `STASH_FAILED`, `CONTRACT_PROCEED=false`, `CONTRACT_PROCEED=unreadable` and a
non-zero `CONTRACT_PROVISIONAL` / `CONTRACT_BLOCKED` count each demand a DIFFERENT summary line,
and `proceed: true` reads identically in all of them. Pass the id and skip the Read and you will
state this set's provenance wrongly.

**`world_reference_rows` — a declared reference table's real rows in every world.** Read the marker
`/simulation-generate-suite` Step 0 wrote (`cat "$WORKDIR/.world_reference_rows.json" 2>/dev/null || echo ABSENT`) and
pass its JSON list verbatim on every call of the build, regenerations and top-ups included. `ABSENT` means no world
type declares `reference_rows:` (or none could be stashed): omit it. A backend whose tool list does not name it is
called without it, and the batch keeps authored rows for those types; say so in one line, never a stop. Its
per-type warnings (a budget cut, a value naming no real row) are advisory.

**`redraws` — omit it on a batch you intend to DELIVER, pass 0 on one you intend to MEASURE.** A
scenario whose residual findings are advisory SHIPS with them attached, at any `redraws`. Only a
slot whose draft is HELD (an `invariant` finding: the loader would refuse it) is redrawn by
default: a *different* scenario is authored into the same graph slot, and the set comes back full.
That is right for a deliverable. It is wrong for a qualification batch, where the substitute hides
the very defect the pass was run to find. Pass `redraws=0` there: a held slot stays unshipped, its
findings and its held draft's `draft_ref` ride the job, and the job's gate findings are the output
you act on (Step 6). The summary attributes the shortfall to the configuration, not to a spent
budget, so it is not a generation failure to report.

It is also the qualification loop's largest avoidable cost: a slot's verdict only exists once its
authoring session has FINISHED, so the replacement starts near the end of the batch and roughly
doubles the round (measured: 15 min → 28-32).

**Which batches measure**: the first qualification batch and every quality-loop regeneration of it.
**Which deliver**: the final `count=7` batch, the ONE re-run of a Step-1 batch that shipped ZERO
scenarios, and a static-only suite generated straight at `count=7`.

The zero-batch carve-out is Step 1's alone, and it is a judgement call rather than a mechanism: at
`redraws=0` every slot still gets its first authoring session, so a re-run over repaired inputs can
ship without any redraw at all — and where the zero WAS deterministic (a declared input impasse) the
backend refuses the redraw regardless of budget, so the carve-out buys nothing there either. It
exists because a loop with no scenarios cannot start; Step 3 always has the previous batch to judge,
so its regenerations measure like every other qualification batch.

**An INPUT edit is not a licence to re-measure from scratch.** Fixing what a finding names is
right, and the repair is re-measured by the NEXT batch in the loop — never by re-running the first
qualification pass over changed inputs. A finding names an input defect, and fixing it does not
re-license another exposure batch, which would report findings of its own.

`redraws` governs CONTENT verdicts only. A TRANSPORT-level interruption — a dropped stream, a
transient 429 — still buys its one replacement at `redraws=0`, so the slots refused under it are
short because of their scenarios. Not a blanket claim about the batch: a spent quota or an
exhausted turn budget is refused separately and says so, so read the whole warning list before
concluding the box was healthy.

**`previous_scenario_set_id` — pass it on EVERY regeneration for an agent that already has a
scenario set.** Read it from `$WORKDIR/.scenario_set_id`, which the previous generation wrote:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; cat "$WORKDIR/.scenario_set_id" 2>/dev/null
```

A flow's id is a slug of the name the extraction model chooses, so rebuilding the dimension graph
RENAMES flows that did not change — few ids survive a rebuild even when the flow set itself is unchanged
(`request_refund_flow` → `refund_request_flow` and so on). Every eval policy scoped by `target_flows` / `current_elements` is keyed on those ids,
so a rebuild silently unscopes the eval set and costs a relink plus a whole edit round spent
re-pointing guards at new names for the same behaviour. Naming the predecessor lets the rebuild
keep the ids it can.

Pass it even when `adopt_from_scenario_set_id` is refused — *especially* then, since a refusal
means the inputs moved and the graph is being rebuilt. The two are unrelated: adoption reuses
SCENARIOS and demands byte-identical inputs; this reuses only the vocabulary. It costs no extra
call, does nothing on a graph cache hit, and degrades to fresh ids with a warning if the
predecessor cannot be read. Omit it only on the very first batch for an agent.

It inherits NO inputs, and the backend holds the call to that: a call naming it that drops an input document an
earlier set of the chain was built from is refused (`scenario.input_doc_dropped`), with the recipe first and one line
per document. Re-send each one from the workdir and call again. Retire one on purpose (a corpus deleted) only by the
overrule the refusal gives, with the reason this build no longer needs it. A scenario bundle or world reference rows
left out of a later batch is not refused: the job carries one warning (`scenario.input_doc_omitted`) and the chain
stops expecting it.

**Write `<plurai_dir>/.scenario_job_id` the moment the call RETURNS — before you poll.**
`generate_scenarios_start` bills for the work as it runs, but `.scenario_set_id` is written
only on COMPLETION, so between start and finish the job exists nowhere on disk. Observed
live: the backend stopped responding mid-generation and a 7-scenario batch that had already
been paid for was recoverable only from the conversation — had the session ended, the id was
gone. Deliberately a SIDECAR, not an early `.scenario_set_id`: a set id on disk means "a
usable completed set" to `/simulation-run`, `list-scenarios` and `generate-mock-env`, and
writing one for an unfinished job would break that for all of them.

The value is the job's returned `id` (the same value that becomes `scenario_set_id`), and
`<plurai_dir>` is the directory resolved above — NOT a hardcoded `plurai_sim/`, which is
wrong in exactly the nested / named-env layouts that resolution exists for:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $PLURAI_DIR — re-source in THIS block
printf '%s\n' "<returned id>" > "$PLURAI_DIR/.scenario_job_id"
```

The repo-root managed block (`/simulation-scan-agent` Step 5) already ignores it — it is a
crash-recovery sidecar, not project state. If the repo's block is missing or its BEGIN line
carries an older rev, re-run `/simulation-scan-agent` Step 5. On success, write `.scenario_set_id` as usual and
DELETE the sidecar — a stale one next to a completed set is misleading. If it is still present at the START of a later run, that is an interrupted job:
call `job_status(id=<id>)` on it before generating anything new, and adopt the result if it
finished.

**Conversation-memory probe (`memory_challenge`).** A dialog agent's most basic
multi-turn property is whether it *carries context across turns* — so ALWAYS pass
this parameter explicitly (**omitted, the backend defaults BY SHAPE** — `'dialog'` →
`'elliptical'`, `'pipeline'` → `'off'` — so an omitted value is
INHERITED, never stated: the batch's memory posture is then decided by a backend default
you did not read, and the deliberate opt-out below cannot be told apart from a forgotten
argument). `memory_challenge='elliptical'` makes the backend rewrite one later
turn into a memory-dependent **elliptical** follow-up: it omits the anchor established
earlier and refers to it obliquely ("is the cheaper one still available?", "and the one
with the balcony?"), so the only way to answer is to RECALL the earlier turn. `/generate-evals` emits
the matching `conversation_memory_judge` ONLY when the linked scenario set actually plants
probes — a set whose probes were all withdrawn ships no STRUCTURAL memory judge
(deliberately: a judge with no anchor passes every run while claiming to measure memory;
one may still be authored against concrete recall expectations the scenario targets
themselves state). After eval-gen, the eval job's `generation_trace.md` says why memory is
or is not covered — read that, not the coverage table (memory probes are complications,
never scenario-target rows). Without the probe, every turn is self-contained
(`memory_dependent: false`) and
memory gets exercised only incidentally, which is the gap this closes. The probe is one
elliptical turn layered onto an existing flow (memory is a cross-cutting property, **not**
a standalone flow), and it composes with knowledge scenarios — on the knowledge path the
planner makes a later same-anchor question elliptical instead. That knowledge route is
conditional, by design: it needs a plan of **at least 3 planted turns** (fewer cannot host an
anchor/probe pair), and the anchor and the probe must be planted **within a couple of user
turns of each other** (a probe delivered many improvised turns after its anchor is not a
memory test — a memoryless agent failing it would not be a real failure). A scenario that
meets neither condition routes the probe to the generic complication when it has a functional
flow to weave it into, and otherwise ships without one. So expect the probe on a SUBSET of
the set — the backend's set-level memory census reports what actually shipped, and that is
the number to read, not the request.

**Pick the value by the DECLARED shape you read in Step 4, not by guesswork:**
- declared `dialog`, or nothing declared → pass `memory_challenge='elliptical'`. Multi-turn, so
  memory is in scope.
- declared `pipeline` → pass `memory_challenge='off'`. A one-shot pipeline has no turns to
  recall across, so the probe is meaningless.
- **Opt-out:** pass `'off'` for a dialog agent only if the user explicitly says memory is
  out of scope (e.g. a stateless single-turn bot). A stateless agent that *fails* the
  elliptical turn is a real finding, not a false alarm — don't silence it pre-emptively.
- **Expect a shape warning on entity-less agents, and don't fight it:** for an agent with
  no entity schemas and no tool output_fields the probe has no
  world-state referent — the probe stays ON (it can anchor on a fact the user states in
  conversation), and any per-scenario probe the domain truly cannot support is withdrawn
  in-loop. That is expected, not a reason to re-run with `'off'`.

**Record a finished corpus build first.** `/build-rag-corpus` returns once its delivery is accepted, so the corpus it
delivered may not be recorded yet, and the markers below name only what `grounding_corpus.py record` has written. Run
this read block after the pin is read; `PENDING=0` means go on:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "JOB=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" pending --workdir "$WORKDIR"
```

Otherwise wait for the job in the foreground with `poll_job.sh` (the recipe in
`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`; exit 14: wait again in this turn; exit 13: the job failed and nothing is recorded; on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)), then
record the finished builds. The record is idempotent, so the suite's Step 0b and any other generator share it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "CORPUS=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.corpus_status.json.tmp" \
  && mv "$ART/.corpus_status.json.tmp" "$ART/.corpus_status.json" || { rm -f "$ART/.corpus_status.json.tmp"; echo "CORPUS=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" record --workdir "$WORKDIR" --status "$ART/.corpus_status.json"; echo "corpus record exit=$?"
```

Exit 0: go on. Exit 1: a build failed, came out degenerate or belongs to another machine; it is not recorded, so this
skill goes on without that base (a degenerate one is a smaller
`--chunk-size` and a new `/build-rag-corpus`); a failed build: run the contact hand-off (`stop_message.py contact --done`). Exit 4: a build is still running; wait again and rerun. Exit 3: stop and run the contact hand-off (`stop_message.py contact --done`). Never pass a corpus id from a build that has not finished: only the ids `record` wrote are passed,
and when the grounding job is not settled this skill's own wait-and-pin rule decides whether it runs at all.

**Knowledge-challenge scenarios (optional).** If the active env manifest
(`plurai_sim/envs/<active>/plurai.env.yaml`) declares a `knowledge_bases` list, or
`plurai_sim/.knowledge_bases` or `plurai_sim/.corpus_id` exists, READ
`${CLAUDE_PLUGIN_ROOT}/skills/generate-scenarios/references/knowledge-bases.md` before composing
`knowledge_bases=` — it carries the answerability precondition, the resolution order, which
entries are skipped, the `ground_from` branch and how each `label` is derived. Skip the Read and
you ship a set with ZERO knowledge scenarios, or — when the schema pins `ground_from` and no corpus
is attached — a set whose grounded fields were authored from no source (each one a gate finding on
the job). No corpus anywhere:
omit `knowledge_bases` (or pass `None`); the feature is inert and nothing is lost. Knowledge
challenges require `shape='dialog'`.

**Document generation (optional).** For a document-extraction agent, `/simulation-setup` writes
`plurai_sim/.document_fields` — a JSON list `[{field, format, brief}]` naming the field(s) whose
content should be an authored document (e.g. an invoice agent's `raw_text`). Pass the parsed list
as `document_fields=` and set `document_generation=True` whenever it is present. This is the
**UNIVERSAL** trigger — it needs no `scenario_state_schema`, so it works for a schema-less
**pipeline** agent (which SKIPS `/generate-mock-env` entirely, so a schema-annotation path
never reaches it). The backend authors a realistic document per scenario and
folds its text into that field instead of leaving it to generic LLM filler; the scenario prose
gives per-scenario variety for free. A stateful agent whose schema env-gen annotated with a
`generated_document` block is also supported (the backend unions both sources), so OR in a
substring check on the byte-for-byte schema text so either channel turns it on:
`document_generation = bool(document_fields) or 'generated_document' in (scenario_state_schema_yaml or '')`.
With neither, it stays `False` (the default — byte-identical). `document_fields` carries only field
names + a short brief (no files), like `knowledge_bases`. Requires the backend's
document-generation support.

**Doc example families (optional) — PER ENV.** If the customer ran `/upload-doc-examples`, the
`set_id` is recorded on the **active env's manifest** (`plurai.env.yaml` → `doc_examples_set_id`)
so each env has its OWN doc set. Resolve it: if `plurai_sim/envs/` exists, run
`env_manifest.py read plurai_sim/envs/$(cat plurai_sim/.active-env)` (it prints the whole manifest as
JSON) and pluck the `doc_examples_set_id` field; otherwise (bare mode) fall back to the one-line
`plurai_sim/.doc_examples_set_id`. Pass it as `doc_examples_set_id=<set_id>`; if neither is
present, pass `doc_examples_set_id=None`. When present, the backend feeds the uploaded family files to
document generation as the `examples_dir` — generated documents are templated from those
real samples (formatting, vocabulary, field layout) rather than being authored from the brief
alone. When absent, generation falls back to brief-only output, which is the unchanged default.
`doc_examples_set_id` is a plain id reference (no file content), exactly like `knowledge_bases`.

**Env workspace scope (`workspace_id`).** The trace brief is stored PER WORKSPACE on the backend,
so scenario-gen must read THIS bench's brief (not another agent's). Pass the stdout of
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"` —
`<bench_id>` in bare mode, `<bench_id>.<alias>` in a named env — on every call. Never omit it: an
omitted id is the box-wide `default` slot any other bench can read or overwrite.

`doc_examples_set_id` supplies the few-shot TEMPLATE (the real sample files); `artifact_feedback`
(Step 3) is the complementary free-text CORRECTION layered on top of it ("closer to those examples",
"drop the summary section", "keep the header block"). Use both together when the user has uploaded
examples AND has reacted to a prior batch's documents — the examples set the baseline, the feedback
nudges the next authoring pass toward what the user actually wanted.

**THE BUNDLE PATH IS CONFIRMED, NEVER AMBIENT — read this BEFORE you call.**
Before composing the call, read `plurai_sim/.scenario_bundle_id`. If it exists you MUST read
`${CLAUDE_PLUGIN_ROOT}/skills/generate-scenarios/references/bundle-path.md` before calling — the
dimension-graph walk is bypassed, the marker has no TTL and no scope, and the path must be
CONFIRMED, never ambient. If you skip that Read you will silently import an old bundle into an
unrelated build, with `count`, `interest` and dimension diversity inert and nobody told.
If the marker is ABSENT there is no bundle: pass `scenario_bundle_id=None`, leave
`augmentation_level=0`, and read nothing.

**Regressions and trace scenarios — the ADDITIONAL bundle (`additional_bundle_id`), on a DELIVERABLE batch only.**
A different parameter with a different shape: `scenario_bundle_id` builds the whole set from its
folders and never walks the graph, while `additional_bundle_id` authors its folders **beside** a
full walk, in the same job, into the same set. The walk still deals its entire `count` and every
draw it makes is byte-identical to the one it would make with no bundle; the set then holds
`count` walk scenarios **plus one per folder**. `augmentation_level` does not apply — a folder
yields exactly one scenario. It is mutually exclusive with `scenario_bundle_id` and with
`adopt_from_scenario_set_id`, and the backend refuses the call rather than picking one.

What goes in it here is **two directories merged into one bundle** — `plurai_sim/regressions/`,
one folder per confirmed agent issue, written by `/record-regression`, and
`plurai_sim/trace_scenarios/`, one folder per production conversation cut before a trace failure.
Staging and uploading them is the SUITE BUILD's job, not this skill's: `/simulation-generate-suite`
stages both with `final_bundle.py stage` — at its final batch, or at Step 1 of a static-only suite
— ingests the staged directory as one bundle and hands you the finalized id. **Pass whatever id you
were handed and nothing else.** Do not go looking for either directory, do not upload one
yourself, and pass `None` when you were handed no id — which is every qualification batch and
every quality-loop regeneration. The reason is not tidiness: the qualification loop exists to tune
the bench, and a regression or a trace scenario is a fixed reproducer of a conversation the loop
must never be invited to improve.

**Each folder's scenario is named by the folder** (`regression_refund_timing_invented`,
`trace_refund_window_invented`, not `scenario_04_…`), so it means the same scenario in the next build and the suite's membership
check finds it by name. No eval is authored for it: the `grades_scenario_target` judges grade the
target its authoring session derives from the folder's `ROLES.md`. The backend refuses, before
authoring anything, a folder whose slug is empty, duplicates another folder's, or looks like the
walk's own `scenario_NN_` shape. A folder carrying `resume_pin.json` continues its recorded
conversation; a folder with no pin may carry `first_talk.txt` and opens on that text verbatim; a
folder offering **both** is refused. Every scenario records `origin.bundle_folder` in its `scenario.yaml`, and the set's
`dataset_metadata.yaml` lists the shipped ones under `bundle_folders` — see Step 6 for the key
that matters more, `bundle_folders_attempted`.

**A folder that cannot be authored does NOT fail the walk**, and that is exactly why Step 6 has to
read the result: the set ships green with a regression silently missing from it.

The response is a generic **Job envelope** (the backend reports every job
kind through this single shape now):

```yaml
id: <uuid>                 # this IS the scenario_set_id
status: queued             # queued | running | done | failed
phase: queued              # free-form sub-state (see Step 5)
started_at: <float>
completed_at: null
error: null
detail:                    # kind-specific typed payload
  scenario_set_id: <uuid>  # == id
  dataset_name: <str>
  scenarios_total: <int>
```

**Save `id` (the scenario_set_id).** Every poll + the eventual end call need
it, and you'll write it to `plurai_sim/.scenario_set_id` on completion.
(`detail.scenario_set_id` mirrors `id`.)

**Environment manifest (if this repo uses named envs).** If `plurai_sim/envs/`
exists, also record the set id into the active env's manifest so a later env switch
can verify it later:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
python "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" write "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" --scenario-set-id <scenario_set_id>
```

Skip if there's no `envs/`. See the `manage-envs` skill.

If the call raises `ValueError("scenario_storage_root is not configured...")`,
the backend doesn't have scenario-gen enabled — not fixable from the customer side: run the contact hand-off (`stop_message.py contact --done`). Don't retry.

If the call raises a `ValueError("count N exceeds scenario_job_max_count=...")`,
the requested count is over the backend's cap. Lower count and retry silently.

On the bundle path, two more raises are possible: a `ValueError` that the bundle is
**not found or not finalized** means `.scenario_bundle_id` is stale/unfinalized — re-run
`/ground-scenarios` to upload + finalize a fresh bundle. A `ValueError` that the fan-out
(`folders × (1 + augmentation_level)`) **exceeds `scenario_job_max_count`** means lower
`augmentation_level` or split the bundle into fewer folders; if the user genuinely needs the
larger fan-out, tell them to ask for a cap raise at support@plurai.ai or on Discord
(https://discord.gg/bg34gh76uJ), as in the README's "Getting help". A cap raise is a request, not an
issue, so this is not the contact hand-off.

<!-- BEGIN shared:ledger-version-refusals-scenarios owner -->
**`ledger_version` refusals.** A message that starts `ledger_version refused (<cause>)` is the backend's answer to
the pin, raised before anything ran. It is not a content problem and it is never retried without the argument: a
set built without the grounded facts is an ungrounded run, and the backend does not allow one.

| cause | what it means | do |
|---|---|---|
| `missing` | the call carried no version: this plugin is older than the backend | update the plugin (the user's step; say so in product terms), then start the build again. Never retry without the argument |
| `no_workspace` | the call named no `workspace_id` | add the bench workspace id (the same call block) and call once more |
| `malformed` | the value is not a version id | re-read the pin from the job as above; never type or shorten an id |
| `grounding_off` | this backend keeps no grounding ledger | not fixable from here: run the contact hand-off (`stop_message.py contact --done`) |
| `unknown` | the backend does not hold that version | re-read the pin from the job as above; the same answer twice: run the contact hand-off (`stop_message.py contact --done`) |
| `other_workspace` | the version belongs to another workspace | the handle on this machine points at another bench: re-run `/simulation-scan-agent`, which records this bench's own |
| `unsettled` | the version is still being made, or a finding holds it | wait for the job as above, then call once more with its `detail.ledger_version` |
<!-- END shared:ledger-version-refusals-scenarios -->

### Step 5: Poll for progress

Loop until `status` is `done` or `failed`, waiting between reads. The job is minute-scale
(a full run is 15-40 minutes), so the cadence is minutes, not seconds — a tighter loop buys
nothing but handler budget, and the harness cannot deliver it anyway.

**One blocking call per wait** — the sleep lives inside the script, so the wait cannot overlap
the read:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <scenario_set_id>
```

**Call this with a Bash `timeout` of `600000`** — the harness default is 120 000 ms, which kills the script 420 s inside its own budget and looks exactly like a wedged backend.

**Poll in the FOREGROUND, and keep the turn open until the job is terminal.** Never run `poll_job.sh` or `wait.sh` in the background, and never end a turn on "I'll check back" while the job is `queued` or `running`: a headless `claude -p` session exits when the turn ends, the background poll dies with it, and the user gets no result. On exit 2 re-invoke in the same turn, until the job is `done` or `failed`.

It returns when the job is terminal, printing one line per status/phase change. Then read the
job ONCE through MCP for the full `detail` envelope and render:

```
job_status(id=scenario_set_id)   # → the detail envelope below; render the terminal result
```

Exit codes: `0` done · `13` failed · `15` **an id the backend does not know** (re-check / re-launch
the id — the backend is fine) · `14` budget spent but backend healthy (**slow, not wedged** —
re-invoke) · `12` this backend has no `GET /jobs/{id}` — a 404 with **no job envelope** (fall back to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110` then `job_status(id=…)`, waits kept SERIAL. Run `wait.sh` in the FOREGROUND — its sleep is internal, so the harness's block on a bare foreground `sleep` does not apply and the tool timeout bounds it. It takes a DURATION and nothing else: never hand-roll a loop that exits on a job status (the backend reports success as `done`, and a loop watching for `complete`/`completed` never exits)) · `77` bad token · `69` job state unknown: backend not responding, or up with the job route faulting (the script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) · `7` Plurai refuses this version of the plugin or its SDK: the plugin's update text was printed; say it verbatim and alone, then stop. Full recipe + rationale: `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`.

`job_status` returns the **Job envelope**. The lifecycle
`status` is one of `queued | running | done | failed`; the scenario
sub-state (the phase names) rides the free-form `phase` field, and the
scenario-specific counters live under `detail`:

```yaml
id: <uuid>                              # the scenario_set_id
status: queued | running | done | failed
phase: queued | building_graph | seeding | rendering | evolving | done | failed
started_at: <float>
completed_at: <float | null>
error: <str | null>                     # only set on failed
detail:
  scenario_set_id: <uuid>
  scenarios_completed: <int>
  scenarios_total: <int>
  latest_iteration_message: <str | null>  # only set during evolving
```

Read the phase from `phase` (not `status`) for the progress label, and the
counters from `detail.scenarios_completed` / `detail.scenarios_total`.

**Render to the user on every status change**, not every poll. One
line per transition, e.g.:

```
[15:02:11] queued — backend has the job, waiting for an asyncio slot.
[15:02:14] building_graph — extracting flows + scoring + edges.
[15:02:38] seeding — sampling 3 walks (stratified).
[15:02:39] rendering — rendering 3 seed prose blocks.
[15:02:55] evolving — running per-scenario evolution loop (3 scenarios).
[15:07:09] done — 3/3 scenarios complete.
```

During ``evolving`` the ``phase`` stays at ``evolving`` (``status`` stays
``running``) from when the batch starts until it finishes, and
``detail.scenarios_completed`` ticks up ONE PER SCENARIO as each finishes.
Three things about that counter are normal and are not stalls:

- **It sits at 0 until the FIRST scenario completes** — 7-20 minutes, longer
  when the batch runs at or below its concurrency limit, because nothing has
  finished yet. ``latest_iteration_message`` moves in that window.
- **It FREEZES during redraw rounds.** A redraw re-authors a failed slot and
  passes no set-level count, deliberately: a 2-scenario redraw batch must not
  walk the set-level number backwards.
- **It can go DOWN at the end.** Mid-run ticks count completions including ones
  that later fail their audit; the terminal value is reconciled to what actually
  shipped.

Reliable wall-time guidance during ``evolving``:

- Expect **3-10 min per scenario**. The backend may run scenarios
  SEQUENTIALLY or at low parallelism, where
  ~9 min/scenario is healthy; six scenarios → ~20-60 min is the normal range,
  higher under rate-limit pressure. ``scenarios_completed`` sits at 0 until the
  first scenario finishes and freezes again through redraw rounds (above), so
  the counter moving at all is the health signal, not its rate.
- Render status updates **passively**, quoting the SERVER's number: ``[15:09:11]
  still evolving (elapsed_seconds=738 → ~12 min) — typical for 6-scenario
  batches.`` Do NOT interrupt the
  user with ``"How do you want to proceed?"`` or ``"Backend LLM is
  stalling"`` framings — both are anti-patterns: the stall diagnosis
  isn't grounded in reliable telemetry, and the user can interrupt with
  Ctrl-C if they want to abort.
- **NEVER compute elapsed time yourself — read ``elapsed_seconds`` off the
  envelope.** You have no clock: you cannot observe time passing, so any elapsed
  figure you derive is really a sum of the waits you INTENDED. If those waits
  overlapped (backgrounded) or never happened, the total is fiction. Observed
  live: a poll loop summed its own sleeps to ~50 minutes for a job that was 15
  minutes old and escalated a healthy run to the team. The envelope carries
  ``elapsed_seconds`` (server-computed, ticking) and ``seconds_since_progress``
  (time since the job last actually MOVED) — use those two numbers and nothing
  else. If an older backend omits them, shell out to ``date`` and subtract
  ``started_at``; never accumulate.
- **One wait per poll — the waits must not overlap.** A poll iteration waits, THEN
  reads. Launching several sleeps concurrently and reading immediately is what
  produces the phantom elapsed above: the waits overlap, so wall-clock time never
  passes even though the durations add up.
  - **`poll_job.sh` is what guarantees this** — its sleep is foreground and internal, so one
    invocation is one wait, start to finish. Prefer it; you only hand-roll the wait on the
    exit-12 fallback path, where the waits being *serial* is then your responsibility: fall
    back to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110`
    then `job_status(id=…)`, waits kept SERIAL — never a bare foreground `sleep`, and never a
    loop of your own: `wait.sh` takes a duration and nothing else, so it cannot be turned into
    one that waits on a job status and never exits.
- **Escalate on STALL, not on duration.** ``elapsed_seconds`` alone never
  justifies escalating — a big batch is legitimately slow. Escalate only when the
  job has stopped MOVING: ``seconds_since_progress`` exceeds ~15 min AND
  ``detail.scenarios_completed`` + ``detail.latest_iteration_message`` are
  unchanged across two consecutive polls at least 5 minutes apart.
  **``poll_job.sh`` honours this: it prints NO stall NOTE while ``phase == 'evolving'``
  (one notice that the clock is not a stall signal there), and uses 30 min for env-gen's
  ``generating``/``verifying``, 15 min elsewhere.**
  **``seconds_since_progress`` is NOT a stall signal during ``evolving``.** That
  phase publishes no per-scenario progress by design (see above), so the counter
  ticks up through a perfectly healthy batch — measured at 620s on a healthy
  3-scenario run, and a 7-scenario batch will pass ~15 min without anything being
  wrong. While ``phase == 'evolving'``, the concrete rule is (compute
  the threshold ONCE at launch so no judgment call is needed mid-flow):
  ``waves = ceil(batch_count / 7)``; ``evolving_deadline = 90 min × waves``
  (90 min for ANY batch up to 7, 180 min for 8-14); escalate only when the
  server's ``elapsed_seconds`` exceeds it. **Scenarios run CONCURRENTLY** — the
  backend authors up to 7 of them at once — so wall time scales with WAVES, not
  with scenario count. A count-linear budget would let a genuinely hung batch sit
  undetected. 90 min/wave leaves margin over a healthy one-wave run (wall time
  is wave-bound, not count-bound). At poll_job.sh's 540s budget that is ~6 exit-2 re-invocations
  for a healthy wave and ~11-12 before the deadline — repeated exit-2 returns
  are the normal shape of a long wave, not a stall signal.
  If the server is configured BELOW 7 concurrent slots this under-counts waves
  and the deadline tightens — treat a fire on a visibly-progressing job as that,
  not as a stall. The
  moving-counter test applies to the phases
  that actually move it. A stalled job
  proves itself; a slow one does not have to. When it genuinely fires: run the contact hand-off (`stop_message.py contact --done`). **Do not auto-cancel.**
- **A `job_status` TIMEOUT does not tell you which failure it is.** A dead backend and a
  heavy call return the same "operation timed out", so classifying transient-vs-severe
  off that string alone is guessing. Probe the server directly before you decide, in ONE call
  (the shell resets between tool calls, so a `$SERVER_URL` from an earlier one is empty):

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
  PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require "backend probe")" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed
  if [ -n "$PLURAI_BIN" ]; then
    "$PLURAI_BIN" backend probe --timeout 10
  else
    echo "PROBE UNAVAILABLE: no plurai runner with backend probe"
  fi
  ```

  It prints exactly the HTTP code of `GET /healthz`, `000` for no answer. **Only a 2xx means the
  backend is alive** and the job is merely slow — keep polling. Any other code — a ``401``, a
  ``404``, a ``502`` — proves only that something answered (an auth layer, another service on the
  port, a proxy with no upstream), not that the backend is healthy: treat it as DOWN/UNHEALTHY
  and name the code. `000`, repeated once, means the BACKEND is down, not the job. Either way say so plainly, and give the user the ``scenario_set_id`` / job id so the work can be adopted when it returns rather than regenerated from scratch. `PROBE UNAVAILABLE` means there is no Python
  runner here (SDK not installed yet) or it predates `backend probe`:
  liveness is NOT VERIFIABLE from this shell. Say that, never call the backend up or down on it,
  and read `job_status` once more; a second timeout is a blocker to report with the job id.
  The block exits 1 on `client_pin: REFUSED` (the client pinned for this workdir refuses this
  runner) or on `refusing unauthenticated remote calls` (no token for a remote backend): stop and
  tell the user, in the message's own words.

**A TYPED refusal is not a transient failure, and it is never retried.** When the failed
envelope carries a typed terminal reason naming a class of INPUT defect, the backend has already
decided the call cannot succeed
as sent — a retry re-sends the same inputs and fails identically, only later. Read the named
FIELD and the named REMEDIATION off the typed channel and act on them: apply the fix yourself
when it is mechanical (a field to correct, a marker to write, a parameter to drop) and say in
one product sentence what you changed, or state the named field and what it needs in plain terms
and ask for that one thing. Never a blind retry, and never a silent one. A `refused_inputs`
with the provider-refusal shape (Error posture) is not an input defect: answer with the refusal
message only. The message-matching below is the FALLBACK
for a backend that ships no typed reason — read it as what it is, a classifier over prose, and
prefer the typed field wherever it exists.

**Retry a failed job only as the retry rule (Error posture) allows.** Anything else goes to the
user with the `scenario_set_id` and the reason in plain terms.

**`terminal_reason` is TOP-LEVEL on the Job envelope, beside `status` and `error` — it is NOT a
`detail` field.** Read `job.terminal_reason`. No Job kind carries that key under `detail`, so
looking for it there finds nothing and silently demotes every typed refusal to the prose
classifier below. Verified live: a refused evals job returned `terminal_reason='refused_inputs'`
at the top level, with 24 `detail` keys and no such key among them.

If `status='failed'` with no typed reason, read the `error` field YOURSELF to classify — never
show it to the user. Modes and what YOU do:
  - `count N exceeds scenario_job_max_count` — caught upstream in
    Step 4; should never reach here. If it does, lower count and
    re-run silently.
  - `scenario generation failed in 'building_graph' phase` — usually a
    PRD parsing problem. Read the message yourself; if it points at
    something in the PRD the user must change, describe the needed fix
    in plain product terms and offer to re-run afterwards. Don't paste
    the message. A provider timeout or 5xx here (`504 Provider timed
    out`) is final: the backend already retried it inside the job, and
    nothing here fixes it. Fix per this list; if nothing here fixes it, or the re-run after the fix fails again, run the contact hand-off (`stop_message.py contact --done`).
  - `all N scenarios failed during evolution` — a batch-wide stall or
    rate limit, which clears between runs: retry once, silently. If that
    fails too, nothing here fixes it: run the contact hand-off
    (`stop_message.py contact --done`).

### Step 6: Save the scenario_set_id

On `status='done'`, the final **Job envelope** is the last response from
`job_status(id=scenario_set_id)` — read the final metadata from `detail`
(file contents are NOT included — fetch individual scenarios via
`get_scenario(scenario_set_id, scenario_name)`; prefer that over a whole-bundle
`fetch_artifact(scenario_set_id)`, which returns every file and can exceed
the MCP result ceiling. If you must bulk-fetch, check
`get_artifact_meta`'s `files[]` manifest (relpath + size) first and narrow
with `fetch_artifact(scenario_set_id, paths=[...])`. A file over the per-file wire
cap never fails the fetch: its body is a placeholder and `withheld_files` names it
as `{path, bytes}` — the run reads it on the backend, so it is not missing, but
never write the placeholder as that file. An older backend has no `withheld_files`;
there the placeholder body itself is the signal):

```yaml
id: <uuid>                 # the scenario_set_id
status: done
phase: done
started_at: <float>
completed_at: <float>      # derive duration as completed_at - started_at
error: null
detail:
  scenario_set_id: <uuid>
  scenario_names: [scenario_01, ...]
  dataset_metadata: {coverage_report: {...}, ...}
  dimension_graph: {flows: [...], edges: [...]}
  coverage: {count, total_flows, covered, uncovered}
```

**Decide on every gate finding yourself.** They ride `detail.gate_authority.findings` (the set's
copy is `dataset_metadata.gate_findings`): a shipped scenario's findings are what its gates saw
and shipped over — a document kept with a validation defect, a document that never arrived, a
field grounded from no source, an audit flag demoted — and a slot that did not ship carries its
own findings plus the held draft's `draft_ref`. Dispatch each per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`, alone: repair it (an input edit, `/edit-scenarios`
on a ground-truth field, or regenerating the slot — read its held draft first, `get_scenario` with
`draft=True`, to see what broke), or accept it. Never ask the user about a finding, and never render a finding or an overrule into
anything the customer reads.

**LOG THE VERDICT for each demoted finding, before the qualification run.** Reading it was
already the rule and the rule did not hold: a scenario shipped carrying an
`audit_flicker_demoted · contradicts_grounding` flag the backend had raised TWICE at generation,
a later critic pass re-derived the same defect, and the round paid a full scenario regeneration
for a decision that was available before the run. A demoted flag is pre-labelled as maybe-noise
and obliges nobody, which is exactly why it needs a written decision rather than a reading.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind warning-triage \
  --verdict flag-wrong --finding "<scenario> <flag>: <one line — what you decided and why>"
```

One entry per demoted finding still present in the shipped text. `--verdict` is required
(`fixed` · `regenerated` · `flag-wrong` · `accepted` · `ruled`) and **`flag-wrong` is a complete, cheap
verdict** — these auditors are single sampled judges with a demonstrated fabrication rate, and
the backend publishes a PROVENANCE CORRECTION when it catches one quoting text that appears in no
grounding block. A `scenario.audit_overruled_by_session` finding takes this same verdict log, and `flag-wrong` on
any flag that states a seeded or served value is cheap only after that value is READ — the
`get_scenario` world or the run's own tool output — and quoted in `--finding`. This is a VERDICT
gate, not a fix gate: nothing here says regenerate. Do not
triage `input_suspects` this way — they stay behind the `seen_n >= 3` line below.

**A demoted flag on a TARGET clause the agent provably cannot observe is a ruling, not flicker.**
When the contract itself keeps the clause's fact from the agent (a step that drops the input it
would need), carry it to every later draw and log `--verdict ruled`. `contract_quote` is the PRD
sentence that states it, verbatim — the writer refuses a quote the PRD does not hold:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_target_rulings.py" --variables "$PLURAI_DIR/scenario_variables.yaml" \
    --audit - --scan-contract "$ART/scan_contract.json" --prd "$PLURAI_DIR/simulation_prd.md" \
    --source "warning triage, set <scenario_set_id>" <<'RULING'
[{"verdict": "UNOBSERVABLE", "defect_surface": "target_clause", "item": "<scenario>", "policy": "<flag>", "clause": "<the target clause>", "ruling": "unobservable", "contract_quote": "<the PRD sentence that keeps it from the agent>", "flow_id": "<flow>"}]
RULING
```

It is an input edit: log it `--kind input-edit --scope scenario` too. **On the FINAL draw** no later
draw reads the ruling, so also correct the shipped clause now: `/edit-scenarios` →
`edit_scenario_gt` on that scenario's `scenario_target`, removing the clause, with the PRD sentence as
`cited_source`.

**`input_suspects` is JUDGMENT-CLASS, and the threshold is the point.** These are the backend's
low-precision guesses about the inputs — so the FULL list stays in the job's
`input_suspects` and only a suspect whose `seen_n` is **3 or more** reaches anything the
user reads. `seen_n` counts independent scenario draws that hit the same wall in THIS run, so a
high count means the inputs blocked several attempts, not that one input was submitted twice.
Below the line, act on it if you agree with it, and say nothing.

**Then read `detail.gate_authority.findings` yourself — a finding whose
`evidence.family` is `grounding_sources_conflict` needs a reaction, not a relay.** It means
the PRD and the knowledge corpus state a fact incompatibly; its `evidence.line` quotes both sides. The set is USABLE (the
backend did not fail the scenario — it follows the corpus), but every later
generation reads the same conflicted inputs and eval generation freezes PRD
lists into judge prompts, so left alone the conflict compounds. Follow
`${CLAUDE_PLUGIN_ROOT}/skills/generate-scenarios/references/prd-corpus-reconciliation.md`:
read the corpus chunk behind the quote, REWRITE whichever document is wrong
(never annotate the PRD with rebuttals — the next run reads both sides as
facts), then regenerate once the artifact that read the wrong side. Report it
to the user in one product sentence ("one PRD claim disagreed with the docs;
corrected and noted"), never the raw warning text.

Write the id to `<plurai_dir>/.scenario_set_id` (one line, no trailing
newline). This file is the contract between `/generate-scenarios` and
`/simulation-run` — the latter reads it and passes
`--scenario-set-id <id>` to `plurai-run-simulation`.

```
<scenario_set_id>
```

**Stage it** — it names the backend set the committed inputs correspond to, so a clone
without it holds a mirror of nothing identifiable. Nothing to edit in any `.gitignore`
(the repo-root managed block already tracks this marker; if the repo's block is missing or
its BEGIN line carries an older rev, re-run `/simulation-scan-agent` Step 5):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .scenario_set_id
```

**If an ADDITIONAL bundle rode this run (`additional_bundle_id` was passed), read what shipped
before anything else — a missing regression or trace scenario is a BLOCKER, never a silent gap.**
The backend does not fail the walk over a folder it could not author, so the set completes `done`
with the folder's scenario absent and nothing in the headline says so. Two places record it, and the metadata is
the authoritative one:

- `detail.dataset_metadata.bundle_folders` — the folders that SHIPPED.
- `detail.dataset_metadata.bundle_folders_attempted` — **asked for and not delivered.** The key
  is written only when something is missing, so its presence is the whole signal.
- `detail.dataset_metadata.bundle_folders_attempted_reasons` — why each of those did not ship:
  `infra_fault` (not judged; the cause is outside the folder), `failed_validation`,
  `session_fault` or `unknown`.

When `bundle_folders_attempted` is present, **say so as a blocker in the report and name every
folder in it, with its reason.** Never report the set as covering a folder that is not in it: the
folder still sits on disk, what it reproduces still reads as covered, and the next run grades an agent against a
reproducer that was never delivered. The usual cause is that the folder's seeded history no longer
coheres with the current environment — for a trace scenario, a world that cannot return what
production returned. The remedies: re-cut a regression against this environment
(`/record-regression` on a fresh run); give a trace scenario an environment whose tools can return
what its world facts record (`/edit-env`), or re-convert it at another cut (`/trace-to-scenarios`);
or retire the folder by deleting it. Do none of that from here; report it.

**If a scenario bundle drove this run (`scenario_bundle_id` was passed), VERIFY the grounding
before scaling.** Fidelity is LLM-mediated, so a `done` run is not proof the real values landed:
for each scenario, `get_scenario(scenario_set_id, scenario_name)` → read the grounded `agent_context.<field>`
identity value (the precondition's id/code) and assert it ∈ the real data source the agent's tools read
(e.g. the record ids in the inventory fixture). On a miss, sharpen the bundle's `ROLES.md` field→value
pinning and regenerate. See `/upload-scenario-bundle` → "Step Verify".

**Then, when the build this bundle was FOR is finished, clear its markers** —
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .scenario_bundle_id .scenario_bundle_augmentation
.scenario_bundle_intent` — and say you did. A marker that outlives its build is
the hijack pointer Step 4 exists to catch, and catching it there costs a confirmation every
time; clearing it here costs one line, once. The bundle itself stays on the backend
(`list_uploads(kind="scenario_bundle")` finds it), so this loses nothing but the ambient
default. Leave the markers in place ONLY while more batches of this same build are still to
come — say so if you do. A later batch that no longer sends `scenario_bundle_id` is not refused for it: the backend
warns once (`scenario.input_doc_omitted`) and the chain stops expecting the bundle.

**Do NOT materialize scenarios locally.** The backend is the GT store.
The plugin path never writes `qual_scenarios/` to the customer's
filesystem. If the customer wants to inspect, point them at
`/list-scenarios` (which calls `get_artifact_meta` and renders a
table) or at fetching individual scenarios via the
`get_scenario(scenario_set_id, scenario_name)` MCP tool.

### Step 7: Surface the summary

Print:

Backend LLM cost figures (`total_cost_usd` etc.) are INTERNAL telemetry — never show them to the customer, in this summary or any progress line.


```
Scenario set ready: <scenario_set_id>

  3 scenarios generated in 4m18s.
  Dataset: generated_dataset

  Flow coverage:
    query_annotations_flow:        3 ⬤⬤⬤
    bulk_update_annotations_flow:  1 ⬤
    modify_label_taxonomy_flow:    1 ⬤
    optimize_prompt_accuracy_flow: 0 (uncovered)
    ...

  Next steps:
    1. Inspect scenarios (optional): /list-scenarios
    2. Run the simulation: /simulation-run

  The scenarios live on the backend as canonical GT.
  To delete this set later, ask me to delete this scenario set.
```

The flow-coverage table comes from
`detail.dataset_metadata.coverage_report` in the done Job envelope from the final `job_status` call.

**Report SHIPPED coverage, never the planned headline.** On current backends the primary
fields — `coverage.covered` / `coverage.uncovered` — already describe what the SHIPPED set
actually tests (the backend audits each shipped scenario's real `target_flows`), the walk's
intent is preserved under `coverage.planned_covered` / `planned_uncovered`, and
`realized_covered` / `realized_uncovered` are retained for wire compatibility (equal to the
primary fields). The two views diverge when scenarios fail generation or drift off their
flow — a walk that planned 7/7 can ship 5/7, and printing "100% flow coverage" from the
planned number is a silently wrong customer report. Headline the shipped figures and, when
the two differ, give the planned figure as context ("planned 7, shipped 5"). On an older
backend whose primary fields are still the plan, `realized_*` (when present) is the shipped
truth — prefer it there. `coverage_report` counts walk intent either way.

**Shipped is still not the same as VERIFIED.** The coverage audit fails OPEN: flows it
could not check (untokenizable narrative language, thin vocabulary, no spec) are counted
covered anyway, and `detail.dataset_metadata.coverage_unverified` (current backends)
carries the per-flow count of those declared-not-measured credits — with
`coverage_unjudgeable_scenarios` naming the scenarios. When non-empty, say so beside the
headline ("7/7 covered, 3 credits unverified by the audit") rather than folding it in.

## Recommended next step: spot-check the set before scaling up

Right after `/generate-scenarios` completes, BEFORE handing off to `/simulation-run` for the full batch, recommend the user spot-check the new set against the customer's actual agent. **Read `references/spot-check-and-troubleshooting.md` for the walkthrough** — inspecting the dimension graph for deliberate-failure complications (so expected `error.json` files aren't mistaken for bugs), running the spot-check by id with `plurai-run-simulation`, and categorizing what failed (deliberate rejection ✅ vs unintended crash/bad-label ❌) before paying for a full-batch run. That reference also carries the failure-mode recovery table and the glossary.

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

## Hard rules

- **Never write scenario files to the customer filesystem.** The
  backend is the canonical store; `plurai-run-simulation
  --scenario-set-id <id> --scenario-id <name>...` streams scenarios
  over MCP at run time.
- **Never read `plurai_sim/qual_scenarios/`.** If a customer has an
  old local directory from an earlier release,
  tell them to delete it and use the plugin path going
  forward.
- **Polling is DELEGATED to `poll_job.sh`** (foreground 15s interval
  internally, ≤540s of wall clock per invocation, exit 14 = re-invoke, on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) —
  one invocation is one blocking wait, so the cadence takes care of
  itself. Hand-roll `job_status` reads ONLY on its exit-12 fallback (a box
  that predates `GET /jobs/{id}`), and there at a ~1-3 min cadence with
  serial waits — Claude Code blocks foreground `sleep`, and a
  backgrounded wait plus an MCP read is a multi-second round-trip by
  itself. See Step 5 for the exact shape. What the user needs is a
  rendered update each time you DO read, not a faster loop.
- **Don't re-trigger generate_scenarios_start on an in-flight set.**
  If the customer asks to "regenerate", confirm whether they want to
  keep the existing set or replace it. To replace: call
  `delete_artifact(<old>)` first, then start a
  new one. Starting in parallel produces two competing sets and
  confuses downstream skills.
- **Never generate scenarios locally.** Don't fall back to a local
  generator from the plugin path even when the
  backend is unreachable — after the error posture's retry rule, run the unavailable stop (`stop_message.py unavailable --done`).

**Failure modes + recovery** and the **glossary** live in `references/spot-check-and-troubleshooting.md` — consult it when a `generate_scenarios_start` call raises, a job reports `status='failed'`, or a `scenario_set_id` goes stale.
