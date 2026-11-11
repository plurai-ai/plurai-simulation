#!/usr/bin/env python3
"""Schedule the eval edit rounds an audit buys, and record each policy's FALSE-share trend across audits.

    scripts/eval_rounds.py schedule --audit <run dir | rows.json> --label <audit label> --stage <qualification|final> --loop "$ART/eval_loop.json"

TRIGGER. A policy gets a bounded edit round when its FALSE share in the audit is at least :data:`FALSE_SHARE` over at
least :data:`MIN_REDS` audited reds — whatever the causes. Audited reds are the policy's ``TRUE RED`` / ``FALSE RED`` /
``UNDECIDABLE`` units; its FALSE reds are those whose cause is the eval's own (:data:`EVAL_CAUSES`). A later row wins
its unit, so a run dir's delta-appended audit counts once per unit.

BEFORE THE FINAL RUN (``--stage qualification``) a policy whose eval-owned FALSE reds are all ``prompt`` gets no round:
judge accuracy is the optimizer's, and those rows still upload as labels. :data:`PREFINAL_PROMPT_ROUNDS_ENV` set to ``1``
restores those rounds. ``--stage final`` (or none) schedules them as before.

AFTER THE FINAL RUN (``--stage final``) ``schedule`` names no round (``edit_rounds: []``, ``postfinal_round: false``)
unless :data:`POSTFINAL_EVAL_ROUND_ENV` is ``1``; its trend points are still recorded.

CONVERGENCE. Each schedule appends one point per policy to ``policy_trend[]`` in the loop file. A point is compared with
the policy's previous point of at least :data:`MIN_REDS` reds: when that point took an edit round and the share did not
drop, this round's ``mode`` is ``rework_axis`` (re-scope the policy's axis, never another carve-out), else ``narrow``.
A strict rise after an edit round counts toward ``rises``; :data:`NON_CONVERGING_RISES` of them flag the policy
``non_converging`` (loop-internal), and it is reworked whenever it still has a FALSE red. Re-scheduling a label
replaces its points.

GUARD DECISIONS. ``guard --result <end_edit_session result> [--scope-rows <rows.json | run dir>]…`` records a committed session's guard decision
(``guard_decide.decide``) on each policy's trend entry. A REVERTED policy (its draft silenced an audited TRUE RED) gets
``reverted: true`` on its latest point and the silenced units join ``keep_red``, which every later round of that policy
carries — its draft must keep each of them red. A reverted round still counts as taken, so a share that did not drop
after it is reworked. Each NAMED-RISK item joins the entry's ``guard_risks`` (by fingerprint): internal, read by the
next audit, never a round of its own.

NEVER SCORED. ``never-scored --report <final suite_diagnostics envelope> --loop-log "$ART/loop_log.md" --run-dir <final run>``
checks each ``policy_never_scored`` finding of a FINAL report: it is landed only by a re-steer that names the policy
(a ``re-steer`` loop-log entry: a re-draw demands its premise) or by a named risk (the gate's ``named_risks``, or the
run's :data:`LOOP_RISKS_FILENAME`: ``--name-untested`` names each one the gate does not as an ``untested_premise`` risk). A loop-log entry
that ACCEPTED it is a violation either way. Exit 3 names each violation.

VOIDED. ``voided --audit <final run dir>`` reads whether the run's evidence is voided for an eval round: at least
:data:`VOIDED_SHARE` of its conversations (every trial's; a stability retry is not one) carry a ``forced`` ``ENV`` row —
a tool the world could not answer, so the agent never reached what the scenario was about. An eval edit learned on
those replies is learned on voided evidence. A row naming a ``thread_id`` other than the conversation's current attempt
is stale and not counted. It decides nothing else: no block, no record.

FLAGGED. ``flagged --run-dir <final run> --scenario-set-id <final set> --out "$ART/flagged_evals.json"`` names each eval
the run's audit ruled ``FALSE RED`` / ``FALSE PASS`` with an eval-owned cause (:data:`FLAG_CAUSES`), counted off the
audit rows, not off the corrections that applied. No eval round follows the suite's
final run, so those evals are flagged for improvement: the file carries one customer line (``line``, each eval by its
display name) and ``display_names`` (every policy's, off the workdir's ``policies_config.yaml``); a run with no audit
writes the file with no policy (exit 0), and
``flagged-line --file <it> --scenario-set-id <the set>`` prints it, rebuilt from the file's ``policies``, only for that set
(a file of another build prints nothing). Each policy also carries ``rulings`` (at most :data:`MAX_RULINGS`): the audit text of its ``rubric`` / ``type`` rows
(:data:`RULING_CAUSES`), which the NEXT build's ``/generate-evals`` folds into its eval intent — the rule repair happens
where the rule is authored, off this run's path. :data:`POSTFINAL_EVAL_ROUND_ENV` set to ``1`` (the suite's bounded round runs again) flags nothing.

``schedule`` prints ``{audit, edit_rounds: [{policy, mode, false_reds, audited_reds, false_share, causes, keep_red?}],
non_converging}``; ``guard`` prints ``{reverted: [policy, …], named_risks: <n>}``; ``voided`` prints ``{voided,
conversations, forced, share, missing}``. Without ``--loop``, ``guard`` writes the artifacts dir's ``eval_loop.json``
(``$ART``, else ``$WORKDIR/artifacts``, else the resolved workdir's).
Exit codes: 0 ok · 2 usage or unreadable input (a run with no audit included) · 3 (``never-scored``) a never-scored
policy landed otherwise; (``voided``) the run is voided.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_decide  # noqa: E402
import red_cell_audit  # noqa: E402
import release_verdict  # noqa: E402

SCHEMA = 'plurai-eval-loop/1'
#: The trigger: a policy whose FALSE share of its audited reds reaches this gets an edit round.
FALSE_SHARE = 0.30
#: Fewer audited reds than this is too thin to trigger a round or to compare a trend point against.
MIN_REDS = 3
#: The FALSE RED causes an eval edit owns; the others (target clause, scenario, env, scan fact) route elsewhere.
EVAL_CAUSES = ('prompt', 'rubric', 'harness', 'type')
RED_VERDICTS = ('TRUE RED', 'FALSE RED', 'UNDECIDABLE')
NON_CONVERGING_RISES = 2
NARROW, REWORK = 'narrow', 'rework_axis'
LOOP_FILENAME = 'eval_loop.json'
NEVER_SCORED = 'policy_never_scored'
#: The run's loop-named risks, merged into the release verdict's ``named_risks`` (``release_verdict.loop_risks``).
LOOP_RISKS_FILENAME = 'loop_named_risks.json'
EXIT_NEVER_SCORED = 3
#: The share of a run's conversations carrying a forced ENV row at which its evidence is voided for an eval round.
VOIDED_SHARE = 0.5
EXIT_VOIDED = 3
#: Set to ``1`` to schedule ``prompt``-only rounds before the final run again (one global setting).
PREFINAL_PROMPT_ROUNDS_ENV = 'PLURAI_PREFINAL_PROMPT_ROUNDS'


#: Set to ``1`` to run the suite's bounded eval round after its final run again (one global setting).
POSTFINAL_EVAL_ROUND_ENV = 'PLURAI_POSTFINAL_EVAL_ROUND'
FLAGGED_SCHEMA = 'plurai-flagged-evals/1'
#: How many flagged evals the line names before it gives the total.
FLAGGED_NAMED = 2
#: The causes that make a FALSE RED / FALSE PASS the eval's own error (``flagged``, and the labels ``red_cells.py labels``
#: uploads): the judge misapplied a right rule (``prompt``), the rule or the eval kind is wrong (``rubric``, ``type``), or
#: the scenario-goal clause it graded is (``target_clause``). A ``scenario``, ``harness``, ``env`` or ``scan_fact`` cause
#: is not a judge error: it stays in the audit and the report, never in the line or the labels.
FLAG_CAUSES = ('prompt', 'rubric', 'type', 'target_clause')
#: The causes whose ruling says the RULE or the eval kind is wrong: ``flagged`` keeps their audit text as ``rulings``,
#: which the next build's eval generation reads (``/generate-evals`` Step 1). A ``prompt`` row is the optimizer's (a label).
RULING_CAUSES = ('rubric', 'type')
MAX_RULINGS = 3
RULING_CHARS = 400


def postfinal_round() -> bool:
    """Whether the suite runs its bounded eval round after the final run (:data:`POSTFINAL_EVAL_ROUND_ENV`)."""
    return os.environ.get(POSTFINAL_EVAL_ROUND_ENV, '').strip() == '1'


def skips_prompt_only(stage: Optional[str]) -> bool:
    """Whether ``schedule`` at ``stage`` leaves a policy whose eval-owned FALSE reds are all ``prompt`` unscheduled."""
    return stage == 'qualification' and os.environ.get(PREFINAL_PROMPT_ROUNDS_ENV, '').strip() != '1'


def _verdict(row: Dict[str, Any]) -> str:
    return str(row.get('verdict') or '').strip().upper().replace('_', ' ')


def read_rows(source: Path) -> List[Dict[str, Any]]:
    """The audit's rows: a run dir's ``red_cell_audit.json``, a typed audit file, or the auditor's JSON list."""
    if source.is_dir():
        return red_cell_audit.rows(source)
    return red_cell_audit.unwrap(json.loads(source.read_text(encoding='utf-8')), source)


def tally(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``{policy: {false_reds, audited_reds, false_share, causes}}`` over each red unit's last ruling."""
    units: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        if row.get('policy') and _verdict(row) in RED_VERDICTS:
            units.pop(key := (str(row.get('item')), str(row['policy']), json.dumps(row.get('turn', row.get('turn_idx')))), None)
            units[key] = row
    out: Dict[str, Dict[str, Any]] = {}
    for (_, policy, _), row in units.items():
        entry = out.setdefault(policy, {'false_reds': 0, 'audited_reds': 0, 'causes': Counter()})
        entry['audited_reds'] += 1
        cause = str(row.get('defect_surface') or row.get('cause') or '').strip().lower()
        if _verdict(row) == 'FALSE RED' and cause in EVAL_CAUSES:
            entry['false_reds'] += 1
            entry['causes'][cause] += 1
    for entry in out.values():
        entry['false_share'] = round(entry['false_reds'] / entry['audited_reds'], 3)
        entry['causes'] = dict(sorted(entry['causes'].items()))
    return out


def _previous(points: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((p for p in reversed(points) if p['audited_reds'] >= MIN_REDS), None)


def schedule(rows: Sequence[Dict[str, Any]], label: str, loop: Dict[str, Any], skip_prompt_only: bool = False) -> Dict[str, Any]:
    """Append this audit's points to ``loop['policy_trend']`` (in place) and return the rounds it schedules; with
    ``skip_prompt_only``, a policy whose eval-owned FALSE reds are all ``prompt`` gets none."""
    trend: List[Dict[str, Any]] = loop.setdefault('policy_trend', [])
    by_policy = {t['policy']: t for t in trend}
    rounds, flagged = [], []
    for policy, now in sorted(tally(rows).items()):
        entry = by_policy.get(policy)
        if entry is None:
            entry = by_policy[policy] = {'policy': policy, 'points': [], 'rises': 0, 'non_converging': False}
            trend.append(entry)
        entry['points'] = [p for p in entry['points'] if p['audit'] != label]
        prev = _previous(entry['points']) if now['audited_reds'] >= MIN_REDS else None
        stalled = bool(prev and prev['edit_round'] and now['false_share'] >= prev['false_share'])
        rose = stalled and now['false_share'] > prev['false_share']  # type: ignore[index]
        entry['rises'] = sum(p.get('rose', False) for p in entry['points']) + rose
        entry['non_converging'] = entry['rises'] >= NON_CONVERGING_RISES
        due = now['audited_reds'] >= MIN_REDS and now['false_share'] >= FALSE_SHARE
        if entry['non_converging']:
            flagged.append(policy)
            due = due or now['false_reds'] > 0
        if skip_prompt_only and set(now['causes']) == {'prompt'}:
            due = False
        mode = REWORK if stalled or entry['non_converging'] else NARROW
        entry['points'].append({'audit': label, **now, 'edit_round': due, 'mode': mode if due else None, 'rose': rose})
        if due:
            rounds.append({'policy': policy, 'mode': mode, **now, **({'keep_red': entry['keep_red']} if entry.get('keep_red') else {})})
    loop.setdefault('audits', [])
    loop['audits'] = [a for a in loop['audits'] if a['audit'] != label] + [
        {'audit': label, 'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'edit_rounds': [r['policy'] for r in rounds]}]
    return {'audit': label, 'edit_rounds': rounds, 'non_converging': flagged}


def _trend_entry(loop: Dict[str, Any], policy: str) -> Dict[str, Any]:
    trend: List[Dict[str, Any]] = loop.setdefault('policy_trend', [])
    entry = next((t for t in trend if t['policy'] == policy), None)
    if entry is None:
        entry = {'policy': policy, 'points': [], 'rises': 0, 'non_converging': False}
        trend.append(entry)
    return entry


def record_guard(result: Dict[str, Any], loop: Dict[str, Any], scope_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Record an ``end_edit_session`` result's guard decision on ``loop['policy_trend']`` (in place)."""
    decision = guard_decide.decide(result, scope_rows)
    guard = result.get('guard') if isinstance(result.get('guard'), dict) else {}
    for policy in decision['revert']:
        entry = _trend_entry(loop, policy)
        if entry['points']:
            entry['points'][-1]['reverted'] = True
        keep = entry.setdefault('keep_red', [])
        units = ({'scenario': i.get('item'), 'turn_idx': i.get('turn')} for i in guard.get('silenced_true_reds') or []
                 if isinstance(i, dict) and i.get('policy') == policy)
        keep += [u for u in units if u not in keep]
    for item in decision['named_risks']:
        risks = _trend_entry(loop, str(item['policy'])).setdefault('guard_risks', [])
        if all(r.get('fingerprint') != item.get('fingerprint') for r in risks):
            risks.append({**item, 'session_id': result.get('session_id')})
    return {'reverted': decision['revert'], 'named_risks': len(decision['named_risks'])}


def default_loop_path() -> Path:
    import append_loop_log  # noqa: PLC0415 - the one artifacts-dir resolver

    try:
        return append_loop_log._artifacts_dir(None) / LOOP_FILENAME  # noqa: SLF001
    except SystemExit as exc:
        raise ValueError('no artifacts directory: pass --loop') from exc


def _detail(report: Dict[str, Any]) -> Dict[str, Any]:
    """A saved job envelope's ``detail``, or the report itself."""
    detail = report.get('detail')
    return detail if isinstance(detail, dict) else report


def _gate(detail: Dict[str, Any]) -> Dict[str, Any]:
    gate = detail.get('release_gate')
    return gate if isinstance(gate, dict) else {}


def never_scored_policies(report: Dict[str, Any]) -> List[str]:
    """The policies a FINAL report's ``policy_never_scored`` findings name (``locus: policy/<name>``)."""
    detail = _detail(report)
    if _gate(detail).get('stage') != 'final':
        return []
    out = []
    for f in detail.get('findings') or []:
        if isinstance(f, dict) and f.get('class') == NEVER_SCORED and str(f.get('locus') or '').startswith('policy/'):
            out.append(str(f['locus']).split('/', 1)[1])
    return sorted(set(out))


#: The owner the backend files a ``policy_never_scored`` row under when the check DID decide cells, but on none of them
#: could its planted content be confirmed to reach the agent (the backend's ``unconfirmed_premise_findings``).
UNCONFIRMED_PREMISE_LAYER = 'facts@scenario'


def unconfirmed_premise_policies(report: Dict[str, Any]) -> List[str]:
    """The :func:`never_scored_policies` whose finding is the unconfirmed-premise kind (:data:`UNCONFIRMED_PREMISE_LAYER`):
    their decided cells are no result, so the verdict must not drop them as decided."""
    named = set(never_scored_policies(report))
    return sorted({str(f['locus']).split('/', 1)[1] for f in _detail(report).get('findings') or []
                   if isinstance(f, dict) and f.get('class') == NEVER_SCORED and f.get('owning_layer') == UNCONFIRMED_PREMISE_LAYER
                   and str(f.get('locus') or '').split('/', 1)[-1] in named})


def _log_entries(text: str) -> List[tuple]:
    """``[(kind, body)]`` of a loop log: an entry is a ``- **<when>** · `<kind>`…`` line and its indented continuation."""
    entries: List[List[str]] = []
    for line in text.splitlines():
        if line.startswith('- **'):
            entries.append([line])
        elif entries and line.startswith('  '):
            entries[-1].append(line)
    out = []
    for lines in entries:
        head = lines[0]
        kind = head.split('`', 2)[1] if head.count('`') >= 2 else ''
        out.append((kind, '\n'.join(lines)))
    return out


def _names(body: str, policy: str) -> bool:
    return re.search(rf'(?<![A-Za-z0-9_]){re.escape(policy)}(?![A-Za-z0-9_])', body) is not None


def loop_risks(run_dir: Optional[Path]) -> List[Dict[str, Any]]:
    try:
        doc = json.loads((Path(run_dir) / LOOP_RISKS_FILENAME).read_text(encoding='utf-8')) if run_dir else []
    except (OSError, ValueError):
        return []
    return [r for r in doc if isinstance(r, dict)] if isinstance(doc, list) else []


def never_scored_violations(report: Dict[str, Any], log_text: str, run_dir: Optional[Path]) -> List[str]:
    """One line per never-scored policy of a final report that the loop ACCEPTED, or landed with no re-steer naming it
    and no named risk."""
    detail = _detail(report)
    named = {r.get('policy') for r in _gate(detail).get('named_risks') or [] if isinstance(r, dict)}
    named |= {r.get('policy') for r in loop_risks(run_dir)}
    entries = _log_entries(log_text)
    out = []
    for policy in never_scored_policies(report):
        if any('ACCEPTED' in body and _names(body, policy) for _, body in entries):
            out.append(f'{policy}: ACCEPTED in the loop log — a policy that scored nothing on the final run is never accepted')
        elif policy not in named and not any(kind == 're-steer' and _names(body, policy) for kind, body in entries):
            out.append(f'{policy}: scored nothing on the final run, and no re-steer demands its premise and no named risk says it is untested')
    return out


def name_untested(run_dir: Path, policies: Sequence[str], unconfirmed: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Add an ``untested_premise`` risk per policy to the run's :data:`LOOP_RISKS_FILENAME` (idempotent), named by the
    policy's display name in the run's workdir ``policies_config.yaml`` — its id only where it has none. A policy in
    ``unconfirmed`` (:func:`unconfirmed_premise_policies`) is marked ``premise_unconfirmed``: its decided cells are no
    result, so the verdict keeps it named (``release_verdict._decided``)."""
    risks = loop_risks(run_dir)
    have = {(r.get('kind'), r.get('policy')) for r in risks}
    names = release_verdict.policy_display_names(release_verdict.run_workdir(Path(run_dir)))
    risks += [{'policy': p, 'display_name': names.get(p) or p, 'kind': 'untested_premise', 'catastrophic': False, 'untested': [],
               'detail': f'{p} had no result that counts on the final run', **({'premise_unconfirmed': True} if p in unconfirmed else {})}
              for p in policies if ('untested_premise', p) not in have]
    tmp = (Path(run_dir) / LOOP_RISKS_FILENAME).with_suffix('.json.tmp')
    tmp.write_text(json.dumps(risks, indent=1) + '\n', encoding='utf-8')
    tmp.replace(Path(run_dir) / LOOP_RISKS_FILENAME)
    return risks


def voided(run_dir: Path) -> Dict[str, Any]:
    """``{voided, conversations, forced, share, missing}`` of a final run's audit (see VOIDED). ValueError when the run
    has no readable audit."""
    import red_cells  # noqa: PLC0415 - heavy sibling: only this verb reads the run's items

    run_dir = Path(run_dir)
    rows = red_cell_audit.rows(run_dir)
    items = {name: d for name, d in red_cells.run_items(run_dir) if not name.endswith('/' + red_cells.RETRY_DIRNAME)}
    forced: set = set()
    missing: Counter = Counter()
    for row in rows:
        if _verdict(row) != 'ENV' or row.get('forced') is not True or not isinstance(row.get('item'), str):
            continue
        item = red_cells.normalize_item(row['item'])
        if item not in items:
            continue
        thread = row.get(red_cell_audit.THREAD_ID)
        if thread and thread != red_cell_audit.thread_of(red_cells._read_json(items[item] / 'transcript.json')):  # noqa: SLF001
            continue
        forced.add(item)
        if row.get('missing'):
            missing[str(row['missing'])] += 1
    share = round(len(forced) / len(items), 3) if items else 0.0
    return {'voided': bool(items) and share >= VOIDED_SHARE, 'conversations': len(items), 'forced': len(forced), 'share': share,
            'missing': dict(missing.most_common())}


def flagged(run_dir: Path) -> Dict[str, Dict[str, Any]]:
    """``{policy: {false_red, false_pass, causes, display_name?}}`` — what the run's AUDIT ruled (see FLAGGED): each
    verdict unit a ``FALSE RED`` / ``FALSE PASS`` row rules with a cause in :data:`FLAG_CAUSES` (a later row wins its
    unit; a unit another row keeps — ``TRUE RED`` / ``IN SCOPE`` / ``UNDECIDABLE`` for a red, ``CONFIRMED PASS`` /
    ``UNDECIDABLE`` for a pass — is not), every trial and retry included, whether or not its correction applied.
    ``display_name`` is the policy's name in the run's workdir ``policies_config.yaml``. ValueError when the run has no
    readable audit."""
    import red_cells  # noqa: PLC0415 - heavy sibling: only this verb walks the run's items

    run = red_cells.Run(Path(run_dir))
    rows = red_cell_audit.rows(run.out)
    ruled, _ = red_cells.ruled_units(run, rows)
    keeps = {red_cells.FALSE_RED: set(red_cells.KEEPS), red_cells.FALSE_PASS: {red_cells.CONFIRMED_PASS, red_cells.UNDECIDABLE}}
    out: Dict[str, Dict[str, Any]] = {}
    for (_, policy, _), rulings in sorted(ruled.items(), key=lambda kv: red_cells._unit_key(kv[0])):  # noqa: SLF001
        names = {v for v, _ in rulings}
        for verdict, kind in ((red_cells.FALSE_RED, 'false_red'), (red_cells.FALSE_PASS, 'false_pass')):
            if verdict not in names or names & keeps[verdict]:
                continue
            row = rows[next(i for v, i in reversed(rulings) if v == verdict)]
            cause = str(row.get('defect_surface') or row.get('cause') or '').strip().lower()
            if cause not in FLAG_CAUSES:
                continue
            entry = out.setdefault(str(policy), {'false_red': 0, 'false_pass': 0, 'causes': Counter()})
            entry[kind] += 1
            entry['causes'][cause] += 1
            text = ' '.join(str(row.get('reason') or row.get('note') or row.get('clause') or '').split())[:RULING_CHARS]
            kept = entry.setdefault('rulings', [])
            if cause in RULING_CAUSES and text and len(kept) < MAX_RULINGS and all(r['ruling'] != text for r in kept):
                kept.append({'cause': cause, 'ruling': text})
    names = release_verdict.policy_display_names(release_verdict.run_workdir(run.out))
    return {p: {**{k: v for k, v in e.items() if k != 'rulings' or v}, 'causes': dict(sorted(e['causes'].items())),
                **({'display_name': names[p]} if release_verdict._is_name(p, names.get(p)) else {})}  # noqa: SLF001
            for p, e in sorted(out.items())}


def flagged_line(policies: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    """The hand-off's one line for the flagged evals, or None when there are none: each eval said by its display name,
    its id only where it has none (``release_verdict.agent_line``'s rule)."""
    names = sorted(str((e or {}).get('display_name') or p) for p, e in policies.items())
    if not names:
        return None
    shown = names[:FLAGGED_NAMED]
    more = len(names) - len(shown)
    who = shown[0] if len(names) == 1 else (f'{", ".join(shown)} and {more} more' if more else ' and '.join(shown))
    one = len(names) == 1
    return f'Evals to optimize — {who}: flagged for improvement; the Evals page names the next step for {"it" if one else "each"}.'


def load_loop(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {'schema': SCHEMA, 'audits': [], 'policy_trend': []}
    doc = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA:
        raise ValueError(f'{path} is not a {SCHEMA} file')
    return doc


def save_loop(path: Path, loop: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(loop, indent=1) + '\n', encoding='utf-8')
    tmp.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n\n', 1)[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('schedule')
    p.add_argument('--audit', required=True, help='the run dir whose red_cell_audit.json rules it, or a rows file')
    p.add_argument('--label', required=True, help="this audit's name in the trend (e.g. the run folder)")
    p.add_argument('--loop', help='the loop file holding policy_trend[] ($ART/eval_loop.json); omitted, nothing is recorded')
    p.add_argument('--stage', choices=('qualification', 'final'), help='the audited run: qualification leaves prompt-only policies unscheduled')
    p = sub.add_parser('guard')
    p.add_argument('--result', required=True, help="end_edit_session's result: a JSON file, or '-' for stdin")
    p.add_argument('--scope-rows', action='append', default=[], help="the plugin's scope rows: a rows file or an audited run dir")
    p.add_argument('--loop', help='the loop file (default: the artifacts dir\'s eval_loop.json)')
    p = sub.add_parser('never-scored')
    p.add_argument('--report', required=True, help="the FINAL run's saved suite_diagnostics envelope")
    p.add_argument('--loop-log', required=True, help='$ART/loop_log.md')
    p.add_argument('--run-dir', help='the final run dir (its loop-named risks count, and --name-untested writes there)')
    p.add_argument('--name-untested', action='store_true', help='name each unlanded never-scored policy as an untested risk')
    p = sub.add_parser('voided')
    p.add_argument('--audit', required=True, help='the final run dir whose red_cell_audit.json rules it')
    p = sub.add_parser('flagged')
    p.add_argument('--run-dir', required=True, help='the final run dir, after verdict_corrections.py build')
    p.add_argument('--scenario-set-id', required=True, help='the final scenario set (the file is valid for it only)')
    p.add_argument('--out', required=True, help='$ART/flagged_evals.json')
    p = sub.add_parser('flagged-line')
    p.add_argument('--file', required=True, help='$ART/flagged_evals.json')
    p.add_argument('--scenario-set-id', required=True, help='the current final scenario set')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'flagged':
            try:
                found = {} if postfinal_round() else flagged(Path(args.run_dir))
            except red_cell_audit.AuditMissing as exc:  # no audit: nothing is flagged, never a failure
                print(f'eval_rounds: {exc} — no eval flagged', file=sys.stderr)
                found = {}
            doc = {'schema': FLAGGED_SCHEMA, 'run': Path(args.run_dir).name, 'scenario_set_id': args.scenario_set_id.strip(),
                   'round': postfinal_round(), 'policies': found, 'line': flagged_line(found),
                   'display_names': release_verdict.policy_display_names(release_verdict.run_workdir(Path(args.run_dir)))}
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(doc, indent=1) + '\n', encoding='utf-8')
            tmp.replace(out)
            print(json.dumps({'round': doc['round'], 'policies': list(found), 'line': doc['line']}))
            return 0
        if args.cmd == 'flagged-line':
            path = Path(args.file)
            doc = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
            # The line is rebuilt from the file's policies, never read back: a file an earlier plugin wrote keeps its policies,
            # while its stored line carries that plugin's wording.
            if isinstance(doc, dict) and doc.get('schema') == FLAGGED_SCHEMA and doc.get('scenario_set_id') == args.scenario_set_id.strip() \
                    and isinstance(doc.get('policies'), dict):
                line = flagged_line(doc['policies'])
                if line:
                    print(line)
            return 0
        if args.cmd == 'voided':
            found = voided(Path(args.audit))
            print(json.dumps(found))
            return EXIT_VOIDED if found['voided'] else 0
        if args.cmd == 'never-scored':
            report = json.loads(Path(args.report).read_text(encoding='utf-8'))
            log_text = Path(args.loop_log).read_text(encoding='utf-8') if Path(args.loop_log).is_file() else ''
            run_dir = Path(args.run_dir) if args.run_dir else None
            if args.name_untested:
                if run_dir is None:
                    raise ValueError('--name-untested writes the final run dir: pass --run-dir')
                gate_named = {r.get('policy') for r in _gate(_detail(report)).get('named_risks') or [] if isinstance(r, dict)}
                if unnamed := [p for p in never_scored_policies(report) if p not in gate_named]:
                    name_untested(run_dir, unnamed, unconfirmed_premise_policies(report))
            violations = never_scored_violations(report, log_text, run_dir)
            for line in violations:
                print(f'eval_rounds: never scored — {line}', file=sys.stderr)
            print(json.dumps({'never_scored': never_scored_policies(report), 'violations': violations}))
            return EXIT_NEVER_SCORED if violations else 0
        if args.cmd == 'guard':
            raw = sys.stdin.read() if args.result == '-' else Path(args.result).read_text(encoding='utf-8')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('--result is not an end_edit_session result object')
            loop_path = Path(args.loop) if args.loop else default_loop_path()
            loop = load_loop(loop_path)
            result: Dict[str, Any] = record_guard(body, loop, guard_decide.load_scope_rows(args.scope_rows))
            if result['reverted'] or result['named_risks']:
                save_loop(loop_path, loop)
            print(json.dumps(result))
            return 0
        loop_path = Path(args.loop) if args.loop else None
        loop = load_loop(loop_path)
        result = schedule(read_rows(Path(args.audit)), args.label, loop, skips_prompt_only(args.stage))
        if args.stage == 'final' and not postfinal_round():  # no eval round follows the suite's final run
            result = {**result, 'edit_rounds': [], 'postfinal_round': False}
        if loop_path is not None:
            save_loop(loop_path, loop)
    except (OSError, ValueError) as exc:
        print(f'eval_rounds: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
