# runner-gate-flags.sh — set $OVERRULE_FLAGS for one plurai-run-simulation call.
#
# Source it (never execute), after $RUNNER and $ART are set:
#
#     source "${CLAUDE_PLUGIN_ROOT}/scripts/runner-gate-flags.sh"
#     "$RUNNER" ... "${OVERRULE_FLAGS[@]}"
#
# An array, one argv item per word in bash and zsh alike.
#
# --overrule: the plugin's gate ledger, only when it exists — a missing --overrule file refuses the run.

OVERRULE_FLAGS=()
if [ -f "$ART/.gate_overrules.jsonl" ]; then
  OVERRULE_FLAGS=(--overrule "$ART/.gate_overrules.jsonl")
fi
