#!/usr/bin/env bash
# poll_job.sh — block until a backend job reaches a terminal state, in ONE tool call.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/poll_job.sh" <job_id> [max_seconds]
#
# ONE BLOCKING CALL PER WAIT. The sleep lives inside the SDK's `plurai job wait`, which runs in the
# FOREGROUND, so one invocation IS one wait and a wait cannot overlap the read after it. (The
# harness blocks a bare foreground `sleep` as a Bash command; `bash poll_job.sh ...` is not that
# command.) A backgrounded hand-typed loop returns to the model at once, and the model then polls
# on top of it.
#
# NEVER derive elapsed time from how many waits you intended — the transcript prints the SERVER's
# `elapsed_seconds` / `seconds_since_progress`, which are the only honest numbers.
#
# NEVER PIPE OR CAPTURE THIS SCRIPT. Its EXIT CODE is the result, and a pipeline reports the
# last command's — `poll_job.sh <id> | tail -5` exits 0 on a job that FAILED. Invoke it bare.
#
# WHAT THIS WRAPPER OWNS, and why the loop is not here. The wait loop, its stall thresholds, the
# health probe and the transcript are `plurai job wait` (SDK), which also serves callers with no
# plugin. What stays here is plugin state the SDK cannot see: the wrong-backend guard, the loop-log
# entry, and the fallback recipe that names this plugin's `wait.sh`.
#
# RESOLUTION. The guard sources resolve-plurai-urls.sh for $SERVER_URL (which refuses a
# non-loopback URL with no token). The SDK resolves the same backend on its own, so this script
# passes it no URL. The runner is the one `resolve_plurai.py` picks for the customer repo.
# The bearer stays in $PLURAI_TOKEN in the ENVIRONMENT: never a script argument, and the
# SDK sends it from its own process, so it is in no command line either.
#
# ENDPOINT. GET $SERVER_URL/jobs/<id>, which returns the SAME envelope as the MCP `job_status`
# tool: `kind`, `status`, `phase`, `elapsed_seconds`, `seconds_since_progress`, `error`, `detail`.
# The wait reads only the lifecycle fields; once the job is terminal, read the full `detail`
# envelope through MCP `job_status`.
#
# BUDGET. One invocation caps at `max_seconds` of WALL CLOCK (default 540 — 240 while a /simulate
# plan is live, so the page's card moves at least every 4 min — clamped to 30..540 by the SDK; a
# value that is not a whole number reads as the default) — under the harness's 600000 ms tool
# ceiling, with room for one in-flight read (20 s) and one health probe (5 s). The runner lookup
# before the wait takes a few seconds and the one status read after a terminal wait is cut at
# 10 s, so the worst case ends near 575 s. A job that outlives the budget is
# not an error: re-invoke to keep waiting. POLL_JOB_INTERVAL / POLL_JOB_MIN_SECONDS shorten the nap
# and the floor for hermetic tests, and the SDK honours them only against a loopback backend.
#
# THE PAGE'S CARD. While a /simulate plan is live (`publish_report.py live`), every wait ends —
# whatever its exit code — with `publish_report.py fill --step progress --job <id>`, which names the
# job's phase on the local overview page and pulls what the job has landed. It never publishes (the run
# publishes once, at its end) and prints nothing; it never changes this script's exit code, and it is
# bounded so the call still ends inside the tool ceiling.
#
# NO RUNNER. A project with no usable Python SDK (not installed yet, or not the version this plugin
# pins) gets ROUTE_MISSING and the MCP fallback recipe, exactly as a backend without GET /jobs/{id}
# does: both mean "this script cannot block on the job; wait with wait.sh and read job_status".
#
# EXIT CODES. The SDK's own table (`plurai.exit_codes`), passed through from `plurai job wait`; the
# names are in sdk-exit-codes.sh.
#   0   OK              job done
#  13   JOB_FAILED      job failed; the envelope's `error` is printed for YOUR classification, not the user.
#                       Or the gateway refused the read for credit (`could not read the status of job <id>
#                       (credit): <sentence>`): the job's state is UNKNOWN and it may have finished
#  15   NOT_FOUND       an id the backend does not know (its 404 carries the job envelope)
#  14   STILL_RUNNING   budget spent, job still running, backend HEALTHY (healthz 2xx): re-invoke to keep waiting
#  12   ROUTE_MISSING   no blocking wait here: the route is missing (a 404/405 with NO job envelope; the box
#                       predates GET /jobs/{id}), or no `plurai` runner with `job wait` is installed. Use the
#                       MCP fallback this script prints.
#  77   CREDENTIALS     bearer rejected (401/403) or none configured
#   7   VERSION_REFUSED  the backend refuses this version of the plugin or its SDK (the SDK exited UPGRADE_REQUIRED, 10):
#                       the plugin's text is printed alone, never the SDK's own
#  69   TRANSPORT       backend NOT RESPONDING or DOWN/UNHEALTHY, or UP with the job route faulting: the job's
#                       state is UNKNOWN
#  64   USAGE          usage error
#  65   REFUSED        before any read: the wrong-backend guard (SPLIT BACKENDS) or the client pin
#                       (client_pin: REFUSED). Stderr names which.

set -u

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sdk-exit-codes.sh
. "$_here/sdk-exit-codes.sh"

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
  echo "usage: poll_job.sh <job_id> [max_seconds]" >&2
  exit "$PLURAI_RC_USAGE"
fi
# The id names a state file below, and goes to the runner: letters, digits, `_`, `-` and `.` only, never `.` or `..`.
case "$JOB_ID" in
  . | .. | *[!A-Za-z0-9_.-]*)
    echo "usage: poll_job.sh <job_id> [max_seconds] — a job id holds only letters, digits, '_', '-' and '.'" >&2
    exit "$PLURAI_RC_USAGE"
    ;;
esac

# A malformed budget reads as the default: a skill passes whatever it computed, and the SDK would
# refuse it with a usage error (64), which no caller maps. The default is set once the flow dir is
# known (below): 240 under a live /simulate plan, else 540.
MAX="${2:-}"
case "$MAX" in
  '' | *[!0-9]*) MAX="" ;;
esac

# shellcheck source=assert-backend-identity.sh
# The guard, not the bare resolver: it sources resolve-plurai-urls.sh itself, so $SERVER_URL
# still lands, and with no argument it compares against the identity this session ARMED — which
# is the only cross-check this script can have, since it takes no identity argument and has no
# model in the loop to pass one. The empty argument is REQUIRED: a sourced script sees the
# CALLER's positional parameters, so without it the guard would read $JOB_ID as a bare host and
# manufacture a split on every poll.
. "$_here/assert-backend-identity.sh" "" || exit "$PLURAI_RC_REFUSED"

# A live /simulate plan (one with a stage still running) gets the page's card after the wait and a
# shorter default budget, so the card moves at least every 4 min. One definition: publish_report.py.
_live=""
if [ -n "${FLOWDIR:-}" ] && _plurai_stdlib "$_here/publish_report.py" live --flowdir "$FLOWDIR" >/dev/null 2>&1; then
  _live=1
fi
if [ -z "$MAX" ]; then
  if [ -n "$_live" ]; then MAX=240; else MAX=540; fi
fi

# Exit 3 from the resolver is the client pin refusing a usable runner: a hard stop, never a
# reason to fall back. Any other failure leaves PLURAI_BIN empty, which is the no-runner path.
# The resolver's stderr is shown only when it did not resolve: on success it is a run-log note
# (which runner, which version) that every poll of every job would otherwise repeat.
_resolver_err="$(mktemp "${TMPDIR:-/tmp}/poll_job.XXXXXX" 2>/dev/null)" || _resolver_err=""
_resolver_note() {
  [ -n "$_resolver_err" ] || return 0
  [ "${1:-}" = show ] && cat "$_resolver_err" >&2
  rm -f "$_resolver_err"
}
PLURAI_BIN="$(_plurai_stdlib "$_here/resolve_plurai.py" --require "job wait" "job status" 2>"${_resolver_err:-/dev/null}")" || { [ $? -eq 3 ] && { _resolver_note show; exit "$PLURAI_RC_REFUSED"; }; }
if [ -n "$PLURAI_BIN" ]; then _resolver_note; else _resolver_note show; fi

# The runnable wait, not a paraphrase: "wait, then read" gets rendered as a foreground `sleep`,
# which the harness blocks. It names wait.sh rather than printing a loop body, because a printed
# loop is retyped at the call site, and a retyped loop with a job STATUS as its exit predicate
# never exits. The paths are absolute from this script's own location: the caller's cwd is the
# CUSTOMER's repo, and $CLAUDE_PLUGIN_ROOT may be unset in this shell.
_fallback() {
  echo "poll_job: FALL BACK: bash \"${_here}/wait.sh\" 110   # FOREGROUND, tool timeout 600000"
  echo "poll_job: then job_status(id=${JOB_ID}) — TERMINAL STATUSES ARE done AND failed, nothing else."
  echo "poll_job: waits kept SERIAL. NEVER a bare foreground sleep, and never a loop of your own."
  echo "poll_job: Full recipe: ${_here%/*}/docs/polling-cadence.md."
}

if [ -z "$PLURAI_BIN" ]; then
  echo "poll_job: no \`plurai\` runner with \`job wait\` is installed for this repo (resolve_plurai.py says"
  echo "poll_job: why, above), so this script cannot block on job ${JOB_ID}."
  _fallback
  echo "poll_job: Installing the Python runner (scripts/resolve_plurai.py --install-line prints the command) gives the blocking poll."
  exit "$PLURAI_RC_ROUTE_MISSING"
fi

# Kill $1 and every process below it, the head last: the caller's `wait` returns when the head dies, and the watchdog
# running this is killed right after. A wrapped command is a chain (subshell, `env`, python, the SDK), and a kill of the
# head alone leaves the tail holding the caller's `$(...)` pipe open. Without `pgrep` (a host that has none) only $1 dies.
_kill_tree() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do _kill_tree "$child"; done
  kill "$1" 2>/dev/null
}

# Run "$@" for at most $1 seconds; its exit code, or the kill's. The watchdog's streams go to
# /dev/null so a caller's `$(...)` does not wait on its `sleep`. Bash 3.2 has no `timeout`.
_bounded() {
  local limit="$1" pid dog rc
  shift
  "$@" &
  pid=$!
  ( sleep "$limit"; _kill_tree "$pid" ) >/dev/null 2>&1 &
  dog=$!
  wait "$pid"
  rc=$?
  kill "$dog" 2>/dev/null
  wait "$dog" 2>/dev/null
  return "$rc"
}

# `kind`, `error` and `typed` of the job's current envelope, one per line; nothing when the read
# fails. Read AFTER the wait ends, because the loop log names the job's kind and the wait's
# transcript is text for the model, not a record to parse. Bounded at 10 s, well under the SDK's
# own 30 s, because it runs after a wait that may have spent its whole budget and the tool call
# still has to end inside 600 s. The read goes through `sdk_call.py`, so a floor that moved since the wait
# answers the plugin's refusal code with the plugin's text on stdout, and never the SDK's own.
_terminal_fields() {
  local envelope
  envelope="$(_bounded 10 _plurai_stdlib --bearer "$_here/sdk_call.py" "$PLURAI_BIN" job status "$JOB_ID" 2>/dev/null)" || {
    local code=$?
    [ "$code" != "$PLURAI_RC_VERSION_REFUSED" ] || printf '%s\n' "$envelope"
    return "$code"
  }
  printf '%s' "$envelope" | _plurai_stdlib -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
for key in ("kind", "error"):
    v = d.get(key)
    print("" if v is None else str(v).replace("\n", " ").replace("\r", " "))
# A typed block names the stop whatever `terminal_reason` says: the wire is additive, so an unavailable job reports
# `internal_error` beside its `unavailable` block.
print("unavailable" if isinstance(d.get("unavailable"), dict) or d.get("terminal_reason") == "unavailable" else "")
' 2>/dev/null
}

# Record the terminal outcome in the loop log, from the script that OBSERVED it. A bookkeeping
# call left to the model is one nothing forces, and a skipped one leaves no trace of the job at
# all; this waiter cannot be skipped without skipping the wait (the principle
# `hooks/pending_register.py` documents). Best-effort in every direction: it never changes this
# script's exit code, never prints on failure, and `append_loop_log.py` REFUSES rather than
# creating an artifacts tree in an arbitrary repo, so a job polled from outside an agent checkout
# writes nothing.
_log_outcome() {
  # $1 outcome, $2 kind, $3 note (may be empty)
  _plurai_stdlib "$_here/append_loop_log.py" --kind job --outcome "$1" \
    --finding "${2:-job} ${JOB_ID}" ${3:+--note "$3"} >/dev/null 2>&1 || true
}

# PLURAI_READER=model: a model reads this transcript, so the SDK adds the lines addressed to it (classify the error
# yourself, the MCP fallback, the wrong-backend check). Without it, `plurai job wait` writes for a person at a shell.
# Set on this one command only: the follow-up `job status` read and the helpers below never see it.
#
# The wait runs through `sdk_call.py`, which holds its output until its exit code is known: an upgrade-required exit
# carries the SDK's own text and a `pip` line, which the customer never reads. That exit prints the plugin's text instead
# and ends here with the plugin's refusal code, before the loop log and the page's card, which a refused call has no
# outcome for.
_plurai_stdlib --bearer PLURAI_READER=model "$_here/sdk_call.py" "$PLURAI_BIN" job wait "$JOB_ID" --max-seconds "$MAX"
rc=$?
[ "$rc" != "$PLURAI_RC_VERSION_REFUSED" ] || exit "$rc"

_unreachable_mark="${HOME:-${TMPDIR:-/tmp}}/.plurai/poll_unreachable/${JOB_ID}"
[ "$rc" = "$PLURAI_RC_TRANSPORT" ] || rm -f "$_unreachable_mark" 2>/dev/null

case "$rc" in
  "$PLURAI_RC_OK" | "$PLURAI_RC_JOB_FAILED")
    fields="$(_terminal_fields)"
    read_rc=$?
    if [ "$read_rc" = "$PLURAI_RC_VERSION_REFUSED" ]; then
      printf '%s\n' "$fields"
      exit "$PLURAI_RC_VERSION_REFUSED"
    fi
    J_KIND="$(printf '%s\n' "$fields" | sed -n 1p)"
    J_ERROR="$(printf '%s\n' "$fields" | sed -n 2p)"
    J_TYPED="$(printf '%s\n' "$fields" | sed -n 3p)"
    if [ "$rc" = "$PLURAI_RC_OK" ]; then
      # The wait saw `done`; a failed follow-up read costs only the kind in the entry.
      _log_outcome "done" "$J_KIND"
    elif [ "$read_rc" != "$PLURAI_RC_JOB_FAILED" ]; then
      # The wait saw `failed`. A follow-up read that did not answer costs the kind and the error
      # in the entry, never the entry itself. `job status` exits 13 only on a read the gateway
      # refused for credit: the wait's 13 beside it says nothing of the job, which may have
      # finished, so there is no outcome to log, as with any job whose state is unknown.
      _log_outcome failed "$J_KIND" "$J_ERROR"
    fi
    if [ "$J_TYPED" = unavailable ]; then
      echo "poll_job: run python3 \"${_here}/stop_message.py\" unavailable --done \"<outcome>\" and say only what it prints."
    fi
    ;;
  "$PLURAI_RC_NOT_FOUND")
    # An id this backend does not know: not an outcome of any job, so nothing is logged. The
    # SDK's advice names its own identity command; this plugin's comparison is the guard,
    # which also arms the session record every helper reads.
    echo "poll_job: in this plugin, that comparison is the guard: call backend_identity() and source"
    echo "poll_job: \"${_here}/assert-backend-identity.sh\" with its object. It reports SPLIT or RESTART."
    ;;
  "$PLURAI_RC_STILL_RUNNING")
    # The SDK names its own command. Re-invoking THIS script keeps the guard and the loop log.
    echo "poll_job: re-invoke poll_job.sh ${JOB_ID} to keep waiting (not \`plurai job wait\` directly)."
    ;;
  "$PLURAI_RC_TRANSPORT")
    # The first unreachable read names the one wait and the one re-invoke. The next one for this job is the stop, and its
    # line is the command and nothing else the model could repeat: no count, no number, no word about why. The count is a
    # mark per job under the user's plurai folder, good for 10 minutes and cleared by any read that answers.
    _stop="python3 \"${_here}/stop_message.py\" unavailable --done \"<outcome>\""
    _wait_once="Plurai could not be reached, so the job's state is unknown. Say nothing of the lines above; run bash \"${_here}/wait.sh\" 45 --quiet, then re-invoke poll_job.sh ${JOB_ID} ONCE after it."
    if [ -n "$(find "$_unreachable_mark" -mmin -10 2>/dev/null)" ]; then
      rm -f "$_unreachable_mark" 2>/dev/null
      echo "poll_job: run ${_stop} and say only what it prints."
    elif mkdir -p "${_unreachable_mark%/*}" 2>/dev/null && : > "$_unreachable_mark" 2>/dev/null; then
      echo "poll_job: ${_wait_once}"
    else
      echo "poll_job: ${_wait_once} If that read cannot reach Plurai either, run ${_stop} and say only what it prints."
    fi
    ;;
  "$PLURAI_RC_ROUTE_MISSING")
    _fallback
    echo "poll_job: this backend does not offer a blocking job wait; use the fallback above."
    ;;
esac

# The page's card (see THE PAGE'S CARD above). Best-effort in every direction: any failure prints
# nothing and the wait's exit code stands. Bounded by what is left of a 585 s call.
_progress() {
  local left
  left=$((585 - SECONDS))
  [ "$left" -ge 15 ] || return 0
  # shellcheck source=resolve-python.sh
  . "$_here/resolve-python.sh" 2>/dev/null
  _bounded "$left" "$PLURAI_PY" "$_here/publish_report.py" fill "$WORKDIR" --step progress --job "$JOB_ID" --flowdir "$FLOWDIR" >/dev/null 2>&1 || return 0
}
[ -z "$_live" ] || _progress
exit "$rc"
