"""Static analyzer for a generated simulate.py (subprocess orchestration model).

The /simulation-scan-agent skill ships a non-trivial contract: state-surfaces audit,
seed_state hook, async/sync invoke_agent matching the graph, no asyncio.run
inside the IPC-driven hooks. The contract is prose in SKILL.md, and prose-driven
LLM code generation has variance — the skill-LLM can read Step 6.5 and still
omit seed_state when patching storage.

This script is a deterministic gate. It parses simulate.py with ``ast`` and
reports every broken contract clause as a gate finding, naming the exact
missing piece and the SKILL.md step that documents the fix; it exits 1 while
one of them holds. The skill runs it
between code generation and the Step 10c smoke as a blocking precondition —
if it fails, the skill iterates rather than declaring done.

Subprocess model: ``invoke_agent`` and ``seed_state`` (if present) are
*auto-discovered* by ``python -m plurai.child`` — there is no
``Orchestrator(seed_state=...)`` kwarg and no ``if __name__ == "__main__":``
block. The verifier does not enforce that seed_state is "wired" to an
Orchestrator constructor.

Usage::

    python verify_simulate.py path/to/simulate.py [--findings-out PATH]

Every ``fail`` is a gate finding (``scripts/gate_ledger.py``), printed as a ``finding:`` line. Exits 1
while something HOLDS — a security or invariant finding, or an advisory one neither repaired nor
overruled in ``<workdir>/artifacts/.gate_overrules.jsonl`` — and 0 otherwise. Designed to be vendored into
each agent's ``plurai/`` dir alongside ``simulate.py`` so the skill (and the
agent owner) can re-run it at any time.
"""

from __future__ import annotations

import argparse
import ast
import enum
import inspect
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import gate_ledger  # noqa: E402 - the plugin's one finding contract
from host_effects import framework_id  # noqa: E402 - the plugin's one framework normaliser


class Code(StrEnum):
    """Every finding code THIS script can emit — the closed set, declared.

    RESTATED, NOT IMPORTED: each verifier declares its own closed set.
    ``verify_config.py`` carries its own ``Code`` and ``verify_smoke.py`` a
    ``Category``; the members the two verifiers share (``HELPER_UNREADABLE`` / ``HELPER_UNPARSEABLE``
    — both scripts discover the same split helpers) are pinned equal by a test, not by an import.

    ``StrEnum`` (the shim above it below 3.11), never a bare ``class Code(str, enum.Enum)``: ``Finding.render()`` interpolates the
    code, and a ``str``-mixin enum formats as ``Code.X`` rather than ``x`` — every operator line
    and every downstream grep silently rewritten while the ``==`` comparisons still pass.

    Several members are read outside this repo — ``world_owner_unreset`` belongs to the closed,
    versioned ``suite-diagnostics-v8`` vocabulary the SDK and the backend both restate, and
    ``seed_state_bad_signature`` is asserted on in the backend's tests. Adding a code is one member +
    one documentation row; renaming one is not a rename at the call sites, it is a new member
    plus the old one kept as an alias, so readers that have not moved still resolve.

    Every emission site's code slot reads this enum (never a literal), every member has a
    symptom→cause row, and every documented code is a member.
    """

    AGENT_ENV_UNREACHABLE = 'agent_env_unreachable'  # the agent runs in a process the harness does not launch while a store is simulated
    ASYNCIO_RUN_IN_INVOKE_AGENT = 'asyncio_run_in_invoke_agent'  # sync wrapper calling asyncio.run inside the dialog hook
    ASSISTANT_MESSAGE_WITHOUT_MODEL_METADATA = 'assistant_message_without_model_metadata'  # warn: `invoke_agent` builds an assistant message by hand with no model or usage on it
    ASYNCIO_RUN_IN_INVOKE_PIPELINE = 'asyncio_run_in_invoke_pipeline'  # same running-loop footgun on the pipeline hook
    ASYNCIO_RUN_IN_SEED_STATE = 'asyncio_run_in_seed_state'  # same footgun on the seeding hook
    BUILD_STATE_MISSING_STATE_FIELDS = 'build_state_missing_state_fields'  # the state builder omits fields the state class declares
    BUILD_STATE_UNKNOWN_KEYS = 'build_state_unknown_keys'  # the state builder writes keys the state class does not declare
    DOCUMENT_READ_OFF_PAYLOAD = 'document_read_off_payload'  # a handed-over document (`.document_fields`) read off the turn payload or `seed_state`, not off the turn's parts
    CHECKPOINTERLESS_GRAPH_REUSE = 'checkpointerless_graph_reuse'  # a pre-compiled graph reused with no persistence handle — cross-turn state dropped
    AGENT_FAKE_UNROUTED = 'agent_fake_unrouted'  # an `agent_fake` endpoint the harness does not point at the reference serve helper in `seed_state`
    ENDPOINT_ENV_UNPINNED = 'endpoint_env_unpinned'  # a declared endpoint variable the harness does not assign a literal mock, `*.invalid` or confirmed value
    DOTENV_IMPORT_SWALLOWED = 'dotenv_import_swallowed'  # `try: import dotenv / except ImportError: pass` — looks loaded, loads nothing
    ENTITIES_PROXY_ISINSTANCE_DICT_MISUSE = 'entities_proxy_isinstance_dict_misuse'  # `isinstance(world_state.entities, dict)` is always False
    ENVELOPE_RENDER_NOT_IDEMPOTENT = 'envelope_render_not_idempotent'  # `kind: json_field` applied unconditionally, so a re-render double-wraps
    HELPER_UNPARSEABLE = 'helper_unparseable'  # warn: a split helper does not parse; AST checks against it are skipped
    HELPER_UNREADABLE = 'helper_unreadable'  # warn: a split helper could not be read at all
    INVOKE_AGENT_RETURNS_PICKED_STATE = 'invoke_agent_returns_picked_state'  # the return picks keys from the saved state, so a resume pin restores only those
    MOCK_TOOL_STATE_WRITES_UNDECLARED = 'mock_tool_state_writes_undeclared'  # warn: a mocked tool's real body writes state keys; no `state_updates:` declared
    MOCK_STATE_UPDATES_UNKNOWABLE = 'mock_state_updates_unknowable'  # warn: a state-update check did not run — unreadable config, or no state-write rule
    MOCK_TOOL_ERROR_ARITY = 'mock_tool_error_arity'  # warn: a `MockToolError(...)` call the installed SDK's signature refuses
    MOCK_TOOL_ERROR_RAISED_IN_MOCK = 'mock_tool_error_raised_in_mock'  # warn: a `@plurai.mock` body raises `MockToolError` instead of returning the failure
    MISSING_INVOKE_HOOK = 'missing_invoke_hook'  # neither `invoke_agent` nor `invoke_pipeline` at module scope
    MISSING_SEED_STATE = 'missing_seed_state'  # storage is patched, its class does not only delegate to a declared mock over a mock-owned world, and no `seed_state` is defined
    NATIVE_TOOL_TRAIL_MISSING = 'native_tool_trail_missing'  # `agent.native_tools` is declared and the harness never passes `plurai.confine` a trail or returns no `tool_trail`
    NO_DOTENV_LOAD = 'no_dotenv_load'  # nothing loads a `.env`, so the child subprocess runs without the agent's env
    ENV_FILE_NOT_LOADED = 'env_file_not_loaded'  # warn: the bench declares `harness.env_file` and no dotenv call names it
    PAYLOAD_MESSAGES_AS_HISTORY = 'payload_messages_as_history'  # the turn payload's `messages` read as prior history
    SEED_STATE_BAD_SIGNATURE = 'seed_state_bad_signature'  # `seed_state` does not take `(agent_context, world_state, resume_seed)`
    SEED_STATE_IGNORES_RESUME_POINT = 'seed_state_ignores_resume_point'  # the seed is taken and its `resume_point` is never named
    SEED_STATE_SLOTS_NEVER_RETURNED = 'seed_state_slots_never_returned'  # no key `seed_state` restores is a top-level key `invoke_agent` returns
    SEED_STATE_REFUSES_EVERY_SEED = 'seed_state_refuses_every_seed'  # a blanket `if resume_seed: raise` the bench manifest does not declare as `harness.resume: unsupported`
    SEED_STATE_IGNORES_RESUME_SEED = 'seed_state_ignores_resume_seed'  # `resume_seed` is declared and never read — the agent opens on an empty history
    SEED_STATE_IGNORES_WORLD_STATE = 'seed_state_ignores_world_state'  # `initial_world_state` is declared and never read
    SNAPSHOT_HAND_ROLLED_FILE_READER = 'snapshot_hand_rolled_file_reader'  # fail (advisory): the snapshot reads a document itself, not via file_row
    SNAPSHOT_READ_ERROR_AS_CONTENT = 'snapshot_read_error_as_content'  # fail (warn once `_unreadable` is named): a read failure inside `world_snapshot` is returned as a string, into a content slot
    SPAWNED_AGENT_OUTPUT_LOST = 'spawned_agent_output_lost'  # a long-lived spawn discards the agent server's output
    SPAWNED_STDOUT_ON_IPC_CHANNEL = 'spawned_stdout_on_ipc_channel'  # a long-lived spawn inherits stdout and pollutes the IPC channel
    START_MODE_STATE_UNSEEDED = 'start_mode_state_unseeded'  # warn: a declared start flag skips the agent code that writes a key it refuses to run without, and the harness never seeds it
    STATE_UPDATE_GATE_UNAPPLIED = 'state_update_gate_unapplied'  # warn: every agent reader of a declared state key is outside what the harness imports
    STATE_CLASS_UNIMPORTABLE = 'state_class_unimportable'  # `--state-class` names something this interpreter cannot import
    STORAGE_CLASS_DROPS_DECLARED_FIELD = 'storage_class_drops_declared_field'  # the storage class projects records without a declared field
    STORAGE_CLASS_NO_WORLD_STATE_REF = 'storage_class_no_world_state_ref'  # storage is patched but no class body reads the world state or delegates to a declared mock
    HARNESS_STORE_SERVES_WORLD = 'harness_store_serves_world'  # warn: a harness object installed over agent code reads or writes `plurai.world_state` itself — harness-authored store behaviour
    STORAGE_FIDELITY_UNKNOWABLE = 'storage_fidelity_unknowable'  # warn: the fidelity check did not run — no schema, or no PyYAML
    SYNTAX_ERROR = 'syntax_error'  # simulate.py does not parse
    TOOL_CONTENT_ENVELOPE_RENDER_INDISTINGUISHABLE = 'tool_content_envelope_render_indistinguishable'  # warn: `kind: text` rendering is indistinguishable from none
    TOOL_CONTENT_ENVELOPE_UNKNOWN_KIND = 'tool_content_envelope_unknown_kind'  # the declared envelope kind is outside the vocabulary
    TOOL_CONTENT_ENVELOPE_UNVERIFIABLE = 'tool_content_envelope_unverifiable'  # warn: an envelope is declared and the rendering cannot be traced
    WORLD_OWNER_CONFLICT = 'world_owner_conflict'  # `world_state.owner` is declared twice and the two disagree
    WORLD_OWNER_UNKNOWABLE = 'world_owner_unknowable'  # warn: the world-owner check did not run — no PyYAML on this interpreter
    WORLD_OWNER_UNOBSERVABLE = 'world_owner_unobservable'  # warn: a non-mock world owner and no module-scope world-snapshot hook to read it
    WORLD_OWNER_UNRESET = 'world_owner_unreset'  # a non-mock world owner and no module-scope `reset` to clear it between scenarios (warn on the `shared_world_ok` opt-in)
    SUBAGENT_INNER_CALLS_UNPROVEN = 'subagent_inner_calls_unproven'  # a sub-agent's own live tool calls are not shown to reach the trail, and those tools do not abstain (`observation_lane: none`)
    SUBAGENT_INNER_CALLS_UNLINKED = 'subagent_inner_calls_unlinked'  # warn: a sub-agent's calls reach the trail without `parent_call_id`, so scoring cannot place them under the delegation call
    WORLD_LIVE_WRITER_UNRESET = 'world_live_writer_unreset'  # warn: tools write a live system and no module-scope `reset` returns it to the same state per conversation, whatever the owner
    WRAPPER_DROPS_RESPONSE_FIELDS = 'wrapper_drops_response_fields'  # the mock dispatch wrapper drops fields the descriptor declares
    WRAPPER_DROPS_STATE_UPDATES = 'wrapper_drops_state_updates'  # a tool declares `state_updates:` and the mock wrapper never lands them
    WRAPPER_IGNORES_TOOL_CONTENT_ENVELOPE = 'wrapper_ignores_tool_content_envelope'  # an envelope is declared and the wrapper never applies it


@dataclass(frozen=True)
class Finding:
    """One contract violation. ``severity='fail'`` is a gate finding (it exits 1 while it holds); ``'warn'`` is informational.

    ``filename`` is the file the line number refers to. ``None`` = simulate.py
    (default; back-compat). Set when the finding originates from a discovered
    helper (Step 8.4 split) so the rendered line number is attributable.
    """

    severity: str
    #: A member of `Code` — never a literal. The annotation is the rule; every call site is checked against it.
    code: Code
    message: str
    skill_step: str  # 'Step 6.5' / 'Step 8' / etc.
    line: int | None = None
    filename: str | None = None

    def render(self) -> str:
        if self.line is not None and self.filename:
            loc = f' ({self.filename}:{self.line})'
        elif self.line is not None:
            loc = f' (line {self.line})'
        else:
            loc = ''
        marker = '✗' if self.severity == 'fail' else '!'
        return f'  {marker} [{self.code}] {self.message}{loc} — see SKILL.md {self.skill_step}'


def _calls_asyncio_run(body: list[ast.stmt]) -> int | None:
    """Walk a function body looking for ``asyncio.run(...)`` invocations.

    Returns the line number of the first match, or None. We accept ``asyncio.run``
    by attribute access on a Name and direct ``run`` calls when ``run`` was
    imported from asyncio (rare but possible). Only the function body is walked
    — nested function definitions are ignored to avoid false positives on a
    legitimate ``asyncio.run(_main())`` inside a sibling helper.
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # asyncio.run(...) — attribute on Name 'asyncio'
        if (
            isinstance(func, ast.Attribute)
            and func.attr == 'run'
            and isinstance(func.value, ast.Name)
            and func.value.id == 'asyncio'
        ):
            return node.lineno
    return None


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the top-level (module-scope) function definition with ``name``, or None."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _module_scope_bindings(tree: ast.Module) -> set[str]:
    """Every name simulate.py binds AT IMPORT TIME — the set ``getattr(module, name)`` can hit.

    The question :func:`_find_function` answers NARROWLY, asked the way the runner asks it. The
    child resolves every hook with ``getattr`` on the imported module, so ``reset = _impl`` and
    ``from ._stores import reset`` export the hook exactly as a ``def`` does; a check that saw
    only ``def`` would refuse a harness the runner drives perfectly well, and a false refusal is
    the reason an author stops reading the verifier. Statement bodies that RUN at import
    (``if`` / ``try`` / ``with`` / ``for``) are descended into; function and class bodies are not,
    because a name bound there is not an attribute of the module.

    A DELIBERATE COPY, three times over: ``skills/simulation-setup/scripts/verify_config.py``'s
    function of the same name, and the SDK's ``plurai/module_bindings.py``. Each verifier keeps its
    own copy rather than importing the other; the SDK needs the same answer without depending on
    this repo at all. The three must answer the binding question identically, and the SDK's copy
    has the same code with docstrings aside.
    """
    bound: set[str] = set()

    def _walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                continue  # its body is a different scope
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = []
                for target in node.targets:
                    if isinstance(target, (ast.Tuple, ast.List)):
                        targets.extend(target.elts)
                    else:
                        targets.append(target)
                for target in targets:
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if isinstance(node.target, ast.Name):
                    bound.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add(alias.asname or alias.name.split('.')[0])
            for field in ('body', 'orelse', 'finalbody'):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    _walk([s for s in nested if isinstance(s, ast.stmt)])
            for handler in getattr(node, 'handlers', []) or []:
                _walk(handler.body)

    _walk(tree.body)
    return bound


def _command_update_keys(fn: ast.AST) -> set[str]:
    """The keys of every ``Command(update=...)`` ``fn`` builds: a dict literal, or a local name
    bound to one plus its ``name['key'] = ...`` writes."""
    local: dict[str, set[str]] = {}
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Dict):
            for target in n.targets if isinstance(n, ast.Assign) else [n.target]:
                if isinstance(target, ast.Name):
                    local.setdefault(target.id, set()).update(k.value for k in n.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                    local.setdefault(target.value.id, set()).add(target.slice.value)
    keys: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _dotted(n.func).split('.')[-1] == 'Command':
            for kw in n.keywords:
                if kw.arg == 'update' and isinstance(kw.value, ast.Dict):
                    keys |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                elif kw.arg == 'update' and isinstance(kw.value, ast.Name):
                    keys |= local.get(kw.value.id, set())
    return keys


def _state_reads(tree: ast.AST, key: str) -> list[int]:
    """Lines reading ``key`` off a mapping: ``x.get('key')`` or a load of ``x['key']``."""
    return [
        n.lineno for n in ast.walk(tree)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'get' and n.args
            and isinstance(n.args[0], ast.Constant) and n.args[0].value == key)
        or (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and isinstance(n.slice, ast.Constant) and n.slice.value == key)
    ]


def _command_wrapper_drops(wrappers: list[ast.FunctionDef | ast.AsyncFunctionDef], every_key: frozenset[str]) -> list[tuple[int, str]]:
    """LangGraph: each success ``Command(update={...})`` literal a wrapper returns that carries none of
    the declared keys and no ``**`` merge."""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()  # a nested wrapper is walked by its factory too
    for wrapper in wrappers:
        for call in ast.walk(wrapper):
            if not (isinstance(call, ast.Call) and _dotted(call.func).split('.')[-1] == 'Command') or id(call) in seen:
                continue
            seen.add(id(call))
            update = next((kw.value for kw in call.keywords if kw.arg == 'update'), None)
            if not isinstance(update, ast.Dict) or _is_error_render(update):
                continue
            keys = {k.value for k in update.keys if isinstance(k, ast.Constant)}
            if None in update.keys or keys & every_key:
                continue
            out.append((call.lineno, f'returns Command(update={{{", ".join(sorted(map(repr, keys)))}}}) on success'))
    return out


def _on_holder(node: ast.AST | None, holder: str, receivers: frozenset[str] | None) -> bool:
    """``node`` is ``<receiver>.<holder>`` — ``ctx.context``, ``self.state``. ``receivers=None`` takes any name."""
    return (
        isinstance(node, ast.Attribute) and node.attr == holder and isinstance(node.value, ast.Name)
        and (receivers is None or node.value.id in receivers)
    )


def _holder_writes(fn: ast.AST, holder: str, receivers: frozenset[str] | None) -> tuple[set[str], bool]:
    """The constant keys ``fn`` stores onto ``<receiver>.<holder>`` (attribute or subscript store,
    ``setattr(<holder>, 'key', …)``), and whether it stores onto it at all — a computed key or an
    ``.update(...)`` counts as a store with no key the AST can name."""
    keys: set[str] = set()
    stored = False
    for n in ast.walk(fn):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign)) else []
        for t in targets:
            if isinstance(t, ast.Attribute) and _on_holder(t.value, holder, receivers):
                keys.add(t.attr)
                stored = True
            elif isinstance(t, ast.Subscript) and _on_holder(t.value, holder, receivers):
                stored = True
                if isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                    keys.add(t.slice.value)
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Name) and n.func.id == 'setattr' and n.args and _on_holder(n.args[0], holder, receivers):
            stored = True
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                keys.add(n.args[1].value)
        elif isinstance(n.func, ast.Attribute) and n.func.attr == 'update' and (
            _on_holder(n.func.value, holder, receivers)
            or (
                isinstance(n.func.value, ast.Call) and _dotted(n.func.value.func) == 'vars' and n.func.value.args
                and _on_holder(n.func.value.args[0], holder, receivers)
            )
        ):
            stored = True
    return keys, stored


def _holder_reads(tree: ast.AST, key: str, holder: str, receivers: frozenset[str] | None) -> list[int]:
    """Lines reading ``key`` off ``<receiver>.<holder>``: an attribute load, ``getattr``, or a subscript / ``.get``."""
    lines: list[int] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) and n.attr == key and _on_holder(n.value, holder, receivers):
            lines.append(n.lineno)
        elif (
            isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and _on_holder(n.value, holder, receivers)
            and isinstance(n.slice, ast.Constant) and n.slice.value == key
        ):
            lines.append(n.lineno)
        elif isinstance(n, ast.Call) and n.args and (
            (isinstance(n.func, ast.Name) and n.func.id == 'getattr' and _on_holder(n.args[0], holder, receivers))
            or (isinstance(n.func, ast.Attribute) and n.func.attr == 'get' and _on_holder(n.func.value, holder, receivers))
        ):
            probe = n.args[1] if isinstance(n.func, ast.Name) and len(n.args) > 1 else n.args[0]
            if isinstance(probe, ast.Constant) and probe.value == key:
                lines.append(n.lineno)
    return lines


def _param_names(fn: ast.AST) -> frozenset[str]:
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return frozenset()
    a = fn.args
    return frozenset(x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs])


@dataclass(frozen=True)
class _StateWrite:
    """How one framework's tool writes AGENT state, read off the AST — a row field of :data:`_FRAMEWORKS`.

    ``tool_keys(fn)`` is the keys a real tool body writes; ``wrapper_drops(wrappers, every_key)`` the
    ``(line, what it does)`` places a mock dispatch wrapper's success path leaves the declared keys
    unlanded; ``reads(tree, key)`` the lines reading ``key`` back. ``spelling`` names the write in
    messages and ``land_fix`` is how a wrapper lands ``_state_update(tool_name, result)``.
    """

    spelling: str
    tool_keys: Callable[[ast.AST], set[str]]
    wrapper_drops: Callable[[list[Any], frozenset[str]], list[tuple[int, str]]]
    reads: Callable[[ast.AST, str], list[int]]
    land_fix: str


def _holder_state_write(holder: str, receiver: str | None, spelling: str, land_fix: str) -> _StateWrite:
    """A framework whose tools store state as attributes of ``<receiver>.<holder>`` — the tool's own
    parameter when ``receiver`` is None (a run-context wrapper), else that name (``self``)."""
    fixed = frozenset({receiver}) if receiver else None

    def tool_keys(fn: ast.AST) -> set[str]:
        return _holder_writes(fn, holder, fixed if fixed is not None else _param_names(fn))[0]

    def wrapper_drops(wrappers: list[Any], _every_key: frozenset[str]) -> list[tuple[int, str]]:
        if any(_holder_writes(w, holder, fixed)[1] for w in wrappers):
            return []
        return [(wrappers[0].lineno, f'never writes onto `<{receiver or "ctx"}>.{holder}`')]

    return _StateWrite(
        spelling=spelling,
        tool_keys=tool_keys,
        wrapper_drops=wrapper_drops,
        reads=lambda tree, key: _holder_reads(tree, key, holder, fixed),
        land_fix=land_fix,
    )


_LANGGRAPH_STATE_WRITE = _StateWrite(
    spelling='Command(update=...)',
    tool_keys=lambda fn: _command_update_keys(fn),
    wrapper_drops=_command_wrapper_drops,
    reads=lambda tree, key: _state_reads(tree, key),
    land_fix='`Command(update={**_state_update(tool_name, result), "messages": [...]})`',
)


@dataclass(frozen=True)
class _AgentFramework:
    """One agent framework this verifier recognises by name, and what it needs configured.

    Some checks only make sense once some framework is in play — cross-turn state
    retention and the native tool trail — and each has a framework-specific fix
    behind an identical failure. The framework is therefore identified once and
    each check reads the field it needs. Adding a framework means adding a
    row here; nothing below hardcodes a particular one.
    """

    label: str
    #: Top-level import roots. Matching one is proof on its own, so a root that an
    #: ordinary application package might also claim does not belong here.
    import_roots: frozenset[str]
    #: Identifiers distinctive enough to stand alone, covering the re-export case
    #: where the framework import lives in the agent's package, not in simulate.py.
    symbols: frozenset[str]
    #: ``(receiver, methods)`` pairs — the framework's characteristic invocation.
    call_shapes: tuple[tuple[str, frozenset[str]], ...]
    #: Identifiers proving cross-turn state survives between invocations.
    persistence_symbols: frozenset[str]
    #: ``agent.framework`` values in ``plurai.config.yaml`` that name this row (normalised by
    #: ``host_effects.framework_id``). A harness often imports the customer's agent package, not the
    #: framework, so the AST cannot see it.
    config_ids: frozenset[str]
    #: Declared framework ids (same normalisation) that own their own tool channel and so
    #: override an AST match on this row: the skill lane builds a ClaudeSDKClient whose tools
    #: are mocks it sees. Any other declaration, free text included, falls back to the AST.
    defers_to: frozenset[str]
    #: How the framework's tools write agent state, or ``None`` when no rule is written down: the
    #: state-update checks then say they could not run, naming the framework.
    state_write: _StateWrite | None


# Every framework whose persistence handle we can name. Detection is
# structural (imports, distinctive symbols, invocation shapes), so prose naming a
# framework in a comment or docstring can never trigger a check — the AST does not
# carry it. A harness matching no row is framework-free as far as these checks are
# concerned: it has nothing to advise, so these checks stay silent rather than
# emitting the false positive that a blanket check produced.
_FRAMEWORKS: tuple[_AgentFramework, ...] = (
    _AgentFramework(
        label='LangGraph',
        import_roots=frozenset({'langgraph'}),
        symbols=frozenset({
            'StateGraph', 'MessageGraph', 'CompiledStateGraph', 'CompiledGraph',
            'create_react_agent', 'MemorySaver', 'GraphRecursionError',
        }),
        call_shapes=(),
        persistence_symbols=frozenset({
            'MemorySaver', 'InMemorySaver', 'SqliteSaver', 'AsyncSqliteSaver',
            'PostgresSaver', 'AsyncPostgresSaver', 'RedisSaver', 'AsyncRedisSaver',
        }),
        config_ids=frozenset({'langgraph'}),
        defers_to=frozenset(),
        state_write=_LANGGRAPH_STATE_WRITE,
    ),
    _AgentFramework(
        label='CrewAI',
        import_roots=frozenset({'crewai'}),
        symbols=frozenset({'CrewBase'}),
        call_shapes=(),
        persistence_symbols=frozenset({'LongTermMemory', 'ShortTermMemory', 'EntityMemory'}),
        config_ids=frozenset({'crewai'}),
        defers_to=frozenset(),
        # A Flow's state: `self.state.<attr> = ...` in the method a tool reaches.
        state_write=_holder_state_write(
            'state', 'self', '`self.state.<attr> = ...`',
            '`for key, value in _state_update(tool_name, result).items(): setattr(self.state, key, value)`',
        ),
    ),
    _AgentFramework(
        label='OpenAI Agents SDK',
        import_roots=frozenset(),
        symbols=frozenset({'RunConfig', 'RunContextWrapper', 'RunResultStreaming'}),
        call_shapes=(('Runner', frozenset({'run', 'run_sync', 'run_streamed'})),),
        persistence_symbols=frozenset({'SQLiteSession'}),
        config_ids=frozenset({'openai_agents', 'openai_agents_sdk'}),
        defers_to=frozenset(),
        # The run context: a tool stores on its `RunContextWrapper` parameter's `.context`.
        state_write=_holder_state_write(
            'context', None, '`<ctx>.context.<attr> = ...`',
            '`for key, value in _state_update(tool_name, result).items(): setattr(ctx.context, key, value)`',
        ),
    ),
    _AgentFramework(
        label='Claude Agent SDK',
        import_roots=frozenset({'claude_agent_sdk'}),
        symbols=frozenset({'ClaudeSDKClient', 'ClaudeAgentOptions'}),
        call_shapes=(),
        # The CLI session keeps the conversation; there is no saver to name.
        persistence_symbols=frozenset(),
        config_ids=frozenset({'claude_agent_sdk'}),
        defers_to=frozenset({'claude_skill'}),
        state_write=None,
    ),
)

#: Receiver names whose invocation marks a graph-driven agent we cannot attribute to
#: a row above.
_UNATTRIBUTED_GRAPH_RECEIVERS = frozenset({'graph'})
_GRAPH_INVOCATION_METHODS = frozenset({'invoke', 'ainvoke', 'stream', 'astream', 'run', 'arun'})

_ALL_PERSISTENCE_SYMBOLS = frozenset().union(*(fw.persistence_symbols for fw in _FRAMEWORKS))


def _matches_call_shape(node: ast.AST, receiver: str, methods: frozenset[str]) -> bool:
    """True for ``<receiver>.<method>(...)`` and ``<expr>.<receiver>.<method>(...)``.

    The second form is the wrapper case: the object is reached through an attribute
    (``self.graph.ainvoke``) rather than bound to a bare name.
    """
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in methods:
        return False
    target = node.func.value
    if isinstance(target, ast.Name):
        return target.id == receiver
    if isinstance(target, ast.Attribute):
        return target.attr == receiver
    return False


def _detect_framework(tree: ast.Module, declared: str | None = None) -> _AgentFramework | None:
    """The framework the config declares or the (merged) source structurally uses, or ``None``.

    A declared ``agent.framework`` naming a row's ``config_ids`` identifies it; one naming a row's
    ``defers_to`` rules that row out. Otherwise any ONE signal identifies a row: an import of one of its roots, a reference to one of its distinctive
    symbols, or one of its characteristic call shapes. Rows are tried in order and the first match
    wins, so a harness importing two frameworks is advised about the one declared first rather
    than about both.
    """
    if (row := _declared_row(declared)) is not None:
        return row
    declared_id = framework_id(declared) if declared else ''
    for framework in _FRAMEWORKS:
        if declared_id in framework.defers_to:
            continue  # the declared framework owns its own tool channel
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split('.')[0] in framework.import_roots for alias in node.names):
                    return framework
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None and node.module.split('.')[0] in framework.import_roots:
                    return framework
            elif isinstance(node, ast.Name):
                if node.id in framework.symbols:
                    return framework
            elif isinstance(node, ast.Attribute):
                if node.attr in framework.symbols:
                    return framework
            if any(_matches_call_shape(node, recv, methods) for recv, methods in framework.call_shapes):
                return framework
    return None


#: A value built fresh where it stands: a list no name holds, so nothing appended to it elsewhere can be in it.
_FRESH = (ast.List, ast.Tuple, ast.Dict, ast.Set, ast.Constant)


def _roots(node: ast.AST) -> set[str] | None:
    """The dotted names a value is reached from — ``{'trail'}`` for ``trail[start:]`` or
    ``list(trail[start:])``, ``{'_TRAILS'}`` for ``_TRAILS.setdefault(tid, [])``, the iterated value's
    for a comprehension, every argument's for a call. ``set()`` for a value built fresh
    (:data:`_FRESH`); ``None`` when no name can be read off it."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, _FRESH):
        return set()
    if isinstance(node, ast.Attribute):
        base = _roots(node.value)
        return None if base is None else {f'{name}.{node.attr}' for name in base}
    if isinstance(node, (ast.Subscript, ast.Await, ast.Starred)):
        return _roots(node.value)
    parts: list[ast.AST] = []
    if isinstance(node, ast.Call):
        parts = [*([node.func.value] if isinstance(node.func, ast.Attribute) else []), *node.args, *(k.value for k in node.keywords)]
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        parts = [generator.iter for generator in node.generators]
    found = [r for r in map(_roots, parts) if r is not None]
    return set().union(*found) if found else None


def _holders(tree: ast.Module) -> tuple[Callable[[ast.AST], set[str] | None], frozenset[str]]:
    """``value -> every name that can hold the object it is reached from`` (``None``: unreadable), and
    the names only ever bound to a value built fresh. Followed through each assignment
    (``trail = _TRAILS.setdefault(...)``, ``state = {'trail': trail}``, ``_STATE[tid] = {'trail': trail}``);
    scopes are merged, so a name bound anywhere is followed everywhere — it can only widen a match."""
    bound: dict[str, set[str]] = {}
    fresh_only: dict[str, bool] = {}
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) and node.value else []
        value = getattr(node, 'value', None)
        values = [v for v in value.values if v is not None] if isinstance(value, ast.Dict) and value.values else [value]
        for target in targets:
            for name in _roots(target) or ():
                for v in values:
                    roots = _roots(v) if v is not None else None
                    bound.setdefault(name, set()).update(roots or ())
                    fresh_only[name] = fresh_only.get(name, True) and isinstance(v, ast.List) and not v.elts

    def holders(value: ast.AST) -> set[str] | None:
        roots = _roots(value)
        if roots is None:
            return None
        seen: set[str] = set()
        pending = list(roots)
        while pending:
            name = pending.pop()
            if name not in seen:
                seen.add(name)
                pending.extend(bound.get(name, ()))
        return seen

    return holders, frozenset(name for name, only in fresh_only.items() if only)


def _returned_values(tree: ast.Module, key: str) -> list[ast.AST]:
    """What the (merged) source puts under ``key`` in a dict — a literal's key, a ``d[key] = ...`` store
    or a ``key=`` keyword (``out.update(...)``, ``dict(...)``) — the way a returned turn carries it. Any
    other string, a comment or a docstring is not one."""
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            out += [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value == key]
        elif isinstance(node, ast.Assign):
            out += [node.value for t in node.targets if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == key]
        elif isinstance(node, ast.Call):
            out += [keyword.value for keyword in node.keywords if keyword.arg == key]
    return out


def _confined_trails(tree: ast.Module) -> list[ast.AST]:
    """The ``trail=`` values the (merged) source passes ``confine`` (``plurai.confine`` or a bare
    imported name): the lists the one recorder of a CLI agent's tool calls appends to."""
    return [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)) == 'confine'
        for keyword in node.keywords
        if keyword.arg == 'trail'
    ]


def _native_trail_gaps(tree: ast.Module) -> tuple[list[str], list[str]]:
    """``(proven, unconfirmed)``: what the harness of a bench declaring ``agent.native_tools`` leaves
    out of the recorded trail lane — confine passed a trail, that same list returned as ``tool_trail``,
    and the record returned as ``host_effects`` (its ``trail: recorded`` stamp is the lane's
    declaration). A mismatch is PROVEN only when confine gets a list built fresh at the call, or every
    returned value is one — built fresh, or a name only ever bound to ``[]`` — none of it the recorded
    list. A returned value no name can be read off (a helper's parameter) is unconfirmed, not a gap."""
    holders, fresh_names = _holders(tree)
    confined, returned = _confined_trails(tree), _returned_values(tree, 'tool_trail')
    proven, unconfirmed = [], []
    if not confined:
        proven.append("never passes `plurai.confine(..., trail=...)` the thread's trail")
    if not returned:
        proven.append('returns no `tool_trail`')
    if confined and returned:
        recorded = set().union(*(holders(v) or set() for v in confined))
        reads = [holders(v) for v in returned]
        if not recorded:
            proven.append('passes `plurai.confine(trail=...)` a list built at the call, which nothing returns')
        elif not any(read and read & recorded for read in reads):
            if all(read is not None and read <= fresh_names for read in reads):
                proven.append('returns as `tool_trail` a list other than the one it passes `plurai.confine(trail=...)`')
            else:
                unconfirmed.append('`tool_trail` is the list it passes `plurai.confine(trail=...)`')
    if not _returned_values(tree, 'host_effects'):
        proven.append("returns no `host_effects` (confine's record, whose `trail: recorded` stamp declares the lane)")
    return proven, unconfirmed


def _has_unattributed_graph_call(tree: ast.Module) -> bool:
    """True for a graph-shaped invocation belonging to no framework we can name.

    ``graph.ainvoke(...)`` / ``self.graph.run(...)`` prove a graph runs the agent,
    which is enough to ask whether its state survives the turn, and not enough to
    name the framework's persistence handle.
    """
    return any(
        _matches_call_shape(node, receiver, _GRAPH_INVOCATION_METHODS)
        for node in ast.walk(tree)
        for receiver in _UNATTRIBUTED_GRAPH_RECEIVERS
    )


def _uses_agent_framework(tree: ast.Module) -> bool:
    """True when a framework the cross-turn check covers (one with a persistence handle to name)
    is recognised, or an unattributed graph drives the agent. A row with none of those, such as
    the Claude Agent SDK's, is left to the stateless check."""
    framework = _detect_framework(tree)
    return (framework is not None and bool(framework.persistence_symbols)) or _has_unattributed_graph_call(tree)


def _invoke_reuses_checkpointerless_graph(
    invoke: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module
) -> int | None:
    """Heuristic for the cross-turn-state-loss footgun.

    ``invoke_agent`` reuses a pre-compiled graph via ``X.graph.ainvoke``/
    ``X.graph.invoke`` while simulate.py builds NO persistence handle for it.
    The orchestrator passes a stable per-scenario ``thread_id`` every turn, but
    without somewhere to persist that is a no-op — so any non-message agent state
    written in turn N (a selected record id, a cached plan, a `verified` flag) is
    discarded before turn N+1 and a multi-turn agent spuriously fails. Returns
    the offending call line, else None. Warn-severity: single-turn /
    invoke_pipeline benches legitimately keep nothing between turns.
    """
    graph_call_line: int | None = None
    for node in ast.walk(invoke):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ('ainvoke', 'invoke')
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == 'graph'
        ):
            graph_call_line = node.lineno
            break
    if graph_call_line is None:
        return None
    # Does the module build a persistence handle any known framework recognises, or
    # pass checkpointer=... to .compile(...)? Either one keeps cross-turn state. The
    # set is the union across frameworks: an on-disk saver persists just as well as
    # an in-memory one, and matching a single spelling warns at a harness that is
    # already doing the right thing.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)) and getattr(node, 'id', getattr(node, 'attr', '')) in _ALL_PERSISTENCE_SYMBOLS:
            return None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'compile'
            and any(kw.arg == 'checkpointer' for kw in node.keywords)
        ):
            return None
    return graph_call_line


def _invoke_treats_payload_as_history(
    invoke: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module
) -> int | None:
    """Framework-free analogue of the checkpointerless check.

    The payload contract sends ONE new user message per turn; a harness that derives
    conversation history from ``payload['messages']`` — iterating it, slicing beyond a
    single element, or passing the whole list into its own prompt build — runs STATELESS
    (every turn sees [system, user]) unless it also keeps a thread-keyed store. Heuristic:
    a for-loop / non-trivial slice / whole-list use of the payload messages, with NO read
    of ``thread_id`` anywhere in the module (the only key a cross-turn store can use).
    Reading only ``[-1]`` / ``[0]`` (the new message) is the correct shape and passes.
    Returns the offending line, else None. Warn-severity: the graph-framework path is
    covered by ``checkpointerless_graph_reuse``, so this fires only when no graph
    framework is in play.
    """

    def _is_payload_messages(node: ast.AST) -> bool:
        # payload['messages'] or a name bound from it (msgs = payload['messages'])
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == 'messages'
        )

    # Names bound to payload['messages'] inside invoke_agent.
    aliases: set[str] = set()
    for node in ast.walk(invoke):
        if isinstance(node, ast.Assign) and _is_payload_messages(node.value):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))

    def _is_history_source(node: ast.AST) -> bool:
        return _is_payload_messages(node) or (isinstance(node, ast.Name) and node.id in aliases)

    offending: int | None = None
    for node in ast.walk(invoke):
        # for m in payload['messages']: ... — iterating THIS turn's user side as history.
        if isinstance(node, (ast.For, ast.AsyncFor)) and _is_history_source(node.iter):
            offending = node.lineno
            break
        # payload['messages'][1:] / [:-1] — slicing beyond the single new message.
        if isinstance(node, ast.Subscript) and _is_history_source(node.value) and isinstance(node.slice, ast.Slice):
            offending = node.lineno
            break
    if offending is None:
        return None
    # A module that reads thread_id anywhere has the key a cross-turn store needs —
    # assume it keeps one (checking the store itself is beyond a static pass).
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == 'thread_id':
            return None
    return offending


_SAVED_STATE_READS = frozenset({'get_state', 'aget_state'})


def _invoke_returns_picked_state(invoke: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """Line of a ``return {...}`` in ``invoke_agent`` that picks keys out of the thread's saved state
    (``get_state(...)`` / ``aget_state(...)`` ``.values``) with no ``**`` spread, else None.

    The return is the turn's ``agent_state``, and a regression pin's ``agent_state`` is exactly that,
    so a picked subset resumes the agent into a state production never reaches. A CODE SHAPE, never a
    key list: which keys the agent routes on is not knowable here, and spreading the values is the fix
    whatever they are. A returned ``app.invoke(...)`` result is not a dict literal and never fires."""

    def _reads_saved_state(node: ast.AST) -> bool:
        node = node.value if isinstance(node, ast.Await) else node
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _SAVED_STATE_READS

    def _assigned(pred: Callable[[ast.expr], bool]) -> set[str]:
        return {t.id for n in ast.walk(invoke) if isinstance(n, ast.Assign) and pred(n.value) for t in n.targets if isinstance(t, ast.Name)}

    snapshots = _assigned(_reads_saved_state)

    def _reads_values(expr: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Attribute) and n.attr == 'values'
            and ((isinstance(n.value, ast.Name) and n.value.id in snapshots) or _reads_saved_state(n.value))
            for n in ast.walk(expr)
        )

    values = _assigned(_reads_values)

    def _picks(expr: ast.AST) -> bool:
        return _reads_values(expr) or any(isinstance(n, ast.Name) and n.id in values for n in ast.walk(expr))

    for node in ast.walk(invoke):
        if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)) or None in node.value.keys:
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if not (isinstance(key, ast.Constant) and key.value == 'messages') and _picks(value):
                return node.lineno
    return None


def _str_set(node: ast.expr) -> frozenset[str] | None:
    """The strings of a literal tuple / list / set (bare, or wrapped in ``frozenset`` / ``set`` / ``tuple``), else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('frozenset', 'set', 'tuple') and len(node.args) == 1:
        node = node.args[0]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and node.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return frozenset(e.value for e in node.elts)
    return None


def _reached_functions(functions: dict[str, ast.AST], roots: tuple[str, ...]) -> dict[str, str]:
    """Each module function the ``roots`` reach by name (called, awaited, or handed to ``to_thread`` / a callback),
    transitively, mapped to the root it was first reached from. The roots themselves are not included."""
    reached: dict[str, str] = {}
    for root in roots:
        todo = [root]
        while todo:
            fn = functions.get(todo.pop())
            for n in ast.walk(fn) if fn is not None else ():
                if isinstance(n, ast.Name) and n.id in functions and n.id not in roots and n.id not in reached:
                    reached[n.id] = root
                    todo.append(n.id)
    return reached


def _seed_state_slots(tree: ast.Module, seed: ast.FunctionDef | ast.AsyncFunctionDef, *, seed_key_only: bool = False) -> frozenset[str]:
    """The ``agent_state`` keys ``seed_state`` keeps a slot for: the strings of a literal collection (inline, or bound at
    module scope) that a loop or comprehension reading the seed's ``agent_state`` iterates, or tests a key ``in`` and
    then restores under that key (``if k in C: state[k] = ...``, ``{k: v for k, v in ... if k in C}``). A ``for`` counts
    only when its body reads or stores by the key; a skip (``continue``), a ``del``, a discarded ``pop``, a value filter
    and ``not in`` are not slots. Empty when it restores any other way. ``seed_key_only`` (a function other than
    ``seed_state``): only a read of the ``'agent_state'`` key counts, never a local that merely shares the name."""
    consts = {n.targets[0].id: s for n in tree.body if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and (s := _str_set(n.value))}
    assigned = {
        t.id for n in ast.walk(seed) if isinstance(n, ast.Assign) and any(isinstance(c, ast.Constant) and c.value == 'agent_state' for c in ast.walk(n.value))
        for t in n.targets if isinstance(t, ast.Name)
    }
    marks = {'agent_state'} | assigned
    named = assigned if seed_key_only else marks

    def reads_state(node: ast.AST) -> bool:
        return any((isinstance(c, ast.Constant) and c.value in marks) or (isinstance(c, ast.Name) and c.id in named) for c in ast.walk(node))

    def tested_key(test: ast.expr) -> tuple[str, ast.expr] | None:
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In) and isinstance(test.left, ast.Name):
            return test.left.id, test.comparators[0]
        return None

    def keyed(stmts: list[ast.stmt], name: str) -> bool:
        discarded = {id(n.value) for stmt in stmts for n in ast.walk(stmt) if isinstance(n, ast.Expr)}
        return any(
            (isinstance(n, ast.Subscript) and not isinstance(n.ctx, ast.Del) and isinstance(n.slice, ast.Name) and n.slice.id == name)
            or (isinstance(n, ast.Call) and id(n) not in discarded and isinstance(n.func, ast.Attribute) and n.func.attr in ('get', 'pop')
                and bool(n.args) and isinstance(n.args[0], ast.Name) and n.args[0].id == name)
            for stmt in stmts for n in ast.walk(stmt)
        )

    found: list[ast.expr] = []
    for loop in ast.walk(seed):
        if isinstance(loop, (ast.For, ast.AsyncFor)) and reads_state(loop):
            if isinstance(loop.target, ast.Name) and keyed(loop.body, loop.target.id):
                found.append(loop.iter)
            for branch in (n for stmt in loop.body for n in ast.walk(stmt) if isinstance(n, ast.If)):
                if (hit := tested_key(branch.test)) and any(
                    isinstance(s, ast.Subscript) and not isinstance(s.ctx, ast.Del) and isinstance(s.slice, ast.Name) and s.slice.id == hit[0]
                    for stmt in branch.body for s in ast.walk(stmt)
                ):
                    found.append(hit[1])
        elif isinstance(loop, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)) and reads_state(loop):
            key = loop.key if isinstance(loop, ast.DictComp) else loop.elt
            for gen in loop.generators:
                found.append(gen.iter)
                found += [hit[1] for test in gen.ifs if (hit := tested_key(test)) and isinstance(key, ast.Name) and key.id == hit[0]]
    return frozenset().union(*(consts.get(e.id, frozenset()) if isinstance(e, ast.Name) else (_str_set(e) or frozenset()) for e in found))


def _literal_keys(node: ast.expr) -> set[str] | None:
    if isinstance(node, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
        return {k.value for k in node.keys}
    return None


def _returned_keys(fn: ast.FunctionDef | ast.AsyncFunctionDef, functions: dict[str, ast.AST], hop: bool = True) -> frozenset[str] | None:
    """The top-level keys ``fn`` returns, when every ``return`` is a dict literal with string keys, a name used only as
    ``name = {...}`` / ``name['<key>'] = ...`` / ``return name``, or (one hop) a call into a module function, direct or
    handed to ``to_thread`` / ``run_in_executor``. Any other shape (a ``**`` spread, a framework's result,
    another use of the name) is unknown: None."""
    keys: set[str] = set()
    returned_names = {id(n.value) for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Name)}
    for ret in ast.walk(fn):
        if not isinstance(ret, ast.Return) or ret.value is None or (isinstance(ret.value, ast.Constant) and ret.value.value is None):
            continue
        value = ret.value.value if isinstance(ret.value, ast.Await) else ret.value
        got: set[str] | frozenset[str] | None = _literal_keys(value)
        if isinstance(value, ast.Call):
            offloaded = isinstance(value.func, ast.Attribute) and value.func.attr in ('to_thread', 'run_in_executor')
            target = next((a for a in value.args if isinstance(a, ast.Name)), None) if offloaded else value.func
            callee = target.id if hop and isinstance(target, ast.Name) and target.id in functions else None
            got = _returned_keys(functions[callee], functions, hop=False) if callee else None
        elif isinstance(value, ast.Name):
            got, allowed = set(), set(returned_names)
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == value.id for t in node.targets):
                    literal = _literal_keys(node.value)
                    got = None if literal is None or got is None else got | literal
                    allowed |= {id(t) for t in node.targets}
                elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == value.id:
                    key = node.slice.value if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) else None
                    got = None if key is None or got is None else got | {key}
                    allowed.add(id(node.value))
            if any(isinstance(n, ast.Name) and n.id == value.id and id(n) not in allowed for n in ast.walk(fn)):
                got = None
        if not got:
            return None
        keys |= got
    return frozenset(keys) or None


def _assistant_message_without_model_metadata(invoke: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    """Line of an assistant message dict literal that ``invoke_agent`` RETURNS with neither a model nor usage, else None.

    The turn's chain is what a trace sink draws its model calls from, and a hand-built
    ``{'role': 'assistant', 'content': reply}`` gives it a call with no model name and no token count.
    A CODE SHAPE: a literal with ``role: 'assistant'``, no ``**`` spread, and none of the
    ``response_metadata`` / ``usage_metadata`` / ``model`` keys, that reaches a ``return``: written in the
    returned expression, or flowing into a name it returns (assigned, ``+=``, ``append`` / ``extend`` /
    ``insert``, through other names too). A literal that only feeds the provider (the ``history`` a
    hand-written loop rebuilds and passes to the model) is not the chain and never fires; neither does a
    message built through the ``_model_calls`` helpers, which is a call, not a literal."""
    flows: dict[str, list[ast.expr]] = {}  # name -> the expressions whose value lands in it
    for node in ast.walk(invoke):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    flows.setdefault(target.id, []).append(node.value)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name) and node.value is not None:
            flows.setdefault(node.target.id, []).append(node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.attr in ('append', 'extend', 'insert')
        ):
            flows.setdefault(node.func.value.id, []).extend(node.args)

    def reached(expr: ast.AST) -> Iterator[ast.AST]:
        """The nodes whose value can be part of ``expr``'s: a call's arguments are not, except a copy's."""
        yield expr
        if isinstance(expr, ast.Call):
            children: list[ast.AST] = [expr.func]
            if isinstance(expr.func, ast.Name) and expr.func.id in _VALUE_PRESERVING_CALLS:
                children += expr.args
        else:
            children = list(ast.iter_child_nodes(expr))
        for child in children:
            yield from reached(child)

    stack: list[ast.AST] = [node.value for node in ast.walk(invoke) if isinstance(node, ast.Return) and node.value is not None]
    seen: set[str] = set()
    lines: list[int] = []
    while stack:
        for node in reached(stack.pop()):
            if isinstance(node, ast.Name) and node.id not in seen:
                seen.add(node.id)
                stack.extend(flows.get(node.id, ()))
            elif isinstance(node, ast.Dict) and None not in node.keys:
                keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                role = keys.get('role')
                if isinstance(role, ast.Constant) and role.value == 'assistant' and not keys.keys() & {'response_metadata', 'usage_metadata', 'model'}:
                    lines.append(node.lineno)
    return min(lines, default=None)


#: Calls whose result carries their argument's value, so a literal passed to one still reaches the return.
_VALUE_PRESERVING_CALLS = frozenset({'list', 'tuple', 'copy', 'deepcopy'})


def _factory_assignments(tree: ast.Module) -> list[ast.Assign]:
    """Detect storage / repository / dataset factory monkey-patches.

    Heuristic: any module-level Assign where the target is an attribute access
    matching ``*_factory.create_*`` or ``factory.create_*_storage``. This is
    the canonical pattern for swapping production storage with a stub.

    False positives are possible but rare — contract checks tied to a
    storage-factory assignment now are: (a) seed_state must be defined when
    storage is patched, and (b) at least one class in the source must have a
    method body that calls ``plurai.world_state.{get_entity, query, apply}``
    (otherwise the patched factory returns a class whose reads bypass the
    mock service, and the agent runs against silently-empty storage).
    """
    matches: list[ast.Assign] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            attr = target.attr
            if attr.startswith('create_') and (attr.endswith('_storage') or 'storage' in attr.lower()):
                matches.append(node)
                break
    return matches


def _declared_fields_not_read_by_storage(tree: ast.Module, declared: tuple[frozenset[str], ...]) -> list[str]:
    """Declared record fields no storage class reads under ANY spelling.

    The shape arm below asks whether the storage class touches world-state at all. This asks the
    next question, which nothing asked: does what it reads BACK carry the fields the seed wrote?
    one harness's ``_to_record`` returned a narrower record view than the seed wrote, so a seeded
    ``user_feedback`` was unreadable on the way in and dropped on the way out — the agent could not
    see the premise, manufactured it, and the run charged the agent for the write.

    Scoped PER DECLARED COLLECTION, and only to collections this class demonstrably projects: a
    collection none of whose keys are read is not this class's business (a label vocabulary, say),
    while one that is read in part but not in whole is the drop. That keeps the rule free of any
    field name while excluding what a flat union of the schema's key names would drag in.

    THE RULE, exactly: any DECLARED key name, read anywhere in any class body, by string literal,
    in any context. So a field read only on a WRITE path satisfies it (reading is what matters, not
    where) and a harness-internal key is never asked about — but a field carried under a
    NON-declared alias is still reported, because the declared name never appears. That is why this
    is a warn: ``raw.get('label')`` for a declared ``eval_result`` reads the value and trips the
    rule, and the remedy is a line in the bench or a dismissal, not a block.

    TWO FALSE NEGATIVES it does not claim to catch: a projection written as a module-level function
    rather than a method (only ``ClassDef`` bodies are walked), and a field reached by attribute
    access or ``getattr`` rather than a string key.
    """
    read: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in ('get', 'pop'):
                read |= {a.value for a in sub.args[:1] if isinstance(a, ast.Constant) and isinstance(a.value, str)}
            elif isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant) and isinstance(sub.slice.value, str):
                read.add(sub.slice.value)
    return sorted({name for group in declared if group & read for name in group - read})


def _has_storage_class_using_world_state(tree: ast.Module) -> bool:
    """True when at least one class definition in the source has a method
    body calling ``plurai.world_state.X(...)``.

    Why this lives at class level (not "anywhere in source"): seed_state at
    module scope may legitimately write through ``plurai.world_state.apply``
    while a stub storage class returns hardcoded defaults from its reads.
    That shape is exactly the silent failure we want to catch — the seed
    populates world-state but tools never read it back. By scoping the
    check to class bodies, we require that the storage class itself
    participates in the round-trip.

    The check is intentionally lenient about *which* class: any class with a
    body referencing ``plurai.world_state.*`` satisfies it. Customers with
    multiple storage classes don't need each one to call world_state — the
    one bound by the patched factory does, and we can't statically resolve
    factory-RHS → class identity in the general case (lambdas, helper
    functions, conditional binding). Lenient is the right call: the smoke
    in Step 9f catches the residual "wrong class binds to factory" case.
    """
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Attribute):
                continue
            # Match `plurai.world_state.<anything>` — sub is the outer
            # Attribute (.X), sub.value is the inner Attribute (.world_state),
            # sub.value.value is the Name (`plurai`).
            inner = sub.value
            if (
                isinstance(inner, ast.Attribute)
                and isinstance(inner.value, ast.Name)
                and inner.value.id == 'plurai'
                and inner.attr == 'world_state'
            ):
                return True
    return False


#: The SDK's world reads and writes as a harness spells them: `plurai.world_state.<op>`, `ctx.world_state`,
#: `plurai.world_state_snapshot()`.
_WORLD_ACCESS_ATTRS = frozenset({'world_state', 'world_state_snapshot'})

#: The hooks the runner calls. A world access inside one is the hook's own job (seeding, snapshotting, resetting), never
#: store behaviour served to the agent.
_RUNNER_HOOKS = frozenset({'seed_state', 'world_snapshot', 'reset', 'probe_read', 'apply_user_action', 'invoke_agent', 'invoke_pipeline'})


def _mock_wrapped_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to a ``plurai.mock_wrap(...)`` (or bare ``mock_wrap(...)``) result: callables that route to a
    declared mock."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _calls_mock_wrap(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _is_mock_wrap_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == 'mock_wrap') or (isinstance(node.func, ast.Name) and node.func.id == 'mock_wrap'))


def _calls_mock_wrap(node: ast.AST) -> bool:
    return any(_is_mock_wrap_call(sub) for sub in ast.walk(node))


def _has_storage_class_delegating_to_mocks(tree: ast.Module, declared_mocks: frozenset[str]) -> bool:
    """True when some class body reaches a declared mock: it calls ``plurai.mock_wrap(...)``, names a module-level callable
    bound to one, or names a ``mode: mock`` tool of ``plurai.config.yaml``. The delegating storage the one-route rule asks
    for — a pass-through to seam mocks — never touches ``plurai.world_state`` itself, and it is as lenient about WHICH class
    as :func:`_has_storage_class_using_world_state`, for the same reason."""
    wrapped = _mock_wrapped_names(tree) | set(declared_mocks)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if _calls_mock_wrap(node):
            return True
        if any(isinstance(sub, ast.Name) and sub.id in wrapped for sub in ast.walk(node)):
            return True
        if any(isinstance(sub, ast.Attribute) and sub.attr in declared_mocks for sub in ast.walk(node)):
            return True
    return False


def _is_mock_handler(node: ast.AST) -> bool:
    """A ``@plurai.mock(...)`` / ``@mock(...)`` handler: the mock's own body, whose world reads are the mock's."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for deco in node.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        if (isinstance(target, ast.Attribute) and target.attr == 'mock') or (isinstance(target, ast.Name) and target.id == 'mock'):
            return True
    return False


def _module_statements(tree: ast.Module) -> list[ast.stmt]:
    """Module-scope statements, including those nested in a module-level ``if`` / ``try`` / ``with`` (never a def body)."""
    out: list[ast.stmt] = []
    pending = list(tree.body)
    while pending:
        stmt = pending.pop(0)
        out.append(stmt)
        if isinstance(stmt, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
            pending[0:0] = [*getattr(stmt, 'body', []), *getattr(stmt, 'orelse', []), *getattr(stmt, 'finalbody', []),
                            *(h for handler in getattr(stmt, 'handlers', []) for h in handler.body)]
    return out


def _imported_names(tree: ast.Module) -> set[str]:
    """Names a module-level import binds, the plurai SDK's excepted: what an assignment onto agent code is rooted at."""
    names: set[str] = set()
    for stmt in _module_statements(tree):
        if isinstance(stmt, ast.Import):
            names |= {(a.asname or a.name).split('.')[0] for a in stmt.names if a.name.split('.')[0] != 'plurai'}
        elif isinstance(stmt, ast.ImportFrom) and (stmt.module or '').split('.')[0] != 'plurai' and stmt.level == 0:
            names |= {a.asname or a.name for a in stmt.names}
    return names


def _attr_root(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _installs(tree: ast.Module) -> list[tuple[str, ast.AST, int]]:
    """(installed name, the value installed, line) for each harness object put over agent code at module scope: an
    assignment onto an attribute of an imported (non-plurai) module or class, ``sys.modules[...] = ``, or a ``setattr`` /
    ``patch`` call. A storage-factory patch (``*_factory.create_*storage*``) is the first shape."""
    imported = _imported_names(tree)
    out: list[tuple[str, ast.AST, int]] = []
    for stmt in _module_statements(tree):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            for target in stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]:
                if isinstance(target, ast.Attribute) and _attr_root(target) in imported:
                    out.append((target.attr, stmt.value, stmt.lineno))
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute) and target.value.attr == 'modules' \
                        and _attr_root(target.value) == 'sys':
                    key = target.slice.value if isinstance(target.slice, ast.Constant) else 'sys.modules'
                    out.append((str(key).rsplit('.', 1)[-1], stmt.value, stmt.lineno))
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for call in (n for n in ast.walk(stmt) if isinstance(n, ast.Call)):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else ''
            if name in ('setattr', 'patch', 'object') and call.args:
                leaf = next((a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)), '')
                values = [*call.args[1:], *(k.value for k in call.keywords)]
                out += [(str(leaf).rsplit('.', 1)[-1], v, call.lineno) for v in values]
    return out


def _harness_store_serves_world(tree: ast.Module, exempt: frozenset[str]) -> list[tuple[str, int]]:
    """(installed name, line) for each harness object installed over agent code (:func:`_installs`) that reaches
    ``plurai.world_state`` — in its own body or through a module-level helper of the same harness files — outside a mock
    handler and the runner hooks. A name in ``exempt`` (the leaf of a declared ``read_through.via`` / ``guards[].via``) is
    the declared overlay, never flagged. A tee (the mock, then the original) and a delegator never touch the world, so
    they stay silent."""
    defs: dict[str, ast.AST] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[stmt.name] = stmt
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    defs.setdefault(target.id, stmt.value)

    def reaches_world(start: ast.AST) -> bool:
        seen: set[int] = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if id(node) in seen or _is_mock_handler(node) or getattr(node, 'name', None) in _RUNNER_HOOKS or _is_mock_wrap_call(node):
                continue
            seen.add(id(node))
            if any(isinstance(sub, ast.Attribute) and sub.attr in _WORLD_ACCESS_ATTRS for sub in ast.walk(node)):
                return True
            pending += [defs[sub.id] for sub in ast.walk(node) if isinstance(sub, ast.Name) and sub.id in defs and defs[sub.id] is not node]
        return False

    return [(name, line) for name, value, line in _installs(tree) if name not in exempt and reaches_world(value)]


def _wrapper_drops_response_fields(tree: ast.Module) -> int | None:
    """Detect the silent-failure-cascade anti-pattern in mock-coroutine wrappers.

    Looks for the AST shape:

        <var>.get("message") or str(<var>)

    where the receiver name on the LEFT matches the argument name on the
    RIGHT — only flagged inside functions that ALSO contain a call to
    ``_resolve_mock_response``. The same-identifier requirement narrows
    the check to the actual anti-pattern: a literal "fall back to
    str(result) when .get('message') returns falsy", which renders the
    SDK's empty stub ``{}`` as the string ``'{}'`` and looks like
    "ambiguous empty success" to the agent's React loop.

    The Step 8.4 closure-tool dispatch wrapper requires:

      * Detect the SDK's error-stub marker ``_plurai_stub_error`` AND
        handler-returned ``status == 'error'``; emit
        ``ToolMessage(status='error', ...)`` so the agent stops retrying.
      * Render the full result as ``json.dumps(result)`` not just ``.message``.

    Returns the line number of the offending ``BoolOp`` when found, or
    ``None`` when the wrapper is clean.

    False-positive scope (verified by tests):
      * Same shape with DIFFERENT identifier on left vs right
        (``foo.get("message") or str(bar)``) is NOT flagged — only the
        same-receiver dual-use pattern that produces the silent-success
        collapse.
      * Same shape outside a function that calls ``_resolve_mock_response``
        (or a local helper one of those routes the result through) is NOT
        flagged — unrelated message-handling code stays clean.
    """
    # The RENDER SURFACE, not just the wrapper: the shape does the same damage when it sits
    # in the `_render_content` helper the envelope guidance tells you to write, and a check
    # scoped to the wrapper alone could be escaped by that one extra hop.
    for node in _render_surface(tree):
        # Walk the function body for ``X.get("message") or str(X)`` shapes.
        for sub in ast.walk(node):
            if not isinstance(sub, ast.BoolOp) or not isinstance(sub.op, ast.Or):
                continue
            # Need exactly two operands: a .get("message") call and a str(...) call.
            if len(sub.values) != 2:
                continue
            left, right = sub.values
            # left: <name>.get("message") — extract the receiver name
            if not (
                isinstance(left, ast.Call)
                and isinstance(left.func, ast.Attribute)
                and left.func.attr == 'get'
                and len(left.args) == 1
                and isinstance(left.args[0], ast.Constant)
                and left.args[0].value == 'message'
                and isinstance(left.func.value, ast.Name)
            ):
                continue
            left_name = left.func.value.id
            # right: str(<same name>) — bind the same-receiver constraint
            if not (
                isinstance(right, ast.Call)
                and isinstance(right.func, ast.Name)
                and right.func.id == 'str'
                and len(right.args) == 1
                and isinstance(right.args[0], ast.Name)
                and right.args[0].id == left_name
            ):
                continue
            return sub.lineno
    return None


#: The envelope kinds `agent.tool_content_envelope` may declare. `json` needs no wrapper
#: indirection, so it is verified like an absent declaration.
TOOL_CONTENT_ENVELOPE_KINDS = ('json', 'json_field', 'text')

#: The function the Step 8.4 template routes every mock return through. Kept only as the
#: name the FIX text tells an operator to write — the check itself reads the RENDERING, not
#: this name: a `_render_content` that is a bare `json.dumps(result)` is the very defect the
#: check exists to catch, and an honest envelope built inline is not a defect at all.
_ENVELOPE_RENDERER = '_render_content'

#: Calls that turn the whole result into text WITHOUT wrapping anything around it — the
#: `json` default's rendering. Passing through one is not yet a verdict: the walk keeps
#: going, because `json.dumps(result)` nested inside a dict/list IS an envelope.
#: `_tool_content` is the Step 8.4 template's one sanctioned selection: a success's `content`
#: IS the real tool's return, so taking it is not a dropped field.
_PASSTHROUGH_RENDERERS = frozenset({'json.dumps', 'json.dump', 'str', 'repr', 'ascii', '_tool_content'})

#: Calls that ask a QUESTION about the result rather than emitting it. A use that reaches
#: only one of these carries nothing to the model and casts no vote. ``plurai.mock_result`` is the
#: template's ``_error_message`` asking the SDK whether the call FAILED; the value it selects
#: reaches the model only through ``_tool_content``, which is read as the passthrough above.
_RESULT_PREDICATES = frozenset({'isinstance', 'len', 'type', 'bool', 'hasattr', 'callable', 'id', 'plurai.mock_result', 'mock_result'})

#: The transport the content is handed to, not the content itself. It is the BOUNDARY of the
#: walk: everything structural outside it (`Command(update={'messages': [ToolMessage(...)]})`)
#: is delivery plumbing, and counting those braces as an envelope would pass the exact
#: hard-coded template this check exists to fail.
_CONTENT_TRANSPORTS = frozenset({'ToolMessage', 'Command', 'AIMessage', 'HumanMessage', 'SystemMessage', 'FunctionMessage'})

#: Nodes that BUILD a structure around whatever they contain — the static signature of an
#: envelope. Reaching one from the result means the result is being wrapped, not passed through.
_STRUCTURE_NODES = (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.JoinedStr, ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp)

#: Nodes the walk passes straight through: they neither wrap nor select, they just carry.
_TRANSPARENT_NODES = (ast.Await, ast.IfExp, ast.BoolOp, ast.Starred, ast.keyword, ast.Return, ast.Assign, ast.AnnAssign, ast.Expr, ast.NamedExpr)


def _dotted(node: ast.AST) -> str:
    """``json.dumps`` for an Attribute chain, ``str`` for a Name, ``''`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f'{prefix}.{node.attr}' if prefix else node.attr
    return ''


#: The spawn calls that start a process the harness owns while the run continues, matched on the
#: trailing name so an alias (``import subprocess as sp``) is the same rule. ``subprocess.run`` /
#: ``check_output`` are deliberately absent: they wait for a short command and hand its output back
#: to the caller, so a ``DEVNULL`` there discards nothing anyone was going to read. A ``Popen``
#: waited on in the same function is that shape too (:func:`_is_short_command`).
_SPAWN_CALLS = frozenset({'Popen', 'create_subprocess_exec', 'create_subprocess_shell'})

#: The two sinks that lose a spawned process's output, what each one costs, and whether it is a
#: contract violation or a question. Matched on the trailing attribute name, so
#: ``subprocess.DEVNULL``, a bare ``DEVNULL`` and ``asyncio.subprocess.DEVNULL`` are one rule. Any
#: other sink — a file handle, ``sys.stderr``, ``subprocess.STDOUT`` beside a file — keeps the
#: output readable.
#:
#: ``PIPE`` is a WARN, not a fail, for the reason ``no_dotenv_load`` is: a drained pipe is a
#: legitimate setup. A shipped harness reads a bounded one-line handshake off
#: ``stdout=PIPE`` while its stderr goes to a log file, and this analyzer cannot see the reader.
#: What it CAN say is what an undrained one costs, which is the half that bit.
_LOST_OUTPUT_SINKS: dict[str, tuple[str, str]] = {
    'DEVNULL': (
        'warn',
        'discarded at the source — nothing is written anywhere, so when the agent answers a turn with a 500 its own log does not exist',
    ),
    'PIPE': (
        'warn',
        'piped to a reader that has to keep draining it. A harness that reads it during the boot wait and then stops loses the log '
        'after readiness — and once the pipe buffer fills (~64 KB on Linux) the SERVER ITSELF BLOCKS on its next write, which looks '
        'like a hung agent mid-dialog. Fine only if something drains it for the whole run AND writes it where you can read it after',
    ),
}

#: The two streams a spawn can lose, in the order a finding names them.
_SPAWN_STREAMS = ('stdout', 'stderr')


def _inherits_stdout(spawn: ast.Call) -> bool:
    """Does this spawn leave ``stdout`` on the parent's fd 1?

    The SDK spawns ``simulate.py`` with ``stdout=PIPE`` as the JSON-lines IPC channel, so a
    process that inherits fd 1 writes its log straight into the protocol: the parent raises
    "Child wrote non-JSON to stdout" and the scenario dies — strictly worse than a ``DEVNULL``,
    which at least lets the run finish. ``stderr`` has no such problem (the SDK tees the child's
    stderr to its own logger), which is why the contract offers it as a destination.

    Decided on the three spellings that mean "inherit" and NOTHING ELSE. An expression this
    analyzer cannot read — ``stdout=open(LOG, 'a')``, ``stdout=logs['server']`` — is not an
    inherited fd, and reading it as one would fail the harness the template asks for.
    """
    value = next((kw.value for kw in spawn.keywords if kw.arg == 'stdout'), None)
    return value is None or (isinstance(value, ast.Constant) and value.value in (None, 1)) or _dotted(value) == 'sys.stdout'


def _is_short_command(spawn: ast.Call, parents: dict[int, ast.AST], enclosing: ast.AST) -> bool:
    """Is this spawn a short command the harness waits for, rather than a server it leaves running?

    Decided on the WAIT, which is the only thing that distinguishes them in the source: a
    ``Popen`` whose handle is ``communicate()``d or ``wait()``ed is a command with a result, and
    its output was never going to outlive the call.

    Scoped to the spawn's OWN function. A long-lived server is waited on in a teardown — the
    shipped harnesses all have one — so a module-wide search reads every agent server as a short
    command and the whole check goes silent on exactly the shape it exists for.
    """
    if isinstance(parents.get(id(spawn)), ast.withitem):
        return True  # `with Popen(...) as p:` waits on exit, by contract
    assigned = parents.get(id(spawn))
    names = {t.id for t in getattr(assigned, 'targets', []) if isinstance(t, ast.Name)} if isinstance(assigned, ast.Assign) else set()
    return bool(names) and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ('communicate', 'wait')
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in names
        for node in ast.walk(enclosing)
    )


def _spawned_output_findings(tree: ast.Module) -> list[Finding]:
    """A server the HARNESS spawns must write where a human can read it afterwards.

    The SDK launches ``simulate.py`` and promotes THAT child's stderr; a process simulate.py
    starts is outside its observability entirely. So when the agent server answers a turn with a
    500, the message the harness raises is the whole record — in one observed case that record was
    ``POST /threads/<id>/turns -> 500: {"error":"internal error","kind":"unknown"}`` and the
    server's own traceback was in a ``DEVNULL``. The cause is still unknown, and would be for
    every such error, because the evidence is destroyed at spawn time.

    Matched on the sink's SPELLING, so an indirection — a ``sink = open(os.devnull)`` local, a
    ``**kwargs`` dict, a ``shell=True`` redirect — passes unseen. Left that way deliberately: this
    is a backstop for a generated harness that has a correct template beside it, not an adversary.
    """
    findings: list[Finding] = []
    parents = _parents(tree)
    for scope in [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call) and _dotted(node.func).rsplit('.', 1)[-1] in _SPAWN_CALLS):
                continue
            if _enclosing_function(node, parents) is not scope:
                continue  # counted in its own function's pass
            if _is_short_command(node, parents, scope):
                continue
            findings.extend(_spawn_findings(node))
    return findings


def _enclosing_function(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
    """The function a node sits in, or the module when it sits at top level."""
    current = parents.get(id(node))
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        current = parents.get(id(current))
    return current


def _spawn_findings(spawn: ast.Call) -> list[Finding]:
    """Every way ONE spawn loses its process's output, grouped so a reader gets one fact once."""
    findings: list[Finding] = []
    if _inherits_stdout(spawn):
        findings.append(Finding(
            severity='fail',
            code=Code.SPAWNED_STDOUT_ON_IPC_CHANNEL,
            message=(
                'this harness spawns a long-lived process without redirecting its `stdout`, so it inherits simulate.py\'s — '
                'which is the SDK\'s JSON-lines IPC channel, not a log. The first line that process prints makes the parent '
                'raise "Child wrote non-JSON to stdout" and the scenario dies. Give it the same log file you give `stderr` '
                '(open it once, append mode, under plurai_sim/artifacts/), or this process\'s stderr.'
            ),
            skill_step='Step 8',
            line=spawn.lineno,
        ))
    sinks = {kw.arg: _dotted(kw.value) for kw in spawn.keywords if kw.arg in _SPAWN_STREAMS}
    # Grouped by SINK, not by stream: `stdout=DEVNULL, stderr=DEVNULL` is one fact about one spawn
    # and two findings of it are the same sentence twice, while the incident's
    # `stdout=DEVNULL, stderr=PIPE` really is two different costs and stays two.
    lost: dict[str, list[str]] = {}
    for stream in _SPAWN_STREAMS:
        sink = sinks.get(stream, '').rsplit('.', 1)[-1]
        if sink in _LOST_OUTPUT_SINKS:
            lost.setdefault(sink, []).append(stream)
    for sink, streams in sorted(lost.items()):
        severity, cost = _LOST_OUTPUT_SINKS[sink]
        findings.append(Finding(
            severity=severity,
            code=Code.SPAWNED_AGENT_OUTPUT_LOST,
            message=(
                f'this harness spawns a process with `{" and ".join(f"{s}={sinks[s]}" for s in streams)}`, so its output is {cost}. '
                'A process YOUR harness starts is outside the SDK\'s observability — route BOTH streams to a file you control '
                '(open it once, append mode, under plurai_sim/artifacts/) or to this process\'s stderr, and quote that file\'s '
                'tail in the error you raise on a non-2xx reply.'
            ),
            skill_step='Step 8',
            line=spawn.lineno,
        ))
    return findings


def _local_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined in this module, by name — the set the walk may follow into.

    A call to one of these is INDIRECTION, not construction: `_render_content(result)` decides
    nothing by itself, which is precisely why checking for that name proved nothing.
    """
    out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node)
    return out


def _parents(node: ast.AST) -> dict[int, ast.AST]:
    """Child-id → parent map for one subtree (``ast`` keeps no back-references)."""
    out: dict[int, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            out[id(child)] = parent
    return out


def _carried_whole(value: ast.expr, local_fns: dict) -> set[str]:
    """The names ``value`` carries out WHOLE — the ones a new binding is another name for.

    A name that only has a field taken off it (``result.get("message")``) is NOT carried: the
    binding holds one field, not the result. Reading every mention as an alias is how the
    REQUIREMENT-2 error branch — ``err_msg = stub_err or result.get("message")``, rendered into
    ``f"Tool {tool!r} failed: {err_msg}"`` — voted 'wrapped' for the whole wrapper, so the
    envelope check passed the very template it grades whatever `_render_content` did.

    Nor is a name handed to a LOCAL function or to a type predicate: the classifier follows the
    first itself and the second carries nothing, so counting either would alias the RENDERED
    string (``content = _render_content(result)``) back onto the result.
    """
    parents = _parents(value)
    out: set[str] = set()
    for node in ast.walk(value):
        if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(id(node))
        if isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is node:
            continue
        if isinstance(parent, ast.Call) and node in parent.args:
            callee = _dotted(parent.func)
            if callee in local_fns or callee in _RESULT_PREDICATES:
                continue
        out.add(node.id)
    return out


def _aliases_of(fn: ast.AST, seeds: set[str], local_fns: dict) -> set[str]:
    """``seeds`` plus every local name assigned an expression that carries one of them whole.

    One hop is not enough in practice: the shipped `json_field` renderer binds
    ``payload = result if isinstance(result, dict) else {"value": result}`` and then places
    ``payload`` in the template, so the name that reaches the envelope is never the one the
    resolver returned. Iterated to a fixed point, which also covers a second rebinding.
    """
    aliases = set(seeds)
    for _ in range(6):
        grew = False
        for node in ast.walk(fn):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            else:
                continue
            mentioned = _carried_whole(node.value, local_fns) if node.value else set()
            if not (mentioned & aliases):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    grew = True
        if not grew:
            break
    return aliases


def _classify_use(origin: ast.AST, parents: dict[int, ast.AST], local_fns: dict, depth: int) -> str | None:
    """What happens to ONE occurrence of the mock result on its way to the model.

    Returns ``'wrapped'`` (a structure is built around it — an envelope exists),
    ``'bare'`` (the whole result reaches the content with nothing around it — the `json`
    default's rendering), ``'dropped'`` (a field is selected off it, so the full result never
    reaches the model), or ``None`` when this occurrence carries nothing to the model or the
    trail could not be followed.

    Walks OUTWARD from the occurrence and stops at the first decisive node. A transport call
    ends the walk rather than continuing through it, so the braces in
    ``Command(update={'messages': [...]})`` are never mistaken for the envelope.
    """
    cur: ast.AST = origin
    selectable = True  # a field taken HERE is taken off the result itself, not off a derived value
    for _ in range(40):
        parent = parents.get(id(cur))
        if parent is None:
            return 'bare'
        if selectable and isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is cur:
            return 'dropped'
        if isinstance(parent, _STRUCTURE_NODES):
            return 'wrapped'
        if isinstance(parent, ast.BinOp):
            return 'wrapped'  # string concatenation / %-formatting composes a shape
        if isinstance(parent, ast.Compare):
            return None  # a test, not output
        if isinstance(parent, (ast.Assign, ast.AnnAssign)) and _rebinds_a_name(parent):
            return None  # a rebinding: the verdict lives on the target, which the aliases carry
        if isinstance(parent, ast.Call):
            name = _dotted(parent.func)
            if name.endswith('.format'):
                return 'wrapped'
            if name in _RESULT_PREDICATES:
                return None
            if name in _PASSTHROUGH_RENDERERS:
                cur, selectable = parent, False
                continue
            if name in _CONTENT_TRANSPORTS:
                return 'bare'  # the boundary: nothing wrapped it before it became a message
            if depth < 3 and name in local_fns:
                return _classify_in(local_fns[name], _param_for(local_fns[name], parent, cur), local_fns, depth + 1)
            return 'wrapped'  # an unrecognised constructor is building something — never fail on a guess
        if isinstance(parent, _TRANSPARENT_NODES):
            cur = parent
            selectable = selectable and isinstance(parent, (ast.Await, ast.IfExp, ast.BoolOp))
            continue
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return 'bare'
        cur, selectable = parent, False
    return None


def _rebinds_a_name(assign: ast.AST) -> bool:
    """Does this assignment land on a plain local name (rather than a subscript / attribute)?"""
    targets = list(assign.targets) if isinstance(assign, ast.Assign) else [assign.target]
    return any(isinstance(target, ast.Name) for target in targets)


def _param_for(fn: ast.AST, call: ast.Call, arg: ast.AST) -> str | None:
    """The parameter name ``arg`` binds to in ``fn``, or ``None`` when it cannot be mapped."""
    args = getattr(fn, 'args', None)
    if args is None:
        return None
    positional = _positional_param_names(args)
    for index, value in enumerate(call.args):
        if value is arg:
            return positional[index] if index < len(positional) else None
    for keyword in call.keywords:
        if keyword.value is arg and keyword.arg:
            return keyword.arg
    return None


def _classify_in(fn: ast.AST, seed: str | None, local_fns: dict, depth: int) -> str | None:
    """The strongest verdict over every occurrence of ``seed`` (and its aliases) inside ``fn``.

    ``wrapped`` beats ``bare`` beats ``dropped``: one path that builds an envelope proves an
    envelope exists (the other occurrences are the same value being tested or re-bound), and
    ``dropped`` is claimed only when NO path carries the whole result out at all.
    """
    if seed is None:
        return None
    parents = _parents(fn)
    aliases = _aliases_of(fn, {seed}, local_fns)
    verdicts = {
        _classify_use(node, parents, local_fns, depth)
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load)
    }
    for verdict in ('wrapped', 'bare', 'dropped'):
        if verdict in verdicts:
            return verdict
    return None


def _mock_wrapper_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function that resolves a mock response — i.e. every dispatch wrapper."""
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        if '_resolve_mock_response' in names or '_resolve_mock_response' in attrs:
            out.append(node)
    return out


def _render_surface(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The dispatch wrappers PLUS the local helpers they route the result through.

    A check that read only the wrapper would miss the field-dropping shape lifted one level
    out — into the `_render_content` the envelope guidance tells you to write — while the
    damage is identical. Both checks read this one surface so neither can be escaped by an
    extra hop.
    """
    wrappers = _mock_wrapper_functions(tree)
    local_fns = _local_functions(tree)
    surface = list(wrappers)
    seen = {id(fn) for fn in surface}
    for wrapper in wrappers:
        for node in ast.walk(wrapper):
            if not isinstance(node, ast.Call):
                continue
            callee = local_fns.get(_dotted(node.func))
            if callee is not None and id(callee) not in seen:
                seen.add(id(callee))
                surface.append(callee)
    return surface


def _wrapper_render_verdicts(tree: ast.Module) -> list[tuple[ast.AST, str | None]]:
    """Per dispatch wrapper: what its rendering does to the resolved mock result."""
    local_fns = _local_functions(tree)
    out: list[tuple[ast.AST, str | None]] = []
    for wrapper in _mock_wrapper_functions(tree):
        parents = _parents(wrapper)
        verdicts: set[str | None] = set()
        for origin in ast.walk(wrapper):
            if not (isinstance(origin, ast.Call) and _dotted(origin.func).endswith('_resolve_mock_response')):
                continue
            # When the call is BOUND to a name, that name's occurrences are the rendering and
            # the assignment itself carries nothing — walking out of it would vote 'bare' for
            # every wrapper written in the ordinary two-statement style. Only an unbound
            # resolver call (`return _render_content(await _resolve_mock_response(...))`) is
            # read at the call site.
            seeds = _bound_names(origin, parents)
            if seeds:
                verdicts |= {_classify_in(wrapper, seed, local_fns, 0) for seed in seeds}
            else:
                verdicts.add(_classify_use(origin, parents, local_fns, 0))
        verdict = next((v for v in ('wrapped', 'bare', 'dropped') if v in verdicts), None)
        out.append((wrapper, verdict))
    return out


def _surface_verdicts(tree: ast.Module) -> set[str | None]:
    """EVERY verdict the mock result collects on the render surface, not just the strongest.

    ``_wrapper_render_verdicts`` collapses to one verdict per wrapper because its question is
    "does an envelope exist at all", and ``'wrapped'`` beating ``'bare'`` is the right answer to
    it. Idempotence is the opposite question — is there ALSO a path that hands the result back
    unwrapped — and a collapsed verdict cannot answer it.
    """
    local_fns = _local_functions(tree)
    out: set[str | None] = set()
    for wrapper in _mock_wrapper_functions(tree):
        parents = _parents(wrapper)
        seeds: set[str] = set()
        for origin in ast.walk(wrapper):
            if not (isinstance(origin, ast.Call) and _dotted(origin.func).endswith('_resolve_mock_response')):
                continue
            # Same split as `_wrapper_render_verdicts`: a BOUND call is read through the name it
            # binds, an unbound one (`return _render_content(await _resolve_mock_response(...))`)
            # at the call site. Skipping the unbound spelling left this set empty, so a renderer
            # whose guard is present and correct still collected no 'bare' — an unclearable fail.
            bound = _bound_names(origin, parents)
            if bound:
                seeds |= bound
            else:
                out |= _collect_use(origin, parents, local_fns, 0)
        if seeds:
            out |= _verdicts_in(wrapper, seeds, local_fns, 0)
    return out


def _collect_use(origin: ast.AST, parents: dict[int, ast.AST], local_fns: dict, depth: int) -> set[str | None]:
    """Every verdict ONE unbound occurrence collects — ``_classify_use`` without the collapse.

    Where the occurrence is handed to a local helper the walk descends into it with
    ``_verdicts_in``'s semantics; ``_classify_use`` would descend through ``_classify_in``, which
    collapses the helper's paths to one verdict before this pass ever saw them.
    """
    node: ast.AST = origin
    while isinstance(parents.get(id(node)), ast.Await):
        node = parents[id(node)]
    parent = parents.get(id(node))
    callee = local_fns.get(_dotted(parent.func)) if isinstance(parent, ast.Call) else None
    param = _param_for(callee, parent, node) if callee is not None else None
    if param is not None and depth < 3:
        return _verdicts_in(callee, {param}, local_fns, depth + 1)
    return {_classify_use(origin, parents, local_fns, depth)}


def _verdicts_in(fn: ast.AST, seeds: set[str], local_fns: dict, depth: int) -> set[str | None]:
    """Every verdict ``seeds`` and their aliases collect inside ``fn``.

    Where an occurrence is handed to a local helper the walk descends into the helper with the
    same collecting semantics, rather than through ``_classify_in``, which would collapse the
    helper's paths to one verdict before this pass ever saw them.
    """
    parents = _parents(fn)
    aliases = _aliases_of(fn, seeds, local_fns)
    out: set[str | None] = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Name) and node.id in aliases and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(id(node))
        callee = local_fns.get(_dotted(parent.func)) if isinstance(parent, ast.Call) else None
        param = _param_for(callee, parent, node) if callee is not None else None
        if param is not None and depth < 3:
            out |= _verdicts_in(callee, {param}, local_fns, depth + 1)
        else:
            out.add(_classify_use(node, parents, local_fns, depth))
    return out


def _bound_names(origin: ast.AST, parents: dict[int, ast.AST]) -> set[str]:
    """The local names ``origin``'s value is assigned to (through any ``await``), if any."""
    node: ast.AST = origin
    while isinstance(parents.get(id(node)), ast.Await):
        node = parents[id(node)]
    holder = parents.get(id(node))
    if isinstance(holder, ast.Assign):
        return {t.id for t in holder.targets if isinstance(t, ast.Name)}
    if isinstance(holder, ast.AnnAssign) and isinstance(holder.target, ast.Name):
        return {holder.target.id}
    return set()


def _idempotence_findings(tree: ast.Module, kind: str, wrapped: ast.AST) -> list[Finding]:
    """The envelope is applied — but is it applied to a result that already carries it?

    Only ``json_field`` builds anything, so only it can build it twice. A mock handler written
    against the agent's REAL tool contract returns the house envelope itself, and a renderer
    that wraps unconditionally then hands the model
    ``{"status": ..., "content": {"status": ..., "content": {...}}}`` — a shape neither the
    agent's tools nor its parser produce, arriving as a green run.

    The static signature of the guard is a PASS-THROUGH path: somewhere on the render surface
    the whole result reaches the content with nothing built around it, which is what
    "already enveloped, hand it back untouched" looks like in every spelling of it.
    """
    if kind != 'json_field' or 'bare' in _surface_verdicts(tree):
        return []
    return [Finding(
        severity='fail',
        code=Code.ENVELOPE_RENDER_NOT_IDEMPOTENT,
        message=(
            "The agent declares agent.tool_content_envelope kind 'json_field' and the mock "
            'dispatch wrapper applies the template UNCONDITIONALLY — so a mock handler that '
            "already returns the agent's own envelope gets wrapped a second time, and the "
            'model receives the template nested inside itself. Guard the wrap: a result that '
            'already matches the template shape passes through untouched (e.g. '
            '`if set(_ENVELOPE_TEMPLATE) <= set(result): return json.dumps(result, '
            'default=str)` before building it). The same read is what lets REQUIREMENT 2 see '
            "an error the handler put INSIDE the envelope. See Step 8.4's `_render_content`."
        ),
        skill_step='Step 8.4',
        line=wrapped.lineno,
    )]


def _envelope_findings(tree: ast.Module, kind: str | None) -> list[Finding]:
    """Does the wrapper's RENDERING match the agent's declared tool-content envelope?

    Skipped entirely when no kind is passed (this file cannot read plurai.config.yaml — see
    the note on check 9) and when the kind is ``json``, which IS the hard-coded behaviour.
    Anything the analysis cannot settle is a warn against the CONFIG, never a fail against
    the code — the same posture the unknown-kind branch already takes.

    The verdict is about what the content IS, not about which function produced it. ``text``
    and ``json`` render identically in the shipped templates (both
    ``json.dumps(result, default=str) if isinstance(result, dict) else str(result)``), so a
    bare render under ``text`` is reported as a config warning; only ``json_field`` — whose
    entire meaning is "the result sits INSIDE a template object" — can fail on it.
    """
    if kind is None:
        return []
    if kind not in TOOL_CONTENT_ENVELOPE_KINDS:
        return [Finding(
            severity='warn',
            code=Code.TOOL_CONTENT_ENVELOPE_UNKNOWN_KIND,
            message=(
                f'--tool-content-envelope {kind!r} is not one of '
                f'{", ".join(TOOL_CONTENT_ENVELOPE_KINDS)}. Envelope check skipped; fix '
                'agent.tool_content_envelope in plurai.config.yaml.'
            ),
            skill_step='Step 8.4',
        )]
    if kind == 'json':
        return []
    verdicts = _wrapper_render_verdicts(tree)
    if not verdicts:
        return []  # no dispatch wrapper in this layout — nothing to route
    wrapped = next((fn for fn, verdict in verdicts if verdict == 'wrapped'), None)
    if wrapped is not None:
        return _idempotence_findings(tree, kind, wrapped)

    dropped = next((fn for fn, verdict in verdicts if verdict == 'dropped'), None)
    if dropped is not None:
        return [Finding(
            severity='fail',
            code=Code.WRAPPER_IGNORES_TOOL_CONTENT_ENVELOPE,
            message=(
                f'The agent declares agent.tool_content_envelope kind {kind!r}, but the mock '
                'dispatch wrapper SELECTS a field off the mock result instead of wrapping it '
                '— so the model never sees the full result, and an empty or error-shaped stub '
                'renders as an ambiguous success. Whatever the kind, the envelope WRAPS the '
                f'full result and never selects a field: render it through one '
                f'`{_ENVELOPE_RENDERER}(result)` that places the WHOLE result in the declared '
                'shape. See Step 8.4 closure-tool dispatch wrapper.'
            ),
            skill_step='Step 8.4',
            line=dropped.lineno,
        )]

    bare = next((fn for fn, verdict in verdicts if verdict == 'bare'), None)
    if bare is not None and kind == 'json_field':
        return [Finding(
            severity='fail',
            code=Code.WRAPPER_IGNORES_TOOL_CONTENT_ENVELOPE,
            message=(
                "The agent declares agent.tool_content_envelope kind 'json_field' — the result "
                'belongs INSIDE the declared template object — but the mock dispatch wrapper '
                'hands back the bare result (a plain `json.dumps(result)` / `str(result)`, '
                'nothing wrapped around it). Mocked tools therefore give the model a payload '
                "shape the agent's real tools never produce, and the agent is measured against "
                'a contract it does not have. Build the declared template around the WHOLE '
                f'result — e.g. one `{_ENVELOPE_RENDERER}(result)` that places it at the '
                "template's `null`. See Step 8.4 closure-tool dispatch wrapper."
            ),
            skill_step='Step 8.4',
            line=bare.lineno,
        )]
    if bare is not None:
        return [Finding(
            severity='warn',
            code=Code.TOOL_CONTENT_ENVELOPE_RENDER_INDISTINGUISHABLE,
            message=(
                f"agent.tool_content_envelope declares kind {kind!r}, but the mock dispatch "
                "wrapper's rendering is the plain JSON/str render — byte-identical to kind "
                "'json', so nothing here distinguishes the declared envelope from the default. "
                'That is what the shipped template emits for this kind, so it is not a code '
                'defect: confirm agent.tool_content_envelope in plurai.config.yaml describes '
                "the agent's REAL tool returns, and drop the key if it has no convention."
            ),
            skill_step='Step 8.4',
            line=bare.lineno,
        )]
    return [Finding(
        severity='warn',
        code=Code.TOOL_CONTENT_ENVELOPE_UNVERIFIABLE,
        message=(
            f'agent.tool_content_envelope declares kind {kind!r}, but the mock dispatch '
            "wrapper's content rendering could not be traced from the resolved result, so "
            'whether the declared envelope is applied is unknown. Confirm '
            'agent.tool_content_envelope in plurai.config.yaml matches what the wrapper emits '
            f'— routing every mock return through one `{_ENVELOPE_RENDERER}(result)` makes it '
            'checkable. See Step 8.4 closure-tool dispatch wrapper.'
        ),
        skill_step='Step 8.4',
        line=verdicts[0][0].lineno,
    )]


def _is_error_render(node: ast.AST) -> bool:
    """True when ``node`` builds a ``status="error"`` ToolMessage — the wrapper's error branch."""
    return any(
        isinstance(call, ast.Call)
        and any(kw.arg == 'status' and isinstance(kw.value, ast.Constant) and kw.value.value == 'error' for kw in call.keywords)
        for call in ast.walk(node)
    )


def _framework_named(framework: _AgentFramework | None, declared: str | None = None) -> str:
    """The agent's framework as the operator named it: the row, else the declared `agent.framework`."""
    if framework is not None:
        return f'a {framework.label} agent'
    if declared:
        return f'the declared `agent.framework: {declared}`'
    return 'an agent with no recognised framework'


def _declared_row(declared: str | None) -> _AgentFramework | None:
    """The row a declared `agent.framework` names by its `config_ids`, or None."""
    declared_id = framework_id(declared) if declared else ''
    return next((fw for fw in _FRAMEWORKS if declared_id and declared_id in fw.config_ids), None)


def _state_update_findings(
    tree: ast.Module,
    declared: dict[str, frozenset[str]] | str | None,
    framework: _AgentFramework | None = None,
    declared_framework: str | None = None,
) -> list[Finding]:
    """Does the mock dispatch wrapper land each tool's declared ``state_updates:``?

    A real tool's success-path state writes (a ``Command(update={...})``, a run-context attribute)
    gate the agent's later turns; a wrapper that returns the message only drops them and the suite
    scores turns production never reaches. How a wrapper lands them is the framework's
    ``state_write`` rule (:data:`_FRAMEWORKS`); with no rule the landing check says it did not run.
    Framework-neutral either way: a declared key no ``{<tool>: {<key>: ...}}`` table entry names —
    the table ``_state_update`` merges from.
    """
    if not declared:
        return []
    if isinstance(declared, str):
        return [Finding(
            severity='warn',
            code=Code.MOCK_STATE_UPDATES_UNKNOWABLE,
            message=f'The mock state-update check did NOT run: {declared}.',
            skill_step='Step 8.4',
        )]
    wrappers = _mock_wrapper_functions(tree)
    if not wrappers:
        return []
    every_key = frozenset().union(*declared.values())
    findings: list[Finding] = []
    write = framework.state_write if framework is not None else None
    if write is None:
        findings.append(Finding(
            severity='warn',
            code=Code.MOCK_STATE_UPDATES_UNKNOWABLE,
            message=(
                f'The check that the mock dispatch wrapper lands the declared state_updates for {sorted(declared)} did NOT run: '
                f'no state-write rule is written down for {_framework_named(framework, declared_framework)}. Confirm by hand that the wrapper '
                "applies `_state_update(tool_name, result)` where the real tool writes the agent's state."
            ),
            skill_step='Step 8.4',
            line=wrappers[0].lineno,
        ))
    else:
        for line, what in write.wrapper_drops(wrappers, every_key):
            findings.append(Finding(
                severity='fail',
                code=Code.WRAPPER_DROPS_STATE_UPDATES,
                message=(
                    f'plurai.config.yaml declares state_updates for {sorted(declared)}, but the mock '
                    f'dispatch wrapper {what}, so the real tool\'s state writes never land and later turns run on '
                    f'state production has moved past. Land them: {write.land_fix}.'
                ),
                skill_step='Step 8.4',
                line=line,
            ))
    carried: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value in declared and isinstance(value, ast.Dict):
                carried.setdefault(key.value, set()).update(k.value for k in value.keys if isinstance(k, ast.Constant))
    for tool_name, keys in sorted(declared.items()):
        missing = sorted(keys - carried.get(tool_name, set()))
        if missing:
            findings.append(Finding(
                severity='fail',
                code=Code.WRAPPER_DROPS_STATE_UPDATES,
                message=(
                    f'tools.{tool_name}.state_updates declares {missing}, and no '
                    f'`{{{tool_name!r}: {{...}}}}` table entry in the harness names them, so the mock '
                    'never writes them. Copy the declaration into `_MOCK_STATE_UPDATES`.'
                ),
                skill_step='Step 8.4',
                line=wrappers[0].lineno,
            ))
    return findings


class SeedStateParam(enum.Enum):
    """The ``seed_state`` positional contract, written down in exactly one place.

    The child calls ``seed_state(initial_agent_context, initial_world_state,
    resume_seed)`` POSITIONALLY on every scenario (plurai-sdk ``child.py``),
    so a parameter's meaning is fixed by where it sits and not by the name a
    template gives it. The checks below
    name a member rather than writing an integer literal, so no reader has to
    count parameters to see which rule is about which parameter, and no edit
    can quietly point a rule at the neighbouring one.

    Each member's value is the name the scaffold and the references use. That
    name WORDS findings; it is never matched against the template, which may
    spell its parameters however it likes and still be correct — the call is
    positional.

    Membership means "passed unconditionally by the child", which is what
    makes ``len(SeedStateParam)`` the arity every hook must accept. A
    parameter the child could omit would not belong here.
    """

    AGENT_CONTEXT = 'initial_agent_context'
    WORLD_STATE = 'initial_world_state'
    RESUME_SEED = 'resume_seed'

    @property
    def scaffold_name(self) -> str:
        """The name the scaffold gives this parameter. Wording only, never matching."""
        return str(self.value)

    @property
    def index(self) -> int:
        """Argument position, DERIVED from declaration order rather than written out.

        Hand-written indices are the failure this avoids: an integer sitting
        next to each member can disagree with the order the members are
        declared in, and nothing would notice — the check would simply start
        interrogating the wrong parameter and stay green.
        """
        return list(type(self)).index(self)


class ParamUse(enum.Enum):
    """What a ``seed_state`` body does with one of its positional parameters.

    Named states rather than a bool, for two reasons. The two ways of failing
    want different wording — "never mentions it" and "overwrites it on line 12
    before reading it" are different edits — and NOT_DECLARED is a genuinely
    third answer that a bool could only smuggle through as True.
    """

    #: First mention is a read. The only compliant state.
    READ = 'read'
    #: The body never names the parameter at all.
    NEVER_MENTIONED = 'never_mentioned'
    #: The body assigns over the parameter before reading it, discarding what
    #: the child passed (``initial_world_state = {}``).
    REBOUND_BEFORE_READ = 'rebound_before_read'
    #: The signature is too narrow to receive this parameter. Owned by the
    #: arity gate, not by the body-read rules.
    NOT_DECLARED = 'not_declared'


@dataclass(frozen=True)
class SeedStateParamUse:
    """The verdict on one parameter: which one, what happened, and where."""

    param: SeedStateParam
    use: ParamUse
    #: The name the template actually gave the parameter, or None when it does
    #: not declare one. May differ from ``param.scaffold_name``.
    declared_name: str | None
    #: Where a finding about this parameter should point: the offending
    #: mention when there is one — the rebinding line for
    #: REBOUND_BEFORE_READ, which is the line the author has to delete — and
    #: the ``def`` line otherwise. Always resolved here rather than left for
    #: each caller to fall back on, so no call site has to re-derive it.
    line: int

    @property
    def is_violation(self) -> bool:
        """True when the body was handed the parameter and threw the value away.

        NOT_DECLARED is deliberately NOT a violation here: a signature too
        narrow to receive the parameter is already reported as
        ``seed_state_bad_signature``, and a second finding for the same one
        edit sends the author chasing two. Callers that need the distinction
        still have it — it is on ``use``, not folded away into a bool.
        """
        return self.use in (ParamUse.NEVER_MENTIONED, ParamUse.REBOUND_BEFORE_READ)

    def describe(self) -> str:
        """The opening clause of a finding: what the body did with this parameter.

        Total by construction — every ``ParamUse`` has an answer here, so no
        caller can trip an unreachable-branch raise inside a verifier run.
        """
        name = self.declared_name or self.param.scaffold_name
        alias = '' if name == self.param.scaffold_name else f' (spelled {name!r} here)'
        if self.use is ParamUse.REBOUND_BEFORE_READ:
            return (
                f'seed_state declares {self.param.scaffold_name}{alias} but assigns over it '
                f'before any read, discarding the value the child passed'
            )
        if self.use is ParamUse.NEVER_MENTIONED:
            return f'seed_state declares {self.param.scaffold_name}{alias} but never reads it'
        if self.use is ParamUse.READ:
            return f'seed_state reads {self.param.scaffold_name}{alias}'
        return (
            f'seed_state declares no positional parameter at position {self.param.index + 1}, '
            f'so it never receives {self.param.scaffold_name}'
        )


def _positional_param_names(args: ast.arguments) -> list[str]:
    """The declared positional parameter names, in the order the child passes them.

    Position-only parameters then normal ones — the two lists ``ast`` keeps
    separate but the caller supplies as one run. ``*args`` contributes nothing
    on purpose: the contract is NAMED positionals so the body can read them by
    name, and a pure-vararg signature forces ``args[1]`` indexing that no
    static check can follow.

    One helper, so the arity gate and the body-read rules can never disagree
    about what "the third positional parameter" is.
    """
    return [a.arg for a in (*args.posonlyargs, *args.args)]


def _seed_state_accepts_contract_arity(
    seed_fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """True when ``seed_state`` declares a named positional for every contract parameter.

    The child calls ``seed_state(initial_agent_context, initial_world_state,
    resume_seed)`` with three positional arguments UNCONDITIONALLY (plurai-sdk
    ``child.py``) — ``resume_seed`` is ``None`` for a fresh scenario rather than
    being withheld. A narrower signature raises
    TypeError inside the IPC handshake and the customer sees a generic
    ``InitError`` on every scenario instead of a helpful diagnosis. We check
    the AST so this becomes a build-time failure with a pointer to the right
    SKILL.md step.

    The required count is ``len(SeedStateParam)`` rather than a literal 3, so
    the arity gate and the per-parameter rules move together: adding a fourth
    contract parameter cannot leave this gate accepting the old shape.

    Wider is fine — extra parameters are the customer's business. Whether the
    third one carries a DEFAULT is not checked here — the breakage it causes is
    a runtime TypeError in a two-argument call (the skill lane's
    ``verify_harness_opacity`` probe makes one), and this gate is about the
    arity alone. The default itself is plainly readable in the signature, and
    the skill lane's harness-opacity suite reads it. That rule lives in
    references/resumed-seed-state.md.
    """
    return len(_positional_param_names(seed_fn.args)) >= len(SeedStateParam)


class _ParamEvent(NamedTuple):
    """One mention of a parameter name in a body: where it is, and read vs write."""

    lineno: int
    ctx: type[ast.expr_context]


#: Binding forms whose value is evaluated BEFORE the name is bound, and the field
#: holding that value. ``ast`` declares the target first in every one of them, so a
#: walk that follows field order meets the bind before the read that fed it.
_VALUE_EVALUATED_FIRST: dict[type[ast.AST], str] = {
    ast.Assign: 'value',
    ast.AnnAssign: 'value',
    ast.AugAssign: 'value',
    ast.NamedExpr: 'value',
    ast.For: 'iter',
    ast.AsyncFor: 'iter',
}


def _evaluation_order(node: ast.AST) -> list[ast.AST]:
    """``node``'s children as Python EVALUATES them, not as ``ast`` declares them.

    Hoists the value child of a binding form to the front and leaves every other
    node's field order alone, which is already evaluation order for the purpose
    here — what a read-before-write rule needs to get right is the one case where
    a name is both bound and read by the same statement.
    """
    children = list(ast.iter_child_nodes(node))
    field = _VALUE_EVALUATED_FIRST.get(type(node))
    value = getattr(node, field, None) if field is not None else None
    if value is None:
        return children
    return [value, *(child for child in children if child is not value)]


def _collect_param_events(body: list[ast.stmt], target: str) -> list[_ParamEvent]:
    """Every ``Name(id=target)`` mention in ``body``, in EVALUATION order.

    Skips nested function / lambda scopes entirely — both because they often
    shadow the outer parameter (``def helper(initial_world_state): ...``) and
    because the rare legitimate closure-capture pattern is unusual enough that
    flagging it is a reasonable nudge to inline the use. This trade matches the
    only realistic adversarial false-positive without complex scope tracking.

    Evaluation order, not source position, because the caller's whole question
    is whether a read happens before a rebind — and ``resume_seed = resume_seed
    or {}`` puts the rebind to the LEFT of the read it depends on. Ordering those
    two by column calls the most routine defaulting idiom in the language a
    discarded parameter, and fails a correct hook with a message saying the
    opposite of what the line does.
    """
    events: list[_ParamEvent] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        if isinstance(node, ast.Name) and node.id == target and isinstance(node.ctx, (ast.Load, ast.Store)):
            events.append(_ParamEvent(node.lineno, type(node.ctx)))
        for child in _evaluation_order(node):
            visit(child)

    for stmt in body:
        visit(stmt)
    return events


def _seed_state_param_use(
    seed_fn: ast.FunctionDef | ast.AsyncFunctionDef, param: SeedStateParam,
) -> SeedStateParamUse:
    """Classify what ``seed_fn``'s body does with one contract parameter.

    The parameter is picked by POSITION, matching how the child passes it, and
    the name is then read back off the signature — a template that spells the
    third parameter ``seed`` is judged on its ``seed``, which is correct and is
    why findings quote ``declared_name``.

    The rule is first-mention-wins: a body whose first event is a ``Load``
    reads the value it was given; one whose first event is a ``Store``
    (``initial_world_state = {}``) discarded it before looking. Everything
    after the first mention is somebody else's business.

    This is a static heuristic and can be fooled — ``_ = resume_seed`` counts
    as a read, and nested scopes are not descended into (see
    ``_collect_param_events``). Reading a parameter is shallow evidence: it
    proves the body touches the value, not that it handles it correctly.
    Trust-but-verify — pair with the Step 9f smoke.

    Severity is the CALLER's call, not this function's: this reports only
    what the body does with a parameter, and each check decides what that
    costs. Both ``seed_state`` gates that use it happen to spend a ``fail``.
    """
    names = _positional_param_names(seed_fn.args)
    if len(names) <= param.index:
        return SeedStateParamUse(param, ParamUse.NOT_DECLARED, None, seed_fn.lineno)
    declared_name = names[param.index]
    events = _collect_param_events(seed_fn.body, declared_name)
    if not events:
        return SeedStateParamUse(param, ParamUse.NEVER_MENTIONED, declared_name, seed_fn.lineno)
    first = events[0]
    use = ParamUse.READ if first.ctx is ast.Load else ParamUse.REBOUND_BEFORE_READ
    return SeedStateParamUse(param, use, declared_name, first.lineno)


#: The resume seed's optional third key. It carries an operator-pinned node name that turn 1
#: must start the graph at; ``convert-trace --start-node`` is what puts it there. Nothing
#: between the converter and this hook interprets it, so the harness is the only place it can
#: be honoured or refused.
_RESUME_POINT_KEY = 'resume_point'


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Identities of every module / class / function docstring constant in ``tree``."""
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, holders)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _names_resume_point(tree: ast.Module) -> bool:
    """True when the source names ``'resume_point'`` in a CODE position.

    A string constant is the only static trace either honest shape leaves behind: honouring
    the pin reads the key off the seed, refusing it names the key in the raise. Prose is
    excluded on purpose — a comment carries no AST node at all, and a docstring is skipped
    here for the same reason, because writing about the key is what an author does INSTEAD
    of handling it.

    Judged on the merged tree rather than the simulate-only one. A split harness keeps its
    replay in ``_storage.py`` / ``_mocks.py``, and the key is named wherever that helper is.
    The scan is unanchored within that tree: a constant in unrelated code, a parametrize id or
    a ``KeyError('resume_point')`` message all clear it. Only the key's total ABSENCE is
    decidable statically — whether the harness READS it and STARTS there is not — so this
    gate catches the harness that never heard of the pin, and a reviewer still reads the hook.
    """
    docstrings = _docstring_nodes(tree)
    return any(
        isinstance(node, ast.Constant)
        and node.value == _RESUME_POINT_KEY
        and id(node) not in docstrings
        for node in ast.walk(tree)
    )


def _refuses_any_seed(fn: ast.AST, param: str) -> bool:
    """True when the hook raises on every seed it is handed.

    ``if resume_seed: raise RuntimeError(...)`` is the placeholder an un-wired harness starts
    from, and it refuses the transcript and the start node in one statement — a hook that never
    resumes cannot resume at the wrong node. Only a branch taken when the seed IS present counts:
    ``if not resume_seed: raise`` breaks fresh scenarios instead, which is a different defect and
    not this rule's to name.

    The raise has to be an UNGUARDED, REACHED statement of that branch, because only there does
    it run for every seed. A raise reached through anything — a nested ``if`` on one key, a
    ``for`` body, a ``try`` that catches it — refuses SOME seeds while replaying the rest, which
    is precisely the harness this exemption must not cover; a raise sitting after a ``return``
    refuses none at all. The accepted cost is that a hook refusing through a helper
    (``if resume_seed: _refuse(seed)``) is reported: the author names the key in the raise or
    inlines it.
    """

    def raises_before_it_returns(body: list[ast.stmt]) -> bool:
        for stmt in body:
            if isinstance(stmt, ast.Raise):
                return True
            if isinstance(stmt, (ast.Return, ast.Break, ast.Continue)):
                return False
        return False

    def tests_a_present_seed(test: ast.expr) -> bool:
        if isinstance(test, ast.Name):
            return test.id == param
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == param
            and any(isinstance(op, (ast.IsNot, ast.NotEq)) for op in test.ops)
        )

    return any(
        isinstance(node, ast.If)
        and tests_a_present_seed(node.test)
        and raises_before_it_returns(node.body)
        for node in ast.walk(fn)
    )


def _build_state_keys(tree: ast.Module) -> set[str]:
    """Return the dict-literal keys of any ``_build_state`` / ``build_state``
    helper that returns a state dict.

    Recognized shape::

        def _build_state(payload):
            ...
            return {
                "field_a": ...,
                "field_b": ...,
            }

    Used by the drift check below to compare against the agent's declared
    state class. Returns an empty set when no such helper exists or it
    doesn't return a literal dict (then we can't tell — skip the check).
    """
    for fn_name in ('_build_state', 'build_state'):
        fn = _find_function(tree, fn_name)
        if fn is None:
            continue
        # Find the final ``return {…}`` (or the only one).
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Return):
                continue
            if isinstance(sub.value, ast.Dict):
                keys: set[str] = set()
                for k in sub.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                if keys:
                    return keys
    return set()


# State keys a framework injects for the customer, so they never need declaring on
# the state class. Listed here (not inlined) so further framework keys can be added
# in one place.
_FRAMEWORK_INJECTED_STATE_KEYS = frozenset({'messages'})


def _build_state_drift(tree: ast.Module, state_class_dotted: str | None) -> list[Finding]:
    """Cross-check ``_build_state`` dict-literal keys against the real state class.

    Loads the dotted state-class path (typically read from
    ``plurai.config.yaml`` ``agent.state_class``). Diffs:

      - keys in ``_build_state`` but not on the state class → ``warn`` (extras
        typically indicate the LLM speculated about fields the state class
        doesn't expose; harmless where the state class is a lax TypedDict but worth
        flagging — e.g., a completion flag declared by the mock but
        never read by any node)
      - keys on the state class but missing from ``_build_state`` → ``warn``
        (the agent might rely on a field's initial value being None; missing
        it can crash some tools at construction time)
      - state class can't be imported → ``warn`` (``state_class_unimportable``)
        so the customer knows the drift check skipped and WHY

    Skipped silently when:
      - no state_class is configured
      - ``_build_state`` doesn't exist or doesn't return a dict literal
    """
    if state_class_dotted is None:
        return []
    build_keys = _build_state_keys(tree)
    if not build_keys:
        return []

    state_keys, load_error = _load_state_class_field_names(state_class_dotted)
    if state_keys is None:
        # Don't go silent when an explicit state_class was provided but we
        # couldn't load it — emit a warn so the customer can fix the path
        # / install missing deps / whatever.
        return [Finding(
            severity='warn',
            code=Code.STATE_CLASS_UNIMPORTABLE,
            message=(
                f'Could not load state class {state_class_dotted!r}: {load_error}. '
                f'Drift check skipped. Verify agent.state_class in '
                f'plurai.config.yaml points at an importable class with '
                f'``__annotations__`` (TypedDict, dataclass, or pydantic model).'
            ),
            skill_step='Step 8',
        )]

    extra = build_keys - state_keys - _FRAMEWORK_INJECTED_STATE_KEYS
    missing = state_keys - build_keys
    findings: list[Finding] = []
    if extra:
        findings.append(Finding(
            severity='warn',
            code=Code.BUILD_STATE_UNKNOWN_KEYS,
            message=(
                f'_build_state declares keys not in {state_class_dotted}: '
                f'{sorted(extra)!r}. Either the LLM speculated about fields '
                f'that don\'t exist on the state class, or the state class '
                f'is out of date. Verify each extra key has a real consumer.'
            ),
            skill_step='Step 8',
        ))
    if missing:
        findings.append(Finding(
            severity='warn',
            code=Code.BUILD_STATE_MISSING_STATE_FIELDS,
            message=(
                f'_build_state omits state-class fields: {sorted(missing)!r}. '
                f'Some tools may crash at construction if they read these '
                f'before any tool has set them. Initialize each to None / '
                f'sensible default in _build_state.'
            ),
            skill_step='Step 8',
        ))
    return findings


def _load_state_class_field_names(dotted: str) -> tuple[set[str] | None, str | None]:
    """Best-effort load: import ``module.Class`` and return its declared fields.

    Handles ``TypedDict`` (uses ``__annotations__``) and dataclass / pydantic
    models (uses ``__annotations__`` too — both store there).

    Returns ``(field_names, None)`` on success, or ``(None, reason)`` when
    loading fails — the caller surfaces ``reason`` as a warn-severity Finding
    so the user knows the drift check skipped and WHY.
    """
    if '.' not in dotted:
        return None, f'{dotted!r} is not a dotted path (need module.ClassName)'
    module_path, _, cls_name = dotted.rpartition('.')
    try:
        import importlib
        module = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001 — third-party imports may fail many ways
        return None, f'{type(exc).__name__}: {exc}'
    cls = getattr(module, cls_name, None)
    if cls is None:
        return None, f'module {module_path!r} has no attribute {cls_name!r}'
    annotations = getattr(cls, '__annotations__', None)
    if not isinstance(annotations, dict):
        return None, f'{dotted} has no __annotations__ dict (not a TypedDict / dataclass / pydantic model?)'
    return set(annotations.keys()), None


def _isinstance_entities_dict_misuse(tree: ast.Module) -> list[int]:
    """Return line numbers of every ``isinstance(<…entities…>, dict)`` call.

    The world-state proxy is a ``MutableMapping``, not a ``dict`` subclass —
    so the check is always False and any ``return {}`` fallback silently
    misroutes the mock onto an empty world. The right APIs are
    ``world_state.get_entity``, ``world_state.query``, or
    ``plurai.world_state_snapshot``.

    The check matches three shapes:
      1. Direct: ``isinstance(ctx.world_state.entities, dict)``
      2. Tuple: ``isinstance(x.entities, (dict, Mapping))``
      3. Aliased: ``entities = ...entities; isinstance(entities, dict)``
         OR ``entities = getattr(state, "entities", ...);
              isinstance(entities, dict)``

    Aliasing tracking is function-scoped — each ``FunctionDef`` /
    ``AsyncFunctionDef`` starts a fresh alias table. Module-level aliasing
    isn't covered (rare in mock-handler code).
    """
    hits: list[int] = []
    # Module-scope aliases (rare but cheap to include for completeness).
    module_aliases = _collect_entities_aliases(tree.body)
    hits.extend(_find_isinstance_dict_hits(tree, aliases=module_aliases))
    # Per-function aliases. We re-walk each function's body to find the
    # isinstance calls within that function's scope, using the function-local
    # alias table built from its own assignments.
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local_aliases = module_aliases | _collect_entities_aliases(fn.body)
        for stmt in fn.body:
            hits.extend(_find_isinstance_dict_hits(stmt, aliases=local_aliases))
    # The same isinstance call may surface from both passes (module-walk and
    # function-walk) when aliasing is purely module-scoped. Dedupe by line.
    return sorted(set(hits))


def _collect_entities_aliases(body: list[ast.stmt]) -> set[str]:
    """Return names assigned from ``<expr>.entities`` or ``getattr(_, 'entities', _)``.

    Module-level only (no nested function walk). Targets:
      - ``entities = ctx.world_state.entities``
      - ``entities = getattr(state, "entities", None)``
      - tuple/multi-target assigns are ignored (rare here)
    """
    aliases: set[str] = set()
    for node in body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        # Form 1: foo = <expr>.entities
        if isinstance(value, ast.Attribute) and value.attr == 'entities':
            aliases.add(target.id)
            continue
        # Form 2: foo = getattr(<expr>, "entities", <default>)
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == 'getattr'
            and len(value.args) >= 2
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == 'entities'
        ):
            aliases.add(target.id)
    return aliases


def _find_isinstance_dict_hits(node: ast.AST, *, aliases: set[str]) -> list[int]:
    """Return line numbers of ``isinstance(target, dict|(dict,...))`` calls
    in ``node``, where ``target`` is either ``<expr>.entities`` or a name in
    ``aliases``.
    """
    hits: list[int] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if not (isinstance(sub.func, ast.Name) and sub.func.id == 'isinstance'):
            continue
        if len(sub.args) != 2:
            continue
        first, second = sub.args
        # First arg: literal ``<…>.entities`` OR a name that was aliased to one.
        if isinstance(first, ast.Attribute) and first.attr == 'entities':
            pass  # match
        elif isinstance(first, ast.Name) and first.id in aliases:
            pass  # match
        else:
            continue
        # Second arg: literal ``dict`` OR a tuple containing it.
        if isinstance(second, ast.Name) and second.id == 'dict':
            hits.append(sub.lineno)
            continue
        if isinstance(second, ast.Tuple) and any(
            isinstance(elt, ast.Name) and elt.id == 'dict' for elt in second.elts
        ):
            hits.append(sub.lineno)
    return hits


def _loads_dotenv(tree: ast.Module) -> bool:
    """True when simulate.py seeds ``os.environ`` from a .env at import time.

    Recognizes the python-dotenv forms — a ``load_dotenv`` / ``dotenv_values`` call, or
    any dotenv import. Deliberately shallow: the point is "did the author think about
    it at all", not to prove correctness. A hand-rolled reader (an ``os.environ``
    loop over a parsed file) is NOT detected and draws a false warn — accepted, since
    the warn is advisory and the template ships the python-dotenv form.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
            if name in {'load_dotenv', 'dotenv_values'}:
                return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, 'module', '') or ''
            if 'dotenv' in mod or any('dotenv' in (a.name or '') for a in node.names):
                return True
    return False


#: Handlers broad enough to catch a missing python-dotenv. A narrower unrelated one
#: (``except ValueError``) is not a swallow.
_IMPORT_ERROR_HANDLERS = {'ImportError', 'ModuleNotFoundError', 'Exception', 'BaseException'}


def _is_dotenv_import(node: ast.AST) -> bool:
    """True for a dotenv ``import`` / ``from`` statement in any form."""
    if not isinstance(node, (ast.Import, ast.ImportFrom)):
        return False
    mod = getattr(node, 'module', '') or ''
    return 'dotenv' in mod or any('dotenv' in (a.name or '') for a in node.names)


def _dotenv_import_swallowed(tree: ast.Module) -> bool:
    """True when the ONLY dotenv usage sits in a try whose ImportError handler is a bare pass.

    The static-presence check above is satisfied by exactly this shape, yet at runtime it does
    NOTHING when python-dotenv is missing — the silent model-fallback failure the check exists
    to catch, wearing a compliant-looking coat. A handler that raises, prints, warns, or logs
    is fine (the degradation is loud); only the say-nothing `pass`/`...` form is flagged.
    """

    def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
        if isinstance(handler.type, ast.Tuple):
            names = {e.id for e in handler.type.elts if isinstance(e, ast.Name)}
        elif isinstance(handler.type, ast.Name):
            names = {handler.type.id}
        else:
            names = set()
        if handler.type is not None and not (_IMPORT_ERROR_HANDLERS & names):
            return False
        # `pass` or a bare constant (`...` / a docstring) — the say-nothing forms.
        return all(isinstance(b, ast.Pass) or (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)) for b in handler.body)

    def _try_swallows_dotenv(node: ast.Try) -> bool:
        if not any(_is_dotenv_import(sub) for stmt in node.body for sub in ast.walk(stmt)):
            return False
        return any(_handler_is_silent(h) for h in node.handlers)

    if not any(_try_swallows_dotenv(node) for node in ast.walk(tree) if isinstance(node, ast.Try)):
        return False
    # An unconditional dotenv usage OUTSIDE any try still counts as real coverage.
    inside_try = {id(sub) for node in ast.walk(tree) if isinstance(node, ast.Try) for stmt in node.body for sub in ast.walk(stmt)}
    return not any(_is_dotenv_import(node) for node in ast.walk(tree) if id(node) not in inside_try)


#: The hook a world the mock does NOT own requires, by NAME. The child resolves it with
#: ``getattr`` on the imported module and calls it as ``reset()`` — before every scenario, after
#: the mock session is opened, before ``seed_state`` puts that scenario's preconditions in.
_RESET_HOOK = 'reset'

#: The hook that makes a world OBSERVABLE, by NAME — the other half of what a non-mock owner
#: implies. ``reset`` CLEARS the store between scenarios; this one READS it at the end of one, and the
#: child returns what it hands back verbatim as that scenario's ``final_world_state.state``. It is
#: the ONLY way a store the mock does not own becomes readable at all: without it the only thing
#: the run can snapshot is the mock's session, which the declaration says is not the world being
#: scored. ``world_snapshot``, not ``snapshot`` — the child ``getattr``\ s the name off the
#: imported module, so a name generic enough to collide with a variable would be called as a hook.
#:
#: Both names are the SDK's, and both are pinned to it by
#: ``tests/test_world_owner_reset_gate.py::test_the_hook_names_are_the_SDKs`` — the same lane that
#: byte-compares :func:`_module_scope_bindings`, for the same reason: a name this file spells
#: differently sends an author to export something nothing calls.
_WORLD_SNAPSHOT_HOOK = 'world_snapshot'

#: The two ``world_state.owner`` values that require :data:`_RESET_HOOK` — and, to be SCORED on
#: the world they own at all, :data:`_WORLD_SNAPSHOT_HOOK`. They differ in WHERE the store is — the
#: agent's own process, or a real system behind it — not in what must be done about it, so one
#: requirement covers both. The third legal value, ``mock``, requires NOTHING: the backend
#: opens a session per scenario and seeds it. So does an ABSENT declaration, which means ``mock``
#: — that is every bench written before the key existed, and this check must stay silent on them.
#:
#: The legal SET and an unknown value are ``verify_config.py``'s (`world_owner_value`, which fails
#: on a typo rather than reading it as ``mock``). This file acts only on the two words it knows,
#: so one typo earns one finding with one remedy, not two.
#:
#: ONE TUPLE FOR BOTH HOOKS, not two lists that could disagree: the population is the same
#: population — every world the mock does not own — and the two hooks are two things owed about
#: it, not two different sets of benches.
_WORLD_OWNERS_NEEDING_RESET = ('agent', 'external')


#: The user's explicit opt-in to live writes on a world nothing resets per conversation (`world_state.<key>: true`),
#: the runner's `orchestrator.WORLD_SHARED_OK_KEY`. With it the run proceeds and carries the `shared_world` risk.
_SHARED_WORLD_OK_KEY = 'shared_world_ok'


def _shared_world_risk(live_writers: tuple[str, ...]) -> str:
    """What an opted-in shared world costs, said the way the release verdict says it."""
    return (f'Kept live by `world_state.{_SHARED_WORLD_OK_KEY}: true`, so the run proceeds with the named risk `shared_world` '
            f'("Conversations share one world: {len(live_writers)} live writing tools with no reset between conversations; '
            f'verdicts may depend on run order."): the suite caps at qualified, and stability retries and trial agreement are '
            f'not comparable. ')


def _mock_writers_first(live_writers: tuple[str, ...]) -> str:
    """The first remedy whenever live writes meet a world nothing resets: mock the systems those tools write."""
    if not live_writers:
        return ''
    return (f'First choice: mock the writing systems ({", ".join(live_writers)}: `mode: mock`, `status: mocked` on the '
            f'map, per system, so no live read of the same store sits beside a mocked write). ')


def _vendored_unreadable_key() -> str:
    """The reserved in-entity key a failed read is listed under, READ from the backend's vendored
    ``world_snapshot_keys`` block. Imported defensively: this script runs standalone from a SKILL
    snippet, so a copy outside the plugin keeps the check on the literal it has always had."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
        from plurai_contracts import contract  # noqa: PLC0415 - repo script, optional at runtime
    except ImportError:
        return '_unreadable'
    served = contract('world_snapshot_keys', default={'unreadable': '_unreadable'})
    return str(served.get('unreadable') or '_unreadable') if isinstance(served, dict) else '_unreadable'


#: The ONE key under which a caught exception's text is a typed fact rather than content.
_UNREADABLE_KEY = _vendored_unreadable_key()


def _exc_str(node: ast.AST | None, exc: str) -> bool:
    """A string built FROM the caught exception ``exc``: an f-string, ``%`` / ``+`` / ``.format``, or
    ``str()`` / ``repr()`` whose operands name it — sliced or put through a method (``.strip()``) still.
    An ``IfExp`` counts per branch, never by its test —
    ``'missing' if e.errno == 2 else 'unreadable'`` is a status, not the exception's text."""
    if node is None:
        return False
    if isinstance(node, ast.IfExp):
        return _exc_str(node.body, exc) or _exc_str(node.orelse, exc)
    if isinstance(node, ast.Subscript):
        return _exc_str(node.value, exc)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _exc_str(node.func.value, exc):
        return True
    built = (
        isinstance(node, ast.JoinedStr)
        or (isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)))
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('str', 'repr'))
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'format')
    )
    return built and any(isinstance(n, ast.Name) and n.id == exc for n in ast.walk(node))


#: The nodes that bound where a slot alias holds: a name bound to the slot in one loop says nothing about the
#: same name in a sibling loop.
_ALIAS_SCOPES = (ast.For, ast.AsyncFor, ast.While, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _SlotResolver:
    """Where the reserved ``_unreadable`` slot is, in one function, whatever form writes it.

    The slot is named by its key — a string literal or a module-level constant holding it — as a
    subscript, a ``setdefault`` call, a dict-literal key or an ``update``/``dict`` keyword. A NAME is an
    alias of the slot when it is bound to one (``n = <slot>``, ``(n := <slot>)``) or one is bound to it
    (``<slot> = n``, ``{KEY: n}``, ``.update(KEY=n)``), and the alias holds only within the loop or
    function that binds it, and the ones nested in or around it.
    """

    def __init__(self, fn: ast.AST, key_names: set[str]) -> None:
        self.key_names = key_names
        self.parents = {child: parent for parent in ast.walk(fn) for child in ast.iter_child_nodes(parent)}
        self.aliases: list[tuple[str, ast.AST | None]] = []
        for node in ast.walk(fn):
            for name in self._bound_names(node):
                self.aliases.append((name, self._scope(node)))

    def _is_key(self, node: ast.AST | None) -> bool:
        return (isinstance(node, ast.Constant) and node.value == _UNREADABLE_KEY) or (isinstance(node, ast.Name) and node.id in self.key_names)

    def names_the_slot(self, node: ast.AST | None) -> bool:
        """``node`` IS the slot: ``x[KEY]`` or ``x.setdefault(KEY, …)``."""
        if isinstance(node, ast.Subscript):
            return self._is_key(node.slice)
        return (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'setdefault'
            and bool(node.args) and self._is_key(node.args[0])
        )

    def _bound_names(self, node: ast.AST) -> list[str]:
        out: list[str] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and self.names_the_slot(node.value):
                    out.append(target.id)
                elif self.names_the_slot(target) and isinstance(node.value, ast.Name):
                    out.append(node.value.id)
        elif isinstance(node, ast.NamedExpr) and self.names_the_slot(node.value):
            out.append(node.target.id)
        elif isinstance(node, ast.Dict):
            out += [v.id for k, v in zip(node.keys, node.values) if self._is_key(k) and isinstance(v, ast.Name)]
        elif isinstance(node, ast.Call):
            out += [kw.value.id for kw in node.keywords if kw.arg == _UNREADABLE_KEY and isinstance(kw.value, ast.Name)]
        return out

    def _scope(self, node: ast.AST) -> ast.AST | None:
        up = self.parents.get(node)
        while up is not None and not isinstance(up, _ALIAS_SCOPES):
            up = self.parents.get(up)
        return up

    def _ancestors(self, node: ast.AST | None) -> list[ast.AST | None]:
        chain = [node]
        while node is not None:
            node = self.parents.get(node)
            chain.append(node)
        return chain

    def _alias_here(self, name: ast.Name) -> bool:
        where = self._scope(name)
        return any(
            alias == name.id and (scope in self._ancestors(where) or where in self._ancestors(scope)) for alias, scope in self.aliases
        )

    def within(self, node: ast.AST | None) -> bool:
        """A write target (or ``.update`` receiver) whose container chain passes through the slot."""
        while node is not None:
            if self.names_the_slot(node) or (isinstance(node, ast.Name) and self._alias_here(node)):
                return True
            if isinstance(node, ast.NamedExpr):
                node = node.value
            elif isinstance(node, (ast.Subscript, ast.Attribute)):
                node = node.value
            else:
                node = node.func if isinstance(node, ast.Call) else None
        return False


def _slot_key_names(tree: ast.Module) -> set[str]:
    """Module-level names bound to the reserved key's string, so ``rec[UNREADABLE]`` resolves to the slot."""
    return {
        t.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant) and node.value.value == _UNREADABLE_KEY
        for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(t, ast.Name)
    }


#: Calls whose arguments are a DIAGNOSTIC channel, never world content: ``print`` and the logging /
#: ``warnings`` method names. ``sys.stderr.write`` / ``sys.stdout.write`` count as ``print``.
_DIAGNOSTIC_FUNCS = frozenset({'print'})
_DIAGNOSTIC_METHODS = frozenset({'debug', 'info', 'warning', 'warn', 'error', 'exception', 'critical', 'log'})


def _is_diagnostic_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _DIAGNOSTIC_FUNCS
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _DIAGNOSTIC_METHODS:
        return True
    stream = func.value
    return (
        func.attr == 'write' and isinstance(stream, ast.Attribute) and stream.attr in ('stderr', 'stdout')
        and isinstance(stream.value, ast.Name) and stream.value.id == 'sys'
    )


class _ExcTextSinks:
    """Where a caught exception's TEXT goes inside one function: an allow-list of sinks, deny by default.

    Harness exception text is never world content. It may go to exactly three places — an argument of a
    diagnostic call (:func:`_is_diagnostic_call`), a ``raise``, or a value under the typed ``_unreadable``
    slot (:class:`_SlotResolver`). Every other destination is the defect, whatever its shape: a return, a
    subscript or attribute write, a dict literal under any other key, an ``append``, a call argument.

    Two places are not destinations at all: a comparison or a branch test turns the text into a bool, and a
    returned tuple/list hands it to a caller that may type it under the slot — that last only when the
    harness names the slot somewhere (otherwise no caller can). A name bound to the text is followed to
    every place the function reads it.
    """

    def __init__(self, fn: ast.AST, slot: _SlotResolver, names_slot: bool) -> None:
        self.fn = fn
        self.slot = slot
        self.names_slot = names_slot
        self.parents = slot.parents

    def leaks(self, node: ast.AST, followed: set[str]) -> bool:
        child, up = node, self.parents.get(node)
        while up is not None:
            if isinstance(up, ast.Raise):
                return False
            if isinstance(up, ast.Call) and child is not up.func and (
                _is_diagnostic_call(up) or (isinstance(up.func, ast.Attribute) and self.slot.within(up.func.value))
            ):
                return False
            if isinstance(up, ast.keyword) and up.arg == _UNREADABLE_KEY:
                return False
            if isinstance(up, ast.Dict) and any(self.slot._is_key(k) and v is child for k, v in zip(up.keys, up.values)):
                return False
            if isinstance(up, ast.Compare) or (isinstance(up, (ast.If, ast.While, ast.IfExp, ast.Assert)) and child is up.test):
                return False
            if isinstance(up, ast.NamedExpr) and child is up.value and self._name_leaks(up.target.id, followed):
                return True
            if isinstance(up, ast.Return):
                return not (self.names_slot and isinstance(child, (ast.Tuple, ast.List)))
            if isinstance(up, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if child is not up.value:
                    return True
                targets = up.targets if isinstance(up, ast.Assign) else [up.target]
                return any(self._target_leaks(t, followed) for t in targets)
            if isinstance(up, ast.stmt):
                return True
            child, up = up, self.parents.get(up)
        return True

    def _target_leaks(self, target: ast.AST, followed: set[str]) -> bool:
        if isinstance(target, ast.Name):
            return self._name_leaks(target.id, followed)
        if isinstance(target, (ast.Subscript, ast.Attribute)):
            return not self.slot.within(target)
        return True

    def _name_leaks(self, name: str, followed: set[str]) -> bool:
        if name in followed:
            return False
        followed.add(name)
        return any(
            self.leaks(use, followed)
            for use in ast.walk(self.fn)
            if isinstance(use, ast.Name) and use.id == name and isinstance(use.ctx, ast.Load)
        )


def _names_the_slot(tree: ast.Module) -> bool:
    """The harness names the reserved slot ANYWHERE — a literal, a module constant, or a keyword. When it
    does not, no failed read can ever be typed, so an exception string handed out is content by construction."""
    return bool(_slot_key_names(tree)) or any(
        (isinstance(n, ast.Constant) and n.value == _UNREADABLE_KEY) or (isinstance(n, ast.keyword) and n.arg == _UNREADABLE_KEY)
        for n in ast.walk(tree)
    )


def _snapshot_read_errors_as_content(tree: ast.Module, simulate_only_tree: ast.Module) -> list[int]:
    """Lines where code reachable from the world-snapshot hook sends a caught exception's TEXT anywhere but
    a diagnostic call, a ``raise`` or the typed ``_unreadable`` slot (:class:`_ExcTextSinks`).

    Any other destination lands in the snapshot as a value — a produced file's text preview, an ``error``
    field on the entity — and the judges read it as content. A failed read is a typed absence instead
    (``references/seed-state-contract.md``). The hook is resolved as the child resolves it (a ``def`` or a
    module-scope alias); calls are followed through local functions and methods, never through a name an
    import binds (:func:`_snapshot_reachable_functions`).
    """
    lines: set[int] = set()
    key_names = _slot_key_names(tree)
    names_slot = _names_the_slot(tree)
    for fn in _snapshot_reachable_functions(tree, simulate_only_tree):
        sinks = _ExcTextSinks(fn, _SlotResolver(fn, key_names), names_slot)
        for node in ast.walk(fn):
            if isinstance(node, ast.ExceptHandler) and node.name:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.JoinedStr, ast.BinOp, ast.Call)) and _exc_str(sub, node.name) and sinks.leaks(sub, set()):
                        lines.add(sub.lineno)
    return sorted(lines)


def _snapshot_reachable_functions(tree: ast.Module, simulate_only_tree: ast.Module) -> list[ast.AST]:
    """The world-snapshot hook and every local function or method it reaches, hook first.

    The hook is resolved as the child resolves it: a ``def`` in simulate.py or a module-scope alias of a
    local function. Calls are followed through local functions and methods (split helpers included, since
    ``tree`` is the merged source), never through a name an import binds. Empty when no hook is bound.
    """
    local_fns = _local_functions(tree)
    imported = {
        (alias.asname or alias.name).split('.')[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    hook: ast.AST | None = _find_function(simulate_only_tree, _WORLD_SNAPSHOT_HOOK)
    for node in simulate_only_tree.body:
        if hook is None and isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and any(
            isinstance(t, ast.Name) and t.id == _WORLD_SNAPSHOT_HOOK for t in node.targets
        ):
            hook = local_fns.get(node.value.id)
    reached: list[ast.AST] = []
    seen: set[int] = set()
    stack = [hook] if hook is not None else []
    while stack:
        fn = stack.pop()
        if id(fn) in seen:
            continue
        seen.add(id(fn))
        reached.append(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in local_fns and node.id not in imported:
                stack.append(local_fns[node.id])
            elif (
                isinstance(node, ast.Attribute) and node.attr in local_fns
                and isinstance(node.value, ast.Name) and node.value.id not in imported
            ):
                stack.append(local_fns[node.attr])
    return reached


#: The document readers ``plurai.file_row`` runs, by the top-level module a harness imports, each with the
#: suffix it reads (``'*'``: any suffix, file_row's fallback). DERIVED FROM THE SDK, not chosen here:
#: ``plurai/file_rows.py`` imports markitdown, openpyxl and pptx, and the SDK installs them as
#: ``markitdown[docx,pptx,xlsx,pdf]``, whose extras add mammoth (.docx) and pdfminer / pdfplumber (.pdf).
#: Those extras also pull in pandas and lxml, which are general-purpose and not listed. Pinned to the SDK
#: by ``tests/test_snapshot_hand_rolled_file_reader.py``.
_FILE_ROW_READERS: dict[str, str] = {
    'markitdown': '*',
    'openpyxl': '.xlsx',
    'pptx': '.pptx',
    'mammoth': '.docx',
    'pdfminer': '.pdf',
    'pdfplumber': '.pdf',
}

#: Readers of a format ``plurai.file_row`` has its own reader for, which it does not run itself. A harness
#: importing one is writing the reader file_row already is. Each suffix is pinned to file_row's reader table.
_SAME_FORMAT_READERS: dict[str, str] = {
    'docx': '.docx',  # python-docx
    'pypdf': '.pdf',
    'PyPDF2': '.pdf',
    'fitz': '.pdf',  # PyMuPDF
}

_DOCUMENT_READER_MODULES = frozenset(_FILE_ROW_READERS) | frozenset(_SAME_FORMAT_READERS)


def _hand_rolled_file_reader(tree: ast.Module, simulate_only_tree: ast.Module) -> tuple[str, int] | None:
    """``(module, line)`` of the first document reader that code reachable from the world-snapshot hook uses
    while ``plurai.file_row`` is reached nowhere from it; ``None`` otherwise.

    A reader is USED when a reachable function imports it (``from markitdown import MarkItDown`` in its
    body) or reads a name a module-scope import of it binds (``openpyxl.load_workbook``). ``file_row`` is
    reached when a reachable function reads ``<plurai>.file_row`` off a name bound to the ``plurai`` package,
    or a name ``from plurai[...] import file_row`` binds. A local function named ``file_row`` is not it.
    """
    reached = _snapshot_reachable_functions(tree, simulate_only_tree)
    if not reached:
        return None
    reader_names: dict[str, str] = {}
    plurai_names: set[str] = set()
    file_row_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root in _DOCUMENT_READER_MODULES:
                    reader_names[alias.asname or root] = root
                if alias.name == 'plurai':
                    plurai_names.add(alias.asname or 'plurai')
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            root = node.module.split('.')[0]
            for alias in node.names:
                if root in _DOCUMENT_READER_MODULES:
                    reader_names[alias.asname or alias.name] = root
                if root == 'plurai' and alias.name == 'file_row':
                    file_row_names.add(alias.asname or alias.name)
    first: tuple[str, int] | None = None
    for fn in reached:
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == 'file_row' and isinstance(node.value, ast.Name) and node.value.id in plurai_names:
                return None
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in file_row_names:
                return None
            hit: str | None = None
            if isinstance(node, ast.Import):
                hit = next((a.name.split('.')[0] for a in node.names if a.name.split('.')[0] in _DOCUMENT_READER_MODULES), None)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level and node.module.split('.')[0] in _DOCUMENT_READER_MODULES:
                hit = node.module.split('.')[0]
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in reader_names:
                hit = reader_names[node.id]
            if hit is not None and (first is None or node.lineno < first[1]):
                first = (hit, node.lineno)
    return first


@dataclass(frozen=True)
class _WorldOwnerConflict:
    """Two rungs of the workspace declare DIFFERENT world owners — a state, not a value.

    Typed rather than collapsed to ``None`` for the reason :func:`_declared_record_fields` names
    about its own negative states: a state that renders as "nothing declared" certifies a bench
    nobody checked. The runner does not pick a winner either — it collects every rung and raises
    ``WorkspaceConfigError`` (exit 2, before a thread is opened), because the declaration is
    env-scoped and a discovery ORDER does not get to settle which answer the env meant.

    ``declarations`` is ``(owner, config path)`` per rung, in the order they were read, so the
    finding can name WHICH file said WHAT. An author's remedy is an edit to one of two files.
    """

    declarations: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _WorldOwnerUnreadable:
    """The question was not ASKED — no interpreter could read the declaration. A state, not None.

    ``None`` from :func:`_declared_world_owner` means "every rung was read and none declares an
    owner", which is a claim about the bench and keeps the `world_owner_unreset` gate silent by
    DESIGN. An absent pyyaml or a config that does not parse is not that claim, and collapsing it
    into ``None`` flipped the gate's rc from 1 to 0 on the same workspace with the same defect —
    the exact thing :func:`_declared_record_fields`' docstring forbids ("a state that renders as
    a green tick certifies a bench nobody checked"). Surfaced as `world_owner_unknowable`, a
    warn, exactly like `storage_fidelity_unknowable`: an unanswerable question is not a defect in
    the harness, and it must not read as a clean pass either.
    """

    reason: str


def verify(
    source: str,
    state_class_dotted: str | None = None,
    *,
    simulate_only_source: str | None = None,
    tool_content_envelope: str | None = None,
    declared_record_fields: tuple[frozenset[str], ...] | str | None = None,
    world_state_owner: str | _WorldOwnerConflict | _WorldOwnerUnreadable | None = None,
    live_writers: tuple[str, ...] = (),
    shared_world_ok: bool = False,
    mock_state_updates: dict[str, frozenset[str]] | str | None = None,
    declared_framework: str | None = None,
    declared_native_tools: tuple[str, ...] = (),
    resume_declaration: dict | str | None = None,
    declared_mocks: frozenset[str] = frozenset(),
    world_access_exempt: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Run all contract checks against ``source``.

    Args:
      source: contents of ``simulate.py`` (and adjacent _storage.py /
        _mocks.py concatenated, in the split-layout case). All AST checks
        operate against this.
      state_class_dotted: optional ``module.ClassName`` for the agent's state
        class — typically read from ``plurai.config.yaml`` ``agent.state_class``.
        When provided, enables the ``_build_state_drift`` cross-check (extras /
        missings between ``_build_state`` and the real state class fields).
        When ``None``, the drift check is skipped silently.
      simulate_only_source: optional simulate.py-ONLY source (no helper
        concat). When given, ``invoke_agent`` / ``seed_state`` definition
        checks run against THIS tree instead of the merged ``source`` —
        ensuring the orchestrator-facing API lives in simulate.py itself,
        not accidentally in a helper. Other checks still use ``source``
        so factory monkey-patches in ``_storage.py`` are visible.
      tool_content_envelope: optional ``agent.tool_content_envelope.kind`` from
        ``plurai.config.yaml``. When it declares a convention of the agent's own
        (``json_field`` / ``text``), the wrapper must render through it rather
        than hard-coding a JSON dump. ``None`` (and ``json``) skips the check —
        this file cannot read the config, and a check that guessed would fire on
        every agent that has no convention at all.
      world_state_owner: optional ``world_state.owner`` from ``plurai.config.yaml``
        — WHO owns the state this suite scores. ``main()`` reads it off the config
        beside simulate.py (:func:`_declared_world_owner`). ``'agent'`` / ``'external'``
        enable the ``reset`` hook check below; ``None`` (an undeclared bench),
        ``'mock'``, and any value the runner cannot read all skip it. A
        :class:`_WorldOwnerConflict` — two rungs declaring different owners — is
        refused outright instead, because which hook is owed is undecidable until
        the two files agree.
      live_writers: the ``mode: real`` tools that write the customer's live system
        (:func:`_declared_live_writers`). With no ``reset`` bound they enable
        ``world_live_writer_unreset``, whatever ``world_state_owner`` says.
      shared_world_ok: ``world_state.shared_world_ok`` (:func:`_declared_shared_world_ok`), the user's
        explicit opt-in to a shared world. With it ``world_owner_unreset`` warns instead of failing and
        both world findings name the ``shared_world`` risk the run carries.
      mock_state_updates: optional ``{tool: keys}`` from each ``mode: mock`` tool's
        ``state_updates:`` in ``plurai.config.yaml`` (:func:`_declared_mock_state_updates`),
        ``omit:`` entries excluded; a string says why the config could not be read.
      declared_framework: optional ``agent.framework`` from ``plurai.config.yaml``
        (:func:`_declared_framework`). It identifies a framework row the AST cannot see
        because the harness imports the customer's agent package, not the framework.
      declared_native_tools: ``agent.native_tools`` from ``plurai.config.yaml``
        (:func:`_declared_native_tools`): tools the agent runs where no plurai lane sees them,
        so the harness owes the trail ``plurai.confine`` records.
      resume_declaration: ``harness.resume`` from ``bench_manifest.json`` (:func:`_declared_resume`).
        Decides the severity of a hook that refuses every seed: ``None`` (no manifest, or one that
        predates the field) warns; a declaration other than a typed ``unsupported`` fails.
      declared_mocks: the ``mode: mock`` tool names of ``plurai.config.yaml`` (:func:`_declared_mocks`). A patched
        storage class that reaches one of them (or a ``plurai.mock_wrap`` callable) delegates to the mock world.
      world_access_exempt: the accessor leaves of every declared ``read_through.via`` / ``guards[].via``
        (:func:`_declared_world_overlays`): the declared overlay, which ``harness_store_serves_world`` never flags.

    Returns the list of findings. Caller decides exit code based on whether
    any have ``severity='fail'``.
    """
    findings: list[Finding] = []

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Finding(
            severity='fail',
            code=Code.SYNTAX_ERROR,
            message=f'simulate.py does not parse: {exc.msg}',
            skill_step='Step 8',
            line=exc.lineno,
        )]

    # invoke_agent / seed_state must live at simulate.py's module scope
    # (the orchestrator imports simulate.py and looks them up by name).
    # When the customer split simulate.py (Step 8.4), the merged source
    # might find these names in a helper file — which would falsely pass
    # while runtime fails with AttributeError. Search the simulate-only
    # tree when provided; fall back to the merged tree for monolithic
    # layouts.
    simulate_only_tree: ast.Module = tree
    if simulate_only_source is not None and simulate_only_source != source:
        try:
            simulate_only_tree = ast.parse(simulate_only_source)
        except SyntaxError:
            # If simulate.py itself doesn't parse, the merged-source parse
            # above would have already failed too — we only reach here when
            # the merged source parses (helpers concat OK) but simulate.py
            # alone doesn't, which is implausible. Fall back to merged tree.
            simulate_only_tree = tree

    invoke = _find_function(simulate_only_tree, 'invoke_agent')
    invoke_pipeline = _find_function(simulate_only_tree, 'invoke_pipeline')
    seed = _find_function(simulate_only_tree, 'seed_state')
    factories = _factory_assignments(tree)
    has_storage_factory = bool(factories)
    # WHAT simulate.py ITSELF binds at module scope, read ONCE — the question both world-owner
    # hook gates ask, answered off one walk the way this file's own `_module_scope_bindings`
    # docstring asks for. Two calls were two answers to one question waiting to disagree.
    simulate_only_bindings = _module_scope_bindings(simulate_only_tree)

    # 1. AT LEAST ONE of invoke_agent / invoke_pipeline must be defined.
    #
    # ``invoke_agent`` is the dialog hook (conversational, multi-turn,
    # user-simulator + tool dispatch). ``invoke_pipeline`` is the
    # one-shot hook (single LLM call, structured input → structured
    # output, no dialog loop). A component bench picks ONE based on
    # what's being tested — full agent vs sub-component. Each bench
    # lives in its own ``plurai_sim_<component>/`` directory with its
    # own simulate.py exporting the relevant hook.
    #
    # The SDK's child-side ``_ChildSession.__init__`` enforces the same
    # contract: "at least one of invoke_agent / invoke_pipeline must
    # exist". This static-check mirrors it so a customer authoring a
    # pipeline-only simulate.py (a generator or a classifier) doesn't
    # get a false ``missing_invoke_agent`` here.
    #
    # BY BINDING, like the world-owner hooks below: the child getattrs the hook off the imported
    # module, so `invoke_agent = _run` or `from ._entry import invoke_agent` exports it as well as
    # a `def` does.
    if not {'invoke_agent', 'invoke_pipeline'} & simulate_only_bindings:
        findings.append(Finding(
            severity='fail',
            code=Code.MISSING_INVOKE_HOOK,
            message=(
                'No invoke_agent(...) OR invoke_pipeline(...) defined at '
                'module scope. Define invoke_agent for dialog-mode '
                'simulations (multi-turn conversational agents), or '
                'invoke_pipeline for one-shot pipelines (single-call '
                'components with structured input → structured output).'
            ),
            skill_step='Step 8',
        ))

    # 1b. A world the mock does NOT own must be CLEARED by the harness, because nothing else
    # can reach it.
    #
    # Exactly two things are reset per scenario: the mock session the backend opens (keyed on a
    # session id, re-seeded per scenario) and the child process the runner spawns (so a
    # module-level store dies with the scenario). A world kept anywhere else — a file, the
    # agent's checkpointer database, a real CRM, a server-side conversation store — is carried
    # forward: scenario 2 opens on scenario 1's writes, scenario 3 on both, and each verdict is
    # partly about the order the scenarios happened to run in. Nothing in the output says so.
    # The run completes, the judges score, and the scores are reproducible only for the run
    # order that produced them. That is why this is a FAIL on a green-looking bench.
    #
    # Keyed on the DECLARATION, never on a name in the harness: `world_state.owner` is the only
    # thing that knows where the store is, and a harness that keeps its world in a file is not
    # distinguishable from one that does not by reading its imports.
    #
    # BY NAME, with the runner's own resolution: `_module_scope_bindings`, not `_find_function`,
    # because the child getattrs the hook off the imported module and `reset = _clear_all` binds
    # it just as well as a `def` does.
    #
    # The same gap HOLDS the run at RUN START (`orchestrator.unreset_external_world_finding`, the
    # holding finding `world.unreset_external`: exit 2 before a thread is opened until it is
    # overruled). This one is the $0 preflight for it: same two conjuncts, at authoring time,
    # before a scenario set has been paid for.
    #
    # DECLARE AND DISPATCH, in the order the runner meets them: a workspace whose two rungs
    # disagree has no owner yet, so demanding one of the two hooks would be a remedy chosen by
    # guessing. One state, one finding.
    if isinstance(world_state_owner, _WorldOwnerUnreadable):
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_OWNER_UNKNOWABLE,
            message=(
                f'The world-owner check did NOT run: {world_state_owner.reason}. `world_state.owner` '
                f'decides whether this harness must export `{_RESET_HOOK}` — an `agent`- or '
                f'`external`-owned world the run cannot reset is held at start (exit 2) — and '
                f'nothing here has read it, so an unreset bench would NOT have been reported. Re-run '
                f'this check on an interpreter with pyyaml, against a config that parses.'
            ),
            skill_step='Step 8',
        ))
    elif isinstance(world_state_owner, _WorldOwnerConflict):
        declared = ', '.join(f'{path}: `world_state.owner: {owner}`' for owner, path in world_state_owner.declarations)
        findings.append(Finding(
            severity='fail',
            code=Code.WORLD_OWNER_CONFLICT,
            message=(
                f'`world_state.owner` is declared more than once and the declarations disagree '
                f'({declared}). Both rungs reach the runner — it reads the config beside '
                f'simulate.py AND the one at the repo root above it — so nothing here can say '
                f'who owns the world this suite scores, and neither can the run: it is refused '
                f'at start (exit 2) rather than letting a discovery order pick the winner. '
                f'Remove one of the two declarations, or make them agree.'
            ),
            skill_step='Step 8',
        ))
    elif (
        world_state_owner in _WORLD_OWNERS_NEEDING_RESET
        and _RESET_HOOK not in simulate_only_bindings
        and shared_world_ok
    ):
        # The user's explicit opt-in (`world_state.shared_world_ok: true`): the run proceeds with the
        # `shared_world` risk, mirrored by the runner, which lifts its `world.unreset_external` hold on it.
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_OWNER_UNRESET,
            message=(
                f'plurai.config.yaml declares `world_state.owner: {world_state_owner}` and simulate.py binds no '
                f'`{_RESET_HOOK}`, so every conversation after the first runs on its predecessors\' writes. '
                f'{_shared_world_risk(live_writers)}{_mock_writers_first(live_writers)}'
            ).rstrip(),
            skill_step='Step 8',
        ))
    elif (
        world_state_owner in _WORLD_OWNERS_NEEDING_RESET
        and _RESET_HOOK not in simulate_only_bindings
    ):
        findings.append(Finding(
            severity='fail',
            code=Code.WORLD_OWNER_UNRESET,
            message=(
                f'plurai.config.yaml declares `world_state.owner: {world_state_owner}`, so the '
                f'world this suite scores is not the mock\'s and the run cannot reset it — but '
                f'simulate.py binds no `{_RESET_HOOK}` at module scope. Every scenario after the '
                f'first would run on its predecessor\'s writes, and the verdicts would be about '
                f'the run order, on a run that completes green. '
                f'{_mock_writers_first(live_writers)}Export '
                f'`def {_RESET_HOOK}() -> None:` at module scope and clear the stores this '
                f'harness owns — the child calls it before every scenario, after the mock session '
                f'is opened and before seed_state puts that scenario\'s preconditions in (do NOT '
                f'clear the mock\'s own world there: the backend re-seeds it per scenario). If the '
                f'deployed mock really owns every record this agent reads, declare '
                f'`world_state: {{owner: mock}}` instead. Only on the user\'s explicit opt-in to '
                f'conversations seeing each other\'s writes, declare `world_state.{_SHARED_WORLD_OK_KEY}: true`: '
                f'the run then proceeds with the named risk `shared_world`. Until one of these, the run is held '
                f'at start (exit 2) unless that finding is overruled with what was verified.'
            ),
            skill_step='Step 8',
        ))

    # 1b'. Live writes on a world nothing returns to the same state before each conversation: ONE
    # verdict on the facts, whatever `world_state.owner` says (declared, undeclared or `mock`), so an
    # honest declaration and a silent one get the same answer. The scan mocks every write surface by
    # default, so this is reached only by a bench written before that default or by the user's
    # explicit opt-in to live writes (`world_state.shared_world_ok: true`), which it names as the
    # `shared_world` risk the run carries. Silent on a conflicting or unreadable owner, whose own
    # finding above stands for the bench, and beside an opted-in `world_owner_unreset`, which already
    # names the risk.
    owner_named_the_risk = shared_world_ok and world_state_owner in _WORLD_OWNERS_NEEDING_RESET
    if (
        live_writers
        and _RESET_HOOK not in simulate_only_bindings
        and not isinstance(world_state_owner, (_WorldOwnerConflict, _WorldOwnerUnreadable))
        and not owner_named_the_risk
    ):
        head = (
            f'tools {", ".join(live_writers)} write a live system and simulate.py binds no '
            f'`{_RESET_HOOK}` that returns it to the same state before every conversation, so each '
            f'conversation after the first runs on its predecessors\' writes and its verdicts depend on '
            f'the run order. '
        )
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_LIVE_WRITER_UNRESET,
            message=(
                f'{head}{_shared_world_risk(live_writers)}{_mock_writers_first(live_writers)}'.rstrip() if shared_world_ok else
                f'{head}{_mock_writers_first(live_writers)}Else export `def {_RESET_HOOK}() -> None:` '
                f'at module scope that resets them per conversation. Keep them live only on the user\'s '
                f'explicit opt-in (`world_state.{_SHARED_WORLD_OK_KEY}: true`), and conversations then see each other\'s writes.'
            ),
            skill_step='Step 8',
        ))

    # 1c. ...and the SECOND thing that same declaration implies, which is NOT a refusal: a world
    # the mock does not own is also a world nothing in the run can READ unless the harness hands
    # it over.
    #
    # The child snapshots the MOCK's session at finalize. For a bench whose world is its own, that
    # session is not the world the declaration names — so an empty one reaches the judges as an
    # OBSERVED, EMPTY world and `len(entities_of_type('x')) == 0` scores a CONCLUSIVE PASS about a
    # store nobody read. `world_snapshot` is the only exit: the child returns whatever it hands
    # back as that scenario's `final_world_state.state`.
    #
    # WARN, NOT FAIL, and the severity is the finding. Its sibling above fails because an unreset
    # world corrupts every scenario after the first, transcript included — nothing is salvageable,
    # and the runner holds the run at start (exit 2). This one costs only the WORLD-READING
    # policies: the dialog happens and every llm_as_judge policy scores exactly as it would have,
    # and the runner WARNS on it (`orchestrator.unobservable_world_warning`, stderr, rc unchanged)
    # rather than refusing. A `fail` here would exit 2 on a bench the runner is willing to drive,
    # which is a verifier that refuses what the product accepts.
    #
    # NOT CHAINED to the reset gate above, deliberately: the two hooks are two INDEPENDENT things
    # owed about one decided owner, not two candidate remedies for one state. Chained, an author
    # who adds `reset` learns about the second obligation only on a second run of this verifier,
    # and the declaration's full cost is never on screen at once. The undecidable states stay
    # silent anyway without a chain — a `_WorldOwnerConflict` / `_WorldOwnerUnreadable` is not one
    # of the two words, so the conjunct below is false for both, which is the same "one state, one
    # finding" the arms above spell out.
    if (
        world_state_owner in _WORLD_OWNERS_NEEDING_RESET
        and _WORLD_SNAPSHOT_HOOK not in simulate_only_bindings
    ):
        findings.append(Finding(
            severity='warn',
            code=Code.WORLD_OWNER_UNOBSERVABLE,
            message=(
                f'plurai.config.yaml declares `world_state.owner: {world_state_owner}`, so the '
                f'world this suite scores is not the mock\'s — but simulate.py binds no '
                f'`{_WORLD_SNAPSHOT_HOOK}` at module scope, so nothing in the run can read that '
                f'store. The dialog and every llm_as_judge policy are unaffected; every '
                f'WORLD-READING policy is demoted to not-measured (`world_unobservable`) rather '
                f'than passing vacuously on the mock\'s session. Export '
                f'`def {_WORLD_SNAPSHOT_HOOK}() -> dict:` at module scope, returning '
                f'`{{entity_id: {{...}}}}` for the store this harness owns — the child calls it at '
                f'finalize. If part of that world really IS in the mock, declare '
                f'`world_state.owned_types: [<the types your store holds>]` and return only your '
                f'store\'s rows: the run composes them with the mock\'s rows of every other type. If the deployed '
                f'mock really owns every record this agent reads, declare '
                f'`world_state: {{owner: mock}}` instead. The run still starts either way — it '
                f'warns at start and abstains on the world.'
            ),
            skill_step='Step 8',
        ))

    # 1d. A read the snapshot DERIVES (a produced file's preview) that fails must not arrive as
    # content: the judges read the slot as the file's text and certify what nobody saw. Fail when the
    # harness never names the slot — no failed read can be typed at all; warn where a caller may route it.
    read_error_severity = 'warn' if _names_the_slot(tree) else 'fail'
    for line in _snapshot_read_errors_as_content(tree, simulate_only_tree):
        findings.append(Finding(
            severity=read_error_severity,
            code=Code.SNAPSHOT_READ_ERROR_AS_CONTENT,
            message=(
                f'code reachable from `{_WORLD_SNAPSHOT_HOOK}` sends a caught exception\'s text somewhere other than a log '
                f'call, a raise or the `{_UNREADABLE_KEY}` slot, so a failed read reaches the judges as a snapshot value, as if it '
                f'were the entity\'s content. Return None there and '
                f'list the field under `{_UNREADABLE_KEY}: {{"<field>": "<ExcClass>: <message>"}}`; install the reader\'s extras in the '
                f'child interpreter (they ship with the SDK\'s own dependencies).'
            ),
            skill_step='Step 8',
            line=line,
        ))

    # 1e. A file in the snapshot is rendered by the SDK's one reader, `plurai.file_row`. A reader written
    # here drops what file_row shows (formula text beside its stored value, docx headers and footers, pptx
    # tables) and the typed truncation the judges' recipe reads. Advisory: a store that really is a
    # spreadsheet of records (rows are entities, not a file's preview) is overruled with that evidence.
    hand_rolled = _hand_rolled_file_reader(tree, simulate_only_tree)
    if hand_rolled is not None:
        reader, line = hand_rolled
        findings.append(Finding(
            severity='fail',
            code=Code.SNAPSHOT_HAND_ROLLED_FILE_READER,
            message=(
                f'code reachable from `{_WORLD_SNAPSHOT_HOOK}` reads a document with `{reader}` and never calls '
                f'`plurai.file_row`, so the judges get this harness\'s rendering of the file, not the SDK\'s. Build each file\'s '
                f'row with one call, `plurai.file_row(path, entity_type=...)` (`preview=False` for a file handed to the agent), '
                f'and declare its fields (`name`, `suffix`, `size_bytes`, `sha256`, `text_preview`). If the reader builds '
                f'records that are entities of their own rather than a file\'s preview, overrule this finding with that evidence.'
            ),
            skill_step='Step 8',
            line=line,
        ))

    # 2. invoke_agent must NOT contain asyncio.run() — that's the running-loop footgun.
    if invoke is not None:
        offending_line = _calls_asyncio_run(invoke.body)
        if offending_line is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.ASYNCIO_RUN_IN_INVOKE_AGENT,
                message=(
                    'invoke_agent body calls asyncio.run(...). The orchestrator '
                    'already drives an event loop and awaits this hook. Use '
                    '`async def invoke_agent` and `await graph.ainvoke(...)`.'
                ),
                skill_step='Step 8',
                line=offending_line,
            ))

    # 2a-native-trail. The config declares tools the agent runs where no plurai lane sees them (no
    # patch, no mock event), so the trail `plurai.confine` records is the only record of them; a
    # harness that does not pass confine the trail, or does not return it, leaves the run's tool
    # channel not measured.
    if invoke is not None and declared_native_tools:
        missing, unconfirmed = _native_trail_gaps(tree)
        if unconfirmed and not missing:
            findings.append(Finding(
                severity='warn',
                code=Code.NATIVE_TOOL_TRAIL_MISSING,
                message=(
                    f'`agent.native_tools` declares {", ".join(declared_native_tools)}; this check could not tell that '
                    f'{"; ".join(unconfirmed)} (it reads names, and the returned value goes through one it cannot follow). '
                    'If it is another list, the run reports the tool channel observed with nothing in it — confirm on a smoke '
                    'turn that used a tool that `tool_trail` is non-empty.'
                ),
                skill_step='Step 8',
                line=invoke.lineno,
            ))
        if missing:
            findings.append(Finding(
                severity='fail',
                code=Code.NATIVE_TOOL_TRAIL_MISSING,
                message=(
                    f'`agent.native_tools` declares {", ".join(declared_native_tools)}, which run where no plurai lane sees '
                    f'them, and the harness {"; ".join(missing)}. Pass the thread\'s trail to `plurai.confine` before the '
                    'session connects and return each turn\'s slice of that list as `tool_trail` beside the record as `host_effects` '
                    '(framework-patterns.md, "Claude Agent SDK"); otherwise the run reports the tool channel not measured.'
                ),
                skill_step='Step 8',
                line=invoke.lineno,
            ))

    # 2a-cross-turn. invoke_agent reuses a pre-compiled graph the module never gave a
    # saver → cross-turn agent state is lost between user turns (a harness artifact
    # that surfaces as spurious agent failures + corrupted eval verdicts). Warn
    # because single-turn / pipeline benches legitimately need no persistence.
    if invoke is not None:
        cross_turn_line = _invoke_reuses_checkpointerless_graph(invoke, simulate_only_tree)
        if cross_turn_line is not None:
            findings.append(Finding(
                severity='warn',
                code=Code.CHECKPOINTERLESS_GRAPH_REUSE,
                message=(
                    'invoke_agent reuses a pre-compiled graph (X.graph.ainvoke/invoke) '
                    'but simulate.py builds no persistence handle for it. The orchestrator '
                    'passes a stable per-scenario thread_id every turn — but with nothing '
                    'persisting that is a no-op, so any non-message agent state written '
                    'in turn N (e.g. a selected record id, a cached plan) is discarded '
                    'before turn N+1 and a multi-turn agent spuriously fails. For a dialog '
                    'bench, recompile the graph with the persistence handle your framework '
                    'provides (the test-harness exception to "never add a checkpointer") and '
                    'let the thread_id persist '
                    'state, OR carry prior-turn state into the payload. Single-turn / '
                    'invoke_pipeline benches can ignore this.'
                ),
                skill_step='Step 8',
                line=cross_turn_line,
            ))

    # 2a-stateless. Framework-free twin of 2a-cross-turn: a harness that
    # derives history from payload['messages'] with no thread_id read anywhere runs
    # STATELESS — every turn sees [system, user], the run still completes, and every
    # memory/journey/continuity verdict is meaningless (observed live for a full
    # qualification pass). Gated on NO graph framework so the two checks never
    # double-report one defect.
    if invoke is not None and not _uses_agent_framework(tree):
        stateless_line = _invoke_treats_payload_as_history(invoke, tree)
        if stateless_line is not None:
            findings.append(Finding(
                severity='warn',
                code=Code.PAYLOAD_MESSAGES_AS_HISTORY,
                message=(
                    "invoke_agent iterates/slices payload['messages'] as if it were the "
                    'conversation, but the SDK sends only the ONE new user message per turn '
                    "and this module never reads config['configurable']['thread_id'] — the "
                    'harness runs STATELESS (every turn sees [system, user]) and nothing else '
                    'will flag it; memory/continuity verdicts become meaningless. Keep a '
                    'module-level dict of message lists keyed on thread_id, append the new '
                    "HumanMessage + the agent's reply each turn, and feed THAT to the agent. "
                    'Confirm with verify_smoke.py --turn (its turn-2 recall probe).'
                ),
                skill_step='Step 8',
                line=stateless_line,
            ))

    # 2a-resume. The turn's return IS the regression pin's `agent_state`: a dict picked out of the
    # saved graph state resumes the agent into a state production never reaches (a done-flag
    # restored without the value the flagged step set). Fail, like the rest of the resume family:
    # no env is fresh-only.
    if invoke is not None:
        picked_line = _invoke_returns_picked_state(invoke)
        if picked_line is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.INVOKE_AGENT_RETURNS_PICKED_STATE,
                message=(
                    "invoke_agent returns a dict of keys picked from the thread's saved state "
                    '(get_state/aget_state .values) with no ** spread. That return is the only source '
                    "of a regression pin's agent_state, so a resumed scenario restores only the picked "
                    'keys and the agent routes on a state production never reaches. Spread the saved '
                    "values under your own keys — {**{k: v for k, v in values.items() if k != 'messages'}, "
                    "'messages': new_messages, ...} — or return the graph's invoke result as the template "
                    'does. An existing bench needs a re-scan. See references/resumed-seed-state.md.'
                ),
                skill_step='Step 8',
                line=picked_line,
            ))

    # 2a-resume-slots. The other half of the same pairing: `seed_state` restores the keys it keeps a slot for from
    # the pin's `agent_state`, which is `invoke_agent`'s return. When not one slot is a top-level key of that return
    # (the state nested under one key, or never returned), every resumed regression reopens without the agent's state.
    # Quiet when the returned keys cannot be read, or `seed_state` unpacks a read of a returned key (`**`, `.update`);
    # a dict literal handed to `.update` stores under its keys, it unpacks nothing. A session keyed by thread is restored
    # where the thread id is known: in `invoke_agent` or a function either hook reaches. That deferred restore is read
    # the same way and warns (never holds).
    if invoke is not None and seed is not None:
        functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        slots, restorer, where, scope = _seed_state_slots(tree, seed), seed, 'seed_state', [seed]
        if not slots:
            reached = _reached_functions(functions, ('seed_state', 'invoke_agent'))
            scope = [seed, invoke, *(functions[n] for n in reached)]
            for fn, root in [(invoke, ''), *((functions[n], r) for n, r in reached.items())]:
                if (found := _seed_state_slots(tree, fn, seed_key_only=True)) and not slots:
                    restorer, where = fn, f'{fn.name}, which {root} runs,' if root else fn.name
                slots |= found
        returned = _returned_keys(invoke, functions) if slots else None
        unpacked = [v for f in scope for d in ast.walk(f) if isinstance(d, ast.Dict) for k, v in zip(d.keys, d.values) if k is None]
        unpacked += [a for f in scope for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'update' for a in c.args if not isinstance(a, ast.Dict)]
        read = [n.args[0] if isinstance(n, ast.Call) else n.slice for u in unpacked for n in ast.walk(u) if (
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('get', 'pop') and n.args) or (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load))]
        unnested = {c.value for c in read if isinstance(c, ast.Constant)} & ((returned or set()) - {'messages', 'agent_state'})
        if returned is not None and not slots & returned and not unnested:
            findings.append(Finding(
                severity='fail' if restorer is seed else 'warn',
                code=Code.SEED_STATE_SLOTS_NEVER_RETURNED,
                message=(
                    f'{where} restores {", ".join(sorted(slots))}, and invoke_agent returns none of them at the top '
                    f'level ({", ".join(sorted(returned))}), so every resumed regression reopens without the agent\'s '
                    'state. Spread its whole state into the return; re-scan an existing bench (references/resumed-seed-state.md).'
                ),
                skill_step='Step 8',
                line=restorer.lineno,
            ))

    # 2a-model. A hand-built assistant message reaches the trace sinks as a model call with no model
    # name and no usage, and a turn that ran tools shows none of them. Warn-only: a framework that
    # exposes neither leaves nothing to carry.
    if invoke is not None:
        bare_line = _assistant_message_without_model_metadata(invoke)
        if bare_line is not None:
            findings.append(Finding(
                severity='warn',
                code=Code.ASSISTANT_MESSAGE_WITHOUT_MODEL_METADATA,
                message=(
                    "invoke_agent builds an assistant message as a dict literal with no model name or token usage "
                    "on it. Trace sinks draw one model call per assistant message of the turn's chain, so this one "
                    'shows as a call with neither, and the turn\'s tool-call steps are missing. Return the turn\'s '
                    'whole chain (assistant messages with their tool calls, the tool results, the final reply) with '
                    "the model and usage the framework reports: copy references/model_calls.py beside simulate.py "
                    "as _model_calls.py and build the messages with it. A value the framework does not expose stays "
                    'out. See references/framework-patterns.md.'
                ),
                skill_step='Step 8',
                line=bare_line,
            ))

    # 2b. invoke_pipeline must NOT contain asyncio.run() either — same
    # running-loop footgun applies to one-shot pipelines.
    if invoke_pipeline is not None:
        offending_line = _calls_asyncio_run(invoke_pipeline.body)
        if offending_line is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.ASYNCIO_RUN_IN_INVOKE_PIPELINE,
                message=(
                    'invoke_pipeline body calls asyncio.run(...). The '
                    'orchestrator already drives an event loop and awaits '
                    'this hook. Use `async def invoke_pipeline` and await '
                    'your pipeline call directly.'
                ),
                skill_step='Step 8',
                line=offending_line,
            ))

    # 3. seed_state must NOT contain asyncio.run() either (symmetric ban).
    if seed is not None:
        offending_line = _calls_asyncio_run(seed.body)
        if offending_line is not None:
            findings.append(Finding(
                severity='fail',
                code=Code.ASYNCIO_RUN_IN_SEED_STATE,
                message=(
                    'seed_state body calls asyncio.run(...). The orchestrator '
                    'awaits this hook. Use `async def seed_state` and `await` '
                    'your storage calls directly.'
                ),
                skill_step='Step 8',
                line=offending_line,
            ))

    # NOTE: the old "module-level singleton storage" enforcement
    # (per_call_storage_factory / no_module_singleton checks) was removed in
    # the world-state-backed rewrite — per-call factory is now correct
    # because the class holds no state. Replaced by check 5 below, which
    # enforces the new contract: storage classes must actually call
    # plurai.world_state, otherwise reads silently return defaults and the
    # agent runs against an empty world without any runtime symptom that
    # setup can see (the first real /simulation-run is where it would surface).

    # 4. When storage is patched, seed_state MUST be defined — unless the patched storage only delegates to declared
    # mocks over a mock-owned world: `start_session` seeds that world, so there is nothing of the harness's to seed.
    delegates = has_storage_factory and _has_storage_class_delegating_to_mocks(tree, declared_mocks)
    mock_owned = world_state_owner in (None, 'mock')
    if has_storage_factory and seed is None and not (delegates and mock_owned):
        findings.append(Finding(
            severity='fail',
            code=Code.MISSING_SEED_STATE,
            message=(
                'Storage factory was patched but no seed_state(...) callable '
                'is defined. Without it, the storage stays empty on turn 1, '
                'tools fail their strict-validation reads, and the React loop '
                'exhausts its iteration budget (GraphRecursionError).'
            ),
            skill_step='Step 6.5',
            line=factories[0].lineno,
        ))

    # 5. When storage is patched, at least one class in the source must
    # use plurai.world_state in a method body. Otherwise the customer's
    # Mock<Entity>Storage returns hardcoded defaults that bypass the mock
    # service entirely — seed_state populates world-state but tool reads
    # never look at it.
    #
    # This is the only static catch for the "stub storage class" silent
    # failure surface: the smoke in Step 9f cannot distinguish "storage
    # returns empty {}" from "agent gracefully handled empty data" — both
    # produce no exception and exit `category=ok`. Without this check,
    # broken adapters ship to backend env-gen.
    # A class that delegates every call to a declared mock (a `plurai.mock_wrap` callable or a `mode: mock` tool) reaches
    # the same world through the mock, which is the shape the one-route rule asks for.
    if has_storage_factory and not _has_storage_class_using_world_state(tree) and not delegates:
        findings.append(Finding(
            severity='fail',
            code=Code.STORAGE_CLASS_NO_WORLD_STATE_REF,
            message=(
                'Storage factory was patched but no class in the source reaches '
                'the mock world: none delegates to a declared mock (a '
                '`plurai.mock_wrap(...)` callable or a `mode: mock` tool of '
                'plurai.config.yaml), and none reads plurai.world_state. The '
                'patched factory returns a class whose reads bypass the mock '
                'service. Make each method a pass-through to the seam mock that '
                'serves the tool (Step 8.4).'
            ),
            skill_step='Step 8.4',
            line=factories[0].lineno,
        ))

    # 5b. FIDELITY, where 5 checked only SHAPE. A storage class that reads world-state can still
    # project it through a record view that drops fields — one harness's did, and the seeded premise
    # three scenarios were built on was invisible to the agent's own read path while the pinned
    # mock, reading the raw record, answered honestly. Gated on 5 passing: where there is no
    # storage class there is nothing to be unfaithful to, and `declared` is None whenever the
    # workspace ships no readable state schema, which is an unanswerable question, not a defect.
    if has_storage_factory and _has_storage_class_using_world_state(tree):
        if isinstance(declared_record_fields, str):
            # THE QUESTION WENT UNASKED, AND THAT HAS TO SHOW. Silence here rendered as a green tick
            # over the known-broken bench: an operator on an interpreter without pyyaml read
            # "satisfies the SKILL.md contract" and had no way to tell it from a clean pass. A warn
            # keeps rc at 0 — an unanswerable question is not a defect in the bench — while naming
            # which state it is, the way `helper_unreadable` does one check over.
            findings.append(Finding(
                severity='warn',
                code=Code.STORAGE_FIDELITY_UNKNOWABLE,
                message=(
                    f'The storage-fidelity check did NOT run: {declared_record_fields}. Every other check above still '
                    f'applies, but nothing here has confirmed that what the storage class reads back carries the fields '
                    f'a scenario seeds — so a seeded premise the agent cannot see would not have been reported (Step 8.4).'
                ),
                skill_step='Step 8.4',
                line=factories[0].lineno if factories else None,
            ))
        elif declared_record_fields:
            unread = _declared_fields_not_read_by_storage(tree, declared_record_fields)
            if unread:
                findings.append(Finding(
                    # WARN, not fail, and the ticket's own failure-mode paragraph is why: "a bench whose
                    # state schema over-declares fields the agent genuinely does not surface gets one
                    # finding to dismiss". A `fail` is rc=1, and Step 9e says do not proceed with
                    # violations outstanding — there is no dismissal move in the recipe, so `fail` made
                    # that sentence false and turned a reachable false positive (a field carried by
                    # attribute access rather than a string key) into a hard block on a correct bench.
                    severity='warn',
                    code=Code.STORAGE_CLASS_DROPS_DECLARED_FIELD,
                    message=(
                        f'The storage class never reads {", ".join(unread)} under any spelling, but the '
                        f'state schema declares {"it" if len(unread) == 1 else "them"} as a record field. '
                        f'A scenario that seeds {"that field" if len(unread) == 1 else "those fields"} '
                        f'writes a premise the agent cannot see: its reads come back without '
                        f'{"it" if len(unread) == 1 else "them"}, so it either ignores the premise or '
                        f'manufactures the state itself — and the run charges that write to the agent. '
                        f'Carry the field through the record projection, or through the same side channel '
                        f'production uses to rehydrate it (Step 8.4).'
                    ),
                    skill_step='Step 8.4',
                    line=factories[0].lineno if factories else None,
                ))

    # 6. A harness object installed over agent code that reads or writes the world itself is harness-authored store
    # behaviour: the harness may only translate and delegate. A warn — "computes" is not decidable here, the route is.
    for name, line in _harness_store_serves_world(tree, world_access_exempt):
        findings.append(Finding(
            severity='warn',
            code=Code.HARNESS_STORE_SERVES_WORLD,
            message=(
                f'`{name}` is installed over the agent\'s code and reads or writes plurai.world_state itself, so the harness '
                'implements the store its tools reach. Code between the agent and a store may only translate (names, types, '
                'error classes), delegate (to a declared mock, the original or in-tree implementation, or a declared '
                '`read_through`), or raise HarnessFault. Put the store under a seam mock that names its tools in `serves:` and '
                'make this a pass-through to it, or run the agent\'s own implementation (Step 8.4).'
            ),
            skill_step='Step 8.4',
            line=line,
        ))

    # 7 / 7b / 7c. Everything the seed_state hook's signature and body owe the
    # contract, behind ONE presence guard: a module that defines no seed_state
    # at all gives every rule below nothing to judge, and each re-testing that
    # separately is what produced the `x_use is not None and x_use.is_violation`
    # pairs — two names tracking one fact, where the None arm meant "no hook"
    # rather than "no violation".
    if seed is not None:
        # 7. seed_state (when defined) must accept
        # (agent_context, world_state, resume_seed).
        # Replaces the old "must be passed to Orchestrator(seed_state=...)" check
        # — under the subprocess model the child auto-discovers seed_state from
        # the module, so wiring is automatic. The remaining failure mode is a
        # wrong-arity signature that explodes inside the IPC handshake.
        accepts_contract_arity = _seed_state_accepts_contract_arity(seed)
        if not accepts_contract_arity:
            findings.append(Finding(
                severity='fail',
                code=Code.SEED_STATE_BAD_SIGNATURE,
                message=(
                    'seed_state must accept at least three positional arguments: '
                    '(initial_agent_context, initial_world_state, resume_seed). '
                    'The child calls it with that shape during init on EVERY '
                    'scenario — resume_seed is None for a fresh one, never '
                    'withheld — so a two-parameter hook raises TypeError before '
                    'turn 1 and surfaces as a bare InitError. The third parameter '
                    'MUST also carry a default (resume_seed: dict | None = None), '
                    'which keeps it inert under any caller still passing two '
                    'arguments. This check gates the arity only; the default is '
                    'readable in the signature and simply is not gated here. See '
                    'references/resumed-seed-state.md.'
                ),
                skill_step='Step 6.5',
                line=seed.lineno,
            ))

        # 7b. seed_state body must reference its initial_world_state parameter.
        # The Plurai backend initial-state extractor populates per-scenario entity
        # history there; ignoring it makes seed_state synthesise an empty
        # __r0 revision and turn 1 reads 0 records, exhausting the React loop.
        # Independent of the arity gate above: a two-positional hook is both
        # wrong-arity AND (possibly) world-state-ignoring, and both need fixing.
        # ``_seed_state_param_use`` reports NOT_DECLARED — not a violation — when
        # there is no second parameter to read, so the arity gate keeps that case.
        world_state_use = _seed_state_param_use(seed, SeedStateParam.WORLD_STATE)
        if world_state_use.is_violation:
            findings.append(Finding(
                severity='fail',
                code=Code.SEED_STATE_IGNORES_WORLD_STATE,
                message=(
                    f'{world_state_use.describe()}. '
                    'Param 2 is the STORE (simulation-setup Step 6.5, "THE WIRE, '
                    'STATED ONCE"): read it, apply it verbatim when non-empty, '
                    'raise if you cannot serve it. The scenario\'s per-revision '
                    'entity history (records, labels, example_sets, commit_id) '
                    "lives in initial_world_state['entities'] — discarding it "
                    "makes seed_state synthesise an empty seed, and the agent's "
                    'first read from world_state returns zero records, exhausting '
                    'the retry loop (OrchestratorAdapterError → GraphRecursionError). '
                    'The store is empty only when the bench declares no world type, '
                    'and then empty is the correct value, not a missing one — so '
                    'reading the parameter and finding nothing is the whole obligation there; '
                    'what is refused is never looking. Param 1 is a different '
                    'surface: it carries the `inject: request` values, which also '
                    'arrive in the turn-1 payload, so seed from it only where your '
                    'agent reads them from a storage surface. See Step 8.5 for the '
                    'canonical shape and ``_seed_state_param_use`` for the '
                    'static-check heuristic (which can be fooled — pair with '
                    'Step 9f smoke).'
                ),
                skill_step='Step 8.5',
                line=world_state_use.line,
            ))

        # 7c. seed_state body must reference its resume_seed parameter — a flat
        # fail, because no environment is entitled to assume its scenarios are
        # fresh. `plurai convert-trace` writes a resume pin into EVERY bundle it
        # converts: capability is not declared in plurai.config.yaml, is not
        # inferred from any config key, and is not gated at the converter. "This
        # env is fresh-only" is therefore not a fact this script — or any config
        # file — can learn. The posture is fail-closed: a seed_state that accepts
        # the seed must either replay it or raise on it. Both of those READ the
        # parameter, so only accept-and-drop trips this check.
        #
        # This is the static half of a gap the SDK cannot close: it refuses a
        # resumed scenario with NO seed_state, but cannot refuse one whose
        # seed_state takes the seed and drops it. Nothing at runtime can — the
        # SDK never reads inside the hook.
        resume_seed_use = _seed_state_param_use(seed, SeedStateParam.RESUME_SEED)
        if resume_seed_use.is_violation:
            findings.append(Finding(
                severity='fail',
                code=Code.SEED_STATE_IGNORES_RESUME_SEED,
                message=(
                    f'{resume_seed_use.describe()}. '
                    'Every trace bundle the converter writes carries a resume pin, '
                    'so any scenario set for this environment may arrive with a '
                    'transcript to replay — no env is declared fresh-only and none '
                    'can be inferred to be. Dropping the seed starts the agent on '
                    'an EMPTY history while the orchestrator excises turn 1 as '
                    'already-said, and the run scores a conversation that never '
                    'happened — green, with nothing in the logs. Branch on the '
                    'seed and replay it into the checkpointer that invoke_agent '
                    'uses: convert_to_messages(resume_seed["messages"]) + await '
                    'graph.aupdate_state(config, values, as_node=<terminal node>), '
                    'then assert next == (), then honour or refuse the seed\'s '
                    'optional resume_point (seed_state_ignores_resume_point covers '
                    'that half). A harness with its own thread-keyed store writes '
                    'the transcript into the store invoke_agent reads (and, only when '
                    'invoke_agent returns just this turn\'s messages, the last seeded '
                    'turn\'s tool_trail as call and result pairs). Reading '
                    'the parameter is all this check can see — see '
                    'references/resumed-seed-state.md for the ways that write still '
                    'half-succeeds.'
                ),
                skill_step='Step 6.5',
                line=resume_seed_use.line,
            ))

        # 7d. A harness that handles the seed owes an answer on its optional third
        # key too. `convert-trace --start-node` pins the node turn 1 must start at,
        # and the value travels to this hook uninterpreted — the SDK and the backend both
        # ferry it, so a harness that never names it is the whole enforcement gap.
        # Dropping it runs the graph from its entry point: the agent redoes the steps
        # the trace already took, and the scenario scores that as the pinned resume.
        # Green, with nothing in the logs, exactly like the accept-and-drop above.
        #
        # Guarded on the two findings that already own a narrower defect, so one edit
        # is reported once: a hook with no third parameter has nothing to read the key
        # off, and a hook that drops the seed entirely has to take the seed first. A
        # hook that raises on any seed is exempt for the opposite reason — it has
        # already refused the pin, node and all.
        if (
            accepts_contract_arity
            and not resume_seed_use.is_violation
            and not _refuses_any_seed(seed, resume_seed_use.declared_name)
            and not _names_resume_point(tree)
        ):
            findings.append(Finding(
                severity='fail',
                code=Code.SEED_STATE_IGNORES_RESUME_POINT,
                message=(
                    f'{resume_seed_use.describe()}, but nothing in this harness names the '
                    f"seed's optional {_RESUME_POINT_KEY!r} key. When an operator converts a "
                    'trace with --start-node, the seed carries the top-level graph node turn 1 '
                    'must start at; a harness that drops it runs from the graph entry point '
                    'instead, redoing the steps the trace already took, and the run scores that '
                    'as the pinned resume — green, with nothing in the logs. On a graph harness, '
                    "HONOUR it: read resume_seed.get('resume_point'), refuse a node that is not "
                    'in the compiled graph (an unknown name and a subgraph interior are both '
                    'SILENT no-ops), and start TURN 1 ONLY at it — Command(goto=node, '
                    'update=payload) as the first invoke, plain payload on every later turn. A '
                    'harness with no graph node to start from REFUSES instead, naming the node: '
                    "`point = resume_seed.get('resume_point')` then `if point: raise "
                    'RuntimeError(...)`, which turns an unhonourable pin into a loud failure '
                    'rather than a silent full-replay. Accept-and-drop is the only shape that '
                    'trips this. See references/resumed-seed-state.md.'
                ),
                skill_step='Step 6.5',
                line=resume_seed_use.line,
            ))

        # 7e. A hook that refuses EVERY seed is a harness that cannot resume — legal only where
        # resuming is impossible, and then DECLARED, typed, in the bench manifest
        # (`harness.resume: {mode: unsupported, reason, evidence}`). A fresh `init` writes
        # `{mode: replay}`, so a refusal there FAILS; a manifest that predates the field (or none)
        # only WARNS, because a refresh never adds it and its fresh scenarios still run.
        if (
            resume_declaration != _NOT_DIALOG
            and accepts_contract_arity
            and not resume_seed_use.is_violation
            and _refuses_any_seed(seed, resume_seed_use.declared_name)
        ):
            declared = resume_declaration if isinstance(resume_declaration, dict) else None
            unsupported = declared is not None and declared.get('mode') == 'unsupported'
            typed = unsupported and declared.get('reason') in _RESUME_UNSUPPORTED_REASONS and bool(declared.get('evidence'))
            if not typed:
                how = (
                    'Replay the seed instead (references/resumed-seed-state.md: the transcript, plus the agent_state keys '
                    'the agent holds — a thread-keyed store whose invoke_agent returns only this turn\'s messages folds the '
                    'last seeded turn\'s tool_trail back as call and result pairs; one returning its full history drops '
                    'and names it). Only where the conversation lives in a session the harness cannot write, declare '
                    'it in bench_manifest.json: "harness": {"resume": {"mode": "unsupported", "reason": '
                    f'"{sorted(_RESUME_UNSUPPORTED_REASONS)[0]}", "evidence": "simulate.py:<line of the raise>"}}}}.'
                )
                if declared is None:
                    lead = ('seed_state raises on every resume seed, and the bench manifest predates `harness.resume` '
                            '(or there is none), so no regression replay can ever run on this harness. ')
                elif unsupported:
                    lead = (f'harness.resume declares mode unsupported without a reason from '
                            f'{sorted(_RESUME_UNSUPPORTED_REASONS)} and an evidence citation. ')
                else:
                    lead = ('seed_state raises on every resume seed, but the bench manifest declares '
                            f'harness.resume mode {declared.get("mode")!r}: the harness claims a replay it refuses. ')
                findings.append(Finding(
                    severity='warn' if declared is None else 'fail',
                    code=Code.SEED_STATE_REFUSES_EVERY_SEED,
                    message=lead + how,
                    skill_step='Step 6.5',
                    line=resume_seed_use.line,
                ))

    # NOTE: there is deliberately no clear()-then-seed reset check.
    # plurai.world_state is per-session by construction, so scenario N+1
    # cannot see scenario N's writes — there is nothing to reset between
    # scenarios. seed_state simply writes the new session's entities
    # through plurai.world_state.apply.

    # 8.1. Mock-coroutine wrapper must not silently drop response fields.
    # The anti-pattern ``msg = result.get("message") or str(result)`` renders
    # the SDK's empty-stub fallback ``{}`` as the string ``'{}'`` — an
    # "ambiguous empty success" to the agent's React loop. On its own that is
    # one confusing tool result; it cascades into GraphRecursionError on
    # multi-clause user turns only where nothing else tells the loop an error
    # occurred, which is the case whenever the wrapper is the only thing
    # reading the stub's error signal. The Step 8.4 closure-tool dispatch
    # wrapper closes both halves: it renders the full result as
    # ``json.dumps(result)`` AND detects ``_plurai_stub_error`` to emit
    # ``ToolMessage(status='error', ...)``.
    drop_line = _wrapper_drops_response_fields(tree)
    if drop_line is not None:
        findings.append(Finding(
            severity='fail',
            code=Code.WRAPPER_DROPS_RESPONSE_FIELDS,
            message=(
                'A mock-coroutine wrapper uses the anti-pattern '
                '`result.get("message") or str(result)` — this renders the '
                'SDK\'s empty-stub fallback as the string `\'{}\'`, which the '
                'agent sees as "ambiguous empty success" and tries to '
                'rephrase-and-retry until GraphRecursionError. Render the FULL '
                'result instead — `json.dumps(result)`, or `_render_content(result)` '
                'when the agent declares an `agent.tool_content_envelope` — and add '
                'an `_plurai_stub_error` branch that emits '
                '`ToolMessage(status="error", ...)`. See Step 8.4 '
                'closure-tool dispatch wrapper for the canonical template.'
            ),
            skill_step='Step 8.4',
            line=drop_line,
        ))

    # 8.4b. The wrapper must render through the DECLARED envelope. Gated on the
    # config-derived kind for the reason check 9 moved to verify_config: this file
    # cannot see plurai.config.yaml, and a check that guesses fires on every agent
    # that has no convention at all. With a kind passed in, an envelope authored in
    # the config and ignored by the wrapper is a silent divergence — the mocked
    # tools hand the model one shape, the real tools another, and the agent is
    # measured against a contract it does not have.
    findings.extend(_envelope_findings(tree, tool_content_envelope))
    findings.extend(_state_update_findings(tree, mock_state_updates, _detect_framework(tree, declared_framework), declared_framework))

    # 8.5. ``isinstance(<…>.entities, dict)`` is always False — the
    # world-state proxy is a Mapping, not a dict subclass. The bug it used
    # to enable: handler falls through to a ``return {}`` branch and every
    # tool sees empty world state regardless of scenario seeding.
    for offending_line in _isinstance_entities_dict_misuse(tree):
        findings.append(Finding(
            severity='fail',
            code=Code.ENTITIES_PROXY_ISINSTANCE_DICT_MISUSE,
            message=(
                'isinstance(<…>.entities, dict) is always False — '
                'world_state.entities is a Mapping-like proxy, not a dict '
                'subclass. To read scenario data, use one of: '
                'ctx.world_state.get_entity(entity_id), '
                'ctx.world_state.query(entity_type, **filters), '
                'or plurai.world_state_snapshot().'
            ),
            skill_step='Step 8',
            line=offending_line,
        ))

    # NOTE: check 9 (``tools_not_wrapped``) moved to verify_config.py.
    # Only plurai.config.yaml knows whether the agent actually declares
    # tools that need a mock — checked here, a tool-less (pure-LLM) agent
    # produced a false-positive warning on every run. verify_config gates
    # the check on the config's declared tools and raises it to fail
    # severity when mock-needing tools exist but simulate.py wraps nothing.

    # 11. _build_state drift vs the agent's state class (optional; skipped
    # silently when no state_class_dotted is provided or the class can't be
    # imported on this PYTHONPATH).
    findings.extend(_build_state_drift(tree, state_class_dotted))

    # 12. .env is the CUSTOMER's responsibility and nothing was checking it.
    # plurai/_dotenv.py states the contract outright — "the customer's simulate.py
    # seeds os.environ" — but the child subprocess imports simulate.py and nothing
    # else, so if simulate.py never loads .env then every DEFAULT_LLM__* / provider
    # key in that file is invisible to the agent. The failure is silent and total:
    # the agent falls back to whatever its code defaults to and the run still goes
    # GREEN, so the suite benchmarks a model nobody chose. (On a provider that
    # rejects an unsupported kwarg it fails confusingly instead.) Warn, not fail:
    # exporting the vars in the real environment is a legitimate setup.
    if _loads_dotenv(tree) and _dotenv_import_swallowed(tree):
        findings.append(Finding(
            severity='warn',
            code=Code.DOTENV_IMPORT_SWALLOWED,
            message=(
                'simulate.py wraps its dotenv import in `try/except ImportError: pass` — statically it '
                'looks covered, but with python-dotenv missing it silently does NOTHING and the agent '
                'falls back to its code defaults WHILE THE RUN STILL PASSES (the exact failure the '
                'dotenv block exists to prevent). Make the except branch LOUD (print/log a warning '
                'naming the fallback) or drop the try and let the ImportError surface.'
            ),
            skill_step='Step 9e',
        ))
    # 13. A server the HARNESS spawns owns its own pipes: its output must reach a file, never a
    # DEVNULL, and never the IPC channel simulate.py's own stdout is.
    findings.extend(_spawned_output_findings(tree))

    if not _loads_dotenv(tree):
        findings.append(Finding(
            severity='warn',
            code=Code.NO_DOTENV_LOAD,
            message=(
                'simulate.py never loads a .env (no load_dotenv/dotenv_values). The child '
                'subprocess imports simulate.py and nothing else, so anything configured only '
                'in .env — DEFAULT_LLM__*, provider keys — is invisible to the agent and it '
                'silently falls back to its code defaults WHILE THE RUN STILL PASSES. Either '
                'call load_dotenv() at module import, or make sure those variables are exported '
                'in the environment that launches plurai-run-simulation.'
            ),
            skill_step='Step 9e',
        ))

    return findings


def _declared_world_owner(workspace: Path) -> str | _WorldOwnerConflict | _WorldOwnerUnreadable | None:
    """``world_state.owner`` as the RUNNER reads it, or ``None`` when nothing declares one.

    Read off the config rather than taken as a flag, for the reason the envelope kind is a flag
    and this is not: the envelope check wants the operator's answer for ONE run, while this one
    asks a structural question about the bench that has exactly one right answer on disk. A gate
    that fires only when an operator remembers a flag is a gate that does not fire.

    TWO RUNGS, the workspace order the runner's own discovery uses: the harness directory
    (``plurai_sim/plurai.config.yaml``, where every plugin-authored bench puts it) and the repo
    root above it. A declaration at either rung reaches the runner, so a verifier that read only
    one would hand back a green tick for a run that is about to be refused.

    COLLECT, then refuse on disagreement — the runner's own rule
    (``orchestrator._declared_workspace_value``), and the reason a first-hit read was not enough:
    both rungs reach it, so the ORDER they are walked in does not get to settle which answer the
    env meant. Two rungs saying the same word are one declaration; two saying different words are
    a :class:`_WorldOwnerConflict`, which the caller refuses. A word this file does not act on is
    still collected — the legal SET is ``verify_config.py``'s, and a typo at the rung it was not
    handed is a disagreement it cannot see either.

    SILENT on the questions that are not this file's — no config at all, a ``world_state:`` that
    is not a mapping, a value outside the legal set. "Nothing declares one" is a real answer
    there, and the legal SET and a malformed key are ``verify_config.py``'s subject, run against
    the same file in the same step; two verifiers refusing one typo in two voices teaches an
    author to read neither.

    NOT silent when nothing could be READ: an absent pyyaml, or a config that exists and does not
    parse, is a :class:`_WorldOwnerUnreadable` carrying the reason. That is a different state
    from "read, and nothing declares an owner", and collapsing the two flipped this gate's rc
    from 1 to 0 on the same bench with the same defect.

    Lowered and stripped the way the runner lowers and strips it, so ``External`` here is the
    ``external`` there. The pyyaml import is local and guarded for the reason
    :func:`_declared_record_fields` names: this file runs under the agent's own interpreter.
    """
    try:
        import yaml  # noqa: PLC0415 - see the docstring: a module-scope import would be fatal here
    except ImportError as exc:
        return _WorldOwnerUnreadable(f'pyyaml is not importable in this interpreter ({exc})')
    found: list[tuple[str, str]] = []
    unread: list[str] = []
    for rung in (workspace, workspace.parent):
        config_path = rung / 'plurai.config.yaml'
        if not config_path.is_file():
            continue
        try:
            document = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        except (OSError, yaml.YAMLError) as exc:
            unread.append(f'{config_path} could not be read ({type(exc).__name__})')
            continue
        world_state = (document or {}).get('world_state') if isinstance(document, dict) else None
        if not isinstance(world_state, dict):
            continue
        declared = world_state.get('owner')
        if isinstance(declared, str) and declared.strip():
            found.append((declared.strip().lower(), str(config_path)))
    if not found:
        # A rung that DID declare wins over a sibling nobody could read: that declaration reaches
        # the runner whatever the other file says. Only when no rung declared anything AND one
        # could not be read is the answer "unknown" rather than "nothing declares one".
        if unread:
            return _WorldOwnerUnreadable('; '.join(unread))
        return None
    if len({owner for owner, _ in found}) > 1:
        return _WorldOwnerConflict(declarations=tuple(found))
    return found[0][0]


def _harness_config(workspace: Path) -> dict[str, Any] | str:
    """The harness directory's ``plurai.config.yaml`` (the repo root's when the harness has
    none), ``{}`` when neither exists, or the reason it could not be read."""
    config_path = next((rung / 'plurai.config.yaml' for rung in (workspace, workspace.parent) if (rung / 'plurai.config.yaml').is_file()), None)
    if config_path is None:
        return {}
    try:
        import yaml  # noqa: PLC0415 - see _declared_record_fields: a module-scope import would be fatal here
    except ImportError as exc:
        return f'pyyaml is not importable in this interpreter ({exc})'
    try:
        document = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        return f'{config_path} could not be read ({type(exc).__name__})'
    return document if isinstance(document, dict) else {}


def _harness_tools(workspace: Path) -> dict[str, Any] | str:
    """The ``tools:`` block of :func:`_harness_config`, ``{}`` when absent, or why it could not be read."""
    document = _harness_config(workspace)
    if isinstance(document, str):
        return document
    tools = document.get('tools')
    return tools if isinstance(tools, dict) else {}


def _declared_mocks(workspace: Path) -> frozenset[str]:
    """The ``mode: mock`` tool names (an absent mode is ``mock``) of :func:`_harness_tools`; empty when it is unreadable."""
    tools = _harness_tools(workspace)
    return frozenset(str(n) for n, e in tools.items() if isinstance(e, dict) and (e.get('mode') or 'mock') == 'mock') if isinstance(tools, dict) else frozenset()


def _declared_world_overlays(workspace: Path) -> frozenset[str]:
    """The last segment of every ``tools.<name>.read_through.via`` and ``guards[].via`` accessor of :func:`_harness_config`."""
    config = _harness_config(workspace)
    if not isinstance(config, dict):
        return frozenset()
    tools = config.get('tools') if isinstance(config.get('tools'), dict) else {}
    vias = [v for e in tools.values() if isinstance(e, dict) and isinstance(e.get('read_through'), dict) and isinstance(e['read_through'].get('via'), list)
            for v in e['read_through']['via'] if isinstance(v, str)]
    vias += [g['via'] for g in config.get('guards') or [] if isinstance(g, dict) and isinstance(g.get('via'), str)] if isinstance(config.get('guards'), list) else []
    return frozenset(v.strip().rsplit('.', 1)[-1] for v in vias if v.strip())


#: Why a harness cannot resume, closed. `history_not_writable`: the conversation lives in a session the
#: harness cannot write (a hosted session that opens empty). Mirrors `bench_manifest.schema.json`.
_RESUME_UNSUPPORTED_REASONS = frozenset({'history_not_writable'})


#: `_declared_resume`'s answer for a bench whose manifest dispatches it as a pipeline: nothing to resume.
_NOT_DIALOG = 'not_dialog'


def _declared_resume(workspace: Path) -> dict | str | None:
    """``harness.resume`` of the ``bench_manifest.json`` beside simulate.py; ``None`` when there is no
    readable manifest or it predates the field; :data:`_NOT_DIALOG` on a pipeline bench."""
    try:
        doc = json.loads((workspace / 'bench_manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    if str((doc.get('agent') or {}).get('dispatch_shape') or 'dialog') != 'dialog':
        return _NOT_DIALOG
    resume = (doc.get('harness') or {}).get('resume')
    return resume if isinstance(resume, dict) else None


def env_file_findings(source: str, workspace: Path) -> list[Finding]:
    """``env_file_not_loaded`` (warn): the ``bench_manifest.json`` beside simulate.py declares ``harness.env_file`` (a file
    the user named for the agent's settings, which every presence check reads) and no ``load_dotenv`` /
    ``dotenv_values`` call carries that value as a string, so the agent would run without the file the checks found."""
    try:
        doc = json.loads((workspace / 'bench_manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    declared = ((doc.get('harness') or {}) if isinstance(doc, dict) else {}).get('env_file')
    if not isinstance(declared, str) or not declared.strip():
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        name = node.func.attr if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) else (
            node.func.id if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else None)
        if name in {'load_dotenv', 'dotenv_values'} and any(
            isinstance(sub, ast.Constant) and sub.value == declared.strip() for arg in node.args for sub in ast.walk(arg)
        ):
            return []
    return [Finding(
        severity='warn',
        code=Code.ENV_FILE_NOT_LOADED,
        message=(
            f'bench_manifest.json declares harness.env_file {declared.strip()!r} (the file the agent\'s settings are read '
            'from, and the one the presence checks read) but no load_dotenv/dotenv_values call in simulate.py names it, so '
            "the agent runs without it. Load it in Phase 1: load_dotenv(Path(__file__).resolve().parent.parent / '<value>') "
            'with the value written as declared.'
        ),
        skill_step='Step 8',
    )]


def _declared_native_tools(workspace: Path) -> tuple[str, ...]:
    """``agent.native_tools`` of :func:`_harness_config`, or ``()`` when none are declared or it is unreadable."""
    document = _harness_config(workspace)
    agent = document.get('agent') if isinstance(document, dict) else None
    names = agent.get('native_tools') if isinstance(agent, dict) else None
    return tuple(str(name) for name in names if name) if isinstance(names, list) else ()


def _declared_document_fields(workspace: Path) -> tuple[str, ...]:
    """The fields ``.document_fields`` beside the harness hands over, or ``()`` when there is none or it is unreadable."""
    try:
        entries = json.loads((workspace / '.document_fields').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ()
    if not isinstance(entries, list):
        return ()
    return tuple(dict.fromkeys(e['field'].strip() for e in entries if isinstance(e, dict) and isinstance(e.get('field'), str) and e['field'].strip()))


def _declared_shape(workspace: Path) -> str | None:
    """``shape:`` of :func:`_harness_config` (``dialog`` / ``pipeline``), or ``None`` when undeclared or unreadable."""
    document = _harness_config(workspace)
    shape = document.get('shape') if isinstance(document, dict) else None
    return shape.strip().lower() if isinstance(shape, str) else None


def _is_alias(node: ast.expr, names: set[str]) -> bool:
    """Whether ``node`` is one of ``names`` itself or a direct copy of it: ``x``, ``x or {}``, ``dict(x)``, ``x.copy()``,
    ``{**x}``. Any other expression that merely mentions ``x`` (a call handed the payload, a value read off it) is not."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        return _is_alias(node.values[0], names)
    if isinstance(node, ast.Call) and not node.keywords and len(node.args) == 1 and isinstance(node.func, ast.Name) and node.func.id == 'dict':
        return _is_alias(node.args[0], names)
    if isinstance(node, ast.Call) and not node.args and isinstance(node.func, ast.Attribute) and node.func.attr == 'copy':
        return _is_alias(node.func.value, names)
    if isinstance(node, ast.Dict) and len(node.keys) == 1 and node.keys[0] is None:
        return _is_alias(node.values[0], names)
    return False


def document_read_off_payload_findings(source: str, document_fields: tuple[str, ...], *, shape: str | None) -> list[Finding]:
    """A document the user hands over is read off the TURN'S PARTS, never off the payload or ``seed_state``.

    Every handed-over document arrives as one ``file`` content part (``<field>.<ext>`` carrying a ``data:`` URI) on
    the turn she hands it over, the opener included, and never as a key of the turn payload or of ``seed_state``'s
    ``initial_agent_context``. A harness that reads a ``.document_fields`` field off either reads a key that is never
    there: the agent is handed nothing, and nothing raises. Keyed on the declared field names; the receivers are the
    first parameter of ``invoke_agent`` / ``seed_state``, every module function parameter spelled the same way (a
    helper handed the payload), and any local assigned from one of them. A field name bound to a module-level string
    constant counts as the field. Only a READ is refused (a Load-context subscript, ``.get`` / ``.pop``) — writing the
    field into a dict the harness builds is not reading it — and only off a direct alias of the parameter (``x =
    payload``, ``dict(payload)``, ``payload.copy()``, ``{**payload}``), never a value merely computed from it. A
    ``shape: pipeline`` bench is not checked: its documents are the hop's inputs, read off ``invoke_pipeline``'s.
    """
    if not document_fields or shape == 'pipeline':
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    fields = set(document_fields)
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    receivers = {
        fn.args.args[0].arg for fn in functions if fn.name in ('invoke_agent', 'seed_state') and fn.args.args and fn.col_offset == 0
    }
    if not receivers:
        return []

    def field_of(key: ast.expr) -> str | None:
        value = key.value if isinstance(key, ast.Constant) else constants.get(key.id) if isinstance(key, ast.Name) else None
        return value if isinstance(value, str) and value in fields else None

    findings: list[Finding] = []
    for fn in functions:
        tainted = {a.arg for a in fn.args.args if a.arg in receivers}
        if not tainted:
            continue
        assigns = [node for node in ast.walk(fn) if isinstance(node, ast.Assign)]
        while added := {
            t.id for node in assigns if _is_alias(node.value, tainted) for t in node.targets if isinstance(t, ast.Name) and t.id not in tainted
        }:
            tainted |= added
        for node in ast.walk(fn):
            read: str | None = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('get', 'pop')
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in tainted
                and node.args
            ):
                read = field_of(node.args[0])
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name) and node.value.id in tainted:
                read = field_of(node.slice)
            if read is not None:
                findings.append(Finding(
                    severity='fail',
                    code=Code.DOCUMENT_READ_OFF_PAYLOAD,
                    message=(
                        f'`{fn.name}` reads the handed-over document `{read}` (`.document_fields`) off the turn payload / '
                        f'`seed_state` context. That key is never there: every document the user hands over arrives as one '
                        f'`file` content part on the turn she hands it over, the opener included '
                        f"(`{{'filename': '{read}.<ext>', 'file_data': 'data:<mime>;base64,…'}}`). Read every turn's parts "
                        f'with `turn_documents(content)` (`references/document_input.py`) and hand the agent each '
                        f'`Document(filename, bytes, media_type)` as it came; delete the payload read.'
                    ),
                    skill_step='Step 8',
                    line=node.lineno,
                ))
    return findings


def _read_topology(workspace: Path) -> dict | None:
    """``artifacts/agent_topology.json`` beside the harness, or ``None`` when it is absent or unreadable."""
    try:
        topology = json.loads((workspace / 'artifacts' / 'agent_topology.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return topology if isinstance(topology, dict) else None


#: The observation lane under which zero recorded calls is unknown rather than "not called" (the backend ``observation_lanes``).
_ABSTAINING_LANE = 'none'
#: What a delegating smoke turn showed about a sub-agent's own calls (``render_topology.INNER_CALLS``): ``linked`` = on the
#: trail, each carrying ``parent_call_id`` equal to the delegation row's ``call_id``; ``recorded`` = on the trail, unlinked.
_INNER_CALLS_ON_TRAIL = ('linked', 'recorded')


def subagent_inner_call_findings(topology: dict | None, tools: dict[str, Any] | str) -> list[Finding]:
    """A sub-agent the agent calls (``subagents[].call``) that calls tools itself (``subagents[].tools``) must have those
    calls on the trail, or they must abstain.

    The orchestrator's recorder does not see a tool loop that runs inside a delegation, so unless the harness records
    the sub-agent's calls too, a retrieval made there reads as "not called" and recall goes red on a call that happened.
    ``inner_calls: linked | recorded`` on the map is what one delegating smoke turn showed; anything else needs each owned
    ``mode: real`` / ``observe`` tool to declare ``observation_lane: none``, so its absence is never read as evidence.
    A ``mode: mock`` tool is recorded by its mock wrapper wherever it is called, and is exempt. FAILS, naming the tools.

    ``recorded`` without ``linked`` — the smoke turn's inner rows carried no ``parent_call_id`` equal to the delegating
    row's ``call_id`` — is ADVISORY (``subagent_inner_calls_unlinked``): the calls are scored, but a judge cannot see them
    under the call they ran inside, so a rule about the agent's own message reads each one as a separate action.
    """
    if not isinstance(topology, dict) or not isinstance(tools, dict):
        return []
    findings: list[Finding] = []
    for entry in topology.get('subagents') or []:
        if isinstance(entry, dict) and entry.get('call') is not None and entry.get('inner_calls') == 'recorded':
            findings.append(Finding(
                severity='warn',
                code=Code.SUBAGENT_INNER_CALLS_UNLINKED,
                message=(
                    f'sub-agent {entry.get("name")!r} (called as {entry["call"]!r}): its calls reach the trail without '
                    f'`parent_call_id`, so scoring cannot place them under the `{entry["call"]}` call they ran inside. Record the '
                    f'enclosing call\'s `call_id` as `parent_call_id` on every inner row (framework-patterns.md, "Calls made '
                    f'inside a delegation"), prove it on the delegating smoke turn, and write `inner_calls: linked`.'
                ),
                skill_step='Step 8',
            ))
        if not isinstance(entry, dict) or entry.get('call') is None or entry.get('inner_calls') in _INNER_CALLS_ON_TRAIL:
            continue
        owned = entry.get('tools') if isinstance(entry.get('tools'), list) else []
        unproven = sorted(
            str(t) for t in owned
            if isinstance(tools.get(str(t)), dict) and tools[str(t)].get('mode') != 'mock'
            and str(tools[str(t)].get('observation_lane') or '').strip().lower() != _ABSTAINING_LANE
        )
        if unproven:
            findings.append(Finding(
                severity='fail',
                code=Code.SUBAGENT_INNER_CALLS_UNPROVEN,
                message=(
                    f'sub-agent {entry.get("name")!r} (called as {entry["call"]!r}) calls {", ".join(unproven)} itself, and nothing '
                    f'shows those calls reach the trail (the map says `inner_calls: {entry.get("inner_calls", "<unset>")}`). Record '
                    f'the sub-agent\'s tool calls in the harness and prove it with one delegating smoke turn (`inner_calls: recorded`); '
                    f'until then declare `observation_lane: none` on each, so a call made inside the delegation is never scored as missing.'
                ),
                skill_step='Step 8',
            ))
    return findings


def _declared_live_writers(workspace: Path) -> tuple[str, ...]:
    """The ``mode: real`` tools of :func:`_harness_config` whose ``descriptor.write_strategy`` is ``real``: they write
    the customer's live system. ``()`` when none are declared or the config is unreadable. The same predicate as
    ``verify_config.py``'s ``world_real_writer_unowned``, so the two verifiers name the same writers."""
    tools = _harness_tools(workspace)
    if isinstance(tools, str):
        return ()
    return tuple(sorted(
        str(name) for name, entry in tools.items()
        if isinstance(entry, dict) and entry.get('mode') == 'real'
        and isinstance(entry.get('descriptor'), dict) and entry['descriptor'].get('write_strategy') == 'real'
    ))


def _declared_shared_world_ok(workspace: Path) -> bool:
    """``world_state.shared_world_ok`` of :func:`_harness_config` is exactly ``true`` — the user's explicit opt-in.
    Anything else is no opt-in here; ``verify_config.py`` refuses a value that is not a boolean (`world_shared_ok_value`)."""
    document = _harness_config(workspace)
    world = document.get('world_state') if isinstance(document, dict) else None
    return isinstance(world, dict) and world.get(_SHARED_WORLD_OK_KEY) is True


def shared_world_writers(simulate_path: Path) -> tuple[str, ...] | None:
    """The live writers of a world conversations share by the user's opt-in, or ``None`` when the run carries no
    ``shared_world`` risk — the runner's ``orchestrator.shared_world_risk`` predicate: ``world_state.shared_world_ok:
    true``, no module-scope ``reset`` proven in simulate.py, and a world that carries over (a non-mock owner, or a
    live writer). The run gate reads it to name the risk the run proceeds with."""
    workspace = simulate_path.parent
    if not _declared_shared_world_ok(workspace):
        return None
    tree = _parse_file(simulate_path)
    if tree is not None and _RESET_HOOK in _module_scope_bindings(tree):
        return None
    writers = _declared_live_writers(workspace)
    owner = _declared_world_owner(workspace)
    return writers if writers or owner in _WORLD_OWNERS_NEEDING_RESET else None


def _declared_framework(workspace: Path) -> str | None:
    """``agent.framework`` of :func:`_harness_config`, or ``None`` when it is absent or unreadable
    (detection then falls back to the AST)."""
    document = _harness_config(workspace)
    agent = document.get('agent') if isinstance(document, dict) else None
    framework = agent.get('framework') if isinstance(agent, dict) else None
    return framework if isinstance(framework, str) else None


def _module_file(root: Path, dotted: str) -> Path | None:
    base = root.joinpath(*dotted.split('.'))
    try:
        return next((c for c in (base.with_suffix('.py'), base / '__init__.py') if c.is_file()), None)
    except OSError:  # an unreadable directory on the way: nothing resolves there
        return None


def _source_root(workspace: Path, dotted: str) -> Path | None:
    """The nearest directory at or above the harness from which ``dotted`` resolves to a file."""
    workspace = workspace.resolve()
    return next((d for d in (workspace, *workspace.parents) if _module_file(d, dotted)), None)


def _parse_file(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
        return None


def _defs_by_name(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every def by name, nested ones included (closure-defined tools live inside a factory)."""
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _absolute_import(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    if not node.level:
        return node.module or ''
    package = module.split('.') if is_package else module.split('.')[:-1]
    base = package[: len(package) - (node.level - 1)]
    return '.'.join([*base, *([node.module] if node.module else [])])


class _RealStateWrites(NamedTuple):
    """What a real tool body writes to agent state, by its framework's ``state_write`` rule."""

    framework: _AgentFramework | None
    #: ``None`` when the framework has no ``state_write`` rule — the body was read, the writes were not.
    keys: list[str] | None
    via: list[str]


def _real_state_writes(
    root: Path, module: str, function: str, harness_framework: _AgentFramework | None = None, declared: _AgentFramework | None = None
) -> _RealStateWrites | None:
    """The state keys a real tool body writes, besides ``messages``, following each
    ``return <helper>(...)`` ONE hop — to a def in the same module or through
    ``from X import helper``. ``None`` when the function cannot be found. The write shape is the
    ``state_write`` rule of the framework the config declares, else the one the tool's own module uses
    (the harness's when the module shows none). Also returns the helpers the keys were read through, for the message."""
    path = _module_file(root, module)
    tree = _parse_file(path) if path else None
    local_defs = _defs_by_name(tree) if tree else {}
    fn = local_defs.get(function)
    if tree is None or fn is None:
        return None
    framework = declared or _detect_framework(tree) or harness_framework
    write = framework.state_write if framework is not None else None
    if write is None:
        return _RealStateWrites(framework, None, [])
    keys = write.tool_keys(fn)
    imported = {
        alias.asname or alias.name: _absolute_import(node, module, path.name == '__init__.py')
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
    }
    via: list[str] = []
    for ret in ast.walk(fn):
        if not isinstance(ret, ast.Return):
            value = None
        elif isinstance(ret.value, ast.Await):
            value = ret.value.value
        else:
            value = getattr(ret, 'value', None)
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            continue
        callee = value.func.id
        target = local_defs.get(callee) if callee != function else None
        if target is None and callee in imported and (source := _module_file(root, imported[callee])):
            source_tree = _parse_file(source)
            target = _defs_by_name(source_tree).get(callee) if source_tree else None
        if target is not None and (found := write.tool_keys(target) - {'messages'}):
            keys |= found
            via.append(callee)
    return _RealStateWrites(framework, sorted(keys - {'messages'}), sorted(set(via)))


def _import_closure(root: Path, harness_trees: list[ast.Module]) -> set[str]:
    """The modules under ``root`` the harness imports, transitively (importing ``a.b.c`` runs
    ``a`` and ``a.b`` too)."""

    def deps(tree: ast.Module, module: str | None, is_package: bool) -> set[str]:
        names: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and (module is not None or not n.level):
                source = _absolute_import(n, module or '', is_package)
                names |= {source, *(f'{source}.{a.name}' for a in n.names)}
        out: set[str] = set()
        for name in names:
            parts = name.split('.')
            out |= {'.'.join(parts[:i]) for i in range(1, len(parts) + 1) if _module_file(root, '.'.join(parts[:i]))}
        return out

    todo = [m for tree in harness_trees for m in deps(tree, None, False)]
    seen: set[str] = set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_file(root, module)
        tree = _parse_file(path) if path else None
        if tree is not None:
            todo += list(deps(tree, module, path.name == '__init__.py') - seen)
    return seen


def _lands(spec: object) -> bool:
    """A ``state_updates:`` entry that lands its key: ``{value: ...}`` or ``{from_result: ...}`` (``omit:`` drops it)."""
    return isinstance(spec, dict) and ('value' in spec or 'from_result' in spec)


def _mock_state_source_findings(workspace: Path, harness_sources: list[str], declared_framework: str | None = None) -> list[Finding]:
    """The two backstops that read the AGENT's source, beside the harness.

    ``mock_tool_state_writes_undeclared``: a ``mode: mock`` tool whose real body (resolved from
    ``descriptor.original_module`` / ``original_function``, one helper hop deep) writes agent state
    by its framework's ``state_write`` rule, while its entry declares no ``state_updates:`` — the
    lift never happened, so nothing downstream can check the wrapper. A body that was read under a
    framework with no rule is ``mock_state_updates_unknowable``, naming the framework.

    ``state_update_gate_unapplied``: a declared key whose every reader in the agent's package
    lies outside what the harness imports, and which the harness never reads itself — the
    flag lands, and nothing the harness runs acts on it (a router above the driven sub-graph).

    Both WARN: a source that cannot be resolved stays silent, and neither can prove the
    success path from the error path, so the finding names the keys and readers for a human.
    """
    tools = _harness_tools(workspace)
    if isinstance(tools, str):
        return []
    harness_trees = [t for t in (_safe_parse(src) for src in harness_sources) if t is not None]
    declared_row = _declared_row(declared_framework)
    harness_framework = declared_row or next((fw for fw in (_detect_framework(t, declared_framework) for t in harness_trees) if fw is not None), None)
    findings: list[Finding] = []
    closures: dict[Path, set[str]] = {}
    unchecked: dict[str, list[str]] = {}  # framework as named -> mocked tools whose writes no rule reads
    for name, entry in tools.items():
        descriptor = entry.get('descriptor') if isinstance(entry, dict) else None
        if not isinstance(descriptor, dict) or entry.get('mode') != 'mock' or not isinstance(descriptor.get('original_module'), str):
            continue
        module = descriptor['original_module']
        root = _source_root(workspace, module)
        if root is None:
            continue
        declared = entry.get('state_updates')
        function = str(descriptor.get('original_function') or name)
        if declared is None:
            writes = _real_state_writes(root, module, function, harness_framework, declared_row)
            if writes is not None and writes.keys is None:
                unchecked.setdefault(_framework_named(writes.framework, declared_framework), []).append(name)
            elif writes is not None and writes.keys:
                via = f' (via {", ".join(writes.via)})' if writes.via else ''
                spelling = writes.framework.state_write.spelling if writes.framework and writes.framework.state_write else 'a state write'
                findings.append(Finding(
                    severity='warn',
                    code=Code.MOCK_TOOL_STATE_WRITES_UNDECLARED,
                    message=(
                        f'tools.{name} is mocked and its real body {module}.{function} '
                        f'writes agent state ({spelling}) to {writes.keys}{via}, but plurai.config.yaml declares no '
                        f'`state_updates:` for it, so the mock returns its message only and those writes never land. '
                        'Declare each success-path key ({value} / {from_result}) or `{omit: <reason>}`.'
                    ),
                    skill_step='Step 8',
                ))
            continue
        if not isinstance(declared, dict):
            continue
        package = root / module.split('.')[0]
        if root not in closures:
            closures[root] = _import_closure(root, harness_trees)
        tool_path = _module_file(root, module)
        tool_tree = _parse_file(tool_path) if tool_path else None
        tool_framework = declared_row or (_detect_framework(tool_tree) if tool_tree else None) or harness_framework
        rule = tool_framework.state_write if tool_framework is not None else None
        reads = rule.reads if rule is not None else _state_reads
        for key, spec in declared.items():
            if not _lands(spec):
                continue
            readers: list[tuple[str, str, int]] = []
            for path in sorted(package.rglob('*.py')):
                if '__pycache__' in path.parts:
                    continue
                tree = _parse_file(path)
                lines = reads(tree, str(key)) if tree else []
                dotted = '.'.join(path.relative_to(root).with_suffix('').parts).removesuffix('.__init__')
                readers += [(dotted, str(path.relative_to(root)), line) for line in lines]
            if not readers or any(dotted in closures[root] for dotted, _, _ in readers):
                continue
            if any(reads(tree, str(key)) for tree in harness_trees):
                continue
            where = ', '.join(f'{rel}:{line}' for _, rel, line in readers)
            findings.append(Finding(
                severity='warn',
                code=Code.STATE_UPDATE_GATE_UNAPPLIED,
                message=(
                    f'tools.{name}.state_updates lands {key!r}, but every agent reader of it ({where}) is outside '
                    'what the harness imports, and the harness never reads it itself — so the flag lands and '
                    'nothing that runs acts on it. Drive the agent from its real entry so that reader runs; when '
                    'the harness must drive the part below it, call the reader\'s own function for the gate '
                    'decision and its reply, and raise `plurai.HarnessFault` naming the turn when that function '
                    'cannot be called. Never write the refusal text in the harness.'
                ),
                skill_step='Step 8',
            ))
    if unchecked:
        # ONE finding per harness: the gap is the framework's, not each tool's.
        tools = [tool for names in unchecked.values() for tool in names]
        shown = ', '.join(tools[:8]) + (f' (+{len(tools) - 8} more)' if len(tools) > 8 else '')
        findings.append(Finding(
            severity='warn',
            code=Code.MOCK_STATE_UPDATES_UNKNOWABLE,
            message=(
                f'{len(tools)} mocked tool(s) declare no `state_updates:` ({shown}); their real bodies were read, but no '
                f'state-write rule is written down for {" / ".join(unchecked)}, so whether the mocks drop state the real '
                'tools write was NOT checked. Declare each success-path key a real tool writes to agent state under '
                '`state_updates:` ({value} / {from_result}), or `state_updates: {}` when it writes none.'
            ),
            skill_step='Step 8',
        ))
    return findings


# ── start modes: a declared flag that starts past the agent's own code ─────────────────────────────────

#: Names a state read hangs off: ``state.get('k')``, ``runtime.state['k']``, ``ctx.context.get('k')``.
_STATE_HOLDERS = frozenset({'state', 'agent_state', 'session_state', 'context', 'ctx', 'deps'})
#: Directories never part of the agent's own code for this check.
_NOT_AGENT_DIRS = frozenset({'__pycache__', '.venv', 'venv', 'site-packages', 'node_modules', '.git', '.tox', 'tests', 'test'})
#: A start flag is an ``agent_context`` field of one of these declared types.
_FLAG_TYPES = frozenset({'bool', 'boolean'})


def _is_state_holder(node: ast.AST) -> bool:
    """``state`` / ``ctx`` (a Name) or ``runtime.state`` / ``ctx.context`` (an attribute) — a holder of agent state."""
    return (isinstance(node, ast.Name) and node.id in _STATE_HOLDERS) or (isinstance(node, ast.Attribute) and node.attr in _STATE_HOLDERS)


def _state_key_read(node: ast.AST) -> str | None:
    """The constant key of ``<holder>.get('k', …)`` or ``<holder>['k']`` on a state holder, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
        first = node.args[0]
        if _is_state_holder(node.func.value) and isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(node, ast.Subscript) and _is_state_holder(node.value) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


def _refused_without(tree: ast.AST) -> dict[str, set[int]]:
    """Keys the agent REFUSES to run without: ``if not <read of k>: raise …`` (or ``… is None``), where the read is
    the state read itself or a local name bound from it in the same function. Line = the ``if``."""
    out: dict[str, set[int]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {
            n.targets[0].id: key for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and (key := _state_key_read(n.value)) is not None
        }
        for n in ast.walk(fn):
            if not isinstance(n, ast.If) or not any(isinstance(s, ast.Raise) for s in n.body):
                continue
            test = n.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                subject = test.operand
            elif isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Is) \
                    and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None:
                subject = test.left
            else:
                continue
            key = bound.get(subject.id) if isinstance(subject, ast.Name) else _state_key_read(subject)
            if key is not None:
                out.setdefault(key, set()).add(n.lineno)
    return out


def _key_writes(tree: ast.AST) -> dict[str, list[tuple[int, bool]]]:
    """Each constant key written as a dict-literal key or a subscript store: ``key -> [(line, value is a truthy constant)]``."""
    out: dict[str, list[tuple[int, bool]]] = {}

    def truthy(value: ast.AST | None) -> bool:
        return isinstance(value, ast.Constant) and bool(value.value) and value.value is not Ellipsis

    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.setdefault(k.value, []).append((k.lineno, truthy(v)))
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                    out.setdefault(t.slice.value, []).append((t.lineno, truthy(n.value)))
    return out


def _collection_strings(node: ast.AST) -> set[str] | None:
    """The string constants of a tuple / list / set literal, or of a dict literal's keys; None for anything else."""
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return None


def _names(node: ast.AST) -> set[str]:
    """Every bare name and attribute name ``node`` mentions."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _imported_from_outside_stdlib(trees: list[ast.Module]) -> set[str]:
    """The names the harness binds by importing from a module outside the standard library (the agent's own, a client)."""
    stdlib = getattr(sys, 'stdlib_module_names', frozenset())
    names: set[str] = set()
    for tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {(a.asname or a.name).split('.')[0] for a in n.names if a.name.split('.')[0] not in stdlib}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and n.module.split('.')[0] not in stdlib:
                names |= {a.asname or a.name for a in n.names}
    return names


def _feeds_imported_result(body: ast.Module, imported: set[str]) -> bool:
    """True when ``body`` passes an imported callee's result into the state: ``x.update(f(...))``, ``x |= f(...)`` or
    ``return f(...)`` (awaited or not), where ``f`` is imported or an attribute of an imported module."""

    def imported_call(node: ast.AST | None) -> bool:
        if isinstance(node, ast.Await):
            node = node.value
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        while isinstance(func, ast.Attribute):
            func = func.value
        return isinstance(func, ast.Name) and func.id in imported

    for n in ast.walk(body):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'update' and any(imported_call(a) for a in n.args):
            return True
        if isinstance(n, ast.AugAssign) and isinstance(n.op, ast.BitOr) and imported_call(n.value):
            return True
        if isinstance(n, ast.Return) and imported_call(n.value):
            return True
    return False


def _harness_seeds(trees: list[ast.Module], flag: str, declared: frozenset[str]) -> set[str] | None:
    """The keys the harness writes into the agent's state on a start where ``flag`` is set, as far as its source says.

    A key counts when it is (a) in the same literal collection as the flag (a tuple of keys copied from the
    context), (b) written or listed inside an ``if`` that tests the flag, or inside a harness function called
    there, or (c) written inside ``seed_state`` (or a harness function it calls) or a function that names the flag
    or a constant holding it. A harness that never names the flag passes the context through: the declared fields
    and every key its functions write are what it seeds. None when the flag's branch feeds the result of an imported
    callee (the agent's own producer) into the state: what that writes is not visible here, so it counts as seeded.
    """
    defs = {n.name: n for t in trees for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    imported = _imported_from_outside_stdlib(trees) - set(defs)
    seeded: set[str] = set()
    holders: set[str] = set()  # module constants whose literal holds the flag
    named = False

    def written(node: ast.AST) -> set[str]:
        keys = set(_key_writes(node))
        for n in ast.walk(node):
            strings = _collection_strings(n)
            if strings is not None and not isinstance(n, ast.Dict):
                keys |= strings
        return keys

    for tree in trees:
        for n in ast.walk(tree):
            strings = _collection_strings(n)
            if strings and flag in strings:
                seeded |= strings
            if isinstance(n, ast.Constant) and n.value == flag or isinstance(n, ast.Name) and n.id == flag or isinstance(n, ast.Attribute) and n.attr == flag:
                named = True
        for stmt in tree.body:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and (strings := _collection_strings(stmt.value)) and flag in strings:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                holders |= {t.id for t in targets if isinstance(t, ast.Name)}
    if not named:
        return set(declared) | {k for fn in defs.values() for k in _key_writes(fn)}
    for tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, ast.If) and (flag in _names(n.test) or any(isinstance(c, ast.Constant) and c.value == flag for c in ast.walk(n.test))):
                body = ast.Module(body=n.body, type_ignores=[])
                if _feeds_imported_result(body, imported):
                    return None
                seeded |= written(body)
                for call in ast.walk(body):
                    callee = call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else None
                    if callee in defs:
                        seeded |= written(defs[callee])
    for name, fn in defs.items():
        mentions = _names(fn) & (holders | {flag}) or any(isinstance(c, ast.Constant) and c.value == flag for c in ast.walk(fn))
        if name == 'seed_state' or mentions:
            seeded |= set(_key_writes(fn))
        if name == 'seed_state':
            for call in ast.walk(fn):
                callee = call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else None
                if callee in defs and callee != name:
                    seeded |= set(_key_writes(defs[callee]))
    return seeded


def _agent_source_root(workspace: Path, dotted: str) -> Path | None:
    """:func:`_source_root`, else the shallowest directory within three levels below the harness's parent from which
    ``dotted`` resolves — the checkout a harness puts on ``sys.path`` itself (``<repo>/<app>/agent``)."""
    found = _source_root(workspace, dotted)
    if found is not None:
        return found
    base = workspace.resolve().parent
    level = [base]
    for _ in range(3):
        children: list[Path] = []
        for d in level:
            try:
                children.extend(
                    child for child in (d.iterdir() if d.is_dir() else ())
                    if child.is_dir() and not child.name.startswith('.') and child.name not in _NOT_AGENT_DIRS
                )
            except OSError:  # an unreadable directory is skipped, never the end of the verify
                continue
        level = sorted(children)
        hit = next((d for d in level if _module_file(d, dotted)), None)
        if hit is not None:
            return hit
    return None


def start_mode_state_findings(workspace: Path, harness_sources: list[str]) -> list[Finding]:
    """WARN ``start_mode_state_unseeded``: a declared boolean ``agent_context`` flag starts a conversation past the
    agent's own code that sets it, that code also writes a state key the agent refuses to run without, and the harness
    does not seed that key on that start.

    Typed facts only: the flag is declared in ``plurai.config.yaml``; the agent's package (``agent.module``) writes it
    with a truthy constant; a file that does so also writes the key; a guard elsewhere raises without it. Silent when
    any of those cannot be read (no ``agent.module``, a bridge or skill bench, an unreadable config).
    """
    document = _harness_config(workspace)
    if not isinstance(document, dict):
        return []
    context = document.get('agent_context')
    fields = context.get('fields') if isinstance(context, dict) else None
    if not isinstance(fields, dict):
        return []
    flags = sorted(str(name) for name, spec in fields.items() if isinstance(spec, dict) and str(spec.get('type', '')).lower() in _FLAG_TYPES)
    agent = document.get('agent')
    module = agent.get('module') if isinstance(agent, dict) else None
    if not flags or not isinstance(module, str) or not module.strip():
        return []
    root = _agent_source_root(workspace, module)
    if root is None:
        return []
    top = root / module.split('.')[0]
    harness_dir = workspace.resolve()
    files = sorted(top.rglob('*.py')) if top.is_dir() else [p for p in (_module_file(root, module.split('.')[0]),) if p]
    writes: dict[str, list[tuple[str, int, bool]]] = {}
    refused: dict[str, list[tuple[str, int]]] = {}
    for path in files:
        rel_parts = path.relative_to(root).parts
        if any(part in _NOT_AGENT_DIRS for part in rel_parts) or path.name.startswith('test_') or harness_dir in path.resolve().parents:
            continue
        tree = _parse_file(path)
        if tree is None:
            continue
        rel = '/'.join(rel_parts)
        for key, sites in _key_writes(tree).items():
            writes.setdefault(key, []).extend((rel, line, hot) for line, hot in sites)
        for key, lines in _refused_without(tree).items():
            refused.setdefault(key, []).extend((rel, line) for line in sorted(lines))
    trees = [t for t in (_safe_parse(src) for src in harness_sources) if t is not None]
    findings: list[Finding] = []
    for flag in flags:
        setters = [(f, line) for f, line, hot in writes.get(flag, []) if hot]
        if not setters:
            continue
        setter_files = {f for f, _ in setters}
        seeded = _harness_seeds(trees, flag, frozenset(str(n) for n in fields))
        if seeded is None:
            continue
        for key in sorted(refused):
            if key == flag or key in seeded:
                continue
            produced = [(f, line) for f, line, _ in writes.get(key, []) if f in setter_files]
            if not produced:
                continue

            def at(sites: list[tuple[str, int]]) -> str:
                shown = sorted(set(sites))
                return ', '.join(f'{f}:{line}' for f, line in shown[:3]) + (f' (+{len(shown) - 3} more)' if len(shown) > 3 else '')

            findings.append(Finding(
                severity='warn',
                code=Code.START_MODE_STATE_UNSEEDED,
                message=(
                    f'agent_context.{flag} starts a conversation past the agent\'s own code that sets it ({at(setters)}), and '
                    f'that code also writes {key!r} ({at(produced)}), which the agent refuses to run without '
                    f'({at(refused[key])}). The harness does not seed {key!r} on that start, so every conversation that '
                    f'starts with {flag} set fails at that read. Seed it before the first turn on that start by calling the '
                    'agent\'s own function that produces it (its resolver or loader) — never a value of the harness\'s own. '
                    f'When the harness already seeds {key!r} on that start by another route, this warning is wrong: say so '
                    'and change nothing.'
                ),
                skill_step='Step 9e',
            ))
    return findings


def _safe_parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _declared_mock_state_updates(workspace: Path) -> dict[str, frozenset[str]] | str:
    """Each ``mode: mock`` tool's declared ``state_updates:`` keys the wrapper must land.

    Read off the harness directory's ``plurai.config.yaml`` (the repo root's when the harness
    has none). ``omit:`` entries are the declared, reasoned drops and are excluded. A malformed
    entry is ``verify_config.py``'s finding, so it is skipped here; a config that exists and
    cannot be read is returned as the reason string.
    """
    tools = _harness_tools(workspace)
    if isinstance(tools, str):
        return tools
    declared: dict[str, frozenset[str]] = {}
    for name, entry in tools.items():
        if not isinstance(entry, dict) or entry.get('mode') != 'mock' or not isinstance(entry.get('state_updates'), dict):
            continue
        keys = frozenset(
            key for key, spec in entry['state_updates'].items()
            if _lands(spec)
        )
        if keys:
            declared[str(name)] = keys
    return declared


def _declared_record_fields(workspace: Path) -> tuple[frozenset[str], ...] | str:
    """The state schema's declared record COLLECTIONS — one field-name set each — or a
    STRING saying why they could not be read.

    Kept as separate sets rather than one union because the schema declares several
    lists-of-records and only some are what a storage class projects; the arm decides which by
    what the class reads, not by a name. (A union reported an unrelated vocabulary key
    alongside the real drop on the first run of this arm.)

    THE NEGATIVE STATES ARE NAMED, not collapsed to None, because the caller renders them on an
    operator's screen and a state that renders as a green tick certifies a bench nobody checked.
    Three states, because three remedies with three owners: no schema (author it — see below), no
    pyyaml in THIS interpreter (install it, or
    invoke through ``$PLURAI_PY``), and a schema present but unusable — corrupt YAML or no declared
    record collection — which is one state carrying its cause.

    The import is local and guarded because the file runs under the agent's own interpreter
    (``$PLURAI_PY``, SKILL.md Step 9) — a module-scope import would take every other check down
    with it on an interpreter that happens to lack pyyaml.
    """
    # THE FILE THE LANE ACTUALLY WRITES. This read used to name
    # ``artifacts/scenario_state_schema.scan-authored.yaml``, which NOTHING in the plugin
    # authors — so `_declared_record_fields` always returned the "no artifact" string, the
    # positive arm could never fire on any real bench, and the remedy printed beside it named a
    # step that does not exist. `/generate-mock-env` writes the env-gen branch's copy and
    # `/extract-scenario-variables` Step 4b the env-gen-skipped one; both land here.
    schema = workspace / 'scenario_state_schema.yaml'
    if not schema.is_file():
        return f'the workspace ships no {schema.name} — build the mock env with generate-mock-env (or run Step 4b of extract-scenario-variables on the env-gen-skipped branch) to author it'
    try:
        import yaml  # noqa: PLC0415 - see the docstring: a module-scope import would be fatal here
    except ImportError:
        return f'pyyaml is not importable from {sys.executable}; install it into that interpreter ({sys.executable} -m pip install pyyaml)'
    try:
        spec = yaml.safe_load(schema.read_text(encoding='utf-8'))
        groups = [
            frozenset(field['item_specs'])
            for field in (spec['agent_context_fields'] or {}).values()
            if isinstance(field, dict) and isinstance(field.get('item_specs'), dict) and field['item_specs']
        ]
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as exc:
        # ``yaml.YAMLError`` descends from ``Exception``, NOT from ``ValueError``, so an earlier
        # version of this tuple let a genuinely corrupt schema — an unterminated flow mapping, a tab
        # indent — raise ParserError/ScannerError straight out of ``main()`` as a traceback. The
        # state was named and unreachable: the only input that took this branch was YAML that
        # PARSED and then failed the walk. ONE state for "present and unusable", because both halves
        # send the reader to the same file, with the cause carried in the message.
        return f'{schema.name} is not usable as authored: {type(exc).__name__}: {exc}'
    return tuple(groups) or f'{schema.name} is not usable as authored: it declares no list-of-records field (no item_specs)'


#: The SDK's accessors for the mock's URL: a value built from them (and literals) pins a variable to the mock.
_MOCK_URL_ACCESSORS = frozenset({'mock_service_url', 'nested_mock_url'})


def _pin_value_ok(node: ast.AST, confirmed: frozenset[str]) -> bool:
    """A pin is literal: the mock's URL (an SDK accessor, alone or in a literal f-string or `+`), a `*.invalid` host, or a
    confirmed store's target. Anything read or computed at run time is no pin."""
    def parts(n: ast.AST) -> list[ast.AST] | None:
        if isinstance(n, ast.JoinedStr):
            return [v.value if isinstance(v, ast.FormattedValue) else v for v in n.values]
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            left, right = parts(n.left), parts(n.right)
            return None if left is None or right is None else left + right
        return [n]
    pieces = parts(node) or []
    accessor = [p for p in pieces if isinstance(p, ast.Call) and _dotted(p.func).rsplit('.', 1)[-1] in _MOCK_URL_ACCESSORS]
    literal = ''.join(p.value for p in pieces if isinstance(p, ast.Constant) and isinstance(p.value, str))
    if len(accessor) + sum(isinstance(p, ast.Constant) and isinstance(p.value, str) for p in pieces) != len(pieces):
        return False
    return bool(accessor) or '.invalid' in literal.casefold() or ' '.join(literal.split()).casefold() in confirmed


def _env_assignments(tree: ast.Module) -> dict[str, list[tuple[str, ast.AST, int]]]:
    """Every `(how, value, line)` the harness gives an environment variable: `os.environ[V] = x`, `os.environ.update(...)`,
    `os.environ.setdefault(V, x)`, or an `env=` mapping it launches a process with."""
    found: dict[str, list[tuple[str, ast.AST, int]]] = {}

    def add(key: ast.AST | None, value: ast.AST, how: str, line: int) -> None:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            found.setdefault(key.value, []).append((how, value, line))

    def mapping(node: ast.AST, how: str) -> None:
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                add(key, value, how, node.lineno)
        elif isinstance(node, ast.Call) and _dotted(node.func) == 'dict':
            for kw in node.keywords:
                add(ast.Constant(kw.arg), kw.value, how, node.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _dotted(target.value).endswith('environ'):
                    add(target.slice, node.value, 'assign', node.lineno)
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.endswith('environ.update'):
                for arg in node.args:
                    mapping(arg, 'assign')
                for kw in node.keywords:
                    add(ast.Constant(kw.arg), kw.value, 'assign', node.lineno)
            elif name.endswith('environ.setdefault') and len(node.args) == 2:
                add(node.args[0], node.args[1], 'setdefault', node.lineno)
            for kw in node.keywords:
                if kw.arg == 'env':
                    mapping(kw.value, 'assign')
    return found


_SPAWNERS = frozenset({'subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
                       'asyncio.create_subprocess_exec', 'asyncio.create_subprocess_shell'})


def _spawns_with_environ(tree: ast.Module) -> bool:
    """The harness starts processes, and every one inherits its pinned `os.environ`: no `env=`, or an `env=` built from
    `os.environ` (directly, or through a name assigned from it). One spawn handed a fresh environment strips the pins."""
    def from_environ(node: ast.AST, derived: set[str]) -> bool:
        """`node` is the environment mapping itself: `os.environ`, a copy of it (`dict(...)`, `.copy()`), or `{**it, ...}`."""
        if isinstance(node, ast.Attribute) and node.attr == 'environ' or isinstance(node, ast.Name) and node.id in derived:
            return True
        if isinstance(node, ast.Dict):
            return any(k is None and from_environ(v, derived) for k, v in zip(node.keys, node.values))
        if isinstance(node, ast.Call) and node.args and _dotted(node.func) == 'dict':
            return from_environ(node.args[0], derived)
        return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'copy' and from_environ(node.func.value, derived)
    derived: set[str] = set()
    for _ in range(3):  # names built from names built from os.environ
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and from_environ(node.value, derived):
                derived |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    envs = [next((kw.value for kw in node.keywords if kw.arg == 'env'), None)
            for node in ast.walk(tree) if isinstance(node, ast.Call) and _dotted(node.func) in _SPAWNERS]
    return bool(envs) and all(env is None or from_environ(env, derived) for env in envs)


def _module_scope(tree: ast.Module, line: int) -> bool:
    """`line` runs at import: it sits in no function or class body."""
    return not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                   and n.lineno <= line <= (n.end_lineno or n.lineno) for n in ast.walk(tree))


def _fake_unrouted(tree: ast.Module, fake: dict[str, Any], served: list[int], given: list[tuple[str, ast.AST, int]]) -> str | None:
    """Why the harness does not serve this `agent_fake` double as the conversation's own vendor (None when it does): it must
    assign the variable a literal `*.invalid` host at module scope (an agent that reads the endpoint at import fails closed),
    then `_serve_agent_fake("<target>", initial_world_state)` inside `seed_state` (the world is known, so the double's world
    inputs are the scenario's), and its `_serve_agent_fake` must be the plugin's reference, verbatim."""
    import agent_fakes  # noqa: PLC0415

    call = f'`{agent_fakes.SERVE}({fake["target"]!r}, initial_world_state)`'
    if not served:
        return f'the harness never assigns it {call}, so the vendor\'s calls reach no simulation'
    if not any(how == 'assign' and isinstance(value, ast.Constant) and isinstance(value.value, str)
               and '.invalid' in value.value.casefold() and _module_scope(tree, line) for how, value, line in given):
        return ('the harness does not first pin it to a literal `*.invalid` host at module scope, so an agent that reads the '
                'endpoint at import keeps whatever the shell holds')
    if not all(agent_fakes.in_seed_state(tree, line) for line in served):
        return f'{call} runs outside `seed_state`, before the scenario\'s world exists'
    if not agent_fakes.helper_is_reference(tree):
        return (f'the harness\'s `{agent_fakes.SERVE}` is not the plugin\'s reference '
                '(`simulation-setup-python/references/agent_fake_serve.py`): copy it verbatim')
    return None


def store_endpoint_findings(source: str, workspace: Path) -> list[Finding]:
    """A simulated store fails closed only if its endpoint is pinned. Each `egress.production_hosts` `{env}` row
    must be ASSIGNED by the harness to a literal: the mock's URL or a `*.invalid` host, or a confirmed store's target
    (`endpoint_env_unpinned`; a `setdefault`, an inherited or a computed value is no pin); the corpus index the map's
    `grounding.endpoint_env` names is not pinned. And an agent the harness does
    not launch (the map's `entry_symbol.executes: external`, unless a bridge child or typed `launched_by: harness` and
    spawned with the harness's environment) cannot be pinned at all while a store is simulated
    (`agent_env_unreachable`). Both hard."""
    config = _harness_config(workspace)
    tree = _safe_parse(source)
    if not isinstance(config, dict) or tree is None:
        return []
    import writer_readers  # noqa: PLC0415 - the ONE reading of what a confirmation covers (beside gate_ledger, on the path above)

    confirmed = frozenset(t for t, answer in writer_readers.confirmed_targets(config).items() if answer in writer_readers.STORE_ANSWERS)
    topology = _read_topology(workspace)
    corpus = writer_readers.corpus_tools(workspace, topology)
    egress = config.get('egress') if isinstance(config.get('egress'), dict) else {}
    rows = egress.get('production_hosts') if isinstance(egress.get('production_hosts'), list) else []
    pins = _env_assignments(tree)
    findings = []
    import agent_fakes  # noqa: PLC0415 - the one reading of a vendor double the agent ships (beside writer_readers)

    fakes = agent_fakes.fakes(config)
    # The corpus index the retrieval tools read (the map's `grounding.endpoint_env`) stays on the corpus route: no pin.
    for var in sorted({str(r['env']) for r in rows if isinstance(r, dict) and r.get('env')} - writer_readers.corpus_endpoint_env(topology, corpus)):
        given = pins.get(var, [])
        fake = fakes.get(var)
        served = [line for how, value, line in given if fake and how == 'assign' and agent_fakes.serves(value, fake)]
        bad = [line for how, value, line in given if line not in served and (how != 'assign' or not _pin_value_ok(value, confirmed))]
        if fake and not bad and (why := _fake_unrouted(tree, fake, served, given)):
            findings.append(Finding(severity='fail', code=Code.AGENT_FAKE_UNROUTED, skill_step='Step 6', line=served[0] if served else None,
                                    message=f'`{var}` is declared served by the agent\'s own double `{fake["target"]}`, but {why}.'))
            continue
        if given and not bad:
            continue
        findings.append(Finding(severity='fail', code=Code.ENDPOINT_ENV_UNPINNED, skill_step='Step 6', line=bad[0] if bad else None, message=(
            f'`{var}` (an `egress` endpoint row) is ' + ('never assigned, so the agent inherits whatever the shell holds' if not given else
            'not pinned to a literal (a `setdefault` keeps an inherited value; a computed one is decided at run time)')
            + '. Assign it in the harness: `os.environ["' + var + '"] = <plurai.mock_service_url() or an `http://<name>.invalid` '
            'host>` for a simulated store or vendor, or the confirmed `target` for a store `world_state.confirmation` names.')))
    import harness_fingerprint  # noqa: PLC0415 - the plugin's one reading of which harness template stands here

    agent = (topology or {}).get('agent')
    entry = agent.get('entry_symbol') if isinstance(agent, dict) else None
    # An `external` entry the harness itself starts is reached by its pins: a bridge bench's child, or one the map types
    # `launched_by: harness` and the harness spawns with its environment (:func:`_spawns_with_environ`).
    launched = isinstance(entry, dict) and entry.get('launched_by') == 'harness' and _spawns_with_environ(tree)
    # A simulated copy the map declares at `agent_host` (with its `route_smoke`) is rewired where it runs: no pin needs to reach it.
    from host_effects import intercepted_at_agent_host  # noqa: PLC0415 - the plugin's one reader of the declaration

    if (isinstance(entry, dict) and entry.get('executes') == 'external' and harness_fingerprint.detect_lang(workspace) != 'bridge'
            and not launched and not intercepted_at_agent_host(topology) and (simulated := writer_readers.unconfirmed_store_tools(config, corpus))):
        findings.append(Finding(severity='fail', code=Code.AGENT_ENV_UNREACHABLE, skill_step='Step 6', message=(
            f'the agent runs in a process this harness does not launch (`entry_symbol.executes: external`), so no pin reaches '
            f'it, while {len(simulated)} tool(s) reach a simulated store ({", ".join(simulated[:6])}{", …" if len(simulated) > 6 else ""}). '
            'Run the agent under the harness (a launched child it passes `env=` to), or, when the harness cannot launch it, '
            'deploy a simulated copy of it at `agent_host` (its actions rewired to the mock, declared on the map as '
            '`agent.tool_interception` with its `why` and the `route_smoke` that proves it). Never point the run at the running '
            'instance: a store runs live only on the user\'s recorded Yes at that store\'s own question.')))
    return findings


#: Step 8's failure form for a `@plurai.mock` body, quoted by both MockToolError findings.
_RETURNED_FAILURE = "return {'status': 'error', 'error': '<the real tool's message>'}"


def _installed_mock_tool_error() -> tuple[inspect.Signature | None, str | None]:
    """``(signature, note)``: ``plurai.MockToolError``'s signature read from the SDK this interpreter imports, never a
    constant, so the check follows the SDK. ``None`` and a one-line note when there is no SDK or no such class."""
    try:
        import plurai  # noqa: PLC0415 - only this check reads the SDK, and only when a harness calls MockToolError
    except Exception as exc:  # noqa: BLE001 - an SDK that does not import is a skipped check, never a crash
        return None, f'MockToolError calls were not checked: the plurai SDK does not import from {sys.executable} ({type(exc).__name__})'
    cls = getattr(plurai, 'MockToolError', None)
    if cls is None:
        return None, f'MockToolError calls were not checked: the installed plurai SDK ({getattr(plurai, "__version__", "?")}) has no MockToolError'
    try:
        return inspect.signature(cls), None
    except (TypeError, ValueError) as exc:
        return None, f'MockToolError calls were not checked: its signature cannot be read ({exc})'


def _mock_tool_error_refs(tree: ast.Module) -> Callable[[ast.AST], bool]:
    """Whether a node names the SDK's ``MockToolError``: through ``import plurai[.x] [as m]`` or
    ``from plurai[.x] import MockToolError [as n]``."""
    modules, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == 'plurai' or a.name.startswith('plurai.')}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or '').split('.')[0] == 'plurai':
            names |= {a.asname or a.name for a in node.names if a.name == 'MockToolError'}

    def is_ref(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == 'MockToolError':
            return ast.unparse(node.value) in modules
        return isinstance(node, ast.Name) and node.id in names

    return is_ref


def mock_tool_error_findings(files: list[tuple[str | None, str]]) -> tuple[list[Finding], str | None]:
    """``MOCK_TOOL_ERROR_RAISED_IN_MOCK`` for a ``@plurai.mock`` body that raises a ``MockToolError`` (directly, through
    a module-level helper that builds one, or by calling a helper that raises one), where the engine reads any raise as
    a handler crash; and ``MOCK_TOOL_ERROR_ARITY`` for any other ``MockToolError(...)`` call the installed SDK's
    signature refuses (a helper that returns one included). Both warn. ``files`` is ``(filename, source)`` per harness
    file (``None`` = simulate.py), so a split ``_mocks.py`` is read too. Returns ``(findings, note)``; the note says the
    arity half did not run, and the SDK is read only when some file calls ``MockToolError``."""
    parsed = [(filename, tree) for filename, source in files if (tree := _safe_parse(source)) is not None]
    refs = {id(tree): _mock_tool_error_refs(tree) for _, tree in parsed}
    calls = [(filename, tree, node) for filename, tree in parsed for node in ast.walk(tree)
             if isinstance(node, ast.Call) and refs[id(tree)](node.func)]
    signature, note = _installed_mock_tool_error() if calls else (None, None)
    findings: list[Finding] = []
    in_mock: set[int] = set()
    for filename, tree in parsed:
        is_ref = refs[id(tree)]
        top = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        builders = {f.name for f in top if any(isinstance(n, ast.Call) and is_ref(n.func) for n in ast.walk(f))}

        def raised(node: ast.AST, is_ref: Callable[[ast.AST], bool] = is_ref, builders: set[str] = builders) -> bool:
            """A raise of the SDK's MockToolError, built here or by a helper that builds one."""
            target = node.func if isinstance(node, ast.Call) else node
            return is_ref(target) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in builders)

        raisers = {f.name for f in top if any(isinstance(n, ast.Raise) and n.exc is not None and raised(n.exc) for n in ast.walk(f))}
        for handler in ast.walk(tree):
            if not _is_mock_handler(handler):
                continue
            name = getattr(handler, 'name', '?')
            for node in ast.walk(handler):
                if isinstance(node, ast.Raise) and node.exc is not None and raised(node.exc):
                    in_mock.add(id(node.exc))
                    how = 'raises `MockToolError`'
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in raisers - {name}:
                    how = f'calls `{node.func.id}`, which raises `MockToolError`,'
                else:
                    continue
                findings.append(Finding(
                    severity='warn', code=Code.MOCK_TOOL_ERROR_RAISED_IN_MOCK, skill_step='Step 8', line=node.lineno, filename=filename,
                    message=f'the `@plurai.mock` handler `{name}` {how} and the engine reads any raise from a mock body as a handler '
                    f'crash, so the agent gets `handler raised: …` instead of the tool\'s message. Return it: `{_RETURNED_FAILURE}`.'))
    if signature is not None:
        shown = signature.replace(parameters=[p.replace(annotation=inspect.Parameter.empty) for p in signature.parameters.values()],
                                  return_annotation=inspect.Signature.empty)
        for filename, _tree, call in calls:
            if id(call) in in_mock or any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
                continue  # raised in a mock body (the finding above), or a spread whose arity is decided at run time
            try:
                signature.bind(*call.args, **{k.arg: k.value for k in call.keywords if k.arg})
            except TypeError as exc:
                findings.append(Finding(
                    severity='warn', code=Code.MOCK_TOOL_ERROR_ARITY, skill_step='Step 8', line=call.lineno, filename=filename,
                    message=f'`{ast.unparse(call)[:120]}` does not fit the installed SDK\'s `MockToolError{shown}` ({exc}), so it raises '
                    f'TypeError instead of the failure. From a `@plurai.mock` body, return the failure: `{_RETURNED_FAILURE}`; anywhere '
                    'else, pass every argument the signature requires.'))
    return findings, note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Verify a generated simulate.py against the SKILL.md contract.',
    )
    parser.add_argument('path', type=Path, help='Path to simulate.py')
    parser.add_argument(
        '--state-class',
        type=str,
        default=None,
        help=(
            'Optional dotted path to the agent\'s state class (e.g. '
            '"our_backend.agents.customer.state.CustomerAgentState"). When '
            'provided, enables _build_state drift checks against the real '
            'class fields. Read this from plurai.config.yaml agent.state_class.'
        ),
    )
    parser.add_argument(
        '--tool-content-envelope',
        type=str,
        default=None,
        # No ``choices=``: an unknown kind is a finding about plurai.config.yaml, and
        # ``_envelope_findings`` renders it as a warn that names the field to fix. Constraining
        # it here would exit 2 at parse time instead, so the CLI and the check would disagree
        # about whether a bad kind is fatal.
        help=(
            "Optional agent.tool_content_envelope.kind from plurai.config.yaml. "
            "When the agent has a tool-return convention of its own ('json_field' / "
            "'text'), this enables the check that mock returns are rendered through "
            "it — without it a mocked tool hands the model a payload shape the real "
            "tools never produce. Omit (or 'json') for an agent with no convention."
        ),
    )
    parser.add_argument(
        '--findings-out',
        type=Path,
        default=None,
        help='Write every gate finding this run minted, as JSON, to this path (never printed on stdout).',
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f'verify_simulate: not a file: {args.path}', file=sys.stderr)
        return 2

    # Auto-discover _storage.py / _mocks.py helpers adjacent to simulate.py
    # (Step 8.4 split layout). Each file is parsed independently — bad
    # helpers produce a distinct warn finding (naming the offending file)
    # and are EXCLUDED from the merged source the AST checks see, so a
    # broken helper doesn't cascade into a wall of false-positive fails.
    #
    # The simulate.py-only source is preserved separately so the
    # invoke_agent / seed_state location check can require those defs in
    # simulate.py specifically (not in a helper, which would falsely pass
    # but break at runtime when the orchestrator looks up the symbols).
    simulate_only_source = args.path.read_text(encoding='utf-8')
    discovery_findings: list[Finding] = []
    helper_texts: list[str] = []
    helper_files: list[tuple[str | None, str]] = []
    for helper_name in ('_storage.py', '_mocks.py'):
        helper_path = args.path.parent / helper_name
        if not helper_path.is_file():
            continue
        try:
            helper_text = helper_path.read_text(encoding='utf-8')
        except OSError as exc:
            discovery_findings.append(Finding(
                severity='warn',
                code=Code.HELPER_UNREADABLE,
                message=(
                    f'could not read {helper_name}: {type(exc).__name__}: {exc}. '
                    f'AST-dependent checks against this file will be skipped.'
                ),
                skill_step='Step 8',
                filename=helper_name,
            ))
            continue
        try:
            ast.parse(helper_text, filename=helper_name)
        except SyntaxError as exc:
            discovery_findings.append(Finding(
                severity='warn',
                code=Code.HELPER_UNPARSEABLE,
                message=(
                    f'{helper_name} does not parse: {exc.msg}. AST-dependent '
                    f'checks against this file will be skipped — fix the '
                    f'syntax error first.'
                ),
                skill_step='Step 8',
                line=exc.lineno,
                filename=helper_name,
            ))
            continue
        helper_texts.append(helper_text)
        helper_files.append((helper_name, helper_text))
    source = '\n'.join([simulate_only_source, *helper_texts])
    mock_error_findings, mock_error_note = mock_tool_error_findings([(None, simulate_only_source), *helper_files])

    findings = (
        discovery_findings
        + verify(
            source,
            state_class_dotted=args.state_class,
            simulate_only_source=simulate_only_source,
            tool_content_envelope=args.tool_content_envelope,
            declared_record_fields=_declared_record_fields(args.path.parent),
            world_state_owner=_declared_world_owner(args.path.parent),
            live_writers=_declared_live_writers(args.path.parent),
            shared_world_ok=_declared_shared_world_ok(args.path.parent),
            mock_state_updates=_declared_mock_state_updates(args.path.parent),
            declared_framework=_declared_framework(args.path.parent),
            declared_native_tools=_declared_native_tools(args.path.parent),
            resume_declaration=_declared_resume(args.path.parent),
            declared_mocks=_declared_mocks(args.path.parent),
            world_access_exempt=_declared_world_overlays(args.path.parent),
        )
        + _mock_state_source_findings(args.path.parent, [simulate_only_source, *helper_texts], _declared_framework(args.path.parent))
        + start_mode_state_findings(args.path.parent, [simulate_only_source, *helper_texts])
        + subagent_inner_call_findings(_read_topology(args.path.parent), _harness_tools(args.path.parent))
        + document_read_off_payload_findings(source, _declared_document_fields(args.path.parent), shape=_declared_shape(args.path.parent))
        + store_endpoint_findings(source, args.path.parent)
        + env_file_findings(source, args.path.parent)
        + mock_error_findings
    )
    fails = [f for f in findings if f.severity == 'fail']
    warns = [f for f in findings if f.severity == 'warn']
    try:
        applied, lines = gate_findings(fails, args.path, findings_out=args.findings_out)
    except (OSError, ValueError) as exc:
        print(f'verify_simulate: the gate ledger could not be read or the findings written ({type(exc).__name__}: {exc})', file=sys.stderr)
        return 2

    if mock_error_note:
        print(f'verify_simulate: note: {mock_error_note}')
    if not findings:
        print(f'verify_simulate: ✓ {args.path} satisfies the SKILL.md contract.')
        return 0

    print(f'verify_simulate: {args.path}')
    for line in lines:
        print(f'  {line}')
    if fails:
        print(f'  {len(fails)} contract violation(s):')
        for f in fails:
            print(f.render())
    if warns:
        print(f'  {len(warns)} warning(s):')
        for f in warns:
            print(f.render())
    return 1 if applied.holding else 0


def gate_findings(fails: list[Finding], simulate_path: Path, *, findings_out: Path | None) -> tuple[gate_ledger.Applied, list[str]]:
    """Each `fail` as a gate finding, settled against the plugin's ledger beside this workdir.

    A `fail` is a gate: the catalogue in `scripts/gate_ledger.py` decides whether it is security, invariant
    or advisory, and an advisory one HOLDS the exit code until it is repaired or the plugin overrules it in
    `<workdir>/artifacts/.gate_overrules.jsonl`. A `warn` is no gate and never holds. The fingerprint covers
    the code, the file and the message; the line number is `observed`, so an overrule survives an edit
    elsewhere in the file and lapses the moment the finding's own text changes.

    A file that does not parse ran no other check, so only `syntax_error` is evaluated then: an overrule of
    any other code is neither applied nor reported stale by a run that never looked at it.
    """
    minted = [
        gate_ledger.make_finding(
            f'plugin.verify_simulate.{f.code}',
            subject=simulate_path.name,
            evidence={'code': str(f.code), 'file': f.filename or simulate_path.name, 'message': f.message},
            observed={'line': f.line},
            remedy=f'the [{f.code}] fix in SKILL.md {f.skill_step}',
            draft_ref=None,
        )
        for f in fails
    ]
    syntax = f'plugin.verify_simulate.{Code.SYNTAX_ERROR}'
    evaluated = (syntax,) if any(g.gate_id == syntax for g in minted) else ('plugin.verify_simulate.',)
    return gate_ledger.settle(minted, art=simulate_path.parent / 'artifacts', evaluated=evaluated, findings_out=findings_out)


if __name__ == '__main__':
    from _stdio import utf8_stdio  # noqa: PLC0415 - sibling script: UTF-8 standard streams on Windows

    utf8_stdio()
    sys.exit(main())
