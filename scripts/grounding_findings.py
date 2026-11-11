#!/usr/bin/env python3
"""The grounding findings handler: what the agent reads, may send and keeps local.

A grounding job reports its findings on `detail.gate_authority` and the holding ones again on `detail.held_by`.
Findings are handled under the gate doctrine (`docs/gate-findings.md`): the agent decides alone, never asks the
user, copies fields and never recomputes them, and nothing reaches a customer-facing file. This script is the
reading half of that handler. It takes the job envelope `job_status` returned, saved to a file or piped in, and:

    grounding_findings.py show      --status F|-  [--json]    # every finding, holding first, with its handling
    grounding_findings.py overrule  --status F|- --gate-id G --fingerprint FP --reason R   # the held trace corpus only
    grounding_findings.py remove    --status F|- --part SHA   # the `removed` argument for a failed part
    grounding_findings.py log       --status F|- --art DIR    # the local log entries and candidates a finding owes
    grounding_findings.py integrity --status F|-              # the run-integrity line a failed trace part owes the report, or nothing

A value conflict never holds, so no value conflict is the agent's to edit, deliver again or overrule: the backend keeps the
winning value, the losing description keeps its own, and the losing value becomes a candidate scenario. A finding holds
only on an evidence fault in a corpus part. Trace analysis is opportunistic: a failed trace part is accepted, and a trace
corpus skipped for its environment never holds the version. The one overrule the agent may send attests such a corpus, so the
next delivery analyzes it. Nothing here talks to the backend: the agent calls
`ground_workspace_start` itself with what these commands print.
Exit codes: 0 done; 2 usage; 3 a check refused the request (one line on stderr, nothing printed); 4 the status is
not a job envelope. Stdlib only, on the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grounding_files
import grounding_keys
from _stdio import utf8_child_text

#: A printed value or remedy longer than this is cut with a marker; `--json` always carries the whole of it.
VALUE_LIMIT = 300
REMEDY_LIMIT = 500

GROUNDING_GATES = ('grounding.mocked_tool', 'grounding.disagreement')
#: The trace part's own hold: the staged traces span more than one environment, or are not the one the analysis is for.
#: The grounding delivery carries it, and it is answered by the agent's knowledge of what it exported, not by a description.
TRACE_GATE = 'trace.corpus_environment_unattested'
#: The evidence fields of that finding the handler copies, in the backend's own terms.
TRACE_EVIDENCE_FIELDS = ('archive_sha256', 'environments', 'expect_environment', 'declared', 'opaque', 'file_count')
GROUNDING_JOB_KIND = 'grounding'
#: A trace part's failure never holds: the suite goes on without it and the report says so in one run-integrity line.
TRACE_PART_KIND = 'trace_archive'
NO_TRACES_LINE = 'Run integrity — built without production traces (trace analysis failed).'

#: Every handling a finding can be given; `docs/grounding-findings.md` explains each.
HANDLES = (
    'accept',
    'remove_part',
    'informational_copy',
    'never_overrule',
    'attest_or_reexport',
    'other_gate',
)
#: The tiers of a fact read from the agent's code. A trace that disagrees with one is the backend's to decide: the mock
#: keeps the code value, and the production value is a candidate trace scenario.
CODE_TIERS = ('code', 'annotation')
#: The shortest reason the backend accepts for an overrule, counted after stripping.
REASON_MIN_CHARS = 20
#: The kinds that carry a kept value and a losing one: a conflict of these kinds leaves the losing value as a candidate scenario.
#: `worse_tier_disagrees` and `equal_tier_disagrees` are the names an older backend sends for `disagrees`.
VALUE_KINDS = ('disagrees', 'description_disagrees', 'worse_tier_disagrees', 'equal_tier_disagrees')
#: Kinds that report a state of the delivery and carry no value: no edit closes one, so each is accepted.
ACCEPTED_KINDS = ('unseen_declared_tool', 'undeclared_observed_tool', 'undeclared_cap_exceeded')


def _fail(message: str, code: int = 3) -> int:
    print(f'grounding_findings: {message}', file=sys.stderr)
    return code


class NotAJob(Exception):
    """The input is not the job envelope `job_status` returns."""


def load_status(source: str) -> dict:
    """The job envelope in `source` (a path, or `-` for stdin). Raises NotAJob when it is not one."""
    try:
        text = sys.stdin.read() if source == '-' else Path(source).read_text(encoding='utf-8')
        envelope = json.loads(text)
    except (OSError, ValueError) as error:
        raise NotAJob(f'{source} is not readable JSON ({type(error).__name__})') from None
    if not isinstance(envelope, dict) or not isinstance(envelope.get('detail'), dict):
        raise NotAJob(f'{source} is not a job envelope: it has no `detail` object')
    return envelope


def _object(owner: dict, name: str) -> dict:
    """The JSON object `owner[name]` holds, or an empty one when it holds anything else: the backend's shapes are read defensively."""
    value = owner.get(name)
    return value if isinstance(value, dict) else {}


def _authority(envelope: dict) -> dict:
    block = envelope['detail'].get('gate_authority')
    return block if isinstance(block, dict) else {}


def findings_of(envelope: dict) -> list:
    """The findings on the job's `gate_authority`, holding ones first and the backend's order kept within each group."""
    listed = [f for f in _authority(envelope).get('findings') or [] if isinstance(f, dict)]
    return [f for f in listed if f.get('holds') is True] + [f for f in listed if f.get('holds') is not True]


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else f'{text[:limit]}…(+{len(text) - limit} more characters)'


def _code_against_trace(kept_tier: Any, other_tier: Any) -> bool:
    return kept_tier in CODE_TIERS and other_tier == 'trace'


def _handle(finding: dict, kind: Any, job_kind: Any) -> str:
    """The handling of one finding. Authority decides first, then which job reported it, then the kind.

    Only an evidence fault holds and is answered by the client: a failed part is removed (`remove_part`) and a held trace
    corpus is attested or exported again (`attest_or_reexport`). Every value conflict, whatever its tiers, is accepted: no
    description edit and no delivery closes it, and its losing value is a candidate scenario.
    """
    if finding.get('authority') in ('security', 'invariant'):
        return 'never_overrule'
    if job_kind != GROUNDING_JOB_KIND:
        return 'informational_copy'
    if finding.get('gate_id') == TRACE_GATE:
        return 'attest_or_reexport'
    if finding.get('gate_id') not in GROUNDING_GATES:
        return 'other_gate'
    if kind == 'evidence_missing':
        if _object(finding, 'observed').get('part_kind') == TRACE_PART_KIND:
            return 'accept'
        return 'remove_part' if finding.get('holds') is True else 'accept'
    return 'accept'


def view(finding: dict, job_kind: Any = GROUNDING_JOB_KIND) -> dict:
    """One finding as the handler reports it: the backend's fields copied by name, then what to do with it."""
    evidence, observed = _object(finding, 'evidence'), _object(finding, 'observed')
    handle = _handle(finding, observed.get('kind'), job_kind)
    overrulable = handle == 'attest_or_reexport' and finding.get('holds') is True and finding.get('authority') == 'advisory'
    shown = {
        'gate_id': finding.get('gate_id'),
        'authority': finding.get('authority'),
        'holds': finding.get('holds') is True,
        'kind': observed.get('kind'),
        'part_kind': observed.get('part_kind'),
        'key': evidence.get('key'),
        'kept_tier': observed.get('kept_tier'),
        'kept_value': evidence.get('kept_value'),
        'other_tier': evidence.get('other_tier'),
        'other_value': evidence.get('other_value'),
        'fingerprint': finding.get('fingerprint'),
        'remedy': finding.get('remedy'),
        'handle': handle,
        'overrulable': overrulable,
    }
    shown['candidate'] = _is_hard_case(shown) or _is_left_disagreement(shown)
    if handle == 'attest_or_reexport':
        shown['trace_environment'] = {**{name: evidence.get(name) for name in TRACE_EVIDENCE_FIELDS}, 'why': observed.get('why')}
    return shown


_OVERRULE_TAIL = re.compile(r'[,;]?\s*\bor\s+overrule\b\.?\s*$', re.IGNORECASE)


def _shown_remedy(item: dict) -> Any:
    """The remedy as `show` prints it. An accepted finding is never overruled (the backend answers `not_holding` for a value
    conflict, which holds nothing), so the clause that offers one is cut and the line says so; `--json` keeps the backend's text whole."""
    remedy = item['remedy']
    if item['handle'] != 'accept' or not isinstance(remedy, str):
        return remedy
    trimmed = _OVERRULE_TAIL.sub('', remedy).rstrip(' ,;')
    return trimmed if trimmed == remedy else f'{trimmed} (no overrule: this finding holds nothing)'


def _text_lines(item: dict) -> list:
    shown = [
        f'FINDING gate_id={item["gate_id"]} authority={item["authority"]} holds={"true" if item["holds"] else "false"} kind={item["kind"]}'
        + (f' part_kind={item["part_kind"]}' if item.get('part_kind') else ''),
        f'  key={item["key"]}',
        f'  kept_tier={item["kept_tier"]} kept_value={_fit(_compact(item["kept_value"]), VALUE_LIMIT)}',
        f'  other_tier={item["other_tier"]} other_value={_fit(_compact(item["other_value"]), VALUE_LIMIT)}',
        f'  fingerprint={item["fingerprint"]}',
        f'  remedy={_fit(json.dumps(_shown_remedy(item), ensure_ascii=False), REMEDY_LIMIT)}',
        f'  handle={item["handle"]}' + (' candidate=scenario' if item['candidate'] else ''),
    ]
    census = item.get('trace_environment')
    if census:
        shown.append('  ' + ' '.join(f'{name}={_fit(_compact(value), VALUE_LIMIT)}' for name, value in census.items()))
    return shown


def _cmd_show(args: argparse.Namespace) -> int:
    envelope = load_status(args.status)
    detail = envelope['detail']
    items = [view(f, envelope.get('kind')) for f in findings_of(envelope)]
    held = detail.get('held_by') if isinstance(detail.get('held_by'), list) else []
    header = {
        'job': envelope.get('id'),
        'kind': envelope.get('kind'),
        'settled': detail.get('settled') is True,
        'held': len(held),
        'findings': len(items),
    }
    if args.json:
        print(json.dumps({'header': header, 'findings': items}, ensure_ascii=False))
        return 0
    print(
        f'GROUNDING_FINDINGS job={header["job"]} kind={header["kind"]} settled={"true" if header["settled"] else "false"} '
        f'held={header["held"]} findings={header["findings"]}'
    )
    for item in items:
        print('\n'.join(_text_lines(item)))
    return 0


#: Why an overrule cannot answer a finding, by the handling the finding was given.
_NOT_OVERRULABLE = {
    'never_overrule': 'a {authority} finding is never overruled: the protection stands. Repair what it names, or follow the error posture.',
    'informational_copy': 'this finding is the generator job\'s informational copy: handle the grounding job\'s own finding, never this copy.',
    'other_gate': 'this finding belongs to another gate, whose doctrine and carrier are its own, not the grounding handler\'s.',
    'remove_part': 'a failed part is repaired by removing it or delivering it again, never by an overrule.',
    'accept': 'a value conflict never holds, so there is nothing to overrule and nothing to edit: accept it.',
}


def _cmd_overrule(args: argparse.Namespace) -> int:
    envelope = load_status(args.status)
    if len(args.reason.strip()) < REASON_MIN_CHARS:
        return _fail(f'the reason must be at least {REASON_MIN_CHARS} characters: say what the export was narrowed by.')
    match = next((f for f in findings_of(envelope) if f.get('fingerprint') == args.fingerprint), None)
    if match is None:
        return _fail('no current finding carries that fingerprint. Copy it from the job\'s findings, byte for byte.')
    item = view(match, envelope.get('kind'))
    if item['gate_id'] != args.gate_id:
        return _fail(f'gate_id must be the finding\'s own: {item["gate_id"]}.')
    if item['handle'] in _NOT_OVERRULABLE:
        return _fail(_NOT_OVERRULABLE[item['handle']].format(authority=item['authority']))
    if not item['overrulable']:
        return _fail('this finding holds nothing, so an overrule would come back not_holding: accept it, or export again if its remedy is in reach.')
    print(json.dumps({'gate_id': args.gate_id, 'fingerprint': args.fingerprint, 'reason': args.reason}, ensure_ascii=False))
    return 0


def _names_part(item: dict, part: str) -> bool:
    """Whether a finding calls for removing `part`: a part whose processing failed, or a trace part held for its environment."""
    if item['kind'] == 'evidence_missing' and item['key'] == part:
        return True
    return item['handle'] == 'attest_or_reexport' and item['holds'] and (item.get('trace_environment') or {}).get('archive_sha256') == part


def _cmd_remove(args: argparse.Namespace) -> int:
    envelope = load_status(args.status)
    if envelope.get('kind') != GROUNDING_JOB_KIND:
        return _fail('a part is removed on the grounding job\'s own finding, not on a generator job\'s copy.')
    named = [f for f in findings_of(envelope) if _names_part(view(f), args.part)]
    if not named:
        return _fail('no failed-part finding and no held trace part names that part: only a part whose processing failed, or a trace part held for its environment, is removed.')
    print(json.dumps([args.part]))
    return 0


def _log_entries(envelope: dict) -> list:
    """The one-line notes a job's findings owe the plugin's local log.

    The production value that a trace showed against a code fact, and a losing description value. The log is operator
    material: it carries no fingerprint, and none of it reaches a hand-off or a report.
    """
    if envelope.get('kind') != GROUNDING_JOB_KIND:
        return []
    items = [view(f) for f in findings_of(envelope)]
    entries = []
    for item in items:
        if _is_hard_case(item):
            entries.append(
                f'grounding candidate trace scenario: {item["key"]} was seen as {_fit(_compact(item["other_value"]), VALUE_LIMIT)} '
                'in production against the kept code value; no blame until a run fails'
            )
        elif _is_left_disagreement(item):
            entries.append(
                f'grounding candidate scenario: {item["key"]} reads {_fit(_compact(item["other_value"]), VALUE_LIMIT)} in the '
                f'{item["other_tier"]} source against the kept {item["kept_tier"]} value; the description is left as it is; no blame until a run fails'
            )
    return entries


CANDIDATES_NAME = 'grounding_candidates.json'


def _is_hard_case(item: dict) -> bool:
    """A grounding job's trace against a code fact: the mock keeps the code value, and the trace value is one to learn from."""
    return item['handle'] == 'accept' and _code_against_trace(item['kept_tier'], item['other_tier'])


def _is_left_disagreement(item: dict) -> bool:
    """Any other value conflict: no edit and no delivery answer it, and the losing value is one to learn from."""
    return item['handle'] == 'accept' and item['kind'] in VALUE_KINDS and not _is_hard_case(item)


def candidates_of(envelope: dict) -> list:
    """The losing values the job's findings leave as they are, each as a candidate scenario: a trace's value against a code fact,
    and the other side of any other value conflict.

    A candidate names the key, the tool it belongs to, both values and tiers, and the trace units and call count that
    showed the value. It carries no fingerprint: it is internal and tells `/trace-to-scenarios` where to look.
    """
    if envelope.get('kind') != GROUNDING_JOB_KIND:
        return []
    found = []
    for finding in findings_of(envelope):
        item = view(finding)
        if not (_is_hard_case(item) or _is_left_disagreement(item)):
            continue
        observed = _object(finding, 'observed')
        try:
            # By kind before parsing: a mirror stored under an older grammar may name a kind it no longer knows.
            tool = grounding_keys.parse_key(item['key']).name if grounding_keys.kind_of(item['key']) in grounding_keys.KINDS else None
        except (grounding_keys.KeyGrammarError, TypeError, AttributeError):
            tool = None
        found.append(
            {
                'key': item['key'],
                'tool': tool,
                'kept_tier': item['kept_tier'],
                'kept_value': item['kept_value'],
                'observed_tier': item['other_tier'],
                'observed_value': item['other_value'],
                'units': observed.get('units'),
                'calls': observed.get('calls'),
            }
        )
    return found


def _record_candidates(art: Path, envelope: dict) -> int:
    """Add the job's candidates to `grounding_candidates.json` beside the loop log, each key and observed value once. Returns the total."""
    path = art / CANDIDATES_NAME
    try:
        kept = json.loads(path.read_text(encoding='utf-8')).get('candidates')
    except (OSError, ValueError, AttributeError):
        kept = None
    kept = [c for c in kept if isinstance(c, dict)] if isinstance(kept, list) else []
    seen = {(c.get('key'), _compact(c.get('observed_value'))) for c in kept}
    added = [c for c in candidates_of(envelope) if (c['key'], _compact(c['observed_value'])) not in seen]
    if added:
        grounding_files.write_atomic(path, json.dumps({'version': 1, 'candidates': [*kept, *added]}, indent=2, ensure_ascii=False) + '\n')
    return len(kept) + len(added)


def _cmd_log(args: argparse.Namespace) -> int:
    envelope = load_status(args.status)
    log = Path(args.art) / 'loop_log.md'
    try:
        existing = log.read_text(encoding='utf-8')
    except OSError:
        existing = ''
    written = 0
    for entry in _log_entries(envelope):
        if entry in existing:
            continue
        done = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve().parent / 'append_loop_log.py'), '--kind', 'note', '--finding', entry, '--artifacts-dir', args.art],
            capture_output=True,
            text=True, **utf8_child_text(),
            check=False,
        )
        if done.returncode != 0:
            return _fail(f'the local log refused an entry: {done.stderr.strip()[:200]}')
        written += 1
    print(f'LOGGED={written}')
    recorded = _record_candidates(Path(args.art), envelope)
    if recorded:
        print(f'CANDIDATES={recorded}')
    return 0


def _cmd_integrity(args: argparse.Namespace) -> int:
    """Print the run-integrity line when a trace part of the pinned job failed: the one grounding fact that reaches the report."""
    envelope = load_status(args.status)
    failed = envelope['detail'].get('failed_parts')
    if any(isinstance(part, dict) and part.get('kind') == TRACE_PART_KIND for part in failed or ()):
        print(NO_TRACES_LINE)
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description='The grounding findings handler.')
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show', help='every finding of the job, holding first')
    show.add_argument('--status', required=True, help='the job envelope `job_status` returned: a file, or - for stdin')
    show.add_argument('--json', action='store_true', help='one JSON object with the whole of every value')
    show.set_defaults(func=_cmd_show)
    send = sub.add_parser('overrule', help='check and print the argument that attests a trace corpus held for its environment')
    send.add_argument('--status', required=True)
    send.add_argument('--gate-id', required=True)
    send.add_argument('--fingerprint', required=True)
    send.add_argument('--reason', required=True)
    send.set_defaults(func=_cmd_overrule)
    remove = sub.add_parser('remove', help='check and print the `removed` argument for a failed part')
    remove.add_argument('--status', required=True)
    remove.add_argument('--part', required=True, help='the sha256 the failed-part finding names as its key')
    remove.set_defaults(func=_cmd_remove)
    log = sub.add_parser('log', help="write the notes the findings owe the plugin's local log")
    log.add_argument('--status', required=True)
    log.add_argument('--art', required=True, help='the artifacts directory whose loop_log.md takes the notes')
    log.set_defaults(func=_cmd_log)
    integrity = sub.add_parser('integrity', help='print the run-integrity line a failed trace part owes the report, or nothing')
    integrity.add_argument('--status', required=True)
    integrity.set_defaults(func=_cmd_integrity)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NotAJob as error:
        return _fail(str(error), 4)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
