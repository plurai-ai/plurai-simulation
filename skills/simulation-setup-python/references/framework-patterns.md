# Framework-Specific Patterns

Read this when the agent under test is NOT a plain LangGraph agent — it carries
the per-framework `apply_mocks` variants (LangChain, CrewAI, OpenAI Agents SDK,
custom no-framework agents), the Claude Agent SDK surface + trail recipe, and
the cross-language pattern table. The one universal rule stays in the SKILL.md
body: **apply_mocks before import, for every framework.**

## The Universal Pattern: apply_mocks before import

The same approach works for ALL frameworks:

```python
# 1. Stubs
sys.modules["our_db"] = MagicMock()

# 2. Patch tools BEFORE importing agent
import our_tools
plurai.apply_mocks(our_tools, {"get_user": "mock", "process_refund": "mock"})

# 3. Import agent — it picks up already-patched tools
from our_agent import app  # graph/chain/executor builds with mocked tool functions

# 4. Run
plurai.Simulate(agent=app.invoke)
```

This works because:
- LangGraph's `ToolNode([tool1, tool2])` gets references to already-patched functions
- LangChain's `AgentExecutor` calls the patched functions when the LLM decides to use a tool
- CrewAI's agents call the patched functions directly
- Any framework that eventually calls the tool function will hit the mock

## Why this works for LangGraph (no rebuild needed)

```python
# In the customer's tools.py:
@tool
def get_user(user_id: str):
    return db.query(User).filter(User.id == user_id).first()

# In the customer's agent.py:
from tools import get_user, process_refund
graph.add_node("tools", ToolNode([get_user, process_refund]))
app = graph.compile(checkpointer=PostgresSaver(...))
```

When simulate.py does `plurai.apply_mocks(tools, {"get_user": "mock"})`, it replaces
`tools.get_user` with a wrapper that routes to the mock service. When agent.py then
imports `from tools import get_user`, it gets the patched version. The ToolNode is
constructed with the mocked function reference. The original function body never executes.

The PostgresSaver will be stubbed via Phase 1 (`sys.modules["psycopg2"] = MagicMock()`),
which makes it a no-op. If it still crashes, stub the checkpointer module directly.

## Tools with closures over stubbed deps

```python
db = get_db_session()  # After Phase 1 stubs, db = MagicMock()

@tool
def get_user(user_id: str):
    return db.query(User).filter(User.id == user_id).first()
```

This is fine. The function definition succeeds. The closure captures MagicMock for `db`.
But `apply_mocks` replaces the entire function — the wrapper intercepts before the body
runs, so the MagicMock closure is never touched.

## bind_tools pattern

```python
llm_with_tools = llm.bind_tools([get_user, process_refund])
```

`bind_tools` extracts the tool SCHEMA (name, args, description) and sends it to the LLM.
The LLM decides which tool to call. Then the ToolNode EXECUTES the function — which is
the patched version from `apply_mocks`. The schema extraction works fine on patched
functions because `apply_mocks` preserves `name`, `description`, and `args_schema`.

## CrewAI

CrewAI tools are assigned to agents, not to a graph node. Use `plurai.apply_mocks` on the module where tools are defined:

```python
import plurai
import our_tools  # Module where tool functions live

plurai.apply_mocks(our_tools, {
    "get_user_profile": "mock",
    "process_refund": "mock",
    "search_knowledge_base": "real",
})

# Now create the CrewAI agent normally.
# When it calls our_tools.get_user_profile(...), it hits the mock.
from crewai import Agent, Crew, Task
agent = Agent(role="...", tools=[our_tools.get_user_profile, our_tools.process_refund])
```

If tools are passed as a list to the Agent constructor, they're already wrapped because `apply_mocks` replaced them in the module namespace.

## OpenAI Agents SDK

Same pattern — mock the tool functions before passing them to the agent:

```python
import plurai
import our_tools

plurai.apply_mocks(our_tools, {
    "get_user_profile": "mock",
    "process_refund": "mock",
})

from agents import Agent, Runner
agent = Agent(name="billing", tools=[our_tools.get_user_profile, our_tools.process_refund])
result = Runner(agent).run(...)
```

**A mocked tool that writes the run context.** A real function tool that stores on its
`RunContextWrapper` parameter (`ctx.context.<attr> = ...`) gates later tools on that value. Declare
those keys under the tool's `state_updates:` and land them where the real tool does — on the context,
not in the returned message:

```python
async def _mock(ctx: RunContextWrapper, **kwargs):
    result = _resolve_mock_response(tool_name, kwargs, return_type=dict)   # from plurai._mocking.resolution import _resolve_mock_response
    for key, value in _state_update(tool_name, result).items():   # the _MOCK_STATE_UPDATES table
        setattr(ctx.context, key, value)
    return _render_content(result)
```

`verify_simulate.py` fails `wrapper_drops_state_updates` when the wrapper never writes onto `ctx.context`.

<!-- BEGIN shared:claude-agent-sdk-surface owner -->
## Claude Agent SDK

A `claude_agent_sdk` agent's tools are the bundled Claude Code CLI's built-ins, run inside the
CLI subprocess. There is no tool function for `apply_mocks` to patch, and no plurai lane sees them.

**The surface is what the session starts with, not what the code lists.** The rule:

- Live built-ins = `tools` minus `disallowed_tools`. An unset `tools` (`None`) is the CLI's
  **full preset**, which grows with each CLI version. A list is exactly those tools, and `[]` is none.
- MCP servers = `mcp_servers`. Servers from settings, plugins and claude.ai connectors also load
  unless `strict_mcp_config=True`, and are never the agent's. `setting_sources=None` loads every settings source.
- `skills` needs `Skill` in a `tools` list.
- `allowed_tools` and `permission_mode` are permission posture. Record them, and never read them
  as the surface.

Read the resolved list from the `tools` field of the session's `init` message, over the agent's
own options, in one interrupted turn:

```python
async with ClaudeSDKClient(options=agent.build_options()) as client:   # the agent's own builder
    await client.query('Reply ok.')
    async for msg in client.receive_messages():
        if isinstance(msg, SystemMessage) and msg.subtype == 'init':
            surface = msg.data['tools']
            await client.interrupt()
            break
```

Every resolved tool goes on the topology's `tools` list. `record_native_tools.py` writes the live
ones no plurai lane wraps to `plurai.config.yaml` as `agent.native_tools: [...]`, and
`render_topology.py` marks `"consent": "required"` on every built-in off the read-only list in
`scripts/host_effects.py` and every tool the map types to act. Each one needs consent,
which the scan records in `plurai_sim/.host_effects_consent`. Without it (headless included) the harness denies the tool,
records the deny, and the run continues; a built-in the scan never recorded is denied at the call.

**Confine every scenario, and deny what was not granted, on the agent's own options before
connect.** Each scenario runs in a fresh folder outside the bench (so the rubric and sibling
scenarios are out of reach), with the agent's `.claude/` copied in so its skills still load, and
the agent's `output_dir` inside it. `plurai.confine` applies the rest from the SDK's copy of that
table. MCP servers the agent does not declare in `mcp_servers` or its own `plugins` never load:

```python
import shutil
import tempfile

import plurai


def _new_scenario_dir() -> Path:
    run_dir = Path(tempfile.mkdtemp(prefix='plurai_scenario_'))
    if (REPO / '.claude').is_dir():
        shutil.copytree(REPO / '.claude', run_dir / '.claude')
    return run_dir
```

`reset()` starts each scenario with `_RUN_DIR = _new_scenario_dir()` (outputs/ and inputs/ inside
it). Where the thread's session is first built, call
`record = plurai.confine(session.options, run_dir=_RUN_DIR, repo=REPO, workdir=HERE, trail=trail)`
(the thread's trail, below) before `await session.__aenter__()`, and return `'host_effects': record`
beside `messages` on every turn; the SDK rolls it up into `run_summary.json`. An ungranted tool
writes only inside the scenario folder, runs its shell sandboxed (network open unless
`PLURAI_SANDBOX=strict`), or is switched off, and the record says which; reads are scoped to the scenario folder and the agent repo whatever
was granted. A declared program leaves the Bash sandbox only by the manifest's `consent.unsandboxed`
grant, and the record names the granted programs and the declared ones that ran sandboxed. The agent's CLI signs in with your existing Claude login (or the token, API key or
cloud provider its environment or your user settings' `env` set, or your user settings'
`apiKeyHelper`) and keeps its session records in a config folder of the scenario's own. Only those
sign-in settings move there: the rest of your user settings (`model`, `permissions`, `hooks`,
plugins, MCP servers) does not reach it. A route its environment declares is the one it signs in
with: your own `apiKeyHelper` and saved `/login` key are then left out. A provider refresh command
in your user settings (`awsAuthRefresh`, `awsCredentialExport`, `gcpAuthRefresh`) still runs, as it
does outside a run, and keeps its cache in your home. Its shell, its auto-memory and its npm cache use a home of
the scenario's own, and a turn the CLI ends on a provider or sign-in error is typed `provider_infra`,
never the agent's reply. The record names the sign-in route and reports where the agent's writes
landed, the CLI's own session records included.

**The trail is the list you pass `plurai.confine`; agent.py stays untouched.** `confine` appends
every tool call the CLI runs or denies to `trail` and its record says so (`'trail': 'recorded'`), so
the harness installs no hooks of its own. Return each turn's slice:

```python
# per turn: start = len(trail); reply = await session.send(text)
# return {'messages': [...], 'tool_trail': trail[start:], 'host_effects': record, 'cli_session': _record_cli_session(session)}
```

**A resumed scenario resumes the CLI's own session at the cut.** `cli_session` is what makes that
possible: `seed_state` and the session builder follow the CLI worked example in
the resumed-seed reference (`resume`, `resume_session_at`, `fork_session`), never a refusal of the seed.

A Skill call lands as `{'tool': 'Skill', 'args': {'skill': '<name>'}}`. If the agent copies its
options or connects in its constructor, confine's hooks never land: confirm the trail is non-empty
on a smoke turn that used a tool. `verify_simulate.py` fails `native_tool_trail_missing` when a
config declaring `agent.native_tools` has a harness that never calls
`plurai.confine(..., trail=...)` or returns no `tool_trail`.

**Injected model input rides beside it, on `input_context`.** Any harness whose agent adds content to its own
model input per turn (a memory, a state snapshot, pre-retrieved documents) wraps that READ call and returns its
return value as the agent received it, never a second call to the source:
`return {'messages': [...], 'input_context': [{'source': '<the read>', 'content': inserted_text}]}`.

**Text the product's code adds to the reply rides beside it, on `appended_output`.** When the product appends its own
text to the assistant's reply after the model's output, keep it in `messages` and declare it:
`return {'messages': [...], 'appended_output': [{'source': 'product', 'site': '<file:line>', 'text': added_text}]}`,
`text` exactly as it appears in the reply. An act the product's code makes adds `'source': 'product'` to its
`agent_actions` entry.
<!-- END shared:claude-agent-sdk-surface -->

## The trail row's tool name

A trail row's `tool` is the `plurai.config.yaml` `tools` key, never the framework's wire name for it: scoring, the
declared-surface check and every lookup in the harness (`row['tool'] == '<key>'`) read the key. A framework that renames
a tool on its own events (CrewAI's `sanitize_tool_name`: `create_draft` for `@tool("Create Draft")`) is mapped back where
the row is built. Where the agent ALSO exposes a message lane (its `messages` carry the model's tool calls), the trail's
`tool` is the name that lane carries, so the runner can tell one call on two lanes apart from two calls: declare the key
under that name rather than remapping the trail. The `--turn` / `--pipeline` smoke names a wire name it can map to one
key (`trail_tool_undeclared`).

<!-- BEGIN shared:calls-inside-a-delegation owner -->
## Calls made inside a delegation

A sub-agent's calls are the system's calls: record them on the same trail, never drop them. Each inner row also carries
`parent_call_id`, the `call_id` of the trail row it ran inside (the delegation call), so scoring shows it under that call
and an eval can choose its unit. Only `parent_call_id` is read: a link under any other key (`via`, `scope`, `task_id`) is
dropped. Prove it on the delegating smoke turn and write `inner_calls: linked` on the map (simulation-setup Step 8).

**LangChain / LangGraph** (`BaseCallbackHandler`). A sub-agent tool's immediate parent is the sub-agent's own chain or
model run, not the delegating tool, so keep every run's parent and walk up to the nearest own-tool run:

```python
class _TrailRecorder(BaseCallbackHandler):
    def __init__(self) -> None:
        self.trail: list[dict] = []
        self._parent: dict = {}   # run_id -> parent_run_id, for EVERY run (chain, model, tool)
        self._row: dict = {}      # run_id of an own-tool run -> its trail row

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        self._parent[run_id] = parent_run_id

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kw):
        self._parent[run_id] = parent_run_id

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kw):
        self._parent[run_id] = parent_run_id

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, inputs=None, **kw):
        self._parent[run_id] = parent_run_id
        row = {'tool': serialized.get('name'), 'args': inputs or {}, 'call_id': str(run_id)}  # your existing call_id rule
        at = parent_run_id
        while at is not None and at not in self._row:
            at = self._parent.get(at)
        if at is not None:
            row['parent_call_id'] = self._row[at]['call_id']
        self._row[run_id] = row
        self.trail.append(row)
```

The sub-agent's runs are children of the delegating tool's run only when the tool passes its `config` on
(`await sub_agent.ainvoke(state, config=config)`); attach the recorder once, at the graph entry.

**CrewAI** (event bus). A delegation is the `Delegate work to coworker` / `Ask question to coworker` tool. Keep a stack of
the open ones from `ToolUsageStartedEvent`, mint each row's `call_id` when it STARTS, and link every row started while a
delegation is open (by another crew member) to the top of the stack. Rows may finish before their delegation does; the
link is by id, so their order on the trail does not matter. CrewAI ≥1.x events carry `sanitize_tool_name(tool.name)`
(`create_draft`), never the name the config declares (`Create Draft`): map it back to the config's `tools` key with
CrewAI's own function, over the config's keys (its delegation tools included: they are made at run time and are never in
`agent.tools`), and compare delegations in the same sanitized form. A CrewAI without that function gets the same rule
restated (measured on crewai 1.15.28), so the recipe imports on any version and maps a raw or a sanitized event name:

```python
try:
    from crewai.utilities.string_utils import sanitize_tool_name
except ImportError:  # a crewai without it: the same rule, restated from crewai 1.15.28
    import hashlib, re, unicodedata

    def sanitize_tool_name(name: str, max_length: int = 64) -> str:
        out = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        out = re.sub(r'([a-z])([A-Z])', r'\1_\2', re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', out)).lower()
        out = re.sub(r'_+', '_', re.sub(r'[^a-zA-Z0-9]+', '_', re.sub(r'[\'"]+', '', out))).strip('_')
        if len(out) > max_length:
            suffix = '_' + hashlib.sha256(out.encode()).hexdigest()[:8]
            out = out[: max_length - len(suffix)].rstrip('_') + suffix
        return out

_DELEGATIONS = ('Delegate work to coworker', 'Ask question to coworker')
#: Every `tools` key plurai.config.yaml declares for the crew's tools, delegation tools included, as written there.
_TOOL_KEYS = ('Create Draft', *_DELEGATIONS)
_KEY_OF = {sanitize_tool_name(key): key for key in _TOOL_KEYS}   # the event's name -> the declared key
_DELEGATION_NAMES = frozenset(sanitize_tool_name(name) for name in _DELEGATIONS)
_open: list[dict] = []   # rows of the delegations still running

def _on_started(event) -> dict:
    wire = sanitize_tool_name(event.tool_name)
    row = {'tool': _KEY_OF.get(wire, event.tool_name), 'args': event.tool_args, 'call_id': f'crew-{uuid4().hex[:8]}'}
    if _open and event.agent_role != _open[-1]['agent']:
        row['parent_call_id'] = _open[-1]['call_id']
    if wire in _DELEGATION_NAMES:
        _open.append({**row, 'agent': event.agent_role})
    return row   # completed with `result` / `status` on its ToolUsageFinished, which pops a finished delegation
```

**A recorder that already knows the enclosing call** (a `task` tool that spawns a background sub-agent with the task
call's own `tool_call_id` as its id): write that id as `parent_call_id` on every row the sub-agent
records, in place of a private key such as `task_id` or `scope`.
<!-- END shared:calls-inside-a-delegation -->

## Custom agents (no framework)

For any agent that calls tool functions directly — classes, scripts, while loops, anything:

```python
import plurai
import our_tools

# Seed WorldState with scenario data
plurai.configure(world_state={
    "entities": {
        "user_123": {"_entity_type": "user", "name": "Alice", "plan": "premium", "balance": 347.0}
    }
})

# Replace tool functions in the module
plurai.apply_mocks(our_tools, {
    "get_user_profile": "mock",
    "process_refund": "mock",
    "rag_search": "real",
})

# Now run the agent. Any call to our_tools.get_user_profile(...)
# returns data from WorldState. The original function body never executes.
from our_backend.agent import MyCustomAgent
agent = MyCustomAgent()
result = agent.run("I want a refund", user_id="user_123")
```

You can also wrap individual functions without apply_mocks:
```python
our_tools.get_user_profile = plurai.mock_wrap(our_tools.get_user_profile, mode="mock")
```

A failed mocked call follows the tool's `tools.<name>.error_channel` in `plurai.config.yaml`
(`raises` | `returns`, how the REAL tool reports the failure the mock models): `raises` →
`mock_wrap` raises `plurai.MockToolError`, as the real tool raises; `returns` → pass
`failure=lambda e: <the failure value the real tool returns>`. A hand-written wrapper reads the
mock's answer with `plurai.mock_result(response)` and catches `plurai.MockToolError` — it decides
only HOW the agent sees the failure (its own exception class, an error tool message, its failure
value), never whether there was one, and never by reading status tokens.

## The turn's model calls: what `invoke_agent` returns

Trace sinks draw one model call per assistant message of the `messages` the harness returns, with the
model name and token usage the message carries. The chain a turn owes them: each assistant message
(with its `tool_calls`), each tool result, the final reply. Copy `references/model_calls.py` beside
`simulate.py` as `_model_calls.py`; it builds the messages from each framework's own record of the turn.
A value the framework does not report stays out. Never a configured default, an estimate, or a model name
read from an env var: a sink shows a missing value as missing and a wrong one as fact.

**LangGraph / LangChain.** Return the graph's result as it is. Its `AIMessage` objects already carry
`tool_calls`, `response_metadata.model_name` and `usage_metadata`, and the SDK serialises them whole.
Rebuilding the messages (`{"role": "assistant", "content": ...}`) is what drops them.

**Strands Agents.** The agent keeps its history as Bedrock Converse messages and counts tokens per event-loop
cycle (one cycle is one model call). Attach a `StrandsTurn` hook once, take the turn's messages after the call:

```python
from _model_calls import StrandsTurn, strands_chain, strands_cycle_usage, strands_model_id

turn = StrandsTurn()
agent.hooks.add_hook(turn)          # once, where the agent is built

def invoke_agent(payload, config=None):
    reply = agent(text)             # the agent's own entry
    chain = strands_chain(
        turn.take(), model=strands_model_id(agent), cycle_usage=strands_cycle_usage(agent)
    )
    return {"messages": chain, "tool_trail": [...]}
```

Give each tool dispatch in `tool_trail` the `toolUseId` as its `call_id`: the chain and the trail are two
records of the same call and are joined on that id. An agents-as-tools setup hooks the orchestrator only; a
sub-agent's own messages belong to its tool call, and the orchestrator's cycles are the ones `strands_cycle_usage` reads. `strands_cycle_usage` needs strands-agents 1.21.0+ (`latest_agent_invocation`); on an older release it warns once and the chain carries no usage.

**A hand-written OpenAI / Anthropic loop.** The loop owns every response, so it builds the chain as it goes:

```python
from _model_calls import anthropic_message, openai_chat_message, tool_message

chain = []
while True:
    completion = client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
    assistant = openai_chat_message(completion)      # model + usage from THIS response
    chain.append(assistant)
    if not assistant.get("tool_calls"):
        break
    for call in assistant["tool_calls"]:
        chain.append(tool_message(call["id"], run_tool(call["name"], call["args"])))
    # extend `history` with the assistant message and the tool results, in the provider's own spelling
return {"messages": chain}
```

`anthropic_message(response)` is the same for a Messages-API loop (`tool_message(block.id, text)` per `tool_use` block). Both stamp the provider (`'openai'` / `'anthropic'`) beside the model, which LangSmith needs to price the
call; pass `provider=` the serving vendor (or None) when the client points at another vendor's compatible endpoint.

**OpenAI Agents SDK.** `openai_agents_chain(result, model=agent.model)` turns the run's `raw_responses`
(one per model call, each with its usage) and `new_items` (the tool outputs) into the chain. Pass `model=` only for an
agent that cannot hand off: the name is stamped on every response, and after a handoff the responses come from
another agent and possibly another model. With handoffs, leave it out.

## Always use apply_mocks

| Framework | Language | Pattern |
|-----------|----------|---------|
| LangGraph | Python | `plurai.apply_mocks(tools_module, {...})` before importing agent |
| LangChain (old) | Python | `plurai.apply_mocks(tools_module, {...})` before importing agent |
| CrewAI | Python | `plurai.apply_mocks(tools_module, {...})` before importing agent |
| OpenAI Agents SDK | Python | `plurai.apply_mocks(tools_module, {...})` before importing agent |
| Claude Agent SDK | Python | No tool function to patch: its built-ins run in the CLI — see "Claude Agent SDK" |
| Custom agent | Python | Same pattern. It works on everything. |

The pattern is always the same: patch tools first, import agent second. The agent picks up mocked tools through normal import resolution.

Node frameworks (LangGraph.js, OpenAI Agents JS, Mastra, Vercel AI SDK): the bridge lane, `simulation-setup-bridge`.
