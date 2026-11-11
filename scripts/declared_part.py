#!/usr/bin/env python3
"""Build the declared part: every key the agent descriptions define, as one JSON file.

The three agent descriptions (`plurai.config.yaml`, the PRD's typed `## Agent facts` block and
`scenario_variables.yaml`) are read with the rules the backend publishes in the `grounding_ledger`
contract block, and the result is kept beside them as `artifacts/declared_part.json`; it is never uploaded. The backend
derives the same inventory from the same descriptions with its own reader and shares no code with this file;
`scripts/tests/test_declared_part.py` holds the two to the published golden vectors and, against a
backend checkout, to each other.

The part carries declarations only: no version id (the backend mints every id), no verdict, no
observation. A hostile description (past the size, node or key cap, an alias bomb, text that is not
YAML) refuses the whole build with a stated cause and leaves no part on disk. A malformed entry is
refused alone, with its cause, and the rest of the file is read.

    python3 declared_part.py build --workdir "$WORKDIR" [--out FILE]

Exit codes: 0 built; 2 nothing to build from; 3 a description is refused; 4 PyYAML is missing. The summary line on stdout
is one JSON object. The script is stdlib plus PyYAML, on the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grounding_keys as gk  # noqa: E402
from grounding_files import DECLARED_PART_NAME, write_atomic  # noqa: E402

DECLARED_PART_VERSION = 1

MAX_DECLARED_KEYS = 10_000
MAX_DESCRIPTION_BYTES = 8 * 1024 * 1024
MAX_YAML_NODES = 500_000

ERROR_CHANNELS = ('raises', 'returns')
CONFIG_SOURCES = ('plugin', 'code', 'annotation', 'trace', 'declared', 'stub_run', 'inferred', 'declared_unknown')
ERROR_CHANNEL_SOURCES = ('plugin', 'trace', 'declared', 'inferred')
DEFAULT_TIER = 'inferred'
#: A config key with no `*_source` slot is the agent's own statement of itself, so it reads as declared.
CONFIG_DEFAULT_TIER = 'declared'
SOURCE_RUNG = {'annotation': 'code'}
#: A value the plugin decided: a spec whose evidence cites `plugin:<session or step>` reads at this rung, whatever else it names.
PLUGIN = 'plugin'
PLUGIN_CITATION_PREFIX = 'plugin:'

#: Keys of a field or argument spec that are not part of its value: prose, the citation with its times, and the source slot.
SPEC_NON_VALUE_KEYS = frozenset({'description', 'evidence', 'last_changed', 'commit', 'scanned_at', 'function_last_changed', 'type_source'})

#: The PRD heading of the typed block: level two, an optional `<number>.` or `<number>)` prefix, then
#: `Agent facts` (a hyphen for the space and a trailing colon are accepted).
FACTS_HEADING = r'^##[ \t]+(?:\d+[.)][ \t]*)?agent[ -]facts[ \t]*:?[ \t]*$'
_FACTS_HEADING = re.compile(FACTS_HEADING, re.IGNORECASE)
_APPENDIX_HEADING = re.compile(r'^#{1,6}\s+VERBATIM SOURCE APPENDIX\b', re.IGNORECASE)
_FENCE_OPEN = re.compile(r'^\s*(`{3,}|~{3,})\s*(\S*)')
_FENCE_BARE = re.compile(r'^\s*(`{3,}|~{3,})\s*$')

#: Origin of a declaration, in the order the descriptions are read, and the file each comes from.
FILE_NAMES = {'config': 'plurai.config.yaml', 'prd': 'simulation_prd.md', 'variables': 'scenario_variables.yaml'}

_BOUNDS_CAUSE = {'too_large': 'too_large', 'expansion': 'yaml_expansion', 'recursive_alias': 'yaml_recursive_alias', 'too_deep': 'yaml_too_deep'}


class DescriptionRefused(ValueError):  # noqa: N818 - a refusal, named for what it states
    """A description the build refuses as a whole. `cause` is a stable code; the message names the file and the cause."""

    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


# --- bounded YAML ----------------------------------------------------------------------------------


class _Bounds(Exception):
    def __init__(self, cause: str, message: str) -> None:
        super().__init__(message)
        self.cause = cause


def _children(node: Any) -> list:
    import yaml  # noqa: PLC0415

    if isinstance(node, yaml.SequenceNode):
        return list(node.value)
    if isinstance(node, yaml.MappingNode):
        return [part for pair in node.value for part in pair]
    return []


def _expanded_size(root: Any, max_nodes: int) -> None:
    """Raise when `root` holds more than `max_nodes` nodes once every alias is expanded.

    An alias references the anchored node, so composing a billion-laughs document is cheap and
    constructing it is not: the expanded size is computed on the shared graph, once per node, before
    anything is constructed. A node that contains itself is refused.
    """
    sizes: dict = {}
    open_nodes: set = set()
    stack: list = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        ident = id(node)
        if expanded:
            open_nodes.discard(ident)
            sizes[ident] = 1 + sum(sizes[id(child)] for child in _children(node))
            if sizes[ident] > max_nodes:
                raise _Bounds('expansion', f'the document expands to more than {max_nodes} nodes once its aliases are resolved')
            continue
        if ident in sizes:
            continue
        if ident in open_nodes:
            raise _Bounds('recursive_alias', 'the document contains an alias that refers to its own ancestor')
        open_nodes.add(ident)
        stack.append((node, True))
        stack.extend((child, False) for child in _children(node) if id(child) not in sizes)


def _yaml_summary(exc: Any) -> str:
    """The error's type and positions, never its prose: pyyaml quotes the token it failed on."""
    marks = []
    for mark in (getattr(exc, 'problem_mark', None), getattr(exc, 'context_mark', None)):
        if mark is not None:
            marks.append(f'line {mark.line + 1}, column {mark.column + 1}')
    return f'{type(exc).__name__} at {"; ".join(marks)}' if marks else type(exc).__name__


def _load_bounded(text: str, max_bytes: int, max_nodes: int) -> Any:
    """`yaml.safe_load` for untrusted text; a failure carries a code and the position, never the document."""
    import yaml  # noqa: PLC0415

    if len(text.encode('utf-8')) > max_bytes:
        raise _Bounds('too_large', f'the document is larger than {max_bytes} bytes')
    loader = yaml.SafeLoader(text)
    summary: Optional[str] = None
    too_deep = False
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        _expanded_size(node, max_nodes)
        return loader.construct_document(node)
    except _Bounds:
        raise
    except yaml.YAMLError as exc:
        summary = _yaml_summary(exc)
    except RecursionError:
        too_deep = True
    finally:
        loader.dispose()
    if too_deep:
        raise _Bounds('too_deep', 'the document is nested too deeply')
    raise _Bounds('unparseable_yaml', summary or 'the document is not valid YAML')


# --- JSON-safe values -------------------------------------------------------------------------------


def _timestamp(value: Any) -> Optional[str]:
    if isinstance(value, dt.datetime):
        moment = value.astimezone(dt.timezone.utc) if value.tzinfo else value
        return moment.replace(tzinfo=None).isoformat(timespec='microseconds' if moment.microsecond else 'seconds') + 'Z'
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip() or None
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=repr)
    if isinstance(value, (dt.datetime, dt.date)):
        return _timestamp(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _spec_value(spec: Any) -> Any:
    """A field or argument spec without its prose and citation; `enum` is spelled `members`. None when nothing is left."""
    if not isinstance(spec, dict):
        return None
    value = {('members' if key == 'enum' else str(key)): item for key, item in spec.items() if key not in SPEC_NON_VALUE_KEYS}
    return _json_safe(value) or None


def _plugin_cited(citation: Any) -> bool:
    """True for a citation (a string or a list of them) that marks a value the plugin decided."""
    cited = [citation] if isinstance(citation, str) else citation if isinstance(citation, list) else []
    return any(isinstance(item, str) and item.startswith(PLUGIN_CITATION_PREFIX) for item in cited)


def _citation(owner: Any) -> Any:
    evidence = owner.get('evidence') if isinstance(owner, dict) else None
    if isinstance(evidence, str):
        return evidence.strip() or None
    if isinstance(evidence, list):
        cited = [item.strip() for item in evidence if isinstance(item, str) and item.strip()]
        return (cited[0] if len(cited) == 1 else cited) or None
    return None


def _spell_key(kind: str, name: str, *segments: tuple) -> str:
    """The key as its declarer spelled it (names trimmed, not folded), for display."""
    gk.make_key(kind, name, *segments)
    parts = [f'{kind}:{name.strip()}']
    for head, *names in segments:
        parts.append(head if not names else f'{head}{gk.SEGMENT_HEADS[head]["separator"]}{names[0].strip()}')
    return '.'.join(parts)


# --- the builder ------------------------------------------------------------------------------------


class _Builder:
    def __init__(self, max_keys: int) -> None:
        self.max_keys = max_keys
        self.entries: list = []
        self.refused: list = []
        self.advisories: list = []
        self._leaves: dict = {}
        self._keys: set = set()

    def refuse(self, origin: str, where: str, cause: str, detail: str) -> None:
        self.refused.append({'origin': origin, 'where': where, 'cause': cause, 'detail': detail})

    def advise(self, kind: str, where: str, detail: str) -> None:
        self.advisories.append({'kind': kind, 'where': where, 'detail': detail})

    def hostile(self, origin: str, cause: str, detail: str) -> DescriptionRefused:
        return DescriptionRefused(cause, f'{FILE_NAMES[origin]}: {detail}')

    def emit(
        self,
        origin: str,
        where: str,
        kind: str,
        name: Any,
        *segments: tuple,
        tier: Optional[str] = None,
        source: Optional[str] = None,
        value: Any = None,
        citation: Any = None,
        owner: Optional[dict] = None,
        scanned_at: Optional[str] = None,
        detail: Optional[dict] = None,
        leaf: Optional[str] = None,
    ) -> None:
        """Key one declaration and record it; a name the grammar refuses (one that folds to nothing) is a refused entry."""
        tier = tier or (CONFIG_DEFAULT_TIER if origin == 'config' else DEFAULT_TIER)
        if _plugin_cited(citation):
            tier, source = PLUGIN, PLUGIN
        names = [name, *(part for segment in segments for part in segment[1:])]
        if not all(isinstance(part, str) for part in names):
            self.refuse(origin, where, 'name_not_a_string', f'a name at {where} is not a string')
            return
        try:
            canonical = str(gk.make_key(kind, name, *segments))
            display = _spell_key(kind, name, *segments)
        except gk.KeyGrammarError as err:
            self.refuse(origin, where, err.cause, str(err))
            return
        if canonical not in self._keys:
            if len(self._keys) >= self.max_keys:
                raise self.hostile(origin, 'key_cap', f'more than {self.max_keys} keys')
            self._keys.add(canonical)
        shown = leaf if leaf is not None else (segments[-1][-1] if segments and len(segments[-1]) > 1 else name)
        self._leaves.setdefault((origin, canonical), []).append(shown.strip())
        owner = owner or {}
        commit = owner.get('commit')
        self.entries.append(
            {
                'key': canonical,
                'display': display,
                'origin': origin,
                'tier': tier,
                'source': source,
                'value': value,
                'citation': citation,
                'scanned_at': _timestamp(owner.get('scanned_at')) or scanned_at,
                'commit': commit if isinstance(commit, str) else None,
                'last_changed': _timestamp(owner.get('last_changed')),
                'function_last_changed': _timestamp(owner.get('function_last_changed')),
                'detail': detail,
            }
        )

    def collisions(self) -> list:
        return [{'key': key, 'origin': origin, 'spellings': list(leaves)} for (origin, key), leaves in self._leaves.items() if len(leaves) > 1]

    def source_tier(self, value: Any, allowed: tuple, where: str) -> tuple:
        """`(rung, source)` of a `*_source` slot; absent is the default tier, outside the vocabulary is absent plus an advisory."""
        if value is None:
            return CONFIG_DEFAULT_TIER, None
        if isinstance(value, str) and value in allowed:
            return SOURCE_RUNG.get(value, value), value
        self.advise('source_outside_vocabulary', where, f'{value!r} is not one of {", ".join(allowed)}; read as absent')
        return CONFIG_DEFAULT_TIER, None


def _load_mapping(text: str, origin: str) -> dict:
    name = FILE_NAMES[origin]
    try:
        loaded = _load_bounded(text, MAX_DESCRIPTION_BYTES, MAX_YAML_NODES)
    except _Bounds as err:
        raise DescriptionRefused(_BOUNDS_CAUSE.get(err.cause, err.cause), f'{name}: {err}') from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise DescriptionRefused('not_a_mapping', f'{name}: the top level must be a mapping, got {type(loaded).__name__}')
    return loaded


# --- the config -------------------------------------------------------------------------------------


def _read_config(b: _Builder, config: dict) -> None:
    scanned_at = _timestamp(config.get('scanned_at'))
    tools = config.get('tools')
    if tools is not None and not isinstance(tools, dict):
        b.refuse('config', 'tools', 'tools_not_a_mapping', f'tools must be a mapping of name to entry, got {type(tools).__name__}')
        tools = {}
    own = _own_declarations(tools or {})
    anchored: set = set()
    for name, entry in (tools or {}).items():
        where = f'tools.{name}'
        if not isinstance(name, str):
            b.refuse('config', where, 'tool_name_not_a_string', f'the tool name {name!r} is not a string; quote it in the YAML')
            continue
        if not isinstance(entry, dict):
            b.refuse('config', where, 'tool_entry_not_a_mapping', f'{where} is {type(entry).__name__}, not a mapping')
            continue
        _read_tool(b, name, entry, where, scanned_at, own, anchored)
    _read_entities(b, config.get('world_state'))


def _own_declarations(tools: dict) -> dict:
    """Per folded tool name, whether its own `tools:` entry declares an error channel; a seam's `serves` names only these tools."""
    own: dict = {}
    for name, entry in tools.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        folded = gk.fold_name(name)
        own[folded] = own.get(folded, False) or entry.get('error_channel') in ERROR_CHANNELS
    return own


def _read_tool(b: _Builder, name: str, entry: dict, where: str, scanned_at: Optional[str], own: dict, anchored: set) -> None:
    mode = entry.get('mode', 'mock')
    if not isinstance(mode, str):
        b.advise('invalid_mode', f'{where}.mode', f'{mode!r} is not a string; read as mock')
        mode = 'mock'
    served, serves_cite = _read_serves(b, name, entry, where, own)
    b.emit('config', where, 'tool', name, value={'mode': mode}, detail={'serves': list(served)} if served else None, scanned_at=scanned_at)
    raw_descriptor = entry.get('descriptor')
    descriptor = raw_descriptor if isinstance(raw_descriptor, dict) else {}
    _read_args(b, name, descriptor, where, scanned_at)
    _read_returns(b, name, descriptor, where, scanned_at)
    channel = _read_channel(b, entry, where)
    tier, source = (
        b.source_tier(entry.get('error_channel_source'), ERROR_CHANNEL_SOURCES, f'{where}.error_channel_source') if channel else (CONFIG_DEFAULT_TIER, None)
    )
    if channel is not None:
        b.emit('config', f'{where}.error_channel', 'tool', name, ('error_channel',), tier=tier, source=source, value={'channel': channel}, scanned_at=scanned_at)
    for target in served:
        # `serves` is a join key, never a statement about the served tool: the seam's args and channel stay under the seam. A served
        # tool whose own entry names no channel gets one value-less slot, so its traced failure texts land on a declared key and reach
        # this seam through the link; the first seam in config order opens it.
        folded = gk.fold_name(target)
        if own.get(folded, False) or folded in anchored:
            continue
        anchored.add(folded)
        b.emit('config', f'{where}.serves', 'tool', target, ('error_channel',), tier=DEFAULT_TIER, citation=serves_cite, scanned_at=scanned_at)


def _read_channel(b: _Builder, entry: dict, where: str) -> Optional[str]:
    """The entry's declared error channel when it names one of the vocabulary; outside it is an advisory and absent."""
    channel = entry.get('error_channel')
    if channel is None:
        return None
    if channel not in ERROR_CHANNELS:
        b.advise('error_channel_outside_vocabulary', f'{where}.error_channel', f'{channel!r} is not one of {", ".join(ERROR_CHANNELS)}; not read')
        return None
    return channel


def _read_serves(b: _Builder, name: str, entry: dict, where: str, own: dict) -> tuple:
    """The real tools a seam entry stands under (`serves:`) and the `serves_cite` that earns the link; without a cite it is unused.

    A tool is named once however it is spelled. A name that is the entry itself is dropped with an advisory: a tool does not stand under
    itself. A name no `tools:` entry declares is dropped with an advisory: the link is unused.
    """
    raw = entry.get('serves')
    if raw is None:
        return (), None
    listed = [item.strip() for item in raw if isinstance(item, str) and item.strip()] if isinstance(raw, list) else []
    own_name = gk.fold_name(name)
    named: dict = {}
    for item in listed:
        folded = gk.fold_name(item)
        if folded == own_name:
            b.advise('serves_names_itself', f'{where}.serves', f'serves lists {name!r}, the entry itself; that name is dropped')
        elif folded not in own:
            b.advise('serves_unknown_tool', f'{where}.serves', f'{item!r} is no tool in the config; the link is unused')
        else:
            named.setdefault(folded, item)
    names = tuple(named.values())
    if not names:
        b.advise('serves_not_a_tool_list', f'{where}.serves', 'serves must be a list of real tool names; the link is unused')
        return (), None
    cite = entry.get('serves_cite')
    if not isinstance(cite, str) or not cite.strip():
        b.advise('serves_without_cite', f'{where}.serves', 'serves has no serves_cite (path:line); the link is unused')
        return (), None
    return names, cite.strip()


def _read_args(b: _Builder, name: str, descriptor: dict, where: str, scanned_at: Optional[str]) -> None:
    """Key the descriptor's args under `name`, the tool that declares them."""
    args = descriptor.get('args')
    specs = args if isinstance(args, dict) else {}
    if not specs:
        schema = descriptor.get('input_schema')
        properties = schema.get('properties') if isinstance(schema, dict) else None
        specs = properties if isinstance(properties, dict) else {}
    for arg, spec in specs.items():
        b.emit(
            'config',
            f'{where}.args.{arg}',
            'tool',
            name,
            ('args', arg),
            value=_spec_value(spec),
            citation=_citation(spec),
            owner=spec if isinstance(spec, dict) else None,
            scanned_at=scanned_at,
        )


def _read_returns(b: _Builder, name: str, descriptor: dict, where: str, scanned_at: Optional[str]) -> None:
    returns = descriptor.get('returns')
    if not isinstance(returns, dict):
        return
    if isinstance(returns.get('type'), str):
        tier, source = b.source_tier(returns.get('type_source'), CONFIG_SOURCES, f'{where}.descriptor.returns.type_source')
        b.emit(
            'config',
            f'{where}.returns',
            'tool',
            name,
            ('returns',),
            tier=tier,
            source=source,
            value={'type': returns['type']},
            citation=_citation(returns),
            owner=returns,
            scanned_at=scanned_at,
        )
    # ``item_fields`` (the fields of each item of a list return) key exactly like ``fields``; ``item: true`` in the value marks them.
    for block, item in (('fields', False), ('item_fields', True)):
        specs = returns.get(block)
        for field, spec in (specs if isinstance(specs, dict) else {}).items():
            citation = _citation(spec)
            slot = spec.get('type_source') if isinstance(spec, dict) else None
            tier, source = b.source_tier(slot, CONFIG_SOURCES, f'{where}.descriptor.returns.{block}.{field}.type_source')
            value = _spec_value(spec)
            b.emit(
                'config',
                f'{where}.returns.{block}.{field}',
                'tool',
                name,
                ('returns',),
                ('field', field),
                tier=tier,
                source=source,
                value={**(value or {}), 'item': True} if item else value,
                citation=citation,
                owner=spec if isinstance(spec, dict) else None,
                scanned_at=scanned_at,
            )


def _read_entities(b: _Builder, world_state: Any) -> None:
    types = world_state.get('entity_types') if isinstance(world_state, dict) else None
    if isinstance(types, list):
        bodies: dict = {name: None for name in types if isinstance(name, str)}
    else:
        bodies = types if isinstance(types, dict) else {}
    for entity, body in bodies.items():
        where = f'world_state.entity_types.{entity}'
        b.emit('config', where, 'entity', entity)
        fields = body.get('fields') if isinstance(body, dict) else None
        if isinstance(fields, dict):
            items = list(fields.items())
        elif isinstance(fields, list):
            items = [(item, None) if isinstance(item, str) else (item.get('name'), item) for item in fields if isinstance(item, (str, dict))]
        else:
            items = []
        for field, spec in items:
            slot = spec.get('type_source') if isinstance(spec, dict) else None
            tier, source = b.source_tier(slot, CONFIG_SOURCES, f'{where}.fields.{field}.type_source')
            b.emit(
                'config',
                f'{where}.fields.{field}',
                'entity',
                entity,
                ('field', field),
                tier=tier,
                source=source,
                value=_spec_value(spec),
                citation=_citation(spec),
                owner=spec if isinstance(spec, dict) else None,
            )


# --- the variables ----------------------------------------------------------------------------------


def _items(b: _Builder, variables: dict, section: str) -> list:
    raw = variables.get(section)
    if raw is None:
        return []
    if not isinstance(raw, list):
        b.refuse('variables', section, f'{section}_not_a_list', f'{section} must be a list, got {type(raw).__name__}')
        return []
    return [(f'{section}[{index}]', item) for index, item in enumerate(raw)]


def _read_variables(b: _Builder, variables: dict) -> None:
    for where, item in _items(b, variables, 'agent_tools'):
        if not isinstance(item, dict):
            b.refuse('variables', where, 'tool_entry_not_a_mapping', f'{where} is {type(item).__name__}, not a mapping')
            continue
        name = item.get('name')
        if not isinstance(name, str):
            b.refuse('variables', where, 'tool_name_not_a_string', f'the tool name {name!r} at {where} is not a string')
            continue
        mode = item.get('mode')
        b.emit('variables', where, 'tool', name, value={'mode': mode} if isinstance(mode, str) else None)
        for field in item.get('output_fields') or []:
            if not isinstance(field, str):
                b.refuse('variables', f'{where}.output_fields', 'field_name_not_a_string', f'the field name {field!r} is not a string')
                continue
            b.emit('variables', f'{where}.output_fields', 'tool', name, ('returns',), ('field', field))
    for where, item in _items(b, variables, 'entity_schemas'):
        _read_variables_entity(b, where, item)


def _read_variables_entity(b: _Builder, where: str, item: Any) -> None:
    if not isinstance(item, dict) or not isinstance(item.get('name'), str):
        b.refuse('variables', where, 'entity_without_a_name', f'{where} is not a mapping with a string name')
        return
    name = item['name']
    b.emit('variables', where, 'entity', name)
    for section, head in (('key_fields', 'field'), ('typical_states', 'state')):
        for member in item.get(section) or []:
            if not isinstance(member, str):
                b.refuse('variables', f'{where}.{section}', f'{head}_name_not_a_string', f'the {head} name {member!r} is not a string')
                continue
            b.emit('variables', f'{where}.{section}', 'entity', name, (head, member))


# --- the PRD's typed block --------------------------------------------------------------------------


def _fenced(lines: list) -> list:
    """Per line: inside a fenced block, delimiters included. A fence closes on the first bare run of the same character at least as long as its opener."""
    inside: list = []
    opener: Optional[str] = None
    for line in lines:
        if opener is None:
            match = _FENCE_OPEN.match(line)
            inside.append(bool(match))
            if match:
                opener = match.group(1)
            continue
        inside.append(True)
        close = _FENCE_BARE.match(line)
        if close and close.group(1)[0] == opener[0] and len(close.group(1)) >= len(opener):
            opener = None
    return inside


def facts_block(prd: str) -> Optional[tuple]:
    """`(info string, body)` of the first fenced block under the level-two `Agent facts` heading, or None.

    The verbatim appendix is never read: its bytes are the customer's own documents.
    """
    lines = prd.splitlines()
    fenced = _fenced(lines)
    for index, (line, inside) in enumerate(zip(lines, fenced)):
        if not inside and _APPENDIX_HEADING.match(line):
            lines, fenced = lines[:index], fenced[:index]
            break
    start = next((i for i, (line, inside) in enumerate(zip(lines, fenced)) if not inside and _FACTS_HEADING.match(line)), None)
    if start is None:
        return None
    body: Optional[list] = None
    info = ''
    for index in range(start + 1, len(lines)):
        if not fenced[index]:
            if body is not None:
                break
            if lines[index].startswith('#'):
                return None
            continue
        if body is None:
            opener = _FENCE_OPEN.match(lines[index])
            body, info = [], opener.group(2) if opener else ''
            continue
        body.append(lines[index])
    if body is None:
        return None
    return info, '\n'.join(body[:-1] if body and _FENCE_BARE.match(body[-1]) else body)


def _read_prd(b: _Builder, prd: str) -> None:
    block = facts_block(prd)
    if block is None:
        return
    info, body = block
    if info.lower() not in {'yaml', 'yml'}:
        b.refuse('prd', 'Agent facts', 'facts_block_not_yaml', 'the first fenced block under the Agent facts heading must be tagged yaml')
        return
    try:
        loaded = _load_bounded(body, MAX_DESCRIPTION_BYTES, MAX_YAML_NODES)
    except _Bounds as err:
        if err.cause == 'unparseable_yaml':
            b.refuse('prd', 'Agent facts', 'facts_block_unparseable', f'the Agent facts block is not valid YAML ({err})')
            return
        raise DescriptionRefused(_BOUNDS_CAUSE.get(err.cause, err.cause), f'{FILE_NAMES["prd"]}: the Agent facts block: {err}') from None
    facts = loaded.get('facts') if isinstance(loaded, dict) else None
    if not isinstance(facts, list):
        b.refuse('prd', 'Agent facts', 'facts_block_not_a_list', 'the Agent facts block must be a mapping with a facts list')
        return
    for index, fact in enumerate(facts):
        where = f'facts[{index}]'
        if not isinstance(fact, dict):
            b.refuse('prd', where, 'fact_not_a_mapping', f'{where} is {type(fact).__name__}, not a mapping')
            continue
        subject, predicate = (str(fact.get(field) or '').strip() for field in ('subject', 'predicate'))
        if not subject or not predicate:
            missing = 'subject' if not subject else 'predicate'
            b.refuse('prd', where, f'fact_without_{missing}', f'{where} has no {missing}')
            continue
        source = fact.get('source')
        b.emit(
            'prd',
            where,
            'agent_fact',
            subject,
            ('fact', predicate),
            tier='prd',
            value=_json_safe(fact.get('value')),
            citation=(source.strip() or None) if isinstance(source, str) else None,
            detail={'kind': str(fact.get('kind') or ''), 'subject': subject, 'predicate': predicate},
            leaf=f'{subject}.{predicate}',
        )


# --- entry points -----------------------------------------------------------------------------------


def build(config: Optional[str] = None, prd: Optional[str] = None, variables: Optional[str] = None) -> dict:
    """The declared part of the descriptions given as text; an absent description declares nothing.

    Raises `DescriptionRefused` for a hostile description. The entries come in the order read: config,
    PRD, then variables.
    """
    builder = _Builder(MAX_DECLARED_KEYS)
    for origin, text in (('config', config), ('prd', prd), ('variables', variables)):
        if text is None:
            continue
        if len(text.encode('utf-8')) > MAX_DESCRIPTION_BYTES:
            raise builder.hostile(origin, 'too_large', f'the description is larger than {MAX_DESCRIPTION_BYTES} bytes')
        if origin == 'prd':
            _read_prd(builder, text)
        elif origin == 'config':
            _read_config(builder, _load_mapping(text, 'config'))
        else:
            _read_variables(builder, _load_mapping(text, 'variables'))
    return {
        'declared_part_version': DECLARED_PART_VERSION,
        'entries': builder.entries,
        'collisions': builder.collisions(),
        'refused': builder.refused,
        'advisories': builder.advisories,
    }


def render(part: dict) -> str:
    """The part as bytes-stable JSON: the same descriptions give the same text, so a repeat delivery sends nothing."""
    return json.dumps(part, ensure_ascii=False, sort_keys=True, indent=1) + '\n'


def read_workdir(workdir: Path) -> dict:
    """The descriptions present in `workdir`, by origin. A file that is not UTF-8 text is refused."""
    texts: dict = {}
    for origin, name in FILE_NAMES.items():
        path = workdir / name
        if not path.is_file():
            continue
        try:
            texts[origin] = path.read_bytes().decode('utf-8')
        except UnicodeDecodeError:
            raise DescriptionRefused('not_utf8', f'{name}: the description is not UTF-8 text') from None
    return texts


def _cmd_build(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    out = Path(args.out) if args.out else workdir / 'artifacts' / DECLARED_PART_NAME
    try:
        texts = read_workdir(workdir)
        if not texts:
            # A part from an earlier build describes files that are gone: never leave it to be delivered.
            out.unlink(missing_ok=True)
            print(f'declared_part: none of {", ".join(FILE_NAMES.values())} exists in {workdir}; nothing to build from.', file=sys.stderr)
            return 2
        part = build(**texts)
    except DescriptionRefused as err:
        out.unlink(missing_ok=True)
        print(f'declared_part: refused ({err.cause}): {err}', file=sys.stderr)
        return 3
    text = render(part)
    write_atomic(out, text)
    refused = [{'origin': r['origin'], 'where': r['where'], 'cause': r['cause']} for r in part['refused']]
    print(
        json.dumps(
            {
                'path': str(out),
                'entries': len(part['entries']),
                'keys': len({e['key'] for e in part['entries']}),
                'refused': refused,
                'collisions': [c['key'] for c in part['collisions']],
                'advisories': len(part['advisories']),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description='Build the declared part of the agent descriptions.')
    sub = parser.add_subparsers(dest='command', required=True)
    build_cmd = sub.add_parser('build', help='read the descriptions in --workdir and write the declared part')
    build_cmd.add_argument('--workdir', required=True, help='the folder holding plurai.config.yaml, simulation_prd.md and scenario_variables.yaml')
    build_cmd.add_argument('--out', default=None, help=f'default: <workdir>/artifacts/{DECLARED_PART_NAME}')
    build_cmd.set_defaults(func=_cmd_build)
    args = parser.parse_args(argv)
    try:
        import yaml  # noqa: F401, PLC0415
    except ImportError:
        print(f'ERROR: pyyaml is required to run declared_part.py, and {sys.executable} does not have it; use the project interpreter that has it.', file=sys.stderr)
        return 4
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
