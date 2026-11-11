"""Which of the plugin's hooks Codex has trusted, read from the user's ``config.toml``.

Codex runs a plugin hook only after the user trusts it, and records the trust in
``[hooks.state."<plugin>@<marketplace>:<hooks file>:<event>:<group>:<hook>"]`` with a
``trusted_hash``. This module recomputes each key and hash for a hooks file and compares them with
what the config holds.

The format, as Codex writes it:

* the key's ``<hooks file>`` is the path inside the plugin of the file the manifest's ``hooks``
  field names, ``hooks/hooks.json`` when it names none; ``<event>`` is the event in snake case;
  ``<group>`` and ``<hook>`` are the zero-based positions of the matcher group in the event's array
  and of the hook in the group's ``hooks``;
* ``trusted_hash`` is ``sha256:`` plus the SHA-256 of the compact, key-sorted JSON of
  ``{"event_name", "matcher" (left out when the group has none), "hooks": [{"async": false,
  "command" as written, "timeout", "type": "command", "statusMessage" only when set}]}``. The timeout
  is the written one, 600 when absent, and at most 3 for ``SessionEnd`` and ``Interrupt``, which Codex
  clamps with a warning. The command is the one Codex runs on this OS (``commandWindows`` on Windows, when the
  entry has one), hashed with ``${CLAUDE_PLUGIN_ROOT}`` still literal, so on the measured versions a plugin update that
  leaves the hooks file alone keeps every hook trusted. A newer Codex also records a fingerprint of the plugin's files,
  apart from ``trusted_hash``, as ``trusted_content_hash``, and runs the hook only while the folder still matches it:
  ``plugin_content_hash`` computes that fingerprint (see there), and a hook whose entry holds one is armed only when it
  matches;
* a hook with ``enabled = false`` does not run whatever its hash.

The format is Codex's, measured on the versions the plugin pins. The recorded ``config.toml`` files
under ``scripts/tests/fixtures/harness_payloads/codex-0.160.0`` are the evidence: a test reproduces
every key and hash in them.

    python3 _codex_trust.py <config.toml> <plugin@marketplace> <hooks file> [<path in plugin>]

prints each hook's key as ``armed`` or ``NOT ARMED`` and exits 1 when any is not armed (the plugin folder is the hooks file's
path with <path in plugin> taken off; where that does not fit, an entry that holds a ``trusted_content_hash`` is not armed). A file it cannot read
gets one line on stderr naming the file and the reason, and exit 2.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import _platform
import _toml_min
from _harness import wire

DEFAULT_TIMEOUT = 600
#: The events whose hook timeout Codex clamps, and the clamp in seconds. The hash covers the clamped value.
CLAMPED_EVENTS = ("SessionEnd", "Interrupt")
CLAMPED_TIMEOUT = 3

ARMED = "armed"
MISSING = "missing"
CHANGED = "changed"
SWITCHED_OFF = "switched off"
#: The plugin's files differ from the fingerprint the hook was approved with, or the fingerprint cannot be checked.
PLUGIN_CHANGED = "plugin files changed"


def running_command(hook: dict[str, Any], windows: bool | None = None) -> str:
    """The command Codex runs, and so hashes, for ``hook`` on this OS: ``commandWindows`` on Windows when the entry has one."""
    if _platform.WINDOWS if windows is None else windows:
        return str(hook.get("commandWindows") or hook["command"])
    return str(hook["command"])


def hook_hash(event: str, matcher: Any, hook: dict[str, Any], windows: bool | None = None) -> str:
    timeout = hook.get("timeout", DEFAULT_TIMEOUT)
    if event in CLAMPED_EVENTS:
        timeout = min(timeout, CLAMPED_TIMEOUT)
    handler = {"async": False, "command": running_command(hook, windows), "timeout": timeout, "type": "command"}
    if "statusMessage" in hook:
        handler["statusMessage"] = hook["statusMessage"]
    identity: dict[str, Any] = {"event_name": wire.snake(event), "hooks": [handler]}
    if matcher is not None:
        identity["matcher"] = matcher
    text = json.dumps(identity, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


#: What Codex skips when it fingerprints a plugin folder: a file or folder of one of these names, at any depth.
_FINGERPRINT_SKIPPED_NAMES = ("__pycache__", ".venv")
_FINGERPRINT_SKIPPED_SUFFIXES = (".pyc", ".egg-info")
_FINGERPRINT_PREFIX = b"codex-plugin-bundle-v1"
_CHUNK = 64 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _os_bytes(text: str) -> bytes:
    """``text`` as Codex's ``OsStr::as_encoded_bytes`` has it: the file system's bytes on POSIX, WTF-8 on Windows."""
    return text.encode("utf-8", "surrogatepass") if os.name == "nt" else os.fsencode(text)


def _is_skipped(name: str) -> bool:
    """Whether Codex leaves out an entry of this name. It tests the name as UTF-8 text, so a name that is not valid UTF-8 is never skipped."""
    if not name:
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return name in _FINGERPRINT_SKIPPED_NAMES or name.endswith(_FINGERPRINT_SKIPPED_SUFFIXES)


def plugin_content_hash(root: Path) -> str:
    """Codex's fingerprint of the plugin folder ``root``: ``sha256:`` and the SHA-256 over ``codex-plugin-bundle-v1`` and,
    for every entry from the root down (a folder's entries in the byte order of their names, depth first), the
    little-endian 64-bit length of its path relative to the root and that path (empty for the root), then ``directory``
    or ``file``, a file's little-endian 64-bit length and its bytes. ``__pycache__``, ``.venv``, ``*.pyc`` and
    ``*.egg-info`` are left out. A symlink, a special file, an unreadable entry or a file that changes while it is read
    raises ``OSError``: Codex then has no fingerprint either.
    """
    digest = hashlib.sha256(_FINGERPRINT_PREFIX)
    if not stat.S_ISDIR(os.lstat(root).st_mode):
        raise OSError(f"{root} is not a folder")
    pending = [root]
    while pending:
        path = pending.pop()
        relative = path.relative_to(root)
        if _is_skipped(relative.name):
            continue
        name = _os_bytes(str(relative)) if relative.parts else b""
        digest.update(len(name).to_bytes(8, "little") + name)
        mode = os.lstat(path).st_mode
        if stat.S_ISDIR(mode):
            digest.update(b"directory")
            entries = sorted(os.listdir(path), key=_os_bytes)
            pending.extend(path / entry for entry in reversed(entries))
        elif stat.S_ISREG(mode):
            size = os.lstat(path).st_size
            digest.update(b"file" + size.to_bytes(8, "little"))
            read = 0
            with open(path, "rb") as handle:
                while chunk := handle.read(_CHUNK):
                    read += len(chunk)
                    digest.update(chunk)
                    if read > size:
                        break
            if read != size:
                raise OSError(f"{path} changed while it was read")
        else:
            raise OSError(f"{path} is not a file or a folder")
    return "sha256:" + digest.hexdigest()


def expected_state(plugin: str, hooks_json: dict[str, Any], source: str, windows: bool | None = None) -> dict[str, tuple[str, str]]:
    """Each trust key of ``hooks_json``, mapped to ``(hash that makes it trusted, the hook's label)``.

    ``plugin`` is ``<plugin>@<marketplace>`` and ``source`` the hooks file's path inside the plugin.
    The label names the hook for a person: its event and the last word of its command.
    """
    expected = {}
    for event, groups in hooks_json.get("hooks", {}).items():
        for group_index, group in enumerate(groups):
            for hook_index, hook in enumerate(group.get("hooks", [])):
                key = f"{plugin}:{source}:{wire.snake(event)}:{group_index}:{hook_index}"
                label = f"{event} {(running_command(hook, windows).split() or ['?'])[-1]}"
                expected[key] = (hook_hash(event, group.get("matcher"), hook, windows), label)
    return expected


def cached_fingerprint(root: Path | None) -> Callable[[], str]:
    """A function that gives the fingerprint of ``root`` and computes it once, whatever its answer: a caller that asks
    for several marketplaces' hooks hashes the folder one time. It raises what ``plugin_content_hash`` raises, each time it is asked,
    and ``OSError`` when there is no folder.
    """
    outcome: list[Any] = []

    def get() -> str:
        if not outcome:
            try:
                outcome.append(plugin_content_hash(root) if root is not None else OSError("no plugin folder to fingerprint"))
            except (OSError, ValueError, MemoryError) as problem:
                outcome.append(problem)
        if isinstance(outcome[0], BaseException):
            raise outcome[0]
        return str(outcome[0])

    return get


def status(
    config: dict[str, Any],
    expected: dict[str, tuple[str, str]],
    plugin_root: Path | None = None,
    fingerprint: Callable[[], str] | None = None,
) -> dict[str, str]:
    """Each trust key's standing in ``config``: armed, missing, changed, switched off or plugin files changed.

    A state entry that holds a ``trusted_content_hash`` is armed only when it is a fingerprint and equals the one of
    ``plugin_root`` now. A fingerprint that differs, that cannot be computed, or a value that is not a fingerprint (or no
    ``plugin_root`` to compute it from) gives ``PLUGIN_CHANGED``: Codex does not run such a hook, so the answer is never armed.
    ``fingerprint`` is a function that gives the folder's (``cached_fingerprint``): a caller that asks twice passes one, so the folder is hashed once.
    """
    if fingerprint is None:
        fingerprint = cached_fingerprint(plugin_root)
    state = config.get("hooks", {}).get("state", {}) if isinstance(config.get("hooks"), dict) else {}
    if not isinstance(state, dict):
        state = {}
    out = {}
    for key, (digest, _label) in expected.items():
        entry = state.get(key)
        if not isinstance(entry, dict):
            out[key] = MISSING
        elif entry.get("enabled", True) is not True:
            out[key] = SWITCHED_OFF
        elif entry.get("trusted_hash") != digest:
            out[key] = CHANGED
        elif "trusted_content_hash" in entry:
            recorded = entry["trusted_content_hash"]
            try:
                current = fingerprint()
            except (OSError, ValueError, MemoryError):
                current = ""
            matches = isinstance(recorded, str) and bool(_DIGEST.fullmatch(recorded)) and recorded == current
            out[key] = ARMED if matches else PLUGIN_CHANGED
        else:
            out[key] = ARMED
    return out


def marketplaces(config: dict[str, Any], plugin: str) -> list[str]:
    """The marketplaces ``plugin`` has any hook state under, sorted. The user chooses the marketplace's name."""
    state = config.get("hooks", {}).get("state", {}) if isinstance(config.get("hooks"), dict) else {}
    found = set()
    for key in state if isinstance(state, dict) else ():
        head = str(key).split(":", 1)[0]
        if head.startswith(plugin + "@"):
            found.add(head[len(plugin) + 1 :])
    return sorted(found)


def _cannot_read(path: str, problem: Exception) -> int:
    """Say on stderr which file failed and why, without quoting its text, which can hold a token; exit code 2."""
    if isinstance(problem, OSError):
        reason = problem.strerror or type(problem).__name__
    elif isinstance(problem, UnicodeDecodeError):
        reason = "not valid UTF-8"
    elif isinstance(problem, json.JSONDecodeError):
        reason = f"{problem.msg} at line {problem.lineno}"
    else:
        reason = str(problem)
    print(f"_codex_trust: cannot read {path}: {reason}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: _codex_trust.py <config.toml> <plugin@marketplace> <hooks file> [<path in plugin>]", file=sys.stderr)
        return 64
    try:
        config = _toml_min.loads(Path(argv[0]).read_text(encoding="utf-8-sig"))
    except (_toml_min.Unreadable, OSError, UnicodeDecodeError) as problem:
        return _cannot_read(argv[0], problem)
    try:
        hooks = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as problem:
        return _cannot_read(argv[2], problem)
    source = argv[3] if len(argv) == 4 else "hooks/hooks.json"
    expected = expected_state(argv[1], hooks, source)
    hooks_file = Path(argv[2]).resolve()
    depth = len(Path(source).parts)
    root = hooks_file.parents[depth - 1] if hooks_file.parts[-depth:] == Path(source).parts else None
    standing = status(config, expected, root)
    for key, state in standing.items():
        print("armed    " if state == ARMED else "NOT ARMED", key)
    return 0 if all(state == ARMED for state in standing.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
