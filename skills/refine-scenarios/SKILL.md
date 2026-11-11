---
name: refine-scenarios
description: >-
  Interactively steer which scenario types the backend generates, review what changed, then materialize. Alternative to /generate-scenarios; run after /simulation-scan-agent and /extract-scenario-variables.
---

# Refine Scenarios

You are the LOCAL Claude Code agent. The customer finished
`/simulation-scan-agent` + `/extract-scenario-variables` + (optionally)
`/generate-evals`, so `plurai_sim/scenario_variables.yaml` +
`plurai_sim/simulation_prd.md` exist. They want to **steer** which
scenario families get generated — rather than accepting the backend's
default flow-weighted sampling, they'll tell the backend exactly what
they want, see the reshaped dimension graph, iterate until happy, and
then materialize.

```
┌── customer's machine (you) ────────────────────────────────────────┐
│  1. Read inputs from plurai_sim/.                                   │
│  2. Ask the user for their initial request.                         │
│  3. MCP: refine_scenarios_start(...) → session_id + graph preview   │
│     (~60-90s — backend builds the dimension graph first call)       │
│  4. Show graph diff. Ask: happy or refine?                          │
│     If refine → MCP: interact_with_scenario_request(session_id, …)  │
│     → re-show diff. Repeat until happy.                             │
│  5. MCP: end_scenarios_session(session_id) → adapt_request          │
│  6. Ask count. MCP: generate_scenarios_start(…, adapt_request=…)    │
│  7. Poll job_status(id=set_id) until done.                             │
│  8. Write .scenario_set_id from terminal job_status result.         │
│  9. Tell the user to /simulation-run.                  │
└─────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ MCP
                                  │
┌── Plurai-hosted backend ────────┴──────────────────────────────────┐
│  plurai MCP server:                                                 │
│    refine_scenarios_start      → builds dimension graph (first      │
│      call only, ~60-90s), applies request, opens a session          │
│    interact_with_scenario_request → one refinement turn; backend    │
│      SESSION holds the full accumulated request — the plugin just   │
│      passes session_id + the new message each turn                  │
│    end_scenarios_session       → finalises the accumulated request  │
│      and returns adapt_request (pass verbatim to generate_…_start)  │
│    generate_scenarios_start    → same as /generate-scenarios:       │
│      spawns the gen pipeline with adapt_request injected            │
│    job_status (poll until done; terminal result = final summary)    │
│      → identical polling/summary contract as /generate-scenarios    │
└─────────────────────────────────────────────────────────────────────┘
```

**Why this lives on the backend.** Same reasoning as `/generate-scenarios`:
customers never provision `DEFAULT_LLM__*`. The backend builds and walks
the dimension graph. `refine_scenarios_start` + `interact_with_scenario_request`
are stateful server-side sessions — the accumulated request is stored on
the backend between turns; the plugin only needs to carry `session_id`.

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

Same as `/generate-scenarios`:

1. **`plurai` MCP server is reachable.**
2. **`plurai_sim/scenario_variables.yaml` exists** (from
   `/extract-scenario-variables`).
3. **`plurai_sim/simulation_prd.md` recommended.** Without it the
   dimension graph degenerates to a single generic flow. Warn but continue.
4. **`plurai_sim/scenario_state_schema.yaml` optional but recommended.**

## The flow

### Step 1: Detect the plurai_sim directory

```bash
for candidate in plurai_sim ./plurai_sim "$(pwd)/plurai_sim"; do
  if [ -f "$candidate/scenario_variables.yaml" ]; then
    PLURAI_DIR="$candidate"
    break
  fi
done
```

If none match, ask the user where their `plurai_sim/` lives (inside a driving command's
flow window, sanction it first:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction workdir-location`).

### Step 2: Load the input files

Read each as text:

```
variables_yaml             ← plurai_sim/scenario_variables.yaml
prd_markdown               ← plurai_sim/simulation_prd.md            (None if missing)
scenario_state_schema_yaml ← plurai_sim/scenario_state_schema.yaml   (None if missing)
document_fields            ← plurai_sim/.document_fields  (JSON list; None if missing — see materialize step)
doc_examples_set_id        ← plurai_sim/.doc_examples_set_id         (one-line string; None if missing — see materialize step)
```

Parse `.document_fields` as JSON (a list of `{field, format, brief}` dicts). If it's
absent, leave `document_fields=None`; if it's present but does not parse as a JSON list,
treat it as `None` (don't pass a broken trigger) and tell the user in plain product terms
that document generation will be skipped because its configuration didn't parse —
asking me to set up the agent again regenerates it. Don't paste the parse error.

If `scenario_state_schema.yaml` is missing, search standard env-gen
locations (the file is written by `/generate-mock-env`):

```
<plurai_dir>/simulation_env/scenario_state_schema.yaml
<plurai_dir>/../simulation_env/scenario_state_schema.yaml
```

If `prd_markdown` is missing, tell the user in plain product terms that
scenario diversity will suffer (every scenario walks the same generic
flow). Offer to abort if they want to write the PRD first.

**Pass each file through BYTE-FOR-BYTE — never re-serialize it.** Use the
exact text `Read` returned and put it straight into the MCP argument. Do
NOT reformat, re-indent, "tidy", or convert YAML between block and flow
style. In particular, never collapse a block mapping into a flow mapping:
`scenario_state_schema.yaml` is emitted by env-gen in block style, and a
field like

```yaml
co_occurs_with:
  type: list[string]      # valid block style — leave it exactly like this
```

becomes INVALID if flattened to `{type: list[string], required: true}` —
the unquoted `[` opens a YAML flow sequence and the backend job crashes in
`building_graph`. Forward the raw bytes.

(There is no local pre-validation of this payload before it's sent — a malformed
YAML fails deep in the backend's `building_graph` phase with a cryptic parser
error instead. Re-`Read` the source file and pass it unchanged — do not try to
hand-fix or re-serialize it, which is exactly what introduces the flow-style
break described above.)

### Step 3: Ask the user for their initial request

Before calling any MCP tool, ask the user what scenario families or types they
want. Explain the concept briefly:

> "Describe the scenario mix you'd like. For example:
> 'a balanced mix of clean valid invoices that auto-email AND defective ones
> routed to review; add multi-currency invoices' — or any specific flow families,
> edge cases, or ratios you have in mind. You can always refine further after
> seeing what the backend generates."

Once they provide a request, proceed.

### Step 4: Start the refinement session

**Note:** Building the dimension graph takes ~60-90 seconds on the first call.
Tell the user upfront ("Starting refinement session — the backend builds the
dimension graph on the first call, expect ~60-90s…").

(For the first three calls' signatures + response shapes at a glance, and the failure-mode / glossary reference, see `references/refine-mcp-reference.md`. The fourth call, `generate_scenarios_start`, is written out in one place only: `${CLAUDE_PLUGIN_ROOT}/skills/refine-scenarios/references/generate-scenarios-call.md`.)

**The stash is pre-authorized by the invocation — never ask consent for it.** It egresses
the PRD and the variables; the harness's tool-approval prompt is the consent chokepoint and
disclosure is the id lines, not a question.

**Stash the big documents from disk first** — immediately before the call, so the ids
reflect what is on disk now (unchanged files re-stash as a free no-op, same id; an edited
PRD mints a new one automatically). Relay any `stash_doc: WARNING:` stderr lines:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
# The markers go first. A stash that cannot run, or a refusal below, then leaves no earlier
# run's id to pass for this run's; each stash that succeeds writes its marker again.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/clear-marker.sh" .prd_upload_id .variables_upload_id .plurai_config_upload_id
# What an inline fallback would carry through your context, in bytes.
wc -c "$PLURAI_DIR/simulation_prd.md" "$PLURAI_DIR/scenario_variables.yaml" "$PLURAI_DIR/plurai.config.yaml" 2>/dev/null
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
echo "PRD_ID=${PRD_ID:-STASH_FAILED} VARS_ID=${VARS_ID:-STASH_FAILED} CONFIG_ID=${CONFIG_ID:-STASH_FAILED}"
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

Read the ids from the `echo` (shell state does not survive to the MCP call); `STASH_FAILED`
→ use that document's inline parameter instead — unless its `stash_doc:` stderr line says
`backend refused the document`, `backend would refuse the document` or `do not send the document
inline`: the document is not UTF-8, blank, over the backend's cap, or refused by the proxy (413),
and inline meets the same refusal, so fix the file first. Never read a leftover marker — a stale
id resolves flawlessly to the OLD text, which is why the block removes every marker first.

**`ledger_version` — the grounded facts the scenarios are built from.** When `refine_scenarios_start` names a
`ledger_version` parameter in your tool list, the backend works from a SETTLED version of the agent's
grounding ledger and requires that id: the config's tool arguments, the PRD's tool, entity and
agent-fact sections, and the variables' tools, entities, user profiles and complications are not read for
the facts they carry (what the variables say ABOUT a tool, a profile or a complication, its description
and typed traits, is still read from them, by name), and the trace brief's tools are not read at all. When
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
   neither a `.ledger_version` left on disk nor a job recorded for another workspace is a pin. Read only `$WORKDIR`'s
   own markers: a job or pin recorded above `envs/` belongs to the bench's own workspace, which this env's call does
   not name.

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

Never send "latest", never read an id out of `grounding_ledger.yaml` or the PRD, and never send an id from another
workspace: the backend refuses all three, and a call with no settled version is not made at all. Never drop the
argument to get a call through. The session and the materializing `generate_scenarios_start` call (Step 7) carry the SAME id: the graph the user approves is adapted over the facts the set is then generated from.

Call:

```
refine_scenarios_start(
    variables_upload_id=<$VARS_ID echoed by the stash above>,  # STASH_FAILED → variables_yaml=<text> inline
    prd_upload_id=<$PRD_ID echoed by the stash above>,         # STASH_FAILED → prd_markdown=<text> inline
    request=<their initial request>,
    prd_markdown=<text or None>,
    scenario_state_schema_yaml=<text or None>,
    inputs_schema_yaml=<text or None>,
    outputs_schema_yaml=<text or None>,
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"; scopes any include_trace_brief pull to THIS bench's brief for the whole session
    ledger_version=<the pin, read above>,  # REQUIRED when the tool list names it; omit otherwise
)
```

A refusal reads `ledger_version refused (<cause>)`: answer it with the table in the section "The `ledger_version` refusals" below Step 4, and never retry without the argument.

The response shape:

```yaml
session_id: <uuid>
turn: 1
request_so_far: <accumulated request string>
graph_diff:
  reweighted: {<flow_id>: <new_weight_int>, ...}   # dict[str, int] — new weight only; no old_weight
  added_flows: [<flow_id>, ...]                     # list of flow-id strings only
  dropped_unknown: [<flow_id_or_label>, ...]
  no_op_reason: <str | null>
flows: [{id, user_goal, priority_weight, synthesized: bool}, ...]
```

**Save `session_id`** — every subsequent `interact_with_scenario_request`
call and the final `end_scenarios_session` need it.

**Present the graph diff** clearly to the user. Render a structured summary.

`graph_diff.reweighted` is a `dict[str, int]` — keys are flow ids, values are the NEW weights (integers 1..10).
To show old → new, look up the flow's old weight from the top-level `flows` list.
`graph_diff.added_flows` is a `list[str]` of flow ids only — look up goals/weights in the top-level `flows` list.

```
Graph shaped by your request (turn 1):

  Reweighted flows (new weight shown; old weight from the flows list):
    invoice_auto_email_flow:   4 → 8   ↑
    defective_invoice_flow:    3 → 7   ↑
    single_currency_flow:      9 → 2   ↓

  New flows synthesized (details from top-level flows list):
    multi_currency_invoice_flow ★  weight=7
      goal: "Handle invoices with multiple currencies and exchange-rate logic"

  Unrecognized labels (dropped_unknown):
    "turbo_mode_flow" — no matching flow in the graph; ignored

  All flows (sorted by priority weight):
    invoice_auto_email_flow         8
    multi_currency_invoice_flow ★   7
    defective_invoice_flow          7
    single_currency_flow            2
    ...

  ★ = synthesized (new node added to the graph)
  Note: "drop a flow" = the backend reweights it down, not removes it.
  dropped_unknown lists requests that named no real flow.
```

**If `graph_diff.no_op_reason` is non-null**, the adaptation produced no change. Tell the user their request had no effect (e.g. it named no real flow) and ask them to rephrase before continuing — do not silently proceed.

Then ask:

> "Happy with this shape? If yes, say 'yes' and I'll ask for the count.
> Or tell me what to change: 'also add X', 'less of Y', 'drop Z' — prior
> context is retained across turns, so you can build on what's already here."

### The `ledger_version` refusals

<!-- BEGIN shared:ledger-version-refusals-scenarios copy: generated from the generate-scenarios skill; edit there -->
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

### Step 5: The refinement loop

Repeat until the user is happy.

For each refinement turn, call:

```
interact_with_scenario_request(
    session_id=<session_id>,
    message=<their refinement message>,
    include_trace_brief=<true ONLY when applying the /analyze-traces brief>,
)
```

> **Trace-driven refine (`include_trace_brief=True`).** When `/analyze-traces` routes here to apply the
> production brief to an already-generated scenario set, set `include_trace_brief=True` (also accepted on
> `refine_scenarios_start`) — the backend prepends the scenario-relevant brief (observed user_style +
> world_state) to the graph-adaptation context. The scenario CONTENT brief additionally re-injects at
> materialization, so just refine then materialize as usual. Leave it `False` (default) for plain edits.

The response has the same shape as `refine_scenarios_start`:

```yaml
session_id: <uuid>
turn: <n>
request_so_far: <full accumulated request string>
graph_diff:
  reweighted: {<flow_id>: <new_weight_int>, ...}   # dict[str, int]
  added_flows: [<flow_id>, ...]                     # list of flow-id strings only
  dropped_unknown: [...]
  no_op_reason: <str | null>
flows: [...]
```

**The backend SESSION holds the full accumulated request.** The skill
only passes `session_id` + the new `message`. This is how full context is
retained across turns — the plugin does NOT need to re-send prior messages.
The user can say "also add X" / "less of Y" / "drop Z" without restating
everything from the beginning.

Re-present the full `graph_diff` + `flows` list on each turn (use the same
format as Step 4). Show the turn number so the user knows how many iterations
they've had.

Remind the user after each turn:
> "Prior context is retained — you can keep refining with 'also…' / 'less of…'
> / 'drop…'. Say 'yes' when you're happy with this shape."

**Handling `dropped_unknown`:** if the backend returns flow labels in
`dropped_unknown`, surface them explicitly:
> "Note: these labels in your request didn't match any flow in the graph and
> were ignored: [list]. If you intended a specific flow, try describing it
> differently — or the backend may have synthesized an equivalent one as a
> new node."

Repeat until the user says they're happy with the shape.

### Step 6: Finalize the session and ask for count

When the user is happy, call:

```
end_scenarios_session(session_id=<session_id>)
```

The response:

```yaml
session_id: <uuid>
adapt_request: <finalized accumulated request string>
graph_diff:
  reweighted: {<flow_id>: <new_weight_int>, ...}   # dict[str, int]
  added_flows: [<flow_id>, ...]                     # list of flow-id strings only
  dropped_unknown: [<flow_id_or_label>, ...]
  no_op_reason: <str | null>
note: <optional backend note>
```

**Save `adapt_request`** — pass it verbatim to `generate_scenarios_start`.

Then ask the user how many scenarios they want:
- Default: 3 (good for qualification and quick spot-checks).
- Recommended for a confident set: 15-30.
- Backend cap governed by `scenario_job_max_count` (default 50).

Also surface the same optional inputs as `/generate-scenarios`:

- **`interest`**: a natural-language steering overlay — **mutually exclusive with
  `adapt_request`**. The backend ignores `interest` entirely when `adapt_request`
  is set. Do NOT pass both expecting them to combine; only one takes effect. After a full
  refinement session, `adapt_request` is already set and `interest` will be silently
  ignored — omit it unless the user explicitly asks to replace the refinement with
  an interest-only run.
**Agent shape: as `/generate-scenarios` Step 4 — omit `shape`, send the config (`plurai_config_upload_id`); read `simulate.py`'s hooks only for its `invoke_pipeline`-only STOP.**

### Step 7: Materialize the scenario set

`generate_scenarios_start` sends the materialized `variables_yaml` (and related config text)
straight to the backend over MCP; the harness's normal tool-approval prompt (naming the tool + its
arguments) is the user's consent chokepoint. The `refine_*` tools carry no file uploads, but Step 4's
`plurai stash-doc` calls DO egress the PRD and the variables — same chokepoint, same posture: both are
pre-authorized by the invocation, and neither is ever a question. Do not duplicate the disclosure in chat.

If the user denies the harness prompt, surface the denial verbatim and stop.

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

Resolve knowledge bases from the canonical manifest: the active env manifest
(`plurai_sim/envs/<active>/plurai.env.yaml`) `knowledge_bases` list, else
`plurai_sim/.knowledge_bases` (JSON list). Pass `knowledge_bases=[{corpus_ids, label, tool}, …]`
— one entry per base, each entry's `corpus_ids` holding 1 (fixed) or N
(per-tenant) ids. **Forward the manifest's `tool` verbatim** when the entry has one (omit
the key when it has none) — drop only the bookkeeping `kb_id`. Omit (or pass
`None`) when there's no corpus. **Derive each `label` from the corpus's own metadata
(`get_artifact_meta(<corpus_id>)`: its `task_description`, scope and counts), never from the
manifest copy unchecked** — the planner reads the label as a description of what it is walking,
and one that overstates the corpus steers the walk off the material that exists. The full rule is
in `/generate-scenarios` where the same list is assembled.

Document generation: `document_generation = bool(document_fields) or 'generated_document' in (scenario_state_schema_yaml or '')`.

Artifact-fidelity feedback (optional): if the user has SEEN a prior batch and wants the
generated artifacts — especially documents — closer to their examples, ask one question
("Anything about how the generated documents/personas should look? e.g. 'closer to the
examples I uploaded', 'shorter', 'keep the letterhead'") and pass the answer verbatim as
`artifact_feedback=<str>`. It steers both the scenario-gen SDK session and the document
sub-agent, and layers a correction on top of `doc_examples_set_id`'s template (it only
affects document authoring when `document_generation=True`). This is the natural home for
iterative "I saw it, now make it closer" feedback. Omit (`None`) on a first pass — there's
nothing to react to yet.

> **Fidelity caveat:** `end_scenarios_session` returns only the accumulated
> request string; `generate_scenarios_start` then RE-RUNS the adaptation as a
> fresh, non-deterministic LLM call. The materialized dimension graph reflects
> the same request but synthesized flow ids and exact weights MAY DIFFER from the
> preview the user approved. This is expected — treat the preview as an
> approximation, not a guarantee.

Call: **compose it from the ONE contract.** Read
`${CLAUDE_PLUGIN_ROOT}/skills/refine-scenarios/references/generate-scenarios-call.md`
before composing — the full parameter list is there and nowhere else.

Three things about this lane, which that file also states: pass
`adapt_request=<adapt_request from end_scenarios_session>`; OMIT `interest` — it and
`adapt_request` are **mutually exclusive** and the backend ignores `interest` when
`adapt_request` is present, so never pass both; and re-stash the upload ids from disk FIRST (the
same `plurai stash-doc --out` block, runner check and `echo` included, as `refine_scenarios_start`
above) — shell state does not survive to the MCP call, so what you pass is what the re-stash just
echoed.

The response is a generic **Job envelope**:

```yaml
id: <uuid>                 # this IS the scenario_set_id
status: queued
phase: queued
started_at: <float>
completed_at: null
error: null
detail:
  scenario_set_id: <uuid>
  dataset_name: <str>
  scenarios_total: <int>
```

**Save `id` (the scenario_set_id).** Every poll + the eventual end call need it.

If the call raises `ValueError("scenario_storage_root is not configured...")`,
the backend doesn't have scenario-gen enabled — not fixable from the customer side: run the contact hand-off (`stop_message.py contact --done`). Don't retry.

If the call raises a `ValueError("count N exceeds scenario_job_max_count=...")`,
lower count and retry silently.

**Environment manifest (if this repo uses named envs).** If `plurai_sim/envs/`
exists, also record the set id into the active env's manifest:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
python "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" write "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" --scenario-set-id <scenario_set_id>
```

Skip if there's no `envs/`.

### Step 8: Poll for progress

Loop until `status` is `done` or `failed`, waiting between reads. The job is minute-scale, so the
cadence is MINUTES, not seconds (see `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` — canonical).

**One blocking call per wait** — the sleep lives inside the script, so the wait cannot overlap the
read:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id>
```

**Call this with a Bash `timeout` of `600000`** — the harness default is 120 000 ms, which kills the script 420 s inside its own budget and looks exactly like a wedged backend.

**Poll in the FOREGROUND, and keep the turn open until the job is terminal.** Never run `poll_job.sh` or `wait.sh` in the background, and never end a turn on "I'll check back" while the job is `queued` or `running`: a headless `claude -p` session exits when the turn ends, the background poll dies with it, and the user gets no result. On exit 2 re-invoke in the same turn, until the job is `done` or `failed`.

It returns when the job is terminal; then read it ONCE through MCP for the full envelope:

```
job_status(id=<job_id>)   # → render the terminal result
```

Exit codes: `0` done · `13` failed · `15` **an id the backend does not know** (re-check / re-launch
the id — the backend is fine) · `14` budget spent, backend healthy (**slow, not wedged** —
re-invoke) · `12` this backend has no `GET /jobs/{id}` — a 404 with **no job envelope** (fall back to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110` then `job_status(id=…)`, waits kept SERIAL. Run `wait.sh` in the FOREGROUND — its sleep is internal, so the harness's block on a bare foreground `sleep` does not apply and the tool timeout bounds it. It takes a DURATION and nothing else: never hand-roll a loop that exits on a job status (the backend reports success as `done`, and a loop watching for `complete`/`completed` never exits)) · `77` bad token · `69` job state unknown: backend not responding, or up with the job route faulting (the script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) · `7` Plurai refuses this version of the plugin or its SDK: the plugin's update text was printed; say it verbatim and alone, then stop.


`job_status` returns the **Job envelope**:

```yaml
id: <uuid>
status: queued | running | done | failed
phase: queued | building_graph | seeding | rendering | evolving | done | failed
started_at: <float>
completed_at: <float | null>
error: <str | null>
detail:
  scenario_set_id: <uuid>
  scenarios_completed: <int>
  scenarios_total: <int>
  latest_iteration_message: <str | null>
```

Read the phase from `phase` (not `status`) for the progress label; read
counters from `detail.scenarios_completed` / `detail.scenarios_total`.

**Render to the user on every status change**, not every poll:

```
[15:02:11] queued — backend has the job, waiting for an asyncio slot.
[15:02:14] building_graph — extracting flows + scoring + edges.
[15:02:38] seeding — sampling 3 walks (stratified, adapted to your request).
[15:02:39] rendering — rendering 3 seed prose blocks.
[15:02:55] evolving — running per-scenario evolution loop (3 scenarios).
[15:07:09] done — 3/3 scenarios complete.
```

`detail.scenarios_completed` ticks up one per scenario as each finishes, but it sits at 0
until the FIRST one completes (7-20 min), freezes through redraw rounds (a redraw passes no
set-level count, so it cannot walk the number backwards), and is reconciled at the end to
what actually shipped — so it can also go down. **All three are normal** — do not interpret
"0/N for several minutes" as a stall.

Reliable wall-time guidance during `evolving`:

- Expect **3-10 min per scenario** at default concurrency settings (same figure as
  generate-scenarios — the two skills quote ONE number, ~9 min/scenario is healthy).
- Render passively, quoting the SERVER's `elapsed_seconds`: `[15:09:11] still evolving (elapsed_seconds=738 → ~12 min) — typical for
  6-scenario batches.` Do NOT ask "How do you want to proceed?" or frame it as
  a stall — the stall diagnosis isn't grounded in reliable telemetry.
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
  - **Use whatever wait primitive your harness provides, one at a time, and re-read only
    after it returns.** What matters is that the waits are *serial* — not which mechanism
    performs them. Claude Code, the harness these skills actually run in, BLOCKS foreground
    `sleep`, so use its own wait/timer primitive there.
  - Only where a foreground blocking sleep is actually available does that reduce to
    ``sleep 30 && <poll>`` as one command. Treat it as an illustration of the serial shape,
    never as the canonical instruction: writing the rule as that shell literal is what once
    forced an operator to choose between the letter of the rule and its purpose.
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
  ticks up through a perfectly healthy batch — a batch passes ~15 min without
  anything being wrong. While ``phase == 'evolving'``, the concrete rule is (compute
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
  moving-counter test applies to the phases that actually move it. A stalled job
  proves itself; a slow one does not have to.
- **A poll that TIMES OUT is a different question than a slow job — probe
  liveness.** When ``job_status`` itself hangs or errors at the
  transport level, run this in ONE call:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
  PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require "backend probe")" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed
  if [ -n "$PLURAI_BIN" ]; then
    "$PLURAI_BIN" backend probe --timeout 5
  else
    echo "PROBE UNAVAILABLE: no plurai runner with backend probe"
  fi
  ```

  It prints exactly the HTTP code of `GET /healthz`, `000` for no answer.
  **Only a 2xx means the backend is up** and the job is slow/large (keep
  polling). Any other code — a ``401``, a ``404``, a ``502`` — proves only that
  something answered (an auth layer, another service on the port, a proxy with no
  upstream), not that the backend is healthy: treat it as DOWN/UNHEALTHY and name
  the code. `000`, repeated once, means the BACKEND is down. Either way it is a blocker, not a stall (say so plainly, include the job id, and stop polling that host). `PROBE UNAVAILABLE` means there is no Python runner here (SDK
  not installed yet) or it predates `backend probe`:
  liveness is NOT VERIFIABLE from this shell. Say that, never call the backend up
  or down on it, and read `job_status` once more; a second timeout is a blocker to report with the job id. The block exits 1 on `client_pin: REFUSED` (the client
  pinned for this workdir refuses this runner) or on `refusing unauthenticated remote
  calls` (no token for a remote backend): stop and tell the user, in the message's
  own words. ``poll_job.sh`` runs this probe for you on
  timeout and reports down-vs-slow directly. When the stall
  rule genuinely fires: run the contact hand-off (`stop_message.py contact --done`).
  **Do not auto-cancel.**

If `status='failed'`, read the `error` field YOURSELF to classify — never show
it to the user. Modes and what YOU do:

- `scenario generation failed in 'building_graph'` — usually a PRD parsing
  problem. Read the message yourself; if it points at something in the PRD the
  user must change, describe the needed fix in plain product terms and offer to
  re-run afterwards. Don't paste the message. A provider timeout or 5xx here
  (`504 Provider timed out`) is final: the backend already retried it inside the
  job, and nothing here fixes it. Fix per this list; if nothing here fixes it, or the re-run after the fix fails again, run the contact hand-off (`stop_message.py contact --done`).
- `all N scenarios failed during evolution` — a batch-wide stall or rate limit,
  which clears between runs: retry once, silently. If that fails too, nothing here fixes it: run the
  contact hand-off (`stop_message.py contact --done`).

### Step 9: Save the scenario_set_id and surface the summary

On `status='done'`, the terminal `job_status(id=scenario_set_id)` result carries the
final summary:

```yaml
id: <uuid>
status: done
phase: done
started_at: <float>
completed_at: <float>
error: null
detail:
  scenario_set_id: <uuid>
  scenario_names: [scenario_01, ...]
  dataset_metadata: {coverage_report: {...}, ...}
  dimension_graph: {flows: [...], edges: [...]}
  gate_authority: {findings: [...]}
  coverage: {count, total_flows, covered, uncovered}
```

Write the id to `<plurai_dir>/.scenario_set_id` (one line, no trailing
newline):

```
<scenario_set_id>
```

**Stage it** — it names the backend set the committed inputs correspond to. Nothing to
edit in any `.gitignore` (the repo-root managed block already tracks this marker; if the
repo's block is missing or its BEGIN line carries an older rev, re-run
`/simulation-scan-agent` Step 5):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .scenario_set_id
```

**Do NOT write scenarios locally.** The backend is the GT store.
Tell the customer to ask me to list the scenarios to inspect them; read
individual scenarios with `get_scenario(scenario_set_id, scenario_name)`.

Bring the page's copy up to date: it pulls the new set over the old one (which moves to
`artifacts/history/`), drafts `suite.json`, and marks the runs of the old set stale.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/artifact_deps.py" apply "$WORKDIR" --refresh
```

Print the summary:

Backend LLM cost figures (`total_cost_usd` etc.) are INTERNAL telemetry — never show them to the customer, in this summary or any progress line.


```
Scenario set ready: <scenario_set_id>

  3 scenarios generated in 4m18s.
  Dataset: generated_dataset
  Shaped by: "<request_so_far>"

  Flow coverage:
    multi_currency_invoice_flow ★:  2 ⬤⬤
    invoice_auto_email_flow:        2 ⬤⬤
    defective_invoice_flow:         1 ⬤
    single_currency_flow:           0 (uncovered — low weight as requested)
    ...

  ★ = synthesized flows (added by your refinement request)

  Next steps:
    1. Inspect scenarios (optional): /list-scenarios
    2. Run the simulation: /simulation-run

  The scenarios live on the backend as canonical GT.
  To delete this set later, ask me to delete this scenario set.
```

The flow-coverage table comes from
`detail.dataset_metadata.coverage_report` in the terminal `job_status` result.
Annotate synthesized (★) flows by checking `detail.dimension_graph` from that
same terminal `job_status` response — **not** the cached preview from the
refinement session. The materialization re-runs the adaptation (a fresh LLM
call), so synthesized flow ids and exact weights MAY DIFFER from the preview
the user approved; derive all annotations from the returned graph, not from memory.

Decide on each finding yourself, per
`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`.

Then read the entries YOURSELF before printing the summary: fix
what you can (e.g. regenerate dropped/under-covered items) without narrating; if
a finding materially limits the set and can't be fixed from here, state the
impact in ONE plain sentence (no internal jargon, no finding text); otherwise
don't mention it.

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

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step refine-scenarios --flowdir "$FLOWDIR"
```

It prints one JSON line. When `flow_continues` is true, leave out this hand-off's "Next" line and any offer to
run another command, and end the turn on this hand-off: the flow continues by itself. In limited mode (Step 0 printed
`ready, limited`) it continues through the `flow-window.sh handoff` line Step 0 names instead: run it right after
this hand-off and do what it prints in the same turn. When `run_end` is true this is the run's one publish: when
`retry` is true, repair it once the way the next sentence repairs a hand-off (`fill_refused` or `garbled`: nothing to
rewrite) and fill again; when `retry` is false, never fill again, whatever `skip` says. Otherwise, when `skip` is
`internal_text`, rewrite each line `internal_text` names the same way, then fill again; when `skip` is `numbers`,
a surface disagrees with the run summary: re-render it (`render_run_report.py`, then `release_verdict.py render`) and
fill again once; still `numbers` is a failure hand-off, never a page with those numbers. When `publish` is false,
add nothing and say nothing about it, except that when `say` lists lines, end the hand-off with them, verbatim, and
never run `done`. Otherwise publish the file at `report` with the Artifact tool, passing `report` as `file_path`
character for character (never respelled, never `~`, never relative), with `icon: "report"`, and never
`Read` it. When `page` is not null, pass it as `url`; if that publish is refused because this conversation has
not read the page, `read` it once (do not open the file the read saves) and publish again. If the page belongs to
another account or is gone, run
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" forget "$WORKDIR"` and publish once without
`url`. Never pass `force`. Then:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step refine-scenarios --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

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

- **Never write scenario files to the customer filesystem.** The backend is
  the canonical store; `plurai-run-simulation --scenario-set-id <id>
  --scenario-id <name>...` streams scenarios over MCP at run time. The page's
  read-only copy under `artifacts/` is pulled by `artifact_deps.py` only.
- **Carry `session_id` across every refinement turn.** The backend session
  holds the accumulated request. Do NOT re-send prior messages; just pass
  `session_id` + the new `message` each time.
- **Pass `adapt_request` verbatim.** Copy the exact string returned by
  `end_scenarios_session` into `generate_scenarios_start`. Do not summarize,
  reformat, or truncate it.
- **Pass all input files BYTE-FOR-BYTE** — see the loading rules in Step 2.
- **Poll on a MINUTES cadence (~1-3 min between reads).** The job is
  server-side and minute-scale, so a seconds-cadence loop wastes the backend's
  handler budget and cannot be honoured anyway — Claude Code blocks foreground
  `sleep`. See `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` for the exact shape. What the user
  needs is a rendered update each time you DO read, not a faster loop.
- **Don't re-trigger `generate_scenarios_start` on an in-flight set.** Confirm
  with the user before replacing an existing set; call
  `delete_artifact(<old>)` first.
- **Never fall back to a local scenario generator.** Generation runs on the backend only.

**The three refine MCP tool calls at a glance** (full `refine_scenarios_start` → `interact_with_scenario_request` → `end_scenarios_session` signatures + response shapes), the **failure-mode recovery table**, and the **glossary** live in `references/refine-mcp-reference.md` — consult it for the exact call surface, when a call raises or a job reports `status='failed'`, or to look up `adapt_request` / `graph_diff` / `synthesized flow` semantics. The fourth call's parameter list is in `${CLAUDE_PLUGIN_ROOT}/skills/refine-scenarios/references/generate-scenarios-call.md` — one copy, for every skill that composes it.
