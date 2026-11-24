#!/usr/bin/env python3
"""The one writer of the id files, driven by a backend answer.

    write_id_file.py minted  ANSWER|-  [--replaces ID]   an edit tool's or `end_edit_session`'s answer
    write_id_file.py created --kind K  ANSWER|-          a creation job's terminal answer, or an upload's
    write_id_file.py from-manifest --env-dir DIR         the handles a saved env's manifest holds
    write_id_file.py to-manifest   --env-dir DIR --name N   the handles on disk, into a saved env's manifest and markers
    write_id_file.py set --kind scenarios|evals|env ID   a suite id the user chose, into every copy CI compares

Every command takes `--workdir DIR` (default: the resolver cascade, as `workspace_id.py` does).

THE RULE. An edit of a scenario set, eval set, env or corpus leaves the id it was made on untouched and ships under a new
one, answered as `minted_id: {kind, id}` (`null` when nothing shipped). Only that id is committed. `minted` writes the id
file of that kind from `minted_id` and from nothing else: not `target_id` (the draft copy an open edit session works on),
not `source_id` (the original), not a scenario set id an eval answer happens to carry. An answer with no `minted_id` field
is refused, since a missing field is a wrong answer and not a null one; a null `minted_id` writes nothing.

One edit moves one id file. The files of artifacts that merely link to the edited one are not touched, and neither are
the other markers: an edit of a scenario set moves `.scenario_set_id` only, a relink or an eval edit `.eval_set_id` only.

CREATION. A new artifact has no earlier id and no draft: the id of a done creation job (its envelope `id`, which equals
`detail.<kind>_id`) or of a finished upload (`set_id`) is its first and only id. `created` writes that one. A refused
eval set that kept its draft (`failed` with `detail.draft_ref`) is written too: the repair session publishes it under the
same id.

A CORPUS id sits in up to three markers (`.corpus_id`, `.corpus_ids`, `.knowledge_bases`), so a minted corpus names the id
it replaces with `--replaces`; the command refuses when no marker holds that id.

TOP-UP. A scenario top-up that adopted scenarios is an edit of its donor set: it lands under the job's own id and answers it as
`detail.minted_id` (`{kind: scenarios, id}`, with `detail.copied_from` naming the donor) once it has finished. `created --kind
scenarios --top-up` writes that id, and refuses an answer whose `minted_id` is missing, null, of another kind, or not the job's own id.

NAMED ENVS. When the workdir holds `plurai.env.yaml`, the manifest key of the kind moves with the marker. `minted` moves
the key alone; `created` also refreshes the manifest's input fingerprints, since a generation reads the current inputs; `--keep-fingerprints` leaves them, for a top-up that adopts a set generated from the same inputs.

THE ROOT COPIES. CI reads a scenario or eval set id from the env folder `ci.yaml` names (`env`, `default` when the key is
absent) and refuses when the root marker `plurai_sim/<marker>` holds another id. So a write of `.scenario_set_id` or
`.eval_set_id` in that env folder (or in any env folder while there is no `ci.yaml`) writes the root marker too. The
other direction holds as well: a write in `plurai_sim/` itself (no `.active-env`, as in a fresh clone) also writes the
marker and manifest key of the env folder CI reads, when that folder holds a manifest. The env id is not copied to the root
from an env folder (CI reads it from the manifest alone), but a write of it in `plurai_sim/` moves the CI env folder's
manifest key and marker as well as the root marker, so CI deploys the env that was minted. The corpus id is read from
elsewhere and stays where it is written. `set` is the fix for copies that already disagree: it writes the id the user chose
as an edit would, into the env folder's marker, its manifest key and, for a scenario or eval set, the root.

SAVING AN ENV. `to-manifest` records the handles in the env folder's manifest and then writes the folder's own markers
from it, as `from-manifest` does on a switch. A saved env then holds every copy CI compares. The ids are read back through
`read_id.py`, which takes the copies in CI's order.

Output is one line. Exit 0: written, unchanged, or nothing to write. Exit 2: a refused answer or a usage error; every check
(the answer, each id, each manifest and marker it would read, PyYAML) runs before the first write, so nothing was written.
A write that then fails (a file that cannot be replaced) names the files already written, and the same command run again
completes the rest: a file that already holds the id is skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills' / 'manage-envs' / 'scripts'))
import harness_fingerprint  # noqa: E402 - sibling script: the workdir resolver cascade lives there
import read_id  # noqa: E402 - sibling script: which env folder the root markers copy is decided there
from grounding_files import write_atomic  # noqa: E402 - sibling script: the atomic write

#: The manifest filename, the marker filenames and the manifest keys of the kinds the reader resolves (scenarios, evals,
#: env), and the id pattern, belong to `read_id`; the writer takes them from there so the two cannot disagree.
MANIFEST = read_id.MANIFEST
_ID = read_id.ID_PATTERN
SCENARIO_SET_ID, _SCENARIOS_KEY = read_id.KINDS['scenarios']
EVAL_SET_ID, _EVALS_KEY = read_id.KINDS['evals']
ENV_ID, _ENV_KEY = read_id.KINDS['env']
CORPUS_ID = '.corpus_id'
CORPUS_IDS = '.corpus_ids'
KNOWLEDGE_BASES = '.knowledge_bases'
SCENARIO_BUNDLE_ID = '.scenario_bundle_id'
DOC_EXAMPLES_SET_ID = '.doc_examples_set_id'

#: The id file of each kind.
MARKERS = {
    'scenarios': SCENARIO_SET_ID,
    'evals': EVAL_SET_ID,
    'env': ENV_ID,
    'corpus': CORPUS_ID,
    'scenario_bundle': SCENARIO_BUNDLE_ID,
    'doc_examples': DOC_EXAMPLES_SET_ID,
}
#: The kinds a mutating tool answers as `minted_id.kind`.
MINTED_KINDS = ('scenarios', 'evals', 'env', 'corpus')
#: The kinds a creation answers: a job envelope (`id`) or an upload result (`set_id`).
JOB_KINDS = ('scenarios', 'evals', 'env')
UPLOAD_KINDS = ('scenario_bundle', 'doc_examples')
#: The key of the kind in an env manifest.
MANIFEST_KEYS = {
    'scenarios': _SCENARIOS_KEY,
    'evals': _EVALS_KEY,
    'env': _ENV_KEY,
    'corpus': 'corpus_id',
    'doc_examples': 'doc_examples_set_id',
}
#: The kinds whose root marker is a copy of the env folder's (THE ROOT COPIES).
ROOT_COPY_KINDS = read_id.ROOT_COPY_KINDS
#: The `detail` key a job envelope repeats its id under, checked when present.
DETAIL_KEYS = {'scenarios': ('scenario_set_id',), 'evals': ('eval_set_id',), 'env': ('env_id', 'env_session_id')}


class Refused(Exception):
    """An answer or a call this writer will not act on. Nothing has been written."""


#: The files this run has written so far, named `<folder>/<file>`; a failure names them.
_WRITTEN: list[str] = []


def _label(path: Path) -> str:
    return f'{path.parent.name}/{path.name}'


def _write(path: Path, data: str | bytes) -> None:
    """Replace `path` whole, and remember that this run wrote it."""
    write_atomic(path, data)
    _WRITTEN.append(_label(path))


def _require_yaml() -> None:
    """Refuse before the first write when this interpreter cannot read or write a manifest."""
    try:
        import yaml  # noqa: F401, PLC0415 - presence check: the manifest module imports it on its own
    except ImportError as exc:
        raise Refused(
            'PyYAML is not importable under this interpreter, and an env manifest needs it: run the writer with "$PLURAI_PY" '
            '(`source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"`), not a bare python3; nothing was written'
        ) from exc


def _manifest_of(directory: Path) -> dict:
    """The manifest of `directory` as a mapping, read before any write; a file that is not one is refused."""
    _require_yaml()
    path = directory / MANIFEST
    try:
        manifest = _manifest_module().read_manifest(directory)
    except Exception as exc:  # noqa: BLE001 - PyYAML's error types, an unreadable file: one refusal
        raise Refused(f'{path} cannot be read ({exc}); nothing was written') from exc
    if not isinstance(manifest, dict):
        raise Refused(f'{path} is not a mapping; nothing was written')
    return manifest


def _load(source: str) -> dict:
    try:
        text = sys.stdin.read() if source == '-' else Path(source).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f'cannot read the answer: {exc}') from exc
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise Refused(f'the answer is not JSON: {exc}') from exc
    if not isinstance(answer, dict):
        raise Refused('the answer is not a JSON object')
    return answer


def _plain_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise Refused(f'{what} is not a plain id: {value!r}')
    return value


def _put(workdir: Path, name: str, value: str) -> bool:
    """Write the marker as one line with no trailing newline. False when it already holds `value`."""
    path = workdir / name
    data = value.encode('utf-8')
    if path.is_file() and path.read_bytes() == data:
        return False
    _write(path, data)
    return True


def _root_copy(workdir: Path) -> Path | None:
    """The simulation directory whose root markers copy `workdir`'s: when `workdir` is the saved env CI reads."""
    if not read_id.is_env_dir(workdir) or not _has_manifest(workdir):
        return None
    return workdir.parent.parent if read_id.root_copies(workdir) else None


def _put_root_copy(workdir: Path, kind: str, value: str) -> str | None:
    """Write the root copy of `kind`'s marker (THE ROOT COPIES); the name it was written under, or None."""
    sim = _root_copy(workdir) if kind in ROOT_COPY_KINDS else None
    if sim is None or not _put(sim, MARKERS[kind], value):
        return None
    return f'the root {MARKERS[kind]}'


def _manifest_module():  # noqa: ANN202 - the module, imported on demand: it needs PyYAML
    import env_manifest  # noqa: PLC0415

    return env_manifest


def _has_manifest(workdir: Path) -> bool:
    return (workdir / MANIFEST).is_file()


# --- corpus ------------------------------------------------------------------------------------------------


def _swap(ids: list, old: str, new: str) -> list:
    return [new if i == old else i for i in ids]


def _plan_corpus(workdir: Path, old: str, new: str) -> list[tuple[Path, str]]:
    """The corpus markers that hold `old`, each with the text it takes; nothing is written. `.knowledge_bases` is parsed here."""
    plan: list[tuple[Path, str]] = []
    single = workdir / CORPUS_ID
    if single.is_file() and single.read_text(encoding='utf-8').strip() == old:
        plan.append((single, new))
    listed = workdir / CORPUS_IDS
    if listed.is_file():
        lines = listed.read_text(encoding='utf-8').split('\n')
        if old in [line.strip() for line in lines]:
            plan.append((listed, '\n'.join(new if line.strip() == old else line for line in lines)))
    bases = workdir / KNOWLEDGE_BASES
    if bases.is_file():
        try:
            entries = json.loads(bases.read_text(encoding='utf-8'))
        except ValueError as exc:
            raise Refused(f'{KNOWLEDGE_BASES} is not JSON ({exc}); nothing was written') from exc
        hit = False
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get('corpus_ids'), list) and old in entry['corpus_ids']:
                entry['corpus_ids'] = _swap(entry['corpus_ids'], old, new)
                hit = True
        if hit:
            plan.append((bases, json.dumps(entries, indent=2, ensure_ascii=False)))
    return plan


def _manifest_corpus_update(workdir: Path, old: str, new: str) -> dict:
    manifest = _manifest_of(workdir)
    updates: dict = {}
    if manifest.get('corpus_id') == old:
        updates['corpus_id'] = new
    if isinstance(manifest.get('corpus_ids'), list) and old in manifest['corpus_ids']:
        updates['corpus_ids'] = _swap(manifest['corpus_ids'], old, new)
    bases = manifest.get('knowledge_bases')
    if isinstance(bases, list) and any(isinstance(b, dict) and old in (b.get('corpus_ids') or []) for b in bases):
        updates['knowledge_bases'] = [
            {**b, 'corpus_ids': _swap(b['corpus_ids'], old, new)} if isinstance(b, dict) and old in (b.get('corpus_ids') or []) else b for b in bases
        ]
    return updates


# --- commands ----------------------------------------------------------------------------------------------


def minted(workdir: Path, source: str, replaces: str | None) -> str:
    answer = _load(source)
    if 'minted_id' not in answer:
        raise Refused(
            'the answer has no `minted_id` field. Only `minted_id` is a committed id: `target_id` of an open edit session is a '
            'draft copy and `source_id` the original. Pass the answer of the edit tool or of `end_edit_session`.'
        )
    found = answer['minted_id']
    if found is None:
        if replaces is not None:
            _plain_id(replaces, '--replaces')
        return 'id-file: no id minted; nothing written.'
    if not isinstance(found, dict):
        raise Refused(f'`minted_id` is not an object: {found!r}')
    kind = found.get('kind')
    if kind not in MINTED_KINDS:
        raise Refused(f'`minted_id.kind` is {kind!r}, not one of {", ".join(MINTED_KINDS)}')
    new = _plain_id(found.get('id'), '`minted_id.id`')
    if kind != 'corpus':
        if replaces is not None:
            raise Refused('--replaces belongs to a minted corpus: the other kinds have one id file')
        return _apply(workdir, kind, new, refresh=False)
    if replaces is None:
        raise Refused('a minted corpus needs --replaces <the corpus id the edit was made on>: three markers can hold a corpus id')
    old = _plain_id(replaces, '--replaces')
    plan = _plan_corpus(workdir, old, new)
    updates = _manifest_corpus_update(workdir, old, new) if _has_manifest(workdir) else {}
    if not plan and not updates:
        raise Refused(f'no corpus marker in {workdir} holds {old}, so no id file moved')
    changed: list[str] = []
    if updates:
        _manifest_module().set_ids(workdir, updates)
        _WRITTEN.append(_label(workdir / MANIFEST))
        changed.append(MANIFEST)
    for path, text in plan:
        _write(path, text)
        changed.append(path.name)
    return f'id-file: wrote {", ".join(changed)}.'


def _top_up_id(detail: dict, envelope_id: str) -> str:
    """The id a finished top-up answers as `detail.minted_id`: a `scenarios` id that is the job's own set id."""
    if 'minted_id' not in detail:
        raise Refused('the answer has no `detail.minted_id`: it is not a finished top-up that adopted scenarios; nothing was written')
    found = detail['minted_id']
    if not isinstance(found, dict):
        raise Refused(f'`detail.minted_id` is {found!r}, not an object: the top-up minted no id; nothing was written')
    if found.get('kind') != 'scenarios':
        raise Refused(f'`detail.minted_id.kind` is {found.get("kind")!r}, not scenarios; nothing was written')
    new = _plain_id(found.get('id'), '`detail.minted_id.id`')
    if new != envelope_id:
        raise Refused(f'`detail.minted_id.id` is {new!r} but the envelope id is {envelope_id!r}; nothing was written')
    return new


def created(workdir: Path, kind: str, source: str, backend_url: str | None = None, *, refresh: bool = True, top_up: bool = False) -> str:
    answer = _load(source)
    status = answer.get('status')
    if kind in UPLOAD_KINDS:
        new = _plain_id(answer.get('set_id'), '`set_id`')
        if status != 'done':
            raise Refused(f'the upload is `{status}`, not `done`; nothing was written')
        return _apply(workdir, kind, new, refresh=refresh, backend_url=backend_url)
    new = _plain_id(answer.get('id'), '`id`')
    raw_detail = answer.get('detail')
    detail: dict = raw_detail if isinstance(raw_detail, dict) else {}
    kept_draft = kind == 'evals' and status == 'failed' and bool(detail.get('draft_ref'))
    if status != 'done' and not kept_draft:
        raise Refused(f'the job is `{status}`, not `done`; nothing was written')
    for key in DETAIL_KEYS[kind]:
        if key in detail and detail[key] != new:
            raise Refused(f'`detail.{key}` is {detail[key]!r} but the envelope id is {new!r}; nothing was written')
    if top_up:
        if kind != 'scenarios':
            raise Refused('--top-up belongs to a scenario set: only a scenario top-up answers `detail.minted_id`')
        new = _top_up_id(detail, new)
    return _apply(workdir, kind, new, refresh=refresh, backend_url=backend_url)


def _env_copy(workdir: Path, kind: str) -> Path | None:
    """The env folder whose marker and manifest key copy `workdir`'s: when `workdir` is `plurai_sim/` (THE ROOT COPIES).

    The env id belongs here too: CI reads it from this folder's manifest, so an env minted in a fresh clone moves that key.
    """
    if kind not in (*ROOT_COPY_KINDS, 'env') or read_id.is_env_dir(workdir):
        return None
    env_dir = read_id.ci_env_dir(workdir)
    return env_dir if _has_manifest(env_dir) else None


def _apply(workdir: Path, kind: str, new: str, *, refresh: bool, backend_url: str | None = None) -> str:
    """Write the manifest key of `kind` in each manifest that holds a copy (THE ROOT COPIES), then the markers.

    Both manifests are read and checked before the first write, so a manifest that cannot be updated stops the write with
    nothing moved. The manifests then go before the markers. `refresh` re-fingerprints the inputs (a generation); without it
    only the key moves.
    """
    wrote: list[str] = []
    key = MANIFEST_KEYS.get(kind)
    copy_dir = _env_copy(workdir, kind)
    copy_manifest = _manifest_of(copy_dir) if copy_dir is not None and key is not None else None
    prior = _manifest_of(workdir) if key is not None and _has_manifest(workdir) else None
    if copy_dir is not None and copy_manifest is not None and key is not None and copy_manifest.get(key) != new:
        _manifest_module().set_ids(copy_dir, {key: new})
        _WRITTEN.append(_label(copy_dir / MANIFEST))
        wrote.append(f'`{key}` in envs/{copy_dir.name}/{MANIFEST}')
    if prior is not None and key is not None and (prior.get(key) != new or refresh):
        module = _manifest_module()
        if refresh:
            kwargs: dict[str, Any] = {key: new}
            module.write_manifest(workdir, name=prior.get('name') or workdir.name, backend_url=backend_url, **kwargs)
        else:
            module.set_ids(workdir, {key: new})
        _WRITTEN.append(_label(workdir / MANIFEST))
        wrote.append(MANIFEST)
    if _put(workdir, MARKERS[kind], new):
        wrote.insert(0, MARKERS[kind])
    root = _put_root_copy(workdir, kind, new)
    if root is not None:
        wrote.append(root)
    if copy_dir is not None and _put(copy_dir, MARKERS[kind], new):
        wrote.append(f'envs/{copy_dir.name}/{MARKERS[kind]}')
    return f'id-file: wrote {", ".join(wrote)}.' if wrote else f'id-file: {MARKERS[kind]} already holds this id.'


def from_manifest(workdir: Path, env_dir: Path) -> str:
    _require_yaml()
    try:
        manifest = _manifest_module().read_manifest(env_dir)
    except Exception as exc:  # noqa: BLE001 - PyYAML's error types, an unreadable file: one refusal
        raise Refused(f'{env_dir / MANIFEST} cannot be read ({exc}); nothing was written') from exc
    if manifest is None:
        raise Refused(f'no {MANIFEST} in {env_dir}')
    if not isinstance(manifest, dict):
        raise Refused(f'{env_dir / MANIFEST} is not a mapping; nothing was written')
    handles: list[tuple[str, str]] = []
    for kind in ('env', 'scenarios', 'evals', 'corpus'):
        value = manifest.get(MANIFEST_KEYS[kind])
        if value:
            handles.append((kind, _manifest_id(value, MANIFEST_KEYS[kind])))
    listed = manifest.get('corpus_ids')
    corpus_text = '\n'.join(_manifest_id(i, 'corpus_ids entry') for i in listed) if isinstance(listed, list) and listed else None
    wrote: list[str] = []
    for kind, plain in handles:
        if _put(workdir, MARKERS[kind], plain):
            wrote.append(MARKERS[kind])
        root = _put_root_copy(workdir, kind, plain)
        if root is not None:
            wrote.append(root)
    if corpus_text is not None and (not (workdir / CORPUS_IDS).is_file() or (workdir / CORPUS_IDS).read_text(encoding='utf-8') != corpus_text):
        _write(workdir / CORPUS_IDS, corpus_text)
        wrote.append(CORPUS_IDS)
    return f'id-file: wrote {", ".join(wrote)}.' if wrote else 'id-file: the handles already match the manifest.'


def _manifest_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise Refused(f'the manifest `{what}` is not a plain id: {value!r}; nothing was written')
    return value


def _read_marker(directory: Path, name: str) -> str | None:
    try:
        value = (directory / name).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value or None


def to_manifest(env_dir: Path, name: str, from_dir: Path, backend_url: str | None) -> str:
    _require_yaml()
    ids = {kind: _read_marker(from_dir, MARKERS[kind]) for kind in ('env', 'scenarios', 'evals', 'corpus', 'doc_examples')}
    for kind, value in ids.items():
        if value is not None:
            _plain_id(value, MARKERS[kind])
    listed = _read_marker(from_dir, CORPUS_IDS)
    corpus_ids = [_plain_id(line.strip(), CORPUS_IDS) for line in listed.splitlines() if line.strip()] if listed else None
    bases_text = _read_marker(from_dir, KNOWLEDGE_BASES)
    try:
        bases = json.loads(bases_text) if bases_text else None
    except ValueError as exc:
        raise Refused(f'{KNOWLEDGE_BASES} is not JSON ({exc}); the manifest was not written') from exc
    if _has_manifest(env_dir):
        _manifest_of(env_dir)
    _manifest_module().write_manifest(
        env_dir,
        name=name,
        backend_url=backend_url,
        env_id=ids['env'],
        scenario_set_id=ids['scenarios'],
        eval_set_id=ids['evals'],
        corpus_id=ids['corpus'],
        corpus_ids=corpus_ids,
        knowledge_bases=bases,
        doc_examples_set_id=ids['doc_examples'],
    )
    _WRITTEN.append(_label(env_dir / MANIFEST))
    markers = from_manifest(env_dir, env_dir)
    return f'id-file: wrote {env_dir / MANIFEST}; {markers.removeprefix("id-file: ")}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

    def add_workdir(p: argparse.ArgumentParser) -> None:
        p.add_argument('--workdir', help='the plurai_sim directory (default: $WORKDIR, else the resolver cascade)')

    m = sub.add_parser('minted', help="write the id file of an edit's `minted_id`")
    m.add_argument('answer', help='the answer as a JSON file, or - for stdin')
    m.add_argument('--replaces', help='a minted corpus: the corpus id the edit was made on')
    add_workdir(m)

    c = sub.add_parser('created', help="write the id of a done creation job or finished upload")
    c.add_argument('answer', help='the job envelope or upload result as a JSON file, or - for stdin')
    c.add_argument('--kind', required=True, choices=[*JOB_KINDS, *UPLOAD_KINDS])
    c.add_argument('--keep-fingerprints', action='store_true', help="a top-up of a set generated from the same inputs: move the manifest key, leave the inputs' fingerprints")
    c.add_argument('--top-up', action='store_true', help="a scenario top-up: write `detail.minted_id.id` and refuse an answer that has none")
    c.add_argument('--backend-url', help="recorded in the env manifest's backend_url when the workdir holds one")
    add_workdir(c)

    f = sub.add_parser('from-manifest', help="write the handles of a saved env's manifest")
    f.add_argument('--env-dir', required=True, type=Path)
    add_workdir(f)

    t = sub.add_parser('to-manifest', help="record the handles on disk in a saved env's manifest")
    t.add_argument('--env-dir', required=True, type=Path)
    t.add_argument('--name', required=True)
    t.add_argument('--backend-url')
    t.add_argument('--from-dir', type=Path, help='where the handles are (default: --workdir)')
    add_workdir(t)

    s = sub.add_parser('set', help='write a suite id the user chose into every copy CI compares')
    s.add_argument('--kind', required=True, choices=[*ROOT_COPY_KINDS, 'env'])
    s.add_argument('id', help='the scenario set, eval set or env id the suite should use')
    add_workdir(s)

    args = parser.parse_args(argv)
    own = args.cmd == 'to-manifest' and args.from_dir is not None
    workdir = args.from_dir if own else harness_fingerprint.resolve_workdir(args.workdir)
    try:
        if args.cmd == 'minted':
            line = minted(workdir, args.answer, args.replaces)
        elif args.cmd == 'created':
            line = created(workdir, args.kind, args.answer, args.backend_url, refresh=not args.keep_fingerprints, top_up=args.top_up)
        elif args.cmd == 'set':
            line = _apply(workdir, args.kind, _plain_id(args.id, 'the id'), refresh=False)
        elif args.cmd == 'from-manifest':
            line = from_manifest(workdir, args.env_dir)
        else:
            line = to_manifest(args.env_dir, args.name, workdir, args.backend_url)
    except (Refused, OSError, RuntimeError) as exc:
        tail = f'. Already written: {", ".join(_WRITTEN)}; the other copies still hold the old id. Fix the cause and run the same command again: it skips what is written' if _WRITTEN else ''
        print(f'ERROR(write_id_file): {exc}{tail}', file=sys.stderr)
        return 2
    print(line)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
