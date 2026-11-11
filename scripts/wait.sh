#!/usr/bin/env bash
# wait.sh — block for a bounded number of SECONDS, in ONE tool call.
#
# The only wait in this plugin that is not `poll_job.sh`, and the only argument it takes is a
# DURATION. That is the whole design.
#
# WHY A SCRIPT AND NOT A ONE-LINER. A backgrounded loop such as
#
#     Bash("until [ $SECONDS -ge 120 ]; do sleep 10; done", run_in_background=true)
#
# is safe only because its exit predicate is a CLOCK. Written inline it teaches the shape "a
# backgrounded shell loop that waits for a job", which is one substitution away from a loop whose
# predicate is a job STATUS. Such a loop never exits when its terminal set is wrong, and a
# backgrounded shell has no tool timeout.
#
# A loop with no body cannot be adapted into that. A caller who wants to wait ON SOMETHING has to
# reach for `poll_job.sh`, which owns the terminal set and is bounded by wall clock either way.
#
# USAGE
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 110
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/wait.sh" 45 --quiet
#
# `--quiet` (before or after the duration) prints only `waited <n>s`: the closing hint to read the job
# points nowhere when the wait follows a failed call rather than a started job.
#
# Run it in the FOREGROUND. Its sleep is internal, so the harness's block on a bare foreground
# `sleep` does not apply (same as `poll_job.sh`), and the tool timeout then bounds it a second
# time — a wait that cannot outlive its turn is exactly what the incident above wanted.
#
# 110, not 120: the harness's DEFAULT tool timeout is 120 000 ms, so a 120 s wait sits exactly on
# its own boundary and gets killed there unless the caller remembers to raise it — and a harness
# kill is indistinguishable from a wedged box, which is the confusion the ceiling below exists to
# avoid. Ask for more than ~110 s and you owe the call `timeout: 600000`.
#
# EXIT CODES
#   0   the wait completed
#  64   usage error (no duration, a non-integer, or an argument that is not a duration at all)
#
# It has no other exit code on purpose: a wait has no verdict to report. Anything you would branch
# on comes from the `job_status` call you make AFTER it.

set -euo pipefail

# Ceiling shared with poll_job.sh: the harness tool timeout is 600000 ms, and a single invocation
# must leave headroom under it rather than being killed at the boundary (a harness kill is
# indistinguishable from a wedged box).
# Both overridable ONLY so the tests can drive them: a source-text assertion cannot prove the
# clamp clamps or that a zero-length nap breaks the loop, and those are exactly the two paths
# whose failure mode is an unbounded or zero-length wait.
MAX_SECONDS=${WAIT_SH_MAX_SECONDS:-540}
CHUNK=${WAIT_SH_CHUNK:-10}

_usage() {
  echo "usage: wait.sh <seconds> [--quiet]   # 1..${MAX_SECONDS} (larger is clamped, not refused)" >&2
  echo "wait.sh takes a DURATION and nothing else. To wait for a JOB, use poll_job.sh <job_id>:" >&2
  echo "  it owns the terminal status set (done|failed) and returns as soon as the job reaches it." >&2
  echo "  Never hand-roll a loop that exits on a job status — the backend reports success as 'done'," >&2
  echo "  and a loop watching for 'complete'/'completed'/'success' never exits at all." >&2
  exit 64
}

QUIET=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--quiet" ]; then QUIET=1; else ARGS+=("$a"); fi
done
[ "${#ARGS[@]}" -eq 1 ] || _usage
ARG="${ARGS[0]}"

# Refuse anything that is not a plain integer. This is the guard that makes the script
# unadaptable: a caller reaching for `wait.sh <job_id>` (or a status) is told, here, that this is
# not the tool for waiting on work — rather than having the argument silently ignored.
case "$ARG" in
  '' | *[!0-9]*) _usage ;;
esac
# …and refuse an absurd one BEFORE any arithmetic touches it: `[ 99999999999999999999 -ge 1 ]`
# is an "integer expression expected" error from bash itself, which would leak above the usage
# block and read as a broken script rather than a rejected argument.
[ "${#ARG}" -le 60 ] || _usage

# NORMALIZE TO BASE 10 — the whole reason this line exists. `[ … ]` converts numerically in base
# 10 while `$(( … ))` treats a leading zero as OCTAL, so an argument like `010` was compared
# against 10 and subtracted as 8: `nap` reached 0, `sleep 0` returned instantly, `elapsed` froze
# below the target, and the loop spun forever with no output — re-creating, inside this script,
# the unbounded hang it exists to prevent. `08`/`09` are not even valid octal, so they raised an
# arithmetic error that `set -e` did not stop and the script reported success having waited 0s.
# `10#` forces base 10 everywhere downstream.
SECS=$((10#$ARG))
[ "$SECS" -ge 1 ] || _usage
if [ "$SECS" -gt "$MAX_SECONDS" ]; then
  echo "wait.sh: ${SECS}s exceeds the ${MAX_SECONDS}s ceiling — waiting ${MAX_SECONDS}s. Re-invoke to keep waiting." >&2
  SECS="$MAX_SECONDS"
fi

# Chunked rather than one `sleep $SECS` so a SIGTERM from the harness lands promptly and the
# elapsed line below is truthful about how far it actually got.
elapsed=0
while [ "$elapsed" -lt "$SECS" ]; do
  nap=$((SECS - elapsed))
  [ "$nap" -gt "$CHUNK" ] && nap="$CHUNK"
  # BELT AND BRACES, independent of the normalization above: a non-advancing iteration is the
  # only shape that can loop forever here, so make it structurally impossible rather than relying
  # on the arithmetic being right. Any future defect that drives `nap` to 0 now exits the loop
  # and reports the honest elapsed instead of spinning silently.
  [ "$nap" -ge 1 ] || break
  sleep "$nap"
  elapsed=$((elapsed + nap))
done

if [ "$QUIET" = 1 ]; then
  echo "waited ${elapsed}s"
else
  echo "wait.sh: waited ${elapsed}s. Nothing was checked — read the job with job_status(id=…) now."
fi
