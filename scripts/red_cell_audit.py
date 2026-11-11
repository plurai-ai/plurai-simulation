#!/usr/bin/env python3
"""One typed red-cell audit per run: ``<run dir>/red_cell_audit.json`` (schema ``plurai-red-cell-audit/1``, C5).

    red_cell_audit.py save --run-dir "$OUT" --stage qualification|final --rows <rows.json | ->
    red_cell_audit.py show --run-dir "$OUT"
    red_cell_audit.py env-rows --run-dir "$OUT"
    red_cell_audit.py eval-brief --run-dir "$OUT"
    red_cell_audit.py upload-args --run-dir "$OUT" --run-upload-id <id> [--out <args.json>]
    red_cell_audit.py uploaded --run-dir "$OUT" --receipt <upload_red_cell_audit result | ->
    red_cell_audit.py state --run-dir "$OUT"

``save`` validates the auditor's rows (a JSON list, ``red_cells.py``'s AUDIT ROWS grammar) and APPENDS them to the run's
file — ``{schema, run, stage, rows}``; a later row wins its unit, so a delta audit is the same file. A ``FALSE RED`` / ``FALSE PASS``
row with no ``quote`` is refused (:data:`QUOTE_ANCHORS`): it could never be corrected. A ``TRUE RED`` / ``FALSE PASS``
with no ``reason`` is saved with a WARNING (:data:`REASONED`; a ``note`` is read as its ``reason``). A run with nothing to
rule saves ``[]``: an audit that ruled nothing is still an audit. The audit makes a final run the scored one
(``runs_index.py``), so ``save`` rebuilds the workdir's ``runs/index.json`` when one exists. ``show`` prints the path and
row count, or ``audit missing: <path>`` (exit 3).

ENV ROWS. An ``ENV`` row names a tool answer the declared world contradicts: ``tool``, ``event`` (its index in the item's
``tool_events.json``), ``returned`` (the disputed answer, quoted from that event's response), ``expected`` (what the
declared rules give instead). ``save`` refuses one whose ``expected`` equals its ``returned`` — a correct answer is no
env defect — unless it names ``missing: <type.field>``, the declared field no seeding could set; ``forced: true``
requires ``missing``. A ``returned`` the event's response does not contain is refused too. ``env-rows`` prints the
file's ENV rows (the last per item, tool and event) — the ONE brief an ``/edit-env`` repair is opened with, so an env
observation that is not a row of this file reaches no repair. ``show`` counts the rows by verdict.

UPLOAD. The backend reads a run's audit off its run upload (``upload_red_cell_audit(run_upload_id, audit)``), in its own
row names: ``upload-args`` prints that call's arguments — the file's envelope with every row projected by
:func:`backend_rows` (``turn`` → one ``turn_idx`` row per turn, the verdict's underscored token, ``defect_surface`` →
``cause``, the item as the backend names it, a scenario-scoped cell as ``turn_idx: null``; the last row of a unit wins,
and a verdict the backend has no token for, or a row naming no cell of the run, is said on stderr and left out). It is
the ONE converter: every upload of a run's audit goes through it, and ``uploaded``'s receipt check counts its rows. ``uploaded`` records the call's receipt beside the file (:data:`UPLOAD_FILENAME`, with the
sha256 of the file it uploaded); ``state`` prints :func:`state`: ``uploaded`` (the receipt is of the file as it stands),
``not_uploaded`` (no receipt, or one of an earlier version) or ``audit missing: <path>`` (exit 3).

THE ONE NAME. Every writer and reader takes the file from :func:`path`; a reader that finds none says
:data:`MISSING` — never a silent "no ruling". Two names for one file let a reader skip a run's audit unseen.

STAMPED ON SAVE (:func:`stamp`). Each policy row gets the ``policy_fingerprint`` of the cell it judged — the backend
corrects a FALSE RED only on a cell at that fingerprint, never on a re-score of an edited policy — and the ``thread_id`` of
the item's attempt it judged (its transcript ``metadata.thread_id``, unless the row names one): the backend applies a
ruling only to that attempt, so a re-run of the item never inherits it (it is listed ``audit_stale``). Each TRUE / FALSE
RED gets every clause the cell's findings charge added to its ``owner_clauses`` (said on stderr); a row naming another
fingerprint than its cell's is refused. A ``code_evidence`` fact (``{path, lines, quote}``) is checked against the agent's
file: lines rewritten to where the quote is, a fact the file does not hold dropped (both said on stderr).

EVAL BRIEF. ``eval-brief`` prints :func:`eval_brief`: per policy an ``/edit-evals`` round repairs, each eval-cause FALSE
RED / FALSE PASS with every clause and code fact, and the TRUE REDs to keep — the ONE brief that round is opened with.

CONSISTENT ACROSS RUNS. ``save`` refuses (exit 5, nothing written) a row that rules a cell the opposite way from an
earlier run's audit on the identical cell (same item, policy, turn and eval revision): the same pattern gets the same
ruling, or the row names what differs (``differs``).

Exit codes: 0 ok · 2 usage or unreadable input · 3 the run has no audit · 4 (``uploaded``) the receipt does not match
the audit as it stands · 5 (``save``) a row contradicts an earlier run's ruling on the identical cell.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Run as a script (`python -I` too) or loaded by path: the sibling `red_cells` resolves from this directory either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCHEMA = 'plurai-red-cell-audit/1'
FILENAME = 'red_cell_audit.json'
STAGES = ('qualification', 'final')
MISSING = 'audit missing'
#: The fields every row carries (the rest are the verdict's own: ``defect_surface``, ``quote``, ``corrected``, …).
ROW_KEYS = ('item', 'verdict')
#: EVERY rubric clause label a TRUE / FALSE RED rests on (a list): a red charged on two clauses names both, so the repair
#: answers both. On a FALSE RED ``save`` adds each clause the judged cell's findings charge that the row left out (:func:`stamp`).
OWNER_CLAUSES = 'owner_clauses'
#: The one-clause key this list replaced: ``save`` writes it as ``[it]``, and readers of an older row do the same.
LEGACY_OWNER_CLAUSE = 'owner_clause'
#: The verdicts whose ``reason`` names the agent's defect — what it did wrong, against which record or rule. The finding
#: reader decides which defect a violation shows from it (``scored_findings.py to-read``), so ``save`` warns on one
#: without it and never refuses it; an auditor's ``note`` is read as its ``reason``.
REASONED = ('TRUE RED', 'FALSE PASS')
LEGACY_REASON = 'note'
#: Product-code facts a ruling rests on: ``[{path, lines, quote}]`` — ``quote`` verbatim from lines ``lines`` (``N`` or
#: ``N-M``) of ``path`` in the agent's repo (resolved from the run dir upward). ``save`` rewrites ``lines`` to where the
#: quote is and drops a fact the file does not hold (:func:`checked_code`). They reach the eval edit session as grounded
#: product facts (the backend's ``inputs/audit_rulings.md``).
CODE_EVIDENCE = 'code_evidence'
#: The ``policy_fingerprint`` of the cell a row judged, always stamped by ``save`` from that cell: the backend applies a
#: FALSE RED's correction only to a cell at that fingerprint, never to a re-score of an edited policy.
POLICY_FINGERPRINT = 'policy_fingerprint'
#: The ``thread_id`` of the attempt a row judged — the item's transcript ``metadata.thread_id`` when it is saved, stamped
#: by ``save`` unless the row names one: the backend applies a ruling only to that attempt, so a re-run of the item (a new
#: thread, the same item name) never inherits the rulings of the attempt it replaced.
THREAD_ID = 'thread_id'
#: The eval causes an ``/edit-evals`` round repairs (:func:`eval_brief`); the others route to scenario, env or scan lanes.
EVAL_CAUSES = ('prompt', 'rubric', 'harness', 'type')
EXIT_MISSING, EXIT_RECEIPT, EXIT_INCONSISTENT = 3, 4, 5

#: The receipt of the run's last ``upload_red_cell_audit``, beside the audit it uploaded.
UPLOAD_FILENAME = 'red_cell_audit_upload.json'
UPLOAD_SCHEMA = 'plurai-red-cell-audit-upload/1'
#: :func:`state`'s answers.
UPLOADED, NOT_UPLOADED = 'uploaded', 'not_uploaded'
#: The audit row's verdict → the backend's token (C5). ``IN SCOPE`` (a dropped verdict the draft must keep) has none: it
#: rules an eval edit, not a cell.
BACKEND_VERDICTS = {'TRUE RED': 'TRUE_RED', 'FALSE RED': 'FALSE_RED', 'FALSE PASS': 'FALSE_PASS', 'CONFIRMED PASS': 'CONFIRMED_PASS',
                    'UNDECIDABLE': 'UNDECIDABLE', 'OUT OF SCOPE': 'OUT_OF_SCOPE'}


def verdict_of(row: Dict[str, Any]) -> str:
    """A row's verdict in its one spelling — upper case, one space between words — so ``TRUE_RED`` / ``true red`` read
    ``TRUE RED`` everywhere a row is read, never as no ruling."""
    return ' '.join(str(row.get('verdict') or '').replace('_', ' ').upper().split())


class AuditMissing(ValueError):
    """The run has no audit file — said, never read as an audit that ruled nothing."""


class AuditInconsistent(ValueError):
    """A new row rules a cell the other way from an earlier run's ruling on the identical cell (:func:`prior_conflicts`)."""


class IndexNotRefreshed(Exception):
    """The audit landed at ``where``; rebuilding the ``runs/index.json`` beside it then failed with ``cause``."""

    def __init__(self, where: Path, workdir: Path, cause: OSError) -> None:
        super().__init__(f'audit saved at {where}; runs/index.json not refreshed ({cause}); run runs_index.py write {workdir}')
        self.where = where


#: The decided rulings of one recorded state, in pairs that contradict each other.
OPPOSED = ({'TRUE RED', 'FALSE RED'}, {'CONFIRMED PASS', 'FALSE PASS'})
#: The row field that says what differs from an earlier run's opposite ruling on the identical cell.
DIFFERS = 'differs'
#: The row field of a TIE-BREAK ruling (``scored_findings.py contested``): a blind re-audit of a unit the audit upheld and
#: the reader judged no agent defect. Its ruling supersedes every earlier row of its unit (``verdict_corrections``).
TIE_BREAK = 'tie_break'


def path(run_dir: Path) -> Path:
    return Path(run_dir) / FILENAME


def unwrap(doc: Any, source: object) -> List[Dict[str, Any]]:
    """The rows of a typed audit file, or of the auditor's JSON list of rows; ValueError on anything else."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict) and doc.get('schema') == SCHEMA and isinstance(doc.get('rows'), list):
        return [r for r in doc['rows'] if isinstance(r, dict)]
    raise ValueError(f'{source} is neither a {SCHEMA} file nor a JSON list of audit rows')


def load(run_dir: Path) -> Dict[str, Any]:
    """The run's typed audit; :class:`AuditMissing` when there is none, ValueError when it is not typed."""
    where = path(run_dir)
    if not where.is_file():
        raise AuditMissing(f'{MISSING}: {where}')
    try:
        doc = json.loads(where.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'{where} could not be read ({exc})') from exc
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or not isinstance(doc.get('rows'), list):
        raise ValueError(f'{where} is not a {SCHEMA} file')
    return doc


def rows(run_dir: Path) -> List[Dict[str, Any]]:
    return unwrap(load(run_dir), path(run_dir))


#: The keys every ``ENV`` row carries (see ENV ROWS).
ENV_KEYS = ('tool', 'event', 'returned', 'expected')
_TYPE_FIELD = re.compile(r'[A-Za-z_][\w-]*\.[A-Za-z_][\w.-]*')


def _flat(value: Any) -> str:
    """A value as comparable text: JSON-dumped when structured, quotes and JSON punctuation dropped, whitespace collapsed,
    case folded — so `"is_active": false` and `is_active false` compare equal."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return ' '.join(re.sub(r'["\'{}\[\],:]', ' ', text).split()).casefold()


def _event_response(run: Any, item: str, event: int) -> Any:
    """The response of the item's ``tool_events.json`` event, or None when the run cannot show it."""
    import red_cells  # noqa: PLC0415 - red_cells imports this module at load

    d = run.dirs.get(red_cells.normalize_item(item)) if run is not None else None
    try:
        events = json.loads((d / 'tool_events.json').read_text(encoding='utf-8')).get('events') if d is not None else None
    except (OSError, ValueError, AttributeError):
        return None
    return events[event].get('response') if isinstance(events, list) and 0 <= event < len(events) and isinstance(events[event], dict) else None


def _env_problem(row: Dict[str, Any], run: Any) -> Optional[str]:
    if lacking := [k for k in ENV_KEYS if row.get(k) in (None, '')]:
        return f'an ENV row needs {" / ".join(lacking)}'
    event = row['event']
    if not isinstance(event, int) or isinstance(event, bool) or event < 0:
        return f'event {event!r} is not an index into tool_events.json'
    missing = row.get('missing')
    if missing is not None and not (isinstance(missing, str) and _TYPE_FIELD.fullmatch(missing.strip())):
        return f'missing {missing!r} is not <type.field>'
    if row.get('forced') is True and missing is None:
        return 'forced needs missing: <type.field> — the declared field no seeding could set'
    if missing is None and _flat(row['expected']) == _flat(row['returned']):
        return 'expected equals returned: the tool answered what the declared rules give (name missing: <type.field> if the world cannot hold the difference)'
    response = _event_response(run, str(row.get('item') or ''), event)
    if response is not None and _flat(row['returned']) not in _flat(response):
        return f'returned is not in event {event}\'s response'
    return None


_LINES = re.compile(r'(\d+)(?:\s*[-\u2013]\s*(\d+))?')


def _ws(text: str) -> str:
    return ' '.join(text.split())


def _source(run_dir: Optional[Path], rel: Path) -> Optional[Path]:
    """``rel`` under the nearest ancestor of the run dir that holds it — the agent's repo, wherever the bench nests it.
    Both the run dir's own path and its real one are walked, so a symlinked ``plurai_sim`` finds the repo it sits in."""
    if run_dir is None:
        return None
    seen: List[Path] = []
    for start in (Path(os.path.abspath(run_dir)), Path(os.path.realpath(run_dir))):
        seen.extend(a for a in start.parents if a not in seen)
    return next((a / rel for a in seen if (a / rel).is_file()), None)


def _where(lines: Sequence[str], quote: str) -> Optional[str]:
    """``N-M``: the lines of the file that hold ``quote``, when it is in the file at all."""
    first = next((q.strip() for q in quote.splitlines() if q.strip()), '')
    span = max(1, len([q for q in quote.splitlines() if q.strip()]))
    for i, line in enumerate(lines):
        if first and first in line:
            for end in range(i, min(len(lines), i + span + 20)):
                if _ws(quote) in _ws('\n'.join(lines[i:end + 1])):
                    return f'{i + 1}-{end + 1}'
    return None


def checked_code(facts: Any, run_dir: Optional[Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """``(facts, notes)``: a row's ``code_evidence`` as the agent's source holds it. Never refuses the save: a fact whose
    quote sits at other lines is kept with ``lines`` rewritten to where it is, and one that is malformed or whose quote the
    file does not hold is dropped — each said in a note."""
    kept: List[Dict[str, Any]] = []
    notes: List[str] = []
    for fact in facts if isinstance(facts, list) else [facts]:
        if not isinstance(fact, dict) or any(not str(fact.get(k) or '').strip() for k in ('path', 'lines', 'quote')):
            notes.append(f'{CODE_EVIDENCE} entry dropped: not {{path, lines, quote}}, all non-empty')
            continue
        rel = Path(str(fact['path']).strip())
        source = None if rel.is_absolute() or '..' in rel.parts else _source(run_dir, rel)
        if source is None:
            notes.append(f'{CODE_EVIDENCE} {str(rel)!r} dropped: no file of the agent\'s repo (a relative path, looked up from the run dir)')
            continue
        lines = source.read_text(encoding='utf-8', errors='replace').splitlines()
        span = _LINES.fullmatch(str(fact['lines']).strip())
        start, end = (int(span.group(1)), int(span.group(2) or span.group(1))) if span else (0, -1)
        quote = str(fact['quote'])
        if 1 <= start <= end and _ws(quote) in _ws('\n'.join(lines[start - 1:end])):
            kept.append({**fact, 'lines': f'{start}-{end}' if end > start else str(start)})
        elif where := _where(lines, quote):
            kept.append({**fact, 'lines': where})
            notes.append(f'{CODE_EVIDENCE} {rel}: the quote is at lines {where}, not {fact["lines"]} — lines rewritten')
        else:
            notes.append(f'{CODE_EVIDENCE} {rel} dropped: the file does not hold the quote')
    return kept, notes


#: The verdicts a final run corrects in place (``verdict_corrections.py``): each needs a ``quote`` its anchor holds, so
#: ``save`` refuses one without a quote — a correction that could never land, refused while the auditor can still quote.
CORRECTING = ('FALSE RED', 'FALSE PASS')
QUOTE_ANCHORS = ('Quote, verbatim, the agent\'s reply at the turn (`quote_turn` on a scenario-scoped cell), a file it wrote '
                 '(`quote_file`), a tool call it made (`<tool> <args JSON>`), or — a FALSE RED only — the tool answer its judge\'s '
                 'finding cites. `code_evidence` is not a correction anchor.')


def _problems(new: Sequence[Any], run_dir: Optional[Path] = None) -> List[str]:
    import red_cells  # noqa: PLC0415 - red_cells imports this module at load

    run = None
    if run_dir is not None and any(isinstance(r, dict) and str(r.get('verdict') or '').strip().upper() == 'ENV' for r in new):
        try:
            run = red_cells.Run(Path(run_dir))
        except ValueError:
            run = None
    out = []
    for i, row in enumerate(new):
        if not isinstance(row, dict):
            out.append(f'row {i} is not an object')
        elif missing := [k for k in ROW_KEYS if not row.get(k)]:
            out.append(f'row {i} has no {" / ".join(missing)}')
        elif row.get(OWNER_CLAUSES) is not None and not (
            isinstance(row[OWNER_CLAUSES], list) and row[OWNER_CLAUSES] and all(isinstance(c, str) and c.strip() for c in row[OWNER_CLAUSES])
        ):
            out.append(f'row {i}: {OWNER_CLAUSES} is a non-empty list of the rubric\'s clause labels (non-empty strings), or null')
        elif str(row['verdict']).strip().upper() == 'ENV' and (why := _env_problem(row, run)):
            out.append(f'row {i} ({row.get("item")}, {row.get("tool")} event {row.get("event")}): {why}')
        elif verdict_of(row) in CORRECTING and not str(row.get('quote') or '').strip():
            out.append(f'row {i} ({row.get("item")}, {row.get("policy")}): a {verdict_of(row)} is corrected only through its quote, '
                       f'and this row has none. {QUOTE_ANCHORS}')
    return out


def prior_conflicts(run_dir: Path, new: Sequence[Dict[str, Any]]) -> List[str]:
    """One line per new row that rules a cell the opposite way (:data:`OPPOSED`) from an earlier run's audit on the
    IDENTICAL cell — a sibling run under the same ``runs/``, the same item, policy and turn, scored at the same eval
    revision — unless the row names what differs (:data:`DIFFERS`). The same pattern gets the same ruling across runs."""
    import red_cells  # noqa: PLC0415 - the item naming and each run's current eval revision

    run_dir = Path(run_dir)
    here = red_cells.Run(run_dir)
    out: List[str] = []
    for other in sorted(p for p in run_dir.parent.iterdir() if p.is_dir() and p.resolve() != run_dir.resolve() and path(p).is_file()):
        try:
            earlier = rows(other)
        except ValueError:
            continue
        there = red_cells.Run(other)
        ruled: Dict[Tuple[str, str, Any], Dict[str, Any]] = {}
        for row in earlier:
            if row.get('policy') and isinstance(row.get('item'), str):
                ruled[(red_cells.normalize_item(row['item']), str(row['policy']), str(row.get('turn', row.get('turn_idx'))))] = row
        for row in new:
            # A tie-break (`TIE_BREAK`) is the deliberate re-ruling of its unit: never held to another run's ruling.
            if str(row.get(DIFFERS) or '').strip() or row.get(TIE_BREAK) or not row.get('policy') or not isinstance(row.get('item'), str):
                continue
            item = red_cells.normalize_item(row['item'])
            prior = ruled.get((item, str(row['policy']), str(row.get('turn', row.get('turn_idx')))))
            if prior is None or {verdict_of(row), verdict_of(prior)} not in OPPOSED:
                continue
            revision = here.revision(item)
            if revision is None or revision != there.revision(item):
                continue
            out.append(f'{item} {row["policy"]}: ruled {verdict_of(row)} here, {verdict_of(prior)} on the identical cell in {other.name} '
                       f'at eval revision {revision} ({" ".join(str(prior.get("reason") or "").split())[:160]}) — rule it the same, '
                       f'or name what differs in `{DIFFERS}`')
    return out


#: A rubric clause label as a judge's finding cites it: ``FAIL 4``, ``FAIL-C``, ``FAIL A`` (a separator is required).
_CLAUSE = re.compile(r'\bFAIL[ -](\d+[a-z]?|[A-Z])\b')


def _clause_key(label: str) -> str:
    return re.sub(r'[\s-]', '', label).upper()


def charged_clauses(findings: Any) -> List[str]:
    """The clause labels a cell's findings charge, in order, once each (``FAIL 4``, ``FAIL 3``). Read off each finding's
    ``why`` only — never its ``quote``, which is the agent's or the user's text — from a list, or from its stored repr."""
    if isinstance(findings, str):
        try:
            findings = ast.literal_eval(findings)
        except (ValueError, SyntaxError):
            return []
    out: Dict[str, str] = {}
    for finding in findings if isinstance(findings, list) else []:
        for match in _CLAUSE.finditer(str(finding.get('why') or '') if isinstance(finding, dict) else ''):
            out.setdefault(_clause_key(match.group(0)), f'FAIL {match.group(1)}')
    return list(out.values())


def thread_of(transcript: Any) -> Optional[str]:
    """A transcript's ``metadata.thread_id`` — its attempt — or None when it names none."""
    metadata = transcript.get('metadata') if isinstance(transcript, dict) else None
    thread = metadata.get('thread_id') if isinstance(metadata, dict) else None
    return thread.strip() if isinstance(thread, str) and thread.strip() else None


def stamp(run_dir: Path, new: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """``(rows, notes)``: each item-bearing row (an ENV row too) with the item's attempt ``thread_id`` (a row naming
    another keeps it, said in a note), each policy row with the judged cell's ``policy_fingerprint`` (ValueError when the
    row names another), a one-clause
    ``owner_clause`` as ``owner_clauses: [it]``, each FALSE RED with every clause the cell's
    findings charge on its units added to ``owner_clauses``, and ``code_evidence`` as the agent's source holds it
    (:func:`checked_code`) — each change said in a note. The cell is the run's CURRENT verdict, the one the auditor read."""
    import red_cells  # noqa: PLC0415 - red_cells imports this module at load

    run = red_cells.Run(Path(run_dir))
    rows: List[Dict[str, Any]] = []
    notes: List[str] = []
    mismatched: List[str] = []
    for i, row in enumerate(new):
        row = dict(row)
        rows.append(row)
        if LEGACY_OWNER_CLAUSE in row:
            legacy = row.pop(LEGACY_OWNER_CLAUSE)
            if isinstance(legacy, str) and legacy.strip() and not row.get(OWNER_CLAUSES):
                row[OWNER_CLAUSES] = [legacy.strip()]
        if str(row.get(LEGACY_REASON) or '').strip() and not str(row.get('reason') or '').strip():
            row['reason'] = row.pop(LEGACY_REASON)
        if row.get(CODE_EVIDENCE) is not None:
            facts, said = checked_code(row[CODE_EVIDENCE], run_dir)
            notes.extend(f'row {i} ({row.get("item")}, {row.get("policy")}): {n}' for n in said)
            if facts:
                row[CODE_EVIDENCE] = facts
            else:
                row.pop(CODE_EVIDENCE)
        if not isinstance(row.get('item'), str):
            continue
        item, policy = red_cells.normalize_item(row['item']), str(row.get('policy') or row.get('verdict'))
        # Every item-bearing row, an ENV row too: a ruling binds the attempt it read, never a re-run of the item.
        if item in run.dirs and (attempt := thread_of(run.transcript(item))):
            if not row.get(THREAD_ID):
                row[THREAD_ID] = attempt
            elif row[THREAD_ID] != attempt:
                notes.append(f'row {i} ({item}, {policy}): {THREAD_ID} {row[THREAD_ID]!r} is not the item\'s current attempt ({attempt}) — '
                             'kept: the ruling applies only to the attempt it names')
        if not row.get('policy'):
            continue
        cell = run.cells(item)[0].get(policy) if item in run.dirs else None
        if not isinstance(cell, dict):
            continue
        judged = cell.get(POLICY_FINGERPRINT) if isinstance(cell.get(POLICY_FINGERPRINT), str) and cell.get(POLICY_FINGERPRINT) else None
        if judged is not None:
            if row.get(POLICY_FINGERPRINT) not in (None, '', judged):
                mismatched.append(f'row {i} ({item}, {policy}): {POLICY_FINGERPRINT} {row[POLICY_FINGERPRINT]!r} is not the judged cell\'s ({judged})')
            row[POLICY_FINGERPRINT] = judged
        if verdict_of(row) != 'FALSE RED':
            continue
        turns, _why = red_cells._row_turns(run, item, policy, row.get('turn', row.get('turn_idx')))  # noqa: SLF001 - the one unit rule
        units = dict(red_cells.units(cell))
        findings = [(units.get(t) or {}).get('findings') or (cell.get('findings') if t is None else None) for t in (turns or [None])]
        named = list(row.get(OWNER_CLAUSES) or [])
        charged = list(dict.fromkeys(c for f in findings for c in charged_clauses(f)))
        added = [c for c in charged if _clause_key(c) not in {_clause_key(n) for n in named}]
        if added:
            row[OWNER_CLAUSES] = [*named, *added]
            notes.append(f'row {i} ({item}, {policy}): the judged cell also charges {", ".join(added)} — added to {OWNER_CLAUSES}')
    if mismatched:
        raise ValueError('; '.join(mismatched))
    return rows, notes


def save(run_dir: Path, stage: str, new: Sequence[Any]) -> Path:
    """Append ``new`` to the run's audit (created on the first save), rebuild an existing ``runs/index.json``, and return
    the audit's path; :class:`AuditInconsistent` (nothing written) when a row contradicts an earlier run's ruling on the
    identical cell (:func:`prior_conflicts`); :class:`IndexNotRefreshed` (the audit written) when the rebuild fails. Each
    row is stamped first (:func:`stamp`; its notes go to stderr)."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ValueError(f'{run_dir} is not a run directory')
    if stage not in STAGES:
        raise ValueError(f'stage {stage!r} is none of {", ".join(STAGES)}')
    if problems := _problems(new, run_dir):
        raise ValueError('; '.join(problems))
    new, notes = stamp(run_dir, new)
    for note in notes:
        print(f'red_cell_audit: {note}', file=sys.stderr)
    unreasoned = [f'{r.get("item")} {r.get("policy")}' for r in new if verdict_of(r) in REASONED and not str(r.get('reason') or '').strip()]
    if unreasoned:
        print(f'red_cell_audit: WARNING — {len(unreasoned)} TRUE RED / FALSE PASS row(s) carry no `reason` (what the agent did wrong, against '
              f'which record or rule): {"; ".join(unreasoned[:5])}{" …" if len(unreasoned) > 5 else ""}', file=sys.stderr)
    if conflicts := prior_conflicts(run_dir, new):
        raise AuditInconsistent('; '.join(conflicts))
    try:
        earlier = load(run_dir)['rows']
    except AuditMissing:
        earlier = []
    doc = {'schema': SCHEMA, 'run': run_dir.resolve().name, 'stage': stage, 'rows': [*earlier, *new]}
    where = path(run_dir)
    tmp = where.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(where)
    import runs_index  # noqa: PLC0415 - sibling script: runs_index imports this module

    index = run_dir.resolve().parent / runs_index.INDEX
    if index.is_file():  # the audit decides `scored_run`: the index that names it is rebuilt with it (none is created)
        try:
            runs_index.write_index(index, runs_index.build_index(index.parents[2], {}))
        except OSError as exc:
            raise IndexNotRefreshed(where, index.parents[2], exc) from exc
    return where


def env_rows(run_dir: Path) -> List[Dict[str, Any]]:
    """The audit's ``ENV`` rows, the last per (item, tool, event) — never one of an attempt its item was re-run since
    (``red_cells.superseded``): the env repair that re-ran it already answered that world fault."""
    import red_cells  # noqa: PLC0415 - red_cells imports this module at load

    found: Dict[Tuple[str, str, Any], Dict[str, Any]] = {}
    audit = rows(run_dir)
    if any(r.get(THREAD_ID) for r in audit):
        audit = red_cells.current_rows(red_cells.Run(Path(run_dir)), audit)
    for row in audit:
        if str(row.get('verdict') or '').strip().upper() == 'ENV':
            key = (str(row.get('item')), str(row.get('tool')), row.get('event'))
            found.pop(key, None)
            found[key] = row
    return list(found.values())


#: The row fields an eval-repair brief carries, in this order.
BRIEF_FIELDS = ('item', 'turn', 'verdict', 'defect_surface', OWNER_CLAUSES, 'quote', 'quote_turn', 'reason', CODE_EVIDENCE)


def _brief_row(row: Dict[str, Any], run: Any) -> Dict[str, Any]:
    """One ruling as the brief carries it; ``policy_since_edited`` when the cell's current verdict carries another
    ``policy_fingerprint`` than the one the ruling judged."""
    if not row.get(OWNER_CLAUSES) and isinstance(row.get(LEGACY_OWNER_CLAUSE), str) and row[LEGACY_OWNER_CLAUSE].strip():
        row = {**row, OWNER_CLAUSES: [row[LEGACY_OWNER_CLAUSE].strip()]}  # a row saved before the list
    out = {k: row[k] for k in BRIEF_FIELDS if row.get(k) not in (None, '', [])}
    import red_cells  # noqa: PLC0415 - red_cells imports this module at load

    item = red_cells.normalize_item(str(row['item']))
    cell = run.cells(item)[0].get(str(row['policy'])) if item in run.dirs else None
    now = cell.get(POLICY_FINGERPRINT) if isinstance(cell, dict) else None
    if row.get(POLICY_FINGERPRINT) and now and now != row[POLICY_FINGERPRINT]:
        out['policy_since_edited'] = True
    return out


def eval_brief(run_dir: Path, stage: Optional[str] = None) -> Dict[str, Any]:
    """THE ``/edit-evals`` brief of the run's audit: ``{run, prompt_only, policies: [{policy, repair, keep_red}]}``.

    One entry per policy with a ``FALSE RED`` / ``FALSE PASS`` of an eval cause (:data:`EVAL_CAUSES`), on the last row of
    each unit: ``repair`` lists each such ruling with EVERY clause it rests on and its product-code facts, ``keep_red``
    the policy's ``TRUE RED`` rulings the draft must keep; a ruling of a cell whose policy was edited since is marked
    ``policy_since_edited``. ``prompt_only`` names the policies whose rulings are all
    ``prompt`` FALSE REDs — the session's ``prompt_only`` argument. A brief that dropped a clause is how a two-clause red
    was repaired on one clause and stayed red. Before the final run (``stage='qualification'``, unless
    ``eval_rounds.PREFINAL_PROMPT_ROUNDS_ENV`` is ``1``) a ``prompt_only`` policy is left out of the brief and named
    under ``labels_only``: its rows upload as labels and it gets no edit."""
    import eval_rounds  # noqa: PLC0415 - eval_rounds imports this module at load
    import red_cells  # noqa: PLC0415 - the one item naming; red_cells imports this module at load

    run = red_cells.Run(Path(run_dir))
    last: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows(run_dir):
        if row.get('policy') and isinstance(row.get('item'), str):
            key = (red_cells.normalize_item(row['item']), str(row['policy']), json.dumps(row.get('turn', row.get('turn_idx'))))
            last.pop(key, None)
            last[key] = row
    by_policy: Dict[str, List[Dict[str, Any]]] = {}
    for row in last.values():
        by_policy.setdefault(str(row['policy']), []).append(row)
    policies, prompt_only, labels_only = [], [], []
    for policy, ruled in sorted(by_policy.items()):
        repair = [r for r in ruled if verdict_of(r) in ('FALSE RED', 'FALSE PASS') and str(r.get('defect_surface') or '').strip().lower() in EVAL_CAUSES]
        if not repair:
            continue
        if all(verdict_of(r) == 'FALSE RED' and str(r.get('defect_surface')).strip().lower() == 'prompt' for r in repair):
            if eval_rounds.skips_prompt_only(stage):
                labels_only.append(policy)
                continue
            prompt_only.append(policy)
        policies.append({'policy': policy, 'repair': [_brief_row(r, run) for r in repair], 'keep_red': [_brief_row(r, run) for r in ruled if verdict_of(r) == 'TRUE RED']})
    out: Dict[str, Any] = {'run': Path(run_dir).resolve().name, 'prompt_only': prompt_only, 'policies': policies}
    return {**out, 'labels_only': labels_only} if labels_only else out


def by_verdict(run_dir: Path) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in rows(run_dir):
        verdict = str(row.get('verdict') or '').strip().upper()
        out[verdict] = out.get(verdict, 0) + 1
    return dict(sorted(out.items()))


def _turns(value: Any) -> Optional[List[Optional[int]]]:
    if value is None:
        return [None]
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return [value]
    if isinstance(value, list) and value and all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in value):
        return list(dict.fromkeys(value))
    return None


def backend_rows(rows: Sequence[Dict[str, Any]], run_dir: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """``(the rows as upload_red_cell_audit reads them, notes)`` — see UPLOAD; the ONE converter of the audit to the backend's
    rows. With ``run_dir`` each row is read against that run's cells (``red_cells.ruled_units``' unit rule): a scenario-scoped
    cell is one ``turn_idx: null`` row whatever turns the row names, and a row naming no cell of the run is left out. A
    later row wins its unit — one the backend has no verdict for (``IN SCOPE``) takes the unit out of the upload. A note
    names each row left out and each uploaded FALSE RED / FALSE PASS with no typed cause (``red_cells.CAUSES``): the
    backend neither discounts nor corrects on it. A unit is per ``policy_fingerprint`` too: a newer ruling of an edited
    policy's cell is uploaded BESIDE the older ruling of its earlier revision, never over it."""
    import red_cells  # noqa: PLC0415 - the one item naming; red_cells imports this module at load

    run = red_cells.Run(Path(run_dir)) if run_dir is not None else None
    #: A unit is ``(item, policy, turn, policy_fingerprint)``: a ruling of one revision of a cell never replaces a ruling of
    #: another — the backend applies each to the revision it judged.
    units: Dict[Tuple[str, str, Optional[int], Optional[str]], Dict[str, Any]] = {}
    notes: List[str] = []
    for i, row in enumerate(rows):
        where = f'row {i} ({row.get("item")}, {row.get("policy")})'
        if not row.get('policy') or not isinstance(row.get('item'), str):
            notes.append(f'{where}: no policy cell to rule')
            continue
        verdict = BACKEND_VERDICTS.get(verdict_of(row))
        item, policy = red_cells.normalize_item(row['item']), str(row['policy'])
        turn = row.get('turn', row.get('turn_idx'))
        if run is not None:
            turns, why = red_cells._row_turns(run, item, policy, turn)  # noqa: SLF001 - the one unit rule
        else:
            turns, why = _turns(turn), f'turn {turn!r} is not an int, a list of ints, or null'
        if turns is None:
            notes.append(f'{where}: {why}')
            continue
        fingerprint = row.get(POLICY_FINGERPRINT) if isinstance(row.get(POLICY_FINGERPRINT), str) and row.get(POLICY_FINGERPRINT) else None
        if verdict is None:
            dropped = [units.pop((item, policy, t, fingerprint), None) for t in turns]
            notes.append(f'{where}: no backend verdict for {row.get("verdict")!r}' + (' (it takes the unit out)' if any(dropped) else ''))
            continue
        cause = row.get('defect_surface', row.get('cause'))
        if verdict in ('FALSE_RED', 'FALSE_PASS') and str(cause or '').strip().lower() not in red_cells.CAUSES:
            notes.append(f'{where}: uploaded, but a {verdict} with no typed cause ({", ".join(red_cells.CAUSES)}) neither discounts nor corrects')
        extra = {k: v for k, v in row.items() if k not in ('item', 'policy', 'turn', 'turn_idx', 'verdict', 'defect_surface', 'cause', 'corrected')}
        for t in turns:
            units.pop((item, policy, t, fingerprint), None)  # the last row of a unit wins, and sits last
            units[(item, policy, t, fingerprint)] = {**extra, 'item': item, 'policy': policy, 'turn_idx': t, 'verdict': verdict,
                                        'cause': None if cause is None else str(cause).strip().lower(),
                                        'corrected': None if row.get('corrected') is None else str(row['corrected'])}
    return list(units.values()), notes


def upload_args(run_dir: Path, run_upload_id: str) -> Tuple[Dict[str, Any], List[str]]:
    """``({run_upload_id, audit}, notes)`` — the ``upload_red_cell_audit`` call for the run's audit as it stands."""
    if not run_upload_id.strip():
        raise ValueError('--run-upload-id is empty: the audit rides the run upload it rules')
    doc = load(run_dir)
    projected, notes = backend_rows(unwrap(doc, path(run_dir)), run_dir)
    return {'run_upload_id': run_upload_id.strip(), 'audit': {'schema': SCHEMA, 'run': doc.get('run') or Path(run_dir).resolve().name,
                                                              'stage': doc.get('stage'), 'rows': projected}}, notes


def sha(run_dir: Path) -> Optional[str]:
    """The sha256 of the run's audit file as it stands, None when it has none."""
    try:
        return hashlib.sha256(path(run_dir).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def record_upload(run_dir: Path, receipt: Any) -> Path:
    """Record ``upload_red_cell_audit``'s receipt beside the audit it uploaded; ValueError (nothing written) when the
    receipt is not one, or counts other rows than the audit projects now — an upload of an earlier version."""
    run_dir = Path(run_dir)
    load(run_dir)
    if not isinstance(receipt, dict) or not isinstance(receipt.get('run_upload_id'), str) or not isinstance(receipt.get('rows'), int):
        raise ValueError('the receipt is not an upload_red_cell_audit result ({run_upload_id, rows, true_red, replaced})')
    args, _ = upload_args(run_dir, receipt['run_upload_id'])
    if receipt['rows'] != len(args['audit']['rows']):
        raise ValueError(f'the receipt counts {receipt["rows"]} rows; the audit as it stands uploads {len(args["audit"]["rows"])} — '
                         're-run upload-args and upload again')
    where = Path(run_dir) / UPLOAD_FILENAME
    doc = {'schema': UPLOAD_SCHEMA, 'run_upload_id': receipt['run_upload_id'], 'audit_sha256': sha(run_dir), 'rows': receipt['rows'],
           'true_red': receipt.get('true_red'), 'recorded_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           # The backend's identity of the audit AS STORED (C8; null from an older backend), and the suite_diagnostics
           # reports whose `release_gate.audit_sha` it made stale — each owed `release_verdict_recompute`.
           'audit_sha': receipt.get('audit_sha') if isinstance(receipt.get('audit_sha'), str) else None,
           'stale_reports': [str(r) for r in receipt.get('stale_reports') or [] if isinstance(r, str) and r]}
    tmp = where.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
    tmp.replace(where)
    return where


def receipt_of(run_dir: Path) -> Dict[str, Any]:
    """The run's recorded upload receipt (:data:`UPLOAD_FILENAME`), ``{}`` when there is none or it does not parse."""
    try:
        doc = json.loads((Path(run_dir) / UPLOAD_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def state(run_dir: Path) -> str:
    """``uploaded`` when the recorded receipt is of the audit file as it stands, ``not_uploaded`` when there is none or
    the audit grew since, :data:`MISSING` when the run has no audit (an unreadable one raises ValueError)."""
    run_dir = Path(run_dir)
    try:
        load(run_dir)
    except AuditMissing:
        return MISSING
    try:
        receipt = json.loads((run_dir / UPLOAD_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return NOT_UPLOADED
    return UPLOADED if isinstance(receipt, dict) and receipt.get('audit_sha256') == sha(run_dir) else NOT_UPLOADED


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('save')
    p.add_argument('--run-dir', required=True, help='the run the rows rule ($OUT)')
    p.add_argument('--stage', required=True, choices=STAGES)
    p.add_argument('--rows', required=True, help="the auditor's rows: a JSON list, or '-' for stdin")
    p = sub.add_parser('show')
    p.add_argument('--run-dir', required=True)
    p = sub.add_parser('env-rows')
    p.add_argument('--run-dir', required=True)
    p = sub.add_parser('eval-brief')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--stage', choices=('qualification', 'final'), help='the audited run: qualification leaves prompt-only policies to labels')
    p = sub.add_parser('upload-args')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--run-upload-id', required=True, help="the run's upload (Step 4c's upload_run_artifacts)")
    p.add_argument('--out', help='write the arguments to this file (for mcp_call.py upload_red_cell_audit @<file>); default stdout')
    p = sub.add_parser('uploaded')
    p.add_argument('--run-dir', required=True)
    p.add_argument('--receipt', required=True, help="upload_red_cell_audit's result: a JSON file, or '-' for stdin")
    p = sub.add_parser('state')
    p.add_argument('--run-dir', required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'upload-args':
            call, left_out = upload_args(Path(args.run_dir), args.run_upload_id)
            for line in left_out:
                print(f'red_cell_audit: {line}', file=sys.stderr)
            if args.out:
                Path(args.out).write_text(json.dumps(call, ensure_ascii=False) + '\n', encoding='utf-8')
                print(json.dumps({'args': args.out, 'rows': len(call['audit']['rows']),
                                  'true_red': sum(r['verdict'] == 'TRUE_RED' for r in call['audit']['rows']), 'left_out': len(left_out)}))
            else:
                print(json.dumps(call, ensure_ascii=False))
            return 0
        if args.cmd == 'uploaded':
            raw = sys.stdin.read() if args.receipt == '-' else Path(args.receipt).read_text(encoding='utf-8')
            try:
                where = record_upload(Path(args.run_dir), json.loads(raw))
            except AuditMissing:
                raise
            except ValueError as exc:
                print(f'red_cell_audit: {exc}', file=sys.stderr)
                return EXIT_RECEIPT
            print(json.dumps({'receipt': str(where), 'state': state(Path(args.run_dir))}))
            import release_verdict  # noqa: PLC0415 - sibling script: the verdict this audit invalidates

            for report in json.loads(where.read_text(encoding='utf-8'))['stale_reports']:
                print(f'red_cell_audit: STALE VERDICT — suite_diagnostics {report} read another audit: release_verdict_recompute '
                      f'(suite_diagnostics_id {report}), poll it, then release_verdict.py render from its envelope', file=sys.stderr)
            if why := release_verdict.stale(Path(args.run_dir)):
                print(f'red_cell_audit: {why} — release_verdict_recompute the report it came from, then release_verdict.py render',
                      file=sys.stderr)
            return 0
        if args.cmd == 'state':
            said = state(Path(args.run_dir))
            print(f'{MISSING}: {path(Path(args.run_dir))}' if said == MISSING else said)
            return EXIT_MISSING if said == MISSING else 0
        if args.cmd == 'save':
            raw = sys.stdin.read() if args.rows == '-' else Path(args.rows).read_text(encoding='utf-8')
            new = json.loads(raw)
            if not isinstance(new, list):
                raise ValueError('--rows must be a JSON list of audit rows')
            try:
                where = save(Path(args.run_dir), args.stage, new)
            except IndexNotRefreshed as exc:  # the audit landed: a stale index is a follow-up, not a failed save
                print(f'red_cell_audit: {exc}', file=sys.stderr)
                where = exc.where
            print(json.dumps({'audit': str(where), 'added': len(new), 'rows': len(rows(Path(args.run_dir)))}))
            return 0
        if args.cmd == 'env-rows':
            print(json.dumps(env_rows(Path(args.run_dir)), indent=1, ensure_ascii=False))
            return 0
        if args.cmd == 'eval-brief':
            print(json.dumps(eval_brief(Path(args.run_dir), args.stage), indent=1, ensure_ascii=False))
            return 0
        print(json.dumps({'audit': str(path(Path(args.run_dir))), 'rows': len(rows(Path(args.run_dir))), 'by_verdict': by_verdict(Path(args.run_dir))}))
        return 0
    except AuditMissing as exc:
        print(f'red_cell_audit: {exc}', file=sys.stderr)
        return EXIT_MISSING
    except AuditInconsistent as exc:
        print(f'red_cell_audit: INCONSISTENT — {exc}', file=sys.stderr)
        return EXIT_INCONSISTENT
    except (OSError, ValueError) as exc:
        print(f'red_cell_audit: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
