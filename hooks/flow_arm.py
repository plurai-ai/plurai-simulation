#!/usr/bin/env python3
"""Mechanical flow-window arming on command invocation (UserPromptSubmit hook).

The guards arm off ``.plurai_flow_active``. A skill's own Step-0 snippet can write it, but a model
that skipped that one Bash line would run the whole flow unguarded, so this hook writes the marker
when the USER types the command: arming is session-mechanical, not model-cooperative. The skills'
Step-0 snippet stays as the fallback for model-invoked flows and for Codex, which has no tool call
to hook for a skill, and it OVERWRITES too, unless the marker names a command that drives it as a
sub-step (``FLOW_DRIVERS`` below, the single definition of that relationship).

Arming does not depend on finding the agent. The window is keyed by session id, which needs no
search and therefore cannot miss. A search for ``$WORKDIR`` that gave up on a directory with no
``plurai_sim`` would leave a session driving an agent in another repo running with both guards off.

Only an explicit invocation at the start of the prompt arms, in the spelling the harness uses:
``/name`` and ``/plurai-simulation:name`` (Claude Code, Copilot CLI) or ``$name`` (Codex CLI). A prose
mention of a command name must not open a window, and neither must a block reason the harness hands
back as a prompt (kind ``reinjected``).

Fail-open and silent: the decision is always "none".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _harness.model import Decision, Event

FLOW_MARKER = ".plurai_flow_active"
# Written by flow_interrupt beside a window; cleared here so it can never outlive that window.
DROPPED = ".flow_dropped"

# THE RESIDUE SET: everything an abandoned flow can leave behind that would change the NEXT
# flow's behaviour, in lockstep with the `rm -f` list in ``scripts/flow-window.sh``'s arm branch
# (a test pins the two). Arming is what opens a new flow, so arming is what must clear them.
#
# The strike counter is the one with teeth: report_guard stands down at MAX_STRIKES and SWEEPS
# THE WINDOW, so an inherited `.report_guard_strikes` of 3 disarms BOTH guards for the whole of a
# flow that has not earned a single strike, silently. A surviving `.ask_sanctioned` inside its
# TTL is one free unearned ask; a surviving `.flow_pending`/`.flow_lease` pre-authorizes a turn
# end with dead work; a surviving `.flow_dropped` releases a heartbeat rescue into the new flow.
# A surviving `.run_driver_findings.jsonl` presents the PREVIOUS flow's unanswered prerequisites as
# this one's, the same class of error as a surviving strike counter, on the artifact a headless
# round is judged by. (`.run_driver` is deliberately NOT here: arming REWRITES it, below.)
RESIDUE_MARKERS = (
    ".ask_sanctioned",
    ".ask_pending",
    ".ask_deny_streak",
    ".ask_breaker_spent",
    ".report_guard_strikes",
    ".flow_pending",
    ".flow_lease",
    ".run_driver_findings.jsonl",
    DROPPED,
)


def sweep_residue(bases) -> None:
    """Clear the previous flow's residue from EVERY marker directory. Never raises.

    All bases, not just the one written to: the session dir and the agent dir are written by
    different halves of this plugin (see ``_workdir.marker_dirs``), so clearing only the write
    target strands a twin that the union readers (``report_guard``, ``flow_heartbeat``) still
    find. ``report_guard._sweep`` is the same idiom on the closing side.
    """
    for base in bases:
        for name in RESIDUE_MARKERS:
            try:
                (base / name).unlink()
            except OSError:
                pass


def open_evidence_epoch(command: str, target: Path) -> None:
    """Open the evidence epoch for a window a hook just STAMPED in ``target``, decided by its one
    owner, ``scripts/open_epoch.py``, and recorded there for the Step-0 arm to print. Never raises,
    never prints to stdout."""
    try:
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts not in sys.path:
            sys.path.append(scripts)
        from _workdir import resolve_workdir  # noqa: PLC0415
        from open_epoch import open_epoch, record  # noqa: PLC0415

        record(target, open_epoch(command, resolve_workdir(), target))
    except Exception as exc:  # noqa: BLE001 - arming is best-effort, but never silently
        print(f"WARNING({command}): the evidence epoch could not be opened ({exc}) — agent_findings.md was not pruned and the run driver record was not retired.", file=sys.stderr)


# THE driver table: one definition, three consumers.
#
#   driven command -> the commands that invoke it as an INNER SUB-STEP
#
# A driven skill must never clobber, re-target, or close its DRIVER's flow window: the
# driver's final hand-off is the deliverable, and the window has to survive every inner
# step to still be armed for it. Three layers act on that relationship:
#   1. ``scripts/flow-window.sh``'s own driver table, which every skill's Step-0
#      `flow-window.sh arm <command>` line runs;
#   2. ``report_guard``: no anchor re-targeting and no window close on a DRIVEN
#      command's anchor (it imports this table);
#   3. ``hooks/tests/test_flow_window_snippets.py``, which derives its expectations from
#      HERE and pins the shell half BEHAVIOURALLY (it runs each skill's Step-0 line and
#      asserts the window survives iff this table says so), so a fourth spelling cannot
#      drift in unnoticed.
# Evidence in the skills: /simulation-generate-suite drives /simulation-run (Step 3.1
# qualification run) and /analyze-simulation (Step 3.2 quality gate); /analyze-simulation
# drives /simulation-run (loop step 5, the re-run for fresh artifacts). Nothing drives the
# other four. (/simulation-fixes --verify re-runs the RUNNER CLI directly, never the
# simulation-run skill, so its Step 0 never fires under fixes.)
FLOW_DRIVERS: dict[str, tuple[str, ...]] = {
    "run": ("generate-suite", "analyze"),
    "analyze": ("generate-suite",),
}


def drives(command: str) -> tuple[str, ...]:
    """The commands ``command`` invokes as inner sub-steps (inverse of FLOW_DRIVERS)."""
    return tuple(driven for driven, drivers in FLOW_DRIVERS.items() if command in drivers)


# Command -> marker content (the report_guard/_ANCHORS profile keys). PUBLIC: it is also THE table
# ``flow_autoarm`` resolves a skill-tool invocation against, because the same six flows can be
# entered either way and a second copy of the names is exactly the drift the FLOW_DRIVERS
# lockstep tests exist to prevent.
COMMANDS = {
    "simulation-generate-suite": "generate-suite",
    "simulation-scan-agent": "scan-agent",
    "simulation-run": "run",
    "simulation-fixes": "fixes",
    "analyze-simulation": "analyze",
    "analyze-traces": "traces",
    # The end-to-end sequencer. Its window covers only its start questions: each stage it invokes
    # arms that stage's own window, so it is deliberately in NO FLOW_DRIVERS entry.
    "simulate": "simulate",
}
#: The skills that are not flows but start backend jobs (their calls carry the command run id): each opens a
#: command scope when it starts, which is a flow window without the guards (``open_command_scope``). A skill
#: invoked while a flow window stands is a sub-step of that flow and shares its id. A test reads the skills'
#: own text and fails when a skill that starts a job is in neither this table nor ``COMMANDS``.
SCOPED_COMMANDS: tuple[str, ...] = (
    "build-rag-corpus",
    "edit-evals",
    "edit-scenarios",
    "extract-scenario-variables",
    "generate-evals",
    "generate-mock-env",
    "generate-scenarios",
    "ground-scenarios",
    "manage-envs",
    "optimize-eval",
    "refine-scenarios",
    "trace-to-scenarios",
    "upload-doc-examples",
    "upload-scenario-bundle",
)


def open_command_scope(command: str, target: Path, *, typed: bool) -> None:
    """Open the command scope of a non-flow skill that starts jobs, through its one owner, ``scripts/open_epoch.py``.
    Never raises, never prints to stdout."""
    try:
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts not in sys.path:
            sys.path.append(scripts)
        from open_epoch import open_scope  # noqa: PLC0415

        open_scope(command, target, typed=typed)
    except Exception as exc:  # noqa: BLE001 - best-effort, but never silently
        print(f"WARNING({command}): its command scope could not be opened ({exc}) — its backend jobs will not be grouped.", file=sys.stderr)


# The three spellings of an invocation: ``/name`` and ``/plurai-simulation:name`` on the harnesses
# that take slash commands, ``$name`` on Codex. Anchored at the start of the prompt: a name anywhere
# else is prose.
_INVOKE_RE = re.compile(
    r"^\s*(?:/(?:plurai-simulation:)?|\$)(" + "|".join(map(re.escape, COMMANDS)) + r")\b",
)

#: The ``/simulate`` run's plan file (``_run_driver.PLAN_MARKER``), named here for the clear below.
SIMULATE_PLAN = ".plurai_simulate.json"
#: The one skill whose typed invocation does NOT end a ``/simulate`` run's autonomy.
SIMULATE_SKILL = "simulate"


def plugin_skills() -> tuple[str, ...]:
    """Every skill this plugin ships: each ``skills/*/SKILL.md`` directory name and its ``name:``
    frontmatter, discovered by reading the front matter of each. Longest first, so an
    alternation built from it never stops at a shorter name's prefix."""
    names: set[str] = set()
    for path in (Path(__file__).resolve().parent.parent / "skills").glob("*/SKILL.md"):
        names.add(path.parent.name)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                key, sep, value = line.partition(":")
                if sep and key.strip() == "name" and value.strip():
                    names.add(value.strip().strip("'\""))
    return tuple(sorted(names, key=lambda n: (-len(n), n)))


#: What Copilot hands its ``UserPromptSubmit`` hook for a skill the user invoked: the skill's expansion, in place of
#: the typed command. The quoted name is the command; the expansion carries no arguments.
_EXPANDED_SKILL_RE = re.compile(r'\s*The user explicitly invoked the "([/$][A-Za-z0-9_][A-Za-z0-9_.:-]*)" skill\.')


def typed_text(prompt: str) -> str:
    """What the user typed: the command a Copilot skill expansion stands for, else ``prompt`` as it is.

    Every reader of a prompt (arming, the plan clear, the user-turn record, the
    interjection test) judges this text, so an expansion counts as the typed command everywhere, never as prose.
    """
    match = _EXPANDED_SKILL_RE.match(prompt)
    return match.group(1) if match else prompt


def typed_plugin_skill(prompt: str) -> str | None:
    """The bare name of the plugin skill ``prompt`` invokes as a typed slash command, or None.

    Bare or ``plurai-simulation:``-qualified, or Codex's ``$name``, at the start of the prompt only. A
    built-in command (``/compact``, ``/model``) and plain text (feedback) are None.
    """
    names = plugin_skills()
    if not names:
        return None
    m = re.match(r"^\s*(?:/(?:plurai-simulation:)?|\$)(" + "|".join(map(re.escape, names)) + r")(?![\w-])", typed_text(prompt))
    return m.group(1) if m else None


def clear_simulate_plan(payload: dict, prompt: str) -> None:
    """End a ``/simulate`` run's autonomy when the user TYPES another of this plugin's skills.

    A step the user calls directly runs interactively, as it always has. Only a typed plugin skill
    clears it: a built-in command (``/compact`` above all) and a plain-text message — feedback, a
    side question, a pasted credential — leave the run as it is. ``/simulate`` itself rewrites the
    plan at its own start. Never raises.
    """
    try:
        name = typed_plugin_skill(prompt)
        if name is None or name == SIMULATE_SKILL:
            return
        from _workdir import marker_dirs  # noqa: PLC0415 - hooks/ is on sys.path at call time

        for base in marker_dirs(payload):
            try:
                (base / SIMULATE_PLAN).unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001 - clearing is best-effort; never affect the prompt
        pass


def stop_waiting(event: Event, prompt: str, bases: list[Path]) -> None:
    """A person typed: the overview page no longer says the flow waits for their answer (``simulate_continue.wait_page``).
    A plain-text answer, a follow-up to a dismissed question, a Codex reply, a typed plugin command — not a heartbeat,
    a harness block or anything else no person typed. ``bases`` is ``_workdir.marker_dirs``; a page that shows no wait
    (``_workdir.page_may_wait``) costs a few file reads and no import, which is every prompt but the answer. Never raises."""
    try:
        from _workdir import flow_dir, page_may_wait  # noqa: PLC0415 - hooks/ is on sys.path at call time

        if not page_may_wait(bases):
            return
        from report_guard import human_typed  # noqa: PLC0415

        if not (human_typed(prompt) or typed_plugin_skill(prompt)):
            return
        from simulate_continue import wait_page  # noqa: PLC0415

        session = flow_dir({"session_id": event.session_id})
        wait_page(event, bases, False, workdir=next((base for base in bases if base != session), None))
    except Exception:  # noqa: BLE001 - the next fill rewrites the step anyway; never affect the prompt
        pass


def _marker_dirs(event: Event) -> list[Path]:
    """``_workdir.marker_dirs`` for this prompt, once: it runs the workdir cascade. [] when it cannot resolve."""
    try:
        from _workdir import marker_dirs  # noqa: PLC0415 - hooks/ is on sys.path at call time

        return marker_dirs({"session_id": event.session_id})
    except Exception:  # noqa: BLE001 - nothing to record into, nothing to clear
        return []


def handle(event: Event) -> Decision:
    """Arm the window for a typed flow command. Never a decision, never an error."""
    # Imported here so the tables above stay importable without the codec package beside them.
    from _harness import model

    lifted = None
    try:
        lifted = _arm(event)
    except Exception:  # noqa: BLE001 - arming is best-effort; it must never affect the prompt
        pass
    return model.context(lifted) if lifted else model.none()


def command_dir(payload: dict) -> Path | None:
    """Where a command's window and run id live: the session's flow directory, else (a session with no id) the agent's.

    The agent directory is never created here: a mkdir on an ambiguous layout materializes a phantom ``plurai_sim/``.
    """
    from _workdir import flow_dir, resolve_workdir  # noqa: PLC0415

    target = flow_dir(payload)
    if target is None:
        target = resolve_workdir()
        if target is None or not target.is_dir():
            return None
    return target


def _scope(command: str, payload: dict) -> None:
    target = command_dir(payload)
    if target is not None:
        open_command_scope(command, target, typed=True)


def _arm(event: Event) -> str | None:
    """Arm the window for a typed command; returns what a message that lifted a halt tells the model (``lift_context``)."""
    if event.kind == "reinjected":
        return None  # the plugin's own block reason coming back as a prompt, not something the user typed
    prompt = typed_text(str(event.raw.get("prompt") or ""))
    clear_simulate_plan({"session_id": event.session_id}, prompt)
    bases = _marker_dirs(event)
    lifted = None
    try:
        from simulate_continue import record_user_turn  # noqa: PLC0415 - hooks/ is on sys.path at call time

        lifted = record_user_turn(bases, prompt)  # what a message that lifted a capacity or credit halt tells the model
    except Exception:  # noqa: BLE001 - recording is best-effort; never affect the prompt
        pass
    stop_waiting(event, prompt, bases)
    match = _INVOKE_RE.match(prompt)
    if match is None:
        scoped = typed_plugin_skill(prompt)
        if scoped in SCOPED_COMMANDS:
            _scope(scoped, {"session_id": event.session_id})
        return lifted
    from _workdir import find_marker, flow_dir, marker_dirs, resolve_workdir

    command = COMMANDS[match.group(1)]
    # The flow directory is keyed by the id the codec decoded, since a harness may spell the field
    # differently; the environment is read only when the payload has none.
    payload = {"session_id": event.session_id}
    bases = marker_dirs(payload)
    # Read BEFORE the stamp: a re-type over its own window, or over its driver's, is a resume
    # of that build's evidence, as the script's same-name and driven arms are.
    existing = find_marker(FLOW_MARKER, bases)
    try:
        current = existing.read_text(encoding="utf-8", errors="replace").strip() if existing else ""
    except OSError:
        current = command
    target = flow_dir(payload)
    if target is None:
        # No session id: fall back to the agent directory, which is where the skills' snippets also
        # land without one. Never create it: a mkdir on an ambiguous multi-agent layout
        # materializes a phantom <repo>/plurai_sim/ that permanently poisons the nearest-ancestor
        # rule. Unarmable here, so the skill's Step-0 snippet arms.
        target = resolve_workdir()
        if target is None or not target.is_dir():
            return
    # An explicit user invocation OVERWRITES UNCONDITIONALLY, with no driver exemption: the user
    # typing a command is re-pointing the window deliberately, so even a driver's marker yields (the
    # skills' Step-0 snippet overwrites too, but keeps its driver's name; only the user can
    # re-point out of a driven sub-step).
    #
    # The file holds the command name and nothing else. The skill-side consumer
    # (`scripts/flow-window.sh`) compares the WHOLE file, so a second line makes every arm read the
    # window as foreign: it would re-stamp a driver's window and, for the findings-owning commands,
    # present a previous build's findings as this run's. The hook-side readers are equally
    # unprepared for it.
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / FLOW_MARKER).write_text(command, encoding="utf-8")
        # The previous flow's residue, cleared across every base exactly as `flow-window.sh arm`
        # clears it: a typed command that inherited an abandoned flow's strike counter would stand
        # report_guard down and sweep the window, disarming BOTH guards for a flow that has not
        # earned a strike.
        sweep_residue(bases)
        if current != command and current not in FLOW_DRIVERS.get(command, ()):
            open_evidence_epoch(command, target)
        # WHO armed this flow, recorded beside the window: the run's own record of its run driver
        # mode, read by skills/simulation-run/scripts/build_run_json.py (its one reader;
        # `scripts/headless_driver.py` touches the FINDINGS file and the env alias, never the
        # stamp). A SEPARATE file because the window holds one command name and is compared
        # whole-file (see the note above).
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path at call time

        _run_driver.stamp(target, _run_driver.resolve(payload))
    except OSError:
        pass  # unarmable layout degrades to the skill-side fallback, never an error
