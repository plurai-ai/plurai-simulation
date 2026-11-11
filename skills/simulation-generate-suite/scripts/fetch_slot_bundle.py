#!/usr/bin/env python3
"""Materialize a scenario set's slot bundles into the customer workspace.

The durable writer for slot bundles. An ad-hoc curl loop over a fixed file list would miss
`agent_context.yaml` (which carries any generated document inline as a base64 data URI and can be
large), so it would never reach the customer's disk.

ENUMERATE, NEVER HARDCODE. `GET /artifacts/<set-id>` returns `detail.files`, the backend's own
`[{path, size_bytes}]` manifest of every fetchable relpath, and its declared contract is that
the manifest is a SUBSET of what the fetch routes serve. So this script keeps no copy of the
backend's SCENARIO_FILES allow-list: a name added there lands here with no change, which a
client-side copy would silently omit.

THE MANIFEST IS UNTRUSTED INPUT. It names where bytes get written, so a path outside `--out`
writes outside `--out` (`../ESCAPED.txt` would land a body in the parent directory). Both
backend routes confine their own side; this script is the remaining hop, so
every path is checked for shape AND resolved target before a single body is requested, and one
bad path refuses the whole run rather than writing the good ones first.

`size_bytes` IS THE INTEGRITY CHECK, not a nicety. Three backend paths answer 200 with a
PLACEHOLDER STRING in place of the bytes — a file over the 256 KB wire cap (`<name> omitted
- ...>`, or a `{"schema": "omitted"}` object for .json) and a binary document (`<binary
document - ...>`). Each would otherwise be written under the real filename and reported as
landed. A short read is the only signal that separates them from content, so a body whose
length differs from the manifest's is a failure — and an entry with NO `size_bytes` is a
failure too, never an unchecked write, since it is the check being absent. The backend states
the SERVED length (a spend-redacted file is shorter than on disk), and marks a file it serves
only as the over-cap placeholder `omitted: true` — or names it in `withheld_files` (`[{path,
bytes}]`, read when the response carries it; an older backend does not): that one is not
requested and not a failure — it is listed in `<slot>/.omitted`, because it stays on the backend
by design and the run reads it there.

TWO LANES, because one of them cannot carry bytes. Slot files go through
`GET /artifacts/<id>/files/<relpath>`, which serves `text/plain; charset=utf-8` off the text
collector — correct for YAML/JSON/txt and for base64 inside them. `documents/<rel>` goes
through `GET /documents/<set>/<slot>/<rel>`, which `read_bytes()` with a real media type; the
text lane answers that path with the binary placeholder above. No `--extra` flag: the manifest
enumerates `documents/**` recursively, and no fetchable class exists that it cannot name.

NOTHING PARTIAL IS LEFT UNLABELLED. A mid-run failure leaves the bytes that did land and
writes `<slot>/.incomplete` naming what did not, because the deliverable is read months later
by someone who has only the directory — stdout is gone by then. The partial files are KEPT
rather than rolled back: they are evidence of what the backend served, and a deleted slot is
indistinguishable from one never fetched.

THE BEARER GOES ONLY TO THE CONFIGURED BACKEND. The backend and the token come from the plugin's one resolver
(`hooks/_login_store.py`): there is no `--server` or `--token`. The token rides every request, so a backend that
is not loopback and not the one the session was started with (the session pin, `hooks/session_pin.py`) is
refused before anything is requested, and a redirect from the backend is an error rather than a hop that
carries the header along.
`--out` must sit under the working directory, the plurai workdir or the temp directory, unless
`--allow-outside-workdir` says otherwise.

Exit: 0 every listed byte landed; 1 a refusal or a failure; 2 no configured backend, an empty
--set-id, an --out outside the working tree, or a backend that is not the one this session is
bound to (the client asked the wrong question — nothing was requested);
3 INCONCLUSIVE — the manifest could not be read, or is absent or empty, which means "nothing
found yet" (job still running, workspace gone) and never "this set has no files".

Usage: fetch_slot_bundle.py --set-id <id> --out "$ART/scenarios" [<slot> ...]
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import posixpath
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'hooks'))

import _confine  # noqa: E402 - after the sys.path lines it depends on
import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
import backend_identity_marker  # noqa: E402
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"

REQUIRED = ('scenario.yaml',)  # a slot that cannot produce this is a failure, not an absence
MARKER = '.incomplete'
OMITTED_MARKER = '.omitted'
_INCOMPLETE_HEADER = 'this bundle is INCOMPLETE — these files did not land:'
_OMITTED_HEADER = 'these files stay on the backend — over its wire cap, so it withholds them (the run reads them there):'

EXIT_OK, EXIT_FAILED, EXIT_INCONCLUSIVE = 0, 1, 3


def _get(url: str, token: str, timeout: float) -> bytes:
    # A blank token sends no Authorization header: a bare `Bearer ` is neither tokenless nor valid.
    token = token.strip()
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    with _no_redirect.OPENER.open(request, timeout=timeout) as response:  # noqa: S310 — checked against the pinned backend in main()
        return response.read()


def manifest(server: str, set_id: str, token: str, timeout: float) -> tuple[list[dict] | None, dict[str, object]]:
    """The backend's `[{path, size_bytes}]` for one set (None when it declined to say), and the
    `{path: bytes}` of every file it names as withheld."""
    meta = json.loads(_get(f'{server}/artifacts/{set_id}', token, timeout))
    detail = meta.get('detail') or {}
    return detail.get('files'), withheld_files(meta.get('withheld_files', detail.get('withheld_files')))


def withheld_files(raw: object) -> dict[str, object]:
    """`{path: bytes}` from a `withheld_files` list; empty when the backend predates the field.

    Untrusted like the rest of the manifest: an entry without a string `path` names nothing, and a
    path the manifest does not list is never fetched either way."""
    if not isinstance(raw, list):
        return {}
    return {entry['path']: entry.get('bytes') for entry in raw if isinstance(entry, dict) and isinstance(entry.get('path'), str)}


def unsafe(path: str, out_dir: str) -> str | None:
    """Why this manifest path may not be written under `out_dir`, or None when it may."""
    if not path or path != posixpath.normpath(path) or path.startswith('/') or path.split('/')[0] == '..':
        return 'not a normalized relative path'
    if ntpath.splitdrive(path)[0] or '\\' in path or '://' in path:
        return 'carries a drive letter, a backslash or a URL scheme'
    root = Path(out_dir).resolve()
    if not (root / path).resolve().is_relative_to(root):
        return f'resolves outside --out ({out_dir})'
    return None


def refusals(entries: list[dict], slots: set[str]) -> list[str]:
    """What else makes the manifest unfetchable — checked BEFORE any body is requested.

    Path safety is NOT repeated here: it runs once in `main`, ahead of slot derivation, because
    a slot name read off an unchecked path (`..`) is not a slot. Two copies of one guard also
    mean neither is pinned — removing either leaves the other answering for it."""
    refused = [f'{entry["path"]!r}: no size_bytes, so nothing could verify its content' for entry in entries if entry.get('size_bytes') is None]
    for slot in sorted(slots):
        listed = {entry['path'][len(slot) + 1:] for entry in entries if entry['path'].startswith(f'{slot}/')}
        refused += [f'{slot}: MISSING_REQUIRED {name}' for name in REQUIRED if name not in listed]
    return refused


def _url_for(server: str, set_id: str, path: str) -> str:
    """Route one manifest path to the lane that can actually carry its bytes.

    PERCENT-ENCODED: a manifest path is a file name, and a space, a `#` or a `?` in one builds a
    URL whose tail is silently dropped or read as a fragment — a 404 nobody can diagnose from the
    name printed beside it. `/` stays safe: these are path segments, not one component.
    """
    slot, _, rest = path.partition('/')
    if rest.startswith('documents/'):
        return f'{server}/documents/{set_id}/{quote(slot)}/{quote(rest[len("documents/"):])}'
    return f'{server}/artifacts/{set_id}/files/{quote(path)}'


def fetch(server: str, set_id: str, entries: list[dict], out_dir: str, token: str, timeout: float) -> dict[str, str | None]:
    """Write every listed path; return ``{path: None if its bytes landed intact else why not}``."""
    results: dict[str, str | None] = {}
    for entry in entries:
        path, expected = entry['path'], entry['size_bytes']
        try:
            body = _get(_url_for(server, set_id, path), token, timeout)
        except urllib.error.HTTPError as exc:
            # The manifest promised this path is fetchable, so even a 404 here is a broken
            # contract — never the "this slot had nothing" that an unlisted path would mean.
            results[path] = f'HTTP {exc.code}, but the manifest listed it'
        except Exception as exc:  # noqa: BLE001 — a broken fetch must not read as an absent file
            # The MESSAGE, not just the class: this reason is what survives into `.incomplete`,
            # and a connection refusal, a TLS failure and a DNS failure are all `URLError`.
            results[path] = f'{type(exc).__name__}: {exc}'
        else:
            if len(body) != expected:
                results[path] = f'{len(body)}B for a {expected}B file — placeholder, not content'
                continue
            full = os.path.join(out_dir, path)
            try:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'wb') as handle:
                    handle.write(body)
            except OSError as exc:
                # Without this guard the traceback escapes `main()`, so `mark_incomplete` never
                # runs and the half-written bundle is left with nothing on disk saying so.
                results[path] = f'{type(exc).__name__}: {exc}'
                continue
            results[path] = None
    return results


def mark_incomplete(out_dir: str, failed: dict[str, str], *, marker_name: str = MARKER, header: str = _INCOMPLETE_HEADER) -> list[str]:
    """Label every slot left partial (or holding a backend-only file), so the directory says so without its stdout."""
    by_slot: dict[str, list[str]] = {}
    for path, reason in sorted(failed.items()):
        by_slot.setdefault(path.split('/', 1)[0] if '/' in path else '', []).append(f'{path}: {reason}')
    written = []
    for slot, lines in by_slot.items():
        marker = os.path.join(out_dir, slot, marker_name) if slot else os.path.join(out_dir, marker_name)
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, 'w', encoding='utf-8') as handle:
                handle.write(header + '\n' + '\n'.join(lines) + '\n')
        except OSError as exc:
            # The marker write fails for the SAME reasons the file write does — a full disk, a
            # read-only tree, a permission. Guarding only the file write would let the traceback
            # escape HERE instead, so no marker would land. Say it on stderr, which is the one
            # channel left.
            print(f'could not mark {slot or "<set-level>"} incomplete ({type(exc).__name__}: {exc})', file=sys.stderr)
            continue
        written.append(os.path.relpath(marker, out_dir))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--set-id', required=True)
    parser.add_argument('--out', required=True, help='the scenarios directory, e.g. "$ART/scenarios"')
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('slots', nargs='*', help='slot folders to keep; default every slot the set has')
    _confine.add_flag(parser)
    args = parser.parse_args(argv)
    if why := _confine.refusal(args.out, allow_outside=args.allow_outside_workdir):
        print(f'fetch_slot_bundle: {why} Nothing requested.', file=sys.stderr)
        return 2
    args.server = url_guard.server_url()
    if not args.server:
        print(url_guard.configuration_problem('fetch_slot_bundle'), file=sys.stderr)
        return 2
    args.token = url_guard.bearer(args.server)
    if not args.set_id.strip():
        # REFUSED BY NAME, at the same exit as a missing backend, because it is the same class
        # of fault: the client asked the wrong question. The documented invocation is
        # `--set-id "$(cat "$WORKDIR/.scenario_set_id")"`, so a missing marker expands to the
        # empty string, builds `GET <server>/artifacts/` and comes back 404/405 — caught below
        # and printed as INCONCLUSIVE, on which the skill text says "re-run it". That loop never
        # resolves and under the headless driver it burns the phase budget to a STALL.
        print('fetch_slot_bundle: --set-id is empty — the id was not read (is '
              '`$WORKDIR/.scenario_set_id` there?). Nothing requested.', file=sys.stderr)
        return 2
    # The bearer rides every request below, so the backend must be the one the session was started with.
    # Checked before anything is requested: this command's environment can name any host.
    if args.token and not url_guard.is_backend_url(args.server):
        print(
            'fetch_slot_bundle: REFUSED — '
            + url_guard.refused_message(
                args.server,
                ', so the bearer would not be sent there. The backend is the one the session was started with '
                '(PLURAI_URL in the environment Claude Code launched with, or the saved login); with no '
                'session pin no backend qualifies. Nothing requested.',
            ),
            file=sys.stderr,
        )
        return 2
    # Same class again, and the one nothing else here can distinguish: a set id is minted on the
    # backend that generated it, so a backend that is a SIBLING deployment 404s the manifest
    # and reports INCONCLUSIVE — on which the skill text says "re-run it", forever.
    if (refusal := backend_identity_marker.split(args.server, caller='fetch_slot_bundle')) is not None:
        print(refusal, file=sys.stderr)
        return 2

    try:
        listed, withheld = manifest(args.server, args.set_id, args.token, args.timeout)
    except Exception as exc:  # noqa: BLE001 — the one unguarded hop; a typo'd id must not traceback
        # A wrong --set-id 404s and a down server raises: both are "we could not find out", which
        # is what INCONCLUSIVE means. Escaping as a traceback would contradict the exit codes this
        # script documents and the deliverable tells the operator to act on.
        print(f'{args.set_id}: INCONCLUSIVE — could not read the file manifest ({type(exc).__name__}: {exc}). Nothing written.')
        return EXIT_INCONCLUSIVE
    if not listed:
        shape = 'absent' if listed is None else 'empty'
        print(f'{args.set_id}: INCONCLUSIVE — the file manifest is {shape} (job still running, or the workspace is gone). Nothing written.')
        return EXIT_INCONCLUSIVE

    # Path shape first: until every path is known safe, a slot name read off one is not trustworthy.
    if bad := [f'{entry.get("path")!r}: {why}' for entry in listed if (why := unsafe(str(entry.get('path') or ''), args.out))]:
        print(f'{args.set_id}: REFUSED — the manifest names {len(bad)} path(s) this may not write. Nothing fetched.')
        print('\n'.join(f'  {line}' for line in bad))
        return EXIT_FAILED

    if bad_type := [entry for entry in listed if not isinstance(entry.get('path'), str)]:
        print(f'{args.set_id}: REFUSED — {len(bad_type)} manifest entr(ies) carry a non-string `path`. Nothing fetched.')
        return EXIT_FAILED
    slots = {entry['path'].split('/', 1)[0] for entry in listed if '/' in entry['path']}
    if not slots:
        # A non-empty manifest naming ONLY set-level files is a set mid-generation, not a set
        # with nothing in it: every per-slot gate below iterates zero times, so without this the
        # script wrote the set-level files and exited 0 having checked no slot at all.
        print(f'{args.set_id}: INCONCLUSIVE — the manifest names {len(listed)} file(s) but no slot folder (a set still being generated). Nothing written.')
        return EXIT_INCONCLUSIVE
    if args.slots:
        if unknown := sorted(set(args.slots) - slots):
            print(f'{args.set_id}: REFUSED — slot(s) not in this set: {unknown}. Known: {sorted(slots)}')
            return EXIT_FAILED
        slots = set(args.slots)
    # Set-level files (no slot prefix) describe the set the slots came from, so they always ride along.
    wanted = [entry for entry in listed if entry['path'].split('/', 1)[0] in slots or '/' not in entry['path']]

    if refused := refusals(wanted, slots):
        print(f'{args.set_id}: REFUSED — the manifest itself is not fetchable as listed. Nothing fetched.')
        print('\n'.join(f'  {line}' for line in refused))
        return EXIT_FAILED

    omitted = {
        entry['path']: f'{withheld.get(entry["path"]) or entry["size_bytes"]}B, over the wire cap'
        for entry in wanted
        if entry.get('omitted') is True or entry['path'] in withheld
    }
    landed = fetch(args.server, args.set_id, [entry for entry in wanted if entry['path'] not in omitted], args.out, args.token, args.timeout)
    failed = {path: reason for path, reason in landed.items() if reason is not None}

    status = EXIT_FAILED if failed else EXIT_OK
    for slot in sorted(slots):
        # What actually reached disk, not what the manifest listed — a required file that came
        # back as a placeholder is a slot missing that file, not a slot that has it.
        names = sorted(path[len(slot) + 1:] for path, reason in landed.items() if path.startswith(f'{slot}/') and reason is None)
        missing = [name for name in REQUIRED if name not in names]
        print(f'{slot}: landed={names}' + (f' MISSING_REQUIRED={missing}' if missing else ''))
        status = EXIT_FAILED if missing else status
    print(f'set-level: {sorted(path for path, reason in landed.items() if "/" not in path and reason is None)}')
    for path, reason in sorted(omitted.items()):
        print(f'OMITTED {path}: {reason} — it stays on the backend')
    if omitted:
        print(f'marked omitted: {mark_incomplete(args.out, omitted, marker_name=OMITTED_MARKER, header=_OMITTED_HEADER)}')
    for path, reason in sorted(failed.items()):
        print(f'FAILED {path}: {reason}')
    if failed:
        print(f'marked incomplete: {mark_incomplete(args.out, failed)}')
    return status


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
