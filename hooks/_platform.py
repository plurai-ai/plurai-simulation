"""The few places the plugin's Python must know it runs on native Windows.

``WINDOWS`` is the one switch. Read it as ``_platform.WINDOWS`` at call time, never ``from _platform import WINDOWS``
(which copies the value at import): a test on Linux sets ``_platform.WINDOWS = True`` to take the Windows branch
without touching ``os.name``. On macOS and Linux every answer is what the plugin did before this module existed.

Stdlib only, importable from hooks (which run under the oldest ``python3`` the host resolves) and scripts.
"""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PureWindowsPath

#: Whether this process runs on native Windows. Tests patch this attribute; read it at call time.
WINDOWS = os.name == "nt"


def home() -> Path:
    """The user's home as every part of the plugin reads it: ``HOME`` when it is set, as the shell scripts do, else
    ``Path.home()`` (on Windows, ``USERPROFILE``; Git Bash, Claude Code and Copilot all take ``HOME`` first too). With
    no home at all (Windows under ``env -i`` with neither), an unresolved ``~``, under which nothing is found. The
    same rule as ``_workdir.flow_root``, which restates it to import nothing of the plugin's."""
    named = os.environ.get("HOME", "").strip()
    try:
        return Path(named) if named else Path.home()
    except RuntimeError:
        return Path("~")


def _is_wsl_launcher(path: str, env: Mapping[str, str]) -> bool:
    """``%SystemRoot%\\System32\\bash.exe`` (and its ``Sysnative`` alias) start WSL, not Git Bash."""
    folder = ntpath.normcase(ntpath.dirname(ntpath.normpath(path)))
    root = ntpath.normcase(ntpath.normpath(env.get("SystemRoot") or env.get("SYSTEMROOT") or "C:\\Windows"))
    return folder in (ntpath.join(root, "system32"), ntpath.join(root, "sysnative")) or folder.endswith(("\\system32", "\\sysnative"))


def _usable(path: str, env: Mapping[str, str]) -> bool:
    return bool(path) and os.path.isabs(path) and os.path.isfile(path) and not _is_wsl_launcher(path, env)


def _git_on_path(env: Mapping[str, str]) -> str:
    """The first absolute ``git.exe`` on ``PATH``. Relative entries are skipped: Windows' own search tries
    the current directory first, which would let a planted ``git.exe`` name the shell."""
    for entry in (env.get("PATH") or env.get("Path") or "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        candidate = os.path.join(entry, "git.exe")
        if os.path.isfile(candidate):
            return candidate
    return ""


def git_bash(env: Mapping[str, str] | None = None) -> str | None:
    """The ``bash`` a subprocess should run.

    Not Windows: ``"bash"``, resolved through ``PATH`` as it always was. Windows: the absolute path of Git
    Bash, or ``None`` when none is found. A bare ``bash`` there follows the CreateProcess search order, which
    reaches ``System32\\bash.exe`` (the WSL launcher) before ``PATH``. The order is
    ``CLAUDE_CODE_GIT_BASH_PATH``, then the Git install that the first ``git.exe`` on ``PATH`` belongs to
    (``<git dir>\\..\\bin\\bash.exe``), then ``%ProgramFiles%\\Git\\bin\\bash.exe``; the WSL launcher is never
    returned.
    """
    if not WINDOWS:
        return "bash"
    source = os.environ if env is None else env
    named = (source.get("CLAUDE_CODE_GIT_BASH_PATH") or "").strip().strip('"')
    if _usable(named, source):
        return named
    git = _git_on_path(source)
    if git:
        folder = os.path.dirname(git)
        for candidate in (os.path.join(folder, "..", "bin", "bash.exe"), os.path.join(folder, "..", "..", "bin", "bash.exe")):
            candidate = os.path.normpath(candidate)
            if _usable(candidate, source):
                return candidate
    program_files = (source.get("ProgramFiles") or source.get("PROGRAMFILES") or "").strip()
    if program_files:
        candidate = os.path.join(program_files, "Git", "bin", "bash.exe")
        if _usable(candidate, source):
            return candidate
    return None


def is_local_drive_path(path: object, within: Iterable[object] = ()) -> bool:
    """Whether a filesystem call on ``path`` stays on this machine's own disks. Ask it BEFORE ``exists``/``is_file``/
    ``stat``/``open`` on any path a model, a tool input or a transcript supplied.

    Off Windows: always True (nothing changes). On Windows: the path is rooted on a letter drive (``C:\\...``). A UNC
    path (``\\\\host\\share``, ``//host/share``), a device path (``\\\\?\\``, ``\\\\.\\``) and a rootless or drive-relative
    one are not: touching a UNC path opens an SMB/WebDAV connection with the user's credentials, so a link a model
    writes would leak the NTLM hash. The one exception is a path that lies, lexically (no filesystem call), inside one
    of ``within``, the session's own roots: a project on a network share is already on that host.
    """
    if not WINDOWS:
        return True
    target = PureWindowsPath(os.fspath(path))  # type: ignore[arg-type]
    if re.fullmatch(r"[A-Za-z]:", target.drive) and target.root:
        return True
    spelled = ntpath.normcase(ntpath.normpath(str(target)))
    for root in within:
        base = PureWindowsPath(os.fspath(root))  # type: ignore[arg-type]
        if not base.drive or not base.root:
            continue
        prefix = ntpath.normcase(ntpath.normpath(str(base))).rstrip("\\")
        if spelled == prefix or spelled.startswith(prefix + "\\"):
            return True
    return False


def cwd_roots() -> tuple:
    """``within`` for ``is_local_drive_path`` from the working directory: ``(cwd,)`` on Windows (``()`` when it cannot
    be read), and ``()`` elsewhere without touching the cwd, so a deleted cwd changes nothing off Windows."""
    if not WINDOWS:
        return ()
    try:
        return (Path.cwd(),)
    except OSError:
        return ()


GIT_BASH_MISSING = "Git Bash was not found (set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe, or install Git for Windows)"


def venv_exe(venv: Path | str, name: str) -> Path:
    """The console script ``name`` of the virtualenv ``venv``: ``bin/<name>`` when it is there, else ``Scripts\\<name>.exe``
    (a Windows venv) when that is, on every platform; neither, this platform's own spelling."""
    root = Path(venv)
    for candidate in (root / "bin" / name, root / "Scripts" / f"{name}.exe"):
        if candidate.is_file():
            return candidate
    return root / "Scripts" / f"{name}.exe" if WINDOWS else root / "bin" / name


def venv_python(venv: Path | str) -> Path:
    """The interpreter of the virtualenv ``venv`` (``venv_exe``)."""
    return venv_exe(venv, "python")
