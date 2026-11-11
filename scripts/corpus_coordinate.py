#!/usr/bin/env python3
"""Which corpora a workdir stands on — read from ALL THREE marker shapes, in one place.

WHY THIS EXISTS. The corpus a knowledge agent stands on is recorded in three different files,
because three different steps write it and each has a different amount to say:

    .knowledge_bases   THE CONTRACT. A JSON list, one entry per RAG/SQL tool, each
                       ``{kb_id, label, corpus_ids: [...], tool}`` — a per-tenant tool carries
                       several corpora in one entry. Written by `/build-rag-corpus`; read by
                       `/generate-scenarios` and `/generate-evals`.
    .corpus_ids        One id per line. Written by `/manage-envs` when a saved env manifest
                       carries a `corpus_ids` list, and staged by `stage-inputs.sh`.
    .corpus_id         BACK-COMPAT ONLY: written *in addition*, and only when the agent has
                       exactly ONE knowledge base whose `corpus_ids` is a single id.

Readers kept picking the third one, which is the only shape a multi-knowledge-base agent does
not have. `lane_pin.py` read `.corpus_id` on three lines and `.knowledge_bases` on none, so its
whole `corpus-moved` axis was dead for that customer class however often their bases were
rebuilt: with `--set-corpus-ids` it printed `corpus-unknown` and exited 0, and without it printed
`FRESH` with no line at all, because "nothing to compare" is not "could not read". The sibling
that got it right, `scan_contract.sh`, only got as far as saying the fingerprint was unbound.

So the shapes are read ONCE, here, and every consumer asks this module instead of naming a file.
A fourth shape lands in one place.

READING RULE. Every shape that answers contributes, and the answer is their UNION. A workdir
half-migrated between shapes (the manifest rewritten, the back-compat single id not) then reports
BOTH ids, which compares unequal to whatever was pinned — loud, in the direction that asks a human
rather than the direction that blesses a frame nobody checked.

UNKNOWN is a real answer and it is not the empty set. ``None`` means no shape could be read: no
marker is present, or one that IS present cannot be parsed. ``frozenset()`` would say "this agent
stands on no corpus", which a reader is entitled to treat as agreement with another empty
coordinate — the exact conflation `corpus-unknown` exists to end.

Usage (the shell consumer, `scan_contract.sh`):
    corpus_coordinate.py [--workdir DIR]      # one id per line on stdout
    exit 0 = ids printed · 1 = no marker is present · 2 = one IS present and cannot be parsed

1 and 2 are both UNKNOWN and neither is "no corpus", but a shell caller has to tell them apart:
1 is the ordinary state of an agent before its corpus is built and is worth no word, while 2 is a
broken marker that leaves the caller's own output bound to nothing — silence there is the
conflation this module exists to end, said by the consumer instead of by the reader.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_fingerprint  # noqa: E402 - sibling script: the workdir resolver cascade lives there

#: The marker shapes, richest first. The order is the order they are REPORTED in, not a
#: precedence: every readable shape contributes to the union (see the reading rule above).
KNOWLEDGE_BASES = '.knowledge_bases'
CORPUS_IDS = '.corpus_ids'
CORPUS_ID = '.corpus_id'
MARKERS = (KNOWLEDGE_BASES, CORPUS_IDS, CORPUS_ID)


class Coordinate(NamedTuple):
    """What the workdir says about its corpora, and how it said it.

    `ids` is the answer every caller wants; `read` and `unreadable` are what a message needs to
    name the file rather than the concept — an operator told "the corpus coordinate is unknown"
    cannot act, and one told "`.knowledge_bases` is not valid JSON" can.
    """

    ids: frozenset[str] | None
    read: tuple[str, ...]
    unreadable: tuple[str, ...]


def render(ids: frozenset[str] | None) -> str:
    """A coordinate as one comma-joined string: sorted ids, `(none)`, or `unknown`.

    Sorted because this string is compared by eye across two runs and a set has no order to be
    stable across them; `(none)` because an empty coordinate rendered as the empty string is what
    a `lane_pin check` line read as when a manifest declared no corpora — `current ` with nothing
    after it, which looks like the reader failed rather than like the answer being "no corpus".
    The three states stay three strings all the way to the operator.
    """
    return 'unknown' if ids is None else (','.join(sorted(ids)) or '(none)')


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8').strip() or None
    except OSError:
        return None


def _one_per_line(raw: str) -> list[str]:
    """`.corpus_ids` and `.corpus_id`: one id per line, blanks dropped.

    A blank line is dropped rather than kept as an empty id for the reason `lane_pin write`
    refuses a blank set id: `$(cat "$WORKDIR/.corpus_id")` yields '' whenever the marker is
    missing, and an empty id compares unequal to every real one — a drift that never happened.
    """
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _from_knowledge_bases(raw: str) -> list[str] | None:
    """Ids out of the manifest, or None when it is present and cannot be understood.

    An entry with no `corpus_ids` is legal (a base declared before it was built) and contributes
    nothing; a document that is not a list of mappings is a broken manifest, and guessing past it
    would silently answer for a customer whose real coordinate nobody read.
    """
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(doc, list):
        return None
    ids: list[str] = []
    for entry in doc:
        if not isinstance(entry, dict):
            return None
        for value in entry.get('corpus_ids') or ():
            if isinstance(value, str) and value.strip():
                ids.append(value.strip())
    return ids


def read_coordinate(workdir: Path) -> Coordinate:
    """The union of every readable marker shape, with the provenance a message needs.

    The shape table names the FILE and the parser together, so a fourth shape is one row.
    """
    shapes = (
        (KNOWLEDGE_BASES, workdir / '.knowledge_bases', _from_knowledge_bases),
        (CORPUS_IDS, workdir / '.corpus_ids', _one_per_line),
        (CORPUS_ID, workdir / '.corpus_id', _one_per_line),
    )
    ids: set[str] = set()
    read: list[str] = []
    unreadable: list[str] = []
    for name, path, parse in shapes:
        raw = _text(path)
        if raw is None:
            continue
        found = parse(raw)
        if found is None:
            unreadable.append(name)
            continue
        read.append(name)
        ids.update(found)
    if unreadable or not read:
        # A marker that exists and cannot be parsed makes the whole coordinate unknown, not a
        # smaller one: answering from the shapes that happened to parse would report a SUBSET as
        # if it were the coordinate, and a subset compares unequal to the pin — a manufactured
        # `corpus-moved` — or equal to it by luck. Unknown is the honest answer and it warns.
        return Coordinate(None, tuple(read), tuple(unreadable))
    return Coordinate(frozenset(ids), tuple(read), ())


def corpus_coordinate(workdir: Path) -> frozenset[str] | None:
    """The corpus ids this workdir stands on, or None when no shape is readable.

    THE function every consumer calls. `None` is UNKNOWN and is never the empty set: see the
    module docstring.
    """
    return read_coordinate(workdir).ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the shared resolver cascade')
    args = parser.parse_args(argv)
    workdir = harness_fingerprint.resolve_workdir(args.workdir)
    coordinate = read_coordinate(workdir)
    if coordinate.ids is None:
        detail = (f'{", ".join(coordinate.unreadable)} present but unreadable'
                  if coordinate.unreadable else f'none of {", ".join(MARKERS)} is present')
        print(f'corpus_coordinate: UNKNOWN in {workdir} — {detail}.', file=sys.stderr)
        return 2 if coordinate.unreadable else 1
    for corpus_id in sorted(coordinate.ids):
        print(corpus_id)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
