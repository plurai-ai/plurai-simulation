# resolve-workdir.sh — export ABSOLUTE $PLURAI_DIR / $WORKDIR / $ART / $FLOWDIR for skill snippets.
#
# Source it (never execute):  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
#
# Why absolute: the harness resets the shell's cwd between tool calls, so a relative
# `plurai_sim/...` in one snippet silently lands somewhere else in the next (observed live:
# marker files written to the wrong directory three times in one session, plus a lost runner
# log). Every marker read/write in a skill snippet should go through these variables.
#
# Anchor resolution, in order (cwd-independent AND nested-layout-aware — an agent living in a
# subdirectory of a monorepo (`<repo>/some-agent/plurai_sim/`) is a documented layout, and a
# git-root-only anchor silently created a phantom `<repo>/plurai_sim/` beside the real one):
#   1. walk UP from $PWD for the nearest directory containing `plurai_sim/`;
#   1.5 with `PLURAI_AGENT_PATH` EXPLICITLY set, that ancestor must sit at or below it — an
#      ancestor belonging to another agent is refused exactly like step 4;
#   2. else, if the repo contains exactly ONE `plurai_sim/` within 3 levels AND that match sits
#      at or below the agent scope (${PLURAI_AGENT_PATH:-$PWD}), use its parent;
#   3. else, with NONE found, fall back to the git toplevel (or $PWD outside a repo);
#   4. else — SEVERAL found and cwd outside all of them, or the SOLE match belonging to another
#      agent outside the scope — refuse: $WORKDIR/$PLURAI_DIR/$ART are pointed at an UNWRITABLE
#      sentinel (an ERROR on stderr names the candidates) so a write fails instead of landing in
#      a guessed <repo>/plurai_sim/artifacts/ or, worse, in a DIFFERENT agent's workdir.
#      $FLOWDIR still resolves — it is session-keyed, needs no agent, and is what the
#      .ask_sanctioned / flow-close snippets depend on.
# The scope test is why `PLURAI_AGENT_PATH` exists: a match is only adopted when it is the agent
# you are pointed at. Set it (or `cd` into the agent) when the repo holds another agent's
# plurai_sim/ and yours does not exist yet. When it is set it binds EVERY step, the
# nearest-ancestor one included — scoping step 2 alone still let a foreign cwd win silently.
# hooks/_workdir.py mirrors the step-1.5 and step-2 SCOPE TESTS but deliberately does NOT mirror
# the refusal — it resolves flow markers for hooks, which must fail open, so where this script
# refuses it simply declines to adopt the foreign match and falls through to the git toplevel.
# Then apply the named-env layout exactly as /simulation-scan-agent Step 0 documents.
# $WORKDIR and $PLURAI_DIR are the same path — two names because older skills grew up with
# either; $ART is the gitignored artifacts dir.
#
# $FLOWDIR is DIFFERENT IN KIND and deliberately not derived from the agent at all — see the
# block above its assignment at the bottom of this file.

# The value the agent-derived paths take when this script REFUSES to name an agent. A path
# under /dev/null cannot be created or written on any POSIX system (ENOTDIR), so a caller that
# ignores stderr still fails at the write rather than succeeding somewhere wrong.
_UNRESOLVED="/dev/null/plurai-workdir-unresolved"

# WHICH cascade step named the root, reported on stderr beside the path. A resolver that only
# says WHERE it landed cannot be checked by the operator reading the transcript: the live hijack
# looked identical to a correct resolve until someone opened the other agent's directory.
_VIA="unresolved"

# Where the cascade starts: $PWD when it is POSIX-absolute, else the physical cwd. The Agent SDK
# exports a Windows-form PWD (`C:\Users\…\proj`) to its child on native Windows, and a relative
# one is possible too; neither reaches `/`, so the walk starts from the physical cwd. `pwd -P` reads
# getcwd, which Git Bash returns as `/c/Users/…`. A normal `/…` PWD keeps its logical form.
case $PWD in /*) _rw_start=$PWD ;; *) _rw_start=$(pwd -P 2>/dev/null || printf '%s' "$PWD") ;; esac

# The AGENT SCOPE — the subtree a repo-wide match is allowed to come from. Read here, before the
# cascade, so every step sees the same value. Default $PWD; `export PLURAI_AGENT_PATH=<agent-path>`
# names the agent explicitly when cwd cannot (a snippet whose cwd the harness reset, a repo whose
# only plurai_sim/ belongs to someone else). It CONSTRAINS step 2 only — it never invents a root.
_AGENT_SCOPE="${PLURAI_AGENT_PATH:-$_rw_start}"

# Physical paths for the scope test: $PWD is logical, `find` and `git rev-parse` return physical,
# and a symlinked checkout (/tmp -> /private/tmp) would otherwise read as "outside the scope".
_plurai_canon() { (CDPATH='' cd -- "$1" 2>/dev/null && pwd -P) || printf '%s\n' "$1"; }
_rw_dirname() {  # dirname(1) by parameter expansion: no PATH lookup decides where this script's files are
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
_plurai_within() {  # $1 at or below $2 ?
  case "$1" in
    "$2" | "$2"/*) return 0 ;;
  esac
  return 1
}

# Step 1 — walk UP from $PWD for the nearest ancestor holding a plurai_sim/.
_PLURAI_ROOT=""
_probe="$_rw_start"
# `.` and a probe that stops changing end the walk too: a drive path (`C:/x` → `C:` → `.`) never reaches `/`.
while [ -n "$_probe" ] && [ "$_probe" != "/" ] && [ "$_probe" != "." ]; do
  if [ -d "$_probe/plurai_sim" ]; then
    _PLURAI_ROOT="$_probe"
    _VIA="nearest-ancestor"
    break
  fi
  _next="$(_rw_dirname "$_probe")"
  [ "$_next" = "$_probe" ] && break
  _probe="$_next"
done
unset _probe _next

# Step 1.5 — the ancestor must be the agent you NAMED. An explicitly-set `PLURAI_AGENT_PATH` binds
# this step as well as step 2, through the same refusal machinery as step 4: otherwise, from a cwd
# inside ANOTHER agent's tree, step 1 would win silently and the flow's hooks would police the
# wrong repository.
if [ -n "$_PLURAI_ROOT" ] && [ -n "${PLURAI_AGENT_PATH:-}" ] && ! _plurai_within "$(_plurai_canon "$_PLURAI_ROOT")" "$(_plurai_canon "$PLURAI_AGENT_PATH")"; then
  echo "ERROR(resolve-workdir): the nearest plurai_sim/ above \$PWD belongs to another agent:" >&2
  echo "  found:              $_PLURAI_ROOT/plurai_sim" >&2
  echo "  PLURAI_AGENT_PATH:  $PLURAI_AGENT_PATH" >&2
  echo "The found root is not at or below the agent you named, so adopting it would silently run this command against a DIFFERENT agent. Refusing to guess — \$WORKDIR/\$PLURAI_DIR/\$ART point at $_UNRESOLVED and every write under them fails. Fix: \`cd $PLURAI_AGENT_PATH\` (creating its plurai_sim/ if needed) before sourcing, or unset PLURAI_AGENT_PATH if $_PLURAI_ROOT is the agent you meant." >&2
  _AMBIGUOUS_ROOT="$_PLURAI_ROOT"
  _VIA="unresolved"
fi

# Steps 2/3 — nothing above us: take the repo's SOLE plurai_sim/, else the git toplevel.
if [ -z "$_PLURAI_ROOT" ]; then
  _git_root="$(git rev-parse --show-toplevel 2>/dev/null || printf '%s' "$_rw_start")"
  # Git for Windows prints the toplevel in drive form (`C:/Users/…`); WORKDIR is POSIX-form like the walk-up's.
  case $_git_root in
    /*) ;;
    *) if [ -x /usr/bin/cygpath ]; then _git_root="$(/usr/bin/cygpath -u "$_git_root" 2>/dev/null || printf '%s' "$_git_root")"; fi ;;
  esac
  _matches="$(find "$_git_root" -maxdepth 3 -type d -name plurai_sim -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null)"
  _match_count="$(printf '%s\n' "$_matches" | grep -c .)"
  _sole_parent=""
  if [ "$_match_count" = "1" ]; then
    # SCOPED, not unconditional. A lone match elsewhere in the repo is a DIFFERENT agent, and
    # adopting it is silent: a fresh scan of `agent_a/` would resolve into `app/agent/plurai_sim/`
    # because that is the repo's only one. Two matches are refused, so exactly one must not be
    # trusted on faith either.
    _sole_parent="$(_plurai_canon "$(_rw_dirname "$_matches")")"
    if _plurai_within "$_sole_parent" "$(_plurai_canon "$_AGENT_SCOPE")" || [ "$_sole_parent" = "$(_plurai_canon "$_rw_start")" ]; then
      _PLURAI_ROOT="$(_rw_dirname "$_matches")"
      _VIA="repo-sole-match"
    else
      echo "ERROR(resolve-workdir): the only plurai_sim/ under $_git_root belongs to another agent:" >&2
      printf '%s\n' "$_matches" | sed 's/^/  /' >&2
      echo "It is not at or below $_AGENT_SCOPE, so adopting it would silently run this command against a DIFFERENT agent. Refusing to guess — \$WORKDIR/\$PLURAI_DIR/\$ART point at $_UNRESOLVED and every write under them fails. Fix: \`mkdir -p <agent-path>/plurai_sim\` and re-source (the nearest-ancestor rule then wins), or \`export PLURAI_AGENT_PATH=<agent-path>\` before sourcing." >&2
      _AMBIGUOUS_ROOT="$_git_root"
      _PLURAI_ROOT="$_git_root"
    fi
  elif [ "$_match_count" = "0" ]; then
    # No agent anywhere yet: the git root is the right anchor for the snippet that CREATES the
    # first plurai_sim/. Nothing to be ambiguous about.
    _PLURAI_ROOT="$_git_root"
    _VIA="git-toplevel"
  else
    # AMBIGUOUS — refuse to name an agent. Anchoring at the repo root is what created a phantom
    # <repo>/plurai_sim/artifacts/ beside the real agents: no caller checks stderr, so
    # `source …; mkdir -p "$ART"` exited 0 and a findings file landed where nothing reads it.
    #
    # Refuse by pointing the agent-derived paths at an UNWRITABLE sentinel rather than by
    # exiting. `exit` in a sourced file kills the caller's shell, which would also take down
    # $FLOWDIR — and $FLOWDIR is session-keyed, needs no agent, and carries the flow markers:
    # the .ask_sanctioned write is the ONLY way to ask the user which directory they meant, and
    # closing .plurai_flow_active is the only way to end the run. Exiting here deadlocked
    # exactly the layout this branch is for.
    #
    # NOT the empty string, which is what this used to be: "$PLURAI_DIR/.mock_service_url" with
    # PLURAI_DIR="" is the valid path /.mock_service_url, so the refusal wrote to the filesystem
    # ROOT instead of failing. Under /dev/null/ every open and mkdir fails with ENOTDIR on any
    # POSIX system, and the path names itself in the error the caller prints.
    echo "ERROR(resolve-workdir): $_match_count plurai_sim/ directories under $_git_root:" >&2
    printf '%s\n' "$_matches" | sed 's/^/  /' >&2
    echo "Refusing to guess — \$WORKDIR/\$PLURAI_DIR/\$ART point at $_UNRESOLVED and every write under them fails. cd into the intended agent directory (the one holding its plurai_sim/) and re-run." >&2
    _AMBIGUOUS_ROOT="$_git_root"
    _PLURAI_ROOT="$_git_root"
  fi
  unset _git_root _matches _match_count _sole_parent
fi

# Named-env layout (/simulation-scan-agent Step 0) when envs/ AND .active-env are both present.
_ENVS_DIR="${_PLURAI_ROOT}/plurai_sim/envs"
_ACTIVE_ENV="${_PLURAI_ROOT}/plurai_sim/.active-env"
if [ -d "$_ENVS_DIR" ] && [ -f "$_ACTIVE_ENV" ]; then
  WORKDIR="${_ENVS_DIR}/$(cat "$_ACTIVE_ENV")"
else
  if [ -d "$_ENVS_DIR" ]; then
    # envs/ without .active-env used to fail loudly at the callers' `$(cat ...)`; a silent
    # fallback here would write a bogus root-level manifest instead. Warn — the operator
    # must pick an env (see /manage-envs).
    echo "WARNING(resolve-workdir): ${_ENVS_DIR} exists but .active-env is missing — falling back to the bare plurai_sim/ layout. Activate an env (ask me to switch environments) if this repo uses named envs." >&2
  fi
  WORKDIR="${_PLURAI_ROOT}/plurai_sim"
fi

PLURAI_DIR="$WORKDIR"
ART="$WORKDIR/artifacts"

# Ambiguous anchor: point the agent-derived paths somewhere unwritable so nothing can be
# written under a guessed root. $FLOWDIR is computed below and deliberately survives — see the
# refusal branch above.
PLURAI_AGENT_RESOLVED=1
if [ -n "${_AMBIGUOUS_ROOT:-}" ]; then
  WORKDIR="$_UNRESOLVED"
  PLURAI_DIR="$_UNRESOLVED"
  ART="$_UNRESOLVED/artifacts"
  # The STATE, exported beside the paths. Callers that need to know "did an agent root
  # resolve?" must read this and never infer it from the paths: emptiness was the old signal
  # and it was wrong in both directions — `"$EMPTY/x"` is the writable path `/x`, and any
  # non-empty sentinel silently satisfies a `[ -n "$ART" ]` test.
  PLURAI_AGENT_RESOLVED=0
fi
export PLURAI_AGENT_RESOLVED

# Say what resolved, and by which rule, when the root was not the obvious one — after the sentinel is applied, so the
# path printed is the one the caller will actually write to. Walking up from $PWD to a plurai_sim/ is the normal case and
# prints nothing: the line is shown to the user with every Bash result, so repeating it on each of the hundreds of
# sources buries the ones that matter (a first scan, a lone match below the cwd, a refusal). Deliberately not a WARNING:
# this is the receipt an operator reads back, not a problem to relay.
if [ "$_VIA" != "nearest-ancestor" ]; then
  echo "resolve-workdir: WORKDIR=$WORKDIR (via $_VIA)" >&2
fi

# $FLOWDIR — the flow-fidelity markers (.plurai_flow_active / .ask_sanctioned /
# .report_guard_strikes / .flow_pending), keyed by SESSION rather than by agent.
#
# Everything else this script exports is per-plurai_sim, because everything else IS the agent's
# simulation state (.scenario_set_id, .eval_set_id, artifacts/). The flow markers are not:
# they answer "is a command flow running in THIS conversation", which is used to deny
# AskUserQuestion and to validate the final Stop message — both acts of the session, of which
# there is exactly one per turn no matter how many plurai_sim directories exist.
#
# Filing them per-agent breaks enforcement: the skills resolve $WORKDIR from the
# Bash tool's cwd (which a skill can cd into), while the hooks resolve from the SESSION's cwd. A
# session driving an agent in another repo wrote the marker where the skill looked and the hooks
# looked somewhere else — so ask_guard and report_guard silently allowed everything for a whole
# run. Keying on the session id, which both sides observe identically, removes the resolution
# step from the enforcement path entirely.
#
# The fallback: no session id (or an unwritable home) => $WORKDIR, so the
# markers keep their current names. The hooks
# dual-read both locations for the same reason.
# _AMBIGUOUS_ROOT FIRST, not `${WORKDIR:-…}`: on the refusal branch $WORKDIR is the unwritable
# sentinel, which is non-empty, so a `:-` fallthrough keyed on WORKDIR would hand $FLOWDIR the
# sentinel too — and the flow markers are the ONE thing that branch must keep working, since
# .ask_sanctioned is how the operator is asked which agent they meant.
FLOWDIR="${_AMBIGUOUS_ROOT:-${WORKDIR:-$PWD}}"
# The id is INHERITED by a `claude` started from another session's Bash tool (a fleet operator, a
# script run from Claude): this shell would see the PARENT's id while this session's hooks key on
# its own, so the markers landed in the parent's folder and its Stop hook enforced a flow it never
# ran. The nearest Claude process above this shell names the real session; it wins, and is exported
# so every script later in the same call agrees. Only a value naming ANOTHER running session is
# replaced; an id no session record knows (a test's, an older Claude Code's) stands as it is.
# It applies only when Claude Code is the innermost harness (neither Copilot's nor Codex's variable
# is set), the same precedence as the session id below.
if [ -z "${COPILOT_AGENT_SESSION_ID:-}${CODEX_THREAD_ID:-}" ] && [ -n "${CLAUDE_CODE_SESSION_ID:-}${CLAUDE_PID:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT:-}/hooks/_workdir.py" ]; then
  # Prints "<pid> <session id>", each field empty unless that variable was inherited from another session.
  _fix="$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_workdir.py" corrected 2>/dev/null)" || _fix=""
  _fix_pid="${_fix%% *}"; _fix_sid="${_fix#* }"
  [ "$_fix_sid" = "$_fix" ] && _fix_sid=""
  [ -n "$_fix_sid" ] && export CLAUDE_CODE_SESSION_ID="$_fix_sid"
  [ -n "$_fix_pid" ] && export CLAUDE_PID="$_fix_pid"
  unset _fix _fix_pid _fix_sid
fi
# The session id is the innermost harness's: Copilot's variable, then Codex's, then Claude Code's.
# Each harness exports its own fresh, and a child process inherits every ancestor's, so Copilot
# started from a Claude Code shell sees both and the first one set is the harness actually running.
# Keep in lockstep with hooks/_workdir.py SESSION_VARS.
# Codex's session variable comes before its thread variable: a sub-agent's shell has the thread of its own
# and the session its hooks carry, and the markers are keyed under the session.
_session_id="${COPILOT_AGENT_SESSION_ID:-${CODEX_SESSION_ID:-${CODEX_THREAD_ID:-${CLAUDE_CODE_SESSION_ID:-}}}}"
# The same variables name the harness (hooks/_workdir.py `harness`); flow-window.sh reads it, empty when no
# harness exports a session id.
if [ -n "${COPILOT_AGENT_SESSION_ID:-}" ]; then
  PLURAI_HARNESS=copilot
elif [ -n "${CODEX_SESSION_ID:-}${CODEX_THREAD_ID:-}" ]; then
  PLURAI_HARNESS=codex
elif [ -n "$_session_id" ]; then
  PLURAI_HARNESS=claude
else
  PLURAI_HARNESS=""
fi
if [ -n "$_session_id" ]; then
  # NOT under ~/.claude (keep in lockstep with hooks/_workdir.py FLOW_ROOT): the auto-mode
  # classifier reads any raw write into the agent's own config tree as self-modification and
  # denies it. Flow state is plugin runtime state, so it lives outside that boundary.
  # Native Windows too: Git Bash's HOME is the profile unless the user set one, and hooks/_workdir.py
  # `flow_root` reads that same HOME.
  _flow_candidate="${HOME}/.plurai/flow/${_session_id}"
  if mkdir -p "$_flow_candidate" 2>/dev/null; then
    FLOWDIR="$_flow_candidate"
  fi
  unset _flow_candidate
elif [ -n "${CLAUDECODE:-}" ]; then
  # Running under Claude Code but the id is not exported: the hooks WILL have it (their payload
  # carries it) while this side does not, so the two halves would key differently. The fallback
  # keeps enforcement working — the guards read both locations for exactly this case — but the
  # split is worth saying out loud. A silent degrade is the failure mode the session keying exists to prevent.
  echo "WARNING(resolve-workdir): CLAUDE_CODE_SESSION_ID is unset under Claude Code — flow markers fall back to \$WORKDIR. Enforcement still works (the hooks read both), but report this: it means the session id is no longer exported to tool shells." >&2
fi

# Persist the NAMED agent beside the flow markers, for the HOOKS.
#
# `PLURAI_AGENT_PATH` is exported inside a Bash tool call; the hooks run in Claude Code's own
# process and never see it, so `hooks/_workdir.py`'s scope test could only ever fire when the
# operator had set it in settings.json. Writing it to $FLOWDIR — session-keyed, observed
# identically by both halves — is the same fix the flow markers themselves use.
# Best-effort: an unwritable flow dir costs the hooks their scope, never the caller's resolve.
if [ -n "${PLURAI_AGENT_PATH:-}" ] && [ -n "${FLOWDIR:-}" ]; then
  printf '%s' "$PLURAI_AGENT_PATH" > "$FLOWDIR/.agent_path" 2>/dev/null || true
fi

unset _AMBIGUOUS_ROOT _session_id
export WORKDIR PLURAI_DIR ART FLOWDIR PLURAI_HARNESS
unset _PLURAI_ROOT _ENVS_DIR _ACTIVE_ENV _AGENT_SCOPE _VIA _rw_start
unset -f _plurai_canon _plurai_within _rw_dirname 2>/dev/null || true
