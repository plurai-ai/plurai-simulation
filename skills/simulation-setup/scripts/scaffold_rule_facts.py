#!/usr/bin/env python3
"""Write the agent's instruction facts into the PRD byte for byte, for the author to extend.

    scaffold_rule_facts.py --repo-root . --source prompts/system_prompt.md --source prompts/response_prompt.md \\
        --config plurai_sim/plurai.config.yaml --into plurai_sim/simulation_prd.md
    scaffold_rule_facts.py ... --decline prompts/system_prompt.md:21=not-an-instruction --into ...

`lint_prd.py` holds three obligations on the instruction facts: a `rule` fact's `evidence` is the agent's own bytes (a
paraphrase is law the agent was never given), its `subject` is the cited `path:LO-HI` (an invented slug resolves to
nothing), and every instruction line is a rule or a typed decline. This script does the mechanical part of all three and
leaves the judgement to the author.

For each `--source PATH[:LO-HI]` (default: the whole file; one file may be given as several ranges) it splits the range
into units, a unit being one instruction: a line, or the lines one wrapped sentence spans (`lint_prd.split_units`, the
join the backend applies to per-line rule facts). It writes, byte for byte:

* `instruction_sources:`  one entry per source, `lines: LO-HI`, with the `unrowed:` declines inside that range;
* one `rule` fact per unit: `subject` and `source` are `path:LINE` (`path:LO-HI` for a wrapped sentence), `evidence` the unit's lines;
* the `### The agent's own instructions, quoted` block the lint matches each `evidence` against: each source range's
  paragraphs once, as the context the rules are read in;
* with `--profile artifacts/data_profile.md`, the measured facts that file's inputs settle (`measured_candidates.py`):
  a `closed_set` per structured URL field, a `conflicts_across_sources` per URL the instructions name that no such set
  holds, an `absent_from_source` for the topics the instructions call out of scope that no corpus document mentions.
  Each states what the sweep counted, with a `check:` where the claim has one; `--skip-measured SUBJECT` drops one;
* a `tool_surface` / `RENDER_ACTIONS` / `omits` fact with its absence `check:`, when `--config` declares
  `render_actions:`. Its terms are verbs an agent of this kind might invent, minus any the config declares and any
  that occurs in the instruction sources or in a `--corpus` path. `--omit-terms a,b,c` names the verbs this domain
  would plausibly invent instead; the same rules drop any that do not hold.

A unit whose text repeats an earlier unit's is declined `duplicate-of-<that subject>`. In a markdown source, a unit
that opens inside the front matter, a fenced code block or a table is reference material, not an instruction, and is
declined `not-an-instruction` the same way; `--row PATH:LO-HI` puts such a unit back as a `rule`. A line of template
slots only (`{name}`, `{{ name }}`, `${expr}`, a unit of its own) is neither: the model reads what fills the slot, so
the line stays open, and the summary names it (unnumbered), until the author declines it
`=filled-by@PATH:LO-HI[, PATH:LO-HI]` (the range that fills it with instructions, also passed as a `--source`) or
`=data-slot@PATH:LINE` (the line that fills it with data); `--row` on it is a usage error. Anything else is a
`rule` until the author declines it: `--decline PATH:LO-HI=not-an-instruction` (a persona line, an example, a table),
`=duplicate-of-<subject>`, or `=not-loaded@PATH:LINE` (the load site that reads other instructions instead). A
decline covers whole units. The numbered unit list this prints is what the author reads to choose.

`--into PRD` splices the three pieces into the PRD between `scaffold:begin` / `scaffold:end` markers it creates, so a
re-run (with more `--decline`s) replaces its own output and touches nothing else. Without `--into`, `--out-dir DIR`
writes the pieces as files. Every evidence string is parsed back and compared to its source lines before anything is
written.

Exit codes: 0 done · 1 a generated fact, or the spliced facts block, does not parse back to what was written (nothing written) · 2 a usage error:
a source or corpus path that is not a file, a range outside its file, overlapping sources, a decline that cuts a unit, `--row` on a slot line,
or an `--into` PRD whose facts block declares `instruction_sources:` outside the markers or has a `facts:` key the script cannot extend.

Stdlib + pyyaml only; runs under the python3 3.9 floor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

try:
    import yaml
except ImportError:
    print(f'ERROR: pyyaml is required to run scaffold_rule_facts.py; install it into {sys.executable}', file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_prd  # noqa: E402 - the grammar this output must satisfy: one spelling of every key and class
import measured_candidates  # noqa: E402 - the sweep the lint reports an untyped result of

#: Verbs an agent in some domain might invent, swept against the sources before they are offered as the `omits` terms.
CANDIDATE_RENDER_VERBS = (
    'render_video', 'render_card', 'render_chart', 'render_table', 'show_image', 'play_audio', 'open_panel', 'navigate_to', 'handoff', 'download_file',
)

_SOURCE_ARG = re.compile(r'^(?P<path>.+?)(?::(?P<lo>\d+)(?:-(?P<hi>\d+))?)?$')
_DECLINE_ARG = re.compile(r'^(?P<path>.+?):(?P<lo>\d+)(?:-(?P<hi>\d+))?=(?P<cls>[^@]+?)(?:@(?P<src>.+))?$')

QUOTED_HEADING = "### The agent's own instructions, quoted"
FACTS_HEADING = '## Agent facts'
YAML_SOURCES = ('# scaffold:begin instruction_sources', '# scaffold:end instruction_sources')
YAML_RULES = ('  # scaffold:begin rule-facts', '  # scaffold:end rule-facts')
MD_QUOTED = ('<!-- scaffold:begin instructions-quoted -->', '<!-- scaffold:end instructions-quoted -->')


class Usage(Exception):
    """A usage error: exit 2."""


class NotByteExact(Exception):
    """A generated fact that does not parse back to its source lines: exit 1."""


class Unit(NamedTuple):
    path: str
    lo: int
    hi: int
    lines: list[str]

    @property
    def subject(self) -> str:
        return f'{self.path}:{self.lo}' if self.lo == self.hi else f'{self.path}:{self.lo}-{self.hi}'

    @property
    def text(self) -> str:
        return '\n'.join(self.lines)


class Source(NamedTuple):
    path: str
    lo: int
    hi: int
    lines: list[str]  # the whole file

    @property
    def span(self) -> tuple[str, int, int]:
        """The key of this range: one file may be several sources, and the ranges of one file never overlap."""
        return self.path, self.lo, self.hi


class Decline(NamedTuple):
    lo: int
    hi: int
    cls: str
    source: str | None


def _range(lo: str | None, hi: str | None, total: int, label: str) -> tuple[int, int]:
    first = int(lo) if lo else 1
    last = int(hi) if hi else (first if lo else total)
    if not 1 <= first <= last <= total:
        raise Usage(f'{label}: {first}-{last} is not a LO-HI range inside the file ({total} lines).')
    return first, last


def read_sources(repo_root: Path, specs: list[str]) -> list[Source]:
    sources: list[Source] = []
    for spec in specs:
        match = _SOURCE_ARG.match(spec)
        if not match:
            raise Usage(f'--source {spec!r} is not PATH or PATH:LO-HI.')
        path = match.group('path').removeprefix('./')
        file = (repo_root / path).resolve()
        if not file.is_file() or not file.is_relative_to(repo_root.resolve()):
            raise Usage(f'--source {path} is not a file under {repo_root}.')
        lines = file.read_text(encoding='utf-8', errors='replace').splitlines()
        lo, hi = _range(match.group('lo'), match.group('hi'), len(lines), f'--source {spec}')
        for other in sources:
            if other.path == path and not (hi < other.lo or lo > other.hi):
                raise Usage(f'--source {spec} overlaps {other.path}:{other.lo}-{other.hi}.')
        sources.append(Source(path, lo, hi, lines))
    return sources


#: A line of template slots only (`{name}`, `{{ name }}`, `${expr}`): the lint's one definition.
slot_only = lint_prd.slot_only


def split_units(source: Source) -> list[Unit]:
    """The rule units of ``source``'s range (``lint_prd.split_units``: the definition the "too coarse" lint counts by),
    with each line of template slots only a unit of its own."""
    units: list[Unit] = []

    def piece(lo: int, hi: int) -> None:
        lines = source.lines[lo - 1 : hi]
        if lo <= hi and any(lint_prd._LETTER.search(line) for line in lines):
            units.append(Unit(source.path, lo, hi, lines))

    for lo, hi in lint_prd.split_units(source.lines, source.lo, source.hi):
        start = lo
        for n in range(lo, hi + 1):
            if slot_only(source.lines[n - 1]):
                piece(start, n - 1)
                piece(n, n)
                start = n + 1
        piece(start, hi)
    return units


MARKDOWN_SUFFIXES = lint_prd.MARKDOWN_SUFFIXES


def reference_lines(source: Source) -> set[int]:
    """The 1-based lines of a markdown ``source`` that are reference material (`lint_prd.reference_lines`): the front
    matter, fenced code blocks (delimiters included) and table rows."""
    return lint_prd.reference_lines(source.path, source.lines)


def _home(path: str, lo: int, hi: int, sources: list[Source]) -> Source | None:
    """The source range of ``path`` that holds ``lo``-``hi``."""
    return next((s for s in sources if s.path == path and s.lo <= lo and hi <= s.hi), None)


#: The classes that carry a source after `@`: its form, and what it cites.
_CITING_CLASSES = {
    lint_prd.UNROWED_NOT_LOADED: ('PATH:LINE', 'the load site that reads other instructions'),
    lint_prd.UNROWED_FILLED_BY: ('PATH:LO-HI[, PATH:LO-HI]', 'the declared range(s) that fill the slot line with instructions'),
    lint_prd.UNROWED_DATA_SLOT: ('PATH:LINE', 'the line that fills the slot line with data'),
}
_CLASSES = 'not-an-instruction | duplicate-of-<subject> | ' + ' | '.join(f'{c}@{form}' for c, (form, _what) in _CITING_CLASSES.items())


def parse_declines(specs: list[str], sources: list[Source]) -> dict[str, list[Decline]]:
    by_path: dict[str, list[Decline]] = {}
    for spec in specs:
        match = _DECLINE_ARG.match(spec)
        if not match:
            raise Usage(f'--decline {spec!r} is not PATH:LO-HI=CLASS (CLASS: {_CLASSES}).')
        path = match.group('path').removeprefix('./')
        file = next((s for s in sources if s.path == path), None)
        if file is None:
            raise Usage(f'--decline {spec!r} names {path}, which is not a --source.')
        lo, hi = _range(match.group('lo'), match.group('hi'), len(file.lines), f'--decline {spec}')
        if _home(path, lo, hi, sources) is None:
            ranges = ', '.join(f'{s.lo}-{s.hi}' for s in sources if s.path == path)
            raise Usage(f'--decline {spec!r} is outside the source range(s) {ranges}.')
        cls, src = match.group('cls').strip(), match.group('src')
        if cls in _CITING_CLASSES:
            if not src:
                form, what = _CITING_CLASSES[cls]
                raise Usage(f'--decline {spec!r}: {cls} needs {what}, `={cls}@{form}`.')
        elif cls != lint_prd.UNROWED_NOT_AN_INSTRUCTION and not (cls.startswith(lint_prd.UNROWED_DUPLICATE_OF) and len(cls) > len(lint_prd.UNROWED_DUPLICATE_OF)):
            raise Usage(f'--decline {spec!r}: the class is {_CLASSES}.')
        by_path.setdefault(path, []).append(Decline(lo, hi, cls, src))
    return by_path


def parse_rows(specs: list[str], sources: list[Source]) -> dict[str, list[tuple[int, int]]]:
    by_path: dict[str, list[tuple[int, int]]] = {}
    for spec in specs:
        match = _SOURCE_ARG.match(spec)
        path = match.group('path').removeprefix('./') if match else ''
        home = next((s for s in sources if s.path == path), None)
        if match is None or home is None or not match.group('lo'):
            raise Usage(f'--row {spec!r} is not PATH:LO-HI naming a --source.')
        by_path.setdefault(path, []).append(_range(match.group('lo'), match.group('hi'), len(home.lines), f'--row {spec}'))
    return by_path


def _merged(declines: list[Decline], source: Source) -> list[Decline]:
    """Neighbouring declines of one class, with nothing but blank lines and headings between them, as one range."""
    out: list[Decline] = []
    for d in sorted(declines, key=lambda d: d.lo):
        if out and out[-1].cls == d.cls and d.cls == lint_prd.UNROWED_NOT_AN_INSTRUCTION and out[-1].source is None and d.source is None and all(
            not ln.strip() or lint_prd._MD_HEADING.match(ln) for ln in source.lines[out[-1].hi : d.lo - 1]
        ):
            out[-1] = Decline(out[-1].lo, d.hi, d.cls, None)
        else:
            out.append(d)
    return out


def plan(sources: list[Source], declines: dict[str, list[Decline]], rows_back: dict[str, list[tuple[int, int]]] | None = None):
    """``(rows, unrowed)``: the units that become `rule` facts, and each source range's declines (the author's inside that
    range, then the reference material and duplicates found here), keyed by `Source.span`: one file may be several
    ranges. A line of template slots only is in neither until the author declines it. A decline must cover whole units.
    A unit that repeats one of an earlier range, of this file or another, is declined as its duplicate."""
    rows: list[Unit] = []
    unrowed: dict[tuple[str, int, int], list[Decline]] = {}
    seen: dict[str, str] = {}
    for source in sources:
        asked = [d for d in declines.get(source.path, []) if source.lo <= d.lo and d.hi <= source.hi]
        reference = reference_lines(source)
        wanted = (rows_back or {}).get(source.path, [])
        found: list[Decline] = []
        for unit in split_units(source):
            cut = [d for d in asked if d.lo <= unit.hi and unit.lo <= d.hi and not (d.lo <= unit.lo and unit.hi <= d.hi)]
            if cut:
                raise Usage(
                    f'--decline {source.path}:{cut[0].lo}-{cut[0].hi} cuts the unit {unit.subject}: a decline covers whole units '
                    f'(widen or narrow it to start at line {unit.lo} and end at line {unit.hi}).'
                )
            if any(d.lo <= unit.lo and unit.hi <= d.hi for d in asked):
                continue
            rowed_back = any(lo <= unit.hi and unit.lo <= hi for lo, hi in wanted)
            if unit.lo in reference and not rowed_back:
                found.append(Decline(unit.lo, unit.hi, lint_prd.UNROWED_NOT_AN_INSTRUCTION, None))
                continue
            if unit.lo not in reference and all(slot_only(ln) for ln in unit.lines):
                # A slot line is what fills it: it stays open until the author names the filler (`slot_lines` lists it).
                if rowed_back:
                    raise Usage(
                        f'--row {unit.subject}: a line of template slots only is never a rule; decline it '
                        f'={lint_prd.UNROWED_FILLED_BY}@<the range that fills it> or ={lint_prd.UNROWED_DATA_SLOT}@<the line that fills it>.'
                    )
                continue
            folded = lint_prd._fold(unit.text)
            if folded in seen:
                found.append(Decline(unit.lo, unit.hi, f'{lint_prd.UNROWED_DUPLICATE_OF}{seen[folded]}', None))
                continue
            seen[folded] = unit.subject
            rows.append(unit)
        unrowed[source.span] = sorted([*asked, *_merged(found, source)], key=lambda d: d.lo)
    return rows, unrowed


# --- emission --------------------------------------------------------------------------------------------


def _scalar_candidates(value: str, indent: int) -> list[str]:
    """``value`` as YAML scalars, best readable first: a literal block for several lines, then a JSON string, then a
    JSON string with every non-ASCII character escaped. The caller keeps the first that parses back to ``value``."""
    quoted = [json.dumps(value, ensure_ascii=False), json.dumps(value, ensure_ascii=True)]
    # A block scalar puts the value's lines in the YAML as they are, and a line that opens a code fence would close
    # the PRD fence the whole facts block sits in.
    if '\n' not in value or any(lint_prd._FENCE_OPEN.match(line) for line in value.split('\n')):
        return quoted
    pad = ' ' * indent
    block = '|-\n' + '\n'.join((pad + ln) if ln else '' for ln in value.split('\n'))
    return [block, *quoted]


def _entry(path: str, lo: int, hi: int, unrowed: list[Decline]) -> str:
    out = [f'  - path: {json.dumps(path, ensure_ascii=False)}', f'    lines: {lo}-{hi}']
    if not unrowed:
        out.append('    unrowed: []')
        return '\n'.join(out)
    out.append('    unrowed:')
    for d in unrowed:
        extra = f', source: {json.dumps(d.source)}' if d.source else ''
        out.append(f'      - {{lines: {d.lo}-{d.hi}, class: {json.dumps(d.cls, ensure_ascii=False)}{extra}}}')
    return '\n'.join(out)


def emit_sources(sources: list[Source], unrowed: dict[tuple[str, int, int], list[Decline]]) -> str:
    return 'instruction_sources:\n' + '\n'.join(_entry(s.path, s.lo, s.hi, unrowed[s.span]) for s in sources)


def emit_rule(unit: Unit) -> str:
    subject = json.dumps(unit.subject, ensure_ascii=False)
    for scalar in _scalar_candidates(unit.text, 6):
        fact = (
            f'  - kind: rule\n    subject: {subject}\n    predicate: instructs\n'
            f'    evidence: {scalar}\n    source: {subject}'
        )
        try:
            parsed = yaml.safe_load('facts:\n' + fact)['facts'][0]
        except yaml.YAMLError:
            continue
        if parsed.get('evidence') == unit.text:
            return fact
    raise NotByteExact(f'no YAML spelling of {unit.subject} parses back to its source lines; nothing was written.')


def emit_measured(candidate: measured_candidates.Candidate) -> str:
    """A `data_profile` fact stating what the sweep measured, with the re-runnable `check:` where the claim has one."""
    lines = [
        '  - kind: data_profile',
        f'    subject: {json.dumps(candidate.subject, ensure_ascii=False)}',
        f'    predicate: {candidate.kind}',
        f'    evidence: {json.dumps(candidate.evidence, ensure_ascii=False)}',
        f'    source: {json.dumps(candidate.source, ensure_ascii=False)}',
    ]
    if candidate.check_kind:
        terms = ', '.join(json.dumps(t, ensure_ascii=False) for t in candidate.terms)
        lines += [
            '    check:',
            f'      kind: {candidate.check_kind}',
            f'      terms: [{terms}]',
            f'      match: {"all" if candidate.check_kind == "existence" else "any"}',
        ]
    return '\n'.join(lines)


def swept_terms(config: dict, haystack: str, candidates: tuple[str, ...] = CANDIDATE_RENDER_VERBS) -> list[str]:
    declared = {str(a) for a in (config.get(lint_prd.RENDER_ACTIONS_KEY) or []) if isinstance(a, str)}
    lowered = haystack.lower()
    return [v for v in candidates if v not in declared and v.lower() not in lowered]


def emit_omits(config: dict, haystack: str, where: str, candidates: tuple[str, ...] = CANDIDATE_RENDER_VERBS) -> str | None:
    declared = lint_prd.declared_render_actions(config)
    if declared is None:
        return None
    terms = swept_terms(config, haystack, candidates)
    if not terms:
        return None
    listed = ', '.join(declared) if declared else 'none'
    evidence = f'plurai.config.yaml declares render_actions: {listed}; none of the swept verbs occurs in {where}'
    return (
        f'  - kind: tool_surface\n    subject: {lint_prd.RENDER_SURFACE_SUBJECT}\n    predicate: omits\n'
        f'    evidence: {json.dumps(evidence, ensure_ascii=False)}\n    source: plurai.config.yaml\n'
        f'    check:\n      kind: absence\n      terms: [{", ".join(terms)}]\n      match: any'
    )


def emit_quoted(rows: list[Unit], sources: list[Source]) -> str:
    """The quoted block: per source range, each run of its units separated only by blank lines is one fence holding the
    source's own lines from the run's first line to its last. A file given as several ranges is quoted once, each
    range under its own entry."""
    out = [QUOTED_HEADING, '', 'These are the sentences the agent was told, copied byte for byte from its instruction files.']
    for source in sources:
        mine = [u for u in rows if u.path == source.path and source.lo <= u.lo and u.hi <= source.hi]
        runs: list[list[Unit]] = []
        for unit in mine:
            if runs and all(not ln.strip() for ln in source.lines[runs[-1][-1].hi : unit.lo - 1]):
                runs[-1].append(unit)
            else:
                runs.append([unit])
        for run in runs:
            lo, hi = run[0].lo, run[-1].hi
            body = '\n'.join(source.lines[lo - 1 : hi])
            longest = max([len(m) for m in re.findall(r'`+', body)] or [0])
            fence = '`' * max(3, longest + 1)
            out += ['', f'From `{source.path}:{lo}-{hi}`:' if hi > lo else f'From `{source.path}:{lo}`:', '', fence, body, fence]
    return '\n'.join(out)


def check_byte_exact(rows: list[Unit], rules_yaml: str) -> None:
    """Parse the emitted rule facts back and compare each ``evidence`` to its unit's lines."""
    try:
        loaded = yaml.safe_load('facts:\n' + rules_yaml)
    except yaml.YAMLError as exc:
        raise NotByteExact(f'the generated facts are not YAML: {exc}')
    facts = [f for f in loaded['facts'] if f.get('kind') == lint_prd.RULE_KIND]
    for fact, unit in zip(facts, rows):
        if fact.get('evidence') != unit.text or fact.get('subject') != unit.subject or fact.get('source') != unit.subject:
            raise NotByteExact(f'the fact for {unit.subject} does not parse back to the source lines; nothing was written.')
    if len(facts) != len(rows):
        raise NotByteExact('the generated fact count differs from the unit count; nothing was written.')


# --- splice into a PRD -----------------------------------------------------------------------------------


def _region(begin_end: tuple[str, str], body: str) -> list[str]:
    return [begin_end[0], *body.split('\n'), begin_end[1]]


def _replace_region(lines: list[str], begin_end: tuple[str, str], body: str) -> bool:
    """Replace the lines between a marker pair with ``body``; False when the PRD has no such pair."""
    begin = next((i for i, ln in enumerate(lines) if ln.strip() == begin_end[0].strip()), None)
    end = next((i for i, ln in enumerate(lines) if ln.strip() == begin_end[1].strip()), None)
    if begin is None or end is None or end < begin:
        return False
    lines[begin : end + 1] = _region(begin_end, body)
    return True


#: The path the PRD template's `instruction_sources:` example names; a block that still carries it holds no declaration.
TEMPLATE_SOURCE_PATH = 'path/to/prompts.py'


def _without_template_sources(body: list[str]) -> list[str]:
    """``body`` minus an `instruction_sources:` declaration that declares nothing: the template's unfilled example, or an
    empty list (`[]`, or the key alone), which the lint reads as absent. A declaration the author filled stays and is
    refused, since two declarations of the key cannot both be read."""
    at = next((i for i, ln in enumerate(body) if ln.startswith(lint_prd.INSTRUCTION_SOURCES_KEY + ':')), None)
    if at is None:
        return body
    # The declaration ends at the next key; a list item at column 0 still belongs to it.
    end = next((i for i in range(at + 1, len(body)) if re.match(r'^\S', body[i]) and not body[i].startswith(('#', '- '))), len(body))
    value = body[at][len(lint_prd.INSTRUCTION_SOURCES_KEY) + 1 :].split('#', 1)[0].strip()
    items = [ln for ln in body[at + 1 : end] if ln.strip() and not ln.lstrip().startswith('#')]
    empty = value in ('', '[]') and not items
    if not empty and not any(TEMPLATE_SOURCE_PATH in ln for ln in body[at:end]):
        raise Usage("the PRD's facts block already declares `instruction_sources:` outside the scaffold markers; delete it (or move it between the markers) and re-run.")
    return [*body[:at], *body[end:]]


#: `facts:` alone on its line, as an empty list, or with a trailing comment: the shapes rule items can follow.
_FACTS_HEAD = re.compile(r'^facts:\s*(?:\[\s*\])?\s*(?:#.*)?$')
#: What the PRD template's example fact carries as its subject: an unfilled placeholder, never a measurement.
TEMPLATE_PLACEHOLDER_SUBJECT = '<entity>.<field>'


def _without_template_placeholder(body: list[str]) -> list[str]:
    """``body`` minus the template's example fact (and the comment block above it): it is a shape, and a placeholder
    that survives the copy is read as a fabricated measurement."""
    at = next((i for i, ln in enumerate(body) if TEMPLATE_PLACEHOLDER_SUBJECT in ln and ln.lstrip().startswith('subject:')), None)
    if at is None:
        return body
    start = at
    while start > 0 and not body[start].lstrip().startswith('- '):
        start -= 1
    while start > 0 and body[start - 1].lstrip().startswith('#'):
        start -= 1
    end = at + 1
    while end < len(body) and (body[end].startswith('    ') or body[end].lstrip().startswith('#')):
        end += 1
    return [*body[:start], *body[end:]]


def _without_closing_fact(body: list[str]) -> list[str]:
    """``body`` minus a `RENDER_ACTIONS` / `omits` fact the author wrote before the scaffold: the scaffold writes that
    fact, and one subject + predicate may be typed once."""
    starts = [i for i, ln in enumerate(body) if re.match(r'^\s*- ', ln)]
    if not starts:
        return body
    indent = min(len(body[i]) - len(body[i].lstrip()) for i in starts)
    firsts = [i for i in starts if len(body[i]) - len(body[i].lstrip()) == indent]
    ends = [*firsts[1:], len(body)]
    kept = list(body)
    for lo, hi in reversed(list(zip(firsts, ends))):
        item = ' '.join(body[lo:hi])
        if re.search(rf'subject:\s*["\']?{lint_prd.RENDER_SURFACE_SUBJECT}["\']?(?:\s|,|}}|$)', item) and re.search(r'predicate:\s*["\']?omits["\']?(?:\s|,|}|$)', item):
            # a comment block that sits above the item goes with it
            while lo > 0 and body[lo - 1].lstrip().startswith('#'):
                lo -= 1
            del kept[lo:hi]
    return kept


def check_spliced(text: str, rows: list[Unit], sources: list[Source]) -> None:
    """Parse the facts block of the spliced PRD and compare it to what was spliced: a block that no longer parses, or
    that lost a scaffold fact to a repeated key, is never written."""
    block = lint_prd._facts_block(lint_prd._without_appendix(text))
    try:
        loaded = yaml.safe_load(block) if block is not None else None
    except yaml.YAMLError as exc:
        raise NotByteExact(f'the spliced facts block is not YAML ({exc}); nothing was written.')
    facts = loaded.get('facts') if isinstance(loaded, dict) else None
    declared = loaded.get(lint_prd.INSTRUCTION_SOURCES_KEY) if isinstance(loaded, dict) else None
    mine = [f for f in facts or [] if isinstance(f, dict) and f.get('kind') == lint_prd.RULE_KIND and f.get('subject') in {u.subject for u in rows}]
    if not isinstance(facts, list) or not isinstance(declared, list) or len(declared) != len(sources) or len(mine) != len(rows):
        raise NotByteExact('the spliced facts block does not carry the scaffold\'s sources and rule facts; nothing was written.')


def splice(prd: str, sources_yaml: str, rules_yaml: str, quoted_md: str) -> str:
    lines = prd.split('\n')
    have = [
        _replace_region(lines, YAML_SOURCES, sources_yaml),
        _replace_region(lines, YAML_RULES, rules_yaml),
        _replace_region(lines, MD_QUOTED, quoted_md),
    ]
    if all(have):
        return '\n'.join(lines)
    if any(have):
        raise Usage('the PRD carries some scaffold markers but not all three pairs; delete the stray ones and re-run.')
    fenced = lint_prd._fenced(lines)
    heading = next((i for i, (ln, f) in enumerate(zip(lines, fenced)) if not f and ln.startswith('#') and lint_prd._is_facts_heading(ln)), None)
    if heading is None:
        block = '\n'.join(
            [FACTS_HEADING, '', '```yaml', *_region(YAML_SOURCES, sources_yaml), 'facts:', *_region(YAML_RULES, rules_yaml), '```', '', *_region(MD_QUOTED, quoted_md), '']
        )
        at = next(
            (i for i, (ln, f) in enumerate(zip(lines, fenced)) if not f and re.match(r'^##\s+(?:[2-9]\.|VERBATIM SOURCE APPENDIX)', ln, re.IGNORECASE)),
            None,
        )
        if at is None:
            return prd.rstrip('\n') + '\n\n' + block
        lines[at:at] = block.split('\n')
        return '\n'.join(lines)
    # The block is the section's own: a fence past the next heading belongs to another section (the appendix, say).
    end = next((i for i in range(heading + 1, len(lines)) if not fenced[i] and lines[i].startswith('#')), len(lines))
    opens = next((i for i in range(heading + 1, end) if fenced[i] and lint_prd._FENCE_OPEN.match(lines[i])), None)
    closes = next((i for i in range(opens + 1, len(lines)) if lint_prd._FENCE_BARE.match(lines[i])), None) if opens is not None else None
    if opens is None or closes is None:
        # A heading with no fenced block under it: the block goes straight under the heading.
        block = ['', '```yaml', *_region(YAML_SOURCES, sources_yaml), 'facts:', *_region(YAML_RULES, rules_yaml), '```', '', *_region(MD_QUOTED, quoted_md)]
        lines[heading + 1 : heading + 1] = block
        return '\n'.join(lines)
    body = _without_template_placeholder(_without_template_sources(lines[opens + 1 : closes]))
    heads = [i for i, ln in enumerate(body) if ln.startswith('facts:')]
    if len(heads) > 1 or (heads and not _FACTS_HEAD.match(body[heads[0]])):
        raise Usage("the PRD's facts block has a `facts:` key this script cannot extend (a repeat, or items written on the key's own line); write `facts:` alone and the items beneath it, then re-run.")
    facts_at = heads[0] if heads else None
    if facts_at is not None and f'subject: {lint_prd.RENDER_SURFACE_SUBJECT}' in rules_yaml:
        body = [*body[: facts_at + 1], *_without_closing_fact(body[facts_at + 1 :])]
    if facts_at is None:
        body = [*_region(YAML_SOURCES, sources_yaml), 'facts:', *_region(YAML_RULES, rules_yaml), *body]
    else:
        # `facts: []` is the empty list the rules would otherwise follow: make it the open list they belong to.
        body = [*_region(YAML_SOURCES, sources_yaml), *body[:facts_at], 'facts:', *_region(YAML_RULES, rules_yaml), *body[facts_at + 1 :]]
    lines[opens + 1 : closes] = body
    after = opens + 1 + len(body) + 1  # first line after the closing fence
    lines[after:after] = ['', *_region(MD_QUOTED, quoted_md)]
    return '\n'.join(lines)


# --- CLI -------------------------------------------------------------------------------------------------


def slot_lines(sources: list[Source], unrowed: dict[tuple[str, int, int], list[Decline]] | None = None) -> list[tuple[str, int, str]]:
    """``(path, line, text)`` of every OPEN line of template slots only inside the source ranges: outside a markdown
    source's reference material, and covered by no decline that closes it for the lint (`filled-by`, `data-slot`, or a
    `not-loaded` file)."""
    closing = (lint_prd.UNROWED_FILLED_BY, lint_prd.UNROWED_DATA_SLOT, lint_prd.UNROWED_NOT_LOADED)
    out: list[tuple[str, int, str]] = []
    for s in sources:
        skipped = reference_lines(s) | {n for d in (unrowed or {}).get(s.span, []) if d.cls in closing for n in range(d.lo, d.hi + 1)}
        out += [(s.path, n, s.lines[n - 1].strip()) for n in range(s.lo, s.hi + 1) if n not in skipped and slot_only(s.lines[n - 1])]
    return out


def _summary(
    rows: list[Unit],
    unrowed: dict[tuple[str, int, int], list[Decline]],
    omits: str | None,
    not_given: list[tuple[str, str]] | None = None,
    slots: list[tuple[str, int, str]] | None = None,
) -> str:
    out = [f'scaffold_rule_facts: {len(rows)} rule fact(s), {sum(len(v) for v in unrowed.values())} decline(s)' + (', 1 omits fact' if omits else '')]
    for i, unit in enumerate(rows, 1):
        first = unit.lines[0].strip()
        out.append(f'  {i:>3}. {unit.subject} ({len(unit.lines)} line(s)) "{first[:70]}"')
    for (path, _lo, _hi), declines in unrowed.items():
        for d in declines:
            out.append(f'  declined {path}:{d.lo}-{d.hi} — {d.cls}')
    for path, n, text in slots or ():
        out.append(
            f'  `{path}:{n}` `{text}` is a template slot, open until you decline it: --decline {path}:{n}={lint_prd.UNROWED_FILLED_BY}@<the range '
            f'that fills it> (passed as --source) or --decline {path}:{n}={lint_prd.UNROWED_DATA_SLOT}@<the line that fills it>.'
        )
    for path, site in not_given or ():
        out.append(f'  `{path}` is loaded by the agent ({site}) and is not a --source: pass it as --source {path}, or decline the whole file with =not-loaded@<load site>.')
    out.append(
        'Read each unit: one that is not an instruction (a persona line, an example, a table) is re-declined with '
        '--decline PATH:LO-HI=not-an-instruction and the script re-run. Then add the facts only you can measure.'
    )
    return '\n'.join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the agent's instruction facts into the PRD byte for byte.")
    parser.add_argument('--repo-root', type=Path, default=Path('.'), help='the agent repo the sources are relative to')
    parser.add_argument('--source', action='append', default=[], metavar='PATH[:LO-HI]', help='a file holding the agent\'s instructions; repeatable')
    parser.add_argument('--decline', action='append', default=[], metavar='PATH:LO-HI=CLASS', help='decline whole units (a unit is a line, or the lines one wrapped sentence spans); repeatable')
    parser.add_argument('--row', action='append', default=[], metavar='PATH:LO-HI', help='keep as a rule a unit that is declined by default (front matter, a fenced block, a table row); repeatable')
    parser.add_argument('--config', type=Path, help='plurai.config.yaml; its render_actions: decides whether an omits fact is written')
    parser.add_argument('--omit-terms', metavar='VERB[,VERB...]', help='the verbs the RENDER_ACTIONS sweep names, in place of the defaults; declared verbs and any that occur in a source are still dropped')
    parser.add_argument('--profile', type=Path, metavar='data_profile.md', help="also propose the measured facts the scan's inputs settle (closed URL sets, URLs outside them, uncovered out-of-scope topics), from the files this profile names")
    parser.add_argument('--skip-measured', action='append', default=[], metavar='SUBJECT', help='drop one proposed measured fact by its subject; repeatable')
    parser.add_argument('--corpus', action='append', default=[], type=Path, metavar='PATH', help='a corpus file or directory the omits sweep terms must not occur in; repeatable')
    out = parser.add_mutually_exclusive_group(required=True)
    out.add_argument('--into', type=Path, metavar='PRD', help='splice the output into this PRD between scaffold markers')
    out.add_argument('--out-dir', type=Path, metavar='DIR', help='write instruction_sources.yaml, rule_facts.yaml and instructions_quoted.md here')
    args = parser.parse_args(argv)
    if not args.source:
        parser.error('give at least one --source.')

    try:
        repo_root = args.repo_root.resolve()
        sources = read_sources(repo_root, args.source)
        rows, unrowed = plan(sources, parse_declines(args.decline, sources), parse_rows(args.row, sources))
        config: dict = {}
        if args.config:
            if not args.config.is_file():
                raise Usage(f'--config {args.config} is not a file.')
            loaded = yaml.safe_load(args.config.read_text(encoding='utf-8'))
            config = loaded if isinstance(loaded, dict) else {}
        haystack = '\n'.join('\n'.join(s.lines[s.lo - 1 : s.hi]) for s in sources)
        for root in args.corpus:
            files = [root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file()) if root.is_dir() else None
            if files is None:
                raise Usage(f'--corpus {root} is not a file or directory.')
            haystack += '\n' + '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files)
        where = ', '.join(s.path for s in sources) + (' or the corpus' if args.corpus else '')
        candidates = tuple(t.strip() for t in args.omit_terms.split(',') if t.strip()) if args.omit_terms else CANDIDATE_RENDER_VERBS
        omits = emit_omits(config, haystack, where, candidates)
        measured: list[measured_candidates.Candidate] = []
        if args.profile:
            if not args.profile.is_file():
                raise Usage(f'--profile {args.profile} is not a file.')
            skipped = set(args.skip_measured)
            measured = [
                c for c in measured_candidates.candidates(repo_root, args.profile.read_text(encoding='utf-8', errors='replace'), [s.path for s in sources])
                if c.subject not in skipped
            ]
        rules_yaml = '\n'.join([*(emit_rule(u) for u in rows), *(emit_measured(c) for c in measured), *([omits] if omits else [])])
        check_byte_exact(rows, rules_yaml)
        sources_yaml, quoted_md = emit_sources(sources, unrowed), emit_quoted(rows, sources)
        if args.into:
            if not args.into.is_file():
                raise Usage(f'--into {args.into} is not a file; write the PRD first (the scaffold fills its facts, it does not write the prose).')
            spliced = splice(args.into.read_text(encoding='utf-8'), sources_yaml, rules_yaml, quoted_md)
            check_spliced(spliced, rows, sources)
            args.into.write_text(spliced, encoding='utf-8')
        else:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            (args.out_dir / 'instruction_sources.yaml').write_text(sources_yaml + '\n', encoding='utf-8')
            (args.out_dir / 'rule_facts.yaml').write_text(rules_yaml + '\n', encoding='utf-8')
            (args.out_dir / 'instructions_quoted.md').write_text(quoted_md + '\n', encoding='utf-8')
    except Usage as exc:
        print(f'scaffold_rule_facts: {exc}', file=sys.stderr)
        return 2
    except NotByteExact as exc:
        print(f'scaffold_rule_facts: {exc}', file=sys.stderr)
        return 1
    given = {(repo_root / s.path).resolve() for s in sources}
    not_given = [(path, site) for path, site in lint_prd.instruction_candidates(repo_root) if (repo_root / path).resolve() not in given]
    print(_summary(rows, unrowed, omits, not_given, slot_lines(sources, unrowed)))
    for c in measured:
        print(f'  measured {c.subject} ({c.kind}): {c.evidence[:110]}')
    if measured:
        print('Confirm each measured fact against the source it cites, or drop it with --skip-measured SUBJECT and re-run.')
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
