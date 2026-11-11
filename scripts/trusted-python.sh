# shellcheck shell=bash
# The interpreter a native-Windows shell (Git Bash, MSYS, Cygwin) runs the plugin's stdlib helpers with.
#
# Sourced, from bash and zsh; it defines functions and runs nothing. On macOS and Linux the callers probe
# their fixed, root-owned interpreter paths (/usr/bin/python3 and siblings) and never call this. Windows has
# no such path, and a `python3` a command finds on PATH is whatever that command's PATH says, so it is never
# looked up there. In order:
#
#   1. the interpreter the SessionStart hook ran under, which `hooks/session_pin.py` records in
#      `<profile folder>/.plurai/pins/<session id>.python`: the harness chose it, not a command. The profile folder is
#      what `/usr/bin/cygpath -F 40` (the system's profile folder) names, never `HOME`, which a command sets. This shell
#      reads the file without checking its access list: sh has no way to read a Windows ACL that is not a parse of a tool's
#      output. The hook that wrote the folder cut it to the user (`_private_files.restrict_directory`), and the Python
#      reader of the same pin checks owner and list on every read, so only an account the user's list does not name,
#      changed by an administrator, could plant a file here;
#   2. the py launcher in the Windows directory (`/usr/bin/cygpath -W` asks the system, not the environment), with
#      `-3` so it ignores the script's shebang and without the variables that steer it, asked once for the
#      interpreter it selects. This is how a copy in the workdir, which has no plugin root, finds one;
#   3. the py launcher a per-user install puts in the user's local application data folder
#      (`<local app data>/Programs/Python/Launcher/py.exe`; `/usr/bin/cygpath -F 28` asks the system for the folder,
#      never `LOCALAPPDATA`), asked the same way. A session whose SessionStart hook did not run has no pin (step 1),
#      and a per-user, Store or install-manager Python leaves nothing in the Windows directory (step 2);
#   4. the interpreter a Python 3.9 or newer registered under the user's, then the machine's, PythonCore key (PEP 514
#      `ExecutablePath`), read with the system directory's `reg.exe` (`/usr/bin/cygpath -S`).
#
# Steps 3 and 4 name folders and keys the user's account can write, as the pin of step 1 is: what they rule out is a
# command's environment choosing the interpreter. None found answers 127.

# The one Windows detector: Git Bash, MSYS2 and Cygwin all ship /usr/bin/cygpath, which no non-admin user can create on
# macOS or Linux. Named by absolute path: a `cygpath` found on PATH is whatever the command's PATH says.
_plurai_on_windows() {
  [ -x /usr/bin/cygpath ]
}

_plurai_cygpath() { /usr/bin/cygpath "$@"; }

# _plurai_trusted_python [PLUGIN_ROOT]: print the POSIX path of the trusted interpreter. PLUGIN_ROOT is the
# directory holding `hooks/session_pin.py`; empty or anything else skips the session pin.
_plurai_trusted_python() {
  local root="${1:-}" sid="" raw="" found="" windir="" local_data="" profile="" pin=""
  if [ -n "$root" ] && [ -f "$root/hooks/session_pin.py" ]; then
    sid="${COPILOT_AGENT_SESSION_ID:-}"
    [ -n "$sid" ] || sid="${CODEX_SESSION_ID:-}"
    [ -n "$sid" ] || sid="${CODEX_THREAD_ID:-}"
    [ -n "$sid" ] || sid="${CLAUDE_CODE_SESSION_ID:-}"
    case "$sid" in
      "" | . | .. | *[!A-Za-z0-9._-]*) sid="" ;;
    esac
    profile="$(_plurai_cygpath -F 40 -u 2>/dev/null)" || profile=""
    pin="$profile/.plurai/pins/$sid.python"
    if [ -n "$sid" ] && [ -n "$profile" ] && [ -f "$pin" ]; then
      IFS= read -r raw <"$pin" || [ -n "$raw" ]
      raw="${raw%$'\r'}"
      [ -n "$raw" ] && found="$(_plurai_cygpath -u -- "$raw" 2>/dev/null)"
      if [ -n "$found" ] && [ -f "$found" ]; then
        printf '%s' "$found"
        return 0
      fi
    fi
  fi
  windir="$(_plurai_cygpath -W 2>/dev/null)" || windir=""
  [ -n "$windir" ] && _plurai_launcher_python "$windir/py.exe" && return 0
  local_data="$(_plurai_cygpath -F 28 -u 2>/dev/null)" || local_data=""
  [ -n "$local_data" ] && _plurai_launcher_python "$local_data/Programs/Python/Launcher/py.exe" && return 0
  _plurai_registered_python && return 0
  return 127
}

# _plurai_launcher_python LAUNCHER: print the POSIX path of the interpreter the py launcher at LAUNCHER selects for `-3`,
# asked without the variables that steer it; 1 when there is no launcher there or it names none.
_plurai_launcher_python() {
  local raw="" found=""
  [ -f "$1" ] || return 1
  raw="$(/usr/bin/env -u PY_PYTHON -u PY_PYTHON3 -u VIRTUAL_ENV -u PLURAI_TOKEN -u PLURAI_IDENTITY_KEY \
    "$1" -3 -I -c 'import sys; print(sys.executable)' 2>/dev/null)" || raw=""
  raw="${raw%$'\r'}"
  [ -n "$raw" ] && found="$(_plurai_cygpath -u -- "$raw" 2>/dev/null)"
  if [ -n "$found" ] && [ -f "$found" ]; then
    printf '%s' "$found"
    return 0
  fi
  return 1
}

# _plurai_registered_python: print the POSIX path of the first Python 3.9 or newer whose PEP 514 `ExecutablePath` exists,
# under HKCU then HKLM; 1 when none does. MSYS2_ARG_CONV_EXCL keeps Git Bash from reading `/s` as a path.
_plurai_registered_python() {
  local sysdir="" hive line version="" major minor raw found=""
  sysdir="$(_plurai_cygpath -S -u 2>/dev/null)" || return 1
  [ -n "$sysdir" ] && [ -f "$sysdir/reg.exe" ] || return 1
  for hive in HKCU HKLM; do
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      while :; do case "$line" in [[:space:]]*) line="${line#?}" ;; *) break ;; esac; done
      case "$line" in
        HKEY_*\\PythonCore\\*\\InstallPath)
          version="${line%\\InstallPath}"
          version="${version##*\\}"
          ;;
        HKEY_*) version="" ;;
        ExecutablePath[[:space:]]*REG_SZ*)  # not WindowedExecutablePath: pythonw prints nothing
          major="${version%%.*}"
          minor="${version#*.}"
          minor="${minor%%[!0-9]*}"
          [ "$major" = 3 ] && [ -n "$minor" ] && [ "$minor" -ge 9 ] 2>/dev/null || continue
          raw="${line#*REG_SZ}"
          while :; do case "$raw" in [[:space:]]*) raw="${raw#?}" ;; *) break ;; esac; done
          [ -n "$raw" ] && found="$(_plurai_cygpath -u -- "$raw" 2>/dev/null)" || found=""
          if [ -n "$found" ] && [ -f "$found" ]; then
            printf '%s' "$found"
            return 0
          fi
          ;;
      esac
    done <<EOF_REG
$(/usr/bin/env MSYS2_ARG_CONV_EXCL='*' "$sysdir/reg.exe" query "$hive\\Software\\Python\\PythonCore" /s 2>/dev/null)
EOF_REG
  done
  return 1
}
