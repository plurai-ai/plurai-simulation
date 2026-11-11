"""Codex CLI codec: its stdin payloads in, its stdout shapes out. See ``codec`` for both.

Codex sends every event as a snake_case object with ``hook_event_name``. Its parser for hook
output rejects a field it does not know, so ``codec.encode`` writes only keys that Codex 0.160 was seen
to accept. Everything nests in ``hookSpecificOutput`` except a stop block; a top-level permission decision
or context is ignored.

A background command reports nothing here, and that is deliberate. Codex sends ``PostToolUse`` for an
``exec_command`` once, when the command exits (``tool_response`` is the final output, with no running-session
header: that header is in the rollout only), sends no hook for the ``write_stdin`` polls, and does not start a
turn when the command finishes. ``wakes_on_background`` is False for that last reason; the codec leaves
``background_shell`` and ``finished_shell`` unset.
"""

from __future__ import annotations

from _harness import codec
from _harness.model import Decision, Event


def decode(raw: bytes) -> Event:
    return codec.decode("codex", raw)


def encode(decision: Decision, event: Event) -> str:
    return codec.encode("codex", decision, event)
