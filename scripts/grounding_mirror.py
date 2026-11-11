#!/usr/bin/env python3
"""The client's copy of the grounding ledger, and the version a suite is built on.

`$WORKDIR/grounding_ledger.yaml` is the latest ledger version, byte for byte what the backend serves for the job
the last delivery recorded. A workdir that never delivered from this machine (a fresh clone, a teammate's) has no job
marker and asks the backend for the newest grounding job of its workspace instead; the job a pull finds that way is recorded
in the marker, so the findings and the corpus builds are read from it. The plugin never edits it and never reads a fact out of it for another file: facts
change in the agent's descriptions, and the next pull shows the result. A pull replaces the file whole, so a hand
edit lives until the next one. The served header is `ledger_version`, `settled`, `pinned`; the backend cannot know
a client's pin, so `pinned` is always `null` there and the pin is its own marker, `.ledger_version`.

    grounding_mirror.py job       --workdir D              # print the job the last delivery recorded, or nothing
    grounding_mirror.py workspace --workdir D              # print the workspace id the backend knows this workdir by, or nothing
    grounding_mirror.py settled --status F                 # has the job's version settled? the suite waits on this, not on the job's end
    grounding_mirror.py pull    --workdir D --plurai BIN   # fetch the latest version into the mirror
    grounding_mirror.py status  --workdir D                # the mirror header, the job and the pin, as JSON
    grounding_mirror.py pin     --workdir D                # pin the mirror's version, only once it is settled
    grounding_mirror.py pinned  --workdir D                # print the version a generator call sends, or nothing
    grounding_mirror.py waived  --workdir D                # print `WAIVED until <expiry>` while a waiver covers the workspace, or nothing
    grounding_mirror.py send    --workdir D                # print what a generator call does with `ledger_version`: one line
    grounding_mirror.py save    --workdir D --env-dir E [--workspace-id W]   # copy the mirror and the pin into a named env
    grounding_mirror.py restore --env-dir E --workdir D [--workspace-id W]   # bring a named env's mirror and pin back

A mirror and its pin belong to the workspace the version was made for: every generator refuses a pin from another one.
With `--workspace-id` (the workspace the env's generator calls send), `save` refuses a mirror of another workspace
(exit 3, nothing copied, one plain line that says the pin was not saved, why, and the next step; a missing mirror
prints the same line as `notice=` after `GROUNDING=ungrounded`) and `restore` brings back nothing from one, printing `GROUNDING=other_workspace` so the skill
pulls the env's own.

`settled` reads the `job_status` envelope saved to F and prints `SETTLED=<true|false> STATUS=<status>`. A version settles
before the job ends: the knowledge-graph build of a corpus part runs on after it, and only the scenario and eval calls wait
for that build. Exit 0 the wait is over (settled, or the job ended unsettled: a hold the findings handler reads); 4 still
processing, wait and read again; 1 the job failed or was cancelled; 3 F is not a job envelope.

`pull` prints one `MIRROR=` line and always exits 0: it ends a skill, and a failed pull is a state to report, never
a reason to lose the skill's own result. The one exception is the runner's credit stop (exit 13, the kind's sentence on
stderr), which is printed as a single `CREDIT-STOP: <sentence>` line instead.

Exit codes elsewhere: 0 done; 2 usage; 3 a check failed; 4 the mirror's version is not settled (`pin`); 5 no mirror
(`pin`). Stdlib only, on the Python 3.9 floor.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _client_handshake
import grounding_files
import sdk_exit_codes as exit_codes
import stop_message
from grounding_files import JOB_MARKER, PIN_MARKER, pinned_version, read_marker, waiver_expiry, write_atomic
from _stdio import utf8_child_text

MIRROR_NAME = 'grounding_ledger.yaml'
MANIFEST_NAME = 'plurai.env.yaml'

_JOB_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')  # never an option, never a path
_HEADER = re.compile(rf'\Aledger_version: ({grounding_files.SHA256_FRAGMENT})\nsettled: (true|false)\npinned: ([^\n]*)\n')
FETCH_SECONDS = 120
#: Grounding jobs asked for per page, and pages read, when a workspace's newest job that made a version is looked for.
LIST_PAGE = 10
LIST_PAGES = 5


_WORKSPACE = re.compile(r'^manifest:\n  workspace_id: (.+)$', re.MULTILINE)


def read_workspace(data: bytes) -> Optional[str]:
    """The workspace id the served version's manifest names, or None when it names none (a version older than the field)."""
    match = _WORKSPACE.search(data[:4096].decode('utf-8', errors='replace'))
    if match is None:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
        value = value[1:-1]
    return value or None


def read_header(data: bytes) -> Optional[dict]:
    """`{ledger_version, settled}` from the first lines of a served version, or None when it is not one."""
    match = _HEADER.match(data[:512].decode('utf-8', errors='replace'))
    if match is None:
        return None
    return {'ledger_version': match.group(1), 'settled': match.group(2) == 'true'}


def _say(**fields: object) -> None:
    print(' '.join(f'{key}={value}' for key, value in fields.items()), flush=True)


def recorded_job(workdir: Path) -> Optional[str]:
    """The grounding job the last delivery recorded, or None when there is none or the marker is not a plain id."""
    value = read_marker(workdir, JOB_MARKER)
    return value if value and _JOB_ID.match(value) else None


class WorkspaceListFailed(Exception):
    """The backend could not say which grounding job is a workspace's newest."""


def workspace_job(workspace: str) -> Optional[str]:
    """The newest grounding job of `workspace` that made a ledger version, or None when the workspace has none.

    Asks `list_artifacts(kind='grounding', workspace_id=...)`, newest first, and skips a job that has made no version yet
    (`detail.ledger_version` unset). Raises WorkspaceListFailed when the backend cannot be asked.
    """
    caller = Path(__file__).resolve().parent / 'mcp_call.py'
    offset = 0
    for _ in range(LIST_PAGES):
        arguments = json.dumps({'kind': 'grounding', 'workspace_id': workspace, 'limit': LIST_PAGE, 'offset': offset})
        try:
            done = subprocess.run([sys.executable, '-B', str(caller), 'list_artifacts', arguments], capture_output=True, text=True, **utf8_child_text(), timeout=FETCH_SECONDS, check=False)
            listing = json.loads(done.stdout) if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise WorkspaceListFailed(type(error).__name__) from None
        if done.returncode == _client_handshake.EXIT_VERSION_REFUSED:
            raise _client_handshake.VersionRefused(done.stderr.strip())
        if not isinstance(listing, dict) or not isinstance(listing.get('artifacts'), list):
            raise WorkspaceListFailed(f'exit {done.returncode}')
        for row in listing['artifacts']:
            detail = row.get('detail') if isinstance(row, dict) and isinstance(row.get('detail'), dict) else {}
            if detail.get('ledger_version') and isinstance(row.get('id'), str) and _JOB_ID.match(row['id']):
                return row['id']
        next_offset = listing.get('next_offset')
        if not listing.get('has_more') or not isinstance(next_offset, int) or next_offset <= offset:
            return None
        offset = next_offset
    return None


def _cmd_pull(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    job = recorded_job(workdir)
    found_by_workspace = job is None
    if job is None:
        workspace = grounding_files.workspace_of(workdir)
        if workspace is None:
            _say(MIRROR='ungrounded')
            return 0
        try:
            job = workspace_job(workspace)
        except WorkspaceListFailed as error:
            _say(MIRROR='unverified', reason=f'the workspace could not be asked for its latest version ({error}): the mirror is as it was')
            return 0
        if job is None:
            _say(MIRROR='ungrounded')
            return 0
    if not args.plurai or not os.access(args.plurai, os.X_OK):
        _say(MIRROR='unverified', reason='no runner: the mirror is as it was')
        return 0
    workdir.mkdir(parents=True, exist_ok=True)
    fd, incoming = tempfile.mkstemp(prefix='.grounding_ledger.incoming.', dir=str(workdir))
    os.close(fd)
    try:
        try:
            done = subprocess.run(
                [args.plurai, 'fetch', 'artifact', job, MIRROR_NAME, '--out', incoming],
                capture_output=True,
                text=True, **utf8_child_text(),
                timeout=FETCH_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _say(MIRROR='unverified', reason=f'the fetch did not finish ({type(error).__name__}): the mirror is as it was')
            return 0
        _client_handshake.raise_if_refused(done)
        if done.returncode == exit_codes.FETCH_CREDIT_REFUSED and (lines := done.stderr.strip().splitlines()):
            # The runner's exit for a credit refusal, the kind's sentence on its last stderr line: the pull stops the skill, it
            # does not carry on as an unverified mirror.
            print(stop_message.stop_line(re.sub(r'^[a-z_]+: ', '', lines[-1])), flush=True)
            return 0
        if done.returncode != 0:
            _say(MIRROR='unverified', reason=f'the fetch failed (exit {done.returncode}): the mirror is as it was')
            return 0
        data = Path(incoming).read_bytes()
        header = read_header(data)
        if header is None:
            _say(MIRROR='unverified', reason='the answer is not a ledger version: the mirror is as it was')
            return 0
        os.replace(incoming, workdir / MIRROR_NAME)
    finally:
        if os.path.exists(incoming):
            os.unlink(incoming)
    if found_by_workspace:
        write_atomic(workdir / JOB_MARKER, job + '\n')
    _say(MIRROR='pulled', ledger_version=header['ledger_version'], settled='true' if header['settled'] else 'false')
    return 0


def fetch_diff(workdir: Path, plurai: str, latest: str, pinned: str) -> Optional[str]:
    """The diff text from `pinned` to `latest` when the backend served one for exactly these two versions, else None.

    `grounding_staleness.py` and `grounding_adopt.py` share this fetch and each keeps its own file for the text, so a
    staleness report never stands in for a pending adoption's diff."""
    if not plurai or not os.access(plurai, os.X_OK):
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    fd, incoming = tempfile.mkstemp(prefix='.ledger_diff.incoming.', dir=str(workdir))
    os.close(fd)
    try:
        try:
            done = subprocess.run(
                [plurai, 'fetch', 'artifact', latest, f'diff/{pinned}', '--out', incoming],
                capture_output=True,
                text=True, **utf8_child_text(),
                timeout=FETCH_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        _client_handshake.raise_if_refused(done)
        if done.returncode != 0:
            return None
        text = Path(incoming).read_text(encoding='utf-8', errors='replace')
    finally:
        if os.path.exists(incoming):
            os.unlink(incoming)
    if not text.startswith(f'from: {pinned}\nto: {latest}\n'):
        return None
    return text


def _fail(message: str, code: int = 3) -> int:
    print(f'grounding_mirror: {message}', file=sys.stderr)
    return code


def mirror_header(workdir: Path) -> Optional[dict]:
    """`{ledger_version, settled}` of the mirror on disk, or None when there is none or it is not a ledger version."""
    try:
        with open(workdir / MIRROR_NAME, 'rb') as handle:
            data = handle.read(4096)
    except OSError:
        return None
    header = read_header(data)
    return None if header is None else {**header, 'workspace_id': read_workspace(data)}


#: The job statuses after which nothing more is processed.
_ENDED = ('done', 'failed', 'cancelled')


def _cmd_settled(args: argparse.Namespace) -> int:
    try:
        envelope = json.loads(Path(args.status).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return _fail(f'{args.status} is not readable JSON ({type(error).__name__}); read job_status again.')
    detail = envelope.get('detail') if isinstance(envelope, dict) else None
    if not isinstance(detail, dict):
        return _fail(f'{args.status} is not a job envelope: it has no `detail` object; read job_status again.')
    status = str(envelope.get('status') or 'unknown')
    settled = detail.get('settled') is True
    print(f'SETTLED={"true" if settled else "false"} STATUS={status}')
    if settled:
        return 0
    if status in ('failed', 'cancelled'):
        return 1
    return 0 if status in _ENDED else 4


def _cmd_job(args: argparse.Namespace) -> int:
    job = recorded_job(Path(args.workdir))
    if job:
        print(job)
    return 0


def _cmd_workspace(args: argparse.Namespace) -> int:
    workspace = grounding_files.workspace_of(Path(args.workdir))
    if workspace:
        print(workspace)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    header = mirror_header(workdir)
    pin = pinned_version(workdir)
    report = {
        'mirror': header is not None,
        'ledger_version': header['ledger_version'] if header else None,
        'settled': header['settled'] if header else None,
        'workspace_id': header['workspace_id'] if header else None,
        'job': recorded_job(workdir),
        'pin': pin,
        'newer_than_pin': bool(header and pin and header['ledger_version'] != pin),
    }
    print(json.dumps(report))
    return 0


def _cmd_pinned(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    pin = pinned_version(workdir)
    # A waived workspace runs ungrounded only on a call that carries no version, so a pin left on disk is never read.
    if pin and waiver_expiry(workdir) is None:
        print(pin)
    return 0


def _cmd_waived(args: argparse.Namespace) -> int:
    expires = waiver_expiry(Path(args.workdir))
    if expires:
        print(f'WAIVED until {expires}')
    return 0


def send_decision(workdir: Path) -> str:
    """What a generator call does about `ledger_version`, as one line.

    - `SEND <id>`: a settled pin and no waiver; the call carries the id.
    - `OMIT waived until <expiry>`: a waiver marker is in force; the call carries no version.
    - `JOB <id>`: no pin and no marker, but a delivery is recorded here; the job decides (wait for it, read its version).
    - `OMIT unpinned`: no pin, no marker and no recorded job (a fresh clone, or a workspace never delivered from here).
      The marker is machine-local, so a waived workspace looks like this on a clone. The call goes without a version and
      the backend decides: it runs a waived workspace ungrounded and refuses any other with `ledger_version refused
      (missing)`, on which the skill stops. A call that starts and reports `grounding_waived` writes the marker.
    """
    expires = waiver_expiry(workdir)
    if expires:
        return f'OMIT waived until {expires}'
    pin = pinned_version(workdir)
    if pin:
        return f'SEND {pin}'
    job = recorded_job(workdir)
    return f'JOB {job}' if job else 'OMIT unpinned'


def _cmd_send(args: argparse.Namespace) -> int:
    print(send_decision(Path(args.workdir)))
    return 0


def env_manifest():  # noqa: ANN202 - the sibling module, imported when a manifest has to be touched
    scripts = Path(__file__).resolve().parent.parent / 'skills' / 'manage-envs' / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import env_manifest  # noqa: PLC0415

    return env_manifest


def _cmd_pin(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    if not (workdir / MIRROR_NAME).is_file():
        print('UNGROUNDED ungrounded, deliver first')
        return 5
    header = mirror_header(workdir)
    if header is None:
        return _fail(f'{MIRROR_NAME} is not a ledger version; pull again before pinning.')
    if not header['settled']:
        print(f'UNSETTLED ledger_version={header["ledger_version"]}')
        return 4
    version = header['ledger_version']
    if (workdir / MANIFEST_NAME).is_file():
        # The manifest is written first: a manifest that cannot be rewritten must not leave a marker that says more.
        try:
            env_manifest().record_ledger_version(workdir, version)
        except Exception as error:  # noqa: BLE001 - a missing PyYAML and a damaged manifest both stop the pin
            return _fail(f'{MANIFEST_NAME} cannot record the pin ({type(error).__name__}); nothing was pinned.')
    write_atomic(workdir / PIN_MARKER, version + '\n')
    print(f'PINNED={version}')
    return 0


def _manifest_pin(env_dir: Path) -> Optional[str]:
    """The version an env's manifest records, or None when it records none or cannot be read."""
    if not (env_dir / MANIFEST_NAME).is_file():
        return None
    try:
        value = (env_manifest().read_manifest(env_dir) or {}).get('ledger_version')
    except Exception:  # noqa: BLE001 - a restore falls back to the marker, and to no pin
        return None
    return value if grounding_files.is_sha256(value) else None


#: The next step a SAVE that dropped the pin names; the skill's delivery-only repair is the one it points at.
_REPAIR = "Say yes to deliver this env's description files under its own workspace (no scan rerun) and the env gets its pin."


def _copy_mirror(source: Path, target: Path) -> Optional[dict]:
    """Copy the mirror between two workdirs, whole. None when the source is not a ledger version."""
    data = (source / MIRROR_NAME).read_bytes()
    header = read_header(data)
    if header is not None and source.resolve() != target.resolve():
        target.mkdir(parents=True, exist_ok=True)
        write_atomic(target / MIRROR_NAME, data)
    return header


def _other_workspace(mirror: Optional[str], workspace: Optional[str]) -> bool:
    """Whether a mirror is known to belong to a workspace other than `workspace`. A mirror that names none is not."""
    return bool(workspace and mirror and mirror != workspace)


def _cmd_save(args: argparse.Namespace) -> int:
    source, env_dir = Path(args.workdir), Path(args.env_dir)
    if not (source / MIRROR_NAME).is_file():
        _say(GROUNDING='ungrounded', notice=f'the grounding pin was not saved: no grounding has been delivered here yet. {_REPAIR}')
        return 0
    held = mirror_header(source)
    if held is not None and _other_workspace(held['workspace_id'], args.workspace_id):
        return _fail(
            f"the grounding pin was not saved: the saved grounding belongs to workspace '{held['workspace_id']}', not '{args.workspace_id}' "
            f'(this env\'s own), and a pin from another workspace is refused by every generator. Nothing was copied. {_REPAIR}'
        )
    header = _copy_mirror(source, env_dir)
    if header is None:
        return _fail(f'{MIRROR_NAME} is not a ledger version; pull again before saving.')
    pin = pinned_version(source)
    if pin and source.resolve() != env_dir.resolve():
        write_atomic(env_dir / PIN_MARKER, pin + '\n')
    _say(GROUNDING='saved', ledger_version=header['ledger_version'], pin=pin or 'none', workspace=(held or {}).get('workspace_id') or 'unknown')
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    env_dir, workdir = Path(args.env_dir), Path(args.workdir)
    if not (env_dir / MIRROR_NAME).is_file():
        print('GROUNDING=ungrounded ungrounded, deliver first')
        return 0
    held = mirror_header(env_dir)
    if held is not None and _other_workspace(held['workspace_id'], args.workspace_id):
        _say(GROUNDING='other_workspace', mirror_workspace=held['workspace_id'], workspace=args.workspace_id)
        return 0
    header = _copy_mirror(env_dir, workdir)
    if header is None:
        return _fail(f'{MIRROR_NAME} in {env_dir} is not a ledger version; deliver again.')
    pin = pinned_version(env_dir) or _manifest_pin(env_dir)
    marker = workdir / PIN_MARKER
    if pin:
        write_atomic(marker, pin + '\n')
    elif marker.exists() and env_dir.resolve() != workdir.resolve():
        marker.unlink()  # another env's pin never rides into this one
    _say(GROUNDING='restored', ledger_version=header['ledger_version'], pin=pin or 'none')
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description='The grounding mirror and the suite pin.')
    sub = parser.add_subparsers(dest='command', required=True)
    pull = sub.add_parser('pull', help='fetch the latest ledger version into the mirror')
    pull.add_argument('--workdir', required=True)
    pull.add_argument('--plurai', default='', help='the resolved `plurai` runner; empty when there is none')
    pull.set_defaults(func=_cmd_pull)
    settled = sub.add_parser('settled', help="has the job's version settled: the saved job_status envelope, read")
    settled.add_argument('--status', required=True, help='the file the `job_status` envelope was saved to')
    settled.set_defaults(func=_cmd_settled)
    job = sub.add_parser('job', help='print the grounding job the last delivery recorded, or nothing')
    job.add_argument('--workdir', required=True)
    job.set_defaults(func=_cmd_job)
    workspace = sub.add_parser('workspace', help='print the workspace id the backend knows this workdir by, or nothing')
    workspace.add_argument('--workdir', required=True)
    workspace.set_defaults(func=_cmd_workspace)
    status = sub.add_parser('status', help='the mirror header, the recorded job and the pin, as one JSON object')
    status.add_argument('--workdir', required=True)
    status.set_defaults(func=_cmd_status)
    pinned = sub.add_parser('pinned', help='print the version a generator call sends, or nothing')
    pinned.add_argument('--workdir', required=True)
    pinned.set_defaults(func=_cmd_pinned)
    waived = sub.add_parser('waived', help='print `WAIVED until <expiry>` while an operator waiver covers the workspace, or nothing')
    waived.add_argument('--workdir', required=True)
    waived.set_defaults(func=_cmd_waived)
    send = sub.add_parser('send', help='print what a generator call does with `ledger_version`: SEND <id>, JOB <id>, OMIT waived until <expiry> or OMIT unpinned')
    send.add_argument('--workdir', required=True)
    send.set_defaults(func=_cmd_send)
    pin = sub.add_parser('pin', help="pin the mirror's version once it is settled")
    pin.add_argument('--workdir', required=True)
    pin.set_defaults(func=_cmd_pin)
    save = sub.add_parser('save', help="copy the mirror and the pin into a named env's directory")
    save.add_argument('--workdir', required=True)
    save.add_argument('--env-dir', required=True)
    save.add_argument('--workspace-id', default=None, help="the env's own workspace (scripts/workspace_id.py): a mirror of another is refused")
    save.set_defaults(func=_cmd_save)
    restore = sub.add_parser('restore', help="bring a named env's mirror and pin into the active workdir")
    restore.add_argument('--env-dir', required=True)
    restore.add_argument('--workdir', required=True)
    restore.add_argument('--workspace-id', default=None, help="the env's own workspace (scripts/workspace_id.py): a mirror of another is not brought back")
    restore.set_defaults(func=_cmd_restore)
    args = parser.parse_args(argv)
    return _client_handshake.refusal_main(lambda: args.func(args))


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
