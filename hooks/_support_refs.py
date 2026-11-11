"""The local reference a failure leaves when it never reached the backend.

Plurai clients send no telemetry, so a failure the backend never saw (an install that found no usable ``plurai``, a tool
call a hook refused) has no backend id for the user to quote. This module appends one line to
``~/.plurai/support_refs.jsonl`` instead::

    {"at": "2026-01-01T12:00:00Z", "kind": "install_failure", "ref": "inst-1a2b3c4d", "summary": "one short line"}

The SDK's ``plurai diagnose`` and the plugin's diagnostic script print the newest lines, and the user pastes them into
an issue; the ``ref`` is what support greps for. Nothing reads the file but those two commands and nothing sends it.

The file is private like the credentials file beside it: the directory is mode 0700 and the file 0600 in the account's
home, and a symlink at either is never followed. Windows has no mode bits, ``dir_fd`` or ``O_NOFOLLOW``: there the
file is reached by path, a reparse point (symlink or junction) at the directory or the file is refused, and the
profile folder's own access rules are the protection. A line carries a kind (a plain word), a ref, a timestamp and a summary
the caller composed from facts it holds (a hook name, an event, a tool name), never from tool input, prompts or
output, and never a credential value. The file is bounded: past ``MAX_BYTES`` it keeps the newest ``KEEP_LINES``.

Recording must never change what its caller does, so ``record`` swallows every failure and answers ``None``.

Stdlib only; hooks run under the oldest interpreter ``python3`` can resolve to (``_hookfail.py``).
"""

from __future__ import annotations

import calendar
import contextlib
import hashlib
import json
import os
import re
import stat
import time

import _login_store
import _platform

FILENAME = "support_refs.jsonl"

#: Set to any non-empty value, recording is off. It switches the file off and never moves it.
DISABLE_ENV = "PLURAI_NO_SUPPORT_REFS"

#: Past this size the file is cut back to its newest ``KEEP_LINES`` lines.
MAX_BYTES = 128 * 1024
KEEP_LINES = 100

#: The characters of a summary and the longest one.
SUMMARY_CHARS = 200

#: The same refusal inside this many seconds is one line, so a loop of identical denials does not fill the file.
REPEAT_SECONDS = 600

#: The prefix of a ref, by kind; any other kind uses its first four letters.
_PREFIX = {"install_failure": "inst", "hook_refused": "hook"}

_KIND = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_STAMP = re.compile(r"[0-9T:+\-.Z]{10,40}")
_MIN_SECRET_CHARS = 8
_TAIL_BYTES = 8192


def _summary(text: str) -> str:
    one_line = " ".join(text.split())
    token = os.environ.get(_login_store.TOKEN_VARIABLE, "").strip()
    if len(token) >= _MIN_SECRET_CHARS:
        one_line = one_line.replace(token, "<token>")
    return one_line[:SUMMARY_CHARS]


def _ref(kind: str, summary: str, now: float) -> str:
    digest = hashlib.sha256(f"{now!r}|{os.getpid()}|{kind}|{summary}".encode()).hexdigest()[:8]
    return f"{_PREFIX.get(kind, kind[:4])}-{digest}"


def _open_directory(home: str) -> int | None:
    """A descriptor for ``~/.plurai`` as a private directory the user owns, created when missing; ``None`` if it cannot be one."""
    path = os.path.join(home, ".plurai")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        os.mkdir(path, 0o700)
        info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        return None
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _tail_lines(descriptor: int, size: int) -> list[str]:
    os.lseek(descriptor, max(0, size - _TAIL_BYTES), os.SEEK_SET)
    return os.read(descriptor, _TAIL_BYTES).decode("utf-8", errors="replace").splitlines()


def _seconds(stamp: object) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def _repeat_of(directory: int, kind: str, summary: str, now: float) -> str | None:
    """The ref of the newest line when it is the same refusal inside ``REPEAT_SECONDS``."""
    try:
        reader = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    except OSError:
        return None
    try:
        lines = _tail_lines(reader, os.fstat(reader).st_size)
    finally:
        os.close(reader)
    return _repeat_in(lines, kind, summary, now)


def _repeat_in(lines: list[str], kind: str, summary: str, now: float) -> str | None:
    """The ref of the newest of ``lines`` when it is the same refusal inside ``REPEAT_SECONDS``."""
    if not lines:
        return None
    try:
        last = json.loads(lines[-1])
    except ValueError:
        return None
    seen = _seconds(last.get("at")) if isinstance(last, dict) else None
    if seen is not None and last.get("kind") == kind and last.get("summary") == summary and 0 <= now - seen <= REPEAT_SECONDS:
        ref = last.get("ref")
        return ref if isinstance(ref, str) else None
    return None


def _trim(directory: int) -> None:
    """Replace the file with its newest ``KEEP_LINES`` lines, through a temporary file in the same directory."""
    reader = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        size = os.fstat(reader).st_size
        text = os.read(reader, size).decode("utf-8", errors="replace")
    finally:
        os.close(reader)
    kept = text.splitlines()[-KEEP_LINES:]
    temporary = f".{FILENAME}.{os.getpid()}.tmp"
    writer = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(writer, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept) + "\n")
        os.replace(temporary, FILENAME, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise


def _windows_paths(home: str, *, create: bool) -> tuple[str, str] | None:
    """``(directory, file)`` of the reference file on Windows, or ``None`` when either is a reparse point or the
    directory is not one. The directory is created when ``create`` and missing."""
    directory = os.path.join(home, ".plurai")
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        if not create:
            return None
        os.makedirs(directory, exist_ok=True)
        info = os.lstat(directory)
    if _login_store.is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
        return None
    path = os.path.join(directory, FILENAME)
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        return directory, path
    if _login_store.is_reparse_point(existing) or not stat.S_ISREG(existing.st_mode):
        return None
    return directory, path


def _windows_tail(path: str) -> list[str]:
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(max(0, size - _TAIL_BYTES))
        return handle.read(_TAIL_BYTES).decode("utf-8", errors="replace").splitlines()


def _windows_record(kind: str, text: str, now: float) -> str | None:
    paths = _windows_paths(str(_login_store.account_home()), create=True)
    if paths is None:
        return None
    directory, path = paths
    if os.path.exists(path):
        repeated = _repeat_in(_windows_tail(path), kind, text, now)
        if repeated is not None:
            return repeated
    ref = _ref(kind, text, now)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "kind": kind, "ref": ref, "summary": text}
    with open(path, "ab") as handle:
        handle.write((json.dumps(row) + "\n").encode("utf-8"))
        oversized = handle.tell() > MAX_BYTES
    if oversized:
        with open(path, encoding="utf-8", errors="replace") as handle:
            kept = handle.read().splitlines()[-KEEP_LINES:]
        temporary = os.path.join(directory, f".{FILENAME}.{os.getpid()}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(kept) + "\n")
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
    return ref


def record(kind: str, summary: str) -> str | None:
    """Append a reference of ``kind`` and return its ref, or ``None`` when nothing was written. Never raises."""
    if not _KIND.fullmatch(kind) or os.environ.get(DISABLE_ENV):
        return None
    try:
        text = _summary(summary)
        now = time.time()
        if _platform.WINDOWS:
            return _windows_record(kind, text, now)
        directory = _open_directory(str(_login_store.account_home()))
        if directory is None:
            return None
        try:
            repeated = _repeat_of(directory, kind, text, now)
            if repeated is not None:
                return repeated
            ref = _ref(kind, text, now)
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "kind": kind, "ref": ref, "summary": text}
            descriptor = os.open(FILENAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    return None
                if info.st_mode & 0o077:
                    os.fchmod(descriptor, 0o600)
                os.write(descriptor, (json.dumps(row) + "\n").encode("utf-8"))
                oversized = info.st_size > MAX_BYTES
            finally:
                os.close(descriptor)
            if oversized:
                _trim(directory)
            return ref
        finally:
            os.close(directory)
    except Exception:  # noqa: BLE001 - see the docstring: a reference must never change what its caller does
        return None


def newest(limit: int) -> list[dict]:
    """The newest ``limit`` well-formed lines, newest first, each as ``{at, kind, ref, summary}``. Never raises.

    A line with a kind or ref of the wrong shape, or that is not JSON, is skipped. The summary is cut to
    ``SUMMARY_CHARS`` and the token is removed from it, so a file edited by hand cannot make a bundle carry one.
    """
    rows: list[dict] = []
    try:
        if _platform.WINDOWS:
            paths = _windows_paths(str(_login_store.account_home()), create=False)
            if paths is None or not os.path.isfile(paths[1]):
                return rows
            lines = _windows_tail(paths[1])
        else:
            directory = _open_directory(str(_login_store.account_home()))
            if directory is None:
                return rows
            try:
                reader = os.open(FILENAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            finally:
                os.close(directory)
            try:
                info = os.fstat(reader)
                if not stat.S_ISREG(info.st_mode):
                    return rows
                lines = _tail_lines(reader, info.st_size)
            finally:
                os.close(reader)
    except Exception:  # noqa: BLE001 - see ``record``
        return rows
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        kind, ref, at, summary = row.get("kind"), row.get("ref"), row.get("at"), row.get("summary")
        if not (isinstance(kind, str) and _KIND.fullmatch(kind) and isinstance(ref, str) and _REF.fullmatch(ref)):
            continue
        rows.append(
            {
                "at": at if isinstance(at, str) and _STAMP.fullmatch(at) else "unknown time",
                "kind": kind,
                "ref": ref,
                "summary": _summary(summary) if isinstance(summary, str) else "",
            }
        )
        if len(rows) >= limit:
            break
    return rows
