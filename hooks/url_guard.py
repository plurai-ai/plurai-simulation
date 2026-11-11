"""The bearer rules the plugin's scripts share: where the Plurai token may go, and which backend a session uses.

A library, not a hook. Scripts and shell callers ask it whether a url is loopback (``--is-loopback``), whether
the bearer may go to a url (``--is-backend``, ``--token-for``), what the resolved backend is (``--backend``) and
why the session pin cannot be used (``--pin-problem``); about twenty scripts import it.

It was also a ``PreToolUse`` hook on the backend MCP server that denied a tool call with no usable bearer. That
registration is retired: it never chose where the bearer goes, because Claude Code takes the MCP url and the
static header from its launch environment and ``headersHelper`` binds the saved token to the backend's origin
(``_login_store.headers``), and on Codex and Copilot the stdio bridge refuses the same cases
(``scripts/mcp_bridge.py``). With no bearer the handshake gets a 401 and no tools are listed, and the flows
refuse at Step 0 (``hooks/_readiness.py``).

What nothing covers, stated: a launch ``PLURAI_URL`` that is http and not loopback (or that two parsers read
differently) is refused by ``_login_store.backend_origin``, yet no hook denies the tool calls of such a session. A
command that is not a flow sends its documents to that url in cleartext; the flows still refuse at Step 0, the
handshake has already sent the token, and no model can set the launch url.
"""

from __future__ import annotations

import os
import re
import sys

# `python -I` (how the shell callers start this file) leaves the script directory off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import session_pin
except ImportError:  # a sibling that is missing must not stop the hook; every answer below is then "no"
    session_pin = None
try:
    import _login_store
except ImportError:  # the guard then denies every call and the helpers send no bearer
    _login_store = None


def _is_loopback(url: str) -> bool:
    """True iff ``url`` targets localhost / 127.0.0.0/8 / ::1. Unparseable → False (remote).

    A URL with no scheme (``localhost:8200``) is read as ``http://``, as curl reads it. A dotted
    decimal host with fewer than four parts (``127.1``) is read the way the resolver behind curl
    reads it, as ``inet_aton`` does. Any other host must be ``localhost`` or an IP literal, so a
    NAME that merely starts like one (``localhost.evil.example``, ``127.evil.example``) is remote.
    """
    import ipaddress
    import re
    import socket
    from urllib.parse import urlsplit

    try:
        hostname = urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return False
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", hostname):
        return False
    try:
        return ipaddress.IPv4Address(socket.inet_aton(hostname)).is_loopback
    except OSError:
        return False


def is_loopback(url: str) -> bool:
    """The loopback test other modules ask (the bridge pins loopback over plain http); see ``_is_loopback``."""
    return _is_loopback(url)


def bearer(url: str) -> str:
    """The token to send to ``url``: the resolved backend's, only when ``url`` has its origin.

    It says nothing about whether ``url`` may receive the token: callers ask ``is_backend_url`` too.
    """
    return _login_store.token_for(url) if _login_store is not None else ""


def may_send(url: str, token: str) -> bool:
    """Whether a script holding ``token`` (possibly empty) may make a request to ``url``: the one rule every transport follows.

    A bearer goes only to a backend the session is bound to (``is_backend_url``). With no bearer the request goes only to a
    loopback address, a local backend that needs none; a remote backend with no token would answer 401.
    """
    return is_backend_url(url) if token else is_loopback(url)


def server_url() -> str:
    """The REST base of the resolved backend (``_login_store.resolve_backend``), or ``""`` when none is usable.

    The one answer scripts take for "where do I send this request"; ``configuration_problem`` says why it is empty.
    """
    if _login_store is None:
        return ""
    backend = _login_store.resolve_backend()
    return backend.url if backend.configured else ""


def configuration_problem(stopper: str) -> str:
    """Why there is no usable backend, as the text ``stopper`` (a script's name) prints: the shared sign-up and login message or the configuration error."""
    if _login_store is None:
        return f"{stopper} stopped: the plugin's login module is missing; reinstall the plugin."
    return _login_store.resolve_backend().refusal(stopper)


def has_bearer() -> bool:
    """Whether the resolved backend carries a bearer at all, wherever it may go (``bearer`` answers per url)."""
    return _login_store is not None and bool(_login_store.resolve_backend().token)


def backend_origins() -> set:
    """The non-loopback origins a bearer may reach: the session pin's (see ``session_pin.py``), and none without a pin.

    They are never read from the environment of the process that asks, which a command prefix sets.
    """
    return session_pin.backend_origins() if session_pin is not None else set()


def pin_problem() -> str:
    """Why this session's pin is not used although it exists (``session_pin.refusal``), or ``""``: the reason a refusal of the bearer's destination leaves out."""
    return session_pin.refusal() if session_pin is not None else ""


#: Said when a Claude Code session has no pin because its hooks did not run (``session_pin.unpinned_claude_session``).
HOOKS_OFF_REASON = (
    "the plugin's hooks are off in this session, so it has no record of the backend it was started with, and the token "
    "goes only to the backend of the saved login or, for a token from the environment, to a Plurai-hosted https address"
)


def refused_message(url: str, tail: str = "; the bearer was not sent") -> str:
    """The line a script prints when it holds a bearer and ``url`` is not the backend the session is bound to, plus the reason
    when the session's pin exists but cannot be used (``pin_problem``), or when there is none because the hooks are off
    in this session.

    ``tail`` ends the sentence (it may be ``""``); the reason follows as a sentence of its own. Never raises: a script that is
    refusing must still refuse, with or without the reason.
    """
    line = f"{url} is not the backend this session is bound to{tail}"
    try:
        reason = pin_problem()
        if not reason and session_pin is not None and session_pin.unpinned_claude_session():
            reason = HOOKS_OFF_REASON
    except Exception:  # noqa: BLE001 - the reason is advice; the refusal stands without it
        reason = ""
    if not reason:
        return line
    return f"{line}{' ' if line.endswith('.') else '. '}{reason[0].upper()}{reason[1:]}."


def bearer_refused_message(url: str) -> str:
    """:func:`refused_message` for a request that was not sent at all: the backend scripts print this when ``may_send`` says no."""
    return refused_message(url, ", so the bearer would not be sent there. Nothing was sent.")


def is_backend_url(url: str) -> bool:
    """True iff ``url`` may receive the bearer: it has the scheme, host and port of a pinned backend.

    The scheme is part of the answer because the same host over cleartext would carry the bearer
    unprotected, and the port because a different port of the same host is a different service. A loopback
    backend is pinned like any other, with its port: loopback as such is not enough, because a remote backend's
    token must never reach a local listener. A URL that two parsers could read as two hosts is not a backend
    (``session_pin.origin``), loopback spellings included. Callers ask this only when they hold a bearer; a
    tokenless local backend needs no pin.

    With no pin, more origins qualify on Claude Code when the session's hooks did not run: the saved login's own
    (``_saved_login_origin``), and for a token from the environment a Plurai-operated https host (``_plurai_operated_origin``).
    """
    if session_pin is None:
        return False
    found = session_pin.origin(url)
    if found is None:
        return False
    return found in backend_origins() or _saved_login_origin(found) or _plurai_operated_origin(found)


#: The domains Plurai operates its backends under. A host qualifies only as a strict subdomain (``<label>.plurai.uk``):
#: the bare domain is not under the suffix, as ``_login_store.ALLOWED_SUFFIX`` reads its glob.
PLURAI_OPERATED_SUFFIXES = (".plurai.ai", ".plurai.uk")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def _plurai_operated_origin(found: tuple) -> bool:
    """Whether ``found`` is a Plurai-operated https host a token from the environment may go to, in a Claude Code session
    with no pin (limited mode).

    A customer's backend is often a ``*.plurai.uk`` box reached with the environment pair (``PLURAI_URL`` and
    ``PLURAI_TOKEN`` from the environment that launched the session), which the saved login's rule never covers. With
    hooks the SessionStart pin records that launch environment; without a hook nothing does, and the environment a script
    sees is one a command prefix can set. So the only guarantee that needs no environment is where the bearer lands: a
    host Plurai operates. A prompt-injected ``PLURAI_URL`` naming any other host still gets nothing.

    The origin is ``session_pin.origin``'s (no userinfo, backslash, ``%``, whitespace or non-ASCII host), and it must be
    https on the default port, its host one or more plain DNS labels followed by a suffix of ``PLURAI_OPERATED_SUFFIXES``
    (no trailing dot, not the bare domain). The token must come from the environment and not be the saved login's, whose
    own rule (``_saved_login_origin``) keeps it to its own backend. The same edges as that rule apply: a made-up session id
    reaches "no pin" from any harness, so a pinned session's token from the environment can reach another Plurai host.
    """
    if _login_store is None or session_pin is None:
        return False
    try:
        scheme, host, port = found
        if scheme != "https" or port != 443 or not isinstance(host, str):
            return False
        suffix = next((s for s in PLURAI_OPERATED_SUFFIXES if host.endswith(s)), None)
        if suffix is None:
            return False
        labels = host[: -len(suffix)].split(".")
        if not labels or not all(_HOST_LABEL.fullmatch(label) for label in labels):
            return False
        if not session_pin.unpinned_claude_session():
            return False
        exported = _login_store.environment_token(os.environ)
        if not exported:
            return False
        saved = _login_store._saved(os.environ)
        return not (saved.token and exported == saved.token)
    except Exception:  # noqa: BLE001 - no answer is "no": the bearer is not sent
        return False


def _saved_login_origin(found: tuple) -> bool:
    """Whether ``found`` is the saved login's own origin in a Claude Code session with no pin (limited mode).

    Every part of the answer comes from the saved credential FILE, read where ``_login_store`` reads it (the account's
    home, never a ``HOME`` a command set): the session has no pin (``session_pin.unpinned_claude_session``), the file holds
    a usable login, ``found`` is its origin (scheme, host and port), and the bearer in this environment is none or that
    login's own (the resolver exports it, so later commands carry it). A url in the environment is no part of it, so a
    prefixed ``PLURAI_URL`` names no new destination, and a token of another backend in the environment is never sent.
    Nothing here reads a flow marker: those live under ``HOME``, which one command sets.

    What it does give up: a command prefixed with a made-up session id (the other harnesses' variables unset) reaches "no
    pin" from a shell of any harness, hooks on or off, and so does a session whose pin file is malformed or of an older
    version (``session_pin.unpinned_claude_session``). A session pinned to one backend can then reach the saved login's
    backend: a loss of one-backend consistency, never a token leak, since the token sent there is the login's own.
    """
    if _login_store is None or session_pin is None:
        return False
    try:
        if not session_pin.unpinned_claude_session():
            return False
        saved = _login_store._saved(os.environ)
        if not saved.token or saved.origin is None or found != saved.origin:
            return False
        exported = _login_store.environment_token(os.environ)
        return not exported or exported == saved.token
    except Exception:  # noqa: BLE001 - no answer is "no": the bearer is not sent
        return False


if __name__ == "__main__":
    # The direct entries. `url_guard.py --is-loopback URL` exits 0 for a loopback URL and 1 for any
    # other. Every script that applies the tokenless-local-dev rule asks here, so there is one
    # copy of it. A malformed call exits 64, which a caller reads as "not loopback": fail closed.
    # `url_guard.py --is-backend URL` exits 0 when the bearer may be sent to URL (an origin the session pin records, or with
    # no pin on Claude Code the saved login's own) and 1 otherwise: the entry for shell callers that hold the bearer and
    # dial a URL. A malformed call exits 64, which a caller reads as "no": fail closed.
    if sys.argv[1:2] == ["--is-loopback"] and len(sys.argv) == 3:
        sys.exit(0 if _is_loopback(sys.argv[2]) else 1)
    if sys.argv[1:2] == ["--is-backend"] and len(sys.argv) == 3:
        sys.exit(0 if is_backend_url(sys.argv[2]) else 1)
    # `url_guard.py --pin-problem` prints why the session's pin cannot be used (its folder or file is another account's, a symlink
    # or writable by others) and exits 0; with nothing wrong with it, it prints nothing and exits 1. THE EXIT CODE IS INVERTED
    # relative to `--is-backend`: 0 means "there is a problem", and 1 does not mean the pin is fine (a missing pin, or a pin that
    # `read` finds malformed, also prints nothing and exits 1). A shell caller that was refused by `--is-backend` asks this only to
    # tell "not the session's backend" from "the session's pin is unusable"; it never reads exit 1 as permission.
    if sys.argv[1:2] == ["--pin-problem"] and len(sys.argv) == 2:
        reason = pin_problem()
        if reason:
            print(reason)
        sys.exit(0 if reason else 1)
    # `url_guard.py --backend` prints the resolved REST base and exits 0, or prints why there is none (the shared
    # sign-up and login message, or the configuration error) and exits 1. The entry for shell callers that
    # take the backend from the plugin's one resolver instead of parsing PLURAI_URL themselves.
    if sys.argv[1:2] == ["--backend"] and len(sys.argv) == 2:
        root = server_url()
        print(root or configuration_problem("plurai"))
        sys.exit(0 if root else 1)
    # `url_guard.py --token-for URL` prints the bearer for URL (the environment's, else the saved credential)
    # and exits 0 when URL may receive it; otherwise it prints nothing and exits 1. The entry for shell
    # callers that send the bearer and have none in their environment.
    if sys.argv[1:2] == ["--token-for"] and len(sys.argv) == 3:
        found = bearer(sys.argv[2]) if is_backend_url(sys.argv[2]) else ""
        if found:
            print(found)
        sys.exit(0 if found else 1)
    sys.exit(64)
