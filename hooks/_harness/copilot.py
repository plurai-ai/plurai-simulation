"""Copilot CLI codec: its stdin payloads in, its stdout shapes out.

Copilot sends most events as snake_case objects with ``hook_event_name``. ``SubagentStart`` and
``PermissionRequest`` arrive camelCase (``sessionId``, ``toolName``, ``toolInput``); the first names
no event at all and the second names it as ``hookName``. Both decode to the same ``Event`` as their
snake_case siblings.

Copilot returns a Stop block's reason as the prompt of a new turn. ``encode`` remembers each block's
reason and ``decode`` names a prompt equal to the last one ``reinjected`` instead of
``user_prompt_submit``, so the hooks that read what the user typed can ignore it.
"""

from __future__ import annotations

import re
from typing import Any

import registry

from _harness import answers, codec, reinject, wire
from _harness.model import Decision, Event


def _field(payload: dict[str, Any], snake_name: str, camel_name: str) -> Any:
    return payload[snake_name] if snake_name in payload else payload.get(camel_name)


def _kind(payload: dict[str, Any]) -> str:
    named = wire.text(payload.get("hook_event_name")) or wire.text(payload.get("hookName"))
    if named:
        return wire.snake(named)
    # The only payload Copilot sends without naming its event.
    if "agentName" in payload and "transcriptPath" in payload:
        return "subagent_start"
    return ""


def _last_message(kind: str, payload: dict[str, Any], transcript_path: str) -> str | None:
    """The final assistant message of a Stop: Copilot's Stop payload names its transcript and sends no
    message, so it is read from ``events.jsonl``, which Copilot flushes after the hook has started (the
    reader waits briefly for the row). None when the transcript supplies none; another event has none to
    name, and its ``last_message`` stays ``""``."""
    if kind != "stop":
        return ""
    sent = wire.text(payload.get("last_assistant_message"))
    if sent:
        return sent
    try:
        # Imported here: the reader is needed by one event, and a decoder that cannot import it must
        # still decode every other event, the closed hooks' among them.
        from _harness import copilot_transcript  # noqa: PLC0415

        return copilot_transcript.awaited_final_message(transcript_path)
    except Exception:  # noqa: BLE001 - a reader that fails has supplied no message
        return None


#: The trailer Copilot appends to a shell result once the command has finished (``read_bash`` and a ``bash`` call
#: that outlived its first wait both end on it; so does a synchronous call, for the shell it ran in). It is the
#: last line of the result, so a line of the job's own output that spells it is not read.
_FINISHED_SHELL = re.compile(r"(?:\A|\n)<(?:detached command with )?shellId: (?P<id>[^\s>]+) completed with exit code -?\d+>\s*\Z")

#: The whole result of an async ``bash`` call: the harness's own line, which names the shell whether or not the
#: call did.
_STARTED_SHELL = re.compile(r"<command started in background with shellId: (?P<id>[^\s>]+)>")

#: What Copilot injects as a user prompt when a background command exits, ~0.3 s later. It is read only when
#: it is the whole prompt, so a message that quotes or wraps it ends nothing. The description between the quotes
#: is the model's and may spell a frame of its own; the greedy match takes the last ``(shellId: …)`` before the
#: harness's own closing words. A failed command's wording is not recorded; ``failed`` is read as the
#: counterpart of the recorded ``completed successfully``.
_SHELL_NOTIFICATION = re.compile(
    r'<system_notification>\nShell command "[^\n]*" \(shellId: (?P<id>[^\s)]+)\) has (?:completed|failed)[^\n]*\n</system_notification>\s*'
)


def _shell_states(kind: str, tool: str | None, tool_input: dict[str, Any], payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(background_shell, finished_shell)`` of a shell result, or of a notification prompt.

    A command started with ``mode: "async"`` runs in the background under the shell id Copilot's own start line
    names (the call's ``shellId`` when that line names none), until a ``read_bash`` result carries the finished
    trailer for that id or Copilot injects its completion notification. A call with no id anywhere has nothing
    a later result could be matched to, so it is not read as background work. A synchronous call's trailer
    names a shell the session reuses, so only a ``read_bash`` result is read as a finish; an async call that
    finished inside its first wait left nothing running.
    """
    if kind == "user_prompt_submit":
        found = _SHELL_NOTIFICATION.fullmatch(wire.text(payload.get("prompt")))
        return None, found.group("id") if found else None
    if kind != "post_tool_use" or tool not in ("shell", "shell_read"):
        return None, None
    result = payload.get("tool_result")
    text = wire.text(result.get("text_result_for_llm")) if isinstance(result, dict) else ""
    finished = _FINISHED_SHELL.search(text)
    if tool == "shell_read":
        return None, finished.group("id") if finished else None
    if tool_input.get("mode") == "async" and finished is None:
        started = _STARTED_SHELL.fullmatch(text.strip())
        named = started.group("id") if started else str(tool_input.get("shellId") or tool_input.get("shell_id") or "")
        return named or None, None
    return None, None


def decode(raw: bytes) -> Event:
    payload = wire.json_object(raw)
    tool_input = _field(payload, "tool_input", "toolInput")
    session_id = wire.text(_field(payload, "session_id", "sessionId"))
    kind = _kind(payload)
    if kind == "user_prompt_submit" and reinject.is_remembered(session_id, wire.text(payload.get("prompt"))):
        kind = "reinjected"
    transcript_path = wire.text(_field(payload, "transcript_path", "transcriptPath"))
    tool = registry.canonical_tool("copilot", wire.text(_field(payload, "tool_name", "toolName")))
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    background, finished = _shell_states(kind, tool, tool_input, payload)
    return Event(
        kind=kind,
        tool=tool,
        tool_input=tool_input,
        session_id=session_id,
        cwd=wire.text(payload.get("cwd")),
        raw=payload,
        harness="copilot",
        transcript_path=transcript_path,
        background_shell=background,
        finished_shell=finished,
        answered_source=lambda: answers.copilot(payload.get("tool_result")),
        last_message_source=lambda: _last_message(kind, payload, transcript_path),
        **registry.CAPABILITIES["copilot"],
    )


def encode(decision: Decision, event: Event) -> str:
    """Copilot reads context only at the top level, and applies a rewrite while keeping the approval dialog."""
    return codec.encode("copilot", decision, event)
