#!/usr/bin/env python3
"""Print the page's running step once, for the chat to say verbatim (report doctrine, item 1); nothing otherwise.

    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/progress_line.py" <workdir> [--flowdir D]

The line is ``publish_report.progress_line``: the running step's bare label, as the page shows it; nothing while no stage
runs, while the flow waits for the user's answer, or when that step was said already. It reads the page's progress state
and publishes nothing, so a skill that must never publish the overview page (setup, analyze) can name it. Exit 0 always.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import publish_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('workdir', type=Path)
    ap.add_argument('--flowdir', type=Path)
    args = ap.parse_args(argv)
    line = publish_report.progress_line(args.workdir.resolve(), args.flowdir.resolve() if args.flowdir else None)
    if line:
        print(line)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
