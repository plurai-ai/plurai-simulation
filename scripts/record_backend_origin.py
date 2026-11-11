#!/usr/bin/env python3
"""Tell a workdir copy of `resolve-plurai-urls.sh` which backend it may send the bearer to.

    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/record_backend_origin.py" "$WORKDIR"   # after resolve-python.sh

Some setups (the Agentforce mock route) copy the resolver into the agent's workdir, where it runs outside
the plugin: it has no url_guard and no session pin to ask, so on its own it lets the bearer go nowhere. This
command closes that gap. A session bound to a remote backend leaves that
backend's origin in `<dir>/.plurai_backend_origin` (one line, `https://host[:port]`), and the copy also
accepts exactly that origin. The origin is the resolved backend's (``_login_store.resolve_backend``), and it is
written only when the plugin's guard (`hooks/url_guard.py`, which reads the session pin, never this command's
environment alone) says the bearer may go there. Beside it goes `<dir>/.plurai_client`, the `X-Plurai-Client` value
(`plurai-simulation/<version>`) the copy names itself with: it has no plugin manifest to read the version from.

Exit 0: recorded. Exit 1: no backend is configured, or it is not a backend this session is bound to; nothing is
written and a stale record is removed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hooks'))
import _client_handshake  # noqa: E402
import _login_store  # noqa: E402
import url_guard  # noqa: E402

#: The file the copied resolver reads, beside itself.
RECORD = '.plurai_backend_origin'
#: The client stamp the copied probe sends, beside the origin it is recorded with.
CLIENT_RECORD = '.plurai_client'


class NotRecordable(Exception):
    """A base the copy must not be told to accept."""


def bases(env: os._Environ[str] | dict[str, str]) -> list[str]:
    """The REST base of the resolved backend; ``NotRecordable`` when no backend is configured."""
    backend = _login_store.resolve_backend(env)
    if not backend.configured:
        raise NotRecordable(backend.problem or 'no Plurai backend is configured')
    return [backend.url]


def expected_record(env: os._Environ[str] | dict[str, str]) -> str | None:
    """The line the record should hold: the backend's origin, which the plugin's guard must accept.

    A local backend is recorded with its port (`http://127.0.0.1:8200`), a remote one as `https://host[:port]`.
    """
    found = set()
    for url in bases(env):
        parts = url_guard.session_pin.origin(url) if url_guard.session_pin is not None else None
        if parts is None or not url_guard.is_backend_url(url):
            raise NotRecordable(f'{url} is not a backend this session is bound to')
        if ':' in parts[1]:
            raise NotRecordable(f'{url} names its host by an IPv6 literal, which the copy does not read')
        if url_guard.is_loopback(url):
            if parts[0] != 'http' or parts[1] not in ('127.0.0.1', 'localhost'):
                raise NotRecordable(f'{url} is a loopback address the copy does not read (use http://127.0.0.1:<port> or http://localhost:<port>)')
        elif parts[0] != 'https':
            raise NotRecordable(f'{url} is not an https backend')
        found.add(parts)
    spelled = sorted(
        f'https://{host}' if scheme == 'https' and port == 443 else f'{scheme}://{host}:{port}' for scheme, host, port in found
    )
    return spelled[0] if spelled else None


def _replace(path: Path, line: str | None) -> None:
    if line is None:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    tmp.write_text(line + '\n', encoding='utf-8')
    os.replace(tmp, path)


def write(directory: Path, line: str | None) -> None:
    """Record ``line`` and the client stamp beside it, or remove both when there is nothing to record."""
    _replace(directory / RECORD, line)
    _replace(directory / CLIENT_RECORD, None if line is None else _client_handshake.header_value())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).is_dir():
        print('usage: record_backend_origin.py <workdir>', file=sys.stderr)
        return 2
    directory = Path(args[0])
    try:
        line = expected_record(os.environ)
    except NotRecordable as exc:
        write(directory, None)
        print(f'not recorded: {exc}. Start the session with the backend you mean (PLURAI_URL and PLURAI_TOKEN, or a saved login).', file=sys.stderr)
        return 1
    write(directory, line)
    print(f'backend origin: {line} recorded in {directory / RECORD}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
