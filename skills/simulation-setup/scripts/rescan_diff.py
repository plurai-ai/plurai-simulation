#!/usr/bin/env python3
"""What a re-scan is about to DELETE or INVERT, surfaced for approval before the overwrite.

A re-scan re-authors `simulation_prd.md` and `scenario_variables.yaml` from scratch. Whatever
the previous scan (or a human, afterwards) learned and wrote down is not carried forward by
anything — it survives only if this roll of the same non-deterministic authoring happens to
say it again. The losses this guards against: a decline rule that comes back INVERTED, rules deleted
without a trace, and a whole enrichment section dropped.

This guard covers prose rules already on disk, so they cannot evaporate silently on a re-scan. It
also runs a check that needs no history at all: whether the NEW document contradicts ITSELF —
a rule against its own verbatim appendix, or against another section.

    rescan_diff.py --old plurai_sim/artifacts/simulation_prd.prev.md \\
                   --new plurai_sim/artifacts/simulation_prd.draft.md \\
                   [--old-vars OLD.yaml --new-vars NEW.yaml]

Exit 0 = nothing to approve · 1 = items need approval · 2 = usage / unreadable input.

The exit code is the whole contract: 1 means STOP, show the operator the list, and write only
what they approve. Rejecting an item means keeping the OLD text for that item — never
discarding the new document wholesale.

TWO TIERS. `dropped` and `inverted` (and the variables findings) GATE — they set exit 1, because
each is a comparison between two documents and a loss is decidable from it. `contradicted` is
ADVISORY: printed, never counted toward the exit code. It reads polarity out of prose inside ONE
document, where false findings are common, and a false finding blocks the flow — the
click-through trainer this guard exists to avoid. A check that cannot
be made precise does not get to hold a flow; it gets to leave a note.

PRECISION-FIRST, BY DESIGN — read this before widening anything. Exit 1 BLOCKS a flow and puts
a list in front of a human, so a finding on an ordinary rewording is not a harmless extra: it is
what teaches an operator (and a headless skill) to approve the list without reading it, and a
gate approved unread is worse than no gate. Reporting every
rule-shaped line whose replacement scored below the match threshold would call most legitimate
paraphrases dropped. So `dropped` is anchored on a NAMED SUBJECT: an old rule is reported only
when it carries a backticked span or a snake_case/dotted token that the new document no longer
names anywhere. That deliberately trades recall — a re-worded prohibition with no identifier in
it ("the agent must never promise a refund over $500") is not reported — for a list short enough
to be read. Widening this to every rule-shaped line is what the
threshold above is calibrated against.

KNOWN UNCAUGHT SHAPE — the decline pair below, in its full form. Do not read this guard as
covering it. A rule whose polarity flips only in its AUTHORING verb,
while a trailing clause about the agent's BEHAVIOUR dominates the line, is not detected:

    old: Never author a target that requires the agent to confirm, quote or reason from
         colour, material, dimensions or age range; grade the decline as correct.
    new: A target about colour, material, dimensions or age range is AUTHORABLE via
         `compare_products(attributes=['description'])`; a blanket decline is NOT correct.

The two lines MATCH (0.61, above `_MATCH`), so this is not a `dropped` item; but both collapse to
one polarity and both read -1, because `decline` is a `_NEGATIVE` token and negation wins the
line. The flip lives in clause 1 (-1 vs +1) and the clauses there align at only 0.565, below
`_MATCH`, so `_is_inverted` never compares them. Result: no finding.

Two fixes are rejected. (1) Compare the LEAD clause of each side
positionally, ignoring alignment: catches this pair and is clean on the five paraphrase probes,
but reports a rule whose clauses were merely SWAPPED as inverted ("the agent uses `get_order` …;
it never guesses an id" vs "the agent never guesses an order id; it uses `get_order` …" — 0.96
similar, opposite lead polarities, same rule). (2) Treat `decline`/`refuse` as agent behaviour
rather than rule polarity and add `correct` as a licence predicate: needs a vocabulary rewrite
whose own false-positive class ("`sku` is correct" vs "`sku` is missing" about one identifier)
has not been measured. Lowering the CLAUSE alignment threshold to 0.55 would catch this pair and keeps
the swapped-clause case correct (best-match alignment picks the right partner at any threshold) —
but that is a constant tuned on a single example, so it is not applied.

The simplified form of the same pair — no trailing behaviour clause — IS caught, and is what
`TestTheInvertedDeclineRule` pins. Do not let that test read as coverage of the shape above;
`test_the_full_form_shipped_pair_is_the_documented_blind_spot` pins the gap next to it.

Stdlib + pyyaml only.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(f'ERROR: pyyaml is required but not importable from {sys.executable}.', file=sys.stderr)
    sys.exit(2)


# --- Rule extraction ------------------------------------------------------------------

#: What a RULE sounds like. Wider than a directive check would be, because the question here is
#: "did the previous document commit to something?", and a commitment can be phrased as a
#: prohibition, a licence, or a flat capability claim.
_RULE_SHAPE = re.compile(
    r'\b(?:never|always|do not|don\'t|must|must not|cannot|can\'t|may not|no longer|'
    r'only|forbidden|banned|unsupported|not seedable|not supported|authorable|licen[cs]ed|'
    r'avoid|refuse|decline|require[sd]?|prohibit(?:ed)?|grade[sd]?)\b',
    re.IGNORECASE,
)

#: Polarity markers. A rule's meaning lives here, and it is exactly what flipped between the two
#: rolls of the same scan, so it is what gets compared rather than the sentence as a whole.
_NEGATIVE = re.compile(
    r'\b(?:never|not|no|cannot|can\'t|don\'t|do not|must not|may not|without|'
    r'forbidden|banned|unsupported|undocumented|broken|unavailable|absent|missing|empty|null|refuse|decline|prohibit(?:ed)?)\b',
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r'\b(?:authorable|licen[cs]ed|supported|available|documented|permitted|allowed|'
    r'works|returns|accepts|is able|can be|uses?|using|call|invoke|instruct(?:s|ed)?)\b',
    re.IGNORECASE,
)

_FENCE_OPEN = re.compile(r'^\s*(`{3,}|~{3,})')
_FENCE_BARE = re.compile(r'^\s*(`{3,}|~{3,})\s*$')
_APPENDIX_HEADING = re.compile(r'^#{1,6}\s+VERBATIM SOURCE APPENDIX\b', re.IGNORECASE)
_LIST_MARKER = re.compile(r'^(?:[-*+]|\d+[.)])\s+')
_MD_NOISE = re.compile(r'[*_`>]+')


def _fenced(lines: list[str]) -> list[bool]:
    """Per line: inside a fenced block? A fence closes on a bare run of the same character at
    least as long as its opener — the appendix wraps files whose own bytes contain fences."""
    inside: list[bool] = []
    opener: str | None = None
    for line in lines:
        if opener is None:
            match = _FENCE_OPEN.match(line)
            inside.append(bool(match))
            if match:
                opener = match.group(1)
            continue
        inside.append(True)
        close = _FENCE_BARE.match(line)
        if close and close.group(1)[0] == opener[0] and len(close.group(1)) >= len(opener):
            opener = None
    return inside


def _split_appendix(text: str) -> tuple[str, str]:
    """`(prose, appendix)`. The appendix is byte-copies of the customer's documents: it is never
    read as this document's rules, only as the authority a rule can contradict."""
    lines = text.splitlines()
    for i, (line, in_fence) in enumerate(zip(lines, _fenced(lines))):
        if not in_fence and _APPENDIX_HEADING.match(line):
            return '\n'.join(lines[:i]), '\n'.join(lines[i:])
    return text, ''


def _section_of(lines: list[str], index: int) -> str:
    for i in range(index, -1, -1):
        if lines[i].startswith('#'):
            return lines[i].lstrip('# ').strip()
    return '(preamble)'


def rules(text: str) -> list[tuple[str, str]]:
    """`(section, rule line)` for every rule-shaped prose line outside fences and the appendix."""
    prose, _ = _split_appendix(text)
    lines = prose.splitlines()
    found: list[tuple[str, str]] = []
    for i, (line, in_fence) in enumerate(zip(lines, _fenced(lines))):
        stripped = _LIST_MARKER.sub('', line.strip())
        if in_fence or not stripped or stripped.startswith(('#', '|')):
            continue
        if _RULE_SHAPE.search(stripped):
            found.append((_section_of(lines, i), stripped))
    return found


def _key(rule: str) -> str:
    return ' '.join(sorted(set(re.findall(r'[a-z0-9_]{3,}', _MD_NOISE.sub('', rule).lower()))))


def _polarity(rule: str) -> int:
    """-1 limiting · +1 licensing · 0 neither. Negation wins a tie: a sentence carrying both is
    usually a limit stated with an exception, and reading it as a licence is the wrong way to
    be wrong here."""
    if _NEGATIVE.search(rule):
        return -1
    return 1 if _POSITIVE.search(rule) else 0


# --- The three findings ---------------------------------------------------------------

#: How much of an old rule a new one must retain to count as the SAME rule re-stated. Tuned so a
#: rewritten sentence still matches its ancestor (which is what makes an inversion visible as an
#: inversion rather than as a deletion plus an unrelated addition).
_MATCH = 0.6

#: Sentence/clause boundary. A rule line routinely carries two claims ("never author X; grade the
#: decline as correct"), and a line collapses to ONE polarity — so the pair that flipped is
#: invisible at line granularity.
_CLAUSE = re.compile(r'[;.]\s+|[;.]$')


def _clauses(rule: str) -> list[str]:
    return [c.strip() for c in _CLAUSE.split(rule) if c and c.strip()]


def _best(key: str, candidates: list[tuple[str, object]]) -> tuple[object, float]:
    """`(payload, ratio)` of the candidate whose key is closest to `key`."""
    best, best_ratio = None, 0.0
    for candidate_key, payload in candidates:
        ratio = difflib.SequenceMatcher(None, key, candidate_key).ratio()
        if ratio > best_ratio:
            best, best_ratio = payload, ratio
    return best, best_ratio


def _is_inverted(old_rule: str, new_rule: str) -> bool:
    """Whether a matched pair came back meaning the opposite.

    Line granularity first. Then, for a multi-clause line, CLAUSE granularity: `_polarity`
    collapses a whole line to one sign and negation wins, so "never authorable; the decline is
    correct" and "authorable; the decline is not correct" both read as limits and the flip goes
    silent — which is the shape this guard exists for. Clause alignment runs ONLY on a pair the
    matcher already called the same rule, so it can add no `dropped` noise of its own.
    """
    if _polarity(old_rule) and _polarity(new_rule) and _polarity(old_rule) != _polarity(new_rule):
        return True
    old_clauses, new_clauses = _clauses(old_rule), _clauses(new_rule)
    if max(len(old_clauses), len(new_clauses)) < 2:
        return False
    candidates = [(_key(c), c) for c in new_clauses]
    for clause in old_clauses:
        mate, ratio = _best(_key(clause), candidates)
        if ratio < _MATCH or not isinstance(mate, str):
            continue
        if _polarity(clause) and _polarity(mate) and _polarity(clause) != _polarity(mate):
            return True
    return False


def compare(old: str, new: str) -> list[dict]:
    old_rules, new_rules = rules(old), rules(new)
    new_keys: list[tuple[str, object]] = [(_key(r), (section, r)) for section, r in new_rules]
    # Every identifier the NEW document still names, anywhere — appendix and fences included.
    # A subject the replacement still talks about has not been lost, whatever happened to the
    # sentence that used to carry it.
    surviving = _identifiers(new)
    findings: list[dict] = []
    for section, rule in old_rules:
        best, best_ratio = _best(_key(rule), new_keys)
        if best_ratio < _MATCH or not isinstance(best, tuple):
            lost = sorted(_identifiers(rule) - surviving)
            if not lost:
                continue
            findings.append({
                'kind': 'dropped', 'section': section, 'old': rule, 'new': None,
                'why': f'the new document states nothing like it and never names {", ".join(f"`{t}`" for t in lost)} '
                       f'again. Nothing else carries this — if it was learned the hard way, this overwrite is where '
                       f'it is lost.',
            })
            continue
        if _is_inverted(rule, best[1]):
            findings.append({
                'kind': 'inverted', 'section': section, 'old': rule, 'new': best[1],
                'why': 'the same rule came back with the opposite meaning. One of the two rolls disagrees with the '
                       'backend invariant, and the document cannot say which.',
            })
    return findings


#: An identifier the two halves of a contradiction share: a backticked span, or a bare
#: snake_case/dotted token. Ordinary words are deliberately excluded — a token has to be
#: specific enough that two sentences using it are talking about the same thing.
_IDENTIFIER = re.compile(r'`([^`\n]{3,60})`|\b([a-z][a-z0-9]*(?:[_.][a-z0-9]+)+)\b')


#: What a token has to LOOK like before two sentences can be said to share a subject.
#:
#: Backtick spans WRAP ACROSS LINES in a real document, and this reader works line by line — so
#: on the continuation line the closing backtick of one identifier pairs with the opening
#: backtick of the next, and the prose between them is captured as a "name" (observed on real
#: PRDs: `— six, in that order`, `, has no`). Shape is what rejects those, and the same check
#: drops what a technical PRD backticks constantly and is not a subject at all: quoted strings,
#: bare numbers, argument fragments (`min_length=2, max_length=3`), field lists.
#: A trailing `:LINE` or `:LINE-LINE` is KEPT: for the `dropped` gate the question is whether the
#: new document still names this subject, and a rule cited at `tools.py:466` is not carried by
#: the new document merely mentioning `tools.py` somewhere else. Only the extra entries of a
#: multi-location citation are trimmed — `tools.py:139, 197, 251` is one subject, not a list.
_IDENT_SHAPE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:[.\-/][A-Za-z0-9_]+)*(?::\d+(?:-\d+)?)?$')


def _identifiers(line: str) -> set[str]:
    found = set()
    for backticked, bare in _IDENTIFIER.findall(line):
        token = re.sub(r'\(.*$', '', (backticked or bare).strip()).strip()  # revert(target=…) -> revert
        token = re.sub(r'(:\d+(?:-\d+)?)\s*,[\s\d,-]*$', r'\1', token).strip()
        if len(token) >= 3 and _IDENT_SHAPE.match(token):
            found.add(token.lower())
    return found


def self_contradictions(text: str) -> list[dict]:
    """Where the new document disagrees with ITSELF about one named surface.

    Two shapes, both shipped: a rule that licenses a tool argument while another section calls
    the same argument undocumented, and a rule that bans a call the verbatim appendix instructs
    the agent to make. The auditor finds these too — one scenario slot at a time, after the
    generation spend."""
    prose, appendix = _split_appendix(text)
    claims: dict[str, list[tuple[str, int, str, frozenset[str]]]] = {}

    def collect(body: str, appendix_side: bool, where: str) -> None:
        lines = body.splitlines()
        for i, (line, in_fence) in enumerate(zip(lines, _fenced(lines))):
            stripped = _LIST_MARKER.sub('', line.strip())
            if not stripped or stripped.startswith(('#', '|')):
                continue
            # The appendix's bytes are INSIDE fences; the prose's claims are outside them.
            if in_fence is not appendix_side:
                continue
            # NOT restricted to rule-shaped lines: half of a contradiction is usually a flat
            # capability statement ("the description attribute is undocumented to the model"),
            # and requiring a modal verb on both sides missed exactly the pair that shipped.
            polarity = _polarity(stripped)
            if not polarity:
                continue
            tokens = frozenset(_identifiers(stripped))
            for token in tokens:
                claims.setdefault(token, []).append((where or _section_of(lines, i), polarity, stripped, tokens))

    collect(prose, appendix_side=False, where='')
    collect(appendix, appendix_side=True, where='the verbatim appendix')

    findings: list[dict] = []
    # One opposed PAIR is one finding. Two lines that share three identifiers used to be
    # reported three times, which is the same note wearing different subject lines.
    seen: set[tuple[str, str]] = set()
    for token, entries in sorted(claims.items()):
        negative = [e for e in entries if e[1] < 0]
        positive = [e for e in entries if e[1] > 0]
        if not (negative and positive):
            continue
        # Same section, both polarities is ordinary prose ("X is unavailable; Y returns it").
        if {e[0] for e in negative} == {e[0] for e in positive} and len({e[0] for e in entries}) == 1:
            continue
        pair = _about_the_same_thing(positive, negative)
        if pair is None:
            continue
        yes, no = pair
        if (yes[2], no[2]) in seen:
            continue
        seen.add((yes[2], no[2]))
        findings.append({
            'kind': 'contradicted', 'section': f'`{token}`', 'old': f'[{yes[0]}] {yes[2]}',
            'new': f'[{no[0]}] {no[2]}',
            'why': f'the document states `{token}` both ways. Generation reads one side and the judges read the '
                   f'other, and the disagreement surfaces per scenario, expensively.',
        })
    return findings


def _about_the_same_thing(positive: list[tuple], negative: list[tuple]) -> tuple[tuple, tuple] | None:
    """The first genuinely opposed pair, or None. Two tests.

    SAME OBJECT. Sharing a subject is not sharing a claim: `search_products` returns `title`
    beside `search_products` does not return `material` is an ordinary tool spec. Each side
    names something the other does not, and that is the tell — a real contradiction has no such
    distinguishing token. So they pair only when one side's identifiers CONTAIN the other's.

    ONE SIDE IS A RULE. A contradiction needs law on one side of it; two DESCRIPTIONS of the
    same identifier in different sections are complementary, which is what a large PRD is mostly
    made of. On the real 30.9 KB customer document the object test alone still produced four
    findings and not one was real — a tool spec beside a business rule, a constructor note
    beside a not-active note — and every one of them had NEITHER side rule-shaped, while both
    pairs this check was built for have exactly one (`revert` "must never be used" against the
    appendix that instructs it; a target "is authorable via" the attribute another section calls
    undocumented). Requiring a modal on BOTH sides was tried and misses both of those.
    """
    for yes in positive:
        for no in negative:
            if not (yes[3] <= no[3] or no[3] <= yes[3]):
                continue
            if _RULE_SHAPE.search(yes[2]) or _RULE_SHAPE.search(no[2]):
                return yes, no
    return None


# --- scenario_variables.yaml ----------------------------------------------------------

#: The list-of-named-things fields. A dropped NAME is the granularity that matters: a re-scan
#: that re-authors the file loses whole archetypes, and the file gives no other handle on them.
_NAMED_LISTS = ('typical_complications', 'typical_user_profiles', 'agent_tools', 'entity_schemas')


def compare_variables(old_text: str, new_text: str) -> list[dict]:
    try:
        old_doc = yaml.safe_load(old_text) or {}
        new_doc = yaml.safe_load(new_text) or {}
    except yaml.YAMLError as exc:
        return [{'kind': 'unreadable', 'section': 'scenario_variables.yaml', 'old': str(exc), 'new': None,
                 'why': 'the variables could not be parsed, so nothing was compared. Check the file by hand.'}]
    if not (isinstance(old_doc, dict) and isinstance(new_doc, dict)):
        return []

    findings: list[dict] = []
    for key in sorted(set(old_doc) - set(new_doc)):
        findings.append({'kind': 'dropped', 'section': 'scenario_variables.yaml', 'old': f'{key}: …', 'new': None,
                         'why': 'the key is gone from the new file. Every prompt that read it now reads nothing.'})
    for field in _NAMED_LISTS:
        old_names = _names(old_doc.get(field))
        gone = old_names - _names(new_doc.get(field))
        for name in sorted(gone):
            findings.append({'kind': 'dropped', 'section': f'scenario_variables.yaml {field}', 'old': name, 'new': None,
                             'why': 'the entry is absent from the new file — the re-scan re-authored the list and did '
                                    'not roll this one again.'})
    return findings


def _names(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item['name']) for item in value if isinstance(item, dict) and item.get('name')}


# --- CLI ------------------------------------------------------------------------------

_ICON = {'dropped': '🗑', 'inverted': '🔄', 'contradicted': '⚔', 'unreadable': '⚠'}


def _items(findings: list[dict], start: int = 1) -> list[str]:
    lines: list[str] = []
    for i, f in enumerate(findings, start):
        lines.append(f'{i}. {_ICON.get(f["kind"], "•")} {f["kind"].upper()} — {f["section"]}')
        lines.append(f'   old: {f["old"]}')
        if f['new']:
            lines.append(f'   new: {f["new"]}')
        lines.append(f'   why: {f["why"]}')
        lines.append('')
    return lines


def render(findings: list[dict]) -> str:
    return '\n'.join([
        f'{len(findings)} item(s) need approval before this re-scan overwrites the previous artifacts.',
        '',
        'Approving an item writes the NEW text. Rejecting it keeps the OLD text for THAT item —',
        'the rest of the new document lands either way.',
        '',
        *_items(findings),
    ])


def render_advisories(findings: list[dict]) -> str:
    """The non-gating tier. Printed, never counted toward the exit code."""
    return '\n'.join([
        f'{len(findings)} advisory note(s) — NOT an approval gate, and the overwrite is not held for them.',
        '',
        'Each is a place the NEW document may state one subject two ways. Reconcile if you agree;',
        'this tier reads prose polarity, so it is a prompt to look, never a verdict.',
        '',
        *_items(findings),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Surface what a re-scan would drop, invert or contradict.')
    parser.add_argument('--old', type=Path, required=True, help='the PRD currently on disk')
    parser.add_argument('--new', type=Path, required=True, help='the drafted replacement')
    parser.add_argument('--old-vars', type=Path, help='scenario_variables.yaml currently on disk')
    parser.add_argument('--new-vars', type=Path, help='the drafted replacement')
    args = parser.parse_args(argv)

    try:
        old_text = args.old.read_text(encoding='utf-8')
        new_text = args.new.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

    findings = compare(old_text, new_text)
    advisories = self_contradictions(new_text)
    if args.old_vars and args.new_vars:
        try:
            findings += compare_variables(args.old_vars.read_text(encoding='utf-8'), args.new_vars.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError) as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2

    if advisories:
        print(render_advisories(advisories))
    if not findings:
        print('✓ nothing this overwrite would drop or invert — safe to write.')
        return 0
    print(render(findings))
    return 1


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
