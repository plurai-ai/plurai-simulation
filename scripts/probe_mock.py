#!/usr/bin/env python3
"""Send real requests to a deployed mock env and say whether it answers. Stdlib only.

    probe_mock.py <mock_url> [--tool NAME] [--args JSON]
    probe_mock.py <mock_url> --timing N

`<mock_url>` is the absolute URL in `.mock_service_url` (a hosted backend serves it as
`https://<host>/mock/<env_id>`). The probe speaks the mock's own wire, the one `plurai`'s
`MockServiceClient` uses, and nothing else:

    GET  /health            -> {"status": "ok"}
    POST /session/create    -> {"session_id", "initial_state", "agent_context"}
    POST /mock/execute      -> {"session_id", "tool", "args", "turn"}   (only with --tool)

A route you guess instead (`/execute`, `/<tool>`, `/api/...`) answers `Unknown: POST /<path>`, which
says nothing about the mock: only the paths above are served. A `--tool` call with empty `--args`
may come back as a validation error on the tool's arguments; that is still an answer from the
running mock. The resolved bearer (`hooks/_login_store.py`) goes only to the backend this session is bound
to: it is sent when `<mock_url>` has the scheme, host and port of an origin the session
pin recorded and of the resolved backend (`hooks/session_pin.py`; none when there is no pin). A mock is served
at `<backend>/mock/<env_id>/`, so it shares the backend's origin; a loopback mock beside a remote backend gets no
bearer. Any other origin gets no Authorization header, and the probe
prints one line saying the token was withheld. A redirect is never followed, so the bearer cannot
reach a host the answer points at. A tokenless loopback backend needs no header and gets none.

`--timing N` is the transport measurement: N sequential authenticated `GET /health`, one line each with
the status and the seconds it took, then `timing: n=N median=…s max=…s`. It sends nothing else, and
exits 1 when any answer was not 2xx. A skill that needs the bearer sent to the mock uses this (through
`probe_mock.sh`) and does not write a `curl` of its own, which would put the token in a command line.

Prints one line per request. The output is for you: it carries the probed URL, so do not relay it.

Exit codes: 0 every request answered 2xx · 1 `/health` did not answer `ok` · 2 usage ·
3 `/session/create` or `/mock/execute` failed (the line says which, with the HTTP status).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _no_redirect  # noqa: E402 - the opener that stamps the plugin on every request and never follows a redirect
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"

TIMEOUT_SECONDS = 20


def _bearer_for(base: str) -> str:
    """The token to send to ``base``: the resolved one, only when ``base`` may receive it.

    That is loopback or an origin the session pin recorded (``hooks/session_pin.py``), never what the
    command's own environment says the backend is.
    """
    token = url_guard.bearer(base)
    return token if token and url_guard.is_backend_url(base) else ""


_OPENER = _no_redirect.OPENER


def _call(
    base: str, method: str, path: str, body: dict | None = None
) -> tuple[int, str]:
    """(HTTP status, body text) for one request; status 0 means no HTTP answer at all."""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    token = _bearer_for(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers=headers, method=method
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(errors="replace")
    except (urllib.error.URLError, OSError) as err:
        return 0, str(err)


def _positive(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from None
    if count < 1:
        raise argparse.ArgumentTypeError("the count must be at least 1")
    return count


def _timing(base: str, count: int) -> int:
    """``count`` sequential ``GET /health``, each printed with its seconds, then the distribution."""
    seconds: list[float] = []
    ok = True
    for _ in range(count):
        started = time.monotonic()
        status, _text = _call(base, "GET", "/health")
        seconds.append(time.monotonic() - started)
        ok = ok and 200 <= status < 300
        print(f"GET /health -> {status} {seconds[-1]:.3f}s")
    print(f"timing: n={count} median={statistics.median(seconds):.3f}s max={max(seconds):.3f}s")
    return 0 if ok else 1


def _brief(text: str, limit: int = 200) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mock_url")
    parser.add_argument(
        "--tool",
        help="a tool name from plurai.config.yaml `tools:`; sent to /mock/execute",
    )
    parser.add_argument(
        "--args", default="{}", help="JSON object of tool arguments (default {})"
    )
    parser.add_argument(
        "--timing",
        type=_positive,
        metavar="N",
        help="send N sequential authenticated GET /health and report each status and its seconds, then the median and max",
    )
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        tool_args = json.loads(ns.args)
    except ValueError:
        print("probe_mock: --args is not JSON", file=sys.stderr)
        return 2
    if not isinstance(tool_args, dict):
        print("probe_mock: --args must be a JSON object", file=sys.stderr)
        return 2
    if not ns.mock_url.startswith(("http://", "https://")):
        print(
            "probe_mock: mock_url must be an absolute http(s) URL (the contents of .mock_service_url)",
            file=sys.stderr,
        )
        return 2
    base = ns.mock_url.rstrip("/")
    if url_guard.has_bearer() and not _bearer_for(base):
        print("bearer token withheld: " + url_guard.refused_message("the mock URL", ""))

    if ns.timing:
        return _timing(base, ns.timing)

    status, text = _call(base, "GET", "/health")
    healthy = False
    if status == 200:
        try:
            healthy = json.loads(text).get("status") == "ok"
        except (ValueError, AttributeError):
            healthy = False
    print(f"GET /health -> {status} {_brief(text)}")
    if not healthy:
        return 1

    session_id = f"probe-{uuid.uuid4().hex[:12]}"
    status, text = _call(
        base,
        "POST",
        "/session/create",
        {"session_id": session_id, "initial_state": {}, "agent_context": {}},
    )
    print(f"POST /session/create -> {status} {_brief(text)}")
    if not 200 <= status < 300:
        return 3

    if ns.tool:
        status, text = _call(
            base,
            "POST",
            "/mock/execute",
            {"session_id": session_id, "tool": ns.tool, "args": tool_args, "turn": 0},
        )
        print(f"POST /mock/execute tool={ns.tool} -> {status} {_brief(text)}")
        if not 200 <= status < 300:
            return 3
    return 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
