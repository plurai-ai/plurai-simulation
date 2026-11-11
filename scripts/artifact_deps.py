#!/usr/bin/env python3
"""`artifacts/manifest.json` — what each artifact on the page was built from — and its blast radius.

    artifact_deps.py record  <wd> (--run NAME | --suite | --pull policies|scenarios)
    artifact_deps.py check   <wd> [--refresh] --json
    artifact_deps.py apply   <wd> [--refresh]
    artifact_deps.py declare <wd> env-edited

An edit rarely changes one file: a grading edit to one scenario dates that scenario's verdicts, the
findings and fix items read off them, and the suite page built from its slot. `check` compares every
artifact's RECORDED inputs with the CURRENT ones through ONE declared table, `DEPENDENCIES` (D1–D10):
each row names its artifact, a reader that lists its items with their recorded inputs, and the inputs
it compares, each with the action a move costs. Nothing branches per artifact: `evaluate` walks the rows,
asks each input's comparator in `COMPARE`, and keeps the HEAVIEST action per item
(`rerun` > `rescore` > `pull` > `rebuild`). A whole-run (or whole-set) entry subsumes its items.

CURRENT values come only from files the plugin already writes (`.scenario_set_id`, `.eval_set_id`,
`.env_id`, the corpus markers, the harness files, `policies_config.yaml`, `artifacts/.pieces/`,
`suite_state.json`) plus the backend observations `--refresh` stores in `current.remote`. RECORDED
values are read from the producer's own file each time (`run.json`, each item's verdicts, `suite.json`,
`runs/index.json`, `pieces.json`); the manifest records only what no producer stamps (harness, env,
corpus and per-policy shas for a run; content shas for suite.json and the renders; pull revisions).

UNVERIFIED IS NEVER CURRENT. An input whose recorded or current side cannot be read is reported with
reason `unverified` and that input's action — including a remote axis with no observation at all, and
one whose latest `--refresh` failed after the last good observation.

`check --json` prints `{schema, adopted, stale[], rebuild[], pull[], rewind}` and exits 0 (2 when the
workdir is not a directory). `apply` = check, then every `pull` (scenario slots/set via
`report_pieces.bundle`, policies via the artifact file route — refused while an edit session holds a
draft), then every `rebuild` in D4 → D5 → D6 order, then prints the post-apply `check` JSON (plus
`applied`, `errors`). It never rescores, re-runs, deletes or edits a run. `declare env-edited` bumps
`current.env_epoch` (an in-place env edit changes nothing on disk). `stale_items(workdir)` and
`draft_sections(workdir)` are pure and local, for `publish_report.py`.

The first command on a workdir with no manifest ADOPTS: it records the current harness/env/corpus and
policy shas for every existing run (it cannot know older ones), `baseline: adopted`, `adopted: true`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / 'hooks'))
import _no_redirect  # noqa: E402 - a redirect must not carry the bearer on
import corpus_coordinate  # noqa: E402 - sibling script: all three corpus marker shapes are read there
import fix_verification  # noqa: E402 - sibling script: the fix plan's header and table grammar live there
import harness_fingerprint  # noqa: E402 - sibling script: the harness fingerprint lives there
import lane_pin  # noqa: E402 - sibling script: the staleness reason vocabulary is owned there
import record_finding  # noqa: E402 - sibling script: the findings store reader lives there
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"
import verdicts_file  # noqa: E402 - the one reader of an item's verdicts
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

SUITE_SCRIPTS = HERE.parent / 'skills' / 'simulation-generate-suite' / 'scripts'

DEPS_SCHEMA = 'plurai-artifact-deps/1'
MANIFEST_SCHEMA = 'plurai-artifact-manifest/1'
MANIFEST = Path('artifacts') / 'manifest.json'

#: Lightest first; the heaviest moved input names an item's action.
ACTIONS = ('rebuild', 'pull', 'rescore', 'rerun')
_RANK = {action: rank for rank, action in enumerate(ACTIONS)}

#: The reasons `lane_pin` already names are read off its table, so a rename there fails here at import.
_PIN = {reason: reason for reason in lane_pin.REASONS}
R_SET = _PIN['scenario-set-moved']
R_GT = _PIN['gt-revision-moved']
R_EVAL_SET = _PIN['eval-set-moved']
R_EVAL_REV = _PIN['eval-revision-moved']
R_HARNESS = _PIN['harness-fingerprint-changed']
R_CORPUS = _PIN['corpus-moved']
R_UNATTRIBUTED = 'gt-edit-unattributed'
R_ENV = 'env-edited'
R_UNVERIFIED = 'unverified'
R_RUN_STALE = 'run-stale'
R_INPUT = 'input-changed'
REASONS = (R_SET, R_GT, R_UNATTRIBUTED, R_EVAL_SET, R_EVAL_REV, R_HARNESS, R_ENV, R_CORPUS, R_UNVERIFIED, R_RUN_STALE, R_INPUT)

POLICIES_STATE_HEADER = 'X-Plurai-Policies-State'
HTTP_TIMEOUT = 10.0
EXIT_UNREADABLE = 2

#: A recorded input nobody recorded — distinct from a recorded `None`; both read as unverified.
UNRECORDED = object()


# --------------------------------------------------------------------------- small readers


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _epoch(value: object) -> Optional[float]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _doc(path: Path) -> dict:
    doc = _read_json(path)
    return doc if isinstance(doc, dict) else {}


def _marker(workdir: Path, name: str) -> Optional[str]:
    try:
        return (workdir / name).read_text(encoding='utf-8').strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _sha_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob('*') if p.is_file())


def _sha_dir(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _files_under(root):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0' + (_sha_file(path) or '-').encode('ascii') + b'\n')
    return digest.hexdigest()


def _int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _verdicts(scenario_dir: Path) -> Optional[dict]:
    """The item's verdicts (`verdicts_file.read`), or None when it has none this reader can use: unverifiable."""
    try:
        return verdicts_file.read(scenario_dir)
    except verdicts_file.VerdictsError:
        return None


# --------------------------------------------------------------------------- the manifest


def empty_manifest(baseline: str) -> dict:
    return {'schema': MANIFEST_SCHEMA, 'baseline': baseline, 'current': {'env_epoch': 0, 'remote': {}}, 'artifacts': {}}


def read_manifest(workdir: Path) -> tuple[Optional[dict], Optional[str]]:
    """`(manifest, why-not)`: `(None, None)` when absent; `(None, why)` when present and unusable."""
    path = workdir / MANIFEST
    if not path.exists():
        return None, None
    doc = _read_json(path)
    if not isinstance(doc, dict) or doc.get('schema') != MANIFEST_SCHEMA:
        return None, f'{path} is not a {MANIFEST_SCHEMA} manifest'
    doc.setdefault('current', {}).setdefault('remote', {})
    doc['current'].setdefault('env_epoch', 0)
    doc.setdefault('artifacts', {})
    return doc, None


def _run_key(run: str) -> str:
    return f'artifacts/runs/{run}'


# --------------------------------------------------------------------------- the context: current coordinates


class Ctx:
    """One workdir's current coordinates, read once, and the entries emitted so far (D8–D10 read D7's)."""

    def __init__(self, workdir: Path, manifest: dict) -> None:
        self.wd = workdir
        self.art = workdir / 'artifacts'
        self.manifest = manifest
        self.entries: list[Entry] = []
        self.current_set = _marker(workdir, '.scenario_set_id')
        self.current_eval = _marker(workdir, '.eval_set_id')
        self.pieces = _doc(self.art / '.pieces' / 'pieces.json')
        self._harness: Any = UNRECORDED

    # -- local axes

    def harness(self) -> Optional[str]:
        if self._harness is UNRECORDED:
            try:
                self._harness = str(harness_fingerprint.fingerprint(self.wd, harness_fingerprint.detect_lang(self.wd))['fingerprint'])
            except (OSError, ValueError):
                self._harness = None
        return self._harness

    def env(self) -> list:
        return [_marker(self.wd, '.env_id'), _int(self.manifest['current'].get('env_epoch')) or 0]

    def corpus(self) -> Any:
        """Sorted ids; `'absent'` when no marker exists; None when one exists and cannot be read (unknown)."""
        reading = corpus_coordinate.read_coordinate(self.wd)
        if reading.ids is not None:
            return sorted(reading.ids)
        return 'absent' if not reading.read and not reading.unreadable else None

    def policies_path(self) -> Path:
        return self.wd / 'policies_config.yaml'

    def policies_sha(self) -> Optional[str]:
        return _sha_file(self.policies_path())

    def policy_shas(self) -> Optional[dict]:
        """`{policy key: sha of its canonical block}`, or None when the file cannot be read as a mapping."""
        try:
            import yaml  # noqa: PLC0415 - present under the plugin's interpreter; absent ⇒ no per-policy granularity

            doc = yaml.safe_load(self.policies_path().read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001 - unreadable ⇒ whole-scenario granularity, never a guess
            return None
        if not isinstance(doc, dict):
            return None
        return {str(key): hashlib.sha256(json.dumps(block, sort_keys=True, default=str).encode('utf-8')).hexdigest() for key, block in doc.items()}

    def slot_dirs(self) -> list[Path]:
        root = self.art / 'scenarios'
        return sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith('.')) if root.is_dir() else []

    def suite_inputs(self) -> dict:
        graph = self.art / 'scenarios' / 'dimension_graph.yaml'
        return {'policies': self.policies_sha(), 'slots': {d.name: _sha_dir(d) for d in self.slot_dirs()}, 'graph': _sha_file(graph)}

    def suite_input_paths(self) -> list[Path]:
        paths = [self.policies_path(), self.art / 'scenarios' / 'dimension_graph.yaml']
        for slot in self.slot_dirs():
            paths.extend(_files_under(slot))
        return [p for p in paths if p.exists()]

    def run_dirs(self) -> list[Path]:
        runs = self.art / 'runs'
        return sorted(p.parent for p in runs.glob('*/run.json')) if runs.is_dir() else []

    def record(self, key: str) -> Optional[dict]:
        rec = self.manifest['artifacts'].get(key)
        return rec if isinstance(rec, dict) and isinstance(rec.get('inputs'), dict) else None

    # -- remote axes: the newest observation wins; a failed refresh newer than it is unverified

    def _failed_after(self, ident: str, at: float) -> bool:
        failed = _epoch((self.manifest['current']['remote'].get(ident) or {}).get('failed_at'))
        return failed is not None and failed >= at

    def remote_set(self, set_id: Optional[str]) -> Optional[tuple[float, int, Optional[list]]]:
        """`(at, gt_revision, edit_log scenarios or None)` — None when unverified."""
        if not set_id:
            return None
        seen: list[tuple[float, int, Optional[list]]] = []
        rec = self.manifest['current']['remote'].get(set_id) or {}
        if _int(rec.get('gt_revision')) is not None:
            log = rec.get('edit_log')
            seen.append((_epoch(rec.get('fetched_at')) or 0.0, rec['gt_revision'], list(log) if isinstance(log, list) else None))
        meta_path = self.art / '.pieces' / f'meta_{set_id}.json'
        detail = _doc(meta_path).get('detail')
        if isinstance(detail, dict) and _int(detail.get('gt_revision')) is not None:
            seen.append((_mtime(meta_path) or 0.0, detail['gt_revision'], edit_log_scenarios(detail.get('edit_log'))))
        if not seen:
            return None
        best = max(seen, key=lambda obs: obs[0])
        return None if self._failed_after(set_id, best[0]) else best

    def remote_eval(self, eval_id: Optional[str]) -> Optional[tuple[float, str]]:
        """`(at, eval_revision)` — None when unverified."""
        if not eval_id:
            return None
        seen: list[tuple[float, str]] = []
        rec = self.manifest['current']['remote'].get(eval_id) or {}
        if _str(rec.get('eval_revision')):
            seen.append((_epoch(rec.get('fetched_at')) or 0.0, rec['eval_revision'].strip()))
        pulled = self.record('policies_config.yaml')
        if pulled and pulled['inputs'].get('eval_set') == eval_id and _str(pulled['inputs'].get('eval_revision')):
            seen.append((_epoch(pulled.get('recorded_at')) or 0.0, pulled['inputs']['eval_revision'].strip()))
        state = _doc(self.art / 'suite_state.json')
        for phase in state.get('phases') or []:
            if not isinstance(phase, dict) or not _str(phase.get('eval_revision')):
                continue
            ids = phase.get('ids') if isinstance(phase.get('ids'), dict) else {}
            if ids.get('eval_set', (state.get('ids') or {}).get('eval_set')) == eval_id:
                seen.append((_epoch(phase.get('at')) or 0.0, phase['eval_revision'].strip()))
        if not seen:
            return None
        best = max(seen, key=lambda obs: obs[0])
        return None if self._failed_after(eval_id, best[0]) else best

    # -- where a run's / the bundle's recorded set comes from

    def run_set(self, run: str, doc: dict) -> Any:
        if _str(doc.get('scenario_set_id')):
            return doc['scenario_set_id'].strip()
        for entry in _doc(self.art / 'runs' / 'index.json').get('runs') or []:
            if isinstance(entry, dict) and entry.get('name') == run and _str(entry.get('scenario_set_id')):
                return entry['scenario_set_id'].strip()
        rec = self.record(_run_key(run))
        return rec['inputs'].get('scenario_set', UNRECORDED) if rec else UNRECORDED

    def bundle_recorded(self) -> tuple[Any, Any]:
        """`(set id, gt_revision)` the page's scenarios were pulled at: pieces.json, the pull record, suite.json."""
        scen = self.pieces.get('scenarios') if isinstance(self.pieces.get('scenarios'), dict) else {}
        if _str(scen.get('set_id')):
            return scen['set_id'].strip(), _int(scen.get('gt_revision'))
        rec = self.record('artifacts/scenarios/')
        if rec and _str(rec['inputs'].get('scenario_set')):
            return rec['inputs']['scenario_set'], _int(rec['inputs'].get('gt_revision'))
        return _str(_doc(self.art / 'suite.json').get('scenario_set_id')) or UNRECORDED, None


def edit_log_scenarios(raw: object) -> Optional[list]:
    """The scenario each `edit_log` entry edited, in order; None when any entry does not name one."""
    if not isinstance(raw, list):
        return None
    names = [entry.get('scenario') for entry in raw if isinstance(entry, dict)]
    return names if len(names) == len(raw) and all(isinstance(n, str) and n for n in names) else None


# --------------------------------------------------------------------------- comparators: one per coordinate


class Moved(NamedTuple):
    suffix: Optional[str] = None  # a sub-item (a slot, a policy) under the item
    reason: Optional[str] = None  # None ⇒ the input's declared reason
    action: Optional[str] = None  # None ⇒ the input's declared action


CURRENT: list = []
_UNVERIFIED = [Moved(reason=R_UNVERIFIED)]


def _equal_or_moved(recorded: Any, current: Any) -> list:
    if recorded is UNRECORDED or recorded is None or current is None:
        return _UNVERIFIED
    return CURRENT if recorded == current else [Moved()]


def _cmp_scenario_set(ctx: Ctx, recorded: Any) -> list:
    return _equal_or_moved(recorded, ctx.current_set)


def _cmp_eval_set(ctx: Ctx, recorded: Any) -> list:
    return _equal_or_moved(recorded, ctx.current_eval)


def _cmp_harness(ctx: Ctx, recorded: Any) -> list:
    return _equal_or_moved(recorded, ctx.harness())


def _cmp_env(ctx: Ctx, recorded: Any) -> list:
    return _equal_or_moved(list(recorded) if isinstance(recorded, (list, tuple)) else recorded, ctx.env())


def _cmp_corpus(ctx: Ctx, recorded: Any) -> list:
    return _equal_or_moved(recorded, ctx.corpus())


def gt_moved(ctx: Ctx, recorded_gt: Optional[int]) -> Any:
    """For the CURRENT set: `[]` current, a set of moved scenarios, `R_UNATTRIBUTED`, or `R_UNVERIFIED`."""
    observed = ctx.remote_set(ctx.current_set)
    if observed is None or recorded_gt is None:
        return R_UNVERIFIED
    _, current_gt, log = observed
    if current_gt == recorded_gt:
        return set()
    if current_gt < recorded_gt:
        return R_UNVERIFIED
    # Attribution guard: the log names one edit per revision, or the moved scenarios cannot be named.
    if log is None or len(log) != current_gt:
        return R_UNATTRIBUTED
    return set(log[recorded_gt:current_gt])


def _cmp_gt_slots(ctx: Ctx, recorded: dict) -> list:
    if recorded['set'] != ctx.current_set:
        return CURRENT  # the scenario_set input answers for a different set
    moved = gt_moved(ctx, recorded['gt'])
    if isinstance(moved, str):
        return [Moved(reason=moved)]
    return [Moved(suffix=slot) for slot in sorted(moved)]


def _cmp_gt_revision(ctx: Ctx, recorded: dict) -> list:
    if recorded['set'] != ctx.current_set:
        return CURRENT
    moved = gt_moved(ctx, recorded['gt'])
    if isinstance(moved, str):
        return [Moved(reason=moved)]
    return [Moved()] if recorded['scenario'] in moved else CURRENT


def _cmp_graph_source(ctx: Ctx, recorded: dict) -> list:
    meta = _doc(ctx.art / '.pieces' / f'meta_{recorded["set"]}.json')
    return [Moved()] if recorded['source'] == 'envelope' and meta.get('status') == 'done' else CURRENT


def _cmp_policies_revision(ctx: Ctx, recorded: dict) -> list:
    if recorded['eval_set'] != ctx.current_eval:
        return CURRENT
    observed = ctx.remote_eval(ctx.current_eval)
    if observed is None:
        return _UNVERIFIED
    if recorded['rev'] is None:
        # No pull stamped this file's revision: it is current only if it was written after every observation.
        written = _mtime(ctx.policies_path())
        return CURRENT if written is not None and written >= observed[0] else _UNVERIFIED
    return CURRENT if recorded['rev'] == observed[1] else [Moved()]


def _cmp_eval_revision(ctx: Ctx, recorded: dict) -> list:
    if recorded['eval_set'] != ctx.current_eval:
        return CURRENT
    observed = ctx.remote_eval(ctx.current_eval)
    if observed is None or recorded['rev'] is None:
        return _UNVERIFIED
    if recorded['rev'] == observed[1]:
        return CURRENT
    changed = _changed_policies(ctx, recorded.get('policies'))
    return [Moved(suffix=name) for name in changed] if changed else [Moved()]


def _changed_policies(ctx: Ctx, recorded: Optional[dict]) -> list:
    """Per-policy items only when the run's policy shas were recorded AND the local file is proven at the
    current revision (D3 emitted nothing); otherwise none, and the whole scenario is stale."""
    if not isinstance(recorded, dict) or any(entry.row == 'D3' for entry in ctx.entries) or not ctx.policies_path().exists():
        return []
    current = ctx.policy_shas()
    if current is None:
        return []
    return sorted(name for name in set(recorded) | set(current) if recorded.get(name) != current.get(name))


def _newest(paths: list[Path]) -> Optional[float]:
    times = [t for t in (_mtime(p) for p in paths) if t is not None]
    return max(times) if times else None


def _cmp_suite_inputs(ctx: Ctx, recorded: Any) -> list:
    """Current when the recorded content shas match, or when suite.json is newer than every input file."""
    if isinstance(recorded, dict) and {k: recorded.get(k) for k in ('policies', 'slots', 'graph')} == ctx.suite_inputs():
        return CURRENT
    built, newest = _mtime(ctx.art / 'suite.json'), _newest(ctx.suite_input_paths())
    return CURRENT if built is not None and (newest is None or built >= newest) else [Moved()]


def render_paths(ctx: Ctx) -> list[Path]:
    paths = [ctx.art / 'scenarios.md', ctx.art / 'evals.md', *(ctx.art / 'scenarios').glob('*.md'), *(ctx.art / 'charts').glob('*.svg')]
    return [p for p in paths if p.is_file()]


def _cmp_renders(ctx: Ctx, recorded: Any) -> list:
    suite = ctx.art / 'suite.json'
    if not suite.exists():
        return CURRENT
    if isinstance(recorded, dict) and recorded.get('suite_json') == _sha_file(suite):
        return CURRENT
    oldest = min((t for t in (_mtime(p) for p in render_paths(ctx)) if t is not None), default=None)
    built = _mtime(suite)
    return CURRENT if oldest is not None and built is not None and oldest >= built else [Moved()]


def _cmp_run_set(ctx: Ctx, _recorded: Any) -> list:
    index = ctx.art / 'runs' / 'index.json'
    names = {e.get('name') for e in _doc(index).get('runs') or [] if isinstance(e, dict)}
    dirs = ctx.run_dirs()
    if names != {d.name for d in dirs}:
        return [Moved()]
    built, newest = _mtime(index), _newest([d / 'run.json' for d in dirs])
    return CURRENT if built is not None and (newest is None or built >= newest) else [Moved()]


def _cmp_upstream(ctx: Ctx, rows: tuple) -> list:
    """A rebuild whose own inputs MOVED is stale too, for the upstream reason. An upstream that is only
    unverified is reported on its own row; it does not make every local rebuild unverifiable with it."""
    upstream = [entry for entry in ctx.entries if entry.row in rows and entry.reason != R_UNVERIFIED]
    return [Moved(reason=upstream[0].reason)] if upstream else CURRENT


def _pin_matches(item: Optional[str], scenario: str, policy: Optional[str]) -> bool:
    if item is None or item == scenario:
        return True
    return item == f'{scenario}/{policy}' if policy else item.startswith(f'{scenario}/')


def _cmp_run_state(ctx: Ctx, cited: list) -> list:
    """The heaviest D7 action over the cited scenarios (None pins = any item of the run)."""
    actions = []
    for run, pins in cited:
        if not (ctx.art / 'runs' / run / 'run.json').exists():
            return [Moved(reason=R_UNVERIFIED, action='rerun')]
        for entry in ctx.entries:
            if entry.row == 'D7' and entry.run == run and (pins is None or any(_pin_matches(entry.item, s, p) for s, p in pins)):
                actions.append(entry.action)
    return [Moved(action=max(actions, key=_RANK.__getitem__))] if actions else CURRENT


def scenario_dirs(ctx: Ctx, run: str, scenarios: Optional[list]) -> list[Path]:
    run_dir = ctx.art / 'runs' / run
    if scenarios:
        return [run_dir / s for s in scenarios]
    return sorted(p.parent for p in run_dir.glob('*/transcript.json'))


def _cmp_verdict_time(ctx: Ctx, recorded: dict) -> list:
    """Stale when a cited scenario's verdicts changed after the item was written: any `history` entry of its
    `verdicts.json` (the run's own included) stamped later. Read off the file's contents, never its mtime."""
    at = recorded['at']
    for run, scenarios in recorded['runs']:
        for scenario_dir in scenario_dirs(ctx, run, scenarios):
            if not scenario_dir.is_dir():
                continue
            doc = _verdicts(scenario_dir)
            if doc is None:
                return _UNVERIFIED
            if at is None:
                if verdicts_file.rescored(doc):
                    return _UNVERIFIED  # an unstamped item beside a later rescore cannot be proven current
                continue
            times = [_epoch(entry.get('at')) for entry in verdicts_file.history(doc)]
            if any(t is None for t in times):
                return _UNVERIFIED
            if any(t > at for t in times):
                return [Moved()]
    return CURRENT


def _cmp_held_stale(_ctx: Ctx, flagged: Any) -> list:
    """A finding row `open_epoch` kept across a resume (`run_stale: true`) is stale until a `--run` re-record clears it."""
    return [Moved()] if flagged is True else CURRENT


COMPARE: dict[str, Callable[[Ctx, Any], list]] = {
    'scenario_set': _cmp_scenario_set,
    'eval_set': _cmp_eval_set,
    'harness': _cmp_harness,
    'env': _cmp_env,
    'corpus': _cmp_corpus,
    'gt_slots': _cmp_gt_slots,
    'gt_revision': _cmp_gt_revision,
    'graph_source': _cmp_graph_source,
    'policies_revision': _cmp_policies_revision,
    'eval_revision': _cmp_eval_revision,
    'suite_inputs': _cmp_suite_inputs,
    'renders': _cmp_renders,
    'run_set': _cmp_run_set,
    'upstream': _cmp_upstream,
    'run_state': _cmp_run_state,
    'verdict_time': _cmp_verdict_time,
    'held_stale': _cmp_held_stale,
}


# --------------------------------------------------------------------------- item readers: one per row


class Item(NamedTuple):
    artifact: Optional[str]  # None ⇒ the row's artifact
    item: Optional[str]
    inputs: dict
    run: Optional[str] = None


def _items_bundle(ctx: Ctx) -> list:
    if not (ctx.art / 'scenarios').is_dir():
        return []
    set_id, gt = ctx.bundle_recorded()
    return [Item(None, None, {'scenario_set': set_id, 'gt_slots': {'set': set_id, 'gt': gt}})]


def _items_graph(ctx: Ctx) -> list:
    if not (ctx.art / 'scenarios' / 'dimension_graph.yaml').is_file():
        return []
    set_id, _ = ctx.bundle_recorded()
    scen = ctx.pieces.get('scenarios') if isinstance(ctx.pieces.get('scenarios'), dict) else {}
    return [Item(None, None, {'scenario_set': set_id, 'graph_source': {'set': set_id, 'source': scen.get('graph_source')}})]


def _items_policies(ctx: Ctx) -> list:
    if not ctx.policies_path().is_file():
        return []
    sha = ctx.policies_sha()
    pulled = ctx.record('policies_config.yaml')
    pieces = ctx.pieces.get('policies') if isinstance(ctx.pieces.get('policies'), dict) else {}
    if pulled and pulled['inputs'].get('sha') == sha:
        eval_set, rev = pulled['inputs'].get('eval_set'), _str(pulled['inputs'].get('eval_revision'))
    elif pieces.get('sha256') == sha and _str(pieces.get('eval_set_id')):
        eval_set, rev = pieces['eval_set_id'].strip(), None
    else:
        eval_set, rev = UNRECORDED, None
    return [Item(None, None, {'eval_set': eval_set, 'policies_revision': {'eval_set': eval_set, 'rev': rev}})]


def _items_suite(ctx: Ctx) -> list:
    suite_path = ctx.art / 'suite.json'
    if not suite_path.is_file():
        return []
    suite = _doc(suite_path)
    rec = ctx.record('artifacts/suite.json')
    inputs: dict = {'scenario_set': _str(suite.get('scenario_set_id')) or UNRECORDED,
                    'suite_inputs': rec['inputs'] if rec else None, 'upstream': ('D1', 'D2', 'D3')}
    if 'eval_set_id' in suite:
        inputs['eval_set'] = _str(suite.get('eval_set_id')) or UNRECORDED
    return [Item(None, None, inputs)]


def _items_renders(ctx: Ctx) -> list:
    if not render_paths(ctx):
        return []
    rec = ctx.record('artifacts/scenarios.md')
    return [Item(None, None, {'renders': rec['inputs'] if rec else None, 'upstream': ('D4',)})]


def _items_runs_index(ctx: Ctx) -> list:
    return [Item(None, None, {'run_set': True})] if (ctx.art / 'runs' / 'index.json').is_file() else []


def _items_runs(ctx: Ctx) -> list:
    """Per run: one run-level item (whole-run axes) and one item per scenario (grading axes)."""
    items = []
    for run_dir in ctx.run_dirs():
        run, doc = run_dir.name, _doc(run_dir / 'run.json')
        artifact = f'{_run_key(run)}/run.json'
        rec = ctx.record(_run_key(run))
        recorded = rec['inputs'] if rec else {}
        harness = recorded.get('harness', UNRECORDED)
        if harness is UNRECORDED:
            pin = lane_pin.read_pin(ctx.wd) or {}
            out_dir = _str(pin.get('out_dir'))
            if out_dir and Path(out_dir).name == run and _str(pin.get('harness_fingerprint')):
                harness = pin['harness_fingerprint']
        run_set = ctx.run_set(run, doc)
        eval_set = _str(doc.get('eval_set_id')) or UNRECORDED
        items.append(Item(artifact, None, {'scenario_set': run_set, 'harness': harness, 'env': recorded.get('env', UNRECORDED),
                                           'corpus': recorded.get('corpus', UNRECORDED), 'eval_set': eval_set}, run))
        integrity = doc.get('run_integrity') if isinstance(doc.get('run_integrity'), dict) else {}
        per_scenario = integrity.get('eval_revisions') if isinstance(integrity.get('eval_revisions'), dict) else {}
        names = [str(r['id']) for r in doc.get('scenarios') or [] if isinstance(r, dict) and r.get('id')]
        for scenario in names or [d.name for d in scenario_dirs(ctx, run, None)]:
            verdicts = _verdicts(run_dir / scenario)
            # A rescore moved this item's frame: its verdicts.json names the revisions it was judged against.
            rescored = verdicts is not None and verdicts_file.rescored(verdicts)
            judged = verdicts_file.judged_against(verdicts) if rescored and verdicts is not None else {}
            gt = _int(judged.get('gt_revision'))
            gt = gt if gt is not None else _int(doc.get('gt_revision'))
            rev = _str(judged.get('eval_revision')) or _str(per_scenario.get(scenario)) or _str(integrity.get('eval_revision'))
            items.append(Item(artifact, scenario, {
                'gt_revision': {'set': run_set, 'gt': gt, 'scenario': scenario},
                'eval_revision': {'eval_set': eval_set, 'rev': rev, 'policies': None if rescored else recorded.get('policies')},
            }, run))
    return items


def _items_release_verdict(ctx: Ctx) -> list:
    return [Item(f'{_run_key(d.name)}/release_verdict.json', None, {'run_state': [(d.name, None)]}, d.name)
            for d in ctx.run_dirs() if (d / 'release_verdict.json').is_file()]


def _items_findings(ctx: Ctx) -> list:
    try:
        rows = record_finding.read_rows(ctx.art / 'agent_findings.jsonl')
    except (OSError, ValueError):
        return []
    items = []
    for finding_id, row in rows.items():
        source = row.get('source') if isinstance(row.get('source'), dict) else {}
        run = _str(source.get('run'))
        held = row.get('run_stale') is True
        if held and (row.get('found_during') == 'scan' or not run):
            items.append(Item(None, finding_id, {'held_stale': True}))
            continue
        if row.get('found_during') == 'scan' or not run:
            continue  # no run input: nothing here can date it
        scenarios = [s for s in source.get('scenarios') or [] if isinstance(s, str) and s] or None
        pins = [(s, None) for s in scenarios] if scenarios else None
        items.append(Item(None, finding_id, {'run_state': [(run, pins)], 'held_stale': held,
                                             'verdict_time': {'at': _epoch(source.get('recorded_at')), 'runs': [(run, scenarios)]}}, run))
    return items


_PIN_CELL = re.compile(r'`([^`]+)` · `([^`]+)`')
_VERIFY_HEADING = re.compile(r'^## Verification — runs/(?P<run>[A-Za-z0-9][\w.-]*)\s*$', re.M)


def parse_fixes(text: str) -> tuple[list[str], dict[int, Optional[list]]]:
    """`(runs, {fix number: pins or None})` — the plan's run and verify run, and each fix's pinned scenarios."""
    runs = []
    for line in text.splitlines():
        match = fix_verification._PLAN_HEADER.match(line.strip())  # noqa: SLF001 - the plan header grammar is owned there
        if match:
            runs.append(match.group('run').removeprefix('runs/'))
            break
    fixes: dict[int, Optional[list]] = {n: None for n in fix_verification.fix_titles(text)}
    verify = _VERIFY_HEADING.search(text)
    if verify:
        runs.append(verify.group('run'))
        section = text[verify.end():].split('\n## ', 1)[0]
        for line in section.splitlines():
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) >= 6 and cells[0].isdigit():
                pins = [(s, None if p == 'goal' else p) for s, p in _PIN_CELL.findall(cells[5])]
                fixes[int(cells[0])] = pins or None
    return list(dict.fromkeys(runs)), fixes


def _items_fixes(ctx: Ctx) -> list:
    path = ctx.art / 'fixes.md'
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    runs, fixes = parse_fixes(text)
    if not runs:
        return []
    at = _mtime(path)
    items = []
    for number, pins in sorted(fixes.items()):
        scenarios = sorted({s for s, _ in pins}) if pins else None
        items.append(Item(None, f'fix {number}', {'run_state': [(run, pins) for run in runs],
                                                  'verdict_time': {'at': at, 'runs': [(run, scenarios) for run in runs]}}, runs[-1]))
    return items


# --------------------------------------------------------------------------- THE table


class Input(NamedTuple):
    coordinate: str  # a key of COMPARE, and of the reader's `Item.inputs`
    action: Optional[str]  # None ⇒ the comparator states it ("same action" as what it reads)
    reason: str


class Row(NamedTuple):
    id: str
    artifact: str
    item: Optional[str]
    items: Callable[[Ctx], list]
    inputs: tuple
    performer: str  # 'apply' performs its pulls/rebuilds; 'flow' = the calling skill redoes the stage


DEPENDENCIES: tuple = (
    Row('D1', 'artifacts/scenarios/', 'slot', _items_bundle,
        (Input('scenario_set', 'pull', R_SET), Input('gt_slots', 'pull', R_GT)), 'apply'),
    Row('D2', 'artifacts/scenarios/dimension_graph.yaml', None, _items_graph,
        (Input('scenario_set', 'pull', R_SET), Input('graph_source', 'pull', R_UNVERIFIED)), 'apply'),
    Row('D3', 'policies_config.yaml', None, _items_policies,
        (Input('eval_set', 'pull', R_EVAL_SET), Input('policies_revision', 'pull', R_EVAL_REV)), 'apply'),
    Row('D4', 'artifacts/suite.json', None, _items_suite,
        (Input('scenario_set', 'rebuild', R_SET), Input('eval_set', 'rebuild', R_EVAL_SET),
         Input('suite_inputs', 'rebuild', R_INPUT), Input('upstream', 'rebuild', R_INPUT)), 'apply'),
    Row('D5', 'artifacts/scenarios.md', None, _items_renders,
        (Input('renders', 'rebuild', R_INPUT), Input('upstream', 'rebuild', R_INPUT)), 'apply'),
    Row('D6', 'artifacts/runs/index.json', None, _items_runs_index,
        (Input('run_set', 'rebuild', R_INPUT),), 'apply'),
    Row('D7', 'artifacts/runs/<run>/run.json', 'scenario[/policy]', _items_runs,
        (Input('scenario_set', 'rerun', R_SET), Input('harness', 'rerun', R_HARNESS), Input('env', 'rerun', R_ENV),
         Input('corpus', 'rerun', R_CORPUS), Input('gt_revision', 'rescore', R_GT), Input('eval_set', 'rescore', R_EVAL_SET),
         Input('eval_revision', 'rescore', R_EVAL_REV)), 'flow'),
    Row('D8', 'artifacts/runs/<run>/release_verdict.json', None, _items_release_verdict,
        (Input('run_state', None, R_RUN_STALE),), 'flow'),
    Row('D9', 'artifacts/agent_findings.jsonl', 'finding id', _items_findings,
        (Input('run_state', None, R_RUN_STALE), Input('verdict_time', 'rebuild', R_RUN_STALE), Input('held_stale', 'rebuild', R_RUN_STALE)), 'flow'),
    Row('D10', 'artifacts/fixes.md', 'fix N', _items_fixes,
        (Input('run_state', None, R_RUN_STALE), Input('verdict_time', 'rebuild', R_RUN_STALE)), 'flow'),
)


class Entry(NamedTuple):
    artifact: str
    item: Optional[str]
    reason: str
    action: str
    row: str
    run: Optional[str]
    performer: str

    def public(self) -> dict:
        out = {'artifact': self.artifact}
        if self.item is not None:
            out['item'] = self.item
        out.update({'reason': self.reason, 'action': self.action})
        return out


def _join(item: Optional[str], suffix: Optional[str]) -> Optional[str]:
    return item if suffix is None else (suffix if item is None else f'{item}/{suffix}')


def evaluate(ctx: Ctx) -> list:
    """Every stale entry, in table order: the heaviest moved input per item, then whole-item subsumption."""
    for row in DEPENDENCIES:
        for it in row.items(ctx):
            best: dict = {}
            for inp in row.inputs:
                if inp.coordinate not in it.inputs:
                    continue
                for moved in COMPARE[inp.coordinate](ctx, it.inputs[inp.coordinate]):
                    entry = Entry(it.artifact or row.artifact, _join(it.item, moved.suffix), moved.reason or inp.reason,
                                  moved.action or inp.action, row.id, it.run, row.performer)
                    key = (entry.artifact, entry.item)
                    if key not in best or _RANK[entry.action] > _RANK[best[key].action]:
                        best[key] = entry
            ctx.entries.extend(best.values())
    return [e for e in ctx.entries if not _subsumed(e, ctx.entries)]


def _subsumed(entry: Entry, entries: list) -> bool:
    if entry.item is None:
        return False
    return any(other.artifact == entry.artifact and other is not entry and _RANK[other.action] >= _RANK[entry.action]
               and (other.item is None or entry.item.startswith(f'{other.item}/')) for other in entries)


def rewind_runs(ctx: Ctx) -> set:
    """The runs a rewind is decided on: the current set's runs, the scored run, and the newest run."""
    dirs = ctx.run_dirs()
    runs = {d.name for d in dirs if ctx.run_set(d.name, _doc(d / 'run.json')) == ctx.current_set}
    scored = _str(_doc(ctx.art / 'runs' / 'index.json').get('scored_run'))
    if scored:
        runs.add(scored)
    if dirs:
        runs.add(max(dirs, key=lambda d: _mtime(d / 'run.json') or 0.0).name)
    return runs


def rewind(ctx: Ctx, entries: list) -> Optional[str]:
    runs = rewind_runs(ctx)
    relevant = [e for e in entries if e.run is None or e.run in runs]
    if any(e.action == 'rerun' for e in relevant):
        return 'run'
    if any(e.row in ('D9', 'D10') for e in relevant) and not any(e.row in ('D7', 'D8') and e.action == 'rescore' for e in relevant):
        return 'fixes'
    return None


def report(ctx: Ctx, entries: list) -> dict:
    rows = {row.id: row for row in DEPENDENCIES}
    rebuild = [rows[r].artifact for r in ('D4', 'D5', 'D6') if any(e.row == r and e.action == 'rebuild' for e in entries)]
    pull = [{k: v for k, v in (('artifact', e.artifact), ('item', e.item)) if v is not None} for e in entries if e.action == 'pull']
    return {'schema': DEPS_SCHEMA, 'adopted': ctx.manifest.get('baseline') == 'adopted', 'stale': [e.public() for e in entries],
            'rebuild': rebuild, 'pull': pull, 'rewind': rewind(ctx, entries)}


# --------------------------------------------------------------------------- recording


def run_inputs(ctx: Ctx, run: str) -> dict:
    return {'harness': ctx.harness(), 'env': ctx.env(), 'corpus': ctx.corpus(), 'scenario_set': ctx.current_set, 'policies': ctx.policy_shas()}


def adopt(ctx: Ctx) -> None:
    """A workdir with no manifest: record today's coordinates for every existing run (it cannot know older ones)."""
    ctx.manifest.update(empty_manifest('adopted'))
    at = _now()
    for run_dir in ctx.run_dirs():
        inputs = run_inputs(ctx, run_dir.name)
        inputs.pop('scenario_set')  # producers stamp it; adopting today's set would bless an old-set run
        ctx.manifest['artifacts'][_run_key(run_dir.name)] = {'producer': 'adopted', 'adopted': True, 'recorded_at': at, 'inputs': inputs}
    if ctx.policies_path().is_file():
        observed = ctx.remote_eval(ctx.current_eval)
        ctx.manifest['artifacts']['policies_config.yaml'] = {'producer': 'adopted', 'adopted': True, 'recorded_at': at, 'inputs': {
            'eval_set': ctx.current_eval, 'eval_revision': observed[1] if observed else None, 'sha': ctx.policies_sha()}}


def _seed_remote(ctx: Ctx, ident: Optional[str], fields: dict, at: float) -> None:
    """Fill an EMPTY remote slot from what a producer observed; never overrides a refresh or a failure."""
    remote = ctx.manifest['current']['remote']
    if ident and ident not in remote and all(v is not None for v in fields.values()):
        remote[ident] = {**fields, 'fetched_at': _iso(at), 'source': 'run'}


def record_run(ctx: Ctx, run: str) -> Optional[str]:
    run_dir = ctx.art / 'runs' / run
    doc = _read_json(run_dir / 'run.json')
    if not isinstance(doc, dict):
        return f'{run_dir}/run.json is absent or unreadable'
    ctx.manifest['artifacts'][_run_key(run)] = {'producer': 'simulation-run', 'recorded_at': _now(), 'inputs': run_inputs(ctx, run)}
    at = _epoch(_doc(run_dir / 'run_summary.json').get('written_at')) or _mtime(run_dir / 'run.json') or 0.0
    integrity = doc.get('run_integrity') if isinstance(doc.get('run_integrity'), dict) else {}
    set_id = _str(doc.get('scenario_set_id')) or ctx.current_set
    _seed_remote(ctx, set_id, {'gt_revision': _int(doc.get('gt_revision'))}, at)
    _seed_remote(ctx, _str(doc.get('eval_set_id')), {'eval_revision': _str(integrity.get('eval_revision'))}, at)
    return None


def record_suite(ctx: Ctx) -> Optional[str]:
    suite = _read_json(ctx.art / 'suite.json')
    if not isinstance(suite, dict):
        return f'{ctx.art}/suite.json is absent or unreadable'
    ctx.manifest['artifacts']['artifacts/suite.json'] = {'producer': 'build_suite_json', 'recorded_at': _now(), 'inputs': {
        'scenario_set': suite.get('scenario_set_id'), 'eval_set': suite.get('eval_set_id'), **ctx.suite_inputs()}}
    return None


def record_renders(ctx: Ctx) -> None:
    ctx.manifest['artifacts']['artifacts/scenarios.md'] = {'producer': 'render_suite', 'recorded_at': _now(),
                                                           'inputs': {'suite_json': _sha_file(ctx.art / 'suite.json')}}


def record_pull(ctx: Ctx, what: str) -> Optional[str]:
    if what == 'policies':
        if not ctx.policies_path().is_file():
            return f'{ctx.policies_path()} is absent'
        observed = ctx.remote_eval(ctx.current_eval)
        inputs = {'eval_set': ctx.current_eval, 'eval_revision': observed[1] if observed else None, 'sha': ctx.policies_sha()}
        ctx.manifest['artifacts']['policies_config.yaml'] = {'producer': 'pull', 'recorded_at': _now(), 'inputs': inputs}
        return None
    observed = ctx.remote_set(ctx.current_set)
    ctx.manifest['artifacts']['artifacts/scenarios/'] = {'producer': 'pull', 'recorded_at': _now(), 'inputs': {
        'scenario_set': ctx.current_set, 'gt_revision': observed[1] if observed else None}}
    return None


# --------------------------------------------------------------------------- the backend (refresh, policies pull)


def server_url() -> str:
    """The REST base of the resolved backend, or ''."""
    return url_guard.server_url()


def http_get(url: str) -> tuple[bytes, dict]:
    """GET with the resolved bearer (never argv). Raises on any failure.

    The URL comes from this command's environment, which a command prefix sets; the bearer goes only to
    a backend the session was bound to (the session pin, hooks/session_pin.py), and a redirect is an
    answer, never a hop."""
    token = url_guard.bearer(url)
    if token and not url_guard.is_backend_url(url):
        raise urllib.error.URLError(url_guard.refused_message(url))
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'} if token else {})
    with _no_redirect.OPENER.open(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310 - checked against the pinned backend above
        return response.read(), {k.lower(): v for k, v in response.headers.items()}


def _meta(ident: str) -> dict:
    server = server_url()
    if not server:
        raise RuntimeError(url_guard.configuration_problem('artifact_deps'))
    body, _ = http_get(f'{server}/artifacts/{quote(ident)}')
    doc = json.loads(body)
    if not isinstance(doc, dict):
        raise ValueError('the artifact meta is not an object')
    return doc


def refresh(ctx: Ctx) -> list:
    """Two GETs (set meta, eval meta) into `current.remote`; a failure is recorded, so the axis reads unverified."""
    notes = []
    remote = ctx.manifest['current']['remote']
    for ident, read in ((ctx.current_set, _set_observation), (ctx.current_eval, _eval_observation)):
        if not ident:
            continue
        try:
            observed = read(_meta(ident))
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            observed, why = None, f'{type(exc).__name__}'
        else:
            why = 'the meta carries no revision'
        if observed is None:
            remote[ident] = {**(remote.get(ident) or {}), 'failed_at': _now()}
            notes.append(f'refresh of {ident} failed ({why}) — its axis reads unverified until a refresh succeeds')
        else:
            remote[ident] = {**observed, 'fetched_at': _now()}
    return notes


def _set_observation(meta: dict) -> Optional[dict]:
    detail = meta.get('detail') if isinstance(meta.get('detail'), dict) else {}
    gt = _int(detail.get('gt_revision'))
    if gt is None:
        return None
    log = edit_log_scenarios(detail.get('edit_log'))
    return {'gt_revision': gt, 'edit_log': log, 'edit_log_len': len(detail['edit_log']) if isinstance(detail.get('edit_log'), list) else None}


def _eval_observation(meta: dict) -> Optional[dict]:
    detail = meta.get('detail') if isinstance(meta.get('detail'), dict) else {}
    revision = _str(detail.get('eval_revision'))
    return {'eval_revision': revision} if revision else None


def pull_policies(ctx: Ctx) -> Optional[str]:
    if not ctx.current_eval:
        return 'no .eval_set_id'
    server = server_url()
    if not server:
        return url_guard.configuration_problem('artifact_deps')
    try:
        body, headers = http_get(f'{server}/artifacts/{quote(ctx.current_eval)}/files/policies_config.yaml')
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return f'policies GET failed ({type(exc).__name__})'
    if headers.get(POLICIES_STATE_HEADER.lower(), '').strip().lower() == 'draft':
        return 'an edit session holds a draft of the eval set — not pulled; end the session, then apply again'
    tmp = ctx.policies_path().with_name('.policies_config.yaml.tmp')
    tmp.write_bytes(body)
    os.replace(tmp, ctx.policies_path())
    return record_pull(ctx, 'policies')


def _report_pieces() -> Any:
    try:
        import report_pieces  # noqa: PLC0415 - sibling script, imported by path at use
    except ImportError:
        return None
    return report_pieces


def pull_scenarios(ctx: Ctx, slots: list) -> Optional[str]:
    pieces = _report_pieces()
    if pieces is None:
        return 'report_pieces.py is unavailable'
    if not ctx.current_set:
        return 'no .scenario_set_id'
    code = pieces.bundle(ctx.wd, ctx.current_set, tuple(slots))
    if code != 0:
        return f'report_pieces.bundle exited {code}'
    ctx.pieces = _doc(ctx.art / '.pieces' / 'pieces.json')
    return record_pull(ctx, 'scenarios')


# --------------------------------------------------------------------------- rebuilds


def run_command(argv: list) -> tuple[int, str]:
    done = subprocess.run(argv, capture_output=True, text=True, **utf8_child_text(), check=False)
    return done.returncode, (done.stderr or done.stdout or '').strip()[-400:]


def _suite_builder() -> Any:
    if str(SUITE_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SUITE_SCRIPTS))
    try:
        import build_suite_json  # noqa: PLC0415 - the builder's own final-build test, imported by path at use
    except ImportError:
        return None
    return build_suite_json


def final_suite_ready(ctx: Ctx, overlay: dict) -> bool:
    """Whether the final build would land: the saved overlay names no slot outside the current ones, and every slot
    has a title and a summary from the overlay or its scenario card (the builder's own exit-3 test, `untitled`).

    Without the builder importable, the overlay must author every slot."""
    authored = overlay.get('scenarios') if isinstance(overlay.get('scenarios'), dict) else {}
    slots = [d.name for d in ctx.slot_dirs()]
    if not set(authored) <= set(slots):
        return False
    builder = _suite_builder()
    if builder is None:
        return set(authored) == set(slots)
    return not builder.untitled(ctx.art, slots, authored)


def suite_command(ctx: Ctx) -> list:
    """The final build when the saved overlay and the slots' cards give every slot its title and summary, else a draft."""
    meta = ctx.art / '.pieces' / f'meta_{ctx.current_set}.json'
    overlay = ctx.art / '.pieces' / 'suite_overlay.json'
    script = [sys.executable, str(SUITE_SCRIPTS / 'build_suite_json.py'), '--art', str(ctx.art)]
    tail = ['--workdir', str(ctx.wd)]
    if overlay.is_file() and final_suite_ready(ctx, _doc(overlay)):
        # The same optional inputs the deliverable's final build passes, so a rebuild keeps the regression and held-back keys.
        extra = ['--bundle', str(ctx.art / 'final_bundle.json')] if (ctx.art / 'final_bundle.json').is_file() else []
        for name in ('suite_missing', 'suite_declined', 'suite_ungraded'):
            if (ctx.art / f'{name}.json').is_file():
                extra += ['--regressions', str(ctx.art / f'{name}.json')]
        return [*script, '--policies', str(ctx.policies_path()), '--overlay', str(overlay), *(['--meta', str(meta)] if meta.is_file() else []), *extra, *tail]
    policies = ['--policies', str(ctx.policies_path())] if ctx.policies_path().is_file() else []
    return [*script, '--draft', '--meta', str(meta), *policies, *tail]


REBUILDS: dict = {
    'D4': (suite_command, record_suite),
    'D5': (lambda ctx: [sys.executable, str(SUITE_SCRIPTS / 'render_suite.py'), str(ctx.art / 'suite.json'), '--artifacts-dir', str(ctx.art)], record_renders),
    'D6': (lambda ctx: [sys.executable, str(HERE / 'runs_index.py'), 'write', str(ctx.wd)], lambda ctx: None),
}


# --------------------------------------------------------------------------- the public API


def _context(workdir: Path) -> Ctx:
    manifest, why = read_manifest(workdir)
    ctx = Ctx(workdir, manifest if manifest is not None else empty_manifest('recorded'))
    if manifest is None and why is None:
        adopt(ctx)
    return ctx


def stale_items(workdir: Path) -> list:
    """Pure + local: `[{artifact, item?, reason, action}]`. A missing manifest is adopted in memory only."""
    ctx = _context(Path(workdir))
    return [e.public() for e in evaluate(ctx)]


def _final_set_ids(art: Path) -> set:
    """The scenario sets ``suite_state.json`` records as the suite's FINAL set (a landed ``final_scenarios`` phase)."""
    phases = _doc(art / 'suite_state.json').get('phases')
    return {
        str((p.get('ids') or {}).get('scenario_set'))
        for p in phases if isinstance(phases, list) and isinstance(p, dict)
        and p.get('phase') == 'final_scenarios' and p.get('status') == 'landed' and isinstance(p.get('ids'), dict)
    } if isinstance(phases, list) else set()


def draft_sections(workdir: Path) -> dict:
    """``{'suite': 'draft'}`` while ``suite.json`` is a draft, plus what the draft holds:
    ``suite.set`` = ``qualification`` (the small calibration set the quality loop runs) or ``final`` (the test set),
    and ``suite.checks`` = ``pending`` until the draft carries evals. Pure: reads, never writes."""
    art = Path(workdir) / 'artifacts'
    suite = _doc(art / 'suite.json')
    if suite.get('draft') is not True:
        return {}
    final = str(suite.get('scenario_set_id') or '') in _final_set_ids(art)
    evals = suite.get('evals')
    scenarios = suite.get('scenarios')
    return {'suite': 'draft', 'suite.set': 'final' if final else 'qualification',
            'suite.scenarios': str(len(scenarios) if isinstance(scenarios, list) else 0),
            'suite.checks': 'landed' if isinstance(evals, list) and evals else 'pending'}


def check(workdir: Path, do_refresh: bool = False) -> tuple[dict, Ctx, list]:
    ctx = _context(workdir)
    notes = refresh(ctx) if do_refresh else []
    return report(ctx, evaluate(ctx)), ctx, notes


def _fresh(ctx: Ctx) -> list:
    ctx.entries = []
    return evaluate(ctx)


def apply(workdir: Path, do_refresh: bool = False) -> tuple[dict, Ctx, list]:
    ctx = _context(workdir)
    notes = refresh(ctx) if do_refresh else []
    entries = evaluate(ctx)
    applied, errors = [], []
    scen_pulls = [e for e in entries if e.action == 'pull' and e.row in ('D1', 'D2')]
    if scen_pulls:
        whole = any(e.item is None for e in scen_pulls)
        slots = [] if whole else sorted({e.item for e in scen_pulls if e.item})
        error = pull_scenarios(ctx, slots)
        (errors if error else applied).append(error or f'pulled scenarios {"set" if whole else ", ".join(slots)}')
    if any(e.action == 'pull' and e.row == 'D3' for e in entries):
        error = pull_policies(ctx)
        (errors if error else applied).append(error or 'pulled policies_config.yaml')
    for row_id, (command, after) in REBUILDS.items():
        if not any(e.row == row_id and e.action == 'rebuild' for e in _fresh(ctx)):
            continue
        code, tail = run_command(command(ctx))
        if code == 0:
            after(ctx)
            applied.append(f'rebuilt {row_id}')
        else:
            errors.append(f'rebuild {row_id} exited {code}: {tail}')
    result = report(ctx, _fresh(ctx))
    return {**result, 'applied': applied, 'errors': errors}, ctx, notes


def save(ctx: Ctx) -> None:
    _write_json(ctx.wd / MANIFEST, ctx.manifest)


# --------------------------------------------------------------------------- CLI


def _writable(workdir: Path) -> Optional[str]:
    if not workdir.is_dir():
        return f'{workdir} is not a directory'
    _, why = read_manifest(workdir)
    return f'{why} — refusing to write over it; move it aside and run again' if why else None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    rec = sub.add_parser('record', help='record what one artifact was built from')
    rec.add_argument('workdir', type=Path)
    what = rec.add_mutually_exclusive_group(required=True)
    what.add_argument('--run', help='a run folder under artifacts/runs/')
    what.add_argument('--suite', action='store_true')
    what.add_argument('--pull', choices=('policies', 'scenarios'))
    chk = sub.add_parser('check', help='list every stale artifact and its action')
    chk.add_argument('workdir', type=Path)
    chk.add_argument('--refresh', action='store_true')
    chk.add_argument('--json', action='store_true')
    app = sub.add_parser('apply', help='perform every pull and local rebuild, then print the post-apply check')
    app.add_argument('workdir', type=Path)
    app.add_argument('--refresh', action='store_true')
    dec = sub.add_parser('declare', help='declare a change nothing on disk shows')
    dec.add_argument('workdir', type=Path)
    dec.add_argument('fact', choices=('env-edited',))
    args = parser.parse_args(argv)
    workdir = args.workdir

    if args.cmd == 'check':
        if not workdir.is_dir():
            print(f'artifact_deps: {workdir} is not a directory', file=sys.stderr)
            return EXIT_UNREADABLE
        _, why = read_manifest(workdir)
        if why:
            print(f'artifact_deps: {why} — nothing it records is trusted; every unrecorded axis reads unverified', file=sys.stderr)
        adopting = not (workdir / MANIFEST).exists()
        result, ctx, notes = check(workdir, args.refresh)
        # An adoption is persisted only once the workdir has an artifacts/ dir: creating it would move the
        # report's home (publish_report keys it on artifacts/ existing).
        if not why and (args.refresh or adopting) and ctx.art.is_dir():
            save(ctx)
        for note in notes:
            print(f'artifact_deps: {note}', file=sys.stderr)
        print(json.dumps(result, indent=2 if not args.json else None, sort_keys=True))
        return 0

    why = _writable(workdir)
    if why:
        print(f'artifact_deps: {why}', file=sys.stderr)
        return EXIT_UNREADABLE
    if args.cmd == 'apply':
        result, ctx, notes = apply(workdir, args.refresh)
        save(ctx)
        for note in notes + result['errors']:
            print(f'artifact_deps: {note}', file=sys.stderr)
        print(json.dumps(result, sort_keys=True))
        return 0
    ctx = _context(workdir)
    if args.cmd == 'declare':
        ctx.manifest['current']['env_epoch'] = (_int(ctx.manifest['current'].get('env_epoch')) or 0) + 1
        save(ctx)
        print(f'artifact_deps: env epoch {ctx.manifest["current"]["env_epoch"]} — every run recorded before it reads {R_ENV}')
        return 0
    error = record_run(ctx, args.run) if args.run else record_suite(ctx) if args.suite else record_pull(ctx, args.pull)
    if error:
        print(f'artifact_deps: {error}', file=sys.stderr)
        return EXIT_UNREADABLE
    save(ctx)
    print(f'artifact_deps: recorded {args.run or ("suite" if args.suite else args.pull)}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
