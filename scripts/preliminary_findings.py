#!/usr/bin/env python3
"""Agent defects the qualification run already showed, as rows for the page's copy of ``agent_findings.jsonl``.

    python3 scripts/preliminary_findings.py <workdir> [--suite-open | --suite-closed]   # prints the rows, one JSON per line

Read-only and stateless: every page fill derives the rows again from artifacts the build keeps, and nothing is written
to the disk ledger (``record_finding``, ``prune_agent_findings`` and ``deliverables`` never see them).
``publish_report.page_findings`` appends them to the page copy only when the vendored page declares
``PAGE_CAPABILITY`` (``supported``).

RUNS. The current build's qualification runs: ``runs/index.json`` ``stage: qualification``, or an unindexed folder
named like one (``publish_report.QUALIFICATION_NAME``). An item counts only when its transcript's
``metadata.scenario_set_id`` is a set ``suite_state.json`` recorded since the last reset or hand-off
(``bench_manifest``'s build boundary), so a set-aside build's runs on disk add nothing.

CANDIDATE UNIT. A unit is (item, eval, turn): ``red_cells``' units, an item being a scenario, its stability retry or
a later trial. It is a candidate when all of these hold:

1. its OWN item is integrity-clean: ``verdicts.json`` ``judging.measurement_integrity.clean`` and the transcript's
   ``finalize_record.user_sim_integrity.clean`` both ``True`` (missing reads as not clean). Integrity is per
   playthrough: a clean retry counts even when its main item breached;
2. the eval is in that item's ``verdicts.json`` ``goal.catastrophic_violations`` or ``high_severity_violations``;
3. the run recorded the unit red (``violated`` and ``status == 'success'``): the cell as the run judged it
   (``verdicts_file.run_cells``: the first cell ``history`` replaced, else the current one);
4. its run's audit (``red_cell_audit.path``) has a row ruling it ``TRUE RED``. Nothing shows before the audit.

ROW. One per eval: ``{id: agent.qual.<eval>, status, found_during: 'quality loop', eval, severity, seen_in:
[{scenario, turn}], of_scenarios, in_progress, excerpt?}``. ``severity`` is the eval's own bucket (``catastrophic`` |
``high``), not a calibrated one. ``seen_in`` holds the scenario ordinal and its first red turn (null when the verdict
names none); no run or folder is named. ``excerpt`` is the judge's one-line ``why`` (absent for a code eval). The key
is never ``evidence_pending`` (the status says so) and never a string ``source`` (``record_finding`` rows carry an
object there).

STATUS, first that holds:

- ``released`` / ``released_because: false_red`` — every candidate unit has since been ruled ``FALSE RED`` too;
  ``release_excerpt`` is the audit's reason. Wins over a citing finding;
- ``confirmed`` / ``potential`` — a filed finding whose ``probe:`` line names the eval (a backticked eval id); the
  strongest citing status, and ``findings`` lists the ids. The page shows filed loop findings while the suite is open
  (``publish_report.page_findings``, same capability), so the row folds into them as soon as they are filed;
- ``released`` / ``rescored_pass`` — no unit still stands: the item's current verdict now passes it;
- ``released`` / ``not_kept`` — the suite handed off and no filed finding cites the eval;
- ``preliminary`` — otherwise.

``in_progress`` is true on every row while the suite is open. The id never changes across rounds, rescores,
confirmation and release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

#: The mark a vendored page declares, in its ``manifest.json`` ``capabilities`` list, when it renders these rows (the
#: ``preliminary`` / ``released`` statuses on ``agent.qual.*`` ids, folding a confirmed row into its ``findings``) AND
#: the ``in_progress`` flag on filed quality-loop findings shown while the suite is open.
PAGE_CAPABILITY = 'preliminary_findings'
ID_PREFIX = 'agent.qual.'
LOOP_STAGE = 'quality loop'
#: The backend's severity buckets the row reads, strongest first.
SEVERITY_LISTS = (('catastrophic', 'catastrophic_violations'), ('high', 'high_severity_violations'))
#: The filed statuses a row mirrors, strongest first; ``retired`` cites nothing.
CITING = ('confirmed', 'potential')
EXCERPT_MAX = 300

_ORDINAL = re.compile(r'^scenario_(\d+)')
_TICKED = re.compile(r'`([^`\s]+)`')
#: A section's ``probe:`` line, its value kept whole (``record_finding._fields`` strips the code span's opening tick).
_PROBE = re.compile(r'^[ \t]*(?:[-*+]|\d+[.)])?[ \t]*[*_]{0,2}[ \t]*probe[ \t]*[*_]{0,2}[ \t]*:(?P<value>.*)$', re.I | re.M)


def supported(vendored: Path) -> bool:
    """Whether the vendored page declares ``PAGE_CAPABILITY`` in its ``manifest.json`` ``capabilities`` list."""
    try:
        manifest = json.loads((vendored / 'manifest.json').read_text('utf-8'))
    except (OSError, ValueError):
        return False
    capabilities = manifest.get('capabilities') if isinstance(manifest, dict) else None
    return isinstance(capabilities, list) and PAGE_CAPABILITY in capabilities


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None


# ── the build ────────────────────────────────────────────────────────────────────────────────


def build_sets(phases: Iterable[Any]) -> set[str]:
    """The scenario sets the CURRENT build recorded: every ``ids.scenario_set`` after the last reset, and after the
    last hand-off unless only the run's top-up follows it (``bench_manifest.handed_off``'s rule)."""
    import bench_manifest  # noqa: PLC0415 - the one build-boundary rule, read lazily

    records = [p for p in phases if isinstance(p, dict)]
    start, closed_at = 0, None
    for i, record in enumerate(records):
        phase = record.get('phase')
        if phase == bench_manifest.RESET_PHASE:
            start, closed_at = i + 1, None
        elif (phase, record.get('status')) == (bench_manifest.HANDOFF_PHASE, 'landed'):
            closed_at = i
        elif closed_at is not None and phase not in bench_manifest.SAME_BUILD_AFTER_HANDOFF:
            start, closed_at = i, None  # a later build began after that hand-off
    ids = (r.get('ids') for r in records[start:])
    return {str(i['scenario_set']) for i in ids if isinstance(i, dict) and i.get('scenario_set')}


def qualification_runs(runs: Path) -> list[Path]:
    """The qualification run folders under ``runs``: indexed ``stage: qualification``, or unindexed and named like one."""
    from publish_report import QUALIFICATION_NAME  # noqa: PLC0415 - the one naming rule (the vendored fill's own)

    if not runs.is_dir():
        return []
    index = _json(runs / 'index.json')
    rows = index.get('runs') if isinstance(index, dict) else None
    stage_of = {str(r.get('name')): r.get('stage') for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
    found = []
    for folder in sorted(p for p in runs.iterdir() if p.is_dir()):
        stage = stage_of.get(folder.name)
        if stage == 'qualification' or (stage is None and QUALIFICATION_NAME.search(folder.name)):
            found.append(folder)
    return found


# ── the units ────────────────────────────────────────────────────────────────────────────────


def _clean(results: Mapping[str, Any]) -> bool:
    return all(isinstance(results.get(key), dict) and results[key].get('clean') is True for key in ('measurement_integrity', 'user_sim_integrity'))


def _severity(verdict: Any, policy: str) -> str | None:
    lists = verdict if isinstance(verdict, dict) else {}
    return next((name for name, key in SEVERITY_LISTS if policy in (lists.get(key) or [])), None)


def _first_turn(unit: Mapping[str, Any], turn_idx: int | None) -> int | None:
    if turn_idx is not None:
        return turn_idx
    turns = [f.get('turn_idx') for f in unit.get('findings') or [] if isinstance(f, dict)]
    turns = [t for t in turns if isinstance(t, int) and not isinstance(t, bool)]
    return min(turns) if turns else None


def _why(unit: Mapping[str, Any], cell: Mapping[str, Any], turn_idx: int | None) -> str | None:
    """The judge's one-line ``why`` for this unit: its own findings, else the cell's (the one at its turn first)."""
    findings = [f for f in (unit.get('findings') or cell.get('findings') or []) if isinstance(f, dict) and isinstance(f.get('why'), str) and f['why'].strip()]
    if turn_idx is not None:
        findings.sort(key=lambda f: f.get('turn_idx') != turn_idx)
    if not findings:
        return None
    text = ' '.join(findings[0]['why'].split())
    return text if len(text) <= EXCERPT_MAX else text[: EXCERPT_MAX - 1] + '…'


def _units(workdir: Path) -> tuple[list[dict[str, Any]], set[str], int]:
    """(every candidate unit of the build's qualification runs, every eval id those runs carry, how many scenarios)."""
    import red_cell_audit  # noqa: PLC0415 - the one audit file name (C5)
    import red_cells  # noqa: PLC0415 - the one reader of a run's items, units and audit rows
    import verdicts_file  # noqa: PLC0415 - the one reader of an item's verdicts

    art = workdir / 'artifacts'
    state = _json(art / 'suite_state.json')
    sets = build_sets(state.get('phases') or []) if isinstance(state, dict) else set()
    found: list[dict[str, Any]] = []
    evals: set[str] = set()
    scenarios: set[str] = set()
    for out in qualification_runs(art / 'runs') if sets else []:
        try:
            run = red_cells.Run(out)
        except ValueError:
            continue
        try:
            audit = red_cell_audit.rows(out)
        except ValueError:
            continue  # no audit yet (`audit missing`), or one that does not read: it rules nothing
        ruled, _ = red_cells.ruled_units(run, audit)
        for item, folder in sorted(run.dirs.items()):
            transcript = _json(folder / 'transcript.json')
            if not isinstance(transcript, dict) or str((transcript.get('metadata') or {}).get('scenario_set_id') or '') not in sets:
                continue
            doc = verdicts_file.read(folder)
            recorded = verdicts_file.run_cells(doc)
            scenarios.add(red_cells.item_scenario(item))
            evals.update(verdicts_file.evals(doc))
            if not _clean({**verdicts_file.judging(doc), **(transcript.get('finalize_record') or {})}):
                continue
            current = run.cells(item)[0]
            for policy, cell in sorted(recorded.items()):
                severity = _severity(verdicts_file.goal(doc), policy)
                if severity is None or not isinstance(cell, dict):
                    continue
                now = dict(red_cells.units(current.get(policy)))
                for turn_idx, unit in red_cells.units(cell):
                    rulings = ruled.get((item, policy, turn_idx), [])
                    if not red_cells.is_red(unit) or not any(v == red_cells.TRUE_RED for v, _ in rulings):
                        continue
                    false_red = next((audit[i] for v, i in rulings if v == red_cells.FALSE_RED), None)
                    rescored = now.get(turn_idx)  # the unit as the current verdicts hold it
                    found.append({
                        'eval': policy, 'item': item, 'scenario': red_cells.item_scenario(item), 'severity': severity,
                        'turn': _first_turn(unit, turn_idx), 'why': _why(unit, cell, turn_idx),
                        'false_red': false_red, 'passes_now': rescored is not None and red_cells.is_pass(rescored),
                    })
    return found, evals, len(scenarios)


# ── the filed findings ───────────────────────────────────────────────────────────────────────


def citations(workdir: Path, evals: set[str]) -> dict[str, list[tuple[str, str]]]:
    """``{eval: [(finding id, status)]}`` — the filed findings whose ``probe:`` line names the eval, status from the
    jsonl row (else the section's own line); a retired finding cites nothing."""
    import prune_agent_findings  # noqa: PLC0415 - the one section splitter and finding-id reader
    import record_finding  # noqa: PLC0415 - the one field-line reader

    art = workdir / 'artifacts'
    try:
        md = (art / 'agent_findings.md').read_text('utf-8')
    except (OSError, UnicodeDecodeError):
        return {}
    statuses: dict[str, str] = {}
    try:
        lines = (art / 'agent_findings.jsonl').read_text('utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get('id'), str):
            statuses[row['id']] = str(row.get('status') or '')
    cited: dict[str, list[tuple[str, str]]] = {}
    for section in prune_agent_findings._split_sections(md)[1:]:
        found = prune_agent_findings._FINDING_ID.search(section)
        if not found or found.group(1).startswith(ID_PREFIX):
            continue
        fields = record_finding._fields(section)
        status = statuses.get(found.group(1)) or record_finding._first_word(fields.get('status', ''))
        if status not in CITING:
            continue
        probe = _PROBE.search(section)
        for policy in sorted(set(_TICKED.findall(probe.group('value') if probe else '')) & evals):
            cited.setdefault(policy, []).append((found.group(1), status))
    return cited


# ── the rows ─────────────────────────────────────────────────────────────────────────────────


def _ordinals(names: Iterable[str]) -> dict[str, int]:
    ordered = sorted(set(names))
    return {name: int(m.group(1)) if (m := _ORDINAL.match(name)) else ordered.index(name) + 1 for name in ordered}


def rows(workdir: Path, *, suite_open: bool) -> list[dict[str, Any]]:
    """The page rows, one per eval with a candidate unit, in eval order (module docstring)."""
    found, evals, of_scenarios = _units(workdir)
    if not found:
        return []
    cited = citations(workdir, evals)
    by_eval: dict[str, list[dict[str, Any]]] = {}
    for unit in found:
        by_eval.setdefault(unit['eval'], []).append(unit)
    out = []
    for policy, units in sorted(by_eval.items()):
        standing = [u for u in units if u['false_red'] is None and not u['passes_now']]
        shown = standing or units
        ordinal = _ordinals(u['scenario'] for u in shown)
        first: dict[str, dict[str, Any]] = {}
        for unit in sorted(shown, key=lambda u: (u['item'], -1 if u['turn'] is None else u['turn'])):
            first.setdefault(unit['scenario'], unit)
        severity = next(name for name, _ in SEVERITY_LISTS if any(u['severity'] == name for u in units))
        row: dict[str, Any] = {
            'id': f'{ID_PREFIX}{policy}', 'status': 'preliminary', 'found_during': LOOP_STAGE, 'eval': policy, 'severity': severity,
            'seen_in': [{'scenario': ordinal[name], 'turn': first[name]['turn']} for name in sorted(first, key=ordinal.__getitem__)],
            'of_scenarios': of_scenarios, 'in_progress': suite_open,
        }
        excerpt = next((u['why'] for u in sorted(shown, key=lambda u: u['item']) if u['why']), None)
        if excerpt:
            row['excerpt'] = excerpt
        citing = cited.get(policy, [])
        if all(u['false_red'] is not None for u in units):
            reason = ' '.join(str(units[0]['false_red'].get('reason') or '').split())
            row.update({'status': 'released', 'released_because': 'false_red'})
            if reason:
                row['release_excerpt'] = reason if len(reason) <= EXCERPT_MAX else reason[: EXCERPT_MAX - 1] + '…'
        elif citing:
            row.update({'status': next(s for s in CITING if any(c == s for _, c in citing)), 'findings': sorted({i for i, _ in citing})})
        elif not standing:
            row.update({'status': 'released', 'released_because': 'rescored_pass'})
        elif not suite_open:
            row.update({'status': 'released', 'released_because': 'not_kept'})
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    ap.add_argument('workdir', type=Path)
    group = ap.add_mutually_exclusive_group()
    group.add_argument('--suite-open', dest='suite_open', action='store_true', default=None)
    group.add_argument('--suite-closed', dest='suite_open', action='store_false')
    args = ap.parse_args(argv)
    suite_open = args.suite_open
    if suite_open is None:
        import publish_report  # noqa: PLC0415

        suite_open = publish_report._suite_open(args.workdir.resolve(), None)
    for row in rows(args.workdir.resolve(), suite_open=suite_open):
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
