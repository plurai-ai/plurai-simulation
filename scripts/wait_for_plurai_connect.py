#!/usr/bin/env python3
"""Wait, in the background, until Claude Code connects the Plurai server again.

A Claude Code session that started before the first login has no Plurai tools until the user reconnects the server
(``/mcp reconnect all``). Claude Code runs the server's ``headersHelper`` (``credentials.py --headers``) on every
connection, and that run writes the time to ``~/.plurai/mcp_connected``. This script polls the file and prints one line:

    reconnected   the file is newer than ``--since``: a connection happened after the wait began
    timed out     no connection within ``--timeout`` seconds (ten minutes by default)

``--since`` is seconds since the epoch and defaults to the moment the script starts. It always exits 0 with its answer
on standard output, so a harness shows no failure for a wait that ended in a timeout. A connection of another session
can end the wait early; the caller looks at its tool list and waits again if the tools are still missing.

Stdlib only and runnable on Python 3.9: it runs under the system ``python3``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import credentials

DEFAULT_TIMEOUT = 600
DEFAULT_INTERVAL = 1.0
USAGE = 'usage: wait_for_plurai_connect.py [--since <epoch seconds>] [--timeout <seconds>] [--interval <seconds>]'


def parse(argv):
    """The options as ``{name: float}``, or ``None`` for a call this script does not understand."""
    values = {}
    names = ('--since', '--timeout', '--interval')
    if len(argv) % 2:
        return None
    for flag, raw in zip(argv[::2], argv[1::2]):
        if flag not in names or flag in values:
            return None
        try:
            values[flag] = float(raw)
        except ValueError:
            return None
    return values


def wait(since, timeout, interval) -> str:
    deadline = time.monotonic() + timeout
    while True:
        if credentials.last_connection() > since:
            return 'reconnected'
        if time.monotonic() >= deadline:
            return 'timed out'
        time.sleep(interval)


def main(argv) -> int:
    values = parse(argv)
    if values is None:
        print(USAGE, file=sys.stderr)
        return 64
    since = values.get('--since', time.time())
    print(wait(since, values.get('--timeout', DEFAULT_TIMEOUT), values.get('--interval', DEFAULT_INTERVAL)))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
