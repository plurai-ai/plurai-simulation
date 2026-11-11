#!/usr/bin/env python3
"""Arm the flow window when a command flow is entered through the skill TOOL.

``flow_arm`` (UserPromptSubmit) arms when the user TYPES ``/simulation-generate-suite``. It is
structurally blind to the other entry: the model invoking the same flow through the skill tool,
which fires no ``UserPromptSubmit`` at all. In a live session the scan was typed and armed
mechanically, then five consecutive flows (setup, generate-suite, extract-variables, run, analyze)
all arrived as skill calls, so arming rested entirely on the skills' own Step-0 Bash line. That line
is observably skippable: two of those skills never executed theirs, which was benign only because a
driver's window happened to stand. A skipped arm is SILENT and total: ask_guard and report_guard both
read the window and both fail open, so the whole flow runs unguarded with nothing saying so.

This hook closes that seam at the entry itself: PreToolUse on the skill tool, one stamp, no verdict.
It never denies. The only capability it exercises is writing the marker the guards read, exactly the
shape ``denial_watch`` uses (complete the semantics hook-side, grant nothing else). The skill tool is
``Skill`` on Claude Code and ``skill`` on Copilot CLI; Codex loads a skill without a tool call, so the
hook is not registered there and a Codex flow is armed by the skill's own Step-0 line alone.

DESIGN INVARIANTS
-----------------
* **Fail-open, and never fail INVISIBLY.** The decision is always "none", so every failure path
  allows. But a write that fails leaves the flow unguarded, which is this hook's own reason to exist,
  so that one path says so on stderr rather than joining the silence of the 20 sub-skills that
  legitimately arm nothing.
* **Arming clears the previous flow's residue** (``flow_arm.sweep_residue``), exactly as
  ``flow-window.sh arm`` does. Inheriting a strike counter stands ``report_guard`` down and sweeps
  the window, which disarms BOTH guards for a flow that has not earned a strike.
* **Driver-guarded**, unlike ``flow_arm``. A typed command is the user re-pointing the window
  deliberately, so it overwrites unconditionally; a skill call is often the DRIVER invoking its own
  sub-step (/simulation-generate-suite -> /analyze-simulation -> /simulation-run), and a driven
  sub-step must never clobber its driver's window. That is what ``scripts/flow-window.sh``'s own arm
  refuses to do (``_drivers_of``), and report_guard's anchor profile depends on the driver's name
  standing.
* **One command table**, imported from ``flow_arm`` (``COMMANDS``): a second copy of the six names
  is exactly the drift ``FLOW_DRIVERS``' lockstep tests exist to prevent.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"


def _skill_name(event: Event) -> str:
    """The skill being invoked, or ``''``. Never raises."""
    return str(event.tool_input.get("skill") or "").strip()


def marker_for(skill: str) -> str | None:
    """The flow marker name a skill invocation arms, or None for a non-flow skill.

    Accepts the plugin-qualified form the skill tool carries (``plurai-simulation:simulation-run``)
    and the bare directory name. The 20 sub-skills (build-rag-corpus, generate-evals, edit-*,
    upload-*, ...) resolve to None BY DESIGN: they deliberately arm no window of their own and run
    inside their driver's.
    """
    from flow_arm import COMMANDS

    name = skill.rsplit(":", 1)[-1].strip().strip("/")
    return COMMANDS.get(name)


def handle(event: Event) -> Decision:
    """Arm the window for a flow entered through the skill tool. Never a decision."""
    # Imported here so the module stays importable without the codec package beside it.
    from _harness import model

    try:
        _arm(event)
    except Exception:  # noqa: BLE001 - arming is best-effort; it must never break a skill call
        pass
    return model.none()


def _arm(event: Event) -> None:
    if event.tool != "skill":
        return
    command = marker_for(_skill_name(event))
    if command is None:
        # A sub-skill arms no window by design; one that starts backend jobs opens a command scope instead.
        from flow_arm import SCOPED_COMMANDS, command_dir, open_command_scope

        name = _skill_name(event).rsplit(":", 1)[-1].strip().strip("/")
        target = command_dir({"session_id": event.session_id}) if name in SCOPED_COMMANDS else None
        if target is not None:
            open_command_scope(name, target, typed=False)
        return

    from _workdir import find_marker, flow_dir, marker_dirs, resolve_workdir
    from flow_arm import FLOW_DRIVERS, open_evidence_epoch, sweep_residue

    payload = {"session_id": event.session_id}
    # Read the window wherever it lives (session dir, legacy, agent dir): the same union every guard
    # reads, so this decision cannot disagree with them.
    bases = marker_dirs(payload)
    existing = find_marker(FLOW_MARKER, bases)
    if existing is not None:
        try:
            lines = existing.read_text(encoding="utf-8", errors="replace").splitlines()
            current = lines[0].strip() if lines else ""
        except OSError:
            return  # a window whose state cannot be read is not one to re-point
        if current == command:
            return  # already armed for this flow
        if current in FLOW_DRIVERS.get(command, ()):
            # The driver invoking its own sub-step. Its window stands and it owns the hand-off;
            # re-stamping here would strip the driver's report profile and, for the findings-owning
            # commands, reset an evidence epoch mid-build.
            return

    target = flow_dir(payload)
    if target is None:
        # No session id: the agent directory, where the skills' own snippets land without one.
        # Never created here: a mkdir on an ambiguous layout materializes a phantom plurai_sim/ that
        # poisons the nearest-ancestor rule.
        target = resolve_workdir()
        if target is None or not target.is_dir():
            return
    # ARM, LOUDLY ON FAILURE. A write that fails here leaves the flow unguarded, the exact outcome
    # this hook exists to prevent, and the outer catch would make that indistinguishable from the
    # silence of the 20 sub-skills that arm nothing. Fail open (never break the skill call), but
    # never fail invisibly.
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / FLOW_MARKER).write_text(command, encoding="utf-8")
    except OSError as exc:
        print(
            f"flow_autoarm: could NOT arm '{command}' at {target} ({exc}) — this flow is "
            "running with the fidelity guards OFF unless its own Step-0 line arms it.",
            file=sys.stderr,
        )
        return
    # The previous flow's residue, cleared exactly as `flow-window.sh arm` clears it: an inherited
    # strike counter stands report_guard down and sweeps the window, disarming BOTH guards for a
    # flow that has not earned a strike.
    sweep_residue(bases)
    open_evidence_epoch(command, target)
    # WHO armed this flow: the same stamp `flow_arm` writes on the typed path, because a skill-tool
    # entry is the same flow with the same run driver and the run record must not depend on which door
    # it came through.
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

    _run_driver.stamp(target, _run_driver.resolve(payload))
    # EXACTLY ONE WINDOW. The read is a union but the write is one directory, so a marker left in
    # another base by an earlier flow (a Step-0 arm that landed in the agent dir because the Bash
    # tool saw no session id) would outlive it: `flow-window.sh close` only clears $FLOWDIR, so that
    # twin keeps both guards enforcing a dead command's report profile. Re-pointing the window means
    # re-pointing all of it.
    for base in bases:
        if base != target:
            for name in (FLOW_MARKER, _run_driver.MODE_MARKER):
                try:
                    (base / name).unlink()
                except OSError:
                    pass
    print(
        f"flow_autoarm: armed '{command}' (skill-tool entry — no typed command to arm it).",
        file=sys.stderr,
    )
