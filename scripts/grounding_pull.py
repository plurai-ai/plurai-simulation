#!/usr/bin/env python3
"""Pull read-only DISPLAY COPIES of what the backend built to ground this agent, and say it plainly.

    grounding_pull.py knowledge <workdir>                 # every knowledge base → artifacts/knowledge/, knowledge_summary.json, knowledge.md
    grounding_pull.py traces    <workdir> [--job-id ID]   # the published trace brief → artifacts/trace_brief/, trace_brief.json

THESE ARE DISPLAY COPIES, NOT GROUND TRUTH. The backend stays the single source of truth for every
corpus and every trace brief: generation, grading and edit sessions read it there, never here. The
files this script writes exist so the customer can SEE what their agent is grounded in (the overview
page and `artifacts/knowledge.md` read them). No step may read them back as ground truth — a copy
can be one edit session behind. Each pulled set carries a `.source.json` saying so, with the id it
came from, when, and a sha256 per file as written.

knowledge — reads `<workdir>/.knowledge_bases` (a JSON list of `{kb_id, label, corpus_ids}`, written
by /build-rag-corpus); a legacy lone `<workdir>/.corpus_id` is one unnamed knowledge base. For each
corpus it GETs `knowledge_graph.json`, `testset.json`, `corpus_meta.json` and `overview.md` (404 on a
corpus built before overviews existed — fine) from `/artifacts/<corpus_id>/files/<name>` into
`artifacts/knowledge/<kb_id>/<corpus_id>/`. A corpus is written all-or-nothing: a failed fetch
leaves its previous copy in place and records the error. Spend keys are dropped from
`corpus_meta.json` before it is written; spend never reaches the customer's folder.

traces — reads the bench workspace id (`<workdir>/.workspace_id`, `<bench>.<alias>` under a named
env; never minted here) and GETs each brief file from `/trace-briefs/<workspace_id>/files/<name>`
into `artifacts/trace_brief/` (staged, then swapped in whole). 404 = that file is absent; 503 =
the backend has no trace store. `--job-id` reads the analysis job's `degraded` / `blind_topics`.

Server + bearer are resolved exactly as `report_pieces.py` resolves them (imported, not copied), and
the wrong-backend guard runs before any GET. The bearer comes from the environment only and is never
printed. Exit: 0 pulled, or nothing to do · 1 a fetch failed (previous copies kept) · 2 misconfigured
or wrong backend. One JSON line on stdout; logs on stderr; never raises.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
import report_pieces  # noqa: E402 - sibling script: server resolution, atomic writes, the clock

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
GET_TIMEOUT = 120.0

KNOWLEDGE_SCHEMA = 'plurai-knowledge-summary/1'
BRIEF_SCHEMA = 'plurai-trace-brief-summary/1'
SOURCE_SCHEMA = 'plurai-display-copy/1'
SOURCE = '.source.json'
DISPLAY_NOTE = ('Display copy of a backend artifact, pulled for the customer to read. The backend is the single '
                'source of truth; never read this copy back as ground truth.')

CORPUS_FILES = ('knowledge_graph.json', 'testset.json', 'corpus_meta.json')
OVERVIEW = 'overview.md'
LEGACY_KB = 'default'

#: The brief files the backend may publish at the brief root: current spellings, then the legacy
#: ones the packaged demo brief carries. Each is tried; a 404 means that file is not in this brief.
BRIEF_FILES = (
    'README.md', 'capabilities.md', 'tools.md', 'world_state.md', 'anomalies_noise.md', 'anomalies.md',
    'user_speaking_style.md', 'user_style.md', 'failures_evals.md', 'scenarios.md', 'extras.md',
    'structural_facts.md', 'knowledge_access.md', 'governing_context.md', 'relationships.md', 'evidence.json',
)
SECTION_TITLES = {
    'capabilities.md': 'Capabilities', 'tools.md': 'Capabilities', 'world_state.md': 'World state',
    'anomalies_noise.md': 'Anomalies & noise', 'anomalies.md': 'Anomalies & noise',
    'user_speaking_style.md': 'How users speak', 'user_style.md': 'How users speak',
    'failures_evals.md': 'Failures & evals', 'scenarios.md': 'Scenarios', 'extras.md': 'Extras',
    'structural_facts.md': 'Structural facts', 'knowledge_access.md': 'Knowledge access',
    'governing_context.md': 'Governing context', 'relationships.md': 'Relationships',
}

#: Link types in the customer's words; anything else reads as its own name with spaces.
LINK_WORDS = {
    'next_section': 'reading order',
    'parent_section': 'section nesting',
    'topic_overlap': 'shared topics',
    'elaboration': 'elaborates',
    'workflow_prerequisite': 'prerequisite steps',
}

#: Backend spend is internal telemetry: a key naming it is dropped from every copy and summary.
_SPEND_KEY = re.compile(r'cost|spend|usd|price|billing', re.IGNORECASE)
_DOC_HEADER = re.compile(r'\[Document:\s*([^|\]]+?)\s*(?:\|[^\]]*)?\]')
_PLAIN_WS = re.compile(r'[A-Za-z0-9_-]{1,64}')

MAX_TOPICS = 8
MAX_DOCS_LISTED = 6
MAX_QUESTIONS = 5
MAX_SOURCES = 3
ANSWER_LINE = 160
ANSWER_KEEP = 600


class _Absent(Exception):
    """The backend answered 404: the file is not there."""


class _Disabled(Exception):
    """The backend answered 503: the store behind the route is not configured."""


def _log(message: str) -> None:
    print(f'grounding_pull: {message}', file=sys.stderr)


# ── the backend ───────────────────────────────────────────────────────────────
def _split_refusal(server: str, workdir: Path) -> Optional[str]:
    """The wrong-backend refusal (`backend_identity_marker.split`); a missing guard module is no refusal."""
    try:
        import backend_identity_marker  # noqa: PLC0415 - a sibling script, loaded by path
    except Exception:  # noqa: BLE001 - the guard is advisory when its module is missing
        return None
    return backend_identity_marker.split(server, caller='grounding_pull', workdir=workdir)


def _get(url: str) -> bytes:
    """One GET; the bearer comes from the resolver, goes only to the session's backend and is never logged."""
    guard = report_pieces.url_guard()
    token = guard.bearer(url)
    if token and not guard.is_backend_url(url):
        raise urllib.error.URLError(guard.refused_message('the url'))
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    try:
        with _no_redirect.OPENER.open(request, timeout=GET_TIMEOUT) as response:  # noqa: S310 - backend-controlled URL
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _Absent() from None
        if exc.code == 503:
            raise _Disabled() from None
        raise


def _describe(exc: BaseException) -> str:
    """A fetch failure in words that carry no URL and no header."""
    if isinstance(exc, urllib.error.HTTPError):
        return f'HTTP {exc.code}'
    if isinstance(exc, _Disabled):
        return 'HTTP 503 (store disabled)'
    if isinstance(exc, urllib.error.URLError):
        return f'unreachable ({type(exc.reason).__name__})'
    return type(exc).__name__


def _preflight(workdir: Path) -> tuple[Optional[str], Optional[str]]:
    """(server, problem): the backend to call, or why none may be called."""
    server = report_pieces.server_url()
    if not server:
        return None, report_pieces.url_guard().configuration_problem('grounding_pull') + ' Nothing was requested.'
    refusal = _split_refusal(server, workdir)
    if refusal:
        _log(refusal)
        return None, 'wrong backend: this session is bound to another host; nothing requested'
    return server, None


def _seg(value: str) -> str:
    return urllib.parse.quote(value, safe='')


# ── small readers ─────────────────────────────────────────────────────────────
def _drop_spend(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_spend(v) for k, v in value.items() if not _SPEND_KEY.search(str(k))}
    if isinstance(value, list):
        return [_drop_spend(v) for v in value]
    return value


def _squash(text: Any) -> str:
    return ' '.join(str(text or '').split())


def _text_of(value: Any) -> str:
    """A testset field as one string: a plain string, or the first message of a multi-turn list."""
    if isinstance(value, str):
        return _squash(value)
    if isinstance(value, dict):
        return _squash(value.get('content'))
    if isinstance(value, list):
        for item in value:
            text = _text_of(item)
            if text:
                return text
    return ''


def _one_line(text: str, limit: int = ANSWER_LINE) -> str:
    """`text` shortened to one readable line: whole when it fits, else its first sentence, else a cut."""
    text = _squash(text)
    if len(text) <= limit:
        return text
    # A sentence end is not a list number (`1.`) and leaves a sentence worth reading on its own.
    sentence = re.match(r'(.{40,}?[^\d\s][.!?])(?=\s)', text)
    if sentence and len(sentence.group(1)) <= limit:
        return sentence.group(1) + ' …'
    cut = text[:limit].rsplit(' ', 1)[0]
    return cut.rstrip(',;:') + ' …'


def _first_paragraph(markdown: str) -> Optional[str]:
    """The first prose paragraph of an overview: no heading, rule, table, list, quote or fence."""
    for block in re.split(r'\n\s*\n', markdown or ''):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        head = lines[0]
        if head.startswith(('#', '---', '***', '|', '```', '<!--', '>', '* ', '- ', '+ ')) or re.match(r'\d+[.)]\s', head):
            continue
        text = ' '.join(lines)
        return _one_line(re.sub(r'^This corpus\b', 'This knowledge base', text), ANSWER_KEEP)
    return None


def _stem(name: str) -> str:
    base = name.rsplit('/', 1)[-1]
    base = base.rsplit('.', 1)[0] if '.' in base else base
    return ' '.join(re.split(r'[^a-z0-9]+', base.lower())).strip()


def _plural(count: int, word: str, many: Optional[str] = None) -> str:
    return f'{count} {word if count == 1 else (many or word + "s")}'


# ── which knowledge bases ─────────────────────────────────────────────────────
def _slug(value: Any, fallback: str) -> str:
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '')).strip('-.')
    return slug if report_pieces._safe_id(slug) else fallback


def read_knowledge_bases(workdir: Path) -> tuple[list, Optional[str]]:
    """(bases, problem). Each base: kb_id (one path segment), label, corpus_ids, knowledge_planning."""
    manifest = workdir / '.knowledge_bases'
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            return [], f'.knowledge_bases cannot be read ({type(exc).__name__})'
        if not isinstance(data, list):
            return [], '.knowledge_bases is not a JSON list'
        bases: list = []
        taken: set = set()
        for k, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                continue
            kb_id = _slug(entry.get('kb_id') or entry.get('tool'), f'kb-{k}')
            base_id, n = kb_id, 2
            while kb_id in taken:
                kb_id, n = f'{base_id}-{n}', n + 1
            taken.add(kb_id)
            raw_ids = entry.get('corpus_ids') if isinstance(entry.get('corpus_ids'), list) else []
            ids = [str(c).strip() for c in raw_ids if report_pieces._safe_id(c)]
            bad = [str(c) for c in raw_ids if not report_pieces._safe_id(c)]
            label = entry.get('label')
            bases.append({
                'kb_id': kb_id,
                'label': _squash(label) if isinstance(label, str) and label.strip() else None,
                'corpus_ids': list(dict.fromkeys(ids)),
                'knowledge_planning': entry.get('knowledge_planning') if isinstance(entry.get('knowledge_planning'), str) else None,
                'bad_ids': bad,
            })
        return bases, None
    legacy = workdir / '.corpus_id'
    if legacy.is_file():
        try:
            corpus_id = legacy.read_text(encoding='utf-8').strip()
        except OSError as exc:
            return [], f'.corpus_id cannot be read ({type(exc).__name__})'
        if not report_pieces._safe_id(corpus_id):
            return [], '.corpus_id does not hold a plain corpus id'
        return [{'kb_id': LEGACY_KB, 'label': None, 'corpus_ids': [corpus_id], 'knowledge_planning': None, 'bad_ids': []}], None
    return [], None


# ── the pull ──────────────────────────────────────────────────────────────────
def _write_source(dest: Path, key: str, ident: str, extra: dict) -> None:
    files = {p.name: report_pieces._sha256(p) for p in sorted(dest.iterdir()) if p.is_file() and p.name != SOURCE}
    record = {'schema': SOURCE_SCHEMA, 'note': DISPLAY_NOTE, key: ident, 'pulled_at': report_pieces._now(), **extra, 'files': files}
    report_pieces._write_atomic(dest / SOURCE, json.dumps(record, indent=2, sort_keys=True) + '\n')


def pull_corpus(server: str, dest: Path, corpus_id: str, kb_id: str) -> Optional[str]:
    """Fetch one corpus's display files into `dest`, all or nothing; the error, or None."""
    got: dict = {}
    for name in CORPUS_FILES + (OVERVIEW,):
        try:
            body = _get(f'{server}/artifacts/{_seg(corpus_id)}/files/{name}')
        except _Absent:
            if name == OVERVIEW:
                got[name] = None
                continue
            return f'{name}: not found on the backend'
        except Exception as exc:  # noqa: BLE001 - any failure keeps the previous copy
            return f'{name}: {_describe(exc)}'
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            return f'{name}: not UTF-8 text'
        if name in CORPUS_FILES:
            try:
                parsed = json.loads(text)
            except ValueError:
                return f'{name}: not valid JSON'
            if name == 'corpus_meta.json':
                text = json.dumps(_drop_spend(parsed), indent=2, ensure_ascii=False) + '\n'
        got[name] = text
    for name, text in got.items():
        if text is None:
            (dest / name).unlink(missing_ok=True)
        else:
            report_pieces._write_atomic(dest / name, text)
    _write_source(dest, 'corpus_id', corpus_id, {'kb_id': kb_id})
    return None


def _prune(root: Path, keep: dict) -> None:
    """Drop copies of knowledge bases / corpora the manifest does not name (one current copy)."""
    if not root.is_dir():
        return
    for kb_dir in root.iterdir():
        if not kb_dir.is_dir() or kb_dir.name.startswith('.'):
            continue
        if kb_dir.name not in keep:
            shutil.rmtree(kb_dir, ignore_errors=True)
            continue
        for corpus_dir in kb_dir.iterdir():
            if corpus_dir.is_dir() and corpus_dir.name not in keep[kb_dir.name]:
                shutil.rmtree(corpus_dir, ignore_errors=True)


def pull_knowledge(workdir: Path) -> dict:
    workdir = Path(workdir)
    result: dict = {'command': 'knowledge', 'workdir': str(workdir)}
    bases, problem = read_knowledge_bases(workdir)
    if problem:
        _log(problem)
        return {**result, 'exit': EXIT_USAGE, 'note': problem}
    if not bases:
        return {**result, 'exit': EXIT_OK, 'note': 'no knowledge base recorded; nothing to pull'}
    server, problem = _preflight(workdir)
    if problem:
        _log(problem)
        return {**result, 'exit': EXIT_USAGE, 'note': problem}

    art = report_pieces._art(workdir)
    root = art / 'knowledge'
    errors: dict = {}
    pulled = 0
    for base in bases:
        kb_errors = [{'corpus_id': bad, 'error': 'not a plain corpus id; skipped'} for bad in base['bad_ids']]
        for corpus_id in base['corpus_ids']:
            error = pull_corpus(server, root / base['kb_id'] / corpus_id, corpus_id, base['kb_id'])
            if error:
                _log(f'{base["kb_id"]}/{corpus_id}: {error}; the previous copy (if any) is kept')
                kb_errors.append({'corpus_id': corpus_id, 'error': error})
            else:
                pulled += 1
        if kb_errors:
            errors[base['kb_id']] = kb_errors

    summary = summarise_knowledge(workdir, bases, errors)
    report_pieces._write_atomic(art / 'knowledge_summary.json', json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    report_pieces._write_atomic(art / 'knowledge.md', render_knowledge_md(summary, out_of_reach_lines(workdir)))
    failed = any(e['error'] != 'not a plain corpus id; skipped' for kb in errors.values() for e in kb)
    if not failed:
        _prune(root, {b['kb_id']: set(b['corpus_ids']) for b in bases})
    return {
        **result,
        'exit': EXIT_FAILED if failed else EXIT_OK,
        'knowledge_bases': len(bases),
        'corpora_pulled': pulled,
        'totals': summary['totals'],
        'totals_line': totals_line(summary['totals']),
        'errors': [{'kb_id': kb, **e} for kb, items in errors.items() for e in items],
        'knowledge_md': 'artifacts/knowledge.md',
        'summary': 'artifacts/knowledge_summary.json',
    }


# ── the summary (read from the copies on disk, so a failed pull still shows the last good one) ──
def _read(path: Path) -> Any:
    return report_pieces._read_json(path)


def _samples_of(testset: Any) -> list:
    if isinstance(testset, dict):
        testset = testset.get('samples')
    return [s for s in testset if isinstance(s, dict)] if isinstance(testset, list) else []


def _sources(sample: dict, id_to_doc: dict, docs_by_stem: dict) -> list:
    found: list = []
    for ref in sample.get('reference_context_refs') or []:
        name = ref.get('doc_name') if isinstance(ref, dict) else id_to_doc.get(str(ref))
        if isinstance(name, str) and name and name not in found:
            found.append(name)
    if not found:
        for context in sample.get('reference_contexts') or []:
            match = _DOC_HEADER.search(context) if isinstance(context, str) else None
            if match:
                name = docs_by_stem.get(_stem(match.group(1)), match.group(1).strip())
                if name not in found:
                    found.append(name)
    return found[:MAX_SOURCES]


def _question(sample: dict, id_to_doc: dict, docs_by_stem: dict) -> Optional[dict]:
    question = _text_of(sample.get('user_input'))
    if not question:
        return None
    answer = _squash(sample.get('reference'))
    return {
        'question': question,
        'answer': answer if len(answer) <= ANSWER_KEEP else _one_line(answer, ANSWER_KEEP),
        'sources': _sources(sample, id_to_doc, docs_by_stem),
    }


def summarise_kb(kb_dir: Path, base: dict, errors: list) -> dict:
    docs: dict = {}
    topics: dict = {}
    link_counts: dict = {}
    warnings: list = []
    passages = links = questions = 0
    excerpt: Optional[str] = None
    overview_paths: list = []
    pulled_at: list = []
    missing: list = []
    per_corpus_questions: list = []
    metas: list = []
    for corpus_id in base['corpus_ids']:
        here = kb_dir / corpus_id
        source = _read(here / SOURCE)
        if not isinstance(source, dict):
            missing.append(corpus_id)
            continue
        if isinstance(source.get('pulled_at'), str):
            pulled_at.append(source['pulled_at'])
        graph = _read(here / 'knowledge_graph.json')
        graph = graph if isinstance(graph, dict) else {}
        nodes = [n for n in graph.get('nodes') or [] if isinstance(n, dict)]
        rels = [r for r in graph.get('relationships') or [] if isinstance(r, dict)]
        passages += len(nodes)
        links += len(rels)
        id_to_doc: dict = {}
        for node in nodes:
            props = node.get('properties') if isinstance(node.get('properties'), dict) else {}
            doc = props.get('doc_name')
            if isinstance(doc, str) and doc:
                docs[doc] = docs.get(doc, 0) + 1
                id_to_doc[str(node.get('id'))] = doc
            labels = [props.get('subgraph_label')] + list(props.get('topic_clusters') or [])
            for label in labels:
                if isinstance(label, str) and label.strip():
                    topics[label.strip()] = topics.get(label.strip(), 0) + 1
        meta = _read(here / 'corpus_meta.json')
        meta = _drop_spend(meta) if isinstance(meta, dict) else {}
        metas.append(meta)
        dist = meta.get('edge_type_distribution')
        if not isinstance(dist, dict):
            dist = {}
            for rel in rels:
                kind = str(rel.get('type') or 'related')
                dist[kind] = dist.get(kind, 0) + 1
        for kind, count in dist.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                link_counts[str(kind)] = link_counts.get(str(kind), 0) + count
        for warning in meta.get('build_warnings') or []:
            if isinstance(warning, str) and warning.strip() and warning not in warnings:
                warnings.append(warning)
        samples = _samples_of(_read(here / 'testset.json'))
        questions += len(samples)
        docs_by_stem = {_stem(d): d for d in id_to_doc.values()}
        per_corpus_questions.append([q for q in (_question(s, id_to_doc, docs_by_stem) for s in samples) if q])
        overview = here / OVERVIEW
        if overview.is_file():
            overview_paths.append(f'knowledge/{base["kb_id"]}/{corpus_id}/{OVERVIEW}')
            if excerpt is None:
                try:
                    excerpt = _first_paragraph(overview.read_text(encoding='utf-8'))
                except OSError:
                    excerpt = None

    picked: list = []
    seen: set = set()
    depth = 0
    while len(picked) < MAX_QUESTIONS and any(depth < len(qs) for qs in per_corpus_questions):
        for qs in per_corpus_questions:  # round-robin, so every corpus of the base is represented
            if depth < len(qs) and len(picked) < MAX_QUESTIONS and qs[depth]['question'] not in seen:
                seen.add(qs[depth]['question'])
                picked.append(qs[depth])
        depth += 1

    ranked_topics = sorted(topics, key=lambda t: -topics[t])[:MAX_TOPICS]
    typed_warnings = plain_warnings(warnings)
    note, set_aside = set_aside_of(metas, docs) if not missing else (None, None)
    if note:
        typed_warnings.append({'code': 'set_aside', 'message': note})
    return {
        'kb_id': base['kb_id'],
        'label': base['label'],
        'corpus_ids': list(base['corpus_ids']),
        'pulled_at': max(pulled_at) if pulled_at else None,
        'counts': {'documents': len(docs), 'passages': passages, 'links': links, 'questions': questions},
        'documents': list(docs),
        'link_types': [
            {'type': kind, 'plain': LINK_WORDS.get(kind, kind.replace('_', ' ')), 'count': count}
            for kind, count in sorted(link_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        'topics': ranked_topics,
        'overview_excerpt': excerpt,
        # `build_warnings` stays a list of plain strings under /1 (page 0.55.2 reads it as string[] and
        # rejects the whole summary otherwise); the codes ride beside it in the additive `_typed` list.
        'build_warnings': [item['message'] for item in typed_warnings],
        'build_warnings_typed': typed_warnings,
        'build_warnings_raw': warnings,
        'sample_questions': picked,
        'has_overview': bool(overview_paths),
        'overview_paths': overview_paths,
        'knowledge_planning': base.get('knowledge_planning'),
        'set_aside_documents': set_aside,
        'missing_corpora': missing,
        'errors': list(errors),
    }


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def set_aside_of(metas: list, kept_docs: dict) -> tuple[Optional[str], Optional[dict]]:
    """What the build left out of the knowledge graph (`corpus_meta.json`): a plain line, None when nothing was, and the
    `{document: chunks}` map, None unless every corpus's map is complete (it is truncated past 500 documents). A
    document is out ENTIRELY when no kept graph node of the base names it."""
    counts, totals = [m.get('set_aside_chunk_count') for m in metas], [m.get('chunk_count') for m in metas]
    if not metas or not all(_count(n) for n in counts + totals):
        return None, None
    maps = [m.get('set_aside_documents') for m in metas]
    complete = all(isinstance(mp, dict) and all(_count(v) for v in mp.values()) and sum(mp.values()) == n for mp, n in zip(maps, counts))
    by_doc: dict = {}
    for mp in maps if complete else []:
        for doc, n in mp.items():
            by_doc[doc] = by_doc.get(doc, 0) + n
    if not sum(counts):
        return None, by_doc if complete else None
    line = f'{sum(counts)} of {_plural(sum(totals), "passage")} were left out of the knowledge graph'
    line += " (the build's size cap)" if all(m.get('subgraphs_capped') is True for m in metas) else ''
    whole = sorted(d for d in by_doc if d not in kept_docs)
    line += f', {_plural(len(whole), "document")} entirely: {_names(whole, MAX_DOCS_LISTED)}' if whole else ''
    return line + '. No question is drawn from them, so the simulation does not test them.', by_doc if complete else None


#: The registry's verdict on the latest build of a part (`grounding_corpus.py record`), in the customer's terms.
NOT_BUILT_REASONS = {
    'failed': 'its build failed on our side',
    'degenerate': 'its documents hold too little connected text to build a knowledge graph',
}


def out_of_reach_lines(workdir: Path) -> list:
    """`knowledge.md`'s agent-level lines: each source `/build-rag-corpus` Step 1 named out of reach, then each part of
    `corpus_roots.json` that `.knowledge_bases` does not carry, with the registry's verdict on its latest build, else the
    part's own `not_built` sentence, else no cause."""
    art = report_pieces._art(Path(workdir))
    data = _read(art / 'corpus_roots.json')
    data = data if isinstance(data, dict) else {}
    away = data.get('out_of_reach')
    names = [Path(str(root)).name for root in away] if isinstance(away, dict) else []
    lines = [f"- Not in any knowledge base: {name} — none of the agent's tools can return it." for name in names]
    bases, parts = _read(Path(workdir) / '.knowledge_bases'), data.get('parts')
    if not isinstance(bases, list) or not isinstance(parts, dict):
        return lines
    built = {str(b.get('kb_id') or b.get('tool')) for b in bases if isinstance(b, dict)}
    registry = _read(art / 'corpus_parts.json')
    rows = registry.get('parts') if isinstance(registry, dict) and isinstance(registry.get('parts'), list) else []
    latest = {str(row.get('kb_id')): row.get('handled') for row in rows if isinstance(row, dict)}
    for kb_id, part in parts.items():
        if kb_id in built:
            continue
        own = part.get('not_built') if isinstance(part, dict) else None
        reason = NOT_BUILT_REASONS.get(str(latest.get(kb_id))) or (_squash(own).rstrip('.') if isinstance(own, str) else '')
        lines.append(f'- No knowledge base for {kb_id}{": " + reason if reason else ""}, so the simulation does not test what that tool returns.')
    return lines


def summarise_knowledge(workdir: Path, bases: list, errors: dict) -> dict:
    root = report_pieces._art(Path(workdir)) / 'knowledge'
    kbs = [summarise_kb(root / base['kb_id'], base, errors.get(base['kb_id'], [])) for base in bases]
    totals = {'knowledge_bases': len(kbs)}
    for key in ('documents', 'passages', 'links', 'questions'):
        totals[key] = sum(kb['counts'][key] for kb in kbs)
    return {'schema': KNOWLEDGE_SCHEMA, 'note': DISPLAY_NOTE, 'pulled_at': report_pieces._now(), 'totals': totals, 'knowledge_bases': kbs}


# ── knowledge.md ──────────────────────────────────────────────────────────────
def totals_line(totals: dict) -> str:
    return ' · '.join((
        _plural(totals.get('knowledge_bases', 0), 'knowledge base'),
        _plural(totals.get('documents', 0), 'document'),
        _plural(totals.get('passages', 0), 'passage'),
        _plural(totals.get('questions', 0), 'question') + ' prepared',
    ))


#: A label longer than this is a description, not a name: the heading takes its first clause.
HEADING_MAX = 60
_CLAUSE_END = re.compile(r'\s+[—–-]\s+|\s*\(|,\s|;\s')


def _heading(kb: dict) -> str:
    """The section's name: the label, cut at its first clause when the label is a whole description."""
    label = str(kb.get('label') or '').strip()
    if not label:
        return 'Knowledge base' if kb['kb_id'] == LEGACY_KB else kb['kb_id']
    if len(label) <= HEADING_MAX:
        return label
    cut = _CLAUSE_END.search(label)
    head = label[:cut.start()].strip() if cut and cut.start() >= 12 else label[:HEADING_MAX].rsplit(' ', 1)[0] + '…'
    return head


def _names(items: list, cap: int) -> str:
    shown = list(items[:cap])
    rest = len(items) - len(shown)
    if rest > 0:
        return ', '.join(shown) + f' and {rest} more'
    if len(shown) > 1:
        return ', '.join(shown[:-1]) + ' and ' + shown[-1]
    return shown[0] if shown else ''


def _render_kb(kb: dict) -> list:
    lines = [f'## {_heading(kb)}', '']
    have_copy = len(kb['missing_corpora']) < len(kb['corpus_ids'])
    if not have_copy:
        return lines + ['_Not available yet: this knowledge base could not be fetched from the backend._', '']
    if kb['errors']:
        stamp = (kb.get('pulled_at') or '')[:10]
        lines += [f'_Showing the copy pulled {stamp}; the latest could not be fetched._' if stamp else
                  '_Showing an earlier copy; the latest could not be fetched._', '']
    covers = kb.get('overview_excerpt')
    if not covers and kb['topics']:
        covers = 'Covers ' + _names(kb['topics'], MAX_TOPICS) + '.'
    if covers:
        lines += [covers, '']
    counts = kb['counts']
    if kb['documents']:
        lines += [f'**Documents** — {counts["documents"]}: {_names(kb["documents"], MAX_DOCS_LISTED)}', '']
    if kb['link_types']:
        connects = ' · '.join(f'{lt["plain"]} ({lt["count"]})' for lt in kb['link_types'])
        lines += [f'**How it connects** — {_plural(counts["passages"], "passage")} joined by {_plural(counts["links"], "link")}: {connects}', '']
    if kb['topics']:
        lines += ['**Topics** — ' + ' · '.join(kb['topics']), '']
    if kb.get('knowledge_planning') == 'off':
        lines += ['**Questions** — this suite asks no questions from this knowledge base.', '']
    elif kb.get('knowledge_planning') == 'grounding_only':
        lines += ['**Questions** — this suite grounds its test data in this knowledge base and asks no questions from it.', '']
    elif kb['sample_questions']:
        lines += [f'**Questions the simulation will ask** — {counts["questions"]} prepared, for example:', '']
        for q in kb['sample_questions']:
            lines.append(f'- {q["question"]}')
            answer = _one_line(q['answer']) if q['answer'] else ''
            source = f' *(from {", ".join(q["sources"])})*' if q['sources'] else ''
            if answer or source:
                lines.append(f'  - Expected: {answer or "—"}{source}')
        lines.append('')
    if kb['build_warnings']:
        lines += ['**Worth knowing**', '']
        lines += [f'- {_squash(w)}' for w in kb['build_warnings']]
        lines.append('')
    paths = kb['overview_paths']
    if len(paths) == 1:
        lines += [f'Full overview: [{OVERVIEW}]({paths[0]})', '']
    elif paths:
        links = ' · '.join(f'[{p.split("/")[-2][:8]}]({p})' for p in paths)
        lines += [f'Full overviews: {links}', '']
    return lines


#: Every build warning the backend writes into `corpus_meta.json` `build_warnings`, by the prefix of its own
#: text → a stable code. The backend is the only author of these lines (the corpus build lints, the
#: role plan, the field planner and the scope check).
#: A line that matches no prefix is `other`; a new or reworded backend line lands there until it is added.
_WARN_CODES: tuple[tuple[str, str], ...] = (
    ('corpus SCOPE check DID NOT RUN', 'scope_check_not_run'),
    ('corpus SCOPE:', 'config_documents'),
    ('testset COVERS ONLY PART of the corpus', 'testset_coverage'),
    ('testset cites MORE distinct chunks', 'testset_citation_mismatch'),
    ('testset contains RESTATED questions', 'testset_restated_questions'),
    ('testset RESTATES the same facts', 'testset_restated_facts'),
    ('testset REDUNDANCY was NOT CHECKED', 'testset_redundancy_unchecked'),
    ('knowledge graph has 0 edges', 'graph_no_edges'),
    ('knowledge graph is TOO SPARSE', 'graph_sparse'),
    ('corpus looks UNDER-CHUNKED', 'under_chunked'),
    ('build lints did not run', 'lints_not_run'),
    ('corpus build: entity_type=', 'entity_type_untyped'),
    ('the subgraph-cap check did NOT run', 'subgraph_cap_unknown'),
    ('synthesis refused', 'synthesis_refused'),
    ('chunk roles:', 'instruction_roles'),
    ('corpus field planner', 'field_planner'),
    ('corpus overview:', 'overview'),
)
WARN_OTHER = 'other'

_WARN_CONFIG = re.compile(r'(\d+) of (\d+) source document\(s\) read as structured CONFIGURATION rather than knowledge\s*[—-]+\s*(.+?)\s*[—-]+', re.S)


def code_of(raw: Any) -> str:
    """The stable code of one raw backend build warning; `other` when no prefix matches."""
    text = str(raw or '').lstrip()
    return next((code for prefix, code in _WARN_CODES if text.startswith(prefix)), WARN_OTHER)


def _plain_config(text: str) -> Optional[str]:
    config = _WARN_CONFIG.search(text)
    if not config:
        return None
    n, total, names = config.groups()
    noun = 'document' if total == '1' else 'documents'
    verb = 'looks' if n == '1' else 'look'
    return f'{n} of {total} {noun} ({names.strip()}) {verb} like configuration rather than documentation, so questions drawn from it may ask the agent to recite settings.'


#: The codes a customer sees, each with its plain wording. Every other code (`other` included) is the
#: operator's: it describes the build's own testset, graph or planners, and its fix is a rebuild.
_PLAIN_WORDING = {'config_documents': _plain_config}


def plain_warnings(warnings: list) -> list[dict]:
    """`[{code, message}]`: the build warnings a customer can act on, in plain words, one per distinct
    message. Only codes in `_PLAIN_WORDING` appear; the backend's own wording never reaches the page or
    knowledge.md, and ``build_warnings_raw`` keeps it verbatim for operators."""
    out: list[dict] = []
    for warning in warnings:
        text = str(warning)
        code = code_of(text)
        word = _PLAIN_WORDING.get(code)
        message = word(text) if word else None
        if message and all(item['message'] != message for item in out):
            out.append({'code': code, 'message': message})
    return out


def render_knowledge_md(summary: dict, out_of_reach: list = ()) -> str:
    lines = [
        '<!-- A display copy of what the Plurai backend built; the backend is the source of truth. Regenerated by grounding_pull.py. -->',
        "# Your agent's knowledge",
        '',
        totals_line(summary.get('totals') or {}),
        '',
    ]
    if out_of_reach:
        lines += ['**Worth knowing**', '', *out_of_reach, '']
    for kb in summary.get('knowledge_bases') or []:
        lines += _render_kb(kb)
    return '\n'.join(lines).rstrip('\n') + '\n'


# ── the trace brief ───────────────────────────────────────────────────────────
def read_workspace_id(workdir: Path) -> tuple[Optional[str], Optional[str]]:
    """(workspace id, problem) — read only, never minted: no marker means no brief was ever published."""
    workdir = Path(workdir)
    root, alias = workdir, None
    if workdir.parent.name == 'envs' and workdir.parent.parent.name == 'plurai_sim':
        root, alias = workdir.parent.parent, workdir.name
    marker = root / '.workspace_id'
    if not marker.exists():
        return None, None
    try:
        value = marker.read_text(encoding='utf-8').strip()
    except OSError as exc:
        return None, f'.workspace_id cannot be read ({type(exc).__name__})'
    if not _PLAIN_WS.fullmatch(value):
        return None, '.workspace_id does not hold a plain id'
    return (f'{value}.{alias}' if alias else value), None


def _job_quality(server: str, job_id: str) -> tuple[Optional[str], Optional[list]]:
    """(degraded, blind_topics) off the analysis job's envelope; (None, None) when it cannot be read."""
    try:
        envelope = json.loads(_get(f'{server}/jobs/{_seg(job_id)}'))
    except Exception as exc:  # noqa: BLE001 - quality is additive; the brief is already pulled
        _log(f'job {job_id}: quality not read ({_describe(exc)})')
        return None, None
    detail = envelope.get('detail') if isinstance(envelope, dict) and isinstance(envelope.get('detail'), dict) else {}
    meta = detail.get('meta') if isinstance(detail.get('meta'), dict) else {}
    degraded = meta.get('degraded') or detail.get('degraded')
    blind = meta.get('blind_topics') if isinstance(meta.get('blind_topics'), list) else detail.get('blind_topics')
    return (
        _squash(degraded) if isinstance(degraded, str) and degraded.strip() else None,
        [str(t) for t in blind if isinstance(t, (str, int))] if isinstance(blind, list) else None,
    )


def pull_traces(workdir: Path, job_id: Optional[str] = None) -> dict:
    workdir = Path(workdir)
    result: dict = {'command': 'traces', 'workdir': str(workdir)}
    workspace, problem = read_workspace_id(workdir)
    if problem:
        _log(problem)
        return {**result, 'exit': EXIT_USAGE, 'note': problem}
    if not workspace:
        return {**result, 'exit': EXIT_OK, 'note': 'no workspace id on this bench; no trace brief to pull'}
    server, problem = _preflight(workdir)
    if problem:
        _log(problem)
        return {**result, 'exit': EXIT_USAGE, 'note': problem}

    got: dict = {}
    for name in BRIEF_FILES:
        try:
            got[name] = _get(f'{server}/trace-briefs/{_seg(workspace)}/files/{name}')
        except _Absent:
            continue
        except _Disabled:
            return {**result, 'exit': EXIT_OK, 'workspace_id': workspace, 'note': 'this backend keeps no trace briefs; nothing pulled'}
        except Exception as exc:  # noqa: BLE001 - any failure keeps the previous copy
            _log(f'{name}: {_describe(exc)}; the previous copy (if any) is kept')
            return {**result, 'exit': EXIT_FAILED, 'workspace_id': workspace, 'errors': [{'file': name, 'error': _describe(exc)}]}
    if not got:
        return {**result, 'exit': EXIT_OK, 'workspace_id': workspace, 'note': 'no trace brief is published for this bench; nothing written'}

    art = report_pieces._art(workdir)
    staged = art / '.trace_brief.staging'
    shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True)
    for name, body in got.items():
        (staged / name).write_bytes(body)
    _write_source(staged, 'workspace_id', workspace, {})
    place = art / 'trace_brief'
    if place.exists():
        old = art / f'.trace_brief.old-{uuid.uuid4().hex}'
        os.replace(place, old)
        shutil.rmtree(old, ignore_errors=True)
    os.replace(staged, place)

    degraded, blind = _job_quality(server, job_id) if job_id else (None, None)
    files = [name for name in BRIEF_FILES if name in got]
    summary = {
        'schema': BRIEF_SCHEMA,
        'note': DISPLAY_NOTE,
        'workspace_id': workspace,
        'pulled_at': report_pieces._now(),
        'files': files,
        'sections': [{'file': name, 'title': SECTION_TITLES[name]} for name in files if name in SECTION_TITLES],
        'readme': 'trace_brief/README.md' if 'README.md' in got else None,
        'degraded': degraded,
        'blind_topics': blind,
    }
    report_pieces._write_atomic(art / 'trace_brief.json', json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    return {**result, 'exit': EXIT_OK, 'workspace_id': workspace, 'files': files, 'summary': 'artifacts/trace_brief.json'}


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    knowledge = sub.add_parser('knowledge', help="pull every knowledge base's corpora and write knowledge.md")
    knowledge.add_argument('workdir', type=Path)
    traces = sub.add_parser('traces', help='pull the published trace brief')
    traces.add_argument('workdir', type=Path)
    traces.add_argument('--job-id', default=None, help="the analysis job, to read the brief's degraded / blind topics")
    args = parser.parse_args(argv)
    try:
        if args.command == 'knowledge':
            result = pull_knowledge(args.workdir)
        else:
            result = pull_traces(args.workdir, (args.job_id or '').strip() or None)
    except Exception as exc:  # noqa: BLE001 - never raise to the caller
        _log(f'{args.command} failed ({type(exc).__name__}: {exc})')
        result = {'command': args.command, 'exit': EXIT_FAILED, 'error': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result['exit'])


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
