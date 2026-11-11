---
name: generate-evals
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: >-
  Generate policies_config.yaml by calling the backend eval generation service with the agent's artifacts. Run after /simulation-scan-agent and /extract-scenario-variables.
---

# Generate Evals (thin wrapper)

You produce a single file: `<agent>/plurai_sim/policies_config.yaml`. The
actual authoring happens on the **Plurai-hosted backend** via the
`plurai` MCP server, which runs the generate-evals
SKILL as an agent on the backend. The generation recipes and
anti-pattern guidance live there, not here.

Your job is to:

1. Collect the agent's plurai artifacts.
2. Send them to the backend via `generate_evals_start`.
3. Poll `job_status` until done.
4. Fetch the result + validate locally via `verify_policies.py`.
5. Tell the user how to iterate: ask me to edit the evals.

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

## Step 1: Confirm preconditions

Verify these files exist in the agent's plurai directory:

- `plurai.config.yaml` — produced by `/simulation-scan-agent`
- `simulation_prd.md` — produced by `/simulation-scan-agent`
- `scenario_variables.yaml` — produced by `/extract-scenario-variables`
- `eval_intent.yaml` — produced by `/extract-eval-intent` (OPTIONAL —
  if missing, synthesize a minimal intent inline from the PRD; the
  backend can also infer when intent is sparse, but an explicit
  `eval_intent.yaml` produces sharper output)

**Do NOT pause to ask the user about `eval_intent.yaml`.** When it's absent
(the common case — `/extract-eval-intent` is optional and the default chain
skips it), silently synthesize a minimal inline intent from the PRD + config
and proceed to generate the evals.

**THAT SYNTHESIS HAS A SCHEMA, AND YOU ARE ONE OF ITS AUTHORS.** An intent you write inline is
a legitimate author of the same document and is bound by the same rules: read
`${CLAUDE_PLUGIN_ROOT}/skills/generate-evals/references/eval_intent_schema.md` — which OWNS
those rules for every author — and write `plurai-eval-intent/2` (axes, each failure mode naming
its source in `traced_to`, one id space across the file), never a remembered shape and never v1.
Two untyped one-shot intents off the same PRD a night apart shared only six policy names in the
eval sets that came out; the schema is what removes that swing. When the caller forwards a context document
(`/simulation-generate-suite [context]` — an operating-policy/PRD `.md`), fold
its eval-relevant content — what to catch, what must never happen — into the
intent you synthesize: this is the only route that document reaches eval
authoring, and skipping it ships judges that never saw the policy the
scenarios were shaped by. **The last build's rule rulings ride the intent too.** When
`$ART/flagged_evals.json` exists and a policy in it carries `rulings` (the final review found that eval's rule or
its kind wrong), turn each into ONE sentence in the PRD's own terms, citing the section it rests on, and add it to
the `notes` of the axis that owns that policy's rule (judge it from the policy's rubric), each sentence opening
`Review note:`, replacing only that axis's sentences that open `Review note:` (any other note is the customer's;
keep it) — in `plurai_sim/eval_intent.yaml` itself when it exists (the backend's echo then stays
`same`), and in the intent you send. A note never says a case passes; it says what the rule charges. It
never removes, narrows or adds a failure mode, and carries no conversation's ids, turns or clause letters; a
ruling no axis owns, or one the PRD does not support, is left out. Say nothing about it.
Generating evals is this skill's job; the
user can sharpen them afterward by asking you to extract the eval intent and regenerate, to edit
them, or by uploading their own. Do NOT stop the build/test chain to
offer an "inline vs extract the eval intent first" choice — just build.

**A term the intent and the PRD SHARE is quoted from the PRD, verbatim — never re-derived.**
When a word decides a judge's verdict ("resolved", "in scope", "confirmed", "complete"), the
intent carries the PRD's own definition word for word, cited to its section. Two documents
defining the same term differently makes the eval unscorable: the judge fires on a scenario the
PRD says is correct, and neither document is wrong on its own. This applies to the intent you
synthesize inline here exactly as it does to an authored `eval_intent.yaml`.

If any of the first three is missing, tell the user the relevant prior
command to run, then stop. Do not proceed.

## Step 2: Read the artifacts

Read all four required files as plain text. You will pass them as text
blobs to the backend — no file paths cross the wire.

Then check the optional files in one call:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
for f in .scenario_set_id .corpus_id .knowledge_bases inputs_schema.yaml outputs_schema.yaml; do [ -e "$PLURAI_DIR/$f" ] && echo "$f=present" || echo "$f=ABSENT"; done   # ABSENT is normal; never probe with ls
```

Read `.scenario_set_id` when present (one line — written by
`/generate-scenarios` Step 6); Step 3 explains why it is required in practice
for corpus-backed agents.

The two schema files are produced by `/extract-scenario-variables`
when the customer's `simulate.py` was type-annotated with Pydantic
models on `invoke_pipeline`:

- `<plurai_dir>/inputs_schema.yaml` — AST-derived input contract
- `<plurai_dir>/outputs_schema.yaml` — AST-derived output contract

When present, read them too. They give the backend SDK skill the EXACT
field names + types the agent receives + returns, which prevents
shape-blind policy authoring (e.g. policies that call `.get('Label')`
on samples that are actually JSON strings, returning `inconclusive` at
eval time because the call raises `AttributeError`). When absent,
proceed without — the skill falls back to inferring from prose, but
its policies will be less precise.

## Step 3: Kick off the backend job

**These stashes are pre-authorized by the invocation — never ask consent for them.** They
send the PRD (which may carry the agent's system prompt verbatim), the variables and the
config to the backend; the harness's own tool-approval prompt is the user's consent
chokepoint, and there is no upload gate in this flow or downstream. Disclosure is the
`*_ID=` lines and the step's summary — a statement, not a question. (A scan once invented
an "upload the spec?" question at the twin of this step, stalling the flow on a gate no
skill defines.)

**Stash the big documents from disk first** — immediately before the call, every time, so
the ids always reflect what is on disk now (the PRD may have been reconciled by
`/build-rag-corpus` Step 6.5 since anyone last stashed it; unchanged files re-stash as a
free no-op returning the same id). Relay any `stash_doc: WARNING:` stderr lines:

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
CFG_ID="$(cat "$PLURAI_DIR/.plurai_config_upload_id" 2>/dev/null || true)"
if [ -f "$ART/data_profile.md" ]; then
  [ -z "$PLURAI_BIN" ] || python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" stash-doc "$ART/data_profile.md" data_profile --out "$PLURAI_DIR/.data_profile_upload_id" || { [ $? -eq 7 ] && exit 7; }
  PROFILE_ID="$(cat "$PLURAI_DIR/.data_profile_upload_id" 2>/dev/null || echo STASH_FAILED)"
else
  PROFILE_ID=ABSENT
fi
source "${CLAUDE_PLUGIN_ROOT}/scripts/stash_scan_contract.sh" || { [ $? -eq 7 ] && exit 7; }   # $CONTRACT_ID + $CONTRACT_PROCEED + the two counts; 7 = the backend refused the version, any other status goes on
echo "PRD_ID=${PRD_ID:-STASH_FAILED} VARS_ID=${VARS_ID:-STASH_FAILED} CFG_ID=${CFG_ID:-STASH_FAILED} PROFILE_ID=$PROFILE_ID CONTRACT_ID=${CONTRACT_ID:-STASH_FAILED} CONTRACT_PROCEED=$CONTRACT_PROCEED CONTRACT_PROVISIONAL=$CONTRACT_PROVISIONAL CONTRACT_BLOCKED=$CONTRACT_BLOCKED"
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

The `echo` is what you read the ids from (shell state does not survive to the MCP call),
and a failed stash prints `STASH_FAILED` — never trust a leftover marker instead: a stale
id resolves flawlessly to the OLD text, which is exactly what makes it dangerous. The
marker removal at the top destroys that false authority before anything runs. For a
`STASH_FAILED` id, classify on that document's `stash_doc:` stderr line — the block runs every
stash with `|| true`, so the sentence, not an exit code, is what you have:

- ends `fall back to the MCP stash_input_doc tool` → the backend predates this stash (no
  `POST /input-docs`, no `/storage`, or an `/input-docs` that ignores the stored object; a small
  file gets this too) → call the MCP `stash_input_doc(text=..., label=...)`;
- `backend refused the document`, `backend would refuse the document`, or `do not send the
  document inline` → the document itself is refused: not UTF-8, blank, over the backend's cap, or
  a 413 from the backend or its proxy. Fix the file; never inline — inline meets the same refusal
  through your context;
- any other `stash_doc:` error (a transport failure, a failed storage upload, no id in the
  answer) → pass that document's inline text parameter instead (benign, just costlier).

**The data profile is OPTIONAL and its two non-id states differ.** `PROFILE_ID=ABSENT` means
there is no local data to profile (a tool-less or API-only agent) — omit the parameter and
proceed silently. `PROFILE_ID=STASH_FAILED` is the file existing and the stash failing:
classify it on stderr like any other document and fall back to the inline text. The marker removal at the
top of the block covers the absent branch with the same anti-stale rule — a marker from a run when the file existed would
resolve flawlessly to a profile no longer on disk.

**Driven by `/simulation-generate-suite`, run its Step 0 job lines (`--phase evals`) around this call**:
an adopted job replaces the call; a started one is recorded the moment it returns.

Call the MCP tool:

```
generate_evals_start(
    intent_yaml=<contents of eval_intent.yaml, or a minimal inline intent>,
    prd_upload_id=<$PRD_ID echoed by the stash above>,          # STASH_FAILED → classify on stderr (above): MCP stash_input_doc,
    variables_upload_id=<$VARS_ID echoed by the stash above>,   #   else pass the inline texts below
    plurai_config_upload_id=<$CFG_ID echoed by the stash above>,
    data_profile_upload_id=<$PROFILE_ID echoed by the stash above>,  # ABSENT → omit this and data_profile_markdown
    prd_markdown=<contents of simulation_prd.md — only when the stash failed>,
    scenario_variables_yaml=<contents of scenario_variables.yaml — only when the stash failed>,
    plurai_config_yaml=<contents of plurai.config.yaml — only when the stash failed>,
    data_profile_markdown=<contents of artifacts/data_profile.md — only when the stash failed>,
    scenario_set_id=<contents of .scenario_set_id when present — required in practice for corpus-backed agents, see below>,
    # NO `shape=`: the config you send above declares it and the backend dispatches on the declaration.
    # Undeclared + an `invoke_pipeline`-only harness: STOP, as /generate-scenarios Step 4 says — do not generate.
    inputs_schema_yaml=<contents of inputs_schema.yaml or None>,
    outputs_schema_yaml=<contents of outputs_schema.yaml or None>,
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"; scopes the auto-discovered trace brief to THIS bench
    scan_contract_upload_id=<$CONTRACT_ID echoed by the stash above>,  # ABSENT/STASH_FAILED → omit (see below)
    regression_bundle_id=<the recorded regressions, uploaded as one bundle>,  # no folders → omit (see below)
    ledger_version=<the pin, read below>,  # REQUIRED; never drop it to get a call through
)
```

**`ledger_version` — the grounded facts the evals are built from, from the one version the whole suite is on.**
`generate_evals_start` requires `ledger_version`, the id of a SETTLED version of the agent's grounding ledger. The backend
reads the tool, entity and agent facts of the set from that version: the variables' `agent_tools` and `entity_schemas`, the
PRD's tool, entity and agent-fact sections and the trace brief's anomalies are not read for them (the faults the traces
observed on a tool's error channel come from the version, and `trace_anomalies_markdown` is not read on a pinned call). Everything
else is still read from the documents as sent, the profile and complication sections and the brief's failures included, and
`provenance` still quotes the customer's own PRD, by section, and never a ledger entry. `/simulation-generate-suite` Step 0b pins
one settled version for the whole build and every generator call of the build sends that one, so an extend run sends the pin the
suite is on now, not the one of the set it extends; a run of this skill on its own reads the pin an earlier suite left.

1. **The pin comes first.** Read it; never read the mirror to choose one:

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
argument to get a call through. The same id goes to the edit session (`/edit-evals`) and to the judge optimizer
(`/optimize-eval`); a newer version reaches an eval set only through the edit path at a `/simulate` stage boundary.

**`regression_bundle_id` — the recorded regressions this set must own.** Run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" list "$WORKDIR/regressions"`. No folders →
omit the argument. Exit 2 names an unfinished folder: finish or delete it first. Otherwise upload the
directory as ONE bundle with the commands of the section "The bundle upload commands" below, reading
`$ART/final_bundle` there as `--bundle-dir "$WORKDIR/regressions"` — never the staged
final bundle, which also carries trace scenarios — and pass the returned `set_id`. Each folder is a confirmed agent
defect with the reply it was confirmed on (`issue_turn.json`). The set names one check per folder that
must fail that reply.

**`scan_contract_upload_id` — grounding by REFERENCE instead of baked literals.** The contract
carries the URL allowlist derived from the customer's corpus and the facts verified against it, so
a grounding eval references the allowlist the scorer derives rather than freezing today's URLs into
a rubric. It also carries the typed absence facts whose probe scenarios need their judges in this
set. **The SAME stashed id must reach `generate_scenarios_start` and this call** — one contract,
two consumers; two different ids are two different premises.

- `CONTRACT_ID=ABSENT` — no contract exists for this env. Omit the parameter and **say the evals
  were authored on UNVERIFIED premises**; a missing file is not a passed check, and any grounding
  eval in the set is then enumerating literals nobody checked.
- `CONTRACT_ID=STASH_FAILED` — classify on stderr as with any other stash (`fall back to the MCP stash_input_doc tool` → the MCP
  `stash_input_doc(text=<the file's contents>, label='scan_contract')` fallback); otherwise omit
  and say the same line.
- A backend that rejects the parameter is an older box: drop it, re-call, say the same line.
- `CONTRACT_PROCEED=false` — the checker found one of the scan's conclusions FALSE against the
  corpus, and the suite's Step-0 gate was overridden to build anyway. **Still pass the id** (the
  allowlist and the surviving facts are real). It demotes the suite's provenance line;
  `/simulation-generate-suite` Step 6 reads the same file.
- `CONTRACT_PROCEED=unreadable` — the file exists and its verdict cannot be parsed. Treat it as
  ABSENT: an unparseable verdict is not a passed one.
- **`CONTRACT_PROVISIONAL` / `CONTRACT_BLOCKED` — a `true` is not automatically a verified
  scan.** The first counts the typed facts the contract carried ASSERTED and UNVERIFIED (declared
  with no executable check, so nothing tested them); the second counts the checks that could not
  run at all. Both are `none` when there is no contract, and `unknown` when its verdict could not
  be parsed. **Any non-zero count goes in your summary line, named**: the eval set was built on a scan whose
  premises include N nobody tested is a different artifact from one built on a scan where every
  premise was checked, and `proceed: true` is identical in both. The report itself names WHICH
  facts, under `provisional_facts` in `scan_contract.json`.

**`scenario_set_id` is the only channel corpus ground truth reaches eval
authoring.** The backend has no corpus parameter here: it reads the linked
scenario set's knowledge plans (the planted questions + exact answers scenario
generation derived from the corpus, plus the admissible ground-truth excerpts)
and only then mandates the knowledge-correctness / retrieval policy bundle.
For a corpus-backed agent (`.knowledge_bases` / `.corpus_id` exists, or the
active env manifest lists bases), omitting it does not merely degrade the
evals — it silently ships a set that never scores the agent's primary
competence, and a confidently blind groundedness judge approves fabrications.
So the order is scenarios first, then evals: if bases exist and
`.scenario_set_id` is absent, run `/generate-scenarios` first. The one
exception is a caller whose flow deliberately runs pre-scenarios (the
`/simulation-setup` chain): proceed, but say plainly that knowledge evals
cannot exist yet and the set must be regenerated once a grounded scenario set
does (`/build-rag-corpus` Step 6.5 and the suite loop both handle this).

**The declared shape prunes dialog-only scaffolding.** The six diverse-failure
conversation catchers either pass vacuously or burn judge calls on a one-shot
pipeline — a config that declares `shape: pipeline` makes the backend drop them
instead of shipping dialog-shaped evals against a pipeline agent.

The response is a generic **Job envelope** (`{id, status: 'queued', phase,
started_at, completed_at, error, detail}`; `detail` carries
the eval job's fields). Read the `eval_set_id` from `Job.id` (or `detail.eval_set_id`,
equal) and save it — you'll need it for polling, fetching, and any subsequent
`/edit-evals` session.

<!-- BEGIN shared:ledger-version-refusals-evals owner -->
**`ledger_version` refusals.** A message that starts `ledger_version refused (<cause>)` is the backend's answer to
the pin, raised before anything ran. It is not a content problem and it is never retried without the argument: a
set built without the grounded facts is an ungrounded run, and the backend does not allow one. `/edit-evals` and
`/optimize-eval` answer a refusal with this same table.

| cause | what it means | do |
|---|---|---|
| `missing` | the call carried no version: this plugin is older than the backend | update the plugin (the user's step; say so in product terms), then start the build again. Never retry without the argument |
| `no_workspace` | the call named no `workspace_id` | add the bench workspace id (the same call block) and call once more |
| `malformed` | the value is not a version id | re-read the pin as above; never type or shorten an id |
| `grounding_off` | this backend keeps no grounding ledger | not fixable from here: run the contact hand-off (`stop_message.py contact --done`) |
| `unknown` | the backend does not hold that version | re-read the pin as above; the same answer twice: run the contact hand-off (`stop_message.py contact --done`) |
| `other_workspace` | the version belongs to another workspace | the handle on this machine points at another bench: re-run `/simulation-scan-agent`, which records this bench's own |
| `unsettled` | the version is still being made, or a finding holds it | wait for the job as above, then call once more with its `detail.ledger_version` |
<!-- END shared:ledger-version-refusals-evals -->


### The bundle upload commands

<!-- BEGIN shared:bundle-upload-commands copy: generated from the simulation-generate-suite skill; edit there -->
```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-bundle)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed: stop
# bash cannot see which backend the MCP server is bound to. Call the read-only backend_identity()
# MCP tool first and paste its object here verbatim.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-bundle --workspace-root "$PLURAI_DIR" --bundle-dir "$ART/final_bundle"
#   stdout → the manifest JSON (capture it verbatim, change nothing)
cat "$ART/final_bundle/BUNDLE.md"                      # raw posture: send this
cat "$PLURAI_DIR/.plurai/uploads/overview_md.md"       # anonymized posture: send THIS instead (the file ingest-bundle named)
```

A `WARNING (disclosure: home_named)` line from `ingest-bundle` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

A non-zero `ingest-bundle` exit is a **HARD STOP** — nothing was uploaded. Then:

```
upload_scenario_bundle_start(
    manifest=<the captured stdout manifest, opaque>,
    overview_md=<raw posture: the BUNDLE.md content, verbatim; anonymized posture: the content of $PLURAI_DIR/.plurai/uploads/overview_md.md, the file ingest-bundle named>,
)   →  UploadFinalizeResult { set_id, status: 'done' }
```
<!-- END shared:bundle-upload-commands -->

## Extend mode

`generate_evals_start(extend_eval_set_id=<existing set>, scenario_set_id=<the LARGER set>, …)`
freezes that set's promoted policies **byte-for-byte** and authors only for what the newly
linked scenario set leaves ungraded — its targets, its ungraded obligations, and any
`eval_intent` item the source set's typed ledger (`out/obligations.yaml`) has no row for. It publishes a **NEW
eval set id**; extending in place would leave a half-extended promoted set on the customer's
disk if the run failed.

It exists for one situation: the eval set was authored against a small QUALIFICATION sample and
the final suite is larger. Relinking moves which population the audits speak about; it authors
nothing. Regenerating authors everything and **discards every `/edit-evals` fix** the set
carries. Extend is the third option, by hand only: `/simulation-generate-suite` does not run it —
a policy authored after its quality loop has no run, critic or gate behind it, so the suite closes
its final-population gaps with one additive `/edit-evals` round instead and reports what no
scenario exercised. Reach for extend mode when a PUBLISHED set must grow and a run will follow.

`intent_yaml` / `prd_markdown` default from the source set's staged inputs when omitted — an
extension authored against a different intent is two half-sets in one file. Wall clock is the
same as an ordinary generation (**~10-30 min**).

After it lands, prove the result is additive before `.eval_set_id` moves:
`plurai fetch policies` → `verify_policies.py` → `scripts/assert_additive_extend.py
<pre> <new> <pre-ledger> <new-ledger>`, the last two being each set's typed ledger (`detail.obligations`
from `job_status`) written to a file. Exit 3 there is a BACKEND DEFECT — stop and report; do not move
the marker. Exit 5 (nothing added, the ledger rebuilt) adopts the new set.

It refuses a source that is not `done`, one an `/edit-evals` session is holding (that file is
the session's DRAFT right now), and at publish if any baseline policy changed.

## The violated-polarity convention (know it before reading any policy)

Every boolean policy pairs its expression/prompt with a `violated:` flag:
- **Violation form**: expression/verdict True ⟺ the BAD thing happened, `violated: ["true"]`.
- **Compliance form**: True ⟺ good/correct, `violated: ["false"]`.

The classic inversion — a compliance-shaped expression (`all(...)`, `x <= N`)
left with `violated: ["true"]` — flags every clean run as a failure.
`verify_policies.py` warns on that shape, and the backend's benign-run
self-test DROPS such policies; treat either signal as "fix the polarity",
never as agent signal.

**`beat_resolution` is the one type with no body of its own**, so the polarity
above is not authored for it. One backend judge resolves, per scenario, what happened
to each planted beat — a knowledge question the simulated user asks, or a probe about
something the agent's world does not hold — and each policy names ONE slice of that
resolution in `additional_params` (field rules in the table below): a `metric` fraction
takes `violated: ["below"]`, a `beat_id` probe `violated: ["false"]`. A beat the dialog
never reached comes back from the runtime as a typed NOT APPLICABLE verdict: not a
pass, not a failure, and needing no `applicability_guard`. `untriggered_verdict` and
`violation_is_absence` are `llm_as_judge` fields and mean nothing here.

**Where a beat comes from — and where it does not.** The backend's user simulator delivers
each planted beat as a user turn during the run and records it in the run's
`delivered_beats` ledger (`{beat id: {turn, kind}}`); the backend's judge resolves it at
finalize. The client SDK neither mints nor reads a beat id, so inspecting the installed
`plurai` package can never say whether these policies execute — a grep that finds nothing
there is the expected result, not a finding. Whether they execute is decided by ONE thing: a
scored run on the backend you are on whose ledger shows beats delivered. A generated set
carrying none of these rows is normal only for an agent with no knowledge base and no
verified absences.

**A `code_eval` reaches that ledger through three sandbox accessors, and a beat id is the only
coordinate it may name.** Which turn carried a beat is a RUNTIME fact — the simulator releases
beats by plan order and precondition, so no beat has an authored turn — and an expression that
indexes a turn number instead is graded against a dialog it invented:

- `beat_delivered('<beat id>')` — was the beat credited on some turn. **The required guard** on
  every beat-bound check: `False` is "no delivery on record", which makes the cell not-applicable
  rather than a failure the agent was never given the chance to avoid.
- `beat_turn('<beat id>')` — the 0-based user turn it landed on, for ordering a tool call against
  it (`c.get('turn') > beat_turn(b)`). An undelivered beat's is `None`, which RAISES under `>` —
  which is why the guard above is not optional.
- `reply_to_beat('<beat id>')` — the agent's reply to the turn the beat actually landed on, `''`
  when it never landed. **THE** way a per-beat check names its evidence.

The same identity is visible to the JUDGES: a user turn that carried a beat reads
`[turn <n> · beat <label>]` in the transcript they are shown, so a rubric can cite the beat by
name instead of describing the turn it guesses carried it.

## Step 4: Poll until done

Wait with ONE blocking call — the sleep lives inside the script, so the wait cannot overlap
the read:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <eval_set_id>
```

**Call this with a Bash `timeout` of `600000`** — the harness default is 120 000 ms, which kills the script 420 s inside its own budget and looks exactly like a wedged backend.

**Poll in the FOREGROUND, and keep the turn open until the job is terminal.** Never run `poll_job.sh` or `wait.sh` in the background, and never end a turn on "I'll check back" while the job is `queued` or `running`: a headless `claude -p` session exits when the turn ends, the background poll dies with it, and the user gets no result. On exit 2 re-invoke in the same turn, until the job is `done` or `failed`.

Eval generation typically runs ~10 minutes — **one or two exit-2 re-invocations are the
normal shape of a healthy job, not a stall signal**.

It returns when the job is terminal (exit `0` done · `13` failed · `15` an id the backend does not
know — re-check / re-launch the id, the backend is fine · `14` slow-but-healthy, re-invoke · `12`
no `GET /jobs/{id}` on this backend, i.e. a 404 with no job envelope — fall back to `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110` then `job_status(id=…)`, waits kept SERIAL. Run `wait.sh` in the FOREGROUND — its sleep is internal, so the harness's block on a bare foreground `sleep` does not apply and the tool timeout bounds it. It takes a DURATION and nothing else: never hand-roll a loop that exits on a job status (the backend reports success as `done`, and a loop watching for `complete`/`completed` never exits) · `77` bad token · `69` job state unknown: backend down, or up with the job route faulting (the script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) · `7` Plurai refuses this version of the plugin or its SDK: the plugin's update text was printed; say it verbatim and alone, then stop). Then call
`job_status(id=eval_set_id)` ONCE and render. Full recipe and rationale:
`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`. `job_status` returns
the generic **Job envelope**: the lifecycle `status` is `queued | running | done
| failed`, the generation phases (`preparing_inputs | calling_skill |
validating`) ride the free-form `phase` string, and the progress fields
live under `detail`.
Stream `detail.latest_iteration_message` + `phase` to the user so they see
forward motion.

**Slow is NORMAL — do not diagnose a long-running job as stuck.** Eval-gen runs
a heavy multi-turn agent on the backend; `detail.policies_completed`
stays `0` until the very end (the agent writes the whole file at once), so it is
**not** a live progress bar — ignore it for liveness. A rate-limited model can make the job take
**many minutes with the CPU idle**; that is expected, not a hang. **Read
`elapsed_seconds` off the envelope — never compute it yourself.** It is server-computed
and ticking, and it is on EVERY Job envelope; `seconds_since_progress` (time since the job
last actually MOVED) rides beside it. You have no clock, so any elapsed figure you
derive is a sum of the waits you INTENDED, and a poll loop that overlapped or skipped its
sleeps reports fiction (see `/generate-scenarios` Step 6, which records a loop escalating a
15-minute job as ~50 minutes old). Fall back to `date` minus `started_at` only against a
backend that omits both fields. Judge liveness by `seconds_since_progress` and an advancing
`phase`, and keep polling (widen to 15-30s). Do **NOT** call it
"stuck"/"rate-limited"/"auth-blocked" or restart the backend — that just discards in-flight
work.

Stop polling when:
- `status='done'` → proceed to Step 5
- `status='failed'` with `detail.draft_ref` set → an invariant gate refused the set and KEPT its
  draft; its findings on `detail.gate_authority.findings` say why. Nothing is published, so
  there is nothing to fetch yet. Write the id to `plurai_sim/.eval_set_id` and repair the draft
  in ONE `/edit-evals` session on that id: the session opens over the draft, each withheld
  policy's authored body is in its `eval.policy.*` finding's `observed.policy_body`, and a
  commit that loads publishes the set under the same id. Then run Step 5. Without `draft_ref`,
  take the branch below.
- `status='failed'` → read `terminal_reason` and the `error` field YOURSELF to classify —
  never print them. Retry per the retry rule (Error posture); a provider timeout or 5xx is final.
  Input-caused — **and "implicated" means the `error` NAMES the input file or field it
  blames; if you cannot quote that name out of the error, this branch does not apply**
  (an unattributed failure is never a question):
  fix the named input if you can, else ask the user in plain product terms, QUOTING the
  named field (inside a driving command's
  flow window, sanction the ask first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction input-fix`). A second failure,
  or anything else nothing here fixes: run the contact hand-off (`stop_message.py contact --done`).

## Step 5: Fetch + validate

**Download to disk, not through your context.** The three files below total
~40-60 KB the backend already owns; an MCP `fetch_artifact` routes every byte through
your own context twice (in + out when writing). Download them straight to disk instead:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# The intent is an INPUT: the backend copy lands beside the run's artifacts and never replaces the local file.
# Remove the previous copy first, so a failed fetch cannot leave it to be compared below.
mkdir -p "$ART"; rm -f "$ART/eval_intent.backend.yaml"
got=""
if [ -n "$PLURAI_BIN" ]; then
  # Each file is written through a temp file and a rename, so a failed fetch never truncates one.
  got="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" fetch artifact <eval_set_id> policies_config.yaml generation_trace.md --dir "$PLURAI_DIR")"
  [ $? -ne 7 ] || { printf '%s\n' "$got"; exit 7; }   # 7 = the backend refused the version: its text is in $got
  more="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" fetch artifact <eval_set_id> eval_intent.yaml --out "$ART/eval_intent.backend.yaml")"
  [ $? -ne 7 ] || { printf '%s\n' "$more"; exit 7; }
  got="$got
$more"
fi
# A file is fetched only when the command printed `wrote <its path>`. Anything else (a `missing`
# line, a failure that stopped the command early, or no runner at all) is a MISSING: line.
for f in "policies_config.yaml:$PLURAI_DIR/policies_config.yaml" "generation_trace.md:$PLURAI_DIR/generation_trace.md" \
         "eval_intent.yaml:$ART/eval_intent.backend.yaml"; do
  printf '%s\n' "$got" | grep -qxF "wrote ${f#*:}" || echo "MISSING:${f%%:*}"
done
if [ -f "$ART/eval_intent.backend.yaml" ]; then
  if [ ! -f "$PLURAI_DIR/eval_intent.yaml" ]; then cp "$ART/eval_intent.backend.yaml" "$PLURAI_DIR/eval_intent.yaml"; echo "INTENT=written"
  elif cmp -s "$ART/eval_intent.backend.yaml" "$PLURAI_DIR/eval_intent.yaml"; then echo "INTENT=same"
  else echo "INTENT=differs"; fi
fi
```

`INTENT=written` → check the copy before it stays:
`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh" && "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/extract-eval-intent/scripts/verify_eval_intent.py" "$PLURAI_DIR/eval_intent.yaml"`.
Exit 2 → remove `$PLURAI_DIR/eval_intent.yaml` (the backend's copy stays in `$ART`), so no intent with a
defect lands in the customer's repo; exit 3 → the interpreter lacks PyYAML: install it and re-run.
`INTENT=differs` → the local `eval_intent.yaml` stays as it is. Tell the user in one line: "The
intent the backend used differs from your `plurai_sim/eval_intent.yaml`; yours is unchanged, and
the backend's copy is at `plurai_sim/artifacts/eval_intent.backend.yaml`." `same` / `written` →
say nothing.

Then READ `policies_config.yaml` (you must validate it — see below) — the read is the
one context cost this step genuinely needs; `generation_trace.md` stays on disk unless a
warning sends you into it. The files:

- `policies_config.yaml` — the generated policy set
- `generation_trace.md` — backend Claude's reasoning trace (operator
  forensics)
- `eval_intent.yaml` — the input echoed back, saved as `$ART/eval_intent.backend.yaml`

(A `MISSING:<file>` line above means that file did not reach disk: no `plurai` CLI in this
repo (the resolver's stderr says so), a backend predating `GET /artifacts/{id}/files/{path}`, or
a failed fetch (the command's stderr names it). Fall back to `fetch_artifact(eval_set_id)` for
those files and write them yourself, the intent to `$ART/eval_intent.backend.yaml` with the same
compare.
**Narrow any re-fetch** on that fallback path: check `get_artifact_meta`'s
`detail.files[]` manifest (a `[{path, size_bytes}]` list, nested under `detail` like every
other artifact field) and pass `paths=['policies_config.yaml']` — large artifacts exceed the
MCP result ceiling. The key is OMITTED, not `[]`, when the backend found no files, so an
absent `detail.files` means "no manifest" — never read it as an empty artifact.)

Also write `<agent>/plurai_sim/.eval_set_id`
(one line) — the `/edit-evals` skill reads this to find the session's
underlying eval set.

Stage both — `policies_config.yaml` is a mirror of a backend set, and without the id beside
it a clone cannot tell WHICH set it mirrors or whether it is still in sync:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" policies_config.yaml .eval_set_id
```

**Environment manifest (if this repo uses named envs).** If `plurai_sim/envs/`
exists, also record the eval set id into the active env's manifest:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
python "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" write "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" --eval-set-id <eval_set_id>
```

Skip if there's no `envs/`. See the `manage-envs` skill.

Then run the local validator. **What it is for, and what it is NOT.** It is a LOAD CHECK on the
bytes that just landed in the customer's repo — does the local toolchain, at ITS schema version,
read this file the way the backend that wrote it meant? It is not a second opinion on the
policies: the backend's findings on a policy BODY (the generation job here, `/edit-evals`' in-turn
gate later) are evidence, and you decide on them per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`
— never by re-grading the body locally. (`verify_policies.py` grades on this
machine; the only call it makes is a best-effort read of the box's sandbox namespace, so a
customer with no backend at all can still validate hand-edited YAML offline):

```bash
# No sandbox-namespace preamble: the verifier reads GET /sandbox-helpers off
# the configured backend itself and falls back to the vendored snapshot on any failure.
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/generate-evals/scripts/verify_policies.py" \
  <agent>/plurai_sim/policies_config.yaml
```

**Read stderr, not just the exit code.** Exit 2 (`✗`) is a file the loader refuses — an
`invariant` finding: repair it (an `/edit-evals` session on this set) or regenerate; nothing
overrules it. A `⚠` line is ADVISORY: the set loads and exit is 0, but exit 0 does NOT mean
clean. Decide on each per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md` (advisory procedure) —
repair it through `/edit-evals` when its remedy is in reach, otherwise continue; a `⚠` is never
by itself a reason to stop the flow. In particular a `⚠ … (reasoning_expression)` line means
every result row for that policy ships `(reasoning_expression failed: …)` in place of its
evidence: the verdict still computes, so nothing downstream complains, and the evidence field is
dead for the life of the eval set — a remedy in reach.

The `--sandbox-names` flag is **optional and rarely needed** — the script reads the live
namespace off the configured box on its own, and falls back to the one vendored beside it
(`sandbox_namespace.json`, a snapshot of the backend's namespace that ships with the plugin) with
a NOTE on stderr whenever it cannot, so it stays stdlib-only and runs with no backend at all.
Pass the flag only to grade against a capture you took yourself. What it will not do is quietly
split the difference: a `--sandbox-names` file it cannot parse is an ERROR, because the caller
named a namespace and grading against a different one is how a real helper gets reported as an
unbound name (and vice versa). A hand-written `curl … && --sandbox-names` preamble is the shape
that ERROR is aimed at — a proxy or SSO endpoint answering 200 with a JSON error body satisfies
`curl -sf` and hard-fails the verification the fallback would have carried.

(The script lives in the PLUGIN, not the customer repo — there is no plugin-root `.venv`, so
run it with the CUSTOMER project's interpreter. Use `$PLURAI_PY` as shown (it prefers the project's venv): a bare
`python3` is typically the system/Homebrew one, which has no `pyyaml` and exits 3 every time.
Any interpreter with `pyyaml` works; the script exits 3 naming the offending interpreter's
path, so a wrong choice is never silent.)

**What the validator refuses (exit 2) — what the backend's loader refuses.** Two rows are deliberately
stricter (a missing `type`, which loads as a code-generated `custom` agent, and `debate_system`),
and a few loader rules are not mirrored yet (`verdict_tier` / `applies_to_polarity` values,
`terminate_values` types, a `file:` prompt that is missing or escapes the config dir) — the
backend's own load gate still refuses those:

| requirement | rule |
|---|---|
| required fields | `type`, `metric_type` (`boolean` \| `int` \| `float` \| `categorical`), `eval_metric_description` (non-empty), `confidence_threshold` (a number in [0, 1]); `evaluation_scope`, when present, is `turn_based` \| `end_to_end` (absent, it loads as `turn_based` — a `⚠`, as is a missing `display_name`). An unknown key is a `⚠`: the loader forbids one, but this plugin's key list may lag a newer backend. |
| closed sets | `severity` (`high` \| `medium` \| `low`), `untriggered_verdict` (`not_applicable` \| `pass` \| `fail`); no `generate_graph: true` on a runtime-built type. |
| `violated` | empty, or the form its `metric_type` takes: boolean `['true']` \| `['false']` (and no `labels`), numeric `['above']` \| `['below']` (with a `violation_threshold`), categorical a subset of `labels`. `labels` is a list of strings. |
| `code_eval` | a non-empty `expression` that compiles as ONE Python expression, exactly as the loader compiles it (a multi-line scalar is joined into one line first; leading whitespace refuses). What it then does in the sandbox — an unbound name, a forbidden builtin, a vacuous binding — is a `⚠`. |
| `beat_resolution` | names its slice in `additional_params`: **exactly one** of `metric` (`coverage` \| `accuracy`) or `beat_id` (`abs_<fact_id>`). The slice fixes the rest — `metric` ⇒ `metric_type: float`, `violated: ['below']` **and** a `violation_threshold`; `beat_id` ⇒ `metric_type: boolean` and `violated: ['false']`. It carries NO `system_prompt` (an `expression` is ignored — a `⚠`): the judge is server-side, so a body field here grades nothing. |
| `hybrid` | requires a non-empty `hybrid_module: <path>` pointing at the generated judge program, relative to the eval-set `out/` dir. `/optimize-eval` writes it; hand-authoring a `hybrid` policy without it fails. |
| `llm_as_judge` | a non-empty `system_prompt`. A `{{ variable }}` in it, or a boolean judge in violation form (`violated: ['true']`), is a `⚠`. |
| the policy-count floor | only an EMPTY file is refused. Below `min(5, 2 + <flow count>)` the validator warns and names the fences that still run — `eval_coverage_audit` for ungraded targets. Read it; the warning is not a licence to skip them. |
| `retrieval_metric` | `metric_type: float`. `additional_params.tool_name` naming the retrieval tool is a `⚠` when absent: retrieval is then scored across ALL tool calls — almost never intended. |

**Sandbox-helper caveat — the vendored namespace can drift in EITHER direction.** The namespace
`verify_policies.py` grades against is vendored from the backend, so it is a snapshot, and a snapshot
is wrong in two ways. The script prefers a LIVE read of the box and falls back to the snapshot;
the `NOTE:` lines on stderr say which one graded, so read them before acting on either symptom:

- **Vendored NEWER than the box** — the snapshot lists a helper the box does not yet bind
  (`contains_script(text, script)` — the supported way to check a script/alphabet, since the
  sandbox has no `ord` and no regex — is the usual one). Symptom: the policy VERIFIES here and is
  WITHHELD by the backend, or comes back as an unbound name.
- **Vendored OLDER than the box** — the snapshot is missing helpers the box binds. Symptom is the
  mirror image and much easier to act on wrongly: `verify_policies.py` reports a WORKING helper
  as unbound, and an author offline rewrites a correct policy around a name that was never the
  problem.
- **A NAME THAT DID NOT MOVE AND A MEANING THAT DID** — the third direction, and the only one a
  live read does not settle. `names_by_scope` types each binding per scope, and a RETYPE leaves
  both name sets identical: `final_step.turn_based` went `populated` → `constant_false` and every
  set-difference comparison stayed silent. Symptom: `verify_policies.py` PASSES a `turn_based`
  `code_eval` that the backend's own `scope_vacuity_offenders` then refuses at
  `end_edit_session` — a clean verdict on a policy the sandbox will not take. Both halves are now
  checked (the drift NOTE compares the matrix VALUES, and the vacuity lint reports a scope token
  it does not recognise instead of assuming `populated`), but a token newer than this plugin
  still leaves the expression ungraded on that axis and says so.

A live read settles the first two directions; **the third is settled only by a plugin update
that refreshes the vendored matrix**. Until you update, trust the backend's own refusal at
`end_edit_session` over the snapshot. `NOTE: the vendored sandbox namespace
differs from …` names the drifted names AND any retyped `name.scope: old -> new`; `NOTE: grading
against the vendored sandbox snapshot, which may be older than the backend` means no live read
happened at all, so no direction has been ruled out.

**Some backend audit lines are REPAIR TRIGGERS, not notices.** The generation job already
audits the authored set against the linked scenarios — with no LLM, just disk and AST — and
reports each line it found as an advisory finding on `detail.gate_authority.findings` (the
line is the finding's `evidence.line`, the policy its `subject`). A `notice.actionable`
finding names its family in `evidence.family`. A finding of one of these families names a
policy that will grade NOTHING as shipped, so it is fixed here, before any scored run:

- `judge_applicability` — the guard is false on every linked scenario; the policy is
  skipped set-wide.
- `premise_coverage` — a declared premise no policy gates on.
- `rubric_polarity` — the verdict is inverted against the convention.
- `url_membership` — a membership criterion whose reference list nobody supplies.
- `knowledge_applicability`, `knowledge_grounding` and `memory_coverage` — ground truth
  exists that nothing grades, or a probe no policy reads.
- `short_of_rule` — the ledger credits an agent rule to a policy that cannot fail part of
  it. Answer it in the ONE Step 2b `/edit-evals` session with exactly one of the three edits
  the line names (widen, re-credit, decline with `ungradeable-from-artifacts (<artifact>)`).
  It is never "accepted": coverage the set claims and does not grade is a false report, not
  a nicety.

**The per-policy checks report on `detail.gate_authority.findings`.**
Every check that fired on an authored policy left one `eval.policy.*` finding whose `subject` is
that policy's name. Dispatch on `authority` per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`; the
`subject` names the policy the repair touches:

- `invariant` — the policy was WITHHELD: it is on `detail.dropped_policies`, and its authored
  body is in `observed.policy_body`. Re-author it from that body, following `remedy`.
- `advisory` — the policy SHIPPED as authored. What decides is what it does to a score:
  - **a false RED** — `eval.policy.flags_benign_run`, `judge_violation_form`, `term_collision`,
    `message_source`, `record_vocabulary`. Repair it before any `/simulation-run`. Keep it as
    authored only when you hold the evidence an overrule would cite (the customer's repo
    file:line, the PRD section) that the check is right for this agent; it holds nothing, so
    nothing is sent.
  - **an error cell or a check that cannot fail** — `errors_on_benign_run`, `lambda`,
    `threshold_unreachable` and the rest. No wrong verdict; it may ship. Fold its repair into
    the session if one is opening anyway.

Batch every one of them into **ONE** `/edit-evals` session and fix them together; a scored
run is not needed to know these are broken, and finding them later costs a full review pass.
Lines outside this list keep the reporting posture below. You decide alone: never ask the
user about a finding, and never put one in a report.

If the validator rejects:
1. The backend's own validation pass should have caught it — read the
   `detail.gate_authority.findings` from the final
   `job_status(id=eval_set_id)` response YOURSELF (they ride `detail` in the done Job
   envelope):
   fix what you can without narrating; if a finding materially limits the
   eval set and can't be fixed from here, state the impact in ONE plain
   sentence — never paste the finding text.
2. If the validator complains about a different issue (schema drift, a
   newly-rejected pattern), tell the user to ask for the evals to be regenerated;
   do NOT hand-edit the YAML.

## Step 6: Report + tell the user how to edit the evals

Print a summary:

Backend LLM cost figures (`total_cost_usd` etc.) are INTERNAL telemetry — never show them to the customer, in this summary or any progress line.

- Path written: `<agent>/plurai_sim/policies_config.yaml`
- Eval set id: `<eval_set_id>`
- Counts: total policies, breakdown by **every** type present —
  `code_eval`, `llm_as_judge`, `retrieval_metric`, `hybrid`, `beat_resolution`.
  All five are first-class (`ACCEPTED_TYPES` in `verify_policies.py`);
  enumerating only the first two omitted a retrieval-agent customer's PRIMARY
  eval bundle from their own summary, since `retrieval_metric` is the pair the
  backend MANDATES for a retrieval-tool agent, `hybrid` is what
  `/optimize-eval` adopts, and `beat_resolution` is the knowledge + absence bundle
  the backend emits for a knowledge-grounded agent.
- **Metrics that couldn't be generated, if any.** Read `detail.dropped_policies`
  from the final `job_status(id=eval_set_id)`. It lists WITHHELD policies only — ones the
  backend proved could never score, kept out of the published set; a policy that shipped
  with an advisory finding is not on it. Step 5's session re-authors what it can, so
  report only the ones still missing after it. The customer
  owns the decision about those — so name them, in product terms: "N metric(s)
  couldn't be generated: <what each was meant to check>. Ask me to
  add them once <the missing input> exists."
  This is the one exception to the warnings posture below: report WHAT could not
  be generated, never the backend's warning text, exception names, or field names.
  Say nothing when the list is empty.
  **Phrase it as a generation RESULT, not as suite coverage.** `/generate-evals`
  has no flow marker of its own, so inside `/simulation-generate-suite`'s window
  this summary IS style-validated by `report_guard`, and the doctrine's ban on
  SUITE-coverage narration is live here: a "Coverage gaps:" label, or "… is not
  covered by the generated set", blocks — and re-rendering the same prescribed
  sentence burns strikes to the 3-strike stand-down, which also disarms
  `ask_guard` for the rest of the session. "couldn't be generated" / "has no
  matching metric in the shipped set" say the same thing and pass.
- **Intent reconciliation — declined-at-authoring gaps.** `detail.dropped_policies`
  only lists policies that were AUTHORED and then withheld; a metric the backend
  DECLINED during authoring (reason recorded only in `generation_trace.md`) never
  appears there, so the bullet above can stay silent about a real gap. After
  reporting withheld policies, reconcile the customer's `eval_intent.yaml` (it's
  local — you sent it) against the shipped policy list (`policies_config.yaml`, or
  `detail.policies` / `policy_names` from the done job): for EACH failure mode under
  each axis (or, for a v1 file, each `goals` / `must_catch`
  item), judge whether some shipped policy plausibly covers its MEANING. This is a
  SEMANTIC judgment you make by reading both documents — keyword/string matching is
  FORBIDDEN (policy names rarely echo the intent's wording). For any intent item
  with no plausible match, say so in product terms: "<intent item> has no matching
  metric in the shipped set — likely declined during generation; ask me to
  add it, or adjust the intent." (Same phrasing constraint as the bullet above:
  "not covered by the generated set" is SUITE-coverage narration and the report guard
  blocks it.) Say nothing when every intent item has a match, or when no
  `eval_intent.yaml` was provided.
- **A metric declined because the AGENT cannot do the thing is a finding, not just a gap.**
  The two reasons an intent item ships uncovered look identical in the trace and are not the
  same fact: the metric was unauthorable (fix the intent), or the agent has no surface the
  metric could measure — a field it never returns, a capability no tool implements. The second
  narrowed what the suite tests, so it earns a `potential` section in `$ART/agent_findings.md`.
  So does a rubric you write to grade a decline as the correct answer. Append — the scan and the
  quality gate write their own sections there — numbering from the highest `## N` already in the
  file, and resolve `$ART` in the same Bash call as the write:

  ```markdown
  ## N. <what the agent cannot do, in domain language>
  - **status:** potential — may be intended
  - **severity if real:** high | medium | low
  - **finding id:** `agent.<stable_snake_case_name>`
  - **found during:** suite build
  - **evidence:** <the static fact, with its numbers>
  - **why it might be intended:** <one line>
  - **what the suite does about it:** <the carve-out — which metric, which rubric>
  - **how to confirm:** <the one check the owner runs>
  ```

  Record the typed fields in the same Bash call — the prune decides on them, not on the prose:
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/record_finding.py" "$ART/agent_findings.md" --id <finding id>
  --status potential --found-during "suite build" --severity <high|medium|low> --title "<the heading, without its number>"`. The `**status:**`
  line is what a reader sees — write it exactly as shown. If the scan already recorded this root
  cause, add your evidence to ITS section instead of opening a second one.

  The `**finding id:**` line is what lets this entry survive being pruned. Titles are not an
  identity: the same defect has been written five ways across builds ("for transitions the guides
  do not document", "for privacy-state changes", "for changes"), so nothing could match a
  re-confirmed finding to its predecessor. When an entry is struck, the prune carries its id onto
  the tombstone, and the next build's writer cites that id instead of opening a fresh defect with
  no history. Choose a name for the BEHAVIOUR, not for this instance of it, and reuse the scan's
  id if it already named this root cause.
- Next steps:
  - Use the eval set by id: `plurai-run-simulation --eval-set-id <eval_set_id> …` (the saved `.eval_set_id` is picked up automatically by `/simulation-run`)
  - Iterate on metrics: `/edit-evals` (opens a multi-turn session with
    the backend to add / modify / remove individual policies)
  - A scenario set drawn BEFORE this eval set existed was dealt BLIND to this set's demands, so
    the next draw passes `eval_set_id` + `previous_scenario_set_id` — the rule and its
    `planting_basis` branch live with the `eval_set_id` parameter in `generate-scenarios`, and
    are not restated here

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
<!-- END error-posture --> The ONE thing that is not internal is
which metrics are MISSING: a withheld policy is a gap in what the suite measures, so report it in
product terms per Step 6. Suppressing the raw text is right; suppressing the fact that a check
does not exist is how a customer comes to believe a suite covers something it does not.

## What NOT to do

- Do not author `policies_config.yaml` yourself. The backend does that —
  this skill orchestrates, doesn't generate.
- **Never use extend mode to FIX a policy.** `extend_eval_set_id` freezes the source set's
  policies and refuses at publish if any of them changed; a wrong threshold, an inverted judge
  or a stale rubric is `/edit-evals` on that set. Extend adds coverage the linked suite needs;
  it does not repair what is there.
- Do not surface the backend's reasoning trace (`generation_trace.md`)
  to the user by default — it's for operator forensics. Mention it
  exists; show it only on request.
- Do not skip the local `verify_policies.py` pass at Step 5 — and do not widen it either. It
  answers ONE question the backend cannot: whether this client's schema version loads the file
  the backend just wrote. Judging the policies themselves is the backend's at both authoring
  surfaces, so a local pass anywhere OTHER than on freshly-landed bytes is the retired one (see
  `/simulation-generate-suite` Step 2b, and `/edit-evals` Step 3b, which runs no validator at all).
- Do not blind-retry `generate_evals_start` repeatedly on failure. One
  silent retry for a transient failure (per the error posture above) is
  fine; beyond that, classify — the customer's intent_yaml might be the
  cause, and repeated retries just burn backend spend.
- Do not pass the customer's filesystem paths to the backend — only
  contents. The backend has no filesystem visibility into the
  customer's machine by design.
