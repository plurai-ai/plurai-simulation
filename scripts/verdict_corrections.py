#!/usr/bin/env python3
"""A FINAL run's FALSE RED / FALSE PASS audit rows applied as corrected verdicts, sent as events to the SDK's verdicts writer.

    verdict_corrections.py build --run-dir <final run> [--audit <rows.json>]…
                                 [--report <saved suite_diagnostics envelope>]… [--out <file>]
    verdict_corrections.py check --run-dir <final run> [--audit <rows.json>]… [--report <envelope>]…

``build`` corrects each verdict unit a ``FALSE RED`` row (a red the rule does not catch) or ``FALSE PASS`` row (a
sampled green the rule does catch: ``red_cells.py list --greens``) rules (``red_cells.ruled_units`` splits rows into
units) and sends ONE ``plurai verdicts apply-correction`` call per corrected unit (``verdict_events``):
``{history_len, source: correction, eval, turn_idx, old: {state}, new: {state}, cell, goal, verdict, cause, quote,
quote_turn, quote_file?, reason, propagated_from}`` — the corrected cell and goal as that unit leaves them, built on the verdicts the
item's previous unit left, ``history_len`` re-read at each call. Every trial and stability retry is its own item, so a
sibling's correction is its own call against that item.

A unit is corrected only when ALL hold, else it is listed in ``skipped`` with the reason and the rest still apply:
no row rules it the other way (:data:`DIRECTIONS`: ``TRUE RED`` / ``IN SCOPE`` / ``UNDECIDABLE`` keep a red,
``CONFIRMED PASS`` / ``UNDECIDABLE`` a pass — such a ruling never flips); it is red (a pass, for a FALSE PASS) in the
item's CURRENT cells; its row names ``defect_surface`` in ``red_cells.CAUSES`` and ``corrected`` = ``pass`` |
``not_applicable`` for a FALSE RED (when absent, by cause: :data:`UNMEASURED_CAUSES` measured nothing and read
``not_applicable``, every other cause ``pass``), ``fail`` (when absent) for a FALSE PASS, whose new red joins the verdict
lists of the tier the backend stamped on its cell (the cell's ``tier``: :data:`TIER_LISTS`); and its ``quote``
is in the agent's reply (``record_finding.check_evidence``) at ``red_cells.evidence_turn``: the unit's turn, else
``quote_turn`` (required for a scenario-scoped cell of a dialog), else turn 0 on a pipeline item, whose whole output
is one reply — or cites a tool call the agent made at that turn (``red_cells.cited_call``: its ``call_id``, or its name
and arguments), recorded as the correction's ``call_id`` — or, for a FALSE RED only, contains the unit's own judge finding
quote at that turn and is in a tool answer recorded there (``red_cells.judge_cited_tool_answer``: a missed action's premise).

The corrected cell's ``reasoning`` is :func:`corrected_reasoning` (:func:`customer_reason` and the audit's quote) — what
the customer page shows — and its finding's ``why`` :func:`customer_reason`; the cell carries the audit's own ruling as
``audit_corrected`` (:func:`audit_evidence`) and drops the overturned verdict's ``confidence``.

CELL ANCHOR. A FALSE RED whose quote no reply, written file or tool answer anchors — the user's own message, a dataset
row of the world — is corrected on its cell (item, policy, turn) when the quote is verbatim in the item's records and in
none of the agent's replies (:func:`cell_anchored`); it never reaches a sibling.

SIBLINGS. A ruling the build accepts also corrects each sibling trial's unit (same scenario, same policy, any trial or
retry) that is in the same recorded state, that NO row rules, and whose reply holds the ruling's quote verbatim, at
least :data:`PROPAGATE_MIN_QUOTE_CHARS` long (the change's ``propagated_from`` names the source). A sibling a row ruled
the other way on that same quote is never flipped: it is listed in ``conflicts``, for the audit to re-read the pair.

RESTORED. A backend gate whose evidence carries ``raw_violated`` (:func:`restoring_gates`) sets a verdict aside: the
cell goes inconclusive and keeps the judge's raw ``violated``. An overrule the backend APPLIED on one (``--report``:
``gate_authority.overrules[]`` with ``outcome: applied``) restores that raw verdict on the backend only; ``build``
lands it here too — the unit of the item whose transcript names the finding's ``thread_id`` goes conclusive at the raw
verdict (``corrected: restored``, the judge's reasoning kept).

``check`` reads the run's current verdicts. With ``--report`` it lists every applied restoring overrule whose unit is not
conclusive at the raw verdict (``not_landed``); it then reads the run's audit (``--audit``, else its own) and lists every
``FALSE RED`` / ``FALSE PASS`` unit the current verdicts still count (``uncorrected``, with why ``build`` refused it):
the correction record holds only when both lists are empty.

FILE ANCHOR. A row may quote a file the agent wrote instead of a reply: ``quote_file`` names its path in the item's
world (each turn's ``turn_world.delta.upserted``, and ``final_world_state.json``; an SDK file row by its ``name``, its
``text_preview`` the text) and ``quote`` must be in a version of it (``red_cells.quote_miss``). Such a ruling reaches a sibling whose world holds the quote in any file.

Exit codes: 0 ok (skipped units are listed, never fatal) · 2 usage or unreadable input · 3 an applied overrule did not land ·
4 an audited correction is still counted · 5 the verdicts writer refused an event (the summary names the item).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'skills' / 'simulation-run' / 'scripts'))

import red_cell_audit  # noqa: E402 - the audit's missing-file signal, its backend verdict tokens
import _client_handshake  # noqa: E402 - the plugin's refusal exit
import record_finding  # noqa: E402 - sibling script: the quote check
import red_cells  # noqa: E402 - the item walk, the current cells, the audit's unit split
import verdict_events  # noqa: E402 - the one route to the SDK's verdicts writer
from build_run_json import REQUIRED_STEP_SKIPPED_KEY, RESTORED_CORRECTION, TARGET_PREMISE_UNSHOWN_KEY, correction_reason  # noqa: E402 - the readers' own rules

PASS, NOT_APPLICABLE, FAIL, RESTORED = 'pass', 'not_applicable', 'fail', RESTORED_CORRECTION
#: The evidence key that marks a backend gate as one that SETS A RAW VERDICT ASIDE (the cell goes inconclusive and keeps
#: the judge's ``violated``): an overrule the backend applies on such a gate restores that raw verdict.
RAW_VERDICT_KEY = 'raw_violated'
#: ``check --report`` exit when an overrule the backend applied never reached this run's scores.
EXIT_NOT_LANDED = 3
#: ``check`` exit when an audited FALSE RED / FALSE PASS is still counted in the run's current verdicts (C8).
EXIT_UNCORRECTED = 4
#: ``build`` exit when the verdicts writer refused an item's event (or no installed SDK offers it).
EXIT_WRITER_REFUSED = 5
#: The ``scenario_verdict.target_premise_unshown`` values that are the AGENT's doing: the target beat was contingent on a
#: step the agent had to take, and it never took it (``contingent_never_met``). Every other ``undelivered:`` reason
#: (``budget``, ``blocked_by:…``, ``refused_out_of_order``, …) is the simulator's, and leaves the goal undecided.
AGENT_SKIPPED_PREMISES = ('undelivered:contingent_never_met',)
#: The inconclusive reason a ``not_applicable`` correction stamps (one of build_run_json's NOT_APPLICABLE_REASONS).
NA_REASON = 'not_applicable'
#: Scenario-verdict lists a policy leaves once none of its units is red.
VIOLATION_LISTS = ('catastrophic_violations', 'decisive_violations', 'high_severity_violations', 'unclassified_violations')
#: The lists a policy's NEW red joins, by the ``tier`` the backend stamped on its cell (``critical`` | ``failing`` |
#: ``standard``); a cell that carries none (an older run) joins the backend's own residue list.
TIER_LISTS: Dict[Optional[str], Tuple[str, ...]] = {
    'critical': ('catastrophic_violations', 'decisive_violations'),
    'failing': ('high_severity_violations',),
    'standard': (),
    None: ('unclassified_violations',),
}
#: A ruling reaches an unruled sibling trial only through a quote this long (whitespace-normalised): a number or a
#: name is not a behaviour.
PROPAGATE_MIN_QUOTE_CHARS = 40


@dataclass(frozen=True)
class Direction:
    """One correcting verdict: the state its unit must be in now, the rulings that keep it, what it may become."""

    verdict: str
    state: str
    recorded: Callable[[Mapping[str, Any]], bool]
    keeps: Tuple[str, ...]
    corrections: Tuple[str, ...]


DIRECTIONS = {
    red_cells.FALSE_RED: Direction(red_cells.FALSE_RED, 'red', red_cells.is_red, red_cells.KEEPS, (PASS, NOT_APPLICABLE)),
    red_cells.FALSE_PASS: Direction(red_cells.FALSE_PASS, 'a pass', red_cells.is_pass, (red_cells.CONFIRMED_PASS, red_cells.UNDECIDABLE), (FAIL,)),
}
#: The FALSE RED causes under which the cell measured nothing (the scenario, harness, mocked world or a scan fact kept the
#: behaviour from being observed): a row naming no ``corrected`` lands ``not_applicable``; every other cause lands
#: ``pass``. Twin of the backend's ``UNMEASURED_CAUSES``; both run the same vectors
#: (``scripts/tests/fixtures/correction_defaults.json``, a byte-identical copy of the backend's).
UNMEASURED_CAUSES = frozenset({'scenario', 'harness', 'env', 'scan_fact'})


def correction(row: Mapping[str, Any], d: Direction) -> Any:
    """What ``row`` corrects its unit to in direction ``d``: its own ``corrected``, else ``fail`` for a FALSE PASS and, for
    a FALSE RED, by cause (:data:`UNMEASURED_CAUSES`)."""
    unmeasured = d.verdict == red_cells.FALSE_RED and row.get('defect_surface') in UNMEASURED_CAUSES
    return row.get('corrected') or (NOT_APPLICABLE if unmeasured else d.corrections[0])


#: The keys the verdicts writer stamps on each cell and turn it stores. A corrected unit is sent without them, so the
#: writer stamps them again from the unit's new verdict.
WRITER_STAMPS = ('state', 'reason')
#: A correction whose evidence is a tool call the agent made (``red_cells.cited_call``): that call's ``call_id``.
QUOTE_CALL = 'call_id'

Record = Dict[str, Any]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# the one function that applies corrections
# ---------------------------------------------------------------------------


def _set_aside(unit: Mapping[str, Any]) -> bool:
    """A unit a backend gate set aside: not conclusive, its raw ``violated`` kept."""
    return unit.get('status') != 'success' and isinstance(unit.get('violated'), bool)


def _correct_unit(unit: Dict[str, Any], record: Mapping[str, Any]) -> Dict[str, Any]:
    old = {'status': unit.get('status'), 'violated': bool(unit.get('violated'))}
    for key in WRITER_STAMPS:
        unit.pop(key, None)
    failed = record['corrected'] == FAIL or (record['corrected'] == RESTORED and bool(record['violated']))
    if record['corrected'] == RESTORED:
        unit['status'] = 'success'
        unit.pop('inconclusive_reason', None)
    unit['violated'] = failed
    for key in ('label', 'value'):
        if isinstance(unit.get(key), bool):
            unit[key] = not failed
    if record['corrected'] == NOT_APPLICABLE:
        unit['status'] = 'inconclusive'
        unit['inconclusive_reason'] = NA_REASON
    if record['corrected'] == RESTORED:
        return old  # the judge's own reasoning and findings are what the restored verdict stands on
    unit['reasoning'] = corrected_reasoning(record)
    unit[AUDIT_CORRECTED] = audit_evidence(record)
    for key in OVERTURNED_KEYS:
        unit.pop(key, None)
    unit.pop('findings', None)
    if failed and record.get('turn_idx') is None:
        unit['findings'] = [_finding(record)]
    return old


def _finding(record: Mapping[str, Any]) -> Dict[str, Any]:
    """The finding a FALSE PASS correction cites: the audit's verbatim quote at its turn (a file it is in, or the tool
    call it names: ``call_id``), with :func:`customer_reason` as its ``why``."""
    turn = record.get('turn_idx') if record.get('turn_idx') is not None else record.get('quote_turn')
    return {**({'turn_idx': turn} if turn is not None else {}), **({'file': record[red_cells.QUOTE_FILE]} if record.get(red_cells.QUOTE_FILE) else {}),
            **({QUOTE_CALL: record[QUOTE_CALL]} if record.get(QUOTE_CALL) else {}),
            'quote': record['quote'], 'why': customer_reason(record)}


#: The mark a corrected unit (and a turn-based cell its correction flipped) carries: the audit's own ruling —
#: ``{verdict, cause, reason, quote, anchor?}`` — the evidence the corrected verdict stands on.
AUDIT_CORRECTED = 'audit_corrected'
#: What belonged to the overturned verdict and leaves the corrected unit: the red (or pass) judge's confidence.
OVERTURNED_KEYS = ('confidence',)
#: The longest audit quote a corrected cell's page reasoning shows (a world row may be long raw data); the whole quote
#: stays in ``audit_corrected``.
EVIDENCE_MAX_CHARS = 200


def audit_evidence(record: Mapping[str, Any]) -> Dict[str, Any]:
    """The audit's own ruling on a corrected unit (:data:`AUDIT_CORRECTED`)."""
    return {k: record[k] for k in ('verdict', 'cause', 'reason', 'quote', 'quote_turn', red_cells.QUOTE_FILE, 'anchor') if record.get(k) is not None}


def corrected_reasoning(record: Mapping[str, Any]) -> str:
    """A corrected cell's ``reasoning``: :func:`customer_reason` and the audit's quote it rests on, verbatim."""
    quote = red_cells.clip(str(record.get('quote') or ''), EVIDENCE_MAX_CHARS)
    said = customer_reason(record)
    return f'{said} Evidence: "{quote}"' if quote else said


def customer_reason(record: Mapping[str, Any]) -> str:
    """The customer-facing reason a corrected cell (and its finding) carries — :func:`build_run_json.correction_reason`
    by the record's correction and the rubric clause its row cites (``clause``). A restored verdict keeps the judge's own
    reasoning and never reaches here."""
    return correction_reason(str(record.get('corrected')), record.get('clause'))


def _goal_axis(cells: Mapping[str, Any], owners: Sequence[str]) -> Optional[str]:
    """The goal owners' reading, the backend's rule: any conclusive red fails; a pass needs one conclusive owner."""
    conclusive = False
    for name in owners:
        cell = cells.get(name)
        if not isinstance(cell, Mapping):
            continue
        if red_cells.is_red(cell):
            return 'fail'
        conclusive = conclusive or cell.get('status') == 'success'
    return 'pass' if conclusive else None


def apply_corrections(item_cells: Mapping[str, Any], verdict_block: Optional[Mapping[str, Any]],
                      records: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(cells, verdict block, changed entries)`` — ``records`` (``{policy, turn_idx, corrected, cause, quote,
    quote_turn?, reason, propagated_from?}``) applied to one item. Pure: the inputs are not mutated.

    A red corrected to ``pass`` / ``not_applicable`` is no longer violated (``not_applicable`` also goes
    ``inconclusive`` with the N/A reason); a pass corrected to ``fail`` is violated and cites the audit's quote. Either
    carries the audit's reason; a turn-based cell's ``violated`` is recomputed from its turns (no conclusive turn left
    → the cell is N/A). The verdict block, as the backend builds it: a policy with no red unit left leaves the violation
    lists; a policy with a NEW red joins the lists of its cell's ``tier`` (:data:`TIER_LISTS`), unless it owns a
    ``target_judge`` goal; a ``target_judge`` goal is re-read from its owners. Nothing here passes or scores the
    conversation: the block carries no ``passed`` or ``score`` (the backend serves none), and the SDK scores the corrected
    verdicts once the writer applies them (``verdict_events.refresh_scores``).

    A ``restored`` record (``{policy, turn_idx, corrected: restored, violated, reason}``: an overrule the backend applied
    on a gate that set the unit aside) makes the set-aside unit conclusive at its raw ``violated``, keeping the judge's
    reasoning; a restored red is a new red as above.
    """
    cells = copy.deepcopy(dict(item_cells))
    changed: List[Dict[str, Any]] = []
    touched, failed = set(), set()
    for record in records:
        policy, turn_idx = str(record['policy']), record.get('turn_idx')
        restoring = record['corrected'] == RESTORED
        failing = record['corrected'] == FAIL or (restoring and bool(record['violated']))
        cell = cells.get(policy)
        if not isinstance(cell, dict):
            continue
        unit = dict(red_cells.units(cell)).get(turn_idx)
        if not isinstance(unit, dict) or not (_set_aside(unit) if restoring else red_cells.is_pass(unit) if failing else red_cells.is_red(unit)):
            continue
        old = _correct_unit(unit, record)
        if turn_idx is not None:
            if isinstance(cell.get('findings'), list) and not restoring:
                cell['findings'] = [f for f in cell['findings'] if not (isinstance(f, dict) and f.get('turn_idx') == turn_idx)]
            if failing and not restoring:
                cell['findings'] = [*(cell.get('findings') or []), _finding(record)]
            turns = [t for t in cell.get('turns') or [] if isinstance(t, dict)]
            was_red = bool(cell.get('violated'))
            cell['violated'] = any(red_cells.is_red(t) for t in turns)
            if not any(t.get('status') == 'success' for t in turns):
                cell['status'], cell['inconclusive_reason'] = 'inconclusive', NA_REASON
            elif restoring and cell.get('status') != 'success':
                cell['status'] = 'success'
                cell.pop('inconclusive_reason', None)
            if cell['violated'] != was_red:
                for key in ('label', 'value'):
                    if isinstance(cell.get(key), bool):
                        cell[key] = not cell['violated']
                if not restoring:
                    cell['reasoning'] = corrected_reasoning(record)
                    cell[AUDIT_CORRECTED] = audit_evidence(record)
                    for key in OVERTURNED_KEYS:
                        cell.pop(key, None)
                if not cell['violated']:
                    cell.pop('findings', None)
        touched.add(policy)
        if failing:
            failed.add(policy)
        changed.append({
            'policy_name': policy, 'turn_idx': turn_idx, 'old': old,
            'new': {'status': unit.get('status'), 'violated': failing},
            **{k: record[k] for k in ('verdict', 'corrected', 'cause', 'quote', 'quote_turn', red_cells.QUOTE_FILE, QUOTE_CALL, 'anchor', 'reason', 'clause', 'propagated_from', 'gate_id')
           if record.get(k) is not None},
        })
    if not isinstance(verdict_block, Mapping):
        return cells, None, changed
    block = copy.deepcopy(dict(verdict_block))
    cleared = {p for p in touched if not any(red_cells.is_red(u) for _, u in red_cells.units(cells.get(p)))}
    for key in VIOLATION_LISTS:
        if isinstance(block.get(key), list):
            block[key] = [name for name in block[key] if str(name) not in cleared]
    owners = [str(o) for o in block.get('target_judge_owners') or []]
    goal_owned = block.get('goal_source') == 'target_judge'
    for policy in sorted(failed - cleared - (set(owners) if goal_owned else set())):
        tier = (cells.get(policy) or {}).get('tier')
        for key in TIER_LISTS.get(tier if isinstance(tier, str) else None, TIER_LISTS[None]):
            names = [str(n) for n in block.get(key) or []]
            block[key] = names if policy in names else [*names, policy]
    if goal_owned and failed & set(owners) and _goal_axis(cells, owners) == 'fail':
        block['goal_achieved'] = False
        if isinstance(block.get('goal_disagreement'), dict):
            block['goal_disagreement']['target_judge'] = 'fail'
    elif goal_owned and block.get('goal_achieved') is False and touched & set(owners):
        judged = _goal_axis(cells, owners)
        if judged != 'fail':
            block['goal_achieved'] = True if judged == 'pass' else None
            disagreement = block.get('goal_disagreement')
            if isinstance(disagreement, dict) and (judged is None or disagreement.get('deterministic') == judged):
                block.pop('goal_disagreement')
            elif isinstance(disagreement, dict):
                disagreement['target_judge'] = judged
    return cells, block, changed


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _rejection(run: red_cells.Run, unit: red_cells.Unit, row: Mapping[str, Any], d: Direction) -> Optional[str]:
    """Why ``row`` may not correct ``unit`` in direction ``d``, or None when it may."""
    item, policy, turn_idx = unit
    cause, corrected = row.get('defect_surface'), correction(row, d)
    if cause not in red_cells.CAUSES:
        return f'defect_surface {cause!r} is none of {", ".join(red_cells.CAUSES)}'
    if corrected not in d.corrections:
        return f'corrected {corrected!r} is none of {", ".join(d.corrections)}'
    if not d.recorded(run.unit(item, policy, turn_idx) or {}):
        return f'not {d.state} in the current cells: there is nothing for a {d.verdict} to correct'
    miss = red_cells.quote_miss(run, item, row, turn_idx, tool_data=d.verdict == red_cells.FALSE_RED)
    if miss and d.verdict == red_cells.FALSE_RED and cell_anchored(run, item, row):
        return None
    return miss


#: A FALSE RED whose quote no reply, written file or tool answer anchors (the user's message, a dataset row of the world)
#: is corrected on its cell itself: ``anchor: cell`` (CELL ANCHOR).
CELL_ANCHOR = 'cell'


def cell_anchored(run: red_cells.Run, item: str, row: Mapping[str, Any]) -> bool:
    """Whether a FALSE RED row's quote, anchored by no reply, file or tool answer, is verbatim in the item's own records —
    the conversation's own messages, world, inputs and tool answers (:func:`_conversation_records`) — and in none of the
    agent's replies (a reply quote at the wrong turn is the row's anchor to fix, never a cell correction)."""
    quote = str(row.get('quote') or '').strip()
    if not quote or item not in run.dirs:
        return False
    if any(_reply_holds(run, item, turn, quote) for turn in _evidence_turns(run, item, None)):
        return False  # a reply holds it: the row's anchor is fixable (its turn), never a cell correction
    return red_cells.holds_quote(red_cells.string_leaves(_conversation_records(run, item)), quote)


def _conversation_records(run: red_cells.Run, item: str) -> List[Any]:
    """What the conversation itself holds besides the agent's replies: each turn's user message and its non-assistant
    messages (tool results), the whole world record (each turn's ``turn_world``, the whole ``final_world_state.json``), the
    inputs it was handed (``injected_inputs``, ``pipeline_io.inputs``) and every turn's tool answers as recorded
    (:func:`red_cells.recorded_tool_answers`), each read field by field. Never the judge's reasoning, the eval block, run
    metadata or a tool call's ``args``."""
    transcript = run.transcript(item)
    records: List[Any] = []
    for turn in transcript.get('turns') or []:
        if isinstance(turn, Mapping):
            records += [turn.get('user_message'), turn.get('turn_world')]
            records += [m.get('content') for m in turn.get('turn_messages') or []
                        if isinstance(m, Mapping) and m.get('role') != 'assistant' and m.get('type') != 'ai']
    io = transcript.get('pipeline_io') if isinstance(transcript.get('pipeline_io'), Mapping) else {}
    records += [io.get('inputs'), transcript.get('injected_inputs'), _read_json(run.dirs[item] / 'final_world_state.json')]
    return records + red_cells.recorded_tool_answers(run, item, None)


def _reply_holds(run: red_cells.Run, item: str, turn_idx: int, quote: str) -> bool:
    transcript = run.transcript(item)
    if red_cells.is_pipeline(transcript):
        replies = red_cells.whole_run_replies(transcript) if turn_idx == red_cells.PIPELINE_TURN else []
    else:
        turn = next((t for t in transcript.get('turns') or [] if isinstance(t, Mapping) and t.get('turn_idx') == turn_idx), None)
        replies = record_finding._replies(dict(turn)) if turn is not None else []  # noqa: SLF001 - the quote check's own reply reader
    needle = ' '.join(quote.split())
    return any(needle in ' '.join(reply.split()) for reply in replies)


def _evidence_turns(run: red_cells.Run, item: str, turn_idx: Optional[int]) -> List[int]:
    """The turns a sibling unit's quote may sit at: its own turn, else every turn of a scenario-scoped cell."""
    return [turn_idx] if turn_idx is not None else red_cells.turn_idxs(run.transcript(item))


def propagate(run: red_cells.Run, ruled: Mapping[red_cells.Unit, Any],
              explicit: Sequence[Tuple[red_cells.Unit, Record]]) -> Tuple[List[Tuple[red_cells.Unit, Record]], List[Dict[str, Any]]]:
    """``(propagated, conflicts)`` — each accepted ruling carried to the sibling trials (same scenario, same policy) of
    its unit whose unit is in the same recorded state, that no row rules, and whose reply holds the ruling's quote
    (at least :data:`PROPAGATE_MIN_QUOTE_CHARS`). A sibling a row ruled the other way on that same quote is a conflict."""
    taken = {unit for unit, _ in explicit}
    propagated: List[Tuple[red_cells.Unit, Record]] = []
    conflicts: List[Dict[str, Any]] = []
    for (item, policy, turn_idx), record in explicit:
        if record.get('anchor') == CELL_ANCHOR or len(' '.join(record['quote'].split())) < PROPAGATE_MIN_QUOTE_CHARS:
            continue
        d = DIRECTIONS[red_cells.FALSE_PASS if record['corrected'] == FAIL else red_cells.FALSE_RED]
        source = item + ('' if turn_idx is None else f' T{turn_idx}')
        for sibling in sorted(run.dirs):
            if sibling == item or red_cells.item_scenario(sibling) != red_cells.item_scenario(item):
                continue
            for t, unit in red_cells.units(run.cells(sibling)[0].get(policy)):
                key = (sibling, policy, t)
                if key in taken or not d.recorded(unit):
                    continue
                if record.get(red_cells.QUOTE_FILE):
                    at = None
                    path = next((p for p, versions in sorted(run.files(sibling).items()) if red_cells.holds_quote(versions, record['quote'])), None)
                    if path is None:
                        continue
                else:
                    path = None
                    at = next((k for k in _evidence_turns(run, sibling, t) if _reply_holds(run, sibling, k, record['quote'])), None)
                    if at is None:
                        continue
                names = {v for v, _ in ruled.get(key, [])}
                if names & set(d.keeps):
                    conflicts.append({'item': sibling, 'policy': policy, 'turn_idx': t, 'ruled': sorted(names & set(d.keeps)),
                                      'against': {'item': item, 'turn_idx': turn_idx, 'verdict': d.verdict}, 'quote': record['quote']})
                if names:
                    continue
                taken.add(key)
                propagated.append((key, {**record, 'turn_idx': t, 'quote_turn': at if t is None else None, red_cells.QUOTE_FILE: path,
                                         'propagated_from': source}))
    return propagated, conflicts


def accepted_records(run: red_cells.Run, rows: Sequence[Mapping[str, Any]]
                     ) -> Tuple[Dict[str, List[Record]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``({item: [record]}, skipped, conflicts)`` — every FALSE RED / FALSE PASS unit the rows may correct, each
    propagated to its unruled siblings (:func:`propagate`), and why each other one may not."""
    ruled, skipped = red_cells.ruled_units(run, rows)
    explicit: List[Tuple[red_cells.Unit, Record]] = []
    for unit in sorted(ruled, key=red_cells._unit_key):  # noqa: SLF001 - the units' one sort order
        item, policy, turn_idx = unit
        # A tie-break ruling (`red_cell_audit.TIE_BREAK`) supersedes every earlier row of its unit: it was asked because two
        # rulings disagreed, so the row it overrules is never "a ruling the other way".
        ruled[unit] = [entry for entry in ruled[unit] if rows[entry[1]].get(red_cell_audit.TIE_BREAK)] or ruled[unit]
        names = {v for v, _ in ruled[unit]}
        for verdict, d in DIRECTIONS.items():
            if verdict not in names:
                continue
            where = {'item': item, 'policy': policy, 'turn_idx': turn_idx}
            keeping = sorted(names & set(d.keeps))
            if keeping:
                skipped.append({**where, 'why': f'the audit also ruled this unit {" and ".join(keeping)}; that ruling never flips'})
                continue
            row = rows[next(i for v, i in reversed(ruled[unit]) if v == verdict)]  # a later row wins its unit
            why = _rejection(run, unit, row, d)
            if why:
                skipped.append({**where, 'why': why})
                continue
            anchored = red_cells.quote_miss(run, item, row, turn_idx, tool_data=verdict == red_cells.FALSE_RED) is None
            path = (str(row.get(red_cells.QUOTE_FILE) or '').strip() or None) if anchored else None
            turn = None if path or not anchored else red_cells.evidence_turn(run, item, row, turn_idx)
            call = None if path or turn is None or _reply_holds(run, item, turn, str(row['quote'])) else red_cells.cited_call(run.transcript(item), turn, str(row['quote']))
            explicit.append((unit, {
                **({} if anchored else {'anchor': CELL_ANCHOR}),
                'policy': policy, 'turn_idx': turn_idx, 'verdict': red_cell_audit.BACKEND_VERDICTS[verdict],
                'corrected': correction(row, d), 'cause': row['defect_surface'],
                'quote': str(row['quote']).strip(), 'quote_turn': turn if turn_idx is None else None, red_cells.QUOTE_FILE: path,
                QUOTE_CALL: (call or {}).get('call_id'),
                'reason': ' '.join(str(row.get('reason') or row.get('note') or row.get('clause') or '').split()),
                'clause': ' '.join(str(row.get('owner_clause') or row.get('clause') or '').split()) or None,
            }))
    propagated, conflicts = propagate(run, ruled, explicit)
    accepted: Dict[str, List[Record]] = {}
    for (item, _, _), record in [*explicit, *propagated]:
        accepted.setdefault(item, []).append(record)
    return accepted, skipped, conflicts


def skipped_step_rulings(run: red_cells.Run, rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[red_cells.Unit]]:
    """``{item: [unit]}`` — the units a row ruled ``TRUE RED`` (and none ruled ``FALSE RED``): the candidates
    :func:`confirm_skipped_steps` reads against each item's goal."""
    ruled, _ = red_cells.ruled_units(run, rows)
    out: Dict[str, List[red_cells.Unit]] = {}
    for unit in sorted(ruled, key=red_cells._unit_key):  # noqa: SLF001 - the units' one sort order
        names = {v for v, _ in ruled[unit]}
        if red_cells.TRUE_RED in names and red_cells.FALSE_RED not in names:
            out.setdefault(unit[0], []).append(unit)
    return out


def confirm_skipped_steps(cells: Mapping[str, Any], block: Mapping[str, Any], confirmed: Sequence[red_cells.Unit]
                          ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """``(verdict block, changed entries)`` — a goal left undecided because the agent never took a step its target
    requires, decided as MISSED once the audit confirms the owner's red.

    Applies when the block's ``target_premise_unshown`` is one of :data:`AGENT_SKIPPED_PREMISES`, its goal is undecided
    (``goal_achieved`` null), and a ``confirmed`` unit (ruled ``TRUE RED``) is a goal owner's that is red in ``cells``:
    ``goal_achieved`` goes ``false``, and ``target_premise_unshown`` gives way to
    ``required_step_skipped: {owner, premise}``. Every reader of the verdicts — run.json's row, the trials refold, the
    report's goal counts — then counts it as the failure it is, never as a goal reached or "never came up"."""
    out = copy.deepcopy(dict(block))
    premise = out.get(TARGET_PREMISE_UNSHOWN_KEY)
    owners = {str(o) for o in out.get('target_judge_owners') or []}
    if premise not in AGENT_SKIPPED_PREMISES or out.get('goal_achieved') is not None:
        return out, []
    hits = [(policy, turn_idx) for _, policy, turn_idx in confirmed
            if policy in owners and red_cells.is_red(dict(red_cells.units(cells.get(policy))).get(turn_idx) or {})]
    if not hits:
        return out, []
    policy, turn_idx = hits[0]
    out.pop(TARGET_PREMISE_UNSHOWN_KEY)
    out.update({'goal_achieved': False, REQUIRED_STEP_SKIPPED_KEY: {'owner': policy, 'premise': premise}})
    state = {'status': 'success', 'violated': True}
    return out, [{'policy_name': policy, 'turn_idx': turn_idx, 'old': state, 'new': state, 'confirmed': red_cells.TRUE_RED,
                  'goal': {'old': None, 'new': False, 'because': REQUIRED_STEP_SKIPPED_KEY}}]


def restoring_gates() -> frozenset:
    """The backend gates that SET A RAW VERDICT ASIDE: those whose evidence carries :data:`RAW_VERDICT_KEY`, read off the
    vendored ``gate_authority`` contract. An overrule the backend applies on one restores that verdict."""
    from plurai_contracts import contract  # noqa: PLC0415 - the vendored gate table, read only when a report is given

    gates = (contract('gate_authority') or {}).get('gates') or {}
    return frozenset(name for name, gate in gates.items() if isinstance(gate, Mapping) and RAW_VERDICT_KEY in (gate.get('evidence_keys') or ()))


def applied_restorations(reports: Sequence[Any]) -> List[Dict[str, Any]]:
    """Every overrule a saved ``suite_diagnostics`` envelope (or bare report) records as ``outcome: applied`` on a
    :func:`restoring_gates` gate: ``{gate_id, fingerprint, thread_id, policy, turn_idx, violated, reason}``, once per
    fingerprint across ``reports``."""
    gates = restoring_gates()
    found: Dict[str, Dict[str, Any]] = {}
    for doc in reports:
        report = doc.get('detail') if isinstance(doc, Mapping) and isinstance(doc.get('detail'), Mapping) else doc
        authority = report.get('gate_authority') if isinstance(report, Mapping) else None
        for entry in (authority if isinstance(authority, Mapping) else {}).get('overrules') or []:
            if not isinstance(entry, Mapping) or entry.get('outcome') != 'applied':
                continue
            overrule = entry.get('overrule') if isinstance(entry.get('overrule'), Mapping) else {}
            finding = entry.get('finding') if isinstance(entry.get('finding'), Mapping) else {}
            gate = overrule.get('gate_id') or finding.get('gate_id')
            if gate not in gates:
                continue
            evidence = finding.get('evidence') if isinstance(finding.get('evidence'), Mapping) else {}
            turn = evidence.get('turn_idx')
            key = str(overrule.get('fingerprint') or finding.get('fingerprint') or len(found))
            found[key] = {'gate_id': gate, 'fingerprint': key, 'thread_id': evidence.get('thread_id'),
                          'policy': evidence.get('policy') or finding.get('subject'), 'turn_idx': turn if red_cells._is_turn(turn) else None,  # noqa: SLF001
                          'violated': evidence.get(RAW_VERDICT_KEY), 'reason': ' '.join(str(overrule.get('reason') or '').split())}
    return list(found.values())


def _thread_items(run: red_cells.Run) -> Dict[str, str]:
    """``{thread_id: item}`` off each item's transcript ``metadata``."""
    out: Dict[str, str] = {}
    for item in run.dirs:
        thread = (run.transcript(item).get('metadata') or {}).get('thread_id')
        if isinstance(thread, str) and thread:
            out[thread] = item
    return out


def restoration_state(run: red_cells.Run, restoration: Mapping[str, Any], threads: Mapping[str, str]) -> Tuple[Optional[str], str]:
    """``(item, state)`` of one applied restoration on this run: ``landed`` (the current unit is conclusive at the raw
    verdict), ``pending`` (still set aside), or why it cannot land here."""
    item = threads.get(str(restoration.get('thread_id')))
    if item is None:
        return None, f'thread {restoration.get("thread_id")!r} is no item of this run'
    if not isinstance(restoration.get('violated'), bool) or not restoration.get('policy'):
        return item, f'the overruled finding names no policy or no {RAW_VERDICT_KEY}, so there is no verdict to restore'
    unit = run.unit(item, str(restoration['policy']), restoration.get('turn_idx'))
    if unit is None:
        return item, f'{item} has no {restoration["policy"]} unit at turn {restoration.get("turn_idx")}'
    if unit.get('status') == 'success':
        if bool(unit.get('violated')) == restoration['violated']:
            return item, 'landed'
        return item, 'conclusive at the OTHER verdict: something after the overrule re-judged it'
    return item, 'pending' if _set_aside(unit) else 'the unit carries no raw verdict to restore'


def restored_records(run: red_cells.Run, reports: Sequence[Any]) -> Tuple[Dict[str, List[Record]], List[Dict[str, Any]]]:
    """``({item: [restored record]}, skipped)`` — every applied restoration (:func:`applied_restorations`) whose unit is
    still set aside here, as an :func:`apply_corrections` record; one that already landed is neither."""
    threads = _thread_items(run)
    accepted: Dict[str, List[Record]] = {}
    skipped: List[Dict[str, Any]] = []
    for restoration in applied_restorations(reports):
        item, state = restoration_state(run, restoration, threads)
        if state == 'landed':
            continue
        if state != 'pending' or item is None:
            skipped.append({'item': item, 'policy': restoration.get('policy'), 'turn_idx': restoration.get('turn_idx'),
                            'why': f'{restoration["gate_id"]} overrule: {state}'})
            continue
        accepted.setdefault(item, []).append({
            'policy': str(restoration['policy']), 'turn_idx': restoration.get('turn_idx'), 'corrected': RESTORED,
            'violated': restoration['violated'], 'reason': restoration['reason'], 'gate_id': restoration['gate_id'],
        })
    return accepted, skipped


def not_landed(run: red_cells.Run, reports: Sequence[Any]) -> List[Dict[str, Any]]:
    """The applied restorations whose unit this run's CURRENT verdicts do not carry at the raw verdict — an overrule the
    backend applied that never reached these scores."""
    threads = _thread_items(run)
    missing = []
    for restoration in applied_restorations(reports):
        item, state = restoration_state(run, restoration, threads)
        if state != 'landed':
            missing.append({'item': item, 'thread_id': restoration.get('thread_id'), 'policy': restoration.get('policy'),
                            'turn_idx': restoration.get('turn_idx'), 'gate_id': restoration['gate_id'], 'why': state})
    return missing


def _read_report(path: Path) -> Any:
    doc = _read_json(path)
    if not isinstance(doc, dict):
        raise ValueError(f'{path} is not a saved suite_diagnostics envelope or report')
    return doc


def _current_state(doc: Mapping[str, Any], policy: str, turn_idx: Optional[int]) -> Optional[str]:
    """The state the item's verdicts hold for one unit: the eval's own state, or the state of its turn ``turn_idx``."""
    entry = (doc.get('evals') or {}).get(policy)
    if not isinstance(entry, Mapping):
        return None
    if turn_idx is None:
        return entry.get('state')
    cell = entry.get('cell') if isinstance(entry.get('cell'), Mapping) else {}
    turn = next((t for t in cell.get('turns') or [] if isinstance(t, Mapping) and t.get('turn_idx') == turn_idx), None)
    return None if turn is None else turn.get('state')


#: The V-S correction event's facts beside the unit (``plurai verdicts apply-correction``); nothing else is sent.
#: ``quote_file`` (optional) rides only when the ruling's quote is anchored on a file the agent wrote; ``call_id`` only
#: when its evidence is a tool call the agent made; ``clause`` only when the audit row cites a rubric clause.
EVENT_FACTS = ('verdict', 'cause', 'quote', 'quote_turn', 'reason', 'propagated_from')
OPTIONAL_EVENT_FACTS = (red_cells.QUOTE_FILE, QUOTE_CALL, 'clause')


def _unit_event(doc: Mapping[str, Any], entry: Mapping[str, Any], cell: Mapping[str, Any], goal: Mapping[str, Any]) -> Dict[str, Any]:
    """One ``apply_corrections`` / ``confirm_skipped_steps`` entry as ONE correction event: the unit's ``old`` state in
    ``doc`` (the item's verdicts as the previous unit left them), its ``new`` state, the corrected ``cell`` and ``goal``.
    ``history_len`` is added when it is sent (``verdict_events``), off the file as it is then."""
    policy, turn_idx = entry['policy_name'], entry.get('turn_idx')
    old = _current_state(doc, policy, turn_idx)
    if entry.get('confirmed'):
        new, verdict = old, red_cell_audit.BACKEND_VERDICTS[entry['confirmed']]
    else:
        new = NOT_APPLICABLE if entry.get('corrected') == NOT_APPLICABLE else FAIL if entry['new']['violated'] else PASS
        verdict = entry.get('verdict')
    return {'source': 'correction', 'eval': policy, 'turn_idx': turn_idx, 'old': {'state': old}, 'new': {'state': new},
            'cell': copy.deepcopy(dict(cell)), 'goal': copy.deepcopy(dict(goal)),
            **{key: entry.get(key) for key in EVENT_FACTS}, **{key: entry[key] for key in OPTIONAL_EVENT_FACTS if entry.get(key)},
            'verdict': verdict}


def correction_events(doc: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                      confirmed: Sequence[red_cells.Unit] = ()) -> Tuple[List[Dict[str, Any]], str]:
    """``(the item's correction events, '')`` — ONE per corrected unit, in order, each built on the verdicts the previous
    one leaves (so a second turn of the same cell carries the first turn's flip, and each ``old`` is the unit's state when
    its call lands); ``([], why)`` when the item has no goal block to correct or no ruled unit is in the state its ruling
    corrects, ``([], '')`` when nothing changes."""
    import convert_verdicts  # noqa: PLC0415 - the writer's cell -> (state, reason) stamp, to step the item's verdicts forward

    current = copy.deepcopy(dict(doc))
    current['evals'] = dict(current.get('evals') or {})
    cells = {name: entry['cell'] for name, entry in current['evals'].items() if isinstance(entry, Mapping) and isinstance(entry.get('cell'), dict)}
    block = doc.get('goal')
    if not isinstance(block, dict):
        return [], 'the item has no goal block to correct'
    events: List[Dict[str, Any]] = []
    for record in records:
        after, block_after, changed = apply_corrections(cells, block, [record])
        if not changed:
            continue
        entry = changed[0]
        policy = entry['policy_name']
        events.append(_unit_event(current, entry, after[policy], block_after or {}))
        cells, block = after, block_after or block
        current['evals'][policy] = convert_verdicts.entry_of(cells[policy])
        cells[policy] = current['evals'][policy]['cell']
    block, decided = confirm_skipped_steps(cells, block, confirmed)
    events.extend(_unit_event(current, entry, cells[entry['policy_name']], block) for entry in decided)
    if not events:
        return [], 'no ruled unit is in the state its ruling corrects' if records else ''
    return events, ''


def build(run: red_cells.Run, rows: Sequence[Mapping[str, Any]], reports: Sequence[Any] = ()) -> Dict[str, Any]:
    """The run's corrections, ONE event per corrected unit (``events``: ``{item, dir, event}``, an item's in order), and
    what was skipped and why. A sibling trial's unit is its own event against that item, ``propagated_from`` set. Sends
    nothing: :func:`send_events` hands the events to the writer."""
    import verdicts_file  # noqa: PLC0415 - the one reader of a conversation's verdicts

    accepted, skipped, conflicts = accepted_records(run, rows)
    restored, unrestorable = restored_records(run, reports)
    for item, records in restored.items():
        accepted.setdefault(item, []).extend(records)
    skipped.extend(unrestorable)
    confirmations = skipped_step_rulings(run, rows)
    corrected: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    for item in sorted({*accepted, *confirmations}):
        records = accepted.get(item, [])
        d = run.dirs[item]
        unit_events, why = correction_events(verdicts_file.read(d), records, confirmations.get(item, ()))
        if not unit_events:
            skipped.extend({'item': item, 'policy': r['policy'], 'turn_idx': r['turn_idx'], 'why': why} for r in records if why)
            continue
        events.extend({'item': item, 'dir': str(d), 'event': event} for event in unit_events)
        corrected.extend({'item': item, 'policy': r['policy'], 'turn_idx': r['turn_idx'], 'corrected': r['corrected'],
                          **{k: r[k] for k in ('cause', 'gate_id', 'propagated_from') if r.get(k)}}
                         for r in records)
        if isinstance(skip := unit_events[-1]['goal'].get(REQUIRED_STEP_SKIPPED_KEY), dict):
            steps.append({'item': item, **skip})
    return {'run_dir': str(run.out), 'corrected': corrected, 'required_steps_skipped': steps, 'events': events,
            'skipped': skipped, 'conflicts': conflicts}


def send_events(events: Sequence[Mapping[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """``(applied items, refused [{item, exit, why}])`` — each event handed to ``plurai verdicts apply-correction``, one
    call per unit (``verdict_events.send`` re-reads the item's ``history_len`` for each). An item's later units are not
    sent once one of its calls is refused: each was built on the verdicts the refused one would have left."""
    applied: List[str] = []
    refused: List[Dict[str, Any]] = []
    stopped: Dict[str, str] = {}
    for entry in events:
        item = entry['item']
        if item in stopped:
            refused.append({'item': item, 'exit': None, 'why': f'not sent: an earlier unit of this item was refused ({stopped[item]})'})
            continue
        code, message = verdict_events.send('apply-correction', Path(entry['dir']), entry['event'])
        if code == 0:
            if item not in applied:
                applied.append(item)
        else:
            stopped[item] = message
            refused.append({'item': item, 'exit': code, 'why': message})
    return applied, refused


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def uncorrected(run: red_cells.Run, rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every unit an audit row ruled ``FALSE RED`` / ``FALSE PASS`` (and no row ruled the other way) that the run's
    CURRENT verdicts still count as recorded — red for a FALSE RED, a pass for a FALSE PASS — with why ``build`` could
    not correct it. Empty is C8 holding: every audited correction reached the scores."""
    ruled, _ = red_cells.ruled_units(run, rows)
    out: List[Dict[str, Any]] = []
    for unit in sorted(ruled, key=red_cells._unit_key):  # noqa: SLF001 - the units' one sort order
        item, policy, turn_idx = unit
        # A tie-break ruling (`red_cell_audit.TIE_BREAK`) supersedes every earlier row of its unit: it was asked because two
        # rulings disagreed, so the row it overrules is never "a ruling the other way".
        ruled[unit] = [entry for entry in ruled[unit] if rows[entry[1]].get(red_cell_audit.TIE_BREAK)] or ruled[unit]
        names = {v for v, _ in ruled[unit]}
        for verdict, d in DIRECTIONS.items():
            if verdict not in names or names & set(d.keeps) or not d.recorded(run.unit(item, policy, turn_idx) or {}):
                continue
            row = rows[next(i for v, i in reversed(ruled[unit]) if v == verdict)]
            out.append({'item': item, 'policy': policy, 'turn_idx': turn_idx, 'verdict': verdict,
                        'why': _rejection(run, unit, row, d) or 'build was not run since this row was saved'})
    return out


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    return _client_handshake.refusal_main(lambda: _main(argv))


def _main(argv: Optional[Sequence[str]]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('build')
    p.add_argument('--run-dir', required=True, help='the FINAL run dir')
    p.add_argument('--audit', action='append', default=[], help="the final run's red-cell audit rows (repeatable); default: the run's own audit")
    p.add_argument('--report', action='append', default=[], help="this run's saved suite_diagnostics envelope (repeatable): its applied "
                   'overrules on a gate that set a verdict aside are restored')
    p.add_argument('--out', help='write the summary JSON here instead of stdout')
    p = sub.add_parser('check')
    p.add_argument('--run-dir', required=True, help='the FINAL run dir')
    p.add_argument('--audit', action='append', default=[], help="the run's audit rows (repeatable); default: the run's own audit")
    p.add_argument('--report', action='append', default=[], help='the same envelope(s): exit 3 when an applied restoring overrule did not land')
    args = ap.parse_args(argv)
    try:
        run = red_cells.Run(Path(args.run_dir))
        reports = [_read_report(Path(r)) for r in args.report]
        if args.cmd == 'build':
            result = build(run, red_cells.audit_rows(run, [Path(a) for a in args.audit]), reports)
            try:
                result['applied'], result['refused'] = send_events(result.pop('events'))
            except verdict_events.WriterUnavailable as exc:
                print(f'verdict_corrections: {exc} — no correction was applied', file=sys.stderr)
                return EXIT_WRITER_REFUSED
            text = json.dumps(result, indent=1, ensure_ascii=False)
            if args.out:
                Path(args.out).write_text(text + '\n', encoding='utf-8')
            else:
                print(text)
            for row in result['skipped']:
                print(f'verdict_corrections: skipped {row.get("item", row.get("row"))} {row.get("policy", "")} — {row["why"]}', file=sys.stderr)
            for row in result['conflicts']:
                print(f'verdict_corrections: conflict {row["item"]} {row["policy"]} — ruled {" and ".join(row["ruled"])}, '
                      f'while {row["against"]["item"]} quoting the same reply was ruled {row["against"]["verdict"]}', file=sys.stderr)
            for row in result['refused']:
                print(f'verdict_corrections: REFUSED {row["item"]} (writer exit {row["exit"]}): {row["why"]} — run `build` again', file=sys.stderr)
            return EXIT_WRITER_REFUSED if result['refused'] else 0
        missing = not_landed(run, reports)
        try:
            counted = uncorrected(run, red_cells.audit_rows(run, [Path(a) for a in args.audit]))
        except red_cell_audit.AuditMissing as exc:
            counted = []
            print(f'verdict_corrections: {exc} — no audited correction to check', file=sys.stderr)
        print(json.dumps({'not_landed': missing, 'uncorrected': counted}, ensure_ascii=False))
        for row in missing:
            print(f'verdict_corrections: NOT LANDED — the backend applied a {row["gate_id"]} overrule on {row["item"] or row["thread_id"]} '
                  f'{row["policy"]} that these scores do not carry ({row["why"]}); run `build` with that envelope', file=sys.stderr)
        for row in counted:
            print(f'verdict_corrections: STILL COUNTED — {row["verdict"]} on {row["item"]} {row["policy"]}'
                  f'{"" if row["turn_idx"] is None else " T" + str(row["turn_idx"])}: {row["why"]}', file=sys.stderr)
        return EXIT_NOT_LANDED if missing else EXIT_UNCORRECTED if counted else 0
    except (ValueError, OSError) as exc:
        print(f'verdict_corrections: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
