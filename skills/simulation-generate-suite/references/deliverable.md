# The deliverable phase — Steps 3b, 4, 5, 6, 7

**This file is the CONTINUATION of `${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/SKILL.md`,
not a reference sheet.** Everything in it is instruction addressed to the same orchestrator, in
the same voice, with the same authority as the steps above it; read it in FULL when Step 3's
quality gate converges, and follow it in order.

It lives here rather than in the skill body because it is ~100 KB that is only reachable after
the loop converges, and the loop is where the session's context is already at its most
expensive: the whole skill was pasted into the invocation turn, the suite started at 277k-412k
tokens, and median time-to-first-token went from 3.1 s below 200k to 8.6 s above 800k across
~400 round-trips. Nothing here was trimmed, softened, or made optional by the move — the ONE
change is when it enters the window.

Preconditions this file assumes, all established above: the qualification gate converged, a
critic pass judged the CURRENT `$WORKDIR/.scenario_set_id`, and `$WORKDIR` / `$ART` resolve
(`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"` in every Bash call — the harness
resets shell state between them).

---

## Step 3b — generate the FINAL suite (count = 7)

**Precondition — a critic must have judged the CURRENT set.** Zero means run
`/analyze-simulation` first, whatever the `score_run` cells said. Scoring is not judging, and a
re-steer is not a pass. (The entry's set id sits on the note line BELOW the kind, hence `-A1`.
The `source` is part of the block: the harness resets the shell between Bash calls, so `$ART`
and `$WORKDIR` are unset here otherwise and the grep reads `/loop_log.md`. An unreadable set id
prints `0` — never an empty pattern, which `grep -c` matches against every line.)

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
SET="$(cat "$WORKDIR/.scenario_set_id" 2>/dev/null)"
if [ -z "$SET" ]; then echo 0; else grep -A1 -- '`critic`' "$ART/loop_log.md" 2>/dev/null | grep -c -- "$SET"; fi
```

**Before you generate the deliverable: a suite that tests NO KNOWLEDGE must not ship silently.**
The delivery is knowledge-blind — a green suite that tested none of it — in either of two shapes,
each read off a typed record, never off your own reading of the batch:

- **Declared, and nothing planted.** `grounding.needs_corpus` is `true` in `$ART/agent_topology.json`
  (the fact Step 0 read) and no knowledge base plants a knowledge turn — no `.knowledge_bases`, or
  every entry `knowledge_planning: "off"` / `"grounding_only"` or unbuilt. The walk then plans zero
  knowledge turns and the rollup below is silent by contract, so this check is what sees it:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 - "$ART/agent_topology.json" "$WORKDIR/.knowledge_bases" "$ART/suite_state.json" <<'PY'
  import json, sys
  def load(path):
      try:
          return json.load(open(path, encoding='utf-8'))
      except (OSError, ValueError):
          return None
  topo, bases, state = load(sys.argv[1]), load(sys.argv[2]), load(sys.argv[3])
  phases = [p for p in (state or {}).get('phases') or [] if isinstance(p, dict)] if isinstance(state, dict) else []
  ends = [i for i, p in enumerate(phases) if (p.get('phase'), p.get('status')) == ('report', 'landed')]
  answered = any(p.get('phase') == 'corpus' and p.get('answer') == 'proceed-without-corpus'
                 and p.get('answer_backed_by') == 'run_driver_ask_answered' for p in phases[ends[-1] + 1 if ends else 0:])
  grounding = topo.get('grounding') if isinstance(topo, dict) else None
  needs = grounding.get('needs_corpus') if isinstance(grounding, dict) else None
  plants = isinstance(bases, list) and any(
      isinstance(b, dict) and b.get('corpus_ids') and b.get('knowledge_planning') not in ('off', 'grounding_only')
      for b in bases)
  if not isinstance(needs, bool):
      print('unrecorded')
  elif needs and not plants:
      print(('answered' if answered else 'knowledge-blind')
            + ': grounding.needs_corpus is true and no knowledge base plants a knowledge turn'
            + ('; this build answered proceed-without-corpus' if answered else ''))
  else:
      print('ok')
  PY
  ```

  `unrecorded` (a topology written before scan recorded the block) → apply the same rule to the
  verdict Step 0 derived in its place.
- **Planted, and nothing realized.** The agent declares a knowledge dimension (a `knowledge_bases`
  entry was passed, or the state schema pins `ground_from`) and the qualification realized no
  knowledge turns while the eval set's knowledge-class judges were SUPPRESSED: a
  `knowledge_turns_unrealized` item on the scenario job, a knowledge-class `never_scored_policy`
  on the eval job.

When the check prints `answered` (a person's answer to the grounding gate, typed in this build's phase record), record
the accepted gap — `append_loop_log.py --kind note --lane scenario --finding "knowledge-blind delivery
ACCEPTED: proceed-without-corpus"` — and generate the final set without asking.

When the check prints `knowledge-blind`, or the *Planted, and nothing realized* shape holds, the order
is fix-then-deliver: attach the corpus (build it with `/build-rag-corpus` when none resolves; the label
is derived from corpus meta — `/generate-scenarios`), re-force the knowledge flow, re-qualify, and only
then generate the final set.

When the repair is not available, decide it yourself — never ask: do not generate the final set
as a knowledge suite; record the gap in `$ART/loop_log.md` as NOT accepted — `append_loop_log.py
--kind note --lane scenario --finding "knowledge-blind delivery NOT accepted: <suppressed judges, or:
no knowledge base attached>"` — which Step 6's demotion rule then reads, and continue to the report.
A deliberate skip is a legal outcome; a silent one is not.

The quality gate validated the *machinery* — the generation pipeline, the steering, the
evals, the harness — on the small qualification sample. The deliverable is bigger: once
the gate converges, run **generate-scenarios again with the steering that produced the final
validated qualification set, changing only the count** — same `[context]`, same knowledge
bases and dials. If the loop re-steered the sample (Step 3 fix), the re-steered values are
in `$ART/loop_log.md` under `re-steer`; read them from there, not from memory. The one addition
is Step 1's demand per `not_applicable` or `skipped` eval that `render_suite.py --not-qualified` lists.

**"Changing only the count" includes the INPUTS — re-send every one of them** (Step 1's rule, and
this is the call it was written for). The deliverable is the batch nobody re-checks, so a
document dropped here ships. Re-read them off the workdir the way Step 1 did, the state schema
included. Compose it ONCE, complete: start from the qualification call's own arguments and carry
every input it sent — each upload id (the scan contract's `.scan_contract_upload_id` among them),
the inline schema files, the knowledge bases, the document fields, the env — changing only the
count, the steering and the bundle. A queued final job re-issued to add a missed input costs the
queue time and a cancel.

**`eval_set_id` is one of them, and by this step it always exists.** Pass
`$WORKDIR/.eval_set_id` — the slot plan seats the eval set's steering demands into the seven
slots it is about to draw, and it can only do that when it is told which set will grade them.
Omitted, the deliverable batch is dealt blind to its own rubrics. Step 1 may legitimately have
run before the evals existed; this call may not.

**A `slot plan: refused: unsatisfiable at count 7; satisfiable at N` here is RECORDED, never
re-issued at `N`.** The count stays 7 (the Final count rule below) — the Qualification-count
rule's re-issue branch belongs to the qualification batch. A refused plan still seats every demand
that fits, and a higher count raises every fractional floor with it, so re-issuing would not
converge. What the seven slots could not carry is read off the demand ledger in 3b.9.

**Log this generation before launching it — `regen_gate` denies it otherwise.** A qualification
set already exists on disk, so the gate cannot tell this call from a Step 3 re-draw by itself,
and it is one-shot: the top-up below needs its own entry too.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --kind regeneration --flag-class coverage --lane scenario --finding "final suite, count 7"
```

`coverage` is the honest class here and the script accepts it at this stage precisely because
breadth is what the final batch is FOR — the same rule that refuses it during qualification.

**Steering is re-expressed for the DELIVERABLE, never carried verbatim, in two cases:**
- *Any INPUT was edited after that batch* — read the `input-edit` entries in `$ART/loop_log.md`.
  A PRD edit rebuilds the flow graph and RENAMES its flows, so a carried `interest` names ids
  that no longer exist and parses to zero deltas — silently, since only the adapt path reports a
  no-op steer. Re-derive it against the flows the current graph has. **This is the WIDER test of
  the two**: re-deriving steering is cheap, so every logged edit triggers it, while the adoption
  forfeit below is decided by a narrower predicate over the same entries (scope + timing, via
  `--adoption-test`) — do not read one verdict as the other's.
- *The text is QUALIFICATION-RELATIVE.* "Prioritise the flows the previous batch never
  walked", "reduce weight on X, already covered twice" — these describe a 3-scenario sample
  and are meaningless for the 7 that ARE the deliverable. Say what the final set must
  contain. (Observed: both at once — a rebuilt graph plus a batch-relative string — and the
  PRD-mandated family the steer was meant to secure never shipped.)

<a id="typed-demands"></a>
**PASS THE MECHANICAL HALF OF THAT STEER AS `steering_demands`, not only as prose.** Steering is
the only channel by which a critic finding reaches the delivery batch, and prose has no delivery
contract: four demands across two builds were issued explicitly, missed, and each then accepted as
a gap because nothing compared what was asked against what shipped. Three shapes are checkable and
the backend measures all three off the batch it already audits — see
the call contract in
`${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/generate-scenarios-call.md` for the wire form:

| what you wrote in prose | the typed demand |
|---|---|
| "one `<complication_id>` slot" | `{'kind': 'complication_dealt', 'subject': '<complication_id>', 'slots': 1}` |
| "a `<flow_id>` scenario where the user asks for more than the seed permits" | `{'kind': 'flow_walked', 'subject': '<flow_id>', 'slots': 1}` |
| "keep at least two slots single-flow" | `{'kind': 'single_flow_slot', 'slots': 2}` |

**`subject` comes from THIS run's graph — never from this table.** The ids above are
placeholders; read the real ones off the dimension graph / `get_artifact_meta`'s coverage roll-up
for the set you are steering. The backend REFUSES a demand whose subject this run does not have
("names a subject this run does not have"), so a copied example costs a refused call — and a
subject that merely LOOKS plausible costs worse: it can never be delivered, and its `delivered: 0`
reads as a coverage gap that no re-run will close.

**Keep the rest as prose and pass BOTH.** A typed demand carries the COUNT, not the shape: the
sentence about *why* that flow's scenario has to refuse-with-a-reason still has to be in
`interest`, because nothing typed can express it. Type what is countable, say the rest, and
send them together. An unknown `kind` is refused at the call — so a demand you invent a shape for
fails loudly instead of being silently dropped, which is the whole point of the channel.

<a id="coverage-closure"></a>
**CLOSE THE QUALIFICATION'S COVERAGE HOLES ON THIS BATCH — computed, never read by eye.** The suite
reads its own coverage here and deals what exercises it; none of it reaches the report or the hand-off.
Fetch the finished `suite_diagnostics` job of the pass that judged the CURRENT set, then:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status '{"id": "<that suite_diagnostics job id>"}' > "$ART/suite_diagnostics_qual.json"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/coverage_closure.py" --diagnostics "$ART/suite_diagnostics_qual.json" \
  --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" --topology "$ART/agent_topology.json" \
  --policies "$WORKDIR/policies_config.yaml" > "$ART/coverage_closure.json"; echo "closure exit=$?"
```

`eval_asks` FIRST, before this call: close all of them in ONE non-additive `/edit-evals` session on the
current set — each is a scope declaration (`target_complications` / `target_flows`) on an existing judge,
its rubric extended only to name what it already grades, so the eval-set demands it adds are seated by
this draw. One no judge can carry is logged as a `note`. Then append `steering_demands` to this call's
typed demands and the `interest` lines to `interest`, and log one `re-steer` entry naming them. Exit 2
names why nothing was computed: log it as a `note` and generate without it.

The adversarial floor (`untrusted_instruction` / `presses_after_refusal` entries) needs no demand from you: the
backend reserves its seats from those typed flags on every set.

<a id="removing-a-flow"></a>
**Removing a flow — steering ALONE cannot do it, so do not pretend it did.** When the loop's
incomplete-triage put a flow over the final 40-minute budget (`exceeds-final-budget`), the flow
must be ABSENT from the deliverable. The backend clamps `interest` / steering weights to **[1, 10]**
and exposes **no removal primitive**, so the lowest weight you can express is still a weight the
walk can take — a down-weighted flow comes back at a lower rate, not at zero, and "I steered away
from it" is a request the generator was free to decline. Three steps, in order, and the last two
are not optional:

1. **Carry the exclusion into the generation steering as an explicit NEGATIVE re-steer.** Step 3b
   cannot read the loop's memory — it re-expresses steering from `$ART/loop_log.md` — so say it in
   the `interest`/`adapt` text you pass to this batch, naming the flow, and log it
   (`append_loop_log.py --kind re-steer --finding "exclude flow <X>: exceeds the 40-min scenario time budget"`).
   This lowers the odds; it does not settle them.
2. **Then INSPECT the delivered set.** From `get_artifact_meta(<the final scenario_set_id>)`'s
   `detail.scenarios[]`, read each row's `walk_trace` / flow ids and `current_elements`. If ANY
   scenario still sits on the excluded flow, delete it and **top up / re-draw** to the target count
   (the same top-up path Step 3b already uses for a short batch — it needs its own
   `append_loop_log.py --kind regeneration` entry).
3. **Only once the set is clean does the report line ship.** A "removed" line over a set that
   still carries the flow is a false statement to the customer about their own suite — worse than
   no line at all.

**Pass `previous_scenario_set_id=$(cat "$WORKDIR/.scenario_set_id")`** — here and on every Step 3
loop regeneration. It keeps the dimension graph's FLOW IDS stable across the rebuild, which is
what stops the eval set from silently unscoping every time the loop edits an input: each policy's
`target_flows` / `current_elements` is keyed on those ids, and a rebuild otherwise renames flows
that did not change (measured live: 0 of 7 and 1 of 8 ids surviving). Unrelated to the adoption
argument below and never mutually exclusive with it — pass it even when adoption is refused,
which is exactly when the graph is being rebuilt. See the call contract in `${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/generate-scenarios-call.md`.

**Do NOT carry `redraws=0` over from the qualification batches.** It belongs to every batch run to
MEASURE and to none run to DELIVER: this batch is the deliverable, so a failed slot SHOULD be
replaced rather than left short. Omit the argument entirely and the backend applies its default.

<a id="the-final-bundle"></a>
**Carry the REGRESSIONS and the TRACE SCENARIOS on this batch — it is the only batch that can carry
them.** Every folder under `plurai_sim/regressions/` replays one confirmed agent issue
`/record-regression` wrote — during a fix round, or at this build's gate exit from a confirmed
finding (SKILL.md, "Gate exit — record regressions from the confirmed findings"), which ran just
before this step so its folders ride here. Every folder under `plurai_sim/trace_scenarios/`
continues one production conversation, cut just before the turn a trace failure showed on. The
qualification batches never see either (they measure the machinery on a small sample; a reproducer
is not machinery), and the quality loop's re-draws never see them either. The deliverable does, or
the issues and the failures ship unguarded and the folders sit on disk describing a coverage that
does not exist.

**Stage both directories as ONE bundle.** `ingest-bundle` takes one directory and the generation
call takes one `additional_bundle_id`, so the two directories are merged into a clean staging
directory with one `BUNDLE.md` describing the merged set:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/final_bundle.py" stage --workdir "$WORKDIR" --out "$ART/final_bundle" \
  > "$ART/final_bundle.json"
echo "stage exit=$?"; cat "$ART/final_bundle.json"
```

It prints `{folders, regressions, trace_scenarios, held_back}` and keeps it in
`$ART/final_bundle.json` — Step 4 reads that file.

- **`folders` empty** — nothing rides. Skip the upload below, pass no `additional_bundle_id`, and
  generate as usual. Still read `held_back`.
- **Exit 2 is a STOP.** Nothing was staged. It names one of three things. An unfinished
  regression — neither its pin nor its opener: the backend does NOT refuse such a folder, it
  authors a scenario for it COLD from `ROLES.md` under the folder's name, so the set would carry a
  scenario by that name that reproduces nothing, and the membership check below, which matches by
  name, would count it carried. A folder name that would not ship as itself. Regressions alone
  over `scenario_job_max_count`. Or a harness that could not be asked about the pinned folders'
  seeds (its Init raised with no seed at all) — fix what it names. Finish, rename or delete the folder — or retire regressions whose
  issues are fixed and verified — before generating; never upload around it.
- **`held_back`** lists the folders the stage left out, each with its `reason`. The walk still
  ships. A regression is never held back, except one whose seed the harness refuses at Init
  (`harness_cannot_replay`); route its `detail` to `/simulation-setup-python`.
  - `anonymized_posture`: `plurai.config.yaml` declares `anonymize: true`, and a trace scenario is
    a production conversation, verbatim, so none is uploaded from this env. Every trace scenario
    is held back until the posture is raw again; never change the posture from this step.
  - `unfinished`: the folder has no pin — its conversion never finished. `/trace-to-scenarios`
    finishes it, or delete the folder; never from this step.
  - `harness_cannot_replay`: the harness's own Init refused the folder's seed, so every run of it
    would die in the init handshake; the `detail` is the harness's words. On a Python harness
    (`simulate.py`), `/simulation-setup-python` wires `seed_state`; on a bridge harness,
    `/simulation-setup-bridge` wires the entry's `seed`. Never wire either from this step.
  - `over_cap`: the walk, the regressions and the other trace scenarios already fill the cap.

The stage assumes the backend's default cap, `scenario_job_max_count = 50`, and the flat
`count = 7`. When the backend's cap differs (its response names it), pass `--max-count <it>`.

**Upload the staged directory as ONE bundle** — the same lane `/trace-to-scenarios` Step 4 uses,
because these are ordinary bundle folders that carry a pin or an opener:

<!-- BEGIN shared:bundle-upload-commands owner -->
```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require ingest-bundle)" || { [ $? -eq 3 ] && exit 1; }   # 3 = the resolver stopped or asked: follow the lines it printed: stop
# bash cannot see which backend the MCP server is bound to. Call the read-only backend_identity()
# MCP tool first and paste its object here verbatim.
source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" '<backend_identity() object>' || exit 1
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" ingest-bundle --workspace-root "$PLURAI_DIR" --bundle-dir "$ART/final_bundle"
#   stdout → the manifest JSON (capture it verbatim, change nothing)
cat "$ART/final_bundle/BUNDLE.md"                      # raw posture: send this
cat "$PLURAI_DIR/.plurai/uploads/overview_md.md"       # anonymized posture: send THIS instead (the file ingest-bundle named)
```

A `WARNING (disclosure: home_named)` line from `ingest-bundle` is printed before any byte leaves: relay it to
the operator in that step's own message, naming the files it lists. It does not stop the upload.

A non-zero `ingest-bundle` exit is a **HARD STOP** — nothing was uploaded. Then:

```
upload_scenario_bundle_start(
    manifest=<the captured stdout manifest, opaque>,
    overview_md=<raw posture: the BUNDLE.md content, verbatim; anonymized posture: the content of $PLURAI_DIR/.plurai/uploads/overview_md.md, the file ingest-bundle named>,
)   →  UploadFinalizeResult { set_id, status: 'done' }
```
<!-- END shared:bundle-upload-commands -->

**Do NOT write that id to `.scenario_bundle_id`.** That marker means *build the WHOLE set from
these folders*, which is the opposite of what this bundle is for, and Step 4 of
`/generate-scenarios` reads it on every later batch — a bundle id parked there turns the next
suite into seven replays of old conversations. The id is passed once, as an argument, and then
forgotten.

**Pass it as `additional_bundle_id` on the final `generate_scenarios_start` call.** The walk still
deals its full `count = 7` and draws exactly what it would have drawn with no bundle; the set then
holds those 7 **plus one scenario per staged folder**, each named by its folder — the same name in
the next build of the same suite, which is what the membership check below matches on.
**Every regression on disk when Step 2 ran is an owned obligation.** The eval set was generated with
the regressions directory as `regression_bundle_id` (`/generate-evals`), so its ledger names one
check per folder that must fail the folder's recorded reply — that check is the folder's owner in
the run report. A folder recorded after that generation (this build's gate-exit record) is in neither
this eval set nor its audits: it is owned, and checked, from the next build's generation on.
**Nothing in this build authors an eval for a trace scenario**, and the relink and additive round
below never extend the set. On a static-only suite there is no qualification batch: Step 2 authors
the eval set against the Step 1 set, which carries the folders, so their scenarios are in the
population the evals are authored for, and the set owns one check per regression folder there too.
Every folder's scenario is also graded by the set's
`grades_scenario_target` judges, against the target its authoring session derives from the folder's
`ROLES.md`, and by whatever other policy applies to any scenario. Step 4 checks that such a judge
exists at all.

**It combines with `adopt_from_scenario_set_id`, and the folders win a shared name.** Pass both
when the [adoption conditions](#adoption-conditions) hold: the backend copies the adopted
qualification scenarios, re-authors every staged folder beside them against the current
environment (folders do not count toward `count`), and leaves to the bundle any name a donor
scenario also carries. Carrying regressions or trace scenarios never forfeits adoption on its own;
the adoption conditions alone decide it.

**The cap counts both halves.** The stage fitted the folders under the cap it assumed. When the
call is still refused because `count + <folder count>` exceeds the backend's
`scenario_job_max_count`, the refusal names both numbers and nothing starts: re-stage with
`--max-count <the cap it names>`, which holds trace scenarios back to fit, and upload again. Never
drop a regression to fit: retire the ones whose issues are fixed and verified (delete the folder,
which is how a regression is retired), or ask for a cap raise at support@plurai.ai or on Discord
(https://discord.gg/bg34gh76uJ): a request, not the contact hand-off.

**Log it before launching**, naming the bundle and every folder riding it — the folders are the
whole record and nothing else indexes them. One entry per kind the stage saw; name the held-back
trace scenarios with their reason:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --kind regression --finding "final batch carries bundle <set_id>: regression_<slug>, regression_<slug>"
bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --kind trace-scenario --finding "final batch carries bundle <set_id>: trace_<slug>; held back: trace_<slug> (<reason>)"
```

**When the job lands, read what actually SHIPPED — before anything else in the tail.** A folder the
backend cannot author against the current environment **does not fail the walk**: the set completes
`done`, the coverage cards render full, and the folder's scenario is simply not in it. From
`get_artifact_meta(<the final scenario_set_id>)`:

- `detail.dataset_metadata.bundle_folders` — what shipped.
- `detail.dataset_metadata.bundle_folders_attempted` — **asked for and missing.** Written only when
  something is missing, so its presence is the whole signal.
- `detail.dataset_metadata.bundle_folders_attempted_reasons` — why each missing folder did not
  ship. Name the reason beside each folder in the blocker: an `infra_fault` needs no edit to the
  folder, a `failed_validation` needs the folder itself looked at.

Check both directories against the set rather than reading the two lists by eye:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
printf '%s\n' <the shipped scenario names, one per line> \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" list "$WORKDIR/regressions" --shipped-from -
printf '%s\n' <the shipped scenario names, one per line> \
  | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/trace_scenarios.py" list "$WORKDIR/trace_scenarios" --shipped-from - --held-back-from "$ART/final_bundle.json"
```

Exit 1 from either means at least one staged folder is not in the set. **That is a build BLOCKER
and it reaches Step 6's report by name** — never a silent gap, and never a "regressions covered"
line. A held-back trace scenario is not in the set by design; the trace listing marks it
`held_back` and does not count it. Do not re-record, re-convert or delete a folder from here: say
which folders are missing and stop. The usual cause is a seeded history that no longer coheres
with the current environment — for a trace scenario, a world that cannot return what production
returned. The remedies are all the operator's call: for a regression, a `/record-regression`
re-cut against a fresh run; for a trace scenario, an environment whose tools can return what its
world facts record (`/edit-env`), or a re-conversion at another cut with `/trace-to-scenarios`;
or a retirement. Exit 2 names an `INCOMPLETE` folder, which is never counted as carried
even when the set holds a scenario by its name — that scenario was authored cold and reproduces
nothing. Report it as the same blocker, and say the folder is unfinished rather than missing.

**A SHORT final batch that carried the bundle is topped up WITHOUT it.** The top-up above passes
`adopt_from_scenario_set_id=<the short set>` and no `additional_bundle_id` (passing it again would
re-author the folders the short set already holds); the donor's bundle scenarios ride across as adopted scenarios under their own names, count
toward `count`, and stay listed under `bundle_folders`. Re-run the membership check above against
the TOPPED-UP set: a bundle scenario that did not clear the adoption bar is missing from it, and
then the honest move is to re-run the full final batch with the bundle rather than ship a set that
lost its reproducers.

**The ADOPTION GATE — run it once, HERE, before the generation call below.** The quality loop
proved the machinery. Adoption asserts one further thing about the artifacts themselves: that
each scenario is passable at all. It reads the last qualification round's
`run_upload_id` — the one the loop pinned with `lane_pin.py`.

**CHECK THE PIN FIRST — the solvability call is a replay consumer, exactly like `score_run`.** It scores a
recording, and a recording is only evidence inside the frame it was pinned in. Seeding is by NAME
intersection, and LLM-named flow and scenario ids churn across regenerations, so a gate run
against a superseded recording can hand a name-matched, content-DIFFERENT scenario a `solvable`
verdict from another scenario's transcript — and that verdict then licenses adoption. A gate that
ran on the wrong population is indistinguishable from a gate that ran.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" check --refusing-only --gt-revision <the last one reported> \
        --eval-revision "<get_artifact_meta(<eval_set_id>).detail.eval_revision, when reported>" \
        --set-corpus-ids "<get_artifact_meta(<scenario_set_id>).detail.corpus_ids, comma-joined, when reported>" \
        --backend-build "<backend_identity().build_id; '' if you have none — that reads backend-unknown, never drift>" \
        --backend-source "<backend_identity().source_id; '' if you have none — same rule. The axis build_id cannot answer: a per-file source patch moves this and leaves build_id naming the image it patched>"
```

Exit 0 → run the call below; a `MOVED (judgement only)` headline (e.g. an eval-only repair) is still exit 0 — the
recording stands. **Exit 3 or 2 → a REFUSING axis moved, or there is no frame: no gate.**
Take the no-gate branch immediately — the last round regenerated the set, or a landing re-run
marked the pin `stale`, and re-pinning means re-running the qualification pass, not re-reading the
old one.

**No upload ⇒ no gate ⇒ no adoption.** A gate that could not run is not a clean gate: say so and
generate the final set fresh rather than adopting on a proof nobody holds.

**Solvability.**

   ```
   solvability_check_start(run_upload_id=<the qual upload>, eval_set_id=<.eval_set_id>,
                           scenario_set_id=<.scenario_set_id>, author_missing_paths=True)
   ```

   **Only a PASSING qual run is adopted as a scenario's reference.** A scenario the agent FAILED
   contributes nothing: adopting that transcript would stamp a guaranteed-violating path, get the
   scenario quarantined, and hide the exact agent defect the suite just caught. **`author_missing_paths=True`
   is what keeps that rule from making the whole check a paid no-op** — a suite worth running
   fails most of qual-1, so adoption alone resolves nothing and every seed downstream stays
   merely-observed. The authoring lane writes a reference for exactly the scenarios adoption
   could not resolve; an authored path NEVER displaces an adopted one (a real passing run is
   stronger evidence and free). Read the three name lists plus `escalation` and
   `unsatisfiable_evals` off the finished `detail`:

   | list | what it means for adoption |
   |---|---|
   | `solvable` | adoptable — and its `reference_path.json` is the scenario's proven path |
   | `untested` | nothing passed it **and nothing authored a path for it**. **Never report it unpassable** — absence of a passing run is absence of evidence. With `author_missing_paths=True` this list is what the authoring lane could not resolve either; read `candidate_sources` for the reason before you act. Exclude it from adoption, carry the failure mode into the replacement generation's steering, and escalate it |
   | `quarantined` | a proven reference an eval amendment invalidated; a scored run REFUSES threads for it, so it must not ride into the deliverable |

   **THE QUAL-1 CALL IS THE ONE THAT MATTERS, and it does not block the critic.** Step 3's item 1
   starts it right after the qual-1 upload and goes straight on to the critic; it runs under the critic
   wait (~10-20 min at count=3) and is polled to `0` before the critic's report is TRIAGED, the first
   step that reads it.

   **One scenario class can never be authored: a TRACE-RESUMED scenario** (one carrying
   `resume_seed.yaml`). The author is handed the opener and the ground truth, never the prior
   conversation, so its path would be scored as a fresh thread that opens mid-conversation and
   stamped unsolvable for an incoherence that never happened. Those scenarios stay
   ADOPTION-ONLY — proven by a real passing run or left `untested` — and `candidate_sources`
   says exactly that. Do not read their `untested` as an authoring failure.

   `unsatisfiable_evals` names policies that blocked every reference path they judged — an
   EVAL-set defect no per-scenario verdict can name. Route it to `/edit-evals` this round.
   An `escalation` scenario whose `verdicts[]` entry lists `blocking_evals` that are ALL `policy_violation` rows on policies with `grades_scenario_target: true` in `policies_config.yaml` is a TARGET defect: repair `scenario_target` with `/edit-scenarios` (`edit_scenario_gt`), then re-score with `score_run`; never regenerate it.
   Read the blockers off that typed entry, never off the escalation row's `detail` prose.
   Verdicts cache on scenario content × eval fingerprints, so a re-check after an amendment
   re-scores exactly what changed. The verdict means "jointly satisfiable under this eval set",
   never "solvable by a competent agent" — word it that way in the report.

<a id="gate-outputs-are-computed"></a>
**Every count written into `loop_log.md` is COMPUTED in the same command that writes it** — `jq`, or python over the job payload — never recalled from reading. The ledger is evidence for later steps (the adoption test parses it), so a wrong count does not stop at the note it is written in. The adoption instance of this rule is script-enforced at [condition (a)](#adoption-conditions); this is the general norm.

**Termination is ONE amend round.** Amend then re-check happens once here, with blocker escalation; there is no critic pass at 3b — after the one round the landing triage lands what is proven and reports what is not. The red-cell audit on the final run (step 4 below) is not a critic pass, so this bound stands.

**Both adoption call sites are governed by this one run.** The generation below, and the top-up
re-invoke further down that passes `adopt_from_scenario_set_id=<the short set>`: the top-up adopts
out of a set this gate already ruled on, so it needs no second gate — but a scenario the gate
excluded must not reappear in its adopted list.

**When adoption is REFUSED, the proofs do not travel — and ANY fresh set costs them, a refusal
being only one way to get one.** A scenario-scope input edit made AFTER the batch forfeits
adoption, a critic scenario-locus finding forfeits it, and the backend refuses on its own
input-hash bar; in each case the final set is generated FRESH. A fresh set shares no scenario with
the qualification set — every slot is renamed and its content regenerated, even where the flow ids
are kept — so nothing measured before it reads the population being shipped. **So a
fresh final set is RUN once, inside this arc, before it lands** — after triage step 5's audit,
within the same ONE amend round (no second full critic):

1. first, with no model call, read back every drawn final world: write each final scenario's `get_scenario` JSON (as
   returned, never reshaped) to one file under `$ART/final_worlds/`, then `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" "$WORKDIR/simulate.py" --seed-world "$ART/final_worlds"`; a `seed_readback_gap` is advisory (the harness lane's one round, else overrule with the reason and name it as a risk: those scenarios will be voided). **Progress fill**
   `suite-final-run` (the SKILL's "Progress fills"), then run the final set with Step 3's run primitive as the FINAL scored run — `FINAL_RUN=1` at the top of each `/simulation-run` block that resolves the stage (Step 2b and the launch block, whose `source …/runner-stage-flags.sh` and `"${RUN_FLAGS[@]}"` build its flags; never re-type them), never a qualification pass — and upload it (the final stage is bound at 4);
2. **Progress fill** `suite-final-check` (the SKILL's "Progress fills") first. Then
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" relink --run-upload <that upload> --eval-revision "<its run_summary.json eval_revision>"`; then start step 3's job and, before polling it, list step 4's red cells (`red_cells.py list`, the corrections reference's step 1) and spawn its auditors in that same turn — the audit never waits on step 3's poll; step 3's envelope is read while they run, and step 4's repairs read it;
3. `suite_diagnostics_start(run_upload_id=<the FINAL run's upload>, eval_set_id=<the CURRENT .eval_set_id>, scenario_set_id=<the FINAL scenario set>, run_stage='final', current_harness_fingerprint=<HARNESS_FP>)` (its first call through the SKILL's Step 0 job lines, `--phase final_evals`; the overrules re-call and step 4's call-again start fresh jobs, each recorded), its finished `job_status` envelope saved to `$ART/suite_diagnostics_final.json` (`mcp_call.py job_status … >`), which Step 6's `lane_pin.py anchor` reads. A holding finding on its `detail.gate_authority.findings` is yours to decide per `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`: repair it, or — with evidence it is wrong for this agent — call again with the same arguments plus `overrules=[{gate_id, fingerprint, reason}]` and save THAT job's envelope instead; never in the hand-off or a report. An `attribution.*` finding on a policy this build already proved the agent's is overruled by script, not by judgement: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/red_cells.py" attribution --report "$ART/suite_diagnostics_final.json" --findings "$ART/agent_findings.md"` plus `--run-dir <this final run>` once step 4 has its audit, and when its `new` is non-zero, call again with its `overrules` added to any you decided. A `status: block` whose `remedy` is `fix_env_rerun_scenarios` is not the suite's last word either: before the suite ends **not yet qualified** on it, apply it once as `/simulation-run` Step 4c's bounded repair does — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bounded_repair.py" claim --out <the final run dir> --report "$ART/suite_diagnostics_final.json"`, then one `/edit-env` fed with the lost scenarios' recorded errors and one re-run of only the scenarios it names into the same run — and call this step again on that upload; exit 3 (already claimed) means the gate stands as it is.
4. run the red-cell audit (`/analyze-simulation` step 6, its per-item tool-answer question included) ONCE over this final run's red cells and sampled greens — every trial's, as the corrections reference's step 1 lists them — and land its rows within the same amend round. That audit is the one step 2 spawned: wait for its auditors here and never spawn a second set for this run. Its rows land as follows: the `ENV` rows → one `/edit-env` + a re-run of only the items they name (a `mockless` bench: step 6's no-env-lane route instead), then ONE delta audit over that re-run's fresh cells only: run step 6's same `red_cells.py list ... --split-dir "$ART/red_cells" --greens 24 --workdir "$WORKDIR"` line, and the auditor's ONLY input is the per-scenario files it writes there for the re-run scenarios (their red units and sampled greens) — never the whole list, never another scenario's file; its rows are saved to this run's audit with `--stage final` and uploaded as the corrections reference's step 1 says, with the re-run's `run_upload_id` (it runs only when this repair re-ran an item); each `gt@scenario` finding of step 3's report and each `target_clause` `FALSE RED` → `edit_scenario_gt` (the latter on that scenario's `scenario_target`) + `score_run`. Then call step 3 again on the upload that carries them (only when one of those repairs ran). Then apply the final-run corrections to this run, in the order `${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/references/final-run-corrections.md` sets — the same procedure `/simulation-run` Step 4d runs on an official run: the `prompt` labels with `--stage final` BEFORE any correction; then, when the ENV rows' repair above re-ran no item, read whether the run's evidence is voided — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/eval_rounds.py" voided --audit "<the final run>"`. Exit 3 (at least half its conversations carry a `forced` ENV row: the agent never reached what those scenarios are about): log it (`append_loop_log.py --kind note --lane eval --finding "final run voided: <forced> of <conversations> conversations — <its missing>"`) and the hand-off says, in product terms, that the final run needs a re-run once what the world could not answer is fixed. It records nothing else and blocks nothing. **No eval edit follows this audit** — no `/edit-evals`, no rescore, no delta audit of an eval edit: each `FALSE RED` and `FALSE PASS` is corrected in place below, and fixing the eval is left to the eval optimizer and the next build. Only when `[ "${PLURAI_POSTFINAL_EVAL_ROUND:-}" = 1 ]` and `voided` did not exit 3 (edits learned on voided replies would be learned on voided evidence), ONE bounded eval round runs here, so the official run does not re-discover what this audit found: one `/edit-evals` session over the `rubric`, `harness` and `type` `FALSE RED` and `FALSE PASS` rows and every policy `eval_rounds.py schedule` names for this audit (`/analyze-simulation` step 6 — it runs when every cause is `prompt` too) (`prompt` `FALSE RED`s under `prompt_only`), its own rescore of this run, and one delta audit over the units that rescore turned red plus the sampled greens, listed by step 6's same `red_cells.py list ... --greens 24 --workdir "$WORKDIR"` line (never `--greens 0`) — the LANDING's eval-side budget (what one edit + one rescore cannot fix and re-verify stays a correction) under `/edit-evals`' retry rule; then the rulings still standing become the official run's known patterns, written BEFORE the correction clears those rulings:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/red_cells.py" patterns --run-dir "$(cat "$WORKDIR/.last_run")" --out "$ART/audit_patterns.json"
   ```

   then, before the re-render, each `policy_never_scored` finding of step 3's report is named untested — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/eval_rounds.py" never-scored --report "$ART/suite_diagnostics_final.json" --loop-log "$ART/loop_log.md" --run-dir "<the final run>" --name-untested` (exit 3: a loop-log entry ACCEPTED one — it never is);
   then `verdict_corrections.py build` LAST, after any re-run above, then, after its `check`, name the evals its corrections found at fault — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/eval_rounds.py" flagged --run-dir "<the final run>" --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")" --out "$ART/flagged_evals.json"` (each eval the audit ruled `FALSE RED` / `FALSE PASS` with a `prompt`, `rubric`, `type` or `target_clause` cause, by its display name, with each `rubric` / `type` ruling, which the next build's eval generation reads; Step 5's `evals.md` and Step 6's hand-off read it) — then the re-render, the re-attach, `correct-scores` and the records, and last the revision the bounded round left when it ran, which `artifact_deps` reads as current (unrecorded, every verdict rescored at it reads `eval-revision-moved`):

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase final_evals --status landed \
     --eval-revision "<the bounded round's end_edit_session(...).eval_revision, when reported>"
   ```

   These steps are pre-authorized by the invocation — never ask consent for them.

A fresh set whose run could not happen is UNVALIDATED rather than letting the qualification run read
as covering it: say so.

<a id="adoption-conditions"></a>
**Reuse the qualification scenarios when — and only when — they earned it.** Pass
`adopt_from_scenario_set_id=$(cat "$WORKDIR/.scenario_set_id")` — every generation writes that
file, so it holds the LAST qualification set even after the loop regenerated the batch, and
this step overwrites it with the final set only afterwards — whether or not the batch carries bundle folders
([above](#the-final-bundle): pass the bundle's `additional_bundle_id` on the same call) —
**and only if ALL THREE hold**:
(a) the loop made **no SCENARIO-scope input edits** (`simulation_prd.md` /
`scenario_variables.yaml` / `scenario_state_schema.yaml` / schemas / briefs) after that
qualification batch was generated — **decide this with the script, never by counting**:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --adoption-test --donor-set "$(cat "$WORKDIR/.scenario_set_id")"
```

Exit 0 → condition (a) holds. **Any non-zero → forfeited**; it prints the entries that
triggered it and a ready-to-paste `--note` naming them, which is what the refusal note owes
the next round. It reads three things a COUNT of `input-edit` entries cannot: when the batch
was generated (the `job` entry naming the donor set), when each edit landed, and which lane
the edited FILE feeds (`--scope`). An entry with no `--scope` is counted as scenario-scope and
forfeits, fail-closed; an edit nobody logged still changes the inputs, and the backend refuses
the adoption on its own input-hash bar either way —
(b) the **final critic pass raised no finding whose fix-locus is a scenario** in that batch —
a scenario the critic flagged must never ride into the deliverable, whatever the backend's own
records say — and (c) the adoption gate above returned it `solvable`: an `untested` or
`quarantined` scenario is excluded from the adopted list, because neither is a scenario a run has
been shown to pass. When passed, the backend adopts only the scenarios that ALSO clear its own bar
(artifacts complete, no HOLDING gate finding names it — anything less is skipped
and a fresh scenario fills the slot; a non-holding finding rides across with the scenario), counts them toward the 7, and generates the remainder planned
around the flows they already cover. The tool REFUSES when the inputs differ or nothing
qualifies — on refusal, rerun this same call without the parameter; never "fix" the refusal by
editing inputs to match. The `coverage.adopted_scenarios` field on the final job names what was
reused; the count saved is generation the customer does not pay for twice. Condition (a) is why
`/analyze-simulation` prices a SCENARIO-scope input edit as forfeiting adoption: the loop decides
here whether this saving exists, by when it chose to edit and which file it chose to edit.

**Final count rule — `count = 7`, flat.** Not a floor, not a formula: seven scenarios is
the deliverable size regardless of what the qualification job's `coverage.recommendation`
suggests. Step 1 told you to ignore that raise-count advice; it stays ignored here. The
backend sizes it for a full coverage sweep (up to 14 on a wide agent), and a suite that
takes half a day to generate is worse product than a sharp seven the user actually
reviews — breadth is what a follow-up round is for, not the first delivery. If the user
has set an explicit budget/count, their number wins.

**A final batch that ships SHORT is TOPPED UP, not disclosed.** The dead slots take flows
with them, and every policy scoped to those flows goes dead set-wide — so re-run this same
call ONCE with `adopt_from_scenario_set_id=<the short set>`: the shipped scenarios are
adopted, only the dead slots are regenerated, and the backend's own shortfall warning
already says to re-run for a fresh budget. The two-condition adoption test above governs
adopting the QUALIFICATION set. **Two things this call inherits from the Step 3 top-up rule, and
neither is optional here:** `count` stays the ORIGINAL total (the backend refuses any `count` at
or below the number of adoptable scenarios), and the re-drive contract is byte-diffed against the
donor except for sixteen exempt fields — so if you repaired anything between the batch and its
top-up, read the exemption list in `SKILL.md` before re-running. Do not assume nothing was edited:
a short batch usually arrives WITH a defect to repair, which is exactly when the diff bites.
Do this before anything below — the marker, the relink, the audit and the render all key
on the final set id, so a set topped up after them is a set nothing re-audits. Only a SECOND
consecutive shortfall is landed and disclosed, as the report's one run-integrity line (Step 6).
If the top-up ships fewer scenarios than the set it adopted from, land that set instead.

**Uncovered flows never raise the count — and never reach the report.** If the
recommendation is present (the backend emits it only when flows were left uncovered), do
not raise the count and do not narrate the gap in the hand-off: the coverage charts and
scenarios.md in `artifacts/` carry the full covered/uncovered picture for a user who
opens them. The tools-reached count in the provenance line stays honest — that number IS the
disclosure; a breadth round is a follow-up the user can choose from the artifacts.

**State the time before launching:** one line — "generating the final 7
scenarios: expect ~20–70 minutes of autonomous work, with progress updates" — wall-clock
only, never dollars. This is the longest uninterruptible step of the whole product; the
user consents to it here, not at hour two.

**OPEN the phase before you make the call, not after it returns.** This is the longest
uninterruptible job in the product and therefore the one most likely to be interrupted; a
`started` record with no matching `landed` is the ONLY thing that tells the next session it died
INSIDE this phase rather than before it. Without it every record says a phase finished, so
`phase resume` can only ever point at the phase AFTER this one — which is the behaviour that
loses a set the backend actually produced:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
  --phase final_scenarios --status started
```

- **RECORD THE PHASE FIRST, then overwrite the marker.** The id the final batch produced is
  written by the step that produced it, before anything else reads it — a measured session died
  at 21:50 with its final batch landing at 21:57, and the only record of which set was current
  was a marker file that still named the superseded one:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
  "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
    --phase final_scenarios --status landed --set scenario_set=<the new set id>
  ```

- **Overwrite `$WORKDIR/.scenario_set_id` with the new set id** — this final set is
  what Steps 4–6 report and what `/simulation-run` scores next. Leave the qualification
  set on the backend (its run artifacts reference it); don't delete it.
- **Immediately RELINK the eval set to the final set**: call
  `relink_eval_scenario_set(eval_set_id=<eval_set_id>, scenario_set_id=<final set id>)`.
  The eval set still records the QUALIFICATION set it was born against, and every
  set-dependent audit on it — **guard coverage above all** (an `applicability_guard`
  false on every scenario is skipped set-wide and grades NOTHING), the memory-probe
  census, the knowledge mandate, source fidelity — describes that old population until
  the link moves. Observed live: a session-authored guard that matched no scenario
  SHIPPED because the relink (and therefore the dead-guard audit) never ran on what
  shipped. Read the result: any `guard_coverage` finding names a policy that will grade
  nothing on the final set — fix it via `/edit-evals` before Step 4; act on the other
  findings per the error posture. (Check `guard_coverage_audited` before trusting an
  empty list.) It REFUSES in two states, both reachable right here: while the eval job is
  still non-terminal, and when the new set has no readable `out/` yet — poll `job_status`
  until the final set is terminal, then retry; the link is unchanged on a refusal. If you
  regenerate evals below, the fresh set is born against the final set and needs no relink.
  A `vacuous by undelivered demand:` warning is the one finding here that is NOT an `/edit-evals`
  task: it names a policy scoped to a flow (or guarded on a complication) this batch was ASKED to
  deliver and did not, so the judge is scoped correctly and the missing half is a scenario. Carry
  it to 3b.9 as an accepted gap; re-scoping it would hide the hole rather than close it.
  A `dead_flow_scope` finding is likewise MECHANICAL: it names a policy whose `target_flows` /
  `flow_filter` matches no flow the linked set declares and carries the closest id that set DOES
  declare — apply that remap in ONE `/edit-evals` round. Left alone the policy grades nothing where
  flows are declared and runs UNSCOPED where they are not, so it can report a vacuous PASS rather
  than a visible SKIP.
  ANY premise warning that names a concrete recommended `applicability_guard` — whatever the
  backend labels it (`premise coverage:`, an outcome-premise note) — is a MECHANICAL fix:
  apply it in ONE `/edit-evals` round — copy the recommended guard verbatim (the backend
  commit gate validates it) — then record what was applied in `$ART/loop_log.md`
  (`append_loop_log.py --kind note --lane eval`). Apply it unless you hold evidence from the customer's agent, PRD or run
  that the premise gap is not real for THIS agent — then leave it unapplied (it holds nothing, so
  there is nothing to overrule) and record why in `$ART/loop_log.md`; never leave it as prose
  nobody acted on. A
  premise warning naming no concrete guard is not mechanical — decide it per that doc rather
  than guessing a guard.
- **Then move the eval lane's pin onto the relinked set** — one call, right after the relink,
  and the VERB records which branch of the adoption gate you took:

  ```bash
  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" adopt    # ONE of the two: `adopt` when the final set ADOPTED the qualification scenarios, `relink` when adoption was REFUSED and the set was generated FRESH
  ```

  The pin says which world the saved artifacts are a recording OF, and the relink moves the set
  ids without moving the recording. Left alone the pin keeps naming the QUALIFICATION set and
  `lane_pin.py check` reports `scenario-set-moved` — a regeneration anomaly — for the designed
  end state of the relink (observed live: a shipped suite whose pin still named its qual set).
  Either verb re-points the ids and declares the frame STALE in one step — `set-adopted` or
  `set-relinked` — so the lane must not write a rescore sidecar against the recording until a
  run of the relinked set is uploaded. The two reasons are not interchangeable: `set-adopted`
  says the recording still holds scenarios the final set contains; `set-relinked` says it holds none of them. Run `relink`, never `adopt`, on the
  "adoption is REFUSED" branch.
**Run the tail as ONE turn, not as a queue of decisions.** Poll the final generation as a wait in the
background (which is what re-invokes you when it lands — never a
detached `nohup`); a turn that ends on it takes the in-progress anchor `Suite: in progress`.
Wait for it in the FOREGROUND instead and the same rule as Step 0c applies — **`timeout:
600000` on the Bash call**, because the harness default is 120 000 ms and `poll_job.sh`'s own
budget is 540 s, so a bare call is killed 420 s inside its budget and reads as a wedged
backend. When the terminal notification arrives, do all of the following in that same
turn, in this order, without pausing between them: **check the count and top up a SHORT batch
first** (above — everything after this keys on the final set id) → relink → `lane_pin.py adopt` (or
`relink` on a fresh set) → act on its
`guard_coverage` findings → the per-judge triage below → AUDIT the set against the final suite, close its gaps additively, and read the coverage diff (triage step 5)
→ re-derive the population-scoped report copy (triage step 6) → Step 4's `suite.json` → Step 5's render. Most of these are genuinely ordered: `relink_eval_scenario_set` refuses while the eval job is
non-terminal or the new set has no readable `out/`; the additive round runs only on the triage's KEEP
branch and rewrites the mirror the coverage diff then reads; and Step 4 needs the final set's
metadata. The render is the one independent tail — a stdlib-only script over `suite.json`. Nothing here is a decision point that needs the user, so a turn
that ends between them is a turn nothing resumes.

- **Then decide, per judge, whether to re-run `/generate-evals`** — the decision below, never a
  reflex, and **KEEP is the default**. A regeneration REPLACES the whole backend eval set, so it
  also discards every `/edit-evals` fix this loop just verified — the set you throw away is the
  only one a run has ever tested, and the one you author instead ships to the customer untested.
  That is the cost side, and it is why the decision is per judge, by READING, never by agent type.

  **Corpus-backing is not the answer to this question.** Planted knowledge ground truth — the
  question, its expected answer, its grounding contexts — is injected into the judge PER SCENARIO
  at scoring time, from the linked set's `knowledge_plan.yaml`, on every scoring path. The eval
  recipe accordingly forbids hardcoding any of it into a prompt and tells knowledge judges to
  grade the injected block BY REFERENCE. So a correctly-authored knowledge judge is set-agnostic:
  relinking re-points it at the final set's planted questions and it grades them, unchanged.

  A judge goes stale only if it BAKED the old set's content into its own text — and that is a
  property of the judge you can read, not of the agent. Work it in this order:

  **Before regenerating anything, inventory the fixes you would lose.** List every `/edit-evals`
  change this loop made (they live in the backend set the regeneration replaces) with its policy
  name and what it changed. After the new set lands, re-apply the surviving ones through
  `/edit-evals` — the gated path, which validates them. Do NOT try to carry them by writing an
  `authoring_constraints` block (or any other invented key) into `intent_yaml`: no schema
  validates it and no backend code reads it, so it looks like it worked and changes nothing.
  1. **Find the coupled judges from the TYPED field.** In the policies mirror
     (`$WORKDIR/policies_config.yaml`), a policy with **`grades_scenario_target: true`** is the
     one the runtime injects each scenario's target into. That flag is the signal — not the name.
  2. **Then READ each one and ask whether it grades a target GENERICALLY.** A judge that states
     its rule literally ("the agent must address the user's stated goal before closing") grades
     any target and is **KEPT as-is** — no regeneration, nothing to carry forward. This is the
     common case, and it is the branch to try first.
  3. **Regenerate only for a judge tuned to the qualification scenarios' specific targets** —
     one that names their entities, quotes their expected answers, or hard-codes their flows.
     That judge would grade the final scenarios against targets they do not have. The test is
     what the judge's own text CONTAINS; it is not satisfied by what the judge is about.
     **Knowledge judges are not automatically in this bucket** — planted questions and their
     expected answers are injected per scenario at scoring time from the linked set's
     `knowledge_plan.yaml`, and the recipe forbids copying any of it into a prompt, so a judge
     that grades the injected block by reference re-points itself at the final set on relink.
     One that instead QUOTES a qualification answer is stale for exactly the reason above, and
     the quote is what puts it here. Read it and see; do not infer it from the agent being
     corpus-backed.
  4. **Legacy fallback, for sets authored before the typed field existed** (no policy in the
     mirror carries `grades_scenario_target` at all): a `*target*`/`*adherence*`-named judge is a
     **suspicion to check under step 2**, never a verdict on its own. Observed live:
     `response_budget_adherence` — a content-agnostic judge about response length — matched the
     name rule, and regenerating on that match alone would have discarded seven verified fixes.
  5. **Then AUDIT the set against the final suite, and close only what the audit names —
     additively, in ONE `/edit-evals` round.** A KEPT set was authored against the 3-scenario
     qualification sample and the final suite has more scenarios; a REGENERATED set is born
     against the final suite, so its ledger is fresh — but a complication no policy guards is
     exactly as likely there, so **the audit and the structural read below run on both branches**
     (only the stale-ledger paragraph that follows is KEEP-specific). The triage
     above asked whether any judge goes stale against the new set; this asks the other direction:
     what the final suite carries that no judge grades. A relink moves which population the AUDITS
     speak about; it authors nothing. **NO NEW POLICY IS AUTHORED HERE — no eval job runs after
     the loop.** (The one policy added here is not authored: the backend's verbatim ledger check, per
     the `scenario fact binding:` row below.) A policy written here would have no scored run, no critic and no diagnostics
     behind it, so it would ship as a claim. The deliverable's population is graded by the
     policies the loop verified; what it still needs from the eval side is an ACCOUNTING against
     its own population and, rarely, one of those policies' rubrics EXTENDED to name an
     obligation nothing grades. Both are one bounded additive edit, never `generate_evals_start`.

     **A relink flags its own accounting.** `relink_eval_scenario_set` warns
     `COVERAGE LEDGER STALE BY CONSTRUCTION` whenever it MOVES the link: the ledger was authored
     for the previous population and no step in the pipeline re-authors it — the relink
     deliberately does not, because the link is a NAME mapping and renaming rows would make the
     ledger silently wrong instead of loudly stale. Treat that warning as the instruction it is:
     re-author the accounting through a targeted additive `/edit-evals`, and do not read the
     target/obligation findings beside it as evidence that anything is ungraded.

     **Call `eval_coverage_audit(eval_set_id=<the CURRENT .eval_set_id>, scenario_set_id=<the
     FINAL scenario set>, run_stage='final')` and read its computed fields — never re-derive
     them.** Read-only, no LLM, seconds. What it reports is the finding set; decide on it per
     `${CLAUDE_PLUGIN_ROOT}/docs/gate-findings.md`. A holding finding you hold evidence against
     is overruled on the audit itself: call `eval_coverage_audit(…)` again with the same arguments
     plus `overrules=[{gate_id, fingerprint, reason}]` copied from the finding (in the save command
     below, an `"overrules"` key in its JSON) and save THAT result — its `release_gate` already has
     the overrule applied. Never tell the user about it.

     **SAVE the audit to `$ART/coverage_audit_final.json`.** Step 6's `lane_pin.py anchor` computes
     the provenance line from that FILE, not from the transcript — with no file it lands on the
     floor rung and the guard then requires that floor rung in the hand-off, on a suite that ran
     fully. Fetch it to disk rather than transcribing what you just read; the rung is supposed to be
     computed from the backend's own bytes, and a hand-copied payload is the authored answer again:

     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" eval_coverage_audit \
       "{\"eval_set_id\": \"$(cat "$WORKDIR/.eval_set_id")\", \"scenario_set_id\": \"$(cat "$WORKDIR/.scenario_set_id")\", \"run_stage\": \"final\"}" \
       > "$ART/coverage_audit_final.json"
     ```

     **Quote the SAVED FILE, not the reply above it.** The two reads are seconds apart against a
     set that can move, so a number taken from the tool reply while the anchor binds the file is
     the one shape this whole step exists to prevent: a hand-off whose prose and whose rung come
     from different bytes. One artifact is authoritative and it is the one on disk.

     The additive round below re-audits; save that one as `coverage_audit_final2.json`, same
     command with the name changed. The anchor reads the newest ROUND, so the suffix is what tells
     it which audit governs.
     **Pass `run_stage='final'`, ALWAYS.** The parameter defaults to `'qualification'` and omitting
     it selects that silently — the audit then answers as though this were still the sample, and its
     guard-coverage gate returns `status: advisory` with the words "This *would* block a final
     scored run". Bound with `run_stage='final'` the same gate returns `status: block` and "This
     BLOCKS the final scored run". Same population, same numbers, two different verdicts, and only
     one of them is about the set that ships. Two builds of the same week split on exactly this: one
     passed the argument and got the block, one omitted it and audited its deliverable advisory. The
     artifact records which you got as `guard_coverage_gate.stage` — read it back and say so. It returns
     the uncovered targets and obligations, the undecided intent items, and any duplicated
     `AXIS:` — the same mechanical bound the eval job authors against, read off the population
     that ships.
     **Pass the population.** Without `scenario_set_id` the audit answers about the eval set
     alone and every target/obligation row it reads is the one authored for the QUALIFICATION
     population — which is the ledger the relink just warned is stale by construction. Observed
     live: a shipped suite whose audit was run unbound and landed with 24 of 29 ledger
     obligations unrowed against the population that actually ships.
     **Read `targets_audited` / `ledger_audited` / `axes_audited` before reading an empty
     finding list**: each list is empty both when there is nothing to report and when the input
     was unreadable, and `ledger_audited: false` means nothing STATES what is covered — the
     opposite of "nothing is uncovered". Report which of the three you got.

     Read `targets_graded_by` before the target findings: non-empty means a target judge with no
     guard, flow or polarity scope grades every scenario target, and no `uncovered_target` is
     emitted. An `uncovered_obligation` detail says `ACCOUNTING gap` when a set-level policy may
     already carry it. That is the difference between a grading gap and an accounting one.

     The additive session publishes NEW policies only. A row whose remedy edits an existing policy
     (extend a rubric, re-scope, declare `target_complications`) runs in one non-additive
     `/edit-evals` session on the same set; the final run scores it.

     | what the audit returns | what to do |
     |---|---|
     | `uncovered_target` (present only when `targets_graded_by` is empty) | a REAL gap: no judge grades this target on every scenario. Write its `target` row in `out/obligations.yaml` naming the scoped policy whose rubric grades it here; if none does, EXTEND an existing policy's rubric to name it — never a new guarded judge — or write the row with a classed `declined_reason` that says why |
     | `uncovered_obligation` whose detail says `ACCOUNTING gap` | nothing may be ungraded; the ledger is. Read the set-level rubrics the detail names and write its row in `out/obligations.yaml` naming the one that grades it |
     | `uncovered_obligation` whose detail does NOT say a set-level policy already carries it | read the rubrics it names. An obligation a rubric grades gets its row. One nothing grades is carried by EXTENDING an existing policy's rubric to name it — a policy the loop already ran — never by a new policy and never by a new guard; if no shipped policy can carry it, its row carries a classed `declined_reason` that says why |
     | `undecided_intent_item` | decide it in `out/obligations.yaml`: an `intent_item` row naming the policy that grades it, or one with a classed `declined_reason`. A precondition NO final scenario plants is a decline that names the missing scenario — never a new guarded judge |
     | `duplicate_axis` | a RE-SCOPE of the owner, never a new policy |
     | an audit finding whose `evidence.line` starts `scenario fact binding:` and says `no code_eval of this set is bound to it` (the relink carries it too) | MECHANICAL, and not authored here: the line quotes the backend's own ledger check. Add that ONE `code_eval` in this additive session with the expression and guard copied verbatim from the line — one check for every flagged fact, never one per kind or key. If the set already has a ledger check, the warning asks to WIDEN it: an edit, in the non-additive session. Record it in `$ART/loop_log.md` (`append_loop_log.py --kind note --lane eval`); the final run scores it. A `scenario fact binding:` line that says `no ledger check can grade it` is the FACT's to fix (`/edit-scenarios`), never an eval edit; one that says the audit did NOT run is unmeasured |
     | an audit finding whose `evidence.line` starts `decline ownership:` (the relink carries it too) | an EDIT, in the one non-additive `/edit-evals` session, which closes every such line: EXTEND the set's judge that grades the agent's offers and refusals — author one there only if none does — so its rubric grades the refusal of the named ask in every mode (offered, started, promised or done), and declare the named complication in its `target_complications`. Never close it on a `code_eval`: it reads calls, not words. Declaring adds a dealing demand and never narrows the judge when it runs. Record it in `$ART/loop_log.md` (`append_loop_log.py --kind note --lane eval`) |
     | only `stale_ledger` | the accounting alone — still this one session, because nothing else in the pipeline writes a row |
     | `ledger_audited: false` / `targets_audited: false` | the input was unreadable, so nothing above is decided. Say which flag, and treat it as unmeasured — not as a clean set |
     | `release_gate.named_risks` carries the eval as `kind: untested_premise` (its `untested: [{complication, demanded, planted}]`; the seating gap the gate moved off its violations at the final stage) | the set could not SEAT that situation, so the eval has no result either way on it — a named "not tested here" risk, NOT a blocking defect, and never a pass. Record it in `$ART/loop_log.md` as a `note` (`append_loop_log.py --kind note --lane scenario`) naming the eval, each complication with its planted/demanded and the `planting_basis` the audit returned — never `blocking-unresolved`, which `lane_pin.py anchor` caps at `not yet qualified`; the named risk itself caps the rung at `Suite: qualified`. The suite's own final run does not re-plant it (an official `/simulation-run` re-plants a `replant` once before it scores); this build carries the premise to the next build's draw. A `block_causes` entry on the SAME eval under `under_planted` (a runtime gap: seated but not delivered) is still the row below |
     | `release_gate.block_causes` carries `{check: <the eval>, cause: under_planted}` (an older backend with no `block_causes`: `guard_coverage_gate.status: block` with the eval named in `under_planted`) | the DRAW came up short, not the judge: this population never seated the premise its guard needs, so nothing measured the eval at all — the same shortfall the relink reports from the other side as `vacuous by undelivered demand`. Not re-scopable into a pass, and never a stop: a named risk that caps the rung, as the row above. Record it in `$ART/loop_log.md` as a `note` (`append_loop_log.py --kind note --lane scenario`) — never `blocking-unresolved` — naming the SET's shortfall, the `planting_basis` the audit returned and the count its `guard_coverage_gate.slot_plan` quotes (`satisfiable_at` — or, where that is null or the plan carries a `scope`, the gate's own sentence that no count exists and why). The suite's own final run does not re-plant it (an official `/simulation-run` re-plants a `replant_premise` once before it scores); you carry the repair to the NEXT build's draw inputs at that count, where `generate-scenarios` authors it |
     | `release_gate.block_causes` carries the eval under any other cause (an older backend: `guard_coverage_gate.status: block` on an eval NOT in `under_planted`) | NOT closeable by a note, and not closeable by this session. It says a high-severity eval measured under half the run, so the set reports a coverage the run does not have. Fix what you can from the additive session; record whatever survives in `$ART/loop_log.md` (`append_loop_log.py --kind blocking-unresolved --surface eval --policy <eval>`) as **BLOCKING — UNRESOLVED**, naming the eval and its admitted/total (`ledger_name_facts_stated (2/7)`). the customer's run reaches its own `release_gate` from what THAT run measured — the scenarios each eval decided, and for a premise-bound eval only those that planted its premise — and never reads this record, so recording it does not clear it |
     | `guard_coverage_gate.status: block` on an eval that a `release_gate.block_causes` list (present, so the gate types its causes) does not carry | the release gate is the EFFECTIVE verdict: an overrule recorded on the eval set released that cause, so nothing blocks on it. With no `block_causes` key the older-backend fallbacks above decide instead. Record no `blocking-unresolved` entry for it — `lane_pin.py anchor` caps the rung on every such entry, and keying one on the raw sub-gate would re-impose the cause the overrule released |
     | `guard_coverage_gate.status: advisory` at `stage: qualification` | you did not pass `run_stage='final'`. This row is not a verdict about the set that ships — re-run the audit bound before reading anything below it |

     **Read the FINAL job's whole composition record, not a sample of it, and record the composition
     facts in `$ART/loop_log.md`.** The job states what it actually composed in
     `detail.dataset_metadata` (the per-flow delivered counts in `coverage_report`, the
     `steering_demands` ledger, and `coverage_shortfall`), and the record is long enough that quoting
     a few rows reads as a summary. These are working facts for the loop and they belong in the loop
     log — this does not license quoting them into a customer report. Carry any `coverage_shortfall` forward as a `mandated_flows` entry on the
     NEXT build rather than re-drawing here; a re-draw at 3b is still forbidden.

     **Re-check the deferral queue against THIS audit before landing.** `$ART/deferrals.md` entries
     written during qualification carry qualification-era numbers, and a deferral checked off `[x]`
     against `admits 0/3` is answered by a fact that no longer describes the population — the final
     audit says `2/7` and lives only in `coverage_audit_final*.json`, which no markdown references.
     Any queued item whose evidence is a guard number must be re-read against the final audit and
     re-stated or re-opened; an item that still says the old number is stale whether or not it is
     ticked.

     Close what it returns with ONE targeted **additive** `/edit-evals` session
     (`edit_session_start(target_kind='evals', target_id=<the CURRENT .eval_set_id>, additive=True, workspace_id=<bench workspace id>, ledger_version=<the pin>)`,
     the id being the stdout of `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"`):
     in that mode the already-committed policies publish byte-verbatim whatever the session does,
     so closing a gap cannot disturb the judges this loop just validated. The set keeps its id —
     the marker does not move. Never a full regeneration, and never `extend_eval_set_id`: it
     authors against the final suite with no run to check the result, which is the one shape this
     step exists to keep out of the deliverable (`/generate-evals` documents the by-hand case it
     still serves).

     Then read `get_artifact_meta(<scenario_set_id>)`'s per-scenario `current_elements` +
     `scenario_target` (`detail.scenarios[]`) against the eval mirror for what the audit cannot
     see — a complication type or element dimension no policy grades. **That reading is
     STRUCTURAL, and its silence is not coverage:** it matches dimensions against policy
     DESCRIPTIONS, so a dimension a policy merely NAMES reads as graded — a policy whose own
     gates are mutually exclusive (a guard and a `target_flows` naming disjoint populations)
     passes it while grading nothing at all. So report it TOGETHER WITH what the backend's own
     probes returned on this set — the audit's findings, and the relink job's `guard_coverage` /
     `dead_flow_scope` findings and any premise/conjunction probe beside them, applied per the
     relink rules above — and never present a clean structural diff as the whole answer. A
     complication the final draw carries that no policy's guard names is the suite's own gap,
     internal like every coverage hole: name THE FINDING in `$ART/loop_log.md` only — "no
     policy grades `<complication>`" — never in scenarios.md, evals.md or the hand-off, and add
     "no scenario in the loop exercised it" only when
     the qualification draw did not carry it either (the loop's `current_elements` say which).
     A precondition the loop never drew is planted and verified by the loop's own draw
     (Step 1's `steering_demands`) on the next build — not closed here by a judge nothing ran.
  6. **Re-derive the population-scoped report copy.** Any sentence in `$ART/evals.md`,
     `$ART/scenarios.md` or the hand-off that counts scenarios, names flows, or describes what
     the eval signal covers was authored against the QUALIFICATION set and is now describing a
     population that does not ship. The ledger is LLM-authored — nothing re-rows it
     deterministically — so re-author those passages from the FINAL set's
     `get_artifact_meta(<final scenario_set_id>)` + the audit you just ran. Copy that still
     names the qual set's scenario count or flow ids is a wrong claim about the deliverable, not
     a stale draft.
  7. **LAND `final_evals`, on whichever branch you took.** The eval side of Step 3b is done here,
     and `resume` after a landed `final_scenarios` names `final_evals` — so a session that dies
     anywhere below is sent back into this whole triage unless it was recorded. The KEEP branch
     keeps the qualification set's id and the full-regeneration branch minted its own at its call;
     neither recorded `final_evals`, so every branch reaches here having recorded nothing:

     ```bash
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
     source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
     "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record \
       --phase final_evals --status landed --set eval_set="$(cat "$WORKDIR/.eval_set_id")"
     ```

     Read the id back from the MARKER rather than typing the one you remember: on the branches
     that reach here it is the set that ships, and re-recording the id already on record is what
     keeps `phase resume` at exit 0.
- An adopted final set is **not** re-run through the gate — the gate certified the machinery,
  and the final scenarios come from that certified machinery. Treat generation-job
  warnings per the error posture as usual.
- Skip this step only in the static-only case (Step 1 already generated `count=7`, carrying the
  [merged bundle](#the-final-bundle) itself).

### 3b.9 — CONFIRM the backend's demand ledger, then check what it could not

**READ THE BACKEND'S ANSWER FIRST — do not re-derive it.** Every demand you typed
[above](#typed-demands) was measured against the delivered set by the job itself, off the same
post-audit flow credits and enacted-complication sets the coverage report uses. Re-deriving that
by eye is how two operators produce two answers about one number.

1. **The typed ledger.** `get_artifact_meta(<the final scenario_set_id>)` →
   `detail.dataset_metadata.steering_demands`, one row per demand:
   `{kind, subject, slots, delivered, measured_off}`. Any row with `delivered < slots` is an
   under-delivered demand, already measured. A row pinning a confirmed finding's (or a carried critical's) `**trigger:**` with
   `delivered < slots` is that finding's **NOT-EXERCISED** in the run-integrity line — never an
   accepted gap. Stated only while the finding's `agent_findings.jsonl` row reads `scored_run.reproduced: 0` (`scored_findings.py record`; no `scored_run` reads the same); once the scored run reproduced it, the line says instead that it reproduced in k of N conversations (`reproduced` of `conversations`) while its planted situation was set up on d of s (`delivered` of `slots`). The ledger counts fresh conversations only, so a finding whose own regression replay ran in this run WAS exercised by that replay: its line gives the replay's reading (replayed, reproduced in k of N conversations or not reproduced), never NOT-EXERCISED. **An ABSENT key is not a clean ledger** — it means
   this batch passed no typed demand at all (or ran on a backend that predates them), so nothing
   was checked and step 3 below is the only check there is; say which of the two you are in.
2. **Read the ledger rows against what could be measured.** The ledger counts the MECHANICAL
   demands only: steering prose is step 3's, never "all delivered". Two cases need their own
   handling:
   - A row whose `subject` names a flow or complication id this run does not have (a typo) can
     never be delivered. Its `delivered: 0` is **not** a coverage gap, and re-running buys
     nothing: fix the subject, or drop the demand.
   - When the number of scenarios a row was measured off (`measured_off`) is smaller than
     `scenarios_completed`, a demand reported short may have been delivered by a scenario nobody
     could read. Read the ledger against that number.
3. **Then check the demands the ledger CANNOT hold** — everything you kept as prose (a challenge
   shape, a narrative condition, a family), which is still a request the walk was free to
   decline. From `detail.scenarios[]`: `current_elements`, `scenario_target`, `walk_trace` / flow
   ids and title. One demand, one verdict, named: **covered** (say which scenario) or
   **uncovered**. A stage whose `contingent_on` never fired, or a demand carried to the deferrals, did
   not deliver an explicit ask: that demand is **uncovered**. List what you asked for mechanically rather than from memory — the log is the
   record:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" --list re-steer
   ```

   One `<stamp> — <finding>` per line. Add the `interest` string and `typical_complications` you
   passed on THIS batch; those are arguments, not log entries, so they are the one part you
   supply. An EMPTY listing is itself a finding: demands you made but never logged cannot be
   verified by anything downstream.
4. **Every shortfall — typed or prose — becomes a VISIBLE accepted gap.** The slot plan already
   seated every demand that fit, a refused plan included, so a typed shortfall is what the draw
   could not deliver — not a count to raise. Write it to `$ART/loop_log.md` so it survives the
   turn, and carry it into Step 6's accepted-gaps count (the `Suite: qualified` branch), never
   into the customer-facing gap prose:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
     --kind note --lane scenario --finding "steering demand NOT delivered: <the demand>" \
     --note "checked against <scenario_set_id>; accepted as a gap"
   ```

**A shortfall can make a JUDGE vacuous, and the relink says which.** `relink_eval_scenario_set`
(run just above) cross-references the same ledger against the eval set and warns
`vacuous by undelivered demand:` for every policy scoped to an under-delivered flow — or guarded
on an under-delivered complication. That policy will ship green on this population having graded
nothing: a `<policy_id>` scoped to a flow the walk never delivered reports a clean pass over zero
cases, and a clean pass over zero cases is indistinguishable from a real one downstream.
**Treat each one as an accepted gap named in the log, not as an `/edit-evals` task**: the judge
is scoped correctly and the missing half is a SCENARIO, so re-scoping it would only hide the
hole. The final coverage gate reports the same judge below its floor; that row is the record of
the gap, not a cue to re-draw. Ran the relink before the final set materialized? Then run it again — the flag is computed
from the linked set's ledger, so it says nothing about a set it was not pointed at.

**Do NOT re-draw to chase an uncovered demand.** A redraw re-runs the same weighted walk and
misses it again at the same rate, and the deliverable batch has already been paid for. An
uncovered demand is a gap this suite ships with and says so — that is the honest outcome, and a
silent full-looking set is the one this checklist exists to prevent.

**Presentation is Step 6's job** — it states only the provenance this run earned
(validated / qualified-with-gaps / not-yet-qualified). Do not summarise the suite here.

## Step 4 — build `$ART/suite.json`

`suite.json` is written by `build_suite_json.py` (Step 5) from the FINAL set (the current
`.scenario_set_id`), never the qualification sample. Every field it can read off a file, it reads; you
write only the overlay below. `references/example_suite.json` is a worked output. **Do not** dump
fetched scenario YAML into the repo — read it, derive, discard. **The ids:** `scenarios[].id` is the scenario's run folder name; `evals[].id` is its `policies_config.yaml` key.
Each eval row's `kind` (`llm_judge` / `code` / `hybrid`) is derived from the policy's `type`, and its `prompt_summary`,
`rule` and `group` are the backend's copy — never author or edit any of them.

**The set's metadata**, verbatim:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
rm -f "$ART/suite_missing.json" "$ART/suite_declined.json" "$ART/suite_ungraded.json"   # this build's answers only
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" get_artifact_meta "{\"id\": \"$(cat "$WORKDIR/.scenario_set_id")\"}" > "$ART/final_set_meta.json"
```

A non-zero exit leaves no usable file: run it again. The builder refuses an answer whose job is not
`done`.

**Capability gaps** — the builder copies `detail.dataset_metadata.capability_gaps` into `suite.json`
when the meta carries any.

**Regressions and trace scenarios that did not ship** — whenever `$ART/final_bundle.json` names a
folder in `folders` or `held_back` ([Step 3b](#the-final-bundle), or Step 1 on a static-only suite):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/final_bundle.py" missing "$ART/final_set_meta.json" \
  --workdir "$WORKDIR" --staged "$ART/final_bundle.json" > "$ART/suite_missing.json"
```

**Exit 0** — every staged folder reached the set. **Exit 1** — the file is a JSON object holding
`missing_regressions: […]`, `missing_trace_scenarios: […]` or both, each entry `{folder, reason}`; the
builder copies them. The reason is the backend's `bundle_folders_attempted_reasons` entry
(`infra_fault` — not judged, the cause is outside the folder; `failed_validation`; `session_fault`;
`unknown`), or `not_handed_over` for a folder the build never gave it. **Exit 2** — the metadata could
not be read, which is a different answer from "nothing missing": report it in Step 6 as a build blocker
naming what was unreadable, and pass the builder no `suite_missing.json`. A folder the stage held back is
in neither list: the builder writes `held_back_trace_scenarios` (`{folder, reason}`) from
`final_bundle.json`'s `held_back`.

**Regressions declined on the record:**
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_call.py" job_status "{\"id\": \"$(cat "$WORKDIR/.eval_set_id")\"}" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" ledger-owners - --declined > "$ART/suite_declined.json"`.
It prints `{}` when none is declined, else `declined_regressions: [{folder, reason}, …]`.

**Ungraded on their target — internal, never the customer's** — after the relink and the
additive `/edit-evals` round have settled the eval mirror, and only when the delivered set shipped a
regression or a trace scenario. In the run, each of those is judged on the concern it exists for only through the
`scenario_target` its authoring session derived from its `ROLES.md` (a regression's owning check is judged on the recorded
reply, a different question), and only an `llm_as_judge` policy declaring `grades_scenario_target: true`
receives a target:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/final_bundle.py" target-graded "$WORKDIR/policies_config.yaml" \
  --meta "$ART/final_set_meta.json" > "$ART/suite_ungraded.json"
```

**Exit 0** — a target grader exists, or no folder shipped. **Exit 1** — stdout's `ungraded_on_target`
names every shipped regression and trace scenario, so it goes into `suite.json` as `ungraded_on_target: […]` and log it for the operator:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --kind note --finding "ungraded on target: <the names> — no llm_as_judge policy declares grades_scenario_target"
```

**Exit 2** — the mirror or the metadata could not be read: log that the same way and pass no
`suite_ungraded.json`. Whether the eval set grades a scenario's target is eval-set fitness: Step 6 never
mentions it, the renderer never shows it, and no customer message carries it. Do not author or edit a
policy from here to clear it — no eval job runs after the loop.

**The overlay — `$ART/suite_overlay.json`, the only fields you author.** The builder refuses any other
key, and refuses a scenario that ends up with no `title` or `summary`:

```json
{"scenarios": {"<scenario id>": {"title": "…", "summary": "…"}}, "requested_count": 7,
 "knowledge": {"per_document": {"<doc>": 6}, "set_aside_documents": {"<doc>": 1}}}
```

Each scenario's `title` and `summary` come from its **scenario card**, `$ART/scenarios/<id>/scenario_card.yaml`
(the backend writes it; the slot fetch lands it): the builder copies the card into `suite.json` and uses its `title`
and `details`. Leave a carded scenario out of `scenarios`. Author a row only for a scenario whose slot has no card
(or whose card the builder left out as stale or malformed; it says so on stderr). Read that scenario with
`get_scenario(scenario_set_id, scenario_name=<id>)` and write, for the customer reviewing their agent's results,
in plain language with concrete specifics and no internal vocabulary:

  - **`title`** — the situation the agent must handle, in 5–10 words, sentence case, starting with "User"
    and stating what the user expects or does. Name the gotcha, not the user's task. Phrase it as a
    situation, not an assertion, so it does not read like an eval name. No quotes, digits, paths or
    proper names. E.g. *"User expects the agent to remember her format preference"*.
  - **`summary`** — the user's requests in order, as one or two sentences, with the real specifics:
    file paths, names, values, and any rejection or follow-up. Derive it from the opening message
    (`first_talk`) and the user's later lines, never from the path math. E.g. *"She asks for slides from
    Research/Heron Survey 2026/field-notes.md, then a one-page brief for panel chair Omar Ibrahim, and
    rejects the first email draft as too casual."*

A title or summary is display text: fix a wrong one by editing the overlay and re-running Step 5's build and
render, never by regenerating scenarios.

  - **`card`** — only when the publish's customer-text check (`customer_text_guard.py`, an `INTERNAL` line on
    `suite.json`) flags a string that came from a scenario card: rewrite that one string, `{"<scenario id>": {"card": {"<field>": "…"}}}`. The rewritable fields are `title`,
    `what_we_test`, `gotcha`, `why_not_trivial`, `coverage_adds`, `persona_line1`, `persona_line2`,
    `details` (text) and `behaviors` (a list of short labels). The builder ignores any other key, says so on
    stderr, and builds on.
- **`requested_count`** — the `count` passed to the final `generate_scenarios_start` (Step 3b). The
  render opens `scenarios.md` with `> **N of M requested scenarios shipped**` when fewer walk scenarios
  shipped; omit it and a short set reads exactly like a complete one. Never put cost anywhere in the spec.
- **Knowledge** — only when the suite has a corpus; omit the key otherwise. `per_document` — the
  `per_document` map on `plurai ingest-corpus --extract-only`'s stdout, which prints one per corpus and
  merges nothing, so merge them; omit the key when that stdout carries none (an older SDK);
  `set_aside_documents` — each corpus build's `job_status(<corpus_id>)` `detail.meta.set_aside_documents`,
  merged. The builder adds the set's `knowledge_reach` and the grading policies itself.

**What the builder derives** (fix a wrong value at its source, never in `suite.json`):
`coverage` is the backend's block verbatim plus its cards — first `simulated tools reached` (K of the M tools on the map
that the scored run called, `(at least)` when the run's `run_note` makes the call record partial; also written as
`coverage.reached`), then `valid paths covered` with the
verified/declared split and the auditor-only counts (`null` stays `null`), `core components exercised`
over the flows outside `complication_flows` (left out when the meta has none). Both flow-share cards are left out
whenever a tool is typed `in_no_flow`, because the flow graph then omits part of the agent; `likelihood`,
`complexity` (bits) and `stars` are the walk's recorded numbers (a row with no recorded walk keeps its
`complexity_level`, unit-less); `persona` = `persona.yaml` parsed and copied as it came (string or dict), never reshaped or shortened;
`ground_truth` is the graded `scenario_target`, byte-for-byte; `goal_check` is the scenario's
`success_criteria.deterministic` (label, expression, rationale) as it came, which `evals.md` lists; `world_state_note` counts
`initial_world_state.yaml`'s entities; each eval's type, scope and score come off its policy.
- **Environment** — `environment.schema` from `scenario_state_schema.yaml` + the entity types in
  `plurai.config.yaml`; `bounds` / `noise` stay empty (trace-derived — see "Flag").

## Step 5 — fetch the slot bundles, then render

**Materialize the shipped set into `$ART/scenarios/` first** — `agent_context.yaml` carries any
generated document inline. The backend is asked which files the set has and exactly those are pulled,
so pass no slot names unless you want a subset. A set already pulled is a cache hit; a new one replaces
the previous set whole, which moves to `artifacts/history/`.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/report_pieces.py" bundle "$WORKDIR" --set-id "$(cat "$WORKDIR/.scenario_set_id")"
```

Exit 1 is a real failure — a fetch broke, or a slot has no `scenario.yaml`. **Exit 3 is INCONCLUSIVE**:
the set has no file manifest yet, so re-run it; do not render as though the slots were empty.

**Then re-run the smoke**, which is the only moment its turn-1 request block has anything to read:
`verify_smoke.py` builds that block from `artifacts/scenarios/*/agent_context.yaml`, which the
fetch above has just written. Run at Step 9f — before any scenario set existed — it could only
report `⚠ request block: EMPTY`, so the bench was certified against an envelope no run will send.

```bash
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" "$WORKDIR/simulate.py"
```

Run it on every route: where the manifest declares `harness.tool_interception.at: agent_host`, it drives no
Init, prints `~ smoke: not_applicable`, and checks the request block statically, so do not skip it there.

Then build `suite.json` from Step 4's files and render it:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # $ART — re-source in THIS block
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
args=()
[ -f "$ART/final_bundle.json" ] && args+=(--bundle "$ART/final_bundle.json")
for f in suite_missing suite_declined suite_ungraded; do [ -f "$ART/$f.json" ] && args+=(--regressions "$ART/$f.json"); done
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/scripts/build_suite_json.py" --art "$ART" \
  --policies "$WORKDIR/policies_config.yaml" --overlay "$ART/suite_overlay.json" "${args[@]}" \
  && python3 "${CLAUDE_PLUGIN_ROOT}/skills/simulation-generate-suite/scripts/render_suite.py" \
  "$ART/suite.json" --artifacts-dir "$ART" \
  && mkdir -p "$ART/.pieces" && cp "$ART/suite_overlay.json" "$ART/.pieces/suite_overlay.json" \
  && "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/artifact_deps.py" record "$WORKDIR" --suite
```

Exit 3 names the scenarios that have no `title` or `summary` from either the overlay or a scenario card:
author them in the overlay and run it again.
Exit 2 names the input it could not use or the overlay key it refuses. The last two lines keep the overlay
for later rebuilds and record what this suite was built from.

Then render the **qualification-scope block** beside them — ONE block naming what the suite's
verdicts are evidence about (client pin, backend image build, `eval_revision`, passes used, critic
verdict, and one limitation line per coordinate that could NOT be read):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" scope > "$ART/qualification_scope.md"
```

**Loop-internal, like the loop log — it never goes in the customer hand-off** (Step 6 bans
limitations and build chronology outright). It is the block an internal report or a PR that cites
this suite as proof pastes, and every line of it is derived — including its limitations, so it
names the coordinates nobody recorded and the ones that MOVED since the pin. Exit 2 still writes a
block (a `NO READABLE FRAME` one): an arc nothing pinned is not scoped, and that is the finding.

Then release the client pin Step 0 armed (a hand-written one is left alone):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/client_pin.py" release --by simulation-generate-suite
```

Stdlib-only. Writes `$ART/scenarios.md`, `$ART/evals.md`,
`$ART/charts/{scenarios_coverage,scenarios_complexity,environment}.svg`,
`$ART/scenarios/<id>.md`, and `$ART/render_notes.txt` when it has anything to say. Any
section whose data is absent is omitted cleanly.

Then STAGE the reader files — the managed block (rev 7) un-ignores them precisely so a clone
holds the suite a human can read, and un-ignoring is not tracking:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/stage-inputs.sh" artifacts/scenarios.md artifacts/evals.md artifacts/suite.json
```

**READ what the renderer reports, and act on it BEFORE Step 6.** Every `render_suite:` line
names a field it could not use and the surface to re-read for it; the same lines land in
`$ART/render_notes.txt`. A missing field renders as `—`, which is indistinguishable from "the
backend had no value" — so a quiet report is not evidence of a complete suite. Fix the overlay or
re-read the source it names, then build and render again until the renderer is silent, or say once in the hand-off that the value does not
exist on this backend. Never edit a rendered file to hide a `—`.

**The render is authoritative and it PRUNES.** Re-rendering after Step 3b deletes the
per-scenario pages of the superseded set and any chart this spec no longer produces, so the
report can't link scenarios that aren't in the suite (nothing outside those renderer-owned
names is touched). The corollary: **a hand-edited line does not survive a re-render** — if a
line in the report is wrong, fix its source or the overlay, never `suite.json` or the output file.
The complexity explainer follows `complexity_unit`, so the fallback above renders as the
generator's 1–5 level and never claims bits.

**Then land the phase, once the renderer is silent and the reader files are staged.** `render` is
a declared phase, so `resume` names it after `final_evals` and a session that dies between the
render and the hand-off is otherwise sent back through Step 4's `suite.json` build for artifacts
that are already on disk:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase render --status landed
```

**Exit 2 with `MISSING <path> — <step>` lines is a failed step, not a landed one.** Both `render landed`
and `report landed` refuse, and write nothing, while a file the report page reads is absent (the table
is `${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py`; `report` also needs this `render landed`). Re-run
each named step once, then record again; still missing is a failure hand-off naming the file.

The verb refuses (exit 2, `ERROR: the render did not finish`) unless `render_suite.py` ran to the
end over the `suite.json` now on disk. A refusal is the renderer's failure: read its error, fix the
input it names, and render again. Never record the phase over a traceback.

## Step 6 — present the suite (the report skeleton is the CONTRACT)

**Close the loop's own records first** — land the report phase:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" phase record --phase report --status landed
```

Same rule as scan-agent: the files are the review surface — **don't re-narrate them**.
(The gate ran on the 3-scenario qualification sample; the suite you're presenting is the
final set generated by that same machinery — Step 3b.)

**Print every link the hand-off carries, and paste each line verbatim** — `scenarios.md` and
`evals.md` always, and the findings line below when it is earned. Each Markdown line is the file's own
link (a path, or a `file://` URL on Copilot and Codex); never type a path, which opens on no surface the
reader has. Publish none of these files and `open` none: the overview page below is the only published
page, and it carries the suite. During a running /simulate run a Markdown line prints nothing, and the
hand-off simply leaves that link out:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"   # the shell resets between calls
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Scenarios →" "$ART/scenarios.md"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Evals →" "$ART/evals.md"
```

The findings line's link is the overview page's Gaps Stage (its scored-run findings), printed AFTER the Overview
page step below has published, never the raw `agent_findings.md` (the internal working copy):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Agent findings →" "$ART/report.html" --anchor gaps            # confirmed findings
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/report_link.py" link "Potential agent issues →" "$ART/report.html" --anchor gaps    # all-potential
```

<!-- BEGIN report-page: generated from ${CLAUDE_PLUGIN_ROOT}/docs/report-page-block.md; edit there -->
**Overview page — after this hand-off's Markdown, never in its place.**

First check that this stage left every file the page reads:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/deliverables.py" check suite --workdir "$WORKDIR"
```

Exit 3 names each missing file and the step that writes it: re-run that step once and check again. A file
still missing makes this a failure, never a success: run the contact hand-off (`stop_message.py contact --done`). Exit 4 names each
customer line that carries our own machinery: rewrite it as the line says (or move it to the internal file it
names) and check again.

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" fill "$WORKDIR" --step suite --flowdir "$FLOWDIR"
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
"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/publish_report.py" done "$WORKDIR" --step suite --flowdir "$FLOWDIR" --url "<the URL the Artifact tool returned, or - when the publish failed or is unavailable>"
```

End the hand-off with the line(s) it prints, verbatim. Never narrate the fill, the publish, a refusal or a
fallback.
<!-- END report-page -->

**The final message IS the skeleton below, filled in — nothing else.** It presents the
PRODUCT: what the suite catches, and the next action. It never recounts the build: no
iteration counts, no critic history, no "remaining work", no defects you found and fixed
along the way, no spend — **and no gap list of any kind** (no "What to watch", no
limitations section, no uncovered-flow narration; the tools-reached count in the provenance line
plus the rendered artifacts are the full disclosure, and doctrine item 3 makes this the
rule, not an omission). The report_guard hook validates this message — a violating render
is blocked and you re-render to the skeleton.

**Every ban in that paragraph is about the SUITE.** Defects the suite caught in the
**customer's agent** are the opposite of build chronology — they are the product working,
and doctrine item 3 names them explicitly as report content. They have exactly one licensed
slot, the agent-findings line below; "nothing else" does not close it.

**COMPUTE the provenance line — do not pick it.** Run this AFTER the final coverage audit has
landed (Step 3b, including any additive round's `coverage_audit_final2.json`) and immediately
before you compose the hand-off:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lane_pin.py" anchor || echo "ANCHOR FAILED — stop and fix this"
```

It reads the lane pin and the audit you saved in Step 3b (a fresh set: its run's `suite_diagnostics_final.json`), prints the ONE line this run's state
licenses, and records it for the guard. When the scored run carries the SDK's scores, it prints a SECOND line, `Agent: Ship /
Don't ship / No verdict — <reasons>`: the verdict on the customer's AGENT, which is a different thing from the suite rung
above it. Before a final scored run, the scored run is the qualification run, and the line says so: `Agent, from the
qualification run (N scenarios): …`, ending "The final run gives the verdict on the full suite." Never shorten it to
`Agent: …`: only the audited final run's verdict is the agent's, and that run can still stop early (a final run
not yet audited prints no Agent line). Put it directly under the
anchor line, as its own line, never merged into it or reworded. **Use what it prints, verbatim.** `report_guard` requires
that exact line, so a rung chosen from the ladder below instead of computed will be blocked. It never licenses
`validated` over an unclean scored measurement, a critic older than the last harness or environment edit (log each such
edit as `harness-fix` / `env-fix`), or a loop no client pin governed.

Audit, then anchor, then message — anchoring before the last audit records a rung the audit is
about to overturn.

The verb must PRINT a rung and exit 0. A non-zero exit means it could not record the anchor, and
the hand-off is unanchored: stop and fix it rather than composing the message anyway.

The ladder below is the vocabulary the verb chooses FROM — here so a reader knows what each rung
means, not so the session picks one:

> **Suite: validated** — N scenarios · M evals · K of M simulated tools reached · quality gate ✓. Catches &lt;the 2-3 sharpest behaviours, in the customer's domain language&gt;.

— only when a fresh independent critic returned a CLEAN report (Step 3).

> **Suite: qualified** — N scenarios · M evals · K of M simulated tools reached. Catches &lt;the 2-3 sharpest behaviours&gt;.

— when the loop LANDED with accepted gaps (Step 3's landing pass). The gaps themselves
stay out of the message — the anchor word "qualified" (vs "validated") plus the coverage
number carry the provenance difference; the artifacts carry the detail.

> **Suite: qualified (ungated set)** — N scenarios · M evals · K of M simulated tools reached. Catches &lt;the 2-3 sharpest behaviours&gt;.

— when the final set was generated FRESH and adoption was REFUSED, and its own run could not
happen. No gate read the population actually shipping; "ungated set"
is the part that must survive editing, because it is the only thing separating this from the
rung above.

> **Suite: not yet qualified** — N scenarios · M evals · K of M simulated tools reached. &lt;why, in product terms&gt;.

— on the static-only path, where no qualification run happened at all (the why reads "No run was
possible (&lt;what stopped it&gt;)"); when the loop landed with `blocking-unresolved` entries that still affect the
final results (the why names what still blocks, in the words of `suite_anchor.json`'s `blocking_live`: "`<eval>` still
has 2 results our check found wrong and could not correct"); and whenever
the verb could not establish the state it needed. Say in the hand-off what could not be established.
When the final release verdict BLOCKS, the line `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/release_verdict.py" render --report "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["audit"])' "$ART/suite_anchor.json")"` prints replaces the `<why>` — the report is the one the anchor read (`suite_anchor.json`'s `audit`).

`K of M` is `suite.json` `coverage.reached` (`tools` of `of`): the simulated tools on the map that the scored run
actually called, the same numbers as the first coverage card. Write `at least K of M` when `coverage.reached.floor` is
true. With no `coverage.reached` (no scored run) leave the clause out — never a flow percentage, which is a share of
the flow graph, not of the agent's tools.

**Judge-side coverage DEBT decides between the first two lines, and the rollup decides it — not
your reading of the run.** Three carriers report it under one name: the scenario job's and the
eval job's `detail.coverage_debt`, and the `score_run` RESULT dict's `coverage_debt`; each sets
`coverage_debt_blocking` (`any item not waived`) beside it. On the FINAL/delivery set ONE
unwaived item is enough — a policy that reached no verdict on any scenario, a guard false
set-wide, a must-catch polarity left unmeasured, a pinned probe nothing grades, planned knowledge
turns unrealized set-wide. `Suite: qualified` is then the best line available and `validated` is
not, until each item is resolved or explicitly accepted with a recorded reason. A green suite
that tests no knowledge because its knowledge judges were suppressed is the shape this rule
exists to stop shipping as validated.

**The ADOPTION GATE demotes on the same axis, read off the solvability verdicts.** `validated` claims the machinery
was proved; two states mean it was not, and each makes `qualified` the best line available: the gate could not run at all
(no qualification upload, or a pin that had moved), or the solvability check returned a non-empty `untested` /
`quarantined` list.

**A suite built over FIX-FIRST findings demotes too.** Read the contract the way Step 0 did —
`"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" contract-gate` — never
`scan_contract.json` directly (a STALE artifact still says `proceed: true`). Any exit but `0`
demotes: `1` means the suite was built over a finding that still holds, `2`/`3`/`4` mean no
current verdict exists. The scenarios and evals carry those premises, so `validated` is not
available — `qualified` is. A finding Step 0 overruled no longer holds: the gate reads `0` over it
and nothing demotes.
The one exception is a `3` whose gate line reads `as not_applicable` (the recorded status, not a
path or reason that merely contains the word): the bench declares no corpus, so no verdict was owed,
and it neither demotes nor blocks `validated`. Every other `3` demotes.

**Census findings NEVER demote.** Flow coverage at count=3, a short-by-design qualification
batch, thin-corpus notes: those describe what a qualification sample IS, not a gap in what the
final set grades. A rule that fires on every suite stops meaning anything.

**Record the items VERBATIM; never re-derive them.** A job's `coverage_debt` and a
`score_run` rollup carry no row of their own in the loop log, so record the items with
`append_loop_log.py --kind note --lane eval`. Never rebuild the list from
job findings or from the Eval-signal table — a second construction of the same finding drifts from
the first, and then the artifacts and the report disagree about what shipped. Accepting one is a
WRITTEN record, not a decision held in the turn:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/bin/plurai-in-workdir" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/append_loop_log.py" \
  --kind note --lane eval --finding "coverage debt ACCEPTED: <kind> · <subject>" --note "<why it is acceptable>"
```

That entry is what Step 6 counts into the `Suite: qualified` branch; an item accepted nowhere is
not accepted. The items themselves stay OUT of the customer message like every other gap — the
anchor word plus the artifacts are the disclosure.

**Those three are hand-offs. A turn may end MID-flow only while the harness holds work that
will re-invoke the session — a run or wait in the background (never a
detached `nohup`), a spawned critic. Such a turn takes the in-progress anchor instead:
`Suite: in progress`.** Between passes — artifacts in, the next critic not yet spawned — is
NOT such a state: spawn it (or land) in the same turn, because a turn ending there is one
nothing will resume. Every turn's last message is style-validated while the window is open,
and the anchor answers exactly one question, does this turn owe the hand-off skeleton; it
never closes the window, it is blocked when nothing is actually in flight, and it exempts
the message from nothing else (no cost figures, no question-shaped ending).

**Genuine failure is its own hand-off, not a skeleton.** If the flow itself failed — e.g.
scenario generation produced NOTHING after the error posture's re-run after the fix (a failed
job that fails again after its re-run) — there is no product to present: run the contact hand-off (`stop_message.py contact --done`), and its output, verbatim, is the final message, with no ids and nothing else. Never dress a failed flow in a provenance line, and never loop past the
posture's re-run budget trying to avoid saying it.
The report_guard recognizes the contact hand-off as the one anchor-exempt hand-off.
Close the flow window after it, same as any other exit.

**One thing joins that message: agent findings, if the gate got far enough to collect any.**
A qualification run that completed and surfaced two defects in the customer's agent is real
work whose value does not depend on Step 3b succeeding — and the next NEW build of this command
prunes the confirmed ones away (Step 0). So when `$ART/agent_findings.md` holds sections, add
whichever of the lines above its tiers earn after the contact hand-off. That is the ONLY addition; nothing
about the build joins it.

**One exception to "no gap list": a run-INTEGRITY fact** (doctrine item 4) — the suite shipped
short of its target count, scenarios were never executed, trials of the presented final run did not run, a
model was substituted, a flow was REMOVED because it cannot complete inside the scenario time
budget, a recorded regression or a trace scenario did not reach the set, a trace scenario was
held back, or the suite was built without production traces. That is a RESULT about this run, not a coverage gap, and it changes how the
numbers read, so it gets exactly ONE flat line under the provenance line, no cause and no repair
story: `Run integrity — the suite shipped 5 of the 7 scenarios requested.` Nothing else about the
build joins it.

**The did-not-run sentence, verbatim** — when the hand-off presents a scored final run, the sentence about trials
that did not run is the `run_integrity` string the run summary module prints for the `run.json` the page renders:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_numbers.py" chat "$ART/runs/final-<n>/run.json"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_numbers.py" covered "$ART/runs/final-<n>/run.json"
```

Paste it as printed; `null` means no such sentence. The second call lists the situations the final run's conversations
put to the agent: the hand-off names a situation only when it is listed (`null`: name no situations as covered). The same call's `setup` (tools that run for real never succeeded),
when not null, is pasted as its own line under it; the Next line leads with fixing what it names, before
`/simulation-fixes`, only when the error it quotes plainly names a credential or a service to start. Never count `totals.errored`, errored rows or "errored rather
than failed" yourself — an errored count includes crashes the run scores as failures, so a self-counted sentence
contradicts the page's passed / failed / not-run numbers.

**The removed-flow line, verbatim** — the one case where the run-integrity line names a knob,
because the removal is reversible and the reversal is the USER's decision, not ours:

> `Run integrity — flow <X> was removed from the suite: it cannot complete inside the 40-minute scenario time budget; extending the budget (--scenario-timeout) is your call.`

Ship it ONLY after Step 3b's ["Removing a flow"](#removing-a-flow) inspection confirms no delivered
scenario still sits on that flow. No cause story, no apology, no offer to fix it for them — the
budget is theirs to set, and a scenario that needs 45 minutes is a fact about the work, not a
defect in the suite.

**The missing-regression line, verbatim** — only when `suite.json` carries
`missing_regressions` (Step 4). Each of those folders replays a confirmed agent issue that was
asked for on this build and is not in the delivered set, so the suite grades everything except the
one thing it was extended to grade. That is a fact about what shipped, which is why it rides here
rather than in a gap list:

> `Run integrity — 1 recorded regression did not reach this suite, so the agent issue it reproduces is not graded here.`

Count only — plural as `N recorded regressions did not reach this suite, so the agent issues they
reproduce are not graded here.` Keep the label and the claim in ONE sentence, as written: the
guard licenses this line by what the sentence says, and a version split across a bullet head and
its body loses the licence on exactly the clause that needs it. The folder names stay OUT of the
customer message; the renderer already writes them into `scenarios.md`, which the hand-off links. No cause story and no repair offer: the
choice between re-cutting the folder and retiring it is the operator's, and it is made against a
fresh run, not from this message.

**The declined-regression line, verbatim** — only when `suite.json` carries `declined_regressions`
(Step 4), under the same rules as the missing-regression line (one sentence, count only, folder names
in `scenarios.md`):

> `Run integrity — 1 recorded regression is declined on the record, so the agent issue it reproduces is not graded here.`

Plural: `N recorded regressions are declined on the record, so the agent issues they reproduce are not graded here.`

**The trace-scenario lines, verbatim** — each only when `suite.json` carries its key (Step 4), in
the same one-sentence form and under the same rules as the missing-regression line: count only,
folder names OUT of the customer message (the renderer writes them into `scenarios.md`), no cause
story, no repair offer.

- `missing_trace_scenarios`:
  > `Run integrity — 1 trace scenario did not reach this suite, so the production failure it replays is not graded here.`

  Plural: `N trace scenarios did not reach this suite, so the production failures they replay are
  not graded here.`
- `held_back_trace_scenarios` — one line per `reason` present:
  > `Run integrity — 1 trace scenario was held back from this suite, because the harness cannot replay a recorded conversation yet.`

  > `Run integrity — 1 trace scenario was held back from this suite, because it did not fit under the scenario cap.`

  > `Run integrity — 1 trace scenario was held back from this suite, because its conversion never finished.`

  Plural: `N trace scenarios were held back from this suite, because …`.

`ungraded_on_target` never gets a line: it is eval-set fitness, and it stays in `suite.json` and
the loop log (Step 4).

**The missing-traces line, verbatim** — only when a trace part of the pinned grounding job failed its analysis
(`detail.failed_parts` with `kind: trace_archive`). The suite went on without that part, so it was built without the
production traces. The handler prints the line, or nothing; carry what it prints as it stands:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
[ -f "$ART/.grounding_status.json" ] && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grounding_findings.py" integrity --status "$ART/.grounding_status.json"
```

> `Run integrity — built without production traces (trace analysis failed).`

No finding text, no part id and no repair offer join it.

**The regressions line, verbatim — whenever `$ART/auto_regressions.txt` exists.** The gate-exit
record step (or, on a static-only suite, Step 1) wrote it: how many of the gate's confirmed
findings became regressions in this build, which folders, and how many confirmed issues have no
regression yet. Its count is from pick time, so re-render it now, against the findings the final run
confirmed (the plan keeps the record step's refusals), then copy the file's one line as it stands:

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
if [ -f "$ART/auto_regressions.txt" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/regressions.py" auto-report "$ART/auto_regressions.json" --findings "$ART/agent_findings.md" > "$ART/auto_regressions.now" && mv "$ART/auto_regressions.now" "$ART/auto_regressions.txt"
  rm -f "$ART/auto_regressions.now"; cat "$ART/auto_regressions.txt"
fi
```

It is a RESULT about what the delivered set
guards, not build chronology, and it asks the customer nothing: an issue the line counts as having
no regression yet is still in `agent_findings.md` and reaches `/simulation-fixes` like any other.

**Capability gaps get one flat line too — only when `suite.json` carries `capability_gaps`**
(Step 4). Like run integrity, this is a RESULT about the deliverable, not build chronology: the
generator found scenario pressure the agent has no surface for. One line, count + remedy framing,
no per-gap narration (the rendered "Capability gaps" section in scenarios.md carries the detail):
`Capability gaps — 2 found; close them by refining the PRD's purpose/design-constraints per the
suggested fixes, or by adding the missing tool.`

A gap whose fix was a design-constraint line was already folded into Step 3's batched input edit,
so what reaches this line is what genuinely needs the customer's call — most often whether the
agent should grow the capability at all. Never quietly widen the remedy framing into "we can fix
this for you": that decision is theirs.

**Evals to optimize — one line, only when the final run's audit found an eval's own rule or judge wrong.** It is the one
exception to "no defects you found and fixed": when our review found a result of the delivered run wrong, the eval
behind it is flagged for improvement rather than edited after the run. Paste what this prints, as printed
(nothing printed: no line):

```bash
source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/eval_rounds.py" flagged-line --file "$ART/flagged_evals.json" --scenario-set-id "$(cat "$WORKDIR/.scenario_set_id")"
```

No count of your own, no cause and no repair offer: `evals.md`'s "Evals to optimize" section names each one's next step.

Then, on ANY of the three, the message assembles in this layout — the provenance line you
picked, plus whichever of the optional lines this run earned (each defined below in its own
section):

> &lt;the provenance line&gt;
>
> Run integrity — &lt;only when one applies&gt;
>
> Regressions: &lt;the line in `$ART/auto_regressions.txt`, when the file exists&gt;
>
> Capability gaps — &lt;only when suite.json carries them&gt;
>
> &lt;the Evals to optimize line, when `eval_rounds.py flagged-line` printed one&gt;
>
> &lt;the Next line&gt;
>
> &lt;the Scenarios link line&gt; · &lt;the Evals link line&gt;
>
> - **Already caught** — &lt;the agent-findings line&gt;
> - **Sharper next round** — &lt;the grounding invitation&gt;
>
> &lt;the overview-page line(s) the block above printed&gt;

The Next line is a dispatch:

| this build recorded in `$ART/loop_log.md` | the Next line |
|---|---|
| an agent crash — a qualification run.json row carries `error_kind: agent_raised`, or a qualification scenario's `transcript.json` carries `pipeline_io.error` | Next: **`/simulation-fixes`** — your agent raised &lt;the exception, quoted&gt; before the suite could score it; it hands you the fix, then **`/simulation-run`** to score it. |
| the hand-off presents a scored final run (`runs/final-*/run.json` rendered on the page), with no `blocking-unresolved` entry still affecting the results (`blocking — unresolved: 0`) and a final release verdict that does not BLOCK | In the suite's own run: &lt;the `passes` line `run_numbers.py chat` prints for that `run.json`, pasted verbatim — never counted by hand&gt;. Next: **`/simulation-fixes`** for the ones that failed; **`/simulation-run`** re-scores the agent after a fix. When every one passed: Next: **`/simulation-run`** re-scores it whenever the agent changes. Never "to score the agent": that run already scored it. |
| no `blocking-unresolved` entry still affecting the results (`lane_pin.py scope` prints `blocking — unresolved: 0`; an eval entry the final run re-checked, a backend entry its audit proved a false red, or a plugin/SDK entry whose checks the final run re-checked, no longer counts) | Next: **`/simulation-run`** to score the agent against it. |
| the final release verdict BLOCKS and its `remedy` is not `rebuild_suite` | no Next line: the verdict line already names the one action. |
| one or more whose remedy includes `rebuild` (`lane_pin.py scope` prints `blocking — unresolved: <N> (<surfaces>; remedy rebuild…)`: a scenario, harness or env entry, the simulated world answering wrong, or a backend check held by a scenario, harness or scanned-fact cause), or `unknown`, none of them an agent crash | Next: **`/simulation-generate-suite`** — rebuilds the suite for your agent; then **`/simulation-run`** to score it. |
| one or more, none `rebuild`, at least one `eval:<checks>` (`… remedy eval:<checks>[, ours]`: an eval entry, or a plugin/SDK entry naming a check whose verdict is still wrong or unchecked), none of them an agent crash | Next: ask me to edit the evals — repair &lt;exactly the checks after `eval:`&gt;; then **`/simulation-run`** to score it. After a presented scored final run, the first row's pass sentence leads, and the ask ends "then **`/simulation-run`** to re-score it". |
| one or more, every one ours (`… remedy ours)`: a plugin/SDK/backend entry naming no check, a crashed job, or a backend entry on a check only the backend judges that the final run did not prove wrong), none of them an agent crash | The fault is ours, and the agent's results stand: the first row's pass sentence and **`/simulation-fixes`** lead as for a clean suite (with no scored final run: Next: **`/simulation-run`** to score it). No eval ask and no rebuild. |

**Keep the blank lines.** Markdown joins consecutive lines into a single paragraph, so a
hand-off written as five lines in a row reaches the customer as one unbroken block with the
coverage number, the next command, the links and both invitations run together. The wording of
every line stays exactly as specified — this is layout, not licence to rewrite them.

**Then, when the suite already caught something in the agent — one line, and only when
`$ART/agent_findings.md` holds at least one `##` section** (the file is created with a header,
so "non-empty" is not the test). When the quality loop ran the agent against real scenarios, the
critic classified some failures as the AGENT's (Step 3) and those findings are already diagnosed;
and `/simulation-fixes` works off the qualification-run evidence sitting on disk — so the user
has a genuine second path here: go deep now, or spend a few minutes fixing what is already
known first. Give them both, without asking:

> - **Already caught** — the agent &lt;finding 1, in domain language&gt;, and the agent &lt;finding 2&gt; (&lt;line&gt;).
>   You can also fix those first with **`/simulation-fixes`** — it works off evidence already on disk, no
>   new run needed. &lt;the Agent findings link line&gt;

`<line>` is `page-counts`' `line`, pasted as printed: `N confirmed agent issues (K seen in one conversation; C not
seen again in the scored run)`, then `N potential, which may be intended` (a part of 0 is not in it). Never count a tier
yourself, never fold one tier into another, and never call a confirmed finding potential or the reverse. Name the
findings from `confirmed`, else from `calibrating`.

When `confirmed` and `calibrating` are both empty — nothing confirmed, only limitations the
build designed around — that line has no findings to name. Use this instead, and never dress a
potential up as something the suite caught:

> - **Also flagged** — N things the agent cannot currently do, which the suite had to design
>   around and which may be intended. &lt;the Potential agent issues link line&gt;

N is the number of `potential` findings `page-counts` printed — never a count of your own.

Rules for that line — the grounding invitation's shape below, sharpened for findings:

- **Name up to 2; the count is the `line`.** A line that shows two of nine reads as "we found two"; the
  `line` gives every tier's total, in the page's order, so nothing makes the reader open the link blind.
- **Name and count every tier from `page-counts`** — `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py"
  page-counts --workdir "$WORKDIR"` prints the page's `confirmed`, `calibrating` and `potential` findings (`{id,
  title}`, in the page's order) and their `line`, so the hand-off says what the page, the fix plan and the closing say.
- **Name the agent as the subject of each finding's own clause** ("the agent quotes a refund
  window the policy does not grant"), never the scenario ("scenario 2 exposed a broken refund
  path"). Both say the same thing to you; to the customer the second reads as OUR suite being
  broken, which is why report_guard blocks it — the noun set that catches suite self-flaws has
  no way to tell the two apart, so the phrasing burden is here. A blocked render is telling you
  to re-phrase, never to drop the finding.
- **It never displaces `Next:`**, which stays the single prominent step. `/simulation-run` is
  still the recommended path — the fix line is the opportunistic alternative, offered flatly —
  except after a presented scored final run, whose Next already names `/simulation-fixes`.
- **Present `/simulation-fixes` as the agent-repair step and nothing else.** Never as
  "it separates genuine agent bugs from test-setup artifacts": the suite was gate-validated,
  so offering "maybe the tests are wrong" as a reading of the customer's own failures un-earns
  the whole arc (report_guard blocks that framing, and it was a live failure).
- **No provenance.** Where a finding came from — a critic, a pass number, an iteration — is
  build chronology and is banned. State what the agent did, and stop.
- **An example quotes an audited true red from the scored final run**, verbatim with its scenario
  and turn — never a qualification run or an abandoned attempt — and states only what that turn
  shows. An example that names a finding is that finding's first evidence line in the page
  copy (`scored_findings.py page-copy`). report_guard blocks a quote found only outside the scored run.
- **An invitation, never a question**: no trailing "?", no "do you want", no "should I".
- Do NOT substitute failing evals or suite defects you fixed to have something to put there.

**Then invite more grounding — one line, only what's actually absent.** The suite was built
from whatever was available; real customer material makes the NEXT round sharper, and the
hand-off is where the user is most able to act on that. Name only the inputs that have not
been supplied — check each before mentioning it, and drop the line entirely when they are all
in place:

> - **Sharper next round** — add **production traces** (`/analyze-traces`) for real phrasing and failure
>   rates · a few **real records or conversations** (`/ground-scenarios`) so scenarios start from actual
>   data · **document samples** (`/upload-doc-examples`) when a flow involves documents.

Rules for that line: it is an invitation, never a question (a question-shaped ending is a stall
the report_guard blocks, and there is nothing to decide here — the user acts when they choose);
it is forward-looking, never a gap report about the suite you just delivered (gap narration is
banned from the hand-off entirely — doctrine item 3); and it never displaces the `Next:` action,
which stays the single prominent step. A handful of real traces or records typically moves
fidelity more than any regeneration does — say it plainly and stop.

Lead with what the suite CATCHES, in the customer's own domain language, not with what it
took to build. Name only behaviours a delivered scenario exercises; one whose pin is `NOT-EXERCISED` is never named as tested. Concrete beats adjectival: "catches a fabricated URL in a media embed and a
refusal the agent should have made" lands where "high quality" does not.

**The window closes itself on the validated hand-off** — report_guard removes the
marker when the final message passes with a provenance anchor (or the failure hand-off).
Do NOT `rm` it before rendering: a close that runs before the message ends would un-arm
the validation of the one message it exists for. An EARLY exit that ends on the contact
hand-off is that same case: render the hand-off and do not close first —
report_guard checks it there and closes the window. Only an early exit that ends on some other
turn (a precondition missing, an error-posture stop that is not that hand-off) closes it
yourself, own-marker-guarded:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close generate-suite
```

## Step 7 — optional user tweaks

The quality gate (Step 3) already drove the suite to top quality; this is for
**user-initiated** changes after that. Offer to change anything:
- **Evals** — run the **edit-evals skill**: a
  multi-turn backend session to add / modify / remove / re-threshold policies. On
  finalize it rewrites `policies_config.yaml`; then re-run Step 4–5 to refresh.
- **Scenarios** — there is no in-place editor; to change them, re-run Step 1 with
  adjusted `[context]` (or drop/add). Then re-run Step 4–5.

Re-rendering is just Step 5; Steps 1–2 don't repeat unless the user changes inputs.

<a id="harness-fix-procedure"></a>
## A HARNESS fix — the six-step procedure

Reached from Step 3's repair rounds (suite skill), which points here rather than carrying this inline:
it is a RARE path — most rounds fix evals or the env — and the always-loaded body is a fixed context
budget every round pays.

**A HARNESS fix has its own procedure, and every step of it is load-bearing.** A `simulate.py`
/ `_mocks.py` / `_storage.py` edit changes how the agent BEHAVES, and unlike an env fix it has
no probe and no cheap observer — the mock knows nothing about it, and `score_run` replays a
transcript the OLD harness produced. So:
1. **Apply the file-owner test, then apply the fix — never ask.** A harness repair is applied without a
   question in every mode and is never declined. If the fix belongs in the SDK, this plugin or the backend
   (`surface: sdk` / `plugin` / `backend`), it is not a harness edit: nothing is written here. A harness
   repair is ONE batch carried by the round's ONE re-run; a defect still there after it is reported, not
   repaired again. Record it as a `harness-fix` loop-log entry whose finding names the file and says what
   changed and why in plain words — that line is shown to the customer at the end of the run — and name it
   the same way in the hand-off.
2. **Edit the harness file.**
3. **Run the wiring verifier this plugin ships, then re-verify the bench** — `verify_simulate.py` checks
   the contract you just touched (with the envelope flag); the after-edit re-verify re-runs
   `verify_config`, the smokes and the bridge over the edited harness. A check left `stale` holds the
   rung below `validated`:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
   ENVELOPE_KIND=$("$PLURAI_PY" -c "import yaml;a=yaml.safe_load(open('plurai_sim/plurai.config.yaml')).get('agent') or {};print((a.get('tool_content_envelope') or {}).get('kind') or '')" 2>/dev/null)
   "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_simulate.py" plurai_sim/simulate.py \
     --tool-content-envelope "${ENVELOPE_KIND:-json}" \
     && "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/bench_manifest.py" verify --mode reuse --after-edit
   ```

   Exit 0 or the edit is not a fix — a harness that does not verify breaks every scenario at
   once, which reads as an agent collapse.
4. **Recompute the fingerprint** — `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_fingerprint.py" --write --quiet`.
   This is the step that makes the change visible: it marks every saved transcript stale, and
   the eval lane's pin check starts refusing replays against them.
5. **Re-run and re-upload BEFORE the red-cell audit — mandatory, no exceptions.** The eval-only
   round is the only round that may skip a re-run, and it may only do so because the
   behaviour evidence is still true. Here it is not: auditing artifacts the old harness
   produced certifies verdicts on evidence the fix invalidated. (A full second critic follows
   the re-run only when the first reported `RUN INVALID`.) A fix made after the sanity pass and before any scored
   run (suite Step 2a) has its re-run in the scored qualification run that follows: nothing is discarded.
6. **The pin is rewritten at the REJOIN, not at the edit.** Re-pinning early re-opens sidecar
   writing against artifacts that have not been re-recorded yet — the exact state the
   fingerprint exists to keep visible.



---

## Which phases open, and why only four

Step 3 item 1 records `--status started` before the qualification run; so do `critic`, `edit` and
`final_scenarios`. The other seven declared phases — `corpus`, `world`, `scenarios`, `evals`,
`final_evals`, `render`, `report` — record `landed` only. That is deliberate, not an omission, and
the reason is what `phase resume` actually reads.

`resume` answers with `NEXT_PHASE[last_landed]` when nothing is open. Wherever the chain is
LINEAR, that already names the phase that died:

```
world landed, nothing else            -> resume_phase: scenarios — world landed
```

`scenarios` is exactly the phase the session was inside. A `started` record there would buy no
better resume point. Where it is NOT linear, it buys everything, because `edit` re-enters
`qualification` on the critic/edit round trip:

```
edit landed, nothing else             -> resume_phase: qualification — edit landed
edit landed + final_scenarios started -> resume_phase: final_scenarios — it started and never
                                                       landed; re-enter it
```

Without the open record the second reading is unreachable, and a session that died drawing the
final set buys a whole qualification round instead. So the four that open are exactly the ones
whose successor is ambiguous.

The one other `started` record is Step 0's job record (SKILL): a phase that starts a backend job
records its id the moment the start call returns, so the re-entered phase adopts that job
(`phase job`) instead of paying for it again. Coming last, it is also a resume point: a corpus that
dies mid-build resumes at `corpus`, not at `none`.

What a landed-only phase gives up is therefore the died-INSIDE-vs-never-STARTED distinction, not
the resume point. The first phase is the one case where even that reads honestly rather than
wrongly: a `corpus` that dies before its start call returns leaves `resume_phase: none — no phase has been
recorded for this workdir`, which is true; one that dies after it resumes at `corpus` and adopts its job.

## Why the solvability poll carries an `echo`

`bash …/poll_job.sh <id>; echo "solvability exit=$?"` — the shape Step 0 uses for `phase resume`
and `window claim`, and this is the site that needs it most.

One `poll_job.sh` invocation caps at 540 s of wall clock. The solvability check runs 10-20 minutes
at count=3. So **exit 14 — budget spent, job still RUNNING, backend healthy — is where every run
lands first**, usually twice, and it is the correct outcome, not a fault. Invoked bare as the last
command of its block, that healthy job renders in the tool result as a FAILURE, which is the same
condition `window claim` got its `echo` for (it exits 4 in precisely the state it exists to
report). A transport failure is exit 69: wait once (`wait.sh 45 --quiet`), re-invoke once, and a second 69 is the stop: run the unavailable stop (`stop_message.py unavailable --done`).

`|| true` is not the alternative: it collapses `0`, `13` and `14` into `$? = 0`, erasing the code
the paragraph above tells you to branch on. `; echo` carries both — the code is printed and the
block's own status is `0`. It is neither a pipe nor a capture, which is all `${CLAUDE_PLUGIN_ROOT}/docs/polling-cadence.md`
forbids: every line the script printed still lands in the tool output, with the code appended.
