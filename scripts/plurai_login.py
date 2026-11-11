#!/usr/bin/env python3
"""Save the Plurai backend url and token where every Plurai client reads them, so no variable has to be exported.

    printf '%s' "$TOKEN" | python3 <plugin root>/scripts/plurai_login.py --url BACKEND_URL
    python3 <plugin root>/scripts/plurai_login.py --status

This is the same command as the SDK's ``plurai login``, in the standard library alone so that it works before the
SDK is installed. ``--url`` is required: there is no default backend. The token comes on stdin, from a pipe or, on a
terminal, from a prompt with the echo off. It is never an argument or read from the environment: either would put
it in a process list. Both files are written as ``{url, token}`` to ``~/.plurai/credentials.json``, byte for byte
the same for the same input (``scripts/tests/fixtures/credentials_parity.json``, owned by the SDK).

The url must be ``https`` under ``*.plurai.ai``, or loopback. It is checked before the token is read, and any other
origin is refused, so the file never holds a pair no client would use and a login that a model runs cannot point later
sessions at a host of its choosing. ``--status`` gives one short answer a user can read: for a saved login the
``backend:`` line and, for a file the SDK's browser login wrote, its ``account`` lines; otherwise "not logged in" or "your
saved login can't be used". It names no variable and never prints the token. This script has no browser
login: a login here replaces the whole file, so a browser login's ``account`` does not outlive it.

The file is written atomically, with mode 0600, inside a directory of mode 0700. A symlink at the file or the
directory is refused, never followed. The ``PLURAI_URL`` / ``PLURAI_TOKEN`` pair in the environment still takes precedence over the saved
credential. The file lives in the account's home directory; a ``HOME`` that names another directory is refused.

On native Windows the file goes under the profile folder the operating system names (``SHGetKnownFolderPath``), never
``HOME`` or ``USERPROFILE``. NTFS has no mode bits, so the folder is cut to the user's account on every save and the
file is cut to the account before the token goes in (``hooks/_private_files.py``, with the calls of ``hooks/_winsec.py``),
and it is written through a temporary file moved into place. A symlink or junction at the directory or the file is
refused. A read refuses a file or folder another account owns or whose access list allows anyone but the user, SYSTEM
and Administrators. Run it from PowerShell or Windows Terminal: Git Bash's mintty is not seen as a terminal.

What the file does not protect against: it is readable by anything that runs as the user, including every shell
command a model runs (``cat ~/.plurai/credentials.json``). An exported ``PLURAI_TOKEN`` is no safer in
those shells by default. On Codex, ``ignore_default_excludes = false`` under ``[shell_environment_policy]`` hides the
exported variable from them while the MCP bridge still receives it, which a saved file cannot do; the price is that
the plugin's own shell steps then find no token. The command prints this when it saves a token. A token that
is piped through a model's command lands in that model's transcript, so type or paste it in a terminal of your own.
A model that holds a terminal can also run this command: it replaces the saved account, and cannot point the
token anywhere but a Plurai host or this machine.

Exit codes follow ``plurai login`` (``sdk_exit_codes``): 0 ok; 64 usage; 65 refused (a url or home directory that cannot be used);
77 the token on stdin is missing or not a token. ``--status`` exits 0 whenever it answers; its first line is the answer.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import stat
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn, TextIO

_PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _login_store
import _platform
import _private_files
import sdk_exit_codes as exit_codes


class _Parser(argparse.ArgumentParser):
    """An argument error that does not repeat the arguments: a token typed there is already too exposed."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print("plurai_login: unrecognised or missing arguments. The token is never an argument: pipe it on stdin.", file=sys.stderr)
        sys.exit(exit_codes.USAGE)


def _refuse(text: str, code: int = exit_codes.REFUSED) -> int:
    print(f"plurai_login: {text}", file=sys.stderr)
    return code


def _open_private_directory(path: Path, uid: int) -> tuple[int | None, str]:
    """A descriptor for ``path`` as a directory only the user can use, created when missing; or ``None`` and why not."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        try:
            os.mkdir(path, 0o700)
        except OSError as exc:
            return None, f"{path} cannot be created ({exc.strerror})"
        info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return None, f"{path} is a symlink, which is not followed; remove it or move it away"
    if not stat.S_ISDIR(info.st_mode):
        return None, f"{path} is not a directory"
    if info.st_uid != uid:
        return None, f"{path} is not owned by you"
    if stat.S_IMODE(info.st_mode) & 0o022:
        return None, f"{path} is writable by other users; run `chmod 700 {path}` and check what it holds"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        return None, f"{path} cannot be opened ({exc.strerror})"
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
        os.fchmod(descriptor, 0o700)
    return descriptor, ""


def _write(directory: int, name: str, document: dict) -> str:
    """Write ``document`` as ``name`` in ``directory`` (a descriptor) atomically with mode 0600; "" or why not."""
    try:
        existing = os.lstat(name, dir_fd=directory)
    except FileNotFoundError:
        existing = None
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        return f"{name} is a symlink, which is not followed; remove it first"
    temporary = f".{name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise
    return ""


DASHBOARD = "Create or revoke API keys in the Plurai dashboard; revoking the key there ends this login."
MAX_STDIN_CHARS = 8192

def _save_windows(path: Path, document: dict) -> str:
    """Write ``document`` to ``path`` on Windows; ``""`` or why not.

    No mode bits, ``dir_fd`` or ``O_DIRECTORY`` exist there. A reparse point (symlink or junction) at the folder or at
    the file is refused instead of followed. The folder is cut to the user's account on every write (not only when it is
    created: an existing folder may be another account's, or open to others, and a new file inherits its list), and the
    file is cut to the user before the token goes into it (``_private_files``, as the SDK's login does).
    """
    folder = path.parent
    try:
        try:
            info = os.lstat(folder)
        except FileNotFoundError:
            folder.mkdir()
            info = os.lstat(folder)
        if _login_store.is_reparse_point(info):
            return f"{folder} is a symlink or junction, which is not followed; remove it or move it away"
        if not stat.S_ISDIR(info.st_mode):
            return f"{folder} is not a directory"
        _private_files.restrict_directory(folder)
        _private_files.write_atomic(path, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
    except OSError as error:
        return f"{path} cannot be written ({error.strerror or error}); nothing was saved"
    return ""


EXPOSURE = (
    "The file is readable by anything that runs as you, a shell a model runs included. An exported PLURAI_TOKEN "
    "is no safer there by default. On Codex, ignore_default_excludes = false under [shell_environment_policy] hides the "
    "exported variable from model-run shells while the MCP bridge still gets it, but the plugin's own shell steps then find "
    "no token (see docs/install-codex-cli.md)."
)


def _status(env: Mapping[str, str]) -> int:
    """One short answer a user can read, always with exit 0: the first line says which answer it is.

    A harness shows a command's non-zero exit to the user, so the answer is in the line, which the setup skill reads:
    "Logged in to Plurai." (then the ``backend:`` and account lines), "Not logged in to Plurai.", "Your saved Plurai
    login can't be used.", "This session uses a Plurai login from its environment." (a url and token pair of its own,
    where a login cannot run), or "Plurai cannot look for your login here: …".
    """
    path = _login_store.file_path()
    resolved = _login_store.resolve_backend(env)
    moved = _login_store.home_problem(env)
    if moved:
        print(f"Plurai cannot look for your login here: {moved}")
        return exit_codes.OK
    saved = _login_store.read_file(path)
    if resolved.source == "env":
        print("This session uses a Plurai login from its environment.")
    elif saved.problem:
        print("Your saved Plurai login can't be used.")
    elif not saved.token:
        print("Not logged in to Plurai.")
    else:
        print("Logged in to Plurai.")
        print(f"backend: {saved.url}")
        if saved.account:
            print("\n".join(saved.account.status_lines()))
    return exit_codes.OK


def _read_token(url: str, stdin: TextIO, read_secret: Callable[[str], str]) -> str:
    if stdin.isatty():
        return read_secret(f"Plurai API key for {url} (input is hidden): ").strip()
    return stdin.read(MAX_STDIN_CHARS).strip()


def main(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    read_secret: Callable[[str], str] = getpass.getpass,
) -> int:
    parser = _Parser(description=(__doc__ or "").split("\n\n")[0], prog="plurai_login.py")
    parser.add_argument("--url", help="the backend the token is for: https under *.plurai.ai, or loopback (required to log in)")
    parser.add_argument("--status", action="store_true", help="say which backend the saved credential is for; never prints the token")
    args = parser.parse_args(argv)
    environment = os.environ if env is None else env
    windows = _platform.WINDOWS
    if args.status:
        return _status(environment)
    if args.url is None:
        return _refuse(
            "--url is required: there is no default backend. Run: printf '%s' \"$TOKEN\" | python3 <plugin root>/scripts/plurai_login.py --url <url>",
            exit_codes.USAGE,
        )
    url = args.url.strip()
    origin, why = _login_store.login_origin(url)
    if origin is None:
        return _refuse(f"{why}. Nothing was saved.")
    moved = _login_store.home_problem(environment)
    if moved:
        return _refuse(f"{moved}. Nothing was saved.")
    path = _login_store.file_path()
    token = _read_token(url, sys.stdin if stdin is None else stdin, read_secret)
    if not token:
        return _refuse("no token on stdin. Pipe it in: printf '%s' \"$TOKEN\" | python3 <plugin root>/scripts/plurai_login.py --url <url>. Nothing was saved.", exit_codes.CREDENTIALS)
    if not _login_store.TOKEN_PATTERN.fullmatch(token):
        return _refuse(f"that is not a token: {_login_store.TOKEN_SHAPE}. Nothing was saved.", exit_codes.CREDENTIALS)
    document = {"url": url, "token": token}
    if windows:
        problem = _save_windows(path, document)
    else:
        directory, problem = _open_private_directory(path.parent, os.getuid())
        if directory is None:
            return _refuse(problem)
        try:
            problem = _write(directory, path.name, document)
        finally:
            os.close(directory)
    if problem:
        return _refuse(problem)
    saved = _login_store.read_file(path)
    if saved.token != token or saved.problem:
        return _refuse(f"the file was written but cannot be read back: {saved.problem or 'it holds another token'}")
    private = " (access limited to your user)" if windows else " (mode 0600)"
    print(f"Saved the credential for {url} in {path}{private}. The key is not shown.")
    print(
        "The plugin's MCP connection and scripts use it when PLURAI_TOKEN is not set. A running session uses it from its "
        "next command on."
    )
    print(EXPOSURE)
    print(DASHBOARD)
    return exit_codes.OK


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
