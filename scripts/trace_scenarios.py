#!/usr/bin/env python3
"""The trace scenarios directory: write the folder, list what is on disk, find what already covers a trace.

A **trace scenario** is a folder under ``plurai_sim/trace_scenarios/`` converted from a production
trace and cut just before the turn a **trace failure** showed on. ``ROLES.md`` opens on the
``## Trace failure:`` paragraph the session writes, and the converter appends its resume section
below it, ending with the **world facts** — the tool calls and results the trace recorded after
the cut, under ``World the live leg needs``. Beside it sit the converter's ``resume_pin.json`` and
``transcript.md``. A trace scenario always resumes: a failure on turn 0 has no history to resume
and gets no folder, so there is no opener-only shape here.

Like a regression, the folder is its own record: its name is ``trace_<slug>``, its presence is
its status, and it reaches a scenario set only through the final batch, which merges this
directory with ``regressions/`` into one bundle (``final_bundle.py``). It carries no blame. Unlike
a regression, the brief that produced it comes back: every later ``/analyze-traces`` chain sees
the same trace failure again. So a retired trace scenario leaves one line in the directory's
``RETIRED`` list — slug, source trace, cut, date and failure — and the chain skips a trace
failure that line names, by its slug or by its trace and cut (``skipped_retired``). A regression is recorded from its run only when the run shows the failure and
``/simulation-fixes`` confirms an agent issue.

Verbs:

``preflight`` whether the brief-driven mode can run here: raw posture, local traces from a source
              the installed converter reads, and a warning when there is no Python harness to
              replay a seed.
``select``    rank a brief's trace failures, skip the ones a folder on disk already records, and
              preselect up to the per-brief cap — none when the final batch is full or the total
              cap is reached.
``candidates`` per selected trace failure, render each sampled trace that is one local file
              (``plurai convert-trace --render-only``), shortest first, each with the folders
              already converted from the same trace.
``plan``      from the cut located in each rendering, what is written without asking, what is
              skipped as already on disk or left out by the total cap, and whether one question is
              needed: an overlap with a folder at another cut, or more located than the final
              batch has room for.
``write``     create ``trace_<slug>`` and its ``ROLES.md`` paragraph, composed from the brief's
              trace failure and the cut, before the conversion — unless the total cap is reached.
``list``      the folders as JSON, each with its shape and the provenance its pin records —
              optionally against the scenario names a set shipped.
``overview``  rewrite the directory's ``BUNDLE.md`` from what is on disk.
``reproduced`` read a finished run's ``run.json`` and say, per trace scenario, whether the
              production failure came back.
``source-of`` which trace scenario, if any, a run's scenario ran as.
``retire``    retire a trace scenario: ``--slug`` removes one the operator no longer wants
              (``git rm -r``) and lists it in ``RETIRED``; ``--record`` lists one already deleted
              by hand; ``--pick``/``--regression`` retires the one a regression recorded from its
              run replaces, when that regression is cut at the seed boundary.

The slug rule, the name cap and the refusal of an unfinished folder are the regressions'
(``regressions.py``), under this directory's prefix: both kinds ride one bundle and reach the
backend's slugifier the same way.

Two caps bound the directory. The per-brief cap limits how many top-ranked trace failures one
run takes; the total cap limits how many ``trace_*`` folders sit on disk. Each is its flag,
``--max-new`` / ``--max-total``, else 5 / 10 (:func:`resolve_caps`). The final batch's room
applies under both. At a full total nothing is written and nothing is replaced or deleted: the
report names the trace failures left out.

The brief-driven verbs never open a trace file. ``candidates`` hands each one to the converter and
reads back only the converter's summary; the session reads the rendering the converter wrote.

Exit codes: 0 success; 1 the directory and a set disagree (``list --shipped-from``); 2 a refusal,
including, from ``list`` and ``overview``, a folder that carries no pin, and from ``retire``, a
record that did not finish.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _confine  # noqa: E402
import plurai_contracts  # noqa: E402
import regressions  # noqa: E402
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

Refused = regressions.Refused
ROLES_FILENAME = regressions.ROLES_FILENAME
PIN_FILENAME = regressions.PIN_FILENAME
OVERVIEW_FILENAME = regressions.OVERVIEW_FILENAME
SCENARIO_NAME_MAX = regressions.SCENARIO_NAME_MAX

SUMMARY = 'The trace scenarios directory: write the folder, list what is on disk, find what already covers a trace.'

#: The directory, relative to the workdir. A sibling of ``regressions/``, tracked in git for the
#: same reason: deleting a folder is how a trace scenario is retired, and a deletion only means
#: something against a history.
TRACE_SCENARIOS_DIRNAME = 'trace_scenarios'

#: Every folder carries it, so a trace scenario and a regression never share a name in the one
#: bundle the final batch uploads, and a walk scenario (``scenario_NN_…``) never looks like one.
FOLDER_PREFIX = 'trace_'

#: The directory's retired list: one line per retired trace scenario. Tracked with the folders,
#: so a clone and every later chain know what the operator retired.
RETIRED_FILENAME = 'RETIRED'
RETIRED_HEADER = (
    '# Retired trace scenarios. The /analyze-traces chain never writes a trace failure listed here\n'
    '# again, by its slug or by its trace and cut. Delete a line to let the chain write it again.\n'
    '# slug<TAB>source trace<TAB>cut turn<TAB>date<TAB>trace failure\n'
)

#: The heading the paragraph opens with, and the rule for when a file opens on it. The converter
#: appends below the paragraph, so this is where every later reader finds what the production
#: conversation went through. The backend reads a folder as a trace scenario by the same heading,
#: so both come from its vendored ``trace_failure_heading`` contract rather than a copy.
_HEADING_CONTRACT: dict[str, Any] = plurai_contracts.contract('trace_failure_heading')
FAILURE_HEADING: str = _HEADING_CONTRACT['heading']
_FAILURE_OPENING = re.compile(_HEADING_CONTRACT['opening_pattern'], _HEADING_CONTRACT['flags'])

SHAPE_RESUMED = regressions.SHAPE_RESUMED
SHAPE_INCOMPLETE = 'incomplete'

#: The three provenance keys the dedup reads off a pin. ``cut_turn`` is absent from an uncut pin.
PROVENANCE_KEYS = ('source_file', 'thread_id', 'cut_turn')

#: The walk count of the delivered batch, which the folders ride beside.
WALK_COUNT = 7

#: The backend's default ``scenario_job_max_count``. The job refuses ``count + folders`` above it.
SCENARIO_JOB_MAX_COUNT = 50

PYTHON_HARNESS = 'simulate.py'

#: The ``--out-dir`` of ``plurai ingest-traces``, relative to the workdir: it holds
#: ``manifest.json`` and the trace files under ``traces/``.
TRACES_DIRNAME = 'traces'
TRACES_MANIFEST = 'manifest.json'

#: The directory under ``TRACES_DIRNAME`` holding one ``<trace_id>.json`` per trace. The archive
#: ingest uploads is rooted here, so the brief names each unit's source files relative to it —
#: a bare ``<trace_id>.json``.
TRACE_FILES_DIRNAME = 'traces'

#: The ingested source every converter reads. ``ingest-traces`` names its sources by the names
#: ``convert-trace --source-format`` gives its readers, so an export's ``source_kind`` IS the reader
#: to dispatch to; a reader beyond this one is feature-detected on the installed SDK
#: (``plurai capabilities`` → ``trace_formats``).
ALWAYS_READABLE_SOURCE_KIND = 'langsmith'

#: Seconds ``plurai capabilities`` may take. It only reads the live parser and registries.
CAPABILITIES_TIMEOUT = 60

#: The per-brief cap: how many top-ranked trace failures one brief-driven run preselects, unless
#: ``--max-new`` names another number.
DEFAULT_MAX_PER_BRIEF = 5

#: The total cap: how many ``trace_*`` folders may sit on disk, unless ``--max-total`` names another
#: number. Every brief failure stays eligible and only the operator deletes a folder, so without it
#: the directory only ever grows.
DEFAULT_MAX_TOTAL = 10

#: The most candidates one confirmation lists: an ``AskUserQuestion`` call carries four
#: questions of four options each.
CONFIRMATION_MAX = 16

#: The fewest user turns a trace needs for a cut at 1 or later: turn 0 to seed, turn 1 to open on.
CUTTABLE_TURNS = 2

#: How many other sampled traces a paragraph names before it counts the rest.
EVIDENCE_TRACES_MAX = 12

#: Seconds one rendering may take. The converter is local and deterministic; a hang is a fault.
RENDER_TIMEOUT = 300


def folders(directory: Path) -> list[Path]:
    return regressions.folders(directory, prefix=FOLDER_PREFIX)


def trace_files_root(workdir: Path) -> Path:
    """The directory the brief's ``source_files`` are relative to: ``<workdir>/traces/traces``."""
    return workdir / TRACES_DIRNAME / TRACE_FILES_DIRNAME


# ── the final batch's room and the harness ────────────────────────────────────


def room(
    *,
    regression_count: int,
    trace_scenario_count: int,
    count: int = WALK_COUNT,
    max_count: int = SCENARIO_JOB_MAX_COUNT,
) -> int:
    """Scenarios the final batch can still carry: ``max_count - count - folders``, negative when over.

    One rule for the stage that holds trace scenarios back and for the brief-driven mode that
    preselects them, so a folder written today is one the next final batch has room for.
    """
    return max_count - count - regression_count - trace_scenario_count


def harness_gap(workdir: Path, harness: Path | None = None) -> str | None:
    """Why no Python harness can be asked about a seed, or ``None`` when one can.

    Whether it TAKES a folder's seed is asked of the harness itself, at the stage
    (``final_bundle.py``, ``verify_smoke.py --seed-resume``). What is left here are the two gaps no
    Init can answer: there is no ``simulate.py`` to run, and a bench manifest that declares the
    harness cannot resume a recorded conversation (``harness.resume: unsupported``, e.g. a bridge
    entry with no ``seed`` export), which asking would only confirm at the cost of a metered session.
    """
    if harness is None:
        harness = workdir / PYTHON_HARNESS
    if not harness.is_file():
        return f'{harness} is not a file, so no harness can be asked whether it takes a seed.'
    return regressions.resume_unsupported(harness.parent)


# ── the brief-driven mode: preflight ──────────────────────────────────────────


def anonymized(workdir: Path) -> bool:
    """Whether this workdir's config declares ``anonymize: true`` (``_run_driver.declared_answer``, the one reader).

    An absent or non-bool key is not ``true``; the converter reads the posture again and refuses on its own.
    """
    hooks = str(Path(__file__).resolve().parent.parent / 'hooks')
    if hooks not in sys.path:
        sys.path.append(hooks)
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path only from here

    return _run_driver.declared_answer('anonymization-posture', workdir) == 'true'


# ── the caps: per brief and in total ──────────────────────────────────────────


def _cap_value(raw: int, *, flag: str) -> int:
    if raw < 1:
        raise Refused(f'{flag} is {raw}; a cap is a whole number of 1 or more.')
    return raw


def resolve_caps(*, max_new: int | None = None, max_total: int | None = None) -> dict[str, int]:
    """The per-brief and total caps in force: each flag, else its default (5 and 10)."""
    return {
        'max_per_brief': DEFAULT_MAX_PER_BRIEF if max_new is None else _cap_value(max_new, flag='--max-new'),
        'max_total': DEFAULT_MAX_TOTAL if max_total is None else _cap_value(max_total, flag='--max-total'),
    }


def total_left(max_total: int, on_disk: int) -> int:
    """How many more ``trace_*`` folders the total cap allows: ``max_total - on_disk``, negative when over."""
    return max_total - on_disk


def cap_line(*, max_total: int, on_disk: int, not_written: list[str]) -> str:
    """The one report line for a run the full total cap wrote nothing in."""
    count = len(not_written)
    return (
        f'Trace scenarios: 0 written — {on_disk} already on disk (cap {max_total}); delete some or raise the cap: '
        f'{count} failure{"" if count == 1 else "s"} not written: {", ".join(not_written)}.'
    )


def _total_reason(max_total: int, on_disk: int) -> str:
    return f'the total cap of {max_total} trace scenario(s) on disk is reached ({on_disk} already there)'


def _total_refusal(max_total: int, on_disk: int) -> str:
    return (
        f'{on_disk} trace scenario(s) are already on disk and the total cap is {max_total}, so no folder is written. '
        'Delete a folder you no longer need, or raise the cap with --max-total N.'
    )


def _require_resolved_runner(plurai: str) -> None:
    """Refuse a ``--plurai`` the resolver would not have chosen: the converter runs with the caller's environment."""
    import resolve_plurai  # noqa: PLC0415 - sibling script; only the paths that run the converter need it

    if (why := resolve_plurai.explicit_runner_refusal(plurai)) is not None:
        raise Refused(f'--plurai {plurai} was not run: {why}')


def _reader_formats(plurai: str) -> list[str] | None:
    """The installed SDK's built-in ``convert-trace`` readers, or ``None`` when it does not say.

    ``None`` covers an SDK whose ``capabilities`` predates ``trace_formats`` and one that cannot be
    run at all; either way nothing beyond :data:`ALWAYS_READABLE_SOURCE_KIND` is known to be read.
    """
    _require_resolved_runner(plurai)
    try:
        proc = subprocess.run([plurai, 'capabilities'], capture_output=True, text=True, **utf8_child_text(), timeout=CAPABILITIES_TIMEOUT, check=False)
        payload = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    formats = payload.get('trace_formats') if isinstance(payload, dict) else None
    return [name for name in formats if isinstance(name, str)] if isinstance(formats, list) else None


def preflight(workdir: Path, *, plurai: str) -> dict[str, Any]:
    """What the brief-driven mode needs before it reads a brief, or a :class:`Refused` naming what is missing.

    Refuses an anonymized env, a workdir with no local traces, and traces from a source the
    installed converter has no reader for — the export's ``source_kind`` is the reader the
    conversion dispatches to. No Python harness is a warning only: the folders convert without
    one, and the final batch holds back every folder whose seed the harness does not take.
    """
    if anonymized(workdir):
        raise Refused(
            'trace-to-scenario is raw-only for now: this env declares `anonymize: true`, and a trace scenario carries '
            'the production conversation verbatim. Nothing was converted.'
        )
    traces_dir = workdir / TRACES_DIRNAME
    manifest_path = traces_dir / TRACES_MANIFEST
    if not traces_dir.is_dir():
        raise Refused(
            f'{traces_dir} does not exist, so the traces the brief was built from are not on this machine. Re-ingest '
            'with `plurai ingest-traces` (the /analyze-traces export) into it, then run this again.'
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(
            f'{manifest_path} could not be read ({type(exc).__name__}), so which sink these traces came from is unknown. '
            'Re-ingest with `plurai ingest-traces`, then run this again.'
        ) from exc
    kind = manifest.get('source_kind') if isinstance(manifest, dict) else None
    if not isinstance(kind, str) or not kind:
        raise Refused(f'{manifest_path} names no source_kind, so which reader converts these traces is unknown. Re-ingest with `plurai ingest-traces`.')
    if kind != ALWAYS_READABLE_SOURCE_KIND:
        formats = _reader_formats(plurai)
        if formats is None or kind not in formats:
            known = 'does not report its readers' if formats is None else f'reads {formats}'
            raise Refused(
                f'these traces came from source_kind {kind!r}, and the installed plurai SDK has no converter reader for it '
                f'(it {known}), so no trace scenario can be written from them. The brief stands and its walk scenarios '
                'still cover each trace failure.'
            )
    if not trace_files_root(workdir).is_dir():
        raise Refused(
            f'{trace_files_root(workdir)} does not exist, so the traces the brief was built from are not on this machine. '
            'Re-ingest with `plurai ingest-traces` (the /analyze-traces export) into '
            f'{traces_dir}, then run this again.'
        )
    trace_count = manifest.get('trace_count')
    return {
        'traces_dir': str(traces_dir),
        'trace_files': str(trace_files_root(workdir)),
        'source_kind': kind,
        'trace_count': trace_count if isinstance(trace_count, int) and not isinstance(trace_count, bool) else None,
        'seed_state_warning': harness_gap(workdir),
    }


# ── the brief-driven mode: choosing trace failures ────────────────────────────


def failure_slug(name: str) -> str | None:
    """The folder slug for a trace failure name, or ``None`` when the name holds no ascii word.

    Lowercase ascii words joined by single underscores, cut on a word boundary so ``trace_<slug>``
    fits the scenario-name cap. A leading ``trace`` word is dropped: the folder prefix already
    says it, and ``require_slug`` refuses a slug that repeats the prefix.
    """
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    words = re.findall(r'[a-z0-9]+', ascii_name.lower())
    if words and words[0] == 'trace':
        words = words[1:]
    limit = SCENARIO_NAME_MAX - len(FOLDER_PREFIX)
    slug = ''
    for word in words:
        joined = f'{slug}_{word}' if slug else word
        if len(joined) > limit:
            break
        slug = joined
    if not slug and words:
        slug = words[0][:limit]
    return slug or None


def failure_opening(text: str) -> str | None:
    """The heading line ``text`` opens on when it is the ``## Trace failure:`` heading, else ``None``.

    The backend's opening rule, read from its contract: one optional byte-order mark, any
    whitespace, then the heading. Only the opening counts; the heading further down is not one.
    """
    match = _FAILURE_OPENING.match(text)
    if match is None:
        return None
    return text[match.end() - len(FAILURE_HEADING) :].splitlines()[0].rstrip()


def folder_failure(folder: Path) -> str | None:
    """The trace failure a folder records, off its ``## Trace failure:`` heading.

    ``None`` when there is no such folder; ``''`` when it exists but opens on no such heading,
    so a caller never reads a broken folder as the failure it is looking for.
    """
    if not folder.is_dir():
        return None
    try:
        text = (folder / ROLES_FILENAME).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return ''
    opening = failure_opening(text)
    return _one_line(opening[len(FAILURE_HEADING):]) if opening is not None else ''


class Retired(NamedTuple):
    """One line of the retired list."""

    slug: str
    source_file: str
    cut_turn: int | None
    date: str
    failure: str


def read_retired(directory: Path) -> list[Retired]:
    """The directory's retired list, in file order; empty when there is none.

    A line that does not read is refused rather than skipped: skipping it would let the chain
    write again what the operator retired.
    """
    path = directory / RETIRED_FILENAME
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise Refused(f'{path} could not be read ({type(exc).__name__}: {exc}).') from exc
    entries: list[Retired] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split('\t')
        cut = fields[2].strip() if len(fields) > 2 else ''
        if len(fields) < 4 or not fields[0].strip() or not (cut.isdigit() or cut == '-'):
            raise Refused(
                f'{path} line {number} does not read as slug, source trace, cut turn and date separated by tabs. '
                'Fix or delete the line; until then the chain cannot tell what is retired.'
            )
        entries.append(Retired(
            slug=fields[0].strip().removeprefix(FOLDER_PREFIX),
            source_file=fields[1].strip(),
            cut_turn=int(cut) if cut.isdigit() else None,
            date=fields[3].strip(),
            failure=_one_line(fields[4]) if len(fields) > 4 else '',
        ))
    return entries


def retired_failure(entries: list[Retired], slug: str, failure: str) -> Retired | None:
    """The line retiring this trace failure: one that names it, under any slug, or failing that a
    line under this slug that names no failure at all.

    A slug is only a name, so a line matched on the failure holds whatever slug the failure wore
    when it was retired. A line that names no failure (its folder stated none) can only be matched
    on its slug.
    """
    wanted = _one_line(failure)
    named = next((e for e in entries if e.failure and e.failure == wanted), None)
    return named or next((e for e in entries if e.slug == slug and not e.failure), None)


def retired_by_trace(entries: list[Retired], source_file: str, cut_turn: int) -> Retired | None:
    """The line retiring this trace at this cut, the trace matched by file name as ``existing`` does."""
    wanted = Path(source_file).name
    return next((e for e in entries if e.source_file and Path(e.source_file).name == wanted and e.cut_turn == cut_turn), None)


def record_retired(directory: Path, entry: Retired) -> str:
    """Append ``entry`` to the retired list, creating it with its header, and return the line.

    A line already present is not written twice. Inside a git work tree the list is staged, so the
    retirement and the record of it land in one commit.
    """
    path = directory / RETIRED_FILENAME
    line = '\t'.join([
        entry.slug, entry.source_file, '-' if entry.cut_turn is None else str(entry.cut_turn), entry.date, _one_line(entry.failure),
    ])
    known = {(e.slug, e.source_file, e.cut_turn) for e in read_retired(directory)}
    if (entry.slug, entry.source_file, entry.cut_turn) not in known:
        directory.mkdir(parents=True, exist_ok=True)
        existing_text = path.read_text(encoding='utf-8') if path.is_file() else RETIRED_HEADER
        if existing_text and not existing_text.endswith('\n'):
            existing_text += '\n'
        path.write_text(existing_text + line + '\n', encoding='utf-8')
    inside = _git(directory, 'rev-parse', '--is-inside-work-tree')
    if inside is not None and inside.returncode == 0 and inside.stdout.strip() == 'true':
        _git(directory, 'add', '--', RETIRED_FILENAME)
    return line


def _strings(value: Any) -> list[str]:
    """A plural brief field as a list of non-empty strings; a bare string is one value."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def read_failures(path: Path) -> dict[str, Any]:
    """The JSON ``get_trace_failures`` returned, checked for the shape the verbs read."""
    try:
        doc = json.loads(_read_arg(path))
    except (OSError, ValueError) as exc:
        raise Refused(
            f'{path} could not be read as JSON ({type(exc).__name__}). Save the object get_trace_failures returned, '
            'verbatim, and pass that file.'
        ) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get('failures'), list):
        raise Refused(f'{path} carries no failures list, so it is not what get_trace_failures returns.')
    if doc.get('present') is not True:
        raise Refused(
            f'{path} says no trace brief is present for workspace {doc.get("workspace_id")!r}. Run /analyze-traces for '
            'this bench first, or pass the workspace the brief was published under.'
        )
    names = [entry.get('name') for entry in doc['failures'] if isinstance(entry, dict)]
    if len(names) != len(doc['failures']) or not all(isinstance(name, str) and name.strip() for name in names):
        raise Refused(f'{path}: every trace failure needs a name, and at least one has none.')
    return doc


def split_units(failure: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The sampled units that name exactly one input file, and the rest with the reason, in sample order.

    A unit built from several files (a row-level adapter's rows, a multi-trace unit) is not one
    conversation the converter can cut.
    """
    single: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for unit in failure.get('units') or []:
        if not isinstance(unit, dict):
            continue
        files = _strings(unit.get('source_files'))
        count = unit.get('source_file_count')
        count = count if isinstance(count, int) and not isinstance(count, bool) else len(files)
        if len(files) == 1 and count == 1:
            single.append({'unit_id': unit.get('unit_id'), 'source_file': files[0]})
        else:
            rest.append({'unit_id': unit.get('unit_id'), 'reason': f'the unit names {count} input files, not one trace'})
    return single, rest


def _suffixed(slug: str, taken: set[str]) -> str:
    """``slug`` itself, or ``slug_2``, ``slug_3``… — the first not in ``taken``, within the name cap.

    Two trace failures whose names slugify alike each get a folder; the louder one keeps the plain
    name, so a later brief ranking them the same way names them the same way. The base is cut at a
    word boundary when the suffix would push the folder name past the cap.
    """
    if slug not in taken:
        return slug
    limit = SCENARIO_NAME_MAX - len(FOLDER_PREFIX)
    number = 2
    while True:
        suffix = f'_{number}'
        base = slug
        while len(base) + len(suffix) > limit and '_' in base:
            base = base.rsplit('_', 1)[0]
        candidate = f'{base[: limit - len(suffix)]}{suffix}'
        if candidate not in taken:
            return candidate
        number += 1


def select(
    doc: dict[str, Any],
    workdir: Path,
    *,
    max_new: int | None = None,
    max_total: int | None = None,
    named: list[str] | None = None,
    hand_pick: tuple[str, int] | None = None,
) -> dict[str, Any]:
    """Which trace failures get a trace scenario this time, and why each other one does not.

    Ranked by ``unit_count``, loudest first, ties in the brief's own order. A trace failure whose
    folder is already on disk — a folder under its slug whose heading names this same failure — is
    skipped: presence is status, and a new brief only adds. So is one the retired list names
    (``skipped_retired``): the operator retired it, and the brief keeps showing it. Both are read
    by the failure a folder or a line records, never by the slug alone, since the slug a failure
    gets depends on the ranking and on what was on disk when its folder was written. A folder under the slug that records
    another failure is a clash of names, not coverage: this failure takes the next suffixed slug,
    and ``plan`` decides whether its conversation duplicates a folder, by trace and cut.
    Sharing a trace with a folder is not a skip here — one trace can show two failures at two
    turns, and whether a candidate duplicates a folder is known only once its cut is (``write``
    refuses the same trace at the same cut). The rest are preselected up to the per-brief cap
    (:func:`resolve_caps`) and the most one confirmation lists; none when the final batch has no
    room left after the walk, the regressions and the trace scenarios already on disk, and none
    when the total cap is reached. A room or a total short of the preselection does not trim it:
    which trace failures locate is known only once the renderings are read, and :func:`plan`
    trims to the total and asks the operator to choose when more locate than the room holds.
    ``named`` takes those trace failures instead of the top-ranked ones and takes no ``max_new``.
    ``hand_pick`` is the operator's own trace and cut for the one named trace failure; it is
    refused outright at a full total, since the one folder it asks for cannot be written.
    """
    if named is not None and max_new is not None:
        raise Refused('--max-new limits the top-ranked trace failures; with --failure the names are the list. Pass one or the other.')
    caps = resolve_caps(max_new=max_new, max_total=max_total)
    failures = [entry for entry in doc['failures'] if isinstance(entry, dict)]
    ranked = sorted(failures, key=lambda entry: -_count(entry.get('unit_count')))
    if named is not None:
        known = {entry['name'] for entry in ranked}
        unknown = [name for name in named if name not in known]
        if unknown:
            raise Refused(
                f'the brief has no trace failure named {", ".join(repr(n) for n in unknown)}. Its trace failures are: '
                + ', '.join(repr(entry['name']) for entry in ranked)
            )
        ranked = [entry for entry in ranked if entry['name'] in set(named)]
    if hand_pick is not None:
        if named is None or len(named) != 1:
            raise Refused('--trace and --cut-turn pick one trace for one trace failure: name exactly one --failure.')
        if hand_pick[1] < 1:
            raise Refused(
                f'--cut-turn {hand_pick[1]}: a trace scenario resumes from history, so its cut is turn 1 or later. A '
                'failure on turn 0 is covered by the walk and gets no folder.'
            )

    directory = workdir / TRACE_SCENARIOS_DIRNAME
    on_disk = [folder.name for folder in folders(directory)]
    left = total_left(caps['max_total'], len(on_disk))
    if hand_pick is not None and left <= 0:
        raise Refused(_total_refusal(caps['max_total'], len(on_disk)))
    regression_count = len(regressions.folders(workdir / regressions.REGRESSIONS_DIRNAME))
    free = room(regression_count=regression_count, trace_scenario_count=len(on_disk))
    wanted = caps['max_per_brief']
    room_reason = (
        f'the final batch has room for {max(free, 0)} more folder(s) beside the walk, {regression_count} '
        f'regression(s) and {len(on_disk)} trace scenario(s)'
    )
    bounds = [(CONFIRMATION_MAX, f'over what one confirmation lists ({CONFIRMATION_MAX})')]
    if named is None:
        bounds.append((wanted, f'over the per-brief cap of {wanted}'))
    if free <= 0:
        bounds.append((0, room_reason))
    if left <= 0:
        bounds.append((0, _total_reason(caps['max_total'], len(on_disk))))
    cap, reason = min(bounds, key=lambda bound: bound[0])

    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    retired_skips: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    taken: set[str] = set()
    retired = read_retired(directory)
    # Which folder records each failure, whatever slug it holds: a slug depends on the ranking and
    # on what was on disk when the folder was written, and the failure it records does not.
    recorded: dict[str, str] = {}
    for folder in folders(directory):
        if held := folder_failure(folder):
            recorded.setdefault(held, folder.name)
    for entry in ranked:
        name = entry['name']
        natural = failure_slug(name)
        if natural is None:
            unusable.append({'failure': name, 'reason': 'its name holds no ascii word to name a folder by'})
            continue
        held_by = recorded.get(_one_line(name))
        if held_by is not None:
            skipped.append({'failure': name, 'slug': held_by.removeprefix(FOLDER_PREFIX), 'folder': held_by, 'matched_on': ['failure']})
            continue
        slug = _suffixed(natural, taken)
        # A folder under this slug that records another failure only shares the name.
        while (owner := folder_failure(directory / f'{FOLDER_PREFIX}{slug}')) is not None and owner != _one_line(name):
            taken.add(slug)
            slug = _suffixed(natural, taken)
        taken.add(slug)
        covered = existing(directory, slug=slug)
        if covered:
            skipped.append({'failure': name, 'slug': slug, **covered[0]})
            continue
        gone = retired_failure(retired, slug, name)
        if gone is not None:
            retired_skips.append({'failure': name, 'slug': gone.slug, 'folder': f'{FOLDER_PREFIX}{gone.slug}', 'retired_on': gone.date})
            continue
        eligible.append({
            'failure': name,
            'slug': slug,
            'folder': f'{FOLDER_PREFIX}{slug}',
            'unit_count': _count(entry.get('unit_count')),
            'caught_by_check': _strings(entry.get('caught_by_check')),
            'evidence': _strings(entry.get('evidence')),
            'revealing_marker': _strings(entry.get('revealing_marker')),
            'units': [unit for unit in entry.get('units') or [] if isinstance(unit, dict)],
            **({'hand_pick': {'source_file': hand_pick[0], 'cut_turn': hand_pick[1]}} if hand_pick is not None else {}),
        })

    # The report line belongs to the total cap alone: with the final batch full as well, raising
    # the cap writes nothing, and the room's own reason is the one to act on.
    full_total = left <= 0 and free > 0 and bool(eligible)
    return {
        'workspace_id': doc.get('workspace_id'),
        'generated_at': doc.get('generated_at'),
        'warning': doc.get('warning') or '',
        'room': free,
        'limit': cap,
        'caps': caps,
        'total': {'cap': caps['max_total'], 'on_disk': len(on_disk), 'left': max(left, 0)},
        'existing_folders': on_disk,
        'selected': eligible[:cap],
        'not_selected': [{'failure': e['failure'], 'slug': e['slug'], 'reason': reason} for e in eligible[cap:]],
        'skipped_existing': skipped,
        'skipped_retired': retired_skips,
        'unusable': unusable,
        'cap_line': cap_line(max_total=caps['max_total'], on_disk=len(on_disk), not_written=[e['failure'] for e in eligible])
        if full_total
        else None,
    }


# ── the brief-driven mode: rendering the candidate traces ─────────────────────


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _converter_summary(stdout: str) -> dict[str, Any] | None:
    """The converter's JSON summary: the last stdout line that parses as an object, or ``None``."""
    for line in reversed(stdout.strip().splitlines()):
        try:
            summary = json.loads(line)
        except ValueError:
            continue
        return summary if isinstance(summary, dict) else None
    return None


def _skip_finding(summary: dict[str, Any] | None, source_file: str) -> dict[str, Any] | None:
    """The finding the converter skipped ``source_file`` under, or ``None`` when it was not skipped.

    The summary names each skipped thread in ``skipped`` (``source_file``, ``gate_id``) and carries
    its finding in ``gate_findings``, with the file as the finding's ``subject``.
    """
    if summary is None:
        return None
    skipped = summary.get('skipped')
    for skip in skipped if isinstance(skipped, list) else []:
        if not isinstance(skip, dict) or skip.get('source_file') != source_file:
            continue
        gate_id = skip.get('gate_id')
        findings = summary.get('gate_findings')
        for finding in findings if isinstance(findings, list) else []:
            if isinstance(finding, dict) and finding.get('gate_id') == gate_id and finding.get('subject') == source_file:
                return finding
        return {'gate_id': gate_id, 'subject': source_file}
    return None


def source_format(workdir: Path) -> str:
    """The ``--source-format`` the ingested traces convert under: the export manifest's ``source_kind``.

    Read again here rather than carried from the preflight, so a step run on its own dispatches on
    the export actually on disk. A manifest naming none falls to the reader every SDK has; the
    preflight already refused that manifest, so this only spares a crash.
    """
    try:
        manifest = json.loads((workdir / TRACES_DIRNAME / TRACES_MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ALWAYS_READABLE_SOURCE_KIND
    kind = manifest.get('source_kind') if isinstance(manifest, dict) else None
    return kind if isinstance(kind, str) and kind else ALWAYS_READABLE_SOURCE_KIND


def render(plurai: str, export: Path, out_dir: Path, *, workspace_root: Path, reader: str) -> dict[str, Any]:
    """Render one trace uncut with ``convert-trace --render-only``: ``{transcript, user_turns}`` or ``{error}``.

    Only the converter's JSON summary is read back; the rendering itself is the session's to read.
    A trace the converter skips comes back as ``{error, finding}``: the error is the finding's
    remedy, and the finding is the session's to decide on, never the customer's to read. A
    non-zero exit with no summary is a converter too old to report per-thread skips refusing this
    trace: the command's flags are fixed and the posture was checked at preflight.
    """
    _require_resolved_runner(plurai)
    command = [
        plurai, 'convert-trace',
        '--workspace-root', str(workspace_root),
        '--export', str(export),
        '--source-format', reader,
        '--out-dir', str(out_dir),
        '--render-only',
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, **utf8_child_text(), timeout=RENDER_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'error': f'the converter could not run ({type(exc).__name__}: {exc})'}
    summary = _converter_summary(proc.stdout)
    finding = _skip_finding(summary, export.name)
    if finding is not None:
        return {'error': finding.get('remedy') or f'the converter skipped it under {finding["gate_id"]}', 'finding': finding}
    if proc.returncode != 0:
        return {
            'error': f'the converter refused this trace (exit {proc.returncode}) without a summary: the installed plurai SDK is too old to '
            'report per-thread skips. Upgrade the SDK in the client venv and render again.'
        }
    rows = summary.get('scenarios') if summary is not None else None
    if summary is None or summary.get('render_only') is not True or not isinstance(rows, list) or len(rows) != 1:
        return {'error': 'the converter printed no render-only summary for one export, so this SDK cannot render'}
    row = rows[0] if isinstance(rows[0], dict) else {}
    transcript, turns = row.get('transcript'), row.get('user_turns')
    if not isinstance(transcript, str) or not Path(transcript).is_file():
        return {'error': 'the converter named no rendering it wrote'}
    if not isinstance(turns, int) or isinstance(turns, bool):
        return {'error': 'the converter reported no user_turns for the rendering'}
    return {'transcript': transcript, 'user_turns': turns}


def candidates(selection: dict[str, Any], workdir: Path, out_dir: Path, *, plurai: str = 'plurai') -> dict[str, Any]:
    """Per selected trace failure, the renderings the session reads to find the cut, in reading order.

    Keeps each sampled unit that names exactly one file present under the ingested trace files
    (:func:`trace_files_root`), renders it into ``out_dir/<slug>/`` (cleared first; it is
    scratch), and drops a trace the converter skips, with the finding it skipped under. Each row
    carries ``export``, the absolute path the conversion reads, and ``overlaps``, the folders
    already converted from the same trace with their cuts. Order: traces with a turn 1 to cut at, fewest user turns first, then
    the single-turn ones, which can only show a failure on turn 0. A hand pick renders its one
    trace and is dropped when its cut is past the last user turn.
    """
    root = trace_files_root(workdir)
    folders_dir = workdir / TRACE_SCENARIOS_DIRNAME
    for guarded in (workdir / TRACES_DIRNAME, folders_dir):
        if _within(out_dir, guarded) or _within(guarded, out_dir):
            raise Refused(f'--out-dir {out_dir} overlaps {guarded}. Render into scratch: "$ART/trace_candidates".')
    reader = source_format(workdir)
    out: list[dict[str, Any]] = []
    for entry in selection.get('selected') or []:
        slug = entry['slug']
        pick = entry.get('hand_pick')
        if pick is not None:
            wanted, dropped = [{'unit_id': None, 'source_file': pick['source_file']}], []
        else:
            wanted, dropped = split_units(entry)

        target = out_dir / slug
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        rendered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for unit in wanted:
            source_file = unit['source_file']
            if source_file in seen:
                continue
            seen.add(source_file)
            export = root / source_file
            if Path(source_file).is_absolute() or not _within(export, root):
                dropped.append({'source_file': source_file, 'reason': f'not a path under {root}'})
                continue
            if not export.is_file():
                dropped.append({'source_file': source_file, 'reason': f'not found under {root}'})
                continue
            result = render(plurai, export, target, workspace_root=workdir, reader=reader)
            if 'error' in result:
                dropped.append({'source_file': source_file, 'reason': result['error'], **({'finding': result['finding']} if 'finding' in result else {})})
                continue
            row = {
                'source_file': source_file,
                'export': str(export.resolve()),
                **result,
                'cuttable': result['user_turns'] >= CUTTABLE_TURNS,
                'overlaps': overlaps(folders_dir, source_file),
            }
            if pick is not None and not 1 <= pick['cut_turn'] < result['user_turns']:
                dropped.append({
                    'source_file': source_file,
                    'reason': f'--cut-turn {pick["cut_turn"]} is not a turn to cut at: the rendering holds '
                    f'{result["user_turns"]} user turn(s), numbered 0 to {result["user_turns"] - 1}, and a cut needs 1 or later',
                })
                continue
            rendered.append(row)
        rendered.sort(key=lambda row: (not row['cuttable'], row['user_turns']))
        out.append({
            'failure': entry['failure'],
            'slug': slug,
            'folder': entry['folder'],
            'unit_count': entry.get('unit_count', 0),
            'caught_by_check': entry.get('caught_by_check', []),
            'evidence': entry.get('evidence', []),
            'revealing_marker': entry.get('revealing_marker', []),
            **({'hand_pick': pick} if pick is not None else {}),
            'candidates': rendered,
            'dropped': dropped,
        })
    return {'out_dir': str(out_dir), 'failures': out}


# ── the brief-driven mode: the plan ───────────────────────────────────────────


def _located_rows(doc: dict[str, Any], located: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    """Each located cut matched to its rendering, in the candidates' rank order.

    Refused when a row names a slug ``candidates`` did not render, a trace that is not one of
    that failure's renderings, a cut outside 1 to the last user turn, or a second cut for one
    trace failure.
    """
    by_slug = {entry['slug']: entry for entry in doc.get('failures') or [] if isinstance(entry, dict) and 'slug' in entry}
    chosen: dict[str, dict[str, Any]] = {}
    for slug, source_file, cut_turn in located:
        entry = by_slug.get(slug)
        if entry is None:
            raise Refused(
                f'no candidate trace failure with slug {slug!r}. The candidates are: '
                + (', '.join(repr(s) for s in by_slug) or 'none')
            )
        if slug in chosen:
            raise Refused(f'{slug!r} is located twice. A trace failure gets one trace scenario: pass one --located for it.')
        rows = {row['source_file']: row for row in entry.get('candidates') or [] if isinstance(row, dict)}
        row = rows.get(source_file)
        if row is None:
            raise Refused(
                f'{source_file} is not a rendering of {entry["failure"]!r}. Its renderings are: '
                + (', '.join(rows) or 'none')
            )
        turns = row.get('user_turns')
        if cut_turn < 1 or (isinstance(turns, int) and cut_turn >= turns):
            raise Refused(
                f'cut turn {cut_turn} is not a turn to cut at in {source_file}: the rendering holds {turns} user turn(s), '
                f'numbered 0 to {turns - 1 if isinstance(turns, int) else "?"}, and a cut needs 1 or later.'
            )
        pick = entry.get('hand_pick')
        chosen[slug] = {
            'failure': entry['failure'],
            'slug': slug,
            'folder': entry['folder'],
            'unit_count': entry.get('unit_count', 0),
            'source_file': source_file,
            'export': row['export'],
            'cut_turn': cut_turn,
            'hand_picked': isinstance(pick, dict) and pick.get('source_file') == source_file,
        }
    return [chosen[slug] for slug in by_slug if slug in chosen]


def plan(
    doc: dict[str, Any], workdir: Path, located: list[tuple[str, str, int]], *, max_total: int | None = None,
) -> dict[str, Any]:
    """What B8 writes from the located cuts, and whether the operator must decide anything first.

    The folders on disk, the final batch's room and the total cap are read again here, since they
    are what the write will meet. A candidate whose trace and cut a folder already carries, or a
    louder candidate in this plan carries, is the same conversation: skipped, never asked about.
    So is one whose trace and cut the retired list names (``skipped_retired``).
    The total cap then keeps the loudest candidates that fit under it and reports the rest as
    ``not_written``, without a question, so a headless run behaves as an attended one; at a full
    total nothing is written and ``cap_line`` is the report line. Everything else is written
    without a question, unless a decision is needed: a candidate whose trace a folder took at
    another cut (``overlap``, asked about per candidate), or more candidates than the room holds
    (``over_room``, the operator picks at most ``pick_at_most`` of them). ``default`` is every
    candidate within the room in rank order — the answer when nobody is at the terminal.
    """
    caps = resolve_caps(max_total=max_total)
    directory = workdir / TRACE_SCENARIOS_DIRNAME
    on_disk = folders(directory)
    regression_count = len(regressions.folders(workdir / regressions.REGRESSIONS_DIRNAME))
    free = room(regression_count=regression_count, trace_scenario_count=len(on_disk))
    left = total_left(caps['max_total'], len(on_disk))

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    retired_skips: list[dict[str, Any]] = []
    kept: dict[tuple[str, int], str] = {}
    retired = read_retired(directory)
    for row in _located_rows(doc, located):
        gone = retired_by_trace(retired, row['source_file'], row['cut_turn'])
        if gone is not None:
            retired_skips.append(
                {key: row[key] for key in ('failure', 'slug', 'source_file', 'cut_turn')}
                | {'folder': f'{FOLDER_PREFIX}{gone.slug}', 'retired_on': gone.date}
            )
            continue
        shared = overlaps(directory, row['source_file'])
        same = [entry['folder'] for entry in shared if entry['cut_turn'] == row['cut_turn']]
        # A louder candidate at this trace and cut, earlier in rank order, carries it as well.
        earlier = kept.get((Path(row['source_file']).name, row['cut_turn']))
        if same or earlier:
            covering = same[0] if same else earlier
            skipped.append({key: row[key] for key in ('failure', 'slug', 'source_file', 'cut_turn')} | {'folder': covering})
            continue
        kept[(Path(row['source_file']).name, row['cut_turn'])] = row['folder']
        rows.append(row | {'overlaps': shared})

    out: dict[str, Any] = {
        'room': free,
        'caps': caps,
        'total': {'cap': caps['max_total'], 'on_disk': len(on_disk), 'left': max(left, 0)},
        'needs_confirmation': False,
        'reasons': [],
        'write': rows,
        'ask': [],
        'pick_at_most': None,
        'default': rows,
        'skipped_existing': skipped,
        'skipped_retired': retired_skips,
        'not_written': [],
        'cap_line': None,
    }
    if free <= 0:
        reason = (
            f'the final batch has room for 0 more folder(s) beside the walk, {regression_count} regression(s) and '
            f'{len(on_disk)} trace scenario(s)'
        )
        out.update(write=[], default=[], not_written=[{'failure': r['failure'], 'slug': r['slug'], 'reason': reason} for r in rows])
        return out
    total_reason = _total_reason(caps['max_total'], len(on_disk))
    over_total = [{'failure': r['failure'], 'slug': r['slug'], 'reason': total_reason} for r in rows[max(left, 0) :]]
    if left <= 0:
        out.update(write=[], default=[], not_written=over_total)
        if over_total:
            out['cap_line'] = cap_line(
                max_total=caps['max_total'], on_disk=len(on_disk), not_written=[r['failure'] for r in rows],
            )
        return out
    rows = rows[:left]
    out.update(write=rows, default=rows, not_written=over_total)
    reasons: list[dict[str, Any]] = [
        {'reason': 'overlap', 'slug': r['slug'], 'source_file': r['source_file'], 'cut_turn': r['cut_turn'], 'overlaps': r['overlaps']}
        for r in rows
        if r['overlaps']
    ]
    if len(rows) > free:
        reasons.append({'reason': 'over_room', 'located': len(rows), 'room': free})
        out.update(write=[], ask=rows, pick_at_most=free, default=rows[:free])
    elif reasons:
        out.update(write=[r for r in rows if not r['overlaps']], ask=[r for r in rows if r['overlaps']])
    out.update(needs_confirmation=bool(reasons), reasons=reasons)
    return out


# ── the brief-driven mode: the paragraph ──────────────────────────────────────


def _one_line(text: str) -> str:
    return ' '.join(text.split())


def _code(text: str) -> str:
    """Inline code for a brief value, with a fence longer than any backtick run inside it."""
    text = _one_line(text)
    run = max((len(m) for m in re.findall(r'`+', text)), default=0)
    fence = '`' * (run + 1)
    pad = ' ' if text.startswith('`') or text.endswith('`') else ''
    return f'{fence}{pad}{text}{pad}{fence}'


def _quote(text: str) -> list[str]:
    """A brief value as a blockquote, so no line of it can open a heading or a fence in ``ROLES.md``."""
    return [f'> {line}'.rstrip() for line in text.strip().splitlines()]


def find_failure(doc: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in doc['failures']:
        if isinstance(entry, dict) and entry.get('name') == name:
            return entry
    raise Refused(
        f'the brief has no trace failure named {name!r}. Its trace failures are: '
        + ', '.join(repr(entry.get('name')) for entry in doc['failures'] if isinstance(entry, dict))
    )


def sampled_files(failure: dict[str, Any]) -> list[str]:
    """Every distinct source file the trace failure's sampled units name, in sample order."""
    out: list[str] = []
    for unit in failure.get('units') or []:
        if isinstance(unit, dict):
            out += [f for f in _strings(unit.get('source_files')) if f not in out]
    return out


def failure_paragraph(
    failure: dict[str, Any], *, source_file: str, cut_turn: int, at_cut: str, hand_picked: bool = False,
) -> str:
    """The ``## Trace failure:`` paragraph ``ROLES.md`` opens on, composed from the brief and the cut.

    It states what production showed and assigns no blame: the check that caught it, the brief's
    evidence quoted as recorded, how many units showed it, the analysis's inferred labels marked
    as inferred, the cut, the one line the authoring session derives the scenario's target from,
    and the other sampled traces as evidence.

    ``source_file`` is one of the trace failure's sampled traces, or, with ``hand_picked``, any
    trace the operator chose — the brief samples at most twelve units, and the operator may know
    another conversation that shows the failure. The paragraph then says the brief did not sample it.
    """
    name = _one_line(failure['name'])
    at_cut = _one_line(at_cut)
    if not at_cut:
        raise Refused('--at-cut is empty. It is the line the scenario target is authored from: what the agent must do at the cut.')
    if cut_turn < 1:
        raise Refused(
            f'--cut-turn {cut_turn}: a trace scenario resumes from history, so its cut is turn 1 or later. A failure on '
            'turn 0 is covered by the walk and gets no folder.'
        )
    sampled = sampled_files(failure)
    if source_file not in sampled and not hand_picked:
        raise Refused(
            f'{source_file} is not one of the traces the brief sampled for {name!r} '
            f'({", ".join(sampled) or "it samples none"}). A trace the operator chose passes --hand-picked.'
        )
    checks = _strings(failure.get('caught_by_check'))
    evidence = _strings(failure.get('evidence'))
    fault = _strings(failure.get('fault_class'))
    origin = _strings(failure.get('origin'))
    units = _count(failure.get('unit_count'))

    lines = [f'{FAILURE_HEADING} {name}', '']
    caught = ', '.join(_code(check) for check in checks) if checks else 'no check the brief names'
    lines += [
        (
            f'**Caught by.** {caught}. The trace analysis saw this failure in {units} production unit(s). It was '
            'observed, not diagnosed: nothing here says whose fault it was.'
        ),
        '',
    ]
    if evidence:
        lines += ['**Evidence, quoted from the brief** (the brief may have truncated it):', '']
        for number, value in enumerate(evidence):
            if number:
                lines.append('')
            lines += _quote(value)
        lines.append('')
    else:
        lines += ['**Evidence.** The brief quotes none.', '']
    if fault or origin:
        labels = []
        if fault:
            labels.append('fault class ' + ', '.join(_code(value) for value in fault))
        if origin:
            labels.append('origin ' + ', '.join(_code(value) for value in origin))
        text = '; '.join(labels)
        lines += [f'**Labels the analysis inferred, not a diagnosis.** {text[0].upper()}{text[1:]}.', '']
    cut = (
        f'**Cut.** This conversation is {_code(source_file)}, cut before user turn {cut_turn}: the turns before it '
        f"are the seed, the user message of turn {cut_turn} opens the live leg, and the agent's reply to it is where "
        'production showed the failure.'
    )
    if source_file not in sampled:
        cut += ' The operator chose this trace; the brief did not sample it.'
    lines += [cut, '', f'**What the agent must do at the cut.** {at_cut}']
    others = [f for f in sampled if f != source_file]
    if others:
        lines += ['', '**Other sampled traces that showed it** (evidence, not converted):', '']
        lines += [f'- {_code(other)}' for other in others[:EVIDENCE_TRACES_MAX]]
        if len(others) > EVIDENCE_TRACES_MAX:
            lines.append(f'- and {len(others) - EVIDENCE_TRACES_MAX} more')
    return '\n'.join(lines) + '\n'


# ── writing a folder ──────────────────────────────────────────────────────────


def write_folder(directory: Path, *, slug: str, paragraph: str, max_total: int) -> Path:
    """Create ``trace_<slug>`` holding the trace failure paragraph as its ``ROLES.md``.

    Written BEFORE the conversion: the converter appends its resume half and the world facts to an
    existing ``ROLES.md`` and never overwrites what is above them. An existing folder is refused
    rather than replaced, because it means the slug already has a trace scenario and re-converting
    over it would swap the conversation earlier runs under that name replayed. A directory already
    holding ``max_total`` folders is refused too: this is the one place a folder is created, so the
    total cap holds for the brief-driven writes and the operator's hand pick alike, and no folder
    is ever replaced to make space.
    """
    regressions.require_slug(slug, prefix=FOLDER_PREFIX)
    if not paragraph.strip():
        raise Refused('the ROLES.md paragraph is empty — the paragraph is the only place the trace failure is stated.')
    if failure_opening(paragraph) is None:
        opening = next(line for line in paragraph.splitlines() if line.strip()).strip()
        raise Refused(
            f'the ROLES.md paragraph opens on {opening[:60]!r}, not on {FAILURE_HEADING!r}. The heading is what the '
            'folder is read by: the converter appends its resume section below the paragraph, and a folder whose first '
            'heading is the resume section states a conversation and no trace failure.'
        )
    folder = directory / f'{FOLDER_PREFIX}{slug}'
    if folder.exists():
        raise Refused(
            f"{folder} already exists, so this trace failure already has a trace scenario. A folder's presence IS its "
            'status: read it to check it covers this failure, and delete it to retire it before writing another.'
        )
    on_disk = len(folders(directory))
    if total_left(max_total, on_disk) <= 0:
        raise Refused(_total_refusal(max_total, on_disk))
    folder.mkdir(parents=True)
    (folder / ROLES_FILENAME).write_text(paragraph.rstrip('\n') + '\n', encoding='utf-8')
    return folder


# ── reading the directory back ────────────────────────────────────────────────


def _read_pin(folder: Path) -> dict[str, Any]:
    try:
        pin = json.loads((folder / PIN_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(
            f'{folder.name}: {PIN_FILENAME} could not be read as JSON ({type(exc).__name__}), so it replays nothing '
            'the backend will accept. Convert the trace into the folder again, or delete the folder.'
        ) from exc
    if not isinstance(pin, dict):
        raise Refused(f'{folder.name}: {PIN_FILENAME} is not a JSON object. Convert the trace again, or delete the folder.')
    return pin


def folder_shape(folder: Path) -> str:
    """``resumed``, or a :class:`Refused` naming why the folder is unfinished.

    A trace scenario has one finished shape. A folder without its pin is what a conversion that
    failed leaves behind, and the backend authors a scenario for it cold from ``ROLES.md`` under
    the folder's name — a trace scenario that replays no production conversation. An opener file
    is refused too: a trace scenario never takes the opener shape, and beside a pin the backend
    refuses the folder outright.
    """
    if not (folder / ROLES_FILENAME).is_file():
        raise Refused(
            f'{folder.name} holds no {ROLES_FILENAME}, which the bundle store requires and which carries the trace '
            'failure paragraph. Write the folder with `trace_scenarios.py write`, or delete it.'
        )
    if (folder / regressions.FIRST_TALK_FILENAME).is_file():
        raise Refused(
            f'{folder.name} carries {regressions.FIRST_TALK_FILENAME}. A trace scenario always resumes from its pin; '
            'a turn-0 failure gets no folder. Remove the file or delete the folder.'
        )
    if not (folder / PIN_FILENAME).is_file():
        raise Refused(
            f'{folder.name} carries no {PIN_FILENAME}, so it replays nothing. Finish the conversion into it or delete '
            'the folder.'
        )
    _read_pin(folder)
    return SHAPE_RESUMED


def provenance(folder: Path) -> dict[str, Any]:
    """``source_file``, ``thread_id`` and ``cut_turn`` off the pin, each ``None`` when absent."""
    block = _read_pin(folder).get('provenance')
    block = block if isinstance(block, dict) else {}
    out: dict[str, Any] = {}
    for key in PROVENANCE_KEYS:
        value = block.get(key)
        if key == 'cut_turn':
            out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
        else:
            out[key] = value if isinstance(value, str) and value else None
    return out


def listing(
    directory: Path,
    *,
    shipped: set[str] | None = None,
    held_back: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The JSON ``list`` prints: one row per folder, plus ``not_in_set`` when ``shipped`` is given.

    An unfinished folder is kept out of the carried accounting altogether, as in ``regressions.py``:
    its cold-authored scenario carries the folder's name, so a name match would report the one
    folder that reproduces nothing as covered. ``held_back`` maps the folders the delivered batch
    left out to the stage's reason; such a row says so and is not counted in ``not_in_set``,
    because the stage already reported it and the backend never had it to lose. With
    ``held_back`` given, the output also lists those folders with their reasons, so a caller
    relays each by why it is out.
    """
    held = held_back or {}
    rows: list[dict[str, Any]] = []
    not_in_set: list[str] = []
    for folder in folders(directory):
        try:
            shape = folder_shape(folder)
        except Refused as exc:
            row = {'folder': folder.name, 'shape': SHAPE_INCOMPLETE, 'reason': str(exc)}
            if folder.name in held:
                row['held_back'] = held[folder.name]
            rows.append(row)
            continue
        row: dict[str, Any] = {'folder': folder.name, 'shape': shape, **provenance(folder)}
        if folder.name in held:
            row['held_back'] = held[folder.name]
        if shipped is not None:
            row['carried'] = folder.name in shipped
            if not row['carried'] and folder.name not in held:
                not_in_set.append(folder.name)
        rows.append(row)
    out: dict[str, Any] = {'dir': str(directory), 'folders': rows}
    if shipped is not None:
        out['not_in_set'] = not_in_set
    if held_back is not None:
        out['held_back'] = [
            {'folder': row['folder'], 'reason': row['held_back']}
            for row in rows
            if 'held_back' in row and not row.get('carried')
        ]
    return out


def existing(directory: Path, *, slug: str) -> list[dict[str, Any]]:
    """The folder that already holds ``slug``, finished or not: an unfinished folder still holds its name."""
    folder = directory / f'{FOLDER_PREFIX}{slug}'
    return [{'folder': folder.name, 'matched_on': ['slug']}] if folder in folders(directory) else []


def overlaps(directory: Path, source_file: str) -> list[dict[str, Any]]:
    """Every finished folder converted from this trace, with the cut its pin records.

    Matched by file NAME: the brief names a trace relative to the ingested trace files (a bare
    ``<trace_id>.json``), a caller may pass a path, and the pin records the name alone. One trace
    can show two failures at two turns, so a shared trace is not a duplicate: only a folder at the
    same cut carries the same conversation.
    """
    wanted = Path(source_file).name
    out = []
    for folder in folders(directory):
        try:
            recorded = provenance(folder) if (folder / PIN_FILENAME).is_file() else {}
        except Refused:
            continue
        if recorded.get('source_file') and Path(recorded['source_file']).name == wanted:
            out.append({'folder': folder.name, 'cut_turn': recorded.get('cut_turn')})
    return out


def overview_section(present: list[Path], *, level: int = 1) -> str:
    """The trace-scenario part of a bundle overview: what the folders are, then one row per folder.

    ``level`` is the heading depth, so the same text heads the directory's own ``BUNDLE.md`` and
    sits as one section of the final batch's merged overview. Raises :class:`Refused` on an
    unfinished folder: the overview reaches the backend as the bundle's own description, and a
    row for a folder that replays nothing describes a set the backend builds anyway.
    """
    rows = []
    for folder in present:
        folder_shape(folder)
        recorded = provenance(folder)
        source = f'`{recorded["source_file"]}`' if recorded['source_file'] else '—'
        cut = str(recorded['cut_turn']) if recorded['cut_turn'] is not None else '—'
        rows.append(f'| `{folder.name}` | {source} | {cut} |')
    body = '\n'.join(rows) if rows else '| _(none)_ | | |'
    return f"""{'#' * level} Trace scenarios — production conversations cut before a trace failure

{len(present)} trace scenario(s). Each folder continues a real production conversation from its
`{PIN_FILENAME}`, cut just before the turn where a failure showed in production. The pin is the
conversation verbatim and is authoritative: carry it through unchanged, and author the opener and
the target for what remains of the arc, not for what already happened. The folder name is the
scenario's name in the delivered set.

`{ROLES_FILENAME}` opens on a `{FAILURE_HEADING}` paragraph: what the production conversation went
through and which check caught it. It assigns no blame — the failure was observed, not diagnosed —
so derive the scenario's target from the situation the paragraph describes, not from a verdict on
whose fault it was. The converter's resume section follows, ending with **{regressions.WORLD_FACTS_HEADING}**:
the tool calls and results the trace recorded after the cut. Those are constraints for the world
author — author a world whose tools can return what production returned, so the live leg meets
the situation it met in production — not a frozen world to copy.

| Folder | Source trace | Cut before turn |
| --- | --- | --- |
{body}
"""


def overview_text(directory: Path) -> str:
    """The directory's own ``BUNDLE.md``, rewritten from what is on disk."""
    return overview_section(folders(directory))


# ── what a run says about each trace scenario ─────────────────────────────────

VERDICT_REPRODUCED = 'reproduced'
VERDICT_NOT_REPRODUCED = 'did not reproduce'
VERDICT_UNGRADED = 'ungraded on its target'
VERDICT_NONE = 'no verdict'

#: The report's list key for each verdict, in the order the report reads them.
VERDICT_KEYS = {
    VERDICT_REPRODUCED: 'reproduced',
    VERDICT_NOT_REPRODUCED: 'did_not_reproduce',
    VERDICT_UNGRADED: 'ungraded_on_target',
    VERDICT_NONE: 'no_verdict',
}

#: ``goal.goal_source`` when the eval set declared no owner for the scenario's goal.
GOAL_SOURCE_NONE = 'none'

#: The two ``goal.goal_status`` values that are a verdict on the goal.
GOAL_FAILED = 'failed'
GOAL_PASSED = 'passed'

#: The walk's own scenario names (``scenario_NN_…``), which the backend refuses as a bundle slug.
_WALK_NAME_RE = re.compile(r'^scenario_\d+_')


def _transcript_scenario(run_dir: Path, row_id: str) -> str | None:
    """``metadata.scenario`` off the row's transcript, or ``None`` when there is none to read."""
    try:
        transcript = json.loads((run_dir / row_id / 'transcript.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    meta = transcript.get('metadata') if isinstance(transcript, dict) else None
    scenario = meta.get('scenario') if isinstance(meta, dict) else None
    return scenario if isinstance(scenario, str) and scenario else None


def _folder_for(names: list[str], on_disk: list[str]) -> str | None:
    """The trace scenario folder a run row is, or ``None`` for a walk scenario or a regression.

    A bundle folder's scenario runs under the folder's own name, so the first answer is a name
    equal to a folder on disk. The run directory's suffix is the fallback, longest folder first so
    ``trace_window`` never claims ``…_trace_refund_window``; a walk scenario or a regression is
    never matched by suffix, since either may end on a trace folder's words. Only a folder in the
    trace scenarios directory is ever claimed: a hand-authored whole-set bundle may name a folder
    ``trace_…`` too, and a retired folder has no paragraph left to say what it replayed.
    """
    for name in names:
        if name in on_disk:
            return name
    for name in names:
        if name.startswith(regressions.FOLDER_PREFIX) or _WALK_NAME_RE.match(name):
            continue
        for folder in sorted(on_disk, key=len, reverse=True):
            if name.endswith(f'_{folder}'):
                return folder
    return None


def verdict(goal: Any) -> str:
    """One trace scenario's verdict, off the goal block ``build_run_json.py`` puts on its row.

    The aggregator's ``goal_status`` IS the verdict; this maps it and never re-derives it from
    ``goal_achieved`` and ``measurement_clean``, because the two disagree exactly where the
    aggregator already decided: a missed goal over an unclean measurement is ``failed``, and an
    achieved one over an integrity nobody could read is ``unclean``. The scenario's target is
    authored from the trace failure paragraph, so ``failed`` is the production failure happening
    again, and ``passed`` did not reproduce it — reported, never a defect: the mock may
    approximate the production world too loosely, or the agent may already have changed. No
    goal owner means nothing graded the target; every other status is no verdict either way.
    """
    goal = goal if isinstance(goal, dict) else {}
    if goal.get('goal_source') == GOAL_SOURCE_NONE:
        return VERDICT_UNGRADED
    return {GOAL_FAILED: VERDICT_REPRODUCED, GOAL_PASSED: VERDICT_NOT_REPRODUCED}.get(goal.get('goal_status'), VERDICT_NONE)


def reproduced(run_json: Path, *, directory: Path) -> dict[str, Any]:
    """The per-trace-scenario report of one finished run, from its ``run.json``.

    A row is matched to a folder in ``directory`` by the transcript's ``metadata.scenario`` first,
    then by the row's run-directory name (see :func:`_folder_for`).
    """
    try:
        doc = json.loads(run_json.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{run_json} could not be read as JSON ({type(exc).__name__}: {exc}).') from exc
    rows = doc.get('scenarios') if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise Refused(
            f'{run_json} carries no scenarios list, so it is not a run.json. Build it with build_run_json.py, the '
            'aggregator /simulation-run runs, and pass the file it writes.'
        )
    on_disk = [folder.name for folder in folders(directory)]
    found: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            continue
        row_id = row['id']
        metadata = row.get('metadata')
        scenario = metadata.get('scenario') if isinstance(metadata, dict) else None
        names = [name for name in (scenario, _transcript_scenario(run_json.parent, row_id), row_id) if isinstance(name, str) and name]
        folder = _folder_for(list(dict.fromkeys(names)), on_disk)
        if folder is None:
            continue
        goal = row.get('goal')
        goal = goal if isinstance(goal, dict) else {}
        found.append({
            'folder': folder,
            'scenario': row_id,
            'verdict': verdict(goal),
            'goal_status': goal.get('goal_status'),
            'goal_source': goal.get('goal_source'),
            'goal_achieved': goal.get('goal_achieved'),
        })
    found.sort(key=lambda entry: entry['folder'])
    out: dict[str, Any] = {'trace_scenarios': found}
    for name, key in VERDICT_KEYS.items():
        out[key] = [entry['folder'] for entry in found if entry['verdict'] == name]
    return out


# ── from trace scenario to regression ─────────────────────────────────────────

#: How ``retire`` removed a folder: a staged ``git rm`` in the customer's repository, or a plain
#: deletion where nothing tracked it.
RETIRED_BY_GIT = 'git rm'
RETIRED_BY_DELETE = 'deleted'

#: What ``/simulation-fixes`` appends to the issue's ``Regression:`` line in ``fixes.md``.
RETIRED_NOTE = 'retired `{folder}`'

#: Environment variables that point git at a repository other than the one the folder sits in. A
#: calling hook can export them, and ``git -C`` does not override them.
_GIT_LOCATORS = frozenset({
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
})


def source_of(transcript: Path, directory: Path) -> dict[str, Any]:
    """The trace scenario a run's scenario ran as: ``{trace_scenario, folder, roles}``, or
    ``{trace_scenario: None, reason}``.

    Matched by the transcript's ``metadata.scenario`` against a folder in ``directory``, by exact
    name only. ``reproduced`` also accepts a run directory named around a folder; here the answer
    decides what a regression copies and which folder is deleted, so a name that only resembles
    a folder never counts. A walk scenario or a regression never names a ``trace_`` folder. The
    transcript must also carry a ``resume_seed``: a trace scenario always resumes, and a run that
    recorded no seed did not replay the trace scenario's conversation.

    The seed is read by :func:`regressions.resume_seed`, the rule ``pick`` records by, so a
    transcript ``pick`` refuses — a seed and ``resume_seed_present`` that disagree, or a reduced
    restore of the seed — is refused here too rather than read as a run of the trace scenario.

    A resumed scenario named like a trace scenario whose folder is not on disk adds ``warning``:
    the folder was retired, or the scenario came from somewhere else, and either way its world
    facts are not there to carry.
    """
    try:
        doc = json.loads(transcript.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{transcript} could not be read as JSON ({type(exc).__name__}: {exc}).') from exc
    doc = doc if isinstance(doc, dict) else {}
    meta = doc.get('metadata')
    name = meta.get('scenario') if isinstance(meta, dict) else None
    resumed = (
        regressions.resume_seed(
            doc,
            where=str(transcript),
            remedy=(
                'The regression folder already recorded from this transcript was cut from it, and `pick` now refuses it: '
                'it changed after `pick` read it, or the pick is not this record\'s. Delete that regression folder with '
                '`git rm -r` (the record step staged it), record the issue again from record-regression Step 4, and run '
                '`retire` for the folder the new record writes.'
            ),
        )
        is not None
    )
    if not isinstance(name, str) or not name:
        return {'trace_scenario': None, 'reason': f'{transcript} names no scenario in metadata.scenario.'}
    if name not in {folder.name for folder in folders(directory)}:
        answer: dict[str, Any] = {'trace_scenario': None, 'reason': f'{name} is not a folder in {directory}.'}
        if resumed and name.startswith(FOLDER_PREFIX):
            answer['warning'] = (
                f'{name} resumed like a trace scenario, but no folder of that name is in {directory} — retired, or never '
                'one. The regression is recorded without world facts.'
            )
        return answer
    if not resumed:
        return {
            'trace_scenario': None,
            'reason': f'{name} ran without a resume_seed, so the run did not replay the trace scenario\'s conversation.',
        }
    folder = directory / name
    return {'trace_scenario': name, 'folder': str(folder), 'roles': str(folder / ROLES_FILENAME)}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    """``git -C cwd …`` against the repository ``cwd`` sits in, or ``None`` when git cannot run.

    ``core.fsmonitor`` is off for every call: the repository's own config can name any command for
    it, and git runs that command on ``status``, ``diff`` and ``add``.
    """
    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATORS}
    try:
        return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(cwd), *args], capture_output=True, text=True, **utf8_child_text(), env=env, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def _remove(folder: Path) -> str:
    """Delete ``folder``, as a staged ``git rm`` when it sits in a git work tree.

    The deletion is how a retirement is recorded, so a tracked folder leaves the index too, and
    the operator commits it with the fix. ``-f`` because the stage step leaves folders staged and
    uncommitted, which a plain ``git rm`` refuses. What ``-f`` would drop that exists nowhere else
    is refused instead: an unstaged edit or an untracked file is in neither the index nor history.
    A folder git ignores is refused by name, since every file in it reads as untracked.
    :func:`retire` has already checked the regression replays the same conversation and carries
    the world facts. Outside a work tree the folder is deleted outright; a symlink is refused,
    since deleting one removes a link, and following it would delete a folder that lives elsewhere.
    """
    if folder.is_symlink():
        raise Refused(f'{folder} is a symlink, so it is not a folder this directory owns. Nothing was retired; retire it by hand.')
    inside = _git(folder.parent, 'rev-parse', '--is-inside-work-tree')
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != 'true':
        shutil.rmtree(folder)
        return RETIRED_BY_DELETE
    ignored = _git(folder.parent, 'check-ignore', '-q', '--', folder.name)
    if ignored is not None and ignored.returncode == 0:
        raise Refused(
            f'{folder.parent.name}/{folder.name} is ignored by git here, so it is not tracked and its retirement cannot be '
            f'recorded. {folder.parent.name}/ is meant to be tracked: retirement needs it tracked, or delete the folder by '
            'hand. Nothing was retired.'
        )
    untracked = _git(folder.parent, 'ls-files', '-o', '--', folder.name)
    unstaged = _git(folder.parent, 'diff', '--quiet', '--', folder.name)
    if untracked is None or unstaged is None or untracked.returncode != 0 or unstaged.returncode not in (0, 1):
        raise Refused(f'git could not report on {folder}. Nothing was retired.')
    if untracked.stdout.strip():
        files = ', '.join(untracked.stdout.split())
        raise Refused(
            f'{folder} holds files git does not track ({files}), which exist nowhere else. Nothing was retired; stage them '
            '(`stage-inputs.sh trace_scenarios`) or remove them, then retire again.'
        )
    if unstaged.returncode == 1:
        raise Refused(
            f'{folder} has edits that are not staged, which exist nowhere else. Nothing was retired; stage or discard '
            'them, then retire again.'
        )
    removed = _git(folder.parent, 'rm', '-r', '-f', '-q', '--', folder.name)
    if removed is None or removed.returncode != 0:
        detail = removed.stderr.strip() if removed is not None else 'git could not run'
        raise Refused(f'git rm refused {folder} ({detail}). Nothing was retired; remove it by hand with `git rm -r`.')
    if folder.exists():
        shutil.rmtree(folder)
    return RETIRED_BY_GIT


def _pin_messages(folder: Path) -> list[Any]:
    try:
        pin = _read_pin(folder)
    except Refused as exc:
        raise Refused(
            f'cannot read {folder.name}/{PIN_FILENAME}, so the two conversations cannot be compared; the trace scenario '
            'is kept.'
        ) from exc
    messages = pin.get('messages')
    if not isinstance(messages, list) or not messages:
        raise Refused(f'{folder.name}: {PIN_FILENAME} carries no messages, so it replays no conversation. Nothing was retired.')
    return messages


def _same_message(ours: Any, theirs: Any) -> bool:
    """One seeded message on both pins: the same id, or, when the ids differ, the same type and content."""
    if not (isinstance(ours, dict) and isinstance(theirs, dict)):
        return ours == theirs
    if ours.get('id') is not None and ours.get('id') == theirs.get('id'):
        return True
    return ours.get('type') == theirs.get('type') and ours.get('content') == theirs.get('content')


def _require_same_conversation(trace: Path, regression: Path) -> None:
    """Refuse unless the two pins carry one conversation.

    At the seed boundary the regression's pin is the trace scenario's seed followed by the same
    opener; the run reader mints its own id for the live message, so the last message is compared
    on its content alone. The pick only says where the cut fell, and a pick left from another
    record would otherwise decide a deletion. No provenance is read.
    """
    ours, theirs = _pin_messages(trace), _pin_messages(regression)
    last_ours, last_theirs = ours[-1], theirs[-1]
    same = (
        len(ours) == len(theirs)
        and all(_same_message(a, b) for a, b in zip(ours[:-1], theirs[:-1]))
        and isinstance(last_ours, dict) and isinstance(last_theirs, dict)
        and last_ours.get('type') == last_theirs.get('type') == 'human'
        and last_ours.get('content') == last_theirs.get('content')
    )
    if not same:
        raise Refused(
            f'{regression.name} does not replay the conversation {trace.name} carries ({len(theirs)} pin messages '
            f'against {len(ours)}, or a seeded message or the opener differs), so the pick is not borne out by the '
            'folders. Nothing was retired; both stay.'
        )


def _require_world_facts_carried(trace: Path, regression: Path) -> None:
    """Refuse when the trace scenario holds world facts its regression does not carry.

    Retiring first would delete the only copy of what production's tools returned. A code fence
    left open in the trace scenario's ``ROLES.md`` hides every heading after it, so the answer
    cannot be read and is refused too.
    """
    trace_roles, regression_roles = trace / ROLES_FILENAME, regression / ROLES_FILENAME
    try:
        trace_text = trace_roles.read_text(encoding='utf-8')
        regression_text = regression_roles.read_text(encoding='utf-8')
    except OSError as exc:
        raise Refused(f'a ROLES.md could not be read ({type(exc).__name__}: {exc}). Nothing was retired.') from exc
    if (line := regressions.unclosed_fence(trace_text)) is not None:
        raise Refused(
            f'{trace_roles} line {line} opens a code fence that never closes, so whether it holds world facts cannot be '
            'read. Nothing was retired.'
        )
    if regressions.world_facts_section(trace_text) is not None and regressions.world_facts_section(regression_text) is None:
        raise Refused(
            f'{trace.name} holds "{regressions.WORLD_FACTS_HEADING}" and {regression.name} does not carry it. Nothing '
            'was retired; run record-regression Step 6b first.'
        )


def retire(directory: Path, *, pick: Path, regression: Path) -> dict[str, Any]:
    """Retire the trace scenario the regression just recorded from its run replaces, or say why not.

    ``pick`` is the JSON ``regressions.py pick --out`` wrote for this record, ``regression`` the
    folder the record wrote. The trace scenario is deleted when that folder is a finished resumed
    regression, the pick's scenario ran as a trace scenario (:func:`source_of`), the cut falls at
    the seed boundary (``regressions.cut_at_seed_boundary``), the two pins carry one conversation,
    and the regression carries the trace scenario's world facts. A later cut seeds live turns the
    trace scenario never had, and both stay.

    Refuses, deleting nothing, when the regression folder is absent or unfinished, the pick cannot
    be read, the pick's transcript is one ``pick`` refuses (:func:`source_of`), the pins disagree
    with the pick, the world facts were not carried, or the deletion would drop content that exists
    nowhere else. A decision — retired or kept — spends the pick,
    so the file is removed; a refusal leaves it for the re-run.
    """
    if not regression.is_dir():
        raise Refused(f'{regression} does not exist: the record wrote no folder, so nothing is retired.')
    if regressions.folder_shape(regression) != SHAPE_RESUMED:
        raise Refused(f'{regression.name} is the opener-only shape; it replays no resume seed, so it replaces no trace scenario.')
    try:
        picked = json.loads(pick.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(
            f'{pick} could not be read as JSON ({type(exc).__name__}): the record step made no pick for this regression, '
            'so nothing is retired.'
        ) from exc
    if not isinstance(picked, dict) or not isinstance(picked.get('transcript'), str):
        raise Refused(f'{pick} is not the JSON `regressions.py pick` prints (no transcript path), so nothing is retired.')

    source = source_of(Path(picked['transcript']), directory)
    name = source['trace_scenario']
    if name is None:
        decided: dict[str, Any] = {'retired': None, 'reason': source['reason']}
    elif not regressions.cut_at_seed_boundary(picked):
        decided = {
            'retired': None,
            'kept': name,
            'reason': f'the regression is cut at live turn {picked.get("cut_turn")}, past the seed boundary: it seeds live '
            f'turns {name} does not carry, so both stay.',
        }
    else:
        folder = directory / name
        _require_same_conversation(folder, regression)
        _require_world_facts_carried(folder, regression)
        entry = _retired_entry(folder)
        decided = {'retired': name, 'how': _remove(folder), 'fixes_note': RETIRED_NOTE.format(folder=name)}
        decided['recorded'] = _record(directory, entry)
    pick.unlink(missing_ok=True)
    return decided


def _today() -> str:
    """The retirement date, in UTC so two machines retiring at one moment write one date."""
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _retired_entry(folder: Path) -> Retired:
    """The retired-list line for a folder still on disk: its pin's trace and cut, and its failure."""
    try:
        recorded = provenance(folder)
    except Refused:
        recorded = {}
    return Retired(
        slug=folder.name.removeprefix(FOLDER_PREFIX),
        source_file=recorded.get('source_file') or '',
        cut_turn=recorded.get('cut_turn'),
        date=_today(),
        failure=folder_failure(folder) or '',
    )


def _record(directory: Path, entry: Retired) -> str:
    """``record_retired``, with a refusal that says how to finish when the list cannot be written."""
    try:
        return record_retired(directory, entry)
    except OSError as exc:
        raise Refused(
            f'{FOLDER_PREFIX}{entry.slug} was removed, but {directory / RETIRED_FILENAME} could not be written '
            f'({type(exc).__name__}: {exc}). Finish the retirement with `retire --record {entry.slug} --source-file '
            f'{entry.source_file or "<trace>"} --cut-turn {entry.cut_turn if entry.cut_turn is not None else "<K>"}`, or the '
            'next chain writes it again.'
        ) from exc


def _bare_slug(slug: str) -> str:
    bare = slug.removeprefix(FOLDER_PREFIX)
    regressions.require_slug(bare, prefix=FOLDER_PREFIX)
    return bare


def retire_folder(directory: Path, slug: str) -> dict[str, Any]:
    """Retire a trace scenario the operator no longer wants: remove it, then list it in ``RETIRED``.

    The removal is :func:`_remove`'s staged ``git rm -r``, with its refusals: nothing that exists
    only in the working tree is deleted. The retired list is what keeps the next chain from
    writing the same trace failure again.
    """
    bare = _bare_slug(slug)
    folder = directory / f'{FOLDER_PREFIX}{bare}'
    if not folder.is_dir() and not folder.is_symlink():
        raise Refused(
            f'{folder} is not on disk. If it was deleted by hand, record the retirement with `retire --record {bare}`; '
            'otherwise check the slug against `trace_scenarios.py list`.'
        )
    entry = _retired_entry(folder)
    how = _remove(folder)
    return {'retired': folder.name, 'how': how, 'recorded': _record(directory, entry)}


def _from_git(directory: Path, relative: str) -> str | None:
    """A file the index, or else ``HEAD``, still holds for a path under ``directory``."""
    for spec in (f':./{relative}', f'HEAD:./{relative}'):
        shown = _git(directory, 'show', spec)
        if shown is not None and shown.returncode == 0:
            return shown.stdout
    return None


def record_deleted(directory: Path, slug: str, *, source_file: str | None = None, cut_turn: int | None = None) -> dict[str, Any]:
    """List a trace scenario the operator deleted by hand in ``RETIRED``, and drop it from the index.

    The trace and the cut come from the flags, or else from the pin git still holds — in the
    index, since a plain ``rm`` leaves it there, or in ``HEAD``. Without either the line cannot say
    which conversation was retired, and the call is refused. A plain ``rm`` also leaves the files
    in the index, so they are removed from it (``git rm -r --cached``) and the deletion is staged.
    """
    bare = _bare_slug(slug)
    name = f'{FOLDER_PREFIX}{bare}'
    if (directory / name).exists():
        raise Refused(f'{directory / name} is still on disk. Retire it with `retire --slug {bare}`, which removes it too.')
    failure = ''
    if source_file is None or cut_turn is None:
        raw = _from_git(directory, f'{name}/{PIN_FILENAME}')
        try:
            pin = json.loads(raw) if raw is not None else None
        except ValueError:
            pin = None
        block = pin.get('provenance') if isinstance(pin, dict) else None
        if isinstance(block, dict):
            if source_file is None and isinstance(block.get('source_file'), str):
                source_file = block['source_file']
            if cut_turn is None and isinstance(block.get('cut_turn'), int) and not isinstance(block.get('cut_turn'), bool):
                cut_turn = block['cut_turn']
    if source_file is None or cut_turn is None:
        raise Refused(
            f'git holds no {PIN_FILENAME} for {name}, so which conversation it carried cannot be read. Pass --source-file '
            'and --cut-turn: the loop log\'s `wrote trace_<slug> from <source_file> cut before turn <K>` entry names both.'
        )
    roles = _from_git(directory, f'{name}/{ROLES_FILENAME}')
    if roles is not None:
        opening = failure_opening(roles)
        if opening is not None:
            failure = _one_line(opening[len(FAILURE_HEADING):])
    indexed = _git(directory, 'ls-files', '--', name)
    held = indexed is not None and indexed.returncode == 0 and bool(indexed.stdout.strip())
    unstaged = _git(directory, 'rm', '-r', '-q', '--cached', '--', name) if held else None
    entry = Retired(slug=bare, source_file=source_file, cut_turn=cut_turn, date=_today(), failure=failure)
    return {
        'retired': name,
        'how': RETIRED_BY_GIT if unstaged is not None and unstaged.returncode == 0 else RETIRED_BY_DELETE,
        'recorded': _record(directory, entry),
    }


# ── commands ──────────────────────────────────────────────────────────────────


def _read_arg(path: Path) -> str:
    return sys.stdin.read() if str(path) == '-' else path.read_text(encoding='utf-8')


def _shipped_names(raw: str) -> set[str]:
    """Scenario names, one per line or as a JSON array of strings."""
    stripped = raw.strip()
    if stripped.startswith('['):
        try:
            names = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise Refused(f'--shipped-from looks like a JSON array and does not parse ({exc}).') from exc
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise Refused('--shipped-from is a JSON value but not an array of scenario names.')
        return {name.strip() for name in names if name.strip()}
    return {line.strip() for line in raw.splitlines() if line.strip()}


def _paragraph_for(args: argparse.Namespace) -> tuple[str, str]:
    """``(slug, paragraph)`` for ``write``, composed from the brief's trace failure and the cut."""
    brief_flags = {'--failure': args.failure, '--trace': args.trace, '--cut-turn': args.cut_turn, '--at-cut': args.at_cut}
    missing = [flag for flag, value in brief_flags.items() if value is None]
    if missing:
        raise Refused(f'--from-failures composes the paragraph from the brief and the cut; it also needs {", ".join(missing)}.')
    failure = find_failure(read_failures(args.from_failures), args.failure)
    slug = args.slug if args.slug is not None else failure_slug(args.failure)
    if slug is None:
        raise Refused(f'{args.failure!r} holds no ascii word to name a folder by; pass --slug.')
    paragraph = failure_paragraph(
        failure, source_file=args.trace, cut_turn=args.cut_turn, at_cut=args.at_cut, hand_picked=args.hand_picked,
    )
    same = [o['folder'] for o in overlaps(args.directory, args.trace) if o['cut_turn'] == args.cut_turn]
    if same:
        raise Refused(
            f'{same[0]} already carries {args.trace} cut before turn {args.cut_turn}: the same conversation, so this '
            'trace failure is covered. Nothing was written.'
        )
    return slug, paragraph


def cmd_write(args: argparse.Namespace) -> int:
    slug, paragraph = _paragraph_for(args)
    caps = resolve_caps(max_total=args.max_total)
    folder = write_folder(args.directory, slug=slug, paragraph=paragraph, max_total=caps['max_total'])
    print(folder)
    if args.harness is not None and (gap := harness_gap(args.harness.parent, args.harness)) is not None:
        print(f'WARNING: {gap} The folder is written and is correct; the final batch holds trace scenarios back until a Python harness takes their seeds.', file=sys.stderr)
    return 0


def held_back_from(path: Path, *, flag: str = '--held-back-from') -> dict[str, str]:
    """``{folder: reason}`` out of the JSON ``final_bundle.py stage`` printed.

    ``flag`` is the option the caller read ``path`` from, so a refusal names the one the operator
    typed.
    """
    try:
        staged = json.loads(_read_arg(path))
    except (OSError, ValueError) as exc:
        raise Refused(f'{flag} {path} could not be read as JSON ({type(exc).__name__}).') from exc
    entries = staged.get('held_back') if isinstance(staged, dict) else None
    if not isinstance(entries, list):
        raise Refused(f'{flag} {path} carries no held_back list — pass the JSON `final_bundle.py stage` printed.')
    return {
        entry['folder']: str(entry.get('reason') or '')
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get('folder'), str)
    }


def _emit(out: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')
    print(text)


def cmd_preflight(args: argparse.Namespace) -> int:
    out = preflight(args.workdir, plurai=args.plurai)
    print(json.dumps(out, indent=2))
    if out['seed_state_warning']:
        print(
            f'WARNING: {out["seed_state_warning"]} The trace scenarios are written anyway and are correct; the final '
            'batch holds every one back until seed_state replays a seed. Setting the agent up again with `/simulation-scan-agent` wires it.',
            file=sys.stderr,
        )
    return 0


def _require_confined(args: argparse.Namespace, *names: str) -> None:
    """Refuse an output path (``--out``, ``--out-dir``) outside the working tree, before any input is read."""
    for name in names:
        value = getattr(args, name, None)
        if value is not None and (why := _confine.refusal(value, allow_outside=args.allow_outside_workdir)):
            raise Refused(why)


def cmd_select(args: argparse.Namespace) -> int:
    _require_confined(args, 'out')
    if (args.trace is None) != (args.cut_turn is None):
        raise Refused('--trace and --cut-turn are one hand pick: pass both, or neither.')
    hand_pick = (args.trace, args.cut_turn) if args.trace is not None else None
    out = select(
        read_failures(args.failures), args.workdir,
        max_new=args.max_new, max_total=args.max_total, named=args.failure, hand_pick=hand_pick,
    )
    _emit(out, args.out)
    if out['cap_line']:
        print(out['cap_line'], file=sys.stderr)
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    _require_confined(args, 'out', 'out_dir')
    try:
        selection = json.loads(_read_arg(args.selection))
    except (OSError, ValueError) as exc:
        raise Refused(f'{args.selection} could not be read as JSON ({type(exc).__name__}). Pass the file `select --out` wrote.') from exc
    if not isinstance(selection, dict) or not isinstance(selection.get('selected'), list):
        raise Refused(f'{args.selection} carries no selected list, so it is not what `select` writes.')
    out = candidates(selection, args.workdir, args.out_dir, plurai=args.plurai)
    _emit(out, args.out)
    for entry in out['failures']:
        paths = [row['transcript'] for row in entry['candidates']]
        listed = '\n'.join(f'  {path}' for path in paths) if paths else '  (none rendered)'
        print(f'{entry["folder"]} — {entry["failure"]}:\n{listed}', file=sys.stderr)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    _require_confined(args, 'out')
    try:
        doc = json.loads(_read_arg(args.candidates))
    except (OSError, ValueError) as exc:
        raise Refused(f'{args.candidates} could not be read as JSON ({type(exc).__name__}). Pass the file `candidates --out` wrote.') from exc
    if not isinstance(doc, dict) or not isinstance(doc.get('failures'), list):
        raise Refused(f'{args.candidates} carries no failures list, so it is not what `candidates` writes.')
    located = []
    for slug, source_file, cut_turn in args.located or []:
        try:
            located.append((slug, source_file, int(cut_turn)))
        except ValueError:
            raise Refused(f'cut turn {cut_turn!r} for {slug!r} is not a whole number.') from None
    out = plan(doc, args.workdir, located, max_total=args.max_total)
    _emit(out, args.out)
    if out['cap_line']:
        print(out['cap_line'], file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    shipped = _shipped_names(_read_arg(args.shipped_from)) if args.shipped_from is not None else None
    held = held_back_from(args.held_back_from) if args.held_back_from is not None else None
    out = listing(args.directory, shipped=shipped, held_back=held)
    print(json.dumps(out, indent=2))
    missing = out.get('not_in_set') or []
    if missing:
        print(
            f'{len(missing)} trace scenario(s) on disk that this scenario set does not carry: {", ".join(missing)}. A '
            'trace scenario reaches a set only through the final batch, so these are covered from the next suite build on.',
            file=sys.stderr,
        )
    unfinished = [row['folder'] for row in out['folders'] if row['shape'] == SHAPE_INCOMPLETE and 'held_back' not in row]
    if unfinished:
        raise Refused(
            f'{len(unfinished)} folder(s) replay nothing: {", ".join(unfinished)}. The backend does not refuse such a '
            "folder — it authors a scenario for it cold from ROLES.md, under the folder's name. Finish the conversion or "
            'delete the folder before the next suite build; it is never counted as carried.'
        )
    return 1 if missing else 0


def cmd_overview(args: argparse.Namespace) -> int:
    text = overview_text(args.directory)
    args.directory.mkdir(parents=True, exist_ok=True)
    path = args.directory / OVERVIEW_FILENAME
    path.write_text(text, encoding='utf-8')
    print(path)
    return 0


def cmd_reproduced(args: argparse.Namespace) -> int:
    out = reproduced(args.run_json, directory=args.trace_dir)
    print(json.dumps(out, indent=2))
    if not out['trace_scenarios']:
        print('No trace scenario ran in this run. Leave the Trace scenarios line out of the summary.', file=sys.stderr)
    return 0


def cmd_source_of(args: argparse.Namespace) -> int:
    answer = source_of(args.transcript, args.trace_dir)
    warning = answer.pop('warning', None)
    print(json.dumps(answer, indent=2))
    if warning:
        print(f'WARNING: {warning}', file=sys.stderr)
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    by_pick = args.pick is not None or args.regression is not None
    modes = [mode for mode, given in (('--pick/--regression', by_pick), ('--slug', args.slug), ('--record', args.record)) if given]
    if len(modes) != 1:
        raise Refused('name one retirement: --slug <slug>, --record <slug>, or --pick with --regression.')
    if (args.source_file is not None or args.cut_turn is not None) and args.record is None:
        raise Refused('--source-file and --cut-turn go with --record: a folder still on disk carries its own.')
    if by_pick:
        if args.pick is None or args.regression is None:
            raise Refused('--pick and --regression go together: the pick and the folder its record wrote.')
        answer = retire(args.directory, pick=args.pick, regression=args.regression)
    elif args.slug is not None:
        answer = retire_folder(args.directory, args.slug)
    else:
        answer = record_deleted(args.directory, args.record, source_file=args.source_file, cut_turn=args.cut_turn)
    print(json.dumps(answer, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='trace_scenarios.py', description=SUMMARY)
    sub = parser.add_subparsers(dest='command', required=True)

    checks = sub.add_parser(
        'preflight',
        help='whether the brief-driven mode can run in this workdir',
        description='Prints JSON {traces_dir, source_kind, trace_count, seed_state_warning}; source_kind is the '
        '--source-format every conversion of these traces names. The warning is repeated on stderr. Exit 0 when it can '
        'run, the harness warning included; 2 under anonymized posture, with no local traces, or with traces from a '
        'source the installed SDK has no reader for.',
    )
    checks.add_argument('workdir', type=Path, help='the plurai_sim workdir ($WORKDIR)')
    checks.add_argument('--plurai', default='plurai', help='the plurai binary (default: plurai on PATH), asked which readers it has')
    checks.set_defaults(func=cmd_preflight)

    choosing = sub.add_parser(
        'select',
        help="rank a brief's trace failures and preselect the ones to turn into trace scenarios",
        description='Prints JSON {workspace_id, generated_at, warning, room, limit, caps, total, existing_folders, '
        'selected, not_selected, skipped_existing, unusable, cap_line}; cap_line, the report line for a full total '
        'cap, is repeated on stderr. Exit 0 whenever the brief reads, even with nothing selected; 2 when no brief is '
        'present, a --failure is not in it, a cap is not a whole number of 1 or more, or the hand pick is malformed or '
        'meets a full total cap.',
    )
    choosing.add_argument('failures', type=Path, help='the JSON get_trace_failures returned, or - for stdin')
    choosing.add_argument('--workdir', type=Path, required=True, help='the plurai_sim workdir, holding regressions/ and trace_scenarios/')
    choosing.add_argument(
        '--max-new', type=int,
        help=f'the per-brief cap for this run: preselect at most this many (default {DEFAULT_MAX_PER_BRIEF}); not with --failure',
    )
    choosing.add_argument(
        '--max-total', type=int,
        help=f'the total cap for this run: trace_* folders on disk at most (default {DEFAULT_MAX_TOTAL})',
    )
    choosing.add_argument('--failure', action='append', help='take this trace failure by exact name; repeatable')
    choosing.add_argument('--trace', help="the operator's hand pick: this trace file, relative to <workdir>/traces/traces as the brief names it")
    choosing.add_argument('--cut-turn', type=int, help="the operator's hand pick: cut before this user turn")
    choosing.add_argument('--out', type=Path, help='also write the JSON here')
    _confine.add_flag(choosing)
    choosing.set_defaults(func=cmd_select)

    rendering = sub.add_parser(
        'candidates',
        help='render each selected trace failure\'s sampled traces for choosing the cut, shortest first',
        description='Prints JSON {out_dir, failures: [{failure, slug, folder, unit_count, caught_by_check, evidence, '
        'revealing_marker, hand_pick?, candidates: [{source_file, export, transcript, user_turns, cuttable, overlaps}], '
        'dropped: [{source_file, reason, finding?}]}]} and, '
        'on stderr, each failure\'s rendering paths in reading order. Exit 0 whatever was dropped; 2 when the selection '
        'cannot be read or --out-dir overlaps the traces or the trace scenarios.',
    )
    rendering.add_argument('selection', type=Path, help='the JSON `select --out` wrote')
    rendering.add_argument('--workdir', type=Path, required=True, help='the plurai_sim workdir, holding traces/traces/')
    rendering.add_argument('--out-dir', type=Path, required=True, help='scratch for the renderings, one subdirectory per slug')
    rendering.add_argument('--plurai', default='plurai', help='the plurai binary (default: plurai on PATH)')
    rendering.add_argument('--out', type=Path, help='also write the JSON here')
    _confine.add_flag(rendering)
    rendering.set_defaults(func=cmd_candidates)

    planning = sub.add_parser(
        'plan',
        help='from the located cuts, what is written without asking and whether one question is needed',
        description='Prints JSON {room, needs_confirmation, reasons, write, ask, pick_at_most, default, skipped_existing, '
        'not_written, caps, total, cap_line}. Each reason is {reason: "overlap", slug, source_file, cut_turn, overlaps} '
        'or {reason: "over_room", located, room}. cap_line, the report line for a full total cap, is repeated on '
        'stderr. Exit 0 whatever it plans; 2 when the candidates cannot be read, a --located row matches no rendering, '
        'or a cap is not a whole number of 1 or more.',
    )
    planning.add_argument('candidates', type=Path, help='the JSON `candidates --out` wrote')
    planning.add_argument('--workdir', type=Path, required=True, help='the plurai_sim workdir, holding regressions/ and trace_scenarios/')
    planning.add_argument(
        '--located', nargs=3, action='append', metavar=('SLUG', 'SOURCE_FILE', 'K'),
        help="one located trace failure: its slug, the rendering's source_file, and the user turn K it is cut before; repeatable",
    )
    planning.add_argument(
        '--max-total', type=int,
        help=f'the total cap for this run: trace_* folders on disk at most (default {DEFAULT_MAX_TOTAL})',
    )
    planning.add_argument('--out', type=Path, help='also write the JSON here')
    _confine.add_flag(planning)
    planning.set_defaults(func=cmd_plan)

    write = sub.add_parser(
        'write',
        help='create trace_<slug> with its trace failure paragraph',
        description='The paragraph is composed from --from-failures with --failure, --trace, --cut-turn and --at-cut. '
        'Prints the folder. Exit 0 written; 2 refused, nothing written — including '
        'when a folder already carries the same trace at the same cut, or the total cap is reached.',
    )
    write.add_argument('directory', type=Path, help='the trace scenarios directory (created if absent)')
    write.add_argument('--slug', help='the trace failure slug; the folder is trace_<slug>. Derived from --failure when omitted')
    write.add_argument('--from-failures', type=Path, required=True, help='the JSON get_trace_failures returned')
    write.add_argument('--failure', help='the trace failure, by exact name')
    write.add_argument('--trace', help='the source file converted, as the brief names it')
    write.add_argument('--cut-turn', type=int, help='the user turn the conversation is cut before (1 or later)')
    write.add_argument('--at-cut', help='one line: what the agent must do at the cut')
    write.add_argument(
        '--hand-picked', action='store_true',
        help='the operator chose --trace; it need not be one of the traces the brief sampled',
    )
    write.add_argument('--harness', type=Path, help='simulate.py, checked for a seed_state that replays a seed (warning only)')
    write.add_argument(
        '--max-total', type=int,
        help=f'the total cap for this run: trace_* folders on disk at most (default {DEFAULT_MAX_TOTAL})',
    )
    write.set_defaults(func=cmd_write)

    listing_parser = sub.add_parser(
        'list',
        help='the folders as JSON: shape and pin provenance',
        description='Exit 0 all present, 1 with --shipped-from when a finished folder is not in the set, 2 when a '
        'folder is unfinished (the JSON is still printed).',
    )
    listing_parser.add_argument('directory', type=Path)
    listing_parser.add_argument(
        '--shipped-from', type=Path,
        help='file holding the scenario names a set shipped — one per line, or a JSON array — or - for stdin',
    )
    listing_parser.add_argument(
        '--held-back-from', type=Path,
        help='the JSON `final_bundle.py stage` printed; a folder it held back is marked and never counted as not in the set',
    )
    listing_parser.set_defaults(func=cmd_list)

    overview = sub.add_parser('overview', help="rewrite the directory's BUNDLE.md from what is on disk")
    overview.add_argument('directory', type=Path)
    overview.set_defaults(func=cmd_overview)

    run = sub.add_parser(
        'reproduced',
        help="per trace scenario in a finished run, whether the production failure came back",
        description='Prints JSON {trace_scenarios: [{folder, scenario, verdict, goal_status, goal_source, goal_achieved}], '
        'reproduced, did_not_reproduce, ungraded_on_target, no_verdict}. Exit 0 whenever run.json reads — a trace '
        'scenario that did not reproduce is reported, not failed. Exit 2 run.json could not be read.',
    )
    run.add_argument('run_json', type=Path, help="the run's run.json, written by build_run_json.py")
    run.add_argument('--trace-dir', type=Path, required=True, help='the trace scenarios directory; only its folders are reported')
    run.set_defaults(func=cmd_reproduced)

    source = sub.add_parser(
        'source-of',
        help="which trace scenario, if any, a run's scenario ran as",
        description='Prints JSON {trace_scenario, folder, roles}, or {trace_scenario: null, reason} for a walk scenario, a '
        'regression, or a run without a resume_seed; a WARNING on stderr when the scenario resumed under a trace_ name '
        'whose folder is not on disk. Exit 0 either way; 2 when the transcript cannot be read, or when `regressions.py '
        'pick` would refuse it (a resume_seed and resume_seed_present that disagree, or a reduced restore of the seed).',
    )
    source.add_argument('transcript', type=Path, help="the scenario's transcript.json in the run")
    source.add_argument('--trace-dir', type=Path, required=True, help='the trace scenarios directory')
    source.set_defaults(func=cmd_source_of)

    retiring = sub.add_parser(
        'retire',
        help='retire a trace scenario and list it in RETIRED: --slug, --record, or the one a regression replaces',
        description=f'Every retirement appends one line to the directory\'s {RETIRED_FILENAME} list (staged in git) and '
        'prints it as `recorded`. --slug removes the folder; --record lists one already deleted by hand, reading its '
        'trace and cut from the pin git still holds unless --source-file and --cut-turn name them. With --pick and '
        f'--regression it prints JSON {{retired: "trace_<slug>", how, fixes_note, recorded}} after a retirement — how is '
        f'"{RETIRED_BY_GIT}" (staged, not committed) or "{RETIRED_BY_DELETE}" — or {{retired: null, reason[, kept]}}, '
        'and removes the pick. Exit 0 either way; 2 when the regression folder is absent or unfinished, the pick cannot be '
        'read, its transcript is one `regressions.py pick` refuses, the pins do not carry one conversation, the world '
        'facts were not carried, the folder is ignored by git or holds unstaged edits or untracked files, or it is a '
        'symlink: nothing is deleted then and the pick stays.',
    )
    retiring.add_argument('directory', type=Path, help='the trace scenarios directory')
    retiring.add_argument('--slug', help='retire this trace scenario: list it in RETIRED and remove it with a staged git rm -r')
    retiring.add_argument(
        '--record', metavar='SLUG',
        help='list a trace scenario deleted by hand in RETIRED, and drop what the index still holds of it',
    )
    retiring.add_argument('--source-file', help='with --record: the trace it was converted from, when git holds no pin')
    retiring.add_argument('--cut-turn', type=int, help='with --record: its cut, when git holds no pin')
    retiring.add_argument('--pick', type=Path, help='the JSON `regressions.py pick --out` wrote for this record')
    retiring.add_argument('--regression', type=Path, help='the regression folder the record wrote')
    retiring.set_defaults(func=cmd_retire)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Refused as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
