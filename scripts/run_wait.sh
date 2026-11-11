#!/usr/bin/env bash
# run_wait.sh — block until the run stage's call exits, in ONE foreground tool call.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/run_wait.sh" "$OUT" [max_seconds]
#
# WHAT IT WAITS ON. `run_stage.sh` writes its own pid to `<run-dir>/.runner_pid` as it starts and the exit code it
# returns to `<run-dir>/.runner_exit` on every exit path. This script only READS those files: it starts nothing
# and parents nothing, so a tool timeout or a reap that kills it leaves the run playing, and re-running it is always
# correct. The run is started by `run_start.py`, which returns at once; this is the session's bounded wait on it.
#
# A LIVE PID. The pid on disk is the run's only while it still runs `run_stage.sh`: a stale pid file's pid can name an
# unrelated process by now, which reads as no run (5). Where `ps` cannot print a command line, liveness alone answers.
#
# THE RUNNER'S OUTPUT. On a finished run it prints the path of `.<run-name>.runner_output` beside the run dir, where
# `run_start.py` sends the runner's output, and its last RUN_WAIT_TAIL_LINES (default 60) lines.
#
# A STALE EXIT. An exit code already on disk when this poll starts may be an earlier run's in a reused run dir, read
# in the moment before the new call clears it; one older than `.runner_pid` belongs to an earlier call outright. The
# first is trusted only if it is still there RUN_WAIT_STALE_SECONDS (default 2) later; the second is never trusted. A
# pid file already there as the poll starts gets the same second look before a dead pid counts as a stopped run.
#
# NEVER PIPE OR CAPTURE THIS SCRIPT. Its EXIT CODE is the result, and a pipeline reports the last command's.
#
# BUDGET. One invocation caps at `max_seconds` of wall clock (default 540, clamped to 1..540), under the harness's
# 600000 ms tool ceiling. It checks every RUN_WAIT_POLL_SECONDS (default 5). A run that outlives the budget is not
# an error: re-invoke to keep waiting.
#
# EXIT CODES
#   0  the runner exited 0; the run's files are under <run-dir>
#   1  the runner exited non-zero; the line printed names its code
#   2  budget spent, the run still playing: re-invoke to keep waiting
#   5  no run: <run-dir> has no pid and no exit code within RUN_WAIT_START_SECONDS (default 30): it never started
#   6  stopped from outside: the recorded code is 128+n (a signal: a `kill`, a closed shell, the system, the session
#      that started it ending), or the call that wrote the pid is gone with no code at all (SIGKILL runs no trap; its
#      pid may name another process by now). Not a failure of the run and no result: the user decides whether it runs
#      again, because a second run spends credits again
#   7  the backend refused the SDK's call (the runner exited 10, the SDK's UPGRADE_REQUIRED): the plugin's update text is
#      printed alone, never the runner's own output
#  64  usage error

set -u

OUT="${1:-}"
[ -n "$OUT" ] || { echo "usage: run_wait.sh <run-dir> [max_seconds]" >&2; exit 64; }
MAX="${2:-540}"
case "$MAX" in '' | *[!0-9]*) MAX=540 ;; esac
[ "$MAX" -ge 1 ] || MAX=1
[ "$MAX" -le 540 ] || MAX=540
EVERY="${RUN_WAIT_POLL_SECONDS:-5}"
case "$EVERY" in '' | *[!0-9]* | 0) EVERY=5 ;; esac
GRACE="${RUN_WAIT_START_SECONDS:-30}"
case "$GRACE" in '' | *[!0-9]*) GRACE=30 ;; esac
STALE="${RUN_WAIT_STALE_SECONDS:-2}"
case "$STALE" in '' | *[!0-9]*) STALE=2 ;; esac

# The first line of file $1, read with builtins only, whitespace removed.
_first_line() {
  local line=""
  { IFS= read -r line || [ -n "$line" ]; } < "$1" 2>/dev/null || return 0
  line="${line//[[:space:]]/}"
  printf '%s' "$line"
}

# Whether the exit code on disk is this run's (see A STALE EXIT).
_fresh_exit() {
  [ -f "$OUT/.runner_exit" ] || return 1
  [ -f "$OUT/.runner_pid" ] && [ "$OUT/.runner_pid" -nt "$OUT/.runner_exit" ] && return 1
  return 0
}

TAIL="${RUN_WAIT_TAIL_LINES:-60}"
case "$TAIL" in '' | *[!0-9]*) TAIL=60 ;; esac
# `.<run-name>.runner_output` beside the run dir, by parameter expansion (no `dirname`).
_run="${OUT%/}"
case "$_run" in */*) LOG="${_run%/*}/.${_run##*/}.runner_output" ;; *) LOG=".${_run}.runner_output" ;; esac
OUTPUT="The runner's printed output is the launch call's output."
_tail_output() {
  [ -f "$LOG" ] && tail -n "$TAIL" "$LOG" 2>/dev/null
  return 0
}

_stopped() {
  local where=""
  [ -f "$LOG" ] && where=" The runner's output is in $LOG; its last lines:"
  echo "run_wait: the run was STOPPED from outside before it finished: $1. It is not a failure of the run and it has no result. Do not start it again: tell the user it stopped and ask whether to run it again.$where"
  _tail_output
  exit 6
}

# This script's folder by builtins only: no PATH lookup decides what it reads.
_here="${BASH_SOURCE[0]}"
case "$_here" in */*) _here="${_here%/*}" ;; *) _here="." ;; esac
_here="$(cd "$_here" && pwd)"
# shellcheck source=sdk-exit-codes.sh
. "$_here/sdk-exit-codes.sh"

# The backend refused the SDK's call: the plugin's text from the runner's output, and exit 7. The runner's output stays on
# disk and is not printed, because it ends with the SDK's own `pip` line.
_refused() {
  # shellcheck source=resolve-python.sh
  . "$_here/resolve-python.sh" 2>/dev/null
  if [ -n "${PLURAI_PY:-}" ] && _tail_output | "$PLURAI_PY" -I "$_here/_client_handshake.py" sdk-refusal 2>/dev/null; then
    exit "$PLURAI_RC_VERSION_REFUSED"
  fi
  # `SDK_REFUSAL_FALLBACK` in `_client_handshake.py`, for a host with no python3 to ask: the sentence alone.
  printf '%s\n' "$PLURAI_SDK_REFUSAL_FALLBACK"
  exit "$PLURAI_RC_VERSION_REFUSED"
}

_finished() {
  local code
  code="$(_first_line "$OUT/.runner_exit")"
  case "$code" in '' | *[!0-9]*) code=1 ;; esac
  # 128+n is how a shell reports a process ended by signal n (n is at most 64); the runner's own codes are all below 128.
  # 129 (SIGHUP) is the run ending with the session that started it (`run_start.py` ties it to that session).
  if [ "$code" -gt 128 ] && [ "$code" -le 192 ]; then
    local how="signal $((code - 128))"
    [ "$code" -eq 129 ] && how="signal 1: the session that started it ended"
    _stopped "the runner exited $code ($how)"
  fi
  # The runner's upgrade-required exit (the SDK's, which the run step uses as well): the plugin's text, never the runner's own.
  [ "$code" -eq "$PLURAI_RC_UPGRADE_REQUIRED" ] && _refused
  local summary=""
  [ -f "$OUT/run_summary.json" ] && summary=" Summary: $OUT/run_summary.json."
  [ -f "$LOG" ] && OUTPUT="The runner's output is in $LOG; its last lines:"
  if [ "$code" -eq 0 ]; then
    echo "run_wait: the run finished: the runner exited 0.$summary $OUTPUT"
    _tail_output
    exit 0
  fi
  echo "run_wait: the run FAILED: the runner exited $code.$summary $OUTPUT"
  _tail_output
  exit 1
}

_alive() {
  local pid args
  pid="$(_first_line "$OUT/.runner_pid")"
  case "$pid" in '' | *[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  # See A LIVE PID.
  args="$(ps -ww -o args= -p "$pid" 2>/dev/null)" || return 0
  case "$args" in '' | *run_stage.sh*) return 0 ;; esac
  return 1
}

waited=0
# An exit code or pid already there as the poll starts: look again STALE seconds later before trusting it, so a call
# that is just starting in a reused run dir has replaced both before a dead earlier pid reads as a stopped run.
if { [ -f "$OUT/.runner_exit" ] || [ -f "$OUT/.runner_pid" ]; } && [ "$STALE" -gt 0 ]; then
  sleep "$STALE"
  waited=$STALE
fi
while :; do
  _fresh_exit && _finished
  # Right after the launch the call may not have written its pid yet: a missing pid file gets GRACE seconds.
  if ! _alive && { [ -f "$OUT/.runner_pid" ] || [ "$waited" -ge "$GRACE" ]; }; then
    # The exit code lands just before the call exits: look once more, so that moment is not read as a lost run.
    _fresh_exit && _finished
    [ -f "$OUT/.runner_pid" ] && _stopped "its run_stage.sh call is gone and left no exit code (it was killed)"
    echo "run_wait: no run in $OUT: it never started (no pid and no exit code). Launch the run first."
    exit 5
  fi
  if [ "$waited" -ge "$MAX" ]; then
    echo "run_wait: the run is still playing after ${waited}s. Re-invoke: bash \"$0\" \"$OUT\""
    exit 2
  fi
  step=$EVERY
  [ $((waited + step)) -le "$MAX" ] || step=$((MAX - waited))
  sleep "$step"
  waited=$((waited + step))
done
