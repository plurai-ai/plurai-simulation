#!/usr/bin/env python3
"""Convert a run written before ``verdicts.json`` existed: one ``plurai verdicts import-legacy`` per attempt folder.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/convert_verdicts.py" <run dir>

Every attempt folder of the run — ``<item>/``, ``trial_<k>/<item>/`` and ``<item>/stability_retry/`` — that holds no
``verdicts.json`` gets one, built from what the old run left: the transcript's own verdict block and, when it is FRESH
under the old rule (newer than the transcript, and no counted ``error.json`` beside it), the ``rescore.json`` sidecar
over it. ``history`` is ``[run, convert]`` (``[run]`` when no fresh sidecar). The SDK writes the file
(:mod:`verdict_events`); this script keeps the old reading rules, one last time, and nowhere else:

- a counted ``error.json`` (no transcript, or newer than it) is the attempt's status, and its transcript is not read;
- a fresh sidecar's cells replace the transcript's; a policy it reports guard-skipped (typed ``not_scored_kinds``, else
  the pre-typed prose) is ``not_applicable``; a policy any fresh sidecar of the run reports REMOVED (``changed[].new``
  null) leaves every attempt; its ``scenario_verdict`` and ``measurement_integrity`` replace the transcript's, and a
  sidecar that carries no ``scenario_verdict`` leaves the goal unknown (the transcript's is the pre-edit one);
- the run facts (``dialog_outcome``, ``user_sim_integrity`` …) are copied into the transcript's ``finalize_record``,
  where readers take them. Nothing is deleted: the transcript keeps its old block, the sidecar stays where it is.

IDEMPOTENT. A folder that already holds ``verdicts.json`` is skipped, and said so; running twice converts nothing twice.

The cell → (state, reason) and signals → status mappings below are the SDK writer's (``plurai/verdicts.py``), ported
because the plugin cannot import the SDK; ``scripts/tests/test_convert_verdicts.py`` holds them to the SDK's vectors when
the SDK is importable.

Exit codes: 0 converted (or nothing to convert) · 2 usage or unreadable input · 5 the writer refused a folder, or no
installed SDK offers ``plurai verdicts`` (the folders already written stay written; run it again after the fix).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _client_handshake  # noqa: E402 - the plugin's refusal exit
from verdicts_file import FILENAME, SCHEMA  # noqa: E402 - the one definition of the file's name and schema

#: The old run's files this converter reads, and the transcript key it is the last reader of.
TRANSCRIPT, ERROR, SIDECAR, TOOL_EVENTS = 'transcript.json', 'error.json', 'rescore.json', 'tool_events.json'
LEGACY_KEY = 'policy_evaluation_results'
RETRY_DIRNAME = 'stability_retry'
EXIT_USAGE, EXIT_WRITER = 2, 5

# ---------------------------------------------------------------------------------------------------------
# The SDK writer's mappings (plurai/verdicts.py), ported
# ---------------------------------------------------------------------------------------------------------

CELLS_KEY, VERDICT_KEY = 'evaluation_results', 'scenario_verdict'
NOT_APPLICABLE_REASONS = frozenset({'not_applicable', 'mixed_not_applicable', 'guard_skipped', 'turn_skipped_every_turn', 'deferred'})
INCONCLUSIVE_PREFIX = 'inconclusive:'
#: The /finalize keys that are conversation facts (the transcript's ``finalize_record``), not verdicts.
RECORD_KEYS = frozenset({'dialog_outcome', 'user_sim_integrity', 'scenario_dimensions', 'origin_basis', 'should_terminate', 'world_state_trustworthy'})
#: Derived from the cells, so never stored.
DERIVED_KEYS = frozenset({
    'aggregate_score', 'pass_rate', 'inconclusive_count', 'inconclusive_rate', 'not_applicable_count', 'not_measured_count',
    'deferred_count', 'conclusive_count', 'policies_evaluated', 'violated_policies', 'inconclusive_policies', 'errored_policies',
    'skipped_policies', 'not_applicable_policies', 'not_measured_policies', 'deferred_policies', 'high_confidence_violations',
    'blocking_violations',
})
IDENTITY_KEYS = frozenset({'eval_revision', 'gt_revision', 'gt_fingerprint', 'eval_set_id', 'scenario_set_id', 'scenario_name', 'run_upload_id'})
#: score_run's envelope, plus the one key the old plugin writer added to a sidecar (``changed_vs_live_run``).
ENVELOPE_KEYS = frozenset({'cells', 'not_scored', 'not_scored_kinds', 'changed', 'baseline_cells', 'gate_authority', 'warnings',
                           'changed_vs_live_run', 'audit_corrections'})
NOISE_CELL_KEYS = frozenset({'usage_metadata', 'latency'})
#: score_run's typed not_scored kinds: these two mean the eval does not apply; these three that nobody re-judged it.
GUARD_SKIP_KINDS = frozenset({'guard_skipped', 'turn_skipped_every_turn'})
KEEP_KINDS = frozenset({'no_agent_turns', 'turn_refused', 'guard_raised'})
#: Before the typed map, a guard skip was only said in the not_scored prose.
GUARD_SKIP_MARKER = 'guard-skipped'
AGENT_RAISED, AGENT_RECURSION_BUDGET, PRE_CRASH_REAL_DATA = 'agent_raised', 'agent_recursion_budget', 'real_data'
#: The cut reasons the runner maps to an ``incomplete`` row (the SDK's ``_row_outcome``).
HARNESS_CUT_REASONS = frozenset({'time_budget_exhausted', 'turn_budget_exhausted', 'provider_infra'})
GOAL_SOURCE_NONE = 'none'
CARRIED_KEY = 'carried_forward'
#: ``changed[].corrected`` of a set-aside verdict an applied overrule restored: never an audit flip.
RESTORED = 'restored'


def state_of(unit: Any) -> Tuple[str, Optional[str]]:
    """``(state, reason)`` of one judged unit — a cell, or one ``turns[]`` entry (the SDK's ``state_of``)."""
    if not isinstance(unit, Mapping):
        return 'ungraded', 'judge_error'
    status = unit.get('status')
    if status == 'success':
        violated = unit.get('violated')
        return (('fail' if violated else 'pass'), None) if isinstance(violated, bool) else ('ungraded', 'judge_error')
    if status == 'not_applicable':
        return 'not_applicable', 'not_applicable'
    if status == 'inconclusive':
        reason = unit.get('inconclusive_reason')
        reason = reason if isinstance(reason, str) and reason else 'unstated'
        if reason in ('not_applicable', 'mixed_not_applicable', 'deferred'):
            return 'not_applicable', reason
        if reason == 'not_measured':
            return 'ungraded', 'not_measured'
        return 'ungraded', f'{INCONCLUSIVE_PREFIX}{reason}'
    return 'ungraded', 'judge_error'


def stamped_cell(cell: Mapping[str, Any]) -> Dict[str, Any]:
    """The backend's cell minus cost/timing, ``state``/``reason`` stamped on each ``turns[]`` entry."""
    out = {k: copy.deepcopy(v) for k, v in cell.items() if k not in NOISE_CELL_KEYS}
    for turn in out.get('turns') if isinstance(out.get('turns'), list) else ():
        if isinstance(turn, dict):
            turn['state'], turn['reason'] = state_of(turn)
    return out


def _all_turns_skipped(cell: Mapping[str, Any]) -> bool:
    turns = cell.get('turns')
    return (cell.get('scope') == 'turn' or isinstance(turns, list)) and not turns and bool(cell.get('skipped_turn_idxs'))


def entry_of(cell: Any, *, skipped: bool = False, errored: bool = False) -> Dict[str, Any]:
    """One eval's ``{state, reason, cell}``: errored wins, then skipped, then the cell's own state."""
    stored = stamped_cell(cell) if isinstance(cell, Mapping) else None
    if errored:
        return {'state': 'ungraded', 'reason': 'judge_error', 'cell': stored}
    if skipped:
        reason = 'turn_skipped_every_turn' if stored is not None and _all_turns_skipped(stored) else 'guard_skipped'
        return {'state': 'not_applicable', 'reason': reason, 'cell': stored}
    if stored is None:
        return {'state': 'ungraded', 'reason': 'judge_error', 'cell': None}
    state, reason = state_of(stored)
    return {'state': state, 'reason': reason, 'cell': stored}


def _names(raw: Any) -> Set[str]:
    return {str(n) for n in raw} if isinstance(raw, list) else set()


def evals_of(per: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every eval a /finalize-shaped block names: its cells, plus a name only in ``skipped_`` / ``errored_policies``."""
    cells = per.get(CELLS_KEY) if isinstance(per.get(CELLS_KEY), Mapping) else {}
    skipped, errored = _names(per.get('skipped_policies')), _names(per.get('errored_policies'))
    names = sorted({str(n) for n in cells} | skipped | errored)
    return {name: entry_of(cells.get(name), skipped=name in skipped, errored=name in errored) for name in names}


def judging_of(source: Mapping[str, Any]) -> Dict[str, Any]:
    """The verdict-side facts of a /finalize or score_run payload: not a cell, the goal, an identity, the envelope, a
    derived number or a conversation fact."""
    skip = RECORD_KEYS | DERIVED_KEYS | IDENTITY_KEYS | ENVELOPE_KEYS | {CELLS_KEY, VERDICT_KEY, 'aggregated_results'}
    return {k: copy.deepcopy(v) for k, v in source.items() if k not in skip}


def finalize_record(per: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: copy.deepcopy(per[k]) for k in sorted(RECORD_KEYS) if k in per}


def gt_fingerprint(source: Mapping[str, Any], cells: Mapping[str, Any]) -> Optional[str]:
    top = source.get('gt_fingerprint')
    if isinstance(top, str) and top:
        return top
    stamps = {c.get('gt_fingerprint') for c in cells.values() if isinstance(c, Mapping) and c.get('gt_fingerprint')}
    return next(iter(stamps)) if len(stamps) == 1 else None


def _int_or_none(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _agent_owned(kind: Any, activity: Any) -> bool:
    return kind == AGENT_RAISED or (kind == AGENT_RECURSION_BUDGET and activity == PRE_CRASH_REAL_DATA)


def _cause(kind: Optional[str]) -> Dict[str, Any]:
    return {'kind': kind, 'transient': False, 'status': None, 'returncode': None}


def status_of_error(record: Mapping[str, Any]) -> Dict[str, Any]:
    """An attempt that wrote ``error.json``: ``agent_error`` when the agent crashed, else ``harness_error``."""
    kind = record.get('kind')
    if kind is None and isinstance(record.get('redrive'), Mapping):
        kind = record['redrive'].get('original_kind')
    transient = record.get('transient') is True
    cause = {'kind': kind if isinstance(kind, str) else None, 'transient': transient,
             'status': _int_or_none(record.get('status')), 'returncode': _int_or_none(record.get('returncode'))}
    if _agent_owned(kind, record.get('pre_crash_tool_activity')):
        return {'value': 'agent_error', 'cause': cause, 'retry': False}
    return {'value': 'harness_error', 'cause': cause, 'retry': transient}


def _unwrap_view(view: Mapping[str, Any], key: str) -> Any:
    if key in view:
        return view[key]
    nested = view.get('aggregated_results')
    return nested.get(key) if isinstance(nested, Mapping) else None


def _goal_facts(view: Mapping[str, Any]) -> Tuple[Optional[bool], Optional[str]]:
    """``(goal_achieved, goal_source)`` off the goal block, as the SDK's ``_goal_facts`` reads them — never off its
    ``passed`` / ``score``, which the backend does not serve. ``(None, None)`` when there is no block."""
    block = _unwrap_view(view, VERDICT_KEY)
    if not isinstance(block, Mapping):
        return None, None
    achieved, source = block.get('goal_achieved'), block.get('goal_source')
    return (achieved if isinstance(achieved, bool) else None), (source if isinstance(source, str) and source else GOAL_SOURCE_NONE)


def _voids_scenario(view: Mapping[str, Any]) -> bool:
    """The SDK's ``_voids_scenario``: ``measurement_integrity.clean`` is not true and a breach VOIDS the scenario. A
    breach typed ``voids_scenario: false`` leaves the dialog scored (its eval ``ungraded``); a claim this cannot read
    as non-voiding (``clean`` not a bool, no typed breach list, a breach without the flag) is read as voiding."""
    block = _unwrap_view(view, 'measurement_integrity')
    if not isinstance(block, Mapping) or 'clean' not in block or block['clean'] is True:
        return False
    breaches = block.get('breaches')
    if block['clean'] is not False or not isinstance(breaches, list) or not breaches:
        return True
    return any(not isinstance(breach, Mapping) or breach.get('voids_scenario') is not False for breach in breaches)


def judged_status(view: Mapping[str, Any], *, graded: bool, cut_by: Optional[str]) -> Dict[str, Any]:
    """The SDK's ``_judged_status``: ungraded / a voiding breach / cut / goal undecided (``goal_achieved`` null with a
    goal owner) / scored."""
    achieved, source = _goal_facts(view)
    if not graded:
        status = {'value': 'harness_error', 'cause': _cause('not_graded')}
    elif _voids_scenario(view):
        status = {'value': 'harness_error', 'cause': _cause('measurement_unclean')}
    elif cut_by is not None:
        status = {'value': 'incomplete', 'cause': _cause(cut_by)}
    elif achieved is None and source != GOAL_SOURCE_NONE:
        status = {'value': 'goal_undecided', 'cause': None}
    else:
        status = {'value': 'scored', 'cause': None}
    return {**status, 'retry': False, 'cut_by': cut_by}


def status_of_landed(view: Mapping[str, Any], *, graded: bool, ended_early: Optional[str], turn_failure: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """A dialog that LANDED, in precedence order (the SDK's ``status_of_landed``)."""
    failure = turn_failure if isinstance(turn_failure, Mapping) else {}
    _, source = _goal_facts(view)
    if _agent_owned(ended_early, failure.get('pre_crash_tool_activity')):
        return {'value': 'agent_error', 'cause': _cause(str(ended_early)), 'retry': False}
    if source == 'pipeline_error':
        return {'value': 'agent_error', 'cause': _cause('pipeline_error'), 'retry': False}
    if ended_early == AGENT_RECURSION_BUDGET:
        return {'value': 'harness_error', 'cause': _cause(ended_early), 'retry': False}
    return judged_status(view, graded=graded, cut_by=ended_early if ended_early in HARNESS_CUT_REASONS else None)


# ---------------------------------------------------------------------------------------------------------
# The old reading rules, one last time
# ---------------------------------------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def counted_error(d: Path) -> bool:
    """An ``error.json`` counts when the folder has no transcript, or the error is NEWER than it (else it is a leftover)."""
    err, transcript = d / ERROR, d / TRANSCRIPT
    return err.exists() and (not transcript.exists() or err.stat().st_mtime > transcript.stat().st_mtime)


def sidecar_state(d: Path) -> Optional[bool]:
    """True when ``rescore.json`` is newer than the transcript beside it (it held the current verdicts), False when older
    (a leftover the old flow ignored), None when there is no pair or a counted error voids the transcript."""
    path, transcript = d / SIDECAR, d / TRANSCRIPT
    if not path.exists() or not transcript.exists() or counted_error(d):
        return None
    return path.stat().st_mtime > transcript.stat().st_mtime


def guard_skips_of(sidecar: Optional[Mapping[str, Any]]) -> Set[str]:
    """The policies a rescore reported guard-skipped: the typed ``not_scored_kinds`` map, else the pre-typed prose."""
    doc = sidecar or {}
    kinds = doc.get('not_scored_kinds')
    if isinstance(kinds, Mapping) and kinds:
        return {str(name) for name, kind in kinds.items() if str(kind) in GUARD_SKIP_KINDS}
    not_scored = doc.get('not_scored')
    return {str(n) for n, why in not_scored.items() if GUARD_SKIP_MARKER in str(why or '').lower()} if isinstance(not_scored, Mapping) else set()


def removed_policies(sidecars: Iterable[Mapping[str, Any]]) -> Set[str]:
    """Policies that LEFT the eval set (``changed[].new`` null) in any fresh sidecar: one set for the whole run."""
    return {str(e['policy_name']) for doc in sidecars for e in doc.get('changed') or []
            if isinstance(e, Mapping) and e.get('policy_name') and e.get('new') is None}


def verdict_field(per: Mapping[str, Any], sidecar: Optional[Mapping[str, Any]], key: str) -> Any:
    """One block, the sidecar's copy first, then the transcript's (``aggregated_results`` unwrapped)."""
    for source in (sidecar, per):
        if not isinstance(source, Mapping):
            continue
        if key in source:
            return source[key]
        nested = source.get('aggregated_results')
        if isinstance(nested, Mapping) and key in nested:
            return nested[key]
    return None


# ---------------------------------------------------------------------------------------------------------
# One attempt's document
# ---------------------------------------------------------------------------------------------------------


def _mtime(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec='seconds')
    except OSError:
        return None


def has_artifacts(d: Path) -> bool:
    return d.is_dir() and any((d / name).exists() for name in (TRANSCRIPT, ERROR, TOOL_EVENTS))


def attempt_dirs(run_dir: Path) -> List[Path]:
    """Every attempt folder of the run: ``<item>/``, ``trial_<k>/<item>/`` and each one's ``stability_retry/``."""
    found: List[Path] = []
    for d in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if d.name.startswith('trial_') and not has_artifacts(d):
            found.extend(sorted(c for c in d.iterdir() if has_artifacts(c)))
        elif has_artifacts(d):
            found.append(d)
    return [a for d in found for a in (d, d / RETRY_DIRNAME) if has_artifacts(a)]


def _ended_early(transcript: Mapping[str, Any], per: Mapping[str, Any], roster: Mapping[str, Any], item: str) -> Optional[str]:
    """The dialog's ended_early reason: the harness's (transcript metadata), else the server's, else the run roster's."""
    meta = transcript.get('metadata') if isinstance(transcript.get('metadata'), Mapping) else {}
    outcome = per.get('dialog_outcome') if isinstance(per.get('dialog_outcome'), Mapping) else {}
    for reason in (meta.get('ended_early'), outcome.get('ended_early'), roster.get(item)):
        if isinstance(reason, str) and reason:
            return reason
    return None


def attempt_of(d: Path, transcript: Mapping[str, Any]) -> Dict[str, Any]:
    retry = d.name == RETRY_DIRNAME
    item = d.parent.name if retry else d.name
    meta = transcript.get('metadata') if isinstance(transcript.get('metadata'), Mapping) else {}
    trial = d.parent.parent.name if retry else d.parent.name
    index = _int_or_none(meta.get('trial_index')) or (int(trial[6:]) if trial[6:].isdigit() and trial.startswith('trial_') else 1)
    scenario = meta.get('scenario') if isinstance(meta.get('scenario'), str) and meta.get('scenario') else item
    thread = meta.get('thread_id') if isinstance(meta.get('thread_id'), str) else None
    return {'scenario': scenario, 'item': item, 'kind': 'stability_retry' if retry else 'trial', 'trial_index': max(index, 1),
            'trial_count': max(_int_or_none(meta.get('trial_count')) or 1, index, 1), 'thread_id': thread}


def _states(evals: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    return {name: entry['state'] for name, entry in sorted(evals.items())}


def _unit_state(raw: Any) -> Optional[str]:
    return state_of(raw)[0] if isinstance(raw, Mapping) else None


def _body(cell: Any) -> Any:
    return {k: v for k, v in cell.items() if k != CARRIED_KEY} if isinstance(cell, Mapping) else cell


def sidecar_cells(sidecar: Mapping[str, Any]) -> Dict[str, Any]:
    """A sidecar's `cells`, each a correction moved saying :func:`correction_reason` wherever it still carries that
    correction's audit `reason` — the cell's `reasoning`, a finding's `why` — as a writer before this rule left it.
    Every other text (a judge's, a restored verdict's) is the cell's own and stays. An old run is converted once, so this
    is where a sidecar a pre-RT57-6 correction wrote gets the customer-facing reason; the auditor's note stays in the
    history entry's ``reason``."""
    run_scripts = str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-run' / 'scripts')
    if run_scripts not in sys.path:
        sys.path.insert(0, run_scripts)
    from build_run_json import CORRECTION_REASONS, correction_reason  # noqa: PLC0415 - the page's one wording of a correction
    cells = {str(name): cell for name, cell in (sidecar.get('cells') or {}).items() if isinstance(cell, dict)}
    for entry in sidecar.get('changed') or []:
        if not isinstance(entry, dict) or entry.get('corrected') not in CORRECTION_REASONS or not entry.get('reason'):
            continue
        cell = cells.get(str(entry.get('policy_name')))
        if cell is None:
            continue
        note, said = ' '.join(str(entry['reason']).split()), correction_reason(str(entry['corrected']), entry.get('clause'))
        cell = cells[str(entry['policy_name'])] = {**cell}
        if ' '.join(str(cell.get('reasoning') or '').split()) == note:
            cell['reasoning'] = said
        if isinstance(cell.get('findings'), list):
            cell['findings'] = [{**f, 'why': said} if isinstance(f, dict) and ' '.join(str(f.get('why') or '').split()) == note else f
                                for f in cell['findings']]
    return cells


def convert_entry(sidecar: Mapping[str, Any], at: Optional[str], run_evals: Mapping[str, Dict[str, Any]], evals: Mapping[str, Dict[str, Any]],
                  removed: Set[str]) -> Dict[str, Any]:
    """``history[1]``: what the fresh sidecar changed over the run's verdicts — each eval whose cell it replaced (old and
    new state, the replaced cell), the audit corrections it carried (``changed[].corrected`` and each cell's
    ``audit_correction`` stub, with their verdict, cause and reason; a quote the sidecar never kept is null)."""
    cells = sidecar.get('cells') if isinstance(sidecar.get('cells'), Mapping) else {}
    corrections = {(str(e.get('policy_name')), e.get('turn_idx')): e for e in sidecar.get('changed') or []
                   if isinstance(e, Mapping) and e.get('corrected') not in (None, '', RESTORED)}
    changes: List[Dict[str, Any]] = []
    for name in sorted(set(run_evals) | set(evals)):
        old, new = run_evals.get(name), evals.get(name)
        if old is not None and new is not None and old['state'] == new['state'] and _body(old['cell']) == _body(new['cell']):
            continue
        change: Dict[str, Any] = {'eval': name, 'turn_idx': None, 'old': {'state': old['state']} if old else None,
                                  'new': {'state': new['state']} if new else None, 'replaced_cell': old['cell'] if old else None}
        stub = cells.get(name, {}).get('audit_correction') if isinstance(cells.get(name), Mapping) else None
        legacy = corrections.pop((name, None), None)
        if isinstance(legacy, Mapping):
            change.update(verdict=legacy.get('verdict') or 'corrected', cause=legacy.get('cause'), quote=legacy.get('quote'),
                          reason=legacy.get('reason'), corrected=legacy.get('corrected'),
                          **{k: legacy[k] for k in ('call_id', 'clause') if legacy.get(k)})
        elif isinstance(stub, Mapping):
            change.update(verdict=stub.get('verdict'), cause=stub.get('cause'), quote=None, reason=None, corrected=stub.get('corrected'),
                          note='corrected by an audit the backend re-applied; the legacy sidecar kept no quote or reason')
        changes.append(change)
    for (name, turn_idx), legacy in sorted(corrections.items(), key=lambda kv: (kv[0][0], -1 if kv[0][1] is None else kv[0][1])):
        changes.append({'eval': name, 'turn_idx': turn_idx, 'old': {'state': _unit_state(legacy.get('old'))},
                        'new': {'state': _unit_state(legacy.get('new'))}, 'verdict': legacy.get('verdict') or 'corrected',
                        'cause': legacy.get('cause'), 'quote': legacy.get('quote'), 'reason': legacy.get('reason'),
                        'corrected': legacy.get('corrected'), **{k: legacy[k] for k in ('call_id', 'clause') if legacy.get(k)}})
    kinds = sidecar.get('not_scored_kinds') if isinstance(sidecar.get('not_scored_kinds'), Mapping) else {}
    dropped = {name: 'removed' for name in sorted(removed & set(run_evals))}
    dropped.update({name: str(kinds.get(name) or 'guard_skipped') for name in sorted(guard_skips_of(sidecar))
                    if (run_evals.get(name) or {}).get('cell') is not None and name not in dropped})
    return {
        'seq': 1, 'source': 'convert', 'at': at,
        'eval_revision': sidecar.get('eval_revision') if isinstance(sidecar.get('eval_revision'), str) else None,
        'gt_revision': _int_or_none(sidecar.get('gt_revision')), 'run_upload_id': sidecar.get('run_upload_id'),
        'scenario_name': sidecar.get('scenario_name'),
        'rejudged': sorted(str(n) for n, c in cells.items() if not (isinstance(c, Mapping) and c.get(CARRIED_KEY))),
        'carried': sum(1 for c in cells.values() if isinstance(c, Mapping) and c.get(CARRIED_KEY)),
        'dropped': dropped, 'changes': changes,
        **({'warnings': copy.deepcopy(sidecar['warnings'])} if sidecar.get('warnings') else {}),
    }


def errored_doc(error: Optional[Mapping[str, Any]], attempt: Mapping[str, Any], *, at: Optional[str] = None) -> Dict[str, Any]:
    """The document of an attempt that wrote ``error.json`` (or nothing): its status, ``graded: false``, no evals."""
    return {
        'verdicts_schema': SCHEMA, 'attempt': dict(attempt), 'graded': False, 'status': status_of_error(error or {}),
        'judged_against': {'eval_set_id': None, 'eval_revision': None, 'scenario_set_id': None, 'gt_fingerprint': None,
                           'gt_revision': None, 'kept_from_earlier_revision': []},
        'goal': None, 'judging': {}, 'evals': {},
        'history': [{'seq': 0, 'source': 'run', 'at': at, 'eval_revision': None, 'gt_fingerprint': None, 'finalize_job_id': None, 'states': {}}],
    }


def landed_doc(transcript: Mapping[str, Any], attempt: Mapping[str, Any], *, sidecar: Optional[Mapping[str, Any]] = None,
               removed: Set[str] = frozenset(), ended_early: Optional[str] = None, run_at: Optional[str] = None,
               convert_at: Optional[str] = None) -> Tuple[Dict[str, Any], List[str]]:
    """``(document, notes)`` of an attempt that LANDED, by the old rules: the transcript's verdict block, with the fresh
    ``sidecar`` (if any) over it and the run's ``removed`` policies gone. ``ended_early`` is the dialog's cut reason
    (:func:`_ended_early`). Pure."""
    notes: List[str] = []
    raw = transcript.get(LEGACY_KEY)
    per: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    if 'skipped_policies' not in per and isinstance((transcript.get('eval') or {}).get('skipped_policies'), list):
        per['skipped_policies'] = transcript['eval']['skipped_policies']
    meta = transcript.get('metadata') if isinstance(transcript.get('metadata'), dict) else {}
    run_evals = evals_of(per)  # the run's own verdicts, as it judged them (history[0])
    if sidecar is None:
        evals = {n: e for n, e in run_evals.items() if n not in removed}
        goal = verdict_field(per, None, VERDICT_KEY)
        judging = judging_of(per)
    else:
        skipped_now = guard_skips_of(sidecar)
        rescored_cells = sidecar_cells(sidecar)
        cells = {str(k): v for k, v in (per.get(CELLS_KEY) or {}).items()}
        cells.update(rescored_cells)
        for name in skipped_now | removed:
            cells.pop(name, None)
        skipped = {str(n) for n in per.get('skipped_policies') or [] if str(n) not in cells and str(n) not in removed} | (skipped_now - removed)
        errored = {str(n) for n in per.get('errored_policies') or [] if str(n) not in rescored_cells and str(n) not in skipped}
        evals = evals_of({CELLS_KEY: cells, 'skipped_policies': sorted(skipped), 'errored_policies': sorted(errored)})
        kinds = sidecar.get('not_scored_kinds') if isinstance(sidecar.get('not_scored_kinds'), Mapping) else {}
        for name in skipped_now - removed:
            if kinds.get(name) == 'turn_skipped_every_turn':
                evals[name]['reason'] = 'turn_skipped_every_turn'
        goal = sidecar.get(VERDICT_KEY) if isinstance(sidecar.get(VERDICT_KEY), dict) else None
        if goal is None:
            notes.append(f"{SIDECAR} carries no scenario_verdict: the goal is unknown (the transcript's is the pre-edit one)")
        judging = {**judging_of(per), **judging_of(sidecar)}
        integrity = verdict_field(per, sidecar, 'measurement_integrity')
        if integrity is not None:
            judging['measurement_integrity'] = copy.deepcopy(integrity)
    goal = copy.deepcopy(goal) if isinstance(goal, dict) else None
    view = {**judging, **({VERDICT_KEY: goal} if goal is not None else {})}
    graded = bool(evals)
    failure = meta.get('turn_failure') if isinstance(meta.get('turn_failure'), dict) else None
    status = status_of_landed(view, graded=graded, ended_early=ended_early, turn_failure=failure)
    run_revision = per.get('eval_revision') if isinstance(per.get('eval_revision'), str) else meta.get('eval_revision')
    run_cells = per.get(CELLS_KEY) if isinstance(per.get(CELLS_KEY), Mapping) else {}
    job = meta.get('finalize_job_id')
    history: List[Dict[str, Any]] = [{
        'seq': 0, 'source': 'run', 'at': run_at, 'eval_revision': run_revision if isinstance(run_revision, str) else None,
        'gt_fingerprint': gt_fingerprint(per, run_cells), 'finalize_job_id': job if isinstance(job, str) else None,
        'states': _states(run_evals),
    }]
    judged = {'eval_set_id': meta.get('eval_set_id') if isinstance(meta.get('eval_set_id'), str) else None,
              'eval_revision': history[0]['eval_revision'],
              'scenario_set_id': meta.get('scenario_set_id') if isinstance(meta.get('scenario_set_id'), str) else None,
              'gt_fingerprint': history[0]['gt_fingerprint'], 'gt_revision': None, 'kept_from_earlier_revision': []}
    if sidecar is not None:
        entry = convert_entry(sidecar, convert_at, run_evals, evals, removed)
        history.append(entry)
        kinds = sidecar.get('not_scored_kinds') if isinstance(sidecar.get('not_scored_kinds'), Mapping) else {}
        # A sidecar that recorded no revision leaves it unknown: its cells are not the run's revision (old rule: withdraw).
        judged.update(eval_revision=entry['eval_revision'], gt_revision=entry['gt_revision'],
                      gt_fingerprint=gt_fingerprint(sidecar, sidecar.get('cells') or {}) or judged['gt_fingerprint'],
                      kept_from_earlier_revision=sorted(n for n, k in kinds.items() if k in KEEP_KINDS and n in evals),
                      **{k: sidecar[k] for k in ('eval_set_id', 'scenario_set_id') if isinstance(sidecar.get(k), str)})
    return {
        'verdicts_schema': SCHEMA, 'attempt': dict(attempt), 'graded': graded, 'status': status,
        'judged_against': judged, 'goal': goal, 'judging': judging, 'evals': evals if graded else {}, 'history': history,
    }, notes


def legacy_doc(d: Path, *, removed: Set[str], roster: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """``(the attempt's verdicts document, notes)``, built by the old rules from the files ``d`` holds."""
    transcript = None if counted_error(d) else _read_json(d / TRANSCRIPT)
    if not isinstance(transcript, dict):
        error = _read_json(d / ERROR) if (d / ERROR).exists() else None
        notes = [] if isinstance(error, dict) else ['no transcript and no readable error.json: recorded as an untyped harness error']
        stale = _read_json(d / TRANSCRIPT) if (d / TRANSCRIPT).exists() else None
        return errored_doc(error if isinstance(error, dict) else None, attempt_of(d, stale if isinstance(stale, dict) else {}),
                           at=_mtime(d / ERROR)), notes
    fresh = sidecar_state(d)
    sidecar = _read_json(d / SIDECAR) if fresh else None
    per = transcript.get(LEGACY_KEY) if isinstance(transcript.get(LEGACY_KEY), dict) else {}
    doc, notes = landed_doc(transcript, attempt_of(d, transcript), sidecar=sidecar if isinstance(sidecar, dict) else None,
                            removed=removed, ended_early=_ended_early(transcript, per, roster, d.name),
                            run_at=_mtime(d / TRANSCRIPT), convert_at=_mtime(d / SIDECAR))
    if fresh is False:
        notes.insert(0, f'{SIDECAR} is OLDER than its transcript (a leftover the old flow ignored): not converted')
    return doc, notes


def run_roster(run_dir: Path) -> Dict[str, Any]:
    """``{item: ended_early reason}`` from the run's own ``run_summary.json`` (covers rows whose transcript says none)."""
    doc = _read_json(run_dir / 'run_summary.json')
    rows = doc.get('ended_early_scenarios') if isinstance(doc, dict) else None
    out: Dict[str, Any] = {}
    for name, row in (rows.items() if isinstance(rows, dict) else ()):
        reason = row.get('reason') if isinstance(row, dict) else row
        if isinstance(reason, str) and reason:
            out[str(name)] = reason
    return out


def with_finalize_record(d: Path) -> bool:
    """Copy the transcript's run facts into its ``finalize_record`` (where readers take them), once, keeping its
    formatting and its mtime. True when it wrote."""
    path = d / TRANSCRIPT
    if counted_error(d) or not path.exists():
        return False
    text = path.read_text(encoding='utf-8')
    doc = json.loads(text)
    if not isinstance(doc, dict) or 'finalize_record' in doc or not isinstance(doc.get(LEGACY_KEY), dict):
        return False
    doc['finalize_record'] = finalize_record(doc[LEGACY_KEY])
    lines = text.split('\n', 2)
    indent = len(lines[1]) - len(lines[1].lstrip(' ')) if len(lines) > 1 and lines[1].startswith(' ') else None
    out = json.dumps(doc, indent=indent, ensure_ascii=not any(ord(c) > 127 for c in text)) + ('\n' if text.endswith('\n') else '')
    stat = path.stat()
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(out, encoding='utf-8')
    os.replace(tmp, path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    return True


def plan(run_dir: Path) -> Tuple[List[Tuple[Path, Dict[str, Any], List[str]]], List[Path]]:
    """``([(folder, document, notes)], [folders already converted])`` — every document built BEFORE anything is written,
    so no write can move the mtimes the old freshness rule reads."""
    folders = attempt_dirs(run_dir)
    pending = [d for d in folders if not (d / FILENAME).exists()]
    sidecars = [doc for d in folders if sidecar_state(d) and isinstance(doc := _read_json(d / SIDECAR), dict)]
    removed, roster = removed_policies(sidecars), run_roster(run_dir)
    return [(d, *legacy_doc(d, removed=removed, roster=roster)) for d in pending], [d for d in folders if d not in pending]


def convert(run_dir: Path, send: Any = None) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """``(converted, skipped, refused [{item, exit, why}])``, folder paths relative to ``run_dir``. ``send`` is
    :func:`verdict_events.send` (injectable for tests)."""
    if send is None:
        import verdict_events  # noqa: PLC0415 - the one route to the SDK writer

        send = verdict_events.send
    todo, done = plan(run_dir)
    converted: List[str] = []
    refused: List[Dict[str, Any]] = []
    for d, doc, notes in todo:
        rel = d.relative_to(run_dir).as_posix()
        for note in notes:
            print(f'convert_verdicts: {rel}: {note}', file=sys.stderr)
        with_finalize_record(d)
        code, message = send('import-legacy', d, doc)
        if code == 0:
            converted.append(rel)
        else:
            refused.append({'item': rel, 'exit': code, 'why': message})
    return converted, [d.relative_to(run_dir).as_posix() for d in done], refused


def main(argv: Optional[List[str]] = None) -> int:
    return _client_handshake.refusal_main(lambda: _main(argv))


def _main(argv: Optional[List[str]]) -> int:
    parser = argparse.ArgumentParser(prog='convert_verdicts.py', description=__doc__.split('\n\n', 1)[0])
    parser.add_argument('run_dir', help='the run dir to convert (its attempt folders, trials and stability retries)')
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f'convert_verdicts: {run_dir} is not a directory', file=sys.stderr)
        return EXIT_USAGE
    try:
        converted, skipped, refused = convert(run_dir)
    except (OSError, ValueError) as exc:
        print(f'convert_verdicts: {exc}', file=sys.stderr)
        return EXIT_USAGE
    except RuntimeError as exc:  # verdict_events.WriterUnavailable
        print(f'convert_verdicts: {exc} — nothing was converted', file=sys.stderr)
        return EXIT_WRITER
    for rel in skipped:
        print(f'skipped {rel}: it already has {FILENAME}')
    for rel in converted:
        print(f'converted {rel}')
    for row in refused:
        print(f'convert_verdicts: REFUSED {row["item"]} (writer exit {row["exit"]}): {row["why"]}', file=sys.stderr)
    print(f'{len(converted)} converted, {len(skipped)} already converted, {len(refused)} refused')
    return EXIT_WRITER if refused else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
