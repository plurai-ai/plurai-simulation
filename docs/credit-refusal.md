# Credit refusal — what each carrier prints

A credit refusal is the backend declining work because of the organization's credit standing, not because of the
work. Nothing a skill does changes it, so the flow stops and says one sentence. The error posture block names the two
shapes; this file says where each arrives and what the sentence is.

## One line from a plugin script

A script that meets a credit stop prints one line, `CREDIT-STOP: <sentence>`, and nothing else about the refusal.
The sentence is finished: pass the line, the sentence, or the runner line that ends with it, as
`stop_message.py refusal --sentence "<text>"` with the continue command and the outcomes done so far, and say what that
prints, verbatim and alone. The script reads the kind, the reset time and the billing link back from the sentence table and
prints what `--refusal` prints for them, never the rest of the text; text that ends in no table sentence, such as a 402's
`detail`, exits 3: pass the refusal's JSON with `--refusal`, and take the `contact` hand-off only when that exits 3 too. The
output is the sentence, "Run `<the user's command>` to continue from where it stopped." (`Say
"<their request>" again` in place of `Run` when the user asked in words; the report guard accepts either as the flow's
hand-off), then the done lines. A continue command that names a skill the user cannot type exits 5.

| Carrier | When it prints the line |
|---|---|
| `mcp_call.py` (exit 8, on stderr) | the gateway answered the handshake or the call with a 402 naming a credit kind |
| `assert-backend-identity.sh` (returns 1, on stderr) | `plurai backend identity` exited 13 with the kind's sentence; the guard arms nothing and makes no second request |
| `grounding_mirror.py pull` (stdout, exit 0) | `plurai fetch artifact` exited 13 with the kind's sentence; the line replaces the `MIRROR=` line |

`poll_job.sh` prints no such line. `plurai job wait` ends a credit stop on exit 13 with a line that ends
`(credit): <sentence>`: `could not read the status of job <id> (credit): <sentence>` for a credit 402 (the job's state
is unknown and it may have finished, so never say it failed, and the script logs no outcome for it), and
`failed — job <id> (credit): <sentence>` for a job that ended with `terminal_reason: credit`. The script passes the
line through, and that sentence goes to `--sentence` like any other. The runner's sentence for a `plurai` command is the same table the plugin renders, so the user reads the same
words from either.

## A credit block inside a payload

A job envelope with `terminal_reason: credit` (its `credit` block beside it, any status) and a tool error that carries
`{"code": <kind>, "resets_at"?}` are rendered by `stop_message.py refusal --refusal`, which also prints the continue command and
what is done so far. The five kinds are `insufficient-credit`, `new-run-refused`, `free-tier-ceiling`,
`credit-unavailable` and `credit-refused`; a block naming none reads as `credit-refused`. `credit-unavailable` is the contact
hand-off alone, with no continue command whatever `--continue` holds, but it halts as the liftable `capacity` kind, so the
user's next message runs the stopped step again; the other four require `--continue`. Pass `--plurai "$PLURAI_BIN"`
when you hold the resolved runner: its `capabilities` names the billing page. Without it the script finds the page of the
session's backend itself, so a stop with no runner still names it. With no page at all, the sentence says "the Plurai
dashboard". A run that ended on exit 20 with `aborted.reason: credit` is rendered by `stop_message.py refusal
--run-summary <its run_summary.json>` (`simulation-run`, Step 4): the summary's `aborted` block picks the sentence, and
the runner's own count of finished scenarios is the first done line.

## A payment refusal with no block

When a tool reports only that a call was refused for payment and shows no block, run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" list_artifacts '{}'`. If the gateway is the one refusing, it prints a
`CREDIT-STOP:` line (exit 8); any other result is not a credit refusal.

## After the stop

The stop is said once. A task that finishes later (a background shell of the stopped run, a poll) gets at most one
line, about what it brought that is new: never the stop again, its cause, the billing link or the continue command.
Say nothing about who is using the organization's runs beyond what the stop's own sentence says. The runs in use can be
the stopped run's own, so never call them "other running work". What the stop left undone is never a list of its own
and never another command or skill: most of this plugin's skills are steps the user cannot type, and the continue
command picks the flow up where it stopped. When the user asks about the stop, answer their question; this rule covers
the turns they did not start.

## Exit codes

A `plurai` command ends a credit stop on `13` (`fetch`, `backend identity`, `job wait`, `job status`) or `20` (a run:
`NOT_MEASURED`, with `aborted.reason: credit` in `run_summary.json`). In `fetch` and `backend identity` the code has no
other meaning. A snippet's own fallback for a failed read (`FETCH-FAILED`, `MISSING:`, `continuing unverified`) never
applies to it: stop, and do not read the same data through another route.
