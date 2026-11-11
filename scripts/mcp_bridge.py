#!/usr/bin/env python3
"""The ``plurai`` MCP server on Copilot and Codex: a stdio server that forwards to the backend.

Copilot and Codex read a plugin's MCP url from a manifest and expand no variable in it, so the backend
cannot be named there. This process is the server the manifest starts instead. The
harness launches it, it resolves the backend (``_login_store.resolve_backend``: the ``PLURAI_URL`` /
``PLURAI_TOKEN`` pair of the environment the harness was launched with, else the saved login) and it forwards
MCP over streamable HTTP to ``<PLURAI_URL>/mcp``. There is no default address. Tool names, results and the hook matchers are
the backend's own and do not change. The Claude Code manifest does not use it: its url takes the variable.

Where the bearer may go. One origin, pinned at start: the scheme, host and port of
the resolved backend, parsed with the session pin's rules. It must
be https, or http to a loopback host. The url may carry no userinfo, no ``%`` in the authority, no
backslash or whitespace, no port 0, and only a plain path (no ``..``, ``//``, ``%``, ``@``, query or
fragment). Every request is made to that exact url, re-parsed before the bearer is attached, and no
redirect is followed: a 3xx is an error. A tool call carries arguments to the backend and nothing that
names a destination.

Where the token comes from. The resolved pair (``hooks/_login_store.py``): the environment's, else the private
credential file's. The token is never written to stdout or stderr. A loopback backend is dialled
without a bearer when there is no token; a remote one is refused.

What is added. A job start or an upload carries the command run id of the command in progress (``hooks/_command_run.py``),
unless the call names one. Nothing else is changed in a call.

What is forwarded. Requests ``initialize``, ``ping``, ``tools/list``, ``tools/call`` and the
notifications ``notifications/initialized`` and ``notifications/cancelled``; every other method is
answered with "method not found" (or dropped) and never sent. ``tools/call`` goes out only for a tool
the backend itself listed. The client's capabilities are not passed on, so the backend asks nothing back;
a request the backend sends anyway is refused to the backend and not relayed. Backend notifications are
relayed only for progress and log messages.

What comes back. The backend holds the bearer and answers every request, so its words are untrusted input
to a process whose stdout an MCP client parses. Every reply is rebuilt as ``jsonrpc``, ``id`` and one of
``result``/``error``; any other key, a ``method`` above all, is dropped. A body or event that does not
parse (nesting that overruns the parser included) is an error for that request, and no text from the
backend reaches a header: the protocol version is a date and the session id is printable ASCII, or
neither is used. A host must be ASCII (punycode spelled out), and any exception from the network layer
is an error for the request. A call is cut off when its answer exceeds ``MAX_RESPONSE_BYTES`` (counted
as it is read, a single line included) or when it has run for twice its per-read timeout, whatever the
backend sends in between; the TLS handshake is bounded by the per-read timeout alone.

A reply cut in transit. When the backend's ``initialize`` result advertises
``capabilities.experimental["ai.plurai/request-key"]`` (``{"version": 1}``), read again for every new
session, every ``tools/call`` carries a fresh random request key in its ``_meta`` (``ai.plurai/request-key``,
32 hex characters, replacing any the client set); which calls the backend reads it on is the backend's
business, not the bridge's. When the reply to a call is lost in transit (the stream ends, or is reset,
before the reply; the connection is reset before the headers; a proxy answers 502 or 504), the bridge asks
once more, under the SAME request key, a fresh JSON-RPC id and what is left of the call's time, only for a
tool whose ``tools/list`` entry carries the standard MCP annotation ``idempotentHint: true``: the backend
declares the repeat safe, and the shared key lets it answer with the first call's result. Every other call
surfaces the cut as an error, as it would without a key. A cancel from the client reaches whichever backend
request is live. A deadline, a 4xx or a 503 is never asked again, and a backend that does not advertise
request keys gets every call exactly as before: no key, and no call asked again.

A refused configuration still starts, so the harness shows the reason: ``tools/list`` holds one tool,
``bridge_status``, and every call returns the reason.

A login made after the bridge started. While the configuration is refused, every request resolves the backend again
(``configure``). Once a login resolves, the bridge starts its backend session with the handshake the client sent,
answers the request that found the login, and sends ``notifications/tools/list_changed`` once, so a client that
honours it lists the backend's tools without a restart. The bridge advertises ``tools.listChanged`` in its
``initialize`` for that reason. A client that does not honour the notification keeps the one-tool list until it
restarts. The ``bridge_status`` tool is the cheap request that makes the bridge look again.

Stdlib only. The python3 floor is 3.9, as for the hooks.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import queue
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# `python3 -I` (how the manifests start this file) leaves the script directory off sys.path.
_PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN / "hooks"))
sys.path.insert(0, str(_PLUGIN / "scripts"))

import _client_handshake
import _command_run
import _placeholder
import _login_store as credentials
import _no_redirect
import _run_driver
import session_pin
import url_guard

PROTOCOL_VERSION = "2025-06-18"

REQUESTS = frozenset({"initialize", "ping", "tools/list", "tools/call"})
NOTIFICATIONS_IN = frozenset({"notifications/initialized", "notifications/cancelled"})
#: What the backend may send the client unprompted. Anything else is dropped.
NOTIFICATIONS_OUT = frozenset({"notifications/progress", "notifications/message"})

#: A tool name as the MCP spec lets one be spelled. A listed name outside this is not offered or forwarded.
TOOL_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")

#: A protocol version as the spec spells it. Only a string of this shape is put in a header.
PROTOCOL_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: A session id as the spec lets one be spelled: visible ASCII, so it cannot carry a header break.
SESSION_PATTERN = re.compile(r"[\x21-\x7e]{1,256}")

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
#: What one answer may hold. Parsed, a body takes 5 to 25 times its size in memory (a long string at the low end, many small
#: arrays at the high end), so the cap is also the bound on this process's memory.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_PAGES = 20
#: The longest a backend may stay silent, per call. A call as a whole ends at twice this.
CALL_TIMEOUT = 900
QUICK_TIMEOUT = 60
#: How much of an answer one read asks for; the cap is checked after each.
READ_CHUNK = 1024 * 1024
LINE_CHUNK = 64 * 1024

#: The ``_meta`` key that carries a call's request key, and the experimental capability that says the backend honours it.
REQUEST_KEY = "ai.plurai/request-key"
REQUEST_KEY_VERSION = 1
#: What the client is told when the reply to a call asked again was cut twice.
CUT_TWICE = "the reply was cut in transit; the call may have run on the backend. Call it again with the same arguments."

STATUS_TOOL = {
    "name": "bridge_status",
    "description": "Why the Plurai backend is not reachable from this session, and what to change.",
    "inputSchema": {"type": "object", "properties": {}},
}

Origin = tuple


# --- configuration -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    """What the launch environment says: the url, its pinned origin, the token, or why none of it may be used."""

    url: str = ""
    origin: Origin | None = None
    token: str = ""
    source: str = "none"
    refusal: str = ""
    notes: list = field(default_factory=list)


def configure(env: os._Environ[str] | dict[str, str]) -> Config:
    backend = credentials.resolve_backend(env)
    if not backend.configured:
        return Config(refusal=f"refused: {backend.problem or credentials.MISSING_CONFIGURATION_MESSAGE}")
    url = f"{backend.url}/mcp"
    found, why = credentials.backend_origin(url)
    if found is None:
        return Config(refusal=f"refused: {why}.")
    if backend.token:
        return Config(url=url, origin=found, token=backend.token, source=backend.source)
    if url_guard.is_loopback(url):
        return Config(url=url, origin=found, source="none")
    return Config(refusal=f"refused: there is no token for {credentials.spell(found)}. {credentials.MISSING_CONFIGURATION_MESSAGE}")


# --- the wire ------------------------------------------------------------------------------------------

_out = threading.Lock()


def emit(message: dict) -> None:
    with _out:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def note(**record: Any) -> None:
    """One diagnostic line on stderr, which the harness logs. It never holds the token."""
    print(json.dumps(record), file=sys.stderr, flush=True)


def error(mid: Any, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}


def rebuild(mid: Any, frame: dict) -> dict:
    """A reply for ``mid`` made from nothing but the backend's result or error.

    Only ``jsonrpc``, ``id`` and one of ``result``/``error`` leave this process. A ``method``, ``params``
    or any other key the backend put in ``frame`` is not copied, so a reply cannot be a request to the client.
    """
    problem = frame.get("error")
    if isinstance(problem, dict):
        code, text = problem.get("code"), problem.get("message")
        rebuilt: dict = {"code": code if isinstance(code, int) and not isinstance(code, bool) else -32000, "message": text if isinstance(text, str) else ""}
        if "data" in problem:
            rebuilt["data"] = problem["data"]
        return {"jsonrpc": "2.0", "id": mid, "error": rebuilt}
    result = frame.get("result")
    if isinstance(result, dict):
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    return error(mid, -32000, "the backend's reply holds no result object")


class BackendError(Exception):
    """The backend could not be reached or answered with something unusable; the text is safe to show."""


class CutInTransit(BackendError):
    """The request reached the backend and its reply was lost on the way back, before the deadline.

    A stream that ended, or was reset, before the reply; a reset before the headers; a proxy's 502 or 504.
    Only a keyed call is asked again (see ``Backend.request``); for any other the text is the error.
    """


def parse(text: bytes | bytearray | str) -> Any:
    """JSON from the backend. A body that is not JSON, or nests deeper than the parser can take, is a ``BackendError``."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        raise BackendError("the backend's answer is not JSON") from None


# --- one call's clock ----------------------------------------------------------------------------------

_current = threading.local()


class Deadline:
    """The total time one backend call may take, kept by a timer that shuts the call's socket.

    A per-read timeout does not end a backend that sends a byte at a time, in the headers or in the body.
    When the deadline passes, the socket is shut down, which wakes whichever read is blocked on it, and
    ``expired`` says why that read failed.
    """

    def __init__(self, seconds: float, said: float | None = None) -> None:
        self.seconds = seconds
        #: The time the message names: the whole call's, when this deadline is what is left of it.
        self.said = seconds if said is None else said
        self.expired = False
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._outer: Deadline | None = None

    def __enter__(self) -> Deadline:  # noqa: PYI034 - typing.Self is not in the 3.9 floor
        self._timer.start()
        self._outer = getattr(_current, "deadline", None)
        _current.deadline = self
        return self

    def __exit__(self, *exc: object) -> None:
        self._timer.cancel()
        _current.deadline = self._outer

    def watch(self, sock: socket.socket) -> None:
        """Cut ``sock`` at the deadline (at once, if it has passed)."""
        with self._lock:
            self._sock = sock
            late = self.expired
        if late:
            self._cut(sock)

    def _fire(self) -> None:
        with self._lock:
            self.expired = True
            sock = self._sock
        if sock is not None:
            self._cut(sock)

    @staticmethod
    def _cut(sock: socket.socket) -> None:
        with contextlib.suppress(OSError):
            socket.socket.shutdown(sock, socket.SHUT_RDWR)  # the base method: an SSL socket's own would unwrap it first

    def message(self) -> str:
        return _late(self.said)


def _late(seconds: float) -> str:
    return f"the backend took longer than {seconds:g} seconds to answer"


def _watched(connection: type) -> type:
    """``connection`` with its socket handed to the running call's ``Deadline`` once it is connected."""

    class Watched(connection):  # type: ignore[misc, valid-type]
        def connect(self) -> None:
            super().connect()
            deadline = getattr(_current, "deadline", None)
            if deadline is not None and self.sock is not None:
                deadline.watch(self.sock)

    return Watched


class _WatchedHTTP(urllib.request.HTTPHandler):
    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        return super().do_open(_watched(http_class), req, **kwargs)


class _WatchedHTTPS(_no_redirect.KeepAliveHTTPS):
    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        return super().do_open(_watched(http_class), req, **kwargs)


class Backend:
    """The one backend this process talks to, with its session."""

    def __init__(self, config: Config, harness: str | None = None) -> None:
        assert config.origin is not None
        self.url, self.origin, self.token = config.url, config.origin, config.token
        #: The harness that started the bridge, which decides the update command a refusal ends with; ``None`` reads the environment's.
        self.harness = harness
        self.opener = _no_redirect.build(_WatchedHTTP(), _WatchedHTTPS(), direct=url_guard.is_loopback(self.url))
        self.session: str | None = None
        self.protocol = PROTOCOL_VERSION
        self.initialize_params: dict | None = None
        self.tools: frozenset[str] | None = None
        #: The listed tools whose ``annotations.idempotentHint`` is true: a cut reply to one is asked for again.
        self.idempotent: frozenset[str] = frozenset()
        #: Whether this session's backend advertised the request-key capability; read on every ``initialize``.
        self.keys = False
        self.lock = threading.RLock()

    # one HTTP request ---------------------------------------------------------------------------------

    def _open(self, method: str, body: bytes | None, timeout: float) -> Any:
        """Open ``self.url``. The bearer is attached only when the url parses to the pinned origin, and never to a redirect."""
        if session_pin.origin(self.url) != self.origin:
            raise BackendError("refused: the request url is not the pinned backend origin")
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": self.protocol}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        deadline = getattr(_current, "deadline", None)
        try:
            request = urllib.request.Request(self.url, data=body, headers=headers, method=method)
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            status = exc.code
            refusal = _client_handshake.refusal_from_http_error(exc)
            exc.close()
            if refusal is not None:
                raise BackendError(
                    _client_handshake.say_refusal(refusal, _run_driver.SOURCE_MCP_BRIDGE, harness=self.harness)
                ) from None
            if 300 <= status < 400:
                raise BackendError(f"the backend answered HTTP {status}, a redirect, which is not followed") from None
            if status in (502, 504):  # a proxy that lost the backend's answer, or never had it
                raise CutInTransit(f"the backend answered HTTP {status}") from None
            raise BackendError(f"the backend answered HTTP {status}") from None
        except (urllib.error.URLError, OSError) as exc:
            if deadline is not None and deadline.expired:
                raise BackendError(deadline.message()) from None
            text = f"the backend is unreachable ({getattr(exc, 'reason', exc)})"
            if isinstance(exc, (ConnectionError, ssl.SSLEOFError)) and not isinstance(exc, urllib.error.URLError):
                # urllib wraps a failure to connect or send; a bare reset, or a TLS end without close_notify, came after the request went out
                raise CutInTransit(text) from None
            raise BackendError(text) from None
        except Exception as exc:  # noqa: BLE001 - whatever the network layer raises ends this request, not the process
            if deadline is not None and deadline.expired:
                raise BackendError(deadline.message()) from None
            raise BackendError(f"the request could not be made ({type(exc).__name__})") from None

    def post(self, message: dict, timeout: float = QUICK_TIMEOUT, left: float | None = None) -> Generator[dict, None, None]:
        """POST ``message`` and yield each JSON-RPC message of the answer as it arrives (a JSON body or SSE frames).

        The call ends at twice ``timeout``, or after ``left`` seconds when that is what remains of it, however
        the backend spaces what it sends.
        """
        with Deadline(2 * timeout if left is None else left, 2 * timeout) as deadline:
            try:
                response = self._open("POST", json.dumps(message).encode("utf-8"), timeout)
                try:
                    returned = response.headers.get("Mcp-Session-Id")
                    if returned and message.get("method") == "initialize":
                        if not SESSION_PATTERN.fullmatch(returned):
                            raise BackendError("the backend's session id is not printable ASCII, so it is not used")
                        self.session = returned
                    kind = (response.headers.get("Content-Type") or "").lower()
                    if "text/event-stream" in kind:
                        yield from _sse(response)
                    else:
                        raw = _read_body(response)
                        if raw.strip():
                            loaded = parse(raw)
                            yield from (item for item in (loaded if isinstance(loaded, list) else [loaded]) if isinstance(item, dict))
                    if deadline.expired:  # a shut-down socket reads as a clean end of stream
                        raise BackendError(deadline.message())
                finally:
                    response.close()
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001 - a read that fails (reset, cut short, timed out) ends this request only
                if deadline.expired:
                    raise BackendError(deadline.message()) from None
                text = f"the backend's answer could not be read ({type(exc).__name__})"
                if isinstance(exc, socket.timeout):  # a backend silent past the per-read timeout is not a cut
                    raise BackendError(text) from None
                raise CutInTransit(text) from None

    # the session ---------------------------------------------------------------------------------------

    def request(self, message: dict, timeout: float = QUICK_TIMEOUT, relay: bool = False, again: Callable[[str], bool] | None = None) -> dict:
        """Send a request and return its reply. With ``relay``, backend notifications reach the client on the way.

        The call has twice ``timeout`` in all, across every attempt. A session the backend forgot is started
        again once. With ``again`` (a keyed call to an idempotent tool), a reply cut in transit is asked for once more under a fresh
        id; ``again(fresh_id)`` says whether the client still wants it, and the two allowances are separate.
        """
        said = 2 * timeout
        end = time.monotonic() + said
        sent, restarted, recut = message, False, False
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise BackendError(_late(said))
            try:
                with contextlib.closing(self.post(sent, timeout, left)) as frames:
                    for frame in frames:
                        if frame.get("id") == sent.get("id") and ("result" in frame or "error" in frame):
                            return frame
                        self._side(frame, relay)
                raise CutInTransit("the backend ended its answer without a reply")
            except CutInTransit:
                if again is None:
                    raise
                if recut:
                    raise CutInTransit(CUT_TWICE) from None
                fresh = f"bridge-retry-{uuid.uuid4().hex}"
                if not again(fresh):
                    raise
                sent, recut = {**message, "id": fresh}, True
            except BackendError as exc:
                if not restarted and self.session and "HTTP 404" in str(exc) and message.get("method") != "initialize" and self._restart():
                    restarted = True
                    continue
                raise

    def _side(self, frame: dict, relay: bool) -> None:
        method = frame.get("method")
        if not isinstance(method, str):
            return
        if "id" in frame:  # a request to the client: this bridge declares no capability, so it refuses
            try:
                for _ in self.post(error(frame["id"], -32601, "this client offers no capabilities")):
                    pass
            except BackendError:
                pass
        elif relay and method in NOTIFICATIONS_OUT:
            emit({k: v for k, v in frame.items() if k in ("jsonrpc", "method", "params")})

    def _restart(self) -> bool:
        """Start a new session after the backend forgot the old one. True when it worked."""
        with self.lock:
            if self.initialize_params is None:
                return False
            self.session = None
            try:
                reply = self.initialize(self.initialize_params, "bridge-reinit")
                if "result" not in reply:
                    return False
                for _ in self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}):
                    pass
            except BackendError:
                return False
            return True

    def initialize(self, params: dict, mid: Any) -> dict:
        with self.lock:
            self.initialize_params = params
            self.keys = False
            reply = self.request({"jsonrpc": "2.0", "id": mid, "method": "initialize", "params": params})
            result = reply.get("result")
            self.keys = _advertises_keys(result)
            if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str) and PROTOCOL_PATTERN.fullmatch(result["protocolVersion"]):
                self.protocol = result["protocolVersion"]
            return reply

    def list_tools(self, mid: Any) -> dict:
        """The backend's whole tool list (every page, in one reply). Remembers the names that may be called."""
        merged: list = []
        cursor: str | None = None
        for page in range(MAX_PAGES):
            params = {} if cursor is None else {"cursor": cursor}
            reply = self.request({"jsonrpc": "2.0", "id": f"bridge-list-{page}", "method": "tools/list", "params": params})
            result = reply.get("result")
            if not isinstance(result, dict):
                return rebuild(mid, reply)
            merged += [tool for tool in result.get("tools", []) if isinstance(tool, dict) and isinstance(tool.get("name"), str) and TOOL_NAME.fullmatch(tool["name"])]
            cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
            if cursor is None:
                break
        self.tools = frozenset(tool["name"] for tool in merged)
        self.idempotent = frozenset(tool["name"] for tool in merged if _idempotent(tool))
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": merged}}

    def may_call(self, name: Any) -> bool:
        if not isinstance(name, str):
            return False
        if self.tools is None:  # the client has not listed yet, so the backend's list is not known
            try:
                self.list_tools("bridge-refresh")
            except BackendError:
                return False
        return self.tools is not None and name in self.tools

    def close(self) -> None:
        if not self.session:
            return
        try:
            self._open("DELETE", None, 5).close()
        except BackendError:
            pass


def _idempotent(tool: dict) -> bool:
    """Whether a ``tools/list`` entry carries the MCP annotation ``idempotentHint: true`` (a repeat has no further effect)."""
    annotations = tool.get("annotations")
    return isinstance(annotations, dict) and annotations.get("idempotentHint") is True


def _advertises_keys(result: Any) -> bool:
    """Whether an ``initialize`` result says the backend answers a repeated request key with the first call's result."""
    capabilities = result.get("capabilities") if isinstance(result, dict) else None
    experimental = capabilities.get("experimental") if isinstance(capabilities, dict) else None
    declared = experimental.get(REQUEST_KEY) if isinstance(experimental, dict) else None
    version = declared.get("version") if isinstance(declared, dict) else None
    return isinstance(version, int) and not isinstance(version, bool) and version == REQUEST_KEY_VERSION


def _read_body(response: Any) -> bytearray:
    """The whole body, read in pieces so the cap is enforced on what has actually arrived."""
    body = bytearray()
    while True:
        piece = response.read(READ_CHUNK)
        if not piece:
            return body
        body += piece
        if len(body) > MAX_RESPONSE_BYTES:
            raise BackendError("the backend's answer is too large to relay")


def _sse(response: Any) -> Iterator[dict]:
    """The JSON-RPC messages of a server-sent-event stream, each as soon as its event ends.

    A line is read in pieces of at most ``LINE_CHUNK`` bytes, and the cap counts every piece, so a line
    that never ends is cut at the cap and not read to its end.
    """
    data: list = []
    total = 0
    line = bytearray()
    while True:
        piece = response.readline(LINE_CHUNK)
        total += len(piece)
        if total > MAX_RESPONSE_BYTES:
            raise BackendError("the backend's answer is too large to relay")
        line += piece
        if piece and not piece.endswith(b"\n"):
            continue
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        line.clear()
        if text.startswith("data:"):
            data.append(text[5:].lstrip())
        elif not text and data:
            loaded = _event(data)
            data = []
            if loaded is not None:
                yield loaded
        if not piece:
            break
    if data:
        loaded = _event(data)
        if loaded is not None:
            yield loaded


def _event(lines: list) -> dict | None:
    """The JSON-RPC message of one event's ``data`` lines, or ``None`` when they are not one."""
    try:
        loaded = parse("\n".join(lines))
    except BackendError:
        return None
    return loaded if isinstance(loaded, dict) else None


# --- dispatch ------------------------------------------------------------------------------------------


def _is_id(value: Any) -> bool:
    return isinstance(value, (str, int)) and not isinstance(value, bool)


class Sequencer:
    """Runs the messages that set a session up, in arrival order, off the thread that reads stdin.

    ``initialize`` and the notifications must reach the backend before any call and in the order the client
    sent them, yet a slow backend must not stop the reader from taking its next line. So they run one at a
    time on a worker, and a message that is not one of them waits at ``wait`` until every such message that
    arrived before it is done.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._cond = threading.Condition()
        self._issued = 0
        self._done = 0
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, job: Callable[[], None]) -> None:
        with self._cond:
            self._issued += 1
        self._queue.put(job)

    def mark(self) -> int:
        """A ticket for the messages submitted so far."""
        with self._cond:
            return self._issued

    def wait(self, ticket: int, timeout: float) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self._done >= ticket, timeout)

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - one job failing must not stop the ones behind it
                note(kind="job-failed", reason=type(exc).__name__)
            finally:
                with self._cond:
                    self._done += 1
                    self._cond.notify_all()


class Bridge:
    def __init__(self, config: Config, env: os._Environ[str] | dict[str, str] | None = None, harness: str | None = None) -> None:
        self.config = config
        #: The harness that started this process (``--harness``): Codex hands a server no session variable, so its manifest names it.
        self.harness = harness
        #: The environment ``configure`` reads again when a login may have appeared.
        self.env = os.environ if env is None else env
        self.backend = Backend(config, harness) if config.origin is not None else None
        #: The ``initialize`` params the client sent while no backend was configured, replayed to the backend once there is one.
        self.client_init: dict | None = None
        self.resolving = threading.Lock()
        #: Ids of requests the client has sent and not had an answer to, and those of them it withdrew.
        self.in_flight: set = set()
        self.cancelled: set = set()
        #: A client id to the backend id of the request now live for it, once a cut reply is asked for again.
        self.live: dict = {}
        self.lock = threading.Lock()

    def accept(self, message: dict) -> None:
        """Note a message as soon as it is read: a request becomes in flight, a cancellation marks its request.

        A cancellation for an id that is not in flight is forgotten at once: remembering it would swallow the
        reply to a later request that reuses the id, and the set would grow with every such message.
        """
        method, mid = message.get("method"), message.get("id")
        if not isinstance(method, str):
            return
        with self.lock:
            if "id" in message:
                if _is_id(mid):
                    self.in_flight.add(mid)
            elif method == "notifications/cancelled":
                params = message.get("params")
                request_id = params.get("requestId") if isinstance(params, dict) else None
                if _is_id(request_id) and request_id in self.in_flight:
                    self.cancelled.add(request_id)

    def dispatch(self, message: dict) -> None:
        mid, method = message.get("id"), message.get("method")
        if not isinstance(method, str):
            return  # a reply to a request this bridge never sent, or noise
        if "id" not in message:
            self.notification(method, message.get("params"))
            return
        if not _is_id(mid):
            emit(error(None, -32600, "invalid request id"))
            return
        try:
            reply = self.answer(mid, method, message.get("params"))
        finally:
            with self.lock:
                self.in_flight.discard(mid)
                self.live.pop(mid, None)
                withdrawn = mid in self.cancelled
                self.cancelled.discard(mid)
        if reply is not None and not withdrawn:
            try:
                emit(reply)
            except Exception as exc:  # noqa: BLE001 - a reply that cannot be written is an error, not a lost request
                emit(error(mid, -32000, f"the reply could not be written ({type(exc).__name__})"))

    def answer(self, mid: Any, method: str, raw: Any) -> dict | None:
        if method not in REQUESTS:
            return error(mid, -32601, f"{method} is not forwarded by the Plurai bridge")
        params: dict = raw if isinstance(raw, dict) else {}
        try:
            if self.backend is None and method != "initialize":
                try:
                    self.look_for_login()
                except BackendError as exc:
                    # Still refused: the client keeps the one-tool list, and the status tool says why.
                    return self.refused(mid, method, params, f"A Plurai login was found, but its backend did not start a session: {exc}")
            return self.refused(mid, method, params) if self.backend is None else self.forward(mid, method, params)
        except BackendError as exc:
            return error(mid, -32000, str(exc))
        except Exception as exc:  # noqa: BLE001 - whatever else goes wrong ends this request; the client is told
            return error(mid, -32000, f"the bridge failed on this request ({type(exc).__name__})")

    def notification(self, method: str, params: Any) -> None:
        """Pass ``method`` on to the backend when it is one of the two the bridge forwards."""
        if method not in NOTIFICATIONS_IN or self.backend is None:
            return
        if method == "notifications/cancelled":
            forwarded: dict = {"jsonrpc": "2.0", "method": method, "params": {k: v for k, v in params.items() if k in ("requestId", "reason")}} if isinstance(params, dict) else {}
        else:
            forwarded = {"jsonrpc": "2.0", "method": method}
        if not forwarded:
            return
        sends = [forwarded]
        request_id = forwarded.get("params", {}).get("requestId")
        if _is_id(request_id):
            with self.lock:
                live = self.live.get(request_id)
            if live is not None:  # the request asked again after a cut, then the first, which may still run
                sends.insert(0, {**forwarded, "params": {**forwarded["params"], "requestId": live}})
        for send in sends:
            try:
                for _ in self.backend.post(send):
                    pass
            except BackendError as exc:
                note(kind="notification-failed", method=method, reason=str(exc))

    def look_for_login(self) -> None:
        """Resolve the backend again; when a login now configures it, start the backend session and announce the new tool list.

        Nothing changes while the configuration is still refused. A backend that does not answer leaves the bridge
        refused and raises, so this request is answered as a refused one, naming why, and the next one tries again.
        """
        with self.resolving:
            if self.backend is not None:
                return
            config = configure(self.env)
            if config.origin is None:
                return
            backend = Backend(config, self.harness)
            params = self.client_init or {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "plurai-bridge", "version": "1"}}
            try:
                if "result" not in backend.initialize(params, "bridge-late-init"):
                    raise BackendError("the backend did not accept the session")
                for _ in backend.post({"jsonrpc": "2.0", "method": "notifications/initialized"}):
                    pass
            except BackendError:
                backend.close()
                raise
            self.config, self.backend = config, backend
            note(kind="login-found", origin=credentials.spell(config.origin), token_source=config.source)
            if self.client_init is not None:
                emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def refused(self, mid: Any, method: str, params: dict, reason: str = "") -> dict:
        reason = reason or self.config.refusal
        if method == "initialize":
            self.client_init = {
                "protocolVersion": params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {"name": "plurai-bridge", "version": "1"},
            }
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "plurai-bridge", "version": "1"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [STATUS_TOOL]}}
        if params.get("name") == STATUS_TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": reason}]}}
        return error(mid, -32000, reason)

    def forward(self, mid: Any, method: str, params: dict) -> dict | None:
        backend = self.backend
        assert backend is not None
        if method == "initialize":
            client = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {"name": "plurai-bridge", "version": "1"}
            version = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else PROTOCOL_VERSION
            return rebuild(mid, backend.initialize({"protocolVersion": version, "capabilities": {}, "clientInfo": client}, mid))
        if method == "ping":
            return rebuild(mid, backend.request({"jsonrpc": "2.0", "id": mid, "method": "ping"}))
        if method == "tools/list":
            return backend.list_tools(mid)
        name, arguments = params.get("name"), params.get("arguments", {})
        if name == STATUS_TOOL["name"]:  # a client that listed before the login still offers it
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "Plurai is connected."}]}}
        if not backend.may_call(name):
            return error(mid, -32602, "unknown tool: the backend did not list it")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return error(mid, -32602, "arguments must be an object")
        # The edit-session placeholder check, for the harnesses whose hooks do not reach the call (refused once).
        if _placeholder.slot(name, arguments) is not None and (said := _placeholder.refusal(name, arguments, _placeholder.state_dir())):
            return error(mid, -32602, said)
        # The id of the command in progress is added here, where Codex's hooks cannot rewrite a call. It is read per call:
        # one bridge serves every command of the session.
        arguments = _command_run.with_id(name, arguments, _command_run.for_call(name))
        call: dict = {"name": name, "arguments": arguments}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else None
        if backend.keys:
            meta = {**(meta or {}), REQUEST_KEY: uuid.uuid4().hex}
        if meta is not None:
            call["_meta"] = meta
        again = functools.partial(self._relive, mid) if backend.keys and name in backend.idempotent else None
        return rebuild(mid, backend.request({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": call}, CALL_TIMEOUT, relay=True, again=again))

    def _relive(self, mid: Any, fresh: str) -> bool:
        """Make ``fresh`` the live backend id for client request ``mid``.

        False when the client withdrew it, or when the session now in use (after a restart) no longer advertises request keys.
        """
        backend = self.backend
        with self.lock:
            if mid in self.cancelled or backend is None or not backend.keys:
                return False
            self.live[mid] = fresh
            return True


def read_lines() -> Iterator[bytes | None]:
    """Each line of stdin; ``None`` for a line longer than ``MAX_MESSAGE_BYTES``, whose rest is discarded."""
    stream = sys.stdin.buffer
    while True:
        line = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE_BYTES and not line.endswith(b"\n"):
            while True:
                rest = stream.readline(MAX_MESSAGE_BYTES)
                if not rest or rest.endswith(b"\n"):
                    break
            yield None
            continue
        yield line


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    harness = args[1] if len(args) == 2 and args[0] == "--harness" and args[1] in _client_handshake.HARNESSES else None
    config = configure(os.environ)
    bridge = Bridge(config, os.environ, harness)
    sequencer = Sequencer()
    note(kind="start", origin=credentials.spell(config.origin) if config.origin else None, token_source=config.source, refused=config.refusal or None, notes=config.notes or None)

    def after(ticket: int, message: dict) -> None:
        sequencer.wait(ticket, 2 * QUICK_TIMEOUT)
        bridge.dispatch(message)

    for line in read_lines():
        if line is None:
            emit(error(None, -32600, f"message larger than {MAX_MESSAGE_BYTES} bytes"))
            continue
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except (ValueError, RecursionError):
            emit(error(None, -32700, "parse error"))
            continue
        if not isinstance(message, dict):
            continue  # a batch or a bare value: the MCP version this bridge speaks has neither
        bridge.accept(message)
        if message.get("method") == "initialize" or "id" not in message:
            # The session precedes every call and `initialized` precedes the first, in the order sent; a slow backend holds up the calls behind it, not this loop.
            sequencer.submit(functools.partial(bridge.dispatch, message))
        else:
            threading.Thread(target=after, args=(sequencer.mark(), message), daemon=True).start()
    sequencer.wait(sequencer.mark(), 2 * QUICK_TIMEOUT)
    if bridge.backend is not None:
        bridge.backend.close()
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
