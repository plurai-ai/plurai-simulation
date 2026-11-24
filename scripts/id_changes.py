#!/usr/bin/env python3
"""The id files that differ from `HEAD`, read from git, never from what a session remembers.

    id_changes.py list     [--workdir DIR] [--before SNAPSHOT.json]
    id_changes.py snapshot [--workdir DIR] OUT.json

An edit skill moves a suite id (a scenario set, an eval set) on disk. CI runs the ids that are committed, so a change that is not
committed with its id files runs the old suite and shows no sign of the edit. `list` prints each id file whose id differs from
`HEAD`, with both ids, and says to commit it with the change. The files are the markers (`.scenario_set_id`, `.eval_set_id`,
`.env_id`) in `plurai_sim/` and in each env folder, and the id keys of each env folder's `plurai.env.yaml`, the copies
`read_id.py` reads. A file that git ignores is not an id file here.

`snapshot` records the ids on disk now; `list --before` then names only the id files a step changed since, with the id each held
before and after, so a step that moves a file (a run that re-plants the scenario set) says so even when the file already
differed from `HEAD`. A step that changed none prints nothing.

`--workdir` is `plurai_sim/` or an env folder (default: `$WORKDIR`, else the resolver cascade, as `read_id.py` does).

Output of `list`, one line per file: `ID-FILE <path>: <old> -> <new>` (`none` when a side has no id), then one line of advice. Outside
a git work tree there is no `HEAD`: `list` says so and exits 0. Exit 0 always, unless a usage error: 2.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import read_id

MARKER_NAMES = tuple(read_id.MARKER_KINDS)
MANIFEST_KEYS = tuple(key for _marker, key in read_id.KINDS.values())
NONE = 'none'
CONFLICT = '(merge conflict)'
UNREADABLE = '(unreadable)'


class Change(NamedTuple):
    """One id that differs: where it lives (a repository path, or `path#key` for a manifest key), the id before and the id after."""

    where: str
    old: str | None
    new: str | None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`git -C repo args`; a git that is missing or hangs is a failed run (return code 127), never an exception."""
    try:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(args, 127, '', '')


def sim_dir(workdir: Path) -> Path:
    """`plurai_sim/` of a workdir that is `plurai_sim/` or one of its env folders."""
    workdir = workdir.resolve()
    return workdir.parent.parent if read_id.is_env_dir(workdir) else workdir


def _is_id_file(sim: Path, path: Path) -> bool:
    """A marker or a manifest in `plurai_sim/` or directly in one of its env folders."""
    parent = path.parent
    return (path.name in MARKER_NAMES and parent == sim) or (path.name in (*MARKER_NAMES, read_id.MANIFEST) and parent.parent == sim / 'envs')


def _ids(path_name: str, text: str, rel: str) -> dict[str, str]:
    """The ids a file holds, keyed by `rel` (a marker) or `rel#key` (a manifest)."""
    if read_id.has_conflict_markers(text):
        return {f'{rel}#{key}': CONFLICT for key in MANIFEST_KEYS} if path_name == read_id.MANIFEST else {rel: CONFLICT}
    if path_name != read_id.MANIFEST:
        value = next((line.strip() for line in text.splitlines() if line.strip()), None)
        return {rel: value} if value else {}
    found: dict[str, str] = {}
    for key in MANIFEST_KEYS:
        try:
            value = read_id._scalar(text, key)  # noqa: SLF001 - the one reader of a manifest key
        except read_id._Unparsed:  # noqa: SLF001
            return {f'{rel}#{key}': UNREADABLE for key in MANIFEST_KEYS}
        if value:
            found[f'{rel}#{key}'] = value
    return found


def _top(sim: Path) -> Path | None:
    done = _git(sim, 'rev-parse', '--show-toplevel')
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else None


def _on_disk(sim: Path, top: Path | None) -> dict[str, str]:
    """The ids in the working tree: the files git tracks or would add (an ignored file is skipped), else every file by name."""
    if top is not None:
        done = _git(top, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', str(sim.relative_to(top)) if sim != top else '.')
        paths = [top / name for name in done.stdout.split('\0') if name] if done.returncode == 0 else None
    else:
        paths = None
    if paths is None:
        paths = [*sim.glob('*'), *sim.glob('envs/*/*')]
    found: dict[str, str] = {}
    for path in sorted(paths):
        if not _is_id_file(sim, path) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        found.update(_ids(path.name, text, path.relative_to(top or sim.parent).as_posix()))
    return found


def _at_head(sim: Path, top: Path) -> dict[str, str] | None:
    """The ids `HEAD` holds; empty before the first commit; `None` when git cannot read `HEAD`, which is not the same as holding no id."""
    if _git(top, 'rev-parse', '--verify', '-q', 'HEAD').returncode != 0:
        return {}
    rel = sim.relative_to(top).as_posix() if sim != top else '.'
    done = _git(top, 'ls-tree', '-r', '--name-only', '-z', 'HEAD', '--', rel)
    if done.returncode != 0:
        return None
    found: dict[str, str] = {}
    for name in done.stdout.split('\0'):
        if not name or not _is_id_file(sim, top / name):
            continue
        shown = _git(top, 'show', f'HEAD:{name}')
        if shown.returncode == 0:
            found.update(_ids(Path(name).name, shown.stdout, name))
    return found


def snapshot(workdir: Path) -> dict[str, str]:
    """The ids on disk now, by file (or manifest key)."""
    sim = sim_dir(workdir)
    return _on_disk(sim, _top(sim))


def changed(workdir: Path, *, before: dict[str, str] | None = None) -> list[Change] | None:
    """The ids that differ on disk from `before` (a `snapshot`), or from `HEAD` without it; `None` when `HEAD` is the baseline and there is no git work tree."""
    sim = sim_dir(workdir)
    top = _top(sim)
    if before is None:
        if top is None:
            return None
        before = _at_head(sim, top)
        if before is None:
            return None
    now = _on_disk(sim, top)
    return [Change(where, before.get(where), now.get(where)) for where in sorted({*before, *now}) if before.get(where) != now.get(where)]


def render(found: list[Change] | None, *, since_snapshot: bool) -> list[str]:
    if found is None:
        return ['ID-FILES unknown: this is not a git work tree, so there is no HEAD to compare with.']
    if not found:
        return [] if since_snapshot else ['ID-FILES clean: no id file differs from HEAD.']
    tag = 'ID-FILE-CHANGED' if since_snapshot else 'ID-FILE'
    lines = [f'{tag} {change.where}: {change.old or NONE} -> {change.new or NONE}' for change in found]
    lines.append(
        'CI: committing the id file(s) above changes what CI runs. Review the change before you push it.'
        if since_snapshot
        else 'COMMIT: commit the id file(s) above together with your change. CI runs the committed ids, so a push without them runs the old suite and shows no sign of the edit.'
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('list', 'snapshot'):
        p = sub.add_parser(name)
        p.add_argument('--workdir', help='the plurai_sim directory or an env folder (default: $WORKDIR, else the resolver cascade)')
    sub.choices['list'].add_argument('--before', type=Path, help='a snapshot: list only what changed since it')
    sub.choices['snapshot'].add_argument('out', type=Path)
    args = parser.parse_args(argv)
    import harness_fingerprint  # noqa: PLC0415 - sibling script: the workdir resolver cascade lives there

    workdir = harness_fingerprint.resolve_workdir(args.workdir)
    if args.command == 'snapshot':
        args.out.write_text(json.dumps(snapshot(workdir), indent=1, sort_keys=True) + '\n', encoding='utf-8')
        return 0
    before = None
    if args.before:
        try:
            before = json.loads(args.before.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            print(f'ERROR(id_changes): cannot read the snapshot {args.before}: {error}', file=sys.stderr)
            return 2
    for line in render(changed(workdir, before=before), since_snapshot=before is not None):
        print(line)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
