#!/usr/bin/env python3
"""Can this machine run this Node agent under the bridge? Every refusal is named, with its remedy.

    python3 bridge_preflight.py <plurai_sim>              # B1, before the runtime is copied
    python3 bridge_preflight.py <plurai_sim> --install    # B4 and Step 0: run the runtime's npm ci when it is absent or stale, then re-check

``--install`` is the plugin's own runtime install, run without asking wherever the plugin is about to run the bridge.
On a workdir with no ``bridge/bridge.map.json`` (not a bridge bench) it checks nothing, prints ``runtime: null`` and
exits 0, so one unconditional call serves every lane.

The agent root is the map's ``agent_root`` once ``bridge/bridge.map.json`` exists, else the workdir's parent
(``--agent-root`` overrides). The runtime is checked once ``bridge/runtime/`` holds the copied ``package.json``
(``--runtime`` requires it before that). A Next app's import graph is walked from ``--entry``, else from
``bridge/entry.mjs`` once written.

Stdlib only, runs under a bare ``python3`` from the plugin. Never installs or builds the agent's own code; ``--install``
runs the bridge runtime's private ``npm ci`` (``constants.json`` ``install_argv``) into ``<workdir>/bridge/runtime``.

Exit 0: supported (warnings allowed). Exit 1: refused (every refusal is printed; the first is the one to fix first).
Exit 2: bad usage. The last stdout line is JSON: ``{verdict: ok|refused, refusal: {code, reason, remedy} | null,
refusals: [...], warnings: [{code, reason, remedy}], node: {version, hooks}, module_system, ts_runner, entry_source,
runtime, installed_now, scan: {files, file_cap, capped, byte_cap, oversize[]}}``; ``installed_now`` is true only when this
call ran the runtime's install and the runtime now matches its lock. A warning is a check that could not reach an answer
(the run may still fail on it), or an early heads-up on something the runtime refuses only if a turn reaches it
(``worker_threads``), or a fix for B2/B3 to apply (``next_runtime_api``); ``scan`` says how much of the agent's source the source checks read.
"""

from __future__ import annotations

import enum
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_map  # noqa: E402 - for the shared constants.json reader

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)


class Code(StrEnum):
    """Every refusal this script names."""

    WINDOWS_HOST = 'windows_host'  # no POSIX pass_fds, so the bridge channel cannot be inherited
    NODE_MISSING = 'node_missing'  # no `node` on PATH (or it reports no version)
    NODE_TOO_OLD = 'node_too_old'  # node without `module.registerHooks`: below the floor, or 23.0 to 23.4
    NPM_MISSING = 'npm_missing'  # no `npm` on PATH: the runtime cannot be installed
    BUN_RUNTIME = 'bun_runtime'  # the agent's code uses Bun globals or `bun:` modules
    DENO_RUNTIME = 'deno_runtime'  # a Deno project: `deno.json` plus `Deno.` / `jsr:` / `npm:` specifiers
    WORKER_THREADS = 'worker_threads'  # warn: a source file starts a worker thread; the runtime refuses one a turn starts
    YARN_PNP = 'yarn_pnp'  # Yarn Plug'n'Play: no node_modules for the bridge's loader to resolve through
    AGENT_DEPS_MISSING = 'agent_deps_missing'  # the agent's own dependencies are not installed (warn: an unimported local-path link)
    NESTJS_NEEDS_BUILD = 'nestjs_needs_build'  # decorator metadata needs the agent's compiled output, and it is absent or stale
    NESTJS_BUILD_UNKNOWN = 'nestjs_build_unknown'  # warn: a tsconfig `extends` this script cannot read may set emitDecoratorMetadata
    NEXT_RUNTIME_API = 'next_runtime_api'  # warn: the entry's module graph imports Next's request-time APIs
    BRIDGE_RUNTIME_MISSING = 'bridge_runtime_missing'  # plurai_sim/bridge/runtime is not copied or not installed
    BRIDGE_RUNTIME_STALE = 'bridge_runtime_stale'  # the installed runtime differs from its package-lock.json
    BRIDGE_RUNTIME_INSTALL_FAILED = 'bridge_runtime_install_failed'  # the runtime's npm ci failed


@dataclass(frozen=True)
class Refusal:
    code: Code
    reason: str
    remedy: str


CONSTANTS = bridge_map.load_constants()
FLOOR: Tuple[int, int, int] = tuple(CONSTANTS['node_floor'])  # type: ignore[assignment]
_FLOOR_TEXT = '.'.join(str(n) for n in FLOOR)
#: Node lines at or above the floor that still lack `module.registerHooks`: [from, to) (it landed in 22.15 and 23.5).
_NO_HOOKS: Tuple[Tuple[Tuple[int, int, int], Tuple[int, int, int]], ...] = (((23, 0, 0), (23, 5, 0)),)
_SOURCE_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
_TS_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts')
_SKIP_DIRS = frozenset({'node_modules', '.git', 'dist', 'build', 'out', '.next', '.turbo', '.cache', 'coverage', 'plurai_sim', '.plurai'})
_MAX_FILES = 5000
_MAX_BYTES = 1_000_000
_BUN = re.compile(r'\bBun\.[A-Za-z_]|[\'"]bun:[a-z]')
_DENO = re.compile(r'\bDeno\.[A-Za-z_]|[\'"](?:jsr|npm):[@A-Za-z0-9]')
#: A file that both imports `worker_threads` and constructs a `Worker` starts a worker thread. Either one alone does not:
#: `isMainThread` / `parentPort` read without a construction, or a browser Web Worker (`new Worker(new URL(...))`).
_WORKER_IMPORT = re.compile(r'[\'"](?:node:)?worker_threads[\'"]')
_WORKER_NEW = re.compile(r'\bnew\s+Worker\s*\(|\.Worker\s*\(')
#: Test code never runs in a simulation turn, so the runtime-kind scans skip it.
_TEST_DIRS = frozenset({'__tests__', 'test', 'tests'})
_TEST_FILE = re.compile(r'\.(?:test|spec)\.[^.]+$')
_NEXT_RUNTIME = ('next/headers', 'next/server', 'server-only')
_NEXT_SCOPE = ('importing it is fine; if a turn calls `after()`, `connection()`, `cookies()` or `headers()`, bridge/entry.mjs (B3) must run '
               'the turn inside Next\'s request scope: set `globalThis.AsyncLocalStorage` before the agent loads, run the turn under Next\'s '
               'work and work-unit async storages (the request store carrying any cookies/headers a turn reads), and run the collected '
               '`after()` tasks once the reply is complete; only if a turn needs a live Next server, treat the agent as a hosted HTTP agent')
_NEXT_REMEDY = {
    'server-only': 'it throws on import outside Next: map it to `server-only/empty.js` in a tsconfig whose `paths` also keeps the agent\'s '
    'own entries (an extending tsconfig\'s `paths` replaces the base\'s), the repo\'s own (e.g. a scripts or eval tsconfig) if it already '
    'does this, else a new one under plurai_sim/bridge/, never the agent\'s tsconfig; point the map\'s (B2) `env.TSX_TSCONFIG_PATH` at it',
    'next/server': _NEXT_SCOPE,
    'next/headers': _NEXT_SCOPE,
}
_LOCAL_SPECS = ('link:', 'file:', 'portal:')
_SPECIFIERS = re.compile(
    r'''(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)(['"])([^'"\n]+)\1''', re.MULTILINE)


def _windows() -> bool:
    return os.name == 'nt'


# --------------------------------------------------------------------------------------------
# reading the agent
# --------------------------------------------------------------------------------------------


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_jsonc(path: Path) -> Optional[Any]:
    """tsconfig's JSON-with-comments: line and block comments and trailing commas, outside strings."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    out, i, in_string = [], 0, False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == '\\' and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            out.append(ch)
        elif text.startswith('//', i):
            i = text.find('\n', i)
            i = len(text) if i < 0 else i
            continue
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    try:
        return json.loads(re.sub(r',(\s*[}\]])', r'\1', ''.join(out)))
    except ValueError:
        return None


def _extends_target(base_dir: Path, spec: str) -> Optional[Path]:
    """Where a tsconfig ``extends`` entry points: a relative path, or a package config under an ancestor's node_modules."""
    if spec.startswith('.') or spec.startswith('/'):
        candidates = [base_dir / spec]
    else:
        candidates = [here / 'node_modules' / spec for here in _ancestors(base_dir)]
    for path in candidates:
        for found in (path, Path(f'{path}.json'), path / 'tsconfig.json'):
            if found.is_file():
                return found
    return None


def _tsconfig_options(path: Path, seen: set, unread: List[str]) -> Dict[str, Any]:
    """One tsconfig's ``compilerOptions`` over its ``extends`` (a string or a list, later entries winning), base first.
    Each ``extends`` entry that cannot be found or parsed is appended to ``unread``."""
    if path in seen:
        return {}
    seen.add(path)
    doc = _read_jsonc(path)
    if not isinstance(doc, dict):
        unread.append(path.name)
        return {}
    merged: Dict[str, Any] = {}
    bases = doc.get('extends')
    for spec in [bases] if isinstance(bases, str) else bases if isinstance(bases, list) else []:
        target = _extends_target(path.parent, spec) if isinstance(spec, str) else None
        if target is None:
            unread.append(str(spec))
        else:
            merged.update(_tsconfig_options(target, seen, unread))
    options = doc.get('compilerOptions')
    merged.update(options if isinstance(options, dict) else {})
    return merged


def _tsconfig(agent_root: Path) -> Tuple[Dict[str, Any], List[str]]:
    """``(compilerOptions, unread)``: tsconfig.json's options merged over its ``extends`` chain, and the configs in that
    chain this script could not read (a package that is not installed, a file that does not parse)."""
    path = agent_root / 'tsconfig.json'
    unread: List[str] = []
    return (_tsconfig_options(path, set(), unread) if path.is_file() else {}), unread


def _compiler_options(agent_root: Path) -> Dict[str, Any]:
    return _tsconfig(agent_root)[0]


def _sources(agent_root: Path, skip: Sequence[Path] = (), limit: int = _MAX_FILES) -> Iterator[Path]:
    """The agent's own source files, at most ``limit``: outside dependencies, build output and the bench."""
    count = 0
    skipped = {p.resolve() for p in skip}
    for here, dirs, files in os.walk(agent_root):
        base = Path(here)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith('.') and (base / d).resolve() not in skipped)
        for name in sorted(files):
            if name.endswith(_SOURCE_SUFFIXES) and not name.endswith('.d.ts'):
                count += 1
                if count > limit:
                    return
                yield base / name


def _scan(agent_root: Path, skip: Sequence[Path], facts: Dict[str, Any]) -> List[Path]:
    """The source files the checks read, with the caps they hit recorded in ``facts['scan']`` and noted as skipped."""
    files = list(_sources(agent_root, skip, limit=_MAX_FILES + 1))
    capped = len(files) > _MAX_FILES
    files = files[:_MAX_FILES]
    oversize = []
    for path in files:
        try:
            if path.stat().st_size > _MAX_BYTES:
                oversize.append(path.relative_to(agent_root).as_posix())
        except OSError:
            continue
    facts['scan'] = {'files': len(files), 'file_cap': _MAX_FILES, 'capped': capped, 'byte_cap': _MAX_BYTES, 'oversize': oversize}
    if capped:
        facts['skipped'].append(f'source checks: the agent has more than {_MAX_FILES} source files; only the first {_MAX_FILES} '
                                '(sorted walk) were read, so a Bun, Deno or worker_threads use past them is not seen')
    if oversize:
        shown = ', '.join(oversize[:5]) + (f' and {len(oversize) - 5} more' if len(oversize) > 5 else '')
        facts['skipped'].append(f'source checks: {len(oversize)} file(s) over {_MAX_BYTES} bytes were not read: {shown}')
    return files


def _text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ''
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def _strip_comments(text: str) -> str:
    """JS/TS source with every ``//`` and ``/* */`` comment blanked to spaces, outside string and template literals.
    Newlines stay, so a line number still names the source line."""
    out: List[str] = []
    i, n, quote = 0, len(text), ''
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == quote or (ch == '\n' and quote != '`'):
                quote = ''
        elif ch in '\'"`':
            quote = ch
            out.append(ch)
        elif text.startswith('//', i):
            end = text.find('\n', i)
            end = n if end < 0 else end
            out.append(' ' * (end - i))
            i = end
            continue
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            end = n if end < 0 else end + 2
            out.append(''.join(c if c == '\n' else ' ' for c in text[i:end]))
            i = end
            continue
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _is_test(path: Path, agent_root: Path) -> bool:
    """A test file (``*.test.*``, ``*.spec.*``) or a file under a ``__tests__`` / ``test`` / ``tests`` directory."""
    rel = path.relative_to(agent_root)
    return bool(_TEST_FILE.search(path.name)) or any(part in _TEST_DIRS for part in rel.parts[:-1])


def _first_site(agent_root: Path, files: Sequence[Path], pattern: re.Pattern) -> Optional[str]:
    """The first ``<file>:<line>`` whose code (comments blanked) matches, in the agent's non-test sources."""
    for path in files:
        if _is_test(path, agent_root):
            continue
        for number, line in enumerate(_strip_comments(_text(path)).splitlines(), 1):
            if pattern.search(line):
                return f'{path.relative_to(agent_root).as_posix()}:{number}'
    return None


def _ancestors(start: Path) -> Iterator[Path]:
    """`start` and its parents, up to and including the first holding `.git` (the repository root)."""
    here = start.resolve()
    while True:
        yield here
        if (here / '.git').exists() or here.parent == here:
            return
        here = here.parent


# --------------------------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------------------------


def _version(argv: List[str]) -> Optional[str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def check_node(facts: Dict[str, Any]) -> List[Refusal]:
    install = f'install Node {FLOOR[0]} LTS ({_FLOOR_TEXT} or newer) and put it on PATH'
    if shutil.which('node') is None:
        return [Refusal(code=Code.NODE_MISSING, reason='no `node` on PATH', remedy=install)]
    reported = _version(['node', '--version'])
    match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', reported or '')
    if match is None:
        return [Refusal(code=Code.NODE_MISSING, reason=f'`node --version` reported {reported!r}, not a version', remedy=install)]
    version = tuple(int(n) for n in match.groups())
    gap = next(((low, high) for low, high in _NO_HOOKS if low <= version < high), None)
    facts['node'] = {'version': '.'.join(match.groups()), 'hooks': 'registerHooks' if version >= FLOOR and gap is None else None}
    if version < FLOOR:
        return [Refusal(code=Code.NODE_TOO_OLD, reason=f'node {facts["node"]["version"]} is below {_FLOOR_TEXT} (module.registerHooks)', remedy=install)]
    if gap is not None:
        upto = '.'.join(str(n) for n in gap[1])
        return [Refusal(code=Code.NODE_TOO_OLD, reason=f'node {facts["node"]["version"]} has no module.registerHooks (Node 23 gained it in {upto})',
                        remedy=f'{install}; on the 23 line, {upto} or newer')]
    return []


def check_npm() -> List[Refusal]:
    if shutil.which('npm') is None:
        return [Refusal(code=Code.NPM_MISSING, reason='no `npm` on PATH (some distro `nodejs` packages ship without it)',
                        remedy='install npm alongside Node (the Node.js installer, or your distro\'s `npm` package)')]
    return []


def check_runtime_kind(agent_root: Path, files: Sequence[Path]) -> List[Refusal]:
    out = []
    site = _first_site(agent_root, files, _BUN)
    if site is not None:
        out.append(Refusal(code=Code.BUN_RUNTIME, reason=f'the agent uses Bun\'s runtime APIs ({site}); no bridge child ships for Bun yet',
                           remedy='none: run the agent on Node, or keep it out of the bridge (a `bun.lock` alone is fine)'))
    if any((agent_root / name).is_file() for name in ('deno.json', 'deno.jsonc')):
        site = _first_site(agent_root, files, _DENO)
        if site is not None:
            out.append(Refusal(code=Code.DENO_RUNTIME, reason=f'a Deno project ({site}); no bridge child ships for Deno yet',
                               remedy='none: no Deno bridge child ships yet'))
    return out


def check_worker_threads(agent_root: Path, files: Sequence[Path], facts: Dict[str, Any]) -> None:
    """Warn (never refuse) on each non-test file that imports ``worker_threads`` and constructs a ``Worker``. The scan cannot
    tell whether a turn reaches the construction; the runtime refuses a worker a turn actually starts."""
    sites = []
    for path in files:
        if _is_test(path, agent_root):
            continue
        lines = _strip_comments(_text(path)).splitlines()
        if not any(_WORKER_IMPORT.search(line) for line in lines):
            continue
        number = next((n for n, line in enumerate(lines, 1) if _WORKER_NEW.search(line)), None)
        if number is not None:
            sites.append(f'{path.relative_to(agent_root).as_posix()}:{number}')
    if sites:
        shown = ', '.join(sites[:5]) + (f' and {len(sites) - 5} more' if len(sites) > 5 else '')
        facts['warnings'].append(Refusal(
            code=Code.WORKER_THREADS,
            reason=f'the agent\'s code starts a worker thread ({shown}); the bridge swaps the main thread\'s modules only, so the '
            'runtime refuses a worker the agent starts during a turn (the turn faults as unsupported_shape worker_threads)',
            remedy='if a turn the entry drives reaches this construction, keep the worker off that path or treat the agent as a '
            'hosted HTTP agent; a worker no turn starts needs nothing'))


def check_yarn_pnp(agent_root: Path) -> List[Refusal]:
    for here in _ancestors(agent_root):
        if (here / '.pnp.cjs').is_file() or (here / '.pnp.js').is_file():
            where = here / ('.pnp.cjs' if (here / '.pnp.cjs').is_file() else '.pnp.js')
        else:
            rc = here / '.yarnrc.yml'
            linker = re.search(r'^\s*nodeLinker:\s*["\']?(\S+?)["\']?\s*$', _text(rc), re.MULTILINE) if rc.is_file() else None
            if linker is None or linker.group(1) != 'pnp':
                continue
            where = rc
        return [Refusal(code=Code.YARN_PNP, reason=f'the agent installs with Yarn Plug\'n\'Play ({where}); the bridge resolves through node_modules',
                        remedy='set `nodeLinker: node-modules` in .yarnrc.yml and run `yarn install`, then re-run')]
    return []


def _installed(agent_root: Path, name: str) -> bool:
    return any((here / 'node_modules' / name / 'package.json').is_file() for here in _ancestors(agent_root))


def _importers(agent_root: Path, files: Sequence[Path], names: Sequence[str]) -> Dict[str, str]:
    """``name`` -> the first non-test source file importing ``name`` or ``name/...``."""
    found: Dict[str, str] = {}
    for path in files:
        if _is_test(path, agent_root):
            continue
        for match in _SPECIFIERS.finditer(_text(path)):
            spec = match.group(2)
            for name in names:
                if name not in found and (spec == name or spec.startswith(f'{name}/')):
                    found[name] = path.relative_to(agent_root).as_posix()
    return found


def _link_target(spec: Any) -> Optional[str]:
    """The path a local-path spec (``link:``/``file:``/``portal:``) names, relative to the agent root; None for any other spec."""
    return str(spec).split(':', 1)[1] if str(spec).startswith(_LOCAL_SPECS) else None


def check_agent_deps(agent_root: Path, package: Optional[Dict[str, Any]], files: Sequence[Path], facts: Dict[str, Any]) -> List[Refusal]:
    """A missing dependency refuses, except a local-path one whose target is absent and that no scanned source imports: a warning,
    since a reinstall recreates the same dangling link and nothing loads it. An incomplete scan words the warning, never refuses:
    a missed importer still fails at B6 with Node's `Cannot find package`."""
    remedy = (f'install the agent\'s own dependencies the way its README says (e.g. `npm ci` in {agent_root}); '
              'the bridge never installs them')
    if package is None:
        return [Refusal(code=Code.AGENT_DEPS_MISSING, reason=f'{agent_root} has no readable package.json, so its dependencies cannot be found',
                        remedy='point --agent-root at the directory holding the agent\'s package.json')]
    deps = package.get('dependencies') if isinstance(package.get('dependencies'), dict) else {}
    missing = sorted(name for name in deps if not _installed(agent_root, name))
    dangling = [name for name in missing if _link_target(deps[name]) is not None and not (agent_root / _link_target(deps[name])).exists()]
    importers = _importers(agent_root, files, dangling) if dangling else {}
    unimported = [name for name in dangling if name not in importers]
    if unimported:
        scan = facts.get('scan') or {}
        oversize = scan.get('oversize') or []
        unread = ([f'{len(oversize)} file(s) not scanned: {", ".join(oversize[:3])}{" and more" if len(oversize) > 3 else ""}'] if oversize else []) + (
            [f'files past the {_MAX_FILES}-file cap not scanned'] if scan.get('capped') else [])
        nobody = f'no scanned source imports them ({"; ".join(unread)})' if unread else 'nothing imports them'
        facts['warnings'].append(Refusal(
            code=Code.AGENT_DEPS_MISSING,
            reason=f'{", ".join(f"{name} ({deps[name]})" for name in unimported)}: local-path link(s) whose target is absent; {nobody}',
            remedy=('none needed unless an unscanned file imports them (B6 then fails with `Cannot find package`)' if unread else
                    'none needed: no source loads them') + ', and a reinstall recreates the same dangling link'))
    out = []
    registry = [name for name in missing if name not in dangling]
    if registry:
        shown = ', '.join(registry[:8]) + (f' and {len(registry) - 8} more' if len(registry) > 8 else '')
        out.append(Refusal(code=Code.AGENT_DEPS_MISSING, reason=f'{len(registry)} of the agent\'s {len(deps)} dependencies are not installed: {shown}',
                           remedy=remedy))
    for name, site in importers.items():
        out.append(Refusal(code=Code.AGENT_DEPS_MISSING, reason=f'{name} ({deps[name]}) is a local-path link whose target is absent, and {site} imports it',
                           remedy=f'make the link target `{_link_target(deps[name])}` exist (or point the spec in package.json at a path '
                           'that does), then reinstall; a reinstall alone recreates the same dangling link'))
    return out


def _newest(paths: Iterator[Path]) -> float:
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def check_nestjs(agent_root: Path, files: Sequence[Path], facts: Dict[str, Any]) -> List[Refusal]:
    options, unread = _tsconfig(agent_root)
    if options.get('emitDecoratorMetadata') is not True:
        if unread and 'emitDecoratorMetadata' not in options:
            facts['warnings'].append(Refusal(
                code=Code.NESTJS_BUILD_UNKNOWN,
                reason=f'tsconfig.json extends {", ".join(unread)}, which this check cannot read, so whether emitDecoratorMetadata is '
                'on (tsx/esbuild cannot emit it; the bridge would need the compiled output) is unknown',
                remedy='install the agent\'s dependencies so the base config resolves, then re-run; or set emitDecoratorMetadata '
                'explicitly in tsconfig.json'))
        return []
    out_dir = agent_root / str(options.get('outDir') or 'dist')
    built = _newest(p for p in out_dir.rglob('*.js')) if out_dir.is_dir() else 0.0
    sources = _newest(p for p in files if p.suffix in _TS_SUFFIXES)
    if built and built >= sources:
        facts['entry_source'] = 'dist'
        facts['ts_runner'] = 'none'
        return []
    state = 'absent' if not built else 'older than the sources'
    return [Refusal(code=Code.NESTJS_NEEDS_BUILD,
                    reason=f'tsconfig sets emitDecoratorMetadata, which tsx/esbuild cannot emit, and the compiled {out_dir.name}/ is {state}',
                    remedy=f'run the agent\'s own build so {out_dir.relative_to(agent_root).as_posix()}/ is newer than its sources, then re-run; '
                    'the bridge imports the compiled output and never builds it')]


def _resolve(agent_root: Path, importer: Path, spec: str, aliases: Dict[str, Path]) -> Optional[Path]:
    if spec.startswith('.'):
        base = (importer.parent / spec)
    else:
        prefix = next((p for p in aliases if spec.startswith(p)), None)
        if prefix is None:
            return None
        base = aliases[prefix] / spec[len(prefix):]
    stem = base.with_suffix('') if base.suffix in _SOURCE_SUFFIXES else base
    for candidate in (base, *(stem.with_suffix(s) for s in _SOURCE_SUFFIXES), *(base / f'index{s}' for s in _SOURCE_SUFFIXES)):
        if candidate.is_file():
            return candidate
    return None


def _aliases(agent_root: Path) -> Dict[str, Path]:
    """tsconfig `paths` of the `<prefix>/*` -> [`<dir>/*`] form (the `@/` alias a Next app carries)."""
    options = _compiler_options(agent_root)
    base = agent_root / str(options.get('baseUrl') or '.')
    out = {}
    for pattern, targets in (options.get('paths') or {}).items():
        if pattern.endswith('*') and isinstance(targets, list) and targets and str(targets[0]).endswith('*'):
            out[pattern[:-1]] = base / str(targets[0])[:-1]
    return out


def _rel(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def check_next(agent_root: Path, package: Optional[Dict[str, Any]], start: Optional[Path], facts: Dict[str, Any]) -> None:
    """Walk the import graph from ``start`` (the construction module, or the bridge entry that imports it) and warn (never refuse)
    once per request-time specifier reached, with its first chain: B3 and the map get past each; whether a turn calls one is a
    runtime fact the walk cannot decide."""
    deps = {**(package or {}).get('dependencies', {}), **(package or {}).get('devDependencies', {})} if package else {}
    if 'next' not in deps:
        return
    if start is None or not start.is_file():
        facts['skipped'].append('next_runtime_api: a Next app, and neither --entry nor bridge/entry.mjs names the module whose imports to walk')
        return
    entry = _rel(start, agent_root)
    aliases = _aliases(agent_root)
    parents: Dict[Path, Optional[Path]] = {start: None}
    queue = [start]
    reached: Set[str] = set()
    while queue and len(parents) < _MAX_FILES and len(reached) < len(_NEXT_RUNTIME):
        here = queue.pop(0)
        for match in _SPECIFIERS.finditer(_text(here)):
            spec = match.group(2)
            if spec in _NEXT_RUNTIME:
                if spec not in reached:
                    reached.add(spec)
                    chain, node = [], here
                    while node is not None:
                        chain.append(_rel(node, agent_root))
                        node = parents[node]
                    facts['warnings'].append(Refusal(code=Code.NEXT_RUNTIME_API, reason=f'{entry} reaches `{spec}` (via {" <- ".join(chain)})',
                                                     remedy=_NEXT_REMEDY[spec]))
                continue
            target = _resolve(agent_root, here, spec, aliases)
            if target is not None and target not in parents and 'node_modules' not in target.parts:
                parents[target] = here
                queue.append(target)
    if queue and len(reached) < len(_NEXT_RUNTIME):
        facts['skipped'].append(f'next_runtime_api: the import walk from {entry} stopped at {_MAX_FILES} modules; '
                                'a request-time import past them is not seen')


# --------------------------------------------------------------------------------------------
# the bridge runtime
# --------------------------------------------------------------------------------------------


def _locked(lock: Any) -> Dict[str, Tuple[Optional[str], bool]]:
    """`node_modules/<name>` -> (version, optional) of a package-lock.json / node_modules/.package-lock.json."""
    packages = lock.get('packages') if isinstance(lock, dict) else None
    return {
        key: (spec.get('version'), bool(spec.get('optional')))
        for key, spec in (packages or {}).items()
        if key.startswith('node_modules/') and isinstance(spec, dict)
    }


def ensure_command(workdir: Path) -> str:
    """The plugin's own runtime install for this workdir: idempotent, private to ``bridge/runtime/``, run without asking."""
    return shlex.join(['python3', str(Path(__file__).resolve()), str(workdir), '--install'])


def check_runtime(runtime: Path) -> List[Refusal]:
    install = f"run the plugin's own runtime install: `{ensure_command(runtime.parent.parent)}`"
    if not (runtime / 'package.json').is_file():
        return [Refusal(code=Code.BRIDGE_RUNTIME_MISSING, reason=f'{runtime} holds no copied runtime (package.json is absent)',
                        remedy=f'copy the runtime (B4), then {install}')]
    if not (runtime / 'node_modules').is_dir():
        return [Refusal(code=Code.BRIDGE_RUNTIME_MISSING, reason=f'{runtime}/node_modules is absent', remedy=install)]
    lock, hidden = _read_json(runtime / 'package-lock.json'), _read_json(runtime / 'node_modules' / '.package-lock.json')
    if lock is None or hidden is None:
        absent = 'package-lock.json' if lock is None else 'node_modules/.package-lock.json'
        return [Refusal(code=Code.BRIDGE_RUNTIME_STALE, reason=f'{runtime}/{absent} is absent or unreadable, so the install cannot be shown to match the lock',
                        remedy=f're-copy the runtime (B4), then {install}')]
    want, have = _locked(lock), _locked(hidden)
    drift = sorted(
        [f'{key[13:]} {have[key][0]} (lock: {want[key][0] if key in want else "absent"})' for key in have if key not in want or want[key][0] != have[key][0]]
        + [f'{key[13:]} not installed' for key, (_, optional) in want.items() if key not in have and not optional]
    )
    if drift:
        return [Refusal(code=Code.BRIDGE_RUNTIME_STALE, reason=f'the installed runtime differs from its package-lock.json: {"; ".join(drift[:6])}',
                        remedy=install)]
    return []


def install_runtime(runtime: Path) -> List[Refusal]:
    argv = list(CONSTANTS['install_argv'])
    argv[argv.index('--prefix') + 1] = str(runtime)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        tail, code = f'{type(exc).__name__}: {exc}', None
    else:
        if done.returncode == 0:
            return []
        tail, code = '\n'.join((done.stderr or done.stdout).strip().splitlines()[-20:]), done.returncode
    return [Refusal(code=Code.BRIDGE_RUNTIME_INSTALL_FAILED, reason=f'`{" ".join(argv)}` failed{f" (exit {code})" if code is not None else ""}:\n{tail}',
                    remedy='set the registry or proxy in ~/.npmrc (the agent repo\'s .npmrc does not apply under --prefix), or run '
                    'the same command with --offline from a warm npm cache')]


# --------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------


def _bridge_map(workdir: Path) -> Optional[Dict[str, Any]]:
    doc = _read_json(workdir / 'bridge' / 'bridge.map.json')
    return doc if isinstance(doc, dict) else None


def _bridge_entry(workdir: Path) -> Optional[Path]:
    """``bridge/<entry.path>`` of the map, once the map and the entry are written."""
    doc = _bridge_map(workdir)
    entry = doc.get('entry') if doc else None
    path = workdir / 'bridge' / str(entry.get('path')) if isinstance(entry, dict) and entry.get('path') else None
    return path if path is not None and path.is_file() else None


def agent_root_of(workdir: Path) -> Path:
    """The map's ``agent_root`` (relative to the workdir) once the map exists, else the workdir's parent."""
    doc = _bridge_map(workdir)
    root = doc.get('agent_root') if doc else None
    return (workdir / root).resolve() if isinstance(root, str) and root else workdir.resolve().parent


def preflight(agent_root: Path, *, entry: Optional[str] = None, workdir: Optional[Path] = None,
              runtime: bool = False, install: bool = False) -> Tuple[List[Refusal], Dict[str, Any]]:
    facts: Dict[str, Any] = {'node': None, 'module_system': None, 'ts_runner': None, 'entry_source': 'source', 'runtime': None,
                             'installed_now': False, 'skipped': [], 'warnings': [], 'scan': None}
    if _windows():
        return [Refusal(code=Code.WINDOWS_HOST, reason='a Windows host has no POSIX pass_fds, so the bridge channel cannot be inherited',
                        remedy='run the simulation under WSL or on macOS/Linux')], facts
    refusals = check_node(facts) + check_npm()
    package = _read_json(agent_root / 'package.json')
    package = package if isinstance(package, dict) else None
    skip = [workdir] if workdir is not None else []
    files = _scan(agent_root, skip, facts)
    facts['ts_runner'] = 'tsx' if any(p.suffix in _TS_SUFFIXES for p in files) else 'none'
    declared = 'esm' if (package or {}).get('type') == 'module' else 'commonjs'
    opposite = ('.cjs', '.cts') if declared == 'esm' else ('.mjs', '.mts')
    facts['module_system'] = 'mixed' if any(p.suffix in opposite for p in files) else declared
    refusals += check_runtime_kind(agent_root, files)
    check_worker_threads(agent_root, files, facts)
    pnp = check_yarn_pnp(agent_root)
    refusals += pnp or check_agent_deps(agent_root, package, files, facts)
    refusals += check_nestjs(agent_root, files, facts)
    bridge_entry = _bridge_entry(workdir) if workdir is not None else None
    check_next(agent_root, package, agent_root / entry if entry else bridge_entry, facts)
    where = workdir / 'bridge' / 'runtime' if workdir is not None else None
    if where is not None and not (runtime or install) and not (where / 'package.json').is_file():
        facts['skipped'].append('the bridge runtime: not copied yet (B4 copies and installs it)')
    elif where is not None:
        found = check_runtime(where)
        if install and found and (where / 'package.json').is_file():
            found = install_runtime(where) or check_runtime(where)
            facts['installed_now'] = not found
        facts['runtime'] = 'installed' if not found else str(found[0].code)
        refusals += found
    return refusals, facts


def main(argv: Optional[List[str]] = None) -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('workdir', nargs='?', type=Path, help='the plurai_sim directory: the agent root and the runtime are read from it')
    parser.add_argument('--agent-root', type=Path, help="the directory holding the agent's package.json (default: the map's agent_root, else the workdir's parent)")
    parser.add_argument('--entry', help='the agent-relative construction module whose imports are walked for Next request APIs '
                                        '(default: bridge/entry.mjs once written)')
    parser.add_argument('--runtime', action='store_true', help='require the bridge runtime to be installed even before it is copied')
    parser.add_argument('--install', action='store_true', help="run the runtime's npm ci when it is not installed or stale, then re-check")
    args = parser.parse_args(argv)
    workdir = args.workdir.resolve() if args.workdir else None
    if workdir is None and args.agent_root is None:
        print('bridge_preflight: pass the plurai_sim workdir (or --agent-root)', file=sys.stderr)
        return 2
    if (args.runtime or args.install) and workdir is None:
        print('bridge_preflight: --runtime / --install need the plurai_sim workdir', file=sys.stderr)
        return 2
    if args.install and not (workdir / 'bridge' / 'bridge.map.json').is_file():  # type: ignore[operator]
        # The lane is declared by the map (harness_fingerprint.detect_lang): with none there is no bridge runtime to install.
        print(f'note: not checked — {workdir} declares no bridge (no bridge/bridge.map.json); nothing to install')
        print(json.dumps({'verdict': 'ok', 'refusal': None, 'refusals': [], 'runtime': None, 'installed_now': False}, sort_keys=True))
        return 0
    agent_root = args.agent_root.resolve() if args.agent_root else agent_root_of(workdir)  # type: ignore[arg-type]
    if not agent_root.is_dir():
        print(f'bridge_preflight: the agent root {agent_root} is not a directory', file=sys.stderr)
        return 2
    refusals, facts = preflight(agent_root, entry=args.entry, workdir=workdir, runtime=args.runtime, install=args.install)
    for refusal in refusals:
        print(f'✗ [{refusal.code}] {refusal.reason} — {refusal.remedy}')
    for warning in facts['warnings']:
        print(f'! [{warning.code}] {warning.reason} — {warning.remedy}')
    for note in facts['skipped']:
        print(f'note: not checked — {note}')
    if not refusals:
        node = facts['node'] or {}
        print(f'✓ bridge_preflight: node {node.get("version")}, {facts["module_system"]}, {facts["ts_runner"]}, entry from {facts["entry_source"]}')
    body = {
        'verdict': 'refused' if refusals else 'ok',
        'refusal': {**asdict(refusals[0]), 'code': str(refusals[0].code)} if refusals else None,
        'refusals': [{**asdict(r), 'code': str(r.code)} for r in refusals],
        'agent_root': str(agent_root),
        **facts,
        'warnings': [{**asdict(w), 'code': str(w.code)} for w in facts['warnings']],
    }
    print(json.dumps(body, sort_keys=True))
    return 1 if refusals else 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
