#!/usr/bin/env python3
"""Run a plugin script under a plugin-owned interpreter that has the packages the script needs, pinned.

    tool_python.py <toolset> <script> [args...]

Some checks need packages no interpreter on the machine is promised to carry: `mock_contract` runs the bench's
contract tests, which import pytest and requests, and the interpreters `resolve-python.sh` picks only had to import
PyYAML (an agent's venv lists pytest as a dev dependency, or not at all). So the plugin keeps its OWN venv per
toolset, under `$PLURAI_STATE_HOME` (else `~/.plurai`) / `tools/<toolset>-<key>`, `key` covering the pins and the base
interpreter's version. It is built once — `uv` when it is on PATH, else `python -m venv` and pip — into a scratch
directory that is renamed into place only after the pinned imports succeed, so a half-built venv is never used and two
concurrent builds leave one. It never depends on the agent's venv, and the script runs with `-E -s`, so the agent's
`PYTHONPATH` and user site cannot shadow what it imports.

The bearer is not handed to the installer: provisioning runs without `PLURAI_TOKEN`. The script itself is
exec'd with this process's environment, which its caller decided. Windows has no exec (`os.execv` starts a new process
and this one exits at once, losing the script's exit code and output), so there the script runs as a child and its exit
code is this process's.

Exit codes: the script's own, once it runs · 2 the interpreter could not be provisioned (no network, no `venv`/pip,
an unknown toolset) — no verdict, with the reason on stderr in the `mock contract: no verdict — …` shape.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402

#: Per toolset, the exact distributions its scripts import. Pinned: a check's verdict must not move with a release.
TOOLSETS: dict[str, tuple[str, ...]] = {
    'mock_contract': ('pytest==8.3.5', 'requests==2.32.3'),
}
#: The modules that prove a toolset's venv is usable, imported before it is renamed into place.
PROBES: dict[str, str] = {'mock_contract': 'import pytest, requests'}
#: The oldest Python a toolset's scripts run on. The world's witness helper env-gen ships uses 3.10 syntax at import time,
#: so below it `uv` is asked for a newer interpreter (it fetches one), and without `uv` there is no verdict.
PYTHON_FLOOR: dict[str, tuple[int, int]] = {'mock_contract': (3, 10)}
READY = '.plurai_tool_ready'
_INSTALL_TIMEOUT = 600


def _no_verdict(why: str) -> int:
    print(f'mock contract: no verdict — {why}', file=sys.stderr)
    return 2


def state_home() -> Path:
    root = os.environ.get('PLURAI_STATE_HOME')
    return Path(root) if root else Path.home() / '.plurai'


def base_python() -> str:
    """The interpreter a venv is made from: the base one behind a venv's python, else this one."""
    return getattr(sys, '_base_executable', '') or sys.executable


def requested_python(toolset: str) -> str | None:
    """The interpreter (or, for `uv`, the version spec) a toolset's venv is made from; None when only `uv` could find one."""
    floor = PYTHON_FLOOR.get(toolset, (3, 9))
    if sys.version_info[:2] >= floor:
        return base_python()
    return f'>={floor[0]}.{floor[1]}' if shutil.which('uv') else None


def venv_dir(toolset: str) -> Path:
    key = json.dumps([toolset, TOOLSETS[toolset], list(sys.version_info[:2]), list(PYTHON_FLOOR.get(toolset, (3, 9)))])
    return state_home() / 'tools' / f'{toolset}-{hashlib.sha256(key.encode()).hexdigest()[:12]}'


def _python_in(venv: Path) -> Path:
    return _platform.venv_python(venv)


def _ready(venv: Path) -> bool:
    return (venv / READY).is_file() and os.access(_python_in(venv), os.X_OK)


def _run(argv: list[str]) -> str | None:
    """None on success, else the last line of what the step printed."""
    # The installer reaches a package index: it gets no bearer, and no activated venv to install into instead.
    env = {k: v for k, v in os.environ.items() if k not in ('PLURAI_TOKEN', 'VIRTUAL_ENV')}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=_INSTALL_TIMEOUT, check=False, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'{Path(argv[0]).name}: {exc}'
    if done.returncode == 0:
        return None
    tail = (done.stderr or done.stdout or '').strip().splitlines()
    return f'{Path(argv[0]).name} exited {done.returncode}: {tail[-1] if tail else "no output"}'


def provision(toolset: str) -> tuple[Path | None, str]:
    """(the toolset's python, '') — built now if it is not there yet — or (None, why it could not be)."""
    venv = venv_dir(toolset)
    if _ready(venv):
        return _python_in(venv), ''
    scratch = venv.with_name(f'{venv.name}.build-{os.getpid()}')
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.parent.mkdir(parents=True, exist_ok=True)
    python = _python_in(scratch)
    uv, pins, base = shutil.which('uv'), list(TOOLSETS[toolset]), requested_python(toolset)
    if base is None:
        floor = PYTHON_FLOOR[toolset]
        here = f'{sys.version_info[0]}.{sys.version_info[1]}'
        return None, f'the plugin\'s {toolset} interpreter needs Python {floor[0]}.{floor[1]}+, this one is {here} and uv is not on PATH'
    if uv:
        create, install = [uv, 'venv', '--quiet', '--python', base, str(scratch)], [uv, 'pip', 'install', '--quiet', '--python', str(python)]
    else:
        create, install = [base, '-m', 'venv', str(scratch)], [str(python), '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check']
    steps = [create, *([[*install, *pins]] if pins else [])]
    for step in [*steps, [str(python), '-I', '-c', PROBES[toolset]]]:
        if (failed := _run(step)) is not None:
            shutil.rmtree(scratch, ignore_errors=True)
            return None, f'could not provision the plugin\'s {toolset} interpreter ({failed})'
    (scratch / READY).write_text(json.dumps({'pins': TOOLSETS[toolset]}) + '\n', encoding='utf-8')
    try:
        os.rename(scratch, venv)
    except OSError:
        # A concurrent build landed first: use it, drop this one.
        shutil.rmtree(scratch, ignore_errors=True)
    return (_python_in(venv), '') if _ready(venv) else (None, f'the plugin\'s {toolset} interpreter at {venv} is not usable')


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2 or args[0] not in TOOLSETS:
        return _no_verdict(f'usage: tool_python.py <{"|".join(TOOLSETS)}> <script> [args...]')
    toolset, script, rest = args[0], args[1], args[2:]
    python, why = provision(toolset)
    if python is None:
        return _no_verdict(why)
    return launch([str(python), '-E', '-s', script, *rest])


def launch(argv: list[str]) -> int:
    """Run ``argv`` as this process: exec it, or on Windows run it as a child and return its exit code."""
    if _platform.WINDOWS:
        return subprocess.run(argv, check=False).returncode
    os.execv(argv[0], argv)
    return 2  # unreachable: execv replaces this process


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
