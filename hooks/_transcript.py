"""Bounded tail reads of the session transcript (the hook payload's ``transcript_path``).

Shared by the hooks that decide on what the session last SAID rather than on a marker:
``flow_interrupt`` (did the main thread's last turn end on an API error?) and ``report_guard``
(did the USER speak since the flow window was armed?). The transcript is JSONL and can be
megabytes; the rows that decide are the newest ones, so a bounded tail read is size-independent.

Stdlib only, and never raises: an unreadable or missing transcript yields no rows, which every
caller reads as its safe answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def rows_newest_first(path_str: str, *, tail_bytes: int) -> Iterator[object]:
    """The transcript's parsed rows, newest first, from at most its last ``tail_bytes``.

    Stops at the first row that does not parse: that is a row mid-write or a truncated tail, and
    reading PAST it would answer from an older turn — a confident wrong answer either way. A blank
    line is not a row and is skipped, not read as a truncation. Rows are yielded as parsed (not
    necessarily dicts); the caller decides what a row means.
    """
    if not path_str:
        return
    try:
        path = Path(path_str)
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > tail_bytes:
                handle.seek(size - tail_bytes)
                handle.readline()  # discard the partial line the seek landed inside
            lines = handle.read().splitlines()
    except OSError:
        return
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:  # noqa: BLE001 - see the docstring: an unparseable row ends the read
            return
        yield row


def object_rows(path_str: str, *, tail_bytes: int) -> list[dict[str, Any]]:
    """The tail's rows that are JSON objects, newest first: what every harness's reader walks."""
    return [row for row in rows_newest_first(path_str, tail_bytes=tail_bytes) if isinstance(row, dict)]
