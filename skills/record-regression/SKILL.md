---
name: record-regression
description: >-
  Record a confirmed agent issue as a regression folder under plurai_sim/regressions/ that replays the conversation which exposed it. One backend scoring call measures how often it recurs, kept in replays.json. Run by /simulation-fixes and /simulation-generate-suite, or by hand.
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Record a regression

**Usage:** `/record-regression [--run <out-dir>] [--issue <n> | --finding <finding_id>] [--plan <plan.json>] [--scenario <name>] [--cut-turn <K>] [--slug <issue_slug>]`

The issue comes from one of two sources:

- **`--issue <n>`** — issue `n` of `$ART/fixes.md`, the fix plan `/simulation-fixes` wrote. This is
  the source at fix acceptance, and the default.
- **`--finding <finding_id>`** — the confirmed section of `$ART/agent_findings.md` whose stable id
  is `finding_id` (`agent.shortfall_unreported`). This is the source at the suite build's gate
  exit: the finding is confirmed and no fix has been accepted for it. `--slug` does not apply; the
  slug is the one the id derives (Step 3).

You are the LOCAL Claude Code agent. One confirmed **agent issue** is about to be fixed, or the
suite build has just confirmed one. The conversation that exposed it lives only in this run's
artifacts, and the next suite build regenerates every scenario — so unless the conversation is recorded now, the reproducer is gone
before the fix can be verified against it, and long before a relapse could be caught.

This command records it. It writes **one folder and nothing else** (Step 6c adds the folder's
local `replays.json`):

```
plurai_sim/regressions/
  BUNDLE.md                              ← what the directory holds, rewritten each time
  regression_<issue_slug>/
    ROLES.md                             ← the issue paragraph, the converter's resume section, and — when
                                           the scenario was a trace scenario — its world facts, last
    issue_turn.json                      ← the issue's turn as recorded: the user's message and the agent's reply
    resume_pin.json                      ← the conversation, cut before the issue's turn
    transcript.md                        ← the same conversation as prose, for the author to read
```

or, when the issue is on the **opening turn** and there is nothing to seed:

```
  regression_<issue_slug>/
    ROLES.md
    issue_turn.json
    first_talk.txt                       ← the recorded opening message, said verbatim, no pin
```

**Nothing is uploaded and nothing is generated here.** The folder reaches a scenario set through
the **final batch** of the next `/simulation-generate-suite`, which stages this directory and
`trace_scenarios/` together and uploads them as one bundle. Until then `--verify` checks the fix through the original scenario, which is the
loop exactly as it stands.

**What the folder deliberately does NOT hold:** world state, agent context, persona, scenario
prose, evals. Every build authors those again from the current pipeline. Freezing them would
freeze products of the very SDK and plugin version whose defects this loop later fixes, and the
backend's seed-coherence audit is what validates the frozen seed against the current world.
The **world facts** a trace scenario hands on (Step 6b) are not a frozen world: they are evidence
of what production's tools returned, read by the world author as a constraint.

**Why each choice is what it is.** The bench can be wrong: a test-setup or mock-env defect can
produce a failure that looks like the agent's. So a regression keeps agent evidence only, and only
an agent issue from a clean run is recorded (Step 1 states the test). An accepted bench fix puts
every active regression back under re-verify. The final batch is the only publisher, because a
second place where scenarios enter a set is a second thing that can drift. A regression stays after
its fix, and `--verify` runs the whole set.

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

## Step 0 — resolve the dirs and the run

Resolve `$WORKDIR` / `$ART` (named-env aware, as in scan-agent Step 0), and pick the run in the
same call — a `$WORKDIR` from an earlier Bash call is empty:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo "regressions: $WORKDIR/regressions"
cat "$WORKDIR/.last_run" 2>/dev/null || ls -1dt "$ART"/runs/*/ 2>/dev/null | head -1
```

`--run <dir>` overrides both. If nothing resolves, stop and say a run is needed first; there is
no conversation to record without one.

**No flow window is opened here.** This command runs inside the `fixes` window when
`/simulation-fixes` invokes it, and needs no gate of its own: the acceptance the operator already
gave at fix selection is the sanction for this write. Inside the `generate-suite` window, at gate
exit, it needs none either: the invocation of the suite build is the sanction, and
`--no-auto-regressions` is how the operator withholds it. Run by hand outside any flow, it opens
nothing either — it writes one folder in the customer's own repo and asks nothing.

## Step 1 — the gate: an agent issue, from a clean run

**Only an agent issue is recorded**, because a regression replays the agent's own conduct and
must not preserve anyone else's mistake. And it is recorded **only from a clean run** — one whose
report holds no bench issue that could have produced the failure: `fixes.md` for an `--issue`, the
quality gate's report for a `--finding` (below).

With `--issue`, read `$ART/fixes.md` and check all four, in order. Any one of them stops the record:

1. **The issue is a CONFIRMED one — a numbered `## <n>.` heading.** The `## Potential agent
   issues` block below them is a different thing: entries the suite build flagged and no run
   confirmed, carrying `severity if real` and no `home`, under a preamble saying each one needs
   the operator's call. An operator may well accept one as real and have it fixed — that is what
   the block is for — but a fix accepted is not a failure observed, and recording it freezes a
   reproducer for conduct no scored run ever caught.
2. **The issue's home is `agent bug`.** A `test-setup` or `mock-env` issue is never recorded.
3. **No bench issue touches this issue's scenario or its policy.** A bench defect on the same
   scenario or the same policy could be what produced this failure, so the evidence is not the
   agent's until a clean run says so.
4. **No harness-wide bench issue is open at all.** A defect in `simulate.py`, the config or the
   mock env's implementation reaches every scenario, so it blocks every record from this run.

When any of them fires, **say so and record nothing**:

> Not recording *<issue title>* yet — `fixes.md` also carries *<bench issue title>* (test-setup)
> on the same scenario, so this failure may be the bench's. Fix that, re-run, and record from the
> clean run.

The regression is then recorded one loop iteration later, from the clean re-run. This is not a
delay to work around: a regression recorded from a dirty run replays the bench's mistake forever,
under a name that claims it is the agent's.

Check 1 refuses for a different reason and gets its own line — nothing here is waiting on a
cleaner run, it is waiting on a run that catches the thing at all:

> Not recording *<issue title>* — it sits under *Potential agent issues*, which no run has
> confirmed. Fixing it is your call and the fix stands on its own; the regression waits until a
> scored run actually catches the behaviour, and there is then a numbered issue to record from.

### With `--finding`: the same four checks, against the gate's report

At the suite build's gate exit there is no `fixes.md`. The issue is a section of
`$ART/agent_findings.md`, and the bench issues are the ones the quality gate left open when it
landed: every **BLOCKING — UNRESOLVED** entry, every accepted gap on a scenario or eval
surface (an accepted harness gap is listed under `accepted_harness`, not open), every red the red-cell audit ruled the bench's, and every unticked line in
`$ART/deferrals.md`. When the suite build invokes this, it has already read those into its plan's
`bench` block — `scenarios`, `policies`, `harness_wide`, `accepted_harness` — so take that block as
the report and do not re-derive it. Run by hand, read them off the landing yourself. Then check all four, in order:

1. **The finding is CONFIRMED.**

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" finding "$ART/agent_findings.md" "<finding_id>" \
     --regressions-dir "$WORKDIR/regressions"
   ```

   It prints the finding — title, severity, `policy` (the probe's eval policy), `seen_in`, and the
   `slug` its id derives — and exits 2 when the file holds no such id, or holds it as `potential`,
   retired, or under a status it cannot read. A potential finding is refused for the reason check 1
   above refuses the `Potential agent issues` block.
2. **Its home is the agent.** A confirmed section carries a `why it is the agent:` line, which is
   the critic's classification. A section without one is refused: nothing says the failure is not
   the bench's.
3. **No bench issue touches its probe's policy.** A bench issue on one of its `seen in:` scenarios
   is not a stop: `pick` leaves that scenario out (`--exclude-scenario`) and cuts from a clean one.
4. **No harness-wide bench issue is open at all.** `accepted_harness` is not one: a harness gap the
   landing accepted in a run it kept qualified never gets the clean re-run this check waits for, so
   it is noted and does not stop the record. What the critic says it reaches is already in
   `scenarios` / `policies`.

A refusal here takes the same shape as above, naming the gate's report instead of `fixes.md`:

> Not recording *<finding title>* — the quality gate left *<bench issue>* open on its policy, so
> this failure may be the bench's. It is recorded from a later build whose gate
> leaves that policy clean, or at `/simulation-fixes` acceptance from a clean run.

## Step 2 — check the installed SDK can do this

The conversion is the SDK's, and an SDK without the reader or the cut writes a different folder
than this command describes. Check before writing anything:

```bash
HELP="$(plurai convert-trace --help 2>&1)"
printf '%s\n' "$HELP" | grep -q -- '--cut-turn' \
  && printf '%s\n' "$HELP" | grep -q -- 'plurai-run' \
  && echo 'both present' \
  || echo 'MISSING: convert-trace lacks the reader, the cut, or both'
```

Both markers are checked separately because they are separate capabilities: counting lines that
match either one passes on a help text that names the reader twice and has no `--cut-turn` at all.
`both present` is the only answer that clears this step. **Anything else is a blocker, not
something to work around** — report it and stop:

> Can't record the regression: the installed `plurai` has no `--cut-turn` on `convert-trace`
> (or no `plurai-run` reader). Update the SDK and run `/record-regression` again.

Do not hand-write a `resume_pin.json`. The pin is machine-read and carried into the generated
scenario byte-preserved; authoring one by hand puts bytes into a customer's suite that no
converter produced.

## Step 3 — map the issue onto the folders that already exist

**One regression per issue, not per sighting.** Before choosing a name, read what is already
recorded:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" list "$WORKDIR/regressions"
```

**An existing folder whose issue is this issue means it is already covered.** Open its `ROLES.md`
and read the paragraph — the slug alone is not enough to decide. If it covers this issue, record
nothing and say which folder already carries it.

**An issue that is a confirmed finding takes the slug its id derives,** so both sources name it
alike. That is every `--finding` issue, and every `--issue` whose `fixes.md` section names a
finding id (its `Finding:` line — an issue `/simulation-fixes` seeded from `agent_findings.md`):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" finding-slug "<finding_id>" --regressions-dir "$WORKDIR/regressions"
```

`agent.shortfall_unreported` derives `shortfall_unreported`. Two ids can cut to one slug at the
name cap; a folder under that slug whose `Finding id:` line records another finding is that
finding's, so the command prints the next suffixed slug (`<slug>_2`) for this one. **A folder of
the name it prints is this issue, already covered** — typically the suite build recorded it at gate exit, before any fix was
accepted — so record nothing and say which folder carries it. A folder under another name whose
`ROLES.md` names the finding id in backticks covers it too. `--slug` does not apply to such an
issue: any other name makes the same finding look uncovered, and the next acceptance records it
twice.

Otherwise choose the slug: **the failed policy or the probe, plus a short cause word**, in
lowercase words joined by single underscores — `refund_timing_invented`,
`greeting_omits_agent`, `escalation_promised_not_made`. The folder is `regression_<slug>`, and
that same string is the scenario's name in every delivered set: the suite build and
`/simulation-run` find the regression in a set by that name. `--slug` overrides.

**The name is the identity.** Retiring a regression and re-recording it under a new slug makes it
a different scenario, and the runs and reports that name the old slug no longer line up with it.
That is by design, and it is a decision, so make it deliberately rather than by renaming.

## Step 4 — choose the conversation and the cut

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" pick "<the run dir from Step 0>" --policy "<the failed policy>" \
  --workdir "$WORKDIR" --out "$ART/regression_picks/regression_<issue_slug>.json"
```

On a harness whose manifest declares `harness.resume: unsupported`, a turn-0 issue is still `opener-only`, and an
issue on a later turn is `shape: opener-rooted`: the recorded opener and the paragraph, replayed as a whole
conversation. A later cut is opener-rooted on any harness when the pick lists `world_written_before_cut`: the history
changed a world its pin cannot restore. Relay any refusal as written.

**With `--plan`** (the suite build passes `$ART/auto_regressions.json` with `--finding`; `/simulation-fixes` passes
`$ART/fixes_regressions.json` with an `--issue` whose section has a `Finding:` line), pick from the plan's row instead:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" pick "<the run dir from Step 0>" --workdir "$WORKDIR" \
  --plan "<the plan>" --finding "<finding_id>" --out "$ART/regression_picks/regression_<issue_slug>.json"
```

The row is the whole choice: the finding's own conversations over every trial (a later trial, or a regression folder's
replay, included), each cut at the finding's own turn, with the plan's bench left out. The pick's `probe` is the check
the row records on (the section's probe, or another check the run tied to the finding): the paragraph's **Probe** line
names that check, never the section's own when they differ. Add no other flag; `pick`
refuses them, a plan made from another run, and a finding the plan does not record. With `--finding` and no plan (by
hand), pass `--policy`, `--seen-in <name>` per `seen_in` Step 1 printed, and `--exclude-scenario` / `--exclude-cell`
per bench scenario and cell; never pass `--scenario` to reach another conversation. When `pick` refuses, record nothing
and report its reason: the finding stays uncovered until a later build, or `/simulation-fixes` at acceptance, records it
from a run that holds its conversation.

It prints the scenario (`item`: `<scenario>/trial_<N>` for a later trial), the cut, the shape and the `transcript` as JSON, and saves the same JSON under
`$ART/regression_picks/`, named for the folder this record writes — scratch, under the gitignored
artifacts dir. `/simulation-fixes` reads that file once its records are done, to decide whether a
trace scenario retires, and its retire step removes the file once it has decided. A refused pick
removes the file too, so no earlier pick under this slug is left behind. Pass `--out` on every pick, with or without the overrides below. What it is doing:

- **The scenario** is the one with the **shortest non-zero seed** among those where the policy
  failed. The same issue in three conversations is one root cause, and the shortest seed replays
  it with the least frozen history riding along. `--scenario <name>` overrides the choice.
- **The cut** is the turn the issue is **on**: turns `0..K-1` are seeded with their agent
  replies, and what the user said on turn `K` becomes the scenario's opening message, said live.
  A judge names that turn itself — per turn, or in a whole-conversation verdict's findings. `--cut-turn K` overrides the cut.

**The two overrides are independent.** The scenario is chosen from the judge's own turns, and
`--cut-turn K` then moves the cut of that scenario — it never moves the choice to another one. A
`K` outside the chosen conversation is refused (exit 2) with the range that exists; to cut a
different conversation, name it with `--scenario`. Both flags together record exactly what you
named.

**A judge may name no turn, and then the command refuses rather than guessing.** Pass `--cut-turn` with the turn your own diagnosis found the evidence on. When such a
policy failed in several scenarios — counting any `pick` refuses (below) — there is also no seed
length to rank them on, and the command asks for `--scenario` as well: the choice is yours, not
the sort order's. A cut taken from the wrong turn produces a folder that replays a conversation
the issue is not in, and nothing downstream ever says so.

**`replay_caveat: {reason: job_open_at_cut, jobs: [...]}`** means the history before the cut started a
job (a tool result whose `_plurai_runtime` is `mode: job, status: running`) and no reveal of it finished
before the cut. The replay's mock does not have that job. Record the folder anyway, at the same cut: the
run report marks it. On a harness that reports its calls on `agent_state.tool_trail`, the run report reads
only the last pinned turn's trail, so a job opened earlier and still open shows only in this pick's output.

`shape: opener-only` means the issue is on turn 0. There is no history to seed, and the converter
refuses that cut — Step 5 writes the recorded opener instead, and Step 6 does not run. `shape: opener-rooted`
is written the same way at its later cut, and Step 6 and 6b do not run for it either.

**A scenario that was itself resumed** prints `resume_seed_messages` above 0. Its transcript keeps
the conversation it resumed apart from its live turns, and the converter puts it back ahead of
them, so:

- **The cut counts live turns** — the numbering its judges and `user_messages` use. The seed is
  never cut into; it rides whole ahead of every cut.
- **Its seed length counts the resume seed too.** `seed_turns` is the resume seed's user turns
  plus the cut, and that is what the choice ranks on.
- **An issue on its turn 0 is `shape: resumed` with `cut_turn: 0`** — the seed is the history, and
  turn 0's words are the opener. Never record it as opener-only: that drops the conversation the
  turn was answering. `write --first-talk-from` refuses a resumed transcript for that reason.
- **`start_node`** appears only on that turn-0 cut, when the scenario resumed at a named graph
  node. Step 6 passes it on, so the regression resumes exactly where the scenario did.

No shape records a resumed scenario faithfully when:

- its seed and `injected_inputs.resume_seed_present` disagree, or its seed cannot be read;
- its seed restore was reduced — `resume_fidelity` holds anything but the runner's `undetermined`
  reading alone: the harness restored only part of the seed, or the agent rewrote its seeded
  history. A fresh scenario's `resume_fidelity` is not read, since no seed was restored;
- its seed is agent state alone and the issue is on its turn 0.

A candidate refused for either of the first two is dropped from the choice and listed under
`refused` with its reason; Step 8 names each one. `pick` exits 2 when every candidate is refused,
when the refused one is the scenario you named, or when the third applies to the one it chose.

## Step 5 — write the folder

Write the **issue paragraph** first. It is the only statement of why this folder exists, it goes
in before the conversion, and the converter appends below it and never overwrites it.

The paragraph carries **the probe, the quoted evidence, and why it is the agent** — and no code
pointers. A file:line in here is a claim about a version of the customer's source that the fix
about to be applied is already changing.

**The paragraph and the recorded turn decide how the regression is graded.** The eval set's
authoring reads both and names one check that must fail the recorded reply; the run
replays the reply against that check. The replay carries the reply, the
turn's calls and the pinned turns, never a world state, so an owner that reads the world is reported
not measured. The scenario built from the folder
is graded on the target its authoring derives from this paragraph. So the **Probe** line must say
plainly what the agent has to do at the cut. A vague one yields an owner and a target that grade
something other than the issue.

```markdown
## Issue: <the issue title, as it reads in fixes.md>

**Probe.** `<the failed policy or the probe>` — <what the agent was supposed to do, one line>.

**Evidence.** <the quoted turn or tool result from the transcript, and what is wrong with it>.

**Why it is the agent.** <the reasoning that put this issue's home at `agent bug` — the bench
did what it was asked; this is the agent's own conduct>.
```

**With `--finding`, the finding's section is the source.** The title is its heading without the
number; **Probe** comes from its `probe:` line, **Evidence** from `evidence:`, and **Why it is the
agent** from `why it is the agent:` — with every file:line pointer those lines carry left out. The
`write` call then takes `--from-finding "<finding_id>"` as well, in either shape below. It refuses
a slug other than the one the id derives, and it ends the paragraph with two lines — the
`**Finding id:** `<finding_id>`` line, which tells this folder from another finding's whose id
cuts to the same slug, and:

> Recorded at suite build from a confirmed finding; no fix accepted yet.

Nothing machine-reads that second line. It tells whoever opens the folder that the gate confirmed
the issue and nobody has accepted a fix for it; do not write it by hand.

**An `--issue` whose `fixes.md` section names a finding id** records the id too: the `write` call
takes `--finding-id "<finding_id>"`, which checks the slug the same way and ends the paragraph on
the `Finding id:` line alone. Without it, a later finding whose id cuts to the same slug reads this
folder as its own and is never recorded.

Write the paragraph to `$ART/regression_paragraph.md` (it is scratch, under the gitignored artifacts dir),
then create the folder. Every transcript path below is **the `transcript` Step 4 printed**, never one rebuilt from the
scenario name: a later trial's folder shares trial 1's name, so a rebuilt path reads trial 1's conversation. **Resumed shape:**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" write "$WORKDIR/regressions" \
  --slug "<issue_slug>" \
  --roles-file "$ART/regression_paragraph.md" \
  --issue-from "<the transcript Step 4 printed>" --cut-turn <K>
```

**Opener-only shape** (Step 4 said `opener-only`) — the same call, plus the transcript the opener
is lifted from:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" write "$WORKDIR/regressions" \
  --slug "<issue_slug>" \
  --roles-file "$ART/regression_paragraph.md" \
  --first-talk-from "<the transcript Step 4 printed>" \
  --issue-from "<the transcript Step 4 printed>" --cut-turn 0
```

**Opener-rooted shape** (Step 4 said `opener-rooted`) — the opener-only call with the pick's `--cut-turn <K>`, plus
`--trigger "<the finding's trigger: line, or the user move that exposed the issue>"`: the replay reaches the issue
only when its scenario makes that move, so `write` refuses this shape without it.

`issue_turn.json` is the turn at `--cut-turn`, kept verbatim. It refuses a cut the transcript
records no turn at, a cut the **Probe** policy's recorded verdict does not name when it names any
(cut at the turn it names — never the one the paragraph's prose points at), a turn with no reply text, a slug that would not survive slugification, an
empty paragraph, and a folder that already exists — that last one means the issue is already covered, so go back to Step 3 rather
than deleting anything. It refuses the opener-only shape when the run handed the agent a document
with the opener (a `.document_fields` input): the folder carries the opener's text only, so record
nothing for that issue rather than an opener that names a document the replay does not hand over.

**On a Claude Code CLI harness the resumed shape also carries the CLI's own session**
(`cli_session/`), which the harness resumes at the cut. `write` refuses such a cut with a named
reason, and nothing is written: `workspace_written_before_cut` — the run wrote files before the
cut, and a replay starts in a fresh scenario folder without them, so record at an earlier turn or
from another scenario; `no_cli_session` — the harness recorded no session for that turn, so route
it to `/simulation-setup-python`.

## Step 6 — convert the conversation into the folder (resumed shape only)

**Skip this whole step for the opener-only and opener-rooted shapes.** There is nothing to convert.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
plurai convert-trace \
  --export "<the transcript Step 4 printed>" \
  --source-format plurai-run \
  --out-dir "$WORKDIR/regressions" \
  --into "regression_<issue_slug>" \
  --cut-turn <K> \
  --workspace-root "$WORKDIR"
```

When Step 4 printed a `start_node`, add `--start-node "<start_node>"` to that call.

`--into` is what keeps the paragraph first: the folder's `ROLES.md` is **appended to**, and the
hand-authored description is never overwritten. The converter writes `resume_pin.json` and
`transcript.md` beside it and prints a JSON summary naming the message count and the cut.

**A non-zero exit from the converter means nothing was written: the folder holds only `ROLES.md` and `issue_turn.json`, and it
must not survive.** Delete it — `rm -r "$WORKDIR/regressions/regression_<issue_slug>"` — then read why from the
converter's stdout summary: `skipped` names the transcript and `gate_findings` carries its finding (no
summary means the invocation itself was refused — a flag, or an SDK too old to report per-thread skips:
upgrade the SDK in the client's venv). Decide on the finding per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`,
tell the user the conversation could not be recorded (never the finding), and stop; record again once
the cause is resolved. The backend does
not refuse a folder with neither its pin nor its opener: the next suite build authors a scenario
for it cold from `ROLES.md`, under the regression's name, so the set carries a scenario that
reproduces nothing while every name check reads the issue as covered. `regressions.py list` and
`overview` refuse such a folder (exit 2), and that refusal is the last line of defence, not the
procedure: nothing in this command may leave a folder without its `resume_pin.json` or its
`first_talk.txt`.

Then check the pin the way the backend will, before it is ever uploaded — the folder this record
wrote, and only that one:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/skills/trace-to-scenarios/scripts/check_resume_pins.py" "$WORKDIR/regressions/regression_<issue_slug>"
```

Exit 0 is clean, **2 means the backend will refuse this folder** — delete it
(`rm -r "$WORKDIR/regressions/regression_<issue_slug>"`), say why, and record from a different
scenario. Never delete a folder this record did not write: Step 5 refuses an existing folder, so
the one checked here is always this record's own, and an older folder the checker refuses is
reported by `regressions.py list` and the suite build, never removed here. Do not edit
a pin: it is the record of what the conversation was.

## Step 6b — keep the trace scenario's world facts (resumed shape only)

**Skip this step for the opener-only and opener-rooted shapes.** Run it after Step 6 has converted the folder and
the pin check passed — never before the conversion.

A **trace scenario**'s `ROLES.md` ends with the **world facts**: the tool calls and results its
production trace recorded after the cut, under the heading `World the live leg needs`. The
converter writes none for a run (`plurai-run`), because a run's tool results came from the mock
and would describe the simulated world. So when the scenario you recorded from was a trace
scenario, the regression takes the section from the trace scenario's own folder. First, find
out whether it was one:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" source-of "<the transcript Step 4 printed>" \
  --trace-dir "$WORKDIR/trace_scenarios"
```

It matches the transcript's scenario name to a folder in `trace_scenarios/`, by exact name, and
reads the transcript's `resume_seed` by the same rule Step 4's `pick` applied.

- **`"trace_scenario": null`** — a walk scenario, a regression, or a run without a seed. There
  is nothing to carry; go to Step 7. When it also printed a `WARNING` (the scenario resumed under
  a `trace_` name whose folder is gone), carry that line into the report.
- **Exit 2 (`REFUSED`)** — the transcript is one `pick` refuses, so it changed after Step 4 read
  it. Delete the folder Step 5 wrote, rewrite the overview (Step 7's `regressions.py overview`) so
  `BUNDLE.md` stops describing it, report the refusal in its own words, and record again from
  Step 4.
- **`"trace_scenario": "trace_<failure_slug>"`** — carry the section, from the `roles` path it
  printed:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" carry-world-facts \
  --from "<the roles path source-of printed>" \
  --into "$WORKDIR/regressions/regression_<issue_slug>"
```

It copies the section verbatim, from its heading line to the end of the trace scenario's
`ROLES.md`, and appends it as the LAST section of the regression's `ROLES.md`, with one line
added under the heading: the section's turn numbers are the production trace's, not the
regression's, which differ once the regression is cut past the seed. **Heading depth:**
the section takes the depth of the regression's own `Provenance` heading, because the converter
writes world facts as that heading's sibling — `###` inside the `--into` merge Step 6 made, `##`
in a standalone conversion. A section from a folder of the other depth has its headings moved to
match; text inside fences is never touched. It writes nothing outside the regression folder.

- **Exit 0 with the file path** — appended.
- **Exit 0 with `already carries`** — the section is there from an earlier run of this step;
  nothing was appended twice.
- **Exit 0 with a `WARNING`** — the trace scenario folder is gone or holds no section. The
  regression is recorded without world facts; carry the warning into the report. **Never delete
  the folder or refuse the record for this.**
- **Exit 2** — the regression folder has no converter resume section, so this ran before the
  conversion or on the wrong folder: run Step 6 first; do not append by hand. When the refusal
  names a code fence that never closes, the issue paragraph you wrote opens a fence it does not
  close: close it in `ROLES.md` and run this command again.

## Step 6c — measure how often the issue recurs at the cut (both shapes)

Run it after Step 6b, or after Step 5 for the opener-only shape. The folder holds one reply the agent
gave once; this re-runs the agent at the cut 5 times and scores each reply against the folder's
**Probe** policy, so a run report can tell a reliable pin from one that rarely recurs there. Each
sample is one agent turn on the local mock, and the paragraph, the pin and the five replies go to the
backend inline, as `score_run`'s transcript does.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
F="$WORKDIR/regressions/regression_<issue_slug>"; P="$ART/regression_picks/regression_<issue_slug>"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" "$WORKDIR/simulate.py" \
  --sample-cut "$F" --samples 5 --from-transcript "<the transcript Step 4 printed>" --samples-out "$P.samples.json"
echo "SAMPLE_EXIT=$?"
```

- **`SAMPLE_EXIT` not 0, or no `.eval_set_id`** — nothing can be scored: keep the folder as seen once,
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" confirm "$F" --seen-once <sample_cut_no_verdict | no_eval_set>`.
- **`$P.samples.json` has a `seen_once_reason`, or no `valid` sample** — `regressions.py confirm "$F" --samples "$P.samples.json"`.
  The reason is typed: a CLI session, a cut turn that called a tool, a non-Python harness, a bench whose
  tools are intercepted at the agent's host (`agent_host_route`), or an SDK without the replay rates — none
  of them is sampled; or a harness whose Init refused the pin's seed on
  every sample (`harness_cannot_resume`), or reported it dropped part of it (`harness_dropped_resume_state`),
  so no reply was drawn.
- **Otherwise score them:**

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  F="$WORKDIR/regressions/regression_<issue_slug>"; P="$ART/regression_picks/regression_<issue_slug>"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" sample-payload "$F" "$P.samples.json" \
    --eval-set-id "$(cat "$WORKDIR/.eval_set_id")" > "$P.payload.json" \
  && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" score_sampled_replies "@$P.payload.json" > "$P.scored.json" \
  && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" confirm "$F" --samples "$P.samples.json" \
       --scored "$P.scored.json" --picks-dir "$ART/regression_picks"
  echo "CONFIRM_EXIT=$?"
  ```

  A refusal before `confirm` (the paragraph names no probe policy, the backend refuses the owner or
  lacks the tool) keeps the folder as seen once: `confirm "$F" --seen-once <no_probe_policy | scoring_refused>`.

`confirm` classifies on k caught of n measured replies. **Exit 0** writes `replays.json` into the folder,
local only — never uploaded — with the band: `reliable` (4 or 5 of 5), `intermittent` (some), or
`seen_once` (fewer than 5 measured, none caught; or nothing sampled). **Exit 3 — rare at the cut:**
caught in none of 5, so the folder does not pin the issue at that turn. `confirm` has already deleted
the folder and rewritten `BUNDLE.md`, and its record (`$P.confirm.json`) names the other turns of the
source run where the probe failed; the run report tracks the issue by its check. Record nothing
more for this issue: skip Step 7, say in Step 8 that it was not pinned, naming those turns as re-cut
candidates — never re-cut automatically — and, inside the suite build, hand its finding id back as
`--rare`. `confirm` deletes only the folder this record just wrote: a folder that already holds
`replays.json`, is tracked in git, or has no pick from this record is refused (exit 2) and kept. Exit 2
is a refusal: report it in its own words; when the folder is the one this record wrote and holds no
`replays.json`, keep it as seen once.

## Step 7 — rewrite the overview, stage, log

The converter writes its own `BUNDLE.md` describing converted traces, which is blind to every
opener-only folder beside them. The suite's stage writes its own merged overview for the upload,
so this file never reaches the backend; it is the directory's description for whoever opens it,
and a stale one misdescribes the folders on disk. Rewrite it from what is actually on disk,
**after** the conversion:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" overview "$WORKDIR/regressions"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" regressions
```

**The regressions directory is tracked on purpose.** Every other staging directory is inside the
managed ignore block; this one is not, because the folder IS the record. There is no ledger and
no provenance file — deleting a folder retires its regression, and git history keeps it.

Then log the entry, so the loop's own memory carries what was recorded:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --kind regression \
  --finding "recorded regression_<issue_slug> from <scenario> cut at turn <K> — <the issue title>"
```

## Step 8 — report

One short paragraph. What was recorded, from where, and what happens to it next:

> Recorded **`regression_refund_timing_invented`** — the refund-timing issue, replayed from
> `scenario_02` cut at turn 1. It joins the scenario set at the next `/simulation-generate-suite`,
> as one extra scenario in the final batch. Until then `--verify` checks the fix through the
> original scenario.

With `--finding`, the next step is the same final batch, and it is in this very build: say
`It joins the scenario set in this build's final batch.` instead.

Add the band from Step 6c in plain words — `caught again in 4 of 5 replays at that turn`, or
`seen once`. Name any warning that came out of Step 5, Step 6 or Step 6b in a second line, and when Step 6b
carried world facts, say from which trace scenario. When Step 4 refused any scenario, add one line
naming each and why, so a conversation the issue is in is never dropped without a word. That
covers the `refused` map of the `pick` output you recorded from, and every scenario named in an
earlier refusal that led you to re-run `pick` with `--scenario`: after the whole-conversation
refusal, the final output's `refused` is `{}`. Then hand back to whatever invoked this —
`/simulation-fixes` still has the fix itself to apply, and decides whether that trace scenario
retires; the suite build goes on to its next finding, then stages the directory.

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

## Notes

- **The folder is written before the fix is applied.** The evidence has to be the agent as it
  was, and an edit to the customer's source between the run and the record does not change the
  transcript — but the paragraph is written from a reading of that source, so writing it first
  keeps the reading honest.
- **Regressions stay after the fix.** They are not cleaned up when the issue closes; the point is
  the relapse, which arrives later.
- **An accepted bench fix puts every active regression under re-verify.** `/simulation-fixes`
  owns that rule: a seed frozen from a bench that turned out wrong may carry its mistake, and the
  next clean run decides which folders survive.
- **A regression that cannot be authored at build time is a reported blocker**, never a silent
  gap. The backend ships the walk and lists the folder under `bundle_folders_attempted`;
  `/simulation-generate-suite` surfaces that in the suite report rather than claiming coverage.
