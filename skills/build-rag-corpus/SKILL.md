---
name: build-rag-corpus
description: >-
  Build a knowledge corpus for an agent's RAG or SQL tools. The SDK extracts chunks locally and uploads them; the backend builds the knowledge graph and test set. One corpus per tool. Run after /simulation-scan-agent for agents with retrieval or text2SQL tools.
---

# Build RAG/SQL Corpus

You are the LOCAL Claude Code agent. The customer's agent has one or
more retrieval (RAG / vector) or text2SQL tools, and they want the backend
to support them properly: a ground-truth **corpus** (knowledge graph +
testset) built from each real data source. The corpus is the
**ground truth scenario generation builds the retrieval scenarios
FROM** — it is later consumed by separate skills to (a) generate
RAG/SQL scenarios + evals (grounded questions with known answers, real
entities seeded into world-state) and (b) make the deployed mock answer
RAG/SQL calls faithfully. (a) is the primary purpose and is independent
of the tool's mock mode: even a `mode: real` tool yields **0 RAG
scenarios** without a corpus, because the generator has no knowledge
flow + no ground-truth answers to author from.

**One corpus per RAG/SQL tool = one knowledge base.** An agent often
owns *several distinct* knowledge bases — e.g. a personal-memory RAG
(recall of prior conversations) AND a company-docs RAG (policy/FAQ).
These are different question *kinds* backed by different data, so each
gets its OWN corpus with its own domain `task_description` (which
becomes the base's **label**). You run the extract→deliver cycle
below **once per tool**; the suite records the bases together in a
`plurai_sim/.knowledge_bases` manifest once the backend has built them. (A per-customer/per-tenant tool
maps to a *list* of corpus ids — many copies of ONE base; that's the
within-base dimension, orthogonal to having several bases.) An agent
with a single RAG tool is just the one-base case.

The split that matters:

```
┌── customer's machine (you, via plurai-sdk) ─────────────────────────┐
│  EXTRACT → ANONYMIZE (if posture: anonymized) → UPLOAD, all local.  │
│  1. Detect the RAG/SQL tool + its data source.                      │
│  2. Gather source connection config (creds stay LOCAL).             │
│  3. Shell: plurai ingest-corpus --source-kind <source_kind> …       │
│       --label <knowledge base id>                                   │
│       extract → ANONYMIZE (if anonymized, fail-closed) → upload     │
│       → prints ONE part entry to stdout                             │
│       { kind: corpus_source, label, sha256, bytes, posture, … }     │
│  4. MCP: ground_workspace_start(workspace_id, previous, added=[…])  │
│       → { job_id, package_version, … } — the grounding job         │
│  Return once the delivery is accepted; never wait for the build.    │
│  Repeat 1–4 per tool.                                               │
└──────────────────────────────────┬──────────────────────────────────┘
                                    │ MCP
┌── Plurai-hosted backend ──────────┴──────────────────────────────────┐
│  The grounding job fetches the part by its hash, adds it to the      │
│  package built on `previous`, and builds the knowledge graph as it   │
│  settles, using the BACKEND's own LLM credentials (the customer      │
│  never sets any).                                                    │
│  The job detail lists each build in `corpus_builds` (corpus_id,      │
│  status, node and edge counts). The backend keeps the graph and      │
│  test set until the corpus is deleted.                               │
└──────────────────────────────────────────────────────────────────────┘
┌── later, in the suite (Step 1's corpus wait) ────────────────────────┐
│  wait for the grounding job → apply the corpus health gate to each   │
│  build → write `.knowledge_bases` / `.corpus_id` for the healthy.    │
└──────────────────────────────────────────────────────────────────────┘
```

**Why the extraction runs locally.** Only the customer's machine can
reach their directories / SQLite-BigQuery-Snowflake DBs / cloud RAG
indexes. Extraction is deterministic I/O — no LLM keys. The heavy,
LLM-driven graph-building + synthesis runs on the backend, using the
BACKEND's own LLM provider and credentials.

**LLM credentials for the corpus build are a BACKEND concern —
NEVER prompt the customer for an LLM key.** Do NOT ask the user to "add
an OpenAI/Anthropic/Google/Azure key": the backend's graph builder uses the
backend's own provider configuration, not the customer's. If a build
fails because the backend has no provider configured (a "no LLM provider
/ no API key" error on the build's entry in the grounding job's `corpus_builds`), that is
unfixable from here — no repair step, so run the contact hand-off (`stop_message.py contact --done`). Never turn it into a
customer key prompt, and never assume
"Azure" from seeing the backend config.

**Why it's a separate process from the mock env.** Building the corpus
and building the simulation environment are distinct jobs with distinct
lifecycles. This skill ONLY delivers the corpus for the backend to build and
persist. Recording it, and wiring it into mock-serving / scenarios / evals, is
handled by the suite and the generator skills.

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
   via the plugin manifest; the backend is the configured one, the one the saved Plurai login names; a remote backend
   requires that login).
2. **The backend can run a corpus build.** On the Plurai-hosted backend that's the default. If the
   backend has no corpus storage configured, the build fails and its entry in the grounding job's
   `corpus_builds` says so. Delivering still succeeds, so the failure surfaces at the suite's Step 0b,
   not here.
3. **`/simulation-scan-agent` has run** — `plurai_sim/plurai.config.yaml`
   exists so you can see which tools read from a RAG index / DB.
4. **`plurai` SDK + its extractor are installed locally.** Install the
   extractor into the agent's venv without asking, by
   `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install --extra extract --record "$ART"`;
   its last line is the outcome (`extractor: installed` or `extractor: NOT installed — <error>`).
   A database or index source adds its driver extra from the source
   table: `--extra extract --extra extract-bigquery`. A `directory` source needs
   no driver extra: the SDK reads PDFs itself, so never install `extract-pdf` for
   one, even when an error hint names it. Never install `plurai-extract` by its
   own name: only the SDK extra pins it. `plurai-extract` is the IP-free extraction
   half of the corpus pipeline (source connectors only — graph building and
   synthesis stay on the backend). The extractor raises a clean
   dependency error naming the missing extra. A missing package is never a
   question and never a wait: when the install fails, say so once with its
   error, run no retry loop, and build no corpus this run — the suite's
   grounding gate reads it as a build that failed (knowledge UNCOVERED), never
   as the user declining.
5. **The customer can supply the source connection details** (a docs
   directory path, a SQLite file/dir, or cloud DB / index credentials).
**Anonymization posture — ask ONCE, here, if `plurai.config.yaml` has no `anonymize:` key.**
Setup deliberately does not ask (it uploads nothing); this skill is the first point where
real customer data leaves the machine, so this is where the question is meaningful. If the
key is ABSENT, ask before uploading, then write the answer into `plurai.config.yaml` as a
top-level `anonymize: true | false` so it is never asked again for this env — only an answer the
run driver gave: a default is not a declaration, so do not write it. (Inside a
driving command's flow window this is a sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction anonymization-posture`,
then ask in that call alone, with no lead-in text: under `/simulate` the flow answers it from the start question.) If the key is
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
   in `plurai.config.yaml` — the default is raw): **`plurai-simulation-sdk[anonymize]` + a spaCy
   English model are installed** — the `plurai ingest-corpus` command (Step 4)
   anonymizes fail-closed under that posture and refuses to run without them:
   install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints), then `python -m spacy download en_core_web_lg`
   (any of `en_core_web_{lg,md,sm}` works). Under the raw (default) posture this
   precondition doesn't apply — `plurai ingest-corpus` uploads as-is, no anonymizer
   required. The `plurai` console script ships with the SDK — install it in the
   **client's venv** (the one the agent uses); Step 4 resolves the exact binary via
   the plugin's `${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py` (activated venv `$VIRTUAL_ENV` → repo
   `.venv` → PATH), so a stale global install can't silently shadow the client's
   pinned SDK.
7. **No separate storage credential** — the SDK CLI publishes the ingested objects
   (anonymized or raw, per the env's posture) to the backend's object-storage service
   over the same diagnostic connection as the MCP tools above
   (the saved login); the endpoint is derived,
   not passed in. Zero new credential plumbing.

## The flow

### Step 1: Detect the plurai_sim directory + RAG/SQL tool

Find `plurai_sim/` (search `plurai_sim`, `./plurai_sim`,
`$(pwd)/plurai_sim`; ask if not found). Read
`plurai_sim/plurai.config.yaml` and look for tools whose descriptor
signals retrieval or SQL:

- `reads_from` mentioning a vector index, search service, or database;
- tool names / descriptions with `rag`, `search`, `retrieve`,
  `vector`, `sql`, `query`, `lookup`, `knowledge`;
- `read_strategy: real` against an index/DB (these are exactly the
  tools a corpus makes faithful).

A `mode: mock` search the map's `grounding.surfaces` leaves out is not
one: it answers from each scenario's seeded rows, so it gets no base.

Tell the user which tool(s) you found and which data source backs
each.

**Prompt-injected knowledge (no tool) is a first-class case, not a
stop.** An agent with `tools: {}` whose knowledge is inlined into its
system prompt (a closed doc set, an FAQ, product guides pasted at
construction time) still has a knowledge base worth grounding: the
corpus becomes the ground truth its planted questions and answer
grading run against, even though nothing retrieves at run time. Detect
it from the PRD/config (knowledge docs named as prompt-injected, a
`knowledge_extraction_policy`, a closed doc list) — then build the
corpus with the KNOWLEDGE-BASE's domain as the label and NO tool name.
Do not invent one: the backend derives its own flow ids and discards
the manifest's `tool` field, so a fabricated tool name is dead metadata
at best and misleads readers of the manifest at worst.

Only when there is neither a RAG/SQL tool NOR prompt-injected knowledge
— a pure transactional agent — say so and stop: a corpus won't help.

**If you find more than one RAG/SQL tool, each is a separate knowledge
base.** List them and confirm with the user which to build (inside a driving command's flow window this is a sanctioned ask — first run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction which-kb`; default:
all of them). Then run Steps 2–4 **once per tool**, giving each its own
`task_description` (the tool's domain — this becomes the base's label)
and delivering one corpus part per tool (Step 4). The suite records the
`{kb_id, tool, label, corpus_ids}` entries in the `.knowledge_bases` manifest
(Step 6). Do NOT merge
distinct tools into one corpus — separate bases are what let scenario
generation exercise each question-kind as its own flow.

**Each base holds only what its tool can return.** `.scan_corpus_roots` (Step 3) is the union. When more than
one RAG/SQL tool reads it, or a line of it is something no tool's code can return, read the agent's code and
write `$ART/corpus_roots.json` before Step 2:

```json
{"parts": {"<kb_id>": {"roots": ["<a .scan_corpus_roots line, or a path inside one>"], "declared_units": 16, "reach": {"<each root>": "<file:line where this tool's query admits it>"}}},
 "out_of_reach": {"<a whole .scan_corpus_roots line>": "<file:line showing no tool's query can return it>"}}
```

Every file of the union falls in a part or out of reach; a non-directory tool's part lists `"roots": []` and one
`reach` string, as may a part with one root. Cite each root where the query admits THAT root's content: a query
that filters (by id, version, type) is cited where the filter lets the root through, not where it reads the table;
a root with no such line belongs in `out_of_reach`. The reconcile refuses an uncited root. Out
of reach is a fact about the agent's code, never an answer to a reconcile finding, and the customer is told
what it names (Step 7). A part you stop building (its extract stays under-chunked at every size, or its build failed
or came out degenerate and is not delivered again) keeps its entry and gains `"not_built": "<one plain sentence, in the
customer's terms: no sizes, codes or errors>"`; like out of reach, it is never an answer to a reconcile finding. Every
part in the file without `not_built` is built and reconciled, and a change to the file re-runs every
part's reconcile; a tool the user declines keeps its roots out of the file, so they hold until the scope is
narrowed at its owner (Step 3). With no file, every line is the one base's.

### Step 2: Choose the extraction source + gather connection config

Map the data source to a `source_kind` (see the registry in
`plurai.rag_extract.SOURCE_REGISTRY`):

| source_kind | backs | typical config | driver extra |
|---|---|---|---|
| `directory` | local docs: every format the SDK reads (`plurai.readers.FORMATS`, each also gzipped, and `.zip` archives); the ingest names any file it skipped | `{"path": "./docs"}` (+ optional `{"chunk_size": 1024, "chunk_overlap": 50, "chunk_mode": "section"}`) | — |
| `sqlite` | local SQLite DB(s) | `{"db_dir": "/data"}` or `{"db_paths": [...]}` | — |
| `json_records` | a JSON / JSONL array of records (products, tickets, accounts) | `{"path": "./catalog.json"}` (+ optional `{"records_path": "data.products", "title_field": "name", "id_field": "sku", "field_map": {"sku": "SKU", "price": "Price"}}`) | — |
| `bigquery` | BigQuery | project/dataset + GCP creds | `extract-bigquery` |
| `snowflake` | Snowflake | account/user/warehouse creds | `extract-snowflake` |
| `redshift` / `azure_sql` / `databricks` | cloud SQL | DB-specific creds | `extract-redshift` / `extract-azure-sql` / `extract-databricks` |
| `azure_search` / `vertex_search` / `bedrock_kb` | cloud RAG index | endpoint + api key / project | `extract-http` / `extract-vertex` / `extract-bedrock` |
| `langchain_vectorstore` / `llamaindex_vectorstore` | an existing LangChain/LlamaIndex vector store or index object | the instantiated store/index | — |

**Credentials stay local.** Collect them only to instantiate the
source for extraction. They are NEVER written to `rag_corpus/` and
NEVER uploaded — only the resulting chunks + relationships are.

**A doc folder is chunked BY SECTION and ships structural edges.** For
`directory`, markdown is split at its headings — one chunk per `#`/`##`/`###`
section (a heading with no body of its own merges into its neighbour; a section
too big for `chunk_size` falls back to token windows) — and the extractor emits
structural relationships between those chunks: `next_section` (reading order
within a doc), `parent_section` (a subsection → the chunk holding its parent
heading), and `links_to` (a markdown link resolving to another ingested file).
This is not cosmetic. The backend walks the graph to plan knowledge questions,
so a corpus of one node per file with zero edges is
structurally unwalkable: it yields no grounded samples and every knowledge
question dies at the relevance floor. Section chunks + edges are what make a doc
folder produce real RAG scenarios. Non-markdown text (`.txt`) and PDFs still use
the token window, and still get `next_section` edges between consecutive chunks.

Also gather **domain hints** and the customer's own **questions** about the corpus (a list of question
strings, usually none). Both are written to ONE file, `$ART/corpus_domain.json`, which
`ingest-corpus --domain-file` embeds in the corpus part, so the build reads exactly what you computed here.
`task_description` is also the base's label (`--kb-label`, Step 4):

```json
{"domain": {"task_description": "...", "semantic_enrichment": false, "domain_keywords": ["..."],
            "entity_type": "product", "instruction_chunks": [{"source": "metadata", "key_path": "kind",
            "regex": "^(persona|rule|script)$", "reason": "flow-file instruction records"}]},
 "questions": []}
```

The `domain` mapping holds the keys below; leave a key out when it does not apply, and write
`"questions": []` when the customer gave none. **The file travels unscrubbed, even under anonymized
posture** (it is derived from the agent's descriptions, which also travel as written), so it holds
descriptions of the domain and never customer record text: no names, ids or sample values in
`task_description`, `domain_keywords` or the questions.
- `task_description` — what the agent does with this data (one line);
- `semantic_enrichment` — set `true` for SQL sources (generates table
  descriptions); `false` for document RAG.
- `domain_keywords` — a list of the domain's own terms (product names, regulated
  entities, the vocabulary a user of THIS agent would type). The backend passes
  them straight into the graph build, where they steer concept extraction
  and the graph overview. Omit it when the corpus's vocabulary is ordinary
  English; give it when the source is full of terms a general model would not
  weight (an internal product taxonomy, a Hebrew/Arabic term set, ticker symbols).
- `entity_type` — the agent's entity-schema type these records ARE (e.g.
  `product`), when the corpus is one record per chunk (`json_records`, or a
  markdown source whose per-chunk metadata carries the record's fields). This is
  what lets a `ground_from: {entity_type: ...}` precondition resolve to real
  corpus rows instead of an LLM-invented value. **Omit it for a prose/document
  corpus**: the backend types only chunks that carry record fields as metadata,
  so declaring it there types nothing.
- `instruction_chunks` — **the agent's INSTRUCTIONS inside the source, declared typed.** A
  corpus extracted from the agent's data carries its persona, conduct rules and scripted lines
  beside its facts (a flow file's `kind: persona | rule | script` records; the system-prompt
  file sitting next to the docs). The backend types every chunk `knowledge` or `instruction`
  by SELECTORS run as code over the chunk (`domain.instruction_chunks`), and a chunk no selector
  matches is knowledge — so a rule left undeclared becomes a knowledge question ("what is your
  refund-window rule?") that is a false red by construction. You scanned the agent and know
  its prompt sources: declare them as a list of `{source, key_path?, regex, reason}` —
  `source: metadata` + `key_path` (a dot path into the chunk metadata, e.g. `kind`, with an
  optional `regex` over the value), `source: name` + `regex` over the source file name, or
  `source: page_content` + `regex` over the text. **Validate the document before the ingest**
  — a selector the backend does not accept, or one that matches no chunk, leaves its
  instructions typed as knowledge, and the plugin does not rely on the build to say so.
  The script below checks the selector's SHAPE; once Step 4's extract-only run has printed its
  `per_document` map, it also checks that each `source: name` selector matches at least one
  extracted document name:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/build-rag-corpus/scripts/verify_domain.py" "$ART/corpus_domain.json" || exit 1
  ```

  **`|| exit 1` is the gate.** Without it the sentence above was the whole enforcement: the
  script exited 1 and the step read on, so the ingest ran over the document anyway. Exit 1 =
  FINDINGS, shapes the backend refuses — fix those. Lines marked `· note (the backend accepts
  this)` are NOT findings and do not move the exit code: they are readable shapes the backend takes
  as they are. **Never edit a selector to silence a note** — weakening a working selector ships
  its instruction chunks typed as knowledge, which is the failure this whole step exists to
  prevent.

  **Re-run the check against the extracted names.** Save the stdout of Step 4's extract-only run (it carries the
  `per_document` map: document name to chunk count) to a file and pass it:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/skills/build-rag-corpus/scripts/verify_domain.py" "$ART/corpus_domain.json" --extract-shape "$ART/extract_shape.json" || exit 1
  ```

  Exit 1 here names a `source: name` selector that matches none of the extracted documents: fix
  its regex against the names in the message (a selector like `(^|/)prompts/` matches nothing when
  the instructions live in `assets.yaml`). An SDK that prints no `per_document` gets a note and
  no match check.

  Omit the key for a corpus that holds no instructions (every chunk is then knowledge, which is
  the safe direction: an instruction mistaken for knowledge costs a false red, a knowledge chunk
  mistaken for an instruction silently removes coverage).

### The stale-handle test

<!-- BEGIN shared:stale-handle-test copy: generated from the simulation-generate-suite skill; edit there -->
**STALE-HANDLE TEST — a recorded corpus id must RESOLVE on this backend before anything reads
it.** List the recorded ids
(`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/corpus_coordinate.py"`, plus the env manifest's when
this repo uses named envs) and call `job_status(id=<id>)` on each: an id
resolves iff `kind == "corpus"` and `status == "done"`. Branch on those two typed fields only —
a deleted id and a failed build are both `status: failed`, so never on `status` alone, the
`error` text or an empty `detail`. An id that does not resolve is a stale handle, not a
recording: keep it as a `.corpus_scope` gloss line and clear it from the live markers and, under
named envs, from the env manifest's copy through its own writer — that copy wins the resolution
order. Never mark it `knowledge_planning: "off"`: the `ground_from` branch passes `off` bases.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $WORKDIR — re-source in THIS block
PLURAI_PY_HINT="${WORKDIR%%/plurai_sim*}/.venv/bin"   # the agent's interpreter, from any cwd inside it
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" - "$WORKDIR" "${PLURAI_URL:-}" "${CLAUDE_PLUGIN_ROOT}" '<stale id>' <<'PY'
import json, sys, time
from pathlib import Path
from urllib.parse import urlsplit
workdir, url, plugin, stale = Path(sys.argv[1]), sys.argv[2], sys.argv[3], set(sys.argv[4:])
def live_bases(bases):
    kept = []
    for base in bases:
        ids = base.get('corpus_ids') or []
        live = [i for i in ids if i not in stale]
        if ids and not live:
            continue  # every corpus this base named is stale: the entry goes, not 'off'
        if live != ids:
            base['corpus_ids'] = live
        kept.append(base)
    return kept
kb = workdir / '.knowledge_bases'
if kb.is_file():
    kept = live_bases(json.loads(kb.read_text(encoding='utf-8')))
    kb.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding='utf-8')
for name in ('.corpus_ids', '.corpus_id'):
    path = workdir / name
    if path.is_file():
        live = [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip() and line.strip() not in stale]
        if live:
            path.write_text('\n'.join(live), encoding='utf-8')
        else:
            path.unlink()
host, day = urlsplit(url).hostname or 'an unknown backend', time.strftime('%Y-%m-%d', time.gmtime())
with open(workdir / '.corpus_scope', 'a', encoding='utf-8') as scope:
    for corpus_id in sorted(stale):
        scope.write(f'stale: {corpus_id} ({day}) — does not resolve on {host}\n')
if (workdir / 'plurai.env.yaml').is_file():  # named envs: filter the manifest's OWN ids, keep its live ones
    sys.path.insert(0, f'{plugin}/skills/manage-envs/scripts')
    import env_manifest
    m = env_manifest.read_manifest(workdir)
    env_manifest.write_manifest(
        workdir, name=m.get('name') or workdir.name,
        corpus_id='' if m.get('corpus_id') in stale else None,
        corpus_ids=[i for i in m['corpus_ids'] if i not in stale] if m.get('corpus_ids') else None,
        knowledge_bases=live_bases(m['knowledge_bases']) if m.get('knowledge_bases') else None)
PY
```
<!-- END shared:stale-handle-test -->

### Step 3: Decide scope, check doc shape, stage the source config

**FIRST — does this agent already have a corpus?** If `$WORKDIR/.knowledge_bases` or
`$WORKDIR/.corpus_id` exists **and resolves** (the STALE-HANDLE TEST in the section just above — run it, block
and all, first: an id that does not resolve is cleared, never offered for reuse), STOP before any
scope question or ingest: delivering different bytes always starts a NEW corpus, so re-running this flow
silently orphans the one the scenarios and evals are already grounded against. Sanction it (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction corpus-reuse`; default: reuse) and ask whether to reuse the existing corpus (naming
its id and, if `.corpus_scope` is present, what it covers) or rebuild it. Reuse costs nothing: the backend keeps
a build while the corpus bytes are the same, whatever the PRD, the variables or the config say. Rebuild only
when the user asks for it or the agent tools' output fields changed substantially. A rebuild removes the part and
delivers it again, whether its bytes changed or not. The removal erases the old build, which destroys ground truth, so
it is its own confirmation (`delete-confirm`). On "rebuild", first run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" forget --workdir "$WORKDIR" --label "<kb_id>"`, which
prints `REMOVED=<sha256>`. Then call `ground_workspace_start(workspace_id=…, previous=<the recorded package version>,
removed=[<that sha256>])` with no `added`, record the result as in Step 4 item 3, and carry on below. The delivery in
Step 4 builds the corpus afresh.

**The scope is `$WORKDIR/.scan_corpus_roots`** — the roots the scan contract hashed, one absolute
path per line, each a directory or one file, the union of the bases. Step 4 passes ONE ingest call the base's
own roots from `$ART/corpus_roots.json` (Step 1), or EVERY line when that file is absent; never re-decide it here.
With no `.scan_corpus_roots` the contract has not run: say so in one line and choose the source below.

**Then, SIZE the source — the scope question exists for LARGE sources, not for every
source.** With `.scan_corpus_roots` present the scope is already decided: size it only to say
it in one line, and never sanction or ask `extraction-scope` over it (unasked, its `skip` would
void the build the grounding gate just chose). With no recorded root, a quick local look (file
count / byte total for a docs dir; table count for a DB) decides:

- **Small source — under ~20 documents AND under ~300 KB total** (the live case: 4 files,
  107 lines): do NOT ask about scope. Default to the ENTIRE source, say so in one line
  ("small source — ingesting all 4 files"), and move on — an entire-vs-subset interrogation
  over four files is pure noise. Small does NOT mean unexamined: still run the doc-shape
  check below, because a thin source is exactly where an under-chunked corpus is fatal.
- **Anything larger: ASK the user about extraction scope — do not silently ingest the whole
  source** (inside a driving command's flow window, sanction it first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction extraction-scope`)**.** `plurai ingest-corpus` (Step 4) extracts ALL documents from `source_config` with no cap, and the whole corpus is fed to the backend's KG build, so a large source (a full docs site, an entire SQL warehouse) means a big, slow, costly build. Before ingesting, ask:

> Do you want to build the corpus from the **entire** data source, or a **focused subset**? For a good test corpus you usually don't need everything — a few representative topics / sections (e.g. one product area, one docs subtree, a single dataset/table) keeps the build fast and cheap and still exercises the retrieval flow. **Recommended: start with a subset.** Which would you like?

**`recommended-subset`** — the answer a live `/simulate` run's guard hands this call instead of asking:
take the recommendation yourself. A record source: one category/area, taken whole (the rule below). A
docs source: the subtree(s) most relevant to the agent's tools and PRD. Record it to
`$WORKDIR/.corpus_scope` as below, say it in one line ("large source — building from <area>; tell me
to widen it"), and continue.

**For a RECORD source (`json_records`, a SQL table), scope by AREA — never by sampling N records.**
The corpus is not only the retrieval ground truth: every downstream scenario draws its products,
its world-state seeds and its pinned `agent_context` from the corpus pool, so a record subset
becomes the entire universe the suite can test: a small slice caps the suite to the few records it
holds and turns every planted question into a lookup on them.
Narrow by CATEGORY/area at the source (`{"path": "./docs/billing"}`, `WHERE category = …`) and take
that area whole; under ~2 000 records, take it whole without asking.

**Record the resolved scope to `$WORKDIR/.corpus_scope` — for EVERY source kind, not just record
sources.** (This paragraph is about record scoping, but the write below is not: a `directory`
corpus that skips it lands on the "no `declared_units` recorded" branch at Step 4 and the
shortfall check can never fire for it — which is the source kind most likely to lose files to a
glob that missed a subdirectory.) Two lines, a declared count and the prose:

```
declared_units: <count>
scope: <what the corpus covers, in one line>
```

`declared_units` is what you EXPECT to be ingested (documents for a `directory` source, records
for a record source). It exists so Step 4 can compare the declaration against what the ingest
actually produced. A scope line that is prose only cannot be checked against anything, and a
corpus that lands short is still registered as the customer's COMPLETE knowledge base — a claim
that licenses judges to say "the sources do not mention X" about sources that were simply never
ingested. With `$ART/corpus_roots.json`, each part
records its own `declared_units` there and `.corpus_scope` keeps the union's. Omit `declared_units` only when the
source genuinely has no countable unit up front (a cloud retriever you cannot enumerate); say so
on the `scope:` line instead of guessing a number.

Append the two lines; keep every line already in the file. `snapshot_sha256:` lines there mean the
source is an org snapshot the scan exported, and `declared_units` is its file count. Prove the
snapshot is still the export before ingesting it:
`python3 "$WORKDIR/export_agentforce_knowledge.py" --verify --project-dir "$WORKDIR"`. Exit 1 is a
STOP: re-run the export, never edit the snapshot.
`inline_sha256:` lines mean the source is knowledge the scan copied out of the agent's code
(`plurai_sim/knowledge_inline`); when it is the only source the copy already wrote `declared_units`, otherwise record
the union's count as above. Copy it again from the current code before
ingesting: `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/extract_inline_knowledge.py" --workdir "$WORKDIR" --when suite`
(after sourcing `resolve-python.sh`). `KNOWLEDGE_INLINE=written` means the code changed since the scan: say one plain line,
"your agent's knowledge changed since the scan; the test knowledge base is rebuilt from the current code — re-run
/simulation-scan-agent to refresh the rest of the setup", and ingest what it wrote; the run's corpus-drift check will then
report drift against the scan, which is expected and is what that line answers. A `warning:` line is said once, as is;
a `fix:` line is for you, never the user. Neither stops the build. Never edit the copy.

If they choose a subset, narrow it at the ONE owner: edit `grounding.sources` in
`agent_topology.json`, re-run `/simulation-scan-agent` Step 3c, and ingest what it records — never a
prose-only exclusion in `.corpus_scope`. A record source narrows by dataset / table (e.g. `WHERE category = …`) or a source-specific filter/limit via the source driver. Confirm the resolved scope with them before ingesting. If they want everything, proceed — but note the backend caps the build's size, so a very large source is only partly covered.

**Then CHECK DOC SHAPE (`directory` sources, every size).** Section chunking only
produces a walkable graph if the docs have sections to split at. One cheap look:

```bash
grep -c '^#\{1,6\} ' <docs>/*.md    # heading count per markdown file
```

- **Several headings per file** — the normal case. Proceed; you should get roughly
  one chunk per heading section.
- **Structureless docs** (0-1 headings per file, or the folder is `.txt`/PDF only):
  the section split has nothing to work with and every file lands as one chunk. Pass a
  smaller `--chunk-size` in Step 4 (e.g. `256`) so long prose still splits into several
  chunks per file. Say so in one line; don't interrogate.

While judging the doc shape, also glance at what the agent's record-returning tools can
RETURN (`descriptor.returns` in `plurai.config.yaml`): if the source is prose-heavy
(FAQ / policy / guides) and no tool carries a free-text field, say the scope note now —
full criteria in `/simulation-generate-suite` Step 0's corpus-answerability check
(attribute-heavy sources match scalar fields fine; this is a pointer, not a gate).

Whatever you do here, verify the OUTCOME against the shape check in Step 4 (item 1b) and the health
gate the suite applies to the built graph (Step 6) — those checks are the gate, not this estimate.

**Stage the source config for the CLI.** The SDK CLI reads `source_config`
from a JSON file (`--source-config`). It holds the connection details (and, for
cloud sources, credentials) used ONLY to instantiate the source locally — it is
**NEVER uploaded**. Write it to a local, restrictive-mode temp file and delete it
right after the CLI returns:

```bash
CFG="$(mktemp)"; chmod 600 "$CFG"; trap 'rm -f "$CFG"' EXIT
printf %s '{"path": "./docs/billing"}' > "$CFG"   # the RESOLVED source_config as JSON
```

(A `directory` / `sqlite` source is just a path; a cloud source carries creds —
hence the 0600 temp file + cleanup. Never write it under `plurai_sim/` or commit it.)

### Step 4: Ingest (extract → anonymize per posture → upload), then deliver the corpus part

The corpus joins the workspace's grounding package as one more part: the agent's descriptions the scan
delivered stay in it, and this step adds the extracted corpus. The backend builds the knowledge graph from the
part while the grounding job settles. **This skill returns once the delivery is accepted and never waits for
the build.** Nothing here sends the backend a model name, a cost knob or a source kind: the build reads the PRD and the
scenario variables from the delivered descriptions, takes the domain hints and questions from inside the
corpus part (`$ART/corpus_domain.json`, Step 2), and sizes itself.

**Run the items in this order: 1b, 1c, 1, 1d, 2, 3** (with `$ART/corpus_roots.json`: every part's 1b, then 1c
once, then each part's 1, 1d, 2, 3; the numbers are labels: the shape check and the
PRD reconcile are local and come before the upload, item 1). The delivery is ONE call carrying the
re-uploaded descriptions and the corpus part, so the build reads the reconciled PRD.

**1. Run the SDK ingest command.** ONE invocation extracts the corpus locally,
then resolves this env's anonymization posture from `plurai.config.yaml`
(the `anonymize:` key, default raw): under **anonymized** posture it
**anonymizes the output (fail-closed)** exactly as before; under the **raw**
(default) posture it uploads the output as-is and prints a loud
`RAW (anonymization off)` banner. Either way it bundles the result into a
single compressed archive, uploads that ONE archive to the backend's storage over
the same diagnostic connection as the MCP tools, and prints ONE **part entry** to stdout.
`<kb_id>` is the knowledge base's id: the tool name, or a domain slug for prompt-injected knowledge
(letters, digits, `.`, `_` and `-`). It is the part's `--label`:

```bash
# Resolve WHICH plurai runs: the Bash tool does not activate the client's venv, so a bare
# `plurai` misses or silently hits a stale global install. The resolver picks the client's
# activated venv ($VIRTUAL_ENV) → the repo .venv → PATH, or fails with guidance.
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-corpus)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
# Preflight: bash cannot see which backend the MCP server is bound to. Call the read-only
# backend_identity() MCP tool first and paste its object here verbatim — that comparison is the
# one thing neither the CLI nor this shell can make alone. The pin is NOT re-checked here; the
# CLI owns it (below). Omitting the argument only downgrades this to a warning.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
# --workspace-root is REQUIRED here like every other ingest call site: its default
# is `.`, and the Bash tool's cwd is the session directory, not the agent root — an omitted
# flag silently unarms the target pin AND, on success, pins a phantom plurai_sim/ under
# whatever directory the shell happened to be in. The same goes for a relative --out-dir.
# The scope: a `directory` source takes its base's roots ($ART/corpus_roots.json, Step 1), else EVERY
# `.scan_corpus_roots` line, as the `--root`s of this ONE call (each call rewrites chunks.json); any other kind
# takes its staged "$CFG". A `directory`
# source also takes the SUFFIXES the scan read those roots with — its recorded scope in
# scan_contract.json, else the `.scan_corpus_suffixes` it was run with — so the corpus holds the
# files the scan read (`--suffixes` alone is the scan's default set). The ingest prints one
# WARNING per suffix it left out; quote them to the user.
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
mkdir -p "$ART"
# The domain hints and questions travel inside the part. An SDK without that capability would upload the part with
# none of them, and the build would run without its instruction selectors and vocabulary.
"$PLURAI_BIN" capabilities | grep -q corpus-domain-member || { echo "build-rag-corpus: this plurai cannot carry the corpus domain inside the part — upgrade the SDK in the client's venv (the install line is in Preconditions 6), then re-run" >&2; exit 1; }
SCOPE=(--source-config "$CFG")
if [ "<source_kind>" = directory ]; then
  "$PLURAI_BIN" ingest-corpus --help | grep -q -- '--scan-contract' || { echo "build-rag-corpus: this plurai's ingest-corpus takes no scan scope — upgrade the SDK in the client's venv, then re-run" >&2; exit 1; }
  if [ -s "$ART/corpus_roots.json" ]; then
    _roots="$(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["parts"][sys.argv[2]]["roots"]))' "$ART/corpus_roots.json" "<kb_id>" 2>/dev/null)" \
      || { echo "build-rag-corpus: <kb_id> is not a part of $ART/corpus_roots.json — add it (Step 1), then re-run" >&2; exit 1; }
    SCOPE=(); while IFS= read -r _root; do [ -n "$_root" ] && SCOPE+=(--root "$_root"); done <<< "$_roots"
  elif [ -s "$WORKDIR/.scan_corpus_roots" ]; then
    SCOPE=(); while IFS= read -r _root; do [ -n "$_root" ] && SCOPE+=(--root "$_root"); done < "$WORKDIR/.scan_corpus_roots"
  fi
  if [ -s "$WORKDIR/artifacts/scan_contract.json" ]; then SCOPE+=(--scan-contract "$WORKDIR/artifacts/scan_contract.json")
  else SCOPE+=(--suffixes); [ -s "$WORKDIR/.scan_corpus_suffixes" ] && while IFS= read -r _sfx; do [ -n "$_sfx" ] && SCOPE+=("$_sfx"); done < "$WORKDIR/.scan_corpus_suffixes"
  fi
fi
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" --refusal-stderr "$PLURAI_BIN" ingest-corpus \
    --workspace-root "$PLURAI_DIR" \
    --source-kind "<source_kind>" \
    "${SCOPE[@]}" \
    --label "<kb_id>" \
    --domain-file "$ART/corpus_domain.json" \
    --out-dir "$PLURAI_DIR/rag_corpus" > "$ART/corpus_part_entry.json" || { [ $? -eq 7 ] && exit 7; }   # 7 = a version refusal: its text is on stderr
# --out-dir: $PLURAI_DIR already IS <agent>/plurai_sim (resolve-workdir.sh), so the
# extractor output lands at plurai_sim/rag_corpus — the exact path the shape check and
# the delivery below read. "$PLURAI_DIR/plurai_sim/rag_corpus" doubles the segment and
# the verification then reads a directory the ingest never wrote.
#   stdout → the part entry JSON, saved to $ART/corpus_part_entry.json
#   stderr → first the resolved TARGET line (`plurai ingest-corpus: uploading to <backend> (target
#            from the configured backend; <pin verdict>)`),
#            then a human-readable disclosure preview (file count, bytes, scrub summary)
#            The target line prints on EVERY invocation, so it is there even when the run is
#            refused. READ IT: it is the authoritative statement of which backend this upload
#            reached. Nothing downstream re-checks it,
#            so `assert-backend-identity.sh` before the upload is the guard.
#   uses the configured backend and token (or the saved login) —
#   the same the backend connection the MCP tools above use; no storage flags needed.
#
# Optional chunking knobs (omit them for the defaults — section chunking + structural edges):
#   --chunk-size 256        smaller sections; use it for structureless prose (Step 3)
#   --chunk-overlap 50      token overlap when an oversize section falls back to windows
#   --chunk-mode tokens     ESCAPE HATCH: the old flat window, no section split. Only for a
#                           source where section chunking demonstrably misbehaves — it is
#                           what produced the one-chunk-per-file, zero-edge corpora.
#   --domain-file FILE      (above) embeds {"domain": {...}, "questions": [...]} in the part: the
#                           hints the build steers by (no credentials). Step 2 wrote and
#                           validated it with verify_domain.py. (`--domain` takes inline hints for
#                           the local manifest only; this skill does not use it.)
```

A `WARNING (disclosure: home_named)` line from `ingest-corpus` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

- **The part entry is one JSON object**, `{kind: "corpus_source", label, sha256, bytes, posture,
  anon_key_fingerprint}` (the fingerprint only under anonymized posture). The SDK streams every
  unit (scrubbed under anonymized posture, as-is under raw) into ONE deterministic `.tar.gz` and
  uploads it as ONE storage object; `sha256` and `bytes` describe that archive, so the same corpus
  exports to the same entry. Under anonymized posture the archive carries a scrub map the backend
  reads. The SDK writes the disclosure preview (to stderr) + `.plurai/uploads/audit.log` itself;
  relay the preview counts, never chunk bodies. An SDK older than 0.28.0 prints a lease manifest
  instead of a part entry; the resolver's floor refuses it before this step runs.
- **Fail-closed — a non-zero exit is a HARD STOP.** Under **anonymized** posture
  this can mean anonymization could not run (Presidio / spaCy missing); under
  **either** posture it can also mean the diagnostic connection is missing
  (Plurai is not logged in in this shell) — **NOTHING was
  uploaded**. Read the error yourself, fix the local environment silently
  (install the missing packages; for a missing login, run the
  `harness-setup` skill — see Failure modes), and re-run; do not show the raw error to the user.

**1b. Check the extracted SHAPE — BEFORE the upload, not after.** Run step 1's command
with `--extract-only` first, its stdout going to `$ART/extract_shape.json` instead of the entry file: it extracts into `--out-dir` and stops before any egress (no
scrub, no archive, no lease, no upload, no audit entry), printing the local health summary
to stdout in the manifest's place:

```json
{"chunks": 61, "relationships": 88, "source_documents": 12, "relationships_per_chunk": 1.44, "source_kind": "directory"}
```

Current SDKs also print a `per_document` map (document name to chunk count); keep this stdout in a
file, since `verify_domain.py --extract-shape` reads it (Step 2's `instruction_chunks`).

Current SDKs add `source_kind` and print a SECOND stderr line reading the shape for that kind
(`plurai ingest-corpus: shape: …`) — read it when present; an older SDK prints four keys and one
line, and the rule below is the same either way.

**The under-chunked verdict is a DIRECTORY verdict.** For a `directory` source,
**`chunks == source_documents`** (one chunk per file) or **`relationships_per_chunk < 1`**
(fewer edges than the chunks that owe one — a non-Markdown file is read as a chain, so its first
chunk owes none) means the extraction is under-chunked — the build would produce an
unwalkable graph and the suite would carry zero grounded knowledge scenarios. Re-run with a
smaller `--chunk-size` (Step 3) until the shape is healthy, THEN run the same command WITHOUT
`--extract-only` to publish. A Markdown file with no headings stays one chain at any size: it
needs section headings.

For **`json_records`** those same conditions are the HEALTHY shape: one record per chunk,
chained, out of ONE source file — so `relationships ≈ chunks - 1` and `source_documents == 1`
by construction. Only **zero** relationships over more than one chunk is a failure there. For
**SQL and cloud-retriever kinds** the chunking is the source's own: read the numbers, expect no
verdict. Report counts only; never paste chunk bodies.

**RECONCILE the ingest against `.corpus_scope` and the contract.** One script reads both and
reports each gap as a gate finding (a `finding:` line):

- `plugin.corpus.shortfall` — the ingest landed materially fewer units than `declared_units` in
  `$WORKDIR/.corpus_scope` (Step 3; the margin is the script's `SHORTFALL_TOLERANCE`):
  `source_documents` for a `directory` source, `chunks` for a record source; a file the ingest
  names in `$PLURAI_DIR/rag_corpus/manifest.json` `not_ingested` as `empty` counts as landed; any
  other reason there (`no text extracted`, `not UTF-8 text`) is content that did not land, so it
  stays in the shortfall.
- `plugin.corpus.source_not_ingested` — a source the contract hashed reached no chunk, or the
  extractor read no text from a file or from some of its pages. Only an `empty` entry of the
  ingest's own `not_ingested` list excuses a source; never write one yourself.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PART=(); [ -s "$ART/corpus_roots.json" ] && PART=(--partition "$ART/corpus_roots.json" --kb-id "<kb_id>" --scan-roots "$WORKDIR/.scan_corpus_roots")
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile_corpus_scope.py" --contract "$WORKDIR/artifacts/scan_contract.json" \
  --chunks "$PLURAI_DIR/rag_corpus/chunks.json" --manifest "$PLURAI_DIR/rag_corpus/manifest.json" \
  --scope "$WORKDIR/.corpus_scope" --art "$ART" "${PART[@]}"
```

With a partition this checks the part: its own files, and every file of the union no part and no out-of-reach
line covers (such a file holds in every part).

- **Exit 0** → continue. A line saying no `declared_units` is recorded is not a passed count:
  never back-fill it from the manifest, which would make the check pass vacuously forever.
- **Exit 1** → a finding holds; do NOT proceed to the build (deliver nothing). This corpus is registered as the
  agent's knowledge base, so a partial one is not a smaller corpus but a WRONG one — every fact
  that never landed becomes a fact the judges hold the agent to having correctly called absent.
  Find the cause (a glob that missed a subdirectory, a filter narrower than intended, files the
  extractor skipped; a remedy on its line is the one to give — the ingest does not OCR), fix it,
  re-extract, and re-run. Never rewrite `declared_units` to match what landed. Never answer a finding by
  narrowing the roots or suffixes in a side file: a source left out of the corpus is a fact the suite does not ground on,
  and a check that reads it can drop out of the final verdict. Narrow only at the owner above, or overrule. Overrule a finding
  (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`) only on evidence that the agent cannot read the
  missing sources either — its own retriever reads the same files and gets no text from them —
  cited by file:line. The decision is yours alone.
- **Exit 2** → an input or the overrule ledger is unreadable: read the error and fix it.

An older SDK rejects the flag (`unrecognized arguments: --extract-only`). Then run step 1 as
it stands and read the same numbers off the artifacts it left at `$PLURAI_DIR/rag_corpus/` —
through `$PLURAI_DIR`, never a relative literal, since under a NAMED ENV it is
`plurai_sim/envs/<name>` and the Bash tool's cwd is the session directory:

```bash
python3 -c "import json,os,pathlib;d=pathlib.Path(os.environ['PLURAI_DIR'])/'rag_corpus';print(len(json.loads((d/'chunks.json').read_text())),'chunks',len(json.loads((d/'relationships.json').read_text())),'relationships')"
```

The check is the same; only its timing slips to after the bytes have already left.

**1c. Reconcile the PRD against the extraction — before any byte is uploaded.** The build reads the PRD
from the descriptions delivered with the corpus, so the PRD is reconciled now, against the local
`$PLURAI_DIR/rag_corpus/chunks.json` (with a partition, every part's: copy each part's `chunks.json` to
`$ART/chunks_<kb_id>.json` before the next extraction rewrites it) and without waiting for any build: follow Step 6.5 (the per-claim pass
with `references/prd-corpus-reconciliation.md`, the sourcing framing, and the scan's own PRD checks), then
carry on. An unchanged PRD changes nothing below.

**1d. Upload the descriptions again, as the scan's Step 4b does.** The reconciled `simulation_prd.md` and the
other descriptions travel in the same call as the corpus part (item 2). Unchanged bytes are the same parts
and add no version. A refused description (`declared_part.py build` exit 3) delivers nothing: say which file
to fix in the product terms of Step 4b item 2's table in `/simulation-scan-agent`, and stop.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/declared_part.py" build --workdir "$WORKDIR"; echo "declared part exit=$?"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-descriptions)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
[ -n "$PLURAI_BIN" ] || { echo "MISSING runner plurai ingest-descriptions"; exit 1; }
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
rm -f "$ART/part_entries.json" "$ART/description_entries.json"
SC_ARGS=()
[ -f "$ART/scan_contract.json" ] && SC_ARGS=(--scan-contract "$ART/scan_contract.json")
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" --refusal-stderr "$PLURAI_BIN" ingest-descriptions --workspace-root "$PLURAI_DIR" --descriptions-dir "$PLURAI_DIR" \
  "${SC_ARGS[@]}" > "$ART/part_entries.json" || { [ $? -eq 7 ] && exit 7; echo "DESCRIPTIONS=UPLOAD_FAILED"; exit 1; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" added --workdir "$WORKDIR" --entries "$ART/part_entries.json" > "$ART/description_entries.json" || { echo "DESCRIPTIONS=UNCHECKED"; exit 1; }
```

A `WARNING (disclosure: home_named)` line is relayed as in item 1. `DESCRIPTIONS=UPLOAD_FAILED` is retried once; an
empty `description_entries.json` (the check failed, its reason on stderr) means run this item once more; a second failure of either: run the contact hand-off (`stop_message.py contact --done`). Pass the SDK no file other than the
descriptions and `$ART/scan_contract.json`.

**2. Read the entry back and deliver it.** The script checks that the entry is a deliverable corpus part,
registers the part (its knowledge base, its label and the shape of the extraction, which the suite's Step 0b
needs when the build ends) and prints the descriptions of item 1d and then the entry as the ONE `added`
argument: the descriptions and the corpus part go in the same call. `<kb_id>` is the same id as
`--label`; the base's label (Step 2's task description) and the tool name are customer text, so each goes
into a file through a quoted heredoc, never inside quotes on the command line. `--tool-file` names the agent
tool that reads the base and is left out, with its file, for prompt-injected knowledge (never invent a tool
name). The previous package version is the one the last delivery recorded, empty on a machine that has none:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
cat > "$ART/kb_label.txt" <<'KB_LABEL'
<the base's label: the task description>
KB_LABEL
cat > "$ART/kb_tool.txt" <<'KB_TOOL'
<the tool name>
KB_TOOL
echo "WORKSPACE_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py")"
echo "PREVIOUS=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" previous --workdir "$WORKDIR")"
echo "ADDED=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" part --workdir "$WORKDIR" --entry "$ART/corpus_part_entry.json" --kb-id "<kb_id>" --kb-label-file "$ART/kb_label.txt" --tool-file "$ART/kb_tool.txt" --rag-dir "$PLURAI_DIR/rag_corpus" --descriptions "$ART/description_entries.json")"
```

An empty `ADDED=` means the check failed and its reason is on stderr (the SDK printed no part entry, or one
the backend would refuse, or the extraction cannot be read, or the descriptions are not the checked ones). Run item 1 or 1d once more; a second failure: run the contact hand-off (`stop_message.py contact --done`). Then call the backend tool, with the three lines as its arguments and no
other argument (no `removed` here, no `overrules`):

```
ground_workspace_start(
    workspace_id=<bench workspace id>,   # the WORKSPACE_ID= line
    previous=<the PREVIOUS= line; leave the argument out when it is empty>,
    added=<the ADDED= array exactly as printed>,
)   →  GroundingStarted { job_id, package_version, package_created, job_reused, … }
```

It returns at once. The backend fetches the parts by their hashes, adds them to the package built on `previous`
(a description replaces the one of its name, every other part stays), and starts the grounding job that builds
the knowledge graph from the corpus against the descriptions it was delivered with.
**Delivering the same bytes again sends nothing new:** the package version is the one the workspace already
has (`package_created` is false) and the job is the existing one, so re-running this skill on an unchanged
extraction keeps one version. Treat that result like any other.

- A refusal reads `package refused (<cause>)`, and nothing changed on the backend. `stale_previous` means
  the workspace has moved on (a teammate delivered, or a fresh clone has no record): read the head from the
  refusal text, pasted into a quoted heredoc so none of its characters reach the shell, and call again ONCE
  with it as `previous`.

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" head --refusal - <<'REFUSAL'
  <the refusal text>
  REFUSAL
  ```

   `previous_unknown` means the recorded version belongs to another
  backend: delete `$WORKDIR/.grounding_package` and call again ONCE with no `previous`.
- **`mixed_posture` and `fingerprint_mismatch` are a posture mismatch**: the corpus was prepared under a
  different anonymization setting than the evidence already in the package (the traces, or an earlier corpus),
  and a workspace holds one setting. This is the error posture's item 2: say in product terms that the corpus
  and the evidence already delivered were prepared with different anonymization settings, that one
  environment holds one setting, and that the part prepared the other way has to be exported again under the
  setting this environment records (`anonymize:` in `plurai.config.yaml`). Never show the cause code or the
  backend's text. Render the failure hand-off and stop: nothing was delivered.
- Any other cause (`hash_mismatch`, `empty_delivery`, …): run item 1 once more, call again once, and on a second refusal, run the contact hand-off (`stop_message.py contact --done`).
- The tool is idempotent on the same previous version and parts, so a transport failure with no envelope is called again once; a second failure: run the unavailable stop (`stop_message.py unavailable --done`).

**3. Remember the package and the job.** Run this with the object the tool returned (an object, never the
refusal text):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" record --workdir "$WORKDIR" --workspace-id "<the WORKSPACE_ID= value>" --result - <<'JSON'
<the JSON object ground_workspace_start returned>
JSON
```

It writes `$WORKDIR/.grounding_package` (the package version the next delivery builds on) and
`$WORKDIR/.grounding_job` (the job that builds and consolidates it). Both are machine-local handles, ignored by
the managed block. The next tool's delivery (Steps 2–4 again) builds on the version this one recorded.

**4. A rebuilt corpus replaces the one it supersedes.** When the `part` command printed
`REPLACES=<sha256>` on stderr, an earlier corpus part carries the same label: new bytes under a label append,
so the old part must be removed or the suite would record both. After item 3, call
`ground_workspace_start(workspace_id=…, previous=<the package_version item 3 recorded>, removed=[<the sha256 it
named>])` with no `added`, then run item 3 again with that result. A refusal is read as in item 2.

**Disclosure + audit stay with the SDK.** The `plurai ingest-corpus` Bash call is the consent chokepoint
(normal Bash approval, naming the exact command), and the CLI writes the preview +
`.plurai/uploads/audit.log`. `ground_workspace_start` carries no chunk bytes: only the part's hash and size,
and the workspace.

### Step 5: Return — the build runs on the backend

This skill **returns once the delivery is accepted** and does not wait for the build: the grounding job
builds each corpus after the version settles, and waiting is the suite's (Step 1's corpus wait of
`/simulation-generate-suite` polls the recorded grounding job until the build ends it; the env does not wait). Do not poll the job here, do not read its
detail, and do not tell the user a corpus is ready: it has been delivered, and the build is the backend's.

The build reads the PRD and the scenario variables from the descriptions the scan delivered, takes the
domain hints and questions from inside the corpus part, and sizes itself: the delivery call has no argument
that shapes the build.

### Step 6: Where the knowledge base is recorded

The suite records it, once the grounding job ends: `/simulation-generate-suite` Step 1's corpus wait reads the job's
`corpus_builds` (one entry per corpus part: `part`, `label`, `job_id`, `status`, `corpus_id`, `node_count`,
`edge_count`, `error`), applies the corpus health gate to each finished build and writes the knowledge-base
markers with

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" record --workdir "$WORKDIR" --status "<the grounding job's saved job_status JSON>"
```

`.knowledge_bases` (one entry per base, the contract `/generate-scenarios` and `/generate-evals` read) and,
for one base with one corpus, `.corpus_id`. An entry has this shape; `tool` is the agent tool that reads the
base and is left out for prompt-injected knowledge, where the domain is the `kb_id` (never invent a tool name
the agent does not have):

```json
{"kb_id": "<tool name or domain slug>", "tool": "<tool name — OMIT for prompt-injected knowledge>",
 "label": "<what this corpus holds — never a claim about the whole source>",
 "corpus_ids": ["<corpus_id>"]}
```

A per-tenant tool delivers several corpora under one `kb_id` and records one entry whose `corpus_ids`
lists them all. This skill writes neither file and never reads a build count. A build that failed or came out degenerate (a graph
the knowledge planner cannot walk) is not recorded there, and the suite tells the user to deliver again with
a smaller `--chunk-size`.

**The backend is the ground truth; `artifacts/knowledge/` is a display copy.** Never read the knowledge graph
or testset back from that copy (or materialize them anywhere else) for any decision: downstream consumers
fetch the corpus via `fetch_artifact(corpus_id)` over MCP, and the copy can be one edit behind.

### Step 6.5: Reconcile the PRD against the corpus

**First, reconcile the PRD — always, from Step 4 item 1c, before any byte is uploaded.** The PRD was authored
from the repo before this corpus existed, and it is
about to become the co-ground-truth every generation reads next to these
chunks: an agent-knowledge claim the corpus contradicts makes generation and
the grounding audit pull in opposite directions on every later run. Follow
`references/prd-corpus-reconciliation.md` (grep the local
`$PLURAI_DIR/rag_corpus/chunks.json` for each PRD knowledge claim; rewrite the
wrong side; mark unfindable claims `(unverified — not found in corpus)`). The PRD is
upstream of every generation, and a generation from a conflicted PRD reproduces the
conflict at full job cost.

**Then fix the SOURCING framing, which the per-claim pass does not touch.** The
PRD was written at `/simulation-scan-agent` Step 3, before this corpus existed,
so its author had to pick the no-corpus branch of the sourcing contract: "the
agent's OWN doc set is authoritative in the corpus's place", with claims marked
`(unverified — not found in the source docs)`. Both statements became FALSE the
moment this corpus landed, and nothing else retracts them — the reconciliation
above rewrites individual claims, never the sentence that says which channel
wins. Sweep the PRD for any prose asserting where facts come from and flip it to
the corpus-backed branch, and switch the marker vocabulary to
`(unverified — not found in corpus)`. A stale authority sentence is how a
ground-truth block ends up backed by no source anyone can check.

**Then re-run the scan's own PRD checks — the reconciliation just rewrote this file, and the
scan contract was projected from the version it replaced.** Both passes above edit the PRD in
place (often its `## Agent facts` block), so run the lines the scan ran over it: the lint, then
the bench manifest's checks — which re-run the appendix parse and re-project the contract over
the edited PRD:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup/scripts/lint_prd.py" \
  "$PLURAI_DIR/simulation_prd.md" --config "$PLURAI_DIR/plurai.config.yaml"
[ -f "$PLURAI_DIR/bench_manifest.json" ] && \
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse
```

A lint exit 4 means findings are open: fix them, or accept one with its reason (setup Step 9). A lint exit 1 names an ERROR the reconciliation introduced — most often a fact added beside one
already stating the same `subject` + `predicate` (merge them). Read `verify`'s exit by
`/simulation-scan-agent`'s `VERIFY_EXIT` table. `verify` re-runs every `always` check — on a bench
with a provider key that includes `smoke_turn` (real, paid agent turns), and consented live probes.
No manifest (a pre-manifest bench) ⇒ run
`verify_prd_appendix.py "$PLURAI_DIR/simulation_prd.md"` instead, and the suite's Step-0 gate
reads the contract as STALE until the bench is re-scanned.

**After the corpus is recorded** (the suite's corpus wait in Step 1, or the record block of a generator): a corpus is consumed by `/generate-scenarios` (to plant knowledge questions =
per-scenario `knowledge_plan.yaml`) and, transitively, by `/generate-evals` (the
backend derives the `knowledge_coverage` + `knowledge_accuracy` pair + `retrieval_recall` /
`retrieval_precision` bundle ONLY from a knowledge-grounded scenario set —
`generate_evals_start` has no corpus parameter; it reads the linked
`scenario_set_id`'s `knowledge_plan.yaml` files). Both read `.knowledge_bases`, which the suite writes
when the build ends (Step 6), so **a scenario set or eval set generated before that is corpus-blind**: zero
knowledge scenarios, zero retrieval/knowledge evals. This skill regenerates nothing: when `.scenario_set_id`
or `.eval_set_id` exists, say in one sentence that the next `/simulation-generate-suite` run records the
corpus and builds the knowledge scenarios and evals from it. NEVER report the corpus as ready to use.

### Step 7: Surface the summary

Backend LLM cost figures (`total_cost_usd` etc.) are INTERNAL telemetry — never show them to the customer, in this summary or any progress line.
The "not ingested" line lists every `not_ingested` entry of `$PLURAI_DIR/rag_corpus/manifest.json` as `<path> (<reason>)`, then every `partially_ingested` entry as `<path> (pages <pages> of <of>: <reason>)`; omit it when there are none.

```
Corpus delivered: <knowledge base label>

  Source: directory  ·  12 source documents  ·  61 chunks
  1 source file(s) were not ingested: docs/blank.md (empty)

  The backend builds the knowledge graph from it; your next
  /simulation-generate-suite run records it and builds the knowledge
  scenarios and evals from it.
  To remove it later, ask me to delete this corpus.
```

One block per knowledge base delivered, then one line per `out_of_reach` entry of `$ART/corpus_roots.json`:
`Not in any knowledge base: <its last path segment> — none of the agent's tools can return it.`, then one line per
part with `not_built`: `No knowledge base for <kb_id>: <its not_built sentence>, so the simulation does not test what that tool returns.` The counts are the local extraction's (`$PLURAI_DIR/rag_corpus/manifest.json`
and `chunks.json`); the build's node and edge counts are not known here and are never printed. A delivery that
repeated unchanged bytes says "already delivered" in place of the first line.

**A gate the user did not answer is RE-ASKED.** If they reject the question, interrupt it, or
reply with something other than a choice, you have no answer — never pick its default, never
pick the recommended option, and never state a choice they did not make. Answer whatever they
actually asked, then put the same gate back. (The flow window re-arms the sanction for you, so
the re-ask is not denied.) Inventing an answer here is not a shortcut: a fabricated corpus
scope silently became the entire product universe for every scenario in one live suite.

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

## Last step, part two — handle the grounding's findings

`${CLAUDE_PLUGIN_ROOT}/docs/grounding-findings.md` is the canonical handler; read it the first time. It runs once the pull above is done, on the job as it stands: the corpus build runs on, so a finding it raises later is read by the suite's Step 0b. The agent
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
keeps the production value as a candidate trace scenario. After each delivery you make for a finding, record it,
wait for the job, pull the mirror again and read again. Three rounds that leave a holding finding end it: carry on,
because the version stays unsettled and unpinned and the suite's Step 0b reads it again. Whatever the findings say stays
internal.

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

- **Credentials never leave the machine.** The source config (with any creds)
  is used only to instantiate the source locally — write it to a temp file the
  CLI reads, then delete it; only the extracted chunks +
  relationships are uploaded.
- **Anonymization is posture-conditional, not unconditional.** Under
  **anonymized** posture, `plurai ingest-corpus` (Step 4) scrubs before it
  uploads and is fail-closed — a missing Presidio/spaCy aborts with nothing
  uploaded, and there is no un-anonymized fallback to "get it through". Under
  the **raw** (default) posture, ingestion uploads as-is with a loud
  `RAW (anonymization off)` banner; that upload is governed by the customer's
  DPA with Plurai. Either way, every upload records its posture in the
  manifest and `.plurai/uploads/audit.log`.
- **Never write the knowledge graph / testset to the customer
  filesystem.** The backend is canonical; fetch over MCP when needed.
- **Never wait for the build.** The skill returns once the delivery is accepted; the suite waits for
  the grounding job and records the knowledge base (Step 6).
- **Reuse-vs-rebuild is gated in Step 3**, before the scope ask and the ingest. New bytes (a new
  extraction) always start a NEW corpus — only delivering the SAME bytes again is
  safe/idempotent (Step 4), whatever the PRD or the variables now say. Rebuilding the same bytes is
  remove-and-redeliver.
- **A rebuild's removal destroys GT — confirm with the user first** (inside a driving
  command's flow window, sanction the ask first: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction delete-confirm`)**.**

## Failure modes + recovery

| Symptom | Likely cause | What to do |
|---|---|---|
| `resolve_plurai.py` exits non-zero (Step 4) | No usable `plurai`: not installed in the client's venv (activated or repo `.venv`) or PATH, or the found install predates the `ingest-*` CLI | Install/upgrade in the client's venv as the resolver's stderr says (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run the resolve line. |
| The ingest block stops with `this plurai cannot carry the corpus domain inside the part` | the runner's `plurai capabilities` lacks `corpus-domain-member` (an SDK below 0.28.2), so it would upload the part without the domain hints | Upgrade the SDK in the client's venv (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line` prints), then re-run. Nothing was uploaded. |
| `plurai ingest-corpus` nonzero exit, stderr names Presidio / spaCy (anonymized-posture envs only) | `plurai-simulation-sdk[anonymize]` or a spaCy model isn't installed (fail-closed under `anonymize: true` — nothing uploaded) | Install the SDK with its `[anonymize]` extra (the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-line --extra anonymize` prints) + `python -m spacy download en_core_web_lg`, then re-run. |
| `resolve_plurai.py` exits 0 but its stderr carries `NOTE: shadows …` | a SECOND `plurai` install is on PATH ahead of (or beside) the one used | Not an error — the resolve SUCCEEDED — but it is a FINDING: carry it into the hand-off verbatim. A shadowing install is why the next command on this machine can behave differently from this one. (A runner below the SDK floor is not this row: the resolver exits 3 with `REFUSED` and the upgrade command.) |
| `plurai ingest-corpus` nonzero exit, stderr names the diagnostic connection | Plurai is not logged in in this shell (fail-closed) | Run the `harness-setup` skill (Precondition 1), then re-run. |
| `plurai ingest-corpus` stderr `CorpusExtractionDependencyError` | the extractor or a source driver extra isn't installed | Take the extra the message names, install it by `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install --extra extract --extra <that extra> --record "$ART"`, and re-run. |
| `plurai ingest-corpus` reports 0 documents | the scope holds nothing the extractor reads (a wrong root, path, query or credential) | Fix the scope where it is decided, then re-run: a `directory` source's roots at `grounding.sources` (re-run `/simulation-scan-agent` Step 3c), any other kind's staged `--source-config` JSON. |
| `chunks.json` has one chunk per file, or `relationships.json` is `[]` (`directory` source) | docs have no heading structure to split at, or `--chunk-mode tokens` was passed | Re-run without `--chunk-mode tokens` and with a smaller `--chunk-size` (e.g. 256). If the docs are genuinely structureless prose, say so — a corpus that thin can't ground retrieval scenarios. |
| A build's `corpus_builds` entry is `done` with `edge_count` below the nodes that owe an edge, or `node_count ==` the source file count, on a **`directory`** source (the suite's Step 0b reports it as degenerate) | under-chunked corpus — too thin to walk for knowledge scenarios ⇒ zero knowledge scenarios | It is not recorded. Re-ingest with a smaller `--chunk-size` and deliver again (Step 4, item 4 removes the old part). |
| The same counts on a **`json_records`** source (`edge_count == node_count - 1`, `node_count ==` the record count, one source file) | NOT a defect — that IS the chain shape a record corpus has | Recorded. Only `edge_count == 0` with more than one chunk is a failure there. |
| A build is `failed` and its `error` contains `has no LLM provider configured` (the failing phase is on the build's job) | the backend has no LLM provider configured for the corpus build | Backend-side, no repair step here: run the contact hand-off (`stop_message.py contact --done`). NEVER prompt the customer for a key; never show the raw error. |
| A build is `failed` with `error='corpus build failed (RuntimeError: ...)'`, or the backend has no corpus storage | backend LLM-side or storage issue, already retried inside the job | Final: do not retry the same inputs. No repair step here: run the contact hand-off (`stop_message.py contact --done`) — no raw error. |

## Glossary

- **corpus** — one built knowledge graph + testset identified by
  `corpus_id`. Persists on the backend as canonical GT. It is built from a **corpus part** of the
  grounding package, which this skill delivers.
- **knowledge base** — one RAG/SQL tool's domain, backed by 1 corpus
  (fixed/shared) or N corpora (per-tenant). Recorded by the suite as one entry in
  `.knowledge_bases`. The backend turns each base into its own scenario
  flow; an agent can own several (e.g. personal-memory + company-docs).
- **chunks** — `Document.to_dict()` payloads extracted from the source
  (the client-side output of `plurai_extract.loader.load_only`).
- **testset** — synthesized samples (question + reference answer +
  reference contexts) the backend produces from the enriched graph.
- **downstream consumers** — separate skills that read the corpus via
  `fetch_artifact` to make the mock serve RAG/SQL faithfully and to
  generate RAG/SQL scenarios + evals.
