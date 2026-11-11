"""Copilot CLI's session transcript: ``session-state/<id>/events.jsonl``.

Each row is ``{type, data, id, timestamp, parentId}``. A prompt a person typed is a ``user.message``
row (``data.content``); an ask answer is a ``human_response.recorded`` row and the ask's own
``tool.execution_complete``, never a ``user.message``. The model's turn is ``assistant.message`` rows,
one per model call: a row that requests tools carries them in ``data.toolRequests``, and the one that
ends the turn requests none. ``model.*`` rows are the CLI's own telemetry and are large, so the tail
read is larger than Claude Code's.

A sub-agent's rows share the file with the main thread's and carry ``agentId`` (top level) or
``data.parentToolCallId``. A prompt the CLI writes on its own account (autopilot's continue nudge) names
a ``data.source``; a typed one names none. Both readers below look at the main thread only, and a prompt
that is not a typed one reads as injected, so a guard that stands down for a person never does so for it.

``events.jsonl`` carries no marker that tells a re-injected Stop block reason from a typed prompt, so
a prompt equal to the reason this session was last blocked with is reported as injected too
(``_harness/reinject.py`` keeps that record).
"""

from __future__ import annotations

import os
import time
from typing import Any

from _transcript import object_rows

from _harness import reinject
from _harness.transcript import Prompt, iso_epoch

TAIL_BYTES = 8 * 1024 * 1024

#: Copilot writes ``events.jsonl`` through a writer that flushes 100 ms after the last row, and starts the
#: ``Stop`` hook about 2 ms after the model's final message, so the final row is usually not on disk when the
#: hook begins to read. The reader waits at most this long for it, polling every ``FLUSH_POLL_SECONDS``: ten
#: times the flush delay, and far inside the hook's own deadline.
FLUSH_WAIT_SECONDS = 1.0
FLUSH_POLL_SECONDS = 0.025

#: The row types that make up the conversation. The rest (``model.*`` telemetry, ``session.*``
#: bookkeeping) sits beside it: it neither starts a turn nor is a place the thread is read from.
_CONVERSATION = frozenset(
    {
        "user.message",
        "assistant.message",
        "assistant.turn_start",
        "assistant.turn_end",
        "tool.execution_start",
        "tool.execution_complete",
        "human_response.recorded",
        "system.message",
    }
)


def _data(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data")
    return data if isinstance(data, dict) else {}


def _subagent(row: dict[str, Any]) -> bool:
    """Whether a sub-agent wrote ``row``: its prompt, replies and tool calls are not the main thread's."""
    data = _data(row)
    return bool(row.get("agentId") or data.get("agentId") or data.get("parentToolCallId"))


def _main_thread(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation rows the main thread wrote, in the order ``rows`` has them."""
    return [r for r in rows if r.get("type") in _CONVERSATION and not _subagent(r)]


def _injected(row: dict[str, Any], session_id: str, content: str) -> bool:
    """Whether the CLI or this plugin wrote the prompt rather than a person: a prompt the CLI originates
    names its ``source``, and a block reason handed back is the one on record."""
    source = _data(row).get("source")
    return (isinstance(source, str) and source not in ("", "user")) or reinject.is_remembered(session_id, content)


def turn_prompt(path: str, session_id: str = "") -> Prompt | None:
    """The prompt that started the turn ending at the newest main-thread row of ``path``.

    Read along the THREAD rather than the file: from the newest main-thread conversation row back
    through ``parentId`` to the first ``user.message``. A prompt a sub-agent was handed, or a row
    appended that no main-thread row descends from, is not what started the turn; a chain that breaks
    inside the tail is unreadable.

    When that prompt is the plugin's own block reason coming back, or one the CLI wrote itself, it is
    returned as injected and the turn is not attributed to any earlier prompt: the file cannot tell a
    second block's reason from a typed prompt once a later block has replaced it in the record, so an
    earlier prompt is not trusted to be the user's.
    """
    rows = object_rows(path, tail_bytes=TAIL_BYTES)
    by_id = {r["id"]: r for r in rows if isinstance(r.get("id"), str)}
    row = next(iter(_main_thread(rows)), None)
    seen: set[str] = set()
    while row is not None:
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in seen:
            return None
        seen.add(row_id)
        if row.get("type") == "user.message" and not _subagent(row):
            content = _data(row).get("content")
            if not isinstance(content, str):
                return None
            return Prompt(content, iso_epoch(row.get("timestamp")), _injected(row, session_id, content))
        parent = row.get("parentId")
        row = by_id.get(parent) if isinstance(parent, str) else None
    return None


def _only_task_complete(data: dict[str, Any]) -> bool:
    """Whether the row's one tool request is Copilot's ``task_complete``, the call that ends a turn."""
    requests = data.get("toolRequests")
    return isinstance(requests, list) and len(requests) == 1 and isinstance(requests[0], dict) and requests[0].get("name") == "task_complete"


def _task_complete_text(data: dict[str, Any]) -> Any:
    """The text a ``task_complete`` row hands over: the call's ``summary``, which the model writes in place of ``content``."""
    arguments = data["toolRequests"][0].get("arguments")
    summary = arguments.get("summary") if isinstance(arguments, dict) else None
    return summary if isinstance(summary, str) and summary.strip() else data.get("content")


def final_message(path: str) -> str | None:
    """The text of the current turn's final assistant message, or None when ``path`` holds none to judge.

    The newest main-thread ``assistant.message`` row, provided it is newer than the turn's own prompt
    and requests no tool: one that does is the middle of a turn, and its text is not a final message.
    The exception is a row whose only request is ``task_complete``: Copilot ends a turn with that call, and
    the model's report is its ``summary`` argument (``content`` is empty there).
    Reaching a prompt first means the turn's own message is not in the file yet, and the text beyond
    it belongs to an earlier turn. Empty text is none.
    """
    for row in _main_thread(object_rows(path, tail_bytes=TAIL_BYTES)):
        if row.get("type") == "user.message":
            return None
        if row.get("type") != "assistant.message":
            continue
        data = _data(row)
        if _only_task_complete(data):
            content = _task_complete_text(data)
        elif data.get("toolRequests"):
            return None
        else:
            content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content
    return None


def awaited_final_message(path: str) -> str | None:
    """``final_message``, waiting up to ``FLUSH_WAIT_SECONDS`` for the row to be flushed.

    A transcript that does not exist is not waited for: nothing will write it within the wait. A file that
    holds a final message returns at once; one that does not is read again every ``FLUSH_POLL_SECONDS``
    until it does or the wait ends, and then None, as ``final_message`` says.
    """
    deadline = time.monotonic() + FLUSH_WAIT_SECONDS
    while True:
        message = final_message(path)
        if message is not None or not os.path.isfile(path) or time.monotonic() >= deadline:
            return message
        time.sleep(FLUSH_POLL_SECONDS)
