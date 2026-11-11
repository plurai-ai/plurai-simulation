# stash_scan_contract.sh — export $CONTRACT_ID and $CONTRACT_PROCEED for a generation call.
#
# Source it (never execute), after `resolve-workdir.sh` has bound $ART and $PLURAI_DIR, the
# caller has resolved $PLURAI_BIN, and the wrong-backend guard has run in the same block:
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"
#   PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask --require stash-doc)" || { [ $? -eq 3 ] && exit 1; }
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/assert-backend-identity.sh" "" || exit 1
#   source "${CLAUDE_PLUGIN_ROOT}/scripts/stash_scan_contract.sh"
#
# $PLURAI_BIN comes from the caller because the resolver prints its own diagnostics and probes the
# binary three times: resolving once per block keeps one account of which runner stashed. An
# empty $PLURAI_BIN is a project with no usable Python runner (SDK not installed, or too old for
# `stash-doc`): the stash cannot start, so it reports STASH_FAILED, never ABSENT.
#
# ONE definition for THREE consumers. `generate_scenarios_start`, `generate_evals_start` and
# `generate_env_start` all build on the scan's conclusions and all need the same two values, and
# two of them computing it differently is two premises for one contract.
#
#   CONTRACT_ID           the stash id; ABSENT (no contract on disk) or STASH_FAILED
#   CONTRACT_PROCEED      the contract's OWN verdict — true | false | unreadable | none
#   CONTRACT_PROVISIONAL  how many typed facts it carried ASSERTED and UNVERIFIED — unknown | none
#   CONTRACT_BLOCKED      how many of its checks could NOT run at all — unknown | none
#
# The verdict travels WITH the id. `false` means the checker found one of the scan's conclusions
# FALSE against the corpus and the operator chose to build anyway (`/simulation-generate-suite`
# Step 0's override) — the premises are acknowledged-bad, not verified, and generating from them
# as if verified is worse than generating without them. Each skill's own text says what its
# consumer does with each state.
#
# THE TWO COUNTS TRAVEL WITH IT for the same reason, and they are not derivable from the boolean.
# A report where every fact is asserted-unverified and six of ten checks are BLOCKED still has
# `proceed: true` — nothing was REFUTED — so without the counts the three generators would receive a signal
# byte-identical to a fully-verified scan. That is the whole of "make PROCEED provisional so the
# loop knows which facts are soft": a `true` over N untested premises is a different premise-set
# from a `true` over none, and the consumer has to be able to say so out loud.
#
# The id is content-addressed, so the three calls re-stashing the unchanged file get the same id
# back for free — which is what makes "the SAME id reaches all three" hold by construction.

# The plugin root is this file's ONE prerequisite: $CLAUDE_PLUGIN_ROOT when it names a checkout,
# else the directory this file was sourced from (`BASH_SOURCE` in bash, `%x` in
# zsh) — the Bash tool does not export the variable; the CLI substitutes it only in loaded
# SKILL.md text. It is checked rather than assumed because an unresolved root degrades into a
# WRONG ANSWER instead of an error. Every path below joins
# onto it, so unset they all become absolute `/scripts/...`: the interpreter resolver is never
# sourced ($PLURAI_PY stays empty, so the parse below reports `unreadable`), so the stash has no
# verdict to travel with. That is precisely the failure the paragraph
# below forbids — a resolution failure wearing the contract's clothes — and it is reachable from
# an ordinary caller: a skill snippet that keeps the plugin root in its own variable and sources
# this file without exporting it.
#
# A sourced file cannot `exit` without killing the caller's shell, so the sentinels are set here
# and the rest is skipped. STASH_FAILED/unreadable is the honest pair: nothing was stashed and
# nothing was read.
_ssc_here="${CLAUDE_PLUGIN_ROOT:+$CLAUDE_PLUGIN_ROOT/scripts}"
if [ -z "$_ssc_here" ] || [ ! -r "$_ssc_here/resolve-python.sh" ]; then
  _ssc_self=""
  if [ -n "${BASH_VERSION:-}" ]; then _ssc_self="${BASH_SOURCE[0]}"; elif [ -n "${ZSH_VERSION:-}" ]; then eval '_ssc_self=${(%):-%x}'; fi
  _ssc_here="$(CDPATH= cd -- "$(dirname -- "${_ssc_self:-.}")" 2>/dev/null && pwd)"
  [ -n "$_ssc_self" ] || _ssc_here=""
  unset _ssc_self
fi
if [ -z "$_ssc_here" ] || [ ! -r "$_ssc_here/resolve-python.sh" ]; then
  unset _ssc_here
  echo "WARNING(stash_scan_contract): \$CLAUDE_PLUGIN_ROOT is unset and this file could not locate its own plugin checkout — the scan contract was NOT stashed and its verdict was NOT read. Export CLAUDE_PLUGIN_ROOT before sourcing this file." >&2
  CONTRACT_ID=STASH_FAILED
  CONTRACT_PROCEED=unreadable
  CONTRACT_PROVISIONAL=unknown
  CONTRACT_BLOCKED=unknown
  export CONTRACT_ID CONTRACT_PROCEED CONTRACT_PROVISIONAL CONTRACT_BLOCKED
  return 0 2>/dev/null || exit 0
fi

# The verdict is parsed with a python3 that can import PyYAML's siblings from the same install —
# a bare `python3` that fails to start would report `unreadable`, which the callers treat as
# ABSENT, turning a resolution failure into a statement about the contract.
. "$_ssc_here/resolve-python.sh"

# The plugin's refusal code and the SDK's upgrade-required code, read once from the exit table in a subshell: this file is
# sourced, and the table's names do not belong in the caller's shell.
_ssc_codes="$(. "$_ssc_here/sdk-exit-codes.sh"; printf '%s %s' "$PLURAI_RC_VERSION_REFUSED" "$PLURAI_RC_UPGRADE_REQUIRED")"
_ssc_refused="${_ssc_codes% *}"
_ssc_upgrade="${_ssc_codes#* }"
unset _ssc_codes

if [ -s "$ART/scan_contract.json" ]; then
  # The stash's EXIT STATUS decides, not whether the marker file happens to exist. A failed stash
  # leaves the id a PREVIOUS run wrote, and because the id is content-addressed, a stale one names
  # DIFFERENT content: the consumer would generate from some earlier scan's contract while
  # believing it holds this one's. Same doctrine as the `else` branch below, which removes the
  # marker rather than leave one pointing at a contract that is no longer on disk: a run whose
  # stash could not start would otherwise report a healthy-looking id from an earlier session.
  if [ -z "${PLURAI_BIN:-}" ]; then
    echo "WARNING(stash_scan_contract): no runner (\$PLURAI_BIN is empty), so the scan contract was NOT stashed." >&2
    rm -f "$PLURAI_DIR/.scan_contract_upload_id"
    CONTRACT_ID=STASH_FAILED
  else
    # `sdk_call.py` holds the stash's output until its exit is known: an upgrade-required exit carries the SDK's own text
    # and a `pip` line, which the customer never reads. That exit prints the plugin's text instead, and this file's return
    # status is the plugin's refusal code.
    if command -v "$PLURAI_PY" >/dev/null 2>&1; then
      "$PLURAI_PY" -I "$_ssc_here/sdk_call.py" "$PLURAI_BIN" stash-doc "$ART/scan_contract.json" scan_contract \
        --out "$PLURAI_DIR/.scan_contract_upload_id"
      _ssc_stash=$?
    else
      # The wrapper is python and this host has none: the runner is its own interpreter, so the stash runs through it with its
      # output held, and an upgrade-required exit prints the fixed update text instead of the SDK's own (SDK_REFUSAL_FALLBACK).
      echo "WARNING(stash_scan_contract): no python3 can run sdk_call.py, so the scan contract is stashed through the runner directly." >&2
      _ssc_fallback="$(. "$_ssc_here/sdk-exit-codes.sh"; printf '%s' "$PLURAI_SDK_REFUSAL_FALLBACK")"
      _ssc_out="$("$PLURAI_BIN" stash-doc "$ART/scan_contract.json" scan_contract --out "$PLURAI_DIR/.scan_contract_upload_id" 2>&1)"
      _ssc_stash=$?
      if [ "$_ssc_stash" = "$_ssc_upgrade" ]; then
        printf '%s\n' "$_ssc_fallback"
        _ssc_stash="$_ssc_refused"
      elif [ -n "$_ssc_out" ]; then
        printf '%s\n' "$_ssc_out" >&2
      fi
      unset _ssc_out
    fi
    if [ "$_ssc_stash" = 0 ]; then
      CONTRACT_ID="$(cat "$PLURAI_DIR/.scan_contract_upload_id" 2>/dev/null || echo STASH_FAILED)"
    else
      rm -f "$PLURAI_DIR/.scan_contract_upload_id"
      CONTRACT_ID=STASH_FAILED
    fi
  fi
  # ONE parse for all three. Three invocations would be three chances for the interpreter to fail
  # between them, and a run reporting `true` beside `unknown` counts would be describing two
  # different reads of the same file.
  _CONTRACT_SIGNALS="$("$PLURAI_PY" -c 'import json,sys; r=json.load(open(sys.argv[1])).get("report",{}); print(str(r.get("proceed")).lower(), len(r.get("provisional_facts") or []), len(r.get("unexecuted_checks") or []))' "$ART/scan_contract.json" 2>/dev/null || echo "unreadable unknown unknown")"
  # Split by parameter expansion, never `set --`: this file is SOURCED, and `set --` would
  # overwrite the caller's own positional parameters on the way past.
  CONTRACT_PROCEED="${_CONTRACT_SIGNALS%% *}"
  _CONTRACT_REST="${_CONTRACT_SIGNALS#* }"
  CONTRACT_PROVISIONAL="${_CONTRACT_REST%% *}"
  CONTRACT_BLOCKED="${_CONTRACT_REST##* }"
  unset _CONTRACT_SIGNALS _CONTRACT_REST
else
  # The marker is removed, not left: one from a run when the file existed would resolve flawlessly
  # to a contract that is no longer on disk.
  rm -f "$PLURAI_DIR/.scan_contract_upload_id"; CONTRACT_ID=ABSENT; CONTRACT_PROCEED=none
  # `none`, not `0`. There is no contract, so "how many premises went untested" has no answer —
  # and `0` there reads as a scan that tested everything.
  CONTRACT_PROVISIONAL=none; CONTRACT_BLOCKED=none
fi

unset _ssc_here
export CONTRACT_ID CONTRACT_PROCEED CONTRACT_PROVISIONAL CONTRACT_BLOCKED
# A refused stash leaves the plugin's refusal code as the return status; the caller stops on it.
if [ "${_ssc_stash:-0}" = "$_ssc_refused" ]; then
  # The code is interpolated before the names are unset, so the caller's shell keeps neither.
  eval "unset _ssc_stash _ssc_refused _ssc_upgrade _ssc_fallback; return $_ssc_refused" 2>/dev/null || exit
fi
unset _ssc_stash _ssc_refused _ssc_upgrade _ssc_fallback
