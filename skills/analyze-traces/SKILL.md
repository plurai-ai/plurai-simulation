---
name: analyze-traces
description: >-
  Ground the simulation in real production traces (one stage of `/simulate`; callable alone). The SDK exports them locally; the backend turns them into a brief. By default it then turns the brief's trace failures into trace scenarios. Optional; run after /simulation-scan-agent.
---

# Analyze Production Traces → Trace Brief

You are the LOCAL Claude Code agent. The customer runs (or ran) their agent in
production and ships traces to an observability platform. The SDK exports from
**LangSmith and Langfuse**; Arize and Braintrust have no exporter yet. They want the backend's generated mock + scenarios + evals to
reflect what *actually happens* in production — not just what the agent's code
suggests. The backend turns a folder of their real traces into evidence on the
workspace's **grounding ledger**, which env-gen / scenario-gen / eval-gen read, and into a
**trace brief** that people read.

> **Traces are one of the OPTIONAL grounding inputs** (all feed the suite; provide what applies):
> production traces (this skill), a **RAG/knowledge corpus** (`/build-rag-corpus` — strongly recommended
> if the agent has a retrieval / vector-search / text2SQL tool), **real records for scenario preconditions**
> (`/upload-scenario-bundle` — when scenarios start from a selected/current record), and
> **document/artifact examples** (`/upload-doc-examples` — whenever a scenario involves a document, not
> only extraction agents). No traces? Skip this and use whichever of the others apply.

The split that matters (identical posture to `/build-rag-corpus`):

```
┌── customer's machine (you, via plurai-sdk) ─────────────────────────┐
│  EXPORT → ANONYMIZE (if posture: anonymized) → UPLOAD, all local.   │
│  1. Detect the sink + project the agent's traces live in.           │
│  2. Gather sink connection config (api_key + project stay LOCAL).   │
│  3. Shell: plurai ingest-traces --source-kind langsmith …           │
│       --declared-names <names the agent descriptions declare>       │
│       export → ANONYMIZE (posture: anonymized; fail-closed) → upload│
│       → prints ONE part entry to stdout                             │
│       { kind: trace_archive, sha256, bytes, posture, … }            │
│  4. MCP: ground_workspace_start(workspace_id, previous, added=[…])  │
│       → { job_id, package_version, … } — the grounding job          │
│  5. Shell: poll_job.sh <job_id>, FOREGROUND, until done/failed;     │
│       the backend analyzes the part itself. On done → read the job, │
│       pull the mirror, confirm the brief, then apply it.            │
└─────────────────────────────────────────────────────────────────────┘
┌── Plurai backend (holds LLM creds) ────────────────────────────────┐
│  The grounding job runs trace analysis on the part (it extracts from│
│  100% of the traces and renders the brief), places what the traces  │
│  show on the ledger's keys, and publishes the brief for people.     │
└─────────────────────────────────────────────────────────────────────┘
```

## Preconditions

1. **`/simulation-scan-agent` has run** (there is a `plurai_sim/` directory with the agent's
   descriptions): the trace part is delivered on top of the descriptions the scan delivered, and
   the names the scrub keeps come from them.
2. **The `plurai` SDK + the sink client are installed locally** — e.g.
   `pip install langsmith` for a LangSmith export. `plurai ingest-traces` (Step 4)
   runs the export via `plurai.trace_extract`, which raises
   `TraceExtractionDependencyError` with the exact install hint if the client is absent.
3. **Backend reachable** — the user is logged in (the saved Plurai login),
   and the Plurai backend has trace storage configured. If it does not, the grounding
   job fails the trace part before any work and says so in its `failed_parts`.
4. **The customer has production traces** in an observability sink and can supply
   its api_key + project.
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
> upload still records its posture in the part entry and `.plurai/uploads/audit.log`.

5. **Only if this env's anonymization posture is anonymized** (`anonymize: true`
   in `plurai.config.yaml` — default raw): **`plurai-simulation-sdk[anonymize]` + a spaCy English
   model are installed** — the pre-egress scrub runs INSIDE `plurai ingest-traces`
   (Step 4) under that posture and is fail-closed, refusing to run without them:
   install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints), then `python -m spacy download en_core_web_lg`
   (any of `en_core_web_{lg,md,sm}` works). Under anonymized posture, without them
   the CLI exits non-zero having uploaded NOTHING. Under the raw (default) posture
   this precondition doesn't apply — no anonymizer required.
6. **No separate storage credential** — `plurai ingest-traces` uploads the ingested
   trace objects (anonymized or raw, per the env's posture) to the backend's
   object-storage service over the same diagnostic connection as the MCP tools
   (the saved login, Precondition 3); the
   endpoint is derived, not passed in. Zero new credential plumbing.

## The flow

**Flags.** `--no-trace-scenarios` skips Step 7b: the brief is published and applied, and no
trace scenario is written from it. Without the flag, Step 7b runs whenever the brief lists a
trace failure. `--max-new N` and `--max-total N` pass through to Step 7b unchanged: the per-brief
cap and the total cap on trace scenarios for this run, else 5 and 10. `--source <langsmith|langfuse>
--project <name>` answer Step 1's platform and project, so that ask is not made (`/simulate` passes both).

**Open the flow window** (flow-fidelity) after resolving `$WORKDIR` (named-env
aware, as in scan-agent Step 0):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm traces
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready traces
```

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" require traces "$WORKDIR"
```

When it exits 3, print its line verbatim as the whole hand-off, close the window (below), and stop.

(the script resolves `$FLOWDIR`, clears the stale ask/report residue and stamps the name —
overwrite, because nothing drives this command as a sub-step, so any other name in the
marker is a stale window from an earlier flow).
While it exists the hooks deny unsanctioned asks and style-validate the final summary;
this command's sanctioned asks (Step 1's platform/project question, Step 7's per-artifact
confirmations) each write the one-shot marker first. The window closes itself when the
validated summary renders; early exits close it own-marker-guarded:

<!-- BEGIN step0: generated from ${CLAUDE_PLUGIN_ROOT}/docs/step0-block.md; edit there -->
**If that line prints a heartbeat instruction, set the heartbeat it names in the SAME turn** — it is this flow's dropped-turn recovery (`${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md` → *Dropped turns*). If the call is denied by the permission mode, continue without the heartbeat — expected under auto mode; never retry it.

Step 0 has three steps, run in this order. A line that lets the flow go on ends only its own step.

**1. Check readiness.** The `ready` line prints `ready`, or one line for each thing that is not set up.

- A `NOT READY` line names something only the user can change (the plugin's hooks not running, the flow window not armed, no shell network, MCP and the shell on different backends), with its fix. Close the window with `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close traces`, tell the user in plain words what the line says to fix, and stop; the flow must not start. The command's exit status is 0 whatever it prints, so a `NOT READY` line stops the flow all the same.
- A `SETUP` line names something the `harness-setup` skill fixes in this session (no Plurai login, or Plurai not connected). Close the window the same way, then run the `harness-setup` skill `from /analyze-traces` (where the harness takes no arguments: "you run it for /analyze-traces"). Add nothing of your own, and show the user nothing from the `SETUP` line. The window stays closed during setup, so the ask and report guards do not block its install question or the turn that waits for the login. When the skill reports Plurai set up, run the `arm` line and the `ready` line above again and go on from here; when it stops, stop. If the same `SETUP` line comes back after the skill reported Plurai set up, do not run the skill again: tell the user `Setup could not finish. Run /plurai-diagnose and share its output with Plurai support.` and stop.
- A `ready, limited` line means the plugin's hooks are off in this session, and the flow runs without them. When a `WARN (hooks)` line follows it, show the user its text once, verbatim, and go on to step 2. Follow `${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md` for the rest of the flow. Right after this flow's hand-off, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff traces` and do what it prints in the same turn, also where a step says to end the turn because the flow continues by itself.

**2. Check the backend.** Step 1 ends on `ready` or `ready, limited` (a `NOT READY` or `SETUP` line ends it first). Before you look at your tool list, run `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" --close traces`. It needs no backend tool. A plugin the backend refuses has none. On a refusal (exit 7 or 8) the same call closes the window, so no close step follows it.

- Exit 0: go on to step 3. A line it printed is an update notice: say it once, as printed, first; it never stops the flow.
- Exit 7 is a version refusal. Say what the check printed as the error posture's version refusal rule says, with no word before it, and stop; the flow must not start, and no tool call, scan or setup follows.
- Exit 8 is a credit refusal: its line is `CREDIT-STOP: <sentence>`. Follow the error posture's credit refusal rule.
- Exit 3 prints one line. A `SETUP` line: handle it as the `SETUP` line above. A `NOT READY` line (for example hooks the harness has not trusted): handle it as the `NOT READY` line above.

**3. Check your tool list.** The script cannot see your tool list: if no backend tool is available to you in this session, run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready traces --no-backend-tools` and act on the line it prints as above.
<!-- END step0 -->

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close traces
```

### Step 1: Detect the sink + project
With `--source` and `--project`, both are known: skip this question (a narrowing is then not asked
for; export without one). Otherwise ask the customer (sanctioned ask — first run
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction sink-project`) which observability platform their agent's traces live in and
the project/session name. Default sink kind: `langsmith`. Confirm they have
traces there (a near-empty project yields a thin brief).

In the same question, let them say WHICH conversations matter, in product words — the default
is the most recent ones, and they only need to answer if they want something narrower:

- **"only failed runs"** → the LangSmith filter `eq(status, "error")`.
- **"only runs tagged X"** → `has(tags, "X")`; **"only runs with bad feedback on K"** →
  `and(eq(feedback_key, "K"), eq(feedback_score, 0))`. Other narrowings are LangSmith run
  filters too; write the filter yourself from their words, and keep the question itself in product words.
- **"only these conversations"** + a list of conversation (thread) ids → `thread_ids`. Each listed
  conversation is exported whole, every turn of it.

Both can be given at once: then only the listed conversations that have a matching run are
exported, still whole. This is part of the one `sink-project` ask, never a second question; when
nobody says, export without a narrowing. Headless (`workspace.run_driver: headless`): no narrowing.

**The export supports `source_kind` `langsmith` and `langfuse`, documented below.** The narrowing
above is LangSmith's (`filter`, `thread_ids`); a Langfuse export takes the most recent traces. If the
customer names another platform (Arize, Braintrust, …), say plainly that the SDK cannot export from it
yet and stop this command — never assume the LangSmith shape transfers to a sink nobody verified.

### Step 2: Gather the sink connection config (creds stay LOCAL)
Credentials come from the environment when set — LangSmith `LANGSMITH_API_KEY`; Langfuse
`LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (+ `LANGFUSE_HOST`) — else from what the user already
gave in this conversation, else ask for them under the `sink-project` sanction. Collect only what's
needed to instantiate the client:

| `source_kind` | `source_config` keys |
|---|---|
| `langsmith` | `api_key`, `project` (optional `api_url` for a self-hosted endpoint) |
| `langfuse` | `public_key`, `secret_key` (the key pair selects the project), optional `host` (self-hosted; else the SDK reads `LANGFUSE_HOST`) |

LangSmith also takes the Step 1 narrowing when there is one: `"filter": "<LangSmith run
filter>"` and/or `"thread_ids": ["<id>", …]`. With `thread_ids`, `--limit` counts conversations,
not runs. **Credentials are used to read the sink and are NEVER
written to disk** — only the resulting trace files + a credential-free manifest
are (the manifest records the narrowing under `selection`).

### Step 3: Stage the sink connection config

There is no job to name yet: the backend mints the grounding job's id in Step 4, when
`ground_workspace_start` runs. The grounding ledger and the derived trace brief are stored PER
WORKSPACE on the backend, so resolve this bench's id now and pass it as `workspace_id` in Step 4
(and on every later call that takes one) — the evidence lands under this bench only, and only this
bench's scenario/env/eval-gen picks it up. The brief comes from this delivery alone: the job `ground_workspace_start` starts runs the trace analysis and publishes the brief whether or not the operator waived grounding for the workspace (a waiver only changes how generator calls carry `ledger_version`), and a workspace that never delivers a trace part has no brief:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"   # → <bench workspace id>
```

Never omit it: an omitted id is the box-wide `default` slot any other bench can read or overwrite.

**Stage the sink connection config for the CLI.** The SDK CLI reads the sink
`source_config` from a JSON file (`--source-config`). It holds the api_key + project
used ONLY to read the sink locally — it is **NEVER uploaded**. Write it to a local,
restrictive-mode temp file and delete it right after the CLI returns:

```bash
CFG="$(mktemp)"; chmod 600 "$CFG"; trap 'rm -f "$CFG"' EXIT
printf %s '{"api_key": "<key>", "project": "<project>"}' > "$CFG"   # LangSmith shape
# Langfuse: printf %s '{"public_key": "<pk>", "secret_key": "<sk>", "host": "<url, only when self-hosted>"}' > "$CFG"
```

**With a Step 1 narrowing, first check that the installed SDK honours it.** An SDK that does not
know `filter` / `thread_ids` ignores them and exports the most recent traces instead — the wrong
conversations, with no error. `plurai capabilities` lists the keys this build honours:

```bash
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)" || { [ $? -eq 3 ] && exit 1; }
"$PLURAI_BIN" capabilities 2>/dev/null | python3 -c 'import json, sys
try: keys = json.load(sys.stdin).get("trace_selection_keys", {}).get("langsmith", [])
except ValueError: keys = []
print("langsmith narrowing:", " ".join(keys) or "none")'
```

Write a key only when that line names it. When a key the operator asked for is missing, do not
export without it: tell them the installed plurai cannot narrow the export and needs upgrading
(the resolver's upgrade line), and stop. With the keys present, write the config with a JSON
encoder — a filter carries double quotes, which a hand-typed `printf` breaks. Run it in the SAME
shell as the `mktemp` / `trap` block above, since the trap deletes `$CFG` when that shell exits:

```bash
python3 - "$CFG" <<'PY'
import json, sys
cfg = {"api_key": "<key>", "project": "<project>"}
cfg["filter"] = 'eq(status, "error")'       # only when the operator narrowed by runs
cfg["thread_ids"] = ["<thread id>"]         # only when the operator named conversations
json.dump(cfg, open(sys.argv[1], "w"))
PY
```

(Never write it under `plurai_sim/` or commit it — it carries the sink credential.)

### Step 4: Check the env, ingest the traces, then deliver the trace part

The trace part joins the workspace's grounding package: the agent's descriptions the scan delivered
stay in it, and this step adds the exported traces as one more part. Nothing here sends the backend
a model name, a cost knob or a topology file.

**1. Check the env (the anchor check), before anything is exported.** The trace part is delivered on
top of the ACTIVE env's descriptions, and `scenario_variables.yaml` is one of them: the helper reads
it, so its exit code says whether this shell is on the right env before any byte leaves. The output
is not used; the declared inventory the backend binds the traces to comes from the delivered
descriptions.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/skills/analyze-traces/scripts/topology_anchor.py" > /dev/null; echo "anchor exit=$?"
```

**Exit 3 is the wrong env, not "no anchor"**: the resolved workdir is not an existing directory (a
stale active env, or no `plurai_sim/` under this shell). Stop, fix the active env (`manage-envs`),
and re-run. Exit 2 (no workdir resolved, or a resolver refusal) stops the same way, and so does exit
4 (the file exists but is unreadable: not UTF-8, or an I/O error): fix the file and re-run. Any
non-zero exit stops this step; never export or deliver the traces after one.

**2. Write the declared names.** The scrub keeps a field whose name an agent description declares, so
the traces can join the declared keys. The names come from the declared part the scan built locally:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/analyze-traces/scripts/trace_delivery.py" names --workdir "$WORKDIR" --out "$ART/declared_names.json"; echo "declared names exit=$?"
```

Exit 0 wrote the list. Exit 2 means no description exists in this env: the setup was never recorded,
so say in product terms that `/simulation-scan-agent` records it first, close the window, and stop;
nothing was exported. Exit 3 means a description is refused as a whole: say which file to fix in the
product terms of Step 4b's table in `/simulation-scan-agent`, and stop.

**3. Run the SDK ingest command.** ONE invocation exports the traces from the sink
locally, then resolves this env's anonymization posture from `plurai.config.yaml`
(the `anonymize:` key, default raw): under **anonymized** posture it
**anonymizes every trace (fail-closed)**, keeping the declared names as field names; under the **raw**
(default) posture it exports the traces as-is and prints a loud
`RAW (anonymization off)` banner. Either way it bundles the result into a single
compressed archive, uploads that ONE archive to the backend's storage over the same
diagnostic connection as the MCP tools, and prints ONE **part entry** to stdout:

```bash
# Resolve WHICH plurai runs: the Bash tool does not activate the client's venv, so a bare
# `plurai` misses or silently hits a stale global install. The resolver picks the client's
# activated venv ($VIRTUAL_ENV) → the repo .venv → PATH, or fails with guidance.
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-traces)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# Preflight: bash cannot see which backend the MCP server is bound to. Call the read-only
# backend_identity() MCP tool first and paste its object here verbatim — that comparison is the
# one thing neither the CLI nor this shell can make alone. The pin is NOT re-checked here; the
# CLI owns it (below). Omitting the argument only downgrades this to a warning.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
rm -f "$ART/trace_part_entry.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" --refusal-stderr "$PLURAI_BIN" ingest-traces \
    --workspace-root "$PLURAI_DIR" \
    --source-kind "<langsmith|langfuse>" \
    --source-config "$CFG" \
    --declared-names "$ART/declared_names.json" \
    --out-dir "$PLURAI_DIR/traces" > "$ART/trace_part_entry.json" || { rc=$?; rm -f "$ART/trace_part_entry.json"; [ "$rc" -eq 7 ] && exit 7; echo "TRACE_INGEST=FAILED"; exit 1; }   # 7 = a version refusal: its text is on stderr, the file is gone
# --workspace-root is REQUIRED on every ingest call: omitted, it silently unarms the
# ingest target pin and pins a phantom plurai_sim/ under whatever directory the shell is in.
# Both paths go through $PLURAI_DIR for the same reason — a relative literal resolves against
# the session cwd, and under a named env $PLURAI_DIR is plurai_sim/envs/<name>.
#   add --limit <int> to cap the export while iterating
#   stdout → the part entry JSON, saved to $ART/trace_part_entry.json
#   stderr → first the resolved TARGET line (`plurai ingest-traces: uploading to <backend> (target
#            from the configured backend; <pin verdict>)`),
#            then a human-readable disclosure preview (trace count, bytes, scrub summary)
#            The target line prints on EVERY invocation, so it is there even when the run is
#            refused. READ IT: it is the authoritative statement of which backend this upload
#            reached. Nothing downstream re-checks it — the workspace ingest pin is gone,
#            so `assert-backend-identity.sh` before the upload is the guard.
#   uses the configured backend and token (or the saved login) —
#   the same backend connection the MCP tools above use; no storage flags needed.
```

A `WARNING (disclosure: home_named)` line from `ingest-traces` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

On exit 0, record the connection (provider and project only, never a credential) so a later `/simulate`
offers Yes/No instead of Connect:
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/simulate_plan.py" traces "$WORKDIR" --connect <langsmith|langfuse> --project "<project>"`.

- **The part entry is one JSON object**, `{kind: "trace_archive", sha256, bytes, posture,
  anon_key_fingerprint}` (the fingerprint only under anonymized posture). The SDK streams every
  trace (scrubbed under anonymized posture, as-is under raw) into ONE deterministic `.tar.gz` and
  uploads it as ONE storage object; `sha256` and `bytes` describe that archive, so the same traces
  export to the same entry. Under anonymized posture the archive carries a scrub map the backend
  reads. The SDK writes the disclosure preview (to stderr) + `.plurai/uploads/audit.log` itself;
  relay the preview counts, never trace bodies.
- **Fail-closed — a non-zero exit is a HARD STOP.** Under **anonymized** posture
  this can mean anonymization could not run (Presidio / spaCy missing); under
  **either** posture it can also mean the sink client is absent, the export
  returned 0 traces, or the diagnostic connection is unset — **NOTHING was
  uploaded**. Read the error yourself: fix local-environment causes silently
  (install the missing packages, set the connection vars — see Failure modes)
  and re-run; for a wrong project / empty export, ask the user in plain
  product terms. Never show the raw error.

**4. Read the entry back and deliver it.** The script checks that the entry is a deliverable trace
part and prints it as the `added` argument. The previous package version is the one the last
delivery recorded, empty on a machine that has none:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
echo "WORKSPACE_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
echo "PREVIOUS=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" previous --workdir "$WORKDIR")"
echo "ADDED=$("$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/analyze-traces/scripts/trace_delivery.py" entry --entry "$ART/trace_part_entry.json")"
```

An empty `ADDED=` means the check failed and its reason is on stderr (the SDK printed no part entry,
or one the backend would refuse). Run step 3 once more; a second failure: run the contact hand-off (`stop_message.py contact --done`). Then call the backend tool, with the three lines as its arguments and no other
argument (no `removed`, no `overrules`; Step 6c's re-export of a held corpus and an explicit re-analysis below are the deliveries that add `removed`):

```
ground_workspace_start(
    workspace_id=<bench workspace id>,   # the WORKSPACE_ID= line
    previous=<the PREVIOUS= line; leave the argument out when it is empty>,
    added=<the ADDED= array exactly as printed>,
)   →  GroundingStarted { job_id, package_version, package_created, job_reused, … }
```

It returns at once. The backend fetches the part by its hash, adds it to the package built on
`previous` (the descriptions stay), and starts the grounding job that analyzes it. **Delivering the
same bytes again sends nothing new:** the package version is the one the workspace already has
(`package_created` is false) and the job is the existing one, so re-running this skill on an
unchanged export keeps one version. Treat that result like any other.

**A finished analysis is final.** A later scan, rescan or input edit that renames or adds tools, arguments or fields
never analyzes delivered traces again, and a name the analysis never saw has no trace evidence. Re-analyze only when
the user asks for it: call `ground_workspace_start` with `previous` and `removed=["<the sha256 in
$ART/trace_part_entry.json>"]` and no `added`, remember and wait for that job (item 5, Step 5), then run items 3 and
4 over to upload and deliver the export again. A part whose analysis failed needs none of this: delivering it again
retries it.

- A refusal reads `package refused (<cause>)`, and nothing changed on the backend. `stale_previous`
  means the workspace has moved on (a teammate delivered, or a fresh clone has no record): read the
  head from the refusal text, pasted into a quoted heredoc so none of its characters reach the shell, and
  call again ONCE with it as `previous`.

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" head --refusal - <<'REFUSAL'
  <the refusal text>
  REFUSAL
  ```

   `previous_unknown` means the recorded version belongs
  to another backend: delete `$WORKDIR/.grounding_package` and call again ONCE with no `previous`.
- **`mixed_posture` and `fingerprint_mismatch` are a posture mismatch**: the traces were prepared
  under a different anonymization setting than the evidence already in the package (the knowledge
  corpus, or earlier traces), and a workspace holds one setting. This is the error posture's item 2:
  say in product terms that the traces and the evidence already delivered were prepared with
  different anonymization settings, that one environment holds one setting, and that the part
  prepared the other way has to be exported again under the setting this environment records
  (`anonymize:` in `plurai.config.yaml`). Never show the cause code or the backend's text. Render the
  failure hand-off and stop: nothing was delivered.
- Any other cause (`hash_mismatch`, `empty_delivery`, …): run step 3 once more, call again once, and
  run the contact hand-off (`stop_message.py contact --done`) on a second refusal.
- The tool is idempotent on the same previous version and parts, so a transport failure with no
  envelope is called again once; a second failure: run the unavailable stop (`stop_message.py unavailable --done`).

**5. Remember the package and the job.** Run this with the object the tool returned (an object,
never the refusal text):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" record --workdir "$WORKDIR" --workspace-id "<the WORKSPACE_ID= value>" --result - <<'JSON'
<the JSON object ground_workspace_start returned>
JSON
```

It writes `$WORKDIR/.grounding_package` (the package version the next delivery builds on) and
`$WORKDIR/.grounding_job` (the job that consolidates it). Both are machine-local handles, ignored by
the managed block. Save the `job_id` from the same object; Step 5 waits on it.

**Disclosure + audit stay with the SDK.** The `plurai ingest-traces` Bash call is the consent
chokepoint (normal Bash approval, naming the exact command), and the CLI writes the preview +
`.plurai/uploads/audit.log` (the previewed bytes are already pseudonymized). `ground_workspace_start`
carries no trace bytes: only the part's hash and size, and the workspace.

**Size note:** the analysis covers the whole corpus and takes minutes. There
is no cheap mode to ask for, so size the corpus you ingest rather than expecting a
switch here. Questions about usage, credits or spend go to the Plurai web dashboard;
this skill shows no figure.

### Step 5: Wait for the grounding job — in the foreground, until it is terminal
The grounding job analyzes the part itself, and the suite's Step 0b waits for the same job, so a
job that is already done returns at once. Wait with ONE blocking call. The sleep lives inside the
script, so the wait cannot overlap the read:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id>
```

`<job_id>` is the grounding job's id, the `job_id` that `ground_workspace_start` returned.

**Call this with a Bash `timeout` of `600000`** — the harness default is 120 000 ms, which kills the script 420 s inside its own budget and looks exactly like a wedged backend.

**This wait is never backgrounded and never handed off.** Nothing re-invokes this session while the job runs: a headless `claude -p` process exits when the turn ends and takes any background task with it, so the customer is left with no brief and no error. Therefore, in every session:

- Never run any command in this step in the background: not `poll_job.sh`, not `wait.sh`, not a `sleep`, not a hand-written loop. A hook refuses the call.
- Never write that the session will "pick up again", "resume on its own", or that you will "check back". It will not.
- Never end the turn while the job is `queued` or `running`, and never render `Trace brief in progress` for this wait. On exit `14` run the same command again, in the same turn.
- End the turn only when the job is terminal: `done` or `failed`. Exits `77` and `7` also end it, as a failure hand-off that says what is wrong (rejected token) or, for `7`, the printed update text, said verbatim and alone; on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`).

The final message is read from the job's end state, never from a prediction: `done` → read the job below, then Step 6 and the hand-off; `failed`, exit `77` or `7`, or a second exit `69` → the failure hand-off (for `69`, the stop above).

The analysis runs several minutes at a time with NO update in between, and the job waits its turn when another delivery of the workspace is being consolidated. The whole job runs 5-40 minutes or longer, so **several exit-14 re-invocations are the normal shape of a healthy job, not a stall signal.**

Exit codes: `0` done · `13` failed · `15` an id the backend does not know (re-check or re-launch; the backend is fine) · `14` budget spent, backend healthy, so re-invoke · `12` no `GET /jobs/{id}` on this backend: use `bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110` in the FOREGROUND, then `job_status(id=job_id)`, serially, repeating until `done` or `failed` (`wait.sh` takes a duration and nothing else; never hand-roll a loop on a job status) · `77` bad token · `69` job state unknown: backend down, or up with the job route faulting (the script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)) · `7` Plurai refuses this version of the plugin or its SDK: the plugin's update text was printed; say it verbatim and alone, then stop. Full recipe: `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`.

On exit `0`, call `job_status(id=job_id)` ONCE for the full envelope and read what the job says about the trace part:
```
job_status(id=job_id)
  →  Job { id, status, phase, error, detail }
     detail: { settled, waiting_on, held_by, failed_parts, processing_jobs, ledger_version, queued_behind }
```

The part's id is the `sha256` in the `ADDED=` array. Read the envelope in this order:

| The envelope says | Do |
|---|---|
| `failed_parts` names the part | The analysis failed and the version carries the gap; read its cause yourself and never relay it. A cause saying no agent description declares a key means the setup was never recorded: say in product terms that `/simulation-scan-agent` records it and this command runs again after. Any other cause is fixed per this table where it names a repair, and otherwise, or when the re-run after the fix fails again, run the contact hand-off (`stop_message.py contact --done`). Skip Step 6 and Step 7 and render the failure hand-off. |
| `status: failed` | Read `error` yourself. A description cause (Step 4b's table in `/simulation-scan-agent`) is said in those product terms; anything else is run the contact hand-off (`stop_message.py contact --done`). A failed job is final: never deliver again to retry it. |
| `waiting_on` still names the part | The backend did not process it: nothing here fixes it, so run the contact hand-off (`stop_message.py contact --done`). |
| `held_by` is not empty | A finding holds the version unsettled. The brief and the trace failures do not depend on it, and no suite pins an unsettled version. Step 6c reads the findings and handles them after the pull: a trace corpus held for its environment is attested or exported again there. Log the counts, `append_loop_log.py --kind note --finding "grounding not settled: held_by=<n> waiting_on=<n>"`, no ids, and carry on. Never ask the user and never put it in the hand-off. |
| `processing_jobs[<the part's sha256>]` | The id of the trace-analysis job, `<analysis job id>` below. Missing: skip the brief confirmation in Step 6. |

### Step 6: Finish + confirm the brief
Call `job_status(id=<analysis job id>)` ONCE. On `status='done'` that envelope
carries `detail.published`, `detail.n_corpus_units`, `detail.brief_files`, and the
run's coverage: `detail.units_failed` / `detail.units_oversize`, with
`detail.meta.blind_topics` naming any brief section that came back empty. Report a
non-zero one to the user — a published brief is not the same as a complete one.
Confirm `detail.published` is true, then call:
```
get_trace_brief_meta(workspace_id=<bench workspace id>)  →  { present: true, source: 'trace-analysis job <id> ...', files: {...} }
```
Confirm `get_trace_brief_meta` shows the brief. The generators that read the brief pick it up from
there. When it shows none although the part was analyzed, the analysis of these exact traces ran
for another workspace and was reused: what the traces showed is on the ledger, and there are no
brief files for this workspace, so write `trace_failures.json` below as it comes and skip the display
copy.

Then write the brief's trace failures to `$ART/trace_failures.json`, whole, on every run (with
`--no-trace-scenarios` too — the overview page reads it):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; WS="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" get_trace_failures "{\"workspace_id\": \"$WS\"}" > "$ART/.trace_failures.json.tmp" \
  && mv "$ART/.trace_failures.json.tmp" "$ART/trace_failures.json" \
  || { rm -f "$ART/.trace_failures.json.tmp"; printf '{"present": false}\n' > "$ART/trace_failures.json"; echo "TRACE_FAILURES=unserved"; }
```

`TRACE_FAILURES=unserved` means the backend does not serve trace failures (or the call failed): the
file records none, and Step 7b carries its `not written` line. Never write or edit this file by hand.

Then pull the customer's read-only display copy of the brief into `$ART/trace_brief/` (summary in
`$ART/trace_brief.json`; the backend stays the brief's single source of truth, so nothing reads this
copy back):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_pull.py" traces "$WORKDIR" --job-id <analysis job id>
```

It prints one JSON line. Exit 1 (a fetch failed; the previous copy stays) or 2 does not stop the
flow: say nothing about it.

### Step 6b: Pull the grounding mirror

Run this once, after the grounding job, before the hand-off and before the apply step. It copies the latest
grounding ledger version to `$WORKDIR/grounding_ledger.yaml`, so Step 7, the next skill and the next
commit see what the traces showed; `${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md` is the canonical source.
It never fails the skill and never goes in a report. It does not pin: the suite pins the settled version it
builds on at its own Step 0b, and a suite already built keeps its pin until the next stage boundary.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

Carry on whatever `MIRROR=` line it prints: `unverified` and `ungrounded` change nothing here. No `MIRROR=`
line means the client pin or the wrong-backend guard stopped the block; run the contact hand-off (`stop_message.py contact --done`). Never write, patch or "fix" `grounding_ledger.yaml` yourself: the pull replaces it whole, and
a fact is changed in the agent's descriptions.

### Step 6c: Handle the grounding's findings

`${CLAUDE_PLUGIN_ROOT}/docs/grounding-findings.md` is the canonical handler; read it the first time. It runs once the pull of Step 6b is done, on the job Step 5 waited for. The agent
decides alone: never ask the user about a finding or an overrule, and never put one in a hand-off or a
report.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "FINDINGS=none"; exit 0; }
STATUS="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}")" || { echo "FINDINGS=unread"; exit 0; }
printf '%s' "$STATUS" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" show --status -
printf '%s' "$STATUS" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" log --status - --art "$ART"
```

Act on each finding by its `handle=`, holding ones first, as that document says. Only an evidence fault holds: remove a
failed part (`handle=remove_part`) and deliver again (`/simulation-scan-agent` Step 4b items 2, 3, 4 and 5, never 2b). A value conflict never holds. It is
`handle=accept` whatever its tiers: make no description edit and no delivery for it, however its remedy reads; the losing
description keeps its value and `log` keeps the losing value as a candidate scenario. A code-versus-trace finding is `handle=accept`: the backend decides it, the mock keeps the code value, and `log`
keeps the production value as a candidate trace scenario. A trace part never holds the version: one whose analysis failed is
`handle=accept`, and generation goes on without it. A trace corpus skipped for its environment (`handle=attest_or_reexport`, the `trace.corpus_environment_unattested`
finding) is not analyzed until you decide it, on what you know of the export. When you narrowed it to this agent (its project, its runs or its
environment tag) so that the other environments the census lists are not the agent, overrule it: the reason says what
you narrowed the export by, and the call is `ground_workspace_start` with `previous` and `overrules` and no `added`.
Otherwise export the traces again narrowed to the one environment (Steps 3 and 4 over) and deliver that archive with
`removed` set to the array `grounding_findings.py remove --part <the finding's archive_sha256>` prints (this step keeps no copy
of the job: pipe a fresh `job_status` into `remove` or `overrule` with `--status -`), so the held
part leaves the package in the same call. After each delivery you make for a finding, record it,
wait for the job, pull the mirror again and read again. Three rounds that leave a holding finding end it: carry on,
because the version stays unsettled and unpinned and the suite's Step 0b reads it again. Whatever the findings say stays
internal.

### Step 7: Apply the brief — generate what's missing, EDIT what already exists

The brief is now the central default for FRESH generation, but it does NOT retroactively change
artifacts you already generated. So the brief is only useful if you act on it — branch per artifact.

**The tool facts are the ledger's, not the config's** (no question). The delivered trace part puts what
the traces showed (per-tool call counts, argument and return shapes, error forms) on the ledger's tool
keys, and the generators read them from the pinned version. This step edits nothing in
`$PLURAI_DIR/plurai.config.yaml`, writes no `returns` source of its own and runs no tool-fact script: a
value the traces contradict is the ledger's finding to carry, and a value that is wrong in the agent's own
description is changed in that description by a scan, never here. Read what the mirror holds for the
traces (Step 6b pulled it):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/analyze-traces/scripts/trace_delivery.py" evidence --workdir "$WORKDIR"
```

It prints one JSON line. `trace_tools` counts the tools the mirror holds trace evidence for. At `0` the
traces showed none of the declared tools (or the mirror is not the version that analyzed them): the env
has nothing to take from them, so leave the env out of the apply set below.

**Then detect what already exists** (this is the generate-vs-edit signal):

- **env generated?** — the id is in `$PLURAI_DIR/.env_id` (the bare layout writes it there and
  nowhere else — check it FIRST, or a customer who never used named envs reads as "not generated"
  and their existing mock silently keeps the pre-brief world), else an `env_id` in the active
  env's own `$PLURAI_DIR/plurai.env.yaml` (the named layout; never a sibling env's manifest), else
  `list_artifacts(kind='env')` returns one for this agent.
  (`.mock_service_url` alone is a stale per-run handle — don't rely on it.)
- **scenarios generated?** — `$PLURAI_DIR/.scenario_set_id` exists, or `list_artifacts(kind='scenarios')`.
- **evals generated?** — `$PLURAI_DIR/.eval_set_id` exists, or `list_artifacts(kind='evals')`.

**Then confirm the whole apply set in ONE question, and act on the answer per surface.**
Sanction it immediately before the ask — `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction apply-brief` —
and ask ONCE, `multiSelect`, one option per surface that exists (env when `trace_tools` is above 0, scenarios, evals),
each option saying what applying does to it (generate it grounded, or edit the existing one).
**One sanction buys one ask CALL, not one question**: asking per surface means
the second and third calls are DENIED, silently — and a denial is not a stop, so the brief
would be applied to surfaces the user never confirmed, or skipped on ones they wanted. The
surfaces they leave unselected are skipped; say which in the report. Then, per selected
surface:

- **NOT yet generated** → generate it; the backend AUTO-consumes the brief, no extra wiring. Env-gen, scenarios and evals are ALL `/simulation-generate-suite` — so a brief published BEFORE that command runs is the cheap path: the mock, the scenarios and the evals are all BORN grounded and nothing needs an edit session.
- **Already generated** → EDIT it per the brief (don't silently leave it stale). Pass
  `include_trace_brief=True` so the backend injects the relevant brief slice:
  - **evals** → open `/edit-evals` (`edit_session_start(target_kind='evals', target_id=<eval_set_id>, workspace_id=<bench workspace id>, ledger_version=<the pin>)`),
    then `interact_edit_session(session_id, "apply the production trace brief — make the evals catch the
    observed failures and tolerate the observed anomalies", include_trace_brief=True)`; verify + `end_edit_session`.
  - **env** → open `/edit-env`: `edit_session_start(target_kind='env', target_id=<env_id>, workspace_id=<bench workspace id>, ledger_version=<the pin>)` →
    `interact_edit_session(session_id, "make the mock reproduce the observed production world state + tool
    returns", include_trace_brief=True)` → verify → `end_edit_session` (REDEPLOYS the env so the live mock reflects the edits).
  - **scenarios** → open `/refine-scenarios`: `interact_with_scenario_request(session_id,
    "steer the scenarios toward the observed production behavior", include_trace_brief=True)` → materialize
    (the scenario CONTENT brief — user_style / world_state — re-injects automatically at generation).

Under `/simulate` this ask takes a default. It is **`env-only`** when a new suite build follows in the run and `trace_tools` is above 0:
the brief is applied to an EXISTING env through `/edit-env` as above, and scenarios and evals are skipped,
because that build regenerates them from the brief (with no env yet, nothing is edited). Otherwise it is
**`none`**: a rerun, or a finish that resumes a stopped build, keeps the mock its scenarios were seeded for,
and the published brief grounds the next new build.

Surface what you did per artifact (generated-fresh vs edited-in-place vs skipped) so the user sees the
brief was actually applied end to end.

### Step 7b: Turn the brief's trace failures into trace scenarios (default)

Skip this step when the operator passed `--no-trace-scenarios`. Otherwise run it after Step 7's
apply set and before the hand-off, **inside this same `traces` window** — do not close it, and do
not arm another. `/trace-to-scenarios` is a sub-skill and arms nothing. By default it writes
every located trace scenario without asking; its one question, the `trace-scenario-selection`
confirmation, comes only when a candidate overlaps a folder at another cut or more are located
than the final batch has room for. It is budgeted to this flow and sanctions itself at its own
step.

**1. Does the brief list a trace failure?** Read `$ART/trace_failures.json` — Step 6 wrote the `get_trace_failures(workspace_id=<bench workspace id>)`
answer there, `{ present, workspace_id, failures: [...], ... }`. With `present: false` or an
empty `failures`, the brief observed no trace failure: skip the rest of this step and add no
line to the hand-off. After `TRACE_FAILURES=unserved` the rest is skipped too, and the
hand-off carries `Trace scenarios: not written — the backend does not serve trace failures yet.`

**2. Run the brief-driven mode.** Invoke `/trace-to-scenarios --from-brief` (the Skill tool, with
`--from-brief` as its arguments, followed by `--max-new N` and `--max-total N` exactly as the
operator passed them to this command) and follow its *Brief-driven mode*, B0 to B9, in this session.
Its B3 is the read you just made: it uses `$ART/trace_failures.json` and does not call
`get_trace_failures` again. It writes folders under `$PLURAI_DIR/trace_scenarios/`, stages them,
and stops; it uploads nothing.

**3. It never fails the brief.** The brief is published and applied whatever this step does. A
refusal — anonymized posture, traces the installed SDK has no reader for, the ingested traces missing from this
machine, an SDK below 0.9.0 — or any other failure is one line in the hand-off, never a stop,
never an escalation and never a retry loop. Carry exactly ONE of these into the hand-off, below
the anchor line:

- `Trace scenarios: <N> written (<folder>, <folder>, …) — they join the suite at the next /simulation-generate-suite.`
  when B8 wrote at least one folder. The folder names are the `trace_<slug>` ones B9 lists.
- The `cap_line` B9 printed, verbatim, when the total cap was full and nothing was written:
  `Trace scenarios: 0 written — <on disk> already on disk (cap <cap>); delete some or raise the cap: <N> failures not written: <name>, <name>.`
- `Trace scenarios: not written — <reason>.` otherwise, the reason in product terms, one of:
  `this env is anonymized, and a trace scenario carries a production conversation verbatim` ·
  `the installed plurai has no reader for these traces` · `the ingested traces are not on this machine` ·
  `the installed plurai is older than 0.9.0` · `no trace failure showed at a turn to cut at` ·
  `none was selected` · `every trace failure already has one or was retired` · `the backend does not serve trace failures yet` ·
  or one plain clause for anything else.

**When the written line is carried, the privacy line follows it**: the `Privacy:` line B9
relayed from `stage-inputs.sh`, verbatim, or the sentence B9 printed in its place. The folders are production conversations, and nothing
else tells the operator that git tracks them.

Nothing else from `/trace-to-scenarios`' report reaches this hand-off: the per-failure detail
(turn 0, not located, skipped) stays in its own output for the operator.

**The final summary opens with the anchor line** (report_guard validates it), in product
terms — what the brief grounds, never the pipeline's internals:

> **Trace brief ready** — grounded in N production traces; env / scenarios / evals now generate against real behavior.

When Step 7b ran, its one `Trace scenarios:` line follows the anchor line; the anchor itself never
changes. When Step 6's pull wrote `$ART/trace_brief/README.md`, the next line is the one
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Read the brief →" "$ART/trace_brief/README.md"`
prints (never type the path).

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

First check that this stage left every file the page reads:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check traces --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step traces --flowdir "$FLOWDIR"
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step traces --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

**Ending a turn while the analysis is still running** — a mid-flow message is style-validated
too, so lead it with the in-progress anchor: `Trace brief in progress`. It answers exactly one
question, does this turn owe the hand-off skeleton; it never closes the window, it is valid only while harness-tracked work other than the job wait is in flight (a spawned subagent — the harness records it and re-invokes you when it finishes; a detached `nohup` does not count and the guard blocks the anchor). The job wait itself (Step 5) is never that work: it stays in the foreground, so a running job never ends a turn, and it exempts the message from nothing else (no cost figures, no question-shaped ending).

**The window closes itself on the validated hand-off** (report_guard removes the marker
when the summary passes with the `Trace brief ready` anchor or the failure hand-off —
never `rm` before rendering). Early exits close it own-marker-guarded (not an exit that ends on the contact hand-off: render that hand-off and leave the window to its check):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close traces
```

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

## Report doctrine — the final message presents the PRODUCT, not the build

Your final message is the product hand-off, and it follows the skill's report skeleton exactly:

1. **Lead with what was built and what it catches/does**, in the customer's own domain language —
   concrete behaviours, not adjectives, and never what it took to build. A situation the conversations put to the
   agent (a complication such as pressure after a refusal) is named only when `run_numbers.py covered <run.json>` lists
   it for the run you present (it prints `null`: name no situations as covered); checks and flows are named as before.
2. **One next action** — the single command or step the user takes from here.
3. **No SUITE-gap narration.** Accepted gaps, uncovered flows, and known limitations of the SUITE
   are not final-report content — the rendered artifacts (scenarios.md, evals.md, coverage charts)
   carry the full picture for a user who wants it. The report never lists what the suite does not
   cover. That line is about the SUITE, never about findings: the defects the suite found in the
   CUSTOMER'S AGENT are the deliverable of `/simulation-run` and `/simulation-fixes`, and a licensed line in
   `/simulation-generate-suite`'s and `/analyze-simulation`'s hand-offs — rank them,
   name them concretely in domain language, and quote the agent's own words as the evidence.
   `/simulation-scan-agent` caught nothing — it ran no scenarios — but it carries one licensed line
   for the POTENTIAL agent issues the scan recorded because they narrowed what the suite tests:
   named and counted, never ranked, and always marked as possibly intended.
4. **Run-INTEGRITY facts are RESULTS, not gap narration — ONE line, when they apply.** What was
   NOT executed, trials that did not run (the `run_integrity` line `run_numbers.py chat` prints, verbatim —
   never a self-counted `errored`), scenarios that landed INCOMPLETE on the
   per-scenario time budget (graded to the turn each reached, in neither the passed nor the failed
   count), a flow REMOVED because it cannot complete inside that budget, a model that was
   substituted or
   overridden, a regeneration that shipped short of its target count: these change how the numbers
   must be read, so the reader is owed them. State the fact flatly and move on — no cause, no
   repair story, no apology — e.g. `Run integrity — 2 of 27 trials could not run on our side.
   The results above cover the 25 that ran.` This is the ONLY thing item 3 lets in: what the suite does
   not COVER stays out. The removed-flow case is the one line that names a knob, because the
   removal is reversible and only the user can decide to reverse it: `Run integrity — flow <X> was
   removed from the suite: it cannot complete inside the 40-minute scenario time budget; extending
   the budget (--scenario-timeout) is your call.`
5. **The product's confidence in its own result is not report content.** How sure the suite, its
   evals or its checks are of a number — sample sizes, thresholds, audits, release checks — stays in
   `loop_log.md`; the reader gets what the result means and the one action that
   changes it (a suite that must be rebuilt: `/simulation-generate-suite`, then `/simulation-run`).
   One line is licensed against this item and item 3: `/simulation-generate-suite`'s "Evals to optimize —" line,
   pasted as `eval_rounds.py flagged-line` prints it (nothing printed: no line) — it names the evals flagged for
   improvement, never a tally of their results, a cause or a repair story.

Everything you found and fixed along the way is process, not content: no defect chronology, no
iteration/critic history, no internal warnings, no spend. Findings about the CUSTOMER'S AGENT are
the product's value — present them as what the suite caught, never as flaws of the suite.

### While the flow is still running — say nothing

Between the invocation and the hand-off the default is **silence**. The tool stream is the progress
indicator; the user cannot act on a build in flight, so commentary buys them nothing and risks
handing them the build's internal vocabulary. The phase you are in, the loop, a sub-agent's report,
what you assumed, what you got wrong, what you are re-checking — that is process, and process is
never narrated however well it is phrased. That includes a one-line note between tool calls ("Now
…", "Step 4: …", "Writing … now"): text written mid-turn reaches the user before any check can stop it.

Exactly four things may be said before the hand-off:

1. **A sanctioned gate or progress line** this skill names at that step, in its words. The page's progress line is
   one: after each milestone fill, and each time a sub-agent a step names returns, say what
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" "$WORKDIR" --flowdir "$FLOWDIR"` prints,
   verbatim (nothing printed: say nothing; the line the harness hands you when a sub-agent returns is that line, said
   once).
2. **A blocker you cannot clear from here** — error-posture items 2 and 3, in the words those items
   give you. The test is *the flow cannot continue*, not *something went wrong*: anything the
   skill's own recovery covers — a retry, a regeneration, a repair round, an empty batch the loop
   re-runs — is error-posture item 1. Fix it and say nothing.
3. **The in-progress anchor**, on a turn that must end with harness-tracked work in flight.
4. **The hand-off** itself, per the skeleton above.

Findings about the CUSTOMER'S AGENT are the product, and they are delivered in the hand-off (or at
a gate that exists to present them). Mid-flow they are not actionable and read as alarm.

### The rules the report gate checks

A hook reads the last message of every turn while a flow runs, and answers a broken rule with one short correction line.
Write the message to these rules the first time:

- **No spend.** No `$` figure for the build's own cost and no `total_cost_usd`. The agent's own amounts (a price it quoted,
  a charge it made) are evidence and stay.
- **Links open.** A local link has to open for the reader: for a report, paste the line `report_link.py link` printed for
  it; for a code location, link its absolute path (`/abs/file.py:42`). Never hand-type a relative path.
- **No stall.** The flow is pre-authorized. Do not end a turn on a question: decide from the skill's decision table, and ask
  only through a sanctioned gate. Timing, context and budget are never a reason to stop early.
- **A turn ends in one of four states:** it lands the flow with the hand-off; it ends on a sanctioned question to the user
  (or on the run driver, for a prerequisite only the run driver can supply); background work is in flight that wakes the
  session when it finishes (a background command or a spawned reviewer); or the harness moved an MCP call to the background
  because it outran its window. A mid-flow line reports work, it does not start any: start the work, or do the next step now,
  and start a wait in the background (never a detached `nohup`). Where nothing wakes the session after a turn (a headless run,
  or a harness that never does), wait inside the turn, in the foreground: `poll_job.sh` for a backend job and `run_wait.sh`
  for a run, re-run until it is done, and a reviewer subagent spawned in the foreground, never in the background.
- **A mid-flow line with a wait.** When the turn ends on the mid-flow line with background work just started, add one
  sentence: roughly how long this stage usually takes, in minutes, and that this session resumes by itself, with nothing for
  the user to type. The estimate has to hold. A spawned reviewer usually takes 10-20 minutes, so never say "about 5": give a
  range that tops out at 15 minutes or more, or say it can run longer. While the suite calibrates the simulation, say only
  `Suite: in progress — calibrating the simulation, about 10-20 minutes; this session picks up again by itself.` and name no
  calibration run, score, reviewer or fix.
- **Exact lines.** The hand-off opens with one of the skill's anchor lines, verbatim, and while work still runs the mid-flow
  line is used verbatim. Never invent a variant: a claim of completion without an anchor is refused. Two lines are computed by
  a script and appear verbatim: the scan's run-integrity line (`bench_manifest.py integrity-line`) and the suite's rung line
  (`lane_pin.py anchor`).
- **The hand-off presents the product.** No raw backend vocabulary (warnings, tracebacks, job internals), no build chronology
  (iterations, critic passes, cycles), no narration of suite defects you found and fixed, no suite-coverage gaps (run-integrity
  facts keep their one line), no doubt about the suite (failures are findings about the agent), no internal quality caveat,
  and none of our own machinery on a customer surface. `/simulation-run` and `/simulation-fixes` present the findings: what
  the suite caught in the AGENT, concretely, ranked, in the customer's domain language, quoting the agent. The suite stages
  present what was built and what it catches; defects the suite caught in the AGENT are the product working, so keep that
  line, with the AGENT as the subject of each clause ("the agent quotes a refund window the policy does not grant"), never the
  scenario. `/simulation-scan-agent` ran no scenario: it keeps one line for the POTENTIAL agent issues the scan recorded, with
  the AGENT as the subject, unranked and marked as possibly intended. An example the suite hand-off quotes is an audited true
  red of the scored final run, verbatim with its scenario and turn, never a qualification run.
- **Over an unanswered prerequisite** (a gate only the run driver can answer, and no answer came back), write no hand-off:
  end the turn on the run driver and say in product terms what they must supply. If a person is here, ask it again through a
  sanction; their answer releases the hold. The failure hand-off stays available, and it carries no anchor line.

## Hard rules
- **Credentials never leave the machine and are never written to disk.** Only the
  exported trace archive (and, under anonymized posture, its scrub map) is uploaded.
- **Anonymization is posture-conditional, not unconditional.** Under
  **anonymized** posture, `plurai ingest-traces` (Step 4) anonymizes every trace
  before it uploads and is fail-closed — a missing Presidio/spaCy aborts with
  nothing uploaded, and a non-zero exit is a HARD STOP. Under the **raw**
  (default) posture, ingestion uploads traces as-is with a loud
  `RAW (anonymization off)` banner; that upload is governed by the customer's
  DPA with Plurai. Either way, every upload records its posture in the part entry
  and `.plurai/uploads/audit.log`.
- **Never print trace bodies** to the console — they may be large / sensitive.
- **`delete_trace_brief(workspace_id=<bench workspace id>)` mutates backend state** — confirm with the user before
  invoking it. Inside this flow's own window that confirmation is a sanctioned ask, and it
  has a gate: first run
  `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction delete-confirm`. Without
  that marker the question is DENIED and a denial is not a stop — the delete would then run
  unconfirmed, which is the one outcome this line exists to prevent.
- **Export is `langsmith` or `langfuse` only** (each with its own `source_config` keys, Step 2).
  For any other sink, say so and stop — don't assume either shape transfers to it.

## Failure modes + recovery
- `resolve_plurai.py` exits ZERO but prints `NOTE: shadows …` → the resolve SUCCEEDED, so this is
  not an error — but it is a FINDING: a second `plurai` install on PATH is why the next command on
  this machine can behave differently from this one. Carry the line into the hand-off verbatim.
  Exit 3 with `REFUSED` is a runner below the SDK floor (or one the client pin forbids): run the
  upgrade command the line names, then retry.
- `resolve_plurai.py` exits non-zero (Step 4) → no usable `plurai`: not in the client's venv
  (activated or repo `.venv`) or PATH, or the found install predates the `ingest-*` CLI →
  install/upgrade IN THE CLIENT'S VENV as the resolver's stderr says (the line
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run
  the resolve line.
- `plurai ingest-traces` nonzero exit, stderr names Presidio / spaCy (anonymized-posture
  envs only) → `plurai-simulation-sdk[anonymize]` or a spaCy model isn't installed (fail-closed under
  `anonymize: true` — nothing uploaded). Install
  the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints) +
  `python -m spacy download en_core_web_lg`, then re-run.
- `plurai ingest-traces` nonzero exit, stderr names the diagnostic connection →
  Plurai is not logged in in this shell (fail-closed). Run the
  `harness-setup` skill (Precondition 3), then re-run.
- `plurai ingest-traces` stderr `TraceExtractionDependencyError` → the sink client isn't
  installed (`pip install langsmith` / `pip install langfuse`); re-run.
- `plurai ingest-traces` stderr names 0 traces → wrong project / filter / thread ids /
  credentials; the line names the filter and ids it tried. Fix a wrong project or credential in
  the staged `--source-config` JSON and re-run; when the narrowing matched nothing, tell the user
  in plain terms ("no failed runs in that project", "none of those conversations were found")
  and ask whether to widen it (the `sink-project` ask again, sanctioned the same way) — never drop
  the narrowing on your own. Headless has no narrowing, so there this is a wrong project or
  credential: report it and stop.
- `plurai ingest-traces` stderr `LangSmith rejected filter …` → the filter you wrote is not valid
  LangSmith syntax (nothing was uploaded). Rewrite it from the operator's words using a form the
  line names (`eq(status, "error")`, `has(tags, "…")`, …) and re-run; never ask the operator to
  write a filter.
- `plurai ingest-traces` stderr `… holds files that are not a previous export` → `$PLURAI_DIR/traces`
  holds files the SDK did not write, and every file there would be uploaded. Nothing was deleted
  or uploaded. Tell the user which directory it is and ask them to move those files aside; never
  delete them yourself.
- `failed_parts` names the part with a cause that says trace analysis is not configured on this
  server → backend-side, no repair step here — run the contact hand-off (`stop_message.py contact --done`).
- `ground_workspace_start` refused with `mixed_posture` or `fingerprint_mismatch` → a posture
  mismatch; Step 4 says what to tell the user. Nothing was delivered.
- `status='failed'` → read `terminal_reason` and `error` YOURSELF — never relay them to the
  user. Retry per the retry rule (Error posture). A failure inside the analysis that names the
  corpus's shape (too many or oversized traces) goes through this skill's repair step first:
  export again with a smaller `--limit` (Step 4's export command) and deliver the new archive
  once. If nothing here fixes it (a provider-key issue, say), or the re-run after the fix fails again,
  run the contact hand-off (`stop_message.py contact --done`) and stop.
