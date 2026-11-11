"""An opener that never follows a redirect, and keeps an HTTPS connection open until the body is read.

urllib re-sends a request's headers, `Authorization` included, to the target of a 3xx. A script that
sends the backend bearer must treat a redirect as an answer, not a hop: a loopback listener or a
misconfigured backend would otherwise carry the header to any host it names.

urllib also sends `Connection: close` on every request, so the server closes the connection the moment
the body is written. Behind the Claude Code Bash sandbox, HTTPS goes through the sandbox's CONNECT proxy,
which destroys the client side of the tunnel as soon as the server side closes and drops whatever it has
not flushed yet: a body past a few tens of KB then ends in `IncompleteRead`. Without the header the
server keeps the connection open, the client reads the whole body by its framing, and the client is the
one that closes.

The trade-off: an HTTP/1.1 server that sends neither `Content-Length` nor chunked encoding, and relies on
closing to end the body, ends in the read timeout instead of EOF. An HTTP/1.0 server closes as before.

Every opener built here identifies the plugin in `User-Agent`. The hosted endpoint rejects requests that
carry urllib's default agent, `Python-urllib/3.x`, with a 401, so every caller sets an explicit one.
"""

from __future__ import annotations

import http.client
import sys
import urllib.request
from pathlib import Path
from typing import Any

# This file is loaded by path as well as by name; only the second has its directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _client_handshake  # noqa: E402 - after the sys.path line it depends on

_PRODUCT = 'plurai-simulation-plugin'


def _user_agent() -> str:
    """`plurai-simulation-plugin/<manifest version>`, or `.../unknown` when no manifest names a valid version."""
    return _client_handshake.user_agent()


#: Sent by every opener built here and by the scripts that open their own connection to the backend.
USER_AGENT = _user_agent()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def keep_alive(connection: type[http.client.HTTPConnection]) -> type[http.client.HTTPConnection]:
    """``connection`` with urllib's ``Connection: close`` request header removed."""

    class KeepAlive(connection):  # type: ignore[misc, valid-type]
        def request(self, method: str, url: str, body: Any = None, headers: Any = None, **kwargs: Any) -> None:
            kept = {name: value for name, value in (headers or {}).items() if name.lower() != 'connection'}
            super().request(method, url, body, kept, **kwargs)

    return KeepAlive


class KeepAliveHTTPS(urllib.request.HTTPSHandler):
    """The HTTPS handler every opener here uses. A caller that needs its own HTTPS handler subclasses this
    one, so both behaviours apply: urllib dispatches to one handler per scheme."""

    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        return super().do_open(keep_alive(http_class), req, **kwargs)


class _ProxyUnlessLoopback(urllib.request.ProxyHandler):
    """The environment's proxy for every request except one to a loopback address, which goes straight to the host:
    a bearer for a local backend must not ride to whatever ``http_proxy`` names."""

    def proxy_open(self, req: Any, proxy: str, type: str) -> Any:  # noqa: A002 - urllib's own signature
        sys.path.insert(0, str(_client_handshake.PLUGIN_ROOT / 'hooks'))
        import url_guard  # noqa: PLC0415 - the one loopback test; a failure to load it fails the request, never the bypass

        if url_guard.is_loopback(req.full_url):
            return None
        return super().proxy_open(req, proxy, type)


def build(*extra: urllib.request.BaseHandler, direct: bool = False) -> urllib.request.OpenerDirector:
    """An opener without redirects, plus the handlers in ``extra``. With ``direct``, no environment proxy
    is used. Without it, the environment's proxy serves every request except one to a loopback address.
    Every request it sends carries ``X-Plurai-Client`` (``_client_handshake.Stamp``): the backend refuses a call
    that names no client, so an opener built any other way is not a transport to the backend."""
    proxy = [urllib.request.ProxyHandler({}) if direct else _ProxyUnlessLoopback()]
    own = [handler for handler in extra if isinstance(handler, urllib.request.HTTPSHandler)]
    if not all(isinstance(handler, KeepAliveHTTPS) for handler in own):
        raise TypeError('an HTTPS handler in `extra` must subclass KeepAliveHTTPS')
    https = [] if own else [KeepAliveHTTPS()]
    opener = urllib.request.build_opener(*proxy, _NoRedirect, _client_handshake.Stamp(), *https, *extra)
    # Applied to a request that names no `User-Agent` of its own; the handler adds it unredirected.
    opener.addheaders = [('User-Agent', USER_AGENT)]
    return opener


#: ``OPENER.open(request, timeout=...)`` in place of ``urllib.request.urlopen``.
OPENER = build()

#: The same, for a loopback backend: no environment proxy, which would otherwise carry a plain-http
#: bearer to whatever ``http_proxy`` names.
DIRECT_OPENER = build(direct=True)
