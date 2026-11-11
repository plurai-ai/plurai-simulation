#!/usr/bin/env python3
"""fetch_env_file.py — fetch env files over REST, paging any the backend withholds over its wire cap.

    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/scripts/fetch_env_file.py" --env <env_id> \\
        --path simulation_env/tests/test_mock_witness.py [--path <relpath> ...] --out-dir <dir>

Each path is read with `GET /artifacts/<env>/files/<path>`. A file the backend withholds over its per-file wire cap
(`X-Plurai-Withheld`, or an `omitted` placeholder body from a backend that predates the header) is read in pages from
offset 0, following `X-Plurai-Next-Offset`: every page must carry the same `X-Plurai-Sha256` and an
`X-Plurai-Offset` equal to the bytes received so far, and the whole must match `X-Plurai-Total-Bytes` and that
sha256. A file rewritten mid-read restarts once. The file is written atomically, in bytes, to `<out-dir>/<basename>`.

A path that is not fetched is one line on stdout, `NOT_FETCHED <path> <reason>: <detail>`, its stale local copy is
removed (it belongs to another world), and it is recorded in `<out-dir>/fetch_failures.json`, which the mock gate reads
as the coverage gap it is. Reasons: `backend_has_no_paged_read`, `sha256_mismatch`, `file_changed_during_read`,
`not_in_env`, `backend_unreachable` (transport, a refused bearer, any other HTTP status).

The base and the bearer come from the plugin's one resolver (`hooks/_login_store.py`, through
`hooks/url_guard.py`), as for every REST script here; a redirect is an answer, never a hop.

Exit: 0 every path fetched · 1 some path not fetched (never a stop: the gate stays advisory) · 64 usage.
Stdlib-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'hooks'))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"

NO_PAGED_READ, SHA_MISMATCH, CHANGED, NOT_IN_ENV, UNREACHABLE = (
    'backend_has_no_paged_read', 'sha256_mismatch', 'file_changed_during_read', 'not_in_env', 'backend_unreachable')
FAILURES = 'fetch_failures.json'
SCHEMA = 'plurai-env-fetch/1'
TIMEOUT = 60

#: One GET: (status, body, lower-cased headers); status 0 is no HTTP answer at all.
Getter = Callable[[str], tuple[int, bytes, dict[str, str]]]


class FetchFailure(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f'{reason}: {detail}')
        self.reason, self.detail = reason, detail


class _Changed(Exception):
    """The file was rewritten between two pages: the read restarts once."""


def _get(url: str, token: str, opener: urllib.request.OpenerDirector) -> tuple[int, bytes, dict[str, str]]:
    """(status, body, lower-cased headers); status 0 is no HTTP answer at all."""
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:  # noqa: S310 - the bound backend, checked in main
            return response.status, response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b'', {k.lower(): v for k, v in (exc.headers or {}).items()}
    except (OSError, ValueError) as exc:
        return 0, repr(exc).encode(), {}


def _is_placeholder(body: bytes, name: str) -> bool:
    """The body a backend serves in place of a file it withholds: `<name omitted — …>`, or JSON `schema: omitted`."""
    text = body.decode('utf-8', 'replace').strip()
    if text.startswith(f'<{name} omitted — ') and text.endswith('>'):
        return True
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get('schema') == 'omitted' and data.get('file') == name


def _int(headers: dict[str, str], name: str) -> int | None:
    try:
        return int(headers[name])
    except (KeyError, ValueError):
        return None


def _failure(status: int, body: bytes, rel: str) -> FetchFailure:
    if status == 404:
        return FetchFailure(NOT_IN_ENV, f'the env serves no {rel}')
    return FetchFailure(UNREACHABLE, f'HTTP {status}: {body[:200].decode("utf-8", "replace")}' if status else body.decode())


def _pages(get: Getter, url: str, rel: str) -> bytes:
    """The whole file, page by page. Raises :class:`_Changed` when it was rewritten under the read."""
    buf, sha, total, offset = bytearray(), '', 0, 0
    while True:
        status, body, headers = get(f'{url}?offset={offset}')
        if status == 416:
            raise _Changed(f'offset {offset} is outside the file now')
        if status != 200:
            raise _failure(status, body, rel)
        page_offset, page_total, page_sha = _int(headers, 'x-plurai-offset'), _int(headers, 'x-plurai-total-bytes'), headers.get('x-plurai-sha256')
        if page_offset is None or page_total is None or not page_sha:
            raise FetchFailure(NO_PAGED_READ, 'the backend answered ?offset= without the X-Plurai-Offset/Total-Bytes/Sha256 headers')
        if page_offset != len(buf):
            raise FetchFailure(NO_PAGED_READ, f'the backend served offset {page_offset} after {len(buf)} bytes')
        if not sha:
            sha, total = page_sha, page_total
        elif (page_sha, page_total) != (sha, total):
            raise _Changed(f'sha256 {sha} became {page_sha} at offset {offset}')
        buf += body
        following = _int(headers, 'x-plurai-next-offset')
        if following is None:
            break
        if following <= offset or len(buf) > total:
            raise FetchFailure(NO_PAGED_READ, f'X-Plurai-Next-Offset {following} after offset {offset} does not advance through {total} bytes')
        offset = following
    if len(buf) != total or hashlib.sha256(buf).hexdigest() != sha:
        raise FetchFailure(SHA_MISMATCH, f'{len(buf)} reassembled bytes (sha256 {hashlib.sha256(buf).hexdigest()}) are not the {total} bytes of sha256 {sha}')
    return bytes(buf)


def fetch(get: Getter, base: str, env_id: str, rel: str) -> bytes:
    """The bytes of env file ``rel``, paged when the backend withholds it. Raises :class:`FetchFailure`."""
    url = f'{base}/artifacts/{quote(env_id, safe="")}/files/{quote(rel, safe="/")}'
    status, body, headers = get(url)
    if status != 200:
        raise _failure(status, body, rel)
    if 'x-plurai-withheld' not in headers and not _is_placeholder(body, Path(rel).name):
        return body
    try:
        return _pages(get, url, rel)
    except _Changed:
        pass  # rewritten under the read: once more from offset 0
    try:
        return _pages(get, url, rel)
    except _Changed as exc:
        raise FetchFailure(CHANGED, str(exc)) from exc


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_failures(directory: Path | None) -> dict[str, dict[str, str]]:
    """`<directory>/fetch_failures.json` `failures`: basename -> {path, reason, detail}; {} when none is recorded."""
    try:
        data = json.loads((directory / FAILURES).read_text(encoding='utf-8')) if directory else None
    except (OSError, ValueError):
        return {}
    failures = data.get('failures') if isinstance(data, dict) and data.get('schema') == SCHEMA else None
    return {str(k): v for k, v in failures.items() if isinstance(v, dict)} if isinstance(failures, dict) else {}


def gap_reason(directory: Path | None) -> str:
    """The coverage gap a failed fetch leaves, as one sentence; '' when every file arrived."""
    failures = read_failures(directory)
    named = ', '.join(f'{name} ({f.get("reason")})' for name, f in sorted(failures.items()))
    return f"the world's witness files did not reach this machine: {named}; no witness test ran" if named else ''


def backend_getter(caller: str = 'fetch_env_file') -> tuple[str, Getter, str]:
    """(the bound backend's base, a GET that carries the bearer only there, why it may not be called — '' when it may)."""
    base = url_guard.server_url()
    token = url_guard.bearer(base)
    refused = ''
    if not base:
        refused = url_guard.configuration_problem(caller)
    elif token and not url_guard.is_backend_url(base):
        refused = url_guard.refused_message(base)
    elif not token and not url_guard.is_loopback(base):
        refused = f'{base} is not loopback and no bearer is set'
    opener = _no_redirect.DIRECT_OPENER if url_guard.is_loopback(base) else _no_redirect.OPENER

    def get(url: str) -> tuple[int, bytes, dict[str, str]]:
        return _get(url, token, opener)

    return base, get, refused


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Fetch env files over REST, paging any withheld over the wire cap.')
    ap.add_argument('--env', required=True, help='the env id')
    ap.add_argument('--path', action='append', required=True, help='an env-relative path; repeatable')
    ap.add_argument('--out-dir', required=True, type=Path, help='each file lands at <out-dir>/<basename>')
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 64
    base, get, refused = backend_getter()
    failures: dict[str, dict[str, str]] = {}
    for rel in args.path:
        dest = args.out_dir / Path(rel).name
        try:
            if refused:
                raise FetchFailure(UNREACHABLE, refused)
            data = fetch(get, base, args.env, rel)
        except FetchFailure as exc:
            failures[dest.name] = {'path': rel, 'reason': exc.reason, 'detail': exc.detail}
            dest.unlink(missing_ok=True)
            print(f'NOT_FETCHED {rel} {exc.reason}: {exc.detail}')
            continue
        _write_atomic(dest, data)
        print(f'FETCHED {rel} {len(data)} bytes')
    record = args.out_dir / FAILURES
    if failures:
        _write_atomic(record, (json.dumps({'schema': SCHEMA, 'env_id': args.env, 'failures': failures}, indent=2, sort_keys=True) + '\n').encode())
    else:
        record.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
