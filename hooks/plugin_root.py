"""Fill in the literal ``${CLAUDE_PLUGIN_ROOT}`` a model copies out of a skill or reference file.

Claude Code substitutes the token in a SKILL.md body only; in a ``references/*.md`` file, and on
Copilot everywhere, it stays literal and the shell does not export it. This PreToolUse hook replaces
the token in a shell command (``command``) and in a file reader's path (Claude Code's ``Read``
``file_path``, Copilot's ``Read`` or ``view`` ``path``) with the plugin root the hook itself lives in.

The rewrite never decides permission: the harness's own approval flow still shows and reviews the
rewritten command. It is registered for Claude Code and Copilot only. Codex applies ``updatedInput``
solely together with ``allow``, and an approved command still acts on its arguments, its working
directory and the workspace's binaries, so no check of the command can make ``allow`` safe; Codex
gets the root from the session glossary instead (``session_glossary.py``).

The root is inserted with the quoting its context needs (bare, inside double quotes, inside single
quotes), so a root with a space or a quote stays one word.

On native Windows the command may run in bash (Git Bash) or in PowerShell (Copilot's shell tool, which
the hook payload names ``bash`` too), so the rewrite must mean the same in both. The root is spelled with
forward slashes and inserted only when every character is one both shells read literally inside quotes
(letters and digits of any script, and `` :/._()-``): raw inside the command's own double or single quotes, and bare in unquoted
text only without a space or parenthesis. Any other root, or a token in a context that would need more,
leaves the command untouched with one line of context saying why.

Stdlib only, importable under the oldest interpreter ``python3`` can resolve to.
"""

from __future__ import annotations

import os
import shlex
import unicodedata

import _platform
from _harness import model
from _harness.model import Decision, Event

TOKEN = "${CLAUDE_PLUGIN_ROOT}"

#: The fields of each tool's input that may hold the token.
_FIELDS = {"shell": ("command",), "read": ("file_path", "path")}

_QUOTES = "'\""

#: Besides letters, digits and combining marks (any script: José, a Hebrew or Hindi user name), what a Windows root may hold for
#: bash and PowerShell 5.1/7 to read it as literal text inside "..." or '...'. PowerShell's own quote and dash
#: characters (U+2018-U+201E, U+2013-U+2015) are none of those, so they never pass.
_WINDOWS_QUOTABLE = " :/._()-"
#: ... and to read it as one bare word.
_WINDOWS_BARE = ":/._-"


def _windows_literal(text: str, allowed: str) -> bool:
    """Every character a letter, a digit or a combining mark (a Devanagari vowel sign), or one of ``allowed``."""
    return bool(text) and all(char.isalnum() or unicodedata.category(char).startswith("M") or char in allowed for char in text)
#: What PowerShell also treats as quote characters, besides ' and ".
_TYPOGRAPHIC_QUOTES = "\u2018\u2019\u201a\u201b\u201c\u201d\u201e"


class _Refused(Exception):
    """The root cannot go into this command in a form both Windows shells read the same way."""


def windows_root(root: str) -> str | None:
    """``root`` with forward slashes, or None when it holds a character outside the shared literal set."""
    spelled = root.replace("\\", "/")
    return spelled if _windows_literal(spelled, _WINDOWS_QUOTABLE) else None


#: What a POSIX shell expands or ends inside double quotes (``!`` is history expansion in an interactive bash).
_DOUBLE_QUOTE_ACTIVE = '$`"\\!'


def shell_path(path: object) -> str:
    """``path`` as one quoted word for a command a hook's text tells the model to run.

    Off Windows: ``"<path>"``, as the hooks always wrote it, unless the path holds a character double quotes
    would expand or end (``$ ` " \\ !``): then inside single quotes, a ``'`` written ``'\\''``, so it never expands.
    On Windows: forward slashes, inside double quotes when ``windows_root`` accepts it (one word in bash and
    PowerShell); else inside single quotes when it holds no quote character of either shell nor a control
    character (literal in both); else ``shlex.quote`` (bash).
    """
    text = str(path)
    if not _platform.WINDOWS:
        if any(char in _DOUBLE_QUOTE_ACTIVE for char in text):
            return "'" + text.replace("'", "'\\''") + "'"
        return f'"{text}"'
    spelled = text.replace("\\", "/")
    if windows_root(spelled) is not None:
        return f'"{spelled}"'
    if not any(char in _QUOTES or char in _TYPOGRAPHIC_QUOTES or ord(char) < 0x20 or ord(char) == 0x7F for char in spelled):
        return f"'{spelled}'"
    return shlex.quote(spelled)


def _plugin_root() -> str:
    """The directory this hook lives in, resolved: ``<root>/hooks/plugin_root.py``."""
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def spelled_root() -> str:
    """The root as the harness names it (``CLAUDE_PLUGIN_ROOT``), or the real one when it names none."""
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or _plugin_root()


def _in_context(root: str, state: str) -> str:
    """``root`` as a shell word fragment that is correct where the scanner is: unquoted (``state``
    empty), inside double quotes or inside single quotes. A root with nothing to quote goes in as it
    is; any other closes the open quote, adds the quoted root, and reopens.

    On Windows ``root`` is already ``windows_root``'s spelling: raw inside either quote, bare only when it
    needs no quoting, and otherwise ``_Refused``."""
    if _platform.WINDOWS:
        if state or _windows_literal(root, _WINDOWS_BARE):
            return root
        raise _Refused
    quoted = shlex.quote(root)
    if quoted == root:
        return root
    return state + quoted + state


def _is_token_at(command: str, i: int) -> bool:
    """Whether the token starts at ``i`` and the shell would expand it as that variable: after a
    ``$`` it would be the tail of ``$$``, the shell's process id."""
    return command.startswith(TOKEN, i) and not (i > 0 and command[i - 1] == "$")


def _substitute(command: str, root: str) -> str | None:
    """``command`` with the root in place of each token, or None when it holds none to replace.

    A token the shell would not expand as the variable (after a backslash or a ``$``) stays literal.
    The quote state is tracked per character; a command that nests quotes inside ``$(...)`` can fool
    it, which for a root that needs quoting breaks that command and nothing else.
    """
    out: list[str] = []
    state = ""  # "", "'" or '"': the quote the next character is inside
    found = False
    i = 0
    while i < len(command):
        char = command[i]
        if _is_token_at(command, i):
            found = True
            out.append(_in_context(root, state))
            i += len(TOKEN)
            continue
        if char == "\\" and state != "'" and i + 1 < len(command):
            out.append(command[i : i + 2])
            i += 2
            continue
        if char in _QUOTES:
            if state == "":
                state = char
            elif state == char:
                state = ""
        out.append(char)
        i += 1
    return "".join(out) if found else None


def handle(event: Event) -> Decision:
    fields = _FIELDS.get(event.tool or "")
    if event.kind != "pre_tool_use" or fields is None:
        return model.none()
    root = spelled_root()
    shell_root = windows_root(root) if _platform.WINDOWS else root
    updated = dict(event.tool_input)
    changed = refused = False
    for field in fields:
        value = updated.get(field)
        if not isinstance(value, str) or TOKEN not in value:
            continue
        if event.tool == "read":
            # A path is data, not shell text: no quoting, no escapes.
            replaced: str | None = value.replace(TOKEN, root)
        elif shell_root is None:
            refused = True
            continue
        else:
            try:
                replaced = _substitute(value, shell_root)
            except _Refused:
                refused = True
                continue
        if replaced is not None:
            updated[field] = replaced
            changed = True
    if changed:
        return model.rewrite(updated)
    if refused:
        return model.context(refusal_note(root))
    return model.none()


def refusal_note(root: str) -> str:
    """The one line of context when a Windows command keeps its literal token."""
    return (
        f"plugin_root: ${{CLAUDE_PLUGIN_ROOT}} was left as written: the plugin root {root} cannot be put into this "
        "command in a form bash and PowerShell both read as one word (it holds a character other than "
        "letters, digits, space, : / . _ ( ) -, or the token is unquoted and the root has a space or parenthesis). "
        "Write the root into the command yourself, with forward slashes, quoted for the shell that runs it."
    )
