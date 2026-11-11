"""Blocking gate: a generated ``simulate.py`` must drive its ``ClaudeSDKClient``
through the client's REAL public API ONLY.

Why: all three ``simulate.py`` variants (``tools`` / ``code``-SDK / ``code``-CLI)
share ONE ``seed_state`` + ``invoke_agent`` body that drives a persistent
``ClaudeSDKClient`` via ``connect()`` to start, then ``query(prompt)`` +
iterating ``receive_response()`` to read the reply. The reference TEMPLATES use
only that sanctioned surface — but in a real run the LLM generating ``simulate.py``
DIVERGED from the template and emitted ``await _client.run(messages)``. There is no
``.run`` method on ``ClaudeSDKClient`` (its real drive API is ``connect`` / ``query``
/ ``receive_response`` / ...), so the harness crashed at the first turn with
``AttributeError: 'ClaudeSDKClient' object has no attribute 'run'``, every scenario
wrote ``error.json``, and nothing scored — a silent, 20-minute-wasting failure.
Prose ("emit the template verbatim") did not stop the divergence, so this is the
deterministic gate that does.

This gate is GENERIC (no customer- or skill-specific strings): it reads the file,
``ast.parse``s it (it NEVER imports/executes the target — a ``simulate.py`` has
side-effecting imports), and looks for the INLINE shape every template uses — a
module-global (or ``async with ... as X``) variable bound to a ``ClaudeSDKClient(...)``
construction, driven via direct ``<var>.<method>(...)`` calls. Two checks run over
that shape:

1. every INLINE ``<client>.<method>(...)`` call it can see must name a method that
   exists on the real ``ClaudeSDKClient`` (the ``.run(...)`` hallucination and its
   kin);
2. a constructed client must be driven inline AT ALL — a client that is constructed
   but never has an observable inline call on it (or an ``async with ... as X``
   entry) fails closed (the "drive floor"; see :func:`assert_client_api`).

LIMITS (by design, not oversights): this checks method-NAME existence only, never
call arity/signature. It intentionally does NOT follow aliases (``x = _client``),
``self``/attribute-stored clients (``self._client = ClaudeSDKClient(...)``), or a
client passed into a helper (``drive(_client)``) — none of those produce an
attributable inline call, so they now FAIL the drive floor instead of silently
passing. Variable tracking is also scope-insensitive: a differently-scoped, unrelated
var that happens to reuse an exact tracked name would be (unlikely but) falsely
flagged as the same client.

Standalone use:  ``python verify_client_api.py path/to/simulate.py``
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Fallback mirror of the real ``ClaudeSDKClient`` public API — used ONLY when
# ``claude_agent_sdk`` is not importable in the gate's interpreter (e.g. the gate is
# run outside the bench venv). When the import DOES succeed we introspect the
# installed class instead (authoritative), so this list is a safety net, not the
# source of truth. Keep it in sync with the SDK if the client surface changes.
_FALLBACK_CLIENT_METHODS: frozenset[str] = frozenset(
    {
        'connect',
        'disconnect',
        'query',
        'receive_response',
        'receive_messages',
        'interrupt',
        'get_context_usage',
        'get_mcp_status',
        'get_server_info',
        'reconnect_mcp_server',
        'rewind_files',
        'set_model',
        'set_permission_mode',
        'stop_task',
        'toggle_mcp_server',
    }
)

_CLIENT_CLASS = 'ClaudeSDKClient'


class ClientApiError(AssertionError):
    """The generated ``simulate.py`` drives its ``ClaudeSDKClient`` via a method the
    real client does not have — the harness would crash with an ``AttributeError``
    at the first turn (the ``.run(...)`` hallucination and its kin)."""


def _allowed_methods() -> frozenset[str]:
    """The real ``ClaudeSDKClient`` public METHOD set. Introspect the installed class
    when ``claude_agent_sdk`` is importable (authoritative); otherwise fall back to
    the hardcoded mirror above so the gate still runs outside the bench venv.

    Filtered to CALLABLE attributes only — ``dir()`` admits any public attribute, and
    a future non-method public attribute on the class would otherwise let a bad
    ``.thatname()`` call slip through as "allowed" without ever being a real method."""
    try:
        from claude_agent_sdk import ClaudeSDKClient
    except Exception:
        return _FALLBACK_CLIENT_METHODS
    methods = frozenset(
        m for m in dir(ClaudeSDKClient) if not m.startswith('_') and callable(getattr(ClaudeSDKClient, m, None))
    )
    return methods or _FALLBACK_CLIENT_METHODS


def _is_client_construction(node: ast.AST) -> bool:
    """True if ``node`` is a ``ClaudeSDKClient(...)`` construction call — matching both
    a bare ``ClaudeSDKClient(...)`` name and a ``mod.ClaudeSDKClient(...)`` attribute
    access, so an aliased/qualified import still resolves."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == _CLIENT_CLASS
    if isinstance(func, ast.Attribute):
        return func.attr == _CLIENT_CLASS
    return False


def _bind_targets(target: ast.AST, names: set[str]) -> None:
    """Collect simple ``Name`` targets a construction is bound to (a bare name, or a
    tuple/list unpack — though a client is virtually always a bare name)."""
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _bind_targets(elt, names)


def _has_with_form_construction(tree: ast.AST) -> bool:
    """True if the tree has an ``async with ClaudeSDKClient(...) as X:`` (or sync
    ``with``) construction. Entering the client via its context-manager protocol is
    itself a real drive of it (``__aenter__``/``__enter__`` run), so this counts
    toward the drive floor even if no further inline ``X.<method>(...)`` call is
    seen — avoids false-positiving the with-form on a minimal/edge-case body."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and _is_client_construction(item.context_expr):
                    return True
    return False


def _find_client_vars(tree: ast.AST) -> set[str]:
    """Every local variable name bound to a ``ClaudeSDKClient(...)`` construction —
    ``X = ClaudeSDKClient(...)`` (plain + annotated) and ``async with
    ClaudeSDKClient(...) as X:``. Detected from the CONSTRUCTION, never a hardcoded
    ``_client`` name, so a renamed client var is still tracked.

    Only the INLINE binding shape is tracked — a client stored on ``self``/an
    attribute (``self._client = ClaudeSDKClient(...)``) is deliberately not resolved
    to a trackable name (the target isn't a plain ``Name``); such a construction still
    fails the drive floor in :func:`assert_client_api` (constructed, zero inline
    calls seen), it's just not attributed to a specific var name here. Tracking is
    also scope-insensitive: an unrelated var in another function that happens to
    reuse an exact tracked name is (rarely, but) treated as the same client."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_client_construction(node.value):
            for target in node.targets:
                _bind_targets(target, names)
        elif isinstance(node, ast.AnnAssign) and node.value is not None and _is_client_construction(node.value):
            _bind_targets(node.target, names)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and _is_client_construction(item.context_expr):
                    _bind_targets(item.optional_vars, names)
    return names


def _collect_client_method_calls(tree: ast.AST, client_vars: set[str]) -> list[tuple[str, str, int]]:
    """Every ``(receiver, method, lineno)`` for an INLINE method call directly on a
    tracked client variable: ``<client>.<method>(...)`` (incl. ``await``ed and
    ``async for``-iterated ones — the ``Call`` node is present regardless of the
    surrounding ``await``/``async for``), plus the direct
    ``ClaudeSDKClient(...).<method>(...)`` chain form.

    Deliberately does NOT follow indirection: an alias (``x = _client; x.run()``), a
    bound/stored attribute (``self._client.run()``), a call reached through a helper
    the client was passed into (``drive(_client)`` then ``cli.run()`` inside it), or a
    method reference taken without calling it (``f = _client.run; f()`` /
    ``getattr(_client, "run")()``) — none of those match this pattern, so none of
    those calls are ever recorded here. That's intentional: those shapes yield ZERO
    calls for their client, which the drive floor in :func:`assert_client_api` fails
    closed on, rather than this function trying (and inevitably failing in some case)
    to resolve arbitrary indirection statically."""
    calls: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in client_vars:
            calls.append((receiver.id, node.func.attr, node.lineno))
        elif _is_client_construction(receiver):
            calls.append((f'{_CLIENT_CLASS}(...)', node.func.attr, node.lineno))
    return calls


def assert_client_api(simulate_py_path: str | Path) -> list[str]:
    """Raise :class:`ClientApiError` unless the generated ``simulate.py`` (a) drives
    its ``ClaudeSDKClient`` ONLY through real client methods, and (b) actually drives
    a constructed client inline at all. Returns the sorted list of client methods it
    calls, on success.

    Fails when: the file is not valid Python; it constructs no ``ClaudeSDKClient``; it
    calls a method on a tracked client that the real client does not have (the
    ``.run(...)`` hallucination and its kin); or it constructs a client but the gate
    can see no inline drive of it at all — the DRIVE FLOOR. The floor exists because a
    handful of structural rewrites (alias reassignment, storing the client on
    ``self``/an attribute, passing it into a helper, or taking an unbound method
    reference via plain attribute access or ``getattr``) all produce ZERO attributable
    inline calls and would otherwise pass vacuously — every real template drives its
    client with at least one inline ``connect()``/``query()``/``receive_response()``
    call (or enters it via ``async with ... as X:``), so "constructed but never
    observably driven" is always a real divergence, not a false positive. The floor
    only requires the client be driven AT ALL, not any specific method (the
    ``code``-SDK/CLI templates elide ``invoke_agent`` and only call ``connect()`` in
    ``seed_state`` — requiring ``query``/``receive_response`` would false-positive
    them). NEVER imports/executes the target."""
    path = Path(simulate_py_path).resolve()
    source = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ClientApiError(
            f'{path} is not valid Python (a generated simulate.py must parse): {exc}'
        ) from exc

    if not any(_is_client_construction(node) for node in ast.walk(tree)):
        raise ClientApiError(
            f'{path} constructs no {_CLIENT_CLASS}(...) — a generated simulate.py must build '
            f'and drive a {_CLIENT_CLASS} (connect() to start, then query(prompt) + iterate '
            f'receive_response()). Emit the reference simulate.py template verbatim.'
        )

    client_vars = _find_client_vars(tree)
    allowed = _allowed_methods()
    calls = _collect_client_method_calls(tree, client_vars)
    bad = [(recv, method, lineno) for recv, method, lineno in calls if method not in allowed]
    if bad:
        offenders = '\n'.join(
            f'  - line {lineno}: `{recv}.{method}(...)` — {_CLIENT_CLASS} has no `.{method}()` method'
            for recv, method, lineno in sorted(bad, key=lambda item: item[2])
        )
        raise ClientApiError(
            f'{path} drives {_CLIENT_CLASS} via method(s) that do not exist on it:\n'
            f'{offenders}\n'
            f'{_CLIENT_CLASS} is driven ONLY through its real API: connect() to start the '
            f'session, then query(prompt) to send a turn and iterate receive_response() to '
            f'read the reply (see the reference simulate.py templates). `.run(...)` is the '
            f'known LLM hallucination — it does NOT exist on {_CLIENT_CLASS}; re-emit the '
            f"template's seed_state / invoke_agent client-drive body verbatim."
        )

    if not (calls or _has_with_form_construction(tree)):
        raise ClientApiError(
            f'{path}: {_CLIENT_CLASS} is constructed but never driven inline — the gate '
            f'cannot see connect()/query()/receive_response() on it. This usually means the '
            f'drive loop was rewritten to alias the client, store it on self/an attribute, or '
            f"pass it into a helper — re-emit the template's seed_state/invoke_agent "
            f'client-drive body verbatim (inline `_client.<method>(...)` calls).'
        )

    return sorted({method for _, method, _ in calls})


if __name__ == '__main__':  # pragma: no cover - operator entrypoint
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    if len(sys.argv) != 2:
        print('usage: python verify_client_api.py path/to/simulate.py', file=sys.stderr)
        raise SystemExit(2)
    try:
        driven = assert_client_api(sys.argv[1])
    except ClientApiError as exc:
        print(f'CLIENT API GATE FAILED: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
    surface = ', '.join(driven) if driven else '(no client method calls found)'
    print(f'CLIENT API GATE PASSED: {_CLIENT_CLASS} driven only through its real API [{surface}]')
