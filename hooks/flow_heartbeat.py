#!/usr/bin/env python3
"""The dropped-turn heartbeat's gate (UserPromptSubmit hook).

A flow whose turn died on a transient API error has nothing scheduled to re-drive it — see
``hooks/flow_interrupt.py`` for why the Stop-hook pending contract cannot cover that path. The
recovery is a session cron the flow schedules at Step 0, firing one fixed sentinel prompt. THIS
hook decides whether a firing reaches the model, and the answer is almost always no.

WHY GATE IT AT ALL — the cost of the obvious version. A cron that simply woke the model on a timer
would pay a full model turn per firing on the flow's entire context, and a suite spends most of
its wall clock legitimately idle, waiting on backgrounded polls and critics. On a 172-minute suite
build with 11 idle gaps totalling 105 minutes and a context growing from 0.32M to 0.62M tokens, a
timer that REACHED THE MODEL every 5 minutes costs 12-15 turns, ~8 minutes of added wall clock and
~40K output tokens, to learn nothing. That cost is why the backgrounded-sleep polling recipe is not
used (docs/polling-cadence.md). Blocking here costs a few milliseconds of Python and no API call
at all — a blocked ``UserPromptSubmit`` prompt is erased before the model is reached — so the
heartbeat is free on every firing except the one that actually rescues a flow.

THE PREDICATE IS THE DROP, NOT THE IDLENESS. The gate opens only on ``.flow_dropped``. Deliberately
NOT "is anything in flight?": the modal drop is the turn the harness starts when a background task
finishes, where that task's launch register is still standing — so a pending-work test would block
the rescue for as long as the register lives, which is the exact failure this exists to end. Keying
on the drop also keeps every ordinary idle state silent, with no timer to tune: a clean in-progress
wait, a question waiting on the user, a landed hand-off and a deliberate interrupt all leave no
marker, so all of them block.

ONE-SHOT. The marker is consumed on the firing it releases. A rescue turn that drops again is
re-armed by ``flow_interrupt``; one that succeeds leaves nothing behind.

Fail-CLOSED once the prompt is known to be ours, fail-OPEN before that: a broken gate must never
reach the model with a heartbeat (that is the latency regression), and must never erase a prompt
that might be the user's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
DROPPED = ".flow_dropped"

# The text scripts/flow-window.sh tells the flow to schedule. The discriminator is this TEXT, not
# the payload's prompt source: a ``source`` field is documented for SessionStart but not for
# UserPromptSubmit, and this is a string no human types.
#
# Matched as a PREFIX of the stripped prompt. Not an exact match: a firing that arrives with
# surrounding whitespace must still gate, and falling through would reach the model unfiltered on
# a 0.5M-token context — the regression this file exists to prevent. Not a substring match either:
# the arm script prints this string verbatim, so a user pasting that line back, or asking about it,
# would have their own prompt erased. Both directions are pinned by tests.
SENTINEL = "plurai-flow heartbeat"

# Deliberately terse: a blocked firing's reason is surfaced once per cron period for as long as the
# session lives, so it has to read as a quiet status line rather than a problem.
_BLOCKED = "plurai-flow: heartbeat idle (no dropped turn to resume)."

# The flow-window command names, mirroring scripts/flow-window.sh's `_known_command`. The resume
# text tells the model to run `flow-window.sh close <command>`, which rejects anything else.
KNOWN_COMMANDS = frozenset({"generate-suite", "run", "analyze", "scan-agent", "fixes", "traces", "simulate"})


def _resume(command: str) -> str:
    """What the ONE firing that gets through tells the model.

    ``command`` is the flow-window MARKER name (generate-suite | run | analyze | scan-agent |
    fixes | traces | simulate), which is what ``flow-window.sh close`` takes — it is NOT a slash command, so
    it is never rendered as one.

    It must not assume the flow is unfinished. The marker proves a turn died, not that the work
    is outstanding — the hand-off may have rendered in the very turn that dropped — so the first
    instruction is to check, and the exit is to close the window rather than to re-render anything.
    """
    return (
        f"The {command} flow's previous turn ended on a transient API error before it finished, so nothing "
        "re-drove it. FIRST decide whether it actually needs resuming: if its hand-off already rendered, or "
        "it is waiting on the answer to a question you asked the user, then run your skill's Step-0 "
        f"flow-window line with `close {command}` instead of `arm {command}`, and stop without saying "
        "anything. Otherwise resume from the flow's CURRENT on-disk state — re-read the ids and artifacts "
        "under $WORKDIR to establish where it got to, continue the step that was in progress, and do not "
        "redo steps that already completed. Never mention this message, the heartbeat, or the interruption "
        "to the user."
    )


def _block() -> Decision:
    """Refuse the firing: a blocked ``UserPromptSubmit`` erases the prompt before any model call."""
    from _harness import model

    return model.block_stop(_BLOCKED)


def handle(event: Event) -> Decision:
    from _harness import model

    # Before the prompt is confirmed to be ours, every failure path decides nothing: this hook runs on
    # EVERY user prompt, and erasing one it cannot even parse is never acceptable.
    prompt = str(event.raw.get("prompt") or "")
    if not prompt.strip().startswith(SENTINEL):
        return model.none()  # a typed prompt (or another cron's) — untouched, no output

    # From here the prompt IS the heartbeat, so failure means BLOCK: reaching the model on a
    # broken gate is the regression this hook exists to prevent. The runner treats this hook as
    # open, so the closed posture lives here, where the prompt is known to be ours.
    try:
        return _gate(event)
    except Exception:  # noqa: BLE001 - fail CLOSED: a broken gate never wakes the model
        return _block()


def _gate(event: Event) -> Decision:
    from _harness import model
    from _workdir import find_marker, find_markers, marker_dirs

    bases = marker_dirs({"session_id": event.session_id})
    marker = find_marker(FLOW_MARKER, bases)
    if marker is None:
        return _block()  # no armed flow → nothing this could resume
    dropped = find_markers(DROPPED, bases)
    if not dropped:
        return _block()  # the flow is running or waiting normally — the common case
    lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
    command = lines[0].strip() if lines else ""
    if command not in KNOWN_COMMANDS:
        # A corrupt or foreign window cannot be resumed OR closed by name: the resume text
        # would hand the model a `flow-window.sh close <name>` that exits 64. Leave the record
        # standing — a later firing under a valid window can still spend it.
        return _block()

    def consume() -> None:
        # Every copy (the union, not the first hit): the window and the drop record can be
        # written to different bases, and a survivor would release a second, redundant firing.
        for path in dropped:
            try:
                path.unlink()
            except OSError:
                pass

    # The record is consumed only once the answer is on the wire (``after_delivery``). The order is
    # the safety property: a failed write then leaves the record standing, which costs one extra
    # firing, bounded and cheap. A record consumed before a failed write strands the flow until a
    # human notices, which is the outcome this hook exists to end.
    return model.context(_resume(command)).after(consume)
