"""Codex CLI's session transcript: ``rollout-*.jsonl``.

Each row is ``{timestamp, ordinal, type, payload}``. A turn opens with an ``event_msg`` row whose
payload type is ``task_started``. The messages of the conversation are ``response_item`` rows whose
payload is a ``message`` with a ``role``; a row's ``content_item_kinds`` says what it is: ``user.text``
for what a person sent, something else for the context the harness writes at the top of a turn.

The async ask tool does not end the turn when the user answers. The answer comes back inside the same
turn as one more ``user.text`` row wrapped in ``<send_user_message_question_reply>``. That row is a
person's answer to a gate: it is not the prompt that started the turn, and ``gate_reply_since`` finds it.
A model call Codex rejected for bad arguments is a ``function_call`` and its error output; neither is a
message, so neither is read by the prompt and reply readers. ``unanswered_ask_epoch`` does read them: an ask
call is accepted when its ``function_call_output`` says so.
"""

from __future__ import annotations

import json
from typing import Any

import registry
from _transcript import object_rows

from _harness.transcript import Prompt, iso_epoch

#: Rollout rows are large (every token-usage record is a row), so the tail read is larger than Claude
#: Code's. A turn that began before it reads as unreadable, which leaves the turn-end rules on.
TAIL_BYTES = 8 * 1024 * 1024

#: The envelope Codex puts around the user's answer to an async ask.
REPLY_TAG = "<send_user_message_question_reply>"


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _user_text(row: dict[str, Any]) -> str | None:
    """The text of a ``user.text`` message row, or None for any other row.

    A user row whose kinds exclude ``user.text`` is the harness's context. A row with no kinds at all
    is read as text a person sent, so a Codex release that drops the field does not blind the reader.
    """
    payload = _payload(row)
    if row.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    meta = payload.get("internal_chat_message_metadata_passthrough")
    kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
    if isinstance(kinds, list) and "user.text" not in kinds:
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))


def _is_reply(text: str) -> bool:
    return text.lstrip().startswith(REPLY_TAG)


def turn_prompt(path: str, session_id: str = "") -> Prompt | None:
    """The prompt that started the turn ending at the newest row of ``path``.

    Read back to the newest ``task_started`` row: the first ``user.text`` row after it that is not an
    ask answer. None when the file holds no ``task_started`` row (the turn began before the tail read,
    or this is no Codex rollout) or the turn has no such row.
    """
    prompt: dict[str, Any] | None = None
    for row in object_rows(path, tail_bytes=TAIL_BYTES):
        if row.get("type") == "event_msg" and _payload(row).get("type") == "task_started":
            if prompt is None:
                return None
            return Prompt(str(_user_text(prompt)), iso_epoch(prompt.get("timestamp")), False)
        text = _user_text(row)
        if text is not None and not _is_reply(text):
            prompt = row  # newest first: the last one assigned is the oldest, the one the turn opened with
    return None


def gate_reply_since(path: str, epoch: float) -> bool:
    """Whether an ask answer was written to ``path`` after ``epoch`` (seconds since the epoch)."""
    for row in object_rows(path, tail_bytes=TAIL_BYTES):
        text = _user_text(row)
        if text is None or not _is_reply(text):
            continue
        stamp = iso_epoch(row.get("timestamp"))
        if stamp is not None and stamp > epoch:
            return True
    return False


def _accepted(output: Any) -> bool:
    """Whether a tool output is the async ask's receipt, ``{"accepted":true}``. A rejection is an error string."""
    if not isinstance(output, str):
        return False
    try:
        receipt = json.loads(output)
    except ValueError:
        return False
    return isinstance(receipt, dict) and receipt.get("accepted") is True


def unanswered_ask_epoch(path: str) -> float | None:
    """When the newest accepted ask of the turn ending at the newest row of ``path`` was accepted, if no answer has come back.

    Walks the turn oldest to newest: an accepted ask call opens a wait, stamped with its receipt row, and an
    answer row closes it. A call Codex rejected for bad arguments has an error output and opens nothing. The stamp
    is what lets a caller tell the gate this ask was made for from one sanctioned after it. None when nothing
    awaits a reply, when the receipt carries no readable time, and when the file holds no ``task_started`` row
    (nothing says which rows are this turn's).
    """
    turn: list[dict[str, Any]] = []
    for row in object_rows(path, tail_bytes=TAIL_BYTES):
        if row.get("type") == "event_msg" and _payload(row).get("type") == "task_started":
            break
        turn.append(row)
    else:
        return None
    ask_calls: set[str] = set()
    asked_at: float | None = None
    for row in reversed(turn):
        if row.get("type") != "response_item":
            continue
        payload = _payload(row)
        kind = payload.get("type")
        call_id = str(payload.get("call_id"))
        output = payload.get("output")
        if kind == "function_call" and registry.canonical_tool("codex", str(payload.get("name") or "")) == "ask":
            ask_calls.add(call_id)
        elif kind == "function_call_output" and call_id in ask_calls and _accepted(output):
            asked_at = iso_epoch(row.get("timestamp"))
        else:
            text = _user_text(row)
            if text is not None and _is_reply(text):
                asked_at = None
    return asked_at
