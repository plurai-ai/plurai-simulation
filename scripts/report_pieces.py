#!/usr/bin/env python3
"""Pull each piece of the report page as soon as it exists, so the page grows during the suite.

    report_pieces.py bundle <workdir> --set-id S [slot ...]   # exit codes of fetch_slot_bundle.py
    report_pieces.py draft  <workdir>                          # 0 written or nothing to draft · 2 refused

`sync(workdir, envelope)` is called by `publish_report.py fill --job` with the `GET /jobs/<id>` answer
it already holds, before the page is filled. It dispatches on the envelope's `kind` through one table,
`PIECES_BY_KIND`, and names the pieces it changed. It never raises and never writes stdout (the
fill's stdout is its JSON line):

* scenarios, running — the flow graph off the envelope (`detail.dimension_graph`, one atomic DB row;
  the file route can 404 or serve a half-written YAML mid-run) → `artifacts/scenarios/dimension_graph.yaml`.
* scenarios, done — the set's bundle (`fetch_slot_bundle.py` into a staging dir, then the swap), its
  `GET /artifacts/<set>` meta → `artifacts/.pieces/meta_<set>.json`, then the draft `suite.json`.
* evals, done — the policies `/generate-evals` pulled: their `(eval_set_id, sha256)` is recorded and
  the draft is rebuilt.

THE CACHE. `artifacts/.pieces/pieces.json` (`plurai-pieces/1`) keys what is on the page: the set and
its `gt_revision`, where the graph came from (`envelope` | `bundle`), the policies' eval set and sha. A
pull runs only when its key differs. A failed pull leaves the cache and the page untouched (retried
next wake); a fetch failure (exit 1) keeps the staging dir and records `last_error`.

ONE CURRENT COPY. A new set (its early graph or its bundle) moves the old set's `artifacts/scenarios/`,
`suite.json`, `scenarios.md`, `evals.md`, suite charts and `final_set_meta.json` to
`artifacts/history/<old set id>/` (never overwritten: `-2`, `-3`), then moves the staged set into
place, then writes `pieces.json` LAST. A same-set refresh after a GT edit swaps only the edited slot
folders (the old ones go to `history/<set>@gt<old revision>/`). The page never reads `history/` or
`.pieces/`: no report schema key is rooted there, and `*` never crosses `/`.

Runs are not moved: they are what the user ran, and stale labels (artifact_deps.py) say what they are.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

SUITE_SCRIPTS = HERE.parent / 'skills' / 'simulation-generate-suite' / 'scripts'
FETCH = SUITE_SCRIPTS / 'fetch_slot_bundle.py'
BUILD = SUITE_SCRIPTS / 'build_suite_json.py'

SCHEMA = 'plurai-pieces/1'
PIECES = '.pieces'
HISTORY = 'history'
SET_META = 'final_set_meta.json'
#: What one scenario set puts on the page (relative to artifacts/); a new set moves them together.
SET_FILES = ('scenarios', 'suite.json', 'scenarios.md', 'evals.md',
             'charts/scenarios_coverage.svg', 'charts/scenarios_complexity.svg', 'charts/environment.svg')
GRAPH = 'dimension_graph.yaml'
POLICIES = 'policies_config.yaml'

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INCONCLUSIVE = 0, 1, 2, 3
META_TIMEOUT = 10.0
FETCH_TIMEOUT = 900.0
BUILD_TIMEOUT = 300.0
_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def _log(message: str) -> None:
    print(f'report_pieces: {message}', file=sys.stderr)


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _art(workdir: Path) -> Path:
    return Path(workdir) / 'artifacts'


def _safe_id(value: Any) -> Optional[str]:
    """A set id as one path segment (it names staging and history dirs), else None."""
    text = str(value or '').strip()
    return text if _SAFE_ID.fullmatch(text) else None


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rev(value: Any) -> int:
    """`gt_revision` is the count of accepted edits; absent reads as none yet."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# ── the cache ─────────────────────────────────────────────────────────────────
def read_pieces(workdir: Path) -> dict:
    data = _read_json(_art(workdir) / PIECES / 'pieces.json')
    return data if isinstance(data, dict) and data.get('schema') == SCHEMA else {'schema': SCHEMA}


def _write_pieces(workdir: Path, pieces: dict) -> None:
    _write_atomic(_art(workdir) / PIECES / 'pieces.json', json.dumps({**pieces, 'schema': SCHEMA}, indent=2, sort_keys=True) + '\n')


def _set_of_meta(path: Path) -> Optional[str]:
    meta = _read_json(path)
    if not isinstance(meta, dict):
        return None
    detail = meta.get('detail') if isinstance(meta.get('detail'), dict) else meta
    return str(detail.get('scenario_set_id') or meta.get('id') or '') or None


def page_set(workdir: Path) -> Optional[str]:
    """The set whose files are on the page now: the cache, else what a pre-cache page names."""
    cached = read_pieces(workdir).get('scenarios')
    if isinstance(cached, dict) and cached.get('set_id'):
        return str(cached['set_id'])
    art = _art(workdir)
    suite = _read_json(art / 'suite.json')
    if isinstance(suite, dict) and suite.get('scenario_set_id'):
        return str(suite['scenario_set_id'])
    return _set_of_meta(art / SET_META)


# ── the backend ───────────────────────────────────────────────────────────────
def server_url() -> str:
    """The REST base of the resolved backend (`hooks/_login_store.py`); '' when none."""
    return url_guard().server_url()


def _split_refusal(server: str, workdir: Path) -> Optional[str]:
    """The wrong-backend refusal (`backend_identity_marker.split`); a missing guard module is no refusal."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        import backend_identity_marker  # noqa: PLC0415 - a sibling script, loaded by path
    except Exception:  # noqa: BLE001 - the guard is advisory here; the fetch runs it again
        return None
    return backend_identity_marker.split(server, caller='report_pieces', workdir=workdir)


def _get_json(url: str) -> Any:
    """One GET; the bearer comes from the resolver and is never logged.

    The bearer goes only to a backend the session was bound to (the session pin, hooks/session_pin.py), and
    a redirect is an answer, never a hop."""
    token = url_guard().bearer(url)
    if token and not url_guard().is_backend_url(url):
        raise urllib.error.URLError(url_guard().refused_message(url))
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    with _no_redirect.OPENER.open(request, timeout=META_TIMEOUT) as response:  # noqa: S310 - checked against the pinned backend above
        return json.loads(response.read())


def url_guard() -> Any:
    """``hooks/url_guard.py``, loaded by path: the one answer to "may the bearer go to this URL"."""
    hooks = str(HERE.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import url_guard  # noqa: PLC0415 - found through the line above

    return url_guard


def _run_fetch(server: str, workdir: Path, set_id: str, out: Path, slots: tuple[str, ...]) -> tuple[int, str]:
    """`fetch_slot_bundle.py` into `out`; the URL and bearer travel in the environment, never argv."""
    env = {**os.environ, 'SERVER_URL': server, 'WORKDIR': str(workdir)}
    argv = [sys.executable, str(FETCH), '--set-id', set_id, '--out', str(out), *slots]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, **utf8_child_text(), env=env, timeout=FETCH_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return EXIT_FAILED, f'fetch_slot_bundle timed out after {FETCH_TIMEOUT:g}s'
    return proc.returncode, (proc.stdout or '') + (proc.stderr or '')


# ── moving files ──────────────────────────────────────────────────────────────
def _history_dir(art: Path, name: str) -> Path:
    """`history/<name>`, or the first free `-2`, `-3`, …: an existing history entry is never overwritten."""
    base = art / HISTORY / name
    candidate, k = base, 2
    while candidate.exists():
        candidate, k = base.with_name(f'{name}-{k}'), k + 1
    return candidate


def _retire(art: Path, name: str, rels: tuple[str, ...], incoming: Optional[str] = None) -> Optional[Path]:
    """Move what is on the page to `history/<name>[-k]/`, each at its own relative path.

    With `incoming`, `final_set_meta.json` moves too when it names another set than `incoming`."""
    moving = [rel for rel in rels if (art / rel).exists()]
    if incoming is not None and (art / SET_META).is_file() and _set_of_meta(art / SET_META) not in (None, incoming):
        moving.append(SET_META)
    if not moving:
        return None
    hist = _history_dir(art, name)
    hist.mkdir(parents=True)
    for rel in moving:
        dest = hist / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(art / rel, dest)
    return hist


def _discard(art: Path, rel: str) -> None:
    """Drop a page file nothing needs to keep (an envelope-only graph superseded by its own set's bundle)."""
    source = art / rel
    if not source.exists():
        return
    trash = art / PIECES / f'trash-{uuid.uuid4().hex}'
    trash.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, trash)
    shutil.rmtree(trash, ignore_errors=True)


def _staging(art: Path, name: str) -> Path:
    """A fresh staging dir under `.pieces/staging/` (a leftover from a failed pull is cleared first)."""
    staged = art / PIECES / 'staging' / name
    if staged.exists():
        shutil.rmtree(staged)
    staged.parent.mkdir(parents=True, exist_ok=True)
    return staged


def _dump_yaml(data: dict) -> str:
    try:
        import yaml  # noqa: PLC0415 - the plugin's interpreter carries it
    except ImportError:
        return json.dumps(data, indent=2, ensure_ascii=False) + '\n'  # JSON is YAML
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# ── the pieces ────────────────────────────────────────────────────────────────
def _envelope_set(envelope: dict) -> Optional[str]:
    detail = envelope.get('detail') if isinstance(envelope.get('detail'), dict) else {}
    return _safe_id(envelope.get('id') or detail.get('scenario_set_id'))


def _graph(workdir: Path, envelope: dict) -> list[str]:
    """The flow graph off a running (or done) scenario job's envelope, the moment it has one."""
    if envelope.get('status') == 'failed':
        return []
    detail = envelope.get('detail') if isinstance(envelope.get('detail'), dict) else {}
    graph = detail.get('dimension_graph')
    set_id = _envelope_set(envelope)
    if not set_id or not isinstance(graph, dict) or not isinstance(graph.get('flows'), list) or not graph['flows']:
        return []
    pieces = read_pieces(workdir)
    cached = pieces.get('scenarios') if isinstance(pieces.get('scenarios'), dict) else {}
    if cached.get('set_id') == set_id:
        return []  # this set's graph — the envelope's or the bundle's fuller one — is already on the page
    art = _art(workdir)
    on_page = page_set(workdir)
    text = _dump_yaml({
        'flows': [f for f in graph['flows'] if isinstance(f, dict)],
        'edges': [e for e in graph.get('edges') or [] if isinstance(e, dict)],
    })
    if on_page == set_id:
        # A page written before the cache existed, of this same set: its own graph file wins.
        if (art / 'scenarios' / GRAPH).is_file():
            return []
        _write_atomic(art / 'scenarios' / GRAPH, text)
    else:
        staged = _staging(art, f'{set_id}.graph')
        staged.mkdir()
        (staged / GRAPH).write_text(text, encoding='utf-8')
        _retire(art, on_page or 'unknown-set', SET_FILES, incoming=set_id)
        os.replace(staged, art / 'scenarios')
    _write_pieces(workdir, {**pieces, 'scenarios': {'set_id': set_id, 'gt_revision': None, 'graph_source': 'envelope', 'pulled_at': _now()}})
    return ['graph']


def _bundle(workdir: Path, envelope: dict) -> list[str]:
    """The set's bundle once its job is done, unless the page already holds it at this revision."""
    if envelope.get('status') != 'done':
        return []
    set_id = _envelope_set(envelope)
    if not set_id:
        return []
    detail = envelope.get('detail') if isinstance(envelope.get('detail'), dict) else {}
    cached = read_pieces(workdir).get('scenarios') or {}
    if (cached.get('set_id') == set_id and cached.get('graph_source') == 'bundle' and cached.get('gt_revision') == _rev(detail.get('gt_revision'))
            and (_art(workdir) / 'scenarios').is_dir()):
        return []
    _, changed = _pull(workdir, set_id, (), sys.stderr)
    return changed


def _policies_key(workdir: Path) -> Optional[dict]:
    path = Path(workdir) / POLICIES
    if not path.is_file():
        return None
    marker = Path(workdir) / '.eval_set_id'
    eval_set = marker.read_text(encoding='utf-8').strip() if marker.is_file() else ''
    return {'eval_set_id': eval_set or None, 'sha256': _sha256(path)}


def _policies(workdir: Path, envelope: dict) -> list[str]:
    """Policies the eval job's caller pulled: recorded by `(eval_set_id, sha256)`, then the draft rebuilt."""
    if envelope.get('status') != 'done':
        return []
    key = _policies_key(workdir)
    if key is None or not key['eval_set_id'] or key['eval_set_id'] != str(envelope.get('id') or ''):
        return []  # not this eval set's policies on disk yet; the next wake (or `draft`) picks them up
    if read_pieces(workdir).get('policies') == key:
        return []
    _, wrote = draft(workdir, sys.stderr)
    pieces = read_pieces(workdir)
    _write_pieces(workdir, {**pieces, 'policies': key})
    return ['policies'] + (['suite'] if wrote else [])


Piece = Callable[[Path, dict], list]

#: Job kind → the pieces its envelope can bring, in order. Kinds not here bring none.
PIECES_BY_KIND: dict[str, tuple[Piece, ...]] = {
    'scenarios': (_graph, _bundle),
    'evals': (_policies,),
}


def sync(workdir: Path, envelope: dict) -> list[str]:
    """Pull every piece `envelope` (a `GET /jobs/<id>` answer) makes available; names what changed.

    Never raises and never writes stdout: a broken piece is logged to stderr and the fill goes on."""
    changed: list[str] = []
    try:
        if not isinstance(envelope, dict):
            return changed
        for piece in PIECES_BY_KIND.get(str(envelope.get('kind') or ''), ()):
            try:
                changed += piece(Path(workdir), envelope)
            except Exception as exc:  # noqa: BLE001 - one piece never costs the fill
                _log(f'{piece.__name__.lstrip("_")} not pulled ({type(exc).__name__}: {exc})')
    except Exception as exc:  # noqa: BLE001 - sync is advisory to the fill
        _log(f'sync failed ({type(exc).__name__}: {exc})')
    return changed


# ── the bundle pull + swap ────────────────────────────────────────────────────
def _moved_slots(old: Any, new: int, edit_log: Any, names: Any) -> Optional[tuple[str, ...]]:
    """The slots edited between two GT revisions, or None when the edit log cannot attribute them."""
    if not isinstance(old, int) or isinstance(old, bool) or not isinstance(edit_log, list) or len(edit_log) != new or old > new:
        return None
    moved = []
    for entry in edit_log[old:new]:
        slot = _safe_id(entry.get('scenario')) if isinstance(entry, dict) else None
        if slot is None:
            return None
        if slot not in moved and (not isinstance(names, list) or slot in [str(n) for n in names]):
            moved.append(slot)
    return tuple(moved)


def _pull(workdir: Path, set_id: str, slots: tuple[str, ...], out: TextIO) -> tuple[int, list[str]]:
    workdir = Path(workdir)
    art = _art(workdir)
    server = server_url()
    if not server:
        _log(url_guard().configuration_problem('report_pieces') + ' Nothing was requested.')
        return EXIT_USAGE, []
    refusal = _split_refusal(server, workdir)
    if refusal:
        _log(refusal)
        return EXIT_USAGE, []
    try:
        meta = _get_json(f'{server}/artifacts/{set_id}')
    except Exception as exc:  # noqa: BLE001 - "could not find out" is INCONCLUSIVE
        _log(f'{set_id}: INCONCLUSIVE — the set meta could not be read ({type(exc).__name__}). Nothing pulled.')
        return EXIT_INCONCLUSIVE, []
    if not isinstance(meta, dict) or meta.get('status') not in (None, 'done'):
        _log(f'{set_id}: not done yet; nothing pulled')
        return EXIT_INCONCLUSIVE, []
    detail = meta.get('detail') if isinstance(meta.get('detail'), dict) else {}
    rev = _rev(detail.get('gt_revision', meta.get('gt_revision')))

    pieces = read_pieces(workdir)
    cached = pieces.get('scenarios') if isinstance(pieces.get('scenarios'), dict) else {}
    on_page = page_set(workdir)
    envelope_only = cached.get('set_id') == set_id and cached.get('graph_source') == 'envelope'
    landed = on_page == set_id and not envelope_only and (art / 'scenarios').is_dir()
    old_rev = cached.get('gt_revision') if cached.get('set_id') == set_id else None

    meta_text = json.dumps(meta, indent=2, ensure_ascii=False) + '\n'
    wanted: Optional[tuple[str, ...]] = None  # None = the whole set
    if landed:
        # The set is on the page: re-pull only the slots asked for plus those the GT edits since the
        # cached revision moved; an edit log that cannot name them costs the whole set.
        moved = () if old_rev == rev else _moved_slots(old_rev, rev, detail.get('edit_log'), detail.get('scenario_names'))
        wanted = None if moved is None else tuple(dict.fromkeys((*slots, *moved)))
        if wanted == ():
            if old_rev == rev:
                _log(f'{set_id}: already on the page at gt_revision {rev}; nothing pulled')
                return EXIT_OK, []
            _write_atomic(art / PIECES / f'meta_{set_id}.json', meta_text)
            _write_pieces(workdir, {**pieces, 'scenarios': {**cached, 'gt_revision': rev, 'pulled_at': _now()}})
            return EXIT_OK, []
    elif slots:
        _log(f'{set_id}: not on the page yet, so the whole set is pulled rather than {list(slots)}')

    staged = _staging(art, f'{set_id}@slots' if wanted else set_id)
    code, text = _run_fetch(server, workdir, set_id, staged, wanted or ())
    if text.strip():
        print(text.rstrip('\n'), file=out)
    if code != EXIT_OK:
        if code != EXIT_INCONCLUSIVE:
            _write_pieces(workdir, {**pieces, 'last_error': {'set_id': set_id, 'exit': code, 'at': _now(), 'staging': str(staged.relative_to(art))}})
        return code, []
    _write_atomic(art / PIECES / f'meta_{set_id}.json', meta_text)

    gt_name = f'{set_id}@gt{old_rev if isinstance(old_rev, int) else "unknown"}'
    if wanted:
        hist: Optional[Path] = None
        for slot in wanted:
            source, place = staged / slot, art / 'scenarios' / slot
            if not source.is_dir():
                continue
            if place.exists():
                hist = hist or _history_dir(art, gt_name)
                hist.mkdir(parents=True, exist_ok=True)
                os.replace(place, hist / slot)
            os.replace(source, place)
        shutil.rmtree(staged, ignore_errors=True)
    else:
        if envelope_only:
            _discard(art, 'scenarios')  # the envelope's graph, superseded by this set's own full one
        elif on_page == set_id:
            _retire(art, gt_name, ('scenarios',))  # same set re-pulled whole; its suite.json is rebuilt
        else:
            _retire(art, on_page or 'unknown-set', SET_FILES, incoming=set_id)
        if (art / 'scenarios').exists():
            _log(f'{set_id}: artifacts/scenarios could not be cleared; the staged set stays in {staged}')
            return EXIT_FAILED, []
        os.replace(staged, art / 'scenarios')

    pieces.pop('last_error', None)
    _write_pieces(workdir, {**pieces, 'scenarios': {'set_id': set_id, 'gt_revision': rev, 'graph_source': 'bundle', 'pulled_at': _now()}})
    _, wrote = draft(workdir, out)
    return EXIT_OK, ['bundle'] + (['suite'] if wrote else [])


def bundle(workdir: Path, set_id: str, slots: tuple = (), *, out: Optional[TextIO] = None) -> int:
    """Pull set `set_id` (or, for the set already on the page, just `slots`) through the swap.

    Returns fetch_slot_bundle's exit code: 0 landed (or already on the page), 1 a fetch failed (the
    page is untouched), 2 no backend URL / wrong backend / bad id, 3 not available yet."""
    safe = _safe_id(set_id)
    bad = [s for s in slots if _safe_id(s) != s]
    if not safe or bad:
        _log(f'refused: set id {set_id!r} / slot(s) {bad} are not single path segments')
        return EXIT_USAGE
    code, _ = _pull(Path(workdir), safe, tuple(slots), out or sys.stderr)
    return code


# ── the draft suite.json ──────────────────────────────────────────────────────
def draft(workdir: Path, out: Optional[TextIO] = None) -> tuple[int, bool]:
    """Build the draft `suite.json` of the set on the page; `(exit code, written)`.

    Nothing to draft (no landed set, or the final suite of this set is already there) is `(0, False)`:
    the final build owns `suite.json` once it exists, and a dependency rebuild refreshes it."""
    out = out or sys.stderr
    workdir = Path(workdir)
    art = _art(workdir)
    cached = read_pieces(workdir).get('scenarios') or {}
    set_id = _safe_id(cached.get('set_id')) if cached.get('graph_source') == 'bundle' else None
    if not set_id:
        print('no scenario set has landed on the page yet; no draft written', file=out)
        return EXIT_OK, False
    meta = art / PIECES / f'meta_{set_id}.json'
    if not meta.is_file():
        print(f'no saved meta for {set_id}; no draft written', file=out)
        return EXIT_OK, False
    current = _read_json(art / 'suite.json')
    if isinstance(current, dict) and not current.get('draft') and current.get('scenario_set_id') == set_id:
        print(f'the final suite.json of {set_id} is in place; no draft written', file=out)
        return EXIT_OK, False
    argv = [sys.executable, str(BUILD), '--draft', '--art', str(art), '--meta', str(meta), '--workdir', str(workdir)]
    policies = workdir / POLICIES
    if policies.is_file():
        argv += ['--policies', str(policies)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, **utf8_child_text(), timeout=BUILD_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        print(f'build_suite_json --draft timed out after {BUILD_TIMEOUT:g}s', file=out)
        return EXIT_FAILED, False
    text = (proc.stdout or '') + (proc.stderr or '')
    if text.strip():
        print(text.rstrip('\n'), file=out)
    if proc.returncode != EXIT_OK:
        return proc.returncode, False
    key = _policies_key(workdir)
    if key is not None:
        _write_pieces(workdir, {**read_pieces(workdir), 'policies': key})
    return EXIT_OK, True


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    pull = sub.add_parser('bundle', help='pull a scenario set (or slots of the set on the page) through the swap')
    pull.add_argument('workdir', type=Path)
    pull.add_argument('--set-id', required=True)
    pull.add_argument('slots', nargs='*', help='slot folders to refresh; default the whole set')
    build = sub.add_parser('draft', help="build the draft suite.json of the set on the page")
    build.add_argument('workdir', type=Path)
    # Slots come AFTER `--set-id` (the documented call), and argparse before 3.12.7 / 3.13.1 fills the
    # `nargs='*'` slots with nothing right after `workdir`, then rejects every slot as an unrecognized
    # argument. Ubuntu 24.04's python3.12 is 3.12.3. So the leftovers are the slots, and only a stray
    # OPTION is still refused; on a fixed interpreter there are no leftovers.
    args, extra = parser.parse_known_args(argv)
    if extra and (args.command != 'bundle' or any(a.startswith('-') for a in extra)):
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command == 'bundle':
        args.slots = [*args.slots, *extra]
        if not args.set_id.strip():
            print('report_pieces: --set-id is empty — the id was not read (is `$WORKDIR/.scenario_set_id` there?). Nothing requested.', file=sys.stderr)
            return EXIT_USAGE
        return bundle(args.workdir, args.set_id.strip(), tuple(args.slots), out=sys.stdout)
    code, _ = draft(args.workdir, sys.stdout)
    return code


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
