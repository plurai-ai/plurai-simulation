"""Blocking gate: a generated `code` / `cli_http` ``simulate.py`` must give the
nested agent-under-test (a) a working directory with NO harness files adjacent and
(b) NO injected env value whose path resolves inside the customer harness dir.

Why: for the Bash-bearing ``code`` / ``cli_http`` channels the nested agent runs
real shell commands. Two distinct leaks let it discover the customer's mock data:

  1. *cwd adjacency* — if its ``cwd`` is (or neighbours) the harness dir, it can
     ``cat simulation_env/mocks.py`` / ``code_surface.yaml`` directly. Closed by
     :func:`assert_opaque_cwd` (the cwd-isolation fix).
  2. *env-var path pivot* — the ``code`` / ``cli_http`` benches inject
     ``PLURAI_CODE_SURFACE`` / ``PYTHONPATH`` / ``NODE_OPTIONS=--import`` whose
     values, when rooted in the customer harness dir, let the agent run
     ``cat "$(dirname "$PLURAI_CODE_SURFACE")"/simulation_env/mocks.py`` and read
     the customer's hardcoded mock answers. Closed by :func:`assert_opaque_env`
     (the path-relocation fix): every such value must resolve to a clean shim dir
     or the installed ``plurai`` package, never under ``plurai_sim/``.

HONEST SCOPE — this gate closes the CUSTOMER-mocks.py pivot ONLY. It does NOT (and
cannot, in-process) close generic-plurai detection: after relocation the agent can
still ``env``-dump and ``cat`` the shim's ``sitecustomize.py`` / the installed
``plurai`` runtime (``fetch_shadow.mjs`` / ``code_hook.cjs``) / the surface YAML, or
dump a grandchild's ``os.environ`` / ``/proc/<pid>/environ``, or hit the localhost
mock URL — those learn "this is a plurai sim" but NOT the customer's specific mock
data, and are explicit Option-D residuals (out-of-trust-domain runner). This gate
asserts the customer-data pivot is closed, nothing more.

Standalone use:  ``python verify_harness_opacity.py path/to/simulate.py``
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

# Filenames (and dir names) that betray the harness if reachable from the agent's
# cwd. The harness lives in ``plurai_sim/``; none of these belongs in the clean
# production skill surface the nested agent should see.
HARNESS_NAMES: frozenset[str] = frozenset(
    {
        'simulate.py',
        'code_surface.yaml',
        'plurai.config.yaml',
        'sitecustomize.py',
        'fetch_shadow.mjs',
        'mocks.py',
        '_llm_mock.py',
        'simulation_env',
        'plurai_sim',
    }
)


class HarnessOpacityError(AssertionError):
    """The nested agent's cwd or injected env exposes harness files — the
    simulation is discoverable from the filesystem the agent runs Bash in."""


class _RecordingOptions:
    """Stand-in for ``ClaudeAgentOptions`` that just records the kwargs (we need
    ``cwd`` and ``env``)."""

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _StubReadScope:
    """Stand-in for ``plurai.host_read_scope(...)`` — hermetic: no wrapper shell is written. Its
    methods take the SDK's own keywords."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def pre_tool_use_hook(self, *, trail: list | None, on_deny: object = None):  # noqa: ANN201, ARG002
        async def hook(*_: object) -> dict:
            return {}

        return hook

    def agent_shell_env(self, *, cli_env: object) -> dict[str, str]:  # noqa: ARG002
        return {}


def _stub_cli_hooks(scope: _StubReadScope, *, trail: list | None, on_read_denied: object = None) -> dict:
    """Stand-in for ``plurai.cli_hooks(...)``: the read gate of the stub scope."""
    return {'PreToolUse': [_RecordingOptions(matcher='Read|Glob|Grep|NotebookRead', hooks=[scope.pre_tool_use_hook(trail=trail, on_deny=on_read_denied)])]}


class _StubResultError(Exception):
    """Stand-in for ``claude_agent_sdk.ResultError``: the terminal error result's payload, its HTTP status read off it."""

    def __init__(self, message: str, data: dict | None = None, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.data, self.exit_code = data or {}, exit_code
        self.api_error_status = self.data.get('api_error_status')


class _RecordingClient:
    """Stand-in for ``ClaudeSDKClient`` — captures the options, never connects.

    ``connect_environ`` is this process's environment at ``connect()``: the one the real client starts
    the CLI with, ``options.env`` laid over it (``spawn_env``)."""

    last: _RecordingClient | None = None

    def __init__(self, options: _RecordingOptions) -> None:
        self.options = options
        self.connect_environ: dict[str, str] | None = None
        _RecordingClient.last = self

    async def connect(self) -> None:
        self.connect_environ = dict(os.environ)

    def spawn_env(self) -> dict[str, str]:
        """The environment the nested CLI, and every shell it runs, would start with: claude_agent_sdk's
        merge, ``{**os.environ at connect, **options.env}``."""
        if self.connect_environ is None:
            raise HarnessOpacityError('seed_state never connected the ClaudeSDKClient')
        return {**self.connect_environ, **(getattr(self.options, 'env', None) or {})}

    async def query(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        return None

    async def receive_response(self):  # pragma: no cover - empty async generator
        return
        yield  # unreachable; present only so this stays an async generator


def _install_stubs() -> str:
    """Put fake ``plurai`` / ``claude_agent_sdk`` / ``langchain_core`` modules in
    ``sys.modules`` so importing a bench is hermetic and starts no session.

    Returns the tempdir backing the fake ``plurai`` package — the caller MUST
    remove it (done in :func:`_capture_options`'s ``finally``). The fake package
    has a real on-disk layout (``_mocking/node/{fetch_shadow.mjs,code_hook.cjs,
    code_hook_loader.mjs}`` + ``__init__.py``) so the RELOCATED cli template's
    ``Path(plurai.__file__).parent/'_mocking'/'node'/'fetch_shadow.mjs'`` resolves
    to a real file OUTSIDE the customer harness, and ``plurai.write_sitecustomize``
    is a real function that drops a ``sitecustomize.py`` into the relocation shim.
    """
    fake_pkg_root = tempfile.mkdtemp(prefix='plurai_fakepkg_')
    node_dir = Path(fake_pkg_root) / '_mocking' / 'node'
    node_dir.mkdir(parents=True, exist_ok=True)
    (Path(fake_pkg_root) / '__init__.py').write_text('# stub plurai package\n', encoding='utf-8')
    # The preload trio is co-located in the installed package; mirror that so the
    # relocated --import target (and any relative require it would do) is present.
    for name, body in (
        ('fetch_shadow.mjs', '// stub fetch-shadow preload\n'),
        ('code_hook.cjs', '// stub code hook router\n'),
        ('code_hook_loader.mjs', '// stub code hook loader\n'),
    ):
        (node_dir / name).write_text(body, encoding='utf-8')

    plurai = types.ModuleType('plurai')
    plurai.__file__ = str(Path(fake_pkg_root) / '__init__.py')  # type: ignore[attr-defined]
    plurai.active_session_id = lambda: 'sid-opacity-gate'  # type: ignore[attr-defined]
    plurai.mock_service_url = lambda: 'http://127.0.0.1:65535'  # type: ignore[attr-defined]
    plurai.watch_interception = lambda *paths: None  # type: ignore[attr-defined]
    plurai.emit_code_surface = lambda *a, **k: None  # type: ignore[attr-defined]
    plurai.install_code_hook_from_env = lambda *a, **k: None  # type: ignore[attr-defined]

    def _write_sitecustomize(target_dir: str) -> str:
        """Real stand-in for ``plurai.write_sitecustomize`` — drops a
        ``sitecustomize.py`` into ``target_dir`` so a relocated PYTHONPATH head
        points at a dir that actually holds one (outside the harness)."""
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, 'sitecustomize.py')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('# stub sitecustomize: install_code_hook_from_env()\n')
        return path

    plurai.write_sitecustomize = _write_sitecustomize  # type: ignore[attr-defined]
    plurai.host_read_scope = _StubReadScope  # type: ignore[attr-defined]
    plurai.cli_hooks = _stub_cli_hooks  # type: ignore[attr-defined]
    sys.modules['plurai'] = plurai

    sdk = types.ModuleType('claude_agent_sdk')
    sdk.ClaudeAgentOptions = _RecordingOptions  # type: ignore[attr-defined]
    sdk.ClaudeSDKClient = _RecordingClient  # type: ignore[attr-defined]
    sdk.AssistantMessage = type('AssistantMessage', (), {})  # type: ignore[attr-defined]
    sdk.ResultMessage = type('ResultMessage', (), {})  # type: ignore[attr-defined]
    sdk.TextBlock = type('TextBlock', (), {})  # type: ignore[attr-defined]
    sdk.HookMatcher = _RecordingOptions  # type: ignore[attr-defined]
    sdk.ResultError = _StubResultError  # type: ignore[attr-defined]
    sys.modules['claude_agent_sdk'] = sdk

    # Own the parent stub fully (saved/restored by _capture_options) so we never
    # mutate a real installed ``langchain_core``.
    lc = types.ModuleType('langchain_core')
    lc_msg = types.ModuleType('langchain_core.messages')
    lc_msg.AIMessage = type(  # type: ignore[attr-defined]
        'AIMessage', (), {'__init__': lambda self, content=None: setattr(self, 'content', content)}
    )
    lc.messages = lc_msg  # type: ignore[attr-defined]
    sys.modules['langchain_core'] = lc
    sys.modules['langchain_core.messages'] = lc_msg

    return fake_pkg_root


def _capture_options(simulate_py_path: str | Path) -> _RecordingOptions:
    """Import the rendered ``simulate.py`` under stubs, run ``seed_state`` ONCE,
    and return the ``_RecordingOptions`` the ``ClaudeAgentOptions`` would carry
    (both ``cwd`` and ``env`` live on it). Restores all mutated global state
    (``sys.modules``, ``os.environ``) and removes the fake-package tempdir."""
    return _capture_client(simulate_py_path).options


def _capture_client(simulate_py_path: str | Path) -> _RecordingClient:
    """:func:`_capture_options`, returning the recording client itself. ``post_seed_environ`` on it is
    this process's environment once ``seed_state`` returned."""
    simulate_py_path = Path(simulate_py_path).resolve()
    stubbed = ('plurai', 'claude_agent_sdk', 'langchain_core', 'langchain_core.messages')
    saved_modules = {k: sys.modules.get(k) for k in stubbed}
    saved_env = os.environ.copy()
    fake_pkg_root = _install_stubs()
    _RecordingClient.last = None
    mod_name = f'rendered_simulate__{simulate_py_path.parent.name}'
    spec = importlib.util.spec_from_file_location(mod_name, simulate_py_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise HarnessOpacityError(f'could not load {simulate_py_path}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
        asyncio.run(mod.seed_state({}, {}))
        client = _RecordingClient.last
        if client is None:
            raise HarnessOpacityError('seed_state did not construct a ClaudeSDKClient')
        client.post_seed_environ = dict(os.environ)  # type: ignore[attr-defined]
        return client
    finally:
        sys.modules.pop(mod_name, None)
        for key, original in saved_modules.items():
            if original is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = original
        os.environ.clear()
        os.environ.update(saved_env)
        shutil.rmtree(fake_pkg_root, ignore_errors=True)


def resolve_agent_cwd(simulate_py_path: str | Path) -> str:
    """Import the rendered ``simulate.py`` and run ``seed_state`` under stubs;
    return the ``cwd`` string the ``ClaudeAgentOptions`` would carry."""
    options = _capture_options(simulate_py_path)
    cwd = getattr(options, 'cwd', None)
    if not cwd:
        raise HarnessOpacityError('ClaudeAgentOptions carried no cwd')
    return str(cwd)


def resolve_agent_env(simulate_py_path: str | Path) -> dict[str, str]:
    """Import the rendered ``simulate.py`` and run ``seed_state`` under stubs;
    return the ``env`` dict the ``ClaudeAgentOptions`` would carry (the env the
    nested agent's Bash subprocesses inherit)."""
    options = _capture_options(simulate_py_path)
    return dict(getattr(options, 'env', None) or {})


def resolve_agent_spawn_env(simulate_py_path: str | Path) -> tuple[dict[str, str], dict[str, str]]:
    """Import the rendered ``simulate.py`` and run ``seed_state`` under stubs; return ``(the environment
    the nested CLI and its shells start with, this process's environment after seed_state)``."""
    client = _capture_client(simulate_py_path)
    return client.spawn_env(), client.post_seed_environ  # type: ignore[attr-defined]


def harness_files_reachable_from(cwd: str | Path) -> set[str]:
    """Return the set of harness paths an agent could reach from ``cwd`` — by
    listing ``cwd``, ``cd ..``-ing to its parent, and exploring the parent tree."""
    base = Path(cwd).resolve()
    found: set[str] = set()
    for root in {base, base.parent}:
        if not root.exists():
            continue
        if root.name in HARNESS_NAMES:
            found.add(str(root))
        for path in root.rglob('*'):
            if path.name in HARNESS_NAMES:
                found.add(str(path))
    return found


def _is_within(path: Path, ancestor: Path) -> bool:
    """True if ``ancestor`` *is* ``path`` or is one of ``path``'s parents (i.e.
    ``path`` lives inside ``ancestor``). Both are resolved before comparison."""
    path = path.resolve()
    ancestor = ancestor.resolve()
    return ancestor == path or ancestor in path.parents


def _harness_path_candidates(env: dict[str, str]) -> list[tuple[str, str]]:
    """Extract every (env_var_name, candidate_path) the nested agent could take a
    ``dirname`` / ``--import`` / ``PYTHONPATH`` chain from. Precise on purpose —
    only the three injected vars the ``code`` / ``cli_http`` benches set — so the
    negative control (a path pointed back at the harness) stays meaningful."""
    candidates: list[tuple[str, str]] = []

    pythonpath = env.get('PYTHONPATH')
    if pythonpath:
        for entry in pythonpath.split(os.pathsep):
            if entry:
                candidates.append(('PYTHONPATH', entry))

    node_options = env.get('NODE_OPTIONS')
    if node_options:
        for token in node_options.split():
            for flag in ('--import=', '--require='):
                if token.startswith(flag):
                    spec = token[len(flag):]
                    if spec:
                        candidates.append(('NODE_OPTIONS', spec))

    surface = env.get('PLURAI_CODE_SURFACE')
    if surface:
        candidates.append(('PLURAI_CODE_SURFACE', surface))

    return candidates


def _opaque_env_check(env: dict[str, str], harness_dir: Path) -> dict[str, str]:
    """Raise :class:`HarnessOpacityError` if any candidate path in ``env`` resolves
    inside ``harness_dir``. Pure check over an ALREADY-captured env — so the
    operator entrypoint can run it on the same capture as the cwd check."""
    for var, raw in _harness_path_candidates(env):
        candidate = Path(raw)
        if _is_within(candidate, harness_dir):
            raise HarnessOpacityError(
                f'injected env var {var}={raw!r} resolves inside the customer harness '
                f'dir {harness_dir} — a Bash-bearing nested agent can take its '
                f'dirname/--import/PYTHONPATH and `cat` the customer '
                f'simulation_env/mocks.py. Relocate it to a clean shim dir or the '
                f'installed plurai package.'
            )
    return env


def assert_opaque_env(simulate_py_path: str | Path) -> dict[str, str]:
    """Raise :class:`HarnessOpacityError` if any injected env value resolves —
    via ``dirname`` / ``--import`` / ``PYTHONPATH`` — to a file or dir inside the
    customer harness dir (the dir holding ``simulate.py``). Such a value lets a
    Bash-bearing nested agent ``cat`` the customer's ``simulation_env/mocks.py``.
    Returns the env on success.

    HONEST SCOPE: this closes the customer-mocks.py pivot ONLY. The returned env
    still names ``PLURAI_*`` and points at a clean shim / the installed ``plurai``
    package — generic-plurai detection (and the grandchild ``os.environ`` dump) are
    Option-D residuals, not asserted here."""
    simulate_py_path = Path(simulate_py_path).resolve()
    return _opaque_env_check(resolve_agent_env(simulate_py_path), simulate_py_path.parent)


def _opaque_cwd_check(cwd: str | Path | None) -> str:
    """Raise :class:`HarnessOpacityError` if ``cwd`` is empty or any harness file is
    reachable from it. Pure check over an ALREADY-captured cwd."""
    if not cwd:
        raise HarnessOpacityError('ClaudeAgentOptions carried no cwd')
    found = harness_files_reachable_from(cwd)
    if found:
        raise HarnessOpacityError(
            f'nested agent cwd {str(cwd)!r} exposes harness files (a Bash-bearing agent '
            f'can `cat` these and detect the simulation): {sorted(found)}'
        )
    return str(cwd)


def assert_opaque_cwd(simulate_py_path: str | Path) -> str:
    """Raise :class:`HarnessOpacityError` if the rendered bench gives the nested
    agent a cwd from which any harness file is reachable. Returns the cwd on
    success (caller may clean it up)."""
    return _opaque_cwd_check(resolve_agent_cwd(simulate_py_path))


def _cleanup_isolated_cwd(cwd: str) -> None:
    """Best-effort removal of an isolated-cwd tempdir created by the bench."""
    if 'plurai_' in cwd:
        shutil.rmtree(Path(cwd).parent, ignore_errors=True)


if __name__ == '__main__':  # pragma: no cover - operator entrypoint
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    if len(sys.argv) != 2:
        print('usage: python verify_harness_opacity.py path/to/simulate.py', file=sys.stderr)
        raise SystemExit(2)
    simulate_py_path = Path(sys.argv[1]).resolve()
    try:
        options = _capture_options(simulate_py_path)  # one seed_state run feeds both checks
        resolved_cwd = _opaque_cwd_check(getattr(options, 'cwd', None))
        _opaque_env_check(dict(getattr(options, 'env', None) or {}), simulate_py_path.parent)
    except HarnessOpacityError as exc:
        print(f'OPACITY GATE FAILED: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        'OPACITY GATE PASSED: nested agent cwd is harness-free and no injected env '
        f'value resolves inside the customer harness ({resolved_cwd})'
    )
    _cleanup_isolated_cwd(resolved_cwd)
