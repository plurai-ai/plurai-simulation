# Overview-page block — canonical source

Every hand-off that publishes the overview page carries a generated copy of the block below, between
`<!-- BEGIN report-page: … -->` and `<!-- END report-page -->` markers, with `{step}` and `{as}`
substituted per file. The copy sits after the
hand-off's own Markdown publishes: the Markdown summary is the source of truth, and the page follows it.

A hand-off of a stage (scan, traces, suite, run, fixes) also gets the part
between `{stage-only}` and `{/stage-only}`, with `{stage}` the `scripts/deliverables.py` stage it checks; every other
copy (the start, the edits) leaves that part out.

This block is the one source; the plugin's development repository regenerates every copy from it and fails
when a copy differs. Where the copy sits in a skill is an authoring decision: the generator rewrites existing
marked copies only.

<!-- BEGIN CANONICAL BLOCK -->
**Overview page — after this hand-off's Markdown, never in its place.**

{stage-only}First check that this stage left every file the page reads:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check {stage} --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

{/stage-only}```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step {step} --flowdir "$FLOWDIR"{as}
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step {step} --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END CANONICAL BLOCK -->

## When the page is published, and the running state (not part of the block)

A /simulate run publishes the page ONCE, at its end (`publish_report.py`, ONE PUBLISH PER RUN). While its plan runs, every
fill but the end skips `live_page`: the page, its `report.data.js` sidecar and `flow_progress.json` are written, the
run's live local copy (`<flowdir>/report.html`, which the start opens in the browser and names) is refreshed, and
nothing is published or said. The end is the hand-off of a stage that did not land or after which no stage is left (the
fill's `run_end`), or, with no hand-off to carry it, the `end` step that `simulate end` (the user's stop while a stage
is left) and a limited-mode halt (`simulate next`) print. A command run on its own publishes at its own hand-off. The
`report` it hands out is `<workdir>/artifacts/.publish/report.html` as an absolute real path, and only when it lies
inside the folder Claude Code was started in; otherwise the fill skips `outside_cwd` and its `say` names the local page
and says to start Claude Code from the agent's folder. A copy that cannot be written skips `no_copy`: the page still
moves, and `say` names it.

A stage that runs long refreshes the local page mid-stage at **milestone** steps (`publish_report.PUBLISH_POINTS` entries
with `milestone: True`; today `scan-map`, called from `skills/simulation-scan-agent/SKILL.md` Step 2b once the map is
rendered, `scan-described`, `scan-harness` and `scan-users`, called from `skills/simulation-setup/SKILL.md` ("Scan progress
fills") at the start of its Steps 9c, 10 and 11, and `scan-checks`, called from the scan's Step 3 once setup returns —
all filled only while a /simulate run drives the scan (`plan_only`) — then `suite-scenarios`, `suite-evals`, `suite-qual`
and `suite-final`, called from `skills/simulation-generate-suite/SKILL.md`, "Progress fills", and `suite-final-run` and
`suite-final-check`, called from its `references/deliverable.md` around the final run, plus `progress`, which
`scripts/poll_job.sh` and the run's own process `scripts/run_stage.sh` (with `run_tick.sh --loop` beside it on Claude
Code) fill with the job's or run's own counters). A milestone call site is not a copy of this block: it fills with
`--step <milestone>` and nothing else; a milestone never publishes and runs no `done`. `flow_continues` is always false
for a milestone, so it never ends the turn, and it is said nowhere. Between those calls `scripts/page_watch.py` refills
the page, below.

Every `fill` also writes the running state, `flow_progress.json` (`plurai-flow-progress/1`: stages
with done / running / pending / halted, the current step and when it began, and the `sections` /
`stale` marks), into the workdir's `artifacts/` (into `$FLOWDIR` before the workdir exists). The page
renders it: the progress band, the calibration and Draft notes, and what is out of date. A hand-off
shows its stage done only when it landed (`scripts/stage_landed.py`, the predicate the plan's `done`
reads too); otherwise halted, with a reason naming the missing file, and every plan transition rewrites
the file. With no live plan, a milestone writes its own stage (a `plan_only` one, a scan milestone, writes nothing and needs no `artifacts/`) and any
other step only the marks. `fill
--note "<plain words>"` replaces a step's default label. Nothing writes into the page itself or into the
vendored `scripts/report/`.

The page draws the progress band itself from `flow_progress.json` (no overlay is ever added), and it never reloads:
every fill also writes `report.data.js` beside the page, which a page open from the file polls every 2 s and swaps
in place. While a Run plays, `run_tick.sh --loop` and `run_stage.sh` fill within a few seconds of each new or rewritten
file in the run dir (a 240 s / 90 s heartbeat otherwise), and `publish_report.py refill` catches the local page up after a
plan transition rewrites `flow_progress.json`. Between the model's calls `scripts/page_watch.py`, one process per flow dir
while the plan is live, refills within a few seconds of a new or rewritten file under `artifacts/`; it writes no fill
record, no publish copy and no running state. It lives until the plan ends or goes 24 h unwritten, or its session's process
is gone. It does not start with the page off, with nobody watching or on native Windows, which keeps the other refreshes.
A published Artifact has no sidecar; it changes only
when it is published again. Every link a hand-off prints is a plain Stage anchor of the vendored `manifest.json`
(`#scan`, `#traces`, `#suite`, `#run`, `#gaps` — the fixes stage folds into Gaps), never `?stage=`, which an
Artifact does not pass to the page.
