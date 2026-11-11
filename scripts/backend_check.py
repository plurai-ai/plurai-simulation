#!/usr/bin/env python3
"""The backend check: one GET of the backend's contracts route, and what a refusal says.

    python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/backend_check.py" [--close <flow>]

The request goes through the stamped, pin-guarded opener the MCP call helper uses, carries the plugin's own version and
the session's bearer, and needs neither a backend tool nor the SDK. The answer settles everything:

* no usable login (no backend, no session pin to send the token under, or no token for a backend that is not loopback) or a
  401 answer: the readiness login line, and no update text; a backend the configuration makes unusable (a url that must
  use https, half of the url and token pair) gets the readiness line that names that problem instead. A Codex session whose hooks are not trusted gets the readiness
  trust line in its place, since no login fixes that. A loopback backend needs no token, as for ``mcp_call.py``, and gets
  the request without one;
* a 426: ``_client_handshake.refusal_text`` for the typed refusal, or for one with no typed body;
* a 200 whose SDK floor is above the SDK this plugin release installs: the same function's SDK text;
* a 402 the gateway made for credit: the credit stop line, as ``mcp_call.py`` prints it;
* anything else (no answer, an older backend without the route): nothing is said, and the check lets the skill go on.

When the backend accepts the plugin, the same step prints the update notice (``_update_notice``): at most once a day, one
read of the public mirror's newest release tag, and a line only when that release is above the installed one. The notice
informs and the check still exits 0.

Exit codes: 0 go on · 3 the login line · 7 a version refusal · 8 a credit refusal. A refusal is printed on stdout, verbatim;
the caller says it alone and runs nothing after it. A flow's Step 0 passes ``--close <flow>``: on exit 7 or 8 the same call
closes the flow window it armed (``flow-window.sh close``, which leaves a window another flow holds alone) and says nothing
of it, so no model turn sits between the check and the refusal text. Exit 3 and exit 0 leave the window to the step that
handles them. Under a headless run driver a version refusal is also filed as a typed
finding in the run record (``_client_handshake.record_refusal``), which the headless driver reads to end its phase.

Stdlib only, like every script here.
"""

from __future__ import annotations

import http
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))

import _client_handshake  # noqa: E402
import _no_redirect  # noqa: E402 - after the sys.path lines it depends on
import _platform  # noqa: E402 - hooks/ is on sys.path as of the lines above
import _readiness  # noqa: E402
import _run_driver  # noqa: E402 - the one list of the stoppers that file a refusal
import _update_notice  # noqa: E402
import stop_message  # noqa: E402
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"

EXIT_OK, EXIT_LOGIN, EXIT_USAGE = 0, 3, 64
EXIT_REFUSED = _client_handshake.EXIT_VERSION_REFUSED
EXIT_CREDIT_REFUSED = stop_message.EXIT_CREDIT_REFUSED
CONTRACTS_PATH = '/client-contracts'
USAGE = 'usage: backend_check.py [--close <flow>]'
#: One local script, so a close that takes longer than this is stuck.
_CLOSE_TIMEOUT = 30
#: One small read; a backend that does not answer inside it is left to the call that needs it.
TIMEOUT = 15
_MAX_BODY = 4 * 1024 * 1024


def _login(env: dict[str, str]) -> int:
    """No usable backend. A configuration error (an unusable ``PLURAI_URL``, half of the url and token pair) is named as the
    readiness check names it, since no login fixes it; otherwise it is the login line. On Codex, hooks the user has not
    trusted are the cause of a missing pin, and the readiness check's trust line says so."""
    print((_readiness.trust_refusal(env) or _readiness.backend_refusal(env)).line())
    return EXIT_LOGIN


def _pin_refused(base: str, env: dict[str, str]) -> int:
    """A login exists, but its bearer may not go to ``base``: the session is bound to another backend, or has no record of one.
    Says what ``mcp_call.py`` says for the same refusal; on Codex, hooks the user has not trusted are the cause of a missing pin."""
    trust = _readiness.trust_refusal(env)
    print(trust.line() if trust else url_guard.bearer_refused_message(base))
    return EXIT_LOGIN


def _refused(refusal: _client_handshake.Refusal, harness: str | None, env: dict[str, str], sdk_version: str | None = None) -> int:
    """Say a refusal."""
    print(
        _client_handshake.say_refusal(
            refusal, _run_driver.SOURCE_BACKEND_CHECK, harness=harness, env=env, sdk_version=sdk_version, newer_release=_update_notice.cached_newest(env)
        )
    )
    return EXIT_REFUSED


def _close_window(flow: str, env: dict[str, str]) -> None:
    """Close the flow window ``flow`` armed, saying nothing: the customer reads the refusal alone. ``flow-window.sh close`` is
    guarded by the window's own marker, so a window another flow holds stays open. A window that cannot be closed here is
    closed by the Stop guard at the end of the turn; the refusal text is never delayed or changed by it."""
    script = Path(__file__).resolve().with_name('flow-window.sh')
    child = {name: value for name, value in env.items() if name != 'PLURAI_TOKEN'}
    bash = _platform.git_bash(child)
    if bash is None:
        return
    try:
        subprocess.run([bash, str(script), 'close', flow], capture_output=True, timeout=_CLOSE_TIMEOUT, env=child, check=False)
    except (OSError, subprocess.SubprocessError):
        return


def check(env: dict[str, str] | None = None, harness: str | None = None, close: str | None = None) -> int:
    """Make the one request and say what it needs said; the exit code is the module's table. With ``close``, a refusal
    (exit 7 or 8) also closes the window of the flow it names, after the text is printed."""
    code = _check(env, harness)
    if close and code in (EXIT_REFUSED, EXIT_CREDIT_REFUSED):
        _close_window(close, dict(os.environ) if env is None else env)
    return code


def _check(env: dict[str, str] | None, harness: str | None) -> int:
    started = time.monotonic()
    env = dict(os.environ) if env is None else env
    base = url_guard.server_url()
    token = url_guard.bearer(base) if base else ''
    if not base or not url_guard.may_send(base, token):
        return _login(env) if not (base and token) else _pin_refused(base, env)
    headers = {'Accept': 'application/json', **({'Authorization': f'Bearer {token}'} if token else {})}
    request = urllib.request.Request(f'{base}{CONTRACTS_PATH}', method='GET', headers=headers)
    try:
        with _no_redirect.OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310 - checked against the pinned backend above
            raw = response.read(_MAX_BODY)
    except urllib.error.HTTPError as exc:
        if exc.code == http.HTTPStatus.UNAUTHORIZED:
            return _login(env)
        if (refusal := _client_handshake.refusal_from_http_error(exc)) is not None:
            return _refused(refusal, harness, env)
        if (line := stop_message.credit_stop_line_of(exc, _client_handshake.MAX_ERROR_BODY)) is not None:
            print(line)
            return EXIT_CREDIT_REFUSED
        return EXIT_OK
    except Exception:  # noqa: BLE001 - no answer is no verdict: the call that needs the backend reports it
        return EXIT_OK
    try:
        answer = json.loads(raw.decode('utf-8', 'replace'))
    except ValueError:
        return EXIT_OK
    sdk = _client_handshake.pinned_sdk_version()
    refusal = _client_handshake.sdk_refusal_from_contracts(answer, sdk)
    if refusal is not None:
        return _refused(refusal, harness, env, sdk)
    if line := _update_notice.notice_for(env, harness or _client_handshake.running_harness(env), started + TIMEOUT):
        print(line)
    return EXIT_OK


def _flow_argument(argv: list[str]) -> tuple[bool, str | None]:
    """``(valid, flow)`` for the command line: no arguments, or ``--close`` and a non-empty flow name."""
    if not argv:
        return True, None
    if len(argv) == 2 and argv[0] == '--close' and argv[1]:
        return True, argv[1]
    return False, None


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    valid, flow = _flow_argument(sys.argv[1:])
    if not valid:
        print(USAGE, file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    raise SystemExit(check(close=flow))
