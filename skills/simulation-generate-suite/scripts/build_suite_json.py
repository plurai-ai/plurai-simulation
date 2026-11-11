#!/usr/bin/env python3
"""Write `$ART/suite.json` from what the backend returned, so no field is hand-copied.

    build_suite_json.py --art <ART> --policies <policies_config.yaml> --overlay <overlay.json>
                        [--bundle <final_bundle.json>] [--regressions <json>]... [--workdir <WORKDIR>] [--meta <meta.json>]
    build_suite_json.py --draft --art <ART> --meta <ART>/.pieces/meta_<set>.json
                        [--policies <policies_config.yaml>] [--overlay <overlay.json>] [--workdir <WORKDIR>]

Every field is read from a file on disk, except the overlay's, which nothing on disk holds:

* `--meta` (default `<ART>/final_set_meta.json`) — the set's whole `get_artifact_meta` answer: the scenario
  rows and their walk numbers, `coverage`, `dataset_metadata` (complication flows, capability gaps,
  knowledge reach, bundle folders).
* `<ART>/scenarios/<id>/` — the fetched slot bundle: `scenario.yaml` (the graded `scenario_target`,
  `walk_trace`, the `success_criteria.deterministic` goal check), `persona.yaml` (copied as it came), `initial_world_state.yaml` (the seeded count),
  `scenario_card.yaml` (the backend's summary card, copied to `scenarios[].card`: :func:`read_card`);
  `<ART>/scenarios/dimension_graph.yaml` gives the per-hop display edges.
* `--policies` — one eval row per policy KEY: the key is the eval id.
* `--bundle` — `final_bundle.json`: which rows are regressions or trace scenarios, and the held-back list.
* `--regressions` — repeatable; each the JSON stdout of `final_bundle.py missing`,
  `regressions.py ledger-owners --declined` or `final_bundle.py target-graded`. Only the keys in
  `REGRESSION_KEYS` are read, and only a non-empty list is written. An empty file is `{}`.
* `<WORKDIR>/scenario_state_schema.yaml` + `plurai.config.yaml` `world_state.entity_types` — the
  environment schema. `<WORKDIR>/.eval_set_id` — the eval set.
* `--diagnostics` (default `<ART>/suite_diagnostics_final.json` when present) — the set's run
  `suite_diagnostics` job envelope: its `complication_enactment` rows, and its `tool_coverage` (the
  set's `coverage.tools` completed with the run's never-called tools). Read only when its
  `scenario_set_id` is this set's. An internal coverage record: no customer page renders it, and nothing gates on it.
  `dealt_never_enacted` names each row (a dealt persona beat's included, `built_in: true`) that no run enacted
  (:func:`dealt_never_enacted`).

The overlay is the authored remainder, refused outside its allow-list:
`{"scenarios": {"<id>": {"title", "summary", "card"}}, "requested_count": <int>, "knowledge": {"per_document", "set_aside_documents"}}`.
A row's `title` and `summary` are the overlay's, else the card's `title` and `details`, else absent; the overlay
authors them only for a scenario with no usable card, per the deliverable's rule. `card` is `{field: text}` over
:data:`CARD_TEXT_FIELDS` and :data:`CARD_LIST_FIELDS`, applied over the slot's card to rewrite a string the publish
guard flagged; any other key, a value of the wrong type, or an override for a scenario with no card is left out
with a stderr line. A card that is malformed or marked `stale` is no card: it is left out with a stderr line and
never fails the build. `requested_count` is the `count` the
final generation was started with (the set's metadata does not keep it: a top-up plans only the new
slots); `per_document` comes off the corpus extract's stdout, which no file keeps. With no `set_aside_documents`
in the overlay, it is read from `<ART>/knowledge_summary.json` (`grounding_pull.py knowledge`, out of each corpus's
`corpus_meta.json`): only when every base it sums belongs to the set and every document row is one of theirs, so
a document it does not list was kept, never unknown (:func:`set_aside_documents`).

Ids: `scenarios[].id` is the scenario's run folder name (the set's scenario name); `evals[].id` is its
`policies_config.yaml` key. Each eval row carries `kind` (`llm_judge` / `code` / `hybrid`, derived from the policy's
`type`: :data:`EVAL_KINDS`) and the policy's `prompt_summary` on one line, unless the customer-text guard flags it; likewise
its backend-written `rule` (at most :data:`RULE_MAX_CHARS`) and `group` (one of :data:`EVAL_GROUPS`). Writes no `audit_notes` and no `complexity_subtitle` (the renderer derives
the subtitle from the unit). A bundle-built set (no walk) carries `dimension_graph: {absent: 'bundle_built'}`.

`--draft` builds the page's suite while the suite is still being made, from a set that has landed
(`read_meta` still refuses a set that is not `done`): `--overlay` is optional and a scenario with neither
an overlay nor a card `title`/`summary` simply lacks them (never exit 3); `--policies` is optional (no policies yet →
`evals: []`); `--bundle`/`--regressions` are final-only inputs and are refused (a draft borrows only the row
sources from `$ART/final_bundle.json`, and only when every folder it names is in this set). The output carries
`draft: true`; the final build at the deliverable overwrites it and carries no `draft` key.

Exit codes: 0 written · 2 an input unreadable or not the expected shape, or an overlay key outside its
allow-list (nothing written) · 3 a scenario has no `title` or `summary` from either the overlay or its card
(nothing written; stdout names them; never in a draft). :func:`untitled` is that test, shared with
`artifact_deps.suite_command`'s choice between the final build and a draft.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

SCHEMA = 'plurai-suite/1'

OVERLAY_KEYS = frozenset({'scenarios', 'requested_count', 'knowledge'})
OVERLAY_SCENARIO_KEYS = frozenset({'title', 'summary', 'card'})
OVERLAY_KNOWLEDGE_KEYS = frozenset({'per_document', 'set_aside_documents'})

#: The backend's per-scenario summary card, in each fetched slot.
CARD_FILE = 'scenario_card.yaml'
CARD_SCHEMA = 'plurai-scenario-card/1'
#: The card's prose fields, each a string; `coverage_adds` ("Takes the agent through …", rendered from the scenario's
#: verified flows) is null when the scenario has none.
#: The persona sits in flat `persona_*` keys so no `persona` key nests inside the card: the publish guard skips
#: a `persona` key's values at any depth, and every card string is ours to guard.
CARD_TEXT_FIELDS = ('title', 'what_we_test', 'gotcha', 'why_not_trivial', 'coverage_adds', 'persona_line1', 'persona_line2', 'details')
CARD_NULLABLE = frozenset({'coverage_adds'})
#: The card's list of short behavior labels (the row labels), each a string.
CARD_LIST_FIELDS = ('behaviors',)
#: Copied as the card carries it and never overridden: the backend copies the name from persona.yaml.
CARD_FIXED_FIELDS = ('persona_name',)
#: Card field → the row field it fills when the overlay authored none.
CARD_ROW_FALLBACK = (('title', 'title'), ('summary', 'details'))

#: Keys copied off a `--regressions` file, each when it is a non-empty list.
REGRESSION_KEYS = ('missing_regressions', 'missing_trace_scenarios', 'declined_regressions', 'ungraded_on_target')

#: Policy `type` → the catalog's Type cell. An unknown type is shown as itself, never blank.
EVAL_TYPES = {
    'code_eval': 'code',
    'llm_as_judge': 'llm',
    'retrieval_metric': 'retrieval',
    'hybrid': 'code + llm',
    'beat_resolution': 'knowledge beat',
}
EVAL_SCOPES = {'turn_based': 'per-turn', 'end_to_end': 'end-to-end'}
#: Policy `type` → the eval's type label, `evals[].kind`: `llm_judge` (LLM-as-a-judge), `code` or `hybrid`. Derived on
#: every build, never stamped into the set, so an optimize-eval adopt or revert never leaves it stale.
#: `retrieval_metric` is deterministic matching plus model judgements, so `hybrid`. `beat_resolution` is decided by its
#: slice (`BEAT_KINDS`). A type outside this table and `UNLABELLED_TYPES` gets no `kind` and a stderr line — never a
#: wrong label.
EVAL_KINDS = {
    'llm_as_judge': 'llm_judge',
    'debate_system': 'llm_judge',
    'code_eval': 'code',
    'hybrid': 'hybrid',
    'retrieval_metric': 'hybrid',
}
#: `beat_resolution` by `additional_params.metric`: `coverage` is arithmetic over the planned questions (no model call);
#: `accuracy` is model-judged. A slice with no metric and a `beat_id` is an absence slice: model-judged too.
BEAT_KINDS = {'coverage': 'code', 'accuracy': 'llm_judge'}
#: Types that carry no label, silently: `custom` is a generated graph that may hold model calls.
UNLABELLED_TYPES = frozenset({'custom'})
#: The backend-written one-line summary of what an eval checks, copied onto `evals[].prompt_summary`.
PROMPT_SUMMARY = 'prompt_summary'
#: The backend-written grouping pair (the backend's own group ids and rule cap, mirrored here), copied onto `evals[].rule`
#: and `evals[].group`: the short rule the eval checks (evals that check the same rule carry the identical string) and
#: the page section it sits in. A rule the guard flags or that runs past the cap, and a group outside the seven, is left
#: out with a stderr line: the eval shows ungrouped, never a refusal.
RULE = 'rule'
GROUP = 'group'
RULE_MAX_CHARS = 40
EVAL_GROUPS = frozenset({'goal', 'truthfulness', 'guardrails', 'safe_changes', 'conversation', 'reply_rules', 'tool_use'})
#: The page's artifact kind in `customer_text_vocabulary.json`: a copied summary is read as one value that page embeds.
PAGE_KIND = 'report_html'
_ROOT_SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'

#: Bundle list → the row's `source`; every other row is the walk's (`spec`).
BUNDLE_SOURCES = (('regressions', 'regression'), ('trace_scenarios', 'trace'))

NOTE_CHARS = 80


class Refused(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def _read_json(path: Path, *, empty: Any = None) -> Any:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise Refused(f'{path}: unreadable ({exc})') from exc
    if not text.strip() and empty is not None:
        return empty
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f'{path}: not JSON ({exc})') from exc


def _read_yaml(path: Path) -> Any:
    """The parsed file, or None when it does not exist. A file that exists and does not parse refuses."""
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f'{path}: unreadable YAML ({exc})') from exc


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pct(part: int, whole: int) -> float:
    value = round(100.0 * part / whole, 1)
    return int(value) if float(value).is_integer() else value


def _ids(value: Any) -> list[str]:
    return [str(x) for x in value] if isinstance(value, list) else []


def _count_or_null(value: Any) -> Optional[int]:
    """A flow-id list as its count; `null` stays `null` (the split did not run), never 0."""
    return None if value is None else len(_ids(value))


def set_aside_documents(art: Path, corpus_ids: list[str], rows: set) -> Optional[dict]:
    """`{document: chunks the build set aside}` summed over the set's knowledge bases, or None when it cannot be complete."""
    try:
        summary = json.loads((art / 'knowledge_summary.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    kbs = [kb for kb in summary.get('knowledge_bases') or [] if isinstance(kb, dict) and set(_ids(kb.get('corpus_ids'))) & set(corpus_ids)]
    if not corpus_ids or {c for kb in kbs for c in _ids(kb.get('corpus_ids'))} != set(corpus_ids):
        return None
    if not all(isinstance(kb.get('set_aside_documents'), dict) and isinstance(kb.get('documents'), list) for kb in kbs):
        return None
    merged: dict = {}
    for kb in kbs:
        for doc, n in kb['set_aside_documents'].items():
            merged[doc] = merged.get(doc, 0) + n
    return merged if rows <= set(merged) | {d for kb in kbs for d in kb['documents']} else None


# ── the overlay ───────────────────────────────────────────────────────────────
def read_overlay(path: Path) -> dict:
    overlay = _read_json(path)
    if not isinstance(overlay, dict):
        raise Refused(f'{path}: the overlay is a JSON object')
    extra = sorted(set(overlay) - OVERLAY_KEYS)
    if extra:
        raise Refused(f'{path}: overlay key(s) {extra} have a source on disk or are not suite.json fields; allowed: {sorted(OVERLAY_KEYS)}')
    scenarios = overlay.get('scenarios') or {}
    if not isinstance(scenarios, dict):
        raise Refused(f'{path}: overlay `scenarios` is an object keyed by scenario id')
    for sid, row in scenarios.items():
        if not isinstance(row, dict):
            raise Refused(f'{path}: overlay scenario {sid!r} is an object')
        bad = sorted(set(row) - OVERLAY_SCENARIO_KEYS)
        if bad:
            raise Refused(f'{path}: overlay scenario {sid!r} carries {bad}; only {sorted(OVERLAY_SCENARIO_KEYS)} are authored')
    knowledge = overlay.get('knowledge') or {}
    if not isinstance(knowledge, dict) or set(knowledge) - OVERLAY_KNOWLEDGE_KEYS:
        raise Refused(f'{path}: overlay `knowledge` takes only {sorted(OVERLAY_KNOWLEDGE_KEYS)}')
    count = overlay.get('requested_count')
    if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
        raise Refused(f'{path}: overlay `requested_count` is the int the final generation was started with')
    return overlay


# ── the set's metadata ────────────────────────────────────────────────────────
def read_meta(path: Path) -> dict:
    """The `detail` block of a `get_artifact_meta` answer (the whole answer, or its detail block)."""
    meta = _read_json(path)
    if not isinstance(meta, dict):
        raise Refused(f'{path}: not a get_artifact_meta answer')
    status = meta.get('status')
    if status is not None and status != 'done':
        raise Refused(f'{path}: the set is `{status}`, not `done`; read its meta again once the job lands')
    detail = meta.get('detail') if isinstance(meta.get('detail'), dict) else meta
    if not any(isinstance(detail.get(k), list) for k in ('scenarios', 'scenario_names')):
        raise Refused(f'{path}: carries neither `detail.scenarios` nor `detail.scenario_names`; write the whole get_artifact_meta answer')
    if not detail.get('scenario_set_id') and meta.get('id'):
        detail = {**detail, 'scenario_set_id': meta['id']}
    return detail


def scenario_names(detail: dict) -> list[str]:
    rows = detail.get('scenarios')
    if isinstance(rows, list):
        names = [str(r.get('name')) for r in rows if isinstance(r, dict) and r.get('name')]
        if names:
            return names
    return _ids(detail.get('scenario_names'))


# ── coverage ──────────────────────────────────────────────────────────────────
def _flow_ids(detail: dict, graph: Optional[dict], coverage: dict) -> list[str]:
    summary = detail.get('dimension_graph') if isinstance(detail.get('dimension_graph'), dict) else {}
    ids = _ids(summary.get('flow_ids'))
    if not ids and isinstance(graph, dict):
        ids = [str(f.get('id')) for f in graph.get('flows') or [] if isinstance(f, dict) and f.get('id')]
    if not ids:
        ids = sorted(set(_ids(coverage.get('covered')) + _ids(coverage.get('covered_unverified')) + _ids(coverage.get('uncovered'))))
    return ids


def _in_no_flow(*blocks: Any) -> list[str]:
    """The tools a `tool_coverage` block types `in_no_flow`: the agent has them and the flow graph carries none of them."""
    return sorted({str(r.get('tool')) for b in blocks if isinstance(b, dict) for r in b.get('uncovered') or []
                   if isinstance(r, dict) and r.get('cause') == 'in_no_flow' and r.get('tool')})


def tools_reached(run_tools: Any) -> Optional[dict]:
    """`{tools: K, of: M, floor}` — K of the M simulated tools (`declared`, the map's tools) the scored run called.

    `floor` when the run's `run_note` says the call record is partial: K is then a lower bound. None without a run
    block, or when it carries no `called` list or no declared tool."""
    if not isinstance(run_tools, dict) or not isinstance(run_tools.get('called'), list):
        return None
    declared = list(dict.fromkeys(_ids(run_tools.get('declared'))))
    if not declared:
        return None
    called = set(_ids(run_tools['called']))
    return {'tools': sum(1 for t in declared if t in called), 'of': len(declared), 'floor': bool(run_tools.get('run_note'))}


def coverage_block(detail: dict, graph: Optional[dict], run_tools: Any = None) -> Optional[dict]:
    """The backend's `coverage` verbatim, plus the `cards` (and the legacy `caption`) the report draws.

    With the scored run's `tool_coverage`, the first card is the simulated tools that run reached. The flow-share
    cards are left out whenever the set's own tool block (or the run's) types a tool `in_no_flow`: the flow graph
    then omits part of the agent, and a share of it would read as coverage of the whole agent."""
    source = detail.get('coverage')
    if not isinstance(source, dict):
        return None
    block = dict(source)
    total = source.get('total_flows')
    covered = _ids(source.get('covered'))
    typed = 'covered_unverified' in source
    declared = _ids(source.get('covered_unverified'))
    cards = []
    if (reach := tools_reached(run_tools)) is not None:
        block['reached'] = reach
        cards.append({'label': 'simulated tools reached' + (' (at least)' if reach['floor'] else ''), 'value': reach['tools'],
                      'unit': f"of {reach['of']}", 'accent': 'blue'})
    if orphans := _in_no_flow(source.get('tools'), run_tools):
        print(f'build_suite_json: {len(orphans)} tool(s) are in no flow, so the flow-share cards are left out', file=sys.stderr)
    elif isinstance(total, int) and not isinstance(total, bool) and total > 0 and isinstance(source.get('covered'), list):
        card: dict[str, Any] = {'label': 'valid paths covered', 'value': _pct(len(covered) + len(declared), total), 'unit': '%', 'accent': 'blue'}
        if typed:
            card['verified'] = _pct(len(covered), total)
            card['declared'] = _pct(len(declared), total)
        for key in ('auditor_only', 'advisory_blind'):
            if key in source:
                card[key] = _count_or_null(source[key])
        cards.append(card)
        complication = (detail.get('dataset_metadata') or {}).get('complication_flows')
        if isinstance(complication, list):
            declining = set(_ids(complication))
            core = [f for f in _flow_ids(detail, graph, source) if f not in declining]
            if core:
                reached = set(covered) | set(declared)
                cards.append({'label': 'core components exercised', 'value': _pct(sum(1 for f in core if f in reached), len(core)),
                              'unit': '%', 'accent': 'cyan'})
        else:
            print('build_suite_json: dataset_metadata carries no complication_flows, so the core-components card is left out', file=sys.stderr)
    if cards or orphans:
        # With tools in no flow and no scored run there is no card to draw, on purpose: written empty so the
        # report leaves the section out without asking for cards that must not exist.
        block['cards'] = cards
    legacy = source.get('unverified')
    if not typed and isinstance(legacy, dict) and legacy:
        credits = sum(v for v in legacy.values() if _number(v))
        block['caption'] = f'{credits:g} flow credit(s) were declared by the walk and not verified by the audit.'
    return block


def run_limits(path: Optional[Path], set_id: Any, *, explicit: bool = False) -> dict:
    """`{complication_enactment, tool_coverage}` off the set's run diagnostics, or `{}`.

    Its `tool_coverage` gives the "tools reached" card (:func:`tools_reached`); the rest is an internal record. This file can never fail
    the build: unreadable, not an envelope, or for another set (both ids must be non-empty and equal) contributes nothing, said on stderr."""
    if path is None or not path.is_file():
        if explicit and path is not None:
            print(f'build_suite_json: --diagnostics {path} does not exist — the run\'s coverage record is left out', file=sys.stderr)
        return {}
    try:
        envelope = _read_json(path)
    except Refused as exc:
        print(f'build_suite_json: {exc} — its coverage record is left out', file=sys.stderr)
        return {}
    status = envelope.get('status') if isinstance(envelope, dict) and isinstance(envelope.get('detail'), dict) else None
    if status is not None and status != 'done':
        print(f'build_suite_json: {path.name} is a `{status}` job, not `done` — its coverage record is left out', file=sys.stderr)
        return {}
    detail = envelope.get('detail') if isinstance(envelope, dict) and isinstance(envelope.get('detail'), dict) else envelope
    if not isinstance(detail, dict):
        print(f'build_suite_json: {path.name} is not a suite_diagnostics job envelope — its coverage record is left out', file=sys.stderr)
        return {}
    theirs, ours = str(detail.get('scenario_set_id') or '').strip(), str(set_id or '').strip()
    if not theirs or not ours or theirs != ours:
        print(f'build_suite_json: {path.name} is for set {theirs or "<none>"!r}, not {ours or "<none>"!r} — its limits are left out', file=sys.stderr)
        return {}
    out: dict[str, Any] = {}
    rows = [r for r in detail.get('complication_enactment') or [] if isinstance(r, dict)]
    if rows:
        out['complication_enactment'] = rows
    if isinstance(detail.get('tool_coverage'), dict):
        out['tool_coverage'] = detail['tool_coverage']
    return out


#: Outcomes that say nothing about whether the user enacted the element: the run errored, no beat enacts it, or the
#: ledger does not carry the beat. A row of only these is never listed as dealt-but-never-enacted.
UNDECIDED = frozenset({'errored', 'unjoinable', 'unconfirmed'})


def dealt_never_enacted(rows: list) -> list[dict]:
    """`[{scenario, complication, outcomes}]` — every backend `complication_enactment` row with a decided outcome
    (:data:`UNDECIDED` aside) and no run that enacted it (a beat whose contingency never arose reads `withheld`)."""
    return [{'scenario': r.get('scenario'), 'complication': r.get('complication'), 'outcomes': r['outcomes']}
            for r in rows if isinstance(r, dict) and isinstance(r.get('outcomes'), dict)
            and set(r['outcomes']) - UNDECIDED and not r['outcomes'].get('enacted')]


# ── per scenario ──────────────────────────────────────────────────────────────
def _neighbours(graph: Optional[dict]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if not isinstance(graph, dict):
        return out
    directed = bool(graph.get('directed'))
    for edge in graph.get('edges') or []:
        if not isinstance(edge, dict) or not _number(edge.get('weight')):
            continue
        a, b = str(edge.get('source_id')), str(edge.get('target_id'))
        out.setdefault(a, {})[b] = float(edge['weight'])
        if not directed:
            out.setdefault(b, {})[a] = float(edge['weight'])
    return out


def walk_path(trace: list[str], neighbours: dict[str, dict[str, float]]) -> list[dict]:
    """The `{from, to, p}` display edges along a walk: each hop's share of its node's edge weight.

    Empty when any hop is not a graph edge — a partial route would read as the whole one."""
    path = []
    for a, b in zip(trace, trace[1:]):
        weights = neighbours.get(a) or {}
        if b not in weights or not sum(weights.values()):
            return []
        path.append({'from': a, 'to': b, 'p': round(weights[b] / sum(weights.values()), 3)})
    return path


def world_state_note(state: Any) -> Optional[str]:
    if not isinstance(state, dict):
        return None
    entities = state.get('entities')
    if not isinstance(entities, dict):
        return None
    if not entities:
        return 'Seeded: an empty world.'
    counts: dict[str, int] = {}
    for value in entities.values():
        kind = str(value.get('_entity_type') or 'record').replace('_', ' ') if isinstance(value, dict) else 'record'
        counts[kind] = counts.get(kind, 0) + 1
    return 'Seeded: ' + ', '.join(f'{n} {kind}' + ('' if n == 1 else '(s)') for kind, n in counts.items()) + '.'


#: The fields of a scenario's compiled goal check (`success_criteria.deterministic`) the report lists.
GOAL_CHECK_KEYS = ('label', 'expression', 'rationale')


def goal_check(body: dict) -> Optional[dict]:
    """The scenario's per-scenario goal check as it came (`scenario.success_criteria.deterministic`), or None.

    It grades this scenario's run beside the eval set and is in no `policies_config.yaml`, so without
    this the eval list never shows it."""
    criteria = body.get('success_criteria')
    check = criteria.get('deterministic') if isinstance(criteria, dict) else None
    if not isinstance(check, dict) or not isinstance(check.get('expression'), str) or not check['expression'].strip():
        return None
    return {k: check[k] for k in GOAL_CHECK_KEYS if isinstance(check.get(k), str) and check[k].strip()}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_text(v) for v in value)


def _say(quiet: bool, message: str) -> None:
    if not quiet:
        print(f'build_suite_json: {message}', file=sys.stderr)


def read_card(slot: Path, *, quiet: bool = False) -> Optional[dict]:
    """The slot's `scenario_card.yaml` as the row's `card`, or None.

    None when there is no card, and when it is not one this builder can show: unparseable, another schema, marked
    `stale` (a later edit moved the scenario it describes), no `title`, or a known field of the wrong type. Display
    data never fails the build, so each of those is a stderr line, never a refusal. Only the known fields are copied."""
    path = slot / CARD_FILE
    if not path.is_file():
        return None
    try:
        card = _read_yaml(path)
    except Refused as exc:
        _say(quiet, f'{exc} — the card is left out')
        return None
    if not isinstance(card, dict) or card.get('schema') != CARD_SCHEMA:
        _say(quiet, f'{path}: not a {CARD_SCHEMA} card — left out')
        return None
    if card.get('stale') is True:
        _say(quiet, f'{path}: marked stale, so it no longer describes its scenario — left out')
        return None
    bad = [k for k in CARD_TEXT_FIELDS + CARD_FIXED_FIELDS if k in card and not isinstance(card[k], str) and not (k in CARD_NULLABLE and card[k] is None)]
    bad += [k for k in CARD_LIST_FIELDS if k in card and not (card[k] == [] or _texts(card[k]))]
    if bad or not _text(card.get('title')):
        _say(quiet, f'{path}: ' + (f'field(s) {bad} are not text' if bad else 'no title') + ' — the card is left out')
        return None
    return {k: card[k] for k in (*CARD_TEXT_FIELDS, *CARD_LIST_FIELDS, *CARD_FIXED_FIELDS) if k in card}


def override_card(name: str, card: Optional[dict], override: Any, *, quiet: bool = False) -> Optional[dict]:
    """The card with the overlay's `card` rewrites applied: text fields and the behavior list only.

    The remedy for a card string the publish guard flagged. Anything else is left out with a stderr line (never a
    refusal): a key outside :data:`CARD_TEXT_FIELDS`/:data:`CARD_LIST_FIELDS`, a value of the wrong type, and an
    override for a scenario with no card (the overlay's `title`/`summary` are that scenario's remedy)."""
    if override is None:
        return card
    if not isinstance(override, dict) or card is None:
        _say(quiet, f'overlay `card` for {name!r} ' + ('is not an object' if card is not None else 'has no scenario card to rewrite') + ' — ignored')
        return card
    out = dict(card)
    for key, value in override.items():
        if (key in CARD_TEXT_FIELDS and _text(value)) or (key in CARD_LIST_FIELDS and _texts(value)):
            out[key] = value
        else:
            _say(quiet, f'overlay `card.{key}` for {name!r} ignored: only the text fields {list(CARD_TEXT_FIELDS + CARD_LIST_FIELDS)} are rewritten, '
                        'each as non-empty text')
    return out


def scenario_card(art: Path, name: str, authored: dict, *, quiet: bool = False) -> Optional[dict]:
    """The scenario's card as the row carries it: the slot's card, then the overlay's rewrites."""
    return override_card(name, read_card(art / 'scenarios' / name, quiet=quiet), authored.get('card'), quiet=quiet)


def row_text(authored: dict, card: Optional[dict]) -> dict:
    """The row's `title`/`summary`: the overlay's, else the card's `title`/`details`, else absent."""
    out = {}
    for key, fallback in CARD_ROW_FALLBACK:
        value = authored.get(key) if _text(authored.get(key)) else (card or {}).get(fallback)
        if _text(value):
            out[key] = value
    return out


def single_hop_text(workdir: Path, art: Path) -> dict[str, dict]:
    """``{scenario: {title, summary}}`` for each regression the scored run played single-hop (``scored_findings``'
    reading of its ``run_summary.json``): only its recorded turn was replayed and graded, so the authored card, written
    for the whole recorded conversation, does not describe it. The title is the folder's ROLES.md issue heading; the
    summary, the turn the slot opens on."""
    if str(_ROOT_SCRIPTS) not in sys.path:
        sys.path.append(str(_ROOT_SCRIPTS))
    import regressions  # noqa: PLC0415 - sibling, stdlib only: the regressions folder's name
    import scored_findings  # noqa: PLC0415 - sibling, stdlib only: the scored run and its single-hop scenarios

    run_dir = scored_findings.scored_run_dir(workdir)
    out = {}
    for name in sorted(scored_findings._single_hop_scenarios(run_dir)) if run_dir is not None else []:  # noqa: SLF001
        try:
            roles = (workdir / regressions.REGRESSIONS_DIRNAME / name / regressions.ROLES_FILENAME).read_text(encoding='utf-8')
            said = ' '.join((art / 'scenarios' / name / 'first_talk.txt').read_text(encoding='utf-8').split())
        except OSError:
            continue
        title = next((line[len('## Issue:'):].strip() for line in roles.splitlines() if line.startswith('## Issue:')), '')
        if title and said:
            said = said if len(said) <= 200 else said[:200].rstrip() + ' …'
            out[name] = {'title': title, 'summary': f'A replay of one recorded turn: the user says “{said}”, and only the agent\'s reply to it is graded.'}
    return out


def untitled(art: Path, names: list[str], authored: dict, *, quiet: bool = True) -> list[str]:
    """The scenarios that would carry no `title` or no `summary`: neither the overlay nor a usable card gives one.

    The final build refuses these (exit 3); `artifact_deps.suite_command` builds a draft while any remain."""
    return _untitled(names, authored, {n: scenario_card(art, n, authored.get(n) or {}, quiet=quiet) for n in names})


def _untitled(names: list[str], authored: dict, cards: dict) -> list[str]:
    return [n for n in names if len(row_text(authored.get(n) or {}, cards.get(n))) < len(CARD_ROW_FALLBACK)]


def scenario_row(name: str, summary: dict, slot: Path, source: str, neighbours: dict, authored: dict, card: Optional[dict] = None,
                 single_hop: bool = False) -> dict:
    scenario_yaml = _read_yaml(slot / 'scenario.yaml')
    body = scenario_yaml.get('scenario') if isinstance(scenario_yaml, dict) and isinstance(scenario_yaml.get('scenario'), dict) else {}

    def pick(key: str) -> Any:
        return summary.get(key) if summary.get(key) is not None else body.get(key)

    row: dict[str, Any] = {'id': name, **row_text(authored, card), **({'card': card} if card else {}), 'source': source}
    level = pick('complexity_level')
    stars = level if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 5 else None
    probability, bits = pick('path_probability'), pick('surprisal_bits')
    if _number(probability) and _number(bits):
        row['likelihood'] = round(probability * 100, 2)
        row['complexity'] = round(bits, 2)
        row['complexity_unit'] = 'bits'
        path = walk_path(_ids(pick('walk_trace')), neighbours)
        if path:
            row['path'] = path
    elif stars is not None:
        row['complexity'] = stars  # no recorded walk (a bundle row): the level, unit-less
    if stars is not None:
        row['stars'] = stars
    persona = _read_yaml(slot / 'persona.yaml')
    if isinstance(persona, (str, dict)) and persona:
        row['persona'] = persona
    # The slot's own scenario.yaml first: a ground-truth edit re-pulls it, while the set's meta keeps the old target.
    target = body.get('scenario_target') if _text(body.get('scenario_target')) else summary.get('scenario_target')
    if single_hop:
        row['single_hop'] = True  # a replay of one recorded turn: its goal is never graded, so it carries no ground truth
    elif isinstance(target, str) and target.strip():
        row['ground_truth'] = target
    check = goal_check(body)
    if check:
        row['goal_check'] = check
    note = world_state_note(_read_yaml(slot / 'initial_world_state.yaml'))
    if note:
        row['world_state_note'] = note
    return row


# ── evals ─────────────────────────────────────────────────────────────────────
def _score(policy: dict) -> Optional[str]:
    metric = policy.get('metric_type')
    if metric == 'boolean':
        return 'pass / fail'
    if metric == 'categorical':
        labels = _ids(policy.get('labels'))
        return ' / '.join(labels) if labels else None
    if metric in ('int', 'float') and _number(policy.get('violation_threshold')):
        return f"threshold {policy['violation_threshold']:g}"
    return None


def eval_kind(key: str, policy: dict) -> Optional[str]:
    """The eval's type label (`EVAL_KINDS`), or None — with a stderr line unless the type is in `UNLABELLED_TYPES`."""
    kind = str(policy.get('type') or '')
    if kind in EVAL_KINDS:
        return EVAL_KINDS[kind]
    if kind in UNLABELLED_TYPES:
        return None
    if kind == 'beat_resolution':
        params = policy.get('additional_params') if isinstance(policy.get('additional_params'), dict) else {}
        metric = params.get('metric')
        if metric in BEAT_KINDS:
            return BEAT_KINDS[metric]
        if metric is None and params.get('beat_id'):
            return 'llm_judge'
        print(f'build_suite_json: eval {key!r} is a beat_resolution slice with metric={metric!r} — no type label', file=sys.stderr)
        return None
    print(f'build_suite_json: eval {key!r} has type {kind or "<none>"!r}, which carries no known type label — no type label', file=sys.stderr)
    return None


def _guard() -> Any:
    if str(_ROOT_SCRIPTS) not in sys.path:
        sys.path.append(str(_ROOT_SCRIPTS))
    import customer_text_guard  # noqa: PLC0415 - sibling, stdlib only: the one reading of "may the customer read this text"

    return customer_text_guard


def guarded_line(key: str, field: str, policy: dict, guard: Any, vocabulary: dict) -> Optional[str]:
    """The policy's backend-written `field` on one line, or None. A line the customer-text guard flags is withheld
    with a stderr line naming the term — the row ships without it rather than the page refusing to publish."""
    raw = policy.get(field)
    if raw is None:
        return None
    text = ' '.join(raw.split()) if isinstance(raw, str) else ''
    if not text:
        print(f'build_suite_json: eval {key!r} carries a {field} that is not a non-empty string — left out', file=sys.stderr)
        return None
    leaks = guard.text_violations(PAGE_KIND, text, vocabulary, rel=f'{field}:{key}', reader='markdown')
    if leaks:
        terms = ', '.join(sorted({leak.term for leak in leaks}))
        print(f'build_suite_json: eval {key!r} {field} carries internal text ({terms}) — left out of the row', file=sys.stderr)
        return None
    return text


def prompt_summary(key: str, policy: dict, guard: Any, vocabulary: dict) -> Optional[str]:
    """The policy's `prompt_summary` on one line, or None (:func:`guarded_line`)."""
    return guarded_line(key, PROMPT_SUMMARY, policy, guard, vocabulary)


def eval_rule(key: str, policy: dict, guard: Any, vocabulary: dict) -> Optional[str]:
    """The policy's `rule` on one line, or None: guard-checked like the summary, and left out past
    :data:`RULE_MAX_CHARS` with a stderr line."""
    text = guarded_line(key, RULE, policy, guard, vocabulary)
    if text is not None and len(text) > RULE_MAX_CHARS:
        print(f'build_suite_json: eval {key!r} {RULE} runs {len(text)} characters, past {RULE_MAX_CHARS} — left out of the row', file=sys.stderr)
        return None
    return text


def eval_group(key: str, policy: dict) -> Optional[str]:
    """The policy's `group` when it is one of :data:`EVAL_GROUPS`, else None — with a stderr line when one was written."""
    raw = policy.get(GROUP)
    if raw is None:
        return None
    if isinstance(raw, str) and raw in EVAL_GROUPS:
        return raw
    print(f'build_suite_json: eval {key!r} carries {GROUP} {raw!r}, not one of {sorted(EVAL_GROUPS)} — left out of the row', file=sys.stderr)
    return None


def eval_rows(policies: dict) -> list[dict]:
    guard = _guard()
    vocabulary = guard.load()
    rows = []
    for key, policy in policies.items():
        policy = policy if isinstance(policy, dict) else {}
        kind = str(policy.get('type') or '')
        row: dict[str, Any] = {'id': str(key), 'type': EVAL_TYPES.get(kind, kind or 'unknown')}
        label = eval_kind(str(key), policy)
        if label:
            row['kind'] = label
        scope = policy.get('evaluation_scope')
        if scope:
            row['scope'] = EVAL_SCOPES.get(str(scope), str(scope))
        score = _score(policy)
        if score:
            row['score'] = score
        summary = prompt_summary(str(key), policy, guard, vocabulary)
        if summary:
            row[PROMPT_SUMMARY] = summary
        rule = eval_rule(str(key), policy, guard, vocabulary)
        if rule:
            row[RULE] = rule
        group = eval_group(str(key), policy)
        if group:
            row[GROUP] = group
        rows.append(row)
    return rows


def graders(policies: dict) -> list[str]:
    """The policies that grade every planted question: a `beat_resolution` metric, and `retrieval_metric`."""
    out = []
    for key, policy in policies.items():
        if not isinstance(policy, dict):
            continue
        params = policy.get('additional_params') if isinstance(policy.get('additional_params'), dict) else {}
        if policy.get('type') == 'retrieval_metric' or (policy.get('type') == 'beat_resolution' and params.get('metric')):
            out.append(str(key))
    return out


# ── environment ───────────────────────────────────────────────────────────────
def _note(spec: dict) -> str:
    text = ' '.join(str(spec.get('description') or '').split())
    return text if len(text) <= NOTE_CHARS else text[: NOTE_CHARS - 1].rstrip() + '…'


def _fields(specs: Any) -> list[dict]:
    if not isinstance(specs, dict):
        return []
    out = []
    for name, spec in specs.items():
        spec = spec if isinstance(spec, dict) else {'type': spec}
        out.append({'name': str(name), 'type': str(spec.get('type') or ''), 'note': _note(spec)})
    return out


def knowledge_missing(art: Path) -> Optional[dict]:
    """`{reader_missing}` when the map says the agent answers from a knowledge base (`grounding.needs_corpus`) and the set
    carries none, else None: the page then says so in plain words. `reader_missing` is the hand-off's own reading
    (`simulate_plan._extractor_error`): the reader's install failed AND no corpus build ran, so a build that ran and failed on
    the backend is never blamed on the install. Advisory: an unreadable map is no flag, never a refused build."""
    try:
        topology = _read_json(art / 'agent_topology.json') if (art / 'agent_topology.json').is_file() else None
    except Refused:
        return None
    grounding = topology.get('grounding') if isinstance(topology, dict) else None
    if not isinstance(grounding, dict) or grounding.get('needs_corpus') is not True:
        return None
    if (scripts := str(Path(__file__).resolve().parents[3] / 'scripts')) not in sys.path:
        sys.path.append(scripts)
    import simulate_plan  # noqa: PLC0415 - the one reading of "the reader could not be installed"

    return {'reader_missing': simulate_plan._extractor_error(art.parent) is not None}  # noqa: SLF001


def environment(workdir: Path) -> Optional[dict]:
    """`{schema: [{entity, cardinality, fields: [{name, type, note}]}]}` from the state schema, then the config's entity types."""
    state = _read_yaml(workdir / 'scenario_state_schema.yaml')
    config = _read_yaml(workdir / 'plurai.config.yaml')
    sources = [
        (state or {}).get('entities') if isinstance(state, dict) else None,
        ((config or {}).get('world_state') or {}).get('entity_types') if isinstance(config, dict) else None,
    ]
    schema: list[dict] = []
    seen: set[str] = set()
    for entities in sources:
        if not isinstance(entities, dict):
            continue
        for name, spec in entities.items():
            if str(name) in seen or not isinstance(spec, dict):
                continue
            seen.add(str(name))
            fields = _fields(spec.get('field_specs')) or _fields(spec.get('fields')) or [
                {'name': str(f), 'type': '', 'note': ''} for f in _ids(spec.get('required_fields'))]
            schema.append({'entity': str(name), 'cardinality': str(spec.get('cardinality') or ''), 'fields': fields})
    return {'schema': schema, 'bounds': [], 'noise': []} if schema else None


# ── assembly ──────────────────────────────────────────────────────────────────
def _bundle_sources(bundle: dict) -> dict[str, str]:
    out = {}
    for key, source in BUNDLE_SOURCES:
        for folder in _ids(bundle.get(key)):
            out[folder] = source
    return out


def _held_back(bundle: dict) -> list[dict]:
    out = []
    for entry in bundle.get('held_back') or []:
        if isinstance(entry, dict) and str(entry.get('folder') or '').strip():
            out.append({k: entry[k] for k in ('folder', 'reason') if entry.get(k) is not None})
    return out


def _draft_sources(art: Path, names: list) -> dict[str, str]:
    """A draft's row sources: `$ART/final_bundle.json`'s labels, only when every folder it names is in this set.

    A bundle from another set would mislabel rows, so it is ignored; its held-back list stays final-only."""
    try:
        bundle = _read_json(art / 'final_bundle.json') if (art / 'final_bundle.json').is_file() else None
    except Refused:
        bundle = None  # a draft never fails on a file it only borrows labels from
    sources = _bundle_sources(bundle) if isinstance(bundle, dict) else {}
    return sources if sources and set(sources) <= set(names) else {}


def _draft_policies(policies_path: Optional[Path]) -> dict:
    """A draft's policies: none yet is `{}`; a file that exists must still be a policy map."""
    policies = _read_yaml(policies_path) if policies_path else None
    if policies is None:
        return {}
    if not isinstance(policies, dict):
        raise Refused(f'{policies_path}: not a policies_config.yaml (a map keyed by policy name)')
    return policies


def build(art: Path, policies_path: Optional[Path], overlay_path: Optional[Path], bundle_path: Optional[Path], regression_paths: list[Path], workdir: Path,
          meta_path: Optional[Path] = None, draft: bool = False, diagnostics_path: Optional[Path] = None) -> dict:
    if draft and (bundle_path or regression_paths):
        raise Refused('--bundle and --regressions are final-only inputs; a draft is built without them')
    if not draft and (policies_path is None or overlay_path is None):
        raise Refused('--policies and --overlay are required unless --draft')
    overlay = read_overlay(overlay_path) if overlay_path else {}
    detail = read_meta(meta_path or art / 'final_set_meta.json')
    if draft:
        policies = _draft_policies(policies_path)
    else:
        policies = _read_yaml(policies_path)
        if not isinstance(policies, dict) or not policies:
            raise Refused(f'{policies_path}: not a policies_config.yaml (a map keyed by policy name)')
    bundle = _read_json(bundle_path) if bundle_path else {}
    if not isinstance(bundle, dict):
        raise Refused(f'{bundle_path}: final_bundle.json is a JSON object')
    graph = _read_yaml(art / 'scenarios' / 'dimension_graph.yaml')
    dataset = detail.get('dataset_metadata') if isinstance(detail.get('dataset_metadata'), dict) else {}

    names = scenario_names(detail)
    authored = overlay.get('scenarios') or {}
    unknown = sorted(set(authored) - set(names))
    if unknown:
        raise Refused(f'{overlay_path}: overlay names scenario(s) {unknown} that the set does not carry; ids are the set\'s scenario names')
    # A regression the scored run played single-hop is described by its one replayed turn, never by its authored card.
    hops = single_hop_text(workdir, art)
    authored = {**authored, **{n: {**(authored.get(n) or {}), **text} for n, text in hops.items() if n in names}}
    cards = {n: None if n in hops else scenario_card(art, n, authored.get(n) or {}) for n in names}
    unauthored = _untitled(names, authored, cards)
    if unauthored and not draft:
        raise Refused('scenario(s) with no title or summary from either the overlay or a scenario card: ' + ', '.join(unauthored), 3)

    summaries = {str(r.get('name')): r for r in detail.get('scenarios') or [] if isinstance(r, dict)}
    sources = _bundle_sources(bundle) if not draft else _draft_sources(art, names)
    neighbours = _neighbours(graph)
    scenarios = [scenario_row(n, summaries.get(n) or {}, art / 'scenarios' / n, sources.get(n, 'spec'), neighbours, authored.get(n) or {}, cards[n],
                              single_hop=n in hops) for n in names]
    missing_slots = [n for n in names if not (art / 'scenarios' / n).is_dir()]
    if missing_slots:
        print(f'build_suite_json: no fetched slot folder for {missing_slots} — persona and seeded count left out; run the slot fetch first', file=sys.stderr)

    suite: dict[str, Any] = {'schema': SCHEMA, **({'draft': True} if draft else {})}
    if detail.get('scenario_set_id'):
        suite['scenario_set_id'] = str(detail['scenario_set_id'])
    eval_set = workdir / '.eval_set_id'
    if eval_set.is_file() and eval_set.read_text(encoding='utf-8').strip():
        suite['eval_set_id'] = eval_set.read_text(encoding='utf-8').strip()
    requested = overlay.get('requested_count')
    if requested is not None and not draft:  # the shortfall is the final's: a draft set is not the shipped count
        suite['requested_count'] = requested
        suite['short_by'] = max(0, requested - sum(1 for s in scenarios if s['source'] == 'spec'))
    # The run's coverage record (a draft has no run): the enactment table, and the tool block the run completed.
    limits = {} if draft else run_limits(diagnostics_path or art / 'suite_diagnostics_final.json', detail.get('scenario_set_id'),
                                         explicit=diagnostics_path is not None)
    coverage = coverage_block(detail, graph, limits.get('tool_coverage'))
    if coverage is not None:
        if 'tool_coverage' in limits:
            coverage['tools'] = limits['tool_coverage']
        suite['coverage'] = coverage
    if limits.get('complication_enactment'):
        suite['complication_enactment'] = limits['complication_enactment']
        if never := dealt_never_enacted(limits['complication_enactment']):
            suite['dealt_never_enacted'] = never
    suite['scenarios'] = scenarios
    suite['evals'] = eval_rows(policies)

    gaps = dataset.get('capability_gaps')
    if isinstance(gaps, list) and gaps:
        suite['capability_gaps'] = gaps
    for path in regression_paths:
        found = _read_json(path, empty={})
        if not isinstance(found, dict):
            raise Refused(f'{path}: a regressions file is the JSON object its command printed')
        for key in REGRESSION_KEYS:
            if isinstance(found.get(key), list) and found[key]:
                suite[key] = found[key]
    held = _held_back(bundle)
    if held:
        suite['held_back_trace_scenarios'] = held

    reach = dataset.get('knowledge_reach')
    maps = overlay.get('knowledge') or {}
    if _ids(detail.get('corpus_ids')) or isinstance(reach, dict) or maps:
        knowledge: dict[str, Any] = {k: maps[k] for k in ('per_document', 'set_aside_documents') if isinstance(maps.get(k), dict)}
        by_doc = reach.get('by_document') if isinstance(reach, dict) and isinstance(reach.get('by_document'), dict) else {}
        if 'set_aside_documents' not in knowledge and (found := set_aside_documents(art, _ids(detail.get('corpus_ids')), set(knowledge.get('per_document') or {}) | set(by_doc))) is not None:
            knowledge['set_aside_documents'] = found
        if isinstance(reach, dict):
            knowledge['knowledge_reach'] = reach
        knowledge['graders'] = graders(policies)
        suite['knowledge'] = knowledge
    elif (missing := knowledge_missing(art)) is not None:
        suite['knowledge_missing'] = missing

    env = environment(workdir)
    if env:
        suite['environment'] = env
    bundle_folders = set(_ids(dataset.get('bundle_folders')))
    if names and bundle_folders and set(names) <= bundle_folders:
        suite['dimension_graph'] = {'absent': 'bundle_built'}
    return suite


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--art', required=True, type=Path, help='the artifacts dir ($ART); suite.json is written here')
    parser.add_argument('--policies', type=Path, help="the workdir's policies_config.yaml (required unless --draft)")
    parser.add_argument('--overlay', type=Path, help='the authored overlay JSON (title, summary and card rewrites, requested_count, knowledge maps); required unless --draft')
    parser.add_argument('--meta', type=Path, help="the set's get_artifact_meta answer (default: $ART/final_set_meta.json)")
    parser.add_argument('--draft', action='store_true', help='build the in-progress suite: overlay and policies optional, `draft: true`, never exit 3')
    parser.add_argument('--bundle', type=Path, help='$ART/final_bundle.json, when the final batch carried one')
    parser.add_argument('--regressions', type=Path, action='append', default=[],
                        help='the JSON stdout of final_bundle.py missing / target-graded, or of regressions.py ledger-owners --declined')
    parser.add_argument('--workdir', type=Path, help="the workdir (default: --art's parent)")
    parser.add_argument('--diagnostics', type=Path,
                        help="the set's run suite_diagnostics job envelope (default: $ART/suite_diagnostics_final.json when present)")
    args = parser.parse_args(argv)
    try:
        suite = build(args.art, args.policies, args.overlay, args.bundle, args.regressions, args.workdir or args.art.parent, args.meta, args.draft,
                      args.diagnostics)
    except Refused as exc:
        stream = sys.stdout if exc.code == 3 else sys.stderr
        print(f'build_suite_json: {exc}', file=stream)
        return exc.code
    write_atomic(args.art / 'suite.json', suite)
    print(f"wrote {args.art / 'suite.json'} — {len(suite['scenarios'])} scenarios, {len(suite['evals'])} evals" + (' (draft)' if args.draft else ''))
    return 0


if __name__ == '__main__':
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'scripts'))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
