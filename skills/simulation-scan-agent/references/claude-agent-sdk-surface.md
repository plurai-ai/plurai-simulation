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
