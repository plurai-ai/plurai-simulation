#!/usr/bin/env python3
"""Copy knowledge written inline in the agent's code into the Markdown the test knowledge base is built from.

The scan types each literal on the map (`grounding.inline`: `{name, tool, source}`), `source` being
`<code file>::<variable>` — the variable the literal is assigned to, inside its function or class
(`agent.py::lookup_knowledge_base.kb`) or at module level (`agent.py::FAQ_ARTICLES`), found BY NAME so an edit
that moves lines never loses it. The value is `ast.literal_eval` of that assignment, the interpreter's own value:
a `dict[str, str]` (key -> article) or a `list`/`tuple` of `str` (position -> article).

Each entry becomes `<workdir>/knowledge_inline/<slug of name>.md`: `# <name>`, then `## <key>` and the article's
exact text per article. An article that leaves a code fence open gets a closing fence line AFTER its text, so the
next key stays a heading; its own bytes never change. The folder is recorded in `grounding.sources` and the
manifest's `sources.corpus_roots`; `.corpus_scope` gets `inline:`, one `inline_sha256: <file> <sha256>` per file
and, when the folder is the only source, `declared_units`. Every other line is kept.

A literal that is not plain text articles is refused with a typed reason (`REFUSALS`) and never guessed at.
`--when scan` (Step 3c) writes and takes back what is refused or no longer declared; `--when suite` (the grounding
gate) rewrites what changed and keeps the last copy of a refused entry; `--when run` (run start) writes nothing and
says what changed since the suite's copy. Every mode warns on a corpus source in the workdir no exporter stamped and
on a declared code file listed as a source. `warning:` lines are plain words for the customer; `fix:` lines are for
the session only.

Stdout: per-base lines, `warning:`/`fix:` lines, then `KNOWLEDGE_INLINE=<none|unchanged|written|stale|kept|refused>`
(with `reason=<codes>` when refused). Exit 0, or 2 when the map or a workdir file cannot be read. Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional

FOLDER = 'knowledge_inline'
TOPOLOGY = 'artifacts/agent_topology.json'
MANIFEST = 'bench_manifest.json'
CORPUS_SCOPE = '.corpus_scope'
#: The `.corpus_scope` keys this script owns. A rerun replaces its own lines and keeps every other.
STAMP_KEYS = ('inline:', 'inline_sha256:')
DECLARED = 'declared_units:'
#: The agentforce knowledge export's folder and stamp (`rewire_agentforce.KNOWLEDGE_SNAPSHOT_DIR`): the other
#: exporter whose folder inside the workdir is a stamped copy, not a hand-written file.
SNAPSHOT_FOLDER = 'knowledge_snapshot'
SNAPSHOT_STAMP = 'snapshot_sha256:'
WHEN = ('scan', 'suite', 'run')
_SYMBOL = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*')

#: What the reader is told per refusal: why nothing was copied, in product words.
REFUSALS = {
    'bad_source': 'is not written as `<file>::<variable>`',
    'missing': 'names a file that is not there',
    'unsupported_language': 'is not Python code, which this version cannot read',
    'unparseable': 'does not parse as Python',
    'not_found': 'names a variable that is not assigned there',
    'ambiguous': 'names a variable assigned more than once there, leaving open which value the agent holds',
    'not_literal': 'is built by code at run time rather than written out word for word',
    'not_articles': 'is not a set of text articles (a key -> article dict, or a list of articles)',
    'too_small': 'holds one article, which is too small to plant questions from',
    'duplicate_name': 'has the same name as another knowledge base on the map',
}


class Refused(Exception):
    def __init__(self, reason: str, detail: str = '') -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Entry:
    name: str
    tool: Optional[str]
    source: str
    file: str = ''
    articles: list[tuple[str, str]] = field(default_factory=list)
    lines: str = ''
    data: bytes = b''
    notes: list[str] = field(default_factory=list)
    refused: Optional[Refused] = None


# --- reading the literal ---------------------------------------------------------------------------


def slug(name: str) -> str:
    return re.sub(r'[\W_]+', '-', name.lower()).strip('-') or 'knowledge'


def _scope_statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement of one scope, through its compound statements but never into a nested def or class."""
    out: list[ast.stmt] = []
    for node in body:
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for name in ('body', 'orelse', 'finalbody'):
            inner = getattr(node, name, None)
            if isinstance(inner, list):
                out += _scope_statements(inner)
        for handler in getattr(node, 'handlers', None) or []:
            out += _scope_statements(handler.body)
        for case in getattr(node, 'cases', None) or []:
            out += _scope_statements(case.body)
    return out


def _named(target: ast.expr, name: str) -> bool:
    return isinstance(target, ast.Name) and target.id == name


def find_value(tree: ast.Module, symbol: str) -> ast.expr:
    """The ONE expression assigned to `symbol` (`func.var`, `Class.method.var`, `VAR`) in its scope."""
    body: list[ast.stmt] = tree.body
    *scopes, name = symbol.split('.')
    for part in scopes:
        defs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part]
        if len(defs) != 1:
            raise Refused('not_found' if not defs else 'ambiguous', f'{len(defs)} definitions of {part}')
        body = defs[0].body
    values: list[ast.expr] = []
    for node in _scope_statements(body):
        if isinstance(node, ast.Assign) and any(_named(t, name) for t in node.targets):
            values.append(node.value)
        elif isinstance(node, ast.AnnAssign) and _named(node.target, name) and node.value is not None:
            values.append(node.value)
        elif isinstance(node, ast.AugAssign) and _named(node.target, name):
            raise Refused('not_literal', f'{name} is extended at line {node.lineno}')
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript) and _named(t.value, name) for t in node.targets):
            raise Refused('not_literal', f'an item of {name} is assigned at line {node.lineno}')
    if len(values) != 1:
        raise Refused('not_found' if not values else 'ambiguous', f'{len(values)} assignments of {name}')
    return values[0]


def articles_of(node: ast.expr) -> list[tuple[str, str]]:
    """`[(heading, article)]` from the literal, in source order; refuses a value that is not plain text articles."""
    fstrings = [n.lineno for n in ast.walk(node) if isinstance(n, ast.JoinedStr)]
    if fstrings:
        raise Refused('not_literal', f'an f-string at line {min(fstrings)}')  # its own line, not the literal's first
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        raise Refused('not_literal', f'at line {node.lineno}') from None
    if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        pairs = [(str(i), v) for i, v in enumerate(value, 1)]
    else:
        raise Refused('not_articles', 'one block of text' if isinstance(value, str) else 'values that are not all text')
    if any(not k.strip() or '\n' in k or '\r' in k for k, _ in pairs):
        raise Refused('not_articles', 'a key that cannot be a heading (blank, or more than one line)')
    pairs = [(k, v) for k, v in pairs if v.strip()]
    if len(pairs) < 2:
        raise Refused('too_small', f'{len(pairs)} article(s) with text')
    return pairs


_FENCE = re.compile(r'\s*(```|~~~)')
_HEADING = re.compile(r'\s*#{1,6}\s')


def _shape(text: str) -> tuple[bool, Optional[str]]:
    """Does the article hold a line the section chunker reads as a heading, and which fence does it leave open?"""
    fence, heading = None, False
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker and (fence is None or marker.group(1) == fence):
            fence = marker.group(1) if fence is None else None
        elif fence is None and _HEADING.match(line):
            heading = True
    return heading, fence


def render(name: str, articles: list[tuple[str, str]]) -> tuple[bytes, list[str]]:
    """One document — the base's `#` title, then each article under its own `##` key, its text unchanged — and a
    warning per article whose text the chunker would split or let swallow the articles after it."""
    parts, notes = [f'# {name}\n'], []
    for key, text in articles:
        heading, fence = _shape(text)
        parts.append(f'\n## {key}\n\n{text}\n' + (f'{fence}\n' if fence else ''))
        if heading:
            notes.append(f'warning: the article "{key}" holds its own headings, so it becomes several smaller sections of the test knowledge base')
        if fence:
            notes.append(f'warning: the article "{key}" leaves a code block open; it is closed right after the article so the articles after '
                         'it keep their own sections (its text is unchanged)')
    return ''.join(parts).encode('utf-8'), notes


def _agent_root(workdir: Path) -> Path:
    """The agent module: the folder holding `plurai_sim` (a named env lives under `plurai_sim/envs/<name>`)."""
    return next((p.parent for p in (workdir, *workdir.parents) if p.name == 'plurai_sim'), workdir.parent)


def _resolve(workdir: Path, rel: str) -> Path:
    """The code file, against the agent module first, then the repo root (`bench_manifest.resolve_source`)."""
    candidate = _agent_root(workdir) / rel
    if candidate.exists():
        return candidate
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bench_manifest  # noqa: PLC0415 - the one path rule, loaded only when the agent module does not hold the file

    return bench_manifest.resolve_source(_agent_root(workdir) / 'plurai_sim', rel)


def read_entry(workdir: Path, raw: dict[str, Any]) -> Entry:
    entry = Entry(name=str(raw.get('name') or '').strip(), tool=raw.get('tool'), source=str(raw.get('source') or '').strip())
    try:
        rel, sep, symbol = entry.source.partition('::')
        rel = rel.replace('\\', '/')  # a map written on Windows
        if not sep or not rel or not _SYMBOL.fullmatch(symbol) or rel.startswith(('/', '~')) or ':' in rel:
            raise Refused('bad_source')
        entry.file = f'{slug(entry.name)}.md'
        if PurePosixPath(rel).suffix.lower() != '.py':
            raise Refused('unsupported_language', PurePosixPath(rel).suffix or 'no suffix')
        path = _resolve(workdir, rel)
        if not path.is_file():
            raise Refused('missing')
        try:
            tree = ast.parse(path.read_bytes(), filename=rel)
        except (SyntaxError, ValueError) as error:
            raise Refused('unparseable', f'at line {getattr(error, "lineno", "?")}') from None
        node = find_value(tree, symbol)
        entry.articles = articles_of(node)
        entry.lines = f'{rel}:{node.lineno}-{node.end_lineno}'
        try:
            entry.data, entry.notes = render(entry.name, entry.articles)
        except UnicodeEncodeError:
            raise Refused('not_articles', 'text that is not valid Unicode') from None
    except Refused as refusal:
        entry.refused = refusal
    return entry


# --- the map, the manifest, the stamps -------------------------------------------------------------


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path} is not a JSON object')
    return value


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def source_path(workdir: Path) -> str:
    """The folder as a corpus source: relative to the agent module, as `grounding.sources` are written."""
    return (workdir / FOLDER).relative_to(_agent_root(workdir)).as_posix()


def record_source(workdir: Path, present: bool) -> None:
    """The folder in `grounding.sources` and the manifest's `sources.corpus_roots`, or out of both."""
    source = source_path(workdir)

    def edit(listed: list[str]) -> list[str]:
        kept = [s for s in listed if s != source]
        return [*kept, source] if present else kept

    topology_path = workdir / TOPOLOGY
    topology = _read_json(topology_path)
    grounding = (topology or {}).get('grounding')
    if isinstance(grounding, dict):
        before = list(grounding.get('sources') or [])
        sources = edit([str(s) for s in before])
        if sources != before:
            if sources:
                grounding['sources'] = sources
            else:
                grounding.pop('sources', None)
            _write_json(topology_path, topology)
    manifest = _read_json(workdir / MANIFEST)
    if manifest is not None and isinstance(manifest.get('sources'), dict):
        before = list(manifest['sources'].get('corpus_roots') or [])
        roots = edit([str(s) for s in before])
        if roots != before:
            manifest['sources']['corpus_roots'] = roots
            _write_json(workdir / MANIFEST, manifest)


def stamps(workdir: Path) -> dict[str, str]:
    path = workdir / CORPUS_SCOPE
    lines = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    return dict(line.removeprefix('inline_sha256:').split()[:2] for line in lines if line.startswith('inline_sha256:') and len(line.split()) >= 3)


def stamp_corpus_scope(workdir: Path, kept: list[Entry], sole_source: bool) -> None:
    """Replace this script's lines in `.corpus_scope`, keeping every other; `declared_units` only when the folder is the whole corpus."""
    path = workdir / CORPUS_SCOPE
    before = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    # A `declared_units` line right after the stamps is this script's own: it goes with them.
    ours = {i for i, line in enumerate(before) if line.startswith(DECLARED) and i and before[i - 1].startswith('inline_sha256:')}
    lines = [line for i, line in enumerate(before)
             if not line.startswith(STAMP_KEYS) and i not in ours and not (sole_source and line.startswith(DECLARED))]
    own: list[str] = []
    if kept:
        described = '; '.join(f'{e.name} ({f"{len(e.articles)} articles" if e.articles else "the earlier copy"}, {e.source})' for e in kept)
        own = [f'inline: {source_path(workdir)} — knowledge written in the agent\'s code, copied word for word: {described}',
               *(f'inline_sha256: {e.file} {hashlib.sha256(e.data).hexdigest()}' for e in kept)]
        lines += own
        if sole_source:
            declared = [line for line in before if line.startswith(DECLARED)]
            if [line for line in before if line.startswith(STAMP_KEYS)] == own and declared and declared[-1] == f'{DECLARED} {len(kept)}':
                return  # the same stamps and count: a build's own lines after them stay where they are
            lines.append(f'{DECLARED} {len(kept)}')
    if lines == before:
        return
    if lines:
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    elif path.is_file():
        path.unlink()


def changed_articles(old: str, articles: list[tuple[str, str]]) -> list[str]:
    """Which articles differ from the copy on disk, by key: changed, added, removed."""
    keys = {k for k, _ in articles}
    out = [f'changed: {k}' for k, v in articles if f'\n## {k}\n' in old and f'\n## {k}\n\n{v}\n' not in old]
    out += [f'added: {k}' for k, _ in articles if f'\n## {k}\n' not in old]
    out += [f'removed: {m}' for m in re.findall(r'(?m)^## (.+)$', old) if m not in keys and not any(f'## {m}' in v for _, v in articles)]
    return out


def unstamped_sources(workdir: Path, grounding: dict, entries: list[Entry]) -> list[str]:
    """Warnings: a corpus source inside the workdir that no exporter stamped, and a declared code file listed as a source."""
    root = _agent_root(workdir)
    scope = (workdir / CORPUS_SCOPE).read_text(encoding='utf-8') if (workdir / CORPUS_SCOPE).is_file() else ''
    stamped = {source_path(workdir)} | ({(workdir / SNAPSHOT_FOLDER).relative_to(root).as_posix()} if SNAPSHOT_STAMP in scope else set())
    code = {e.source.partition('::')[0].replace('\\', '/').removeprefix('./') for e in entries}
    out = []
    for raw in grounding.get('sources') or []:
        rel = str(raw).replace('\\', '/').rstrip('/')
        inside = (root / rel).resolve()
        if rel.removeprefix('./') in code:
            out += [f'warning: `{rel}` is your agent\'s code, not a document: the knowledge in it is copied into the test knowledge '
                    'base, so the code itself is never read as one',
                    f'fix: remove `{rel}` from grounding.sources']
        elif rel not in stamped and (inside == workdir.resolve() or workdir.resolve() in inside.parents):
            out += [f'warning: `{rel}` is a knowledge file in the test folder that was written by hand, not copied from your agent, so '
                    'nothing checks it against what your agent answers from',
                    'fix: never write a knowledge file by hand: declare the literal in grounding.inline, or point grounding.sources at the '
                    'agent\'s own documents']
    return out


# --- one pass --------------------------------------------------------------------------------------


def run(workdir: Path, when: str) -> tuple[list[str], str]:
    """The report lines and the outcome word; writes only what `when` allows."""
    topology = _read_json(workdir / TOPOLOGY)
    grounding = (topology or {}).get('grounding')
    grounding = grounding if isinstance(grounding, dict) else {}
    declared = grounding.get('inline') if isinstance(grounding.get('inline'), list) else []
    entries = [read_entry(workdir, raw) for raw in declared if isinstance(raw, dict)]
    seen: set[str] = set()
    for entry in entries:
        if entry.refused is None and entry.file in seen:
            entry.refused = Refused('duplicate_name')
        seen.add(entry.file)
    folder = workdir / FOLDER
    stamped = stamps(workdir)
    lines: list[str] = []
    outcomes: list[str] = []
    kept: list[Entry] = []
    for entry in entries:
        old_path = folder / entry.file
        old = old_path.read_bytes() if entry.file and old_path.is_file() else None
        if entry.refused is not None:
            why = REFUSALS[entry.refused.reason]
            detail = f' ({entry.refused.detail})' if entry.refused.detail else ''
            has_copy = entry.file in stamped and old is not None
            if when != 'scan' and has_copy:
                entry.data = old or b''
                kept.append(entry)
                outcomes.append('kept')
                lines.append(f'warning: the knowledge at `{entry.source}` {why}{detail}; the earlier copy is kept, so its answers are checked '
                             'against the articles as they were when it was copied — re-run /simulation-scan-agent')
            else:
                outcomes.append('refused')
                lines.append(f'warning: the knowledge at `{entry.source}` {why}{detail}, so it was not copied and answers from it are not '
                             'checked against it')
            continue
        sha = hashlib.sha256(entry.data).hexdigest()
        if stamped.get(entry.file) == sha and old == entry.data:
            kept.append(entry)
            outcomes.append('unchanged')
            lines.append(f'{entry.name}: unchanged — {len(entry.articles)} articles from {entry.lines}')
            continue
        diff = changed_articles(old.decode('utf-8', errors='replace'), entry.articles) if old is not None and entry.file in stamped else []
        what = '; '.join(diff) or 'not copied before'
        if when == 'run':
            outcomes.append('stale')
            lines.append(f'warning: your agent\'s knowledge `{entry.name}` changed since this suite was built ({what}); this run checks answers '
                         'against the earlier copy — re-run /simulation-generate-suite to rebuild the test knowledge base from the code')
            continue
        folder.mkdir(parents=True, exist_ok=True)
        old_path.write_bytes(entry.data)
        kept.append(entry)
        outcomes.append('written')
        lines.append(f'{entry.name}: copied {len(entry.articles)} articles from {entry.lines} -> {source_path(workdir)}/{entry.file} ({what})')
        lines += entry.notes  # said when the copy is written, not on every pass
    if when != 'run':
        live = {e.file for e in kept}
        if when == 'scan':
            for stale in sorted(folder.glob('*.md')) if folder.is_dir() else ():
                if stale.name not in live:
                    stale.unlink()
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        present = bool(kept) or (when != 'scan' and folder.is_dir() and any(folder.glob('*.md')))
        if when == 'scan' or present:
            record_source(workdir, present)
        grounding = ((_read_json(workdir / TOPOLOGY) or {}).get('grounding')) or {}
        sole = [str(s) for s in grounding.get('sources') or []] == [source_path(workdir)]
        if when == 'scan' or kept:
            stamp_corpus_scope(workdir, kept, sole)
    lines += unstamped_sources(workdir, grounding if isinstance(grounding, dict) else {}, entries)
    for word in ('stale', 'refused', 'kept', 'written', 'unchanged'):
        if word in outcomes:
            reasons = sorted({e.refused.reason for e in entries if e.refused is not None})
            return lines, f'{word} reason={",".join(reasons)}' if word == 'refused' else word
    return lines, 'none'


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', required=True, type=Path, help='the plurai_sim workdir ($WORKDIR)')
    parser.add_argument('--when', required=True, choices=WHEN, help='scan (Step 3c), suite (the grounding gate) or run (run start)')
    args = parser.parse_args(argv)
    try:
        lines, outcome = run(args.workdir.resolve(), args.when)
    except (OSError, ValueError) as error:
        print(f'extract_inline_knowledge: {error}', file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(f'KNOWLEDGE_INLINE={outcome}')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
