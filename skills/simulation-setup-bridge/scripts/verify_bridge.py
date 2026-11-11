#!/usr/bin/env python3
"""Verify a bridge bench: the map is sound (static), and every mapped entry reaches its mocked tool (live).

    python3 verify_bridge.py <plurai_sim> [--static]        # or <plurai_sim>/simulate.py

STATIC (stdlib + PyYAML, no Node, no SDK): ``bridge/bridge.map.json`` through ``bridge_map.py`` in the order its
docstring fixes (shape -> files -> evidence -> tools -> hosts, stopping after the first that fails), with the
dispatch table read from ``simulate.py``'s ``_MOCKED`` by its source, plus the stale-copy check of the bridge's
verbatim workdir copies and a warn for each dispatched mock parameter with no default.

LIVE (the plurai SDK, Node and the installed bridge runtime; no model call): ``simulate.py`` is imported on a local
mock session seeded with ``agent.smoke_inputs[0]`` of ``bench_manifest.json`` (else an empty world), and
``simulate._bridge_host().route_probe()`` asks the child to call every mapped entry once. Each mapped entry must have
been swapped and dispatched, and ``plurai.get_events`` must hold a ``tool_call`` of its tool (a mock-side error still
counts as reached; any other error on a probe result fails it); the egress guard must refuse both a ``fetch`` and a
socket; ``entry.resume`` must agree with the entry's ``seed`` export; and every ``env`` row of ``passthrough_hosts`` /
``sink_hosts``, resolved as the child would resolve it, must not name a production host.

Exit 0: no fail (warns allowed). Exit 1: a finding fails the bench. Exit 2: no verdict (an input is unreadable, or
the live half's SDK / Node / runtime is absent). On a fail, the first line of each failing finding is also the last
output on stderr. The last stdout line is JSON:
``{"verdict": "pass|fail|no_verdict", "static": ..., "live": ..., "findings": [...], "reason"?: ...}``.
"""

from __future__ import annotations

import ast
import asyncio
import enum
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_map  # noqa: E402 - the one map validator, beside this script

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = Path(__file__).resolve().parents[1]
MAP_PATH = Path('bridge') / 'bridge.map.json'
RUNTIME_DIR = Path('bridge') / 'runtime'
#: workdir-relative copy -> the plugin file it is copied from, verbatim (B4): the host and every runtime file.
_PATHS = bridge_map.load_constants()['paths']
BRIDGE_COPIES: Dict[str, Path] = {
    _PATHS['host']: SKILL_DIR / 'references' / 'host' / 'bridge_host.py',
    **{f'{_PATHS["runtime_dir"]}/{name}': SKILL_DIR / 'references' / 'node' / 'runtime' / name for name in _PATHS['runtime_files']},
}
SESSION_ID = 'verify-bridge'


class Code(StrEnum):
    """Every finding code this script emits beyond the static map codes, which are ``bridge_map.Code``'s own."""

    BRIDGE_COPY_STALE = 'bridge_copy_stale'  # a verbatim bridge copy in the workdir is absent or not this plugin's
    BRIDGE_PROBE_FAILED = 'bridge_probe_failed'  # the route probe could not run to its reply (start refused, child died)
    BRIDGE_ROUTE_NOT_SWAPPED = 'bridge_route_not_swapped'  # an entry's specifier did not land on the swap, or a replace did not load
    BRIDGE_ROUTE_UNREACHED = 'bridge_route_unreached'  # a mapped entry dispatched nothing, or its mock recorded no call
    BRIDGE_EGRESS_OPEN = 'bridge_egress_open'  # the child's egress guard let a probe fetch or socket out
    BRIDGE_RESUME_MISMATCH = 'bridge_resume_mismatch'  # `entry.resume` disagrees with the entry's `seed` export
    BRIDGE_MOCK_PARAM_REQUIRED = 'bridge_mock_param_required'  # warn: a dispatched mock has a parameter with no default


@dataclass(frozen=True)
class Finding:
    severity: str
    code: Union[Code, bridge_map.Code]
    where: str
    message: str

    def render(self) -> str:
        return f'{"✗" if self.severity == "fail" else "!"} [{self.code}] {self.message}'


class NoVerdict(Exception):
    """An input or a prerequisite is missing: the check reached no verdict (exit 2)."""


def _from_map(finding: Any) -> Finding:
    return Finding(severity=finding.severity, code=finding.code, where=finding.where, message=finding.message)


# --------------------------------------------------------------------------------------------
# reading the bench
# --------------------------------------------------------------------------------------------


def read_config(workdir: Path) -> Dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415 - the static half needs it only for the config
    except ImportError as exc:
        raise NoVerdict(f'PyYAML is not importable from {sys.executable}, so plurai.config.yaml cannot be read') from exc
    path = workdir / 'plurai.config.yaml'
    try:
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise NoVerdict(f'{path} cannot be read ({type(exc).__name__}: {exc})') from exc
    return config if isinstance(config, dict) else {}


def _sequence_names(node: ast.AST, assigned: Mapping[str, ast.AST]) -> Optional[List[ast.AST]]:
    """The elements of a literal tuple/list, or of the module-level literal a name is bound to."""
    if isinstance(node, ast.Name) and node.id in assigned:
        node = assigned[node.id]
    return list(node.elts) if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else None


def _mock_decorated(tree: ast.Module) -> Set[str]:
    """The tool names of every ``@plurai.mock('<tool>')`` handler."""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and ast.unparse(deco.func).rsplit('.', 1)[-1] == 'mock' and deco.args
                        and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str)):
                    names.add(deco.args[0].value)
    return names


def dispatch_names(source: str) -> Tuple[Set[str], Optional[str]]:
    """``(names, note)``: the keys of simulate.py's ``_MOCKED`` dispatch table, read from its source.

    The shapes read: a dict literal with string keys; a comprehension keyed ``fn.__name__`` (or the loop variable)
    over a literal tuple/list of functions (or of strings), inline or bound at module level. Anything else falls back
    to the ``@plurai.mock`` tool names, with a note saying so."""
    tree = ast.parse(source)
    assigned: Dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigned[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigned[node.target.id] = node.value
    table = assigned.get('_MOCKED')
    if isinstance(table, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in table.keys):
        return {k.value for k in table.keys}, None  # type: ignore[union-attr]
    if isinstance(table, ast.DictComp) and len(table.generators) == 1 and isinstance(table.generators[0].target, ast.Name):
        var = table.generators[0].target.id
        elements = _sequence_names(table.generators[0].iter, assigned)
        key = table.key
        if elements is not None:
            if isinstance(key, ast.Attribute) and key.attr == '__name__' and isinstance(key.value, ast.Name) and key.value.id == var:
                if all(isinstance(e, ast.Name) for e in elements):
                    return {e.id for e in elements}, None  # type: ignore[attr-defined]
            if isinstance(key, ast.Name) and key.id == var and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements):
                return {e.value for e in elements}, None  # type: ignore[attr-defined]
    fallback = _mock_decorated(tree)
    return fallback, (
        'simulate.py defines no `_MOCKED` dispatch table this check can read from its source, so the '
        f'@plurai.mock tool names stand in for it ({len(fallback)} found)'
    )


def _dispatched_functions(tree: ast.Module, assigned: Mapping[str, ast.AST]) -> Set[str]:
    """The names of the functions ``_MOCKED`` dispatches to: a comprehension's elements, a dict literal's values (bare
    or through ``mock_wrap``), else every ``@plurai.mock`` handler."""
    table = assigned.get('_MOCKED')
    if isinstance(table, ast.DictComp) and len(table.generators) == 1:
        elements = _sequence_names(table.generators[0].iter, assigned)
        if elements is not None and all(isinstance(e, ast.Name) for e in elements):
            return {e.id for e in elements}  # type: ignore[attr-defined]
    if isinstance(table, ast.Dict):
        values = [v.args[0] if isinstance(v, ast.Call) and v.args else v for v in table.values]
        if all(isinstance(v, ast.Name) for v in values):
            return {v.id for v in values}  # type: ignore[attr-defined]
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(d, ast.Call) and ast.unparse(d.func).rsplit('.', 1)[-1] == 'mock' for d in node.decorator_list)}


def required_mock_params(source: str) -> List[Finding]:
    """``BRIDGE_MOCK_PARAM_REQUIRED`` (warn) for each dispatched mock with a parameter that has no default (a
    ``**kwargs`` cannot supply it): the bridge sends only the keys the call carried, so a call that leaves one out is a
    HarnessFault on every turn that does."""
    tree = ast.parse(source)
    assigned: Dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigned[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assigned[node.target.id] = node.value
    names = _dispatched_functions(tree, assigned)
    findings: List[Finding] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in names:
            continue
        positional = node.args.posonlyargs + node.args.args
        required = [a.arg for a in positional[:len(positional) - len(node.args.defaults)]]
        required += [a.arg for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is None]
        if required:
            findings.append(Finding(
                severity='warn', code=Code.BRIDGE_MOCK_PARAM_REQUIRED, where=f'simulate.py:{node.lineno}',
                message=f'the mock `{node.name}` (simulate.py:{node.lineno}) requires {", ".join(f"`{r}`" for r in required)}: the '
                'bridge sends only the keys the call carried, so a call that leaves one out is a HarnessFault, not a tool '
                'failure; give each a default (`=None`)'))
    return findings


def absent_bridge_copies(workdir: Path) -> List[Finding]:
    """Each verbatim bridge copy (``bridge/host.py``, ``bridge/runtime/*``) that is not in the workdir at all."""
    return [
        Finding(severity='fail', code=Code.BRIDGE_COPY_STALE, where=str(MAP_PATH.parent),
                message=f'{workdir / name} is absent, so the bench runs without this plugin\'s copy; copy it (B4): '
                f'cp {shlex.quote(str(source))} {shlex.quote(str(workdir / name))}')
        for name, source in BRIDGE_COPIES.items()
        if not (workdir / name).is_file()
    ]


def stale_bridge_copies(workdir: Path) -> List[Finding]:
    """The plugin's stale-copy check, kept to the bridge's rows (``bridge/host.py``, ``bridge/runtime/**``), and every
    one of those copies that is absent."""
    script = PLUGIN_ROOT / 'scripts' / 'check_workdir_copies.py'
    done = subprocess.run([sys.executable, str(script), str(workdir)], capture_output=True, text=True, check=False)
    if done.returncode not in (0, 1):
        raise NoVerdict(f'check_workdir_copies.py could not run (exit {done.returncode}): {done.stderr.strip()[-300:]}')
    lines = done.stdout.splitlines()
    bridge = str(workdir / MAP_PATH.parent) + '/'
    stale = [i for i, line in enumerate(lines) if 'differs from this plugin' in line and line.startswith(bridge)]
    return absent_bridge_copies(workdir) + [
        Finding(severity='fail', code=Code.BRIDGE_COPY_STALE, where=str(MAP_PATH.parent),
                message=lines[i] + (f' {lines[i + 1].strip()}' if i + 1 < len(lines) else ''))
        for i in stale
    ]


# --------------------------------------------------------------------------------------------
# static
# --------------------------------------------------------------------------------------------


def static_findings(workdir: Path, doc: Any, config: Mapping[str, Any], names: Set[str]) -> List[Finding]:
    """``bridge_map``'s checks in their fixed order, stopping after the first that fails."""
    bridge_dir = workdir / MAP_PATH.parent
    out: List[Finding] = []
    steps = (
        lambda: bridge_map.validate_map(doc),
        lambda: bridge_map.check_files(doc, bridge_dir=bridge_dir),
        lambda: bridge_map.check_evidence(doc, agent_root=(workdir / str(doc['agent_root'])).resolve()),
        lambda: bridge_map.check_tools(doc, config=config, dispatch_names=names),
        lambda: bridge_map.check_hosts(doc, config=config),
    )
    for step in steps:
        found = [_from_map(f) for f in step()]
        out += found
        if any(f.severity == 'fail' for f in found):
            break
    return out


# --------------------------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------------------------


def _smoke_world(workdir: Path) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """``(world, inputs, source)`` of ``agent.smoke_inputs[0]`` in ``bench_manifest.json``, else an empty world."""
    try:
        entries = json.loads((workdir / 'bench_manifest.json').read_text(encoding='utf-8'))['agent']['smoke_inputs']
        first = entries[0]
        return dict(first.get('world') or {}), dict(first.get('inputs') or {}), 'agent.smoke_inputs[0]'
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {}, {}, 'an empty world (bench_manifest.json declares no agent.smoke_inputs)'


def _load_simulate(simulate: Path) -> Any:
    if str(simulate.parent) not in sys.path:
        sys.path.insert(0, str(simulate.parent))
    spec = importlib.util.spec_from_file_location('_plurai_simulate_under_bridge_probe', simulate)
    if spec is None or spec.loader is None:
        raise NoVerdict(f'{simulate} cannot be loaded as a module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _why(error: Any) -> Optional[str]:
    """A probe result's ``error`` (a string, or ``{kind, message}``) as one line, or None."""
    if isinstance(error, Mapping):
        return f'{error.get("kind")}: {error.get("message")}' if error.get('kind') else str(error.get('message') or '') or None
    return str(error) if error else None


def _is_mock_error(error: Any) -> bool:
    return isinstance(error, Mapping) and error.get('kind') == 'mock_error'


def _route_of(doc: Mapping[str, Any], pointer: str) -> Optional[Mapping[str, Any]]:
    """The ``http`` route an ``/http/<i>`` pointer names, or None."""
    head, _, index = pointer.rpartition('/')
    routes = doc.get('http') or []
    if head == '/http' and index.isdigit() and int(index) < len(routes) and isinstance(routes[int(index)], Mapping):
        return routes[int(index)]
    return None


def _unswapped_remedy(doc: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    """What to fix when an entry did not land on its swap, naming the route's host for an ``http`` entry."""
    route = _route_of(doc, str(result.get('entry'))) if result.get('kind') == 'http' else None
    if route is None:
        return 'check its specifier or module path against the importing file its evidence names'
    host = route.get('host')
    request = f'{route.get("method")} {route.get("path")}'
    body_keys = sorted(k for k in (route.get('match') or {}) if str(k).startswith('body.'))
    shown_host = f'${host.get("env")}' if isinstance(host, Mapping) else host
    if route.get('method') == 'GET' and body_keys:
        return (f'a GET to {shown_host} carries no request body, so `match` {", ".join(body_keys)} can never hold: match on '
                '`query.<k>` or `header.<k>` instead')
    if isinstance(host, Mapping):
        name = host.get('env')
        return (f'its host is the URL in ${name}: set {name} where the bench runs (unset, the route is inert), then check '
                f'{request} and its `match` against the request the agent sends there')
    if '*' in str(host):
        return (f'its host is the pattern {host!r} (the probe sent to it with each `*` as a concrete label): check that it '
                'matches the host the agent calls (a `*` is one or more characters inside one label, a leading `*.` one or more '
                f'labels), then {request} and its `match`')
    return f'check {request} and its `match` against the request the agent sends to {host}'


def judge_probe(doc: Mapping[str, Any], done: Mapping[str, Any], events: List[Mapping[str, Any]],
                entry_exports: Optional[Tuple[str, ...]]) -> List[Finding]:
    """The probe's reply and the mock's event log -> findings. Pure, so the rule is testable without a child.

    A result's ``error`` passes only as a mock-side failure (``{kind: mock_error}``): the route reached its mock, and the
    mock refused. Any other error fails the entry, even one whose call was dispatched."""
    findings: List[Finding] = []
    results = {str(r.get('entry')): r for r in done.get('results') or () if isinstance(r, Mapping)}
    called: Dict[str, List[Mapping[str, Any]]] = {}
    for event in events:
        if isinstance(event, Mapping) and event.get('type') == 'tool_call':
            called.setdefault(str(event.get('tool')), []).append(event)
    for result in results.values():
        entry, why = str(result.get('entry')), _why(result.get('error'))
        if result.get('swapped') is False:
            findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_NOT_SWAPPED, where=entry,
                                    message=f'{entry} ({result.get("kind")}) did not land on its swap{f": {why}" if why else ""} — '
                                    f'{_unswapped_remedy(doc, result)}'))
        elif result.get('kind') == 'replace' and why:
            findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_NOT_SWAPPED, where=entry,
                                    message=f'{entry} (replace) resolved to its replacement, which did not load: {why} — fix the '
                                    'replacement module so it imports cleanly'))
    for tool, pointers in sorted(bridge_map.mapped_tools(doc).items()):
        for pointer in pointers:
            result = results.get(pointer)
            if result is None:
                findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_UNREACHED, where=pointer, message=f'{pointer} → {tool}: the route probe returned no result for it'))
                continue
            error = result.get('error')
            if result.get('swapped') is False:
                continue
            if not result.get('dispatched'):
                why = _why(error)
                findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_UNREACHED, where=pointer,
                                        message=f'{pointer} → {tool}: no ext_call was dispatched{f" ({why})" if why else ""}'))
            elif tool not in called:
                findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_UNREACHED, where=pointer,
                                        message=f'{pointer} → {tool}: dispatched, but the mock recorded no call of {tool!r} — its dispatch row does not reach the mock (mock_wrap)'))
            elif error and not _is_mock_error(error):
                findings.append(Finding(severity='fail', code=Code.BRIDGE_ROUTE_UNREACHED, where=pointer,
                                        message=f'{pointer} → {tool}: dispatched, but the call failed outside the mock ({_why(error)}) — '
                                        'a harness error means the arguments or the value do not fit the mock; any other error is the '
                                        'swap itself failing'))
    guard = done.get('egress_guard') if isinstance(done.get('egress_guard'), Mapping) else {}
    for channel in ('fetch', 'socket'):
        if guard.get(channel) != 'refused':
            findings.append(Finding(severity='fail', code=Code.BRIDGE_EGRESS_OPEN, where='',
                                    message=f'the egress guard did not refuse a probe {channel} (it reported {guard.get(channel)!r}): the agent could reach a real host'))
    if entry_exports is not None:
        declared = doc['entry']['resume'] == 'seed'
        if declared != ('seed' in entry_exports):
            findings.append(Finding(severity='fail', code=Code.BRIDGE_RESUME_MISMATCH, where='/entry/resume',
                                    message=f'entry.resume says {"seed" if declared else "unsupported"}, and the entry {"does not export" if declared else "exports"} '
                                    '`seed`; make the two agree'))
    return findings


def _env_host(environ: Mapping[str, str], name: str) -> Optional[str]:
    """The hostname of the URL (or bare host) a variable holds, as the child reads a host row's ``env``; None when unset."""
    value = (environ.get(name) or '').strip()
    return bridge_map._hostname(value) or None if value else None


def env_host_findings(doc: Mapping[str, Any], config: Mapping[str, Any], environ: Mapping[str, str]) -> List[Finding]:
    """``BRIDGE_PASSTHROUGH_IS_PRODUCTION`` for a ``passthrough_hosts`` / ``sink_hosts`` ``env`` row whose variable, read
    as the child reads it (the map's own ``env`` value over ``environ``), names a production host. The static half
    cannot resolve these rows; a production ``env`` row resolves from ``environ``."""
    child_env = dict(environ)
    for name, row in (doc.get('env') or {}).items():
        if isinstance(row, Mapping) and isinstance(row.get('value'), str):
            child_env[name] = row['value']
    egress = config.get('egress') if isinstance(config, Mapping) else None
    production: List[str] = []
    for row in (egress.get('production_hosts') if isinstance(egress, Mapping) else None) or ():
        if isinstance(row, Mapping) and isinstance(row.get('host'), str) and row['host'].strip():
            production.append(row['host'])
        elif isinstance(row, Mapping) and isinstance(row.get('env'), str):
            resolved = _env_host(environ, row['env'])
            if resolved:
                production.append(resolved)
    findings: List[Finding] = []
    for block, verb in (('passthrough_hosts', 'lets the agent reach'), ('sink_hosts', 'answers 202 for')):
        for i, row in enumerate(doc.get(block) or ()):
            name = row.get('env') if isinstance(row, Mapping) else None
            host = _env_host(child_env, name) if isinstance(name, str) else None
            hit = next((p for p in production if bridge_map._hosts_overlap(host, p)), None) if host else None
            if hit is not None:
                where = bridge_map._pointer(block, i, 'env')
                findings.append(Finding(
                    severity='fail', code=bridge_map.Code.BRIDGE_PASSTHROUGH_IS_PRODUCTION, where=where,
                    message=f'{where}: ${name} is {host!r} here, so the row {verb} {hit!r}, a production host of '
                    'egress.production_hosts; route that host to its mock tool with an `http` entry instead'))
    return findings


def _probe_failed(message: str) -> List[Finding]:
    return [Finding(severity='fail', code=Code.BRIDGE_PROBE_FAILED, where='', message=message)]


def live_findings(simulate: Path, doc: Mapping[str, Any], config: Optional[Mapping[str, Any]] = None) -> Tuple[List[Finding], List[str]]:
    """Run the route probe through the bench's own harness. Raises :class:`NoVerdict` when a prerequisite is absent; any
    failure of the bench itself (``simulate.py`` does not import, the session does not start, the probe raises) is a
    ``BRIDGE_PROBE_FAILED`` finding carrying its own words."""
    workdir = simulate.parent
    if shutil.which('node') is None:
        raise NoVerdict('node is not on PATH; run bridge_preflight.py')
    if not (workdir / RUNTIME_DIR / 'node_modules').is_dir():
        from bridge_preflight import ensure_command  # noqa: PLC0415 - one spelling of the runtime install

        raise NoVerdict(f"the bridge runtime is not installed ({workdir / RUNTIME_DIR}/node_modules is absent); run the plugin's own runtime install: {ensure_command(workdir)}")
    try:
        import plurai  # noqa: PLC0415 - the live half only
    except ImportError as exc:
        raise NoVerdict(f'the plurai SDK is not importable from {sys.executable} ({exc})') from exc
    world, inputs, source = _smoke_world(workdir)
    notes = [f'world: {source}']
    try:
        module = _load_simulate(simulate)
    except NoVerdict:
        raise
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - the bench's own import failure, reported in its words
        return _probe_failed(f'simulate.py did not import: {type(exc).__name__}: {exc}'), notes
    make_host = getattr(module, '_bridge_host', None)
    if not callable(make_host):
        return _probe_failed('simulate.py has no `_bridge_host()`: it is not the bridge template; regenerate it from simulate_bridge.py'), notes
    try:
        plurai.configure(mode='local')
        plurai.start_session(SESSION_ID, world, inputs)
    except Exception as exc:  # noqa: BLE001 - the session over the smoke world did not start
        return _probe_failed(f'the local mock session did not start over {source}: {type(exc).__name__}: {exc}'), notes
    host = None
    try:
        host = make_host()
        done = asyncio.run(host.route_probe())
        exports = host.entry_exports
        events = plurai.get_events(SESSION_ID).get('events') or []
    except Exception as exc:  # noqa: BLE001 - any raise is the probe's failure, reported with its own words
        return _probe_failed(f'the route probe did not complete: {type(exc).__name__}: {exc}'), notes
    finally:
        if host is not None:
            try:
                host.close()
            except Exception as exc:  # noqa: BLE001 - a close failure is reported, never a traceback
                notes.append(f'the bridge host did not close cleanly: {type(exc).__name__}: {exc}')
    errored = sorted({str(e.get('tool')) for e in events if isinstance(e, Mapping) and e.get('status') == 'error'})
    if errored:
        notes.append(f'reached with a mock-side error (routing is fine): {", ".join(errored)}')
    findings = judge_probe(doc, done, list(events), tuple(exports) if exports is not None else None)
    return findings + env_host_findings(doc, config or {}, os.environ), notes


# --------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------


def _summary(verdict: str, static: str, live: str, findings: List[Finding], reason: Optional[str] = None) -> str:
    body: Dict[str, Any] = {'verdict': verdict, 'static': static, 'live': live, 'findings': [{**asdict(f), 'code': str(f.code)} for f in findings]}
    if reason:
        body['reason'] = reason
    return json.dumps(body, sort_keys=True)


def run(simulate: Path, *, static_only: bool) -> int:
    workdir = simulate.parent
    try:
        if not simulate.is_file():
            raise NoVerdict(f'{simulate} is not a file')
        config = read_config(workdir)
        source = simulate.read_text(encoding='utf-8')
        names, note = dispatch_names(source)
        doc, findings = bridge_map.load_map(workdir / MAP_PATH)
        found = [_from_map(f) for f in findings]
        if doc is not None:
            found += static_findings(workdir, doc, config, names)
        found += stale_bridge_copies(workdir)
        found += required_mock_params(source)
    except (NoVerdict, SyntaxError, OSError) as exc:
        print(f'verify_bridge: NO VERDICT — {exc}')
        print(_summary('no_verdict', 'no_verdict', 'skipped', [], str(exc)))
        return 2
    if note:
        print(f'note: {note}')
    for finding in found:
        print(finding.render())
    static_failed = any(f.severity == 'fail' for f in found)
    if static_only or static_failed:
        live = 'skipped'
        if static_failed and not static_only:
            print('live route probe: skipped — the static checks failed')
    else:
        try:
            probed, notes = live_findings(simulate, doc, config)
        except NoVerdict as exc:
            print(f'verify_bridge: static checks pass; the live route probe reached NO VERDICT — {exc}')
            print(_summary('no_verdict', 'pass', 'no_verdict', found, str(exc)))
            return 2
        for line in notes:
            print(f'note: {line}')
        for finding in probed:
            print(finding.render())
        live = 'fail' if any(f.severity == 'fail' for f in probed) else 'pass'
        found += probed
    failed = any(f.severity == 'fail' for f in found)
    if not failed:
        mapped = len(bridge_map.mapped_tools(doc)) if doc is not None else 0
        print(f'✓ verify_bridge: {mapped} mapped tool(s){"" if static_only else ", every one reached through the bridge, egress guard armed"}')
    print(_summary('fail' if failed else 'pass', 'fail' if static_failed else 'pass', live, found))
    if failed:
        # The cause, last on stderr: a run-gate keeps a failing check's last stderr line, where an SDK warning printed
        # during the probe would otherwise stand in for it. The first line only: a probe fault carries a log tail. In
        # reverse, so the first failing finding is the last line.
        for finding in reversed(found):
            if finding.severity == 'fail':
                print(finding.render().splitlines()[0][:400], file=sys.stderr, flush=True)
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('bench', type=Path, help='the plurai_sim workdir, or its simulate.py')
    parser.add_argument('--static', action='store_true', help='only the static checks: no Node, no SDK')
    args = parser.parse_args(argv)
    bench = args.bench.resolve()
    return run(bench / 'simulate.py' if bench.is_dir() else bench, static_only=args.static)


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
