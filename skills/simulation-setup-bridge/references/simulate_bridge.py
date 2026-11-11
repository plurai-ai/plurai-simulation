# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""
Plurai simulation harness: {agent name} ({runtime}), run through the bridge.

TEMPLATE: copy to plurai_sim/simulate.py and fill every {placeholder} and every `<...>` from the scan
(Steps 1-7). The agent runs UNCHANGED in one bridge child per scenario process (Node, started on the first
seed or turn), entered through bridge/entry.mjs. What it calls outside itself reaches the @plurai.mock
functions below through bridge/bridge.map.json; the protocol is
skills/simulation-setup-bridge/references/bridge-contract.md.

Wire per turn:  simulate.py -> child   turn {thread_id, turn_index, text, parts?, request}
                child -> simulate.py   ext_call {tool, args}   (0..n, served inline by bridge/host.py)
                child -> simulate.py   turn_done {text, trail, finish_reason, input_context?, appended?}

Safe under parallel children: one child per scenario process, no fixed port (the channel is a socketpair),
a per-session log (artifacts/bridge/<thread_id>.<pid>.log), and mkdtemp for any scratch space; the thread
key is plurai.active_session_id(), never a sibling's.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_DIR = HERE / 'bridge'
AGENT_ROOT = (HERE / json.loads((BRIDGE_DIR / 'bridge.map.json').read_text(encoding='utf-8'))['agent_root']).resolve()

# ================================================================
# PHASE 1: environment — exactly what production's start command loads, nothing more
# ================================================================
#: The env file production's start command loads (`node --env-file=.env`, `import 'dotenv/config'`), relative
#: to the agent root, citing that line; None when production loads none. A file the user named for the settings
#: (the bench's `harness.env_file`, written here as an absolute path) replaces it. It reaches the child through os.environ. Inert values for config
#: the agent reads at import go in the map's `env` block, never here.
AGENT_ENV_FILE = None  # e.g. '.env'  (package.json:12 "start": "node --env-file=.env dist/server.js")

if AGENT_ENV_FILE is not None:
    from dotenv import load_dotenv  # python-dotenv ships with the plurai SDK

    load_dotenv(AGENT_ROOT / AGENT_ENV_FILE)  # a variable already set in the environment wins

import plurai  # noqa: E402

#: The env var the agent resolves its model from (cite the line), recorded per turn in run_info.model.
MODEL_ENV = '{MODEL_ENV_VAR}'
#: The model production uses when MODEL_ENV is unset (cite the default), or None.
MODEL_DEFAULT = None

#: The agent_context fields declared `inject: request`: the values production's client sends on every call.
_REQUEST_KEYS = ()  # e.g. ('sessionToken', 'locale')

#: Per thread: the request values, and the next turn index.
_REQUEST = {}
_TURN_INDEX = {}


def _world():
    return plurai.get_simulation_context().world_state


# ================================================================
# PHASE 2: MOCKS — one @plurai.mock per tool the map routes to, named exactly as the config tool
# ================================================================
# simulation-setup-python Step 8 applies unchanged: DUMB bodies that read and write the world, rich
# descriptors in plurai.config.yaml, and a failure the real client reports RETURNED as Step 8 shows; for a
# tool declared `error_channel: raises`, the call throws a PluraiMockError carrying that message.
# Every parameter has a default: the bridge sends only the keys the call carried (`undefined` is dropped, a
# spread brings only the object's own keys), and a kwarg the signature requires but the call omitted is a
# HarnessFault locally and in the route probe, not a tool failure.
#
# @plurai.mock('orders_get')
# def orders_get(order_id=None):
#     return _world().get_entity(order_id)

#: Every mock above. The dispatch table the bridge serves ext_calls from is built from it, each through
#: plurai.mock_wrap, so every call reaches the session's mock and is recorded.
_MOCKS = ()

_MOCKED = {fn.__name__: plurai.mock_wrap(fn) for fn in _MOCKS}

# ================================================================
# PHASE 3: the bridge host — one per scenario process, the child spawned on first use
# ================================================================
#: The Node executable (Node >= 22.15).
_NODE = 'node'
_HOST = None


def _bridge_host():
    """The process's bridge host. Loaded by file path, never `import bridge.host` (plurai_sim/ is on sys.path)."""
    global _HOST
    if _HOST is None:
        spec = importlib.util.spec_from_file_location('_plurai_bridge_host', BRIDGE_DIR / 'host.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _HOST = module.BridgeHost(
            bridge_dir=BRIDGE_DIR,
            dispatch=_MOCKED,
            harness_fault=plurai.HarnessFault,
            mock_error=plurai.MockToolError,
            agent_fault=getattr(plurai, 'AgentFault', None),
            allow_hosts=module.load_allow_hosts(HERE / 'plurai.config.yaml'),
            node=_NODE,
        )
    return _HOST


def _declared_resume():
    """The map's `entry.resume`: 'seed', or {'unsupported': <reason>} for an entry with no seed export."""
    return json.loads((BRIDGE_DIR / 'bridge.map.json').read_text(encoding='utf-8'))['entry']['resume']


def _thread_id(config=None):
    tid = ((config or {}).get('configurable') or {}).get('thread_id') or plurai.active_session_id()
    if not tid:
        raise plurai.HarnessFault('no thread id: the conversation has no key for the agent\'s memory')
    return tid


def _remember_request(thread_id, values):
    request = _REQUEST.setdefault(thread_id, {})
    for key in _REQUEST_KEYS:
        if (values or {}).get(key) not in (None, ''):
            request[key] = values[key]
    return request


def _new_message(payload):
    """(text, parts) of the NEW user message; parts only when the content is a list, passed unconverted."""
    messages = payload.get('messages') or []
    if not messages:
        return '', None  # the opening call of an agent that speaks first (agent.io.may_open)
    message = messages[-1]
    content = message.get('content') if isinstance(message, dict) else getattr(message, 'content', '')
    if isinstance(content, list):
        text = '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
        return text, content
    return str(content or ''), None


# ================================================================
# PHASE 4: invoke_agent — the NEW user message only; history is the agent's, keyed on the thread
# ================================================================
async def invoke_agent(payload, config=None):
    thread_id = _thread_id(config)
    request = _remember_request(thread_id, payload)  # turn 1 carries agent_context spread into the payload
    text, parts = _new_message(payload)
    turn_index = _TURN_INDEX.get(thread_id, 0)
    _TURN_INDEX[thread_id] = turn_index + 1
    done = await _bridge_host().turn(thread_id=thread_id, turn_index=turn_index, text=text, parts=parts, request=dict(request))
    result = {
        'messages': [{'role': 'assistant', 'content': done.get('text') or ''}],
        'run_info': {
            'model': os.environ.get(MODEL_ENV) or MODEL_DEFAULT,
            'finish_reason': done.get('finish_reason'),
            'bridge': done['ledger'],
        },
    }
    # The trail is tri-state: a list (observed, maybe empty) is returned; null (cannot observe) leaves the key out.
    if isinstance(done.get('trail'), list):
        result['tool_trail'] = done['trail']
    # What the agent put into its own model input this turn, as it received it (forwarded, never re-read).
    if done.get('input_context') is not None:
        result['input_context'] = done['input_context']
    # Text the product's own code added to the reply after the model's output: it stays in the reply and is declared,
    # typed as the product's, so the judges do not read it as the agent's words.
    if done.get('appended'):
        result['appended_output'] = [{'source': 'product', 'site': e.get('site'), 'text': e.get('text')} for e in done['appended']]
    return result


# ================================================================
# PHASE 5: seed_state — the world is the mock's; request values; a resumed history goes in under the turns' keys
# ================================================================
async def seed_state(initial_agent_context, initial_world_state, resume_seed=None):
    # The world is the mock's: the backend seeds it into the session from initial_world_state['entities'] and every
    # read goes there through the bridge, so there is no harness-side store to write.
    entities = (initial_world_state or {}).get('entities') or {}
    thread_id = plurai.active_session_id()
    if entities and thread_id is None:
        raise plurai.HarnessFault('world entities were provided but no mock session is active')
    request = _remember_request(thread_id, initial_agent_context) if thread_id else {}
    if not resume_seed:
        return None
    if not thread_id:
        raise plurai.HarnessFault('no active session in seed_state: the resumed history has no thread to go into')
    if not resume_seed.get('messages'):
        raise plurai.HarnessFault('the resume seed carries no messages: there is no conversation to resume')
    point = resume_seed.get('resume_point')
    if point is not None:
        raise plurai.HarnessFault(
            f'resume_point pins the start node {point.get("node")!r}, and this harness drives no graph node: '
            're-convert the trace without --start-node'
        )
    resume = _declared_resume()
    if resume != 'seed':
        raise plurai.HarnessFault(f'this agent cannot resume a recorded conversation: the bridge entry exports no seed ({resume.get("unsupported")})')
    agent_state = resume_seed.get('agent_state') or {}
    # The same request values turn 1 sends: the entry keys the resumed thread exactly as its turns will address it.
    done = await _bridge_host().seed(thread_id=thread_id, messages=resume_seed['messages'], agent_state=agent_state, request=dict(request))
    if not done.get('saved'):
        raise plurai.HarnessFault('the resumed conversation did not reach the agent\'s memory for this thread')
    dropped = sorted(set(agent_state) - set(done.get('restored_state_keys') or []))
    return {'resume_state_dropped': dropped} if dropped else None
