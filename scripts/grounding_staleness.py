#!/usr/bin/env python3
"""What moved in the grounding ledger since the version a suite pinned.

    grounding_staleness.py --workdir D [--plurai BIN]

Prints one `STALENESS=` line and always exits 0: `ungrounded` (no pin), `unverified` (a pin and no mirror),
`current` (the mirror is the pinned version) or `moved`. A moved check asks the backend for the diff between the two
(`fetch_artifact(<mirror's version>, paths=['diff/<pinned>'])`), writes it to `artifacts/ledger_moved.yaml` and counts
the entries, findings and parts it adds, removes and changes. With no runner, no answer, or an answer that is not the
diff between exactly those two versions, it says only that the mirror's id differs from the pinned one as of the last
pull. The mirror and the pin are never written: adopting a newer version is a stage-boundary edit, not a check.

Runs under `$PLURAI_PY` (PyYAML counts the diff); without PyYAML the line carries `counts=unavailable` and the kept
diff still holds the detail. `grounding_mirror.py` stays stdlib-only so its pull block runs on any interpreter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _client_handshake
import grounding_files
import grounding_mirror

DIFF_NAME = 'ledger_moved.yaml'
_DIFF_SECTIONS = (
    ('entries', 'added'),
    ('entries', 'removed'),
    ('entries', 'changed'),
    ('findings', 'added'),
    ('findings', 'removed'),
    ('findings', 'changed'),
    ('parts', 'added'),
    ('parts', 'removed'),
)


def _say(**fields: object) -> None:
    print(' '.join(f'{key}={value}' for key, value in fields.items()), flush=True)


def diff_counts(text: str) -> str:
    """`entries_added=N ...` for the diff, or `counts=unavailable` when PyYAML is missing or the body is not a mapping."""
    try:
        import yaml  # noqa: PLC0415 - optional: the check reports without counts when it is missing
    except ImportError:
        return 'counts=unavailable'
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return 'counts=unavailable'
    if not isinstance(document, dict):
        return 'counts=unavailable'
    counts = []
    for section, change in _DIFF_SECTIONS:
        body = document.get(section)
        rows = body.get(change) if isinstance(body, dict) else None
        counts.append(f'{section}_{change}={len(rows) if isinstance(rows, list) else 0}')
    return ' '.join(counts)


def _cmd(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    pinned = grounding_files.pinned_version(workdir)
    if pinned is None:
        _say(STALENESS='ungrounded', reason='no suite has pinned a version here')
        return 0
    header = grounding_mirror.mirror_header(workdir)
    if header is None:
        _say(STALENESS='unverified', pinned=pinned, reason='no mirror on disk to compare with')
        return 0
    latest = header['ledger_version']
    if latest == pinned:
        _say(STALENESS='current', pinned=pinned)
        return 0
    diff = grounding_mirror.fetch_diff(workdir, args.plurai, latest, pinned)
    if diff is None:
        _say(
            STALENESS='moved',
            pinned=pinned,
            latest=latest,
            detail='unavailable',
            reason="the mirror's version differs from the pinned one as of the last pull; what moved needs the backend",
        )
        return 0
    grounding_files.write_atomic(workdir / 'artifacts' / DIFF_NAME, diff)
    print(f'STALENESS=moved pinned={pinned} latest={latest} {diff_counts(diff)} diff=artifacts/{DIFF_NAME}', flush=True)
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="what moved in the mirror since the pinned version")
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--plurai', default='', help='the resolved `plurai` runner; empty when there is none')
    args = parser.parse_args(argv)
    return _client_handshake.refusal_main(lambda: _cmd(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
