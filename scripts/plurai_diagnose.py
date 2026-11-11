#!/usr/bin/env python3
"""Print the bundle a user pastes into a Plurai support issue: the plugin's facts, then the SDK's.

    python3 <plugin root>/scripts/plurai_diagnose.py

Plurai clients send no telemetry, so what the user pastes is all support has. The bundle opens with what only the
plugin knows (its version, the harness, the operating system and Python). Then, when a usable SDK that has
``plurai diagnose`` is installed, it runs that command in the current directory and prints its bundle. Otherwise it
says the SDK is missing or too old, names the install line, and prints what the plugin can see itself: whether a
credentials file, ``PLURAI_URL`` and ``PLURAI_TOKEN`` are present (never their values), and the newest local support
references (``hooks/_support_refs.py``), which is where a failed install and a refused hook decision leave their ref.

Nothing in the text comes from the user's project. Every line is built from a version, a presence flag, a url origin or
a reference this plugin wrote, and the finished text has the saved and the exported token removed, the SDK's output
included.

Standard library only, so it works before the SDK is installed. Exit 0 whenever a bundle was printed.
"""

from __future__ import annotations

import contextlib
import io
import os
import platform
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

_PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN / "hooks"))
sys.path.insert(0, str(_PLUGIN / "scripts"))

import _client_handshake
import _command_run
import _login_store
import _support_refs
import _workdir
import resolve_plurai
from _stdio import utf8_child_text
from session_glossary import HARNESS_NAMES

#: The SDK's own bundle is printed whole; this bounds how long it may take and how much of it is kept.
_SDK_TIMEOUT_SECONDS = 60
_SDK_MAX_CHARS = 20000
_REFERENCES = 10
_MIN_SECRET_CHARS = 8


def plugin_version() -> str:
    version = _client_handshake.plugin_version(_PLUGIN)
    return "unknown" if version == _client_handshake.UNKNOWN_VERSION else version


def _origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url``: userinfo, path and query are dropped. A value that is no url reads ``unreadable``."""
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return "unreadable"
    if parts.scheme not in ("http", "https") or not host:
        return "unreadable"
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _credential_lines(saved: _login_store.Credential) -> list[str]:
    if _login_store.home_problem():
        file_line = "Credentials file: not looked for (HOME is not the account home)"
    elif saved.token:
        file_line = f"Credentials file: present (backend {_origin(saved.url)})"
    elif saved.problem:
        file_line = "Credentials file: present but unusable (run plurai_login.py --status for the reason)"
    else:
        file_line = "Credentials file: absent"
    url = os.environ.get("PLURAI_URL", "").strip()
    return [
        file_line,
        f"PLURAI_URL: present (origin {_origin(url)})" if url else "PLURAI_URL: absent",
        "PLURAI_TOKEN: present" if os.environ.get("PLURAI_TOKEN", "").strip() else "PLURAI_TOKEN: absent",
    ]


def _reference_lines() -> list[str]:
    rows = _support_refs.newest(_REFERENCES)
    if not rows:
        return ["Local references: none"]
    return [
        "Local references (newest first):",
        *(f"  - {row['at']} {row['kind']} {row['ref']}" + (f": {row['summary']}" if row["summary"] else "") for row in rows),
    ]


def _sdk_env() -> dict[str, str]:
    """The environment the SDK's bundle runs in: ours, with the id of the command in progress when the plugin minted one,
    so the bundle names the command the user is reporting on (the SDK never reads the plugin's flow directory)."""
    env = dict(os.environ)
    run_id = _command_run.current()
    if run_id:
        env[_command_run.ENV_VAR] = run_id
    return env


def _sdk_bundle() -> tuple[str | None, str]:
    """``(bundle, why_not)``: the SDK's bundle, or ``None`` and a one-line reason."""
    with contextlib.redirect_stderr(io.StringIO()):
        path, _code = resolve_plurai.resolve(required=(*resolve_plurai._DEFAULT_REQUIRED, "diagnose"))
    if path is None:
        return None, "SDK: no usable `plurai diagnose` was found (the SDK is missing, too old or refused)"
    try:
        done = subprocess.run(
            [path, "diagnose", "--workdir", os.getcwd()],
            capture_output=True,
            text=True,
            **utf8_child_text(),
            timeout=_SDK_TIMEOUT_SECONDS,
            check=False,
            env=_sdk_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, "SDK: `plurai diagnose` could not be run"
    if done.returncode != 0:
        return None, f"SDK: `plurai diagnose` exited {done.returncode}"
    return done.stdout[:_SDK_MAX_CHARS].rstrip(), ""


def _without_tokens(text: str, saved: _login_store.Credential) -> str:
    for secret in {os.environ.get("PLURAI_TOKEN", "").strip(), saved.token}:
        if len(secret) >= _MIN_SECRET_CHARS:
            text = text.replace(secret, "<token redacted>")
    return text


def render() -> str:
    saved = _login_store.read_file() if not _login_store.home_problem() else _login_store.Credential()
    harness = HARNESS_NAMES.get(_workdir.harness(os.environ), "unknown")
    lines = [
        "--- plurai diagnostic bundle, plugin section (no token, no credential value, no project file content) ---",
        f"plurai-simulation plugin: {plugin_version()}",
        f"Harness: {harness}",
        f"OS: {platform.system()} {platform.release()} {platform.machine()}",
        f"Python: {platform.python_version()} (the python3 the plugin's scripts run under)",
        f"Command run id: {_command_run.current() or 'none in progress'}",
    ]
    sdk, why_not = _sdk_bundle()
    if sdk is not None:
        lines.append("--- end of plugin section ---")
        lines.append(sdk)
    else:
        lines += [
            why_not,
            f"Install the SDK: {resolve_plurai.INSTALL_LINE}",
            *_credential_lines(saved),
            *_reference_lines(),
            "--- end of bundle ---",
        ]
    return _without_tokens("\n".join(lines), saved)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: plurai_diagnose.py (takes no arguments)", file=sys.stderr)
        return 64
    print(render())
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
