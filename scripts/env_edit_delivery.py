#!/usr/bin/env python3
"""The bookkeeping after `/edit-env` commits a session that patched the config.

A session opened on a pinned grounding ledger version holds the config as a file it may patch. When the backend
commits it, `end_edit_session` returns the patched `config_yaml` and a `delivery`: the grounding job that consolidates
the delivery, the package version it made, and the SHA-256 of the config bytes it delivered. The skill writes
`config_yaml` to the active env's `plurai.config.yaml`, and this script checks that file against the delivered hash
before it remembers the package version and the job. A config retyped with one byte changed is refused here, so the
next delivery never builds on a package whose config the file no longer matches.

    env_edit_delivery.py record --workdir D --workspace-id W --package P --job J --config-sha256 S --delivery-workspace DW

The package version lands in `.grounding_package` and the job in `.grounding_job`, the machine-local handles the next
delivery and the next generator pin read (`scripts/grounding_delivery.py` owns them).

Exit codes: 0 recorded; 2 usage; 3 a check failed (stated on stderr, nothing written). Stdlib only, on the Python 3.9
floor.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grounding_delivery  # noqa: E402 - the sibling module owns the markers
import grounding_files  # noqa: E402

CONFIG_NAME = 'plurai.config.yaml'


def _fail(message: str) -> int:
    print(f'env_edit_delivery: {message}', file=sys.stderr)
    return 3


def _cmd_record(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    config = workdir / CONFIG_NAME
    if not grounding_files.is_sha256(args.config_sha256):
        return _fail(f'--config-sha256 {args.config_sha256!r} is not the `config_sha256` of the delivery; nothing was recorded.')
    if not config.is_file():
        return _fail(f'{config} does not exist: write the `config_yaml` the backend returned to it, then record again. Nothing was recorded.')
    written = 'sha256:' + hashlib.sha256(config.read_bytes()).hexdigest()
    if written != args.config_sha256:
        return _fail(
            f'{config} is not the config the backend delivered ({written} on disk, {args.config_sha256} delivered). '
            'Write the returned `config_yaml` to it again, byte for byte, then record again. Nothing was recorded.'
        )
    if args.delivery_workspace != args.workspace_id:
        return _fail(f'the delivery is for workspace {args.delivery_workspace!r}, not this bench {args.workspace_id!r}; nothing was recorded.')
    result = {'package_version': args.package, 'job_id': args.job, 'workspace_id': args.workspace_id}
    problem = grounding_delivery.record_delivery(workdir, result, args.workspace_id)
    return _fail(problem) if problem else 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description='Bookkeeping after /edit-env commits a patched config.')
    sub = parser.add_subparsers(dest='command', required=True)
    record = sub.add_parser('record', help='check the written config against the delivered hash, then remember the package version and the job')
    record.add_argument('--workdir', required=True)
    record.add_argument('--package', required=True, help="the delivery's package_version")
    record.add_argument('--job', required=True, help="the delivery's job_id")
    record.add_argument('--config-sha256', required=True, help="the delivery's config_sha256")
    record.add_argument('--workspace-id', required=True, help="this bench's workspace id (scripts/workspace_id.py)")
    record.add_argument('--delivery-workspace', required=True, help="the delivery's workspace_id: a delivery for another workspace is refused")
    record.set_defaults(func=_cmd_record)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
