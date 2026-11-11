#!/usr/bin/env python3
"""Build a run's `run.json` summary from the per-scenario artifacts the runner wrote.

This is the aggregation `/simulation-run` Step 4b runs, in code rather than prose. Every
shape trap is enforced HERE, as a comment on the line that implements it — if you must hand-roll (no python3 on the box,
or probing a new artifact shape), read those comments, not your memory of them.

    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"   # $PLURAI_PY: the tool declaration is read through PyYAML
    "$PLURAI_PY" build_run_json.py <out_dir> --workdir <plurai_sim> --dataset-name X --eval-set-id Y \
        --gt-revision N --agent-version V --run-name R [--local-only] [--run-mode M] \
        [--expected-scenarios N] [--coverage-debt <file>] [--stage qualification|final|verify|repeat]

Reads each `<out_dir>/<scenario>/` — its verdicts from `verdicts.json` and nowhere else
(`scripts/verdicts_file.py`, docs/verdicts.md), what happened from transcript.json /
tool_events.json, remedy prose from error.json — and the runner's own roster
(`<out_dir>/run_summary.json`, which says how many scenarios there were SUPPOSED to be),
writes `<out_dir>/run.json` — the spec `render_run_report.py` renders. Stdlib-only, except
that the agent's TOOL DECLARATION (`<workdir>/plurai.config.yaml` `tools:`) is read through
pyyaml when it is importable — absent, the declaration is UNKNOWN, never guessed from silence.
Anything unknowable is omitted, never emitted blank — and anything MISSING is reported
rather than defaulted, because both read as a number the report never earned.

THE PRODUCER CONTRACT (`schema: plurai-run/1`). `stage` is the run's stage (`RUN_STAGES`), from `--stage`
or kept from the previous run.json on a rebuild, never inferred from a folder name; `scripts/runs_index.py`
indexes it and names the scored run. Every `evals[]` row carries `verdict_tier` / `tier_source` / `severity`
(:func:`eval_tier_fields`) — typed `null` / `unknown` / `null` when the eval set was not read, never guessed.
THE SCORES are the SDK's, never this file's: before anything is read, `plurai scores refresh` recomputes the run's
scores block (`run_summary.json` → `scores`, `plurai.scoring`) and each row's `result`, `score` (0-100) and
`fail_reasons` are its attempt's entry, copied; `totals` carries the block's `suite_score`, `agent_verdict` and
`scenarios_failed`, and `scores` the block verbatim. No weight, cutoff or pass rule lives in the plugin.
REGRESSIONS have exactly ONE source here: `regression_replays` (the SDK's replay block, copied) and the
`regressions[]` rows joined from it (:func:`regression_rows`). Nothing else in this file — not a flipped
cell, not a scenario that failed twice, not a folder name — makes or implies a regression; a run built
with no `--regression-replays` dir has no `regressions` key at all.

A PINNED RUN WHOSE FRAME CANNOT STAND IS REFUSED (exit 6). With `--workdir`, the eval lane's pin
(`scripts/lane_pin.py`) says which run dir its saved artifacts are a recording OF and in which
frame. When THIS out_dir is that recording and an axis the pin weights `REFUSING` has moved
since, the transcripts describe a world that no longer exists — a `simulate.py` that is gone
(`harness-fingerprint-changed`) or scenarios that are (`scenario-set-moved`). WHICH axes those
are is projected from `lane_pin.REFUSING_DETECTED_REASONS`, never listed here: a private list
would drift from the sidecar writer's. When the fingerprint cannot be computed
(`harness-fingerprint-unreadable`) or the pin itself cannot be read (`pin-unreadable`), the frame
cannot be verified at all. In every case `run.json`, `analytics.md` and `charts/` are REMOVED,
`run_stale.json` (written atomically) names the heaviest reason, every moved axis with both its
values under `moved`, and — when the harness axis is one of them — both fingerprints by name; and
every reader downstream — the render, the suite critic, the findings — has a typed finding instead
of evidence until the run is re-made. That is the mechanical form of "a critic never reads a run
scored under a superseded harness"; the skill step is the recipe form of the same rule.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import inspect
import io
import json
import math
import re
import shutil
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from types import SimpleNamespace
from typing import NamedTuple

try:
    import yaml
except ImportError:  # the declaration read is optional — see declared_tool_count
    yaml = None

# --- The backend's constants, READ rather than retyped ---------------------------------------------
#
# Three of the constants below are the backend's, not this file's: it re-scores the same saved
# artifacts this script aggregates, so a divergence means the two halves publish different
# tool-error counts over one run — and `dead_tools`, hence the whole outcome attribution, hangs
# off that count. Hand-mirrored copies drift, so each is read from the backend's contract.
#
# Each literal stays in place as the DOCUMENTED DEFAULT ON ABSENCE. That is what
# makes this safe against an older backend — boxes run pinned image digests, so a plugin talking
# to a backend that predates a block is a live deployment state, not a hypothetical — and
# `_report_contract_fallbacks()` in `main()` names every block that took its literal, so a
# fallback is never the silent hand-mirror wearing a new name.
_REPO_SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
if str(_REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_REPO_SCRIPTS))
try:
    import plurai_contracts as _contracts
except ImportError as _exc:  # this file COPIED out of the plugin — the docstring invites that
    _contracts = None
    _READER_ERROR: str | None = str(_exc)
else:
    _READER_ERROR = None
try:
    import operator_identity as _identity  # scripts/operator_identity.py: the plugin's own deterministic check
except ImportError:  # copied out of the plugin: the check is not run, and the build says so
    _identity = None
import verdicts_file  # noqa: E402 - scripts/verdicts_file.py: the one reader of verdicts.json, never optional
import _client_handshake  # noqa: E402 - scripts/_client_handshake.py: the plugin's refusal exit
import verdict_events  # noqa: E402 - scripts/verdict_events.py: the one route to the SDK's scores (`plurai scores refresh`)
try:
    from _stdio import utf8_child_text, utf8_stdio
except ImportError:  # a copy run outside the plugin decodes its children and its own streams as the platform does
    def utf8_child_text() -> dict:
        return {}

    def utf8_stdio() -> None:
        return None

#: The checks this build grades itself, beside the backend's cells: `{name: (run.json row key, evals[] tier fields)}`. Out of
#: the backend census (`_reconcile_cells`), and their `decided_scenarios` is read off the rows, not the SDK's trials block.
PLUGIN_CHECKS: dict[str, tuple[str, dict]] = {_identity.CHECK: (_identity.ROW_KEY, _identity.TIER_FIELDS)} if _identity else {}


def identity_cell_of(d: Path, transcript: Mapping, markers: list | None) -> tuple[str, str, dict, dict] | None:
    """`(check, row key, cell, row block)` of the operator-identity check on one scenario, or None when the run captured
    no markers. The cell grades the FIRST attempt, authoritative like every other cell of the row; the row block adds the
    kinds the runner's second attempt (`stability_retry/`) exposed, when it took one — `identity_marks` turns them into
    the check's reproduction mark."""
    if _identity is None or markers is None:
        return None
    cell = _identity.scenario_cell(d, transcript, markers)
    retry = _identity.retry_kinds(d, markers)
    return _identity.CHECK, _identity.ROW_KEY, cell, {'exposed': cell['kinds'], **({} if retry is None else {'retry_exposed': retry})}


def identity_marks(stability: Mapping, rows: list[dict]) -> None:
    """Put the operator-identity check's reproduction mark on each scenario's stability row whose per-policy marks the
    runner published and nothing withdrew, the way the runner marks every policy's red across its two attempts
    (`retry_only` is the red only the retry showed; the renderer appends it beside `failing_evals`)."""
    if _identity is None:
        return
    for row in rows:
        block, marks = row.get(_identity.ROW_KEY), stability.get(str(row.get('id')))
        # A cross-trial row (`selection: unconditional`) compares trials, not the two attempts read here.
        if (not isinstance(block, dict) or not isinstance(marks, dict) or not isinstance(marks.get('policy_reproduction'), dict)
                or marks.get('marks_withdrawn') or marks.get('marks_valid') is False or marks.get('selection') == 'unconditional'):
            continue
        if mark := _identity.reproduction(block.get('exposed') or [], block.get('retry_exposed')):
            marks['policy_reproduction'][_identity.CHECK] = mark

#: Blocks that could not even be looked up, because the reader itself is not importable.
_UNREAD_CONTRACTS: list[str] = []

#: run.json blocks whose value was NOT READ, as distinct from read-and-absent. Each entry is one
#: sentence naming the block and why. Reported by `_report_contract_fallbacks` at the end, in its
#: own note: these are not the backend constants and are not literal fallbacks.
_UNREAD_BLOCKS: list[str] = []


def _contract(name: str, default: object) -> object:
    """The backend's value for `name`, or `default`, the literal fallback when the contract cannot supply it."""
    if _contracts is None:
        _UNREAD_CONTRACTS.append(name)
        return default
    return _contracts.contract(name, default=default)


def _report_contract_fallbacks() -> None:
    """Say which the backend constants came from this file's own literals. Once, at the end."""
    if _UNREAD_CONTRACTS:
        print(
            f'NOTE(plurai-contracts): {_REPO_SCRIPTS / "plurai_contracts.py"} is not importable '
            f'({_READER_ERROR}), so {len(_UNREAD_CONTRACTS)} backend constant(s) came from this '
            f"file's own literals: {', '.join(sorted(_UNREAD_CONTRACTS))}. That is this script "
            'running outside its plugin checkout; the literals are the ones it shipped with, and '
            'nothing is comparing them to the backend.',
            file=sys.stderr,
        )
    if _UNREAD_BLOCKS:
        print(
            f'NOTE(unread): {len(_UNREAD_BLOCKS)} run.json block(s) were not read at all, so their '
            f'ABSENCE from run.json is not a statement about this run: '
            f'{"; ".join(sorted(_UNREAD_BLOCKS))}.',
            file=sys.stderr,
        )
    if _contracts is not None:
        _contracts.report_fallbacks()


def _read_json(path: Path) -> dict | None:
    """The file's parsed content, or None when it does not exist. A file that exists but
    does not parse RAISES — a corrupt artifact is a finding, not a value to default."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def scenario_dirs(out: Path) -> list[Path]:
    """The per-scenario dirs, by name. A dir counts when it holds any runner artifact —
    `charts/` and other renderer output live beside them and hold none."""
    names = ('transcript.json', 'tool_events.json', 'error.json', verdicts_file.FILENAME)
    return sorted(d for d in out.iterdir() if d.is_dir() and any((d / n).exists() for n in names))


def read_verdicts(d: Path) -> dict:
    """`d`'s verdicts (`verdicts_file.read`); a missing or unreadable file stops the build and names the converter."""
    try:
        return verdicts_file.read(d)
    except verdicts_file.VerdictsError as exc:
        raise SystemExit(str(exc)) from exc


def run_record(transcript: object) -> dict:
    """The transcript's run facts from `/finalize` (`finalize_record`: `dialog_outcome`, `user_sim_integrity`,
    `scenario_dimensions` …), `{}` when it carries none. Facts about the conversation, never verdicts."""
    record = transcript.get('finalize_record') if isinstance(transcript, dict) else None
    return dict(record) if isinstance(record, dict) else {}


def verdict_view(doc: Mapping, transcript: object) -> dict:
    """One attempt in the shape this file's readers take: the run facts (:func:`run_record`), the judging blocks,
    `scenario_verdict` (the file's `goal`) and `evaluation_results` (the file's cells) — all from `verdicts.json`
    but the run facts."""
    view = {**run_record(transcript), **verdicts_file.judging(doc), 'evaluation_results': verdicts_file.cells(doc)}
    if (goal := verdicts_file.goal(doc)) is not None:
        view['scenario_verdict'] = goal
    return view


def declared_scenarios(out: Path) -> tuple[int | None, set[str], bool | None]:
    """`(expected count, declared names, scored)` from the runner's own `run_summary.json`.

    Discovery below can only see scenarios that WROTE something, so a scenario that died
    before its first artifact is invisible to it — a 7-scenario run that lost 2 reports
    "5 scenarios, 5 passed", a clean 100% on a run that lost two. The runner writes this
    file beside the per-scenario dirs from the same counters its own exit code comes from,
    and clears a previous one at entry, so its `total` / `scenarios` are THIS run's roster.
    Best-effort on the runner's side, so absence is normal and simply leaves the shortfall
    unknown — `--expected-scenarios` is the manual override.
    """
    doc = _read_json(out / 'run_summary.json')
    if not isinstance(doc, dict):
        return None, set(), None
    declared = doc.get('scenarios')
    names = {str(k) for k in declared} if isinstance(declared, dict) else set()
    total = doc.get('total')
    # `scored` is the runner saying whether the evals RAN. An unscored run's artifacts carry
    # 0.0 everywhere meaning "not measured", which aggregates into a clean-looking report —
    # the runner says so and this is the only place that carries it through. None on an
    # older runner that never wrote the flag: unknown, not False.
    scored = doc.get('scored')
    return (total if isinstance(total, int) else (len(names) or None)), names, (scored if isinstance(scored, bool) else None)


def graded_scenarios(out: Path) -> set[str] | None:
    """WHICH rows produced a policy verdict, per the runner's own roster — or None when unstated.

    THE EVIDENCE BEHIND `scored`. The runner writes it so a partly-graded run is readable as such
    instead of collapsing to a bare `scored: false`; reading only the boolean would render
    "this run was NEVER SCORED — the verdicts below are placeholders" over a partly graded
    report. A genuinely partial run must read as "N of M graded", never as a run that measured
    nothing.

    None when the key is absent or unusable: an older runner did not say, and inferring an empty
    set from silence is the same mistake one level down.

    IT SAYS A ROW WAS GRADED, NEVER THAT ITS WORLD WAS SOUND. A row the backend scored over a
    world state it did not certify is graded and belongs here — the SDK keeps those cells, so such
    a row reports `scored: true` and appears on this roster. That fact has its OWN channel
    (`untrustworthy_scenarios`); this seam does not carry it.
    """
    doc = _read_json(out / 'run_summary.json')
    graded = doc.get('graded_scenarios') if isinstance(doc, dict) else None
    return {str(name) for name in graded} if isinstance(graded, list) else None


#: The named-risk kind for seeded starting rows whose landing the run could not measure.
UNOBSERVED_SEED_KIND = 'unobserved_seed'


def unobserved_seed_scenarios(out: Path) -> list[str]:
    """Scenarios that were handed seeded world rows (`injected_inputs.initial_world_state.entities`) and whose runner
    recorded NO post-seed read (`post_seed_world_state` present and null): a harness-owned store with no `world_snapshot`.
    The backend checks every seeded row landed against that read (`undelivered_seed`); without it nothing can be checked.
    A runner that records no such key (older) is silence, not a finding."""
    names = []
    for d in scenario_dirs(out):
        doc = _read_json(d / 'transcript.json')
        injected = doc.get('injected_inputs') if isinstance(doc, dict) else None
        if not isinstance(injected, dict) or 'post_seed_world_state' not in injected or injected['post_seed_world_state'] is not None:
            continue
        seed = injected.get('initial_world_state')
        if isinstance(seed, dict) and isinstance(seed.get('entities'), dict) and seed['entities']:
            names.append(d.name)
    return names


def untrustworthy_scenarios(out: Path) -> dict[str, str] | None:
    """`{scenario: the runner's stated reason}` for rows scored over a world state the backend did
    NOT certify — or None when the runner says nothing at all.

    A SCORED ROW, which is the whole difficulty. The backend sets `world_state_trustworthy: False` on
    a `status: completed` finalize with every cell populated (an uncorroborated proxy error, the
    zero-entity trust verdict) and grades it anyway, so the row reports `scored: true` and is on
    the `graded_scenarios` roster. Without reading the distrust map the report would never
    mention the world state: a silent downgrade on the one axis a reader cannot recover from the
    scores.

    None when the key is absent or unusable — an older runner did not say, and reading silence as
    "every world was trustworthy" is the inference `scored` and `graded_scenarios` already refuse
    one axis over. An EMPTY map is NOT silence: it is the runner saying it looked and found none,
    and it is published as such.

    The runner's VALUE shape is deliberately not pinned. A string rides through as the reason; any
    other shape names the row with no reason rather than being rendered as one — the map's KEYS
    are the finding, and a sentence invented here would read as evidence the runner never gave.
    """
    doc = _read_json(out / 'run_summary.json')
    rows = doc.get('untrustworthy_scenarios') if isinstance(doc, dict) else None
    if isinstance(rows, list):
        return {str(name): '' for name in rows}  # a roster that names rows and types no reason
    if not isinstance(rows, dict):
        return None
    return {str(name): (value.strip() if isinstance(value, str) else '') for name, value in rows.items()}


#: `verdict_corrections.RESTORED`: the `corrected` value of a set-aside verdict an applied backend overrule restored.
RESTORED_CORRECTION = 'restored'

#: What a cell a final-run correction moved says on the customer page, by its `corrected` value
#: (`scripts/verdict_corrections.py` writes it). Never the audit's own `reason`: that is the auditor's working note (its
#: voice, the trail's internal event ids, comparisons with other trials), kept as the correction's `reason` in the
#: `verdicts.json` history, the audit trail. A red's sentence names the rubric clause it breaks when the audit row cites one.
CORRECTION_REASONS = {
    'fail': 'On review of the full conversation, this breaks the rule',
    'pass': 'On review of the full conversation, the rule is not broken here.',
    'not_applicable': 'On review of the full conversation, this scenario could not measure the rule.',
}
#: The opener of an audit row's `owner_clause`: the verdict the rubric's clause states (`FAIL — <clause>`).
_CLAUSE_VERDICTS = ('FAIL', 'PASS', 'N/A', 'NOT APPLICABLE')


def correction_reason(corrected: str, clause: object = None) -> str:
    """:data:`CORRECTION_REASONS` for `corrected` (a red's naming `clause`, its leading verdict word dropped)."""
    said = CORRECTION_REASONS.get(corrected, CORRECTION_REASONS['pass'])
    if corrected != 'fail':
        return said
    text = ' '.join(str(clause or '').split())
    head, sep, rest = text.partition(' — ')
    text = rest.strip() if sep and head.strip().upper() in _CLAUSE_VERDICTS else text
    return f'{said}: {text.rstrip(".")}.' if text else f'{said}.'


def corrected_reds(dirs: Iterable[Path]) -> dict[str, int]:
    """`{policy: conversations whose red an audit correction cleared}` — off each attempt's `verdicts.json` `history`:
    a recorded flip carrying an audit `verdict` from `fail` to anything else, counted once the eval is no longer `fail`.
    A restored set-aside verdict (`new` back to `fail`) never clears a red: it was not one."""
    cleared: Counter[str] = Counter()
    for d in dirs:
        doc = verdicts_file.read(d)  # a VerdictsError (ValueError) propagates: release_verdict reports it and counts none
        states = {name: entry['state'] for name, entry in verdicts_file.evals(doc).items()}
        names = {str(c['eval']) for c in verdicts_file.changes(doc)
                 if c.get('verdict') and (c.get('old') or {}).get('state') == 'fail' and (c.get('new') or {}).get('state') != 'fail'}
        cleared.update(p for p in names if p in states and states[p] != 'fail')
    return dict(cleared)


#: Where a scenario's SECOND attempt keeps its artifacts — the SDK's
#: `plurai.run_artifacts.STABILITY_RETRY_DIRNAME`, named here because this reader must find the
#: retry's own `verdicts.json` and plurai is not a dependency of this script.
#:
#: NOT a scenario dir, and `scenario_dirs()` is why: it walks the TOP LEVEL only, so a retry's
#: transcript never enters the roster and its verdicts are never mistaken for a scenario's.
RETRY_DIRNAME = 'stability_retry'


# ONE BLOCK IN TWO PLACES. This plugin owns the zero-row classifier below; the SDK's
# `plurai/observability/rollup.py` carries the same bytes between the same markers, because the
# runner reads it to decide which ok tool calls came back as an EMPTY READ of a seeded world
# (its `world_unread_scenarios` channel of `measurement_clean`). A byte comparison on both sides
# fails when the two drift: change the block here, then copy it whole to the SDK; never edit one copy.
# --- BEGIN shared zero-result classifier (mirror) --------------------------------------------
#: Keys whose value ANNOTATES a result set rather than BEING one — the shapes the live mock
#: actually emits beside an empty list (a prose `note`, a `count: 0`, the echoed `query`).
#: Everything else populated is data. See `_zero_result`.
_ANNOTATION_KEYS = frozenset({
    'note', 'notes', 'message', 'msg', 'detail', 'details', 'reason', 'status', 'error', 'errors',
    'warning', 'warnings', 'query', 'filters', 'count', 'total', 'total_count', 'num_results',
    'page', 'offset', 'limit', 'cursor', 'next_cursor', 'has_more',
})


def _json_container(value: object) -> object:
    """`value` decoded when it is JSON text of a list or dict; anything else unchanged."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        if isinstance(decoded, (list, dict)):
            return decoded
    return value


def _zero_result(payload: object) -> bool:
    """True when a tool RESPONSE is an empty list/dict/zero-row shape — the call succeeded
    and returned nothing. The live mock's zero-row shape is a dict whose list-valued keys
    are all empty beside non-list metadata (`{"results": [], "note": "…"}`), so a dict
    counts when it HAS list values, every one of them is empty, and NOTHING ELSE in it
    carries data — never when it merely contains an empty list beside populated ones.

    That last clause is the guard: `{"warnings": [], "balance": 500}` is a complete answer
    that happens to carry one empty list, and counting it as a zero-row retrieval failure
    inflates the very number this counter exists to measure. A populated non-list value is
    DATA unless its key annotates the result set (`_ANNOTATION_KEYS`); an annotation key
    this set does not know therefore UNDERcounts, which is the safe direction for a headline.

    It judges what the agent was HANDED: a mock success envelope (`status: success` + `content`)
    is judged on its `content`, and counts outright when its runtime sibling (`_plurai_runtime`)
    says `status: not_found` — the tool's declared nothing-found answer, whatever its shape. A
    runtime latency `not_found` (`grounding: not_found_no_repair_budget`) counts too: the mock
    could not read the world either. An empty map from a success envelope is a write ACK, not an
    empty read. JSON text of a list or dict is decoded ONLY in what a success envelope hands over
    (its content, and each of that map's values, one level). Every other shape — bare, or an
    error envelope (`status: error`, `_plurai_handler_error`, `_plurai_unregistered_tool`) — is
    judged by the rule above exactly as before, so no error envelope becomes countable.
    """
    if (isinstance(payload, dict) and payload.get('status') == 'success' and 'content' in payload
            and '_plurai_handler_error' not in payload and '_plurai_unregistered_tool' not in payload):
        runtime = payload.get('_plurai_runtime')
        if isinstance(runtime, dict) and runtime.get('status') == 'not_found':
            return True
        payload = _json_container(payload['content'])
        if payload == {}:
            return False  # a write ACK — the success envelope handed back nothing to read
        if isinstance(payload, dict):
            payload = {k: _json_container(v) for k, v in payload.items()}
    if payload == [] or payload == {}:
        return True
    if not isinstance(payload, dict):
        return False
    lists = [v for v in payload.values() if isinstance(v, list)]
    if not lists or any(lists):   # no list at all, or one of them has rows — not a zero-row shape
        return False
    return all(not v or k in _ANNOTATION_KEYS for k, v in payload.items() if not isinstance(v, list))
# --- END shared zero-result classifier (mirror) ----------------------------------------------


def _dispatch_error(e: dict) -> bool:
    """True when a tool_call event's TOP-LEVEL `status` is an error — a FRAMEWORK/dispatch
    failure, the call itself never went through (the same filter the backend's own
    tool_call_error_rate uses; 'ok', or absent on old records, is not one).

    `_TRAIL_NOT_DISPATCHABLE_STATUSES` rides beside `error` because `tool_events.json` is NOT
    only what went through /mock/execute: the SDK merges the harness's own trail into it (the
    rows carry `_lane: 'trail'`) and passes the harness's `status` through verbatim, so the token
    reaches this predicate on the event lane too — on a recorded bench EVERY failing
    row arrives that way. A mock never mints that token, so widening here cannot reinterpret a
    /mock/execute row; it only stops one lane reading as a success what the other reads as a
    failure. `refused` is deliberately NOT added: it has never been a dispatch error HERE, it is
    on artifacts `score_run` replays, and this range may not change what it means.

    The `_not_dispatchable` marker counts the same way: the SDK keeps such a row's `status` at the
    abstaining `unknown` for grading and marks it instead, and the union drops the trail row that
    carried the raw token as a same-id duplicate, so the marker is all that is left to read.

    (Read at CALL time, so the constant's home stays beside the trail contract it belongs to.)"""
    status = str(e.get('status') or '').lower()
    return status == 'error' or status in _TRAIL_NOT_DISPATCHABLE_STATUSES or bool(e.get('_not_dispatchable'))


#: THE REFUSAL ENVELOPE CONTRACT — the shared vocabulary, byte-identical with the backend's
#: `simulation/tool_call_recovery.py` (`envelope_refused`). Two tiers, and the tiers are the
#: whole point:
#:
#: `error` is the token the env-generation recipe mandates, and it is read UNCONDITIONALLY.
#:
#: The synonyms below are what GENERATED MOCKS actually emit — on one demo-box run a refusal came
#: back `{"status": "failed", …}`, the transport stamped `status: ok`, run.json reported
#: `tool_refusals: 0`, and the one policy built to grade a refusal (guard
#: `len(failed_tool_calls()) > 0`) guard-skipped on the ONE scenario that exercised it. They are
#: also genuinely AMBIGUOUS: a polling tool SUCCEEDS while reporting a failed job
#: (`{"status": "failed", "job_id": "j-17", "finished_at": …}`), so a synonym counts only when
#: the SAME top-level object also names a blocker.
#:
#: `rejected` and `denied` are deliberately NOT here. At envelope top level beside a `reason`
#: they are the successful call reporting a business outcome ("card declined") for a payments /
#: moderation / approval agent — unlike the three above they have a common non-refusal reading —
#: and because `score_run` REPLAYS saved artifacts, admitting them would flip verdicts on runs
#: already on disk (their verbs would vanish from `operation_types()`).
#:
#: Standardizing the envelope at the generator is the source fix (on the backend side); this is
#: the consumer half and must not be dropped when that lands, because deployed mocks outlive the
#: recipe that wrote them.
_DRIFTED_REFUSAL_STATUSES = frozenset(
    _contract('drifted_refusal_statuses', default=('failed', 'failure', 'refused'))  # type: ignore[arg-type]
)

#: What turns a drifted status token into the CALL's verdict rather than a returned record's own
#: field. Read off the TOP-LEVEL object only — never a substring scan of the serialized blob,
#: which reads `{"status":"ok","jobs":[{"status":"failed"}],"message":"2 results"}` (a polling
#: agent's SUCCESS) as a refused call.
#:
#: `message` is NOT one of them: nearly every mock envelope carries a `message` on the happy path
#: too, so corroborating on it corroborates everything. The key must NAME a blocker.
_BLOCKER_KEYS = ('error', 'errors', 'error_message', 'error_code', 'reason', 'detail', 'details', 'code', 'status_code')


def _blocker_named(value: object) -> bool:
    """Whether a blocker-key value actually NAMES something — `{'error': None}` does not.

    Mocks routinely carry `error: null` / `error: ""` on success, so reading a blocker key by
    PRESENCE scores correct calls as refusals."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {'none', 'null', 'ok', 'success'}
    # A tuple, not `list | tuple | …`: the union form is a RUNTIME expression needing 3.10, and
    # this script runs on the CUSTOMER's `python3`, which the plugin does not choose. (the backend's
    # twin spells it as a union — same behaviour, and it runs on an interpreter we pin.)
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _refusal_envelope(raw: dict) -> bool:
    """True when a dispatched call's RESPONSE payload is refusal-shaped."""
    if raw.get('refused') is True:
        return True   # the typed spelling, for mocks that grow one
    status = str(raw.get('status') or '').strip().lower()
    if status == 'error':
        return True
    return status in _DRIFTED_REFUSAL_STATUSES and any(_blocker_named(raw.get(key)) for key in _BLOCKER_KEYS)


#: Where a harness whose agent runs its OWN tool loop reports the calls it made — a list of
#: `{tool, args, call_id, result, status}` entries on each turn's `agent_state` (a pipeline agent's
#: under `pipeline_io.outputs`, the same key), in execution order. Read from the backend's `agent_state_tool_trail` block (owner:
#: `simulation/tool_call_recovery.py:AGENT_STATE_TOOL_TRAIL_KEY`), and it is the ONLY channel
#: that topology has: a `mode: real`
#: dispatch writes no /mock/execute event, and a self-contained loop leaves no tool_use block in
#: the transcript either. Without this channel a self-contained loop's terminal failures publish as
#: `tool_errors: 0` here.
#: ONE read, because the backend serves the key and its failed-status vocabulary as one block —
#: they are the two halves of "how a self-reported trail entry is interpreted" and a consumer
#: that took one from the backend and the other from memory would be half-mirrored, which is worse
#: than either.
_TOOL_TRAIL_CONTRACT: dict = _contract(  # type: ignore[assignment]
    'agent_state_tool_trail',
    default={'key': 'tool_trail', 'failed_statuses': sorted({'error', 'unavailable'} | _DRIFTED_REFUSAL_STATUSES)},
)
_AGENT_STATE_TOOL_TRAIL_KEY = str(_TOOL_TRAIL_CONTRACT['key'])

#: The THIRD dispatch outcome: the name was never dispatchable at all. The trail's `status`
#: otherwise carries two values (`ok` / `error`) for a three-valued fact — dispatched-ok,
#: dispatched-and-failed, and refused-by-name — so a harness gating a verb the flow does not
#: expose could only stamp `error`, and `dead_tools` would read invented verbs as a dead environment.
#:
#: THE TOKEN IS A LABEL, NOT AN EXEMPTION. It says WHOSE the failure is; it does not say the call
#: succeeded. Answering `status: 'ok'` for it would put the row in `calls` and in neither
#: `errors` nor `dead_tools`/`unproven` — and `invented_verbs` is computed from
#: `dead | unproven`, so the one signal the token exists to produce could never fire on a
#: harness that emitted it. The exculpation is `declared_surface`'s job and only its job: a name
#: absent from `tools:` UNION `render_actions:` is subtracted from `dead` before the attribution,
#: which is a typed declaration rather than the harness's own account of itself.
#:
#: `refused` is deliberately NOT here. It is in the vendored `failed_statuses` contract and
#: deployed harnesses stamp it with that meaning; admitting it to this set would RE-READ a token
#: in the field, with no version guard, on runs already on disk. This token gets the new
#: reading; `refused` keeps its existing one.
#:
#: NOT `unknown_tool` either: that token already means an ORPHAN TOOL RESULT on both sides of this
#: seam (SDK `plurai/observability/tool_call_recovery.py`, the backend `simulation/tool_call_recovery.py`),
#: and reusing it would make one word mean two unrelated things in one pipeline.
#:
#: The reader accepts the token ahead of any harness that emits it (the writer half is the SDK
#: trail contract + the simulate.py template): a reader that lags its producer misreads the very
#: runs the producer exists for.
_TRAIL_NOT_DISPATCHABLE_STATUSES = frozenset({'not_dispatchable'})

#: A trail entry's own `status`, when it names a FAILED dispatch. Wider than
#: `_refusal_envelope` allows on a RESPONSE, and the difference is whose field it is: there the
#: token sits on a payload the tool returned, where `failed` is a job poll's legitimate answer;
#: here the harness stamped it on the CALL RECORD, about the dispatch. `unavailable` is the
#: harness gate refusing a call before it left — and `not_dispatchable` is the same gate refusing
#: it for the other reason, so it joins the set rather than escaping it. UNION, never a
#: replacement: the vendored contract stays the source for the tokens it names.
_TRAIL_FAILED_STATUSES = frozenset(_TOOL_TRAIL_CONTRACT['failed_statuses']) | _TRAIL_NOT_DISPATCHABLE_STATUSES

#: TRANSPORT failures rendered as PROSE where the data belongs — the shape a self-reported trail
#: actually carries ("The response from the database is csv:http://127.0.0.1:8399: fetch failed").
#: `_dispatch_error` and `_refusal_envelope` structurally cannot see it: it is not an object
#: blob, and the entry's own `status` says the call returned.
#:
#: Every phrase names a failure to REACH the tool, which is why widening to it is safe where the
#: envelope contract refuses `rejected` / `denied` / a poll's `status: failed`: those have a
#: successful business reading and a connection that was never made does not. Read on the TRAIL
#: lane only.
#:
#: ANCHORED TO THE END, which is the rule that keeps it honest. The backend's own rationale: the engine substitutes its error text FOR the data, so the
#: phrase TERMINATES the content; a tool that legitimately RETURNS text about a network problem
#: ("the customer said the connection failed at their end and we reissued the invoice") keeps
#: talking afterwards. Without the anchor that text would score as a failed retrieval — and an
#: over-counted error is not a symmetrical mistake here: it "feeds `dead_tools`, which can label a
#: whole flow family environment-zeroed".
#:
#: What a missing anchor would cost on THIS side, end to end: a support/CRM agent whose `get_ticket`
#: returns customer prose would get both trail entries mislabelled `error`, so
#: `by_tool['get_ticket'] = {calls: 2, errors: 2}`, `dead_tools` names it, `outcome_attribution`
#: returns `environment`, and the customer-facing report publishes "N flow families were zeroed by
#: the environment, not by the agent" — EXCULPATING a real agent failure. The price of the anchor
#: is that a trailing fragment hides a real one ("request timed out after 30s"), which is the
#: direction this file leans everywhere: an UNDERcount is the safe side of a headline,
#: and the harness's own status stamp (`_TRAIL_FAILED_STATUSES`) is the reliable channel for
#: anything the prose misses.
#:
#: The backend OWNS this pattern; the literal below is only the fallback for a payload that does not
#: carry the block. It is read from the vendored
#: `transport_failure_pattern` block, and a parity check against the backend fails when the literal
#: diverges from it (the end anchor `\s*[.!]?\s*$` is part of the pattern).
_TRANSPORT_FAILURE_LITERAL = (
    r'(?:'
    r'\b(?:fetch|request|connection|connect|socket|lookup|dns)\s+(?:failed|error|refused|reset|timed\s+out)'
    r'|\b(?:econnrefused|econnreset|enotfound|etimedout|ehostunreach|epipe)'
    r'|\b(?:service\s+unavailable|bad\s+gateway|gateway\s+time-?out|internal\s+server\s+error)'
    r')\s*[.!]?\s*$'
)
#: FLAGS travel with the pattern. A client that rebuilds the regex and forgets `IGNORECASE` gets
#: a matcher that reads only lowercase prose — a difference that shows up as an error quietly
#: never being counted, not as an error.
_TRANSPORT_FAILURE_CONTRACT: dict = _contract(  # type: ignore[assignment]
    'transport_failure_pattern',
    default={'pattern': _TRANSPORT_FAILURE_LITERAL, 'flags': re.IGNORECASE},
)
try:
    _TRANSPORT_FAILURE_RE = re.compile(
        str(_TRANSPORT_FAILURE_CONTRACT['pattern']), int(_TRANSPORT_FAILURE_CONTRACT.get('flags', re.IGNORECASE))
    )
except (KeyError, TypeError, ValueError, re.error) as _pattern_error:
    # A vendored pattern that does not COMPILE would take the whole aggregation down after a run
    # that already cost real credits. Fall back to the shipped literal and say so — loudly, and
    # in the same channel as every other fallback, so it cannot read as "nothing to report".
    print(
        f'NOTE(plurai-contracts): the vendored transport_failure_pattern is not usable '
        f'({_pattern_error!r}) — using this file\'s own literal. Update '
        'the plugin to refresh the vendored contracts.',
        file=sys.stderr,
    )
    _TRANSPORT_FAILURE_RE = re.compile(_TRANSPORT_FAILURE_LITERAL, re.IGNORECASE)


#: Share of a trail row's prose that must be LATIN letters before the transport vocabulary above
#: is treated as having HAD A CHANCE at it. Same structural test, same floor, and the same
#: reasoning as `skills/generate-evals/scripts/verify_policies.py:_reads_as_latin_script`: a
#: SCRIPT question, derived from the UCD, never a table of languages — it needs no lexicon and it
#: cannot itself go dark on a script nobody listed.
_LATIN_SHARE_FLOOR = 0.5


@lru_cache(maxsize=None)
def _is_latin_letter(ch: str) -> bool:
    """Is this character a letter of the LATIN script? Derived from the UCD, never a table.

    `ch.isascii()` answers a CODEPAGE question and is wrong in both directions: `añadió` and
    `Gürültü` are Latin prose an English-keyed regex can sit beside, while ASCII carries every
    non-Latin harness's scaffolding — tool ids, `status`, JSON punctuation.
    """
    try:
        return unicodedata.name(ch).startswith('LATIN ')
    except ValueError:
        return False  # unnamed in the UCD; not a letter this claims to read


def _reads_as_latin_script(text: str) -> bool:
    """Could `_TRANSPORT_FAILURE_RE` have read this text at all?

    Letterless text answers True — there is nothing to read and that is not a script finding.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    return sum(_is_latin_letter(ch) for ch in letters) / len(letters) >= _LATIN_SHARE_FLOOR


def _trail_dispatch_unreadable(entry: dict) -> bool:
    """True when NOTHING decided this trail row's dispatch outcome.

    `_trail_dispatch_errored` has exactly two channels: the harness's own status stamp, and an
    English transport-failure regex. A row that carries NO stamp at all and answers in a script
    that regex structurally cannot read therefore scored `errors: 0` — indistinguishable, in
    every number this script publishes, from a tool that worked. That is the shape a customer
    running a Japanese or a Thai agent gets, and it is
    the one direction an undercount is NOT safe in: `dead_tools` reads errors-over-calls, so a
    tool that failed every dispatch in Japanese reads as healthy and its family's zero is charged
    to the agent.

    THE FIX IS NOT A WIDER REGEX. Adding failure vocabulary per language is a table that goes
    dark on the next language, and it would have to be maintained in three repos. The honest
    reading is that the outcome is UNCLASSIFIED: not counted as an error (nothing established
    one), not counted as clean (nothing established that either), and REPORTED, so a reader
    learns the counters could not see this run rather than reading their silence as health.

    Narrow on purpose — it requires the stamp to be ABSENT, not merely non-failing. A row stamped
    `returned` is a claim the harness made and this script's job is not to second-guess it in
    one script and not another; flagging every stamped row would put this note on every call of
    every non-English run, which is the noise that makes the real ones get skimmed past.
    """
    if str(entry.get('status') or '').strip():
        return False
    raw = entry.get('result')
    if raw is None:
        return False  # no prose at all is a different gap, and the trail contract already has it
    text = raw if isinstance(raw, str) else str(raw)
    return bool(text.strip()) and not _reads_as_latin_script(text)


def _trail_dispatch_errored(entry: dict) -> bool:
    """True when a self-reported trail entry records a failed DISPATCH — the harness's own status
    stamp, or the transport-failure prose that stamp structurally cannot carry.

    Deliberately NOT the response envelope, even though this function could read it. `tool_stats`
    keeps a dispatch failure apart from a tool that dispatched fine and answered with an error-shaped payload, and
    the trail record carries its payload through as `response`, so the envelope branch there reads it exactly as it
    does an event's.
    Folding it in here would do more than mislabel: `dead_tools` reads errors-over-calls, so a
    `mode: real` tool that correctly refused every call in a run would read as DEAD and attribute
    a whole flow family to the environment on the strength of the agent's own refusal handling.

    A `not_dispatchable` stamp IS one of these. Nothing was reached, which is the same thing
    `unavailable` already says, and the row must not read as a call that worked: `dead_tools` is
    errors-over-calls and `invented_verbs` is computed off its output, so exempting the stamp here
    silences the very finding the stamp was minted to raise. What the stamp buys is
    `_trail_not_dispatchable`, which says whose the failure is; who is EXCULPATED is settled
    against `declared_surface`, one layer up."""
    status = str(entry.get('status') or '').strip().lower()
    if status in _TRAIL_FAILED_STATUSES:
        return True
    raw = entry.get('result')
    return raw is not None and _TRANSPORT_FAILURE_RE.search(raw if isinstance(raw, str) else str(raw)) is not None


def _trail_not_dispatchable(entry: dict) -> bool:
    """True when the trail says the CALL NEVER LEFT because the name is not one this agent has.

    A NARROWING of `_trail_dispatch_errored`, never an alternative to it: every row this answers
    True for is also a failed dispatch. It names WHOSE the failure is — the agent asked for a verb
    its flow does not expose — which is what lets `tool_stats` say so on a run whose declaration is
    unknown and `invented_verbs` therefore cannot speak."""
    return str(entry.get('status') or '').strip().lower() in _TRAIL_NOT_DISPATCHABLE_STATUSES


#: The ENVIRONMENT's own typed tags on a call it did not answer. The mock engine (plurai-mockenv
#: `_engine.py`) stamps a handler that RAISED, a write it refused and a tool with no handler; the backend's
#: runtime library (`env_runtime/grounded.py`) RETURNS `_plurai_runtime_fault` when a grounded or
#: runtime tool faults; the SDK child answers `_plurai_stub_error` in place of a mock it could not reach.
#: A handler that RETURNED an `error` envelope carries none of them.
_ENV_UNANSWERED_MARKERS = (
    '_plurai_handler_error', '_plurai_unregistered_tool', '_plurai_write_refused', '_plurai_runtime_fault', '_plurai_stub_error',
)

#: The engine's typed fact that the AGENT's arguments failed the tool's declared input schema (the event's
#: `shape`, stamped before the handler runs). The only fact that charges a failed call to the agent: an
#: error envelope alone does not, because a generated handler returns the same envelope from its
#: catch-all crash branch as from a refused argument.
_ENGINE_INVALID_ARGS_SHAPE = 'dict_validation_failed'

#: Trail stamps that say the call never reached a tool: the harness gate refused it before it left.
_TRAIL_UNANSWERED_STATUSES = frozenset({'unavailable'}) | _TRAIL_NOT_DISPATCHABLE_STATUSES

#: Where an error envelope carries its prose. Read STRUCTURALLY — the transport regex is anchored to the
#: end of the text, and `str()` of a dict always ends in `}`.
_ENVELOPE_PROSE_KEYS = ('error', 'error_message', 'message', 'detail', 'details', 'reason')


def _transport_failed(raw: object) -> bool:
    """Transport-failure prose where the data belongs: the text itself, or an envelope's own prose field."""
    if isinstance(raw, str):
        return _TRANSPORT_FAILURE_RE.search(raw) is not None
    if isinstance(raw, dict):
        return any(isinstance(raw.get(key), str) and _TRANSPORT_FAILURE_RE.search(raw[key]) is not None for key in _ENVELOPE_PROSE_KEYS)
    return False


def _trail_unanswered(entry: dict) -> bool:
    """A trail entry's typed fact that the environment did not ANSWER: an `unavailable` /
    `not_dispatchable` stamp, or transport-failure prose where the data belongs. A bare `error`
    stamp is a failed call with no owner named, and is not one."""
    if str(entry.get('status') or '').strip().lower() in _TRAIL_UNANSWERED_STATUSES:
        return True
    return _transport_failed(_parsed(entry.get('result')))


def _from_trail(e: dict) -> bool:
    """Whether a call record came from the harness's self-reported trail — this script's recovery
    (`_recovered`) or the SDK's merge (`_lane: trail`) — rather than the mock's own event log."""
    return bool(e.get('_recovered')) or e.get('_lane') == 'trail'


def _parsed(raw: object) -> object:
    """A JSON string parsed; prose is returned as it came."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _call_response(e: dict) -> object:
    """A call record's response, a JSON string parsed — prose is returned as it came."""
    return _parsed(e.get('response') if e.get('response') is not None else e.get('result'))


def _env_unanswered(e: dict) -> bool:
    """True when a failed dispatch carries the ENVIRONMENT's typed fact that it did not answer.

    OWNER BY FACT, never by `status`: `_dispatch_error` answers "did the call succeed" — the question
    `tool_errors` and the backend's `tool_call_error_rate` ask. `dead_tools` asks "did the environment
    answer", and only an environment marker, a harness stamp or transport prose says it did not."""
    if not _dispatch_error(e):
        return False
    if e.get('_unanswered') or e.get('_not_dispatchable'):
        return True
    raw = _call_response(e)
    if isinstance(raw, dict) and any(raw.get(marker) for marker in _ENV_UNANSWERED_MARKERS):
        return True
    return _from_trail(e) and _transport_failed(raw)


def _agent_invalid_args(e: dict) -> bool:
    """A failed dispatch the mock engine typed as the AGENT's: its arguments failed the tool's declared
    input schema. Nothing else — an error envelope, a trail dict — carries that fact."""
    return _dispatch_error(e) and not _from_trail(e) and e.get('shape') == _ENGINE_INVALID_ARGS_SHAPE and not _env_unanswered(e)


def _carry_unanswered(calls: list[dict], trail: list[dict]) -> list[dict]:
    """The trail's typed "never answered" stamp, carried onto the row the union KEPT for the same call.

    The SDK merges the harness trail into `tool_events.json` and rewrites an `unavailable` stamp to
    `status: error`, so when the union keeps its row over this script's own trail row (same tool, same
    id) the stamp is gone. Read here, off the trail, before it can decide an owner — and only onto a
    TRAIL row: a call the engine logged answered for itself, and a harness stamp does not overwrite that."""
    stamped = {(str(r.get('tool')), cid) for r in trail if r.get('_unanswered') and (cid := _call_identity(r))}
    if not stamped:
        return calls
    return [
        dict(c, _unanswered=True) if _from_trail(c) and not c.get('_unanswered') and (str(c.get('tool')), _call_identity(c)) in stamped else c
        for c in calls
    ]


#: The turn a PIPELINE transcript's trail is read at: it has no dialog turns, and its whole run is one exchange
#: (`red_cells.PIPELINE_TURN`, the turn its evidence is read at).
PIPELINE_TRAIL_TURN = 0


#: A refused ATTEMPT: the SDK's `tool_call.refusal` — `{by, reason}`, `by` one of these — on a call a gate
#: said no to for a permission, approval or policy reason (plurai `_protocol.TRAIL_REFUSAL_KEY` / `REFUSED_BY`). It is
#: something the agent TRIED, never a broken tool: the record keeps the `_denied` shape (`status: 'unknown'`).
_REFUSAL_KEY = 'refusal'
_REFUSED_BY = frozenset({'mock', 'harness', 'confinement', 'user'})
_TRAIL_DENIED_STATUS = 'denied'


def _refusal_text(value: object) -> str:
    return value if isinstance(value, str) else '' if value is None else json.dumps(value, sort_keys=True, ensure_ascii=False)


def _trail_refusal(entry: Mapping) -> dict | None:
    """The refusal one trail row records, as the SDK reads it: a declared `refusal`, else the harness's own `denied`
    stamp, else a mock envelope's top-level `refusal` sibling in its `result`. None when nothing typed says no."""
    declared = entry.get(_REFUSAL_KEY)
    if isinstance(declared, Mapping):
        by = str(declared.get('by') or '').strip().lower()
        reason = declared.get('reason')
        return {'by': by if by in _REFUSED_BY else 'harness', 'reason': _refusal_text(reason if reason not in (None, '') else entry.get('result'))}
    if str(entry.get('status') or '').strip().lower() == _TRAIL_DENIED_STATUS:
        return {'by': 'harness', 'reason': _refusal_text(entry.get('result'))}
    result = entry.get('result')
    sibling = result.get(_REFUSAL_KEY) if isinstance(result, Mapping) else None
    if isinstance(sibling, Mapping):
        return {'by': 'mock', 'reason': _refusal_text(sibling.get('reason'))}
    return None


def refused_attempt(e: Mapping) -> dict | None:
    """`{tool, turn, args, by, reason}` of a call record a gate refused (its typed `refusal`), else None."""
    refusal = e.get(_REFUSAL_KEY)
    if not isinstance(refusal, Mapping) or not refusal.get('by'):
        return None
    return {'tool': str(e.get('tool') or '?'), 'turn': e.get('turn'), 'args': e.get('args') or {},
            'by': str(refusal['by']), 'reason': _refusal_text(refusal.get('reason'))}


def _trail_record(entry: object, turn: int) -> dict | None:
    """One trail row as a `tool_call` record (see :func:`trail_calls`), or None for a row that names no tool."""
    if not isinstance(entry, dict) or not isinstance(entry.get('tool'), str) or not entry.get('tool'):
        return None
    record = {'type': 'tool_call', 'tool': entry['tool'], 'args': entry.get('args') or {},
              'response': entry.get('result'), 'turn': turn, '_recovered': True,
              'status': 'error' if _trail_dispatch_errored(entry) else 'ok'}
    # A refused attempt (a declared refusal, a `denied` stamp, a mock's refusal sibling) wins over the status beside it:
    # the agent tried it and a gate said no, which is neither a failed dispatch nor a clean call.
    if refusal := _trail_refusal(entry):
        record.update(status='unknown', _denied=True, refusal=refusal)
    # `status: 'ok'` above is what the union and the counters need (this row is not an
    # error), and on an unreadable row it is not a finding — nothing looked. The marker
    # keeps that distinction alive for the one consumer that must not lose it: the
    # report, which otherwise publishes the silence as health.
    if _trail_dispatch_unreadable(entry):
        record['_dispatch_unreadable'] = True
    # …and the OTHER thing a status stamp can say about a failed dispatch: the name was
    # never one this agent has. It rides as a MARKER beside `status: 'error'`, not instead
    # of it — the counters must see the failure — so that `tool_stats` can name these rows
    # for what they are on a run whose declaration is unknown.
    if _trail_not_dispatchable(entry):
        record['_not_dispatchable'] = True
    # …and whether the stamp (or the prose) says the call was never ANSWERED, which the
    # rewritten `status` above does not carry (`_env_unanswered`).
    if record['status'] == 'error' and _trail_unanswered(entry):
        record['_unanswered'] = True
    # The harness's OWN id for the dispatch, carried through unchanged — it is what lets
    # the union below tell a duplicate of a logged call from a retry of one. Omitted when
    # the harness declares none, never synthesised: an id minted here matches nothing on
    # the other lane, so it would read as a distinct call and inflate the count. Read from
    # `call_id` ONLY — the field the trail contract writes and the field the SDK and backend
    # twins' trail lane reads (`str(entry.get('call_id') or '')`); reading `id` here would
    # hand this lane an identity the twins never see, and the three copies of the union
    # would count the same run differently.
    raw_id = entry.get('call_id')
    if isinstance(raw_id, str) and raw_id.strip():
        record['call_id'] = raw_id.strip()
    return record


def _pipeline_trail(doc: Mapping, seen: Sequence[dict]) -> list[dict]:
    """The calls a PIPELINE agent reported, from `pipeline_io.outputs.<trail key>`, read at :data:`PIPELINE_TRAIL_TURN`.

    The same rows the dialog path carries per turn (C6). A row whose `call_id` the turns' trail (`seen`) already carries
    is that call, not a second one; beside a turn trail an id-less pipeline row cannot be told from one of its rows, so
    it is not read."""
    io = doc.get('pipeline_io')
    outputs = io.get('outputs') if isinstance(io, Mapping) else None
    rows = outputs.get(_AGENT_STATE_TOOL_TRAIL_KEY) if isinstance(outputs, Mapping) else None
    if not isinstance(rows, list):
        return []
    ids = {r['call_id'] for r in seen if r.get('call_id')}
    out: list[dict] = []
    for entry in rows:
        record = _trail_record(entry, PIPELINE_TRAIL_TURN)
        if record is None or ((record['call_id'] in ids) if record.get('call_id') else bool(seen)):
            continue
        out.append(record)
    return out


def trail_calls(d: Path) -> list[dict]:
    """`tool_call`-shaped records from the transcript's trail: each turn's `agent_state.tool_trail`, then a pipeline
    agent's `pipeline_io.outputs.tool_trail` (:func:`_pipeline_trail`).

    Shaped like a `tool_events.events` entry so `tool_stats` can union them in and apply one set
    of rules: `status` is resolved to the DISPATCH verdict here (`_trail_dispatch_errored` — a
    harness stamp, or transport prose the stamp cannot carry), the result rides through as
    `response` so the refusal branch can read the envelope for itself, and `_recovered` is
    stamped because the record did not come through /mock/execute.

    Missing or oddly-shaped input yields [] — this is an enrichment, and a report about a run must
    never BE the run's failure. A CORRUPT transcript still raises, as everywhere else here: the
    main loop parses the same file and would raise on it too."""
    doc = _read_json(d / 'transcript.json')
    if not isinstance(doc, dict):
        return []
    turns = doc.get('turns')
    out: list[dict] = []
    for idx, turn in enumerate(turns if isinstance(turns, list) else []):
        state = turn.get('agent_state') if isinstance(turn, dict) else None
        trail = state.get(_AGENT_STATE_TOOL_TRAIL_KEY) if isinstance(state, dict) else None
        if not isinstance(trail, list):
            continue
        out.extend(record for entry in trail if (record := _trail_record(entry, idx)) is not None)
    return [*out, *_pipeline_trail(doc, out)]


#: The backend's closed `tools.<name>.observation_lane` vocabulary and, per lane, whether zero recorded calls is evidence
#: the tool was not called (`observation_lanes`). The lanes read off the agent's trail rather than the mock's events.
_OBSERVATION_LANES: dict = _contract(  # type: ignore[assignment]
    'observation_lanes', default={'absence_is_evidence': {'none': False, 'tool_events': True, 'tool_trail': True, 'tool_trail_cited': False}})
_TRAIL_LANES = ('tool_trail', 'tool_trail_cited')
#: The `measurement_unclean_because` channel when the config declares a tool on a trail lane whose absence is evidence
#: and no item of the run carries the trail at all: every such tool reads "never called", unmeasured.
DECLARED_LANE_UNREAD_CHANNEL = 'declared_lane_unread'


def declared_trail_tools(workdir: str | None) -> list[str]:
    """The tools `plurai.config.yaml` declares on a trail lane whose absence is evidence (`observation_lane`)."""
    tools = (_config_document(workdir)[0] or {}).get('tools')
    evidence = (_OBSERVATION_LANES or {}).get('absence_is_evidence') or {}
    specs: Iterable[tuple[object, object]] = ()
    if isinstance(tools, dict):
        specs = tools.items()
    elif isinstance(tools, list):
        specs = ((t.get('name'), t) for t in tools if isinstance(t, dict))
    return sorted(str(name) for name, spec in specs
                  if isinstance(spec, dict) and spec.get('observation_lane') in _TRAIL_LANES and evidence.get(spec['observation_lane']))


def carries_trail(d: Path) -> bool:
    """Whether the item's transcript carries the trail key at all (a turn's `agent_state`, or a pipeline's outputs) —
    an empty trail is a measured zero; no key is a lane the harness never wired."""
    doc = _read_json(d / 'transcript.json')
    if not isinstance(doc, dict):
        return False
    io = doc.get('pipeline_io')
    outputs = io.get('outputs') if isinstance(io, Mapping) else None
    if isinstance(outputs, Mapping) and _AGENT_STATE_TOOL_TRAIL_KEY in outputs:
        return True
    return any(isinstance(t, dict) and isinstance(t.get('agent_state'), dict) and _AGENT_STATE_TOOL_TRAIL_KEY in t['agent_state']
               for t in doc.get('turns') or [])


def declared_lane_unread(dirs: Sequence[Path], workdir: str | None) -> list[str]:
    """The trail-lane tools (:func:`declared_trail_tools`) this run could not observe: no readable item carries the
    trail key. `[]` when none is declared, or any item carries it."""
    declared = declared_trail_tools(workdir)
    readable = [d for d in dirs if (d / 'transcript.json').is_file()]
    return declared if declared and readable and not any(carries_trail(d) for d in readable) else []


def tool_trail_results_missing(trail_results: Mapping[str, tuple[int, int]]) -> dict | None:
    """``{entries, scenarios}`` when the run's trail carries a dispatch that did not fail and EVERY such entry lacks a
    ``result`` — the harness never records what its tools returned — else ``None``.

    Run-wide and all-or-nothing: a tool that legitimately returned nothing leaves some entries null on a correct
    harness, while a wiring that drops every result leaves none. Error entries are excluded — a failed dispatch has
    no result to record.
    """
    returned = sum(n for n, _ in trail_results.values())
    if not returned or any(n != null for n, null in trail_results.values()):
        return None
    return {'entries': returned, 'scenarios': sorted(trail_results)}


def cumulative_trail_ids(trail: list[dict]) -> dict[str, list[int]]:
    """`{call_id: turns}` for every trail id that appears on MORE than one turn.

    The trail is read as a PER-TURN report, and nothing enforces that: a harness whose state
    accumulates re-lists every earlier call each turn, which inflates the count quadratically. The
    union keeps both rows — collapsing them here would diverge from the SDK and backend twins, which
    report the shape rather than repair it (`_warn_if_trail_is_cumulative`) — so this is the same
    report, on this lane: an inflated count is at least attributable instead of a mystery. Ids are
    optional; a trail without them is simply unchecked.
    """
    turns_by_id: dict[str, set[int]] = {}
    for record in trail:
        call_id = record.get('call_id')
        if isinstance(call_id, str) and call_id:
            turns_by_id.setdefault(call_id, set()).add(int(record.get('turn', 0)))
    return {cid: sorted(turns) for cid, turns in turns_by_id.items() if len(turns) > 1}


def _call_identity(record: dict) -> str | None:
    """A call record's DECLARED id, when it carries a usable one.

    Read from `call_id` (the field a harness trail and the mock event log write) and then `id`
    (the field every record normalized by the SDK and backend twins carries), so one predicate answers
    for the raw `tool_events` events and for the recovered records alike. Reading only `call_id`
    here left the id lane shut on every artifact whose events were normalized upstream, which is
    the state the union silently degraded to the name rule in.

    A blank or non-string id is NOT an identity: a shared id is read below as "the same call", so
    one empty string on each lane would delete a real dispatch. Absent is the honest answer — the
    name lane handles it, loudly.
    """
    for key in ('call_id', 'id'):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _verb_args_identity(call: dict) -> tuple[str, object]:
    """WHAT a call is — its verb and its arguments AS PARSED — compared with `==`.

    `dict.__eq__` is recursive, key-order-insensitive and numeric, so two authors of one call
    collapse to one identity without a canonicalizer. The SDK and backend `verb_args_identity`, verbatim.
    """
    return (str(call.get('name') or call.get('tool') or ''), call.get('args') or {})


def union_by_call_identity(events: list[dict], trail: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """`(merged call records, {tool: rows the name rule dropped unverified})`.

    ONE RULE, THREE COPIES. This is the plugin's copy of the union the SDK
    (`plurai/observability/tool_call_recovery.py`) and the backend run on the same artifacts; one
    fixture table is proven on all three. The copies must agree: when they diverge, the consumers
    of ONE run publish different tool counts (`calls: 1, floor: 3` from one, `calls: 3, floor: 1`
    from another).

    The union exists at all because `tool_events.json` records only what went through
    `/mock/execute`: a `mode: real` dispatch writes no event and a self-contained agent loop
    leaves no transcript structure, so without the trail a call that FAILED and a call that never
    happened are byte-identical here.

    IDENTITY FIRST. Where a tool's event log declares ids, a trail row duplicates a logged call
    only when it NAMES that call, and every other row is a call in its own right. A `mode: mock`
    tool whose agent retries internally on a 5xx is the topology that rule exists for: the mock
    logs the one call it saw and the trail carries the other nineteen. Deduping on the tool NAME
    would delete all nineteen — `tool_stats` would report `calls: 1, errors: 0`, the tool would never
    reach `dead_tools`, `outcome_attribution` would come back `agent`, and the report would tell the
    customer their agent cannot do search.

    Identity is scoped PER TOOL. A call id is unique within the lane that minted it, not across
    lanes, so a harness that numbers its dispatches per tool would otherwise have one tool's `c1`
    delete another's — a cross-tool collision silently deleting a real row.

    NAME + COUNT is the fallback, and ONLY for rows identity cannot answer: a row carrying no id,
    or one whose tool's event log declares none (a trail id compared against a log that carries
    none matches nothing, so opening the id lane on that comparison would count every logged call
    twice — and an over-count feeds `dead_tools`). There the rule drops as many rows as there are
    logged events still unaccounted for and keeps the rest.

    A DROP IS RECORDED IN `floored` IN EXACTLY TWO STATES, and is silent otherwise. (a) An
    identity existed on ONE lane and not the other: the lanes could have been matched by id and
    were not, and the remedy is to emit `call_id` on both. (b) The tool's trail rows OUTNUMBER its
    logged events: the surplus proves the two lanes do not fully overlap, so the rows the count
    rule dropped may be retries rather than duplicates. Where NEITHER lane declares an id AND the
    trail is no longer than the log, the count rule is the only rule and dropping
    exactly as many rows as the log holds is its documented reconciliation, not a suppression —
    `tool_stats` prints a note for what comes back here, and a note on every ordinary run is noise
    that hides the real ones.

    AN ID-LESS LOGGED EVENT IS STILL A CALL ON BOTH LANES. Where a tool's log declares ids for
    only SOME of its events (a call that reached the mock without the active call id, such as one
    made inside a delegated worker thread), a trail row whose id names no logged event is first
    matched to the earliest unclaimed id-less event of the same tool and args
    (`_verb_args_identity`) and is one call with it, not a second one. The match is by args, not
    identity, so its drop is a floor by limb (a). A row that matches none is a call in its own
    right, as before.

    What this rule deliberately does NOT do is dedupe the trail against ITSELF. A harness that
    reports one dispatch on several turns is a CUMULATIVE trail, and collapsing it here would
    silently mask that defect instead of letting it surface as an over-count somebody fixes.
    """
    #: Ids the EVENT LOG declared, per tool. Non-empty for a tool is what opens the id lane.
    logged_ids: dict[str, set[str]] = {}
    #: Logged events not yet claimed by a trail row — the budget the name rule spends.
    unaccounted: dict[str, int] = {}
    #: Logged events that declare NO id and are not yet matched, per tool, in log order.
    idless: dict[str, list[int]] = {}
    for index, event in enumerate(events):
        name = str(event.get('tool'))
        unaccounted[name] = unaccounted.get(name, 0) + 1
        if call_id := _call_identity(event):
            logged_ids.setdefault(name, set()).add(call_id)
        else:
            idless.setdefault(name, []).append(index)
    #: How many events each tool logged, kept whole. The floor rule's (b) limb compares the SIZE
    #: of the two lanes, which `unaccounted` stops being once the first row is charged against it.
    logged_total: dict[str, int] = dict(unaccounted)
    #: How many rows the trail carries per tool — the other half of that comparison.
    trailed_total: dict[str, int] = {}
    for entry in trail:
        trailed_total[str(entry.get('tool'))] = trailed_total.get(str(entry.get('tool')), 0) + 1
    seen: dict[str, set[str]] = {name: set(ids) for name, ids in logged_ids.items()}
    merged = list(events)
    floored: dict[str, int] = {}
    for entry in trail:
        name = str(entry.get('tool'))
        call_id = _call_identity(entry)
        if call_id is not None and name in logged_ids:
            if call_id in seen[name]:
                # THE logged call, named by its own id — one dispatch reported on two lanes.
                unaccounted[name] = unaccounted.get(name, 0) - 1
                continue
            pending = idless.get(name, [])
            match = next((pos for pos, at in enumerate(pending) if _verb_args_identity(events[at]) == _verb_args_identity(entry)), None)
            if match is not None and unaccounted.get(name, 0) > 0:
                # An id-less logged event with this row's tool and args: the same call (see above).
                pending.pop(match)
                unaccounted[name] -= 1
                floored[name] = floored.get(name, 0) + 1
                continue
            seen[name].add(call_id)
            merged.append(entry)
            continue
        if unaccounted.get(name, 0) > 0:
            unaccounted[name] -= 1
            # The two-limbed floor rule stated above: an id on one lane only, or a trail longer
            # than the log. Neither, and this drop is the reconciliation, not a finding.
            if call_id is not None or name in logged_ids or trailed_total.get(name, 0) > logged_total.get(name, 0):
                floored[name] = floored.get(name, 0) + 1
            continue
        merged.append(entry)
    return merged, floored


#: HOW a scenario's tool trail is normalized before two of them are compared — written onto every
#: row as `trail_signature_mode`, never left implicit. The choice DECIDES the answer, so two runs
#: built by different plugin versions would otherwise be compared under different definitions with
#: nothing on either artifact saying so.
#:
#: ORDERED, and keyed on the ARGUMENT KEYS. Ordered, because two of the same call swapped is
#: exactly the variance a noise floor exists to characterise and a multiset erases it — measured
#: on three same-bench runs, one arm reordered two `get_flight_status` calls and a multiset read
#: it as no movement at all. Keyed on the KEYS rather than the values, because a booking code, a
#: date the agent computed or a case variant is a property of the DIALOG rather than of the tool
#: plan: on the same runs the full-value reading reported `+3 −2` where this one reported `+1`,
#: i.e. it inflates the floor with value churn. The value reading ships beside it
#: (`trail_signature_args`) so a reader can still see when only values moved.
#:
#: WHY IT IS A NAME AND NOT A CONSTANT SOMEBODY REMEMBERS: a comparison reads this field off both
#: runs and refuses to compare two modes it cannot reconcile.
TRAIL_SIGNATURE_MODE = 'tool+arg_keys_ordered'

#: What a scenario's signature says about WHERE its calls came from. `recovered` is a trail every
#: one of whose calls this script rebuilt from the agent's own `agent_state.tool_trail` because
#: `/mock/execute` logged none of them — a DERIVATION of the transcript rather than an
#: independently recorded tool plan, so comparing two such signatures partly measures transcript
#: variance. Without this split the same label
#: means two different things on the two benches. The count rides beside it
#: (`trail_signature_recovered`), so a MIXED trail is legible as the number it is.
TRAIL_SOURCE_AUTHORITATIVE = 'authoritative'
TRAIL_SOURCE_RECOVERED = 'recovered'


def trail_signature(calls: Sequence[Mapping]) -> dict:
    """`{calls, args, recovered, source}` — one attempt's tool plan, normalized two ways.

    `calls` is `TRAIL_SIGNATURE_MODE`: `tool(key,key)` per dispatch, in dispatch order, argument
    keys sorted so a harness that emits them in a different order is not read as a different plan.
    `args` is the same list with the FULL arguments instead of their key set — the strictest
    reading, carried so a comparison can say when only values moved without having to guess.

    Values are serialized with `sort_keys` and `default=str` and are NOT normalized further. That
    is deliberate and it is this function's G2 guard: argument-value normalization is exactly where
    a customer literal would sneak in (stripping "dates", lower-casing "codes"), and the key-set
    reading needs no value rules at all. A value rule added later is keyed on the value's TYPE,
    never on a field name.
    """
    tokens: list[str] = []
    args: list[str] = []
    recovered = 0
    for call in calls:
        name = str(call.get('tool') or '?')
        raw = call.get('args')
        raw = raw if isinstance(raw, Mapping) else {}
        tokens.append(f'{name}({",".join(sorted(str(k) for k in raw))})')
        args.append(f'{name}({json.dumps(dict(raw), sort_keys=True, default=str)})')
        recovered += int(bool(call.get('_recovered')))
    out = {'calls': tokens, 'args': args, 'recovered': recovered}
    # An attempt with NO calls claims neither adjective: there is no call whose source could be
    # authoritative or recovered, and stamping one would put a measurement on a row nobody made
    # one for. `calls: []` is the whole of what is known, and the comparison reads it as `no_trail`.
    if tokens:
        out['source'] = TRAIL_SOURCE_RECOVERED if recovered == len(tokens) else TRAIL_SOURCE_AUTHORITATIVE
    return out


def trail_distance(before: Sequence[str], after: Sequence[str]) -> dict[str, int]:
    """`{added, dropped}` — how far one normalized trail is from another, ORDER INCLUDED.

    An ordered alignment (`difflib.SequenceMatcher`), never a multiset difference. Two calls
    swapped is `+1 −1` here and `+0 −0` under a multiset, and a reordering is precisely the
    phenomenon a noise floor exists to characterise: drop the order and the bench's own reordering
    becomes invisible and the floor is understated.

    The two numbers are reported apart and summed into ONE distance by the comparison, so a swap
    costs 2 and a dropped call costs 1. Symmetric by construction.
    """
    matcher = difflib.SequenceMatcher(a=list(before), b=list(after), autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return {'added': len(after) - matched, 'dropped': len(before) - matched}


def retry_trail_signature(d: Path) -> dict | None:
    """The SECOND ATTEMPT's signature from `stability_retry/`, or None when the scenario has none.

    Its own reader rather than a second `tool_stats` call: that function's notes are per-SCENARIO
    advice about the run's tool counts, and a retry would print every one of them a second time in
    a stream where nothing distinguishes the two attempts. The CALL LIST itself is built by the
    same union rule over the same two lanes (`union_by_call_identity` — the one rule, three
    repos), so the two attempts' signatures are comparable by construction rather than by luck.
    """
    retry = d / RETRY_DIRNAME
    doc = _read_json(retry / 'tool_events.json')
    if doc is None:
        return None
    events = [e for e in doc.get('events', []) if isinstance(e, dict) and e.get('type') == 'tool_call']
    calls, _ = union_by_call_identity(events, trail_calls(retry))
    return trail_signature(calls)


def stability_reasons(out: Path) -> dict[str, str]:
    """`{scenario: the runner's stated reason for taking a second attempt}`, from `run_summary.json`.

    WHY THE SELECTION IS PUBLISHED BESIDE THE DISTANCE. The runner takes a repeat only for
    `invalid_run`, `incomplete` or `eval_failed` and caps the pool at half the suite, so the
    population is CONDITIONED ON FAILURE: a scenario whose cells all passed never earns one, and a
    clean run earns none at all.

    That is why `trail_repeat_distance` ships as EVIDENCE and never as a noise floor: a retry-derived
    distance can be 0 where a true independent repeat of the whole set puts it at 1, and a number
    called "the floor" that is selection-biased and can be zero where the truth is one is worse than no number, because it licenses discounting a real
    delta. The floor comes from N ≥ 2 same-configuration whole-SET runs or it does not exist.
    """
    doc = _read_json(out / 'run_summary.json')
    rows = (doc or {}).get('stability')
    if not isinstance(rows, dict):
        return {}
    return {str(name): str(row['reason']) for name, row in rows.items()
            if isinstance(row, dict) and isinstance(row.get('reason'), str) and row['reason']}


def stamp_trail_signature(row: dict, tools: dict, repeat: dict | None, selection: str | None) -> dict:
    """Put this scenario's SINGLE-RUN trail facts on its row — and only the single-run ones.

    ONE stamper for the graded row and the errored row alike: an errored scenario still dispatched
    tools, and its trail is the first thing a reader reaches for when asking whether the
    environment was the problem.

    WHAT IS DELIBERATELY NOT HERE: the cross-run label (`same_trail` / `within_trail_noise` /
    `beyond_trail_noise` / `trail_noise_unknown`). `run.json` is written before any second run
    exists, so a label on it would be a comparison against nothing. It lives on the comparison's
    output (`compare_runs.py`) and nowhere else.
    """
    signature = tools.get('signature') or trail_signature([])
    row['trail_signature'] = list(signature['calls'])
    row['trail_signature_args'] = list(signature['args'])
    row['trail_signature_mode'] = TRAIL_SIGNATURE_MODE
    row['trail_signature_recovered'] = signature['recovered']
    if signature.get('source'):
        row['trail_source'] = signature['source']
    # The second sample the RUNNER took, when it took one — a fact, with the selection that
    # produced it stated beside it so nobody reads it as a floor (see `stability_reasons`).
    if repeat is not None:
        row['trail_repeat_distance'] = repeat
        row['trail_repeat_selection'] = selection or 'unknown'
    return row


def merged_calls(d: Path, doc: dict | None = None) -> tuple[list[dict], list[dict], dict[str, int]]:
    """`(calls, trail, floored)`: the item's `tool_events.json` tool calls (`doc`, read from `d` when None) unioned with
    its self-reported trail (`union_by_call_identity`, then `_carry_unanswered`) — the one call list :func:`tool_stats`
    counts and :func:`served_failures` reads. Says nothing: the notes on what it found are `tool_stats`'s."""
    if doc is None:
        doc = _read_json(d / 'tool_events.json') or {}
    events = [e for e in doc.get('events', []) if isinstance(e, dict) and e.get('type') == 'tool_call']
    trail = trail_calls(d)
    calls, floored = union_by_call_identity(events, trail)
    return _carry_unanswered(calls, trail), trail, floored


def _call_failed(e: dict) -> bool | None:
    """Whether a call's own result says it failed, read off typed top-level fields only: a dispatch error, a refusal
    envelope, a named `error` / `errors`, or `success` / `ok` false. None when its result is no mapping (unknown)."""
    if _dispatch_error(e):
        return True
    raw = _call_response(e)
    if not isinstance(raw, dict):
        return None
    return (_refusal_envelope(raw) or any(_blocker_named(raw.get(k)) for k in ('error', 'errors'))
            or raw.get('success') is False or raw.get('ok') is False)


def served_failures(calls: Sequence[dict], serves: Mapping[str, frozenset[str]] | None) -> list[dict]:
    """`[{turn, tool, said_ok, own_error, results, requests: {backend: {code: n}}}]`: per turn, the failed backend
    requests (`_dispatch_error` rows of a `serves:` entry) charged to the tool that made them. A request is charged to the one
    tool of its `serves:` list the agent called that turn; when several were called, to the one whose own result failed
    (:func:`_call_failed`); otherwise to none. A row with no turn is never charged: the two lanes share no clock within a
    turn, so the turn is the only link. `said_ok`: every call of that tool that turn returned a readable result that
    does not say it failed. `own_error`: one of those calls is itself a dispatch error. `results`: those calls' results."""
    serves = serves or {}
    by_turn: dict[int, list[dict]] = {}
    for e in calls:
        turn = e.get('turn')
        if isinstance(turn, int) and not isinstance(turn, bool):
            by_turn.setdefault(turn, []).append(e)
    out = []
    for turn, rows in sorted(by_turn.items()):
        own: dict[str, list[dict]] = {}
        for e in rows:
            if str(e.get('tool') or '?') not in serves:
                own.setdefault(str(e.get('tool') or '?'), []).append(e)
        outcome = {tool: [_call_failed(e) for e in its] for tool, its in own.items()}
        charged: dict[str, dict[str, Counter]] = {}
        for e in rows:
            request = str(e.get('tool') or '?')
            if request not in serves or not _dispatch_error(e):
                continue
            candidates = serves[request] & set(own)
            if len(candidates) > 1:
                candidates = {tool for tool in candidates if True in outcome[tool]}
            if len(candidates) == 1:
                raw = r if isinstance(r := _call_response(e), dict) else {}
                code = raw.get('code', raw.get('status_code'))
                charged.setdefault(next(iter(candidates)), {}).setdefault(request, Counter())[str(code if code is not None else 'error')] += 1
        out.extend({'turn': turn, 'tool': tool, 'said_ok': all(f is False for f in outcome[tool]),
                    'own_error': any(_dispatch_error(e) for e in own[tool]), 'results': [r if isinstance(r := _call_response(e), dict) else None for e in own[tool]],
                    'requests': {request: dict(codes) for request, codes in sorted(requests.items())}}
                   for tool, requests in sorted(charged.items()))
    return out


def tool_stats(d: Path, *, surface_known: bool = False, backend: frozenset[str] = frozenset(),
               serves: Mapping[str, frozenset[str]] | None = None) -> dict:
    """`{calls, errors, refusals, zero_results, recovered, by_tool}` — the mock's event log
    UNIONED with the agent's self-reported trail from transcript.json.

    `backend` names the tools `plurai.config.yaml` declares `serves:` for, and the C12 mocks beneath the tool that are
    none of the agent's own tools, the scan inventory's live list (`declared_backend_requests`):
    requests the agent's own code makes below its tools, never a call the agent chose. Their rows
    are left out of `calls`, `errors`, `refusals`, `zero_results` and `recovered` and counted in
    `backend_calls` instead, so a customer's tool count is the agent's own. A failing backend request is
    the served tool's failure whatever that tool's own result says (it may relay the failure, or report
    success over it), so with `serves` (`declared_serves`) it is counted once, on the call it served:
    one error per (turn, tool) :func:`served_failures` charges, unless that call already is a dispatch error.
    `by_tool`, `recovered_by_tool` and `signature` still read every row: a dead mock is an environment fact.

    The events are under `doc["events"]` — there is NO top-level `tool_events` key, and
    reading one yields None/0 on a file with 25 events, rendering exactly the
    silently-green report this function exists to prevent. `.get` both
    levels: an artifact from an older runner may be a bare `{}`.

    The file is written for EVERY scenario, including a tool-less run (present-but-empty,
    `{"session_id": …, "events": []}`) — so a MISSING file means the scenario never finalized,
    which is worth reporting, and this prints it to stderr rather than defaulting quietly.
    An EMPTY file is either a genuinely tool-less agent (expected — zeros) or an agent
    whose `mode: real` tools bypass `/mock/execute` and leave no event. A file holding a
    literal `null` is neither: it is the runner DECLARING the channel unread for this
    scenario, and it is reported as an unread channel — not as a scenario that failed to
    finalize, and not as a measured zero.

    UNIONED with the agent's self-reported trail (`trail_calls`), deduped by CALL IDENTITY
    (`union_by_call_identity` — the ONE rule, shared byte-for-byte with the SDK's and the backend's
    twins): where a tool's log declares ids, a trail entry is dropped only when it NAMES a call
    the log already holds, so a mock-logged tool the agent RETRIED keeps every retry. Without the
    union a live-mode tool is invisible here (its terminal csv failures would publish as `0 tool
    errors`); with the union deduping on the tool NAME instead, a hybrid tool would lose 19
    dispatches to one logged event.

    The rows the name rule could not verify come back as `floored`, and they are RETURNED under
    `floor` as well as printed. stderr is not what the customer reads, so it cannot be the only
    place a reader learns the counts are a floor: `run.json` and `analytics.md` are, and both
    are computed from these very numbers. The note is printed ONCE per scenario and ONLY for the
    two states that are findings (an id on one lane only, or a trail longer than the log) — a
    note on every ordinary run is noise, and the next real one gets skimmed past — and `floor` is
    empty in exactly the same states, for exactly the same reason.
    """
    events_file = d / 'tool_events.json'
    doc = _read_json(events_file)
    unread = False
    if doc is None:
        # THREE states, not two. `_read_json` answers None for a file that is not there AND for
        # one holding a literal `null` — and the SDK writes exactly that literal when the tool
        # channel was never read for this scenario (`run_simulation.py`'s `_write_json(... ,
        # result.tool_events)`, deliberately NOT `or {}`). Reading the declaration as an absent
        # file said "the scenario did not finalize" about a scenario that finalized fine, and
        # then published `0 tool calls` as a measurement of a channel nobody measured. The backend's
        # two readers already honour the tri-state (`score_saved_run`'s
        # `TOOL_CHANNEL_UNSUPPLIED_KEY`, `run_artifacts_store`'s `NULLABLE_ARTIFACTS`); this is
        # the third reader.
        unread = events_file.exists()
        if unread:
            print(f'note: {d.name}/tool_events.json is a literal `null` — the runner DECLARED this tool '
                  'channel unread for this scenario. The counts below are not a measurement of zero calls; '
                  'nothing observed this scenario\'s tools at all', file=sys.stderr)
        else:
            print(f'note: {d.name}/tool_events.json is missing — the scenario did not finalize', file=sys.stderr)
        doc = {}
    calls, trail, floored = merged_calls(d, doc)
    if repeated := cumulative_trail_ids(trail):
        shown = ', '.join(f'{cid} (turns {",".join(map(str, turns))})' for cid, turns in sorted(repeated.items())[:5])
        print(f'note: {d.name} — {len(repeated)} trail call_id(s) appear on more than one turn, so `{_AGENT_STATE_TOOL_TRAIL_KEY}` '
              'looks CUMULATIVE rather than per-turn: the tool counts for this scenario are INFLATED, not a measurement. '
              f'Report only the calls each turn issued. Repeated ids: {shown}', file=sys.stderr)
    if floored:
        # Said ONCE per scenario, and it names the size of the hole. Every count below is a FLOOR
        # for these tools, and a floor a reader cannot see is indistinguishable from a measurement
        # — which is exactly how a hybrid tool's 19 lost dispatches read as a healthy `calls: 1`.
        named = ', '.join(f'{name}x{n}' for name, n in sorted(floored.items()))
        print(f'note: {d.name} — {sum(floored.values())} self-reported tool call(s) could not be matched to '
              f"the mock's event log by `call_id`, so they were reconciled on the TOOL NAME and dropped "
              f'({named}). The tool counts for this scenario are a FLOOR, not a measurement: nothing here '
              'can tell a duplicate of a logged call from a retry the mock never saw, and these tools '
              'either declared an id on one lane only or reported more calls than the log holds. Emit '
              '`call_id` on both lanes to count them.', file=sys.stderr)
    # tool_errors and tool_refusals are TWO DIFFERENT FINDINGS — keep them apart. One pass,
    # one predicate (`_dispatch_error`): a second filtered list re-tested by membership makes
    # the two ways of asking the same question free to drift.
    own = [e for e in calls if str(e.get('tool') or '?') not in backend]
    errors = 0
    zero = 0
    for e in own:
        if _dispatch_error(e):
            errors += 1
            continue  # a dispatch failure is tool_errors' finding — not a refusal, not an empty result
        raw = e.get('response') if e.get('response') is not None else e.get('result')
        if raw is None:
            continue  # a recovered record whose ToolMessage never surfaced — unknown, not empty
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                continue  # prose response — has content, not an envelope and not a zero-row shape
        # An error-shaped answer from a dispatched call (`status: "error"`, an HTTP 404 the tool relayed) is neither a
        # dispatch failure nor an empty result, and it is NOT a refusal either: `tool_refusals` is the SDK's typed
        # refusal count (C7, `refusal: {by, reason}`), never this envelope.
        if isinstance(raw, dict) and _refusal_envelope(raw):
            continue
        if _zero_result(raw):
            zero += 1
    # A failed request beneath a tool is that tool's failed call, counted once per (turn, tool) unless the call already is one.
    errors += sum(1 for f in served_failures(calls, serves) if not f['own_error'])
    # `_recovered: true` marks records this script rebuilt from the agent's own
    # trail (`trail_calls`) rather than reading out of the mock's event log — the only
    # out-of-band lane it has (the SDK's `merged_tool_calls` also walks `chatbot_messages`; this
    # one does not). A run counted mostly from recovered records is a run whose tools bypassed
    # the mock, so its state effects were never observed.
    recovered_by_tool = dict(Counter(str(e.get('tool') or '?') for e in calls if e.get('_recovered')))
    recovered = sum(1 for e in own if e.get('_recovered'))
    # Per TOOL, not just per scenario: a run-wide "this tool fired and never once succeeded" is
    # what lets the outcome attribution below tell an agent that failed from a family that was
    # never testable (see `dead_tools`). Counted here because this is the one pass over the
    # merged call list.
    by_tool: dict[str, dict[str, int]] = {}
    #: `{tool: rows whose dispatch outcome NOTHING decided}` — no harness stamp, and prose in a
    #: script the transport vocabulary structurally cannot read (`_trail_dispatch_unreadable`).
    #: They are in `calls` and in neither `errors` nor any clean count, which is the honest
    #: place for them; what they must not be is invisible, because their silence reads as health.
    unclassified: dict[str, int] = {}
    #: `{tool: rows the harness gated BY NAME}` — a subset of the errors above, never a third
    #: bucket. They count as failed dispatches because nothing came back; what this says is whose
    #: the failure is, and it is the only channel that says it when `--workdir` names no config
    #: and `invented_verbs` therefore may not speak.
    gated_by_name: dict[str, int] = {}
    #: `{tool: [failed calls, the first one's own error]}` — the dispatches that failed, their error as the call streamed it.
    setup: dict[str, list] = {}
    for e in calls:
        tally = by_tool.setdefault(str(e.get('tool') or '?'), {'calls': 0, 'errors': 0, 'unanswered': 0, 'invalid_args': 0})
        tally['calls'] += 1
        tally['errors'] += int(_dispatch_error(e))
        # Two SUBSETS of `errors`, split by whose typed fact the failure carries (`dead_tools`):
        # the environment's (never answered) and the engine's own "the agent's arguments were invalid".
        tally['unanswered'] += int(_env_unanswered(e))
        tally['invalid_args'] += int(_agent_invalid_args(e))
        if _dispatch_error(e):
            setup.setdefault(str(e.get('tool') or '?'), [0, _call_error(e)])[0] += 1
        if e.get('_dispatch_unreadable'):
            name = str(e.get('tool') or '?')
            unclassified[name] = unclassified.get(name, 0) + 1
        # Both lanes: the marker for a row `trail_calls` rewrote the status of, and the raw token
        # for one that reached this list through the SDK's merged event log.
        if e.get('_not_dispatchable') or _trail_not_dispatchable(e):
            name = str(e.get('tool') or '?')
            gated_by_name[name] = gated_by_name.get(name, 0) + 1
    if gated_by_name:
        named = ', '.join(f'{name}x{n}' for name, n in sorted(gated_by_name.items()))
        # The `--workdir` half is advice, so it is printed only when it is still ACTIONABLE. With
        # the declaration already read it is noise on a run that did the right thing.
        advice = ('' if surface_known else
                  ' Pass `--workdir` so the declaration can name them as invented verbs rather than as a dead'
                  ' environment.')
        print(f'note: {d.name} — {sum(gated_by_name.values())} call(s) the harness refused BY NAME ({named}): the '
              'agent asked for a verb its flow does not expose, so nothing was dispatched. They count as failed '
              f'dispatches — a call that never left did not succeed.{advice}', file=sys.stderr)
    if unclassified:
        named = ', '.join(f'{name}x{n}' for name, n in sorted(unclassified.items()))
        print(f'note: {d.name} — {sum(unclassified.values())} dispatch outcome(s) in a script the transport '
              f'vocabulary cannot read ({named}). The harness stamped no status and the failure regex is '
              'English-keyed, so these calls are NOT counted as errors and NOT counted as clean. Emit a '
              '`status` on the trail so the outcome is stated rather than inferred from prose.', file=sys.stderr)
    refused = [a for e in own if (a := refused_attempt(e)) is not None]
    return {'calls': len(own), **({'backend_calls': len(calls) - len(own)} if backend else {}), 'errors': errors, 'refusals': len(refused), 'zero_results': zero,
            # Every attempt a gate refused, named: the agent's own try, eligible as a finding.
            'refused_attempts': refused,
            'recovered': recovered, 'recovered_by_tool': recovered_by_tool, 'by_tool': by_tool, 'floor': dict(sorted(floored.items())),
            'unclassified': dict(sorted(unclassified.items())),
            # `{tool: rows the harness refused BY NAME}`: the only evidence `invented_verbs` accepts.
            'not_dispatchable': dict(sorted(gated_by_name.items())),
            'setup': setup,
            # Whether the mock's lane was DECLARED unread for this scenario (a literal `null`
            # file), so `calls` above can be told from a measured zero by a reader of the row and
            # not only by whoever was watching stderr.
            'events_unread': unread,
            # The trail rows whose dispatch did not fail, and how many of them carry no `result`: the run-wide
            # all-null reading (`tool_trail_results_missing`) is a harness that never records what its tools returned.
            'trail_returned': sum(1 for r in trail if r['status'] != 'error'),
            'trail_returned_without_result': sum(1 for r in trail if r['status'] != 'error' and r.get('response') is None),
            # The scenario's TOOL PLAN, normalized — built off this same merged list so the
            # signature and every count above describe the same dispatches. Computing it from
            # `tool_events.json` alone would describe a different run: the union is where a
            # live-mode call that bypassed the mock enters the list at all.
            'signature': trail_signature(calls)}


def closed_class_detectors(per: dict) -> list[dict] | None:
    """The transcript's `closed_class_detectors` rows, or None when it carries none.

    WHICH CLOSED CLASSES WERE CHECKED IN CODE on this scenario, and by what. A closed class is a
    value set the customer's sources enumerate exhaustively, and it licenses a judge to red a
    value that is not on the list — so a reader has to be able to tell a licence a DETECTOR stands
    behind from one a judge applied alone.

    None (not a synthesised empty list) when the scenario closes nothing: "no class was closed"
    and "classes were closed and nothing checked them" are different facts about a run, and the
    second is the one worth acting on.
    """
    rows = per.get('closed_class_detectors')
    if not isinstance(rows, list):
        return None
    kept = [r for r in rows if isinstance(r, dict) and r.get('class') and r.get('status')]
    return kept or None


def closed_class_summary(per_scenario: dict[str, list[dict]]) -> dict | None:
    """Roll the per-scenario rows up by CLASS — the shape a report renders.

    Rolled up by class and not by scenario because the decision is a property of the registry the
    whole set is grounded on: every scenario that carries a class carries the same verdict about
    it, and one row per scenario per class would be the same sentence N times. A class whose
    scenarios disagree about its STATUS is reported as `inconsistent` rather than collapsed — that
    would mean the set was scored against two registries, which is worth a loud line. The other
    fields are taken from the first row; they are properties of the class, not of the scenario, and
    a set that disagreed about them is already named by the status.
    """
    if not per_scenario:
        return None
    by_class: dict[str, dict] = {}
    # PARALLEL to the record, never inside it: `statuses` is scratch, and a scratch key stored in
    # the dict that ships to run.json leaks into the customer-facing artifact the first time
    # anything copies or dumps it early.
    statuses_by_class: dict[str, dict[str, list[str]]] = {}
    for scenario, rows in sorted(per_scenario.items()):
        for row in rows:
            name = str(row['class'])
            by_class.setdefault(
                name,
                {
                    'class': name,
                    'alias': str(row.get('alias') or name),
                    'members': row.get('members'),
                    'rule': str(row.get('rule') or ''),
                    'reason': str(row.get('reason') or ''),
                },
            )
            statuses_by_class.setdefault(name, {}).setdefault(str(row['status']), []).append(scenario)
    classes = []
    for entry in sorted(by_class.values(), key=lambda e: e['class']):
        statuses = statuses_by_class[entry['class']]
        if len(statuses) == 1:
            entry['status'] = next(iter(statuses))
        else:
            entry['status'] = 'inconsistent'
            entry['inconsistent_across'] = {k: sorted(v) for k, v in sorted(statuses.items())}
        classes.append(entry)
    return {'classes': classes, 'checked': sum(1 for c in classes if c['status'] == 'active'), 'total': len(classes)}


def user_sim_integrity(per: dict) -> dict | None:
    """The transcript's `user_sim_integrity` block, or None when it carries none.

    SCENARIO fidelity, not measurement integrity: the block records where the USER SIMULATOR
    departed from the scenario's own schedule — a scripted question delivered three turns
    early, a beat over-reported, a clamp released. When it did, the transcript is unfaithful to
    the scenario, and every turn-indexed judgment over those turns graded a dialog the scenario
    never specified. No eval fix repairs that; it is a REGENERATE-or-rerun finding.

    None (not a synthesised clean block) on an older transcript: "no anomalies" and "nobody looked" must not render alike.
    """
    block = per.get('user_sim_integrity')
    if not isinstance(block, dict) or 'clean' not in block:
        return None
    return block


#: `goal_source` when the eval set declared NO goal owner for a scenario. Such a row is out of
#: the "N of M goals achieved" DENOMINATOR and counted beside it instead — a customer whose eval
#: set merely predates the goal owner must not read `0 of 7` on every report.
GOAL_SOURCE_NONE = 'none'

#: The goal owners the backend declares today. NOT a gate: an unrecognized source still names an
#: owner, so the scenario stays measurable and the name is REPORTED on the row rather than
#: swallowed by a closed set that pretends nothing fell outside it.
KNOWN_GOAL_SOURCES = frozenset({'deterministic', 'target_judge', GOAL_SOURCE_NONE, 'pipeline_error', 'pipeline_collapse', 'agent_closed'})

#: OPTIONAL `scenario_verdict` key the backend sets when the situation a scenario's target is written against never
#: arose, so neither axis decided the goal; absent on every other row. The reason such a row carries:
TARGET_PREMISE_UNSHOWN_KEY = 'target_premise_unshown'
TARGET_PREMISE_UNSHOWN_REASON = "the situation this scenario tests never came up in the conversation, so its goal wasn't measured"
#: The key a final-run correction (`verdict_corrections.confirm_skipped_steps`) sets in place of the one above when the
#: situation never came up because the AGENT skipped a step the scenario requires and the audit confirmed the owner's
#: red: `{owner, premise}`, beside `goal_achieved: false`. The goal was missed, never "not measured".
REQUIRED_STEP_SKIPPED_KEY = 'required_step_skipped'


def _verdict_field(per: dict, key: str) -> dict | None:
    """One block of the verdict view (:func:`verdict_view`), or ``None``.

    The nested ``{aggregated_results: {...}}`` envelope is unwrapped, the same tolerance the
    SDK's reader applies. Looking in one place only would read a present block as absent,
    which lands as ``unknown`` — a silent downgrade of a run that WAS measured.
    """
    if not isinstance(per, dict):
        return None
    if key in per:
        return per[key]
    nested = per.get('aggregated_results')
    return nested[key] if isinstance(nested, dict) and key in nested else None


def _integrity_of(block: object) -> tuple[bool | None, str | None]:
    """``(clean, unreadable)`` from a ``measurement_integrity`` block. THREE cases, not two.

    `clean` absent is `(None, None)` — nobody checked. `clean` a real bool is that bool. `clean`
    PRESENT but not a bool (`'false'`, `0`, a mis-keyed shape) is `(None, <description>)`: the
    backend tried to say something this version cannot read, and treating that as silence sends
    it down the same path as "nobody checked", which ends in a pass. Callers must treat an
    unreadable claim as NOT clean — the same rule the sibling `passed` field already gets.
    """
    if not isinstance(block, dict) or 'clean' not in block:
        return None, None
    clean = block['clean']
    if isinstance(clean, bool):
        return clean, None
    return None, f'measurement_integrity.clean is {clean!r} ({type(clean).__name__}), not a bool'


#: A breach owned by the scenario's own ground truth: its declared criterion raised or produced nothing.
_GT_BREACH_OWNER = 'gt@scenario'
#: A breach the RIG owns (`turn_eval_error`, `unreadable_evidence`, …): a per-turn pass crashed or the
#: harness could not read declared evidence, so the hole runs through every turn-based policy's stream —
#: the goal owner's and every veto's included. It always voids the verdict.
_HARNESS_BREACH_OWNER = 'harness@client'
#: A breach the EVAL SET owns (`policy_error`, `guard_error`, `uncelled_policy`): ONE named policy produced no
#: cell. It spares the verdict only when that policy is KNOWN unable to decide it (`_verdict_clean`).
_POLICY_BREACH_OWNER = 'policy@eval_set'
#: the backend's closed `verdict_tier` set (`policies/verdict_types.py`). A declared value outside it is a tier this
#: plugin cannot read, so the policy's veto capability is UNKNOWN — never the default.
VERDICT_TIERS = ('catastrophic', 'failing', 'standard')
#: The declared tiers whose confident red fails the trial (`scenario_verdict.passed`).
_VETO_TIERS = ('catastrophic', 'failing')
#: The eval set the plugin holds locally, under `--workdir`.
POLICIES_CONFIG_FILENAME = 'policies_config.yaml'


#: Where a policy's effective tier came from (`evals[].tier_source`): its author's `verdict_tier`; a
#: `terminate_values` STOP floor; the backend's derivation for an undeclared tier; or `unknown` — the eval set
#: was not read, the policy is not in it, or it declares a tier outside :data:`VERDICT_TIERS`.
TIER_SOURCES = ('declared', 'terminate_floor', 'derived', 'unknown')


def policy_effective_tier(body: object) -> tuple[str | None, str]:
    """``(tier, source)`` — the tier a confident red on this policy carries, and where it came from.

    Mirrors the backend's own classifiers (`eval_coverage.trial_tier` + `loader.is_integrity_floor`) rather
    than inventing a third, one branch per row:

    ======================================================  ==========================  =================
    body                                                    tier                        source
    ======================================================  ==========================  =================
    any ``terminate_values`` floor, not declared            ``failing``                 ``terminate_floor``
    ``catastrophic`` (a STOP floor at any scope still
    lands in `decisive_violations`)
    a declared ``verdict_tier`` in :data:`VERDICT_TIERS`    that tier                   ``declared``
    undeclared, ``severity: high`` or                       ``failing``                 ``derived``
    ``grades_source_fidelity: true``
    undeclared, anything else                               ``standard``                ``derived``
    a declared tier outside :data:`VERDICT_TIERS`, or a     ``None``                    ``unknown``
    body this cannot read
    ======================================================  ==========================  =================
    """
    if not isinstance(body, dict):
        return None, 'unknown'
    declared = body.get('verdict_tier')
    token = '' if declared is None else str(declared).strip().lower()
    if body.get('terminate_values') and token != 'catastrophic':
        return 'failing', 'terminate_floor'
    if not token:
        severity = body.get('severity')
        high = (isinstance(severity, str) and severity.strip().lower() == 'high') or body.get('grades_source_fidelity') is True
        return ('failing' if high else 'standard'), 'derived'
    return (token, 'declared') if token in VERDICT_TIERS else (None, 'unknown')


def policy_veto_capability(body: object) -> bool | None:
    """Whether this policy, had it produced a cell, could have FAILED the scenario by itself — or ``None``.

    :func:`policy_effective_tier`'s reading as a bool: ``True`` for a ``catastrophic`` or ``failing`` tier
    (declared, a ``terminate_values`` STOP floor, or derived from ``severity: high`` / ``grades_source_fidelity``),
    ``False`` for ``standard``, ``None`` for an UNKNOWN tier — which the caller reads as "could have", never
    as "could not".
    """
    tier, _source = policy_effective_tier(body)
    return None if tier is None else tier in _VETO_TIERS


def eval_set_veto_capability(workdir: str | None) -> dict[str, bool | None] | None:
    """``{policy: policy_veto_capability(body)}`` over ``<workdir>/policies_config.yaml``, or ``None``.

    ``None`` means the eval set was not read (no workdir, no file, no pyyaml, unreadable, not a mapping):
    every `policy@eval_set` breach then reads as UNKNOWN tier and voids its verdict. See :func:`eval_set_bodies`.
    """
    bodies = eval_set_bodies(workdir)
    return None if bodies is None else {name: policy_veto_capability(body) for name, body in bodies.items()}


def eval_tier_fields(name: str, bodies: Mapping[str, object] | None) -> dict:
    """``{verdict_tier, tier_source, severity}`` for one `evals[]` row — every key always present.

    ``verdict_tier`` / ``tier_source`` are :func:`policy_effective_tier` over the policy's local body;
    ``severity`` is its declared ``severity`` string, else ``None``. A policy the local eval set does not
    hold (or an eval set that was not read: ``bodies`` is ``None``) is ``None`` / ``unknown`` / ``None`` —
    never a guessed tier.
    """
    body = (bodies or {}).get(name)
    tier, source = policy_effective_tier(body) if body is not None else (None, 'unknown')
    severity = body.get('severity') if isinstance(body, dict) else None
    return {'verdict_tier': tier, 'tier_source': source, 'severity': severity.strip().lower() if isinstance(severity, str) and severity.strip() else None}


def eval_set_bodies(workdir: str | None) -> dict[str, object] | None:
    """``{policy: body}`` off ``<workdir>/policies_config.yaml``, or ``None`` when the eval set was not read.

    ``None`` means: no workdir, no file, no pyyaml, unreadable, or not a mapping. Said on stderr when the
    file exists and could not be read — a silent `None` would turn every spared side-judge error into an
    `unclean` row with nothing pointing at why.
    """
    if not workdir:
        return None
    path = Path(workdir) / POLICIES_CONFIG_FILENAME
    if not path.is_file():
        return None
    if yaml is None:
        print(f'note: pyyaml is not importable from {sys.executable} — {path} was not read, so no errored policy can be '
              'shown unable to veto: every policy_error / guard_error / uncelled_policy breach voids its verdict.', file=sys.stderr)
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        print(f'note: {path} could not be read ({exc}) — every policy_error / guard_error / uncelled_policy breach voids '
              'its verdict, because no errored policy can be shown unable to veto.', file=sys.stderr)
        return None
    if not isinstance(doc, dict):
        print(f'note: {path} does not parse to a mapping ({type(doc).__name__}) — every policy_error / guard_error / '
              'uncelled_policy breach voids its verdict.', file=sys.stderr)
        return None
    return {str(name): body for name, body in doc.items()}


#: The backend's name fallback for a policy that typed NEITHER source-fidelity field (`loader._SOURCE_FIDELITY_NAME_TOKENS`,
#: `loader._SOURCE_DEFERRAL_NAME_TOKENS`); a declaration on either field settles both (`loader.source_role_declared`).
_SOURCE_ROLE_NAME_TOKENS = {'grades_source_fidelity': ('documentation_fidelity', 'knowledge_groundedness'),
                            'defers_to_source_fidelity': ('synthesis',)}


def _declared_flag(raw: object) -> bool | None:
    """The backend's `loader.declared_target_grading`: a raw YAML flag as True / False / ``None`` (undeclared)."""
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        token = raw.strip().lower()
        return True if token in ('true', 'yes', 'on', '1') else False if token in ('false', 'no', 'off', '0') else None
    return bool(raw)


def _source_role(name: str, body: object, field: str) -> bool:
    """Whether policy ``name`` holds the source-fidelity role ``field`` — The backend's `policy_grades_source_fidelity` /
    `policy_defers_to_source_fidelity` precedence: its own declaration, then a declared sibling (False), then its name."""
    declared = {f: _declared_flag(body.get(f)) if isinstance(body, dict) else None for f in _SOURCE_ROLE_NAME_TOKENS}
    if declared[field] is not None:
        return declared[field]
    if any(v is not None for v in declared.values()):
        return False
    return any(token in name.lower() for token in _SOURCE_ROLE_NAME_TOKENS[field])


def _voids_goal(subject: str, goal_owners: set[str], bodies: Mapping[str, object]) -> bool:
    """An errored policy whose missing cell voids the GOAL verdict even though it does not own the goal:

    * a ``terminate_values`` STOP floor — had it fired the dialog would have ended earlier, so the goal was graded
      on a conversation that should not have existed;
    * a source-fidelity owner when a goal owner defers to it — the backend voids a deferring PASS on an owner red,
      so an owner that produced no cell hides that void.
    """
    body = bodies.get(subject)
    if isinstance(body, dict) and body.get('terminate_values'):
        return True
    return (_source_role(subject, body, 'grades_source_fidelity')
            and any(_source_role(o, bodies.get(o), 'defers_to_source_fidelity') for o in goal_owners))


def _verdict_clean(integrity: object, clean: bool | None, owners: object,
                   bodies: Mapping[str, object] | None = None, *, goal: bool = False) -> tuple[bool | None, list[str]]:
    """``(clean as the VERDICT reads it, breaches that leave the verdict standing)``.

    The backend sets ``clean = not breaches``, so one ``policy_error`` on a side judge would turn a passed
    scenario ``unclean`` — in no rate — though the checks that decide the goal all ran. A breach
    leaves the verdict standing ONLY when it is a `policy@eval_set` breach whose subject is KNOWN to be
    in the local eval set (``bodies``), neither a goal owner (``target_judge_owners``) nor goal-voiding
    (:func:`_voids_goal`), and — on the ROW axis only (``goal=False``) — not veto-capable
    (:func:`policy_veto_capability`: not catastrophic / failing, no STOP floor). The GOAL axis carries no
    veto, so a veto-capable side judge's error leaves the goal verdict standing. Everything else voids:
    ``voids_scenario``, ``gt@scenario``, ``harness@client`` (a crashed turn pass is a hole in every
    turn-based stream), an owner this version does not know, a policy the eval set does not hold, and on
    the row axis an errored policy whose tier is unknown — a policy that never produced a cell cannot be
    shown to have spared the scenario it could have failed.

    On the GOAL axis, when every breach carries the backend's typed ``voids_goal``, that flag decides (stamped
    with the eval set the run was scored with): the goal stands iff none is ``True``. The local rule above
    is only the fallback for a backend that does not stamp it.

    Unchanged — the block's own ``clean`` decides — when ``clean`` is not ``False``, when any breach
    lacks its typed ``owner`` / ``voids_scenario``, or when the goal owners are not published: a
    breach that cannot be routed is never read as one that spared the goal.
    """
    if clean is not False or not isinstance(integrity, dict):
        return clean, []
    breaches = integrity.get('breaches')
    if not isinstance(breaches, list) or not breaches:
        return clean, []
    if goal and all(isinstance(b, dict) and isinstance(b.get('voids_goal'), bool) for b in breaches):
        if any(b['voids_goal'] for b in breaches):
            return False, []
        return True, [f"{b.get('kind')}: {b.get('subject')}" for b in breaches]
    if not isinstance(owners, list):
        return clean, []
    if not all(isinstance(b, dict) and isinstance(b.get('voids_scenario'), bool) and isinstance(b.get('owner'), str) for b in breaches):
        return clean, []
    goal_owners = set(_names(owners))
    known = bodies if bodies is not None else {}

    def spares(b: dict) -> bool:
        subject = str(b.get('subject'))
        if b['voids_scenario'] or b['owner'] != _POLICY_BREACH_OWNER or subject in goal_owners or _voids_goal(subject, goal_owners, known):
            return False
        return subject in known and (goal or policy_veto_capability(known[subject]) is False)

    if not all(spares(b) for b in breaches):
        return False, []
    return True, [f"{b.get('kind')}: {b.get('subject')}" for b in breaches]


def _names(raw: object) -> list[str]:
    """A published policy-name list as a list of strings, or `[]`. Total: `transcript.json` is
    hand-authorable, so nothing guarantees the shape."""
    return [str(v) for v in raw] if isinstance(raw, list) else []


def _finite(raw: object) -> float | None:
    """``raw`` as a float, or ``None`` when it is not a finite number this can carry.

    TOTAL, and it has to be: `transcript.json` and `run.json` are hand-authorable and this reader
    decides every row. A JSON integer wider than a float raises `OverflowError` from `float()` (a
    400-digit int is a plain `int` and passes an `isinstance` guard), and `json.loads` accepts
    bare `NaN` / `Infinity`, which reach the report as a poisoned number rather than as an error.
    `bool` is excluded because it is an `int` subclass and would read as 1.0.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        value = float(raw)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _status_ladder(axis: bool | None, *, veto: bool, clean: bool | None, unreadable: str | None) -> str:
    """The four-rung precedence chain a verdict status is read by — over ONE axis, stated ONCE.

    `scenario_verdict` reads the GOAL with it — `goal_achieved`, NO veto: "did the agent do what it was asked" is not
    a question a policy outcome answers. Whether the CONVERSATION passed is not a ladder here at all: it is
    the SDK's scores block, copied onto the row. `veto` stays for a caller with a stated failure to join in.

    The rungs, in order:
      1. a STATED failure outranks everything — a goal its owner says was missed is a miss
         however the integrity read;
      2. `unclean` outranks what is left — a measurement the backend says did not hold is never read
         as an achieved goal, which is the false green this rung exists to refuse;
      3. `None` is UNMEASURED, not a failure, and is checked last because a stated verdict is
         the more specific claim;
      4. otherwise the axis says so: `passed`.
    """
    if axis is False or veto:
        return 'failed'
    if clean is False or unreadable:
        return 'unclean'
    if axis is None:
        return 'not_evaluated'
    return 'passed'


def _unknown_verdict(reason: str, clean: bool | None) -> dict:
    """The `unknown` goal block — no readable verdict, and NEVER a pass.

    `goal_status` is `unknown` for the same reason `status` is, and it is written HERE rather
    than left off: a key that appears on some payloads and not others is read by the next
    consumer with a `.get()` default, and the default for a goal rate is the silent vacuous pass.
    """
    goal: dict = {'status': 'unknown', 'goal_status': 'unknown', 'goal_achieved': None, 'goal_source': None,
                  'catastrophic_violations': [], 'decisive_violations': [], 'measurement_clean': clean}
    goal['reason'] = reason
    return goal


def scenario_verdict(per: dict, bodies: Mapping[str, object] | None = None) -> dict:
    """The backend's own per-scenario GOAL verdict, READ — never re-derived from the eval cells.

    It answers "did this scenario achieve its goal", which only the backend can say: it knows which check OWNS the goal
   , and `verdicts.json` keeps its block as `goal`. It never says whether the CONVERSATION passed: that is
    the row's `result`, `score` and `fail_reasons`, copied from the SDK's scores block (`plurai.scoring`, read through
    `verdict_events.refresh_scores`). The backend serves no `passed` or `score`, and nothing here derives one.

    `status` and `goal_status` are the same goal ladder (`status` stays for the readers that key on it):

      `passed`         the goal was achieved
      `failed`         the backend says the goal was missed
      `not_evaluated`  nothing owned the goal, or the owner reached no conclusion
      `unclean`        a measurement breach that voids the GOAL (`voids_goal`) — never read as an achieved goal
      `unknown`        no verdict block at all, or a `goal_achieved` this version cannot read

    PRECEDENCE: a STATED miss outranks an unclean measurement, and `unclean` outranks `not_evaluated`. A block whose
    `goal_achieved` key is ABSENT is `unknown`, not `not_evaluated`: a stated `goal_achieved: null` is a measurement,
    a missing field is a shape we do not understand.
    """
    block = _verdict_field(per, 'scenario_verdict')
    integrity = _verdict_field(per, 'measurement_integrity')
    clean, unreadable = _integrity_of(integrity)

    # A SENTINEL, so "the key is absent" and "the key is present and null" stay distinguishable —
    # that distinction is the whole difference between `unknown` and `not_evaluated`.
    missing = object()
    goal_achieved = block.get('goal_achieved', missing) if isinstance(block, dict) else missing
    if not (goal_achieved is None or isinstance(goal_achieved, bool)):
        # NAME what was there. A wire-shape mismatch reported as "your backend is old" would send
        # the reader to an upgrade that will not fix it.
        if block is None:
            why = 'this run was scored by a backend that publishes no scenario_verdict'
        elif not isinstance(block, dict):
            why = f'no readable scenario_verdict block ({type(block).__name__})'
        elif goal_achieved is missing:
            why = 'scenario_verdict.goal_achieved is missing'
        else:
            why = f'scenario_verdict.goal_achieved is {goal_achieved!r}, not a bool or null'
        return _unknown_verdict(f'{why}; {unreadable}' if unreadable else why, clean)
    goal_source = block.get('goal_source')
    goal_source = goal_source if isinstance(goal_source, str) and goal_source else GOAL_SOURCE_NONE
    violations = _names(block.get('catastrophic_violations'))
    # What `/finalize` stopped the run on — the catastrophic set UNION the `terminate_values` blocking set. Carried for
    # the readers that name a stop the eval cells do not show; it decides nothing here.
    decisive = _names(block.get('decisive_violations'))
    # The goal ladder reads only the breaches that void the GOAL (`_verdict_clean(goal=True)`): typed `voids_goal`, else
    # the local rule. Whether a breach voids the CONVERSATION is the SDK's status (`harness_error`), read as it is.
    goal_clean, standing = _verdict_clean(integrity, clean, block.get('target_judge_owners'), bodies, goal=True)
    goal_status = _status_ladder(goal_achieved, veto=False, clean=goal_clean, unreadable=unreadable)
    if goal_status == 'failed':
        reason = f'goal not achieved (owner: {goal_source})'
        if isinstance(skipped := block.get(REQUIRED_STEP_SKIPPED_KEY), dict):
            reason = f'goal not achieved: the agent skipped a step this scenario requires (owner: {skipped.get("owner") or goal_source})'
    elif goal_status == 'unclean':
        reason = (unreadable or 'the measurement of the goal is not trustworthy (measurement_integrity.clean=false)') + ' — never read as a pass'
        breaches = integrity.get('breaches') if isinstance(integrity, dict) else None
        if unmeasured := [f"{b.get('kind')}: {b.get('subject')}" for b in (breaches if isinstance(breaches, list) else []) if isinstance(b, dict)]:
            reason = f'{reason}; unmeasured: {", ".join(unmeasured)}'
    elif goal_status == 'not_evaluated' and isinstance(block.get(TARGET_PREMISE_UNSHOWN_KEY), str) and block[TARGET_PREMISE_UNSHOWN_KEY]:
        # The backend let neither axis decide: the situation the target is written against never arose. The
        # owner may well have answered, so "reached no conclusion" would be false beside its conclusive cell.
        reason = TARGET_PREMISE_UNSHOWN_REASON
    elif goal_status == 'not_evaluated':
        reason = ('the eval set declared no goal owner for this scenario' if goal_source == GOAL_SOURCE_NONE
                  else f'the goal owner ({goal_source}) reached no conclusion')
    else:
        reason = f'goal achieved (owner: {goal_source})'
    if decisive and set(decisive) != set(violations):
        # The server stopped the run on something the catastrophic set does not name (a `terminate_values` floor):
        # named, so a reader is never sent to the wrong check.
        reason = f'{reason}; the run was stopped on: {", ".join(decisive)}'
    if goal_source not in KNOWN_GOAL_SOURCES:
        reason = f'{reason}; unrecognized goal_source {goal_source!r}'
    if goal_source == GOAL_SOURCE_NONE and goal_achieved is not None:
        # The two fields contradict each other: nothing owned the goal, yet a verdict was stated.
        # Honour the stated verdict (it is the more specific claim) and say the block is odd.
        reason = f'{reason}; goal_source says no owner yet goal_achieved={goal_achieved!r}'
    if standing:
        reason = f'{reason}; measurement breaches that leave this goal verdict standing: {", ".join(standing)}'
    if clean is None and not unreadable:
        # A pass over an UNSTATED integrity is not a checked pass. Said on every status, because
        # a reader cannot see from here which channels the backend actually joined.
        reason = f'{reason}; measurement integrity unstated (nobody checked)'
    if unclassified := _names(block.get('unclassified_violations')):
        # The backend's OWN residue reporter: a confidently-violated cell whose TIER could not be
        # asked about (a policy since removed from the eval set). Not a red — nobody classified
        # it — but a verdict computed over one is not a complete verdict, and this row is the
        # only place a reader would ever see it.
        reason = f'{reason}; tier unclassified for: {", ".join(unclassified)}'
    if block.get('polarity_untyped'):
        # ABSENCE IS NOT FULFILMENT: nothing was polarity-gated, so every action-expecting policy
        # graded this scenario under an assumption nobody typed.
        reason = f'{reason}; scenario polarity untyped — no policy was gated on it'
    if block.get('goal_disagreement'):
        reason = f'{reason}; goal_disagreement={block["goal_disagreement"]}'
    # the backend's failing-tier reds (never the goal owner, never a decisive one); ABSENT from an older backend, never `[]`.
    served = block.get('high_severity_violations')
    high = {'high_severity_violations': _names(served)} if isinstance(served, list) else {}
    # The evals that OWN the goal, by the backend's resolver — so a report sets them apart without guessing
    # by name. ABSENT from an older backend, never `[]` (which says the set declared no owner).
    owners = block.get('target_judge_owners')
    high |= {'target_judge_owners': _names(owners)} if isinstance(owners, list) else {}
    # The two goal graders' conclusive readings when they DISAGREE, typed — `outcome_attribution`
    # reads it, and a key a consumer has to parse out of `reason` prose is not one it can branch on.
    if disagreement := conclusive_disagreement(block.get('goal_disagreement')):
        high |= {'goal_disagreement': disagreement}
    return {'status': goal_status, 'goal_status': goal_status, 'goal_achieved': goal_achieved, 'goal_source': goal_source,
            'catastrophic_violations': violations, 'decisive_violations': decisive,
            'measurement_clean': clean, 'reason': reason, **high}


#: The ONE disagreement that disputes a zero: the deterministic check failed what the target judge
#: passed. The compiled check is a NECESSARY condition of the target, so its pass beside a judge's
#: fail is the ordinary shape of a real agent failure, never a dispute between the graders.
DISPUTING_DISAGREEMENT = {'deterministic': 'fail', 'target_judge': 'pass'}


def conclusive_disagreement(value: object) -> dict | None:
    """The backend's `goal_disagreement` block, when both goal axes concluded and differ; else None.

    The deterministic check and the target judge each read `pass` or `fail`. Anything else — an
    axis that reached no conclusion, an unknown axis name, a malformed block — is not a dispute
    between two verdicts, and is not read as one.
    """
    if not isinstance(value, dict):
        return None
    readings = {axis: value.get(axis) for axis in ('deterministic', 'target_judge')}
    return readings if set(readings.values()) == {'pass', 'fail'} else None


def goal_is_measurable(goal: dict) -> bool:
    """Is this row in the "N of M goals achieved" DENOMINATOR? True iff something owned the goal.

    A scenario whose eval set declared no owner is not a failure to achieve a goal — there was
    no goal to achieve — so it leaves the denominator and is counted beside it instead.
    """
    source = goal.get('goal_source')
    return isinstance(source, str) and source != GOAL_SOURCE_NONE


#: The backend's goal STATUS → the result the "N of M goals achieved" headline counts it as. The three non-verdicts
#: (`not_evaluated`, `unclean`, `unknown`) fall through the lookup's default: neither a pass nor a failure. The row's
#: own `result` is not read off this: it is the SDK's (`scored_result`).
_GOAL_RESULT = {'passed': 'pass', 'failed': 'fail'}


def _count_goal_result(totals: dict, result: str) -> None:
    """Bump the ONE total a row's result belongs in.

    Every place a row takes its result lands here — the row that reached no eval verdict, the crash and the graded
    one — so they cannot drift into counting differently.
    `not_evaluated` is CONDITIONAL, like `incomplete`: absent rather than zeroed, because a 0
    asserts "we checked and there were none", which a run scored by a backend that predates the
    verdict block has not earned.
    """
    if result == 'not_evaluated':
        totals['not_evaluated'] = totals.get('not_evaluated', 0) + 1
    else:
        totals['passed' if result == 'pass' else 'failed'] += 1


def _turn_order(turn: str) -> tuple[int, str]:
    """Sort turn LABELS numerically — '10' after '9', not before it. Shared by `anomaly_turns`,
    the self-report turns beside it, and a stored run's index-keyed beat schedule, whose keys are
    plan positions and read the same way."""
    return len(turn), turn


def user_sim_summary(blocks: dict[str, dict]) -> dict | None:
    """The run-wide roll-up of the per-scenario blocks, or None when no transcript carried one.

    Omitted whole rather than synthesised (an older backend wrote none), so "we looked and it
    was clean" cannot be confused with "nobody looked".
    """
    if not blocks:
        return None
    kinds: dict[str, int] = {}
    for block in blocks.values():
        for found in (block.get('anomalies') or {}).values():
            for kind in (found or {}):
                kinds[str(kind)] = kinds.get(str(kind), 0) + 1
    # the backend publishes a three-valued `state` beside `clean` ('anomalies' | 'unchecked' | 'clean'):
    # `clean: false` with `state: unchecked` means the evaluator-voice judge did not answer for the
    # transcript — nothing departed from anything, and rendering it as simulator misbehaviour would
    # manufacture a finding. An older block with
    # no `state` reads `clean`.
    unchecked = sorted(name for name, block in blocks.items() if block.get('state') == 'unchecked')
    summary = {
        'measured': len(blocks),
        'clean': sum(1 for block in blocks.values() if block.get('clean')),
        'unchecked': unchecked,
        # Which scenario, and on which turns — a run whose anomalies cluster in one
        # scenario is a different finding from one where every scenario drifted.
        'anomaly_turns': {name: sorted((block.get('anomalies') or {}), key=_turn_order)
                          for name, block in sorted(blocks.items()) if not block.get('clean') and name not in unchecked},
        'anomaly_kinds': dict(sorted(kinds.items())),
    }
    # Per scenario, every beat the plan carried: the turn credited with it, or the reason nothing
    # was. Without the pairing a reader meets `turns 2,3,4` and cannot tell a beat whose
    # precondition the conversation never reached — a scenario that behaved correctly — from one
    # the turn budget cut off, which is the difference between reading the run and redrawing the
    # scenario. The KEY is omitted when no scenario carried a beat ledger, so a backend that
    # publishes none is not reported as a run whose scenarios planted nothing.
    delivery = {name: beats for name, block in sorted(blocks.items())
                if (beats := scripted_delivery(block))}
    if delivery:
        summary['scripted_delivery'] = delivery
    # The simulator's LEDGER disagreements, which the backend folds BESIDE `anomalies` (never in it): an
    # over-counted beat, a self-report the plan could not resolve. They leave `clean` alone because
    # the transcript is on schedule, and are carried here so a reader still knows which turns to
    # read. The keys are emitted whenever ANY block carries the table, empty or not, so a
    # clean run and an older backend that publishes no table never render alike (the same rule
    # `measured` follows); a present-but-unreadable table is said out loud rather than dropped.
    published = [name for name, block in blocks.items() if 'self_report' in block]
    if published:
        self_kinds: dict[str, int] = {}
        self_turns: dict[str, list[str]] = {}
        unreadable: list[str] = []
        for name, block in sorted(blocks.items()):
            if 'self_report' not in block:
                continue
            noted = block.get('self_report')
            if not isinstance(noted, dict):
                # ``null`` included: a published table this reader cannot read is not "the ledger agreed".
                unreadable.append(name)
                continue
            if not noted:
                continue
            self_turns[name] = sorted(noted, key=_turn_order)
            for found in noted.values():
                for kind in (found if isinstance(found, dict) else {}):
                    self_kinds[str(kind)] = self_kinds.get(str(kind), 0) + 1
        summary['self_report_turns'] = self_turns
        summary['self_report_kinds'] = dict(sorted(self_kinds.items()))
        if unreadable:
            print(f'build_run_json: user_sim_integrity.self_report is not a mapping on {", ".join(unreadable)} — carried as unreadable, not as clean', file=sys.stderr)
            summary['self_report_unreadable'] = unreadable
    return summary


#: The reason a STORED run gives for an uncredited beat. Its ledger is credit-only and predates
#: the typed reasons, so the honest answer is that nobody recorded one — picking a plausible
#: member of the backend's vocabulary would put a cause on the page that the run never measured.
_REASON_UNRECORDED = 'unknown'


def scripted_delivery(block: dict) -> list[dict]:
    """Each beat the scenario planted, as `{beat, kind, landed, reason}`.

    The backend publishes the ledger keyed by the beat's own stable ID, in two halves under
    `user_sim_integrity`: `delivered_beats` (`{id: {turn, kind}}`, every kind — the user's own
    premise arc, a planted knowledge question, a verified absence) and `undelivered_beats`
    (`{id: {kind, reason}}`, why nothing delivered it). The join happens here, once, so the
    report never has to.

    THE BEAT ID IS THE COORDINATE, and a turn is not. Release is by plan ORDER and precondition,
    so no beat is DUE on a turn and nothing derived here may imply one: a beat credited after
    another did not run late, and `early` is not a state the producer records. `reason` carries
    the distinction instead — a precondition the conversation never reached is a scenario that
    behaved correctly, and a beat the turn budget cut off is not, and only the producer holds
    which of the two happened.

    A STORED RUN still reads. An artifact written before the id-keyed ledger carries the
    index-keyed `scripted_delivery_schedule` / `scripted_delivery_turns` pair instead: those
    beats keep their index as the id, have no recorded `kind`, and an uncredited one reads
    `unknown` rather than a reason that run never measured.

    `[]` is "nobody compared" — no ledger in either spelling — and the report renders no table
    for it. Deliberately NOT the same output as a ledger whose beats all landed.
    """
    delivered, undelivered = block.get('delivered_beats'), block.get('undelivered_beats')
    if not isinstance(delivered, dict) and not isinstance(undelivered, dict):
        return _stored_run_delivery(block)
    landed = [{'beat': str(beat), 'kind': _beat_kind(record), 'landed': _beat_turn(record), 'reason': None}
              for beat, record in (delivered if isinstance(delivered, dict) else {}).items()]
    # The credited beats in the order the DIALOG carried them, which is the only order either map
    # can be put in: a beat id is a NAME, and neither half publishes the plan index that would
    # order the two together. The uncredited ones have no turn at all, so they follow, by id.
    landed.sort(key=lambda row: (not isinstance(row['landed'], int), row['landed'] if isinstance(row['landed'], int) else 0, row['beat']))
    missed = [{'beat': str(beat), 'kind': _beat_kind(record), 'landed': None, 'reason': _beat_reason(record)}
              for beat, record in sorted((undelivered if isinstance(undelivered, dict) else {}).items(), key=lambda item: str(item[0]))]
    return landed + missed


def _beat_kind(record: object) -> str | None:
    """A ledger entry's `kind`, or None when it carries none. None rather than a guessed
    'knowledge': the kinds fail for different reasons and mislabelling one hides that."""
    kind = record.get('kind') if isinstance(record, dict) else None
    return str(kind) if kind else None


def _beat_turn(record: object) -> object:
    """The turn a delivery was credited on. `None` when the entry is unreadable — a row that
    cannot say WHERE the beat landed renders as uncredited, and the block's own
    `delivery_ledger_degraded` note (which this report already carries) says why."""
    return record.get('turn') if isinstance(record, dict) else None


def _beat_reason(record: object) -> str:
    """Why this beat was never voiced, as the producer's own token. Passed through verbatim
    rather than mapped here: the vocabulary is the backend's and it grows, and a reason this side
    does not recognise must still reach the page."""
    reason = record.get('reason') if isinstance(record, dict) else None
    return str(reason) if reason else _REASON_UNRECORDED


def _stored_run_delivery(block: dict) -> list[dict]:
    """The same projection over the index-keyed pair a stored run carries.

    The schedule is that artifact's only record of WHICH beats the plan held, so it decides the
    rows; the due turn it names is dropped with the rest of the position vocabulary, because
    rendering it would re-introduce the early/late reading this projection exists to retire. An
    empty delivered map under a real schedule is the alarming answer for every beat — a plan
    existed and nothing was credited — not an absent one.
    """
    schedule = block.get('scripted_delivery_schedule')
    if not isinstance(schedule, dict) or not schedule:
        return []
    delivered = block.get('scripted_delivery_turns')
    delivered = delivered if isinstance(delivered, dict) else {}
    rows: list[dict] = []
    for beat in sorted(schedule, key=lambda index: _turn_order(str(index))):
        landed = delivered.get(beat)
        rows.append({'beat': str(beat), 'kind': None, 'landed': landed,
                     'reason': None if landed is not None else _REASON_UNRECORDED})
    return rows


#: How many dispatches a tool must have made before "it never once succeeded" is a claim about the
#: TOOL rather than about one transient 5xx. The sibling `family_stability` exists because "a
#: failure charged on n=1 is a different claim from one that reproduced", and this reads run-wide
#: with a much longer reach: one call declared dead attributes every zeroed family containing that
#: scenario to the environment and tells the reader its scenarios are not evidence about capability.
_DEAD_TOOL_MIN_CALLS = 2


def dead_tools(by_tool: dict[str, dict], floored: dict[str, int] | None = None) -> tuple[list[str], list[str], list[str]]:
    """`(dead, unproven, unproven_on_a_floor)` — the tools that never once succeeded, split by
    whether the run dispatched them enough times for that to be a claim about the TOOL.

    `dead` fired at least `_DEAD_TOOL_MIN_CALLS` times and the environment never ANSWERED one of
    them (`_env_unanswered` — an engine marker, a harness stamp, transport prose). A tool that
    merely REFUSED every call is not dead: it answered, and `_trail_dispatch_errored` keeps that
    finding out of this one — so does `refused_tools`, for a tool the engine typed every call's
    arguments invalid on. A tool that never worked with neither fact on every call is `unproven`
    (`unowned_failures` names it) — an error envelope alone is no owner's fact: a generated handler
    returns the same envelope from its catch-all crash branch as from a refused argument.

    `unproven` failed every dispatch it got and got FEWER than the floor. The floor's not-engaged
    state is reported, not left silent: silence here is not neutral, it rolls the tool onto the HEALTHY
    side of the ledger. A tool exercised once by one scenario (`escalate_to_human`, `issue_refund`
    — exactly the shape of a single-scenario capability) that failed its only dispatch never
    reaches the floor, so `blocked` would be empty for its family and the attribution below would
    charge the whole zero to the agent off n=1. What is reported instead: not "the tool is dead" (the run
    did not earn that), not "the tool is fine" (it never worked), but "the run never asked twice".

    Dead is a property of the ENVIRONMENT, not of the agent: a dead endpoint, an unseeded adapter,
    a handler that raises on every call. It is the one fact that separates "the agent could not do
    this" from "nothing here could have", and it is only readable because a live-mode dispatch
    reaches the counters (otherwise a run whose every csv call failed would publish `tool_errors: 0`).

    `floored` is `{tool: rows the union suppressed}` (`tool_stats`' `floor`, summed over the run),
    and THIS IS THE RULE IT BUYS. A floor can only ever mean the run dispatched MORE than `calls`
    says, so a tool with one counted failure and one suppressed row was dispatched once or twice
    and nothing here can say which. Neither verdict is available:

    * It is NOT promoted to `dead`. `dead` licenses `environment` — the strongest thing this
      script says about a family — and minting it out of rows the union could not verify would
      charge a customer's zeroed flow to their environment off arithmetic that admits it guessed.
    * It is NOT quietly left in `unproven` either, because `unproven`'s claim is "the run never
      asked twice", and on a floored count the run may well have. It stays in `unproven` — that
      is the conservative attribution, and the family still caps at `mixed` — and it is ALSO
      returned in `unproven_on_a_floor`, a SUBSET of `unproven`, so the one thing that is not
      established gets said instead of being inferred from silence.

    Third value rather than a fourth state: consumers that only branch on dead-vs-unproven keep
    working, and the caveat cannot be lost by a reader who does not know about it, because the
    renderer prints it beside the tools it qualifies.
    """
    floor = floored or {}
    never_worked = [(name, tally) for name, tally in _never_worked(by_tool) if not _invalid_args_every_call(tally)]
    under_floor = [(name, tally) for name, tally in never_worked if tally['calls'] < _DEAD_TOOL_MIN_CALLS]
    return (sorted(name for name, tally in never_worked if tally['calls'] >= _DEAD_TOOL_MIN_CALLS and _never_answered(tally)),
            sorted(name for name, tally in never_worked if tally['calls'] < _DEAD_TOOL_MIN_CALLS or not _never_answered(tally)),
            sorted(name for name, tally in under_floor if tally['calls'] + floor.get(name, 0) >= _DEAD_TOOL_MIN_CALLS))


def _never_worked(by_tool: dict[str, dict]) -> list[tuple[str, dict]]:
    return [(name, tally) for name, tally in by_tool.items() if tally['calls'] and tally['errors'] >= tally['calls']]


def _never_answered(tally: dict) -> bool:
    return tally['unanswered'] >= tally['calls']


def _invalid_args_every_call(tally: dict) -> bool:
    return tally['invalid_args'] >= tally['calls']


def _call_error(e: dict) -> str:
    """A failed call's own error, as the call streamed it: never classified, only trimmed for the report line."""
    raw = e.get('response') if e.get('response') is not None else e.get('result')
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str) if raw is not None else ''
    return text.strip()[:300]


def setup_failed_tools(run_setup: dict[str, list], by_tool: dict[str, dict], real: frozenset[str], served: frozenset[str]) -> list[dict]:
    """The tools the bench runs for REAL whose every call failed, each with its own first error quoted as-is, for the run
    report's real-tools line (`run_numbers.setup_line`). A tool that worked once is not listed; a mocked tool's error is
    the world's content, and so is the error of a real tool a seam mock serves (`served`, :func:`declared_mock_served_tools`):
    its backend is the mock. A tool dispatched fewer than `_DEAD_TOOL_MIN_CALLS` times is not listed either — one refused
    call is the shape of a planted refusal, and it stays in `unproven`. Attribution is untouched."""
    out = []
    for name in sorted(n for n in run_setup if n in real and n not in served):
        (failed, error), calls = run_setup[name], int((by_tool.get(name) or {}).get('calls') or 0)
        if calls >= _DEAD_TOOL_MIN_CALLS and failed >= calls:
            out.append({'tool': name, 'failed': failed, 'calls': calls, 'error': error})
    return out


def refused_tools(by_tool: dict[str, dict]) -> list[str]:
    """Tools that never succeeded because the mock engine typed EVERY call's arguments invalid against the
    tool's declared input schema (`_agent_invalid_args`). The one failure this script charges to the agent:
    the family that called one reads `agent` and names it (`outcome_attribution`'s `refused_tools`)."""
    return sorted(name for name, tally in _never_worked(by_tool) if _invalid_args_every_call(tally))


def unowned_failures(by_tool: dict[str, dict]) -> list[str]:
    """The SUBSET of `dead_tools`' `unproven` that the run dispatched often enough to call dead, and
    that stays unproven because no typed fact says the environment left every call unanswered."""
    return sorted(name for name, tally in _never_worked(by_tool)
                  if tally['calls'] >= _DEAD_TOOL_MIN_CALLS and not _never_answered(tally) and not _invalid_args_every_call(tally))


def _refused_by_name_every_call(name: str, by_tool: dict[str, dict], not_dispatchable: dict[str, int]) -> bool:
    """True when the harness stamped EVERY one of this name's rows `not_dispatchable` — the one fact that
    makes a name outside the declared surface an invented verb. One answered row, or a row whose failure
    was transport, says the call left."""
    calls = int((by_tool.get(name) or {}).get('calls') or 0)
    return calls > 0 and int(not_dispatchable.get(name) or 0) >= calls


def _verb_surface_unmatched_finding(names: list[str], by_tool: dict[str, dict], run_name: str) -> str | None:
    """The advisory, non-holding plugin finding for names the run DISPATCHED that the surface does not carry.

    Operator-only: a `finding:` line on stdout, never a run.json key or report text. None when this file
    runs copied out of the plugin, where the ledger module is not importable."""
    try:
        import gate_ledger  # noqa: PLC0415 - sibling script under scripts/, already on sys.path for plurai_contracts
    except ImportError:
        return None
    finding = gate_ledger.make_finding(
        gate_ledger.RUN_VERB_SURFACE_UNMATCHED,
        subject='plurai.config.yaml',
        evidence={'verbs': list(names)},
        observed={'run': run_name, 'calls': {name: int((by_tool.get(name) or {}).get('calls') or 0) for name in names}},
        remedy=('nothing to repair: the run dispatched these names and the harness refused none of them by name, so '
                'they are not invented verbs; `tools:`/`render_actions:` simply does not list them. They are left out '
                'of dead/unproven tools and out of the report.'),
        draft_ref=None,
    )
    return gate_ledger.finding_line(finding)


def _named_verbs(items: list) -> set[str]:
    """The verb names in a `tools:` / `render_actions:` LIST — both item shapes the config allows.

    A bare string is its own name; a mapping names itself under `name` (`- name: get_order`, what
    `lint_prd.py:check_tool_coverage` reads). Anything else contributes nothing rather than a
    `str()` of itself: a stringified dict would enter the surface as a verb no run can ever call,
    which reads as a declaration that is wide when it is only malformed.
    """
    names = {item.strip() for item in items if isinstance(item, str) and item.strip()}
    names |= {str(item['name']).strip() for item in items
              if isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip()}
    return names


#: Why there is no declared surface, in tokens that ride `run.json` to the renderer. SIX states
#: return None here and they do NOT share a remedy: the first two are closed by re-running with
#: `--workdir`, the next three by fixing the environment that reads the file, and the last by
#: editing the config. One message for all six ("the agent's declaration was not read … Re-run with
#: `--workdir`") would be untrue on a run that PASSED `--workdir` over a readable config declaring
#: neither key: advice already taken.
SURFACE_NO_WORKDIR = 'no_workdir'
SURFACE_NO_CONFIG = 'no_config'
SURFACE_NO_READER = 'no_reader'
SURFACE_UNREADABLE = 'unreadable'
SURFACE_NOT_A_MAPPING = 'not_a_mapping'
SURFACE_DECLARES_NEITHER = 'declares_neither'


class SurfaceReading(NamedTuple):
    """The declared verb surface — and, when there is none, WHICH of the six states this is."""

    verbs: frozenset[str] | None
    why: str | None


#: Surface notes already written this process. The reading is taken once per SCENARIO DIRECTORY
#: (`tool_stats(..., surface_known=…)`) and once more run-wide, so one fact about the ONE config
#: was printed FOUR times on a 3-scenario run. Repetition is not emphasis: the same sentence four
#: times reads as four findings, or teaches the reader to skim the stream the other notes are in.
#: The sibling `--workdir` advice was gated for exactly this.
_SURFACE_NOTED: set[str] = set()


def _surface_note(message: str) -> None:
    """Write a surface note to stderr the FIRST time it is produced, and never again.

    Keyed on the rendered message, so two different configs (or two different failures of the
    same one) still each get said — what is suppressed is only the identical repeat.
    """
    if message in _SURFACE_NOTED:
        return
    _SURFACE_NOTED.add(message)
    print(message, file=sys.stderr)


def read_surface(workdir: str | None) -> SurfaceReading:
    """:func:`declared_surface`, plus the reason its answer is None. See that docstring."""
    doc, why = _config_document(workdir)
    if not isinstance(doc, dict):
        return SurfaceReading(None, why)
    verbs: set[str] = set()
    declared = False
    tools = doc.get('tools')
    if isinstance(tools, dict):
        verbs |= {str(name) for name in tools}
        declared = True
    elif isinstance(tools, list):
        verbs |= _named_verbs(tools)
        declared = True
    renders = doc.get('render_actions')
    if isinstance(renders, list):
        verbs |= _named_verbs(renders)
        declared = True
    if not declared:
        _surface_note(f'note: {workdir}/plurai.config.yaml declares neither `tools:` nor `render_actions:` — the '
                      'agent\'s verb surface is UNKNOWN, so no failed call can be named an invented verb.')
        return SurfaceReading(None, SURFACE_DECLARES_NEITHER)
    return SurfaceReading(frozenset(verbs), None)


def declared_real_tools(workdir: str | None) -> frozenset[str]:
    """The tools `plurai.config.yaml` declares `mode: real` — they never pass through the mock, so a
    trail record is their only channel, not a bypass. Empty when no declaration is read."""
    tools = (_config_document(workdir)[0] or {}).get('tools')
    specs: Iterable[tuple[object, object]]
    if isinstance(tools, dict):
        specs = tools.items()
    elif isinstance(tools, list):
        specs = ((t.get('name'), t) for t in tools if isinstance(t, dict))
    else:
        specs = ()
    return frozenset(str(name) for name, spec in specs if isinstance(spec, dict) and spec.get('mode') == 'real')


def declared_mock_served_tools(workdir: str | None) -> frozenset[str]:
    """The tools a `mode: mock` entry names in `serves:` — real tools whose backend is that seam mock
    (`writer_readers.served_by_seam`, the one reading). Empty when no declaration is read or the reader is not importable."""
    try:
        import writer_readers  # noqa: PLC0415 - sibling script under scripts/, already on sys.path for plurai_contracts
    except ImportError:
        return frozenset()
    return writer_readers.served_by_seam(_config_document(workdir)[0] or {})


def declared_serves(workdir: str | None) -> dict[str, frozenset[str]]:
    """`{backend request: the tools its `serves:` names}` for each `tools` entry declaring a non-empty `serves` list — the
    one reading of `serves:`. Empty when no declaration is read."""
    tools = (_config_document(workdir)[0] or {}).get('tools')
    if not isinstance(tools, dict):
        return {}
    return {str(name): frozenset(str(s) for s in spec['serves'] if isinstance(s, str) and s.strip())
            for name, spec in tools.items() if isinstance(spec, dict) and isinstance(spec.get('serves'), list) and spec['serves']}


def declared_serving_seams(workdir: str | None) -> frozenset[str]:
    """The `tools` keys whose entry declares a non-empty `serves` list — the backend requests the agent's own code
    makes below the tools it names, the same predicate the backend's surface check reads. Empty when no declaration is read."""
    return frozenset(declared_serves(workdir))


def declared_backend_requests(workdir: str | None) -> frozenset[str]:
    """The tools whose calls are requests made below the agent's tools, never a call the agent chose: the `serves:`
    seams (:func:`declared_serving_seams`), and the `mode: mock` entries at a C12 seam beneath the tool (`seam:
    transport | client | entity`) that are none of the agent's own tools. The second arm reads only when the bench
    manifest records the agent's live tool list (`surfaces.production_tools.live`); otherwise it is the serves set."""
    serving = declared_serving_seams(workdir)
    tools = (_config_document(workdir)[0] or {}).get('tools')
    if not workdir or not isinstance(tools, dict):
        return serving
    try:
        import agent_inventory  # noqa: PLC0415 - sibling script under scripts/, already on sys.path for plurai_contracts
        import bench_manifest  # noqa: PLC0415 - sibling script under scripts/, already on sys.path for plurai_contracts
    except ImportError:
        return serving
    manifest = _read_json(bench_manifest.manifest_path(Path(workdir)))
    surfaces = manifest.get('surfaces') if isinstance(manifest, dict) else None
    production = surfaces.get('production_tools') if isinstance(surfaces, dict) else None
    if not isinstance(production, dict) or not isinstance(production.get('live'), list) or not production['live']:
        return serving
    own = {str(n) for key in ('names', 'live') for n in production.get(key) or []}
    own |= {str(n) for switch in production.get('switches') or [] if isinstance(switch, dict) for n in switch.get('gates') or []}
    own |= agent_inventory.not_loaded_tools(production)
    return serving | {
        str(name) for name, spec in tools.items()
        if isinstance(spec, dict) and spec.get('mode', 'mock') == 'mock' and spec.get('seam') in bench_manifest.BACKEND_SEAMS and str(name) not in own
    }


def declared_surface(workdir: str | None) -> set[str] | None:
    """Every verb `plurai.config.yaml` DECLARES — `tools` keys UNION `render_actions` — or None.

    TWO KEYS, one surface. `declared_tool_count` reads `tools:` alone, which is right for the
    `no_tools` attribution and wrong for "is this name a thing the agent has": an agent that acts
    through a UI render declares `tools: {}` BY DESIGN and lists its verbs under `render_actions`
    (SDK `plurai/render_actions.py`; this plugin's own `bench_manifest.py` already reads the key).
    A name in neither is not a blocked capability — it is a verb the agent INVENTED, and the
    engine refusing it is the finding.

    None for every state that is not a declaration (no workdir, no config, no reader, an
    unparseable file, a config declaring neither key). Silence is never an empty surface: with an
    unknown declaration nothing here may call any name invented.

    BOTH `tools:` SHAPES, which is the difference between a fix and a fix that misfires. The
    config's list form carries MAPPINGS (`- name: get_order`, the shape `lint_prd.py` reads and
    `declared_tool_count` counts), and keeping only the `str` items while still answering
    `declared: True` published an EMPTY surface for a fully-declared agent — every real tool then
    reads as an invented verb, and every family whose genuinely dead tool got zeroed out flips
    from `environment` to `agent`. An empty surface is the most dangerous answer this function
    has; it may only ever come from a config that really declares nothing.

    WHICH None it is rides :func:`read_surface`; this wrapper is the set-or-None view every
    caller that only asks "is a name declared" wants.
    """
    verbs = read_surface(workdir).verbs
    return None if verbs is None else set(verbs)


def _config_document(workdir: str | None) -> tuple[dict | None, str | None]:
    """`plurai.config.yaml` as a mapping — with the reason when it is not one.

    EVERY None THAT IS NOT THE ORDINARY ABSENCE SAYS SO, the rule `declared_tool_count` already
    follows one axis over. A file that is present and unreadable returned None in silence, and
    None here means "the surface is unknown", so one bad byte or a missing pyyaml reverted the
    invented-verb finding to the reading it replaced with nothing on stderr to say it had. No
    workdir and no file are the exception: those are the ordinary shape of a run nobody passed a
    config to, and a note on them would fire on every such run and teach the reader to skim.
    """
    if not workdir:
        return None, SURFACE_NO_WORKDIR
    path = Path(workdir) / 'plurai.config.yaml'
    if not path.is_file():
        return None, SURFACE_NO_CONFIG
    if yaml is None:
        _surface_note(f'note: pyyaml is not importable from {sys.executable} — {path} was not read, so the agent\'s '
                      'verb surface is UNKNOWN and no failed call can be named an invented verb.')
        return None, SURFACE_NO_READER
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        _surface_note(f'note: {path} could not be read ({exc}) — the agent\'s verb surface is UNKNOWN, so no failed '
                      'call can be named an invented verb.')
        return None, SURFACE_UNREADABLE
    if not isinstance(doc, dict):
        _surface_note(f'note: {path} does not parse to a mapping ({type(doc).__name__}) — the agent\'s verb surface '
                      'is UNKNOWN, so no failed call can be named an invented verb.')
        return None, SURFACE_NOT_A_MAPPING
    return doc, None


#: The reason a `tools:` declaration is UNKNOWN, in the same vocabulary `SURFACE_*` uses one axis
#: over — because it is the same six states over the same file, and typing one of them while the
#: other stayed a bare `return None` is how the no-workdir case became the invisible one.
#: `no_key` is this reading's own state: a config that parses but declares no `tools:` at all.
SURFACE_NO_KEY = 'no_key'


def declared_world_owner(workdir: object) -> str | None:
    """`world_state.owner` from `plurai.config.yaml` when it declares world types, else None (unread, or no world).
    On a mockless bench, `agent` means the harness builds and seeds its own world per conversation."""
    path = Path(str(workdir)) / 'plurai.config.yaml' if workdir else None
    if path is None or yaml is None or not path.is_file():
        return None
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    world = doc.get('world_state') if isinstance(doc, dict) else None
    if not isinstance(world, dict) or not isinstance(world.get('entity_types'), dict) or not world['entity_types']:
        return None
    owner = str(world.get('owner') or '').strip().lower()
    return owner or None


def declared_tool_reading(workdir: str | None) -> tuple[int | None, str | None]:
    """How many tools `plurai.config.yaml` DECLARES, and — when nothing typed says — WHY NOT.

    READ from the declaration, never inferred from the run: a run with no tool activity is the
    same shape whether the agent has no tools or its harness reported none, and the attribution
    below must not tell those apart by guessing. `tools: {}` (or `tools: []`) is the scan's typed
    "EMPTY BY DESIGN" — a render-action agent has nothing a mock could observe.

    THE SECOND RETURN IS THE POINT. Six states answer None and they do not share a remedy: the
    first is closed by re-running with `--workdir`, the next four by fixing the environment that
    reads the file, the last by editing the config. Every one of them lands the report on
    "unobserved — have the harness report `agent_state.tool_trail`", which on a tool-LESS agent is
    an instruction to instrument something that does not exist. The reason token rides `run.json`
    as `declared_tools_unknown` so the renderer can say "the declaration was never read" instead
    of accusing the harness, and each one is said on stderr as it is decided.

    `no_workdir` is said on stderr like every other unknown-declaration state. It is the state a
    caller reaches by forgetting one flag, and left silent it publishes `unobserved` for the flow
    families of an agent whose config declares `tools: {}`, purely because the run was built
    without `--workdir`.
    """
    if not workdir:
        print('note: no --workdir — `plurai.config.yaml` was not read, so the tool declaration is UNKNOWN. '
              'A family with no tool activity renders as `unobserved` (a measurement gap) rather than as '
              '`no_tools`, even on an agent that declares an empty tool surface. Pass --workdir to decide it.',
              file=sys.stderr)
        return None, SURFACE_NO_WORKDIR
    path = Path(workdir) / 'plurai.config.yaml'
    if not path.is_file():
        print(f'note: no {path} — the tool declaration is UNKNOWN, so a family with no tool activity renders as '
              '`unobserved` rather than as `no_tools`.', file=sys.stderr)
        return None, SURFACE_NO_CONFIG
    if yaml is None:
        print(f'note: pyyaml is not importable from {sys.executable} — {path} was not read, so the tool '
              'declaration is UNKNOWN and a family with no tool activity renders as unobserved, not as tool-less.',
              file=sys.stderr)
        return None, SURFACE_NO_READER
    try:
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        print(f'note: {path} could not be read ({exc}) — the tool declaration is UNKNOWN.', file=sys.stderr)
        return None, SURFACE_UNREADABLE
    if not isinstance(doc, dict):
        print(f'note: {path} does not parse to a mapping ({type(doc).__name__}) — the tool declaration is UNKNOWN.',
              file=sys.stderr)
        return None, SURFACE_NOT_A_MAPPING
    if 'tools' not in doc:
        print(f'note: {path} declares no `tools:` key — the tool declaration is UNKNOWN (write `tools: {{}}` to declare none).',
              file=sys.stderr)
        return None, SURFACE_NO_KEY
    tools = doc['tools']
    # The two shapes the config may take (the verifier and lint_prd accept both). A bare `tools:`
    # (YAML null) is NOT `tools: {}`: nothing was declared, and typing it `no_tools` would turn an
    # unfinished config into "the agent has no tools, by declaration".
    if isinstance(tools, (dict, list)):
        # `agent.native_tools` run in the agent's own runtime, outside `tools:`: still tools.
        agent = doc.get('agent')
        native = agent.get('native_tools') if isinstance(agent, dict) else None
        return len(tools) + (len(native) if isinstance(native, list) else 0), None
    print(f'note: {path} carries a `tools:` key with no mapping under it ({type(tools).__name__}) — the declaration is '
          'UNKNOWN; write `tools: {}` to declare an empty surface.', file=sys.stderr)
    return None, SURFACE_NOT_A_MAPPING


def declared_tool_count(workdir: str | None) -> int | None:
    """The count alone, for callers that do not act on the reason. See `declared_tool_reading`."""
    return declared_tool_reading(workdir)[0]


#: The two row states that ARE a verdict about the agent — the backend decided the scenario's goal
#: over a whole conversation. Every other state (`incomplete`, `error`, `not_evaluated`) reached
#: no such verdict, and an attribution computed over one is a claim about work nobody finished
#: measuring. See `outcome_attribution` — and the renderer's `incomplete` sentence, which must
#: name all three ways of getting there.
_DECIDED_RESULTS = frozenset({'pass', 'fail'})


def outcome_attribution(members: dict[str, list[str]], results: dict[str, str], tools_by_scenario: dict[str, set[str]],
                        dead: set[str], unproven: set[str] | frozenset[str] = frozenset(), *,
                        refused: set[str] | frozenset[str], disputed: set[str] | frozenset[str],
                        declared_tools: int | None = None) -> list[dict]:
    """Per ZEROED flow family, whether its scenarios were even testable — one row each.

    A family at 0/3 reads as an agent that cannot do that flow. It reads the same way when the
    flow's only data source was dead for the whole run, and then it is a statement about the
    environment's design (a dead csv endpoint zeroes every family that reads it, and the report
    would otherwise point the reader at the scenario or at what the agent refused). The attribution is deliberately coarse — five values, off two mechanical
    facts — because anything finer would be this script guessing at intent:

      `incomplete`   some scenario here never reached a verdict, so the family's population was
                     not fully measured and nothing about it is a capability finding
      `disputed`     every scenario here failed because the deterministic check failed what the
                     target judge passed — the suite's own graders dispute the zero, so it is a
                     suite finding to resolve before it is the agent's. Decided PER SCENARIO: a family
                     holding some disputed members reads the rest through the values below, and
                     names the disputed ones (`disputed_scenarios`)
      `no_tools`     the agent DECLARES no tools (`tools: {}`), so there is nothing here an
                     environment could have blocked — no tools to observe, by declaration
      `unobserved`   NO tool activity was observed for any scenario here — the question cannot
                     be asked, let alone answered
      `environment`  every scenario in the family called a tool that never succeeded
      `mixed`        some did — or the only tools that blocked them are `unproven`
      `agent`        none did — no scenario here touched a tool that failed run-wide

    `unobserved` is not a rounding of `agent`.
    An agent with no /mock/execute events, no transcript tool structure and no `tool_trail` has an
    EMPTY tool set for every scenario, which is `blocked == []` — the same shape as a family that
    ran against a healthy environment. Reporting that as "this is a finding about the agent" is an
    assertion from zero observations, and it is exactly the inference the recovery module's own
    warning forbids: an empty tool view is UNKNOWN, not proof.

    `no_tools` is the one case where that emptiness IS known, and it is known from the
    DECLARATION (`declared_tools == 0`, read off `plurai.config.yaml`), never from the silence.
    A render-action agent declares `tools: {}` by design; nine of its families rendered
    "unobserved — cannot say whether the environment was the cause … have the harness report
    `agent_state.tool_trail`" on a run whose config said, typed, that there was nothing to
    observe. Keyed on the declaration only: a declared surface that produced no activity stays
    `unobserved`, because there the gap is real.

    `incomplete` is the same refusal one axis along, and it is why this takes the RESULT MAP rather
    than a set of winners. The exclusion is not `result == 'pass'` alone, because `incomplete` is a
    first-class row state (see `TIME_BUDGET_EXHAUSTED`) whose whole contract is that "it is neither
    a pass nor a fail… withheld from the rate a reader divides by" — and an errored row reached no
    verdict either. A family every member of which hit TIME_BUDGET_EXHAUSTED still makes tool calls,
    so `observed` is truthy and `blocked` empty, and the row would come out `agent`: the report would render
    "❌ agent — this is a finding about the agent, Passed: 0" over scenarios this script's own
    stderr calls "graded to the turn it reached". So ANY undecided member takes the
    family out of the capability question altogether. The value is additive: the four other values keep
    their meanings and their names; consumers that do not know this one still see a value they
    must not read as `agent`.

    `unproven` tools (`dead_tools`' second half — failed every dispatch, under the floor) block a
    scenario like a dead one but cannot carry `environment`, which claims the tool is dead. A
    family whose only blocked tools are unproven therefore caps at `mixed`: the run did not earn
    "the environment zeroed this", and it certainly did not earn "the agent cannot do this" off a
    single failed dispatch.

    `environment` is NOT an acquittal and must not be rendered as one either: it says this family
    was not cleanly measured, not that the agent would have passed.

    `refused` tools (`refused_tools`) had every call's arguments typed invalid by the engine: they block
    nothing, and a family that called one is NAMED with them (`refused_tools` on the row) so an `agent`
    reading says what the agent's arguments ran into.

    `disputed` scenarios are those whose goal row carries `goal_disagreement` ==
    `DISPUTING_DISAGREEMENT` (deterministic `fail`, target judge `pass`). The backend fails the goal when
    EITHER axis conclusively fails, so that zero rests on a check the target's own judge contradicts.
    The other direction (deterministic `pass`, judge `fail`) is NOT disputed: the check is only a
    necessary condition, so it reads exactly as an undisputed failure. ADDITIVE, and second only to `incomplete`: a tool
    reading is a claim about why the agent failed, and that is not yet established.
    """
    rows: list[dict] = []
    broken = set(dead) | set(unproven)
    for family, names in sorted(members.items()):
        if not names or any(results.get(name) == 'pass' for name in names):
            continue
        undecided = sorted(name for name in names if results.get(name) not in _DECIDED_RESULTS)
        # A disputed member's zero is the suite's to settle, so the readings below are over the others.
        set_aside = sorted(name for name in names if name in disputed)
        read = [name for name in names if name not in disputed]
        blocked = sorted(name for name in read if tools_by_scenario.get(name, set()) & broken)
        by_dead = [name for name in read if tools_by_scenario.get(name, set()) & set(dead)]
        observed = [name for name in read if tools_by_scenario.get(name)]
        row: dict = {
            'family': family,
            'scenarios': len(names),
            'passed': 0,
            'attribution': ('incomplete' if undecided
                            else 'disputed' if not read
                            else 'no_tools' if not observed and declared_tools == 0
                            else 'unobserved' if not observed
                            # `environment` needs every member blocked by a tool proven dead; an
                            # `unproven` tool gets the family to `mixed` and no further.
                            else 'environment' if len(by_dead) == len(read)
                            else 'mixed' if blocked else 'agent'),
            'blocked_scenarios': blocked,
            'dead_tools': sorted({t for name in blocked for t in tools_by_scenario.get(name, set()) & set(dead)}),
        }
        # Named, not just counted: "which two of the five never finished" is the difference between
        # re-running a scenario and re-reading the whole family.
        if undecided:
            row['unmeasured_scenarios'] = undecided
        if set_aside and read:
            row['disputed_scenarios'] = set_aside
        if blocked_unproven := sorted({t for name in blocked for t in tools_by_scenario.get(name, set()) & set(unproven)}):
            row['unproven_tools'] = blocked_unproven
        if answered := sorted({t for name in names for t in tools_by_scenario.get(name, set()) & set(refused)}):
            row['refused_tools'] = answered
        rows.append(row)
    return rows


#: The SDK's `StabilityReason` value that means a scenario was GRADED and lost. The other two —
#: `incomplete` (the budget cut the dialog) and `invalid_run` (the first attempt was never a
#: measurement) — earn a stability row for the same reason but are not failures of the agent.
_GRADED_FAILURE_REASON = 'eval_failed'


def family_stability(members: dict[str, list[str]], stability: dict) -> list[dict]:
    """Per flow family, how its scenarios IN DOUBT broke down by verdict stability — one row per
    family that has any.

    A suite verdict of `5 failed` says nothing about whether any of them reproduces, and the
    per-scenario stability rows in `run_summary.json` are keyed by scenario, so a reader has to
    join them to the families by hand. Rendered per family because that is the grain the failure
    is actionable at: three stable failures in one family is a defect with an address, three
    `unmeasured` ones across three families is a run that never asked.

    WHAT `failures` COUNTS, and why it is not the row count. The runner files a stability row for
    every scenario that EARNED a retry, and its typed `reason` is one of three: `eval_failed`,
    `incomplete` (the per-scenario time budget cut the dialog — graded to the turn it reached, a
    budget decision, not a defect) and `invalid_run` (the first attempt was never a measurement).
    Counting rows would call all three failures, so a family whose scenarios ran out of time would
    read as one whose scenarios failed — the two have opposite remedies. `failures` is the
    `eval_failed` rows alone, `in_doubt` is the row count, and `also_in_doubt` names the rest —
    already split HERE so the renderer never has to know which reason means "graded and lost",
    which would be this contract's string spelled in two repositories of it.

    WHAT IT STILL CANNOT COUNT. A scenario that CRASHED gets no row at all — a retry is offered
    against a result, and there is none — so it is absent here rather than counted. The
    per-scenario table is where a crash appears; this table is silent about it BY CONSTRUCTION,
    which is why it is named for doubt rather than for failure.
    """
    rows: list[dict] = []
    for family, names in sorted(members.items()):
        marks: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for name in names:
            row = stability.get(name)
            if isinstance(row, dict):
                mark = str(row.get('stability') or 'unknown')
                marks[mark] = marks.get(mark, 0) + 1
                # An older runner wrote no typed `reason`; `unknown` keeps it out of `failures`
                # rather than guessing it into them.
                reason = str(row.get('reason') or 'unknown')
                reasons[reason] = reasons.get(reason, 0) + 1
        if marks:
            rows.append({
                'family': family,
                'failures': reasons.get(_GRADED_FAILURE_REASON, 0),
                'in_doubt': sum(marks.values()),
                'also_in_doubt': {r: n for r, n in sorted(reasons.items()) if r != _GRADED_FAILURE_REASON},
                'stability': dict(sorted(marks.items())),
            })
    return rows


#: The runner's `host_toolchain` keys that never reach run.json (see `run_integrity`).
_HOST_TOOLCHAIN_UNCOPIED = frozenset({'stamp', 'acknowledged'})


#: The `measurement_unclean_because` channel a trial that could not run adds (C7 `invalid_trials`): its scenarios
#: measured nothing, so the run did not measure what it claims, however clean its graded rows were.
INVALID_TRIALS_CHANNEL = 'invalid_trials'


def invalid_trials(summary: Mapping) -> int:
    """Trials that could not run on our side: C7 `totals.invalid_trials` when the runner states it, else the trials
    block's per-scenario `invalid` entries. An agent crash is a valid, failed trial (`agent_raised`), never counted."""
    totals = summary.get('totals')
    stated = totals.get('invalid_trials') if isinstance(totals, Mapping) else None
    if isinstance(stated, int) and not isinstance(stated, bool):
        return stated
    trials = summary.get('trials') if isinstance(summary.get('trials'), Mapping) else {}
    rows = trials.get('scenarios') if isinstance(trials.get('scenarios'), Mapping) else {}
    return sum(1 for row in rows.values() if isinstance(row, Mapping)
               for entry in row.get('invalid') or [] if isinstance(entry, Mapping) and entry.get('reason') != AGENT_RAISED)


def agent_host_copy(workdir: Path | None) -> dict | None:
    """`{route_smoke, granted}` when the bench's manifest declares its tools intercepted at the agent's host
    (`harness.tool_interception.at: agent_host`): the agent ran as a copy there, which the route smoke brought up, and
    `granted` (`.host_effects_consent`, its grants that name no tool left out) names what was let run live inside it. None else."""
    doc = _read_json(workdir / 'bench_manifest.json') if workdir is not None else None
    hosted = ((doc.get('harness') if isinstance(doc, dict) else None) or {}).get('tool_interception')
    if not isinstance(hosted, dict) or hosted.get('at') != 'agent_host' or not str(hosted.get('route_smoke') or '').strip():
        return None
    try:
        import host_effects  # noqa: PLC0415 - scripts/host_effects.py: the one reader of the operator's grants
    except ImportError:  # this file COPIED out of the plugin
        granted: list[str] = []
    else:
        granted = sorted(host_effects.granted(workdir) - host_effects.NON_TOOL_GRANTS)
    return {'route_smoke': str(hosted['route_smoke']), 'granted': granted}


def run_integrity(out: Path) -> dict | None:
    """The runner's own run-level integrity signals, COPIED from `run_summary.json`.

    Each key is surfaced because a report silent on a top-level `measurement_clean: false`, a
    corpus drift check that never ran for want of a baseline, or an undeclared egress posture
    reads as a clean measurement.

    COPIED, never recomputed. `measurement_clean` folds three channels the runner can see and
    this script cannot, and a second construction of it here would drift from the one the run
    recorded. One fold is added, never removed: a trial that could not run (:func:`invalid_trials`)
    makes it false under :data:`INVALID_TRIALS_CHANNEL`. `measurement_unclean_because` is what makes the value readable: without it a
    reader sees `false` beside a per-scenario integrity map in which every row is clean and has
    no way to learn that it was the USER-SIM block next door that drove it.

    None when there is no roster at all (an older runner), so "clean" and "nobody looked" do not
    render alike.
    """
    doc = _read_json(out / 'run_summary.json')
    if not isinstance(doc, dict):
        return None
    block: dict = {}
    if 'measurement_clean' in doc:
        block['measurement_clean'] = doc['measurement_clean']
        # Both channel lists, copied side by side and never merged: `..._unclean_because` names
        # what BREACHED (fix it), `..._unchecked_because` names what could NOT BE MEASURED on
        # some row (read it) — the reason a `null` verdict is a coverage gap (the evaluator-voice
        # judge did not answer), rather than an older runner.
        for key in ('measurement_unclean_because', 'measurement_unchecked_because'):
            channels = doc.get(key)
            if isinstance(channels, list) and channels:
                block[key] = [str(channel) for channel in channels]
    if invalid_trials(doc):
        block['measurement_clean'] = False
        block['measurement_unclean_because'] = [*block.get('measurement_unclean_because', []), INVALID_TRIALS_CHANNEL]
    # WHICH rows the backend scored over a world state it did not certify. It sits here, beside
    # the per-row maps `measurement_clean` folds, because it is the same kind of fact — whether
    # the run measured what it claims to — and because it is the only place the distrust survives,
    # since the SDK keeps those rows' cells. The empty map is published
    # too: "the runner looked and found none" is a different fact from "the runner never said",
    # which is the distinction the whole block exists to keep.
    distrusted = untrustworthy_scenarios(out)
    if distrusted is not None:
        block['untrustworthy_scenarios'] = dict(sorted(distrusted.items()))
    # `host_toolchain`: what this host gave the agent of what its own files declare it needs, resolved by the
    # runner BEFORE the run. Copied, never re-derived — all but the runner's headline stamp (and an older
    # runner's acknowledgement), which stay in run_summary.json: whether a run started over a gap is the
    # plugin's own decision, never run.json content.
    for key in ('scan_contract_drift', 'egress', 'scenario_set', 'host_effects', 'host_toolchain'):
        if isinstance(doc.get(key), dict):
            block[key] = doc[key]
    if isinstance(block.get('host_toolchain'), dict):
        block['host_toolchain'] = {k: v for k, v in block['host_toolchain'].items() if k not in _HOST_TOOLCHAIN_UNCOPIED}
    # WHICH eval set revision graded this run (the runner copies the backend finalize's content
    # digest over the set's per-policy fingerprints). Copied, never derived: it is the run side
    # of the lane pin's `eval-revision-moved` comparison. Absent on an older runner.
    if isinstance(doc.get('eval_revision'), str) and doc['eval_revision']:
        block['eval_revision'] = doc['eval_revision']
    if isinstance(doc.get('eval_revisions'), dict) and doc['eval_revisions']:
        block['eval_revisions'] = {str(k): str(v) for k, v in doc['eval_revisions'].items()}
    # Per scenario: `stable` / `flaky` / `unmeasured`, and the reason it was not retried. A
    # failure charged on n=1 is a different claim from one that reproduced, and the runner is the
    # only side that knows which it was.
    stability = doc.get('stability')
    if isinstance(stability, dict) and stability:
        block['stability'] = {str(name): row for name, row in sorted(stability.items()) if isinstance(row, dict)}
    # A world conversations share by the user's opt-in: the runner's `shared_world` named risk, and
    # `stability_comparable: false` with its reason, because a retry or another trial ran on other writes.
    risks = [r for r in doc['named_risks'] if isinstance(r, dict)] if isinstance(doc.get('named_risks'), list) else []
    # D6: seeded starting rows on a world the run cannot read back — the one case landing cannot be measured.
    if unobserved := unobserved_seed_scenarios(out):
        risks.append({'kind': UNOBSERVED_SEED_KIND, 'scenarios': unobserved})
    if risks:
        block['named_risks'] = risks
    if doc.get('stability_comparable') is False:
        block['stability_comparable'] = False
        block['stability_comparable_reason'] = doc.get('stability_comparable_reason')
    return block or None


def relabel_rescored_eval_revision(block: dict, dirs: Sequence[Path]) -> str | None:
    """Make `run_integrity.eval_revision` name the revision the CELLS IN THIS FILE were scored at.

    `run_summary.json` records the revision the RUN was scored at, and each scenario's cells come from its
    `verdicts.json`, which a rescore moves to another revision (`judged_against.eval_revision`). A rescored run
    must not ship the run's revision as a label over somebody else's verdicts.

    The run's value is not discarded, it is RENAMED to `eval_revision_at_run`: which revision the
    live run used is a real fact and the only thing that makes "this was rescored" legible.

    THREE OUTCOMES, and the third is why this returns a note rather than always writing a scalar:

    * no scenario's verdicts were rescored — nothing is relabelled, and a run that was never
      rescored keeps exactly the bytes it had;
    * every scenario ends up at ONE revision — the scalar says so;
    * they DISAGREE (some scenarios rescored, some not, or two rescore rounds) — then no scalar is
      true, so the per-scenario `eval_revisions` map carries it and the scalar is dropped rather
      than made to pick a winner. Absence is already a state readers handle (an older runner writes
      no revision at all); a wrong scalar is not.
    """
    graded = {d.name: doc for d in dirs if verdicts_file.graded(doc := read_verdicts(d))}
    rescored = {name: rev for name, doc in graded.items()
                if verdicts_file.rescored(doc) and isinstance(rev := verdicts_file.judged_against(doc).get('eval_revision'), str) and rev}
    if not rescored:
        return None
    run_rev = block.get('eval_revision')
    at_run = run_rev if isinstance(run_rev, str) else None
    # Every graded scenario, at the revision ITS cells came from: its rescore's, or the run's.
    per_scenario = {name: rev for name in graded if (rev := rescored.get(name, at_run))}
    if at_run:
        block['eval_revision_at_run'] = at_run
    distinct = set(per_scenario.values())
    if len(distinct) == 1:
        block['eval_revision'] = distinct.pop()
        if isinstance(block.get('eval_revisions'), dict):
            # The run-time map would otherwise still name the revision every scenario was scored at BEFORE the rescore.
            block['eval_revisions'] = {**block['eval_revisions'], **dict(sorted(per_scenario.items()))}
        return None
    block.pop('eval_revision', None)
    block['eval_revisions'] = {**(block.get('eval_revisions') or {}), **dict(sorted(per_scenario.items()))}
    return (
        f'the {len(per_scenario)} scenario(s) in this run were scored at {len(distinct)} different eval revisions '
        f'({", ".join(sorted(distinct))}) because a rescore covered some and not others — `run_integrity.eval_revision` '
        'is therefore NOT written and `eval_revisions` carries the per-scenario truth.'
    )


#: What a quarantined stability row says instead of its marks. The row is KEPT — its `reason` and
#: its `stability` verdict are still the runner's, and deleting them would lose the only record
#: that a retry happened at all — but every PER-POLICY claim on it is withdrawn.
#:
#: ONE token, and the DETAIL beside it names which of the causes below fired. A second word for
#: "the runner itself disproved these marks" would be a second vocabulary for one state — the row
#: has no marks either way, and a reader who branches on the token would branch on the cause.
STABILITY_STALE = 'stale_rescore'
#: `marks_withdrawn` on a re-scored scenario whose every graded trial is now judged at one eval revision (the trials
#: refold): the counts are current and comparable; only the run-time per-policy marks were not re-derived. Re-running
#: fixes nothing.
STABILITY_REFOLDED = 'refolded_one_revision'

#: The cross-trial aggregate a FINAL run's runner writes into `run_summary.json` → `trials`.
TRIALS_SCHEMA = 'trials-v1'
#: A listed trial dir: `''` is `$OUT` itself (trial 1), the rest are plain names beneath it.
_TRIAL_DIR = re.compile(r'[A-Za-z0-9_-]*')


def trials_block(out: Path) -> dict | None:
    """`run_summary.json`'s `trials` — the SDK owns every number in it, from each trial's `verdicts.json`;
    re-deriving it here from `run.json` rows would read decorated cell names.

    COPIED verbatim unless a listed trial's `verdicts.json` was changed after the run (`verdicts_file.rescored`).
    Then the k/N are about a scoring that no longer exists, and the block is RECOMPUTED by the SDK's own
    aggregator over the same files (:func:`refold_trials`). Where that is impossible (plurai unreachable) the
    scenario's entry is WITHDRAWN instead, and so are the run-level numbers pooled over it. `run_summary.json`
    itself is the run-end snapshot and is never rewritten. Unlisted `trial_k/` dirs are never read.
    """
    doc = _read_json(out / 'run_summary.json')
    block = doc.get('trials') if isinstance(doc, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict) or block.get('schema') != TRIALS_SCHEMA:
        print(f'note: run_summary.json carries a `trials` block this script cannot read (schema '
              f'{block.get("schema") if isinstance(block, dict) else type(block).__name__!r}) — not copied', file=sys.stderr)
        return None
    rows = scenarios if isinstance(scenarios := block.get('scenarios'), dict) else {}
    dirs = [d for d in block.get('dirs') or [] if isinstance(d, str) and _TRIAL_DIR.fullmatch(d)]
    rescored: dict[str, dict[int, dict]] = {}
    for name in rows:
        for index, sub in enumerate(dirs):
            if verdicts_file.exists(out / sub / name) and verdicts_file.rescored(doc := read_verdicts(out / sub / name)):
                rescored.setdefault(name, {})[index] = doc
    if not rescored:
        return block
    refolded, why = refold_trials(out, block, dirs, rescored)
    if refolded is not None:
        print(f'note: the cross-trial counts on {len(refolded["rescored"]["scenarios"])} scenario(s) were RECOMPUTED from '
              f'their changed {verdicts_file.FILENAME}: {", ".join(refolded["rescored"]["scenarios"])}.', file=sys.stderr)
        return refolded
    stale = sorted(rescored)
    for name in stale:
        trials_hit = ', '.join(dirs[i] or 'trial 1' for i in sorted(rescored[name]))
        rows[name] = {'withdrawn': STABILITY_STALE, 'withdrawn_detail': f'{trials_hit} was re-scored after the run'
                      + (f' and the counts could not be recomputed: {why}' if why else '')}
    run = pooled if isinstance(pooled := block.get('run'), dict) else {}
    # Which scenario failed a critical or failing-tier check is the scores block's (`run.json` → `scores`, refreshed by
    # the SDK after every write), never this block's: the SDK publishes no severity axis here.
    block['run'] = {'scenarios': run.get('scenarios'), 'withdrawn': STABILITY_STALE, 'withdrawn_scenarios': stale,
                    'inconsistent_scenarios': [s for s in run.get('inconsistent_scenarios') or [] if s not in stale]}
    print(f'note: the cross-trial counts on {len(stale)} scenario(s) were WITHDRAWN — a trial was re-scored after '
          f'the run: {", ".join(stale)}. Re-run to refresh them.', file=sys.stderr)
    return block


#: The SDK aggregator's names `refold_trials` calls — all present, or no recompute.
_SDK_TRIALS_NAMES = ('cells_from_run_dir', 'aggregate_trials')


def _sdk_trials() -> object | None:
    """`plurai.trials` when it carries every function the recompute needs, else None — SHARED, NEVER RETYPED
    (the same stance as :func:`_sdk_mark_classifier`): the aggregate is the SDK's arithmetic, and a copy
    here would be a second implementation of it inside the consumer."""
    try:
        from plurai import trials  # noqa: PLC0415 - optional, and only on the recompute path
    except Exception:  # noqa: BLE001 - a half-installed plurai must not kill the whole run.json build
        return None
    return trials if all(callable(getattr(trials, name, None)) for name in _SDK_TRIALS_NAMES) else None


def refold_trials(out: Path, block: dict, dirs: Sequence[str], rescored: Mapping[str, Mapping[int, dict]]) -> tuple[dict | None, str]:
    """`(block recomputed by the SDK's aggregator, '')`, or `(None, why)` when plurai cannot be used here.

    Every listed trial is re-read with `plurai.trials.cells_from_run_dir` — the runner's own reader, over the same
    `verdicts.json` files this script reads — and re-aggregated with `aggregate_trials`. No overlay and no parity
    check: the stored block and the recompute read one source, so they differ exactly where a verdict changed.
    """
    sdk = _sdk_trials()
    if sdk is None:
        return None, 'the SDK aggregator (plurai.trials) is not importable here'
    cells_by_trial = [dict(sdk.cells_from_run_dir(out / sub)) for sub in dirs]
    fresh = sdk.aggregate_trials(cells_by_trial, run_name='', measurement=[(None, ())] * len(cells_by_trial))
    revisions = sorted({rev for by in rescored.values() for doc in by.values()
                        if isinstance(rev := verdicts_file.judged_against(doc).get('eval_revision'), str) and rev})
    # The scenarios whose every graded trial is judged NOW against one eval revision and one ground truth: their trials
    # compare one eval set. What each trial's `verdicts.json` is judged against, re-scored or not: a trial nobody
    # re-scored is still at its run-time revision, and that can be the very revision the others were re-scored at.
    one_revision: dict[str, str] = {}
    for name in rescored:
        docs = [read_verdicts(out / sub / name) for sub in dirs if verdicts_file.exists(out / sub / name)]
        frames = {(judged.get('eval_revision'), judged.get('gt_fingerprint'))
                  for doc in docs if verdicts_file.graded(doc) for judged in (verdicts_file.judged_against(doc),)}
        if (len(docs) == len(dirs) and len(frames) == 1 and isinstance(rev := next(iter(frames))[0], str) and rev):
            one_revision[name] = rev
    return {**block, 'scenarios': fresh['scenarios'], 'run': fresh['run'],
            'rescored': {'scenarios': sorted(rescored), 'eval_revisions': revisions, 'one_revision': dict(sorted(one_revision.items())),
                         'detail': "these scenarios' counts were re-aggregated by the SDK's own aggregator over each "
                                   f"trial's current {verdicts_file.FILENAME}, after a rescore or a correction changed one"}}, ''


#: The SDK's per-regression replay block (`run_summary.json` → `regression_replays`) and its per-check rate
#: over every judged turn (→ `policy_turn_rates`). The SDK owns every number in both; this file copies them.
REGRESSION_REPLAYS_SCHEMA = 'regression-replays-v1'
POLICY_TURN_RATES_SCHEMA = 'policy-turn-rates-v1'
#: A regression folder's record-time rate (`replays.json`, `regressions.py confirm`).
REGRESSION_CONFIRM_SCHEMA = 'regression-confirm-v1'
#: Where the regression-only step's owners and the record step's confirm records live, under `$ART`.
REGRESSION_OWNERS_FILENAME = 'regression_owners.json'
REGRESSION_PICKS_DIRNAME = 'regression_picks'
#: Where a regression row's owner came from: the eval set's ledger; else the folder's ROLES.md probe, either
#: because the ledger was read and named no owner for the folder, or because it was never read (no saved owners at all).
OWNER_LEDGER, OWNER_PROBE_UNOWNED, OWNER_PROBE_NO_LEDGER = 'ledger', 'probe_unowned', 'probe_no_ledger'
#: The owners file's schema (`regressions.py ledger-owners`, refreshed before every run).
REGRESSION_OWNERS_SCHEMA = 'regression-owners-v1'
#: A folder kept as "seen once" whose replay batch measured this many cells at its cut without one red
#: is not pinned there: it is read as a recurring issue type, not a pinned regression.
NOT_PINNED_MIN_MEASURED = 5
REGRESSION_PINNED, REGRESSION_NOT_PINNED = 'pinned', 'not_pinned'
#: A folder whose owner was not applicable on every valid replay, decided on neither axis, with its state restored: it can
#: never fail, so it is named for the operator to re-cut or retire, never kept as pinned.
REGRESSION_MEASURES_NOTHING = 'measures_nothing'
#: The SDK's `ended_early` on a single-hop regression replay: the run stopped after the turn under test.
REPLAY_CUT_REASON = 'replay_cut'
#: The goal reason a single-hop replay row carries: that turn is the situation under test, so it is never "ended early,
#: before the situation came up".
REPLAY_CUT_GOAL_REASON = 'a replay of the recorded turn only; its goal is not graded'

try:
    import regressions as _regressions  # the regressions directory's one reader (scripts/regressions.py)
except ImportError:  # this file COPIED out of the plugin
    _regressions = None


def _schema_block(doc: object, key: str, schema: str) -> dict | None:
    """`doc[key]` when it carries `schema`; None otherwise — an unknown or absent schema is no section, never a 0."""
    block = doc.get(key) if isinstance(doc, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict) or block.get('schema') != schema:
        print(f'note: run_summary.json carries a `{key}` block this script cannot read (schema '
              f'{block.get("schema") if isinstance(block, dict) else type(block).__name__!r}) — not copied', file=sys.stderr)
        return None
    return block


def trial_dirs(out: Path) -> list[str]:
    """The run's listed trial dirs (`run_summary.json` → `trials.dirs`, `''` being `$OUT` itself), in the order every SDK
    `by_trial` list follows; `['']` for a run with no trials block."""
    doc = _read_json(out / 'run_summary.json')
    block = doc.get('trials') if isinstance(doc, dict) else None
    dirs = [d for d in (block.get('dirs') if isinstance(block, dict) else None) or [] if isinstance(d, str) and _TRIAL_DIR.fullmatch(d)]
    return dirs or ['']


def _refolded(out: Path, block: dict | None, aggregate: str) -> dict | None:
    """`block` RECOMPUTED by the SDK's own `plurai.trials.<aggregate>` over every listed trial's cells when a listed trial's
    `verdicts.json` was changed after the run (the :func:`trials_block` stance: the copy is about a scoring that no longer
    exists); the copy otherwise. Where plurai cannot recompute it, the copy is kept, marked `basis: copied`, and stderr says so."""
    if block is None:
        return None
    doc = _read_json(out / 'run_summary.json')
    names = list(doc.get('scenarios') or {}) if isinstance(doc, dict) and isinstance(doc.get('scenarios'), dict) else []
    dirs = trial_dirs(out)
    if not any(verdicts_file.exists(out / sub / name) and verdicts_file.rescored(read_verdicts(out / sub / name)) for sub in dirs for name in names):
        return block
    sdk = _sdk_trials()
    if sdk is None or not callable(fold := getattr(sdk, aggregate, None)):
        print(f'note: a verdict of this run changed after it, and plurai.trials.{aggregate} is not importable here — the copied '
              f'block is kept (`basis: copied`)', file=sys.stderr)
        return {**block, 'basis': 'copied'}
    return {**fold([dict(sdk.cells_from_run_dir(out / sub)) for sub in dirs]), 'rescored': True}


def policy_turn_rates_block(out: Path) -> dict | None:
    """`run_summary.json`'s `policy_turn_rates`: each check's red rate over the turns it judged — COPIED, or refolded by the
    SDK over the corrected verdicts (:func:`_refolded`)."""
    return _refolded(out, _schema_block(_read_json(out / 'run_summary.json'), 'policy_turn_rates', POLICY_TURN_RATES_SCHEMA),
                     'aggregate_policy_turn_rates')


#: Which replays a regression's entry reads (`regression_replays.regressions[<scenario>].population`): the run's own,
#: which its audit corrected and its attribution linked, or the regression-only step's, which nothing audited.
POPULATION_RUN, POPULATION_REPLAY_STEP = 'run', 'replay_step'


def regression_replays_block(out: Path, replays_dir: Path | Sequence[Path] | None) -> dict | None:
    """Every regression's SDK `regression_replays` entry — this run's OWN when it carried the regression (its replays are
    the ones the audit corrected and the attribution linked; refolded over the corrected verdicts, :func:`_refolded`), else
    the regression-only step's from `replays_dir`, COPIED. Each entry names its `population`.

    Several dirs (the single-hop replays, then the full replays a regression fell back to): each regression not in this
    run takes its entry from the LAST dir that replayed it, carrying that dir's `replay_mode`."""
    dirs = [replays_dir] if isinstance(replays_dir, Path) else list(replays_dir or [])
    blocks: list[tuple[dict, str]] = []
    for source in dirs:
        if not (source / 'run_summary.json').is_file():
            print(f'note: --regression-replays {source} holds no run_summary.json — no regression section from it', file=sys.stderr)
            continue
        if (block := _schema_block(_read_json(source / 'run_summary.json'), 'regression_replays', REGRESSION_REPLAYS_SCHEMA)) is not None:
            blocks.append((block, POPULATION_REPLAY_STEP))
    own = _schema_block(_read_json(out / 'run_summary.json'), 'regression_replays', REGRESSION_REPLAYS_SCHEMA) \
        if (out / 'run_summary.json').is_file() else None
    if own is not None and (own.get('regressions') or not blocks):
        blocks.append((_refolded(out, own, 'aggregate_regression_replays') or own, POPULATION_RUN))
    if not blocks:
        return None
    regressions: dict = {}
    for block, population in blocks:
        regressions.update({name: {**entry, 'population': population} if isinstance(entry, dict) else entry
                            for name, entry in (block.get('regressions') or {}).items()})
    return {**blocks[-1][0], 'regressions': regressions}


def _folder_for(scenario: str, names: Sequence[str]) -> str | None:
    """The folder a replayed scenario is: its exact name; else the one folder whose name it is a prefix of (a
    legacy name the backend cut at its cap); else none — two folders sharing the prefix are not guessed between."""
    if scenario in names:
        return scenario
    prefixed = [name for name in names if name.startswith(scenario)]
    return prefixed[0] if len(prefixed) == 1 else None


def _folder_facts(folder: Path) -> tuple[str | None, str | None, dict | None]:
    """`(title, probe policy, replays.json)` of one regression folder — each None when absent or unreadable."""
    roles = folder / 'ROLES.md'
    text = roles.read_text(encoding='utf-8') if roles.is_file() else ''
    heading = '## Issue:'
    title = next((line.strip()[len(heading):].strip() for line in text.splitlines() if line.strip().startswith(heading)), None)
    probe = _regressions.probe_policy(text) if _regressions is not None and text else None
    try:
        recorded = json.loads((folder / 'replays.json').read_text(encoding='utf-8')) if (folder / 'replays.json').is_file() else None
    except (OSError, ValueError):
        recorded = None
    return title or None, probe, recorded if isinstance(recorded, dict) and recorded.get('schema') == REGRESSION_CONFIRM_SCHEMA else None


def _regressions_on_disk(workdir: Path | None) -> list[Path]:
    root = workdir / 'regressions' if workdir is not None else None
    if root is None or not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and d.name.startswith('regression_'))


def _regression_owners(workdir: Path | None) -> tuple[dict[str, str], bool]:
    """`({folder: ledger owner}, whether the ledger was read)` from `$ART/regression_owners.json`. No file, or one this
    script cannot read, is "the ledger was never read" — said so on every row it heads, never silently."""
    path = workdir / 'artifacts' / REGRESSION_OWNERS_FILENAME if workdir is not None else None
    try:
        doc = json.loads(path.read_text(encoding='utf-8')) if path is not None and path.is_file() else None
    except (OSError, ValueError):
        print(f'note: {path} could not be read — every regression row takes its probe, labelled as unread', file=sys.stderr)
        doc = None
    if not isinstance(doc, dict) or doc.get('schema') != REGRESSION_OWNERS_SCHEMA or not isinstance(doc.get('owners'), dict):
        return {}, False
    return {str(k): str(v) for k, v in doc['owners'].items() if isinstance(v, str) and v.strip()}, True


def measured_replays(cell: object) -> int:
    """The replays of one SDK cell that measured its check: failed + passed, less the passes whose premise never arose
    (``trigger_absent``, counted inside ``passed`` by the SDK). The one rule for the run.json status, the report's
    customer line and the clean-draw ledger (``regressions._clean_this_run``)."""
    if not isinstance(cell, dict):
        return 0
    return int(cell.get('passed') or 0) + int(cell.get('failed') or 0) - int(cell.get('trigger_absent') or 0)


#: An SDK cell's rating pairs (`plurai.trials.rated_cell`): recomputed when a reading moves the cell's counts.
_RATING_KEYS = ('wilson95_failed', 'clean_replays_to_clear', 'wilson80_failed', 'clean_replays_to_decide')


def _restated(cell: object, said: Mapping) -> object:
    """An SDK axis cell restated on the folder's replay reading (`regressions.replay_reading`): `failed` = caught, `passed` =
    measured less caught, the replays that measured nothing moved to `na` (a pass whose premise never arose) or
    `inconclusive` (a whole replay that met the cut with less state), its label the reading's; the SDK's own counts and
    label kept under `sdk`. The rating pairs are recomputed by `plurai.trials.rated_cell` when the counts moved (the SDK's
    kept, and `basis: copied` said on the reading, where plurai is not importable). A cell that is not a dict is returned as is."""
    if not isinstance(cell, dict):
        return cell
    failed, passed, absent = int(cell.get('failed') or 0), int(cell.get('passed') or 0), int(cell.get('trigger_absent') or 0)
    caught, measured = int(said['caught']), int(said['measured'])
    out = {**cell, 'failed': caught, 'passed': measured - caught, 'trigger_absent': 0, 'na': int(cell.get('na') or 0) + absent,
           'inconclusive': int(cell.get('inconclusive') or 0) + max(0, failed + passed - absent - measured), 'label': said['label'],
           'sdk': {key: cell.get(key) for key in ('passed', 'failed', 'trigger_absent', 'label')}}
    if (caught, measured) != (failed, failed + passed):
        sdk = _sdk_trials()
        rate = getattr(sdk, 'rated_cell', None) if sdk is not None else None
        if callable(rate):
            rated = rate({'passed': measured - caught, 'failed': caught})
            out.update({key: rated.get(key) for key in _RATING_KEYS if key in cell or key in rated})
        else:
            out['rating_basis'] = 'copied'
    return out


def _run_reading(out: Path | None) -> object | None:
    """`regressions.RunReading` of this run — its standing links, the one reader of "this red is this finding" — or None
    when there is no run dir or it cannot be read (said on stderr: the reading then links nothing)."""
    if out is None or _regressions is None:
        return None
    try:
        return _regressions.RunReading(out)
    except Exception as exc:  # noqa: BLE001 - a run that cannot be read links nothing; the rows still build
        print(f'note: the attribution of {out.name} could not be read ({type(exc).__name__}: {exc}) — the regression rows link nothing', file=sys.stderr)
        return None


def regression_rows(replays: dict, workdir: Path | None, out: Path | None = None) -> list[dict]:
    """One row per replayed regression: `{folder, scenario, title, owner, owner_source, at_cut, anywhere, clear_axis, recorded, status, restore_reduced, replay_caveat, reading}`.

    `reading` is THE replay reading (`regressions.replay_reading`): over this run's own audited replays when it carried the
    folder (`population: run`, its standing links read off `out`), else the regression-only step's. Its owner is the check
    the run's links tie to the folder's own finding, else the one a run before stored, else the eval set's ledger owner
    (`$ART/regression_owners.json`), else the folder's ROLES.md probe — `probe_unowned` when the ledger was read and named
    none for it, `probe_no_ledger` when it was never read; `at_cut` / `anywhere` are that owner's SDK cells restated on the
    reading (:func:`_restated`; the SDK's counts kept under `sdk`), so every surface reads one k of N.
    `clear_axis` is the SDK's name for the cell a clear verdict reads (`at_cut` for a resumed replay, `anywhere`
    for one played from its opener; `at_cut` when the block names none), unless the folder's `replays.json` pins one.
    `status` is `not_pinned` for a folder recorded as seen once whose batch measured
    :data:`NOT_PINNED_MIN_MEASURED` cells on that axis without a red — it moves to the recurring issue types — and
    :data:`REGRESSION_MEASURES_NOTHING` for an owned one whose replays measured it on neither axis (:func:`measured_replays`:
    N/A and trigger-absent passes measure nothing) on at least one valid replay and left none undecided, unless one
    resumed with less state.
    """
    folders = {d.name: d for d in _regressions_on_disk(workdir)}
    owners, ledger_read = _regression_owners(workdir)
    probe_source = OWNER_PROBE_UNOWNED if ledger_read else OWNER_PROBE_NO_LEDGER
    run = _run_reading(out) if any(isinstance(e, dict) and e.get('population') == POPULATION_RUN
                                   for e in (replays.get('regressions') or {}).values()) else None
    rows = []
    for scenario, entry in sorted((replays.get('regressions') or {}).items()):
        if not isinstance(entry, dict):
            continue
        name = _folder_for(scenario, list(folders))
        title, probe, recorded = _folder_facts(folders[name]) if name else (None, None, None)
        owner, source = (owners[name], OWNER_LEDGER) if name in owners else ((probe, probe_source) if probe else (None, None))
        reading = None
        if _regressions is not None:
            own = entry.get('population') == POPULATION_RUN and out is not None
            reading = _regressions.replay_reading(entry, folder=folders.get(name) if name else None, dirs=trial_dirs(out) if own else (),
                                                  owners=owners, ledger_read=ledger_read, run=run if own else None)
            owner, source = reading['owner'], reading['owner_source']
        cells = (entry.get('policies') or {}).get(owner) if owner else None
        if reading is not None and isinstance(cells, dict):
            cells = {axis: _restated(cells.get(axis), reading[axis]) for axis in ('at_cut', 'anywhere')}
            if any(isinstance(c, dict) and c.get('rating_basis') == 'copied' for c in cells.values()):
                reading['basis'] = 'copied'
        at_cut = cells.get('at_cut') if isinstance(cells, dict) else None
        # A folder pinned on an axis (`regressions.py repin`: its owner cannot decide at the cut) clears there, whatever the SDK names.
        pinned = (recorded or {}).get('clear_axis')
        axis = pinned if pinned in ('at_cut', 'anywhere') else entry.get('clear_axis') if entry.get('clear_axis') in ('at_cut', 'anywhere') else 'at_cut'
        cleared_on = cells.get(axis) if isinstance(cells, dict) else None
        status = REGRESSION_PINNED
        if (recorded or {}).get('band') == 'seen_once' and isinstance(cleared_on, dict) and not cleared_on.get('failed') \
                and measured_replays(cleared_on) >= NOT_PINNED_MIN_MEASURED:
            status = REGRESSION_NOT_PINNED
        # Undecided replays (inconclusive, deferred) may decide on the next run, and a run whose replays all errored (an
        # outage) measured nothing of the folder: only valid replays that met nothing measure nothing.
        valid = entry.get('valid')
        if owner and isinstance(valid, int) and valid > 0 and not entry.get('restore_reduced') and not any(
                measured_replays(cell) or int(cell.get('inconclusive') or 0) or int(cell.get('deferred') or 0)
                for cell in (((cells or {}).get(a) or {}) for a in ('at_cut', 'anywhere'))):
            status = REGRESSION_MEASURES_NOTHING
        row = {
            'folder': name, 'scenario': scenario, 'title': title, 'owner': owner, 'owner_source': source,
            'at_cut': at_cut, 'anywhere': cells.get('anywhere') if isinstance(cells, dict) else None, 'clear_axis': axis,
            'valid': entry.get('valid'), 'invalid': entry.get('invalid') or [], 'recorded': recorded, 'status': status,
            'replay_mode': entry.get('replay_mode'),
            # Replays that met the cut with less state than the recording (the SDK's count): not measured there.
            'restore_reduced': entry.get('restore_reduced') or 0,
            # The valid replays' failed and refused tool calls (the SDK's sums): a retirement never counts such a run.
            'tool_errors': entry.get('tool_errors'), 'tool_refusals': entry.get('tool_refusals'),
            # A job the pin started and never saw finish: the replay's mock does not have it (`regressions.JOB_OPEN_AT_CUT`).
            'replay_caveat': _regressions.pin_caveat(folders[name]) if _regressions is not None and name else None,
            **({'reading': {**reading, 'population': entry.get('population'), 'axis': axis}} if reading is not None else {}),
        }
        # Hollow: its replays cannot measure its issue (`regressions.hollow_reason`). `status` is left as it is; `retire`
        # drops the folder from the kept set and the planner records its finding again.
        if _regressions is not None and name and workdir is not None and (hollow := _regressions.hollow_reason(workdir, name, row)):
            row['hollow'] = hollow
        rows.append(row)
    return rows


def issue_type_rows(rows: Sequence[dict], rates: dict | None, workdir: Path | None) -> list[dict]:
    """The recurring issue types: the checks owning the regressions on disk, the folders a record found rare
    at their cut (`$ART/regression_picks/*.confirm.json`) and the not-pinned rows — each with its
    `policy_turn_rates` row, verbatim. Labelled by the CHECK: its rate is that check's red rate, which can
    count a different failure of the same check, never the defect's own rate."""
    policies = (rates or {}).get('policies') or {}
    owners, _ = _regression_owners(workdir)
    behind: dict[str, dict] = {}

    def add(policy: str | None, folder: str | None, title: str | None, source: str, **extra: object) -> None:
        if not policy:
            return
        entry = behind.setdefault(policy, {'policy': policy, 'rate': policies.get(policy), 'behind': []})
        entry['behind'].append({'folder': folder, 'title': title, 'source': source, **extra})

    for folder in _regressions_on_disk(workdir):
        title, probe, _ = _folder_facts(folder)
        row = next((r for r in rows if r.get('folder') == folder.name), {})
        add(row.get('owner') or owners.get(folder.name) or probe, folder.name, title, row.get('status') or REGRESSION_PINNED)
    picks = workdir / 'artifacts' / REGRESSION_PICKS_DIRNAME if workdir is not None else None
    for path in sorted(picks.glob('*.confirm.json')) if picks is not None and picks.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get('schema') == REGRESSION_CONFIRM_SCHEMA and record.get('band') == 'rare_at_cut':
            add(record.get('owner'), record.get('folder'), None, 'rare_at_cut', recut_candidates=record.get('recut_candidates') or [])
    return [behind[name] for name in sorted(behind)]


#: The LOCAL-ONLY machine copy beside run.json: the per-check rates and the recurring issue types. Never
#: indexed, uploaded or attached — run.json is, and the customer's report is rendered from it.
REGRESSIONS_DETAIL_JSON = 'regressions_detail.json'
REGRESSIONS_DETAIL_SCHEMA = 'regressions-detail-v1'


def regressions_detail(out: Path, args: argparse.Namespace, spec: dict) -> dict | None:
    """`{schema, policy_turn_rates, issue_types}` for `regressions_detail.json`, or None when the run has no
    per-check rates (an older SDK) or no check behind any regression."""
    rates = policy_turn_rates_block(out)
    if rates is None:
        return None
    workdir = Path(args.workdir) if getattr(args, 'workdir', None) else None
    issue_types = issue_type_rows(spec.get('regressions') or [], rates, workdir)
    if not issue_types:
        return None
    return {'schema': REGRESSIONS_DETAIL_SCHEMA, 'policy_turn_rates': rates, 'issue_types': issue_types}


#: The keys of one scenario row a trials report reads per trial (`trial_rows`).
_TRIAL_ROW_KEYS = ('result', 'goal', 'deterministic', 'score', 'fail_reasons', 'turns', 'rooted_findings')


def trial_specs(out: Path, args: argparse.Namespace, block: dict, first: dict, scores_block: dict | None = None) -> list[dict | None]:
    """One spec per listed trial, in `block['dirs']` order — trial 1's is `first`, already built; every other is built by
    THIS builder over that trial's dir, so the trials report and the pooled totals read every trial without a second
    reader and without depending on when `trial_k/run.json` was written. `None` for a trial that left no scenario dir."""
    specs: list[dict | None] = []
    for sub in [d for d in block.get('dirs') or [] if isinstance(d, str) and _TRIAL_DIR.fullmatch(d)]:
        if not sub:
            specs.append(first)
            continue
        if not (out / sub).is_dir():  # a listed trial that left nothing on disk
            specs.append(None)
            continue
        # Each trial's own build (Step 4b) prints its notes; this nested one would only repeat them.
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                specs.append(_spec_of(out / sub, args, scores_block, sub))
            except SystemExit:  # the trial left no scenario dir at all
                specs.append(None)
    return specs


def trial_rows(block: dict, specs: Sequence[dict | None]) -> dict[str, list[dict | None]]:
    """`{scenario: [row | None per listed trial]}` — each trial's OWN row, off :func:`trial_specs`. `None` where the
    trial produced no row for the scenario (or left no dir). Only `_TRIAL_ROW_KEYS` travel; `trials` stays the SDK's."""
    per_trial = [{str(r.get('id')): {k: r[k] for k in _TRIAL_ROW_KEYS if k in r} for r in spec['scenarios']} if spec else {} for spec in specs]
    names = listed if isinstance(listed := block.get('scenarios'), dict) else {}
    return {str(name): [rows.get(str(name)) for rows in per_trial] for name in names}


#: C7, the run's numbers the SDK produces (`run_summary.json` `totals`, or a refold's `trials.run.totals`): copied into
#: `run.json` `totals` by :func:`apply_c7`. Validity and its causes are the SDK's, never recounted here; `passed_valid` is
#: the valid trials whose own row reads `pass`. The headline denominator is `valid_trials`.
C7_TOTALS = ('valid_trials', 'invalid_trials', 'passed_valid', 'tool_refusals', 'invalid_by_cause')
#: The `totals` keys that are not counts, so pooling over trials does not add them (`pool_trials`); C7's are run-level.
_UNPOOLED_TOTALS = ('unscored_run', *C7_TOTALS)
#: The eval-row keys that are counts (`_zero_counts`, `total`, the turn grain), summed by `pool_trials`.
_POOLED_EVAL_COUNTS = ('passed', 'failed', 'not_applicable', 'not_measured', 'deferred', 'inconclusive', 'errored', 'skipped', 'total',
                       'passed_turns', 'decided_turns')


def pool_trials(first: dict, specs: Sequence[dict | None]) -> None:
    """Pool `first`'s `totals` and `evals` over EVERY trial in `specs` (trial 1 is `first` itself), in place — the k/K
    `analytics.md` reads, so `run.json` and `eval_signal.md` never count trial 1 alone.

    `totals.scenarios` stays the scenarios (trial 1's); `totals.conversations` is the conversations drawn (every trial's
    rows, the denominator of every pooled count) and `totals.trials` the trials pooled. Every other count is summed,
    `unscored_run` holds only when every trial is unscored. No score is pooled: the run's scores are the SDK's
    (:func:`apply_scores`).
    An eval row sums its counts, recomputes its `pass_rate` over the pooled decided verdicts, and carries
    `decided_scenarios` — the DISTINCT scenarios some trial decided it on (`trials.scenarios`), the sample a one-sided
    reading is floored on: three trials of one scenario agreeing are not three scenarios.
    """
    built = [spec for spec in specs if spec is not None]
    totals: dict = dict(first['totals'])
    pooled = {k: sum(int(spec['totals'].get(k) or 0) for spec in built)
              for k in dict.fromkeys(k for spec in built for k, v in spec['totals'].items()
                                     if k not in _UNPOOLED_TOTALS and isinstance(v, int) and not isinstance(v, bool))}
    totals.update(pooled, scenarios=first['totals']['scenarios'], conversations=pooled.get('scenarios', 0), trials=len(built))
    if 'unscored_run' in totals and not all(spec['totals'].get('unscored_run') for spec in built):
        del totals['unscored_run']
    first['totals'] = totals
    rows: dict[str, dict] = {}
    for spec in built:
        for row in spec['evals']:
            into = rows.get(str(row['id']))
            if into is None:  # the first trial that rows it gives its tier fields; every count starts at zero
                into = rows[str(row['id'])] = {k: v for k, v in row.items() if k not in _POOLED_EVAL_COUNTS and k != 'inconclusive_reasons'}
            for key in _POOLED_EVAL_COUNTS:
                if isinstance(row.get(key), int):
                    into[key] = into.get(key, 0) + row[key]
            for reason, n in (row.get('inconclusive_reasons') or {}).items():
                hist = into.setdefault('inconclusive_reasons', {})
                hist[reason] = hist.get(reason, 0) + n
            if row.get('deferred_to'):
                into['deferred_to'] = sorted({*into.get('deferred_to', ()), *row['deferred_to']})
    cells = [c for c in (first.get('trials') or {}).get('scenarios', {}).values() if isinstance(c, dict)]
    for row in rows.values():
        row.pop('pass_rate', None)
        if row.get('total'):
            row['pass_rate'] = round(row['passed'] / row['total'], 3)
        if row['id'] in PLUGIN_CHECKS:
            row_key = PLUGIN_CHECKS[row['id']][0]
            row['decided_scenarios'] = len({str(r.get('id')) for spec in built for r in spec['scenarios'] if row_key in r})
        elif any(isinstance(c.get('policies'), dict) for c in cells):
            row['decided_scenarios'] = sum(1 for c in cells if (p := (c.get('policies') or {}).get(row['id'])) and (p.get('passed') or p.get('failed')))
        for key in _CONDITIONAL_COUNTS:
            if not row.get(key):
                row.pop(key, None)
    first['evals'] = list(rows.values())


def withdraw_trial_marks(stability: dict, stale: Sequence[str], refolded: Mapping[str, str] | None = None) -> None:
    """A re-scored trial disowns its scenario's cross-trial stability marks too — `trials_block`'s boundary.

    `refolded` is `{scenario: revision}` for the scenarios whose every graded trial the trials refold found judged at ONE
    eval revision: their row is stamped with that revision (`first_eval_revision` / `second_eval_revision` name the run-time
    one otherwise, a revision none of the counts above come from) and says so — the trials compare, only the run-time
    marks were not re-derived — instead of sending the reader to re-run a scenario that needs nothing."""
    for name in stale:
        row = stability.get(name)
        if isinstance(row, dict) and row.get('selection') == 'unconditional':
            kept = {k: v for k, v in row.items() if k not in _MARK_KEYS and k not in _RUNNER_VALIDITY_KEYS}
            if revision := (refolded or {}).get(name):
                stability[name] = kept | {
                    'first_eval_revision': revision, 'second_eval_revision': revision,
                    'eval_revision_at_run': row.get('first_eval_revision'),
                    'marks_withdrawn': STABILITY_REFOLDED,
                    'marks_withdrawn_detail': f'every trial of this scenario is judged at eval revision {revision}, so '
                                              'its cross-trial counts compare one eval set; the per-policy marks the run '
                                              'computed before a re-score or correction changed them were not re-derived.'}
                continue
            stability[name] = kept | {
                'marks_withdrawn': STABILITY_STALE,
                'marks_withdrawn_detail': 'a trial of this scenario was re-scored after the run, so its cross-trial marks '
                                          'compare scorings that no longer exist. Re-run the scenario.'}


#: The per-policy claims a stability row makes ABOUT TWO ATTEMPTS. Withdrawn together or not at
#: all: a reproduction mark without its evidence reads as corroboration nobody measured.
_MARK_KEYS = frozenset({'policy_reproduction', 'policy_evidence'})

#: The runner's OWN verdict on those claims (plurai `_marks_comparability`): `marks_valid: False`
#: plus a reason, written only as a DISPROOF. Dropped whenever this file withdraws or recomputes,
#: because the row must assert the validity of its marks exactly once — the SDK's negative beside
#: this file's `marks_withdrawn` is two writers answering the same question in one row.
_RUNNER_VALIDITY_KEYS = frozenset({'marks_valid', 'marks_invalid_reason'})


#: The marks that COMPARE the two attempts (plurai `_COMPARING_MARKS`). `unmeasured` is the fourth
#: value and it compares nothing — a map holding only those has no comparison to publish.
_COMPARING_MARKS = frozenset({'reproduced', 'not_reproduced', 'retry_only'})


def rescore_frame(doc: Mapping) -> dict:
    """One rescored attempt as the mark comparison reads it, all off its `verdicts.json`: `{cells, eval_revision,
    gt_revision?, run_upload_id?, scenario_name?}` — the upload id and item name of its newest rescore, when that rescore
    scored an upload item."""
    judged = verdicts_file.judged_against(doc)
    frame = {'cells': verdicts_file.cells(doc), 'eval_revision': judged.get('eval_revision')}
    if judged.get('gt_revision') is not None:
        frame['gt_revision'] = judged['gt_revision']
    latest = verdicts_file.latest_rescore(doc) or {}
    if upload := latest.get('run_upload_id'):
        frame['run_upload_id'] = upload
    if item := latest.get('scenario_name'):
        frame['scenario_name'] = item
    return frame


def _mismatched_frame(first: Mapping, retry: Mapping) -> str | None:
    """Why these two rescored attempts are not two samples of ONE measurement, beyond the eval revision.

    The eval set is not the only frame a comparison lives inside, and the recipe warns about both
    of these in prose while nothing enforced either:

    * the GROUND TRUTH. `score_run` reads the scenario's GT live, so one round shares it — but a GT
      edit BETWEEN the two calls moves what the judges grade against, exactly as an eval edit does.
      Both files carry `gt_revision`, so it is checkable.
    * the RUN ITEM the artifacts came from. The retry is scored as its own upload item
      (`scenario_name: <scenario>/stability_retry`, the run's `run_upload_id`); a call that names the
      SCENARIO with that upload scores the FIRST attempt twice, and two rescores of one transcript
      return `reproduced` on every red — `stable`, the verdict that licenses a redraw, bought with no
      second sample at all. Same upload AND same item (or a retry rescore naming none) is that case.
    """
    if 'gt_revision' in first and 'gt_revision' in retry and first['gt_revision'] != retry['gt_revision']:
        return (f'the first attempt was rescored against GT revision {first["gt_revision"]} and the retry against '
                f'{retry["gt_revision"]}, so the two samples were judged against different ground truth. Rescore both '
                f'attempts in one round.')
    upload = first.get('run_upload_id')
    if upload and retry.get('run_upload_id') == upload and retry.get('scenario_name') in (None, first.get('scenario_name')):
        return (f'both rescores name the same run-artifacts upload ({upload}) and the same item, so the retry was scored '
                f"from the FIRST attempt's saved transcript — one sample counted twice, which returns `reproduced` on "
                f'every red. Score the retry as its own item: `scenario_name: <scenario>/{RETRY_DIRNAME}` (rescore_run.py does).')
    return None


class StabilityMarks(NamedTuple):
    """Which scenarios lost their per-policy marks, and which had them RECOMPUTED. Two lists
    because they are opposite news and the caller reports them as such."""

    withdrawn: list[str]
    recomputed: list[str]


class _RescoredAttempt:
    """One attempt's RESCORED verdicts, shaped the way the SDK's classifier reads an attempt.

    `_policy_reproduction` / `_policy_evidence` touch exactly two things on a `ThreadResult` — its
    finalize response's verdict block and `ended_early` — so a rescored attempt (:func:`rescore_frame`)
    is PRESENTED as those rather than having the rule re-derived against it here.

    `violated_policies` is not in `verdicts.json` (no derived lists there), so it is computed with the backend's own two-term predicate
    (:func:`conclusively_violated`). `ended_early` is None because a rescore is not a dialog: it
    re-grades the transcript an attempt already produced, and whether that attempt was cut short
    is a fact about the RUN, which the row already carries as `reason`.
    """

    def __init__(self, doc: Mapping, transcript: Mapping | None = None) -> None:
        cells = {str(name): cell for name, cell in (doc.get('cells') or {}).items() if isinstance(cell, dict)}
        live = run_record(transcript)
        self.finalize = SimpleNamespace(
            policy_evaluation_results={
                'evaluation_results': cells,
                'violated_policies': sorted(name for name, cell in cells.items() if conclusively_violated(cell)),
                # THE ATTEMPT'S OWN RUN FACTS, carried from its transcript rather than defaulted.
                # `_stability_reason` reads `user_sim_integrity.run_valid` and `ended_early`, and an
                # attempt that runs over the clock or is ruled invalid measured NOTHING — the
                # classifier then writes every mark `unmeasured`. Presenting a clean attempt instead
                # would manufacture a full comparison out of a sample the runner had already disqualified,
                # which is the wrong-comparison publication this path exists to refuse. A rescore does
                # not re-run the dialog, so these are exactly the live attempt's own.
                **({'user_sim_integrity': live['user_sim_integrity']} if isinstance(live.get('user_sim_integrity'), dict) else {}),
            }
        )
        self.ended_early = ((transcript or {}).get('metadata') or {}).get('ended_early')


def _sdk_mark_classifier() -> SimpleNamespace | None:
    """The SDK's own mark rules, or None when plurai is unreachable or predates the evidence rule.

    SHARED, NEVER RETYPED. Deriving `reproduced` / `not_reproduced` / `retry_only` / `unmeasured`
    from two cell sets IS `plurai.run_simulation._policy_reproduction`, with its graded-policies
    filter, its no-measurement reasons and its `reason` gating — a rule that WRITES these marks in
    the first place. A copy here would be a second implementation of it inside the consumer, and
    the two would drift apart, as any hand-mirrored constant does.

    So an unreachable plurai means NO RECOMPUTE — the marks stay withdrawn and the detail says the
    classifier could not be reached. A comparison computed by a guess is worse than a withdrawn one.
    """
    try:
        from plurai import run_simulation  # noqa: PLC0415 - optional, and only on the recompute path
    except Exception:  # noqa: BLE001 - a half-installed plurai must not kill the whole run.json build
        return None
    # A plurai that renames any of these is a real deployment (this script runs against whatever
    # the customer's venv holds), and it is a fact to REPORT, not an AttributeError mid-build. The
    # evidence TOKEN is read here for the same reason the callables are: comparing against a local
    # `'deterministic'` literal would, on a rename, silently empty the PARTIAL caveat and leave the
    # row claiming every mark is a judged sample — a hand-mirrored constant flipping a negative into
    # a positive.
    rules = SimpleNamespace(
        reproduction=getattr(run_simulation, '_policy_reproduction', None),
        evidence=getattr(run_simulation, '_policy_evidence', None),
        verdict=getattr(run_simulation, '_stability_verdict', None),
        deterministic=getattr(run_simulation, 'DETERMINISTIC_EVIDENCE', None),
    )
    if not all(callable(rule) for rule in (rules.reproduction, rules.evidence, rules.verdict)) or not isinstance(rules.deterministic, str):
        return None
    # AN SDK WHOSE VERDICT READS NO `evidence` derives `stable` from ANY reproduction, a
    # deterministic one included — a rule the current runner dropped, because a re-run expression cannot
    # tell a false red from a true one. Recomputing through it would publish the verdict that
    # licenses a redraw over rows the current runner would not call stable, so it gets what an
    # unreachable classifier gets: no recompute.
    if 'evidence' not in inspect.signature(rules.verdict).parameters:
        return None
    return rules


def recomputed_marks(row: Mapping, first: Mapping | None, retry: Mapping | None,
                     first_transcript: Mapping | None = None, retry_transcript: Mapping | None = None) -> dict | None:
    """The per-policy marks REDERIVED from two attempts rescored at one eval revision, or None.

    THE PRECONDITION IS EACH FILE'S OWN `judged_against.eval_revision` (:func:`rescore_frame`), both
    present and equal — never the row's `first_eval_revision` / `second_eval_revision`. Those are the
    revisions the two attempts were graded at WHEN THEY RAN, so after a one-sided rescore they still
    read equal: they are equal EXACTLY when the marks are as stale as the withdrawal exists to catch.

    PARTIAL BY CONSTRUCTION, and the row says so. `score_run` re-runs a `code_eval`'s expression
    over the same saved shape, so a deterministic cell cannot disagree with itself and its mark is
    a tautology rather than a second sample — the SDK already labels those `deterministic`, and the
    detail names them so the restored table is not read as a full flip table. (A judge running at
    the legacy `policy_judge_samples: 1` lands in the same label here, because the `score_run`
    envelope carries no per-cell token usage for the SDK's discriminator to read. It under-claims
    in that one case, which is the safe direction: `deterministic` is printed INSTEAD of a
    reproduction mark, never in place of a doubt.)
    """
    first, retry = first or {}, retry or {}
    revision = first.get('eval_revision')
    if not isinstance(revision, str) or not revision or retry.get('eval_revision') != revision:
        return None
    if _mismatched_frame(first, retry) is not None:
        return None
    reason = row.get('reason')
    if not isinstance(reason, str) or not reason:
        # The classifier's first argument. Without it nothing can say whether the first attempt was
        # a sample of its own reds, and `_policy_reproduction` would read the absence as a reason
        # that is not `eval_failed` and call every red `unmeasured` — marks replaced by a verdict
        # nobody reached.
        return None
    rules = _sdk_mark_classifier()
    if rules is None:
        return None
    attempts = (_RescoredAttempt(first, first_transcript), _RescoredAttempt(retry, retry_transcript))
    reproduction = rules.reproduction(*attempts, reason)
    if not (set(reproduction.values()) & _COMPARING_MARKS):
        # NOTHING TO RESTORE. Every mark is `unmeasured` — the retry measured nothing the first
        # attempt's reds could be compared against (it was cut, ruled invalid, or graded none of
        # them), or the first attempt never earned a comparison. Stamping `marks_recomputed` over
        # that would publish "the comparison holds again" on a row that compares nothing, which is
        # the claim the withdrawal exists to refuse. Same test the SDK applies before it qualifies
        # a row's marks at all (`_marks_comparability`).
        return None
    evidence = rules.evidence(*attempts, reproduction)
    tautologies = sorted(name for name, label in evidence.items() if label == rules.deterministic)
    return {
        'policy_reproduction': reproduction,
        'policy_evidence': evidence,
        # DERIVED WITH THEM, never carried. For `eval_failed` the SDK computes the row's verdict
        # FROM the marks precisely so the two cannot disagree — so republishing the run-time `stability`
        # beside recomputed marks can leave `stable`, the verdict that LICENSES A REDRAW, over a map
        # that now derives `flaky`. Same classifier, same rule, one more call.
        'stability': rules.verdict(attempts[1], reason, reproduction, evidence=evidence),
        'marks_recomputed': revision,
        'marks_recomputed_detail': (
            f'both attempts were RESCORED at eval revision {revision}, so these marks compare two samples one eval set '
            'produced; they were rederived from the two attempts\' verdicts by the runner\'s own classifier, not by this reader. '
            + (f'PARTIAL: {len(tautologies)} of these ({", ".join(tautologies)}) re-run a fixed expression over a saved '
               'shape, so their mark is a tautology and not a second sample — read them as `deterministic`, never as '
               'corroboration.' if tautologies else 'Every mark here is a judged sample.')
        ),
    }


def _stale_detail(row: Mapping, retry_dir: Path, first: Mapping | None, retry: Mapping | None) -> str:
    """WHICH half of the comparison cannot be made, so the row names its own remedy.

    Four causes and four sentences, in the order `recomputed_marks` rules them out. One sentence
    for all of them would name the retry as the missing half even where both attempts HAD been
    rescored — a remedy that does not apply is worse than none.
    """
    if first is None:
        return (
            f'the RETRY was rescored after the stability marks were written and the first attempt was not '
            f'({retry_dir.parent.name}/{verdicts_file.FILENAME} records no rescore), so the two attempts were scored under different '
            f'eval revisions and no per-policy comparison between them is meaningful. Rescore the first attempt under '
            f'the same revision and the marks are recomputed, or re-run the scenario.'
        )
    if retry is None:
        return (
            f'this scenario was RESCORED after the stability marks were written, so the two attempts were scored under '
            f'different eval revisions and no per-policy comparison between them is meaningful. The RETRY was not '
            f'rescored ({retry_dir.parent.name}/{RETRY_DIRNAME}/{verdicts_file.FILENAME} records no rescore) — score it under the '
            f'same revision and the marks are recomputed, or re-run the scenario.'
        )
    first_rev, retry_rev = first.get('eval_revision'), retry.get('eval_revision')
    if not (isinstance(first_rev, str) and first_rev) or not (isinstance(retry_rev, str) and retry_rev):
        if not (first_rev or retry_rev):
            unstamped = 'neither attempt'
        elif not first_rev:
            unstamped = 'the first attempt'
        else:
            unstamped = 'the retry'
        return (
            f'both attempts were rescored, but {unstamped} records NO eval revision, so whether the '
            f'two samples were graded by the same judges cannot be established — and an unverifiable comparison is not '
            f'a valid one. Rescore with a writer that persists `eval_revision`.'
        )
    if first_rev != retry_rev:
        return (
            f'the first attempt was rescored at eval revision {first_rev} and the retry at {retry_rev}, so every '
            f'per-policy mark here would compare verdicts two different eval sets produced. Rescore both attempts in '
            f'one round.'
        )
    if (mismatch := _mismatched_frame(first, retry)) is not None:
        return mismatch
    if not (isinstance(row.get('reason'), str) and row.get('reason')):
        return (
            'both attempts were rescored at one eval revision, but this row records no `reason` for the retry — so '
            'nothing can say whether the first attempt was a sample of its own reds, which is the first thing the '
            'classifier decides. An older runner wrote this row, and the comparison cannot be rederived from the '
            'verdicts alone.'
        )
    if _sdk_mark_classifier() is None:
        return (
            'both attempts were rescored at one eval revision, but the marks could not be recomputed: '
            "`plurai.run_simulation`'s mark rules could not be used from this interpreter — plurai is not "
            "importable, this release renamed them, or it predates the verdict that reads each mark's evidence (an "
            'older one calls a row `stable` on a deterministic re-run alone). This reader will not retype the rule '
            'that writes these marks. Run this script under the venv the SDK is installed in, on a matching plurai.'
        )
    return (
        "both attempts were rescored at one eval revision, but the RETRY measured nothing the first attempt's reds "
        'could be compared against — it was cut short, ruled invalid, or graded none of them, so every mark the '
        'classifier reaches is `unmeasured`. There is no comparison to publish; re-run the scenario to get one.'
    )


def quarantine_restaled_stability(block: dict, dirs: Sequence[Path]) -> StabilityMarks:
    """Withdraw the per-policy marks on any stability row nothing can still vouch for — or, when
    BOTH attempts were rescored at one eval revision, recompute them through the SDK's classifier.
    A row whose retry was never spent compared nothing: it loses its run-time marks, never to a withdrawal.

    `run_summary.json`'s `stability` is copied verbatim (`run_integrity`), while each attempt's
    eval cells come from its `verdicts.json`, which a rescore changes. After `/edit-evals` +
    `score_run` attempt 1 is scored at eval revision N and attempt 2 at N-1 — and the marks, which
    are a COMPARISON of the two attempts, would be computed across that boundary.

    THE MARKS GO, THE ROW STAYS. `policy_reproduction` and `policy_evidence` are claims about two
    attempts scored under one eval set; the moment one attempt is rescored they are claims about
    nothing, and a reader cannot tell which half moved. `reason`/`stability`/`authoritative_attempt`
    are facts about the RUN — a retry was earned, it was taken — and survive a rescore untouched.

    TWO THINGS CAN DISQUALIFY A ROW, and both are read here rather than one:

    * THE RUNNER ITSELF already disproved the marks — `marks_valid: False`, which plurai stamps
      when the two attempts' finalizes named different eval revisions (or one named none). The
      runner's own reason rides the withdrawal.
    * A RESCORE overtook them: either attempt's `verdicts.json` records a change after the run
      (`verdicts_file.rescored`), and the other attempt cannot be squared with it.

    AND ONE THING CAN RESTORE THEM, ahead of both. Score the `stability_retry/` transcript under
    the same eval revision as attempt 1 — the recipe that does it is in `/simulation-generate-suite`
    Step 3 — and `recomputed_marks` rederives the comparison from the two files through the SDK's
    own classifier. It is tried BEFORE the runner's disproof is read, because a rescore of both
    attempts supersedes the run-time verdicts that disproof was about.
    """
    rows = block.get('stability')
    if not isinstance(rows, dict) or not rows:
        return StabilityMarks([], [])
    withdrawn: list[str] = []
    recomputed: list[str] = []
    for d in dirs:
        row = rows.get(d.name)
        if not isinstance(row, dict) or not (row.keys() & _MARK_KEYS):
            continue
        kept = {k: v for k, v in row.items() if k not in _MARK_KEYS and k not in _RUNNER_VALIDITY_KEYS}
        retry_dir = d / RETRY_DIRNAME
        first_doc = read_verdicts(d)
        retry_doc = read_verdicts(retry_dir) if verdicts_file.exists(retry_dir) else None
        first = rescore_frame(first_doc) if verdicts_file.rescored(first_doc) else None
        retry = rescore_frame(retry_doc) if retry_doc is not None and verdicts_file.rescored(retry_doc) else None
        overtaken = first is not None or retry is not None
        if overtaken:
            # FIRST, and ahead of the runner's disproof below: a rescore of BOTH attempts at one
            # revision supersedes what the run-time finalizes said, so a `marks_valid: False` about
            # them is a statement about a scoring nothing publishes.
            restored = recomputed_marks(row, first, retry, _read_json(d / 'transcript.json'), _read_json(retry_dir / 'transcript.json'))
            if restored is not None:
                # THE RUN-TIME STAMPS GO WITH THE VERDICTS THEY DESCRIBE. `first_eval_revision` /
                # `second_eval_revision` name the revisions the two FINALIZES used; `marks_recomputed`
                # names the one revision these marks came from; the run's own is still on the block,
                # as `eval_revision_at_run`.
                rows[d.name] = {k: v for k, v in kept.items() if k not in ('first_eval_revision', 'second_eval_revision')} | restored
                recomputed.append(d.name)
                continue
        if row.get('marks_valid') is False:
            rows[d.name] = {
                **kept,
                'marks_withdrawn': STABILITY_STALE,
                'marks_withdrawn_detail': str(row.get('marks_invalid_reason') or 'the runner recorded these marks as invalid and gave no reason'),
            }
            withdrawn.append(d.name)
            continue
        if not overtaken:
            # Nothing overtook the marks and the runner vouches for them: they stand, untouched.
            continue
        if retry_doc is None and not (set(marks.values()) if isinstance(marks := row.get('policy_reproduction'), dict) else set()) & _COMPARING_MARKS:
            # NO SECOND ATTEMPT AND NOTHING COMPARED (a retry never spent: every mark `unmeasured`). A rescore cannot make a
            # comparison stale that was never made, so nothing is withdrawn and no remedy is owed; the run-time marks and
            # their agreement numbers go, because they describe verdicts the rescore replaced.
            rows[d.name] = kept
            continue
        rows[d.name] = {
            **kept,
            'marks_withdrawn': STABILITY_STALE,
            'marks_withdrawn_detail': _stale_detail(row, retry_dir, first, retry),
        }
        withdrawn.append(d.name)
    return StabilityMarks(withdrawn, recomputed)


#: THE `ended_early` CHANNEL. WHY a dialog stopped, when it stopped for something other than the
#: user reaching their goal. Both halves of the stack type it and nothing on this side read
#: either — so a scenario the SIMULATED USER walked away from rendered as an ordinary low-score
#: agent failure, and a scenario cut by the turn budget rendered as the same thing. They route to
#: different owners: a setting on the run, a defect in simulate.py, a finding about the scenario.
#:
#: ONE FACT, TWO CONSTRUCTIONS — and this reads BOTH, because NEITHER is complete:
#:
#: * SERVER (`finalize_record.dialog_outcome`, the backend `Thread.dialog_outcome`). Always
#:   PRESENT on a current backend and explicitly `null` on a normal ending, so `{ended_early:
#:   None}` and a missing block are different claims — "it ended the way it was supposed to" vs
#:   "the producer is too old to say". It rides INSIDE transcript.json, so it survives upload and
#:   a `score_run` replay, and it carries `turn_count`/`max_turns`, without which the reason is
#:   half a fact: "gave up at 4 of 13" and "gave up at 13 of 13" have different owners. Its
#:   vocabulary is the backend's `EarlyExitReason` — and it includes `policy_terminated`, which ONLY
#:   the server can know (an eval's `terminate_values` stopped the run).
#: * CLIENT (`metadata.ended_early`). Written ONLY when non-null, so absent is the normal ending.
#:   Its vocabulary includes `time_budget_exhausted` and `adapter_degraded`, which only the harness
#:   can know (it holds the clock, and it knows which replies it manufactured itself) — and on the
#:   all-degraded case the SDK deliberately SUPERSEDES whatever the server said, because a
#:   transcript in which every turn was emptied is not a measurement of the agent at all.
#:
#: So the client's reason WINS where it has one (it is the superset, and its override is a
#: considered one), the server's fills in the rest, and the server's block always supplies the
#: budget context. A disagreement is reported on stderr rather than stored under a third name:
#: inventing a key for it would be a third construction of the same fact.
#:
#: The vocabulary is OPEN and this file FILTERS ON NO MEMBER of it — the same rule, for the same
#: reason, as the `harness_*` namespace below. Both sides relay a string they did not mint and
#: keep an unrecognised one rather than dropping it, so any enumeration here silently loses the
#: next reason added. Known today: `user_gave_up`, `turn_cap_exhausted`, `time_budget_exhausted`,
#: `adapter_degraded`, `policy_terminated` — a list to read the report BY, never a list to filter
#: the data THROUGH. The renderer advises on the ones it knows and names the rest.
#:
#: NOT an `error_kind` and never merged into one: a scenario that ended early still finalized,
#: still scored, and still has a transcript. It moves no total and no rate — whether a cut makes
#: the row `incomplete` is the attempt's `verdicts.json` status, decided once by its writer; this
#: says how to READ the rate, which is why it rides the row rather than the counters.
def merge_ended_early(early: dict[str, dict], name: str, transcript: dict) -> None:
    """Put the transcript's own `ended_early_of` on ``early[name]``, authoritative for its row over the roster's.

    The roster may carry the stated cause the transcript's metadata does not (an older harness writing only the
    reason); it is carried over ONLY under the same reason — a cause belongs to the reason it was stated for — and so
    are its disputed turn and route."""
    if not (cut_here := ended_early_of(transcript, name)):
        return
    roster = early.get(name) or {}
    if 'cause' not in cut_here and roster.get('cause') and roster.get('reason') == cut_here['reason']:
        cut_here['cause'] = roster['cause']
    if 'disputed_turn' in roster and roster.get('reason') == cut_here['reason']:
        cut_here['disputed_turn'] = roster['disputed_turn']
    if 'route' in roster and roster.get('reason') == cut_here['reason']:
        cut_here['route'] = roster['route']
    early[name] = cut_here


def ended_early_of(transcript: dict, name: str) -> dict | None:
    """`{reason, turn_count, max_turns}` for a dialog that stopped early, or None.

    None means the dialog ended the ordinary way OR that neither producer said — deliberately
    indistinguishable, because inventing a reason from a low score is the exact inference this
    channel exists to stop. Unknowable sub-keys are OMITTED, never nulled.

    `turn_count` here is the SERVER's /user-turn CYCLE count — the same number the transcript
    carries as `user_turn_cycles`, and one LESS than its top-level `turn_count` whenever a
    closing-turn reply was delivered (that reply is in `turns` but is not a /user-turn cycle).
    Labelled `user_turns` on the row so nothing compares it to the Turns column.
    """
    outcome = run_record(transcript).get('dialog_outcome')
    outcome = outcome if isinstance(outcome, dict) else {}
    served = outcome.get('ended_early')
    served = served if isinstance(served, str) and served else None

    meta = transcript.get('metadata')
    client = meta.get('ended_early') if isinstance(meta, dict) else None
    client = client if isinstance(client, str) and client else None

    if served and client and served != client:
        print(f'note: {name} — the harness recorded ended_early={client!r} and the server recorded {served!r}. '
              f'Using the harness reason (it supersedes on the cases only it can see); the server reason is '
              'in the transcript if you need it.', file=sys.stderr)
    reason = client or served
    if not reason:
        return None
    row: dict = {'reason': reason}
    # The simulated user's own stated CAUSE for a give-up (SDK `metadata.ended_early_cause`,
    # written only beside a non-null reason). The reason says WHO stopped; the cause says what it
    # saw — `reply_not_visible` is a harness fault, `agent_refused` is the agent's — and it is
    # RELAYED, never inferred: an absent cause is an absent key, and the renderer then asserts no
    # routing at all rather than a generic "look at the scenario or the agent".
    cause = meta.get('ended_early_cause') if isinstance(meta, dict) else None
    if isinstance(cause, str) and cause:
        row['cause'] = cause
    # Budget context, from the server's block only — the harness's metadata carries no such pair.
    if isinstance(outcome.get('turn_count'), int):
        row['user_turns'] = outcome['turn_count']
    if isinstance(outcome.get('max_turns'), int):
        row['max_turns'] = outcome['max_turns']
    return row


def roster_ended_early(out: Path) -> dict[str, dict]:
    """`{scenario: {reason}}` from the runner's own `run_summary.json`, or `{}` when it says none.

    A THIRD reader of the same fact, and not a redundant one: the runner records a row for every
    scenario it cut, INCLUDING one whose artifacts this aggregation never sees. The
    all-turns-degraded case is exactly that shape — a scenario whose transcript is not a
    measurement of the agent is also the one most likely to be missing — so reading only the
    transcripts loses precisely the rows the channel matters most for. No budget context here:
    the summary carries reasons only, and a fabricated pair would read as measured.
    """
    doc = _read_json(out / 'run_summary.json')
    rows = doc.get('ended_early_scenarios') if isinstance(doc, dict) else None
    if not isinstance(rows, dict):
        return {}
    # The runner's sibling map of stated CAUSES (`ended_early_causes`), joined onto the reason rows
    # it belongs to: a cause with no reason row is a cause for a dialog the roster does not say
    # stopped, and is left where it is rather than invented into a row.
    causes = doc.get('ended_early_causes') if isinstance(doc, dict) else None
    causes = causes if isinstance(causes, dict) else {}
    # …and the runner's `plan_exhausted_disputed_turns`: a `plan_exhausted` row whose simulated user
    # pushed back on its last turn, mapped to the 0-based agent turn it disputed.
    disputed = doc.get('plan_exhausted_disputed_turns') if isinstance(doc, dict) else None
    disputed = disputed if isinstance(disputed, dict) else {}
    # …and the runner's ONE route per row (`ended_early_routes`: `{owner, remedy, qualifier?}`), which
    # the report renders verbatim — it keeps no remedy table of its own.
    routes = doc.get('ended_early_routes') if isinstance(doc, dict) else None
    routes = routes if isinstance(routes, dict) else {}
    blocks: dict[str, dict] = {}
    for k, v in rows.items():
        if isinstance(v, str) and v:
            blocks[str(k)] = {'reason': v}
            cause = causes.get(k)
            if isinstance(cause, str) and cause:
                blocks[str(k)]['cause'] = cause
            turn = disputed.get(k)
            if v == PLAN_EXHAUSTED_REASON and isinstance(turn, int) and not isinstance(turn, bool):
                blocks[str(k)]['disputed_turn'] = turn
            route = routes.get(k)
            if isinstance(route, dict) and isinstance(route.get('owner'), str) and isinstance(route.get('remedy'), str):
                blocks[str(k)]['route'] = {key: str(val) for key, val in route.items() if key in ('owner', 'remedy', 'qualifier') and val}
    return blocks


#: The `ended_early` reason the server stops on once the scenario's schedule is spent.
PLAN_EXHAUSTED_REASON = 'plan_exhausted'


def ended_early_summary(blocks: dict[str, dict]) -> dict | None:
    """The run-wide roll-up, or None when NOTHING ended early.

    Unlike `user_sim_summary` this is omitted on the empty case rather than published as a
    measured zero, because there is nothing to distinguish: a run where no scenario was cut and
    a run written by producers that type no reasons both produce an empty map, and the renderer
    prints nothing either way. `run_summary.json` keeps the distinction on disk beside this
    file, where a reader who needs it can see the key exists and is empty.

    The roll-up carries REASONS only; the budget context stays on the rows, where the scenario it
    belongs to is beside it.
    """
    if not blocks:
        return None
    counted: dict[str, int] = {}
    for block in blocks.values():
        reason = block['reason']
        counted[reason] = counted.get(reason, 0) + 1
    # The stated causes, per scenario, beside the reasons — a separate map for the same reason the
    # runner keeps them apart: `{scenario: reason}` is the shape every reader already branches on,
    # and a scenario with no stated cause is ABSENT here, never blank.
    causes = {name: block['cause'] for name, block in blocks.items() if block.get('cause')}
    disputed = {name: block['disputed_turn'] for name, block in blocks.items() if 'disputed_turn' in block}
    routes = {name: block['route'] for name, block in blocks.items() if block.get('route')}
    return {
        # Which scenario for which reason — a run where all three ended for one reason is a
        # different finding from one where each ended for its own, and the remedies differ.
        'scenarios': {name: block['reason'] for name, block in sorted(blocks.items())},
        'reasons': dict(sorted(counted.items())),
        **({'causes': dict(sorted(causes.items()))} if causes else {}),
        **({'disputed': dict(sorted(disputed.items()))} if disputed else {}),
        **({'routes': dict(sorted(routes.items()))} if routes else {}),
    }


#: The first `ended_early` reason that changes a row's RESULT rather than only how it reads.
#:
#: The per-scenario TIME budget does not kill a scenario. It LANDS it: the dialog ends between
#: turns, the closing turn is still delivered, `/finalize` runs, and the judges grade the turns
#: that were actually reached. So the row has a transcript, real eval cells and a real score — but
#: over a CENSORED conversation, which is neither a pass nor a fail. `pass` would certify work the
#: dialog never got to, and `fail` would publish the agent failing obligations its conversation
#: never reached; both are claims this run did not earn. It gets its own state instead, out of
#: `passed`/`failed` and out of the score, with its turn count and its cells kept — those ARE
#: measurements, of a shorter conversation.
#:
#: DISTINCT from `harness_scenario_timeout`, which survives as the derived hard backstop for a
#: true hang: that one still cancels before a transcript exists, so it is still an `error` row and
#: still counted in `harness_timeouts`. Landed-but-short and died-with-nothing are different
#: findings for different owners; one state for both is how a slow agent and a hung one read alike.
TIME_BUDGET_EXHAUSTED = 'time_budget_exhausted'


def _table_safe(s: str) -> str:
    """Annotations land verbatim in a markdown table cell: `|` breaks the row and a
    backtick breaks the code span, so neither may survive into an annotation string."""
    return s.replace('|', '/').replace('`', "'")


def conclusively_violated(cell: Mapping) -> bool:
    """The backend's `policies.policy_manager_graph.is_violation` — `violated` AND `status == 'success'`.

    THE predicate, spelled once for the two readers that need the SET of violated names: the
    per-turn list below, and the rescored-attempt view the stability recompute hands the SDK's
    classifier. A hedged verdict (`violated: true` + `status: 'inconclusive'`) is NOT a violation,
    however its flag reads, and counting one contradicts `pass_rate` in the same report.
    """
    return bool(cell.get('violated')) and cell.get('status') == 'success'


def violated_turn_idxs(cell: dict) -> list[int]:
    """CONCLUSIVELY violated turns only — `violated and status == 'success'`. The `turns`
    list deliberately carries hedged entries (`violated: true` + `status: 'inconclusive'`);
    counting those as violations contradicts pass_rate in the same report. The top-level
    cell's representative fields describe ONE turn (the server ORs `violated` across all),
    so a per-turn policy is always diagnosed FROM this list, never from the top cell."""
    return [t['turn_idx'] for t in cell.get('turns', []) if isinstance(t, dict) and conclusively_violated(t)]


def turn_grain_counts(cell: dict) -> tuple[int, int]:
    """`(passed, decided)` over the cell's OWN TURNS — the grain a `scope: turn` policy declares.

    Counted with the same rule as every other number in this file: a turn is DECIDED when its
    entry says `status: 'success'`, and it PASSED when that entry is not `violated`. The `turns`
    list carries hedged and errored entries too (`violated: true` + `status: 'inconclusive'` is
    the common one — see `violated_turn_idxs`), and counting those as decided would put cells
    that say nothing about the agent into the denominator a one-sidedness test divides by, which
    is the mistake `eval_signal`'s own docstring records at the scenario grain.

    NO new data and no second call: this reads the list `failing_annotation` above already walks
    to write `(turns 0,1,2 · 0-based)`. The scenario roll-up beside it is MONOTONE — one failing
    turn reds the whole scenario — so a turn-scoped judge that discriminates 3 of 20 turns rolls
    to 0 of 3 scenarios, and the report would then publish `no evidence it can pass` over a run whose
    own artifacts name the turns it passed.
    """
    decided = [t for t in cell.get('turns') or [] if isinstance(t, dict) and t.get('status') == 'success']
    return sum(1 for t in decided if not t.get('violated')), len(decided)


def failing_annotation(name: str, cell: dict) -> str:
    """The failing_evals entry. For a turn_based policy, name WHERE it failed — and say
    which base, since `turn_idx` is 0-based (turn_idx 0 is the first exchange). Annotations
    stay in scenarios[].failing_evals only, never in evals[].id: ids are truncated for the
    chart and each distinct string becomes its own bar, splitting one policy into N rows."""
    idxs = violated_turn_idxs(cell) if cell.get('scope') == 'turn' else []
    if idxs:
        return _table_safe(f'{name} (turns {",".join(str(i) for i in idxs)} · 0-based)')
    return _table_safe(name)


def policy_name(entry: object) -> str:
    """The bare policy name out of a run.json name-list entry — the inverse of the annotations above.

    Those entries are ANNOTATED strings (`name (turns 1,3 · 0-based)`, `name (n/a — premise
    absent · …)`), and comparing two annotations as if they were names reports a policy as moved
    whenever a judge reworded its reason. Split on the annotation opener, never on substring
    membership: a policy whose name is another's prefix goes missing under a join.
    """
    return str(entry).split(' (', 1)[0].strip()


def _quote_key(quote: object) -> str | None:
    """One finding's quote reduced to the key two judges quoting the SAME sentence agree on.

    Whitespace and case are normalized away, because that is how two independently-written
    judges differ when they are talking about one utterance. NOTHING else is: a looser key —
    a prefix, a substring, the first N characters — merges two DIFFERENT mistakes made in one
    turn into a single root, and that error is invisible from the report, because the reader
    never learns there were two. An over-strict key only fails to collapse, which is the state
    this whole function exists to improve on and which the report still shows honestly.

    `None` when there is no quote to group on — see `rooted_findings` for why an unquoted
    finding is a different KIND of charge rather than a root with an empty key.

    KNOWN LIMIT: this is EQUALITY after that fold. When one quote strictly CONTAINS another (a short
    quote and a longer span that includes it, charged by different judges), the backend counts one
    instance and this key counts two. So the rendered utterance count is a CEILING on the distinct
    mistakes, not an exact count, and the section says so.

    Equality is kept deliberately:

    * The backend's rule is a PAIRWISE predicate and is not transitive. Partitioning N findings with
      it is a single-linkage closure, which nothing has measured; adopting it here would ship an
      unmeasured partition under a measured rule's name.
    * A faithful copy needs the backend's fold and term weights as well, and a partial copy is a known
      way to diverge: two callers folding differently apply the threshold to different bytes. A parity
      guard over the predicate alone would pass while the two sides disagreed.

    Adopting the backend's rule (predicate, fold and weight, under a cross-repo parity guard like the
    one `_TRANSPORT_FAILURE_RE` has) is its own change with its own measurement.
    """
    if not isinstance(quote, str):
        return None
    return ' '.join(quote.split()).casefold() or None


#: A recorded speaker that is NOT the agent: a tool's result, the user's turn, a system prompt.
_NOT_AGENT_ROLES = frozenset({'tool', 'function', 'human', 'user', 'system'})


def _agent_voices(turns: object) -> dict[int, str]:
    """`{turn_idx: the agent's own folded text}` off the transcript's role-tagged turns — `agent_message`
    plus the text of every message on the turn (`agent_messages`, `turn_messages`) whose recorded `type`
    or `role` is not a tool's, the user's or the system's. A tool result never counts: the dataset rows
    and records an agent reads or writes ride there, and they are not its words. `{}` when the transcript
    carries no turns, which leaves every finding as it was."""
    voices: dict[int, str] = {}
    for position, turn in enumerate(turns if isinstance(turns, list) else []):
        if not isinstance(turn, dict):
            continue
        idx = turn.get('turn_idx', position)
        if not isinstance(idx, int) or isinstance(idx, bool):
            continue
        spoken = [m for m in [*(turn.get('agent_messages') or []), *(turn.get('turn_messages') or [])]
                  if isinstance(m, dict) and not _NOT_AGENT_ROLES & {str(m.get('type')), str(m.get('role'))}]
        agent = [turn.get('agent_message'), *(_message_text(m.get('content')) for m in spoken)]
        voices[idx] = _quote_key(' '.join(a for a in agent if isinstance(a, str))) or ''
    return voices


#: How a judge marks the words it left out of a quote.
_ELISION_RE = re.compile(r'\s*(?:\.{3,}|…)\s*')
#: Markdown emphasis and code marks, which a judge quoting rendered prose drops.
_MARKUP = str.maketrans('', '', '*`')


def _said_in(key: str, text: str) -> bool:
    """Whether the folded quote `key` is in the folded `text` — markdown marks aside, and each span
    between its elisions (`...`, `…`) in order, since a judge quoting a long reply shortens it that way."""
    at, text = 0, text.translate(_MARKUP)
    for part in filter(None, _ELISION_RE.split(key.translate(_MARKUP))):
        at = text.find(part, at)
        if at < 0:
            return False
        at += len(part)
    return True


def _message_text(content: object) -> str:
    """A message's text: a plain string, or the `text` of each list-typed content block."""
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    return ' '.join(b['text'] for b in blocks if isinstance(b, dict) and isinstance(b.get('text'), str))


#: The sandbox helper whose verdict is the agent's own DECLARED error replies (`plurai.config.yaml` `agent.error_replies`):
#: The backend `raw_error_replies`, which its `policies/fingerprint.py` keys on the same way (the helper named in the expression).
ERROR_REPLY_HELPER = 'raw_error_replies'
#: What an anchored error-reply charge says: the reply IS the finding.
ERROR_REPLY_WHY = 'The agent answered with its own error message instead of a reply.'


def declared_error_replies(workdir: str | None) -> list[dict]:
    """`agent.error_replies` off `plurai.config.yaml` — each `{text, match: exact|prefix}`; `[]` when none is declared."""
    doc, _ = _config_document(workdir)
    agent = doc.get('agent') if isinstance(doc, dict) else None
    replies = agent.get('error_replies') if isinstance(agent, dict) else None
    return [r for r in replies if isinstance(r, dict) and isinstance(r.get('text'), str) and r['text'].strip()
            and r.get('match') in ('exact', 'prefix')] if isinstance(replies, list) else []


def anchor_error_replies(cells: Mapping, bodies: Mapping | None, declared: Sequence[Mapping], turns: object) -> Mapping:
    """`cells`, with a QUOTABLE ANCHOR on each conclusively violated `code_eval` cell that grades the agent's declared error
    replies (its expression calls :data:`ERROR_REPLY_HELPER`) and filed no finding: one finding per turn whose agent reply
    IS a declared error reply — a `prefix` one it starts with, an `exact` one it equals on a turn where no tool call
    failed (the backend excuses that one as the agent handling the fault) — quoting that reply as said. The judges that
    charge the same reply then share its root in :func:`rooted_findings`, so one error reply reads as one mistake, not
    one per eval. Patterned raw errors (stack traces, SDK dumps) are the backend's to find and stay unanchored. Pure."""
    if not declared or not isinstance(bodies, Mapping):
        return cells
    out = dict(cells)
    for name, cell in cells.items():
        body = bodies.get(name)
        if (not isinstance(cell, dict) or cell.get('findings') or not conclusively_violated(cell) or not isinstance(body, Mapping)
                or body.get('type') != 'code_eval' or ERROR_REPLY_HELPER not in str(body.get('expression') or '')):
            continue
        anchors = []
        for position, turn in enumerate(turns if isinstance(turns, list) else []):
            said = turn.get('agent_message') if isinstance(turn, dict) else None
            idx = turn.get('turn_idx', position) if isinstance(turn, dict) else None
            if not isinstance(said, str) or not isinstance(idx, int) or isinstance(idx, bool):
                continue
            state = turn.get('agent_state')
            trail = state.get(_AGENT_STATE_TOOL_TRAIL_KEY) if isinstance(state, dict) else None
            faulted = any(isinstance(c, dict) and c.get('status') == 'error' for c in trail or [])
            text = ' '.join(said.split())
            if any(text.startswith(' '.join(r['text'].split())) if r['match'] == 'prefix' else (not faulted and text == ' '.join(r['text'].split()))
                   for r in declared):
                anchors.append({'turn_idx': idx, 'quote': said.strip(), 'why': ERROR_REPLY_WHY})
        if anchors:
            out[name] = {**cell, 'findings': anchors}
    return out


def rooted_findings(cells: Mapping, *, turns: object) -> list[dict]:
    """The scenario's CONCLUSIVE charges, rooted on the agent utterance each one is about.

    One agent sentence can break several policies at once, and every judge then files its own
    finding against it. When three judges charge
    `turn 1 · "I can confirm that a 30-day enrolment window is standard"` — the same turn index
    and a byte-identical quote — with three different reasons, the report would publish three
    failing evals. That is ONE agent mistake, and a reader counting rows over-states the agent's
    misbehaviour by however many judges happened to cover that sentence.

    So findings are grouped on `(turn_idx, normalized quote)` — the pair the judges already
    agree on whenever they are describing the same utterance — and each root carries the evals
    charging it WITH their own reasons, so nothing a judge said is lost in the collapse.

    NO VERDICT MOVES. Every cell keeps its own pass/fail, `failing_evals` still lists each
    charged policy, and `policy_violations` still counts cells — this list is the reader's index
    from those charges back to the agent's own sentences, not a re-scoring of them. Grouping
    here is also independent of the backend's `defers_to` authoring: it collapses on the
    evidence the judges filed, with no dependency on an author having declared the sibling edge.

    THREE things are deliberately NOT grouped:

    * a different turn, or a different quote on the same turn — different utterances, and a
      report that merged them would hide one mistake behind another;
    * a finding with NO quote. A turn-scoped charge (a per-turn word cap, say) names a turn and
      no sentence, so there is no evidence it is about the same utterance as a quoted finding on
      that turn — or as another unquoted one. Each gets its own root and is never merged with
      anything, which is why the unquoted key carries its own eval name and position;
    * a conclusively violated cell that filed NO finding at all — a `code_eval` charging a world
      state names no utterance, so it has no root here at all. It stays in `failing_evals`,
      where it stays; this list is about utterances, and inventing one for such a cell
      would be a fabricated quote.

    A quote the agent did not SAY is not an agent utterance either. An absence-shaped judge cites the
    user's request the agent left unmet; a judge of what the agent generated cites a dataset row the
    agent wrote through a tool. Neither is in that turn's agent text (`turns`, the transcript's
    role-tagged turns, read by `_agent_voices`), so it roots nothing and the eval counts under
    "named NO utterance". Attribution is by the recorded speaker: only a quote found in the agent's
    own text roots, and a turn the transcript did not record leaves its quotes as they were.

    Values ride RAW, not `_table_safe`. These are data a critic reads, not annotations built for
    a markdown table, and the renderer applies its own `_cell` to whatever it prints.
    """
    voices = _agent_voices(turns)
    roots: dict[tuple, dict] = {}
    # Eval-name order, so `charged_by` is the same list whatever order the verdicts file keeps.
    for name in sorted(cells):
        cell = cells[name]
        if not isinstance(cell, dict) or not conclusively_violated(cell):
            continue
        for position, finding in enumerate(cell.get('findings') or []):
            if not isinstance(finding, dict):
                continue
            idx = finding.get('turn_idx')
            # `bool` is an `int`: a `turn_idx: true` would otherwise root on turn 1.
            #
            # A finding whose turn is MISSING or not an int is dropped: it cannot be placed in the
            # conversation, and a root with no turn is not an utterance a reader can go and look
            # at. It is not silent — the eval then contributes no charge, so the rendered tail
            # counts it under "named NO utterance", which is the honest reading (this table has
            # nothing from it) and not a claim that the judge quoted nothing. Said here because
            # the two states LOOK the same in the tail.
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            quote = finding.get('quote')
            key = _quote_key(quote)
            agent_said = voices.get(idx)
            if key is not None and agent_said is not None and not _said_in(key, agent_said):
                continue
            root = roots.setdefault(
                # An unquoted finding's key is unique to that finding, so `setdefault` can never
                # join it to another — the rule above, spelled as the key rather than as a branch
                # somewhere else that a later edit could drop.
                (idx, key) if key is not None else (idx, None, name, position),
                {'turn_idx': idx, 'quote': quote.strip() if isinstance(quote, str) else None, 'charged_by': []},
            )
            root['charged_by'].append({'eval': name, 'why': finding.get('why')})
    # Turn order, then the quote, so the list reads in the order the conversation happened.
    # Sorting is STABLE, so the unquoted roots on one turn keep the eval-name order above.
    return sorted(roots.values(), key=lambda root: (root['turn_idx'], root['quote'] or ''))


#: THE VERDICT CONTRACT's vocabulary, read off the backend's `verdict_axis` block rather than retyped.
#: The literals below are the DOCUMENTED DEFAULT ON ABSENCE. The backend keeps `not_measured` apart
#: from the N/A set on purpose ("a suite whose scenarios are being truncated needs a different fix
#: than one whose premises never fire"), which a hand copy folding the two together would lose.
_VERDICT_AXIS: dict = _contract(  # type: ignore[assignment]
    'verdict_axis',
    default={
        'statuses': ['success', 'error', 'inconclusive'],
        'pre_demotion_statuses': ['not_applicable'],
        'not_applicable_reasons': ['mixed_not_applicable', 'not_applicable'],
        'not_measured_reasons': ['not_measured'],
        'deferred_reasons': ['deferred'],
        'own_axis_reasons': ['deferred', 'mixed_not_applicable', 'not_applicable', 'not_measured'],
    },
)

#: Every status a cell can carry ON THE WIRE, plus the RAW shape four the backend factories write
#: before `agent_node` demotes it at one seam. `/finalize` and the re-score path both pass that
#: seam, so no run ships the raw cell today; accepting it is defensive, because the token states
#: outright that the cell does not apply.
CELL_STATUSES = frozenset(_VERDICT_AXIS['statuses'])
PRE_DEMOTION_STATUSES = list(_VERDICT_AXIS['pre_demotion_statuses'])

NOT_MEASURED_REASON = 'not_measured'

#: A judge that handed its charge to a named sibling (the backend `defer_to_named_siblings`); its cell
#: names the owners (`deferred_to`) and the turns (`deferred_turns`, 0-based). Its OWN axis: the
#: judge decided and the owner's verdict carries the charge, so it is never an inconclusive eval.
DEFERRED_REASON = 'deferred'
DEFERRED_REASONS = frozenset(_VERDICT_AXIS.get('deferred_reasons') or [DEFERRED_REASON])

#: The premise never AROSE. Its OWN counter, because the backend publishes its own `not_applicable_count`
#: and folding it into the hedge column would make premises that never arose read as "the judge
#: could not decide".
NOT_APPLICABLE_REASONS = frozenset(_VERDICT_AXIS['not_applicable_reasons'])

#: The dialog that would have carried it was CENSORED — an absence-shaped judge whose violation
#: lands on turns the scenario time budget cut off. Its own counter beside the one above for the
#: reason the backend keeps them apart: a truncated suite and a premise-gated one are different repairs.
NOT_MEASURED_REASONS = frozenset(_VERDICT_AXIS['not_measured_reasons'])

#: The two reasons that MEASURED NOTHING. With `deferred` they are the backend's `own_axis_reasons`,
#: the reasons that roll up OUTSIDE `inconclusive_count`; `unreproduced_across_attempts` stays in it.
#:
#: Every OTHER reason means the judge saw a live premise and could not decide, which is a defect
#: to chase. Rendered alike, a correctly premise-gated judge — or a correctly demoted one — is
#: indistinguishable from a broken one, and the round buys a fix for a judge that is working.
NOTHING_MEASURED_REASONS = NOT_APPLICABLE_REASONS | NOT_MEASURED_REASONS


def cell_axis(cell: dict) -> str:
    """WHICH counter this cell belongs in — `passed`/`failed`/`not_applicable`/`not_measured`/
    `inconclusive`/`errored`.

    THE one reading of THE VERDICT CONTRACT in this file, so the counter, the name list and the
    reconciliation below cannot disagree about a cell all three are looking at. An N/A cell rides
    `status='inconclusive'` on the wire for compatibility, so the status alone is never the answer.

    Raises on a status the contract does not name. That is the point: a silent skip would
    enter no counter, no list and no total, so one unrecognised cell would leave `total` a row
    shorter and the row would still publish `pass_rate: 1.0`, at exit 0, named nowhere. A dropped cell is a vacuous pass.
    """
    status = cell.get('status')
    if status in PRE_DEMOTION_STATUSES:
        # The raw factory shape, before the wire demotes it. It states outright that it does not
        # apply, so it lands on the N/A axis rather than being refused or dropped.
        return 'not_applicable'
    if status == 'success':
        return 'failed' if cell.get('violated') else 'passed'
    if status == 'error':
        return 'errored'
    if status == 'inconclusive':
        reason = inconclusive_reason(cell) or ''
        if reason in NOT_APPLICABLE_REASONS:
            return 'not_applicable'
        if reason in NOT_MEASURED_REASONS:
            return 'not_measured'
        if reason in DEFERRED_REASONS:
            return 'deferred'
        # An unrecognised REASON stays a hedge — the backend's own rule, and `encode_inconclusive_reason`
        # '+'-joins ordinary gates, so a reason this build has not heard of is expected traffic.
        # Only the STATUS is a closed vocabulary.
        return 'inconclusive'
    # Absent and unrecognised are NAMED APART, because they point at different producers: a cell
    # with no status at all was written by something that never filled the field, while an
    # unrecognised one means a vocabulary moved and this copy of it did not. Both are the same
    # refusal — a cell that states no verdict enters no counter either way — but the reader's next
    # step differs, and `status None` alone reads as the second when it is the first.
    said = 'carries no `status` at all' if status is None else f'carries status {status!r}, which is outside'
    raise ValueError(
        f'the cell {said} the backend verdict contract '
        f'({", ".join(sorted(CELL_STATUSES | set(PRE_DEMOTION_STATUSES)))}). '
        'A cell that states no recognised verdict enters no counter and no denominator, so the row '
        'would publish a pass rate over a silently shorter total — re-vendor scripts/contracts/ '
        'against the backend that produced this run, or fix the producer.'
    )

#: `verdicts.json`'s closed reasons beside a state (docs/verdicts.md), on top of the cell vocabulary above: an eval the
#: guard turned off for this scenario (its own `skipped` counter and list), and an ungraded eval whose judge crashed.
GUARD_SKIP_REASONS = frozenset({'guard_skipped', 'turn_skipped_every_turn'})
JUDGE_ERROR_REASON = 'judge_error'


def eval_axis(entry: Mapping) -> str:
    """WHICH counter one `verdicts.json` eval belongs in — a lookup on its `(state, reason)`, never a re-reading of the
    cell. `pass` / `fail` are the conclusive verdicts; `not_applicable` splits into `skipped` (a guard), `deferred`
    (graded under its owner) and `not_applicable`; `ungraded` into `not_measured`, `errored` (the judge crashed) and
    `inconclusive` (every other reason, `inconclusive:<gate>` included). Raises on a state outside the four."""
    state, reason = entry.get('state'), entry.get('reason')
    if state == 'pass':
        return 'passed'
    if state == 'fail':
        return 'failed'
    if state == 'not_applicable':
        return 'skipped' if reason in GUARD_SKIP_REASONS else 'deferred' if reason in DEFERRED_REASONS else 'not_applicable'
    if state == 'ungraded':
        return 'not_measured' if reason in NOT_MEASURED_REASONS else 'errored' if reason == JUDGE_ERROR_REASON else 'inconclusive'
    raise ValueError(f'the eval carries state {state!r}, outside {", ".join(verdicts_file.STATES)}')


#: The `not_measured_cause` the backend stamps on a turn the CLIENT emptied — the runner produced no
#: agent speech and declared no actions, so the judges were never invoked (the backend's
#: `ADAPTER_NO_REPLY_REASON`). It rides BESIDE `inconclusive_reason`, never
#: inside it: `not_measured` is the exclusive axis every rollup matches on and may not be joined.
_ADAPTER_NO_REPLY_CAUSE = 'adapter_no_reply'

#: WHOSE an unmeasured turn is, per cause. Without this a report cannot tell "the dialog was cut
#: before this surface could exist" from "the runner emptied this turn" — and those are the clock
#: and the client, two different people. A cause this build does not know renders WITHOUT an
#: owner rather than borrowing one: an unattributed hole is honest, a misattributed one is not.
_NOT_MEASURED_OWNER = {_ADAPTER_NO_REPLY_CAUSE: 'harness'}


def unmeasured_turns(cells: dict) -> tuple[list[int], str | None]:
    """`(turns on which NOTHING was measured, the cause when the wire named one)`.

    A turn counts only when EVERY turn-scoped cell that reached it reads `not_measured`. One
    judge abstaining on a turn the others graded is a fact about that policy; calling the turn
    unmeasured on it would report a measured turn as a hole and inflate the very number an
    operator is meant to act on.

    The CAUSE is read off the rolled-up cell, because the backend's `_turn_stream_entry` carries
    `inconclusive_reason` per turn and not `not_measured_cause`. That is enough for the case this
    exists for — every turn emptied by the runner, where the representative turn is itself one of
    them — and a stream whose cause the wire did not carry renders unattributed rather than
    guessed.

    ONLY FROM A CELL THAT PRODUCED ONE OF THESE HOLES. First-wins across every turn-scoped cell
    would take the stamp off cells that contributed no reported turn at all, publishing
    `2 turns unmeasured (harness)` on the strength of an unrelated policy's `not_measured_cause` —
    and `(harness)` is a repair address, pointing the reader at the customer's own runner. Cells
    that disagree name no cause either: two causes over one hole is not one fact, and the rule
    this function is written to is that an unattributed hole is honest and a misattributed one is
    not.
    """
    verdict: dict[int, bool] = {}
    #: `{cause: turns the cell stamping it read as not_measured}` — collected, never resolved,
    #: until the holes themselves are known. A cell's own turns are what earn its cause a hearing.
    claims: dict[str, set[int]] = {}
    for cell in cells.values():
        if not isinstance(cell, dict) or cell.get('scope') != 'turn':
            continue
        declared = cell.get('not_measured_cause')
        cause_turns = claims.setdefault(declared, set()) if isinstance(declared, str) and declared else None
        for entry in cell.get('turns') or []:
            if not isinstance(entry, dict) or not isinstance(entry.get('turn_idx'), int):
                continue
            unmeasured = entry.get('status') == 'inconclusive' and entry.get('inconclusive_reason') == NOT_MEASURED_REASON
            idx = entry['turn_idx']
            verdict[idx] = verdict.get(idx, True) and unmeasured
            if unmeasured and cause_turns is not None:
                cause_turns.add(idx)
    holes = sorted(idx for idx, only in verdict.items() if only)
    named = {cause for cause, turns in claims.items() if turns & set(holes)}
    return holes, named.pop() if len(named) == 1 else None

def deferred_annotation(name: str, cell: dict) -> str:
    """The deferred_evals entry: the eval and WHO graded the case in its place — never a vote, never a turn index."""
    owners = [str(o) for o in cell.get('deferred_to') or [] if o]
    return _table_safe(f'{name} (graded under {", ".join(owners) if owners else "another eval"})')


#: Reader-facing prose per reason. An unknown reason prints VERBATIM rather than vanishing —
#: the server's vocabulary is open, and a silently dropped reason is a silently collapsed cell.
#:
#: `not_measured` names NO cause here. The backend writes that one token for several causes and
#: for a helper abstention carries none at all on the wire, so any fixed sentence is a guess a
#: dict literal cannot revise — and it printed "the dialog was cut" beside a cell whose own
#: reasoning said nothing was looked for, two contradictory causes on one row. The cause, when
#: there is one, travels in the cell's reasoning appended below.
_INCONCLUSIVE_PROSE = {
    'not_applicable': 'n/a — premise absent',
    'mixed_not_applicable': 'n/a — premise absent on the turns judged',
    'not_measured': "n/a — not measured (see the cell's own reason)",
    'low_confidence': 'low confidence',
    'weak_agreement': 'weak agreement',
    'no_evidence': 'no evidence',
    'sample_error': 'sample error',
    'absence_after_turn_cap': 'set aside — the turn cap ended the dialog',
    'contested_applicability': "undecided — the judge's samples split on whether the rule applied",
}

#: The backend's sampling tally, prepended to a judged cell's reasoning (`policy_manager_graph`: `[self-consistency 2/3 agree
#: violated=True]`, `[not applicable by 2/3 sample(s); 1 dissenting verdict(s) (violated=True) outvoted on
#: applicability]`, `[not measured by …]`). Judge machinery, never customer text: the note drops it.
_SAMPLING_TALLY = re.compile(r'^\[(?:self-consistency |not applicable by |not measured by )[^\]]*\]\s*')


def inconclusive_reason(cell: dict) -> str | None:
    """The cell's typed `inconclusive_reason`, or None when it declares none (an older
    server wrote no reason at all, and inventing one would manufacture the distinction)."""
    reason = cell.get('inconclusive_reason')
    return reason if isinstance(reason, str) and reason else None


def inconclusive_annotation(name: str, cell: dict) -> str:
    """The inconclusive_evals entry, saying WHY the judge left the cell open.

    The reason leads: `not_applicable` means nothing was measured, everything else means the
    judge could not decide. The `agreement` vote split rides along when there is one — the
    same split dents pass_rate in one scenario and passes silently in another purely on which
    way a vote fell, so the reader must see it. A premise-absent cell suppresses it: its
    agreement is agreement that the premise was absent, not a verdict about the agent.
    """
    reason = inconclusive_reason(cell)
    bits = [_INCONCLUSIVE_PROSE.get(reason, reason)] if reason else []
    agreement = cell.get('agreement')
    if agreement is not None and reason not in NOTHING_MEASURED_REASONS:
        vote = f'{agreement:g}' if isinstance(agreement, float) else str(agreement)
        bits.append(f'{vote} agreement')
    # THE CELL'S OWN SENTENCE, for the two reasons whose fixed prose is a CAUSAL CLAIM. A code
    # alone renders as one — "its premise never arose … which is not a defect" — and on a cell that
    # reads "no entry carries a beat_id" that sentence would stand over a BACKEND defect. The code says which channel; only the
    # grader's own reasoning says WHICH measurement failed, and the difference is whether the
    # reader goes to /edit-evals or to the harness.
    if reason in NOTHING_MEASURED_REASONS and (why := _cell_reasoning(cell, bits[0] if bits else '')):
        bits.append(why)
    return _table_safe(f'{name} ({" · ".join(bits)})' if bits else name)


#: How much of a grader's reasoning reaches the row. Enough for the sentence that names the
#: failed measurement, bounded because these render inside a markdown table cell.
_REASONING_EXCERPT = 220


def _cell_reasoning(cell: dict, label: str = '') -> str:
    """The grader's own words, first sentence-ish, or `''`.

    Stripped of anything the row ALREADY says beside it: the two builders' channel prefixes
    (`Not applicable — `, `Not measured — `) and every phrase of `label`, which is the
    rendered `_INCONCLUSIVE_PROSE` for this same cell. Repeating it inside the reason is how
    the row reads as two claims about one cell.

    The label half is DERIVED rather than listed because a hardcoded list goes stale: the grader
    also writes `premise absent — `, which is the tail of `n/a — premise absent`, and a spelling the
    list misses leaks into the row, rendering e.g.
    ``bounded_decline_judge (n/a — premise absent · premise absent — no topic was re-asked…)``.
    A new reason token costs nothing here.
    """
    if _corrected_to(cell, 'not_applicable'):
        # A BACKEND correction (the backend `red_cell_audit`) keeps the judge's reasoning, which argues the verdict the audit
        # overruled; the customer reads the sentence a plugin correction of the same unit shows (`correction_reason`).
        return correction_reason('not_applicable')
    raw = cell.get('reasoning')
    text = _SAMPLING_TALLY.sub('', ' '.join(str(raw).split())) if isinstance(raw, str) else ''
    said = ['Not applicable', 'Not measured'] + [part.strip() for part in label.split('—') if part.strip()]
    # UNTIL NOTHING MATCHES, not once. Stopping after the first hit would leave a DOUBLED prefix half
    # stripped — the leak this function exists to close — and the grader
    # writes both `n/a — premise absent` and `premise absent — `, so two of the phrases above can
    # legitimately open the same string. Bounded by `said`: each pass must consume something, so
    # it terminates.
    for _ in range(len(said)):
        for phrase in said:
            for dash in (' — ', ' - '):
                prefix = f'{phrase}{dash}'
                if text[: len(prefix)].casefold() == prefix.casefold():
                    text = text[len(prefix) :]
                    break
            else:
                continue
            break
        else:
            break
    return text if len(text) <= _REASONING_EXCERPT else text[: _REASONING_EXCERPT - 1].rstrip() + '…'


#: The key the backend's red-cell audit stamps on a cell (and on each turn) it corrected (`red_cell_audit.AUDIT_CORRECTION_KEY`).
AUDIT_CORRECTION_KEY = 'audit_correction'


def _corrected_to(cell: dict, corrected: str) -> bool:
    """Whether a backend audit correction set this cell, or a turn of it, to `corrected`. A turn-stream cell carries
    `corrected` only on its turns' stamps."""
    stamps = [cell.get(AUDIT_CORRECTION_KEY), *(t.get(AUDIT_CORRECTION_KEY) for t in cell.get('turns') or [] if isinstance(t, dict))]
    return any(isinstance(s, dict) and s.get('corrected') == corrected for s in stamps)


def merged_policy_order(orders: list[list[str]], skipped: list[list[str]]) -> list[str]:
    """ONE row per policy in the EVAL SET, not one per policy that happened to be judged.

    The order is a merge of each scenario's `verdicts.json` entry order (a new name slots
    in after the nearest name already placed), so the rows read in the eval set's order
    even though no single scenario judged every policy. Names that `verdicts.json` marks
    gated off in every scenario — judged nowhere — are unioned in at the end: without this the eval
    simply vanishes from the report, and a judge that graded NOTHING is precisely what the
    Eval-signal table exists to surface."""
    union: list[str] = []
    for order in orders:
        prev = -1
        for name in order:
            if name in union:
                prev = union.index(name)
            else:
                prev += 1
                union.insert(prev, name)
    for names in skipped:
        for name in names:
            if name not in union:
                union.append(name)
    return union


def _reconcile_cells(scenario: str, staged: list, expected: Iterable[str]) -> None:
    """Every eval this scenario's `verdicts.json` holds must have landed in exactly one counter — a
    new axis the loop was not taught is caught here. The file itself is complete by construction
    (its writer refuses a partial roster), so there is no census to tie it to.
    """
    counted = {name for name, _state, _reason in staged}
    if missing := sorted(set(expected) - counted):
        raise SystemExit(
            f'{scenario}: {len(missing)} eval(s) entered no verdict counter — {", ".join(missing)}. '
            'A dropped eval is a vacuous pass: it leaves `total`, so the row publishes a pass rate over '
            'a denominator shorter than the set it was scored against.'
        )


#: The counters that are NOT part of the row shape a reader can index — added with the no-verdict axes and
#: emitted only when non-zero, per this file's own rule one function down: a zeroed
#: `not_applicable` asserts "we looked and none were premise-absent", which a run against a server
#: that writes no reason at all has not earned. `inconclusive` keeps its unconditional key: it is
#: not an addition, and every existing reader indexes it.
_CONDITIONAL_COUNTS = ('not_applicable', 'not_measured', 'deferred')


def _zero_counts() -> dict[str, int]:
    """A policy's verdict counters, all zero. A FACTORY, not a shared constant: `bump` mutates
    the dict it gets. Spelled once so the counters a policy is bumped through and the row shape
    a never-judged policy renders as cannot acquire different keys.

    FOUR no-verdict-of-its-own counters, matching the four axes the backend publishes off `/finalize`
    (`not_applicable_count` / `not_measured_count` / `deferred_count` / `inconclusive_count`) — never one.
    Folding `not_measured` into `not_applicable` would present a measurement gap as an absent premise.
    """
    return {'passed': 0, 'failed': 0, 'not_applicable': 0, 'not_measured': 0, 'deferred': 0, 'inconclusive': 0, 'errored': 0, 'skipped': 0}


def eval_rows(names: list[str], counts: dict[str, dict[str, int]],
              reasons: dict[str, dict[str, int]],
              turn_counts: dict[str, dict[str, int]] | None = None,
              owners: dict[str, set[str]] | None = None) -> list[dict]:
    """One `evals` row per policy in `names` — the eval set's order, from `merged_policy_order`.

    A policy with no entry in `counts` was never judged anywhere and still gets a row: that is
    the whole point of taking the order from the SET rather than from what happened to be
    scored.

    `turn_counts` is the DECLARED-GRAIN pair for the policies whose cells carry `scope: turn`,
    and for no others — see `turn_grain_counts`. Optional, because a caller that has not
    gathered it is asking for exactly the scenario-grain rows.
    """
    rows = []
    for name in names:
        c = counts.get(name, _zero_counts())
        # total = the CONCLUSIVE count (passed + failed) and pass_rate divides by it —
        # an inconclusive cell in the denominator renders a judge that reached no verdict
        # as "all fail", a claim about the agent drawn from cells that carry none.
        total = c['passed'] + c['failed']
        row = {'id': name, **{k: v for k, v in c.items() if v or k not in _CONDITIONAL_COUNTS}, 'total': total}
        # Present ONLY when the server typed at least one hedge. An empty histogram would
        # assert "we looked and none were premise-absent", which a run against a server that
        # writes no reason at all has not earned — the renderer reads absence as "unknown"
        # and keeps the label it had.
        if reasons.get(name):
            row['inconclusive_reasons'] = dict(sorted(reasons[name].items()))
        # WHO graded its deferred cells, beside the `deferred` count — present only when it has one.
        if (owners or {}).get(name):
            row['deferred_to'] = sorted(owners[name])  # type: ignore[index]
        # …and when it decided NOTHING there is no rate: the key is omitted rather than set
        # to 0, which a reader (or a sort) takes for "this judge failed everything". The
        # counts beside it say what actually happened — hedged, errored, or never run.
        if total:
            row['pass_rate'] = round(c['passed'] / total, 3)
        # THE POLICY'S OWN GRAIN, beside the scenario roll-up — never instead of it. Every field
        # above is the scenario-grain measurement (the table's `0/3 passed`, the
        # pass-rate chart, `policy_violations`); these two say how many TURNS the same cells
        # decided, which is the only grain at which "did it ever come out the other way?" is a
        # question about a `evaluation_scope: turn_based` judge. Present ONLY for a policy whose
        # cells declared that scope AND decided at least one turn: a zero pair would assert a
        # measurement nothing made, and a rate computed from it divides by nothing.
        if grain := (turn_counts or {}).get(name):
            if grain.get('decided_turns'):
                row.update(grain)
        rows.append(row)
    return rows


#: The runner's `harness_*` namespace is OPEN — the SDK reads the slug off a class attribute,
#: so it grows whenever a new failure class declares one. These THREE are the ones that mean a
#: BUDGET expired, and only these may be counted as timeouts: matching the bare prefix instead
#: would launder any future harness kind (a dead mock, say) into "hit the turn budget" and hand
#: the reader a stopwatch to fix it. The same list, with each knob, is stated for readers in
#: `/simulation-run`'s `error_kind` vocabulary table — that table and this frozenset are the
#: two halves of one contract; move them together.
HARNESS_TURN_TIMEOUT = 'harness_turn_timeout'
HARNESS_SCENARIO_TIMEOUT = 'harness_scenario_timeout'
HARNESS_FINALIZE_POLL_TIMEOUT = 'harness_finalize_poll_timeout'
HARNESS_TIMEOUT_KINDS = frozenset({HARNESS_TURN_TIMEOUT, HARNESS_SCENARIO_TIMEOUT, HARNESS_FINALIZE_POLL_TIMEOUT})
HARNESS_KIND_PREFIX = 'harness_'
#: A non-timeout harness kind this script advises on by name: the harness's `seed_state`
#: hook could not be called as the seed contract requires (missing on a resumed scenario, or a
#: wrong arity). A TEMPLATE defect — never the agent, and never a budget.
HARNESS_SEED_STATE_CONTRACT = 'harness_seed_state_contract'
#: The harness voided its own row: it raised `plurai.HarnessFault`, or made a world write the mock
#: refused (a key no entity holds). A HARNESS-CODE defect — never the agent, and never a budget.
HARNESS_FAULT = 'harness_fault'
#: The backend's mock runtime could not generate a mocked tool's answer. Not the agent, not a budget,
#: and not the harness's code: the backend's. error.json's typed `transient` decides the remedy.
HARNESS_MOCK_RUNTIME = 'harness_mock_runtime'
#: The runner could not reach the deployed mock, so the world the evals would read is not the mock's. Not the agent,
#: not a budget. Its remedy is NOT a flat string — see `mock_unreachable_remedy` below.
HARNESS_MOCK_UNREACHABLE = 'harness_mock_unreachable'
#: The remedy when the phase is unknown: no claim about whether the agent dialed a mocked tool.
MOCK_UNREACHABLE_REMEDY = (
    'the runner could not use the deployed mock, so this scenario is not counted — re-run it; if it repeats, recover the '
    'deployment (switch to the env, else deploy_env)'
)


def mock_unreachable_fault(d: Path) -> dict:
    """`{raised_at}` off a `harness_mock_unreachable` error.json — the SDK's own typed phase tag (`'init'` /
    `'finalize'`), stamped at every `MockUnreachableError` raise site. Unreadable, or an older SDK build that
    predates the field, reads as `None`: 'unknown', never a guessed phase — `turns_reached` was tried first
    (round 4) and dropped: a pipeline-shape hop's own finalize-capture raise site never increments it, so
    that counter reads 0 there identically to a genuine init failure (round-4 verify N1)."""
    try:
        doc = _read_json(d / 'error.json')
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        doc = None
    doc = doc if isinstance(doc, dict) else {}
    raised_at = doc.get('raised_at')
    return {'raised_at': raised_at if raised_at in ('init', 'finalize') else None}


def mock_unreachable_remedy(fault: dict) -> str:
    """The ONE remedy for a `harness_mock_unreachable` row, stderr and report alike. This kind has (at
    least) THREE SDK raise sites with different preconditions — init, dialog finalize-capture, and
    pipeline-hop finalize-capture — keyed by the SDK's own typed `raised_at` (never `turns_reached`, which a
    pipeline hop never touches, and never a prose heuristic). At INIT the agent got no turn in before the
    mock's own health check failed — "no mocked tools, wrong --mock-mode" is a live possibility. At FINALIZE
    the agent already drove its dialog or hop against the mock; only the final state capture failed, so the
    posture check would be a non-sequitur. An unknown phase gets neither phase-specific claim:
    `MOCK_UNREACHABLE_REMEDY`."""
    if fault.get('raised_at') == 'finalize':
        return (
            'the runner could not use the deployed mock, so this scenario is not counted — the mock stopped answering '
            'after the run started; re-run it, and if it repeats, recover the deployment (switch to the env, else deploy_env)'
        )
    if fault.get('raised_at') == 'init':
        return f'{MOCK_UNREACHABLE_REMEDY}; an agent with nothing to mock runs --mock-mode mockless'
    return MOCK_UNREACHABLE_REMEDY


#: The AGENT raised in its own turn: nothing expired and nothing of ours failed. A FAILED row (the SDK's
#: trials block counts the same trial as a failed goal), never errored; its `error.json` names the agent code.
AGENT_RAISED = 'agent_raised'
#: `verdicts.json` `status.value`s that decide a row's result before any verdict is read (`verdicts_file.STATUSES`):
#: the agent crashed (a fail, scored 0); our setup or infrastructure failed (errored, out of the score); the time
#: budget cut the dialog (incomplete, out of the score).
AGENT_ERROR, HARNESS_ERROR, INCOMPLETE = 'agent_error', 'harness_error', 'incomplete'
#: The writer's cause for an attempt that finalized and that nothing graded. Not an `error.json` kind: nobody knows what
#: happened (the judge job crashed, /finalize returned before scoring), so the row stays untyped and is named on stderr.
NOT_GRADED = 'not_graded'
#: The Plurai backend answered the runner's own call with an HTTP error (`status`, `transient`): infrastructure.
#: Errored and out of every rate, never the agent's failure. error.json's typed `transient` and `status` pick the remedy.
BACKEND_HTTP = 'backend_http'
#: The non-transient backend statuses that name the OPERATOR's request or config, each with its one remedy. Any other
#: non-transient status is the backend's and the SDK's contract, and so is a 404 on a request addressing a thread the
#: backend itself created (`BACKEND_THREAD_URL`): it lost it. Those rows take `BACKEND_FAULT_REMEDY`.
BACKEND_HTTP_OPERATOR_REMEDIES = {
    401: 'check the Plurai credentials this run was configured with, then re-run it',
    403: 'check the Plurai credentials this run was configured with, then re-run it',
    404: 'check the scenario and eval set ids this run was given, then re-run it',
}
#: The contact channels, spelled here because this file is copied out of the plugin and `scripts/stop_message.py` does not
#: travel with it; `test_build_run_json_stop_constants.py` pins the string to that module's.
CONTACT_CHANNELS = 'support@plurai.ai or on Discord: https://discord.gg/bg34gh76uJ'
#: A scenario row is not a stop: its remedy for a backend fault names the contact channels in its own sentence and never
#: carries the hand-off text.
BACKEND_FAULT_REMEDY = f'if it happens again, reach out at {CONTACT_CHANNELS}'
#: The agent's LLM provider or model runtime refused or failed the agent's own call (a 429, a 5xx, a dropped
#: connection, a rejected key or model, the CLI missing): infrastructure. Errored and out of every rate, never the agent's failure.
PROVIDER_INFRA = 'provider_infra'
PROVIDER_INFRA_REMEDY = (
    "the agent's LLM provider or model runtime failed its call, so this scenario is not counted — "
    'fix the key, model or runtime if it was rejected or missing, then re-run it'
)


def agent_raise(doc: dict) -> str:
    """The agent's exception and its message, off an agent_raised error.json: its `caused_by` is the child's own
    `<class>: <message>`, which carries none of the runner's wording; `error` only when an older record lacks it."""
    cause = doc.get('caused_by')
    said = cause.get('error') if isinstance(cause, dict) else None
    return str(said or doc.get('error') or 'no error recorded')


#: The runner's wording around a child's raise in a LANDED dialog's `turn_failure.error` (SDK `_subprocess`:
#: `child reported {cls} during {phase}: {message}`); `child_exc_class` is the typed `{cls}`.
_CHILD_REPORTED = 'child reported {cls} during '


def landed_raise(failure: object) -> str:
    """The agent's exception and its message, off a landed dialog's `turn_failure`, in the shape :func:`agent_raise`
    reads off error.json (`<class>: <message>`): the runner's wording is dropped only where the typed `child_exc_class`
    opens it; any other `error` is said as it is."""
    failure = failure if isinstance(failure, dict) else {}
    error, cls = failure.get('error'), failure.get('child_exc_class')
    if not isinstance(error, str) or not error.strip():
        return 'no error recorded'
    lead = _CHILD_REPORTED.format(cls=cls) if isinstance(cls, str) and cls else None
    if lead and error.startswith(lead) and ': ' in error[len(lead):]:
        return f'{cls}: {error[len(lead):].split(": ", 1)[1]}'
    return error


#: The goal-ladder keys an agent crash restates on a LANDED row; the backend's served lists beside them stay as served.
_GOAL_LADDER_KEYS = ('status', 'goal_status', 'goal_achieved', 'goal_source', 'reason')


def agent_raised_goal(error: str, *, owned: bool) -> dict:
    """The goal block of a row whose AGENT raised in its own turn (no verdict was published): the goal is
    FAILED, owned by the crash, or N/A (`goal_source: none`) when the eval set declared no goal owner."""
    reason = f'the agent raised in its own turn: {error}'
    status = 'failed' if owned else 'not_evaluated'
    return {'status': status, 'goal_status': status, 'goal_achieved': False if owned else None,
            'goal_source': AGENT_RAISED if owned else GOAL_SOURCE_NONE,
            'catastrophic_violations': [], 'decisive_violations': [], 'measurement_clean': None,
            'reason': reason if owned else f'{reason}; the eval set declared no goal owner'}


#: A request addressing one thread the backend created (`/threads/<id>/...`), off error.json's typed `request_url`.
BACKEND_THREAD_URL = re.compile(r'/threads/[^/?#]+')


def backend_http_fault(d: Path) -> dict:
    """`{transient, status, detail}` off a `backend_http` error.json; unreadable reads as not transient, status unknown."""
    try:
        doc = _read_json(d / 'error.json')
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        doc = None
    doc = doc if isinstance(doc, dict) else {}
    status = doc.get('status')
    body = doc.get('response_body')
    try:
        parsed = json.loads(body) if isinstance(body, str) else None
    except ValueError:
        parsed = None
    detail = parsed.get('detail') if isinstance(parsed, dict) else None
    if not (isinstance(detail, str) and detail.strip()):
        detail = body if isinstance(body, str) and body.strip() else str(doc.get('error') or 'no detail recorded').split('\n', 1)[0]
    url = doc.get('request_url')
    return {'transient': doc.get('transient') is True, 'status': status if isinstance(status, int) and not isinstance(status, bool) else None,
            'detail': detail.strip()[:200], 'thread_addressed': isinstance(url, str) and bool(BACKEND_THREAD_URL.search(url)),
            'credit': credit_code(doc)}


def credit_code(doc: object) -> str | None:
    """The credit kind an error.json names in its `credit` block, or None when it carries none."""
    block = doc.get('credit') if isinstance(doc, dict) else None
    code = block.get('code') if isinstance(block, dict) else None
    return code if isinstance(code, str) and code.strip() else None


def credit_remedy(fault: dict) -> str:
    """The ONE remedy for a row the backend refused for credit, stderr and report alike. It names no re-run and no
    hand-off: the run's own stop message says what the user does next."""
    return (f"the Plurai backend refused work for credit ({fault.get('credit') or 'credit-refused'}), so this scenario is "
            'not counted — not a verdict about the agent; do not re-run it, the run\'s stop message says what to do')


def backend_http_remedy(fault: dict) -> str:
    """The ONE remedy for a backend_http row, stderr and report alike: re-run a transient one; otherwise name the
    status and the backend's detail, and send the operator to what the typed status says is theirs, else to the contact
    channels."""
    if fault.get('credit'):
        return credit_remedy(fault)
    status = fault.get('status')
    said = f'{status}: {fault.get("detail")}' if status is not None else fault.get('detail') or 'no detail recorded'
    if fault.get('transient') is True:
        return f'the Plurai backend did not answer ({said}), so this scenario is not counted — re-run it'
    remedy = BACKEND_HTTP_OPERATOR_REMEDIES.get(status, BACKEND_FAULT_REMEDY)
    if status == 404 and fault.get('thread_addressed'):
        remedy = BACKEND_FAULT_REMEDY  # the backend lost a thread it created: no id of the operator's explains it
    return f'the Plurai backend refused the request ({said}), so this scenario is not counted — {remedy}'


def mock_runtime_fault(d: Path) -> dict:
    """`{transient, cause}` off a `harness_mock_runtime` error.json; unreadable reads as not transient."""
    try:
        doc = _read_json(d / 'error.json')
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        doc = None
    doc = doc if isinstance(doc, dict) else {}
    return {'transient': doc.get('transient') is True, 'cause': str(doc.get('cause') or 'no cause recorded'),
            'credit': credit_code(doc)}


def mock_runtime_remedy(fault: dict) -> str:
    """The ONE remedy for a mock-runtime row, stderr and report alike."""
    cause = fault.get('cause') or 'no cause recorded'
    if fault.get('credit'):
        return credit_remedy(fault)
    if fault.get('transient') is True:
        return f"the backend's mock runtime did not answer ({cause}) — not a verdict about the agent; re-run the scenario"
    return f"the backend's mock runtime cannot answer on this deployment ({cause}) — re-running will not help; it is the backend's to fix"


#: The `error_kind` for a scenario that reached no verdict because every policy in the set was
#: GATED OFF for it — the guards all said does-not-apply. Not a runner slug: nothing crashed, so
#: the runner has nothing to type. It is minted here because the row still has to say `error`
#: (nothing scored) and a bare `error` is read downstream as the AGENT crashing.
NO_APPLICABLE_POLICIES = 'no_applicable_policies'


def errored_row(name: str, tools: dict, kind: str | None = None,
                skipped: list[str] | None = None, ended_early: dict | None = None,
                result: str = 'error', goal: dict | None = None) -> dict:
    """The row for a scenario that scored NOTHING — an ungraded `verdicts.json` (the runner's
    error row), or a graded one no judge reached a conclusion in. It contributes no eval cell and
    no score to the mean (the caller stamps an agent crash 0). ONE builder for every way of getting here — a tail written out
    twice is free to drift. `turns` is unknowable, so the key is dropped (the renderer
    shows "—"), never emitted null.

    `error_kind` rides along when the failure has a type. It exists because a bare
    `result: "error"` is read downstream as the AGENT crashing — /simulation-fixes says so in
    as many words — and a scenario killed by a simulation budget, or gated off entirely, has no
    transcript for anyone to check that reading against. Absent when unknown, never blank.

    `skipped` is the policy names this scenario's guards gated off. It is passed ONLY on the
    never-graded path, where it is the only record that those policies are gated off — the
    run predates the guards, so the transcript names them nowhere. Hard-coding `[]` here would
    discard the guard-skip record and leave the row indistinguishable from a crash.

    `ended_early` is ORTHOGONAL to `error_kind` and carried beside it, never folded in: one says
    the row reached no verdict, the other says why the DIALOG stopped. A scenario can have both
    (every turn emptied by the harness ends the dialog `adapter_degraded` and can still lose its
    finalize), and collapsing them would let a harness defect render as a crash.

    `result` is `error` for every way of scoring nothing EXCEPT one: a scenario the backend did
    reach — and told us it evaluated no goal on — is `not_evaluated`, which is a fact about the
    EVAL SET, not a crash. `error` there would send the reader hunting an error.json that does
    not exist. `goal` carries the backend's verdict block on both paths so the two are one shape."""
    row = {'id': name, 'result': result, 'score': None,
           'tool_calls': tools['calls'], 'tool_errors': tools['errors'], 'tool_refusals': tools['refusals'],
           'tool_calls_recovered': tools['recovered'],
           **({'tool_calls_backend': tools['backend_calls']} if 'backend_calls' in tools else {}),
           'deterministic': None, 'failing_evals': [], 'not_applicable_evals': [], 'inconclusive_evals': [],
           'deferred_evals': [], 'skipped_evals': [_table_safe(n) for n in (skipped or [])], 'needs_review': []}
    if goal is not None:
        row['goal'] = goal
    if kind:
        row['error_kind'] = kind
    if ended_early:
        row['ended_early'] = ended_early
    # An errored row's tool counts are a floor for exactly the same reasons a graded row's are,
    # and it is the row a reader reaches for FIRST when asking whether the environment was the
    # problem. Absent when the union verified everything, which is the ordinary run.
    if tools.get('floor'):
        row['tool_count_floor'] = dict(tools['floor'])
    if tools.get('unclassified'):
        row['unclassified_dispatch_outcomes'] = dict(tools['unclassified'])
    if tools.get('events_unread'):
        row['tool_events_unread'] = True
    if tools.get('refused_attempts'):
        row['refused_attempts'] = list(tools['refused_attempts'])
    return row


def roster_totals(out: Path, dirs: list[Path], expected_override: int | None) -> dict[str, int | bool]:
    """The totals the runner's own roster implies, and the warnings that go with them.

    Everything this file says about the run AS A WHOLE rather than about any one scenario —
    `missing_scenarios` and `unscored_run`. Both are findings, so both are absent unless earned:
    a zero would assert "we checked and there were none", which a run against an older runner
    that wrote no roster has not earned.
    """
    # What the run was SUPPOSED to produce, against what wrote artifacts. The caller's count
    # wins over the runner's file when both are there (a caller passing it knows something
    # the roster does not — a subset re-run, a roster written before a later scenario was
    # added). A shortfall is BOTH shouted and recorded: a report that silently drops the
    # scenarios that died reads as a complete run, which is the one thing it must never do.
    expected, declared, scored = declared_scenarios(out)
    if expected_override is not None:
        expected = expected_override
    found = {d.name for d in dirs}
    unwritten = sorted(declared - found)
    # Both readings, worst case: the roster names WHICH ones never wrote, an explicit count
    # can exceed the roster (a scenario added after it was written), and neither may mask the other.
    shortfall = max(len(unwritten), (expected - len(dirs)) if expected is not None else 0)
    totals: dict[str, int | bool] = {}
    if shortfall:
        named = f' — {", ".join(unwritten)}' if unwritten else ''
        print(f'WARNING: {shortfall} of {expected} scenarios wrote NO artifacts at all{named}. '
              'They are absent from this report, so it does not describe the whole run.', file=sys.stderr)
        totals['missing_scenarios'] = shortfall
    graded = graded_scenarios(out)
    if graded is not None and declared:
        # THE PER-ROW READING. `scored` is `graded >= scenarios`, so ONE ungraded
        # row makes it false, and the banner it drives tells the reader every verdict in the
        # report is a placeholder, even over a run that graded most of its rows. The count is
        # what the renderer says instead; the banner is kept for the one case it describes.
        totals['graded_scenarios'] = len(graded & declared)
        totals['scenario_total'] = len(declared)
    if not (graded & declared if graded is not None and declared else set()) and (scored is False or (graded == set() and declared)):
        # NOTHING was graded, so every score in these artifacts is a "not measured" 0.0 — read
        # as verdicts they render a run that reached no verdict as a finished one. Recorded,
        # never inferred: absent means the runner did not say.
        totals['unscored_run'] = True
        print('WARNING: the runner reports this run was NEVER SCORED — the eval verdicts in these artifacts are '
              'placeholders, not measurements. Score the run before reading any rate off this report.', file=sys.stderr)
    elif totals.get('graded_scenarios') is not None and totals['graded_scenarios'] < totals.get('scenario_total', 0):
        print(f'note: {totals["graded_scenarios"]} of {totals["scenario_total"]} scenario(s) produced a policy verdict. '
              'The rows that did are real measurements; the rest are not, and the report says which.', file=sys.stderr)
    elif expected is not None and len(dirs) > expected:
        # Say only what was checked. Dirs are selected on artifact PRESENCE, with no mtime floor
        # and no comparison against this run's start, so this code makes no
        # claim about "a previous run's artifacts": the ordinary cause is a partial retry into the
        # same OUT. The aggregation is correct either way; what the reader needs is that those
        # rows are older than this invocation's envelope stamps.
        print(f'note: {len(dirs) - expected} scenario dir(s) beyond the {expected} this run declared — '
              'they are being aggregated too. Ordinarily these are earlier attempts of the SAME suite '
              '(a partial re-run into the same directory); their rows carry the earlier verdicts while '
              'run.json is stamped with this invocation\'s eval set / gt revision / agent version.', file=sys.stderr)
    return totals


def build_spec(out: Path, args: argparse.Namespace) -> dict:
    """`run.json` for the run dir `out` (:func:`_spec_of`) — on a run of N trials, its `totals` and `evals` pooled over
    every trial (:func:`pool_trials`), the per-trial rows beside them (`trial_rows`).

    FIRST the SDK recomputes the run's scores (`plurai scores refresh`, :func:`run_scores`), so every row's result,
    score and fail reasons, and the run's suite score and agent verdict, are read off the verdicts as they stand now."""
    scores_block, trial_dir = run_scores(out)
    spec = _spec_of(out, args, scores_block, trial_dir)
    if isinstance(trials := spec.get('trials'), dict):
        specs = trial_specs(out, args, trials, spec, scores_block)
        spec['trial_rows'] = trial_rows(trials, specs)
        pool_trials(spec, specs)
    apply_c7(spec, c7_totals(out, spec.get('trials')))
    apply_scores(spec, scores_block)
    # A hollow row is said removed, and `retire` drops it, only on a run that measured the set whole: a stopped run, or one
    # that lost scenarios, drops nothing (`regressions.hollow_rows`), so none of its rows is marked hollow.
    if _regressions is not None and _regressions._run_measured_whole(spec) is not None:  # noqa: SLF001
        for row in spec.get('regressions') or []:
            if isinstance(row, dict):
                row.pop('hollow', None)
    return spec


def run_scores(out: Path) -> tuple[dict | None, str]:
    """`(the run's scores block, this trial's folder under the run dir)` — the block `plurai scores refresh` just wrote
    (`verdict_events.refresh_scores`) over the run `out` belongs to (`out` itself, or its parent for a `trial_<k>` dir).
    `(None, …)` when the SDK could not give one, said on stderr: every row is then `not_evaluated` with no score — the
    plugin never computes one in its place. A version refusal is not that case: it raises `VersionRefused` and the build stops."""
    run_dir, trial_dir = verdict_events.scores_home(out)
    block, why = verdict_events.refresh_scores(run_dir)
    if block is None:
        print(f'note: no scores block for {run_dir} ({why}) — no row is scored and no agent verdict is given; '
              'install the plurai SDK this plugin requires and build again', file=sys.stderr)
    return block, trial_dir


#: The scores block's conversation `result` → the row's (`not_scored`: neither a pass nor a failure).
SCORED_RESULTS = {'pass': 'pass', 'fail': 'fail', 'not_scored': 'not_evaluated'}


def scored_result(conversation: Mapping[str, object] | None) -> str:
    """The row `result` the SDK's conversation entry states; an attempt the block does not name is `not_evaluated`."""
    return SCORED_RESULTS.get(str((conversation or {}).get('result')), 'not_evaluated')


def stamp_scores(row: dict, conversation: Mapping[str, object] | None) -> None:
    """The row's `score` (0-100, unrounded, null when not scored) and `fail_reasons` (`low_score`, `critical_failure`,
    `failing_tier`, `goal_missed`, `crashed`), COPIED from the SDK's conversation entry; without one the row has no
    score and no `fail_reasons` key (unknown, never `[]`)."""
    if conversation is None:
        return
    row['score'] = conversation.get('score')
    row['fail_reasons'] = list(conversation.get('fail_reasons') or [])


def apply_scores(spec: dict, block: dict | None) -> None:
    """`run.json` takes the run's scores block: verbatim under `scores` (every conversation, scenario, the suite and the
    agent verdict), and in `totals` the suite score, the agent verdict and the failing scenarios, COPIED or counted off
    the block's own results. Nothing when there is no block."""
    if block is None:
        return
    spec['scores'] = block
    suite = block.get('suite') if isinstance(block.get('suite'), dict) else {}
    agent = block.get('agent') if isinstance(block.get('agent'), dict) else {}
    scenarios = block.get('scenarios') if isinstance(block.get('scenarios'), dict) else {}
    spec['totals'].update({
        'suite_score': suite.get('score'),
        'agent_verdict': agent.get('verdict'),
        'scenarios_failed': sum(1 for entry in scenarios.values() if isinstance(entry, dict) and entry.get('result') == 'fail'),
    })


def c7_totals(out: Path, trials: object) -> dict | None:
    """C7 as the SDK produced it: a REFOLDED trials block's `run.totals` (the counts over the verdicts a rescore or a
    correction left, :func:`trials_block`), else `run_summary.json` `totals`; None when neither carries it (an older SDK,
    or a refold that could not recount it)."""
    if isinstance(trials, dict) and trials.get('rescored'):
        run = trials.get('run')
        found = run.get('totals') if isinstance(run, dict) else None
    else:
        doc = _read_json(out / 'run_summary.json')
        found = doc.get('totals') if isinstance(doc, dict) else None
    return found if isinstance(found, dict) and all(k in found for k in C7_TOTALS) else None


def apply_c7(spec: dict, c7: dict | None) -> None:
    """`run.json` `totals` takes C7's run numbers, and each scenario row its trials' validity
    (`trials: [{trial, validity, cause, tool_refusals}]`) and its `tool_refusals`, summed over those trials: one source
    for the number on the row and in the totals. Validity is the SDK's; the passes among the valid trials are counted off
    the per-trial rows the page shows (`trial_rows`, or the single trial's scenario rows), so a breach that leaves the
    row's verdict standing, or a single-trial rescore, counts the same in both places. Without C7 the totals stay as
    counted (an older SDK), said."""
    if c7 is None:
        print('note: run_summary.json carries no C7 totals (valid_trials, invalid_by_cause, ...) — run.json keeps its own counts',
              file=sys.stderr)
        return
    spec['totals'].update({k: c7[k] for k in C7_TOTALS})
    per = c7.get('scenarios') if isinstance(c7.get('scenarios'), dict) else {}
    if per:
        results = spec.get('trial_rows') or {str(r.get('id')): [r] for r in spec.get('scenarios') or [] if isinstance(r, dict)}
        spec['totals']['passed_valid'] = sum(
            1 for sid, entry in per.items() if isinstance(entry, dict)
            for t, row in zip(entry.get('trials') or [], results.get(sid) or [])
            if isinstance(t, dict) and t.get('validity') == 'valid' and isinstance(row, dict) and row.get('result') == 'pass')
    for row in spec.get('scenarios') or []:
        entry = per.get(str(row.get('id'))) if isinstance(row, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get('trials'), list):
            row['trials'] = entry['trials']
            row['tool_refusals'] = sum(t.get('tool_refusals') or 0 for t in entry['trials'] if isinstance(t, dict))


def _spec_of(out: Path, args: argparse.Namespace, scores_block: dict | None = None, trial_dir: str = '') -> dict:
    """ONE trial dir's `run.json`: every count over the scenario dirs directly under `out` (trial 1 for a run dir).

    `scores_block` is the run's scores block (`verdict_events.refresh_scores`; None: the SDK could not give one) and
    `trial_dir` this trial's folder under the run dir (`''`: trial 1): each row's `result`, `score` and `fail_reasons`
    are that attempt's entry in it, copied."""
    dirs = scenario_dirs(out)
    if not dirs:
        raise SystemExit(f'no scenario dirs under {out} — nothing to aggregate')

    # Which policies could have failed a scenario by themselves, off the local eval set — read ONCE. An errored
    # policy spares a verdict only when this says it could not (`_verdict_clean`); `None` spares none.
    # The same read gives each `evals[]` row its effective tier (`eval_tier_fields`).
    policy_bodies = eval_set_bodies(getattr(args, 'workdir', None))
    # The operator's identity markers, captured when the run started (`operator_identity.py capture`). None: not checked.
    identity_markers = _identity.read_markers(out) if _identity else None
    if identity_markers is None:
        print(f'note: no operator identity was captured when this run started ({_identity.FILENAME if _identity else "operator_identity.py not importable"}), '
              'so `operator_identity_exposed` was not checked.', file=sys.stderr)

    per_policy: dict[str, dict[str, int]] = {}
    # Kept BESIDE per_policy rather than inside it: the counter dict is spread straight into
    # the eval row, and a histogram among the ints would land there unconditionally — an old
    # run.json has no reason data at all, and an empty histogram asserts it was checked.
    per_policy_reasons: dict[str, dict[str, int]] = {}
    #: `{policy: {'passed_turns': n, 'decided_turns': n}}` for the policies whose cells declared
    #: `scope: turn`. Kept BESIDE per_policy for the same reason as the histogram above: it is
    #: spread onto the eval row and only a turn-scoped policy has one, so a policy with no entry
    #: here renders exactly the scenario-grain row.
    per_policy_turns: dict[str, dict[str, int]] = {}
    #: `{policy: the evals that graded its deferred cells}` — the eval row names who carried its verdict.
    per_policy_owners: dict[str, set[str]] = {}
    orders: list[list[str]] = []
    skipped_lists: list[list[str]] = []
    scenario_rows: list[dict] = []
    #: `{scenario: user_sim_integrity block}` for the scenarios whose transcript carried one.
    user_sim: dict[str, dict] = {}
    closed_classes: dict[str, list[dict]] = {}
    #: `{scenario: ended_early reason}` for the scenarios whose dialog stopped for something
    #: other than the user reaching its goal. Seeded from the runner's roster so a scenario that
    #: was cut AND lost its artifacts still appears; each transcript then overrides its own row.
    early: dict[str, dict] = roster_ended_early(out)
    totals = {'scenarios': len(dirs), 'passed': 0, 'failed': 0, 'errored': 0,
              'policy_violations': 0, 'tool_calls': 0, 'tool_errors': 0, 'tool_refusals': 0,
              'zero_result_tool_calls': 0, 'tool_calls_recovered': 0}

    totals.update(roster_totals(out, dirs, args.expected_scenarios))

    def bump(name: str, state: str, reason: str | None = None) -> None:
        per_policy.setdefault(name, _zero_counts())[state] += 1
        # WHY the judge hedged, kept per policy: `not_applicable` means the premise never
        # arose, so an eval hedged that way measured nothing and is not the same finding as
        # one that saw a live premise and could not decide. Without the split the report has
        # nothing to branch on and renders both as "never conclusive".
        if reason:
            hist = per_policy_reasons.setdefault(name, {})
            hist[reason] = hist.get(reason, 0) + 1

    #: `{flow family: [scenario names]}` and `{scenario: {tool names it called}}` — the two
    #: halves `outcome_attribution` needs, gathered in the one pass over the dirs.
    family_members: dict[str, list[str]] = {}
    tools_by_scenario: dict[str, set[str]] = {}
    run_by_tool: dict[str, dict[str, int]] = {}
    #: `{tool: rows the union suppressed}` across every scenario. SUMMED, unlike the per-scenario
    #: map's own two producers: two scenarios are two separate dispatch histories, so their
    #: unverified rows are different rows and adding them keeps the run-level number a floor.
    run_floor: dict[str, int] = {}
    #: …and `{tool: rows whose dispatch outcome nothing decided}`, summed the same way.
    run_unclassified: dict[str, int] = {}
    #: …and `{tool: rows the harness refused BY NAME}`, summed the same way (`invented_verbs`).
    run_not_dispatchable: dict[str, int] = {}
    #: …and `{tool: [failed calls, the first one's error]}`, summed the same way (`setup_failed_tools`).
    run_setup: dict[str, list] = {}
    #: `{scenario: (trail rows whose dispatch did not fail, those of them with no result)}` (`tool_trail_results_missing`).
    trail_results: dict[str, tuple[int, int]] = {}
    #: `{scenario: (tool stats, retry trail distance, the selection that produced it)}` — the
    #: TRAIL facts, gathered in the one pass over the dirs and stamped onto the rows after the
    #: loop. Gathered rather than stamped in place because a row leaves this loop by four
    #: different paths and a per-path stamp is four chances to miss one; the row builders
    #: themselves stay ignorant of it.
    trail_by_scenario: dict[str, tuple[dict, dict | None, str | None]] = {}
    #: WHICH scenarios the runner took a second attempt on, and why. Read once (see
    #: `stability_reasons` for why the selection is published beside every repeat distance).
    repeat_selection = stability_reasons(out)
    #: The rows whose AGENT raised, with its error: their goal is stamped after the loop (`agent_raised_goal`) — those
    #: with no transcript (error.json), and those that LANDED on the agent's raise (`turn_failure`).
    raised_rows: list[tuple[dict, str]] = []
    landed_raised: list[tuple[dict, str]] = []

    real_tools = declared_real_tools(getattr(args, 'workdir', None))
    #: The agent's declared error replies: the anchor an error-reply code_eval's charge roots on (`anchor_error_replies`).
    error_replies = declared_error_replies(getattr(args, 'workdir', None))
    #: The backend requests made below the agent's tools (`serves:` seams, and C12 mocks beneath the tool that are none of
    #: the agent's own tools, the scan inventory's live list): counted apart from the agent's calls, never printed.
    backend = declared_backend_requests(getattr(args, 'workdir', None))
    if backend:
        totals['tool_calls_backend'] = 0
    serves = declared_serves(getattr(args, 'workdir', None))
    for d in dirs:
        tools = tool_stats(d, surface_known=declared_surface(getattr(args, 'workdir', None)) is not None, backend=backend, serves=serves)
        if real_tools:
            # The bypass alarm's own counts: the calls to tools a mock could have seen, and how many of them were reconstructed.
            totals['tool_calls_mocked'] = totals.get('tool_calls_mocked', 0) + sum(
                tally['calls'] for tool, tally in tools['by_tool'].items() if tool not in real_tools)
            totals['tool_calls_mocked_recovered'] = totals.get('tool_calls_mocked_recovered', 0) + sum(
                n for tool, n in tools['recovered_by_tool'].items() if tool not in real_tools)
        # The runner's own second sample of THIS scenario, when it took one. A distance, never a
        # threshold: `stability_reasons` states the selection bias that makes it unusable as one.
        retry_signature = retry_trail_signature(d)
        trail_by_scenario[d.name] = (
            tools,
            None if retry_signature is None else trail_distance(tools['signature']['calls'], retry_signature['calls']),
            repeat_selection.get(d.name),
        )
        for tool_name, dropped in tools['floor'].items():
            run_floor[tool_name] = run_floor.get(tool_name, 0) + dropped
        for tool_name, unread in tools['unclassified'].items():
            run_unclassified[tool_name] = run_unclassified.get(tool_name, 0) + unread
        for tool_name, gated in tools['not_dispatchable'].items():
            run_not_dispatchable[tool_name] = run_not_dispatchable.get(tool_name, 0) + gated
        for tool_name, (count, error) in tools['setup'].items():
            run_setup.setdefault(tool_name, [0, error])[0] += count
        if tools['trail_returned']:
            trail_results[d.name] = (tools['trail_returned'], tools['trail_returned_without_result'])
        totals['tool_calls'] += tools['calls']
        totals['tool_errors'] += tools['errors']
        totals['tool_refusals'] += tools['refusals']
        totals['zero_result_tool_calls'] += tools['zero_results']
        totals['tool_calls_recovered'] += tools['recovered']
        if backend:
            totals['tool_calls_backend'] += tools['backend_calls']
        tools_by_scenario[d.name] = set(tools['by_tool'])
        for tool_name, tally in tools['by_tool'].items():
            run_tally = run_by_tool.setdefault(tool_name, {'calls': 0, 'errors': 0, 'unanswered': 0, 'invalid_args': 0})
            for key in run_tally:
                run_tally[key] += tally[key]
        # THE ATTEMPT'S STATUS, decided once by the writer (`verdicts.json` `status`): this file never classifies an
        # attempt again. error.json is opened only for remedy prose. An ungraded attempt (no verdicts at all) shares
        # ONE tail (`errored_row`).
        verdicts = read_verdicts(d)
        state = verdicts_file.status(verdicts)
        cause = state['cause'] or {}
        kind = cause.get('kind') if isinstance(cause.get('kind'), str) and cause.get('kind') else None
        transcript = (_read_json(d / 'transcript.json') or {}) if verdicts_file.graded(verdicts) else None
        if transcript is None:
            if state['value'] == AGENT_ERROR:
                # The AGENT crashed: a failed conversation, scored 0 (never left out of the score).
                totals['failed'] += 1
                print(f'note: {d.name}: the agent raised in its own turn ({kind}) — counted FAILED and scored 0; its error.json names the agent code', file=sys.stderr)
                scenario_rows.append(errored_row(d.name, tools, kind or AGENT_RAISED, ended_early=early.get(d.name), result='fail'))
                stamp_scores(scenario_rows[-1], verdict_events.conversation(scores_block, d.name, trial_dir))
                raised_rows.append((scenario_rows[-1], agent_raise(_read_json(d / 'error.json') or {})))
                continue
            if state['value'] != HARNESS_ERROR:
                print(f'note: {d.name}: {verdicts_file.FILENAME} is ungraded with status {state["value"]!r} — treated as errored', file=sys.stderr)
            if kind == NOT_GRADED:
                kind = None
                print(f'note: {d.name} finalized but nothing graded it; never scored, counted as errored (NOT failed)', file=sys.stderr)
                # It LANDED, so its transcript still says how the dialog went: the simulator's fidelity and why it stopped
                # are facts about the SCENARIO, not the verdicts, and nothing graded it is no reason to drop them.
                landed = _read_json(d / 'transcript.json') or {}
                if (integrity := user_sim_integrity(run_record(landed))) is not None:
                    user_sim[d.name] = integrity
                merge_ended_early(early, d.name, landed)
            totals['errored'] += 1
            if kind and kind.startswith(HARNESS_KIND_PREFIX):
                # NOT VALID: the runner did not score this row — its typed `harness_*` kind says why (a
                # budget, a child that exited, a seed contract), which is not always the harness's own
                # fault. Counted as well as in `errored` (like `harness_timeouts`), never passed or failed.
                totals['not_valid'] = totals.get('not_valid', 0) + 1
            if kind in HARNESS_TIMEOUT_KINDS:
                # Counted SEPARATELY as well as in `errored`, not instead of it: the scenario
                # still scored nothing, so every denominator that excludes an errored scenario
                # must keep excluding this one. The extra total is what lets the report say the
                # run hit a simulation budget rather than the agent crashing.
                totals['harness_timeouts'] = totals.get('harness_timeouts', 0) + 1
                print(f'note: {d.name} hit a SIMULATION budget ({kind}) — not scored, and not a verdict about the agent', file=sys.stderr)
            elif kind == HARNESS_SEED_STATE_CONTRACT:
                # Not a budget: no timeout count, and a remedy that names the file, not a knob.
                print(f"note: {d.name} was refused at init by the harness's seed_state contract ({kind}) — fix "
                      "simulate.py's seed_state(initial_agent_context, initial_world_state, resume_seed=None) and run "
                      'verify_simulate.py; not a verdict about the agent', file=sys.stderr)
            elif kind == HARNESS_FAULT:
                print(f'note: {d.name} was voided by a harness fault ({kind}) — fix the harness code its error.json '
                      'names (the raise, or the world write the mock refused), then re-run; not a verdict about the agent',
                      file=sys.stderr)
            elif kind == HARNESS_MOCK_RUNTIME:
                print(f'note: {d.name} was voided by a mock runtime fault ({kind}) — {mock_runtime_remedy(mock_runtime_fault(d))}',
                      file=sys.stderr)
            elif kind == HARNESS_MOCK_UNREACHABLE:
                print(f'note: {d.name} ({kind}): {mock_unreachable_remedy(mock_unreachable_fault(d))}', file=sys.stderr)
            elif kind == BACKEND_HTTP:
                print(f'note: {d.name}: {backend_http_remedy(backend_http_fault(d))}', file=sys.stderr)
            elif kind == PROVIDER_INFRA:
                print(f'note: {d.name}: {PROVIDER_INFRA_REMEDY}', file=sys.stderr)
            elif kind and kind.startswith(HARNESS_KIND_PREFIX):
                # A harness fault this script does not know how to advise on. It keeps its
                # `error_kind` on the row (a reader can still see what the runner said) but it
                # is NOT a timeout, so it earns neither the count nor the "raise the budget"
                # remediation that goes with one.
                print(f'note: {d.name} declares harness kind {kind!r}, which is not a known timeout — '
                      'reported as an untyped harness fault, not counted as a timeout', file=sys.stderr)
            # No transcript, so the roster is the ONLY place a reason can come from here.
            scenario_rows.append(errored_row(d.name, tools, kind, ended_early=early.get(d.name)))
            if kind == HARNESS_MOCK_RUNTIME:
                scenario_rows[-1]['mock_runtime'] = mock_runtime_fault(d)
            if kind == HARNESS_MOCK_UNREACHABLE:
                scenario_rows[-1]['mock_unreachable'] = mock_unreachable_fault(d)
            if kind == BACKEND_HTTP:
                scenario_rows[-1]['backend_http'] = backend_http_fault(d)
            continue

        # `tool_calls_recovered` rides the ROW as well as the totals: the report annotates
        # per scenario, and a run whose reconstructions cluster in one scenario is a
        # different finding from one where every scenario bypassed the mock equally.
        row: dict = {'id': d.name, 'result': None, 'score': None, 'turns': None,
                     'tool_calls': tools['calls'], 'tool_errors': tools['errors'],
                     'tool_refusals': tools['refusals'],
                     'tool_calls_recovered': tools['recovered'],
                     **({'tool_calls_backend': tools['backend_calls']} if 'backend_calls' in tools else {}),
                     'deterministic': None, 'failing_evals': [], 'not_applicable_evals': [], 'inconclusive_evals': [],
                     'deferred_evals': [], 'skipped_evals': [], 'needs_review': []}
        # `{tool: rows the union could not verify}` — the receipt that says `tool_calls` above is
        # a LOWER BOUND for these tools. It reached stderr and nothing else, so the report the
        # customer reads rendered a floor as a measurement (and `dead_tools`, hence the whole
        # outcome attribution, hangs off the same count). Per scenario, because a floor
        # concentrated in one dialog is a different finding from one spread across the run.
        if tools.get('floor'):
            row['tool_count_floor'] = dict(tools['floor'])
        # …and the rows whose outcome nothing decided at all. Beside the floor because they are
        # the same class of fact: a number in this row that is not the measurement it looks like.
        if tools.get('unclassified'):
            row['unclassified_dispatch_outcomes'] = dict(tools['unclassified'])
        # Same class again, one rung further out: the channel itself was DECLARED unread for this
        # scenario (a literal `null` `tool_events.json`), so `tool_calls: 0` above is an absence
        # of observation and not an observation of absence.
        if tools.get('events_unread'):
            row['tool_events_unread'] = True
        # The attempts a gate refused, named so a finding can cite them; absent when there were none.
        if tools.get('refused_attempts'):
            row['refused_attempts'] = list(tools['refused_attempts'])

        # WHY this dialog stopped, when it stopped for something other than the user reaching
        # its goal. Read here — beside `turns`, before any verdict is looked at — because it is
        # the fact that decides how every number below this line reads: a scenario the simulated
        # user abandoned, or one the turn budget cut, scores low for every obligation its
        # conversation never reached, and that is not the agent's failure. The transcript holds
        # BOTH producers' records and is authoritative for its own row; the roster covers rows
        # whose artifacts are missing.
        merge_ended_early(early, d.name, transcript)
        if d.name in early:
            row['ended_early'] = early[d.name]
        # The SDK's typed stamp: the dialog LANDED on a failed turn (`{turn_idx, closing, kind, exception,
        # child_exc_class, error}`) — mid-dialog, `ended_early` names its owner; on the closing call it was
        # scored WITHOUT its closing reply.
        meta = transcript.get('metadata')
        if isinstance(meta, dict) and isinstance(failure := meta.get('turn_failure'), dict):
            row['turn_failure'] = failure

        if transcript.get('turn_count') is not None:
            row['turns'] = transcript.get('turn_count')
        else:
            row.pop('turns')

        # The attempt's verdicts (docs/verdicts.md), beside the transcript's run facts.
        per = verdict_view(verdicts, transcript)
        entries = verdicts_file.evals(verdicts)
        # Read BEFORE the never-scored bail-out below: a scenario nobody graded can still have a
        # user simulator that broke its schedule, and that is a fact about the SCENARIO, not
        # about the verdicts. Recorded on every scenario that carries the block.
        integrity = user_sim_integrity(per)
        if integrity is not None:
            user_sim[d.name] = integrity
        # Read on the same pass and under the same rule: a scenario nobody graded still carries
        # the registry's decision about which of its closed classes anything checks.
        if detectors := closed_class_detectors(per):
            closed_classes[d.name] = detectors
        # The scenario's own declared flow families, straight off the transcript the backend
        # wrote. Same source the runner's `family_gradient` folds; read here as well because the
        # gradient publishes counts without members, and the attribution below needs to know
        # WHICH scenarios a zeroed family holds.
        for flow in ((per.get('scenario_dimensions') or {}).get('target_flows') or []):
            family_members.setdefault(str(flow), []).append(d.name)
        # Policies the guard turned off for THIS scenario (`not_applicable` / `guard_skipped`): out of the cells,
        # named in `skipped_evals`. Every other eval rides `er`, keyed by name, the cell the judge wrote.
        guard_skipped: set[str] = {name for name, entry in entries.items() if eval_axis(entry) == 'skipped'}
        er = {name: entry['cell'] for name, entry in entries.items()
              if name not in guard_skipped and isinstance(entry.get('cell'), dict)}
        # The plugin's own deterministic check, graded here over the agent's replies and the files it wrote: a cell like
        # any other, so it reaches the eval rows, `failing_evals`, `policy_violations` and the score below.
        identity = identity_cell_of(d, transcript, identity_markers)
        if identity:
            er[identity[0]] = identity[2]
        # The cell verdicts are STAGED, not committed: a scenario that turns out never to
        # have been scored at all (below) contributes NOTHING to the eval rows or the
        # violation count, and a bump already applied cannot be taken back.
        staged: list[tuple[str, str, str | None]] = []
        #: `(policy, passed turns, decided turns)` — staged with the verdicts above and committed
        #: with them, so a scenario that turns out never to have been scored contributes nothing
        #: at either grain.
        staged_turns: list[tuple[str, int, int]] = []
        violations = 0
        cell_states = {'passed': 0, 'failed': 0}
        # The SIX-state split — never collapsed into passed-vs-rest, and never into one undecided
        # bucket either. passed and failed are the CONCLUSIVE verdicts, the only cells that say
        # anything about the agent; a judge that CRASHED is a broken eval to repair; and the ways
        # of deciding nothing are separate findings with separate repairs. The file states each
        # eval's state; `eval_axis` is a lookup on it (the plugin's own check, which has no file
        # entry, is read by `cell_axis`), and both RAISE on a state the contract does not name.
        try:
            axes = {name: eval_axis(entry) for name, entry in entries.items() if name not in guard_skipped}
            if identity:
                axes[identity[0]] = cell_axis(identity[2])
        except ValueError as exc:
            raise SystemExit(f'{d.name}: {exc}') from exc
        for name, axis in axes.items():
            cell = er.get(name) or {}
            status = cell.get('status')
            if axis == 'passed':
                staged.append((name, 'passed', None))
                cell_states['passed'] += 1
            elif axis == 'failed':
                staged.append((name, 'failed', None))
                cell_states['failed'] += 1
                # policy_violations = conclusively violated cells ONLY (`violated` AND
                # status == 'success') — inconclusive/errored cells are non-passes but NOT
                # violations, and conflating them inflates reported agent misbehaviour.
                violations += 1
                row['failing_evals'].append(failing_annotation(name, cell))
            elif axis in ('not_applicable', 'not_measured'):
                staged.append((name, axis, inconclusive_reason(cell)))
                # ITS OWN NAME LIST, beside the hedges rather than inside them. No histogram can
                # reach this grain: `compare_runs.cells_of` derives its per-policy verdicts from
                # the row's name lists — "run.json carries no per-scenario cell map, the lists ARE
                # the record" — so until this list exists every run-to-run delta calls a
                # premise-gated cell a judge that could not decide.
                annotated = inconclusive_annotation(name, cell)
                row['not_applicable_evals'].append(annotated)
                # The review lens reads `violated` on this axis too: the backend's `na_quorum` stamps
                # `mixed_not_applicable` on a cell whose surviving majority said violated.
                if cell.get('violated'):
                    row['needs_review'].append(annotated)
            elif axis == 'inconclusive':
                staged.append((name, 'inconclusive', inconclusive_reason(cell)))
                annotated = inconclusive_annotation(name, cell)
                row['inconclusive_evals'].append(annotated)
                # needs_review = the cells that reached NO verdict but whose majority LEANED
                # VIOLATED — the server's own `violated` flag, never a lean re-derived from
                # reasoning text. A LENS on inconclusive_evals / not_applicable_evals (the name
                # appears in both its list and this one), never a further state; nothing
                # downstream may count it as a violation.
                if cell.get('violated'):
                    row['needs_review'].append(annotated)
            elif axis == 'deferred':
                # Graded under its owner (the backend C9): the owner's verdict carries the charge, so this is neither a
                # verdict nor a hedge — its own list and counter, never `inconclusive_evals` or `needs_review`.
                staged.append((name, 'deferred', None))
                row['deferred_evals'].append(deferred_annotation(name, cell))
            elif axis == 'errored':
                staged.append((name, 'errored', None))
            # An axis with no branch above stages nothing, and `_reconcile_cells` names the cell.
            # …and, for a policy the eval set declared at the TURN grain, the same cell counted at
            # THAT grain. Keyed on the cell's declared `scope` — never on a name, a count or a
            # threshold — and only off a cell that reached a conclusive scenario verdict, so the
            # turn pair can never appear on a row whose scenario column decided nothing.
            if status == 'success' and cell.get('scope') == 'turn':
                staged_turns.append((name, *turn_grain_counts(cell)))

        # THE RECONCILIATION: every eval the file holds landed in exactly one counter.
        _reconcile_cells(d.name, staged, axes)

        # A policy gated off for this scenario carries no verdict, and appears only here — a
        # parser that walks the verdicts alone reports 100% pass and never mentions it.
        skipped = [n for n in entries if n in guard_skipped]
        for name in skipped:
            staged.append((name, 'skipped', None))
        row['skipped_evals'] = [_table_safe(n) for n in skipped]

        # THE AGENT'S OWN SENTENCES, with every eval that charged each one. Built
        # here rather than in the renderer because `run.json` is what the suite critic and
        # /simulation-fixes read: the collapse has to be in the typed row, or each of them
        # re-derives it (or does not) from three annotated names that share no visible root.
        # Staged with the rest of the row — a scenario that turns out never to have been graded
        # is replaced by `errored_row` below and carries none of this.
        if roots := rooted_findings(anchor_error_replies(er, policy_bodies, error_replies, transcript.get('turns')), turns=transcript.get('turns')):
            row['rooted_findings'] = roots

        # TURNS NOTHING WAS MEASURED ON, and whose they are. The backend files one
        # `not_measured` cell per turn_based policy for a turn the runner emptied rather than
        # invoking judges over an absent utterance — the acceptance line is "3 turns unmeasured
        # (harness)" instead of three reds, and the report counts them from these. Present only when there ARE some: an empty list on every row would assert this
        # was measured on runs whose backend files no such cell.
        unmeasured, unmeasured_cause = unmeasured_turns(er)
        if unmeasured:
            row['unmeasured_turns'] = unmeasured
            if unmeasured_cause:
                row['unmeasured_turns_cause'] = unmeasured_cause
                # …and the OWNER, resolved here so every consumer of run.json reads the same
                # attribution rather than each re-deriving one from the cause token.
                if owner := _NOT_MEASURED_OWNER.get(unmeasured_cause):
                    row['unmeasured_turns_owner'] = owner

        # The row's result is the attempt's STATUS, then — for a scored one — the SDK's own conversation result
        # (`plurai.scoring`, its scores block): `result`, `score` and `fail_reasons` are COPIED, never computed here.
        # `conclusive` only says whether any eval reached a conclusion (the eval rows below count it); it scores nothing.
        conclusive = cell_states['passed'] + cell_states['failed']
        scored = verdict_events.conversation(scores_block, d.name, trial_dir)
        if scores_block is not None and scored is None:
            print(f'note: {d.name}: the scores block names no attempt for it in this trial — no score, counted not evaluated', file=sys.stderr)
        # the backend's own GOAL verdict for this scenario, read once for the run's "N of M goals achieved" headline. It
        # rides the row so every consumer of run.json reads the same answer this file did.
        goal = scenario_verdict(per, policy_bodies)
        row['goal'] = goal
        # A premise the dialog never reached because it ended early says so, with the reason the Ended early column shows.
        # Not when the AGENT raised: that goal is a crash, stamped after the loop (`landed_raised`), never "not measured".
        # A single-hop replay (`replay_cut`) is the other exception: its one turn IS the situation under test, and its
        # goal is not graded by design, so it says that instead of "before the situation came up".
        if (cut := early.get(d.name) or {}).get('reason') == REPLAY_CUT_REASON and goal.get('goal_status') != 'passed':
            goal['reason'] = REPLAY_CUT_GOAL_REASON
        elif state['value'] != AGENT_ERROR and cut.get('reason') \
                and str(goal.get('reason') or '').startswith(TARGET_PREMISE_UNSHOWN_REASON):
            used, cap = cut.get('user_turns'), cut.get('max_turns')
            at = f' at user-turn {used} of {cap}' if isinstance(used, int) and isinstance(cap, int) else ''
            goal['reason'] = goal['reason'].replace(TARGET_PREMISE_UNSHOWN_REASON, f"the conversation ended early{at} ({cut['reason']}), before the "
                                                    "situation this scenario tests came up, so its goal wasn't measured", 1)
        # The confident high-severity reds beside the goal, as the backend SERVED them. `None` when this
        # row has a verdict and its backend served no list — UNKNOWN, never a zero; absent with no verdict.
        if goal.get('status') != 'unknown':
            row['high_severity_failed'] = goal.get('high_severity_violations')
            # What the server failed the row on (the terminate floors included) — the one derivation every surface reads.
            row['critical_failed'] = goal.get('decisive_violations') or goal.get('catastrophic_violations') or []

        if not conclusive and state['value'] != AGENT_ERROR:
            # NO EVAL REACHED A CONCLUSION, so nothing scored: the SDK's block says `not_scored` too. The backend may
            # still have decided the GOAL — a deterministic goal check answers whether or not a judge survived — and
            # that rides the row's `goal` and the goals headline; the row itself is `not_evaluated`, never an
            # `error` (that would send the reader hunting an error.json that does not exist). A `harness_error` attempt
            # is the exception: the SDK left it out to be re-run, so it errors below whatever its goal says.
            if goal['status'] != 'unknown' and state['value'] != HARNESS_ERROR:
                result = scored_result(scored)
                _count_goal_result(totals, result)
                print(f'note: {d.name} reached NO eval verdict ({goal["reason"]}) — counted as {result}; it has no score, '
                      'because no eval concluded.', file=sys.stderr)
                # `error_kind` rides ONLY on a row that reached no verdict, so a guard-gated row keeps its kind.
                scenario_rows.append(errored_row(d.name, tools,
                                                 NO_APPLICABLE_POLICIES if guard_skipped and result == 'not_evaluated' else None,
                                                 skipped=skipped or None, ended_early=early.get(d.name),
                                                 result=result, goal=goal))
                stamp_scores(scenario_rows[-1], scored)
                continue
            # NOTHING concluded and no goal block was read. The scenario finalized but was never GRADED — so it
            # ERRORED: reading it as 0 would publish the agent failing work nobody judged.
            #
            # TWO WAYS TO GET HERE, and they need opposite investigations. Guards that gated every
            # policy off for this scenario mean nothing crashed — the guards are precisely why
            # nothing scored; otherwise nothing graded a scenario the backend reached.
            gated = bool(guard_skipped)
            if gated:
                print(f'note: {d.name} reached no verdict because every policy in the set is GATED OFF for it '
                      f'({", ".join(sorted(guard_skipped))}) — its {verdicts_file.FILENAME} says so. Nothing crashed: '
                      'counted as errored (NOT failed) because nothing scored, and error_kind says which.',
                      file=sys.stderr)
            else:
                print(f'note: {d.name} finalized but its {verdicts_file.FILENAME} carries no conclusive verdict; never scored, '
                      'counted as errored (NOT failed)', file=sys.stderr)
            totals['errored'] += 1
            # `skipped` carries through: on this path it is the ONLY record that those policies
            # are gated off, and the eval rows below never see this scenario.
            scenario_rows.append(errored_row(d.name, tools,
                                             NO_APPLICABLE_POLICIES if gated else kind,
                                             skipped=skipped if gated else None,
                                             # This row HAS a transcript, so its reason was
                                             # already resolved above; the rebuilt row must not
                                             # lose it just because nothing graded the scenario.
                                             ended_early=early.get(d.name),
                                             goal=goal))
            continue

        orders.append(list(axes))
        skipped_lists.append(list(skipped))
        for name, axis, reason in staged:
            bump(name, axis, reason)
            if axis == 'deferred':
                per_policy_owners.setdefault(name, set()).update(str(o) for o in (er.get(name) or {}).get('deferred_to') or [] if o)
        for name, turns_passed, turns_decided in staged_turns:
            grain = per_policy_turns.setdefault(name, {'passed_turns': 0, 'decided_turns': 0})
            grain['passed_turns'] += turns_passed
            grain['decided_turns'] += turns_decided
        totals['policy_violations'] += violations
        # THE STATUS FIRST, as the writer decided it (`verdicts.json` `status`), read as it is. An AGENT crash is a
        # failed conversation, scored 0. A harness or infrastructure failure — a measurement breach that VOIDS the
        # scenario among them — is errored, re-run, out of the score. A dialog the HARNESS cut short (`incomplete`)
        # LANDED: it scored, over a conversation that was censored, so it is neither a pass nor a fail and out of
        # `passed`/`failed`. Every other status is SCORED, and its result is the SDK's.
        #
        # SCENARIO-level only, and deliberately: its individual verdicts above still count at the
        # EVAL level. A verdict on the turns the dialog DID reach is a real measurement — "the
        # agent fabricated a spec on turn 2" is true whether or not turn 9 ever happened.
        if state['value'] == AGENT_ERROR:
            row['result'] = scored_result(scored) if scored is not None else 'fail'
            _count_goal_result(totals, row['result'])
            landed_raised.append((row, landed_raise(row.get('turn_failure'))))
            print(f'note: {d.name}: the agent raised in its own turn ({kind or "agent_error"}) — counted FAILED and scored 0',
                  file=sys.stderr)
        elif state['value'] == HARNESS_ERROR:
            row['result'] = 'error'
            if kind:
                row['error_kind'] = kind
            totals['errored'] += 1
            print(f'note: {d.name}: a harness or infrastructure failure ({kind or "untyped"}) — errored, out of the '
                  'score; re-run it', file=sys.stderr)
        elif state['value'] == INCOMPLETE:
            row['result'] = 'incomplete'
            # Conditional like `harness_timeouts` — absent, never a zero. A zero would assert
            # "we checked and none were cut", which a run against a runner that predates the
            # landed budget (it cancelled instead) has not earned.
            totals['incomplete'] = totals.get('incomplete', 0) + 1
            cut_by = (early.get(d.name) or {}).get('reason') or 'the time budget'
            print(f'note: {d.name} was cut by the harness ({cut_by}) and landed INCOMPLETE — graded to the '
                  'turn it reached, so it is neither passed nor failed and out of the score', file=sys.stderr)
        else:
            # THE ROW'S VERDICT, copied from the SDK's scores block: a low score, a critical or failing-tier failure,
            # a missed goal or a crash fails it (`fail_reasons` names which). `not_scored` (nothing weighed) and an
            # attempt the block does not name read `not_evaluated` — neither a pass nor a failure.
            row['result'] = scored_result(scored)
            _count_goal_result(totals, row['result'])
            if row['result'] == 'not_evaluated':
                print(f'note: {d.name} has no score in the run\'s scores block — counted as not_evaluated: '
                      'neither a pass nor a failure, and out of every rate in this report.', file=sys.stderr)
        # The score and fail reasons, as the block states them (a harness loss and a cut dialog are `not_scored`).
        stamp_scores(row, scored)

        # The DETERMINISTIC channel — its own namespace, never folded into
        # evaluation_results: it moves no pass_rate and no eval row, so it gets its own
        # field. status 'error' means the CHECK is broken, never that the agent failed it.
        ssr = per.get('scenario_success_results')
        det = (ssr.get('checks') or {}).get('deterministic') if isinstance(ssr, dict) else None
        if isinstance(det, dict) and det.get('status'):
            row['deterministic'] = {'status': det.get('status'), 'label': det.get('label'), 'detail': det.get('detail')}
        if identity:
            # The KINDS the agent exposed (empty when clean), never a value: the finding's typed form for every reader.
            # `retry_exposed`: the second attempt's, when the runner took one.
            row[identity[1]] = identity[3]
        scenario_rows.append(row)

    # An agent crash's GOAL, stamped once every verdict is read — the SDK's trials block's rule (`plurai.trials`): one
    # eval set per run, so its goal owner is read off the rows the backend verdicted that did NOT crash; the goal is
    # FAILED, N/A only when they all say the eval set declared none. A landed crash keeps the backend's lists and a goal
    # the backend already failed (its reason is the more specific one); any other goal of it is the crash's.
    crashed_ids = {id(row) for row, _ in (*raised_rows, *landed_raised)}
    verdicted_sources = {r['goal'].get('goal_source') for r in scenario_rows if r.get('goal') and id(r) not in crashed_ids}
    goal_owned = verdicted_sources != {GOAL_SOURCE_NONE}
    for row, error in raised_rows:
        row['goal'] = agent_raised_goal(error, owned=goal_owned)
    for row, error in landed_raised:
        if (row.get('goal') or {}).get('goal_status') != 'failed':
            crash_goal = agent_raised_goal(error, owned=goal_owned)
            row['goal'] = {**(row.get('goal') or {}), **{key: crash_goal[key] for key in _GOAL_LADDER_KEYS}}

    # THE TOOL TRAIL, stamped after the loop over EVERY row it built — for the same reason the
    # world-state mark below is: an errored row leaves the loop by a different path, and its trail
    # is the first thing a reader reaches for when asking whether the environment was the problem.
    # Unconditional: a row with no calls carries `trail_signature: []`, which is a measurement
    # ("nothing dispatched") and is read as `no_trail` by the comparison — the absence of the key
    # would instead mean "built by a plugin that never looked".
    for row in scenario_rows:
        facts = trail_by_scenario.get(str(row.get('id')))
        if facts is not None:
            stamp_trail_signature(row, *facts)
            if identity_markers:
                # The agent's own arguments, verbatim but for the operator's identity: each marker value becomes its kind.
                row['trail_signature_args'] = [_identity.redact(a, identity_markers) for a in row['trail_signature_args']]
        if identity_markers and row.get('refused_attempts'):
            # The refused attempts' arguments and reasons are the agent's own too: the same redaction.
            row['refused_attempts'] = json.loads(_identity.redact(json.dumps(row['refused_attempts'], ensure_ascii=False), identity_markers))

    # WHOSE WORLD WAS NOT CERTIFIED. Stamped after the loop, over EVERY row it built — an errored
    # row takes a different path out of it, and a distrusted world is exactly as true of one of
    # those. Only the rows the map NAMES are stamped: with the map present an unnamed row is
    # certified, and writing that on every row would put a measurement on rows whose runner never
    # made one. `False` only, never `True`, for the same reason every other finding here is
    # absent unless earned.
    if distrusted_rows := untrustworthy_scenarios(out):
        for row in scenario_rows:
            reason = distrusted_rows.get(str(row.get('id')))
            if reason is None:
                continue
            row['world_state_trustworthy'] = False
            if reason:
                row['world_state_untrustworthy_reason'] = reason
        named = ', '.join(f'{name}{f" ({why})" if why else ""}' for name, why in sorted(distrusted_rows.items()))
        print(f'WARNING: the backend did NOT certify the world state on {len(distrusted_rows)} scenario(s) '
              f'({named}) — and SCORED them anyway, so those verdicts stand over a world nothing corroborated. '
              'They are graded rows, not placeholders, and no rate in this report is dented by them: read them '
              'against the world snapshot before acting on one.', file=sys.stderr)

    # THE HEADLINE DENOMINATOR: "N of M goals achieved", where M counts the scenarios something
    # actually owned a goal for. Without that definition a customer whose eval set merely
    # PREDATES the goal owner reads `0 of 7` on every report — the suite looks catastrophic
    # because nothing was asked of it. A scenario with no owner is not a missed goal; it is a
    # gap in the eval set, and it belongs beside the rate under `not_evaluated`, never inside it.
    #
    # `incomplete` rows are out of both, the same way they are out of every other rate here:
    # they scored over a conversation the clock censored.
    #
    # Both keys are ALWAYS written (a 0 here is a real "we looked and none were measurable"),
    # which is also what lets the report tell a run.json built by this version from an older one
    # that carries no goal data at all.
    #
    # THE AXIS BOTH NUMERATORS ARE READ OFF is `goal['goal_status']` — the GOAL answer — and not
    # `r['result']`, which is the row verdict and therefore the JOIN of the goal with the policy
    # veto. Counting the headline off the join would let one catastrophic red zero a fraction the
    # report presents as independent, so a single judge scoped to the wrong evidence would report
    # "0 of 3 goals achieved" over three rows that each achieved their goal. The ROW
    # keeps saying `fail` — that is the policy axis doing its job, and `passed`/`failed`/
    # `policy_violations` below are untouched by any of this.
    #
    # A SINGLE-HOP REPLAY row (`replay_cut`) is out of the fraction too: it replays one recorded turn, and its goal is not
    # graded by design — inside M it read as an owned goal that reached no verdict. Counted beside it instead
    # (`single_hop_goal_rows`), so the report can still account for every row.
    hop_rows = {name for name, block in early.items() if (block or {}).get('reason') == REPLAY_CUT_REASON}
    in_scope = [r for r in scenario_rows if r.get('result') in ('pass', 'fail', 'not_evaluated') and goal_is_measurable(r.get('goal') or {})]
    owned = [r for r in in_scope if r.get('id') not in hop_rows]
    if hop_goal_rows := len(in_scope) - len(owned):
        totals['single_hop_goal_rows'] = hop_goal_rows
    totals['goals_measurable'] = len(owned)
    # ONE per-row goal result, read once and counted twice: two separate comprehensions over the
    # same table are two places for the fraction to stop reconciling with its own shortfall line.
    goal_results = [_GOAL_RESULT.get((r.get('goal') or {}).get('goal_status'), 'not_evaluated') for r in owned]
    totals['goals_achieved'] = sum(1 for result in goal_results if result == 'pass')
    # …and, inside M, the rows that reached NO verdict at all: the goal was owned, and the
    # measurement of it did not land (the judge died, or its owner abstained). They can never be
    # in N, so without this count the fraction reads as a failure rate and a broken judge paints
    # the headline red — the manufactured red on the far side of the green this change removes.
    # The report renders the fraction NEUTRAL whenever this is non-zero, and says the number.
    totals['goals_unverdicted'] = sum(1 for result in goal_results if result == 'not_evaluated')
    # Counted SEPARATELY from goal passes: a met goal can still carry a high-severity failure.
    # Over the rows whose backend SERVED the list only; an unserved row is counted apart, never as a zero. Both over the
    # goal fraction's scored rows: an `incomplete` row is out of every rate here.
    scored = [r for r in scenario_rows if r.get('result') in ('pass', 'fail', 'not_evaluated')]
    served_high = [r['high_severity_failed'] for r in scored if isinstance(r.get('high_severity_failed'), list)]
    if served_high:
        totals['scenarios_with_high_severity_failures'] = sum(1 for high in served_high if high)
        totals['high_severity_reported'] = len(served_high)
    if unserved_high := sum(1 for r in scored if 'high_severity_failed' in r and r['high_severity_failed'] is None):
        totals['high_severity_unreported'] = unserved_high
    # …and the rows the server failed on a critical check, which neither the goal nor the high count carries.
    served_critical = [r['critical_failed'] for r in scored if isinstance(r.get('critical_failed'), list)]
    if served_critical:
        totals['scenarios_with_critical_failures'] = sum(1 for critical in served_critical if critical)
        totals['critical_reported'] = len(served_critical)

    evals = eval_rows(merged_policy_order(orders, skipped_lists), per_policy, per_policy_reasons,
                      per_policy_turns, per_policy_owners)
    # Each row's EFFECTIVE tier — what a confident red on it does to the trial — off the local eval set. Every row
    # carries the three keys; an eval set that was not read types every row unknown, said once here.
    for row in evals:
        row.update(PLUGIN_CHECKS[row['id']][1] if row['id'] in PLUGIN_CHECKS else eval_tier_fields(row['id'], policy_bodies))
    if policy_bodies is None and evals:
        print(f'note: the eval set was not read ({"no --workdir" if not getattr(args, "workdir", None) else POLICIES_CONFIG_FILENAME + " unread"}), '
              f'so every evals[] row carries verdict_tier null / tier_source unknown — no tier is guessed.', file=sys.stderr)

    spec: dict = {'schema': RUN_SCHEMA, 'run_name': args.run_name or out.name}
    # The run's STAGE, never inferred from a folder name: `--stage`, else the stage this run dir's previous
    # run.json carried (a rebuild — a rescore, a re-render — keeps it). See `resolve_run_stage`.
    if (stage := resolve_run_stage(out, getattr(args, 'stage', None))) is not None:
        spec['stage'] = stage
    if args.local_only:
        spec['local_only'] = True
    # The MOCK POSTURE this run used, so the report can read its own tool evidence correctly.
    # ORTHOGONAL to `local_only`, which is about the observability platform: a `connect` run
    # can be local_only, and a `local` run can be recorded on a platform. Stamped only when
    # KNOWN — an absent or unrecognised marker leaves the key out, because a guessed `connect`
    # would make a no-mock run render a "bypassed the mock" alarm.
    run_mode = _resolve_run_mode(args)
    if run_mode is not None:
        spec['run_mode'] = run_mode
    # WHO answered the gates — present when nobody did (headless), or when a gate put to a person
    # came back unanswered (interactive). Absent is the claim "there was a person here".
    # See `_run_driver_block`.
    run_driver = _run_driver_block(args)
    if run_driver is not None:
        spec['run_driver'] = run_driver
    for key, value in (('dataset_name', args.dataset_name), ('eval_set_id', args.eval_set_id),
                       ('gt_revision', args.gt_revision), ('agent_version', args.agent_version)):
        if value is not None:   # gt_revision 0 is a real revision, not "absent"
            spec[key] = value
    # A `-dirty` version is provenance for US (`agent_source`), never a caveat on the customer's report: the version the
    # report names is the commit, and whether the agent's own source differs from it is recorded here, truthfully.
    if (source := agent_source(args.agent_version, Path(args.workdir) if getattr(args, 'workdir', None) else None)) is not None:
        spec['agent_version'] = source['commit']
        spec['agent_source'] = source
    # `missing_scenarios`, `incomplete`, `not_evaluated` and `harness_timeouts`
    # are conditional — absent, not zeroed (above). A zero would assert "we checked and there
    # were none", which is a claim a run against an older runner (no typed `kind` at all) has not
    # earned. The three `goals_*` keys are the OPPOSITE: always written, because a 0 there is a
    # real "we looked and nothing owned a goal" — and their presence is also what lets the report
    # tell a run.json built by this version from an older one carrying no goal axis at all.
    # The rows that were single-hop replays of a recorded regression (`ended_early: replay_cut`): one turn each, so the
    # headline counts them as replays of a recorded turn, never as conversations. Conditional, like `incomplete`.
    if hop_rows:
        totals['single_hop_replays'] = len(hop_rows)
        # The scenarios this scored run itself played single-hop: what the report's scored-run sentence counts by,
        # never a replay row's own mode (a side replay of a scenario the run played whole).
        spec['single_hop_scenarios'] = sorted({str(name).rsplit('/', 1)[-1] for name in hop_rows})
    ordered_totals = {k: totals[k] for k in ('scenarios', 'single_hop_replays', 'goals_achieved', 'goals_measurable', 'goals_unverdicted',
                                             'single_hop_goal_rows',
                                             'scenarios_with_critical_failures', 'critical_reported',
                                             'scenarios_with_high_severity_failures', 'high_severity_reported',
                                             'high_severity_unreported', 'passed', 'failed',
                                             'not_evaluated', 'incomplete', 'errored', 'not_valid', 'missing_scenarios',
                                             'harness_timeouts', 'unscored_run', 'graded_scenarios', 'scenario_total',
                                             'policy_violations', 'tool_calls',
                                             'tool_errors', 'tool_refusals', 'zero_result_tool_calls',
                                             'tool_calls_recovered', 'tool_calls_mocked', 'tool_calls_mocked_recovered',
                                             'tool_calls_backend')
                      if k in totals}
    spec.update({'totals': ordered_totals, 'evals': evals, 'scenarios': scenario_rows})
    # A FINAL run's N trials: `$OUT` is trial 1, so every total above is trial 1's; `build_spec` pools them over the trials.
    if (trials := trials_block(out)) is not None:
        spec['trials'] = trials
    # REGRESSIONS, beside the trials and for the same reason: the SDK owns every count, this copies them.
    # The rows join each replayed regression to its folder, its owner and its record-time rate. The per-check
    # rates and the issue types behind them are RUN DRIVER-ONLY and never enter run.json, which is indexed and
    # attached to the backend: `regressions_detail` writes them to their own local file.
    replays_dirs = [Path(d) for d in getattr(args, 'regression_replays', None) or []]
    if (replays := regression_replays_block(out, replays_dirs or None)) is not None:
        spec['regression_replays'] = replays
        spec['regressions'] = regression_rows(replays, Path(args.workdir) if getattr(args, 'workdir', None) else None, out)
    # USER-SIM FIDELITY — its own top-level block, never folded into `totals`. It is not a
    # count of anything the agent did: it says whether the transcripts faithfully enact the
    # scenarios, which is the precondition every other number in this file depends on. Omitted
    # whole when NO transcript carried the block (an older backend), so "we looked and it was
    # clean" cannot be confused with "nobody looked".
    # ENDED EARLY — its own top-level block, beside `user_sim_integrity` and for the same
    # reason: it is not a count of anything the agent did. It says which scenarios' dialogs
    # stopped for something other than the user reaching its goal, which is the difference
    # between "the agent scored badly" and "the simulated user gave up" / "the budget ran out".
    # Omitted whole when nothing ended early — there is nothing to say.
    cut = ended_early_summary(early)
    if cut is not None:
        spec['ended_early'] = cut
    # The warning is about obligations a dialog never reached: a row whose goal WAS achieved has none.
    # Nor does a single-hop replay (`replay_cut`): it stops after the turn under test by design, and every obligation it
    # never reached is censored, not scored as unmet (the report's ended-early part says so to the customer).
    achieved = {r['id'] for r in scenario_rows if (r.get('goal') or {}).get('goal_status') == 'passed'}
    short = ended_early_summary({name: block for name, block in early.items() if name not in achieved and name not in hop_rows})
    if short is not None:
        named = ', '.join(f'{r}x{n}' for r, n in short['reasons'].items())
        print(f'WARNING: {len(short["scenarios"])} scenario(s) ended for something other than the user getting '
              f'what they came for ({named}) — those dialogs stopped short, so every obligation they never '
              'reached scores as unmet. Read their rows as the reason says, not as the agent failing.',
              file=sys.stderr)
    debt = _coverage_debt(out, args)
    if debt is not None:
        spec['coverage_debt'] = debt['coverage_debt']
        if isinstance(debt.get('coverage_debt_blocking'), bool):
            spec['coverage_debt_blocking'] = debt['coverage_debt_blocking']
    dead_names, unproven_names, unproven_floored = dead_tools(run_by_tool, run_floor)
    dead, unproven = set(dead_names), set(unproven_names)
    # The FULL result map, not the set of winners. `incomplete` and `error` are row states that
    # reached no whole-dialog verdict, and an attribution computed over them charges the agent for
    # conversations nobody finished (see `outcome_attribution`).
    declared, declared_why = declared_tool_reading(getattr(args, 'workdir', None))
    if declared is not None:
        # The declaration rides run.json as the typed fact it is, so a reader can see WHY a family
        # says `no_tools` without re-opening the config.
        spec['declared_tools'] = declared
    else:
        # And so does its ABSENCE. Without this the renderer cannot tell "the agent has a tool
        # surface that produced nothing" from "nobody read the declaration", and would print the
        # first over the second — a demand for `agent_state.tool_trail` on an agent with no tools.
        spec['declared_tools_unknown'] = declared_why
    if (world_owner := declared_world_owner(getattr(args, 'workdir', None))) is not None:
        spec['world_owner'] = world_owner
    # Emitted whatever the attribution says. A tool that fired repeatedly and never once succeeded
    # is a fact about the RUN, and the report's give-up advice reads it to point at the
    # environment — so gating it on "some family scored zero" would lose that pointer on exactly the
    # run where a dead tool coexists with one passing scenario per family.
    # A name in neither `tools:` nor `render_actions:` was never a capability this environment
    # could have blocked: the engine gates verbs BECAUSE THE FLOW DOES NOT EXPOSE THEM, so
    # nothing was blocked and the agent invented them. Subtracted from `dead` BEFORE the
    # attribution, so the families read `agent`.
    reading = read_surface(getattr(args, 'workdir', None))
    surface = None if reading.verbs is None else set(reading.verbs)
    if surface is None and (dead or unproven):
        # THE DEGRADATION, IN THE ARTIFACT. Without a readable declaration nothing may be called
        # invented, so every one of these names stays in `dead_tools` and its family reads
        # `environment` — the exact wrong RED this key exists to prevent. Stderr alone is not the
        # customer's artifact: without this key the report would render a confident environment
        # finding with no trace that the surface was never read. SKILL.md documents this key
        # beside `invented_verbs`.
        spec['verb_surface_unknown'] = sorted(dead | unproven)
        # …and WHICH of the six no-surface states this is, because they do not share a remedy and
        # the page would print the `--workdir` one for all of them (see SURFACE_* above).
        spec['verb_surface_unknown_reason'] = reading.why
    if surface is not None:
        absent = (dead | unproven) - surface
        # INVENTED ONLY ON THE HARNESS'S OWN WORD. A name outside the surface proves nothing by
        # itself: a config whose `tools:` keys are the mocked SEAM (client methods) never lists the
        # model-facing verbs the agent builds at run time, so a real `run_<analysis>` that the
        # world answered with a denial would read as invented and the report would tell the customer to repair
        # the agent. Only a name EVERY row of which the harness refused by name (`not_dispatchable`)
        # is invented. Absence of a tool response is not that evidence: a transport failure has none.
        invented = sorted(name for name in absent if _refused_by_name_every_call(name, run_by_tool, run_not_dispatchable))
        # The rest were dispatched: no agent blame, no environment blame, no report text — one
        # advisory plugin finding for the operator (`docs/gate-findings.md`).
        if unmatched := sorted(absent - set(invented)):
            if (line := _verb_surface_unmatched_finding(unmatched, run_by_tool, out.name)) is not None:
                print(line)
        if invented:
            spec['invented_verbs'] = invented
            # …and HOW OFTEN, because the names alone do not say what the agent DID. The families
            # below read `agent` with the reading "no scenario here was blocked by a dead tool",
            # and a reader who cannot see that the agent reached for two verbs it does not have
            # is told whose the finding is and never what it is. Counted off the same run-wide
            # `by_tool` tally `dead_tools` was decided from, so the number in the report and the
            # number that drove the attribution are one number.
            spec['invented_verb_calls'] = {name: int((run_by_tool.get(name) or {}).get('calls') or 0)
                                           for name in invented}
            print(f'note: {", ".join(invented)} appear in no `tools:` or `render_actions:` declaration — '
                  'the agent called a verb it does not have, which is a finding about the AGENT, not about the '
                  'environment. They are excluded from dead_tools.', file=sys.stderr)
        dead -= absent
        unproven -= absent
    refused = refused_tools(run_by_tool)
    disputed = {r['id'] for r in scenario_rows if isinstance(r.get('goal'), dict) and r['goal'].get('goal_disagreement') == DISPUTING_DISAGREEMENT}
    attribution = outcome_attribution(family_members, {r['id']: str(r.get('result') or '') for r in scenario_rows},
                                      tools_by_scenario, dead, unproven, declared_tools=declared, refused=set(refused),
                                      disputed=disputed)
    if dead:
        spec['dead_tools'] = sorted(dead)
    # …and so is a tool the run dispatched ONCE, which failed. Its own key, never folded into
    # `dead_tools`: that name is read as a claim about the tool, and n=1 does not carry one. The
    # renderer prints it beside the table so the floor's not-engaged state is visible rather than
    # rolled onto the healthy side of the ledger.
    if unproven:
        spec['unproven_tools'] = sorted(unproven)
    # …and the subset of those whose "the run never asked twice" is NOT established, because the
    # union suppressed a row that would have crossed the floor. Published rather than folded into
    # `unproven_tools`: the tool's blocking behaviour is unchanged (its family still caps at
    # `mixed`), and what changes is only what the report is allowed to say about the sample.
    if unproven_floored:
        spec['unproven_tools_floored'] = list(unproven_floored)
    # …and the subset that is unproven on OWNERSHIP rather than on sample: dispatched enough, never
    # once worked, and no typed fact says the environment left it unanswered.
    if unowned := [name for name in unowned_failures(run_by_tool) if name in unproven]:
        spec['unproven_tools_unowned'] = unowned
    # …and the real tools whose every call failed, said plainly with the tool's own error (never a seam mock's answer).
    if setup := setup_failed_tools(run_setup, run_by_tool, real_tools, declared_mock_served_tools(getattr(args, 'workdir', None))):
        spec['setup_failed_tools'] = setup
    # Tools whose every call the engine typed as the agent's invalid arguments — the agent's, and named.
    if refused:
        spec['refused_tools'] = refused
    if attribution:
        spec['outcome_attribution'] = attribution
        # The DENOMINATOR these rows could see. A scenario that produced no readable transcript
        # declares no `target_flows`, so it joins no family — and a family of three whose two
        # members crashed would otherwise print `Scenarios: 1` as its population with an
        # attribution decided over a third of it. Stated rather than corrected, for the same
        # reason the runner states `family_gradient.coverage`: the missing rows are unrecoverable
        # from here, and a silent denominator is the failure this whole block exists to remove.
        spec['outcome_attribution_coverage'] = {'reported': len({n for names in family_members.values() for n in names}),
                                                'population': len(dirs),
                                                # WHY the rest are out: a scenario that declares no family (a regression
                                                # replay) is not one that produced no transcript.
                                                'unreadable': sum(1 for d in dirs if not isinstance(_read_json(d / 'transcript.json'), dict))}
        env_zeroed = [row['family'] for row in attribution if row['attribution'] == 'environment']
        if env_zeroed:
            print(f'WARNING: {len(env_zeroed)} flow famil(y/ies) scored 0 with every scenario in them calling a tool that '
                  f'never once succeeded in this run ({", ".join(sorted(dead))}) — {", ".join(env_zeroed)}. Half the outcome '
                  'distribution can be the ENVIRONMENT\'s design rather than the agent; those families were not cleanly '
                  'measured and must not be reported as capability findings.', file=sys.stderr)
    integrity_block = run_integrity(out)
    if copy := agent_host_copy(Path(args.workdir) if getattr(args, 'workdir', None) else None):
        # The agent ran as a copy on its own host: the runner's host record is the harness's, so the line names the copy.
        integrity_block = integrity_block or {}
        integrity_block['agent_host_copy'] = copy
    if run_floor:
        # The run-level floor belongs in the section that answers "did this run measure what it
        # claims to" — a tool count that is a lower bound is precisely that question, and it is
        # the one number in the report the reader has no other way to discover. It also creates
        # the block on a runner too old to have written one: "nobody looked" is a statement about
        # the RUNNER's signals, and this signal is computed right here.
        integrity_block = integrity_block or {}
        integrity_block['tool_count_floor'] = dict(sorted(run_floor.items()))
    if run_unclassified:
        # Beside the floor, and for the same reason: both say a tool number in this report is not
        # the measurement it looks like. The floor says the count is short; this says the run
        # never learned whether those calls succeeded.
        integrity_block = integrity_block or {}
        integrity_block['unclassified_dispatch_outcomes'] = dict(sorted(run_unclassified.items()))
    if unread := declared_lane_unread(dirs, getattr(args, 'workdir', None)):
        # A MEASUREMENT gap: these tools' only channel is the trail, absence on it reads as "never called", and no item
        # of this run carried it — every verdict about them graded the harness's wiring, not the agent.
        integrity_block = integrity_block or {}
        integrity_block['measurement_clean'] = False
        integrity_block['measurement_unclean_because'] = [*integrity_block.get('measurement_unclean_because', []), DECLARED_LANE_UNREAD_CHANNEL]
        integrity_block[DECLARED_LANE_UNREAD_CHANNEL] = unread
        print(f'WARNING: harness — {", ".join(unread)} declare `observation_lane` on the `{_AGENT_STATE_TOOL_TRAIL_KEY}` lane, '
              'and no conversation of this run carries that trail: their calls were not observed, and a check reading '
              'them saw "never called". Record the trail at the tool wrapper, then re-run.', file=sys.stderr)
    if missing := tool_trail_results_missing(trail_results):
        # A HARNESS finding, beside the floor: every evidence judge graded this run's tool calls without
        # their results, so no tool-grounded verdict here measured the agent.
        integrity_block = integrity_block or {}
        integrity_block['tool_trail_results_missing'] = missing
        print(f'WARNING: harness — every one of the {missing["entries"]} `{_AGENT_STATE_TOOL_TRAIL_KEY}` entries whose '
              f'dispatch did not fail carries no `result` ({", ".join(missing["scenarios"])}): the judges graded the tool '
              'calls blind. Record each tool\'s returned value as the entry\'s `result` at the tool wrapper, then re-run.',
              file=sys.stderr)
    if integrity_block is not None:
        # BEFORE the stability quarantine below, because both answer the same question — which
        # revision produced the verdicts in this file — and this one decides the LABEL the
        # quarantine's note is read against.
        if mixed := relabel_rescored_eval_revision(integrity_block, dirs):
            print(f'note: {mixed}', file=sys.stderr)
        # BEFORE `family_stability` rolls the marks up: a per-family verdict computed over rows
        # that are about to be withdrawn would carry the same stale comparison one grain coarser,
        # with nothing left on the row to reveal it — and one computed over rows about to be
        # RECOMPUTED would roll up the pre-rescore comparison under the new revision's label.
        marks = quarantine_restaled_stability(integrity_block, dirs)
        # Withdrawn AND refolded scenarios alike: the marks compare trial 1 with the others as they were scored at
        # run time, and a refold re-aggregates the counts but does not re-derive a comparison.
        trials_doc = spec.get('trials') or {}
        refold = trials_doc.get('rescored') or {}
        withdraw_trial_marks(integrity_block.get('stability') or {},
                             [*(trials_doc.get('run', {}).get('withdrawn_scenarios') or []), *(refold.get('scenarios') or [])],
                             refold.get('one_revision') if isinstance(refold.get('one_revision'), dict) else None)
        # AFTER both, which rebuild or withdraw the rows: the plugin's own check beside the runner's marks.
        identity_marks(integrity_block.get('stability') or {}, scenario_rows)
        if marks.withdrawn:
            print(f'note: the stability marks on {len(marks.withdrawn)} scenario(s) were WITHDRAWN — nothing can vouch '
                  f'for a per-policy comparison between their two attempts: {", ".join(marks.withdrawn)}. Each row\'s '
                  '`marks_withdrawn_detail` names which half is missing and what restores it.', file=sys.stderr)
        if marks.recomputed:
            print(f'note: the stability marks on {len(marks.recomputed)} scenario(s) were RECOMPUTED from both '
                  f'attempts\' rescores at one eval revision ({", ".join(marks.recomputed)}) — the comparison holds '
                  'again, PARTIALLY: see each row\'s `marks_recomputed_detail` for the deterministic policies whose '
                  'mark is a tautology rather than a second sample.', file=sys.stderr)
        if by_family := family_stability(family_members, integrity_block.get('stability') or {}):
            integrity_block['family_stability'] = by_family
        spec['run_integrity'] = integrity_block
    # Copied beside `run_integrity`, never inferred: a stopped run has no scenarios, and a reader that finds the
    # block treats the file as no run (`main` builds none from such a summary).
    if (aborted := aborted_run(out)) is not None:
        spec['aborted'] = aborted
    if detectors := closed_class_summary(closed_classes):
        spec['closed_class_detectors'] = detectors
        # `inconsistent` is NOT folded in here: it was active on some scenarios, so "a judge
        # decided membership alone" is false of those, and it has its own louder meaning (the set
        # was scored against more than one registry) that the report states separately.
        if dark := [c['class'] for c in detectors['classes'] if c['status'] not in ('active', 'inconsistent')]:
            print(f'NOTE: {len(dark)} of {detectors["total"]} closed class(es) have NO code-side detector '
                  f'({", ".join(dark[:5])}{", …" if len(dark) > 5 else ""}) — a judge decided membership in them '
                  'alone. That is a legitimate state, and it is the one worth reading a red in those classes against.',
                  file=sys.stderr)
    fidelity = user_sim_summary(user_sim)
    if fidelity is not None:
        spec['user_sim_integrity'] = fidelity
        if fidelity['clean'] + len(fidelity['unchecked']) < fidelity['measured']:  # unchecked rows measured nothing
            print(f'WARNING: the user simulator departed from the scenario schedule in '
                  f'{fidelity["measured"] - fidelity["clean"] - len(fidelity["unchecked"])} of {fidelity["measured"]} scenarios '
                  f'({", ".join(fidelity["anomaly_kinds"])}) — those transcripts do not enact the '
                  'scenarios they were drawn from, so turn-indexed verdicts over the affected '
                  'turns grade a dialog nobody specified.', file=sys.stderr)
    return spec


#: Where a scoring-time coverage rollup is dropped for this run. Written by whoever held the
#: scoring result — the runner, or the skill that called `score_run` — never derived here.
_DEBT_SIDECAR = '.coverage_debt.json'


def _coverage_debt(out: Path, args: argparse.Namespace) -> dict | None:
    """The scoring-time rollup, COPIED — the backend says which policies graded nothing.

    Deliberately not computed. This file already classifies each eval from its own verdicts
    (that is the Eval-signal table), and that answers a different question: whether a judge
    DISCRIMINATED on this sample. Set-wide debt — a guard false on every scenario, a suppressed
    class, a probe nothing grades — is the backend's determination at scoring time, and a second
    construction of it from the counts here would drift from the one the backend recorded.

    Absent file = the backend shipped no rollup for this run; the key is omitted rather than
    emptied, because "nothing was in debt" and "nobody looked" must not render alike.
    """
    path = Path(args.coverage_debt) if getattr(args, 'coverage_debt', None) else out / _DEBT_SIDECAR
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        print(f'WARNING: {path} could not be read ({exc}) — run.json omits coverage_debt, and the '
              'report will render no graded-nothing panel. Fix the file or drop it.', file=sys.stderr)
        return None
    # Accept both the bare list and the carrier dict: score_run returns the rollup inside its
    # result, and an operator who saved the whole result should not have to unwrap it.
    if isinstance(loaded, list):
        loaded = {'coverage_debt': loaded}
    if not isinstance(loaded, dict) or not isinstance(loaded.get('coverage_debt'), list):
        print(f'WARNING: {path} carries no `coverage_debt` list — run.json omits it.', file=sys.stderr)
        return None
    items = [i for i in loaded['coverage_debt'] if isinstance(i, dict)]
    return {'coverage_debt': items, 'coverage_debt_blocking': loaded.get('coverage_debt_blocking')}


#: The postures `.run_mode` may declare — mirroring the SDK's `_sidecar_mock_mode`, which reads
#: the same file and treats anything else as unset.
#:
#: `mockless` is what the setup flows write for an agent with nothing to mock. `local` is kept
#: because it is a REAL posture (a mocked world hosted in-process) and because workdirs written
#: before the split still carry it for the mockless case — reading it stays correct, writing it
#: for a mockless agent is what claimed a held-constant world that never existed.
_RUN_MODES = ('local', 'connect', 'mockless')

#: `run.json`'s schema id: the producer contract a reader may key on.
RUN_SCHEMA = 'plurai-run/1'
#: The stages a run may carry (`run.json` `stage`): the suite loop's `qualification` pass, the scored `final`
#: run, and the fix plan's `verify` and `repeat` (before-configuration) runs. `scripts/runs_index.py` and
#: `scripts/release_verdict.py` read the same four; anything else is unknown and never written.
RUN_STAGES = ('qualification', 'final', 'verify', 'repeat')


#: The suffix `git describe --dirty` puts on a version whose checkout has tracked changes.
DIRTY_SUFFIX = '-dirty'
#: Tracked files the plugin itself edits in the agent's repository, outside the simulation dir.
SIMULATION_EDITS = ('.gitignore',)


def agent_source(version: str | None, workdir: Path | None) -> dict | None:
    """`{version, commit, changed_outside_simulation, reproducible}` for a `-dirty` agent version, else None — INTERNAL.

    `git describe --dirty` counts every tracked change in the checkout, the simulation's own files included (the
    workdir, and the `.gitignore` lines setup writes), so a clean agent on a bench with a simulation in it reads
    "dirty". `changed_outside_simulation` is the tracked changes outside those (null when git cannot say), and
    `reproducible` whether the agent's source is the commit (null when unknown)."""
    if not version or not version.endswith(DIRTY_SUFFIX):
        return None
    import subprocess  # noqa: PLC0415 - only a dirty version asks git

    changed: list[str] | None = None
    if workdir is not None and workdir.is_dir():
        try:
            top = Path(subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=workdir, capture_output=True, text=True, **utf8_child_text(),
                                      timeout=20, check=True).stdout.strip())
            status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=top, capture_output=True, text=True, **utf8_child_text(),
                                    timeout=20, check=True).stdout
            sim = workdir.resolve().relative_to(top.resolve()).parts
            paths = [line[3:].split(' -> ')[-1].strip('"') for line in status.splitlines() if len(line) > 3]
            changed = sorted(p for p in paths if p not in SIMULATION_EDITS and Path(p).parts[:len(sim)] != sim)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f'note: the agent checkout could not be read ({type(exc).__name__}) — agent_source.reproducible unknown', file=sys.stderr)
    return {'version': version, 'commit': version[:-len(DIRTY_SUFFIX)], 'changed_outside_simulation': changed,
            'reproducible': None if changed is None else not changed}


def resolve_run_stage(out: Path, requested: str | None) -> str | None:
    """The `stage` this run.json carries: `requested` (`--stage`) when it is one of :data:`RUN_STAGES`, else the
    stage the run dir's PREVIOUS run.json carried (a rebuild without `--stage` keeps it), else None — omitted.

    Never read off the folder name. An unknown `--stage` is said on stderr and read as absent, so a typo can
    neither stamp a stage nor erase the one a rebuild keeps."""
    if requested is not None:
        if requested in RUN_STAGES:
            return requested
        print(f'note: --stage {requested!r} is not one of {RUN_STAGES} — omitted; the stage the previous run.json carried (if any) is kept.',
              file=sys.stderr)
    try:
        previous = _read_json(out / 'run.json')
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        previous = None
    stage = previous.get('stage') if isinstance(previous, dict) else None
    return stage if stage in RUN_STAGES else None


#: Exit code for a pinned recording whose harness moved. Distinct from `SystemExit(str)`'s 1 so
#: a caller can tell "nothing to aggregate" from "aggregated nothing ON PURPOSE".
EXIT_PINNED_RUN_STALE = 6
#: Exit code for a run the runner stopped because a hosted mock could not answer (its `MOCK_UNANSWERED`): the
#: summary holds the reason and no verdict, so there is no run to aggregate.
EXIT_RUN_ABORTED = 11
#: Exit code for a run the runner stopped because the backend refused work or could not be reached (its `NOT_MEASURED`:
#: credit, capacity or unavailable). The finished scenarios keep their rows in the summary, but a partial run is no result:
#: nothing is aggregated or rendered from it.
EXIT_MEASURED_STOP = 20
#: The `aborted.reason` of a run stopped for credit, for capacity and as unavailable: `stop_message`'s reasons, spelled here
#: for the same reason as `CONTACT_CHANNELS` and pinned to them by the same test.
CREDIT_REASON, CAPACITY_REASON, UNAVAILABLE_REASON = 'credit', 'capacity', 'unavailable'
#: The stops that keep the finished scenarios' rows (exit `EXIT_MEASURED_STOP`, never a mock abort's), each with what
#: stopped the run in the words of the `ABORTED` line: the customer-facing fact, never the typed cause or status.
MEASURED_STOP_WHY = {CREDIT_REASON: 'for credit', CAPACITY_REASON: 'because the backend could not take the run',
                     UNAVAILABLE_REASON: 'because Plurai could not be reached'}
MEASURED_STOP_REASONS = frozenset(MEASURED_STOP_WHY)
#: The typed stale finding written beside the artifacts in place of `run.json`.
_STALE_SIDECAR = 'run_stale.json'
#: The pin axis whose values this finding names outright, because the SKILL steps that read it
#: name them. Every other refusing axis rides in `moved`.
_HARNESS_AXIS = 'harness-fingerprint-changed'


def pinned_run_staleness(out: Path, workdir: str | None) -> dict | None:
    """The typed stale finding for THIS run dir, or None while its recorded harness still stands.

    Scoped to the recording the pin names (`pin.out_dir` == `out`): a FRESH run built right after
    a harness fix legitimately differs from a pin that still names the previous run — the pin is
    rewritten at the next upload — and refusing it would block exactly the re-run the harness fix
    was made for. An absent pin, a pin with no `out_dir`, or one naming another dir is UNKNOWN,
    not stale; an unreadable lane_pin module is reported, never guessed around.
    """
    if not workdir:
        return None
    try:
        import lane_pin  # noqa: PLC0415 - sibling script under scripts/, already on sys.path for plurai_contracts
    except ImportError as exc:
        print(f'note: cannot import lane_pin ({exc}) — the pinned-run staleness check did not run', file=sys.stderr)
        return None
    pin_path = Path(workdir) / lane_pin.PIN
    pin = lane_pin.read_pin(Path(workdir))
    if pin is None:
        if not pin_path.exists():
            return None
        # A pin that exists and cannot be read is not an absent one: it may name THIS recording,
        # and building over a frame nobody can read is the same defect as building over a moved one.
        return {
            'stale': True,
            'reason': 'pin-unreadable',
            'pin': str(pin_path),
            'remedy': f'{pin_path} exists but does not read as a pin (corrupt, or unreadable) — rewrite it at the next upload '
                      '(`lane_pin.py write`) or delete it; nothing is aggregated over an unverifiable frame',
        }
    if not pin.get('out_dir'):
        return None
    try:
        if Path(str(pin['out_dir'])).resolve() != out.resolve():
            return None
    except OSError:
        return None
    try:
        # WHICH axes void the recording is a PROJECTION of `lane_pin`'s weight table, not a
        # constant here: a private list would give this aggregator its own opinion about a question
        # the pin already answers, and it could disagree with the sidecar writer's list.
        # A regenerated scenario set leaves these transcripts a record of scenarios that no longer
        # exist, and run.json is exactly what the critic reads as evidence about the current suite.
        drift = lane_pin.moved_coordinates(pin, Path(workdir), None)
        moved = {axis: drift[axis] for axis in lane_pin.REFUSING_DETECTED_REASONS if axis in drift}
    except OSError as exc:
        # The pin names THIS recording and its harness cannot be hashed: "unknown" would build
        # run.json over a recording nobody could verify, which is the defect this check exists for.
        return {
            'stale': True,
            'reason': 'harness-fingerprint-unreadable',
            'pinned_harness_fingerprint': pin.get('harness_fingerprint'),
            'run_upload_id': pin.get('run_upload_id'),
            'pinned_at': pin.get('pinned_at'),
            'remedy': f'the harness fingerprint could not be computed ({exc}) — fix the harness file it names and rebuild; '
                      'nothing is aggregated over an unverifiable frame',
        }
    if not moved:
        return None
    finding: dict = {
        'stale': True,
        # The HEAVIEST axis headlines; every one of them is listed in `moved`, so a reader acting
        # on one axis is never blinded by a coarser one that sorts earlier.
        'reason': lane_pin.heaviest(list(moved)),
        'moved': {axis: {'pinned': pinned, 'current': current} for axis, (pinned, current) in moved.items()},
        'run_upload_id': pin.get('run_upload_id'),
        'pinned_at': pin.get('pinned_at'),
        'remedy': 'the frame these transcripts were recorded in is gone — re-run the scenarios, re-upload and re-pin; '
                  'nothing here is evidence about the current suite',
    }
    if _HARNESS_AXIS in moved:
        # Named outright as well as in `moved`: the analyze-simulation / simulation-run steps that
        # read this file name these two fields. Dropping them to a generic map would break the recipe that reads the finding.
        pinned, current = moved[_HARNESS_AXIS]
        finding['pinned_harness_fingerprint'] = pinned
        finding['current_harness_fingerprint'] = current
        finding['remedy'] = ('the harness that produced these transcripts is gone — re-run the scenarios, re-upload and '
                             're-pin; nothing here is evidence about the current harness')
    return finding


#: The renders a refusal removes beside run.json: a previous render standing next to a typed
#: stale finding is the evidence the critic would read instead of the finding.
_RENDERED_OUTPUTS = ('run.json', 'analytics.md', 'eval_signal.md')
_CHARTS_DIR = 'charts'


def aborted_run(out: Path) -> dict | None:
    """The `aborted` block of the run's `run_summary.json` (`reason`, `cause`, `scenario`, `tool`, `status_code`,
    `remedy`; for a credit stop `reason: credit`, `code`, `resets_at?`, `scenario`, `remedy`), or None when
    the runner finished the run.

    The runner writes `aborted: null` into every summary of a run that was not stopped, and an older runner writes
    no key. A block means the run was stopped before it measured everything: a mock that could not answer leaves no
    scored scenario, a credit stop keeps the rows of those that finished. Either way it is not a run to aggregate or
    render."""
    doc = _read_json(out / 'run_summary.json')
    block = doc.get('aborted') if isinstance(doc, dict) else None
    return block if isinstance(block, dict) and block else None


def _remove_renders(out: Path) -> list[str]:
    """Delete every rendered output of a previous build; returns what was removed, for the finding."""
    removed: list[str] = []
    for name in _RENDERED_OUTPUTS:
        path = out / name
        if path.exists():
            path.unlink()
            removed.append(name)
    charts = out / _CHARTS_DIR
    if charts.is_dir():
        shutil.rmtree(charts)
        removed.append(f'{_CHARTS_DIR}/')
    return removed


def _run_driver_block(args: argparse.Namespace) -> dict | None:
    """WHO ran this — `{mode, declared_by, input_required[], asks_unanswered[], gates_resolved[],
    stops_settled_by_config[]}`, plus `record: "partial"` and `unreadable[]` when the findings could not all be read — or None for an
    ordinary human run.

    CONDITIONAL, exactly like `run_mode` and `local_only` above: absence means `interactive`,
    which is also what an absent declaration means, so every existing interactive run.json stays
    byte-identical. A run that had NO human at the terminal says so, and carries every typed
    `run_driver_input_required` finding `ask_guard` filed for it — the gates that had no declared
    default and were not guessed.

    READERS (named because a row nothing reads is dead weight): `render_run_report.py`'s header block, so the analyze/critic step reads the round's
    defaulted decisions rather than inferring them; and `scripts/lane_pin.py`'s qualification-scope
    block, which turns an unmet prerequisite into a limitation on what the verdicts are evidence
    about.

    `None` is therefore a CLAIM — "there was a person here" — which is why the one state that is
    not a claim, the reader being unreachable, is reported through `_UNREAD_CONTRACTS` rather
    than collapsed into it. And it is why `asks_unanswered` is in the condition below: an
    INTERACTIVE round that sanctioned a gate, asked it and got nothing back (the tool errored,
    the operator walked away, the session had nobody in it) published that claim with no person
    anywhere in it.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'hooks'))
        import _run_driver  # noqa: PLC0415 - hooks/ is only on sys.path here
    except ImportError as exc:
        # NAMED, not defaulted. `None` means "an ordinary human run", and this arm is the
        # opposite state: the reader is not there, so WHO ran this round was never read. It
        # fires whenever this file is COPIED out of the plugin into the customer's
        # `plurai_sim/` — the copy the docstring at the top of this file invites — where
        # `parents[3]/hooks` does not exist, and a headless round would have reported as
        # interactive with every `run_driver_input_required` finding silently dropped. Same
        # channel as the contract fallbacks forty lines up, for the same reason: a fallback is
        # never the silent hand-mirror wearing a new name.
        _UNREAD_BLOCKS.append(
            f'`run_driver` — hooks/_run_driver is not importable from '
            f'{Path(__file__).resolve().parents[3] / "hooks"} ({type(exc).__name__}: {exc}), which is this '
            f'file running as a COPY outside its plugin checkout. A headless round therefore reports here '
            f'exactly as a human one does, and its run_driver_input_required findings are not in this file'
        )
        return None
    workdir = Path(args.workdir) if getattr(args, 'workdir', None) else None
    bases = _run_driver.marker_bases(workdir)
    resolution = _run_driver.read_stamp(bases) or _run_driver.resolve(workdir=workdir)
    recorded, gaps = _run_driver.read_findings_with_gaps(bases)
    # DE-DUPLICATED the same way `_rows` below dedups `recorded`: two entries in `bases` can name
    # the same findings file (a cwd fallback and an explicit `--workdir` agreeing on one
    # directory), and `gaps` has no downstream dedup of its own the way `recorded` does — so the
    # same unreadable line would otherwise be named twice.
    gaps = list({(gap.get('base'), gap.get('line'), gap.get('why')): gap for gap in gaps}.values())
    # A resumed build keeps its first session's defaults, so a gate can collect a DEFAULT_APPLIED
    # and an ASK outcome for the same (gate, command) — in either order (a re-ask after a kept
    # default, or a mode flip mid-build that defaults a gate a prior session already asked). It is
    # one gate, and only the LATEST of the two decides which list it lands in: `recorded` is
    # already time-sorted (`read_findings_with_gaps`), so the last of these per key, in list order,
    # is that event — the earlier one is superseded and goes. A STOP is one of these events too: it
    # is durable (a later session must see it), so a later answer or default for the same gate is
    # what retires it here — and its session and durable copies collapse to the one latest row.
    #
    # A gate in `GATE_OUTCOME_PHASE` has ONE owner by declaration, so it is keyed by gate alone, and
    # its landed phase (`GATE_RESOLVED`, a landed id) is the latest event that replaces a default or
    # stop filed under any command.
    outcome_gates = _run_driver.GATE_OUTCOME_PHASE
    _SUPERSEDING = (_run_driver.DEFAULT_APPLIED, _run_driver.ASK_UNANSWERED, _run_driver.ASK_ANSWERED,
                    _run_driver.INPUT_REQUIRED, _run_driver.GATE_RESOLVED, _run_driver.ANSWER_DECLARED)

    def _gate_key(record: dict) -> tuple:
        gate = record.get('gate')
        return (gate,) if gate in outcome_gates else (gate, record.get('command'))

    latest_of: dict[tuple, dict] = {}
    # What each GATE_RESOLVED replaced — published only when that was a headless row.
    replaced: dict[int, str | None] = {}
    for record in recorded:
        if record.get('kind') not in _SUPERSEDING:
            continue
        key, prior = _gate_key(record), latest_of.get(_gate_key(record))
        if record.get('kind') == _run_driver.GATE_RESOLVED:
            prior_kind = prior.get('kind') if prior is not None else None
            replaced[id(record)] = replaced.get(id(prior)) if prior_kind == _run_driver.GATE_RESOLVED else prior_kind
        latest_of[key] = record
    recorded = [r for r in recorded if r.get('kind') not in _SUPERSEDING or r is latest_of.get(_gate_key(r))]
    recorded = [r for r in recorded if r.get('kind') != _run_driver.GATE_RESOLVED or replaced.get(id(r)) in (
        _run_driver.DEFAULT_APPLIED, _run_driver.INPUT_REQUIRED, _run_driver.ASK_UNANSWERED)]
    # A PREREQUISITE whose answer is a config key (`DECLARED_BY_CONFIG`) is settled by that key — its stop,
    # and an ask of it that came back empty: matched BY GATE, so a row filed under any command goes. A STOP
    # is settled only when the hook attested the key at a gate reached after it (`ANSWER_DECLARED`): a key
    # that appears in the config inside the window the stop halted leaves the stop standing. What the
    # attestation shows is that the key was present at that gate, not who wrote it: the run driver editing
    # the config mid-window and the model writing the key leave the same trace. A settled stop is
    # therefore published as `stops_settled_by_config` with its writer `unattested`, never dropped and
    # never worded as the run driver's own declaration.
    declared_at: dict[object, float] = {}
    for r in recorded:
        if r.get('kind') == _run_driver.ANSWER_DECLARED:
            declared_at[r.get('gate')] = max(declared_at.get(r.get('gate'), 0.0), float(r.get('at') or 0.0))

    def _settled_by_config(record: dict) -> bool:
        gate = record.get('gate')
        if record.get('kind') not in (_run_driver.INPUT_REQUIRED, _run_driver.ASK_UNANSWERED):
            return False
        if _run_driver.declared_answer(str(gate), workdir) is None:
            return False
        return record.get('kind') == _run_driver.ASK_UNANSWERED or declared_at.get(gate, -1.0) >= float(record.get('at') or 0.0)

    settled_stops: dict[tuple, dict] = {}
    for r in recorded:
        if r.get('kind') == _run_driver.INPUT_REQUIRED and _settled_by_config(r):
            settled_stops.setdefault((r.get('gate'), r.get('command')), {
                **{k: r[k] for k in ('gate', 'command', 'needs') if k in r}, 'writer': 'unattested'})
    recorded = [r for r in recorded if not _settled_by_config(r)]

    def _rows(kind: str, *fields: str) -> list[dict]:
        """The hook's findings of one kind, projected and DE-DUPLICATED by gate+command.

        A denied ask is often retried inside the same window — the model re-sanctions and asks
        again — and each retry appends another line. Counting those as separate decisions would
        report one defaulted gate as three, which is a number the round never earned.
        """
        out: list[dict] = []
        seen: set[tuple] = set()
        for record in recorded:
            if record.get('kind') != kind:
                continue
            key = (record.get('gate'), record.get('command'))
            if key in seen:
                continue
            seen.add(key)
            out.append({k: record[k] for k in fields if k in record})
        return out

    # BOTH halves of what the run driver's absence cost this run: the gates it ANSWERED from the
    # declared table, and the gates it refused to answer at all. A record carrying only the second
    # leaves "did grounding-gate actually default to proceed-without-corpus here?" answerable only
    # from the model's prose.
    required = _rows(_run_driver.INPUT_REQUIRED, 'gate', 'command', 'needs')
    # A scope gate's default carries the cut it backed (`_run_driver.scope_of_default`): named here, where a headless
    # run driver reads what was decided for them. Absent on every other row.
    defaulted = _rows(_run_driver.DEFAULT_APPLIED, 'gate', 'command', 'default', 'why', *_run_driver.SCOPE_KEYS)
    # …and what a defaulted or stopped gate's own phase later APPLIED, in place of that row.
    resolved = _rows(_run_driver.GATE_RESOLVED, 'gate', 'applied')
    # …and the INTERACTIVE half: gates that were sanctioned, ASKED, and came back with nothing.
    # Deduplicated differently from `_rows` on purpose — not "the first line per gate" but the
    # LAST EVENT per gate, because `report_guard` re-arms an unanswered sanction so the model can
    # re-ask, and a gate that errored and was then answered must not read as unanswered forever.
    latest: dict[tuple, dict] = {}
    for record in recorded:
        if record.get('kind') in (_run_driver.ASK_UNANSWERED, _run_driver.ASK_ANSWERED):
            latest[(record.get('gate'), record.get('command'))] = record
    unanswered = [{k: r[k] for k in ('gate', 'command') if k in r}
                  for r in latest.values() if r.get('kind') == _run_driver.ASK_UNANSWERED]
    # `unanswered` belongs in this condition, not only in the block below: `None` is the claim
    # "there was a person here", and a round that put a question to that person and got nothing
    # back is the one round that must not publish it. An ask that WAS answered is the evidence
    # FOR the claim, so it leaves this record exactly as it has always been.
    # …and `gaps` too: a record that could not be read in full is not the claim either.
    if (resolution.mode == _run_driver.DEFAULT_MODE and not required and not defaulted and not unanswered and not gaps
            and not resolved and not settled_stops):
        return None
    block: dict = {'mode': resolution.mode, 'declared_by': resolution.source}
    if gaps:
        # PARTIAL, with each unreadable line named — never folded into "no gate was reached".
        block['record'] = 'partial'
        block['unreadable'] = gaps
    if defaulted:
        block['defaults_applied'] = defaulted
    if required:
        block['input_required'] = required
    if unanswered:
        block['asks_unanswered'] = unanswered
    if resolved:
        block['gates_resolved'] = resolved
    if settled_stops:
        block['stops_settled_by_config'] = list(settled_stops.values())
    return block


def _resolve_run_mode(args: argparse.Namespace) -> str | None:
    """`--run-mode` if given, else `<workdir>/.run_mode`, else None (with a note on stderr).

    Never inferred from the artifacts: a report DEDUCING the posture from its tool evidence would
    misread a local run, whose calls are all reconstructed, as an alarm about the agent in the
    mock-bypass headline.
    """
    if getattr(args, 'run_mode', None):
        return args.run_mode
    workdir = getattr(args, 'workdir', None)
    if not workdir:
        print('note: no --workdir and no --run-mode — run.json omits run_mode, and the report '
              'cannot tell a local run from a mock-bypassing one.', file=sys.stderr)
        return None
    try:
        raw = (Path(workdir) / '.run_mode').read_text(encoding='utf-8').strip().lower()
    except OSError:
        # NOT a defect on its own: no writer stamps `connect`, so the marker is simply absent on
        # every deployed-mock run. Phrased as a fault it would fire on the normal path every time.
        print(f'note: no .run_mode under {workdir} — expected on a deployed-mock run (nothing writes a '
              '`connect` marker). run.json omits run_mode and the render reads absence as connect; pass '
              '--run-mode connect to state it.', file=sys.stderr)
        return None
    if raw not in _RUN_MODES:
        print(f'note: .run_mode under {workdir} says {raw!r}, which is not one of {_RUN_MODES} — '
              'run.json omits run_mode rather than stamping a guess.', file=sys.stderr)
        return None
    return raw


def main(argv: list[str] | None = None) -> int:
    return _client_handshake.refusal_main(lambda: _main(argv))


def _main(argv: list[str] | None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate a run's per-scenario artifacts into run.json.")
    ap.add_argument('out_dir', help='the run dir ($OUT) holding the per-scenario subdirs')
    ap.add_argument('--run-name', help='defaults to the run dir name')
    ap.add_argument('--dataset-name')
    ap.add_argument('--eval-set-id', help='the eval-set these verdicts came from — stamps the render for staleness checks')
    ap.add_argument('--gt-revision', type=int, help='the GT revision scored against (0 is real, not absent)')
    ap.add_argument('--agent-version')
    ap.add_argument('--expected-scenarios', type=int,
                    help='how many scenarios this run was SUPPOSED to produce. Optional — the runner writes its '
                         "own roster to <out_dir>/run_summary.json and that is read automatically; pass this only "
                         'when it is missing or you know better. A shortfall is shouted on stderr and recorded as '
                         'totals.missing_scenarios, so a run that lost scenarios cannot render as a complete one.')
    ap.add_argument('--local-only', action='store_true', help='no observability platform recorded this run')
    ap.add_argument('--coverage-debt',
                    help="the scoring-time coverage rollup (a score_run result, or its bare "
                         f"`coverage_debt` list); defaults to <out_dir>/{_DEBT_SIDECAR} when present")
    ap.add_argument('--workdir', help="the agent workdir ($PLURAI_DIR) — its .run_mode marker says whether this "
                                      'run used the in-process mock (local) or a deployed one (connect)')
    ap.add_argument('--run-mode', choices=_RUN_MODES,
                    help='override the .run_mode marker; use when the run did not come from this workdir')
    ap.add_argument('--stage', metavar='{' + ','.join(RUN_STAGES) + '}',
                    help="the run's stage, stamped as run.json `stage` (never inferred from the folder name). Omitted, a "
                         "rebuild keeps the previous run.json's stage; an unknown value is said on stderr and omitted")
    ap.add_argument('--regression-replays', action='append',
                    help="the regression-only step's out dir ($ART/regression_replays/<run>): its run_summary.json's "
                         '`regression_replays` is copied, joined to the folders under <workdir>/regressions. Repeat it for '
                         'the full replays a regression fell back to, AFTER the single-hop dir: the last dir wins per regression')
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    # BEFORE anything else: a stopped run is no result, and the render of an earlier run in the same dir would
    # otherwise stand beside a summary that says nothing was measured.
    if (aborted := aborted_run(out)) is not None:
        removed = _remove_renders(out)
        reason = aborted.get('reason')
        measured_stop = reason in MEASURED_STOP_REASONS
        # A measured stop keeps the rows of the scenarios that finished, so it did score something; it is still not a run.
        stopped = (f'stopped this run {MEASURED_STOP_WHY[reason]}, so no run.json or analytics were built from the part that finished'
                   if measured_stop else 'stopped this run before it scored anything, so no run.json or analytics were built')
        # Capacity and unavailable stops say what happened in the customer's words, so their typed cause stays in run_summary.json.
        cause = f' ({aborted["cause"]})' if aborted.get('cause') and reason not in (CAPACITY_REASON, UNAVAILABLE_REASON) else ''
        # A measured stop is said once, by `stop_message.py`; its `remedy` is the stop's own text and stays out of this line.
        next_step = ('Say the stop with `stop_message.py refusal --run-summary "<run dir>/run_summary.json"`.' if measured_stop
                else aborted.get("remedy") or "Read run_summary.json `aborted` for what to change.")
        print(f'ABORTED {aborted.get("reason", "aborted")}{cause}: the runner {stopped}; '
              f'removed {removed or "nothing"}. {next_step}', file=sys.stderr)
        return EXIT_MEASURED_STOP if measured_stop else EXIT_RUN_ABORTED
    # BEFORE aggregating: a run.json built over a dead harness is not made current by being
    # complete, and the critic PREFERS run.json rows — so the stale one is removed, not left.
    stale = pinned_run_staleness(out, args.workdir)
    if stale is not None:
        # The previous render goes too — analytics.md and the charts are what a reader (and the
        # critic handed this out_dir) would otherwise read as current — and the finding is written
        # tmp+replace: a half-written sidecar at a path the next reader trusts is the one failure a
        # typed refusal must not have.
        stale['removed'] = _remove_renders(out)
        sidecar = out / _STALE_SIDECAR
        tmp = sidecar.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(stale, indent=2) + '\n', encoding='utf-8')
        tmp.replace(sidecar)
        # EVERY refusing axis that moved, with both its values — not the headline alone. The
        # `moved` map is absent on the two frame-unverifiable reasons, which have no coordinate.
        values = ' · '.join(
            f'{axis}: pinned {pair.get("pinned")} → current {pair.get("current")}'
            for axis, pair in (stale.get('moved') or {}).items()
        ) or ' · '.join(f'{k.replace("_harness_fingerprint", "")} {v}' for k, v in stale.items() if k.endswith('_harness_fingerprint') and v)
        print(f'STALE {stale["reason"]}: this run dir is the recording pinned by the eval lane and its frame cannot stand'
              f'{" — " + values if values else ""}. No run.json written; removed {stale["removed"] or "nothing"}; the typed '
              f'finding is at {sidecar}. {stale["remedy"]}.', file=sys.stderr)
        return EXIT_PINNED_RUN_STALE
    spec = build_spec(out, args)
    (out / 'run.json').write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if (run_driver := regressions_detail(out, args, spec)) is not None:
        (out / REGRESSIONS_DETAIL_JSON).write_text(json.dumps(run_driver, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    else:  # an earlier build's run driver file must not stand beside this run.json
        (out / REGRESSIONS_DETAIL_JSON).unlink(missing_ok=True)
    # A recording that is current again (re-pinned, or re-made) must not sit beside a stale finding
    # from its predecessor — a reader that checks the sidecar first would refuse a fresh run.
    (out / _STALE_SIDECAR).unlink(missing_ok=True)
    t = spec['totals']
    missing = f" / {t['missing_scenarios']} MISSING" if t.get('missing_scenarios') else ''
    # Named on the headline for the same reason as the three around it: it moves no rate, it
    # decides how the rates read. Without it a 7-scenario run with 2 incompletes prints
    # "3 passed / 2 failed" and reads as a 5-scenario run.
    landed_short = f" / {t['incomplete']} INCOMPLETE" if t.get('incomplete') else ''
    if t.get('not_valid'):
        unscored = sorted({str(r['error_kind']) for r in spec['scenarios'] if str(r.get('error_kind') or '').startswith(HARNESS_KIND_PREFIX)})
        landed_short += f" / {t['not_valid']} NOT VALID (not scored: {', '.join(unscored)})"
    timeouts = f" / {t['harness_timeouts']} SIMULATION TIMEOUT" if t.get('harness_timeouts') else ''
    unscored = ' / NEVER SCORED' if t.get('unscored_run') else ''
    # On the headline for the same reason as the three above: it does not move a total, it
    # decides how the totals read.
    cut = spec.get('ended_early') or {}
    early = f" / {len(cut.get('scenarios') or {})} ENDED EARLY" if cut else ''
    over = f" × {t['trials']} trials = {t['conversations']} conversations" if t.get('trials') else ''
    print(f"wrote {out / 'run.json'} — {t['scenarios']} scenarios{over} "
          f"({t['passed']} passed / {t['failed']} failed{landed_short} / {t['errored']} errored{missing}{timeouts}{unscored}{early}), "
          f"{len(spec['evals'])} evals")
    # LAST, and once: every the backend constant that came from this file's own literal instead of
    # the vendored payload gets named here. A default that fires without saying so is the
    # hand-mirror again.
    _report_contract_fallbacks()
    return 0


if __name__ == '__main__':
    utf8_stdio()
    raise SystemExit(main())
