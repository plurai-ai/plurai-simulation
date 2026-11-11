"""The SDK's exit codes, by name, for the Python scripts that mirror or dispatch on a plurai command line.

The table is the SDK's `plurai.exit_codes`: every plurai command returns these and only these, and 2 is
never one of them. `sdk-exit-codes.sh` carries the same table for shell callers and
`scripts/tests/test_sdk_exit_codes.py` keeps the two equal. Stdlib only.
"""

from __future__ import annotations

OK = 0
POLICIES_FAILED = 1
UPGRADE_REQUIRED = 10
MOCK_UNANSWERED = 11
ROUTE_MISSING = 12
JOB_FAILED = 13
STILL_RUNNING = 14
NOT_FOUND = 15
NOT_READY = 16
BACKEND_REFUSED = 17
SCAN_HELD = 18
REQUIREMENT_GAP = 19
NOT_MEASURED = 20
USAGE = 64
REFUSED = 65
INPUT_UNREADABLE = 66
TRANSPORT = 69
INTERNAL_FAULT = 70
CREDENTIALS = 77

#: Every code by name, in the order of the SDK's README table.
TABLE: dict[str, int] = {
    name: value for name, value in globals().items() if name.isupper() and isinstance(value, int)
}

#: What `plurai fetch` and `plurai backend identity` exit with when the gateway refuses the read for credit (a 402 naming a
#: credit kind; the kind's sentence is on stderr). In those commands' tables the code has that one meaning, so a bare exit
#: of it is the credit stop. Not in `TABLE`: it is a reading of `JOB_FAILED`, not a code of its own.
FETCH_CREDIT_REFUSED = JOB_FAILED
