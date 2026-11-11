"""Static audit: which agent_context / state keys does the production
agent's tool code READ as a precondition — AND which production types
does it ``isinstance``-check on runtime objects (storage adapters etc.)?

The /simulation-scan-agent skill's PRD authoring step extracts the public
tool surface (args / returns / business rules) from descriptors, but
production tool BODIES often gate on internal state reads OR runtime
type-dispatch:

    if not state.get("refinement_questions"):
        return {"status": "error", "content": "Refinement questions required in state"}

    if isinstance(storage, ExampleSetApiStorage):
        ...
    if isinstance(storage, CalibrationStorage):
        ...
    raise TypeError(f"Unsupported storage type: {type(storage).__name__}")

Both classes of read are invisible to descriptor-level analysis, so
they don't end up in the PRD, which means:
  - ``state.get`` reads with no default: tools error → React loop
    retries → recursion budget exhausts → GraphRecursionWrap.
  - ``isinstance`` checks on mocked replacements: the mock class
    doesn't subclass the production type → TypeError → broad
    ``except Exception`` swallows it → soft failure with empty
    return → silent observability hole. (Real case: a mock storage
    class that didn't subclass the production type was rejected by an
    ``isinstance`` gate; the agent kept running on empty data and
    logged a swallowed TypeError every turn.)

This audit walks every Python file in a customer's agent module
looking for both patterns. It surfaces them so the customer can
declare missing state keys in the PRD AND make sure their mocks
subclass the production types being type-checked.

Usage::

    python audit_state_preconditions.py <agent_module_root> [--prd <path>] [--schema <path>]
                                        [--exclude <glob> ...]

Reports:
  - Every distinct string key read from ``state`` / ``agent_context`` /
    ``RunnableConfig.configurable`` / ``payload`` in the agent code.
  - Every distinct type used in ``isinstance(x, Type)`` checks in the
    agent code (excluding plain builtins like dict/str/list).
  - Each occurrence's file:line for grepability.
  - Diff against the PRD or scenario_state_schema (when --prd /
    --schema is supplied): keys read by the code but NOT declared in
    the contract.

Exits 0 if every read is declared (or no contract supplied), 1 if
any undeclared reads are found. ``isinstance`` findings are reported
informationally — they don't fail the audit because the audit can't
know which mocks actually exist.

Scope: the AGENT's own code. The walk skips vendored/generated trees
(``.venv``, ``__pycache__``, ``node_modules``, ``site-packages``,
``.git``, ``.tox``) and, because the audit is normally pointed at the
agent ROOT, the simulation harness beside it: the whole ``plurai_sim/``
directory plus this plugin's own generated scripts by basename
(``verify_*.py``, ``audit_*.py``, ``test_mock_contract.py``) wherever
they sit. Auditing those reported the verifiers' own ``isinstance``
sites — pages of noise about code the customer does not own. Add
``--exclude <glob>`` (repeatable, matched against the basename and the
path relative to the module root) for anything else.

Catches the common patterns:

    state.get("foo")
    state["foo"]
    agent_context.get("foo")
    agent_context["foo"]
    config.get("configurable", {}).get("foo")
    config["configurable"]["foo"]
    config["configurable"][SOME_CONSTANT]   # resolved to its string value
    payload.get("foo")

Module-level string constants (``NAME = "literal"`` at top level of
any .py file in the agent module) are resolved across the whole
project. A read like ``config["configurable"][CONSUMPTION_BACKEND_KEY]``
is reported as ``config.configurable["consumption_backend"] (via
constant CONSUMPTION_BACKEND_KEY)``. This is the canonical pattern
for middleware injection (consumption backends, tracing clients,
auth contexts) — the simulate.py adapter MUST seed these keys or
production code reaches ``None`` and crashes / silently degrades.

NOT catching (false-negative tolerated):
  - Dynamic keys built via f-strings / format / + concatenation.
  - Custom wrapper classes (CustomState.get('foo')) — we only check
    common identifier names.
  - Nested object attribute reads (state.refinement_questions). Agent
    state is usually dict-shaped, so this is rare.
  - Constants assigned to non-string values (computed at runtime,
    derived from imports) — only literal ``NAME = "..."`` resolves.
  - Per-run values a framework carries anywhere other than a nested
    ``config['configurable']`` dict.

False negatives are acceptable; false positives cost manual review.
The audit is a HELP for the customer's PRD authoring, not a
guarantee that the PRD is complete.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Sequence

# Identifiers we treat as state-bound dicts. Tool implementations
# typically receive one of these, whatever the framework: a graph node
# takes ``(state, config)``, and tools elsewhere take their args plus an
# injected state or context object.
_STATE_LIKE_IDENTIFIERS = frozenset({
    'state',
    'agent_context',
    'context',
    'ctx',
    'payload',
    'session_state',
})

# Names whose ``.get('configurable', {}).get('key')`` chains we want to walk
# into. That nesting is the ``RunnableConfig`` spelling specifically; a
# framework that carries per-run values some other way is not read here.
_CONFIG_LIKE_IDENTIFIERS = frozenset({'config', 'cfg', 'runnable_config'})

# Builtins and stdlib types whose ``isinstance(x, T)`` checks are
# uninteresting for mock-subclass auditing — they're never the customer's
# own domain types. Excluding them keeps the isinstance report focused on
# storage/service/protocol classes that mocks must satisfy.
_ISINSTANCE_IGNORE = frozenset({
    'str', 'int', 'float', 'bool', 'bytes', 'bytearray', 'complex',
    'list', 'tuple', 'set', 'frozenset', 'dict',
    'object', 'type', 'Exception', 'BaseException',
    'datetime', 'date', 'time', 'timedelta',
    'Path', 'PurePath',
    'UUID',
    # langchain/langgraph message types — these are dependency types, not
    # customer-owned. Mocks don't replace these.
    'BaseMessage', 'HumanMessage', 'AIMessage', 'SystemMessage', 'ToolMessage',
    'FunctionMessage', 'ChatMessage',
    # Pydantic / dataclass introspection
    'BaseModel',
})


@dataclass(frozen=True)
class StateRead:
    """One read of a literal-string key from a state-bound dict.

    ``via_constant`` carries the symbol name when the key was written
    as a module-level constant rather than a literal at the call site
    (e.g. ``config["configurable"][CONSUMPTION_BACKEND_KEY]``). The
    audit resolves the constant to its string value but keeps the
    symbol name so the report can flag the indirection — these are
    almost always middleware-injection keys the simulate.py adapter
    must seed.
    """

    container: str  # e.g. 'state', 'agent_context'
    key: str
    file: str
    line: int
    via_constant: str | None = None

    def location(self, root: Path) -> str:
        try:
            rel = Path(self.file).resolve().relative_to(root.resolve())
        except ValueError:
            rel = Path(self.file)
        return f'{rel}:{self.line}'


@dataclass(frozen=True)
class IsinstanceCheck:
    """One ``isinstance(x, Type)`` check found in the agent code.

    ``type_name`` is the dotted reference as written in source (e.g.
    ``ExampleSetApiStorage`` or ``storage_module.CalibrationStorage``).
    Mocked replacements MUST subclass this type for the dispatch to
    pick them up — otherwise the production code falls through to its
    error branch (typically a ``raise TypeError`` that's caught by a
    broad ``except Exception`` and silently swallowed).
    """

    type_name: str
    file: str
    line: int

    def location(self, root: Path) -> str:
        try:
            rel = Path(self.file).resolve().relative_to(root.resolve())
        except ValueError:
            rel = Path(self.file)
        return f'{rel}:{self.line}'


class _StateReadVisitor(ast.NodeVisitor):
    """AST visitor that records both state-key reads and isinstance-checks.

    Two parallel collections so report() can present them as distinct
    categories. Mocked-storage rejections (the ``isinstance`` case) are
    a different kind of fix from missing state defaults (the ``state.get``
    case), and operators need to act on them differently.

    ``constants`` is the project-wide ``{name: value}`` map of resolved
    module-level string constants. The visitor uses it to translate
    ``config["configurable"][SOME_CONST]`` into a recorded read of the
    literal string ``SOME_CONST`` resolves to. A value of ``None`` in
    the map marks an ambiguous symbol (defined with different values in
    different files) — those are reported as unresolved so the operator
    can investigate manually.
    """

    def __init__(self, filename: str, constants: dict[str, str | None] | None = None) -> None:
        self.filename = filename
        self.constants: dict[str, str | None] = dict(constants or {})
        self.reads: list[StateRead] = []
        self.isinstance_checks: list[IsinstanceCheck] = []
        self.gemini_streaming_risks: list[GeminiStreamingRisk] = []
        # Enclosing-function parameter stack: a state-like name only carries INJECTED state
        # when it arrived as a parameter (the tool/node shape this audit exists for). A
        # local dict that merely SHARES the name — a CLI's own `state`, a tool building a
        # `payload` return value — reads agent OUTPUT or its own scratch, not a
        # precondition, and flagging it forced junk declarations into the PRD (observed
        # live: `payload["catalog"]` and a CLI's `state["last_suggestions"]`).
        self._param_frames: list[set[str]] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        args = node.args
        params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                params.add(extra.arg)
        self._param_frames.append(params)
        self.generic_visit(node)
        self._param_frames.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function
    visit_Lambda = _visit_function  # `add_node("n", lambda state: ...)` is the canonical node shape

    def _is_injected_state(self, container: str) -> bool:
        # ANY enclosing frame, not just the innermost: a node factory's inner helper closing
        # over its outer `state`/`ctx` parameter reads genuinely injected state.
        return any(container in frame for frame in self._param_frames)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Writes/deletes never read a precondition — `payload["catalog"] = {...}` builds a
        # return value. generic_visit still runs so reads nested in the value/slice count.
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.generic_visit(node)
            return
        # state["foo"] / agent_context["foo"] / state[SOME_CONST] — only when the container
        # is a parameter of the enclosing function (injected state, not a same-named local).
        container = _identifier_name(node.value)
        if container in _STATE_LIKE_IDENTIFIERS and self._is_injected_state(container):
            key, via = self._resolve_key_node(node.slice)
            if key is not None:
                self.reads.append(StateRead(container, key, self.filename, node.lineno, via))
        # config["configurable"]["foo"] / config["configurable"][SOME_CONST]
        # The pattern is: outer Subscript whose .value is itself an inner
        # Subscript on a config-like Name with slice "configurable". The
        # outer slice is the actual key we want. ``_is_config_like_target``
        # also accepts ``(config or {})`` etc. so the fallback idiom matches.
        else:
            inner = node.value
            if (
                isinstance(inner, ast.Subscript)
                and _is_config_like_target(inner.value)
                and _string_literal(inner.slice) == 'configurable'
            ):
                key, via = self._resolve_key_node(node.slice)
                if key is not None:
                    self.reads.append(StateRead(
                        container='config.configurable',
                        key=key,
                        file=self.filename,
                        line=node.lineno,
                        via_constant=via,
                    ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # state.get("foo") / agent_context.get("foo", default) / state.get(SOME_CONST) —
        # same injected-parameter scoping as visit_Subscript.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == 'get'
            and node.args
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in _STATE_LIKE_IDENTIFIERS
            and self._is_injected_state(node.func.value.id)
        ):
            key, via = self._resolve_key_node(node.args[0])
            if key is not None:
                self.reads.append(StateRead(
                    container=node.func.value.id,
                    key=key,
                    file=self.filename,
                    line=node.lineno,
                    via_constant=via,
                ))
        # config.get("configurable", {}).get("foo") — LangGraph idiom.
        # Also handles config.get("configurable", {}).get(SOME_CONST) where
        # SOME_CONST is a module-level string constant. ``_is_config_like_target``
        # accepts both ``config`` directly and the ``(config or {})`` fallback
        # idiom that production code routinely uses to defend against a None
        # config argument.
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == 'get'
            and node.args
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Attribute)
            and node.func.value.func.attr == 'get'
            and node.func.value.args
            and isinstance(node.func.value.args[0], ast.Constant)
            and node.func.value.args[0].value == 'configurable'
            and _is_config_like_target(node.func.value.func.value)
        ):
            key, via = self._resolve_key_node(node.args[0])
            if key is not None:
                self.reads.append(StateRead(
                    container='config.configurable',
                    key=key,
                    file=self.filename,
                    line=node.lineno,
                    via_constant=via,
                ))
        # isinstance(x, SomeType) — runtime type dispatch the mocks must satisfy.
        # Skip plain builtins (str/int/dict/...) because mocks never replace them.
        # Skip well-known dependency types (BaseMessage etc.) — they're library
        # types, not customer domain types, so the operator can't action them.
        elif (
            isinstance(node.func, ast.Name)
            and node.func.id == 'isinstance'
            and len(node.args) == 2
        ):
            for type_name in _isinstance_type_names(node.args[1]):
                if type_name in _ISINSTANCE_IGNORE:
                    continue
                self.isinstance_checks.append(IsinstanceCheck(
                    type_name=type_name,
                    file=self.filename,
                    line=node.lineno,
                ))
        # Gemini-3 thinking-model construction with streaming left enabled.
        # Two patterns the customer's code can take:
        #   (1) ChatGoogleGenerativeAI(model="gemini-3-flash-preview", ...,
        #       thinking_level=..., disable_streaming=True/missing)
        #   (2) LLMConfig(type="google", name="gemini-3...", model_kwargs={
        #       "thinking_level": ..., "disable_streaming": True/missing})
        # Both reach the same SDK; the disable_streaming guard must appear
        # in EITHER the direct kwargs OR the model_kwargs dict.
        risk = self._classify_gemini_streaming_risk(node)
        if risk is not None:
            self.gemini_streaming_risks.append(risk)
        self.generic_visit(node)

    def _classify_gemini_streaming_risk(
        self, node: ast.Call,
    ) -> GeminiStreamingRisk | None:
        """Return a risk record iff this Call is a Gemini-3 thinking-model
        construction that's missing the ``disable_streaming=True`` flag.

        Returns ``None`` for any other Call (non-Gemini, non-thinking,
        already-disabled-streaming, or unable-to-statically-determine).
        """
        callee = _identifier_name(node.func) or ''
        if callee not in ('ChatGoogleGenerativeAI', 'LLMConfig'):
            return None

        # Collapse kwargs + model_kwargs into a single lookup so we can ask
        # "is disable_streaming set?" / "is thinking_level set?" once.
        # Treat None values as "not set" (matches Python truthiness checks
        # on these specific flags in the langchain-google SDK).
        kw_values: dict[str, ast.AST] = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        model_kwargs = kw_values.get('model_kwargs')
        mk_values: dict[str, ast.AST] = {}
        if isinstance(model_kwargs, ast.Dict):
            for k, v in zip(model_kwargs.keys, model_kwargs.values):
                key_str = _string_literal(k)
                if key_str is not None:
                    mk_values[key_str] = v

        # Gemini-3 model name detection. For ChatGoogleGenerativeAI the model
        # comes via ``model=...``; for LLMConfig the field is ``name=...``
        # and an explicit ``type="google"`` confirms the provider.
        if callee == 'LLMConfig':
            type_arg = _string_literal(kw_values.get('type'))
            if type_arg != 'google':
                return None
            model_name = _string_literal(kw_values.get('name'))
        else:
            model_name = _string_literal(kw_values.get('model'))
        if not model_name or 'gemini-3' not in model_name:
            return None

        # Thinking-mode detection. Either ``thinking_level`` / ``thinking_budget``
        # at the top level OR nested under model_kwargs counts. The flag's
        # mere presence is what we gate on — its specific value (LOW / MEDIUM /
        # HIGH / DISABLED) is the customer's call; we don't second-guess it.
        thinking_keys = {'thinking_level', 'thinking_budget', 'include_thoughts'}
        has_thinking = any(k in kw_values for k in thinking_keys) or any(
            k in mk_values for k in thinking_keys
        )
        if not has_thinking:
            return None

        # disable_streaming=True guard. Considered satisfied when EITHER:
        #   - a top-level ``disable_streaming=True`` (or any truthy literal)
        #   - or the same key in model_kwargs with a truthy value
        # We can only verify the True branch via static analysis; an
        # expression / variable falls through to the unsafe path because we
        # can't prove it's True at audit time.
        def _is_true_literal(n: ast.AST | None) -> bool:
            return isinstance(n, ast.Constant) and n.value is True

        if _is_true_literal(kw_values.get('disable_streaming')):
            return None
        if _is_true_literal(mk_values.get('disable_streaming')):
            return None

        return GeminiStreamingRisk(
            file=self.filename,
            line=node.lineno,
            model_hint=model_name,
        )

    def _resolve_key_node(self, node: ast.AST) -> tuple[str | None, str | None]:
        """Resolve a key-position node to ``(literal_value, via_constant_name)``.

        - String literal → (value, None).
        - Name resolved via project constants → (constant_value, name).
        - Unresolved Name or anything dynamic → (None, None).
        """
        literal = _string_literal(node)
        if literal is not None:
            return literal, None
        if isinstance(node, ast.Name) and node.id in self.constants:
            value = self.constants[node.id]
            if value is not None:
                return value, node.id
        return None, None


def _isinstance_type_names(arg: ast.AST) -> list[str]:
    """Extract the type identifier(s) from the 2nd arg of ``isinstance(x, T)``.

    Handles three shapes:
      - ``isinstance(x, Foo)`` → ['Foo']
      - ``isinstance(x, module.Foo)`` → ['module.Foo']
      - ``isinstance(x, (Foo, Bar))`` → ['Foo', 'Bar']  (tuple of alternatives)
    Anything more dynamic (function call result, attribute chain we can't
    walk, etc.) is silently skipped — false negatives are fine, false
    positives would noise the report.
    """
    if isinstance(arg, ast.Name):
        return [arg.id]
    if isinstance(arg, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = arg
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ['.'.join(reversed(parts))]
        return []
    if isinstance(arg, ast.Tuple):
        names: list[str] = []
        for elt in arg.elts:
            names.extend(_isinstance_type_names(elt))
        return names
    return []


def _identifier_name(node: ast.AST) -> str | None:
    """Return the bare identifier name if ``node`` is a Name node."""
    return node.id if isinstance(node, ast.Name) else None


def _is_config_like_target(node: ast.AST) -> bool:
    """Return True if ``node`` evaluates to a config-like dict for the
    purposes of the audit.

    Accepts both ``Name(config)`` directly and the defensive
    ``(config or {})`` / ``(config or default)`` idiom that production
    code uses to handle a None config argument. The audit unwraps a
    single ``BoolOp(or)`` whose left operand is a config-like Name.
    """
    if isinstance(node, ast.Name) and node.id in _CONFIG_LIKE_IDENTIFIERS:
        return True
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Name) and first.id in _CONFIG_LIKE_IDENTIFIERS:
            return True
    return False


def _string_literal(node: ast.AST) -> str | None:
    """Return the literal string value if ``node`` is a string constant."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


@dataclass(frozen=True)
class GeminiStreamingRisk:
    """One ``ChatGoogleGenerativeAI(...)`` construction site that uses a
    Gemini-3 thinking model AND doesn't pass ``disable_streaming=True``.

    The underlying failure mode (documented in langchain-google#1382 and
    openclaw#76071): Gemini-3 thinking models emit ``thoughtSignature``
    frames during the thinking phase that some idle-watchdogs don't
    recognise as activity. The call looks like a wall-clock hang but
    the model is still working. ``disable_streaming=True`` bypasses the
    streaming path entirely, so the response arrives as a single blob
    that the provider-level timeout can bound correctly.

    Surfaced at audit time so operators see the diagnosis BEFORE a
    plurai-run-simulation invocation against the agent hits a
    300+ second hang on its first turn.
    """

    file: str
    line: int
    model_hint: str  # which gemini-3 model name was named, when extractable

    def location(self, root: Path) -> str:
        try:
            rel = Path(self.file).resolve().relative_to(root.resolve())
        except ValueError:
            rel = Path(self.file)
        return f'{rel}:{self.line}'


@dataclass(frozen=True)
class AuditResult:
    """Combined audit output: state-key reads + isinstance checks +
    Gemini-3 streaming risks."""

    reads: list[StateRead]
    isinstance_checks: list[IsinstanceCheck]
    gemini_streaming_risks: list[GeminiStreamingRisk] = field(default_factory=list)


_SKIPPED_PARTS = frozenset({'.venv', '__pycache__', 'node_modules', '.git', 'site-packages', '.tox', 'plurai_sim'})

# The plugin's OWN generated scripts, by EXACT basename, wherever they are copied to.
# `plurai_sim` above catches the usual home; these are the ones an operator copies next to the
# agent. Exact names, not `verify_*.py` / `audit_*.py` globs: those also swallow ordinary customer
# files — `verify_payment.py`, `verify_token.py`, `audit_trail.py` — and this audit EXITS 1 on an
# undeclared read, so a file hidden by a name collision turns a real finding into a pass.
_SKIPPED_BASENAMES = frozenset(
    {
        'verify_state_schema.py',
        'verify_policies.py',
        'verify_simulate.py',
        'verify_smoke.py',
        'verify_client_api.py',
        'verify_harness_opacity.py',
        'verify_config.py',
        'verify_prd_appendix.py',
        'audit_state_preconditions.py',
        'test_mock_contract.py',
    }
)


def _is_skipped(py: Path, root: Path, excludes: Sequence[str] = ()) -> bool:
    """True when ``py`` is vendored, is harness/verifier code, or matches a ``--exclude`` glob.

    Excludes are matched against BOTH the basename and the module-root-relative path, so
    ``--exclude 'legacy/*'`` and ``--exclude '*_pb2.py'`` both do what they look like.
    """
    try:
        relative = py.relative_to(root)
    except ValueError:  # not under root at all — fall back to the absolute path
        relative = py
    # Relative, so pointing the audit AT a skipped directory still audits it: the name of a
    # parent of the module root is the operator's business, not a reason to report nothing.
    if any(part in _SKIPPED_PARTS for part in relative.parts):
        return True
    if py.name in _SKIPPED_BASENAMES:
        return True
    rel = relative.as_posix()
    return any(fnmatch(py.name, pattern) or fnmatch(rel, pattern) for pattern in excludes)


def _collect_module_string_constants_from_tree(tree: ast.Module) -> dict[str, str]:
    """Return ``{name: value}`` for every top-level ``NAME = "literal"``
    assignment in a single parsed module.

    Only considers single-target, string-valued assignments at module
    scope. Tuple unpacking, annotated assignments with non-string
    values, and conditionally-defined constants are deliberately
    skipped — the audit prefers no resolution over wrong resolution.
    """
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = _string_literal(node.value)
        if value is None:
            continue
        out[node.targets[0].id] = value
    return out


def collect_project_constants(root: Path, excludes: Sequence[str] = ()) -> dict[str, str | None]:
    """Project-wide pre-pass for module-level string constants.

    Walks every .py file under ``root`` (excluding vendored paths) and
    returns a unified ``{name: value}`` map. When the same symbol is
    defined with conflicting values across files, the entry collapses
    to ``None`` so the visitor treats it as unresolved (better than
    arbitrarily picking one definition).

    Cross-file resolution matters because middleware-key constants
    are conventionally defined once (in a ``consumption.py`` /
    ``constants.py`` module) and imported by the call sites that
    read them — same-file resolution alone misses that idiom.
    """
    out: dict[str, str | None] = {}
    for py in root.rglob('*.py'):
        if _is_skipped(py, root, excludes):
            continue
        try:
            source = py.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for name, value in _collect_module_string_constants_from_tree(tree).items():
            if name not in out:
                out[name] = value
            elif out[name] is not None and out[name] != value:
                out[name] = None  # ambiguous — multiple definitions disagree
    return out


def audit_file(path: Path, constants: dict[str, str | None] | None = None) -> AuditResult:
    """Run the audit on a single Python file. Skips unparseable files
    with a warning rather than aborting the whole audit — a single
    syntactically broken file in the agent's codebase shouldn't block
    the rest of the analysis.

    ``constants`` is the optional project-wide constants map. When
    omitted, only literal-string keys are resolved (Name-key reads
    are silently skipped — preserves the pre-enhancement behavior
    for callers that haven't opted in).
    """
    empty = AuditResult(reads=[], isinstance_checks=[], gemini_streaming_risks=[])
    try:
        source = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        print(f'warning: skipping {path}: {exc}', file=sys.stderr)
        return empty
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f'warning: skipping {path} (syntax error): {exc}', file=sys.stderr)
        return empty
    visitor = _StateReadVisitor(str(path), constants=constants)
    visitor.visit(tree)
    return AuditResult(
        reads=visitor.reads,
        isinstance_checks=visitor.isinstance_checks,
        gemini_streaming_risks=visitor.gemini_streaming_risks,
    )


def audit_module(root: Path, excludes: Sequence[str] = ()) -> AuditResult:
    """Walk ``root`` recursively, auditing every .py file the AGENT owns.

    Excludes common vendored / generated paths (``.venv``, ``__pycache__``,
    ``node_modules``), the simulation harness beside the agent
    (``plurai_sim/`` and this plugin's own ``verify_*.py`` / ``audit_*.py`` /
    ``test_mock_contract.py``), and anything matching ``excludes`` — so the
    report is about the customer's code and nothing else.

    Runs a project-wide pre-pass to collect module-level string
    constants so the visitor can resolve symbols like
    ``CONSUMPTION_BACKEND_KEY`` to their literal values when they
    appear in key positions.
    """
    constants = collect_project_constants(root, excludes)
    reads: list[StateRead] = []
    checks: list[IsinstanceCheck] = []
    risks: list[GeminiStreamingRisk] = []
    for py in root.rglob('*.py'):
        if _is_skipped(py, root, excludes):
            continue
        result = audit_file(py, constants=constants)
        reads.extend(result.reads)
        checks.extend(result.isinstance_checks)
        risks.extend(result.gemini_streaming_risks)
    return AuditResult(reads=reads, isinstance_checks=checks, gemini_streaming_risks=risks)


def declared_keys_from_prd(prd_path: Path) -> set[str]:
    """Best-effort extract of declared agent_context keys from the
    customer's simulation_prd.md.

    Looks for fenced YAML / markdown lists under a section that mentions
    ``agent_context``. Tolerant of multiple shapes — operators write
    these by hand. False negatives (missing a key the PRD does declare)
    cost a spurious warning; false positives (claiming a key is
    declared when it isn't) would mute a real audit finding, so we
    err toward conservative extraction.
    """
    import re  # noqa: PLC0415

    if not prd_path.is_file():
        return set()
    text = prd_path.read_text(encoding='utf-8')

    # Find the "agent_context" or "agent context" section then extract
    # all backticked-identifiers + bare-identifiers in the next ~80 lines.
    keys: set[str] = set()
    # Pattern: lines starting with ``-``, ``*``, ``+`` that have a
    # backticked identifier; OR YAML-style ``key:`` lines.
    for chunk_match in re.finditer(
        r'(?im)^#{1,6}.*agent[_ ]context.*$',
        text,
    ):
        # Take the next 100 lines after the heading.
        start = chunk_match.end()
        chunk = text[start:start + 6000]
        # Stop at next heading
        next_heading = re.search(r'(?m)^#{1,6}\s', chunk)
        if next_heading:
            chunk = chunk[:next_heading.start()]
        # Backticked identifiers in list items
        keys.update(re.findall(r'[-*+]\s+`([a-zA-Z_][a-zA-Z0-9_]*)`', chunk))
        # YAML-style "key:" at line start (lowercase identifier)
        keys.update(re.findall(r'(?m)^\s*([a-z_][a-z0-9_]*):', chunk))
    return keys


def declared_keys_from_schema(schema_path: Path) -> set[str]:
    """Extract agent_context keys from scenario_state_schema.yaml.

    The schema's authoritative shape is::

        agent_context_fields:
          dataset_id:
            type: string
            required: true

    We pull every top-level key under ``agent_context_fields``.
    """
    if not schema_path.is_file():
        return set()
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print(
            'warning: pyyaml not available; cannot parse schema. '
            'Install with `uv pip install pyyaml`.',
            file=sys.stderr,
        )
        return set()
    try:
        loaded = yaml.safe_load(schema_path.read_text(encoding='utf-8')) or {}
    except yaml.YAMLError as exc:
        print(f'warning: schema parse error: {exc}', file=sys.stderr)
        return set()
    fields = loaded.get('agent_context_fields') or {}
    if not isinstance(fields, dict):
        return set()
    return set(fields.keys())


def report(
    result: AuditResult,
    root: Path,
    declared: set[str],
    contract_name: str | None,
) -> tuple[set[str], set[str]]:
    """Print a structured report. Returns ``(found_keys, undeclared_keys)``.

    State-key reads drive the exit code (undeclared = fail). isinstance
    checks are reported informationally — the auditor has no contract to
    diff them against, so they're always a "review me" warning, not a fail.
    """
    reads = result.reads
    by_key: dict[tuple[str, str], list[StateRead]] = defaultdict(list)
    for r in reads:
        by_key[(r.container, r.key)].append(r)

    print('=== Agent state preconditions audit ===')
    print(f'Module root: {root}')
    files_scanned = len({r.file for r in reads} | {c.file for c in result.isinstance_checks})
    print(f'Files scanned: {files_scanned}')
    print(f'Distinct (container, key) reads: {len(by_key)}')
    print(f'Distinct isinstance() types: {len({c.type_name for c in result.isinstance_checks})}')
    print()

    found_keys: set[str] = set()
    undeclared: set[str] = set()

    if by_key:
        found_keys = {key for (_container, key) in by_key.keys()}
        if contract_name:
            undeclared = found_keys - declared

        print('Reads grouped by container + key:')
        for (container, key), locations in sorted(by_key.items()):
            mark = '  '
            if contract_name and key not in declared:
                mark = '! '  # undeclared
            # Show via-constant provenance when every site agrees on the
            # symbol (the usual case — a single constant gets imported
            # everywhere). Mixed sites (some literal, some constant) drop
            # the annotation so the operator inspects the raw list.
            vias = {r.via_constant for r in locations}
            suffix = ''
            if vias == {None}:
                pass
            elif len(vias) == 1:
                only_via = next(iter(vias))
                suffix = f'  ← via constant {only_via}'
            else:
                suffix = '  ← mixed literal + constant call sites'
            print(f'{mark}{container}["{key}"]  ({len(locations)} site(s)){suffix}')
            for r in locations[:5]:  # cap to first 5 sites per key
                print(f'      {r.location(root)}')
            if len(locations) > 5:
                print(f'      ... and {len(locations) - 5} more sites')

        _report_middleware_keys(reads, root)

        if contract_name:
            print()
            if undeclared:
                print(f'!!! {len(undeclared)} key(s) read by code but NOT declared in {contract_name}:')
                for k in sorted(undeclared):
                    print(f'      - {k}')
                print()
                print('Add these to your PRD\'s agent_context_fields section')
                print('and the schema. Scenarios will then carry the values your')
                print('production code needs; agents won\'t error-cascade trying')
                print('to recover from missing state at runtime.')
                print()
                print('!!! RESOLUTION RULE — read this before you edit the schema:')
                print('    A key whose tool ERRORS / refuses when it is missing or empty')
                print('    (e.g. `if not state.get("X"): return error(...)`) is a STARTUP')
                print('    PRECONDITION — state a prior in-graph flow (onboarding /')
                print('    calibration) produced before this session. For each such key:')
                print('      • mark it `precondition: true` in the schema, AND')
                print('      • make sure scenarios seed a COHERENT, NON-EMPTY value')
                print('        (the backend hard-rejects an empty precondition at gen time).')
                print('    Do NOT resolve it with `required: false` / `default: []` /')
                print('    `default: ""` — an empty default re-creates the exact')
                print('    production-impossible boot state the agent\'s first tool errors')
                print('    on, which cascades into a high tool-error rate that looks like')
                print('    an agent fault but is a harness defect. Only a key that is')
                print('    genuinely OPTIONAL (tool works fine when it is absent) may use')
                print('    a default.')
            else:
                print(f'OK — every read is declared in {contract_name}.')
    else:
        print('No state-shaped reads found. If your tools do gate on state,')
        print('they may use identifiers outside the audit\'s allowlist')
        print(f'({sorted(_STATE_LIKE_IDENTIFIERS)}).')

    _report_isinstance_checks(result.isinstance_checks, root)
    _report_gemini_streaming_risks(result.gemini_streaming_risks, root)
    return found_keys, undeclared


def _report_gemini_streaming_risks(
    risks: list[GeminiStreamingRisk], root: Path,
) -> None:
    """Surface Gemini-3 thinking-model constructions that left streaming on.

    Why this is preventive: the runtime symptom of this misconfiguration
    is a plurai-run-simulation invocation that hangs for the FULL
    per-turn budget (1260s in a qualification pass, 1860s in a scored run)
    on its first turn, with no error in the agent's own logs — the SDK is mid-stream of
    ``thoughtSignature`` frames the idle-watchdog can't see. Operators
    spend 30+ minutes diagnosing this from scratch. Catching it at audit
    time (before the first scenario runs) avoids that entirely.

    Empty (no risks found) → no callout.
    """
    if not risks:
        return
    print()
    print('=== Gemini-3 thinking + streaming risk ===')
    print(f'Found {len(risks)} LLM construction site(s) using a Gemini-3')
    print('thinking model with streaming enabled (the langchain-google default).')
    print('Gemini-3 emits thoughtSignature frames during the thinking phase')
    print('that some idle-watchdogs do not recognise as activity — calls')
    print('look like wall-clock hangs but the model is still working. Fix:')
    print('add ``"disable_streaming": True`` to the model_kwargs (or the')
    print('top-level kwarg, depending on call shape).')
    print('References: langchain-google#1382, openclaw#76071,')
    print('            steipete/summarize#82.')
    print()
    for r in risks:
        print(f'  ! {r.location(root)} — model={r.model_hint!r}')


def _report_middleware_keys(reads: list[StateRead], root: Path) -> None:
    """Print a focused callout for ``config["configurable"][CONST]`` reads.

    These are the canonical middleware-injection points (consumption
    backends, tracing clients, auth contexts). The simulate.py adapter
    MUST seed every one of them in the ``config["configurable"]`` it
    passes to ``graph.ainvoke(...)`` — otherwise production code reads
    ``None`` and crashes / silently degrades on the first invocation
    that exercises the middleware.

    Empty (no constant-resolved configurable reads) → no callout.
    """
    middleware = [
        r for r in reads
        if r.container == 'config.configurable' and r.via_constant is not None
    ]
    if not middleware:
        return
    by_key: dict[tuple[str, str], list[StateRead]] = defaultdict(list)
    for r in middleware:
        by_key[(r.via_constant or '?', r.key)].append(r)
    print()
    print('=== Middleware injection keys (config["configurable"][CONST]) ===')
    print(f'Found {len(by_key)} key(s) read via module-level constants.')
    print('These are typically middleware (consumption backend, tracing,')
    print('auth context). Your simulate.py adapter must seed each one in')
    print('the config["configurable"] passed to graph.ainvoke(...), or the')
    print('production code reads None and crashes at the first tool call.')
    print()
    for (const_name, key), locations in sorted(by_key.items()):
        print(f'  {const_name} = "{key}"  ({len(locations)} read site(s))')
        for r in locations[:3]:
            print(f'      {r.location(root)}')
        if len(locations) > 3:
            print(f'      ... and {len(locations) - 3} more sites')


def _report_isinstance_checks(checks: list[IsinstanceCheck], root: Path) -> None:
    """Print isinstance findings. Always informational, never fails the audit."""
    if not checks:
        return
    by_type: dict[str, list[IsinstanceCheck]] = defaultdict(list)
    for c in checks:
        by_type[c.type_name].append(c)

    print()
    print('=== isinstance() checks on customer types ===')
    print(f'Found {len(by_type)} distinct type(s) used in isinstance() checks.')
    print('Any MOCK that replaces an instance of one of these types must')
    print('SUBCLASS the production type — otherwise the production code\'s')
    print('isinstance() dispatch will reject the mock, raising TypeError.')
    print('A broad except Exception will typically swallow it as an empty')
    print('return, producing a silent failure that\'s visible only as a')
    print('logged traceback at DEBUG level. (Real case: ``MockUserExampleStorage``')
    print('rejected by ``services.py:load_user_examples`` because the type-check')
    print('whitelisted ExampleSetApiStorage / CalibrationStorage but not the mock.)')
    print()
    for type_name, locations in sorted(by_type.items()):
        print(f'  isinstance(x, {type_name})  ({len(locations)} site(s))')
        for c in locations[:5]:
            print(f'      {c.location(root)}')
        if len(locations) > 5:
            print(f'      ... and {len(locations) - 5} more sites')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='audit_state_preconditions',
        description=__doc__.splitlines()[0] if __doc__ else None,
    )
    p.add_argument(
        'module_root',
        type=Path,
        help='Path to the agent module root (e.g. ./agent or .)',
    )
    p.add_argument(
        '--prd',
        type=Path,
        help='Optional: path to simulation_prd.md. Reads declared keys from the PRD.',
    )
    p.add_argument(
        '--schema',
        type=Path,
        help='Optional: path to scenario_state_schema.yaml. Authoritative source of declared keys.',
    )
    p.add_argument(
        '--exclude',
        action='append',
        default=[],
        metavar='GLOB',
        help=(
            'Repeatable: skip files matching GLOB (matched against the basename and the path '
            'relative to the module root). plurai_sim/, verify_*.py, audit_*.py and '
            'test_mock_contract.py are always skipped.'
        ),
    )
    args = p.parse_args(argv)

    if not args.module_root.is_dir():
        print(f'error: {args.module_root} is not a directory', file=sys.stderr)
        return 2

    result = audit_module(args.module_root, args.exclude)

    declared: set[str] = set()
    contract_name: str | None = None
    if args.schema:
        declared = declared_keys_from_schema(args.schema)
        contract_name = f'schema={args.schema}'
    elif args.prd:
        declared = declared_keys_from_prd(args.prd)
        contract_name = f'PRD={args.prd}'

    _found, undeclared = report(result, args.module_root, declared, contract_name)
    return 1 if undeclared else 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
