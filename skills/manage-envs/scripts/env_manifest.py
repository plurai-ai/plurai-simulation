#!/usr/bin/env python3
"""Environment manifest + alignment helper for the Plurai simulation plugin.

An *environment* is a committable bundle under ``plurai_sim/envs/<name>/``:
the aligned input artifacts (plurai.config.yaml, simulation_prd.md,
scenario_variables.yaml, scenario_state_schema.yaml, ...) plus a manifest
(``plurai.env.yaml``) that records the backend handles the artifacts were
generated against (env_id, scenario_set_id, eval_set_id, corpus_id) and a
sha256 fingerprint of each aligned input AT generation time.

This module is the single source of truth for that manifest:
  * ``write_manifest`` — snapshot current ids + recompute fingerprints.
  * ``read_manifest`` — load it.
  * ``check_drift`` — compare current input hashes vs the manifest and
    report which downstream artifacts (mock / scenarios / evals) are now
    stale. Used by /use-env + the run skills to WARN (not block).

An input may be fingerprinted over a PROJECTION of its content as well as whole
(see ``PROJECTIONS``): the PRD's mock edge hangs off its facts projection, so a
scenario-steering edit does not mark the mock stale.

Dependency-light on purpose (stdlib + pyyaml) so it runs in a customer repo
without the backend package. Invoke as a CLI (see ``main``) or import the
functions.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml ships with plurai
    yaml = None

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))

from grounding_files import pinned_version  # noqa: E402 - the pin `scripts/grounding_mirror.py pin` writes into the workdir

MANIFEST_FILENAME = 'plurai.env.yaml'
SCHEMA_VERSION = 1

# The aligned input artifacts whose content the manifest fingerprints. Each
# maps to the downstream backend artifacts that go STALE when it changes —
# this is the alignment contract surfaced by check_drift.
#
# simulation_prd.md's `mock` edge is NOT here: it hangs off the FACTS PROJECTION
# in PROJECTIONS below.
ALIGNED_INPUTS: dict[str, tuple[str, ...]] = {
    'plurai.config.yaml': ('mock', 'scenarios', 'evals'),
    'simulation_prd.md': ('scenarios', 'evals'),
    'scenario_variables.yaml': ('scenarios', 'evals'),
    'scenario_state_schema.yaml': ('scenarios',),
    'eval_intent.yaml': ('evals',),
}

#: Canonical ordering for a drift record's ``stale_downstream`` list.
_DOWNSTREAM_ORDER = ('mock', 'scenarios', 'evals')

#: Where the facts projection is computed. ONE implementation of the routed-span parser —
#: simulation-setup's verify_prd_appendix.py, which mirrors the backend's routing regex
#: byte-for-byte — because a second copy here would be a second contract to keep in step.
_VERIFY_PRD_SCRIPTS = Path(__file__).resolve().parent.parent.parent / 'simulation-setup' / 'scripts'


def _now_iso() -> str:
    # timezone.utc (not datetime.UTC) for portability to customer repos on
    # Python < 3.11.
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')  # noqa: UP017


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


#: Keys the scan writes about WHEN, not WHAT: re-running the scan moves them without changing a type, a field or a
#: citation, so they are not part of what the mock, the scenarios or the evals were built from.
_CHANGE_TIME_ROOT_KEYS = ('scanned_at', 'change_times')
_CHANGE_TIME_KEYS = ('commit', 'last_changed', 'function_last_changed')


def _without_change_times(node: object) -> object:
    if isinstance(node, dict):
        return {k: _without_change_times(v) for k, v in node.items() if k not in _CHANGE_TIME_KEYS}
    if isinstance(node, list):
        return [_without_change_times(v) for v in node]
    return node


def _config_hash(path: Path) -> str:
    """sha256 of `plurai.config.yaml`, over its content minus the change times the scan writes.

    A re-scan writes a new `scanned_at`, and a config written by an older scan may still carry a scan `commit` and a
    `last_changed` beside its citations; none of them is an input the env was generated from, so counting them would
    mark the mock, the scenarios and the evals stale after every scan. A config that carries none of them hashes its bytes.
    """
    try:
        config = yaml.safe_load(path.read_bytes().decode('utf-8')) if yaml is not None else None
    except (yaml.YAMLError, UnicodeDecodeError):
        return _sha256(path)
    if not isinstance(config, dict) or not any(k in config for k in _CHANGE_TIME_ROOT_KEYS):
        return _sha256(path)
    stripped = _without_change_times({k: v for k, v in config.items() if k not in _CHANGE_TIME_ROOT_KEYS})
    return hashlib.sha256(json.dumps(stripped, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')).hexdigest()


def _facts_hash(path: Path) -> str:
    """sha256 of the PRD's FACTS PROJECTION — the whole document MINUS the routed
    ``## Scenario design constraints`` span (the verbatim appendix bytes stay in; they are
    source facts the mock is built from).

    Env generation consumes world facts and tool contracts and has no use for scenario-design
    directives, so the MOCK's staleness is computed over this projection and a steering-only PRD
    edit stops buying a 10-25 minute env regeneration. Backend eval generation still reads the
    WHOLE PRD, directives included, so `scenarios` and `evals` keep the full-file hash and DO go
    stale on a directives edit.

    If that module cannot be imported or the PRD is not UTF-8, fall back to the full-file hash:
    that is the pre-projection behaviour, which over-reports staleness and never under-reports it.
    """
    try:
        if str(_VERIFY_PRD_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_VERIFY_PRD_SCRIPTS))
        from verify_prd_appendix import facts_hash  # noqa: PLC0415 — optional sibling, resolved at call time

        return facts_hash(path.read_bytes().decode('utf-8'))
    except (ImportError, UnicodeDecodeError):
        return _sha256(path)


#: Files fingerprinted a SECOND time over a projection of their content:
#: ``<file> -> (fingerprint key, downstream artifacts that hang off the projection, projector)``.
#: The projection's downstream set is disjoint from the file's own in ALIGNED_INPUTS — together
#: they are the file's full alignment contract.
PROJECTIONS: dict[str, tuple[str, tuple[str, ...], Callable[[Path], str]]] = {
    'simulation_prd.md': ('simulation_prd.md#facts', ('mock',), _facts_hash),
}


def fingerprint_inputs(env_dir: Path) -> dict[str, str]:
    """sha256 of each aligned input file present in ``env_dir``, plus each projection hash."""
    out: dict[str, str] = {}
    for name in ALIGNED_INPUTS:
        f = env_dir / name
        if f.is_file():
            out[name] = _config_hash(f) if name == 'plurai.config.yaml' else _sha256(f)
            if (proj := PROJECTIONS.get(name)) is not None:
                key, _downstream, projector = proj
                out[key] = projector(f)
    return out


def read_manifest(env_dir: Path) -> dict | None:
    """Load the manifest dict, or None if absent."""
    mf = env_dir / MANIFEST_FILENAME
    if not mf.is_file():
        return None
    if yaml is None:
        raise RuntimeError('pyyaml is required to read the env manifest')
    return yaml.safe_load(mf.read_text(encoding='utf-8')) or {}


def write_manifest(  # noqa: PLR0913 — id fields are explicit by design
    env_dir: Path,
    *,
    name: str,
    backend_url: str | None = None,
    env_id: str | None = None,
    scenario_set_id: str | None = None,
    eval_set_id: str | None = None,
    corpus_id: str | None = None,
    corpus_ids: list[str] | None = None,
    knowledge_bases: list[dict] | None = None,
    doc_examples_set_id: str | None = None,
    ledger_version: str | None = None,
) -> dict:
    """Write/refresh ``plurai.env.yaml`` for ``env_dir``.

    Preserves existing ids when a new value isn't supplied (so calling this
    after /generate-scenarios updates scenario_set_id without clobbering
    env_id). Always recomputes fingerprints from the current files and
    refreshes ``updated_at`` (preserving the original ``created_at``).

    ``ledger_version`` is the grounding ledger version the env's generators were pinned to. It follows the
    workdir's pin marker whenever the marker names one, so a write that passes no version (a generator recording
    its own set id) cannot leave an older pin in the manifest.
    """
    if yaml is None:
        raise RuntimeError('pyyaml is required to write the env manifest')
    env_dir.mkdir(parents=True, exist_ok=True)
    prior = read_manifest(env_dir) or {}

    def _keep(new, key):  # noqa: ANN001, ANN202
        return new if new is not None else prior.get(key)

    manifest = {
        **prior,  # a key this writer does not know (hand-added, newer schema) is kept, not dropped
        'schema_version': SCHEMA_VERSION,
        'name': name or prior.get('name'),
        'backend_url': _keep(backend_url, 'backend_url'),
        'env_id': _keep(env_id, 'env_id'),
        'scenario_set_id': _keep(scenario_set_id, 'scenario_set_id'),
        'eval_set_id': _keep(eval_set_id, 'eval_set_id'),
        'corpus_id': _keep(corpus_id, 'corpus_id'),
        # Multiple per-tenant corpora (round-robin at generation). Scalar corpus_id
        # stays for the single-shared-corpus case; a manifest may carry either.
        'corpus_ids': _keep(corpus_ids, 'corpus_ids'),
        # Multiple KNOWLEDGE BASES — one entry per distinct RAG/SQL tool the agent
        # owns (e.g. a personal-memory KB + a company-docs KB). Each is
        # ``{kb_id, label, corpus_ids: [...], tool}``; a per-customer tool carries a
        # LIST of corpus ids (round-robin within that KB). The backend turns each
        # entry into its own knowledge subflow. Orthogonal to the flat corpus_ids
        # above (which is the SINGLE-KB per-tenant case); a manifest carries whichever
        # the build produced.
        'knowledge_bases': _keep(knowledge_bases, 'knowledge_bases'),
        # Uploaded doc-examples set (the /upload-doc-examples backend handle). Recorded per-env
        # so /generate-scenarios reads THIS env's few-shot doc set (not a bare global dotfile) and
        # switching envs switches the doc set — same discipline as corpus_id above.
        'doc_examples_set_id': _keep(doc_examples_set_id, 'doc_examples_set_id'),
        'ledger_version': ledger_version or pinned_version(env_dir) or prior.get('ledger_version'),
        'fingerprints': fingerprint_inputs(env_dir),
        'created_at': prior.get('created_at') or _now_iso(),
        'updated_at': _now_iso(),
    }
    (env_dir / MANIFEST_FILENAME).write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding='utf-8',
    )
    return manifest


def record_ledger_version(env_dir: Path, ledger_version: str) -> dict:
    """Set ``ledger_version`` in the existing manifest and nothing else.

    Pinning is not generating, so the fingerprints stay as they were and a drifted input still reads as drifted.
    Raises FileNotFoundError when there is no manifest.
    """
    if yaml is None:
        raise RuntimeError('pyyaml is required to write the env manifest')
    manifest = read_manifest(env_dir)
    if manifest is None:
        raise FileNotFoundError(f'no {MANIFEST_FILENAME} in {env_dir}')
    manifest['ledger_version'] = ledger_version
    (env_dir / MANIFEST_FILENAME).write_text(yaml.safe_dump(manifest, sort_keys=False), encoding='utf-8')
    return manifest


def check_drift(env_dir: Path) -> list[dict]:
    """Compare current input hashes vs the manifest's recorded fingerprints.

    Returns a list of drift records, each:
      ``{'file': str, 'status': 'changed'|'added'|'removed',
         'stale_downstream': [..]}``
    Empty list == in sync. Raises FileNotFoundError if there's no manifest.

    ONE record per file even when the file is fingerprinted over a projection too
    (``simulation_prd.md``): the record's ``stale_downstream`` is the UNION of what the
    whole-file hash and the projection hash each invalidate, so a facts edit reports all three
    while a directives-only edit reports scenarios + evals and leaves the mock alone.

    Note: ``corpus_id`` is tracked in the manifest but has no aligned input
    file (the corpus is built from uploaded chunks, not a local artifact), so
    corpus staleness is never reported here.
    """
    manifest = read_manifest(env_dir)
    if manifest is None:
        raise FileNotFoundError(f'no {MANIFEST_FILENAME} in {env_dir}')
    recorded = manifest.get('fingerprints') or {}
    current = fingerprint_inputs(env_dir)
    drift: list[dict] = []
    for name, downstream in ALIGNED_INPUTS.items():
        was = recorded.get(name)
        now = current.get(name)
        stale = set(downstream) if was != now else set()
        if (proj := PROJECTIONS.get(name)) is not None:
            key, proj_downstream, _projector = proj
            # A manifest written before projections existed carries no projection key, so this
            # reads as drift once and self-heals on the next write — over-reporting, which is
            # the safe direction for a warn-only check.
            if recorded.get(key) != current.get(key):
                stale |= set(proj_downstream)
        if not stale:
            continue
        # Status is read off the FILE, not a projection: added/removed is one event for every
        # hash computed from it.
        if was is None:
            status = 'added'
        elif now is None:
            status = 'removed'
        else:
            status = 'changed'
        drift.append({
            'file': name,
            'status': status,
            'stale_downstream': [d for d in _DOWNSTREAM_ORDER if d in stale],
        })
    return drift


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Plurai env manifest helper')
    sub = parser.add_subparsers(dest='cmd', required=True)

    w = sub.add_parser('write', help='write/refresh the manifest')
    w.add_argument('env_dir', type=Path)
    w.add_argument('--name', required=True)
    w.add_argument('--backend-url')
    w.add_argument('--env-id')
    w.add_argument('--scenario-set-id')
    w.add_argument('--eval-set-id')
    w.add_argument('--corpus-id')
    w.add_argument('--corpus-ids', nargs='*', help='one or more per-tenant corpus ids')
    w.add_argument(
        '--knowledge-bases',
        help='JSON list of knowledge bases, each {kb_id, label, corpus_ids:[...], tool} '
        '(one per RAG/SQL tool the agent owns)',
    )
    w.add_argument('--doc-examples-set-id', help='the /upload-doc-examples backend set id for THIS env')
    w.add_argument('--ledger-version', help='the grounding ledger version the env was pinned to (default: the workdir pin)')

    r = sub.add_parser('read', help='print the manifest as JSON')
    r.add_argument('env_dir', type=Path)

    c = sub.add_parser('check', help='report drift as JSON (exit 0 always)')
    c.add_argument('env_dir', type=Path)

    args = parser.parse_args(argv)
    if args.cmd == 'write':
        kbs = json.loads(args.knowledge_bases) if args.knowledge_bases else None
        m = write_manifest(
            args.env_dir, name=args.name, backend_url=args.backend_url,
            env_id=args.env_id, scenario_set_id=args.scenario_set_id,
            eval_set_id=args.eval_set_id, corpus_id=args.corpus_id,
            corpus_ids=args.corpus_ids or None,
            knowledge_bases=kbs,
            doc_examples_set_id=args.doc_examples_set_id,
            ledger_version=args.ledger_version,
        )
        print(json.dumps(m, indent=2))
    elif args.cmd == 'read':
        m = read_manifest(args.env_dir)
        if m is None:
            print(f'no {MANIFEST_FILENAME} in {args.env_dir}', file=sys.stderr)
            return 1
        print(json.dumps(m, indent=2))
    elif args.cmd == 'check':
        try:
            drift = check_drift(args.env_dir)
        except FileNotFoundError as exc:
            print(json.dumps({'error': str(exc)}))
            return 1
        print(json.dumps({'in_sync': not drift, 'drift': drift}, indent=2))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
