#!/usr/bin/env python3
"""Decide whether to send a follow-up revert after an eval or env edit commits, from the regression guard's report.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/guard_decide.py" <end_edit_session result.json|-> [--scope-rows <rows.json | run dir>]…

Who owns what (the plugin is the admin, the server reports):

* The backend ALWAYS retains and commits exactly the edit the plugin sent. Its guard never refuses, holds or withdraws
  an eval edit; it only REPORTS, on ``guard``, every recorded verdict on the linked run that the committed edit moved.
* This script only decides whether the plugin sends a FOLLOW-UP edit. Nothing here undoes anything on the server:
  a revert is a new, explicit client call (``revert_edited_policies``) that the backend commits and retains as its own
  revision, exactly like the edit before it.

From the report:

* REVERT — every policy in ``guard.silenced_true_reds`` (a red the linked audit ruled TRUE_RED that the committed
  edit no longer fails). The plugin follows up with ``revert_edited_policies(eval_set_id, session_id, policies)``
  (:data:`revert_call`), restoring those policies' pre-session bodies as a new committed revision.
* INTENDED — a ``dropped_verdicts`` / ``lost_reds`` item the audit ruled ``FALSE_RED`` or ``OUT_OF_SCOPE``: the
  narrowing the edit was for. Kept, labelled. So is a ``dropped_verdicts`` item the plugin's own scope triage ruled
  ``OUT OF SCOPE`` (``--scope-rows``: red-cell audit rows ``{item, policy, turn, verdict: IN SCOPE | OUT OF SCOPE}``, a
  rows file or a run dir's audit; never uploaded to the server's audit). Matched on ``(item, policy, turn)``, a null
  ``turn`` covering the whole cell, the last row winning; it carries ``scope_ruling``.
* NAMED RISK — every other ``dropped_verdicts`` / ``lost_reds`` / ``unevaluated`` item (judge variance, or a unit no
  audit ruled). Kept, internal, visible to the next audit. Never loop on one and never re-edit for one.

An item on a reverted policy is neither: the revert restores it. Nothing is decided on a result that shipped
nothing (``outcome`` other than ``committed``), or that carries no guard object.

Prints one JSON object: ``{outcome, guard_status, run_upload_id, revert, revert_call, intended, named_risks}``;
``revert_call`` is the tool's arguments, or null when nothing is reverted. Each kept item is the guard's own
``{item, policy, turn, fingerprint, now, audit_ruling}`` plus ``kind`` (``dropped_verdict`` | ``lost_red`` |
``unevaluated``). Internal: none of this is customer text.

An ENV result (``target_kind: env``) is read from its ``env_guard`` instead (:func:`decide_env`): the backend committed the
edit as sent and reports each check that passed before and does not now. RESTORE — every ``lost_witnesses`` item (a mock
witness test of a tool, passing before the edit, failing or gone after it): the plugin opens a new edit session with
``restore_request`` as its message, an explicit follow-up edit the backend commits like any other. NAMED RISK — every
``regressions`` / ``unfinished`` item, kept and internal. Prints ``{outcome, guard_status, restore, restore_request,
named_risks}``; each kept item is the guard's own plus ``guard_list`` (``regression`` | ``unfinished``).

Exit codes: 0 ok · 2 usage, unreadable input, or a body that is not a result object. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import red_cell_audit  # noqa: E402 - sibling script: the one audit file name and row shape

#: The audit rulings that make a moved verdict the edit's intended narrowing.
INTENDED_RULINGS = frozenset({'FALSE_RED', 'OUT_OF_SCOPE'})
#: ``guard`` list → the ``kind`` a kept item carries. Only the first two can be intended.
KEPT_LISTS = (('dropped_verdicts', 'dropped_verdict'), ('lost_reds', 'lost_red'), ('unevaluated', 'unevaluated'))
LABELLABLE = frozenset({'dropped_verdict', 'lost_red'})
SHIPPED = frozenset({'committed', 'promoted'})
SCOPE_VERDICTS = frozenset({'IN SCOPE', 'OUT OF SCOPE'})
OUT_OF_SCOPE = 'OUT OF SCOPE'
ScopeIndex = Dict[Tuple[str, str, Optional[int]], str]


def load_scope_rows(sources: Sequence[str]) -> List[Dict[str, Any]]:
    """The scope rows of each source: a run dir's audit (none when it has no audit), or a rows file."""
    rows: List[Dict[str, Any]] = []
    for source in sources:
        where = Path(source)
        if where.is_dir():
            try:
                rows += red_cell_audit.rows(where)
            except red_cell_audit.AuditMissing:
                continue
        else:
            rows += red_cell_audit.unwrap(json.loads(where.read_text(encoding='utf-8')), where)
    return [r for r in rows if str(r.get('verdict') or '').strip().upper().replace('_', ' ') in SCOPE_VERDICTS]


def scope_index(rows: Sequence[Dict[str, Any]]) -> ScopeIndex:
    """``{(item, policy, turn): verdict}``; a row naming several turns rules each, a null turn the whole cell."""
    index: ScopeIndex = {}
    for row in rows:
        turns = row.get('turn', row.get('turn_idx'))
        verdict = str(row.get('verdict')).strip().upper().replace('_', ' ')
        for turn in turns if isinstance(turns, list) else [turns]:
            index[(str(row.get('item') or '').strip().strip('/'), str(row.get('policy')), turn)] = verdict
    return index


def _scope(index: ScopeIndex, item: Dict[str, Any]) -> Optional[str]:
    key = (str(item.get('item')), str(item['policy']))
    return index.get((*key, item.get('turn'))) or index.get((*key, None))


def _items(guard: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    return [i for i in guard.get(key) or [] if isinstance(i, dict) and i.get('policy')]


def decide(result: Dict[str, Any], scope_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """The decision on one ``end_edit_session`` result, with the plugin's own scope rows (see the module docstring)."""
    guard = result.get('guard')
    outcome = result.get('outcome')
    out: Dict[str, Any] = {'outcome': outcome, 'guard_status': None, 'run_upload_id': None, 'revert': [], 'revert_call': None,
                           'intended': [], 'named_risks': []}
    if not isinstance(guard, dict) or outcome not in SHIPPED:
        return out
    out['guard_status'], out['run_upload_id'] = guard.get('status'), guard.get('run_upload_id')
    revert = sorted({str(i['policy']) for i in _items(guard, 'silenced_true_reds')})
    index = scope_index(scope_rows)
    for key, kind in KEPT_LISTS:
        for item in _items(guard, key):
            if item['policy'] in revert:
                continue
            kept = {**item, 'kind': kind}
            if kind in LABELLABLE and item.get('audit_ruling') in INTENDED_RULINGS:
                out['intended'].append(kept)
            elif kind == 'dropped_verdict' and _scope(index, item) == OUT_OF_SCOPE:
                out['intended'].append({**kept, 'scope_ruling': OUT_OF_SCOPE})
            else:
                out['named_risks'].append(kept)
    out['revert'] = revert
    if revert:
        out['revert_call'] = {'eval_set_id': result.get('target_id'), 'session_id': result.get('session_id'), 'policies': revert}
    return out


#: ``env_guard`` lists → the ``guard_list`` a kept item carries. ``lost_witnesses`` is restored, never kept.
ENV_KEPT_LISTS = (('regressions', 'regression'), ('unfinished', 'unfinished'))
#: The message of the follow-up env edit that restores lost witnesses; one evidence line per lost test follows it.
RESTORE_REQUEST = (
    'Restore what the last edit of this env lost: each mock witness test below passed on the env before that edit and '
    'fails now. Make each pass again and keep the rest of that edit; never change a test to make it pass.'
)


def decide_env(result: Dict[str, Any], scope_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """The decision on one env ``end_edit_session`` result (see the module docstring). ``scope_rows`` is unused: an env
    regression has no audit or scope ruling."""
    del scope_rows
    guard, outcome = result.get('env_guard'), result.get('outcome')
    out: Dict[str, Any] = {'outcome': outcome, 'guard_status': None, 'restore': [], 'restore_request': None, 'named_risks': []}
    if not isinstance(guard, dict) or outcome not in SHIPPED:
        return out
    out['guard_status'] = guard.get('status')
    lost = [i for i in guard.get('lost_witnesses') or [] if isinstance(i, dict) and i.get('id')]
    out['restore'] = sorted({str(i['id']) for i in lost})
    if lost:
        lines = [f"- [{i['id']}] ({', '.join(i.get('tools') or []) or 'no tool named'}; {i.get('cause')}): {i.get('detail') or ''}" for i in lost]
        out['restore_request'] = '\n'.join([RESTORE_REQUEST, *lines])
    for key, label in ENV_KEPT_LISTS:
        out['named_risks'] += [{**i, 'guard_list': label} for i in guard.get(key) or [] if isinstance(i, dict) and i.get('id')]
    return out


#: ``target_kind`` → the decision on that kind's guard. A result with no kind (or another) is read as an eval result.
DECIDERS = {'evals': decide, 'env': decide_env}


def decide_any(result: Dict[str, Any], scope_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """The decision on one ``end_edit_session`` result of any kind, dispatched on its ``target_kind``."""
    return DECIDERS.get(str(result.get('target_kind') or ''), decide)(result, scope_rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n\n', 1)[0])
    ap.add_argument('result', help="end_edit_session's result: a JSON file, or '-' for stdin")
    ap.add_argument('--scope-rows', action='append', default=[], help='a scope-rows file or an audited run dir (repeatable)')
    args = ap.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.result == '-' else Path(args.result).read_text(encoding='utf-8')
        body = json.loads(raw)
        scope_rows = load_scope_rows(args.scope_rows)
    except (OSError, ValueError) as exc:
        print(f'guard_decide: {exc}', file=sys.stderr)
        return 2
    if not isinstance(body, dict):
        print('guard_decide: not an end_edit_session result object', file=sys.stderr)
        return 2
    print(json.dumps(decide_any(body, scope_rows), indent=1))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
