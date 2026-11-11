#!/usr/bin/env python3
"""When a /simulate stage has LANDED: one predicate for the plan and the page.

``hooks/simulate_continue.py`` records a stage ``done`` only when :func:`stage_landed` holds, and
``scripts/publish_report.py`` shows it ``done`` in ``flow_progress.json`` only then, so the plan and the
file never disagree. A stage landed when:

* its proof file (:data:`STAGE_PROOF`) was written at or after the stage began (``since``, epoch). The fixes
  stage of a run with nothing to diagnose writes no ``fixes.md`` (``skills/simulation-fixes/SKILL.md``,
  "nothing to diagnose … stop"), so there :func:`fixes_due` (``deliverables.fixes_required``) stands in for its proof;
* every file the report reads for the stage exists: ``deliverables.missing(workdir, stage)`` is empty.

:func:`stage_gaps` says what is missing, as ``(path relative to the workdir, who writes it)``, proof first.
Stdlib only; runs under a stock ``python3`` (3.9). Never raises on a workdir it cannot read: that is a gap.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))
sys.path.append(str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402 - is_local_drive_path: the plugin's one UNC gate


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def scored_run_json(workdir: Path) -> Path | None:
    """The scored run's ``run.json``: ``runs/index.json`` ``scored_run`` (null there: none is scored yet — a final run
    with no audit never is), else, with no index, the run ``.last_run`` names."""
    art = workdir / 'artifacts'
    index = _read_json(art / 'runs' / 'index.json')
    if index is not None and 'scored_run' in index:
        name = index['scored_run']
        ok = isinstance(name, str) and name.strip() and '/' not in name and name not in ('.', '..')
        found = art / 'runs' / name.strip() / 'run.json' if ok else None
        return found if found is None or _platform.is_local_drive_path(found, within=(workdir,)) else None
    try:
        last = (workdir / '.last_run').read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not last:
        return None
    run_dir = Path(last)
    found = (run_dir if run_dir.is_absolute() else workdir / run_dir) / 'run.json'
    return found if _platform.is_local_drive_path(found, within=(workdir,)) else None


#: Stage -> the file whose write since the stage began PROVES it ran to its end (under the agent's workdir).
STAGE_PROOF: dict[str, Callable[[Path], Path | None]] = {
    'scan': lambda wd: wd / 'artifacts' / 'verification.json',
    'traces': lambda wd: wd / 'artifacts' / 'trace_failures.json',
    'suite': lambda wd: wd / 'artifacts' / 'suite_anchor.json',
    'run': scored_run_json,
    'fixes': lambda wd: wd / 'artifacts' / 'fixes.md',
}
#: Who writes each stage's proof: what a proof gap names, as a deliverables row names its writer.
PROOF_WRITER = {
    'scan': '/simulation-scan-agent',
    'traces': '/analyze-traces',
    'suite': '/simulation-generate-suite',
    'run': '/simulation-run',
    'fixes': '/simulation-fixes',
}

def proof_fresh(stage: str, workdir: Path | None, since: float | None) -> bool:
    """Was ``stage``'s proof file written at or after ``since``? False on anything unreadable."""
    finder = STAGE_PROOF.get(stage)
    if finder is None or workdir is None or since is None:
        return False
    try:
        path = finder(workdir)
        return path is not None and path.is_file() and path.stat().st_mtime >= since
    except OSError:
        return False


def fixes_due(workdir: Path) -> bool:
    """Whether the fixes stage owes a ``fixes.md``: ``deliverables.fixes_required``, the table's own CONDITIONAL
    ``fixes.md`` row (a failed or errored scored row after any rescore, or a confirmed finding). Never spelled here a
    second time. Unreadable, or the table broken: not due (the table's "no false blocks" rule)."""
    try:
        import deliverables  # noqa: PLC0415 - read per call, so a test's table reaches it
        return bool(deliverables.fixes_required(workdir))
    except Exception:  # noqa: BLE001 - a broken table never wedges a run
        return False


def missing(workdir: Path, stage: str) -> list[tuple[str, str]]:
    """``deliverables.missing(workdir, stage)``: the files the report reads for ``stage`` that are not there.
    Broken, nothing is reported missing: the proof file still gates the stage."""
    try:
        import deliverables  # noqa: PLC0415 - read per call, so a test's table reaches it
        found = deliverables.missing(workdir, stage)
        return [(str(row[0]), str(row[1])) for row in found]
    except Exception:  # noqa: BLE001 - a broken table never wedges a run
        return []


def _not_handed_off(stage: str, workdir: Path) -> bool:
    """A suite anchor that records ``handed_off: false`` (``lane_pin.py anchor`` over a build that had not handed off):
    written, yet no hand-off — the reading ``simulate_plan._handed_off`` gives it. A legacy anchor has no such key."""
    return stage == 'suite' and (_read_json(STAGE_PROOF['suite'](workdir)) or {}).get('handed_off') is False


def stage_gaps(stage: str, workdir: Path | None, since: float | None) -> list[tuple[str, str]]:
    """What ``stage`` still owes, proof first: ``[]`` iff it landed. ``since`` is when it began (epoch)."""
    if stage not in STAGE_PROOF or workdir is None:
        return [(stage, PROOF_WRITER.get(stage, stage))]
    gaps: list[tuple[str, str]] = []
    if (not proof_fresh(stage, workdir, since) or _not_handed_off(stage, workdir)) and (stage != 'fixes' or since is None or fixes_due(workdir)):
        proof = STAGE_PROOF[stage](workdir)
        rel = 'artifacts/runs/index.json' if proof is None else _relative(proof, workdir)
        gaps.append((rel, PROOF_WRITER[stage]))
    gaps.extend(gap for gap in missing(workdir, stage) if gap[0] not in {g[0] for g in gaps})
    return gaps


def stage_landed(stage: str, workdir: Path | None, since: float | None) -> bool:
    """THE predicate: ``stage``'s proof is fresh since ``since`` and the report's files for it exist."""
    return not stage_gaps(stage, workdir, since)


def _relative(path: Path, workdir: Path) -> str:
    try:
        return path.relative_to(workdir).as_posix()
    except ValueError:
        return path.name
