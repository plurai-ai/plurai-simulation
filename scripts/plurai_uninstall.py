#!/usr/bin/env python3
"""List and remove what the Plurai simulation plugin writes outside its own install folder.

    plurai_uninstall.py plan   [--project DIR] [--home DIR]
    plurai_uninstall.py remove [--project DIR] [--home DIR] --groups GROUP[,GROUP...]

``plan`` prints what exists, per group, and removes nothing. ``remove`` deletes the named groups and
nothing else; there is no default group, so a caller has to name each one it was told to remove.

The groups, and where each one lives:

* ``project``: the generated files in the project. Each ``plurai_sim/`` workdir, the managed
  ``plurai-simulation`` block in a ``.gitignore`` (and the legacy ``plurai_sim`` lines it replaced),
  and the generated Agentforce trees.
* ``project-state``: ``.plurai/`` folders in the project (upload previews, the upload audit log,
  gate files).
* ``home``: the plugin's state in the user's home: the per-session flow markers, the session pins, the suite windows, the
  plugin-owned tool venvs, the local support references (``support_refs.jsonl``), the time of the last server connection (``mcp_connected``), the update notice's day stamp (``update_notice``), and the url-split markers in
  the temp directory.
* ``login``: the saved credential, ``credentials.json``. A group of its own because deleting it is how a
  user signs out of this machine, which they may want to keep.

``FOOTPRINT`` is the one list of what the plugin writes. The README's file list is checked against it,
and the tests drive the real writers and then this script, so a new writer that is not listed here shows
up as a leftover.

What it never touches: the agent's source, ``.env`` files, virtualenvs, a ``.gitignore`` line that is not
the plugin's, the plugin's own install folder (the harness removes that), and anything on the backend.
It never follows a symlink: a link is unlinked, the folder it points to is left alone.

Stdlib only; runs under a stock ``python3`` (3.9). Exit 0 done, 2 a usage error, 1 a path could not be removed.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))

import _login_store

GROUPS = ("project", "project-state", "home", "login")
GROUP_TITLES = {
    "project": "generated project files",
    "project-state": "plugin state in the project",
    "home": "plugin state in your home folder",
    "login": "the saved login",
}


@dataclass(frozen=True)
class Entry:
    """One thing the plugin writes: where, which group removes it, and what it is."""

    group: str
    where: str
    path: str
    what: str


#: Everything the plugin writes outside its install folder. ``path`` is the spelling the README uses.
FOOTPRINT: tuple[Entry, ...] = (
    Entry(
        "project",
        "inside the project",
        "plurai_sim/",
        "the workdir beside the agent: simulate.py, plurai.config.yaml, simulation_prd.md, the mocks, scenarios, evals, artifacts and runs",
    ),
    Entry(
        "project",
        "inside the project",
        ".gitignore",
        "one managed block between `# BEGIN plurai-simulation` and `# END plurai-simulation`, and no other line",
    ),
    Entry(
        "project",
        "inside the project",
        "generated/",
        "Agentforce only: the rewired copy of the agent and its generated Apex, under the package folder and the DX root",
    ),
    Entry("project-state", "inside the project", ".plurai/", "upload previews, the upload audit log and gate files"),
    Entry("home", "outside the project", "~/.plurai/flow/", "one folder per session with the flow markers"),
    Entry("home", "outside the project", "~/.plurai/pins/", "one file per running session: the backend the session was started with and, on Windows, its Python"),
    Entry("home", "outside the project", "~/.plurai/suite_windows/", "one file per workdir that claims the account window"),
    Entry("home", "outside the project", "~/.plurai/poll_unreachable/", "one marker per job while the backend cannot be reached"),
    Entry("home", "outside the project", "~/.plurai/tools/", "plugin-owned virtualenvs for the contract checks"),
    Entry("home", "outside the project", "~/.plurai/support_refs.jsonl", "the local references a failed install or a refused hook decision leaves for a support issue"),
    Entry("home", "outside the project", "~/.plurai/mcp_connected", "the time Claude Code last connected the Plurai server, which the setup's reconnect wait reads"),
    Entry("home", "outside the project", "~/.plurai/update_notice", "the day the update notice last read the public tag list, and the newest release it saw"),
    Entry("home", "outside the project", ".plurai_url_split_*", "one marker a day in the temp folder"),
    Entry(
        "login",
        "outside the project",
        "~/.plurai/credentials.json",
        "the backend url and API key that `plurai_login.py` saves (mode 0600)",
    ),
)

#: Folders no walk enters: another tool's tree, or one that holds installed code and never a workdir.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        "dist",
        "build",
    }
)
_MAX_DEPTH = 8
_BLOCK_BEGIN = re.compile(r"^# BEGIN plurai-simulation\b")
_BLOCK_END = re.compile(r"^# END plurai-simulation\b")
#: A legacy ignore rule of the plugin: a pattern with ``plurai_sim`` as a whole path segment (``plurai_sim/artifacts/``,
#: ``**/plurai_sim/**/runs/``). A negation, or a name that only contains the word, is the user's.
_LEGACY_RULE = re.compile(r"(?:^|/)plurai_sim(?:/|$)")
_AGENTFORCE_MARKERS = ("aiAuthoringBundles/*_Plurai", "namedCredentials/Plurai_Mock*")
_DX_FILES = ("apex/plurai_mock_health.apex", "manifest/package.xml", "manifest/destructiveChanges.xml")


@dataclass
class Item:
    """One removal: a whole path, or a rewrite of a ``.gitignore``."""

    path: Path
    describe: str
    rewrite: str | None = None  # the new text of a .gitignore; None removes the path
    root: Path | None = None  # the folder the path must stay under, with no link in between (see `_contained`)


@dataclass
class Plan:
    items: dict[str, list[Item]] = field(default_factory=lambda: {group: [] for group in GROUPS})
    skipped: list[str] = field(default_factory=list)  # found, but not safe to touch, with the reason

    def add(self, group: str, item: Item) -> None:
        self.items[group].append(item)


def _contained(path: Path, root: Path, *, final_link_ok: bool) -> bool:
    """Whether ``path`` is below ``root`` with no symbolic link on the way.

    A link in a parent would carry a write or a removal out of ``root``. The last component may be a link when the
    caller only unlinks it (the link goes, its target stays); a rewrite never takes that allowance.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    current = root
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink() and not (final_link_ok and index == len(parts) - 1):
            return False
    return True


def _is_link_or_file(path: Path) -> bool:
    return path.is_symlink() or path.is_file()


def _describe_dir(path: Path) -> str:
    if path.is_symlink():
        return f"{path} (a link: only the link is removed)"
    count = sum(len(files) for _, _, files in os.walk(path))
    return f"{path} ({count} files)"


def _walk(project: Path, unscanned: list[str]):
    """``(directory, subdirectory names, file names)`` below ``project``. Subfolders that are links are listed (a linked
    ``plurai_sim`` is the plugin's to unlink) but not entered, and neither are `_SKIP_DIRS` or a ``plurai_sim``.
    A folder the walk cannot read, or that lies below the depth cap, is added to ``unscanned``."""
    base_depth = len(project.parts)

    def unreadable(error: OSError) -> None:
        unscanned.append(f"{error.filename} ({error.strerror}: not scanned)")

    for current, dirs, files in os.walk(project, followlinks=False, onerror=unreadable):
        here = Path(current)
        listed = sorted(d for d in dirs if d not in _SKIP_DIRS)
        yield here, listed, sorted(files)
        if len(here.parts) - base_depth >= _MAX_DEPTH:
            unscanned.extend(f"{here / d} (deeper than {_MAX_DEPTH} levels: not scanned)" for d in listed if d != "plurai_sim")
            dirs[:] = []
        else:
            dirs[:] = [d for d in listed if d != "plurai_sim" and not (here / d).is_symlink()]


def _gitignore_rewrite(text: str) -> str | None:
    """``text`` without the plugin's managed blocks and legacy ``plurai_sim`` lines; None when there is nothing to remove
    or a block has no end line (the rest of the file is not ours to guess at)."""
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        if _BLOCK_BEGIN.match(line):
            end = next((j for j in range(index + 1, len(lines)) if _BLOCK_END.match(lines[j])), None)
            if end is None:
                return None
            changed = True
            index = end + 1
            # The blank line that separated the block from its neighbour goes with it.
            if kept and not kept[-1].strip():
                kept.pop()
            elif index < len(lines) and not lines[index].strip():
                index += 1
            continue
        rule = line.strip()
        if not rule.startswith(("#", "!")) and _LEGACY_RULE.search(rule):
            changed = True
        else:
            kept.append(line)
        index += 1
    return "".join(kept) if changed else None


def _agentforce_trees(directory: Path, dirs: list[str]) -> list[Path]:
    """The generated trees that belong to the plugin, under a folder that has a ``generated`` child."""
    if "generated" not in dirs:
        return []
    generated = directory / "generated"
    if generated.is_symlink():
        return []
    if any(any(generated.glob(pattern)) for pattern in _AGENTFORCE_MARKERS):
        return [generated]
    return [
        generated / rel
        for rel in _DX_FILES
        if (generated / "apex" / "plurai_mock_health.apex").exists() and (generated / rel).exists()
    ]


def home_roots(home: Path | None, env: dict[str, str]) -> list[Path]:
    """The folders the plugin keeps state in: HOME's, the account's, and ``PLURAI_STATE_HOME``."""
    if home is not None:
        return [home / ".plurai"]
    roots = [Path.home() / ".plurai", _login_store.account_home() / ".plurai"]
    if env.get("PLURAI_STATE_HOME"):
        roots.append(Path(env["PLURAI_STATE_HOME"]))
    return list(dict.fromkeys(roots))


def build_plan(project: Path, home: Path | None = None, env: dict[str, str] | None = None) -> Plan:
    env = dict(os.environ) if env is None else env
    plan = Plan()
    state_homes = {root.resolve() for root in home_roots(home, env)}
    for directory, dirs, files in _walk(project, plan.skipped):
        if "plurai_sim" in dirs:
            plan.add("project", Item(directory / "plurai_sim", _describe_dir(directory / "plurai_sim"), root=project))
        for tree in _agentforce_trees(directory, dirs):
            plan.add("project", Item(tree, f"{tree} (generated Agentforce files)", root=project))
        if ".gitignore" in files:
            path = directory / ".gitignore"
            if path.is_symlink():
                plan.skipped.append(f"{path} (a symbolic link: not read or rewritten; edit its target yourself)")
            elif (new_text := _gitignore_rewrite(path.read_text(encoding="utf-8", errors="replace"))) is not None:
                plan.add("project", Item(path, f"{path} (the plugin's lines only)", rewrite=new_text, root=project))
        if ".plurai" in dirs and (directory / ".plurai").resolve() not in state_homes:
            plan.add("project-state", Item(directory / ".plurai", _describe_dir(directory / ".plurai"), root=project))
    for root in home_roots(home, env):
        for name in ("flow", "pins", "suite_windows", "poll_unreachable", "tools", "support_refs.jsonl", "mcp_connected", "update_notice"):
            if (root / name).is_symlink() or (root / name).exists():
                plan.add(
                    "home",
                    Item(
                        root / name,
                        _describe_dir(root / name)
                        if (root / name).is_dir() and not (root / name).is_symlink()
                        else str(root / name),
                        root=root,
                    ),
                )
        if (root / "credentials.json").is_symlink() or (root / "credentials.json").exists():
            plan.add("login", Item(root / "credentials.json", str(root / "credentials.json"), root=root))
    temp = Path(tempfile.gettempdir())
    if temp.is_dir():
        for marker in sorted(temp.glob(".plurai_url_split_*")):
            plan.add("home", Item(marker, str(marker), root=temp))
    return plan


def _remove_path(path: Path) -> None:
    if _is_link_or_file(path):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _prune_empty(path: Path, stop: Path) -> None:
    """Remove ``path`` and its parents up to (not including) ``stop`` while they are empty."""
    while path != stop and path.is_dir() and not any(path.iterdir()):
        path.rmdir()
        path = path.parent


def apply(
    plan: Plan, groups: list[str], project: Path, home: Path | None = None, env: dict[str, str] | None = None
) -> tuple[list[str], list[str]]:
    """Remove the items of ``groups``; return ``(removed, failed)`` as printable rows."""
    removed: list[str] = []
    failed: list[str] = []
    for group in groups:
        for item in plan.items[group]:
            if item.root is None or not _contained(item.path, item.root, final_link_ok=item.rewrite is None):
                failed.append(f"{item.path}: refused, a symbolic link on its path leads out of {item.root}")
                continue
            try:
                if item.rewrite is not None:
                    if item.rewrite.strip():
                        item.path.write_text(item.rewrite, encoding="utf-8")
                        removed.append(f"{item.path} (the plugin's lines)")
                    else:
                        item.path.unlink()
                        removed.append(f"{item.path} (empty after the plugin's lines went)")
                else:
                    _remove_path(item.path)
                    removed.append(str(item.path))
                    if group == "project" and "generated" in item.path.parts:
                        _prune_empty(item.path.parent, project)
            except OSError as error:
                failed.append(f"{item.path}: {error}")
    for root in home_roots(home, dict(os.environ) if env is None else env):
        try:
            if root.is_dir() and not root.is_symlink() and not any(root.iterdir()):
                root.rmdir()
                removed.append(f"{root} (empty)")
        except OSError as error:
            failed.append(f"{root}: {error}")
    return removed, failed


def _project_root(given: str | None) -> Path:
    if given:
        return Path(given).resolve()
    import subprocess

    from _stdio import utf8_child_text

    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, **utf8_child_text(), check=False)
    return Path(out.stdout.strip() or os.getcwd()).resolve()


def _print_plan(plan: Plan) -> None:
    found = False
    for group in GROUPS:
        items = plan.items[group]
        print(
            f"[{group}] {GROUP_TITLES[group]}: {len(items)} to remove"
            if items
            else f"[{group}] {GROUP_TITLES[group]}: nothing found"
        )
        for item in items:
            found = True
            print(f"  {item.describe}")
    for row in plan.skipped:
        print(f"  skipped {row}")
    if not found and not plan.skipped:
        print("The plugin has written nothing that this script removes.")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0], prog="plurai_uninstall.py")
    parser.add_argument("command", choices=("plan", "remove"))
    parser.add_argument("--project", help="the project folder to scan (default: the git top level of the current directory)")
    parser.add_argument(
        "--home", help="use this folder as the home folder instead of the account's (for tests and other accounts)"
    )
    parser.add_argument("--groups", help=f"remove: a comma-separated list of {', '.join(GROUPS)}")
    args = parser.parse_args(argv)

    project = _project_root(args.project)
    home = Path(args.home).resolve() if args.home else None
    plan = build_plan(project, home)
    if args.command == "plan":
        _print_plan(plan)
        return 0
    chosen = [g.strip() for g in (args.groups or "").split(",") if g.strip()]
    unknown = [g for g in chosen if g not in GROUPS]
    if not chosen or unknown:
        print(
            f"remove needs --groups with some of: {', '.join(GROUPS)}" + (f" (unknown: {', '.join(unknown)})" if unknown else ""),
            file=sys.stderr,
        )
        return 2
    removed, failed = apply(plan, chosen, project, home)
    for row in removed:
        print(f"removed {row}")
    for row in failed:
        print(f"could not remove {row}", file=sys.stderr)
    skipped = plan.skipped if "project" in chosen else []
    for row in skipped:
        print(f"skipped {row}", file=sys.stderr)
    if not removed and not failed and not skipped:
        print("nothing to remove in " + ", ".join(chosen))
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
