#!/usr/bin/env python3
"""The plugin's side of a corpus part: what it keeps when it delivers one, and what it records when the build ends.

`/build-rag-corpus` extracts a corpus locally, uploads it with `plurai ingest-corpus`, and hands the printed part
entry to the backend's `ground_workspace_start`. It returns once the delivery is accepted. The grounding job builds
the knowledge graph from the part while it settles, and lists each build in its detail as `corpus_builds`. The
suite's Step 0b reads that detail after the job ends. Two commands carry the bookkeeping between those points:

    grounding_corpus.py part   --workdir D --entry F --kb-id ID (--kb-label TEXT | --kb-label-file F) --rag-dir DIR
                               [(--tool NAME | --tool-file F)] [--descriptions F]
    grounding_corpus.py record --workdir D --status F
    grounding_corpus.py pending --workdir D
    grounding_corpus.py forget  --workdir D --label LABEL

The label and the tool name are customer text: a skill writes each to a file with a quoted heredoc and passes
`--kb-label-file` and `--tool-file`, so no character in them reaches the shell. `--kb-label` and `--tool` take the
same text as a value.

`part` checks the entry `ingest-corpus` printed (so a refusal is stated before a tool call is spent), registers the
part in `artifacts/corpus_parts.json` with the shape of the local extraction, and prints the entry as the `added`
argument. With `--descriptions` (the checked entries `grounding_delivery.py added` printed for the re-uploaded agent
descriptions) the argument lists those first and the corpus part last, so one `ground_workspace_start` call carries the
reconciled PRD and the corpus the build reads it with. New bytes under a label already registered supersede that part: the registry keeps the new row and stderr
names the old one as `REPLACES=<sha256>`, which the caller delivers as `removed`. The registry is what `record` needs and the backend cannot know: which knowledge base a part belongs to,
the label that describes it, and the source kind and document counts the health gate reads.

`record` reads the grounding job's detail (the `job_status` object, or the detail alone) and, for every build that is
done and healthy, writes the knowledge-base markers the scenario and eval generators read: `.knowledge_bases` (one
entry per base, a base rebuilt replaces its own corpus ids and no other entry) and `.corpus_id` (only for one base with
one corpus). A failed, degenerate or unregistered build is reported and never recorded. A build is handled once: the
registry marks it, so the next suite neither writes a base back that the suite cleared or marked nor repeats a report.

The health gate: a graph the knowledge planner cannot walk (it needs two hops)
plants no knowledge question, so recording it hands the suite a corpus that tests nothing.

    directory      no edges; or one node per source file; or fewer edges than the nodes that owe one (a file read in
                   windows is a chain, so its first node owes none)
    json_records   no edges over more than one node (a record corpus is a chain out of one file)
    any other kind no edges (the source's own chunking; there is no ratio to judge)

`forget` drops the registered part under a label and prints `REMOVED=<sha256>`, which the caller delivers as `removed`:
the rebuild of unchanged bytes. Delivering the same bytes again then registers the part afresh, so `record` writes the new
build's corpus id.

`pending` prints `PENDING=<n>`, the delivered corpus parts whose build has no recorded outcome. A generator that starts
before the suite's Step 0b reads it, waits for the grounding job when it is not zero, and runs `record`; the suite and the
generators share the one idempotent record.

`record` prints one `CORPUS <kb_id> <state> ...` line per build, then `RESULT recorded=<n> failed=<n> degenerate=<n>
waiting=<n> unregistered=<n> handled=<n>`. Exit codes: 0 nothing needs the agent; 1 a build failed, is degenerate or is
unregistered; 3 an input or a marker cannot be read (nothing is written); 4 a build is still running. Stdlib only, on
the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grounding_files
from grounding_files import write_atomic

REGISTRY_NAME = 'corpus_parts.json'
KNOWLEDGE_BASES = '.knowledge_bases'
CORPUS_ID = '.corpus_id'
CORPUS_KIND = 'corpus_source'
#: Suffixes a directory source splits by section; any other file is read in windows, a chain whose first node owes no edge.
SECTION_SUFFIXES = frozenset({'.md', '.markdown'})

_PLAIN_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_ENTRY_KEYS = frozenset({'kind', 'sha256', 'bytes', 'name', 'label', 'posture', 'anon_key_fingerprint'})
_POSTURES = ('raw', 'anonymized')
_REGISTERED_KEYS = ('sha256', 'label', 'kb_id', 'kb_label', 'source_kind', 'chunks', 'relationships', 'source_documents', 'chain_documents')


def _fail(message: str) -> int:
    print(f'grounding_corpus: {message}', file=sys.stderr)
    return 3


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# --- the printed entry -------------------------------------------------------------------------------


def _check_label(entry: dict) -> Optional[str]:
    label = entry.get('label')
    if not isinstance(label, str) or not label:
        return 'carries no label'
    if not _PLAIN_NAME.match(label) or '..' in label:
        return f'label {label!r} is not a plain name (letters, digits, ".", "_" and "-")'
    return None


def check_entry(entry: Any) -> Optional[str]:
    """The cause a printed corpus part entry is not deliverable, or None."""
    return grounding_files.check_part_entry(entry, CORPUS_KIND, label_check=_check_label)


# --- the local extraction ----------------------------------------------------------------------------


def extraction_shape(rag_dir: Path) -> Optional[dict]:
    """The source kind and counts of the extraction the part was built from, or None when it cannot be read."""
    try:
        chunks = json.loads((rag_dir / 'chunks.json').read_text(encoding='utf-8'))
        manifest = json.loads((rag_dir / 'manifest.json').read_text(encoding='utf-8'))
        relationships_path = rag_dir / 'relationships.json'
        relationships = json.loads(relationships_path.read_text(encoding='utf-8')) if relationships_path.is_file() else []
    except (OSError, ValueError):
        return None
    if not isinstance(chunks, list) or not isinstance(relationships, list) or not isinstance(manifest, dict):
        return None
    source_kind = manifest.get('source_kind')
    if not isinstance(source_kind, str) or not source_kind:
        return None
    names: set = set()
    for chunk in chunks:
        name = chunk.get('name') if isinstance(chunk, dict) else None
        if isinstance(name, str):
            names.add(name)
    chains = 0
    if source_kind == 'directory':
        chains = sum(1 for name in names if PurePosixPath(name).suffix.lower() not in SECTION_SUFFIXES)
    return {
        'source_kind': source_kind,
        'chunks': len(chunks),
        'relationships': len(relationships),
        'source_documents': len(names),
        'chain_documents': chains,
    }


# --- the registry ------------------------------------------------------------------------------------


def _read_registry(workdir: Path) -> Optional[list]:
    """The registered parts; [] when there is no registry; None when it exists and cannot be read."""
    path = workdir / 'artifacts' / REGISTRY_NAME
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    parts = document.get('parts') if isinstance(document, dict) else None
    if not isinstance(parts, list) or not all(isinstance(p, dict) and all(k in p for k in _REGISTERED_KEYS) for p in parts):
        return None
    return parts


def _check_descriptions(listed: Any) -> Optional[str]:
    """The cause the description entries `grounding_delivery.py added` printed are not what a corpus delivery carries, or None."""
    if not isinstance(listed, list) or not all(isinstance(e, dict) for e in listed):
        return 'is not the JSON array of description entries `grounding_delivery.py added` prints'
    if not listed:
        return 'lists no description'
    if not all(e.get('kind') == 'agent_description' and isinstance(e.get('name'), str) for e in listed):
        return 'holds an entry that is not an agent_description part'
    names = [e['name'] for e in listed]
    if len(set(names)) != len(names):
        return 'lists two parts that share one name'
    return None


def _text_option(value: Optional[str], path: Optional[str], option: str) -> Optional[str]:
    """The text an option carries, given inline or in a file (its one trailing newline dropped). Raises ValueError for a bad file."""
    if path is None:
        return value
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f'{option} {path} cannot be read ({type(error).__name__}); write the text there again.') from None
    return text[:-1] if text.endswith('\n') else text


def register_part(workdir: Path, entry: dict, kb_id: str, kb_label: str, tool: Optional[str], rag_dir: Path, descriptions: list) -> tuple:
    """Register the checked corpus part and return `(added, None)`: the descriptions, then the entry. Else `(None, cause)`, nothing written.

    New bytes under a label already registered supersede that part; stderr names the old one as `REPLACES=<sha256>`.
    """
    shape = extraction_shape(rag_dir)
    if shape is None:
        return None, f'the extraction under {rag_dir} cannot be read (chunks.json and manifest.json); extract again before delivering.'
    parts = _read_registry(workdir)
    if parts is None:
        return None, f'artifacts/{REGISTRY_NAME} cannot be read; remove it and deliver again.'
    row = {'sha256': entry['sha256'], 'label': entry['label'], 'kb_id': kb_id, 'kb_label': kb_label, **shape}
    if tool:
        row['tool'] = tool
    delivered_before = next((part for part in parts if part['sha256'] == entry['sha256']), None)
    if delivered_before is not None and delivered_before.get('handled'):
        row['handled'] = delivered_before['handled']  # the same bytes make no new build: its outcome stands
    kept = [part for part in parts if part['sha256'] != entry['sha256'] and part['label'] != entry['label']]
    for part in parts:
        if part['label'] == entry['label'] and part['sha256'] != entry['sha256']:
            print(f'grounding_corpus: REPLACES={part["sha256"]}', file=sys.stderr)
    write_atomic(workdir / 'artifacts' / REGISTRY_NAME, json.dumps({'version': 1, 'parts': [*kept, row]}, indent=2, ensure_ascii=False) + '\n')
    return [*descriptions, entry], None


def _cmd_part(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    try:
        entry = json.loads(Path(args.entry).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _fail(f'{args.entry} is not the JSON object `plurai ingest-corpus` prints; the delivery did not complete.')
    problem = check_entry(entry)
    if problem is not None:
        return _fail(f'the part entry {problem}; nothing was delivered.')
    try:
        kb_label = _text_option(args.kb_label, args.kb_label_file, '--kb-label-file')
        tool = _text_option(args.tool, args.tool_file, '--tool-file')
    except ValueError as error:
        return _fail(str(error))
    if not kb_label:
        return _fail('the knowledge-base label is empty; nothing was delivered.')
    descriptions: list = []
    if args.descriptions:
        try:
            listed = json.loads(Path(args.descriptions).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            listed = None
        problem = _check_descriptions(listed)
        if problem is not None or not isinstance(listed, list):
            return _fail(f'{args.descriptions} {problem}; nothing was delivered.')
        descriptions = listed
    added, problem = register_part(workdir, entry, args.kb_id, kb_label, tool, Path(args.rag_dir), descriptions)
    if problem is not None:
        return _fail(problem)
    print(json.dumps(added, separators=(',', ':'), ensure_ascii=False))
    return 0


# --- the health gate ---------------------------------------------------------------------------------


def judge(part: dict, nodes: Any, edges: Any) -> Optional[str]:
    """None when the built graph can carry knowledge questions, else the cause it cannot."""
    if not _is_count(nodes) or not _is_count(edges):
        return 'the build reported no node and edge counts'
    if nodes == 0:
        return 'the graph has no nodes'
    kind = part['source_kind']
    if kind == 'directory':
        if edges == 0:
            return 'the graph has no edges'
        if nodes == part['source_documents']:
            return 'one node per source file, so the graph has nothing to walk'
        if edges < nodes - part['chain_documents']:
            return 'fewer edges than the nodes that owe one'
        return None
    if kind == 'json_records':
        return 'a record corpus with no edges over more than one record' if edges == 0 and nodes > 1 else None
    return 'the graph has no edges' if edges == 0 else None


# --- record ------------------------------------------------------------------------------------------


def _read_bases(workdir: Path) -> Optional[list]:
    path = workdir / KNOWLEDGE_BASES
    if not path.is_file():
        return []
    try:
        bases = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(bases, list) or not all(isinstance(b, dict) and isinstance(b.get('kb_id'), str) for b in bases):
        return None
    return bases


def _builds(status: Any) -> Optional[list]:
    detail = status.get('detail') if isinstance(status, dict) and isinstance(status.get('detail'), dict) else status
    builds = detail.get('corpus_builds') if isinstance(detail, dict) else None
    if builds is None and isinstance(detail, dict):
        return []
    if not isinstance(builds, list) or not all(isinstance(b, dict) and isinstance(b.get('part'), str) for b in builds):
        return None
    return builds


def _base_entry(rows: list) -> dict:
    """One `.knowledge_bases` entry from the registered parts and the finished builds of one base."""
    first = rows[0][0]
    nodes = sum(build['node_count'] for _, build in rows)
    if len(rows) == 1:
        label = f'{first["kb_label"]} — {nodes} knowledge-graph nodes'
    else:
        label = f'{first["kb_label"]} — {len(rows)} corpora, {nodes} knowledge-graph nodes in total'
    entry = {'kb_id': first['kb_id']}
    if first.get('tool'):
        entry['tool'] = first['tool']
    entry['label'] = label
    entry['corpus_ids'] = [build['corpus_id'] for _, build in rows]
    return entry


def _cmd_record(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    try:
        status = json.loads(Path(args.status).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _fail(f'{args.status} is not the JSON the grounding job reports; nothing was recorded.')
    builds = _builds(status)
    if builds is None:
        return _fail('the job detail carries a corpus_builds value that is not a list of builds; nothing was recorded.')
    registered = _read_registry(workdir)
    if registered is None:
        return _fail(f'artifacts/{REGISTRY_NAME} cannot be read; nothing was recorded.')
    bases = _read_bases(workdir)
    if bases is None:
        return _fail(f'{KNOWLEDGE_BASES} cannot be read; fix or remove it. Nothing was recorded.')
    by_part = {part['sha256']: part for part in registered}
    counts = {'recorded': 0, 'failed': 0, 'degenerate': 0, 'waiting': 0, 'unregistered': 0, 'handled': 0}
    healthy: dict = {}
    verdicts: dict = {}
    lines = []
    for build in builds:
        part = by_part.get(build['part'])
        name = part['kb_id'] if part else str(build.get('label') or build['part'])
        if part is None:
            counts['unregistered'] += 1
            lines.append(f'CORPUS {name} unregistered: this machine did not deliver the part; run /build-rag-corpus here to record its base')
            continue
        if part.get('handled'):
            counts['handled'] += 1
            lines.append(f'CORPUS {name} handled before ({part["handled"]})')
            continue
        state = build.get('status')
        if state in ('queued', 'running'):
            counts['waiting'] += 1
            lines.append(f'CORPUS {name} waiting')
        elif state != 'done':
            counts['failed'] += 1
            verdicts[part['sha256']] = 'failed'
            lines.append(f'CORPUS {name} failed: the build did not finish; the agent classifies the error, the customer is told to deliver again later')
        else:
            cause = judge(part, build.get('node_count'), build.get('edge_count'))
            corpus_id = build.get('corpus_id')
            if cause is None and (not isinstance(corpus_id, str) or not corpus_id):
                cause = 'the build names no corpus id'
            if cause is not None:
                counts['degenerate'] += 1
                verdicts[part['sha256']] = 'degenerate'
                lines.append(
                    f'CORPUS {name} degenerate: {cause}; re-run /build-rag-corpus with a smaller --chunk-size, or tell the user the documents are too thin to ground retrieval scenarios'
                )
            else:
                counts['recorded'] += 1
                verdicts[part['sha256']] = 'recorded'
                healthy.setdefault(part['kb_id'], []).append((part, build))
                lines.append(f'CORPUS {name} recorded corpus_id={corpus_id} nodes={build["node_count"]} edges={build["edge_count"]}')
    if healthy:
        merged = [base for base in bases if base['kb_id'] not in healthy]
        merged.extend(_base_entry(rows) for rows in healthy.values())
        order = {base['kb_id']: index for index, base in enumerate(bases)}
        merged.sort(key=lambda base: order.get(base['kb_id'], len(order)))
        write_atomic(workdir / KNOWLEDGE_BASES, json.dumps(merged, indent=2, ensure_ascii=False) + '\n')
        single = merged[0]['corpus_ids'][0] if len(merged) == 1 and len(merged[0].get('corpus_ids') or []) == 1 else None
        if single:
            write_atomic(workdir / CORPUS_ID, single)
        elif (workdir / CORPUS_ID).is_file():
            (workdir / CORPUS_ID).unlink()
    if verdicts:
        for part in registered:
            if part['sha256'] in verdicts:
                part['handled'] = verdicts[part['sha256']]
        write_atomic(workdir / 'artifacts' / REGISTRY_NAME, json.dumps({'version': 1, 'parts': registered}, indent=2, ensure_ascii=False) + '\n')
    print('\n'.join(lines))
    print('RESULT ' + ' '.join(f'{key}={value}' for key, value in counts.items()))
    if counts['waiting']:
        return 4
    return 1 if counts['failed'] or counts['degenerate'] or counts['unregistered'] else 0


def _cmd_pending(args: argparse.Namespace) -> int:
    registered = _read_registry(Path(args.workdir))
    if registered is None:
        return _fail(f'artifacts/{REGISTRY_NAME} cannot be read; remove it and deliver the corpus again.')
    print(f'PENDING={sum(1 for part in registered if not part.get("handled"))}')
    return 0


def _cmd_forget(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    registered = _read_registry(workdir)
    if registered is None:
        return _fail(f'artifacts/{REGISTRY_NAME} cannot be read; remove it and deliver the corpus again.')
    gone = [part for part in registered if part['label'] == args.label]
    if not gone:
        return _fail(f'no corpus part is registered under the label {args.label!r}; nothing to remove.')
    write_atomic(workdir / 'artifacts' / REGISTRY_NAME, json.dumps({'version': 1, 'parts': [p for p in registered if p['label'] != args.label]}, indent=2, ensure_ascii=False) + '\n')
    for part in gone:
        print(f'REMOVED={part["sha256"]}')
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Bookkeeping for a delivered corpus part and its build.")
    sub = parser.add_subparsers(dest='command', required=True)
    part = sub.add_parser('part', help='check the entry `plurai ingest-corpus` printed, register the part, print it as the `added` argument')
    part.add_argument('--workdir', required=True)
    part.add_argument('--entry', required=True, help='the file the printed object was saved to')
    part.add_argument('--kb-id', required=True, help='the knowledge base the corpus belongs to: the tool name or a domain slug')
    label = part.add_mutually_exclusive_group(required=True)
    label.add_argument('--kb-label', help='what this corpus holds, in the words of its task description')
    label.add_argument('--kb-label-file', help='a file holding that text, for a label with shell characters in it')
    part.add_argument('--descriptions', default=None, help='the checked description entries (`grounding_delivery.py added`) to deliver in the same call')
    tool = part.add_mutually_exclusive_group()
    tool.add_argument('--tool', default=None, help='the agent tool that reads the base; leave out for prompt-injected knowledge')
    tool.add_argument('--tool-file', default=None, help='a file holding the tool name')
    part.add_argument('--rag-dir', required=True, help='the extraction directory the part was built from (<plurai_sim>/rag_corpus)')
    part.set_defaults(func=_cmd_part)
    record = sub.add_parser('record', help='record the knowledge bases the grounding job built')
    record.add_argument('--workdir', required=True)
    record.add_argument('--status', required=True, help='the job_status object of the grounding job, saved to a file')
    record.set_defaults(func=_cmd_record)
    pending = sub.add_parser('pending', help='print how many delivered corpus parts have no recorded outcome yet')
    pending.add_argument('--workdir', required=True)
    pending.set_defaults(func=_cmd_pending)
    forget = sub.add_parser('forget', help='drop the part registered under a label and print its hash, to deliver as `removed`')
    forget.add_argument('--workdir', required=True)
    forget.add_argument('--label', required=True, help='the label the part was delivered under (the --kb-id of `part`)')
    forget.set_defaults(func=_cmd_forget)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
