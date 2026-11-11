#!/usr/bin/env python3
"""The account-window claim — where it lives, who else holds one, and when it ends. ONE owner.

One account window runs one suite: two suites on two DIFFERENT agents in one window both died on
the same reset. The claim makes that condition visible; it REPORTS and never blocks.

* **Machine-global, one file per workdir** under ``$PLURAI_STATE_HOME`` (else ``~/.plurai``) /
  ``suite_windows/``. Not under the workdir — a per-workdir location cannot see the other suite —
  and not a single holder: a losing claim that writes nothing hides a second live suite the moment
  the first one ends.
* **It ends when the suite's flow window is deliberately closed** — ``flow-window.sh close
  generate-suite`` and report_guard's close (clean hand-off or stand-down) call
  :func:`release_flow`, which releases the key the claim RECORDED beside the window — the skill's
  own resolution, never a hook-side re-resolution that cannot see a multi-agent repo's agent.
  NOT at SessionEnd: a headless driver continues one suite across sessions. A suite that dies
  expires after :data:`TTL_HOURS`.
* **No verdict is not free.** An unreadable or undated claim counts as live and is reported.

Usage: ``_suite_window.py release <workdir|''> [<flowdir>...]`` — exit 0 released (or nothing to
release), 1 a claim could not be removed. ``bench_manifest.py window claim|status`` is the
reporting half.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import sys
import time
from pathlib import Path

WINDOWS_DIRNAME = "suite_windows"
TTL_HOURS = 8
#: Beside the flow window: the key this session's claim was written under.
CLAIM_RECORD = ".suite_claim"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"


def state_root() -> Path:
    root = os.environ.get("PLURAI_STATE_HOME")
    return Path(root) if root else Path.home() / ".plurai"


def claim_key(workdir: str | Path) -> str:
    """A workdir as a claim KEY — the PHYSICAL path, so a claim by one spelling (a symlinked or
    logical ``$PWD``) is still ours when read back by the other."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:  # bench_manifest loads this module by path, without hooks/ on sys.path
            sys.path.append(here)
        import _platform  # noqa: PLC0415

        # A claim record is a file a command can write: on Windows a UNC key is kept as spelled, never resolved.
        if not _platform.is_local_drive_path(workdir, within=_platform.cwd_roots()):
            return str(workdir)
        return str(Path(workdir).resolve())
    except OSError:
        return str(workdir)


def _path_for(key: str) -> Path:
    return state_root() / WINDOWS_DIRNAME / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] + ".json")


def _expired(started_at: str) -> bool | None:
    """True past the TTL, False while live, None when the stamp cannot be read (NOT expired)."""
    try:
        when = calendar.timegm(time.strptime(started_at, _STAMP))
    except (TypeError, ValueError, OverflowError):
        return None
    return time.time() - when > TTL_HOURS * 3600


def _read(path: Path) -> tuple[dict | None, str | None]:
    """The claim, or ``(None, why)`` for a file that exists and is not a readable claim."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "it is not readable JSON (truncated, half-written, or hand-edited)"
    if not isinstance(doc, dict):
        return None, f"its top level is a {type(doc).__name__}, not a claim object"
    other = doc.get("workdir")
    if other is not None and not isinstance(other, str):
        return None, f"its 'workdir' is a {type(other).__name__}, not the path a claim names"
    return doc, None


def _candidates() -> list[Path]:
    try:
        return sorted((state_root() / WINDOWS_DIRNAME).glob("*.json"))
    except OSError:
        return []


def others(workdir: str | Path) -> list[dict]:
    """Every OTHER live claim: ``{workdir, started_at, undated}``, or ``{path, unreadable}``.

    A positively expired claim is removed on the way past; an undated or unreadable one is kept
    and reported, never read as free.
    """
    key = claim_key(workdir)
    ours = _path_for(key)
    found: list[dict] = []
    for path in _candidates():
        if path == ours:
            continue
        doc, why = _read(path)
        if doc is None:
            found.append({"path": str(path), "unreadable": why})
            continue
        holder = doc.get("workdir")
        if not holder or claim_key(holder) == key:
            continue
        started = str(doc.get("started_at") or "")
        expired = _expired(started)
        if expired:
            path.unlink(missing_ok=True)
            continue
        found.append({"workdir": holder, "started_at": started, "undated": expired is None})
    return found


def holds(workdir: str | Path) -> dict | None:
    """This workdir's own live claim, or None."""
    doc, _why = _read(_path_for(claim_key(workdir)))
    return doc if doc and _expired(str(doc.get("started_at") or "")) is not True else None


def claim(workdir: str | Path, plugin_version: str, flowdir: str | Path | None = None) -> list[dict]:
    """Record OUR claim (always) — and its key beside ``flowdir``'s window — and return every other
    live claim."""
    key = claim_key(workdir)
    if flowdir and Path(flowdir).is_dir():
        (Path(flowdir) / CLAIM_RECORD).write_text(key, encoding="utf-8")
    found = others(key)
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"workdir": key, "started_at": time.strftime(_STAMP, time.gmtime()), "plugin_version": plugin_version}) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return found


def release(workdir: str | Path) -> None:
    """End THIS workdir's claim — never another's. Raises OSError when it cannot be removed."""
    key = claim_key(workdir)
    _path_for(key).unlink(missing_ok=True)


def release_flow(flowdirs: list, workdir: str | Path | None = None) -> None:
    """Release every key a claim recorded beside the window in ``flowdirs`` (consuming the record),
    and ``workdir``'s own claim when one is given."""
    keys = [str(workdir)] if workdir else []
    for base in flowdirs:
        record = Path(base) / CLAIM_RECORD
        try:
            keys.append(record.read_text(encoding="utf-8").strip())
            record.unlink()
        except OSError:
            continue
    for key in filter(None, keys):
        release(key)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "release":
        print("usage: _suite_window.py release <workdir|''> [<flowdir>...]", file=sys.stderr)
        return 64
    try:
        release_flow([d for d in argv[3:] if d], argv[2] or None)
    except OSError as exc:
        print(f"suite-window: could not release the account-window claim for {argv[2]} ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
