#!/usr/bin/env python3
"""The READ half of `$PLURAI_DIR/.backend_identity`, for the python helpers.

`scripts/assert-backend-identity.sh` is the guard, and it ARMS this record the first time a
session completes a verified PRECISE check — see the arm-time-record block in that file for why
the binding can only be learned there and not re-derived later. A shell helper picks the check up
by sourcing the guard on one line. A python helper cannot source bash, and three of them resolve
a backend from the environment and call it with no cross-check at all:

* `scripts/mcp_call.py` — a second MCP client, on `$PLURAI_URL` verbatim;
* `skills/generate-evals/scripts/verify_policies.py` — whose every live-read failure is a silent
  fallback, so a wrong-but-HEALTHY box has it grade the policy file against another deployment's
  sandbox namespace and flow vocabulary and say nothing;
* `skills/simulation-generate-suite/scripts/fetch_slot_bundle.py` — where a wrong box is
  indistinguishable from a bad set id (`INCONCLUSIVE — Nothing written`).

ONE module rather than three comparisons, because three copies of "which host is this" is three
chances to compare a different pair — the same reason `_backend_root()` in verify_policies.py is
one function for two reads.

HOST GRAIN, and no network. The guard's armed record is host-grain for a cost reason (it is read
in `poll_job.sh`'s preflight on every poll) and this side must not be finer than the record it
reads. A fetch here would also put a round trip inside `verify_policies.py`, which is required to
stay runnable offline.

FAIL-OPEN BY CONSTRUCTION. Anything other than "this session armed a host, and it is not the one
you are about to call" returns None: no record, an unreadable or malformed one, one armed by a
different Claude Code process, no binding key at all, an unresolvable workdir. A guard that refused on its own missing input is one
callers learn to skip, which is how the argument form of the shell guard came to fail open.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Kept in one place on this side exactly as the shell guard keeps it in one place on its own.
MARKER = '.backend_identity'


def host_of(url: str) -> str:
    """`scheme://user@host:port/path` → `host`, the same parse as `_plurai_host_of`.

    Ported rather than shelled out to: this is called before a refusal, in scripts that must run
    with no bash at all in the loop, and the parse is four lines.
    """
    rest = (url or '').split('://', 1)[-1].split('/', 1)[0].split('@')[-1]
    if rest.startswith('['):  # an IPv6 literal, brackets stripped
        return rest[1:].split(']', 1)[0]
    return rest.split(':', 1)[0]


def binding() -> str:
    """The key the record is armed and read under — `_abi_binding` in the shell guard, ported.

    The innermost harness decides. Copilot and Codex export a session id and no process id, so
    their key is `session:<id>`. Claude Code keys on its PROCESS (`$CLAUDE_PID`), because that is
    what expanded plugin.json and so what the MCP binding lives as long as; `claude -c` /
    `--resume` keep the conversation id across a relaunch that re-binds. Its session id is the
    fallback where no pid is exported, and with no key at all the answer is '' — unarmed, never a
    key every session-less run would share.
    """
    for var in ('COPILOT_AGENT_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID'):
        session = (os.environ.get(var) or '').strip()
        if session:
            return f'session:{session}'
    pid = (os.environ.get('CLAUDE_PID') or '').strip()
    if pid:
        return f'pid:{pid}'
    session = (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()
    return f'session:{session}' if session else ''


def _workdir() -> Path | None:
    """The agent's workdir: what the caller's shell already resolved, else the shared cascade.

    `$WORKDIR` is exported by `resolve-workdir.sh`, and a skill snippet that sources it and then
    runs one of these scripts in the SAME Bash call hands it over for free — which is the common
    case and costs no `git rev-parse` and no glob. `hooks/_workdir.py` is the maintained python
    port of that cascade, so a script invoked bare gets the same answer rather than a third
    implementation of it.
    """
    for name in ('WORKDIR', 'PLURAI_DIR'):
        value = (os.environ.get(name) or '').strip()
        if value:
            return Path(value)
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    try:
        from _workdir import resolve_workdir  # noqa: PLC0415 - optional, and only on the bare path
    except Exception:  # noqa: BLE001 - a missing port is "unarmed", never a failure of the caller
        return None
    return resolve_workdir()


def armed(workdir: str | Path | None = None) -> dict | None:
    """This PROCESS's armed record, or None when there is nothing to compare against.

    Keyed on `binding()`: a record left by an earlier process in the same workdir is not a stale
    opinion to refuse on — the operator may have legitimately moved backends since — so it reads
    as absent, exactly as the shell guard treats it.
    """
    key = binding()
    if not key:
        return None
    root = Path(workdir) if workdir is not None else _workdir()
    if root is None:
        return None
    try:
        record = json.loads((root / MARKER).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 - absent, unreadable or malformed are all "unarmed"
        return None
    if not isinstance(record, dict) or not record.get('server_url'):
        return None
    if record.get('binding') != key:
        return None
    record['_path'] = str(root / MARKER)
    return record


def split(target_url: str, *, caller: str, workdir: str | Path | None = None) -> str | None:
    """The refusal paragraph when `target_url` is not the backend this session is bound to.

    Names BOTH sides, like every other refusal this guard family emits: "wrong backend" with one
    host in it is a sentence an operator cannot act on, and the whole failure mode here is that
    the two hosts look interchangeable until one of them answers a clean 404.
    """
    record = armed(workdir)
    if record is None:
        return None
    bound, target = host_of(record['server_url']), host_of(target_url)
    if not bound or not target or bound == target:
        return None
    instance = record.get('instance_id')
    return (
        f'{caller}: SPLIT BACKENDS — refusing to call a backend this session is not bound to.\n'
        f'  MCP server is bound to : {bound}   (recorded by this Claude Code process\'s latest verified '
        f'backend_identity() check, at {record["server_url"]}'
        + (f' — instance_id {instance}' if instance else '')
        + ')\n'
        f'  this call would go to  : {target}   ({target_url})\n'
        '  Everything generated through MCP lives on the FIRST; this call would run against the '
        'SECOND and get clean 404s that look like expired ids.\n'
        '  Fix: align PLURAI_URL with the backend the MCP server is bound to and retry. If '
        'the MCP side is the wrong one, restart Claude Code with the intended '
        'PLURAI_URL — it cannot be changed mid-session.\n'
        '  If the RECORD is the stale side, re-arm it: call backend_identity() and source '
        f'assert-backend-identity.sh with its object — a verified check rewrites {record["_path"]}.'
    )
