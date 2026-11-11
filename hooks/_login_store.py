"""The plugin's one backend resolver: the url and bearer a session talks to, as one atomic unit.

``resolve_backend`` returns ``(url, token, source)``: the ``PLURAI_URL`` / ``PLURAI_TOKEN`` pair from the environment
that launched the process, else the pair saved by ``scripts/plurai_login.py`` in ``~/.plurai/credentials.json``.
The two sources are never combined: a url from one and a token from the other is a configuration error that
names both, because a bearer paired with a host from another source is exactly what a model-run command could
use to redirect it. A ``.env`` file is not a source.

The saved file is read with the care a secret needs:

* it is looked for in the home directory the account database names, never in a ``HOME`` that differs from
  it: a command a model runs sets its own environment, and where the credential lives does not move with it.
  Windows has no account database here: the home is the profile folder the operating system names for the user
  (``SHGetKnownFolderPath``), never ``HOME``, ``USERPROFILE``, ``HOMEDRIVE`` or ``HOMEPATH``; a profile on a
  network path or a drive that is not fixed, or a ``.plurai`` folder that resolves elsewhere through a link, is not
  used (``_winsec``, a copy of the SDK's file, whose cases are in ``scripts/tests/fixtures/credentials_windows_vectors.json``);
* it is opened without following a symlink (the ``~/.plurai`` directory included), and judged through the
  descriptor it was opened with, so the file checked is the file read;
* it is a regular file owned by the user, with no group or other permission bits, in a directory that
  only the user can write to (on Windows the same is done with access lists: owner and list of the file and of
  the folder must be the user's alone, plus SYSTEM and Administrators);
* it is ``{url, token}``, with the ``account`` a browser login adds beside them (display data, never used to
  decide where the token goes; ``Credential.account``), and nothing else: a file carrying a ``version`` or an
  ``origin`` is not a credentials file;
* its ``url`` is accepted only under ``*.plurai.ai`` or on loopback. Any other backend reaches a client only
  through ``PLURAI_URL`` in the launch environment, which the plugin's own commands cannot rewrite.

The bearer goes only to URLs under the resolved origin (``token_for``): the same scheme, host and port. A loopback
backend is one origin like any other, so its token goes only to that port, and a remote backend's token never goes to
a loopback port, where any local process could be listening. The destination is decided by the launch environment and
the session pin (``url_guard``), never by anything a command can rewrite.

On Claude Code the manifest's MCP entry dials ``${PLURAI_URL}/mcp``, falling back to the production default, which Claude Code expands from the launch environment and cannot read from the file. ``effective_mcp_url`` is
that address, and ``claude_code_mismatch`` says when the saved login is for another one.

Stdlib only; hooks run under the oldest interpreter ``python3`` can resolve to (``_hookfail.py``).
"""

from __future__ import annotations

import errno
import json
import ntpath
import os
import re
import shlex
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# `python -I` (how callers that hold the bearer start a script) leaves this directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _platform
import _private_files
import _winsec
import session_pin

#: The shortest bearer accepted, the SDK's own minimum (``plurai.credentials.MIN_TOKEN_LENGTH``): a shorter value would
#: escape the SDK's redaction of what it writes.
MIN_TOKEN_LENGTH = 8
#: What a bearer may be made of. A token with whitespace or a control character is not one.
TOKEN_PATTERN = re.compile(rf"[A-Za-z0-9._~+/=-]{{{MIN_TOKEN_LENGTH},4096}}")
#: How a refusal says what a token must be.
TOKEN_SHAPE = f"it must be one run of letters, digits and . _ ~ + / = - (no spaces), {MIN_TOKEN_LENGTH} to 4096 characters"
_MAX_BYTES = 64 * 1024
#: The path of a backend url: plain segments only.
_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)*/?")
TOKEN_VARIABLE = "PLURAI_TOKEN"


@dataclass(frozen=True)
class Account:
    """Who a browser login is for, as the SDK's ``plurai login`` saves it beside the token. No field is a secret."""

    organization: str
    organization_id: str
    user: str
    user_id: str
    key_name: str
    key_id: str
    #: An ISO 8601 time, or ``None`` for a key that does not expire.
    expires_at: str | None

    def status_lines(self) -> list[str]:
        """The ``--status`` lines after ``backend:``, in the order and spelling ``plurai login --status`` prints them.

        Every field comes from the backend through the file, so each is printed through ``_printable``: a name with an escape
        sequence or a newline would otherwise drive the terminal or forge a line the model reads.
        """
        shown = _printable
        return [
            f"key: {shown(self.key_name)} ({shown(self.key_id)})",
            f"expires: {shown(self.expires_at or 'never')}",
        ]


def _account(value: object) -> Account | None:
    """The ``account`` of a credentials file, or ``None`` when it is missing or has another shape.

    The shape is the SDK's (``plurai.credentials.Account.from_json``): every field a string, whatever it holds, and the
    expiry a string or ``None``. A reader that does not understand ``account`` ignores it, so another shape leaves the
    login usable and shows only the backend. Values reach a ``--status`` line only through ``_printable``.
    """
    if not isinstance(value, dict):
        return None
    organization, user = value.get("organization"), value.get("user")
    if not isinstance(organization, dict) or not isinstance(user, dict):
        return None
    fields = {
        "organization": organization.get("name"),
        "organization_id": organization.get("id"),
        "user": user.get("display"),
        "user_id": user.get("id"),
        "key_name": value.get("key_name"),
        "key_id": value.get("key_id"),
    }
    expires = value.get("expires_at")
    if not all(isinstance(item, str) for item in fields.values()) or not (expires is None or isinstance(expires, str)):
        return None
    return Account(**fields, expires_at=expires)


def _expired(expires_at: str | None) -> bool:
    """Whether ``expires_at`` has passed. A time with no offset is UTC, as the SDK reads it.

    The SDK also refuses an expiry it cannot parse; this reader, which runs on older Pythons whose parser accepts less,
    calls a key expired only when it can read the time. So the plugin never sends the user into a login the SDK would
    answer with the key it already has.
    """
    if not expires_at:
        return False
    text = expires_at.strip()
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
    except ValueError:
        return False
    utc = timezone.utc  # noqa: UP017 - the hooks run on Python 3.9, which has no datetime.UTC
    return (moment if moment.tzinfo else moment.replace(tzinfo=utc)) <= datetime.now(utc)


@dataclass(frozen=True)
class Credential:
    """What the credential file holds. ``problem`` says why a file that exists was not used."""

    token: str = field(default="", repr=False)
    origin: tuple | None = None
    problem: str = ""
    #: The url the file names.
    url: str = ""
    #: Who a browser login is for; ``None`` for a stdin login, which saves ``{url, token}`` only.
    account: Account | None = None
    #: Whether a new login replaces the file and so clears ``problem``. False for a problem that sits outside the file
    #: (the home, its owner, its folder), which a login would meet again.
    login_fixes: bool = False


def _replaced_by_login(problem: str) -> Credential:
    """A file that is not used for a reason inside the file itself, which a new login rewrites."""
    return Credential(problem=problem, login_fixes=True)


#: Said with every reason a profile cannot be used: the backend can still be named in the environment.
_ENVIRONMENT_HINT = f"; a backend named in the environment (PLURAI_URL and {TOKEN_VARIABLE}) is still used"


def _printable(text: str) -> str:
    """``text`` with every character that is not printable replaced, for a message that names a path or an account.

    A profile folder or the target of a link comes from the system or the file system, and the account's names come from
    the backend; any of them may hold a newline, an escape sequence (C0 or C1) or a bidirectional override that would
    forge a line of the message, drive the terminal it is printed on, or reorder what the user reads.
    """
    return "".join(char if char.isprintable() else "?" for char in text)


def windows_profile(real: Callable[[str], str] = os.path.realpath) -> tuple[str, str]:
    """``(the user's profile folder, why it may not hold the credential)`` on Windows; the reason is ``""`` when it may.

    The folder is the operating system's answer, not an environment variable's. A profile that is itself a link is followed
    to its target, which must be a fixed local folder. The reason is a sentence naming the folder (control characters
    replaced); the folder returned is the system's answer as it is.
    """
    try:
        profile = _winsec.profile_directory()
        drive = _winsec.drive_type(_winsec.local_root(profile))
        resolved = real(profile)
        resolved_root = _winsec.local_root(resolved)
        resolved_drive = _winsec.drive_type(resolved_root) if resolved_root else 0
    except OSError as exc:
        return "", f"the user profile cannot be determined ({exc}), so the saved credential is not looked for{_ENVIRONMENT_HINT}"
    code = _winsec.profile_problem(profile, drive, resolved, resolved_drive)
    if code:
        target = "" if resolved.lower() == profile.lower() else f" (it resolves to {_printable(resolved)})"
        return profile, _winsec.message(code, _printable(profile)) + target + _ENVIRONMENT_HINT
    folder = ntpath.join(profile, ".plurai")
    resolved_folder = real(folder)
    if code := _winsec.folder_problem(resolved, resolved_folder):
        return profile, f"{_winsec.message(code, _printable(folder))} (it resolves to {_printable(resolved_folder)}); remove the link or move it away{_ENVIRONMENT_HINT}"
    return profile, ""


def windows_home() -> Path:
    """The home on Windows: the profile folder the operating system names (``Path("~")``, under which nothing is found, when there is none)."""
    profile, _ = windows_profile()
    return Path(profile) if profile else Path("~")


def account_home() -> Path:
    """The user's home directory as the account database has it.

    ``HOME`` is the environment of whoever launched the process, and a command a model runs sets its own.
    An account with no database entry (a container user) falls back to the expansion of ``~``. Windows has
    no account database (``pwd``): there it is the profile folder the operating system names (``windows_home``).
    """
    if _platform.WINDOWS:
        return windows_home()
    try:
        import pwd

        home = pwd.getpwuid(os.getuid()).pw_dir
    except (ImportError, KeyError):
        home = ""
    return Path(home) if home else Path(os.path.expanduser("~"))


def home_problem(env: Mapping[str, str] | None = None) -> str:
    """Why the saved credential is not looked for under ``env``, or ``""``.

    A ``HOME`` that is set and names another directory than the account's home would send the lookup to a
    directory the command chose, so the file is not used while it differs. On Windows ``HOME`` is not read; the
    reason is the profile folder's, if it may not be used.
    """
    if _platform.WINDOWS:
        return windows_profile()[1]
    source = os.environ if env is None else env
    named = source.get("HOME")
    if not named:
        return ""
    actual = account_home()
    try:
        if os.path.normpath(named) == os.path.normpath(str(actual)) or os.path.samefile(named, actual):
            return ""
    except OSError:
        pass
    return f"HOME is {named}, not the account's home {actual}, so the saved credential is not looked for there; run this with your own HOME, or export {TOKEN_VARIABLE}"


def file_path() -> Path:
    """``~/.plurai/credentials.json`` in the account's home directory."""
    return account_home() / ".plurai" / "credentials.json"


#: Set to a home directory, the saved login IN that home is not read: it reads as missing. The root conftest sets it to
#: the account's home of whoever runs the suite, so neither the test process nor a child it starts (which imports this
#: module afresh, and with no pin would carry that login to its backend in limited mode) reads the developer's login,
#: while a test that names a home of its own still reads the file there. It can only take a login away, never name
#: another file, so a command that sets it only finds no login.
IGNORE_SAVED_LOGIN_VARIABLE = "PLURAI_IGNORE_SAVED_LOGIN"


def _saved_login_ignored(env: Mapping[str, str] | None = None) -> bool:
    """Does :data:`IGNORE_SAVED_LOGIN_VARIABLE`, in this process's environment or in ``env``, name the account's home?"""
    named = [value for source in (os.environ, env or {}) if (value := (source.get(IGNORE_SAVED_LOGIN_VARIABLE) or "").strip())]
    if not named:
        return False
    home = os.path.normpath(str(account_home()))
    return any(os.path.normpath(value) == home for value in named)


def owner_is_acceptable(file_uid: int, user_uid: int) -> bool:
    """A credential file must belong to the user running the process: a file of another user may have been planted."""
    return file_uid == user_uid


def spell(value: tuple) -> str:
    """An origin as text, for messages and for the file: ``scheme://host:port``."""
    return session_pin._spell(value)


def backend_origin(url: str) -> tuple[tuple | None, str]:
    """The origin a bearer may go to for the backend url ``url``, or ``None`` and why not.

    The session pin's parse (``session_pin.origin``: http(s), a host, no userinfo, backslash, whitespace or
    ``%`` in the authority), then the rules that keep two parsers from reading the url two ways: port 0,
    a query, a fragment, an escape or an ``@`` anywhere, and a path that is not a plain run of segments.
    The scheme must be https, or http to a loopback host. The reason never repeats ``url``, which may
    carry a secret.
    """
    import url_guard

    text = url.strip()
    found = session_pin.origin(text)
    if found is None:
        return None, "PLURAI_URL is not one unambiguous http(s) url (no userinfo, backslash, whitespace or % in the host, and a scheme and host)"
    if found[2] == 0:
        return None, "PLURAI_URL names port 0"
    if "?" in text or "#" in text or "%" in text or "@" in text:
        return None, "PLURAI_URL carries a query, a fragment, an escape or an @, which two parsers could read differently"
    path = urlsplit(text).path
    if not _PATH.fullmatch(path) or any(segment in (".", "..") for segment in path.split("/")):
        return None, "PLURAI_URL has a path that is not a plain run of segments (no '..', '//' or escapes)"
    if found[0] == "https" or url_guard.is_loopback(text):
        return found, ""
    return None, "a backend that is not on this machine must use https"


#: Hosts under this suffix may be named by the credentials file: the glob ``*.plurai.ai``, so the bare domain is not under it.
#: The one definition; the SDK's resolver carries the same constant.
ALLOWED_SUFFIX = ".plurai.ai"


def _refused_file_origin(found: tuple, url: str) -> str:
    """Why a credentials file may not name ``url`` (origin ``found``), or ``""`` when it may: under ``*.plurai.ai`` or loopback.

    The reason names the host and never repeats the url, which may carry a secret.
    """
    import url_guard

    if url_guard.is_loopback(url):
        return ""
    host = found[1]
    if host.endswith(ALLOWED_SUFFIX) and all(host.split(".")):
        return ""
    return (
        f"{host} is not under *.plurai.ai and is not loopback, so no client would use a token saved for it. "
        "Allowed origins: *.plurai.ai or loopback. To use another backend, export PLURAI_URL and PLURAI_TOKEN "
        "in the environment that launches this command"
    )


def login_origin(url: str) -> tuple[tuple | None, str]:
    """The origin a login may save for ``url``, or ``None`` and why not.

    ``backend_origin``'s rules, and then the allowed origins (``_refused_file_origin``). Login refuses everything
    else at write time, so the file never holds a pair that no client would use.
    """
    found, why = backend_origin(url)
    if found is None:
        return None, why.replace("PLURAI_URL", "--url")
    refused = _refused_file_origin(found, url)
    return (None, refused) if refused else (found, "")


is_reparse_point = _private_files.is_reparse_point


def _fix_or_delete(folder: Path) -> str:
    return f"; fix its owner and access list, or delete {_printable(str(folder))} and run scripts/plurai_login.py again"


def _security_problem(path: Path, folder: Path, user: str, read_security: Callable[[], _winsec.FileSecurity]) -> str:
    """Why the owner or the access list of ``path`` (the file or its folder) is not the user's alone, or ``""``; the reason names what to fix."""
    try:
        security = read_security()
    except OSError as exc:
        return f"the owner and access list of {_printable(str(path))} cannot be read ({exc})"
    code = _winsec.access_problem(security.owner, security.allowed, security.dacl_present, user)
    return f"{_winsec.message(code, _printable(str(path)))}{_fix_or_delete(folder)}" if code else ""


def _read_windows(target: Path) -> bytes | Credential:
    """The bytes of the credential at ``target`` on Windows, or the ``Credential`` that says why not.

    Neither the file nor ``~/.plurai`` may be a reparse point (a symlink or a junction), and the file must be a
    regular file with no alternate-stream name; the descriptor opened must be the very file ``lstat`` judged
    (same volume and file index), which stands in for ``O_NOFOLLOW``. The folder is checked on a handle of its own before
    the file is opened, and the file on the open descriptor's own handle: the object checked is the object read. Both must
    be owned by the user or the Administrators group (an elevated process creates files for the latter), and their access
    lists must allow no one but the user, SYSTEM and Administrators.
    """
    if ":" in target.name:
        return Credential(problem=f"{_printable(str(target))} names an alternate data stream, which is not a credential file")
    again = "a new login replaces it"
    try:
        if is_reparse_point(os.lstat(target.parent)):
            return _replaced_by_login(f"{_printable(str(target.parent))} is a symlink or junction, which is not followed; {again}")
        judged = os.lstat(target)
    except FileNotFoundError:
        return Credential()
    except OSError as exc:
        return Credential(problem=f"{_printable(str(target))} cannot be inspected ({exc.strerror})")
    if is_reparse_point(judged):
        return _replaced_by_login(f"{_printable(str(target))} is a symlink or junction, which is not followed; {again}")
    if not stat.S_ISREG(judged.st_mode):
        return Credential(problem=f"{_printable(str(target))} is not a regular file")
    try:
        user = _winsec.user_sid()
    except OSError as exc:
        return Credential(problem=f"the current user cannot be identified ({exc})")
    if problem := _security_problem(target.parent, target.parent, user, lambda: _winsec.file_security(target.parent)):
        return Credential(problem=problem)
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return Credential()
    except OSError as exc:
        return Credential(problem=f"{_printable(str(target))} cannot be opened ({exc.strerror})")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (judged.st_dev, judged.st_ino):
            return Credential(problem=f"{_printable(str(target))} changed while it was being opened; try again")
        if problem := _security_problem(target, target.parent, user, lambda: _winsec.descriptor_security(descriptor)):
            return Credential(problem=problem)
        return os.read(descriptor, _MAX_BYTES + 1)
    finally:
        os.close(descriptor)


def windows_private_problem(path: Path) -> str:
    """On Windows, why ``path`` (a file) or its folder is not the user's alone, or ``""``: the checks of ``_read_windows`` for a file that is not the credential.

    Not Windows: ``""``, because the mode bits are the check there. An unreadable owner or access list is a reason.
    """
    if not _platform.WINDOWS:
        return ""
    try:
        user = _winsec.user_sid()
    except OSError as exc:
        return f"the current user cannot be identified ({exc})"
    return _security_problem(path.parent, path.parent, user, lambda: _winsec.file_security(path.parent)) or _security_problem(
        path, path.parent, user, lambda: _winsec.file_security(path)
    )


def read_file(path: Path | None = None, *, uid: int | None = None) -> Credential:
    """The credential at ``path`` (default ``file_path()``), or why it was not used. A missing file is no credential and no problem.

    On Windows the file is judged as ``_read_windows`` says (owner and access list instead of mode bits); the url rules are the same.
    """
    if path is None:
        if _platform.WINDOWS and (unusable := home_problem()):
            return Credential(problem=unusable)
        if _saved_login_ignored():
            return Credential()
    target = file_path() if path is None else path
    if _platform.WINDOWS:
        found = _read_windows(target)
        if isinstance(found, Credential):
            return found
        return _parse(target, found)
    me = os.getuid() if uid is None else uid
    try:
        if stat.S_ISLNK(os.lstat(target.parent).st_mode):
            return _replaced_by_login(f"{target.parent} is a symlink, which is not followed; a new login replaces it")
    except FileNotFoundError:
        return Credential()
    except OSError as exc:
        return Credential(problem=f"{target.parent} cannot be inspected ({exc.strerror})")
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return Credential()
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return _replaced_by_login(f"{target} is a symlink, which is not followed; a new login replaces it")
        return Credential(problem=f"{target} cannot be opened ({exc.strerror})")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return Credential(problem=f"{target} is not a regular file")
        if not owner_is_acceptable(info.st_uid, me):
            return Credential(problem=f"{target} is not owned by the user running this session")
        if stat.S_IMODE(info.st_mode) & 0o077:
            return _replaced_by_login(f"{target} permissions let other users read it (mode {stat.S_IMODE(info.st_mode):o}); a new login replaces it")
        folder = os.stat(target.parent)
        if not owner_is_acceptable(folder.st_uid, me) or stat.S_IMODE(folder.st_mode) & 0o022:
            return Credential(problem=f"{target.parent} must belong to the user and not be writable by others")
        raw = os.read(descriptor, _MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    return _parse(target, raw)


def _parse(target: Path, raw: bytes) -> Credential:
    """The credential in the bytes ``raw`` read from ``target``, or why it is not one."""
    if len(raw) > _MAX_BYTES:
        return _replaced_by_login(f"{target} is too large to be a credential file")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _replaced_by_login(f"{target} is not valid JSON")
    if not isinstance(document, dict) or "version" in document or "origin" in document:
        return _replaced_by_login(f"{target} is not a {{url, token}} credential file; a new login replaces it")
    token = document.get("token")
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        return _replaced_by_login(f"{target} holds no usable token")
    named_url = document.get("url")
    found, why = backend_origin(named_url) if isinstance(named_url, str) else (None, "it is not a string")
    if found is None:
        return _replaced_by_login(f"the url in {target} cannot take a token ({why})")
    refused = _refused_file_origin(found, named_url)
    if refused:
        return _replaced_by_login(f"the saved login in {target} is not used: {refused}")
    account = _account(document.get("account"))
    if account is not None and _expired(account.expires_at):
        return _replaced_by_login(f"the key saved in {target} has expired; a new login replaces it")
    return Credential(token=token, origin=found, url=named_url.strip(), account=account)


def environment_token(env: Mapping[str, str] | None = None) -> str:
    """The token in the environment, whitespace removed; empty when unset."""
    source = os.environ if env is None else env
    return "".join((source.get(TOKEN_VARIABLE) or "").split())




# --- the resolver ---------------------------------------------------------------------------------------------

URL_VARIABLE = "PLURAI_URL"

#: The API root Claude Code dials when ``PLURAI_URL`` is unset: the default of the manifest's
#: ``${PLURAI_URL:-<default>}/mcp``, and the only default address in any client. ``.claude-plugin/plugin.json``
#: carries the same value, which a test holds in step.
MANIFEST_DEFAULT_URL = "https://sandbox.plurai.ai/v1"

#: What a refusal says when nothing configures a backend. It is for the model, which runs the setup skill and shows
#: the user nothing of it; the sign-up page in the browser is the one place the terms are shown.
MISSING_CONFIGURATION_MESSAGE = "Plurai is not logged in. Run the harness-setup skill to log in."

_PARTIAL_TAIL = (
    "Set both PLURAI_URL and PLURAI_TOKEN, or unset the one that is set to use the saved login."
)


@dataclass(frozen=True)
class Backend:
    """The backend one session talks to: its API root, its bearer, and where the pair came from.

    ``source`` is ``env`` (the launch environment's pair, or a loopback url alone), ``file`` (the saved
    login) or ``none``. ``problem`` says why no usable pair exists, and is empty for a backend that is
    merely not configured. ``url`` has no trailing slash and no ``/mcp``.
    """

    url: str = ""
    token: str = field(default="", repr=False)
    source: str = "none"
    problem: str = ""
    #: Whether a new login clears ``problem``. A backend that is merely not configured needs a login too, and has no problem.
    login_fixes: bool = False

    @property
    def needs_login(self) -> bool:
        """Whether a login is what this backend lacks: nothing configured, or a problem a new login clears."""
        return not self.configured and (not self.problem or self.login_fixes)

    @property
    def configured(self) -> bool:
        return bool(self.url) and not self.problem

    def refusal(self, stopper: str) -> str:
        """The text for a command that cannot go on: the problem, or the shared missing-configuration message."""
        return f"{stopper} stopped: {self.problem}" if self.problem else unconfigured_text(stopper)


def unconfigured_text(stopper: str) -> str:
    """The shared missing-configuration message, with one leading line naming what stopped."""
    return f"{stopper} stopped.\n{MISSING_CONFIGURATION_MESSAGE}"


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def api_root(url: str) -> str:
    """The API root a url names: no surrounding space, no trailing slash, no ``/mcp``."""
    text = url.strip().rstrip("/")
    return text.removesuffix("/mcp").rstrip("/")


def _saved(env: Mapping[str, str]) -> Credential:
    """The saved login, or an empty credential carrying why the file is not looked for."""
    # A home that is not the account's is named before the ignored login, so its reason reads the same under the tests.
    moved = home_problem(env)
    if moved:
        return Credential(problem=moved)
    return Credential() if _saved_login_ignored(env) else read_file(file_path())


def _partial(have: str, lack: str, extra: str = "") -> Backend:
    return Backend(problem=f"Plurai is partly configured: {have} is set but {lack} is not.{extra} {_PARTIAL_TAIL}")


def resolve_backend(env: Mapping[str, str] | None = None) -> Backend:
    """The one answer to "which backend, with which bearer": the environment pair, else the saved pair.

    * both variables set: the environment's pair (any https origin, or loopback);
    * neither set: the saved login, when it is usable and its url is under ``*.plurai.ai`` or loopback;
    * ``PLURAI_URL`` alone: the saved login when it is for that very url (so exporting the saved url
      completes the configuration), a tokenless local backend when the url is loopback, else an error;
    * ``PLURAI_TOKEN`` alone: an error that names the saved login it will not be combined with.

    A url from one source and a token from the other is never a pair.
    """
    source = _env(env)
    named = (source.get(URL_VARIABLE) or "").strip()
    token = environment_token(source)
    if token and len(token) < MIN_TOKEN_LENGTH:
        return Backend(problem=f"{TOKEN_VARIABLE} is not usable: {TOKEN_SHAPE}.")
    if named:
        found, why = backend_origin(named)  # the url as given: a spelling two parsers read differently is refused whole
        if found is None:
            return Backend(problem=why)
        root = api_root(named)
        if token:
            return Backend(url=root, token=token, source="env")
        saved = _saved(source)
        if saved.token and api_root(saved.url) == root:
            return Backend(url=root, token=saved.token, source="file")
        import url_guard

        if url_guard.is_loopback(root):
            return Backend(url=root, source="env")
        if saved.token:
            return _partial(URL_VARIABLE, TOKEN_VARIABLE)
        # No usable login at all: the url picks the backend `plurai login` signs in to, so the remedy is a login, not
        # unsetting the url, and the message names no variable.
        return Backend(problem=MISSING_CONFIGURATION_MESSAGE, login_fixes=True)
    if token:
        default = api_root(MANIFEST_DEFAULT_URL) if _claude_code(source) else ""
        if default:
            return Backend(url=default, token=token, source="env")
        saved = _saved(source)
        refused = f" The saved login names {saved.url}; a token from the environment is not combined with a url from the file." if saved.token else ""
        return _partial(TOKEN_VARIABLE, URL_VARIABLE, refused)
    saved = _saved(source)
    if saved.problem:
        return Backend(problem=saved.problem, login_fixes=saved.login_fixes)
    if not saved.token:
        return Backend()
    return Backend(url=api_root(saved.url), token=saved.token, source="file")


def _claude_code(env: Mapping[str, str]) -> bool:
    """Whether ``env`` is a Claude Code (or harness-less) shell, the only place the manifest default applies."""
    import _workdir

    return _workdir.harness(env) == "claude"


def token_for(url: str, env: Mapping[str, str] | None = None) -> str:
    """The bearer for ``url``: the resolved backend's, only when ``url`` has the backend's origin (scheme, host and port).

    REST routes and mock URLs of the backend share its origin: a mock is deployed behind the backend and served at
    ``<backend>/mock/<id>/``, so no flow sends the bearer to a mock on another origin. In particular a remote
    backend's token never goes to a loopback port (a local listener would collect it), and a loopback backend's token
    goes only to its own port. A url two parsers could read as two hosts gets nothing.
    ``url_guard.is_backend_url`` additionally holds the session's pin.
    """
    found = session_pin.origin(url.strip())
    if found is None:
        return ""
    backend = resolve_backend(env)
    if not backend.token:
        return ""
    return backend.token if found == session_pin.origin(backend.url) else ""


# --- Claude Code ------------------------------------------------------------------------------------------------
# Claude Code reads the saved login through the manifest's ``headersHelper``, and its SessionStart notice names a login
# for another backend than the MCP url; both ask these functions, so there is one reader of the file and one rule for
# which backend a token belongs to.


def effective_mcp_url(env: Mapping[str, str] | None = None) -> str:
    """The MCP endpoint Claude Code dials: ``<PLURAI_URL>/mcp``, else the manifest default's, else ``""``.

    Claude Code expands the manifest's variable from the launch environment, so a session's later exports and the
    saved login do not move it.
    """
    root = api_root((_env(env).get(URL_VARIABLE) or "") or MANIFEST_DEFAULT_URL)
    return f"{root}/mcp" if root else ""


def claude_code_mismatch(env: Mapping[str, str] | None = None) -> str:
    """Why the saved login is for another backend than the one Claude Code dials, or ``""``.

    Only a pair that comes from the file can disagree with the MCP url; the environment's pair is the MCP url. The
    text names the exact export that makes the two agree.
    """
    backend = resolve_backend(env)
    if backend.source != "file":
        return ""
    dialled = effective_mcp_url(env)
    if dialled == f"{backend.url}/mcp":
        return ""
    where = dialled or "no backend (PLURAI_URL is not exported and the plugin has no default address)"
    return (
        f"the saved login is for {backend.url}, but the MCP server dials {where}, so tool calls and the shell side "
        f"would reach different backends. Export the saved backend in the environment that launches this session "
        f"and restart it: export PLURAI_URL={shlex.quote(backend.url)}"
    )


def headers(server_url: str, env: Mapping[str, str] | None = None) -> dict:
    """The MCP ``headersHelper``'s answer for the server Claude Code is connecting to.

    Claude Code strips credential-named variables from a plugin helper's environment, so the helper cannot see
    whether the operator exported a token and resolves without one. It sends the saved token only when the pair
    is the file's (nothing else configured, or the exported url is the file's own) and ``server_url`` is under that
    backend's origin; otherwise the manifest's static ``Authorization`` header, the operator's own pair, stands.
    """
    seen = {key: value for key, value in _env(env).items() if key != TOKEN_VARIABLE}
    if resolve_backend(seen).source != "file":
        return {}
    found = token_for(server_url, seen)
    return {"Authorization": f"Bearer {found}"} if found else {}
