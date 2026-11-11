#!/usr/bin/env python3
"""What the grounding scripts share: marker names, the id shape, the atomic write, the pin reader and the part-entry check.

The delivery, mirror, corpus, trace, adoption and env-manifest scripts run under a bare `python3` or `$PLURAI_PY`, import
each other by path and agree on a handful of names and shapes. They live here once so a change cannot reach one script
and miss another. Stdlib only, on the Python 3.9 floor; nothing here prints or exits.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional, Union

#: The machine-local handle for the grounding job the last delivery started.
JOB_MARKER = '.grounding_job'
#: The workdir's pin: the settled ledger version a suite is built on. A named env's manifest records the same id.
PIN_MARKER = '.ledger_version'
#: The operator waiver the last delivery reported (`{"reason", "expires"}`); present only while a waiver covers the workspace.
WAIVER_MARKER = '.grounding_waived'
#: The declared part the scan builds beside the descriptions. It stays on the machine: the traces' scrub names and the consent check read it.
DECLARED_PART_NAME = 'declared_part.json'
#: The `/simulate` stage boundaries a newer settled version is adopted at.
BOUNDARIES = ('suite', 'run', 'fixes')

#: A package or ledger version id, and the hash of a part: `sha256:` and 64 lowercase hex digits.
SHA256 = re.compile(r'^sha256:[0-9a-f]{64}\Z')
#: The same shape as a fragment, for patterns that find an id inside a longer text.
SHA256_FRAGMENT = SHA256.pattern[1:].replace(r'\Z', '')

_ENTRY_KEYS = frozenset({'kind', 'sha256', 'bytes', 'name', 'label', 'posture', 'anon_key_fingerprint'})
_POSTURES = ('raw', 'anonymized')


def is_sha256(value: Any) -> bool:
    """Whether `value` is a `sha256:<64 hex>` id."""
    return isinstance(value, str) and SHA256.match(value) is not None


def write_atomic(path: Path, data: Union[str, bytes]) -> None:
    """Replace `path` whole: a reader sees the old file or the new one, never half of it. Creates the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data.encode('utf-8') if isinstance(data, str) else data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_marker(workdir: Path, name: str) -> Optional[str]:
    """The one stripped line a marker file holds, or None when it is absent, unreadable, empty or longer than a line."""
    try:
        text = (workdir / name).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None
    value = text.strip()
    return value if value and '\n' not in value else None


def pinned_version(workdir: Path) -> Optional[str]:
    """The version the workdir is pinned to, or None when there is no pin or the marker is damaged."""
    value = read_marker(workdir, PIN_MARKER)
    return value if is_sha256(value) else None


def record_waiver(workdir: Path, waived: Any) -> None:
    """Keep the waiver a delivery reported, or drop the one a former delivery kept when this one reports none."""
    marker = workdir / WAIVER_MARKER
    if isinstance(waived, dict) and isinstance(waived.get('expires'), str):
        write_atomic(marker, json.dumps({'reason': str(waived.get('reason') or ''), 'expires': waived['expires']}) + '\n')
    else:
        marker.unlink(missing_ok=True)


def waiver_expiry(workdir: Path, now: Optional[dt.datetime] = None) -> Optional[str]:
    """When the waiver in force for this workdir ends, or None when none is: no marker, a damaged one, or a lapsed one.

    A waiver the operator set on the backend decides how a generator call is read: a call that carries no
    `ledger_version` runs ungrounded there, and one that carries a version is grounded on it. The marker is the
    delivery's report of that waiver, so a workdir that never delivered has none.
    """
    try:
        data = json.loads((workdir / WAIVER_MARKER).read_text(encoding='utf-8'))
        expires = data['expires']
        ends = dt.datetime.fromisoformat(expires.replace('Z', '+00:00'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=dt.timezone.utc)
    return expires if ends > (now or dt.datetime.now(dt.timezone.utc)) else None


_PLAIN_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def workspace_of(workdir: Path) -> Optional[str]:
    """The workspace id the backend knows this workdir by: the bench's id, and `<bench id>.<alias>` under a named env.

    The read-only twin of `scripts/workspace_id.py`, which mints the bench id on first use; this never writes. None when
    the bench has no readable id yet, which means nothing was ever delivered under it.
    """
    root, alias = (workdir.parent.parent, workdir.name) if workdir.parent.name == 'envs' and workdir.parent.parent.name == 'plurai_sim' else (workdir, None)
    value = read_marker(root, '.workspace_id')
    if value is None or _PLAIN_ID.match(value) is None:
        return None
    return f'{value}.{alias}' if alias else value


def check_part_entry(entry: Any, kind: str, label_check: Optional[Callable[[dict], Optional[str]]] = None) -> Optional[str]:
    """The cause a part entry `plurai ingest-*` printed is not deliverable as a `kind` part, or None.

    It states what the backend would refuse (a part with no posture, a fingerprint on a raw part, none on an anonymized
    one) before a tool call is spent. `label_check` runs once the kind is right, for the parts that carry a label.
    """
    if not isinstance(entry, dict):
        return 'is not a JSON object'
    extra = sorted(set(entry) - _ENTRY_KEYS)
    if extra:
        return f'carries {extra}, which a part entry does not'
    if entry.get('kind') != kind:
        return f'is a {entry.get("kind")!r} part, not a {kind} part'
    if label_check is not None:
        problem = label_check(entry)
        if problem is not None:
            return problem
    if not is_sha256(entry.get('sha256')):
        return 'has no sha256 in the form sha256:<64 hex>'
    size = entry.get('bytes')
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return 'has no byte count'
    posture = entry.get('posture')
    if posture not in _POSTURES:
        return 'states no posture: an evidence part is raw or anonymized, never unstated'
    fingerprint = entry.get('anon_key_fingerprint')
    if posture == 'raw' and fingerprint is not None:
        return 'is raw and carries an anon_key_fingerprint'
    if posture == 'anonymized' and not fingerprint:
        return 'is anonymized and carries no anon_key_fingerprint'
    return None
