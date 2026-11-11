# resolve-python.sh — export $PLURAI_PY: a python3 that can import PyYAML.
#
# Source it (never execute):  source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
#                             "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_egress.py" ...
#
# The plugin's runtime scripts are stdlib + PyYAML, and PyYAML is the one thing a system
# interpreter routinely lacks. A bare `python3` in a skill snippet is whatever the Bash tool's
# PATH names — so on a machine whose project venv would run the script fine, the script instead
# exits non-zero and its caller reports NOT VERIFIED / NOTHING declared. That is a resolution
# failure wearing a verdict's clothes, which is the one outcome these callers must never emit.
#
# TIERS (a preference order over CANDIDATES; the first one that actually imports yaml wins, and
# it is printed):
#   1. $PLURAI_PYTHON_BIN         — an explicit override, for a checkout-local install
#   2. $PLURAI_PY_HINT/python3    — a bin dir the CALLER already resolved (scan_contract.sh
#                                   passes the directory of the CLI it picked, so the
#                                   projector runs under the same install as the contract)
#   3. $VIRTUAL_ENV/bin           — the venv the operator activated
#   4. ./.venv/bin, then other ./.venv*|./venv* — repo-root venvs, sorted
#   5. PATH
#
# Native Windows (Git Bash / MSYS / Cygwin, by _plurai_py_on_windows): a venv keeps its interpreter in
# `Scripts/python.exe`, so each `*/bin` tier is followed by its `*/Scripts` twin, `python.exe` is
# tried first in every tier, and a PE executable (`MZ`) counts as compiled. Elsewhere none of this applies.
#
# TRUSTED BY CONTENT, NOT BY NAME: a candidate under the working directory, whichever tier named it,
# must be a compiled executable beside a venv's `pyvenv.cfg`, and one an environment variable names
# must be compiled (see _plurai_py_is_interpreter), because the script that runs under it inherits
# the session's environment, bearer included.
#
# PROBED, NOT ASSUMED: `import yaml` is the capability the callers need, so a tier that exists
# but cannot import it loses to a later one that can. When NO candidate carries PyYAML,
# $PLURAI_PY is the first interpreter that merely EXISTS and $PLURAI_PY_HAS_YAML is 0 — the
# caller then fails on the script's own ImportError, which names the missing dependency, instead
# of on an empty command. $PLURAI_PY is never empty: `"$PLURAI_PY" script.py` with an empty
# value executes `script.py` itself, which is a different program.
#
# POSIX sh, and `set -u`-safe: scan_contract.sh sources it under `set -u`.

PLURAI_PY=""
PLURAI_PY_HAS_YAML=0
_plurai_py_first=""

# The Windows detector, identical to `_plurai_on_windows` in trusted-python.sh (inlined: this file is sourced
# from anywhere). By absolute path, so a `cygpath` on PATH cannot switch a Mac or Linux host.
_plurai_py_on_windows() {
  [ -x /usr/bin/cygpath ]
}
if _plurai_py_on_windows; then _plurai_py_win=1; else _plurai_py_win=0; fi
_plurai_py_dirname() {  # dirname(1) by parameter expansion: no PATH lookup decides where this script's files are
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
# On Windows, a path a caller or the environment spelled `C:\...` (an activated venv's VIRTUAL_ENV, a hint) as bash
# spells it, so every tier and $PLURAI_PY have one form. Elsewhere, and for a bare name, the value as given.
_plurai_py_posix() {
  case "$_plurai_py_win:$1" in
    1:*\\* | 1:?:*) /usr/bin/cygpath -u -- "$1" 2>/dev/null || printf '%s' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

# Records the first EXISTING interpreter (the fallback), and adopts a candidate as $PLURAI_PY
# only when it can import yaml.
_plurai_py_probe() {
  [ -n "$1" ] && [ -f "$1" ] && [ -x "$1" ] || return 1
  # A candidate the working tree holds, or an environment variable names, is vetted before anything
  # runs it: see _plurai_py_is_interpreter ($2 = named).
  _plurai_py_is_interpreter "$1" "${2:-}" || return 1
  [ -n "$_plurai_py_first" ] || _plurai_py_first="$1"
  # The probe runs a candidate that is not yet chosen, so it gets no bearer to send anywhere.
  /usr/bin/env -u PLURAI_TOKEN "$1" -c 'import yaml' >/dev/null 2>&1 || return 1
  PLURAI_PY="$1"
  PLURAI_PY_HAS_YAML=1
  return 0
}

# Whether the file at $1 sits under the working directory, by the path it will be executed through.
# Both sides are canonicalised with `pwd -P`, which reports the on-disk spelling, so a symlinked
# directory or a different letter case on a case-insensitive volume does not hide a file in the tree.
_plurai_py_in_tree() {
  _plurai_dir="$(CDPATH='' cd -- "$(_plurai_py_dirname "$1")" 2>/dev/null && pwd -P)" || return 1
  _plurai_here="$(pwd -P)"
  case "$_plurai_dir/" in "$_plurai_here"/*) unset _plurai_dir _plurai_here; return 0 ;; esac
  unset _plurai_dir _plurai_here
  return 1
}

# Whether the file at $1 starts with the magic number of a compiled executable (ELF, Mach-O, fat
# Mach-O). Content decides, not a `#!` line: a file with no `#!` is run as a shell script by the
# shell that executes it when the kernel refuses it.
_plurai_py_is_compiled() {
  case "$(LC_ALL=C /usr/bin/head -c 4 "$1" 2>/dev/null | /usr/bin/od -An -tx1 | /usr/bin/tr -d ' \n')" in
    7f454c46 | cffaedfe | cefaedfe | feedfacf | feedface | cafebabe | bebafeca) return 0 ;;
    4d5a*) [ "$_plurai_py_win" = 1 ] && return 0 ;;
  esac
  return 1
}

# Whether the interpreter at $1 may be run. One inside the working tree runs with the session's
# environment, bearer included, so it has to be what a venv holds: a compiled executable (or a
# symlink to one) beside the venv's `pyvenv.cfg`. A text script at that path is a program someone
# placed there. The check is on what the file is, not a boundary against someone who can build a
# convincing venv in the workspace: the workspace is trusted (see the trust model in
# resolve_plurai.py). A file an environment variable names (`named`: $PLURAI_PYTHON_BIN, the hint,
# $VIRTUAL_ENV) has to be compiled wherever it is. Nothing relaxes either check. A candidate found on
# PATH outside the tree is the operator's own lookup and is used as found, pyenv and asdf shell
# shims included.
_plurai_py_is_interpreter() {
  if _plurai_py_in_tree "$1"; then
    [ -f "$(_plurai_py_dirname "$(_plurai_py_dirname "$1")")/pyvenv.cfg" ] || return 1
    _plurai_py_is_compiled "$1"
  elif [ -n "${2:-}" ]; then
    _plurai_py_is_compiled "$1"
  fi
}

_plurai_py_tier() {  # bin dir [named] -> probe python3 then python inside it (python.exe first on Windows)
  [ -n "$1" ] || return 1
  if [ "$_plurai_py_win" = 1 ]; then
    _plurai_py_probe "$1/python.exe" "${2:-}" && return 0
  fi
  _plurai_py_probe "$1/python3" "${2:-}" || _plurai_py_probe "$1/python" "${2:-}"
}

# The OVERRIDE reports when it loses, unlike the tiers below it. A tier that does not resolve is
# the normal case and says nothing; an operator who NAMED an interpreter and silently got a
# different one has no way to notice, and the sibling CLI resolver refuses that outright.
if [ -n "${PLURAI_PYTHON_BIN:-}" ] && ! _plurai_py_probe "$PLURAI_PYTHON_BIN" named; then
  echo "WARNING(resolve-python): PLURAI_PYTHON_BIN=$PLURAI_PYTHON_BIN is not an executable file, or cannot import yaml — IGNORING it and resolving from the tiers below." >&2
fi
[ -n "$PLURAI_PY" ] || [ -z "${PLURAI_PY_HINT:-}" ] || _plurai_py_tier "$PLURAI_PY_HINT" named
if [ -z "$PLURAI_PY" ] && [ "$_plurai_py_win" = 1 ]; then
  _plurai_py_hint="$(_plurai_py_posix "${PLURAI_PY_HINT:-}")"
  case "$_plurai_py_hint" in */bin) _plurai_py_tier "${_plurai_py_hint%/bin}/Scripts" named ;; esac
fi
# Guarded on VIRTUAL_ENV being SET, not on the joined path existing: unset, the join is the
# absolute `/bin/python3`, a real system path this would adopt as "the venv the operator
# activated".
[ -n "$PLURAI_PY" ] || [ -z "${VIRTUAL_ENV:-}" ] || _plurai_py_tier "$VIRTUAL_ENV/bin" named
[ -n "$PLURAI_PY" ] || [ "$_plurai_py_win" = 0 ] || [ -z "${VIRTUAL_ENV:-}" ] || _plurai_py_tier "$(_plurai_py_posix "$VIRTUAL_ENV")/Scripts" named
[ -n "$PLURAI_PY" ] || _plurai_py_tier "$PWD/.venv/bin"
[ -n "$PLURAI_PY" ] || [ "$_plurai_py_win" = 0 ] || _plurai_py_tier "$PWD/.venv/Scripts"
# The repo-root venv scan, in a FUNCTION so zsh's `no_nomatch` can be scoped to it.
#
# This file is SOURCED, so it runs in the caller's shell — zsh on macOS, where an unmatched glob is
# a fatal `no matches found` under the default `nomatch`. An agent module with no venv of its own
# therefore ABORTED this script here: $PLURAI_PY stayed unset, and the caller's
# `"$PLURAI_PY" verify_config.py` ran with an empty command name — a resolution failure wearing a
# verifier's clothes, which the header above forbids.
#
# `local_options` makes the relaxation function-scoped, so the operator's own shell keeps its
# settings; the guard means bash and dash never reach the zsh builtin. The glob itself is KEPT and
# quoted — `find` was tried and silently regressed two cases the glob has always handled: a path
# containing a space (its output is field-split on IFS) and a venv that is a symlink to a directory
# (`-type d` does not match one). `[ -d ]` follows symlinks, and glob order is already sorted.
_plurai_py_scan_venvs() {
  if [ -n "${ZSH_VERSION:-}" ]; then
    setopt local_options no_nomatch 2>/dev/null || true
  fi
  for _plurai_venv in "$PWD"/.venv* "$PWD"/venv*; do
    [ -d "$_plurai_venv" ] || continue
    if _plurai_py_tier "$_plurai_venv/bin" || { [ "$_plurai_py_win" = 1 ] && _plurai_py_tier "$_plurai_venv/Scripts"; }; then
      unset _plurai_venv
      return 0
    fi
  done
  unset _plurai_venv
  return 1
}
[ -n "$PLURAI_PY" ] || _plurai_py_scan_venvs || true
[ -n "$PLURAI_PY" ] || _plurai_py_probe "$(command -v python3 2>/dev/null || true)"
[ -n "$PLURAI_PY" ] || _plurai_py_probe "$(command -v python 2>/dev/null || true)"

PLURAI_PY="$(_plurai_py_posix "$PLURAI_PY")"
_plurai_py_first="$(_plurai_py_posix "$_plurai_py_first")"
if [ -n "$PLURAI_PY" ]; then
  echo "resolve-python: PLURAI_PY=$PLURAI_PY (imports yaml)" >&2
elif [ -n "$_plurai_py_first" ]; then
  PLURAI_PY="$_plurai_py_first"
  echo "WARNING(resolve-python): no interpreter found that can import PyYAML — falling back to $PLURAI_PY, which will fail on the import. The Plurai SDK installs PyYAML with it: when this project has no SDK either, run python3 \"\${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py\" --ask and follow what it prints, and do not install pyyaml on its own. With the SDK in place, install pyyaml in this repo's venv (or point \$PLURAI_PYTHON_BIN at an interpreter that has it)." >&2
else
  # Nothing named python3/python anywhere. Keep a non-empty value so the caller's command is a
  # failed exec of a named interpreter, not an accidental exec of the script argument.
  PLURAI_PY="python3"
  echo "WARNING(resolve-python): no python3 on this machine — \$PLURAI_PY is the bare name and every plugin script invocation will fail to exec." >&2
fi

export PLURAI_PY PLURAI_PY_HAS_YAML
unset _plurai_py_first _plurai_py_win _plurai_py_hint
unset -f _plurai_py_on_windows _plurai_py_dirname _plurai_py_posix _plurai_py_probe _plurai_py_in_tree _plurai_py_is_compiled _plurai_py_is_interpreter _plurai_py_tier _plurai_py_scan_venvs 2>/dev/null || true
