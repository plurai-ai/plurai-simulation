#!/usr/bin/env python3
"""Claim the ONE bounded repair a run may take — a release-gate remedy fixed in place, never a loop.

    scripts/bounded_repair.py claim --out "$OUT" --report <saved suite_diagnostics envelope or coverage audit>

Writes `$OUT/bounded_repair.json` — `{remedy, rerun_scenarios, replant, deterministic_losses, transient_losses,
rerun_concurrency, claimed_at}`, read off the report's `release_gate` (fields an older backend omits read as empty; each loss classed by the run's
own `error.json` `transient` / `route_cause` where they type one, `release_verdict.run_loss_classes`) —
and exits 0. A transient loss (load or latency in the simulated tools) is re-run, never sent to /edit-env, and the
re-run then goes at `rerun_concurrency` (:data:`TRANSIENT_RERUN_CONCURRENCY`); otherwise it is null (the run's own). So
does a loss every trial repeated the same way (the SDK's `trials.repeated_losses`): trials run side by side can share one
outage, so it is re-run once, apart in time, and only a repeat that survives that re-run is said as reproduced. A
scenario the backend refused to LOAD (`release_verdict.unloadable_losses`) is neither re-run nor sent to /edit-env — only a
rebuilt suite repairs it — and a claim left with nothing to repair is not bounded (exit 4). The
claim is the cap: a run gets one, pre-run (`/simulation-run` Step 2b) or post-run (Step 4c), never both.

Two gates are bounded: a block whose `remedy` is in :data:`BOUNDED`, and a FINAL gate that passes but still
publishes a non-empty `replant` — a situation the run could not set up, named as a "not tested here"
(`untested_premise`) risk rather than a block. That one is claimed as `replant_premise`, under the same cap.

Exit codes: 0 claimed · 3 already claimed for this `$OUT` · 4 the remedy is not a bounded one (nothing written).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_verdict import (  # noqa: E402
    deterministic_losses, read_report, report_gate, run_loss_classes, sdk_repeats, transient_losses, unloadable_losses, with_run_losses)

#: The remedies a repair in place can close: re-run the lost scenarios (`rerun` — every loss transient or untyped,
#: e.g. a turn that timed out waiting on the simulated tools); fix the env and re-run them; top up one premise.
BOUNDED = ('rerun', 'fix_env_rerun_scenarios', 'replant_premise')
MARKER = 'bounded_repair.json'
#: The concurrency a re-run of transient losses goes at: the faults were the simulated tools not answering in time under
#: load, so the one targeted re-run lowers the load rather than repeating it.
TRANSIENT_RERUN_CONCURRENCY = 1
EXIT_CLAIMED, EXIT_NOT_BOUNDED = 3, 4


def _list(gate: dict, key: str) -> list:
    value = gate.get(key)
    return value if isinstance(value, list) else []


def cmd_claim(args: argparse.Namespace) -> int:
    out = Path(args.out)
    marker = out / MARKER
    if marker.exists():
        print(f'bounded repair: already claimed for this run ({marker}) — present the gate as it stands', file=sys.stderr)
        return EXIT_CLAIMED
    report, _ = read_report(Path(args.report))
    # The run's own typed records (`error.json` `transient` / `route_cause`) class each loss before its repair is picked.
    gate = with_run_losses(report_gate(report), run_loss_classes(out)) if report is not None else None
    remedy = (gate or {}).get('remedy')
    untested = bool(gate) and gate.get('stage') == 'final' and not gate.get('blocking') and bool(_list(gate, 'replant'))
    if untested:
        remedy = 'replant_premise'
    elif not gate or not gate.get('blocking') or remedy not in BOUNDED:
        print(f'bounded repair: remedy {remedy!r} is not a bounded one — present the gate as it stands', file=sys.stderr)
        return EXIT_NOT_BOUNDED
    # A scenario the backend refused to LOAD is refused again on a re-run, and /edit-env cannot reach it: never repaired here.
    unloadable = set(unloadable_losses(gate))
    rerun = [name for name in _list(gate, 'rerun_scenarios') if name not in unloadable]
    if unloadable and not rerun and not _list(gate, 'replant'):
        print(f'bounded repair: {", ".join(sorted(unloadable))} could not be loaded — only a rebuilt suite repairs that; present the '
              'gate as it stands', file=sys.stderr)
        return EXIT_NOT_BOUNDED
    # A repeat is re-run like a transient loss (never /edit-env: it is not in `deterministic_losses`), one at a time.
    blips, repeats = transient_losses(gate), [name for name in sdk_repeats(out) if name in rerun]
    claim = {'remedy': remedy, 'rerun_scenarios': rerun, 'replant': _list(gate, 'replant'),
             'deterministic_losses': deterministic_losses(gate, report), 'transient_losses': blips,
             'rerun_concurrency': TRANSIENT_RERUN_CONCURRENCY if blips or repeats else None,
             'claimed_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    out.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open('x', encoding='utf-8') as fh:
            json.dump(claim, fh, indent=2)
    except FileExistsError:
        print(f'bounded repair: already claimed for this run ({marker})', file=sys.stderr)
        return EXIT_CLAIMED
    print(json.dumps(claim))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    claim = sub.add_parser('claim', help='claim the run\'s one bounded repair')
    claim.add_argument('--out', required=True, help='the run dir ($OUT)')
    claim.add_argument('--report', required=True, help='the saved suite_diagnostics envelope or eval_coverage_audit result')
    claim.set_defaults(func=cmd_claim)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
