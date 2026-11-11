"""The Python half of bridge protocol 1: drive the agent's bridge child from ``simulate.py``.

Copied VERBATIM to ``plurai_sim/bridge/host.py`` (a stale copy is refused by ``check_workdir_copies.py``).
``simulate.py`` loads it by file path (``spec_from_file_location('_plurai_bridge_host', ...)``), never as
``import bridge.host``. It imports nothing from ``plurai``: the harness hands it the classes it raises.

The normative text is ``skills/simulation-setup-bridge/references/bridge-contract.md`` (A.1-A.8); the shared values
(timeouts, limits, env names, message vocabulary) are read from ``bridge/runtime/constants.json``.

- Spawn is lazy (first ``seed`` / ``turn`` / ``route_probe``), through ``subprocess.Popen`` with one end of a
  ``socketpair`` passed by ``pass_fds``; the parent closes the child's end right after spawn.
- Every read races the child's exit (``proc.poll()``): a grandchild that inherited the socket keeps it open
  after the child dies, so EOF alone is never trusted.
- ``ext_call`` is served INLINE on the task awaiting the request, in arrival order, so the SDK session and turn
  contextvars reach the mock. There is no background reader.
- No respawn: after the child dies every later request is a harness fault naming the first death. A request
  cancelled mid-flight (the SDK's turn timeout) is a death too: the child is still inside it.
- A faulting event attributed to an earlier request faults the request that reads it; one still unread at
  teardown is written to the log and to stderr.
- Teardown is synchronous and bounded (shutdown, 1 s, SIGTERM to the group, 1 s, SIGKILL to the group).
- Every exception is raised OUTSIDE any ``except`` block: the SDK's provider check walks ``__context__``.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import datetime as _dt
import inspect
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ['BridgeAgentError', 'BridgeHost', 'BridgeProviderError', 'BridgeProviderUnreachable', 'load_allow_hosts']

_READ_CHUNK = 1 << 20
#: How long the host keeps reading after a terminal reply, for what the child sent right behind it.
_DRAIN_S = 0.05
_TYPE_RE = re.compile(rb'"type"\s*:\s*"([A-Za-z_]{1,40})"')
_LOG = logging.getLogger(__name__)


class BridgeProviderError(Exception):
    """The agent's model provider answered with a typed failure status (A.6 row 2): read as ``provider_infra``."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class BridgeProviderUnreachable(ConnectionError):
    """The agent's model provider could not be reached (A.6 row 3): read as ``provider_infra``."""


class BridgeAgentError(Exception):
    """The agent's code raised and the child could not attribute it to the agent (A.6 row 5), or the SDK has no
    ``AgentFault`` (row 4 degraded): read as ``other``."""


def load_allow_hosts(config_path: Path) -> list[str]:
    """The config's ``egress.allow_hosts`` as bare hostnames; ``[]`` when the file, the key or a list is absent.

    A malformed value reads as ``[]`` and logs a warning naming the file: no consent means MORE blocking, never less.
    """
    path = Path(config_path)
    if not path.is_file():
        return []
    import yaml  # noqa: PLC0415  # lazy: the host is otherwise stdlib only

    problem: str | None = None
    raw: Any = None
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        problem = f'it could not be read as YAML ({type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ""})'
    else:
        if doc is not None and not isinstance(doc, dict):
            problem = f'its top level is a {type(doc).__name__}, not a mapping'
        elif isinstance(doc, dict) and doc.get('egress') is not None:
            egress = doc['egress']
            if not isinstance(egress, dict):
                problem = f'egress is a {type(egress).__name__}, not a mapping'
            else:
                raw = egress.get('allow_hosts')
                if raw is not None and not isinstance(raw, list):
                    problem = f'egress.allow_hosts is a {type(raw).__name__}, not a list'
    if problem is not None:
        _LOG.warning('%s: %s; the bridge allows no extra egress hosts', path, problem)
        return []
    hosts: list[str] = []
    for entry in raw or []:
        text = str(entry).strip()
        if not text:
            continue
        if '://' in text:
            text = text.split('://', 1)[1]
        host = text.split('/', 1)[0].rsplit('@', 1)[-1]
        host = host[1:].split(']', 1)[0] if host.startswith('[') else host.split(':', 1)[0]
        host = host.lower()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


class _Died(Exception):
    """Internal: the child is gone (or the channel is unusable). Carries the death's description."""


class _Request:
    """What one host request saw while it awaited its terminal reply."""

    def __init__(self, rid: str, kind: str, ledger_keys: Sequence[str]) -> None:
        self.rid = rid
        self.kind = kind
        self.ledger = dict.fromkeys(ledger_keys, 0)
        #: A.6 row 1 causes, in arrival order: a faulting event or a harness ext_result, each one sentence.
        self.harness: list[str] = []
        #: The terminal reply arrived; what is read after it (the drain) is no longer in flight.
        self.finished = False


def _json_default(value: Any) -> Any:
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    raise TypeError(f'{type(value).__name__} is not JSON serializable')


def _safe_name(text: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '_', text)[:120] or 'bridge'


class BridgeHost:
    """One bridge child per scenario process, started on first use and never restarted."""

    def __init__(
        self,
        *,
        bridge_dir: Path,
        dispatch: Mapping[str, Callable[..., Any]],
        harness_fault: type[Exception],
        mock_error: type[Exception],
        agent_fault: type[Exception] | None,
        allow_hosts: Sequence[str] = (),
        node: str = 'node',
        log_dir: Path | None = None,
    ) -> None:
        self._bridge_dir = Path(bridge_dir).resolve()
        self._dispatch = dispatch
        self._harness_fault = harness_fault
        self._mock_error = mock_error
        self._agent_fault = agent_fault
        self._allow_hosts = [str(h) for h in allow_hosts]
        self._node = node
        self._log_dir = Path(log_dir) if log_dir is not None else self._bridge_dir.parent / 'artifacts' / 'bridge'
        self._constants: dict[str, Any] | None = None
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buffer = bytearray()
        self._pending: asyncio.Future | None = None
        self._log_path: Path | None = None
        self._log_file: Any = None
        self._seq = 0
        #: Every request id this host has sent: an event naming one that is not in flight is a late one.
        self._issued: set[str] = set()
        #: Late faulting events found at teardown, each one sentence (also written to the log and stderr).
        self.late_faults: list[str] = []
        self._death: str | None = None
        self._busy = False
        self._closed = False
        self._atexit = False
        self._torn_down = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._entry_exports: tuple[str, ...] | None = None

    # ------------------------------------------------------------------ public interface

    @property
    def entry_exports(self) -> tuple[str, ...] | None:
        """The entry module's exports, from ``pong``; ``None`` before the child started."""
        return self._entry_exports

    async def turn(self, *, thread_id: str, turn_index: int, text: str, parts: list | None, request: Mapping[str, Any]) -> dict:
        """One user turn. Returns the ``turn_done`` body (no ``type``/``id``) plus ``ledger``."""
        body: dict[str, Any] = {'thread_id': thread_id, 'turn_index': turn_index, 'text': text, 'request': dict(request)}
        if isinstance(parts, list):
            body['parts'] = parts
        reply, state = await self._call('turn', body, thread_id=thread_id)
        trail = reply.get('trail')
        if trail is not None and not isinstance(trail, list):
            raise self._fault(f'protocol: turn_done.trail is {type(trail).__name__}, not a list or null')
        out = {k: v for k, v in reply.items() if k not in ('type', 'id')}
        served = state.ledger.get('ext_calls', 0)
        if trail == [] and served > 0:
            # "Observed none" beside served external calls means the trail adapter did not see the agent's tools.
            self._note(f'turn {state.rid}: the child sent trail [] after serving {served} mapped call(s); the trail is read as null (unobserved)')
            out['trail'] = None
        out['ledger'] = dict(state.ledger)
        return out

    async def seed(self, *, thread_id: str, messages: list, agent_state: Mapping[str, Any] | None, request: Mapping[str, Any]) -> dict:
        """Write a resumed conversation into the agent's thread. Returns the ``seed_done`` body.

        ``request`` is the same values ``turn`` sends, so the entry keys the resumed thread exactly as its turns will.
        """
        body: dict[str, Any] = {'thread_id': thread_id, 'messages': list(messages), 'request': dict(request)}
        if agent_state is not None:
            body['agent_state'] = dict(agent_state)
        reply, _state = await self._call('seed', body, thread_id=thread_id)
        return {k: v for k, v in reply.items() if k not in ('type', 'id')}

    async def route_probe(self) -> dict:
        """The model-free route probe. Returns the ``route_probe_done`` body."""
        reply, _state = await self._call('route_probe', {}, thread_id='route_probe')
        return {k: v for k, v in reply.items() if k not in ('type', 'id')}

    def close(self) -> None:
        """Synchronous, idempotent, at most ~2.5 s: shutdown, 1 s, SIGTERM to the group, 1 s, SIGKILL to the group."""
        if self._closed:
            return
        self._closed = True
        if self._death is None and self._proc is not None:
            self._death = 'the bridge was closed'
        self._teardown(polite=True)

    # ------------------------------------------------------------------ request cycle

    async def _call(self, kind: str, body: dict[str, Any], *, thread_id: str) -> tuple[dict, _Request]:
        if self._closed:
            raise self._fault(f'the bridge host is closed; no {kind} can run after teardown')
        if self._death is not None:
            raise self._fault(
                f'the bridge child already died ({self._death}); it is never restarted inside a scenario process, '
                f'because a restart would silently reset the agent\'s memory and in-memory stores, so this {kind} cannot run'
            )
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise self._fault(f'the bridge channel belongs to the event loop that started it; this {kind} runs on another one')
        if self._busy:
            raise self._fault(f'a second host request ({kind}) while one is in flight; the bridge serves one request at a time')
        self._busy = True
        try:
            outcome = await self._call_inner(kind, body, thread_id=thread_id)
        except asyncio.CancelledError:
            self._mark_dead(_cancelled(kind))
            raise
        finally:
            self._busy = False
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def _call_inner(self, kind: str, body: dict[str, Any], *, thread_id: str) -> Any:
        """The reply and the request state, or the exception to raise (built here, raised by the caller outside
        every ``except`` block)."""
        started = await self._ensure_started(thread_id)
        if started is not None:
            return started
        constants = self._const()
        deadline = None
        if kind == 'route_probe':
            deadline = time.monotonic() + float(constants['timeouts_s']['route_probe'])
        died: str | None = None
        reply: dict | None = None
        state: _Request | None = None
        try:
            reply, state = await self._roundtrip(kind, body, deadline=deadline)
        except _Died as exc:
            died = str(exc)
        except _Refused as exc:
            return self._fault(str(exc))
        if died is not None:
            self._mark_dead(f'{died} mid-{kind}')
            return self._fault(f'the bridge child {died} mid-{kind}')
        assert reply is not None and state is not None
        return self._classify(kind, reply, state)

    async def _roundtrip(self, kind: str, body: dict[str, Any], *, deadline: float | None) -> tuple[dict, _Request]:
        rid = self._next_id()
        state = _Request(rid, kind, self._const()['ledger_keys'])
        await self._send({'type': kind, 'id': rid, **body}, what=kind)
        self._issued.add(rid)
        terminal = self._const()['messages']['terminal_for'][kind]
        while True:
            msg = await self._read(deadline=deadline, what=f'{terminal} for {kind}')
            if await self._absorb(msg, state):
                continue
            mtype = msg.get('type')
            if msg.get('id') != rid or mtype not in (terminal, 'error'):
                raise _Died(f'broke the protocol (a {mtype!r} reply with id {msg.get("id")!r} while {rid} awaited {terminal})')
            state.finished = True
            await self._drain(state)
            return msg, state

    async def _absorb(self, msg: dict, state: _Request) -> bool:
        """Serve an ``ext_call`` or note an ``event``; ``False`` for anything else. An id-less ``error`` is fatal."""
        mtype = msg.get('type')
        if mtype == 'ext_call':
            await self._serve(msg, state)
            return True
        if mtype == 'event':
            self._note_event(msg, state)
            return True
        if mtype == 'error' and msg.get('id') is None:
            raise _Died(f'reported a fatal error ({msg.get("kind")}: {msg.get("message")})')
        return False

    async def _drain(self, state: _Request) -> None:
        """Read what the child sent right behind a terminal reply, for at most ``_DRAIN_S``: a faulting event the
        agent's code raised after its entry returned still faults this request."""
        assert self._proc is not None
        reader, _writer = await self._channel()
        end = time.monotonic() + _DRAIN_S
        while True:
            newline = self._buffer.find(b'\n')
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                msg = self._parse(raw)
                if not await self._absorb(msg, state):
                    raise _Died(f'broke the protocol (a {msg.get("type")!r} reply with id {msg.get("id")!r} after {state.rid} had its reply)')
                continue
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            if self._pending is None:
                self._pending = asyncio.ensure_future(reader.read(_READ_CHUNK))
            done, _ = await asyncio.wait({self._pending}, timeout=remaining)
            if not done:
                return  # the read stays pending; the next request's first read picks it up
            task, self._pending = self._pending, None
            chunk = task.result()
            if not chunk:
                return  # EOF: the next request sees the death
            self._buffer.extend(chunk)

    def _classify(self, kind: str, reply: dict, state: _Request) -> Any:
        """A.6, first match wins. Returns the reply and state, or the exception to raise."""
        if state.harness:
            return self._fault(f'{kind} {state.rid}: ' + '; '.join(state.harness))
        if reply.get('type') != 'error':
            return reply, state
        err_kind = reply.get('kind')
        message = str(reply.get('message') or '')
        stack = reply.get('stack')
        detail = f'{message}\n{stack}' if isinstance(stack, str) and stack.strip() else message
        if err_kind != 'agent_raised' or kind != 'turn':
            return self._fault(f'{kind} {state.rid} failed in the bridge child ({err_kind}): {detail}')
        provider = reply.get('provider') if isinstance(reply.get('provider'), dict) else None
        if provider is not None:
            status = provider.get('status')
            typed = isinstance(status, int) and not isinstance(status, bool)
            if typed and (status in self._const()['provider_statuses'] or 500 <= status <= 599):
                return BridgeProviderError(f'the agent\'s model provider {provider.get("host")} answered {status}: {detail}', status)
            if provider.get('network') is True:
                return BridgeProviderUnreachable(f'the agent\'s model provider {provider.get("host")} was unreachable: {detail}')
        if reply.get('owner') == 'agent' and self._agent_fault is not None:
            return self._agent_fault(detail)
        return BridgeAgentError(detail)

    # ------------------------------------------------------------------ ext_call serving (A.4.1-A.4.4)

    async def _serve(self, msg: dict, state: _Request) -> None:
        req = msg.get('req')
        key = 'ext_calls' if req is not None and req == state.rid and not state.finished else 'out_of_turn'
        if key in state.ledger:
            state.ledger[key] += 1
        tool = msg.get('tool')
        result = await self._dispatch_call(tool, msg.get('args'))
        reply: dict[str, Any] = {'type': 'ext_result', 'id': msg.get('id'), **result}
        line = self._encode(reply)
        if isinstance(line, str):
            reply = {'type': 'ext_result', 'id': msg.get('id'), 'ok': False, 'error': {'kind': 'harness', 'message': f'{tool}: {line}'}}
            result = reply
            line = self._encode(reply)
            assert isinstance(line, bytes)
        if not result.get('ok') and result['error']['kind'] == 'harness':
            state.harness.append(f'the mock dispatch of {tool!r} failed in the harness ({result["error"]["message"]})')
        await self._write(line)

    async def _dispatch_call(self, tool: Any, args: Any) -> dict[str, Any]:
        fn = self._dispatch.get(tool) if isinstance(tool, str) else None
        if fn is None:
            return {'ok': False, 'error': {'kind': 'harness', 'message': f'no mock in the dispatch table for tool {tool!r}'}}
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {'ok': False, 'error': {'kind': 'harness', 'message': f'{tool}: args is {type(args).__name__}, not an object'}}
        # Bind first, so a TypeError the mock's body raises is never mistaken for args that do not fit.
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            signature = None  # no introspectable signature: the call itself is the check
        if signature is not None:
            # Against a deployed mock the body never runs (the deployed handler answers), so only a name the mock does not
            # take is the harness's; a key the call left out goes to that handler. Locally and in the route probe the body
            # runs, so the args bind in full.
            bind_args = signature.bind_partial if self._deployed_mock() else signature.bind
            try:
                bind_args(**args)
            except TypeError as exc:
                return {'ok': False, 'error': {'kind': 'harness', 'message': f'{tool}: the args do not fit the mock ({exc})'}}
        try:
            value = fn(**args)
            if inspect.isawaitable(value):
                value = await value
        except self._mock_error as exc:
            text = getattr(exc, 'message', None)
            return {'ok': False, 'error': {'kind': 'mock_error', 'message': str(text if isinstance(text, str) else exc)}}
        except Exception as exc:  # noqa: BLE001  # a mock body bug is the harness's, reported on the wire
            return {'ok': False, 'error': {'kind': 'harness', 'message': f'{tool}: the mock raised {type(exc).__name__}: {exc}'}}
        return {'ok': True, 'value': value}

    @staticmethod
    def _deployed_mock() -> bool:
        """Whether this process's mock is a deployed one (``plurai.configure`` in connect mode: a mock service url and no
        in-process ``_local_service``, the attribute the SDK's own child reads). Read off the SDK module already loaded,
        never imported; anything it cannot tell keeps the full bind."""
        sdk = sys.modules.get('plurai')
        url = getattr(sdk, 'mock_service_url', None)
        try:
            return callable(url) and url() is not None and getattr(sdk, '_local_service', None) is None
        except Exception:  # noqa: BLE001  # an SDK that cannot answer keeps the full bind
            return False

    def _note_event(self, msg: dict, state: _Request) -> None:
        kind = msg.get('kind')
        count = msg.get('count')
        count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 1 else 1
        key = {'egress_refused': 'refused_egress', 'unmapped_call': 'unmapped', 'unsupported_shape': 'unmapped', 'sink': 'sink'}.get(kind)
        if key in state.ledger:
            state.ledger[key] += count
        req = msg.get('req')
        faulting = kind in self._const()['faulting_event_kinds']
        # Attributed to this request, or emitted while the entry loads (ping): fail closed even if the agent swallowed it.
        if faulting and (req == state.rid or state.kind == 'ping'):
            late = f', after the {state.kind} had its reply' if state.finished else ''
            state.harness.append(_describe_event(msg) + late)
        elif faulting and req in self._issued:
            # Attributed to an earlier request that already returned: the request reading it is the first that can fail.
            state.harness.append(f'{_describe_event(msg)}, late from the earlier request {req}')
        elif kind not in self._const()['event_kinds']:
            state.harness.append(f'protocol: an event of unknown kind {kind!r}')

    # ------------------------------------------------------------------ start (A.1, A.8)

    async def _ensure_started(self, thread_id: str) -> Exception | None:
        if self._proc is not None:
            return None
        problem = self._spawn(thread_id)
        if problem is not None:
            self._mark_dead(problem)
            return self._fault(problem)
        constants = self._const()
        ready_s = self._timeout(constants['env']['ready_timeout'], constants['timeouts_s']['ready'])
        start_s = self._timeout(constants['env']['start_timeout'], constants['timeouts_s']['start'])
        died: str | None = None
        try:
            ready = await self._read(deadline=time.monotonic() + ready_s, what='ready')
            if ready.get('type') == 'error' and ready.get('id') is None:
                raise _Died(f'reported a fatal error instead of ready ({ready.get("kind")}: {ready.get("message")})')
            if ready.get('type') != 'ready' or ready.get('protocol') != constants['protocol']:
                raise _Died(f'sent {ready.get("type")!r} (protocol {ready.get("protocol")!r}) instead of ready (protocol {constants["protocol"]})')
            reply, state = await self._roundtrip('ping', {}, deadline=time.monotonic() + start_s)
        except asyncio.CancelledError:
            self._mark_dead(_cancelled('start'))
            raise
        except _Died as exc:
            died = f'did not start: it {exc}'
        except _Refused as exc:
            died = f'did not start: {exc}'
        if died is not None:
            self._mark_dead(died)
            return self._fault(f'the bridge child {died}')
        if state.harness or reply.get('type') == 'error':
            why = '; '.join(state.harness) or f'{reply.get("kind")}: {reply.get("message")}' + (f'\n{reply["stack"]}' if reply.get('stack') else '')
            self._mark_dead(f'did not start ({why})')
            return self._fault(f'the bridge child did not start: {why}')
        exports = reply.get('entry_exports')
        self._entry_exports = tuple(str(e) for e in exports) if isinstance(exports, list) else ()
        return None

    def _spawn(self, thread_id: str) -> str | None:
        """Start the child. Returns why it could not, or ``None``."""
        if os.name == 'nt':
            return 'a Windows host cannot run the bridge (no pass_fds)'
        runtime = self._bridge_dir / 'runtime'
        try:
            self._constants = json.loads((runtime / 'constants.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            return f'the bridge runtime is missing or unreadable ({runtime / "constants.json"}: {exc}); re-run the bridge runtime install step'
        constants = self._constants
        map_path = self._bridge_dir / 'bridge.map.json'
        try:
            doc = json.loads(map_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            return f'the bridge map is missing or unreadable ({map_path}: {exc})'
        if not isinstance(doc, dict) or doc.get('schema') != constants['map_schema'] or doc.get('child') not in constants['children']:
            return f'{map_path} is not a {constants["map_schema"]} map for a shipped child ({constants["children"]})'
        missing = sorted(t for t in _mapped_tools(doc) if t not in self._dispatch)
        if missing:
            return f'the bridge map routes to tools the dispatch table does not mock: {", ".join(missing)}'
        agent_root = (self._bridge_dir.parent / str(doc.get('agent_root') or '.')).resolve()
        env = dict(os.environ)
        rows = doc.get('env') if isinstance(doc.get('env'), dict) else {}
        reserved = constants['child_env_prefix']
        forbidden = set(constants['map_env_forbidden'])
        dropped: list[str] = []
        for name, row in rows.items():
            if not (isinstance(row, dict) and isinstance(row.get('value'), str)):
                continue
            if str(name).startswith(reserved) or name in forbidden:
                dropped.append(str(name))
                continue
            env[name] = row['value']
        argv = [self._node, '--import', str(runtime / 'register.mjs'), str(runtime / 'child.mjs')]
        log_path = self._log_dir / f'{_safe_name(thread_id)}.{os.getpid()}.log'
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = log_path.open('ab', buffering=0)
            self._log_path = log_path
            self._log_file.write(f'--- bridge start: {" ".join(argv)} (cwd {agent_root}) ---\n'.encode())
        except OSError as exc:
            return f'the bridge log could not be opened ({log_path}: {exc})'
        for name in dropped:
            self._note(f'map env {name} dropped: {reserved}* and {", ".join(sorted(forbidden))} are the bridge runtime\'s own, never the map\'s')
        names = constants['env']
        try:
            parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as exc:
            return f'the bridge channel could not be created (socketpair: {exc})'
        env[names['fd']] = str(child.fileno())
        env[names['map']] = str(map_path)
        env[names['protocol']] = str(constants['protocol'])
        env[names['allow_hosts']] = json.dumps(self._allow_hosts)
        failure: str | None = None
        try:
            self._proc = subprocess.Popen(  # noqa: S603  # the argv is the bridge's own runtime
                argv,
                cwd=str(agent_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=self._log_file,
                stderr=self._log_file,
                start_new_session=True,
                pass_fds=(child.fileno(),),
            )
        except OSError as exc:
            failure = f'could not be spawned ({self._node}: {exc})'
        finally:
            child.close()  # the parent's copy of the child end: a dead child must be able to yield EOF
        if failure is not None:
            parent.close()
            return f'the bridge child {failure}'
        self._sock = parent
        if not self._atexit:
            atexit.register(self.close)
            self._atexit = True
        return None

    # ------------------------------------------------------------------ channel (A.2)

    async def _channel(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self._reader is None or self._writer is None:
            assert self._sock is not None
            self._loop = asyncio.get_running_loop()
            self._reader, self._writer = await asyncio.open_unix_connection(sock=self._sock, limit=int(self._const()['max_line_bytes']))
        return self._reader, self._writer

    def _encode(self, msg: dict) -> bytes | str:
        """The wire line, or why it cannot be sent (unserializable, NaN, over the line limit)."""
        try:
            data = json.dumps(msg, allow_nan=False, default=_json_default, ensure_ascii=False).encode('utf-8') + b'\n'
        except (TypeError, ValueError) as exc:
            return f'the value is not serializable for the bridge ({exc})'
        limit = int(self._const()['max_line_bytes'])
        if len(data) - 1 > limit:
            return f'protocol: a {msg.get("type")} message of {len(data) - 1} bytes is over the {limit}-byte line limit'
        return data

    async def _send(self, msg: dict, *, what: str) -> None:
        line = self._encode(msg)
        if isinstance(line, str):
            raise _Refused(f'{what}: {line}')
        await self._write(line)

    async def _write(self, line: bytes) -> None:
        _reader, writer = await self._channel()
        try:
            writer.write(line)
            await writer.drain()
        except (ConnectionError, OSError) as exc:
            raise _Died(f'closed its channel ({type(exc).__name__})') from None

    async def _read(self, *, deadline: float | None, what: str) -> dict:
        """The next message, racing the child's exit (and ``deadline``, when one applies)."""
        assert self._proc is not None
        reader, _writer = await self._channel()
        limit = int(self._const()['max_line_bytes'])
        poll = float(self._const()['timeouts_s']['exit_poll'])
        exited_at: float | None = None
        while True:
            newline = self._buffer.find(b'\n')
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if len(raw) > limit:
                    raise _Died(self._oversize(raw, limit))
                return self._parse(raw)
            if len(self._buffer) > limit:
                raise _Died(self._oversize(bytes(self._buffer[:4096]), limit, size=len(self._buffer)))
            if self._pending is None:
                self._pending = asyncio.ensure_future(reader.read(_READ_CHUNK))
            timeout = poll
            if deadline is not None:
                timeout = max(0.0, min(poll, deadline - time.monotonic()))
            done, _ = await asyncio.wait({self._pending}, timeout=timeout)
            if done:
                task, self._pending = self._pending, None
                chunk = task.result()
                if not chunk:
                    _wait(self._proc, poll)
                    code = self._proc.poll()
                    raise _Died(f'exited (code {code})' if code is not None else 'closed its channel')
                self._buffer.extend(chunk)
                continue
            code = self._proc.poll()
            if code is not None:
                # Drain what it sent before it died, for one more poll; a grandchild may hold the socket open,
                # so EOF is never waited for.
                if exited_at is None:
                    exited_at = time.monotonic()
                elif time.monotonic() - exited_at >= poll:
                    raise _Died(f'exited (code {code})')
                continue
            if deadline is not None and time.monotonic() >= deadline:
                raise _Died(f'sent no {what} within its timeout')

    def _parse(self, raw: bytes) -> dict:
        try:
            msg = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise _Died(f'sent a line that is not JSON ({exc}): {raw[:200]!r}') from None
        if not isinstance(msg, dict) or msg.get('type') not in self._const()['messages']['child_to_host']:
            kind = msg.get('type') if isinstance(msg, dict) else type(msg).__name__
            raise _Died(f'sent a message of unknown type {kind!r}')
        return msg

    @staticmethod
    def _oversize(head: bytes, limit: int, *, size: int | None = None) -> str:
        match = _TYPE_RE.search(head[:4096])
        mtype = match.group(1).decode() if match else 'unknown'
        size_text = f'{size}+' if size is not None else str(len(head))
        return f'broke the protocol: a {mtype} message of {size_text} bytes is over the {limit}-byte line limit'

    # ------------------------------------------------------------------ death, teardown, faults

    def _mark_dead(self, why: str) -> None:
        if self._death is None:
            self._death = why
        self._teardown(polite=False)

    def _teardown(self, *, polite: bool) -> None:
        if self._torn_down:
            return
        self._torn_down = True
        proc = self._proc
        if proc is not None:
            grace = self._const().get('timeouts_s', {}) if self._constants else {}
            if polite and proc.poll() is None and self._sock is not None:
                with contextlib.suppress(OSError):
                    self._sock.send(json.dumps({'type': 'shutdown', 'id': self._next_id()}).encode() + b'\n')
                _wait(proc, float(grace.get('shutdown_grace', 1)))
            if proc.poll() is None:
                _killpg(proc.pid, signal.SIGTERM)
                _wait(proc, float(grace.get('sigterm_grace', 1)))
            # Always: the agent's grandchildren share the group and outlive a leader that exited on its own.
            _killpg(proc.pid, signal.SIGKILL)
            _wait(proc, 0.3)
            self._surface_late_faults()
        if self._pending is not None:
            with contextlib.suppress(Exception):
                self._pending.cancel()
            self._pending = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._log_file is not None:
            with contextlib.suppress(OSError):
                self._log_file.close()
            self._log_file = None

    def _surface_late_faults(self) -> None:
        """At teardown, with the child gone: read what it sent that no request read, and report every faulting event
        attributed to a request this host sent. No request is in flight to fail, so the log and stderr carry it."""
        data = bytearray(self._buffer)
        self._buffer.clear()
        task = self._pending
        if task is not None and task.done() and not task.cancelled() and task.exception() is None:
            data += task.result()
            self._pending = None
        if self._reader is not None:
            data += bytes(getattr(self._reader, '_buffer', b''))
        limit = int(self._const()['max_line_bytes']) if self._constants else 1 << 26
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.setblocking(False)
                while len(data) <= 2 * limit:
                    chunk = self._sock.recv(_READ_CHUNK)
                    if not chunk:
                        break
                    data += chunk
        faulting = self._const()['faulting_event_kinds'] if self._constants else ()
        for raw in bytes(data).split(b'\n'):
            try:
                msg = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                continue
            if not (isinstance(msg, dict) and msg.get('type') == 'event' and msg.get('kind') in faulting):
                continue
            if msg.get('req') not in self._issued:
                continue
            text = f'{_describe_event(msg)}, late from request {msg.get("req")}: it arrived after that request returned and no later request read it'
            self.late_faults.append(text)
            self._note(f'late fault: {text}')
            print(f'plurai bridge: {text} (log {self._log_path})', file=sys.stderr, flush=True)

    def _note(self, text: str) -> None:
        """One host line in the per-session log (the child's stdout and stderr share it)."""
        if self._log_file is not None:
            with contextlib.suppress(OSError, ValueError):
                self._log_file.write(f'--- bridge: {text} ---\n'.encode())

    def _next_id(self) -> str:
        self._seq += 1
        prefix = self._const()['id_prefix']['host'] if self._constants else 'r'
        return f'{prefix}{self._seq}'

    def _fault(self, message: str) -> Exception:
        return self._harness_fault(f'{message}\n{self._tail()}')

    def _tail(self) -> str:
        if self._log_path is None:
            return '--- no bridge log (the child never started) ---'
        lines = int(self._constants['log_tail_lines']) if self._constants else 60
        try:
            text = self._log_path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return f'--- bridge log {self._log_path} unreadable ---'
        tail = '\n'.join(text.splitlines()[-lines:])
        return f'--- bridge log tail ({self._log_path}, last {lines} lines) ---\n{tail}'

    def _const(self) -> dict[str, Any]:
        assert self._constants is not None, 'constants are read at spawn'
        return self._constants

    @staticmethod
    def _timeout(env_name: str, default: float) -> float:
        raw = os.environ.get(env_name, '').strip()
        try:
            value = float(raw) if raw else float(default)
        except ValueError:
            value = float(default)
        return value if value > 0 else float(default)


class _Refused(Exception):
    """Internal: a host message could not be sent (unserializable or over the line limit). The child is intact."""


def _mapped_tools(doc: Mapping[str, Any]) -> set[str]:
    """Every tool the map routes to: ``packages.*.calls.*.tool``, ``modules.*.proxy.calls.*.tool``, ``http[*].tool``
    (the rule ``bridge_map.mapped_tools`` documents; re-derived here because the host is copied alone)."""
    tools: set[str] = set()
    owners = list((doc.get('packages') or {}).values()) + [
        m.get('proxy') or {} for m in (doc.get('modules') or {}).values() if isinstance(m, dict)
    ]
    for owner in owners:
        calls = owner.get('calls') if isinstance(owner, dict) else None
        for call in (calls or {}).values():
            if isinstance(call, dict) and isinstance(call.get('tool'), str):
                tools.add(call['tool'])
    for route in doc.get('http') or []:
        if isinstance(route, dict) and isinstance(route.get('tool'), str):
            tools.add(route['tool'])
    return tools


def _describe_event(msg: Mapping[str, Any]) -> str:
    """One faulting event as a sentence fragment: kind, shape, what, via, site, count."""
    count = msg.get('count')
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 1 else 1
    what = msg.get('host') or msg.get('path') or '?'
    shape = f' ({msg["shape"]})' if msg.get('shape') else ''
    site = f' at {msg["site"]}' if msg.get('site') else ''
    return f'{msg.get("kind")}{shape}: {what} via {msg.get("via")}{site} (x{count})'


def _cancelled(kind: str) -> str:
    return (
        f'a {kind} was cancelled mid-flight; the child is still inside it and its reply would answer the next request, '
        'so the channel is closed'
    )


def _wait(proc: subprocess.Popen, seconds: float) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=seconds)


def _killpg(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)
