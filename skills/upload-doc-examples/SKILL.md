---
name: upload-doc-examples
description: >-
  Upload real example documents (invoices, forms, tickets, PDFs) so generated documents follow real formatting. Only chosen examples leave the machine. Run after /simulation-scan-agent when scenarios involve documents.
---

# Upload Document Examples

You are the LOCAL Claude Code agent. The customer's agent reads from or
extracts data from documents (invoices, receipts, forms, tickets, etc.),
and they want the backend to generate realistic documents for each scenario —
templated from real example documents they already have, not just authored
from a one-line brief. This skill uploads those example document families
to the backend so document generation has real samples to learn from.

The split that matters:

```
┌── customer's machine (you, via plurai-sdk) ─────────────────────────┐
│  SCAN → ANONYMIZE (if posture: anonymized) → UPLOAD, all local.     │
│  1. Locate the examples folder (ask the user for the path).         │
│     Layout: <root>/<family>/FAMILY.md + example files.              │
│  2. Shell: plurai ingest-doc-examples …                             │
│       scan → ANONYMIZE (if anonymized: content_b64, fail-closed) →  │
│       upload ONE archive → prints the single-archive MANIFEST       │
│       { lease_id, checksum, bytes, … } to stdout                     │
│  3. MCP: upload_doc_examples_start(manifest=…, types_overview_md?)   │
│       → UploadFinalizeResult { set_id, status: 'done', ... }          │
│       (set_id is server-minted, in this same call)                   │
│  4. MCP: get_upload_meta(set_id) → 'done'.                          │
│  5. Write plurai_sim/.doc_examples_set_id (no trailing newline).    │
└──────────────────────────────────────────────────────────────────────┘
                                   │ MCP
┌── Plurai-hosted backend ─────────┴──────────────────────────────────┐
│  upload_doc_examples_start(manifest) → FETCHES the single            │
│    archive by lease_id, extracts+verifies it, mints                  │
│    the set_id, seals the set, returns the manifest of families +     │
│    file counts. Storage is a backend service reached via the         │
│    diagnostic bearer — no separate storage target                    │
│    returned.                                                          │
│  get_upload_meta(upload_id=set_id) → 'done' (sync, no poll).        │
│                                                                       │
│  Stored as a plain file store — no LLM build, no graph synthesis.    │
│  The families are later used as few-shot examples_dir by the         │
│  document-generation step during scenario generation.                │
│  No TTL eviction — they persist until delete_upload.                 │
└──────────────────────────────────────────────────────────────────────┘
```

**Why the ingest runs locally.** Only the customer's machine has the example
documents. `plurai ingest-doc-examples` scans the folder, then resolves this env's
anonymization posture from `plurai.config.yaml` (the `anonymize:` key, default raw):
under **anonymized** posture it **anonymizes each example (reaching INSIDE the
base64 file content) fail-closed**, exactly as before; under the **raw** (default)
posture it uploads the examples as-is and prints a loud `RAW (anonymization off)`
banner — that upload is governed by the customer's DPA with Plurai. Either way it
bundles everything into ONE compressed archive and uploads that ONE archive to the
storage service — no LLM keys involved. The backend fetches and extracts that single
archive by `lease_id`. Under anonymized posture, anonymization is format-preserving,
so the examples still template document generation faithfully — only PII *values*
are pseudonymized, the structure is unchanged.

**This is a plain file store, not a corpus build.** Unlike `/build-rag-corpus`,
there is no LLM-driven enrichment phase, no testset synthesis, and no
backend LLM credentials required. The backend simply receives and stores
the family files so document generation can reference them later.

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

1. **`plurai` MCP server reachable** (auto-registered
   via the plugin manifest; the backend is the configured one, the one the saved Plurai login names;
   a remote backend requires that login).
2. **`/simulation-scan-agent` has run** — `plurai_sim/plurai.config.yaml`
   exists and `.document_fields` has been written (a scenario field carries
   document content — the agent reads, produces, or references a document;
   this is NOT limited to document-extraction agents). For a stateful agent whose
   document field name is only CONFIRMED when the world is stood up, that confirmation
   now happens at `/simulation-generate-suite` time — upload the examples whenever you
   have them; the set id is read per-env at generation.
3. **`plurai` SDK is installed locally** — install it as
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints.
   The extractor is `plurai.doc_examples_extract.extract_doc_examples`.
4. **The customer can supply the path to a folder of example document
   families** — each family is a subfolder containing a required `FAMILY.md`
   (describing the family — type, fields, typical content) plus one or more
   example files.
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

5. **Only if this env's anonymization posture is anonymized** (`anonymize: true`
   in `plurai.config.yaml` — default raw): **`plurai-simulation-sdk[anonymize]` + a spaCy English
   model are installed** — the pre-egress scrub runs INSIDE `plurai
   ingest-doc-examples` (Step 2) under that posture, reaching into each example's
   base64 content; it is fail-closed and refuses to run without them: install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints), then `python -m spacy download en_core_web_lg`. Under
   anonymized posture, a true binary asset (a scan/image that can't be scrubbed as
   text) raises `DocExampleBinaryError` → the CLI exits non-zero having uploaded
   NOTHING. Under the raw (default) posture this precondition doesn't apply — no
   anonymizer required, and binary assets upload unscrubbed.
6. **No separate storage credential** — `plurai ingest-doc-examples` uploads the
   ingested objects (anonymized or raw, per the env's posture) to the backend's
   object-storage service over the same diagnostic connection as the MCP tools
   above (the saved login); the endpoint is
   derived, not passed in. Zero new credential plumbing.

## The flow

### Step 1: Locate the examples folder

Ask the user for the path to their document examples folder. Explain the
expected layout:

```
<root>/
  invoice_standard/
    FAMILY.md          ← required: family name + description
    example_001.txt
    example_002.txt
    ...
  invoice_international/
    FAMILY.md
    example_001.txt
    ...
  receipt_retail/
    FAMILY.md
    example_receipt.txt
    ...
```

`FAMILY.md` is required in every family folder — if any subfolder is
missing `FAMILY.md` (or has no example files), the `plurai ingest-doc-examples`
scan (Step 2) **fails with a ValueError** (non-zero exit) listing the offending
families. Nothing uploads until the user fixes the layout and re-runs.

Confirm the resolved path with the user before proceeding.

### Step 2: Ingest (scan → anonymize per posture → upload), then start the set

**1. Run the SDK ingest command.** ONE invocation scans the examples folder
locally, then resolves this env's anonymization posture from `plurai.config.yaml`
(the `anonymize:` key, default raw): under **anonymized** posture it
**anonymizes every example (reaching INSIDE the base64 content, fail-closed)**
exactly as before; under the **raw** (default) posture it bundles the examples
as-is and prints a loud `RAW (anonymization off)` banner. Either way it bundles
the result into a single compressed archive, uploads that ONE archive to
the backend's storage over the same diagnostic connection as the MCP tools (one
lease for the whole set), and prints the **single-archive manifest** to stdout:

```bash
# Resolve WHICH plurai runs: the Bash tool does not activate the client's venv, so a bare
# `plurai` misses or silently hits a stale global install. The resolver picks the client's
# activated venv ($VIRTUAL_ENV) → the repo .venv → PATH, or fails with guidance.
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-doc-examples)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# Preflight: bash cannot see which backend the MCP server is bound to. Call the read-only
# backend_identity() MCP tool first and paste its object here verbatim — that comparison is the
# one thing neither the CLI nor this shell can make alone. The pin is NOT re-checked here; the
# CLI owns it (below). Omitting the argument only downgrades this to a warning.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-doc-examples \
    --workspace-root "$PLURAI_DIR" \
    --source-dir "<resolved-examples-path>" \
    --out-dir "$PLURAI_DIR/doc_examples"
# --workspace-root is REQUIRED on every ingest call: omitted, it silently unarms the
# ingest target pin and pins a phantom plurai_sim/ under whatever directory the shell is in.
# Both paths go through $PLURAI_DIR for the same reason — a relative literal resolves against
# the session cwd, and under a named env $PLURAI_DIR is plurai_sim/envs/<name>.
#   stdout → the single-archive manifest JSON (capture it verbatim)
#   stderr → first the resolved TARGET line (`plurai ingest-doc-examples: uploading to <backend> (target
#            from the configured backend; <pin verdict>)`),
#            then a human-readable disclosure preview (family/file counts, bytes, scrub summary)
#            The target line prints on EVERY invocation, so it is there even when the run is
#            refused. READ IT: it is the authoritative statement of which backend this upload
#            reached. Nothing downstream re-checks it,
#            so `assert-backend-identity.sh` before the upload is the guard.
#   uses the configured backend and token (or the saved login) —
#   the same the backend connection the MCP tools above use; no storage flags needed.
```

A `WARNING (disclosure: home_named)` line from `ingest-doc-examples` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

There is no `--set-id` — the set doesn't exist yet; the backend mints `set_id` in
the next call, from this manifest. `--source-dir` is the examples folder from
Step 1 (the CLI scans it — no separate extract step).

- **Capture stdout as the manifest and hand it to `upload_doc_examples_start`
  UNMODIFIED** (opaque — do not interpret or edit its contents). It is a JSON
  object shaped `{lease_id, checksum, bytes, posture, anon_key_fingerprint}` —
  the SDK streams every family (scrubbed under anonymized posture, as-is under
  raw) into ONE `.tar.gz` and uploads it as ONE storage object; `checksum`/
  `bytes` describe that single archive (no per-member list on the wire — the
  deterministic tar+gzip means the server verifies the whole archive by its
  checksum, so member integrity is implied). Parse the stdout text into the
  object and pass it to `manifest=` **as that object** (change nothing). The
  SDK writes the disclosure preview (to stderr) + `.plurai/uploads/audit.log`
  itself; relay the preview counts, never file bodies.
- **Fail-closed — a non-zero exit is a HARD STOP.** Under **anonymized** posture
  this can mean anonymization could not run (Presidio / spaCy missing) or an
  example is a true binary that can't be text-scrubbed (`DocExampleBinaryError`);
  under **either** posture it can also mean the layout is invalid (a family
  missing `FAMILY.md` or with no examples) or the storage target is unset —
  **NOTHING was uploaded**. Read the error yourself: fix local-environment
  causes silently (missing packages, unset connection vars — see Failure
  modes) and re-run; for input problems (an un-scrubbable binary example, a
  family missing `FAMILY.md` or examples) tell the user what to change in
  plain product terms. Never show the raw error.

**2. Start the set**, handing the backend the manifest:

```
upload_doc_examples_start(
    manifest=<the captured stdout manifest, opaque>,
    types_overview_md=<a set-level _TYPES.md overview, or omit>,   # optional
)   →  UploadFinalizeResult { set_id, status: 'done', ... }   # family names, file counts, etc.
```

`set_id` is the returned `set_id` — the backend mints it here, in this one call;
there is no earlier `set_id` to thread through. The backend **fetches the single
archive from storage by `lease_id`**, verifies its checksum/size,
extracts it under hardened caps (path-jail, decompression-bomb limits), seals
the set, and returns the family/file-count result.
It is **synchronous** — no async build. `types_overview_md` is an optional
set-level overview (the extractor does not emit one; pass a `_TYPES.md` you
maintain, or omit it). **Retrying with the SAME manifest (same `lease_id`) is
idempotent** — it returns the EXISTING set instead of creating a second one.

**Disclosure + audit move to the SDK on this path.** The `plurai ingest-doc-examples`
Bash call is the consent chokepoint (normal Bash approval, naming the exact command),
and the CLI writes the preview + `.plurai/uploads/audit.log`. `upload_doc_examples_start`
carries no file bytes (only the opaque single-archive manifest + the optional overview).

### Step 3: Confirm the set is done

`upload_doc_examples_start` is **synchronous** — it fetches, validates, and
writes the manifest immediately, so a single confirming read is enough. There is no
per-set status tool; use the generic upload-surface `get_upload_meta` (upload status
folds into it because the call is synchronous):

```
get_upload_meta(upload_id=<set_id>)   →  status: 'done'
```

The only two status values are `'preparing'` (a stale/in-progress state you should
not normally observe on this path) and `'done'` (finalized + stored). E.g.:

```
[15:02:32] done — 3 families, 12 files stored.
```

**Failures surface early, not as `status='failed'`.** A bad input fails the
`plurai ingest-doc-examples` CLI (non-zero exit, Step 2) BEFORE anything uploads; a
manifest/archive problem raises from `upload_doc_examples_start` itself. If
either surfaces an error, read it yourself — there is no async build to retry:
fix what's fixable from here silently and re-run; translate input problems into
a plain-terms ask; for a backend problem you can't fix from here, run the contact hand-off (`stop_message.py contact --done`).

### Step 4: Write the marker (PER ENV)

Record `set_id` so it's scoped to THIS env (each env has its own doc set — same discipline as
`corpus_id`). If `plurai_sim/envs/` exists, write it to the **active env's manifest**:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # ABSOLUTE $PLURAI_DIR — cwd-reset-proof
python "${CLAUDE_PLUGIN_ROOT}/skills/manage-envs/scripts/env_manifest.py" \
    write "$PLURAI_DIR" --name "$(basename "$PLURAI_DIR")" --doc-examples-set-id "<set_id>"
```

Then (always, as the bare-mode fallback + a quick local marker) also write the one-liner:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $PLURAI_DIR — re-source in THIS block
printf %s "<set_id>" > "$PLURAI_DIR/.doc_examples_set_id"
```

Stage it — it names the backend example set the generated documents were templated
from. Nothing to edit in any `.gitignore` (the repo-root managed block already tracks
this marker; if the repo's block is missing or its BEGIN line carries an older rev,
re-run `/simulation-scan-agent` Step 5):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" .doc_examples_set_id
```

`/generate-scenarios` reads the active env's `doc_examples_set_id` first, this dotfile
only in bare mode.

### Step 5: Surface the summary

```
Doc example set ready: <set_id>

  3 families · 12 files stored.

  Families: invoice_standard, invoice_international, receipt_retail

  The example files live on the backend as the few-shot examples_dir
  for document generation. /generate-scenarios will use them
  automatically (it reads plurai_sim/.doc_examples_set_id).

  To remove this set later, ask me to delete this example set.
```

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

- **Anonymization is posture-conditional, not unconditional.** Under
  **anonymized** posture, `plurai ingest-doc-examples` (Step 2) scrubs each
  example — including INSIDE the base64 content — before it uploads, and is
  fail-closed: a missing Presidio/spaCy, or an un-scrubbable binary, aborts
  with nothing uploaded. Under the **raw** (default) posture, ingestion
  uploads examples as-is with a loud `RAW (anonymization off)` banner — raw
  customer documents DO leave the machine in this posture, processing
  governed by the customer's DPA with Plurai. Either way, every upload
  records its posture in the manifest and `.plurai/uploads/audit.log`.
- **Don't re-run the ingest→start flow for the same set of examples once it's stored.**
  To rebuild, confirm with the user, `delete_upload(<old>)`, then start a new one. A
  fresh manifest (a new ingest run) always creates a NEW set — only retrying with the
  SAME manifest is safe/idempotent (Step 2).
- **`delete_upload` destroys the stored examples — confirm with the user first.** Inside a
  driving command's flow window, sanction that confirmation first:
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction delete-confirm` — an
  unsanctioned ask is denied, and a denial is not a stop.
- **No LLM build happens here.** A bad input fails the CLI (Step 2) or raises from
  `upload_doc_examples_start`, not as a `status='failed'` payload — there is nothing
  async to retry.

## Failure modes + recovery

| Symptom | Likely cause | What to do |
|---|---|---|
| `resolve_plurai.py` exits non-zero (Step 2) | No usable `plurai`: not installed in the client's venv (activated or repo `.venv`) or PATH, or the found install predates the `ingest-*` CLI | Install/upgrade in the client's venv as the resolver's stderr says (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run the resolve line. |
| `plurai ingest-doc-examples` nonzero exit, stderr names Presidio / spaCy (anonymized-posture envs only) | `plurai-simulation-sdk[anonymize]` or a spaCy model isn't installed (fail-closed under `anonymize: true` — nothing uploaded) | Install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints) + `python -m spacy download en_core_web_lg`, then re-run. |
| `plurai ingest-doc-examples` stderr `DocExampleBinaryError` (anonymized-posture envs only) | an example is a true binary that can't be text-scrubbed | Remove/convert the offending binary example, then re-run. Do NOT bypass the scrub. |
| `resolve_plurai.py` exits 0 but its stderr carries `NOTE: shadows …` | a SECOND `plurai` install is on PATH ahead of (or beside) the one used | Not an error — the resolve SUCCEEDED — but it is a FINDING: carry it into the hand-off verbatim. A shadowing install is why the next command on this machine can behave differently from this one. (A runner below the SDK floor is not this row: the resolver exits 3 with `REFUSED` and the upgrade command.) |
| `plurai ingest-doc-examples` nonzero exit naming the diagnostic connection | Plurai is not logged in in this shell (fail-closed) | Run the `harness-setup` skill (Precondition 1), then re-run. |
| `plurai ingest-doc-examples` reports 0 families / 0 files | wrong source path, or no family folder has a `FAMILY.md` + ≥1 example | Fix the layout (Step 1) and re-run. |
| Stale `set_id` (not found via `get_upload_meta`) | Server restarted, or the set was deleted | Start a new upload; no recovery for the old one. |

## Glossary

- **family** — one folder of related example documents, identified by its
  `FAMILY.md` descriptor. The backend groups few-shot examples by family
  so document generation can match the right template to each scenario.
- **doc example set** — one upload set identified by `set_id` (server-minted by
  `upload_doc_examples_start`). `plurai ingest-doc-examples` bundles the set's
  ingested family objects (anonymized or raw, per the env's posture) into ONE
  archive and uploads it; the backend fetches and extracts that archive by
  `lease_id` on start, and persists the families as the few-shot examples_dir
  until deleted.
- **`families.json`** — the SDK's per-scan wire output under `--out-dir`: a JSON array of
  `{family, path, content_b64}` entries, one per example file (anonymized in place before
  upload, under anonymized posture).
- **single-archive manifest** — the opaque JSON the CLI prints to stdout
  (`{lease_id, checksum, bytes, posture, anon_key_fingerprint}` — no
  `id`/`set_id`, no per-member list); handed verbatim to
  `upload_doc_examples_start(manifest=…)`, which fetches and
  extracts the single archive by `lease_id`, verifying it against
  the archive's own `checksum`/`bytes`.
- **few-shot examples_dir** — the backend's internal reference to the
  uploaded family files; passed to document generation so outputs
  resemble real customer documents rather than generic LLM-authored text.
