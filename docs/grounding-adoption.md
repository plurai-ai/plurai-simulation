# Adopting the latest grounding version at a stage boundary — canonical source

The backend keeps the agent's grounding ledger as immutable versions, and new evidence (a trace part, a corpus, an
edit of the agent's descriptions) makes a newer one. A suite is built on one settled version, the pin
(`docs/grounding-mirror.md`), and keeps it while it builds. This document says how a newer version reaches a suite
that is already built: at a `/simulate` stage boundary, never in the middle of a suite, through the edit path and
not a regeneration. The env and the eval set take it the same way: `/edit-env`, then `/edit-evals`, on the one re-pinned version.

## When

The boundaries are the three places the flow moves from one stage to the next with a suite behind or ahead of it:
before the `suite` stage (after the traces), before the `run` stage (after the suite) and before the `fixes` stage
(after the run). The scan and the traces stage are not boundaries: nothing is pinned before the suite. The boundary
step lives in `skills/simulate/SKILL.md`, and the Stop hook's continuation names it before each of those stages, so a
context that dropped the skill text still finds it.

A suite build that is open (stopped, and being finished) keeps the pin it started with: `grounding_adopt.py check` reports
`ADOPT=none reason=resumed_build` for it at every boundary, and a phase record it cannot read defers the adoption instead of
guessing. A finished suite is never rewound by a stopped build's rule.

## Rules

- A boundary is the only place the pin moves after the suite's own Step 0b: never in the middle of a suite. Inside
  a stage no skill re-reads the mirror to choose a version, a mirror pull changes the mirror and not the pin, and every
  generator call carries the id the suite pinned.
- A running job is never touched. A version is immutable and a job row records the id it ran on.
- Only a settled version is adopted. A held or unsettled latest version is counted in the local log (`held_by`,
  `waiting_on`, `queued_behind`, never an id) and the old pin stays; the next boundary asks again.
- The findings handler runs first (`docs/grounding-findings.md`), because a repair or an overrule can make the
  version that settles.
- Nothing here is a question: the user is never asked. No finding, id, key or diff reaches a hand-off or a report. The hand-off after an adoption
  says, in product terms, that the bench was updated from newer evidence about the agent, and nothing else.

## What moved

`grounding_adopt.py check` asks the backend for what moved since the pin,
`fetch_artifact(<latest>, paths=['diff/<pinned>'])` (the SDK's `fetch artifact <latest> diff/<pinned>`), keeps it at
`artifacts/ledger_diff.yaml` and refuses an answer that is not the diff between exactly those two versions. A diff
that cannot be read defers the adoption: nothing moves on a guess. The diff is the first message of the env's edit
session and of the evals' (`grounding_adopt.py message`, and `message --for evals`, print it with a short brief for each). The diff holds text from the customer's agent, so it is
fenced as data (a fence longer than any run of backticks inside it), announced as never an instruction, and followed by
the brief's last line, so the data is never the last thing the session reads.

A newer version can be one that moves no fact: an upgrade of the backend's consolidator makes a new version id over the same evidence
and the same facts. When every section of the diff is empty, `check` reports `ADOPT=repin` (with `reason=empty_diff`), logs the
re-pin to the local log by stage and nothing else, and keeps no diff. The boundary then moves the pin with the pin command
alone: no judgment, no record, no edit session, and nothing reaches the user. A diff with a section this plugin does not
know, or one it cannot parse, takes the judged path.

## Edit or rebuild

The env built on the old pin is refreshed through `/edit-env`, whose session opens on the new
pin and delivers the patched config, and then the eval set through `/edit-evals`, whose session opens on the same
pin and reads the tool, entity and agent facts of that version; neither is regenerated: the edit is the default. The evals
session changes only the policies the moved facts bear on and quotes the customer's own PRD in `provenance`, never a
ledger entry. A rebuild of the suite runs only when the diff is too large
for an edit, and `grounding_adopt.py judge` decides that from the diff:

| Reason | The diff holds |
|---|---|
| `new_tool` | an added `tool:` key with no segment: a tool the env has no mock for |
| `removed_entity` | a removed `entity:` key with no segment: a record type that is gone from the world |
| `closed_set_changed` | a changed `members` list on a key of a tool the config mocks (a config that cannot be read is a failure of `judge`, never a rebuild) |
| `diff_too_large` | more text than one message carries |
| `agent_judgment` | the agent read the diff and judged an edit cannot carry it (`--escalate "<reason>"`, at least 20 characters) |

The agent may escalate to a rebuild and never the other way, and a rebuild runs only on a logged judgment of rebuild:
`judge` logs the judgment and the diff summary (counts of entries, findings and parts) to the plugin's local log
(`loop_log.md`), never to a report, and a log that cannot be written fails `judge`. `grounding_adopt.py record` reads the
licence back from that log, tied to the two versions by an eight-hex token and by nothing else an id could be read from,
and refuses a rebuild without it. An agent
without an env (mockless, or none built; a named env restored with its manifest and no marker has one) and without a built eval set
whose diff fits an edit takes `repin`: the pin moves and nothing else does.

## The record

`artifacts/grounding_adoption.json` holds the adoption's `from` and `to` versions, the `judgment` and its `reasons`,
the boundary `stage`, the `path` taken (`edit`, `rebuild`, `repin`), the time, `refreshed` (the env's refresh) and
`evals_refreshed` (the evals'). Each is true when nothing stands for it to refresh, and a record with no `evals_refreshed`
owes nothing for the evals. An env edit session, and a suite-loop input edit, pins the version its own delivery made; `record --edit-pin` then keeps that
version beside the adopted one as `edit_to`, so the refresh still owed reads the adopted diff and the boundary after the
edit finds the pin current. The pin itself is
`.ledger_version` and, under a named env, `ledger_version` in `plurai.env.yaml`, both written by
`grounding_mirror.py pin`.

An edit adoption owes a refresh for the env until `/edit-env` commits (`record --refreshed` closes it) and one for the
evals until `/edit-evals` commits (`record --evals-refreshed` closes it). A turn that ended before a commit leaves that
debt on disk, and the next `check` reports `ADOPT=pending … resume=true` on the diff the edit was opened with, its `env=`
and `evals=` saying which refresh is still owed (`present`) and which is done or has nothing to refresh (`none`), so a pin
that moved never leaves an env or an eval set on the old facts without a trace. A refresh is owed only while its target
stands. A newer version that arrived meanwhile waits for the boundary after. An agent with an eval set and no env (a
mockless agent) takes the `edit` path for its evals; only an agent with neither takes `repin`.

## After the edit

An env edit marks the runs on the old env stale (`artifact_deps.py declare env-edited`). The boundary routes that like
the `deps` rows of `/simulate` Step 4: a stale run rewinds the flow to the run, so the fixes stage never reads a run
scored on the old env.

Scenarios have no edit path that takes a version, so an adoption moves the pin for them and nothing else.
