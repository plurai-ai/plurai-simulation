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
