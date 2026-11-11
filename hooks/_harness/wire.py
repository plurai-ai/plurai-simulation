"""Helpers every codec shares: reading a JSON object leniently and moving between event-name spellings."""

from __future__ import annotations

import json
import re
from typing import Any


def json_object(raw: bytes) -> dict[str, Any]:
    """The JSON object in ``raw``; an empty one for anything else.

    A hook whose verdict does not depend on the payload must not change it because stdin is garbage,
    and a hook that needs a field treats its absence as "nothing to act on".
    """
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def snake(name: str) -> str:
    """``PreToolUse`` and ``permissionRequest`` to ``pre_tool_use`` and ``permission_request``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def pascal(kind: str) -> str:
    """``pre_tool_use`` to ``PreToolUse``."""
    return "".join(word.capitalize() for word in kind.split("_"))
