#!/usr/bin/env python3
"""The local page between the model's calls: a new or rewritten file lands under ``artifacts/``, and the page catches up within seconds.

    page_watch.py <workdir> --flowdir D [--owner PID]

``publish_report.spawn_page_watch`` starts it, detached, at the start of a /simulate run and at every flow entry, when the
plan is live, the page is on, none runs yet and somebody may be watching. Native Windows has no watcher: a read of the
page there can make a foreground fill's replace fail. Any failure of its own only leaves the page to its other refreshes.
Its stderr goes to ``<flowdir>/.page_watch.err``, never to the session.

ONE PER FLOW DIR. An OS lock on ``<flowdir>/.page_watch.lock``, which dies with its holder however the holder dies. A
second instance exits 0. A lock that cannot be taken means running unlocked: two watchers are harmless. The holder
removes the file when it exits.

EVERY WAKE (``RUN_FILL_SECONDS``, 3 by default, or twice the last refill's length when that is longer). It exits after
``MISSES`` wakes in a row with no live plan (``publish_report.live_plan``: the plan ended or went 24 h unwritten; a halted
or paused plan is live), when the owner is gone, or after ``MAX_ERRORS`` wakes in a row that raised. The spawner names the
owner (``run_start.owner_pid``): from in here that walk would find the spawning python. With no owner it is tied to the
plan alone. The workdir is the one recorded in the plan when that is an existing folder, else the argument: a run can
start before its scan names the real one.

THE CHANGE TEST (:func:`changed`). A file under ``artifacts/`` modified at or after the second the last refill started
in (a 1 s filesystem cannot say more; the cost is one extra refill). Only files count: creating the fill's private dir
moves the mtime of ``artifacts/`` itself. Not input: any file or folder whose name starts with a dot (the fill's private
dir, the publish copy, the markers and the runner's output; the page reads none), the page and its sidecar (:data:`PAGE_FILES`),
any folder with ``.runner_pid`` and no ``.runner_exit``, which is a run still playing, with its own refresher, and while
one plays, ``flow_progress.json``, which that refresher's fills rewrite. Otherwise ``flow_progress.json`` is input: a plan
transition rewrites it.

THE ACTION. ``publish_report.refill`` and nothing else: no fill, so no fill record, no publish copy, no running state.
A refill that raises is tried once more at the next wake and named once per distinct error on stderr; it never ends the
watcher.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

LOCK = '.page_watch.lock'
ERRORS = '.page_watch.err'
DEFAULT_SECONDS = 3.0
#: Wakes in a row with no live plan before it exits: one unreadable read of the plan is not the plan's end.
MISSES = 3
#: Wakes in a row that raised before it exits: an exit check that cannot run must not make it immortal.
MAX_ERRORS = 20
#: The page and its sidecar, which every refill rewrites.
PAGE_FILES = frozenset({'report.html', 'report.data.js'})
PROGRESS_FILE = 'flow_progress.json'


def take_lock(flowdir: Path) -> tuple[int | None, bool]:
    """``(the held lock's descriptor, whether another watcher holds it)``. ``(None, False)``: it cannot be taken at all."""
    import fcntl  # noqa: PLC0415 - POSIX only, and native Windows never starts this

    try:
        held = os.open(str(flowdir / LOCK), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None, False
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(held)
        return None, True
    except OSError:
        os.close(held)
        return None, False
    return held, False


def lock_held(flowdir: Path) -> bool:
    """Whether a watcher holds the lock now. A free lock is let go at once, and no file is created."""
    if not (flowdir / LOCK).exists():
        return False
    held, busy = take_lock(flowdir)
    if held is not None:
        os.close(held)
    return busy


def _release(held: int, flowdir: Path) -> None:
    os.close(held)
    try:
        (flowdir / LOCK).unlink()
    except OSError:
        pass


def _every() -> float:
    try:
        seconds = float(os.environ.get('RUN_FILL_SECONDS', ''))
    except ValueError:
        return DEFAULT_SECONDS
    return seconds if 0 < seconds < float('inf') else DEFAULT_SECONDS


def changed(artifacts: Path, since_ns: int) -> bool:
    """Whether an input under ``artifacts`` was modified at or after the whole second of ``since_ns`` (see THE CHANGE TEST).
    No ``artifacts`` yet: False."""
    floor = since_ns - since_ns % 1_000_000_000
    root = str(artifacts)
    progress = playing = False
    for folder, dirs, files in os.walk(root):
        if '.runner_pid' in files and '.runner_exit' not in files:
            playing = True
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if not name.startswith('.')]
        for name in files:
            if name.startswith('.') or name in PAGE_FILES:
                continue
            try:
                if os.stat(os.path.join(folder, name)).st_mtime_ns < floor:
                    continue
            except OSError:
                continue  # gone since the listing, or a dangling link
            if name == PROGRESS_FILE and folder == root:
                progress = True  # input only if no run turns out to be playing
            else:
                return True
    return progress and not playing


def _workdir_of(plan: dict, given: Path) -> Path:
    """The workdir recorded in the plan when it is an existing folder (``simulate_continue.plan_workdir``), else ``given``."""
    named = plan.get('workdir')
    try:
        return Path(named) if isinstance(named, str) and named and Path(named).is_dir() else given
    except OSError:
        return given


def watch(workdir: Path, flowdir: Path, owner: int | None) -> None:
    """The loop: see EVERY WAKE, THE CHANGE TEST and THE ACTION."""
    stamp = time.time_ns()  # before the imports, which can take a while: what lands during them is new
    import publish_report  # noqa: PLC0415 - after the lock: a second instance exits without paying for it
    import run_start  # noqa: PLC0415

    every = _every()
    pause = every
    misses = errors = 0
    restored = False
    said: set[str] = set()
    while True:
        time.sleep(pause)
        pause = every
        try:
            plan = publish_report.live_plan(flowdir)
            misses = 0 if plan is not None else misses + 1
            if misses >= MISSES or (owner is not None and not run_start.alive(owner)):
                return
            here = _workdir_of(plan, workdir) if plan is not None else workdir
            if plan is not None and changed(here / 'artifacts', stamp):
                before, stamp = stamp, time.time_ns()
                started = time.monotonic()
                try:
                    publish_report.refill(here, flowdir)
                    restored = False
                except Exception as error:  # noqa: BLE001 - never ends the watcher
                    if not restored:
                        stamp = before
                    restored = not restored
                    line = f'page_watch: refill: {type(error).__name__}: {error}'
                    if line not in said:
                        said.add(line)
                        print(line, file=sys.stderr, flush=True)
                pause = max(every, 2 * (time.monotonic() - started))
            errors = 0
        except Exception:  # noqa: BLE001 - the next wake tries again, up to MAX_ERRORS
            errors += 1
            if errors >= MAX_ERRORS:
                return


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='page_watch.py')
    parser.add_argument('workdir', type=Path)
    parser.add_argument('--flowdir', type=Path, required=True)
    parser.add_argument('--owner', type=int)
    args = parser.parse_args(argv)
    held, busy = take_lock(args.flowdir)
    if busy:
        return 0
    try:
        watch(args.workdir, args.flowdir, args.owner)
    finally:
        if held is not None:
            _release(held, args.flowdir)
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
