#!/usr/bin/env python3
"""Keep the dropped-turn heartbeat off the wire under auto mode (PreToolUse: Bash|CronCreate).

Under auto mode the permission classifier denies session-cron creation, so the heartbeat's
`CronCreate` — which every flow's arm instructs once per session — produced a red, unexplained
classifier denial at the start of every flow, and since the schedule sentinel is only written on
an OBSERVED CronCreate, the next arm instructed it again.

TWO BRANCHES, and the FIRST is the one that matters:

* **Bash (stamp, never deny).** The fix is not to deny the call more nicely — it is for the call
  never to be made. `flow-window.sh arm` decides whether to print the CronCreate instruction by
  reading ``.flow_heartbeat_unavailable``, and a shell script cannot see the permission mode.
  A hook can: ``permission_mode`` is carried on tool events, and **PreToolUse runs before the
  command it announces**, so when the arm's own Bash call is announced this stamps the sentinel
  and the script — running moments later, in that same tool call — reads it and prints no heartbeat
  line at all. Nothing is instructed, nothing is called, nothing is denied, and the user sees no
  error at all. This branch NEVER denies: a stamp is all it does.
* **CronCreate (deny).** The backstop, for a cron that gets attempted anyway — a model
  improvising it, a stamp that could not be written, a flow armed before this hook existed. It
  denies with a reason that says the skip is expected, which renders as a tool error; that is
  strictly better than the classifier's unexplained block, and strictly worse than the Bash
  branch's silence, which is why the stamp exists.

Every other mode, and every cron that is not the plugin's own sentinel, passes untouched.

The honest cost: auto-mode sessions run without dropped-turn rescue (they already did — the
classifier was denying the cron; now it is deliberate, quiet, and once). To opt back in, export
``PLURAI_FLOW_HEARTBEAT=force`` and add ``CronCreate`` to ``permissions.allow`` — a narrow
non-shell allow rule resolves before the classifier, so the pair restores the heartbeat.

Fail-open: a crashed gate must never block a cron in the modes where it is allowed. If it fails
open in auto mode the classifier denies the call anyway, and ``denial_watch`` stamps the same
sentinel from the ``PermissionDenied`` event — the net under this wire.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
HEARTBEAT_UNAVAILABLE = ".flow_heartbeat_unavailable"

#: The arm call whose printed output the stamp has to beat. Matched loosely (the skills spell it
#: with $CLAUDE_PLUGIN_ROOT, an absolute path, and sometimes behind a `cd …;`) because a miss
#: only costs the fallback — the CronCreate branch below still catches the call.
_ARM_CALL = re.compile(r"flow-window\.sh[\"']?\s+arm\b")

_DENIED = (
    "plurai-flow: session crons are not available under the auto permission mode, so the "
    "dropped-turn heartbeat is skipped — this is EXPECTED. Continue the flow without it and "
    "do not retry this call; later flow-window arms in this session will not re-ask. "
    "(To restore the heartbeat under auto mode: export PLURAI_FLOW_HEARTBEAT=force and add "
    "the heartbeat tool to permissions.allow.)"
)


def _stamp_unavailable(session_id: str) -> None:
    """Record, beside the window, that this session cannot schedule the heartbeat."""
    from _workdir import find_marker, flow_dir, marker_dirs

    payload = {"session_id": session_id}
    marker = find_marker(FLOW_MARKER, marker_dirs(payload))
    base = marker.parent if marker is not None else flow_dir(payload)
    if base is None:
        return
    try:
        base.mkdir(parents=True, exist_ok=True)
        (base / HEARTBEAT_UNAVAILABLE).write_text("auto-mode", encoding="utf-8")
    except OSError:
        pass  # degrades to one instruction per arm, never to a failed deny


def handle(event: Event) -> Decision:
    """Stamp the arm's Bash call, or deny the heartbeat's CronCreate, under auto mode. Else nothing."""
    from _harness import model

    try:
        return _decide(event)
    except Exception:  # noqa: BLE001 - fail OPEN: never block a cron the mode allows
        return model.none()


def _decide(event: Event) -> Decision:
    from _harness import model
    from flow_heartbeat import SENTINEL

    if event.tool not in ("shell", "cron"):
        return model.none()

    # Only auto mode changes anything, in either branch. "auto" is the observed payload value
    # (observed on recorded tool events: every permission_mode was
    # 'auto'); it is also the documented one. If a harness ever reports something else the
    # whole hook goes quiet and the chain degrades to the pre-gate behaviour — the classifier
    # denies the cron and denial_watch stamps the sentinel from PermissionDenied.
    if str(event.raw.get("permission_mode") or "") != "auto":
        return model.none()
    if os.environ.get("PLURAI_FLOW_HEARTBEAT", "").strip() == "force":
        return model.none()  # the operator opted the heartbeat back in — let the permission system rule

    if event.tool == "shell":
        # STAMP ONLY — this branch can never deny. PreToolUse runs before the announced
        # command, so stamping here means the arm script, which runs moments later inside
        # this same tool call, reads the sentinel and never prints the CronCreate
        # instruction. The call is not made, so nothing is denied and nothing renders.
        if _ARM_CALL.search(str(event.tool_input.get("command") or "")):
            _stamp_unavailable(event.session_id)
        return model.none()

    if not str(event.tool_input.get("prompt") or "").strip().startswith(SENTINEL):
        return model.none()  # someone else's cron — none of this hook's business, in any mode
    _stamp_unavailable(event.session_id)
    return model.deny(_DENIED)
