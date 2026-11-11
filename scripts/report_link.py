#!/usr/bin/env python3
"""Print the hand-off link to a local report — a link that opens on the reader's surface.

    python3 scripts/report_link.py link "<label>" "$ART/agent_findings.md"   # the whole link line
    python3 scripts/report_link.py url "$ART/report.html"                    # the page to republish in place
    python3 scripts/report_link.py record "$ART/report.html" "<URL>"         # after a publish lands
    python3 scripts/report_link.py forget "$ART/report.html"                 # the page is gone / not ours

WHY THIS EXISTS. The hand-offs used to hand-type `plurai_sim/artifacts/<file>`. That is relative to
the AGENT dir, and no surface resolves a link against it: VS Code joins it onto the workspace cwd,
the terminal onto the shell cwd, claude.ai onto its own origin — and in a named env the file lives
under `plurai_sim/envs/<name>/artifacts/`, so the literal is wrong even from the agent dir.

`link` prints `**[<label>](<target>)**`, where the target is the report's PUBLISHED page when there
is one that still shows what is on disk, and otherwise the report's file: its absolute path (which
VS Code and the Claude Code terminal open as-is), or under Copilot and Codex a `file://` URL, because
their terminals open a link target as a URL and a path has no scheme. It exits 1 and prints nothing when the report does not exist, so a link
to a missing report is never handed out — nor for the overview page when its last fill was refused (``fill_refused``).

THE PAGE MARKER. `record` writes the marker `.artifact_<key>_url` into the workdir — the parent of
the report's `artifacts/` dir — and the key is the report's path under it (`agent_findings`,
`runs/r1/analytics` → `runs_r1_analytics`) — one page per report, so one run's analytics page is
never overwritten by the next run's. Line 2 holds the sha256 of the report when `record` ran: for
`report.html` that is the file at `done`, which a background refill may already have replaced, and
`publish_report.refill` moves it along with a page that was still the published one. A report changed
any other way since then is linked by its path, because the page no longer shows it. `url` ignores that
check — a stale page is still the page to republish IN PLACE.

ONE PUBLISHED PAGE. The overview page (``publish_report.py``, ``report.html``) is the only report
published as an artifact; it records its page through ``record`` / ``recorded`` / ``forget`` here. A
Markdown report is never published: its hand-off prints ``link``, which is then the file's own link —
even beside a page marker an older plugin left for it (``target`` never reads one for a ``.md``).
While a /simulate plan is RUNNING (``_run_driver.autonomous_plan``, not ``halted``, not ``paused``)
that page carries every stage, so a stage's report is GATED (``gated``): ``url``, ``link`` and
``record`` print and write nothing for it — no second link beside the page. ``fixes.md`` is never
gated. A halted, paused or finished run, and a step command run on its own, link every report.

Stdlib-only, like its siblings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / 'hooks'
sys.path.insert(0, str(HOOKS))
import _workdir as session  # noqa: E402 - which harness owns the shell

#: The harnesses whose terminals open a link target as a URL.
FILE_URL_HARNESSES = ('copilot', 'codex')

#: Reports still linked during a running /simulate run: the fix plan, which the overview page does not render — and
#: the overview page itself, whose Stages (e.g. `#gaps`) a hand-off links.
UNGATED = frozenset({'fixes.md', 'report.html'})

#: Keys whose marker predates this script. Benches in the field already hold these, and a new name
#: would publish a second map page beside the one the customer already has.
LEGACY_KEYS = {'agent_topology': 'topology'}

#: What `record` accepts as a page. Anything else — a placeholder the model left unfilled, a local
#: path — would be handed out as a link target on the next `link`.
PAGE = re.compile(r'https://\S+')

#: The overview page's fill record (``publish_report.py fill``), and the skips that mean the fill was refused: the
#: page on disk is then an earlier step's, kept, and never handed out as this step's.
FILL_RECORD = '.report_fill.json'
REFUSED = frozenset({'internal_text', 'fill_refused', 'garbled', 'numbers'})


def fill_refused(home: Path | None) -> bool:
    """Whether the last fill recorded in ``home`` (the workdir, or the flow dir before ``artifacts/``) was refused — or a
    later step skipped before any fill ran (``refused``): the refusal lasts until a fill actually runs."""
    try:
        record = json.loads((home / FILL_RECORD).read_text('utf-8')) if home is not None else None
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and (record.get('skip') in REFUSED or record.get('refused') is True)


def _workdir(report: Path) -> Path | None:
    for parent in report.parents:
        if parent.name == 'artifacts':
            return parent.parent
    return None


def _marker(report: Path) -> Path | None:
    workdir = _workdir(report)
    if workdir is None:
        return None
    rel = report.relative_to(workdir / 'artifacts').with_suffix('')
    key = re.sub(r'[^A-Za-z0-9_-]+', '_', rel.as_posix())
    return workdir / f'.artifact_{LEGACY_KEYS.get(key, key)}_url'


def running_plan(report: Path) -> bool:
    """Whether a /simulate run is RUNNING: ``_run_driver.autonomous_plan`` over ``$FLOWDIR`` (every
    publish block sources ``resolve-workdir.sh``, which exports it), else over the marker dirs the hooks
    read — and neither ``halted`` nor ``paused`` (Esc). A plan that cannot be read answers False: the page
    publishes, as it always did."""
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path above

        flowdir = os.environ.get('FLOWDIR', '').strip()
        bases = [Path(flowdir)] if flowdir else _run_driver.marker_bases(_workdir(report.resolve()))
        plan = _run_driver.autonomous_plan(bases)
    except Exception:  # noqa: BLE001 - best-effort by contract: never block a publish on this read
        return False
    return plan is not None and not plan.get('halted') and not plan.get('paused')


def gated(report: Path) -> bool:
    """Whether ``report`` publishes no page of its own now: a running /simulate run's overview page carries it."""
    return report.name not in UNGATED and running_plan(report)


def _sha(report: Path) -> str:
    return hashlib.sha256(report.read_bytes()).hexdigest()


def recorded(report: Path) -> tuple[str, str | None] | None:
    """The page recorded for ``report`` and the sha256 it was published at, or None."""
    marker = _marker(report)
    if marker is None or not marker.is_file():
        return None
    lines = marker.read_text('utf-8').split()
    if not lines or not PAGE.fullmatch(lines[0]):
        return None
    sha = next((x[len('sha256='):] for x in lines[1:] if x.startswith('sha256=')), None)
    return lines[0], sha


def local(report: Path, harness: str | None = None, anchor: str | None = None) -> str:
    """The report's file as a link target: its absolute path, or under Copilot and Codex a ``file://`` URL.

    Those terminals open a link target as a URL, and a path with no scheme is not one, so the click ends in a
    Finder error. The URL's percent-escapes carry a space or a parenthesis, so it needs no angle form. Only the URL
    carries ``#anchor`` (a fragment is part of a URL); a bare path stays bare, since ``path#anchor`` names no file.
    """
    if (harness or session.harness()) in FILE_URL_HARNESSES:
        fragment = f'#{urllib.parse.quote(anchor, safe="-._~")}' if anchor else ''
        return Path(os.path.abspath(report)).as_uri() + fragment
    path = str(report)
    # A space or a parenthesis ends a bare CommonMark link target; the angle form carries both.
    return f'<{path}>' if re.search(r'[\s()<>]', path) else path


def target(report: Path, anchor: str | None = None, harness: str | None = None) -> str:
    """The page when it still shows what is on disk (with ``#anchor``), else the local target (:func:`local`).

    A Markdown report is always its own file: a page marker left beside one is a page a pre-0.55 plugin
    published before the overview page became the only published page, and linking it would hand the
    reader that retired Markdown artifact."""
    page = None if report.suffix == '.md' else recorded(report)
    if page is not None and page[1] == _sha(report):
        return page[0] + ('' if anchor is None else f'#{anchor}')
    return local(report, harness, anchor)


def record(report: Path, page: str) -> None:
    """Record ``page`` as the report's published page, at the report's current bytes."""
    marker = _marker(report)
    if marker is None:
        raise ValueError(f'{report} is not under an artifacts/ dir, so it has no page marker')
    if not PAGE.fullmatch(page):
        raise ValueError(f'refusing to record {page!r} — not an https page URL')
    marker.write_text(f'{page}\nsha256={_sha(report)}\n', 'utf-8')


def forget(report: Path) -> None:
    marker = _marker(report)
    if marker is not None:
        marker.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p_link = sub.add_parser('link', help='print the whole markdown link line')
    p_link.add_argument('label')
    p_link.add_argument('report', type=Path)
    p_link.add_argument('--anchor', help='the in-page section the link lands on, e.g. suite')
    sub.add_parser('url', help='print the recorded page, to republish in place').add_argument('report', type=Path)
    p_rec = sub.add_parser('record', help='record the page a publish returned')
    p_rec.add_argument('report', type=Path)
    p_rec.add_argument('page')
    sub.add_parser('forget', help='drop the recorded page').add_argument('report', type=Path)
    args = ap.parse_args(argv)

    report = args.report.resolve()
    if args.cmd != 'forget' and not report.is_file():
        print(f'report_link: no such report: {args.report}', file=sys.stderr)
        return 1

    if args.cmd in ('link', 'url', 'record') and gated(report):
        # Nothing to republish, link or record: the running /simulate run's overview page carries this stage.
        return 1 if args.cmd == 'url' else 0
    if args.cmd == 'link':
        if report.name == 'report.html' and fill_refused(_workdir(report)):
            return 1  # the kept page is an earlier step's: never this step's link
        print(f'**[{args.label}]({target(report, args.anchor)})**')
        return 0
    if args.cmd == 'url':
        page = recorded(report)
        if page is None:
            return 1
        print(page[0])
        return 0
    if args.cmd == 'forget':
        if _marker(report) is None:
            print(f'report_link: {args.report} is not under an artifacts/ dir, so it has no page marker', file=sys.stderr)
            return 2
        forget(report)
        return 0
    try:
        record(report, args.page)
    except ValueError as refusal:
        print(f'report_link: {refusal}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
