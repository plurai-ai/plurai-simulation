# `verdicts.json`: one conversation's verdicts (canonical source)

Every attempt of a run has one `verdicts.json` beside its `transcript.json` / `error.json`. It is the
only place a verdict is read from. `transcript.json` is the record of what happened (turns, injected
inputs, agent state, metadata, and the run facts `/finalize` returned under `finalize_record`:
`dialog_outcome`, `user_sim_integrity`, `scenario_dimensions`, …). It holds no verdicts and is never
changed after the run. `verdicts.json` is how that conversation was judged, complete every time.

Cite this file from a skill as `${CLAUDE_PLUGIN_ROOT}/docs/verdicts.md`, never as a bare `docs/…` path:
the reader's cwd is the customer's repo. The plugin's development repository checks every such reference.

## Where it lives

One file per attempt, matching the runner's item directories:

| attempt | file |
|---|---|
| a conversation (trial 1) | `$OUT/<scenario>/verdicts.json` |
| a later trial | `$OUT/trial_<k>/<scenario>/verdicts.json` |
| a stability retry | `$OUT/<scenario>/stability_retry/verdicts.json` |

Error rows get one too (`graded: false`, `evals: {}`), so a conversation's status always has one home.
The scenario level across trials is computed from the trial files `run_summary.json` `trials.dirs` lists;
there is no second file. A stability retry is a stability probe: it is not averaged into the score.

## Shape (`verdicts_schema: 1`)

```jsonc
{
  "verdicts_schema": 1,
  "attempt": {"scenario": "<id>", "item": "<dir name>", "kind": "trial|stability_retry", "trial_index": 1, "trial_count": 3, "thread_id": "sim-…"},
  "graded": true,
  "status": {"value": "scored|agent_error|harness_error|incomplete|goal_undecided", "cause": null, "retry": false},
  "judged_against": {"eval_set_id": "…", "eval_revision": "…", "scenario_set_id": "…", "gt_fingerprint": "…",
                     "gt_revision": 0, "kept_from_earlier_revision": []},
  "goal": { /* the backend's scenario verdict, verbatim */ },
  "judging": { /* measurement_integrity, scenario_success_results, evaluated_guards, guard_suppressions, … */ },
  "evals": {"<eval>": {"state": "pass|fail|not_applicable|ungraded", "reason": null, "cell": { /* the backend's cell */ }}},
  "history": [ /* one entry per change, oldest first; never in the report bundle */ ]
}
```

- **Every eval of the set is present** on a graded file, each with one state. An eval guard-skipped on
  every turn carries `cell: null`.
- **The cell is the backend's, verbatim** (minus cost and latency): `turn_idx`-keyed `turns[]` (each with its
  own `state`/`reason`), `reasoning`, `findings[{turn_idx, quote, why}]`, `confidence`, `agreement`. The
  conversation view marks failing turns from this file alone.
- **No derived numbers.** No `pass_rate`, no score, no violated / inconclusive name lists, no counts: they
  are derived from `evals` by the reader that shows them.

### Status (decided once, by the writer)

| status | means | in the score |
|---|---|---|
| `agent_error` | the agent crashed or raised, anywhere in the conversation | yes, as a fail scored 0 |
| `harness_error` | our setup or infrastructure failed (a `harness_*` kind, a backend or provider failure, a measurement breach that VOIDS the scenario) | no: re-run it |
| `incomplete` | the time or turn budget cut the conversation | no |
| `goal_undecided` | the goal was not decided (`goal_achieved` null) | yes, never failed for the goal |
| `scored` | none of the above | yes |

Precedence is top to bottom. A breach that does not void the scenario leaves it `scored`, with the eval it names
`ungraded`: readers take the status as it is and never re-judge a measurement. `cause` carries the runner's typed facts (`{kind, transient, status?}`), so
no reader classifies an attempt from `error.json` again; `error.json` is opened only for remedy prose.
A transient loss is `harness_error` with `retry: true`.

### Eval states

| state | reasons |
|---|---|
| `pass` | — |
| `fail` | — |
| `not_applicable` | `not_applicable`, `mixed_not_applicable`, `guard_skipped`, `turn_skipped_every_turn`, `deferred` |
| `ungraded` | `not_measured`, `judge_error`, `inconclusive:<gate>` |

A score leaves `not_applicable` and `ungraded` out separately and counts each, so a set where everything
is N/A never reads like one that could not be graded. A contested split vote is `ungraded`
(`inconclusive:contested_applicability`) and never blocks.

### `history`

`history[0]` is the run's own entry (`source: run`, states only). Each later change appends one entry:

- `rescore` (a `score_run` after an eval or ground-truth fix): the evals it re-judged, the ones it
  carried, the ones that left (`dropped`: guard-skipped now, or removed from the set), each flip with the
  cell it replaced, and the audit corrections the backend applied itself (`backend_corrections`);
- `correction` (a FALSE RED / FALSE PASS audit ruling): the unit, old → new, verdict, cause, quote,
  reason, and `propagated_from` for a sibling trial it reached;
- `convert` (an old run converted once): what the legacy files recorded.

A replaced cell is kept verbatim only in the entry that replaced it, so the judge's original reasoning is
never lost. `verdicts_file.rescored(doc)` is true once anything after `history[0]` exists.

## Who writes it

One writer, the SDK (`plurai verdicts …`), at three events: the run end (with the transcript, from the
same `/finalize` response), a rescore, and an audit correction. The plugin decides WHAT changes (the
rescore refusals, the correction rules) and hands the writer a typed event; the writer checks the result
is complete and current, appends `history`, and writes atomically. Nothing else writes the file.

## Who reads it

Every plugin reader reads verdicts through `scripts/verdicts_file.py` and nowhere else: `build_run_json`
(rows, trials, stability marks, revision labels), `red_cells` and the audit and correction tools built on
it, `regressions`, `preliminary_findings`, `artifact_deps`, `deliverables`, and the critic and audit
prompts. There is no fallback: a run dir without the file is refused, naming the one converter command
(`verdicts_file.CONVERTER`, `convert_verdicts.py <run dir>`), which converts an old run once. A guard test fails on any other reader of `rescore.json`, `score_overwrites.json` or
the transcript's verdict block.

## The scores

The SDK scores the run from these files and nothing else (`plurai.scoring`): a 0-100 score and a result per
conversation, per scenario and for the suite, and the agent's verdict (Ship / Don't ship / No verdict).
`plurai scores refresh --run-dir <$OUT>` writes that block into `run_summary.json` → `scores` (schema `scores-v1`)
and prints it. The plugin calls it after every `plurai verdicts` write (`verdict_events.send`) and before it builds
`run.json` (`build_run_json.run_scores`), and copies what it says: each row's `result`, `score` and
`fail_reasons`, the suite score and the agent verdict (`release_verdict.agent_line`). No weight, cutoff or pass
rule lives in the plugin; a guard test fails on one.

`run_summary.json` is otherwise the run-end snapshot: only its `scores` key is rewritten, by the SDK. `run.json` carries the current numbers:
it is built from these files, so it is stale whenever one of them is newer (`deliverables.py check run`
names it).

The report page reads row results from `run.json` and drawer detail from `verdicts.json`. The scored run's
files always ship, whatever the page's size budget cuts; `history` stays out of the bundle.

## `score_run` response shapes — `summary=false` vs `summary=true`

A rescore's input. The two modes return **different cell shapes**: `summary=true` **collapses the per-turn
`turns` array** and **drops `findings` entirely**.

| cell field | `summary=false` | `summary=true` |
|---|---|---|
| `turns` (per-turn verdicts; `turn_based` policies only) | present | **ABSENT** |
| `turns_summary` | absent | `{scored_turns, violated_turn_idxs, inconclusive_turn_idxs, errored_turn_idxs, clean_turns}` |
| `findings` (quoted violation instances) | present when non-empty | **DROPPED** |
| `reasoning` | full judge prose | truncated to 240 chars + `…[truncated — full text via summary=False]` |
| `status`, `violated`, `confidence`, `agreement`, `inconclusive_reason`, `label`, `value`, `type`, `scope`, `skipped_turn_idxs` | present | present, verbatim |

- Never conclude "no per-turn violations" from a summary result: reach for `turns_summary.violated_turn_idxs`.
- `violated_turn_idxs` is conclusive-only; a hedged turn lands in `inconclusive_turn_idxs`.
- Summary cells are safe to feed back as `baseline_cells`: the diff compares `status` + `violated` only.

## Rules

- **ONE `score_run` call per scenario per round, and when its result is applied to `verdicts.json` that
  call is `summary=false`.** The file keeps each cell's `reasoning` and `findings`, and the writer refuses
  a summary-shaped result. Use `summary=true` only in rounds that apply nothing.
- **A stability retry is rescored BY REFERENCE, as its own upload item**: `scenario_name: "<scenario>/stability_retry"`
  with the run's `run_upload_id` (`rescore_run.py` does it; the backend judges a run item against its scenario's ground
  truth). That is what makes the run's red-cell audit apply to the retry as to its twin. Never score it under the
  SCENARIO's name with that upload: that re-scores the first attempt, one sample counted twice, and `build_run_json`
  withdraws the row's marks when both rescores name the same upload and item.
- **A full re-run writes a new transcript and a new `verdicts.json` together.** There is nothing to delete
  first and no freshness rule: the file beside a transcript is always that attempt's.
- **Landing carve-out: apply no rescore on a pass that REGENERATES the scenario set.** When the round ends
  by minting a new `.scenario_set_id`, those verdicts describe scenarios that are no longer the
  deliverable; the final set's own run carries its own.
- **A rescore or a correction INVALIDATES the rendered run report.** Rebuild `$OUT/run.json` with
  `build_run_json.py` and re-run `render_run_report.py`, or prepend a STALE banner to `analytics.md` naming
  the rescored scenarios and delete `$OUT/eval_signal.md`. Never quote a figure from a stale render.
- **A rescore belongs to the eval lane's PIN, not to the timeline.** It judges ONE saved run in ONE frame,
  and that frame moves silently. The rescore step passes `--pin "$WORKDIR"`, and it REFUSES (exit 5,
  nothing changed) when the staleness voids the behaviour record: `harness-fingerprint-changed`,
  `scenario-set-moved`, or `run-superseded` (declared before a full re-run). It warns and still applies
  when only the judgement moved (`gt-revision-moved`, `eval-set-moved`, `eval-revision-moved`,
  `corpus-moved`, `corpus-unknown`, `set-adopted`, `set-relinked`). The frame and its reasons:
  `${CLAUDE_PLUGIN_ROOT}/docs/lane-split.md`.
