#!/usr/bin/env python3
"""Which complications a rebuild dropped, and which of those had found a confirmed defect; and, before a re-scan writes
its vocabulary, which ones to carry into it.

    vocabulary_loss.py --previous <old plurai_sim> --workdir <new plurai_sim> --out <dir>   (operator-only, offline)
    vocabulary_loss.py carry --workdir <plurai_sim>                                        (the scan, before it writes)

The compare writes ``<dir>/vocabulary_loss.json`` and a short ``<dir>/vocabulary_loss.md``, for the operator and the
fleet review; no hook or stage runs it.

``carry`` runs at extract-scenario-variables Step 4, while the workdir still holds the previous build's policies and
scenarios. It reads what the scan arm saved before its prune, only when stamped at the current hand-off count: the
confirmed findings (``artifacts/previous_findings.json``) and the vocabulary (``artifacts/previous_scenario_variables.yaml``,
so a re-run after the scan rewrote ``scenario_variables.yaml`` gives the same carry), and the links of the runs of
that build's scenario set only (``runs/`` is never pruned). It rewrites ``artifacts/vocabulary_carry.json``: at most
:data:`CARRY_CAP` names of the previous ``typical_complications`` that are tier A to a confirmed finding (ranked by the
finding's severity, then the conversations linked, then the name), each entry verbatim with the findings it set up;
the rest of tier A goes to ``not_carried`` (operator-only). It deletes the scan's ``artifacts/vocabulary_retired.json``
first, so a retirement is always this scan's. Any failure leaves no carry file. Nothing customer-facing reads either file.

What it compares: the ``typical_complications`` names in the two ``scenario_variables.yaml`` files, by exact string. A
name that is gone may have been renamed, so every line says "lost or renamed" and claims nothing more.

Two tiers, from typed records only (no prose, no similarity):

* **Tier A, lost with a confirmed finding.** A confirmed finding of the previous build (``agent_findings.jsonl``, status
  ``confirmed``; a scan finding only once a scored run's attribution linked it) was attributed on an item whose
  ``current_elements`` dealt the lost name, linked by, in order:

  - ``element``: the attribution reader named it as the complication that set up the finding on that scenario
    (``finding_attribution.json`` ``elements``). A reader's answer for a (finding, scenario), ``none`` included,
    replaces the policy rule on that scenario;
  - ``policy``: a policy that declares it (``target_complications``, or a ``current_elements`` guard);
  - ``trigger``: the finding's ``trigger:`` names it as an exact id token.

  The item and policy are the PAIRS the run's ``finding_attribution.json`` links record; a finding with no links pairs
  its subject policies with its scenarios. A finding with no policy at all links every name its scenarios dealt, marked
  ``link: scenario``.
* **Tier B, lost or renamed.** Every other lost name. When a finding's policy declares no complication, the lost names
  its scenarios dealt carry ``seen_in_finding_scenarios``: the coarse join, marked as such and never promoted to tier A.
  A name the new build retired (``artifacts/vocabulary_retired.json``) is listed with its why.

Families are compared only as labels: each lost line carries the ``families`` the previous build gave it, and the
previous PRD's family lines are kept verbatim so a reader can see what a label meant then.

An empty or missing baseline reports ``status: no_baseline`` with a reason, never empty lists.

Needs PyYAML and runs on Python 3.9. Without PyYAML it exits 2 and writes nothing, rather than report zero losses.
Exit codes: 0 written (``compared`` or ``no_baseline``); 2 an input unreadable or PyYAML missing (nothing written).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(1, str(_HERE.parent / 'skills' / 'simulation-setup' / 'scripts'))

SCHEMA = 'plurai-vocabulary-loss/1'
JSON_NAME = 'vocabulary_loss.json'
MD_NAME = 'vocabulary_loss.md'
CARRY_SCHEMA = 'plurai-vocabulary-carry/1'
CARRY_NAME = 'vocabulary_carry.json'
RETIRED_NAME = 'vocabulary_retired.json'
#: At most this many classes are carried: a third of the 8-12 pool, so the carry never freezes the vocabulary.
CARRY_CAP = 4
_SEVERITY_ORDER = ('critical', 'high', 'medium', 'low')
_LINK_ORDER = ('element', 'policy', 'trigger', 'scenario')


class Unreadable(Exception):
    """An input the comparison depends on could not be read; nothing is written."""


def _require_yaml() -> Any:
    try:
        import yaml  # noqa: PLC0415 - checked first, so a missing PyYAML is a clear refusal, not zero losses
    except ImportError:
        print(
            f'ERROR: vocabulary_loss needs PyYAML, which {sys.executable} cannot import. Nothing was compared. '
            'Run it under an interpreter that has PyYAML (for example `uv run --with pyyaml python3 scripts/vocabulary_loss.py ...`).',
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return yaml


# --- reading -------------------------------------------------------------------------------------


def _load_yaml(yaml: Any, path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise Unreadable(f'{path} is unreadable ({type(exc).__name__}: {exc})') from exc


def _complications(doc: Any) -> list[dict[str, Any]]:
    entries = doc.get('typical_complications') if isinstance(doc, dict) else None
    return [e for e in entries or [] if isinstance(e, dict) and e.get('name')]


def _family_lines(prd: Path) -> list[str]:
    """The PRD's family lines, verbatim: the only record of what a family label meant in that build."""
    try:
        text = prd.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    return [line.strip() for line in text.splitlines() if line.lstrip().startswith(('- Families', '- Family'))]


def _stage(row: dict[str, Any]) -> str:
    return str(row.get('found_during') or '').split(' ', 1)[0]


def _item(raw: Any) -> str:
    return str(raw).split('/trial_', 1)[0]


Pair = Tuple[str, Optional[str], str]  # (scenario item, policy, the conversation it was linked in)


def _links(artifacts: Path, runs: Collection[str] | None = None) -> tuple[dict[str, list[Pair]], dict[tuple[str, str], str]]:
    """``({finding id: [(item, policy, conversation), ...]}, {(finding id, item): element or "none"})`` from each run's
    ``finding_attribution.json``: every run, or only the ``runs`` named."""
    out: dict[str, list[Pair]] = {}
    answered: dict[tuple[str, str], str] = {}
    for path in sorted(artifacts.glob('runs/*/finding_attribution.json')):
        if runs is not None and path.parent.name not in runs:
            continue
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise Unreadable(f'{path} is unreadable ({type(exc).__name__}: {exc})') from exc
        for link in doc.get('links') or [] if isinstance(doc, dict) else []:
            if isinstance(link, dict) and link.get('to') and link.get('item'):
                out.setdefault(str(link['to']), []).append((_item(link['item']), str(link['policy']) if link.get('policy') else None, str(link['item'])))
        for said in doc.get('elements') or [] if isinstance(doc, dict) else []:
            if isinstance(said, dict) and said.get('finding') and said.get('scenario') and said.get('element'):
                answered[(str(said['finding']), _item(said['scenario']))] = str(said['element'])
    return out, answered


def _tier_a(rows: dict[str, dict], names: set[str], links: dict[str, list[Pair]], answered: dict[tuple[str, str], str],
            policies: dict[str, Any], elements: _Elements, triggers: dict[str, str]) -> tuple[dict, dict, dict[str, set[str]], int]:
    """``(tier_a, seen, conversations, confirmed read)`` over ``names``: ``tier_a`` {name: {finding id: entry}}, ``seen``
    {name: {(item, finding id): policies}} (the coarse join), ``conversations`` {name: the conversations its links hold}."""
    import coverage_closure  # noqa: PLC0415 - after the PyYAML check
    import scored_findings  # noqa: PLC0415 - the one reader of a `trigger:`'s id tokens

    tier_a: dict[str, dict[str, dict[str, Any]]] = {}
    seen: dict[str, dict[tuple[str, str], set[str]]] = {}
    conversations: dict[str, set[str]] = {}
    confirmed = 0
    for row in rows.values():
        fid = str(row['id'])
        if row.get('status') != 'confirmed':
            continue
        if _stage(row) == 'scan' and fid not in links and not any(f == fid for f, _ in answered):
            continue  # a scan finding counts only once a scored run's attribution linked it: never by its scenarios alone
        confirmed += 1
        pairs: list[Pair] = list(links.get(fid, []))
        if not pairs and _stage(row) != 'scan':
            subject_policies = sorted({str(s).split(':', 1)[1] for s in row.get('subjects') or [] if str(s).startswith('policy:')})
            raw = sorted({str(i) for i in [*((row.get('source') or {}).get('scenarios') or []), *((row.get('scored_run') or {}).get('items') or [])]})
            pairs = [(_item(i), p, i) for i in raw for p in subject_policies] if subject_policies else [(_item(i), None, i) for i in raw]
        pairs += [(item, None, item) for (f, item) in answered if f == fid and item not in {p[0] for p in pairs}]
        named = scored_findings._trigger_ids(triggers.get(fid) or '')  # noqa: SLF001

        def hit(name: str, kind: str, pair: dict[str, Any], conversation: str, fid: str = fid, row: dict = row) -> None:
            entry = tier_a.setdefault(name, {}).setdefault(fid, {'id': fid, 'severity': row.get('severity'), 'link': kind, 'pairs': []})
            if _LINK_ORDER.index(kind) < _LINK_ORDER.index(entry['link']):
                entry['link'] = kind
            if pair not in entry['pairs']:
                entry['pairs'].append(pair)
            conversations.setdefault(name, set()).add(conversation)

        for item, policy, conversation in pairs:
            if item.startswith('regression_'):
                continue  # a replay deals no complication of its own
            dealt = elements(item)
            if not dealt:
                continue
            said = answered.get((fid, item))
            if said is not None:  # the reader's typed answer on this scenario replaces the policy rule there
                if said in dealt & names:
                    hit(said, 'element', {'item': item, 'element': said}, conversation)
                continue
            if policy is None:
                for name in dealt & names:
                    hit(name, 'scenario', {'item': item, 'policy': None}, conversation)
                continue
            declared = coverage_closure.declared_complications({policy: policies.get(policy)})
            by_policy = dealt & declared & names
            by_trigger = (dealt & named & names) - by_policy
            for name in by_policy:
                hit(name, 'policy', {'item': item, 'policy': policy}, conversation)
            for name in by_trigger:
                hit(name, 'trigger', {'item': item, 'policy': policy}, conversation)
            if not declared:
                for name in (dealt & names) - by_trigger:
                    seen.setdefault(name, {}).setdefault((item, fid), set()).add(policy)
    return tier_a, seen, conversations, confirmed


def _policies(yaml: Any, workdir: Path, notes: list[str]) -> dict[str, Any]:
    path = workdir / 'policies_config.yaml'
    if not path.is_file():
        notes.append('the previous build has no policies_config.yaml, so no policy link can be read')
        return {}
    loaded = _load_yaml(yaml, path)
    if not isinstance(loaded, dict):
        raise Unreadable(f'{path} is not a mapping of policies')
    return loaded


def _retired(workdir: Path) -> list[dict[str, str]]:
    """The scan's ``artifacts/vocabulary_retired.json`` entries with a name and a why."""
    try:
        doc = json.loads((workdir / 'artifacts' / RETIRED_NAME).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    entries = doc.get('retired') if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return []
    return [{'name': str(e['name']).strip(), 'why': ' '.join(str(e['why']).split())} for e in entries
            if isinstance(e, dict) and str(e.get('name') or '').strip() and str(e.get('why') or '').strip()]


class _Elements:
    """``current_elements`` of a scenario item, read once: the current set first, then the archived sets."""

    def __init__(self, yaml: Any, artifacts: Path) -> None:
        self._yaml = yaml
        self._artifacts = artifacts
        self._cache: dict[str, set[str] | None] = {}
        self.unresolved: set[str] = set()

    def __call__(self, item: str) -> set[str] | None:
        if item not in self._cache:
            self._cache[item] = self._read(item)
            if self._cache[item] is None:
                self.unresolved.add(item)
        return self._cache[item]

    def _read(self, item: str) -> set[str] | None:
        candidates = [self._artifacts / 'scenarios' / item / 'scenario.yaml', *sorted(self._artifacts.glob(f'history/*/scenarios/{item}/scenario.yaml'))]
        for path in candidates:
            if path.is_file():
                doc = _load_yaml(self._yaml, path)
                scenario = doc.get('scenario') if isinstance(doc, dict) and isinstance(doc.get('scenario'), dict) else doc
                elements = scenario.get('current_elements') if isinstance(scenario, dict) else None
                return {str(e) for e in elements or []}
        return None


# --- the comparison ------------------------------------------------------------------------------


def compare(previous: Path, workdir: Path) -> dict[str, Any]:
    yaml = _require_yaml()
    import record_finding  # noqa: PLC0415 - after the PyYAML check
    import regressions  # noqa: PLC0415 - THE findings-file parser
    import rescan_diff  # noqa: PLC0415 - exits on a missing PyYAML at import, hence after the check

    base = {'schema': SCHEMA, 'previous': str(previous), 'workdir': str(workdir), 'compared_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}

    new_vars = workdir / 'scenario_variables.yaml'
    if not new_vars.is_file():
        raise Unreadable(f'{new_vars} does not exist: the new build has no vocabulary to compare')
    new_doc = _load_yaml(yaml, new_vars)

    old_vars = previous / 'scenario_variables.yaml'
    if not old_vars.is_file():
        return {**base, 'status': 'no_baseline', 'reason': f'{old_vars} does not exist'}
    old_doc = _load_yaml(yaml, old_vars)
    old_entries = _complications(old_doc)
    if not old_entries:
        return {**base, 'status': 'no_baseline', 'reason': f'{old_vars} declares no typical_complications'}

    new_list = new_doc.get('typical_complications') if isinstance(new_doc, dict) else None
    gone = rescan_diff._names(old_doc.get('typical_complications')) - rescan_diff._names(new_list)
    lost = [e for e in old_entries if str(e['name']) in gone]
    lost_names = {str(e['name']) for e in lost}

    artifacts = previous / 'artifacts'
    notes: list[str] = []
    store = artifacts / 'agent_findings.jsonl'
    if not store.is_file():
        notes.append('the previous build has no agent_findings.jsonl, so no loss can be tied to a finding')
    try:
        rows = record_finding.read_rows(store)
    except (OSError, ValueError) as exc:
        raise Unreadable(f'{store} is unreadable ({exc})') from exc
    policies = _policies(yaml, previous, notes)
    md = artifacts / 'agent_findings.md'
    try:
        triggers = {f.finding_id: f.trigger for f in regressions.parse_findings(md.read_text(encoding='utf-8') if md.is_file() else '')
                    if f.finding_id and f.trigger}
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable(f'{md} is unreadable ({exc})') from exc
    links, answered = _links(artifacts)
    elements = _Elements(yaml, artifacts)
    tier_a, seen, _conversations, confirmed = _tier_a(rows, lost_names, links, answered, policies, elements, triggers)
    retired = {e['name']: e['why'] for e in _retired(workdir)}

    def _line(entry: dict[str, Any]) -> dict[str, Any]:
        line = {'name': str(entry['name']), 'families': [str(f) for f in entry.get('families') or []]}
        return {**line, 'retired': retired[line['name']]} if line['name'] in retired else line

    lost_with_findings = [
        {**_line(e), 'findings': sorted(tier_a[str(e['name'])].values(), key=lambda f: f['id'])} for e in lost if str(e['name']) in tier_a
    ]
    lost_or_renamed = []
    for e in lost:
        if str(e['name']) in tier_a:
            continue
        line = _line(e)
        if str(e['name']) in seen:
            line['seen_in_finding_scenarios'] = [
                {'item': item, 'finding': fid, 'policies': sorted(ps)} for (item, fid), ps in sorted(seen[str(e['name'])].items())
            ]
        lost_or_renamed.append(line)
    if elements.unresolved:
        notes.append(f'{len(elements.unresolved)} finding item(s) have no scenario.yaml on disk, so their complications are unknown: '
                     + ', '.join(sorted(elements.unresolved)))
    return {
        **base,
        'status': 'compared',
        'previous_count': len(old_entries),
        'new_count': len(_complications(new_doc)),
        'confirmed_findings_read': confirmed,
        'lost_with_findings': lost_with_findings,
        'lost_or_renamed': lost_or_renamed,
        'prd_family_lines_previous': _family_lines(previous / 'simulation_prd.md'),
        'notes': notes,
    }


# --- the carry -----------------------------------------------------------------------------------


def _severity_rank(findings: dict[str, dict[str, Any]]) -> int:
    said = {str(f.get('severity') or '').lower() for f in findings.values()}
    return min((_SEVERITY_ORDER.index(s) for s in said if s in _SEVERITY_ORDER), default=len(_SEVERITY_ORDER))


def carry(workdir: Path) -> dict[str, Any]:
    """The previous build's complications that set up a confirmed finding, for the re-scan to keep or retire (see the
    module doc). Reads the workdir as it stands before the scan writes its vocabulary; writes nothing."""
    yaml = _require_yaml()
    import open_epoch  # noqa: PLC0415 - the saved findings' writer, and the hand-off count it stamps

    art = workdir / 'artifacts'
    out: dict[str, Any] = {'schema': CARRY_SCHEMA, 'workdir': str(workdir), 'written_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                           'cap': CARRY_CAP, 'carried': [], 'not_carried': [], 'notes': []}
    saved_path = art / open_epoch.PREVIOUS_FINDINGS_FILENAME
    try:
        saved = json.loads(saved_path.read_text(encoding='utf-8')) if saved_path.is_file() else None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise Unreadable(f'{saved_path} is unreadable ({type(exc).__name__}: {exc})') from exc
    handoffs = open_epoch._handoffs(workdir)  # noqa: SLF001
    if not isinstance(saved, dict) or handoffs is None or saved.get('taken_at_handoff') != handoffs:
        out['notes'].append(f'no saved findings of the last handed-off build ({open_epoch.PREVIOUS_FINDINGS_FILENAME} stamped at hand-off '
                            f'{handoffs}): nothing to carry')
        return out
    out['taken_at_handoff'], out['scenario_set'] = handoffs, saved.get('scenario_set')
    # The arm's copy of the handed-off vocabulary, never the live file: the scan rewrites that one, and a re-run of
    # the carry after it must give the same carry (so a dropped name still warns).
    variables = art / open_epoch.PREVIOUS_VARIABLES_FILENAME
    pool = _complications(_load_yaml(yaml, variables)) if variables.is_file() else []
    if not pool:
        out['notes'].append(f'no previous vocabulary ({open_epoch.PREVIOUS_VARIABLES_FILENAME} with typical_complications): nothing to carry')
        return out
    rows = {str(r['id']): r for r in saved.get('rows') or [] if isinstance(r, dict) and r.get('id')}
    try:
        index = json.loads((art / 'runs' / 'index.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, ValueError):
        index = {}
    runs = {str(r.get('name')) for r in (index.get('runs') if isinstance(index, dict) else None) or []
            if isinstance(r, dict) and saved.get('scenario_set') and r.get('scenario_set_id') == saved.get('scenario_set')}
    if not runs:
        out['notes'].append('no run of the handed-off scenario set is indexed: only the findings\' own scenarios are read')
    names = {str(e['name']) for e in pool}
    links, answered = _links(art, runs)
    tier_a, _seen, conversations, _confirmed = _tier_a(rows, names, links, answered, _policies(yaml, workdir, out['notes']),
                                                      _Elements(yaml, art), {fid: str(r.get('trigger') or '') for fid, r in rows.items()})
    by_name = {str(e['name']): e for e in pool}
    ranked = sorted(tier_a, key=lambda n: (_severity_rank(tier_a[n]), -len(conversations.get(n, ())), n))
    for n, name in enumerate(ranked):
        found = [{'id': f['id'], 'title': str(rows[f['id']].get('title') or f['id']), 'severity': f.get('severity'), 'link': f['link']}
                 for f in sorted(tier_a[name].values(), key=lambda f: f['id'])]
        line = {'name': name, 'found': found, 'conversations': len(conversations.get(name, ()))}
        if n < CARRY_CAP:
            out['carried'].append({**line, 'entry': by_name[name]})
        else:
            out['not_carried'].append(line)
    return out


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def carry_main(argv: list[str]) -> int:
    """``carry --workdir W``: rewrite ``W/artifacts/vocabulary_carry.json``; on any failure leave none (exit 2)."""
    parser = argparse.ArgumentParser(prog='vocabulary_loss.py carry', description='the classes a re-scan keeps or retires')
    parser.add_argument('--workdir', required=True, type=Path, help="the agent's plurai_sim workdir, before the scan writes its vocabulary")
    args = parser.parse_args(argv)
    art = args.workdir / 'artifacts'
    for stale in (CARRY_NAME, RETIRED_NAME):  # a retirement and a carry are always this scan's
        (art / stale).unlink(missing_ok=True)
    try:
        result = carry(args.workdir)
        art.mkdir(parents=True, exist_ok=True)
        _write_json(art / CARRY_NAME, result)
    except Exception as exc:  # noqa: BLE001 - nothing to carry is said, never a stale carry
        (art / CARRY_NAME).unlink(missing_ok=True)
        print(f'ERROR: the carry could not be read ({type(exc).__name__}: {exc}). Nothing to carry.', file=sys.stderr)
        return 2
    print(f'carried {len(result["carried"])} complication(s): {", ".join(c["name"] for c in result["carried"]) or "none"} -> {art / CARRY_NAME}')
    return 0


# --- the short report ----------------------------------------------------------------------------


def render_md(result: dict[str, Any]) -> str:
    out = ['# Vocabulary loss (operator-only)', '', f'- previous: `{result["previous"]}`', f'- new: `{result["workdir"]}`', '']
    if result['status'] == 'no_baseline':
        return '\n'.join([*out, f'**No baseline:** {result["reason"]}. Nothing was compared.', ''])
    a, b = result['lost_with_findings'], result['lost_or_renamed']
    out += [
        f'Complications: {result["previous_count"]} before, {result["new_count"]} now. Exact names, so a lost name may have been renamed.',
        f'Lost with a confirmed finding (tier A): **{len(a)}**. Lost or renamed (tier B): **{len(b)}**.',
        '',
        '## Tier A: lost or renamed, had found a confirmed defect',
        '',
    ]
    for line in a:
        found = '; '.join(
            f'{f["id"]} ({f.get("severity") or "?"}, {f["link"]} link: ' + ', '.join(_pair(p) for p in f['pairs']) + ')' for f in line['findings']
        )
        out.append(f'- `{line["name"]}`{_families(line)}: {found}{_retired_note(line)}')
    if not a:
        out.append('- none')
    out += ['', '## Tier B: lost or renamed', '']
    for line in b:
        extra = ''
        if line.get('seen_in_finding_scenarios'):
            extra = ' (seen in finding scenarios, coarse: ' + ', '.join(f'{s["item"]} / {s["finding"]}' for s in line['seen_in_finding_scenarios']) + ')'
        out.append(f'- `{line["name"]}`{_families(line)}{extra}{_retired_note(line)}')
    if not b:
        out.append('- none')
    if result['prd_family_lines_previous']:
        out += ['', '## Previous PRD families (verbatim)', '', *(f'> {line}' for line in result['prd_family_lines_previous'])]
    if result['notes']:
        out += ['', '## Notes', '', *(f'- {note}' for note in result['notes'])]
    return '\n'.join([*out, ''])


def _pair(pair: dict[str, Any]) -> str:
    if pair.get('element'):
        return f'{pair["item"]} / element'
    return f'{pair["item"]} / {pair["policy"]}' if pair.get('policy') else str(pair['item'])


def _retired_note(line: dict[str, Any]) -> str:
    return f' — retired by the new scan: {line["retired"]}' if line.get('retired') else ''


def _families(line: dict[str, Any]) -> str:
    return f' (families {", ".join(line["families"])})' if line['families'] else ''


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['carry']:
        return carry_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    parser.add_argument('--previous', required=True, type=Path, help="the old build's plurai_sim workdir")
    parser.add_argument('--workdir', required=True, type=Path, help="the new build's plurai_sim workdir")
    parser.add_argument('--out', required=True, type=Path, help=f'the folder {JSON_NAME} and {MD_NAME} are written to')
    args = parser.parse_args(argv)
    try:
        result = compare(args.previous, args.workdir)
    except Unreadable as exc:
        print(f'ERROR: {exc}. Nothing was written.', file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / JSON_NAME).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (args.out / MD_NAME).write_text(render_md(result), encoding='utf-8')
    if result['status'] == 'no_baseline':
        print(f'no_baseline: {result["reason"]}')
    else:
        print(f'tier A {len(result["lost_with_findings"])}, tier B {len(result["lost_or_renamed"])} -> {args.out / JSON_NAME}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
