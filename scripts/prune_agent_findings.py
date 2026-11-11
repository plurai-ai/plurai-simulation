#!/usr/bin/env python3
"""Drop the CONFIRMED sections of an agent-findings file, keep the POTENTIAL ones.

Arming a build window opens a new evidence epoch: the previous build's confirmed defects
must not be presented as this one's, which is why arming used to delete the file outright.
But the file now carries a second tier — limitations recorded because they narrowed what the
suite tests — and the scan's are written before any build arms and re-derived by nothing.

The two losses are not symmetric, and that decides every judgement call below. A surviving
stale CONFIRMED finding is loud and recoverable: it names a run folder that no longer exists,
and a human reading the report can see that. A lost POTENTIAL entry is silent and
unrecoverable — re-deriving it means re-running the scan, a whole customer-facing command.

So this script keeps on doubt. It keeps a section it can positively read as carrying doubt and
drops the rest; anything it cannot parse AT ALL it leaves alone, and any failure exits non-zero
so the caller can WARN rather than guess. The caller must not delete the file on that exit — see
flow-window.sh.

Note the asymmetry that decides the tombstone's wording below: the read is "does this section
declare doubt", never "is this section confirmed". A dropped section is one whose doubt marker was
not READABLE, which includes a confirmed finding AND an author who spelled the status a way this
parser does not know. The tombstone may therefore say the section was dropped; it may not say what
the section was.

Usage: prune_agent_findings.py <path> <stage,...>   (a missing file is a no-op, exit 0)
  <stage,...>: the `found during:` stages whose confirmed sections are KEPT — the arming command's
  own table (open_epoch.RESETS_FINDINGS); '' keeps none.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import record_finding  # noqa: E402

# The tier marker. Three skills hand-author this file from a template, so the match has to
# survive ordinary markdown variance — bold or not, colon inside or outside the emphasis,
# a backticked value, a numbered list — while still anchoring to the status FIELD, so the
# word "potential" in a section's prose cannot rescue a confirmed finding.
#
# The VALUE vocabulary is a RECOGNITION HINT, nothing more. Widening it recovers the sections an
# author spelled `possible` / `suspected` from the drop pile; it does not make the drop pile a
# claim. Everything this regex misses lands in the not-readable arm — which is why the tombstone
# below says only that the section carried no readable doubt marker, and why widening here
# is the SECOND fix, never the one the honesty of the output rests on. Deliberately English-only
# and deliberately not exhaustive: a customer-language findings file will still miss, and the
# output has to be correct when it does.
_POTENTIAL = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*status[ \t]*[*_`]{0,2}[ \t]*:"
    r"[ \t]*[*_`]{0,2}[ \t]*(?:potential|possible|suspected|unconfirmed|candidate|open)\b",
    re.IGNORECASE | re.MULTILINE,
)

# A section opens at a top-level `## ` heading in column 0; the literal space already
# excludes `###`, which is a sub-heading inside a section and must not split it.
_SECTION = re.compile(r"^## ")

# Fence delimiters, opening and closing. A finding's evidence block routinely QUOTES customer
# markdown, so a `## ` line inside a fence is quoted text, not a new section — splitting there
# cuts a POTENTIAL entry in half and drops its tail (the closing fence included), which is the
# silent unrecoverable loss this whole script is written to avoid.
#
# The two patterns are deliberately asymmetric. Opening is lenient (any indent, any info
# string); closing demands a line of nothing but the same character, at least as long. Both
# lean the same way — toward staying INSIDE a fence — because the failure modes are not
# symmetric: an over-eager close splits a quoted heading (silent loss), while an over-eager
# open merges sections, and a merged section is a UNION, so it is kept whenever any half is
# potential. Merging can only ever keep more than it should, which is the loud, recoverable side.
_FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_FENCE_CLOSE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")


# Content the parser could not section: a heading-shaped line that is not a `## ` start
# (`### 1.`, `#1.`, an indented `  ## 1.`). Keeping such a file is the safe call, but it must
# not be a SILENT one — the confirmed findings in it are about to be presented as this
# build's, which is the failure the epoch reset exists to prevent.
_UNREADABLE = re.compile(r"^[ \t]*#{1,6}[^\s#]|^[ \t]+#{2,6}[ \t]|^[ \t]*#{3,6}[ \t]", re.MULTILINE)


def _split_sections(text: str) -> list[str]:
    """`[preamble, *sections]`, splitting only at `## ` headings OUTSIDE fenced code blocks.

    Same contract as the `re.split` on a zero-width lookahead it replaces — the preamble comes
    first and may be empty, each section carries its own heading, and concatenating the result
    reproduces the input byte for byte.
    """
    parts: list[list[str]] = [[]]
    fence: tuple[str, int] | None = None  # (delimiter char, opening run length)
    for line in text.splitlines(keepends=True):
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened:
                fence = opened.group(1)[0], len(opened.group(1))
            elif _SECTION.match(line):
                parts.append([])
        else:
            closed = _FENCE_CLOSE.match(line)
            if closed and closed.group(1)[0] == fence[0] and len(closed.group(1)) >= fence[1]:
                fence = None
        parts[-1].append(line)
    return ["".join(part) for part in parts]


# The tombstone block's marker line, and the shape of one tombstone. A dropped section leaves
# its heading behind so the numbering a reader sees is CONTINUOUS: without this a ledger
# jumps from `## 3` straight to `## 9`, and a blinded review charges the five missing findings
# as a misreported claim — the gap is made here, by a prune nobody can see.
#
# Deliberately NOT a `## ` section and not a heading of any level. Three skills gate on "does the
# file have `##` sections" and omit the customer-facing findings block when it does not, so a
# tombstone that opened a section would make a file with nothing left in it read as one that
# still carries findings. `_SECTION` therefore never splits on these, and `_UNREADABLE` — which
# fires on `###`-shaped lines — never sees them either.
#
# The line says only what is KNOWN. It used to assert "confirmed then" for every section, which is
# a claim this script cannot make: a section is dropped when the doubt marker was not READABLE, and
# an author who wrote `status: possible`, or authored in the customer's language, got a positive
# FALSE claim that a never-confirmed limitation had been a confirmed agent defect. That is the same
# class as the "report must not manufacture the verdict it reports" fix two commits earlier, and it
# is the one that matters here: the vocabulary widening beside it is a recognition hint, so a miss
# must land in the not-readable arm and never in a claim.
#
# …and it names the CATEGORY, not one spelling of it. `_POTENTIAL` accepts six spellings
# (potential, possible,
# suspected, unconfirmed, candidate, open), so "not readable as `status: potential`" told the
# author of a dropped `status: suspected` section that their spelling was the problem when the
# script would have kept it. The tombstone is read by a person reconstructing a gap in the
# numbering; a wrong remedy there costs a re-run.
_TOMBSTONE_MARKER = "**Pruned in an earlier build** — numbering is never reused:"
_TOMBSTONE_LINE = "- ~~{heading}~~{ident} — dropped when a later build armed its evidence window (no readable doubt marker on the `status:` line).\n"

# WITHDRAWN sections get their own sentence, because the one above is a claim about them that is
# false and damaging. A `status: RETIRED` finding is one an earlier round REPRODUCED AS A FALSE RED
# and took back; it carries no readable doubt marker either, so it was struck under wording that
# tells a reader its doubt was merely unreadable — i.e. that it might be a real defect the parser
# could not see. Two of the seven tombstones in the 0909 archive are this shape
# (`agent.channel_roles_incomplete`, `agent.kc_root_outside_decline`), and a withdrawal presented
# as a suppressed defect is exactly the kind of manufactured verdict the line above was fixed to
# stop making.
#
# It still DROPS. Nothing about what is pruned changes here: a withdrawn finding is last build's
# and must not ride into this one either. Only the sentence differs.
_RETIRED_TOMBSTONE_LINE = "- ~~{heading}~~{ident} — WITHDRAWN by an earlier round (`status: RETIRED`), then dropped when a later build armed its evidence window.\n"

# The durable identity of a finding, and the reason this whole file matters to a reader. Titles are
# NOT that identity: four entries of the 0909 archive (#13, #17, #25 and #30 in the live file) are
# four spellings of one defect — "for transitions the guides do not document", "for privacy-state
# changes", "for changes" — so a later build re-confirming it cannot match its predecessor by text.
# The `- **finding id:**` line the authoring templates already ask for IS that key, and the prune
# was throwing it away at exactly the moment it became load-bearing: every struck entry in the real
# ledger lost it, so #33 `agent.invented_reversal_route` had nothing to cite when it re-surfaced as
# the same defect as struck #9 and #20.
#
# Carried onto the tombstone, never invented: a section without one leaves the tombstone exactly as
# it reads today. Same markdown tolerance as `_POTENTIAL` — three skills hand-author this file.
# The run AFTER the colon is `[ \t*_`]*`, not the `[*_`]{0,2}` its siblings use, and the real file
# is why: `- **finding id:** `agent.reply_budget_exceeded`` closes the bold AND opens a code span,
# so three emphasis characters and a space stand between the colon and the value. A two-character
# class matched none of the seven ids in the 0909 archive — which the negative control caught
# before this shipped, because it was run against the real bytes rather than a fixture written to
# the pattern. The capture starts `[A-Za-z0-9]`, so a permissive run cannot eat into the id.
_FINDING_ID = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*finding[ \t_-]*id[ \t]*[*_`]{0,2}[ \t]*:"
    r"[ \t*_`]*([A-Za-z0-9][\w.\-/]*)",
    re.IGNORECASE | re.MULTILINE,
)

# The withdrawal marker, read off the status FIELD like `_POTENTIAL` and for the same reason: the
# word "retired" in a section's prose may not retype a live finding as a withdrawn one.
_RETIRED = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*status[ \t]*[*_`]{0,2}[ \t]*:"
    r"[ \t]*[*_`]{0,2}[ \t]*(?:retired|withdrawn|revoked)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _leading_number(line: str) -> int | None:
    """The `N` a tombstone line's heading opens with, or None when it has none."""
    match = re.search(r"~~\s*(\d+)", line)
    return int(match.group(1)) if match else None


def _heading_of(section: str) -> str:
    """A section's heading text, without the `## ` and without trailing whitespace."""
    return section.splitlines()[0][3:].strip()


def _identity_of(section: str) -> str:
    """The ` (\\`<id>\\`)` suffix a tombstone carries, or `''` when the section declares none.

    Rendered here rather than at the format site so a section with no id produces a tombstone
    byte-identical to today's: the whole value of this line is that a later build can cite it, and
    an invented or empty id would be worse than none.
    """
    found = _FINDING_ID.search(section)
    return f" (`{found.group(1)}`)" if found else ""


def _tombstone(section: str) -> str:
    """One tombstone line for one dropped section — withdrawn sections get their own sentence."""
    template = _RETIRED_TOMBSTONE_LINE if _RETIRED.search(section) else _TOMBSTONE_LINE
    return template.format(heading=_heading_of(section), ident=_identity_of(section))


def _with_tombstones(preamble: str, dropped: list[str]) -> str:
    """`preamble` carrying one tombstone per dropped SECTION, in ONE block at its end.

    Takes the sections rather than their headings: the tombstone carries the
    section's `- **finding id:**` and picks its wording from the section's status, and neither is
    recoverable from a heading string.

    Earlier builds' tombstones are re-emitted ahead of this build's, so a file pruned three times
    still reads as one continuous ledger rather than three stacked blocks.

    Anything in the preamble that is NOT a tombstone is carried through on either side of the
    block, never dropped. Today the marker is always written last so nothing follows it — but this
    file exists because a prune nobody could see removed content silently, and a hand-added note
    under the block must not be the next thing it removes."""
    body, _, existing = preamble.partition(_TOMBSTONE_MARKER)
    tombstones = [line for line in existing.splitlines(keepends=True) if line.startswith("- ~~")]
    trailing = [line for line in existing.splitlines(keepends=True) if not line.startswith("- ~~") and line.strip()]
    tombstones += [_tombstone(section) for section in dropped]
    # BY NUMBER, not by the order the prunes happened in: the skill tells a reader the numbering
    # is continuous, and `~~1~~, ~~3~~, ~~2~~` after two prunes is not what that promises. An
    # unnumbered heading sorts last rather than crashing the sort.
    tombstones.sort(key=lambda line: (_leading_number(line) is None, _leading_number(line) or 0, line))
    return (body.rstrip("\n") + "\n\n" + _TOMBSTONE_MARKER + "\n" + "".join(tombstones)
            + ("\n" + "".join(trailing) if trailing else "") + "\n")


# The stage a section was recorded in — its `found during:` field's first word, read like `status:`.
_FOUND_DURING = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*found[ \t]+during[ \t]*[*_`]{0,2}[ \t]*:"
    r"[ \t]*[*_`]{0,2}[ \t]*([a-z]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _kept(section: str, keep_stages: tuple[str, ...], rows: dict[str, dict] | None = None) -> bool:
    """Potential, or recorded in a stage the arming command does not re-derive.

    A section whose `finding id` has a typed row (`record_finding.py`) is decided by that row alone;
    the prose fields are read only for a section no row covers."""
    found = _FINDING_ID.search(section)
    row = (rows or {}).get(found.group(1)) if found else None
    if row is not None:
        return row.get('status') == 'potential' or str(row.get('found_during') or '').split(' ')[0] in keep_stages
    stage = _FOUND_DURING.search(section)
    return bool(_POTENTIAL.search(section)) or bool(stage and stage.group(1).lower() in keep_stages)


def prune(text: str, *, keep_stages: tuple[str, ...], rows: dict[str, dict] | None = None) -> tuple[str, int, int]:
    """Return (pruned text, kept count, dropped count). Unparseable input is returned whole."""
    preamble, *sections = _split_sections(text)
    if not sections:
        return text, 0, 0

    kept = [s for s in sections if _kept(s, keep_stages, rows)]
    dropped = [s for s in sections if not _kept(s, keep_stages, rows)]
    if not dropped:
        return text, len(kept), 0
    preamble = _with_tombstones(preamble, dropped)
    if not kept:
        # Header + tombstones only. The three skills that gate on this file test "has ## sections",
        # and a tombstone is not one, so a file with nothing left still reads as empty to them.
        return preamble, 0, len(dropped)
    return preamble + "".join(kept).rstrip("\n") + "\n", len(kept), len(dropped)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: prune_agent_findings.py <path> <stage,...>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    keep_stages = tuple(stage for stage in argv[2].split(",") if stage)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as exc:
        # ValueError covers UnicodeDecodeError: this file quotes customer data as evidence,
        # so one stray non-UTF-8 byte is a realistic way in — and must not cost the file.
        print(f"prune_agent_findings: cannot read {path}: {exc}", file=sys.stderr)
        return 1

    store = record_finding.store_path(path)
    try:
        rows = record_finding.read_rows(store)
    except (OSError, ValueError) as exc:
        print(f"prune_agent_findings: cannot read {store}: {exc}", file=sys.stderr)
        return 1
    try:
        pruned, kept, dropped = prune(text, keep_stages=keep_stages, rows=rows)
    except (re.error, RecursionError, MemoryError) as exc:
        print(f"prune_agent_findings: cannot parse {path}: {exc}", file=sys.stderr)
        return 1

    if not (kept or dropped) and _UNREADABLE.search(text):
        # Headings the parser cannot section. Exit 0 — the file is intact and that is the
        # right outcome — but say so, or a stale confirmed finding rides into this build's
        # report behind a clean-looking arm.
        print(
            f"prune_agent_findings: {path} has headings this parser cannot read, so nothing was "
            f"pruned — it may still carry the PREVIOUS build's confirmed findings",
            file=sys.stderr,
        )
        return 0
    if pruned == text:
        return 0

    # Same-directory temp + atomic replace, the house idiom (see gate_ledger.py).
    # The arm that calls this is followed immediately by the build that reads
    # the file, so a half-written findings file is worse than either version of it.
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(pruned, encoding="utf-8")
        # Carry the original's mode: mkstemp/create defaults are 0600, and silently
        # narrowing an artifact in the customer's repo makes the NEXT read fail.
        tmp.chmod(path.stat().st_mode & 0o7777)
        tmp.replace(path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"prune_agent_findings: cannot rewrite {path}: {exc}", file=sys.stderr)
        return 1

    # The typed rows follow their sections: a dropped section's row goes with it.
    live = {m.group(1) for m in _FINDING_ID.finditer(pruned)}
    if rows and set(rows) - live:
        try:
            record_finding.write_rows(store, {i: r for i, r in rows.items() if i in live})
        except OSError as exc:
            print(f"prune_agent_findings: cannot rewrite {store}: {exc}", file=sys.stderr)
            return 1

    # Say what moved. Without this the one state that matters — sections present, none of
    # them recognised as potential — is indistinguishable from a file that legitimately had
    # none, and the operator sees a clean arm over a file that just lost a section.
    print(f"prune_agent_findings: kept {kept}, dropped {dropped} confirmed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv))
