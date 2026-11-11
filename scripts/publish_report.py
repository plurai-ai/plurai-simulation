#!/usr/bin/env python3
"""Fill the overview page from a workdir and print the hand-off's link line — one page per project.

    "$PLURAI_PY" scripts/publish_report.py fill <workdir> --step S [--flowdir D] [--as COMMAND] [--note "<plain words>"]
    "$PLURAI_PY" scripts/publish_report.py fill <workdir> --step progress --flowdir D (--job ID | --run-dir OUT --run-total N [--trials K])
    "$PLURAI_PY" scripts/publish_report.py done <workdir> --step S --url <https-url|-> [--flowdir D]
    "$PLURAI_PY" scripts/publish_report.py forget <workdir> [--flowdir D]
    "$PLURAI_PY" scripts/publish_report.py refill <workdir> [--flowdir D]   # the local page catches up; nothing published
    python3 scripts/publish_report.py live --flowdir D      # exit 0: a live plan has a stage running
    python3 scripts/publish_report.py local                 # exit 0: no Artifact tool here, the page stays a file

``fill`` writes ``<workdir>/artifacts/report.html`` from the vendored page (``scripts/report/``,
see ``vendor_report.py``) and the workdir's own files, and prints ONE JSON line:

    {publish, skip, report, page, anchor, integrity_line, flow_continues,
     version, kept, cut, dropped, misread, misread_count, bytes, filled_at, deliverable_gaps, internal_text}

When ``publish`` is true the skill publishes ``report`` with the Artifact tool — in place at
``page`` when there is one. For a workdir page ``report`` is ``artifacts/.publish/report.html``, a copy of that fill's own
render, which only a publishable fill writes: the Artifact tool refuses a file that changed between its approval and its publish, and ``report.html``
has background writers (the run's ``--local`` loop, ``refill``). The skill hands the URL (or ``-``) to ``done``, which records it and prints
the integrity line (when the page could not read something) and the link line. When ``publish``
is false the skill says nothing: ``skip`` names why, first hit wins (``SKIPS``). The page always
publishes otherwise — under ``anonymize: true`` too, which governs only what is uploaded while the env is built and
never what the page shows — and when it dropped or misread a file (``integrity_line`` counts it in chat; the paths and fields stay in
this record's ``dropped`` / ``misread``, never in customer text).

INTERNAL TEXT. A customer artifact that carries our own machinery (``customer_text_guard.violations``, read with the
page's own findings copy, then the filled page's every embedded text) is never published: ``skip`` is ``internal_text``
and ``internal_text`` lists one ``INTERNAL <path>:<line> …`` line per hit, for the flow to rewrite (or move to the
internal file) and fill again. The page reads verdicts.json as the graders' record (the judge's words, the agent's
words it quotes, the fixed correction sentence code writes), never as our prose; the audit's own note under
``audit_corrected`` never reaches the page, whose schema keeps only its ``quote_turn`` (``customer_text_vocabulary.json``
README). A refused fill (``report_link.REFUSED``) never removes the last good page: it stays with its sidecar and its
marker, so the next clean fill republishes in place, while ``done`` and ``report_link link`` read the refusal and hand
out no link as this step's. A hand-off's line gains ``say``, which the hand-off ends with: ``NOT_UPDATED_LINE``, or
``NO_PAGE_LINE`` when there is no page yet.

LOCAL-ONLY HARNESSES. Copilot CLI and Codex CLI have no Artifact tool. Under them (named by the session
variable the harness exports, ``hooks/_workdir.SESSION_VARS``) ``fill`` still writes the page, then returns
``publish`` false, ``skip`` ``local_only``, ``report`` and ``page`` null, and two more keys: ``local_page``,
the page's absolute path, and ``say``, the lines the hand-off relays (the integrity line, then one sentence
naming the path). The path is told only when the file sits inside the work folder and reads as one line; a
milestone says nothing, and so does a never-scanned start, whose page lives in the flow dir. No URL is ever
claimed and nothing leaves the machine. Claude Code, and a shell no harness owns, get the line above unchanged.

A project with no ``artifacts/`` yet fills ``start`` from an empty seed into ``<flowdir>/report.html`` and creates
nothing under the workdir. That page is outside the session's folder and is never published (skip ``live_page`` in a
run, else ``no_page``); ``<flowdir>/.report_url_pending`` is read for a URL an older version recorded there.

ONE PUBLISH PER RUN. A /simulate run publishes once, at its end (:func:`publish_moment`). While the live, unhalted plan
for this workdir runs, every fill but its end skips ``live_page``: it still writes the page, its sidecar and
``flow_progress.json``, and ``say`` is ``[]`` (the start's names the live page, below). The end is the hand-off of a
plan stage that did not land (it has ``deliverable_gaps``) or after which no stage is left (``flow_continues`` false),
and the ``end`` step (a user's stop, ``simulate end``; a limited-mode halt, ``simulate next``; a stop hand-off of the
error posture). ``end`` with no /simulate run of this workdir in this session skips ``no_plan``. Such a fill carries
``run_end`` true. When such a fill is refused (``report_link.REFUSED``), ``retry`` is true the first time: the hand-off
rewrites once and fills again; a second refusal of the same step carries ``retry`` false, and the hand-off ends with
``say``, never filling again. Milestones (``progress``
included) skip ``live_page`` always. A command run on its own (no plan) publishes at its own hand-off. ``end`` on a
project with no ``artifacts/`` skips ``no_page``; an ``end`` whose page the last publish already shows skips
``unchanged`` and says the recorded link.

INSIDE THE SESSION'S FOLDER. ``report`` is ``<workdir>/artifacts/.publish/report.html`` as an absolute real path. When
it does not lie inside the folder Claude Code was started in (:func:`session_cwd`: the session pin's ``project_dir``,
else ``CLAUDE_PROJECT_DIR``; neither known: publish), nothing is published: skip ``outside_cwd``, and ``say`` names the
local page (``LOCAL_LINE``) and ``OUTSIDE_LINE``. Claude Code asks before publishing a file outside that folder in
every mode, so the plugin never hands one out. A copy that cannot be written costs only the publish: skip ``no_copy``, and
``say`` names the local page, which that fill still moved in place.

Every ``fill`` but a ``--local`` one (``run_tick.sh --loop``: it only refreshes the local page, makes no publish copy,
and so never clears a refusal or moves the signature ``done`` records as published) writes ``.report_fill.json`` (the line, the fill's stderr, the step and the skip)
into the workdir once it has ``artifacts/``, else into ``<flowdir>``, for the operator and for
``done``. ``flow_continues`` is true while a /simulate plan (``<flowdir>/.plurai_simulate.json``,
read as plain JSON) is active and lists a stage after this step that is not done.

THE RUNNING STATE. Every ``fill`` keeps ``flow_progress.json`` (``PROGRESS_SCHEMA``) in step, in the
workdir's ``artifacts/`` once it exists, else in the flow dir (and in the empty seed). The page renders
it: the progress band, the halted and complete states, the Draft and Out of date notes. Nothing is
ever written into the page's DOM. While the plan is live (not finished, written in the last 24 h) the
file holds its stages: a hand-off counts its own stage done only when it LANDED (``stage_landed.py``,
the predicate the plan's ``done`` reads too) and shows the NEXT stage running; a hand-off that did not
land shows its stage halted, and ``halted_reason`` names the first file it did not write
(``deliverable_gaps`` in the line lists every one, for the operator); a halted plan shows its stage
halted. With no live plan, a milestone writes its own stage alone (a ``plan_only`` one writes nothing) and any other
step only the ``sections`` / ``stale`` marks; the file is removed when it would hold nothing. The step text is the
running stage's default (``STAGES``), a milestone's own ``label``, or ``--note`` — plain words, at
most ``NOTE_MAX`` characters; a note carrying a path, URL or markup character is dropped for the
default (the fill record says so). While a question the flow asked waits for the user's answer, the running stage's
step reads ``WAIT_LABEL`` (:func:`wait_progress`, which the ask hooks turn on and off); the next fill drops it.

THE LIVE PAGE. Every fill also writes ``report.data.js`` beside the page (the vendored fill, simulation-report 0.56); a
page open from the file polls it every 2 s and swaps the data in place when its stamp changes, so a local page stays live
with no reload. The fill happens in a private sibling dir; the page is moved over ``report.html`` first and the sidecar
after it, so a reader never finds the page missing nor a stamp ahead of its page. While a Run plays, ``run_tick.sh
--loop`` (Claude Code) and ``run_stage.sh`` (Copilot, Codex) fill within a few seconds of a new or rewritten file in the run dir;
``refill`` catches the page up after a plan transition rewrote ``flow_progress.json``. Between the model's calls
``page_watch.py`` refills within a few seconds of a new or rewritten file under ``artifacts/`` that is not dot-named: one
process per flow dir, from a run's start or a flow entry while its plan is live until that plan ends or goes 24 h unwritten,
or its session's process is gone, running through a halt or a pause. While a run plays it leaves that run's folder and
``flow_progress.json`` to the run's own refresher. It is off with the page off, with nobody watching
(:func:`_nobody_watching`), and on native Windows, which keeps its other refreshes. A published Artifact has no sidecar and changes only when it is published again.

THE LIVE LOCAL PAGE. A /simulate run keeps ONE local page for its whole length: ``<flowdir>/report.html``. Every workdir
fill (``fill``, ``fill --local``, ``refill``) of this session's run, in any state, for this workdir copies the
workdir's current page, then its sidecar, there (:func:`_mirror_live_page`; best-effort, never removing the sidecar).
The start of the run opens it in the browser (:func:`_start_live_page`) and ``say`` names it; with nobody at a browser
(below, plus an SSH login, a remote session or a Unix host with no display, :func:`no_display`) it is only named.
It stays across a session's end, so a tab open through a pause and a resume keeps polling it and a tab open on a finished run
still refreshes; a ``/clear`` during the run and the TTL prune sweep it (``hooks/flow_cleanup.py``), so the closing names the workdir's page instead. ``anchor`` is the step's section, or null when that section is one
the page draws only with content it does not have yet (``GATED_ANCHORS``).

MILESTONES (``milestone: True``: ``scan-map``, ``scan-described``, ``scan-harness``, ``scan-users``, ``scan-checks``,
``suite-scenarios``, ``suite-evals``, ``suite-qual``, ``suite-final``, ``suite-final-run``, ``suite-final-check``,
``progress``) refresh the local page mid-stage and
never publish (``live_page``). They are not hand-offs: ``flow_continues`` is always false for them. The
scan milestones (the agent map once rendered, then each part of the setup as it starts) are ``plan_only``: they publish
only while a live /simulate plan runs the scan, else they skip ``no_plan`` — before any workdir check — and write no
running state, so a scan or setup run on its own shows the page at its hand-off alone.

THE WAKE-UP CARD (``--step progress``). Its stage is the live plan's running stage (no live plan:
skip ``no_plan``). ``--job <id>`` reads ``GET <server>/jobs/<id>`` (bearer from the environment only)
and names the step from ``JOB_LABELS``; ``--run-dir OUT --run-total N [--trials K]`` counts finished
transcripts locally (``RUN_LABEL``). A landed job also lets ``report_pieces.sync`` pull its pieces
before the page fills. Every fill records a ``signature`` of what the page shows; ``done --url
<https>`` copies it to ``.report_published.json``, and an ``end`` fill that would publish the same
signature again skips ``unchanged``.

THE PROGRESS LINE (``progress_line.py``, :func:`progress_line`). The chat's only mid-flow words are the page's own: after a
milestone's fill (and, from the ``PostToolUse`` hook, when a sub-agent the main thread spawned returns), the running
step's bare label is said once, verbatim — the labels above, which never name a calibration run. It prints nothing while no
stage runs, while the flow waits for the user's answer, or when that step was said already (``PROGRESS_SAID``). Exit 0.

EDIT STEPS (``edit-scenarios``, ``edit-evals``, ``edit-env``, ``refine-scenarios``) are hand-offs with
no stage; each skips ``driven`` while a window in its ``driven_by`` runs it.

STALE AND DRAFT. ``artifact_deps.stale_items`` / ``draft_sections`` (read by path; absent means
nothing) land in ``flow_progress.json`` as ``stale`` / ``sections``, without the items inside a run the
page never reads, and each item as the id the page counts it by (``_page_item``): the page drops an entry it cannot name.

THE FINDINGS COPY (``page_findings``). The page reads its own copy of ``agent_findings.md`` / ``.jsonl``, never written
back. While the suite is open, a vendored page that does not declare ``preliminary_findings.PAGE_CAPABILITY`` (its
``manifest.json`` ``capabilities``) gets no quality-loop finding; one that does gets them marked ``in_progress``, plus
the audited qualification rows ``preliminary_findings.rows`` derives at every fill.

THE LOCAL PAGE OPENS ITSELF. ``done --url -`` opens the local page in the default browser, once per page path, in a
detached process, unless this run's start already opened its live page (:func:`_live_page_open`).
``<flowdir>/.report_opened.json`` lists what was opened. Nothing opens with no flow dir, when a recorded
URL still shows this page (the link line already leads there), under a headless run driver or with
``PLURAI_SIM_NONINTERACTIVE`` set in any way, under a harness other than Claude Code, in a ``claude -p`` or Agent SDK
session (``CLAUDE_CODE_ENTRYPOINT`` ``sdk*``), or under ``PLURAI_NO_PAGE_OPEN``, which the test suites set. A failure to
open changes neither the exit code nor what ``done`` prints.

Exit 0 with the line. Exit 64 on an unknown step. Exit 2 on a caller bug: a step other than
``start`` on a workdir with no ``artifacts/``, a seeded start with no ``--flowdir``, or a ``--url``
that is neither ``-`` nor an https page. ``done`` exits 1, printing nothing, when there is no page
file to link.

Needs PyYAML, like the fill script it runs: call it under ``$PLURAI_PY``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _no_redirect  # noqa: E402
import _platform  # noqa: E402 - is_local_drive_path: the plugin's one UNC gate
import _workdir  # noqa: E402 - which harness owns the shell
import report_link  # noqa: E402
import url_guard  # noqa: E402 - the one answer to "may the bearer go to this URL"
import stage_landed  # noqa: E402
import customer_text_guard  # noqa: E402 - the one reading of "may the customer read this text"
import scored_findings  # noqa: E402 - the findings the scored run reproduces, one per defect (3i)
import run_numbers  # noqa: E402 - the page's copy of a run.json (`page_rows`)
import check_customer_numbers  # noqa: E402 - every number on the page equals C7, or the page is not published

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED = REPO_ROOT / 'scripts' / 'report'
FLOW_ARM = REPO_ROOT / 'hooks' / 'flow_arm.py'

#: Every stage a /simulate plan may hold, in run order (``hooks/simulate_continue.STAGES``): the
#: name the page's own rail uses, and the step ``flow_progress.json`` names while that stage runs from its start.
STAGES: dict[str, tuple[str, str]] = {
    'scan': ('Scan', 'Mapping your agent'),
    'traces': ('Traces', 'Reading your production traces'),
    'suite': ('Suite', 'Building the test suite'),
    'run': ('Run', 'Running every scenario against your agent'),
    'fixes': ('Fixes', 'Tracing each failure to its cause'),
}

#: The suite stage's default step by ``<workdir>/.run_mode`` (``local`` is the older spelling of
#: ``mockless``); absent or anything else keeps ``STAGES['suite']``.
SUITE_DEFAULT_BY_RUN_MODE: dict[str, str] = {
    'mock': 'Standing up the mocked world and authoring scenarios',
    'mockless': 'Authoring scenarios and the checks that grade replies',
    'local': 'Authoring scenarios and the checks that grade replies',
}

#: The job kinds the step names; any other kind (trace analysis, optimize, prewarm) falls back.
JOB_KINDS = frozenset({'corpus', 'env', 'scenarios', 'evals', 'solvability', 'suite_diagnostics'})
#: (kind, phase) → the step in plain words. ``'*'`` is any kind in ``JOB_KINDS``. A pair holds
#: (with a counter, without one); ``{c}``/``{n}`` come from ``JOB_COUNTERS`` and show only when the
#: total is above 0. No elapsed time goes in a label: the page shows when the step started, from
#: ``step.since``. A failed job, or a key not here, names no step. The job's own message fields are
#: internal prose and are never read.
JOB_LABELS: dict[tuple[str, str], Any] = {
    ('*', 'queued'): 'Waiting for a free slot on the backend',
    ('*', 'queued_for_slot'): 'Waiting for a free slot on the backend',
    ('corpus', 'preparing'): 'Reading your knowledge sources',
    ('corpus', 'planning_fields'): 'Planning how your knowledge gets searched',
    ('corpus', 'building_graph'): 'Building the knowledge graph',
    ('corpus', 'enriching'): 'Enriching the knowledge graph',
    ('corpus', 'synthesizing'): 'Writing questions from your knowledge',
    ('corpus', 'done'): 'Knowledge base ready',
    ('env', 'generating'): 'Building the mocked world',
    ('env', 'verifying'): 'Checking the mocked world',
    ('env', 'deploying'): 'Starting the mocked world',
    ('env', 'done'): 'Mocked world ready',
    ('scenarios', 'building_graph'): "Mapping your agent's flows",
    ('scenarios', 'seeding'): 'Choosing which paths to test',
    ('scenarios', 'evolving'): ('Writing scenarios · {c} of {n} ready', 'Writing scenarios'),
    ('scenarios', 'building_scenarios'): ('Importing your scenarios · {c} of {n} ready', 'Importing your scenarios'),
    ('scenarios', 'generating_documents'): 'Generating the documents scenarios use',
    ('scenarios', 'writing_scenario_cards'): 'Summarizing each scenario',
    ('scenarios', 'done'): 'Scenarios ready',
    ('scenarios', 'cache_hit'): 'Scenarios ready',
    ('evals', 'preparing_inputs'): 'Preparing to write the checks',
    ('evals', 'calling_skill'): ('Writing the checks that grade replies · {c} of {n}', 'Writing the checks that grade replies'),
    ('evals', 'validating'): 'Validating the checks',
    ('evals', 'done'): 'Checks ready',
    ('solvability', 'resolving_candidates'): 'Checking each scenario can be solved',
    ('solvability', 'authoring_reference_paths'): 'Checking each scenario can be solved',
    ('solvability', 'scoring_reference_paths'): 'Checking each scenario can be solved',
    ('suite_diagnostics', 'resolving_bundle'): "Reviewing the suite's quality",
    ('suite_diagnostics', 'analyzing'): "Reviewing the suite's quality",
}
#: Where each kind's counter lives in the job's ``detail``: (completed, total).
JOB_COUNTERS: dict[str, tuple[str, str]] = {
    'scenarios': ('scenarios_completed', 'scenarios_total'),
    'evals': ('policies_completed', 'policies_total'),
}
#: The run stage is a local runner, not a backend job: (with a counter, without one), plus the
#: trials suffix when each scenario plays more than once (trials run in parallel, so no "trial k").
RUN_LABEL = ('Playing conversations · {c} of {n} finished', 'Playing conversations')
#: A run the SUITE plays (its qualification loop, its own final run) is internal: the step names the work, never
#: its counts, and the page shows none of its conversations or scores (``hidden_runs``).
SUITE_RUN_LABEL = 'Calibrating the simulation'
#: The suite's own final run (:func:`_final_run`): it scores the agent, so it is not named as calibration.
FINAL_RUN_LABEL = 'Scoring your agent on the final suite'
QUALIFICATION_NAME = re.compile(r'(?:^|[-_])qual')
RUN_TRIALS = ' · each played {k} times'
JOB_TIMEOUT_S = 10


def _point(
    command: str, anchor: str | None, stage: str | None, *, final_run_only: bool = False, trial_caveat: bool = False,
    driven_by: tuple[str, ...] | None = None, plan_only: bool = False,
) -> dict[str, Any]:
    return {
        'command': command, 'anchor': anchor, 'stage': stage, 'final_run_only': final_run_only, 'trial_caveat': trial_caveat, 'milestone': False,
        'driven_by': driven_by, 'plan_only': plan_only,
    }


def _milestone(stage: str, command: str, anchor: str, label: str, *, plan_only: bool = False) -> dict[str, Any]:
    return {**_point(command, anchor, stage, plan_only=plan_only), 'milestone': True, 'label': label}


#: The windows an edit runs inside as a sub-step of the suite's quality loop: there the suite's own
#: milestones publish, so the edit's hand-off skips ``driven``.
EDIT_DRIVERS = ('generate-suite', 'analyze')

#: Where each step publishes: the flow command whose window it runs in (the ``driven`` skip asks
#: whether the open window drives it — ``driven_by`` when set, else ``hooks/flow_arm.FLOW_DRIVERS``),
#: the page Stage its link lands on (an anchor of the vendored ``manifest.json``: ``scan``, ``traces``, ``suite``,
#: ``run``, ``gaps``; the ``fixes`` stage has no tab of its own since page 0.56 — it folds into Gaps), the plan stage it belongs to, whether only a final run
#: publishes, and whether its summary covers a run's trials (the link then says the page shows
#: trial 1). A ``milestone`` refreshes the local page mid-stage — never a hand-off, never a publish — and its ``label`` is the step
#: ``flow_progress.json`` names from then on. ``progress`` takes its stage from the live plan and its label
#: from the job or the run. A ``plan_only`` point fills only while a live, unhalted /simulate plan runs its
#: stage (else ``no_plan``, and no running state is written): run on its own, its command shows the page at its
#: hand-off alone.
PUBLISH_POINTS: dict[str, dict[str, Any]] = {
    'start': _point('simulate', None, None),
    'scan': _point('scan-agent', 'scan', 'scan'),
    'traces': _point('traces', 'traces', 'traces'),
    'suite': _point('generate-suite', 'suite', 'suite', trial_caveat=True),
    'run': _point('run', 'run', 'run', final_run_only=True, trial_caveat=True),
    'fixes': _point('fixes', 'gaps', 'fixes', trial_caveat=True),
    'scan-map': _milestone('scan', 'scan-agent', 'scan', 'Map ready — building the setup that runs your agent', plan_only=True),
    'scan-described': _milestone('scan', 'scan-agent', 'scan', 'Agent described — checking its description against the code', plan_only=True),
    'scan-harness': _milestone('scan', 'scan-agent', 'scan', 'Wiring your agent into the simulation', plan_only=True),
    'scan-users': _milestone('scan', 'scan-agent', 'scan', 'Building the user profiles and world your scenarios draw from', plan_only=True),
    'scan-checks': _milestone('scan', 'scan-agent', 'scan', 'Setup built — checking it runs your agent', plan_only=True),
    'suite-scenarios': _milestone('suite', 'generate-suite', 'suite', 'Scenarios ready — writing the checks that grade replies'),
    'suite-evals': _milestone('suite', 'generate-suite', 'suite', 'Checks ready — calibrating the simulation'),
    'suite-qual': _milestone('suite', 'generate-suite', 'suite', SUITE_RUN_LABEL),
    'suite-final': _milestone('suite', 'generate-suite', 'suite', 'Generating the final scenario set'),
    'suite-final-run': _milestone('suite', 'generate-suite', 'suite', FINAL_RUN_LABEL),
    'suite-final-check': _milestone('suite', 'generate-suite', 'suite', 'Checking the final results'),
    'progress': {**_point('simulate', None, None), 'milestone': True, 'label': None},
    'edit-scenarios': _point('edit-scenarios', 'suite', None, driven_by=EDIT_DRIVERS),
    'edit-evals': _point('edit-evals', 'suite', None, driven_by=EDIT_DRIVERS),
    'edit-env': _point('edit-env', 'scan', None, driven_by=EDIT_DRIVERS),
    'refine-scenarios': _point('refine-scenarios', 'suite', None, driven_by=EDIT_DRIVERS),
    # The run's one publish when the user stops it mid-run (`simulate end`) or a limited-mode halt (`simulate next`):
    # no stage, never a milestone, and never `live_page`.
    'end': _point('simulate', None, None, trial_caveat=True),
}
LABEL = 'Overview page →'

#: Stages the page draws only once their content exists (``page_sections``). Page 0.56 sends a link to a Stage with
#: nothing to show to the current Stage; the link names the page itself instead, so it never claims a Stage it cannot open.
GATED_ANCHORS = frozenset({'run', 'gaps'})

#: ``--note``: plain words only, so a path, URL, address or markup never reaches the page.
NOTE_MAX = 90
NOTE_REFUSED = re.compile(r'[\\/<>`{}@]|://')

#: The skips, in the order they are tested; the first that holds wins.
SKIPS = ('disabled', 'not_vendored', 'no_plan', 'no_page', 'driven', 'not_final', 'internal_text', 'fill_refused', 'garbled', 'numbers',
         'local_only', 'live_page', 'unchanged', 'outside_cwd', 'no_copy')

#: The harnesses with no Artifact tool: the page stays a file in the work folder and the hand-off names it.
LOCAL_ONLY_HARNESSES = ('copilot', 'codex')
LOCAL_LINE = 'The overview page is saved at {path}; open it in a browser.'
LOCAL_TRIALS = ' It shows trial 1 of {n}; the summary above covers every trial.'
#: What a hand-off says when its fill was refused (``report_link.REFUSED``, ``say``): the last page stays, or there is none.
NOT_UPDATED_LINE = 'The overview page was not updated this time; the summary above is current.'
NO_PAGE_LINE = 'There is no overview page yet; the summary above is current.'
#: What the start of a /simulate run says about the live local page (``live_page``): opened in the browser, or only named.
LIVE_OPENED_LINE = 'The overview page is open in your browser and updates as the run goes: {path}'
LIVE_NAMED_LINE = 'The overview page updates as the run goes: {path} (open it in a browser).'
#: The second line of an ``outside_cwd`` skip: Claude Code asks before publishing a file outside the folder it started in.
OUTSIDE_LINE = "To get a shareable link, start Claude Code from your agent's folder."
#: A path with one of these cannot be told as a single line.
CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')

#: Suffixes of the link line, applied in this order.
SUFFIXES = {
    'trials': ' — shows trial 1 of {n}; the summary above covers every trial.',
    'stale': ' (not updated this time — this opens the local copy).',
}

#: The count only: which file and which field go to the fill record's ``dropped`` / ``misread``, never to the customer.
INTEGRITY = 'The overview page could not show {count}; the summary above is authoritative.'

#: How long :func:`spawn_page_watch` may spend reading the process table, inside a hook.
OWNER_TIMEOUT_S = 1.0
#: How long a ``refill``'s vendored fill may take; a stuck one must not hold a background refresher for good.
REFRESH_TIMEOUT_S = 120
#: The internal off switch. No skill text names it.
OFF_SWITCH = 'PLURAI_REPORT_PAGE'
OFF_VALUES = ('0', 'false', 'off')

FLOW_MARKER = '.plurai_flow_active'
PLAN_FILE = '.plurai_simulate.json'
PLAN_MAX_AGE_S = 24 * 3600
PENDING = '.report_url_pending'
FILL_RECORD = report_link.FILL_RECORD
OPENED_RECORD = '.report_opened.json'
NO_OPEN_SWITCH = 'PLURAI_NO_PAGE_OPEN'
#: What the last https publish showed (``{signature, step, at}``), beside ``.report_fill.json``.
PUBLISHED_RECORD = '.report_published.json'
FLOW_PAGE = 'report.html'
#: Beside the workdir page: the copy a publishable fill hands out as ``report`` (:func:`_publish_copy`).
PUBLISH_DIR = '.publish'
#: The typed running state the page renders: in the workdir's artifacts/ once it exists, else in the flow dir.
PROGRESS_FILE = 'flow_progress.json'
#: The update file the vendored fill (simulation-report 0.56) writes beside its output page; a page open from the file polls it.
SIDECAR = 'report.data.js'
PROGRESS_SCHEMA = 'plurai-flow-progress/1'


class CallerBug(Exception):
    pass


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return None


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def flow_drivers(flow_arm: Path = FLOW_ARM) -> dict[str, tuple[str, ...]]:
    """``hooks/flow_arm.FLOW_DRIVERS``, read from its source by path (nothing is imported or run)."""
    try:
        tree = ast.parse(flow_arm.read_text('utf-8'))
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        if isinstance(target, ast.Name) and target.id == 'FLOW_DRIVERS' and node.value is not None:
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return {}
            return {str(k): tuple(v) for k, v in value.items()} if isinstance(value, dict) else {}
    return {}


def _seeded(workdir: Path, step: str) -> bool:
    """A start on a project with no artifacts/ yet fills from an empty seed into the flow dir."""
    if (workdir / 'artifacts').is_dir():
        return False
    if step != 'start':
        raise CallerBug(f'{workdir} has no artifacts/ — only the start step publishes before a workdir exists')
    return True


def _page_file(workdir: Path, seeded: bool, flowdir: Path | None) -> Path:
    if not seeded:
        return workdir / 'artifacts' / 'report.html'
    if flowdir is None or not flowdir.is_dir():
        raise CallerBug('a start on a project with no artifacts/ needs an existing --flowdir to hold the page')
    return flowdir / FLOW_PAGE


def _pending(flowdir: Path | None) -> tuple[str, str | None] | None:
    if flowdir is None:
        return None
    try:
        tokens = (flowdir / PENDING).read_text('utf-8').split()
    except OSError:
        return None
    if not tokens or not report_link.PAGE.fullmatch(tokens[0]):
        return None
    return tokens[0], next((t[len('sha256='):] for t in tokens[1:] if t.startswith('sha256=')), None)


def _recorded(page_file: Path, seeded: bool, flowdir: Path | None) -> tuple[str, str | None] | None:
    """The page this project already has: the workdir's own record, else the start page waiting to be adopted."""
    own = None if seeded else report_link.recorded(page_file)
    return own or _pending(flowdir)


def _scored_run(workdir: Path) -> dict | None:
    """The scored run's run.json: ``runs/index.json`` ``scored_run``, else the run ``.last_run`` names."""
    run_dir = _scored_run_dir(workdir)
    return _json(run_dir / 'run.json') if run_dir is not None else None


def _scored_run_dir(workdir: Path) -> Path | None:
    """The scored run's folder (:func:`_scored_run`), or None when no candidate holds a readable run.json."""
    runs = workdir / 'artifacts' / 'runs'
    index = _json(runs / 'index.json')
    name = index.get('scored_run') if isinstance(index, dict) else None
    candidates: list[Path] = []
    if isinstance(name, str) and name:
        candidates.append(runs / name)
    else:
        try:
            last = (workdir / '.last_run').read_text('utf-8').strip()
        except OSError:
            last = ''
        if last:
            pointed = Path(last)
            candidates.extend([pointed] if pointed.is_absolute() else [workdir / pointed, runs / pointed])
    for run_dir in candidates:
        if _platform.is_local_drive_path(run_dir, within=(workdir,)) and isinstance(_json(run_dir / 'run.json'), dict):
            return run_dir
    return None


#: The checker codes that do not block a page: a run.json without C7 renders as before (nothing is projected).
NUMBERS_NOT_BLOCKING = frozenset({'c7_missing'})


def numbers_mismatches(workdir: Path, page_file: Path | None = None) -> list[dict]:
    """``check_customer_numbers`` over the scored run (analytics, banners) and, when given, the filled page — every
    blocking finding. ``[]`` when there is no scored run."""
    run_dir = _scored_run_dir(workdir)
    if run_dir is None:
        return []
    html = page_file.read_text('utf-8') if page_file is not None and page_file.is_file() else None
    found = check_customer_numbers.check(run_dir, report=html)
    return [f for f in found if f['code'] not in NUMBERS_NOT_BLOCKING]


def flow_continues(flowdir: Path | None, step: str) -> bool:
    """True iff an active /simulate plan lists a stage after ``step`` that is not done yet. Never for a
    milestone: it refreshes the page mid-stage and is not a hand-off, so it must never end the turn."""
    if flowdir is None or PUBLISH_POINTS[step]['milestone']:
        return False
    path = flowdir / PLAN_FILE
    plan = _json(path)
    if not isinstance(plan, dict) or plan.get('finished') or plan.get('halted'):
        return False
    try:
        if time.time() - path.stat().st_mtime > PLAN_MAX_AGE_S:
            return False
    except OSError:
        return False
    stages = [s for s in plan.get('stages') or [] if isinstance(s, str)]
    done = set(plan.get('done') or [])
    if step == 'start':
        later = stages
    elif step in stages:
        later = stages[stages.index(step) + 1:]
    else:
        return False
    return any(stage not in done for stage in later)


# ── the running state: flow_progress.json ─────────────────────────────────────────────────────


def _iso(stamp: object) -> str | None:
    """``stamp`` as ``YYYY-MM-DDTHH:MM:SSZ`` when it is an ISO time, else None."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _epoch(stamp: str | None) -> float | None:
    iso = _iso(stamp)
    return None if iso is None else datetime.fromisoformat(iso[:-1] + '+00:00').timestamp()


def _ago(now: str, seconds: object) -> str | None:
    """``now`` less ``seconds`` as an ISO time, when ``seconds`` is a non-negative finite number, else None."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 <= seconds < float('inf'):
        return None
    at = _epoch(now)
    try:
        return None if at is None else _iso(datetime.fromtimestamp(at - seconds, tz=timezone.utc).isoformat())
    except (OverflowError, OSError, ValueError):
        return None


def _same_kind(a: object, b: object, *keys: str) -> bool:
    """Whether two step sources are dicts naming the same job ``kind`` (and the same ``keys``)."""
    return isinstance(a, dict) and isinstance(b, dict) and a.get('kind') is not None and all(a.get(k) == b.get(k) for k in ('kind', *keys))


def live_plan(flowdir: Path | None) -> dict | None:
    """The /simulate plan the running state reads: present, not finished, written in the last
    ``PLAN_MAX_AGE_S``. A HALTED plan is live here — it renders as halted, never as running."""
    if flowdir is None:
        return None
    path = flowdir / PLAN_FILE
    plan = _json(path)
    if not isinstance(plan, dict) or plan.get('finished') is True or not isinstance(plan.get('stages'), list):
        return None
    try:
        if time.time() - path.stat().st_mtime > PLAN_MAX_AGE_S:
            return None
    except OSError:
        return None
    return plan


def progress_stage(plan: dict | None) -> str | None:
    """The stage a wake-up card belongs to: the live, unhalted plan's first stage not done yet."""
    if plan is None or plan.get('halted'):
        return None
    done = set(plan.get('done') or [])
    return next((s for s in plan.get('stages') or [] if s in STAGES and s not in done), None)


def stage_label(stage: str, run_mode: str | None) -> str:
    """A stage's default step; the suite's follows the workdir's ``.run_mode``."""
    return (SUITE_DEFAULT_BY_RUN_MODE.get(run_mode or '') if stage == 'suite' else None) or STAGES[stage][1]


def _run_mode(workdir: Path) -> str | None:
    try:
        return (workdir / '.run_mode').read_text('utf-8').strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _label(template: Any, completed: int | None, total: int | None) -> str:
    """One ``JOB_LABELS``/``RUN_LABEL`` value, filled. A counter shows only when its total is above 0."""
    counted, plain = template if isinstance(template, tuple) else (None, template)
    if counted is not None and completed is not None and total:
        return counted.format(c=min(completed, total), n=total)
    return plain


def _server_url() -> str | None:
    return url_guard.server_url() or None


def fetch_job(job_id: str) -> dict | None:
    """``GET <server>/jobs/<id>`` — the job envelope, or None on any failure. The bearer comes from the
    resolver, never from argv."""
    base = _server_url()
    if not base or not job_id:
        return None
    request = urllib.request.Request(f'{base}/jobs/{urllib.parse.quote(job_id, safe="")}', headers={'Accept': 'application/json'})
    token = url_guard.bearer(base)
    if token:
        # The bearer goes only to a backend the session was bound to (the session pin, hooks/session_pin.py).
        if not url_guard.is_backend_url(base):
            return None
        request.add_header('Authorization', f'Bearer {token}')
    try:
        with _no_redirect.OPENER.open(request, timeout=JOB_TIMEOUT_S) as response:  # the bearer never follows a redirect
            if response.status != 200:
                return None
            envelope = json.loads(response.read().decode('utf-8'))
    except Exception:  # noqa: BLE001 - any failure is "no job label", never a failed fill
        return None
    return envelope if isinstance(envelope, dict) and isinstance(envelope.get('kind'), str) else None


def job_label(envelope: dict) -> tuple[str | None, dict]:
    """(the step ``JOB_LABELS`` names for this envelope or None, the typed ``source``)."""
    kind, phase = envelope.get('kind'), envelope.get('phase')
    detail = envelope.get('detail') if isinstance(envelope.get('detail'), dict) else {}
    fields = JOB_COUNTERS.get(kind) if isinstance(kind, str) else None
    completed, total = (_count(detail.get(fields[0])), _count(detail.get(fields[1]))) if fields else (None, None)
    source = {'kind': kind if isinstance(kind, str) else None, 'phase': phase if isinstance(phase, str) else None, 'completed': completed, 'total': total}
    if envelope.get('status') == 'failed' or kind not in JOB_KINDS or not isinstance(phase, str):
        return None, source
    template = JOB_LABELS.get((kind, phase)) or JOB_LABELS.get(('*', phase))
    if template is None:
        return None, source
    return _label(template, completed, total), source


def run_label(run_dir: Path, total: int | None, trials: int | None) -> tuple[str, dict]:
    """The run stage's step: finished conversations (trial 1's ``*/transcript.json``) of ``total``."""
    completed = sum(1 for _ in run_dir.glob('*/transcript.json')) if run_dir.is_dir() else 0
    total = total if total is not None and total > 0 else None
    label = _label(RUN_LABEL, completed, total)
    if trials is not None and trials > 1:
        label += RUN_TRIALS.format(k=trials)
    return label, {'kind': 'run', 'phase': None, 'completed': completed, 'total': total}


def sync_pieces(workdir: Path, envelope: dict) -> list[str]:
    """``report_pieces.sync`` (read by path; absent ⇒ nothing): pull what this job has landed."""
    try:
        import report_pieces  # noqa: PLC0415 - optional sibling, read by path

        changed = report_pieces.sync(workdir, envelope)
    except Exception as error:  # noqa: BLE001 - a piece never costs the card
        print(f'publish_report: piece sync skipped: {type(error).__name__}', file=sys.stderr)
        return []
    return [str(name) for name in changed] if isinstance(changed, list) else []


def _of_hidden_run(item: dict, hidden: set[str] | frozenset) -> bool:
    """Whether a stale item is a file inside a run folder the page never reads (``hidden_runs``)."""
    parts = str(item.get('artifact') or '').split('/')
    return len(parts) > 2 and parts[:2] == ['artifacts', 'runs'] and parts[2] in hidden


#: Stale items whose ``item`` names nothing the page shows. The page counts an entry by the id it shows (a scenario,
#: finding, Eval or tool) and drops one it cannot find. A fix plan's ``fix N`` therefore reaches the page with no item,
#: and still counts as a result.
ITEMLESS_ON_PAGE = ('artifacts/fixes.md',)


def _page_item(entry: dict) -> dict:
    """One stale entry as the page can count it: a run's per-policy ``<scenario>/<policy>`` as its scenario (the
    scenario is what a rescore updates), an ``ITEMLESS_ON_PAGE`` item as none. Everything else is unchanged."""
    item, artifact = entry.get('item'), str(entry.get('artifact') or '')
    if not isinstance(item, str):
        return entry
    if artifact in ITEMLESS_ON_PAGE:
        return {key: value for key, value in entry.items() if key != 'item'}
    if artifact.startswith('artifacts/runs/') and '/' in item:
        return {**entry, 'item': item.split('/', 1)[0]}
    return entry


def page_marks(workdir: Path, hidden: set[str] | frozenset = frozenset()) -> dict[str, Any]:
    """``{sections, stale}`` from ``artifact_deps`` (read by path; absent ⇒ ``{}`` / ``[]``). A stale item inside a
    ``hidden`` run is left out: that run is not on the page, so nothing on the page predates anything through it.
    Each item is the id the page counts it by (``_page_item``), once."""
    sections: dict[str, str] = {}
    stale: list[dict] = []
    try:
        import artifact_deps  # noqa: PLC0415 - optional sibling, read by path
    except Exception:  # noqa: BLE001
        return {'sections': sections, 'stale': stale}
    try:
        found = artifact_deps.draft_sections(workdir)
        sections = {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}
    except Exception as error:  # noqa: BLE001 - the page never fails on a mark
        print(f'publish_report: draft sections unread: {type(error).__name__}', file=sys.stderr)
    try:
        found = artifact_deps.stale_items(workdir)
        for item in found if isinstance(found, list) else []:
            if isinstance(item, dict) and not _of_hidden_run(item, hidden) and (shown := _page_item(item)) not in stale:
                stale.append(shown)
    except Exception as error:  # noqa: BLE001
        print(f'publish_report: stale items unread: {type(error).__name__}', file=sys.stderr)
    return {'sections': sections, 'stale': stale}


def clean_note(note: str | None) -> tuple[str | None, str | None]:
    """(the note to show, why it was dropped). Plain words only; whitespace collapsed; capped at ``NOTE_MAX``."""
    if note is None:
        return None, None
    text = ' '.join(note.split())
    if not text:
        return None, None
    if NOTE_REFUSED.search(text):
        return None, 'note dropped: it carried a path, URL, address or markup character; the default step label shows instead'
    if len(text) > NOTE_MAX:
        text = text[: NOTE_MAX - 1].rsplit(' ', 1)[0].rstrip(' ,;:—-') + '…'
    return text, None


def build_progress(
    plan: dict | None, step: str, note: str | None, now: str, prev: dict | None, *,
    gaps: list[tuple[str, str]], point: dict | None = None, run_mode: str | None = None, source: dict | None = None,
    marks: dict | None = None, elapsed: object = None,
) -> dict[str, Any]:
    """THE running state at this publish — what ``flow_progress.json`` holds and the page renders.

    A hand-off counts its own stage done when ``gaps`` is empty (it landed: ``report_guard`` records
    it only after this turn, on the same predicate) and shows the next stage running; with ``gaps`` it
    shows that stage halted, the reason naming the first missing file. A milestone keeps its stage
    running with its own label; the start shows the first stage running. A halted plan shows its stage
    halted and nothing running (``gaps`` are that stage's). With no plan (a milestone of a direct
    call; ``fill`` never builds one for a ``plan_only`` milestone) the step's own stage stands alone. ``since`` carries over from ``prev`` while a stage keeps
    its state (or was running), so it means "since it began". The step keeps its ``since`` while it is the
    same step: the same label, or the same job ``kind`` and ``phase`` (a counter tick or a re-poll); a job
    first seen mid-way starts at ``now`` less the job's ``elapsed`` seconds. ``point`` overrides the step's table
    entry (``progress`` resolves its stage and label per fill); the step text is ``note`` > the
    milestone's label > the stage default (``stage_label``).
    """
    point = point or PUBLISH_POINTS[step]
    own = point['stage']
    if plan is None:
        ids, done, halted, started = ([own] if own in STAGES else []), set(), None, None
    else:
        ids = [s for s in plan.get('stages') or [] if s in STAGES]
        done = {s for s in plan.get('done') or [] if s in ids}
        halted = plan.get('halted') or None
        started = _iso(plan.get('started_at'))
        if own in ids and halted is None:
            if point['milestone']:
                done.discard(own)
            elif gaps and own not in done:
                halted = own  # the hand-off of a stage that did not land: the plan halts on it at this turn's end
            else:
                done.add(own)
    remaining = [s for s in ids if s not in done]
    halted_at = (halted if halted in ids else remaining[0] if remaining else None) if halted else None
    running = None if halted else remaining[0] if remaining else None
    before = {s.get('id'): s for s in (prev or {}).get('stages') or [] if isinstance(s, dict)}
    stages = []
    for sid in ids:
        state = 'done' if sid in done else 'halted' if sid == halted_at else 'running' if sid == running else 'pending'
        last = before.get(sid) or {}
        since = None
        if state != 'pending':
            kept = _iso(last.get('since')) if last.get('state') in (state, 'running') else None
            planned = _iso((plan.get('since') or {}).get(sid)) if plan is not None and isinstance(plan.get('since'), dict) else None
            since = kept or (now if state == 'running' else planned)
        stages.append({'id': sid, 'label': STAGES[sid][0], 'state': state, 'since': since})
    step_now = None
    if running is not None:
        label = note or (point.get('label') if point['milestone'] and running == own else None) or stage_label(running, run_mode)
        last_step = (prev or {}).get('step')
        last_step = last_step if isinstance(last_step, dict) else {}
        step_source = source if running == own else None
        last_source = last_step.get('source')
        same = last_step.get('label') == label or _same_kind(last_source, step_source, 'phase')
        kept = _iso(last_step.get('since')) if same else None
        first = _ago(now, elapsed) if kept is None and step_source is not None and not _same_kind(last_source, step_source) else None
        step_now = {'label': label, 'since': kept or first or now, 'source': step_source}
    reason = None
    if halted:
        reason = halted_reason(halted_at, gaps) if halted_at else 'The run stopped.'
    marks = marks or {}
    return {
        'schema': PROGRESS_SCHEMA, 'updated_at': now, 'started_at': started, 'stages': stages, 'step': step_now, 'halted_reason': reason,
        'sections': dict(marks.get('sections') or {}), 'stale': list(marks.get('stale') or []),
    }


def halted_reason(stage: str, gaps: list[tuple[str, str]]) -> str:
    """One plain sentence for the page: the stage, and the first file it did not write (``artifacts/`` dropped)."""
    if not gaps:
        return f'The run stopped during the {STAGES[stage][0]} stage.'
    first = gaps[0][0]
    first = first[len('artifacts/'):] if first.startswith('artifacts/') else first
    more = len(gaps) - 1
    missing = f'{first} was' if not more else f'{first} and {more} other file{"" if more == 1 else "s"} were'
    return f'The {STAGES[stage][0]} stage stopped before it finished: {missing} not written.'


def judged_gaps(workdir: Path, plan: dict | None, point: dict) -> list[tuple[str, str]]:
    """What the stage this state judges still owes (``stage_landed.stage_gaps``): a hand-off's own stage while the
    plan holds it unhalted and not done, else the plan's halted stage. ``[]`` when no stage is judged."""
    if plan is None:
        return []
    own, halted = point['stage'], plan.get('halted') or None
    if halted is None and not point['milestone'] and own in (plan.get('stages') or []) and own not in (plan.get('done') or []):
        stage = own
    elif halted in STAGES:
        stage = halted
    else:
        return []
    since = plan.get('since') if isinstance(plan.get('since'), dict) else {}
    return stage_landed.stage_gaps(stage, workdir, _epoch(since.get(stage)))


def _progress_home(workdir: Path, flowdir: Path | None) -> Path | None:
    art = workdir / 'artifacts'
    if art.is_dir():
        return art
    return flowdir if flowdir is not None and flowdir.is_dir() else None


def running_state(
    workdir: Path, flowdir: Path | None, step: str, note: str | None, now: str, *,
    point: dict | None = None, source: dict | None = None, marks: dict | None = None, elapsed: object = None,
) -> tuple[dict | None, bool, list[tuple[str, str]]]:
    """(the running state, whether a live plan backs it, the judged stage's gaps) — and keep
    ``flow_progress.json`` in step with it.

    Plan-backed: the file is written (in the workdir once it has artifacts/, the flow dir's copy then
    removed). No live plan: a milestone writes its own stage alone (``fill`` never calls this for a ``plan_only`` one); any other step writes no stage, so a
    run whose plan is not live never renders, and keeps only the ``sections`` / ``stale`` marks. The
    file is removed only when that state holds nothing. A step with no stage (the start, an
    edit) shows the live plan as it stands.
    """
    plan = live_plan(flowdir)
    point = point or PUBLISH_POINTS[step]
    progress = {'point': point, 'run_mode': _run_mode(workdir), 'source': source, 'marks': marks, 'elapsed': elapsed}
    if plan is not None and point['stage'] is not None and point['stage'] not in plan['stages']:
        plan = None  # a direct call while some other run's plan is live: not that run's progress
    home = _progress_home(workdir, flowdir)
    shelves = [p for p in (workdir / 'artifacts', flowdir) if p is not None]
    prev = next((doc for doc in (_json(p / PROGRESS_FILE) for p in shelves) if isinstance(doc, dict)), None)
    if plan is None:
        if not (point['milestone'] or step == 'start'):
            progress['point'] = {**point, 'stage': None}  # a hand-off or an edit: no stage runs; the marks stand alone
        if prev is not None and _iso(prev.get('started_at')) is not None:
            prev = None  # a plan's state: nothing carries over
        state = build_progress(None, step, note, now, prev, gaps=[], **progress)
        if not any(state[key] for key in ('stages', 'step', 'halted_reason', 'sections', 'stale')):
            for shelf in shelves:
                (shelf / PROGRESS_FILE).unlink(missing_ok=True)
            return None, False, []
        if home is not None:
            _write_atomic(home / PROGRESS_FILE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            if flowdir is not None and home != flowdir:
                (flowdir / PROGRESS_FILE).unlink(missing_ok=True)
        return state, False, []
    if prev is not None and _iso(prev.get('started_at')) != _iso(plan.get('started_at')):
        prev = None  # an earlier run's state: nothing carries over
    gaps = judged_gaps(workdir, plan, point)
    state = build_progress(plan, step, note, now, prev, gaps=gaps, **progress)
    if home is not None:
        _write_atomic(home / PROGRESS_FILE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        if flowdir is not None and home != flowdir:
            (flowdir / PROGRESS_FILE).unlink(missing_ok=True)
    return state, True, gaps


def refresh_progress(workdir: Path, flowdir: Path, *, refill_page: bool = True) -> None:
    """``flow_progress.json`` rebuilt from the plan as it now stands: ``hooks/simulate_continue`` calls it on every plan
    transition (a landing, a halt, a rewind, the end), so the file and the plan agree. The draft and stale marks carry
    over from the file it replaces; the step is the running stage's default. Then the local page catches up with the
    file (``refill``), in a detached process so the hook that wrote the plan never waits on a fill."""
    prev = next((doc for doc in (_json(p / PROGRESS_FILE) for p in (workdir / 'artifacts', flowdir)) if isinstance(doc, dict)), None)
    marks = {'sections': prev.get('sections') or {}, 'stale': prev.get('stale') or []} if prev is not None else None
    running_state(workdir, flowdir, 'progress', None, _now(), marks=marks)
    if refill_page:
        _spawn_refill(workdir, flowdir)


#: ``refill`` needs PyYAML, and a hook's bare ``python3`` is not promised one: the child then sources this resolver
#: first, as every skill snippet does, and runs under the interpreter it picks.
RESOLVE_PYTHON = REPO_ROOT / 'scripts' / 'resolve-python.sh'
POSIX_SH = Path('/bin/sh')


def _refill_command(args: list[str]) -> list[str]:
    """The command of a detached ``refill`` or ``page_watch.py``: this interpreter when it imports yaml, else ``$PLURAI_PY`` from
    ``resolve-python.sh`` (no ``sh`` here, as on native Windows: this interpreter, and the refill does nothing)."""
    try:
        import yaml  # noqa: F401, PLC0415 - only asked whether it imports
    except ImportError:
        if POSIX_SH.is_file():
            return [str(POSIX_SH), '-c', '. "$0"; exec "$PLURAI_PY" "$@"', str(RESOLVE_PYTHON), *args]
    return [sys.executable, *args]


def _spawn_refill(workdir: Path, flowdir: Path | None) -> None:
    """``refill`` in a detached process, so the hook that wrote the progress file never waits on a fill. Nothing starts
    when there is no local page to refill (``refill`` would refill nothing)."""
    seeded = not (workdir / 'artifacts').is_dir()
    page = (flowdir / FLOW_PAGE if flowdir is not None else None) if seeded else workdir / 'artifacts' / FLOW_PAGE
    if page is None or not page.is_file():
        return
    try:
        subprocess.Popen(  # noqa: S603 - this script, by path, under a vetted interpreter
            _refill_command([str(Path(__file__).resolve()), 'refill', str(workdir), *(('--flowdir', str(flowdir)) if flowdir else ())]),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
        )
    except OSError:
        pass  # the next fill catches the page up


def spawn_page_watch(workdir: Path, flowdir: Path | None) -> None:
    """``page_watch.py`` in a detached process, tied to the harness process this call runs under. This runs inside hooks,
    so the cheap reasons come first. Nothing starts on native Windows (a read there can fail a foreground ``os.replace``), with
    the page off, while a watcher runs, with nobody watching (:func:`_nobody_watching`) or with no live plan. ``OWNER_TIMEOUT_S``
    bounds the process table's read; past it the watcher runs untied. A failure leaves the page to its other refreshes: this
    never raises and prints nothing."""
    try:
        if os.name == 'nt' or flowdir is None or os.environ.get(OFF_SWITCH, '').strip().lower() in OFF_VALUES:
            return
        import page_watch  # noqa: PLC0415 - sibling script, only asked here

        if page_watch.lock_held(flowdir) or _nobody_watching(workdir) or live_plan(flowdir) is None:
            return
        import run_start  # noqa: PLC0415 - sibling script, only asked here

        owner = run_start.owner_pid(timeout=OWNER_TIMEOUT_S)
        command = _refill_command([str(Path(__file__).resolve().with_name('page_watch.py')), str(workdir), '--flowdir', str(flowdir),
                                   *(('--owner', str(owner)) if owner is not None else ())])
        try:
            sink = (flowdir / page_watch.ERRORS).open('ab')
        except OSError:
            sink = None
        try:
            subprocess.Popen(  # noqa: S603 - this script's sibling, by path, under a vetted interpreter
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL if sink is None else sink,
                start_new_session=True, close_fds=True,
            )
        finally:
            if sink is not None:
                sink.close()
    except Exception:  # noqa: BLE001, S110 - the watcher must never cost the fill or the hook that started it
        pass


def _progress_path(workdir: Path, flowdir: Path | None) -> Path | None:
    """The ``flow_progress.json`` an edit outside a fill rewrites: the workdir's, else the flow dir's; None when neither exists."""
    return next((p / PROGRESS_FILE for p in (workdir / 'artifacts', flowdir) if p is not None and (p / PROGRESS_FILE).is_file()), None)


#: The running step while the flow waits for the user's answer to a question it asked (:func:`wait_progress`).
WAIT_LABEL = 'Waiting for your answer'
#: Where :func:`wait_progress` keeps the step the wait covered. Not a page field: the fill drops it, and a fill writes
#: the file without it.
WAIT_KEY = 'waiting'


def wait_progress(workdir: Path, flowdir: Path | None, waiting: bool, *, refill_page: bool = True) -> bool:
    """The running stage's step says the flow waits for the user's answer (``waiting``), or says again what it said before.

    On: only while a stage is ``running``; the step reads ``WAIT_LABEL`` and the step it covered is kept under
    ``WAIT_KEY``; a step already reading ``WAIT_LABEL`` is left as it is. Off: the covered step comes back while the
    step still reads ``WAIT_LABEL``; when a halt, a resume or a fill wrote the step since, only the key goes. File only,
    like :func:`halt_progress`. Any fill rebuilds the file without the key, so the sign never outlives the next fill.
    Then the local page catches up (``refill``). False when the page shows nothing new."""
    path = _progress_path(workdir, flowdir)
    state = _json(path) if path is not None else None
    if path is None or not isinstance(state, dict):
        return False
    step = state.get('step') if isinstance(state.get('step'), dict) else None
    showing = step is not None and step.get('label') == WAIT_LABEL
    now = _now()
    if waiting:
        if showing or not any(isinstance(s, dict) and s.get('state') == 'running' for s in state.get('stages') or []):
            return False
        state.update({WAIT_KEY: {'step': step}, 'step': {'label': WAIT_LABEL, 'since': now}, 'updated_at': now})
    else:
        if WAIT_KEY not in state:
            return False
        covered = state.pop(WAIT_KEY)
        if showing:
            prior = covered.get('step') if isinstance(covered, dict) else None
            state.update(step=prior if isinstance(prior, dict) else None, updated_at=now)
    _write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    if waiting or showing:
        if refill_page:
            _spawn_refill(workdir, flowdir)
        return True
    return False


#: Beside ``flow_progress.json``: the step :func:`progress_line` said last (its label and ``since``), so each is said once.
PROGRESS_SAID = '.progress_said'
#: The MAIN thread's own record of the step it said (the relay hook's, ``hooks/pending_register``): a sub-agent's
#: ``progress_line.py`` call writes ``PROGRESS_SAID`` only, and its words never reach the chat.
PROGRESS_TOLD = '.progress_told'


def progress_line(workdir: Path | None, flowdir: Path | None, *, stamp: str = PROGRESS_SAID) -> str | None:
    """THE line the chat says mid-flow (``progress_line.py``): the running step's bare label, as the page shows it — a
    milestone's label, a job's or a run's step, or the stage's default; never a ``Stage —`` prefix. None while no stage
    runs, while the flow waits for the user's answer, or when this step was said already. Never raises."""
    try:
        path = _progress_path(workdir, flowdir) if workdir is not None else \
            (flowdir / PROGRESS_FILE if flowdir is not None and (flowdir / PROGRESS_FILE).is_file() else None)
        state = _json(path) if path is not None else None
        if path is None or not isinstance(state, dict) or not any(isinstance(s, dict) and s.get('state') == 'running' for s in state.get('stages') or []):
            return None
        step = state.get('step') if isinstance(state.get('step'), dict) else {}
        label = ' '.join(str(step.get('label') or '').split())
        if not label or label == WAIT_LABEL:
            return None
        said, record = f'{label}\t{step.get("since") or ""}', path.parent / stamp
        if record.is_file() and record.read_text(encoding='utf-8', errors='replace') == said:
            return None
        _write_atomic(record, said)
        return label
    except (OSError, ValueError):
        return None


def progress_told(workdir: Path | None, flowdir: Path | None) -> None:
    """The main thread said the line its own ``progress_line.py`` call printed: record it as told (``PROGRESS_TOLD``), so the
    relay does not say it again. Never raises."""
    try:
        path = _progress_path(workdir, flowdir) if workdir is not None else (flowdir / PROGRESS_FILE if flowdir is not None else None)
        if path is not None and (path.parent / PROGRESS_SAID).is_file():
            _write_atomic(path.parent / PROGRESS_TOLD, (path.parent / PROGRESS_SAID).read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return None


def halt_progress(workdir: Path, flowdir: Path | None, reason: str | None = None, *, refill_page: bool = True) -> bool:
    """A flow that stopped without a hand-off: each ``running`` stage in ``flow_progress.json`` reads ``halted``, with
    no step, and ``halted_reason`` is the first sentence of ``reason`` when ``clean_note`` keeps it (a sentence carrying
    ids in backticks is dropped), else the stage's default (``halted_reason``). Touches the file only, never the
    ``/simulate`` plan: a plan's halt is ``refresh_progress``'s, and finds nothing running here. A resumed flow's entry
    un-halts its stage (:func:`resume_progress`). Then the local page catches up (``refill``). False when
    nothing was running."""
    path = _progress_path(workdir, flowdir)
    state = _json(path) if path is not None else None
    if path is None or not isinstance(state, dict):
        return False
    running = [s for s in state.get('stages') or [] if isinstance(s, dict) and s.get('state') == 'running']
    if not running:
        return False
    for stage in running:
        stage['state'] = 'halted'
    first = re.split(r'(?<=[.!?])\s', ' '.join((reason or '').split()), maxsplit=1)[0]
    note, _ = clean_note(first)
    stage_id = running[0].get('id')
    state.update(step=None, updated_at=_now(),
                 halted_reason=note or (halted_reason(stage_id, []) if stage_id in STAGES else 'The run stopped.'))
    _write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    if refill_page:
        _spawn_refill(workdir, flowdir)
    return True


def resume_progress(workdir: Path, command: str) -> bool:
    """A flow entry (``open_epoch.py``) un-halts its own stage, the inverse of :func:`halt_progress`: in a no-plan
    ``artifacts/flow_progress.json`` (``started_at`` null, as :func:`running_state` reads it) the ``halted`` stage that
    ``command`` publishes (its ``PUBLISH_POINTS`` stage) reads ``running`` again, with no ``halted_reason`` and the
    stage's default step. A plan's state (its transitions own it) and another command's halted stage stay as they are.
    Then the local page catches up (``refill``). False when nothing changed."""
    path = workdir / 'artifacts' / PROGRESS_FILE
    state = _json(path)
    if not isinstance(state, dict) or _iso(state.get('started_at')) is not None:
        return False
    owned = {p['stage'] for p in PUBLISH_POINTS.values() if p['command'] == command and p['stage'] in STAGES}
    halted = [s for s in state.get('stages') or [] if isinstance(s, dict) and s.get('state') == 'halted' and s.get('id') in owned]
    if not halted:
        return False
    now = _now()
    halted[0]['state'] = 'running'
    state.update(step={'label': stage_label(halted[0]['id'], _run_mode(workdir)), 'since': now}, halted_reason=None, updated_at=now)
    _write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    _spawn_refill(workdir, None)
    return True


def refill(workdir: Path, flowdir: Path | None, vendored: Path = VENDORED) -> bool:
    """Refill the LOCAL page in place from the workdir as it stands — no publish, no fill record, no link line, no
    running-state write — so a page open in a browser (it polls ``report.data.js``) catches up with a
    rewritten ``flow_progress.json`` or any input the watcher sees land (``page_watch.py``). A page that still is the
    published one stays so: its recorded sha moves with it, so a background refresh never turns the link into a path. Only a page a fill already wrote is refilled; anything that would
    skip a fill (the off switch, nothing vendored, internal text) or an interpreter without PyYAML refills nothing;
    a refused fill's kept page is left as it is until the next fill (``report_link.fill_refused``), and internal
    text leaves the page as it is (nothing takes it down), so a refresh landing between a fill and its
    ``done --url`` never costs the page its recorded URL. True when the page was refilled."""
    if os.environ.get(OFF_SWITCH, '').strip().lower() in OFF_VALUES or not (vendored / '_vendor.json').is_file():
        return False
    try:
        import yaml  # noqa: F401, PLC0415 - the vendored fill reads the YAML inputs with it; without it a refill would drop them
    except ImportError:
        return False
    seeded = not (workdir / 'artifacts').is_dir()
    if seeded and (flowdir is None or not flowdir.is_dir()):
        return False
    page_file = flowdir / FLOW_PAGE if seeded else workdir / 'artifacts' / 'report.html'
    if not page_file.is_file() or report_link.fill_refused(_record_dir(workdir, flowdir)):
        return False  # a refused fill's kept page is left as it is until the next fill
    suite_open = not seeded and _suite_open(workdir, flowdir)
    hidden = frozenset() if seeded else hidden_runs(workdir, flowdir, suite_open)
    replaced = None if seeded else page_findings(workdir, suite_open, hidden, in_progress=_renders_in_progress(vendored))
    if not seeded and customer_text_guard.violations(workdir, {f'artifacts/{k}': v for k, v in (replaced or {}).items()}):
        return False
    progress = _json(flowdir / PROGRESS_FILE) if seeded else None
    line: dict[str, Any] = dict.fromkeys(('version', 'kept', 'cut', 'dropped', 'misread', 'misread_count', 'bytes', 'filled_at'))
    published = None if seeded else report_link.recorded(page_file)
    if published is not None and published[1] != _sha(page_file):
        published = None  # a fill moved the page on since that publish: the link is no longer this page's
    skip, _ = _fill_page(workdir, seeded, page_file, vendored, line, progress if isinstance(progress, dict) else None, hidden, replaced,
                         refresh=True, flowdir=flowdir)
    if skip is None and published is not None:
        report_link.record(page_file, published[0])
    return skip is None


def integrity_line(dropped: list, misread: list, misread_count: int) -> str | None:
    if not dropped and not misread:
        return None
    count = len(dropped) + max(misread_count, len(misread))
    return INTEGRITY.format(count=f'{count} item' + ('' if count == 1 else 's'))


def _handed_off(workdir: Path, plan: dict) -> bool:
    """Did THIS run's suite build hand off, by the phase record: ``bench_manifest.handed_off`` (the one finished rule:
    the last ``report landed`` is followed only by the run's top-up), with that ``report landed`` at or after the plan's
    ``since.suite``. The record decides, not the anchor file (``simulate_plan._handed_off``): an anchor written over a
    reset-last or unreadable record says ``handed_off`` too. Anything unreadable is False — the loop stays hidden."""
    try:
        import bench_manifest  # noqa: PLC0415 - scripts/ is on sys.path; loaded only when a plan holds the suite open
    except Exception:  # noqa: BLE001 - no rule to read: not handed off
        return False
    state = _json(workdir / 'artifacts' / 'suite_state.json')
    phases = state.get('phases') if isinstance(state, dict) else None
    if not isinstance(phases, list) or not bench_manifest.handed_off(phases):
        return False
    landed = [p.get('at') for p in phases if isinstance(p, dict) and (p.get('phase'), p.get('status')) == (bench_manifest.HANDOFF_PHASE, 'landed')]
    since = plan.get('since') if isinstance(plan.get('since'), dict) else {}
    at, began = _epoch(landed[-1] if landed else None), _epoch(since.get('suite'))
    return at is not None and began is not None and at >= began


def _suite_open(workdir: Path, flowdir: Path | None) -> bool:
    """Whether the suite has not handed off yet, so its quality loop stays internal. Keyed on the hand-off, never on
    the plan being active: a suite that halted or was abandoned before handing off keeps its loop hidden.

    The live plan's first stage not done — halted or not — is the suite, and its build has not handed off since the
    stage began (:func:`_handed_off`: a capacity halt can outlast the hand-off it interrupted, and the page never hides
    a final run the hand-off already showed); with no live plan, ``suite_state.json`` with no landed final set, no
    ``suite.json`` yet, or a ``suite.json`` still a draft of a set it records (the hand-off writes the final one)."""
    plan = live_plan(flowdir)
    if plan is not None:
        done = set(plan.get('done') or [])
        first = next((s for s in plan.get('stages') or [] if s in STAGES and s not in done), None)
        return first == 'suite' and not _handed_off(workdir, plan)
    state = _json(workdir / 'artifacts' / 'suite_state.json')
    phases = state.get('phases') if isinstance(state, dict) else None
    if not isinstance(phases, list):
        return False
    if not any(isinstance(p, dict) and p.get('phase') == 'final_scenarios' and p.get('status') == 'landed' for p in phases):
        return True
    # Abandoned before the hand-off: no suite.json yet, or a draft of a set the suite itself built (qualification or
    # final). A draft of a set the suite never recorded (edited or regenerated after the run) is not the suite reopening.
    suite = _json(workdir / 'artifacts' / 'suite.json')
    if suite is None:
        return True
    own = {str(p['ids']['scenario_set']) for p in phases if isinstance(p, dict) and isinstance(p.get('ids'), dict) and p['ids'].get('scenario_set')}
    return isinstance(suite, dict) and suite.get('draft') is True and str(suite.get('scenario_set_id') or '') in own


def _final_run(workdir: Path) -> bool:
    """Whether a run the suite plays now is its final run: ``suite_state.json``'s last record is the final set landing
    or the final run's diagnostics (``final_evals``). Any other record, or none, is the calibration loop."""
    state = _json(workdir / 'artifacts' / 'suite_state.json')
    phases = state.get('phases') if isinstance(state, dict) else None
    last = phases[-1] if isinstance(phases, list) and phases and isinstance(phases[-1], dict) else {}
    return (last.get('phase'), last.get('status')) == ('final_scenarios', 'landed') or last.get('phase') == 'final_evals'


def hidden_runs(workdir: Path, flowdir: Path | None, suite_open: bool | None = None) -> set[str]:
    """Run folders the page never reads: every qualification run, and — while the suite is still being built — a
    run not indexed yet or the suite's own final run. The suite's runs are its internal calibration. ``suite_open``
    is ``_suite_open`` when the caller already has it."""
    runs = workdir / 'artifacts' / 'runs'
    if not runs.is_dir():
        return set()
    index = _json(runs / 'index.json')
    rows = index.get('runs') if isinstance(index, dict) else None
    stage_of = {str(r.get('name')): r.get('stage') for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
    suite_open = _suite_open(workdir, flowdir) if suite_open is None else suite_open
    hidden = set()
    for folder in runs.iterdir():
        if not folder.is_dir():
            continue
        stage = stage_of.get(folder.name)
        if stage is None and QUALIFICATION_NAME.search(folder.name):
            stage = 'qualification'  # not indexed: the vendored page's own naming rule (fill_report.QUALIFICATION)
        if stage == 'qualification' or (suite_open and stage in (None, 'final')):
            hidden.add(folder.name)
    return hidden


#: What a finding's ``found_during`` says when the suite's quality loop found it (``record_finding.STAGES``).
LOOP_FINDING = 'quality loop'
FINDINGS_MD, FINDINGS_ROWS = 'agent_findings.md', 'agent_findings.jsonl'
#: The filled page's kind in `customer_text_vocabulary.json`: every text it embeds is the customer's.
PAGE_KIND = 'report_html'
_MD_SECTION = re.compile(r'^## \d+\.', re.M)
_MD_FOUND = re.compile(r'^- \*\*found during:\*\*\s*(.+?)\s*$', re.M)
_MD_ID = re.compile(r'^- \*\*finding id:\*\*\s*`([^`]+)`', re.M)
_MD_RUN = re.compile(r'^- \*\*run:\*\*\s*(?:runs/)?([^\s·]+)[^\n]*\n', re.M)


def page_findings(workdir: Path, suite_open: bool, hidden: set[str] | frozenset, *, in_progress: bool = False) -> dict[str, str] | None:
    """The page's copy of ``agent_findings.md`` / ``.jsonl`` and of ``auto_regressions.json`` (``regressions.page_copy``:
    the kept / not-kept lists counted as the chat line counts them, in customer wording); always without a ``run:`` line naming a hidden run, so the
    page says "first caught building the Suite" rather than naming the internal qualification run, and without the
    field lines that feed our own loop (``customer_text_guard.customer_copy``). The jsonl copy, whatever its source, has
    its machine paths shortened inside each string value (:func:`short_paths`); once the suite is handed off,
    ``fixes.md`` is overlaid by its page copy (``scored_findings.fixes_for_page``) when that differs. None when no file
    needs a copy: once the gate's plan exists its copy is always overlaid, so the page reads through the copy view on
    every fill (one more ``copy_for_page`` + ``page_folds`` per tick).

    While the suite is open, a page that does not render in-progress findings gets no quality-loop finding
    (``loop_free_findings``, renumbered): it would show one as settled. With ``in_progress`` (the vendored page declares
    ``preliminary_findings.PAGE_CAPABILITY``) the loop's filed findings stay, each jsonl row marked ``in_progress``
    while the suite is open, and the qualification rows ``preliminary_findings.rows`` derives are appended to the
    jsonl. A derivation that fails costs only those rows, never the fill."""
    out = dict(loop_free_findings(workdir) or {}) if suite_open and not in_progress else {}
    if in_progress:
        rows = in_progress_rows(workdir, suite_open)
        if rows is not None:
            out[FINDINGS_ROWS] = rows
    path = workdir / 'artifacts' / FINDINGS_MD
    md = out.get(FINDINGS_MD)
    if md is None and path.is_file():
        try:
            md = path.read_text('utf-8')
        except (OSError, UnicodeDecodeError):
            md = None
    disk_md = md
    if not suite_open and md is not None:
        # Once a scored final run exists, the customer reads only what it reproduces, one finding per defect (3i).
        rows_path = workdir / 'artifacts' / FINDINGS_ROWS
        try:
            rows = out.get(FINDINGS_ROWS, rows_path.read_text('utf-8') if rows_path.is_file() else None)
        except (OSError, UnicodeDecodeError):
            rows = None
        md, scored_rows = scored_findings.copy_for_page(workdir, md, rows)
        if scored_rows != rows and scored_rows is not None:
            out[FINDINGS_ROWS] = scored_rows
    if md is not None:
        # The fields that feed our own loop (`customer_text_guard.customer_copy`) never reach the page.
        stripped = customer_text_guard.customer_copy(f'artifacts/{FINDINGS_MD}', md)
        stripped = _MD_RUN.sub(lambda m: '' if m.group(1) in hidden else m.group(0), stripped) if hidden else stripped
        if FINDINGS_MD in out or stripped != disk_md:
            out[FINDINGS_MD] = stripped
    _short_row_paths(workdir, out)
    if not suite_open:
        _fixes_copy(workdir, out)
    # The regressions list: one count with the chat line (`regressions.page_copy`), in customer wording.
    try:
        import regressions  # noqa: PLC0415 - sibling: the regressions directory's one reader

        regression_copy = regressions.page_copy(workdir)
    except Exception as error:  # noqa: BLE001 - a derivation never costs the fill; the page keeps the plan as written
        print(f'publish_report: regressions page copy skipped: {type(error).__name__}: {error}', file=sys.stderr)
    else:
        if regression_copy is not None:
            out[regressions.AUTO_PLAN_FILENAME] = regression_copy
    return out or None


FIXES_MD = 'fixes.md'
_VENDORED_FILL: list[Any] = []


def _vendored_fill() -> Any | None:
    """The vendored ``fill_report`` module, imported by path once (None when it cannot be: said on stderr)."""
    if not _VENDORED_FILL:
        try:
            import importlib.util  # noqa: PLC0415

            spec = importlib.util.spec_from_file_location('_vendored_fill_report', VENDORED / 'fill_report.py')
            module = importlib.util.module_from_spec(spec) if spec is not None and spec.loader is not None else None
            if module is None:
                raise ImportError(f'no module at {VENDORED / "fill_report.py"}')
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _VENDORED_FILL.append(module if hasattr(module, 'MACHINE_PATH') and hasattr(module, 'shareable') else None)
        except Exception as error:  # noqa: BLE001 - a page copy left as it is never costs the fill
            print(f'publish_report: the findings rows keep their paths ({type(error).__name__}: {error})', file=sys.stderr)
            _VENDORED_FILL.append(None)
    return _VENDORED_FILL[0]


def short_paths(rows_text: str) -> str:
    """``agent_findings.jsonl`` with every machine path inside a string value shortened the way the vendored fill
    shortens them (``fill_report.MACHINE_PATH`` → ``shareable``), each changed line re-dumped: the fill then finds no path
    to cut, so it never cuts one through a JSON escape (the page drops a line that no longer parses). Unchanged when the
    vendored fill cannot be imported."""
    fill = _vendored_fill()
    if fill is None:
        return rows_text

    def short(value: Any) -> Any:
        if isinstance(value, str):
            return fill.MACHINE_PATH.sub(lambda m: m.group(0) if m['sandbox'] else fill.shareable(m.group(0)), value)
        if isinstance(value, dict):
            return {k: short(v) for k, v in value.items()}
        return [short(v) for v in value] if isinstance(value, list) else value

    lines = []
    for line in rows_text.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            lines.append(line)
            continue
        shortened = short(doc)
        lines.append(line if shortened == doc else json.dumps(shortened, ensure_ascii=False, sort_keys=True))
    return '\n'.join(lines) + ('\n' if rows_text.endswith('\n') else '')


def _short_row_paths(workdir: Path, out: dict[str, str]) -> None:
    """Put the page's jsonl copy (``out``, else the disk file) through :func:`short_paths` into ``out`` when it changes."""
    path = workdir / 'artifacts' / FINDINGS_ROWS
    try:
        rows = out.get(FINDINGS_ROWS, path.read_text('utf-8') if path.is_file() else None)
    except (OSError, UnicodeDecodeError):
        return
    if rows is not None and (shortened := short_paths(rows)) != rows:
        out[FINDINGS_ROWS] = shortened


def _fixes_copy(workdir: Path, out: dict[str, str]) -> None:
    """Put the page's copy of ``fixes.md`` (``scored_findings.fixes_for_page``) into ``out`` when it differs from the file."""
    path = workdir / 'artifacts' / FIXES_MD
    try:
        text = path.read_text('utf-8') if path.is_file() else None
        copy = scored_findings.fixes_for_page(workdir, text) if text is not None else None
    except Exception as error:  # noqa: BLE001 - a derivation never costs the fill; the page keeps the plan as written
        print(f'publish_report: fix plan page copy skipped: {type(error).__name__}: {error}', file=sys.stderr)
        return
    if copy is not None and copy != text:
        out[FIXES_MD] = copy


def in_progress_rows(workdir: Path, suite_open: bool) -> str | None:
    """The page copy of ``agent_findings.jsonl`` for a page that renders in-progress findings: the disk rows, a quality
    loop one marked ``in_progress`` while the suite is open, then the qualification rows (``preliminary_findings``).
    None when that is the disk file unchanged."""
    path = workdir / 'artifacts' / FINDINGS_ROWS
    try:
        disk = path.read_text('utf-8') if path.is_file() else ''
    except (OSError, UnicodeDecodeError):
        return None
    lines = []
    for line in disk.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            doc = None
        if suite_open and isinstance(doc, dict) and doc.get('found_during') == LOOP_FINDING:
            line = json.dumps({**doc, 'in_progress': True}, ensure_ascii=False, sort_keys=True)
        lines.append(line)
    try:
        import preliminary_findings  # noqa: PLC0415 - optional sibling, read by path

        lines += [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in preliminary_findings.rows(workdir, suite_open=suite_open)]
    except Exception as error:  # noqa: BLE001 - a derivation never costs the fill
        print(f'publish_report: preliminary findings skipped: {type(error).__name__}: {error}', file=sys.stderr)
    text = '\n'.join(lines) + ('\n' if lines else '')
    return None if text == disk else text


def loop_free_findings(workdir: Path) -> dict[str, str] | None:
    """``agent_findings.md`` / ``.jsonl`` without the quality loop's own findings (renumbered), as replacement
    texts by file name; None when there are none to leave out. Shown again once the suite hands them over."""
    art = workdir / 'artifacts'
    out: dict[str, str] = {}
    try:
        rows = (art / FINDINGS_ROWS).read_text('utf-8').splitlines() if (art / FINDINGS_ROWS).is_file() else []
        md = (art / FINDINGS_MD).read_text('utf-8') if (art / FINDINGS_MD).is_file() else ''
    except (OSError, UnicodeDecodeError):
        return None
    kept_rows, loop_ids = [], set()
    for row in rows:
        try:
            doc = json.loads(row)
            loop = doc.get('found_during') == LOOP_FINDING
        except (ValueError, AttributeError):
            doc, loop = None, False
        if loop:
            loop_ids.add(str(doc.get('id')))
        else:
            kept_rows.append(row)
    if len(kept_rows) != len(rows):
        out[FINDINGS_ROWS] = '\n'.join(kept_rows) + ('\n' if kept_rows else '')
    starts = [m.start() for m in _MD_SECTION.finditer(md)]
    if starts:
        head, sections = md[:starts[0]], [md[a:b] for a, b in zip(starts, [*starts[1:], len(md)])]
        def from_loop(sec: str) -> bool:
            ids = {m.group(1) for m in _MD_ID.finditer(sec)}
            return bool(ids & loop_ids) or any(m.group(1).lower().startswith(LOOP_FINDING) for m in _MD_FOUND.finditer(sec))

        keep = [sec for sec in sections if not from_loop(sec)]
        if len(keep) != len(sections):
            out[FINDINGS_MD] = head + ''.join(_MD_SECTION.sub(f'## {n}.', sec, count=1) for n, sec in enumerate(keep, 1))
    return out or None


_RUN_FILE = re.compile(r'artifacts/runs/[^/]+/run\.json')


def page_sections(workdir: Path, kept: list, replaced: dict[str, str] | None = None) -> set[str]:
    """The ``GATED_ANCHORS`` this fill's page draws, from the files it read (``kept``): ``run`` with a Run; ``gaps``
    with a Run, a recorded finding, a fix plan or a production failure. A findings file is read as the page got it
    (``replaced``)."""
    files = {str(path) for path in kept}
    shown = set()
    if any(_RUN_FILE.fullmatch(path) for path in files):
        shown.add('run')
    production = _json(workdir / 'artifacts' / 'trace_failures.json') if 'artifacts/trace_failures.json' in files else None
    failures = isinstance(production, dict) and bool(production.get('failures'))

    def text(name: str) -> str:
        if f'artifacts/{name}' not in files:
            return ''
        if replaced and name in replaced:
            return replaced[name]
        try:
            return (workdir / 'artifacts' / name).read_text('utf-8')
        except (OSError, UnicodeDecodeError):
            return ''

    recorded = bool(text(FINDINGS_ROWS).strip()) or _MD_SECTION.search(text(FINDINGS_MD)) is not None
    if shown or failures or recorded or 'artifacts/fixes.md' in files:
        shown.add('gaps')
    return shown


def landing_anchor(anchor: str | None, shown: set[str]) -> str | None:
    """``anchor``, or None when it is a ``GATED_ANCHORS`` section the page has not drawn (``page_sections``)."""
    return anchor if anchor not in GATED_ANCHORS or anchor in shown else None


#: The run.json files the vendored page reads (``fill_report`` RUN), relative to ``artifacts/runs``. simulation-report
#: 0.56.1 reads no trial's run.json: a later trial's conversations come off the run's ``scores`` and each ``verdicts.json``.
PAGE_RUN_FILES = ('*/run.json',)


#: The tool-event files the vendored page reads (``fill_report`` TOOL_EVENTS, TRIAL_TOOL_EVENTS), relative to a run folder.
PAGE_TOOL_EVENTS = ('*/tool_events.json', 'trial_*/*/tool_events.json')


def page_runs(workdir: Path, hidden: set[str] | frozenset = frozenset()) -> dict[str, str]:
    """``{path under artifacts/runs: the file as the page reads it}`` for every shown file whose page copy differs from
    it: each run.json without the rows the page cannot type (``run_numbers.page_rows``), and each tool-event file with the
    operator's identity replaced by its kind (``operator_identity.redact`` over the run's captured markers; a run with
    no markers file is left as it is). Only what the page shows of the agent's tool calls: the graded cells and the
    agent's replies are never rewritten."""
    runs = workdir / 'artifacts' / 'runs'
    out: dict[str, str] = {}
    for run_json in sorted(p for pattern in PAGE_RUN_FILES for p in runs.glob(pattern)) if runs.is_dir() else []:
        rel = run_json.relative_to(runs).as_posix()
        run = _json(run_json)
        if rel.split('/', 1)[0] in hidden or not isinstance(run, dict):
            continue
        page = run_numbers.page_rows(run)
        if page != run:
            out[rel] = json.dumps(page, ensure_ascii=False, indent=2) + '\n'
    out.update(_redacted_tool_events(runs, hidden))
    return out


def _redacted_tool_events(runs: Path, hidden: set[str] | frozenset) -> dict[str, str]:
    """``{path under artifacts/runs: the tool-event file with the run's operator markers redacted}`` for each one that
    changes (:func:`page_runs`)."""
    out: dict[str, str] = {}
    try:
        import operator_identity  # noqa: PLC0415 - sibling: the markers' one reader and redactor
    except ImportError as error:
        print(f'publish_report: tool events keep the operator identity ({error})', file=sys.stderr)
        return out
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir() and p.name not in hidden) if runs.is_dir() else []:
        markers = operator_identity.read_markers(run_dir)
        if not markers:
            continue
        for path in sorted(p for pattern in PAGE_TOOL_EVENTS for p in run_dir.glob(pattern)):
            try:
                text = path.read_text('utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            redacted = operator_identity.redact(text, markers)
            if redacted != text:
                out[path.relative_to(runs).as_posix()] = redacted
    return out


def _mirror(source: Path, mirror: Path, projected: dict[str, str], left_out: set[str] | frozenset = frozenset()) -> None:
    """``mirror`` as a symlink copy of ``source``, without ``left_out``, each ``projected`` file (its path relative to
    ``source``) written in place of the original with the original's mtime: the page dates its inputs off mtimes."""
    mirror.mkdir()
    for entry in source.iterdir():
        inner = {rel.split('/', 1)[1]: text for rel, text in projected.items() if rel.startswith(f'{entry.name}/')}
        if entry.name in left_out:
            continue
        if entry.name in projected:
            (mirror / entry.name).write_text(projected[entry.name], 'utf-8')
            stat = entry.stat()
            os.utime(mirror / entry.name, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        elif inner and entry.is_dir():
            _mirror(entry, mirror / entry.name, inner)
        else:
            (mirror / entry.name).symlink_to(entry)


def page_view(workdir: Path, scratch: Path, hidden: set[str] | frozenset = frozenset(), replaced: dict[str, str] | None = None,
              runs: dict[str, str] | None = None) -> Path:
    """The tree the vendored fill reads: ``workdir`` itself, or a symlink mirror of it when the page must not read
    it as it is — some run folders are ``hidden`` (left out of the mirror's ``artifacts/runs``), a findings file
    is ``replaced`` (its page copy, same mtime), or a ``run.json`` is projected for the page (``runs``, path under
    ``artifacts/runs``, :func:`page_runs`; same mtime)."""
    replaced = dict(replaced or {})
    projected = dict(runs or {})
    if not hidden and not replaced and not projected:
        return workdir
    view = scratch / 'view'
    (view / 'artifacts').mkdir(parents=True)
    for entry in workdir.iterdir():
        if entry.name != 'artifacts':
            (view / entry.name).symlink_to(entry)
    for entry in (workdir / 'artifacts').iterdir():
        if entry.name in replaced:
            continue
        if (hidden or projected) and entry.name == 'runs' and entry.is_dir():
            _mirror(entry, view / 'artifacts' / 'runs', projected, hidden)
            continue
        (view / 'artifacts' / entry.name).symlink_to(entry)
    for name, text in replaced.items():
        original = workdir / 'artifacts' / name
        (view / 'artifacts' / name).write_text(text, 'utf-8')
        try:
            stat = original.stat()
        except FileNotFoundError:
            continue  # a page copy with no disk file (rows derived before any finding was filed) keeps its own mtime
        os.utime(view / 'artifacts' / name, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    return view


def _driven(flowdir: Path | None, command: str, driven_by: tuple[str, ...] | None = None) -> bool:
    """Whether the open flow window runs this step as a sub-step: ``driven_by`` when the point names
    it, else ``hooks/flow_arm.FLOW_DRIVERS``."""
    if flowdir is None:
        return False
    try:
        window = (flowdir / FLOW_MARKER).read_text('utf-8').strip()
    except OSError:
        return False
    return window in (driven_by if driven_by is not None else flow_drivers().get(command, ()))


def _not_final(workdir: Path) -> bool:
    run = _scored_run(workdir)
    stage = run.get('stage') if isinstance(run, dict) else None
    return stage is not None and stage != 'final'


def fill(
    workdir: Path, step: str, flowdir: Path | None, as_command: str | None, vendored: Path, note: str | None = None, *,
    job: str | None = None, run_dir: Path | None = None, run_total: int | None = None, trials: int | None = None,
    local: bool = False,
) -> dict[str, Any]:
    point = PUBLISH_POINTS[step]
    if step != 'progress' and (job is not None or run_dir is not None):
        raise CallerBug('--job and --run-dir belong to --step progress')
    line: dict[str, Any] = {
        'publish': False, 'skip': None, 'report': None, 'page': None, 'anchor': landing_anchor(point['anchor'], set()), 'integrity_line': None,
        'flow_continues': flow_continues(flowdir, step), 'version': None, 'kept': [], 'cut': [], 'dropped': [], 'misread': [],
        'misread_count': 0, 'bytes': None, 'filled_at': None, 'deliverable_gaps': [], 'internal_text': [], 'numbers': [],
    }
    now = _now()
    note, note_dropped = clean_note(note)
    has_artifacts = (workdir / 'artifacts').is_dir()
    stage = progress_stage(live_plan(flowdir)) if step == 'progress' and has_artifacts else None
    # The wake-up card with no running stage, or a plan-only point its plan is not running: no state, no page.
    no_plan = (step == 'progress' and stage is None) or (point['plan_only'] and progress_stage(live_plan(flowdir)) != point['stage'])
    source = pieces = elapsed = None
    if stage is not None:
        label = None
        if job is not None:
            envelope = fetch_job(job)
            if envelope is not None:
                pieces = sync_pieces(workdir, envelope)  # before the fill, so a piece that just landed is on this page
                label, source = job_label(envelope)
                elapsed = envelope.get('elapsed_seconds')  # a job first seen mid-way: its step started this long ago
        elif run_dir is not None:
            if stage == 'suite':
                label, source = FINAL_RUN_LABEL if _final_run(workdir) else SUITE_RUN_LABEL, None
            else:
                label, source = run_label(run_dir, run_total, trials)
        point = {**point, 'stage': stage, 'label': label}
    suite_open = has_artifacts and _suite_open(workdir, flowdir)  # once per fill: the runs and findings it hides agree
    hidden = hidden_runs(workdir, flowdir, suite_open) if has_artifacts else frozenset()
    marks = page_marks(workdir, hidden) if has_artifacts else {'sections': {}, 'stale': []}
    live: tuple[dict | None, bool] = (None, False)
    gaps: list[tuple[str, str]] = []
    # Otherwise _seeded refuses below (write no state for a caller bug), or a fill with no plan to show leaves the state alone.
    if (step == 'start' or has_artifacts) and not no_plan:
        state, backed, gaps = running_state(workdir, flowdir, step, note, now, point=point, source=source, marks=marks, elapsed=elapsed)
        live = (state, backed)
        line['deliverable_gaps'] = [{'path': path, 'writer': writer} for path, writer in gaps]
    run_end, live_page = publish_moment(workdir, flowdir, step, point, bool(gaps), line['flow_continues'])
    line['run_end'] = run_end
    stderr = ''
    skip = None
    signature = None
    if os.environ.get(OFF_SWITCH, '').strip().lower() in OFF_VALUES:
        skip = 'disabled'
    elif not (vendored / '_vendor.json').is_file():
        skip = 'not_vendored'
    elif no_plan:  # before the workdir check: a point with no plan to show needs no artifacts/
        skip = 'no_plan'
    elif step == 'end' and not _run_of_here(workdir, flowdir):  # no /simulate run of this workdir in this session: no run to end
        skip = 'no_plan'
    elif step == 'end' and not has_artifacts:  # a run stopped before its scan wrote a page: nothing to publish
        skip = 'no_page'
    else:
        seeded = _seeded(workdir, step)
        page_file = _page_file(workdir, seeded, flowdir)
        if _driven(flowdir, as_command or point['command'], point.get('driven_by')):
            skip = 'driven'
        elif point['final_run_only'] and _not_final(workdir):
            skip = 'not_final'
        else:
            replaced = None if seeded else page_findings(workdir, suite_open, hidden, in_progress=_renders_in_progress(vendored))
            # The page itself is checked once filled (`_fill_page`): the one on disk is the last fill's.
            kinds = set(customer_text_guard.load().get('artifacts') or {}) - {PAGE_KIND}
            leaks = [] if seeded else customer_text_guard.violations(workdir, {f'artifacts/{k}': v for k, v in (replaced or {}).items()}, kinds=kinds)
            # The copy is this render's, so it is decided before the render: an `unchanged` end, known only after it, rewrites
            # the copy too (the same page its link shows), and hands nothing out.
            publish_copy: Path | None = page_file.parent / PUBLISH_DIR / page_file.name
            if local or seeded or live_page or _workdir.harness() in LOCAL_ONLY_HARNESSES or not inside_session_cwd(publish_copy):
                publish_copy = None
            if leaks:  # the last page stays: `done` and `report_link link` read the refusal and never hand it out as this step's
                skip, line['internal_text'] = 'internal_text', [str(leak) for leak in leaks]
            else:
                skip, stderr = _fill_page(workdir, seeded, page_file, vendored, line, live[0], hidden, replaced, flowdir=flowdir,
                                          publish_copy=publish_copy)
            if skip in report_link.REFUSED and not point['milestone']:
                line['say'] = [NOT_UPDATED_LINE if page_file.is_file() else NO_PAGE_LINE]
            if skip is None:
                line['anchor'] = landing_anchor(point['anchor'], page_sections(workdir, line['kept'] or [], replaced))
                recorded = _recorded(page_file, seeded, flowdir)
                line['page'] = recorded[0] if recorded else None
                signature = page_signature(live[0], marks, line['kept'] or [], None if seeded else workdir, replaced)
                if _workdir.harness() in LOCAL_ONLY_HARNESSES:
                    skip = 'local_only'
                    _tell_local_page(line, workdir, page_file, seeded, step)
                elif live_page:
                    skip = 'live_page'
                    line.update({'publish': False, 'report': None, 'say': []})
                    if step == 'start':
                        line['say'] = _start_live_page(workdir, flowdir)
                elif local:
                    line['report'] = None  # a local refresh hands nothing out: report.html has background writers
                elif seeded:
                    skip = 'no_page'  # the flow dir's seed is outside the session's folder: it is never published
                    line.update({'publish': False, 'report': None, 'say': []})
                elif step in ('progress', 'end') and line['page'] is not None and signature == _published_signature(workdir, flowdir):
                    skip = 'unchanged'  # the run's page already shows this: the link stands
                    line.update({'publish': False, 'report': None, 'say': [f'**[{LABEL}]({line["page"]})**'] if step == 'end' else []})
                elif publish_copy is None:
                    skip = 'outside_cwd'
                    path = page_file.resolve()
                    line.update({'publish': False, 'report': None, 'local_page': str(path),
                                 'say': [*([line['integrity_line']] if line['integrity_line'] else []), LOCAL_LINE.format(path=path), OUTSIDE_LINE]})
                elif line['report'] is None:  # `_fill_page` moved the page in but could not write the copy: only the publish is lost
                    skip = 'no_copy'
                    line.update({'publish': False, 'say': [*([line['integrity_line']] if line['integrity_line'] else []),
                                                           LOCAL_LINE.format(path=page_file.resolve())]})
                else:
                    # The real path, absolute: a spelling through a symlinked folder is one Claude Code cannot match to the file.
                    line['report'] = str(publish_copy.resolve())
    line['skip'] = skip
    # The run's one publish refused: one rewrite and refill, counted here so a second refusal of the same end says so.
    line['retry'] = bool(run_end and skip in report_link.REFUSED and not local and not _end_refused_before(workdir, flowdir, step))
    if note_dropped:
        stderr = f'{stderr}\n{note_dropped}'.lstrip('\n')
    record = {'step': step, 'skip': skip, 'line': line, 'stderr': stderr, 'at': now, 'live': live[0], 'signature': signature}
    if skip in ('disabled', 'not_vendored', 'no_plan', 'driven', 'not_final') and report_link.fill_refused(_record_dir(workdir, flowdir)):
        record['refused'] = True  # no fill ran: the page on disk is still the one a refusal kept
    if pieces is not None:
        record['pieces'] = pieces
    if not local:  # like `refill`: a local refresh never stands in for the hand-off's fill, its refusal or its signature
        _write_fill_record(workdir, flowdir, record)
    if step == 'start':
        spawn_page_watch(workdir, flowdir)
    return line


def _end_refused_before(workdir: Path, flowdir: Path | None, step: str) -> bool:
    """Whether the last recorded fill was this same ``step``, the run's end (``run_end``), refused: its one retry is spent.
    With no record dir a retry could never be counted, so it reads as spent."""
    home = _record_dir(workdir, flowdir)
    if home is None:
        return True
    record = _json(home / FILL_RECORD)
    if not isinstance(record, dict) or record.get('step') != step or record.get('skip') not in report_link.REFUSED:
        return False
    return isinstance(record.get('line'), dict) and record['line'].get('run_end') is True


def _plan_of(workdir: Path, plan: dict | None) -> dict | None:
    """``plan`` when it runs THIS workdir: its ``workdir`` unset, or the same real path."""
    if plan is None:
        return None
    named = plan.get('workdir')
    if isinstance(named, str) and named:
        try:
            if Path(named).resolve() != workdir.resolve():
                return None
        except OSError:
            return None
    return plan


def _plan_runs_here(workdir: Path, flowdir: Path | None) -> dict | None:
    """The live, unhalted /simulate plan when it runs THIS workdir."""
    plan = live_plan(flowdir)
    return None if plan is None or plan.get('halted') else _plan_of(workdir, plan)


def _run_of_here(workdir: Path, flowdir: Path | None) -> bool:
    """Whether this session's /simulate run, in any state (halted or ended too), written in the last ``PLAN_MAX_AGE_S``,
    is THIS workdir's: the live local page mirrors its fills (:func:`_mirror_live_page`)."""
    if flowdir is None:
        return False
    path = flowdir / PLAN_FILE
    plan = _json(path)
    if not isinstance(plan, dict) or not isinstance(plan.get('stages'), list):
        return False
    try:
        if time.time() - path.stat().st_mtime > PLAN_MAX_AGE_S:
            return False
    except OSError:
        return False
    return _plan_of(workdir, plan) is not None


def _mirror_live_page(workdir: Path, flowdir: Path | None) -> None:
    """Copy the workdir's page, then its sidecar, into ``<flowdir>/`` — the one local page a /simulate run keeps open
    (THE LIVE LOCAL PAGE). The source is the workdir's CURRENT files, never a fill's private copy: with several writers
    (a fill, the run's ``--local`` loop, a detached ``refill``, the hooks) the last mirror carries the last state. Each
    file lands whole (``os.replace``); the flow dir's sidecar is never removed, so a polling tab never sees it missing.
    Best-effort: a failure (a browser holding the file on Windows) never fails the fill."""
    try:
        if not _run_of_here(workdir, flowdir) or flowdir is None or not flowdir.is_dir():
            return
        source = workdir / 'artifacts'
        for name in (FLOW_PAGE, SIDECAR):  # the page first: never a stamp ahead of its page
            if not (source / name).is_file():
                continue
            fd, tmp = tempfile.mkstemp(prefix=f'.{name}.', dir=str(flowdir))
            os.close(fd)
            try:
                shutil.copyfile(source / name, tmp)
                os.replace(tmp, flowdir / name)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
    except Exception as error:  # noqa: BLE001 - the workdir page is filled; the live copy catches up on the next fill
        print(f'publish_report: live page not mirrored: {type(error).__name__}', file=sys.stderr)


def _start_live_page(workdir: Path, flowdir: Path | None) -> list[str]:
    """The start of a /simulate run: open ``<flowdir>/report.html`` in the browser once (the same file on every run), and
    name it. Nobody at a browser here (:func:`_nobody_at_the_browser`): only named. No page there: nothing said."""
    if flowdir is None or not (flowdir / FLOW_PAGE).is_file() or CONTROL_CHARS.search(str(flowdir)):
        return []
    path = (flowdir / FLOW_PAGE).resolve()
    opened = False
    try:
        if not _nobody_at_the_browser(workdir):
            _write_atomic(flowdir / OPENED_RECORD, json.dumps([str(path)], ensure_ascii=False) + '\n')
            _open_in_browser(path.as_uri())
            opened = True
    except Exception:  # noqa: BLE001, S110 - a failed open only names the page
        opened = False
    return [(LIVE_OPENED_LINE if opened else LIVE_NAMED_LINE).format(path=path)]


def publish_moment(workdir: Path, flowdir: Path | None, step: str, point: dict, unlanded: bool, continues: bool) -> tuple[bool, bool]:
    """``(run_end, live_page)`` for this fill (see ONE PUBLISH PER RUN).

    ``run_end``: this fill is the run's one publish — the ``end`` step, or a hand-off of a stage the live plan for this
    workdir holds when that stage did not land (``unlanded``) or no stage after it is left (``continues`` false).
    ``live_page``: the page stays local — every milestone (``progress`` included), and every other point while that plan
    runs and this is not its end. Neither holds for a command run on its own: its hand-off publishes."""
    if step == 'end':
        return True, False
    if point['milestone']:
        return False, True
    plan = _plan_runs_here(workdir, flowdir)
    if plan is None:
        return False, False
    run_end = point['stage'] is not None and point['stage'] in (plan.get('stages') or []) and (unlanded or not continues)
    return run_end, not run_end


def session_cwd() -> Path | None:
    """The real path of the folder Claude Code was started in: the session pin's ``project_dir`` (written at SessionStart),
    else ``CLAUDE_PROJECT_DIR``; None when neither is known."""
    try:
        import session_pin  # noqa: PLC0415 - hooks/ is on sys.path above

        pin = session_pin.read()
        if pin is not None and pin.project_dir:
            return Path(pin.project_dir).resolve()
    except Exception:  # noqa: BLE001, S110 - no pin is "not known"; the variable is next
        pass
    named = os.environ.get('CLAUDE_PROJECT_DIR', '').strip()
    try:
        return Path(named).resolve() if named else None
    except OSError:
        return None


def inside_session_cwd(path: Path) -> bool:
    """Whether ``path`` (by its real path) lies inside the session's folder. Unknown folder: True (fail open, publish)."""
    cwd = session_cwd()
    if cwd is None:
        return True
    try:
        return path.resolve().is_relative_to(cwd)
    except OSError:
        return False


def _publish_copy(source: Path, target: Path) -> None:
    """The file a publishable fill hands out as ``report``: ``artifacts/.publish/report.html``, a copy of that fill's own
    render, taken before the render replaces the page. The local page has background writers (the run's ``--local`` loop,
    a detached ``refill``), and the Artifact tool refuses a file that changed between its approval and its publish. One
    stable path, so a publish with no ``url`` still redeploys the same page. Written whole, then moved in place."""
    target.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{target.name}.', dir=str(target.parent))
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        try:
            shutil.copymode(source, tmp)
        except OSError:
            pass  # the bytes are what is published
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _trial_count(workdir: Path, step: str, seeded: bool) -> int | None:
    """How many trials the scored run played when more than one, for the steps whose page shows only the first."""
    run = _scored_run(workdir) if PUBLISH_POINTS[step]['trial_caveat'] and not seeded else None
    trials = run.get('trials') if isinstance(run, dict) else None
    n = trials.get('n') if isinstance(trials, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 1 else None


def _tell_local_page(line: dict[str, Any], workdir: Path, page_file: Path, seeded: bool, step: str) -> None:
    """Turn a filled page into a local one: nothing to publish, and ``say`` holds the lines the hand-off relays.
    The file is named only when it sits inside the work folder (the flow dir's seed page does not) and its path
    reads as one line; a milestone is no hand-off and says nothing."""
    line.update({'publish': False, 'report': None, 'page': None, 'local_page': None, 'say': []})
    path = page_file.resolve()
    if seeded or not path.is_relative_to(workdir.resolve()) or CONTROL_CHARS.search(str(path)):
        return
    line['local_page'] = str(path)
    if PUBLISH_POINTS[step]['milestone']:
        return
    n = _trial_count(workdir, step, seeded)
    sentence = LOCAL_LINE.format(path=path) + (LOCAL_TRIALS.format(n=n) if n is not None else '')
    line['say'] = [line['integrity_line'], sentence] if line['integrity_line'] else [sentence]


def closing_link(workdir: Path, since: float | None) -> list[str]:
    """The overview page's line in a /simulate run's closing message, or [] when there is none to name.

    The page this run last published: the recorded URL, when ``.report_published.json`` says a publish landed at or
    after ``since`` (a page from an earlier run — another account's, perhaps — is not named). No sha check: a refill
    after that publish rewrites the local file, and the published page is still this run's. No publish landed this run:
    the workdir's ``artifacts/report.html`` by its path (``LOCAL_LINE``) when this run filled it. Under a harness with no
    Artifact tool, the local file written at or after ``since``, said the way its hand-off says it
    (:func:`_tell_local_page`). With no ``since``, nothing proves a page this run's, and none is named."""
    page_file = workdir / 'artifacts' / FLOW_PAGE
    if since is None:
        return []  # no start to prove the page this run's: a page is never named on trust
    if _workdir.harness() in LOCAL_ONLY_HARNESSES:
        if not page_file.is_file() or page_file.stat().st_mtime < since:
            return []  # an earlier run's file: this run filled no page
        line: dict[str, Any] = {'integrity_line': None}
        _tell_local_page(line, workdir, page_file, False, 'fixes')
        return list(line['say'])
    published = _json(workdir / PUBLISHED_RECORD)
    published = published if isinstance(published, dict) else {}
    at = _epoch(published.get('at'))
    page = report_link.recorded(page_file)
    if page is None or at is None or at < since:
        # No link landed this run (the publish failed, was declined, or the agent's folder is outside the session's):
        # the workdir's own page, filled this run, by its path. Never the flow dir's live copy, which goes with the plan.
        if not page_file.is_file() or page_file.stat().st_mtime < since or CONTROL_CHARS.search(str(page_file.resolve())):
            return []
        return [LOCAL_LINE.format(path=page_file.resolve())]
    step = published.get('step')
    n = _trial_count(workdir, step if isinstance(step, str) and step in PUBLISH_POINTS else 'fixes', False)
    return [f'**[{LABEL}]({page[0]})**' + (SUFFIXES['trials'].format(n=n) if n is not None else '')]


def _renders_in_progress(vendored: Path) -> bool:
    """Whether the vendored page declares ``preliminary_findings.PAGE_CAPABILITY`` (never, when that sibling is unreadable)."""
    try:
        import preliminary_findings  # noqa: PLC0415 - optional sibling, read by path

        return preliminary_findings.supported(vendored)
    except Exception as error:  # noqa: BLE001 - an unreadable mark is "not declared", never a failed fill
        print(f'publish_report: page capability unread: {type(error).__name__}', file=sys.stderr)
        return False


def page_signature(state: dict | None, marks: dict, kept: list, workdir: Path | None, replaced: dict[str, str] | None = None) -> str:
    """What the filled page shows, as one hash: the step, the stages' states, the draft and stale
    marks, the files the page read and the newest of their mtimes, and the findings copies the page got
    in place of the disk files (``replaced``: derived rows move no mtime). A piece landing always moves it."""
    newest = 0
    for rel in kept if workdir is not None else []:
        try:
            newest = max(newest, (workdir / str(rel)).stat().st_mtime_ns)
        except OSError:
            continue
    step = (state or {}).get('step')
    stages = [[s.get('id'), s.get('state')] for s in (state or {}).get('stages') or [] if isinstance(s, dict)]
    label = step.get('label') if isinstance(step, dict) else None
    copies = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in sorted((replaced or {}).items())}
    shown = [label, stages, marks.get('sections') or {}, marks.get('stale') or [], sorted(map(str, kept)), newest, copies]
    return hashlib.sha256(json.dumps(shown, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def _published_signature(workdir: Path, flowdir: Path | None) -> str | None:
    home = _record_dir(workdir, flowdir)
    published = _json(home / PUBLISHED_RECORD) if home is not None else None
    signature = published.get('signature') if isinstance(published, dict) else None
    return signature if isinstance(signature, str) else None


def _fill_page(workdir: Path, seeded: bool, page_file: Path, vendored: Path, line: dict[str, Any], progress: dict | None,
               hidden: set[str] | frozenset = frozenset(), replaced: dict[str, str] | None = None, *,
               refresh: bool = False, flowdir: Path | None = None, publish_copy: Path | None = None) -> tuple[str | None, str]:
    """Run the vendored fill into ``page_file``; on success fold its summary into ``line``. Returns (skip, stderr).
    The empty seed carries the running state too, so the page renders it. The fill writes into a private sibling
    dir (``.report.fill.<pid>/``); its page then replaces ``page_file`` in one step, and only after it the update
    sidecar the fill writes beside its output (``report.data.js``): a page opened mid-fill still finds the previous
    one, and a reader polling the sidecar never sees a stamp before the page it describes. A failed or refused
    fill (internal text, or, for a fill but not a ``refresh``, a number that is not C7's) keeps the previous page
    and sidecar as they are: the fill record's refusal keeps ``done`` and ``report_link link`` from handing it out
    as this step's, and it makes no ``publish_copy``, which a fill that passed the checks writes from its own render. A copy
    that cannot be written costs only the publish: the page, its sidecar and the mirror still move, and ``report`` is None
    (``no_copy``). A ``refresh`` gives up after ``REFRESH_TIMEOUT_S``: it holds up nothing, and its caller carries on."""
    fill_dir = page_file.with_name(f'.{page_file.stem}.fill.{os.getpid()}')
    filled = fill_dir / page_file.name  # created by the fill, so it keeps the usual mode
    try:
        with tempfile.TemporaryDirectory() as seed:
            source = Path(seed) if seeded else page_view(workdir, Path(seed), hidden, replaced, page_runs(workdir, hidden))
            if seeded:
                (source / 'artifacts').mkdir()
                if progress is not None:
                    (source / 'artifacts' / PROGRESS_FILE).write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n', 'utf-8')
            shutil.rmtree(fill_dir, ignore_errors=True)  # a dead run's leftover under a reused pid
            fill_dir.mkdir()
            done = subprocess.run(
                [sys.executable, str(vendored / 'fill_report.py'), str(vendored / 'index.html'), str(source), str(filled)],
                capture_output=True,
                text=True,
                check=False,
                timeout=REFRESH_TIMEOUT_S if refresh else None,
            )
    except BaseException:
        shutil.rmtree(fill_dir, ignore_errors=True)
        raise
    summary = _last_json_object(done.stdout) if done.returncode == 0 else None
    skip = 'fill_refused' if done.returncode != 0 else 'garbled' if summary is None else None
    if skip is None and not seeded:
        try:
            leaks = customer_text_guard.text_violations(PAGE_KIND, filled.read_text('utf-8'))
        except (OSError, UnicodeDecodeError):
            leaks = []  # the guard never wedges a page it cannot read
        if leaks:  # like the sources' check: nothing is published, so the flow rewrites and fills again
            line['internal_text'] = [str(leak) for leak in leaks]
            skip = 'internal_text'
        elif not refresh and (mismatches := numbers_mismatches(workdir, filled)):
            skip, line['numbers'] = 'numbers', mismatches  # a number that is not C7's never reaches a page
    if skip is not None or summary is None:
        shutil.rmtree(fill_dir, ignore_errors=True)  # the last good page and its sidecar stay
        return skip, done.stderr.rstrip()
    stderr, copied = done.stderr.rstrip(), True
    try:
        if publish_copy is not None:
            try:
                _publish_copy(filled, publish_copy)
            except OSError as error:
                copied = False
                stderr = f'{stderr}\npublish_report: publish copy not written: {type(error).__name__}'.lstrip('\n')
        os.replace(filled, page_file)
        if (fill_dir / SIDECAR).is_file():
            os.replace(fill_dir / SIDECAR, page_file.with_name(SIDECAR))
    finally:
        shutil.rmtree(fill_dir, ignore_errors=True)
    if not seeded:
        _mirror_live_page(workdir, flowdir)
    line.update({key: summary.get(key, line[key]) for key in ('version', 'kept', 'cut', 'dropped', 'misread', 'misread_count', 'bytes', 'filled_at')})
    line.update({
        'publish': True,
        'report': str(page_file) if copied else None,
        'integrity_line': integrity_line(line['dropped'] or [], line['misread'] or [], int(line['misread_count'] or 0)),
    })
    return None, stderr


def _last_json_object(stdout: str) -> dict | None:
    lines = stdout.strip().splitlines()
    try:
        value = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _record_dir(workdir: Path, flowdir: Path | None) -> Path | None:
    """Where ``.report_fill.json`` lives: the workdir once it has artifacts/, else the flow dir."""
    home = workdir if (workdir / 'artifacts').is_dir() else flowdir
    return home if home is not None and home.is_dir() else None


def _write_fill_record(workdir: Path, flowdir: Path | None, record: dict) -> None:
    home = _record_dir(workdir, flowdir)
    if home is not None:
        _write_atomic(home / FILL_RECORD, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def _open_in_browser(uri: str) -> None:
    """The default browser on ``uri``, from a detached process: ``webbrowser`` can pick a console browser that inherits
    stdout and blocks. ``-I`` keeps a ``webbrowser.py`` in the customer's folder from being imported."""
    subprocess.Popen(  # noqa: S603 - the running interpreter, a fixed program, one file URI as its argument
        [sys.executable, '-I', '-c', 'import sys, webbrowser; webbrowser.open(sys.argv[1])', uri],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )


def _nobody_at_the_browser(workdir: Path) -> bool:
    """Whether no browser should open here: a harness with no Artifact tool, or nobody watching."""
    return _workdir.harness() != 'claude' or _nobody_watching(workdir)


def _nobody_watching(workdir: Path) -> bool:
    """Whether no person can be looking at the local page here, whichever harness runs: the suites' switch, a declared
    operator, an SDK session, no display. Codex and Copilot do not count: they open no browser, and a person reads the page."""
    import _run_driver  # noqa: PLC0415 - inside the caller's fail-quiet guard

    if os.environ.get(NO_OPEN_SWITCH, '').strip() or os.environ.get(_run_driver.ENV_ALIAS, '').strip():
        return True
    if os.environ.get('CLAUDE_CODE_ENTRYPOINT', '').startswith('sdk'):
        return True
    if no_display():
        return True
    return _run_driver.resolve(workdir=workdir).headless


#: Set by an SSH login: the browser would open on the far machine, or nowhere.
SSH_VARS = ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY')
#: Set in a Claude Code session that runs on a remote host (cloud sessions). Unverified name, read conservatively: any
#: non-empty value means no local browser; absent changes nothing.
REMOTE_VAR = 'CLAUDE_CODE_REMOTE'


def no_display() -> bool:
    """Whether no browser can be shown here: an SSH login, a remote Claude Code session, or a Linux (or other Unix but
    macOS) host with no ``DISPLAY`` and no ``WAYLAND_DISPLAY``. The page's path is still said."""
    if any(os.environ.get(name, '').strip() for name in SSH_VARS):
        return True
    if os.environ.get(REMOTE_VAR, '').strip().lower() not in ('', '0', 'false'):
        return True
    if sys.platform not in ('darwin', 'win32', 'cygwin'):
        return not (os.environ.get('DISPLAY', '').strip() or os.environ.get('WAYLAND_DISPLAY', '').strip())
    return False


def _open_local_page(workdir: Path, step: str, page_file: Path, seeded: bool, flowdir: Path | None) -> None:
    """Open an unpublished local page once per path (see THE LOCAL PAGE OPENS ITSELF). The path is recorded first, so
    a record that cannot be written opens nothing rather than opening on every call. Never raises."""
    try:
        if flowdir is None or not flowdir.is_dir() or _nobody_at_the_browser(workdir):
            return
        recorded = _recorded(page_file, seeded, flowdir)
        if recorded is not None and recorded[1] == _sha(page_file):
            return
        path = page_file.resolve()
        opened = _json(flowdir / OPENED_RECORD)
        opened = opened if isinstance(opened, list) and step != 'start' else []  # a new flow in the same session opens again
        if str(path) in opened:
            return
        _write_atomic(flowdir / OPENED_RECORD, json.dumps([*opened, str(path)], ensure_ascii=False) + '\n')
        _open_in_browser(path.as_uri())
    except Exception:  # noqa: BLE001, S110 - the link line still leads to the page; a failed open is nobody's step failure
        pass


def _live_page_open(flowdir: Path | None) -> bool:
    """Whether this run's start opened the live local page (``<flowdir>/report.html``): a second tab is never opened."""
    if flowdir is None:
        return False
    opened = _json(flowdir / OPENED_RECORD)
    try:
        return isinstance(opened, list) and str((flowdir / FLOW_PAGE).resolve()) in opened
    except OSError:
        return False


def done(workdir: Path, step: str, url: str, flowdir: Path | None) -> tuple[int, list[str]]:
    if url != '-' and not report_link.PAGE.fullmatch(url):
        raise CallerBug(f'refusing --url {url!r} — pass the https URL the Artifact tool returned, or - when the publish failed')
    seeded = _seeded(workdir, step)
    page_file = _page_file(workdir, seeded, flowdir)
    if not page_file.is_file() or report_link.fill_refused(_record_dir(workdir, flowdir)):
        return 1, []  # no page, or the last good page kept by a refused fill: never this step's, so nothing is opened or recorded
    if url == '-' and not _live_page_open(flowdir):
        _open_local_page(workdir, step, page_file, seeded, flowdir)  # before the milestone return: a milestone can be a path's first
    home = _record_dir(workdir, flowdir)
    record = _json(home / FILL_RECORD) if home is not None else None
    filled = record['line'] if isinstance(record, dict) and record.get('step') == step and isinstance(record.get('line'), dict) else None
    anchor = filled.get('anchor') if filled is not None else landing_anchor(PUBLISH_POINTS[step]['anchor'], set())
    if url != '-':
        if seeded:
            _write_atomic(page_file.parent / PENDING, f'{url}\nsha256={_sha(page_file)}\n')
        else:
            report_link.record(page_file, url)
            _record_published(workdir, step)
            if flowdir is not None:
                (flowdir / PENDING).unlink(missing_ok=True)
    if PUBLISH_POINTS[step]['milestone']:
        return 0, []  # mid-stage: the URL is recorded, and nothing is said
    recorded = _recorded(page_file, seeded, flowdir)
    # A URL just given is this page's, whatever a refill landing since `record` did to the file.
    current = recorded is not None and (url != '-' or recorded[1] == _sha(page_file))
    target = (recorded[0] + ('' if anchor is None else f'#{anchor}')) if recorded is not None and current else report_link.local(page_file)
    suffixes = []
    n = _trial_count(workdir, step, seeded)
    if n is not None:
        suffixes.append(SUFFIXES['trials'].format(n=n))
    if url == '-' and recorded is not None and not current:
        suffixes.append(SUFFIXES['stale'])
    lines = []
    if filled is not None and filled.get('integrity_line'):
        lines.append(str(filled['integrity_line']))
    lines.append(f'**[{LABEL}]({target})**' + ''.join(suffixes))
    return 0, lines


def _record_published(workdir: Path, step: str) -> None:
    """Copy this step's fill signature to ``.report_published.json`` — what the published page shows.
    A fill record of another step (or none) leaves nothing to trust, so the old record goes."""
    record = _json(workdir / FILL_RECORD)
    target = workdir / PUBLISHED_RECORD
    signature = record.get('signature') if isinstance(record, dict) and record.get('step') == step and record.get('skip') is None else None
    if not isinstance(signature, str):
        target.unlink(missing_ok=True)
        return
    _write_atomic(target, json.dumps({'signature': signature, 'step': step, 'at': _now()}, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def forget(workdir: Path, flowdir: Path | None) -> None:
    report_link.forget(workdir / 'artifacts' / 'report.html')
    (workdir / PUBLISHED_RECORD).unlink(missing_ok=True)
    if flowdir is not None:
        (flowdir / PENDING).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p_fill = sub.add_parser('fill', help='fill the page and print the JSON line')
    p_done = sub.add_parser('done', help='record the published URL and print the link line')
    p_forget = sub.add_parser('forget', help='drop the recorded page (it is gone or not ours)')
    p_refill = sub.add_parser('refill', help='refill the local page in place (no publish, no record); exit 0 always')
    p_live = sub.add_parser('live', help='exit 0 when a live /simulate plan has a stage running (the wake-up card applies), else 1')
    sub.add_parser('local', help='exit 0 when this harness has no Artifact tool (the page stays a local file), else 1')
    for p in (p_fill, p_done, p_forget, p_refill):
        p.add_argument('workdir', type=Path)
    for p in (p_fill, p_done, p_forget, p_live, p_refill):
        p.add_argument('--flowdir', type=Path)
    for p in (p_fill, p_done):
        p.add_argument('--step', required=True)
    p_fill.add_argument('--as', dest='as_command', help='the flow command this publish runs under (default: the step\'s own)')
    p_fill.add_argument('--note', help=f'the running step in plain words (at most {NOTE_MAX} characters; default: the step\'s own label)')
    source = p_fill.add_mutually_exclusive_group()
    source.add_argument('--job', help='--step progress: the backend job whose phase names the step')
    source.add_argument('--run-dir', type=Path, help='--step progress: the running run\'s output dir (finished transcripts are counted)')
    p_fill.add_argument('--run-total', type=int, help='with --run-dir: how many scenarios the run plays')
    p_fill.add_argument('--trials', type=int, help='with --run-dir: how many times each scenario plays')
    p_fill.add_argument('--vendored', type=Path, default=VENDORED, help=argparse.SUPPRESS)
    p_fill.add_argument('--local', action='store_true', help=argparse.SUPPRESS)  # run_tick.sh --loop: refresh the local page only
    p_done.add_argument('--url', required=True)
    args = ap.parse_args(argv)

    if args.cmd == 'local':
        return 0 if _workdir.harness() in LOCAL_ONLY_HARNESSES else 1
    flowdir = args.flowdir.resolve() if args.flowdir else None
    if args.cmd == 'live':
        return 0 if progress_stage(live_plan(flowdir)) is not None else 1
    workdir = args.workdir.resolve()
    if args.cmd == 'refill':
        try:
            refill(workdir, flowdir)
        except Exception as error:  # noqa: BLE001 - a refill is best-effort; the next fill catches the page up
            print(f'publish_report: refill skipped: {type(error).__name__}', file=sys.stderr)
        return 0
    step = getattr(args, 'step', None)
    if step is not None and step not in PUBLISH_POINTS:
        print(f'publish_report: unknown step {step!r} — one of {", ".join(PUBLISH_POINTS)}', file=sys.stderr)
        return 64
    try:
        if args.cmd == 'fill':
            run_dir = args.run_dir.resolve() if args.run_dir else None
            line = fill(workdir, step, flowdir, args.as_command, args.vendored, args.note,
                        job=args.job, run_dir=run_dir, run_total=args.run_total, trials=args.trials, local=args.local)
            print(json.dumps(line, ensure_ascii=False))
            return 0
        if args.cmd == 'done':
            code, lines = done(workdir, step, args.url, flowdir)
            for text in lines:
                print(text)
            return code
        forget(workdir, flowdir)
        return 0
    except CallerBug as bug:
        print(f'publish_report: {bug}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
