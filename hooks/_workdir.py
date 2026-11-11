"""Marker-location resolution shared by the flow-fidelity hooks (ask_guard / report_guard).

TWO resolvers, because the markers are two different kinds of state:

* ``flow_dir(session_id)`` — where the flow window / sanction / strike counter live. Keyed by
  SESSION, mirroring ``scripts/resolve-workdir.sh``'s ``$FLOWDIR``. This is the one enforcement
  reads, and it involves no filesystem search at all.
* ``resolve_workdir(start)`` — the AGENT's directory, below. Still correct for the agent's own
  state, and kept as the legacy/fallback read for the markers (a session with no id, or a
  marker left in an agent directory by an older plugin).

Why the split: the two sides of this plugin would otherwise derive the marker location
independently — the skills from the Bash tool's cwd, the hooks from the session's cwd. When the
agent under test lives outside the session's repo those disagree, the hooks find no marker, and
both guards allow everything for the whole run without a word. Session keying removes the
disagreement rather than making the search cleverer; no search can find an agent in another repo.

A Python port of ``scripts/resolve-workdir.sh``'s anchor cascade, kept in lockstep with it
(the shell script is the skills' canonical resolver; this module exists because hooks are
Python processes that cannot source it):

1. walk UP from ``start`` for the nearest directory containing ``plurai_sim/``;
2. else, if the git toplevel contains exactly ONE ``plurai_sim/`` within 3 levels AND that match
   sits at or below the agent scope (``$PLURAI_AGENT_PATH`` or ``start``), use its parent (the
   documented nested-agent monorepo layout);
3. else fall back to the git toplevel (or ``start`` outside a repo).

Step 2's SCOPE TEST is the one place the two resolvers differ in POSTURE, not in verdict: the
shell REFUSES a sole match outside the scope (it belongs to another agent, and adopting it points
the whole run at the wrong workdir), while this port must never crash a hook — so it simply does
not adopt it and falls through to step 3.

Then the named-env layout: ``plurai_sim/envs/$(cat .active-env)`` when both exist, else the
bare ``plurai_sim/``. Every step is best-effort — resolution failure returns None and the
caller treats it as "no flow markers found" (both guards are fail-open on this path).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

# Keep in lockstep with scripts/resolve-workdir.sh's $FLOWDIR.
#
# NOT under ~/.claude — deliberately. The auto-mode permission classifier treats the agent's own
# configuration tree as self-modification territory, so any marker write there that reaches it as
# a raw shell command pattern-matches its built-in "auto-mode bypass" rule and is denied. Flow
# state is plugin RUNTIME state, not Claude configuration; housing it outside ~/.claude removes
# that signal for every write, scripted or improvised. (The observed field failure: ask_guard's
# old teach text had the model `echo` a gate name into ~/.claude/plurai/flow/…, the classifier
# denied it as self-modification, and the flow deadlocked — every recovery was itself denied.)
def flow_root() -> Path:
    """``~/.plurai/flow``, where ``~`` is ``HOME`` on every platform, as ``resolve-workdir.sh`` reads it, else
    ``Path.home()``: the rule ``_platform.home`` states, restated here so this module imports nothing of the plugin's.
    With no home at all (Windows under ``env -i``), an unresolved ``~`` under which nothing is found, so importing
    never fails a hook."""
    home = os.environ.get("HOME", "").strip()
    try:
        return (Path(home) if home else Path.home()) / ".plurai" / "flow"
    except RuntimeError:
        return Path("~") / ".plurai" / "flow"


FLOW_ROOT = flow_root()

#: Written into a session's flow directory by the SessionStart hook, holding the name of the harness
#: that ran it. Its presence is the proof Step 0's readiness check reads that the plugin's hooks run
#: in this session, and its content says which harness's hooks they are.
HOOKS_READY_MARKER = ".hooks_ready"

#: Written into a session's flow directory by Step 0's readiness check (never by a hook) when it found no
#: ``HOOKS_READY_MARKER`` on Claude Code and let the flow run in limited mode, holding the warning it showed. Its
#: presence, with no ``HOOKS_READY_MARKER`` beside it, is what turns on the limited-mode branches (:func:`limited_mode`);
#: the check removes it once it finds the hooks running.
HOOKS_DEGRADED_MARKER = ".hooks_degraded"


#: The variables a harness exports its own session id in, innermost harness first. A child process
#: inherits every variable its ancestors set, so Copilot started from a Claude Code shell sees both
#: its own and Claude Code's; each harness sets its own variable fresh and a plain Claude Code
#: session sets neither of the others, so the first one set belongs to the innermost harness. Keep in
#: lockstep with ``scripts/resolve-workdir.sh``.
#:
#: Codex exports two. ``CODEX_SESSION_ID`` is the id its hooks carry in every payload, a sub-agent's
#: included. ``CODEX_THREAD_ID`` is the id of the thread the shell runs in, which for a sub-agent is its
#: own thread and not the session its hooks key their markers and pin under. So the session variable
#: leads, and the thread variable answers only where a build does not export the first.
#:
#: Copilot's variable is the shell's own id. In a sub-agent that is neither the id the sub-agent's hooks
#: carry nor its parent's, so a sub-agent's shell finds no markers and no pin, and every reader of the
#: pin then allows loopback only.
SESSION_VARS = ("COPILOT_AGENT_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID")

#: The harness each variable of ``SESSION_VARS`` belongs to.
HARNESS_OF_SESSION_VAR = {
    "COPILOT_AGENT_SESSION_ID": "copilot",
    "CODEX_SESSION_ID": "codex",
    "CODEX_THREAD_ID": "codex",
    "CLAUDE_CODE_SESSION_ID": "claude",
}


def harness(env: Mapping[str, str] | None = None) -> str:
    """The harness that owns this shell, by the session variable it exports (the order of ``SESSION_VARS``
    makes the innermost win); ``claude`` when none does, which is Claude Code or a shell outside every harness."""
    env = os.environ if env is None else env
    for var in SESSION_VARS:
        if str(env.get(var, "")).strip():
            return HARNESS_OF_SESSION_VAR[var]
    return "claude"


def limited_mode(bases: list[Path], env: Mapping[str, str] | None = None) -> bool:
    """Whether this session runs in limited mode (``docs/limited-mode.md``): Claude Code, the readiness check's
    :data:`HOOKS_DEGRADED_MARKER` in the flow directory (``bases[0]``), and no :data:`HOOKS_READY_MARKER` beside it.
    Never raises; anything unreadable is "no", so every limited-mode branch stays closed.

    Only the flow directory's ``.hooks_ready`` counts, the one rule the readiness check and ``flow-window.sh``
    (``_hooks_marked``) read too: the SessionStart hook writes its marker there, and a shell that exports no session id
    has the workdir as its flow directory. A marker in a later base (the workdir beside a session-keyed flow directory)
    may be one an earlier hooked session left, and counting it would turn limited mode off here while ``ready`` turned
    it on."""
    try:
        if harness(env) != "claude" or not bases or not (bases[0] / HOOKS_DEGRADED_MARKER).is_file():
            return False
        return not (bases[0] / HOOKS_READY_MARKER).is_file()
    except Exception:  # noqa: BLE001 - not limited
        return False


_MAX_ANCESTORS = 12


def sessions_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where Claude Code records each running session as ``<pid>.json`` (``sessionId`` inside).

    Claude Code keeps its state in ``$CLAUDE_CONFIG_DIR`` when that is set and non-empty, else in ``~/.claude``;
    ``scripts/dev/bootstrap.sh`` resolves it the same way. The default sits under the home ``flow_root`` resolved
    (Claude Code's ``~`` follows ``HOME`` too), so importing this module needs no ``USERPROFILE`` on Windows."""
    env = os.environ if env is None else env
    config = str(env.get("CLAUDE_CONFIG_DIR", "")).strip()
    return (Path(config) if config else flow_root().parents[1] / ".claude") / "sessions"


def own_session() -> tuple[str, str]:
    """``(pid, session id)`` of the Claude Code process this PROCESS runs under, or ``("", "")``; see ``own_session_id``."""
    try:
        pid = os.getppid()
    except OSError:
        return "", ""
    for _ in range(_MAX_ANCESTORS):
        if pid <= 1:
            return "", ""
        try:
            import json  # noqa: PLC0415 - only this path needs it

            sid = json.loads((sessions_dir() / f"{pid}.json").read_text(encoding="utf-8")).get("sessionId")
            if isinstance(sid, str) and sid.strip():
                return str(pid), sid.strip()
        except (OSError, ValueError, AttributeError):
            pass
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2, check=False).stdout
            pid = int(out.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            return "", ""
    return "", ""


def own_session_id() -> str:
    """The id of the Claude Code session this PROCESS runs under, read from the process tree; "" if unknown.

    ``$CLAUDE_CODE_SESSION_ID`` is inherited: a ``claude`` started from another session's Bash tool
    (a fleet operator, a script run from Claude) sees the PARENT's id in its own tool shells, while its
    hooks get its own id from their payload. The skill side then wrote its flow markers into the
    parent's folder and the parent's Stop hook enforced a flow it never ran (RT37 fleet). The nearest
    ancestor with a session record is the session that owns this shell, so it wins over the variable.
    Best-effort and silent: any failure answers "" and the caller keeps the variable."""
    return own_session()[1]


def _recorded(pid: str = "", sid: str = "") -> bool:
    """Whether ``pid`` / ``sid`` names a session Claude Code has a record of (a real, running one)."""
    try:
        if pid:
            return (sessions_dir() / f"{pid}.json").is_file()
        import json  # noqa: PLC0415

        for f in sessions_dir().glob("*.json"):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("sessionId") == sid:
                    return True
            except (OSError, ValueError, AttributeError):
                continue
    except OSError:
        pass
    return False


def corrected_session(env_sid: str, env_pid: str) -> tuple[str, str]:
    """``(pid, sid)`` to use INSTEAD of the inherited variables, ``""`` where a variable stands.

    Only an INHERITED value is replaced: one that names another running session (a record exists for
    it) while the process tree says this shell belongs to a different one. A value no record knows —
    a test's made-up id, an older Claude Code — is left exactly as it is."""
    own_pid, own_sid = own_session()
    if not own_sid:
        return "", ""
    sid = own_sid if env_sid and env_sid != own_sid and _recorded(sid=env_sid) else ""
    pid = own_pid if env_pid and own_pid and env_pid != own_pid and _recorded(pid=env_pid) else ""
    return pid, sid


def session_id(payload: dict | None = None) -> str:
    """This session's id: the hook payload's, else the environment's — corrected when it was inherited.

    The payload carries ``session_id`` on every event of every harness, and the variable ``SESSION_VARS``
    names for that harness equals it, which is what the SKILLS' shell snippets key on, so the two sides
    agree. The exception is a Copilot sub-agent, whose hooks and shell each carry an id of their own: its
    shell resolves a session no hook keyed anything under. A script has no payload and reads the variable.
    ``CLAUDE_CODE_SESSION_ID`` can be a PARENT session's (``own_session_id``); ``corrected_session`` swaps
    that case only.
    """
    from_payload = (payload or {}).get("session_id")
    if from_payload:
        return str(from_payload).strip()
    for var in SESSION_VARS:
        value = os.environ.get(var, "").strip()
        if var == "CLAUDE_CODE_SESSION_ID" and value:
            value = corrected_session(value, "")[1] or value
        if value:
            return value
    return ""


def flow_dir(payload: dict | None = None) -> Path | None:
    """Where this SESSION's flow markers live, or None when there is no session id.

    Never creates the directory: a reader must not manufacture the thing it is testing for, and
    the writers (resolve-workdir.sh, flow_arm) mkdir it themselves.
    """
    sid = session_id(payload)
    if not sid or "/" in sid or sid in (".", ".."):
        return None  # a path-shaped id would escape the flow root
    return FLOW_ROOT / sid


def marker_dirs(payload: dict | None = None) -> list[Path]:
    """Every directory that may hold this session's flow markers, most authoritative first.

    Call this ONCE per hook invocation and pass the result to ``find_marker``: the fallback entry
    runs the full cwd cascade (a ``git rev-parse`` subprocess plus a depth-3 glob), which is not
    something to repeat per marker lookup.

    The session directory is the contract; the cwd-resolved agent directory is the FALLBACK, kept
    permanently rather than as a migration step. The reason is an ASYMMETRY between the two sides,
    not the absence of a session id on this one: a hook always has ``session_id`` from its payload,
    while a skill's Bash snippet only sees ``$CLAUDE_CODE_SESSION_ID`` if the harness exports it
    into the tool environment (it does today, but that is an undocumented implementation detail).
    In that asymmetric state ``flow_arm`` arms the session directory while the skill writes its
    ``.ask_sanctioned`` to ``$WORKDIR`` — and a guard reading only the session directory would then
    DENY a gate the flow's own script authorized, permanently, with no way for the model to recover.
    That is the fail-closed loop this second entry buys off.

    (Note what does NOT justify it: a hook with no session id at all fails OPEN, not closed —
    ``flow_dir`` is None, so the WINDOW is not found either and the guard returns before it ever
    looks for a sanction.)
    """
    out: list[Path] = []
    flow = flow_dir(payload)
    if flow is not None:
        out.append(flow)
    legacy = resolve_workdir()
    if legacy is not None and legacy not in out:
        out.append(legacy)
    return out


def find_marker(name: str, bases: list[Path]) -> Path | None:
    """The first existing ``name`` across ``bases`` (from ``marker_dirs``), or None."""
    return next(iter(find_markers(name, bases)), None)


def find_markers(name: str, bases: list[Path]) -> list[Path]:
    """EVERY existing ``name`` across ``bases``, most authoritative first.

    First-hit-wins is right for a marker whose mere existence is the answer (the window, the
    sanction). It is wrong the moment the marker's CONTENT or age decides — a session-armed
    directory and a fallback-armed one are written independently, so the first hit can be the
    stale copy while the live one sits in the other. Readers of that kind take the union.
    """
    out: list[Path] = []
    for base in bases:
        candidate = base / name
        try:
            if candidate.is_file():
                out.append(candidate)
        except OSError:
            continue
    return out


#: The overview page's running state (``scripts/publish_report.py`` ``PROGRESS_FILE``), the key its ``wait_progress``
#: writes while a question waits for the user (``WAIT_KEY``, quoted as JSON spells it) and the /simulate plan, whose
#: workdir may lie elsewhere. Named here so a hook can tell "nothing waits" without importing the writer; a test pins
#: them to publish_report's.
PROGRESS_FILE = "flow_progress.json"
WAIT_MARK = '"waiting"'
PLAN_FILE = ".plurai_simulate.json"


def page_may_wait(bases: list[Path]) -> bool:
    """Whether the overview page these directories lead to may say it waits for the user's answer: a ``flow_progress.json``
    in a base (the flow dir's copy) or in its ``artifacts/`` carries the wait key, or a base holds a /simulate plan (its
    workdir is the plan's to name). Reads a few small files and imports nothing, so the hooks that end a wait pay
    nothing more when none is shown. Never raises; an unreadable file reads as no wait."""
    for base in bases:
        try:
            if (base / PLAN_FILE).is_file():
                return True
        except OSError:
            pass
        for path in (base / PROGRESS_FILE, base / "artifacts" / PROGRESS_FILE):
            try:
                if WAIT_MARK in path.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                continue
    return False


def _git_toplevel(start: Path) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        text = out.stdout.decode().strip()
        if out.returncode == 0 and text:
            return Path(text)
    except Exception:
        pass
    return start


def _within(candidate: Path, ancestor: Path) -> bool:
    """``candidate`` is ``ancestor`` or lives under it — resolve-workdir.sh's `_plurai_within`."""
    return candidate == ancestor or ancestor in candidate.parents


def _named_agent() -> str:
    """The agent the operator NAMED, from the environment or the flow dir.

    The env var is exported inside a Bash tool call. Hooks run in Claude Code's own process and
    never see it, so reading only the environment made this scope unreachable through the
    mechanism the skills document. `resolve-workdir.sh` therefore also persists it to
    ``$FLOWDIR/.agent_path`` — session-keyed, which is the one location both halves observe
    identically. The env var still WINS when set: a hook invoked with it
    present is being told something more current than a file from an earlier turn.
    """
    named = (os.environ.get("PLURAI_AGENT_PATH") or "").strip()
    if named:
        return named
    # The SESSION dir only, resolved from the environment — never `marker_dirs`, whose fallback
    # entry calls back into this cascade. No session id means no file to read, not a scan of
    # other sessions' directories.
    flow = flow_dir()
    if flow is not None:
        try:
            return (flow / ".agent_path").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
    return ""


def _agent_scope(probe: Path) -> Path:
    """The subtree a repo-wide sole match may come from — the NAMED agent, else the cwd.

    An agent named by its module file (``/simulate assistant/agent.py`` exports ``PLURAI_AGENT_PATH=assistant/agent.py``)
    is the directory that holds it: a file has no subtree, and scoping to it adopted nothing but the repo root."""
    named = _named_agent()
    if not named:
        return probe
    try:
        import _platform  # noqa: PLC0415 - hooks/ is on sys.path wherever this module is

        # Anchored on the cwd, as `resolve()` reads it: a relative name (`assistant/agent.py`) is no rootless path to refuse.
        named_path = Path(named).expanduser().absolute()
        # A file a command can write names it: on Windows never followed to another host (UNC).
        if not _platform.is_local_drive_path(named_path, within=(probe,)):
            return probe
        resolved = named_path.resolve()
        return resolved.parent if resolved.is_file() else resolved
    except Exception:
        return probe


def _plurai_root(start: Path) -> Path:
    probe = start.resolve()
    scoped = bool(_named_agent())
    scope = _agent_scope(probe)
    for candidate in (probe, *probe.parents):
        if (candidate / "plurai_sim").is_dir():
            # resolve-workdir.sh step 1.5: with the scope set EXPLICITLY, an ancestor belonging
            # to another agent is not this session's workdir. Where the shell REFUSES, this
            # falls through to the glob/toplevel — hooks fail open, exactly as at step 2.
            if scoped and not _within(candidate, scope):
                break
            return candidate
    top = _git_toplevel(start)
    try:
        # Depths 1-3, matching resolve-workdir.sh's `find -maxdepth 3` exactly — a depth
        # divergence changes the AMBIGUITY verdict (which root wins when several exist).
        matches = [
            p
            for pattern in ("plurai_sim", "*/plurai_sim", "*/*/plurai_sim")
            for p in top.glob(pattern)
            if p.is_dir() and "node_modules" not in p.parts and ".git" not in p.parts
        ]
        if len(matches) == 1:
            # SCOPED exactly like resolve-workdir.sh step 2: a sole match belonging to a
            # DIFFERENT agent (not at or below the scope) is not this session's workdir, and
            # adopting it sends the hooks looking for flow markers in someone else's directory.
            # Where the shell refuses, this falls through to the git toplevel — hooks fail open.
            parent = matches[0].parent.resolve()
            if _within(parent, _agent_scope(probe)) or parent == probe:
                return matches[0].parent
    except Exception:
        pass
    return top


def resolve_workdir(start: Path | None = None) -> Path | None:
    """The effective ``$WORKDIR`` (a.k.a. ``$PLURAI_DIR``), or None when unresolvable."""
    try:
        root = _plurai_root(start or Path.cwd())
        sim = root / "plurai_sim"
        envs = sim / "envs"
        active = sim / ".active-env"
        if envs.is_dir() and active.is_file():
            name = active.read_text(encoding="utf-8", errors="replace").strip()
            if name:
                import _platform  # noqa: PLC0415 - hooks/ is on sys.path wherever this module is

                # `.active-env` is a file a command can write; on Windows `envs / "//host/share"` is a UNC path.
                return envs / name if _platform.is_local_drive_path(envs / name, within=(sim,)) else None
        return sim
    except Exception:
        return None


if __name__ == "__main__":  # `python3 _workdir.py session-id` — the shell side's way to the same answer
    import sys

    if sys.argv[1:] == ["session-id"]:
        print(own_session_id())
    elif sys.argv[1:] == ["corrected"]:
        print(" ".join(corrected_session(os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip(), os.environ.get("CLAUDE_PID", "").strip())))
