#!/usr/bin/env python3
"""Witness that a bench's agent-host route reaches the mock: the proof a `route_smoke` exit code cannot give.

A bench whose tools are intercepted at the agent's host runs a simulated copy of the agent, and the scan writes both that
copy and the `route_smoke` that brings it up. An exit 0 from a script the scan wrote says nothing about where the copy's
calls go; a copy still wired to the live stores passes it. So the suite's route-smoke step opens a fresh session on the
deployed mock BEFORE the smoke, hands its id to the smoke as `$PLURAI_ROUTE_SMOKE_SESSION` (the smoke drives one call
through the copy carrying it as the copy's mock session), and AFTER a 0 exit requires the mock to have recorded at least
one tool call in that session.

    route_witness.py open  <workdir>          prints the session id ("" for a self-witnessed route); exit 2 when it cannot open one
    route_witness.py check <workdir> <sid>    exit 0 when the mock recorded a tool call in <sid> (or <sid> is ""), else 1

Self-witnessed: a route whose rewrite plan stands beside the workdir (the Agentforce rewrite: `up` makes the copy's own
health callout to the mock and stops on anything but 200) needs no session; the shared locate decides
(`rewire_agentforce.located_agent_file`). One recorded call proves the copy reaches the mock, not that every simulated
store is rewired. Stdlib only; the bearer is the resolved one (`url_guard.bearer`), sent only to a loopback or session-pinned backend,
never followed through a redirect and never printed.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'simulation-setup-agentforce' / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_redirect  # noqa: E402 - the opener that stamps the plugin on every request and never follows a redirect
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"

MOCK_URL_FILE = '.mock_service_url'
SESSION_ENV = 'PLURAI_ROUTE_SMOKE_SESSION'
_TIMEOUT = 30


def self_witnessed(workdir: Path) -> bool:
    import rewire_agentforce  # noqa: PLC0415 - the one locate of the rewrite's bundle

    return rewire_agentforce.located_agent_file(workdir) is not None


def _base(workdir: Path) -> str:
    try:
        url = (workdir / MOCK_URL_FILE).read_text(encoding='utf-8').strip()
    except OSError:
        url = ''
    if not url:
        raise RuntimeError(f'{workdir / MOCK_URL_FILE} is absent or empty: the world is not deployed, so there is no mock to witness the route')
    return url.rstrip('/')


def _call(url: str, body: dict | None = None) -> dict:
    token = url_guard.bearer(url) if url_guard.is_backend_url(url) else ''
    headers = {'Content-Type': 'application/json', **({'Authorization': f'Bearer {token}'} if token else {})}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method='POST' if body is not None else 'GET')
    with _no_redirect.OPENER.open(request, timeout=_TIMEOUT) as response:
        payload = json.loads(response.read() or b'{}')
    if not isinstance(payload, dict) or payload.get('status') == 'error' or payload.get('error'):
        raise RuntimeError(f'the mock refused the call: {str(payload)[:300]}')
    return payload


def open_session(workdir: Path) -> str:
    if self_witnessed(workdir):
        return ''
    sid = f'route-smoke-{uuid.uuid4().hex[:12]}'
    _call(f'{_base(workdir)}/session/create', {'session_id': sid, 'initial_state': {}, 'agent_context': {}})
    return sid


def recorded_calls(workdir: Path, sid: str) -> list[str]:
    events = _call(f'{_base(workdir)}/events/{sid}').get('events')
    if not isinstance(events, list):
        raise RuntimeError(f'the mock returned no event list for session {sid}')
    return [str(e.get('tool')) for e in events if isinstance(e, dict) and e.get('type') == 'tool_call']


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ('open', 'check') or (argv[0] == 'check' and len(argv) != 3):
        print('usage: route_witness.py open <workdir> | route_witness.py check <workdir> <session id>', file=sys.stderr)
        return 2
    workdir = Path(argv[1])
    try:
        if argv[0] == 'open':
            print(open_session(workdir))
            return 0
        sid = argv[2]
        if not sid:
            print('route witnessed by its own bring-up (the rewrite plan beside the workdir)')
            return 0
        calls = recorded_calls(workdir, sid)
    except (RuntimeError, OSError, ValueError, urllib.error.URLError) as error:
        print(f'route_witness: {error}', file=sys.stderr)
        return 2 if argv[0] == 'open' else 1
    if calls:
        print(f'route witnessed: the mock recorded {len(calls)} call(s) from the copy in {sid} ({", ".join(sorted(set(calls))[:6])})')
        return 0
    print(f'route_witness: the route smoke exited 0 but the mock recorded no call in {sid}. The smoke must drive one call through '
          f'the copy with ${SESSION_ENV} as its mock session; a copy still wired to the live stores, or a smoke that never '
          'reached the copy, ends here.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
