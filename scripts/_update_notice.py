"""The update notice: once a day, the newest release tag of the plugin's public mirror, and the line that says it is newer.

The backend check calls :func:`notice` after the backend accepted the plugin. The read is one HTTPS GET of the mirror's
tag listing through the stamped opener (``_no_redirect.OPENER``): no token, no redirect followed, and nothing about the
customer in the request, which carries only the plugin's own version stamp and user agent. Tags below release rank in the
handshake grammar (``dev``, ``a``, ``b``, ``rc``) are ignored.

State is one file, ``update_notice`` in the plugin's state folder (``$PLURAI_STATE_HOME``, else ``~/.plurai``). Its
modification time is the day stamp and its body holds the newest release the last successful read saw. The stamp is
touched before the read starts, so a read the harness kills still counts as today's, and a read that fails is not retried
until the stamp is a day old. A stamp that cannot be written means no read, so a read-only home never reads on every run.

``PLURAI_NO_UPDATE_NOTICE`` set to a non-empty value turns the read off. ``PLURAI_UPDATE_TAGS_URL`` names another listing
(the tests' stand-in); it is read-only and can carry no secret.

Stdlib only; the python3 floor is 3.9.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

# Importable on its own: the sibling scripts and the hooks' state folder reader come from this file's own location.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import _client_handshake  # noqa: E402
import _install_record  # noqa: E402
import _no_redirect  # noqa: E402
import _suite_window  # noqa: E402

DISABLE_ENV = "PLURAI_NO_UPDATE_NOTICE"
TAGS_URL_ENV = "PLURAI_UPDATE_TAGS_URL"
#: The tag listing of the mirror's repository. Only this first page of 100 tags is read, so a repository with more tags than that
#: is judged by the page it shows; the newest of those tags is taken by version, whatever order the listing uses.
TAGS_URL = "https://api.github.com/repos/plurai-ai/plurai-simulation/tags?per_page=100"
#: Where the stamp and the last answer live, under the state folder.
STATE_FILE = "update_notice"
#: One read per this many seconds.
INTERVAL = 24 * 60 * 60
#: The longest the read may hold the check, whatever the network does; the check's own budget is the cap.
READ_TIMEOUT = 3.0
_MAX_BODY = 1024 * 1024


_MENU = "/plugin > Marketplaces > {} > Enable auto-update."


def state_file() -> Path:
    """The stamp and last answer, in the plugin's state folder (``_suite_window.state_root``)."""
    return _suite_window.state_root() / STATE_FILE


def _enabled(env: Mapping[str, str]) -> bool:
    return not str(env.get(DISABLE_ENV, "")).strip()


def newest_release(listing: Any) -> Optional[str]:  # noqa: ANN401 - whatever JSON the mirror answered
    """The highest release version a tag listing names (a leading ``v`` is dropped), or ``None``.

    The listing's own order says nothing about versions, so every entry is compared by ``version_key``. A tag that is not
    a version the handshake understands, or that is a pre-release, is skipped.
    """
    if not isinstance(listing, list):
        return None
    best: Optional[Tuple[Tuple[int, ...], str]] = None
    for item in listing:
        name = item.get("name") if isinstance(item, dict) else None
        version = name[1:] if isinstance(name, str) and name[:1] in ("v", "V") else name
        key = _client_handshake.release_key(version)
        if key is None:
            continue
        if best is None or key > best[0]:
            best = (key, str(version))
    return best[1] if best else None


def cached_newest(env: Mapping[str, str]) -> Optional[str]:
    """The newest release the last successful read saw, ``None`` when there is none or the read is turned off.

    Never reads the network: a refusal text may name a release the notice already found.
    """
    if not _enabled(env):
        return None
    try:
        saved = json.loads(state_file().read_text(encoding="utf-8")).get("newest")
    except (OSError, ValueError, AttributeError):
        return None
    return saved if _client_handshake.version_key(saved) is not None else None


def _due(path: Path, now: float) -> bool:
    try:
        age = now - path.stat().st_mtime
    except OSError:
        return True
    return not 0 <= age < INTERVAL


def write_state(path: Path, newest: Optional[str]) -> bool:
    """Write the stamp (the file's modification time) and the newest release seen; ``False`` when the file cannot be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        scratch.write_text(json.dumps({"newest": newest}), encoding="utf-8")
        os.replace(scratch, path)
    except OSError:
        return False
    return True


def _fetch(url: str, timeout: float) -> Optional[str]:
    """The newest release of the listing at ``url``, or ``None`` on any failure: a failed read says nothing.

    The read runs in a daemon thread and is abandoned after ``timeout`` plus a margin, so a stalled name lookup, which a
    socket timeout does not cover, cannot hold the check.
    """
    found: List[Optional[str]] = [None]

    def read() -> None:
        try:
            request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
            with _no_redirect.OPENER.open(request, timeout=timeout) as response:  # noqa: S310 - a fixed https listing, or the tests' loopback one
                found[0] = newest_release(json.loads(response.read(_MAX_BODY).decode("utf-8", "replace")))
        except Exception:  # noqa: BLE001 - no verdict: the notice is best effort
            return

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout + 1.0)
    return found[0]


def notice_text(newest: str, installed: str, harness: str, marketplaces: List[str]) -> str:
    """The notice: the newer version, the installed one, and how to update; never an error word."""
    lines = [f"A newer Plurai plugin is available: {newest} (you have {installed})."]
    command = _client_handshake.update_command(harness, marketplaces)
    if harness == _client_handshake.CLAUDE and marketplaces:
        lines += [
            f"Turn on updates once: {_MENU.format(marketplaces[0])}",
            f"Or update now: {command}",
        ]
    else:
        lines.append(f"Update: {command}")
    return "\n".join(lines)


def notice_for(env: Mapping[str, str], harness: str, deadline: float) -> Optional[str]:
    """The notice to print, or ``None``. Reads the mirror at most once a day; ``deadline`` is a ``time.monotonic`` instant the read stays inside."""
    installed = _client_handshake.manifest_version()
    installed_key = _client_handshake.version_key(installed)
    if installed is None or installed_key is None or _client_handshake.is_dev(installed) or not _enabled(env):
        return None
    path = state_file()
    if not _due(path, time.time()):
        return None
    budget = min(READ_TIMEOUT, deadline - time.monotonic() - 1.0)
    if budget <= 0:
        return None
    if not write_state(path, cached_newest(env)):
        return None
    newest = _fetch(str(env.get(TAGS_URL_ENV) or TAGS_URL), budget)
    newest_key = _client_handshake.version_key(newest)
    if newest is None or newest_key is None:
        return None
    write_state(path, newest)
    if newest_key <= installed_key:
        return None
    marketplaces = _install_record.installed_marketplaces(harness, _client_handshake.CLIENT_NAME, _client_handshake.PLUGIN_ROOT, env)
    return notice_text(newest, installed, harness, marketplaces)
