"""Answer readers: did the user answer the ask tool's question, judged from the tool's result?

One reader per harness, because each harness words an answer, a cancellation and an error in its own
shape. Every reader is an allowlist on the answered shape. The two mistakes cost different amounts: a
false "unanswered" costs one re-ask and corrects itself at the next Stop, while a false "answered"
retires the in-flight marker and lets the model fabricate the user's reply. A result a reader does not
recognise is therefore unanswered.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to (``hooks/_hookfail.py``
explains the floor).
"""

from __future__ import annotations

from typing import Any

#: How deep to look for Claude Code's ``answers`` key. The result is a small envelope, not a document.
_MAX_DEPTH = 4

#: How a successful Copilot ``ask_user`` result starts. A cancellation ("User cancelled the request.")
#: and a decline ("The user is not available to respond ...") are also ``success`` results, so the
#: prefix is what tells an answer from them.
_ANSWER_PREFIX = "User responded:"


def _carries_answers(node: object, depth: int = 0) -> bool:
    """Whether ``node`` contains a non-empty ``answers`` mapping."""
    if depth > _MAX_DEPTH:
        return False
    if isinstance(node, dict):
        if node.get("answers"):
            return True
        return any(_carries_answers(v, depth + 1) for v in node.values())
    if isinstance(node, list):
        return any(_carries_answers(v, depth + 1) for v in node)
    return False


def claude(result: Any) -> bool:
    """Claude Code: the result is structured and carries a non-empty ``answers`` mapping.

    The harness phrases a rejection, an interruption and a tool error as prose, and a result flagged
    ``is_error`` or ``error`` is not an answer whatever it carries.
    """
    if isinstance(result, dict) and (result.get("is_error") or result.get("error")):
        return False
    return _carries_answers(result)


def copilot(result: Any) -> bool:
    """Copilot CLI: ``result_type`` is ``success`` and the text starts with ``User responded:``."""
    if not isinstance(result, dict) or result.get("result_type") != "success":
        return False
    text = result.get("text_result_for_llm")
    return isinstance(text, str) and text.startswith(_ANSWER_PREFIX)
