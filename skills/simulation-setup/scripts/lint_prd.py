#!/usr/bin/env python3
"""Mechanical checks over a drafted `simulation_prd.md`.

The PRD is the only input carrying the agent's domain ground truth to scenario generation
AND to every judge, so a defect in it is authored into the suite and then graded against.
The expensive way to find one is a scored run and a review pass.

Eight groups of checks, all cheap. Every finding is warn-tier: it does not fail the run, but it stays OPEN
(exit 4) until the PRD is fixed or the finding is accepted with a recorded reason (`--accept`, below). A clean PRD
exits 0. The typed errors exit 1 instead: typed placement (group 1's error
tier) — a typed fact in a section the auditor never reads is not a matter of judgement, it is a
fact that grounds nothing — and fact identity (group 5's) — two facts sharing a `subject` and a
`predicate`, which the scan contract reports as two verdicts under one id (`duplicate_facts`, the
rule's one owner; `scan_facts.py` calls it). A third error, group 8, is a self-contradiction inside the
document rather than a typed fact.

Exit codes: 0 clean (or every finding accepted) · 1 an error · 2 a usage error or a refused `--accept` ·
3 the PRD or `--config` cannot be read · 4 findings are open. The bench manifest maps 4 to `not_verified`, so
a PRD with open findings is never recorded as `verified`.

An open finding is closed by editing the PRD, or by `--accept <fp> --reason "<what you verified>"` where `<fp>`
is the `[fp:xxxxxxxx]` that ends the finding's line. The acceptance is a `plugin.prd_lint.open_finding` overrule in the
gate ledger (`scripts/gate_ledger.py`); it binds to the finding's text, so rewording the finding lapses it.

1. **Placement — section-typed.** The auditor's fact surface is the PRD minus the sections
   ROUTED OUT of it (`AUDITOR_ROUTED_OUT`: `## Scenario design constraints`, routed to the
   generator as steering), and the lint + the scan contract read the `## Agent facts` block only
   BEFORE the verbatim appendix. So the typed facts have exactly one place to be, and a facts
   block anywhere else is an ERROR naming the section it sits in: inside the routed-out span
   (depth-aware — a `### Agent facts` under the design heading is inside it), or after the
   appendix heading (where `_without_appendix` cuts and the block reads as SECTION ABSENT).
   Two world facts once sat in the directives section where the auditor never reads them, and
   the loop paid a final-batch regeneration for it. Beside that, the PROSE tier stays
   advisory: a sentence in the directives section that carries a figure and no imperative is a
   census stated as prose — a heuristic over sentence shape, hence warn-tier, with the remedy
   now the typed channel (write it as an Agent fact) rather than "move the sentence". Known blind
   spot: a world fact carrying an incidental steering word is exempt. The mirror direction — a
   generator directive parked OUTSIDE the section — was built and removed: across three real
   PRDs it produced no true findings, while flagging the authoring guidance in this plugin's own
   PRD template, which no check can tell apart from a directive to the generator.
2. **Completeness.** Tools the config declares — and the RENDER ACTIONS it declares under
   `render_actions:` — that no PRD section mentions. Fully mechanical — the config names them,
   the text either contains them or does not. Advisory: documenting one as out of scope is a
   valid disposition.
2b. **The render surface.** A render-action agent declares `tools: {}` by design, so its
   actions lived nowhere typed and no completeness check could see them: an agent inventing a
   `show_video` it does not have was graded by nobody. `render_actions:` in plurai.config.yaml is
   the typed surface (`verify_config.py` shape-checks it here as a pre-flight for a real consumer —
   its `_KNOWN_ROOT_KEYS` note names who reads the key and how they refuse it), and the PRD CLOSES it with one
   `tool_surface` / `RENDER_ACTIONS` / `omits` fact whose absence `check` names the verbs the
   agent does NOT have. Checked both ways: a declared surface with no closing fact, a closing fact
   over an undeclared surface, a closing fact with no `check:` (prose only), and a closing fact
   whose sweep names a verb the declaration says the agent HAS.
3. **Citation resolution.** A `` `path/to/file.py:LINE` `` citation whose file does not exist,
   or whose line is past the end of that file. EXISTENCE-ONLY, deliberately: whether the cited
   lines SAY what the claim says is not mechanically decidable (see below), but whether they
   exist is. It never fires on a closed-source behaviour — that carries `(inferred)` and no
   citation, which is the whole point of the marker. A `file.py:NNN` written WITHOUT backticks is
   reported as broken too: nothing reads it as a citation, so its claim passes as uncited.
4. **Agent-derived law.** A target-shape rule ("never author a target requiring X", "X is
   authorable via Y") or a grading rule ("grade the decline as correct") anywhere outside the
   verbatim appendix. Such a rule is the backend's generic invariant re-stated in one agent's
   vocabulary by a process that can state it either way round — the same repo scanned twice has
   produced opposite law. Two narrow shapes only, because the class is decidable exactly where
   the giveaway verbs are.
5. **Facts grammar.** The `## Agent facts` block, against the closed `kind` / `predicate`
   vocabulary in `references/agent_facts.md` — an unknown token, a missing `source`, or
   `evidence` written as an instruction. Absence of the section is reported in the status line,
   not as a finding.
6. **Mechanical claims** — the three shapes that NAME their own falsifier, so checking them
   needs no reading: a block declared a byte-exact copy of a named file whose bytes are not in
   that file; a whole-file negative ("no `try/except` anywhere in the file") the file refutes;
   and one counted noun phrase given two different counts in the same document. All three
   shipped false in one scan-authored PRD and cost ~45 minutes of mid-loop authoring; the
   third's residue is still in that document, because the correction fixed one of its two sites.
7. **Rule coverage.** `instruction_sources:` beside `facts:` declares where the
   agent's instructions live; every line in those ranges is cited by a `rule` fact or declined by
   a typed `unrowed` range, and a `rule` citing outside every range is reported (a tool description
   is `tool_surface`). The backend loads a PRD with an unrowed line — the rule simply has no row
   to own it — so this is an open finding, never an error. SKIPPED when the key is absent, with a
   warning when `rule` facts exist — `check_rule_coverage`. A line of template slots only closes only with a
   `filled-by` decline (the declared range that fills it) or a `data-slot` decline (the line that fills it).
8. **Input denial — error.** A `## Scenario design constraints` paragraph denying the simulated user
   the file channel ("hands over no file attachments") while an `## Agent facts` entry quotes the
   agent taking a user's files ("input files the user mentions") — `check_input_denials`.
9. **Seedability of state the simulation writes.** A `not_seedable` fact on a field of the config's
   `agent.state_class`: the harness writes that state in `seed_state`; or on a declared world type
   (`world_state.entity_types` / `owned_types`): the scenario seeds its rows — `check_state_seedability`.
10. **The claim audit's refutations.** Whether a claim is TRUE is a reading, not a lint; the reading is done
   by the independent Step-9c auditor over every site `claim_sites.py` lists, and its REFUTED rows
   (`artifacts/prd_claim_audit.jsonl`) are open findings here — `check_claim_audit`. A missing audit is
   `claim audit: not run` in the status line.

**This is a BACKSTOP, not the defense.** The Step-9 instruction span is what stops the law being
written: a headless run carrying it produced twelve typed facts and no law, while the same run
without it spontaneously authored grading law.

**LINE-SCOPED MATCHING IS THE RECURRING HAZARD HERE.** It has been wrong twice: once in
`rescan_diff.py`, where a backticked identifier wrapped and one name's closing backtick paired
with the next name's opening backtick, turning the prose between them into a "name"; and once
here, where the declarative law below wraps mid-sentence so no single line holds both halves.
Before adding a pattern, ask whether the thing it matches can wrap — and if it can, read
`_paragraphs`, not lines.

**On the exemption that was considered and REFUSED.** The declarative check fires on a
retail PRD's "Correct behaviour is to recognise the rows do not match and say so, rather than
present them as the answer". That was nearly spared as narrative. It is not: it is a grading rule
derived from this agent's data, and it decomposes exactly the way the doctrine prescribes — the
FACT is `tool_surface` / `search_products` / `matching_semantics` ("literal token overlap; can
return rows that do not answer the request", which re-derives stably), and the INVARIANT (don't
present non-matching rows as the answer) is generic honesty the auditor already holds on every
agent. Two scans of that same measurement could as easily produce "presenting the closest rows is
acceptable when nothing matches" — the same nondeterminism, in the same place. Do not widen an
exemption back over it. That a finding is not an error is what makes the
trade right: a borderline line costs one sentence of author attention (a fix, or an accepted reason), and
missing derived law costs a dead scenario slot.

**What is deliberately NOT here.** A quote-provenance check (does quoted text match a
registered source) was built and removed. It misfired four independent ways on real PRDs —
appendix fences split by their own markdown headings, smart apostrophes against ASCII
sources, hard-wrapped quotes, and bare verbs read as attributions — and, decisively, the
defect class it was built for is not quote-shaped: the PRD that carried it stated an
interpretation in unquoted prose while its appendix held the correct bytes. Whether prose is
a faithful reading of a source is not mechanically decidable. That class is handled by the
authoring rules instead — quote instruction text rather than restate it, and mark a derived
behaviour `(inferred)`.

Stdlib + pyyaml only (plus the plugin's own `scripts/gate_ledger.py`, stdlib, for the accept ledger).

Usage:
    lint_prd.py plurai_sim/simulation_prd.md --config plurai_sim/plurai.config.yaml
    lint_prd.py plurai_sim/simulation_prd.md --config ... --accept <fp> --reason "<what you verified>"
    lint_prd.py plurai_sim/simulation_prd.md --config ... --accept-from accepts.txt   # one `<fp> <reason>` per line
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(f'ERROR: pyyaml is required but not importable from {sys.executable}.', file=sys.stderr)
    sys.exit(3)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import gate_ledger  # noqa: E402 - the plugin's one finding contract and ledger
import measured_candidates  # noqa: E402 - the sweep `scaffold_rule_facts.py --profile` proposes facts from

# ONE normalization of the routed heading, owned by the sibling that mirrors the backend's own
# routing regex (`scenario_target_audit._DESIGN_CONSTRAINTS_TEXT_RE`). A second spelling here
# routed `## 1b. Scenario design constraints`, `Scenario_design_constraints` and the singular
# differently from the backend, and read `— draft` as the routed section.
from verify_prd_appendix import _DESIGN_ROUTED_RE, _ENUM_PREFIX_RE  # noqa: E402

DESIGN_HEADING = '## Scenario design constraints'

#: The exit code of a run that leaves findings open. 0 stays clean, 1 an error, 2 a usage error, 3 an unreadable input.
OPEN_FINDINGS_EXIT = 4
#: Leading hex digits of a finding's fingerprint, printed as `[fp:xxxxxxxx]` and accepted by `--accept`.
FP_SHOWN = 8

#: The leading words of the two findings that mean the facts block is unreadable (not YAML, or not a `facts:` mapping).
UNREAD_FACTS_PREFIX = 'facts: the "## Agent facts" block'
#: A PRD without the typed block records no machine-derived specifics: an open finding, so the block is never forgotten.
ABSENT_FACTS_FINDING = (
    'facts: the PRD has no `## Agent facts` block, so nothing typed is left for the auditor and the scan contract to check. '
    'Add the block before the verbatim appendix: `scaffold_rule_facts.py` writes the `instruction_sources` and `rule` facts '
    'and `references/prd_authoring_contract.md` shows the rest.'
)

#: The sections the grounding auditor does NOT read — everything else in the PRD is its fact
#: surface. One entry today, matched by the backend's own routing regex (`_is_design_heading`);
#: the point of naming it as a vocabulary is that placement is decided by SECTION MEMBERSHIP (see
#: `check_facts_placement`), never by the shape of a sentence.
AUDITOR_ROUTED_OUT = ('scenario design constraints',)

#: The plurai.config.yaml key that declares the render / action surface, and the RESERVED
#: `subject` the PRD's closing fact uses for it. Two names for one surface: the config lists what
#: the agent HAS, the fact (predicate `omits`, kind `tool_surface`) states what it has NOT.
RENDER_ACTIONS_KEY = 'render_actions'
RENDER_SURFACE_SUBJECT = 'RENDER_ACTIONS'

# What a generator DIRECTIVE sounds like. Used only to tell a directive from a bare
# statement of fact — never to judge the directive's content. Matched ANYWHERE in the line
# for the in-section test, because a real PRD writes the justifying fact and its rule in one
# sentence ("791 of 791 records carry locale: he. Do not author an opening that assumes
# otherwise."). The vocabulary covers the verbs authors actually reach for; a narrower list
# reported ordinary steering as misplaced facts, which is how a lint gets ignored.
_DIRECTIVE_SHAPE = re.compile(
    r'\b(?:do not|don\'t|never|always|avoid|prefer|must|should|'
    r'author|cover|weight|include|exclude|ensure|limit|cap|focus|vary|balance|distribute|'
    r'reflect|restrict|keep|start|open|target|write|use|make|treat .{0,30} as)\b',
    re.IGNORECASE,
)

# A line that says WHERE the fact properly lives is a cross-reference, not a misplaced fact.
# A CITATION is not a world fact. The `§` had to be IMMEDIATELY preceded by `in `/`see `, so a
# real citation — which names the document first (`in artifacts/data_profile.md §3`) — missed the
# exemption, and the `3` then satisfied the digit test below.
#
# The document is allowed to sit between the verb and the section mark, and the run must be able
# to CROSS A DOT to get there: `[^.\n]` cannot pass the one in `data_profile.md`, so the widened
# branch never fired and a bare `*.md` alternative was silently doing the work instead. That
# alternative exempted any line merely MENTIONING a markdown file — `669 of 791 rows are in_stock,
# per the census dump written to data_profile.md` — which is the class this check exists to catch.
# A citation verb is what separates naming a document from citing one.
_CROSS_REFERENCE = re.compile(
    r'(?:\b(?:in|see|per)\b[^\n]{0,60}?§)|(?:in |see )§|\bworld fact in\b|\bsee the .{0,30}section\b',
    re.IGNORECASE,
)

# Markdown list markers, stripped before the digit test — an ordered list's own "1." is not
# a world fact, and counting it made every numbered constraint a finding.
_LIST_MARKER = re.compile(r'^(?:[-*+]|\d+[.)])\s+')


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) pairs, splitting on H2/H3."""
    parts = re.split(r'^(#{2,3} .*)$', text, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1] if i + 1 < len(parts) else '') for i in range(1, len(parts), 2)]


def _heading_title(heading: str) -> str:
    """The heading's text with the `#`s and any leading enumeration (`1.`, `1b.`, `(2)`, `§3`) removed."""
    title = re.sub(r'^\s{0,3}#{1,6}\s*', '', heading).strip()
    return _ENUM_PREFIX_RE.sub('', title).strip()


def _is_design_heading(heading: str) -> bool:
    """Is this the heading the BACKEND routes out of the auditor's fact surface?

    Decided by the backend's own routing regex (via `verify_prd_appendix`), so every variant it
    honours — numbered, hyphen/underscore-joined, singular, trailing colon, any case, any depth
    — is routed here too, and a heading it does NOT route (`— draft` appended) is not.
    """
    return bool(_DESIGN_ROUTED_RE.match(_heading_title(heading)))


def _design_span(text: str) -> str:
    for heading, body in _sections(text):
        if _is_design_heading(heading):
            return body
    return ''


def check_placement(text: str, entity_terms: set[str]) -> list[str]:
    """World facts stranded in the directives section, where nothing grounds them."""
    findings: list[str] = []
    for line in _design_span(text).splitlines():
        stripped = _LIST_MARKER.sub('', line.strip())
        if not stripped or stripped.startswith(('#', '|', '>')):
            continue
        # A COUNT is the tell. Steering is imperative; a line carrying a figure and no
        # imperative is stating data about the world — a census, a row count, a distribution.
        if _DIRECTIVE_SHAPE.search(stripped) or _CROSS_REFERENCE.search(stripped):
            continue
        if not re.search(r'\b\d[\d,]*\b', stripped):
            continue
        named = sorted(t for t in entity_terms if re.search(rf'\b{re.escape(t)}\b', stripped))
        about = f' about {named[0]!r}' if named else ''
        findings.append(
            f'placement: a line in "{DESIGN_HEADING}" states a world fact{about} — '
            f'"{stripped[:110]}". The auditor reads this section as steering, not as facts, '
            f'so nothing is grounded by it and no judge can cite it. Write the measurement as a '
            f'typed entry under "## Agent facts" (the one section placement is checked for); leave '
            f'the rule it justifies here.'
        )
    return findings


#: A family list the backend READS: the item's head is `Families…:` or
#: `Family <name>:`. Any other head that lists `(A) …` families is a list the backend never sees.
_READ_FAMILY_HEAD = re.compile(r'^(?:Families\b[^:.]*[:.]|Family\s)', re.IGNORECASE)
_UNREAD_FAMILY_LIST = re.compile(r'^(?P<head>[^:]*\bfamil(?:y|ies)\b[^:]*):(?P<body>.*)$', re.IGNORECASE)
#: A listed family: a `(X)` starting the list body or a `;`-separated part of it. A list names two or more.
#: A `Families` head (not a single `Family <name>`), and the text after its first `:` / `.`.
_FAMILIES_LIST_HEAD = re.compile(r'^Families\b[^:.]*[:.](?P<body>.*)$', re.IGNORECASE)
_LISTED_FAMILY = re.compile(r'(?:^|;)\s*\([A-Za-z0-9][\w-]{0,23}\)')


def check_family_heads(text: str) -> list[str]:
    """A family list the backend's family parser reads nothing from: a list under a head it does not
    match (`Cover these families:`), or a `Families` head whose entries are not `(X)`-labelled."""
    findings: list[str] = []
    lines = _design_span(text).splitlines()
    for at, line in enumerate(lines):
        if not _LIST_MARKER.match(line.strip()):
            continue  # the scan declares families as list items; prose ABOUT the heading is not one
        item = re.sub(r'\*+|__', '', _LIST_MARKER.sub('', line.strip()))
        unread = _UNREAD_FAMILY_LIST.match(item)
        if unread and len(_LISTED_FAMILY.findall(unread['body'])) > 1 and not _READ_FAMILY_HEAD.match(item):
            findings.append(
                f'families: "{item[:90]}" lists families under the head "{unread["head"].strip()}:", which the '
                f'backend does not read — start the item with `Families:` (`- Families: (A) …; (B) …`) or write '
                f'one `- Family <name>: …` per family, or the family coverage check never runs.'
            )
        elif (head := _FAMILIES_LIST_HEAD.match(item)) and not _LISTED_FAMILY.findall(head['body'].strip()):
            indent = len(line) - len(line.lstrip())
            children = []
            for child in lines[at + 1:]:
                if child.strip() and len(child) - len(child.lstrip()) <= indent:
                    break
                if _LIST_MARKER.match(child.strip()):
                    children.append(re.sub(r'\*+|__', '', _LIST_MARKER.sub('', child.strip())))
            if not any(_LISTED_FAMILY.match(c) for c in children):
                findings.append(
                    f'families: "{item[:90]}" heads a family list, but no entry under it opens with a `(X)` label '
                    f'(`A.` is not one), so the backend reads no family from it — write `(A) …` entries, or the '
                    f'family coverage check never runs.'
                )
    return findings


def _routed_out(heading: str) -> bool:
    """Is this heading one the auditor routes OUT of its fact surface? One vocabulary, one matcher."""
    return _is_design_heading(heading)


#: Every heading depth, for the placement pass — the appendix may be an H1 (`_APPENDIX_HEADING`
#: accepts one), and a routed span opened at any depth closes at the same depth or shallower.
_PLACEMENT_HEADING = re.compile(r'^\s{0,3}(#{1,6})\s')


def check_facts_placement(text: str) -> list[str]:
    """ERRORS: a typed `## Agent facts` block in a section the auditor never reads.

    Section-TYPED, not sentence-shaped: the facts block is the one typed thing in the PRD, the
    routed-out sections are a closed vocabulary, and the appendix boundary is where every reader
    of the block stops. Membership is decided the way `check_mechanism_claims` slices sections —
    a heading closes a span only at the same depth or shallower — so a `### Agent facts` nested
    under `## Scenario design constraints` is inside the routed-out span, exactly as the auditor
    sees it. Each error names the section the block sits in; a block in the right place says
    nothing here.
    """
    lines = text.splitlines()
    fenced = _fenced(lines)
    routed_depth: int | None = None
    routed_heading = ''
    past_appendix = False
    errors: list[str] = []
    for line, in_fence in zip(lines, fenced):  # one flag per line (`_fenced`); no `strict=` below 3.10
        if in_fence:
            continue
        # H1 INCLUDED: `_APPENDIX_HEADING` accepts an H1 appendix and `_without_appendix` cuts at
        # it, so a placement pass that only saw H2..H6 never noticed the boundary and an H1
        # appendix silenced the error.
        heading = _PLACEMENT_HEADING.match(line)
        if not heading:
            continue
        depth = len(heading.group(1))
        if routed_depth is not None and depth <= routed_depth:
            routed_depth = None
        if _APPENDIX_HEADING.match(line):
            past_appendix = True
        if _routed_out(line):
            routed_depth, routed_heading = depth, line.strip()
            continue
        if not _is_facts_heading(line):
            continue
        if routed_depth is not None:
            errors.append(
                f'placement ERROR: "{line.strip()}" sits inside "{routed_heading}", which the auditor routes OUT of its '
                f'fact surface — every typed fact in it grounds nothing and no judge can cite it. Move the whole '
                f'"## Agent facts" section out to its own H2 (before the verbatim appendix).'
            )
        elif past_appendix:
            errors.append(
                f'placement ERROR: "{line.strip()}" sits after the VERBATIM SOURCE APPENDIX heading, where this lint and '
                f'the scan contract stop reading — the block is reported as SECTION ABSENT and none of its facts are '
                f'verified. Move it before the appendix, which must stay the last H2.'
            )
    return errors


# --- Fence tracking -------------------------------------------------------------------
#
# The appendix wraps whole source files in 4-5 backtick fences whose bytes contain 3-backtick
# fences of their own, so a naive "count the ``` runs" reader loses track inside the first
# entry. This mirrors the rule the backend parses by: a fence closes on the first bare run of
# the SAME character that is at least as long as its opener.
_FENCE_OPEN = re.compile(r'^\s*(`{3,}|~{3,})')
_FENCE_BARE = re.compile(r'^\s*(`{3,}|~{3,})\s*$')


def _fenced(lines: list[str]) -> list[bool]:
    """Per line: is it inside a fenced block (delimiters included)?"""
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


_APPENDIX_HEADING = re.compile(r'^#{1,6}\s+VERBATIM SOURCE APPENDIX\b', re.IGNORECASE)


def _without_appendix(text: str) -> str:
    """The PRD up to the live verbatim appendix heading.

    The appendix holds BYTE-COPIES of the customer's own documents. Reading rules out of
    someone else's file and reporting them as this PRD's authoring defects is how a lint
    becomes noise, and the bytes are not editable here anyway — the fix is at the source file.
    """
    lines = text.splitlines()
    for i, (line, in_fence) in enumerate(zip(lines, _fenced(lines))):
        if not in_fence and _APPENDIX_HEADING.match(line):
            return '\n'.join(lines[:i])
    return text


# --- Agent-derived law ----------------------------------------------------------------

#: A rule about what a SCENARIO TARGET may be. Either polarity — the ban and the licence are the
#: same defect, and the pair is what a re-scan flips between.
#: `authorable` is a standalone trigger: the licence roll of the shipped pair never says
#: "target" at all ("`material` IS authorable via compare_products(...)"), and the word belongs
#: to no other register.
#: The `target` alternatives need an AUTHORING/PERMISSION verb as well as the polarity word.
#: Polarity alone made "target" plus any later negation the trigger, and ordinary domain prose
#: carries both ("the target account cannot be closed while a balance is outstanding") — a lint
#: read during authoring cannot afford that.
_TARGET_LAW = re.compile(
    r'\b(?:un)?authorable\b'
    r'|\btargets?\b(?=[^\n]*\b(?:author(?:s|ed|ing)?|permitted|licen[cs]ed|allowed|forbidden|prohibited)\b)'
    r'(?=[^\n]*\b(?:never|not|do not|don\'t|must not|cannot|may not|forbidden|prohibited|'
    r'licen[cs]ed|permitted|allowed)\b)'
    r'|\b(?:never|do not|don\'t|must not|no)\b[^\n]{0,60}\bauthor(?:ing)?\b[^\n]{0,40}\btargets?\b',
    re.IGNORECASE,
)

#: A rule about how behaviour is GRADED. The PRD may say what the agent does; it may not say
#: what a verdict on that behaviour should be.
_GRADING_LAW = re.compile(
    r'\bgrad(?:e|es|ed|ing)\b[^\n]{0,60}\b(?:correct|incorrect|pass|passing|fail|failure|violation)\b'
    # The determiner is optional because the sentence that SHIPPED carries one: "A blanket
    # decline is NOT the correct behaviour to grade" sat in a real PRD's design constraints and
    # this pattern walked past it, defeated by one article.
    r'|\bis (?:not )?(?:the |a )?correct behaviou?r\b'
    r'|\bcounts? as (?:a )?(?:pass|correct|failure|violation)\b'
    r'|\b(?:score|mark)[sd]? (?:it|the \w+) as (?:correct|incorrect|passing|failing)\b',
    re.IGNORECASE,
)


#: Law with no imperative in it: an EXPECTATION stated as though it were a fact about the world.
#: "Expected agent behavior in these scenarios is graceful disclosure of a data gap, not
#: fabrication" carries no "author", no "grade X as Y", no modal — and is the same forbidden
#: class, because it fixes the verdict. Three parts, all required: an expectation word, a
#: BEHAVIOUR object close to it, and a polarity CONTRAST. The contrast is what separates
#: legislating from describing — a sentence that says what correct behaviour is *instead of*
#: something else has ruled, and two scans can rule opposite ways.
_EXPECTATION_LAW = re.compile(
    r'\b(?:expected|compliant|correct|desired)\b[^\n]{0,25}\bbehaviou?r\b[^\n]{0,140}?'
    r'(?:,\s*not\b|\brather than\b|\binstead of\b)',
    re.IGNORECASE,
)

_LIST_START = re.compile(r'^(?:[-*+]|\d+[.)])\s+')


def _paragraphs(text: str) -> list[str]:
    """Prose rejoined across the lines it wraps over.

    LINE-SCOPED MATCHING IS THE RECURRING HAZARD IN THIS FILE — it has now been wrong twice, and
    a third reader will meet it again. Once in `rescan_diff.py`, where a backticked identifier
    wrapped and the closing backtick of one name paired with the opening backtick of the next,
    turning the prose between them into a "name". And once here: the expectation law below wraps
    mid-sentence in real output ("… is graceful" / "disclosure of a data gap, not fabrication"),
    so no line ever contains both halves. Anything matching across a sentence must read
    paragraphs, not lines.

    A list ITEM starts its own paragraph: joining a whole bullet list would let an expectation
    word in one rule pair with a contrast three rules later.
    """
    out: list[str] = []
    buf: list[str] = []
    lines = text.splitlines()
    for line, in_fence in zip(lines, _fenced(lines)):
        stripped = line.strip()
        if in_fence or not stripped or stripped.startswith(('#', '>', '|')):
            if buf:
                out.append(' '.join(buf))
                buf = []
            continue
        if _LIST_START.match(stripped) and buf:
            out.append(' '.join(buf))
            buf = []
        buf.append(stripped)
    if buf:
        out.append(' '.join(buf))
    return out


def _finding(kind: str, quote: str) -> str:
    return (
        f'authored-law: a {kind} rule derived from this agent — "{quote[:110]}". '
        f'Two scans of the same data can state this either way round, and the backend already '
        f'enforces the generic invariant (targets ground in the licensed tool surface; an honest '
        f'decline grades correct otherwise). Write the MEASUREMENT under "## Agent facts" instead '
        f'(see references/agent_facts.md).'
    )


def check_authored_law(text: str) -> list[str]:
    """Target-shape, grading and declarative-expectation rules derived from THIS agent.

    Three narrow shapes. The wider question ("is this sentence law or fact?") is the same
    undecidable one the removed quote-provenance check foundered on; these are decidable because
    the giveaway is a verb pattern, not a reading.

    The first two are line-scoped, which is what a bullet-shaped rule is. The third reads
    PARAGRAPHS — see `_paragraphs` for why that is not optional.
    """
    body = _without_appendix(text)
    findings: list[str] = []
    flagged: list[str] = []
    lines = body.splitlines()
    for line, in_fence in zip(lines, _fenced(lines)):
        stripped = line.strip()
        # Blockquotes are the template's own guidance callouts, and a PRD that kept one is
        # quoting the instructions rather than legislating. Same trade check_placement makes:
        # the alternative reported this plugin's own template as a defect on every agent.
        if in_fence or not stripped or stripped.startswith(('#', '>')):
            continue
        kind = 'target-shape' if _TARGET_LAW.search(stripped) else ('grading' if _GRADING_LAW.search(stripped) else '')
        if not kind:
            continue
        flagged.append(stripped)
        findings.append(_finding(kind, stripped))

    for paragraph in _paragraphs(body):
        match = _EXPECTATION_LAW.search(paragraph)
        # One paragraph is one finding: the line pass above may already have reported the same
        # sentence through its imperative half.
        if not match or any(line in paragraph for line in flagged):
            continue
        findings.append(_finding('declarative grading', match.group(0)))
    return findings


# --- Typed facts ----------------------------------------------------------------------

#: Closed, per `references/agent_facts.md`. A token absent here is either a rule wearing a fact's
#: clothes or a real gap in the vocabulary — both are worth a human look, neither is silent.
_FACT_PREDICATES = {
    'data_profile': ('null_on_all_rows', 'closed_set', 'distribution', 'recoverable_from_text', 'absent_from_source', 'conflicts_across_sources'),
    # `writes`/`id_stability`/`ordering`/`idempotency` cover what a call CHANGES and what
    # survives it. They must stay in step with the table in `references/agent_facts.md` —
    # they were added there first and this tuple was not updated, so every fact the skill
    # had just made mandatory linted as an unknown token on every run.
    'tool_surface': (
        'returns', 'omits', 'accepts', 'rejects', 'undocumented_to_model', 'matching_semantics',
        'hard_limit', 'writes', 'id_stability', 'ordering', 'idempotency',
        # `source_unbound`: the agent's own config binds a retrieval source to empty. The backend keys
        # its answer-delivery mandate on this exact token. This is a retyped copy: if the two drift, the
        # fact still lints clean here and the backend silently emits no delivery judge.
        'source_unbound',
    ),
    'seedability': ('seedable', 'not_seedable'),
    # A behaviour rule QUOTED from the agent's own instructions — the obligations the backend
    # makes the eval set own or decline (`contract_clause` rows). Pinned to the vendored
    # `agent_rule_fact_kind` contract block, which the plugin's tests compare against.
    'rule': ('instructs',),
    # A run setting (step cap, timeout, model, token limit) the harness passes with a value other than
    # the one production's caller sends, or where shipped callers disagree. Plugin-side only: the backend
    # reads PRD facts by kind and ignores one it does not consume.
    'run_setting': ('harness_differs',),
}
_FACT_KEYS = ('kind', 'subject', 'predicate', 'evidence', 'source')
RULE_KIND = 'rule'

#: What an empty retrieval binding MEANS on its platform, required on every `source_unbound` fact.
#: Only `none` (nothing is searched) is the defect hypothesis the backend answers with a delivery
#: eval; `unknown` is a medium potential finding with no mandate; `platform_default` is a bound
#: source, so it writes no fact at all.
SOURCE_UNBOUND = 'source_unbound'
EMPTY_MEANS = ('none', 'platform_default', 'unknown')

#: Instruction shapes in an `evidence` string. A fact states what was measured; the moment it
#: states what someone should do with the measurement it is law again, wearing the typed shape.
#: `author` and `grade` are matched as VERBS with their object, never as bare tokens: they are
#: also ordinary field names, and a `post.author` / `submission.grade` fact whose evidence names
#: its own column is the exact thing this section exists to collect.
_EVIDENCE_INSTRUCTION = re.compile(
    r'\b(?:never|do not|don\'t|must not|should)\b'
    r'|\b(?:un)?authorable\b'
    r'|\bauthor(?:s|ed|ing)?\b[^\n]{0,40}\btargets?\b'
    r'|\bgrad(?:e|es|ed|ing)\b[^\n]{0,40}\b(?:as|correct|incorrect|a pass|a failure)\b',
    re.IGNORECASE,
)


def _is_facts_heading(heading: str) -> bool:
    normalized = re.sub(r'^#+\s*(?:\d+[.)]\s*)?', '', heading.lower()).replace('-', ' ').rstrip(': ').strip()
    return normalized.startswith('agent fact')


def _facts_block(text: str) -> str | None:
    """The first fenced block under the `## Agent facts` heading, or None when absent."""
    lines = text.splitlines()
    fenced = _fenced(lines)
    start = next((i for i, (line, f) in enumerate(zip(lines, fenced)) if not f and line.startswith('#') and _is_facts_heading(line)), None)
    if start is None:
        return None
    body: list[str] | None = None
    for i in range(start + 1, len(lines)):
        if not fenced[i]:
            if body is not None:
                break
            if lines[i].startswith('#'):  # next section, no block
                return None
            continue
        if body is None:
            body = []  # the opening delimiter
            continue
        body.append(lines[i])
    if body is None:
        return None
    return '\n'.join(body[:-1] if body and _FENCE_BARE.match(body[-1]) else body)


def _facts_entries(text: str) -> tuple[list | None, list[str]]:
    """`(entries, findings)` — the parsed `facts:` list, None when there is no section or it does
    not parse. ONE parse for every check that reads the block; `check_facts` grades the entries,
    `check_render_surface` reads them."""
    block = _facts_block(_without_appendix(text))
    if block is None:
        return None, []
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return None, [f'facts: the "## Agent facts" block is not parseable YAML ({exc}). Nothing in it is read.']
    entries = loaded.get('facts') if isinstance(loaded, dict) else None
    if not isinstance(entries, list):
        return None, ['facts: the "## Agent facts" block must be a mapping with a `facts:` list (see references/agent_facts.md).']
    return entries, []


def check_facts(text: str) -> tuple[list[str], int | None]:
    """`(findings, fact count)` — count is None when the PRD carries no facts section."""
    entries, parse_findings = _facts_entries(text)
    if entries is None:
        return parse_findings, (0 if parse_findings else None)

    findings: list[str] = []
    # Where a `rule` fact's quote must already sit: the PRD minus the facts block, whitespace-folded
    # so a quote re-wrapped across lines still matches its bytes.
    quoted = ' '.join(text.split()).replace(' '.join((_facts_block(_without_appendix(text)) or '').split()), '', 1)
    for i, entry in enumerate(entries, 1):
        where = f'facts[{i}]'
        if not isinstance(entry, dict):
            findings.append(f'facts: {where} is not a mapping — every entry carries {", ".join(_FACT_KEYS)}.')
            continue
        subject = entry.get('subject') or '?'
        for key in _FACT_KEYS:
            if not str(entry.get(key) or '').strip():
                findings.append(f'facts: {where} ({subject}) has no `{key}`. A fact with no {key} is prose in a typed costume.')
        kind, predicate = str(entry.get('kind') or ''), str(entry.get('predicate') or '')
        if kind and kind not in _FACT_PREDICATES:
            findings.append(f'facts: {where} ({subject}) has kind `{kind}`; the closed set is {", ".join(sorted(_FACT_PREDICATES))}.')
        elif predicate and predicate not in _FACT_PREDICATES.get(kind, ()):
            findings.append(
                f'facts: {where} ({subject}) has predicate `{predicate}`, which is not in the closed set for '
                f'kind `{kind}`: {", ".join(_FACT_PREDICATES[kind])}.'
            )
        evidence = str(entry.get('evidence') or '')
        if kind == RULE_KIND:
            # Its evidence IS an instruction — to the agent, not to the generator — so what makes it
            # a fact is being the agent's own bytes, not a paraphrase of them.
            if ' '.join(evidence.split()) not in quoted:
                findings.append(
                    f'facts: {where} ({subject}) is a `rule` whose `evidence` the PRD does not quote anywhere else — copy the '
                    f"sentence the PRD quotes from the agent's instructions byte for byte; a paraphrased rule is law the agent was never given."
                )
        elif _EVIDENCE_INSTRUCTION.search(evidence):
            findings.append(
                f'facts: {where} ({subject}) states an INSTRUCTION in `evidence` — "{evidence[:80]}". '
                f'Evidence is the measurement or the quoted bytes; what to do about it is the backend invariant\'s job.'
            )
        findings.extend(_fields_key_findings(entry, where=where, subject=str(subject), kind=kind))
        findings.extend(_detector_key_findings(entry, where=where, subject=str(subject), predicate=predicate))
        if predicate == SOURCE_UNBOUND:
            findings.extend(_empty_means_findings(entry, where=where, subject=str(subject)))
    return findings, len(entries)


def _empty_means_findings(entry: dict, *, where: str, subject: str) -> list[str]:
    """A `source_unbound` fact types what the empty binding means; a platform default is not unbound."""
    value = entry.get('empty_means')
    if value not in EMPTY_MEANS:
        return [
            f'facts: {where} ({subject}) is `source_unbound` with `empty_means: {value!r}`; write one of {", ".join(EMPTY_MEANS)} '
            "— what the platform does with the empty value the agent's code passes, cited from its docs or code in `evidence`."
        ]
    if value == 'platform_default':
        return [
            f'facts: {where} ({subject}) says the empty binding means the platform default — that source is bound, so write no '
            '`source_unbound` fact; build the corpus from that default source instead.'
        ]
    return []


def duplicate_facts(entries: list) -> list[str]:
    """ERRORS: a fact whose `subject.predicate` id repeats an earlier fact's — THE owner of the rule.

    Keyed on the composed id, not the pair: the id is what the scan contract reports a verdict
    under and what collides (`a.x`/`omits` and `a`/`x.omits` are one id). A repeat has no
    verdict of its own: `scan_facts.py` (which calls this) names it and forwards both, and the
    contract reports two verdicts under one id. A fact carries one `source`, which is what makes a
    second citation for the same claim look like a second fact.
    """
    first: dict[str, int] = {}
    errors: list[str] = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        subject, predicate = str(entry.get('subject') or '').strip(), str(entry.get('predicate') or '').strip()
        if not subject or not predicate:
            continue
        key = f'{subject}.{predicate}'  # the SAME composition `scan_facts._project_one` builds its id with
        if key not in first:
            first[key] = i
            continue
        errors.append(
            f'facts[{i}]: `{key}` is already declared by facts[{first[key]}] — two facts cannot share a subject '
            f'and a predicate, and the scan contract reports two verdicts under one id. Merge facts[{i}] into facts[{first[key]}] '
            f'(one `source`; the other citation goes in its `evidence`).'
        )
    return errors


def check_fact_identity(text: str) -> list[str]:
    """ERRORS: `duplicate_facts` over the PRD's block — the lint's half of the one rule."""
    entries, _ = _facts_entries(text)
    return [f'identity ERROR: {e}' for e in duplicate_facts(entries or [])]


#: The user's FILE channel as a PRD spells it: what a design constraint denies and a quoted capability names.
_FILE_CHANNEL = r'(?:input\s+)?(?:files?|attachments?|documents?|uploads?)'
_HANDING_VERB = r'(?:provides?|supplies|supply|attach(?:es)?|uploads?|shares?|sends?|gives?)'
#: A design constraint that takes the file channel away from the simulated user, set-wide. A family
#: whose user NAMES a file without supplying it is no denial: the object comes before the verb there.
_INPUT_DENIAL = re.compile(
    rf'\bhands?\s+over\s+no\b[^.;]{{0,40}}?\b{_FILE_CHANNEL}\b'
    rf'|\b{_HANDING_VERB}\s+no\b[^.;]{{0,40}}?\b{_FILE_CHANNEL}\b'
    rf'|\bnever\s+(?:hands?\s+over|{_HANDING_VERB})\s+(?:(?:a|an|any|the)\s+)?(?:\w+\s+)?{_FILE_CHANNEL}\b'
    rf'|\bno\s+(?:(?:file|document)\s+)?(?:attachments?|uploads?)\b',
    re.IGNORECASE,
)
#: A quoted capability: the agent takes a file the user hands over or names ("input files the user mentions").
_INPUT_FILE_CAPABILITY = re.compile(
    rf'\b(?:input|uploaded|attached|incoming)\s+{_FILE_CHANNEL}\b'
    rf'|\b{_FILE_CHANNEL}\s+(?:that\s+)?(?:the\s+)?users?\s+(?:mentions?|names?|{_HANDING_VERB})\b'
    rf'|\busers?\s+(?:mentions?|names?|{_HANDING_VERB})\s+(?:(?:a|an|the|their|its)\s+)?{_FILE_CHANNEL}\b',
    re.IGNORECASE,
)


def input_file_capabilities(text: str) -> list[str]:
    """The `## Agent facts` subjects whose quoted `evidence` has the agent take a file the user hands over or names.
    `verify_config.py`'s `input_file_channel_undeclared` reads the same list."""
    entries, _ = _facts_entries(text)
    return [
        str(e.get('subject') or '?') for e in entries or []
        if isinstance(e, dict) and _INPUT_FILE_CAPABILITY.search(str(e.get('evidence') or ''))
    ]


def check_input_denials(text: str) -> list[str]:
    """ERRORS: a design constraint denying the user's file channel while the facts quote the agent taking user files.

    Both halves are in this one document, so the contradiction is decided here: the constraint would
    steer every scenario away from the channel the agent's own quoted instructions describe."""
    capabilities = input_file_capabilities(text)
    if not capabilities:
        return []
    return [
        f'input-denial ERROR: "…{paragraph[max(0, denial.start() - 80):denial.end() + 40]}…" denies the simulated user the '
        f'file channel, while the agent facts quote the agent taking a user\'s files ({", ".join(capabilities)}). '
        f'No scenario would hand the agent a file. Declare the handover (simulation-setup Step 8: `file` in '
        f'`agent.io.input` and a `.document_fields` entry) and drop the denial.'
        for paragraph in _paragraphs(_design_span(text))
        if (denial := _INPUT_DENIAL.search(paragraph))
    ]


#: A claim that the AGENT lacks a capability: "has no tool to X" (an error when a production tool does X) and "the
#: agent cannot X" (a warning: it is as often a policy limit as a missing tool). X runs to the clause's end.
_ABSENCE_CLAIM = re.compile(
    r'\b(?:has|have|with)\s+no\s+(?:\w+\s+)?(?:tools?|capability|capabilities|way|function)\s+(?:to|for|that)\s+(?P<tool>[^.;:\n]{3,80})'
    r'|\bno\s+(?:\w+\s+)?tools?\s+(?:exists?\s+)?(?:to|for)\s+(?P<none>[^.;:\n]{3,80})'
    r'|\b(?:the\s+agent|it)\s+(?:cannot|can\'t|can\s+not|is\s+unable\s+to|is\s+not\s+able\s+to)\s+(?P<cannot>[^.;:\n]{3,80})',
    re.IGNORECASE,
)
#: Words of a claim that name no capability, and the verbs every tool name shares: a match needs one word beyond them.
_CLAIM_FILLER = frozenset({'a', 'an', 'the', 'any', 'of', 'to', 'for', 'or', 'and', 'in', 'on', 'from', 'by', 'with', 'its', 'their',
                           'user', 'users', 'it', 'them', 'this', 'that', 'own', 'directly', 's'})
_GENERIC_VERBS = frozenset({'read', 'get', 'list', 'fetch', 'search', 'find', 'create', 'update', 'delete', 'send', 'write', 'add',
                            'remove', 'set', 'check', 'view', 'show', 'look', 'access', 'see', 'use', 'make', 'retrieve', 'query',
                            'edit', 'modify', 'manage', 'handle', 'do', 'perform', 'open', 'load', 'call', 'run'})


def _words(text: str) -> set[str]:
    """A phrase or a tool name as singular lowercase words (`get_calendarEvents` -> get, calendar, event)."""
    tokens = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])', text)
    return {t.lower()[:-1] if len(t) > 3 and t.lower().endswith('s') and not t.lower().endswith('ss') else t.lower() for t in tokens} - _CLAIM_FILLER


def production_tool_names(prd: Path) -> list[str]:
    """The agent's production tools (C3) — the ONE inventory artifact the validator and the map read, else the bench
    manifest's copy — plus the manifest's `not_loaded` tools that still ship (off only for a variant or on another
    surface): the PRD may call none of them absent."""
    production: list[dict] = []
    for path, keys in ((prd.parent / 'artifacts' / 'agent_inventory.json', ('production_tools',)), (prd.parent / 'bench_manifest.json', ('surfaces', 'production_tools'))):
        try:
            node = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for key in keys:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and isinstance(node.get('names'), list):
            production.append(node)
    if not production:
        return []
    shipped = [n for g in production[-1].get('not_loaded') or [] if isinstance(g, dict) and g.get('because') != 'default_off_switch' for n in g.get('names') or []]
    return sorted({str(n) for n in [*production[0]['names'], *shipped]})


def check_absence_claims(text: str, tools: list[str]) -> tuple[list[str], list[str]]:
    """`absence_claim_contradicts_source`: a claim that the agent lacks a capability its production tool set has.

    ``(errors, warnings)`` — "has no tool to X" is an ERROR (the PRD turns a harness cut into an agent limit, and every
    judge then grades the agent for not doing what it can), "the agent cannot X" a warning. A claim contradicts a tool
    when they share two specific words, or one and the verb. The verbatim appendix is the agent's own
    text and is never read here."""
    if not tools:
        return [], []
    names = {name: _words(name) for name in tools}
    errors: list[str] = []
    warnings: list[str] = []
    for match in _ABSENCE_CLAIM.finditer(_without_appendix(text)):
        claim = (match.group('tool') or match.group('none') or match.group('cannot')).split('(')[0]
        wanted = _words(claim)
        scored = []
        for name, words in names.items():
            specific, verbs = len((wanted & words) - _GENERIC_VERBS), len(wanted & words & _GENERIC_VERBS)
            if specific >= 2 or (specific and verbs):
                scored.append((-specific - verbs, name))
        hits = [name for _, name in sorted(scored)]
        if not hits:
            continue
        line = f'absence_claim_contradicts_source: "{match.group(0).strip()}" — the agent ships {", ".join(f"`{h}`" for h in hits[:5])} under its production defaults. Mock the tool; never state a harness limit as the agent\'s'
        (warnings if match.group('cannot') else errors).append(line)
    return errors, warnings


#: The `## Agent facts` key, beside `facts:`, that declares where the agent's instructions live.
INSTRUCTION_SOURCES_KEY = 'instruction_sources'
#: Closed reasons an `unrowed` range may give. `duplicate-of-<subject>` must name a `rule` fact;
#: `not-loaded` must cite (`source: path:LINE`) the load site that reads something else instead.
UNROWED_NOT_AN_INSTRUCTION = 'not-an-instruction'
UNROWED_DUPLICATE_OF = 'duplicate-of-'
UNROWED_NOT_LOADED = 'not-loaded'
#: The only declines that close a line of template slots only: `filled-by` cites the declared range(s) that fill it with
#: instructions (`source: path:LO-HI[, path:LO-HI]`), `data-slot` the line that fills it with data (`source: path:LINE`).
UNROWED_FILLED_BY = 'filled-by'
UNROWED_DATA_SLOT = 'data-slot'
_LINE_RANGE =re.compile(r'^\s*(\d+)\s*(?:-\s*(\d+))?\s*$')
_RULE_SOURCE = re.compile(r'^\s*`?(?P<path>[^\s`:]+):(?P<lines>\d+(?:-\d+)?)`?\s*$')
_LETTER = re.compile(r'[^\W\d_]')
_MD_HEADING = re.compile(r'^\s*#{1,6}\s')
_QUOTE_ESCAPE = re.compile(r'\\(["\'\\])')


def _line_range(value: object) -> tuple[int, int] | None:
    match = _LINE_RANGE.match(str(value))
    if not match:
        return None
    lo, hi = int(match.group(1)), int(match.group(2) or match.group(1))
    return (lo, hi) if 1 <= lo <= hi else None


def _citation(value: object) -> tuple[str, tuple[int, int]] | None:
    """A `path:LINE[-END]` citation as ``(path, (lo, hi))``; ``None`` when it is not one or its range is not LO-HI."""
    match = _RULE_SOURCE.match(str(value or ''))
    cited = _line_range(match.group('lines')) if match else None
    return (match.group('path').removeprefix('./'), cited) if match and cited else None


def _cites_a_load_site(value: object, repo_root: Path, declined: str) -> bool:
    """`path:LINE[-END]` names existing lines of a file in the agent's tree other than the ``declined`` one."""
    citation = _citation(value)
    resolved = _resolve(citation[0], repo_root) if citation else None
    root = repo_root.resolve()
    loadable = resolved is not None and resolved.resolve().is_relative_to(root) and resolved.resolve() != (root / declined).resolve()
    return citation is not None and loadable and citation[1][1] <= len(resolved.read_text(encoding='utf-8', errors='replace').splitlines())


def _cites_a_harness_line(value: object, repo_root: Path) -> bool:
    """A `not_seedable` decline's `source` cites why: a declared carrying lane (`.document_fields`), or existing lines of a
    file under `plurai_sim/` (the harness). Several cites may be joined by `,`, `;`, `+`, `and` or `plus`."""
    parts = re.split(r'\s*[,;+]\s*|\s+(?:and|plus)\s+', str(value or '').strip())
    for part in (p.strip().strip('`') for p in parts):
        if part == '.document_fields' or part.endswith('/.document_fields'):
            return True
        citation = _citation(part)
        if citation and 'plurai_sim' in Path(citation[0]).parts and _cites_a_load_site(part, repo_root, ''):
            return True
    return False


def _fold(value: str) -> str:
    """Whitespace- and quote-escape-folded, so a quote re-wrapped or JSON-escaped still matches."""
    return ' '.join(_QUOTE_ESCAPE.sub(r'\1', value).split())


#: One template slot: `{name}` (str.format, f-string), `{{ name }}` (Jinja, Handlebars), `${expr}` (a JS template literal).
_SLOT = re.compile(r'\{\{\s*[^{}]+?\s*\}\}|\$\{[^{}]+\}|\{[A-Za-z_][^{}\s]*\}')
_IDENTIFIER = re.compile(r'[A-Za-z_]\w*')
_BUILTIN_NAMES = frozenset(dir(builtins))


def slot_only(line: str) -> bool:
    """Whether ``line`` holds nothing but template slots: the model reads whatever fills them, not these bytes.

    PUBLIC: `scaffold_rule_facts.py` leaves such a line open by the same definition."""
    text = line.strip()
    return bool(text) and not _SLOT.sub('', text).strip()


def slot_names(line: str) -> list[str]:
    """The names the slots of ``line`` read: a `{expr}` slot's Python names minus builtins (`{chr(10).join(tools)}` reads
    `tools`), else, and for a Jinja or JS slot, its first identifier."""
    names: list[str] = []
    for match in _SLOT.finditer(line):
        slot = match.group(0)
        body = slot.strip('{}$ ')
        found: list[str] = []
        if not slot.startswith(('{{', '${')):
            try:
                found = [n.id for n in ast.walk(ast.parse(body, mode='eval')) if isinstance(n, ast.Name) and n.id not in _BUILTIN_NAMES]
            except (SyntaxError, ValueError):
                found = []
        first = _IDENTIFIER.search(body)
        names += [n for n in (found or ([first.group(0)] if first else [])) if n not in names]
    return names


MARKDOWN_SUFFIXES = ('.md', '.markdown', '.mdx')
_TABLE_ROW = re.compile(r'^\s*\|')


def reference_lines(path: str, lines: list[str]) -> set[int]:
    """The 1-based lines of a markdown file that are reference material: the front matter, fenced code blocks
    (delimiters included) and table rows. Another kind of file holds none: a fence or a table inside a prompt string
    is part of the prompt.

    PUBLIC: `scaffold_rule_facts.py` declines these by default."""
    if not path.lower().endswith(MARKDOWN_SUFFIXES):
        return set()
    marked = {n for n, inside in enumerate(_fenced(lines), 1) if inside}
    marked |= {n for n, line in enumerate(lines, 1) if _TABLE_ROW.match(line)}
    if lines and lines[0].rstrip() == '---':
        close = next((n for n in range(2, len(lines) + 1) if lines[n - 1].rstrip() == '---'), None)
        if close is not None:
            marked |= set(range(1, close + 1))
    return marked


def _closes_slots(decline: dict, path: str, lines: list[str], dspan: tuple[int, int], spans: list, repo_root: Path) -> bool:
    """A `filled-by` / `data-slot` decline holds: every contentful line of ``dspan`` is template slots only, and its
    `source` cites what fills them outside the declined lines. `filled-by`: each comma-separated `path:LO-HI` lies inside
    a declared `instruction_sources` range. `data-slot`: `path:LINE[-END]` resolves in the tree, and its lines carry every
    name each slot reads (`slot_names`)."""
    body = [lines[m - 1] for m in range(dspan[0], min(dspan[1], len(lines)) + 1) if _LETTER.search(lines[m - 1])]
    if not body or not all(slot_only(line) for line in body):
        return False

    def overlaps(lo: int, hi: int) -> bool:
        return lo <= dspan[1] and dspan[0] <= hi

    source = str(decline.get('source') or '')
    if decline.get('class') == UNROWED_FILLED_BY:
        cites = [_citation(part) for part in source.split(',')]
        return all(
            c is not None and not (c[0] == path and overlaps(*c[1])) and any(s[0] == c[0] and s[1] <= c[1][0] and c[1][1] <= s[2] for s in spans)
            for c in cites
        )
    citation = _citation(source)
    resolved = _resolve(citation[0], repo_root) if citation else None
    if citation is None or resolved is None:
        return False
    try:
        cited = resolved.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return False
    lo, hi = citation[1]
    if hi > len(cited) or (resolved.resolve() == (repo_root / path).resolve() and overlaps(lo, hi)):
        return False
    text = '\n'.join(cited[lo - 1 : hi])
    return all(re.search(rf'(?<!\w){re.escape(name)}(?!\w)', text) for line in body for name in slot_names(line))


def check_rule_coverage(text: str, repo_root: Path) -> tuple[list[str], str]:
    """ADVISORIES: an instruction line no `rule` fact rows and no `unrowed` range declines — `(findings, status)`.

    The eval set is asked to own exactly the ROWED rules, so a rule the PRD never typed is an
    obligation nothing grades. A rule's `source` (`path:LINE[-END]`) rows the lines of its range
    that its whitespace-folded quote CARRIES, plus any following lines the quote runs onto (a
    wrapped rule cited at its first line) — a range its quote does not carry reaches no clause.
    A `duplicate-of-<subject>` decline holds only over lines that subject's quote carries. Lines
    without a letter (`{`, `},`, a closing fence) and markdown headings are never reported. A line
    of template slots only (`slot_only`, outside a markdown file's `reference_lines`) is reported on
    its own until a `filled-by` or `data-slot` decline ties it to what fills it (`_closes_slots`):
    a `rule` quoting it or a `not-an-instruction` range over it accounts for nothing. What
    counts as an instruction is the scan author's judgement, recorded as a typed decline; this
    only guarantees a judgement exists for every line. A PRD without the key is SKIPPED, with a
    warning when it carries `rule` facts.
    """
    block = _facts_block(_without_appendix(text))
    try:
        loaded = yaml.safe_load(block) if block is not None else None
    except yaml.YAMLError:
        loaded = None  # `check_facts` reports the parse
    facts = loaded.get('facts') if isinstance(loaded, dict) and isinstance(loaded.get('facts'), list) else []
    rules = [f for f in facts if isinstance(f, dict) and str(f.get('kind')) == RULE_KIND]
    # Declared EMPTY is declared absent: `instruction_sources: []` would silence the finding without the reason it asks for.
    if not isinstance(loaded, dict) or not loaded.get(INSTRUCTION_SOURCES_KEY):
        # A facts block that declares no instruction files leaves every rule the agent was told unrowed and ungraded,
        # whether or not it types some. A PRD with no facts block at all is reported once, by `check_facts`.
        warnings = [
            f'rule coverage: the facts block declares no `{INSTRUCTION_SOURCES_KEY}:` ({len(rules)} `rule` fact(s)) — declare the files '
            f"holding the agent's instructions (`scaffold_rule_facts.py` writes the declaration and the rows), so a rule the PRD never "
            f'typed is reported instead of ungraded. An agent with no instruction text accepts this finding with that reason.'
        ] if isinstance(loaded, dict) else []  # a block that does not parse is reported once, as an error
        return warnings, 'SKIPPED (no instruction_sources)'
    sources = loaded[INSTRUCTION_SOURCES_KEY]
    if not isinstance(sources, list):
        return [f'rule coverage: `{INSTRUCTION_SOURCES_KEY}:` must be a list of `{{path, lines, unrowed}}` entries.'], 'UNREADABLE'
    findings: list[str] = []
    spans: list[tuple[str, int, int, list[str]]] = []
    declined: dict[str, set[int]] = {}
    # The declines that close a line of template slots only; nothing else does (a `not-loaded` file is never read at all).
    slots_closed: dict[str, set[int]] = {}
    read: list[tuple[str, str, list[str], dict]] = []
    for i, entry in enumerate(sources, 1):
        where = f'{INSTRUCTION_SOURCES_KEY}[{i}]'
        path = str(entry.get('path') or '').strip().removeprefix('./') if isinstance(entry, dict) else ''
        resolved = _resolve(path, repo_root) if path else None
        if resolved is None:
            findings.append(f'rule coverage: {where} names `{path or "(no path)"}`, which is not a file under the repo root.')
            continue
        try:
            lines = resolved.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError as exc:
            findings.append(f'rule coverage: {where} names `{path}`, which could not be read ({exc.strerror or exc}).')
            continue
        span = _line_range(entry['lines']) if entry.get('lines') is not None else (1, len(lines))
        if span is None or span[1] > len(lines):
            findings.append(f'rule coverage: {where} `lines: {entry.get("lines")}` is not a LO-HI range inside `{path}` ({len(lines)} lines).')
            continue
        spans.append((path, span[0], span[1], lines))
        read.append((where, path, lines, entry))
    # Declines are read once every span is known: a `filled-by` may cite a range declared after its own entry.
    for where, path, lines, entry in read:
        for j, decline in enumerate(entry.get('unrowed') or [], 1):
            cls = str(decline.get('class') or '') if isinstance(decline, dict) else ''
            dspan = _line_range(decline.get('lines')) if isinstance(decline, dict) else None
            named = cls[len(UNROWED_DUPLICATE_OF) :] if cls.startswith(UNROWED_DUPLICATE_OF) else ''
            quote = next((_fold(str(r.get('evidence') or '')) for r in rules if named and str(r.get('subject') or '').strip() == named), '')
            # A duplicate declines only the named rule's own bytes — anything else is its own rule.
            duplicate = bool(quote) and dspan is not None and all(
                not _LETTER.search(lines[m - 1]) or _fold(lines[m - 1]) in quote for m in range(dspan[0], min(dspan[1], len(lines)) + 1)
            )
            not_loaded = cls == UNROWED_NOT_LOADED and _cites_a_load_site(decline.get('source'), repo_root, path)
            slots = cls in (UNROWED_FILLED_BY, UNROWED_DATA_SLOT) and dspan is not None and _closes_slots(decline, path, lines, dspan, spans, repo_root)
            if dspan is None or not (cls == UNROWED_NOT_AN_INSTRUCTION or duplicate or not_loaded or slots):
                findings.append(
                    f'rule coverage: {where}.unrowed[{j}] (`{cls}`) needs `lines: LO-HI` and a class from the closed set: '
                    f'`{UNROWED_NOT_AN_INSTRUCTION}`, `{UNROWED_DUPLICATE_OF}<subject>` naming a `rule` fact whose quote carries every declined line, '
                    f'or `{UNROWED_NOT_LOADED}` with `source: path:LINE` citing a load site in the agent\'s tree (not this file) that reads other instructions; '
                    f'on lines of template slots only, `{UNROWED_FILLED_BY}` with `source:` the declared `path:LO-HI` range(s) that fill them, '
                    f'or `{UNROWED_DATA_SLOT}` with `source: path:LINE`, another line that names each slot.'
                )
                continue
            declined.setdefault(path, set()).update(range(dspan[0], dspan[1] + 1))
            if slots or not_loaded:
                slots_closed.setdefault(path, set()).update(range(dspan[0], dspan[1] + 1))
    rowed: dict[str, set[int]] = {}
    for rule in rules:
        source = str(rule.get('source') or '')
        path, cited = _citation(source) or ('', None)
        home = next((s for s in spans if cited and s[0] == path and s[1] <= cited[0] and cited[1] <= s[2]), None)
        if home is None or cited is None:
            findings.append(
                f'rule coverage: the `rule` fact {rule.get("subject") or "?"} cites `{source}`, outside every declared '
                f'`{INSTRUCTION_SOURCES_KEY}` range — a rule is a line of the agent\'s own instructions, cited `path:LINE[-END]`; '
                f'a tool description is `tool_surface`, not `rule`.'
            )
            continue
        evidence, n = _fold(str(rule.get('evidence') or '')), cited[1] + 1
        # Rowed only where the quote CARRIES the line (or, quoting part of one long line, sits in it).
        covered = {m for m in range(cited[0], cited[1] + 1) if evidence and (b := _fold(home[3][m - 1])) and (b in evidence or evidence in b)}
        while n <= home[2] and (body := _fold(home[3][n - 1])) and body in evidence:
            covered.add(n)
            n += 1
        rowed.setdefault(path, set()).update(covered)
    counts = {'rowed': 0, 'declined': 0, 'unaccounted': 0}
    for path, lo, hi, lines in spans:
        run: list[int] = []
        reference = reference_lines(path, lines)
        for n in range(lo, hi + 2):
            open_line = n <= hi and bool(_LETTER.search(lines[n - 1])) and not _MD_HEADING.match(lines[n - 1])
            # A line of slots only is what fills it: no rule and no `not-an-instruction` range accounts for it.
            slot = open_line and n not in reference and slot_only(lines[n - 1])
            if slot:
                state = 'declined' if n in slots_closed.get(path, ()) else 'unaccounted'
            else:
                state = 'rowed' if n in rowed.get(path, ()) else 'declined' if n in declined.get(path, ()) else 'unaccounted'
            if open_line:
                counts[state] += 1
            if open_line and state == 'unaccounted' and not slot:
                run.append(n)
                continue
            if run:
                where = f'{path}:{run[0]}' + (f'-{run[-1]}' if len(run) > 1 else '')
                findings.append(
                    f'rule coverage: `{where}` "{lines[run[0] - 1].strip()[:90]}" is in a declared instruction source, but no '
                    f'`rule` fact cites it and no `unrowed` range declines it — row each instruction there as a `rule` fact, or '
                    f'decline the range (`{UNROWED_NOT_AN_INSTRUCTION}` / `{UNROWED_DUPLICATE_OF}<subject>` / `{UNROWED_NOT_LOADED}`).'
                )
                run = []
            if slot and state == 'unaccounted':
                findings.append(
                    f'rule coverage: `{path}:{n}` `{lines[n - 1].strip()[:90]}` is a template slot: decline it '
                    f'`={UNROWED_FILLED_BY}@<the range that fills it>` (declared as a source) or `={UNROWED_DATA_SLOT}@<the line that fills it>`.'
                )
    return findings, f'{counts["rowed"]} rowed, {counts["declined"]} declined, {counts["unaccounted"]} unaccounted'


#: Where an agent that runs on the Claude Code runtime keeps instruction text it loads at run time, relative to the repo
#: root. `CLAUDE.md` is loaded only through `setting_sources`; the others through `skills=` or the `Skill` tool as well.
_LOADED_INSTRUCTION_GLOBS = ('.claude/skills/*/SKILL.md', '.claude/agents/*.md', '.claude/commands/**/*.md')
_LOADED_MEMORY_FILES = ('CLAUDE.md', '.claude/CLAUDE.md')
#: How agent code switches that loading on, strongest signal first. The first match is the cited load site.
_LOAD_SITES = (
    re.compile(r'\b(?:setting_sources|settingSources)\b'),
    re.compile(r'\bskills\s*[=:]'),
    re.compile(r"""["']Skill["']"""),
)
_AGENT_CODE_SUFFIXES = ('.py', '.ts', '.tsx', '.js', '.mjs')
_NOT_AGENT_CODE = {'node_modules', 'venv', 'env', 'site-packages', '__pycache__', 'dist', 'build', 'outputs', 'plurai_sim'}
_CODE_FILE_LIMIT, _CODE_BYTES_LIMIT = 2000, 400_000


def _load_site(repo_root: Path) -> tuple[str, bool] | None:
    """`(path:LINE, via setting_sources)` of the line in the agent's code that loads `.claude/` instruction files, or None.

    Dot-directories and dependency, build and output trees are not the agent's code. The strongest signal wins
    (`setting_sources`, then `skills=`, then the `Skill` tool), the first line of the first file, in path order."""
    best: tuple[int, str] | None = None
    seen = 0
    for base, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _NOT_AGENT_CODE)
        for name in sorted(files):
            if not name.endswith(_AGENT_CODE_SUFFIXES):
                continue
            seen += 1
            path = Path(base) / name
            try:
                # A FIFO or device has a size of 0 and no end, so only a regular file is opened.
                if seen > _CODE_FILE_LIMIT or not path.is_file() or path.stat().st_size > _CODE_BYTES_LIMIT:
                    continue
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                for rank, pattern in enumerate(_LOAD_SITES):
                    if pattern.search(line) and (best is None or rank < best[0]):
                        best = (rank, f'{path.relative_to(repo_root).as_posix()}:{number}')
                        break
    return (best[1], best[0] == 0) if best else None


def instruction_candidates(repo_root: Path) -> list[tuple[str, str]]:
    """`(path, load site)` of each instruction file the agent loads at run time: skill, agent and command files under
    its `.claude/`, plus `CLAUDE.md` when `setting_sources` is set. Empty when its code loads none.

    PUBLIC: `scaffold_rule_facts.py` names the ones it was not given as a `--source`."""
    site = _load_site(repo_root)
    if site is None:
        return []
    found: list[Path] = [p for pattern in _LOADED_INSTRUCTION_GLOBS for p in sorted(repo_root.glob(pattern)) if p.is_file()]
    if site[1]:
        found += [repo_root / name for name in _LOADED_MEMORY_FILES if (repo_root / name).is_file()]
    return [(p.relative_to(repo_root).as_posix(), site[0]) for p in found]


def _declared_source_files(text: str, repo_root: Path) -> set[Path]:
    """The files the facts block's `instruction_sources` entries name (an entry for a file declares or declines it)."""
    block = _facts_block(_without_appendix(text))
    try:
        loaded = yaml.safe_load(block) if block is not None else None
    except yaml.YAMLError:
        return set()
    sources = loaded.get(INSTRUCTION_SOURCES_KEY) if isinstance(loaded, dict) else None
    paths = (str(e.get('path') or '').strip().removeprefix('./') for e in sources if isinstance(e, dict)) if isinstance(sources, list) else ()
    return {(repo_root / p).resolve() for p in paths if p}


def check_instruction_candidates(text: str, repo_root: Path) -> list[str]:
    """An instruction file the agent loads that the PRD neither declares as an `instruction_sources` entry nor declines.

    A skill file is the agent's own instruction text, so its rules are what the eval set owes a grade for. A PRD that
    declares only the code that sets the agent up has no rule for them, and nothing else reports the omission."""
    candidates = instruction_candidates(repo_root)
    if not candidates:
        return []
    declared = _declared_source_files(text, repo_root)
    return [
        f'instruction files: `{path}` is loaded by the agent (`{site}`) but the facts block neither declares it as an `{INSTRUCTION_SOURCES_KEY}` '
        f'entry nor declines it — pass it to `scaffold_rule_facts.py` as `--source {path}`, or, if the agent never reads it, decline the whole '
        f'file (`--decline {path}:1-<last line>={UNROWED_NOT_LOADED}@<the line that loads something else>`).'
        for path, site in candidates
        if (repo_root / path).resolve() not in declared
    ]


#: What may follow a sentence's final mark: closing quotes, brackets, a code span's backtick. The backend applies the same set.
_SENTENCE_CLOSERS = '"\'\u201d\u2019)]`\u00bb'
#: A line that opens a structural item (a list item, a table or bundle row, a heading) starts a new unit, whatever the
#: line before it ended with. The backend applies the same rule.
_NEW_ITEM = re.compile(r'^(?:(?:[-*\u2022]|\d+[.)])\s|\||#)')


def continues(before: str, line: str) -> bool:
    """Whether ``line``, on the line after ``before``, carries on ``before``'s sentence; the backend joins per-line rule facts back into clauses by this
    same rule. Not after a
    sentence or a table row ends; not into a structural item; and not into a capitalised line unless ``before`` ends on
    a comma or colon."""
    end, quote = before.rstrip(), line.strip()
    if end.rstrip(_SENTENCE_CLOSERS).endswith(('.', '!', '?')) or end.endswith('|') or _NEW_ITEM.match(quote):
        return False
    return not quote[:1].isupper() or end.endswith((',', ':'))


def split_units(lines: list[str], lo: int = 1, hi: int | None = None) -> list[tuple[int, int]]:
    """The 1-based ``(first, last)`` line spans of the rule units in ``lines[lo-1:hi]``: the one definition of a rule unit,
    used by `scaffold_rule_facts.py` to write one `rule` fact per unit and by the "too coarse" rule to count them.

    A unit is one instruction: a line, or the lines one wrapped sentence spans (``continues``). A blank line or a
    markdown heading ends a unit. A run with no letter in it is no instruction and is dropped."""
    units: list[tuple[int, int]] = []
    start = last = 0  # the open unit's first and last line; 0 when none is open

    def close() -> None:
        nonlocal start
        if start and any(_LETTER.search(line) for line in lines[start - 1 : last]):
            units.append((start, last))
        start = 0

    for n in range(lo, (len(lines) if hi is None else hi) + 1):
        line = lines[n - 1]
        if not line.strip() or _MD_HEADING.match(line):
            close()
        elif start and continues(lines[last - 1], line):
            last = n
        else:
            close()
            start = last = n
    close()
    return units


#: A harness CODE file (`plurai_sim/`, `plurai_sim_<component>/`: `simulate.py`, `_storage.py`, a bridge `replace`) — code
#: Plurai's setup wrote, never the agent's or the platform's. Its `artifacts/` and `probes/` hold measurements of the agent
#: (a rendered prompt, a probe's printout), which are evidence and stay citable.
_HARNESS_PATH = re.compile(r'(?<![\w/.-])(plurai_sim[\w-]*/(?!artifacts/|probes/)[\w./-]*\.(?:py|ts|tsx|js|mjs|cjs))(?![\w])')
#: The fact kind that states a run setting the HARNESS passes (`run_setting.harness_differs`): a fact about the harness,
#: so citing it is its point.
_HARNESS_FACT_KIND = 'run_setting'
#: A parenthesised citation that names one: `(plurai_sim/_storage.py WorldStateBackend.run_report)`, `(source: plurai_sim/x.py:12)`.
_HARNESS_CITE = re.compile(r'\(([^()]*?(?<![\w/.-])plurai_sim[\w-]*/[^()]*)\)')


def _source_is_harness(where: str, quote: str, path: str) -> str:
    return (
        f'source_is_harness: {where} "{quote[:110]}" cites `{path}`, a harness file, as the source of platform behaviour. The '
        'harness only translates and delegates, so it is never evidence of what the platform does: cite the agent\'s code or the '
        'platform\'s own documentation, or state the behaviour as a coverage hole.'
    )


def check_harness_cited_as_source(text: str, config: dict) -> list[str]:
    """``source_is_harness``: a PRD statement, a ``## Agent facts`` entry or a ``business_rules`` / ``error_cases`` entry that
    cites a path under the harness directory as the source of what the platform does. Prose is read line by line outside
    fences and the verbatim appendix, and counts only a parenthesised citation; a fact counts by its ``source`` /
    ``evidence``; a rule by its trailing ``(source: …)``. Advisory: an open finding, accepted with a reason."""
    findings: list[str] = []
    body = _without_appendix(text)
    lines = body.splitlines()
    # Paragraphs, because a citation wraps across lines; table rows, which `_paragraphs` leaves out, one by one.
    rows = [line.strip() for line, in_fence in zip(lines, _fenced(lines)) if not in_fence and line.strip().startswith('|')]
    for paragraph in [*_paragraphs(body), *rows]:
        for cite in _HARNESS_CITE.finditer(paragraph):
            if path := _HARNESS_PATH.search(cite.group(1)):
                findings.append(_source_is_harness('the PRD says', paragraph, path.group(1)))
    entries, _parse = _facts_entries(text)
    for entry in (e for e in entries or [] if isinstance(e, dict) and e.get('kind') != _HARNESS_FACT_KIND):
        for key in ('source', 'evidence'):
            values = entry.get(key) if isinstance(entry.get(key), list) else [entry.get(key)]
            for value in (v for v in values if isinstance(v, str)):
                if path := _HARNESS_PATH.search(value):
                    findings.append(_source_is_harness(f'the fact `{entry.get("subject")}.{entry.get("predicate")}`', value, path.group(1)))
    tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
    for name, entry in tools.items():
        descriptor = entry.get('descriptor') if isinstance(entry, dict) and isinstance(entry.get('descriptor'), dict) else {}
        rules = [r for r in descriptor.get('business_rules') or [] if isinstance(r, str)] if isinstance(descriptor.get('business_rules'), list) else []
        rules += [c['condition'] for c in descriptor.get('error_cases') or [] if isinstance(c, dict) and isinstance(c.get('condition'), str)] \
            if isinstance(descriptor.get('error_cases'), list) else []
        for rule in rules:
            for cite in _HARNESS_CITE.finditer(rule):
                if path := _HARNESS_PATH.search(cite.group(1)):
                    findings.append(_source_is_harness(f'a rule of tools.{name}', rule, path.group(1)))
    return list(dict.fromkeys(findings))


def check_coarse_rules(text: str, repo_root: Path) -> list[str]:
    """A `rule` fact whose quote holds several rule units (``split_units``) of an instruction file is a group quoted as
    one rule: it satisfies coverage, and every rule but one gets no owner of its own. One rule per fact."""
    entries, _parse = _facts_entries(text)
    findings: list[str] = []
    for rule in (e for e in entries or [] if isinstance(e, dict) and str(e.get('kind')) == RULE_KIND):
        cited = _citation(rule.get('source'))
        file = _resolve(cited[0], repo_root) if cited else None
        if cited is None or file is None:
            continue
        lo, hi = cited[1]
        try:
            body = file.read_text(encoding='utf-8', errors='replace').splitlines()[lo - 1 : hi]
        except OSError:
            continue  # the coverage check names the unreadable file
        units = len(split_units(body))
        if units > 1:
            findings.append(
                f'rule fact too coarse: `{cited[0]}:{lo}-{hi}` quotes {hi - lo + 1} lines in {units} separate units '
                f'(instructions, list items) as one rule — split it into one rule per fact, each cited at its own lines '
                f'(`scaffold_rule_facts.py` does)'
            )
    return findings


def check_measured_facts(text: str, repo_root: Path, art: Path) -> tuple[list[str], str]:
    """`(findings, status)`: a measured fact the scan's own inputs settle (a closed URL set, a URL the instructions name
    that no set holds, a topic the instructions call out of scope that no corpus document mentions) that no fact of
    the PRD types. Reads the files `artifacts/data_profile.md` names and the declared `instruction_sources`; a PRD with
    neither is SKIPPED, and so is one whose facts block cannot be read (`check_facts` reports that)."""
    profile = art / 'data_profile.md'
    entries, _parse = _facts_entries(text)
    if not profile.is_file() or entries is None:
        return [], 'SKIPPED (no data_profile.md or no facts block)'
    block = _facts_block(_without_appendix(text)) or ''
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        loaded = None
    declared = loaded.get(INSTRUCTION_SOURCES_KEY) if isinstance(loaded, dict) else None
    sources = [str(s.get('path')).removeprefix('./') for s in declared or [] if isinstance(s, dict) and s.get('path')]
    found = measured_candidates.candidates(repo_root, profile.read_text(encoding='utf-8', errors='replace'), sources)
    open_ = [c for c in found if not measured_candidates.is_typed(c, entries)]
    return [measured_candidates.finding_text(c) for c in open_], f'{len(found) - len(open_)} of {len(found)} typed'


def _class_definitions(repo_root: Path) -> dict[str, list[tuple[str, ast.ClassDef]]]:
    """Every `class` of the agent's Python code by name, as ``[(path, node)]``; the tree pruned as `_load_site` prunes it."""
    found: dict[str, list[tuple[str, ast.ClassDef]]] = {}
    seen = 0
    for base, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _NOT_AGENT_CODE)
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            seen += 1
            path = Path(base) / name
            try:
                if seen > _CODE_FILE_LIMIT or not path.is_file() or path.stat().st_size > _CODE_BYTES_LIMIT:
                    continue
                tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
            except (OSError, SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    found.setdefault(node.name, []).append((path.relative_to(repo_root).as_posix(), node))
    return found


def state_fields(state_class: str, repo_root: Path) -> dict[str, str] | None:
    """``{field: path:LINE}`` of the agent's state class (`agent.state_class`), or None when it does not resolve to exactly
    one definition. A dotted `pkg.mod.Class` is looked up in its module, a bare name in the whole tree. The fields are the
    class body's annotated names plus those of every base defined once in the repo; a base from a library adds none."""
    defs = _class_definitions(repo_root)
    module, _, name = state_class.strip().rpartition('.')
    dotted = {p: p.removesuffix('.py').removesuffix('/__init__').replace('/', '.') for p, _n in defs.get(name, [])}
    mine = [(p, n) for p, n in defs.get(name, []) if not module or dotted[p] == module or dotted[p].endswith('.' + module)]
    if len(mine) != 1:
        return None
    fields: dict[str, str] = {}
    todo, done = [mine[0]], set()
    while todo:
        path, node = todo.pop()
        if id(node) in done:
            continue
        done.add(id(node))
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.setdefault(stmt.target.id, f'{path}:{stmt.lineno}')
        for parent in node.bases:
            parent_name = parent.id if isinstance(parent, ast.Name) else parent.attr if isinstance(parent, ast.Attribute) else ''
            if len(defs.get(parent_name, [])) == 1:
                todo.append(defs[parent_name][0])
    return fields


def check_state_seedability(text: str, config: dict, repo_root: Path) -> list[str]:
    """A `seedability` fact calling a field of the agent's own state class (`agent.state_class`) `not_seedable`: the harness
    writes that state at turn 0, so the fact is false, and the backend reads it as a premise no scenario can reach. The
    subject is the bare field, `<Class>.<field>` or `state.<field>`. Likewise a subject `<type>[.<field>]` naming a declared
    world type the mock holds: the scenario seeds that type's rows. A harness-owned type (route H: `owned_types`, or every
    type under `owner: agent` / `external`) MAY be declined, when the fact's `source` cites the line that makes it
    unseedable (:func:`_cites_a_harness_line`); uncited, it is refused."""
    state_class = (config.get('agent') or {}).get('state_class') if isinstance(config.get('agent'), dict) else None
    entries, _parse = _facts_entries(text)
    claims = [
        (str(e.get('subject') or '').strip(), e.get('source')) for e in entries or []
        if isinstance(e, dict) and e.get('kind') == 'seedability' and e.get('predicate') == 'not_seedable'
    ]
    if not claims:
        return []
    has_class = isinstance(state_class, str) and bool(state_class.strip())
    fields = (state_fields(state_class, repo_root) if has_class else None) or {}
    name = state_class.strip().rpartition('.')[2] if has_class else ''
    world = config.get('world_state') if isinstance(config.get('world_state'), dict) else {}
    owned_value, types_value = world.get('owned_types'), world.get('entity_types')
    owned = {t for t in owned_value if isinstance(t, str)} if isinstance(owned_value, list) else set()
    world_types = owned | ({str(t) for t in types_value} if isinstance(types_value, dict) else set())
    harness_owned = str(world.get('owner') or '').strip().lower() in ('agent', 'external')
    route_h = owned if isinstance(owned_value, list) else (world_types if harness_owned else set())
    findings = []
    for subject, source in claims:
        field = next((f for f in fields if subject in (f, f'{name}.{f}', f'{state_class.strip()}.{f}', f'state.{f}')), None)
        world_type = subject.split('.', 1)[0]
        if field is not None:
            findings.append(
                f'facts: `{subject}` is `not_seedable`, but it is a field of `{state_class.strip()}` (`{fields[field]}`), the state the '
                'harness writes in `seed_state`. Declare it in `plurai.config.yaml` `agent_context.fields` (setup Step 4b); '
                '`not_seedable` is for state only a live external system writes.'
            )
        elif world_type in route_h and subject == world_type:
            if not _cites_a_harness_line(source, repo_root):
                findings.append(
                    f"facts: `{subject}` is `not_seedable`, but `{world_type}` is a declared world type (`world_state.owned_types`: the "
                    "harness's own store, written in `seed_state`) and the fact cites no line that makes it unseedable. Cite it — "
                    '`source:` the `plurai_sim/` harness line (`path:LINE`), or the declared lane that carries it (`.document_fields`) — '
                    'or deliver the type (`world_schema.py` writes its block).'
                )
        elif world_type in world_types:
            store = "`world_state.owned_types`: the harness's own store" if world_type in route_h else "`world_state.entity_types`: the mock's world"
            findings.append(
                f'facts: `{subject}` is `not_seedable`, but `{world_type}` is a declared world type ({store}), so the scenario seeds its '
                'rows. Declare the field on the type (a harness-owned type is written in `seed_state`); '
                '`not_seedable` is for state only a live external system writes.'
            )
    return findings


#: Prose-depth signals, each a cheap structural trace of one kind of required content (see
#: `references/prd_authoring_contract.md`, "Required prose content"). They read shapes, never
#: the agent's vocabulary: a turn marker, an ordered list, a table, a heading that names a kind
#: of content. A PRD that shows fewer than `PROSE_SIGNALS_NEEDED` of them gets a hint.
_TURN_MARKER = re.compile(r'^\s*(?:[-*]\s+)?\**(?:user|customer|agent|assistant|human|bot|caller)\b[^:\n]{0,24}:', re.IGNORECASE)
_ORDERED_ITEM = re.compile(r'^\s*\d+[.)]\s+\S')
_TABLE_RULE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')
_LABEL = re.compile(r'^\s*(?:#{2,5}\s+|(?:[-*]\s+|\d+[.)]\s+)?\*\*)(?P<label>[^\n]{0,120})')
_CONTENT_LABEL = re.compile(
    r'\b(?:hard limits?|limits?|boundar(?:y|ies)|cannot|can\'t|absen(?:ce|t|ces)|catalog(?:ue)?|vocabular(?:y|ies)|closed|'
    r'procedures?|examples?|dialogues?|exchanges?|happy path)\b',
    re.IGNORECASE,
)
PROSE_SIGNALS = (
    'example exchanges (turn-marked lines)',
    'procedures (ordered steps)',
    'a closed catalog (a table)',
    'a heading or label naming limits, absences, a catalog or a vocabulary',
)
PROSE_SIGNALS_NEEDED = 2


def _prose_lines(text: str) -> list[str]:
    """The PRD's authored prose: every line before the verbatim appendix except the `## Agent facts` section (the
    scaffold writes it) and the directives section (steering, not ground truth)."""
    lines = _without_appendix(text).splitlines()
    kept: list[str] = []
    skipping = False
    for line, fenced in zip(lines, _fenced(lines)):
        if not fenced and re.match(r'^##\s', line):
            skipping = _is_facts_heading(line) or _is_design_heading(line)
        if not skipping:
            kept.append(line)
    return kept


def _normalised(line: str) -> str:
    """A line as compared against the appendix: trimmed, runs of whitespace collapsed, and empty when nothing but
    markdown markers (a bullet, a rule, a quote bar, a fence, a bare list number) is left."""
    flat = ' '.join(line.split())
    return flat if re.search(r'[^\W_]', re.sub(r'^\d+[.)]$', '', flat)) else ''


def _appendix_lines(text: str) -> set[str]:
    """Every normalised line the verbatim appendix carries, which are the corpus documents' own lines."""
    body = _without_appendix(text).splitlines()
    return {n for n in map(_normalised, text.splitlines()[len(body):]) if n}


def _copied_flags(prose: list[str], carried: set[str]) -> list[bool]:
    """Per prose line: it repeats a line the appendix carries. A table's rule row inherits the verdict of its header
    row, because the rule row is the same bytes in every table and says nothing about whose table it is."""
    flags: list[bool] = []
    for i, line in enumerate(prose):
        if _TABLE_RULE.match(line):
            flags.append(i > 0 and flags[i - 1])
        else:
            flags.append(_normalised(line) in carried and bool(_normalised(line)))
    return flags


def check_prose_depth(text: str) -> tuple[list[str], str]:
    """`(missing, status)`: the prose-depth signals the PRD's authored prose lacks, and the count it shows.

    A HINT, never a finding: whether a section says enough about an agent is not decidable from its shape, but a PRD
    whose prose shows none of the traces of hard limits, closed catalogs, procedures or example exchanges is the
    thin one, and the author wants to hear it once. Four shapes, agent-independent; fewer than two present means
    the prose is a few sentences per section. Lines that repeat a line of the verbatim appendix are not read: a
    corpus document pasted into the prose carries every shape and says nothing the agent's own instructions do.
    SKIPPED when the PRD holds no authored prose to read."""
    all_prose = _prose_lines(text)
    if not any(line.strip() for line in all_prose):
        return [], 'SKIPPED (no prose)'
    own = [line for line, copied in zip(all_prose, _copied_flags(all_prose, _appendix_lines(text))) if not copied]
    present = [
        sum(bool(_TURN_MARKER.match(line)) for line in own) >= 2,
        sum(bool(_ORDERED_ITEM.match(line)) for line in own) >= 3,
        sum(bool(_TABLE_RULE.match(line)) for line in own) >= 1,
        any(m and _CONTENT_LABEL.search(m.group('label')) for m in map(_LABEL.match, own)),
    ]
    missing = [name for name, ok in zip(PROSE_SIGNALS, present) if not ok]
    return (missing if sum(present) < PROSE_SIGNALS_NEEDED else []), f'{sum(present)} of {len(PROSE_SIGNALS)} signals'


#: A prose line shorter than this is too small to be a copy of anything (a label, a step verb, a table cell).
DUPLICATE_MIN_CHARS = 15
#: The hint fires at this many repeated lines, or at a quarter of the prose when that is at least `DUPLICATE_FLOOR`.
DUPLICATE_LINES = 15
DUPLICATE_SHARE = 0.25
DUPLICATE_FLOOR = 6


def prose_duplicates(text: str) -> tuple[int, int, str]:
    """`(copied, total, first_heading)`: the non-trivial prose lines that repeat a line of the verbatim appendix, all
    the non-trivial prose lines, and the first copied heading (else the heading above the first copied line)."""
    prose = _prose_lines(text)
    flags = _copied_flags(prose, _appendix_lines(text))
    sized = [(line, copied) for line, copied in zip(prose, flags) if len(_normalised(line)) >= DUPLICATE_MIN_CHARS]
    first = ''
    last_heading = ''
    for line, copied in sized:
        if re.match(r'^\s*#{1,6}\s', line):
            last_heading = line.strip()
            if copied:
                first = first or last_heading
        elif copied and not first:
            first = last_heading
    return sum(copied for _, copied in sized), len(sized), first


def duplicate_prose_hint(copied: int, total: int, heading: str) -> str:
    """The hint text, or '' when the copies are too few to matter."""
    if copied < DUPLICATE_LINES and not (copied >= DUPLICATE_FLOOR and copied >= DUPLICATE_SHARE * total):
        return ''
    where = f' The first copied block starts at "{heading}".' if heading else ''
    return (
        f'prose depth: {copied} of {total} prose lines repeat lines of the verbatim appendix.{where} Text copied from '
        'the appendix into the prose adds no depth and is not counted: the appendix already carries it. State the agent\'s own procedures '
        'and vocabularies from its instructions and code with `path:LO-HI` citations, and summarise a corpus procedure in one '
        'line. A hint only: it is no finding and changes no exit code.'
    )


def prose_depth_hint(missing: list[str]) -> str:
    return (
        'prose depth: the PRD\'s prose shows too few traces of the required content (the agent\'s hard limits, the closed '
        'vocabularies and catalogs it stays inside, what it cannot do, the procedures it states, example exchanges). '
        f'Not found: {"; ".join(missing)}. A thin section reaches scenario generation as thin ground truth; '
        'see "Required prose content" in references/prd_authoring_contract.md. A hint only: it is no finding and changes no exit code.'
    )


#: The predicate a `detector:` block is legal on. A detector is a claim about EXHAUSTIVENESS, and
#: no other predicate makes one — ONE spelling with the backend's `PREDICATE_CLOSED_SET`
#: and with `scan_facts.py`'s projection.
CLOSED_SET_PREDICATE = 'closed_set'

#: The rungs a `detector.candidate` may name. Closed, so a typo reaches the author here rather
#: than the backend, where it would name a fact they have to trace back to this file.
DETECTOR_RUNGS = ('shape', 'induced', 'near_miss')


def _detector_key_findings(entry: dict, *, where: str, subject: str, predicate: str) -> list[str]:
    """Shape checks for the optional `detector:` block — the one that says whether this closed set
    MUST be checked in code, and supplies what proves the check works.

    A closed set licenses a judge to treat a value outside the list as one the sources never
    declared. Until the detector ladder existed only `url` had anything in code behind that, and
    nobody had to say so — which is the failure this key exists to end. `required: false` remains a
    legitimate answer; it just has to be an answer.

    Every check here fails CLOSED, because each silent default is the wrong one:

    * `required` must be a real boolean — `required: "false"` is truthy, and read loosely it turns
      "judge-only by declaration" into a hard gate, or the reverse, with nothing saying which.
    * `negatives` must carry at least one literal when `required` is true: without one the
      positive-control gate cannot run, so the requirement could never fail. That is a rubber stamp.
    * the block is legal only on `predicate: closed_set`. Anywhere else it is an author reaching
      for a guarantee the predicate cannot carry.
    """
    if 'detector' not in entry:
        return []
    value = entry['detector']
    if predicate != CLOSED_SET_PREDICATE:
        return [
            f'facts: {where} ({subject}) carries `detector:` on predicate `{predicate or "(none)"}`. A detector is a claim about '
            f'EXHAUSTIVENESS, so the block is only meaningful on `{CLOSED_SET_PREDICATE}` — the backend refuses it elsewhere.'
        ]
    if not isinstance(value, dict):
        return [f'facts: {where} ({subject}) has `detector:` that is not a mapping — write `detector: {{required: true, negatives: ["..."]}}`.']
    findings: list[str] = []
    required = value.get('required')
    if not isinstance(required, bool):
        findings.append(
            f'facts: {where} ({subject}) has `detector.required: {required!r}`, which is not a real boolean. A quoted "false" is '
            f'TRUTHY, so the loose reading turns "closed, judge-only by declaration" into a hard gate — write `true` or `false`.'
        )
    negatives = value.get('negatives') or []
    if not isinstance(negatives, list) or any(not isinstance(n, str) or not n.strip() for n in negatives):
        findings.append(f'facts: {where} ({subject}) has `detector.negatives` that is not a list of non-blank literals the detector MUST catch.')
        negatives = []
    if required is True and not negatives:
        findings.append(
            f'facts: {where} ({subject}) requires a detector but declares no `detector.negatives`. At least one literal that is NOT '
            f'a member is what the positive-control gate runs against; without one the requirement cannot fail, which is a rubber stamp.'
        )
    candidate = value.get('candidate')
    if candidate is not None:
        if not isinstance(candidate, dict):
            findings.append(f'facts: {where} ({subject}) has `detector.candidate:` that is not a mapping.')
        elif str(candidate.get('rung') or '') not in DETECTOR_RUNGS:
            findings.append(
                f'facts: {where} ({subject}) has `detector.candidate.rung: {candidate.get("rung")!r}`; the closed set is {", ".join(DETECTOR_RUNGS)}.'
            )
    return findings


def _fields_key_findings(entry: dict, *, where: str, subject: str, kind: str) -> list[str]:
    """Shape checks for the optional `fields:` key — the one that makes a fact SUBTRACT.

    Only the shape is decidable here: whether each name is a real `output_field` of the subject's
    tool needs plurai.config.yaml, and the backend already ignores an entry that is not one. What
    a lint CAN catch is the key being unusable — a bare string instead of a list, a non-string
    member, or the key on a kind whose facts never reach the answerability fence — because those
    are silently inert, and inert is exactly the state this key exists to end.
    """
    if 'fields' not in entry:
        return []
    value = entry['fields']
    if kind != 'tool_surface':
        return [f'facts: {where} ({subject}) carries `fields:` on kind `{kind}`. Only `tool_surface` facts bear on the answerability fence, so it is ignored.']
    if not isinstance(value, list) or not value:
        return [f'facts: {where} ({subject}) has `fields:` that is not a non-empty list — write it as `fields: [description, image_url]`. As authored it is ignored.']
    bad = [repr(f) for f in value if not isinstance(f, str) or not f.strip()]
    if bad:
        return [f'facts: {where} ({subject}) has non-name entries in `fields:` ({", ".join(bad)}). Each must be a declared output_field of `{subject.split(".", 1)[0]}`.']
    return []


def declared_render_actions(config: dict) -> list[str] | None:
    """The `render_actions:` declaration, or None when the config carries none (or a malformed one —
    `verify_config.py` fails that shape; here it is simply not a declaration)."""
    actions = config.get(RENDER_ACTIONS_KEY)
    if not isinstance(actions, list):
        return None
    return [str(a).strip() for a in actions if isinstance(a, str) and a.strip()]


def check_tool_coverage(text: str, config: dict) -> list[str]:
    tools = config.get('tools')
    names: list[str] = []
    if isinstance(tools, dict):
        names = [str(k) for k in tools]
    elif isinstance(tools, list):
        names = [str(t.get('name')) for t in tools if isinstance(t, dict) and t.get('name')]

    def _undocumented(candidates: list[str]) -> list[str]:
        # Word-boundary, not substring: `search` is contained in "research", which silently
        # marked an undocumented tool as covered.
        return sorted(n for n in candidates if n and not re.search(rf'\b{re.escape(n)}\b', text))

    findings: list[str] = []
    if missing := _undocumented(names):
        findings.append(
            f'coverage: {len(missing)} declared tool(s) are named nowhere in the PRD: '
            f'{missing}. Each is either a flow the suite will never author, or a contract '
            f'no judge can grade. Disposition each — documenting one as out of scope is a valid answer.'
        )
    # The same question over the OTHER surface. Render actions are not tools — they have no
    # dispatch — but a judge grades them exactly as it grades a tool call, and one the PRD never
    # names is one no judge can grade.
    if missing := _undocumented(declared_render_actions(config) or []):
        findings.append(
            f'coverage: {len(missing)} declared render action(s) (`{RENDER_ACTIONS_KEY}:`) are named nowhere in the PRD: '
            f'{missing}. A render the PRD never describes is one no judge can grade the agent for using — or for inventing.'
        )
    return findings


def check_render_surface(text: str, config: dict) -> list[str]:
    """The typed render surface against the fact that CLOSES it — both ways.

    `render_actions:` says what the agent HAS; the `tool_surface` / `RENDER_ACTIONS` / `omits`
    fact says what it has NOT, and its absence `check` is what the scan contract executes. Neither
    half means much alone: a declared list with no closing fact leaves an invented verb ungraded
    (the `show_video` an agent with no video render kept emitting), and a closing fact over an
    undeclared surface is a claim about a list nobody wrote down. Every finding here is advisory,
    like coverage — but each one names the exact key or fact to write.
    """
    declared = declared_render_actions(config)
    entries, _parse = _facts_entries(text)
    closing = [
        e for e in (entries or [])
        if isinstance(e, dict) and str(e.get('kind')) == 'tool_surface'
        and str(e.get('subject')) == RENDER_SURFACE_SUBJECT and str(e.get('predicate')) == 'omits'
    ]
    findings: list[str] = []
    if declared is None:
        if closing:
            findings.append(
                f'render surface: "## Agent facts" closes `{RENDER_SURFACE_SUBJECT}` (a `tool_surface` / `omits` fact) but '
                f'plurai.config.yaml declares no `{RENDER_ACTIONS_KEY}:` — the fact states what the agent lacks against a '
                f'surface nobody wrote down. Declare the list (`{RENDER_ACTIONS_KEY}: [...]`, `[]` for none).'
            )
        return findings
    if not closing:
        findings.append(
            f'render surface: plurai.config.yaml declares `{RENDER_ACTIONS_KEY}: {declared}` but no '
            f'`tool_surface` / `{RENDER_SURFACE_SUBJECT}` / `omits` fact closes the surface — nothing states the agent has '
            f'no OTHER render action, so one it invents is graded by nobody. Write the fact with a '
            f'`check: {{kind: absence, terms: [...]}}` naming the verbs the agent does NOT have.'
        )
        return findings
    for fact in closing:
        check = fact.get('check')
        if not isinstance(check, dict):
            findings.append(
                f'render surface: the `{RENDER_SURFACE_SUBJECT}` / `omits` fact carries no `check:` — it closes the surface '
                f'as prose only, which the scan contract cannot execute. Add the absence sweep with its terms.'
            )
            continue
        terms = {str(t).strip() for t in (check.get('terms') or []) if str(t).strip()}
        if overlap := sorted(terms & set(declared)):
            findings.append(
                f'render surface: the `{RENDER_SURFACE_SUBJECT}` / `omits` fact sweeps for {overlap}, which '
                f'`{RENDER_ACTIONS_KEY}:` declares the agent HAS — the fact and the declaration contradict each other; '
                f'one of them is wrong.'
            )
    return findings


def check_cited_links(text: str, config: dict) -> list[str]:
    """A link-absence fact against a retrieval that hands the agent a link for what it cites.

    `observation_lane: tool_trail_cited` is the typed lane for "this retrieval returns a reference
    for each unit it cites". An `absent_from_source` fact whose terms are URL shapes sweeps the
    corpus alone, so it would grade that cited link as an invented one. Advisory, like its sibling.
    """
    tools = config.get('tools')
    entries = tools.items() if isinstance(tools, dict) else ((t.get('name'), t) for t in tools if isinstance(t, dict)) if isinstance(tools, list) else ()
    citing = sorted(str(n) for n, t in entries if isinstance(t, dict) and str(t.get('observation_lane')) == 'tool_trail_cited')
    if not citing:
        return []
    facts, _parse = _facts_entries(text)
    findings: list[str] = []
    for fact in facts or []:
        if not isinstance(fact, dict) or str(fact.get('predicate')) != 'absent_from_source':
            continue
        check = fact.get('check')
        terms = [str(t).strip() for t in (check.get('terms') or [])] if isinstance(check, dict) else []
        if urls := [t for t in terms if '://' in t or t.lower().startswith('www.')]:
            findings.append(
                f'cited links: {", ".join(citing)} cites what it retrieves (`observation_lane: tool_trail_cited`), so every '
                f'cited answer carries a link to its source — a link IS in what the agent receives. The '
                f'`{fact.get("subject")}` absent_from_source fact sweeps {urls} over the corpus alone and would grade that '
                f'citation as invented. Drop the fact (or drop its link terms).'
            )
    return findings


# A CODE CITATION, in the one shape the authoring rules ask for: backticked `path:line`, with
# a file extension and a line number (a range is allowed; only its start is resolved).
# Backticked ONLY — an unfenced "see runner.py:12" in prose is rare and the pattern would then
# have to guess at sentence punctuation. `[\w./\-]` cannot cross the `:` in a URL scheme, so
# `https://host/a.py:80` never matches.
_CODE_CITATION = re.compile(r'`([\w./\-]+\.[A-Za-z][\w]{0,4}):(\d+)(?:-\d+)?`')

#: The other half of the contract, counted for the ratio in the status line.
_INFERRED_MARKER = re.compile(r'\(inferred\)', re.IGNORECASE)


def _citations(text: str) -> list[tuple[str, str]]:
    """Every DISTINCT `(path, line)` citation, in order of first appearance.

    One definition of "distinct", because the resolver below and the ratio in the status line
    both count these and a PRD that cites the same line twice must not read as two citations.
    """
    return list(dict.fromkeys(_CODE_CITATION.findall(text)))


#: Sections whose claims describe MECHANISM — what the code does when the agent acts. A rule in
#: "Agent Conversation Patterns" is about dialogue and carries no such obligation.
_MECHANISM_SECTIONS = re.compile(r'^\s{0,3}#{2,6}\s*\d*\.?\s*(entity schemas|tool specifications|business rules)', re.IGNORECASE)

#: Verbs that assert what the implementation DOES. Deliberately narrow: each names a state
#: change or an identity rule that a reader could only know by reading code or probing — not
#: the descriptive vocabulary a PRD uses to introduce a field ("holds", "contains", "is a").
#: Third-person VERB forms only. The bare singular is a noun in this domain ("in the last
#: commit", "the write path"), and matching it read a quoted trigger phrase out of one customer's
#: system prompt as a mechanism claim.
_MECHANISM_VERB = re.compile(
    r'\b(commits\b|(?:is|are|was|were|gets?)\s+committed\b|auto-?commits\b|persists\b|writes (?:to|a|the)\b|overwrites\b|mutates\b|'
    r'renumbers\b|reindexes\b|clones\b|resolves to\b|is immutable\b|are immutable\b|immutably\b|'
    r'appends a new\b|increments\b|rolls back\b|truncates\b|clamps\b|caps (?:at|the)\b|'
    # A WRITER named in the passive ("when committed by `optimize_evaluator`") is a write-path claim: it
    # shipped in an entity-table row while a shared builder wrote the field on every other commit too.
    r'(?:set|written|committed|populated)\s+(?:only\s+)?by\b)',
    re.IGNORECASE,
)

#: A markdown table's delimiter row (`|---|:--:|`), which carries no claim.
_TABLE_RULE = re.compile(r'^\|?[\s:|-]+\|?\s*$')


def _table_rows(lines: list[str]) -> list[str]:
    """Each table row outside a fence as ONE claim, its cells joined by ` | `.

    `_paragraphs` drops every `|` line, so an entity-table description ("the best score, when committed by
    `optimize_evaluator`") was never read by any claim check. A row is its own unit: joining rows would let
    one row's citation clear the next row's claim.
    """
    return [
        ' | '.join(cell.strip() for cell in line.strip().strip('|').split('|'))
        for line, fenced in zip(lines, _fenced(lines))
        if not fenced and line.lstrip().startswith('|') and not _TABLE_RULE.match(line.strip())
    ]

#: Any heading the slicer below treats as a section boundary, with its DEPTH captured. Shares
#: `_MECHANISM_SECTIONS`' prefix on purpose: a line the two disagree about would open a section
#: that never closes, or close one that never opened. H2..H6 matches `_sections`' own H2/H3
#: convention — a lone `#` mid-body is not a boundary here.
_ANY_HEADING = re.compile(r'^\s{0,3}(#{2,6})\s')

#: A double-quoted run: the PRD QUOTING the agent's own prompt, a tool description or a UI
#: label. Reporting someone else's words as this PRD's unmarked claim is the same trade
#: `_without_appendix` makes for the verbatim appendix, and the same one `check_authored_law`
#: makes for blockquotes.
#:
#: Covers the ASCII pair `"…"` (what PRDs commonly quote with), the CURLY pair
#: `“…”` and the German `„…“` — an LLM-authored PRD routinely curls its quotes, and a class
#: that held only the ASCII pair reported those excerpts as this PRD's own claims. Single
#: quotes are still uncovered, deliberately: an apostrophe would open a span that swallows the
#: rest of the paragraph.
_QUOTED_SPAN = re.compile(r'[“„"]([^“”„"]{0,400})[”“"]|"([^"]{0,400})"')


def check_mechanism_claims(text: str) -> list[str]:
    """Mechanism claims in the code-facing sections that carry neither a citation nor `(inferred)`.

    The convention exists and is followed unevenly INSIDE one document: a shipped PRD cites
    `store.py:315` / `reranker.py:75` for its reranker claims while its entity-mutation claims
    shipped unmarked — and the unmarked ones are exactly what the pass-1 critic found wrong
    ("asserted immutability/mutation semantics with no (inferred) markers and three
    miscitations"). One PRD restated a tool's *'Save Version' UI wording* as the mechanism for
    `revision.committer`; the seeded state followed the actual resolver, and the scenarios job
    flagged `grounding_sources_conflict` two minutes after the batch whose Step-3b adoption the
    resulting edit forfeited.

    Both are the same shape: a claim about what the implementation DOES, written from the
    surface it was read on, with nothing recording which. This does not judge whether the claim
    is TRUE — that is the undecidable prose-vs-source check this file removed once. It asks only
    that the claim carry its evidence class, which is decidable and is the thing that was missing.

    PARAGRAPH-scoped, never line-scoped — the recurring hazard in this file (see `_paragraphs`):
    a citation routinely wraps onto the line after the claim it supports.

    PRDs are authored in English even for non-English agents, so the verb list holds; a
    Hebrew-quoted span simply does not match, which is a miss rather than a false positive.
    """
    body = _without_appendix(text)
    lines = body.splitlines()
    fenced = _fenced(lines)
    # Slice the mechanism sections out first, so a claim in a dialogue section is never read.
    #
    # DEPTH-AWARE, and that is the whole check: the PRD template puts every tool under
    # `### {tool_name}` and every entity under `### {EntityType}`, so treating any heading as a
    # section boundary ended the scan at the first subheading — on one shipped PRD that left SIX of
    # 880 lines and zero paragraphs, and the "real PRDs are clean" test passed by scanning
    # nothing. A section ends at the next heading of the SAME depth or shallower; a deeper one
    # is part of it.
    in_section = False
    section_depth = 0
    kept: list[str] = []
    for line, is_fence in zip(lines, fenced):
        heading = None if is_fence else _ANY_HEADING.match(line)
        if heading:
            if _MECHANISM_SECTIONS.match(line):
                in_section, section_depth = True, len(heading.group(1))
            elif len(heading.group(1)) <= section_depth:
                in_section = False
        if in_section:
            kept.append(line)
    findings: list[str] = []
    for paragraph in [*_paragraphs('\n'.join(kept)), *_table_rows(kept)]:
        # Quoted runs are the agent's own prompt/tool text being REPORTED, not the PRD's claim.
        match = _MECHANISM_VERB.search(_QUOTED_SPAN.sub(' ', paragraph))
        if not match:
            continue
        if _CODE_CITATION.search(paragraph) or _INFERRED_MARKER.search(paragraph):
            continue
        findings.append(
            f'mechanism claim: "{paragraph[:110]}" states what the implementation DOES ("{match.group(0)}") '
            f'with no `path:line` citation and no `(inferred)` marker. A mechanism read off a UI label or a '
            f'tool description rather than the code is how a PRD comes to contradict the seeded state — cite '
            f'the code, or mark it `(inferred)` so the reader knows which.'
        )
    return findings


# --- Mechanical claims: the ones that carry their own checkable anchor -----------------
#
# A PRD claim is normally prose about a repo the lint cannot adjudicate. Three shapes are the
# exception, because the claim NAMES what would falsify it, and all three shipped false in one
# scan-authored PRD and were corrected mid-loop at ~45 minutes of authoring: (a) a block declared
# a byte-for-byte copy of a named file that was in fact the RENDERED form (the `type:` line
# dropped); (b) a whole-file negative ("no try/except anywhere in the file") against a file with
# one; (c) a count ("six local files") that was seven.
#
# These do NOT preserve adoption — a design-constraint edit encodes a qualification-run lesson and
# forfeits it whenever it lands. The value is first-draw quality: the round that does not have to
# be spent.

#: A claim that a quoted block is the source's OWN BYTES. Deliberately narrow: `verbatim` ALONE is
#: not here, because a PRD legitimately heads a section "the visual asset catalog, verbatim" over a
#: RENDERED view and says so in the next sentence, and because §5.5's "Copied in order with the
#: guide's own control names" is a faithful-order claim rather than a byte claim. What distinguishes
#: the checkable class is an assertion about BYTES.
_BYTE_EXACT_CLAIM = re.compile(
    r'\bbyte[- ]for[- ]byte\b|\bbyte[- ]identical\b|\bcharacter[- ]for[- ]character\b'
    # `the bytes of` was here and is REMOVED: it fires on ordinary mechanism prose ("logs the
    # bytes of each reply"), which is the ignorable-lint direction this file's header warns about.
    r'|\bcopied verbatim\b|\bverbatim cop(?:y|ies)\b|\bexact bytes\b',
    re.IGNORECASE,
)

#: A backticked path, with or without the `:LINE` suffix `_CODE_CITATION` requires. Same character
#: class, so a URL scheme's `:` is still uncrossable.
_BACKTICKED_PATH = re.compile(r'`([\w./\-]+\.[A-Za-z][\w]{0,4})(?::\d+(?:-\d+)?)?`')


def _resolve(ref: str, repo_root: Path) -> Path | None:
    """``ref`` as a file under ``repo_root``, or None.

    The PRD names the file, so the lint reads only inside the agent's repo, as the scaffold does (``read_sources``):
    an absolute path, a ``..`` and a symlink that leaves the root resolve to nothing, and their lines never reach a
    finding."""
    candidate = repo_root / ref
    try:
        if not candidate.is_file() or not candidate.resolve().is_relative_to(repo_root.resolve()):
            return None
    except OSError:
        return None
    return candidate


def _lead_in(lines: list[str], fence_at: int) -> str:
    """The prose paragraph immediately above the fence opening at ``fence_at``.

    Walks back over blank lines, then collects the contiguous prose run. Positional, because the
    claim and the block it is about are related by ADJACENCY and `_paragraphs` discards position.

    The HEADING that opens the run is included: a section titled "the catalog, byte-for-byte from
    assets.yaml" is the idiomatic markdown placement for exactly this claim, and stopping below
    the heading made that shape invisible with no counter-signal.
    """
    i = fence_at - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    end = i
    while i >= 0 and lines[i].strip() and not lines[i].lstrip().startswith(('#', '>', '|')):
        i -= 1
    if i >= 0 and lines[i].lstrip().startswith('#'):
        i -= 1  # take the heading too
    return ' '.join(line.strip() for line in lines[i + 1 : end + 1])


def _fence_blocks(lines: list[str], fenced: list[bool]) -> list[tuple[int, list[str]]]:
    """`(opening line index, body lines)` for every fenced block, delimiters excluded.

    A block left OPEN at end of document still yields its body: an unterminated fence has no
    closing delimiter to strip, and the closed case is handled by the bare-delimiter branch below.
    """
    out: list[tuple[int, list[str]]] = []
    start: int | None = None
    for i, inside in enumerate(fenced):
        if inside and start is None:
            start = i
            continue
        # A CLOSING delimiter ends the block even when the very next line opens another one:
        # `_fenced` marks both as inside, so waiting for a False here merged two blocks into one
        # body containing the delimiters between them — and the finding then cited "```" as the
        # first line that did not match.
        if inside and start is not None and start != i and _FENCE_BARE.match(lines[i]):
            out.append((start, lines[start + 1 : i]))
            start = None
            continue
        if not inside and start is not None:
            out.append((start, lines[start + 1 : i - 1]))
            start = None
    if start is not None:
        out.append((start, lines[start + 1 :]))
    return out


def check_byte_exact_quotes(text: str, repo_root: Path) -> list[str]:
    """Blocks DECLARED byte-exact copies of a named file that are not contiguous in that file.

    A "byte-for-byte" claim is the definition of checkable — the author has named the falsifier —
    and the shipped instance was false in the cheapest possible way: the block was what
    `load_assets()` RENDERS from the manifest, one field short of the manifest's own bytes. A
    reader who trusts the label reads a field that is not there into the agent's ground truth.

    Leading indentation the markdown added is allowed off (a common prefix on every line), because
    that is a property of the embedding and not of the source. Nothing else is.
    """
    body = _without_appendix(text)
    lines = body.splitlines()
    fenced = _fenced(lines)
    findings: list[str] = []
    for start, block in _fence_blocks(lines, fenced):
        if not block:
            continue
        lead = _lead_in(lines, start)
        if not _BYTE_EXACT_CLAIM.search(lead):
            continue
        # AS WRITTEN, or with a common prefix removed — whichever matches. Dedenting
        # unconditionally would break the correctly-indented case, since the source's own
        # indentation is part of its bytes; trying both costs one comparison. Both are
        # properties of the BLOCK, so they are computed once rather than per candidate path.
        indent = min((len(ln) - len(ln.lstrip()) for ln in block if ln.strip()), default=0)
        quoted = '\n'.join(block).strip('\n')
        dedented = '\n'.join(ln[indent:] if ln.strip() else '' for ln in block).strip('\n')
        for ref in dict.fromkeys(_BACKTICKED_PATH.findall(lead)):
            source = _resolve(ref, repo_root)
            if source is None:
                continue  # unresolvable is `check_citations`' finding, not a second one here
            try:
                haystack = source.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n')
            except OSError:
                continue
            if quoted and quoted not in haystack and dedented not in haystack:
                missing = next((ln.strip() for ln in quoted.splitlines() if ln.strip() and ln.strip() not in haystack), '')
                findings.append(
                    f'byte-exact claim: the block below "{lead[:90]}" is declared a byte-exact copy of `{ref}`, but '
                    f'its bytes are not in that file' + (f' (first line that is not: "{missing[:70]}")' if missing else '') +
                    '. Either quote the file\'s own bytes, or say what the block IS — a rendered view, a filtered '
                    'subset — and name the byte-exact source separately.'
                )
            break  # the first RESOLVABLE path is the one the claim is about
    return findings


#: A whole-file negative: "no <token> anywhere in the file" / "no <token> in `path`". The SCOPE
#: marker is what makes this decidable — a negative about one function is a claim the lint cannot
#: bound, while "anywhere in the file" names its own falsifier.
_NEGATIVE_IN = re.compile(
    r'\bno\s+(?P<token>`[^`\s]{2,40}`|[^\s,;.]{3,40})\s+'
    r'(?:(?:appears?|occurs?|exists?)\s+)?(?:anywhere\s+|at all\s+)?(?:in|inside)\s+'
    r'(?:the\s+(?:entire\s+|whole\s+)?file|`(?P<path>[\w./\-]+\.[A-Za-z]\w{0,4})`)',
    re.IGNORECASE,
)

#: The mirror: "`path` contains no <token>".
_NEGATIVE_CONTAINS = re.compile(
    r'`(?P<path>[\w./\-]+\.[A-Za-z]\w{0,4})`[^\n]{0,40}?\b(?:contains|has|carries|holds|includes)\s+no\s+'
    r'(?P<token>`[^`\s]{2,40}`|[^\s,;.]{3,40})',
    re.IGNORECASE,
)

#: What a negated thing has to LOOK like before the lint greps for it. Code-shaped only: an
#: ordinary-English negative — "there is no retrieval, no ranking" — is a claim about behaviour,
#: not about bytes, and grepping it would report the word `retrieval`.
#:
#: EIGHT SHAPES. FOUR of them — the four CASING shapes: CONSTANT_CASE, camelCase, PascalCase and
#: kebab-case — were added; the pattern shipped with the other four (slash-joined, snake_case, the
#: call form, the dotted name). snake_case alone is a PYTHON convention: a TypeScript, Java or Go
#: agent writes `buildSystemPrompt`, `RetrievalClient`, `MAX_RETRIES` — every one of which was
#: `continue`d, producing byte-identical output to a claim the lint checked and found TRUE. Each
#: casing shape below requires an internal case CHANGE (or an all-caps underscore), which is
#: exactly what an ordinary English word cannot have: `input`, `retrieval` and `Retrieval` all
#: still fall through.
#:
#: kebab is the loosest of the eight, and it is here because the scope marker already carries the
#: weight: `_NEGATIVE_IN` / `_NEGATIVE_CONTAINS` only fire on a claim the author scoped to a FILE
#: ("anywhere in the file", "`path` contains no …"), which is a statement about bytes whatever the
#: token looks like.
#:
#: THE CASING RANGES ARE ASCII ON PURPOSE, and it is a trade, not an oversight. `RecuperaciónCliente`
#: and `検索クライアント` are code-shaped to a reader and match none of the eight, so they land in the
#: UNCHECKED channel — reported, never silently continued. That is the safe direction and the
#: reason it stays: widening the runs to any Unicode letter would admit tokens like `検索_設定`,
#: and `_token_hits` greps with `\b`, which does not exist between two characters of an unspaced
#: script — so an identifier sitting inside CJK prose would come back "not found" and the false
#: negative would print as a claim CHECKED AND TRUE. An over-abstain is legible; that is not.
_GREPPABLE_TOKEN = re.compile(
    r'^(?:'
    r'\w+/[\w/]+'                              # a slash-joined pair: try/except
    r'|[a-z][a-z0-9]*(?:_[a-z0-9]+)+'          # snake_case
    r'|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+'          # CONSTANT_CASE
    r'|[a-z][a-z0-9]*(?:[A-Z][a-zA-Z0-9]*)+'   # camelCase — an internal capital, which English has not
    r'|[A-Z][a-z0-9]+(?:[A-Z][a-zA-Z0-9]*)+'   # PascalCase — TWO humps, so `Retrieval` is not one
    r'|[a-z][a-z0-9]*(?:-[a-z0-9]+)+'          # kebab-case
    r'|\w+\(\)'                                # a call form
    r'|[\w]+\.[A-Za-z_]\w*'                    # a dotted name
    r')$'
)


def _token_hits(token: str, source: Path) -> list[int] | None:
    """1-based line numbers refuting the negative, `[]` when it holds, `None` when unreadable.

    THE THIRD STATE IS THE POINT. Returning `[]` for an unreadable file made "this file refutes
    the claim" and "this file could not be opened" the same answer, and the second one then
    printed `no findings` — a check that silently did not run, reported as a clean document.

    `try/except` is a PAIR the claim negates together, so the negative is false only when BOTH
    halves are in the file — and it is answered PER FILE, not per line. Line-scoped matching is
    the recurring hazard in this module and it bit here too: the construct that refuted the
    shipped claim is `try:` on one line and `except (...)` two lines below, so requiring both on
    one line reported the false negative as true.
    """
    parts = [p for p in token.replace('()', '').split('/') if p]
    try:
        lines = source.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return None
    per_part = [[i for i, line in enumerate(lines, 1) if re.search(rf'\b{re.escape(p)}\b', line)] for p in parts]
    if not all(per_part):
        return []
    return sorted({hits[0] for hits in per_part})


def check_whole_file_negatives(text: str, repo_root: Path) -> tuple[list[str], list[str]]:
    """`(findings, tokens this lint could NOT grep)`.

    Whole-file negatives about a named file that the file itself refutes.

    PARAGRAPH-scoped — the recurring hazard in this file (see `_paragraphs`): the shipped instance
    wrapped between the negative and the citation that bounded it.

    THE SECOND RETURN VALUE IS THE POINT of the pair, and it is the same shape `check_facts`
    already uses. A token the grammar cannot recognise used to `continue` into silence, so
    "checked and TRUE" and "never looked at" rendered identically — the exact conflation the rest
    of this lint's status line exists to refuse. It is not a FINDING (nothing about the PRD is
    wrong), so it rides beside them and lands in the status line as a count.
    """
    findings: list[str] = []
    ungreppable: list[str] = []
    for paragraph in _paragraphs(_without_appendix(text)):
        for pattern in (_NEGATIVE_IN, _NEGATIVE_CONTAINS):
            for match in pattern.finditer(paragraph):
                token = match.group('token').strip('`')
                if not _GREPPABLE_TOKEN.match(token):
                    ungreppable.append(token)
                    continue
                # The scope marker's own path when it named one, else the paragraph's single
                # resolvable path — "no try/except anywhere in the file" names the file elsewhere.
                refs = [match.group('path')] if match.group('path') else list(dict.fromkeys(_BACKTICKED_PATH.findall(paragraph)))
                resolved = [(r, s) for r in refs if r and (s := _resolve(r, repo_root))]
                if len(resolved) != 1:
                    continue  # no file, or an ambiguous one — the lint does not guess which
                ref, source = resolved[0]
                hits = _token_hits(token, source)
                if hits is None:
                    findings.append(
                        f'whole-file negative: "{match.group(0)[:90]}" could not be checked — `{ref}` is not '
                        f'readable from here. That is not the same as the claim holding; re-run with --repo-root '
                        f'pointed at the agent repo, or drop the whole-file scope.'
                    )
                    continue
                if hits:
                    where = ', '.join(f'{ref}:{n}' for n in hits[:3]) + (' …' if len(hits) > 3 else '')
                    findings.append(
                        f'whole-file negative: "{match.group(0)[:90]}" — but `{token}` is in that file at {where}. '
                        f'A negative scoped to a whole file is checkable, so it is checked; narrow the claim to '
                        f'the scope that is true, or drop it.'
                    )
    return findings, ungreppable


#: The number WORDS a PRD counts with; digits come from `_COUNTED_PHRASE`'s own `\d{1,3}`.
#: Beyond ten a PRD writes the digit, so the words stop there.
_NUMBER_WORDS = {w: i for i, w in enumerate(
    ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'))}

#: `<number> <modifier> <plural head>` — a COUNTED noun phrase. The modifier is REQUIRED: a bare
#: head ("three files", "two tools") collides across unrelated sentences, while "six local files"
#: and "seven local files" are the same claim stated twice.
_COUNTED_PHRASE = re.compile(
    r'\b(?P<num>\d{1,3}|' + '|'.join(_NUMBER_WORDS) + r')\s+'
    r'(?P<phrase>[a-z][\w-]*\s+[a-z][\w-]*s)\b',
    re.IGNORECASE,
)


def check_cardinality(text: str) -> list[str]:
    """One counted noun phrase given two different counts in the same document.

    SCOPE, honestly. A bare count ("reads seven local files") is not decidable against a repo — the
    lint cannot know which files are meant, and tracing one through a manifest is worse than not
    trying: this very PRD counts "3 assets the agent is licensed to construct a link from" against
    a six-entry `assets.yaml`, and that claim is TRUE (it counts one of the two asset types). What
    IS decidable with no inference at all is the document disagreeing with itself, and that is the
    live residue of the class: a mid-loop correction fixed "six local files" -> "seven" in
    §3 and left the same claim as "six" in the overview, where it still ships.

    KNOWN BLIND SPOT, and the price of catching the real class: two sections counting genuinely
    DIFFERENT sets with the same words collide — "the agent exposes 3 search tools" in §1 beside
    "we model 5 search tools for the persona bank" in §4 is two true statements and one finding.
    Advisory, so it costs the author one sentence; Narrowing it to a single
    section would drop true findings that span §1 and §3.

    The directives section is excluded — steering legitimately says "at most 2 scenarios open
    mid-task" and "vary the opening across the 5 categories" about the same nouns. That exclusion
    compares PARAGRAPHS, not a substring of the raw span: `_paragraphs` rejoins wrapped lines with
    a space, so a wrapped steering rule is not a substring of the section it came from and a
    substring test exempted only the rules that happened to fit on one line. Line-scoped matching
    is the recurring hazard in this module.
    """
    body = _without_appendix(text)
    design = set(_paragraphs(_design_span(text)))
    counts: dict[str, dict[int, str]] = {}
    for paragraph in _paragraphs(body):
        if paragraph in design:
            continue
        for match in _COUNTED_PHRASE.finditer(paragraph):
            raw = match.group('num').lower()
            value = _NUMBER_WORDS[raw] if raw in _NUMBER_WORDS else int(raw)
            counts.setdefault(' '.join(match.group('phrase').lower().split()), {}).setdefault(value, match.group(0))
    findings: list[str] = []
    for phrase, seen in sorted(counts.items()):
        if len(seen) < 2:
            continue
        stated = ', '.join(f'"{quote}"' for _, quote in sorted(seen.items()))
        findings.append(
            f'cardinality: "{phrase}" is counted {len(seen)} different ways in this PRD — {stated}. '
            f'If these count the SAME set, at most one is true and a judge grounded on the wrong one grades '
            f'against a fact the repo does not have. If they count different sets, say which in the text.'
        )
    return findings


#: Trees a citation never points into when its path is completed from a suffix.
_DEPENDENCY_DIRS = {'node_modules', 'venv', '.venv', 'env', 'site-packages', '__pycache__', '.git'}
_SUFFIX_SEARCH_LIMIT = 20_000


def _suffix_matches(ref: str, repo_root: Path) -> list[str]:
    """Repo-relative paths of files under ``repo_root`` whose path ends with the cited ``ref`` on a directory boundary."""
    tail = '/' + ref.lstrip('/')
    found: list[str] = []
    seen = 0
    for base, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(d for d in dirs if d not in _DEPENDENCY_DIRS)
        for name in sorted(files):
            seen += 1
            relative = (Path(base) / name).relative_to(repo_root).as_posix()
            if seen <= _SUFFIX_SEARCH_LIMIT and ('/' + relative).endswith(tail):
                found.append(relative)
    return found


def _line_count(path: Path) -> int | None:
    try:
        with path.open(encoding='utf-8', errors='replace') as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None  # the count only sharpens a citation finding; an unreadable file leaves that finding as it is


def check_citations(text: str, repo_root: Path) -> list[str]:
    """Citations that do not RESOLVE — a missing file, or a line past the end of one.

    Existence-only on purpose. Whether the cited lines say what the claim says they say is not
    mechanically decidable (a prose-vs-source check was built here once and removed — see the
    module docstring), but a citation pointing at a file that moved, or at a line a refactor
    deleted, is decidable and is the shape a stale claim takes. Advisory like the rest: a path
    outside the agent's repo is a legitimate citation this cannot follow.

    A citation is repo-root-relative. One that misses only its leading directories (`utils/emails.py` for
    `src/pkg/utils/emails.py`) is still a finding, but names the unique file it resolves to, so the fix is a rewrite
    and not a search; a suffix two files share lists both.
    """
    findings: list[str] = []
    for ref, line_no in _citations(text):
        # An absolute citation is honoured as written — `/` drops the left side for one.
        candidate = repo_root / ref
        if not candidate.is_file():
            matches = _suffix_matches(ref, repo_root)
            if len(matches) == 1:
                total = _line_count(repo_root / matches[0])
                past = f' The file has {total} lines, so the line is past its end too.' if total is not None and int(line_no) > total else ''
                findings.append(
                    f'citation: `{ref}:{line_no}` is not repo-root-relative: it resolves to `{matches[0]}:{line_no}` — cite that path '
                    f'(citations are repo-root-relative, like `--source`).{past}'
                )
            elif len(matches) > 1:
                findings.append(
                    f'citation: `{ref}:{line_no}` is not repo-root-relative and more than one file ends with it ({", ".join(f"`{m}`" for m in matches[:4])}) '
                    f'— cite the full path of the one you read.'
                )
            else:
                findings.append(
                    f'citation: `{ref}:{line_no}` names a file that does not exist under the repo root. '
                    f'Either the path moved (re-cite it) or the behaviour has no readable location, '
                    f'in which case the claim is an inference and carries `(inferred)` instead.'
                )
            continue
        total = _line_count(candidate)
        if total is not None and int(line_no) > total:
            findings.append(
                f'citation: `{ref}:{line_no}` points past the end of the file ({total} lines). '
                f'The claim it supports is grounded in bytes that are no longer there.'
            )
    return findings


#: A `file.ext:LINE` written WITHOUT backticks. A closed list of source and document suffixes, so a
#: `host.com:8080` is never one; a URL is removed before matching.
_BARE_CITATION = re.compile(
    r'(?<![\w./\-`])([\w./\-]*[\w\-]\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|go|java|kt|rb|rs|cs|php|swift|scala|cls|sql|sh|'
    r'ya?ml|json|toml|md|txt|cfg|ini)):(\d+)(?:-\d+)?(?![\w`])'
)
_INLINE_CODE = re.compile(r'(`+)[^`\n]*?\1')
_URL = re.compile(r'\b[a-z][a-z0-9+.-]*://\S+', re.IGNORECASE)


def check_unticked_citations(text: str) -> tuple[list[str], int]:
    """`(findings, count)`: `file.py:NNN` citations with no backticks are BROKEN, because no check reads them.

    `_CODE_CITATION` takes the backticked shape only, so `(…, optimization_services.py:170)` was neither
    resolved nor counted, and its paragraph read as uncited. Fenced blocks (the facts YAML) and the
    appendix are out of scope; a backticked span is removed first, so `x.py:12` inside one never fires.
    ONE finding names them all: the repair is the same mechanical edit for each, and a PRD that wrote its
    citations bare wrote dozens of them.
    """
    body = _without_appendix(text).splitlines()
    found: dict[str, None] = {}
    for line, fenced in zip(body, _fenced(body)):
        if fenced:
            continue
        for match in _BARE_CITATION.finditer(_URL.sub(' ', _INLINE_CODE.sub(' ', line))):
            found.setdefault(match.group(0))
    if not found:
        return [], 0
    shown = ', '.join(list(found)[:8]) + (f' and {len(found) - 8} more' if len(found) > 8 else '')
    return [
        f'citation: {len(found)} citation(s) are not back-ticked, so none is read as a citation — nothing resolves them and '
        f'their claims read as uncited: {shown}. Back-tick each with its repo-root-relative path, and check the line says '
        f'what the claim says.'
    ], len(found)


#: What the Step-9c claim audit writes (`references/prd_claim_audit.md`), and the site list it read.
CLAIM_AUDIT_FILE = 'prd_claim_audit.jsonl'
CLAIM_SITES_FILE = 'claim_sites.json'
CLAIM_VERDICTS = ('supported', 'refuted', 'unsupported')


def _claim_text(text: str) -> str:
    """A claim as compared: markdown emphasis, code ticks and escapes dropped, case and whitespace folded. An auditor
    copying `optimize_evaluator` without its ticks still quotes the PRD; compared raw, the row read as a misquote,
    and the first unrelated PRD edit then closed it as rewritten."""
    return ' '.join(re.sub(r'[`*_\\]', '', text).casefold().split())


def check_claim_audit(prd_bytes: bytes, art: Path) -> tuple[list[str], str]:
    """`(findings, status)`: each REFUTED row of the independent claim audit is an open finding.

    The audit is a reader, not this lint: it opened every site `claim_sites.py` lists for the names in a
    claim and judged the claim against all of them. A refuted row stays open while its claim's words are
    still in the PRD; it closes when the PRD rewrites them, or through `--accept` with the file:line the
    author verified (the existing route, which binds to this finding's text). Words that are not in the PRD
    the audit READ (the sha `claim_sites.json` recorded) are a misquoted row, and stay open as one.
    `unsupported` is advisory: counted, never a finding. No audit file is `not run` in the status, never
    silence.
    """
    path = art / CLAIM_AUDIT_FILE
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return [], 'not run'
    except (OSError, UnicodeDecodeError) as exc:
        return [], f'UNREADABLE ({type(exc).__name__})'
    try:
        audited_sha = json.loads((art / CLAIM_SITES_FILE).read_text(encoding='utf-8')).get('prd_sha256')
    except (OSError, ValueError, AttributeError):
        audited_sha = None
    changed = audited_sha is not None and audited_sha != hashlib.sha256(prd_bytes).hexdigest()
    prd = _claim_text(prd_bytes.decode('utf-8', 'replace'))
    counts = dict.fromkeys(CLAIM_VERDICTS, 0)
    findings: list[str] = []
    unreadable = rewritten = 0
    for line in filter(str.strip, raw.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        claim = ' '.join(str(row.get('claim') or '').split()) if isinstance(row, dict) else ''
        if not claim or row.get('verdict') not in CLAIM_VERDICTS:
            unreadable += 1
            continue
        # A refutation that names no site is a verdict nothing decides: it counts as unsupported, never a finding.
        verdict = 'unsupported' if row['verdict'] == 'refuted' and not row.get('sites') else row['verdict']
        counts[verdict] += 1
        if verdict != 'refuted':
            continue
        sites = ', '.join(f'`{s}`' for s in row['sites'][:4])
        reason = ' '.join(str(row.get('reason') or 'no reason given').split())
        if _claim_text(claim) in prd:
            findings.append(
                f'claim audit: refuted — "{claim[:160]}" — {reason[:240]} (sites: {sites}). Re-read those sites and correct '
                f'the claim, or accept it with the file:line you verified.'
            )
        elif changed:
            rewritten += 1
        else:
            findings.append(
                f'claim audit: a refuted row quotes words that are not in the PRD it audited — "{claim[:160]}" (sites: {sites}). '
                f'Find the claim it means and correct it, or accept with the file:line you verified.'
            )
    status = (
        f'{sum(counts.values())} claims — {counts["supported"]} supported, {counts["refuted"]} refuted '
        f'({counts["refuted"] - rewritten} still in the PRD, {rewritten} rewritten), {counts["unsupported"]} unsupported'
        + (f', {unreadable} unreadable row(s)' if unreadable else '')
        + (', the PRD changed since' if changed else '')
    )
    return findings, status


def _entity_terms(config: dict) -> set[str]:
    """Tool/entity names, used only to NAME the subject in a placement finding."""
    terms: set[str] = set()
    tools = config.get('tools')
    if isinstance(tools, dict):
        terms |= {str(k) for k in tools}
    elif isinstance(tools, list):
        terms |= {str(t.get('name')) for t in tools if isinstance(t, dict) and t.get('name')}
    entities = config.get('entities') or config.get('entity_schemas')
    if isinstance(entities, dict):
        terms |= {str(k) for k in entities}
    return {t for t in terms if t and len(t) > 3}


def main() -> int:
    parser = argparse.ArgumentParser(description='Mechanical checks over a drafted simulation_prd.md.')
    parser.add_argument('prd', type=Path)
    parser.add_argument('--config', type=Path, help='plurai.config.yaml — enables the coverage check and names entities in placement findings')
    parser.add_argument(
        '--repo-root',
        type=Path,
        help="the agent repo the PRD's `path:line` citations are relative to; defaults to the PRD's grandparent (the plurai_sim layout)",
    )
    parser.add_argument(
        '--accept', metavar='FP', action='append', default=[],
        help='accept an open finding, named by the [fp:xxxxxxxx] it printed; repeatable, each paired in order with its own --reason',
    )
    parser.add_argument(
        '--reason', action='append', default=[],
        help=f'what you verified, citing the repo (file:line); at least {gate_ledger.OVERRULE_REASON_MIN_CHARS} characters. Recorded in the gate ledger beside the PRD',
    )
    parser.add_argument(
        '--accept-from', type=Path, metavar='FILE',
        help='accept many findings in one call: one `<fp> <reason>` per line (blank lines and `#` lines ignored)',
    )
    args = parser.parse_args()
    if len(args.accept) != len(args.reason):
        parser.error('--accept and --reason go together, one --reason per --accept: a finding is accepted only with the reason it was accepted for.')
    pairs = list(zip(args.accept, args.reason))
    if args.accept_from is not None:
        try:
            listed = args.accept_from.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            parser.error(f'--accept-from {args.accept_from}: {exc}')
        for n, raw in enumerate(listed, 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            fp, _, reason = line.partition(' ')
            if not reason.strip():
                parser.error(f'--accept-from {args.accept_from}:{n}: a line is `<fp> <reason>`; this one has no reason.')
            pairs.append((fp, reason.strip()))

    try:
        # UnicodeDecodeError is a ValueError, so a bare OSError guard turned a latin-1 PRD
        # into a traceback from a script that promises to exit 0 unless the PRD is unreadable.
        text = args.prd.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        print(f'ERROR: cannot read {args.prd}: {exc}', file=sys.stderr)
        return 3

    config: dict = {}
    coverage_ran = False
    if args.config:
        if not args.config.is_file():
            # Passing --config and silently getting no coverage check is worse than not
            # passing it: the operator believes the check ran.
            print(f'ERROR: --config {args.config} does not exist.', file=sys.stderr)
            return 3
        try:
            loaded = yaml.safe_load(args.config.read_text(encoding='utf-8'))
            config = loaded if isinstance(loaded, dict) else {}
            coverage_ran = bool(config.get('tools'))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            # Names only what is actually lost. Placement still runs — it just cannot name
            # the entity — so its findings from this run remain valid.
            print(f'  ⚠ could not parse {args.config} ({exc}); the tool-coverage check is skipped.', file=sys.stderr)

    repo_root = (args.repo_root or args.prd.resolve().parent.parent).resolve()
    citation_findings = check_citations(text, repo_root)
    unticked_findings, unticked = check_unticked_citations(text)
    audit_findings, audit_status = check_claim_audit(args.prd.read_bytes(), args.prd.resolve().parent / 'artifacts')
    fact_findings, fact_count = check_facts(text)
    byte_findings = check_byte_exact_quotes(text, repo_root)
    negative_findings, unchecked_negatives = check_whole_file_negatives(text, repo_root)
    # A block that cannot be parsed or is not a `facts:` mapping is read by nobody: no disposition makes it ground anything.
    unread = [f for f in fact_findings if f.startswith(UNREAD_FACTS_PREFIX)]
    fact_findings = [f for f in fact_findings if f not in unread]
    errors = [*check_facts_placement(text), *unread]
    identity_errors = check_fact_identity(text)
    denial_errors = check_input_denials(text)
    absence_errors, absence_warnings = check_absence_claims(text, production_tool_names(args.prd))
    coverage_findings, rule_coverage = check_rule_coverage(text, repo_root)
    candidate_findings = check_instruction_candidates(text, repo_root)
    measured_findings, measured_status = check_measured_facts(text, repo_root, args.prd.resolve().parent / 'artifacts')
    thin_prose, prose_status = check_prose_depth(text)
    copy_hint = duplicate_prose_hint(*prose_duplicates(text))
    findings = [
        *check_placement(text, _entity_terms(config)),
        *check_family_heads(text),
        *check_tool_coverage(text, config),
        *check_render_surface(text, config),
        *check_cited_links(text, config),
        *citation_findings,
        *unticked_findings,
        *audit_findings,
        *check_authored_law(text),
        *check_mechanism_claims(text),
        *byte_findings,
        *negative_findings,
        *check_cardinality(text),
        *fact_findings,
        *check_state_seedability(text, config, repo_root),
        *coverage_findings,
        *absence_warnings,
        *candidate_findings,
        *check_coarse_rules(text, repo_root),
        *check_harness_cited_as_source(text, config),
        *measured_findings,
    ]
    if fact_count is None and not errors:
        findings.append(ABSENT_FACTS_FINDING)
    art = args.prd.resolve().parent / 'artifacts'
    minted = [
        gate_ledger.make_finding(
            gate_ledger.PRD_LINT_OPEN_FINDING,
            subject=args.prd.name,
            evidence={'finding': f},
            observed={},
            remedy='fix the PRD and re-run lint_prd.py, or accept this finding with the reason you verified',
            draft_ref=None,
        )
        for f in dict.fromkeys(findings)
    ]
    findings_file = art / '.findings' / 'lint_prd.json'
    if pairs:
        return _accept_all(pairs, minted, art, findings_file)
    try:
        applied, _ = gate_ledger.settle(minted, art=art, evaluated=(gate_ledger.PRD_LINT_OPEN_FINDING,), findings_out=None)
        ledger_problem = ''
    except (OSError, gate_ledger.LedgerError) as exc:
        # A ledger that cannot be read lifts nothing: every finding stays open, and the author is told why.
        applied = gate_ledger.Applied(holding=tuple(minted), overruled=(), stale=(), not_holding=())
        ledger_problem = f'the gate ledger {gate_ledger.ledger_path(art)} could not be read ({type(exc).__name__}: {exc}); no acceptance was applied.'
    open_texts = {f.fingerprint: f.evidence['finding'] for f in applied.holding}
    for f in applied.holding:
        print(f'  ⚠ {f.evidence["finding"]} [fp:{f.fingerprint[:FP_SHOWN]}]', file=sys.stderr)
    for f in applied.overruled:
        print(f'  ✓ accepted: {f.evidence["finding"][:100]} [fp:{f.fingerprint[:FP_SHOWN]}]', file=sys.stderr)
    for o in applied.stale:
        print(f'  ⚠ an acceptance lapsed (fp:{o.fingerprint[:FP_SHOWN]}): the finding it covered changed or is gone — judge the current finding again', file=sys.stderr)
    if ledger_problem:
        print(f'  ⚠ {ledger_problem}', file=sys.stderr)
    if thin_prose:
        print(f'  ℹ {prose_depth_hint(thin_prose)}', file=sys.stderr)
    if copy_hint:
        print(f'  ℹ {copy_hint}', file=sys.stderr)
    # ERRORS LAST among the findings: the bench manifest records a failing check's final stderr line as its evidence.
    for e in (*errors, *identity_errors, *denial_errors, *absence_errors):
        print(f'  ✖ {e}', file=sys.stderr)
    # A bare "no findings" cannot distinguish a check that passed from one that never ran —
    # the same conflation these checks look for in a PRD.
    #
    # The citation COUNTS are reported, not just the failures, because the only mechanical
    # pressure this lint applies runs against citing: marking every code-behaviour claim
    # `(inferred)` lints perfectly clean. The ratio is what makes that visible — a PRD with 20
    # markers and 1 citation is a document that chose not to look, and the suite-gate critic
    # reads this line.
    cited = len(_citations(text))
    inferred = len(_INFERRED_MARKER.findall(text))
    # An ABSENT facts section reads clean and is not clean: it is a PRD that recorded no
    # machine-derived specifics at all, which is the state the authored-law rules replaced.
    facts_status = 'SECTION ABSENT' if fact_count is None else f'{fact_count} typed'
    # THE TWO REPO-READING CHECKS REPORT THEIR ROOT. Both silently find nothing when
    # `--repo-root` is wrong (the default is a guess at the plurai_sim layout), and a run that
    # scanned the wrong tree otherwise prints a byte-identical clean line — the did-it-run
    # conflation the rest of this status line already exists to refuse.
    # UNCHECKED NEGATIVES ARE NAMED, not counted into the clean line. A whole-file negative whose
    # token this lint's grammar cannot recognise was `continue`d, so it rendered exactly like one
    # that was checked and held — and the shapes it could not recognise were every non-Python
    # casing, i.e. every TypeScript / Java / Go agent's identifiers.
    unchecked = (
        f' · {len(unchecked_negatives)} whole-file negative(s) NOT greppable — unchecked: '
        + ', '.join(sorted({f'"{t}"' for t in unchecked_negatives})[:5])
        if unchecked_negatives else ''
    )
    loaded = len(instruction_candidates(repo_root))
    loaded_files = f' · loaded instruction files: {loaded}, {len(candidate_findings)} unaccounted' if loaded else ''
    render_surface = 'SKIPPED (no config)' if not args.config else ('declared' if declared_render_actions(config) is not None else 'undeclared')
    status = (
        f'placement: ran, {len(errors)} error(s) · coverage: {"ran" if coverage_ran else "SKIPPED (no tools in config)"} '
        f'· render surface: {render_surface} '
        f'· citations: {cited - len(citation_findings)} resolved, {len(citation_findings) + unticked} broken '
        f'· claim audit: {audit_status} '
        f'· (inferred) markers: {inferred} · facts: {facts_status} · rule coverage: {rule_coverage}{loaded_files} · measured: {measured_status} · prose depth: {prose_status} '
        f'· mechanical claims vs {repo_root}: {len(byte_findings)} byte-exact, {len(negative_findings)} whole-file negative'
        f'{unchecked}'
    )
    accepted = f'{len(applied.overruled)} accepted with a reason' if applied.overruled else ''
    # The clean mark only when NOTHING was found: an ERROR beside "✓ no findings" reads as a contradiction.
    failing = errors or identity_errors or denial_errors or absence_errors
    # The headline is all a caller that runs this as a manifest check shows, and the manifest reads its leading
    # count. The open findings are named here as work, with the way to read them and the only two ways to close one.
    if open_texts:
        headline = (
            f'{len(open_texts)} PRD finding(s) open (their text is on stderr: run lint_prd.py itself) — '
            'fix the PRD and re-run; keep one only by accepting it with the reason you verified'
            + (f' ({accepted})' if accepted else '')
        )
    elif failing:
        headline = '0 PRD finding(s) open' + (f' ({accepted})' if accepted else '')
    elif applied.overruled:
        headline = f'✓ simulation_prd.md: no open findings, {accepted}'
    else:
        headline = '✓ simulation_prd.md: no findings'
    if identity_errors:
        headline = f'{len(identity_errors)} duplicate-fact ERROR(s), {headline}'
    if denial_errors:
        headline = f'{len(denial_errors)} input-denial ERROR(s), {headline}'
    if absence_errors:
        headline = f'{len(absence_errors)} absence-claim ERROR(s), {headline}'
    if errors:
        headline = f'{len(errors)} placement ERROR(s), {headline}'
    print(f'{headline} [{status}]')
    # A typed fact the auditor cannot read is an error: there is no disposition an author can write that makes
    # it ground anything. So is a repeated `subject.predicate`: the contract reports two verdicts under its one id.
    # So is a constraint denying the file channel the PRD's own facts quote: no scenario would test it.
    # So is a claim that the agent lacks a tool it ships: the PRD would grade a harness cut as the agent's limit.
    if failing:
        return 1
    if open_texts:
        # LAST on stderr: the manifest keeps the final stderr line of a non-zero exit as the check's evidence.
        print(
            f'  open: {len(open_texts)} finding(s) — fix the PRD and re-run, or accept one with the reason you verified: '
            f'lint_prd.py {args.prd.name} --accept <fp> --reason "<what you verified>" (several: repeat the pair, or --accept-from <file>)',
            file=sys.stderr,
        )
        return OPEN_FINDINGS_EXIT
    return 0


def _accept_all(pairs: list[tuple[str, str]], minted: list, art: Path, findings_file: Path) -> int:
    """Record each ``(fp, reason)`` acceptance in one process, every one held to the single acceptance's rules.

    Each is decided alone, in order: a refused one is named on stderr and the rest still land. Exit 0 when all landed,
    2 when any was refused (the ⚠ lines of the next lint run name what is still open)."""
    refused = 0
    seen: set[str] = set()
    for prefix, reason in pairs:
        key = ' '.join(reason.split()).lower()
        if key in seen:
            print(f'lint_prd: --accept {prefix!r}: that reason is already given for another finding in this call. Say what you verified for THIS one.', file=sys.stderr)
            refused += 1
            continue
        code = _accept(prefix, reason, minted, art, findings_file)
        if code == 0:
            seen.add(key)
        else:
            refused += 1
    if len(pairs) > 1:
        print(f'lint_prd: {len(pairs) - refused} of {len(pairs)} acceptance(s) recorded' + (f', {refused} refused' if refused else ''), file=sys.stderr)
    return 2 if refused else 0


def _accept(prefix: str, reason: str, minted: list, art: Path, findings_file: Path) -> int:
    """Record ONE acceptance in the gate ledger: the open finding whose fingerprint starts with ``prefix``."""
    wanted = prefix.strip().lower().removeprefix('fp:')
    matches = [f for f in minted if len(wanted) >= FP_SHOWN and f.fingerprint.startswith(wanted)]
    if len(matches) != 1:
        why = 'matches no open finding' if not matches else f'matches {len(matches)} findings; give more of the fingerprint'
        print(f'lint_prd: --accept {prefix!r} {why}. Run lint_prd.py again and copy an [fp:xxxxxxxx] from a ⚠ line.', file=sys.stderr)
        return 2
    # One reason per finding: the same words recorded for a second finding are an acceptance nobody read.
    try:
        recorded = {' '.join(line.overrule.reason.split()).lower() for line in gate_ledger.read_ledger(gate_ledger.ledger_path(art)) if line.overrule.gate_id == gate_ledger.PRD_LINT_OPEN_FINDING}
    except (OSError, gate_ledger.LedgerError):
        recorded = set()
    if ' '.join(reason.split()).lower() in recorded:
        print('lint_prd: that reason is already recorded for another finding. Say what you verified for THIS one (the file:line you read).', file=sys.stderr)
        return 2
    gate_ledger.write_findings(findings_file, matches)
    return gate_ledger.main([
        'overrule', '--art', str(art), '--gate-id', gate_ledger.PRD_LINT_OPEN_FINDING,
        '--fingerprint', matches[0].fingerprint, '--reason', reason, '--finding-from', str(findings_file),
    ])


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
