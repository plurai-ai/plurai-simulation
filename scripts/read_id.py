#!/usr/bin/env python3
"""The one reader of the committed suite ids.

    read_id.py scenarios|evals|env [--workdir DIR] [--absent TEXT]

Prints the id, one line. Exit 1: no copy holds it (stderr names where it looked). Exit 2: a copy that is not an id (a merge
conflict included), or a usage error. `--workdir` defaults to `$WORKDIR`, else the resolver cascade, as `write_id_file.py`
does.

`--absent TEXT` is for a caller that works without the id: when no copy holds it, TEXT is printed and the exit is 0. Only
that case is absent. A copy that is not an id still exits 2, so a caller never reads a conflict as "no id". Callers bind the
id as `ID="$(read_id.py KIND)" || exit $?` (or `--absent`), never `2>/dev/null || …`, which hides the exit 2.

WHERE AN ID LIVES. A scenario or eval set id has up to three copies: the env folder's marker
(`plurai_sim/envs/<env>/.scenario_set_id`, `plurai_sim/envs/<env>/.eval_set_id`), the key in that folder's
`plurai.env.yaml`, and the root marker (`plurai_sim/.scenario_set_id`, `plurai_sim/.eval_set_id`). CI reads them in that order
and refuses copies that disagree. This reader takes the same order, so a skill edits and runs the suite CI tests, and
`write_id_file.py` keeps the copies in step.

* The workdir is an env folder (`plurai_sim/envs/<active>/`): its marker, its manifest key, then the root marker. The root
  marker counts only when the root copies this env, which is when `ci.yaml` names it or there is no `ci.yaml` (the
  writer's rule): the root of a repo whose CI tests another env holds that env's id.
* The workdir is `plurai_sim/` itself (no `.active-env`, as in a fresh clone, since `.active-env` is ignored by git): the
  suite is the one in the env folder `ci.yaml` names (`default` without one), read in the same order, marker, manifest
  key, then the root marker. `write_id_file.py` writes that folder's copies from this workdir too.
* The env id is not copied to the root. CI reads it from the manifest alone; the env folder's `.env_id` is this
  machine's deploy handle, written from the manifest on a switch, and comes first so a skill acts on the env deployed
  here. In `plurai_sim/` the root `.env_id` comes last.

`ci.yaml` that does not parse counts as no `ci.yaml`: CI refuses it by name, and until then the root copies every env.

A copy that holds git conflict markers (`<<<<<<<` … `>>>>>>>`, what a merge leaves when two branches moved the same id) is not read:
the reader exits 2 and says to resolve the merge conflict, take main's id and re-apply the edit with the skill that made it.

Copies that disagree: the first one is printed, and a warning on stderr names every copy. The `/simulation-ci` readiness
check asks which id the suite should test and writes it into every copy.

The manifest key and `ci.yaml`'s `env` are top-level scalars: they are read with PyYAML when it imports and with a line
reader when it does not, so a skill snippet's bare `python3` runs this.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

MANIFEST = 'plurai.env.yaml'
CI_YAML = 'ci.yaml'
DEFAULT_CI_ENV = 'default'

#: The marker file and the manifest key of each kind.
KINDS = {
    'scenarios': ('.scenario_set_id', 'scenario_set_id'),
    'evals': ('.eval_set_id', 'eval_set_id'),
    'env': ('.env_id', 'env_id'),
}
#: The kind of each marker file, for callers that name a marker.
MARKER_KINDS = {marker: kind for kind, (marker, _key) in KINDS.items()}
#: The kinds whose root marker copies the env folder's.
ROOT_COPY_KINDS = ('scenarios', 'evals')

#: What a backend id looks like; the writer validates with the same pattern.
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_SCALAR = re.compile(r"""^(?P<key>[A-Za-z_][\w-]*):[ \t]*(?P<quote>['"]?)(?P<value>[^'"#\s]*)(?P=quote)[ \t]*(?:\#.*)?$""")


CONFLICT_ADVICE = "resolve the merge conflict, take main's id and re-apply your edit with the skill that made it"


def has_conflict_markers(text: str) -> bool:
    """Whether `text` holds an unresolved git merge conflict: an opening and a closing marker line."""
    lines = text.splitlines()
    return any(line.startswith('<<<<<<<') for line in lines) and any(line.startswith('>>>>>>>') for line in lines)


class ConflictError(Exception):
    """An id file holds git conflict markers; the message says how to resolve it."""


class Copy(NamedTuple):
    """One file that holds an id: where (a path, or a manifest key and its path) and the value it holds."""

    where: str
    value: str


class _Unparsed(Exception):
    """A YAML file PyYAML refuses."""


def _scalar(text: str, key: str) -> str | None:
    """The string under top-level `key` of a YAML mapping; None when the key or a string value is missing.

    Raises `_Unparsed` when PyYAML refuses the text. Without PyYAML a line reader finds the key.
    """
    try:
        import yaml  # noqa: PLC0415 - optional: the line reader below covers an interpreter without it
    except ImportError:
        for line in text.splitlines():
            match = _SCALAR.match(line)
            if match and match['key'] == key:
                return match['value'] or None
        return None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise _Unparsed from error
    value = document.get(key) if isinstance(document, dict) else None
    return (value.strip() or None) if isinstance(value, str) else None


def ci_env(sim: Path) -> str | None:
    """The env alias `ci.yaml` names (`default` when the key is absent); None when there is no `ci.yaml` that parses."""
    try:
        return _scalar((sim / CI_YAML).read_text(encoding='utf-8'), 'env') or DEFAULT_CI_ENV
    except (OSError, UnicodeDecodeError, _Unparsed):
        return None


def ci_env_dir(sim: Path) -> Path:
    """The env folder CI reads in `sim` (`plurai_sim/`): the alias `ci.yaml` names, else `default`."""
    return sim / 'envs' / (ci_env(sim) or DEFAULT_CI_ENV)


def root_copies(env_dir: Path) -> bool:
    """Whether the root markers of `plurai_sim/` copy this env folder's: `ci.yaml` names it, or there is no `ci.yaml`."""
    alias = ci_env(env_dir.parent.parent)
    return alias is None or alias == env_dir.name


def is_env_dir(workdir: Path) -> bool:
    return workdir.parent.name == 'envs'


def scan(workdir: Path, kind: str) -> tuple[list[Copy], list[str]]:
    """``(copies, conflicted)`` for `kind`: the readable copies in read order (see the module docstring) and the names of the
    files that hold merge conflict markers, which are not copies."""
    marker, key = KINDS[kind]
    found: list[Copy] = []
    conflicted: list[str] = []

    def read(path: Path, where: str) -> str | None:
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return None
        if has_conflict_markers(text):
            conflicted.append(where)
            return None
        return text

    def from_marker(directory: Path) -> None:
        text = read(directory / marker, str(directory / marker))
        value = next((line.strip() for line in text.splitlines() if line.strip()), None) if text is not None else None
        if value is not None:
            found.append(Copy(str(directory / marker), value))

    def from_env_dir(env_dir: Path) -> None:
        from_marker(env_dir)
        where = f'`{key}` in {env_dir / MANIFEST}'
        text = read(env_dir / MANIFEST, where)
        try:
            value = _scalar(text, key) if text is not None else None
        except _Unparsed:
            value = None
        if value is not None:
            found.append(Copy(where, value))

    if is_env_dir(workdir):
        from_env_dir(workdir)
        if kind in ROOT_COPY_KINDS and root_copies(workdir):
            from_marker(workdir.parent.parent)
        return found, conflicted
    env_dir = ci_env_dir(workdir)
    if env_dir.is_dir():
        from_env_dir(env_dir)
    from_marker(workdir)
    return found, conflicted


def copies(workdir: Path, kind: str) -> list[Copy]:
    """Every copy of `kind`'s id that the workdir's suite reads, in read order (see the module docstring).

    Raises `ConflictError` when a copy holds merge conflict markers: reading around it would return another copy's id as if it were
    the suite's."""
    found, conflicted = scan(workdir, kind)
    if conflicted:
        raise ConflictError(f'{", ".join(conflicted)} has a merge conflict: {CONFLICT_ADVICE}.')
    return found


def committed_id(workdir: Path, kind: str) -> str | None:
    """The id the workdir's suite reads for `kind`: the first copy, or None when there is none."""
    found = copies(workdir, kind)
    return found[0].value if found else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kind', choices=sorted(KINDS))
    parser.add_argument('--workdir', help='the plurai_sim directory or an env folder (default: $WORKDIR, else the resolver cascade)')
    parser.add_argument('--absent', metavar='TEXT', help='print TEXT and exit 0 when no copy holds the id; a copy that is not an id still exits 2')
    args = parser.parse_args(argv)
    import harness_fingerprint  # noqa: PLC0415 - sibling script: the workdir resolver cascade lives there

    workdir = harness_fingerprint.resolve_workdir(args.workdir)
    marker, key = KINDS[args.kind]
    try:
        found = copies(workdir, args.kind)
    except ConflictError as error:
        print(f'ERROR(read_id): {error}', file=sys.stderr)
        return 2
    if not found and args.absent is not None:
        print(args.absent)
        return 0
    if not found:
        print(f'read_id: no {marker} for {workdir}: no marker, no `{key}` in an env manifest.', file=sys.stderr)
        return 1
    for copy in found:
        if not ID_PATTERN.match(copy.value):
            print(f'ERROR(read_id): {copy.where} does not hold an id (it holds {copy.value[:40]!r}).', file=sys.stderr)
            return 2
    if len({copy.value for copy in found}) > 1:
        held = ', '.join(f'{copy.where} holds {copy.value}' for copy in found)
        print(f'WARNING(read_id): the copies of {marker} disagree: {held}. Read {found[0].value}, the id CI reads. '
              'Ask the user which id the suite should test; /simulation-ci (its readiness check) writes it into every copy.', file=sys.stderr)
    print(found[0].value)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
