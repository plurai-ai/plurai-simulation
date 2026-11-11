#!/usr/bin/env python3
"""The scan's delivery bookkeeping for the grounding package.

The scan uploads the agent descriptions with `plurai ingest-descriptions`, passes the part entries it
printed to `ground_workspace_start` with the workspace's previous package version, and remembers the
package version and the job the backend returned. This file holds the three small steps between those
calls so no skill retypes them:

    grounding_delivery.py previous --workdir D               # the recorded package version, or nothing
    grounding_delivery.py added    --workdir D --entries F   # the printed entries, checked, as the `added` argument
    grounding_delivery.py record   --workdir D --result J|-  # remember the package version and the job
    grounding_delivery.py head     --refusal TEXT|-          # the head a stale_previous refusal names (- reads stdin)
    grounding_delivery.py waiver   --workdir D --expires ISO # keep the waiver a generator call reported (detail.grounding_waived.expires)

The markers live beside the other machine-local handles in the workdir: `.grounding_package` holds the
package version the next delivery builds on, `.grounding_job` the job that consolidates it. Both are
ignored by the managed gitignore block, so a fresh clone starts without them; the backend then refuses
the first delivery as `stale_previous` and names the head, which `head` reads.

Exit codes: 0 done; 2 usage; 3 a check failed (stated on stderr, nothing written). Stdlib only, on the
Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grounding_files
from grounding_files import JOB_MARKER, record_waiver, write_atomic

PACKAGE_MARKER = '.grounding_package'
#: A description the backend reads the scan's claims from; it sits in `artifacts/` and is delivered when the SDK lists it.
SCAN_CONTRACT_NAME = 'scan_contract.json'
DESCRIPTION_FILES = ('plurai.config.yaml', 'simulation_prd.md', 'scenario_variables.yaml')

_HEAD_NAMED = re.compile(rf'\(stale_previous\).*?previous=({grounding_files.SHA256_FRAGMENT})')
_ENTRY_KEYS = frozenset({'kind', 'name', 'sha256', 'bytes'})


def _fail(message: str) -> int:
    print(f'grounding_delivery: {message}', file=sys.stderr)
    return 3


def recorded_package(workdir: Path) -> Optional[str]:
    """The package version the last delivery recorded, or None when there is none or the marker is damaged."""
    try:
        text = (workdir / PACKAGE_MARKER).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return text if grounding_files.is_sha256(text) else None


def _cmd_previous(args: argparse.Namespace) -> int:
    version = recorded_package(Path(args.workdir))
    if version:
        print(version)
    return 0


def _local_file(workdir: Path, name: str) -> Path:
    return workdir / 'artifacts' / name if name == SCAN_CONTRACT_NAME else workdir / name


def _sha256_of(path: Path) -> tuple:
    data = path.read_bytes()
    return 'sha256:' + hashlib.sha256(data).hexdigest(), len(data)


def checked_descriptions(workdir: Path, listed: Any) -> tuple:
    """`(entries, None)` when the printed description entries are exactly the files on disk, byte for byte, else `(None, cause)`."""
    if not isinstance(listed, list) or not all(isinstance(e, dict) for e in listed):
        return None, 'the description entries are not a JSON array of part entries; the delivery did not complete.'
    names = [e.get('name') for e in listed]
    if len(set(names)) != len(names):
        return None, f'two uploaded parts share one name: {sorted({n for n in names if names.count(n) > 1})}'
    for entry in listed:
        if entry.get('kind') != 'agent_description' or set(entry) != _ENTRY_KEYS:
            return None, f'the entry {entry.get("name")!r} is not an agent_description part entry; scan delivers descriptions only.'
        if not grounding_files.is_sha256(entry.get('sha256')) or not isinstance(entry.get('bytes'), int):
            return None, f'the entry {entry.get("name")!r} has no sha256 and byte count.'
    expected = [name for name in DESCRIPTION_FILES if (workdir / name).is_file()]
    allowed = set(expected) | ({SCAN_CONTRACT_NAME} if _local_file(workdir, SCAN_CONTRACT_NAME).is_file() else set())
    if not set(expected) <= set(names) or not set(names) <= allowed:
        missing, extra = sorted(set(expected) - set(names)), sorted(set(names) - allowed)
        return None, f'the uploaded parts are not the files on disk (not uploaded: {missing}; not on disk: {extra}).'
    for entry in listed:
        path = _local_file(workdir, entry['name'])
        if not path.is_file() or _sha256_of(path) != (entry['sha256'], entry['bytes']):
            return None, f'{entry["name"]} changed after it was uploaded; nothing was delivered. Build the part again and redo the upload.'
    return listed, None


def _cmd_added(args: argparse.Namespace) -> int:
    try:
        listed = json.loads(Path(args.entries).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _fail(f'{args.entries} is not the JSON array `plurai ingest-descriptions` prints; the delivery did not complete.')
    entries, problem = checked_descriptions(Path(args.workdir), listed)
    if problem is not None:
        return _fail(problem)
    print(json.dumps(entries, separators=(',', ':')))
    return 0


def record_delivery(workdir: Path, result: Any, workspace_id: Optional[str] = None) -> Optional[str]:
    """Remember the package version and the job a `ground_workspace_start` result names, and the waiver it reports, in machine-local markers.

    Returns the cause the result is refused for (and writes nothing), or None once both markers are written. With a
    `workspace_id`, a result for another workspace is refused.
    """
    if not isinstance(result, dict):
        return '--result is not the object `ground_workspace_start` returned.'
    version, job = result.get('package_version'), result.get('job_id') or result.get('id')
    if not isinstance(version, str) or not grounding_files.is_sha256(version):
        return 'the result names no package_version, so the next delivery has nothing to build on; nothing was recorded.'
    if not isinstance(job, str) or not job.strip() or '\n' in job:
        return 'the result names no job id; nothing was recorded.'
    if workspace_id and result.get('workspace_id') != workspace_id:
        return f'the result is for workspace {result.get("workspace_id")!r}, not {workspace_id!r}; nothing was recorded.'
    write_atomic(workdir / PACKAGE_MARKER, version + '\n')
    write_atomic(workdir / JOB_MARKER, job.strip() + '\n')
    record_waiver(workdir, result.get('waived'))
    return None


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        result = json.loads(sys.stdin.read() if args.result == '-' else args.result)
    except ValueError:
        return _fail('--result is not JSON: pass the object `ground_workspace_start` returned.')
    problem = record_delivery(Path(args.workdir), result, args.workspace_id)
    return _fail(problem) if problem else 0


def _cmd_waiver(args: argparse.Namespace) -> int:
    try:
        dt.datetime.fromisoformat(args.expires.replace('Z', '+00:00'))
    except ValueError:
        return _fail('--expires is not the ISO 8601 time `detail.grounding_waived.expires` carries; nothing was recorded.')
    record_waiver(Path(args.workdir), {'expires': args.expires, 'reason': ''})
    return 0


def _cmd_head(args: argparse.Namespace) -> int:
    match = _HEAD_NAMED.search(sys.stdin.read() if args.refusal == '-' else args.refusal)
    if match:
        print(match.group(1))
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description='Bookkeeping for the scan\'s grounding delivery.')
    sub = parser.add_subparsers(dest='command', required=True)
    previous = sub.add_parser('previous', help='print the recorded package version, or nothing')
    previous.add_argument('--workdir', required=True)
    previous.set_defaults(func=_cmd_previous)
    added = sub.add_parser('added', help='check the entries `plurai ingest-descriptions` printed and print them as the `added` argument')
    added.add_argument('--workdir', required=True)
    added.add_argument('--entries', required=True, help='the file the printed array was saved to')
    added.set_defaults(func=_cmd_added)
    record = sub.add_parser('record', help='remember the package version and the job `ground_workspace_start` returned')
    record.add_argument('--workdir', required=True)
    record.add_argument('--result', required=True, help='the JSON object the tool returned, or - to read it from stdin')
    record.add_argument('--workspace-id', default=None, help='refuse a result for another workspace')
    record.set_defaults(func=_cmd_record)
    waiver = sub.add_parser('waiver', help='keep the waiver a generator call reported, so the next call omits ledger_version')
    waiver.add_argument('--workdir', required=True)
    waiver.add_argument('--expires', required=True, help='the answer\'s detail.grounding_waived.expires')
    waiver.set_defaults(func=_cmd_waiver)
    head = sub.add_parser('head', help='print the package version a stale_previous refusal names, or nothing')
    head.add_argument('--refusal', required=True, help='the refusal text, or - to read it from stdin (a quoted heredoc keeps its characters out of the shell)')
    head.set_defaults(func=_cmd_head)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
