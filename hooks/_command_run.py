"""The command run id: what a command stamps into its flow directory, and how a chokepoint reads it back.

A command (a flow, or a skill that is not a flow but starts backend jobs) gets ONE id, minted by
``scripts/open_epoch.py`` when the command starts. The backend groups every job and upload the command starts
under it. This module holds the pieces both halves share: the id's shape, the file it lives in, the
environment variable a child process inherits it through, and the one function that puts it into a backend
call's arguments.

The id is a grouping label, not a credential and not a trace id. It is written beside the flow window
(``~/.plurai/flow/<session id>/``, or the agent's ``plurai_sim/`` when the session has no id) and leaves with the
window.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

# `python3 -I` (how shell callers start this file) leaves its directory off sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

#: Beside the flow window. One line: the id.
ID_FILE = ".command_run_id"

#: Opens the hook gate for a command that is not a flow; holds the command's name (``registry.GATE_MARKERS``).
SCOPE_MARKER = ".plurai_command_scope"

#: How a child process (the SDK runner, a nested ``claude -p``) receives the id.
ENV_VAR = "PLURAI_COMMAND_RUN_ID"

#: The argument every job-start and upload tool of the backend takes.
ARGUMENT = "command_run_id"

#: The backend accepts at most this many printable, non-blank characters and refuses anything else.
_SHAPE = re.compile(r"[!-~]{1,128}")


def valid(value: object) -> bool:
    """Whether the backend would take ``value``: 1 to 128 visible ASCII characters, no whitespace."""
    return isinstance(value, str) and _SHAPE.fullmatch(value) is not None


def mint() -> str:
    return "cmd_" + uuid.uuid4().hex


def inherited(env: Mapping[str, str] | None = None) -> str | None:
    """The id a parent process handed this one, when it is one the backend would take."""
    value = (os.environ if env is None else env).get(ENV_VAR, "").strip()
    return value if valid(value) else None


def open_id(flowdir: Path, env: Mapping[str, str] | None = None) -> str:
    """Start a new command's id in ``flowdir``: the inherited one, else a fresh one. Raises ``OSError``."""
    value = inherited(env) or mint()
    flowdir.mkdir(parents=True, exist_ok=True)
    (flowdir / ID_FILE).write_text(value, encoding="utf-8")
    return value


def read_file(base: Path) -> str | None:
    try:
        value = (base / ID_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return value if valid(value) else None


def keep_or_open(flowdir: Path, env: Mapping[str, str] | None = None) -> str:
    """The id already in ``flowdir``, else a new one (a resumed window that has none). Raises ``OSError``."""
    return read_file(flowdir) or open_id(flowdir, env)


def clear(bases: list[Path]) -> None:
    """Remove the id (and with it the command scope) from every directory a window can live in."""
    for base in bases:
        for name in (ID_FILE, SCOPE_MARKER):
            try:
                (base / name).unlink()
            except OSError:
                pass


def current(payload: dict | None = None, env: Mapping[str, str] | None = None) -> str | None:
    """The id of the command in progress, or None.

    An id the environment carries wins (a parent process chose it for this one); otherwise the id beside the
    session's window, then beside the agent's. Never raises.
    """
    try:
        found = inherited(env)
        if found:
            return found
        from _workdir import flow_dir, resolve_workdir  # noqa: PLC0415 - the resolver is only needed on this path

        flow = flow_dir(payload)
        value = read_file(flow) if flow is not None else None
        if value:
            return value
        # The workdir cascade (a git call and globs) runs only when the flow directory holds no id.
        legacy = resolve_workdir()
        value = read_file(legacy) if legacy is not None and legacy != flow else None
        if value:
            return value
    except Exception:  # noqa: BLE001 - a label that cannot be found is left off, never a failed call
        pass
    return None


def for_call(tool: str, payload: dict | None = None, env: Mapping[str, str] | None = None) -> str | None:
    """The id a backend call to ``tool`` carries, or None when ``tool`` takes none.

    A job start while no command is in progress is itself a command starting that nothing announced (a Codex skill
    load runs no hook), so it opens a command scope of its own: its later jobs share the id until the next command.
    A read or a poll never opens one.
    """
    from registry import COMMAND_RUN_ID_TOOLS  # noqa: PLC0415 - registry is a sibling of this module

    if tool not in COMMAND_RUN_ID_TOOLS:
        return None
    return current(payload, env) or _open_unannounced(payload)


def _open_unannounced(payload: dict | None) -> str | None:
    try:
        from _workdir import flow_dir, resolve_workdir  # noqa: PLC0415

        target = flow_dir(payload)
        if target is None:
            target = resolve_workdir()
            if target is None or not target.is_dir():
                return None
        scripts = str(Path(_HERE).parent / "scripts")
        if scripts not in sys.path:
            sys.path.append(scripts)
        from open_epoch import open_scope  # noqa: PLC0415

        return open_scope("backend-call", target)
    except Exception:  # noqa: BLE001 - a label that cannot be written is left off, never a failed call
        return None


def with_id(tool: str, arguments: dict, command_run_id: str | None) -> dict:
    """``arguments`` with the id added, when ``tool`` takes it, ``command_run_id`` is a usable id, and the caller named none.

    ``tool`` is the verb the backend lists (``generate_scenarios_start``). The arguments are returned as they
    came for every other tool: the backend refuses an argument a tool does not declare.
    """
    if not valid(command_run_id):
        return arguments
    from registry import COMMAND_RUN_ID_TOOLS  # noqa: PLC0415 - registry is a sibling of this module

    if tool not in COMMAND_RUN_ID_TOOLS or arguments.get(ARGUMENT):
        return arguments
    return {**arguments, ARGUMENT: command_run_id}


if __name__ == "__main__":
    # `_command_run.py current` prints the id of the command in progress and exits 0, or prints nothing and exits 1:
    # the entry for shell callers that hand it to a child process.
    if sys.argv[1:] == ["current"]:
        found = current()
        if found:
            print(found)
        sys.exit(0 if found else 1)
    sys.exit(64)
