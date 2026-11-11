#!/usr/bin/env python3
"""Client-side structure check for a PRD before it is uploaded (Step 9b).

Two independent checks, each mirroring a place where the BACKEND ROUTES on structure and
degrades silently when the structure is wrong:

1. **The VERBATIM SOURCE APPENDIX parse.** Ports the backend parser (the backend
   ``factories/source_fidelity.py`` — ``split_verbatim_appendix`` / ``_parse_appendix_entries``)
   so a malformed appendix is caught before upload, not at judge time. The backend does not
   degrade on format errors — it silently DROPS documents (each drop is a "lost label") and
   cancels the ``(COMPLETE)`` closed-set assertion. This script prints per-entry results, every
   lost label with its cause, and whether ``(COMPLETE)`` survives.

2. **The ``## Scenario design constraints`` heading.** The backend routes that section to the
   generator as DIRECTIVES and keeps it out of the rubric auditor's fact surface. Routing is by
   the heading itself, and the backend is tolerant about FORM (heading level, a single-level
   section number, hyphen/underscore joins, singular, a trailing colon, any case) and strict about
   WORDING. A heading it will NOT route — reworded, or numbered at more than one level — leaves
   the section in the fact surface, where its directives are judged as world facts ("family A
   never mentions links" read as "links do not exist"). Nothing downstream reports that, so such
   a near-miss FAILS here; a routed-but-non-canonical spelling is only a nudge.

Parsing contract (mirrored exactly):

- The appendix heading is ``VERBATIM SOURCE APPENDIX`` at any heading level, with an
  optional ``(COMPLETE)`` token on the same line. The appendix runs to the next
  same-or-shallower heading or EOF — it must be the LAST section of the PRD.
- Each entry is a DEEPER sub-heading (its text is the document's label) followed by one
  fenced block (3+ backticks or tildes). A fence closes only on a bare run of the SAME
  character AT LEAST as long as the opener; a LONGER closing run means the document's own
  bytes contained a fence the wrapper did not clear (entry lost — wrap in a longer fence).
- Lost-label causes: a sub-heading with no fenced block; an unclosed fence; an inner
  fence >= the wrapper closing it early; stray unfenced text between entries.
- Any lost label cancels ``(COMPLETE)``; so does a zero-entry appendix.

Also prints a largest-first map of where the characters sit in the PROSE half — the default
view, and structural orientation only: there is no size target to author toward.

``--facts-hash`` is a third, non-reporting mode: it prints the sha256 of the PRD's FACTS
PROJECTION (the document minus the routed design-constraints span) and nothing else, so env
staleness can be computed over the half env generation actually consumes. It lives here
because the span parser above is the one implementation of the backend's routing contract.

Exit codes: 0 clean — which includes "no appendix" (Step 9b legitimately does not apply to
every agent) and "no design-constraints section" (a PRD may have no generator directives);
1 lost labels, an appendix heading that yielded no entries, or a near-miss
``## Scenario design constraints`` heading; 2 usage/read error.

Stdlib-only on purpose — it runs in the CUSTOMER's environment.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Byte-for-byte the backend's regexes (source_fidelity.py).
APPENDIX_HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s+VERBATIM\s+SOURCE\s+APPENDIX\s*(\(\s*COMPLETE\s*\))?\s*$', re.IGNORECASE)
HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s+(\S.*?)\s*$')
FENCE_RE = re.compile(r'^\s{0,3}(`{3,}|~{3,})\s*(\S*)\s*$')


def closes_fence(fence_m: re.Match[str], opener: str) -> bool:
    """Whether this fence line CLOSES the open fence ``opener`` (the backend's rule, one place).

    A fence closes only on a bare run (no info string) of the SAME character at least as
    long as the opener. Every scanner below depends on this identically, and a divergence
    between them would silently change which entries parse.
    """
    return fence_m.group(1)[0] == opener[0] and len(fence_m.group(1)) >= len(opener) and not fence_m.group(2)


class AppendixSpan(NamedTuple):
    """Where the appendix sits in the PRD (``depth is None`` means no appendix found)."""

    depth: int | None
    start: int
    end: int
    declared_complete: bool


class AppendixReport(NamedTuple):
    """What the parse registered, what it lost, and how ``(COMPLETE)`` fared."""

    entries: list[tuple[str, str]]
    lost: list[str]
    found: bool
    declared_complete: bool
    #: The PRD with the appendix section cut out — the backend's derived text. This
    #: is the half the auditor reads as prose and the half the design-constraints routing looks
    #: at, so both the heading check and the section map run over it.
    derived_text: str

    @property
    def complete_survives(self) -> bool:
        # The backend's rule verbatim: COMPLETE survives only a non-empty, loss-free appendix.
        return self.declared_complete and bool(self.entries) and not self.lost


def find_appendix(lines: list[str]) -> AppendixSpan:
    """Locate the appendix heading and its extent.

    Fence-aware exactly like the backend: a heading inside a fenced block (e.g. the
    PRD's own code examples) never opens or closes the appendix.
    """
    fence: str | None = None
    depth: int | None = None
    start = end = len(lines)
    complete = False
    for i, line in enumerate(lines):
        fence_m = FENCE_RE.match(line)
        if fence is not None:
            if fence_m is not None and closes_fence(fence_m, fence):
                fence = None
            continue
        if fence_m:
            fence = fence_m.group(1)
            continue
        if depth is None:
            m = APPENDIX_HEADING_RE.match(line)
            if m:
                depth = len(m.group(1))
                complete = m.group(2) is not None
                start, end = i, len(lines)
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) <= depth:
            end = i
            break
    return AppendixSpan(depth, start, end, complete)


def parse_entries(body: list[str], appendix_depth: int) -> tuple[list[tuple[str, str]], list[str]]:
    """(entries, lost) — ``(label, text)`` per fenced file, and what did not parse (with why)."""
    entries: list[tuple[str, str]] = []
    lost: list[str] = []
    pending: str | None = None
    fence: str | None = None
    captured: list[str] = []
    stray = False

    for line in body:
        fence_m = FENCE_RE.match(line)
        if fence is not None:
            if fence_m is not None and closes_fence(fence_m, fence):
                if pending is not None:
                    if len(fence_m.group(1)) > len(fence):
                        # A LONGER run closing a shorter opener: the document's own bytes
                        # contained a fence the wrapper did not clear. CommonMark ends the
                        # block here, so the rest of the file is cut off and rescanned as
                        # appendix structure. The convention is to wrap a fence-bearing
                        # document in a LONGER fence.
                        lost.append(f'{pending} (a fence in the document body closed the wrapper early — wrap the file in a LONGER fence)')
                    else:
                        entries.append((pending, ''.join(captured)))
                pending, fence, captured = None, None, []
            elif pending is not None:
                captured.append(line)
            continue
        if fence_m:
            # Fence state is tracked even with no pending label, so an unlabelled block's
            # markdown headings can never be mistaken for the next file boundary.
            fence = fence_m.group(1)
            continue
        heading = HEADING_RE.match(line)
        if heading and len(heading.group(1)) > appendix_depth:
            if pending is not None:
                lost.append(f'{pending} (no fenced block followed the sub-heading)')
            pending = heading.group(2).strip()
            continue
        if line.strip() and pending is None and entries:
            # Bare text outside every entry, AFTER at least one file parsed: the tail of a
            # document that was cut short. Prose BEFORE the first entry is left alone (an
            # introductory sentence under the appendix heading is legitimate).
            stray = True
    if fence is not None:
        lost.append(f'{pending or "<unlabelled block>"} (the fenced block was never closed)')
    elif pending is not None:
        lost.append(f'{pending} (no fenced block followed the sub-heading)')
    if stray:
        lost.append('<unfenced text between entries> (a document was cut short, or an entry is missing its sub-heading)')
    return entries, lost


def check(prd_text: str) -> AppendixReport:
    """Parse the PRD's appendix into an :class:`AppendixReport`."""
    lines = prd_text.splitlines(keepends=True)
    span = find_appendix(lines)
    if span.depth is None:
        return AppendixReport([], [], False, False, prd_text)
    entries, lost = parse_entries(lines[span.start + 1 : span.end], span.depth)
    derived = ''.join(lines[: span.start]) + ''.join(lines[span.end :])
    return AppendixReport(entries, lost, True, span.declared_complete, derived)


# The JUDGE-TIME INJECTION channel, which is not the auditor's grounding channel and has its own
# arithmetic: a source document <=8KB registers as ONE all-or-nothing unit; a bigger one is split
# at its own markdown headings. At judge time units are included in order until the SHARED 24KB
# injection budget is spent (aggregate across appendix + staged + corpus units, cited first), and
# whatever no longer fits is SKIPPED whole — so a big entry costs its neighbours, not only itself.
#
# THIS IS A COPY of the backend's `UNIT_MAX_BYTES`, in another repo, with no shared source. The backend
# returns the real numbers on every stash, so prefer RELAYING those; treat this as the offline
# fallback for a setup run with no backend reachable yet.
_UNIT_WHOLE_BYTES = 8 * 1024

# The heading the backend ROUTES on. Spelling is the whole contract: the routed section is offered
# to the generator as directives and withheld from the auditor's fact surface, and a heading that
# does not match routes nowhere.
_DESIGN_HEADING_TEXT = 'Scenario design constraints'
_DESIGN_HEADING_DEPTH = 2
#: What the BACKEND actually routes on — deliberately tolerant about form (any heading depth, an
#: optional section number, hyphen/underscore joins, singular, a trailing colon, any case) and
#: strict about wording. Mirrored from ``scenario_target_audit._DESIGN_CONSTRAINTS_TEXT_RE``.
#: This check FAILS only on a heading the backend will NOT route; a routed heading that merely
#: differs from the canonical spelling is a nudge, never an error. Failing one the backend accepts
#: would be this script inventing a stricter contract than the thing it is a pre-flight for.
_DESIGN_ROUTED_RE = re.compile(
    r'^(?:\d+[a-z]?[.)][ \t]*)?scenario[ \t_-]+design[ \t_-]+constraints?[ \t]*:?[ \t]*$',
    re.IGNORECASE,
)
#: Content stems of the canonical heading. A heading sharing two of the three is close enough that
#: it was almost certainly MEANT to be this section — which is exactly the dangerous case.
_DESIGN_STEMS = frozenset({'scenario', 'design', 'constraint'})
_DESIGN_FILLER = frozenset({'a', 'an', 'and', 'for', 'in', 'of', 'on', 'the', 'to'})
#: Leading enumeration a numbered PRD adds (`1.`, `1b.`, `(2)`, `A)`, `§3`) — stripped before
#: comparing, because `## 1b. Scenario design constraints` is a near-miss, not a different section.
_ENUM_PREFIX_RE = re.compile(r'^(?:§\s*)?[(\[]?\d+[a-z]?[.):\]]?\s+|^[(\[]?[a-z][.)\]]\s+', re.IGNORECASE)


def headings(text: str) -> list[tuple[int, int, str, str]]:
    """(line index, depth, title, raw line) for every heading OUTSIDE a fenced block.

    Fence-aware for the reason the appendix scan is: a heading inside one of the PRD's own code
    samples is neither a section boundary nor a routed heading.
    """
    fence: str | None = None
    found: list[tuple[int, int, str, str]] = []
    for i, line in enumerate(text.splitlines(keepends=True)):
        fence_m = FENCE_RE.match(line)
        if fence is not None:
            if fence_m is not None and closes_fence(fence_m, fence):
                fence = None
            continue
        if fence_m:
            fence = fence_m.group(1)
            continue
        if m := HEADING_RE.match(line):
            found.append((i, len(m.group(1)), m.group(2).strip(), m.group(0).strip()))
    return found


def _stems(title: str) -> set[str]:
    """A heading's content words: numbering dropped, lowercased, de-pluralized, filler removed."""
    words = re.findall(r'[a-z]+', _ENUM_PREFIX_RE.sub('', title.strip()).lower())
    return {w[:-1] if w.endswith('s') and len(w) > 3 else w for w in words} - _DESIGN_FILLER


class DesignHeadingCheck(NamedTuple):
    """How the PRD spells the routed design-constraints heading.

    ``status`` is ``recognized`` (canonical spelling), ``routed`` (the backend routes it, but it
    is not the spelling the template mandates), ``near-miss`` (clearly meant to be this section
    and will NOT route — the failure) or ``absent`` (no such section at all).
    """

    status: str
    heading: str = ''


def check_design_heading(prose: str) -> DesignHeadingCheck:
    """Whether the PRD carries the routed ``## Scenario design constraints`` heading.

    Runs over the PROSE half only — the routed section lives there; a heading inside the verbatim
    appendix is the customer's own document bytes and routes nothing.

    A near-miss is reported ONLY when the exact heading is absent: a `###` sub-heading repeating
    the words INSIDE a correctly-spelled section is a subdivision, not a mis-spelling, and failing
    on it would punish a PRD that is already right.
    """
    near: str = ''
    routed: str = ''
    for _i, depth, title, raw in headings(prose):
        if _DESIGN_ROUTED_RE.match(title):
            if depth == _DESIGN_HEADING_DEPTH and title == _DESIGN_HEADING_TEXT:
                return DesignHeadingCheck('recognized', raw)
            routed = routed or raw
            continue
        stems = _stems(title)
        if not near and len(stems & _DESIGN_STEMS) >= 2 and len(stems) <= 6:
            near = raw
    if routed:
        return DesignHeadingCheck('routed', routed)
    return DesignHeadingCheck('near-miss', near) if near else DesignHeadingCheck('absent')


def design_constraints_span(text: str) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of the routed design-constraints section, or ``None``.

    The FIRST routed heading wins — that is the one the backend routes — and the section runs
    to the next heading of the same or shallower depth, or EOF. Fence-aware through
    :func:`headings`, so a design heading inside one of the PRD's own fenced blocks (or inside
    a verbatim appendix entry) opens nothing.
    """
    marks = headings(text)
    for idx, (i, depth, title, _raw) in enumerate(marks):
        if not _DESIGN_ROUTED_RE.match(title):
            continue
        end = len(text.splitlines(keepends=True))
        for j, d, _t, _r in marks[idx + 1 :]:
            if d <= depth:
                end = j
                break
        return (i, end)
    return None


def facts_projection(text: str) -> str:
    """The PRD MINUS the routed design-constraints span — the document's FACT half.

    Deliberately the whole document minus that one section: the VERBATIM SOURCE APPENDIX bytes
    stay IN, because they are source facts the mock is built from. What comes out is only the
    span the backend routes away from the fact surface — generator directives, which env
    generation has no use for.
    """
    span = design_constraints_span(text)
    if span is None:
        return text
    lines = text.splitlines(keepends=True)
    return ''.join(lines[: span[0]]) + ''.join(lines[span[1] :])


def facts_hash(text: str) -> str:
    """sha256 of :func:`facts_projection` — the fingerprint a MOCK's staleness is computed over.

    A steering-only PRD edit leaves this hash unchanged, so it does not mark the mock stale and
    buy a 10-25 minute env regeneration. It is deliberately NOT the fingerprint for scenarios or
    evals: backend eval generation reads the whole PRD, directives included, so those keep the
    full-file hash and do go stale on a directives edit.
    """
    return hashlib.sha256(facts_projection(text).encode('utf-8')).hexdigest()


def design_heading_line(check_result: DesignHeadingCheck) -> str:
    """The one-line verdict on the routed heading, printed in every state."""
    if check_result.status == 'recognized':
        return (
            f'verify_prd_appendix: ✓ `## {_DESIGN_HEADING_TEXT}` present — the backend routes that section to the '
            'generator as directives and keeps it out of the auditor\'s fact surface (so a FACT written there is '
            'unenforceable).'
        )
    if check_result.status == 'routed':
        return (
            f'verify_prd_appendix: ✓ `{check_result.heading}` routes (the backend matches this wording) — '
            f'`## {_DESIGN_HEADING_TEXT}` is the spelling the template mandates, if you want the canonical form.'
        )
    if check_result.status == 'near-miss':
        return (
            f'verify_prd_appendix: ✗ `{check_result.heading}` is NOT the routed heading — write it exactly as '
            f'`## {_DESIGN_HEADING_TEXT}` (no numbering, no rewording). As spelled, the section stays in the '
            'auditor\'s fact surface and its directives are judged as world facts.'
        )
    return (
        f'verify_prd_appendix: no `## {_DESIGN_HEADING_TEXT}` section — fine when the PRD has no generator '
        'directives; a design rule written anywhere else is read as a world fact.'
    )


def section_sizes(text: str) -> list[tuple[str, int]]:
    """Every heading's own span — heading line through the next heading of ANY level — largest first.

    Non-overlapping, so the sizes sum to the document. A nested breakdown would count a parent
    against its children and mis-rank where the mass actually sits.
    """
    lines = text.splitlines(keepends=True)
    marks = [(i, raw) for i, _depth, _title, raw in headings(text)]
    if not marks:
        return []
    out: list[tuple[str, int]] = []
    if marks[0][0]:
        out.append(('(preamble)', len(''.join(lines[: marks[0][0]]))))
    for (start, title), (nxt, _) in zip(marks, [*marks[1:], (len(lines), '')]):
        out.append((title, len(''.join(lines[start:nxt]))))
    return sorted(out, key=lambda kv: -kv[1])


def injection_notes(report: AppendixReport) -> list[str]:
    """Advisory lines about the judge-time INJECTION channel. Empty when every entry registers whole.

    Warnings only — an oversized entry still registers; the cost is that it stops being one
    all-or-nothing unit, which is exactly the kind of thing that must be said out loud."""
    notes: list[str] = []
    for label, body in report.entries:
        if (nbytes := len(body.encode('utf-8'))) > _UNIT_WHOLE_BYTES:
            notes.append(
                f'  ! {label} is {nbytes} bytes (>{_UNIT_WHOLE_BYTES}) — the backend splits it at its markdown headings '
                f'instead of registering it whole, and an over-budget piece is skipped entirely at judge time. '
                f'Prefer several small real files over one concatenated blob.'
            )
    return notes


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # `--sections` is the DEFAULT view and is accepted only so existing invocations keep working.
    facts_only = '--facts-hash' in args
    positional = [a for a in args if a not in ('--sections', '--facts-hash')]
    if len(positional) != 1:
        print('usage: verify_prd_appendix.py [--sections | --facts-hash] <simulation_prd.md>', file=sys.stderr)
        return 2
    prd_path = Path(positional[0])
    try:
        # read_BYTES, not read_text: read_text applies universal-newline translation, so a CRLF
        # document is measured one char short PER LINE — while `plurai stash-doc` ships the file's
        # bytes unchanged and the backend parses the bytes it receives. Measuring a different
        # document than the backend parses is how this check goes green on a PRD the backend reads
        # differently. (read_text(newline='') is 3.13+; this runs in a customer's env.)
        text = prd_path.read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError — without this it would
        # traceback, and the shell exit would alias a tool crash to "lost labels".
        print(f'verify_prd_appendix: cannot read {prd_path}: {exc}', file=sys.stderr)
        return 2

    if facts_only:
        # stdout carries the hash and NOTHING else, so it is assignable (`H=$(… --facts-hash …)`)
        # and every caller reads the same projection this script's span parser defines.
        print(facts_hash(text))
        return 0

    report = check(text)
    # `or text`: a PRD that is nothing but an unparseable appendix has an EMPTY derived half, and
    # mapping that would print "no headings" for a document that plainly has some.
    prose = report.derived_text or text

    design = check_design_heading(prose)
    print(design_heading_line(design))
    # A near-miss heading is a FAILURE for the same reason a lost label is: the document looks
    # right, the backend routes it somewhere else, and nothing downstream says so.
    failures = [] if design.status != 'near-miss' else [f'`{design.heading}` is not the routed design-constraints heading']

    if not report.found:
        print(f'verify_prd_appendix: {prd_path} has no VERBATIM SOURCE APPENDIX heading — nothing to check (fine when Step 9b does not apply).')
    else:
        print(f'verify_prd_appendix: {prd_path}')
        for label, body in report.entries:
            print(f'  ✓ {label} ({len(body.encode("utf-8"))} bytes registered)')
        for line in injection_notes(report):
            print(line)
        for reason in report.lost:
            print(f'  ✗ LOST: {reason}')
        if not report.entries and not report.lost:
            print('  ✗ the appendix heading parsed but yielded NO entries — every document is missing')

        if report.declared_complete:
            if report.complete_survives:
                print('  (COMPLETE) declared and SURVIVES — judges may assert "the sources do not mention X".')
            else:
                print('  (COMPLETE) declared but CANCELLED — a lost/empty appendix can never be a closed world. Fix the entries or drop the token.')
        else:
            print('  no (COMPLETE) declared — sources register without negative authority.')

        if report.lost or not report.entries:
            why = f'{len(report.lost)} lost label(s)' if report.lost else 'an appendix with no entries'
            failures.append(f'{why}; the backend would register only {len(report.entries)} document(s)')
        else:
            print(f'verify_prd_appendix: ✓ all {len(report.entries)} document(s) parse; nothing lost.')

    # The map trails the verdicts: it is orientation, not a finding — where the prose mass sits,
    # in raw characters. There is no size target, so no number here is a pass or a fail.
    rows = section_sizes(prose)[:12]
    if rows:
        print('  largest prose sections (heading through the next heading):')
        for title, size in rows:
            print(f'    {size:>7,}  {title}')
    else:
        print('  (no markdown headings to map — the prose is one unbroken span.)')

    if failures:
        print(f'verify_prd_appendix: FAILED — {"; ".join(failures)}.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
