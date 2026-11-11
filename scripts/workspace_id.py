#!/usr/bin/env python3
"""Print the `workspace_id` this bench sends on every backend call that takes one.

    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-workdir.sh"; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_id.py"

Bare mode prints `<bench_id>`; named-env mode (`$WORKDIR` = `plurai_sim/envs/<alias>`) prints
`<bench_id>.<alias>`. Exit 0 = printed · 2 = no bench resolved, or the marker is unreadable.

WHY. The backend files the trace brief under `{trace_storage_root}/{workspace_id}/brief` and
nothing else scopes it — not the agent, not the organisation (one per box). An omitted id is the
shared `default` slot, so two benches on one box read and overwrite each other's brief; an env
alias alone is a repo-local directory name, so `prod` in two repos is one slot. The bench id is
an opaque random token minted ONCE into `plurai_sim/.workspace_id` (beside `envs/`, never inside
one), so every env of a bench shares it and no other bench can.

IT IDENTIFIES THE BENCH, NOT A RUN. It is committed with the bench (a clone keeps its brief) and a
reset of the per-run id markers (`.scenario_set_id`, `.eval_set_id`, `.env_id`, … and
`artifacts/suite_state.json`) must leave it in place: a re-minted id orphans the published brief.
A bench COPY meant to be independent deletes it, and the next call mints a fresh one.

An unreadable marker is refused, never overwritten — overwriting is the same orphaning.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_fingerprint  # noqa: E402 - sibling script: the workdir resolver cascade lives there

MARKER = '.workspace_id'

#: A single path-safe segment — what the backend's `_sanitize_workspace_id` passes through as-is.
_PLAIN = re.compile(r'[A-Za-z0-9_-]{1,64}')


def bench_root(workdir: Path) -> tuple[Path, str | None]:
    """The `plurai_sim/` that owns the marker, and the env alias when `workdir` is a named env."""
    if workdir.parent.name == 'envs' and workdir.parent.parent.name == 'plurai_sim':
        return workdir.parent.parent, workdir.name
    return workdir, None


def bench_id(root: Path) -> str:
    """Read the bench id, minting it on first use. Two concurrent first calls agree: the mint
    is a hard link onto the marker name, which exactly one of them wins."""
    marker = root / MARKER
    if not marker.exists():
        tmp = root / f'{MARKER}.{os.getpid()}.tmp'
        tmp.write_text(secrets.token_hex(8) + '\n', encoding='utf-8')
        try:
            os.link(tmp, marker)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    value = marker.read_text(encoding='utf-8').strip()
    if not _PLAIN.fullmatch(value):
        raise ValueError(f'{marker} does not hold a plain id ({value!r}); refusing to re-mint — a new id orphans '
                         'the brief the old one published. Restore it from git, or delete it to start a fresh bench.')
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    args = parser.parse_args(argv)

    root, alias = bench_root(harness_fingerprint.resolve_workdir(args.workdir))
    if not root.is_dir():
        print(f'ERROR(workspace_id): {root} is not a bench directory.', file=sys.stderr)
        return 2
    try:
        value = bench_id(root)
    except (OSError, ValueError) as exc:
        print(f'ERROR(workspace_id): {exc}', file=sys.stderr)
        return 2
    print(f'{value}.{alias}' if alias else value)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
