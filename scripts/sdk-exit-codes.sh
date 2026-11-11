# sdk-exit-codes.sh — the SDK's exit codes, by name, for the shell scripts that dispatch on them.
#
# Source it (never execute). The table is the one in the SDK's `plurai.exit_codes`; every plurai
# command line returns these and only these, and 2 is never one of them. `sdk_exit_codes.py` holds
# the same table for Python callers, and `scripts/tests/test_sdk_exit_codes.py` keeps the two equal.
#
# A script that branches on a status compares against these names, so a renumbering is one edit here
# and one in the Python table, not a search through case arms.

PLURAI_RC_OK=0
PLURAI_RC_POLICIES_FAILED=1
PLURAI_RC_UPGRADE_REQUIRED=10
PLURAI_RC_MOCK_UNANSWERED=11
PLURAI_RC_ROUTE_MISSING=12
PLURAI_RC_JOB_FAILED=13
PLURAI_RC_STILL_RUNNING=14
PLURAI_RC_NOT_FOUND=15
PLURAI_RC_NOT_READY=16
PLURAI_RC_BACKEND_REFUSED=17
PLURAI_RC_SCAN_HELD=18
PLURAI_RC_REQUIREMENT_GAP=19
PLURAI_RC_NOT_MEASURED=20
PLURAI_RC_USAGE=64
PLURAI_RC_REFUSED=65
PLURAI_RC_INPUT_UNREADABLE=66
PLURAI_RC_TRANSPORT=69
PLURAI_RC_INTERNAL_FAULT=70
PLURAI_RC_CREDENTIALS=77

# The one code that is the plugin's own and not the SDK's: a script that stops on a version refusal and prints the plugin's
# text exits with it (`EXIT_VERSION_REFUSED` in `_client_handshake.py`). 7 is no row of the SDK's table.
PLURAI_RC_VERSION_REFUSED=7

# What a host with no python3 to ask prints for a refused SDK: `SDK_REFUSAL_FALLBACK` in `_client_handshake.py`, the sentence alone
# (only a running interpreter knows the harness, so no update command is named). The one copy the shell scripts print;
# `test_sdk_refusal_call_sites.py` keeps it equal.
PLURAI_SDK_REFUSAL_FALLBACK='Plurai refused the SDK this plugin installs, and the update text could not be produced. Update the plugin in a terminal, then start a new session.'
