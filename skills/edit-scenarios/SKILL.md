---
name: edit-scenarios
user-invocable: false  # internal building block of the simulation-* product commands; hidden from the / menu (still model-invocable)
description: >-
  Fix a scenario's grading ground truth in place (a wrong expected_answer, stale evidence quotes, a mis-stated target), one field per call. Not for persona, questions or world: regenerate for those.
---

# Edit Scenarios (grading-GT only)

You apply targeted corrections to a generated scenario's **grading ground truth** with the
backend's `edit_scenario_gt` MCP tool. This is NOT a session and NOT an editor over scenario
files: one deterministic call per field, applied in place on the backend, gated server-side.
It exists for the eval-quality iteration loop — before it, correcting one wrong
`expected_answer` cost a full scenario regeneration (~45 min); now it is one call plus a
`score_run` re-score (minutes, judge tokens only), because the judges re-read scenario GT
live from the artifact at score time.

**The three verbs, positioned:** `/generate-scenarios` = new dice. `/refine-scenarios` =
steered new dice (always a NEW `scenario_set_id`). `edit_scenario_gt` = same dice, corrected
label — the id never changes, so every saved run stays rescoreable.

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

## The contract — what is editable, what is locked

Editable `field_path` forms (all judge-only reads; the simulated user never sees them):

- `knowledge_plan[<i>].expected_answer` — the answer the knowledge judges grade against
- `knowledge_plan[<i>].evidences` — retrieval GT, `[{passage_index, quote}]`; quotes must be
  **verbatim corpus text** (retrieval alignment substring-matches them — a paraphrase
  silently zeroes recall)
- `knowledge_plan[<i>].grounding_contexts` — recall-fallback passages (list of strings)
- `knowledge_plan[<i>].recall_source_turns` — earlier-turn indices (list of ints)
- `scenario_target` — the judge-consumed success narrative (plain string)
- `success_criteria.deterministic.{expression,metadata,label,rationale}` — the deterministic
  check (edited expressions re-run the backend's generation-time AST gate + a sandbox probe)

**Locked** `field_path` forms — `persona`, `first_talk`, `initial_world_state`, `agent_context`,
`knowledge_plan[<i>].question`/`.topic`/`.memory_dependent`, `absence_plan` — shape what the
scenario ASKS of the agent (the simulated user and its world read them), so editing one changes
what the suite tests. The backend HOLDS such an edit: `applied: false` with a holding
`scenario.gt_field_locked` finding, the set untouched. It applies it only when you re-send the
same call with an overrule naming that finding (a world or `agent_context` edit applies only if
the generator's seed validator then passes). Their normal fix is still `/generate-scenarios` or
`/refine-scenarios`. A `field_path` in neither list (a typo, `turn_index`, adding or removing plan
entries) gives `applied: false` with the invariant `scenario.gt_field_unknown`: nothing is
editable there, so re-check the path — never overrule it.

## Judgment BEFORE the call — two request shapes

1. **The source rule.** Every GT change must be grounded in a NAMED external truth, passed as
   `cited_source`: an operator-stated fact ("the user confirmed the refund window is 60
   days"), a corpus quote, or a PRD reference. **"The agent answered differently in the run"
   is NOT a source** — editing GT to match agent output launders the agent's mistakes into
   ground truth, which is precisely the false-green this surface must never create. If the
   only evidence is the disagreement itself, that is a needs-external-ground-truth item:
   state the exact fact for the user to confirm, and only edit once they (or the corpus)
   answer. For a **corpus-backed set, verify the claim client-side first**: fetch the corpus
   (`fetch_artifact(<corpus_id>)`, id from `plurai_sim/.corpus_id` / `.knowledge_bases`) and
   find the quoted text in its samples/graph. Corpus supports the new value → proceed, cite
   the quote. Corpus contradicts it → STOP: the conflict is upstream — route via
   `${CLAUDE_PLUGIN_ROOT}/skills/edit-scenarios/references/prd-corpus-reconciliation.md`
   (fix the wrong document at its source), not through a GT edit that papers over it.
2. **Structural asks.** "Change the question", "make the persona calmer", "add another turn",
   "seed a different order" — refuse and route: these change behaviour under test, so the fix
   is `/refine-scenarios` (steer + regenerate) or `/generate-scenarios`. Say why in one plain
   sentence; never forward them hoping the backend sorts it out.

## The call

```
edit_scenario_gt(
  scenario_set_id  = $(cat plurai_sim/.scenario_set_id),
  scenario_name    = <folder name, e.g. from list_artifacts / the run report>,
  field_path       = 'knowledge_plan[2].expected_answer',
  new_value_json   = '"60 days from delivery for damaged items."',   # JSON-encoded!
  cited_source     = 'corpus: returns_policy chunk — "Damaged items may be returned within 60 days"',
)
```

`new_value_json` is the JSON encoding of the value: a string field takes a JSON string
(quoted), `evidences` a JSON array of `{passage_index, quote}` objects, `metadata` a JSON
object. One field per call; batch several corrections as several calls.

**Branch on `applied`** (a backend predating it has no such field: there a refusal is an error
and a returned result is an applied edit):

- `applied: true` → the edit is written; the response carries `gt_revision` (the set's edit
  counter); `warnings` is empty on the wire. After an overruled locked-field edit the scenario
  asks something its generation did not author: every saved run of that scenario was played
  against the old version, so re-run it rather than re-score it, and never relay a finding's text.
- `applied: false` → nothing was written. Read `gate_authority.findings` and dispatch each per
  `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`. A holding `scenario.gt_field_locked` or
  `scenario.gt_edit_check` finding you decide to lift goes back on the SAME call with
  `overrules=[{gate_id, fingerprint, reason}]` copied from it; the outcome of each comes back on
  `gate_authority.overrules`. An edit can hold on more than one check — the re-send reports the
  next one. `scenario.gt_field_unknown` is invariant: fix the path.

A refusal (error) still means nothing was written: a malformed value, an unparseable file, a
missing `cited_source`.

## After the edit — verify with score_run, pair the eval set

After the last accepted edit, bring the local artifacts up to date: it pulls the edited scenarios,
rebuilds `suite.json`, coverage and the renders, and prints which run cells, findings and fix items
the edit made stale.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/artifact_deps.py" apply "$WORKDIR" --refresh
```

- **`rescore_runs` is the re-score list.** An applied edit names every saved run upload of the set carrying the
  scenario (`[{run_upload_id, items}]`): re-score each such run with `/edit-evals` Step 5's `rescore_run.py` block on its
  local run dir and that `run_upload_id` (`rescore='changed'`, each item's current cells as `baseline_cells`), so those
  runs' verdicts follow the edit; empty → nothing was uploaded to re-score.
- **Re-score, don't re-run.** Re-score the `rescore` items it lists (only those scenarios). When qualification-run artifacts exist, verify with the
  backend's `score_run` exactly as `/edit-evals` Step 5 (the score_run item) does. **The run
  upload is pre-authorized by the invocation — never ask consent for it**; the harness's
  tool-approval prompt is the chokepoint and disclosure is a statement, never a question.
  (Upload the run once:
  `source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1; PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-run)" || { [ $? -eq 3 ] && exit 1; }; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-run --run-dir <run dir> --workspace-root "$PLURAI_DIR"` prints the lease manifest — the preflight
  resolves `$PLURAI_DIR` in the SAME call and catches a wrong-backend upload; never hand-roll the
  tar — then `upload_run_artifacts_start(manifest=...)` and pass `run_upload_id`; inline
  dicts only for a backend predating `run_upload_id`, never as a workaround for an SDK whose
  CLI lacks `ingest-run` (upgrade it — the resolver says so); score and apply with `/edit-evals` Step 5's
  `rescore_run.py` block — it passes each item's current cells as `baseline_cells` and applies each `summary=false`
  result to that item's verdicts — never score twice, and honour `not_scored`). The knowledge /
  memory / retrieval judges read the edited GT immediately. A `WARNING (disclosure: home_named)` line
  `ingest-run` prints comes before any byte leaves: relay it to the operator, naming the files it lists.
  It does not stop the upload.
- **One limit to state plainly:** `score_run` re-scores end_to_end AND turn_based
  policies (turn-based cells are re-judged per transcript prefix on current backends),
  but it does **not** re-evaluate `success_criteria.deterministic` — a deterministic-check
  edit pays off on the next live run, so don't report "the edit didn't take" when its
  cell doesn't move. A `scenario_target` edit's `deterministic_check` says what it did to the
  goal check: `recompiled`, `retired`, `superseded` (unmeasured until its expression is
  re-authored; the goal rests on the target judge) or `unchanged`. A
  `scenario_target` edit re-scores only through a `*target*`/`*adherence*`-named judge —
  the runtime injects the target into exactly those judges (the naming contract in the
  backend's eval schema reference); with no such judge in the set, it too waits for the
  next live run.
- **The `stale_eval_gt` pairing.** Editing `evidences`/`grounding_contexts` leaves the
  groundedness judges of every eval set built from this scenario set stale: they
  embedded the OLD excerpts verbatim at eval-build time. A GT edit without the pairing is a
  half-fix — the scenario says one thing, the judge still grades by another. Either
  re-derive the embedded excerpts via an `/edit-evals` session **from the EDITED scenario
  set** (`fetch_artifact(<scenario_set_id>, paths=['<scenario>/knowledge_plan.yaml'])` — the
  eval workspace's staged `inputs/knowledge_grounding.md` predates your edit, so never
  re-derive from it), or re-run `/generate-evals` (the `gt_revision` bump makes regeneration
  cache-safe). `expected_answer` / `scenario_target` / deterministic-check edits need no
  pairing — judges consume those live.

Then check what is still stale, and say in the hand-off that a re-run will update any `rerun` item
it lists:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/artifact_deps.py" check "$WORKDIR" --json
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

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step edit-scenarios --flowdir "$FLOWDIR"
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step edit-scenarios --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

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

Scoping note for this skill: a held `edit_scenario_gt` result is gate-findings material, and a
refusal is usually step 1 or 2 material. A held `scenario.gt_edit_check` finding (the authoring
check it failed is the `rule` in its evidence) usually means the requested value is wrong in a way
the user can fix ("that quote isn't verbatim corpus text"); overrule it only with evidence the
check is wrong about this value. Never paste a finding or the allowlist. The
`stale_eval_gt` consequence is explained in your own words per the pairing section above, never
quoted.

## What NOT to do

- **Never edit GT on the run's say-so.** The transcript proves disagreement, not truth.
- **Never forward a structural ask as a grading fix** (question / persona / world / turn count).
  Its fix is `/refine-scenarios` or `/generate-scenarios`. The one exception is yours alone to
  take: a locked-field edit you judge to be what the suite must test, sent and then overruled per
  the call section above, with the evidence in the overrule's `reason`. Never ask the user about it.
- **Never write scenario files locally.** The backend stays the only store, and this tool is the
  only sanctioned mutation of it; `artifacts/scenarios/` is the page's read-only copy, pulled by
  `artifact_deps.py`.
- **Never skip the `stale_eval_gt` pairing** after a retrieval-GT edit — a rescore against a
  judge that embeds the old excerpts "verifies" against stale ground truth.
