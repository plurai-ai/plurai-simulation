#!/usr/bin/env python3
"""Does the built corpus hold what was declared and every source the scan contract hashed? — `/build-rag-corpus` Step 4.

Two gate findings (``scripts/gate_ledger.py``), both advisory and HOLDING: exit 1 until the cause is
repaired or the plugin overrules the finding in ``$ART/.gate_overrules.jsonl``.

``plugin.corpus.shortfall`` — the COUNT. ``declared_units`` (the last such line of ``$WORKDIR/.corpus_scope``)
against what landed: distinct source documents for a ``directory`` source, chunks for any other kind; a
file the ingest's ``not_ingested`` list names as ``empty`` counts as landed. Landing more than
:data:`SHORTFALL_TOLERANCE` of the declaration short holds. No ``declared_units`` recorded is no finding,
said in one line: the count is never back-filled from what landed.

``plugin.corpus.source_not_ingested`` — the NAMES. Every path in the contract's `source_inventory.files`
must be an ingested source (a chunk `name` in `rag_corpus/chunks.json`) or an `empty` entry of the
ingest's own `not_ingested` list in `rag_corpus/manifest.json` (`{path, reason}`, written by the SDK
from the extractor). No model-written text excuses a source.
Only `empty` excuses a path: an empty file had nothing to land. Any other reason (`no text
extracted`, `not UTF-8 text`, `unreadable PDF`) means content existed and did not land, so it holds
with the remedy of converting that file, whether or not the contract hashed it. A PDF the ingest read from only some pages is a `partially_ingested`
entry (`{path, reason, pages, of}`): `empty` pages are blank and `no_text_layer` pages (a diagram, a
scan with no text on a file whose other pages landed) hold nothing a text corpus can carry, so both are
only named; any other reason holds the same way. A `not_ingested` entry names the file as the inventory does, so it covers
that path only. An ingest names a chunk by its FILE (`guide.md`), or by its inventory path when two
files share that name, while the inventory records the path under the scanned root
(`docs/guide.md`). A document named by an inventory path covers exactly that path; the paths left
must be covered by as many ingested documents of their file name (each document's chunks restart
at `chunk_idx` 0; chunks that carry no `chunk_idx` count one document per distinct name).
A missing path no suffix in the manifest's `staged_suffixes` admits was never staged, so the line names
that cause and its remedy: re-extracting cannot help it. A suffix admits a path whose lower-cased name
ends with it (`.json.gz` admits `x.json.gz`), and an archive member (`a.zip!/m.txt`) is staged with
its archive.

Both fingerprints cover the counts (``declared_units``, ``landed``, ``not_ingested_reasons``) AND the files
(``unaccounted_paths``: every source the name check holds on, sorted; ``not_ingested_paths``: every path the
ingest's ``not_ingested`` names for a reason other than ``empty``, sorted). An overrule therefore lapses when
what landed changes, and an overrule for one lost file never lifts a later loss of another with the same counts.

ONE PART of a partitioned scope (``--partition $ART/corpus_roots.json --kb-id <id> --scan-roots .scan_corpus_roots``,
written by ``/build-rag-corpus`` Step 1 when each RAG tool gets its own base): the name check covers the contract's
paths under this part's roots PLUS every path no part and no out-of-reach root covers, so a file assigned to no base
holds in every part; out-of-reach roots (each a whole ``.scan_corpus_roots`` line, with the code line that shows no
tool can return it) are named, never checked; the shortfall compares the part's own ``declared_units``. Every root of a
part carries its own reach cite (a file:line in a ``reach`` map, or the one ``reach`` string of a single-root part):
an uncited root is refused, never taken as in or out of reach. An inventory
id maps to its file by the SDK's rule: bare under one root, prefixed with the root's name under several.

Usage:
    reconcile_corpus_scope.py --contract <scan_contract.json> --chunks <chunks.json> --manifest <manifest.json> \\
        --scope <.corpus_scope> --art <artifacts dir> [--findings-out <path>]
        [--partition <corpus_roots.json> --kb-id <kb_id> --scan-roots <.scan_corpus_roots>]
    exit 0 = nothing holds · 1 = a finding holds · 2 = an input or the ledger is unreadable
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gate_ledger  # noqa: E402

#: How far short of ``declared_units`` a corpus may land, as a fraction of the declaration, before the
#: shortfall holds. One constant; the threshold is not tuned here.
SHORTFALL_TOLERANCE = 0.05

_DECLARED_RE = re.compile(r'^\s*declared_units\s*:\s*(\S.*?)\s*$')


def _name(path: str) -> str:
    return PurePosixPath(path.strip()).name


def _rel(path: str) -> str:
    return PurePosixPath(path.strip()).as_posix().removeprefix('./')


def _staged(path: str, staged_suffixes: list[str]) -> bool:
    """Whether a staged suffix admits ``path``: its lower-cased file name, or its archive's for a member, ends with one."""
    name = _name(path.split('!/', 1)[0]).lower()
    return any(name.endswith(s.lower()) and name != s.lower() for s in staged_suffixes)


def _pages(entry: dict) -> str:
    return f'pages {", ".join(str(n) for n in entry["pages"])} of {entry["of"]}'


#: The one remedy for content that existed and did not land, a whole file or some of its pages.
UNREAD_REMEDY = (
    'Re-extracting as-is cannot help (the ingest does not OCR): OCR each scanned PDF or convert the file to '
    'Markdown or UTF-8 text, then re-extract'
)

#: What the finding asks for when the names do not reconcile. The line above it names the files.
NOT_INGESTED_REMEDY = 'land every source the contract hashed: apply the remedy on the line that names the files, then re-extract'

#: What the finding asks for when fewer units landed than declared.
SHORTFALL_REMEDY = (
    'find why units did not land (a glob that missed a subdirectory, a filter narrower than intended, files the extractor '
    'skipped), fix it and re-extract; never rewrite declared_units to match what landed'
)

#: Page reasons that name a page without holding: nothing on it could land in a text corpus.
NAMED_PAGE_REASONS = ('empty', 'no_text_layer')

#: The ``not_ingested_reasons`` key for a missing source the ingest did not name.
UNNAMED_REASON = 'not named by the ingest'


class Reconciled(NamedTuple):
    """The name check's outcome. ``code`` is 1 when a source is unaccounted for; ``lines`` say which and
    why; ``missing`` counts the hashed sources that reached no chunk; ``reasons`` counts every file (or
    file's pages) that did not land, by reason — the finding's ``not_ingested_reasons``; ``unaccounted``
    is every path the check holds on (no chunk, no text, or pages with no text), sorted — the finding's
    ``unaccounted_paths``."""

    code: int
    lines: list[str]
    missing: int
    reasons: dict[str, int]
    unaccounted: list[str]


def reconcile(
    inventory: list[str],
    chunks: list[dict],
    not_ingested: list[dict] | None,
    *,
    staged_suffixes: list[str] | None,
    partially_ingested: list[dict] | None,
) -> Reconciled:
    """The name check for one comparison. Pure, so the rule is testable without files.

    ``staged_suffixes`` is the manifest's; ``None`` (an SDK that does not record it) names no cause.
    ``not_ingested`` ``None`` is an SDK that does not record what it left out, which is not ``[]``;
    ``partially_ingested`` ``None`` is likewise an SDK that does not record pages it left out.
    """
    recorded = not_ingested is not None
    not_ingested = not_ingested or []
    excused = {_rel(str(entry['path'])) for entry in not_ingested if entry.get('reason') == 'empty'}
    unread = {_rel(str(entry['path'])): entry.get('reason') or 'no reason given' for entry in not_ingested if entry.get('reason') != 'empty'}
    partial = partially_ingested or []
    unread_pages = [entry for entry in partial if entry.get('reason') not in NAMED_PAGE_REASONS]
    # A document named by its inventory path (a name two files share) covers exactly that path; the rest
    # are covered by as many documents of their file name.
    firsts = [_rel(str(c.get('name') or '')) for c in chunks if c.get('chunk_idx') == 0]
    # A chunk with no index cannot say where its document starts: one document per distinct name.
    firsts += sorted({_rel(str(c.get('name') or '')) for c in chunks if 'chunk_idx' not in c})
    exact = {_rel(p) for p in inventory} & set(firsts)
    documents = Counter(_name(n) for n in firsts if n not in exact)
    left: dict[str, list[str]] = {}
    for p in inventory:
        if _rel(p) not in excused and _rel(p) not in exact:
            left.setdefault(_name(p), []).append(p)
    missing = [p for name, paths in left.items() if len(paths) > documents[name] for p in paths]
    if missing or unread or unread_pages:
        unaccounted = sorted({_rel(p) for p in missing} | set(unread) | {_rel(str(entry['path'])) for entry in unread_pages})
        # Each file is named once: in the reasons list when the ingest gave one, else in the silent list.
        # An unread file holds whether or not the contract hashed it.
        silent = [p for p in missing if _rel(p) not in unread]
        reasons = Counter(unread.values())
        reasons.update(f'{entry.get("reason") or "no reason given"} (some pages)' for entry in unread_pages)
        if silent:
            reasons[UNNAMED_REASON] += len(silent)
        line = f'unaccounted: {len(missing)} of {len(inventory)} source(s) the contract hashed reached no chunk' if missing else 'unaccounted:'
        if silent:
            line += ' and the ingest did not name them: ' + ', '.join(silent)
        if not recorded:
            return Reconciled(
                1, [line + ' — this plurai does not record which files its ingest left out or why: upgrade plurai and re-run the ingest'], len(missing), dict(reasons), unaccounted
            )
        unstaged = (
            sorted({PurePosixPath(p.split('!/', 1)[0]).suffix.lower() for p in silent if not _staged(p, staged_suffixes)})
            if staged_suffixes is not None
            else []
        )
        if unread:
            line += f'{" —" if missing else ""} the extractor read no text from {len(unread)} file(s): ' + ', '.join(f'{p} ({reason})' for p, reason in unread.items())
        if unread_pages:
            line += f'{" —" if missing or unread else ""} the extractor read no text from some pages of {len(unread_pages)} file(s): ' + ', '.join(
                f'{entry["path"]} ({_pages(entry)} without text)' for entry in unread_pages
            )
        if unread or unread_pages:
            line += '. ' + UNREAD_REMEDY
        if unstaged:
            line += (
                f' — the ingest does not stage {", ".join(unstaged)}: convert those file(s) to Markdown, drop '
                f'{", ".join(unstaged)} from `sources.suffixes` in the bench manifest, and re-scan'
            )
        return Reconciled(1, [line], len(missing), dict(reasons), unaccounted)
    named = [f'not ingested: {entry["path"]} ({entry.get("reason") or "no reason given"})' for entry in not_ingested]
    named += [f'partly ingested: {entry["path"]} ({_pages(entry)}: {entry.get("reason") or "no reason given"})' for entry in partial]
    return Reconciled(0, [f"reconciled {len(inventory)} source(s) against the ingested documents and the ingest's not_ingested list.", *named], 0, {}, [])


def declared_units(scope_text: str | None) -> int | None:
    """The last ``declared_units:`` line of ``.corpus_scope`` as a count; ``None`` when none is recorded.

    Raises ValueError for a line that is not a non-negative integer: a declaration nobody can compare
    against is an unreadable input, never a silent pass.
    """
    values = [m.group(1) for line in (scope_text or '').splitlines() if (m := _DECLARED_RE.match(line))]
    if not values:
        return None
    if not values[-1].isdigit():
        raise ValueError(f'declared_units is {values[-1]!r}, not a count')
    return int(values[-1])


def landed_units(source_kind: str | None, chunks: list[dict], not_ingested: list[dict] | None) -> int:
    """Units that landed: distinct source documents (plus files named ``empty``) for a ``directory``
    source, chunks for any other kind — the units ``declared_units`` counts for that kind."""
    if source_kind != 'directory':
        return len(chunks)
    empty = sum(1 for entry in not_ingested or [] if entry.get('reason') == 'empty')
    return len({c.get('name') for c in chunks}) + empty


def shortfall(declared: int | None, landed: int) -> bool:
    return declared is not None and declared - landed > declared * SHORTFALL_TOLERANCE


def corpus_findings(
    *, subject: str, declared: int | None, landed: int, not_ingested: list[dict] | None, inventory: list[str], names: Reconciled
) -> list[gate_ledger.GateFinding]:
    """The gate findings for one reconcile: a shortfall against the declaration, unaccounted sources, both, or none.

    Each fingerprint carries the FILES beside the counts: the counts alone let an overrule for one lost file lift a
    later loss of another that happens to leave the same numbers.
    """
    findings = []
    files = {
        'unaccounted_paths': list(names.unaccounted),
        'not_ingested_paths': sorted({_rel(str(entry['path'])) for entry in not_ingested or [] if entry.get('reason') != 'empty'}),
    }
    if shortfall(declared, landed):
        reasons = Counter(entry.get('reason') or 'no reason given' for entry in not_ingested or [] if entry.get('reason') != 'empty')
        findings.append(
            gate_ledger.make_finding(
                gate_ledger.CORPUS_SHORTFALL,
                subject=subject,
                evidence={'declared_units': declared, 'landed': landed, 'not_ingested_reasons': dict(sorted(reasons.items())), **files},
                observed={},
                remedy=SHORTFALL_REMEDY,
                draft_ref=None,
            )
        )
    if names.code:
        findings.append(
            gate_ledger.make_finding(
                gate_ledger.CORPUS_SOURCE_NOT_INGESTED,
                subject=subject,
                evidence={
                    'declared_units': len(inventory),
                    'landed': len(inventory) - names.missing,
                    'not_ingested_reasons': dict(sorted(names.reasons.items())),
                    **files,
                },
                observed={},
                remedy=NOT_INGESTED_REMEDY,
                draft_ref=None,
            )
        )
    return findings


def _norm(path: str) -> PurePosixPath:
    return PurePosixPath(os.path.normpath(path.strip()))


def _to_file(ident: str, roots: list[PurePosixPath]) -> PurePosixPath | None:
    """The file an ingest or inventory id names, given the roots that call was run over (the SDK's naming rule)."""
    head, sep, member = ident.partition('!/')
    head = head.strip().removeprefix('./')
    if len(roots) == 1:
        found = roots[0] if head == roots[0].name else roots[0] / head
    else:
        root = next((r for r in roots if r.name == head.split('/', 1)[0]), None)
        found = None if root is None else root.parent / head
    return None if found is None else PurePosixPath(f'{found}{sep}{member}')


def _to_id(path: PurePosixPath, roots: list[PurePosixPath]) -> str | None:
    """The inverse: a file's id under ``roots``, or None when no root covers it."""
    head, sep, member = str(path).partition('!/')
    head_path = PurePosixPath(head)
    root = next((r for r in roots if _under(head_path, r)), None)
    if root is None:
        return None
    rel = (root.name if head_path == root else str(head_path.relative_to(root))) if len(roots) == 1 else str(head_path.relative_to(root.parent))
    return f'{rel}{sep}{member}'


def _under(path: PurePosixPath | None, root: PurePosixPath) -> bool:
    head = None if path is None else PurePosixPath(str(path).partition('!/')[0])
    return head is not None and (head == root or root in head.parents)


#: A reach cite: a file of the agent's code and a line in it (`tools/kb.py:74`, `kb.py:57-62`).
CITE = re.compile(r'[\w.-]+\.\w+:\d+')


class Part(NamedTuple):
    """One part of a partitioned scope: what its name check covers, the lines naming the rest, its declaration."""

    inventory: list[str]
    lines: list[str]
    declared: int | None
    roots: list[PurePosixPath]


def read_part(partition: dict, kb_id: str, scan: list[PurePosixPath], inventory: list[str], scope_declared: int | None) -> Part:
    """This part's slice of the contract inventory. Raises ValueError for a partition that re-decides the scope."""
    parts, away = partition.get('parts'), partition.get('out_of_reach') or {}
    if not isinstance(parts, dict) or kb_id not in parts:
        raise ValueError(f'{kb_id!r} is not a part of the partition: add it with its roots (Step 1)')
    for name, part in parts.items():
        if not isinstance(part, dict) or not isinstance(part.get('roots'), list):
            raise ValueError(f'part {name!r} needs a `roots` list and the `reach` line that shows its tool returns them')
        reach, own = part.get('reach'), [str(r) for r in part['roots']]
        if not own and not (isinstance(reach, str) and reach.strip()):
            raise ValueError(f'part {name!r} needs the `reach` line that shows its tool returns what it holds')
        # Each root is cited on its own: a `reach` map {root: file:line}, or one string for a part's only root.
        cites = reach if isinstance(reach, dict) else {own[0]: reach} if len(own) == 1 else {}
        if uncited := [r for r in own if not CITE.search(str(cites.get(r) or ''))]:
            raise ValueError(
                f'part {name!r} cites no file:line for {uncited}: in `reach`, give each root the line where its tool\'s query '
                'admits it, or move a whole .scan_corpus_roots line to out_of_reach with the line that shows no tool can return it'
            )
        if [r for r in part['roots'] if not any(_under(_norm(str(r)), s) for s in scan)]:
            raise ValueError(f'part {name!r} names a root outside .scan_corpus_roots: the union scope is never re-decided')
    if not isinstance(away, dict) or any(_norm(str(r)) not in scan or not str(c).strip() for r, c in away.items()):
        raise ValueError('every out_of_reach key is a whole .scan_corpus_roots line, with the code line that shows no tool can return it')
    away_roots = [_norm(str(r)) for r in away]
    roots = {name: [_norm(str(r)) for r in part['roots']] for name, part in parts.items()}
    if clash := [str(r) for rs in roots.values() for r in rs for a in away_roots if _under(r, a) or _under(a, r)]:
        raise ValueError(f'a part reads a root named out of reach: {clash}')
    declared = parts[kb_id].get('declared_units')
    if declared is None and scope_declared is not None:
        raise ValueError(f'part {kb_id!r} records no declared_units while .corpus_scope declares the union: record the part\'s own count')
    if declared is not None and (not isinstance(declared, int) or isinstance(declared, bool) or declared < 0):
        raise ValueError(f'part {kb_id!r} declared_units is {declared!r}, not a count')
    mine, unassigned = [], []
    for p in inventory:
        f = _to_file(p, scan)
        if any(_under(f, r) for r in roots[kb_id]):
            mine.append(p)
        elif not any(_under(f, r) for r in [*away_roots, *(r for rs in roots.values() for r in rs)]):
            unassigned.append(p)
    lines = [f'out of reach (in no knowledge base): {r} — {c}' for r, c in away.items()]
    if unassigned:
        lines.append(f'in no knowledge base and not named out of reach: {", ".join(unassigned)} — assign each root to a part, or name it out of reach with its code line')
    return Part([*mine, *unassigned], lines, declared, roots[kb_id])


def to_contract_ids(entries: list[dict] | None, part_roots: list[PurePosixPath], scan: list[PurePosixPath]) -> list[dict] | None:
    """The ingest's own ``not_ingested`` / ``partially_ingested`` paths, renamed from the part's naming to the contract's."""
    if entries is None:
        return None
    return [{**e, 'path': (f and _to_id(f, scan)) or e['path']} for e in entries for f in [_to_file(str(e['path']), part_roots)]]


#: Both corpus gates run on every call, so an overrule of either that matches nothing now is stale.
EVALUATED = (gate_ledger.CORPUS_SHORTFALL, gate_ledger.CORPUS_SOURCE_NOT_INGESTED)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--chunks', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--scope', type=Path, required=True, help="$WORKDIR/.corpus_scope; a missing file declares no units")
    parser.add_argument('--art', type=Path, required=True, help="the workdir's artifacts/ directory ($ART), where the overrule ledger lives")
    parser.add_argument('--findings-out', type=Path, default=None, help='write every gate finding this run minted, as JSON, to this path')
    parser.add_argument('--partition', type=Path, default=None, help="$ART/corpus_roots.json: this call checks ONE part of it")
    parser.add_argument('--kb-id', default=None, help='the part (knowledge base) this ingest built')
    parser.add_argument('--scan-roots', type=Path, default=None, help='$WORKDIR/.scan_corpus_roots, the union scope')
    args = parser.parse_args(argv)
    partitioned = (args.partition, args.kb_id, args.scan_roots)
    if any(partitioned) and not all(partitioned):
        print('reconcile_corpus_scope: --partition, --kb-id and --scan-roots go together.', file=sys.stderr)
        return 2
    try:
        contract = json.loads(args.contract.read_text(encoding='utf-8'))
        chunks = json.loads(args.chunks.read_text(encoding='utf-8'))
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        inventory = [str(f['path']) for f in contract['source_inventory']['files']]
        raw_not_ingested = manifest.get('not_ingested')
        not_ingested = None if raw_not_ingested is None else [{'path': str(e['path']), 'reason': str(e.get('reason') or '')} for e in raw_not_ingested]
        raw_partial = manifest.get('partially_ingested')
        partially_ingested = (
            None
            if raw_partial is None
            else [
                {'path': str(e['path']), 'reason': str(e.get('reason') or ''), 'pages': [int(n) for n in e['pages']], 'of': int(e['of'])}
                for e in raw_partial
            ]
        )
        staged_suffixes = manifest.get('staged_suffixes')
        if staged_suffixes is not None and not isinstance(staged_suffixes, list):
            raise TypeError(f'staged_suffixes must be a list, got {type(staged_suffixes).__name__}')
        source_kind = manifest.get('source_kind')
        declared = declared_units(args.scope.read_text(encoding='utf-8') if args.scope.exists() else None)
        part = None
        if args.partition:
            scan = [_norm(r) for r in args.scan_roots.read_text(encoding='utf-8').splitlines() if r.strip()]
            part = read_part(json.loads(args.partition.read_text(encoding='utf-8')), args.kb_id, scan, inventory, declared)
            inventory, declared = part.inventory, part.declared
            not_ingested = to_contract_ids(not_ingested, part.roots, scan)
            partially_ingested = to_contract_ids(partially_ingested, part.roots, scan)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f'reconcile_corpus_scope: an input could not be read ({type(exc).__name__}: {exc}).', file=sys.stderr)
        return 2
    chunks = [c for c in chunks if isinstance(c, dict)]
    names = reconcile(inventory, chunks, not_ingested, staged_suffixes=staged_suffixes, partially_ingested=partially_ingested)
    landed = landed_units(source_kind, chunks, not_ingested)
    findings = corpus_findings(
        subject=f'{source_kind or "unknown"} corpus' + (f' {args.kb_id}' if part else ''), declared=declared, landed=landed, not_ingested=not_ingested, inventory=inventory, names=names
    )
    try:
        applied, ledger_lines = gate_ledger.settle(findings, art=args.art, evaluated=EVALUATED, findings_out=args.findings_out)
    except (OSError, ValueError) as exc:
        print(f'reconcile_corpus_scope: the gate ledger could not be read or the findings written ({type(exc).__name__}: {exc}).', file=sys.stderr)
        return 2
    basis = 'source document(s)' if source_kind == 'directory' else 'chunk(s)'
    if declared is None:
        count = f'no declared_units recorded in {args.scope}: there is no declaration to compare {landed} landed {basis} against.'
    elif shortfall(declared, landed):
        count = f'shortfall: {landed} of {declared} declared unit(s) landed as {basis}, more than {SHORTFALL_TOLERANCE:.0%} short.'
    else:
        count = f'landed {landed} {basis} against {declared} declared unit(s).'
    out = sys.stderr if applied.holding else sys.stdout
    for line in [count, *(part.lines if part else []), *names.lines, *ledger_lines]:
        print(line, file=out)
    return 1 if applied.holding else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
