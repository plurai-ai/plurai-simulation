#!/usr/bin/env bash
# Canonical resolution of the backend REST server URL + the bearer requirement.
#
# Source this from any skill step that shells out to the backend REST server or the
# SDK CLIs — do NOT inline your own ${PLURAI_URL%/mcp} derivation:
#
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-plurai-urls.sh" || exit 1
#
# The backend is the plugin's one resolver (hooks/_login_store.py, asked through `url_guard.py --backend`): the
# PLURAI_URL / PLURAI_TOKEN pair of the environment, else the pair saved by scripts/plurai_login.py. The two sources are
# never combined, and there is no default address. When nothing is configured, or the configuration is partial,
# this file prints why (the shared sign-up and login message, or the configuration error) and returns 1.
#
# Sets (and exports):
#   SERVER_URL    the REST orchestration base: the API root PLURAI_URL names (the backend serves EVERY surface
#                 under one base URL, locally and deployed alike);
#   PLURAI_URL    the same root, so the SDK CLIs the caller starts resolve the very pair this file resolved;
#   PLURAI_TOKEN  the resolved bearer, when there is one (a loopback backend may have none).
#
# A NON-loopback backend with no bearer aborts with a remediation message — remote endpoints require the token;
# requests without it would only fail later and less clearly. Loopback stays tokenless (local dev).
#
# A copy of this file outside the plugin (the setup skill copies it into the customer's project, where it runs
# after the plugin is gone) has no resolver to ask: it takes PLURAI_URL from its environment and the token from
# PLURAI_TOKEN, and sends the bearer only to loopback or the origin recorded beside it.

_plurai_host_of() {
  # scheme://user@host:port/path → host (brackets stripped for IPv6 literals).
  local u="${1#*://}"
  u="${u%%/*}"
  u="${u##*@}"
  case "$u" in
    \[*\]*) u="${u#\[}"; u="${u%%\]*}" ;;
    *) u="${u%%:*}" ;;
  esac
  printf '%s' "$u"
}

# The bearer every script that sources this file sends: whitespace removed, and empty means no
# token, so a caller sends no Authorization header at all rather than a bare `Bearer `.
_plurai_bearer() {
  local t="${PLURAI_TOKEN:-}"
  printf '%s' "${t//[[:space:]]/}"
}

# The loopback test is `hooks/url_guard.py --is-loopback`, the one entry every caller in the
# plugin asks. This file is sourced from bash and from zsh, so it finds its own path by either shell's spelling. A
# guard it cannot find or run answers "not loopback": the check fails closed, into asking for a
# token. The refusal below asks only when there is no token, since only then does it decide.
_plurai_self=""
if [ -n "${BASH_VERSION:-}" ]; then _plurai_self="${BASH_SOURCE[0]}"; elif [ -n "${ZSH_VERSION:-}" ]; then eval '_plurai_self=${(%):-%x}'; fi
# Native Windows: sourced as `C:\...\scripts\resolve-plurai-urls.sh` (Copilot substitutes the plugin root with
# backslashes), the split below finds no `/`. Spelled with forward slashes there. The detector is trusted-python.sh's
# `_plurai_on_windows`, inlined because that file is found from this path; elsewhere nothing changes.
_plurai_self_on_windows() {
  [ -x /usr/bin/cygpath ]
}
case "$_plurai_self" in
  *\\*) if _plurai_self_on_windows; then _plurai_self="${_plurai_self//\\//}"; fi ;;
esac
unset -f _plurai_self_on_windows
_plurai_guard=""
_plurai_record=""
if [ -n "$_plurai_self" ]; then
  case "$_plurai_self" in */*) _plurai_guard="${_plurai_self%/*}/../hooks/url_guard.py"; _plurai_record="${_plurai_self%/*}/.plurai_backend_origin" ;; *) _plurai_guard="./../hooks/url_guard.py"; _plurai_record="./.plurai_backend_origin" ;; esac
fi

# Native Windows has no fixed system interpreter: there `trusted-python.sh` (beside this file, in the plugin
# and in a workdir copy) names the one the session pinned at SessionStart, never one from PATH. Its
# `_plurai_on_windows` is the detector; elsewhere this answers 1 and the fixed paths below decide.
_plurai_trusted=""
if [ -n "$_plurai_guard" ] && [ -f "${_plurai_guard%/../hooks/url_guard.py}/trusted-python.sh" ]; then
  # shellcheck source=trusted-python.sh
  . "${_plurai_guard%/../hooks/url_guard.py}/trusted-python.sh" && _plurai_trusted=1
fi
_plurai_windows_python() {
  if [ -z "$_plurai_trusted" ]; then
    # A copy without the helper beside it: refuse on Windows, defer to the fixed paths elsewhere.
    [ -x /usr/bin/cygpath ] && return 2
    return 1
  fi
  _plurai_on_windows || return 1
  # A workdir copy has no guard beside it and so no plugin root: its parent is not one, whatever it holds.
  if [ -f "$_plurai_guard" ]; then
    _plurai_trusted_python "${_plurai_guard%/hooks/url_guard.py}" || return 2
  else
    _plurai_trusted_python "" || return 2
  fi
}

# The guard runs where a command cannot reach it: under a fixed system interpreter (a `python3` found on
# PATH is whatever `PATH=/tmp/x:$PATH <script>` says it is, and would receive the bearer and answer
# "yes"), isolated (`-I`), in an environment that holds the session id and HOME (where the saved credential
# lives) and nothing else (no bearer, no PATH, no PYTHON*). None found, or the guard missing, answers "no".
_plurai_guard_python() {
  local candidate
  _plurai_windows_python
  case "$?" in 0) return 0 ;; 2) return 1 ;; esac
  for candidate in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$candidate" ]; then printf '%s' "$candidate"; return 0; fi
  done
  return 1
}
# Every stdlib-only helper this plugin runs from a script that holds the bearer goes through here, for
# the same reason: a `python3` found on PATH is whatever `PATH=/tmp/x:$PATH <script>` says it is, and
# would receive the bearer and the identity key, and answer (a resolved runner path, an identity
# verdict) for questions that decide where the bearer goes. So: the fixed interpreter above, isolated
# (`-I`: no PYTHON*, no user site, no script directory on sys.path), with PLURAI_TOKEN and
# PLURAI_IDENTITY_KEY removed from its environment. The rest of the environment is kept, because
# these helpers read the session's variables (WORKDIR, ART, CLAUDE_*, HOME, PATH for `git`) and hold
# nothing secret once those two are gone. A helper that needs the identity key is handed it by name;
# the one step that sends the bearer says `--bearer` and inherits it (never as an argument, which
# every process can read):
#
#   _plurai_stdlib [--bearer] [NAME=value ...] <python args...>
#
# None found answers 127: it never falls back to PATH.
_plurai_stdlib() {
  local py keep=""
  local -a pass
  pass=()
  if [ "${1:-}" = --bearer ]; then keep=1; shift; fi
  py="$(_plurai_guard_python)" || {
    if [ -x /usr/bin/cygpath ]; then
      echo "plurai: no trusted python on this Windows host: the session did not pin one at SessionStart, no py launcher is in the Windows directory or the user's own Python folder, and no Python 3.9 or newer is registered. The plugin's helpers do not take one from PATH, because that would hand the bearer to whatever PATH names." >&2
    else
      echo "plurai: no python3 at /usr/bin, /opt/homebrew/bin or /usr/local/bin. The plugin's helpers do not take one from PATH, because that would hand the bearer to whatever PATH names." >&2
    fi
    return 127
  }
  while [ "$#" -gt 0 ]; do
    case "$1" in
      [A-Za-z_]*=*) pass+=("$1"); shift ;;
      *) break ;;
    esac
  done
  if [ -n "$keep" ]; then
    /usr/bin/env -u PLURAI_IDENTITY_KEY ${pass[@]+"${pass[@]}"} "$py" -I "$@"
  else
    /usr/bin/env -u PLURAI_TOKEN -u PLURAI_IDENTITY_KEY ${pass[@]+"${pass[@]}"} "$py" -I "$@"
  fi
}

# One call into the guard: a fixed interpreter, isolated, in an environment of the session variables, HOME (where the
# saved login lives) and the configured url. The first argument says whether this shell holds a bearer: empty for none,
# `xxxxxxxx` for one of the minimum length, `x` for one that is too short (which the guard refuses by name). The guard
# learns that and never the secret, because a url with no token (or a token with no url) is the partial configuration
# it must name.
_plurai_guard_call() {  # <x|""> <guard args...>
  local present="$1"
  shift
  [ -n "$_plurai_guard" ] && [ -f "$_plurai_guard" ] || return 1
  _plurai_in_session "$present" "$_plurai_guard" "$@"
}
# The one place the fixed interpreter is started for a plugin file: isolated, in an environment of the session variables, HOME and the
# configured url, with the id of the command in progress when the caller inherited one. <present> is as above; <file> is
# a file beside the guard.
_plurai_in_session() {  # <x|""> <file> <args...>
  local py="" present="$1" file="$2"
  shift 2
  py="$(_plurai_guard_python)" || return 1
  /usr/bin/env -i "HOME=${HOME:-}" "PLURAI_URL=${PLURAI_URL:-}" "PLURAI_TOKEN=$present" "PLURAI_COMMAND_RUN_ID=${PLURAI_COMMAND_RUN_ID:-}" "COPILOT_AGENT_SESSION_ID=${COPILOT_AGENT_SESSION_ID:-}" "CODEX_SESSION_ID=${CODEX_SESSION_ID:-}" "CODEX_THREAD_ID=${CODEX_THREAD_ID:-}" "CLAUDE_CODE_SESSION_ID=${CLAUDE_CODE_SESSION_ID:-}" "$py" -I "$file" "$@" 2>/dev/null
}
# The id of the command in progress (`_command_run.py current`), printed, or nothing. No bearer reaches it.
_plurai_command_run_id() {
  local run="${_plurai_guard%url_guard.py}_command_run.py"
  [ -f "$run" ] || return 1
  _plurai_in_session "" "$run" current
}
_plurai_ask_guard() {  # --is-loopback|--is-backend|--token-for URL
  _plurai_guard_call "" "$1" "$2"
}
# The resolved REST base, printed (`url_guard.py --backend`), or why there is none: the shared sign-up and login
# message, or the configuration error, printed on stdout with status 1.
_plurai_present() {
  local present="" bearer
  bearer="$(_plurai_bearer)"
  if [ -n "$bearer" ]; then
    # The shortest token the guard accepts (`_login_store.MIN_TOKEN_LENGTH`); a test holds the two equal.
    if [ "${#bearer}" -ge 8 ]; then present="xxxxxxxx"; else present="x"; fi
  fi
  printf '%s' "$present"
}
_plurai_backend() {
  _plurai_guard_call "$(_plurai_present)" --backend
}
# The credential saved for the origin of $1, printed, when $1 is a backend this session is bound to and the
# file is private (`url_guard.py --token-for`). Nothing is printed, and nothing is an error, otherwise. The
# token travels on a pipe into a variable, never through an argument.
_plurai_saved_token() {
  _plurai_ask_guard --token-for "$1"
}
# The backend: the resolver's answer inside the plugin, PLURAI_URL alone in a copy that has no resolver.
if [ -n "$_plurai_guard" ] && [ -f "$_plurai_guard" ]; then
  if ! SERVER_URL="$(_plurai_backend)"; then
    echo "${SERVER_URL:-plurai: the Plurai backend could not be resolved (no python3 at a fixed path, or the guard cannot run).}" >&2
    unset SERVER_URL _plurai_self _plurai_record
    return 1 2>/dev/null || exit 1
  fi
else
  SERVER_URL="${PLURAI_URL:-}"
  SERVER_URL="${SERVER_URL%/}"
  SERVER_URL="${SERVER_URL%/mcp}"
  if [ -z "$SERVER_URL" ]; then
    echo "ERROR: PLURAI_URL is not set, and this copy of the resolver is outside the plugin, so it cannot read the saved login. Export PLURAI_URL and PLURAI_TOKEN, or run it from the plugin." >&2
    unset SERVER_URL _plurai_self _plurai_record
    return 1 2>/dev/null || exit 1
  fi
fi
export SERVER_URL
if [ -z "$(_plurai_bearer)" ]; then
  _plurai_saved="$(_plurai_saved_token "$SERVER_URL")" || _plurai_saved=""
  if [ -n "$_plurai_saved" ]; then
    PLURAI_TOKEN="$_plurai_saved"
    export PLURAI_TOKEN
  fi
  unset _plurai_saved
fi
if [ -z "$(_plurai_bearer)" ] && ! _plurai_ask_guard --is-loopback "$SERVER_URL"; then
  unset _plurai_self _plurai_record
  echo "ERROR: the Plurai backend ($SERVER_URL) is not on this machine but no token is available — refusing unauthenticated remote calls. Export PLURAI_TOKEN together with PLURAI_URL, save the login in a terminal of your own with scripts/plurai_login.py, or export a loopback PLURAI_URL for tokenless local dev." >&2
  return 1 2>/dev/null || exit 1
fi
# The bearer goes only where the SESSION was bound, not where this command's environment says. A
# command prefix (`PLURAI_URL=https://evil.example <script>`) sets the variables above for one
# run, so the URL they resolved to is checked against the session pin (hooks/session_pin.py) by the
# one entry every caller asks, `url_guard.py --is-backend`: loopback, or a pinned origin; with no pin
# only loopback. A guard it cannot find or run answers "no". Nothing to protect without a
# token, so a tokenless loopback run is never asked.
#
# A copy of this file outside the plugin has no guard and no pin to ask. It then answers from the
# one rule that needs neither: the one origin the plugin's guard accepted when the copy was made
# (`.plurai_backend_origin` beside it, written by scripts/record_backend_origin.py: `https://host[:port]`, or
# `http://127.0.0.1:port` / `http://localhost:port` for a local backend). Loopback as such is no answer, because
# a remote backend's token must never reach a local listener. Narrower than the guard, never wider.
_plurai_recorded_origin() {
  local rec="" host port
  [ -n "$_plurai_record" ] && [ -f "$_plurai_record" ] || return 1
  IFS= read -r rec < "$_plurai_record" || [ -n "$rec" ] || return 1
  local loopback_port=""
  case "$rec" in
    http://127.0.0.1:*) loopback_port="${rec#http://127.0.0.1:}" ;;
    http://localhost:*) loopback_port="${rec#http://localhost:}" ;;
  esac
  if [ -n "$loopback_port" ]; then
    # A local backend is recorded with its port: that port and no other.
    case "$loopback_port" in *[!0-9]*) return 1 ;; esac
    printf '%s' "$rec"
    return 0
  fi
  case "$rec" in https://*) ;; *) return 1 ;; esac
  host="${rec#https://}"
  case "$host" in *:*) port="${host#*:}"; host="${host%%:*}"; case "$port" in '' | *[!0-9]*) return 1 ;; esac ;; esac
  # A lowercase ASCII DNS name and nothing else: no userinfo, no path, no second colon, no empty label. The letters are
  # spelled out because a range such as a-z follows the locale's collation: bash 3.2 in a UTF-8 locale (macOS) puts
  # capitals inside it.
  case "$host" in '' | *[!abcdefghijklmnopqrstuvwxyz0123456789.-]* | .* | *. | *..*) return 1 ;; esac
  printf '%s' "$rec"
}
_plurai_is_backend() {
  if [ -n "$_plurai_guard" ] && [ -f "$_plurai_guard" ]; then
    # The guard is told that a bearer is held (never the bearer): with no pin, a bearer from the environment may go to a
    # Plurai-operated https host (`url_guard._plurai_operated_origin`), which it cannot judge from "no bearer".
    _plurai_guard_call "$(_plurai_present)" --is-backend "$1"
    return $?
  fi
  local url="${1%/}" recorded
  if recorded="$(_plurai_recorded_origin)"; then
    # The origin exactly, alone or followed by a path; nothing a client could read as another host.
    case "$url" in *[[:space:][:cntrl:]\\]*) return 1 ;; esac
    case "$url" in "$recorded" | "$recorded"/*) return 0 ;; esac
  fi
  return 1
}
if [ -n "$(_plurai_bearer)" ]; then
  if ! _plurai_is_backend "$SERVER_URL"; then
    echo "ERROR: $SERVER_URL is not the backend this session is bound to, so PLURAI_TOKEN would not be sent there. The backend is the one Claude Code was launched with (PLURAI_URL in its environment, recorded when the session started); setting it for one command does not change it. With no session only the origin recorded in .plurai_backend_origin beside this file qualifies; re-run the setup in a session bound to the backend to record it." >&2
    # An unusable pin (another account's, a symlink, writable by others) is refused as "no pin"; say which it is.
    _plurai_pin_reason="$(_plurai_guard_call "" --pin-problem)" || _plurai_pin_reason=""
    [ -z "$_plurai_pin_reason" ] || echo "ERROR: $_plurai_pin_reason." >&2
    unset _plurai_pin_reason
    unset _plurai_self _plurai_record
    return 1 2>/dev/null || exit 1
  fi
fi
# The SDK CLIs the caller starts resolve the very pair resolved above, from the environment.
PLURAI_URL="$SERVER_URL"
export PLURAI_URL
# …and the command run id of the command in progress, which they put on the jobs they start (hooks/_command_run.py).
# Read by the plugin's own code under the same fixed interpreter as the guard, in the environment of session variables
# and HOME it needs to find the flow directory. A copy outside the plugin has no flow directory to read; the variable
# it inherited stands.
if [ -n "$_plurai_guard" ] && [ -f "$_plurai_guard" ]; then
  _plurai_run_id="$(_plurai_command_run_id)" || _plurai_run_id=""
  if [ -n "$_plurai_run_id" ]; then
    PLURAI_COMMAND_RUN_ID="$_plurai_run_id"
    export PLURAI_COMMAND_RUN_ID
  fi
  unset _plurai_run_id
fi
unset _plurai_self _plurai_record
