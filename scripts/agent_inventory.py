#!/usr/bin/env python3
"""The agent's inventory, read off its OWN source: every input it accepts (C2) and the tools it ships (C3).

WHY. Every guard on a scan compared the scan's declarations with themselves: an input channel the
scan never declared and a tool category the harness switched off were invisible, because nothing
read the agent a second time. This reads it, deterministically, so `bench_manifest.py validate`,
the surface check and `lint_prd.py` compare the bench against the agent rather than against the scan.

WHAT IS READ. The harness (`plurai_sim/`) names the agent: the modules `simulate.py` (and a bridge
entry) import are the SEEDS. From them:

- the TOOL SCOPE — every file the seeds import, transitively, plus every module a dynamic loader
  imports from a directory (`importlib.import_module` over `os.listdir`);
- the INPUT SCOPE — the seeds and the files one import away, plus their production CALLERS (the
  files importing a seed, two hops up: the route or task handler the harness drives around).

Inputs (`inputs[]`, each `{name, kind, source, evidence}`):
  request models a route parses (pydantic / dataclass / TypedDict params, zod `.parse` in a route
  handler) and their file/image parts; payload keys an entry reads off its request parameter; the
  declared entry point's own content parameters (`send(self, message: str)`); multimodal content
  builders; upload routes (repo-wide); upload-reading tools (re-read over a live enumeration's
  built-ins, a bare `Read` included); and, in chat-surface
  frontend files, file pickers, host-injected messages and mutation calls between turns — including
  the calls they make through the in-repo modules they import (services, lib clients).

Tools (`production_tools`, C3): every tool defined in the tool scope (`@tool`-family decorators,
`StructuredTool`/`Tool(name=…)`, `BaseTool` subclasses; AI-SDK `key: tool({…})`, factory
registrations, `createTool`, `DynamicStructuredTool`, MCP `server.tool(…)`, an agent SDK options
list `ClaudeAgentOptions(allowed_tools=[…])`; the tools a framework adds from how the code builds the agent:
CrewAI delegation, a todo middleware, Google ADK `sub_agents`, Mastra Memory's options) or declared in a YAML
file beside it (`tools: [{name: …, …}]`), each capability switch found (an env-gated tool block, a
config key a directory loader reads, a settings toggle, a container class whose enable flag defaults
OFF, a declared `enabled: false`) with its production default, and `names` = the tools on at those
defaults. A default the reader cannot evaluate counts as ON: the guard may then ask for an operator
answer, never silently narrow. `production_coverage` is the ONE "N of M production tools" reading.

A MODE SELECTOR is a capability switch too (`kind: selector`): a payload key an entry compares against the task
types its host registers (provider registrations' ``task_type=``, a task-type enum). Its production default is every
value the host offers (`values`), and `selects` maps each value to the inputs only its branch reaches.

The repository is the git work tree holding the harness; without one, the outermost project directory
around the agent — and an empty input list read that way is a loud warning, never a silent zero.

The kinds are C2's: `text`, `image`, `file_content_part`, `file_by_path` (an `agent.io.input` part:
`text` / `image_url` / `file`) and `file_by_reference`, `structured_context`, `between_turn_action`,
`host_injected_message` (an `agent.io.channels` kind, C1). A kind is the reader's suggestion; the
scan may reclassify an entry when it decides `modelled` or `waived`.

Error replies (`error_replies[]`, each `{text, match: exact|prefix, cite}`, at most 20; `error_replies_dropped` counts
the rest): the fixed text the agent's error paths hand the USER — returned, yielded or sent from an `except` body or
through an error-named variable, or the message of an exception whose text a handler hands on (a template keeps its
fixed head as a `prefix`). Python only, over the input scope inside the agent root; `record_error_replies.py` writes them
to `agent.error_replies`, which the backend's raw-error-reply check matches exactly.

Control messages (`control_messages[]`, each `{text, sent_by, matched_at}`): a fixed message a frontend control posts as
the user's turn (`role: "user"` with a literal or template `content`) that the agent compares whole (`==`, membership of
a literal tuple, `===`), read over every agent file under the agent root, not the import scope. `scenario_variables.yaml`
declares them as `control_messages`; extract-scenario-variables' verify names one it omits.

Stdlib only (`bench_manifest.py` imports it under a bare interpreter); Python 3.9 floor.

    agent_inventory.py extract [--workdir <plurai_sim>] [--out <path>]   # writes artifacts/agent_inventory.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from _stdio import utf8_child_text

SCHEMA_ID = 'plurai-agent-inventory/1'
INVENTORY_FILENAME = 'agent_inventory.json'

#: C2's kinds. The first four ride the user's message (`agent.io.input`), the rest are C1 channels.
PART_KINDS = {'text': 'text', 'image': 'image_url', 'file_content_part': 'file', 'file_by_path': 'file'}
CHANNEL_KINDS = ('file_by_reference', 'structured_context', 'between_turn_action', 'host_injected_message')
INPUT_KINDS = (*PART_KINDS, *CHANNEL_KINDS)

PY_SUFFIXES = ('.py',)
TS_SUFFIXES = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')
FRONTEND_SUFFIXES = ('.tsx', '.jsx', '.vue', '.svelte')
#: Directories that hold no production code of the agent: dependencies, builds, caches, tests, the harness.
SKIP_DIRS = frozenset({
    'node_modules', '.venv', 'venv', 'env', '__pycache__', 'dist', 'build', 'out', 'coverage', 'site-packages',
    'tests', 'test', '__tests__', '__mocks__', 'fixtures', 'e2e', 'spec', 'specs', 'plurai_sim', 'migrations', 'storybook-static',
})
_TEST_FILE = re.compile(r'(^test_.*\.py$|_test\.py$|^conftest\.py$|\.(test|spec|stories)\.[jt]sx?$|\.d\.ts$)')
#: A file a harness directory carries: such a directory is the bench, never the agent.
_HARNESS_MARKERS = ('plurai.config.yaml', 'bench_manifest.json')
#: A directory that is a project root when no git work tree names one.
_PROJECT_MARKERS = ('pyproject.toml', 'setup.py', 'setup.cfg', 'requirements.txt', 'package.json', 'go.mod', 'Cargo.toml')
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 40000


# --------------------------------------------------------------------------------------------
# the file set
# --------------------------------------------------------------------------------------------


#: How the root was found: the git work tree (the only answer that is the whole repository), or project markers.
ROOT_BY_GIT = 'git'
ROOT_BY_MARKERS = 'project_markers'
#: `warnings[].code` when the root is not a git work tree and the reader found no input: the frontend and the routes
#: around the agent may sit outside the directory read, so an empty input list there is not evidence of none.
WARN_NO_GIT_ROOT = 'no_git_root_zero_inputs'
#: `warnings[].code` when no declared entry resolves and the scope falls back to the harness's imports.
WARN_ENTRY_UNRESOLVED = 'entry_unresolved'
#: `warnings[].code` when no tool-assembly site was found: `loader` is null, never a synthesized `<file>:1`.
WARN_LOADER_NOT_FOUND = 'loader_not_found'


def find_root(workdir: Path) -> Tuple[Optional[Path], Optional[str]]:
    """``(root, how)``: the git work tree holding the workdir (``git rev-parse --show-toplevel``, else the nearest
    ancestor carrying ``.git`` when git cannot run), else the outermost of the project directories nested around the
    agent module (a service inside an app inside a repo is read as the repo). ``(None, None)`` when neither names one,
    so nothing is read from a directory that is not the agent's."""
    try:
        done = subprocess.run(['git', '-C', str(workdir), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, **utf8_child_text(), timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is not None and done.returncode == 0 and done.stdout.strip():
        return Path(done.stdout.strip()).resolve(), ROOT_BY_GIT
    module = workdir.resolve().parent
    tree = next((d for d in (workdir.resolve(), *workdir.resolve().parents) if (d / '.git').exists()), None)
    if tree is not None:
        return tree, ROOT_BY_GIT
    if not any((module / marker).is_file() for marker in _PROJECT_MARKERS):
        return None, None
    root = module
    while root.parent != root and any((root.parent / marker).is_file() for marker in _PROJECT_MARKERS):
        root = root.parent
    return root, ROOT_BY_MARKERS


#: A directory that is the root of the agent's own repository: a project marker, or a repository's top-level files.
_REPO_MARKERS = (*_PROJECT_MARKERS, 'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'README.md', 'README.rst', 'README')
#: How many commits that touch the harness are read for the files they also changed (the bench overlay).
OVERLAY_COMMITS = 50


def agent_root(root: Path, seeds: List[Path]) -> Path:
    """The root of the agent's upstream tree: from the first entry file that has one, the outermost ancestor (inside
    ``root``) carrying a project or repository marker. Anything outside it — a wrapper beside the agent's checkout, a
    bench config, a bench component — is the bench, never the agent."""
    for seed in seeds:
        marked = [d for d in seed.parents if (d == root or root in d.parents) and any((d / m).exists() for m in _REPO_MARKERS)]
        if marked:
            return marked[-1]
    return root


def bench_overlay(root: Path, found_by: Optional[str], top: Path, workdir: Path) -> List[str]:
    """The files inside the agent root that git says are not the customer's: untracked or modified in the work tree,
    or changed by a commit that also changed the harness workdir. ``[]`` without a git work tree (nothing to tell)."""
    if found_by != ROOT_BY_GIT:
        return []
    harness = os.path.relpath(workdir.resolve(), root)

    def git(*args: str) -> List[str]:
        try:
            done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, **utf8_child_text(), timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        return done.stdout.splitlines() if done.returncode == 0 else []

    scope = os.path.relpath(top, root)
    found = {line[3:].strip().strip('"').split(' -> ')[-1] for line in git('status', '--porcelain', '--untracked-files=all', '--', scope) if len(line) > 3}
    for commit in git('log', f'-{OVERLAY_COMMITS}', '--format=%H', '--', harness):
        found |= set(git('show', '--name-only', '--format=', commit, '--', scope))
    # A `.gitignore` is never agent source, and the scan writes its own managed block into one.
    return sorted(f for f in found if f and not (f == harness or f.startswith(harness.rstrip('/') + '/')) and os.path.basename(f) != '.gitignore')


def source_files(root: Path) -> List[Path]:
    """Every production source file under ``root``: dependencies, builds, tests and harness dirs pruned."""
    found: List[Path] = []
    for here, dirs, files in os.walk(root):
        if here != str(root) and any(marker in files for marker in _HARNESS_MARKERS):
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        for name in sorted(files):
            if name.endswith(PY_SUFFIXES + TS_SUFFIXES + FRONTEND_SUFFIXES) and not _TEST_FILE.search(name):
                path = Path(here) / name
                try:
                    if path.stat().st_size <= MAX_FILE_BYTES:
                        found.append(path)
                except OSError:
                    continue
        if len(found) >= MAX_FILES:
            break
    return found


class Repo:
    """The source files of one repository, read once, with the import resolution both languages need."""

    def __init__(self, root: Path, files: List[Path]) -> None:
        self.root = root
        self.files = files
        self._text: Dict[Path, Optional[str]] = {}
        self._tree: Dict[Path, Optional[ast.Module]] = {}
        self._imports: Dict[Path, Set[Path]] = {}
        self.by_suffix: Dict[str, List[Path]] = {}
        for path in files:
            if path.suffix == '.py':
                parts = list(path.relative_to(root).with_suffix('').parts)
                if parts[-1] == '__init__':
                    parts = parts[:-1]
                for i in range(len(parts)):
                    self.by_suffix.setdefault('.'.join(parts[i:]), []).append(path)
        self.fileset = set(files)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def text(self, path: Path) -> str:
        if path not in self._text:
            try:
                self._text[path] = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                self._text[path] = None
        return self._text[path] or ''

    def tree(self, path: Path) -> Optional[ast.Module]:
        if path not in self._tree:
            try:
                self._tree[path] = ast.parse(self.text(path))
            except (SyntaxError, ValueError):
                self._tree[path] = None
        return self._tree[path]

    # ---- Python -----------------------------------------------------------------------------

    @property
    def import_roots(self) -> List[Path]:
        """The directories an absolute import resolves from, as ``sys.path`` would hold them: the repository root and
        the directory above each top-level package (the first ancestor without an ``__init__.py``: a ``src/`` layout,
        a monorepo package)."""
        if not hasattr(self, '_import_roots'):
            roots: Set[Path] = {self.root}
            for package in {f.parent for f in self.files if f.name == '__init__.py'}:
                top = package
                while (top.parent / '__init__.py') in self.fileset and top.parent != self.root:
                    top = top.parent
                roots.add(top.parent)
            self._import_roots = sorted(roots, key=lambda p: (len(p.parts), str(p)))
        return self._import_roots

    def resolve_py(self, dotted: str, near: Path) -> Optional[Path]:
        """``a.b.c`` -> its file under an import root (the candidate nearest ``near``); else, for a dotted name, an
        exact dotted suffix of a repo path (a namespace package). A bare ``time`` resolves only at an import root, so
        a stdlib module never lands on a same-named file deep in the tree."""
        parts = dotted.split('.')
        rooted = [p for root in self.import_roots for p in (root.joinpath(*parts[:-1], parts[-1] + '.py'), root.joinpath(*parts, '__init__.py'))
                  if p in self.fileset]
        candidates = rooted or (self.by_suffix.get(dotted) or [] if len(parts) > 1 else [])
        if not candidates:
            return None
        return min(candidates, key=lambda p: (len(os.path.relpath(p, near.parent).split(os.sep)), str(p)))

    def py_imports(self, path: Path) -> Set[Path]:
        tree = self.tree(path)
        found: Set[Path] = set()
        if tree is None:
            return found
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    package = path.parent
                    for _ in range(node.level - 1):
                        package = package.parent
                    prefix = '.'.join(package.relative_to(self.root).parts) if package != self.root and self.root in package.parents else ''
                    base = '.'.join(p for p in (prefix, base) if p)
                names = [f'{base}.{alias.name}' if base else alias.name for alias in node.names] + ([base] if base else [])
            for name in names:
                target = self.resolve_py(name, path)
                if target is not None and target != path:
                    found.add(target)
        return found | self.dynamic_dir_modules(path)

    def dynamic_dir_modules(self, path: Path) -> Set[Path]:
        """The modules a loader imports by name from a directory listing (``importlib.import_module`` over ``os.listdir``)."""
        if path.suffix != '.py':
            return set()
        text = self.text(path)
        if not re.search(r'\b(import_module|__import__|spec_from_file_location)\b', text) or not re.search(r'\b(listdir|glob|iterdir|scandir)\b', text):
            return set()
        return {f for d in self._loader_dirs(path) for f in self.files if f.parent == d and f.suffix == '.py'}

    def _loader_dirs(self, path: Path) -> List[Path]:
        tree = self.tree(path)
        dirs: List[Path] = []
        for node in ast.walk(tree) if tree is not None else ():
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and 0 < len(node.value) < 120:
                candidate = (path.parent / node.value.strip('/').strip()).resolve()
                if node.value.strip('/') and candidate.is_dir() and self.root in candidate.parents and candidate not in dirs:
                    dirs.append(candidate)
        return dirs

    # ---- TypeScript / JavaScript --------------------------------------------------------------

    _TS_IMPORT = re.compile(r'''(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]''', re.MULTILINE)

    def resolve_ts(self, spec: str, near: Path) -> Optional[Path]:
        if spec.startswith('.'):
            base = (near.parent / spec).resolve()
        else:
            alias = self._alias_target(spec, near)
            if alias is None:
                return None
            base = alias
        stem = base.with_suffix('') if base.suffix in ('.js', '.mjs', '.cjs', '.jsx') else base
        tries = [base, *(stem.with_name(stem.name + s) for s in TS_SUFFIXES), *(base / f'index{s}' for s in TS_SUFFIXES)]
        return next((t for t in tries if t in self.fileset), None)

    def _alias_target(self, spec: str, near: Path) -> Optional[Path]:
        for parent in near.parents:
            config = parent / 'tsconfig.json'
            if config.is_file():
                paths = _tsconfig_paths(config)
                for pattern, targets in paths.items():
                    head = pattern.rstrip('*')
                    if pattern.endswith('*') and spec.startswith(head) and targets:
                        return (config.parent / targets[0].rstrip('*') / spec[len(head):]).resolve()
                    if spec == pattern and targets:
                        return (config.parent / targets[0]).resolve()
                if spec.startswith(('@/', '~/')):
                    return (parent / spec[2:]).resolve()
                return None
            if parent == self.root:
                break
        return (self.root / spec[2:]).resolve() if spec.startswith(('@/', '~/')) else None

    _IMPORT_LINE = re.compile(r'^\s*(?:from\s+\S+\s+)?import\b[^\n]*|^\s*from\s+\S+', re.MULTILINE)

    def naming(self, stem: str) -> List[Path]:
        """The files whose import lines name ``stem`` — an index built once, so finding callers never re-reads the repo."""
        if not hasattr(self, '_naming'):
            index: Dict[str, List[Path]] = {}
            for path in self.files:
                text = self.text(path)
                lines = self._IMPORT_LINE.findall(text) if path.suffix == '.py' else [m.group(1) for m in self._TS_IMPORT.finditer(text)]
                for word in {w for line in lines for w in re.findall(r'[\w-]+', line)}:
                    index.setdefault(word, []).append(path)
            self._naming = index
        return self._naming.get(stem, [])

    def imports(self, path: Path) -> Set[Path]:
        if path not in self._imports:
            if path.suffix == '.py':
                self._imports[path] = self.py_imports(path)
            else:
                found = {self.resolve_ts(m.group(1), path) for m in self._TS_IMPORT.finditer(self.text(path))}
                self._imports[path] = {f for f in found if f is not None and f != path}
        return self._imports[path]


def _tsconfig_paths(config: Path) -> Dict[str, List[str]]:
    try:
        raw = config.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return {}
    # Comments outside strings only: an `include` glob like "**/*.ts" holds a `/*` that is no comment.
    raw = re.sub(r'("(?:[^"\\]|\\.)*")|/\*.*?\*/|//[^\n]*', lambda m: m.group(1) or '', raw, flags=re.DOTALL)
    raw = re.sub(r',(\s*[}\]])', r'\1', raw)
    try:
        options = json.loads(raw).get('compilerOptions') or {}
    except (ValueError, AttributeError):
        return {}
    base = options.get('baseUrl') or '.'
    paths = options.get('paths') or {}
    return {k: [str(Path(base) / t) for t in v] for k, v in paths.items() if isinstance(v, list)}


def _harness_files(workdir: Path) -> List[Path]:
    files = [p for p in sorted(workdir.glob('*.py')) if not _TEST_FILE.search(p.name)]
    bridge = workdir / 'bridge'
    if bridge.is_dir():
        files += sorted(p for p in bridge.glob('*') if p.suffix in TS_SUFFIXES)
    return files


def declared_entry(workdir: Path) -> Optional[str]:
    """The agent's declared entry modules, comma-joined: the config's ``agent.module`` (dotted, or a path for a TS
    agent) and the module of the manifest's ``agent.entry_symbol.ref`` (``module:symbol``). None when neither names one."""
    found: List[str] = []
    try:
        config = BlockYaml((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')).root
    except (OSError, UnicodeDecodeError):
        config = None
    agent = config.get('agent') if isinstance(config, dict) else None
    if isinstance(agent, dict) and isinstance(agent.get('module'), str) and agent['module'].strip():
        found.append(agent['module'].strip())
    try:
        ref = (json.loads((workdir / 'bench_manifest.json').read_text(encoding='utf-8'))['agent'].get('entry_symbol') or {}).get('ref')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        ref = None
    if ref and (module := str(ref).partition(':')[0].strip()) and module not in found:
        found.append(module)
    return ','.join(found) or None


def entry_seeds(repo: Repo, workdir: Path, entry: Optional[str]) -> List[Path]:
    """The files the declared entry names (comma-separated): a dotted module under the import roots, or a path
    beside the agent."""
    hits: List[Path] = []
    for one in (e.strip() for e in (entry or '').split(',') if e.strip()):
        if '/' in one or one.endswith(TS_SUFFIXES + PY_SUFFIXES):
            base = workdir.resolve().parent
            hit = repo.resolve_ts('./' + one.lstrip('./'), base / '_') if not one.endswith(PY_SUFFIXES) else None
            hit = hit or next((p for p in ((base / one).resolve(), (repo.root / one).resolve()) if p in repo.fileset), None)
        else:
            hit = repo.resolve_py(one, workdir.resolve())
        if hit is not None and hit not in hits:
            hits.append(hit)
    return hits


def seeds(repo: Repo, workdir: Path) -> List[Path]:
    """The agent files the harness imports (and a bridge map's replaced modules): where the agent starts when no entry
    is declared."""
    found: Set[Path] = set()
    probe = Repo(repo.root, repo.files)
    for harness in _harness_files(workdir):
        probe.fileset.add(harness)
        try:
            probe._text[harness] = harness.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if harness.suffix == '.py':
            tree = probe.tree(harness)
            for node in ast.walk(tree) if tree is not None else ():
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module, *(f'{node.module}.{a.name}' for a in node.names)]
                else:
                    continue
                found.update(t for t in (repo.resolve_py(n, harness) for n in names) if t is not None)
        else:
            found.update(t for t in (repo.resolve_ts(m.group(1), harness) for m in Repo._TS_IMPORT.finditer(probe._text[harness] or '')) if t is not None)
    found.update(_bridge_modules(repo, workdir))
    return sorted(found)


def _bridge_modules(repo: Repo, workdir: Path) -> Set[Path]:
    try:
        bridge_map = json.loads((workdir / 'bridge' / 'bridge.map.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    if not isinstance(bridge_map, dict) or not isinstance(bridge_map.get('modules'), dict):
        return set()
    root_rel = str(bridge_map.get('agent_root') or '..')
    for base in ((workdir / root_rel).resolve(), (workdir / 'bridge' / root_rel).resolve()):
        hits = {(base / rel).resolve() for rel in bridge_map['modules']} & repo.fileset
        if hits:
            return hits
    return set()


def forward(repo: Repo, start: List[Path], hops: Optional[int] = None) -> Set[Path]:
    seen = set(start)
    frontier = list(start)
    depth = 0
    while frontier and (hops is None or depth < hops):
        nxt = [t for f in frontier for t in repo.imports(f) if t not in seen]
        seen.update(nxt)
        frontier = nxt
        depth += 1
    return seen


#: A module imported by more files than this is a hub (a package root, a shared client), not the agent's entry:
#: its importers are the whole product, not the caller the harness drives around.
HUB_IMPORTERS = 15


def _importers(repo: Repo, target: Path) -> Set[Path]:
    stem = target.stem if target.stem not in ('__init__', 'index') else target.parent.name
    return {f for f in repo.naming(stem) if f != target and target in repo.imports(f)}


def callers(repo: Repo, targets: List[Path], hops: int = 2) -> Set[Path]:
    """The files that import ``targets``, ``hops`` levels up: the production caller the harness drives around."""
    found: Set[Path] = set()
    frontier = set(targets)
    for _ in range(hops):
        hits: Set[Path] = set()
        for target in frontier:
            importers = _importers(repo, target) - found - set(targets)
            if len(importers) <= HUB_IMPORTERS:
                hits |= importers
        found |= hits
        frontier = hits
        if not frontier:
            break
    return found


# --------------------------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------------------------

_TEXT_NAMES = frozenset({'input', 'message', 'messages', 'text', 'query', 'prompt', 'question', 'content', 'user_input',
                         'user_message', 'msg', 'history', 'chat_history', 'parts'})
_IMAGE_WORD = re.compile(r'image|photo|picture|screenshot', re.IGNORECASE)
_FILE_WORD = re.compile(r'attach|upload|file|document|(?:^|_)docs?(?:_|$)', re.IGNORECASE)
_CONTENT_TYPES = re.compile(r'\b(UploadFile|bytes|BinaryIO|IO\[|File\b)')


def classify(name: str, annotation: str = '') -> str:
    """The C2 kind a field's name (and annotation) suggests."""
    if _IMAGE_WORD.search(name):
        return 'image'
    if _CONTENT_TYPES.search(annotation):
        return 'file_content_part'
    if _FILE_WORD.search(name):
        return 'file_by_reference'
    if name.lower() in _TEXT_NAMES:
        return 'text'
    return 'structured_context'


#: Request parameter names an entry reads its payload off.
_PAYLOAD_PARAMS = frozenset({'task', 'payload', 'request', 'req', 'body', 'inputs', 'event'})
#: Transport metadata on a request object, never the agent's input.
_TRANSPORT_ATTRS = frozenset({'headers', 'cookies', 'META', 'environ', 'scope', 'client'})
#: Annotations that leave a payload parameter a request (a parameter typed as any other class is a response, not one).
_UNTYPED = frozenset({'dict', 'Dict', 'Any', 'Mapping', 'MutableMapping', 'object', 'JSON', 'Json'})
_ROUTE_DECORATOR = re.compile(r'\.(post|put|patch|get|route|api_route|websocket)$')
_TOOL_DECORATORS = frozenset({'tool', 'function_tool', 'kernel_function', 'register_tool', 'tool_plain'})
_TOOL_CLASSES = frozenset({'StructuredTool', 'Tool', 'FunctionTool', 'BaseTool', 'DynamicStructuredTool'})
_PART_TYPES = {'image_url': 'image', 'image': 'image', 'input_image': 'image', 'file': 'file_content_part',
               'input_file': 'file_content_part', 'document': 'file_content_part'}
_PART_PAYLOAD_KEYS = frozenset({'image_url', 'source', 'file', 'file_data', 'file_id', 'url', 'data', 'base64', 'mime_type', 'media_type', 'path'})
#: A tool that READS what the user hands over: a read verb and the object it reads, as name tokens.
_READ_VERBS = frozenset({'view', 'read', 'get', 'load', 'open', 'describe', 'analyze', 'analyse', 'inspect', 'extract', 'parse', 'fetch'})
_READ_OBJECTS = {'image': 'image', 'images': 'image', 'photo': 'image', 'upload': 'file_by_reference', 'uploads': 'file_by_reference',
                 'uploaded': 'file_by_reference', 'attachment': 'file_by_reference', 'attachments': 'file_by_reference',
                 'file': 'file_by_path', 'files': 'file_by_path', 'document': 'file_by_path', 'documents': 'file_by_path'}
#: A tool named by the read verb ALONE reads a path the user names (a coding agent's built-in `Read`).
_BARE_READERS = frozenset({'read'})


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_dotted(node.value)}.{node.attr}'
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ''


def _const_str(node: Optional[ast.AST]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _kw(call: ast.Call, *names: str) -> Optional[ast.AST]:
    return next((k.value for k in call.keywords if k.arg in names), None)


class Collector:
    """Accumulates entries and tools; the first source of a (name, kind) wins, in sorted file order."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.inputs: Dict[Tuple[str, str], Dict[str, str]] = {}
        self.tools: Dict[str, str] = {}
        self.switches: Dict[str, Dict[str, Any]] = {}
        #: Tool classes by class name -> ``[(file, tool name)]``: what a toolset that instantiates one holds.
        self.tool_classes: Dict[str, List[Tuple[Path, str]]] = {}
        #: Switches that are an enable flag (a class default, a declared `enabled:`), never where the set is assembled.
        self.flags: Set[str] = set()
        #: Registrars the reader cannot enumerate (`{source, why}`): only a live enumeration names their tools.
        self.dynamic: Dict[str, Dict[str, str]] = {}
        #: Tool-assignment sites (``tools=[…]``, ``bind_tools([…])``, a returned tool list) -> how many tools each holds.
        self.sites: Dict[str, int] = {}
        #: Constructor -> the tools a framework middleware it builds injects (`_INJECTED_BY_CLASS`, subclasses included).
        self.injecting: Dict[str, Tuple[str, ...]] = {}  # filled by collect_assembled_tools
        #: Where the agent pauses for the user's answer (``interrupt``, a mid-turn ``Command(goto=END)``): a UI card.
        self.pauses: Set[str] = set()
        #: Installed tool classes the agent's own code calls directly, never handed to a model (`{class, name, source}`).
        self.backends: Dict[str, Dict[str, Optional[str]]] = {}

    def add_dynamic(self, path: Path, line: int, why: str) -> None:
        source = f'{self.repo.rel(path)}:{line}'
        self.dynamic.setdefault(source, {'source': source, 'why': why})

    def add_input(self, name: str, kind: str, path: Path, line: int, evidence: str) -> None:
        key = (name, kind)
        if name and key not in self.inputs:
            self.inputs[key] = {'name': name, 'kind': kind, 'source': f'{self.repo.rel(path)}:{line}', 'evidence': evidence}

    def add_tool(self, name: str, path: Path, line: int) -> None:
        if name and name not in self.tools:
            self.tools[name] = f'{self.repo.rel(path)}:{line}'


# ---- tools ----------------------------------------------------------------------------------


def _module_strings(tree: Optional[ast.Module]) -> Dict[str, str]:
    """The module's top-level ``NAME = 'literal'`` constants: a tool class may name itself through one."""
    found: Dict[str, str] = {}
    for stmt in tree.body if tree is not None else ():
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and (value := _const_str(stmt.value)):
            found[stmt.targets[0].id] = value
    return found


def _py_tool_name(node: ast.AST, consts: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The tool a definition declares, or None: a tool-family decorator, a tool constructor, a BaseTool subclass."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for deco in node.decorator_list:
            dotted = _dotted(deco)
            if dotted.split('.')[-1] in _TOOL_DECORATORS:
                if isinstance(deco, ast.Call):
                    named = _const_str(deco.args[0]) if deco.args else None
                    return named or _const_str(_kw(deco, 'name', 'name_or_callable')) or node.name
                return node.name
    if isinstance(node, ast.Call) and _dotted(node.func).split('.')[0] in _TOOL_CLASSES:
        return _const_str(_kw(node, 'name'))
    if isinstance(node, ast.ClassDef) and any(_dotted(b).split('.')[-1].endswith('Tool') for b in node.bases):
        for stmt in node.body:
            target = stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else getattr(stmt, 'target', None)
            if isinstance(target, ast.Name) and target.id == 'name':
                return _named(getattr(stmt, 'value', None), consts)
        # `super().__init__(name='x', …)` in the tool's constructor.
        return next((_named(_kw(c, 'name'), consts) for c in ast.walk(node) if isinstance(c, ast.Call) and _dotted(c.func).endswith('__init__')
                     and _named(_kw(c, 'name'), consts)), None)
    return None


def _named(node: Optional[ast.AST], consts: Optional[Dict[str, str]]) -> Optional[str]:
    """A string literal, or a module constant naming one."""
    return _const_str(node) or ((consts or {}).get(node.id) if isinstance(node, ast.Name) else None)


_TS_TOOL_PATTERNS = (
    re.compile(r'(?<![\w.])([A-Za-z_]\w*)\s*:\s*tool\(\s*\{'),
    re.compile(r'(?<![\w.])([A-Za-z_]\w*)\s*:\s*(?:await\s+)?[a-z]\w*Tool\('),
    re.compile(r'\bcreateTool\(\s*\{[^}]*?\bid\s*:\s*[\'"]([^\'"]+)[\'"]', re.DOTALL),
    re.compile(r'\bnew\s+(?:DynamicStructuredTool|DynamicTool|StructuredTool)\(\s*\{[^}]*?\bname\s*:\s*[\'"]([^\'"]+)[\'"]', re.DOTALL),
    re.compile(r'\.(?:tool|registerTool)\(\s*[\'"]([\w.-]+)[\'"]'),
)


def _line_of(text: str, offset: int) -> int:
    return text.count('\n', 0, offset) + 1


def collect_tools(repo: Repo, files: List[Path], out: Collector) -> None:
    for path in sorted(files):
        if path.suffix == '.py':
            tree = repo.tree(path)
            consts = _module_strings(tree)
            for node in ast.walk(tree) if tree is not None else ():
                name = _py_tool_name(node, consts)
                if name:
                    out.add_tool(name, path, getattr(node, 'lineno', 1))
                    if isinstance(node, ast.ClassDef):
                        out.tool_classes.setdefault(node.name, []).append((path, name))
        elif path.suffix in TS_SUFFIXES:
            text = repo.text(path)
            for pattern in _TS_TOOL_PATTERNS:
                for match in pattern.finditer(text):
                    out.add_tool(match.group(1), path, _line_of(text, match.start()))


# ---- tools the source assembles without defining them -----------------------------------------

#: A pass-through constructor: ``X.wrap(inner)`` exposes ``inner`` under ``inner``'s own name.
_WRAPPERS = frozenset({'wrap', 'from_tool', 'from_langchain', 'from_langchain_tool'})
#: Tools a framework adds itself when a constructor flag is on: ``(constructor, flag) -> names`` (a framework fact,
#: read off its own source, never an agent's).
_INJECTED_BY_FLAG = {('Agent', 'allow_delegation'): ('Delegate work to coworker', 'Ask question to coworker')}
#: Tools a framework middleware adds when the agent installs it (directly, or through a subclass of its own).
_INJECTED_BY_CLASS: Dict[str, Tuple[str, ...]] = {'TodoListMiddleware': ('write_todos',)}
#: An agent SDK's options class, by the qualified name the file's imports bind: its ``tools`` / ``allowed_tools``
#: lists name the SDK's built-in tools as strings (``allowed_tools=["Read", "Write"]``).
_SDK_OPTIONS = frozenset({'claude_agent_sdk.ClaudeAgentOptions', 'claude_agent_sdk.types.ClaudeAgentOptions',
                          'claude_code_sdk.ClaudeCodeOptions', 'claude_code_sdk.types.ClaudeCodeOptions'})
#: Tools a framework adds when a constructor, by the qualified name the file's imports bind, is handed a keyword that is
#: not provably empty: ``(constructor, keyword) -> names``. Google ADK (read in google-adk 2.11.0,
#: ``flows/llm_flows/extensions/_agent_transfer.py``) offers ``transfer_to_agent`` to an ``LlmAgent`` (``Agent`` is its
#: alias) whose ``sub_agents`` it may transfer to, and to each of those sub-agents for the transfer back to it.
_INJECTED_BY_IMPORTED_FLAG: Dict[Tuple[str, str], Tuple[str, ...]] = {
    (ctor, 'sub_agents'): ('transfer_to_agent',)
    for ctor in ('google.adk.Agent', 'google.adk.agents.Agent', 'google.adk.agents.LlmAgent',
                 'google.adk.agents.llm_agent.Agent', 'google.adk.agents.llm_agent.LlmAgent')
}
#: How deep a third-party package's re-exports are followed to the class that names a tool.
_REEXPORT_HOPS = 6


def site_packages(root: Path, workdir: Path) -> List[Path]:
    """The agent's installed packages: the interpreter `PLURAI_CHILD_PYTHON` names (``<venv>/bin/python``, never
    resolved through its symlink), else a ``.venv`` / ``venv`` beside the agent or at the repository root."""
    prefixes: List[Path] = []
    if child := os.environ.get('PLURAI_CHILD_PYTHON'):
        prefixes.append(Path(child).parent.parent)
    for here in (workdir.resolve().parent, root):
        prefixes += [here / '.venv', here / 'venv']
    found: List[Path] = []
    for prefix in prefixes:
        for candidate in [*sorted(prefix.glob('lib/python3*/site-packages')), prefix / 'Lib' / 'site-packages']:
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found


class Installed:
    """Reads a tool class's ``name`` default out of the installed package that defines it."""

    def __init__(self, dirs: List[Path]) -> None:
        self.dirs = dirs
        self._trees: Dict[Path, Optional[ast.Module]] = {}

    def _module(self, dotted: str) -> Optional[Tuple[Path, ast.Module]]:
        parts = dotted.split('.')
        for base in self.dirs:
            for path in (base.joinpath(*parts[:-1], parts[-1] + '.py'), base.joinpath(*parts, '__init__.py')):
                if path.is_file():
                    if path not in self._trees:
                        try:
                            self._trees[path] = ast.parse(path.read_text(encoding='utf-8'))
                        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                            self._trees[path] = None
                    tree = self._trees[path]
                    return (path, tree) if tree is not None else None
        return None

    def tool_name(self, module: str, cls: str, hops: int = _REEXPORT_HOPS) -> Optional[str]:
        """The ``name`` default class ``cls`` declares, following ``module``'s re-exports (``from .x import cls``)."""
        found = self._module(module) if hops > 0 else None
        if found is None:
            return None
        path, tree = found
        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef) and stmt.name == cls:
                return _py_tool_name(stmt, _module_strings(tree))
            if isinstance(stmt, ast.ImportFrom) and any((a.asname or a.name) == cls for a in stmt.names):
                original = next(a.name for a in stmt.names if (a.asname or a.name) == cls)
                package = module.split('.') if path.name == '__init__.py' else module.split('.')[:-1]
                base = '.'.join(package[:len(package) - stmt.level + 1] if stmt.level else [])
                target = '.'.join(p for p in (base, stmt.module or '') if p) if stmt.level else (stmt.module or '')
                return self.tool_name(target, original, hops - 1)
        return None


def _imported_names(tree: ast.Module) -> Dict[str, Tuple[str, str]]:
    """``{local name: (module, original name)}`` for a file's absolute ``from M import N [as A]`` imports."""
    found: Dict[str, Tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                found[alias.asname or alias.name] = (node.module, alias.name)
    return found


def _tool_lists(node: ast.AST) -> List[List[ast.expr]]:
    """The tool lists a node hands an agent: a ``tools=[…]`` keyword, ``bind_tools([…])``, ``tools.append(x)`` /
    ``tools.extend([…])``, and the list a tool-named function returns."""
    lists: List[List[ast.expr]] = []
    if isinstance(node, ast.Call):
        value = _kw(node, 'tools')
        if isinstance(value, (ast.List, ast.Tuple)):
            lists.append(value.elts)
        method = node.func.attr if isinstance(node.func, ast.Attribute) else ''
        if method == 'bind_tools' and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
            lists.append(node.args[0].elts)
        target = _dotted(node.func.value) if isinstance(node.func, ast.Attribute) else ''
        if method in ('append', 'extend') and node.args and re.search(r'(^|_)tools$', target.split('.')[-1].lower()):
            arg = node.args[0]
            lists.append(arg.elts if method == 'extend' and isinstance(arg, (ast.List, ast.Tuple)) else [arg])
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lower().endswith('tools'):
        lists += [r.value.elts for r in ast.walk(node) if isinstance(r, ast.Return) and isinstance(r.value, (ast.List, ast.Tuple))]
    return lists


def _subclasses_of(repo: Repo, files: List[Path], roots: Set[str]) -> Set[str]:
    """Every class in ``files`` deriving (transitively, by name) from one of ``roots``, and the roots themselves."""
    bases: Dict[str, Set[str]] = {}
    for path in (f for f in files if f.suffix == '.py'):
        for node in ast.walk(repo.tree(path) or ast.Module(body=[], type_ignores=[])):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(_dotted(b).split('.')[-1] for b in node.bases)
    found = set(roots)
    while grown := {c for c, b in bases.items() if c not in found and b & found}:
        found |= grown
    return found


def collect_assembled_tools(repo: Repo, files: List[Path], out: Collector, installed: Installed) -> None:
    """The tools a tool list holds without the scope defining them: an inline instance of an installed tool class
    (its ``name`` default read from the package), a wrapper (``X.wrap(inner)`` is ``inner``), a tool a framework
    injects (a constructor flag, an installed middleware); and the registrars whose names only exist at run time
    (``tool(fn)`` over a computed ``__name__``, a tool class named by its constructor's argument) — recorded
    `dynamic`, so only a live enumeration can name them. Each tool list is a candidate assembly site (`loader`)."""
    injecting: Dict[str, Tuple[str, ...]] = {cls: names for root, names in _INJECTED_BY_CLASS.items() for cls in _subclasses_of(repo, files, {root})}
    out.injecting = injecting
    for path in sorted(f for f in files if f.suffix == '.py'):
        tree = repo.tree(path)
        if tree is None:
            continue
        # Installed packages only: an in-repo name is read off the scope itself.
        imported = {k: v for k, v in _imported_names(tree).items() if repo.resolve_py(v[0], path) is None}
        listed: Set[int] = set()
        values: Dict[str, ast.expr] = {t.id: n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        bound = _bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = _qualified(node.func, bound)
                for (name, flag), names in _INJECTED_BY_IMPORTED_FLAG.items():
                    if callee == name and (value := _kw(node, flag)) is not None and _default_on(value) is not False:
                        for tool_name in names:
                            out.add_tool(tool_name, path, node.lineno)
                if callee in _SDK_OPTIONS:
                    named = [(s, e.lineno) for kw in ('tools', 'allowed_tools') if isinstance(v := _kw(node, kw), (ast.List, ast.Tuple))
                             for e in v.elts if (s := _const_str(e))]
                    for tool_name, line in named:
                        out.add_tool(tool_name, path, line)
                    if named:
                        site = f'{repo.rel(path)}:{node.lineno}'
                        out.sites[site] = max(out.sites.get(site, 0), len({n for n, _ in named}))
                ctor = _dotted(node.func).split('.')[-1]
                for (name, flag), names in _INJECTED_BY_FLAG.items():
                    if ctor == name and (value := _kw(node, flag)) is not None and _default_on(value) is not False:
                        for tool_name in names:
                            out.add_tool(tool_name, path, node.lineno)
                for tool_name in injecting.get(ctor, ()):
                    out.add_tool(tool_name, path, node.lineno)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == '__name__' for t in node.targets) \
                    and _const_str(node.value) is None:
                out.add_dynamic(path, node.lineno, 'a tool function is named at run time (`__name__` assigned a computed value)')
            for elements in _tool_lists(node):
                held = sum(_element_tool(e, path, imported, values, out, installed) for e in elements)
                site = f'{repo.rel(path)}:{getattr(node, "lineno", 1)}'
                out.sites[site] = max(out.sites.get(site, 0), held)
                listed |= {id(n) for e in elements for n in ast.walk(e)} | {id(values[e.id]) for e in elements if isinstance(e, ast.Name) and e.id in values}
        for node in ast.walk(tree):
            ctor = _dotted(node.func).split('.')[-1] if isinstance(node, ast.Call) else ''
            if ctor in imported and ctor not in _TOOL_DECORATORS and id(node) not in listed:
                module, original = imported[ctor]
                read = installed.tool_name(module, original)
                if read is not None or ('.tools.' in f'.{module}.' and original[:1].isupper() and original not in _TOOL_CLASSES
                                        and not original.endswith(('Exception', 'Error'))):
                    out.backends.setdefault(f'{module}.{original}', {'class': f'{module}.{original}', 'name': read,
                                                                     'source': f'{repo.rel(path)}:{getattr(node, "lineno", 1)}'})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_pause(node, bound):
                out.pauses.add(f'{repo.rel(path)}:{node.lineno}')
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if any(_dotted(b).split('.')[-1].endswith('Tool') for b in cls.bases) and _py_tool_name(cls) is None and any(
                    isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'name' and _dotted(t.value) == 'self' for t in n.targets)
                    and _const_str(n.value) is None for n in ast.walk(cls)):
                out.add_dynamic(path, cls.lineno, f'`{cls.name}` takes its tool name from its constructor: its instances are named at run time')


# ---- tools a TS framework adds from how the agent's code builds it ----------------------------------------------

#: Mastra Memory (``@mastra/memory``) adds tools to the agent it serves, decided by the ``options`` it is built with. The
#: one rule read here is the measured one: a literal ``workingMemory: {enabled: true}``
#: adds ``updateWorkingMemory``. Every other switch that can change that set (:data:`_MASTRA_MEMORY_UNREAD`), and any
#: value the code computes or spreads in, is not guessed: the constructor is recorded `dynamic`, and the live smoke's
#: enumeration names what it adds (``trail_tool_untyped`` otherwise).
_MASTRA_MEMORY_IMPORT = re.compile(
    r'''(?:import\s*\{([^}]*)\}\s*from|(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\()\s*['"]@mastra/memory['"]''')
#: The Memory switches beside ``workingMemory.enabled`` that add, rename or drop a tool, with their default literal:
#: ``(object, key, default)``. Set to anything but its default, one leaves the constructor unread.
_MASTRA_MEMORY_UNREAD = (('options', 'readOnly', 'false'), ('options', 'observationalMemory', 'false'),
                         ('workingMemory', 'agentManaged', 'true'), ('workingMemory', 'useStateSignals', 'false'))


def _mastra_memory_tools(argument: str) -> Tuple[List[str], List[str]]:
    """``(the tools, the switches left unread)`` for one ``new Memory(<argument>)`` (:data:`_MASTRA_MEMORY_IMPORT`)."""
    unread: List[str] = []

    def literal(value: Optional[str], name: str) -> Optional[str]:
        """The body of an object literal that spreads nothing in; None for an absent or ``false`` value, and None with
        ``name`` unread for anything else (a reference, a call, a spread that could carry any key)."""
        if value is None or value == 'false':
            return None
        body = _balanced(value, 0) if value.startswith('{') else None
        if body is None or not _spreads_nothing(body):
            unread.append(name)
            return None
        return body

    objects: Dict[str, Optional[str]] = {'memory': literal(argument.strip() or None, 'the constructor argument')}
    for parent, key in (('memory', 'options'), ('options', 'workingMemory')):
        body = objects[parent]
        objects[key] = literal(_entry_value(body, key), key) if body is not None else None
    for where, key, default in _MASTRA_MEMORY_UNREAD:
        body = objects[where]
        if body is not None and _entry_value(body, key) not in (None, default):
            unread.append(key)
    working = objects['workingMemory']
    enabled = _entry_value(working, 'enabled') if working is not None else None
    if enabled not in (None, 'true', 'false'):
        unread.append('enabled')
    return (['updateWorkingMemory'] if enabled == 'true' and not unread else []), unread


def collect_ts_injected_tools(repo: Repo, files: List[Path], out: Collector) -> None:
    """The tools a TS framework adds to the agent from how the agent's code builds it: each ``new Memory(…)`` a file
    imports from ``@mastra/memory`` (:func:`_mastra_memory_tools`), cited at the constructor. A switch left unread is
    recorded `dynamic`, so only a live enumeration names what it adds."""
    for path in sorted(f for f in files if f.suffix in TS_SUFFIXES):
        text = _code_only(repo.text(path))
        bound = [b.group(1) or 'Memory' for m in _MASTRA_MEMORY_IMPORT.finditer(text)
                 for b in re.finditer(r'(?<![\w$])Memory(?:\s*(?:as|:)\s*([A-Za-z_$][\w$]*))?(?![\w$])', m.group(1) or m.group(2) or '')]
        for match in (m for local in dict.fromkeys(bound) for m in re.finditer(rf'(?<![\w$.])new\s+{re.escape(local)}\s*\(', text)):
            tools, unread = _mastra_memory_tools(_balanced(text, match.end() - 1, '('))
            line = _line_of(text, match.start())
            for name in tools:
                out.add_tool(name, path, line)
            if unread:
                out.add_dynamic(path, line, f'Mastra Memory decides the tools it adds from {", ".join(f"`{k}`" for k in unread)}, '
                                            'which the static read does not evaluate')


#: The framework calls that stop a run for the user's answer, by the qualified name the file's imports bind.
_PAUSE_CALLS = frozenset({'langgraph.types.interrupt', 'langgraph.errors.NodeInterrupt'})


def _bindings(tree: ast.Module) -> Dict[str, str]:
    """``{local name: qualified name}`` for a file's absolute imports (``from M import N [as A]``, ``import M [as A]``)."""
    found = {local: f'{module}.{name}' for local, (module, name) in _imported_names(tree).items()}
    for node in (n for n in ast.walk(tree) if isinstance(n, ast.Import)):
        for alias in node.names:
            local = alias.asname or alias.name.split('.')[0]
            found[local] = alias.name if alias.asname else local
    return found


def _qualified(node: ast.AST, bound: Dict[str, str]) -> str:
    """The dotted name of ``node`` with its head resolved through the file's imports; '' when the head is unbound."""
    head, _, rest = _dotted(node).partition('.')
    return '.'.join(p for p in (bound[head], rest) if p) if head in bound else ''


def _is_pause(call: ast.Call, bound: Dict[str, str]) -> bool:
    """A point where the agent stops for the user's answer, resolved by import, never by bare name: LangGraph's
    ``interrupt(…)`` (or a raised ``NodeInterrupt``), a LangGraph ``Command(resume=…)`` answering one, or a
    ``Command(goto=END)`` a middleware returns mid-turn (a clarification or approval card the UI renders), ``END``
    being LangGraph's. An ``interrupt`` bound to anything else — an asyncio cancellation helper, ``task.interrupt()``
    — is not a pause."""
    callee = _qualified(call.func, bound)
    if callee in _PAUSE_CALLS:
        return True
    if _dotted(call.func).split('.')[-1] != 'Command' or (callee and not callee.startswith('langgraph.')):
        return False
    goto, resume = _kw(call, 'goto'), _kw(call, 'resume')
    ends = goto is not None and (target := _qualified(goto, bound)).startswith('langgraph.') and target.endswith('.END')
    # A Command rebuilt from another (``resume=result.resume``) forwards a field; it answers nothing.
    answers = resume is not None and not (isinstance(resume, ast.Constant) and resume.value is None) \
        and not (isinstance(resume, ast.Attribute) and resume.attr == 'resume')
    return ends or (callee.startswith('langgraph.') and answers)


def _element_tool(node: ast.expr, path: Path, imported: Dict[str, Tuple[str, str]], values: Dict[str, ast.expr], out: Collector,
                  installed: Installed, depth: int = 0) -> int:
    """1 when a tool-list element is a tool (recorded on the way), else 0."""
    if depth > 4:
        return 0
    if isinstance(node, ast.Name):
        if node.id in out.tools:
            return 1
        return _element_tool(values[node.id], path, imported, values, out, installed, depth + 1) if node.id in values else 0
    if isinstance(node, ast.Attribute):
        return int(node.attr in out.tools)
    if not isinstance(node, ast.Call):
        return 0
    func = _dotted(node.func).split('.')[-1]
    if isinstance(node.func, ast.Attribute) and node.func.attr in _WRAPPERS and node.args:
        return _element_tool(node.args[0], path, imported, values, out, installed, depth + 1)
    if func in _TOOL_DECORATORS:
        if (named := _const_str(node.args[0]) if node.args else None) is not None:
            out.add_tool(named, path, node.lineno)
        else:
            out.add_dynamic(path, node.lineno, f'`{func}(…)` wraps a function whose name is set at run time')
        return 1
    if func in out.tool_classes or (func in _TOOL_CLASSES and _py_tool_name(node)):
        return 1
    if func in imported:
        module, original = imported[func]
        if (name := installed.tool_name(module, original)) is not None:
            out.add_tool(name, path, node.lineno)
        else:
            out.add_dynamic(path, node.lineno, f'`{func}()` is an instance of `{module}.{original}`, whose `name` default the reader could '
                            'not read (the package is not installed where the reader looks)')
        return 1
    return 0


# ---- platform metadata: an agent defined as a declarative bundle -------------------------------

#: Agent bundle files: a platform's declarative agent definition (topics/subagents, actions and their targets).
BUNDLE_SUFFIXES = ('.agent',)
_BUNDLE_BLOCK = re.compile(r'^(?:start_agent|subagent|topic)\s+(\w+)\s*:\s*$')
_BUNDLE_KEY = re.compile(r'^(\s*)"?(\w+)"?\s*:\s*(.*)$')
_BUNDLE_TRANSITION = re.compile(r'@utils\.transition\s+to\s+@(?:subagent|topic)\.(\w+)')
_BUNDLE_WHEN = re.compile(r'^\s*available when\s+@variables\.(\w+)\s*(==|!=)\s*(\S+)')
_BUNDLE_VARIABLE = re.compile(r'^\s{4}(\w+)\s*:\s*(mutable|linked)\s+(\w+)(?:\s*=\s*(\S+))?')


def bundle_files(root: Path) -> List[Path]:
    found: List[Path] = []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
        found += [Path(here) / f for f in sorted(files) if f.endswith(BUNDLE_SUFFIXES)]
    return found


def declared_bundle(workdir: Path) -> Optional[str]:
    """The bundle the config's ``agent.bundle`` names (a path from the repository root), or None."""
    try:
        config = BlockYaml((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')).root
    except (OSError, UnicodeDecodeError):
        return None
    agent = config.get('agent') if isinstance(config, dict) else None
    bundle = agent.get('bundle') if isinstance(agent, dict) else None
    return bundle.strip() if isinstance(bundle, str) and bundle.strip() else None


def collect_bundles(repo: Repo, out: Collector, declared: Optional[str] = None) -> List[Dict[str, str]]:
    """A declarative agent bundle (the declared one, else every bundle in the repository): every action with a ``target:`` (or a platform utility such as an escalation) is a
    tool; an ``available when @variables.X`` guard on a transition is a switch gating the actions of the block it
    reaches; a ``linked`` variable and an ``External`` one are inputs the host hands the agent. Returns the bundle's
    knowledge blocks (``[{name, source}]``)."""
    knowledge: List[Dict[str, str]] = []
    paths = bundle_files(repo.root)
    for path in [p for p in paths if repo.rel(p) == declared] or paths:
        lines = repo.text(path).splitlines()
        block, section = '', ''
        keys: List[Tuple[int, str, int]] = []
        actions: Dict[str, List[str]] = {}
        guards: List[Tuple[str, str, int, str]] = []
        variables: Dict[str, str] = {}
        for number, line in enumerate(lines, 1):
            if match := _BUNDLE_BLOCK.match(line):
                block, keys = match.group(1), []
                continue
            if line and not line[0].isspace():
                section = line.split(':')[0].strip()
                block = '' if section in ('variables', 'knowledge', 'config', 'system', 'language', 'access') else block
                if section == 'knowledge':
                    knowledge.append({'name': f'{path.stem} knowledge', 'source': f'{repo.rel(path)}:{number}'})
                continue
            if section == 'variables' and (var := _BUNDLE_VARIABLE.match(line)):
                variables[var.group(1)] = var.group(4) or ''
                body = next((lines[number:number + i] for i, after in enumerate(lines[number:], 0) if after.strip() and len(after) - len(after.lstrip()) <= 4),
                            lines[number:])
                external = any(re.match(r'\s*visibility:\s*"?External"?\s*$', after) for after in body)
                if var.group(2) == 'linked' or external:
                    out.add_input(var.group(1), 'structured_context', path, number,
                                  f'the bundle declares a {"linked" if var.group(2) == "linked" else "External"} variable the host sets')
                continue
            if key := _BUNDLE_KEY.match(line):
                indent = len(key.group(1))
                keys = [k for k in keys if k[0] < indent] + [(indent, key.group(2), number)]
                rest = key.group(3)
                parent = next((k for k in reversed(keys[:-1]) if k[0] == indent - 4), None)
                if key.group(2) == 'target' and parent is not None:
                    out.add_tool(parent[1], path, parent[2])
                    actions.setdefault(block, []).append(parent[1])
                elif rest.startswith('@utils.') and not rest.startswith('@utils.transition'):
                    out.add_tool(key.group(2), path, number)
                    actions.setdefault(block, []).append(key.group(2))
                elif transition := _BUNDLE_TRANSITION.search(rest):
                    keys[-1] = (indent, transition.group(1), number)
                continue
            if when := _BUNDLE_WHEN.match(line):
                reached = next((k[1] for k in reversed(keys) if k[0] < len(line) - len(line.lstrip())), '')
                guards.append((when.group(1), reached, number, f'{when.group(2)} {when.group(3)}'))
        for variable, reached, number, test in guards:
            key_name = f'variables.{variable}'
            _add_switch(out, key_name, repo, path, number, None, variables.get(variable, ''), actions.get(reached, []))
            out.switches[key_name].setdefault('condition', test)
            out.flags.add(key_name)
        if held := sum(len(set(names)) for names in actions.values()):
            out.sites[f'{repo.rel(path)}:1'] = held
    return knowledge


# ---- tools declared in YAML -----------------------------------------------------------------

YAML_SUFFIXES = ('.yaml', '.yml')
_YAML_KEY = re.compile(r'''^(?P<key>"[^"]*"|'[^']*'|[^\s'"\[\]{}#&*!|>%@`,-][^:]*?)\s*:(?:[ \t]+(?P<rest>.*))?$''')
_YAML_BLOCK_SCALAR = re.compile(r'^[|>][-+0-9]*$')
_YAML_TRUE = frozenset({'true', 'yes', 'on'})
_YAML_FALSE = frozenset({'false', 'no', 'off'})


def _yaml_scalar(text: str) -> Any:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    if value.lower() in _YAML_TRUE:
        return True
    if value.lower() in _YAML_FALSE:
        return False
    return None if value.lower() in ('', '~', 'null') else value


def _yaml_uncomment(line: str) -> str:
    quote = ''
    for i, ch in enumerate(line):
        if quote:
            quote = '' if ch == quote else quote
        elif ch in '"\'' and (i == 0 or line[i - 1] in ' \t:[{,-'):
            quote = ch
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            return line[:i].rstrip()
    return line.rstrip()


class BlockYaml:
    """The block-style subset of YAML a declaration file uses — mappings, sequences, scalars; block scalars (``|``,
    ``>``) skipped, flow collections kept as text — with the line each key and item sits on. Stdlib only: enough to
    read the tools a file declares, never a general parser."""

    def __init__(self, text: str) -> None:
        self.rows: List[Tuple[int, int, str]] = []
        for number, raw in enumerate(text.splitlines(), 1):
            line = _yaml_uncomment(raw)
            body = line.strip()
            if body and body not in ('---', '...') and not body.startswith('%'):
                self.rows.append((number, len(line) - len(line.lstrip()), body))
        #: ``id(mapping or sequence)`` -> ``{key or index: line}``.
        self.lines: Dict[int, Dict[Any, int]] = {}
        self.i = 0
        self.root = self._block(self.rows[0][1]) if self.rows else None

    @staticmethod
    def _is_item(content: str) -> bool:
        return content == '-' or content.startswith('- ')

    def _block(self, indent: int) -> Any:
        return self._seq(indent) if self._is_item(self.rows[self.i][2]) else self._map(indent)

    def _map(self, indent: int) -> Dict[Any, Any]:
        node: Dict[Any, Any] = {}
        lines = self.lines.setdefault(id(node), {})
        while self.i < len(self.rows):
            number, here, content = self.rows[self.i]
            if here < indent or (here == indent and self._is_item(content)):
                break
            match = _YAML_KEY.match(content) if here == indent else None
            self.i += 1
            if match is None:
                continue  # a deeper continuation line, or a line this subset does not read
            key = _yaml_scalar(match.group('key'))
            node[key] = self._value((match.group('rest') or '').strip(), here)
            lines[key] = number
        return node

    def _seq(self, indent: int) -> List[Any]:
        node: List[Any] = []
        lines = self.lines.setdefault(id(node), {})
        while self.i < len(self.rows):
            number, here, content = self.rows[self.i]
            if here > indent:
                self.i += 1
                continue
            if here < indent or not self._is_item(content):
                break
            rest = content[1:].lstrip()
            lines[len(node)] = number
            if rest and _YAML_KEY.match(rest):
                inner = here + len(content) - len(rest)
                self.rows[self.i] = (number, inner, rest)
                node.append(self._map(inner))
            else:
                self.i += 1
                node.append(self._value(rest, here))
        return node

    def _value(self, rest: str, indent: int) -> Any:
        if rest.startswith(('&', '!')):
            rest = rest.partition(' ')[2].strip()
        if rest and not _YAML_BLOCK_SCALAR.match(rest):
            return _yaml_scalar(rest)
        if rest:
            while self.i < len(self.rows) and self.rows[self.i][1] > indent:
                self.i += 1
            return ''
        if self.i < len(self.rows):
            here, content = self.rows[self.i][1], self.rows[self.i][2]
            if here > indent or (here == indent and self._is_item(content)):
                return self._block(here)
        return None


def yaml_toolsets(text: str) -> List[Dict[str, Any]]:
    """Every toolset a YAML file declares: a mapping whose ``tools`` is a list of entries each carrying a ``name`` and a
    definition (a bare list of names is an allow-list, not a declaration). Each is ``{label, tools: [(name, line)],
    enabled: bool | None, enabled_line}`` — ``enabled`` is the toolset's own flag when it states one."""
    doc = BlockYaml(text)
    found: List[Dict[str, Any]] = []

    def walk(node: Any, label: str) -> None:
        if isinstance(node, dict):
            tools = node.get('tools')
            if isinstance(tools, list):
                named = [(item['name'], doc.lines[id(item)]['name']) for item in tools
                         if isinstance(item, dict) and isinstance(item.get('name'), str) and len(item) > 1]
                if named:
                    enabled = node.get('enabled')
                    found.append({'label': label, 'tools': named, 'enabled': enabled if isinstance(enabled, bool) else None,
                                  'enabled_line': doc.lines[id(node)].get('enabled')})
            for key, value in node.items():
                if key != 'tools':
                    walk(value, str(key))
        elif isinstance(node, list):
            for value in node:
                walk(value, label)

    walk(doc.root, '')
    return found


def collect_yaml_tools(repo: Repo, files: List[Path], out: Collector) -> None:
    """Tools declared in YAML beside the tool scope's modules (a loader that reads its own directory's ``*.yaml``). A
    toolset that states ``enabled: false`` is a switch, OFF, cited at that line; one that states nothing counts ON."""
    for folder in sorted({f.parent for f in files}):
        for path in sorted(p for p in folder.iterdir() if p.suffix in YAML_SUFFIXES and p.is_file() and not _TEST_FILE.search(p.name)):
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            text = repo.text(path)
            if 'tools' not in text:
                continue
            for toolset in yaml_toolsets(text):
                for name, line in toolset['tools']:
                    out.add_tool(name, path, line)
                if toolset['enabled'] is False:
                    key = f'{toolset["label"] or path.stem}.enabled'
                    _add_switch(out, key, repo, path, toolset['enabled_line'] or 1, False, 'false', [n for n, _ in toolset['tools']])
                    out.flags.add(key)


# ---- capability switches --------------------------------------------------------------------

_ENV_CALLS = frozenset({'os.getenv', 'getenv', 'os.environ.get', 'environ.get'})
_TRUE_WORDS = frozenset({'1', 'true', 'yes', 'on', 'enabled'})
_CONFIG_READERS = re.compile(r'(get_value|get_config|get_setting|get_option|get_app_value|getenv)$')
_TOGGLE_KEY = re.compile(r'tool|toolset|categor|capabilit|feature|mcp|plugin|skill|(^|_)(enabled?|disabled?)(_|$)|(^|_)mode$|expos|^NEXT_PUBLIC_',
                         re.IGNORECASE)
#: A flag that switches capabilities by its name: `*_enabled` / `enable_*` / `*_disabled`, a mode (`is_plan_mode`,
#: `*_mode`), an exposure setting. Read wherever it guards a tool, a middleware add or the prompt.
_SWITCH_NAME = re.compile(r'(^|_)(enabled?|disabled?)(_|$)|(^|_)mode$|expos', re.IGNORECASE)
#: A parameter that turns every container on regardless of its class default (``enable_all_toolsets=True``).
_ENABLE_ALL = re.compile(r'^enable_all', re.IGNORECASE)


def _env_var(node: ast.AST) -> Optional[Tuple[str, Optional[str]]]:
    """``(VAR, default)`` when ``node`` reads an env var, else None."""
    if isinstance(node, ast.Call) and _dotted(node.func) in _ENV_CALLS and node.args:
        var = _const_str(node.args[0])
        default = _const_str(node.args[1]) if len(node.args) > 1 else _const_str(_kw(node, 'default'))
        return (var, default) if var else None
    if isinstance(node, ast.Subscript) and _dotted(node.value) in ('os.environ', 'environ'):
        var = _const_str(node.slice if not isinstance(node.slice, ast.Index) else node.slice.value)  # type: ignore[attr-defined]
        return (var, None) if var else None
    return None


def _evaluate(node: ast.AST, var: str, default: Optional[str]) -> Any:
    """``node`` with the env var at its default; ``...`` when the expression is beyond this reader."""
    found = _env_var(node)
    if found is not None and found[0] == var:
        return default
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('lower', 'strip', 'upper') and not node.args:
        inner = _evaluate(node.func.value, var, default)
        return inner if inner is ... or inner is None else getattr(str(inner), node.func.attr)()
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _evaluate(node.operand, var, default)
        return ... if inner is ... else not _truthy(inner)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = _evaluate(node.left, var, default), _evaluate(node.comparators[0], var, default)
        if left is ... or right is ...:
            return ...
        op = node.ops[0]
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.In) and isinstance(right, (list, tuple, set, str)):
            return left in right
        return ...
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_evaluate(e, var, default) for e in node.elts]
        return ... if ... in values else tuple(values)
    return ...


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in _TRUE_WORDS if isinstance(value, str) else bool(value)


def collect_switches(repo: Repo, files: List[Path], out: Collector) -> None:
    """Env-gated tool blocks and config-keyed directory loaders in the tool scope; settings toggles repo-wide."""
    for path in sorted(files):
        if path.suffix != '.py' or (tree := repo.tree(path)) is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                env = next((e for e in map(_env_var, ast.walk(node.test)) if e is not None), None)
                if env is None:
                    continue
                gated = sorted({n for n in (_py_tool_name(sub) or _const_str(sub) or getattr(sub, 'id', None) for stmt in node.body for sub in ast.walk(stmt)) if n in out.tools})
                if not gated:
                    continue
                value = _evaluate(node.test, env[0], env[1])
                _add_switch(out, f'env:{env[0]}', repo, path, node.lineno, None if value is ... else _truthy(value), repr(env[1]), gated)
        loaded = repo.dynamic_dir_modules(path)
        if loaded:
            gated = sorted(n for n, src in out.tools.items() if any(src.startswith(repo.rel(f) + ':') for f in loaded))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _CONFIG_READERS.search(_dotted(node.func)) and node.args and (key := _const_str(node.args[0])):
                    _add_switch(out, key, repo, path, node.lineno, None, '', gated)
    for path in repo.files:
        if path.suffix != '.py' or 'default' not in repo.text(path) or (tree := repo.tree(path)) is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or (default := _kw(node, 'default')) is None:
                continue
            key = _const_str(_kw(node, 'id', 'key', 'name'))
            if not key or (key not in out.switches and not _TOGGLE_KEY.search(key)):
                continue
            on = _default_on(default)
            switch = out.switches.get(key)
            if switch is None:
                _add_switch(out, key, repo, path, node.lineno, on, ast.unparse(default)[:120], [])
            else:
                switch.update({'default': ast.unparse(default)[:120], 'default_on': on, 'declared': f'{repo.rel(path)}:{node.lineno}'})


def _tools_in(stmts: List[ast.stmt], out: Collector) -> Set[str]:
    """The production tools a block defines, names or injects (a middleware that adds its own)."""
    found: Set[str] = set()
    for sub in (n for stmt in stmts for n in ast.walk(stmt)):
        found |= {n for n in (_py_tool_name(sub), _const_str(sub), getattr(sub, 'id', None), getattr(sub, 'attr', None)) if isinstance(n, str) and n in out.tools}
        if isinstance(sub, ast.Call):
            found |= set(out.injecting.get(_dotted(sub.func).split('.')[-1], ()))
    return found


def collect_flag_switches(repo: Repo, files: List[Path], out: Collector) -> None:
    """Capability flags read by NAME (`_SWITCH_NAME`): a request/config flag bound off a ``.get('<flag>', <default>)``
    that guards a tool block or is handed to a function that builds one (the default is the literal's), and any
    ``if <…flag>:`` over a tool block (a settings attribute: default unresolved, so counted ON)."""
    functions: Dict[str, ast.AST] = {}
    trees = [(p, t) for p in sorted(f for f in files if f.suffix == '.py') if (t := repo.tree(p)) is not None]
    for _path, tree in trees:
        functions.update((fn.name, fn) for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)))
    for path, tree in trees:
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'get'
                        and node.value.args and (key := _const_str(node.value.args[0])) and _SWITCH_NAME.search(key)):
                    continue
                local = node.targets[0].id
                gated: Set[str] = set()
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.If) and any(isinstance(n, ast.Name) and n.id == local for n in ast.walk(sub.test)):
                        gated |= _tools_in(sub.body, out)
                    elif isinstance(sub, ast.Call) and any(isinstance(a, ast.Name) and a.id == local for a in sub.args) \
                            and (callee := functions.get(_dotted(sub.func).split('.')[-1])) is not None and callee is not fn:
                        gated |= _tools_in(callee.body, out)  # type: ignore[attr-defined]
                if gated:
                    default = node.value.args[1] if len(node.value.args) > 1 else None
                    _add_switch(out, key, repo, path, node.lineno, _default_on(default) if default is not None else None,
                                ast.unparse(default)[:120] if default is not None else '', sorted(gated))
                    out.flags.add(key)
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and (flag := next((_dotted(n) for n in ast.walk(node.test) if isinstance(n, (ast.Name, ast.Attribute))
                                                           and _SWITCH_NAME.search(_dotted(n).split('.')[-1])), None)):
                if (gated_here := _tools_in(node.body, out)) and not any(_env_var(n) for n in ast.walk(node.test)):
                    _add_switch(out, flag, repo, path, node.lineno, None, '', sorted(gated_here))
                    out.flags.add(flag)


_TS_SPREAD = re.compile(r'\.\.\.\(\s*([^?;]{1,200}?)\s*\?\s*\{')
_TS_ENV = re.compile(r'\b(?:process\.)?env\.([A-Z][A-Z0-9_]+)')


def collect_ts_switches(repo: Repo, files: List[Path], out: Collector) -> None:
    """A TS tool registry's conditional spread (``...(cond ? { a: aTool(…) } : {})``): ``cond`` is a switch gating
    those tools — an env var when it reads one (directly or through the ``const`` it is bound to), else the condition.
    Its production default is unresolved (counted ON). The registry with the most tools is an assembly site."""
    for path in sorted(f for f in files if f.suffix in TS_SUFFIXES):
        text = repo.text(path)
        if not any(src.startswith(repo.rel(path) + ':') for src in out.tools.values()):
            continue
        registered = [(m.start(), m.group(1)) for pattern in _TS_TOOL_PATTERNS for m in pattern.finditer(text) if m.group(1) in out.tools]
        if registered:
            out.sites[f'{repo.rel(path)}:{_line_of(text, min(o for o, _ in registered))}'] = len({n for _, n in registered})
        for match in _TS_SPREAD.finditer(text):
            gated = sorted(k for k in _top_level_keys(_balanced(text, match.end() - 1)) if k in out.tools)
            if not gated:
                continue
            condition = match.group(1).strip()
            env = _TS_ENV.search(condition)
            bound = re.fullmatch(r'!?\s*(\w+)', condition)
            if env is None and bound and (declared := re.search(rf'\bconst\s+{bound.group(1)}\s*=\s*([^;]+);', text)):
                env = _TS_ENV.search(declared.group(1))
            key = f'env:{env.group(1)}' if env else f'condition:{condition[:80]}'
            _add_switch(out, key, repo, path, _line_of(text, match.start()), None, '', gated)
            out.flags.add(key)


def _default_on(node: ast.AST) -> Optional[bool]:
    """Whether a settings default switches its capabilities ON: a `True` inside it (``dict.fromkeys(x, True)``), an
    explicit `False` / empty value OFF, anything else unresolved."""
    constants = [n.value for n in ast.walk(node) if isinstance(n, ast.Constant)]
    if True in constants:
        return True
    if isinstance(node, ast.Constant) and (node.value in (False, None, '', 0)):
        return False
    if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)) and not getattr(node, 'elts', getattr(node, 'keys', None)):
        return False
    return None


def _add_switch(out: Collector, key: str, repo: Repo, path: Path, line: int, on: Optional[bool], default: str, gates: List[str]) -> None:
    switch = out.switches.setdefault(key, {'switch': key, 'source': f'{repo.rel(path)}:{line}', 'default': default, 'default_on': on, 'gates': []})
    switch['gates'] = sorted(set(switch['gates']) | set(gates))


#: The attribute a tool container declares its own on/off with.
_ENABLE_FLAGS = frozenset({'enabled', 'is_enabled', 'enable', 'enabled_by_default', 'default_enabled'})


def _class_flag(cls: ast.ClassDef) -> Optional[Tuple[str, ast.AST, int]]:
    """``(attr, default, line)`` when the class body declares an enable flag with a default (``enabled: bool = False``,
    ``enabled = Field(default=False)``)."""
    for stmt in cls.body:
        target = stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else getattr(stmt, 'target', None)
        value = getattr(stmt, 'value', None)
        if isinstance(target, ast.Name) and target.id in _ENABLE_FLAGS and value is not None:
            if isinstance(value, ast.Call) and _dotted(value.func).split('.')[-1] == 'Field':
                value = value.args[0] if value.args else _kw(value, 'default')
            if value is not None:
                return target.id, value, stmt.lineno
    return None


def _turns_on(cls: ast.ClassDef, attr: str) -> bool:
    """Whether a subclass sets the flag itself: a class-level override, the flag passed to its base's ``__init__``, or
    ``self.<flag> = …`` in its own ``__init__``. An expression the reader cannot evaluate counts as ON."""
    values: List[ast.AST] = []
    flag = _class_flag(cls)
    if flag is not None and flag[0] == attr:
        values.append(flag[1])
    for fn in cls.body:
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == '__init__'):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and _dotted(node.func).endswith('__init__') and (value := _kw(node, attr)) is not None:
                values.append(value)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == attr and _dotted(t.value) == 'self' for t in node.targets):
                values.append(node.value)
    return any(_default_on(v) is not False for v in values)


def collect_class_defaults(repo: Repo, files: List[Path], out: Collector) -> None:
    """A tool container whose class declares its enable flag OFF (``class Toolset: enabled: bool = False``): a
    subclass that never turns the flag on loads only when configuration enables it, so the tools it holds are not
    production. One switch per such class, OFF, cited at the flag's line, gating every tool held only by subclasses
    that leave it off (a tool also held by one that turns it on stays production)."""
    classes: List[Tuple[Path, ast.ClassDef]] = []
    for path in sorted(f for f in files if f.suffix == '.py'):
        tree = repo.tree(path)
        classes += [(path, node) for node in ast.walk(tree) if isinstance(node, ast.ClassDef)] if tree is not None else []
    children: Dict[str, List[Tuple[Path, ast.ClassDef]]] = {}
    for path, cls in classes:
        for base in {_dotted(b).split('.')[-1] for b in cls.bases}:
            children.setdefault(base, []).append((path, cls))
    enable_all = next((f'{repo.rel(path)}:{arg.lineno}' for path, tree in ((p, repo.tree(p)) for p in repo.files if p.suffix == '.py' and 'enable_all' in repo.text(p))
                       if tree is not None
                       for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                       for arg, default in _param_defaults(fn) if _ENABLE_ALL.search(arg.arg) and _default_on(default) is True), None)
    for path, root in classes:
        flag = _class_flag(root)
        if flag is None or _default_on(flag[1]) is not False or root.name not in children:
            continue
        attr = flag[0]
        held_on: Set[str] = set()
        own = frozenset(_held_tools(path, root, out))
        held: Set[str] = set(own)
        frontier = [(p, c, False, own) for p, c in children[root.name]]
        seen: Set[int] = {id(root)}
        while frontier:
            here, cls, parent_on, parent_held = frontier.pop()
            if id(cls) in seen:
                continue
            seen.add(id(cls))
            on = parent_on or _turns_on(cls, attr)
            available = parent_held | set(_held_tools(here, cls, out))
            held |= available
            if on:
                held_on |= available
            frontier += [(p, c, on, available) for p, c in children.get(cls.name, [])]
        gates = sorted(held - held_on)
        if gates:
            key = f'{root.name}.{attr}'
            # A class default is no off switch where an enable-all path defaults ON: production loads every container.
            _add_switch(out, key, repo, path, flag[2], None if enable_all else False, ast.unparse(flag[1])[:120], gates)
            if enable_all:
                out.switches[key]['overridden_by'] = enable_all
            out.flags.add(key)


def _param_defaults(fn: ast.AST) -> List[Tuple[ast.arg, ast.expr]]:
    """``[(parameter, its default)]`` for the parameters of ``fn`` that declare one."""
    args = fn.args  # type: ignore[attr-defined]
    positional = [*args.posonlyargs, *args.args]
    pairs = list(zip(positional[len(positional) - len(args.defaults):], args.defaults))
    return pairs + [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]


def _held_tools(path: Path, cls: ast.ClassDef, out: Collector) -> List[str]:
    """The tools a container class instantiates (``tools=[GetLogs(self)]``): each tool class it calls, resolved in its
    own file first, else when the class name is unique across the scope."""
    held: List[str] = []
    for node in ast.walk(cls):
        if isinstance(node, ast.Call) and (candidates := out.tool_classes.get(_dotted(node.func).split('.')[-1])):
            local = [name for where, name in candidates if where == path]
            held += local or ([candidates[0][1]] if len(candidates) == 1 else [])
    return held


# ---- inputs ---------------------------------------------------------------------------------


def _class_fields(cls: ast.ClassDef) -> List[Tuple[str, str, int]]:
    return [(s.target.id, ast.unparse(s.annotation), s.lineno) for s in cls.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)]


def _classes(repo: Repo, files: List[Path]) -> Dict[str, Tuple[Path, ast.ClassDef]]:
    found: Dict[str, Tuple[Path, ast.ClassDef]] = {}
    for path in files:
        tree = repo.tree(path) if path.suffix == '.py' else None
        for node in ast.walk(tree) if tree is not None else ():
            if isinstance(node, ast.ClassDef):
                found.setdefault(node.name, (path, node))
    return found


def _request_fields(out: Collector, model: str, classes: Dict[str, Tuple[Path, ast.ClassDef]], evidence: str) -> None:
    if model not in classes:
        return
    path, cls = classes[model]
    for field, annotation, line in _class_fields(cls):
        out.add_input(field, classify(field, annotation), path, line, f'{evidence}: {model}.{field}: {annotation}')


def collect_py_inputs(repo: Repo, files: List[Path], payload_files: Set[Path], out: Collector) -> None:
    """Request models and multimodal builders across ``files``; payload keys only where the entry and its callers read them."""
    classes = _classes(repo, [f for f in repo.files if f.suffix == '.py'])
    for path in sorted(f for f in files if f.suffix == '.py'):
        tree = repo.tree(path)
        if tree is None:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = next((d for d in fn.decorator_list if _ROUTE_DECORATOR.search(_dotted(d))), None)
            for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:
                model = (ast.unparse(arg.annotation) if arg.annotation is not None else '').split('[')[0].split('.')[-1]
                if model in classes and (route is not None or (_REQUEST_MODEL.search(model) and path in payload_files)):
                    _request_fields(out, model, classes, f'`{fn.name}` parses its request as')
            names = _payload_names(fn) if path in payload_files else set()
            for node in ast.walk(fn) if names else ():
                key = _payload_key(node, names)
                if key is not None:
                    out.add_input(key, classify(key), path, getattr(node, 'lineno', fn.lineno), f'`{fn.name}` reads `{ast.unparse(node)[:80]}`')
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                part = _content_part(node)
                if part is not None:
                    out.add_input(_enclosing(tree, node) or f'{path.stem} content part', part, path, node.lineno, f'builds a `{ast.unparse(node)[:80]}` part')


_REQUEST_MODEL = re.compile(r'(Request|Input|Payload|Body)$')


def _payload_names(fn: ast.AST) -> Set[str]:
    """The parameters an entry reads its payload off: a payload-named one left untyped or typed as a request."""
    names: Set[str] = set()
    for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]:  # type: ignore[attr-defined]
        model = (ast.unparse(arg.annotation) if arg.annotation is not None else '').split('[')[0].split('.')[-1]
        if arg.arg in _PAYLOAD_PARAMS and (not model or model in _UNTYPED or _REQUEST_MODEL.search(model)):
            names.add(arg.arg)
    return names


def _payload_key(node: ast.AST, params: Set[str]) -> Optional[str]:
    """The key a LEAF read takes off a request parameter (``task['input'].get('x')`` -> ``x``); None for anything else."""
    key: Optional[str] = None
    base: Optional[ast.AST] = None
    if isinstance(node, ast.Subscript):
        index = node.slice if not isinstance(node.slice, ast.Index) else node.slice.value  # type: ignore[attr-defined]
        key, base = _const_str(index), node.value
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
        key, base = _const_str(node.args[0]), node.func.value
    if key is None or base is None:
        return None
    root = base
    while isinstance(root, (ast.Subscript, ast.Call, ast.Attribute)):
        if isinstance(root, ast.Attribute) and root.attr in _TRANSPORT_ATTRS:
            return None
        root = root.value if isinstance(root, (ast.Subscript, ast.Attribute)) else root.func  # type: ignore[union-attr]
    if not (isinstance(root, ast.Name) and root.id in params):
        return None
    return key


def _content_part(node: ast.Dict) -> Optional[str]:
    keys = [_const_str(k) for k in node.keys if k is not None]
    if 'type' not in keys:
        return None
    kind = _PART_TYPES.get(_const_str(node.values[keys.index('type')]) or '')
    if kind is None or not (set(keys) & _PART_PAYLOAD_KEYS):
        return None
    return 'file_by_reference' if 'file_id' in keys else kind


def _enclosing(tree: ast.Module, target: ast.AST) -> Optional[str]:
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(n is target for n in ast.walk(fn)):
            inner = _enclosing_in(fn, target)
            return inner or fn.name
    return None


def _enclosing_in(fn: ast.AST, target: ast.AST) -> Optional[str]:
    for child in ast.iter_child_nodes(fn):
        for node in ast.walk(child):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn and any(n is target for n in ast.walk(node)):
                return node.name
    return None


_TS_ROUTE = re.compile(r'export\s+(?:async\s+)?function\s+(POST|PUT|PATCH)\b|export\s+const\s+(POST|PUT|PATCH)\s*=|\b(?:app|router|server)\.(post|put|patch)\(')
_TS_PARSE = re.compile(r'\b([A-Za-z_]\w*)\s*\.\s*(?:safeParse|parse|safeParseAsync|parseAsync)\(')
_TS_DESTRUCTURED_BODY = re.compile(r'const\s*\{([^}]{1,400})\}\s*=\s*(?:await\s+)?\w+\.json\(\)')
_TS_PART_LITERAL = re.compile(r'type\s*:\s*z\.literal\(\s*[\'"](file|image|image_url|input_image|input_file)[\'"]\s*\)')
_TS_CONTENT_PART = re.compile(r'type\s*:\s*[\'"](image_url|image|input_image|file|input_file)[\'"]\s*,\s*(image_url|image|file|data|url|source|mediaType|mimeType)\s*:')


def _balanced(text: str, start: int, opener: str = '{') -> str:
    """The body of the ``opener`` bracket (``{`` or ``(``) opening at or after ``start``, the rest of ``text`` when it
    never closes. A string or template literal is read whole, so a brace or comma inside one is text."""
    open_at = text.find(opener, start)
    if open_at < 0:
        return ''
    depth = 0
    for i, ch, quoted in _scan(text, open_at):
        if quoted:
            continue
        depth += 1 if ch in '({[' else -1 if ch in ')}]' else 0
        if depth == 0:
            return text[open_at + 1:i]
    return text[open_at + 1:]


def _scan(text: str, start: int = 0) -> Iterator[Tuple[int, str, bool]]:
    """``(index, char, inside a string literal)`` from ``start``; the quotes themselves count as inside. A
    ``'``/``"`` string ends at its line's end (an apostrophe in JSX text never swallows the file); a template spans lines."""
    quote, escaped = '', False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == quote or (ch == '\n' and quote != '`'):
                quote = ''
            yield i, ch, True
        elif ch in '\'"`':
            quote = ch
            yield i, ch, True
        else:
            yield i, ch, False


def _top_level_parts(body: str) -> List[str]:
    """``body`` split at its top-level commas (nested brackets and string literals read whole), blank parts dropped."""
    parts: List[str] = []
    depth, begin = 0, 0
    for i, ch, quoted in _scan(body):
        if quoted:
            continue
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
        elif ch == ',' and depth == 0:
            parts.append(body[begin:i])
            begin = i + 1
    parts.append(body[begin:])
    return [p for p in parts if p.strip()]


_ENTRY_KEY = re.compile(r'\s*["\']?([A-Za-z_][\w-]*)["\']?\s*:')


def _top_level_keys(body: str) -> List[str]:
    return list(dict.fromkeys(m.group(1) for m in map(_ENTRY_KEY.match, _top_level_parts(body)) if m))


def _entry_value(body: str, key: str) -> Optional[str]:
    """The source of ``key``'s value among an object body's top-level entries (a shorthand ``{ key }`` is the reference
    ``key``); None when no entry names it."""
    for part in _top_level_parts(body):
        match = _ENTRY_KEY.match(part)
        if match and match.group(1) == key:
            return part[match.end():].strip()
        if part.strip() == key:
            return key
    return None


def _spreads_nothing(body: str) -> bool:
    """No top-level entry of an object body is a spread (``...base``), which could carry any key."""
    return not any(part.strip().startswith('...') for part in _top_level_parts(body))


def _ts_schema(repo: Repo, name: str, files: List[Path]) -> Optional[Tuple[Path, int, str]]:
    definition = re.compile(rf'\bconst\s+{re.escape(name)}\s*(?::[^=]+)?=\s*z\s*\.\s*object\(')
    for path in files:
        text = repo.text(path)
        match = definition.search(text)
        if match:
            return path, _line_of(text, match.start()), _balanced(text, match.end() - 1)
    return None


def collect_ts_inputs(repo: Repo, files: List[Path], out: Collector) -> None:
    ts = sorted(f for f in files if f.suffix in TS_SUFFIXES)
    near = sorted(forward(repo, ts, hops=1))
    for path in ts:
        text = repo.text(path)
        if _TS_ROUTE.search(text):
            for match in _TS_PARSE.finditer(text):
                found = _ts_schema(repo, match.group(1), [path, *near])
                if found is None:
                    continue
                schema_path, line, body = found
                for key in _top_level_keys(body):
                    out.add_input(key, classify(key), schema_path, line, f'the route parses its request with `{match.group(1)}`')
                for part in _schema_parts(repo, schema_path, body):
                    out.add_input(f'{match.group(1)}[{part}]', _PART_TYPES.get(part, 'file_content_part'), schema_path, line, f'`{match.group(1)}` admits a `{part}` part')
            for match in _TS_DESTRUCTURED_BODY.finditer(text):
                for key in (k.split(':')[0].split('=')[0].strip() for k in match.group(1).split(',')):
                    if re.fullmatch(r'[A-Za-z_]\w*', key):
                        out.add_input(key, classify(key), path, _line_of(text, match.start()), 'the route destructures its JSON body')
            if 'formData()' in text:
                out.add_input(_next_route(repo, path), 'file_by_reference', path, _line_of(text, text.index('formData()')), 'the route reads multipart form data')
        for match in _TS_CONTENT_PART.finditer(text):
            out.add_input(f'{path.stem} {match.group(1)} part', _PART_TYPES[match.group(1)], path, _line_of(text, match.start()), f'builds a `{match.group(1)}` content part')


def _schema_parts(repo: Repo, path: Path, body: str, depth: int = 0) -> List[str]:
    """The content-part literals a schema admits, following the schemas it names in the same file."""
    parts = [m.group(1) for m in _TS_PART_LITERAL.finditer(body)]
    if depth < 3:
        text = repo.text(path)
        for ref in sorted(set(re.findall(r'\b([A-Za-z_]\w*Schema)\b', body))):
            match = re.search(rf'\bconst\s+{re.escape(ref)}\s*=\s*', text)
            if match:
                end = re.search(r'\n(?:export\s+)?const\s', text[match.end():])
                parts += _schema_parts(repo, path, text[match.end():match.end() + end.start() if end else len(text)], depth + 1)
    return sorted(set(parts))


def _next_route(repo: Repo, path: Path) -> str:
    parts = repo.rel(path).split('/')
    if 'app' in parts and path.stem == 'route':
        return '/' + '/'.join(p for p in parts[parts.index('app') + 1:-1] if not p.startswith('('))
    return repo.rel(path)


def collect_upload_routes(repo: Repo, out: Collector) -> None:
    """Upload routes anywhere in the repo: a file the user uploads lands in a store the agent reads."""
    for path in repo.files:
        if 'upload' not in repo.text(path).lower():
            continue
        if path.suffix == '.py':
            tree = repo.tree(path)
            nodes = list(ast.walk(tree)) if tree is not None else []
            prefix = next((_const_str(_kw(n, 'prefix')) or '' for n in nodes if isinstance(n, ast.Call) and _kw(n, 'prefix') is not None), '')
            for fn in nodes:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                takes_file = any(a.annotation is not None and _CONTENT_TYPES.search(ast.unparse(a.annotation)) for a in fn.args.args)
                for deco in fn.decorator_list:
                    literal = _const_str(deco.args[0]) if isinstance(deco, ast.Call) and deco.args and _ROUTE_DECORATOR.search(_dotted(deco)) else None
                    route = f'{prefix}{literal}' if literal is not None else ''
                    if route and (takes_file or 'upload' in route.lower()):
                        out.add_input(route, 'file_by_reference', path, fn.lineno, f'upload route `{fn.name}`')
        elif path.suffix in TS_SUFFIXES and path.stem == 'route' and 'upload' in repo.rel(path).lower() and _TS_ROUTE.search(repo.text(path)):
            out.add_input(_next_route(repo, path), 'file_by_reference', path, 1, 'upload route handler')


_CHAT_SURFACE = re.compile(r'useChat|useAssistant|sendMessage|/api/chat|\bonSend\b|Chat(?:Provider|Input|Panel|Window)\b')
_FRONTEND_UPLOAD = re.compile(r'type\s*=\s*[{]?\s*[\'"]file[\'"]|\bonDrop\s*=|new\s+FileReader\b|readAsDataURL|readAsText\(')
_FRONTEND_INJECT = re.compile(r'role\s*:\s*[\'"](system|assistant)[\'"]|\bsetMessages\(|\bappend\(\s*\{')
#: A mutation call: `fetch` or any fetch wrapper (`fetchWithRetry`, `apiFetch`) with a write method, or an HTTP client's
#: write verb (`axios.post`, `apiClient.patch`). The URL may be a template over a base the module declares.
_FRONTEND_MUTATION = re.compile(r'\b\w*[Ff]etch\w*\(\s*([`\'"][^`\'"]+[`\'"]|[A-Za-z_][\w.]*(?:\s*\+\s*[`\'"][^`\'"]*[`\'"])?)\s*,\s*\{[^}]{0,300}?'
                                r'method\s*:\s*[\'"](POST|PUT|PATCH|DELETE)[\'"]', re.DOTALL)
_CLIENT_MUTATION = re.compile(r'\b(?:axios|ky|http|api|client|\w+(?:Api|Client))\.(post|put|patch|delete)\(\s*[`\'"]([^`\'"]+)[`\'"]')
#: The agent's own turn endpoint, by its last path segment: the user's message, never an action between turns.
_TURN_ENDPOINT = frozenset({'chat', 'message', 'messages', 'stream', 'completions', 'invoke', 'run'})
#: How far the reader follows a chat surface's in-repo imports (its contexts, hooks, service and lib modules) for the
#: calls the UI makes through them.
FRONTEND_IMPORT_HOPS = 2


def _frontend_url(text: str, url: str) -> str:
    """A call's route: a leading ``${base}`` resolved to the string the module declares for it (``const base =
    '/api/x'``, ``private readonly baseUrl = '/api/x'``) or dropped (a host the reader cannot see), any later
    interpolation an ``{id}``, the query dropped."""
    lead = re.match(r'\$\{\s*(?:this\.)?(\w+)\s*\}', url)
    base = ''
    if lead:
        declared = re.search(rf'\b(?:const|let|var|readonly|private|protected|public|static)\s+{lead.group(1)}\s*(?::[^=;\n]+)?=\s*[`\'"]([^`\'"$]*)[`\'"]', text)
        base = declared.group(1).rstrip('/') if declared else ''
    rest = url[lead.end():] if lead else re.sub(r'^\$\{[^}]*\}', '', url)
    return base + re.sub(r'\$\{[^}]*\}', '{id}', rest).split('?')[0]


_JS_STRING_OR_COMMENT = re.compile(r'("(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`)|/\*.*?\*/|//[^\n]*', re.DOTALL)


def _code_only(text: str) -> str:
    """``text`` with its comments blanked (newlines kept, so line numbers hold): a doc example is not a call."""
    return _JS_STRING_OR_COMMENT.sub(lambda m: m.group(1) or re.sub(r'[^\n]', ' ', m.group(0)), text)


def _url_text(expression: str) -> str:
    """A call's URL argument as a template: a literal unquoted; an identifier URL (``this.baseUrl + '/x'``, ``url``)
    as ``${identifier}`` plus its literal tail, so its declared base resolves like a template's."""
    if expression[:1] in '`\'"':
        return expression[1:-1]
    head, _, tail = expression.partition('+')
    return '${' + head.strip() + '}' + tail.strip().strip('`\'"')


def _server_actions(repo: Repo, path: Path) -> List[Tuple[str, int]]:
    """The server actions a module exports (``export const xAction = client….inputSchema(schema)…``): ``[(name, line)]``."""
    text = repo.text(path)
    return [(m.group(1), _line_of(text, m.start())) for m in _SERVER_ACTION.finditer(text)]


_SERVER_ACTION = re.compile(r'export\s+const\s+(\w+)\s*=\s*\w+(?:\s*\.\s*\w+\([^;]{0,400}?\))*?\s*\.\s*inputSchema\(', re.DOTALL)


def _frontend_calls(repo: Repo, path: Path, out: Collector, via: str = '') -> None:
    text = _code_only(repo.text(path))
    for match in _TS_ADDITIONAL_KWARGS.finditer(text):
        for key in _top_level_keys(_balanced(text, match.end() - 1)):
            out.add_input(f'additional_kwargs.{key}', classify(key), path, _line_of(text, match.start()), f'the UI sends `additional_kwargs.{key}`{via}')
    calls = [(m.start(), _url_text(m.group(1)), m.group(2)) for m in _FRONTEND_MUTATION.finditer(text)]
    calls += [(m.start(), m.group(2), m.group(1).upper()) for m in _CLIENT_MUTATION.finditer(text)]
    for offset, url, method in sorted(calls):
        route = _frontend_url(text, url)
        if via and not route.startswith('/'):
            continue  # an imported module's call to another host (analytics, a vendor API) is not the app's own route
        if route.rstrip('/').split('/')[-1] not in _TURN_ENDPOINT:
            out.add_input(route, 'between_turn_action', path, _line_of(text, offset), f'the UI calls {method} {route}{via}')


def collect_frontend(repo: Repo, out: Collector) -> None:
    """File pickers, host-injected messages and mutation calls in the repo's chat-surface frontend files, and the
    mutation calls those files make through the in-repo modules they import (``FRONTEND_IMPORT_HOPS`` deep)."""
    surfaces: List[Path] = []
    for path in repo.files:
        if path.suffix not in FRONTEND_SUFFIXES:
            continue
        text = repo.text(path)
        if not (_CHAT_SURFACE.search(text) or 'chat' in repo.rel(path).lower()):
            continue
        surfaces.append(path)
        if match := _FRONTEND_UPLOAD.search(text):
            out.add_input(f'{path.stem} upload', 'file_content_part', path, _line_of(text, match.start()), 'the UI takes a file from the user')
        if match := _FRONTEND_INJECT.search(text):
            out.add_input(f'{path.stem} injected message', 'host_injected_message', path, _line_of(text, match.start()), 'the UI adds a message to the conversation')
        _frontend_calls(repo, path, out)
    origin = {path: path for path in surfaces}
    frontier = list(surfaces)
    for _ in range(FRONTEND_IMPORT_HOPS):
        reached = sorted({t for f in frontier for t in repo.imports(f) if t not in origin and t.suffix in TS_SUFFIXES + FRONTEND_SUFFIXES},
                         key=str)
        for target in reached:
            origin[target] = next(origin[f] for f in frontier if target in repo.imports(f))
            _frontend_calls(repo, target, out, f' (through a module {repo.rel(origin[target])} imports)')
        frontier = reached
    # A server action the chat surface (or a module it reaches) calls: a click that changes the agent's world between turns.
    calling = {f: repo.text(f) for f in origin}
    for target in origin:
        for name, line in _server_actions(repo, target):
            if any(re.search(rf'\b{name}\b', text) for f, text in calling.items() if f != target):
                out.add_input(name, 'between_turn_action', target, line, 'a server action the chat surface calls')


#: An env var naming who the agent acts as (an inbox owner, an account): an input the deployment hands the agent.
_IDENTITY_ENV = re.compile(r'(?:^|_)(EMAIL|USER(?:NAME)?|ACCOUNT|OWNER|TENANT)(?:_|$)')
_CLI_OPTION = re.compile(r'^--?[A-Za-z][\w-]*$')
#: The kinds that carry content the user hands over (a side channel is only an input when it carries one).
_CONTENT_KINDS = ('image', 'file_content_part', 'file_by_path', 'file_by_reference')
_TS_ADDITIONAL_KWARGS = re.compile(r'\badditional_kwargs\s*:\s*\{')


def collect_entry_inputs(repo: Repo, files: List[Path], out: Collector) -> None:
    """Inputs that ride outside a request model: a message's ``additional_kwargs`` key (an upload channel), a CLI
    option an entry parses (argparse / click / typer), an identity the agent reads from the environment."""
    for path in sorted(f for f in files if f.suffix == '.py'):
        tree = repo.tree(path)
        for node in ast.walk(tree) if tree is not None else ():
            if isinstance(node, (ast.Subscript, ast.Call)):
                base = node.value if isinstance(node, ast.Subscript) else (node.func.value if isinstance(node.func, ast.Attribute) and node.func.attr == 'get' else None)
                index = (node.slice if isinstance(node, ast.Subscript) else (node.args[0] if node.args else None))  # type: ignore[union-attr]
                if base is not None and _dotted(base).endswith('additional_kwargs') and (key := _const_str(index)) and classify(key) in _CONTENT_KINDS:
                    out.add_input(f'additional_kwargs.{key}', classify(key), path, node.lineno, f'reads `{ast.unparse(node)[:80]}` off a message')
            if isinstance(node, ast.Call) and node.args and (flag := _const_str(node.args[0])) and _CLI_OPTION.match(flag) \
                    and _dotted(node.func).split('.')[-1] in ('add_argument', 'option', 'Option') \
                    and (kind := classify(flag.lstrip('-').replace('-', '_'))) in _CONTENT_KINDS + ('text',):
                out.add_input(flag, kind, path, node.lineno, f'a CLI option `{ast.unparse(node)[:80]}`')
            if (env := _env_var(node)) is not None and _IDENTITY_ENV.search(env[0]):
                out.add_input(env[0], 'structured_context', path, getattr(node, 'lineno', 1), 'the agent reads who it acts as from the environment')


_REACH_PARENTS = (ast.If, ast.IfExp, ast.While, ast.Compare, ast.BoolOp, ast.JoinedStr)


def collect_reach(repo: Repo, files: List[Path], out: Collector) -> None:
    """Where an input steers the agent: a read of it (a payload key, an attribute of a request/context object) or the
    local bound from it that lands in a branch test, a comparison or a formatted string (the prompt). Recorded as the
    input's ``reaches``: such an input changes behaviour, so it is never transport, session state or internal."""
    wanted = {e['name'] for e in out.inputs.values() if e['kind'] in ('structured_context', 'text')}
    for path in sorted(f for f in files if f.suffix == '.py'):
        tree = repo.tree(path)
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))) if tree is not None else ():
            parents = {child: parent for parent in ast.walk(fn) for child in ast.iter_child_nodes(parent)}
            bound: Dict[str, str] = {}
            for node in ast.walk(fn):
                for name in (n for n in _read_names(node) if n in wanted):
                    if (where := _steers(node, parents)) is not None:
                        _reach(out, name, f'{repo.rel(path)}:{where[0]} ({where[1]})')
                    parent = parents.get(node)
                    while parent is not None and not isinstance(parent, ast.stmt):
                        parent = parents.get(parent)
                    if isinstance(parent, ast.Assign) and len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
                        bound.setdefault(parent.targets[0].id, name)
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id in bound and isinstance(node.ctx, ast.Load) and (where := _steers(node, parents)):
                    _reach(out, bound[node.id], f'{repo.rel(path)}:{where[0]} ({where[1]} via `{node.id}`)')


#: Objects a request, a run config or a context arrives on: a key read off one is the input's own value.
_REQUEST_BASES = _PAYLOAD_PARAMS | {'context', 'llm_context', 'configurable', 'config', 'input', 'data', 'params', 'kwargs', 'options'}


def _read_names(node: ast.AST) -> List[str]:
    """The input names a read may carry: an env var; a key or attribute read off a request-like object (its chain
    passes a `_REQUEST_BASES` name); and the object read from itself (``context.get(…)`` reads ``context``)."""
    if (env := _env_var(node)) is not None:
        return [env[0]]
    if isinstance(node, ast.Subscript):
        key, base = _const_str(node.slice), node.value
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
        key, base = _const_str(node.args[0]), node.func.value
    elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        key, base = node.attr, node.value
    else:
        return []
    chain: List[str] = []
    root: ast.AST = base
    while isinstance(root, (ast.Subscript, ast.Attribute, ast.Call)):
        if isinstance(root, ast.Attribute):
            chain.append(root.attr)
        root = root.func if isinstance(root, ast.Call) else root.value  # type: ignore[union-attr]
    chain.append(getattr(root, 'id', ''))
    names = [chain[0]] if chain[0] and chain[0] not in ('self', 'cls') else []
    return names + ([key] if key and set(chain) & _REQUEST_BASES else [])


def _steers(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> Optional[Tuple[int, str]]:
    child, parent = node, parents.get(node)
    while parent is not None and not isinstance(parent, ast.stmt):
        if isinstance(parent, _REACH_PARENTS) and not (isinstance(parent, (ast.IfExp, ast.If, ast.While)) and child is not parent.test):
            return parent.lineno, 'the prompt' if isinstance(parent, ast.JoinedStr) else 'a branch'
        child, parent = parent, parents.get(parent)
    if isinstance(parent, (ast.If, ast.While)) and child is parent.test:
        return parent.lineno, 'a branch'
    return None


def _reach(out: Collector, name: str, where: str) -> None:
    for entry in out.inputs.values():
        if entry['name'] == name:
            entry.setdefault('reaches', where)


#: A frontend call that sends an object to the app (a fetch body, a server action, a chat hook).
_SENDS = re.compile(r'\b(?:\w*[Ff]etch\w*|execute\w*|\w+Action|sendMessage|append|mutate\w*|post|put|patch)\s*\(|\bbody\s*:\s*(?=\{)')


def collect_senders(repo: Repo, out: Collector) -> None:
    """An in-repo caller that SENDS an input: a frontend file whose object literal carries the input's key inside a
    call to the app (``fetch(…, {body: {message}})``, ``someAction({message})``). Recorded as ``sent_by``: such an input
    is reachable in production."""
    wanted = {e['name'] for e in out.inputs.values() if re.fullmatch(r'[A-Za-z_]\w*', e['name'])}
    for path in (f for f in repo.files if f.suffix in FRONTEND_SUFFIXES + TS_SUFFIXES):
        text = _code_only(repo.text(path))
        for call in _SENDS.finditer(text):
            args = text[call.end():call.end() + 600]
            for name in wanted:
                if hit := re.search(rf'[{{,]\s*({name})\s*[:,}}]', args):
                    for entry in out.inputs.values():
                        if entry['name'] == name and not entry['source'].startswith(repo.rel(path) + ':'):
                            entry.setdefault('sent_by', f'{repo.rel(path)}:{_line_of(text, call.end() + hit.start(1))}')


_JS_LITERAL = r'(`(?:[^`\\]|\\.)*`|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\')'
#: A message object the UI adds for the USER, and the literal content it carries.
_UI_USER_ROLE = re.compile(r'\brole\s*:\s*(?:["\']user["\']|\w+\.User\b)')
_UI_CONTENT = re.compile(r'\b(?:content|text)\s*:\s*' + _JS_LITERAL)
#: An exact comparison against a string literal in agent-side TS/JS.
_TS_EQUALS = re.compile(r'(?:===?|!==?)\s*' + _JS_LITERAL + r'|' + _JS_LITERAL + r'\s*(?:===?|!==?)')


def _whole_message(literal: str) -> Optional[re.Pattern[str]]:
    """The pattern a UI literal sends, anchored at both ends: a template's ``${…}`` matches any text. None when the fixed
    text holds no letter (`` `${text}` `` or `` `${a}: ${b}` `` would match every message)."""
    body = literal[1:-1]
    parts = re.split(r'\$\{[^}]*\}', body) if literal[0] == '`' else [body]
    if not re.search(r'[^\W\d_]', ''.join(parts)):
        return None
    return re.compile('^' + '.+'.join(re.escape(p) for p in parts) + '$', re.DOTALL)


def _py_exact_literals(tree: ast.Module) -> List[Tuple[int, str]]:
    """``(line, text)`` of each string a comparison matches WHOLE: ``==`` / ``!=`` against a literal, or ``in`` a literal
    tuple, list or set (``"x" in msg`` is a substring test, never one)."""
    found: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for i, op in enumerate(node.ops):
            left, right = operands[i], operands[i + 1]
            if isinstance(op, (ast.Eq, ast.NotEq)):
                found += [(node.lineno, x.value) for x in (left, right) if isinstance(x, ast.Constant) and isinstance(x.value, str)]
            elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
                found += [(node.lineno, e.value) for e in right.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return found


def collect_control_messages(repo: Repo, top: Path) -> List[Dict[str, str]]:
    """``[{text, sent_by, matched_at}]``: each fixed message a UI control posts as the USER's turn that the agent matches
    whole. The UI side reads frontend files (`FRONTEND_SUFFIXES`; an agent's own `.ts` turns are not a control) for a
    ``role: "user"`` object with a literal ``content`` / ``text``; the agent side reads every Python file and every
    non-frontend TS/JS file under ``top`` (never the import scope: a matcher module the entry does not import is still
    the agent's) for an exact comparison against a literal. A template joins each literal it matches."""
    sends: List[Tuple[str, re.Pattern[str]]] = []
    for path in (f for f in repo.files if f.suffix in FRONTEND_SUFFIXES):
        text = _code_only(repo.text(path))
        for role in _UI_USER_ROLE.finditer(text):
            depth, opener = 0, -1
            for i in range(role.start() - 1, -1, -1):
                depth += {'}': 1, '{': -1}.get(text[i], 0)
                if depth < 0:
                    opener = i
                    break
            content = _UI_CONTENT.search(_balanced(text, opener)) if opener >= 0 else None
            pattern = _whole_message(content.group(1)) if content else None
            if content and pattern:
                sends.append((f'{repo.rel(path)}:{_line_of(text, text.index(content.group(0), opener))}', pattern))
    if not sends:
        return []
    matched: List[Tuple[str, str]] = []
    for path in (f for f in repo.files if top in f.parents):
        if path.suffix in PY_SUFFIXES:
            tree = repo.tree(path)
            matched += [(value, f'{repo.rel(path)}:{line}') for line, value in (_py_exact_literals(tree) if tree is not None else [])]
        elif path.suffix in TS_SUFFIXES and path.suffix not in FRONTEND_SUFFIXES:
            text = _code_only(repo.text(path))
            for m in _TS_EQUALS.finditer(text):
                literal = m.group(1) or m.group(2)
                if '${' not in literal:
                    matched.append((literal[1:-1], f'{repo.rel(path)}:{_line_of(text, m.start())}'))
    found = {(value, sent_by, at) for sent_by, pattern in sends for value, at in matched if pattern.match(value)}
    return [{'text': value, 'sent_by': sent_by, 'matched_at': at} for value, sent_by, at in sorted(found)]


def name_tokens(name: str) -> List[str]:
    """``get_calendarEvents`` -> ``['get', 'calendar', 'events']``."""
    return [t.lower() for t in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name)]


def upload_tool_inputs(names: List[str], source: Dict[str, str], how: str = '') -> List[Dict[str, str]]:
    """The tools in ``names`` that read a file or image the user handed over, each an input of its own cited at
    ``source[name]``: a read verb and the object it reads (``view_image``, ``read_file``), or the read verb alone
    (``Read``: a path the user names)."""
    found: List[Dict[str, str]] = []
    for name in names:
        tokens = name_tokens(name)
        kind = next((_READ_OBJECTS[t] for t in tokens if t in _READ_OBJECTS), None)
        kind = kind or ('file_by_path' if len(tokens) == 1 and tokens[0] in _BARE_READERS else None)
        if kind is not None and tokens and tokens[0] in _READ_VERBS:
            found.append({'name': name, 'kind': kind, 'source': source[name], 'evidence': f'tool `{name}`{how} reads what the user hands over'})
    return found


def collect_upload_tools(out: Collector, names: List[str]) -> None:
    """The production tools that read a file or image the user handed over: each is an input of its own."""
    for entry in upload_tool_inputs(names, out.tools):
        out.inputs.setdefault((entry['name'], entry['kind']), entry)


#: A parameter that hands the entry a collaborator (its last name token), never a value the user sends.
_COLLABORATORS = frozenset({'handler', 'manager', 'client', 'service', 'callback', 'factory', 'provider', 'loader', 'store',
                            'session', 'hook', 'fn', 'func', 'cb'})


def declared_symbols(workdir: Path) -> List[Tuple[str, str]]:
    """The declared entry point as ``(module, qualname)`` candidates: the manifest's ``agent.entry_symbol.ref``
    (``module:symbol``) and the config's ``agent.entry_point`` (``module:symbol``, a symbol of ``agent.module``, or a
    dotted path split at each dot). Only a candidate that resolves to a definition counts (`collect_entry_params`)."""
    found: List[Tuple[str, str]] = []
    try:
        ref = (json.loads((workdir / 'bench_manifest.json').read_text(encoding='utf-8'))['agent'].get('entry_symbol') or {}).get('ref')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        ref = None
    if ref and ':' in str(ref):
        module, _, qual = str(ref).partition(':')
        found.append((module.strip(), qual.strip()))
    try:
        config = BlockYaml((workdir / 'plurai.config.yaml').read_text(encoding='utf-8')).root
    except (OSError, UnicodeDecodeError):
        config = None
    agent = config.get('agent') if isinstance(config, dict) else None
    point = agent.get('entry_point') if isinstance(agent, dict) else None
    if isinstance(point, str) and point.strip():
        point = point.strip()
        if ':' in point:
            module, _, qual = point.partition(':')
            found.append((module.strip(), qual.strip()))
        else:
            if isinstance(agent.get('module'), str) and agent['module'].strip():  # type: ignore[union-attr]
                found.append((agent['module'].strip(), point))  # type: ignore[index]
            parts = point.split('.')
            found += [('.'.join(parts[:i]), '.'.join(parts[i:])) for i in range(1, len(parts))]
    return list(dict.fromkeys(found))


def _definition(tree: ast.Module, qual: str) -> Optional[ast.AST]:
    """The function ``qual`` (``Class.method`` or ``function``) names at a module's top level, or None."""
    body: List[ast.stmt] = tree.body
    node: Optional[ast.AST] = None
    for part in qual.split('.'):
        node = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part), None)
        if node is None:
            return None
        body = node.body  # type: ignore[attr-defined]
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def collect_entry_params(repo: Repo, workdir: Path, out: Collector) -> None:
    """The declared entry point's own parameters (``send(self, message: str)``): what the caller hands the agent each
    turn, when no request model or payload object carries it. Each content parameter — text, an image, a file — is an
    input, classified by its name and annotation. A structured parameter (an id, a context object) is session state the
    request-model and payload rules read where it matters, a payload-named one is theirs, and a collaborator (a
    ``file_handler``) is no value the user sends."""
    seen: Set[Tuple[Path, int]] = set()
    for module, qual in declared_symbols(workdir):
        path = repo.resolve_py(module, workdir.resolve()) if module else None
        tree = repo.tree(path) if path is not None else None
        fn = _definition(tree, qual) if tree is not None else None
        if fn is None or (path, fn.lineno) in seen:  # type: ignore[attr-defined]
            continue
        seen.add((path, fn.lineno))  # type: ignore[arg-type, attr-defined]
        args = fn.args  # type: ignore[attr-defined]
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg in ('self', 'cls') or arg.arg in _PAYLOAD_PARAMS or set(name_tokens(arg.arg)[-1:]) & _COLLABORATORS:
                continue
            annotation = ast.unparse(arg.annotation) if arg.annotation is not None else ''
            kind = classify(arg.arg, annotation)
            if kind in ('text', *_CONTENT_KINDS):
                out.add_input(arg.arg, kind, path, arg.lineno, f'the declared entry `{qual}` takes `{arg.arg}{": " + annotation if annotation else ""}`')  # type: ignore[arg-type]


# ---- mode selectors: one agent, several production task types --------------------------------

#: The keyword a host registration names the task type it serves with (a provider, a handler registered per type).
_MODE_KWARGS = frozenset({'task_type', 'task_types', 'interaction_type', 'modality'})
#: An enum of the modes a host offers.
_MODE_ENUM = re.compile(r'(TaskType|Modality|InteractionType)s?$')
_ENUM_BASES = frozenset({'Enum', 'StrEnum'})
#: `selects` key for what a selector's branch reaches whatever value it took (a `!=` / `not in` test).
SELECTOR_OTHER = '*'


def _mode_strings(node: Optional[ast.AST], consts: Dict[str, Set[str]]) -> List[str]:
    """The string values ``node`` spells: a literal, a sequence of them, or a constant naming one (``X``, ``Enum.X``)."""
    if node is None:
        return []
    if (value := _const_str(node)) is not None:
        return [value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [v for e in node.elts for v in _mode_strings(e, consts)]
    known = consts.get(_dotted(node)) if isinstance(node, (ast.Name, ast.Attribute)) else None
    return sorted(known) if known and len(known) == 1 else []


_MODE_TEXT = re.compile(r'\b(task_types?|interaction_type|modality)\s*=|Enum\b')


def production_modes(repo: Repo, near: Set[Path]) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """``({value: source}, constants)``: every task type a host registration (``task_type='…'``) or a mode enum offers,
    repo-wide, and the string constants (``NAME`` and ``Enum.MEMBER``) of those files and ``near`` that a selector may
    compare against."""
    hosts = [p for p in repo.files if p.suffix == '.py' and _MODE_TEXT.search(repo.text(p))]
    trees = [(p, t) for p in sorted(set(hosts) | {f for f in near if f.suffix == '.py'}) if (t := repo.tree(p)) is not None]
    consts: Dict[str, Set[str]] = {}
    for _path, tree in trees:
        for name, value in _module_strings(tree).items():
            consts.setdefault(name, set()).add(value)
    modes: Dict[str, str] = {}
    for path, tree in (pt for pt in trees if pt[0] in hosts):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in _MODE_KWARGS:
                        for value in _mode_strings(kw.value, consts):
                            modes.setdefault(value, f'{repo.rel(path)}:{node.lineno}')
            elif isinstance(node, ast.ClassDef) and _MODE_ENUM.search(node.name) and {_dotted(b).split('.')[-1] for b in node.bases} & _ENUM_BASES:
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and (value := _const_str(stmt.value)):
                        modes.setdefault(value, f'{repo.rel(path)}:{stmt.lineno}')
                        consts.setdefault(f'{node.name}.{stmt.targets[0].id}', set()).add(value)
    return modes, consts


def _assigned_keys(fn: ast.AST, names: Set[str]) -> Dict[str, str]:
    """``{local: payload key}`` for each local an entry binds straight from a payload read (``x = task['input'].get('x')``)."""
    bound: Dict[str, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = next((k for sub in ast.walk(node.value) if (k := _payload_key(sub, names)) is not None), None)
            if key is not None:
                bound.setdefault(node.targets[0].id, key)
    return bound


def collect_selectors(repo: Repo, payload_files: Set[Path], out: Collector) -> None:
    """A payload key an entry compares against the task types its host registers (``task.get('type') == MULTIMODAL``)
    is a MODE SELECTOR: a capability switch whose production default is EVERY value the host offers. Each branch the
    comparison gates (an ``if`` testing it, directly or through the local it is bound to) ``selects`` the payload keys
    that test reads and the functions its body calls — the inputs reachable only on that task type."""
    modes, consts = production_modes(repo, forward(repo, sorted(payload_files), hops=1))
    if len(modes) < 2:
        return
    for path in sorted(f for f in payload_files if f.suffix == '.py'):
        tree = repo.tree(path)
        for fn in ast.walk(tree) if tree is not None else ():
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not (names := _payload_names(fn)):
                continue
            bound = _assigned_keys(fn, names)
            for compare in (n for n in ast.walk(fn) if isinstance(n, ast.Compare) and len(n.ops) == 1):
                sides = (compare.left, compare.comparators[0])
                key = next((k for side in sides if (k := _payload_key(side, names) or bound.get(getattr(side, 'id', ''))) is not None), None)
                values = [v for side in sides for v in _mode_strings(side, consts) if v in modes]
                if key is None or not values:
                    continue
                positive = isinstance(compare.ops[0], (ast.Eq, ast.In, ast.Is))
                switch = f'selector:{key}'
                _add_switch(out, switch, repo, path, compare.lineno, True, ' | '.join(sorted(modes)), [])
                entry = out.switches[switch]
                entry.update(kind='selector', field=key, values=[{'value': v, 'source': modes[v]} for v in sorted(modes)])
                selects: Dict[str, List[str]] = entry.setdefault('selects', {})
                for value in values if positive else [SELECTOR_OTHER]:
                    selects[value] = sorted(set(selects.get(value, [])) | _gated(fn, compare, names, bound) - {key})


def _gated(fn: ast.AST, compare: ast.Compare, names: Set[str], bound: Dict[str, str]) -> Set[str]:
    """What a branch on ``compare`` reaches: the payload keys its test reads (directly or through a bound local) and
    the functions its body calls."""
    flags = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign) and n.value is compare for t in n.targets if isinstance(t, ast.Name)}
    reached: Set[str] = set()
    for branch in (n for n in ast.walk(fn) if isinstance(n, (ast.If, ast.IfExp))):
        test = list(ast.walk(branch.test))
        if not any(n is compare or (isinstance(n, ast.Name) and n.id in flags) for n in test):
            continue
        reached |= {k for n in test if (k := _payload_key(n, names) or (bound.get(n.id) if isinstance(n, ast.Name) else None))}
        body = branch.body if isinstance(branch.body, list) else [branch.body]
        reached |= {_dotted(n.func).split('.')[-1] for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Call)}
    return reached


# --------------------------------------------------------------------------------------------
# error replies: the fixed text the agent's own error paths hand the user
# --------------------------------------------------------------------------------------------

#: Shorter than this (or one unbroken token: a key, a URL, an env name) is no reply sentence — matching it would hit
#: legitimate replies.
ERROR_REPLY_MIN_CHARS = 12
#: A prefix (a template's fixed head) shorter than a sentence would open legitimate replies too.
ERROR_REPLY_MIN_PREFIX_CHARS = 25
#: The most an agent declares, highest confidence first; the rest are counted (`error_replies_dropped`), never listed.
ERROR_REPLY_CAP = 20
#: The name tokens, anywhere in a dotted call name, of a call whose text is a log line, never the reply (`logger.error`,
#: `_LOGGER.error`, `logging.error`, `framework.log_error`).
_LOG_TOKENS = frozenset({'log', 'logger', 'logging'})
#: The name tokens of a variable that holds an error path's text (`error_text = '…'`).
_ERROR_TOKENS = frozenset({'error', 'err', 'fail', 'failed', 'failure', 'fallback'})
#: The name tokens of a call that hands text to the user (`bot.send_message(…)`, `response.async_set_error(…)`).
_SEND_TOKENS = frozenset({'send', 'reply', 'respond', 'say', 'speak', 'answer', 'emit', 'publish', 'error'})
#: The key of a returned dict, or the keyword of a sending call, that carries the reply (`{"response": …}`, `text=…`).
_REPLY_KEYS = frozenset({'response', 'reply', 'answer', 'speech', 'text', 'content', 'output', 'message', 'msg'})
#: Catch-all handlers: handing their message on says nothing about any one exception class's.
_CATCH_ALL = frozenset({'Exception', 'BaseException'})


def _reply_text(node: ast.AST, consts: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """``(text, match)`` when ``node`` is reply text: a literal or a module constant (``exact``), or a template's fixed
    head — an f-string, ``'…'.format(…)``, ``'…' % …``, ``'…' + …`` (``prefix``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, 'exact'
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id], 'exact'
    if isinstance(node, ast.JoinedStr):
        head = ''
        for part in node.values:
            fixed = _const_str(part) if isinstance(part, ast.Constant) else (
                consts.get(part.value.id) if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) and part.conversion == -1
                and part.format_spec is None else None)
            if fixed is None:
                return head, 'prefix'
            head += fixed
        return head, 'exact'
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'format' and _const_str(node.func.value) is not None:
        head, brace, _ = str(_const_str(node.func.value)).partition('{')
        return head, 'prefix' if brace else 'exact'
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        left = _reply_text(node.left, consts)
        return (left[0].partition('%')[0] if isinstance(node.op, ast.Mod) else left[0], 'prefix') if left else None
    return None


def _sends(callee: str) -> bool:
    """A call that hands text on: a function or method (a class constructor builds, an exception included) whose name
    sends (`_SEND_TOKENS`), never a log call."""
    name = callee.split('.')[-1]
    tokens = name_tokens(name)
    # A method named exactly `error` is a logger's or a progress tracker's (`tracker.error(STAGE, '…', id)`), never a
    # chat reply; a sender says more (`async_set_error`).
    return bool(name) and not name[0].isupper() and tokens != ['error'] and not _LOG_TOKENS & set(name_tokens(callee)) and bool(_SEND_TOKENS & set(tokens))


def _sinks(stmts: List[ast.stmt], entry: bool) -> List[ast.expr]:
    """What ``stmts`` hand the user: a returned or yielded dict's reply keys, a bare returned or yielded value only in the
    agent's entry module (elsewhere it feeds a caller, or the model), and a sending call's positional and reply-keyword
    arguments."""
    found: List[ast.expr] = []
    for node in (n for stmt in stmts for n in ast.walk(stmt)):
        if isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:
            value = node.value
            found += [v for k, v in zip(value.keys, value.values) if _const_str(k) in _REPLY_KEYS] if isinstance(value, ast.Dict) else [value] if entry else []
        elif isinstance(node, ast.Call) and _sends(_dotted(node.func)):
            found += [*node.args, *(k.value for k in node.keywords if k.arg in _REPLY_KEYS)]
    return found


def _handler_body_ids(fn: ast.AST) -> Set[int]:
    return {id(n) for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler) for stmt in h.body for n in ast.walk(stmt)}


def reply_bound_exceptions(trees: List[Tuple[ast.Module, bool]]) -> Set[str]:
    """The exception classes whose MESSAGE becomes the reply: an ``except C as e`` (never a catch-all) whose body hands
    ``e`` to the user (``str(e)``, ``f"{e}"``, ``e``). ``trees`` pairs each module with whether it is the entry."""
    bound: Set[str] = set()
    for tree, entry in trees:
        for handler in (h for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and h.name):
            caught = handler.type
            if caught is not None and any(isinstance(n, ast.Name) and n.id == handler.name for sink in _sinks(handler.body, entry) for n in ast.walk(sink)):
                bound |= {_dotted(t).split('.')[-1] for t in (caught.elts if isinstance(caught, ast.Tuple) else [caught])} - _CATCH_ALL
    return bound


def _py_error_replies(tree: ast.Module, bound: Set[str], entry: bool, near: bool) -> List[Tuple[int, int, Tuple[str, str]]]:
    """``(tier, line, (text, match))`` for the text an error path hands the user: a literal returned, yielded or sent from
    an ``except`` body (tier 0); a variable assigned there, or an error-named one (tier 1 outside an ``except``), that
    reaches such a sink; a ``raise`` message of a reply-bound exception, in the entry's own package only (tier 0;
    elsewhere, where it surfaces is not readable). A variable the code appends to (``text += …``) holds a ``prefix``.
    A tool's own body is the model's input, never the user's reply: skipped."""
    consts = _module_strings(tree)
    hits: List[Tuple[int, int, Tuple[str, str]]] = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if any(_dotted(d).split('.')[-1] in _TOOL_DECORATORS for d in fn.decorator_list):
            continue
        in_except = _handler_body_ids(fn)
        appended: Set[str] = set()
        assigned: Dict[str, List[Tuple[ast.expr, bool]]] = {}
        raised: List[ast.expr] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name):
                appended.add(node.target.id)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                assigned.setdefault(node.targets[0].id, []).append((node.value, id(node) in in_except))
            elif near and isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args \
                    and _dotted(node.exc.func).split('.')[-1] in bound:
                raised.append(node.exc.args[0])

        def through(expr: ast.expr, bound_raise: bool) -> List[Tuple[int, int, Tuple[str, str]]]:
            direct = _reply_text(expr, consts)
            if direct is not None:
                return [(0, getattr(expr, 'lineno', 0), direct)] if bound_raise or id(expr) in in_except else []
            if not isinstance(expr, ast.Name):
                return []
            name = expr.id
            out = []
            for value, caught in assigned.get(name, []):
                reply = _reply_text(value, consts)
                if reply is not None and (bound_raise or caught or _ERROR_TOKENS & set(name_tokens(name))):
                    out.append((0 if bound_raise or caught else 1, value.lineno, (reply[0], 'prefix') if name in appended else reply))
            return out

        hits += [hit for expr in _sinks(fn.body, entry) for hit in through(expr, False)]
        hits += [hit for expr in raised for hit in through(expr, True)]
    return hits


def collect_error_replies(repo: Repo, files: List[Path], entry: List[Path]) -> Tuple[List[Dict[str, str]], int]:
    """``(error_replies[], dropped)``: the fixed reply text the agent's error paths hand the user (each ``{text, match,
    cite}``), whitespace-normalised and capped at `ERROR_REPLY_CAP` — the entry's own package first, then the most
    direct path — with ``dropped`` counting the rest. Static Python only (AST): a reply path it cannot read is
    dropped, never guessed."""
    trees = [(path, tree) for path in files if path.suffix in PY_SUFFIXES for tree in [repo.tree(path)] if tree is not None]
    bound = reply_bound_exceptions([(tree, path in entry) for path, tree in trees])
    near = {e.parent for e in entry}
    ranked: List[Tuple[int, int, int, int, str, str, str]] = []
    for order, (path, tree) in enumerate(trees):
        for tier, line, (raw, match) in _py_error_replies(tree, bound, path in entry, path.parent in near):
            text = ' '.join(raw.split())
            if len(text) >= (ERROR_REPLY_MIN_PREFIX_CHARS if match == 'prefix' else ERROR_REPLY_MIN_CHARS) and ' ' in text:
                ranked.append((path.parent not in near, tier, order, line, text, match, f'{repo.rel(path)}:{line}'))
    found: Dict[Tuple[str, str], Dict[str, str]] = {}
    for *_, text, match, cite in sorted(ranked):
        found.setdefault((text, match), {'text': text, 'match': match, 'cite': cite})
    replies = list(found.values())
    return replies[:ERROR_REPLY_CAP], max(0, len(replies) - ERROR_REPLY_CAP)


# --------------------------------------------------------------------------------------------
# the inventory
# --------------------------------------------------------------------------------------------


def extract(workdir: Path, entry: Optional[str] = None) -> Dict[str, Any]:
    """The inventory of the agent beside ``workdir``, or ``{'unavailable': why}`` when no repository resolves.

    The scope starts at the declared entry (``entry``, else `declared_entry`), so it never depends on what the harness
    happens to import; only an undeclared entry falls back to the harness's imports (`WARN_ENTRY_UNRESOLVED`).
    ``root_found_by`` says how the repository was found; ``warnings`` (each ``{code, detail}``, also printed on stderr)
    carries `WARN_NO_GIT_ROOT` when no git work tree was found and no input was read, and `WARN_LOADER_NOT_FOUND` when
    no tool-assembly site was found (``loader: null`` — never a synthesized cite)."""
    root, found_by = find_root(workdir)
    if root is None:
        return {'schema': SCHEMA_ID, 'unavailable': 'no git work tree or project root holds this workdir'}
    repo = Repo(root, source_files(root))
    entry_module = entry or declared_entry(workdir)
    warnings: List[Dict[str, str]] = []
    started = sorted(set(entry_seeds(repo, workdir, entry_module)) | _bridge_modules(repo, workdir.resolve()))
    if not started:
        started = seeds(repo, workdir.resolve())
        warnings.append({'code': WARN_ENTRY_UNRESOLVED, 'detail': (
            f'the declared entry {entry_module!r} resolves to no file of {root}' if entry_module else 'no entry is declared (plurai.config.yaml agent.module)')
            + '; the tool scope falls back to what the harness imports. Declare the entry module (`extract --entry`).'})
    top = agent_root(root, entry_seeds(repo, workdir, entry_module))
    started = [f for f in started if top in f.parents] or started
    module = workdir.resolve().parent
    scope = forward(repo, started) if started else {f for f in repo.files if module in f.parents}
    out = Collector(repo)
    collect_tools(repo, sorted(scope), out)
    collect_assembled_tools(repo, sorted(scope), out, Installed(site_packages(root, workdir)))
    collect_ts_injected_tools(repo, sorted(scope), out)
    knowledge = collect_bundles(repo, out, declared_bundle(workdir))
    collect_yaml_tools(repo, sorted(scope), out)
    collect_switches(repo, sorted(scope), out)
    collect_flag_switches(repo, sorted(scope), out)
    collect_ts_switches(repo, sorted(scope), out)
    collect_class_defaults(repo, sorted(scope), out)
    # Callers of the AGENT's entry only — a seed that reaches the tools (a bridge-replaced tool module is not one). A
    # seed that reaches no tool (a logger, a db client) is imported by every route of the product.
    tool_files = {src.rpartition(':')[0] for src in out.tools.values()}
    replaced = _bridge_modules(repo, workdir.resolve())
    entry_files = [s for s in started if s not in replaced and any(repo.rel(f) in tool_files for f in forward(repo, [s]))] or started
    # Inputs are also read around what the harness drives (inside the agent's tree): the routes and callers it stands in for.
    driven = sorted(set(entry_files) | {f for f in seeds(repo, workdir.resolve()) if top in f.parents})
    above = callers(repo, driven) if started else set()
    input_scope = (forward(repo, sorted(set(started) | set(driven)), hops=1) | above) if started else scope
    payload_scope = (set(entry_files) | above) if started else scope
    off = {n for s in out.switches.values() if s['default_on'] is False for n in s['gates']}
    names = sorted(n for n in out.tools if n not in off)
    collect_py_inputs(repo, sorted(input_scope), payload_scope, out)
    collect_entry_params(repo, workdir, out)
    collect_ts_inputs(repo, sorted(input_scope), out)
    collect_upload_routes(repo, out)
    collect_frontend(repo, out)
    collect_entry_inputs(repo, sorted(input_scope | scope), out)
    collect_upload_tools(out, names)
    collect_selectors(repo, payload_scope, out)
    collect_reach(repo, sorted(input_scope | scope), out)
    collect_senders(repo, out)
    control_messages = collect_control_messages(repo, top)
    error_replies, error_replies_dropped = collect_error_replies(repo, sorted(f for f in input_scope if top in f.parents), entry_files)
    found = {e['name'] for e in out.inputs.values()}
    for switch in (s for s in out.switches.values() if s.get('kind') == 'selector'):
        switch['selects'] = {value: [n for n in reached if n in found] for value, reached in sorted(switch['selects'].items())}
    # Where the set is assembled: the switch that gates the most tools (a directory loader over an env-gated one). An
    # enable flag gates tools without assembling them.
    widest = max((s for k, s in out.switches.items() if s['gates'] and k not in out.flags), key=lambda s: len(s['gates']), default=None)
    site = max(((n, s) for s, n in out.sites.items() if n), default=None)
    loader = widest['source'] if widest else (site[1] if site else None)
    if loader is None:
        warnings.append({'code': WARN_LOADER_NOT_FOUND, 'detail': 'no site that assembles the tool set (a loader, a gated block) was found; '
                         '`loader` is null — cite it by hand only from a line that holds the tool list'})
    if found_by != ROOT_BY_GIT and not out.inputs:
        warnings.append({'code': WARN_NO_GIT_ROOT, 'detail': (
            f'no git work tree holds {workdir}; read {root} (the outermost project directory around the agent) and found NO '
            'input. The routes and the frontend that hand the agent its inputs may sit outside it: run the scan from '
            'inside the agent\'s git checkout, or decide every input by hand.')})
    for warning in warnings:
        print(f'agent_inventory: WARNING [{warning["code"]}] {warning["detail"]}', file=sys.stderr)
    return {
        'schema': SCHEMA_ID,
        'root': str(root),
        'root_found_by': found_by,
        'warnings': warnings,
        'entry': entry_module,
        'agent_root': repo.rel(top) if top != root else '.',
        'bench_overlay': bench_overlay(root, found_by, top, workdir),
        'seeds': [repo.rel(s) for s in started],
        'inputs': sorted(out.inputs.values(), key=_input_order),
        'production_tools': {
            'loader': loader,
            'count': len(names),
            'names': names,
            'static': names,
            'switches': sorted(out.switches.values(), key=lambda s: s['switch']),
            'dynamic': sorted(out.dynamic.values(), key=lambda d: d['source']),
            'backends': sorted(out.backends.values(), key=lambda b: str(b['class'])),
            'pauses': sorted(out.pauses),
        },
        'knowledge': knowledge,
        'error_replies': error_replies,
        'error_replies_dropped': error_replies_dropped,
        'control_messages': control_messages,
    }


# --------------------------------------------------------------------------------------------
# the production set a bench accounts for — ONE reading, for the validator and the map alike
# --------------------------------------------------------------------------------------------


def map_tool_names(topology: Dict[str, Any]) -> Set[str]:
    """The tools a topology map carries: its tools and subagents by name, and the call that reaches each subagent."""
    on_map = {str(t.get('name')) for bank in ('tools', 'subagents') for t in topology.get(bank) or [] if isinstance(t, dict)}
    return on_map | {str(t.get('call')) for t in topology.get('subagents') or [] if isinstance(t, dict) and t.get('call')}


def overridden_tools(production: Dict[str, Any], overrides: Optional[List[Any]]) -> Set[str]:
    """The tools an operator-answered `harness.capability_overrides` entry switches off: its switch's gates, plus any it names."""
    gates = {s.get('switch'): s.get('gates') or [] for s in production.get('switches') or [] if isinstance(s, dict)}
    return {str(t) for o in overrides or [] if isinstance(o, dict) and str(o.get('answer_id') or '').strip()
            for t in [*gates.get(o.get('switch'), []), *(o.get('tools') or [])]}


def not_loaded_tools(production: Dict[str, Any]) -> Set[str]:
    """The tools a cited `surfaces.production_tools.not_loaded` group says the production default does not load."""
    return {str(n) for group in production.get('not_loaded') or [] if isinstance(group, dict) for n in group.get('names') or []}


def production_coverage(production: Dict[str, Any], present: Set[str], overrides: Optional[List[Any]] = None) -> Dict[str, Any]:
    """How much of the production set (C3) ``present`` (a map's tools, a live tool set) accounts for: ``{total,
    carried, dropped, exempt}``. ``dropped`` are the production tools ``present`` lacks that neither an operator's
    override nor a cited ``not_loaded`` group accounts for; ``carried = total - len(dropped)`` — the "N of M" every
    reader prints; ``exempt`` are the absent tools an override or a citation accounts for."""
    names = [str(n) for n in production.get('names') or []]
    exempt = overridden_tools(production, overrides) | not_loaded_tools(production)
    absent = set(names) - present
    dropped = sorted(absent - exempt)
    return {'total': len(names), 'carried': len(names) - len(dropped), 'dropped': dropped, 'exempt': sorted(absent & exempt)}


def inventory_path(workdir: Path) -> Path:
    return workdir / 'artifacts' / INVENTORY_FILENAME


#: What the setup smoke records: the agent's tool list built with every switch at its production default.
LIVE_SCHEMA_ID = 'plurai-live-tools/1'


def _input_order(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    return str(entry['source']).rpartition(':')[0], str(entry['name']), str(entry['kind'])


def _static_names(production: Dict[str, Any]) -> List[str]:
    """The tools the static read found (an empty `static` is a reading too, never the merged `names`)."""
    return list(production['static'] if 'static' in production else production.get('names') or [])


def _live_inputs(production: Dict[str, Any]) -> List[Dict[str, str]]:
    """The inputs a live enumeration adds: the upload-tool rule over the tools only the live list names (a built-in
    the SDK supplies, never defined in the source), cited at the call that built the list."""
    if 'live' not in production:
        return []
    only_live = sorted(set(production['live']) - set(_static_names(production)))
    by = str(production.get('live_enumerated_by') or '')
    return upload_tool_inputs(only_live, {name: by for name in only_live}, ' (enumerated live)')


def _static_inputs(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The inventory's inputs without those its live enumeration added."""
    derived = _live_inputs(inventory.get('production_tools') or {})
    return [e for e in inventory.get('inputs') or [] if e not in derived]


def digest(inventory: Dict[str, Any]) -> str:
    """The hash of the source's STATIC reading: the same before and after a live enumeration is merged in (its tools
    and the inputs they add), and on any machine (the absolute root is left out). The manifest records it; the
    validator, the map and lint_prd read the one artifact it names. `error_replies` is left out: it feeds the config
    (`record_error_replies.py`), never the tool and input reading the manifest copied. So is `control_messages`: it feeds
    `scenario_variables.yaml` (verify's advisory)."""
    body = {k: v for k, v in inventory.items() if k not in ('root', 'error_replies', 'error_replies_dropped', 'control_messages')}
    if 'inputs' in body:
        body['inputs'] = _static_inputs(inventory)
    production = dict(body.get('production_tools') or {})
    static = production.pop('static', production.get('names')) or []
    for key in ('live', 'live_enumerated_by'):
        production.pop(key, None)
    production.update(names=static, count=len(static))
    body['production_tools'] = production
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode('utf-8')).hexdigest()


def with_live(inventory: Dict[str, Any], live: List[str], enumerated_by: str) -> Dict[str, Any]:
    """C3 = static ∪ live: the inventory with the live enumeration merged in (``live``, ``live_enumerated_by``), and
    the input rules that read tool names re-run over the tools only the live list names (a built-in `Read` reads a
    path the user hands over). A previous enumeration's inputs are replaced, never stacked."""
    static_inputs = _static_inputs(inventory)
    production = dict(inventory['production_tools'])
    static = _static_names(production)
    names = sorted(set(static) | set(live))
    production.update(static=static, live=sorted(set(live)), live_enumerated_by=enumerated_by, names=names, count=len(names))
    have = {(e['name'], e['kind']) for e in static_inputs}
    added = [e for e in _live_inputs(production) if (e['name'], e['kind']) not in have]
    merged = {**inventory, 'production_tools': production}
    if 'inputs' in inventory or added:
        merged['inputs'] = sorted(static_inputs + added, key=_input_order)
    return merged


def read_artifact(workdir: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(inventory_path(workdir).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def current(workdir: Path, fresh: Dict[str, Any]) -> Dict[str, Any]:
    """The ONE inventory: the artifact on disk when it is the source's current reading (its live enumeration kept),
    else ``fresh``."""
    stored = read_artifact(workdir)
    return stored if stored is not None and 'production_tools' in stored and digest(stored) == digest(fresh) else fresh


def _live_names(raw: Any) -> List[str]:
    """A live tool list: a JSON array of names, or of ``{name}`` objects (a toolset/tool dump)."""
    items = raw.get('tools') if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError('expected a JSON array of tool names (or of objects with a `name`)')
    names = [str(i.get('name') if isinstance(i, dict) else i) for i in items]
    if not all(n and n != 'None' for n in names):
        raise ValueError('every live tool needs a name')
    return names


def _resolve_workdir(given: Optional[str]) -> Path:
    if given:
        return Path(given).resolve()
    here = Path.cwd()
    return here / 'plurai_sim' if (here / 'plurai_sim').is_dir() else here


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read the agent's inputs (C2) and production tools (C3) off its source.")
    sub = parser.add_subparsers(dest='cmd', required=True)
    ex = sub.add_parser('extract', help='write artifacts/agent_inventory.json and print a summary')
    ex.add_argument('--workdir', help='the plurai_sim workdir (default: ./plurai_sim, else .)')
    ex.add_argument('--out', help=f'where to write (default: <workdir>/artifacts/{INVENTORY_FILENAME})')
    ex.add_argument('--entry', help='the agent\'s entry module (dotted, or a path for a TS agent) when plurai.config.yaml does not name it yet')
    live = sub.add_parser('record-live', help='merge the tool list the smoke enumerated at production defaults into the inventory (C3 = static ∪ live)')
    live.add_argument('--workdir', help='the plurai_sim workdir (default: ./plurai_sim, else .)')
    live.add_argument('--from', dest='source', required=True, help='a JSON array of tool names (or of objects with `name`); - for stdin')
    live.add_argument('--enumerated-by', required=True, help='<path>:<line> of the call that built the list (e.g. the agent\'s get_tools)')
    args = parser.parse_args(argv)
    workdir = _resolve_workdir(args.workdir)
    if args.cmd == 'record-live':
        return _record_live(workdir, args.source, args.enumerated_by)
    inventory = extract(workdir, args.entry)
    if (kept := current(workdir, inventory)) is not inventory and 'live' in kept['production_tools']:
        inventory = with_live(inventory, kept['production_tools']['live'], kept['production_tools'].get('live_enumerated_by', ''))
    target = Path(args.out) if args.out else inventory_path(workdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inventory, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if 'unavailable' in inventory:
        print(f'agent_inventory: NOT READ — {inventory["unavailable"]}; wrote {target}')
        return 2
    tools = inventory['production_tools']
    print(f'agent_inventory: {len(inventory["inputs"])} input(s), {tools["count"]} production tool(s), '
          f'{len(tools["switches"])} capability switch(es) from {len(inventory["seeds"])} seed file(s); wrote {target}')
    for entry in inventory['inputs']:
        print(f'  input  {entry["kind"]:<22} {entry["name"]}  ({entry["source"]})')
    for switch in tools['switches']:
        if switch.get('kind') == 'selector':
            print(f'  switch {switch["switch"]} at {switch["source"]}: selects between {len(switch["values"])} production task type(s) '
                  f'({", ".join(v["value"] for v in switch["values"])}); cover each')
            continue
        state ={True: 'on', False: 'OFF', None: 'unresolved (counted on)'}[switch['default_on']]
        print(f'  switch {switch["switch"]} at {switch["source"]}: default {state}, gates {len(switch["gates"])} tool(s)')
    for message in inventory['control_messages']:
        print(f'  control message {message["text"]!r}: sent by {message["sent_by"]}, matched whole at {message["matched_at"]}')
    return 0


def _record_live(workdir: Path, source: str, enumerated_by: str) -> int:
    try:
        raw = json.loads(sys.stdin.read() if source == '-' else Path(source).read_text(encoding='utf-8'))
        live = _live_names(raw)
    except (OSError, ValueError) as error:
        print(f'agent_inventory: record-live refused: {error}', file=sys.stderr)
        return 2
    fresh = extract(workdir)
    if 'unavailable' in fresh:
        print(f'agent_inventory: NOT READ — {fresh["unavailable"]}', file=sys.stderr)
        return 2
    merged = with_live(current(workdir, fresh), live, enumerated_by)
    inventory_path(workdir).parent.mkdir(parents=True, exist_ok=True)
    inventory_path(workdir).write_text(json.dumps(merged, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    production = merged['production_tools']
    blind = sorted(set(production['live']) - set(production['static']))
    print(f'agent_inventory: {len(production["live"])} live tool(s) recorded; C3 = {production["count"]} '
          f'({len(blind)} the static read missed{": " + ", ".join(blind[:12]) if blind else ""})')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
