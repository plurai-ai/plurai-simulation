#!/usr/bin/env python3
"""The files each stage must leave for the report page, read off the workdir: ONE table for the skills, the phase record and CI.

    deliverables.py check <scan|traces|suite|run|fixes> [--workdir W]

Exit 0: nothing is missing. Exit 3: one `MISSING <path> — <writer step>` line per file the page reads that the
stage has not written. Exit 4 (nothing missing): one `INTERNAL <path>:<line> …` line per customer-artifact line that
carries our own machinery (`customer_text_guard.violations`) — rewrite it, or move it to the internal file, and check
again. Exit 2: usage (an unknown stage, or no workdir resolves).

`DELIVERABLES[stage]` holds one `Row(glob, when, writer_step)` per file pattern the page reads, keyed by the
page's own schema key (`scripts/report/schema.json`), so CI can check the two against each other. `when(disk)`
returns the paths that row requires on this workdir; `[]` means the row does not apply here. Every page pattern
that no stage gates is in `NOT_GATED`, with the reason.

What counts as missing: the path is not a file, or it is JSON that does not parse, or it fails its row's own
check (a draft `suite.json`, a `suite.json` or `final_set_meta.json` for another set than `.scenario_set_id`).

A row may also name PROBLEMS with a present file (`Row.problems`): each is printed on its own MISSING line, after the
writer step. `fixes.md` has two: an agent fix without replacement text, and a scenario in a symptom line that the plan's
run does not have.

A row may also give ADVICE on a present file (`Row.warnings`): each is printed on a `WARN <path> — <writer step>: …` line
that never changes the exit code. `fixes.md` has one: an agent fix whose `In short:` line (the fix in one plain
sentence, shown on the page under the issue's name) is missing or runs past 20 words. `suite` and `run` add one more: the
scored final run's audited violations with no `finding_attribution.json`, or one that leaves some without an edge
(`scored_findings.attribution_gaps`).

NO FALSE BLOCKS. A CONDITIONAL row reads its condition from disk: the set's file list in `final_set_meta.json`
(minus `withheld_files` and `omitted` entries), the phase record's current build, the scored run's rows, the
findings file. When that condition cannot be read, the row requires nothing; the file that should have carried
it is a REQUIRED row of its own, so its absence is still named.

Paths resolve the way the page reads them: relative to the workdir the fill is given (`$WORKDIR`). A bare
`plurai_sim/` that uses named envs resolves to its active env (`plurai_sim/envs/<name>`), as resolve-workdir.sh does.

Stdlib only. `missing(wd, stage)` is the import surface: a list of `(relpath, writer_step)`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verdicts_file  # noqa: E402 - sibling script: the verdicts file's one name

sys.path.append(str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402 - is_local_drive_path: the plugin's one UNC gate


STAGES = ('scan', 'traces', 'suite', 'run', 'fixes')
EXIT_OK, EXIT_USAGE, EXIT_MISSING, EXIT_INTERNAL = 0, 2, 3, 4

SUITE_STATE = 'artifacts/suite_state.json'
META = 'artifacts/final_set_meta.json'
SCENARIOS = 'artifacts/scenarios'
RUNS = 'artifacts/runs'
FINDINGS_MD = 'artifacts/agent_findings.md'
FINDINGS_ROWS = 'artifacts/agent_findings.jsonl'
#: A finding status that `/simulation-fixes` diagnoses and writes up (`record_finding.STATUSES`).
DIAGNOSED_FINDING = 'confirmed'
#: A scored-run row result `/simulation-fixes` writes a plan for (`build_run_json`'s `result`).
FIXABLE_RESULTS = ('fail', 'error')
#: The phase-record vocabulary this module reads (`bench_manifest.RESET_PHASE`, the corpus phase).
RESET_PHASE = 'reset'
CORPUS_PHASE = 'corpus'

_SEGMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_SECTION = re.compile(r'(?m)^## \S')


def _segment(value: object) -> str | None:
    """`value` as one safe path segment, else None."""
    return value.strip() if isinstance(value, str) and _SEGMENT.match(value.strip()) and value.strip() not in ('.', '..') else None


class Disk:
    """One workdir as the rows read it; every read is cached and never raises."""

    def __init__(self, wd: Path) -> None:
        self.wd = wd
        self._json: dict[str, Any] = {}

    def is_file(self, rel: str) -> bool:
        return (self.wd / rel).is_file()

    def text(self, rel: str) -> str | None:
        try:
            return (self.wd / rel).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None

    def json(self, rel: str) -> Any:
        """The parsed file, or None when it is absent or does not parse."""
        if rel not in self._json:
            try:
                self._json[rel] = json.loads((self.wd / rel).read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, ValueError):
                self._json[rel] = None
        return self._json[rel]

    def parses(self, rel: str) -> bool:
        return self.json(rel) is not None

    def marker(self, name: str) -> str | None:
        value = (self.text(name) or '').strip()
        return value or None

    # ── the conditions ────────────────────────────────────────────────────────────────────────

    def build_phases(self) -> list[dict]:
        """The phase record's current build: the records after the last reset ([] when unreadable)."""
        doc = self.json(SUITE_STATE)
        phases = doc.get('phases') if isinstance(doc, dict) else None
        records = [p for p in phases if isinstance(p, dict)] if isinstance(phases, list) else []
        cut = max((i for i, p in enumerate(records) if p.get('phase') == RESET_PHASE), default=-1)
        return records[cut + 1:]

    def set_id(self) -> str | None:
        return self.marker('.scenario_set_id')

    def meta_detail(self) -> dict | None:
        meta = self.json(META)
        detail = meta.get('detail') if isinstance(meta, dict) else None
        return detail if isinstance(detail, dict) else None

    def meta_set_id(self) -> str | None:
        meta, detail = self.json(META), self.meta_detail()
        for value in ((detail or {}).get('scenario_set_id'), meta.get('id') if isinstance(meta, dict) else None):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def bundle_paths(self) -> list[str] | None:
        """The set's file list as the bundle fetch writes it (`fetch_slot_bundle.py`): `detail.files` minus every
        `withheld_files` path and every `omitted` entry. None when the list cannot be read or is another set's."""
        meta, detail = self.json(META), self.meta_detail()
        if detail is None or not isinstance(detail.get('files'), list):
            return None
        ours, theirs = self.set_id(), self.meta_set_id()
        if ours and theirs and ours != theirs:
            return None
        raw = meta.get('withheld_files', detail.get('withheld_files'))
        withheld = {e['path'] for e in raw if isinstance(e, dict) and isinstance(e.get('path'), str)} if isinstance(raw, list) else set()
        return [e['path'] for e in detail['files']
                if isinstance(e, dict) and isinstance(e.get('path'), str) and not e.get('omitted') and e['path'] not in withheld]

    def graph_has_flows(self) -> bool:
        graph = (self.meta_detail() or {}).get('dimension_graph')
        if not isinstance(graph, dict):
            return False
        count = graph.get('flow_count')
        return bool(graph.get('flows')) or (isinstance(count, int) and count > 0)

    def built_corpus(self) -> bool:
        """This build recorded `corpus landed` WITH a corpus id. A no-corpus answer (`--answer`) lands `corpus` with
        no id and leaves no `rag_corpus/`."""
        return any(p.get('phase') == CORPUS_PHASE and p.get('status') == 'landed' and isinstance(p.get('ids'), dict)
                   and p['ids'].get('corpus') for p in self.build_phases())

    def scored_run(self) -> str | None:
        """`runs/index.json` `scored_run` (null there: no run is scored yet), else — no index — the run `.last_run`
        names when it sits under `artifacts/runs/`."""
        index = self.json(f'{RUNS}/index.json')
        if isinstance(index, dict) and 'scored_run' in index:
            return _segment(index.get('scored_run'))
        last = self.marker('.last_run')
        if not last:
            return None
        path = Path(last) if Path(last).is_absolute() else self.wd / last
        if not _platform.is_local_drive_path(path, within=(self.wd,)):
            return None
        try:
            if path.resolve().parent == (self.wd / RUNS).resolve():
                return _segment(path.name)
        except OSError:
            return None
        return None

    def rows(self, run_dir: str) -> list[tuple[str, str]]:
        """`(row id, result)` for each row of `<run_dir>/run.json` whose id is one folder name."""
        doc = self.json(f'{run_dir}/run.json')
        rows = doc.get('scenarios') if isinstance(doc, dict) else None
        out = []
        for row in rows if isinstance(rows, list) else []:
            ident = _segment(row.get('id')) if isinstance(row, dict) else None
            if ident:
                out.append((ident, str(row.get('result') or '')))
        return out

    def trial_dirs(self) -> list[str]:
        """The scored run's later trials (`run.json` `trials.dirs[1:]`) the page reads (`trial_*`)."""
        run = self.scored_run()
        doc = self.json(f'{RUNS}/{run}/run.json') if run else None
        trials = doc.get('trials') if isinstance(doc, dict) else None
        dirs = trials.get('dirs') if isinstance(trials, dict) else None
        if not isinstance(dirs, list):
            return []
        return [f'{RUNS}/{run}/{d}' for d in (_segment(d) for d in dirs[1:]) if d and d.startswith('trial_')]

    def finding_rows(self) -> list[dict]:
        text = self.text(FINDINGS_ROWS)
        rows = []
        for line in (text or '').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows


# ── the rows ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Row:
    #: The page's schema key this row gates (`scripts/report/schema.json`).
    glob: str
    #: The paths this row requires on this workdir; [] when it does not apply here.
    when: Callable[[Disk], list[str]]
    #: The step that writes the file, named in every MISSING line.
    writer_step: str
    #: Whether a present, parsing file is the deliverable; None when presence is enough.
    valid: Callable[[Disk, str], bool] | None = None
    #: What is wrong with a present, valid file: one sentence each, named after the writer step in its MISSING line.
    problems: Callable[[Disk, str], list[str]] | None = None
    #: Advice on a present, valid file: one sentence each, printed on a WARN line that never changes the exit code.
    warnings: Callable[[Disk, str], list[str]] | None = None


def _always(rel: str) -> Callable[[Disk], list[str]]:
    return lambda _disk: [rel]


def _findings_md(disk: Disk) -> list[str]:
    """Both or neither: a recorded finding row needs its section."""
    return [FINDINGS_MD] if disk.finding_rows() else []


def _findings_rows(disk: Disk) -> list[str]:
    """Both or neither: a finding section needs its row."""
    return [FINDINGS_ROWS] if _SECTION.search(disk.text(FINDINGS_MD) or '') else []


def _bundle(name: str) -> Callable[[Disk], list[str]]:
    """`artifacts/scenarios/<slot>/<name>` for each slot whose file the set lists and the fetch writes."""
    def when(disk: Disk) -> list[str]:
        paths = disk.bundle_paths() or []
        return [f'{SCENARIOS}/{p}' for p in paths if p.count('/') == 1 and p.endswith(f'/{name}') and _segment(p.split('/')[0])]
    return when


def _graph(disk: Disk) -> list[str]:
    paths = disk.bundle_paths() or []
    return [f'{SCENARIOS}/dimension_graph.yaml'] if 'dimension_graph.yaml' in paths and disk.graph_has_flows() else []


def _corpus(name: str) -> Callable[[Disk], list[str]]:
    return lambda disk: [f'rag_corpus/{name}'] if disk.built_corpus() else []


def _scored(name: str) -> Callable[[Disk], list[str]]:
    def when(disk: Disk) -> list[str]:
        run = disk.scored_run()
        return [f'{RUNS}/{run}/{name}'] if run else []
    return when


def _transcripts(disk: Disk) -> list[str]:
    """A transcript per scored row, except a row the runner errored (its error.json stands in for it)."""
    run = disk.scored_run()
    if not run:
        return []
    base = f'{RUNS}/{run}'
    return [f'{base}/{i}/transcript.json' for i, result in disk.rows(base) if result != 'error' and not disk.is_file(f'{base}/{i}/error.json')]


def _errors(disk: Disk) -> list[str]:
    """An error.json for each errored scored row that has no transcript."""
    run = disk.scored_run()
    if not run:
        return []
    base = f'{RUNS}/{run}'
    return [f'{base}/{i}/error.json' for i, result in disk.rows(base) if result == 'error' and not disk.is_file(f'{base}/{i}/transcript.json')]


def _trial_rows(disk: Disk, trial: str) -> list[tuple[str, str]]:
    """`(row id, result)` for each conversation trial `trial` owes: every row of the scored run's own `run.json` (each
    trial plays the run's scenarios), plus any other row the trial's `run.json` names, each with the trial's own result
    ('' when its `run.json` does not say). A missing or empty trial `run.json` therefore un-requires nothing."""
    run = disk.scored_run()
    own = dict(disk.rows(trial))
    ids = [i for i, _result in disk.rows(f'{RUNS}/{run}')] if run else []
    ids += [i for i in own if i not in ids]
    return [(i, own.get(i, '')) for i in ids]


def _trial_transcripts(disk: Disk) -> list[str]:
    return [f'{d}/{i}/transcript.json' for d in disk.trial_dirs() for i, result in _trial_rows(disk, d)
            if result != 'error' and not disk.is_file(f'{d}/{i}/error.json')]


def _verdicts(disk: Disk) -> list[str]:
    """A `verdicts.json` per scored row, errored rows included: the page draws every verdict from it, and it is never cut."""
    run = disk.scored_run()
    if not run:
        return []
    base = f'{RUNS}/{run}'
    return [f'{base}/{i}/{verdicts_file.FILENAME}' for i, _result in disk.rows(base)]


def _trial_verdicts(disk: Disk) -> list[str]:
    return [f'{d}/{i}/{verdicts_file.FILENAME}' for d in disk.trial_dirs() for i, _result in _trial_rows(disk, d)]


def _run_json_current(disk: Disk, rel: str) -> bool:
    """`run.json` is built FROM its rows' `verdicts.json`: one older than any of them is a build of verdicts that have
    since changed (a rescore or a correction), and owes a rebuild before anything reads its rows."""
    run_json = disk.wd / rel
    base = rel.rsplit('/', 1)[0]
    try:
        built = run_json.stat().st_mtime
        newest = max((p.stat().st_mtime for row in disk.rows(base) if (p := disk.wd / base / row[0] / verdicts_file.FILENAME).is_file()),
                     default=None)
    except OSError:
        return False
    return newest is None or built >= newest


def _provisional_audit(disk: Disk) -> list[str]:
    """A FINAL scored run whose rendered verdict is `provisional` (`release_verdict.py`: its red-cell audit has not
    landed on the backend) owes that audit, uploaded, and a render over it. Rendered and not provisional — or never
    rendered (a run recorded on a platform has no banner) — owes nothing here. The newest `final` run of the index
    with no audit owes one too: it is never the scored run until it has it (`runs_index.py`)."""
    run = disk.scored_run()
    verdict = disk.json(f'{RUNS}/{run}/release_verdict.json') if run else None
    owed = [f'{RUNS}/{run}/red_cell_audit.json'] if isinstance(verdict, dict) and verdict.get('provisional') is True else []
    index = disk.json(f'{RUNS}/index.json')
    finals = [_segment(e.get('name')) for e in (index.get('runs') or []) if isinstance(e, dict) and e.get('stage') == 'final'] if isinstance(index, dict) else []
    newest = finals[-1] if finals else None
    if newest and newest != run and not disk.is_file(f'{RUNS}/{newest}/red_cell_audit.json'):
        owed.append(f'{RUNS}/{newest}/red_cell_audit.json')
    return owed


def _stale_verdict(disk: Disk) -> list[str]:
    """A scored run whose `release_verdict.json` was computed over another audit than the one beside it (C8,
    `release_verdict.stale`) owes the verdict recomputed over that audit, then rendered."""
    run = disk.scored_run()
    if not run:
        return []
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import release_verdict  # noqa: PLC0415 - sibling script, needed only by this row

    return [f'{RUNS}/{run}/{release_verdict.VERDICT_FILE}'] if release_verdict.stale(disk.wd / RUNS / run) else []


def _uncorrected_scores(disk: Disk) -> list[str]:
    """A scored run whose audit rules a `FALSE RED` / `FALSE PASS` its current verdicts still count owes that correction
    in the item's `verdicts.json` history (C8: `verdict_corrections.py build`), whatever edit path the run took since."""
    run = disk.scored_run()
    if not run:
        return []
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import red_cell_audit  # noqa: PLC0415 - sibling scripts, needed only by this row
    import red_cells  # noqa: PLC0415
    import verdict_corrections  # noqa: PLC0415

    out = disk.wd / RUNS / run
    try:
        rows = red_cell_audit.rows(out)
    except red_cell_audit.AuditMissing:
        return []
    run_items = red_cells.Run(out)
    owed = {str(run_items.dirs[u['item']].relative_to(out)) for u in verdict_corrections.uncorrected(run_items, rows) if u['item'] in run_items.dirs}
    return [f'{RUNS}/{run}/{rel}/{verdicts_file.FILENAME}' for rel in sorted(owed)]


def _fixes(disk: Disk) -> list[str]:
    """fixes.md is written when there is something to diagnose: a scored row whose result is failed or errored, or a
    confirmed finding. Otherwise `/simulation-fixes` says so and writes nothing. THE rule: `stage_landed` asks
    `fixes_required` rather than spell it again. A `run.json` older than its verdicts is the run stage's own MISSING row."""
    run = disk.scored_run()
    base = f'{RUNS}/{run}' if run else None
    red = base is not None and any(result in FIXABLE_RESULTS for _i, result in disk.rows(base))
    confirmed = any(row.get('status') == DIAGNOSED_FINDING for row in disk.finding_rows())
    return ['artifacts/fixes.md'] if red or confirmed else []


def fixes_required(wd: str | Path) -> bool:
    """Whether the fixes stage owes a `fixes.md` on this workdir (the `fixes` row applies). Anything unreadable: False,
    like every CONDITIONAL row — the scored run that would carry the reds is a REQUIRED row of the run stage."""
    try:
        return bool(_fixes(Disk(page_root(wd))))
    except Exception:  # noqa: BLE001 - a condition that cannot be read requires nothing
        return False


FIXES_MD_PATH = 'artifacts/fixes.md'
_PLAN_SECTION = re.compile(r'(?m)^## (\d+)\.')
_PLAN_RUN = re.compile(r'(?m)^# Fix plan\b[^\n]*?\brun (\S+)')
#: A scenario as a plan writes it: the short `scenario_03`, a full run id, or a `regression_<slug>` folder. The digit
#: keeps an eval name such as `scenario_goal_attainment` out.
_SCENARIO_NAME = re.compile(r'\b(?:scenario_\d+\w*|regression_\w+)')
#: Replacement text: a fence, a block quote, or a quoted passage long enough to be wording rather than a term.
_REPLACEMENT_TEXT = re.compile(r'```|^\s*>\s?\S|"[^"\n]{20,}"|\u201c[^\u201d\n]{20,}\u201d', re.MULTILINE)
_AGENT_HOME = re.compile(r'home:\s*\**agent\b', re.IGNORECASE)


def _plan_field(section: str, name: str) -> str:
    """The text of a plan section's `- **<name>:**` bullet, up to the next bullet; '' when it has none."""
    match = re.search(rf'(?ms)^- \*\*{re.escape(name)}[^\n]*?:\*\*(.*?)(?=^- \*\*|\Z)', section)
    return match.group(1) if match else ''


def _plan_sections(text: str) -> list[tuple[str, str]]:
    """`(number, section text)` for each numbered `## N.` section of a plan."""
    parts = re.split(r'(?m)^(?=## )', text)
    return [(m.group(1), part) for part in parts if (m := _PLAN_SECTION.match(part))]


def _fixes_plan_problems(disk: Disk, rel: str) -> list[str]:
    """What the plan gets wrong that a reader would act on. Each rule reads only what it can read: a plan without
    numbered sections, or a run whose `run.json` has no rows, is not held to the rule that needs them."""
    text = disk.text(rel) or ''
    sections = _plan_sections(text)
    named = _PLAN_RUN.search(text)
    run = _segment(named.group(1)) if named else None
    run = run if run and disk.rows(f'{RUNS}/{run}') else disk.scored_run()
    ids = [i for i, _result in disk.rows(f'{RUNS}/{run}')] if run else []
    problems: list[str] = []
    for number, section in sections:
        if _AGENT_HOME.search(_plan_field(section, 'Root cause')) and not _REPLACEMENT_TEXT.search(_plan_field(section, 'Proposed fix')):
            problems.append(f'fix {number} is an agent fix without replacement text: quote the new wording as a fenced or quoted block, '
                            'not only a direction')
        unknown = sorted({n for n in _SCENARIO_NAME.findall(_plan_field(section, 'Symptom')) if not any(i == n or i.startswith(f'{n}_') for i in ids)})
        if ids and unknown:
            problems.append(f'fix {number} names {", ".join(unknown)} in its Symptom line; {RUNS}/{run}/run.json has no such scenario')
    return problems


#: The bullet the overview page shows under an issue's name: the fix in one plain sentence (`- **In short:**`).
SUMMARY_LABEL = 'In short'
SUMMARY_MAX_WORDS = 20


def _fixes_plan_warnings(disk: Disk, rel: str) -> list[str]:
    """An agent fix the page cannot sum up in one plain line: no `In short:` bullet, or one too long."""
    out: list[str] = []
    for number, section in _plan_sections(disk.text(rel) or ''):
        if not _AGENT_HOME.search(_plan_field(section, 'Root cause')):
            continue
        words = len(_plan_field(section, SUMMARY_LABEL).split())
        if not words:
            out.append(f'fix {number} has no "{SUMMARY_LABEL}:" line: say in one plain sentence how the fix makes the issue stop')
        elif words > SUMMARY_MAX_WORDS:
            out.append(f'fix {number}\'s "{SUMMARY_LABEL}:" line runs {words} words: keep it to one plain sentence of about 15')
    return out


def _same_set(disk: Disk, rel: str) -> bool:
    """Not a draft, and of the set `.scenario_set_id` names (unreadable either side: not held against it)."""
    doc = disk.json(rel)
    if not isinstance(doc, dict) or doc.get('draft'):
        return False
    ours = disk.set_id()
    theirs = doc.get('scenario_set_id')
    return not (ours and isinstance(theirs, str) and theirs.strip() and theirs.strip() != ours)


def _meta_of_set(disk: Disk, _rel: str) -> bool:
    ours, theirs = disk.set_id(), disk.meta_set_id()
    return not (ours and theirs and ours != theirs)


SCAN_STEP = '/simulation-scan-agent'
SUITE_STEP = '/simulation-generate-suite'
RUN_STEP = '/simulation-run'
FINAL_CORRECTIONS_STEP = 'final-run corrections step 3a (scored_findings.py to-read, the reader, attribute)'

DELIVERABLES: dict[str, list[Row]] = {
    'scan': [
        Row('artifacts/agent_topology.json', _always('artifacts/agent_topology.json'), f'{SCAN_STEP} Step 2 (the topology spec)'),
        Row('artifacts/verification.json', _always('artifacts/verification.json'), f'{SCAN_STEP} Step 3c (bench_manifest.py verify)'),
        Row('bench_manifest.json', _always('bench_manifest.json'), f'{SCAN_STEP} Step 2 (bench_manifest.py init)'),
        Row(FINDINGS_MD, _findings_md, 'the step that recorded the finding (its agent_findings.md section)'),
        Row(FINDINGS_ROWS, _findings_rows, 'record_finding.py --backfill (one row per agent_findings.md section)'),
    ],
    'traces': [
        Row('traces/manifest.json', _always('traces/manifest.json'), '/analyze-traces Step 4 (the SDK export into traces/)'),
        Row('artifacts/trace_failures.json', _always('artifacts/trace_failures.json'), '/analyze-traces Step 6 (get_trace_failures)'),
    ],
    'suite': [
        Row('artifacts/suite.json', _always('artifacts/suite.json'), f'{SUITE_STEP} Step 5 (build_suite_json.py)', _same_set),
        Row(META, _always(META), f'{SUITE_STEP} Step 4 (get_artifact_meta)', _meta_of_set),
        Row('policies_config.yaml', _always('policies_config.yaml'), f'generate-evals ({SUITE_STEP} Step 2)'),
        Row('artifacts/scenarios/*/scenario.yaml', _bundle('scenario.yaml'), f'{SUITE_STEP} Step 5 (report_pieces.py bundle)'),
        Row('artifacts/scenarios/*/agent_context.yaml', _bundle('agent_context.yaml'), f'{SUITE_STEP} Step 5 (report_pieces.py bundle)'),
        Row('artifacts/scenarios/*/knowledge_plan.yaml', _bundle('knowledge_plan.yaml'), f'{SUITE_STEP} Step 5 (report_pieces.py bundle)'),
        Row('artifacts/scenarios/dimension_graph.yaml', _graph, f'{SUITE_STEP} Step 5 (report_pieces.py bundle)'),
        Row('rag_corpus/manifest.json', _corpus('manifest.json'), '/build-rag-corpus (the local extraction into rag_corpus/)'),
        Row('rag_corpus/chunks.json', _corpus('chunks.json'), '/build-rag-corpus (the local extraction into rag_corpus/)'),
    ],
    'run': [
        Row('artifacts/runs/index.json', _always(f'{RUNS}/index.json'), f'{RUN_STEP} Step 4 (runs_index.py write)'),
        Row('artifacts/runs/*/run.json', _scored('run.json'), f'{RUN_STEP} Step 4 (build_run_json.py, again after any rescore or correction)',
            _run_json_current),
        Row('artifacts/runs/*/run_summary.json', _scored('run_summary.json'), f'{RUN_STEP} Step 3 (the runner, at the end of the run)'),
        Row('artifacts/runs/*/*/transcript.json', _transcripts, f'{RUN_STEP} Step 3 (the runner, one per scenario)'),
        Row('artifacts/runs/*/*/error.json', _errors, f'{RUN_STEP} Step 3 (the runner, for an errored scenario)'),
        Row('artifacts/runs/*/trial_*/*/transcript.json', _trial_transcripts, f'{RUN_STEP} Step 3 (the runner, per trial)'),
        Row(f'artifacts/runs/*/*/{verdicts_file.FILENAME}', _verdicts, f'{RUN_STEP} Step 3 (the runner, one per conversation)'),
        Row(f'artifacts/runs/*/trial_*/*/{verdicts_file.FILENAME}', _trial_verdicts, f'{RUN_STEP} Step 3 (the runner, per trial)'),
        # Required only while the verdict is provisional, so a present file is still owed its upload and the re-render.
        Row('artifacts/runs/*/red_cell_audit.json', _provisional_audit,
            f'{RUN_STEP} Step 4d (red_cell_audit.py save + upload_red_cell_audit + uploaded, then the banner block again)',
            lambda _disk, _rel: False),
        # Required only while the verdict stands on another audit than the run's: a present file is owed its recompute.
        Row('artifacts/runs/*/release_verdict.json', _stale_verdict,
            f'{RUN_STEP} Step 4d (release_verdict_recompute over the uploaded audit, then the banner block; release_verdict.py stale exits 13 until then)',
            lambda _disk, _rel: False),
        # Required only while an audited correction is still counted: a present file does not carry it yet.
        Row(f'artifacts/runs/*/*/{verdicts_file.FILENAME}', _uncorrected_scores,
            f'{RUN_STEP} Step 4d (verdict_corrections.py build: an audited FALSE RED / FALSE PASS is not in its history yet)',
            lambda _disk, _rel: False),
    ],
    'fixes': [
        Row(FIXES_MD_PATH, _fixes, '/simulation-fixes Step 3 (the plan)', problems=_fixes_plan_problems,
            warnings=_fixes_plan_warnings),
    ],
}

#: Page patterns no stage gates, and why.
NOT_GATED: dict[str, str] = {
    'eval_intent.yaml': 'generate-evals deletes it when its verification exits 2, so a complete suite can lack it',
    'artifacts/knowledge_summary.json': 'grounding_pull.py knowledge writes it only when a knowledge base is recorded and the backend '
                                        'answers, and the page falls back without it: a missing summary must never block a run',
    'artifacts/flow_progress.json': 'running state, not a deliverable: the fill rewrites it every step and removes it when it would '
                                    'hold nothing',
    # Read by simulation-report 0.56; every one is optional on the page (its section is left out when the file is absent).
    'artifacts/scenarios/*/initial_world_state.yaml': 'a slot file the backend lists only for a world-bearing set; fetch_slot_bundle.py lands '
                                                      'whatever the set lists, and the page leaves the seeded chips out without it',
    'artifacts/scenarios/*/persona.yaml': 'a slot file fetch_slot_bundle.py lands when the set lists it; the page shows no persona without it',
    'artifacts/scenarios/*/first_talk.txt': 'a slot file fetch_slot_bundle.py lands when the set lists it; the page shows no opening '
                                            'message without it',
    'artifacts/runs/*/*/final_world_state.json': "the runner's per-conversation world, written only on a world-bearing run; the page "
                                                 'leaves the World panel out without it',
    'artifacts/runs/*/trial_*/*/final_world_state.json': "the runner's per-trial world, written only on a world-bearing run; the page "
                                                         'leaves the World panel out without it',
    'artifacts/runs/*/*/tool_events.json': "the runner's tool calls beside a transcript; the page leaves the tool panels out without it",
    'artifacts/runs/*/trial_*/*/tool_events.json': "the runner's tool calls beside a trial transcript; the page leaves the tool panels out "
                                                   'without it',
    'artifacts/auto_regressions.json': "written only at the suite's gate exit (regressions.py auto-plan), never by a later recording; "
                                       'the page leaves the regressions shelf out without it',
    'regressions/*/issue_turn.json': 'written per recorded regression folder (regressions.py write), and a suite may record none',
    # Read by simulation-report 0.56.0-rc.5.
    'rag_corpus/relationships.json': "the SDK extraction's links between passages, written beside chunks.json; optional on the page, "
                                     'and grounding_corpus.extraction_shape reads it as optional too',
    # Read by simulation-report 0.56.1.
    'grounding_ledger.yaml': 'the grounding ledger mirror grounding_mirror.py pull writes only when the backend serves a grounding '
                             'job for the workspace; the page leaves the ledger out without it',
    'artifacts/trace_brief/anomalies_noise.md': 'a trace brief file grounding_pull.py traces lands only when /analyze-traces ran and '
                                                'the brief publishes it; the page leaves the anomalies out without it',
    f'artifacts/runs/*/*/stability_retry/{verdicts_file.FILENAME}': "the runner's verdicts of a stability retry, written only "
                                                                    'when the runner replayed a conversation whose trials disagreed '
                                                                    '(its stability_retry/ folder); a replay whose file is missing '
                                                                    'is not named here, and the page shows no retry without it',
    # Read by simulation-report 0.56.2.
    'artifacts/runs/*/trial_*/*/error.json': "the runner's error for a trial attempt that did not finish; a trial row that errored owes "
                                             'no transcript, and the page shows no error text for that attempt without it',
    # Read by simulation-report 0.56.4.
    'artifacts/scan_facts.yaml': "the PRD's typed facts as scan_contract.sh projects them (scan_facts.py), written only when the scan "
                                 'runs its contract against a corpus over a readable `## Agent facts` block, and removed when the '
                                 'projection is refused; the page leaves the facts out without it',
    'plurai.config.yaml': "the scan's config, which every bench carries and bench_manifest.py validate already names as a finding "
                          "when it is absent, so the scan's bench_manifest.json and verification.json rows stand on it; the page "
                          "reads only the agent's module from it and shows no module without it",
    'artifacts/runs/*/trial_*/run_summary.json': "the runner's summary of one later trial (its trial_k/ folder), read only for each "
                                                 "attempt's start, end and duration; the run's own run_summary.json is the gated "
                                                 "one, and the page shows no timing for that trial's attempts without it",
    'artifacts/obligations.json': "no step writes it yet: the eval set's coverage ledger reaches the plugin only as the "
                                  '`detail.obligations` rows of its job answer, which no step lands as this file; the page shows '
                                  'no obligations without it',
    # Read by simulation-report 0.56.8.
    'artifacts/run_warnings.json': 'no step writes it yet: the plugin warnings the page lists have no writer in this release; '
                                   'the page leaves the Plugin warnings panel out without it',
}


def page_root(wd: str | Path) -> Path:
    """The dir the page reads: `wd`, or the active env when `wd` is a bare `plurai_sim/` that uses named envs."""
    root = Path(wd)
    envs, active = root / 'envs', root / '.active-env'
    if envs.is_dir() and active.is_file():
        try:
            name = _segment(active.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            name = None
        if name and (envs / name).is_dir():
            return envs / name
    return root


def _present(disk: Disk, rel: str, row: Row) -> bool:
    if not disk.is_file(rel):
        return False
    if rel.endswith('.json') and not disk.parses(rel):
        return False
    return row.valid is None or row.valid(disk, rel)


def missing(wd: str | Path, stage: str) -> list[tuple[str, str]]:
    """`(relpath, writer_step)` for every file `stage` must leave for the report that is not there. Unknown stage: KeyError."""
    disk = Disk(page_root(wd))
    out: list[tuple[str, str]] = []
    for row in DELIVERABLES[stage]:
        try:
            required = row.when(disk)
        except Exception:  # noqa: BLE001 - a condition that cannot be read requires nothing
            required = []
        for rel in required:
            if not _present(disk, rel, row):
                out.append((rel, row.writer_step))
            elif row.problems:
                out.extend((rel, f'{row.writer_step}: {problem}') for problem in row.problems(disk, rel))
    return out


def advice(wd: str | Path, stage: str) -> list[tuple[str, str]]:
    """`(relpath, writer_step: advice)` for every present file `stage` left that its row would word better. Never blocks."""
    disk = Disk(page_root(wd))
    out: list[tuple[str, str]] = []
    for row in DELIVERABLES[stage]:
        if row.warnings is None:
            continue
        try:
            required = row.when(disk)
        except Exception:  # noqa: BLE001 - a condition that cannot be read requires nothing
            required = []
        for rel in required:
            if _present(disk, rel, row):
                out.extend((rel, f'{row.writer_step}: {warning}') for warning in row.warnings(disk, rel))
    if stage in ATTRIBUTED_STAGES:
        out.extend(_attribution_advice(disk))
    return out


#: The stages whose scored final run owes `finding_attribution.json`: one edge per audited violation (`scored_findings.py`).
ATTRIBUTED_STAGES = ('suite', 'run')


def _attribution_advice(disk: Disk) -> list[tuple[str, str]]:
    """A WARN when the scored final run's audited violations have no attribution file, or one that leaves some without an
    edge: the page then shows them as "not yet traced" and no finding as reproduced. Never a block."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        import scored_findings  # noqa: PLC0415 - sibling script, needed only by this advice

        gap = scored_findings.attribution_gaps(disk.wd)
        stale = scored_findings.stale_replay_readings(disk.wd)
    except Exception as exc:  # noqa: BLE001 - advice that cannot be read gives none, and says so
        print(f'deliverables: the scored run\'s attribution could not be read ({type(exc).__name__}: {exc})', file=sys.stderr)
        return []
    run = disk.scored_run()
    return ([(f'{RUNS}/{run}/{scored_findings.ATTRIBUTION}', f'{FINAL_CORRECTIONS_STEP}: {gap}')] if gap and run else []) + \
        ([(f'{RUNS}/{run}/run.json', f'{FINAL_CORRECTIONS_STEP}, then Step 4b: {stale}')] if stale and run else [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='name every file the stage must leave for the report that is missing')
    check.add_argument('stage', choices=STAGES)
    check.add_argument('--workdir', help='defaults to $WORKDIR, else the resolver cascade')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from harness_fingerprint import resolve_workdir  # noqa: PLC0415 - the one cascade; only the CLI needs it

    try:
        workdir = resolve_workdir(args.workdir)
    except SystemExit:
        return EXIT_USAGE
    gaps = missing(workdir, args.stage)
    for rel, step in gaps:
        print(f'MISSING {rel} — {step}')
    warns = advice(workdir, args.stage)
    for rel, step in warns:
        print(f'WARN {rel} — {step}')
    import customer_text_guard  # noqa: PLC0415 - sibling, stdlib only

    leaks = customer_text_guard.violations(page_root(workdir))
    for leak in leaks:
        print(leak)
    if gaps:
        return EXIT_MISSING
    if leaks:
        return EXIT_INTERNAL
    above = f'; {len(warns)} warning(s) above' if warns else ''
    print(f'deliverables: {args.stage} — nothing the report reads is missing{above}')
    return EXIT_OK


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
