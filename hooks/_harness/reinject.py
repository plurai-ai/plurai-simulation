"""Recognising a block reason that the harness hands back as a prompt.

Copilot answers a Stop block by starting a new turn whose ``UserPromptSubmit`` carries the block's
reason as the prompt. The hooks that read what the user typed would then read the plugin's own text as
the user's. The codec that encodes a block remembers its reason here, per session, and the codec that
decodes the next prompt asks whether the prompt is that reason.

The record is one file beside the session's flow markers, which ``flow_cleanup`` removes at session
end. Both halves are best-effort: a record that cannot be written or read means a re-injected reason
is treated as a user prompt, the behaviour without this module, and never a failed hook.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to (``hooks/_hookfail.py``
explains the floor).
"""

from __future__ import annotations

from pathlib import Path

#: The file the last block reason is kept in, inside the session's flow directory. Listed in
#: ``flow_cleanup`` so it is swept with the session's other markers.
RECORD = ".last_block_reason"


def _record_path(session_id: str) -> Path | None:
    """Where ``session_id``'s record lives, or None for no id or an id that is not a plain name."""
    if not session_id:
        return None  # an empty id would fall through to the environment inside ``flow_dir``
    try:
        from _workdir import flow_dir

        directory = flow_dir({"session_id": session_id})
    except Exception:  # noqa: BLE001 - see the module docstring
        return None
    return None if directory is None else directory / RECORD


def remember(session_id: str, reason: str) -> None:
    """Record ``reason`` as the last one this session was blocked with."""
    path = _record_path(session_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason, encoding="utf-8")
    except OSError:
        pass


def is_remembered(session_id: str, prompt: str) -> bool:
    """Whether ``prompt`` is the last block reason of ``session_id``, ignoring surrounding whitespace."""
    path = _record_path(session_id)
    if path is None or not prompt.strip():
        return False
    try:
        return path.read_text(encoding="utf-8").strip() == prompt.strip()
    except OSError:
        return False
