"""Claude Code codec: its stdin payloads in, its stdout shapes out. See ``codec`` for both."""

from __future__ import annotations

from _harness import codec
from _harness.model import Decision, Event


def decode(raw: bytes) -> Event:
    return codec.decode("claude", raw)


def encode(decision: Decision, event: Event) -> str:
    return codec.encode("claude", decision, event)
