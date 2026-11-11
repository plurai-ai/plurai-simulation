#!/usr/bin/env python3
"""Refuse zsh parameter-expansion flags in a Bash call during a Plurai scope (PreToolUse: Bash).

Claude Code's own Bash safety parser cannot analyse ``${(P)v}`` and the other ``${(…)name}`` flags. It
cannot rule out a dangerous command it did not parse, so it stops and asks the user to approve the call,
and a session with nobody to answer auto-denies it. The construct is never valid bash, so it is never a
portable command either. This hook denies it before the parser sees it, with the portable rewrite, so the
model re-sends a command that runs without a question.

The rule is a plain substring check: any command whose text contains ``${(`` is refused, wherever it sits
(quoted, in a heredoc body, in a comment). The model re-sends it in portable form. The registry's ``if`` rule
``Bash(*${(*)`` starts the hook only for such a command, and Claude Code runs it for any command it cannot
parse whatever the rule says, so an ordinary command starts no Python for it.

Fail-open (the registry's posture): ``run.py`` turns a crash into no opinion and logs it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

#: The construct: ``${(`` and its flags, named in the reason text.
_FLAG = re.compile(r"\$\{\([^)\s}]*\)?")

_DENIED = (
    "plurai: `{flag}` is zsh-only `${{(…)name}}` syntax, which the harness's command safety check cannot analyse, so it would "
    "stop to ask the user. Use POSIX sh / bash syntax only and re-send: for an indirect environment-variable check, "
    '`[ -n "$(printenv "$v")" ] && echo "$v=set" || echo "$v=unset"`; for other flags, a plain '
    "`printf '%s\\n' \"$x\" | while IFS= read -r line; do …; done` loop, or python3. If `${{(` is part of a script or "
    "data (a JavaScript template literal, say), write that text to a file with the file-writing tool and run the file."
)


def zsh_flag(command: str) -> str | None:
    """The first ``${(…)`` in ``command``, or None."""
    found = _FLAG.search(command)
    return found.group() if found else None


def handle(event: Event) -> Decision:
    """Deny a shell call whose text contains ``${(``. Else nothing."""
    from _harness import model

    if event.tool != "shell":
        return model.none()
    flag = zsh_flag(str(event.tool_input.get("command") or ""))
    return model.deny(_DENIED.format(flag=flag)) if flag else model.none()
