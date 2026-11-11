#!/usr/bin/env python3
"""Customer artifacts say the verdict and the findings — never how our pipeline reached them.

    customer_text_guard.py check [--workdir W]

Reads every customer artifact the workdir holds (`customer_text_vocabulary.json` `artifacts`: scenarios.md, evals.md,
agent_topology.md, data_profile.md, the page's copy of agent_findings.md / .jsonl and of auto_regressions.json, fixes.md, the scored run's banners and
all of its analytics.md, and every text the filled report.html embeds but the customer's own voice and world: `skip` files,
`skip_fields` keys) against the typed internal vocabulary in that file,
plus one structural rule: fixes.md lists agent fixes only — a section homed to an internal home (test-setup, mock-env) or
headed as a test-setup follow-up belongs in `fixes_md.internal_file`. A kind with no `path` (the final chat message) is
read only from the text handed to `text_violations` (`hooks/report_guard.py` hands it the hand-off).

Exit 0: nothing internal. Exit 4: one `INTERNAL <path>:<line> [<term>/<class>] "<excerpt>" — <rewrite>` line per
hit, so the flow rewrites that text (or moves it to the internal file) and checks again. Exit 2: usage.

`violations(workdir, texts=…, kinds=…)` is the import surface: `publish_report.fill` refuses to publish while it is
non-empty (skip `internal_text`), with `texts` the page's own copies, and checks the filled page itself before it replaces
the last one (`text_violations('report_html', …)`). `customer_copy(rel, text)` is the projection the page
gets: agent_findings.md without its `internal_fields` lines. Unreadable data or files are no violation (said on
stderr): the guard never wedges a hand-off it cannot judge. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DATA = Path(__file__).with_name('customer_text_vocabulary.json')
EXIT_OK, EXIT_USAGE, EXIT_INTERNAL = 0, 2, 4
EXCERPT_CHARS = 120

_COMMENT = re.compile(r'<!--.*?-->', re.S)
_FIELD = re.compile(r'^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_`]{0,2}[ \t]*(?P<key>[a-z][a-z ]*?)[ \t]*[*_`]{0,2}[ \t]*:', re.I)
_SECTION = re.compile(r'^## ', re.M)
_HOME = re.compile(r'\bhome:\s*\**\s*(?P<home>[a-z][\w -]*?)\s*\**\s*(?:[(—–/.,;|]|$)', re.I | re.M)
#: The banner block `release_verdict.prepend` writes at the head of analytics.md.
_BANNER = re.compile(r'\A<!-- plurai:release-verdict -->\n((?:>[^\n]*\n)*)')
#: The data element the filled report.html carries (`scripts/report/fill_report.py` DATA_ID).
_PAGE_DATA = re.compile(r'<script\b[^>]*\bid="plurai-report-data"[^>]*>(.*?)</script>', re.S)
#: A parsed file's machine value — an id, an enum, a path: one token the page maps or links, never prose.
_TOKEN = re.compile(r'[\w.:/@#-]*')


@dataclass(frozen=True)
class Term:
    id: str
    cls: str
    pattern: re.Pattern[str]
    kinds: frozenset[str] | None
    say_instead: str
    #: A line this matches is the term's sanctioned use.
    unless: re.Pattern[str] | None = None
    #: A line this matches opens a block that is the term's sanctioned use: the bullet lines that follow it, up to the first
    #: line that is not one (a stop message's "Done so far" list names what exists with its id).
    unless_block: re.Pattern[str] | None = None
    #: Read the line with its http(s) links blanked out: an id inside a link is the address of the customer's own
    #: platform page (a trace on LangSmith or Langfuse), not our machinery shown to them. A link to OUR backend
    #: stays: an id in it is still ours.
    outside_urls: bool = False


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    term: str
    cls: str
    excerpt: str
    say_instead: str

    def __str__(self) -> str:
        return f'INTERNAL {self.path}:{self.line} [{self.term}/{self.cls}] "{self.excerpt}" — {self.say_instead}'


def load(path: Path = DATA) -> dict:
    """The vocabulary, or `{}` with one stderr line (the guard then reads nothing as internal)."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'customer_text_guard: {path.name} unreadable ({exc}) — no customer text is checked', file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def terms(data: Mapping) -> list[Term]:
    out = []
    for raw in data.get('terms') or []:
        flags = 0 if raw.get('case_sensitive') else re.IGNORECASE
        kinds = raw.get('artifacts')
        unless = re.compile(raw['unless_line'], re.IGNORECASE) if raw.get('unless_line') else None
        block = re.compile(raw['unless_block'], re.IGNORECASE) if raw.get('unless_block') else None
        out.append(
            Term(raw['id'], raw['class'], re.compile(raw['pattern'], flags), frozenset(kinds) if kinds else None, raw['say_instead'], unless,
                 block, bool(raw.get('outside_urls')))
        )
    return out


_LIST_ITEM = re.compile(r'[ \t]*[-*+][ \t]')
_URL = re.compile(r'https?://[^\s)\]>"\']+', re.IGNORECASE)
def _backend_host() -> str | None:
    """The host of the resolved backend (`hooks/_login_store.py`); a link to it is ours, whatever id it carries."""
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    try:
        import _login_store  # noqa: PLC0415 - found through the line above
    except Exception:  # noqa: BLE001 - without the resolver only `plurai.ai` hosts are ours
        return None
    backend = _login_store.resolve_backend()
    return urlsplit(backend.url).hostname if backend.configured else None


def _ours(url: str) -> bool:
    """Whether ``url`` points at Plurai: a `plurai.ai` host, or the host of the resolved backend."""
    host = urlsplit(url).hostname or ''
    if host == 'plurai.ai' or host.endswith('.plurai.ai'):
        return True
    return host == _backend_host()


def _blank_platform_links(line: str) -> str:
    """``line`` with every link to a non-Plurai page blanked to the same length, so offsets still point into it."""
    return _URL.sub(lambda m: m.group() if _ours(m.group()) else ' ' * len(m.group()), line)


def _excerpt(line: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 40)
    text = ' '.join(line[start:start + EXCERPT_CHARS].split())
    return ('…' if start else '') + text.replace('"', "'")


def strip_internal_fields(text: str, fields: list[str], *, keep_lines: bool = False) -> str:
    """agent_findings.md without the field lines that feed our own loop (`internal_fields`), continuation lines included.
    `keep_lines` blanks them instead, so every remaining line keeps its number in the file."""
    keys = {f.lower() for f in fields}
    out, dropping = [], False
    for line in text.splitlines(keepends=True):
        field = _FIELD.match(line)
        if field and line.lstrip()[:1] in '-*+':
            dropping = field.group('key').strip().lower() in keys
        elif not line.startswith((' ', '\t')) or not line.strip():
            dropping = False
        if not dropping:
            out.append(line)
        elif keep_lines:
            out.append('\n')
    return ''.join(out)


def customer_copy(rel: str, text: str, data: Mapping | None = None) -> str:
    """What the customer reads of `rel`: agent_findings.md loses its internal field lines; anything else is itself."""
    data = load() if data is None else data
    if rel == (data.get('artifacts') or {}).get('agent_findings_md', {}).get('path'):
        return strip_internal_fields(text, list(data.get('internal_fields') or []))
    return text


def _lines(reader: str, rel: str, text: str, data: Mapping) -> Iterator[tuple[int, str]]:
    """`(line number, text)` of what the customer reads of one artifact."""
    if reader == 'findings_rows':
        for n, row in enumerate(text.splitlines(), 1):
            try:
                doc = json.loads(row)
            except ValueError:
                continue
            if isinstance(doc, dict) and isinstance(doc.get('title'), str):
                yield n, doc['title']
        return
    if reader == 'regression_rows':
        # The page's regressions lists (`regressions.page_copy`): what each kept or not-kept row says.
        try:
            doc = json.loads(text)
        except ValueError:
            return
        n = 0
        for key in ('record', 'skipped'):
            for row in (doc.get(key) if isinstance(doc, dict) and isinstance(doc.get(key), list) else []):
                for field in ('title', 'reason'):
                    n += 1
                    if isinstance(row, dict) and isinstance(row.get(field), str):
                        yield n, row[field]
        return
    if reader == 'verdict_banner':
        try:
            doc = json.loads(text)
        except ValueError:
            return
        banner = doc.get('customer_banner', doc.get('banner')) if isinstance(doc, dict) else None
        if isinstance(banner, str):
            yield 1, banner
        # The Agent line beside it (`release_verdict.agent_line`) is customer text too.
        if isinstance(doc, dict) and isinstance(doc.get('agent_line'), str):
            yield 2, doc['agent_line']
        return
    if reader == 'banner':
        block = _BANNER.match(text)
        if block:
            yield from enumerate(block.group(1).splitlines(), 2)
        return
    if reader in ('head', 'body'):
        # The report's head below its banner (read as `banner`): every line before its first `## ` section — the title and
        # the run's provenance line. The body is every line from that section on.
        block = _BANNER.match(text)
        skip = block.group(0).count('\n') if block else 0
        lines = _COMMENT.sub(lambda m: '\n' * m.group(0).count('\n'), text).splitlines()
        cut = next((i for i in range(skip, len(lines)) if lines[i].startswith('## ')), len(lines))
        span = range(skip, cut) if reader == 'head' else range(cut, len(lines))
        yield from ((i + 1, lines[i]) for i in span)
        return
    if reader == 'findings_markdown':
        text = strip_internal_fields(text, list(data.get('internal_fields') or []), keep_lines=True)
    # A comment and a stripped field are blanked line for line, so every line keeps its number.
    text = _COMMENT.sub(lambda m: '\n' * m.group(0).count('\n'), text)
    yield from enumerate(text.splitlines(), 1)


def _strings(value: object, fields: Collection[str] = ()) -> Iterator[str]:
    """Every prose string value of a parsed file (machine tokens aside), but the values under a `fields` key, at any depth."""
    if isinstance(value, str):
        if not _TOKEN.fullmatch(value):
            yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if key not in fields:
                yield from _strings(item, fields)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, fields)


def _page_parts(text: str, skip: list[str], skip_fields: Mapping[str, list[str]]) -> Iterator[tuple[str, str, str]]:
    """`(embedded path, reader, text)` of every file the filled page embeds but those `skip` names: a findings file
    through its own reader, any other text as markdown, a parsed file as its prose values one per line, less the values
    under the keys `skip_fields` names for its path (the customer's voice and world inside a file we otherwise wrote)."""
    found = _PAGE_DATA.search(text)
    try:
        files = json.loads(found.group(1)).get('files') if found else None
    except (ValueError, AttributeError):
        files = None
    if not isinstance(files, Mapping):
        return
    skipped = re.compile('|'.join(skip) or '(?!)')
    for name, body in files.items():
        if skipped.search(str(name)):
            continue
        if isinstance(body, str):
            reader = 'findings_rows' if name.endswith('.jsonl') else 'findings_markdown' if name.endswith('agent_findings.md') else 'markdown'
            yield str(name), reader, body
        else:
            fields = {key for pattern, keys in skip_fields.items() if re.search(pattern, str(name)) for key in keys}
            yield str(name), 'markdown', '\n'.join(' '.join(s.split()) for s in _strings(body, fields))


def _structure(rel: str, text: str, rule: Mapping) -> Iterator[Violation]:
    """fixes.md's structural rule: an internally homed section, or a test-setup heading, is not the customer's."""
    target = str(rule.get('internal_file') or 'the internal file')
    homes = {h.lower() for h in rule.get('internal_homes') or []}
    heading = re.compile(rf'^#+ .*(?:{rule.get("internal_headings") or "(?!)"})', re.I | re.M)
    for match in heading.finditer(text):
        yield Violation(rel, text.count('\n', 0, match.start()) + 1, 'internal_section', 'repair_item', match.group(0).strip()[:EXCERPT_CHARS],
                        f'move the section to {target}: fixes.md lists agent fixes only')
    starts = [m.start() for m in _SECTION.finditer(text)]
    for begin, end in zip(starts, [*starts[1:], len(text)]):
        home = _HOME.search(text, begin, end)
        if home and home.group('home').strip().lower() in homes:
            title = text[begin:text.find('\n', begin) if '\n' in text[begin:end] else end].strip()
            yield Violation(rel, text.count('\n', 0, begin) + 1, 'internal_home', 'repair_item', title[:EXCERPT_CHARS],
                            f'home `{home.group("home").strip()}` is ours: move the section to {target}')


def _scored_run(workdir: Path) -> Path | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import stage_landed  # noqa: PLC0415 - sibling, stdlib only: THE scored-run reader

    found = stage_landed.scored_run_json(workdir)
    return found.parent if found is not None and found.parent.is_dir() else None


def _artifact_paths(workdir: Path, spec: Mapping) -> list[Path]:
    if not spec.get('path'):
        return []
    if spec.get('under') == 'scored_run':
        run = _scored_run(workdir)
        dirs = [run, *sorted(run.glob('trial_*'))] if run is not None else []
        return [d / spec['path'] for d in dirs if (d / spec['path']).is_file()]
    path = workdir / spec['path']
    return [path] if path.is_file() else []


def _relative(path: Path, workdir: Path) -> str:
    try:
        return path.relative_to(workdir).as_posix()
    except ValueError:
        return path.as_posix()


def _check(kind: str, spec: Mapping, rel: str, text: str, data: Mapping, vocab: list[Term]) -> list[Violation]:
    """Every internal line of one artifact's text, as the customer reads it."""
    reader = spec.get('reader', 'markdown')
    if reader == 'report_page':
        out = []
        for name, inner, body in _page_parts(text, list(spec.get('skip') or []), spec.get('skip_fields') or {}):
            out += _check(kind, {**spec, 'reader': inner}, f'{rel}#{name}', body, data, vocab)
        return out
    out = []
    open_blocks: set[str] = set()
    for n, line in _lines(reader, rel, text, data):
        for term in vocab:
            if term.kinds is not None and kind not in term.kinds:
                continue
            in_block = False
            if term.unless_block is not None:
                in_block = bool(term.unless_block.search(line)) or (term.id in open_blocks and _LIST_ITEM.match(line) is not None)
                if in_block:
                    open_blocks.add(term.id)
                else:
                    open_blocks.discard(term.id)
            subject = _blank_platform_links(line) if term.outside_urls else line
            match = term.pattern.search(subject)
            if match and not in_block and not (term.unless is not None and term.unless.search(line)):
                out.append(Violation(rel, n, term.id, term.cls, _excerpt(line, match), term.say_instead))
    if kind == 'fixes_md':
        out.extend(_structure(rel, text, data.get('fixes_md') or {}))
    return out


def text_violations(kind: str, text: str, data: Mapping | None = None, rel: str | None = None, reader: str | None = None) -> list[Violation]:
    """Every internal line of `text` read as artifact `kind` — the hand-off of a chat, or a page not yet in place.
    `reader` reads it with that reader instead of the kind's own: one value a page embeds is `markdown` under the page's kind."""
    data = load() if data is None else data
    spec = (data.get('artifacts') or {}).get(kind)
    if not isinstance(spec, Mapping):
        return []
    if reader:
        spec = {**spec, 'reader': reader}
    return _check(kind, spec, rel or str(spec.get('path') or kind), text, data, terms(data))


def _regressions_page_copy(workdir: Path) -> str | None:
    """The page's copy of the regressions plan (`regressions.page_copy`): the plan on disk holds operator reasons the page
    never shows, so the guard reads what the page gets. None (said on stderr) when it cannot be derived."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import regressions  # noqa: PLC0415 - sibling: the one derivation of the page's regressions lists

        return regressions.page_copy(workdir)
    except Exception as exc:  # noqa: BLE001 - the guard never wedges a hand-off it cannot judge
        print(f'customer_text_guard: the regressions page copy could not be derived ({type(exc).__name__}: {exc}) — not checked', file=sys.stderr)
        return None


def violations(workdir: Path, texts: Mapping[str, str] | None = None, data: Mapping | None = None,
               kinds: Collection[str] | None = None) -> list[Violation]:
    """Every internal line a customer would read in `workdir`. `texts` (relpath -> text) replaces a file's text with
    the copy the customer actually gets (the page's findings copy); `kinds` limits the read to those artifact kinds."""
    data = load() if data is None else data
    vocab = terms(data)
    out: list[Violation] = []
    for kind, spec in (data.get('artifacts') or {}).items():
        if kinds is not None and kind not in kinds:
            continue
        for path in _artifact_paths(workdir, spec):
            rel = _relative(path, workdir)
            if texts is not None and rel in texts:
                text = texts[rel]
            elif spec.get('reader') == 'regression_rows':
                copied = _regressions_page_copy(workdir)
                if copied is None:
                    continue
                text = copied
            else:
                try:
                    text = path.read_text(encoding='utf-8')
                except (OSError, UnicodeDecodeError) as exc:
                    print(f'customer_text_guard: {rel} unreadable ({exc}) — not checked', file=sys.stderr)
                    continue
            out += _check(kind, spec, rel, text, data, vocab)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='name every internal line a customer artifact carries')
    check.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from harness_fingerprint import resolve_workdir  # noqa: PLC0415 - the one cascade; only the CLI needs it
    import deliverables  # noqa: PLC0415 - the page's own root (a named env resolves to its active env)

    try:
        workdir = deliverables.page_root(resolve_workdir(args.workdir))
    except SystemExit:
        return EXIT_USAGE
    found = violations(Path(workdir))
    for violation in found:
        print(violation)
    if found:
        return EXIT_INTERNAL
    print('customer_text_guard: no internal text on a customer artifact')
    return EXIT_OK


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
