"""An unfilled template placeholder (``{BRIEF}``) in an edit-session message: refuse the call once.

A skill hands a sub-agent or a later turn a message template with a slot such as ``{BRIEF}`` to fill in. A message
sent with the slot still in it reaches the backend's edit session as literal text, and the session edits against
nothing. The check reads every string of the call's arguments for ``{NAME}`` (an upper-case name of three or more
characters, not ``${NAME}``, which is a shell variable).

Code can carry the same shape on purpose (``f'{WORLD}#orders'`` in an edit-env message), so the refusal is ONCE: the
first call is refused with what to do, and the identical call sent again goes through. The record of the refused call
lives only in the run's ``<workdir>/artifacts/``, never elsewhere on the host. It is never created here; without it,
nothing is refused (a refusal that cannot be counted would refuse the deliberate retry too).

Three callers share it: the PreToolUse hook (``placeholder_guard.py``), ``scripts/mcp_call.py`` and the stdio bridge
``scripts/mcp_bridge.py``. Stdlib only, importable under the oldest interpreter ``python3`` can resolve to.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

#: The backend tools whose arguments carry a message to an edit session.
TOOLS = ("edit_session_start", "interact_edit_session", "interact_with_scenario_request")
#: An unfilled slot: ``{NAME}``, never ``${NAME}``.
PATTERN = re.compile(r"(?<!\$)\{[A-Z][A-Z0-9_]{2,}\}")
#: The record of refused calls, in the state directory.
STATE_FILE = ".placeholder_denied.json"
#: How many refused calls the record keeps; the oldest goes first.
CAP = 50

MESSAGE = (
    "plurai: this message still carries {slot}, a placeholder that was never filled in, so the backend would read it "
    "as literal text. Replace {slot} with the text it stands for and send the call again. If the backend must see "
    "{slot} exactly as written, send the same call again unchanged."
)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def slot(tool: str, arguments: Any) -> str | None:
    """The first unfilled placeholder in the arguments of ``tool`` (one of :data:`TOOLS`), or None."""
    if tool not in TOOLS:
        return None
    for text in _strings(arguments):
        if found := PATTERN.search(text):
            return found.group()
    return None


def _digest(tool: str, arguments: Any) -> str:
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{tool}\n{canonical}".encode("utf-8")).hexdigest()


def state_dir(start: Path | None = None) -> Path | None:
    """Where the record lives: ``$WORKDIR/artifacts``, else ``<workdir resolved from start>/artifacts``, whichever
    exists first, else None. Nothing is created."""
    import _workdir  # noqa: PLC0415 - a sibling module, loaded only when a placeholder was found

    candidates: list[Path | None] = []
    named = os.environ.get("WORKDIR", "").strip()
    if named:
        candidates.append(Path(named) / "artifacts")
    workdir = _workdir.resolve_workdir(start)
    candidates.append(workdir / "artifacts" if workdir else None)
    for candidate in candidates:
        try:
            if candidate is not None and candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def refusal(tool: str, arguments: Any, where: Path | None) -> str | None:
    """The message refusing this call, or None to let it through: None when it carries no placeholder, when the
    identical call was refused before (recorded in ``where``), or when there is nowhere to record the refusal."""
    found = slot(tool, arguments)
    if found is None or where is None:
        return None
    path = where / STATE_FILE
    digest = _digest(tool, arguments)
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
        seen = [d for d in seen if isinstance(d, str)] if isinstance(seen, list) else []
    except (OSError, ValueError):
        seen = []
    if digest in seen:
        return None
    try:
        tmp = path.with_name(f".{STATE_FILE}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps([*seen, digest][-CAP:]), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return None
    return MESSAGE.format(slot=f"`{found}`")
