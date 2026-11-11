#!/usr/bin/env python3
"""The one reader of a conversation's verdicts: ``<run dir>/<item>/verdicts.json`` (``docs/verdicts.md``).

One file per attempt, beside its ``transcript.json`` / ``error.json``: ``<run>/<item>/``, ``<run>/trial_<k>/<item>/``
and ``<run>/<item>/stability_retry/``. The SDK writes it (``plurai verdicts …``); a rescore and an audit correction
change it through the same writer and append to its ``history``. Every plugin reader reads verdicts through this module
and from nowhere else — never from the transcript, which records what happened and holds no verdicts.

Read-only and stdlib-only. There is no fallback: a run dir written before this file existed is refused by name, with
the one command that converts it (:data:`CONVERTER`).

    doc = verdicts_file.read(item_dir)          # raises VerdictsError (a ValueError) naming the converter
    verdicts_file.status(doc)['value']          # scored | agent_error | harness_error | incomplete | goal_undecided
    verdicts_file.evals(doc)                    # {eval: {state, reason, cell}}, every eval of the set when graded
    verdicts_file.cells(doc)                    # {eval: the backend cell}, the evals that carry one
    verdicts_file.rescored(doc)                 # history holds anything after the run's own entry
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

FILENAME = 'verdicts.json'
SCHEMA = 1
#: The conversation's status (precedence, strongest first): the writer decides it once; readers never re-derive it.
STATUSES = ('agent_error', 'harness_error', 'incomplete', 'goal_undecided', 'scored')
#: An eval's state. ``reason`` (closed per state) says which kind of not_applicable / ungraded it is.
STATES = ('pass', 'fail', 'not_applicable', 'ungraded')
#: ``history[].source`` of the run's own entry; any other source (rescore, correction, convert) is a later change.
RUN_SOURCE = 'run'
#: The command that turns a run dir from before this file into one that has it.
CONVERTER = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/convert_verdicts.py" <run dir>'


class VerdictsError(ValueError):
    """An item's ``verdicts.json`` is missing, unreadable, or of a schema this reader does not know."""


def path(item_dir: Path) -> Path:
    return Path(item_dir) / FILENAME


def exists(item_dir: Path) -> bool:
    return path(item_dir).is_file()


def read(item_dir: Path) -> Dict[str, Any]:
    """The item's verdicts. Raises :class:`VerdictsError` naming :data:`CONVERTER` when the file is missing, does not
    parse, or is not ``verdicts_schema`` :data:`SCHEMA` with a known status and a known state on every eval."""
    p = path(item_dir)
    if not p.is_file():
        raise VerdictsError(f'{p} is missing: this run was written before verdicts.json existed, or the runner did not '
                            f'finish this item. Convert an old run once with {CONVERTER}; re-run an unfinished one.')
    try:
        doc = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VerdictsError(f'{p} could not be read as JSON ({type(exc).__name__}: {exc}).') from exc
    if not isinstance(doc, dict) or doc.get('verdicts_schema') != SCHEMA:
        found = doc.get('verdicts_schema') if isinstance(doc, dict) else type(doc).__name__
        raise VerdictsError(f'{p} has verdicts_schema {found!r}; this reader knows {SCHEMA}. Convert with {CONVERTER}.')
    value = (doc.get('status') or {}).get('value') if isinstance(doc.get('status'), dict) else None
    if value not in STATUSES:
        raise VerdictsError(f'{p} has status {value!r}, outside {", ".join(STATUSES)}.')
    if not isinstance(doc.get('evals'), dict) or not isinstance(doc.get('graded'), bool):
        raise VerdictsError(f'{p} carries no `evals` map or no boolean `graded`.')
    for name, entry in doc['evals'].items():
        if not isinstance(entry, dict) or entry.get('state') not in STATES:
            state = entry.get('state') if isinstance(entry, dict) else type(entry).__name__
            raise VerdictsError(f'{p}: eval {name!r} has state {state!r}, outside {", ".join(STATES)}.')
    return doc


def status(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """``{value, cause, retry, cut_by}``. ``cause`` is ``{kind, transient, status?}`` (the runner's typed ``error.json``
    facts) or None; ``cut_by`` the harness's cut reason when it cut the dialog (kept under an outranking status too)."""
    block = doc.get('status') if isinstance(doc.get('status'), dict) else {}
    cause = block.get('cause')
    cut_by = block.get('cut_by')
    return {'value': block.get('value'), 'cause': cause if isinstance(cause, dict) else None, 'retry': block.get('retry') is True,
            'cut_by': cut_by if isinstance(cut_by, str) and cut_by else None}


def graded(doc: Mapping[str, Any]) -> bool:
    return doc.get('graded') is True


def evals(doc: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{eval: {state, reason, cell}}`` — every eval of the set on a graded file; ``{}`` on an error row."""
    return {str(name): entry for name, entry in (doc.get('evals') or {}).items() if isinstance(entry, dict)}


def cells(doc: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{eval: the backend cell}`` for the evals that carry a cell (an eval guard-skipped on every turn carries none)."""
    return {name: entry['cell'] for name, entry in evals(doc).items() if isinstance(entry.get('cell'), dict)}


def goal(doc: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The backend's ``scenario_verdict`` block, as the last writer event left it; None when there is none."""
    block = doc.get('goal')
    return block if isinstance(block, dict) else None


def judging(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """The judging blocks beside the cells (``measurement_integrity``, ``scenario_success_results``, ``evaluated_guards`` …)."""
    block = doc.get('judging')
    return dict(block) if isinstance(block, dict) else {}


def judged_against(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """``{eval_set_id, eval_revision, scenario_set_id, gt_fingerprint, gt_revision, kept_from_earlier_revision}``."""
    block = doc.get('judged_against')
    return dict(block) if isinstance(block, dict) else {}


def history(doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    entries = doc.get('history')
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def rescored(doc: Mapping[str, Any]) -> bool:
    """Whether anything changed this attempt's verdicts after the run judged them (a rescore, a correction, a conversion)."""
    return any(entry.get('source') != RUN_SOURCE for entry in history(doc))


def changes(doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Every recorded flip, oldest first, each with its entry's ``source``: a correction entry is one flip; a rescore or
    conversion entry lists its own under ``changes``."""
    out: List[Dict[str, Any]] = []
    for entry in history(doc):
        source = entry.get('source')
        if isinstance(entry.get('changes'), list):
            out.extend({**change, 'source': source} for change in entry['changes'] if isinstance(change, dict))
        elif 'eval' in entry and 'new' in entry:
            out.append(dict(entry))
    return out


def run_cells(doc: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{eval: cell}`` as the run judged it: each eval's first replaced cell in ``history``, else its current one."""
    found: Dict[str, Dict[str, Any]] = {}
    for entry in history(doc):
        for change in (entry.get('changes') if isinstance(entry.get('changes'), list) else [entry]):
            name, cell = (change.get('eval'), change.get('replaced_cell')) if isinstance(change, dict) else (None, None)
            if isinstance(name, str) and isinstance(cell, dict) and cell and name not in found:
                found[name] = cell
    return {**cells(doc), **found}


def latest_rescore(doc: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The newest ``source: rescore`` (or ``convert``) history entry, or None."""
    return next((e for e in reversed(history(doc)) if e.get('source') in ('rescore', 'convert')), None)
