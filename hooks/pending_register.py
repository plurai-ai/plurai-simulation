#!/usr/bin/env python3
"""Mechanical pending-work registration (PostToolUse, prompt and subagent lifecycle hooks).

``report_guard`` blocks a mid-flow turn end that leaves nothing to resume the session
(the PENDING contract in ``docs/polling-cadence.md``). That rule needs to know
whether harness-tracked work is actually in flight — and this hook is what knows it,
because the harness itself re-invokes the session when such work finishes:

* a ``Bash`` call carrying ``run_in_background`` — a backgrounded ``run_wait.sh`` on a run,
  a backgrounded ``poll_job.sh``;
* a ``Bash`` call the harness MOVED to the background after launch — the intent is not in
  ``tool_input`` at all, only in ``tool_response`` (the background handle it hands back), so
  a register that reads the input alone cannot see the very case it exists for;
* a Copilot ``bash`` call in ``mode: "async"`` — the harness's own spelling of a background command, read by
  its codec into ``Event.background_shell`` — which ends at the ``read_bash`` result that reports the
  command finished or at the ``<system_notification>`` prompt Copilot injects when it exits
  (``Event.finished_shell``);
* an ``mcp__*`` call the harness moved to the background after its window expired — the
  handle arrives as harness-authored PROSE in the first text block, with no structured key
  anywhere in the response;
* a ``Task`` call — a spawned subagent, e.g. /analyze-simulation's independent critic;
* a ``Monitor`` call — an armed watch whose every emitted line re-invokes the session.

Only a harness that wakes the model when background work finishes or a subagent reports
(``Event.wakes_on_background``) registers a background shell, an MCP call, a subagent or its lease. On one that
does not (Codex) nothing would resume the session, so the turn that would end on such work is told to keep
polling in the foreground (``report_guard``) instead of being blessed here. The same holds under a declared
headless operator on a harness whose headless session ends with its turn (``Event.wakes_when_headless`` is
False: ``claude -p``), and there an armed ``Monitor`` registers nothing either: nothing measured says such a
session stays open for one.

A detached ``nohup … &`` is deliberately NOT registrable: the harness never hears from it,
so a turn ending on one genuinely does strand the flow, which is the defect being caught.

TWO RECORDS, because a tool call and a subagent have different lifetimes.

* The REGISTER (``.flow_pending``) is written when a tool call is observed to put work in
  flight. It is an event, not a lifetime: nothing tells it when the work ends.
* The LEASE (``.flow_lease``) is a SET of live holders, keyed by agent id: ``SubagentStart``
  adds the key, ``SubagentStop`` removes it, and the lease exists while the set is non-empty.
  So several concurrent subagents each hold their own entry instead of overwriting each
  other. Keyed, NOT counted — a repeated start for one key refreshes that entry rather than
  adding a second, because a single stop could never clear a second one. A subagent spans
  many turns and is not re-spawned to re-announce itself, so its liveness has to be a state
  the harness maintains rather than a token some later call renews.

WHY A HOOK AND NOT A SKILL SNIPPET. The first cut had the skills write the register
themselves. Review killed it on the precedent already in this directory — ``flow_arm``'s
docstring: "a model that skipped one Bash line ran the whole flow unguarded". A register
the model writes is an ASSERTION it can make without doing the work (verified: writing the
marker and making no ``Task`` call passed the gate, reproducing the exact stall), and three
of six skills had already dropped the ``$FLOWDIR`` derivation the write needs. Observing
the tool call instead makes the register a FACT, costs the skills nothing, and cannot be
satisfied by narration. It also removes the ``source … ; printf … >`` snippet shape that
``scripts/flow-window.sh``'s header says the permission classifier denies in auto mode.

The register is a RECORD OF WORK, not a bearer token: ``report_guard`` READS it and leaves it
standing, because the task outlives the turn that launched it (consuming it on a passing turn
blocked the very next turn while the work was still running). It ends when the WORK does —
which is why this hook watches for the end as carefully as for the start:

* a background shell, at the tool result that reports it finished (``_completed_handle``);
* a backgrounded MCP call — and a backgrounded shell the session never reads back — at the
  ``<task-notification>`` the harness queues as a user prompt when the task reaches a terminal
  status (``_notified_handle``, dispatched on ``UserPromptSubmit``);
* a subagent, at ``SubagentStop`` (lease and register together);
* any flow close — landing, stand-down, the next flow opening, session end.

Only a completion NOBODY observes (a Monitor that exits on its own, a harness that hands back
no shell handle to match on) falls through to ``report_guard``'s staleness backstop. Ending the
register at the completion is the whole point: the turn right after a finished job is where a
flow strands, and a launch-time record with no end would bless exactly that turn.

Fail-open, silent, exit 0 always: this hook never blocks a tool call. Its worst failure is
a missing register, which costs one block whose reason tells the model what to do.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
PENDING = ".flow_pending"
LEASE = ".flow_lease"
# Every canonical tool whose call can put harness-tracked work in flight, and (``MCP_PREFIXES``)
# the native name prefixes that make an MCP tool one. Kept in LOCKSTEP with the registry entry for
# this hook (a test pins it): a tool this hook understands but the registered matcher does not select
# never reaches the hook at all, so its work is unregistrable and every turn ending on it is blocked,
# which is exactly what an unmatched ``Monitor`` did. ``monitor`` and ``cron`` exist on Claude Code
# only; a harness without one never offers the call.
REGISTRABLE_TOOLS = ("shell", "subagent", "monitor", "cron")
# An MCP tool name is per-server and per-tool (``mcp__plugin_plurai-simulation_plurai__
# interact_edit_session``), so it cannot be enumerated the way the built-ins can, and a diagnostic
# call the harness backgrounds is precisely the long call this contract exists for.
MCP_PREFIXES = ("mcp__",)
# Keys the harness may carry a backgrounded call's handle under. Structured keys only — the
# response TEXT of an ordinary foreground command can say anything, and a register that fires
# on prose would back a turn end with nothing behind it.
_BACKGROUND_HANDLE_KEYS = ("backgroundTaskId", "background_task_id", "shellId", "shell_id", "bashId", "bash_id")
# Scoped to mcp__* only. A `Task`/`Monitor` result that happens to carry a `task_id` would
# otherwise be read as ITS background handle and suppress the register that tool just earned.
_MCP_HANDLE_KEYS = (*_BACKGROUND_HANDLE_KEYS, "task_id", "taskId")
# THE ONE DELIBERATE PROSE MATCH, and the reason it is safe where the Bash one is not: this
# sentence is written by the HARNESS, not by the called tool — an MCP server cannot author the
# first block of its own tool_response frame, while a shell command's stdout is entirely under
# the command's control. It is anchored at OFFSET 0 of the first text block (``.match``) so a
# frame quoted anywhere inside a payload cannot manufacture a register.
_MCP_BACKGROUNDED_RE = re.compile(
    # `.*?` rather than `[^.]*\.` — the duration is not always integral ("after 2.5 minutes"),
    # and a dot-excluding class silently failed to match, disabling the register.
    r'MCP tool "(?P<tool>[^"]*)" is still running.*?'
    r"It was moved to the background as task (?P<task>[A-Za-z0-9_-]+)",
    re.DOTALL,
)


def _first_text_block(response: object) -> str | None:
    """The text of the FIRST content block of an MCP tool response, else None."""
    if isinstance(response, list) and response:
        block = response[0]
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
    return None


def _native_tool(event: Event) -> str:
    """The tool's name as its harness spells it; the notes and the MCP test read this, not the canonical name."""
    return str(event.raw.get("tool_name") or event.raw.get("toolName") or "")


def _is_mcp(event: Event) -> bool:
    return _native_tool(event).startswith(MCP_PREFIXES)


def _response(event: Event) -> object:
    """The tool's result: ``tool_response`` on Claude Code and Codex, ``tool_result`` on Copilot."""
    raw = event.raw
    return raw["tool_response"] if "tool_response" in raw else raw.get("tool_result")


def _background_handle(event: Event) -> str | None:
    """The handle of a call the harness is holding in the background, else None.

    A command the harness MOVES to the background after launch never carried
    ``run_in_background`` in its input — the only evidence is the handle it hands back in
    ``tool_response``. Reading the input alone therefore misses precisely the case where the
    model did not plan the wait, which is when it most needs the register.

    Structured keys first. For an ``mcp__*`` tool there are none: the response is a content-block
    list and the handle exists only in the harness's own first line, so that one frame is parsed.
    """
    response = _response(event)
    mcp = _is_mcp(event)
    if isinstance(response, dict):
        for key in _MCP_HANDLE_KEYS if mcp else _BACKGROUND_HANDLE_KEYS:
            value = response.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        return None
    if mcp:
        text = _first_text_block(response)
        if text is not None:
            found = _MCP_BACKGROUNDED_RE.match(text)
            if found:
                return found.group("task")
    return None


def _registers(event: Event) -> str | None:
    """The note to record when this tool call puts harness-tracked work in flight, else None."""
    tool = event.tool or ""
    tool_input = event.tool_input
    if not event.wakes_on_background and (tool in ("shell", "subagent") or _is_mcp(event)):
        # Background work on a harness that starts no turn when it finishes or reports has nothing that
        # resumes the session, so a register for it would bless exactly the turn end it cannot back.
        return None
    if _is_mcp(event):
        # ONLY with a handle. A ``*_start`` job envelope also comes back "running", but the
        # harness is holding nothing for it and will never re-invoke the session — the flow
        # resumes it by POLLING, in a turn of its own. Registering there would back exactly the
        # turn end this contract exists to catch.
        handle = _background_handle(event)
        return f"background mcp ({handle}): {_native_tool(event)}" if handle else None
    if tool not in REGISTRABLE_TOOLS:
        return None
    if tool == "cron":
        # NOT a register — nothing is in flight. This branch exists to turn the dropped-turn
        # heartbeat sentinel into an OBSERVED FACT: `flow-window.sh arm` cannot know whether the
        # call it asked for was ever made, so writing the sentinel itself made "already
        # scheduled" a receipt for having printed the instruction. Only THIS flow's cron counts.
        return None
    if tool == "subagent":
        # The spawn tool is ``Agent`` (``Task`` in older Claude Code builds), ``task`` or ``Agent`` on
        # Copilot and ``collaborationspawn_agent`` on Codex. A harness whose spawn went unregistered
        # would lean entirely on the SubagentStart lease.
        return f"subagent: {tool_input.get('description') or tool_input.get('task_name') or 'spawned'}"
    if tool == "monitor":
        # An armed monitor streams events back into the session — each emitted line re-invokes
        # it, exactly like a subagent's report. Its absence here was a hole, not a policy: a
        # Monitor-based wait could not register at all, so a turn ending on one was blocked
        # while the watch it announced was genuinely running.
        return f"monitor: {tool_input.get('description') or 'armed'}"
    if tool == "shell":
        # The command itself is the most useful note for a human reading the register; the
        # guard never parses it. Truncated because a runner invocation can be very long.
        command = str(tool_input.get("command") or "")[:120]
        # The handle is recorded whenever the harness supplies one — for the EXPLICIT
        # background call too, not just the moved-to-background one: it is what a later
        # completion observation matches this register against, and a register nothing can
        # match ends only by the staleness backstop.
        handle = _background_handle(event) or event.background_shell or None
        if event.reports_background_shell:
            # The codec already read the harness's own background spelling. An input flag it never reports as
            # a running shell has no completion signal that could clear the register.
            backgrounded = event.background_shell is not None
        else:
            backgrounded = tool_input.get("run_in_background") is True or handle is not None
        if backgrounded:
            return f"background bash{f' ({handle})' if handle else ''}: {command}"
    return None


# --- ending the register ------------------------------------------------------------------
#
# A background shell ENDS where the model reads its result: the tool response then carries the
# same handle with a terminal status. Observing that is what ends the register, because the
# turn AFTER it is the strand this whole contract exists to catch — the model processes a
# finished job and stops with nothing to resume the flow, backed by the very launch whose
# completion it just consumed.
# `success` / `succeeded` are deliberately ABSENT. They are the word a backgrounded Bash LAUNCH
# response uses ("the call succeeded"), not a word about the work: treating one as terminal made
# `_completed_handle` fire on the launch itself, so `main()` cleared the note and NO register was
# ever written — the exact strand this contract exists to catch. Every status here says something
# about the WORK having ended.
_TERMINAL_STATUSES = frozenset(
    {
        "completed", "complete", "done", "finished",
        "failed", "error", "killed", "exited", "cancelled", "canceled", "timeout", "timed_out",
    }
)


def _completed_handle(event: Event) -> str | None:
    """The handle of a background shell this tool result reports as FINISHED, else None."""
    if event.finished_shell:
        return event.finished_shell  # the codec read it from the harness's own result format
    response = _response(event)
    if not isinstance(response, dict):
        return None
    if str(response.get("status") or "").strip().lower() not in _TERMINAL_STATUSES:
        return None
    return _background_handle(event)


# A backgrounded MCP call produces NO second tool result: the session never reads it back, so
# there is no ``_completed_handle`` moment. The harness instead queues a task notification as a
# user prompt, and that is the completion. It ends a backgrounded BASH register on the same
# terms — a shell whose output the model never opened has no other end either.
_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>.*?</task-notification>", re.DOTALL)
_TASK_ID_RE = re.compile(r"<task-id>\s*([A-Za-z0-9_-]+)\s*</task-id>")
_TASK_STATUS_RE = re.compile(r"<status>\s*([A-Za-z_]+)\s*</status>")


def _notified_handle(prompt: str) -> str | None:
    """The handle a ``<task-notification>`` prompt reports as FINISHED, else None."""
    frame = _TASK_NOTIFICATION_RE.search(prompt or "")
    if frame is None:
        return None
    body = frame.group(0)
    task = _TASK_ID_RE.search(body)
    status = _TASK_STATUS_RE.search(body)
    if task is None or status is None:
        return None
    if status.group(1).strip().lower() not in _TERMINAL_STATUSES:
        return None
    return task.group(1)


def _release_register(path: Path, *, handle: str | None = None, prefix: str | None = None) -> None:
    """Remove the register when it is the one this completion ended.

    Matched on CONTENT, never blindly. One file holds the most recent launch, so a subagent's
    stop must not clear a background runner's register — nor the reverse — while that other
    work is still running: releasing early re-opens the strand, and the guard is the only thing
    watching for it.
    """
    try:
        note = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if handle is not None and f"({handle})" not in note:
        return
    if prefix is not None and not note.startswith(prefix):
        return
    try:
        path.unlink()
    except OSError:
        pass


# --- the subagent lease -------------------------------------------------------------------
#
# One line per holder, ``<epoch>\t<key>\t<note>``. The epoch is per ENTRY, not per file: a
# holder whose ``SubagentStop`` never arrives (a crashed subagent, a killed session) must
# expire on its own rather than ride the file's mtime, which every OTHER holder refreshes.
_LEASE_SEP = "\t"


_LEASE_KEY_FIELDS = ("agent_id", "agent_type", "subagent_type", "agentName", "agent_name")


def _lease_keys(raw: dict) -> list:
    """The identities a live holder may be keyed by in the lease set, most specific first.

    ``agent_id`` when the harness supplies it, so concurrent subagents hold independent entries.
    The type name is the fallback: two concurrent subagents of one type then share one entry (the
    second start refreshes it, the first stop releases both), which errs toward releasing EARLY, so
    the guard blocks a turn whose sibling is still running. That is the safe direction, but not
    because it is a missed block: a released lease is exactly what makes the guard block, so an early
    release costs one EXTRA block, whose reason tells the model what to do. The unsafe direction would
    be releasing LATE: a lease outliving its work blesses the turn where the flow actually strands.

    A start takes the first key. A stop releases every key it names, because a harness may identify a
    subagent by name at its start and by id at its stop (Copilot's start carries only ``agentName``).
    """
    keys = []
    for field in _LEASE_KEY_FIELDS:
        value = str(raw.get(field) or "").strip()
        if value:
            keys.append(value.replace(_LEASE_SEP, " ").replace("\n", " ")[:120])
    return keys


def _read_lease(path: Path) -> list[tuple[float, str, str]]:
    """Parsed lease entries; unreadable or malformed lines are dropped, never fatal."""
    out: list[tuple[float, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split(_LEASE_SEP, 2)
        if len(parts) < 2:
            continue
        try:
            out.append((float(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""))
        except ValueError:
            continue
    return out


def _write_lease(path: Path, entries: list[tuple[float, str, str]]) -> None:
    """Replace the lease file, or remove it when nothing holds it.

    Written through a temp file + ``os.replace`` so a reader never sees a half-written lease:
    a torn line parses as garbage, is dropped, and the guard then blocks a turn whose subagent
    is very much alive.
    """
    try:
        if not entries:
            path.unlink(missing_ok=True)
            return
        body = "\n".join(f"{stamp:.0f}{_LEASE_SEP}{key}{_LEASE_SEP}{note}" for stamp, key, note in entries)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # unwritable layout degrades to one block, never a failed subagent


def live_lease_holders(path: Path, cutoff: float) -> list[str]:
    """The keys holding ``path`` whose entries are newer than ``cutoff``.

    The reader half of the format, exported so ``report_guard`` cannot drift from the writer.
    """
    return [key for stamp, key, _note in _read_lease(path) if stamp >= cutoff]


def _take_lease(path: Path, key: str, note: str) -> None:
    entries = [e for e in _read_lease(path) if e[1] != key]
    entries.append((time.time(), key, note))
    _write_lease(path, entries)


def _release_lease(path: Path, key: str) -> None:
    _write_lease(path, [e for e in _read_lease(path) if e[1] != key])


#: Written beside the flow window once THIS session's dropped-turn heartbeat cron is observed to
#: exist. `scripts/flow-window.sh` reads it to decide whether to re-print the CronCreate
#: instruction; it never writes it, because a script cannot observe a tool call.
HEARTBEAT_SCHEDULED = ".flow_heartbeat_scheduled"


def _record_heartbeat(base: Path, event: Event) -> None:
    """Write the sentinel when this cron call is the flow's OWN heartbeat, else do nothing."""
    from flow_heartbeat import SENTINEL  # the prompt's owner defines the string

    if not str(event.tool_input.get("prompt") or "").strip().startswith(SENTINEL):
        return  # somebody else's cron says nothing about this flow's rescue path
    try:
        (base / HEARTBEAT_SCHEDULED).write_text(SENTINEL, encoding="utf-8")
    except OSError:
        pass  # an unwritable layout degrades to one extra instruction, never to a failed call


def _session_wakes(event: Event) -> bool:
    """The harness's wake, less a declared headless operator where headless ends the session with its turn."""
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    return _run_driver.wakes_on_background(event.wakes_on_background, event.wakes_when_headless)


#: The events this hook dispatches on besides a tool call, as the codecs name them.
_PROMPT = "user_prompt_submit"
_SUBAGENT_START = "subagent_start"
_SUBAGENT_STOP = "subagent_stop"


#: What the main thread is told when a sub-agent it spawned returns inside a flow (report doctrine, item 1).
RELAY = "Before your next step, say this progress line to the user, verbatim, on its own line, and nothing more about the work: {line}"
#: A main-thread shell call that printed the progress line (``progress_line.py``): the main thread said it.
_PROGRESS_LINE_CALL = re.compile(r"\bprogress_line\.py\b")


def _page_dirs(event: Event) -> tuple[Path | None, Path | None] | None:
    """``(workdir, flow dir)`` of the page's running state inside a flow window, else None."""
    from _workdir import find_marker, flow_dir, marker_dirs, resolve_workdir

    payload = {"session_id": event.session_id}
    bases = marker_dirs(payload)
    if find_marker(FLOW_MARKER, bases) is None:
        return None
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time
    import simulate_continue  # noqa: PLC0415 - puts scripts/ on sys.path; the plan's workdir reader

    plan = _run_driver.read_plan(_run_driver.find_plan(bases))
    return (simulate_continue.plan_workdir(plan) if plan is not None else resolve_workdir()), flow_dir(payload)


def _told(event: Event) -> None:
    """A main-thread ``progress_line.py`` call that printed a line: the main thread said it, so the relay keeps silent on it.
    A sub-agent's call (its payload carries ``agent_id``) never reached the chat and records nothing here."""
    if event.kind != "post_tool_use" or event.tool != "shell" or event.raw.get("agent_id"):
        return
    response = _response(event)
    printed = response.get("stdout") if isinstance(response, dict) else response if isinstance(response, str) else ""
    if not _PROGRESS_LINE_CALL.search(str(event.tool_input.get("command") or "")) or not str(printed or "").strip():
        return
    dirs = _page_dirs(event)
    if dirs is not None:
        import publish_report  # noqa: PLC0415 - scripts/ is on sys.path (simulate_continue)

        publish_report.progress_told(*dirs)


def _relay(event: Event) -> str | None:
    """The page's running step, owed to the chat when a sub-agent the MAIN thread spawned returns (``progress_line.py``).

    A stage handed to sub-agents is silent for the whole hop (RT67: 149 minutes): what a sub-agent writes never reaches the
    chat, and the doctrine's prose rule to relay was skipped. Observed here, the relay is a fact, like the register. A spawn
    from inside a sub-agent carries its ``agent_id`` and is never told (that stamp is the main thread's), and a background
    spawn returns before its work, so its return has nothing new: flagged in its input, or a response whose ``status`` is
    not ``completed`` (a spawn the harness backgrounds by default, a Codex spawn). Only inside a flow window; the line is
    said once, against the main thread's own record (``PROGRESS_TOLD``), never a sub-agent's ``progress_line.py`` call."""
    if event.kind != "post_tool_use" or event.tool != "subagent" or event.raw.get("agent_id") or event.tool_input.get("run_in_background") is True:
        return None
    response = _response(event)
    if isinstance(response, dict) and "status" in response and response["status"] != "completed":
        return None
    dirs = _page_dirs(event)
    if dirs is None:
        return None
    import publish_report  # noqa: PLC0415 - THE progress line; scripts/ is on sys.path (simulate_continue)

    return publish_report.progress_line(*dirs, stamp=publish_report.PROGRESS_TOLD)


def handle(event: Event) -> Decision:
    """Record or release pending work for this event; never an error.

    Silent on every harness (a UserPromptSubmit hook's output is injected into the model's context), but for one context
    line: the progress line a main-thread sub-agent's return owes the chat (:func:`_relay`).
    """
    # Imported here so the module stays importable without the codec package beside it.
    from _harness import model

    try:
        _record(event)
    except Exception:  # noqa: BLE001 - registration is best-effort; it must never affect a tool call
        pass
    try:
        _told(event)
        line = _relay(event)
    except Exception:  # noqa: BLE001 - a relay is best-effort; the doctrine's sentence stays the fallback
        line = None
    return model.context(RELAY.format(line=line)) if line else model.none()


def _record(event: Event) -> None:
    from _workdir import find_marker, marker_dirs

    payload = {"session_id": event.session_id}
    if event.kind == _PROMPT:
        # The harness queues a ``<task-notification>`` user prompt when a backgrounded task reaches a
        # terminal status, the only completion signal a backgrounded MCP call ever produces. Only a
        # prompt counts: a block reason the harness hands back decodes as ``reinjected`` and falls
        # through to the tool branch, which has nothing to read in it.
        notified = _notified_handle(str(event.raw.get("prompt") or "")) or event.finished_shell
        if notified is not None:
            # Every marker directory, as at SubagentStop: the launch may have been observed under a
            # window that has since been re-armed elsewhere.
            for base in marker_dirs(payload):
                _release_register(base / PENDING, handle=notified)
        return
    lifecycle = event.kind in (_SUBAGENT_START, _SUBAGENT_STOP)
    if lifecycle and not event.wakes_on_background:
        return  # a lease would bless a turn end that nothing resumes when the subagent reports
    note = None if lifecycle else _registers(event)
    if (lifecycle or note is not None) and not _session_wakes(event):
        # A headless session that ends with its turn: the work would die with it, so nothing blesses that turn end.
        if lifecycle:
            return
        note = None
    done = None if lifecycle else _completed_handle(event)
    if done is not None:
        note = None  # a result that reports the work FINISHED never opens a register
    keys = _lease_keys(event.raw) if lifecycle else []
    # A cron call opens no register (nothing is in flight) but still has work to do: it is the
    # OBSERVATION that turns the heartbeat sentinel into a fact. It must therefore survive the early
    # exit, which reads "nothing to record" off the register/lease alone.
    is_cron = not lifecycle and event.tool == "cron"
    if note is None and done is None and not keys and not is_cron:
        return
    bases = marker_dirs(payload)
    # Only inside a flow. Outside one the register would be litter nothing reads, and writing it into
    # an arbitrary repo is exactly the phantom-file hazard flow_arm refuses to create. The window's
    # OWN location is authoritative: writing beside it is what guarantees the guard's `find_marker`
    # looks where this wrote, closing the divergence a `$FLOWDIR`-derived write could open.
    marker = find_marker(FLOW_MARKER, bases)
    if marker is None:
        return
    if is_cron:
        _record_heartbeat(marker.parent, event)
    elif event.kind == _SUBAGENT_START:
        raw = event.raw
        kind = str(raw.get("agent_type") or raw.get("subagent_type") or raw.get("agentName") or raw.get("agent_name") or "subagent")
        _take_lease(marker.parent / LEASE, keys[0], kind)
    elif event.kind == _SUBAGENT_STOP:
        # Released in EVERY marker directory, not just the one holding the window: the start may have
        # been observed under a window that has since been re-armed elsewhere, and a lease nobody
        # releases is the latch this contract cannot have. The subagent's REGISTER goes with it: the
        # spawn wrote one at launch, and the turn after the critic reports is exactly where a flow
        # strands.
        for base in bases:
            for key in keys:
                _release_lease(base / LEASE, key)
            _release_register(base / PENDING, prefix="subagent:")
    elif done is not None:
        for base in bases:
            _release_register(base / PENDING, handle=done)
    elif note is not None:
        try:
            (marker.parent / PENDING).write_text(note, encoding="utf-8")
        except OSError:
            pass  # unwritable layout degrades to one block, never a failed tool call
