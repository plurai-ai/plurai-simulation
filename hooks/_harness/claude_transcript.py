"""Claude Code's session transcript: JSONL rows linked by ``uuid`` and ``parentUuid``.

A prompt is a ``type: user`` row whose content is text rather than a tool result. Rows the harness
injects (a skill body, Stop-hook feedback, a subagent's hand-back) carry ``isMeta``; the compaction
summary carries ``isCompactSummary``; a prompt written by anything other than a person carries an
``origin`` whose ``kind`` is not ``human``. Esc is a row of its own (:data:`INTERRUPT_PREFIX`). The account's usage
limit is either a main-thread ``isMeta`` user row carrying :data:`USAGE_LIMIT_KEY` (a grace allowance, then a lift), or
the API-error row of a turn the limit refused (:func:`usage_limit_notes`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from _transcript import object_rows

from _harness.transcript import USAGE_LIMIT_STOPPED, Prompt, iso_epoch

#: How much of the file's tail is read. Larger than the dropped-turn check's, because the row looked
#: for is the PROMPT that started this turn, and every tool call the turn made since sits between it
#: and the end of the file. A prompt beyond this bound reads as unreadable, which leaves the turn-end
#: rules on.
TAIL_BYTES = 2 * 1024 * 1024


#: The row Claude Code writes when the user presses Esc (``[Request interrupted by user]``, or ``... for
#: tool use]`` when a tool call was refused). It has no ``origin``, and nothing else starts with it.
INTERRUPT_PREFIX = "[Request interrupted by user"


def _prompt_text(row: dict[str, Any]) -> str | None:
    """The text of a main-thread user row that starts (or re-enters) a turn, or None.

    None for a row that is part of a turn already running (a tool result), so the caller walks past it.
    """
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return None
    return "\n".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text")


def _thread_rows(path: str) -> Iterator[dict[str, Any]]:
    """The rows of the conversation's thread inside the tail, newest first.

    Read along the THREAD rather than the file: from the newest main-thread row back through
    ``parentUuid``. Following the chain keeps a row merely appended to the file (never part of the
    conversation) from posing as the user. A chain that breaks inside the tail, at a compaction
    boundary or at a missing parent ends the iteration there.
    """
    rows = object_rows(path, tail_bytes=TAIL_BYTES)
    by_uuid = {r["uuid"]: r for r in rows if isinstance(r.get("uuid"), str)}
    row = next((r for r in rows if r.get("type") in ("user", "assistant") and not r.get("isSidechain")), None)
    seen: set[str] = set()
    while row is not None:
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or uuid in seen:
            return
        seen.add(uuid)
        yield row
        parent = row.get("parentUuid")
        row = by_uuid.get(parent) if isinstance(parent, str) else None


def _row_prompt(row: dict[str, Any]) -> Prompt | None:
    """The prompt a thread row is, or None for any other row (a tool result, injected harness text, an answer)."""
    if row.get("type") != "user" or row.get("isMeta") or row.get("isCompactSummary"):
        return None
    text = _prompt_text(row)
    if text is None:
        return None
    origin = row.get("origin")
    injected = isinstance(origin, dict) and origin.get("kind") != "human"
    return Prompt(text, iso_epoch(row.get("timestamp")), injected, text.lstrip().startswith(INTERRUPT_PREFIX))


def prompts_on_thread(path: str, session_id: str = "") -> Iterator[Prompt]:
    """The prompts on the conversation's thread (:func:`_thread_rows`), newest first, injected ones included."""
    for row in _thread_rows(path):
        prompt = _row_prompt(row)
        if prompt is not None:
            yield prompt


def prompts_until(path: str, epoch: float, session_id: str = "") -> tuple[list[Prompt], bool]:
    """The prompts on the thread written after ``epoch``, newest first, and whether the walk REACHED ``epoch``.

    The walk stops at the first thread row stamped at or before ``epoch`` (True). A thread that breaks first (the
    tail, a compaction, a missing parent) is False: what lies between it and ``epoch`` was never read."""
    prompts: list[Prompt] = []
    for row in _thread_rows(path):
        at = iso_epoch(row.get("timestamp"))
        if at is not None and at <= epoch:
            return prompts, True
        prompt = _row_prompt(row)
        if prompt is not None:
            prompts.append(prompt)
    return prompts, False


#: The key Claude Code stamps on the note it injects when the account's usage limit is reached (``"wrap_up"``: finish
#: the piece in hand, then stop and say so) and on the one that lifts it (``"release"``). One note when the limit hits,
#: one when it lifts; the turns between them end on purpose. A limit with no grace writes no note: the refused turn's
#: API-error row stands for it (:func:`_limit_event`).
USAGE_LIMIT_KEY = "usageLimitNote"
#: The key as it is written at the top level of a row. Both quotes are part of it, so a mention inside a JSON string
#: (where every quote is escaped) never matches; a candidate line is still parsed and checked.
_USAGE_LIMIT_NEEDLE = b'"' + USAGE_LIMIT_KEY.encode() + b'"'
#: The key Claude Code stamps on the row that stands for a turn the API refused; with :data:`LIMIT_STOP_API_ERROR` (or,
#: before Claude Code wrote ``apiError``, the class :data:`LIMIT_STOP_ERROR`) the account's usage limit refused it.
_API_ERROR_NEEDLE = b'"isApiErrorMessage"'
LIMIT_STOP_API_ERROR = "usage_limit_reached"
#: The error class ``flow_interrupt.NON_RECOVERABLE`` reads as a usage cap: every such row is a "You've hit your … limit".
LIMIT_STOP_ERROR = "rate_limit"


def _limit_event(row: Any) -> tuple[str, float | None] | None:
    """``(kind, epoch)`` when ``row`` is a main-thread usage-limit event, else None.

    A ``type: user`` row whose TOP-LEVEL :data:`USAGE_LIMIT_KEY` is a non-empty string is a note (``wrap_up``,
    ``release``, …). A ``type: assistant`` row with ``isApiErrorMessage`` that the limit refused is
    :data:`USAGE_LIMIT_STOPPED`; any other API error (a dropped connection, an overload) is not the limit."""
    if not isinstance(row, dict) or row.get("isSidechain"):
        return None
    if row.get("type") == "user":
        kind = row.get(USAGE_LIMIT_KEY)
        return (kind, iso_epoch(row.get("timestamp"))) if isinstance(kind, str) and kind else None
    if row.get("type") == "assistant" and row.get("isApiErrorMessage") is True and (
        row.get("apiError") == LIMIT_STOP_API_ERROR or row.get("error") == LIMIT_STOP_ERROR
    ):
        return USAGE_LIMIT_STOPPED, iso_epoch(row.get("timestamp"))
    return None


def usage_limit_notes(path: str, session_id: str = "") -> list[tuple[str, float | None]]:
    """``(kind, epoch)`` of every usage-limit event in the transcript at ``path`` (:func:`_limit_event`), newest first.

    The WHOLE file is read, not the bounded tail the prompt readers use: the limit is written once, when it hits, and
    every turn after it lies between that row and the end of the file, so a tail would lose it after a few large tool
    results. Only a line holding one of the keys is parsed. A line that does not parse (one mid-write) or that holds a
    key anywhere but at the top level is passed over. Nothing readable is the empty list; never raises."""
    events: list[tuple[str, float | None]] = []
    if not path:
        return events
    try:
        with open(path, "rb") as handle:
            for line in handle:
                if _USAGE_LIMIT_NEEDLE not in line and _API_ERROR_NEEDLE not in line:
                    continue
                try:
                    event = _limit_event(json.loads(line))
                except ValueError:
                    continue
                if event is not None:
                    events.append(event)
    except OSError:
        return []
    events.reverse()
    return events


def turn_prompt(path: str, session_id: str = "") -> Prompt | None:
    """The prompt that started the turn ending at the newest main-thread row of ``path``.

    The first prompt on the thread (:func:`prompts_on_thread`) that is not a tool result and not
    injected harness text is what started the turn; None when the thread breaks before one.
    """
    return next(prompts_on_thread(path, session_id), None)
