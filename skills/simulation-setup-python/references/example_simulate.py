# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""
Plurai Simulation Setup — Example
Shows all three modes: mock, real, observe.
Inline mocks are DUMB. Descriptors are RICH. Observers capture real results.
"""
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock

# ================================================================
# PHASE 1: STUBS — prevent import crashes
# ================================================================
sys.modules["our_backend.db"] = MagicMock()        # STUB: SQLAlchemy session
sys.modules["our_backend.redis"] = MagicMock()      # STUB: Redis client
sys.modules["our_backend.services"] = MagicMock()   # STUB: Stripe, Twilio
os.environ.setdefault("STRIPE_KEY", "plurai-stub")

# Seed os.environ from the repo .env. REQUIRED, not optional: the runner launches this
# file in a child subprocess that imports simulate.py and nothing else, so anything
# configured only in .env (DEFAULT_LLM__*, provider keys) is invisible to the agent and
# it silently falls back to its code defaults WHILE THE RUN STILL PASSES. Harmless when
# there is no .env, and never overrides a variable already exported. When the bench
# declares `harness.env_file` (a file the user named), load that path instead:
# `load_dotenv(Path(__file__).resolve().parent.parent / '<harness.env_file>')`.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # LOUD on purpose: a bare `pass` here silently recreates the exact failure the comment
    # above warns about — python-dotenv missing means every .env-only setting
    # (DEFAULT_LLM__*, provider keys) is invisible and the agent runs on its code defaults
    # while the run still passes. Exporting the vars in the environment is the legitimate
    # no-dotenv setup; then this warning is your confirmation, not an error.
    print(
        'WARNING(simulate.py): python-dotenv is not installed — .env was NOT loaded. '
        'If your model/provider config lives only in .env, the agent is now running on '
        'its code defaults. Install python-dotenv or export the variables explicitly.',
        file=sys.stderr,
    )

# ================================================================
# PHASE 1b: STORAGE SWAP for a `mode: real` tool over a faked store
# ================================================================
# A real tool reaches a faked store ONLY through a seam mock that names it in
# `serves:` (plurai.config.yaml: `mode: mock`, `seam: client | entity`,
# `serves: [<the real tools>]`, `serves_cite`). Where the agent's storage must be
# swapped to reach that seam (a factory, a backend class), install a PASS-THROUGH:
# each method calls the seam through `plurai.mock_wrap` and translates (argument
# names, the production DTO, the production exception) — nothing else. It never
# reads or writes plurai world-state itself and never decides an outcome from row
# values: what the store does (validation, numbering, history) is the seam mock's
# contract. A store served by a real engine the harness runs, or by the agent's
# own in-tree implementation, needs no swap at all.
#
# Position matters: this MUST run BEFORE Phase 2 (agent import). Tool
# modules that bind their factory at import time (`session = create()`)
# need the factory patched first; otherwise `from X import session`
# captures the unpatched reference.
#
# Pseudocode — uncomment + edit the imports to match the customer's repo.
# If no real tool reaches a faked store, omit this section entirely.
#
# def audit_log_append(entry: dict) -> dict:          # the seam call; its body never runs
#     raise plurai.HarnessFault("audit_log_append reached its unmocked body")
# _AUDIT_APPEND = plurai.mock_wrap(audit_log_append)
# class SimAuditLogStorage(our_backend.services.audit_log.AuditLogStorage):
#     def append(self, entry):
#         return AuditLogRecord(**_AUDIT_APPEND(entry.model_dump()))
# import our_backend.services.audit_log_factory as _audit_factory
# _audit_factory.create_audit_log_storage = lambda *a, **kw: SimAuditLogStorage()
#
# Coherence guarantee: the seam mock writes to the SAME plurai world-state
# that @plurai.mock handlers below read from. A `mode: mock` tool calling
# ctx.world_state.query("audit_log") sees what the real tool just wrote
# through the seam; cross-tool state is consistent by construction.

# ================================================================
# PHASE 1c: PER-CHILD PORTS AND ACCOUNTS (only if the agent listens or the harness keys a store)
# ================================================================
# Sibling children run on one host, so a port the agent binds at a fixed or
# production default (a framework component's default counts) fails every
# sibling's Init with "address already in use". Hand the agent its own port
# through the config key, env var or argument it reads: port 0 where it
# accepts that, else the free loopback port below, picked in this child.
# Omit this section when nothing in the agent listens and no store is keyed.
import socket


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# os.environ["<YOUR_AGENT_PORT_ENV>"] = str(free_loopback_port())
# or, in the config the runner hands the agent: {"<component>": {"<port_key>": free_loopback_port()}}
#
# When a runner you may not edit builds that config INLINE, wrap the call it
# makes and set the port key on the way through; the runner's file is untouched:
#
# import <runner_module> as _runner
# _runner_setup = _runner.<setup_call>
#
# async def _setup_own_port(*args, **kwargs):
#     # put {"<port_key>": free_loopback_port()} into the config argument for the listening component
#     return await _runner_setup(*args, **kwargs)
#
# _runner.<setup_call> = _setup_own_port
#
# A store the agent reads under one account (a mailbox, a tenant, a user):
# seed_state creates the account per conversation and points the agent at it,
# and reset / world_snapshot touch only that account's rows:
#
# ACCOUNT_ID = f"sim-{plurai.active_session_id()}"   # inside seed_state, after Init

# ================================================================
# PHASE 2: Import the agent
# ================================================================
from our_backend.agents.billing import graph, AgentState
from our_backend.agents.billing import (
    get_user_profile, process_refund, get_transactions,
    rag_search, send_notification, llm_node, should_use_tools
)

# ================================================================
# PHASE 3: Replace context-loading nodes
# ================================================================
import plurai

def sim_fetch_context(state):
    ctx = plurai.get_simulation_context()
    state["user_id"] = ctx.agent_context.get("user_id")
    state["user_profile"] = ctx.agent_context.get("user_profile", {})
    state["permissions"] = ctx.agent_context.get("permissions", [])
    return state

# ================================================================
# PHASE 4: MOCKS + OBSERVERS
# ================================================================
# Inline mocks: DUMB — just WorldState lookup
# Descriptors: RICH — backend uses these to generate real mocks
# Observers: capture real tool results into WorldState
#
# Three canonical ways to read scenario state inside a handler:
#   1. ctx.world_state.get_entity(entity_id)            # single entity
#   2. ctx.world_state.query("type", **filters)         # filtered list
#   3. plurai.world_state_snapshot()                    # full dict
#
# Examples of all three appear below: get_user_profile uses #1,
# get_transactions uses #2, dump_state uses #3.
#
# NEVER write isinstance(ctx.world_state.entities, dict) — always False;
# the verifier rejects it. The proxy is a Mapping, not a dict subclass.
#
# Every parameter has a default: a call carries only the arguments the agent passed.
# A failure the real tool reports is RETURNED, never raised:
#     return {"status": "error", "error": "<the real tool's message>"}
# A tool declared `error_channel: raises` throws that message to the agent; any other
# tool gets the failed call as its wrapper reports one (`mock_wrap` hands back the
# dict, or `failure(e)` when one is given). A raise from a mock body (MockToolError
# included) is a handler crash: the agent never gets the body's message.

# --- MOCK: get_user_profile ---
@plurai.mock("get_user_profile", descriptor={
    "description": "Fetch user profile from production Postgres",
    "original_module": "our_backend.db.queries",
    "reads_from": "Postgres users table joined with accounts",
    "writes_to": None,
    "stateful": False,
    "args": {
        "user_id": {"type": "str", "description": "UUID of the user"},
    },
    "returns": {
        "type": "dict",
        "fields": {
            "id": {"type": "str"},
            "name": {"type": "str"},
            "plan": {"type": "str", "enum": ["free", "pro", "enterprise"]},
            "balance": {"type": "float", "min": 0},
        },
    },
    "agent_usage": "Agent reads name, plan, balance to determine eligibility",
    "depends_on": [],
    "depended_by": ["process_refund"],
})
def mock_get_user_profile(user_id=None):
    # DUMB: just return entity as-is; a miss is the real tool's own failure, returned
    ctx = plurai.get_simulation_context()
    return ctx.world_state.get_entity(user_id) or {"status": "error", "error": f"user {user_id} not found"}


# --- MOCK: process_refund ---
@plurai.mock("process_refund", descriptor={
    "description": "Process refund via Stripe + update Postgres balance",
    "original_module": "our_backend.services.payments",
    "reads_from": "Stripe API + Postgres",
    "writes_to": "Stripe (refund) + Postgres (balance, transaction)",
    "stateful": True,
    "args": {
        "user_id": {"type": "str"},
        "amount": {"type": "float"},
    },
    "returns": {
        "type": "dict",
        "fields": {
            "status": {"type": "str", "enum": ["approved", "rejected"]},
            "new_balance": {"type": "float"},
        },
    },
    "business_rules": [
        "Balance must be >= refund amount",
        "Premium users: full refund up to $500",
        "Free users: store credit only",
    ],
    "state_transitions": {
        "user.balance": "decremented by amount",
        "creates": "transaction with type=refund",
    },
    "agent_usage": "Agent checks status, shows new_balance to user",
    "depends_on": ["get_user_profile"],
    "depended_by": ["get_transactions"],
})
def mock_process_refund(user_id=None, amount=None):
    # DUMB: minimal state update
    ctx = plurai.get_simulation_context()
    user = ctx.world_state.get_entity(user_id)
    if not user or amount is None:
        return {"status": "rejected"}
    # Runs inside the agent's tool call: never wait for its runtime to go idle here (sibling calls deadlock); yield with `await asyncio.sleep(0)`.
    ctx.world_state.apply({user_id: {"balance": user.get("balance", 0) - amount}})
    return {"status": "approved", "new_balance": user.get("balance", 0) - amount}


# --- MOCK: get_transactions ---
@plurai.mock("get_transactions", descriptor={
    "description": "Get transaction history from Postgres",
    "original_module": "our_backend.db.queries",
    "reads_from": "Postgres transactions table",
    "writes_to": None,
    "stateful": False,
    "args": {"user_id": {"type": "str"}},
    "returns": {"type": "list[dict]"},
    "depends_on": ["process_refund"],
    "depended_by": [],
})
def mock_get_transactions(user_id=None):
    # DUMB: query WorldState
    ctx = plurai.get_simulation_context()
    return ctx.world_state.query("transaction", user_id=user_id) or []


# --- MOCK: dump_state ---
# Demonstrates the third canonical read pattern — bulk dict snapshot.
# Use this when a tool genuinely needs every entity at once (e.g., an
# "export everything" or "audit summary" tool). For typed scans, prefer
# .query("type") — one HTTP call AND server-side filtering.
@plurai.mock("dump_state", descriptor={
    "description": "Return a structural summary of every entity (debug/export tool)",
    "original_module": "our_backend.debug",
    "reads_from": "Full state dump — every entity",
    "writes_to": None,
    "stateful": False,
    "args": {},
    "returns": {
        "type": "dict",
        "fields": {
            "entity_count": {"type": "int"},
            "by_type": {"type": "dict", "description": "{entity_type: count}"},
        },
    },
    "agent_usage": "Agent reports totals to the user",
    "depends_on": [],
    "depended_by": [],
})
def mock_dump_state():
    # DUMB: read the full snapshot, count by entity_type. One HTTP call.
    snapshot = plurai.world_state_snapshot()
    by_type: dict[str, int] = {}
    for entity in snapshot.values():
        etype = entity.get("_entity_type", "unknown")
        by_type[etype] = by_type.get(etype, 0) + 1
    return {"entity_count": len(snapshot), "by_type": by_type}


# --- MOCK: send_notification ---
@plurai.mock("send_notification", descriptor={
    "description": "Send notification via Twilio SMS",
    "original_module": "our_backend.services.twilio",
    "reads_from": None,
    "writes_to": "Twilio API (sends SMS)",
    "stateful": False,
    "args": {"user_id": {"type": "str"}, "message": {"type": "str"}},
    "returns": {"type": "dict", "fields": {"status": {"type": "str"}}},
    "agent_usage": "Agent just checks status == 'sent'",
    "depends_on": [],
    "depended_by": [],
})
def mock_send_notification(user_id=None, message=None):
    # DUMB: always succeed
    return {"status": "sent"}


# --- OBSERVE: rag_search ---
# Real execution — hits the actual RAG index.
# Observer captures results into WorldState so downstream mocks can read them.
@plurai.observe("rag_search")
def observe_rag_search(args, result):
    """Capture real search results into WorldState."""
    ctx = plurai.get_simulation_context()
    if isinstance(result, list):
        for i, doc in enumerate(result):
            ctx.world_state.entities[f"search_result_{i}"] = {
                "_entity_type": "search_result",
                "query": args.get("query", ""),
                "title": doc.get("title", ""),
                "content": doc.get("content", ""),
            }


# ================================================================
# PHASE 5: Rebuild graph and compile
# ================================================================
plurai_tool_node = plurai.MockToolNode(
    tools=[get_user_profile, process_refund, get_transactions,
           rag_search, send_notification],
    mock_config={
        "get_user_profile": "mock",
        "process_refund": "mock",
        "get_transactions": "mock",
        "send_notification": "mock",
        "rag_search": "observe",       # Real + capture results to WorldState
        # "audit_log_lookup": "real",  # Example: real tool body over the audit-log seam mock (Phase 1b)
    }
)


from langgraph.graph import StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

graph2 = StateGraph(AgentState)
graph2.add_node("fetch_context", sim_fetch_context)   # Replaced
graph2.add_node("llm", llm_node)                      # Original
graph2.add_node("tools", plurai_tool_node)             # Replaced
graph2.set_entry_point("fetch_context")
graph2.add_edge("fetch_context", "llm")
graph2.add_conditional_edges("llm", should_use_tools,
    {"tools": "tools", "__end__": "__end__"})
graph2.add_edge("tools", "llm")
app = graph2.compile(checkpointer=MemorySaver())

# ================================================================
# PHASE 6: Adapter plurai-run-simulation imports
# ================================================================
# Note: this example has 7 phases (an extra "rebuild graph" step at Phase 5);
# SKILL.md numbers them 1-6 with seed_state at "5b". The same logical steps
# in either order — just one phase off. If you cross-reference SKILL.md, map:
#   SKILL.md Phase 5 (Adapter)        ↔ this file's PHASE 6
#   SKILL.md Phase 5b (seed_state)    ↔ this file's PHASE 6b
#   SKILL.md Phase 6 (Driver loop)    ↔ this file's PHASE 7
# ============ THE PAYLOAD CONTRACT (read before writing the adapter) ====================
# Per turn, the SDK child sends payload = {'messages': [ONE new HumanMessage]} — a
# ONE message holding only the NEW user side, NEVER the conversation so far — and `[]` on the
# opening call at a bench declaring `agent.io: {may_open: true}`, so never index [-1] blind.
# On a turn carrying an attachment it is still one message, whose `content` is a parts list.
# DOCUMENTS the user hands over arrive in that list, in ONE shape on whichever turn she hands one
# over (the opener included): a `file` part `{'filename': '<field>.<ext>', 'file_data': 'data:<mime>;base64,<...>'}`
# (`file.file_data` carries the URI; an `image_url` part carries `image_url.url`). The filename keeps the
# document's own extension and the URI its media type, for every format; a served document too large to
# inline is folded to that `data:` string by the SDK first. It is never an `agent_context` key and never a
# `seed_state` value. Decode every turn through `_documents.py`, a copy of `references/document_input.py`
# (see `_handed_documents` below). The config declares the part kind at the root:
# `agent.io: {input: [text, file]}`. Without it the SDK fails the scenario as `harness_undeclared_input`
# (`verify_config.py`: `handover_kind_not_declared`); a harness that reads a `.document_fields` field off the
# payload or `seed_state` is refused too (`verify_simulate.py`: `document_read_off_payload`).
# **agent_context is spread into the payload on TURN 1 ONLY. History is YOUR harness's
# job: persist it keyed on config['configurable']['thread_id'] (stable per scenario) —
# a LangGraph checkpointer does this natively; a framework-free harness must keep its
# own thread-keyed store. A harness that rebuilds history from payload['messages']
# runs STATELESS — every turn sees [system, user] — and NOTHING flags it: the run
# completes, and every memory/journey/continuity verdict is meaningless (observed
# live for a full qualification pass). Source of truth: plurai/child.py
# handle_user_turn. Most LangGraph agents accept this shape directly (compile with a
# checkpointer!); otherwise translate here.
# CHANNELS (`agent.io.channels`, Step 8): a planted input arrives on its turn under the channel's `field` (a file
# item's id or id list, a context value) and as typed records in payload['channel_inputs']; a host-injected message
# comes BEFORE the user's message in payload['messages']; a between-turn action, and a file item's write, reach a
# harness-owned world through `apply_user_action` (`surface` = the channel name, `action` = its field).
# The turn that answers an interrupt carries payload['interrupt_choice']: one of the `choices`
# you offered, verbatim. Map THAT to your resume value and never parse the words. Absent means
# the turn answers no interrupt (or the backend predates the field). Name the choice that RUNS
# the paused actions as the interrupt's `approve` (APPROVE_CHOICE below): any other answer then
# reads as a DENIED call, never as one that executed.
# =========================================================================================
#
# Sync vs async signature — the SDK's documented contract is sync-OR-async:
# the orchestrator detects awaitables via inspect.isawaitable and awaits only
# those, so pick the one matching your graph:
#   Sync (graph.invoke):   def invoke_agent(...): return app.invoke(...)
#   Async (graph.ainvoke): async def invoke_agent(...): return await app.ainvoke(...)
# Don't decide this from a version number — decide it from BEHAVIOUR, which the
# smoke test already measures. A pre-fix SDK build awaits invoke_agent
# UNCONDITIONALLY; a sync `def` against one dies on turn 1 with an
# object-not-awaitable TypeError, and verify_smoke.py reports exactly that
# ("invoke_agent's return was awaited but is not awaitable"). If you see it:
# make invoke_agent `async def`, or update the plurai package. (The pin this
# plugin installs, v0.3.0, IS a pre-fix build — the isawaitable branches landed
# after that tag and no later tag exists yet — so on the pinned SDK write the
# async form.)
#
# Per-call LLM timeout: the orchestrator kills a turn at its own budget
# (default 120s) and fails the SCENARIO. Give the model client an explicit
# request timeout + bounded retries well under that budget so a stalled
# provider call fails fast and legibly instead of eating the whole turn:
#   ChatAnthropic(..., timeout=60, max_retries=1)  # or the provider's equivalent
#
# Never wrap with asyncio.run(...) — the orchestrator already drives an event
# loop; nested asyncio.run raises on turn 1.
#
# Pass the run config your production entry passes, step cap included — never raise or
# add one (e.g. LangGraph's `recursion_limit`): the cap is per request, so a turn that hits
# it here hits it in production.
#
# THE RETURN CONTRACT — what `invoke_agent` owes the runner, and what it must never do
# -------------------------------------------------------------------------------------
# Return the agent's OWN output, unaltered:
#
#   * the agent state dict with the message list under `messages` (LangGraph's result), or
#   * a plain string, or
#   * a message list under `messages` in whatever message shape your framework produces —
#     LangChain objects, OpenAI role dicts, Anthropic content blocks. The SDK reads all of
#     them through the ecosystem's own converter; you do not normalise anything here. The list
#     is the full history, or only THIS turn's messages (`_RETURNS_TURN_ONLY`) taken from the
#     turn's own stream events or by message id — never by a message count read before the
#     turn (summarization or trimming can shrink the history mid-turn).
#
# A turn is COMPLETE when the agent either SPOKE or ACTED. An agent whose answer is a render
# verb, a navigation or a hand-off legitimately produces `content: ""` plus `tool_calls` — that
# is a real turn, and the SDK carries it as `agent_actions` beside an empty reply.
#
# THE CHAIN CARRIES THE MODEL CALLS. Trace sinks (LangSmith, Langfuse) draw one model call per assistant
# message of the `messages` you return, and show the model name and token usage that message carries. A
# LangGraph / LangChain result already does: its `AIMessage` objects hold `tool_calls`, `response_metadata`
# and `usage_metadata`, so return the result as it is and never rebuild the messages. A framework whose
# native turn is another shape (Strands, a hand-written OpenAI / Anthropic loop, the OpenAI Agents SDK)
# returns the turn's whole chain in the SDK's spelling: each assistant message with its tool calls, each
# tool result, the final reply, and per assistant message the model and usage the framework reports.
# Copy `references/model_calls.py` beside this file as `_model_calls.py` and build the messages through it
# (`strands_chain`, `openai_chat_message`, `anthropic_message`, `openai_agents_chain`; framework-patterns.md
# has one worked turn per framework). A value the framework does not report stays out of the message: never
# a configured default, an estimate or the model name read from an env var. Returning only
# `[{"role": "assistant", "content": reply}]` shows the turn as one call with no model and no usage
# (`verify_simulate` warns: `assistant_message_without_model_metadata`).
#
# NEVER paint actions into the reply text, and never emit a placeholder sentence for a turn the
# agent left silent. Both manufacture words the agent did not say: the simulated user then
# answers a message that was never sent, and every judge grades the harness's prose as the
# agent's. If a render-only turn is reaching the backend as an empty reply, the fix is in the
# SDK's projection (`agent_actions`) or in what you return here — not in a sentence
# you invent. `verify_smoke.py --turn` drives a render-only turn whenever plurai.config.yaml
# declares `render_actions`, so this is checked at setup rather than discovered in a suite.
#
# Also: a subprocess YOUR harness spawns is outside the SDK's observability. The SDK promotes
# the CHILD's stderr only, so send anything you need to see from a spawned server to a file you
# control (or to this process's stderr) — a `DEVNULL` there is a harness decision, not an SDK
# limitation, and diagnosing it as one sends a real defect to the wrong repo. Concretely:
#
#     _LOG_PATH = Path(__file__).parent / "artifacts" / "agent_server.log"
#     _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)   # gitignored, and not there on a fresh clone
#     _LOG = _LOG_PATH.open("a", buffering=1)
#     proc = subprocess.Popen([node, SERVER], stdout=_LOG, stderr=subprocess.STDOUT)  # ONE handle
#
# and when a turn comes back non-2xx, raise with that file's tail attached:
#
#     raise RuntimeError(f"{method} {path} -> {code}: {body[:600]}\n--- agent server log ---\n{_tail(_LOG_PATH)}")
#
# The SDK persists that MESSAGE verbatim into the scenario's error.json — which is why the status,
# the body and the URL have to be IN it; they are captured as separate fields only for an
# exception that carries an httpx-shaped `.response`/`.request`, which a RuntimeError does not.
# That record is all anyone gets afterwards for a mid-dialog 500. `stdout` must be redirected
# whatever you do with it: simulate.py's own stdout is the SDK's IPC channel, so a server that
# inherits it writes its log into the protocol and the scenario dies on the first line. Never a
# bare `PIPE` nobody keeps draining either: the log is lost after the boot wait AND the server
# blocks on its next write once the buffer fills — a hang that reads as a stuck agent. If your
# harness ATTACHES to an already-running server instead of spawning one, say so in that same
# message: the server's log is external and nobody should go looking for it here.

# The offered choice that RUNS the paused actions — your agent's own token, verbatim.
APPROVE_CHOICE = "approve"

# How production hands this agent a user's file (Step 8), one of `_HANDOVERS`: `content_part` when it
# has an upload channel, `by_path` when it takes text and reads the files a user names from disk.
HANDOVER = "{content_part|by_path}"
# content_part: the agent-state / payload field the production agent reads an upload from — fill the
# {placeholder} from Step 1–7 (a vision-content builder, an `attachments` argument).
ATTACHMENTS_FIELD = "{attachments_field}"
# by_path: the scenario's own folder, inside the area the agent may read (the `run_dir` given to
# `plurai.confine`); handed-over files land in its `inputs/`.
SCENARIO_DIR = Path("{scenario_dir}")


# A declared channel's `field` -> the agent's own request key, only where the two differ (`CHANNEL_FIELDS` below).
CHANNEL_FIELDS = {}


def _with_channels(payload):
    """The planted channel values under the request keys the production agent reads them from."""
    moved = {CHANNEL_FIELDS[key]: payload[key] for key in CHANNEL_FIELDS if key in payload}
    return {**{k: v for k, v in payload.items() if k not in CHANNEL_FIELDS}, **moved}


def _turn_content(payload):
    messages = payload.get("messages") or []
    message = messages[-1] if messages else None
    return message.get("content") if isinstance(message, dict) else getattr(message, "content", None)


def _handed_documents(payload):
    """The documents the user handed over on THIS turn, decoded to bytes.

    Raises `UnreadableDocument` on a part it cannot decode: a scenario written around an upload that
    silently receives none grades the agent on a question it was never shown. Each `Document` keeps the
    name and media type it was handed over with (`uploads.md`, `text/markdown`); hand the agent exactly that.
    A pipeline input is a bare `data:` string, read with `document_bytes(inputs["invoice"])` -> `(bytes, media_type)`.
    """
    # `_documents.py` is a copy of references/document_input.py made beside this file (Step 8); it does
    # not exist until then, so it is imported where it is used.
    from _documents import turn_documents

    return turn_documents(_turn_content(payload))


def _as_attachments(payload, documents):
    return {**payload, ATTACHMENTS_FIELD: documents}


def _by_path(payload, documents):
    # The files go where the agent can read them, and the message names each path the way this agent's
    # users name a file (its prompt or docs say how); the user's words stay as they were.
    from _documents import plant, turn_text

    named = "\n".join(f"[attached file: {path}]" for path in plant(documents, SCENARIO_DIR / "inputs"))
    said = turn_text(_turn_content(payload))
    message = {"role": "user", "content": f"{said}\n\n{named}" if said else named}
    return {**payload, "messages": [*payload["messages"][:-1], message]}


_HANDOVERS = {"content_part": _as_attachments, "by_path": _by_path}


def invoke_agent(payload, config=None):
    cfg = {**(config or {})}
    payload = _with_channels(payload)
    # [] on a turn with no `file` / `image_url` part. Otherwise they reach the agent the way production
    # hands it a user's file: its upload channel, or a planted file named by path.
    if documents := _handed_documents(payload):
        payload = _HANDOVERS[HANDOVER](payload, documents)
    # Resume a paused interrupt() on the user's TYPED decision. A non-LangGraph confirmation flag
    # compares against your own offered value: confirmation = 1 if choice == APPROVE_CHOICE else 0
    if (choice := payload.get("interrupt_choice")) is not None:
        result = app.invoke(Command(resume=choice), config=cfg)
    else:
        result = app.invoke(payload, config=cfg)
    # Say which choice approves on every pause the turn ends on (a HumanInTheLoopMiddleware
    # payload types its own `approve` and needs nothing).
    for pause in (result.get("__interrupt__") or []) if isinstance(result, dict) else []:
        if isinstance(value := getattr(pause, "value", pause), dict) and "action_requests" not in value:
            value.setdefault("approve", APPROVE_CHOICE)
    # Self-documenting runs: every non-`messages` key of the returned dict is persisted
    # per turn into transcript.json (`agent_state`). Recording the RESOLVED model
    # makes a wrong-model run detectable from the artifacts alone — a repo .env silently
    # swapping the model was otherwise invisible (observed live: gpt-5-mini -> gpt-4.1 and
    # nothing on disk showed it). Point this at wherever YOUR agent resolves its model.
    if isinstance(result, dict):
        result.setdefault("run_info", {"model": os.environ.get("DEFAULT_LLM__NAME", "<your-resolved-model-id>")})
    # If your agent runs its OWN tool loop — it dispatches internally and returns prose, with no
    # tool_use block in the messages it hands back — also return `tool_trail`: one entry per
    # dispatch, in execution order, `{"tool", "args", "call_id", "result", "status"}`. `result` is
    # the value the tool returned: when your framework's trace carries no outputs, record them at
    # the tool wrapper (a trail whose every result is null fails the --turn smoke). It is the
    # ONLY channel that topology has. A `mode: real` dispatch writes no /mock/execute event and a
    # self-contained loop leaves no transcript structure, so without it a call that FAILED and a
    # call that never happened are byte-identical to every counter and every judge: one live run
    # dispatched 42 calls, 12 of them terminal failures, and its report published "30 tool calls /
    # 0 tool errors" while a tool-invocation floor reddened a scenario that had dispatched twice.
    # A `mode: mock` tool the agent dispatches once needs nothing here — the mock logs it. One it
    # RETRIES internally does: the mock logs the single call it saw and the trail is the only
    # record of the rest, so `call_id` is REQUIRED on every entry and must be unique per dispatch —
    # that is what the counters dedupe the two lanes on. An entry without one is deduped by tool
    # NAME instead, which cannot tell a retry from a duplicate: it is dropped, this scenario's tool
    # counts become a FLOOR rather than a measurement, and the aggregator says so on stderr.
    #
    # `status` is FOUR-valued, not two. Write "ok" for a dispatch that returned, "error" for one
    # that was dispatched and failed, "not_dispatchable" for a name YOUR GATE REFUSED BECAUSE
    # THIS FLOW DOES NOT EXPOSE IT — the agent asked for a verb it does not have and nothing was
    # ever called — and "denied" with `"refusal": {"by": "harness", "reason": <why>}` for a call your
    # approval or permission gate said no to (`approval_required`, a policy block): the agent TRIED
    # it, and that attempt is graded and can be a finding. Never "error" for a refusal: it is not a
    # broken tool.
    #
    # Do NOT stamp "error" for that third case. Both spellings count as a failed dispatch — a call
    # that never left did not succeed, and a stamp that exempted it would delete the finding
    # entirely — so the difference is not arithmetic, it is WHOSE the failure is. Only
    # "not_dispatchable" says the name was never one this agent has, which is what lets the report
    # name the calls your gate refused and distinguish them from an endpoint that broke. What
    # decides whether such a verb is charged to the AGENT rather than to the environment is your
    # `plurai.config.yaml`: a name in neither `tools:` nor `render_actions:` is an invented verb,
    # and with no declaration to check against nothing may call it one.
    #
    # ONE SPELLING. `plurai._protocol.TRAIL_STATUS_NOT_DISPATCHABLE` is the token's one home and
    # the only stamp that carries this meaning. `refused` is NOT a synonym: it is an older token
    # that already means a dispatch that FAILED, harnesses in the field emit it with exactly that
    # meaning, and the reader keeps it that way rather than re-reading a live token with no
    # version guard — so a gate that writes `refused` says "this call failed" and nothing about
    # the name, which is the distinction you came here to make. Never `unknown_tool` either —
    # that already means an orphan tool RESULT on both sides of this seam.
    #
    #     return {"messages": [...], "tool_trail": [
    #         {"tool": "get_order", "args": {"id": "A-1"}, "call_id": "c1",
    #          "result": {...}, "status": "ok"},
    #         {"tool": "render_dashboard", "args": {}, "call_id": "c2",
    #          "result": "render_dashboard is not one of this agent's tools.",
    #          "status": "not_dispatchable"},
    #         {"tool": "delete_order", "args": {"id": "A-1"}, "call_id": "c3",
    #          "result": "approval required", "status": "denied",
    #          "refusal": {"by": "harness", "reason": "approval required"}},
    #     ]}
    return result

# Async-graph variant — uncomment if your graph is async-only (graph.ainvoke).
# The orchestrator awaits the result automatically.
#
# async def invoke_agent(payload, config=None):
#     cfg = {**(config or {})}
#     return await app.ainvoke(payload, config=cfg)


# ================================================================
# PHASE 6b: seed_state hook (only if your agent has essential storage surfaces)
# ================================================================
# Required ONLY when Step 6.5's audit found essential storage the HARNESS holds
# (a real engine it runs, or the agent's own in-tree store) or a request value the
# agent reads from storage. A store a seam mock serves is seeded by the backend
# through start_session; writing its rows again here is harmless. Non-essential surfaces
# (analytics, logging, feature flags) stay as MagicMock stubs in Phase 1.
# If LangGraph state + mock-env world state cover everything your tools
# touch, skip this entire phase.
#
# The one rule: write the scenario's seed entities through
# plurai.world_state.apply(...) — same surface the @plurai.mock handlers
# read from. That's it. No singleton, no clear()-then-seed dance —
# plurai.world_state is per-session by construction (scenario N+1 gets a
# fresh session id, so old session's entities are invisible).
#
# Same asyncio.run ban as invoke_agent: declare async def if you want,
# but never wrap with asyncio.run(). plurai.world_state.apply itself is
# synchronous; the async signature is purely a convenience for callers.
#
# ⚠ PSEUDOCODE BELOW — not runnable as-is. Replace EVERY identifier with
# your agent's scenario_state_schema field names + entity types:
#   - `<your_rows_field>` / `<your_choices_field>` / `<your_parent_key>` → keys
#     YOUR scenario_state_schema declares in agent_context_fields (read
#     agent_context.yaml to learn them; they're agent-specific)
#   - `<your_row_type>` / `<your_parent_type>` → entity types YOUR
#     scenario_state_schema declares
#
# def seed_state(initial_agent_context, initial_world_state, resume_seed=None):
#     # The third parameter is REQUIRED (with its default): the child calls
#     # this hook with three positional args on every scenario and passes
#     # None when the scenario is fresh, so a two-parameter signature raises
#     # TypeError at init. The body must HANDLE a non-None value — every
#     # converted trace bundle carries a resume pin and no config declares an
#     # env fresh-only, so accepting the seed and dropping it fails Step 9e's
#     # seed_state_ignores_resume_seed. Step 5d below replays it; a harness
#     # that genuinely cannot restore a conversation raises there instead.
#
#     # 5a. Auto-populate agent_context defaults from scenario_state_schema.yaml.
#     #
#     # The agent's production tool implementations read fields like
#     # ``state.get("<your_state_field>")``, ``agent_context.get("<your_choices_field>")``.
#     # When a scenario YAML forgets one of these, the tool returns a structured
#     # error like ``"<field> required in state"``; the agent's React
#     # loop then retries other tools trying to recover, exhausting the
#     # recursion budget (GraphRecursionWrap). The audit at Step 9e.4
#     # surfaces every key read by the production code; the schema
#     # declares the required ones with sensible default_values.
#     #
#     # This loop carries those defaults into agent_context whenever the
#     # scenario itself didn't set the field. Defense in depth — even if
#     # the scenario author forgets a field, the agent doesn't crash.
#     _apply_schema_defaults(initial_agent_context)
#
#     # 5b. Apply the scenario's seeded entities verbatim when present.
#     #
#     # initial_world_state['entities'] carries the scenario's seeded world —
#     # every entity and the history between them. Ignoring it and synthesising
#     # a fresh default instead discards that world; the next tool that reads
#     # from world_state sees nothing and the agent's retry loop runs to its
#     # budget (GraphRecursionError).
#     #
#     # Before applying, mirror domain invariants from agent_context into the
#     # entities the agent's code reads them from: when production reads a value
#     # off an entity (not off state) and refuses to proceed without it, a
#     # schema default at the agent_context layer alone does not reach it.
#     entities = (initial_world_state or {}).get("entities") or {}
#     if entities:
#         _mirror_agent_context_onto_entities(entities, initial_agent_context)
#         plurai.world_state.apply(entities)
#     else:
#         # 5c. Fallback synthesis when initial_world_state.entities is empty
#         # (legacy scenarios with no seeded entities).
#         synth = {}
#         for i, row in enumerate(initial_agent_context.get("<your_rows_field>", [])):
#             synth[f"row_{i}"] = {"_entity_type": "<your_row_type>", **row}
#         synth["parent_0"] = {
#             "_entity_type": "<your_parent_type>",
#             "<your_parent_key>": initial_agent_context.get("<your_parent_key>") or "default",
#             "<your_choices_field>": initial_agent_context.get("<your_choices_field>", []),
#             "<your_row_ids_field>": list(synth),
#         }
#         plurai.world_state.apply(synth)
#
#     # 5d. Resumed scenario: replay the seed into whatever invoke_agent reads
#     # this thread's history from. Steps 2-5 of the worked example in
#     # references/resumed-seed-state.md go here verbatim, and Step 8.5's
#     # references/seed-state-contract.md shows the whole hook assembled.
#     # It runs LAST because a resumed scenario needs the seeded world in
#     # place first, and because neither branch above may return: an early
#     # return here is the silent miss that scores an empty history green.
#     # Restore the agent_state keys the agent has a slot for (a graph's
#     # channels, a state model's fields); skip the rest and name them.
#     # Hand each seed message WHOLE to the agent's own converter, with its
#     # `type`, `tool_calls` and `tool_call_id`; never reduce it to a bare
#     # {role, content}: a tool result then loses the call it answers, and
#     # every pin with a tool exchange fails. Raise plurai.HarnessFault when
#     # fewer messages are restored than seeded.
#     if not resume_seed:
#         return None
#     # LangGraph (reference step 3; declare seed_state `async def`):
#     #   messages = convert_to_messages(resume_seed["messages"])
#     #   await app.aupdate_state(config, {"<your_messages_channel>": messages, **restored}, as_node="<a node whose only edge is END>")
#     #   if (await app.aget_state(config)).next != (): raise plurai.HarnessFault("the seed left the graph mid-step")
#     ...
#     return {"resume_state_dropped": dropped} if dropped else None
#
#
# def _apply_schema_defaults(agent_context):
#     """Populate agent_context defaults from scenario_state_schema.yaml.
#
#     When the schema declares a field with ``required: true`` AND a
#     ``default_value``, this helper writes that default into
#     ``agent_context`` only when the scenario didn't already set it.
#     Schema authoring lives alongside simulate.py:
#
#         agent_context_fields:
#           <your_state_field>:
#             type: string
#             required: true
#             default_value: "<a value production would hold here>"
#           <your_choices_field>:
#             type: list
#             required: true
#             default_value: []
#
#     Missing schema file → no-op (the helper is best-effort).
#     Missing default_value on a required field → no-op for that field
#     (the audit at Step 9e.4 surfaces the gap; this helper just plugs
#     known-good defaults forward, it doesn't invent them).
#     """
#     import pathlib  # noqa: PLC0415
#     try:
#         import yaml  # noqa: PLC0415
#     except ImportError:
#         return
#     schema_path = pathlib.Path(__file__).parent / "scenario_state_schema.yaml"
#     if not schema_path.is_file():
#         return
#     try:
#         schema = yaml.safe_load(schema_path.read_text(encoding="utf-8")) or {}
#     except yaml.YAMLError:
#         return
#     fields = schema.get("agent_context_fields") or {}
#     if not isinstance(fields, dict):
#         return
#     for name, spec in fields.items():
#         if not isinstance(spec, dict):
#             continue
#         if not spec.get("required"):
#             continue
#         if "default_value" not in spec:
#             continue
#         if _scenario_value_is_set(agent_context.get(name), spec["default_value"]):
#             continue  # scenario set a real value; don't clobber
#         agent_context[name] = spec["default_value"]
#
#
# def _scenario_value_is_set(scenario_value, default_value):
#     """True iff the scenario explicitly set a meaningful value.
#
#     Auto-generated scenarios often emit ``<field>: ''`` or
#     ``<list_field>: []`` for cold-start tests, but production tools
#     read these via ``if not state.get(field): return error`` — so the
#     empty container triggers the same precondition error as a missing
#     key. Treating empty strings / lists / dicts as "not set" for fields
#     whose default is also a container lets the default backfill in
#     those cases, making cold-start scenarios runnable without scenario
#     YAML edits.
#
#     Booleans, ints, and floats are NEVER overwritten when present —
#     ``opted_in: false`` is a meaningful explicit choice, not a
#     "set to default please" signal.
#     """
#     if scenario_value is None:
#         return False
#     if isinstance(default_value, bool) or isinstance(scenario_value, bool):
#         return True  # bool present → always honor (False is meaningful)
#     if isinstance(default_value, (int, float)) and not isinstance(default_value, bool):
#         return True  # numeric present → always honor (0 is meaningful)
#     if isinstance(scenario_value, (str, list, dict, tuple, set)):
#         return bool(scenario_value)  # empty container → treat as unset
#     return True  # unknown shape → assume set
#
#
# def _mirror_agent_context_onto_entities(entities, agent_context):
#     """Backfill empty entity fields from agent_context after schema defaults.
#
#     The schema applies defaults at the agent_context layer. When the agent's
#     production code reads the same value off an ENTITY instead — the current
#     parent record's list of allowed choices, its rows — and enforces it hard:
#
#         if not current.<your_choices_field>:
#             raise ValueError("<the production integrity error>")
#
#     the raise is caught by a broad ``except Exception`` in the agent's React
#     loop, which retries — eating recursion budget until GraphRecursionWrap.
#     Mirror the agent_context value into every entity of that type whose field
#     is empty so the invariant holds.
#
#     The SAME failure mode applies to rows: when the scenario contract seeds
#     agent_context rows as the current parent's inline list, the extractor may
#     strand them in agent_context and leave the entity's list empty. The store
#     reads the entity (not agent_context), so the data reads as EMPTY. Put them
#     on the CURRENT parent only (one marked current, e.g. by a head flag), and
#     only when it carries none of its own — never clobber rows the extractor
#     DID seed.
#
#     Skip this entirely if your agent reads these values from ``state`` (the
#     schema default at the agent_context layer is then already sufficient).
#     """
#     if not agent_context:
#         return
#     choices = agent_context.get("<your_choices_field>") or []
#     rows = agent_context.get("<your_rows_field>") or []
#     if not choices and not rows:
#         return
#     for ent in entities.values():
#         if not isinstance(ent, dict) or ent.get("_entity_type") != "<your_parent_type>":
#             continue
#         if choices and not ent.get("<your_choices_field>"):
#             ent["<your_choices_field>"] = list(choices)
#         if rows and ent.get("<your_current_flag>") and not ent.get("<your_rows_field>"):
#             ent["<your_rows_field>"] = [dict(r) for r in rows if isinstance(r, dict)]


# ================================================================
# PHASE 6c: reset() hook (only if this harness owns state the mock does not)
# ================================================================
# The opposite of seed_state, and the child calls it just before: reset()
# takes the LAST scenario's writes out, seed_state puts THIS scenario's
# preconditions in.
#
# Required ONLY when plurai.config.yaml declares a world the mock does not
# own:
#
#     world_state:
#       owner: external      # `mock` (the default — omit the key) | `agent` | `external`
#
# Exactly two things are reset per scenario: the mock session the backend opens
# (keyed on session_id, re-seeded per scenario) and THIS child process (so
# the module-level lists above are born and die inside one scenario).
# THIS child also has siblings: up to concurrency x trials copies of this
# file run at once on one host, and sibling trials seed the same scenario.
# Anything this harness CREATES or CLAIMS is per child: a listener binds
# port 0 (the agent gets the bound port through its own config), a working
# or config dir comes from tempfile.mkdtemp(), and a socket, lock file,
# throwaway sqlite or store record it creates carries
# plurai.active_session_id(). What it only CONNECTS to (a model server, an
# index, a running app database) stays shared, its rows scoped to the child.
# Reuse only records this child owns, never a sibling's; the full rule is
# seed-state-contract.md's "Safe under parallel children".
# Anything else this harness writes to SURVIVES: a file, a sqlite/Postgres
# row, the checkpointer's database, a real CRM, a server-side conversation
# store. Scenario 2 then opens on scenario 1's writes and every verdict is
# partly about the order the scenarios ran in — on a run that completes
# GREEN, because nothing in the output says a scenario started dirty. That
# is why a declared `agent`/`external` world with no reset() is refused at
# RUN START (plurai-run-simulation exits 65, before a thread is opened), and
# why Step 9e fails it as `world_owner_unreset`.
#
# NO ARGUMENTS and no return: it is not preparing a scenario, so it is told
# nothing about one. A signature that demands an argument is refused by name
# during the Init handshake. `async def reset()` is fine — the child awaits
# it — under the same asyncio.run ban as the two hooks above.
#
# Clear only what THIS harness owns. Never clear plurai.world_state here:
# the backend opens a fresh session and re-seeds it per scenario, so clearing it
# from the harness fights the seeding instead of completing it.
#
# ⚠ PSEUDOCODE BELOW — replace every `<your_…>` with YOUR store's own clear.
#
# def reset() -> None:
#     """Clear the stores this harness owns. Called before every scenario."""
#     # The store this process does NOT own — one line per store:
#     #   <your_store_path>.unlink(missing_ok=True)
#     #   <your_db>.execute("DELETE FROM <your_table>")
#     #   await <your_client>.purge(prefix=<your_prefix>)
#     <your_store>.clear()
#
#     # A checkpointer / graph-state database is the commonest one to forget:
#     # without it the conversation "already happened" for scenario 2.
#     # <your_checkpointer>.delete_all()
#
#     # by_path: the hand-over hashes `plant` recorded.
#     # from _documents import PLANTED; PLANTED.clear()
#
#     # Leave the store EMPTY, not re-seeded. Whatever the next scenario
#     # needs arrives through seed_state.


# ================================================================
# PHASE 6d: world_snapshot() hook (same benches as 6c — the other half)
# ================================================================
# reset() CLEARS the store this harness owns. world_snapshot() READS it: the
# child calls it once, at finalize, and reports what it returns VERBATIM as
# that scenario's final_world_state — which is what every world-reading
# policy (`entities_of_type`, `find_entity`) is scored against.
#
# Required by the SAME declaration as 6c, so a declared `agent`/`external`
# world owes BOTH hooks:
#
#     world_state:
#       owner: external      # `mock` (the default — omit the key) | `agent` | `external`
#
# Without it, the only world the run can snapshot is the MOCK's session —
# which that declaration says is not the world being scored. An empty one
# then reaches the judges as an OBSERVED, EMPTY world and
# `len(entities_of_type("<your_type>")) == 0` scores a CONCLUSIVE PASS about
# a store nobody read. With the hook absent the run does NOT do that: it
# warns at start, reports no world, and every world-reading policy resolves
# to `world_unobservable` (not-measured) instead. The dialog and every
# llm_as_judge policy are unaffected either way — which is why Step 9e
# reports this as a WARNING (`world_owner_unobservable`) and the run still
# starts, unlike 6c's refusal.
#
# NO ARGUMENTS. Returns `{entity_id: {...}}` — one key per record, each value
# a dict of that record's fields, with `_entity_type` naming its type so
# `entities_of_type()` can select it. `async def world_snapshot()` is fine —
# the child awaits it — under the same asyncio.run ban as the hooks above.
# Whatever it returns must be JSON-serializable; a value that is not is
# reported as a finalize error rather than silently replaced.
#
# A SPLIT WORLD IS DECLARED, NEVER MERGED BY HAND. `owner: agent` says the
# mock does not own EVERY record this agent reads — not that it owns none. If
# some of the world really is in the mock, declare the types your store holds
# (`world_state.owned_types`, below) and return only your own rows; the run
# composes them with the mock's rows of every other type.
#
# A FILE the agent wrote or was handed is ONE call, `plurai.file_row` — never
# a document reader of your own (Step 9e: `snapshot_hand_rolled_file_reader`).
# Pass `preview=False` for a file handed to the agent, and declare the row's
# fields (`name`, `suffix`, `size_bytes`, `sha256`, `text_preview`) in
# `world_state.entity_types`. A by_path harness reports the files it planted with
# `_documents.planted_rows`: each row adds `planted_sha256`, the hash at hand-over,
# and a file that is gone is a row with `missing: true` (declare both fields too).
#
# ⚠ PSEUDOCODE BELOW — replace every `<your_…>` with YOUR store's own read.
#
# def world_snapshot() -> dict:
#     """The world this harness owns, as {entity_id: {...}}. Called at finalize."""
#     world = {
#         # One entry per record, keyed by the id a policy would look up:
#         #   f"<your_type>_{row.id}": {"_entity_type": "<your_type>", **row.fields}
#         f"<your_type>_{row['id']}": {"_entity_type": "<your_type>", **row}
#         for row in <your_store>.all()
#     }
#     for path in <your_output_dir>.rglob("*"):
#         if path.is_file():
#             rel = path.relative_to(<your_output_dir>).as_posix()
#             world[f"output_file:{rel}"] = plurai.file_row(path, entity_type="output_file")
#     from _documents import planted_rows  # by_path only:
#     world.update(planted_rows(SCENARIO_DIR / "inputs"))
#     return world
#
# # If part of the world really IS in the mock, declare the types this store holds
# # (plurai.config.yaml `world_state.owned_types: [<your_type>]`) and return ONLY
# # your own rows: the run composes them with the mock's rows of every other type.
# # A record keeps one key for the whole run — the key it was seeded under, or the
# # store's own primary key — never a list position or a count.


# ================================================================
# PHASE 6e: probe_read() hook (any bench with a real reader of a mocked write)
# ================================================================
# The real-reader probe (scripts/real_reader_probe.py) calls ONE of the agent's
# real read tools through this harness, with every patch above in place, to
# prove a mocked write is visible where the agent actually reads it. Call the
# agent's own tool object exactly as its loop does (injected runtime/config
# included); never re-implement the read. Sync or async; returns what the
# agent's loop would hand the model.
#
# def probe_read(tool: str, args: dict):
#     return <your_agent_tools>[tool].invoke(args)


# ================================================================
# PHASE 6f: read-through overlay (a `mode: real` tool that declares `read_through`)
# ================================================================
# A real reader of a store a mocked write lands in (config: `read_through:
# {via: [helpdesk.store.TicketStore.get, helpdesk.store.TicketStore.query,
# helpdesk.store.TicketStore.search]}`, with a `tool_graph` edge `create_ticket
# -> search_tickets, shared_entity: ticket`). `via` names EVERY accessor the
# reader reaches, one per read shape — point (`get`), list/query (`query`),
# search (`search`) — and each is rebound so the agent's own read sees the mock
# world over the real store; verify_config FAILs `read_through_unbound` when a
# `via` is never rebound. Render world rows in the accessor's native type.
# The world side only looks up by key or by exact field match (or calls a mock
# reader); it never computes filters, rankings or aggregates itself — the real
# accessor's own code does any computing, a search included.
# Outside a scenario is `active_session_id() is None`, never a caught exception.
# Inside one, nothing is skipped: what the overlay cannot read, place or render
# raises `plurai.HarnessFault` (the turn is the harness's, the real-reader probe
# names it); verify_config WARNs `read_through_swallowed` on a catch-all
# `except` that raises nothing.
#
# import helpdesk.store as _store
#
# _real_get, _real_query, _real_search = (
#     _store.TicketStore.get, _store.TicketStore.query, _store.TicketStore.search)
# _CLOSED = {"deleted"}  # the tombstone state of this type reads as not found
#
# def _world(**exact):
#     try:
#         return plurai.world_state.query("ticket", **exact) or []
#     except Exception as exc:  # a failing world read is the harness's fault, never "no rows"
#         raise plurai.HarnessFault(f"read_through TicketStore: world read {exact!r} failed: {exc}") from exc
#
# def _ticket(row):
#     try:
#         return _store.Ticket.from_dict(row)
#     except Exception as exc:  # a world row the store's type cannot hold: raised, never skipped
#         raise plurai.HarnessFault(f"read_through TicketStore: world ticket {row.get('id')!r} does not render: {exc}") from exc
#
# def _sim_get(self, ticket_id):                       # point: the world's row wins
#     if plurai.active_session_id() is None:
#         return _real_get(self, ticket_id)
#     rows = _world(id=str(ticket_id))
#     if not rows:
#         return _real_get(self, ticket_id)
#     return None if rows[0].get("status") in _CLOSED else _ticket(rows[0])
#
# def _sim_query(self, **filters):                     # list / query: exact fields
#     if plurai.active_session_id() is None:
#         return _real_query(self, **filters)
#     held = {str(r.get("id")) for r in _world()}
#     real = [t for t in _real_query(self, **filters) if str(t.id) not in held]
#     return real + [_ticket(r) for r in _world(**filters) if r.get("status") not in _CLOSED]
#
# def _sim_search(self, text, **kwargs):               # search: the real code matches
#     if plurai.active_session_id() is None:
#         return _real_search(self, text, **kwargs)
#     # This conversation's copy of the cache (a scratch file the harness owns, made
#     # once per session) with the world's tickets laid in by id: the world's row
#     # wins, a tombstone deletes. The real `search` then runs its own SQL over it.
#     copy = _conversation_copy(self)           # yours: copies self's db file once per session id
#     for row in _world():
#         try:
#             copy.delete(str(row["id"])) if row.get("status") in _CLOSED else copy.upsert(_ticket(row))
#         except plurai.HarnessFault:
#             raise
#         except Exception as exc:  # a row it cannot lay in: raised, never skipped
#             raise plurai.HarnessFault(f"read_through TicketStore.search: cannot lay in ticket {row.get('id')!r}: {exc}") from exc
#     return _real_search(copy, text, **kwargs)
#
# _store.TicketStore.get = _sim_get        # one assignment per accessor: the
# _store.TicketStore.query = _sim_query    # rebinding verify_config looks for
# _store.TicketStore.search = _sim_search
#
# An aggregate (an availability calculator over a busy-time accessor) overlays
# the lowest accessor UNDER the computation the same way: it hands the world's
# events (looked up by exact field) to the real code, which does the windowing
# and the slot arithmetic.
#
# A store whose keys are PATHS (a file tree the overlay lays world files into so
# the real list / search code runs over them): canonicalise BOTH sides before
# any containment or equality test — the store may resolve symlinks the
# harness's own value keeps (macOS `/var` -> `/private/var`), and comparing the
# two spellings drops every file — and raise when a file cannot be placed.
#
# _SCRATCH = Path(tempfile.mkdtemp(prefix="sim-")).resolve()  # canonical once
#
# def _place(host_path, content):
#     host = Path(os.path.realpath(host_path))
#     if not host.is_relative_to(_SCRATCH):  # both sides canonical
#         raise plurai.HarnessFault(
#             f"read_through: world file {host_path!r} resolves to {host}, outside this conversation's copy {_SCRATCH}")
#     host.parent.mkdir(parents=True, exist_ok=True)
#     host.write_text(content, encoding="utf-8")


# ================================================================
# How to run this file
# ================================================================
# Under the subprocess orchestration model, simulate.py is NOT a script you
# launch directly. The orchestrator (parent process) spawns it as a child
# subprocess and drives it over stdin/stdout JSON envelopes. You don't need
# an ``if __name__ == "__main__":`` block.
#
# Run via either:
#
#   1. plurai-run-simulation CLI (recommended), resolving scenarios BY ID
#      from a Plurai backend:
#      $ plurai-run-simulation \
#          --scenario-set-id <id> --scenario-id <id> \
#          --agent-module-path ./simulate.py \
#          --out ./run-output
#
#   2. plurai.Orchestrator from your own script:
#      from plurai.orchestrator import Orchestrator
#      async with Orchestrator(
#          base_url="<backend URL>",
#          simulate_path=Path("./simulate.py"),
#      ) as o:
#          result = await o.run(scenario_set_id="<id>", scenario_id="<id>")
#
# `seed_state` (if defined above) is auto-discovered by the child — no need
# to pass it through any constructor.
