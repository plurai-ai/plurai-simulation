#!/usr/bin/env python3
"""The grounding ledger's key grammar, as the plugin writes it.

The backend publishes the grammar, the fold and golden vectors as data in the `grounding_ledger`
contract block and shares no code. This module is the plugin's own implementation of those rules;
`scripts/tests/test_grounding_contract_parity.py` proves it reproduces every vector, so a rule that
moves in the block fails a test here instead of splitting a key in two.

A key is `kind ":" name ("." segment)*`. A name is folded (NFKC, casefold, every run of `-`, `_` and
space becomes one `_`, leading and trailing `_` dropped; camelCase is not split) and then escaped:
`%`, `.` and `:` become `%25`, `%2E` and `%3A`, so a name holding them never reads as a boundary.

User profiles and complications are scenario design, not ledger kinds. A reader of a stored mirror reads
a key's kind with `kind_of` before parsing it, so a kind the grammar no longer knows is skipped.

Stdlib only. Python 3.9 floor.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

#: The `grounding_ledger` block `version` this implementation follows. A block that moves past it
#: means a rule changed that this file does not know.
SUPPORTED_CONTRACT_VERSION = 4

KINDS = ('tool', 'entity', 'agent_fact')

#: Segment head to the number of names it carries and the separator between head and name.
SEGMENT_HEADS: dict[str, dict[str, Any]] = {
    'returns': {'names': 0, 'separator': None},
    'error_channel': {'names': 0, 'separator': None},
    'args': {'names': 1, 'separator': '.'},
    'field': {'names': 1, 'separator': ':'},
    'state': {'names': 1, 'separator': ':'},
    'fact': {'names': 1, 'separator': ':'},
}

#: The segment chains a kind accepts, by segment head.
CHAINS: dict[str, tuple[tuple[str, ...], ...]] = {
    'tool': ((), ('returns',), ('returns', 'field'), ('args',), ('error_channel',)),
    'entity': ((), ('field',), ('state',)),
    'agent_fact': (('fact',),),
}

FOLD_SEPARATORS = '-_ '
ESCAPES = {'%': '%25', '.': '%2E', ':': '%3A'}

_SEPARATOR_RUN = re.compile(r'[-_ ]+')
_ESCAPE_SEQUENCE = re.compile(r'%(25|2[Ee]|3[Aa])')
_DECODED = {'25': '%', '2e': '.', '3a': ':'}
_SHOWN_CHARS = 80


class KeyGrammarError(ValueError):
    """A name or key the grammar refuses. `cause` is a stable code; the message names the offender."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


def _shown(name: str) -> str:
    return repr(name if len(name) <= _SHOWN_CHARS else name[:_SHOWN_CHARS] + '…')


def _normalise(name: str) -> str:
    return unicodedata.normalize('NFKC', name)


def fold_name(name: str) -> str:
    return _SEPARATOR_RUN.sub('_', _normalise(name).casefold()).strip('_')


def escape_name(name: str) -> str:
    return ''.join(ESCAPES.get(char, char) for char in name)


def unescape_name(text: str) -> str:
    if ':' in text:
        raise KeyGrammarError('bad_shape', f'the name {_shown(text)} holds a raw ":"; write it as %3A')
    if '%' in _ESCAPE_SEQUENCE.sub('', text):
        raise KeyGrammarError('bad_escape', f'the name {_shown(text)} has a "%" that is not %25, %2E or %3A')
    return _ESCAPE_SEQUENCE.sub(lambda match: _DECODED[match.group(1).lower()], text)


def _folded(name: Any) -> str:
    if not isinstance(name, str):
        raise KeyGrammarError('bad_shape', f'a name must be a string, got {type(name).__name__}')
    folded = fold_name(name)
    if not folded:
        raise KeyGrammarError('empty_name', f'the name {_shown(name)} folds to nothing')
    return folded


class Key:
    """A parsed key: `kind`, the folded `name` and the folded segments, each `(head, *names)`."""

    def __init__(self, kind: str, name: str, segments: tuple[tuple[str, ...], ...] = ()) -> None:
        self.kind = kind
        self.name = name
        self.segments = segments

    def __str__(self) -> str:
        parts = [f'{self.kind}:{escape_name(self.name)}']
        for head, *names in self.segments:
            parts.append(head if not names else f'{head}{SEGMENT_HEADS[head]["separator"]}{escape_name(names[0])}')
        return '.'.join(parts)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Key) and (self.kind, self.name, self.segments) == (other.kind, other.name, other.segments)

    def __hash__(self) -> int:
        return hash((self.kind, self.name, self.segments))

    def __repr__(self) -> str:
        return f'Key({str(self)!r})'


def _check_chain(kind: str, segments: tuple[tuple[str, ...], ...]) -> None:
    if kind not in KINDS:
        raise KeyGrammarError('unknown_kind', f'{kind!r} is not a key kind; the kinds are {", ".join(KINDS)}')
    heads = tuple(segment[0] if segment else '' for segment in segments)
    for segment in segments:
        spec = SEGMENT_HEADS.get(segment[0] if segment else '')
        if spec is None or len(segment) != 1 + spec['names']:
            raise KeyGrammarError('bad_segment', f'{segment!r} is not a segment')
    if heads not in CHAINS[kind]:
        cause = 'bad_shape' if kind == 'agent_fact' and not heads else 'bad_segment'
        raise KeyGrammarError(cause, f'a {kind} key does not take the segments {list(heads)}')


def make_key(kind: str, name: str, *segments: tuple[str, ...]) -> Key:
    """The key of `kind`, `name` and `segments` (`('returns',)`, `('field', name)`, `('args', name)`, ...)."""
    _check_chain(kind, segments)
    folded = tuple((segment[0], *(_folded(part) for part in segment[1:])) for segment in segments)
    return Key(kind, _folded(name), folded)


def kind_of(text: str) -> str:
    """The kind a key text names, read without parsing it."""
    return text.partition(':')[0]


def parse_key(text: str) -> Key:
    """The key written as `text`, in any spelling: names are decoded and folded."""
    kind, colon, rest = text.partition(':')
    if not colon or not rest:
        raise KeyGrammarError('bad_shape', f'{_shown(text)} is not kind:name')
    tokens = rest.split('.')
    if any(not token for token in tokens):
        raise KeyGrammarError('bad_shape', f'{_shown(text)} has an empty segment')
    name, segments, index = unescape_name(tokens[0]), [], 1
    while index < len(tokens):
        head, colon, tail = tokens[index].partition(':')
        spec = SEGMENT_HEADS.get(head)
        index += 1
        if spec is None:
            raise KeyGrammarError('bad_segment', f'{head!r} is not a segment head in {_shown(text)}')
        if spec['names'] == 0:
            if colon:
                raise KeyGrammarError('bad_segment', f'{head!r} takes no name in {_shown(text)}')
            segments.append((head,))
        elif spec['separator'] == '.':
            if colon or index >= len(tokens):
                raise KeyGrammarError('bad_shape', f'{head!r} needs a name in {_shown(text)}')
            segments.append((head, unescape_name(tokens[index])))
            index += 1
        else:
            if not colon:
                raise KeyGrammarError('bad_shape', f'{head!r} needs ":name" in {_shown(text)}')
            segments.append((head, unescape_name(tail)))
    return make_key(kind, name, *segments)
