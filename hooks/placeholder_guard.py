#!/usr/bin/env python3
"""Refuse, once, an edit-session message that still carries an unfilled placeholder (PreToolUse: the backend's
``edit_session_start``, ``interact_edit_session`` and ``interact_with_scenario_request``).

The rule and its record are ``_placeholder.py``'s, shared with ``scripts/mcp_call.py`` and the stdio bridge. The
identical call sent again goes through, so a brace the backend must see literally costs one round trip.

Fail-open (the registry's posture): ``run.py`` turns a crash into no opinion and logs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

PREFIX = "backend:"


def handle(event: Event) -> Decision:
    """Deny the first call of an edit-session tool whose arguments carry ``{NAME}``. Else nothing."""
    from _harness import model

    import _placeholder  # noqa: PLC0415 - a sibling module

    tool = event.tool or ""
    verb = tool[len(PREFIX) :] if tool.startswith(PREFIX) else ""
    if _placeholder.slot(verb, event.tool_input) is None:
        return model.none()
    where = _placeholder.state_dir(Path(event.cwd) if event.cwd else None)
    said = _placeholder.refusal(verb, dict(event.tool_input), where)
    return model.deny(said) if said else model.none()
