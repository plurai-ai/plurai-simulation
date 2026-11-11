"""Refuse a workdir whose copies of the plugin's own files are not this plugin's.

Some plugin files are copied UNCHANGED into the customer's workdir, because they have to run after
the plugin is gone (the hosted route's lifecycle and the rewrite it runs). A copy is frozen at the
plugin version that made it, and the lifecycle runs the copy, never the plugin: a bench scanned by
another plugin version keeps applying that version's rules, silently, whatever plugin is installed. This
check runs from the plugin before anything runs the copies, and names the exact command that
re-syncs each one.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_workdir_copies.py" "$WORKDIR"

Exit 0: every copy present is byte-for-byte the plugin's (an absent one is not this check's
business). Exit 1: at least one differs; stdout carries one `cp` command per copy to re-sync, to its
full destination (a copy may sit in a subdirectory of the workdir), and a stale bridge lockfile also
carries the `npm ci` that re-installs the runtime from it. A resolver copy whose backend record
(`record_backend_origin.py`) does not name the backend this session is bound to, or whose client stamp is not this plugin's version, is stale too, with the
command that re-records it.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import _client_handshake
import record_backend_origin

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

#: The bridge runtime in the plugin, and its shared protocol facts (which files a workdir copies, the install argv).
BRIDGE_RUNTIME = 'skills/simulation-setup-bridge/references/node/runtime'
_BRIDGE = json.loads((PLUGIN_ROOT / BRIDGE_RUNTIME / 'constants.json').read_text(encoding='utf-8'))
#: The workdir path of the copied runtime directory and of its lockfile.
BRIDGE_RUNTIME_DIR = _BRIDGE['paths']['runtime_dir']
BRIDGE_LOCKFILE = f'{BRIDGE_RUNTIME_DIR}/package-lock.json'

#: The workdir-relative path -> the plugin file it is copied from, unchanged. Every
#: `cp "${CLAUDE_PLUGIN_ROOT}/<path>" "$WORKDIR/<dest>"` a skill runs is a row here
#: (`test_check_workdir_copies.py` fails when they part).
COPIED_INTO_WORKDIR: dict[str, str] = {
    'rewire_agentforce.py': 'skills/simulation-setup-agentforce/scripts/rewire_agentforce.py',
    'agentforce_mock_path.sh': 'skills/simulation-setup-agentforce/scripts/agentforce_mock_path.sh',
    'export_agentforce_knowledge.py': 'skills/simulation-setup-agentforce/scripts/export_agentforce_knowledge.py',
    'resolve-plurai-urls.sh': 'scripts/resolve-plurai-urls.sh',
    'trusted-python.sh': 'scripts/trusted-python.sh',
    _BRIDGE['paths']['host']: 'skills/simulation-setup-bridge/references/host/bridge_host.py',
    **{f'{BRIDGE_RUNTIME_DIR}/{name}': f'{BRIDGE_RUNTIME}/{name}' for name in _BRIDGE['paths']['runtime_files']},
}


def install_command(workdir: Path) -> str:
    """The bridge runtime's install (`npm ci` from the shipped lockfile), prefixed at this workdir's runtime copy."""
    argv = list(_BRIDGE['install_argv'])
    argv[argv.index('--prefix') + 1] = str(workdir / BRIDGE_RUNTIME_DIR)
    return shlex.join(argv)


def stale_copies(workdir: Path, plugin_root: Path = PLUGIN_ROOT) -> list[tuple[str, Path]]:
    """(workdir-relative path, the plugin file it must equal) for each present copy that differs."""
    stale = []
    for name, source in COPIED_INTO_WORKDIR.items():
        copy, original = workdir / name, plugin_root / source
        if copy.is_file() and copy.read_bytes() != original.read_bytes():
            stale.append((name, original))
    return stale


def stale_record(workdir: Path) -> bool:
    """True when the workdir holds a resolver copy and its backend record is not the one this session needs.

    A base the session is not bound to is the resolver's own refusal to make, so it is not stale here.
    """
    if not (workdir / 'resolve-plurai-urls.sh').is_file():
        return False
    try:
        expected = record_backend_origin.expected_record(os.environ)
    except record_backend_origin.NotRecordable:
        return False
    wanted = {record_backend_origin.RECORD: expected, record_backend_origin.CLIENT_RECORD: _client_handshake.header_value()}
    return any(
        (path.read_text(encoding='utf-8').strip() if (path := workdir / name).is_file() else None) != value for name, value in wanted.items()
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('usage: check_workdir_copies.py <workdir>', file=sys.stderr)
        return 2
    workdir = Path(args[0])
    stale = stale_copies(workdir)
    for name, original in stale:
        print(f'{workdir / name} differs from this plugin\'s copy, so what runs from it does not apply this plugin\'s rules; re-copy it:')
        print(f'cp {shlex.quote(str(original))} {shlex.quote(str(workdir / name))}')
    if any(name == BRIDGE_LOCKFILE for name, _ in stale):
        print('The bridge runtime was installed from the old lockfile; once it is re-copied, re-install it:')
        print(install_command(workdir))
    record = stale_record(workdir)
    if record:
        print(f'The records beside {workdir / record_backend_origin.RECORD} do not name the backend and plugin version this session is bound to, so the copies would refuse it or name a stale client; re-record them:')
        print(f'{shlex.quote(sys.executable)} {shlex.quote(str(PLUGIN_ROOT / "scripts" / "record_backend_origin.py"))} {shlex.quote(str(workdir))}')
    return 1 if stale or record else 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
