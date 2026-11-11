#!/usr/bin/env python3
"""Are the observability sink keys reachable where the runner reads them? Names only, never a value.

    python3 scripts/sink_keys.py langsmith            # from the agent dir, the cwd the runner gets
    python3 scripts/sink_keys.py langfuse --from DIR  # resolve the .env as a runner started in DIR would

Exit 0: every required key is set, in the environment or in the `.env` the SDK loads. Exit 1: at
least one is missing; the output names it and the file to put it in. Exit 2: usage error.

WHY THIS EXISTS. The SDK builds its sink settings from the environment AND from one `.env`: the
file `PLURAI_ENV_FILE` names, else the nearest `.env` walking up from the runner's cwd, and none
when `PLURAI_DISABLE_DOTENV` is truthy (`plurai/_dotenv.py`). A shell test of `$LANGSMITH__API_KEY`
sees only the first half, and under Claude Code an exported credential may not reach the Bash tool's
subprocesses at all, in which case it reads as unset here and never reaches the runner either.
Non-secret names such as `LANGSMITH__PROJECT` survive. The `.env` is therefore the route that works
on every host, and this check reads it the way the SDK does, so the skill neither runs a sink with
no key nor tells the user to export one.

A missing key's line names the target file and whether git ignores it: a key must never land in a
tracked file.

Stdlib-only, like its siblings.
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import subprocess
import sys
from pathlib import Path

from _stdio import utf8_child_text

#: The keys each sink refuses to start without (the SDK's `<Sink>SinkConfig` validators).
REQUIRED: dict[str, tuple[str, ...]] = {
    'langsmith': ('LANGSMITH__API_KEY',),
    'langfuse': ('LANGFUSE__SECRET_KEY', 'LANGFUSE__PUBLIC_KEY'),
    'arize': ('ARIZE__API_KEY', 'ARIZE__SPACE_ID'),
    'braintrust': ('BRAINTRUST__API_KEY',),
}

_TRUTHY = frozenset({'1', 'true', 'yes', 'on', 't', 'y'})


def dotenv_path(start: Path) -> tuple[Path | None, str]:
    """The `.env` the SDK loads for a runner started in ``start``, and why that one.

    Asks the SDK itself (`plurai._dotenv.resolve_dotenv_path`, from ``start``) when this interpreter
    can import it, so the answer cannot drift from the runner's. Otherwise mirrors it: the disable
    switch, then the explicit file (used only when it is a file, relative to the runner's cwd), then
    python-dotenv's `find_dotenv(usecwd=True)`, which returns the NEAREST `.env` up the tree whether
    or not it holds the key.
    """
    if os.environ.get('PLURAI_DISABLE_DOTENV', '').strip().lower() in _TRUTHY:
        return None, 'disabled'
    override = os.environ.get('PLURAI_ENV_FILE')
    try:
        from plurai._dotenv import resolve_dotenv_path  # pyright: ignore[reportMissingImports] - optional: present in the agent venv
    except ImportError:
        resolve_dotenv_path = None
    except Exception as exc:  # noqa: BLE001 - an SDK that is installed but fails to import must not read as a missing key
        _note_import_failure('the SDK', exc)
        resolve_dotenv_path = None
    if resolve_dotenv_path is not None:
        here = Path.cwd()
        try:
            os.chdir(start)
            found = resolve_dotenv_path()
        finally:
            os.chdir(here)
        if found:
            return (start / found).resolve(), 'PLURAI_ENV_FILE' if override else 'nearest'
        return None, 'PLURAI_ENV_FILE names no file' if override else 'none'
    if override:
        path = start / Path(override).expanduser()
        return (path, 'PLURAI_ENV_FILE') if path.is_file() else (None, 'PLURAI_ENV_FILE names no file')
    for directory in (start, *start.parents):
        candidate = directory / '.env'
        if candidate.is_file():
            return candidate, 'nearest'
    return None, 'none'


def dotenv_names(path: Path) -> set[str]:
    """The names (upper-cased: the SDK's settings match case-insensitively) a `.env` gives a non-blank value.

    Read with python-dotenv, the parser the SDK uses, when it is importable; otherwise with
    `_dotenv_values`, a stdlib port of its grammar and `${VAR}` interpolation. A blank value counts as
    unset, as the sink configs coerce it to ``None``.
    """
    try:
        from dotenv import dotenv_values  # pyright: ignore[reportMissingImports] - optional: present in the agent venv
    except ImportError:
        dotenv_values = None
    except Exception as exc:  # noqa: BLE001 - as for the SDK import above
        _note_import_failure('python-dotenv', exc)
        dotenv_values = None
    try:
        parsed = dotenv_values(path) if dotenv_values is not None else _dotenv_values(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeDecodeError):
        return set()
    return {name.upper() for name, value in parsed.items() if value and value.strip()}


def _note_import_failure(what: str, exc: Exception) -> None:
    print(f'sink-keys: {what} could not be imported ({type(exc).__name__}); reading the .env with the stdlib mirror', file=sys.stderr)


# python-dotenv's grammar (`dotenv/parser.py`), token for token, so an interpreter without it reads a
# `.env` the way the runner will: multi-line quoted values, escapes, and a malformed line skipped as unset.
_MULTILINE_WHITESPACE = re.compile(r'\s*', re.MULTILINE)
_WHITESPACE = re.compile(r'[^\S\r\n]*')
_EXPORT = re.compile(r'(?:export[^\S\r\n]+)?')
_SINGLE_QUOTED_KEY = re.compile(r"'([^']+)'")
_UNQUOTED_KEY = re.compile(r'([^=\#\s]+)')
_EQUAL_SIGN = re.compile(r'(=[^\S\r\n]*)')
_SINGLE_QUOTED_VALUE = re.compile(r"'((?:\\.|[^'\\])*)'", re.DOTALL)
_DOUBLE_QUOTED_VALUE = re.compile(r'"((?:\\.|[^"\\])*)"', re.DOTALL)
_UNQUOTED_VALUE = re.compile(r'([^\r\n]*)')
_COMMENT = re.compile(r'(?:[^\S\r\n]*#[^\r\n]*)?')
_END_OF_LINE = re.compile(r'[^\S\r\n]*(?:\r\n|\n|\r|$)')
_REST_OF_LINE = re.compile(r'[^\r\n]*(?:\r|\n|\r\n)?')
_DOUBLE_QUOTE_ESCAPES = re.compile(r'\\[\\\'"abfnrtv]')
_SINGLE_QUOTE_ESCAPES = re.compile(r"\\[\\']")
_VARIABLE = re.compile(r'\$\{(?P<name>[^}:]*)(?::-(?P<default>[^}]*))?\}')


class _Unparsed(Exception):
    pass


def _take(pattern: re.Pattern[str], text: str, pos: int) -> tuple[int, tuple[str, ...]]:
    match = pattern.match(text, pos)
    if match is None:
        raise _Unparsed
    return match.end(), match.groups()


def _unescape(pattern: re.Pattern[str], value: str) -> str:
    return pattern.sub(lambda m: codecs.decode(m.group(0), 'unicode-escape'), value)


def _value(text: str, pos: int) -> tuple[int, str]:
    char = text[pos : pos + 1]
    if char == "'":
        pos, (value,) = _take(_SINGLE_QUOTED_VALUE, text, pos)
        return pos, _unescape(_SINGLE_QUOTE_ESCAPES, value)
    if char == '"':
        pos, (value,) = _take(_DOUBLE_QUOTED_VALUE, text, pos)
        return pos, _unescape(_DOUBLE_QUOTE_ESCAPES, value)
    if char in ('', '\n', '\r'):
        return pos, ''
    pos, (value,) = _take(_UNQUOTED_VALUE, text, pos)
    return pos, re.sub(r'\s+#.*', '', value).rstrip()


def _dotenv_values(text: str) -> dict[str, str | None]:
    """`dotenv.dotenv_values` on ``text``: the bindings in order (the last wins), `${VAR}` / `${VAR:-default}`
    resolved against the environment and the names bound above, and a line it cannot parse dropped."""
    bindings: list[tuple[str, str | None]] = []
    pos = 0
    while pos < len(text):
        try:
            pos, _ = _take(_MULTILINE_WHITESPACE, text, pos)
            if pos >= len(text):
                break
            pos, _ = _take(_EXPORT, text, pos)
            key: str | None = None
            if text[pos : pos + 1] != '#':
                pos, (key,) = _take(_SINGLE_QUOTED_KEY if text[pos : pos + 1] == "'" else _UNQUOTED_KEY, text, pos)
            pos, _ = _take(_WHITESPACE, text, pos)
            value: str | None = None
            if text[pos : pos + 1] == '=':
                pos, (equal_sign,) = _take(_EQUAL_SIGN, text, pos)
                if len(equal_sign) > 1 and text[pos : pos + 1] == '#':
                    value = ''  # `KEY= # note`: whitespace, then a comment
                else:
                    pos, value = _value(text, pos)
            pos, _ = _take(_COMMENT, text, pos)
            pos, _ = _take(_END_OF_LINE, text, pos)
        except _Unparsed:
            pos = _REST_OF_LINE.match(text, pos).end()  # pyright: ignore[reportOptionalMemberAccess] - always matches
            continue
        if key is not None:
            bindings.append((key, value))
    resolved: dict[str, str | None] = {}
    for name, value in bindings:
        if value is not None:
            env: dict[str, str | None] = {**os.environ, **resolved}
            value = _VARIABLE.sub(lambda m, env=env: env.get(m['name'], m['default'] or '') or '', value)
        resolved[name] = value
    return resolved


def env_has(name: str) -> bool:
    """Whether the process environment gives ``name`` a non-blank value, matched case-insensitively."""
    return any(key.upper() == name and value.strip() for key, value in os.environ.items())


def git_ignored(path: Path) -> bool | None:
    """True / False when git can say whether ``path`` is ignored; None outside a repository."""
    try:
        done = subprocess.run(
            ['git', '-C', str(path.parent), 'check-ignore', '-q', '--', str(path)],
            capture_output=True,
            text=True, **utf8_child_text(),
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(done.returncode)


def check(sink: str, start: Path) -> tuple[int, list[str]]:
    """(exit code, output lines) for ``sink`` with the runner started in ``start``."""
    names = REQUIRED[sink]
    env_file, why = dotenv_path(start)
    in_file = dotenv_names(env_file) if env_file else set()
    where: dict[str, str] = {}
    for name in names:
        if env_has(name):
            where[name] = 'the environment'
        elif name in in_file:
            where[name] = str(env_file)
    missing = [name for name in names if name not in where]
    if not missing:
        return 0, [f'sink-keys: present — {name} in {where[name]}' for name in names]
    lines = [f'sink-keys: missing — {name} is set in neither this session\'s environment nor {env_file or "any .env the run reads"}' for name in missing]
    if why == 'disabled':
        lines.append(
            'sink-keys: PLURAI_DISABLE_DOTENV is set, so the run reads no .env — unset it and put the key in the agent folder\'s .env'
        )
        return 1, lines
    if why == 'PLURAI_ENV_FILE names no file':
        lines.append('sink-keys: PLURAI_ENV_FILE names no file, so the run reads no .env — point it at the file that holds the key, or unset it')
        return 1, lines
    target = env_file or start / '.env'
    ignored = git_ignored(target)
    state = {True: 'gitignored', False: 'NOT gitignored — add it to .gitignore before writing a key', None: 'not in a git repository'}[ignored]
    lines.append(f'sink-keys: put {" and ".join(missing)} in {target} ({"exists" if target.is_file() else "create it"}; {state})')
    return 1, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Whether the sink keys are reachable where the runner reads them (names only).')
    parser.add_argument('sink', choices=sorted(REQUIRED))
    parser.add_argument('--from', dest='start', default='.', help="the runner's cwd (the agent dir); defaults to the current directory")
    args = parser.parse_args(argv)
    code, lines = check(args.sink, Path(args.start).resolve())
    print('\n'.join(lines))
    return code


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
