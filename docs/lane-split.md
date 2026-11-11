# The two-lane quality loop — design record

The suite-quality loop (`/simulation-generate-suite` Step 3) iterates over two surfaces with very
different costs. A scenario or harness change needs a fresh scored run to observe: a new agent
transcript, twenty to thirty minutes of it. An eval change needs only a re-judgement of a saved
transcript: `score_run`, judge tokens, fifteen to twenty minutes for a whole set.

Run serially, the cheap surface pays the expensive surface's price — every eval fix waits for the
next full round, because a fresh scored run is the only substrate it is validated against. That is
the missing tier this document's design adds: a verification substrate for the eval lane that is
NOT the next full round.

The two lanes:

* **scenario / harness lane** — regeneration, env fixes, `simulate.py` fixes. Publishes snapshots.
* **eval lane** — `/edit-evals` plus `score_run` REPLAY over saved run artifacts, validated
  against a pinned snapshot rather than against a fresh run.

## Decisions

**Concurrency is INTERLEAVED SINGLE-SESSION LANES, not parallel sessions and not lane subagents.**
The scenario lane's long poles — a regeneration job, a re-run, a redeploy — already run as
background jobs that re-invoke the session when they land. The eval lane fills those windows.
True parallelism would race four single-writer things at once: the flow-window marker, the
one-shot ask-sanction bearer token, `report_guard`'s single in-progress anchor, and the unlocked
single-line marker files. What it would buy back is residual minutes.

**One critic opens the loop; the rejoin verifies it; there is no pass counter.** The full critic
runs once, before the interval. The rejoin verifies the repairs mechanically (`score_run` full-set
diff, `suite_diagnostics`) and runs a delta RED-CELL AUDIT over what the round changed (the
full audit already ran beside the critic) — never a per-lane critic, and a second full critic only when the first reported
`RUN INVALID`. An eval-only interval reaches the rejoin through its own trigger, so the landing is
always reachable. Per-interval lane budgets: the
eval lane gets ≤2 edit+rescore rounds (this is the existing "a finding surviving two consecutive
re-scores goes to landing triage" rule, stated as a budget); the scenario lane gets ≤1
regeneration batch + 1 batched env-fix round with its single settling re-run + ≤1 harness-fix
batch folded into that same re-run.

**Existing markers keep their exact semantics.** `.scenario_set_id` and `.eval_set_id` have
readers across many skills; renaming one is the highest-blast-radius change available here.
(`.qual_transport` is narrower than it looks — its writer and every reader live in
`simulation-generate-suite` alone — but it is left alone for the same reason.) Exactly one file
is added.

## `.eval_lane_pin.json` — the eval lane's coordinate frame

```json
{
  "scenario_set_id": "…", "eval_set_id": "…", "run_upload_id": "…",
  "gt_revision": 4, "harness_fingerprint": "…", "backend_build": "…", "out_dir": "…",
  "pinned_at": "2026-08-26T09:14:00Z"
}
```

Written at each upload (`scripts/lane_pin.py write`), rewritten only at rejoin. It answers the one
question a replay cannot answer for itself: *what are these saved artifacts a recording OF*.

`scripts/lane_pin.py check` exits **0** for FRESH, **3** for STALE headlined by the heaviest coordinate
that moved, **2** when there is no readable pin (absence is not freshness). With `--refusing-only` a
move whose every reason weighs WARN exits **0** under a `MOVED (judgement only)` headline; the
adoption gate and the eval lane pass it:

| reason | what moved |
|---|---|
| `scenario-set-moved` | `.scenario_set_id` is no longer the set these artifacts came from |
| `corpus-moved` | the corpus was rebuilt: `.corpus_id` differs from the one pinned at the upload, or is not among the set's own `detail.corpus_ids` (passed as `--set-corpus-ids`) |
| `harness-fingerprint-changed` | the harness that produced the transcripts is gone |
| `gt-revision-moved` | the grading ground truth was revised since the upload |
| `eval-set-moved` | the eval set was REPLACED (regenerated / extended), not edited in place |
| `eval-revision-moved` | the eval set was edited IN PLACE since the upload (same `.eval_set_id`, different rubrics) |
| `backend-build-moved` | the BACKEND was redeployed between the pin and this check (`backend_identity().build_id`, passed as `--backend-build`) |
| `corpus-unknown` | a corpus comparison was ASKED for and one side could not be read — warns, never refuses (see below) |
| `backend-unknown` | a backend-build comparison was ASKED for (the checker passed `--backend-build`) and one side is not a build — warns, never refuses. A `check` that passes no build asks nothing and reports nothing on this axis |
| `run-superseded` | declared by `lane_pin.py stale` — see below |
| `set-adopted` | declared by `lane_pin.py adopt` — the final set ADOPTED the recorded scenarios and the eval set was relinked to it |
| `set-relinked` | declared by `lane_pin.py relink` — the final set was generated FRESH (adoption refused) and the eval set was relinked to it |

An **unknown** current value is never reported as drift. The pin cannot tell "it did not move"
from "I could not look", and guessing either way is worse than silence: it would refuse every
replay on a backend that reports no `gt_revision`, or bless one whose set was deleted underneath
it.

With two exceptions, which is why `corpus-unknown` and `backend-unknown` exist. Most axes' reference
IS the pin, so an unknown side means no comparison was ever asked for. The corpus axis's reference can
arrive from OUTSIDE the pin (`--set-corpus-ids`), and the backend axis's CURRENT value always does
(`--backend-build`: `lane_pin.py` is stdlib-only and never calls the box), so "the caller asked and the
side it needs cannot be read" is a real third state — and with nowhere to put it the check said FRESH,
asserting the agreement it could not make. It **warns and does not refuse**: `check` still exits 0,
the re-score is still applied, because nothing was measured to have moved.

`--set-corpus-ids` is what makes the SET half of `corpus-moved` reachable at all — without it the
axis can only compare the workdir marker against the pin. Pass it wherever you have the set's
`detail.corpus_ids`: to `lane_pin.py check` AND to `apply_rescore.py --pin` (through `rescore_run.py`).

A fresh RUN is a staleness with no on-disk tell — an env fix moves no marker and no harness
file, so every coordinate still matches while the artifacts underneath are superseded. That one is
**declared**: `lane_pin.py stale --reason run-superseded`, cleared by the next `write`. The relink
of the eval set to the final scenario set is the other declared kind, and it carries TWO reasons
because it records two different facts: `set-adopted` (the final set adopted the recorded
scenarios, so the recorded scenarios are still in it) and `set-relinked` (the final set was
generated fresh, so the recording shares nothing with the set the ids now name). `lane_pin.py
adopt` / `lane_pin.py relink` write them; the vocabulary is closed (`lane_pin.DECLARED_REASONS`),
so `stale --reason` refuses anything outside it.

**A re-score belongs to the PIN, not to the timeline.** A result is applied to a conversation's verdicts only
while the pin is fresh; a landing re-run marks the pin stale; a rejoin rewrites the pin and re-opens applying.
`apply_rescore.py --pin "$WORKDIR" [--set-corpus-ids a,b]` enforces it, and the enforcement splits by what the
staleness voids:

* **REFUSED** (exit 5, nothing applied) for `harness-fingerprint-changed` and
  `scenario-set-moved`. These void the BEHAVIOUR record itself — the harness that produced the
  transcripts is gone, or the scenarios the verdicts grade are not the current set — so applying one
  publishes evidence about a world that no longer exists.
* **REFUSED** too for `run-superseded`: it is declared BEFORE a full re-run, so the conversations a
  re-score would apply to are the new run's, not the ones the result describes.
* **WARNED** (still applied) for `gt-revision-moved`, `eval-set-moved`, `eval-revision-moved`,
  `set-adopted` and `set-relinked`. There the behaviour still stands and only the judgement of it
  moved: a fact for the round to weigh, not a reason to withhold the round's evidence.

`backend-build-moved` is weighted `WARN` alongside them but `apply_rescore.py` never SEES it: that
script passes no `--backend-build`, so the axis is not asked there (nor in `build_run_json.py`).
Only the `lane_pin.py check` call sites ask it.

`--backend-build` takes `backend_identity().build_id` — the IMAGE the backend runs. It answers the
question none of the other axes can: the box was REDEPLOYED between qualification and the final
generation, so the machinery that produced the two halves is not the same machinery, while every
marker on disk still agrees. An empty value (a failed fetch, a 401, a backend predating the field)
is normalized to `unknown` at `write` and reported as `backend-unknown` at `check` — never as drift,
which it was on every check for the rest of a build before the normalization. `lane_pin.py scope`
renders the whole frame — client pin, the sets now named, the RECORDING behind them (its upload,
its backend image build, its `eval_revision`), passes used, critic verdict, and one limitation line
per coordinate it could not read or that has MOVED since the pin — as the report's qualification
scope block.

**The weight lives on the axis, and every refusing reader projects one table.** `lane_pin.REASONS`
maps each detected axis to `REFUSING` or `WARN` (and `DECLARED_REASONS` does the same for the
declared ones) with **no default**, so a new axis cannot be added without stating what it costs;
`lane_pin.REFUSING_REASONS` is the projection `apply_rescore.py` takes and
`REFUSING_DETECTED_REASONS` the one `build_run_json.py` takes — neither keeps a list of its own.
It did, and that is the defect this replaced: the axes are ordered COARSEST-first, `staleness()`
returned the first moved one, and inserting the warn-only `corpus-moved` ahead of
`harness-fingerprint-changed` meant a rebuilt corpus answered on behalf of a dead harness — the
sidecar writer, handed only the corpus reason, wrote the file and exited 0. `check` now HEADLINES
the heaviest moved reason (and still lists every axis under it), and `staleness()` returns the
same one.

## The harness fingerprint

`scripts/harness_fingerprint.py` — one sha256 over a language-aware manifest:

| language | manifest |
|---|---|
| python | `plurai.config.yaml`, `simulate.py`, `_mocks.py`, `_storage.py` |
| claude-skill | the above plus `_skill_tools.py` |
| bridge | the python set plus every file under `bridge/` except `bridge/runtime/node_modules/` (and bytecode caches) |

The language is read off the files: `_skill_tools.py` marks claude-skill, `bridge/bridge.map.json`
marks bridge, anything else is python.

A file's **presence** is part of the hash (an absent entry hashes as `null`, not as omitted —
inlining `_mocks.py` into `simulate.py` and deleting it is a harness change), and the manifest
itself is inside the hash, so a language read differently never collides with the old reading.
Content hashes only — no mtimes, no sizes, no absolute paths — so two machines agree.

It exists because a harness edit is otherwise unobservable: it leaves no mark on the run artifacts,
the eval set, or any backend id, while it changes exactly what a replay assumes is fixed.

The value travels two ways. `plurai ingest-run --harness-fingerprint` stamps it (with the eval set,
the scenario set, the agent version and the SDK's own version) into a nested `provenance` key in
the lease manifest; the plugin LIFTS that block out and passes it to `upload_run_artifacts_start`
as separate arguments, because the backend's manifest model ignores undeclared keys and would
accept a verbatim manifest while silently dropping the provenance. And `score_run` takes
`current_harness_fingerprint`, answering with `harness_state` ∈ `match` | `mismatch` |
`unverifiable` — `unverifiable` being what you get for not saying. A server-side mismatch only
warns; the local pin check is the enforcement point.

## Rejoin — the lane merge

The trigger is the scenario lane LANDING: its settling qualification re-run completes. Not when
the regeneration job finishes — a set nobody has run is not a snapshot.

1. **Quiesce the eval lane** — finish the current round against the OLD pin, then CLOSE the edit
   session. The backend refuses an extend/relink path while a session is held.
2. **`relink_eval_scenario_set`** — this IS the merge operation. Its audit output (guard coverage,
   dead flow scope, premise warnings) is worked in ONE `/edit-evals` round, charged to the NEXT
   interval.
3. **Re-upload the new run artifacts** → a new `run_upload_id`.
4. **Rewrite the pin.**
5. **Re-validate on the NEW upload.**
6. **Verify and audit.** The three mechanical checks, then the delta red-cell audit (the full one ran beside the critic); a full barrier critic
   only behind `RUN INVALID`.

In-flight eval work at the barrier: committed-and-verified edits carry (they live in the backend
eval set and survive the relink); committed-but-unverified are verified in step 5;
uncommitted get the bounded finish in step 1 and are never abandoned.

## The harness-fix procedure

A `simulate.py` / `_mocks.py` / `_storage.py` edit changes how the agent BEHAVES, and unlike an
env fix it has no probe and no cheap observer. Six steps, each load-bearing:

1. File-owner test, then apply — never ask. A harness repair is applied without a question in every
   mode and never declined; a defect in the SDK, the plugin or the backend is not a harness edit at
   all. One batch, carried by the round's one re-run; still failing after it → reported, not repaired
   again. Record it as a `harness-fix` loop-log entry naming the file and what changed and why in plain
   words, and name it the same way in the hand-off.
2. Edit the harness file.
3. Run the plugin's `verify_simulate.py` (`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/`,
   with the config's `tool_content_envelope.kind`) — never a copy in the workdir, whose version
   nobody records. Exit 0 or the edit is not a fix — a harness that does not verify breaks every
   scenario at once, which reads as an agent collapse.
4. Recompute the fingerprint (`harness_fingerprint.py --write`). This is what makes the change
   visible: every saved transcript is now stale and the pin check starts refusing replays.
5. Re-run and re-upload BEFORE the next critic. Mandatory: the eval-only round may skip a re-run
   only because its behaviour evidence is still true, and here it is not.
6. Rewrite the pin at the REJOIN, not at the edit. Re-pinning early re-opens applying re-scores
   against artifacts nobody has re-recorded.

Step 4 is also the interlock: it stops the eval lane until the rejoin, which is the mechanism
working rather than a failure — there is nothing left to replay against.

## The `terminate_values` carve-out

A policy that declares `terminate_values` can end the simulation early — a per-turn verdict ORs
into the run's stop signal. Editing such a policy changes WHERE FUTURE RUNS STOP, and a replay
over a saved transcript cannot validate that: the transcript already stopped where it stopped.

So an eval finding whose fix touches a `terminate_values`-bearing policy is **fixed in the eval
lane, validated only at the next scored run**. Everything else about the stopping point is
eval-independent and replays normally.

## Rollout and the fallback drill

The structure flip is gated on `$WORKDIR/.loop_mode` (`serial` | `interleaved`).

**`interleaved` IS the default today, and an absent marker reads as `interleaved`.** `serial` is
the opt-OUT, per build, by one file write. What the opt-in default cost is that a model
blocked on foreground polls takes far longer per suite than one that overlaps its lanes. The executing text — `simulation-generate-suite/SKILL.md`, the thing a
driver actually runs — is the authority on this and says the same; if this document and that one
ever disagree again, the SKILL wins.

The serial ordering is preserved verbatim as the `serial` branch, so opting in and dropping back
out are the same one file write, taking effect at the next interval:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
echo serial      > "$WORKDIR/.loop_mode"   # opt OUT
echo interleaved > "$WORKDIR/.loop_mode"   # …or back in (same as deleting the marker)
```

No reinstall, no session restart, nothing to re-run.

**Never update the plugin mid-suite-build.** The installed plugin is a cached snapshot keyed on
version; updating it swaps the loop's own instructions underneath a build whose
earlier intervals ran on the previous ones, and the markers on disk were written by those. Finish
the suite, then update.
