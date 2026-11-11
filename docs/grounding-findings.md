# The grounding findings handler — canonical source

The backend's grounding job reports what it found while it consolidated the agent's descriptions, traces and
corpus. Those findings are handled the way every other gate finding is (`${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`,
not amended here): the agent decides alone, never asks the user, copies fields and never recomputes them, and no
finding or overrule reaches the hand-off, a run report, a suite report, `evals.md` or `scenarios.md`.
Facts change in the agent's own descriptions, never in the mirror (`${CLAUDE_PLUGIN_ROOT}/docs/grounding-mirror.md`).

## Who decides what

Only an evidence fault in a corpus part holds a version: a corpus part that failed to process, with `evidence_missing` on a
mocked kind (`handle=remove_part`). Trace analysis is opportunistic and never holds: a trace part whose analysis failed is
`evidence_missing` on `grounding.disagreement` (`handle=accept`), and a trace corpus that spans environments
(`handle=attest_or_reexport`, gate `trace.corpus_environment_unattested`) is skipped, not analyzed, while the version settles
without it. The suite never stops on a trace part: it goes on, and a failed one costs the report one run-integrity line
(below). Every value conflict is a non-holding `grounding.disagreement`, whatever its tiers: the backend keeps the better-ranked value,
a trace adds its observed values beside the declared one (it replaces a type only when that type is a guess), and the plugin
has nothing to answer it with. A trace is compared whatever its window;
a trace that disagrees with a code fact gets the remedy "the mock keeps the code value; the production value is in the entry's
evidence"; two descriptions that disagree get a remedy that names a file. In every case the plugin makes no description edit,
sends no delivery and no overrule: the losing description keeps its value, and `log` keeps the losing value as a candidate
scenario.

The plugin keys on what the job reports (`holds`, and the kind of an evidence fault), never on the wording of a remedy and
never on a pair of tiers.

`/simulation-generate-suite` Step 0b runs the handler after the mirror pull and before the pin. Every skill that
delivers a part (`/simulation-scan-agent`, `/analyze-traces`, `/build-rag-corpus`) runs it after its own pull
(below). `scripts/grounding_findings.py` reads; the agent removes, exports again, delivers and calls.

## Read the findings

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "FINDINGS=none"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}" > "$ART/.grounding_status.json.tmp" \
  && mv "$ART/.grounding_status.json.tmp" "$ART/.grounding_status.json" \
  || { rm -f "$ART/.grounding_status.json.tmp"; echo "FINDINGS=unread"; exit 0; }
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" show --status "$ART/.grounding_status.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" log --status "$ART/.grounding_status.json" --art "$ART"
```

`FINDINGS=none` means nothing was delivered from here. `FINDINGS=unread` means the job could not be read: read it
once more, and if it is still unread go on, because the pin step decides on the mirror. Otherwise the block prints
one `GROUNDING_FINDINGS` header and then each finding, holding ones first, in the backend's own terms: `gate_id`,
`authority`, `holds`, `kind`, `key`, `kept_tier`, `kept_value`, `other_tier`, `other_value`, `fingerprint` and
`remedy`, copied. A finding that holds nothing and is accepted has the remedy's "or overrule" clause cut and reads "(no overrule: this finding holds nothing)" instead, because an overrule on it comes back `not_holding`; `--json` keeps the backend's text whole. Beneath each comes the `handle=` the plugin gives it. A value cut for length says how many characters it dropped.

`log` writes the notes a finding owes the plugin's local log (`loop_log.md`, never customer-facing): the production value of each code-versus-trace disagreement as a candidate trace scenario, and the
losing value of every other value conflict as a candidate scenario, with no blame until a run fails.
`show` marks each with `candidate=scenario`. Each candidate is also recorded in
`artifacts/grounding_candidates.json` (the key, its tool, both values and tiers, the trace units and the call count; no
fingerprint), each key and observed value once, and `log` prints `CANDIDATES=<total>`. `/trace-to-scenarios` reads the
file to choose which of the brief's trace failures to turn into trace scenarios first. `log` writes nothing else.


## Read the findings without saving the envelope

`/analyze-traces` and `/build-rag-corpus` read the same findings and keep no copy of the job envelope: it is piped
into each command and nothing is written for it.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
JOB="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" job --workdir "$WORKDIR")"
[ -n "$JOB" ] || { echo "FINDINGS=none"; exit 0; }
STATUS="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$JOB\"}")" || { echo "FINDINGS=unread"; exit 0; }
printf '%s' "$STATUS" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" show --status -
printf '%s' "$STATUS" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" log --status - --art "$ART"
```

It prints exactly what the block above prints. `remove` and `overrule` take the same input: read `job_status` again and pipe it
to the command with `--status -`.

## What each `handle=` means

| `handle=` | What it is | What the agent does |
|---|---|---|
| `accept` | a value conflict of any tiers (it never holds), a collision or state marker, a trace part's `evidence_missing`, or any other non-holding `evidence_missing` | no edit, no delivery, no overrule; `log` records the losing value of a value conflict as a candidate scenario (a code-versus-trace production value is a candidate trace scenario). A failed trace part may be delivered again on your own judgement: that grounds the next build and never moves this build's pin |
| `remove_part` | a corpus part failed to process, its evidence is missing on a mocked kind, and the finding holds | remove the part (below), or deliver it again through its own skill. Never an overrule |
| `informational_copy` | a generator job's copy of a finding | shown and never overruled: the grounding job's own finding is the one that is handled |
| `attest_or_reexport` | `trace.corpus_environment_unattested`: the staged traces span more than one environment, or are not the one the analysis is for, so the part was skipped. It never holds the version: nothing waits on it. `environments=`, `expect_environment=`, `declared=`, `opaque=` and `file_count=` are the census, copied | optional, on your own judgement, and it grounds the next build: when the export is the agent being grounded (you narrowed it to its project, runs or tag, and the census shows the rest is not the agent), overrule with a reason that says what you narrowed it by (below). Otherwise export the traces again narrowed to the one environment and deliver again through `/analyze-traces` with `removed` set to the held archive (below) |
| `never_overrule` | `security` or `invariant` authority | follow `gate-findings.md`; no overrule, and no second attempt after a refusal |
| `other_gate` | a finding of a gate that is not a grounding gate | follow `gate-findings.md` for that gate |

## Overrule a skipped trace corpus

The one overrule the agent may send attests a trace corpus skipped for its environment (`handle=attest_or_reexport`). It is
sent on the next `ground_workspace_start`, which a call without `added` makes on the package already recorded:
`workspace_id`, `previous` from `grounding_delivery.py previous`, and `overrules` with the object the checker prints.
Nothing else goes in that call.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" overrule --status "$ART/.grounding_status.json" \
  --gate-id "<the finding's gate_id>" --fingerprint "<the finding's fingerprint>" --reason "<what the export was narrowed by>"
```

The checker prints the object or refuses with one line and sends nothing:

- the `gate_id` and `fingerprint` are copied from a finding this job reports now, byte for byte;
- the reason has at least 20 characters and says what the export was narrowed by (the project, the run filter, the
  environment tag), because what lifts that hold is how the traces were chosen and not a line of the customer's code;
- only the holding `trace.corpus_environment_unattested` finding is overruled: never a grounding disagreement, a
  marker kind, a failed part, a finding that holds nothing or a generator job's copy, and never a `security` or
  `invariant` one.

An overrule that applies makes a new job, and the read block above shows the finding gone after the wait. One that
lifts nothing returns the existing job: save the object the call returned to `$ART/.grounding_status.json` and run
`grounding_findings.py show` on it.

`remove --part <the key of the failed-part finding>` prints the `removed` argument for a part whose processing
failed, and `remove --part <the finding's archive_sha256>` the one for a trace part held for its environment. Send it the
same way; the second goes in the same call as the corrected export (`/analyze-traces`, Step 6c). Removing erases the
part's uploaded bytes, so it is never done for any other part.

## The one line that reaches the report

A finding never reaches a customer-facing file. One grounding fact does: a trace part of the pinned job whose analysis
failed (`detail.failed_parts` with `kind: trace_archive`), because the suite was then built without production traces.
`integrity` prints the line, or nothing:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" integrity --status "$ART/.grounding_status.json"
```

The suite carries the printed line, verbatim, as the report's run-integrity line.

## After a delivery and after a pull

A delivery makes a new version, and the findings of a version show on the job that made it. Every skill that delivers
a part runs the read block above once its own pull is done, and acts on what it prints:

| Skill | Where | The job it reads |
|---|---|---|
| `/simulation-scan-agent` | Step 4d, after the pull of Step 4c | the descriptions' job, already waited for at Step 4b |
| `/analyze-traces` | Step 6c, after the pull of Step 6b | the job Step 5 waited for until it was terminal |
| `/build-rag-corpus` | the findings step, after its pull | the job as it stands: the build runs on, so what it finds later is read by the suite's Step 0b |

Each carries the read block verbatim.
`FINDINGS=none` is a workdir that delivered nothing from here. Act by `handle=` as the suite does: the description
removal of a failed part, the attestation or re-export of a trace corpus. A value conflict, and any finding that holds nothing, is
accepted: no description is edited for it. After each delivery record it (`grounding_delivery.py record`), wait for the job (`poll_job.sh`),
pull the mirror and read again: an overrule makes a new job and a new version without reprocessing. After three rounds
that leave a holding finding, stop handling it: the version stays unsettled and unpinned, and run the contact hand-off (`stop_message.py contact --done`) with no finding text. None of it is said to the user, and none of it enters the hand-off.

A delivery or an overrule the backend refuses states its cause in the refusal text (`package refused (<cause>)`,
`overrule refused (<cause>)`). Size limits are among them: more than 256 parts added or removed (`too_many_parts`), a name,
label or fingerprint over 128 characters, more than 200 overrules (`too_many_overrules`), a reason over 4000
characters (`reason_too_long`). Read the text as it stands and never paraphrase it. A size refusal is not called again
unchanged: shorten the reason or send fewer entries, and when that cannot be done, run the contact hand-off (`stop_message.py contact --done`).
