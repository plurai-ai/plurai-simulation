"""Where a script may write when a command line names the path.

A plugin script a model proposes and a user approves is read as "render this report", not as "write
this file anywhere the user can". So an output path (`--out`, `--out-dir`) is confined to the
directories the product works in: the session's project directory, the plurai workdir it resolved and
the system temp directory, as the SessionStart hook recorded them in the session pin
(`hooks/session_pin.py`). They are not read from the command's own environment or working directory,
which one command can change. Anything else is refused unless the caller passes
`--allow-outside-workdir`, which says so in the command the user reads before approving it. With no
pin there are no roots, so the flag is the only way to write, with one exception: a Claude Code session whose
hooks did not run (`session_pin.unpinned_claude_session`, limited mode) may write inside a folder named
`plurai_sim`, the plurai workdir's own name, found by its shape on disk and not by any variable
(`_plurai_sim_refusal`). Several scripts build their own `--out`, so a flag cannot stand in for it.

Paths are compared after resolving symlinks and `..`, so neither can carry a write out of a root.

A root also holds the files a tool runs on its own, so inside one a path with a component named in
`CODE_DIRECTORIES` (git hooks and config, Claude Code settings and hooks, ...) is refused as well. Those
names are compared without regard to letter case, as a volume that ignores it reads them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

FLAG = '--allow-outside-workdir'

#: Directories (and files) whose contents a tool executes without being asked: hooks, settings, MCP
#: servers, editor tasks, shell environments and an interpreter's `.pth` files. A list of names cannot
#: be complete; it covers the places this product's own tools read, and the flag covers the rest.
CODE_DIRECTORIES = frozenset({'.git', '.claude', '.codex', '.husky', '.vscode', '.mcp.json', '.envrc', 'site-packages'})


def add_flag(parser: argparse.ArgumentParser) -> None:
    """Declare the one switch that lifts the confinement for a run."""
    parser.add_argument(
        FLAG, action='store_true',
        help="allow the output path to sit outside the session's project directory, the plurai workdir and the temp directory "
             '(otherwise such a path is refused)',
    )


def _roots() -> list[Path]:
    """The session's roots, from the pin recorded when it started; none when there is no pin.

    Not the working directory (a command can `cd`), not `WORKDIR` / `PLURAI_DIR` / `TMPDIR` (a command
    prefix sets them): the pin holds what the session was bound to (`hooks/session_pin.py`).
    """
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import session_pin  # noqa: PLC0415 - found through the line above

    pin = session_pin.read()
    if pin is None:
        return []
    return [Path(root) for root in (pin.project_dir, pin.temp_dir, *pin.workdirs) if root]


def _unpinned_claude_session() -> bool:
    """Whether this is a Claude Code session with no pin because its hooks did not run (``session_pin``)."""
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import session_pin  # noqa: PLC0415 - found through the line above

    return session_pin.unpinned_claude_session()


def _code_parts(parts: tuple[str, ...]) -> list[str]:
    """The parts of a whole path that are checked against `CODE_DIRECTORIES`: all of them but the three that make a
    Claude Code worktree's own folder, `.claude/worktrees/<name>`, which holds a checkout and nothing Claude Code runs.
    Only that exact shape is passed over: a `.claude` anywhere else, and every part inside the worktree (its `.git`, its
    own `.claude`), is still checked."""
    out: list[str] = []
    i = 0
    while i < len(parts):
        if (
            i + 3 < len(parts)
            and parts[i].casefold() == '.claude'
            and parts[i + 1].casefold() == 'worktrees'
            and parts[i + 2].casefold() not in CODE_DIRECTORIES
        ):
            i += 3
            continue
        out.append(parts[i])
        i += 1
    return out


def _plurai_sim_refusal(path: str | Path, target: Path) -> str | None:
    """With no pin in limited mode: ``target`` (resolved) may be written when a folder named `plurai_sim` that exists is
    one of its ancestors and no part of its whole path is a place that runs code on its own; why not otherwise.

    The resolved path decides, so a symlink or `..` cannot carry the write out of the folder, and the whole path is
    checked for `CODE_DIRECTORIES`, not only the part below the folder: a `plurai_sim` inside `.git` is still `.git`.
    The one part of a path passed over is a Claude Code worktree's own folder (`_code_parts`), or no write in a session
    started in a worktree could land.
    """
    if not any(parent.name == 'plurai_sim' and parent.is_dir() for parent in target.parents):
        return (
            f'{path} was not written: the plugin\'s hooks are off in this session, so it has no record of the folders it works '
            f'in, and then only a path inside a plurai_sim folder may be written. Name a path there, or add {FLAG} if writing '
            'there is what you mean.'
        )
    if any(part.casefold() in CODE_DIRECTORIES for part in _code_parts(target.parts)):
        return (
            f'{path} is a place that runs code on its own (git hooks, Claude Code settings, MCP configuration), so it was not written. '
            f'Add {FLAG} if writing there is what you mean.'
        )
    return None


def refusal(path: str | Path, *, allow_outside: bool) -> str | None:
    """Why ``path`` may not be written, or None when it may."""
    if allow_outside:
        return None
    target = Path(os.path.realpath(path))
    roots = [Path(os.path.realpath(root)) for root in _roots()]
    for root in roots:
        if target == root or root in target.parents:
            inside = target.relative_to(root).parts
            if any(part.casefold() in CODE_DIRECTORIES for part in inside):
                return (
                    f'{path} is a place that runs code on its own (git hooks, Claude Code settings, MCP configuration), so it was not written. '
                    f'Add {FLAG} if writing there is what you mean.'
                )
            return None
    if not roots and _unpinned_claude_session():
        return _plurai_sim_refusal(path, target)
    if not roots:
        return (
            f'{path} was not written: this command has no session pin, so it does not know which directories the session works in. '
            f'Run it from a Claude Code session with the plugin enabled, or add {FLAG} if writing there is what you mean.'
        )
    return (
        f'{path} is outside the session\'s project directory, its plurai workdir and the temp directory, so it was not written. '
        f'Name a path inside one of them, or add {FLAG} if writing there is what you mean.'
    )
