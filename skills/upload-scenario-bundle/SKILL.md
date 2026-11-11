---
name: upload-scenario-bundle
description: >-
  Ground scenario preconditions in real records: pick records locally, one folder per scenario with a ROLES.md, and upload only those. /generate-scenarios then builds from the folders. Run after /simulation-scan-agent.
---

# Upload Scenario Bundle

You are the LOCAL Claude Code agent. The customer's agent has a per-scenario
precondition that should reference the agent's REAL data — a "current record" the
scenario places the agent on (e.g. a retail assistant's currently-viewed item, a
banking agent's active account, a support agent's selected ticket). When the
precondition's identity value (an id/code) is a REAL one from the agent's data
source, the agent's lookup tool resolves it to a record; when it's invented, the
tool finds no matching record and the dialog is grounded on something that
doesn't exist. (The agent doesn't crash — the precondition field is typically
optional — but the scenario is incoherent.) Left to the scenario generator, the
field is **LLM-hallucinated** (a made-up id). This skill fixes that by uploading,
per scenario, a folder containing the REAL record + a `ROLES.md` instructing the
generator to build the scenario from it.

`/ground-scenarios` routes here when the customer's material is records to start
from; arriving directly works the same.

The split that matters:

```
┌── customer's machine (you, via plurai-sdk) ─────────────────────────┐
│  SELECTION+AUTHORING (local), SCAN → ANONYMIZE (if anon.) → UPLOAD. │
│  1. Identify the precondition field + the real data source          │
│       (the fixture / index / DB the agent's tools read).            │
│  2. SELECT a representative SUBSET of REAL records — NOT the        │
│       whole dataset. Ask the user how many + which slice.           │
│  3. Per scenario, write a folder LOCALLY in a staging dir:          │
│        <scenario>/record.json        ← the EXACT real record        │
│        <scenario>/agent_context.json ← deterministic shape pin      │
│        <scenario>/ROLES.md           ← field→agent_context map      │
│       (optional set-level BUNDLE.md overview).                      │
│  4. Shell: plurai ingest-bundle --bundle-dir <dir> …                │
│       scan → ANONYMIZE (if anonymized: PII-strip, fail-closed)      │
│       → upload ONE archive → prints the single-archive MANIFEST     │
│       { lease_id, checksum, bytes, … } to stdout                    │
│  5. MCP: upload_scenario_bundle_start(manifest=…, overview_md?)     │
│       → UploadFinalizeResult { set_id, status: 'done', ... }        │
│       (set_id is server-minted, in this same call)                  │
│  6. MCP: get_upload_meta(set_id) → 'done'.                          │
│  7. Write plurai_sim/.scenario_bundle_id (no newline) +             │
│       .scenario_bundle_augmentation (chosen level) +                │
│       .scenario_bundle_intent (when + what for).                    │
│  8. Tell the user to run /simulation-generate-suite, then VERIFY.   │
└─────────────────────────────────────────────────────────────────────┘
                                   │ MCP
┌── Plurai-hosted backend ─────────┴──────────────────────────────────┐
│  upload_scenario_bundle_start(manifest) → FETCHES the single         │
│    archive by lease_id, extracts+verifies it, mints                 │
│    the set_id, VALIDATES every folder has a ROLES.md AND ≥1         │
│    material file (else RAISES), stores overview_md as the           │
│    set-level BUNDLE.md, returns the manifest immediately            │
│    (synchronous — no LLM build here). Storage is a backend service   │
│    reached via the diagnostic bearer — no separate                   │
│    storage target returned.                                          │
│  get_upload_meta(upload_id=set_id) → 'done' (sync, no poll).        │
│  Later, generate_scenarios_start(scenario_bundle_id=<set_id>)       │
│    stages each folder as provided_materials/ + builds from it.      │
│  No TTL eviction — persists until delete_upload.                    │
└─────────────────────────────────────────────────────────────────────┘
```

**Why the ingest runs locally.** Only the customer's machine has the agent's real
data source. `plurai ingest-bundle` scans the staged bundle dir, then resolves this
env's anonymization posture from `plurai.config.yaml` (the `anonymize:` key, default
raw): under **anonymized** posture it **anonymizes each record fail-closed**
(reaching INSIDE the record content), exactly as before; under the **raw** (default)
posture it uploads the records as-is and prints a loud `RAW (anonymization off)`
banner — that upload is governed by the customer's DPA with Plurai. Either way it
bundles every folder into ONE compressed archive and uploads that ONE archive to the
storage service. The backend fetches and extracts that single archive by `lease_id`.
Under anonymized posture, anonymization is format-preserving, so the records still
ground the scenarios faithfully — only PII *values* are pseudonymized, the structure
is unchanged.

**This is a plain file store, not a corpus build.** Like `/upload-doc-examples`
and unlike `/build-rag-corpus`, there is no LLM-driven enrichment, no testset
synthesis, and no backend LLM credentials required. The backend simply receives
and stores the folders; the conditioning happens later, inside
`/generate-scenarios`.

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

**Use this** when a precondition field (a "current record", a "selected record",
an "active account") must be a REAL record from the agent's data **and you want
per-scenario control** over which records get exercised, with **no corpus to
build**.

Three things you MUST tell the user up front, because they change what they get:

- **Conditioning, not a deterministic copy.** At `augmentation_level=0` the
  backend is instructed to reproduce the provided values **VERBATIM**, but the
  values reach `agent_context` *through the generation LLM* — it is best-effort,
  not a guaranteed byte-copy. The only hard backend guarantee is that the folder
  was staged. **So a VERIFY step after generation is MANDATORY** (Step "Verify"
  below) — a clean run is NOT proof the value was grounded.
- **Bypasses the dimension-graph WALK — not the corpus.** When a bundle is used,
  `/generate-scenarios` skips the graph walk, so the walk-driven dials are
  **ignored**: `count` (fan-out is `folders × (1 + augmentation_level)`),
  `interest`, dimension diversity, and the graph's knowledge-FLOW injection. If
  the agent ALSO needs broad flow/dimension coverage, that is a SEPARATE,
  non-bundle run.
- **`knowledge_bases` ARE honored — corpus knowledge-Q&A still runs.** Attaching a
  corpus to a bundle run is NOT a no-op: each imported scenario's SDK session wires
  the kb tools and drives the knowledge Q&A over the corpus, conditioned on the folder's real
  provided materials. So a chunks-only product corpus feeds gradable
  product-knowledge-extraction questions even though the bundle (not the corpus)
  owns the world-state. `memory_challenge` is likewise honored — the elliptical
  memory probe rides a complication. **Attach the corpus** when the agent answers
  knowledge questions; don't drop it just because it's a bundle run.
- **Only corpus WORLD-grounding (`ground_from`) is skipped.** The bundle path does
  not materialize corpus `agent_context` / world-state entities — the bundle's
  provided materials own the world. If the schema declares `ground_from:
  {entity_type: …}`, that WORLD-grounding annotation is **inert for world-value
  sourcing** on a bundle run (the bundle drives the value). This is orthogonal to
  the corpus Q&A above: the corpus still answers questions, it just doesn't seed the
  world here.

**Do NOT use this** for a document-extraction agent's example formatting — that's
`/upload-doc-examples`. And if you want MANY scenarios sampled across a LARGE real
pool (rather than a hand-picked record per scenario) with deterministic grounding
that keeps the graph walk's diversity, use `/build-rag-corpus` + a `ground_from`
schema annotation instead (see `/analyze-simulation` dimension 3).

## Entry points

Three ways in. The default is the whole lane; the other two split it at the publish
boundary, so one set can hold both kinds without either lane restating the other's steps.

- **Full lane** (the default — direct invocation, or `/ground-scenarios` routing here).
  Step 1 through Step Verify, staging into this lane's own `plurai_sim/scenario_bundle`.
  Every precondition below applies.
- **Author-only** (`/ground-scenarios` both-lanes route, first leg). Steps 1 through 3 ONLY,
  authoring into the shared dir the router names instead of `scenario_bundle` — then **stop
  and return**. Do not ingest, do not call any MCP tool, do not write the markers. The
  converter has not run yet, so the dir is half a set; publishing it here would ship the
  records alone under their own `set_id`. Two things bind on this entry that do not on the
  full lane: **every folder needs its `ROLES.md` before you stop** (the converter refuses the
  next leg over one that lacks it), and **do not write a `BUNDLE.md`** — the converter owns
  the set-level overview whenever it runs and rewrites it unconditionally, so a hand-authored
  one is replaced without a word. Precondition 1 does not gate this entry; every other one
  does. If a folder you author holds the very records one of the conversations to be converted
  refers to, say its NAME when you return: the next leg converts that export with
  `--into <folder>` so the pin lands inside it instead of in a folder of its own.
- **Publish an existing dir** (`/ground-scenarios` both-lanes route, last leg). Steps 4 through
  Verify on a dir that is already fully authored — by this lane, by the converter, or by
  both. Skip Steps 1–3 entirely: the folders exist, and the records in them were selected and
  confirmed on the author-only leg. Pass `--workspace-root "$PLURAI_DIR"` to
  `ingest-bundle`, the same spelling the converter ran under, so posture and the audit log
  resolve from one place. THREE things to carry into Step 4 and Verify: **read the dir's
  root `BUNDLE.md` and pass it as `overview_md`** — the converter writes one on every dir it
  touches, `ingest-bundle` excludes it from the archive, and this start call is its only way
  to the backend, so omitting it publishes at exit 0 having dropped the set-level instruction
  the resumption Verify goes on to check; the folder count you confirm is **both kinds
  together**, counting a folder that holds both ONCE; and this lane's Verify covers only the
  grounding it authored — resumption is checked by `/trace-to-scenarios`' own Verify step,
  which you also run. A folder holding both kinds needs BOTH: its grounding verified here, its
  resumption verified there.

Everything else in this file — the anonymization posture rules, the error posture, the hard
rules — holds identically in all three.

## Preconditions

1. **`plurai` MCP server reachable** (auto-registered via the
   plugin manifest; the backend is the configured one, the one the saved Plurai login names;
   a remote backend requires that login. Preflight with
   `list_uploads(kind="scenario_bundle")` — the generic upload surface, which is
   what this backend actually serves; there is no per-kind bundle listing tool.
   It proves the server answers and shows any bundles already stored, and that is
   ALL it proves: a backend with no `scenario_bundle_storage_root` omits the kind
   from the id space entirely, so the call returns an EMPTY page rather than
   raising — the same answer a configured backend with no bundles yet gives.
   **Bundle storage being disabled therefore surfaces at Step 5**, where
   `upload_scenario_bundle_start` RAISES
   `"scenario-bundle tools are disabled — no scenario_bundle_storage_root"`. When it
   does, the operator hasn't enabled bundle storage — unfixable from here: run the contact hand-off (`stop_message.py contact --done`). Don't read the empty
   preflight page as that verdict, and don't read it as "no bundle storage".
2. **`/simulation-scan-agent` has run** — `plurai_sim/plurai.config.yaml` and
   `scenario_variables.yaml` exist. `scenario_state_schema.yaml` is IDEAL but it is an
   ENV-GEN output, so it does not exist yet between `/simulation-scan-agent` and
   `/simulation-generate-suite` — in that window take the `agent_context` field name and
   its sub-fields from `plurai.config.yaml`'s `agent_context.fields` instead. Uploading
   early is still correct: the bundle is consumed at generation time, after the world is up.
3. **The agent's real data source is locally readable** — the fixture file /
   index / DB the agent's tools actually read (e.g. the catalog/account/records
   file behind its lookup tool). You select records FROM it.
4. **The precondition field is identified** — the `agent_context` field name plus
   which record keys it carries (the identity key — an id/code — plus its
   companion fields).
5. **`plurai` SDK installed in the client's venv** (the venv the agent uses; install
   it as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints).
   `plurai ingest-bundle` (Step 4) scans the staged bundle dir and,
   per this env's anonymization posture, either anonymizes each record (anonymized
   posture) or uploads it as-is (raw, the default) — then uploads the ingested
   objects. Step 4 resolves the exact binary via the plugin's
   `${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py` (activated venv `$VIRTUAL_ENV` → repo `.venv` →
   PATH), so a stale global install can't silently shadow the client's pinned SDK.
**Anonymization posture — ask ONCE, here, if `plurai.config.yaml` has no `anonymize:` key.**
Setup deliberately does not ask (it uploads nothing); this skill is the first point where
real customer data leaves the machine, so this is where the question is meaningful. If the
key is ABSENT, ask before uploading, then write the answer into `plurai.config.yaml` as a
top-level `anonymize: true | false` so it is never asked again for this env — only an answer the
run driver gave: a default is not a declaration, so do not write it. (Inside a
driving command's flow window this is a sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction anonymization-posture`.) If the key is
already present, use it and do NOT re-ask — it is read-never-overridden, and changing it on
an env that has already uploaded artifacts is refused fail-closed (mixed-posture).
**Under `run_driver: headless` there is no default for `anonymization-posture`: the run stops until
`anonymize:` is declared.**

> Does this data contain PII you want scrubbed locally before it is uploaded to Plurai? If
> yes, everything uploaded to build this env is anonymized locally first (simulation results upload as they are) — this needs `plurai-simulation-sdk[anonymize]`
> plus a spaCy model installed locally, and ingestion HARD STOPS if either is missing. If no
> (**default**), data uploads as-is, processing governed by your DPA with Plurai; every
> upload still records its posture in the manifest and `.plurai/uploads/audit.log`.

6. **Only if this env's anonymization posture is anonymized** (`anonymize: true`
   in `plurai.config.yaml` — default raw): **`plurai-simulation-sdk[anonymize]` + a spaCy English
   model are installed** — the pre-egress scrub runs INSIDE `plurai ingest-bundle`
   (Step 4) under that posture, reaching into each record's content; it is
   fail-closed and refuses to run without them: install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints),
   then `python -m spacy download en_core_web_lg` (any of `en_core_web_{lg,md,sm}`
   works). Under the raw (default) posture this precondition doesn't apply — no
   anonymizer required.
7. **No separate storage credential** — `plurai ingest-bundle` uploads the ingested
   objects (anonymized or raw, per the env's posture) to the backend's object-storage
   service over the same diagnostic connection as the MCP tools above
   (the saved login); the endpoint is derived,
   not passed in. Zero new credential plumbing.

## The flow

### Step 1: Identify the precondition field + the real data source

Read `plurai_sim/scenario_state_schema.yaml` (`agent_context_fields:`) and pick
the field that must be grounded in real data. Read `plurai.config.yaml` to find
which tool reads the data source and where that source lives on disk. **Confirm
the resolved data-source path with the user** before reading it.

If the field is already annotated with `ground_from:`, tell the user that
annotation is **inert for world-value sourcing on this bundle run** (the bundle
drives the value) — this is expected, not a conflict.

### Step 2: Decide how many scenarios + select REAL records (a subset)

**ASK the user about scope — do NOT silently use the whole dataset:**

> A scenario bundle uploads one real record per scenario folder. You usually want
> a small, representative SUBSET — e.g. 5–10 records spanning the meaningful axes
> (type / value band / status) — not the entire dataset. **Recommended: start
> with a focused subset.** How many scenarios, and which slice of the data?

Then read the source LOCALLY and select that many records, chosen for spread
(across the meaningful axes) so the bundle exercises variety. One record
→ one scenario folder. **Show the user the chosen records** (the identity key +
a human label) and get confirmation before authoring.

### Step 3: Author each scenario folder LOCALLY

Create a local staging dir, e.g. `plurai_sim/scenario_bundle/`, with one subfolder
per selected record. (On the author-only entry, use the shared dir the router named
instead — the converted folders land beside yours in it.) Each folder needs:

- **A variable file** — the EXACT real record, e.g. `record.json`. Keep it
  to the record's real fields (the subset the precondition needs); do NOT invent
  fields or values. JSON or YAML both work — the backend never parses it, it just
  stages it for the generator to Read.
- **An `agent_context.json`** (STRONGLY RECOMMENDED — the deterministic shape pin).
  Map the record into the **exact `agent_context` shape the customer's pipeline
  expects** — real values, real nesting, the field name + key names from
  `scenario_state_schema.yaml` (`agent_context_fields:`). You know both halves (the
  real record + the schema shape), so write the concrete object. When present the
  backend applies these keys **corpus-authoritatively** (via the same grounding
  write-site the graph path uses) — the uploaded keys WIN over the generator's, so
  the grounded field's VALUES cannot drift through the generation LLM (the failure
  mode ROLES.md prose alone can't prevent). Include ONLY the grounded precondition
  field(s); leave persona/first_talk to the generator. Absent → the backend falls
  back to LLM-mediated mapping (best-effort).
- **A required `ROLES.md`** — freeform prose telling the generator how to use each
  file + how to build the scenario around the record. With `agent_context.json`
  present the identity SHAPE is already pinned deterministically, so ROLES.md is
  the narrative/role channel (what the scenario is about); without it, ROLES.md is
  the ONLY (LLM-mediated) field→`agent_context` mapping channel. Use the template below.

Optionally add a set-level **`BUNDLE.md`** (one paragraph describing the bundle as
a whole + the global rule, e.g. "every `record.json` is a REAL record from the
agent's data source; set the precondition field to its exact field values"). **Not on
the author-only entry** — there the converter writes that file, from both kinds of
folder, and replaces whatever is there without printing anything.

**Folder names (and every path segment)** must contain no `/` or `\` and must not
be exactly `.` or `..` — a period *within* a name like `record.json` is fine.
They become jailed path segments backend-side. **Prefer plain ASCII** stable names
like `record_0042` or `record_01`.

#### ROLES.md template

```markdown
# Scenario materials — roles

## record.json
This file holds ONE REAL record from the agent's data source (the source its tools
read). It is the EXACT record the scenario's precondition references (the "current
record").

**Build instruction (fidelity-critical):**
- Set the `agent_context` precondition field to these EXACT field values:
  - `id`         ← record.json.id          (do NOT change or invent an id)
  - `name`       ← record.json.name
  - the remaining keys ← the same keys, verbatim
- Do NOT substitute a different record, a made-up id, or a generalized example.
  The agent's lookup tool resolves this id against the real source — a wrong
  or invented id finds no matching record, so the dialog references something that
  doesn't exist.
- Presentational keys the schema lets you author may be filled naturally, but the
  IDENTITY keys above are authoritative and must match record.json.

## (the scenario itself)
A user acting on THIS record asks a realistic question about it. Build persona /
first_talk / scenario_target around this specific record.
```

#### agent_context.json (the deterministic shape pin)

Write the record already mapped into the pipeline's exact `agent_context` shape — the
field name + nesting from `scenario_state_schema.yaml` (`agent_context_fields:`), real
values from `record.json`. Only the grounded precondition field(s); the generator
authors the rest. Example (a "currently viewed room" precondition nested under
`page_context`):

```json
{ "page_context": { "room_id": "R-3021", "title": "…", "nightly_rate": 189, "currency": "EUR" } }
```

Match the schema's declared container + keys exactly; if the schema declares a flat field
(no nesting), emit it flat. The backend applies these keys corpus-authoritatively (they win
over anything the generator authors under the same field), so their values don't drift with
session load.

### Step 4: Ingest (scan → anonymize per posture → upload), then start the set

**1. Run the SDK ingest command.** ONE invocation scans the staged bundle dir
locally, then resolves this env's anonymization posture from `plurai.config.yaml`
(the `anonymize:` key, default raw): under **anonymized** posture it
**anonymizes every record (reaching INSIDE the record content, fail-closed)**
exactly as before; under the **raw** (default) posture it bundles the records
as-is and prints a loud `RAW (anonymization off)` banner. Either way it bundles
the result into a single compressed archive, uploads that ONE archive to
the backend's storage over the same diagnostic connection as the MCP tools (one
lease for the whole set), and prints the **single-archive manifest** to stdout:

```bash
# Resolve WHICH plurai runs: the Bash tool does not activate the client's venv, so a bare
# `plurai` misses or silently hits a stale global install. The resolver picks the client's
# activated venv ($VIRTUAL_ENV) → the repo .venv → PATH, or fails with guidance.
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-bundle)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# Preflight: bash cannot see which backend the MCP server is bound to. Call the read-only
# backend_identity() MCP tool first and paste its object here verbatim — that comparison is the
# one thing neither the CLI nor this shell can make alone. The pin is NOT re-checked here; the
# CLI owns it (below). Omitting the argument only downgrades this to a warning.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-bundle \
    --bundle-dir plurai_sim/scenario_bundle
#   --bundle-dir is the local staging dir you authored in Step 3
#   stdout → the single-archive manifest JSON (capture it verbatim)
#   stderr → first the resolved TARGET line (`plurai ingest-bundle: uploading to <backend> (target
#            from the configured backend; <pin verdict>)`),
#            then a human-readable disclosure preview (folder/file counts, bytes, scrub summary)
#            The target line prints on EVERY invocation, so it is there even when the run is
#            refused. READ IT: it is the authoritative statement of which backend this upload
#            reached. Nothing downstream re-checks it — there is no workspace ingest pin,
#            so `assert-backend-identity.sh` before the upload is the guard.
#   uses the configured backend and token (or the saved login) —
#   the same the backend connection the MCP tools above use; no storage flags needed.
```

A `WARNING (disclosure: home_named)` line from `ingest-bundle` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

There is no `--set-id` — the set doesn't exist yet; the backend mints `set_id` in
the next call, from this manifest. `--bundle-dir` is the staging dir from Step 3
(the CLI scans it — there is no separate extract step; the bundle is a
pre-materialized directory whose relative paths are the addressing, and the root
`BUNDLE.md` is auto-excluded — it rides the start call as `overview_md`).

- **Capture stdout as the manifest and hand it to `upload_scenario_bundle_start`
  UNMODIFIED** (opaque — do not interpret or edit its contents). It is a JSON
  object shaped `{lease_id, checksum, bytes, posture, anon_key_fingerprint}` —
  the SDK streams every folder (scrubbed under anonymized posture, as-is under
  raw) into ONE `.tar.gz` and uploads it as ONE storage object; `checksum`/
  `bytes` describe that single archive (no per-member list on the wire — the
  deterministic tar+gzip means the server verifies the whole archive by its
  checksum, so member integrity is implied). Parse the stdout text into the
  object and pass it to `manifest=` **as that object** (change nothing). The
  SDK writes the disclosure preview (to stderr) + `.plurai/uploads/audit.log`
  itself; relay the preview counts, never record bodies.
- **Fail-closed — a non-zero exit is a HARD STOP.** Under **anonymized** posture
  this can mean anonymization could not run (Presidio / spaCy missing); under
  **either** posture it can also mean the storage target is unset — **NOTHING
  was uploaded**. Read the error yourself, fix the local environment silently
  (missing packages, unset connection vars — see Failure modes), and re-run;
  never show the raw error to the user.

**2. Start the set**, handing the backend the manifest:

```
upload_scenario_bundle_start(
    manifest=<the captured stdout manifest, opaque>,
    overview_md=<raw posture: the set-level BUNDLE.md content, verbatim; anonymized posture: the content of .plurai/uploads/overview_md.md, the file ingest-bundle named>,  # MANDATORY when the dir holds a BUNDLE.md
)   →  UploadFinalizeResult { set_id, status: 'done', ... }   # scenario folder + file counts
```

`set_id` is the returned `set_id` — the backend mints it here, in this one call;
there is no earlier `set_id` to thread through. The backend **fetches the single
archive from storage by `lease_id`**, verifies its checksum/size,
extracts it under hardened caps (path-jail, decompression-bomb limits),
**validates every folder has a `ROLES.md` + ≥1 material
file** (else RAISES — no set created), stores `overview_md` as the set-level
`BUNDLE.md`, and returns the folder/file-count result. It is **synchronous** — no
async build. `overview_md` is the set-level `BUNDLE.md` (Step 3) — a freeform
overview spanning all folders; it is NOT a per-folder file, so pass it here (the
CLI already excludes it from the uploaded objects). **Whenever the staging dir
holds a `BUNDLE.md`, passing it is MANDATORY, not a nicety.** This call is the
only route it has to the backend, and omitting it does not fail — the set is
created, minus whatever only the overview said. On a dir the converter wrote,
that missing half is the instruction that the authored opener and policies must
address what REMAINS of each arc rather than what already happened, which is
exactly what the resumption Verify then checks. Read the file and pass its
content verbatim; hand-authoring a replacement is not the same thing. **Under
anonymized posture, when `ingest-bundle` printed `overview_md: send the content of
<path>`, pass THAT file instead**: it is `BUNDLE.md` anonymized like the archive (folder
names pseudonymized, content scrubbed), and the original would send what the scrub removed. Confirm the folder count
matches your staging dir and each folder's `material_count >= 1`. **Retrying with
the SAME manifest (same `lease_id`) is idempotent** — it returns the EXISTING set
instead of creating a second one.

**Disclosure + audit move to the SDK on this path.** The `plurai ingest-bundle` Bash
call is the consent chokepoint (normal Bash approval, naming the exact command), and the
CLI writes the preview + `.plurai/uploads/audit.log` (the previewed bytes are already
pseudonymized). `upload_scenario_bundle_start` / `get_upload_meta` carry no record bytes
(only the opaque single-archive manifest + the optional overview).

### Step 5: Confirm the set is done

`upload_scenario_bundle_start` is **synchronous** — it fetches, validates, and
writes the set immediately, so a single confirming read is enough. There is **no
`upload_scenario_bundle_status` tool** — upload status is part of the generic upload
surface; use `get_upload_meta` (upload status folds into it because the call is
synchronous):

```
get_upload_meta(upload_id=<set_id>)   →  status: 'done'
```

The only two status values are `'preparing'` (a stale/in-progress state you should not
normally observe on this path) and `'done'` (finalized + stored). E.g.:

```
[15:02:32] done — 6 scenario folders, 18 files stored.
```

**Failures surface early, not as `status='failed'`.** A bad input fails the
`plurai ingest-bundle` CLI (non-zero exit, Step 4) BEFORE anything uploads; a
folder-validation / manifest / archive problem raises from
`upload_scenario_bundle_start` itself. If either surfaces an error, read it
yourself — there is no async build to retry: fix the staging dir / local
environment silently (e.g. add a missing `ROLES.md`) and re-run; for a backend problem you can't fix from here, run the contact hand-off (`stop_message.py contact --done`).

### Step 6: Write the markers

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
printf %s "<set_id>" > "$PLURAI_DIR/.scenario_bundle_id"
printf %s "0"        > "$PLURAI_DIR/.scenario_bundle_augmentation"
printf '%s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "<one line: what this bundle is FOR>" \
  > "$PLURAI_DIR/.scenario_bundle_intent"
```

(The first two are one line each, no trailing newline — matching the `.doc_examples_set_id` /
`.scenario_set_id` convention.) `augmentation_level=0` is the faithful-reproduction
default: one scenario per folder. Write a higher integer ONLY if the user
explicitly wants variations (each folder then ALSO yields N varied scenarios as
exemplar-based augmentation).

**`.scenario_bundle_intent` records WHEN this bundle was uploaded and WHAT FOR**, in your own
words — `real Salesforce account records for the account-lookup preconditions`, not
`bundle upload`. It exists because `.scenario_bundle_id` is an unscoped pointer with no TTL:
it persists until someone deletes it, and **every** later `generate_scenarios_start` — a suite
build's qualification batch, a regeneration next week, another operator's run — reads it and
takes the IMPORT path, which bypasses the dimension-graph walk and makes `count`, `interest`
and dimension diversity inert (`knowledge_bases` and `memory_challenge` still apply — the
backend runs the same knowledge prelude and carries the memory complication on this path).
A marker nobody remembers writing silently converts someone else's suite build into a bundle
import. `/generate-scenarios` reads this line back and states it out loud before it takes that
path, so a stale marker is visible at the one moment it matters. Machine-local by design (the
managed `.gitignore` block ignores anything not named there): a clone carries the staged id
but no intent line, so `/generate-scenarios` SKIPS the bundle path there and says so, leaving
the tracked id in place — it never deletes a marker it cannot explain, because whoever
committed the id is not whoever is running that build.

Stage the id — it names the backend bundle this repo's scenarios were built from.
Nothing to edit in any `.gitignore`: the repo-root managed block already tracks
`.scenario_bundle_id` and ignores `.scenario_bundle_augmentation`. If the repo's block is
missing or its BEGIN line carries an older rev, re-run `/simulation-scan-agent` Step 5.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .scenario_bundle_id
```

(Note: there is no `manage-envs` manifest key for a scenario bundle today, so even if
the repo uses named envs, the bundle id lives only in these dotfiles — a known
limitation.)

### Step 7: Hand off to /generate-scenarios

Tell the user:

```
Scenario bundle ready: <set_id>

  <scenario_count> scenario folders uploaded, augmentation_level=0.

  Next: run /simulation-generate-suite — its scenario build reads plurai_sim/.scenario_bundle_id +
  .scenario_bundle_augmentation and builds one scenario per folder from the
  REAL records. The dimension-graph walk (count / interest / diversity) is
  BYPASSED on this path, while the knowledge and memory dials still apply —
  it says so out loud, quoting .scenario_bundle_intent, before it takes that
  path, and clears all three markers when the build they were written for is
  done.

  Then VERIFY (below) — fidelity is LLM-mediated, so confirm each grounded
  value is a real record before you trust the set.

  To remove this bundle later, ask me to delete this scenario bundle.
```

### Step Verify: confirm the grounding actually landed (MANDATORY)

`augmentation_level=0` asks the generator to reproduce values verbatim, but the
copy is LLM-mediated — so prove it. This is read-only; no local materialization.

1. After `/generate-scenarios` reports `done`, enumerate the new set:
   `get_artifact_meta(scenario_set_id=<new id>)` → `detail.scenarios[].name`.
2. For each scenario, `get_scenario(scenario_set_id, scenario_name)`. It returns
   `{scenario_set_id, scenario_name, files}` — the scenario content lives in the
   `files` dict (`agent_context.yaml`, `scenario.yaml`, `persona.yaml`, …), NOT as
   top-level keys. Parse `files['agent_context.yaml']` as YAML and read the
   precondition field's identity value (the `id`) + its companion fields.
3. Load the real data source locally (e.g. the set of all `id` values from the
   agent's data source) and **assert each scenario's grounded `id` ∈ that
   set, AND assert the companion fields match the source record for that `id`** —
   the companion check is mandatory, not optional: it catches the LLM keeping the
   right id but drifting the companion fields (a partial grounding that the
   id-only check would pass silently).
4. Report a pass/fail table: scenario → grounded id → in-source? On any miss, the
   `ROLES.md` field→value pinning wasn't strong enough — sharpen it, optionally
   start a fresh bundle, and regenerate.
5. **Stronger proof (recommended):** run 1–2 scenarios via `plurai-run-simulation`
   and confirm `tool_events.json` shows the agent's lookup tool returned a
   NON-EMPTY record for the grounded id — i.e. the dialog is built on a real
   record end-to-end, and the original "empty tool returns on a hallucinated id"
   defect is gone.

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

- **Don't upload whole datasets.** One real record per folder, a representative
  SUBSET — always ask the user about scope first (Step 2).
- **Anonymization is posture-conditional, not unconditional.** Under
  **anonymized** posture, `plurai ingest-bundle` (Step 4) scrubs each record —
  INSIDE the record content — before it uploads, and is fail-closed: a missing
  Presidio/spaCy aborts with nothing uploaded, and a non-zero exit is a HARD
  STOP. Under the **raw** (default) posture, ingestion uploads records as-is
  with a loud `RAW (anonymization off)` banner; that upload is governed by the
  customer's DPA with Plurai. Either way, every upload records its posture in
  the manifest and `.plurai/uploads/audit.log`.
- **ROLES.md must pin the field→`agent_context` mapping** verbatim ("set
  `agent_context.<field>` to these exact values; do NOT invent an id"). It is
  the only lever on fidelity, because reproduction is LLM-mediated.
- **Never run a bundle AND corpus `ground_from` (WORLD-grounding) for the same
  batch** — they're mutually exclusive: the bundle path skips corpus world-seeding
  AND the graph walk. If the schema has `ground_from`, tell the user it's inert for
  *world-value sourcing* this run. (World-grounding ONLY — corpus knowledge-Q&A still
  runs when `knowledge_bases` are attached; see "When this applies — and when it does
  NOT".)
- **Only the chosen records leave the machine.** Select locally; never upload the
  full source. Records may carry customer data — the `plurai ingest-bundle` disclosure
  preview (stderr) + `.plurai/uploads/audit.log` record exactly what's sent and its
  posture (pseudonymized under anonymized posture; as-is, DPA-governed, under raw).
- **VERIFY after generation.** Fidelity is best-effort; a clean run is NOT proof
  of grounding. Assert the grounded value ∈ the real data source.
- **No LLM build happens here.** Failures surface as a non-zero `plurai ingest-bundle`
  exit (Step 4) or a RAISED `upload_scenario_bundle_start` error — never a
  `status='failed'` payload, so there's nothing async to retry.
- **Don't re-run the ingest→start flow for a set that's already stored** (to rebuild:
  confirm, `delete_upload(<old>)`, start fresh). A fresh manifest (a new
  ingest run) always creates a NEW set — only retrying with the SAME manifest is
  safe/idempotent (Step 4).
- **`delete_upload` destroys the set — confirm with the user first.** Inside a driving
  command's flow window, sanction that confirmation first:
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction delete-confirm` — an
  unsanctioned ask is denied, and a denial is not a stop.

## Failure modes + recovery

| Symptom | Likely cause | What to do |
|---|---|---|
| `upload_scenario_bundle_start` raises `scenario-bundle tools are disabled — no scenario_bundle_storage_root` (Step 5; the Precondition-1 preflight cannot see this — it returns an empty page instead) | Backend has bundle uploads disabled (no `scenario_bundle_storage_root`, and no `scenario_storage_root` to derive it from) | No repair step here, and the customer can't fix it: run the contact hand-off (`stop_message.py contact --done`) and stop (no raw error). |
| `resolve_plurai.py` exits non-zero (Step 4) | No usable `plurai`: not installed in the client's venv (activated or repo `.venv`) or PATH, or the found install predates the `ingest-*` CLI | Install/upgrade in the client's venv as the resolver's stderr says (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run the resolve line. |
| `plurai ingest-bundle` nonzero exit, stderr names Presidio / spaCy (anonymized-posture envs only) | `plurai-simulation-sdk[anonymize]` or a spaCy model isn't installed (fail-closed under `anonymize: true` — nothing uploaded) | Install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints) + `python -m spacy download en_core_web_lg`, then re-run. |
| `resolve_plurai.py` exits 0 but its stderr carries `NOTE: shadows …` | a SECOND `plurai` install is on PATH ahead of (or beside) the one used | Not an error — the resolve SUCCEEDED — but it is a FINDING: carry it into the hand-off verbatim. A shadowing install is why the next command on this machine can behave differently from this one. (A runner below the SDK floor is not this row: the resolver exits 3 with `REFUSED` and the upgrade command.) |
| `plurai ingest-bundle` nonzero exit naming the diagnostic connection | Plurai is not logged in in this shell (fail-closed) | Run the `harness-setup` skill (Precondition 1), then re-run. |
| `plurai ingest-bundle` reports 0 folders / 0 files | wrong `--bundle-dir`, or the staging dir has no scenario folders | Fix the staging dir (Step 3) and re-run. |
| `upload_scenario_bundle_start` raises "missing its required ROLES.md" / "has a ROLES.md but no material files" | A folder has materials but no `ROLES.md`, or only a `ROLES.md` | Fix the offending folder in the staging dir, re-run `plurai ingest-bundle`, re-run `upload_scenario_bundle_start`. |
| Stale `set_id` (not found via `get_upload_meta`) | Server restarted, or the set was deleted | Start a new upload; no recovery for the old one. |
| `/generate-scenarios` VERIFY shows a grounded value NOT in the real source | LLM didn't reproduce verbatim at `augmentation_level=0` | Sharpen the `ROLES.md` field→value pinning and regenerate — your fix to make, silently. If the regenerated set fails the same check, nothing here fixes it: run the contact hand-off (`stop_message.py contact --done`). |
| `generate_scenarios_start` raises "would generate N scenarios … exceeding scenario_job_max_count" | `folders × (1 + augmentation_level)` is over the backend cap | Lower `augmentation_level`, split the bundle into fewer folders, or ask the operator to raise the cap. |
| `generate_scenarios_start` raises "bundle not found or not finalized" | A stale / unfinalized `.scenario_bundle_id` | Re-run this skill to upload + start a fresh bundle. |

## Glossary

- **scenario bundle** — one finalized set (`scenario_bundle_id`) of per-scenario
  folders; one folder = one scenario's real materials. Persists on the backend
  until `delete_upload`.
- **scenario folder** — `scenarios/<name>/` holding a required `ROLES.md` + ≥1
  material file.
- **material file** — any non-`ROLES.md` file in a folder: the json/yaml variable
  file (the real record) and any attachment.
- **ROLES.md** — freeform prose pinning each file's role + the field→`agent_context`
  mapping. The fidelity-critical channel; the backend never parses it, it inlines
  it into the generation prompt.
- **BUNDLE.md** — set-level overview, passed as `overview_md` to
  `upload_scenario_bundle_start`; `plurai ingest-bundle` excludes it from the
  uploaded objects (it's an inline param), so that call is its only route to the
  backend. Hand-authored it is optional; present on disk it is mandatory to pass.
- **augmentation_level** — fidelity dial: `0` = faithful reproduction (1 scenario
  per folder); `N > 0` = ALSO N varied scenarios per folder (folder as exemplar).
- **provided_materials/** — where the backend stages a folder so the scenario
  generator can Read it and build the scenario from it.
- **single-archive manifest** — the opaque JSON `plurai ingest-bundle` prints to
  stdout (`{lease_id, checksum, bytes, posture, anon_key_fingerprint}` — no
  `id`/`set_id`, no per-member list); handed verbatim to
  `upload_scenario_bundle_start(manifest=…)`, which fetches
  and extracts the single archive by `lease_id`, verifying it
  against the archive's own `checksum`/`bytes`.
