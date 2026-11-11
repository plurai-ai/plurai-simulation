"""Which marketplaces a harness's install record names as providing a plugin.

The update command of a refusal names the marketplace the running install came from, because a harness refreshes a
marketplace's clone only on request and a plugin name alone can be ambiguous when two marketplaces provide it. Each
harness keeps its own record; every reader is best-effort and an unreadable or missing record gives no names, so a
refusal is never held back by a record it cannot read.

Stdlib only; the python3 floor is 3.9.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Set, Tuple

#: A marketplace name is printed in a command to copy, so only a plain name is.
_MARKETPLACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)

#: What a reader finds: every marketplace that provides the plugin, and those whose install folder holds the running copy.
_Found = Tuple[Set[str], List[str]]


def _home(env: Mapping[str, str], variable: str, folder: str) -> Path:
    named = str(env.get(variable, "")).strip()
    return Path(named) if named else Path(env.get("HOME") or os.path.expanduser("~")) / folder


def _inside(path: Path, folder: object) -> bool:
    """Whether ``path`` is ``folder`` or below it, links resolved on both sides."""
    if not folder:
        return False
    try:
        path.resolve().relative_to(Path(str(folder)).resolve())
    except (OSError, ValueError):
        return False
    return True


def _claude(plugin: str, root: Path, env: Mapping[str, str]) -> _Found:
    """``<config dir>/plugins/installed_plugins.json``: ``plugins`` maps ``<plugin>@<marketplace>`` to its installs."""
    record = json.loads((_home(env, "CLAUDE_CONFIG_DIR", ".claude") / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    names: Set[str] = set()
    running: List[str] = []
    for key, installs in record["plugins"].items():
        name, _, marketplace = str(key).partition("@")
        if name != plugin or not marketplace:
            continue
        names.add(marketplace)
        if any(_inside(root, item.get("installPath")) for item in installs if isinstance(item, dict)):
            running.append(marketplace)
    return names, running


def _codex(plugin: str, root: Path, env: Mapping[str, str]) -> _Found:
    """``<codex home>/config.toml``: a ``[plugins."<plugin>@<marketplace>"]`` table per installed plugin, one with
    ``enabled = false`` not counted; the copy that runs sits under ``<codex home>/plugins/cache/<marketplace>/<plugin>``."""
    home = _home(env, "CODEX_HOME", ".codex")
    text = (home / "config.toml").read_text(encoding="utf-8")
    header = re.compile(r'^\[plugins\.["\']{}@([^"\'\]]+)["\']\][ \t]*$'.format(re.escape(plugin)))
    names: Set[str] = set()
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            found = header.match(stripped)
            current = found.group(1) if found else None
            if current:
                names.add(current)
        elif current and re.fullmatch(r"enabled\s*=\s*false\s*(#.*)?", stripped):
            names.discard(current)
            current = None
    return names, [name for name in names if _inside(root, home / "plugins" / "cache" / name / plugin)]


def _copilot(plugin: str, root: Path, env: Mapping[str, str]) -> _Found:
    """``<copilot home>/installed-plugins/<marketplace>/<plugin>``: one folder per install."""
    installed = _home(env, "COPILOT_HOME", ".copilot") / "installed-plugins"
    names = {folder.name for folder in installed.iterdir() if (folder / plugin).is_dir()}
    return names, [name for name in names if _inside(root, installed / name / plugin)]


#: The reader of each harness, keyed by the names ``_client_handshake.HARNESSES`` lists.
READERS: Dict[str, Callable[[str, Path, Mapping[str, str]], _Found]] = {"claude": _claude, "codex": _codex, "copilot": _copilot}


def installed_marketplaces(harness: str, plugin: str, root: Path, env: Mapping[str, str]) -> List[str]:
    """The marketplaces ``harness`` records as providing ``plugin``: the one the copy at ``root`` runs from first, then the
    others sorted; ``[]`` when the record is missing or unreadable.
    """
    try:
        names, running = READERS[harness](plugin, root, env)
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return []
    return sorted((name for name in names if _MARKETPLACE_NAME.fullmatch(name)), key=lambda name: (name not in running, name))

