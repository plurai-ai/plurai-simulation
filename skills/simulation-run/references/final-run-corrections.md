# Final-run corrections — audit, labels, corrected verdicts, re-render

The one procedure that turns a FINAL scored run's red-cell audit into the verdicts the report shows. Two places run it,
each on its own run and never both on one: `/simulation-generate-suite`'s deliverable step 4 (the suite's own final run,
after that step's repairs) and `/simulation-run` Step 4d (an official final run). It needs the run dir (`$OUT`), that
run's `run_upload_id` and the current `.eval_set_id`. These steps are pre-authorized by the invocation — never ask
consent for them.

**One run, one audit.** The audit rows, the labels and the corrections belong to the run whose replies they quote. Never copy
them to another run, and never pass one run's audit rows to another run's `labels` or `build`: a ruling on one reply
says nothing about another run's reply, even when the scenario and the words match. Each run is audited on its own.
The one thing that crosses is the suite's `$ART/audit_patterns.json`: a hint to the official run's auditor, never a row.

1. **Audit.** Run the red-cell audit (`/analyze-simulation` step 6, its per-item tool-answer question included) ONCE
   over this run's red cells and sampled greens — every trial's, as `red_cells.py list --run-dir "$OUT" --greens 24
   --workdir "$WORKDIR"` enumerates them, its rows saved to the run's audit with `--stage final` and
   uploaded (below). The green sample is at most 24 pass units (about 5 more minutes), half of them passes the
   judge itself was unsure of (`sampled: doubt`), the rest one per scenario × eval whose other trial is red and two per
   high-stakes eval, judge or code check (one of each eval with no red read first). A `sampled` unit is a pass:
   rule it `CONFIRMED PASS`, `FALSE PASS` (the reply breaks the rule as written: quote it and name the
   `defect_surface` that let it pass) or `UNDECIDABLE`. Trials whose replies make the same claim get the same ruling,
   or the row names what differs — across runs too: `save` exits 5 on a row an earlier run's audit ruled the other way
   on the identical cell, until it is ruled the same or its `differs` says why not. A `FALSE RED` row sets `corrected` only to overrule its cause's
   default (`scenario` / `harness` / `env` / `scan_fact`: nothing was measured → `not_applicable`; any other → `pass`). A pipeline item
   (no turns; its whole output is one reply) is quoted at `quote_turn: 0`, verbatim from the `pipeline_outputs` the
   list shows — never abbreviated. A group carrying `known_patterns` (an official run's list) was already ruled at this
   eval revision by the suite's final audit: when this reply shows the same behaviour, rule it the same way at once,
   still quoting this reply; reason in full where no pattern fits. No `/edit-evals` and no rescore follow from this
   audit on an official run, nor on the suite's own final run: fixing an eval is left to the eval optimizer and the next
   build (under `PLURAI_POSTFINAL_EVAL_ROUND=1` the suite runs one bounded eval round between steps 2 and 3, its
   deliverable step 4).
   **Upload it — before any verdict lands.** The backend reads the audit off the run upload; until the receipt of the
   file as it stands is recorded, the run's verdict renders `provisional` and the run stage has not landed. After every
   save (a delta audit too), run `/analyze-simulation` step 6's upload block (`red_cell_audit.py upload-args` →
   `upload_red_cell_audit` → `red_cell_audit.py uploaded`) with `--run-dir "$OUT"` and this run's `run_upload_id`.
   Exit 4 from `uploaded` means the audit grew since the upload: run the block again. A refused upload is recorded
   (`append_loop_log.py --kind note --lane eval --finding "red-cell audit not uploaded: <why>"`) and the verdict stays provisional.
2. **Labels — BEFORE any correction.** The `prompt` `FALSE RED` and `FALSE PASS` rows upload as that eval's labels:
   `/analyze-simulation` step 6's `red_cells.py labels` + `upload_eval_labels` block with `--stage final`, this run's
   `run_upload_id` and this run's audit rows. `labels` labels only a unit still as recorded, and a corrected unit is not. A
   `scenario`, `harness`, `env` or `scan_fact` row is not a judge error: it stays in the audit and the report, never a label.
3. **Correct.** Every `FALSE RED` and `FALSE PASS` row is CORRECTED in place, never re-judged: `verdict_corrections.py
   build` sends each item's corrections to the SDK's verdicts writer, which records them (quote and reason included) in
   that conversation's verdicts; exit 5 names an item the writer refused — run `build` again. Run it LAST, after any re-run of this run: it corrects only a unit still red (a pass,
   for a `FALSE PASS`), with its quote verified in that reply (for a `FALSE RED`, or in the tool answer its judge cited), that no row ruled the other way, and prints each row it
   skipped and why. A `FALSE RED` whose quote no reply, file or tool answer holds (the user's own message, a dataset row of
   the world) is corrected on its cell when the quote is in that conversation's records and in none of the agent's replies. A ruling also corrects each UNRULED sibling trial whose reply holds its quote; each `conflicts`
   line is one claim ruled two ways: re-read that pair once, fix the wrong row, and run `build` again. A goal owner's `TRUE RED` on a goal left undecided because the agent never
   took a step it requires is written as a failed goal (`required_steps_skipped`). `--report`
   also lands every overrule that envelope APPLIED on a set-aside verdict; `check` exits 3 naming any that did
   not land, and 4 naming each audited correction the scores still count — never post the scores past either. A quote
   from a file the agent wrote is anchored by `quote_file` (its path), not `quote_turn`: fix the row, `build` again.

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   OUT="$(cat "$WORKDIR/.last_run")"   # the final run
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verdict_corrections.py" build --run-dir "$OUT" \
       --report "<this run's saved suite_diagnostics envelope>" && \
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verdict_corrections.py" check --run-dir "$OUT" --report "<the same envelope>"
   ```

3a. **Attribute — before the re-render.** Attribute this run's violations — each `TRUE RED` and `FALSE PASS` shows ONE finding's defect, a new one, or none, and a model reader decides which, never the check it failed:
   `source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"; READS="$ART/finding_reads/$(basename "$OUT")"; mkdir -p "$READS"; "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py" to-read --workdir "$WORKDIR" --run "$(basename "$OUT")" --render "$READS" > "$READS/brief.json"`
   (`$PLURAI_PY`: it reads `policies_config.yaml` and each scenario's premise through PyYAML). The brief holds every
   finding's section, every violation with its quote and the audit's reason, the conversations each confirmed finding is
   read in (`items`), every scenario's premise, the reader's `question` and the `answers` shape. ONE fresh report-only
   reader answers it over the renders (never the raw transcripts) — one pass, never re-read; split by conversation into
   more readers only when the brief does not fit one — and writes `$READS/answers.json`. Then, whoever drives the run,
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py" attribute --workdir "$WORKDIR" --run "$(basename "$OUT")" --answers "$READS/answers.json" --found-during run`
   (a new defect's stage is the run's own, read from `runs/index.json`);
   it writes `finding_attribution.json` beside the audit and files each new defect's row and section;
   `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py" record --workdir "$WORKDIR" --run "$(basename "$OUT")"`
   then stamps every finding. A `WARNING` line or a non-zero exit of any of the three is a loop-log note, never a block:
   a violation with no edge shows as "not yet traced", never dropped. Run each as written, never piped into `tail`/`head`;
   if you trim output, `set -o pipefail` first and read the last line.
   `record` also stores, in each replayed regression folder's `replays.json`, the check its own replays were linked on: every
   later run reads that folder on that check. Step 4's rebuild then reads every ruling once; a `run.json` built before
   `attribute` is named by `deliverables.py check` (`WARN … run.json`), and rebuilding it (Step 4b) clears it.

3b. **Tie-break — only when the audit and the reader disagree.** `"$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/scored_findings.py" contested --workdir "$WORKDIR" --run "$(basename "$OUT")" > "$READS/contested.json"`
   lists the units the audit upheld (`TRUE RED` / `FALSE PASS`) that the reader linked `none`, at most 8, BLIND: the reply
   and the two reasons as unlabelled arguments, never which ruling said what. With none listed, skip to step 4. Else ONE
   fresh report-only auditor answers its `question` once — one pass, never re-read — with one audit row per unit carrying
   `"tie_break": true`; it may rule that the rule does not apply here (`FALSE RED`, `corrected: not_applicable`). Save the
   rows with step 1's save (`--stage final`, the delta-audit path) and its upload block, then step 2's labels, step 3's
   `build`, and `record` again. A tie-break `FALSE RED` is corrected (the check then passes); a tie-break `TRUE RED`
   overrules the reader's `none` (the red shows as not yet traced). A skipped tie-break is a loop-log note
   (`append_loop_log.py --kind note --lane eval --finding "tie-break skipped: <why>"`), never a block.

4. **Re-render.** First re-derive the run's discounts from THIS audit: `red_cells.py attribution --report <this run's
   saved suite_diagnostics envelope> --findings "$ART/agent_findings.md" --run-dir "$OUT"`. Then call
   `suite_diagnostics_start` again with the same arguments (plus its `overrules` when its `new` is non-zero) — ALWAYS: the
   report read before the upload is `provisional` and its discounts undecided — and that job is the one the banner block
   saves. A check is "not counted" only on that report's `release_gate.suite_defects` `discounted` rows. Then rebuild `run.json` and the report with `/simulation-run` Step 4b's `build_run_json.py` and
   `render_run_report.py` blocks, unchanged (each trial dir too), then Step 4c's `analytics.md` banner block (the
   banner's counts leave out the reds a correction cleared), and, when the run's report was already attached, re-run
   Step 4c's attach block (`ingest-run-report` → `attach_run_report`, which replaces the old attachment; pre-authorized by the invocation).
   `release_verdict.py stale --out "$OUT"` exits 13 while the verdict read another audit than the backend holds: call
   `release_verdict_recompute` with each `stale_reports` id the upload receipt names, poll it, and render from its envelope.
5. **Overwrite the recorded scores** on the connected observability platform:

   ```bash
   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
   OUT="$(cat "$WORKDIR/.last_run")"
   PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require correct-scores)" \
     && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" "$PLURAI_BIN" correct-scores --run-dir "$OUT"
   ```

   Exit 1 from the resolver (the SDK lacks the verb) is `append_loop_log.py --kind note --lane eval --finding "scores not corrected: the SDK predates correct-scores"`; exit 7 is a version refusal (its output is the update text: say it as the error posture says, and stop); any other non-zero exit or a `failed` line is one line in the hand-off, never a failure.
6. **Record.** Each `target_clause` row, and each other non-`prompt` `FALSE RED` or `FALSE PASS` that `build` corrected, is recorded for the next build (`append_loop_log.py --kind red-cell-audit --finding "next build: <cause> — <the row>"`). A row still standing after that is a named suite defect, never a block: record it (`append_loop_log.py --kind red-cell-audit --finding "suite defect: <eval|backend|scenario> — <the row>"`) and name it in the hand-off beside the verdict.
