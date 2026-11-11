---
name: optimize-eval
description: >-
  Improve one generated llm_as_judge policy: the backend rewrites its rubric or decomposes it into a hybrid judge and benchmarks the candidates; adopt one only on your explicit confirmation. Supports --revert. Run after /generate-evals.
---

# Optimize Evals (rubric rewrite, or llm_as_judge → hybrid)

`llm_as_judge` policies grade by asking a model to read the whole conversation. This skill asks
the backend to improve **one** of them, either by rewriting its rubric or by decomposing it into a
`hybrid` judge: regex/string extraction plus a few small-model structured sub-calls that reproduce
the judge's behavior with mostly mechanical checks. It is **opt-in and per-policy**: nothing changes
until you confirm adoption. The comparison you show is judge quality only.

The backend first picks a method for this policy: **rewrite its rubric** (meaning-based
checks — the judge stays an LLM judge) or **decompose it** into a hybrid (mechanical checks). Tell
the user which one they are getting (Step 4).

This is a thin orchestrator. The ground truth, code-gen, and benchmarking all run on
the backend; you trigger, poll, present the tradeoff, and (on confirmation) adopt.

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

## Step 1: Preconditions

Read `plurai_sim/.eval_set_id` (written by `/generate-evals`). If missing, tell
the user the evals are not built yet (`/simulation-generate-suite` builds them) and stop.

Read `plurai_sim/policies_config.yaml`. List the policies whose `type` is
`llm_as_judge` — these are the only optimizable candidates. If there are none, tell
the user there's nothing to optimize and stop. **Do not** chain this from
`/generate-evals`; it is always a deliberate, separate run.

If invoked as `/optimize-eval --revert`, skip to **Revert** below.

## Step 2: Pick the target + budget

If exactly one `llm_as_judge` policy exists, confirm it with the user. Otherwise
list them and ask which to optimize.

Surface sensible defaults the user can just accept:
- iteration budget (`max_iterations`, default backend value),
- synthetic benchmark only (Step 2b): minimum acceptable quality
  (`quality_floor`, e.g. 0.85 — agreement with the strong-model gold) and optional
  `num_samples`.

## Step 2b: Gold

Omit `labelled_gold`: the backend benchmarks on this eval's stored labels when there are
enough, else on synthetic gold — read which from `detail.quality_metric`.

## Step 3: Start the run

**`ledger_version` — the facts the benchmark and the judge code are generated against.** The backend requires the id of a
SETTLED version of the agent's grounding ledger (`ledger_version`, with `workspace_id`) and reads the tool, entity and
agent facts of that version, not the ones the set was generated from. Send the pin the suite is on now. Read it; never
read the mirror to choose one:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" pinned --workdir "$WORKDIR"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_mirror.py" waived --workdir "$WORKDIR"
```

`WAIVED until <date>` printed: the operator waived grounding for this workspace, and the backend runs a waived
workspace ungrounded only on a call that carries no `ledger_version`. Send the call with `ledger_version` omitted (the one case where the argument is left out, bar an undelivered env below) and skip the steps that read a job: `pinned` prints nothing here, and neither
a `.ledger_version` left on disk nor a job recorded for another workspace is a pin. Read only `$WORKDIR`'s own
markers: a job or pin recorded above `envs/` belongs to the bench's own workspace, which this env's call does not
name.

An id printed is `ledger_version`, exactly as printed. Nothing printed: read the job the last delivery recorded, with
`cat "$WORKDIR/.grounding_job" 2>/dev/null || echo "NO_GROUNDING_JOB"`, then `job_status(id=<the job id>)`; `status: done` with
`detail.settled: true` → the pin is `detail.ledger_version`; still running → wait for it (`poll_job.sh <the job id>`,
foreground); a failed job → do not start the run; `NO_GROUNDING_JOB` → nothing was delivered under this env from this machine. Start the run WITHOUT `ledger_version` and
let the backend decide: it runs a workspace the operator waived ungrounded, and refuses any other before anything
starts. A message that starts `ledger_version refused (missing)` means the agent's setup is not recorded yet: stop,
and tell the user once, in product terms, that `/simulation-scan-agent` records it. When the call starts, read
`detail.grounding_waived` on its answer; when it carries `expires`, keep it with `python3
"${CLAUDE_PLUGIN_ROOT}/scripts/grounding_delivery.py" waiver --workdir "$WORKDIR" --expires
<detail.grounding_waived.expires>` so the next call omits the version at once. Never read the mirror to choose a version, never
send "latest", and never drop the argument to get a call through. A message that starts `ledger_version refused (<cause>)`
is answered by the table of the section "The `ledger_version` refusals" below Step 3.

```
optimize_eval_start(
    eval_set_id=<from .eval_set_id>,
    policy_name=<chosen>,
    workspace_id=<bench workspace id>,  # ALWAYS — the stdout of: source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"
    ledger_version=<the pin, read above>,  # REQUIRED; never drop it to get a call through
    max_iterations=<opt>,
    quality_floor=<opt>, num_samples=<opt>,   # read only on synthetic gold
    # method=<'rubric' | 'decompose'>,        # ONLY when the user asked for one; omit = 'auto'
)
```

Do not pass `method` by default: the backend decides. A refusal at start that names the
method (a rubric rewrite needs more audited cells; a source-fidelity policy cannot be
decomposed) is a user-actionable input problem: say so in product terms.

On stored labels the backend rebuilds each cell's exact judge input from the
uploaded run, measures the CURRENT judge on the held-out cells, and adopts a hybrid
only if it beats it there without missing more real violations (`quality_floor` is
not read).

Returns the generic **Job envelope** (`{id, status: 'queued', phase, started_at,
completed_at, error, detail}`). Read `optimize_job_id` from
`Job.id` (or `detail.optimize_job_id`, equal; `detail` is `OptimizeDetail`,
which also carries `eval_set_id` + `policy_name`). Save `optimize_job_id` in
working memory (ephemeral — not persisted to a file).

### The `ledger_version` refusals

<!-- BEGIN shared:ledger-version-refusals-evals copy: generated from the generate-evals skill; edit there -->
**`ledger_version` refusals.** A message that starts `ledger_version refused (<cause>)` is the backend's answer to
the pin, raised before anything ran. It is not a content problem and it is never retried without the argument: a
set built without the grounded facts is an ungrounded run, and the backend does not allow one. `/edit-evals` and
`/optimize-eval` answer a refusal with this same table.

| cause | what it means | do |
|---|---|---|
| `missing` | the call carried no version: this plugin is older than the backend | update the plugin (the user's step; say so in product terms), then start the build again. Never retry without the argument |
| `no_workspace` | the call named no `workspace_id` | add the bench workspace id (the same call block) and call once more |
| `malformed` | the value is not a version id | re-read the pin as above; never type or shorten an id |
| `grounding_off` | this backend keeps no grounding ledger | not fixable from here: run the contact hand-off (`stop_message.py contact --done`) |
| `unknown` | the backend does not hold that version | re-read the pin as above; the same answer twice: run the contact hand-off (`stop_message.py contact --done`) |
| `other_workspace` | the version belongs to another workspace | the handle on this machine points at another bench: re-run `/simulation-scan-agent`, which records this bench's own |
| `unsettled` | the version is still being made, or a finding holds it | wait for the job as above, then call once more with its `detail.ledger_version` |
<!-- END shared:ledger-version-refusals-evals -->

## Step 4: Poll

Call `job_status(id=optimize_job_id)` every 5 seconds. It returns the
generic **Job envelope**: the lifecycle `status` is `queued | running | done |
failed`, the optimize phases (`preparing_inputs | generating_gt |
iterating | selecting`) ride the free-form `phase` string, and the progress
fields live under `detail` (`OptimizeDetail`).
Stream `phase` + `detail.latest_iteration_message` +
`detail.iterations_completed`/`detail.iterations_total` to the user.

When `detail.route` first appears, show one line: `Method: rubric rewrite` (or
`decomposition`) `. Why: <detail.route.reason>`. If the user wants the other method,
re-call `optimize_eval_start(method=…)` with the same arguments (the backend reads the
same stored labels). Never ask unprompted.

**This is a long job** — synthetic GT annotation by a strong model, then several
code-gen + benchmark iterations. Slow is normal. **Read `elapsed_seconds` off the
envelope — never compute it yourself.** It is server-computed and ticking, and it is on
EVERY Job envelope, alongside `seconds_since_progress` (time since the job last actually
MOVED). You have no clock, so an elapsed figure you derive is a sum of the waits you
INTENDED, which a backgrounded or skipped sleep makes fiction. Treat a
long-but-advancing `phase` / `detail.iterations_completed` as healthy; **do not restart** a
slow-but-streaming job.

Stop polling when `status` is `done` or `failed`. A stale `optimize_job_id`
returns a terminal `status='failed'` Job with a not-found `error` (it does NOT
raise).

A job whose `terminal_reason` is `cost_cap` stopped at the backend's own budget. Say
exactly that: the run stopped at the backend's budget limit, and the original judge stays. State it as a
reason, never as a figure, and do not retry it.

## Step 5: Present the tradeoff

The terminal `job_status(id=optimize_job_id)` result (when `status='done'`) IS the
**Job envelope** with the full before/after snapshot; the before/after snapshot
lives under `detail` (`OptimizeDetail`); derive elapsed from `completed_at - started_at`. Render a compact before/after table:

| | original (llm_as_judge) | hybrid |
| --- | --- | --- |
| quality (vs gold) | `detail.original_quality` | `detail.hybrid_quality` |
| real violations missed | `detail.original_violations_missed` | `detail.hybrid_violations_missed` |

(Drop the "missed" row's original column when it is null: a synthetic benchmark
measures no original judge.)

**Rubric route** (`detail.route.method == 'rubric'`): title the right column `new rubric`
and add a row `rubric length (chars)`: `detail.original_rubric_chars` →
`iterations[selected].rubric_chars`. Say: "The
judge stays an LLM judge with rewritten grading instructions." Offer to show
`detail.winner_rubric` before the adopt question.

Also show `detail.quality_floor_met` and `detail.iterations_completed`. Show no cost or spend figure,
absolute or relative, and make no price comparison between the judges: the comparison is quality
only.

- If `detail.selected_iteration` is **null** / `detail.quality_floor_met` is **false**: tell the
  user **no adoptable hybrid was found** — keep the original judge. Do not adopt.
- Otherwise ask the user explicitly: *adopt the hybrid into the eval set?* Default
  to NOT adopting unless they say yes. (Optionally fetch the experiment doc via
  `fetch_artifact(eval_set_id, paths=['optimize_trace.md'])` to show failure
  details — narrow to that one file; this step doesn't need the rest of the set.)

## Step 6: Adopt (only on confirmation)

```
optimize_eval_adopt(optimize_job_id=<saved>)
```

This is the only call that changes the eval set. It backs up the original, copies
the judge program into the eval set's `out/hybrid/<policy>/`, and rewrites the
policy to `type: hybrid` — **the `eval_set_id` is unchanged**, so the user's
existing run commands and saved envs keep working. On the **rubric route** it changes
only that policy's grading instructions (`system_prompt`); the policy stays
`llm_as_judge`, and the ground-truth hold below never applies.

**The backend holds adoption for ground-truth-dependent judges** (a knowledge-
correctness or memory judge): a hybrid injects no runtime ground truth and the
optimizer's benchmark scored without it, so adopting one would return that judge
to grading blind — a regression the quality floor cannot see. That comes back as
`adopted: false` with a holding finding on `gate_authority.findings` — a finding
you decide on per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`, never a question
for the user, never mentioned to them: keep the original judge unless you hold
evidence that ground-truth grading adds nothing for that policy on THIS agent —
only then re-call `optimize_eval_adopt(optimize_job_id=<saved>, overrules=[{gate_id,
fingerprint, reason}])` with `gate_id` and `fingerprint` copied from that finding
and `reason` citing the evidence. `gate_authority.overrules` answers it (`applied`
adopts). When you keep the original judge, tell the user only that it was kept.

Then refresh the local copy:
1. Sync the mirror from the PROMOTED bytes:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require 'fetch policies')" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed — never proceed on an empty PLURAI_BIN
   # The guard checks $SERVER_URL against the backend this session is bound to before the fetch.
   source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
   if [ -n "$PLURAI_BIN" ]; then
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" fetch policies <eval_set_id> --out "$PLURAI_DIR/policies_config.yaml"; echo "rc=$?"
   else
     echo "rc=no-runner"   # no plurai CLI in this repo: nothing was fetched; use the MCP fallback below
   fi
   # rc=7 = the backend refused this plugin or its SDK: the update text is printed above; say it verbatim and alone, then stop.
   # rc=16 = the backend says DRAFT: an /edit-evals session is open on this set. Nothing
   #        was promoted by it; close that session before trusting the mirror.
   ```

   (`fetch_artifact(eval_set_id, paths=['policies_config.yaml'])` is the MCP fallback, for
   `rc=no-runner`, `rc=65`, `rc=77` and `rc=69` (never for `rc=16`, a draft); it carries the same `policies_state`. Write
   `optimize_trace.md` alongside if returned.)
2. **Verify the promoted bytes locally** — run `verify_policies.py` against the file you just
   synced (same invocation as `/generate-evals` Step 5, with the live sandbox namespace; do NOT
   reach for `/edit-evals` for it — that skill runs no local validator at all, its in-turn gate
   is the judge there). This is a LOAD CHECK, not a second opinion: adoption
   rewrote a policy to `type: hybrid` on the backend and validated it there; this is the check
   that the set the LOCAL toolchain sees is the same one. **Exit 2 is the defect** — the set
   does not load; `--revert` and report. `rc=16` from the fetch means an `/edit-evals` session
   is open (nothing was synced, so there is nothing to verify): close it and re-sync, do NOT
   revert the adoption over it.
3. `.eval_set_id` is unchanged — say so explicitly.

## Revert

`/optimize-eval --revert` (or after the user asks to undo): pick the previously
optimized policy, then:

```
optimize_eval_revert(eval_set_id=<from .eval_set_id>, policy_name=<chosen>)
```

This restores the original `llm_as_judge` from the newest backup and removes the
hybrid artifact (an adopted rubric: the previous instructions, from the backup that
adopt recorded). A refusal because the policy changed since the rubric was adopted
means: change it with `/edit-evals` instead. `optimize_eval_revert` validated the restored config server-side;
re-sync the local `policies_config.yaml` with `plurai fetch policies` (same
block as Step 6) and re-run `verify_policies.py` on it — the same LOAD CHECK as Step 6.2, for
the same reason: a revert rewrote the file too.

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

## Guardrails

- Never author the hybrid policy locally — the backend owns generation + adoption.
- Never trust a `policies.yaml` fetch without checking the policies-state response header (the script
  does it for you). During an open `/edit-evals` session that route serves the session's DRAFT,
  byte-identical to the promoted set — verifying a draft says nothing about what scores.
- Never auto-adopt; always get explicit user confirmation, and default to "keep the
  original" when the quality floor wasn't met.
- Don't surface a failed job's `error` to the user — read it yourself to
  classify. Retry a transient failure per the retry rule (Error posture);
  otherwise follow the error posture — a plain-terms ask if the user's input needs
  changing, otherwise fix per this skill's repair step and, when nothing here fixes it or the re-run after the fix fails again, run the contact hand-off (`stop_message.py contact --done`). Never paste the raw error text.
- Don't pass filesystem paths over the wire; the backend works by `eval_set_id` +
  `policy_name`.
