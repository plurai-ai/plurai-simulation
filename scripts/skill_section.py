#!/usr/bin/env python3
"""Print one section of a long markdown file, numbered, with a footer that says where it ended.

    skill_section.py skills/simulation-setup/SKILL.md "Step 9"
    skill_section.py skills/simulation-setup/SKILL.md "Step 9" --from 1700

A shell that cuts a long tool output keeps the head and the tail and drops the middle without a line
number to show it. A section printed here is numbered, capped at ``--max`` lines (default 250) and closed
by one footer line: ``END`` names the section's last line and the heading that follows it, ``CONTINUES``
names the line to resume from. An output is whole when its numbers run without a jump to the footer; a
jump, or no footer, means a cut. Lines are numbered as ``nl`` and ``wc -l`` count them (by newline only).

The section is the first heading whose text contains the title (case-insensitive) and everything below
it up to the next heading of the same or a higher level. Headings inside code fences do not count.

Exit codes: 0 printed · 2 a file that cannot be read, no such heading, or ``--from`` outside the section.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """``(line number, level, text)`` for every heading outside a code fence."""
    found: list[tuple[int, int, str]] = []
    opener: str | None = None
    for number, line in enumerate(lines, 1):
        fence = _FENCE.match(line)
        if opener is not None:
            if fence and fence.group(1)[0] == opener[0] and len(fence.group(1)) >= len(opener) and not line.strip().strip("`~"):
                opener = None
            continue
        if fence:
            opener = fence.group(1)
            continue
        match = _HEADING.match(line)
        if match:
            found.append((number, len(match.group(1)), match.group(2)))
    return found


def section(lines: list[str], title: str) -> tuple[int, int, str, str | None] | None:
    """``(first, last, heading, next heading)`` of the section whose heading contains ``title``."""
    marks = headings(lines)
    for index, (number, level, text) in enumerate(marks):
        if title.lower() in text.lower():
            for later_number, later_level, later_text in marks[index + 1 :]:
                if later_level <= level:
                    return number, later_number - 1, text, later_text
            return number, len(lines), text, None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print one section of a markdown file, numbered, with an END or CONTINUES footer.")
    parser.add_argument("file", type=Path)
    parser.add_argument("title", help="text the section's heading contains")
    parser.add_argument("--from", dest="start", type=int, help="resume at this line of the section")
    parser.add_argument("--max", dest="limit", type=int, default=250, help="most lines to print (default 250)")
    args = parser.parse_args(argv)
    # Skill text carries arrows and dashes a cp1252 console (the Windows default) cannot encode; a cut read is what
    # this script exists to prevent, so an unencodable character is replaced rather than ending the output.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    try:
        lines = args.file.read_text(encoding="utf-8", errors="replace").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
    except OSError as exc:
        print(f"skill_section: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    found = section(lines, args.title)
    if found is None:
        print(f"skill_section: no heading in {args.file} contains {args.title!r}.", file=sys.stderr)
        return 2
    first, last, heading, following = found
    start = args.start if args.start is not None else first
    if not first <= start <= last:
        print(f"skill_section: --from {start} is outside the section ({first}-{last}).", file=sys.stderr)
        return 2
    stop = min(last, start + max(args.limit, 1) - 1)
    for number in range(start, stop + 1):
        print(f"{number:6d}\t{lines[number - 1]}")
    name = f"{args.file.name} § {heading}"
    if stop < last:
        print(f"CONTINUES {name}: printed lines {start}-{stop} of {first}-{last}; next: --from {stop + 1}")
    else:
        print(f"END {name}: lines {first}-{last} of {len(lines)}; next section: {following if following else 'none (end of file)'}")
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
