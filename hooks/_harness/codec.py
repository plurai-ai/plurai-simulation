"""The decoder and encoder shared by every codec, and the table of how the harnesses differ.

Claude Code and Codex send the same payload shape (snake_case, one ``hook_event_name``), and all three
harnesses answer in the same few JSON objects. ``SHAPES`` holds what differs; ``claude.py`` and
``codex.py`` bind ``decode`` and ``encode`` to their row, and ``copilot.py`` reads the shared ``encode``
and decodes its own payloads.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to (``hooks/_hookfail.py``
explains the floor).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import registry
from _harness import answers, reinject, wire
from _harness.model import Decision, Event


@dataclass(frozen=True)
class Shape:
    """One harness's decoding and encoding differences."""

    #: Judges the tool result's ``tool_response``. None: the harness's ask results are never read as answers
    #: (Codex's async tool returns only that the question was shown), so ``answered`` stays False.
    answer_reader: Callable[[Any], bool] | None = None
    #: Whether the payload's ``stop_hook_active`` is read, on a harness that sends it.
    reads_stop_hook_active: bool = False
    #: The native name of the ask tool that waits for an answer. Codex's registry matches both of its ask
    #: tools; this one is told apart by the name.
    blocking_ask_tool: str | None = None
    #: Whether ``additionalContext`` nests in ``hookSpecificOutput`` (Claude Code, Codex) or sits at the
    #: top level, the only place Copilot reads it.
    nests_context: bool = True
    #: Whether a ``rewrite`` is sent as ``updatedInput``. Codex applies it only together with ``allow``,
    #: and that ``allow`` skips Codex's own approval prompt, so a rewrite there is dropped: sending it
    #: would grant an approval the user withheld.
    applies_rewrite: bool = True
    #: Whether a ``notice`` travels as context. Codex's parser rejects a field it does not know and
    #: ``systemMessage`` is not among the keys it was seen to accept, so a notice that rides on no other
    #: output is sent as context, and one that rides on context is appended to its text. A notice beside
    #: a deny, a stop block or a rewrite is dropped: the reason or the rewrite is what the model needs.
    notice_is_context: bool = False
    #: Whether a correction at a Stop (``Decision.feedback``) is sent as ``additionalContext`` under ``Stop``. Claude Code
    #: shows that as feedback, not as an error, and the model acts on it. Copilot and Codex have no measured channel
    #: of that kind at Stop, so their corrections stay blocks.
    stop_feedback_as_context: bool = False
    #: Called with ``(session_id, reason)`` when a stop block is encoded. Copilot returns a block's reason
    #: as the prompt of a new turn, so its decoder must recognise that prompt later.
    on_block_stop: Callable[[str, str], None] | None = None


SHAPES: dict[str, Shape] = {
    # A lambda, so the reader is looked up in ``answers`` when an event is read, not when this table loads.
    "claude": Shape(answer_reader=lambda result: answers.claude(result), stop_feedback_as_context=True),
    "copilot": Shape(nests_context=False, on_block_stop=reinject.remember),
    "codex": Shape(
        reads_stop_hook_active=True,
        blocking_ask_tool="request_user_input",
        applies_rewrite=False,
        notice_is_context=True,
    ),
}


def _shape(harness: str) -> Shape:
    """``harness``'s row of ``SHAPES``; a harness missing from it or from ``registry.CAPABILITIES`` is a setup error."""
    if harness not in SHAPES or harness not in registry.CAPABILITIES:
        raise ValueError(f"no codec for harness {harness!r}: add a row for it to codec.SHAPES and to registry.CAPABILITIES")
    return SHAPES[harness]


def decode(harness: str, raw: bytes) -> Event:
    """A payload that is not a JSON object decodes to an empty event."""
    shape = _shape(harness)
    payload = wire.json_object(raw)
    tool_input = payload.get("tool_input")
    native_tool = wire.text(payload.get("tool_name"))
    reader = shape.answer_reader
    return Event(
        kind=wire.snake(wire.text(payload.get("hook_event_name"))),
        tool=registry.canonical_tool(harness, native_tool),
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        session_id=wire.text(payload.get("session_id")),
        cwd=wire.text(payload.get("cwd")),
        raw=payload,
        harness=harness,
        transcript_path=wire.text(payload.get("transcript_path")),
        stop_hook_active=shape.reads_stop_hook_active and payload.get("stop_hook_active") is True,
        blocking_ask=native_tool == shape.blocking_ask_tool,
        answered_source=(lambda: reader(payload.get("tool_response"))) if reader else False,
        last_message_source=lambda: wire.text(payload.get("last_assistant_message")),
        **registry.CAPABILITIES[harness],
    )


def _context(shape: Shape, event: Event, text: str) -> dict[str, Any]:
    if not shape.nests_context:
        return {"additionalContext": text}
    if not event.kind:
        raise ValueError("context answers an event, and the payload named none")
    return {"hookSpecificOutput": {"hookEventName": wire.pascal(event.kind), "additionalContext": text}}


def encode(harness: str, decision: Decision, event: Event) -> str:
    """The stdout for ``decision``: one JSON object, or the empty string for "no opinion".

    A permission decision and a rewrite sit in ``hookSpecificOutput``, a stop block at the top level. A
    rewrite carries no ``permissionDecision``: the harness applies it and leaves its permission flow alone.
    """
    shape = _shape(harness)
    out: dict[str, Any] = {}
    if decision.kind == "deny":
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.text,
        }
    elif decision.kind == "rewrite":
        if shape.applies_rewrite:
            out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "updatedInput": decision.tool_input}
    elif decision.kind == "block_stop" and decision.feedback and shape.stop_feedback_as_context:
        out["hookSpecificOutput"] = {"hookEventName": "Stop", "additionalContext": decision.text}
    elif decision.kind == "block_stop":
        out["decision"] = "block"
        out["reason"] = decision.text
        if shape.on_block_stop:
            shape.on_block_stop(event.session_id, decision.text)
    elif decision.kind == "context":
        notice = decision.notice if shape.notice_is_context else ""
        out.update(_context(shape, event, f"{decision.text}\n\n{notice}" if notice else decision.text))
    elif decision.kind == "none" and shape.notice_is_context and decision.notice and event.kind:
        out.update(_context(shape, event, decision.notice))
    if decision.notice and not shape.notice_is_context:
        out["systemMessage"] = decision.notice
    return json.dumps(out) if out else ""
