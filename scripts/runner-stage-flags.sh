# runner-stage-flags.sh — set $RUN_FLAGS, the stage-derived flags of one plurai-run-simulation launch.
#
# Source it (never execute) in Step 3's launch block, after $RUNNER, $OUT, $WORKDIR, $ART and $FLOWDIR are set:
#
#     source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-stage-flags.sh"
#     ... "$RUNNER" ... "${RUN_FLAGS[@]}"
#
# The CLI substitutes the root in that `source` line but exports nothing, so inside this file $CLAUDE_PLUGIN_ROOT is
# usually empty: the plugin's other scripts are found from this file's own path (an exported root is the fallback).
#
# The flags come from typed state only, so a relaunch re-sources this file and never re-types them:
#   $FINAL_RUN (the declaration; unset = the flow marker decides, as Step 2b's), $RUN_PHASE (a driver's
#   `RUN_PHASE=qualification`), $PLURAI_REPLAY, $PLURAI_QUAL_STABILITY_RETRY, $OUT/run_summary.json (a merge
#   re-run), $WORKDIR/regressions, the scan markers, the ledger, and what `"$RUNNER" --help` advertises
#   (argparse is strict: an unknown flag exits 64 before a single scenario runs).
# The phase is NEVER derived from the stage: a `fixes` re-run is a qualification STAGE (Step 2b) that still
# runs at the full budget with its stability retry.
#
# An array, one argv item per word in bash 3.2 and zsh alike; every append is guarded, and nothing here
# calls `exit` or `return` (either would end or confuse the caller's shell). It warns; it never stops a run.

_rsf_help="$("$RUNNER" --help 2>/dev/null || true)"

# The final check, as the stage's: a declared FINAL_RUN outranks the marker; undeclared, a `run` marker or none is final.
if [ -z "${FINAL_RUN:-}" ]; then
  case "$(cat "$FLOWDIR/.plurai_flow_active" 2>/dev/null || true)" in run|"") FINAL_RUN=1 ;; esac
fi
if [ "${FINAL_RUN:-0}" = 1 ]; then RUN_PHASE=full; fi

PHASE_FLAG=""
case "$_rsf_help" in
  *--run-phase*) PHASE_FLAG="--run-phase=${RUN_PHASE:-full}" ;;
  *) if [ "${RUN_PHASE:-full}" = "qualification" ]; then
       echo "[run-phase] this runner predates --run-phase — the qualification pass will use the FULL per-turn budget"
     fi ;;
esac

# A --scenario-id re-run into an $OUT that already holds a run merges into it, and the runner refuses any N but
# that run's own: its run_summary.json `trials` block's `n` when the block is trials-v1, else 1 (the SDK's
# `_trials_out_refusal`). So a merge keeps the run's own N, whatever the stage.
_rsf_prior_n=""
if [ -s "$OUT/run_summary.json" ]; then
  _rsf_prior_n="$(python3 -c '
import json, sys
try:
    doc = json.loads(open(sys.argv[1], encoding="utf-8").read())
except (OSError, ValueError):
    raise SystemExit(0)
if isinstance(doc, dict):
    block = doc.get("trials")
    n = block.get("n") if isinstance(block, dict) and block.get("schema") == "trials-v1" else 1
    if isinstance(n, int) and not isinstance(n, bool):
        print(n)
' "$OUT/run_summary.json" 2>/dev/null || true)"
fi
TRIALS_FLAG=""
case "$_rsf_help" in
  *--trials*)
    if [ -n "$_rsf_prior_n" ]; then
      if [ "$_rsf_prior_n" != 1 ]; then TRIALS_FLAG="--trials=$_rsf_prior_n"; fi
    elif [ "${FINAL_RUN:-0}" = 1 ]; then
      case "${PLURAI_REPLAY:-}" in
        replay|record) echo "[trials] PLURAI_REPLAY=$PLURAI_REPLAY serves one recorded sample — the final run is one sample per scenario" ;;
        *) TRIALS_FLAG="--trials=3" ;;
      esac
    fi ;;
  *) if [ "${FINAL_RUN:-0}" = 1 ]; then echo "[trials] this runner predates --trials — the final run is one sample per scenario"; fi ;;
esac

STABILITY_FLAG=""
case "$_rsf_help" in
  *--stability-retry*)
    # Trials replace the retry on the final run; the final run's trials measure stability, so qualification skips
    # its re-run unless PLURAI_QUAL_STABILITY_RETRY=1 brings it back.
    if [ "${RUN_PHASE:-full}" = "full" ] && [ -z "$TRIALS_FLAG" ]; then STABILITY_FLAG="--stability-retry"; fi
    if [ "${RUN_PHASE:-full}" = "qualification" ] && [ "${PLURAI_QUAL_STABILITY_RETRY:-}" != 1 ]; then STABILITY_FLAG="--no-stability-retry"; fi ;;
  *) echo "[stability] this runner predates --stability-retry — every failure in this run lands 'stability: unmeasured'" ;;
esac

DRIFT_FLAGS=()
case "$_rsf_help" in
  *--scan-contract*)
    if [ -s "$ART/scan_contract.json" ] && [ -s "$WORKDIR/.scan_corpus_roots" ]; then
      DRIFT_FLAGS=(--scan-contract="$ART/scan_contract.json")
      while IFS= read -r _rsf_root; do
        if [ -n "$_rsf_root" ]; then DRIFT_FLAGS+=(--scan-corpus="$_rsf_root"); fi
      done < "$WORKDIR/.scan_corpus_roots"
      # Re-hash with the suffixes the baseline was walked with: `scan_contract.sh` records them beside the roots
      # whenever the scan widened past the checker's default set, and a baseline recorded WIDE and re-hashed NARROW
      # reports every widened file as `removed` — a drift verdict the sources never earned.
      if [ -s "$WORKDIR/.scan_corpus_suffixes" ]; then
        while IFS= read -r _rsf_sfx; do
          if [ -n "$_rsf_sfx" ]; then DRIFT_FLAGS+=(--scan-suffixes="$_rsf_sfx"); fi
        done < "$WORKDIR/.scan_corpus_suffixes"
      fi
    fi ;;
esac

# The plugin root, from this file's own path (bash and zsh spell it differently), by parameter expansion only: no `cd`,
# whose hooks can print into a captured value. Native Windows sources it as `C:\...\scripts\...`: forward slashes there.
_rsf_self=""
if [ -n "${BASH_VERSION:-}" ]; then _rsf_self="${BASH_SOURCE[0]}"; elif [ -n "${ZSH_VERSION:-}" ]; then eval '_rsf_self=${(%):-%x}'; fi
_rsf_self_on_windows() {
  [ -x /usr/bin/cygpath ]
}
case "$_rsf_self" in
  *\\*) if _rsf_self_on_windows; then _rsf_self="${_rsf_self//\\//}"; fi ;;
esac
unset -f _rsf_self_on_windows
case "$_rsf_self" in "") _rsf_plugin="" ;; */*) _rsf_plugin="${_rsf_self%/*}/.." ;; *) _rsf_plugin=".." ;; esac
if [ -z "$_rsf_plugin" ] || [ ! -r "$_rsf_plugin/scripts/runner-gate-flags.sh" ]; then
  _rsf_plugin=""
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -r "${CLAUDE_PLUGIN_ROOT}/scripts/runner-gate-flags.sh" ]; then _rsf_plugin="$CLAUDE_PLUGIN_ROOT"; fi
fi
if [ -z "$_rsf_plugin" ]; then
  echo "[run-flags] the plugin's scripts were not found — this launch carries no gate overrules, no knowledge-copy check and no replay plan"
fi

OVERRULE_FLAGS=()
if [ -n "$_rsf_plugin" ]; then
  source "$_rsf_plugin/scripts/runner-gate-flags.sh"   # $OVERRULE_FLAGS — the plugin's ledger, when it exists
fi

# Knowledge copied from the agent's code (`inline_sha256:` stamps in .corpus_scope): has the code changed since the
# suite's copy? Prints its `warning:` line, writes nothing, never stops the run.
if [ -n "$_rsf_plugin" ] && grep -q '^inline_sha256:' "$WORKDIR/.corpus_scope" 2>/dev/null; then
  source "$_rsf_plugin/scripts/resolve-python.sh"   # $PLURAI_PY
  "$PLURAI_PY" "$_rsf_plugin/scripts/extract_inline_knowledge.py" --workdir "$WORKDIR" --when run | grep '^warning:' || true
fi

# The recorded regressions' replay plan: each one single-hop at its cut, or whole. The plan, not the session, decides.
REPLAY_PLAN_FLAGS=()
if [ -n "$_rsf_plugin" ] && [ -d "$WORKDIR/regressions" ]; then
  case "$_rsf_help" in
    *--replay-plan*)
      source "$_rsf_plugin/scripts/resolve-python.sh"   # $PLURAI_PY — replay-plan reads policies_config.yaml through PyYAML
      if "$PLURAI_PY" "$_rsf_plugin/scripts/regressions.py" replay-plan "$WORKDIR/regressions" \
        --owners "$ART/regression_owners.json" --policies "$WORKDIR/policies_config.yaml" --before "$ART/regression_before.json" \
        --seed-fidelity "$ART/seed_resume_fidelity.json" --out "$ART/replay_plan.json" >/dev/null; then
        REPLAY_PLAN_FLAGS=(--replay-plan="$ART/replay_plan.json")
      else
        echo "[replay-plan] the replay plan could not be built — the recorded regressions replay whole"
      fi ;;
    *) echo "[replay-plan] this runner predates --replay-plan — the recorded regressions replay whole" ;;
  esac
fi

RUN_FLAGS=()
if [ -n "$PHASE_FLAG" ]; then RUN_FLAGS+=("$PHASE_FLAG"); fi
if [ -n "$TRIALS_FLAG" ]; then RUN_FLAGS+=("$TRIALS_FLAG"); fi
if [ -n "$STABILITY_FLAG" ]; then RUN_FLAGS+=("$STABILITY_FLAG"); fi
if [ "${#REPLAY_PLAN_FLAGS[@]}" -gt 0 ]; then RUN_FLAGS+=("${REPLAY_PLAN_FLAGS[@]}"); fi
if [ "${#DRIFT_FLAGS[@]}" -gt 0 ]; then RUN_FLAGS+=("${DRIFT_FLAGS[@]}"); fi
if [ "${#OVERRULE_FLAGS[@]}" -gt 0 ]; then RUN_FLAGS+=("${OVERRULE_FLAGS[@]}"); fi

# One line, every launch: the stage and the flags it carries (a path is named by its flag only).
_rsf_said=""
for _rsf_flag in "${RUN_FLAGS[@]}"; do
  case "$_rsf_flag" in
    --*/*) _rsf_said="$_rsf_said ${_rsf_flag%%=*}" ;;
    --*) _rsf_said="$_rsf_said $_rsf_flag" ;;
  esac
done
if [ "${FINAL_RUN:-0}" = 1 ]; then _rsf_stage=final; else _rsf_stage=qualification; fi
echo "[run-flags] stage=$_rsf_stage:${_rsf_said:- none}"
