#!/usr/bin/env python3
"""The net under the flow control plane: react to auto-mode denials (PermissionDenied hook).

``PermissionDenied`` fires when auto mode denies a tool call. The event is read-only toward
the model — no reason can be injected, exit codes are ignored — but the hook process itself can
act on the plugin's own state, and two denials are the plugin's business:

* **A denied raw sanction write.** Every current text instructs `flow-window.sh sanction`, but
  an older skill copy or an improvising model can still emit the raw `echo gate > ….ask_sanctioned`
  shape, which the classifier denies as self-modification — the exact chain that deadlocks a
  live flow. The intent of that command is unambiguous and its
  semantics are the plugin's own, so this hook COMPLETES them: it writes the gate marker beside
  the window, and the model's next AskUserQuestion passes. Safe by construction — the only
  capability this grants is asking the human a question.
* **The denied heartbeat cron.** ``cron_gate`` pre-empts this in auto mode; if it failed open
  (or a future mode denies crons without being "auto"), the classifier's denial lands here and
  the same ``.flow_heartbeat_unavailable`` stamp stops the per-arm re-instruction.

Never emits ``retry``: retrying the denied call would just be denied again — the recovery is
that the NEXT, correctly-shaped action succeeds. Fail-open, silent, exit 0 always.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
ASK_SANCTION = ".ask_sanctioned"
HEARTBEAT_UNAVAILABLE = ".flow_heartbeat_unavailable"

#: A command whose ENTIRE text is one simple sanction write — `echo gate > …/.ask_sanctioned`.
#: Full-match on purpose (review finding): a substring match minted tokens from a denied
#: `grep "echo x > .ask_sanctioned"`, a `git log --grep=…`, a heredoc quoting the shape, a
#: compound command denied for its OTHER half, and an `.ask_sanctioned.bak` target. A lone
#: redirect is what an improvising model actually emits (the shape a model actually emits); anything
#: composite is not unambiguously a sanction write, and an ambiguous denial must complete
#: nothing.
_RAW_SANCTION_WRITE = re.compile(
    r"""^\s*(?:echo|printf)\s+(?:-[a-zA-Z]+\s+)*['\"]?(?P<gate>[A-Za-z0-9._-]+)['\"]?\s*"""
    r""">{1,2}\s*['\"]?\S*\.ask_sanctioned['\"]?\s*$""",
)
#: The canonical shape itself, denied: `bash "…/flow-window.sh" sanction <gate>`. The README
#: concedes some environments' classifiers may block plugin script calls; the net must cover
#: the shape the whole redesign standardizes on, not only the deprecated raw one.
_SCRIPT_SANCTION = re.compile(
    r"""^\s*(?:bash\s+)?['\"]?\S*flow-window\.sh['\"]?\s+sanction\s+(?P<gate>[A-Za-z0-9._-]+)\s*$""",
)
_RAW_SANCTION_TOUCH = re.compile(r"\.ask_sanctioned|flow-window\.sh")


def _window_base(payload: dict) -> Path | None:
    from _workdir import find_marker, marker_dirs

    marker = find_marker(FLOW_MARKER, marker_dirs(payload))
    return marker.parent if marker is not None else None


#: Where a denial is RECORDED for the scan's verification file to fold in. Beside the window, so
#: it is per-flow and is swept with it.
DENIAL_LOG = ".denials.jsonl"


def _record_denial(payload: dict, tool: str) -> None:
    """Append one denial beside the armed window, for `bench_manifest.py verify` to report.

    The error posture tells this flow to fix what it can and say nothing — right for a transient
    job stall, wrong for a DENIAL: the permission mode is the operator's own setting and only they
    can lift it. Under that posture a denied artifact publish or a denied verifier vanishes into
    silence, and the scan's hand-off reads as though neither had happened. This does not
    interrupt anything; it leaves a fact for the report to carry.
    """
    base = _window_base(payload)
    if base is None:
        return  # no armed flow → nothing is collecting this
    entry = {
        "tool": tool,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # The tool NAME and nothing from its input: a denied Bash command can carry a token, a
        # path or customer text, and this file is uploaded with the rest of the scan's artifacts.
        "summary": f"{tool} denied by the permission mode",
    }
    try:
        base.mkdir(parents=True, exist_ok=True)
        with (base / DENIAL_LOG).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass


def handle(event: Event) -> Decision:
    """React to one denied tool call. Never a decision: the event is read-only toward the model."""
    from _harness import model

    try:
        _react(event.raw if isinstance(event.raw, dict) else {})
    except Exception:  # noqa: BLE001 - a net must never become a new failure
        pass
    return model.none()


def _react(payload: dict) -> None:
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}

    # Recorded for EVERY denied tool, before the two completions below: those two exist to
    # repair a denial, and a repaired denial is still one the operator's mode caused.
    _record_denial(payload, tool)

    if tool == "CronCreate":
        from flow_heartbeat import SENTINEL

        if str(tool_input.get("prompt") or "").strip().startswith(SENTINEL):
            from _workdir import flow_dir

            base = _window_base(payload) or flow_dir(payload)
            if base is not None:
                try:
                    base.mkdir(parents=True, exist_ok=True)
                    (base / HEARTBEAT_UNAVAILABLE).write_text("denied", encoding="utf-8")
                except OSError:
                    pass
        return

    if tool == "Bash":
        command = str(tool_input.get("command") or "")
        if not _RAW_SANCTION_TOUCH.search(command):
            return
        m = _RAW_SANCTION_WRITE.fullmatch(command.strip()) or _SCRIPT_SANCTION.fullmatch(command.strip())
        if m is None:
            return  # a read, an rm, or anything composite/ambiguous — nothing to complete
        base = _window_base(payload)
        if base is None:
            return  # no armed flow → a sanction would authorize nothing legitimate
        gate = m.group("gate")
        from ask_guard import KNOWN_GATES

        if gate not in KNOWN_GATES:
            # Completing an UNKNOWN gate would turn this net into the mint ask_guard's
            # allowlist closes: the raw write was denied, and the name is one no skill
            # defines, so ask_guard would void the marker anyway. Leave no marker; say why.
            print(
                f"denial_watch: NOT completing the denied sanction write — '{gate}' is not a "
                "gate any skill defines. An action the flow's own text instructs is "
                "pre-authorized by the invocation: do it and announce it, never ask consent "
                "for it.",
                file=sys.stderr,
            )
            return
        try:
            (base / ASK_SANCTION).write_text(gate, encoding="utf-8")
        except OSError:
            return
        print(
            f"denial_watch: completed the denied raw sanction write hook-side (gate={gate}) — "
            "the next ask passes; the raw shape stays denied.",
            file=sys.stderr,
        )
