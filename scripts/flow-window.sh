#!/usr/bin/env bash
# flow-window.sh — arm / close a command's flow window, or sanction one ask, in ONE call.
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" arm      <command>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" close    <command>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction <gate>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" unsanction
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" unasked  <gate>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" ready    <command> [--no-backend-tools]
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" handoff  <command>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" simulate begin <stage>[,<stage>...]
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" simulate done|skip|rewind <stage>
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" simulate resume|end|next
#
# ONE invocation of ONE script, because that is what the permission classifier can allowlist
# in auto mode: a snippet combining `source`, a multi-target `rm -f` and a `>` redirect is
# denied there, and a denied arm runs the whole flow with both guards off. $FLOWDIR /
# $WORKDIR / $ART are resolved HERE, in the same process that writes them, so no caller can
# drop the `source` and write markers to the filesystem root.
#
# <command> is the MARKER NAME, not the slash command: generate-suite | run | analyze |
# scan-agent | fixes | traces | simulate (the report_guard profile keys).
#
# The marker holds ONE command name and NOTHING else, and is compared WHOLE-FILE.
#
# ARM also prints the dropped-turn HEARTBEAT instruction — only when it actually STAMPED, and
#   only until the cron EXISTS ($FLOWDIR/.flow_heartbeat_scheduled, written by
#   hooks/pending_register.py when it observes the CronCreate — never by this script, which
#   cannot know whether the call was made). Stop hooks do not fire when a turn
#   ends on an API error, so the pending contract cannot catch a turn killed mid-response; the
#   heartbeat is what re-drives the flow then, and hooks/flow_heartbeat.py blocks every firing
#   that is not that rescue. Scheduling it needs a tool call, which no hook can make. The job is
#   never deleted: a firing that outlives its flow finds no window and is blocked, and the job
#   itself is session-only (`durable` is not passed and defaults to false), so it dies with this
#   Claude session rather than persisting to .claude/scheduled_tasks.json. With no SessionStart
#   marker there is no hook to block a firing, so no heartbeat is asked for (`_heartbeat_notice`).
#
# ARM is a NO-OP on STATE when the window ALREADY names <command> — a re-arm is a resume, and the
#   twin hook (hooks/flow_autoarm.py) has always treated it that way. Nothing is cleared and
#   nothing is pruned, so a resumed build keeps the CONFIRMED findings its own runs produced.
#   It is NOT a no-op on the heartbeat instruction: a resume is exactly when the dropped-turn
#   rescue matters, so `_heartbeat_notice` runs on that path too.
#
# ARM otherwise clears the stale ask/report residue, then STAMPS <command> over whatever the window
#   holds — UNLESS the name already there DRIVES <command> as a sub-step (`_drivers_of()`,
#   mirroring hooks/flow_arm.py FLOW_DRIVERS, which report_guard reads too): a driven skill
#   must never clobber its driver's window, and a driven arm clears NOTHING (the residue is the
#   driver's live state, its run driver findings included) — as hooks/flow_autoarm.py does. An arm that actually STAMPED also opens
#   the evidence epoch through its ONE owner, scripts/open_epoch.py, which the hooks call too
#   when they stamp first. A DRIVEN arm keeps the findings file.
#   That owner also mints the command run id into $FLOWDIR (a resume keeps the window's own and mints only when it has none);
#   this script mints nothing itself, and `close` removes the id with the window.
#
# CLOSE is own-marker-guarded — an inner sub-step's early exit must not tear down the OUTER
#   driver's window — and reports, without touching it, any other name it finds.
#
# SANCTION writes the gate name to the one-shot $FLOWDIR/.ask_sanctioned that ask_guard
#   consumes. It is a BEARER TOKEN: write it immediately before the question, never ahead
#   of time. It buys one AskUserQuestion **CALL**, not one question — ask_guard consumes the
#   marker on the tool call and never inspects the questions inside it, so a gate that needs
#   several answers BATCHES them into that single call rather than sanctioning twice.
#
#   Inside a live /simulate run (a plan file in $FLOWDIR, hooks/_run_driver.py autonomous_plan) SANCTION answers the
#   run's decision gates itself, on every harness: the default is applied and recorded, no sanction is written, and
#   the line printed tells the model to apply it and continue. A plain-text or form ask is not always seen by
#   ask_guard, so the guard alone cannot hold that rule. A gate the run still asks (a credential, a fact only the
#   user has, the start call), a gate outside the armed flow's budget and a run with no live plan are sanctioned as
#   above; a declared headless run driver keeps its own dispatch.
#
# UNSANCTION revokes an unspent sanction (no argument). For the caller who wrote one and then
#   did not ask — the marker would otherwise silently authorize the next unrelated ask in the
#   window, including a DRIVER's. Revoking a marker that is not there is a no-op, not an error.
#   This subcommand exists so no skill text ever instructs a raw `rm` of a flow marker: a raw
#   path write/delete is the shape the auto-mode classifier denies (see hooks/_workdir.py on
#   the flow root's location).
#
# SIMULATE writes the /simulate run's plan ($FLOWDIR/.plurai_simulate.json) through its ONE writer,
#   hooks/simulate_continue.py, so no skill text ever writes the file itself:
#     begin <stages>   a fresh plan (stages ⊆ scan,traces,suite,run,fixes); clears halted and stalls
#     done <stage>     only for a stage with no window of its own (a present-only run); exit 3 unless
#                      the stage's file proof holds
#     skip <stage>     the user asked to go on without an OPTIONAL stage (traces only): the plan records it
#                      done and skipped, writing no artifact; exit 3 for any other stage
#     rewind <stage>   feedback: <stage> and every later stage leave `done` (a skipped one stays skipped unless it is <stage>);
#                      clears halted; re-opens an ended plan
#     resume           the user's message is answered: lifts an Esc pause, and reopens a run that turns which never
#                      moved it on stood down; a halted run says what lifts its halt. A run that was ended, or that
#                      finished every stage, stays closed: nothing is written and the line says so (exit 0)
#     end              finished: autonomy ends; also the "stop" route. With a plan in this session it also
#                      closes the window the run armed (/simulate's, or a stage command's), as `close` would.
#                      It is the user's stop: while a stage is unfinished it exits 3 and changes nothing, unless a
#                      person typed a message since the run was last directed (a typed answer to a gate does not
#                      count), the run is halted or paused, or no stage is left
#
# UNASKED is for a session with NO AskUserQuestion tool (`claude -p`, CI): it revokes the sanction,
#   applies the gate's SAFE default (hooks/_run_driver.safe_default), appends one
#   `run_driver_default_applied` row with `why: no-ask-tool` to $ART/.run_driver_findings.jsonl (never
#   creating $WORKDIR for it), and exits 0 — the flow continues, never blocks. The rows outlive the
#   arm sweep; a new build retires its rows, a resumed build keeps them (scripts/open_epoch.py).
#
# READY is Step 0's readiness check, run right after `arm`: it prints `ready`, or one line per unmet
#   check, each naming the fix (hooks/_readiness.py holds the checks). A `NOT READY` line stops the
#   flow. A `SETUP (login)` or `SETUP (connect)` line is something the harness-setup skill fixes in the
#   session: the flow runs the skill and goes on. Only the NOT READY lines print when any exist.
#   The answer is in the printed lines and the exit status is 0 for all of them: a harness shows a non-zero exit to the
#   user as an error. `--no-backend-tools` reports that the session's tool list has no backend tool, which no script can see.
#   On Claude Code with the plugin's hooks skipped it prints `ready, limited (hooks off)`: the flow runs in limited
#   mode (hooks/_readiness.py, ${CLAUDE_PLUGIN_ROOT}/docs/limited-mode.md), and the first time in the session the check
#   also prints a WARN line for the user and records `.hooks_degraded`, the key the limited-mode branches below read.
#
# HANDOFF is limited mode's stand-in for the Stop hook, run right after a hand-off: a NO-OP (silent) unless the session
#   is in limited mode (`_limited`: Claude Code, `.hooks_degraded` in $FLOWDIR, no `.hooks_ready` in $FLOWDIR).
#   There it closes the window when it holds <command>, and, when this session holds a /simulate plan whose next stage
#   is <command>'s (or <command> is simulate), records that stage done (exit 3 with the missing-file line when it did
#   not land) and prints how the run goes on, which the model does in the same turn. Any other command ran on its own
#   and resumes no plan, silently. A driven sub-step (its driver holds the window) is left alone, silently.
#   With hooks running report_guard does all of this at Stop, and must still find the window there.
#
# SIMULATE NEXT prints how a /simulate run goes on from its plan as it stands, and writes nothing.
#
# EXIT CODES
#   0  done (a close that found a foreign window is a no-op, not an error)
#   1  the window could not be resolved, read or written — or `arm` was asked to open a window
#      with no agent root resolved ($PLURAI_AGENT_RESOLVED=0) — or `ready` could not run its check
#   3  `simulate done` or `handoff` refused (no file proof for the stage), `simulate skip` of a stage that is not
#      optional or not in the plan, `simulate done|skip|rewind|resume|next` with no plan, or `simulate end` while
#      a stage is unfinished and no message the user typed since the run was last directed asked to stop
#  64  usage error — a missing or extra argument, an unknown subcommand, an unknown command
#      name, or a gate outside [A-Za-z0-9._-]

set -u

FLOW_MARKER=".plurai_flow_active"
# Records that THIS SESSION's heartbeat cron EXISTS — written by hooks/pending_register.py on an
# observed CronCreate carrying flow_heartbeat.SENTINEL, never here (see the arm branch).
# Session-scoped, swept only at session end.
HEARTBEAT_SCHEDULED=".flow_heartbeat_scheduled"
# Records that THIS SESSION cannot schedule the heartbeat (auto permission mode denies session
# crons) — written by hooks/cron_gate.py at the denied call, or by hooks/denial_watch.py when
# the classifier itself denied it. Session-scoped like the sentinel above, swept at session end.
HEARTBEAT_UNAVAILABLE=".flow_heartbeat_unavailable"
# Records that THIS window outlived its process, so its session-only cron is gone — written by
# hooks/flow_cleanup.py at a SessionEnd that kept the window. Whichever arm prints the notice first
# consumes it, so a resumed flow gets exactly one instruction, even when a driven arm comes first.
HEARTBEAT_LAPSED=".flow_heartbeat_lapsed"

ACTION="${1:-}"
ARG="${2:-}"
_USAGE="usage: flow-window.sh arm|close|handoff <command> | ready <command> [--no-backend-tools] | sanction|unasked <gate> | unsanction | simulate begin <stages> | simulate done|skip|rewind <stage> | simulate resume|end|next"
if [ "$ACTION" = "simulate" ]; then
  # Validated in full by the python writer (stage names, arity); exit 64 there on bad usage.
  if [ "$#" -lt 2 ]; then
    echo "$_USAGE" >&2
    exit 64
  fi
elif [ "$ACTION" = "unsanction" ]; then
  if [ "$#" -ne 1 ]; then
    echo "$_USAGE" >&2
    exit 64
  fi
elif [ "$ACTION" = "ready" ]; then
  if [ -z "$ARG" ] || [ "$#" -gt 3 ] || { [ "$#" -eq 3 ] && [ "${3:-}" != "--no-backend-tools" ]; }; then
    echo "$_USAGE" >&2
    exit 64
  fi
elif [ -z "$ACTION" ] || [ -z "$ARG" ] || [ "$#" -gt 2 ]; then
  echo "$_USAGE" >&2
  exit 64
fi

# THE driver table, mirroring hooks/flow_arm.py FLOW_DRIVERS: driven command -> the
# commands that invoke it as an inner sub-step. Keep the two in step — the hooks' tests
# assert this half against that one.
_drivers_of() {
  case "$1" in
    run) printf '%s' 'generate-suite analyze' ;;
    analyze) printf '%s' 'generate-suite' ;;
    *) printf '%s' '' ;;
  esac
}

_known_command() {
  case "$1" in
    generate-suite | run | analyze | scan-agent | fixes | traces | simulate) return 0 ;;
    *) return 1 ;;
  esac
}

# Every gate a skill's own text may sanction — kept in lockstep with hooks/ask_guard.py
# KNOWN_GATES (the tests assert equality) and pinned to the skills' own snippets. The sanction
# marker is a bearer token written by the same model ask_guard polices, so the NAME is the only
# machine-checkable part of one: refusing an unknown name HERE stops a self-minted consent gate
# before the marker ever exists, and ask_guard voids any that arrive another way.
_KNOWN_GATES="anonymization-posture apply-brief blocker component-pick corpus-reuse degraded-mode-consent delete-confirm document-generation extraction-scope fix-selection grounding-consent grounding-gate input-fix knowledge-audience observability-opt-in platform-key provider-key rescan-diff-approval saved-setup-gate simulate-start sink-project topology-approval trace-scenario-selection which-kb which-scenario workdir-location"
_known_gate() {
  case " $_KNOWN_GATES " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# The harness this shell belongs to, as resolve-workdir.sh names it in $PLURAI_HARNESS (empty: no harness
# exports a session id, which is Claude Code's shape). Unset, the innermost harness's session variable
# decides, in the resolver's order (a child process inherits every ancestor's).
_harness_name() {
  if [ -n "${PLURAI_HARNESS+x}" ]; then
    printf '%s' "${PLURAI_HARNESS:-claude}"
  elif [ -n "${COPILOT_AGENT_SESSION_ID:-}" ]; then
    printf '%s' copilot
  elif [ -n "${CODEX_SESSION_ID:-}" ] || [ -n "${CODEX_THREAD_ID:-}" ]; then
    printf '%s' codex
  else
    printf '%s' claude
  fi
}

_fw_dirname() {  # dirname(1) by parameter expansion: no PATH lookup decides where this script's files are
  _plurai_dn=$1
  if [ -x /usr/bin/cygpath ]; then  # native Windows: a backslash separates too, as it does for MSYS dirname
    while :; do case $_plurai_dn in *\\*) _plurai_dn="${_plurai_dn%%\\*}/${_plurai_dn#*\\}" ;; *) break ;; esac; done
  fi
  case $_plurai_dn in
    '') printf '.'; return 0 ;;
    *[!/]*) ;;
    *) printf '/'; return 0 ;;
  esac
  while :; do case $_plurai_dn in */) _plurai_dn=${_plurai_dn%/} ;; *) break ;; esac; done
  case $_plurai_dn in */*) ;; *) printf '.'; return 0 ;; esac
  _plurai_dn=${_plurai_dn%/*}
  while :; do case $_plurai_dn in */) _plurai_dn=${_plurai_dn%/} ;; *) break ;; esac; done
  printf '%s' "${_plurai_dn:-/}"
}
_here="$(CDPATH='' cd -- "$(_fw_dirname "${BASH_SOURCE[0]}")" && pwd)"

# The helpers below are stdlib-only and run with whatever the caller exported, `PLURAI_TOKEN`
# included, so they run under a fixed system interpreter, isolated, without the bearer: a `python3` found
# on PATH is whatever `PATH=/tmp/x:$PATH <script>` says it is. None found answers 127 (a helper that
# cannot run leaves the arm untouched, as its callers already treat any failure).
# On native Windows there is no fixed system interpreter; `trusted-python.sh` names the one the session pinned.
# shellcheck source=trusted-python.sh
. "$_here/trusted-python.sh"
_fw_python() {
  local py candidate
  if _plurai_on_windows; then
    py="$(_plurai_trusted_python "$_here/..")" || return 127
    /usr/bin/env -u PLURAI_TOKEN -u PLURAI_IDENTITY_KEY "$py" -I "$@"
    return
  fi
  for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$candidate" ]; then py="$candidate"; break; fi
  done
  [ -n "${py:-}" ] || return 127
  /usr/bin/env -u PLURAI_TOKEN -u PLURAI_IDENTITY_KEY "$py" -I "$@"
}

# `_run_driver.py` reads the config through PyYAML when it can, and decides a gate's default from
# what it reads, so it runs under the interpreter `resolve-python.sh` finds for PyYAML. It gets no
# bearer: that resolution may land on a `python3` from PATH, which is why the secrets are removed
# before it runs, not why it is trusted. (Its answer sets an run-driver stamp and applies a gate
# default; it does not say where anything is sent.)
_fw_yaml_python() {
  if [ -z "${_FW_PY:-}" ]; then
    # shellcheck source=resolve-python.sh
    . "$_here/resolve-python.sh" 2>/dev/null || true
    _FW_PY="${PLURAI_PY:-}"
  fi
  [ -n "$_FW_PY" ] || return 127
  /usr/bin/env -u PLURAI_TOKEN -u PLURAI_IDENTITY_KEY "$_FW_PY" -I "$@"
}
# The one dispatch for a gate nobody can answer: drop any sanction, apply the gate's SAFE default (or file
# the typed stop) through `_run_driver.py unasked`, and print the line to show. `unasked` calls it bare;
# a Codex `sanction` under a declared headless run driver calls it with `headless <where it was declared>`,
# because a plain-text ask never reaches ask_guard there. The python half never creates $WORKDIR for a
# record (unresolved, $ART is under /dev/null).
_fw_unasked() {
  local why="${1:-}" applied lead lost
  rm -f "$FLOWDIR/.ask_sanctioned"
  applied="$(_fw_yaml_python "$_here/../hooks/_run_driver.py" unasked "$ARG" "$FLOWDIR" "${ART:-}" ${why:+"$why"} 2>/dev/null)" || applied=""
  if [ "$why" = "headless" ]; then
    lead="run driver headless (${2:-}) — nobody answers an ask."
    lost="run driver headless — nobody answers an ask for '$ARG', and the record could NOT be written"
  else
    lead=""
    lost="no ask possible for '$ARG' and the record could NOT be written"
  fi
  if [ -n "$applied" ]; then
    echo "flow-window: ${lead:+$lead }$applied"
  else
    echo "flow-window: $lost — take the SAFE side (decline any external or host write; skip what only the user could supply), say so in ONE line naming it unrecorded, and continue the flow."
  fi
}

# shellcheck source=resolve-workdir.sh
. "$_here/resolve-workdir.sh"

if [ -z "${FLOWDIR:-}" ]; then
  echo "flow-window: \$FLOWDIR did not resolve — the flow window cannot be $ACTION-ed. See resolve-workdir.sh's stderr above." >&2
  exit 1
fi

MARKER="$FLOWDIR/$FLOW_MARKER"

# The window's content on stdout; $(cat) strips the trailing newline, so a marker written
# either way compares equal. Empty output + status 0 means NO window; a present-but-unreadable
# marker exits NON-ZERO and must not take the same branch — a close would report success on a
# window it left armed, and an arm would clobber a driver's.
_window() {
  [ -e "$MARKER" ] || return 0
  cat "$MARKER" 2>/dev/null
}

_read_window() {
  if ! current="$(_window)"; then
    echo "flow-window: $MARKER exists but could not be read — refusing to $ACTION a window whose state is unknown." >&2
    exit 1
  fi
}

# The dropped-turn HEARTBEAT instruction. A function because BOTH arm outcomes need it: the arm
# that stamped, and the SAME-NAME RESUME. A resume is the case the guard exists for — the flow
# came back from an interruption — and it is precisely then that the dropped-turn rescue matters,
# yet the early exit skipped this block entirely and a resumed flow ran with no rescue at all.
# (The DRIVEN arm prints it only when the window outlived its process: $HEARTBEAT_LAPSED.)
#
# Printed only ONCE PER SESSION: a session commonly runs several commands, and one cron per armed
# flow would leave several identical jobs firing on the same minute for the rest of the session.
# The sentinel is session-scoped like $FLOWDIR itself, and is deliberately NOT in the residue set
# the arm clears — it records what this SESSION was told to schedule, which the next flow in the
# same session does not undo. No hook can call CronCreate, so this is an instruction rather than
# an action — the same shape as `sanction`'s "ask it now".
# WHO is running this flow — the declared run driver, stamped beside the window exactly as the
# hook halves stamp it (hooks/flow_arm.py, hooks/flow_autoarm.py). ONE resolver for all three, so
# a window armed through the shell cannot disagree with one armed through a hook about the mode it
# ran under. Best-effort in every direction: the stamp is the run's RECORD, never a gate, so a
# python3 that cannot run it leaves the arm untouched.
_run_driver_stamp() {
  _mode="$(_fw_yaml_python "$_here/../hooks/_run_driver.py" stamp "$FLOWDIR" 2>/dev/null)" || _mode=""
  # The interactive default is the ordinary session and needs no line; a declared run driver does.
  [ -n "$_mode" ] && [ "$_mode" != "interactive default" ] && echo "flow-window: run driver ${_mode}."
  _headless_advisory
  return 0
}

# ADVISORY ONLY: the mode stays what was declared. Claude Code's print mode (`claude -p`) runs with
# CLAUDE_CODE_ENTRYPOINT=sdk-cli, and a nested session inherits that variable, so it decides nothing. A flow armed
# there with no declaration at all runs as interactive, and its background run dies when the turn ends; the line
# names the declaration so whoever starts the run can make it.
_headless_advisory() {
  [ "$_mode" = "interactive default" ] || return 0
  [ "${CLAUDE_CODE_ENTRYPOINT:-}" = "sdk-cli" ] || return 0
  [ "$(_harness_name)" = "claude" ] || return 0
  echo "flow-window: advisory: this session may be a print-mode run (\`claude -p\`) and no run driver is declared, so the flow runs as interactive. If nobody is watching, the session ends with its turn and work started in the background dies with it: whoever starts the run declares \`PLURAI_SIM_NONINTERACTIVE=1\` or \`workspace.run_driver: headless\`. Do not declare it yourself; say it in one line of your final message."
}

# Whether the harness has a scheduled-prompt tool to set the heartbeat with: Claude Code alone
# (hooks/registry.py `has_heartbeat`, which hooks/tests/test_heartbeat_by_harness.py pins this to).
# Elsewhere the notice below says nothing, not even a "skip this" line: the advice is for a tool the
# model does not have.
_has_heartbeat() {
  [ "$(_harness_name)" = "claude" ]
}

# Whether the plugin's hooks run in this session: a SessionStart marker in $FLOWDIR, and only there — the one rule
# `ready` (hooks/_readiness.py), `_limited` and hooks/_workdir.py `limited_mode` all read. The hook writes its marker into the
# session's flow dir, and a shell that exports no session id has $FLOWDIR = $WORKDIR, so its own marker is still found.
# A `.hooks_ready` in $WORKDIR beside a session-keyed $FLOWDIR may be one an earlier hooked session left behind: counting
# it would let `ready` say limited while `handoff`/`end` say hooked (a stalled run) and ask for an ungated heartbeat.
_hooks_marked() {
  [ -e "$FLOWDIR/.hooks_ready" ]
}

# Limited mode (hooks/_workdir.py `limited_mode`): Claude Code, Step 0's `.hooks_degraded` in $FLOWDIR, and no SessionStart
# marker there (`_hooks_marked`). The positive key, never a missing marker alone: only `ready` writes the record, and only
# when it found no marker.
_limited() {
  [ "$(_harness_name)" = "claude" ] && [ -e "$FLOWDIR/.hooks_degraded" ] && ! _hooks_marked
}

# CLOSE's body: the window named $1 (the caller checked it is the one armed) and its residue, everywhere.
_close_window() {
  rm -f "$MARKER" "$FLOWDIR/.flow_pending" "$FLOWDIR/.flow_lease" "$FLOWDIR/.flow_dropped" "$FLOWDIR/.run_driver" "$FLOWDIR/.command_run_id"
  # The heartbeat reads the UNION of both marker dirs, so the close must clear both — the
  # record is what releases a full-context rescue turn, and a survivor releases one for a
  # flow that just closed. Guarded on the resolved state, exactly as the arm residue clear is.
  if [ "${PLURAI_AGENT_RESOLVED:-1}" = "1" ] && [ -n "${WORKDIR:-}" ]; then
    rm -f "$WORKDIR/.flow_dropped"
  fi
  # The suite's account-window claim ends with its window: the key it recorded, plus this workdir's.
  if [ "$1" = "generate-suite" ]; then
    _claimed=""
    [ "${PLURAI_AGENT_RESOLVED:-1}" = "1" ] && _claimed="${WORKDIR:-}"
    if ! _fw_python "$_here/../hooks/_suite_window.py" release "$_claimed" "$FLOWDIR"; then
      echo "WARNING(flow-window): closed 'generate-suite' but its account-window claim was not released — it expires after 8 hours." >&2
    fi
  fi
  echo "flow-window: closed '$1'."
}

_heartbeat_notice() {
  _has_heartbeat || return 0
  # Without its hooks (no SessionStart marker; `arm` runs before `ready`) the heartbeat has no hook to hold its firings
  # back, so every one would reach the model as a prompt: none is asked for.
  _hooks_marked || return 0
  rm -f "$FLOWDIR/$HEARTBEAT_LAPSED"
  if [ -e "$FLOWDIR/$HEARTBEAT_UNAVAILABLE" ]; then
    # Silent: the permission mode denies session crons and the flow goes on without the rescue, which needs no action.
    # It prints on every later arm of the session, and naming the call here is what would get it re-attempted and re-denied.
    :
  elif [ -e "$FLOWDIR/$HEARTBEAT_SCHEDULED" ]; then
    # Silent: the sentinel is written by hooks/pending_register.py when it OBSERVES the cron, so the heartbeat exists and
    # there is nothing to do. A line that names the call invites a second one.
    :
  else
    echo "flow-window: heartbeat — set a heartbeat now, one call: cron \"*/7 * * * *\", prompt \"plurai-flow heartbeat\", recurring."
  fi
}

case "$ACTION" in
  arm)
    if ! _known_command "$ARG"; then
      echo "flow-window: '$ARG' is not a flow-window command name (generate-suite|run|analyze|scan-agent|fixes|traces|simulate)" >&2
      exit 64
    fi
    # ARM ONLY. $FLOWDIR is session-keyed and survives a resolver refusal, so arming used to
    # succeed on an UNRESOLVED agent — the flow then ran with its hooks policing a repository
    # nobody named. `close` and `sanction` stay ungated: a window armed earlier must stay
    # closable, and .ask_sanctioned is how the operator is asked which agent they meant.
    if [ "${PLURAI_AGENT_RESOLVED:-1}" != "1" ]; then
      echo "flow-window: refusing to arm '$ARG' — no agent root resolved (PLURAI_AGENT_RESOLVED=0). See resolve-workdir.sh's ERROR above. 'cd <agent-path>' is what SELECTS a root; PLURAI_AGENT_PATH only NARROWS which roots may be adopted, so exporting it alone does not resolve one. Do both, in this same call, and re-run. Arming here would police a repository nobody named." >&2
      exit 1
    fi
    # SAME-NAME RE-ARM = NO-OP, decided BEFORE anything is cleared. The hook twin already has
    # exactly this branch (hooks/flow_autoarm.py: `if current == command: return 0`); this
    # script only PRINTED `(was '$current')` and then stamped and pruned anyway.
    #
    # What that cost: a resumed build re-arms its own window, and the prune below took
    # agent_findings.md back to its `potential` sections — destroying the CONFIRMED findings
    # the build's own qualification runs had just produced, so the operator would have to reason
    # around the skill's own instruction to avoid deleting live evidence.
    #
    # The evidence epoch was decided by whichever writer STAMPED this window — usually the
    # entry hook, before this line ran (scripts/open_epoch.py).
    _read_window
    if [ "$current" = "$ARG" ]; then
      # Silent: a same-name re-arm changes nothing the user or the model has to act on.
      # The entry hook's epoch decision, said where the model reads it — or completed here, when
      # the hook could not name the agent (a session at a multi-agent repo root).
      _epoch="$(cat "$FLOWDIR/.epoch_opened" 2>/dev/null)"
      rm -f "$FLOWDIR/.epoch_opened"
      case "$_epoch" in
        "$ARG undecided:"*) _fw_python "$_here/open_epoch.py" "$ARG" "${WORKDIR:-}" ;;
        "$ARG "*) echo "flow-window: ${_epoch#*: }" ;;
      esac
      # A resume keeps this build's decisions but not its STOP rows: it re-reaches any gate still unmet.
      _fw_python "$_here/open_epoch.py" --flowdir "$FLOWDIR" --resume "$ARG" "${WORKDIR:-}"
      # A resume re-reads the declaration: a config edited between the two arms is the operator
      # saying something new, and the window's record has to be the CURRENT answer.
      _run_driver_stamp
      # …and the heartbeat instruction still goes out. A resume is EXACTLY the case that needs
      # the dropped-turn rescue — the flow is coming back from an interruption — and this early
      # exit used to skip it, so the guard that protects a resumed build's findings also took
      # away its way of surviving the next dropped turn.
      _heartbeat_notice
      exit 0
    fi
    # A DRIVEN arm is a no-op on everything, like the hook twin (hooks/flow_autoarm.py): the residue
    # below is the DRIVER's live state — its typed run driver findings above all — not a previous flow's.
    for driver in $(_drivers_of "$ARG"); do
      if [ "$current" = "$driver" ]; then
        # Silent: the driver's window stands and it owns the hand-off; the sub-step has nothing to say.
        # Silent in a live window (the driver printed it); a resumed window's cron died with its process.
        [ -e "$FLOWDIR/$HEARTBEAT_LAPSED" ] && _heartbeat_notice
        exit 0
      fi
    done

    # .flow_pending, .flow_lease and .flow_dropped join the residue set: a register, a lease or a
    # dropped-turn record left by an
    # abandoned flow would pre-authorize the NEXT flow's first mid-flow turn end with dead work.
    rm -f "$FLOWDIR/.ask_sanctioned" "$FLOWDIR/.ask_pending" "$FLOWDIR/.ask_deny_streak" "$FLOWDIR/.ask_breaker_spent" "$FLOWDIR/.report_guard_strikes" "$FLOWDIR/.flow_pending" "$FLOWDIR/.flow_lease" "$FLOWDIR/.flow_dropped" "$FLOWDIR/.run_driver_findings.jsonl"
    # The pre-session-keyed locations, where an older marker may still be lying around.
    # Guarded on the RESOLVED STATE, not on the path's emptiness: with an ambiguous agent root
    # resolve-workdir.sh points $WORKDIR at an unwritable sentinel, so `rm -f "$WORKDIR/…"`
    # would target something that is neither the marker nor the filesystem root.
    if [ "${PLURAI_AGENT_RESOLVED:-1}" = "1" ] && [ -n "${WORKDIR:-}" ]; then
      rm -f "$WORKDIR/.ask_sanctioned" "$WORKDIR/.ask_pending" "$WORKDIR/.ask_deny_streak" "$WORKDIR/.ask_breaker_spent" "$WORKDIR/.report_guard_strikes" "$WORKDIR/.flow_pending" "$WORKDIR/.flow_lease" "$WORKDIR/.flow_dropped" "$WORKDIR/.run_driver_findings.jsonl"
    fi

    if ! printf '%s' "$ARG" > "$MARKER"; then
      echo "flow-window: could not write $MARKER" >&2
      exit 1
    fi
    # The evidence epoch — decided and pruned by its ONE owner, never here.
    rm -f "$FLOWDIR/.epoch_opened"
    if ! _fw_python "$_here/open_epoch.py" --flowdir "$FLOWDIR" "$ARG" "${WORKDIR:-}"; then
      echo "WARNING(flow-window): armed '$ARG' but its evidence epoch could not be opened — agent_findings.md may still carry the PREVIOUS build's confirmed findings, and the run driver record its durable rows." >&2
    fi
    echo "flow-window: armed '$ARG'${current:+ (was '${current}')}."
    _run_driver_stamp
    _heartbeat_notice
    ;;

  close)
    if ! _known_command "$ARG"; then
      echo "flow-window: '$ARG' is not a flow-window command name (generate-suite|run|analyze|scan-agent|fixes|traces|simulate)" >&2
      exit 64
    fi
    _read_window
    if [ "$current" = "$ARG" ]; then
      _close_window "$ARG"
    else
      echo "flow-window: window holds '${current:-nothing}', not '$ARG' — left as it is."
    fi
    ;;

  sanction)
    # The gate name is a discovery key the ask_guard logs and the flow's own prose names;
    # keep it to the same alphabet the other marker writers use.
    case "$ARG" in
      *[!A-Za-z0-9._-]*)
        echo "flow-window: gate name must match [A-Za-z0-9._-]+, got: $ARG" >&2
        exit 64
        ;;
    esac
    if ! _known_gate "$ARG"; then
      echo "flow-window: '$ARG' is not a gate any skill defines — refusing to sanction it. Sanctions exist only for the asks a skill's own text scripts at a named step (plus 'blocker', for a genuine PREREQUISITE only the user can supply — never a decision). An action the flow's own text instructs — an upload it scripts, a sub-agent it mandates, a file it writes — is pre-authorized by the invocation: do it and announce it in product terms, never ask consent for it. Known gates: $_KNOWN_GATES" >&2
      exit 64
    fi
    _harness="$(_harness_name)"
    _mode="$(_fw_yaml_python "$_here/../hooks/_run_driver.py" mode 2>/dev/null)" || _mode=""
    if [ "$_harness" = "codex" ] && [ "${_mode%% *}" = "headless" ]; then
      # A Codex ask is plain text or the async tool, and neither reaches the ask guard in a form that
      # could dispatch it, so a declared headless run driver is dispatched at THIS step: nothing is
      # sanctioned, the gate's default is applied and recorded (or the typed stop filed).
      _fw_unasked headless "${_mode#* }"
      exit 0
    fi
    if [ "${_mode%% *}" != "headless" ]; then
      # A live /simulate run answers its decision gates itself, and a plain-text or form ask is not always
      # seen by the ask guard, so the answer is applied at THIS step on every harness: nothing is
      # sanctioned, the decision is recorded, and the model is told to apply it and continue. A gate the
      # run still puts to the user (a credential, a fact only the user has, the start call), a gate
      # outside the armed flow's budget, a gate sanctioned with no window armed (except the knowledge-base
      # gates, _run_driver.UNWINDOWED_GATES), and a run with no live plan fall through to the sanction below;
      # so does a python that cannot run the helper, and the guard remains the second line where it sees the ask.
      _applied="$(_fw_yaml_python "$_here/../hooks/_run_driver.py" autonomous "$ARG" "$FLOWDIR" "${ART:-}" 2>/dev/null)" && [ -n "$_applied" ] && {
        rm -f "$FLOWDIR/.ask_sanctioned"
        echo "flow-window: $_applied"
        exit 0
      }
    fi
    if ! printf '%s' "$ARG" > "$FLOWDIR/.ask_sanctioned"; then
      echo "flow-window: could not write $FLOWDIR/.ask_sanctioned" >&2
      exit 1
    fi
    # One line: the rules for the ask (one call, every question batched, within ten minutes, `unasked` where nobody can be
    # asked, the shape of a Codex ask) are in the skills' error-posture text, which the model already reads.
    echo "flow-window: one question allowed ($ARG)."
    ;;

  unasked)
    if ! _known_gate "$ARG"; then
      echo "flow-window: '$ARG' is not a gate any skill defines — nothing to default. Known gates: $_KNOWN_GATES" >&2
      exit 64
    fi
    _fw_unasked
    ;;

  ready)
    if ! _known_command "$ARG"; then
      echo "flow-window: '$ARG' is not a flow-window command name (generate-suite|run|analyze|scan-agent|fixes|traces|simulate)" >&2
      exit 64
    fi
    # The check never sees the bearer (_fw_python removes it); it is told only whether one was exported.
    _bearer_exported=""
    [ -n "${PLURAI_TOKEN:-}" ] && _bearer_exported=1
    PLURAI_READINESS_TOKEN_PRESENT="$_bearer_exported" _fw_python "$_here/../hooks/_readiness.py" check "$ARG" "$FLOWDIR" ${3:+"$3"}
    _status=$?
    # The answer is the printed lines. A harness shows a non-zero exit to the user as an error, so a check that ran exits 0
    # whether it printed `ready`, `SETUP` or `NOT READY`; only a check that could not run fails.
    if [ "$_status" -ne 0 ]; then
      echo "flow-window: the readiness check could not run (exit $_status) — treat the session as NOT READY and stop." >&2
      exit 1
    fi
    exit 0
    ;;

  handoff)
    if ! _known_command "$ARG"; then
      echo "flow-window: '$ARG' is not a flow-window command name (generate-suite|run|analyze|scan-agent|fixes|traces|simulate)" >&2
      exit 64
    fi
    # Silent unless limited: with hooks running, report_guard closes the window at Stop and continues the run, and
    # must still find the window there.
    _limited || exit 0
    _read_window
    # A driven sub-step (the suite's own run) hands nothing off: its driver's window stands and its driver hands off.
    for driver in $(_drivers_of "$ARG"); do
      [ "$current" = "$driver" ] && exit 0
    done
    [ "$current" = "$ARG" ] && _close_window "$ARG"
    _sim_workdir=""
    [ "${PLURAI_AGENT_RESOLVED:-1}" = "1" ] && _sim_workdir="${WORKDIR:-}"
    for _base in "$FLOWDIR" "$_sim_workdir"; do
      if [ -n "$_base" ] && [ -f "$_base/.plurai_simulate.json" ]; then
        _fw_python "$_here/../hooks/simulate_continue.py" handoff "$ARG" --flowdir "$FLOWDIR" --workdir "$_sim_workdir"
        exit $?
      fi
    done
    exit 0
    ;;

  simulate)
    # The plan's ONE writer. $WORKDIR is passed only when an agent root resolved: the stage proofs
    # read $WORKDIR/artifacts, and an unresolved sentinel path must never be recorded as the agent.
    _sim_workdir=""
    [ "${PLURAI_AGENT_RESOLVED:-1}" = "1" ] && _sim_workdir="${WORKDIR:-}"
    shift
    # `end` is an explicit stop: no hand-off is owed any more, so it closes the window a stage of this
    # run armed (or /simulate's own) — left armed, report_guard blocked the next turn end as mid-flow.
    # Only when this session holds a plan (no run, nothing it armed), and fail-open: the end itself landed.
    # The names: `simulate` plus hooks/simulate_continue.py STAGE_OF_COMMAND's commands.
    _sim_plan=""
    if [ "$*" = "end" ]; then
      for _base in "$FLOWDIR" "$_sim_workdir"; do
        [ -n "$_base" ] && [ -f "$_base/.plurai_simulate.json" ] && _sim_plan=1
      done
    fi
    _fw_python "$_here/../hooks/simulate_continue.py" "$@" --flowdir "$FLOWDIR" --workdir "$_sim_workdir"
    _sim_rc=$?
    if [ "$_sim_rc" -eq 0 ] && [ -n "$_sim_plan" ]; then
      if current="$(_window)"; then
        case "$current" in
          simulate | scan-agent | traces | generate-suite | run | fixes) _close_window "$current" ;;
        esac
      else
        echo "WARNING(flow-window): the run ended, but its window could not be read and was left as it is." >&2
      fi
    fi
    exit $_sim_rc
    ;;

  unsanction)
    if [ -e "$FLOWDIR/.ask_sanctioned" ]; then
      if rm -f "$FLOWDIR/.ask_sanctioned"; then
        echo "flow-window: revoked the unspent sanction."
      else
        echo "flow-window: could not remove $FLOWDIR/.ask_sanctioned" >&2
        exit 1
      fi
    else
      echo "flow-window: no unspent sanction to revoke."
    fi
    ;;

  *)
    echo "$_USAGE" >&2
    exit 64
    ;;
esac
exit 0
