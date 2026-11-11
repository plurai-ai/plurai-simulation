#!/usr/bin/env python3
"""Claude Code's entry to the saved backend credential: the MCP ``headersHelper``.

The reader is ``hooks/_login_store.py``, which owns the file's path (``~/.plurai/credentials.json`` in the
account's home), its schema (``{"url": ..., "token": ...}``), the checks that make a file safe to read, and the one
resolver that says which backend and token a session uses. Everything below asks it; nothing here reads the file.

    credentials.py --headers    the MCP server's headersHelper. Claude Code strips ``*TOKEN*`` variables
                                from a plugin helper's environment, so this resolves without a token. It prints
                                {"Authorization": "Bearer <token>"} when the pair is the saved login (nothing else
                                configured, or the exported ``PLURAI_URL`` is the login's own) and
                                $CLAUDE_CODE_MCP_SERVER_URL is under that backend, else {} and the manifest's
                                static header stands.
                                When it sends the saved login's header it also writes the time to
                                ``~/.plurai/mcp_connected``: Claude Code runs the helper on every connection, so the
                                file's time is the last connection made with the saved login, which
                                ``wait_for_plurai_connect.py`` watches. A connection without that header fails, so
                                it leaves no time. The answer does not depend on that write.

Stdlib only and runnable on Python 3.9: the hooks and the helper run it under the system ``python3``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path


def _reader():
    """``hooks/_login_store.py``, loaded by path so a foreign ``_login_store`` on ``sys.path`` (a ``PYTHONPATH``
    into an agent repo) never stands in for it."""
    module = sys.modules.get('_login_store')
    if module is None:
        path = Path(__file__).resolve().parent.parent / 'hooks' / '_login_store.py'
        spec = importlib.util.spec_from_file_location('_login_store', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules['_login_store'] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop('_login_store', None)
            raise
    return module


def credentials_file() -> Path:
    return _reader().file_path()


def connect_stamp() -> Path:
    """The file whose content is the time of the last ``--headers`` run: beside the saved login, in the account's folder."""
    return _reader().account_home() / '.plurai' / 'mcp_connected'


def record_connection(now=None) -> None:
    """Write the time (seconds since the epoch) of this connection. A folder that cannot be written changes nothing."""
    target = connect_stamp()
    try:
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_text(repr(time.time() if now is None else now), encoding='utf-8')
    except OSError:
        pass


def last_connection() -> float:
    """The time of the last recorded connection; 0 when there is none or the file is not a time."""
    try:
        return float(connect_stamp().read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return 0.0


def token_for(url: str, environ=None) -> str:
    return _reader().token_for(url, environ)


def headers(server_url: str, environ=None) -> dict:
    return _reader().headers(server_url, environ)


def main(argv: list) -> int:
    if argv == ['--headers']:
        answer = headers(os.environ.get('CLAUDE_CODE_MCP_SERVER_URL') or '')
        if answer:
            record_connection()
        print(json.dumps(answer))
        return 0
    print('usage: credentials.py --headers', file=sys.stderr)
    return 64


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
