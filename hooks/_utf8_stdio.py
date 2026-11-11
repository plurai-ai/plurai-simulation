"""UTF-8 standard streams for a hook process on native Windows, and nothing anywhere else.

Claude Code, Copilot and Codex write the payload to a hook's stdin as UTF-8 and decode its stdout and
stderr as UTF-8. On Windows Python gives a pipe the ANSI code page (cp1252), so a hook's em dash
reaches the harness as byte 0x97 and a non-ASCII line breaks its decoding. Every hook entry point calls
:func:`utf8_stdio` first; ``scripts/_stdio.py`` hands the same function to the plugin's scripts. Off
Windows it returns at once, so macOS and Linux are unchanged under any locale. Python 3.15 makes UTF-8
the default (PEP 686), and this goes with it. Stdlib only, and hooks-local: a hooks/ tree runs without
scripts/ beside it.
"""

from __future__ import annotations

import os
import sys

#: Native Windows. A test sets ``_utf8_stdio.WINDOWS`` to take the Windows branch; ``_platform.WINDOWS`` is not read
#: here, so a test that patches that one never re-encodes its own streams.
WINDOWS = os.name == "nt"


def utf8_stdio() -> None:
    """On Windows, make each standard stream that is not UTF-8 already UTF-8 with ``errors='replace'``."""
    if not WINDOWS:
        return
    for name in ("stdin", "stdout", "stderr"):  # by name: a hook script never touches stdin itself (the codec reads it)
        stream = getattr(sys, name, None)
        try:
            if stream.encoding.lower().replace("_", "-") not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):  # None, a StringIO, or a stream already read from: kept as it is
            continue
