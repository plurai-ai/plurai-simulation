#!/usr/bin/env python3
"""Dropped-turn detector for the plurai command flows (StopFailure hook).

``Stop`` does not fire when a turn ends on an API error — ``StopFailure`` does. That gap is why
the pending contract (docs/polling-cadence.md) cannot cover a turn killed mid-response: the
contract lives in ``report_guard``, a Stop hook, and on this path no Stop hook runs at all. The
flow is then idle with nothing scheduled to re-drive it, and it stays that way until a human
types something.

This hook records the fact and nothing else. It writes ``.flow_dropped`` beside the window when a
turn of an ARMED flow ended on an API error a later attempt can actually get past.
``hooks/flow_heartbeat.py`` is the only reader, and that marker is the ONLY thing that lets the
flow's heartbeat cron reach the model — which is what keeps the heartbeat free: with no marker a
firing is blocked before any API call is made.

WHAT COUNTS AS RECOVERABLE — decided against the real corpus, not the vocabulary. Across the 955
``isApiErrorMessage`` rows in ``~/.claude/projects`` on the machine this was built on:

* ``server_error`` (52) — "Connection lost/closed mid-response", 529 Overloaded, ENOTFOUND. The
  the class a long unattended build meets most, and the primary target.
* ``unknown`` (35) — "The socket connection was closed unexpectedly" ×18, "Unable to connect to
  API (ConnectionRefused)" ×14, FailedToOpenSocket, "Stream idle timeout". Every one a transport
  drop, i.e. exactly what this exists to rescue. It reaches the recoverable path by being in
  NEITHER set (see below), which is why non-membership must not read as a refusal.
* ``rate_limit`` (856) is deliberately NOT recoverable, and this is the one call the vocabulary
  gets wrong. Only 12 of those 856 are the transient class ("Server is temporarily limiting
  requests (not your usage limit)"); the other 844 are hard stops — 565 org monthly spend limit,
  182 weekly limit, 57 session limit — that clear in hours or need an admin. Treating the bucket
  as recoverable would make the DOMINANT error class a self-sustaining retry loop: the rescue
  turn dies on the same 429, StopFailure re-arms, every cron period, for as long as the session
  lives. ``report_guard``'s strike counter cannot bound it, because that is a Stop hook and by
  construction never runs here. A flow that hits a usage cap has stopped, not stalled.

TWO SIGNALS. The payload's class arrives as ``error`` — the harness builds the StopFailure input as
``{hook_event_name, error, error_details, last_assistant_message}``, with ``error`` drawn from the
matcher vocabulary and defaulting to ``"unknown"``. That is the first signal. When it is missing or
is a value in neither set, the transcript's last main-thread assistant row decides instead — a
dropped stream is finalized into a row carrying ``isApiErrorMessage`` and ``error`` (that row shape
is undocumented but verified against the real transcripts above). So a renamed or missing payload
FIELD still gets the rescue. It does not widen the vocabulary: both paths refuse the same explicit
non-recoverable classes, and everything else is treated as a transport failure.

Fail-SAFE rather than fail-open or fail-closed: any error → no marker. A missing marker leaves
today's behaviour untouched (the flow waits for a human), which is always safer than a marker
written for a turn that did not really drop.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
DROPPED = ".flow_dropped"

# Classes a later attempt can get past. ``overloaded``, ``model_not_found`` and
# ``max_output_tokens`` are documented matcher values that never appear in the corpus above
# (529s arrive as ``server_error``); they are classified on their documented meaning, not on
# observation.
RECOVERABLE = frozenset({"server_error", "overloaded"})
# Classes that will NOT clear on their own, so a rescue would burn a turn per period and end in
# the same place. Listed explicitly because non-membership is what routes a value to the
# transcript: an error class outside BOTH sets (``unknown``, or a class added after this was
# written) must be treated as a possible transport drop, never silently as a refusal.
# The set must therefore hold EVERY hard stop in the vocabulary — a hard stop missing from it is
# classified recoverable, the cron re-fires every period, and each rescue dies the same way with
# nothing to bound it (``report_guard``'s strike counter is a Stop hook and never runs on this path).
NON_RECOVERABLE = frozenset(
    {
        "rate_limit",  # see the docstring: 844/856 real occurrences are hard usage caps
        "authentication_failed",
        "oauth_org_not_allowed",
        "account_on_hold",  # needs an appeal, not a retry
        "billing_error",
        "invalid_request",
        "model_not_found",
        "max_output_tokens",
    }
)

# How much of the transcript tail to read. It is JSONL and can be megabytes; the row that decides
# is the last one written, so a bounded tail read is size-independent and in practice sufficient.
# A tail carrying no assistant row at all reads as "no drop" — the safe direction.
_TAIL_BYTES = 65536


def _dropped_in_transcript(path_str: str) -> bool:
    """True when the transcript's last MAIN-THREAD assistant row is a recoverable API error.

    The independent second signal (see the docstring). Only the last such row counts: an earlier
    error in the same session is history, and a turn that recovered from one has a normal row
    after it. Sidechain (subagent) rows are skipped — they belong to another thread's turn, and
    only the main thread's ending is this hook's business. A row that does not parse ends the read
    (``_transcript.rows_newest_first``), so a truncated tail reads as "no drop" and lets the payload
    path decide.
    """
    from _transcript import rows_newest_first  # noqa: PLC0415 - hooks/ is on sys.path at call time

    for row in rows_newest_first(path_str, tail_bytes=_TAIL_BYTES):
        if not isinstance(row, dict) or row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        if not row.get("isApiErrorMessage"):
            return False  # the turn ended normally
        return str(row.get("error") or "").strip() not in NON_RECOVERABLE
    return False


def _error_class(payload: dict[str, Any]) -> str:
    """The turn's error class as the harness publishes it.

    ``error`` is the field name in the StopFailure input. Reading a wrong one costs both branches
    below at once — the explicit refusal never fires, and the recoverable classes fall through to
    the transcript, leaving the "two signals" redundancy as one.
    """
    return str(payload.get("error") or "").strip()


def _recoverable(payload: dict[str, Any]) -> bool:
    """Did this turn end on something a later attempt can get past?"""
    error_class = _error_class(payload)
    if error_class in RECOVERABLE:
        return True
    if error_class in NON_RECOVERABLE:
        return False  # an explicit refusal is authoritative — do not second-guess it
    return _dropped_in_transcript(str(payload.get("transcript_path") or ""))


def handle(event: Event) -> Decision:
    """Write ``.flow_dropped`` when an armed flow's turn dropped on a recoverable error. Never a decision."""
    from _harness import model

    try:
        _record(event)
    except Exception:  # noqa: BLE001 - a detector failure must never affect the session
        pass
    return model.none()


def _record(event: Event) -> None:
    from _workdir import find_marker, marker_dirs

    payload = event.raw
    bases = marker_dirs({"session_id": event.session_id})
    marker = find_marker(FLOW_MARKER, bases)
    if marker is None:
        return  # no flow of ours was running → nothing to resume, nothing to record
    if not _recoverable(payload):
        # The flow has stopped, not stalled: the page's running stage reads halted, with its default sentence.
        import simulate_continue  # noqa: PLC0415 - hooks/ is on sys.path at call time

        simulate_continue.halt_page(event, bases, None)
        return
    # Beside the window that was actually found, so the reader's union sees it wherever this
    # flow was armed (the session directory, or the agent-directory fallback). The content is
    # diagnostic only and is never read back: "unclassified" is the honest stamp when the
    # transcript decided rather than the payload.
    try:
        stamp = _error_class(payload) or "unclassified"
        (marker.parent / DROPPED).write_text(f"{stamp} {int(time.time())}\n", encoding="utf-8")
    except OSError:
        pass  # an unwritable marker degrades to today's behaviour, never to an error
