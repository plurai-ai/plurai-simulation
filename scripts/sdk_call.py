#!/usr/bin/env python3
"""Run one SDK command; a version refusal reads as the plugin's text.

    python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/sdk_call.py" [--refusal-stderr] "$PLURAI_BIN" <command> [arguments]

The command's output is held until its exit code is known. The SDK's upgrade-required exit carries the SDK's own text and
a ``pip`` line, which the plugin never shows: that exit prints ``_client_handshake.sdk_refusal_message`` on stdout and
exits with the plugin's refusal code (7). ``--refusal-stderr`` prints that text on stderr instead, for a call whose stdout
is redirected to a file or captured, so the text never lands in the file. Any other exit replays the held stdout and stderr and exits with the command's own
code. Standard input and the environment pass through; the held output is shown when the command ends, which is when the
harness returns a call's output anyway.

Exit codes: the command's own, 7 on a version refusal, 126 or 127 when the command cannot be started, 128+n when the
command ends on signal n, 64 on a call with no command.

Stdlib only, like every script here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdk_exit_codes  # noqa: E402 - after the sys.path line it depends on

EXIT_USAGE, EXIT_NOT_EXECUTABLE, EXIT_NOT_FOUND = 64, 126, 127
REFUSAL_STDERR = "--refusal-stderr"


def call(argv: list[str]) -> int:
    """Run ``argv`` and return the exit code the caller sees; see the module docstring."""
    refusal_stream = sys.stdout
    if argv[:1] == [REFUSAL_STDERR]:
        refusal_stream, argv = sys.stderr, argv[1:]
    if not argv:
        print(f"usage: sdk_call.py [{REFUSAL_STDERR}] <plurai runner> <command> [arguments]", file=sys.stderr)
        return EXIT_USAGE
    try:
        done = subprocess.run(argv, capture_output=True, check=False)  # noqa: S603 - the runner the resolver chose, named by the caller
    except FileNotFoundError:
        print(f"sdk_call: {argv[0]}: no such file", file=sys.stderr)
        return EXIT_NOT_FOUND
    except OSError as error:
        print(f"sdk_call: {argv[0]}: {error.strerror or type(error).__name__}", file=sys.stderr)
        return EXIT_NOT_EXECUTABLE
    if done.returncode == sdk_exit_codes.UPGRADE_REQUIRED:
        import _client_handshake  # noqa: PLC0415 - its urllib import is paid only by a refusal, not by every SDK call

        print(_client_handshake.sdk_refusal_message((done.stdout + done.stderr).decode("utf-8", "replace")), file=refusal_stream)
        return _client_handshake.EXIT_VERSION_REFUSED
    sys.stdout.flush()
    sys.stdout.buffer.write(done.stdout)
    sys.stdout.buffer.flush()
    sys.stderr.flush()
    sys.stderr.buffer.write(done.stderr)
    sys.stderr.buffer.flush()
    return 128 - done.returncode if done.returncode < 0 else done.returncode


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(call(sys.argv[1:]))
