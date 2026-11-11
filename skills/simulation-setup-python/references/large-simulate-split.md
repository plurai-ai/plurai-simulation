# When `simulate.py` grows — split into 3 files

Read this when `simulate.py` exceeds ~600 lines. It carries the 3-file layout,
the storage pass-through a real tool's store swap uses (delegate to the seam mock,
translate, nothing else), and the mock-dispatch pattern choice. Below the threshold
a single file is fine — splitting smaller files adds boilerplate without reducing
review burden. A long storage class is never a reason to split: it is a sign the
harness is implementing the store, which the seam mock owns.

**Recommended layout (use the EXACT filenames so verifiers auto-discover the helpers):**

```
plurai_sim/         ← MUST be named this (NOT `plurai/`). See "directory naming" below.
├── simulate.py       # orchestrator: Phase 1 stubs, Phase 2 configure, Phase 5 invoke_agent + seed_state
├── _storage.py       # Phase 3: storage pass-through to the seam mocks + factory swap (import-time side effect)
├── _mocks.py         # Phase 4: @plurai.mock descriptors + coroutine factories
└── plurai.config.yaml
```

**⚠️ DIRECTORY NAMING — DO NOT use `plurai/` as the directory name.** The customer's simulation directory MUST be named `plurai_sim/` (or any name that doesn't collide with the SDK package name `plurai`). If you name it `plurai/`, Python's import system finds the directory FIRST on `sys.path` and `import plurai` resolves to the customer's directory instead of the SDK — `import plurai; plurai.configure(...)` then fails with `AttributeError: module 'plurai' has no attribute 'configure'`. We hit this in a real customer regenerate. The verifier discovery is path-based and doesn't care about the directory name; the only requirement is non-collision with `plurai` (the SDK package). Use `plurai_sim/`.

**Import order is load-bearing.** `simulate.py` must import `_storage.py` BEFORE the agent module. Otherwise any factory captured at agent-import time — `from our_backend.factory import create_storage` binds `create_storage` to the current attribute, or a module-level `_storage = create_storage()` runs eagerly — sees the unpatched production factory. A late patch on the factory module's attribute won't redirect already-bound names.

**`simulate.py` (the orchestrator):**

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""Plurai simulation entry point. Splits per-phase logic into _storage.py + _mocks.py."""
from __future__ import annotations
import sys, os
from unittest.mock import MagicMock

# ============== Phase 1: Stubs — MUST run before anything else ==============
sys.modules["our_backend.db"] = MagicMock()
os.environ.setdefault("STRIPE_KEY", "plurai-stub")

# ============== Phase 2: Import plurai ==============
# Import only — do NOT call plurai.configure() here. The harness configures the
# SDK inside this subprocess (with the run's mock URL and mode) before it calls
# seed_state, so a configure() call in simulate.py is at best redundant and at
# worst wrong: it takes (service_url, *, mode) and has no workspace/project
# parameter, and the mock URL is not in this process's environment to pass.
import plurai

# ============== Phase 3: Storage swap to the seam mocks (side-effect import) ==============
# MUST precede the agent import below. `from .` is intentional: the
# customer's package is named `plurai` (per SKILL.md convention) and
# shadows the plurai SDK, so dotted imports like `from plurai import _storage`
# would resolve ambiguously. Relative imports are portable.
from . import _storage  # noqa: F401

# ============== Phase 4a: Import the agent ==============
from our_backend.agents.customer import CustomerAgent
_AGENT = CustomerAgent(...)

# ============== Phase 4b: Install mock coroutines on the agent's tools ==============
from ._mocks import install_mock_coroutines
install_mock_coroutines(_AGENT.graph)

# ============== Phase 5: orchestrator-facing API ==============
async def invoke_agent(payload, config=None):
    return await _AGENT.graph.ainvoke(payload, config=config or {})
```

> **⚠️ Cross-turn state persistence — REQUIRED for multi-turn dialog benches.**
> The orchestrator drives N user-turns and passes a **stable per-scenario
> `thread_id`** (`config={'configurable': {'thread_id': ...}}`) on every turn —
> but a `thread_id` only persists state if the graph was compiled **with a
> checkpointer**. If you reuse the agent's production graph (`_AGENT.graph`)
> compiled **without** one (the correct deployment pattern — the LangGraph
> platform supplies persistence in prod), then **any agent state field written
> in turn N is silently discarded before turn N+1**. Only the `messages` list
> survives (the orchestrator re-sends it); non-message state (an
> selected record id, a cached plan, a `verified` flag) is lost. The agent then
> spuriously fails on turn N+1 ("no record selected", "must re-verify")
> — a **harness artifact** that looks like an agent bug and corrupts eval verdicts.
>
> For a **dialog** bench, the harness MUST reproduce prod's thread persistence.
> Pick one:
> ```python
> # PREFERRED — rebuild the agent with a saver attached, so the orchestrator's
> # thread_id persists ALL state across turns, exactly like production (the
> # test-harness exception to "never add a checkpointer"). Spelled below for
> # LangGraph; on another framework use whatever persistence handle it provides:
> from langgraph.checkpoint.memory import MemorySaver
> _graph = _AGENT.builder.compile(checkpointer=MemorySaver())   # builder = the uncompiled StateGraph
> async def invoke_agent(payload, config=None):
>     return await _graph.ainvoke(payload, config=config or {})
>
> # FALLBACK — when only the compiled, checkpointer-less graph is reachable:
> # manually carry the prior turn's non-message state forward. KEY IT ON thread_id:
> # a single unkeyed module-level dict BLEEDS state ACROSS SCENARIOS in
> # the same runner process — worse than cross-turn loss, it contaminates every
> # later scenario with the first one's residue. Keying on the orchestrator's
> # per-scenario thread_id makes each scenario's carry its own.
> _carry: dict[str, dict] = {}
> async def invoke_agent(payload, config=None):
>     tid = (config or {}).get("configurable", {}).get("thread_id", "default")
>     scoped = _carry.setdefault(tid, {})
>     result = await _AGENT.graph.ainvoke({**scoped, **payload}, config=config or {})
>     scoped.update({k: v for k, v in result.items() if k != "messages"})
>     return result
> ```
> Stubbing out the *production* checkpointer's deps (psycopg2/redis) so it no-ops
> is fine for imports, but a no-op checkpointer gives NO cross-turn persistence —
> you still need one of the above. Single-turn / `invoke_pipeline` benches are
> exempt (no turn boundary to lose state across).

```python

async def seed_state(initial_agent_context, initial_world_state, resume_seed=None):
    # Write the scenario's seed entities through plurai.world_state.apply
    # — no singleton to import, no clear() needed. Per-session isolation
    # is the SDK's responsibility.
    #
    # The third parameter is not optional: the child always calls this hook
    # with three positional args (None when the scenario is fresh), so a
    # two-parameter signature is a TypeError at init. Declare it with the
    # default AND handle a non-None value — accepting the seed and dropping
    # it fails Step 9e's seed_state_ignores_resume_seed.
    #
    # Both halves are elided here; this sketch is about WHERE the hooks live.
    # Take them from the Step 8.5 worked example, in that order — the replay
    # runs last and the world-state half must not return before it.
    import plurai
    plurai.world_state.apply({...})  # see Step 8.5 worked example
    if not resume_seed:
        return
    ...                              # see Step 8.5 worked example, steps 2-5
```

(`invoke_agent` / `invoke_pipeline` and `seed_state` MUST live in simulate.py at module scope — the orchestrator imports simulate.py and looks them up by name. The verifier's `missing_invoke_hook` check enforces "at least one of `invoke_agent` / `invoke_pipeline` must be defined" against simulate.py specifically, NOT against the helpers.)

**Hook choice — `invoke_agent` vs `invoke_pipeline`.** Each component bench picks ONE hook based on what's being tested:

- **`invoke_agent(payload, config)` → dialog mode.** Multi-turn conversational agent. The orchestrator drives N user-turn / agent-response rounds. **Payload contract: each call's `payload['messages']` carries THIS turn's user side and nothing else — never the conversation so far — which is normally the one new user message, is `[]` on the opening call at a bench that declares `agent.io: {may_open: true}` (the agent opens, so turn 0 has no user side at all), and is STILL one message when that turn's user side carries content PARTS — the parts ride inside that message's `content` (a list of `{'type': …}` parts instead of a string), and a wire that carries several messages for one turn (`user_messages` per turn, `initial_messages` for the opener) is concatenated into that single turn, never passed through as several; and `**agent_context` is spread in on turn 1 only — the harness OWNS history, keyed on `config['configurable']['thread_id']` (a checkpointer for LangGraph; a thread-keyed store for framework-free agents). A harness that rebuilds history from `payload['messages']` runs stateless and nothing flags it — every memory/continuity verdict is then meaningless.** Use when the bench targets the full conversational flow, the agent's tool dispatch, multi-step planning, recursion-budget behavior. Pairs with `persona.yaml` + `first_talk.txt` + `initial_world_state.yaml` per scenario; `seed_state` recommended.
- **`invoke_pipeline(inputs) → dict` → one-shot mode.** Single LLM call / pipeline / sub-component. The orchestrator dispatches exactly one call and captures the structured output. Use when the bench targets a sub-component with a fixed input/output contract — sample generation, prompt optimization, single-step classification, summarization, anything that takes structured input and returns structured output without a conversation. Its inputs are the scenario's own `initial_agent_context`, handed to the hook as the typed input dict — there is no `pipeline_io.yaml` to author, and nothing generates one. `seed_state` optional (only if the pipeline reads from world_state). **Declare it — that bench's own `plurai.config.yaml` carries `shape: pipeline` at the ROOT** (Step 8): the declaration is what dispatches, and an undeclared one-shot bench is refused by the runner and by `verify_config`.

A bench tests ONE component, so its simulate.py exports ONE hook. To bench multiple components of the same agent (e.g. the dialog flow AND a sub-pipeline AND another sub-pipeline), create a separate `plurai_sim_<component>/` directory per component — each with its own simulate.py, its own plurai.config.yaml, its own scenarios. Don't share a simulate.py across components: the harness layer (stubs, mock service, storage pass-throughs) varies per component, and sharing creates either a bloated config or an anaemic one.

**Document-extraction agents — feed the generated document to the agent's extraction call.** When the agent's job is to read/extract from a DOCUMENT (a tool or pipeline input ingests a file / PDF / image — `extract_invoice(raw_text)`, `extract_ticket(file_bytes, content_type)`, an OCR/vision call), scenario generation authors a realistic document per scenario and delivers it through the scenario's `agent_context`, so it reaches your hook in `inputs[<field>]` (the field named in `plurai_sim/.document_fields`, written at setup). **Author the hook to feed that document to the agent's REAL extraction function** — how to unpack `inputs[<field>]` is dictated by what that function's signature expects:

- **Extraction takes TEXT** (e.g. `extract_invoice(raw_text: str)`) — `inputs[<field>]` is the document's plain text. Pass it straight through: `extract_invoice(inputs["raw_text"])`.
- **Extraction takes BYTES** (a VISION/OCR agent, e.g. `extract_ticket(file_bytes, content_type)`) — `inputs[<field>]` is a self-describing `data:<content_type>;base64,<…>` URI (the world-state binary channel). The vision call wants raw bytes + a content type, so DECODE it in the hook before calling. Handle BOTH shapes so the harness is robust whether the field arrived as a `data:` URI or a plain value:

  ```python
  from _documents import document_bytes  # a copy of references/document_input.py, kept beside simulate.py

  async def invoke_pipeline(inputs: dict) -> dict:
      file_bytes, content_type = document_bytes(inputs["ticket_image"])
      data = extract_ticket(file_bytes, content_type)        # the REAL vision call
      return { ...data fields... }
  ```

  `references/document_input.py` is the ONE reader for every harness shape. `document_bytes(value)` returns `(bytes, media_type)` for a `data:` URI, raw bytes, a `file` / `image_url` content part or an older `{'content_type', 'data_base64'}` dict, and RAISES `UnreadableDocument` on anything else, so a document the harness cannot decode fails the scenario instead of reaching the agent as nothing. A binary too big to inline reaches the agent as a `data:` URI too: the SDK fetches the backend's document reference with its own bearer and folds the bytes in before the agent sees the value. A DIALOG harness takes the same documents as `file` / `image_url` parts on a user turn: `turn_documents(message_content)` lists them, and the bench declares `agent.io: {input: [text, file]}` at the root of its `plurai.config.yaml`, which the SDK requires before it will put a part on a turn (`harness_undeclared_input` otherwise). An agent that reads the files a user names, not an upload, takes them by path: `plant(documents, <scenario dir>/inputs)` writes them and the harness names each path in the message (`HANDOVER = "by_path"` in the template, Step 8).

  Match the field name + the function to the agent's actual contract (the `.document_fields` entry names the field; the agent's own code names the extraction function and its `(file_bytes, content_type)` signature). This is what makes a document agent testable from scratch end-to-end — the generated document reaches the agent's real extraction/vision path, instead of being filled with placeholder text the agent never truly parses.

**`_storage.py` (a pass-through to the seam mocks + the factory swap):**

`_storage.py` exists only when the agent's storage interface must be swapped to reach the seam its store is mocked at (Step 6.5's route (c) — a factory that hands tools a client object, a backend class the tools construct). For non-essential storage (analytics, logging, feature flags) stay with `sys.modules["..."] = MagicMock()` in Phase 1 of `simulate.py`. A store served by a real engine the harness runs, or by the agent's own in-tree implementation (routes (a) / (b)), needs no `_storage.py`: the agent's own code runs and `seed_state` seeds the store.

**The class implements no store behaviour.** Each method does exactly one of: *translate* (argument names, types, the production DTO it returns, the production exception it raises), *delegate* (to a seam call wrapped by `plurai.mock_wrap`, or to the original / in-tree implementation), or raise `plurai.HarnessFault` for a call no route serves. It holds no rows, never reads or writes `plurai.world_state` itself, and never decides an outcome from row values — validation, status transitions, naming or numbering, pricing or totals, derived reports, non-equality filtering, ordering and paging belong to the seam mock (env-gen builds it from the config; its contract tests pin it) or are a cited coverage hole. `verify_simulate.py` WARNs `harness_store_serves_world` on an installed class that touches the world itself; its storage checks (`missing_seed_state`, `storage_class_no_world_state_ref`) accept this delegating shape.

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""Phase 3: the records store, mocked at the records client seam — a pass-through + factory swap.

Side effect on import: replaces the production storage factory with one that returns a
SimRecordStorage. Must be imported BEFORE the agent module. The class holds no state:
every call goes to a seam mock, which serves the scenario's world.
"""
import plurai
import our_backend.records.factory as _records_factory
from our_backend.records.base import RecordStorage
from our_backend.records.errors import RecordConflict, RecordNotFound
# The production-typed DTOs the real tool bodies expect. Substitute your agent's actual DTOs here.
from our_backend.clients.record import (
    CreateRecordUpdateRequest,
    CreateRecordUpdateResponse,
    RecordView,
    RevisionView,
)


# The seam calls. Their bodies never run: mock_wrap routes each call to the mock of the same
# name in plurai.config.yaml (`mode: mock`, `seam: client`, `serves: [<the real tools>]`).
def records_get_record(owner_id: str, record_id: str | None = None) -> dict:
    raise plurai.HarnessFault("records_get_record reached its unmocked body")


def records_get_revision(collection_id: str, index: int | None = None) -> dict:
    raise plurai.HarnessFault("records_get_revision reached its unmocked body")


def records_create_record(owner_id: str, payload: dict) -> dict:
    raise plurai.HarnessFault("records_create_record reached its unmocked body")


_GET_RECORD = plurai.mock_wrap(records_get_record)
_GET_REVISION = plurai.mock_wrap(records_get_revision)
_CREATE_RECORD = plurai.mock_wrap(records_create_record)

# The store's own exception for each failure code the mock models — translation, not judgement.
_ERRORS = {"NOT_FOUND": RecordNotFound, "CONFLICT": RecordConflict}


def _call(seam, *args, **kwargs):
    """One seam call; a failure the mock modelled leaves as the exception the production store raises."""
    try:
        return seam(*args, **kwargs)
    except plurai.MockToolError as exc:
        content = exc.response.get("content") if isinstance(exc.response, dict) else None
        code = str(content.get("code") or "") if isinstance(content, dict) else ""
        raise _ERRORS.get(code, RecordConflict)(exc.message) from None


class SimRecordStorage(RecordStorage):
    """RecordStorage over the records seam mock.

    Subclass choice MATTERS. Pick the production class the agent's code actually does
    ``isinstance(storage, ...)`` against: an ``isinstance(storage, ApiRecordStorage)`` gate that
    activates special behavior (caching, observability, optimistic writes) silently never runs
    in simulation otherwise. Grep the agent's source for ``isinstance(.*RecordStorage`` first.
    """

    def get_record(self, owner_id: str, record_id: str | None = None) -> RecordView:
        return RecordView(**_call(_GET_RECORD, owner_id, record_id=record_id))

    def get_revision(self, collection_id: str, index=None) -> RevisionView:
        return RevisionView(**_call(_GET_REVISION, collection_id, index=index))

    def create_record(self, owner_id: str, payload: CreateRecordUpdateRequest) -> CreateRecordUpdateResponse:
        return CreateRecordUpdateResponse(**_call(_CREATE_RECORD, owner_id, payload.model_dump()))

    def delete_record(self, owner_id: str, record_id: str) -> None:
        # No tool the agent exposes reaches it: no route, so a loud fault, never a quiet success.
        raise plurai.HarnessFault(f"delete_record({owner_id}, {record_id}) has no route in this bench")


def _sim_storage_factory(*args, **kwargs):
    return SimRecordStorage()  # a fresh instance is fine — it holds no state.


_records_factory.create_record_storage = _sim_storage_factory
```

**What the seam mock owns, not this file.** HEAD pointers, revision numbering, per-revision snapshots, point-in-time reads, fields a rebuild carries forward and the store's not-found are the STORE's behaviour, so they are the seam mock's contract: write them as its `business_rules` / `post_conditions`, and its contract tests pin them (`${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/mock-contract-tests.md`, "What a versioned store's seam mock must hold"). When a tool only works if this file does one of them, the mock is wrong: route it to `/edit-env`, never into `_storage.py`.

**Fail closed.** The pass-through answers no absence itself: a not-found the mock modelled arrives as its error and leaves as the production exception; a call with no route raises `plurai.HarnessFault`. An `except: return {}` or a default in a method that returns a value fabricates a state the real store could never return — a declared "not found" branch becomes unreachable and every check downstream grades the fabrication green.

**Typed DTOs are translation.** Building the production Pydantic type from the mock's dict (and a request model's `model_dump()` on the way in) belongs here; what the typed object holds is the mock's answer, never a value this file computed.

**A module-global "reset per scenario" is dead code.** The runner spawns a FRESH child process
per scenario, so every module global is already per-scenario and re-initialising one in
`seed_state` can never execute a second time. Leaving such a reset in the file is worse than
omitting it: it reads as the fix for a drain that is actually happening WITHIN one scenario,
and the real cause keeps running underneath it.

**The same class works in Phase 1 (local) and Phase 2 (backend).** `plurai.mock_wrap` routes each seam call to the in-process `LocalMockService` by default, and to the deployed `simulation_env/` server when `--mock-service-url` points at it. The customer's `_storage.py` doesn't change.

**`_mocks.py` (descriptors + coroutine factories):**

## Mock-dispatch pattern choice (READ BEFORE writing `_mocks.py`)

`_mocks.py` ships TWO patterns for wiring `@plurai.mock` handlers into the agent's tool catalog. The choice has a **major observability consequence** the LLM-driven generation step won't get right on its own.

| Pattern | How it works | `tool_events.events` includes `tool_call` entries? | Use when |
|---|---|---|---|
| **Dispatch wrapper** (RECOMMENDED DEFAULT) — `_install_mock_coroutines` calling `plurai._mocking.resolution._resolve_mock_response` | Routes every tool call through plurai's `/mock/execute` HTTP endpoint. The mock service receives the dispatch, the handler runs, the SDK records a `tool_call` event. | ✅ YES — full visibility | **All standard agents.** This is the right default unless you have a specific reason otherwise. |
| **Coroutine swap** — `install_mock_coroutines` setting `tool.coroutine = factory()` directly | Replaces the tool's coroutine in-process. The new coroutine reads/writes `plurai.world_state` directly. No `/mock/execute` HTTP call → no `tool_call` event recorded. | ❌ NO — tool dispatches are invisible to `tool_events.events` | Only when the tool object can't be reached for HTTP-dispatch swap (closure-bound factory tools, dynamically-attached tools) AND the customer accepts losing `tool_claim_match`-style policy coverage. |

**Why this matters:** policies like `tool_claim_match`, `excessive_tool_errors`, `tool_call_recovery`, and any custom `code_eval` expression that gates on `tool_calls()` use `tool_events.events` filtered to `type == 'tool_call'`. With the coroutine-swap pattern that list is **empty** — every such policy will silently look "clean" or report nothing useful, even when the agent is fabricating or misbehaving.

**A real failure:** an agent's `simulate.py` used the coroutine-swap pattern (the SKILL's old default). On a multi-turn scenario with several tool dispatches, `tool_events.events` had 0 `tool_call` entries (only `state_update` events from world_state writes). The `tool_claim_match` policy could not detect fabrications because it had nothing to match against.

**Decision rule:** start with the dispatch wrapper. Only fall back to coroutine swap if `_find_tools(graph)` can't locate the tool objects (rare — closures and dynamically-built tool lists are the main cases).

---

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""Phase 4: @plurai.mock descriptors + coroutine factories.

Importing this module registers descriptors with plurai (decorator side
effect). Call install_mock_coroutines(graph) to swap the agent's tool
.coroutines with our mock bodies.

Mock bodies read/write plurai.world_state directly — the same world the
seam mocks _storage.py's pass-through calls serve. That's how mock tools and
mode: real tools see one coherent world.
"""
import plurai

@plurai.mock("create_record", descriptor={"description": "...", ...})
def _descriptor_only_create_record(*_a, **_kw):
    raise RuntimeError("Descriptor-only — real impl is the .coroutine swap below.")

# ... 9 more @plurai.mock decorators ...

async def _coroutine_create_record(ctx, ...):
    # Mock body reads/writes plurai.world_state directly. Inside a
    # handler, ctx.world_state uses per-thread handler state directly (no
    # HTTP round-trip); outside (e.g. mode: real tool body) the same
    # calls speak HTTP to the configured mock service. Same code path.
    head = ctx.world_state.query("record", owner_id=..., head=True)
    ...

_MOCK_COROUTINE_FACTORIES = {
    "create_record": lambda: _coroutine_create_record,
    # ...
}

def install_mock_coroutines(graph) -> None:
    """Swap each registered tool's .coroutine with our mock body.

    `_find_tools` walks the compiled LangGraph for ToolNode instances and
    returns their StructuredTool members; implement in this module
    (it's framework-specific, ~10 lines).
    """
    for tool in _find_tools(graph):  # framework-specific helper; see install_mock_coroutines docstring
        factory = _MOCK_COROUTINE_FACTORIES.get(tool.name)
        if factory is not None:
            tool.coroutine = factory()
```

**Dispatch wrapper for closure-defined tools** — when the customer's `@tool` functions live inside a factory function/closure (no module-level exposure), `apply_mocks(module, ...)` cannot find them. Use the dispatch-wrapper pattern below, which routes every tool call through `plurai._mocking.resolution._resolve_mock_response` regardless of how the agent's tool dispatch is structured. The wrapper handles FOUR non-obvious requirements you MUST get right — getting any one wrong produces a cascading silent failure that ends in `GraphRecursionError` (agent rephrases and retries empty/error responses until the LangGraph budget exhausts):

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""_mocks.py — dispatch wrapper for closure-defined @tool functions."""
import ast
import json
import logging

import plurai
from plurai._mocking.resolution import _resolve_mock_response
from langchain_core.messages import ToolMessage
from langgraph.types import Command

log = logging.getLogger(__name__)

_MOCK_TOOL_NAMES = {"list_records", "restore_record", ...}  # the tool names you registered with @plurai.mock

# REQUIREMENT 4's table: `tools.<name>.state_updates` from plurai.config.yaml, copied as declared
# (`omit:` entries left out). A tool that declares none is absent.
_MOCK_STATE_UPDATES = {
    "restore_record": {"head_index": {"from_result": "index"}, "last_error": {"value": None}},
}


def _state_update(tool_name, result):
    """The state keys the REAL tool writes on success, built from the mock's result."""
    update = {}
    for key, spec in _MOCK_STATE_UPDATES.get(tool_name, {}).items():
        if "value" in spec:
            update[key] = spec["value"]
        elif isinstance(result, dict) and spec["from_result"] in result:
            update[key] = result[spec["from_result"]]
        else:
            raise RuntimeError(
                f"mock {tool_name!r} returned no {spec['from_result']!r} field, which its declared "
                f"state update {key!r} reads — fix the mock or tools.{tool_name}.state_updates"
            )
    return update


def _install_mock_coroutines(agent_instance) -> None:
    def _make_wrapper(tool_name: str):
        async def _mock_coroutine(**kwargs):
            user_args = {
                k: v for k, v in kwargs.items()
                if k not in ("runtime", "config", "tool_call_id", "state", "store")
            }

            # REQUIREMENT 1: Unwrap LangChain's single-string ``input=`` packing.
            # Some @tool decls (``def my_tool(input: str)``) make the LLM emit
            # kwargs={"input": "{'request': '...'}"}. Without this unpack the
            # mock handler gets request="" and can't fulfill the call.
            if set(user_args.keys()) == {"input"} and isinstance(user_args["input"], str):
                parsed_ok = False
                for parser in (json.loads, ast.literal_eval):
                    try:
                        parsed = parser(user_args["input"])
                        if isinstance(parsed, dict):
                            user_args = parsed
                            parsed_ok = True
                            break
                    except (json.JSONDecodeError, ValueError, SyntaxError, TypeError):
                        continue
                if not parsed_ok:
                    # Both parsers failed. Log loudly — silently passing the raw
                    # string forward makes the downstream mock error look like
                    # the mock's fault when the real cause is upstream
                    # serialization. Customer reading their LangSmith trace
                    # sees the upstream cause first.
                    log.warning(
                        "plurai-wrapper: could not parse LangChain input=%r for tool %r; "
                        "passing raw kwargs to mock handler.",
                        user_args["input"][:200], tool_name,
                    )

            tool_call_id = "plurai-mock"
            if "runtime" in kwargs and getattr(kwargs["runtime"], "tool_call_id", None):
                tool_call_id = kwargs["runtime"].tool_call_id

            result = _resolve_mock_response(tool_name, user_args, return_type=dict)

            # REQUIREMENT 2: A FAILED call fails — surface it as ToolMessage(status='error').
            # WHETHER it failed is `plurai.mock_result`'s to decide (`_error_message` below):
            # the SDK's error stub, a handler's ``{"status": "error", "message": ...}``, a
            # handler that raised, the engine's refusal. Never re-read status tokens here.
            # This branch decides only HOW the agent sees the failure: the error ToolMessage
            # is how a tool that RAISES fails (`tools.<name>.error_channel: raises`). For a
            # tool declared `error_channel: returns`, render the failure VALUE its real tool
            # returns through `_render_content` instead.
            #
            # Rendering a failure as ``status='error'`` tells LangChain's React loop to stop
            # rephrase-and-retry. Without it the agent loops to GraphRecursionError.
            err_msg = _error_message(result)
            if err_msg is not None:
                return Command(update={"messages": [ToolMessage(
                    content=_render_error_content(tool_name, err_msg),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    status="error",
                )]})

            # REQUIREMENT 3: Render the result's `content` — the real tool's return, and
            # the ONE field that reaches the model (`_tool_content`) — through the agent's
            # OWN envelope. Never pick any other field: the old anti-pattern
            # ``result.get("message") or str(result)`` rendered ``{}`` as ``'{}'``
            # on empty success ("did it work?"). `_render_content` below is the
            # only place the payload shape is decided, so a mocked tool and a real
            # one hand the model the same shape.
            content = _render_content(result)

            # REQUIREMENT 4: Land the REAL tool's success-path state writes. A real tool that
            # returns `Command(update={"<flag>": True, ...})` gates later turns on those
            # keys; a messages-only mock drops them, and the suite scores turns production
            # never reaches.
            return Command(update={**_state_update(tool_name, result), "messages": [ToolMessage(
                content=content, tool_call_id=tool_call_id, name=tool_name,
            )]})

        _mock_coroutine.__name__ = f"mock_{tool_name}"
        return _mock_coroutine

    for tool in agent_instance.get_tools():
        if tool.name in _MOCK_TOOL_NAMES:
            tool.coroutine = _make_wrapper(tool.name)
```

**`_render_content` / `_render_error_content` / `_error_message` — the tool-content envelope.**
Generated together from `agent.tool_content_envelope` in `plurai.config.yaml` (captured at Step 6 of
the `simulation-setup` skill, modelled on the claude-skill path's
`return_envelope` — `${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/references/code_surface_sdk_shape.yaml`). They
exist because a mocked tool that returns raw JSON into an agent whose REAL tools return
`{"status", "content"}` hands the model a payload its own tools never produce — and the
agent is then measured against a contract it does not have. Emit all three for the declared
kind: they share one description of the envelope, and a wrapper that renders success through
the agent's shape and errors through a bare string is back to two contracts.

**None of the three may swallow its input silently.** A broad `except` (or a
`contextlib.suppress(Exception)`) that then returns `None`, `''`, `{}`, `[]` — or nothing at
all — turns a malformed result into a well-formed NOTHING: the agent gets an empty render,
every downstream verdict grades that silence, and no artifact records that the input was
broken. Let it RAISE — a harness fault must read as a harness fault — or return a diagnostic
the render carries, or at minimum say out loud what you fell back to; SILENT is the defect,
and a narrow `except` for the error you actually expect is always fine. The acceptance tier's
`test_no_message_shaping_helper_swallows_its_input` reads these functions' source for it.

When the agent declares no envelope — `content` rendered the way the agent's ToolNode renders a real return (a str verbatim, anything else as JSON):

```python
# No `agent.tool_content_envelope` in plurai.config.yaml → the default: `content` rendered the
# way the agent's ToolNode renders a real return.


def _tool_content(result):
    # ONLY `content` reaches the model: it is the real tool's return. `plurai.mock_result` is the
    # SDK's ONE reader of the mock envelope; `status` and every sibling (state keys, bookkeeping)
    # stay with the wrapper — `_state_update`, graders. A failed call never reaches here
    # (`_error_message` first).
    return plurai.mock_result(result)


def _render_content(result):
    result = _tool_content(result)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)

def _render_error_content(tool_name, err_msg):
    # The diagnostic IS the content — the ToolMessage carries `name` and `status` already.
    # Coerced to text because `ToolMessage.content` is typed `str | list`: a handler whose
    # `_plurai_stub_error` is a dict would otherwise raise a pydantic ValidationError inside
    # the wrapper and take the turn down, which is a harness fault wearing an agent's face.
    return err_msg if isinstance(err_msg, str) else json.dumps(err_msg, ensure_ascii=False, default=str)

def _error_message(result):
    # Whether the call FAILED is the SDK's to decide — `plurai.mock_result` executes the mock
    # envelope's contract (`_plurai_stub_error` stubs, `status: "error"`, a handler that raised, the
    # engine's refusal, a non-standard status token beside `content`) — never a status-token guess
    # here. Returns the failure's own words (a structured diagnostic as JSON), or None.
    try:
        plurai.mock_result(result)
    except plurai.MockToolError as e:
        return e.message
    return None
```

```python
# `kind: json_field` with `template: {status: success, content: null}` and
# `error_template: {status: error, content: null}` — the agent's own convention. The result's
# `content` is placed at the template's `null` as-is; nothing is dropped or reshaped.
_ENVELOPE_TEMPLATE = {"status": "success", "content": None}
_ERROR_TEMPLATE = {"status": "error", "content": None}
# The ONE key the template marks with `null` — where the payload goes, and where an
# already-enveloped result keeps its own.
_PAYLOAD_FIELD = next(k for k, v in _ENVELOPE_TEMPLATE.items() if v is None)


def _tool_content(result):
    # ONLY `content` reaches the model: it is the real tool's return. `plurai.mock_result` is the
    # SDK's ONE reader of the mock envelope; `status` and every sibling (state keys, bookkeeping)
    # stay with the wrapper — `_state_update`, graders. A failed call never reaches here
    # (`_error_message` first).
    return plurai.mock_result(result)


def _already_enveloped(result):
    return isinstance(result, dict) and set(_ENVELOPE_TEMPLATE) <= set(result)


def _render_content(result):
    # IDEMPOTENT. A handler written against the agent's REAL tool contract returns the
    # envelope itself; wrapping unconditionally then gives the model
    # {"status": ..., "content": {"status": ..., "content": {...}}} — a shape neither the
    # agent's tools nor its parser produce, in a run that looks green.
    # A failure the real tool reports through its VALUE (`{"status": "error", "content": {...}}`,
    # which `plurai.mock_result` returns rather than raises) is its error envelope, not a success.
    template = _ERROR_TEMPLATE if isinstance(result, dict) and result.get("status") == "error" else _ENVELOPE_TEMPLATE
    result = _tool_content(result)
    if _already_enveloped(result):
        return json.dumps(result, ensure_ascii=False, default=str)
    return json.dumps({k: (result if v is None else v) for k, v in template.items()}, ensure_ascii=False, default=str)


def _render_error_content(tool_name, err_msg):
    # The diagnostic IS the content. The ToolMessage already carries `name=tool_name` and
    # `status="error"`, so a "Tool 'x' failed:" prefix restates the two fields the model is
    # given structurally and pushes the store's own wording out of the first line it reads.
    return json.dumps({k: (err_msg if v is None else v) for k, v in _ERROR_TEMPLATE.items()}, ensure_ascii=False, default=str)


def _error_message(result):
    # Whether the call FAILED is the SDK's to decide — `plurai.mock_result` executes the mock
    # envelope's contract (`_plurai_stub_error` stubs, `status: "error"`, a handler that raised, the
    # engine's refusal, a non-standard status token beside `content`) — never a status-token guess
    # here. Returns the failure's own words (a structured diagnostic as JSON), or None.
    try:
        plurai.mock_result(result)
    except plurai.MockToolError as e:
        return e.message
    return None
```

```python
# `kind: text` — the agent's tools return a plain string. Identical to the default render;
# the declaration is what makes it a statement about the agent rather than a fallback.


def _tool_content(result):
    # ONLY `content` reaches the model: it is the real tool's return. `plurai.mock_result` is the
    # SDK's ONE reader of the mock envelope; `status` and every sibling (state keys, bookkeeping)
    # stay with the wrapper — `_state_update`, graders. A failed call never reaches here
    # (`_error_message` first).
    return plurai.mock_result(result)


def _render_content(result):
    result = _tool_content(result)
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)

def _render_error_content(tool_name, err_msg):
    # The diagnostic IS the content — the ToolMessage carries `name` and `status` already.
    # Coerced to text because `ToolMessage.content` is typed `str | list`: a handler whose
    # `_plurai_stub_error` is a dict would otherwise raise a pydantic ValidationError inside
    # the wrapper and take the turn down, which is a harness fault wearing an agent's face.
    return err_msg if isinstance(err_msg, str) else json.dumps(err_msg, ensure_ascii=False, default=str)

def _error_message(result):
    # Whether the call FAILED is the SDK's to decide — `plurai.mock_result` executes the mock
    # envelope's contract (`_plurai_stub_error` stubs, `status: "error"`, a handler that raised, the
    # engine's refusal, a non-standard status token beside `content`) — never a status-token guess
    # here. Returns the failure's own words (a structured diagnostic as JSON), or None.
    try:
        plurai.mock_result(result)
    except plurai.MockToolError as e:
        return e.message
    return None
```

Whatever the kind, the envelope WRAPS; the only field it selects is `content`, through
`_tool_content`. Rendering any other field of `result` through it is the same defect as the
anti-pattern above, one layer out. And it wraps ONCE:
the same `_already_enveloped` read is what makes the wrap idempotent. A value the handler put
inside the agent's own envelope is the tool's RETURN, rendered as the real tool returns it.

The four requirements (`input=` unwrap + parse-failure logging; a failed call fails, as `plurai.mock_result` reads the envelope — no status tokens restated here; `content` rendered through the declared envelope; the real tool's success-path state writes via `_state_update`) are NOT optional — together they form the load-bearing error-propagation contract from the env-gen mock through the SDK to the agent's React loop. The verifier `wrapper_drops_response_fields` check in Step 9e flags the most common anti-pattern (`result.get("message") or str(result)`); run it with `--tool-content-envelope <kind>` when the config declares one, and it also checks that the wrapper actually routes through the envelope rather than hard-coding `json.dumps` (`wrapper_ignores_tool_content_envelope`) and that a `json_field` wrap is guarded (`envelope_render_not_idempotent`). A tool whose `plurai.config.yaml` entry declares `state_updates:` must land them: a messages-only success return, or a `_MOCK_STATE_UPDATES` entry missing a declared key, fails `wrapper_drops_state_updates`.

**A Python harness over a Node agent (a bridge)** follows the bridge contract of the `simulation-setup-bridge` skill (run that skill for it).

**Verifier compatibility.** When `_storage.py` / `_mocks.py` exist alongside `simulate.py`, the verifiers auto-discover them:

- `verify_config.py`: `_mocks.py` is included in the `@plurai.mock` decorator scan (tool-name consistency); `_storage.py` is included in the storage-monkey-patch scan (the essential-storage check, which applies only to a legacy real reader labelled `read_strategy: mocked`).
- `verify_simulate.py`: `_storage.py` is included in the storage-factory-assignment scan (triggers `missing_seed_state` if storage is patched without a seed_state — unless the patched class delegates to a declared mock over a mock-owned world — and `storage_class_no_world_state_ref` if no class reaches the mock world, by delegating to a declared mock or otherwise), and in `harness_store_serves_world` (a class installed over agent code that reads or writes `plurai.world_state` itself); `_mocks.py` is included in the tool-wrapping scan.
- Both verifiers parse each file independently. A SyntaxError or unreadable helper produces a single `helper_unparseable` / `helper_unreadable` warn finding (with the offending filename), NOT a cascade of false-positive fails against simulate.py.
- A near-miss filename (`mocks.py` without underscore, `storage.py` without underscore) when the canonical name is absent produces a `helper_naming_convention_drift` warn — telling the customer to rename.

Use these exact filenames (`_storage.py`, `_mocks.py`) or the verifier emits the drift warning and skips the helper. The verifiers do NOT depend on the package name.
