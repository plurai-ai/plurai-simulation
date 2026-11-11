"""A strict reader for the subset of TOML that Codex's ``config.toml`` holds.

``tomllib`` needs Python 3.11 and hooks run under the 3.9 floor, so the file is read here. The subset
is keys, tables, arrays of tables, strings (basic and literal, single-line and multi-line), booleans,
integers (decimal, hex, octal, binary, with underscores), floats, dates and times (kept as source
text), arrays (several lines allowed) and single-line inline tables. A duplicate key, a key that clashes
with a table, ``inf``, ``nan`` and anything malformed make :func:`loads` raise :class:`Unreadable` for
the whole file, naming the first line at fault. The caller refuses on that even when the line sits in a
table it never reads, the accepted cost of a small reader. The reason never quotes the line, which can
hold a token.
"""

from __future__ import annotations

import re
from typing import Any


class Unreadable(ValueError):
    """The file is not in the subset; the message is ``line N: <the construct>``."""


_CTRL = r"\x00-\x08\x0a-\x1f\x7f"  # control characters TOML refuses in a comment or a single-line string; tab is fine
_MULTI_CTRL = r"\x00-\x08\x0b-\x1f\x7f"  # a multi-line string also takes a newline
_ESCAPES = r'["\\btnfr]|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}'
_BASIC = r'"(?:[^"\\' + _CTRL + r"]|\\(?:" + _ESCAPES + r"))*\""
_LITERAL = r"'[^'" + _CTRL + r"]*'"
# Up to two quotes before the closing delimiter belong to the string; a line-ending backslash ends in whitespace.
_MULTI = {
    '"': re.compile(
        r'"""\n?(?P<body>(?:[^"\\' + _MULTI_CTRL + r']|"{1,2}(?!")|\\(?:' + _ESCAPES + r'|[ \t]*\n[ \t\n]*))*)(?P<close>"{3,5})'
    ),
    "'": re.compile(r"'''\n?(?P<body>(?:[^'" + _MULTI_CTRL + r"]|'{1,2}(?!'))*)(?P<close>'{3,5})"),
}
_KEY = re.compile(rf"[A-Za-z0-9_-]+|{_BASIC}|{_LITERAL}")
_DOTTED = re.compile(rf"(?:{_KEY.pattern})(?:[ \t]*\.[ \t]*(?:{_KEY.pattern}))*")
_DATE = r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?"
_DIGITS = r"[0-9](?:_?[0-9])*"  # an underscore sits between two digits
_NUMBER = (
    r"0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*|0o[0-7](?:_?[0-7])*|0b[01](?:_?[01])*"
    rf"|[+-]?(?:0|[1-9](?:_?[0-9])*)(?P<frac>\.{_DIGITS})?(?P<exp>[eE][+-]?{_DIGITS})?"
)
_SCALAR = re.compile(rf"(?P<date>{_DATE})|(?P<str>{_BASIC}|{_LITERAL})|(?P<bool>true|false)|{_NUMBER}")
_BLANK = re.compile(rf"(?:[ \t\n]|#[^{_CTRL}]*)*")
_SPACE = re.compile(r"[ \t]*")
_EQUALS = re.compile(r"[ \t]*=[ \t]*")
_END = re.compile(rf"[ \t]*(?:#[^{_CTRL}]*)?(?:\n|\Z)")
_INF_NAN = re.compile(r"[+-]?(?:inf|nan)\b")
_ESCAPE = re.compile(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|[ \t]*\n[ \t\n]*|.)")
_SIMPLE = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(body: str) -> str:
    """``body`` of a basic string with its escapes resolved; a line-ending backslash drops the whitespace after it."""

    def one(m: re.Match[str]) -> str:
        e = m[1]
        if e[0] in "uU":
            code = int(e[1:], 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise Unreadable("an escape that is no Unicode scalar value")
            return chr(code)
        return "" if e[0] in " \t\n" else _SIMPLE[e]

    return _ESCAPE.sub(one, body)


def _text(token: str) -> str:
    """A key or single-line string token without its quotes, escapes resolved in a basic one."""
    if token[0] == '"':
        return _unescape(token[1:-1])
    return token[1:-1] if token[0] == "'" else token


class _Reader:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.start = 0
        self.inline: set[int] = set()  # ids of inline tables: a later line cannot extend them
        self.opened: set[int] = set()  # ids of tables a [header] named
        self.arrays: set[int] = set()  # ids of arrays of tables

    def at(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        m = pattern.match(self.s, self.i)
        if m:
            self.i = m.end()
        return m

    def clash(self, reason: str) -> Unreadable:
        self.i = self.start  # a clash is reported at the statement that causes it
        return Unreadable(reason)

    def keys(self) -> list[str]:
        m = self.at(_DOTTED)
        if not m:
            raise Unreadable("a key it cannot parse")
        return [_text(t) for t in _KEY.findall(m.group())]

    def walk(self, table: dict, parts: list[str]) -> dict:
        for part in parts:
            table = table.setdefault(part, {})
            if isinstance(table, list) and id(table) in self.arrays:
                table = table[-1]
            if not isinstance(table, dict) or id(table) in self.inline:
                raise self.clash("a key that is already a value")
        return table

    def put(self, table: dict, path: list[str], value: Any) -> None:
        table = self.walk(table, path[:-1])
        if path[-1] in table:
            raise self.clash("a key defined twice")
        table[path[-1]] = value

    def value(self) -> Any:
        c = self.s[self.i : self.i + 1]
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        quote = self.s[self.i : self.i + 3]
        if quote in ('"""', "'''"):
            m = self.at(_MULTI[quote[0]])
            if not m:
                raise Unreadable("a multi-line string it cannot parse")
            body = m["body"] + quote[0] * (len(m["close"]) - 3)
            return _unescape(body) if quote[0] == '"' else body
        m = self.at(_SCALAR)
        if not m:
            raise Unreadable("inf or nan, which it does not read" if _INF_NAN.match(self.s, self.i) else "a value it cannot parse")
        if m["date"]:
            return m.group()
        if m["str"]:
            return _text(m.group())
        if m["bool"]:
            return m.group() == "true"
        digits = m.group().replace("_", "")
        return float(digits) if m["frac"] or m["exp"] else int(digits, 0)

    def array(self) -> list[Any]:
        self.i += 1
        items: list[Any] = []
        while True:
            self.at(_BLANK)
            if self.s.startswith("]", self.i):
                self.i += 1
                return items
            items.append(self.value())
            self.at(_BLANK)
            if self.s.startswith(",", self.i):
                self.i += 1
            elif not self.s.startswith("]", self.i):
                raise Unreadable("an array it cannot parse")

    def inline_table(self) -> dict[str, Any]:
        self.i += 1
        table: dict[str, Any] = {}
        self.at(_SPACE)
        while not self.s.startswith("}", self.i):
            path = self.keys()
            if not self.at(_EQUALS):
                raise Unreadable("an inline table it cannot parse")
            self.put(table, path, self.value())
            self.at(_SPACE)
            if self.s.startswith(",", self.i):
                self.i += 1
                self.at(_SPACE)
                if self.s.startswith("}", self.i):
                    raise Unreadable("a trailing comma in an inline table")
            elif not self.s.startswith("}", self.i):
                raise Unreadable("an inline table it cannot parse")
        self.i += 1
        self.inline.add(id(table))
        return table

    def header(self, root: dict) -> dict:
        array = self.s.startswith("[[", self.i)
        close = "]]" if array else "]"
        self.i += len(close)
        self.at(_SPACE)
        path = self.keys()
        self.at(_SPACE)
        if not self.s.startswith(close, self.i):
            raise Unreadable("a table header it cannot parse")
        self.i += len(close)
        parent, name = self.walk(root, path[:-1]), path[-1]
        if not array:
            node = parent.setdefault(name, {})
            if not isinstance(node, dict) or id(node) in self.inline | self.opened:
                raise self.clash("a table defined twice or a key that is already a value")
            self.opened.add(id(node))
            return node
        items = parent.setdefault(name, [])
        if not isinstance(items, list) or (items and id(items) not in self.arrays):
            raise self.clash("a key that is already a value")
        self.arrays.add(id(items))
        items.append({})
        return items[-1]

    def read(self) -> dict[str, Any]:
        root: dict[str, Any] = {}
        current = root
        while True:
            self.at(_BLANK)
            if self.i >= len(self.s):
                return root
            self.start = self.i
            try:
                if self.s[self.i] == "[":
                    current = self.header(root)
                else:
                    path = self.keys()
                    if not self.at(_EQUALS):
                        raise Unreadable("a line that is no key and value")
                    self.put(current, path, self.value())
                if not self.at(_END):
                    raise Unreadable("text after a table header or a value on the same line")
            except Unreadable as problem:
                raise Unreadable(f"line {self.s.count(chr(10), 0, self.i) + 1}: {problem}") from None


def loads(text: str) -> dict[str, Any]:
    """The tables and keys of ``text``; raises :class:`Unreadable` when any line is outside the subset."""
    return _Reader(text.replace("\r\n", "\n")).read()
