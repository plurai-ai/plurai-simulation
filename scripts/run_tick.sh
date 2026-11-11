#!/usr/bin/env bash
# run_tick.sh — the overview page's live refresh while the runner plays a run.
#
#   bash "$_here/run_tick.sh" --loop $$ "$OUT" <scenarios> [trials] </dev/null >/dev/null 2>&1 &   # in run_stage.sh
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_tick.sh" "$ART/runs/<run-name>" <scenarios> [trials]   # retired: prints STOP
#
# THE MODEL'S TICK IS RETIRED. The page is published once, at the end of a /simulate run (publish_report.py, ONE
# PUBLISH PER RUN), so no tick hands the model a publish any more. Called without --loop (a skill text or a session
# from an older plugin), it prints the one "arm no more ticks" line at once and waits for nothing.
#
# --loop <pid>. The fill with no model in the chain, kept LIVE: `run_stage.sh` starts it beside the runner, so
# `flow_progress.json` and the local page (which polls its `report.data.js` every 2 s; a /simulate run's own live copy
# in the flow dir is mirrored by each fill) advance while the run plays. It wakes every RUN_FILL_SECONDS (default 3)
# and fills when a file under the run dir is newer than its last fill (a transcript, a tool-event file, a world
# landed), and at least every RUN_TICK_SECONDS (default 240) as a heartbeat; a wake with nothing new costs one `find`
# and no fill. It only FILLS (`fill --local`: the local page, no publish copy, no fill record). It stops itself,
# checked before every fill (so a fill is never cut off half-way): the run wrote `run_summary.json` or
# `.runner_exit`, process <pid> (`run_stage.sh` itself) is gone, RUN_TICK_MAX_SECONDS (default 43200) have passed, or
# the plan is no longer live. It prints nothing. With no live /simulate plan, where the page is a local file
# (Copilot and Codex: `run_stage.sh` refills it itself), or under a declared headless operator
# (`hooks/_run_driver.py mode`), it does nothing.
#
# The fill is best-effort: a failed fill never stops the loop.
#
# EXIT CODES
#   0   a tick ran (or there is nothing to tick); --loop: the loop ended
#  64   usage error

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USAGE="usage: run_tick.sh [--loop <pid>] <run-dir> <scenarios> [trials]"
LOOP=''
if [ "${1:-}" = "--loop" ]; then
  LOOP="${2:-}"
  case "$LOOP" in '' | *[!0-9]*) echo "$USAGE" >&2; exit 64 ;; esac
  shift 2
fi
OUT="${1:-}"
N="${2:-}"
K="${3:-1}"
case "$N" in '' | *[!0-9]*) echo "$USAGE" >&2; exit 64 ;; esac
case "$K" in '' | *[!0-9]*) echo "$USAGE" >&2; exit 64 ;; esac
[ -n "$OUT" ] || { echo "$USAGE" >&2; exit 64; }
WAIT="${RUN_TICK_SECONDS:-240}"
case "$WAIT" in '' | *[!0-9]*) WAIT=240 ;; esac

# shellcheck source=resolve-workdir.sh
. "$_here/resolve-workdir.sh" >/dev/null 2>&1
STOP='PLURAI_PROGRESS no tick is due: arm no more ticks. Say nothing about it.'
live() { [ -n "${FLOWDIR:-}" ] && python3 "$_here/publish_report.py" live --flowdir "$FLOWDIR" >/dev/null 2>&1; }
# Where the page is a local file `run_stage.sh` refills it from the call that plays the run, so neither a
# tick nor the loop has a second writer to add. A declared headless operator has nobody watching the page while
# the run plays, and a session that ends with its turn holds that turn open on the run's own poll, which a tick
# armed beside it would only block: there neither runs either.
headless() { case "$(python3 -I "$_here/../hooks/_run_driver.py" mode 2>/dev/null)" in "headless "*) return 0 ;; esac; return 1; }
if [ -z "$LOOP" ] || ! live || python3 "$_here/publish_report.py" local >/dev/null 2>&1 || headless; then
  [ -n "$LOOP" ] || echo "$STOP"
  exit 0
fi

# shellcheck source=resolve-python.sh
. "$_here/resolve-python.sh" >/dev/null 2>&1
trials=()
[ "$K" -gt 1 ] && trials=(--trials "$K")
# --local: it only refreshes the local page, never the publish copy (the Artifact tool refuses a file changed between
# its approval and its publish) nor the fill record.
fill() { "$PLURAI_PY" "$_here/publish_report.py" fill "$WORKDIR" --step progress --run-dir "$OUT" --run-total "$N" "${trials[@]}" --flowdir "$FLOWDIR" "$@" 2>/dev/null; }

MAX="${RUN_TICK_MAX_SECONDS:-43200}"
case "$MAX" in '' | *[!0-9]*) MAX=43200 ;; esac
EVERY="${RUN_FILL_SECONDS:-3}"
case "$EVERY" in '' | *[!0-9.]* | .) EVERY=3 ;; esac
# The last fill's moment, as a file: a run-dir file newer than it is new input. The fill never writes into the run
# dir, so its own outputs never wake it. Without a marker (`mktemp` failed) only the heartbeat fills.
mark="$(mktemp "${TMPDIR:-/tmp}/run_tick.XXXXXX" 2>/dev/null)" || mark=""
trap '[ -z "$mark" ] || rm -f "$mark"' EXIT
# The run stage call's own files (`.runner_*`, its output copy among them) say nothing new about the page.
changed() { [ -n "$mark" ] && [ -n "$(find "$OUT" -mindepth 1 -newer "$mark" ! -name '.runner_*' -print 2>/dev/null | head -n 1)" ]; }
last=$SECONDS
while sleep "$EVERY"; do
  [ -f "$OUT/run_summary.json" ] || [ -f "$OUT/.runner_exit" ] && break
  kill -0 "$LOOP" 2>/dev/null || break
  [ "$SECONDS" -ge "$MAX" ] && break
  [ $((SECONDS - last)) -ge "$WAIT" ] || changed || continue
  live || break
  [ -z "$mark" ] || touch "$mark"
  last=$SECONDS
  fill --local >/dev/null 2>&1
done
exit 0
