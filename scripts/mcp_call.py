#!/usr/bin/env python3
"""Call ONE Plurai MCP tool over plain HTTP, when the MCP tools are not in the tool list.

WHY. The manifest registers the Plurai MCP server once, at session start, from the env the
harness launched with. When that env was incomplete the handshake 401s and the server DROPS —
and the session then has no `mcp__plugin_plurai-simulation_plurai__*` tools even
though `claude mcp list` reports the server healthy and the backend is perfectly reachable.
The skills' preflight says to restart, which is right for a whole flow. It is the wrong answer
for "poll this job" or "read that artifact", where speaking MCP over HTTP by hand is the alternative.

SCOPE. Reads and polls. A full generation flow should still be restarted into a session with
the tools registered — this exists so a healthy backend is not a hard stop, not so the flow can
be run without its tooling.

    scripts/mcp_call.py job_status '{"id": "<job id>"}'
    scripts/mcp_call.py list_artifacts '{}' | jq .
    scripts/mcp_call.py generate_scenarios_start '{...}' --traffic-class smoke
    scripts/mcp_call.py score_run @args.json        # arguments from a file (`-` = stdin): past ARG_MAX

TRAFFIC CLASS. `--traffic-class smoke` marks the jobs a probe starts, keeping them out of any
channel that counts customer signal. It is off by default: a backend that does not accept the field
rejects the call, and marking by default would cost a rejected call plus a retry on every start. The marker rides only on job-STARTING calls (reads and polls create no row);
`--traffic-class none` is that default and omits the field, `customer` states the other case. A
backend that does not know the field yet is retried ONCE without it, so an EXPLICIT marking still
works against a backend whose half has not landed — decided from the refusal's STRUCTURE (a
pydantic `loc` naming the field) rather than from its English wording, so a framework release that
rewords the message cannot silently turn the retry off. A refusal of the VALUE is never retried:
dropping the marker would succeed and mislabel the job.

Prints the tool's own JSON payload to stdout (the `result.content[0].text` body, parsed and
re-emitted); diagnostics to stderr. Stdlib only, like every script here.

Exit codes: 0 ok · 2 usage · 3 no token for a remote backend, or a URL that is not the backend this session is
bound to · 4 transport/HTTP failure · 5 protocol failure (no session id, unparseable frame) ·
6 the tool itself reported an error · 7 the backend refuses this plugin's version (HTTP 426); stderr is the refusal text
`_client_handshake.refusal_text` makes, which ends with the update command (the contact text alone when the refusal is not about
the version) · 8 the gateway refused the call for credit:
stderr is one `CREDIT-STOP: <sentence>` line.
(Not 10, the SDK's `UPGRADE_REQUIRED`: 10 to 12 are the exits of `release_verdict.py`, which chains with this script.)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# This file is loaded by path (importlib) as well as run as a script, and only the second puts
# its own directory on sys.path — so the sibling import is made to work under both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))

import _client_handshake  # noqa: E402
import _no_redirect  # noqa: E402 - after the sys.path lines it depends on
import _command_run  # noqa: E402
import _placeholder  # noqa: E402 - the edit-session placeholder check the PreToolUse hook runs too
import _run_driver  # noqa: E402 - the one list of the stoppers that file a refusal
import backend_identity_marker  # noqa: E402
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"
import stop_message  # noqa: E402 - the credit sentence behind a gateway-made credit refusal

_PROTOCOL_VERSION = '2025-06-18'
_TIMEOUT = 300  # a generation tool can think for minutes; a poll returns instantly

#: The calls that CREATE a job row — the only ones a traffic class can ride on. Every
#: job-starting tool on this surface ends `_start`; `score_run` is the sync exception, and it
#: publishes a coverage rollup of its own, so it is bucketed the same way. A read or a poll gets
#: no marker: it makes no row, and a field the tool does not declare would be a hard error.
_SCORE_RUN = 'score_run'


def _starts_a_job(tool: str) -> bool:
    return tool.endswith('_start') or tool == _SCORE_RUN


#: How a backend that predates the field refuses it: a SIGNATURE complaint naming the field.
#: Both halves are required. The shape alone would retry a call refused over some other
#: argument, reporting the wrong cause; the NAME alone would swallow a backend that knows the
#: field and is rejecting its VALUE ("traffic_class must be one of …") — the retry would then
#: drop the marker, succeed, and mislabel the job, which is the failure this marker exists to
#: prevent.
_MARKER = 'traffic_class'

#: THE PROSE TUPLE IS THE FALLBACK, NOT THE TEST. These five are English phrasings from the
#: FastMCP/pydantic versions we happened to meet, and a version that words a signature mismatch
#: differently makes the retry never fire — so an operator who explicitly passed `--traffic-class`
#: gets a hard-failed job start over a field the backend simply does not know. The refusal has a
#: STRUCTURE underneath the prose, and the structure is what is read first: pydantic reports every
#: argument error as `{'loc': [..., 'traffic_class'], 'type': ...}`, whatever language or wording
#: the message layer wraps it in.
_REJECTS_SIGNATURE = ('unexpected keyword', 'extra_forbidden', 'unrecognized', 'unknown field', 'no parameter named')

#: `type` values that mean "the field EXISTS and its VALUE is wrong" — the one case that must NOT
#: retry, because dropping the marker would then succeed and mislabel the job. Substring-matched:
#: pydantic v2 says `literal_error`, v1 said `type_error.enum` / `value_error.const`, and a
#: constrained-string refusal is `enum`-shaped under every version. Anything else on a `loc` that
#: names the field is a SIGNATURE refusal, which is the safe default: the cost of retrying is one
#: extra call, the cost of not retrying is a refused job the operator asked for.
_VALUE_CONSTRAINT_TYPES = ('literal', 'enum', 'const')


def _die(code: int, message: str) -> int:
    print(f'mcp_call: {message}', file=sys.stderr)
    return code


def _credit_stop(error: urllib.error.HTTPError) -> int | None:
    """Print the `CREDIT-STOP:` line and return the credit exit when `error` is the gateway's credit refusal, else None."""
    line = stop_message.credit_stop_line_of(error, _client_handshake.MAX_ERROR_BODY)
    if line is None:
        return None
    print(line, file=sys.stderr)
    return stop_message.EXIT_CREDIT_REFUSED


def _upgrade_refusal(error: urllib.error.HTTPError) -> int | None:
    """Print the version refusal as it is, verbatim and alone, and return its exit code, when the backend refused this plugin's
    version (HTTP 426); else `None`. The text is the one every transport renders and files for a headless run driver."""
    refusal = _client_handshake.refusal_from_http_error(error, _client_handshake.MAX_ERROR_BODY)
    if refusal is None:
        return None
    print(_client_handshake.say_refusal(refusal, _run_driver.SOURCE_MCP_CALL), file=sys.stderr)
    return _client_handshake.EXIT_VERSION_REFUSED


def _endpoint() -> str:
    """The /mcp endpoint of the resolved backend (`hooks/_login_store.py`), or '' when none is configured."""
    root = url_guard.server_url()
    return f'{root}/mcp' if root else ''


def _post(url: str, token: str, payload: dict, session_id: str | None) -> tuple[dict | None, str | None]:
    """`(parsed frame, session id)`. A notification (no id) parses to `(None, …)`."""
    headers = {
        'Content-Type': 'application/json',
        # BOTH, in this order: the server streams SSE for tool calls and answers plain JSON
        # for the handshake, and it chooses off this header.
        'Accept': 'application/json, text/event-stream',
    }
    if token:
        # Every caller that posts, not only `main`, goes through this: the bearer goes only to a backend the
        # session was bound to (the session pin, hooks/session_pin.py).
        if not url_guard.is_backend_url(url):
            raise urllib.error.URLError(url_guard.refused_message(url))
        headers['Authorization'] = f'Bearer {token}'
    if session_id:
        headers['Mcp-Session-Id'] = session_id
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST', headers=headers)
    with _no_redirect.OPENER.open(request, timeout=_TIMEOUT) as response:  # noqa: S310 - checked against the pinned backend above
        # The session id arrives as a RESPONSE HEADER on the initialize reply and must ride
        # every later request; there is no other place it is published.
        got_session = response.headers.get('Mcp-Session-Id')
        body = response.read().decode('utf-8', 'replace')
    return _parse_frame(body, payload.get('id')), got_session


#: Asked on every tool call. The backend then sends a progress beat on the call's own stream while the
#: call runs, or waits for a scoring slot, so a long call is never idle past `_TIMEOUT`; the beats are
#: notifications, which `_is_response` skips.
_PROGRESS_TOKEN = 'plurai-mcp-call'


def _call_params(tool: str, arguments: dict) -> dict:
    return {'name': tool, 'arguments': arguments, '_meta': {'progressToken': _PROGRESS_TOKEN}}


def _is_response(frame: object, expect_id: object) -> bool:
    """Is this frame the ANSWER to our request, rather than some other traffic on the stream?

    A JSON-RPC response carries `result` or `error`; a notification (server-side progress, a
    log record) carries `method` and no id. Taking the first `data:` line unconditionally
    printed `{}` and exited 0 on such a frame — a fabricated empty success. Every call asks for
    progress beats (`_PROGRESS_TOKEN`), so a long call's stream carries them before its answer.
    """
    if not isinstance(frame, dict):
        return False
    if 'result' not in frame and 'error' not in frame:
        return False
    # An id is matched when both sides have one; a server that omits it is not second-guessed.
    return not (expect_id is not None and 'id' in frame and frame['id'] != expect_id)


def _parse_frame(body: str, expect_id: object = None) -> dict | None:
    """A JSON-RPC frame out of either transport shape: bare JSON, or an SSE event.

    The `data:` lines are scanned FIRST and the body is only parsed whole as a fallback: a
    real SSE event opens with `event: message`, so anything keyed on the body's own first
    characters misses every framed reply the server actually sends. Every candidate is then
    filtered through `_is_response` — one stream can carry more than one frame.
    """
    text = body.strip()
    if not text:
        return None
    candidates: list[object] = []
    for line in text.splitlines():
        if line.startswith('data:'):
            try:
                candidates.append(json.loads(line[len('data:') :].strip()))
            except json.JSONDecodeError:
                continue
    if not candidates:
        try:
            candidates.append(json.loads(text))
        except json.JSONDecodeError:
            return None
    for frame in candidates:
        if _is_response(frame, expect_id):
            return frame  # type: ignore[return-value]  - _is_response narrows to dict
    return None


def _walk(node: object):
    """Every mapping anywhere in `node`, including ones serialized into a JSON STRING.

    The validation detail routinely arrives re-encoded — pydantic's error list stringified into
    an `error.message`, or into a tool result's text content — so a walk that only descends real
    dicts and lists finds the prose and never the structure it is quoting.
    """
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
    elif isinstance(node, str) and node[:1] in ('{', '['):
        try:
            yield from _walk(json.loads(node))
        except ValueError:
            return


def _structured_verdict(body: object) -> bool | None:
    """`True` signature refusal · `False` value refusal · `None` no structure to read.

    Read off pydantic's own error entries: an argument error carries `loc` naming the offending
    field, and `type` saying WHY. A `type` that presupposes the field exists (a Literal/enum/const
    constraint) is the backend rejecting our VALUE — retrying without the marker would succeed and
    MISLABEL the job, which is the failure the marker exists to prevent. Every other reason on a
    `loc` that names the field is the field itself being unknown.
    """
    verdict: bool | None = None
    for node in _walk(body):
        loc = node.get('loc')
        if not isinstance(loc, (list, tuple)) or not any(str(part) == _MARKER for part in loc):
            continue
        kind = str(node.get('type') or '').lower()
        if any(token in kind for token in _VALUE_CONSTRAINT_TYPES):
            return False  # decisive: the field is known and our value was refused
        verdict = True
    return verdict


def _rejects_marker(frame: dict | None) -> bool:
    """Whether this answer is the backend refusing the `traffic_class` field itself.

    Both shapes count: a JSON-RPC `error`, and an `isError` tool result — FastMCP reports a
    signature mismatch through either depending on where it is caught. The STRUCTURE decides when
    there is one; the prose tuple is consulted only for a body that carries none, because a
    framework release that rewords its message must not silently turn the retry off."""
    if not isinstance(frame, dict):
        return False
    result = frame.get('result')
    result = result if isinstance(result, dict) and result.get('isError') else ''
    body = frame.get('error') or result or ''
    structured = _structured_verdict(body)
    if structured is not None:
        return structured
    # `ensure_ascii=False` so the tokens are matched against the prose the backend actually
    # wrote rather than against `\uXXXX` escapes of it.
    blob = json.dumps(body, ensure_ascii=False).lower()
    return _MARKER in blob and any(token in blob for token in _REJECTS_SIGNATURE)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        print(__doc__, file=sys.stderr)
        return 2 if len(argv) < 2 else 0
    rest = list(argv[1:])
    # Default OFF, not `smoke`: no backend accepts this field yet, so marking by default bought a
    # rejected call plus a retry on every job start — a live cost for a marker nothing reads. The
    # flag stays so the marking is one word away the day a consumer exists.
    traffic_class = 'none'
    if '--traffic-class' in rest:
        i = rest.index('--traffic-class')
        if i + 1 >= len(rest):
            return _die(2, '--traffic-class needs a value (a class name, or `none` to omit the field).')
        traffic_class = rest[i + 1]
        del rest[i:i + 2]
    tool, raw_args = rest[0], (rest[1] if len(rest) > 1 else '{}')
    # `@<path>` / `-`: arguments from a file / stdin. A `score_run` over a saved retry runs past
    # ARG_MAX (1.2 MB live; Linux caps ONE argv string at 128 KB), so argv cannot carry it.
    try:
        if raw_args == '-':
            raw_args = sys.stdin.read()
        elif raw_args.startswith('@'):
            raw_args = Path(raw_args[1:]).read_text(encoding='utf-8')
    except OSError as exc:
        return _die(2, f'could not read the arguments from {raw_args[1:]!r}: {exc}')
    try:
        arguments = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return _die(2, f'arguments must be a JSON object: {exc}')
    if not isinstance(arguments, dict):
        return _die(2, f'arguments must be a JSON OBJECT, got {type(arguments).__name__}')
    # No hook sees a call made from the shell: the placeholder check runs here (refused once, as in the hook).
    if _placeholder.slot(tool, arguments) is not None and (said := _placeholder.refusal(tool, arguments, _placeholder.state_dir())):
        return _die(2, said)

    # An explicit `traffic_class` in the arguments always wins — the caller said it on purpose.
    marked = False
    if _starts_a_job(tool) and traffic_class != 'none' and 'traffic_class' not in arguments:
        arguments['traffic_class'] = traffic_class
        marked = True

    # The id of the command in progress rides every job start; it is added here so that no skill text has to carry it.
    arguments = _command_run.with_id(tool, arguments, _command_run.for_call(tool))

    url = _endpoint()
    if not url:
        return _die(3, url_guard.configuration_problem('mcp_call'))
    # This script is a SECOND MCP client, built from the resolved backend on purpose (see _endpoint).
    # That is exactly the shape that can address a different deployment than the session's own
    # MCP server, and nothing here could notice: the wrong-but-healthy box answers every read.
    if (refusal := backend_identity_marker.split(url, caller='mcp_call')) is not None:
        return _die(3, refusal)
    token = url_guard.bearer(url)  # the resolved pair's token
    # The bearer goes only to a backend the session was bound to (the session pin, hooks/session_pin.py).
    if not url_guard.may_send(url, token):
        if token:
            return _die(3, url_guard.bearer_refused_message(url))
        return _die(3, f'no token is available for the backend ({url}), and it is remote — the handshake would 401. ' + url_guard.configuration_problem('mcp_call'))

    try:
        frame, session_id = _post(
            url,
            token,
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {'protocolVersion': _PROTOCOL_VERSION, 'capabilities': {}, 'clientInfo': {'name': 'plurai-mcp-call', 'version': '1'}},
            },
            None,
        )
    except urllib.error.HTTPError as exc:
        if (upgrade := _upgrade_refusal(exc)) is not None:
            return upgrade
        if (credit := _credit_stop(exc)) is not None:
            return credit
        hint = ' (the bearer was rejected — same token the MCP server uses)' if exc.code in (401, 403) else ''
        return _die(4, f'initialize failed: HTTP {exc.code}{hint}')
    except Exception as exc:  # noqa: BLE001 - any transport failure is one message to the operator
        return _die(4, f'initialize failed: {type(exc).__name__}: {exc}')
    if not session_id:
        return _die(5, 'the server returned no Mcp-Session-Id on initialize; cannot continue.')
    if frame and frame.get('error'):
        return _die(5, f'initialize error: {json.dumps(frame["error"], ensure_ascii=False)}')

    try:
        # Required by the protocol before any tool call; the server answers 202 with no body.
        _post(url, token, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, session_id)
        frame, _ = _post(url, token, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': _call_params(tool, arguments)}, session_id)
        if marked and _rejects_marker(frame):
            # The backend half may not have landed. Retry once WITHOUT the marker rather than
            # failing a call the operator asked for: the marker is bookkeeping, never a
            # precondition of the work.
            print(f'mcp_call: this backend does not accept traffic_class — retrying {tool} without it.', file=sys.stderr)
            arguments.pop('traffic_class', None)
            frame, _ = _post(url, token, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': _call_params(tool, arguments)}, session_id)
    except urllib.error.HTTPError as exc:
        if (upgrade := _upgrade_refusal(exc)) is not None:
            return upgrade
        if (credit := _credit_stop(exc)) is not None:
            return credit
        return _die(4, f'{tool} failed: HTTP {exc.code}')
    except Exception as exc:  # noqa: BLE001
        return _die(4, f'{tool} failed: {type(exc).__name__}: {exc}')

    if frame is None:
        return _die(5, f'{tool}: no parseable JSON-RPC frame in the response.')
    if frame.get('error'):
        return _die(6, f'{tool} error: {json.dumps(frame["error"], ensure_ascii=False)}')

    result = frame.get('result')
    if not isinstance(result, dict):
        # Off-contract for tools/call, but a non-dict `result` is still an answer: print it
        # rather than tracebacking on `.get`. Nothing here can carry `isError`.
        print(json.dumps(result if result is not None else {}, ensure_ascii=False, indent=2))
        return 0

    content = result.get('content') or []
    text = content[0].get('text') if content and isinstance(content[0], dict) else None
    if text is None:
        # A tool with no text content still answered; hand back the whole result rather than
        # inventing an emptiness the caller cannot distinguish from a failure.
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        try:
            print(json.dumps(json.loads(text), ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print(text)  # a tool that answers prose is not an error
    # AFTER both print paths, never inside one of them, so an `isError` result whose content has no text first element
    # exits 6 as a text-shaped error does: the documented "6 the tool itself reported an error" covers both.
    if result.get('isError'):
        return _die(6, f'{tool} reported isError (payload printed above).')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main(sys.argv))
