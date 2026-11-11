# Polling a backend job — canonical source

Every skill that polls a long backend job (`generate_scenarios_start`, `generate_evals_start`,
`generate_env_start`, `refine_scenarios_start`, …) carries a copy of the
recipe below. The inline copy is what binds — Claude Code loads only the invoked SKILL.md and
there is no include mechanism — so THIS file is the single place to edit it.

Cite this file from a skill as `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`, never as a bare
`docs/…` path — the reader's cwd is the CUSTOMER's repo, where that path does not exist.
The plugin's development repository checks every such reference.

## The recipe

**One blocking call per wait — `scripts/poll_job.sh`.** The sleep lives INSIDE the script and runs
in the foreground, so one invocation IS one wait and it returns only when the job is terminal (or
the budget runs out):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id>        # default budget 540s; call it with a 600000 ms tool timeout
bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id> 300    # or bound it yourself
```

It renders one line per status/phase change, quoting the SERVER's `elapsed_seconds`. Exit codes:

| code | meaning | what you do |
|---|---|---|
| 0 | job `done` | call `job_status(id=<job_id>)` ONCE for the full `detail` envelope, then render |
| 13 | job `failed`, or its status could not be read for credit | a line `could not read the status of job <id> (credit): <sentence>` is the error posture's credit refusal: the gateway refused the read, the job's state is unknown and it may have finished, so never say it failed. Otherwise read the envelope's `terminal_reason` first: `terminal_reason: credit` is the same refusal (stop, never retry), and its line is `failed — job <id> (credit): <sentence>`; otherwise classify the printed `error` yourself per the error posture |
| 15 | **the id is unknown to the backend** (the backend serves that envelope as a 404) | re-check / re-launch it — the backend is fine |
| 14 | budget spent, backend healthy (`/healthz` 2xx) — **slow, not wedged** | re-invoke to keep waiting |
| 12 | no blocking wait here: `GET /jobs/{id}` missing (a 404/405 with **no job envelope** in the body), or no `plurai` runner with `job wait` installed | use the MCP fallback below |
| 77 | bearer rejected or none configured | check `PLURAI_TOKEN` against this backend |
| 69 | **job state unknown** — the box is not responding or down/unhealthy (`/healthz` non-2xx), OR it is UP and the job route is faulting | the script says which: a wedged/down box will not resolve by waiting; a faulting route is a backend-side fault, not a slow job. The script prints the move: one `wait.sh 45 --quiet`, then re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`) |
| 7 | Plurai refuses this version of the plugin or its SDK | the script printed the update text, ending with the one command that updates the plugin: say it as printed and stop; never retry |
| 65 | refused before any read: the wrong-backend guard (`SPLIT BACKENDS`) or the client pin | stderr names which; fix what it names |

Under a live `/simulate` plan the default budget is 240 s, and each wait ends by refreshing the local overview page with the job's phase; it prints nothing for you and never publishes (the page is published once, at the run's end).

A single invocation is clamped to 540 s of **wall clock** (not a sum of sleeps — the HTTP reads
are real time too), under the harness's 600000 ms tool ceiling; a longer requested wait is refused.

**Never pipe it, never capture it.** `poll_job.sh` communicates through its EXIT CODE — that is
the whole table above — and a shell pipeline reports the exit status of the LAST command in it.
`bash poll_job.sh <id> | tail -5` therefore exits 0 whatever the job did, so a `failed` job (13),
a spent budget (14) and a missing route (12) all read as `done`. Command substitution has the same
effect. Invoke it bare, let its lines land in the tool output, and branch on the code.

**Give the Bash call `timeout: 600000`.** Every invocation, in every skill. The harness's DEFAULT
tool timeout is 120 000 ms, so a bare call is killed at 120 s — 420 s inside the script's own
budget, and a harness kill is indistinguishable from a wedged backend: you lose the exit code that
tells you whether to re-invoke (14), re-launch (13) or stop (69). 600000 also covers the worst case,
which is the 540 s budget plus one in-flight read (20 s), one healthz probe (5 s), the runner lookup
and one status read after the wait (at most 10 s).

**Poll in the FOREGROUND, and keep the turn open until the job is terminal.** Never put `run_in_background` on `poll_job.sh` or `wait.sh`, and never end a turn on "I'll check back" while the job is `queued` or `running`: a headless `claude -p` session exits when the turn ends, the background poll dies with it, and the user gets no result. On exit 14 re-invoke in the same turn, until the job is `done` or `failed`; on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`). The one exception is an interactive `/simulation-generate-suite`, which waits in the background and is re-invoked by the task notification; a headless session never does.

**MCP `job_status` stays** — for the FINAL render once the job is terminal (it carries the `detail`
envelope the REST route need not), and as the whole loop on the fallback path.

### Fallback — backends predating `GET /jobs/{id}` (exit 12)

Older images have no REST job route, so the wait and the read must be separate steps: `job_status`
is an MCP call and must come from the main loop, and a shell-only wait cannot make it.

```
# 1. wait — FOREGROUND. Its sleep is internal, so the harness's block on a bare `sleep`
#    command does not apply, and the tool timeout bounds it a second time.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110
# 2. when it completes, read the job through MCP and render
job_status(id=<job_id>)   # → render; TERMINAL STATUSES ARE `done` AND `failed`, nothing else;
                          #   on any other status, wait again
```

**`wait.sh` takes a DURATION and nothing else. Never write your own wait loop.** A loop whose exit
predicate is a job STATUS rather than a clock does not exit when the status set is wrong — the backend
reports success as `done`, so a loop watching for `complete`/`completed`/`success` runs forever,
and backgrounded it is bounded by nothing. Waiting ON something is `poll_job.sh`'s job; it owns the
terminal set.

**On this path the waits MUST be SERIAL** — and that is exactly what the script exists to
guarantee. A BACKGROUNDED wait returns to the model immediately, so the wait and the poll
overlap: reads land seconds apart after "waits" that add up to minutes, and a run can burn dozens of
polls and tool calls on pure overhead. Wall-clock time appears to stand still while the durations
add up.

## Why this shape

- **Overlap is a race, not a style choice.** The only way to stop the wait and the read from
  overlapping is to put the sleep somewhere that genuinely blocks. Inside a script it does; the
  harness blocks a bare foreground `sleep` as a Bash *command*, and `bash poll_job.sh …` is not
  that command.
- **Down and slow must be distinguishable.** On timeout the script probes `/healthz` and says
  which one it is, because a wedged box looks exactly like a slow job. (Only a 2xx counts as up. A 401, 404 or 5xx proves that something answered — an auth
  layer, another service on the port, a proxy with no upstream — not that the backend is healthy,
  so the script reports it as down/unhealthy and names the code; "no response at all" is down.)
- **The cadence that matters is the RENDERED one, and it is MINUTES.** These jobs run 5-40
  minutes. The script's own 15 s HTTP reads are free — they cost no tool calls and no context —
  but it prints only on a status/phase CHANGE, which is the update the user actually needs. Never
  translate "poll faster" into more tool calls.
- **NEVER compute elapsed time yourself.** You have no clock: any elapsed figure you derive is a
  sum of the waits you INTENDED, which is fiction if they overlapped or never happened. Quote
  `elapsed_seconds` / `seconds_since_progress` off the envelope — the script already does.
- **Escalate on STALL, not on duration.** Read `seconds_since_progress` (or the phase/counter
  fields), never `elapsed_seconds` alone: a long job making steady progress is healthy, and a
  short job whose counters have not moved is not. The script flags the stall for you, **against
  the threshold that phase earns**: 30 min for env-gen's `generating`/`verifying` (both are
  silent for the whole of a tool call and of the contract pytest), no stall signal at all during
  the scenario walk's `evolving` (judge that one by `elapsed_seconds`), 15 min everywhere else.
  A large counter under a phase whose threshold has not been crossed, with no NOTE printed, is a
  HEALTHY job — do not escalate it.

## Suspending a turn — the pending contract (canonical source)

While a flow window is open, a turn may end in exactly four states:

1. it **lands** the flow (the command's hand-off anchor, or the failure hand-off);
2. it ends **on the user** — a sanctioned blocker ask, whose reply is the resume;
3. the **harness holds work that will re-invoke the session**: a `run_in_background=true`
   Bash task (`run_wait.sh` on a run, or `poll_job.sh` in an interactive `/simulation-generate-suite`), a spawned subagent (the critic),
   or an armed `Monitor` watch. On Copilot the background command is a `bash` call in `mode: "async"`, and the
   harness injects a notification prompt when it exits. **Codex does not start a turn when a background command
   finishes** (nor, as far as is known, when a subagent reports), so it has no such state: there the wait is a
   foreground poll inside the turn (keep the command, or its `write_stdin` polls, going until it exits;
   `poll_job.sh` stays a foreground command, re-run on exit 14, on exit `69` wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`)), and a turn end with the job still running is
   blocked with that remedy rather than a prompt to background it.
4. the **harness moved an MCP call to the background** because it outran its window (a long
   `interact_edit_session` turn): the session is re-invoked by the task notification the
   harness queues when that task reaches a terminal status.

Any other turn end stalls the flow — nothing resumes it and it sits idle until a human
notices (observed live: a completed run unprocessed for 14 minutes; the next seam stranded
the same flow again). `report_guard` (Stop hook) corrects it with one line, and the report
section of the guarded skills names the four states. Note this is a STATE test, not a phrasing test: rewording a stall does not
get past it, and rendering the honest in-progress line is never what triggers it.

A detached `nohup … &` does not satisfy (3): the harness never hears from it again, so a
turn ending on one strands the flow exactly as if nothing had been launched.

**A run is not a tracked task; its wait is.** Every harness bounds the work it tracks (a background
command's time limit, a headless session's wait ceiling, a sub-agent's life), and a run can outlast all of
them. So `run_start.py` starts the run in a process of its own, tied to the harness process (it stops
within seconds when the session ends), and returns at once; the tracked work is `run_wait.sh <run-dir>`, a
read-only poll of at most 540 s, re-armed on exit 2. Re-arming it after any stop is always correct: it
starts nothing. The same holds for `poll_job.sh` on a backend job, which the backend owns.

**You do not register anything by hand.** `hooks/pending_register.py` records the work when it
observes the `run_in_background` Bash call, the `Task` spawn or the `Monitor` watch (PostToolUse)
— on Copilot, a `bash` call in `mode: "async"`, ended by the `read_bash` result whose last line reports the
command finished, or by the `<system_notification>` prompt Copilot injects when it exits (an async call that names
no shell id registers nothing); on a harness that does not wake the model (Codex) nothing is registered, for a
background shell or an MCP call alike —
and also when the harness itself moves a call to the background, which shows up only as the
handle in the tool RESPONSE (a structured key for a Bash call, the harness's own first line for
an MCP one). A `*_start` job envelope that merely reports `running` is NOT that: the harness
holds nothing for it, so the flow resumes it by polling, in a turn of its own. `report_guard`
READS that record: it is a record of work, not a one-shot token, so it keeps backing turn ends
for as long as the work can be running. It is cleared when the flow lands or closes, when the
next flow opens, at the task notification that reports the work finished, and — as a staleness
backstop sized to OUTLIVE the work, never to approximate it — once it is older than
`report_guard`'s `PENDING_TTL_SECONDS` (`MCP_PENDING_TTL_SECONDS` for a backgrounded MCP call,
whose only release signal is a notification a compacted session may never see).

A spawned SUBAGENT gets a second, stronger record: `SubagentStart` adds the agent's key to a
live-holder set that `SubagentStop` removes it from — a set keyed by agent id, not a count, so
one key means one entry however often the start arrives. A launch-time record cannot carry a wait the
launcher never re-announces — a critic runs across several turns and `/analyze-simulation`
requires a brand-new one each pass, so nothing re-registers it — while a lease lasts exactly as
long as its holder.

Making both an observation rather than a written claim is deliberate: an earlier cut had the
skills write the marker, and a model could then satisfy the gate by writing a file and doing no
work — the model-cooperative link `hooks/flow_arm.py` exists to remove.

## Dropped turns — the heartbeat

The contract above covers every way a turn can END. It cannot cover a turn that DIES: when a turn
ends on an API error the harness fires `StopFailure`, not `Stop`, so `report_guard` never runs and
the flow is left with nothing scheduled to re-invoke it. Observed live: a suite build sat idle for
4 h 39 min after a mid-response connection drop, one landing round from done.

Two hooks close that path, and neither is a timer you have to reason about:

- `hooks/flow_interrupt.py` (`StopFailure`) writes `$FLOWDIR/.flow_dropped` when a turn of an
  ARMED flow died on an error a later attempt can get past.
- `hooks/flow_heartbeat.py` (`UserPromptSubmit`) gates the cron the flow schedules at Step 0. A
  firing reaches the model **only** when `.flow_dropped` is present; every other firing is blocked
  before any API call, and the record is consumed by the firing it releases.

**Which errors count.** `server_error` and `overloaded` are recoverable; `rate_limit`,
`authentication_failed`, `oauth_org_not_allowed`, `account_on_hold`, `billing_error`,
`invalid_request`, `model_not_found` and `max_output_tokens` are not. `rate_limit` is the
surprising one and it is deliberate: the class covers both minutes-scale throttling and hard
usage caps, and it is overwhelmingly the latter, so
rescuing it would make the commonest error a self-sustaining retry loop that nothing can bound.
Any class in NEITHER list — `unknown`, or one added after this was written — is treated as a
possible transport drop and decided by the transcript, never silently refused: an `unknown` error
is typically a socket/connection failure, which is exactly what this exists to rescue.

**Auto permission mode.** Auto mode denies session-cron creation, so under it the heartbeat
does not exist — and the call is never attempted. `hooks/cron_gate.py` (PreToolUse on
`Bash|CronCreate`) stamps `$FLOWDIR/.flow_heartbeat_unavailable` when it sees the arm's own Bash
call announced: PreToolUse runs before that command, so the script reads the sentinel moments
later and prints a status line instead of the `CronCreate` instruction. Nothing is called and
nothing is denied. The `CronCreate` branch stays as the backstop for a cron attempted anyway. The flow runs
normally without dropped-turn rescue. To restore it under auto mode, export
`PLURAI_FLOW_HEARTBEAT=force` and add `CronCreate` to `permissions.allow` (a narrow non-shell
allow rule resolves before the classifier).

**Why the predicate is the DROP and not "is anything in flight".** A pending-work test would block
the rescue in exactly the case that matters — the turn the harness starts when a background task
finishes, where that task's register is still standing — and it would need a staleness bound wrong
in both directions (too short false-fires on a 40-minute run; the 4 h register TTL strands the
flow). Keying on the drop needs no bound at all: an ordinary in-progress wait, a question waiting
on the user, a landed hand-off and a deliberate interrupt all leave no record.

**Why this is affordable.** A blocked firing pays a few milliseconds of Python and one line of
stderr, and a model turn is paid only on a real drop. A timer that REACHED THE MODEL on every
firing would pay a model turn on the whole build context each time to learn nothing. The rule from
*Why this shape* still holds: never translate "poll faster" into more tool calls.

**A firing can never interrupt work.** The scheduler enqueues at low priority and a scheduled
prompt fires between turns, never mid-response; if Claude is busy when one comes due it waits for
the current turn to end, and there is no catch-up for fires missed while busy. That is what makes
a 7-minute heartbeat safe to leave running underneath a three-hour build.

**You do not create the record, and you do not delete the cron.** `flow-window.sh arm` prints the
`CronCreate` line when it stamps a window, once per SESSION (a driven sub-step prints nothing unless the session was resumed, since its cron died with the process, and
neither does a later command in the same session — several identical jobs would just mean several
blocked firings a period). The record is written and consumed by the hooks and swept wherever the
other flow markers are swept, including by `flow_arm` when a new window is stamped and by
`report_guard` on any Stop — a completed turn is proof the drop was recovered, whether by the
heartbeat or by a human typing "continue". The job itself is never deleted: a firing that outlives
its flow finds no window and is blocked like any other. It is session-scoped in the strongest
sense: the arm instruction does not pass `durable`, which defaults to false — the job lives in
the in-memory session store and dies when this Claude session exits. Nothing is written to
`.claude/scheduled_tasks.json`, so a fresh conversation starts with none and no restart carries
one forward. (The scheduler's seven-day auto-expiry is a ceiling for jobs that live that long; a
session-only one never reaches it.)

## What NOT to write

- `sleep 5` in a foreground shell command — blocked by the harness.
- **Any wait loop you wrote yourself**, backgrounded or not: `until [ "$s" = complete ]`,
  `while ! done; do …`, or anything else whose exit predicate is a job STATUS. The terminal
  statuses are `done` and `failed`; a loop watching for any other spelling never exits, and
  backgrounded it is bounded by nothing. Use `poll_job.sh <job_id>` to wait on a job and
  `wait.sh <seconds>` to wait on the clock — those two cover every case.
- Piping or capturing `poll_job.sh` (`| tail`, `> log`, `$(…)`). Its exit code IS the result and
  a pipeline reports the LAST command's, so `poll_job.sh … | tail -5` exits 0 on a FAILED job.
  Call it bare and read the exit code.
- `nohup <long job> &` for work the flow must resume on — detached means never re-invoked.
- A hand-rolled poll loop when `poll_job.sh` is available. It exists so the wait cannot overlap
  the read; re-deriving the loop inline re-introduces the race.
- "Poll every ~5 seconds" — unachievable as a tool-call cadence, and the two rules then contradict
  each other. Faced with a choice between the letter of a rule and its purpose, a reader picks
  one, and you no longer know which.
- Chained short sleeps to work around the block.
