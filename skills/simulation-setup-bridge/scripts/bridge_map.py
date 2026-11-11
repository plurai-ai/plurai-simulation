"""The stdlib validator of ``plurai_sim/bridge/bridge.map.json`` (schema ``plurai.bridge.map/1``).

The signatures, the ``Code`` members, ``MapFinding`` and the pure/filesystem split below are the interface the host,
the scripts and the tests build against.

Why a hand validator: the plugin's scripts run under a bare ``python3`` (3.9 floor) with no ``jsonschema``. The
JSON Schema at ``skills/simulation-setup-bridge/references/node/bridge.map.schema.json`` is the reference; a CI
test runs both over one good/bad corpus and fails when they disagree. The Node child checks only ``schema`` and
``child`` and trusts the rest, so this is the ONE validator.

Who calls it: ``verify_bridge.py`` (the static half, from the plugin) composes these checks in this order and
stops after the first function that returns a ``fail``:
``validate_map`` -> ``check_files`` -> ``check_evidence`` -> ``check_tools`` -> ``check_hosts``.
The workdir host (``plurai_sim/bridge/host.py``) cannot import this module (it is copied alone); it re-derives
the mapped tool set itself, by the rule ``mapped_tools`` documents.
"""

from __future__ import annotations

import enum
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)

SCHEMA_ID = 'plurai.bridge.map/1'

#: The plugin-source location of the shared protocol facts (provider/sink tables, refused packages, ...).
CONSTANTS_PATH = Path(__file__).resolve().parents[1] / 'references' / 'node' / 'runtime' / 'constants.json'


class Code(StrEnum):
    """Every code this module emits (the finding-code registry reads this enum)."""

    BRIDGE_MAP_INVALID = 'bridge_map_invalid'
    BRIDGE_EVIDENCE_INVALID = 'bridge_evidence_invalid'
    BRIDGE_TOOL_NOT_DECLARED = 'bridge_tool_not_declared'
    BRIDGE_TOOL_NOT_MOCKED = 'bridge_tool_not_mocked'
    BRIDGE_TOOL_UNREACHABLE = 'bridge_tool_unreachable'
    BRIDGE_PASSTHROUGH_IS_PRODUCTION = 'bridge_passthrough_is_production'


#: fail unless listed here.
WARN_CODES = frozenset({Code.BRIDGE_TOOL_UNREACHABLE})


@dataclass(frozen=True)
class MapFinding:
    code: Code
    #: RFC 6901 JSON pointer into the map ('' = the whole document), e.g. '/packages/@acme~1orders/calls/OrdersClient#get'.
    where: str
    #: One sentence naming the entry and the fix.
    message: str

    @property
    def severity(self) -> str:
        """'fail' | 'warn', read off the code (``WARN_CODES``), so a finding can never disagree with its code."""
        return 'warn' if self.code in WARN_CODES else 'fail'


def load_constants(path: Optional[Path] = None) -> Dict[str, Any]:
    """``constants.json`` (default: ``CONSTANTS_PATH``)."""
    return json.loads((path or CONSTANTS_PATH).read_text(encoding='utf-8'))


def load_map(path: Path) -> Tuple[Optional[Any], List[MapFinding]]:
    """``(doc, findings)``: the parsed map, or ``(None, [BRIDGE_MAP_INVALID])`` when it is unreadable or not JSON."""
    try:
        return json.loads(path.read_text(encoding='utf-8')), []
    except OSError as exc:
        why = f'{path} cannot be read ({type(exc).__name__}: {exc}); write the map at plurai_sim/bridge/bridge.map.json'
    except ValueError as exc:
        why = f'{path} is not JSON ({exc}); rewrite it as one JSON object of schema {SCHEMA_ID}'
    return None, [_finding(code=Code.BRIDGE_MAP_INVALID, where='', message=why)]


# --------------------------------------------------------------------------------------------
# the shape (the JSON Schema's rules, restated)
# --------------------------------------------------------------------------------------------


def _pattern(source: str) -> re.Pattern:
    """A schema ``pattern``, verbatim, with its closing ``$`` read as ECMA-262 reads it: the end of the string. Python's
    ``$`` also matches before a trailing newline, so ``'name\\n'`` would pass a search the schema refuses."""
    return re.compile(source[:-1] + r'\Z' if source.endswith('$') else source)


#: The schema's `pattern`s, verbatim (through `_pattern`). JSON Schema patterns are unanchored searches; `re.search` is
#: the same read.
_SPECIFIER = _pattern(r'^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*(/[A-Za-z0-9._~-]+)*$')
_IDENT = _pattern(r'^[A-Za-z_$][A-Za-z0-9_$]*$')
_NAME = _pattern(r'^[A-Za-z_][A-Za-z0-9_]*$')
_CALL_PATH = _pattern(r'^[A-Za-z_$][A-Za-z0-9_$]*(#[A-Za-z_$][A-Za-z0-9_$]*)?(\.[A-Za-z_$][A-Za-z0-9_$]*)*$')
_PARAM = _pattern(r'^(\.\.\.|\*)?[A-Za-z_][A-Za-z0-9_]*$')
_EVIDENCE = _pattern(r'^[^\s:][^:]*:[1-9][0-9]*$')
_ENTRY_PATH = _pattern(r'^[A-Za-z0-9_./-]+\.mjs$')
_ENTRY_PATH_ESCAPES = re.compile(r'(^/|\.\./)')
_MODULE_KEY_ESCAPES = re.compile(r'(^/|^\.\./|/\.\./)')
_REPLACE_FILE = _pattern(r'^replace/[A-Za-z0-9_.-]+\.mjs$')
_IMPL = _pattern(r'^replace/[A-Za-z0-9_.-]+\.mjs#[A-Za-z_$][A-Za-z0-9_$]*$')
_HOST_PATTERN = _pattern(r'^(\*\.)?[A-Za-z0-9*-]+(\.[A-Za-z0-9*-]+)*$')
_ROUTE_PATH = _pattern(r'^/[^\s?#]*$')
_MATCH_KEY = _pattern(r'^(query|body|header)\.[^\s]+$')
_ROUTE_SOURCE = _pattern(r'^(path\.[A-Za-z_][A-Za-z0-9_]*|query\.[^\s]+|body\.[^\s]+|body)$')

_ROOT_KEYS = ('schema', 'child', 'agent_root', 'entry', 'env', 'packages', 'modules', 'http', 'passthrough_hosts', 'sink_hosts', 'keep_real')
_ROOT_REQUIRED = ('schema', 'child', 'agent_root', 'entry')
_EXPORT_KINDS = ('ctor', 'fn', 'object', 'real')
_METHODS = ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', '*')
_PROXY_KEYS = ('exports', 'calls', 'local', 'revive')
_PACKAGE_KEYS = _PROXY_KEYS + ('evidence', 'subpaths')
_CALL_KEYS = ('tool', 'params', 'revive', 'probe_args', 'evidence')
_ROUTE_KEYS = ('method', 'host', 'path', 'match', 'tool', 'params', 'status', 'error_status', 'evidence')
_ROUTE_REQUIRED = ('method', 'host', 'path', 'tool', 'evidence')
_HOST_ROW_KEYS = ('host', 'env', 'reason', 'evidence')
#: `local` builtin -> the check on its one value (`const` takes any JSON value).
_LOCAL_BUILTINS = {
    'const': lambda value: True,
    'self': lambda value: value is True,
    'await_all': lambda value: value is True,
    'callback_self': lambda value: _is_int(value) and value >= 0,
    'impl': lambda value: isinstance(value, str) and bool(_IMPL.search(value)),
}
#: The refusal -> the seam the message points at instead (contract A.11).
_REFUSED_SEAM = {
    'raw_sql_driver': "one call path serves every SQL statement, so the path cannot name the tool: map the agent's own data-access module "
                      '(`modules.<path>.proxy`) instead; with none, the bench is refused',
    'builder_chain': 'a builder chain cannot be proxied: map the HTTP it speaks as an `http` route with a `match` on the table or path',
    'command_dispatch': 'send(new XCommand()) dispatches every operation through one call: map it as an `http` route with a `match` on '
                        '`header.X-Amz-Target` or the path',
}


def _finding(code: Code, where: str, message: str) -> MapFinding:
    return MapFinding(code=code, where=where, message=message)


def _pointer(*parts: Any) -> str:
    """RFC 6901: `~` -> `~0`, `/` -> `~1`."""
    return ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def _is_int(value: Any) -> bool:
    """JSON Schema's integer: a whole number, never a boolean."""
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())


def _matches(pattern: re.Pattern, value: Any) -> bool:
    return isinstance(value, str) and bool(pattern.search(value))


def _label(where: str) -> str:
    return where or 'the map'


class _Shape:
    """Walks one map, collecting a ``BRIDGE_MAP_INVALID`` per rule broken."""

    def __init__(self) -> None:
        self.findings: List[MapFinding] = []

    def fail(self, where: str, message: str) -> None:
        self.findings.append(_finding(code=Code.BRIDGE_MAP_INVALID, where=where, message=f'{_label(where)}: {message}'))

    def keys(self, node: Mapping[str, Any], where: str, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> None:
        for key in node:
            if key not in allowed:
                self.fail(_pointer_join(where, key), f'`{key}` is not a key here (allowed: {", ".join(allowed)}); remove it')
        for key in required:
            if key not in node:
                self.fail(where, f'the required `{key}` is missing; add it')

    def obj(self, node: Any, where: str, what: str) -> bool:
        if isinstance(node, dict):
            return True
        self.fail(where, f'{what} must be a JSON object, not {type(node).__name__}')
        return False

    def array(self, node: Any, where: str, what: str) -> bool:
        if isinstance(node, list):
            return True
        self.fail(where, f'{what} must be a JSON array, not {type(node).__name__}')
        return False

    def string(self, node: Any, where: str, what: str, pattern: Optional[re.Pattern] = None, *, min_length: int = 0) -> bool:
        if not isinstance(node, str):
            self.fail(where, f'{what} must be a string, not {type(node).__name__}')
            return False
        if len(node) < min_length:
            self.fail(where, f'{what} must not be empty')
            return False
        if pattern is not None and not pattern.search(node):
            self.fail(where, f'{what} {node!r} does not match {pattern.pattern}')
            return False
        return True

    def evidence(self, node: Any, where: str) -> None:
        self.string(node, where, 'evidence (`<agent-relative file>:<line>`)', _EVIDENCE)

    def reason(self, node: Any, where: str) -> None:
        self.string(node, where, 'a reason', min_length=1)

    # --- the definitions ---------------------------------------------------------------------

    def entry(self, node: Any, where: str) -> None:
        if not self.obj(node, where, '`entry`'):
            return
        self.keys(node, where, ('path', 'resume'), ('path', 'resume'))
        if 'path' in node and self.string(node['path'], where + '/path', '`entry.path`', _ENTRY_PATH) and _ENTRY_PATH_ESCAPES.search(node['path']):
            self.fail(where + '/path', f'`entry.path` {node["path"]!r} must stay under plurai_sim/bridge/ (no leading `/`, no `../`)')
        if 'resume' in node:
            resume = node['resume']
            if resume == 'seed':
                return
            ok = isinstance(resume, dict) and list(resume) == ['unsupported'] and isinstance(resume['unsupported'], str) and resume['unsupported']
            if not ok:
                self.fail(where + '/resume', '`entry.resume` is "seed" (the entry exports seed) or {"unsupported": "<why it cannot resume>"}')

    def env(self, node: Any, where: str, constants: Mapping[str, Any]) -> None:
        if not self.obj(node, where, '`env`'):
            return
        prefix = str(constants.get('child_env_prefix') or 'PLURAI_BRIDGE_')
        forbidden = set(constants.get('map_env_forbidden') or ('NODE_OPTIONS',))
        for name, row in node.items():
            at = _pointer_join(where, name)
            if not _NAME.search(name):
                self.fail(at, f'env name {name!r} is not a variable name')
            elif name.startswith(prefix) or name in forbidden:
                self.fail(at, f'env name {name!r} is the bridge\'s own; the map never sets {prefix}* or {", ".join(sorted(forbidden))}')
            if not self.obj(row, at, f'`env.{name}`'):
                continue
            self.keys(row, at, ('value', 'reason', 'evidence'), ('value', 'reason', 'evidence'))
            if 'value' in row:
                self.string(row['value'], at + '/value', f'`env.{name}.value`')
            if 'reason' in row:
                self.reason(row['reason'], at + '/reason')
            if 'evidence' in row:
                self.evidence(row['evidence'], at + '/evidence')

    def revive(self, node: Any, where: str) -> None:
        if not self.obj(node, where, '`revive`'):
            return
        self.keys(node, where, ('dates',), ('dates',))
        dates = node.get('dates')
        if 'dates' in node and self.array(dates, where + '/dates', '`revive.dates`'):
            for i, key in enumerate(dates):
                self.string(key, _pointer_join(where + '/dates', i), 'a revived key', min_length=1)
            hashable = [d for d in dates if isinstance(d, str)]
            if len(set(hashable)) != len(hashable):
                self.fail(where + '/dates', '`revive.dates` lists a key twice')

    def call(self, node: Any, where: str) -> None:
        if not self.obj(node, where, 'a mapped call'):
            return
        self.keys(node, where, _CALL_KEYS, ('tool', 'params'))
        if 'tool' in node:
            self.string(node['tool'], where + '/tool', '`tool`', _NAME)
        if 'params' in node and self.array(node['params'], where + '/params', '`params`'):
            for i, param in enumerate(node['params']):
                self.string(param, _pointer_join(where + '/params', i), 'a param (`kw`, `...kw` or `*kw`)', _PARAM)
        if 'revive' in node:
            self.revive(node['revive'], where + '/revive')
        if 'probe_args' in node:
            self.obj(node['probe_args'], where + '/probe_args', '`probe_args`')
        if 'evidence' in node:
            self.evidence(node['evidence'], where + '/evidence')

    def local(self, node: Any, where: str) -> None:
        builtins = ', '.join('{%s}' % name for name in _LOCAL_BUILTINS)
        if not isinstance(node, dict) or len(node) != 1 or next(iter(node)) not in _LOCAL_BUILTINS:
            self.fail(where, f'a `local` is exactly one of {builtins}')
            return
        (name, value), = node.items()
        if not _LOCAL_BUILTINS[name](value):
            want = {'self': 'true', 'await_all': 'true', 'callback_self': 'an argument index >= 0', 'impl': '"replace/<file>.mjs#<export>"'}[name]
            self.fail(where + '/' + name, f'`{name}` takes {want}, not {value!r}')

    def proxy_body(self, node: Any, where: str, allowed: Tuple[str, ...], required: Tuple[str, ...]) -> None:
        if not self.obj(node, where, 'a proxy'):
            return
        self.keys(node, where, allowed, required)
        exports = node.get('exports')
        if 'exports' in node and self.obj(exports, where + '/exports', '`exports`'):
            if not exports:
                self.fail(where + '/exports', '`exports` declares no export; name at least one')
            for name, kind in exports.items():
                at = _pointer_join(where + '/exports', name)
                if not _IDENT.search(name):
                    self.fail(at, f'export name {name!r} is not an identifier')
                if kind not in _EXPORT_KINDS:
                    self.fail(at, f'export kind {kind!r} is not one of {", ".join(_EXPORT_KINDS)}')
        calls = node.get('calls')
        if 'calls' in node and self.obj(calls, where + '/calls', '`calls`'):
            for path, call in calls.items():
                at = _pointer_join(where + '/calls', path)
                if not _CALL_PATH.search(path):
                    self.fail(at, f'call path {path!r} is not `<export>`, `<export>.<member>…` or `<export>#<member>…`')
                self.call(call, at)
        local = node.get('local')
        if 'local' in node and self.obj(local, where + '/local', '`local`'):
            for path, builtin in local.items():
                at = _pointer_join(where + '/local', path)
                if not _CALL_PATH.search(path):
                    self.fail(at, f'local path {path!r} is not `<export>`, `<export>.<member>…` or `<export>#<member>…`')
                self.local(builtin, at)
        if 'revive' in node:
            self.revive(node['revive'], where + '/revive')
        # The schema's anyOf: a non-empty `calls` (a non-object one is already a type error above), or every export `real`.
        calls_given = 'calls' in node and (not isinstance(calls, dict) or bool(calls))
        all_real = not isinstance(exports, dict) or all(kind == 'real' for kind in exports.values())
        if not calls_given and not all_real:
            self.fail(where, 'maps no call, and not every export is `real`: map at least one call, or declare every export `real`')

    def package(self, node: Any, where: str) -> None:
        self.proxy_body(node, where, _PACKAGE_KEYS, ('exports', 'evidence'))
        if not isinstance(node, dict):
            return
        if 'evidence' in node:
            self.evidence(node['evidence'], where + '/evidence')
        if 'subpaths' in node and self.array(node['subpaths'], where + '/subpaths', '`subpaths`'):
            for i, sub in enumerate(node['subpaths']):
                self.string(sub, _pointer_join(where + '/subpaths', i), 'a subpath specifier', _SPECIFIER)
            hashable = [s for s in node['subpaths'] if isinstance(s, str)]
            if len(set(hashable)) != len(hashable):
                self.fail(where + '/subpaths', '`subpaths` lists a specifier twice')

    def module(self, node: Any, where: str) -> None:
        if not self.obj(node, where, 'a module entry'):
            return
        if 'proxy' in node and 'replace' in node:
            self.fail(where, 'a module entry is EITHER a `proxy` or a `replace`, never both')
            return
        if 'proxy' not in node and 'replace' not in node:
            self.fail(where, 'a module entry needs a `proxy` (a client facade) or a `replace` (framework infra)')
            return
        if 'proxy' in node:
            self.keys(node, where, ('evidence', 'proxy'), ('evidence', 'proxy'))
            self.proxy_body(node['proxy'], where + '/proxy', _PROXY_KEYS, ('exports',))
        else:
            self.keys(node, where, ('evidence', 'replace'), ('evidence', 'replace'))
            self.string(node['replace'], where + '/replace', '`replace`', _REPLACE_FILE)
        if 'evidence' in node:
            self.evidence(node['evidence'], where + '/evidence')

    def host_row(self, node: Any, where: str) -> None:
        if not self.obj(node, where, 'a host row'):
            return
        self.keys(node, where, _HOST_ROW_KEYS, ('reason', 'evidence'))
        if ('host' in node) == ('env' in node):
            self.fail(where, 'a host row names exactly one of `host` (a hostname or `*.` suffix) or `env` (a variable holding a URL)')
        if 'host' in node:
            self.string(node['host'], where + '/host', '`host`', _HOST_PATTERN)
        if 'env' in node:
            self.string(node['env'], where + '/env', '`env`', _NAME)
        if 'reason' in node:
            self.reason(node['reason'], where + '/reason')
        if 'evidence' in node:
            self.evidence(node['evidence'], where + '/evidence')

    def route(self, node: Any, where: str) -> None:
        if not self.obj(node, where, 'an http route'):
            return
        self.keys(node, where, _ROUTE_KEYS, _ROUTE_REQUIRED)
        if 'method' in node and node['method'] not in _METHODS:
            self.fail(where + '/method', f'method {node["method"]!r} is not one of {", ".join(_METHODS)}')
        if 'host' in node:
            host = node['host']
            if isinstance(host, dict):
                if list(host) != ['env'] or not _matches(_NAME, host['env']):
                    self.fail(where + '/host', 'a route host is a hostname pattern or {"env": "<VAR holding a URL>"}')
            else:
                self.string(host, where + '/host', 'the route host', _HOST_PATTERN)
        if 'path' in node:
            self.string(node['path'], where + '/path', 'the route path', _ROUTE_PATH)
        if 'match' in node and self.obj(node['match'], where + '/match', '`match`'):
            for key, value in node['match'].items():
                at = _pointer_join(where + '/match', key)
                if not _MATCH_KEY.search(key):
                    self.fail(at, f'match key {key!r} is not `query.<k>`, `body.<k>` or `header.<k>`')
                self.string(value, at, f'match value of {key!r}')
        if 'tool' in node:
            self.string(node['tool'], where + '/tool', '`tool`', _NAME)
        if 'params' in node and self.obj(node['params'], where + '/params', '`params`'):
            for key, source in node['params'].items():
                at = _pointer_join(where + '/params', key)
                if not _NAME.search(key):
                    self.fail(at, f'param name {key!r} is not a kwarg name')
                self.string(source, at, f'the source of {key!r} (`path.<p>`, `query.<k>`, `body.<k>` or `body`)', _ROUTE_SOURCE)
        for key in ('status', 'error_status'):
            if key in node and not (_is_int(node[key]) and 100 <= node[key] <= 599):
                self.fail(where + '/' + key, f'`{key}` is an HTTP status from 100 to 599, not {node[key]!r}')
        if 'evidence' in node:
            self.evidence(node['evidence'], where + '/evidence')


def _pointer_join(where: str, *parts: Any) -> str:
    return where + _pointer(*parts)


def _shape_findings(doc: Any, constants: Mapping[str, Any]) -> List[MapFinding]:
    v = _Shape()
    if not v.obj(doc, '', 'the map'):
        return v.findings
    v.keys(doc, '', _ROOT_KEYS, _ROOT_REQUIRED)
    if 'schema' in doc and doc['schema'] != SCHEMA_ID:
        v.fail('/schema', f'`schema` is {SCHEMA_ID!r}, not {doc["schema"]!r}')
    if 'child' in doc and doc['child'] != 'node':
        v.fail('/child', f'`child` is "node" (the only bridge child that ships), not {doc["child"]!r}')
    if 'agent_root' in doc and v.string(doc['agent_root'], '/agent_root', '`agent_root`', min_length=1) and doc['agent_root'].startswith('/'):
        v.fail('/agent_root', '`agent_root` is relative to plurai_sim/, never absolute')
    if 'entry' in doc:
        v.entry(doc['entry'], '/entry')
    if 'env' in doc:
        v.env(doc['env'], '/env', constants)
    if 'packages' in doc and v.obj(doc['packages'], '/packages', '`packages`'):
        for spec, entry in doc['packages'].items():
            at = _pointer('packages', spec)
            if not _SPECIFIER.search(spec):
                v.fail(at, f'{spec!r} is not a bare npm specifier')
            v.package(entry, at)
    if 'modules' in doc and v.obj(doc['modules'], '/modules', '`modules`'):
        for path, entry in doc['modules'].items():
            at = _pointer('modules', path)
            if not path or _MODULE_KEY_ESCAPES.search(path):
                v.fail(at, f'module key {path!r} is an agent-relative source path (no leading `/`, no `../`)')
            v.module(entry, at)
    if 'http' in doc and v.array(doc['http'], '/http', '`http`'):
        for i, route in enumerate(doc['http']):
            v.route(route, _pointer('http', i))
    for block in ('passthrough_hosts', 'sink_hosts'):
        if block in doc and v.array(doc[block], '/' + block, f'`{block}`'):
            for i, row in enumerate(doc[block]):
                v.host_row(row, _pointer(block, i))
    if 'keep_real' in doc and v.obj(doc['keep_real'], '/keep_real', '`keep_real`'):
        for name, why in doc['keep_real'].items():
            v.reason(why, _pointer('keep_real', name))
    return v.findings


# --------------------------------------------------------------------------------------------
# the rules the schema cannot express
# --------------------------------------------------------------------------------------------


def _proxy_bodies(doc: Mapping[str, Any]) -> List[Tuple[str, Mapping[str, Any]]]:
    """(pointer, body) for every package and module proxy: the places calls and locals live."""
    out: List[Tuple[str, Mapping[str, Any]]] = []
    for spec, entry in _rows(doc.get('packages')):
        out.append((_pointer('packages', spec), entry))
    for path, entry in _rows(doc.get('modules')):
        if isinstance(entry.get('proxy'), dict):
            out.append((_pointer('modules', path, 'proxy'), entry['proxy']))
    return out


def _package_name(spec: str) -> str:
    parts = spec.split('/')
    return '/'.join(parts[:2]) if spec.startswith('@') else parts[0]


def _refusal(spec: str, refused: Mapping[str, Any]) -> Optional[str]:
    name = _package_name(spec)
    for shape in ('raw_sql_driver', 'builder_chain'):
        if name in (refused.get(shape) or ()):
            return shape
    if any(spec.startswith(prefix) for prefix in (refused.get('command_dispatch_prefix') or ())):
        return 'command_dispatch'
    return None


def _segment_params(path: str) -> List[str]:
    return [segment[1:] for segment in path.split('/') if segment.startswith(':') and len(segment) > 1]


def _rule_findings(doc: Mapping[str, Any], constants: Mapping[str, Any]) -> List[MapFinding]:
    out: List[MapFinding] = []

    def fail(where: str, message: str) -> None:
        out.append(_finding(code=Code.BRIDGE_MAP_INVALID, where=where, message=f'{_label(where)}: {message}'))

    for where, body in _proxy_bodies(doc):
        calls = body.get('calls') if isinstance(body.get('calls'), dict) else {}
        local = body.get('local') if isinstance(body.get('local'), dict) else {}
        for path in sorted(set(calls) & set(local)):
            fail(_pointer_join(where, 'local', path), f'{path!r} is both a mapped call and a `local`; it is one or the other')
        for path, call in calls.items():
            params = call.get('params') if isinstance(call, dict) else None
            if not isinstance(params, list):
                continue
            rests = [i for i, p in enumerate(params) if isinstance(p, str) and p.startswith('*')]
            if len(rests) > 1 or (rests and rests[0] != len(params) - 1):
                fail(_pointer_join(where, 'calls', path, 'params'), 'a `*kw` param collects the remaining arguments, so there is at most one and it is last')
    refused = constants.get('refused_packages') or {}
    packages = doc.get('packages') if isinstance(doc.get('packages'), dict) else {}
    for spec, entry in packages.items():
        shape = _refusal(spec, refused)
        if shape is not None:
            fail(_pointer('packages', spec), f'{spec!r} is refused as `{shape}`: {_REFUSED_SEAM[shape]}')
        subpaths = entry.get('subpaths') if isinstance(entry, dict) and isinstance(entry.get('subpaths'), list) else []
        for i, sub in enumerate(subpaths):
            if not isinstance(sub, str):
                continue
            at = _pointer('packages', spec, 'subpaths', i)
            if not sub.startswith(spec + '/'):
                fail(at, f'{sub!r} is not a subpath of {spec!r}; a subpath starts with `{spec}/`')
            shape = _refusal(sub, refused)
            if shape is not None:
                fail(at, f'{sub!r} is refused as `{shape}`: {_REFUSED_SEAM[shape]}')
    modules = doc.get('modules') if isinstance(doc.get('modules'), dict) else {}
    for path in modules:
        if '..' in path.split('/') and not _MODULE_KEY_ESCAPES.search(path):  # the schema's own `not` refuses the rest
            fail(_pointer('modules', path), f'module key {path!r} has a `..` segment; name the agent-relative source file itself')
    for where, evidence, _spec in _evidence_sites(doc):
        rel = evidence.rpartition(':')[0]
        if rel.startswith('/') or '..' in rel.split('/'):
            fail(where, f'evidence {evidence!r} must name a file under the agent root (no leading `/`, no `..` segment)')
    for where, host in _host_patterns(doc):
        if _too_broad(host):
            fail(where, f'host pattern {host!r} names no literal label, so it matches every host; name the host or its domain')
    for i, route in enumerate(doc.get('http') or ()):
        if not (isinstance(route, dict) and isinstance(route.get('path'), str)):
            continue
        where = _pointer('http', i)
        segments = _segment_params(route['path'])
        twice = sorted({name for name in segments if segments.count(name) > 1})
        if twice:
            fail(where + '/path', f'path param(s) {", ".join(twice)} bind more than one segment; name each segment once')
        params = route.get('params')
        if isinstance(params, dict):
            for key, source in params.items():
                if isinstance(source, str) and source.startswith('path.') and source[5:] not in segments:
                    fail(_pointer_join(where, 'params', key), f'{source!r} names no `:{source[5:]}` segment of {route["path"]!r}')
        elif params is None:
            match = route.get('match') if isinstance(route.get('match'), dict) else {}
            fixed = [key.split('.', 1)[1] for key in match if isinstance(key, str) and key.startswith(('query.', 'body.'))]
            clash = sorted({name for name in segments if name in fixed} | {name for name in fixed if fixed.count(name) > 1})
            if clash:
                fail(where, f'the default params (path params ∪ query ∪ body keys) clash on {", ".join(clash)}; declare `params` explicitly')
    return out


def validate_map(doc: Any, *, constants: Optional[Mapping[str, Any]] = None) -> List[MapFinding]:
    """PURE (no filesystem). Every rule of the JSON Schema, plus what it cannot express, all as ``BRIDGE_MAP_INVALID``:

    - a ``*x`` param that is not last, or more than one;
    - an http route ``params`` default whose path/query/body keys clash;
    - a ``local`` and a ``calls`` entry on the same path;
    - a ``packages`` key or ``subpaths`` entry listed in ``constants['refused_packages']`` (``raw_sql_driver``,
      ``builder_chain``) or under a ``command_dispatch_prefix``; the message names the refusal and the seam to use instead;
    - a ``subpaths`` entry that does not start with ``<key>/``;
    - a ``modules`` key with a ``..`` segment, and an ``evidence`` file that is absolute or has a ``..`` segment;
    - a host pattern with no literal label (``*``, ``*.*``): it would match every host.
    """
    constants = load_constants() if constants is None else constants
    findings = _shape_findings(doc, constants)
    if isinstance(doc, dict):
        findings += _rule_findings(doc, constants)
    return findings


# --------------------------------------------------------------------------------------------
# the bench's files
# --------------------------------------------------------------------------------------------


def check_files(doc: Mapping[str, Any], *, bridge_dir: Path) -> List[MapFinding]:
    """``BRIDGE_MAP_INVALID`` for a missing ``entry.path``, ``modules.*.replace`` or ``local.*.impl`` file under ``bridge_dir``."""
    wanted: List[Tuple[str, str, str]] = [('/entry/path', str(doc['entry']['path']), 'the entry module (bridge-contract A.12)')]
    for path, entry in (doc.get('modules') or {}).items():
        if 'replace' in entry:
            wanted.append((_pointer('modules', path, 'replace'), str(entry['replace']), f'the replacement for {path}'))
    for where, body in _proxy_bodies(doc):
        for path, builtin in (body.get('local') or {}).items():
            if 'impl' in builtin:
                wanted.append((_pointer_join(where, 'local', path, 'impl'), str(builtin['impl']).split('#', 1)[0], f'the local `{path}`'))
    return [
        _finding(code=Code.BRIDGE_MAP_INVALID, where=where, message=f'{where}: {rel} ({what}) is not a file under {bridge_dir}; write it, or fix the path')
        for where, rel, what in wanted
        if not (bridge_dir / rel).is_file()
    ]


def _rows(node: Any) -> List[Tuple[Any, Dict[str, Any]]]:
    """(key, row) for each object row of a JSON object or array; anything else reads as no rows."""
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    return [(key, row) for key, row in items if isinstance(row, dict)]


def _evidence_sites(doc: Mapping[str, Any]) -> List[Tuple[str, str, Optional[str]]]:
    """(pointer, evidence, the specifier its file must import or None) for every string ``evidence`` in the map. Reads
    a map of any shape: the rules run beside the shape check, not after it."""
    sites: List[Tuple[str, str, Optional[str]]] = []

    def add(where: str, row: Mapping[str, Any], spec: Optional[str] = None) -> None:
        if isinstance(row.get('evidence'), str):
            sites.append((where + '/evidence', row['evidence'], spec))

    for name, row in _rows(doc.get('env')):
        add(_pointer('env', name), row)
    for spec, entry in _rows(doc.get('packages')):
        add(_pointer('packages', spec), entry, spec)
    for path, entry in _rows(doc.get('modules')):
        add(_pointer('modules', path), entry)
    for where, body in _proxy_bodies(doc):
        for path, call in _rows(body.get('calls')):
            add(_pointer_join(where, 'calls', path), call)
    for i, route in _rows(doc.get('http')):
        add(_pointer('http', i), route)
    for block in ('passthrough_hosts', 'sink_hosts'):
        for i, row in _rows(doc.get(block)):
            add(_pointer(block, i), row)
    return sites


def _host_patterns(doc: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """(pointer, pattern) for every literal host pattern: a route's string ``host`` and a host row's ``host``."""
    found = [(_pointer('http', i, 'host'), route['host']) for i, route in _rows(doc.get('http')) if isinstance(route.get('host'), str)]
    for block in ('passthrough_hosts', 'sink_hosts'):
        found += [(_pointer(block, i, 'host'), row['host']) for i, row in _rows(doc.get(block)) if isinstance(row.get('host'), str)]
    return found


def _imports(text: str, spec: str) -> bool:
    """Whether a source text names ``spec`` as a quoted module specifier (or a subpath of it)."""
    return re.search(r'([\'"`])' + re.escape(spec) + r'(/[^\'"`]*)?\1', text) is not None


def check_evidence(doc: Mapping[str, Any], *, agent_root: Path) -> List[MapFinding]:
    """``BRIDGE_EVIDENCE_INVALID`` when an ``evidence`` file is missing or its line is out of range, or when a ``packages``
    entry's evidence file does not contain the specifier."""
    findings: List[MapFinding] = []
    texts: Dict[str, Optional[str]] = {}
    for where, evidence, spec in _evidence_sites(doc):
        rel, _, line = str(evidence).rpartition(':')
        if rel not in texts:
            path = agent_root / rel
            try:
                texts[rel] = path.read_text(encoding='utf-8', errors='replace') if path.is_file() else None
            except OSError:
                texts[rel] = None
        text = texts[rel]
        if text is None:
            findings.append(_finding(code=Code.BRIDGE_EVIDENCE_INVALID, where=where, message=f'{where}: {evidence!r} names {rel}, which is not a file under the agent root {agent_root}; cite the agent file that shows it'))
            continue
        count = len(text.splitlines())
        if int(line) > count:
            findings.append(_finding(code=Code.BRIDGE_EVIDENCE_INVALID, where=where, message=f'{where}: {evidence!r} is past the end of {rel} ({count} lines); cite the line that shows it'))
        elif spec is not None and not _imports(text, spec):
            findings.append(_finding(code=Code.BRIDGE_EVIDENCE_INVALID, where=where, message=f'{where}: {rel} never imports {spec!r}; cite the agent file:line that imports the package'))
    return findings


# --------------------------------------------------------------------------------------------
# the map against the config and the harness
# --------------------------------------------------------------------------------------------


def mapped_tools(doc: Mapping[str, Any]) -> Dict[str, List[str]]:
    """tool -> the JSON pointers that route to it: every ``packages.*.calls.*.tool``, ``modules.*.proxy.calls.*.tool``
    and ``http[*].tool``. Assumes ``validate_map`` returned no fail."""
    tools: Dict[str, List[str]] = {}
    for where, body in _proxy_bodies(doc):
        for path, call in (body.get('calls') or {}).items():
            tools.setdefault(call['tool'], []).append(_pointer_join(where, 'calls', path))
    for i, route in enumerate(doc.get('http') or ()):
        tools.setdefault(route['tool'], []).append(_pointer('http', i))
    return tools


def _config_mock_tools(config: Mapping[str, Any]) -> Set[str]:
    """The config's `mode: mock` tools (an absent mode is mock)."""
    tools = config.get('tools') if isinstance(config, Mapping) else None
    if not isinstance(tools, Mapping):
        return set()
    return {str(name) for name, spec in tools.items() if (spec.get('mode', 'mock') if isinstance(spec, Mapping) else 'mock') == 'mock'}


def check_tools(doc: Mapping[str, Any], *, config: Mapping[str, Any], dispatch_names: Set[str]) -> List[MapFinding]:
    """Against the parsed ``plurai.config.yaml`` and the names in ``simulate.py``'s dispatch table:
    ``BRIDGE_TOOL_NOT_DECLARED`` (mapped, but not a ``mode: mock`` config tool), ``BRIDGE_TOOL_NOT_MOCKED`` (mapped, but not in
    ``dispatch_names``), ``BRIDGE_TOOL_UNREACHABLE`` (warn: a config mock tool no entry maps)."""
    declared = _config_mock_tools(config)
    mapped = mapped_tools(doc)
    findings: List[MapFinding] = []
    for tool in sorted(mapped):
        where = mapped[tool][0]
        sites = ', '.join(mapped[tool])
        if tool not in declared:
            findings.append(_finding(
                code=Code.BRIDGE_TOOL_NOT_DECLARED, where=where,
                message=f'{sites} routes to {tool!r}, which plurai.config.yaml does not declare as a `mode: mock` tool; declare it, or route the call to the tool that is'))
        if tool not in dispatch_names:
            findings.append(_finding(
                code=Code.BRIDGE_TOOL_NOT_MOCKED, where=where,
                message=f'{sites} routes to {tool!r}, which is not in simulate.py\'s dispatch table (`_MOCKED`); add its `@plurai.mock` and its `mock_wrap` row'))
    for tool in sorted(declared - set(mapped)):
        findings.append(_finding(
            code=Code.BRIDGE_TOOL_UNREACHABLE, where='',
            message=f'plurai.config.yaml declares the mock tool {tool!r} and no map entry routes to it, so the agent can never reach its mock '
            'through the bridge; map the call that reaches it, or set it `mode: real` if the agent never makes it'))
    return findings


def _norm_host(value: str) -> str:
    """A host or host pattern, lower-cased, trailing dots stripped."""
    return value.strip().lower().rstrip('.')


def _host_regex(pattern: str) -> re.Pattern:
    """A host pattern as a full-match regex (bridge-contract A.7; ``egress.mjs`` ``hostMatcher`` reads it the same way): a ``*`` matches one or more characters inside one label, never a
    dot; a leading ``*.`` matches one or more whole labels, so never the apex. Case-insensitive; trailing dots and a port
    are ignored on the pattern as on the host."""
    pattern = _hostname(pattern)
    prefix = ''
    if pattern.startswith('*.'):
        prefix, pattern = r'(?:[^.]+\.)+', pattern[2:]
    labels = [re.escape(label).replace(r'\*', '[^.]+') for label in pattern.split('.')]
    return re.compile(r'\A' + prefix + r'\.'.join(labels) + r'\Z')


def _too_broad(pattern: str) -> bool:
    """A pattern with no literal label (a label with no ``*``): ``*``, ``*.*``, ``a*.b*``. It matches nearly any host."""
    return all('*' in label for label in _hostname(pattern).split('.'))


def _hostname(value: str) -> str:
    """`host`, `host:port`, `[v6]:port` or a URL -> the lower-case hostname, trailing dots stripped."""
    value = value.strip().lower()
    if '://' in value:
        value = value.split('://', 1)[1]
    value = value.split('/', 1)[0].rsplit('@', 1)[-1]
    if value.startswith('['):
        return value[1:].split(']', 1)[0]
    return _norm_host(value.split(':', 1)[0] if value.count(':') == 1 else value)


def _host_matches(pattern: str, host: str) -> bool:
    """Whether a concrete host (a hostname, `host:port` or a URL) is one the pattern names."""
    return _host_regex(pattern).match(_hostname(host)) is not None


def _hosts_overlap(a: str, b: str) -> bool:
    """Either side names the other: a pattern's literal text read as a host by the other's regex."""
    a, b = _hostname(a), _hostname(b)
    return bool(_host_regex(a).match(b) or _host_regex(b).match(a))


def check_hosts(doc: Mapping[str, Any], *, config: Mapping[str, Any]) -> List[MapFinding]:
    """``BRIDGE_PASSTHROUGH_IS_PRODUCTION`` when a ``passthrough_hosts`` / ``sink_hosts`` ``host`` row matches a
    ``config['egress']['production_hosts'][*]['host']`` (hostname match, ``*.`` suffixes honoured). ``env`` rows are
    not resolvable statically and are skipped."""
    egress = config.get('egress') if isinstance(config, Mapping) else None
    rows = egress.get('production_hosts') if isinstance(egress, Mapping) else None
    production = [str(r['host']) for r in rows or () if isinstance(r, Mapping) and isinstance(r.get('host'), str) and r['host'].strip()]
    findings: List[MapFinding] = []
    for block, verb in (('passthrough_hosts', 'lets the agent reach'), ('sink_hosts', 'answers 202 for')):
        for i, row in enumerate(doc.get(block) or ()):
            host = row.get('host')
            if not isinstance(host, str):
                continue
            hit = next((p for p in production if _hosts_overlap(host, p)), None)
            if hit is not None:
                findings.append(_finding(
                    code=Code.BRIDGE_PASSTHROUGH_IS_PRODUCTION, where=_pointer(block, i, 'host'),
                    message=f'{_pointer(block, i, "host")}: {host!r} {verb} {hit!r}, a production host of egress.production_hosts; '
                    'route that host to its mock tool with an `http` entry instead'))
    return findings
