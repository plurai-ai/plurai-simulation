#!/usr/bin/env python3
"""Carry the red-cell audit's TARGET-CLAUSE false reds into `scenario_variables.yaml` as typed rulings.

A FALSE RED whose defect is a clause of the scenario TARGET (a `grades_scenario_target` judge
grading an obligation the agent's contract never asks for) has no rubric to edit: `/edit-evals`
cannot reach it, and the next draw's author re-derives the same clause from the same doctrine.
Written here as a `target_rulings` row, the ruling rides the variables upload to the backend, which
tells the author not to require it and has the auditor block a target that does, citing the row's
`id`.

    record_target_rulings.py --variables plurai_sim/scenario_variables.yaml \
        --audit <run dir>/red_cell_audit.json --scan-contract plurai_sim/artifacts/scan_contract.json \
        --source "red-cell audit, set <scenario_set_id>" [--prd plurai_sim/simulation_prd.md]

`--audit` is a run's typed audit (`red_cell_audit.py`) or a JSON list of rows (or `-` for stdin). Only `defect_surface:
target_clause` rows whose verdict is `FALSE RED`, or `UNOBSERVABLE` (a warning triage confirming a
demoted target flag names a clause the agent provably cannot observe — a structural fact, never
flicker), are written; every other row is the landing's `/edit-evals` scope or an agent finding,
and is ignored here. Each written row needs the
quoted target `clause` and a `ruling` (`not_in_contract` | `unobservable`); `contract_quote` (the
contract's own clause, verbatim) and `flow_id` are carried when present. A `not_in_contract` row must
quote the clause the reply followed: with an empty `contract_quote` it rests on silence, so it is
refused (exit 2, named) — that cell is `UNDECIDABLE`, not a ruling.

What it guarantees:

* **Idempotent.** A ruling's `id` is derived from its flow and its normalized obligation, so the
  same FALSE RED re-recorded is the same row, never a second one.
* **Comments survive.** The rows live in a managed block spliced as TEXT between markers, the same
  way `record_egress.py` writes `plurai.config.yaml`; a hand-written top-level `target_rulings:`
  outside the block is refused, never overwritten.
* **Validated before it lands.** The spliced file must read back with exactly these rows AND pass
  `verify.py` — the same validator `/extract-scenario-variables` runs — or nothing is written.
* **A scan-authored judge is never a target ruling.** `--scan-contract` is required: a `target_clause`
  row whose `policy` is a key of the contract's `absence_probe_policies` is refused (exit 2, named
  with the probe's `fact_id`). That judge grades a premise FACT the scan dealt, not a target clause;
  its FALSE RED is `scan_fact`, and the landing retires the fact from the PRD. A contract file that
  does not exist means no scan-authored judge (none was stashed); one that does not parse is exit 4.
* **Quotes are checked, stale rulings drop.** With `--prd`, a NEW row whose `contract_quote` is not
  in the PRD is refused (exit 2, named): it cites a clause the contract does not hold. A PRIOR ruling
  whose quote has left the PRD rests on a clause that was edited away, so it is dropped (and named);
  so is a PRIOR `not_in_contract` ruling with an empty quote, with or without `--prd`.
  Both compare under `normalize_contract_text` — the same normalization the backend's quote gate uses
  (case, quote glyphs and markdown emphasis are formatting), pinned by a shared vector fixture.

* **A potential finding's subject is not ruled out silently.** A ruling whose audit row names a subject a
  `potential` row of `artifacts/agent_findings.jsonl` (beside the variables file) carries — `policy:<the row's
  policy>`, or `complication:<the row's complication>` when the row names the user move its red rests on — is a
  holding `plugin.rulings.potential_subject` finding: a gap nobody could classify is not edited away. The run is
  refused (exit 2, nothing written) unless the ledger already lifts it or `--overrule-reason "<what you verified>"`
  is given, which appends the overrule to `$ART/.gate_overrules.jsonl` and a `note` to `loop_log.md` and proceeds.
  The overrule is internal: the finding stays `potential`, and no hand-off or report names it.

Exit 0 written or nothing to write; 2 a row or the result is invalid, or a potential finding's subject is ruled out
without an overrule (nothing written); 3 the file carries a hand-written or corrupted block (nothing written); 4 an
input could not be read.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_ledger  # noqa: E402 - sibling script; stdlib only
import record_finding  # noqa: E402 - sibling script: the findings store reader and the subject grammar
import red_cell_audit  # noqa: E402 - sibling script: the run's typed audit file (C5)
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

BEGIN_PREFIX = '# BEGIN plurai-simulation target_rulings rev'
BEGIN = f'{BEGIN_PREFIX} 1 (managed — rewritten by record_target_rulings.py)'
END = '# END plurai-simulation target_rulings'

#: Mirrors the backend's `TARGET_RULING_KINDS`; a row outside it is dropped by the backend.
RULING_KINDS = ('not_in_contract', 'unobservable')
ROW_KEYS = ('id', 'obligation', 'ruling', 'contract_quote', 'flow_id', 'source')
#: The two findings that rule a target clause out: a red-cell `FALSE RED`, and a warning triage that
#: confirms a demoted target flag names a clause the agent provably cannot observe (`UNOBSERVABLE`).
QUALIFYING_VERDICTS = ('FALSE RED', 'UNOBSERVABLE')

_UNMANAGED = re.compile(r'^target_rulings\s*:', re.MULTILINE)
_VERIFY = Path(__file__).resolve().parent.parent / 'skills' / 'extract-scenario-variables' / 'scripts' / 'verify.py'


def _load_verify() -> Any:
    spec = importlib.util.spec_from_file_location('scenario_variables_verify', _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _norm(text: str) -> str:
    return ' '.join(str(text or '').split())


#: Mirrors the backend's `_CONTRACT_FORMATTING`; both run `contract_quote_norm_vectors.json`.
_CONTRACT_FORMATTING = str.maketrans('', '', '`*"\'\u201c\u201d\u2018\u2019')


def normalize_contract_text(text: str) -> str:
    """The backend's `normalize_contract_text`: lowercase, `` ` * " ' “ ” ‘ ’ `` removed, whitespace
    collapsed. The one comparison form for "this quote is in the PRD", shared so the two repos agree."""
    return ' '.join(str(text or '').translate(_CONTRACT_FORMATTING).lower().split())


def ruling_id(obligation: str, flow_id: str = '') -> str:
    """Stable id for a ruling: the same obligation under the same flow is the same ruling."""
    digest = hashlib.sha256(f'{_norm(flow_id)}\n{_norm(obligation).lower()}'.encode()).hexdigest()
    return f'tr-{digest[:10]}'


#: The backend's `PROBE_POLICY_PREFIX` + `_`: every absence-probe judge it merges is named `absence_probe_<fact_id>`.
SCAN_PROBE_PREFIX = 'absence_probe_'


def scan_probe_policies(contract: Any) -> dict[str, str]:
    """`{policy_name: fact_id}` for every judge the scan authored: the keys of `absence_probe_policies`,
    each with the `fact_id` its probe row (`report.sections.absence_probes.probes[]`) carries."""
    if not isinstance(contract, dict):
        return {}
    sections = (contract.get('report') or {}).get('sections') or {}
    probes = (sections.get('absence_probes') or {}).get('probes') or []
    fact_of = {p.get('policy_name'): _norm(p.get('fact_id')) for p in probes if isinstance(p, dict)}
    return {name: fact_of.get(name, '') for name in contract.get('absence_probe_policies') or {}}


def rows_from_audit(audit: Any, source: str, *, scan_probes: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    """`(rulings, problems)` from red-cell audit rows. Only target-clause FALSE REDs qualify, and one
    on a judge in `scan_probes` (the scan-authored ones) is a problem, never a ruling."""
    if not isinstance(audit, list):
        return [], ['the audit must be a JSON list of red-cell rows.']
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    for i, row in enumerate(audit):
        if not isinstance(row, dict):
            problems.append(f'audit row {i} is not an object.')
            continue
        verdict = _norm(str(row.get('verdict') or '').replace('_', ' ')).upper()
        if verdict not in QUALIFYING_VERDICTS or _norm(row.get('defect_surface')) != 'target_clause':
            continue
        clause = _norm(row.get('clause'))
        ruling = _norm(row.get('ruling')).lower()
        where = f'audit row {i} ({row.get("item", "?")}/{row.get("policy", "?")})'
        policy = _norm(row.get('policy'))
        if policy not in scan_probes and policy.startswith(SCAN_PROBE_PREFIX):
            # A backend-merged probe with no local contract behind it (an agent with no corpus: the backend typed it
            # from the PRD's Absences). Still scan-authored — the fact id is the name's suffix.
            scan_probes = {**scan_probes, policy: policy.removeprefix(SCAN_PROBE_PREFIX)}
        if policy in scan_probes:
            problems.append(
                f'{where} types a FALSE RED on the scan-authored judge {policy!r} as `target_clause`; it is `scan_fact` '
                f'(fact_id {scan_probes[policy] or "?"!r}) — the landing retires that fact from the PRD, a target ruling cannot reach it.'
            )
            continue
        if verdict == 'UNOBSERVABLE' and ruling != 'unobservable':
            problems.append(f'{where} is an UNOBSERVABLE row whose ruling is {row.get("ruling")!r}; it must be `unobservable`.')
            continue
        if not clause:
            problems.append(f'{where} is a target_clause FALSE RED with no quoted `clause`.')
            continue
        if ruling not in RULING_KINDS:
            problems.append(f'{where} has ruling {row.get("ruling")!r}; it must be one of {", ".join(RULING_KINDS)}.')
            continue
        quote = _norm(row.get('contract_quote'))
        if quote and not normalize_contract_text(quote):
            problems.append(f'{where} has contract_quote {quote!r}, which is only formatting characters; quote the PRD clause verbatim.')
            continue
        if ruling == 'not_in_contract' and not quote:
            problems.append(f'{where} is a not_in_contract ruling with no contract_quote; quote the clause the reply followed, or rule the cell UNDECIDABLE.')
            continue
        flow_id = _norm(row.get('flow_id'))
        out.append(
            {
                'id': ruling_id(clause, flow_id),
                'obligation': clause,
                'ruling': ruling,
                'contract_quote': quote,
                'flow_id': flow_id,
                'source': _norm(source),
                # Not a ROW_KEY: `merge` drops it. What the ruling rules out, for the potential-finding guard.
                '_subjects': audit_subjects(row),
            }
        )
    return out, problems


def audit_subjects(row: dict) -> list[str]:
    """The subjects an audit row's ruling would rule out: its `policy`, and its `complication` when it names one."""
    named = [f'policy:{_norm(row.get("policy"))}', f'complication:{_norm(row.get("complication"))}']
    return [s for s in (record_finding.parse_subject(n) for n in named) if s]


def potential_subjects(findings_rows: dict[str, dict]) -> dict[str, list[str]]:
    """`{subject: [finding id, ...]}` over the `potential` rows of the findings store."""
    named: dict[str, list[str]] = {}
    for fid, row in sorted(findings_rows.items()):
        if row.get('status') == 'potential':
            for subject in record_finding.subjects_of(row):
                named.setdefault(subject, []).append(fid)
    return named


def subject_findings(new: list[dict[str, Any]], prior_ids: set[str], findings_rows: dict[str, dict]) -> list[gate_ledger.GateFinding]:
    """One holding `plugin.rulings.potential_subject` finding per (NEW ruling, subject, potential finding) it would rule out."""
    named = potential_subjects(findings_rows)
    out: dict[str, gate_ledger.GateFinding] = {}
    for row in new:
        if row['id'] in prior_ids:
            continue
        for subject in row.get('_subjects') or []:
            for fid in named.get(subject, []):
                finding = gate_ledger.make_finding(
                    gate_ledger.RULINGS_POTENTIAL_SUBJECT,
                    subject=row['id'],
                    evidence={'finding': fid, 'subject': subject, 'ruling': row['id']},
                    observed={'obligation': row['obligation'][:200]},
                    remedy=f'keep the clause graded: {fid} is potential, and ruling {subject} out would narrow the suite away from it',
                    draft_ref=None,
                )
                out[finding.fingerprint] = finding
    return list(out.values())


def _log_overrule(art: Path, line: str) -> str | None:
    """Append the internal loop-log note; why it could not be written, or None."""
    script = Path(__file__).resolve().parent / 'append_loop_log.py'
    done = subprocess.run(
        [sys.executable, str(script), '--kind', 'note', '--artifacts-dir', str(art), '--finding', line],
        capture_output=True, text=True, **utf8_child_text(), check=False,
    )
    return None if done.returncode == 0 else (done.stderr.strip() or f'exit {done.returncode}')


def render_block(rows: list[dict[str, str]]) -> str:
    body = yaml.safe_dump({'target_rulings': rows}, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1 << 16).rstrip('\n')
    return f'{BEGIN}\n{body}\n{END}\n'


def managed_span(text: str) -> tuple[int, int] | None:
    """`(start, end)` of the managed block, `None` when there is none; raises on a half-block."""
    start, end = text.find(BEGIN_PREFIX), text.find(END)
    if start < 0 and end < 0:
        return None
    if start < 0 or end < 0 or end < start:
        raise ValueError('the managed target_rulings block is corrupted (a BEGIN or END marker is missing or out of order); repair it by hand.')
    return start, end + len(END)


def splice(text: str, rendered: str) -> str:
    span = managed_span(text)
    if span is not None:
        return text[: span[0]] + rendered + text[span[1] :].removeprefix('\n')
    separator = '' if text.endswith('\n\n') or not text.strip() else '\n' if text.endswith('\n') else '\n\n'
    return text + separator + rendered


def quote_in_prd(quote: str, prd_text: str) -> bool:
    normalized = normalize_contract_text(quote)
    return bool(normalized) and normalized in normalize_contract_text(prd_text)


def merge(prior: list[dict[str, Any]], new: list[dict[str, str]], prd_text: str | None) -> tuple[list[dict[str, str]], list[str]]:
    """Prior rows first, new rows appended by id. A PRIOR `not_in_contract` row with an empty quote is
    dropped, and with a PRD so is a PRIOR row whose quote is gone; a NEW row is never dropped here —
    `main` has already refused one whose quote is empty or absent."""
    kept_prior: list[dict[str, Any]] = []
    stale: list[str] = []
    for row in prior:
        if not isinstance(row, dict) or not row.get('id'):
            continue
        if _norm(row.get('ruling')).lower() == 'not_in_contract' and not _norm(row.get('contract_quote')):
            stale.append(str(row['id']))
            continue
        if prd_text is not None and _norm(row.get('contract_quote')) and not quote_in_prd(str(row.get('contract_quote')), prd_text):
            stale.append(str(row['id']))
            continue
        kept_prior.append(row)
    merged: dict[str, dict[str, str]] = {}
    for row in [*kept_prior, *new]:
        if str(row['id']) not in merged:
            merged[str(row['id'])] = {k: _norm(row.get(k)) for k in ROW_KEYS}
    return list(merged.values()), stale


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911 - one exit per refusal class
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--variables', required=True, help='path to scenario_variables.yaml')
    ap.add_argument('--audit', required=True, help="the run's red_cell_audit.json, a JSON list of rows, or '-' for stdin")
    ap.add_argument('--source', required=True, help='where the rulings came from, e.g. "red-cell audit, set <id>"')
    ap.add_argument('--scan-contract', required=True, help="the bench's scan_contract.json; its scan-authored judges are never target rulings")
    ap.add_argument('--prd', help='simulation_prd.md; drops rulings whose contract_quote it no longer contains')
    ap.add_argument('--overrule-reason', help='internal: rule out a subject a potential finding names anyway, saying what you verified')
    args = ap.parse_args(argv)

    path = Path(args.variables)
    try:
        original = path.read_text(encoding='utf-8')
        audit = json.loads(sys.stdin.read() if args.audit == '-' else Path(args.audit).read_text(encoding='utf-8'))
        if isinstance(audit, dict):
            audit = red_cell_audit.unwrap(audit, args.audit)
        prd_text = Path(args.prd).read_text(encoding='utf-8') if args.prd else None
        contract_path = Path(args.scan_contract)
        contract = json.loads(contract_path.read_text(encoding='utf-8')) if contract_path.exists() else None
    except (OSError, ValueError) as exc:
        print(f'record_target_rulings: cannot read an input ({exc}).', file=sys.stderr)
        return 4

    new, problems = rows_from_audit(audit, args.source, scan_probes=scan_probe_policies(contract))
    if prd_text is not None:
        problems += [
            f'{row["id"]} ({row["obligation"][:60]!r}): its contract_quote {row["contract_quote"][:80]!r} is not in the PRD — quote the PRD clause verbatim.'
            for row in new
            if row['contract_quote'] and not quote_in_prd(row['contract_quote'], prd_text)
        ]
    if problems:
        print(f'record_target_rulings: {len(problems)} problem(s) — NOTHING was written:', file=sys.stderr)
        for problem in problems:
            print(f'  - {problem}', file=sys.stderr)
        return 2

    try:
        span = managed_span(original)
    except ValueError as exc:
        print(f'record_target_rulings: {path}: {exc}', file=sys.stderr)
        return 3
    outside = original if span is None else original[: span[0]] + original[span[1] :]
    if _UNMANAGED.search(outside):
        print(
            f'record_target_rulings: {path} carries a top-level `target_rulings:` OUTSIDE the managed block. '
            'Refusing to overwrite hand-authored rulings — move them into the block by hand, or delete them.',
            file=sys.stderr,
        )
        return 3
    try:
        prior = ((yaml.safe_load(original[span[0] : span[1]]) or {}).get('target_rulings') or []) if span else []
    except (yaml.YAMLError, AttributeError) as exc:
        print(f'record_target_rulings: {path}: the managed block does not parse ({exc}); repair it by hand.', file=sys.stderr)
        return 3

    art = path.parent / 'artifacts'
    try:
        findings_rows = record_finding.read_rows(record_finding.store_path(art / 'agent_findings.md'))
        named = subject_findings(new, {str(r.get('id')) for r in prior if isinstance(r, dict)}, findings_rows)
        ledger = gate_ledger.read_ledger(gate_ledger.ledger_path(art)) if named else []
        held = [f for f in named if not gate_ledger.is_overruled(f, ledger)]
    except (OSError, ValueError) as exc:
        print(f'record_target_rulings: cannot read the findings store or the overrule ledger under {art} ({exc}).', file=sys.stderr)
        return 4
    if held and args.overrule_reason is None:
        print(f'record_target_rulings: {len(held)} ruling(s) would rule out a subject a POTENTIAL agent finding names — NOTHING '
              'was written. A gap nobody could classify stays graded; rule it out only with an internal --overrule-reason '
              '(ledger and loop log only, never the hand-off):', file=sys.stderr)
        for f in held:
            print(f'  - {f.evidence["ruling"]} rules out {f.evidence["subject"]}, named by potential finding {f.evidence["finding"]}', file=sys.stderr)
        return 2
    reason = ' '.join(str(args.overrule_reason or '').split())
    overrules = [gate_ledger.Overrule(f.gate_id, f.fingerprint, reason) for f in held]
    try:
        for overrule, f in zip(overrules, held):
            gate_ledger.check_overrule(overrule, finding=f.as_dict())
    except gate_ledger.OverruleRefused as exc:
        print(f'record_target_rulings: the overrule was refused ({exc.label}: {exc}) — NOTHING was written.', file=sys.stderr)
        return 2

    rows, stale = merge(prior, new, prd_text)
    for rid in stale:
        print(f'record_target_rulings: dropped {rid} — its contract_quote is empty or no longer in the PRD.', file=sys.stderr)
    if not rows and span is None:
        print('record_target_rulings: the audit carries no target_clause FALSE RED or UNOBSERVABLE row; nothing to do.', file=sys.stderr)
        return 0
    updated = splice(original, render_block(rows)) if rows else original[: span[0]] + original[span[1] :].removeprefix('\n')
    if updated == original:
        print(f'record_target_rulings: {path} already carries these rulings; nothing to do.', file=sys.stderr)
        return 0

    parsed = yaml.safe_load(updated)
    if not isinstance(parsed, dict) or (parsed.get('target_rulings') or []) != rows:
        print(f'record_target_rulings: the spliced file does not read back the rulings; {path} is UNCHANGED.', file=sys.stderr)
        return 2
    temp = path.with_suffix(path.suffix + '.rulings-tmp')
    temp.write_text(updated, encoding='utf-8')
    # Only errors this splice INTRODUCED refuse the write: a pre-existing defect elsewhere in the
    # file is /extract-scenario-variables' to fix, and must not strand a ruling the landing needs.
    verify = _load_verify().verify
    before = set(verify(path))
    errors = [e.replace(str(temp), str(path)) for e in verify(temp) if e.replace(str(temp), str(path)) not in before]
    if errors:
        temp.unlink(missing_ok=True)
        print(f'record_target_rulings: the result fails verify.py; {path} is UNCHANGED:', file=sys.stderr)
        for err in errors:
            print(f'  - {err}', file=sys.stderr)
        return 2
    temp.replace(path)
    print(f'record_target_rulings: {path} now carries {len(rows)} ruling(s): {", ".join(r["id"] for r in rows)}')
    return _record_overrules(art, overrules, held)


def _record_overrules(art: Path, overrules: list[gate_ledger.Overrule], held: list[gate_ledger.GateFinding]) -> int:
    """The overrules the written rulings rest on → the ledger and a loop-log note: internal, never the hand-off."""
    for overrule, f in zip(overrules, held):
        try:
            gate_ledger.append_overrule(
                gate_ledger.ledger_path(art), overrule, finding=f.as_dict(), at=gate_ledger._now(),  # noqa: SLF001
                plugin_version=gate_ledger._plugin_version(), session=os.environ.get('CLAUDE_CODE_SESSION_ID', '').strip(),  # noqa: SLF001
            )
        except (gate_ledger.OverruleRefused, OSError) as exc:
            print(f'record_target_rulings: WARNING the rulings are written but the overrule did not reach the ledger ({exc}).', file=sys.stderr)
            return 2
        note = (f'overrule {f.gate_id}: ruling {f.evidence["ruling"]} rules out {f.evidence["subject"]} although potential '
                f'finding {f.evidence["finding"]} names it — {overrule.reason}')
        failed = _log_overrule(art, note)
        if failed:
            print(f'record_target_rulings: WARNING the overrule is in the ledger but loop_log.md was not written ({failed}).', file=sys.stderr)
        print(f'record_target_rulings: overruled internally — {f.evidence["ruling"]} rules out {f.evidence["subject"]} ({f.evidence["finding"]} stays potential).')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
