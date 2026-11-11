#!/usr/bin/env python3
"""Re-score every item of a saved run against the backend eval set: all calls submitted at once, `rescore='changed'`.

    rescore_run.py --run-dir "$OUT" --eval-set-id <id> --scenario-set-id <id> --run-upload-id <id> \
        --results-dir <dir> [--resume] [--harness-fingerprint <fp>] [--summary] [--pin "$WORKDIR"] [--art "$ART"] [--set-corpus-ids a,b]

ITEMS are every scenario, every trial and every stability retry of the run, under the backend's item
names (``red_cells.run_items``: ``$OUT/trial_<k>/<scenario>/`` is item ``<scenario>/trial_<k>``,
``$OUT/<scenario>/stability_retry/`` is ``<scenario>/stability_retry``). Each is scored BY REFERENCE
through the run upload, which holds every item under that name, so the run's red-cell audit applies to a
retry exactly as to its twin; a retry passed inline with no ``run_upload_id`` read no audit at all.

BASELINE. Every call passes ``baseline_cells`` = the item's current cells (``apply_rescore.baseline_cells``):
what ``rescore='changed'`` carries forward and ``changed`` diffs against.

ONE BATCH. Every ``score_run`` is submitted at once, each through ``mcp_call.py`` writing its own
``<results-dir>/<item>.json``; the backend bounds how many it scores at a time, and a queued call keeps
heartbeating. A client-side batch with a barrier lets one slow item stall the rest.

``rescore='changed'`` on every call: a cell whose policy, ground truth and thread did not move carries
forward at zero judge calls, so a re-score after an eval commit judges only the edited policies.

With ``--resume`` a result file that already holds ``cells`` is re-read, not re-scored, so a
re-invocation picks up where an interrupted one stopped; without it every item is scored afresh (a
result from before the latest commit is not this commit's verdict). Without ``--summary`` each result is applied to the
item's verdicts through ``apply_rescore.py``; ``--summary`` asks for the compact result and applies nothing.

Prints one JSON summary. Exit codes: 0 every item scored (and its result applied) · 1 some item
failed — the summary names it and its result file · 2 usage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import red_cells  # noqa: E402 - after the sys.path line it depends on
from apply_rescore import baseline_cells  # noqa: E402 - the one baseline every call is given
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

MCP_CALL = HERE / 'mcp_call.py'
APPLY = HERE / 'apply_rescore.py'

#: ``(tool, arguments, result file) -> (exit code, stderr)``; the result JSON lands in the file.
Caller = Callable[[str, Dict[str, Any], Path], Tuple[int, str]]


def mcp_caller(tool: str, arguments: Dict[str, Any], result: Path) -> Tuple[int, str]:
    """One ``mcp_call.py`` subprocess; its arguments travel as a file (a long call can pass ARG_MAX)."""
    args_file = result.with_name(result.name + '.args')
    args_file.write_text(json.dumps(arguments), encoding='utf-8')
    with result.open('w', encoding='utf-8') as out:
        proc = subprocess.run([sys.executable, str(MCP_CALL), tool, f'@{args_file}'], stdout=out, stderr=subprocess.PIPE, text=True, **utf8_child_text(), check=False)
    return proc.returncode, proc.stderr


def result_path(results_dir: Path, item: str) -> Path:
    return results_dir / (item.replace('/', '__') + '.json')


def _scored(path: Path) -> bool:
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and isinstance(doc.get('cells'), dict)


def item_arguments(item: str, item_dir: Path, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """One item's ``score_run`` arguments: its item name (a retry too, by reference) and its current cells as ``baseline_cells``."""
    return {**arguments, 'scenario_name': item, 'baseline_cells': baseline_cells(item_dir)}


def score_items(
    items: Sequence[Tuple[str, Path]],
    arguments: Dict[str, Any],
    results_dir: Path,
    *,
    resume: bool = False,
    caller: Caller = mcp_caller,
) -> Tuple[List[str], Dict[str, Tuple[int, str]]]:
    """``(reused items, {item: (exit code, stderr)} for every submitted one)`` — all submitted in one batch."""
    results_dir.mkdir(parents=True, exist_ok=True)
    reused = [item for item, _ in items if resume and _scored(result_path(results_dir, item))]
    pending = [item for item, _ in items if item not in reused]
    outcomes: Dict[str, Tuple[int, str]] = {}
    if not pending:
        return reused, outcomes
    with ThreadPoolExecutor(max_workers=len(pending)) as pool:
        dirs = dict(items)
        futures = {item: pool.submit(caller, 'score_run', item_arguments(item, dirs[item], arguments), result_path(results_dir, item)) for item in pending}
        for item, future in futures.items():
            try:
                outcomes[item] = future.result()
            except Exception as exc:  # noqa: BLE001 - one failed call is that item's failure, never the batch's
                outcomes[item] = (1, f'{type(exc).__name__}: {exc}')
    return reused, outcomes


def apply_result(result: Path, out: Path, item_dir: Path, passthrough: Sequence[str]) -> Tuple[int, str]:
    cmd = [sys.executable, str(APPLY), str(result), '--out-dir', str(out), '--scenario', str(item_dir.relative_to(out)), *passthrough]
    proc = subprocess.run(cmd, capture_output=True, text=True, **utf8_child_text(), check=False)
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def main(argv: Optional[Sequence[str]] = None, *, caller: Caller = mcp_caller) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    ap.add_argument('--run-dir', required=True, help='the local run dir ($OUT)')
    ap.add_argument('--eval-set-id', required=True)
    ap.add_argument('--scenario-set-id', required=True)
    ap.add_argument('--run-upload-id', required=True, help="the run's upload (upload_run_artifacts_start)")
    ap.add_argument('--results-dir', required=True, help='one <item>.json score_run result per item lands here')
    ap.add_argument('--resume', action='store_true', help='re-read a result file that already holds cells instead of re-scoring it')
    ap.add_argument('--harness-fingerprint', help='current_harness_fingerprint, passed on every call')
    ap.add_argument('--summary', action='store_true', help='summary=true results; nothing is applied')
    ap.add_argument('--pin', help="apply_rescore.py --pin (the workdir holding .eval_lane_pin.json)")
    ap.add_argument('--art', help='apply_rescore.py --art')
    ap.add_argument('--set-corpus-ids', help='apply_rescore.py --set-corpus-ids')
    args = ap.parse_args(argv)

    out = Path(args.run_dir)
    if not out.is_dir():
        print(f'rescore_run: {out} is not a run directory', file=sys.stderr)
        return 2
    items = red_cells.run_items(out)
    arguments: Dict[str, Any] = {
        'eval_set_id': args.eval_set_id,
        'scenario_set_id': args.scenario_set_id,
        'run_upload_id': args.run_upload_id,
        'summary': bool(args.summary),
        'rescore': 'changed',
    }
    if args.harness_fingerprint:
        arguments['current_harness_fingerprint'] = args.harness_fingerprint
    passthrough: List[str] = []
    for flag, value in (('--pin', args.pin), ('--art', args.art), ('--set-corpus-ids', args.set_corpus_ids)):
        if value:
            passthrough += [flag, value]
    results_dir = Path(args.results_dir)
    reused, outcomes = score_items(items, arguments, results_dir, resume=args.resume, caller=caller)

    failed: List[Dict[str, Any]] = []
    applied: List[str] = []
    for item, item_dir in items:
        path = result_path(results_dir, item)
        code, err = outcomes.get(item, (0, ''))
        if code != 0 or not _scored(path):
            failed.append({'item': item, 'result': str(path), 'exit': code, 'error': err.strip()[-600:]})
            continue
        if args.summary:
            continue
        code, err = apply_result(path, out, item_dir, passthrough)
        if code != 0:
            failed.append({'item': item, 'result': str(path), 'apply_exit': code, 'error': err[-600:]})
        else:
            applied.append(item)
    summary = {
        'items': len(items),
        'submitted': len(outcomes),
        'reused': reused,
        'applied': len(applied),
        'failed': failed,
    }
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
