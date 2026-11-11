#!/usr/bin/env bash
# run_stage.sh — play the run stage's runner and keep the overview page's card moving.
#
#   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_start.py" -- "$RUNNER" <runner flags>   # starts this, detached
#
# WHY IT WRAPS THE RUNNER. A page refresh that waits for the model to arm a second task does not happen: the
# model launches the runner and goes on. Here the refresh is a child of the very process that plays the run, so it
# starts with the run and ends with it, and nothing asks the model for it.
#
# WHAT IT DOES. Runs the runner command after `--` in the background of this script, stdin and output untouched,
# and exits with the runner's own exit code. Where the overview page is a local file (Copilot CLI and Codex CLI
# have no Artifact tool: `publish_report.py local`) and a live /simulate plan has a stage running, a sidecar
# refills the page with `publish_report.py fill --step progress --run-dir`, counting finished conversations: within
# RUN_FILL_SECONDS (default 3) of a new file under the run dir, and at least every RUN_STAGE_SECONDS (default 90)
# as a heartbeat. The page open from the file polls its `report.data.js` every 2 s, so it stays live with no reload;
# a wake with nothing new costs one `find` and no fill. The run directory, the scenario count and the trial count are read off the
# runner's own flags (`--out`, each `--scenario-id`, `--trials`), so the call carries no second copy of them.
#
# Before the runner starts it records the operator's identity markers in `<run-dir>/.operator_identity.json`
# (`operator_identity.py capture`), the run-start half of the `operator_identity_exposed` check, and the runner
# command itself in `.<run-name>.runner_command` beside the run directory.
#
# WHERE THE MODEL PUBLISHES THE PAGE (Claude Code) the sidecar does not start: this script starts
# `run_tick.sh --loop $$` beside the runner for the refresh (it decides where it applies and stops with this
# script); the page is published at the hand-off alone (once per /simulate run), never mid-run.
#
# THE RUN'S EXIT, ON DISK. With a run directory, the call clears `<run-dir>/.runner_exit` and writes its own pid to
# `<run-dir>/.runner_pid` as it starts, and writes the runner's exit code to `<run-dir>/.runner_exit` the moment the
# runner exits, atomically (128+n when this call is signalled first; a signal after that leaves the runner's code).
# `run_wait.sh` polls those files, so the session waits on the run from bounded calls of its own.
#
# THE OWNER. With RUN_OWNER_PID (`run_start.py` sets it to the harness process that launched the run), a watcher
# checks every 2 s that the process lives and, once it is gone, sends this script HUP: the runner is stopped and
# 129 recorded, so a run never outlives its session. On Windows the check is `run_start.py --alive` under
# RUN_OWNER_PYTHON, because Git Bash's `kill -0` does not see a native pid. A TERM (`kill "$(cat .runner_pid)"`)
# is still the deliberate stop.
#
# THE SIDECAR NEVER OUTLIVES THE CALL. It stops when the runner exits, when `<run-dir>/run_summary.json` appears,
# when this script is signalled (the runner is stopped too) and when this script is gone (a SIGKILL runs no
# trap). Its output goes nowhere, so the call prints what the runner prints. It is best-effort: a failed fill is
# ignored, never stops the runner and never changes the exit code.
#
# EXIT CODES
#   the runner's own (128+n when this script is signalled; 129 when its owner is gone)
#   64   usage error (no runner command after `--`)

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "${1:-}" = "--" ] && shift
[ "$#" -gt 0 ] || { echo "usage: run_stage.sh -- <runner command>" >&2; exit 64; }

EVERY="${RUN_STAGE_SECONDS:-90}"
case "$EVERY" in '' | *[!0-9]* | 0) EVERY=90 ;; esac
FILL="${RUN_FILL_SECONDS:-3}"
case "$FILL" in '' | *[!0-9]* | 0) FILL=3 ;; esac

OUT=""
N=0
K=1
prev=""
for arg in "$@"; do
  case "$arg" in
    --out=*) OUT="${arg#--out=}" ;;
    --scenario-id | --scenario-id=*) N=$((N + 1)) ;;
    --trials=*) K="${arg#--trials=}" ;;
  esac
  case "$prev" in
    --out) OUT="$arg" ;;
    --trials) K="$arg" ;;
  esac
  prev="$arg"
done
case "$K" in '' | *[!0-9]*) K=1 ;; esac

# The refill loop. It runs in its own process group so the last resort below can take its python child with it.
_sidecar() {
  local parent="$1" waited py
  stopping=""
  trap 'stopping=1' TERM
  # shellcheck source=resolve-workdir.sh
  . "$_here/resolve-workdir.sh" >/dev/null 2>&1
  [ -n "${FLOWDIR:-}" ] || return 0
  # Only a run under `runs/` is a run the page counts.
  case "$OUT" in "${ART:-/nonexistent}"/runs/* | runs/*) ;; *) return 0 ;; esac
  python3 "$_here/publish_report.py" live --flowdir "$FLOWDIR" >/dev/null 2>&1 || return 0
  python3 "$_here/publish_report.py" local >/dev/null 2>&1 || return 0
  # shellcheck source=resolve-python.sh
  . "$_here/resolve-python.sh" >/dev/null 2>&1
  py="$PLURAI_PY"
  local args=(--step progress --run-dir "$OUT" --flowdir "$FLOWDIR")
  [ "$N" -gt 0 ] && args+=(--run-total "$N")
  [ "$K" -gt 1 ] && args+=(--trials "$K")
  while [ -z "$stopping" ]; do
    waited=0
    while [ "$waited" -lt "$EVERY" ] && [ -z "$stopping" ]; do
      sleep 1
      waited=$((waited + 1))
      kill -0 "$parent" 2>/dev/null || return 0
      # Newer than the marker made at start, so a summary left by an earlier run in this directory does not
      # end the refills. Without a marker (`mktemp` failed) any summary does.
      [ "$OUT/run_summary.json" -nt "$started" ] && return 0
      # New input since the last fill: a file under the run dir newer than `$filled` (the fill never writes there).
      # This call's own files (`.runner_*`) and the run dir's own mtime are not new input.
      [ $((waited % FILL)) -eq 0 ] && [ -n "$filled" ] && [ -n "$(find "$OUT" -mindepth 1 -newer "$filled" ! -name '.runner_*' -print 2>/dev/null | head -n 1)" ] && break
    done
    [ -z "$stopping" ] || return 0
    [ -z "$filled" ] || touch "$filled"
    "$py" "$_here/publish_report.py" fill "$WORKDIR" "${args[@]}" >/dev/null 2>&1 || true
  done
}

sidecar=""
runner=""
started=""
filled=""
_stop_sidecar() {
  [ -n "$sidecar" ] || return 0
  # TERM lets the fill in flight finish: the sidecar acts on it between two commands.
  kill -TERM "$sidecar" 2>/dev/null
  local i=0
  while kill -0 "$sidecar" 2>/dev/null && [ "$i" -lt 40 ]; do
    sleep 0.5
    i=$((i + 1))
  done
  kill -KILL -- "-$sidecar" 2>/dev/null
  wait "$sidecar" 2>/dev/null
  sidecar=""
  [ -z "$started" ] || rm -f "$started"
  [ -z "$filled" ] || rm -f "$filled"
  started=""
  filled=""
}
# The exit code, where `run_wait.sh` reads it: written to a temp name and renamed, so a reader never sees half a line.
# Written once: the first code recorded (the runner's own, or the signal's when it came first) is the run's.
recorded=""
_record_exit() {
  [ -n "$OUT" ] && [ -z "$recorded" ] || return 0
  recorded=1
  printf '%s\n' "$1" > "$OUT/.runner_exit.$$" 2>/dev/null && mv -f "$OUT/.runner_exit.$$" "$OUT/.runner_exit" 2>/dev/null
  rm -f "$OUT/.runner_exit.$$" 2>/dev/null
  return 0
}
watcher=""
_on_signal() {
  [ -z "$watcher" ] || kill "$watcher" 2>/dev/null
  [ -z "$runner" ] || kill -TERM "$runner" 2>/dev/null
  # Recorded before the sidecar's wind-down, which can take seconds a follow-up SIGKILL may not leave it.
  _record_exit "$1"
  _stop_sidecar
  exit "$1"
}

{ exec 3<&0; } 2>/dev/null || exec 3</dev/null
# A marker left by an earlier run in this directory must not read as this run's exit.
if [ -n "$OUT" ]; then
  mkdir -p "$OUT" 2>/dev/null
  rm -f "$OUT/.runner_exit" 2>/dev/null
  printf '%s\n' "$$" > "$OUT/.runner_pid" 2>/dev/null
fi
# The operator's identity markers, into the run's internal file before the agent starts (`operator_identity.py`):
# build_run_json grades `operator_identity_exposed` against them. Silent and best-effort: no value is ever printed.
[ -z "$OUT" ] || python3 "$_here/operator_identity.py" capture --out "$OUT" >/dev/null 2>&1 || true
# The runner command beside the run directory (never inside it: a run directory is what an upload packs, and the
# command names local paths) so a later re-run of the run's set (`/simulation-fixes --verify`) repeats it with another
# `--out`: one `shlex.join` line (bash `%q` writes `$'..'` for a newline or a byte outside the locale, which nothing
# else reads back; an argument with a newline spans lines inside its quotes), led by `PLURAI_OBSERVABILITY=<value>`
# when the run had one. The token is never an argument. Best-effort and silent.
if [ -n "$OUT" ]; then
  _cmdfile="$(dirname "${OUT%/}")/.$(basename "${OUT%/}").runner_command"
  python3 -c '
import os, shlex, sys
lead = ["PLURAI_OBSERVABILITY=" + os.environ["PLURAI_OBSERVABILITY"]] if os.environ.get("PLURAI_OBSERVABILITY") else []
print(shlex.join(lead + sys.argv[1:]))' "$@" >"$_cmdfile" 2>/dev/null || true
fi
if [ -n "$OUT" ]; then
  parent=$$
  started="$(mktemp "${TMPDIR:-/tmp}/run_stage.XXXXXX" 2>/dev/null)" || started=""
  # The last fill's moment (touched by the sidecar before each fill); without it only the heartbeat fills.
  filled="$(mktemp "${TMPDIR:-/tmp}/run_stage.XXXXXX" 2>/dev/null)" || filled=""
  set -m
  _sidecar "$parent" </dev/null >/dev/null 2>&1 &
  sidecar=$!
  set +m
fi
trap '_on_signal 143' TERM
trap '_on_signal 130' INT
trap '_on_signal 129' HUP
"$@" <&3 &
runner=$!
# The page's progress fill where the model publishes it (Claude Code): only a run under `runs/` is one the page counts.
if [ -n "$OUT" ] && [ "$N" -gt 0 ]; then
  case "$OUT" in */runs/* | runs/*) bash "$_here/run_tick.sh" --loop $$ "$OUT" "$N" "$K" </dev/null >/dev/null 2>&1 & ;; esac
fi
# THE OWNER (see the header).
# `ps -p`, not `kill -0`: an owner under another account (a `sudo` hop) answers EPERM, which is alive.
_owner_alive() {
  if [ -x /usr/bin/cygpath ]; then
    "${RUN_OWNER_PYTHON:-python3}" "$_here/run_start.py" --alive "$RUN_OWNER_PID" >/dev/null 2>&1
  elif command -v ps >/dev/null 2>&1; then
    ps -p "$RUN_OWNER_PID" >/dev/null 2>&1
  else
    kill -0 "$RUN_OWNER_PID" 2>/dev/null
  fi
}
case "${RUN_OWNER_PID:-}" in
  '' | *[!0-9]*) ;;
  *)
    ( while _owner_alive && kill -0 "$runner" 2>/dev/null; do sleep 2; done
      kill -0 "$runner" 2>/dev/null && kill -HUP $$ ) </dev/null >/dev/null 2>&1 &
    watcher=$!
    ;;
esac
wait "$runner"
code=$?
runner=""
[ -z "$watcher" ] || kill "$watcher" 2>/dev/null
watcher=""
# The runner's code, before the wind-down below can be interrupted.
_record_exit "$code"
_stop_sidecar
exit "$code"
