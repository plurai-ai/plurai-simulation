#!/usr/bin/env python3
"""SessionEnd sweeper for the flow-fidelity markers.

The report_guard closes the flow window on a validated hand-off, but a flow can also end
without one — a precondition early-exit, a user interrupt, a crashed session. A marker
that outlives its session would deny the NEXT session's legitimate asks until the teach
text's manual recovery. This hook removes the markers at session end; within a session,
each command's Step 0 clears stale residue before opening its own window.

TWO sweeps, because there are two ways a window outlives its flow:

1. **This session's markers** — the session flow directory (and, for a fallback-armed or
   legacy window armed before windows were keyed by session, the agent directory the cwd cascade
   resolves to). The session directory's window, run driver stamp, plan and live page (``_FLOW_OWNED``) stay while the window
   stands, unless the session ended by ``/clear``: a resume re-enters the same id and continues the same flow, in the tab
   already open. With no window the flow is finished, and those go with the rest (the stamp describes the window), except
   the live page and its sidecar: the tab opened at the run's start still points there, and a refresh must find it.
2. **Other sessions' abandoned directories, by age.** SessionEnd does not always fire — a
   killed terminal, a crashed host — so a session directory can outlive its session.

   Be precise about what this buys, because the tempting justification is wrong: an
   abandoned SESSION directory cannot deny anything to a NEW session, which gets a new id and
   never looks inside it. Only a resume of the same id does, and it is the same flow coming
   back. So it is litter, not a stuck window — worth collecting because
   nothing else will, but it is not the thing that went wrong in the field. The window that
   really did sit armed for days lived in an AGENT directory, and this sweep deliberately
   does not touch those: their contents are the customer's, not ours.

Fail-open, silent, exit 0 always — the same posture as the guards it cleans up after.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from _harness.reinject import RECORD as _LAST_BLOCK_REASON
from _workdir import HOOKS_DEGRADED_MARKER, HOOKS_READY_MARKER

if TYPE_CHECKING:
    from _harness.model import Decision, Event

_MARKERS = (
    ".plurai_flow_active",
    ".ask_sanctioned",
    ".ask_pending",
    ".ask_deny_streak",
    ".ask_breaker_spent",
    ".report_guard_strikes",
    ".flow_pending",
    ".flow_lease",
    ".flow_dropped",
    ".flow_heartbeat_scheduled",
    ".flow_heartbeat_unavailable",
    ".flow_heartbeat_lapsed",
    # denial_watch's log. Not listed here it is never unlinked — and because the sweep below
    # rmdir()s the session directory only when it is EMPTY, one leftover log kept every session
    # directory alive until the 14-day prune, while a workdir-armed log accumulated across scans
    # and fed last month's denials into this scan's integrity line.
    ".denials.jsonl",
    # The run driver record and the typed findings a headless flow leaves (hooks/_run_driver.py).
    # Session-scoped like everything above, and unlisted they would be the two files that keep every
    # session directory non-empty — so the rmdir below silently fails and the whole sweep stops
    # collecting, exactly as `.denials.jsonl` did.
    ".run_driver",
    ".run_driver_findings.jsonl",
    # The suite claim's key (hooks/_suite_window.py) and the entry hook's epoch decision
    # (scripts/open_epoch.py): session-scoped records, read by this session's close and Step-0 arm.
    ".suite_claim",
    ".epoch_opened",
    # The command run id and the command scope (hooks/_command_run.py): swept with the window they belong to.
    ".command_run_id",
    ".plurai_command_scope",
    # SessionStart's proof that the hooks run (hooks/session_glossary.py); the next SessionStart writes it again.
    HOOKS_READY_MARKER,
    # Step 0's record that the session ran in limited mode (hooks/_readiness.py), swept with the marker it stands in for.
    HOOKS_DEGRADED_MARKER,
    # The Stop block reason a harness may hand back as a prompt (_harness/reinject.py).
    _LAST_BLOCK_REASON,
    # A /simulate run's plan (hooks/simulate_continue.py) and the overview page's URL waiting for the
    # scan to adopt it (scripts/publish_report.py). Session-scoped flow state like the window: kept
    # with it on a resume (_FLOW_OWNED), swept otherwise — unlisted, they kept the directory non-empty.
    ".plurai_simulate.json",
    ".plurai_simulate_seen",
    # When a person last typed to the run (hooks/simulate_continue.py USER_TURN_MARKER); `simulate end` reads it
    # beside the plan. Swept with the plan's other session state, not kept: a resumed run takes the message the
    # user types next, and an old one must not license its `end`.
    ".plurai_simulate_user",
    ".report_url_pending",
    # The trace source a never-scanned project's start question recorded before its workdir existed
    # (scripts/simulate_plan.py traces); the scan's run moves it into the workdir.
    ".simulate_state_pending.json",
    # The run's live local page (scripts/publish_report.py: the seeded start page of a never-scanned project, and the
    # mirror of the workdir's page every fill of the run copies there) and its update sidecar, with that fill's record.
    # The page and sidecar stay with the plan (`_FLOW_OWNED`): a resume opens no new tab, so the open one keeps polling
    # them, and a finished run keeps them for the tab still open on them (`_FINISHED_RUN_PAGE`). A `/clear` during the run
    # and the TTL prune sweep them; the fill's record is swept at every session end.
    "report.html",
    "report.data.js",
    # The running-state record the overview page is drawn from before the workdir exists (scripts/publish_report.py).
    "flow_progress.json",
    ".report_fill.json",
    # The local pages `done` already opened (scripts/publish_report.py). One run across stages, like the pending URL.
    ".report_opened.json",
    # The page watcher's lock (scripts/page_watch.py), which it removes itself on exit: this catches a killed holder's.
    ".page_watch.lock",
    # Its stderr, for the operator.
    ".page_watch.err",
)
# The empty seed tree that start page fills from (`$FLOWDIR/report_seed/artifacts/`). Removed with
# rmdir only, deepest first — never rmtree: a seed holding anything else is left, and logged nowhere.
_SEED_DIRS = ("report_seed/artifacts", "report_seed")

# Deliberately generous. The mtime here is the last MARKER WRITE, not the last sign of life,
# so a still-open session whose window was armed long ago looks identical to an abandoned one
# — and pruning a live session's window turns both guards off mid-flow, which is precisely the
# failure this whole change exists to end. Long-lived terminals are normal; the cost of being
# wrong in the other direction is a few tiny files per abandoned session.
_TTL_SECONDS = 14 * 24 * 3600

# The WINDOW and its run driver stamp belong to the FLOW, not the process: a resume (`claude --resume`,
# `codex resume`) re-enters THIS session id, so sweeping them here ends a flow the next turn
# continues. They are closed by report_guard / `flow-window.sh close`, by `/clear`, or by the TTL
# below. The stamp is kept only while the window stands: a flow closed by report_guard's hand-off
# leaves the stamp behind, and nothing reads it once the window is gone. A /simulate plan and its
# pending overview URL are the same kind of state: one run across stages. So are the typed run driver findings: the
# flow's own readers see them on a resume. A new arm clears them (`flow_arm.RESIDUE_MARKERS`), and a closed window or a
# `/clear` sweeps them here. A headless driver does not depend on this copy: it reads its own record directory
# (`_run_driver.DRIVER_RECORD_ENV`), which no sweep touches. The live page and its sidecar stay for the same run: a resume
# opens no new tab, and the one open keeps polling them.
_FLOW_OWNED = (".plurai_flow_active", ".command_run_id", ".run_driver", ".run_driver_findings.jsonl", ".plurai_simulate.json", ".plurai_simulate_seen", ".report_url_pending", ".report_opened.json", ".simulate_state_pending.json", "flow_progress.json", "report.html", "report.data.js")

# What a finished run leaves behind: the page the customer has had open since the run's start. A refresh after the session
# ends must still find it, and the TTL prune collects it with the directory.
_FINISHED_RUN_PAGE = ("report.html", "report.data.js")

# A kept window outlives its process, and the session-only heartbeat cron does not. This marker
# records that debt for the next arm of the resumed flow (scripts/flow-window.sh), which is the
# only reader: the sentinel swept above cannot say it, since a live window whose cron is not yet
# observed has no sentinel either. Written after the sweep, never preserved through it, and only
# on a harness that has a heartbeat at all (`registry.has_heartbeat`).
_HEARTBEAT_LAPSED = ".flow_heartbeat_lapsed"


def _prune_abandoned(root: Path, keep: Path | None) -> None:
    """Delete session flow directories older than the TTL, sparing ``keep``."""
    cutoff = time.time() - _TTL_SECONDS
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if keep is not None and entry == keep:
            continue
        try:
            # A symlink is never followed: is_dir()/stat() would resolve it and the unlink loop
            # below would then empty whatever it points AT. Nothing legitimate puts a symlink
            # here, which is exactly why it must not be treated as an ordinary session dir.
            if entry.is_symlink() or not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                continue
            # One level only, and unlink (never rmtree): a session directory holds a few flat
            # marker files (_MARKERS). Anything else in there raises and is skipped rather than recursed
            # into — this code deletes under $HOME and gets no benefit from being clever.
            for seed in _SEED_DIRS:
                try:
                    if (entry / seed).is_dir() and not (entry / seed).is_symlink():
                        for leftover in (entry / seed).iterdir():
                            if leftover.is_file() and not leftover.is_symlink():
                                leftover.unlink()
                        (entry / seed).rmdir()
                except OSError:
                    pass
            for child in entry.iterdir():
                child.unlink()
            entry.rmdir()
        except OSError:
            continue  # a directory we cannot clear is not worth failing a session end over


def handle(event: Event) -> Decision:
    """Sweep this session's markers and collect abandoned sessions. Never a decision, never an error."""
    # Imported here so the module stays importable without the codec package beside it.
    from _harness import model

    try:
        _sweep(event)
    except Exception:  # noqa: BLE001 - cleanup must never fail a session end
        pass
    return model.none()


def _sweep(event: Event) -> None:
    from _workdir import FLOW_ROOT, flow_dir, marker_dirs, page_may_wait
    from registry import has_heartbeat

    payload = {"session_id": event.session_id}
    session_dir = flow_dir(payload)
    window_open = session_dir is not None and (session_dir / ".plurai_flow_active").exists()
    standing = window_open and event.raw.get("reason") != "clear"
    keep = _FLOW_OWNED if standing else () if window_open else _FINISHED_RUN_PAGE
    bases = marker_dirs(payload)
    if session_dir is not None and session_dir.is_dir() and page_may_wait(bases):
        # Nobody is left to answer: the overview page stops saying the flow waits (before a sweep that does not keep the
        # flow dir's copy takes it). Only a session that ran a flow has its directory, and only a page showing a wait costs an import.
        try:
            from simulate_continue import wait_page  # noqa: PLC0415 - hooks/ is on sys.path at call time

            wait_page(event, bases, False, workdir=next((base for base in bases if base != session_dir), None))
        except Exception:  # noqa: BLE001 - cleanup must never fail a session end
            pass
    for base in bases:
        for name in _MARKERS:
            if base == session_dir and name in keep:
                continue
            try:
                (base / name).unlink()
            except OSError:
                pass
        for seed in _SEED_DIRS:
            try:
                if not (base / seed).is_symlink():
                    (base / seed).rmdir()
            except OSError:
                pass
    if standing and session_dir is not None and has_heartbeat(event.harness):
        try:
            (session_dir / _HEARTBEAT_LAPSED).write_text("", encoding="utf-8")
        except OSError:
            pass
    # The session's own directory, once empty of markers: remove it too, so the TTL sweep below has
    # less to walk on later sessions.
    if session_dir is not None:
        try:
            session_dir.rmdir()
        except OSError:
            pass  # non-empty (something else wrote here) or already gone
    _prune_abandoned(FLOW_ROOT, session_dir)
