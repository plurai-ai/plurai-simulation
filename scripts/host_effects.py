"""Which of the agent's live tools need the operator's grant, and what keeps an ungranted one from acting.

Consent is deny-by-default and typed, never keyed on a tool's name. The scan's map types every live
(non-mocked) tool with three facts: `writes` (what its own writes reach), `effects` (what it does
beyond reading: a shell, the operator's files, the network, work that outlives the scenario) and
`runs` (where it executes). A tool needs a grant when its typing says it acts, and a runtime's
built-in needs one unless it is on that runtime's read-only allow-list, whatever it was typed:
typing may ADD a denial, never remove one. A live tool the map does not classify is refused, never
read as safe.

The grant is `plurai_sim/.host_effects_consent` (`granted: a, b`). What stops an ungranted tool is
dispatched on `CONFINERS`, per runtime: the Claude Agent SDK's CLI built-ins are confined by
`plurai.confine`, a hosted agent's org actions are denied by its rewrite, and on every other
runtime nothing confines a live tool, so it is mocked or the run does not start.

`render_topology.py` marks consent on the map from here, `bench_manifest.py` carries the list, and
`verify_config.py` refuses a bench whose ungranted tools nothing would stop. `HOST_EFFECT_TOOLS`
and `READ_ONLY_BUILTINS` are parity-pinned to the SDK's copies (`plurai.host_effects`) by
`test_host_effects_confinement.py`; this module is stdlib-only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: The Claude Agent SDK built-ins `plurai.confine` confines IN PLACE or switches off by name when
#: ungranted (its per-tool dispatch keys: file edits scoped to the scenario, `Bash` sandboxed, the
#: rest disallowed). Not the consent set: any built-in off `READ_ONLY_BUILTINS` needs a grant.
HOST_EFFECT_TOOLS = (
    'Bash',
    'Write',
    'Edit',
    'NotebookEdit',
    'WebFetch',
    'WebSearch',
    'RemoteTrigger',
    'PushNotification',
    'ScheduleWakeup',
    'CronCreate',
    'CronDelete',
)

#: The built-ins that write a FILE, by path: what a recorded tool trail says landed in the scenario's
#: workspace. SDK-owned (`plurai.host_effects._WRITE_TOOLS`); this copy is parity-pinned beside the
#: table above.
FILE_WRITE_TOOLS = ('Edit', 'NotebookEdit', 'Write')

#: Per runtime, the built-ins that read a FILE by the path the model passes: live, one reaches a file the
#: user names in a message. Plugin-owned (`verify_config`'s `input_file_channel_undeclared` reads it).
FILE_READ_BUILTINS: Mapping[str, tuple[str, ...]] = {'claude_agent_sdk': ('Read', 'NotebookRead')}

#: Programs never granted out of the Bash sandbox (`consent.unsandboxed`): a shell, or a program whose
#: job is to run another command, would let every command out. SDK-owned
#: (`plurai.host_effects.UNGRANTABLE_PROGRAMS`); this copy is parity-pinned beside the table above.
UNGRANTABLE_PROGRAMS = (
    'sh',
    'bash',
    'zsh',
    'dash',
    'ksh',
    'mksh',
    'ash',
    'fish',
    'csh',
    'tcsh',
    'pwsh',
    'busybox',
    'env',
    'xargs',
    'sudo',
    'doas',
    'su',
    'nohup',
    'nice',
    'timeout',
    'time',
    'stdbuf',
    'setsid',
    'chroot',
    'script',
    'command',
    'builtin',
    'exec',
    'eval',
    'osascript',
    'open',
    'genv',
    'gxargs',
    'gtimeout',
    'gnohup',
    'gnice',
    'gstdbuf',
    'gtime',
)


#: Per runtime, the built-ins known to only read, to act only on the agent's own background tasks
#: (which run under the same confinement), or to run under the same confinement (a sub-agent runs
#: under the same options): the ONLY built-ins that need no grant. A new or unknown built-in is not
#: here, so it needs one.
READ_ONLY_BUILTINS: Mapping[str, tuple[str, ...]] = {
    'claude_agent_sdk': (
        'Read',
        'Glob',
        'Grep',
        'LS',
        'NotebookRead',
        'ToolSearch',
        'Skill',
        'TodoWrite',
        'Task',
        'Agent',
        'Workflow',
        'ListAgents',
        'CronList',
        'TaskOutput',
        'BashOutput',
        'TaskStop',
        'KillShell',
        'ListMcpResourcesTool',
        'ReadMcpResourceTool',
    ),
}

#: What a map tool's own writes reach: `live` is the customer's real system, so it runs only on a grant. `world` is
#: simulated storage, the declared per-conversation local store included (a store the harness creates or resets for each
#: conversation: a temp folder, a leg-local database): a write there is not live and needs no grant.
WRITES = ('live', 'world', 'none')
#: What a live tool does beyond reading. `none` stands alone.
EFFECTS = ('shell', 'files', 'network', 'schedule', 'none')
#: Where a live tool executes: in the agent's process (its call is in the message history), by the
#: model provider or the agent's hosting platform, or as a built-in of the agent's CLI subprocess.
RUNS = ('in_process', 'provider_hosted', 'subprocess')

#: The remedy every refusal of an unclassified tool names.
TYPING_REMEDY = ('re-run /simulation-scan-agent: its Step 2a types `writes`, `effects` and `runs` on every live tool, and on a '
                 '`rag` entry that is a tool the config runs for real (or lists that tool under `tools`)')

#: A tool the CLI serves from an MCP server is named by the CLI with this prefix; it is not a built-in.
MCP_PREFIX = 'mcp__'

#: How an ungranted consent-required tool is kept from acting, per runtime (see `runtime_of`).
#: A runtime with no row has nothing that confines a live tool: `mock_or_refuse`.
CONFINERS: Mapping[str, str] = {'claude_agent_sdk': 'confine', 'agent_host': 'rewire_deny'}
DEFAULT_CONFINER = 'mock_or_refuse'

#: Which live tools each confiner actually reaches; a tool its runtime's confiner does not reach is `mock_or_refuse`.
_REACHES: Mapping[str, Callable[[dict[str, Any]], bool]] = {
    'confine': lambda tool: tool.get('runs') == 'subprocess' and not str(tool.get('name')).startswith(MCP_PREFIX),
    'rewire_deny': lambda tool: True,
    'mock_or_refuse': lambda tool: False,
}

#: What happens to an ungranted consent-required tool, per confiner: the map and the consent question say exactly this.
UNGRANTED_OUTCOME: Mapping[str, str] = {
    'confine': (
        "each scenario runs in its own folder outside your repo: file edits stay there, `Bash` runs "
        "sandboxed, writing only there and in Claude Code's own temp and log folders and reading none "
        'of your credentials, with its network open as in your terminal (`PLURAI_SANDBOX=strict` closes '
        'it), and any other one is switched off.'
    ),
    'rewire_deny': 'the simulated copy of your agent cannot call them.',
    'mock_or_refuse': 'each is replaced by the mock; a run with one left real and ungranted does not start.',
}

#: What the map says instead of `UNGRANTED_OUTCOME['confine']` for a Claude Code skill, whose harness
#: applies no `plurai.confine`, per `agent.action_channel`. `code`: the harness runs the skill's own code
#: through these built-ins, so nothing sandboxes them and no grant changes what they may do. `tools`: the
#: harness offers the skill none of them. Any other value (the channel not settled yet): nothing is promised.
SKILL_CHANNEL_OUTCOME: Mapping[str, str] = {
    'code': (
        'granted or not, they run unconfined: this skill runs its own code through them. Each scenario starts '
        'in a scratch folder outside your repo, the vendor calls the map mocks are routed to the mock, and your '
        'vendor keys are kept out of their environment, but they read, write and reach the network as you do.'
    ),
    'tools': 'this skill is given none of them: it acts only through its mocked tools.',
}
UNSETTLED_CHANNEL_OUTCOME = (
    "what they may do is settled when this skill's harness is written; until then nothing here promises "
    'that anything confines them.'
)


def action_channel_of(topology: dict[str, Any] | None) -> str | None:
    """The map's `agent.action_channel`, written for a Claude Code skill only (`tools` | `code`, or `unknown`
    before setup has inferred it); None for any other agent."""
    agent = topology.get('agent') if isinstance(topology, dict) else None
    channel = agent.get('action_channel') if isinstance(agent, dict) else None
    return channel if isinstance(channel, str) and channel else None


def ungranted_line(outcome: str, *, action_channel: str | None) -> str:
    """What the map says follows an ungranted tool's name, for this confiner `outcome`: what
    `UNGRANTED_OUTCOME` promises, except for a skill (`action_channel` set), whose harness applies no
    `plurai.confine`: there it says what the channel does, and promises nothing while it is unsettled."""
    if outcome == 'confine' and action_channel is not None:
        return SKILL_CHANNEL_OUTCOME.get(action_channel, UNSETTLED_CHANNEL_OUTCOME)
    return 'unless you grant them, ' + UNGRANTED_OUTCOME[outcome]


class UnclassifiedTools(ValueError):
    """A tool the bench runs for real that the map does not classify (`writes`, `effects`, `runs`)."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__(f'agent_topology.json does not classify {names}, so nobody knows what they do on this machine; {TYPING_REMEDY}')


def framework_id(declared: str) -> str:
    """The runtime id a declared framework names: the part before any ` · ` qualifier, normalised, so
    `Claude Agent SDK`, `claude-agent-sdk` and `claude_agent_sdk · ClaudeSDKClient` read as one id."""
    head = declared.split('·', 1)[0]
    return '_'.join(head.strip().lower().replace('-', ' ').split())


def intercepted_at_agent_host(topology: dict[str, Any] | None) -> bool:
    """Whether the map declares `agent.tool_interception.at: agent_host` with its `why` and the `route_smoke` that proves
    the route: the agent's own host (a rewired copy of the agent) calls the mock, so no harness binds its tools. Undeclared,
    unreadable or missing either key is the harness, so it fails closed."""
    agent = topology.get('agent') if isinstance(topology, dict) else None
    block = agent.get('tool_interception') if isinstance(agent, dict) else None
    return (isinstance(block, dict) and block.get('at') == 'agent_host'
            and all(str(block.get(key) or '').strip() for key in ('why', 'route_smoke')))


def runtime_of(topology: dict[str, Any] | None, config: dict[str, Any] | None) -> str:
    """The runtime id `CONFINERS` and `READ_ONLY_BUILTINS` are keyed by: `agent_host` when the map
    declares the tools intercepted there, else the config's `agent.framework`, else the map's."""
    if intercepted_at_agent_host(topology):
        return 'agent_host'
    for doc in (config, topology):
        agent = doc.get('agent') if isinstance(doc, dict) else None
        if isinstance(agent, dict) and str(agent.get('framework') or '').strip():
            return framework_id(str(agent['framework']))
    return ''


def confiner(runtime: str) -> str:
    """The runtime's confiner: its `CONFINERS` row, else `mock_or_refuse`."""
    return CONFINERS.get(runtime, DEFAULT_CONFINER)


def outcome_for(tool: dict[str, Any], *, runtime: str) -> str:
    """What stops this tool when ungranted: the runtime's confiner when it reaches the tool, else `mock_or_refuse`."""
    row = confiner(runtime)
    return row if _REACHES[row](tool) else DEFAULT_CONFINER


def _live(topology: dict[str, Any] | None) -> list[dict[str, Any]]:
    tools = topology.get('tools') if isinstance(topology, dict) else None
    return [t for t in tools if isinstance(t, dict) and t.get('name') and t.get('status') != 'mocked'] if isinstance(tools, list) else []


def _typed_rag(topology: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The live `rag` entries that carry any typed fact: a retrieval tool the map files under `rag`
    and types there. One that carries none is the grounding band's alone and classifies nothing."""
    rag = topology.get('rag') if isinstance(topology, dict) else None
    return [r for r in rag if isinstance(r, dict) and r.get('name') and r.get('status') != 'mocked'
            and {'writes', 'effects', 'runs'} & set(r)] if isinstance(rag, list) else []


def _typed_surfaces(topology: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every live map entry that classifies a tool: the live `tools`, and the typed live `rag` entries."""
    return _live(topology) + _typed_rag(topology)


def _classified(tool: dict[str, Any]) -> bool:
    effects = tool.get('effects')
    return (
        tool.get('writes') in WRITES
        and tool.get('runs') in RUNS
        and isinstance(effects, list)
        and bool(effects)
        and all(e in EFFECTS for e in effects)
        and ('none' not in effects or effects == ['none'])
    )


def unclassified_tools(topology: dict[str, Any] | None, config: dict[str, Any] | None) -> list[str]:
    """Every tool the bench would run for real that the map does not classify: a live map tool
    (or a typed `rag` entry) without a valid `writes` + `effects` + `runs`, and a config tool not
    `mode: mock` that the map does not list as a live tool or a typed live `rag` entry. Such a tool
    is refused, never run by default; no map classifies nothing."""
    live = {str(t['name']): t for t in _typed_surfaces(topology)}
    out = {name for name, tool in live.items() if not _classified(tool)}
    tools = (config or {}).get('tools')
    for name, entry in tools.items() if isinstance(tools, dict) else ():
        mode = entry.get('mode') if isinstance(entry, dict) else None
        if mode != 'mock' and str(name) not in live:
            out.add(str(name))
    return sorted(out)


def consent_required(topology: dict[str, Any], *, runtime: str) -> list[str]:
    """The live map tools that run for real only on the operator's grant: typed to act (`effects`
    other than `none`), typed a live writer, or a runtime built-in (`runs: subprocess`) off that
    runtime's read-only allow-list — whatever it was typed (on a runtime with built-ins, one of
    `HOST_EFFECT_TOOLS` typed otherwise is still one). Raises `UnclassifiedTools` for a live tool
    the map does not classify. A typed live `rag` entry counts as a tool."""
    live = _typed_surfaces(topology)
    if untyped := sorted(str(t['name']) for t in live if not _classified(t)):
        raise UnclassifiedTools(untyped)
    builtins = runtime in READ_ONLY_BUILTINS
    read_only = set(READ_ONLY_BUILTINS.get(runtime, ()))

    def builtin(tool: dict[str, Any]) -> bool:
        return tool['runs'] == 'subprocess' or (builtins and tool['name'] in HOST_EFFECT_TOOLS)

    return sorted({
        str(t['name']) for t in live
        if t['effects'] != ['none'] or t['writes'] == 'live' or (builtin(t) and t['name'] not in read_only)
    })


def typed_writes(topology: dict[str, Any] | None) -> dict[str, str]:
    """Each map tool's (or typed `rag` entry's) typed `writes`, by name (one that types none is absent)."""
    tools = topology.get('tools') if isinstance(topology, dict) else None
    rows = [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []
    return {str(t['name']): str(t['writes']) for t in [*rows, *_typed_rag(topology)] if t.get('name') and 'writes' in t}


def live_write_tools(config: dict[str, Any] | None, *, at_agent_host: bool, typed_writes: Mapping[str, str]) -> list[str]:
    """The config's tools that write a live system; each runs only on a grant in `.host_effects_consent`.

    Under `mode: observe` a tool is live unless the map types its writes `none`. Under `mode: real` a
    tool with a `writes_to` is live unless a seam mock `serves:` it; one without is live unless the map
    types its writes `none` or `world`; an explicit `write_strategy: real` makes it live whatever the map
    says. The map's typing never removes a declared write.

    A write into a confirmed store (`writer_readers.local_store_writers`: `world_state.owner: agent`,
    every type it writes in `owned_types`, its `writes_to` naming a `world_state.confirmation` target)
    is never live: the harness creates it, or the user confirmed a disposable copy, and it is reset for
    each conversation. It holds on the harness route only.

    `at_agent_host` (the map's `intercepted_at_agent_host`): there no harness patches a real tool's
    storage, so a `mode: real` write cannot be mocked, and any declared write is live, as under
    `observe`. `typed_writes` is the map's (`typed_writes`); both are required so no caller drops one."""
    import writer_readers  # noqa: PLC0415 - the ONE reading of which writes land in the local store, beside this module

    tools = (config or {}).get('tools')
    local = set() if at_agent_host else set(writer_readers.local_store_writers(config or {}))
    served = writer_readers.served_by_seam(config or {})
    out = []
    for name, entry in sorted(tools.items(), key=lambda item: str(item[0])) if isinstance(tools, dict) else ():
        if str(name) in local:
            continue
        mode = entry.get('mode') if isinstance(entry, dict) else None
        descriptor = entry.get('descriptor') if isinstance(entry, dict) and isinstance(entry.get('descriptor'), dict) else {}
        writes_to = bool(str(descriptor.get('writes_to') or '').strip())
        explicit = descriptor.get('write_strategy')
        declared_write = writes_to or explicit not in (None, '', 'stub')
        if at_agent_host:
            live = mode in ('real', 'observe') and declared_write
        elif mode == 'real':
            live = explicit == 'real' or (str(name) not in served if writes_to else typed_writes.get(str(name)) not in ('none', 'world'))
        else:
            live = mode == 'observe' and typed_writes.get(str(name)) != 'none'
        if live:
            out.append(str(name))
    return out


def typed_live_writers(topology: dict[str, Any] | None) -> list[str]:
    """The map's tools typed `writes: live` that run for real; each runs only on a grant, like a config live writer."""
    return sorted(str(t['name']) for t in _typed_surfaces(topology) if t.get('writes') == 'live')


#: The operator's grants, in the workdir beside `plurai.config.yaml`.
CONSENT_FILE = '.host_effects_consent'

#: Names `.host_effects_consent` grants that are no tool: the hosted route's bring-up (`route_smoke`) and the
#: Agentforce harness's upload of a handed-over file into the org (`file_uploads`). A report listing what ran
#: live leaves them out.
NON_TOOL_GRANTS = frozenset({'route_smoke', 'file_uploads'})


def granted(workdir: Path) -> frozenset[str]:
    """What `.host_effects_consent` grants: `granted: a, b` names them; anything else grants nothing.

    The same grammar as the hosted route's stdlib reader (`rewire_agentforce._granted`, which is
    copied into the customer tree and cannot import this); `test_host_effects.py` fails when they part."""
    try:
        text = (workdir / CONSENT_FILE).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return frozenset()
    head, sep, names = text.partition(':')
    return frozenset(n.strip() for n in names.split(',') if n.strip()) if sep and head.strip() == 'granted' else frozenset()


def native_tools(topology: dict[str, Any], config: dict[str, Any] | None) -> list[str]:
    """The live tools on the map that no plurai lane wraps: every non-mocked topology tool the
    config's `tools:` does not name (a Claude Agent SDK agent's built-ins, for one)."""
    wrapped = (config or {}).get('tools')
    wrapped = set(wrapped) if isinstance(wrapped, dict) else set()
    return sorted({str(t['name']) for t in _live(topology)} - wrapped)


#: The runtimes whose tools run where no plurai lane sees them (a Claude Agent SDK agent's CLI
#: subprocess). `agent.native_tools` is recorded only for these.
UNOBSERVED_RUNTIME_FRAMEWORKS = frozenset({'claude_agent_sdk'})


def recorded_native_tools(topology: dict[str, Any], config: dict[str, Any] | None) -> list[str] | None:
    """What the scan writes to `agent.native_tools`: an unobserved runtime's native tools, else
    `None` (nothing is written; its plurai lanes observe its tools)."""
    agent = (config or {}).get('agent')
    framework = framework_id(str(agent.get('framework') or '')) if isinstance(agent, dict) else ''
    return native_tools(topology, config) if framework in UNOBSERVED_RUNTIME_FRAMEWORKS else None
