#!/usr/bin/env bash
# assert-run-report.sh — a run must not publish a verdict with the customer's analysis nowhere.
#
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-run-report.sh" "$OUT" "$WORKDIR" || exit 1
#
# WHY. Everything downstream of a run reads the RUNNER's files — `run_summary.json` and the
# per-scenario transcripts — so a Step 3/4b render that was skipped, died on a box with no
# python3, or threw mid-way is invisible to every consumer: the gates still read their inputs,
# the release gate still publishes `block`/`pass`, and the one artifact the CUSTOMER reads is the
# only one whose absence nothing asserts. Runs have ended that way and published a verdict with
# no report and no error to act on.
#
# WHAT IT IS NOT. It is NOT "run.json must exist". Step 4b is conditional BY DESIGN — on a run
# recorded to a platform experiment the platform UI *is* the analysis, and a local report is not
# owed. So this asks the generic question instead: is the customer's analysis SOMEWHERE? The
# predicate is the marker the same skill already declares, `$WORKDIR/.observability`:
#
#   isolated  + no run.json + no run_stale.json  ->  REFUSE (exit 1): nobody has an analysis
#   isolated  + no run.json + run_stale.json     ->  pass, echoing its reason: already explained
#   recording                                    ->  no-op: the platform holds it
#   run.json present                             ->  no-op, silent
#
# An ABSENT or unreadable marker reads as `isolated`, which is the runner's own documented
# precedence (`--observability` > $PLURAI_OBSERVABILITY > this sidecar > default `isolated`) —
# no platform, so the local report is the only analysis there could be.
#
# The refusal path REUSES `run_stale.json` rather than inventing a second vocabulary. It cannot
# WRITE one: that sidecar is written by `build_run_json.py`, the very program whose absence this
# is about, so the typed finding for "it never ran" has to be a refusal here and not a file.
#
# KNOWN UNDER-COVERAGE, stated not fixed: a `recording` run on Langfuse, which Step 4b also says
# needs the local report. The marker carries only `isolated|recording`, and widening its token
# would cross into the SDK, which reads it. That arm still leaves the customer platform traces.
#
# SHELL, not python, on purpose: "no python3 on the box" is one of the ways the render goes
# missing, and a guard written in python could not run there either.
#
# EXIT / RETURN
#   0  the analysis exists somewhere, or its absence is already explained, or none is owed
#   1  a run is about to publish a verdict and NOBODY has the customer's analysis

_arr_out="${1:-}"
_arr_workdir="${2:-}"
# The commands printed below name this file's own plugin root: the Bash tool does not export
# $CLAUDE_PLUGIN_ROOT, so a remedy spelled with the variable would run `/skills/...`.
_arr_self=""
if [ -n "${BASH_VERSION:-}" ]; then _arr_self="${BASH_SOURCE[0]}"; elif [ -n "${ZSH_VERSION:-}" ]; then eval '_arr_self=${(%):-%x}'; fi
_arr_root="$( [ -n "$_arr_self" ] && CDPATH= cd -- "$(dirname -- "$_arr_self")/.." 2>/dev/null && pwd)"
[ -n "$_arr_root" ] || _arr_root='${CLAUDE_PLUGIN_ROOT}'

if [ -z "$_arr_out" ] || [ -z "$_arr_workdir" ]; then
  # A preflight that hard-fails on its own missing input just gets deleted from the block.
  echo "NOTE(run-report): no run dir / workdir passed — cannot check whether this run has a customer report. Call as: source \"$_arr_root/scripts/assert-run-report.sh\" \"\$OUT\" \"\$WORKDIR\" || exit 1" >&2

elif [ -f "$_arr_out/run.json" ]; then
  :   # the report was built; nothing to say

else
  _arr_obs=""
  [ -r "$_arr_workdir/.observability" ] && _arr_obs="$(cat "$_arr_workdir/.observability" 2>/dev/null)"
  if [ "$_arr_obs" = "recording" ]; then
    :   # recorded to a platform — the experiment IS the customer's analysis, and Step 4b skips
  elif [ -f "$_arr_out/run_stale.json" ]; then
    echo "NOTE(run-report): no run.json — the aggregator REFUSED this run and said why. Its typed finding is at $_arr_out/run_stale.json:" >&2
    cat "$_arr_out/run_stale.json" >&2
    echo "  Not a missing report: the absence is explained and the remedy is in that file. Report it as the finding." >&2
  else
    echo "STOP(run-report): this run is about to publish a verdict and the CUSTOMER'S ANALYSIS IS NOWHERE." >&2
    echo "  missing: $_arr_out/run.json$([ -f "$_arr_out/analytics.md" ] || printf ' + analytics.md')$([ -d "$_arr_out/charts" ] || printf ' + charts/')" >&2
    echo "  observability: ${_arr_obs:-isolated (no \$WORKDIR/.observability)} — nothing was recorded to a platform, so the local report is the only analysis there is." >&2
    echo "  No run_stale.json either, so this is not the aggregator refusing the run: Step 3/4b never produced the report." >&2
    echo "  Fix — run Step 3's two commands, then retry this step:" >&2
    echo "    python3 \"$_arr_root/skills/simulation-run/scripts/build_run_json.py\" \"$_arr_out\" --workdir \"$_arr_workdir\"" >&2
    echo "    python3 \"$_arr_root/skills/simulation-run/scripts/render_run_report.py\" \"$_arr_out/run.json\" --out-dir \"$_arr_out\"" >&2
    unset _arr_out _arr_workdir _arr_obs _arr_self _arr_root
    return 1 2>/dev/null || exit 1
  fi
fi

unset _arr_out _arr_workdir _arr_obs _arr_self _arr_root
