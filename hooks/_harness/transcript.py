"""Reading a session transcript: which prompt started the turn that is ending, and whether a gate was answered.

``report_guard`` stands its turn-end rules down when the user typed something after the flow was
armed, and that needs the transcript the Stop payload names. The transcript's format is the
harness's own, so each harness has a reader module (``<harness>_transcript.py``) with
``turn_prompt(path, session_id)``, and this module picks the reader by harness and hands back one
harness-neutral ``Prompt``. A reader may also report the account's usage limit (``usage_limit_notes``): a turn the
limit ended is a pause, not a stall (``simulate_continue``), and the turn-end rules stand down for it (``report_guard``);
the first message a person types after the limit stopped the session resumes the run (``prompts_until``).

``None`` is the only way a reader says it cannot tell: a harness with no reader, a missing or empty
file, rows that are not that harness's format (another harness's transcript, or a file that is no
transcript at all), a thread that breaks inside the bounded tail it read. A caller treats ``None`` as
"no evidence", never as an answer, and never reads one harness's file with another's reader.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to (``hooks/_hookfail.py``
explains the floor).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: Harness -> the reader module that parses its transcript. A harness without an entry has no reader.
READERS = {"claude": "claude_transcript", "copilot": "copilot_transcript", "codex": "codex_transcript"}


@dataclass(frozen=True)
class Prompt:
    """The prompt that started the turn: its text, when it was written, whether the harness or this
    plugin wrote it rather than a person (an injected message, a re-injected block reason), and whether
    it is the record of the user pressing Esc (only a harness that writes one says so)."""

    text: str
    epoch: float | None
    injected: bool = False
    interrupted: bool = False


def iso_epoch(stamp: Any) -> float | None:
    """``stamp`` (an ISO-8601 string, ``Z`` or an offset) as epoch seconds, or None when it is not one."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _from_reader(harness: str, path: str, name: str, fallback: Any, use: Callable[[Callable[..., Any]], Any]) -> Any:
    """``use(<the harness's reader>.<name>)``, or ``fallback`` when there is no reader for ``harness``, no ``path``, the
    reader has no ``name``, or reading raises. Every question below is asked through here, so each one answers "no
    evidence" the same way."""
    module = READERS.get(harness)
    if module is None or not path:
        return fallback
    try:
        function = getattr(importlib.import_module("_harness." + module), name, None)
        return fallback if function is None else use(function)
    except Exception:  # noqa: BLE001 - a reader that raises has not read the file
        return fallback


def turn_prompt(harness: str, path: str, session_id: str = "") -> Prompt | None:
    """The prompt that started the turn recorded at the end of ``path``, read as ``harness``'s format; None when unreadable."""
    return _from_reader(harness, path, "turn_prompt", None, lambda read: read(path, session_id))


def prompts_on_thread(harness: str, path: str, session_id: str = "") -> list[Prompt]:
    """The prompts on the conversation's thread, newest first, read as ``harness``'s format.

    A reader that walks the thread (Claude Code's) lists every prompt on it. A reader that only names the
    prompt that started the turn (Copilot's, Codex's) yields that one, so a caller sees the turn's own
    prompt on every harness and an Esc only where the transcript records one. A walk that raises falls back
    to the turn's own prompt (:func:`turn_prompt`); when that is unreadable too the result is the empty
    list, which a caller reads as "no evidence".
    """
    walked = _from_reader(harness, path, "prompts_on_thread", None, lambda walk: list(walk(path, session_id)))
    if walked is not None:
        return walked
    started = turn_prompt(harness, path, session_id)
    return [] if started is None else [started]


def gate_reply_since(harness: str, path: str, epoch: float) -> bool:
    """Whether the transcript at ``path`` records a person's answer to a gate after ``epoch``.

    Only a harness whose ask tool is answered inside the turn writes one (Codex's async ask, whose
    answer is a user-role row); every other harness answers through the tool's own result, so for them
    this is False, and so it is for anything unreadable.
    """
    return bool(_from_reader(harness, path, "gate_reply_since", False, lambda read: read(path, epoch)))


#: The usage-limit note kinds that LIFT the limit. Every other kind a reader reports means the limit was reached.
USAGE_LIMIT_LIFTED = frozenset({"release"})
#: The kind a reader reports for a turn the limit REFUSED (an API error, no grace). That turn ends through the harness's
#: failure event, which runs no Stop hook, so a turn end that reads it as the newest event got past it: it never holds.
USAGE_LIMIT_STOPPED = "stopped"


def _newest_and_reached(events: list[tuple[str, float | None]]) -> tuple[tuple[str, float | None] | None, float | None]:
    newest = events[0] if events else None
    reached = next((at for kind, at in events if kind not in USAGE_LIMIT_LIFTED), None)
    return newest, reached


def usage_limit_state(harness: str, path: str) -> tuple[tuple[str, float | None] | None, float | None]:
    """``(newest event, reached)`` from ONE read of the transcript: the newest usage-limit event (``(kind, epoch)``),
    and when the limit last STOPPED the session — the newest event that is not a lift (a ``wrap_up`` note, or a turn
    the limit refused).

    Only a harness that records the limit in its transcript has a reader for it (``usage_limit_notes``; Claude Code: a
    ``wrap_up`` note when the limit is reached, a ``release`` when it lifts, or the refused turn's API-error row). Every
    other harness, and anything unreadable, is ``(None, None)``: no evidence, never a limit."""
    return _from_reader(harness, path, "usage_limit_notes", (None, None), lambda read: _newest_and_reached(list(read(path))))


def limit_in_force(note: tuple[str, float | None] | None, since: float) -> bool:
    """Does ``note`` (the newest event of :func:`usage_limit_state`) say the usage limit holds, reached after ``since`` (epoch)?

    A note from before ``since`` belongs to an earlier run, and one with no timestamp cannot be placed: neither holds.
    A refused turn (:data:`USAGE_LIMIT_STOPPED`) never holds at a turn end: the turn ending now got past it."""
    if note is None or note[0] in USAGE_LIMIT_LIFTED or note[0] == USAGE_LIMIT_STOPPED:
        return False
    return note[1] is not None and note[1] > since


def usage_limit_since(harness: str, path: str, since: float) -> bool:
    """Is the account's usage limit holding the session, reached after ``since``? False on anything unreadable."""
    return limit_in_force(usage_limit_state(harness, path)[0], since)


def prompts_until(harness: str, path: str, epoch: float, session_id: str = "") -> tuple[list[Prompt], bool]:
    """The prompts on the conversation's thread written after ``epoch``, newest first, and whether the reader walked
    the thread all the way back to ``epoch`` (True) rather than losing it first (a bounded tail, a compaction).

    Only a reader that walks the thread has it (Claude Code's); every other harness, and anything unreadable, is
    ``([], False)``: no evidence."""
    return _from_reader(harness, path, "prompts_until", ([], False), lambda walk: walk(path, epoch, session_id))


def unanswered_ask_epoch(harness: str, path: str) -> float | None:
    """When the turn recorded at the end of ``path`` had an ask accepted whose answer has not come back, else None.

    Only a harness whose ask returns before the answer exists records this in its transcript (Codex's
    async ask: an accepted receipt now, the answer as a later user-role row). For every other harness the
    tool's own result carries the answer, so this is None, and so it is for anything unreadable.
    """
    return _from_reader(harness, path, "unanswered_ask_epoch", None, lambda read: read(path))
