# The grounding mirror and the pin — canonical source

The backend keeps one grounding ledger per workspace: the agent's facts, each with its tier and the
evidence behind it, as immutable versions. The client holds two things about it, both on disk in the
workdir.

| File | What it is | Who writes it |
|---|---|---|
| `grounding_ledger.yaml` (the mirror) | The latest version, byte for byte what the backend serves for the job the last delivery recorded. Header: `ledger_version`, `settled`, `pinned`. | `scripts/grounding_mirror.py pull`, nobody else |
| `.ledger_version` (the pin) | The settled version a suite is built on. | `scripts/grounding_mirror.py pin`, nobody else. Four steps run it: `/simulation-generate-suite` Step 0b item 4, `/generate-mock-env` Step 3 item 5 (a fresh env with no pin yet), `/edit-env` "The patched config" item 4, and `/simulate`'s stage boundary item b when it adopts a newer version |

The served header's `pinned` is always `null`: the backend cannot know what a client pinned, and the
mirror is the served bytes, so the pin is its own file. A named env's `plurai.env.yaml` records the same
id as `ledger_version`.

The mirror carries no finding and no overrule: findings live on the grounding job and are read from
there. Facts change in the agent's descriptions (the config, the PRD, the scenario variables), never in
the mirror, and a delivery never carries it. A hand edit lasts until the next pull.

## The pull block

Every skill that calls the backend ends with this block, once, after its last backend call and before
its hand-off. `/simulation-generate-suite` also runs it in Step 0b. Skills carry a verbatim copy of it.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch artifact')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pull --workdir "$WORKDIR" --plurai "$PLURAI_BIN"
```

It prints one line and never fails the skill:

| Line | Meaning | Do |
|---|---|---|
| `MIRROR=pulled ledger_version=… settled=…` | the mirror now holds that version | nothing |
| `MIRROR=ungrounded` | no delivery is recorded in this workdir and the backend holds none for its workspace | nothing; the next scan delivers |
| `MIRROR=unverified reason=…` | the fetch did not finish, or the answer was not a ledger version | nothing; the mirror is as it was |
| `CREDIT-STOP: <sentence>` (no `MIRROR=` line) | the backend refused the fetch for credit (the runner exits 13 with the kind's sentence) | the error posture's credit refusal: stop, and say the sentence, verbatim and alone |

No `MIRROR=` line and no `CREDIT-STOP:` line means the block stopped first: the client pin refused the SDK (`REFUSED`) or
the wrong-backend guard did (`SPLIT BACKENDS`, or a remote URL with no token). Nothing was fetched and
the mirror is as it was; run the contact hand-off (`stop_message.py contact --done`). Never put a `MIRROR=` line, a
version id or a job id in a hand-off: a delivered setup is not news.

A workdir with no recorded job (a fresh clone, a teammate's delivery) is not ungrounded for that reason alone: the pull asks
the backend for the newest grounding job of the workdir's workspace (`list_artifacts(kind='grounding', workspace_id=...)`,
skipping a job that made no version yet) and fetches that job's version. The workspace id is the bench's, and
`<bench id>.<alias>` under a named env (`grounding_mirror.py workspace` prints it, and never mints one). The pull records the job it
found in `.grounding_job`, so the findings and the corpus builds are read from it like those of a delivery made here.

## The suite's wait and pin

`/simulation-generate-suite` Step 0b waits until the recorded delivery job's version settles (`grounding_mirror.py
settled`, not the job's end), pulls, and pins the mirror's version when it is `settled`. `grounding_mirror.py pin` takes no
version: it reads the mirror and refuses an unsettled one (exit 4) or no mirror (exit 5), so no skill can pin an id the
backend did not report settled. A version is `settled` when the descriptions and the trace parts are processed, each corpus
part's evidence is read, and no holding finding is open; the job's `detail.held_by` lists the findings that hold it and
`detail.settled_versions` every version that settled. A trace part never holds. The knowledge-graph build of a corpus part
is not part of settling: env generation starts on the pinned version while it runs, and the scenarios and evals wait for it
on the same version. The build's end makes no new version, so the pin does not move. Without a pin a generator that requires
`ledger_version` is not called. Each generator reads the pin with `grounding_mirror.py pinned` and never
reads the mirror to choose a version, so every generator of one suite builds on one id.

A workspace the operator waived (`grounding_waived_workspaces` on the backend) runs ungrounded only on a call that
carries no `ledger_version`; a call that carries one is grounded on it. The delivery result reports the waiver, and
`grounding_delivery.py record` keeps it in `.grounding_waived` (reason and expiry) until a later delivery reports none.
While it has not lapsed, `grounding_mirror.py pinned` prints nothing, so a `.ledger_version` left on disk is never sent,
and `grounding_mirror.py waived` prints `WAIVED until <expiry>`, on which the generator omits the argument. A workdir that
never delivered under the waived workspace has no marker, because the marker is machine-local and the backend's waiver is
not readable from the client. With no marker, no pin and no recorded job, the generator makes the unpinned call: it omits
`ledger_version` and the backend decides. A waived workspace runs ungrounded, and any other is refused with
`ledger_version refused (missing)`, on which the generator stops and tells the user once that `/simulation-scan-agent`
records the agent's setup. A call that starts and reports `detail.grounding_waived` writes the marker, so the next call on
this machine omits the version at once. `grounding_mirror.py send` prints the decision as one line: `SEND <id>`,
`JOB <id>`, `OMIT waived until <expiry>` or `OMIT unpinned`.

## How a waived or ungrounded workspace gets a brief

The waiver changes only generator calls. Delivering a trace part still runs `ground_workspace_start`, and the job it
starts runs `trace_analysis` and publishes the brief whether or not a waiver covers the workspace. A workspace that never
delivers anything has no job and no brief. Ungrounded generation therefore has a brief only after `/analyze-traces`
delivered a trace part.

Before the first scenario call, the suite records the knowledge bases the job built (the corpus record block below).
`/build-rag-corpus` delivers a corpus part and returns; the backend builds the knowledge graph after the version settles,
the job ends when the build does, and it lists each build in the job detail's `corpus_builds` (`part`, `label`, `job_id`, `status`, `corpus_id`, `node_count`, `edge_count`, `error`).
`grounding_corpus.py record` applies the corpus health gate to each finished build and writes `.knowledge_bases` and
`.corpus_id` for the healthy ones, so the scenario and eval generators keep reading the markers they always read.

## The corpus record block

`/build-rag-corpus` returns once its delivery is accepted; the backend builds the knowledge graph after the version
settles, and the grounding job ends when the build does. The suite runs these blocks before its first scenario call, so the
env is generated while the corpus builds. A skill that passes corpus ids (`/generate-scenarios`, `/refine-scenarios`, the
corpus edit of `/edit-env`) can run straight after `/build-rag-corpus`, so each carries these two blocks verbatim, after the pin is read and before the knowledge-base markers are. The first reads the
recorded job and how many delivered corpus parts have no outcome yet:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "JOB=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" pending --workdir "$WORKDIR"
```

`PENDING=0` means nothing was delivered here or every build has an outcome: go on. Otherwise wait for the job to end (the
build ends it) with `poll_job.sh` in the foreground (the recipe in `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`; exit 14 means wait again in the same
turn, exit 13 means the job failed and nothing is recorded; on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)), then run the second block. `record` writes `.knowledge_bases` and
`.corpus_id` for the finished, healthy builds only and is idempotent, so the suite and any number of generators share it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "CORPUS=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.corpus_status.json.tmp" \
  && mv "$ART/.corpus_status.json.tmp" "$ART/.corpus_status.json" || { rm -f "$ART/.corpus_status.json.tmp"; echo "CORPUS=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_corpus.py" record --workdir "$WORKDIR" --status "$ART/.corpus_status.json"; echo "corpus record exit=$?"
```

Read the exit: 0 go on; exit 1 a build failed, came out degenerate or belongs to another machine, so it is not recorded and
the generator is called without that base (a degenerate one is a smaller `--chunk-size` and a new `/build-rag-corpus`); a failed build: run the contact hand-off (`stop_message.py contact --done`); exit 4 a build is still running, so wait again and rerun; exit 3 the status or a
marker cannot be read. When the grounding job is not settled, the skill's own wait-and-pin rule decides whether the generator
runs at all, and never pass a corpus id from a build that has not finished: only what `record` wrote is passed.

## Staleness

`grounding_staleness.py` compares the mirror's version with the pin and prints one `STALENESS=` line:
`ungrounded` (no pin), `unverified` (a pin and no mirror), `current`, or `moved`. A moved check asks the backend for
`fetch_artifact(<mirror's version>, paths=['diff/<pinned>'])`, keeps it as `artifacts/ledger_moved.yaml` and counts the
entries, findings and parts it adds, removes and changes (`parts` has no changes). With no runner, or no answer, or an answer that is not the diff between exactly
those two versions, it says only that the mirror's id differs from the pinned one as of the last pull. It never
rewrites the mirror and never moves the pin: adopting a newer version is a stage-boundary edit, not a check.
`/generate-mock-env` runs it once the pin is known and before it sends the pin; `/manage-envs` USE and
`/simulation-generate-suite` Step 0c run it beside the manifest drift check. No skill asks the user about the answer.
The check needs PyYAML for the counts, so skills run it under `$PLURAI_PY`; without PyYAML the line carries
`counts=unavailable` and the diff file still holds the detail. It is a script of its own so that `grounding_mirror.py`
stays stdlib-only and the pull block runs on any interpreter. The diff fetch is `grounding_mirror.fetch_diff`, shared
with the stage-boundary adoption.

## Adoption at a stage boundary

`/simulate` runs the pull block before it invokes the `suite`, `run` and `fixes` stages, and adopts a newer settled
version there, never inside a stage: `docs/grounding-adoption.md`.

## SAVE and USE

`grounding_mirror.py save` copies the mirror and the pin into the named env's directory beside its
config and variables. `restore` brings them back and prints `GROUNDING=ungrounded ungrounded, deliver
first` for an env that has no mirror. A mirror and its pin belong to the workspace the version was made for, and every
generator refuses a pin from another one. Both commands take the env's own workspace (`--workspace-id`): `save`
refuses a mirror whose manifest names another workspace (exit 3, nothing copied, one line saying the pin was not saved, why, and the next step; a missing mirror prints the same as `notice=`; the skill offers its delivery-only repair), and `restore` brings back nothing
from one and prints `GROUNDING=other_workspace`, after which the skill pulls the env's own version by its workspace id. `env_manifest.py write` records the workdir's pin as the manifest's
`ledger_version`.
