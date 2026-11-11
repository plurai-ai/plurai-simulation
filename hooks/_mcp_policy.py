"""Whether an organization's managed settings keep Claude Code from loading the plugin's MCP server.

Under ``allowManagedMcpServersOnly`` Claude Code loads only the MCP servers an admin's ``allowedMcpServers`` admits, and
says nothing to the session when ours is not among them: the init event lists no ``plurai`` server and the tools are
simply absent. They are deferred behind ToolSearch, so nothing tells the model and it never passes
``--no-backend-tools``: without this check Step 0 says ``ready``. ``excluded`` reads the same managed settings and names the cause; Step 0 prints it as a
``WARN (backend tools)`` line that sends the model to its tool list, the one real fact (``_readiness.py``).

The rules, as Claude Code applies them (2.1.316, and the managed-MCP docs):

* The server is ``plugin:plurai-simulation:plurai``, remote, at the expanded ``${PLURAI_URL}/mcp``.
* Only admin sources count, ranked: the cached server-managed settings, the machine's Windows policy key, then the
  managed-settings file with its drop-ins. The user's policy key is not an admin source. (The macOS managed-preferences
  plist is not read here: a list there is missed, so a lower source's list may decide, which costs one warning line.)
* The lock is on when any admin source sets it. The list is the highest-ranked admin source's ``allowedMcpServers``,
  whole. A lock with no list admits everything; without the lock this check says nothing.
* An invalid entry is dropped alone ("Invalid entry was ignored"), and a ``serverName`` must match ``^[a-zA-Z0-9_-]+$``,
  so the scoped name can never be written. Only a value that is not a list enforces an empty one.
* With any ``serverUrl`` entry left, a remote server is admitted by URL only; else by ``serverName`` equal to the scoped
  name, which never matches.
* ``deniedMcpServers`` from an admin source blocks by URL or by the scoped name.
* A ``managed-mcp.json`` that parses takes over every MCP server, and plugin servers do not load under it, unless it
  names our URL itself.

Anything this module cannot read the way Claude Code would (a stale cache, an entry with ``${VAR}``, a pattern it
cannot parse) is no cause: the warning is advice, and a wrong one would send the model after a fault that is not there.
Stdlib only, never raises.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

# `flow-window.sh` runs its helpers under `python3 -I`, which leaves the script's directory off sys.path.
_HOOKS = Path(__file__).resolve().parent
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import _login_store  # noqa: E402 - hooks/ is on sys.path above
import _platform  # noqa: E402
import _workdir  # noqa: E402
import registry  # noqa: E402

#: The causes ``excluded`` names.
DENIED = "denied"
NOT_ALLOWLISTED = "not_allowlisted"
MANAGED_MCP_JSON = "managed_mcp_json"

#: The name Claude Code gives the plugin's server, and the prefix of its tools in the model's tool list.
SCOPED_NAME = f"plugin:{registry.PLUGIN}:{registry.SERVER}"
TOOL_PREFIX = f"mcp__plugin_{registry.PLUGIN}_{registry.SERVER}__"

_SERVER_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_DEFAULT_PORTS = {"http": 80, "https": 443}
#: Claude Code replaces its cached server-managed settings on each fetch; an older file may be one it no longer uses.
_STALE_SECONDS = 24 * 3600

#: The machine's policy folder Claude Code reads its managed settings from, per platform. On Windows it sits in the
#: machine's Program Files folder (``_program_files``); this drive is only the fallback when the registry names none.
POLICY_FOLDERS = {
    "windows": "C:/Program Files/ClaudeCode",
    "darwin": "/Library/Application Support/ClaudeCode",
    "linux": "/etc/claude-code",
}
_POLICY_KEY = "SOFTWARE\\Policies\\ClaudeCode"
#: Where Windows records its Program Files folder, and the values that name it: the 64-bit one first, which a 32-bit
#: interpreter reads too (``ProgramFilesDir`` reads as the x86 folder there).
_PROGRAM_FILES_KEY = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
_PROGRAM_FILES_VALUES = ("ProgramW6432Dir", "ProgramFilesDir")
_MAX_BYTES = 2 * 1024 * 1024


class _Unknown(Exception):
    """Something this module cannot read as Claude Code would: no cause."""


def _glob(pattern: str, text: str) -> bool:
    return re.fullmatch("".join(".*" if char == "*" else re.escape(char) for char in pattern), text) is not None


def _url_matches(pattern: str, url: str) -> bool:
    """Claude Code's ``serverUrl`` rule: ``*`` wildcards; the host case-insensitive without a trailing dot; no port is
    the default port (every port when the host has a ``*``); no path is every path."""
    if "${" in pattern:
        raise _Unknown
    parts = re.fullmatch(r"([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]*)([^?#]*)(?:[?#].*)?", pattern.strip())
    if parts is None:
        raise _Unknown
    scheme, authority, path = parts.groups()
    if "@" in authority or "[" in authority or scheme.lower() not in _DEFAULT_PORTS:
        raise _Unknown
    host_pattern, _, port_pattern = authority.partition(":")
    target = urlsplit(url)
    try:
        port = target.port or _DEFAULT_PORTS[target.scheme.lower()]
    except (KeyError, ValueError) as problem:
        raise _Unknown from problem
    if scheme.lower() != target.scheme.lower():
        return False
    if not _glob(host_pattern.lower().rstrip("."), (target.hostname or "").lower().rstrip(".")):
        return False
    if port_pattern and port_pattern != "*":
        if not port_pattern.isdigit():
            raise _Unknown
        if int(port_pattern) != port:
            return False
    elif not port_pattern and "*" not in host_pattern and port != _DEFAULT_PORTS[scheme.lower()]:
        return False
    return not path or _glob(path, target.path or "/")


def _entries(value: object) -> list[dict]:
    """The entries Claude Code keeps from an ``allowedMcpServers`` value: invalid ones dropped one at a time."""
    if not isinstance(value, list):
        return []
    kept: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        if any(isinstance(field, str) and "${" in field for field in entry.values()):
            raise _Unknown
        if isinstance(entry.get("serverUrl"), str):
            kept.append(entry)
        elif isinstance(entry.get("serverName"), str) and _SERVER_NAME.match(entry["serverName"]):
            kept.append(entry)
        elif "serverCommand" in entry:
            kept.append(entry)
    return kept


def _admits(entries: list[dict], url: str) -> bool:
    urls = [entry["serverUrl"] for entry in entries if isinstance(entry.get("serverUrl"), str)]
    if urls:
        return any(_url_matches(pattern, url) for pattern in urls)
    return any(entry.get("serverName") == SCOPED_NAME for entry in entries)


def _denied(sources: list[tuple[str, list[dict]]], url: str) -> bool:
    for _, documents in sources:
        for document in documents:
            listed = document.get("deniedMcpServers")
            for entry in listed if isinstance(listed, list) else []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("serverName") == SCOPED_NAME:
                    return True
                pattern = entry.get("serverUrl")
                try:
                    if isinstance(pattern, str) and _url_matches(pattern, url):
                        return True
                except _Unknown:
                    continue
    return False


# --- the admin sources, read as Claude Code reads them ------------------------------------------------------------


def _document(text: str) -> dict:
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_document(path: Path) -> dict:
    """A JSON object file, or ``{}`` for one that is missing, too large, unreadable or not an object."""
    try:
        if not path.is_file() or path.stat().st_size > _MAX_BYTES:
            return {}
        return _document(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return {}


def _program_files() -> Path | None:
    """The machine's Program Files folder as the Windows registry records it (not the environment, which any prefix
    sets); None off Windows, without ``winreg``, or when no value names an absolute folder."""
    if not _platform.WINDOWS:
        return None
    try:
        import winreg  # noqa: PLC0415 - Windows only

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROGRAM_FILES_KEY) as key:
            for name in _PROGRAM_FILES_VALUES:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if isinstance(value, str) and len(value) > 3 and value[1:3] in (":\\", ":/"):
                    return Path(value)
    except Exception:  # noqa: BLE001 - ImportError, OSError, a missing hive: the fallback drive answers
        return None
    return None


def _policy_folder(env: Mapping[str, str]) -> Path:
    """The managed-settings folder (or file) ``CLAUDE_CODE_MANAGED_SETTINGS_PATH`` names, else the machine's."""
    named = (env.get("CLAUDE_CODE_MANAGED_SETTINGS_PATH") or "").strip()
    if named:
        return Path(named)
    if _platform.WINDOWS:
        program_files = _program_files()
        return program_files / "ClaudeCode" if program_files is not None else Path(POLICY_FOLDERS["windows"])
    return Path(POLICY_FOLDERS["darwin" if sys.platform == "darwin" else "linux"])


def _file_documents(env: Mapping[str, str]) -> list[dict]:
    """The managed-settings file and its drop-ins; a folder variable that names a file is read as one."""
    folder = _policy_folder(env)
    documents: list[dict] = []
    try:
        if folder.is_file():
            documents.append(_read_document(folder))
            folder = folder.parent
        documents.append(_read_document(folder / "managed-settings.json"))
        drop_ins = folder / "managed-settings.d"
        if drop_ins.is_dir():
            documents.extend(_read_document(path) for path in sorted(drop_ins.glob("*.json")))
    except OSError:
        pass
    return documents


def _registry_documents(hives: tuple[str, ...] = ("HKEY_LOCAL_MACHINE",)) -> list[dict]:
    """The ``Settings`` value of the Windows policy key in each of ``hives`` (the machine's: the user's is no admin
    source); none off Windows or without ``winreg``."""
    if not _platform.WINDOWS:
        return []
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return []
    documents: list[dict] = []
    for hive in (getattr(winreg, name, None) for name in hives):
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, _POLICY_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "Settings")
        except OSError:
            continue
        if isinstance(value, str) and len(value) <= _MAX_BYTES:
            documents.append(_document(value))
    return documents


def _remote_settings_path(env: Mapping[str, str]) -> Path:
    """The server-managed settings Claude Code cached: ``CLAUDE_CODE_REMOTE_SETTINGS_PATH``, else its folder's file."""
    named = (env.get("CLAUDE_CODE_REMOTE_SETTINGS_PATH") or "").strip()
    return Path(named) if named else _workdir.sessions_dir(env).parent / "remote-settings.json"


# --- the rules ---------------------------------------------------------------------------------------------------


def _managed_mcp_json(env: Mapping[str, str], url: str) -> bool:
    """A ``managed-mcp.json`` beside the managed settings that parses and does not name our URL itself."""
    folder = _policy_folder(env)
    base = folder.parent if folder.is_file() else folder
    if not (base / "managed-mcp.json").is_file():
        return False
    servers = _read_document(base / "managed-mcp.json").get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return False  # unparseable, or nothing it would load: Claude Code does not take over with it
    named = {str(entry.get("url", "")).strip().rstrip("/") for entry in servers.values() if isinstance(entry, dict)}
    return url.rstrip("/") not in named


def _sources(env: Mapping[str, str]) -> list[tuple[str, list[dict]]]:
    """The admin sources, highest-ranked first, each with its documents."""
    return [
        ("remote", [_read_document(_remote_settings_path(env))]),
        ("machine", _registry_documents(("HKEY_LOCAL_MACHINE",))),
        ("file", _file_documents(env)),
    ]


def _stale(env: Mapping[str, str]) -> bool:
    try:
        return time.time() - _remote_settings_path(env).stat().st_mtime > _STALE_SECONDS
    except OSError:
        return True


def excluded(env: Mapping[str, str]) -> tuple[str, str]:
    """``(cause, url)`` when the managed settings keep the plugin's server out of this session, else ``("", url)``.
    ``url`` is the expanded MCP url (``""`` when none is configured, which is never a cause). Never raises."""
    url = ""
    try:
        url = _login_store.effective_mcp_url(env)
        if not url:
            return "", ""
        parts = urlsplit(url)
        if not parts.scheme or not parts.hostname:
            return "", url
        parts.port  # noqa: B018 - a port that does not parse raises here, inside the guard
        sources = _sources(env)
        if _denied(sources, url):
            return DENIED, url
        if _managed_mcp_json(env, url):
            return MANAGED_MCP_JSON, url
        if not any(document.get("allowManagedMcpServersOnly") is True for _, documents in sources for document in documents):
            return "", url
        for name, documents in sources:
            values = [document["allowedMcpServers"] for document in documents if "allowedMcpServers" in document]
            if not values:
                continue
            if name == "remote" and _stale(env):
                return "", url
            entries = [entry for value in values for entry in _entries(value)]
            return ("", url) if _admits(entries, url) else (NOT_ALLOWLISTED, url)
        return "", url
    except Exception:  # noqa: BLE001 - _Unknown and anything else: advice nobody can trust is no advice
        return "", url


def _origin(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return f"{parts.scheme}://{parts.netloc}"


def _why(cause: str, url: str) -> str:
    try:
        from session_glossary import HARNESS_NAMES  # noqa: PLC0415 - only the words need it

        harness = HARNESS_NAMES.get("claude", "the harness")
    except Exception:  # noqa: BLE001 - a warning without the name still warns
        harness = "the harness"
    return {
        NOT_ALLOWLISTED: (
            f"your organization's {harness} settings allow only approved MCP servers, and Plurai's server ({url}) does "
            "not appear on that list."
        ),
        DENIED: f"Plurai's server ({url}) is on your organization's blocked MCP list (deniedMcpServers).",
        MANAGED_MCP_JSON: (
            "your organization's managed-mcp.json controls MCP servers on this machine, and plugin servers do not load under it."
        ),
    }[cause]


def _fix(cause: str, url: str) -> str:
    return {
        NOT_ALLOWLISTED: (
            f'The fix is your admin\'s: add {{"serverUrl": "{_origin(url)}/*"}} to allowedMcpServers in managed settings, '
            "then restart the session."
        ),
        DENIED: "The fix is your admin's: remove that entry, then restart the session.",
        MANAGED_MCP_JSON: (
            "The fix is your admin's: approve Plurai through allowedMcpServers instead (see docs/install-shared.md), "
            "then restart the session."
        ),
    }[cause]


def warning(cause: str, url: str) -> str:
    """The ``WARN (backend tools)`` text Step 0 prints above ``ready``: what the settings say, and how to check."""
    return (
        f"{_why(cause, url)} Search your tool list for {TOOL_PREFIX}. If no Plurai tool is there, run ready again with "
        f"--no-backend-tools. {_fix(cause, url)}"
    )


def refusal(cause: str, url: str) -> str:
    """The text of the stop once the model reported the tools missing: a reconnect cannot get past a policy."""
    return f"the Plurai tools are not in this session, and reconnecting cannot bring them: {_why(cause, url)} {_fix(cause, url)}"
