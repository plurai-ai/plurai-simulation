#!/bin/sh
# The gate in front of every hook that only matters while a Plurai flow or command is open.
#
#   sh gate.sh [prompt|skill|token] -- COMMAND [ARG...]
#
# Reads the event payload from stdin. When the session has an open scope it runs COMMAND with the payload
# on its stdin, byte for byte, and exits with the command's status. Otherwise it exits 0 with no output,
# which every harness reads as "no opinion". It starts no Python and no process but `cat` (to read the
# payload); every other step is a shell builtin, so a session that runs no Plurai command pays for one
# `sh` per event and nothing else.
#
# The scope is open when any of these holds:
#   - a marker file below sits in the session's flow directory (~/.plurai/flow/<session id>/) or in the
#     agent's plurai_sim directory the same way hooks/_workdir.py resolves it (the nearest ancestor of
#     the working directory, its envs/<name>/ children, and, at a git root, plurai_sim directories up to
#     three levels down);
#   - `prompt`: the user's prompt starts a plugin command (`/name`, `/plurai-simulation:name`, `$name`,
#     or Copilot's expansion of a skill the user invoked), or is the dropped-turn heartbeat's prompt;
#   - `skill`: the tool call invokes a skill of this plugin;
#   - `token`: the tool call's input names CLAUDE_PLUGIN_ROOT, which only text from the plugin's skills
#     carries (the hook that fills the variable in must run for a skill that opened no scope).
# When the session id or HOME cannot be read, the gate cannot tell and runs COMMAND: a missed scope would
# switch the guards off, a needless start costs one process.
#
# Not for hooks on the plugin's own MCP server (their matcher is their gate) and not for SessionStart or
# SessionEnd (one Python start each keeps the session pin and the readiness marker). The marker names
# below are the registry's GATE_MARKERS; a test pins the two together.

entry=
case ${1-} in prompt | skill | token) entry=$1; shift ;; esac
[ "${1-}" = -- ] && shift
[ "$#" -gt 0 ] || exit 64

# `x` guards the payload's trailing newlines against command substitution.
payload=$(cat; printf x)
payload=${payload%x}

run() {
  printf '%s' "$payload" | "$@"
  exit $?
}

# json_string KEY: REPLY is the text after the opening quote of the first string value of "KEY": ... (to the
# end of the payload, still JSON-escaped); status 1 when there is none.
json_string() {
  _rest=$payload
  while :; do
    case $_rest in *\""$1"\"*) ;; *) REPLY=; return 1 ;; esac
    _rest=${_rest#*\""$1"\"}
    _after=${_rest#"${_rest%%[![:space:]]*}"}
    case $_after in
      :*)
        _after=${_after#:}
        _after=${_after#"${_after%%[![:space:]]*}"}
        case $_after in
          \"*) REPLY=${_after#\"}; return 0 ;;
        esac
        REPLY=
        return 1
        ;;
    esac
  done
}

# Up to the closing quote: the value of a short field such as an id or a skill name.
json_short() {
  json_string "$1" || return 1
  REPLY=${REPLY%%\"*}
}

open_scope() {
  for _marker in .plurai_flow_active .plurai_command_scope .plurai_simulate.json; do
    [ -f "$1/$_marker" ] && return 0
  done
  return 1
}

# `${1%/}` keeps the root's paths single-slashed: a path that starts with `//` is a UNC path to Git Bash on Windows,
# and each file test on it waits for a network name lookup.
open_workdir() {
  open_scope "${1%/}/plurai_sim" && return 0
  for _env in "${1%/}"/plurai_sim/envs/*; do
    open_scope "$_env" && return 0
  done
  return 1
}

# Whether a workdir with an open scope sits at or above $1, or below a git root among its ancestors.
open_workdir_near() {
  _dir=$1
  while [ -n "$_dir" ]; do
    open_workdir "$_dir" && return 0
    if [ -e "${_dir%/}/.git" ]; then
      for _found in "${_dir%/}"/*/plurai_sim "${_dir%/}"/*/*/plurai_sim "${_dir%/}"/*/*/*/plurai_sim; do
        [ -d "$_found" ] && open_workdir "${_found%/plurai_sim}" && return 0
      done
    fi
    case $_dir in
      / | "") break ;;
      */*) _dir=${_dir%/*} ;;
      *) break ;;
    esac
    [ -n "$_dir" ] || _dir=/
  done
  return 1
}

# The payload's cwd as a path this shell can walk, in REPLY: a POSIX absolute path as is, a Windows drive path
# (`C:\\Users\\x` as JSON text, or `C:/Users/x`) as the Git Bash form `/c/Users/x`. Anything else (a relative
# path, a UNC path, a name, one longer than 4096 characters) is no starting point, and the walk never sees it.
walkable_cwd() {
  [ "${#REPLY}" -le 4096 ] || return 1
  case $REPLY in
    [A-Za-z]:*)
      _drive=$(printf %s "${REPLY%"${REPLY#?}"}" | tr 'A-Z' 'a-z')
      REPLY=${REPLY#??}
      REPLY=$(printf %s "$REPLY" | sed 's/\\\\/\//g')
      REPLY=/$_drive$REPLY
      ;;
  esac
  case $REPLY in
    //*) return 1 ;;
    /*) return 0 ;;
  esac
  return 1
}

# The plugin's own skills are the directories under skills/ next to this file's directory.
here=${0%/*}
[ "$here" = "$0" ] && here=.
root=${here%/hooks}
[ "$root" = "$here" ] && root=$here/..

plugin_skill() {
  case $1 in
    "" | *[!A-Za-z0-9_-]*) return 1 ;;
  esac
  [ -f "$root/skills/$1/SKILL.md" ]
}

# A prompt that starts a plugin command: the text after the quote of "prompt":.
prompt_starts_command() {
  json_string prompt || return 1
  _text=$REPLY
  while :; do
    case $_text in
      " "*) _text=${_text# } ;;
      '\n'* | '\t'* | '\r'*) _text=${_text#??} ;;
      *) break ;;
    esac
  done
  # The dropped-turn heartbeat's prompt fires from a session cron that outlives its flow; the hook that
  # blocks it must run when no scope is open any more.
  case $_text in 'plurai-flow heartbeat'*) return 0 ;; esac
  case $_text in 'The user explicitly invoked the \"'*) _text=${_text#'The user explicitly invoked the \"'} ;; esac
  case $_text in
    /plurai-simulation:*) _text=${_text#/plurai-simulation:} ;;
    /* | \$*) _text=${_text#?} ;;
    *) return 1 ;;
  esac
  plugin_skill "${_text%%[!A-Za-z0-9_-]*}"
}

skill_call_names_ours() {
  json_short skill || return 1
  plugin_skill "${REPLY#plurai-simulation:}"
}

# --- the decision ----------------------------------------------------------------------------------------

session=
json_short session_id || json_short sessionId && session=$REPLY
[ -n "$session" ] || run "$@"
[ -n "${HOME-}" ] || run "$@"

case $session in
  */* | . | ..) ;;
  *) open_scope "$HOME/.plurai/flow/$session" && run "$@" ;;
esac

open_workdir_near "$PWD" && run "$@"
if json_short cwd && [ "$REPLY" != "$PWD" ] && walkable_cwd; then
  open_workdir_near "$REPLY" && run "$@"
fi

case $entry in
  prompt) prompt_starts_command && run "$@" ;;
  skill) skill_call_names_ours && run "$@" ;;
  token) case $payload in *CLAUDE_PLUGIN_ROOT*) run "$@" ;; esac ;;
esac
exit 0
