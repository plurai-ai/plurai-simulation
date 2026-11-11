"""Say it out loud when a hook cannot run — fail-open must not mean fail-invisible.

A hook fails OPEN unless ``registry.py`` marks it closed (a closed hook denies when it cannot decide;
no shipped hook is): a crashed guard must never wedge a session. That posture is right, and it has one hole. When a hook could not even LOAD — PEP 604 annotations under a
`python3` that resolves to 3.9, a missing stdlib name, a syntax error mid-edit — the flow-fidelity
contract switched off ENTIRELY (`pending_register` registered nothing, so `report_guard` saw no
pending work, so the dropped-turn rescue and the turn-end guard were both inert) and NOTHING said
so. The operator watched a flow that looked armed and was not.

So: still never block a tool call on an open hook — but print ONE line naming the module and the
interpreter, which is the pair that identifies this failure class immediately (``note``, called by
``_harness/failpost.py`` for a handler that raises and by the hooks that load a helper late).

Deliberately dependency-free (stdlib, no plugin imports): this is the module that runs when other
imports are what broke.
"""

from __future__ import annotations

import sys
from typing import Any


def note(module: str, exc: BaseException | Any) -> None:
    """The one line. Kept to a single line so it survives a crowded hook log."""
    print(
        f"plurai-hook: {module} FAILED to run ({type(exc).__name__}: {exc}) under "
        f"{sys.executable} (Python {sys.version.split()[0]}) — this hook's contract is OFF for "
        "this session. If the interpreter is < 3.10, that is the known PEP 604 class: point "
        "`python3` at a newer one, or report it.",
        file=sys.stderr,
    )
