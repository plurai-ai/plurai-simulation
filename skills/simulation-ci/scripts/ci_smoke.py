#!/usr/bin/env python3
"""The smoke-list proposal: the scenarios with the most gating cells, within the default size of a pull request run.

    ci_smoke.py propose --catalog catalog.json [--trials 3]

``catalog.json`` is a list of ``{"name": "<scenario>", "gating_cells": <n>}`` for the scenarios of the committed set, where a
gating cell is a scenario x policy whose tag is in ``fail_on.tags`` and that applies to the scenario. The proposal holds no more
threads (scenarios x trials, regressions included) than ``SMOKE_CAP_THREADS``, a provisional default size: the user may confirm a
longer list, which costs more threads. Regression scenarios are always part of a run, so they take room first and are not listed:
the list names the others, ranked by gating cells and then by name.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_common as common


@dataclass(frozen=True)
class Proposal:
    smoke: tuple[str, ...]
    regressions: tuple[str, ...]
    trials: int
    cap_threads: int
    note: str | None

    @property
    def scenarios(self) -> int:
        return len(self.smoke) + len(self.regressions)

    @property
    def threads(self) -> int:
        return self.scenarios * self.trials

    def as_json(self) -> dict[str, object]:
        return {
            'smoke': list(self.smoke),
            'regressions': list(self.regressions),
            'scenarios': self.scenarios,
            'trials': self.trials,
            'threads': self.threads,
            'cap_threads': self.cap_threads,
            'note': self.note,
        }


def read_catalog(path: Path) -> dict[str, int]:
    """``{scenario name: gating cells}`` from the catalog file; raises ``ValueError`` naming what is wrong with it."""
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f'{path} is not a readable JSON catalog ({type(error).__name__})') from error
    if not isinstance(rows, list):
        raise ValueError(f'{path} must hold a list of {{"name", "gating_cells"}} objects')  # noqa: TRY004
    catalog: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get('name'), str) or not isinstance(row.get('gating_cells', 0), int):
            raise ValueError(f'{path} holds a row that is not {{"name": str, "gating_cells": int}}: {row!r}')  # noqa: TRY004
        catalog[row['name']] = int(row.get('gating_cells', 0))
    return catalog


def propose(catalog: Mapping[str, int], *, trials: int = common.DEFAULT_TRIALS, cap_threads: int = common.SMOKE_CAP_THREADS) -> Proposal:
    regressions = tuple(sorted(name for name in catalog if name.startswith(common.REGRESSION_PREFIX)))
    room = cap_threads // max(trials, 1) - len(regressions)
    others = sorted((name for name in catalog if name not in regressions and catalog[name] > 0), key=lambda name: (-catalog[name], name))
    chosen = tuple(others[: max(room, 0)])
    note = None
    if room < 0:
        note = f'the {len(regressions)} regression scenarios alone make {len(regressions) * trials} threads, over the proposal size of {cap_threads} threads (they run anyway); no other scenario was added'
    elif len(others) > len(chosen):
        note = f'{len(others) - len(chosen)} more scenarios with gating cells did not fit under {cap_threads} threads; the full run covers them'
    return Proposal(chosen, regressions, trials, cap_threads, note)


def unknown_names(smoke: Sequence[str], catalog: Mapping[str, int]) -> list[str]:
    """Names in ``smoke`` that the committed set does not have."""
    return [name for name in smoke if name not in catalog]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0] if __doc__ else None)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('propose')
    p.add_argument('--catalog', required=True, type=Path)
    p.add_argument('--trials', type=int, default=common.DEFAULT_TRIALS)
    args = parser.parse_args(argv)
    try:
        proposal = propose(read_catalog(args.catalog), trials=args.trials)
    except ValueError as error:
        print(f'simulation-ci: {error}', file=sys.stderr)
        return 2
    print(json.dumps(proposal.as_json()))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(common.SCRIPTS))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
