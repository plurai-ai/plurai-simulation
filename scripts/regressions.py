#!/usr/bin/env python3
"""The regressions directory: choose what to record, write the folder, list what is on disk.

A **regression** is a folder under ``plurai_sim/regressions/`` that replays one confirmed agent
issue. It carries agent evidence only — the resume pin cut before the issue's turn, the issue
paragraph in ``ROLES.md``, and the transcript rendering; or, for an issue on the opening turn of a
scenario that was not itself resumed, the recorded opener in ``first_talk.txt`` and the paragraph,
with no pin. On a harness whose manifest declares it cannot resume a recorded conversation, an issue
after the opening turn takes a third shape, opener-rooted: the recorded opener and the paragraph,
whose ``**Trigger.**`` line states the user move that exposed the issue, replayed as a whole
conversation. Every shape also keeps ``issue_turn.json``: the issue's turn as the run recorded it, the
user's message and the agent's reply. The eval set names a check that must fail that reply, and the
backend replays it against that check. Its name is the issue slug, its presence is its status, and it
reaches a scenario set only through the final batch, which stages this directory with
``trace_scenarios/`` and uploads them as one bundle (``final_bundle.py``).

The verbs, because different callers need one of them and all of them need the same rules about
what a folder is:

``pick``               choose the scenario and the cut for one issue, out of a finished run.
``write``              create the folder, its ``ROLES.md`` and ``issue_turn.json``, plus ``first_talk.txt``
                       on the opener shapes.
``carry-world-facts``  append a trace scenario's world facts to the ``ROLES.md`` of the regression
                       recorded from its run.
``overview``           rewrite the directory's ``BUNDLE.md`` so it describes what is actually on disk.
``list``               enumerate the folders, optionally against the scenario names a set shipped.
``finding``            read one confirmed finding out of the suite build's ``agent_findings.md``.
``finding-slug``       derive the issue slug from a finding's stable id.
``auto-plan``          choose which confirmed findings the suite build records at gate exit.
``auto-report``        render the suite report's one line about what those findings became (it keeps its
                       ``--refused`` / ``--rare`` in the plan, so a re-render needs only ``--findings``), and add the
                       folders written to the build's ledger.
``fixes-lines``        print the fix plan's ``Regression:`` line of each finding, from an ``auto-plan --acceptance``.
``sample-payload``     print the backend scoring call for replies re-sampled at a folder's cut.
``confirm``            classify the folder on how often its issue recurred there, and keep the result.
``replay-trials``      print how many replays the regression-only step of a run takes.
``repin``              pin a regression on ``anywhere`` when its owner cannot decide at the cut and can across
                       the conversation (``replays.json`` ``clear_axis``).
``replay-plan``        write the runner's ``--replay-plan``: which folders replay only their issue turn, which whole.
``retire``             move out the regressions a run cleared on the SDK's own clear verdict, fix or no fix.
``runnable``           drop the retired regressions from a list of scenario ids.

An issue comes from one of two sources. ``/simulation-fixes`` records an accepted agent issue out
of ``fixes.md``; the suite build records a confirmed finding out of ``agent_findings.md`` at gate
exit. Both name a finding's folder by the slug its stable id derives, so the second source's folder
is the one the first finds when the same finding is accepted later.

The folder is the whole record. There is no provenance file and no ledger: the source run, the
scenario and the cut live in the pin's provenance block and in the paragraph, and nothing
machine-reads them. ``replays.json`` (``confirm``) is a local measurement beside them, never uploaded. Deleting a folder retires its regression, and git history keeps it — which is
why the directory sits outside the managed ignore block while every other staging directory is
inside it.

Naming mirrors what the backend does with an additional bundle: each folder's scenario is named by
the folder's bare slug, so it means the same scenario in the next build and ``list --shipped-from``
finds it by name. The backend refuses an empty slug, two folders that slugify alike, and a slug
shaped like the walk's own ``scenario_NN_`` names. The slug rule here is narrower than all three —
lowercase ascii words joined by single underscores, under a ``regression_`` prefix no walk name
can wear — so a folder this script writes is one whose name survives slugification unchanged, and
the refusal lands here rather than after an upload.

Exit codes: 0 success, 1 the directory and the set disagree — ``list`` found a folder no set
carries — 2 a refusal, including, from ``list`` and ``overview``, a folder that carries neither
its pin nor its opener.
The backend does not refuse such a folder: it authors a scenario for it
cold from ``ROLES.md``, under the folder's name, so the set carries a scenario by the regression's
name that reproduces nothing. The two verbs that read the directory are the only place that folder
can stop a build, so both stop on it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:  # 3.10+; the annotations are strings (`from __future__`), so it is never needed at runtime
    from typing import TypeGuard

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prune_agent_findings as findings_file  # noqa: E402
from host_effects import FILE_WRITE_TOOLS  # noqa: E402 - the plugin's one host-effects table
import verdicts_file  # noqa: E402 - the one reader of a conversation's verdicts
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

SUMMARY = 'The regressions directory: choose what to record, write the folder, list what is on disk.'

#: The directory, relative to the workdir. Named here so the skills that read it, the skill that
#: writes it and the tests all take it from one place.
REGRESSIONS_DIRNAME = 'regressions'

#: Every folder carries it, so a directory listing separates regressions from whatever else an
#: operator has left beside them without reading any file.
FOLDER_PREFIX = 'regression_'

ROLES_FILENAME = 'ROLES.md'
PIN_FILENAME = 'resume_pin.json'
FIRST_TALK_FILENAME = 'first_talk.txt'
ISSUE_TURN_FILENAME = 'issue_turn.json'
OVERVIEW_FILENAME = 'BUNDLE.md'
TRANSCRIPT_FILENAME = 'transcript.json'
#: Where a resumed regression recorded from a Claude Code CLI harness keeps that CLI's own session
#: file (``<session id>.jsonl``): the harness resumes it at the cut. Local only — no upload carries it.
#: SDK-owned (``plurai.run_artifacts.CLI_SESSION_DIRNAME``, which ``bundle_units`` skips); mirrored by value.
CLI_SESSION_DIRNAME = 'cli_session'
#: The typed reasons a resumed CLI regression is refused at record time.
REFUSED_WORKSPACE_WRITTEN = 'workspace_written_before_cut'
REFUSED_NO_CLI_SESSION = 'no_cli_session'
#: Where :func:`read_transcript` puts the conversation's current cells (its ``verdicts.json``): an in-memory key, never on disk.
VERDICT_CELLS = '_verdict_cells'
#: How often the folder's issue recurred at its cut when it was recorded (:func:`confirm`). Local only:
#: SDK-owned (``plurai.run_artifacts.REGRESSION_REPLAYS_FILENAME``, which ``bundle_units`` skips); mirrored by value.
REPLAYS_FILENAME = 'replays.json'

#: The slug an issue gets. Lowercase ascii words joined by single underscores, which is exactly the
#: set of names ``slugify_scenario_name`` returns unchanged — so the name the operator reads in the
#: repo, the folder on disk and the scenario in the delivered set are one string, not three that
#: happen to correspond. Anything else is refused at record time, where a rename is free.
SLUG_RE = re.compile(r'^[a-z0-9]+(?:_[a-z0-9]+)*$')

#: The heading the issue paragraph opens with. The converter appends its resume section BELOW the
#: paragraph, so this heading is where every later reader — the fix loop, the suite report, the
#: operator opening the folder a year on — finds what the regression reproduces.
ISSUE_HEADING = '## Issue:'

#: The heading of the world-facts section: the tool calls and results a production trace recorded
#: after the cut. The SDK's converter writes it as the last section of a trace scenario's
#: ``ROLES.md``; a regression recorded from that scenario's run carries it on
#: (:func:`carry_world_facts`); ``trace_scenarios.py`` names it to the world author. One string
#: for all three, so a reader and the writer never disagree on where the section starts.
WORLD_FACTS_HEADING = 'World the live leg needs'

#: The last heading the converter writes before the world facts in a resumed folder's
#: ``ROLES.md``. The converter roots the world facts at this heading's depth — ``##`` in a
#: standalone folder, ``###`` inside an ``--into`` merge — so its depth is the one a carried
#: section takes.
PROVENANCE_HEADING = 'Provenance'

#: What the backend publishes about an additional bundle, on the finished set's metadata. The
#: shipped list is written whenever a bundle rode the job; the attempted list only when a folder
#: asked for did not ship, which is why its ABSENCE cannot be read as "nothing is missing" without
#: the shipped list beside it.
SHIPPED_KEY = 'bundle_folders'
ATTEMPTED_KEY = 'bundle_folders_attempted'
METADATA_KEY = 'dataset_metadata'

#: Why each attempted folder did not ship, keyed by folder: ``infra_fault`` (not judged — the
#: cause is outside the folder), ``failed_validation``, ``session_fault`` or ``unknown``. Written
#: with the attempted list; a folder it does not name reads as :data:`REASON_UNKNOWN`.
ATTEMPTED_REASONS_KEY = 'bundle_folders_attempted_reasons'
REASON_UNKNOWN = 'unknown'
#: The reason for a folder on disk that the backend names in neither list: it was never handed over.
REASON_NOT_HANDED_OVER = 'not_handed_over'

#: How long a scenario name may be before the backend's slugifier truncates it. A longer folder
#: name still uploads and still generates — it simply ships under a PREFIX of itself, so the folder
#: stops naming its own scenario: the set-membership check reads it as absent on every build, and
#: the runs and reports that name it carry a string the next truncation may cut elsewhere.
SCENARIO_NAME_MAX = 50

SHAPE_RESUMED = 'resumed'
SHAPE_OPENER_ONLY = 'opener-only'
#: The shape an issue AFTER the opening turn gets when no pin can replay it faithfully — the harness cannot resume,
#: or the history before the cut wrote a world the replay cannot restore (:func:`world_written_before_cut`): the
#: recorded opener, no pin, and ``issue_turn.json`` of the later turn. Its replay plays the whole conversation live.
SHAPE_OPENER_ROOTED = 'opener-rooted'
#: The typed reason a later cut takes the opener-rooted shape on a harness that CAN resume: a turn before the cut
#: changed the world, and the run's scenario seeded no row of the types it captured, so those rows are the
#: harness's own, rebuilt unchanged for every scenario. A pin restores the words of that history and not what it
#: changed, so the replay's agent would answer against the world as it stood before the conversation.
WORLD_WRITTEN_BEFORE_CUT = 'world_written_before_cut'
#: The paragraph line an opener-rooted folder states its trigger on: the user move that exposed the issue,
#: which the scenario built from the folder has to make for the replay to reach it.
TRIGGER_LINE = '**Trigger.** {trigger}'
_TRIGGER_RE = re.compile(r'^[ \t]*(?:[-*+][ \t]+)?[*_]{0,2}trigger[*_]{0,2}[ \t]*[.:][*_]{0,2}[ \t]*\S', re.IGNORECASE | re.MULTILINE)

#: The scan contract's typed document inputs, in the workdir: each entry's ``field`` is an
#: ``initial_agent_context`` key the run hands the agent a document under.
DOCUMENT_FIELDS_FILENAME = '.document_fields'

#: The key the SDK's runner writes its own reading of a seed restore under, inside a resumed
#: run's ``resume_fidelity``; every other key there is the harness's report from ``seed_state``.
#: The SDK owns the name. ``test_regressions_against_converter.py`` runs the converter over the
#: same reports, so a rename on that side fails there rather than passing here.
SEED_RESTORE_KEY = 'sdk_seed_restore'
#: The harness's own key naming the seed's state keys it had no slot for and skipped: a skip, not a
#: reduced restore. SDK-owned (``plurai.run_artifacts.RESUME_STATE_DROPPED_KEY``); mirrored by value.
RESUME_STATE_DROPPED_KEY = 'resume_state_dropped'


class Refused(Exception):
    """A condition the operator has to resolve. The message is the whole report."""


# ── reading a run ─────────────────────────────────────────────────────────────


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise Refused(f'{path} could not be read as JSON ({type(exc).__name__}: {exc}).') from exc


def flatten(content: Any) -> str:
    """The text of a message, mirroring the flattener the converter and the backend use.

    String content as-is, text out of a block list, non-text blocks dropped. The one place it
    matters is the opener lifted onto a turn-0 folder: what this returns is what the scenario
    will say, so a message that flattens to nothing has no opener to record.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block if isinstance(block, str) else block.get('text')
            for block in content
            if isinstance(block, str) or (isinstance(block, dict) and isinstance(block.get('text'), str))
        ]
        return '\n'.join(part for part in parts if part)
    return '' if content is None else str(content)


def user_turn_count(transcript: dict[str, Any]) -> int:
    """How many turns the conversation has, counted over the USER messages.

    The same count the cut is expressed in: turn indices run ``0 .. count - 1``, turns ``0..K-1``
    are seeded with their agent replies, and turn ``K`` becomes the scenario's opening message.
    A resumed scenario's ``user_messages`` hold its live leg only, which is also what its judges
    and the converter's ``--cut-turn`` number, so the count is the live leg's.
    """
    messages = transcript.get('user_messages')
    return len(messages) if isinstance(messages, list) else 0


def _refuse_reduced_restore(transcript: dict[str, Any], *, where: str, remedy: str) -> None:
    """Refuse a RESUMED run whose ``resume_fidelity`` reports a restore short of the whole seed.

    ``resume_fidelity`` carries two kinds of report. Every key but :data:`SEED_RESTORE_KEY` is the
    harness's own account from ``seed_state``, which it returns only for a restore short of the
    whole seed. :data:`SEED_RESTORE_KEY` is the runner's reading of the restore: ``partial`` when
    it measured a loss, ``undetermined`` when it could not tell. ``undetermined`` alone is the
    ordinary reading for an agent that returns only its new messages, so that run records. Every
    other report, a shape this does not know included, means the live turns did not answer the
    seed the transcript records. The converter refuses such a run, so it is refused here, before
    any folder exists. Called for a resumed run only: see :func:`resume_seed`.
    """
    fidelity = transcript.get('resume_fidelity')
    if not fidelity:
        return
    if isinstance(fidelity, dict):
        # State keys with no slot were skipped; the conversation was restored whole.
        fidelity = {key: value for key, value in fidelity.items() if key != RESUME_STATE_DROPPED_KEY}
        if not fidelity:
            return
    if isinstance(fidelity, dict) and set(fidelity) == {SEED_RESTORE_KEY}:
        reading = fidelity[SEED_RESTORE_KEY]
        if isinstance(reading, dict) and reading.get('state') == 'undetermined':
            return
    raise Refused(
        f'{where}: records a reduced restore of its resume seed (resume_fidelity): the harness restored only part of the '
        'seed, or the agent rewrote its seeded history, so the live turns did not answer the conversation the transcript '
        'records, and a regression cut from it anywhere replays a conversation the agent never had. To record it, make '
        f"the harness's seed_state replay the whole seed and the agent keep it, then re-run the scenario. {remedy}"
    )


def resume_seed(transcript: dict[str, Any], *, where: str, remedy: str = 'Name a different scenario with --scenario.') -> dict[str, Any] | None:
    """The resume seed a RESUMED scenario ran from, or ``None`` for a fresh one.

    A resumed scenario's transcript keeps the conversation it resumed apart from its live turns,
    under the top-level ``resume_seed`` key, and the converter puts it back ahead of them in the
    pin. The run writes ``injected_inputs.resume_seed_present`` from that same seed, so a non-empty
    seed is present if and only if the flag is ``True``; an absent flag, or absent
    ``injected_inputs``, reads as ``False``. Either one without the other is refused, because
    nothing in the transcript says which half is true: trusting the flag replays the live leg as
    if nothing preceded it, and trusting the seed replays a history the agent may never have
    answered. A seed whose ``messages`` or ``agent_state`` has the wrong type is refused too, and
    so is a resumed run that recorded a reduced restore of its seed (:func:`_refuse_reduced_restore`).
    A fresh run's ``resume_fidelity`` is not read: only a harness whose ``seed_state`` returned a
    report for ``resume_seed=None`` writes one there, and no seed was restored for it to fall short
    of. The converter refuses every shape refused here, so the choice never names a scenario the
    conversion step then cannot read. ``remedy`` ends each refusal, because the caller decides what
    the operator can do instead.
    """
    seed = transcript.get('resume_seed')
    injected = transcript.get('injected_inputs')
    flagged = isinstance(injected, dict) and injected.get('resume_seed_present') is True
    if not seed:
        if flagged:
            raise Refused(
                f'{where}: records that it was resumed (injected_inputs.resume_seed_present) but carries no resume_seed, so '
                'the conversation it resumed is not in the transcript and a regression recorded from it would replay the live '
                f'leg alone. {remedy}'
            )
        return None
    if not flagged:
        raise Refused(
            f'{where}: carries a resume_seed but does not record that it was resumed (injected_inputs.resume_seed_present '
            'is not true). The run writes both from the one seed, so the transcript contradicts itself, and a regression '
            f'recorded from it would either seed a history the agent may never have answered or drop one it did. {remedy}'
        )
    if not isinstance(seed, dict) or not isinstance(seed.get('messages', []), list) or not isinstance(seed.get('agent_state', {}), dict):
        raise Refused(
            f'{where}: its resume_seed is not an object holding a messages list and an agent_state object, so the '
            f'conversation it resumed cannot be read. {remedy}'
        )
    _refuse_reduced_restore(transcript, where=where, remedy=remedy)
    return seed


def _seed_messages(seed: dict[str, Any] | None) -> list[Any]:
    return seed.get('messages', []) if seed else []


def seed_turns(cut: int, seed: dict[str, Any] | None) -> int:
    """How many user turns the pin cut at ``cut`` seeds: the resume seed's, then the live leg's."""
    return sum(1 for message in _seed_messages(seed) if isinstance(message, dict) and message.get('type') == 'human') + cut


def _carries_history(cut: int, seed: dict[str, Any] | None) -> bool:
    """Whether the pin has a history to seed — the resumed shape — rather than an opener alone."""
    return cut > 0 or bool(_seed_messages(seed))

def _state_only_refusal(where: str, remedy: str) -> Refused:
    """The refusal for a turn-0 issue in a scenario resumed from a seed of agent state alone."""
    return Refused(
        f'{where}: resumed from a seed of agent state alone, and the issue is on its first live turn. A pin needs a '
        'history to seed and the opener-only shape carries no state, so neither shape records the conversation the '
        f'issue is in. {remedy}'
    )


def _refused_lines(refused: dict[str, str]) -> str:
    """The candidates the seed reader refused, one ``  name: reason`` line each."""
    return ''.join(f'  {name}: {reason}\n' for name, reason in refused.items())


def _unrecordable_at(cut: int, seed: dict[str, Any] | None) -> bool:
    """Whether no shape can record this cut: turn 0 of a scenario resumed from state alone."""
    return seed is not None and cut == 0 and not _seed_messages(seed)



def scenario_dirs(run_dir: Path) -> list[Path]:
    """Every scenario directory of a run that actually produced a transcript.

    A scenario that errored wrote no transcript, so there is nothing to reproduce and nothing to
    cut — it is not a candidate, and saying so here keeps the choice below from inventing one.
    """
    return sorted(d for d in run_dir.iterdir() if d.is_dir() and (d / TRANSCRIPT_FILENAME).is_file())


def item_order(item: str) -> tuple[str, int, str]:
    """The sort key of a conversation's item name: by scenario, then trial 1 (the bare name), trial 2…, then a retry."""
    import red_cells  # noqa: PLC0415 - sibling: the item grammar the audit and the attribution share

    scenario, _, rest = item.partition('/')
    trial = red_cells._TRIAL_DIR.fullmatch(rest)  # noqa: SLF001
    return scenario, (int(trial.group(1)) if trial else 1 if not rest else sys.maxsize), rest


def run_conversations(run_dir: Path) -> dict[str, Path]:
    """``{item: directory}`` for every conversation of the run with a transcript, every trial and retry included,
    trial 1 first (:func:`item_order`).

    The item is the name the run's audit and its finding attribution give the conversation (``red_cells.run_items``):
    ``<scenario>`` for trial 1, ``<scenario>/trial_<N>`` for ``trial_<N>/<scenario>/``, ``<scenario>/stability_retry``
    for a re-run. A trial's directory shares its name with trial 1's, so the item, never the directory name, says which
    conversation a pick is. Only the transcript is required here: an unreadable ``verdicts.json`` costs its own
    conversation when it is read (:func:`violating_candidates`)."""
    import red_cells  # noqa: PLC0415

    found: dict[str, Path] = {}
    for d in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        trial = red_cells._TRIAL_DIR.fullmatch(d.name)  # noqa: SLF001
        if trial and int(trial.group(1)) >= 2:
            found.update({f'{s.name}/{d.name}': s for s in sorted(p for p in d.iterdir() if p.is_dir()) if (s / TRANSCRIPT_FILENAME).is_file()})
            continue
        if (d / TRANSCRIPT_FILENAME).is_file():
            found[d.name] = d
        if (d / red_cells.RETRY_DIRNAME / TRANSCRIPT_FILENAME).is_file():
            found[f'{d.name}/{red_cells.RETRY_DIRNAME}'] = d / red_cells.RETRY_DIRNAME
    return dict(sorted(found.items(), key=lambda entry: item_order(entry[0])))


def read_transcript(path: Path) -> dict[str, Any]:
    """The run transcript at ``path``, with the conversation's CURRENT verdicts — the cells of the ``verdicts.json``
    beside it, after any rescore or audit correction — under :data:`VERDICT_CELLS`. The transcript itself holds none.
    A missing or unreadable ``verdicts.json`` is refused, naming the converter."""
    transcript = _read_json(path)
    if not isinstance(transcript, dict):
        return transcript
    try:
        transcript[VERDICT_CELLS] = verdicts_file.cells(verdicts_file.read(path.parent))
    except verdicts_file.VerdictsError as exc:
        raise Refused(str(exc)) from exc
    return transcript


def _policy_result(transcript: dict[str, Any], policy: str) -> dict[str, Any] | None:
    results = transcript.get(VERDICT_CELLS)
    if not isinstance(results, dict):
        return None
    result = results.get(policy)
    return result if isinstance(result, dict) else None


def violates(transcript: dict[str, Any], policy: str) -> bool:
    """Whether this scenario's judges found the named policy violated — its cell in the current verdicts
    (:func:`read_transcript`), which every eval of the set has."""
    result = _policy_result(transcript, policy)
    return bool(result and result.get('violated'))


def violated_turns(transcript: dict[str, Any], policy: str) -> list[int]:
    """Every turn the policy's recorded verdict names violated, ascending: ``turns[].violated`` ∪ ``findings[].turn_idx``.

    A turn-scoped judge names its turns in ``turns``; a scenario-scoped one grades the conversation
    and names the turns its evidence sits on in ``findings``, read only when that verdict is violated.
    Typed fields only — never a turn number in prose.
    """
    result = _policy_result(transcript, policy)
    if not result:
        return []
    found = {
        entry['turn_idx'] for entry in result.get('turns') or ()
        if isinstance(entry, dict) and entry.get('violated') and _is_int(entry.get('turn_idx'))
    }
    if result.get('violated'):
        found |= {entry['turn_idx'] for entry in result.get('findings') or () if isinstance(entry, dict) and _is_int(entry.get('turn_idx'))}
    return sorted(found)


def judge_turn(transcript: dict[str, Any], policy: str) -> int | None:
    """The first turn the policy's recorded verdict names violated (:func:`violated_turns`), or None.

    A judge that names no turn leaves nothing to derive, and the caller supplies the analyst's
    evidence turn instead. Returning None rather than guessing a turn is the point: a cut taken
    from the wrong turn produces a regression that replays a conversation the issue is not in.
    """
    turns = violated_turns(transcript, policy)
    return turns[0] if turns else None


def _match_scenario(run_dir: Path, wanted: str) -> Path:
    """The scenario directory the operator named, by exact name or unambiguous substring.

    A run's directories carry the set id in the middle (``scenario_02_scenarios_f153289d_scenario_02``),
    so the name an operator has in hand from a fix plan is almost never the directory name. An
    ambiguous substring is refused rather than resolved — picking one of two would record the
    issue from a conversation nobody chose.
    """
    candidates = scenario_dirs(run_dir)
    exact = [d for d in candidates if d.name == wanted]
    if exact:
        return exact[0]
    partial = [d for d in candidates if wanted in d.name]
    if not partial:
        known = ', '.join(d.name for d in candidates) or '(none)'
        raise Refused(f'no scenario in {run_dir} matches {wanted!r}. Scenarios with a transcript: {known}.')
    if len(partial) > 1:
        names = ', '.join(d.name for d in partial)
        raise Refused(f'{wanted!r} matches several scenarios ({names}) — name one of them exactly.')
    return partial[0]


def _names_scenario(directory_name: str, name: str) -> bool:
    """Whether ``name`` — as a fix plan or the gate's report spells it — names this run directory.

    A run directory repeats the scenario's own name around the set id
    (``scenario_02_scenarios_f153289d_scenario_02``), and the reports name the scenario, not the
    directory. The match is on that shape, never on a bare substring: ``scenario_1`` must not
    name ``scenario_10_…``.
    """
    return directory_name == name or directory_name.startswith(f'{name}_') or directory_name.endswith(f'_{name}')


def _names_any(directory: Path, names: Collection[str]) -> bool:
    """Whether any of ``names``, spelled as the reports spell scenarios, names this run directory."""
    return any(_names_scenario(directory.name, name) for name in names)


def parse_cell(text: str, *, flag: str) -> tuple[str, str]:
    """A ``<scenario>:<policy>`` bench cell, split at the first colon; both halves are required."""
    scenario, _, policy = text.partition(':')
    if not scenario.strip() or not policy.strip():
        raise Refused(f'{flag} {text!r} is not <scenario>:<policy>; name the scenario and the eval policy it is red on.')
    return scenario.strip(), policy.strip()


def _cell_names_item(cell_scenario: str, item: str) -> bool:
    """Whether a bench cell's scenario half names this conversation. A bare scenario names every trial of it; an item
    (``<scenario>/trial_<N>``, trial 1 spelled ``<scenario>/trial_1``, as the audit's local ``trial_<N>/<scenario>``
    too) names that one trial only, so a false red on one trial never benches another trial's true one."""
    import red_cells  # noqa: PLC0415

    scenario, _, rest = item.partition('/')
    if '/' not in cell_scenario:
        return _names_scenario(scenario, cell_scenario)
    raw = cell_scenario.strip().strip('/')
    local = red_cells._LOCAL_TRIAL_ITEM.fullmatch(raw)  # noqa: SLF001
    named, _, named_rest = (f'{local.group(2)}/trial_{local.group(1)}' if local else raw).partition('/')
    return _names_scenario(scenario, named) and (named_rest == rest or (named_rest == 'trial_1' and not rest))


def bench_touches(item: str, policy: str | None, scenarios: Collection[str], cells: Collection[str], *, flag: str = '--exclude-cell') -> bool:
    """Whether a bench issue touches this conversation for ``policy``: a benched scenario (every trial of it), or a
    benched cell of this policy that names it (:func:`_cell_names_item`). A cell of another policy leaves it alone."""
    scenario = item.partition('/')[0]
    if any(_names_scenario(scenario, name) for name in scenarios):
        return True
    return any(cell_policy == policy and _cell_names_item(cell_scenario, item)
               for cell_scenario, cell_policy in (parse_cell(cell, flag=flag) for cell in cells))


def _violating(
    pairs: Collection[tuple[str, Path]], policy: str | None, benched: Callable[[str], bool],
) -> tuple[list[tuple[str, Path]], dict[str, str]]:
    """``(item, directory)`` of each conversation whose current verdict has ``policy`` violated, and apart, each one
    whose verdicts could not be read, with its refusal. A benched one stays a candidate, so the caller names it."""
    candidates: list[tuple[str, Path]] = []
    unreadable: dict[str, str] = {}
    for name, d in pairs:
        try:
            transcript = read_transcript(d / TRANSCRIPT_FILENAME)
        except Refused as exc:
            # Unreadable verdicts beside ONE scenario cost that scenario only, refused with the reason.
            if benched(name):
                candidates.append((name, d))  # the caller refuses it for the bench, before anything reads it again
            else:
                unreadable[name] = str(exc)
            continue
        if violates(transcript, policy or ''):
            candidates.append((name, d))
    return candidates, unreadable


def violating_candidates(pool: list[Path], policy: str | None, exclude: Collection[str]) -> tuple[list[Path], dict[str, str]]:
    """The scenarios in ``pool`` whose current verdict has ``policy`` violated — the conversations :func:`choose`
    ranks — and, apart, each one whose verdicts could not be read, with its refusal.

    A benched scenario stays a candidate, so the caller's bench exclusion names it.
    :func:`plan_auto_regressions` reads the same list, so the plan and the record never disagree on which
    conversations show an issue.
    """
    found, unreadable = _violating([(d.name, d) for d in pool], policy, lambda name: any(_names_scenario(name, n) for n in exclude))
    return [d for _, d in found], unreadable


def runs_without_a_conversation(workdir: Path) -> bool:
    """Whether ``workdir/bench_manifest.json`` declares ``agent.dispatch_shape: pipeline``: the agent runs with no user
    conversation, a fact of the agent, so no finding of any run of it holds a turn to record. An absent or unreadable
    manifest is False."""
    try:
        doc = json.loads((workdir / 'bench_manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and isinstance(doc.get('agent'), dict) and doc['agent'].get('dispatch_shape') == 'pipeline'


def resume_unsupported(workdir: Path) -> str | None:
    """Why the bench's harness cannot resume a recorded conversation, or ``None`` when it can or nothing says.

    Reads ``harness.resume`` of ``workdir/bench_manifest.json``: ``mode: unsupported`` is a sentence;
    ``replay``, an absent or unreadable manifest, an absent field and a non-dialog bench are ``None``.
    """
    try:
        doc = json.loads((workdir / 'bench_manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or str((doc.get('agent') or {}).get('dispatch_shape') or 'dialog') != 'dialog':
        return None
    resume = (doc.get('harness') or {}).get('resume')
    if not isinstance(resume, dict) or resume.get('mode') != 'unsupported':
        return None
    reason = resume.get('reason')
    return (
        'the bench manifest declares this harness cannot resume a recorded conversation '
        f'(harness.resume: unsupported{f", {reason}" if reason else ""})'
    )


#: Why a whole-conversation red cut at its audited quote turn is not recorded on a resumed shape: a resumed replay is
#: read at its cut (the SDK's at-cut reading), so a red that names no turn reads `inconclusive`, or `passed` on a
#: seeded history turn, while the defect is there. Only a replay played whole from its opener can measure it.
CANNOT_MEASURE_AT_CUT = (
    'its check grades the whole conversation and names no turn, so a replay cut mid-conversation cannot measure it'
)


class CannotMeasure(Refused):
    """Every conversation left for the issue is a whole-conversation red whose replay would be read at the cut."""


#: Why a cut replayed from its opener is not recorded when the run handed the agent a document with that opener: the
#: opener folder carries the opener's text alone (:func:`write_folder`'s own refusal, applied at the choice).
OPENER_DOCUMENT = 'its conversation opened with a handed-over document, which a replay from the opening message cannot carry'


class OpenerDocument(Refused):
    """Every conversation left for the issue would replay from an opener that came with a document (:data:`OPENER_DOCUMENT`)."""


#: Why a resumed cut is not recorded when a turn before it handed the agent a document (:func:`handed_documents_before`):
#: the pin keeps a ``[file: name]`` placeholder, never the file, and the replay's fresh workspace does not hold it.
HISTORY_DOCUMENT = "its conversation handed over a document before the issue's turn, which a replay cannot carry yet"


class HistoryDocument(Refused):
    """Every conversation left for the issue would resume after a turn that handed over a document (:data:`HISTORY_DOCUMENT`)."""


def _replays_whole(cut: int, transcript: dict[str, Any], seed: dict[str, Any] | None, resume_gap: str | None) -> bool:
    """Whether the folder cut here replays from its opener (opener-only, or opener-rooted), never resumed at the cut."""
    return not _carries_history(cut, seed) or resume_gap is not None or bool(world_written_before_cut(transcript, cut))


def choose(
    run_dir: Path,
    *,
    policy: str | None,
    scenario: str | None,
    cut_turn: int | None,
    resume_gap: str | None,
    workdir: Path,
    exclude: Collection[str] = (),
    exclude_cells: Collection[str] = (),
    within: Collection[str] | None = None,
    pool: Mapping[str, Path] | None = None,
    cuts: Mapping[str, int] | None = None,
    quote_cut: Collection[str] = (),
) -> dict[str, Any]:
    """Pick the scenario and the cut for one issue.

    ``resume_gap`` is :func:`resume_unsupported` of the bench: when set, the harness cannot resume a
    recorded conversation, so a turn-0 candidate (the opener-only shape) wins over every later cut, and a
    later cut on a scenario that was not itself resumed is the opener-rooted shape: the recorded opener,
    replayed as a whole conversation. A cut in a scenario that was itself resumed is refused with that
    reason. Required, never defaulted: a caller that does not ask would record folders nothing can replay.
    A later cut whose history changed a world the pin cannot restore (:func:`world_written_before_cut`) is
    opener-rooted the same way on any harness, and loses the ranking to a candidate whose pin replays faithfully.

    One regression per issue, from the scenario with the SHORTEST NON-ZERO seed: the same issue
    seen in three conversations is one root cause, and the shortest seed replays it with the least
    frozen history, so the fewest products of the current toolchain ride along. A candidate whose
    cut is turn 0 seeds nothing, which is the opener-only shape rather than a shorter seed — it
    wins only when no candidate has a seed at all.

    A RESUMED candidate's pin carries its resume seed ahead of the cut, so its seed is the resume
    seed's user turns plus the cut (:func:`seed_turns`), and it ranks on that. Its cut still counts
    live turns — the numbering its judges report — so a turn-0 issue there is the resumed shape
    cut at the seed, never the opener-only shape, which would drop the history the turn answered.

    The two overrides are independent. ``scenario`` replaces the choice — and, when a policy is
    named too, the named conversation still has to be one the judges found that policy violated
    in, because a cut taken from a passing conversation records a regression that passes forever.
    ``cut_turn`` replaces the
    cut of whichever scenario is chosen and never the choice itself, so the ranking runs on each
    candidate's default cut and the override is applied to the winner. Applied to every candidate
    first, one number ties them all and the tie-break on the name records whichever sorts first.
    An override that does not fit the chosen conversation is refused, never carried to another.

    A candidate whose transcript :func:`resume_seed` refuses is dropped from the ranking and
    reported under ``refused`` with its reason. Left in, one unreadable transcript would refuse the
    whole choice while another conversation with the same issue records cleanly; dropped without a
    word, the operator would never learn it was passed over. The choice is refused only when every
    candidate is, naming each reason. A scenario named with ``scenario`` is the operator's own
    choice, so its refusal stands as it is.

    ``exclude`` names scenarios a bench issue touches, as the reports spell them rather than as run
    directories (:func:`_names_scenario`), so a conversation from them may carry the
    bench's mistake rather than the agent's. They leave the ranking the way a refused candidate
    does and are listed under ``refused``, but they do not count as conversations the analyst has
    to choose between: the bench ruled them out, not the missing turn.

    ``exclude_cells`` names ``<scenario>:<policy>`` cells a bench issue touches, or ``<scenario>/trial_<N>:<policy>``
    for one trial (:func:`bench_touches`). A cell leaves the ranking like an excluded scenario, but only when its
    policy is the one being chosen for: the scenario's conversation is still a clean source for any other policy's issue.

    ``within`` names the only scenarios the issue may be recorded from, spelled as the reports
    spell them — a finding's ``seen in:`` list. The issue paragraph is written from those
    conversations, so a cut from any other scenario that happens to fail the same policy records
    a paragraph over a conversation that may not show the issue, and the finding then reads as
    covered for good. When none of them has a transcript, has the policy violated, or survives
    the checks below, the choice is refused and nothing widens it.

    ``pool`` is the planned choice instead (:func:`finding_pool`, read off a plan row by :func:`pick_from_plan`):
    ``{item: directory}`` over every trial, the finding's own conversations. ``cuts`` is each one's own cut (the
    finding's first linked turn there), which replaces the judge's first red: two findings of one check in one
    conversation are cut each at its own turn. ``quote_cut`` names the items cut at an audited quote turn of a
    whole-conversation red; one whose shape would be read at the cut is refused with :data:`CANNOT_MEASURE_AT_CUT`
    (:class:`CannotMeasure` when no other is left). Without a pool the candidates are the run's top-level scenarios.
    Every candidate is keyed by its ITEM — ``considered``, ``refused``, the tie-break (trial 1 first) and the output's
    ``scenario`` and ``item`` — and ``transcript`` is that trial's own: a later trial's directory shares trial 1's name.

    ``workdir`` (required: the bench, ``regressions_dir``'s parent) holds the typed document fields: a candidate whose cut
    replays from its opener (:func:`_replays_whole`) while that opener came with a document (:func:`document_inputs`) is
    refused with :data:`OPENER_DOCUMENT` — the write step's own rule, so the plan, the pick and the write agree —
    and :class:`OpenerDocument` is raised when no other candidate is left.
    """
    if not run_dir.is_dir():
        raise Refused(f'{run_dir} is not a directory — name the run output directory.')
    if policy is None and scenario is None:
        raise Refused('name the failed policy (--policy) or the scenario (--scenario); one of them chooses the conversation.')
    if pool is not None and (scenario is not None or within is not None):
        raise Refused('a planned pick is the whole choice: --scenario and --seen-in do not apply to it.')
    own_cuts = dict(cuts or {})
    quoted = set(quote_cut)

    def benched(name: str) -> bool:
        return bench_touches(name, policy, exclude, exclude_cells)

    seen = list(dict.fromkeys(within)) if within is not None else None
    if seen is not None and not seen:
        raise Refused('the issue names no scenario it was seen in, so there is no conversation to record it from.')

    def seen_in(name: str) -> bool:
        return seen is None or any(_names_scenario(name, wanted) for wanted in seen)

    #: Candidates whose re-score sidecar could not be read: refused one by one, never the whole run.
    unreadable: dict[str, str] = {}

    if scenario is not None:
        named = _match_scenario(run_dir, scenario)
        if not seen_in(named.name):
            raise Refused(
                f'{named.name} is not one of the scenarios the issue was seen in ({", ".join(seen or ())}), so its '
                'conversation may not show the issue. Name one of those, or record nothing.'
            )
        if benched(named.name):
            raise Refused(
                f'{named.name} is a scenario a bench issue touches, so its conversation may replay the bench\'s '
                'mistake rather than the agent\'s. Record from a clean run of it, or from another scenario.'
            )
        if policy is not None and not violates(read_transcript(named / TRANSCRIPT_FILENAME), policy):
            raise Refused(
                f'{named.name} did not violate {policy!r}, so the conversation named here is not one the run caught the '
                'issue in, and a regression cut from it replays a passing conversation forever. Name a scenario the run '
                'judged as violating that policy, or — when the issue is one no policy grades — drop --policy and pass '
                '--cut-turn to record from this conversation deliberately.'
            )
        candidates = [(named.name, named)]
    else:
        pairs = list(pool.items()) if pool is not None else [(d.name, d) for d in scenario_dirs(run_dir) if seen_in(d.name)]
        if pool is not None and not pairs:
            raise Refused(f'the plan names no conversation of {run_dir} to record the issue from. Nothing is recorded; the issue stays uncovered.')
        if seen is not None and not pairs:
            raise Refused(
                f'none of the scenarios the issue was seen in ({", ".join(seen)}) has a transcript in {run_dir}, so no '
                'conversation that shows it is there to record. Nothing is recorded; the issue stays uncovered.'
            )
        candidates, unreadable = _violating(pairs, policy, benched)
        if seen is not None and not candidates and not unreadable:
            raise Refused(
                f'none of the scenarios the issue was seen in ({", ".join(seen)}) has {policy!r} violated in {run_dir}, '
                'so this run holds no conversation that shows it. Nothing is recorded; the issue stays uncovered.'
            )
        if not candidates and not unreadable:
            raise Refused(
                f'no scenario in {run_dir} has {policy!r} violated, so there is no conversation to record. '
                'Check the policy name against the run report, or name the scenario with --scenario.'
            )

    # The default cut is what the candidates are ranked on, so it is derived for every one of them
    # even when an explicit cut is going to replace it on the winner.
    with_default: list[tuple[int | None, str, Path, dict[str, Any], dict[str, Any] | None]] = []
    refused: dict[str, str] = {
        name: 'a bench issue touches this scenario, so its conversation may carry the bench\'s mistake'
        for name, _ in candidates if benched(name)
    }
    bench_refused = len(refused)
    refused.update(unreadable)
    cannot_measure = documented = history_documented = 0
    candidates = [(name, d) for name, d in candidates if not benched(name)]
    for name, directory in candidates:
        transcript = read_transcript(directory / TRANSCRIPT_FILENAME)
        if user_turn_count(transcript) == 0:
            continue
        if scenario is not None:
            seed = resume_seed(transcript, where=name)
        else:
            try:
                seed = resume_seed(transcript, where=name, remedy='')
            except Refused as exc:
                refused[name] = str(exc).removeprefix(f'{name}: ').rstrip()
                continue
        default_cut = own_cuts[name] if name in own_cuts else judge_turn(transcript, policy) if policy else None
        if name in quoted and default_cut is not None and not _replays_whole(default_cut, transcript, seed, resume_gap):
            refused[name] = CANNOT_MEASURE_AT_CUT
            cannot_measure += 1
            continue
        # The cut this candidate records: its own, or the opener when the harness cannot resume and the judge names none.
        opener_whole = default_cut is None and resume_gap is not None and not _seed_messages(seed)
        if (opener_whole or (default_cut is not None and _replays_whole(default_cut, transcript, seed, resume_gap))) \
                and document_inputs(transcript, workdir):
            refused[name] = OPENER_DOCUMENT
            documented += 1
            continue
        # A resumed cut replays the pinned history in a fresh workspace: a document handed over, or a file the agent
        # wrote, before the cut is not there (the write step's own rules, so the plan, the pick and the write agree).
        if default_cut is not None and not _replays_whole(default_cut, transcript, seed, resume_gap):
            if handed_documents_before(transcript, default_cut):
                refused[name] = HISTORY_DOCUMENT
                history_documented += 1
                continue
            if writes := workspace_writes_before(transcript, default_cut):
                refused[name] = f'{REFUSED_WORKSPACE_WRITTEN}: the conversation wrote files before the issue\'s turn ({"; ".join(writes[:3])})'
                continue
        with_default.append((default_cut, name, directory, transcript, seed))

    if not with_default:
        if cannot_measure and cannot_measure == len(refused) - bench_refused:
            raise CannotMeasure(CANNOT_MEASURE_AT_CUT)
        if documented and documented == len(refused) - bench_refused:
            raise OpenerDocument(OPENER_DOCUMENT)
        if history_documented and history_documented + documented == len(refused) - bench_refused:
            raise HistoryDocument(HISTORY_DOCUMENT)
        if refused:
            # Every scenario the policy failed in is on this list, so `--scenario` leads nowhere:
            # what is left is the cause each reason names.
            raise Refused(
                f'every scenario in {run_dir} with {policy!r} violated and a user turn is refused, so there is no '
                'conversation to record:\n'
                + _refused_lines(refused)
                + 'Resolve what each line names, then re-run that scenario and record from the new run.'
            )
        raise Refused(f'no scenario in {run_dir} recorded a user turn — there is no conversation to cut.')

    # A refused candidate is still a conversation the issue is in. Under a judge that names no turn
    # the choice between conversations is the analyst's, so it counts here: counted out, the
    # survivor is returned alone and takes a --cut-turn diagnosed from the refused one.
    unranked = [name for default_cut, name, _, _, _ in with_default if default_cut is None]
    if unranked and len(with_default) + len(refused) - bench_refused > 1:
        raise Refused(
            f'{len(with_default) + len(refused)} scenarios have {policy!r} violated, and its judge grades the conversation '
            f'as a whole and names no turn, so nothing ranks them by seed length: {", ".join(unranked)}'
            + (f', and {", ".join(refused)}, refused before ranking:\n' + _refused_lines(refused) if refused else '. ')
            + 'Name the conversation with --scenario and the turn the issue is on with --cut-turn.'
        )

    if resume_gap is not None:
        opener_only = [entry for entry in with_default if entry[0] == 0 and not _carries_history(0, entry[4])]
        with_default = opener_only or with_default

    if len(with_default) == 1:
        default_cut, name, directory, transcript, seed = with_default[0]
    else:
        ranked = [entry for entry in with_default if entry[0] is not None]
        # A candidate no shape can record never wins over one that can; alone, it is chosen and
        # refused below, which names why.
        ranked = [entry for entry in ranked if not _unrecordable_at(entry[0], entry[4])] or ranked
        # A pin that replays its history faithfully wins over one whose world change it would lose.
        ranked = [entry for entry in ranked if not world_written_before_cut(entry[3], entry[0])] or ranked
        seeded = [entry for entry in ranked if _carries_history(entry[0], entry[4])]
        default_cut, name, directory, transcript, seed = min(seeded or ranked, key=lambda entry: (seed_turns(entry[0], entry[4]), item_order(entry[1])))

    turns = user_turn_count(transcript)
    cut = cut_turn if cut_turn is not None else default_cut
    opener_cut = cut is None and resume_gap is not None and not _seed_messages(seed)
    if opener_cut:
        # A harness that cannot resume replays the whole conversation from its opener (`_replays_whole`): no cut changes
        # what the replay measures, so a judge that names no turn needs none — the opener is the cut (`cut_basis: opener`).
        cut = 0
    if cut is None:
        raise Refused(
            f'{name}: the judge for {policy!r} grades the conversation as a whole and names no turn, '
            'so the cut has to come from the evidence you diagnosed — pass --cut-turn <the turn the issue is on>.'
            if policy
            else f'{name}: no policy was named, so nothing derives a cut — pass --cut-turn.'
        )
    if cut < 0 or cut >= turns:
        raise Refused(
            f'{name}: --cut-turn {cut} is outside the conversation, which has turns 0..{turns - 1}. '
            'The cut names the turn the issue is ON; everything before it becomes the seed.'
            + (' Turns count from the first live turn; the resume seed this scenario started from comes before them, whole.' if seed else '')
            + ('' if scenario is not None else ' --cut-turn changes the cut of the chosen scenario, never the choice; to cut a different conversation, name it with --scenario.')
        )
    if resume_gap is not None and _seed_messages(seed):
        raise Refused(
            f'{resume_gap}. {name} was itself resumed from a recorded conversation, so no replay on this '
            'harness reaches the turn the issue is on. Record the issue from a scenario the run started at its '
            'opening message (name it with --scenario).'
        )
    if cut_turn is not None and _replays_whole(cut, transcript, seed, resume_gap) and document_inputs(transcript, workdir):
        raise OpenerDocument(f'{name}: {OPENER_DOCUMENT}. Name a scenario whose opener carries no document, or record nothing.')
    if cut_turn is not None and not _replays_whole(cut, transcript, seed, resume_gap):
        if documents := handed_documents_before(transcript, cut):
            raise HistoryDocument(f'{name}: {HISTORY_DOCUMENT} ({", ".join(documents)}). Cut at or before that turn, or record from another scenario.')
        if writes := workspace_writes_before(transcript, cut):
            raise Refused(f'{REFUSED_WORKSPACE_WRITTEN}: {name} wrote the workspace before turn {cut} ({"; ".join(writes[:3])}). '
                          'Cut at a turn before the first write, or record from another scenario.')
    world_gap = world_written_before_cut(transcript, cut)
    if world_gap and _seed_messages(seed):
        raise Refused(
            f'{name}: {WORLD_WRITTEN_BEFORE_CUT} (turns {", ".join(map(str, world_gap))}): the history before the cut '
            'changed a world no replay restores, and the scenario was itself resumed, so its opener does not replay that '
            'history either. Record the issue from a scenario the run started at its opening message (name it with --scenario).'
        )
    if _unrecordable_at(cut, seed):
        remedy = 'Name a different scenario with --scenario, or cut at a later turn.'
        if refused:
            remedy += ' Refused before ranking:\n' + _refused_lines(refused).rstrip('\n')
        raise _state_only_refusal(name, remedy)
    seed_messages = len(_seed_messages(seed))
    # Under either gap a later cut is replayed from the opener, live: nothing is seeded and no pinned history rides along.
    rooted = (resume_gap is not None or bool(world_gap)) and _carries_history(cut, seed)
    # The node the harness entered when this scenario resumed. A cut at the seed replays that very
    # moment, so the pin names the node again; a later cut starts the leg somewhere that node is not.
    resume_point = seed.get('resume_point') if seed else None
    start_node = resume_point.get('node') if isinstance(resume_point, dict) else None
    # Kept, never re-cut: a job the pinned history left open is a fact about the replay, not a reason to drop the pin.
    caveat = None if rooted else cut_caveat(transcript, cut)
    return {
        'scenario': name,
        'item': name,
        # The run the item is of: what a pick's provenance is read by (:func:`hollow_reason`).
        'run': run_dir.name,
        'transcript': str((directory / TRANSCRIPT_FILENAME).resolve()),
        'cut_turn': cut,
        'seed_turns': 0 if rooted else seed_turns(cut, seed),
        'user_turns': user_turn_count(transcript),
        'resume_seed_messages': seed_messages,
        'shape': SHAPE_OPENER_ROOTED if rooted else SHAPE_RESUMED if _carries_history(cut, seed) else SHAPE_OPENER_ONLY,
        **({'start_node': start_node} if cut == 0 and isinstance(start_node, str) and start_node.strip() else {}),
        **({WORLD_WRITTEN_BEFORE_CUT: world_gap} if rooted and world_gap else {}),
        **({'cut_basis': 'opener'} if opener_cut else {}),
        'considered': [name for name, _ in candidates],
        'refused': refused,
        **({'replay_caveat': caveat} if caveat else {}),
    }


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def cut_at_seed_boundary(picked: Any) -> bool:
    """Whether :func:`choose` cut a resumed scenario at its first live turn.

    ``cut_turn: 0`` with ``resume_seed_messages`` above 0. The pin is then the scenario's own
    resume seed followed by the same opener, which for a trace scenario's run is the trace
    scenario's own conversation. Any later cut seeds live turns too, and a fresh scenario has no
    seed to share.
    """
    if not isinstance(picked, dict):
        return False
    cut, seeded = picked.get('cut_turn'), picked.get('resume_seed_messages')
    return _is_int(cut) and _is_int(seeded) and cut == 0 and seeded > 0


# ── writing a folder ──────────────────────────────────────────────────────────


def require_slug(slug: str, *, prefix: str = FOLDER_PREFIX) -> str:
    """The slug, or a refusal naming what to rename it to.

    Refused here, at the one moment renaming is free. The same shapes the backend refuses would
    otherwise surface after an upload, in a job that generated nothing for the folder.

    The argument is the BARE slug; ``prefix`` is added here. A slug that already carries the
    prefix is refused rather than accepted, because the folder it would write —
    ``regression_regression_<slug>`` — is a name every later membership check reads as a different
    folder from the one the operator meant to write. ``prefix`` is a parameter because trace
    scenarios obey the same rule under their own prefix, and a second copy of it would drift.
    """
    if not SLUG_RE.match(slug):
        raise Refused(
            f'{slug!r} is not a usable slug. Use lowercase ascii words joined by single underscores '
            '(refund_double_charge), so the folder name and the scenario name in the delivered set are one '
            'string, and the membership check and /simulation-run match the two by name.'
        )
    if slug.startswith(prefix):
        bare = slug[len(prefix):]
        raise Refused(
            f'{slug!r} already carries the {prefix!r} prefix this adds, so the folder would be '
            f'{prefix}{slug}. Pass the bare slug ({bare!r}).'
        )
    name = f'{prefix}{slug}'
    if len(name) > SCENARIO_NAME_MAX:
        raise Refused(
            f'{name!r} is {len(name)} characters; the delivered scenario name is cut at {SCENARIO_NAME_MAX} '
            'on a word boundary, so this folder would ship under a shortened name and stop naming its own '
            f'scenario. Shorten the slug to {SCENARIO_NAME_MAX - len(prefix)} characters or fewer.'
        )
    return slug


def _turn_states(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Each recorded turn's ``agent_state`` (``{}`` when it reported none), in turn order."""
    turns = [t for t in transcript.get('turns') or [] if isinstance(t, dict)]
    return [t.get('agent_state') if isinstance(t.get('agent_state'), dict) else {} for t in turns]


def _is_cli_run(states: list[dict[str, Any]]) -> bool:
    """Whether a Claude Code CLI harness recorded the run: a turn reports its confinement record or its CLI session."""
    return any(isinstance(state.get('host_effects'), dict) or 'cli_session' in state for state in states)


def workspace_writes_before(transcript: dict[str, Any], cut: int) -> list[str]:
    """The file writes a CLI harness's trail shows before ``cut`` (``turn N: <tool> <path>``); none on any other harness.

    The replay of a resumed CLI session starts in a fresh scenario folder, so such a file is not there."""
    states = _turn_states(transcript)
    if not _is_cli_run(states):
        return []
    return [
        f'turn {index}: {entry.get("tool")} {(entry.get("args") or {}).get("file_path") or (entry.get("args") or {}).get("notebook_path") or ""}'.rstrip()
        for index, state in enumerate(states[:cut])
        for entry in state.get('tool_trail') or []
        if isinstance(entry, dict) and entry.get('tool') in FILE_WRITE_TOOLS and entry.get('status') not in ('error', 'denied') and not entry.get('_denied')
    ]


def handed_documents_before(transcript: dict[str, Any], cut: int) -> list[str]:
    """The filename of every document a user turn before ``cut`` handed the agent (a ``file`` part carrying ``file_data``).

    Every file part counts, not only a typed document field: the pin flattens each one to ``[file: name]``."""
    names: list[str] = []
    for row in transcript.get('turns') or []:
        if not isinstance(row, dict) or not _is_int(row.get('turn_idx')) or row['turn_idx'] >= cut:
            continue
        parts = row.get('user_messages')
        names += [str(part['file'].get('filename') or 'unnamed') for part in (parts if isinstance(parts, list) else [])
                  if isinstance(part, dict) and part.get('type') == 'file' and isinstance(part.get('file'), dict) and part['file'].get('file_data')]
    return names


def cli_session_to_carry(transcript: dict[str, Any], cut: int, *, workdir: Path, where: str) -> Path | None:
    """The CLI session file a resumed regression carries, or ``None`` for a harness that is not a Claude Code CLI.

    A CLI harness reports its confinement record (``host_effects``) on every turn, and its session
    (``cli_session``: ``session_id``, the ``at`` uuid its turn ended on, the scrubbed ``file``). The
    replay resumes that session at the cut, so the turn before the cut must report one whose file
    holds its ``at``. The workspace is not part of the session: a trail showing a file written before
    the cut is refused (:func:`workspace_writes_before`), since the replay's scenario folder would not hold it.
    """
    states = _turn_states(transcript)
    if not _is_cli_run(states):
        return None
    writes = workspace_writes_before(transcript, cut)
    if writes:
        raise Refused(
            f'{REFUSED_WORKSPACE_WRITTEN}: {where} wrote the workspace before turn {cut} ({"; ".join(writes[:3])}'
            f'{" …" if len(writes) > 3 else ""}). A resumed CLI session replays in a fresh scenario folder without those files, so '
            'this cut does not reproduce the issue. Record it at a turn before the first write, or from another scenario.'
        )
    if cut > 0:
        before = states[cut - 1] if cut - 1 < len(states) else {}
    else:
        seed = transcript.get('resume_seed')
        before = (seed.get('agent_state') or {}) if isinstance(seed, dict) else {}
    session = before.get('cli_session') if isinstance(before.get('cli_session'), dict) else {}
    source = workdir / str(session.get('file') or '') if session.get('file') else None
    if source is None or not source.is_file() or not session.get('at'):
        raise Refused(
            f'{REFUSED_NO_CLI_SESSION}: {where} has no recorded CLI session for the turn before turn {cut} '
            f'({"no cli_session report" if not session else f"{source} is not on disk"}). A CLI harness replays only by '
            'resuming its own session; regenerate the harness from the current template (run /simulation-scan-agent) and re-run.'
        )
    try:
        uuids = {json.loads(line).get('uuid') for line in source.read_text(encoding='utf-8').splitlines() if line.strip()}
    except ValueError as exc:
        raise Refused(f'{REFUSED_NO_CLI_SESSION}: {source} does not parse as a CLI session ({exc}).') from exc
    if session['at'] not in uuids:
        raise Refused(f'{REFUSED_NO_CLI_SESSION}: {source} holds no entry {session["at"]!r}, the turn the cut resumes from.')
    return source


#: The largest ``issue_turn.json`` that keeps its recorded world: the backend's per-file wire cap
#: (``MAX_WIRE_FILE_BYTES``). A world that would push the file past it is left out.
ISSUE_TURN_MAX_BYTES = 256 * 1024


def issue_turn_text(recorded: dict[str, Any]) -> str:
    """``issue_turn.json`` exactly as :func:`write_folder` writes it — what the wire cap is measured on."""
    return json.dumps(recorded, ensure_ascii=False, indent=2) + '\n'


def recorded_world(transcript: dict[str, Any], cut: int) -> dict[str, Any] | None:
    """The run's recorded world through the cut, or None when it captured no world types.

    ``types`` are the captured types turn 0 reports, ``initial`` the run's starting rows of those types
    only, and ``turn_worlds`` each turn's ``turn_world`` through the cut, verbatim — never a later turn
    and never the run's final world. None too when a turn through the cut carries no ``turn_world``.
    The backend folds it; this copies it.
    """
    rows = {r.get('turn_idx'): r for r in transcript.get('turns') or () if isinstance(r, dict)}
    first = (rows.get(0) or {}).get('turn_world')
    types = first.get('types') if isinstance(first, dict) else None
    if not isinstance(types, list) or not types:
        return None
    turn_worlds = [(rows.get(turn) or {}).get('turn_world') for turn in range(cut + 1)]
    if not all(isinstance(world, dict) for world in turn_worlds):
        return None
    injected = transcript.get('injected_inputs')
    start = injected.get('initial_world_state') if isinstance(injected, dict) else None
    entities = start.get('entities') if isinstance(start, dict) else None
    initial = {
        eid: row for eid, row in (entities or {}).items() if isinstance(row, dict) and row.get('_entity_type') in types
    } if isinstance(entities, dict) else {}
    return {'types': types, 'initial': initial, 'turn_worlds': turn_worlds}


def world_written_before_cut(transcript: dict[str, Any], cut: int) -> list[int]:
    """The turns before ``cut`` whose world change a pin cut there cannot restore (:data:`WORLD_WRITTEN_BEFORE_CUT`).

    Empty when the run captured no world, when its scenario seeded rows of the captured types (the build authors
    that world afresh, consistent with the pinned history), or when no turn before the cut changed it.
    """
    world = recorded_world(transcript, cut)
    if world is None or world['initial']:
        return []
    return [
        turn for turn, turn_world in enumerate(world['turn_worlds'][:cut])
        if any((turn_world.get('delta') or {}).get(key) for key in ('upserted', 'removed'))
    ]


def issue_turn(transcript: dict[str, Any], cut: int, *, where: str) -> dict[str, Any]:
    """The turn the issue is ON, as the run recorded it: the user's message, the agent's reply, the
    turn's own calls and results when it made any, and the recorded world through it (:func:`recorded_world`).

    Read from the transcript's ``turns`` — the per-turn view numbered like the cut, live turns only
    on a resumed scenario. Everything is kept verbatim: the reply is the output the eval set's owning
    check must still fail, and a reply that claims an outcome is judged against the calls it rests on
    and the world it left. The world grades the reply at the gate and never seeds a build; it is left
    out, with a line saying so, when it would push the file past :data:`ISSUE_TURN_MAX_BYTES`.
    """
    turns = transcript.get('turns')
    row = next((r for r in turns if isinstance(r, dict) and r.get('turn_idx') == cut), None) if isinstance(turns, list) else None
    if row is None:
        raise Refused(f'{where}: records no turn {cut}, so there is no recorded reply to keep. Check --cut-turn against the run.')
    reply = row.get('agent_message')
    reply = reply if isinstance(reply, str) else flatten(reply)
    if not reply.strip():
        raise Refused(f'{where}: turn {cut} carries no agent reply text, so there is nothing for a check to fail. Cut at the turn the issue is on.')
    user = row.get('user_message')
    recorded = {'turn_index': cut, 'user_message': None if user is None else (user if isinstance(user, str) else flatten(user)), 'agent_reply': reply}
    chain = row.get('turn_messages')
    if isinstance(chain, list) and chain:
        recorded['turn_messages'] = chain
    world = recorded_world(transcript, cut)
    if world is not None:
        size = len(issue_turn_text({**recorded, 'world': world}).encode('utf-8'))
        if size <= ISSUE_TURN_MAX_BYTES:
            recorded['world'] = world
        else:
            print(f'NOTE: {where}: the recorded world is left out of {ISSUE_TURN_FILENAME} ({size} bytes with it, over the '
                  f'{ISSUE_TURN_MAX_BYTES}-byte wire cap); a check that reads the world is not measured on this regression.', file=sys.stderr)
    return recorded


#: The key the backend's runtime library puts its typed account of a call under, inside a runtime tool's result
#: envelope (``RUNTIME_SIBLING``). Backend-owned; mirrored by value.
RUNTIME_SIBLING = '_plurai_runtime'
#: The typed reason a pin carries when a JOB-mode workflow tool started before the cut and no reveal of the
#: finished job followed before it. Job state lives in the mock process, per session, and a replay opens a new
#: session: the job the pinned history started does not exist there, so polling it answers ``unknown`` and the
#: reply at the cut is made against a world the recording never had. The cut stays where it is — a single-hop
#: replay answers exactly the cut turn, so an earlier cut would replay a different reply — and the pin is kept.
JOB_OPEN_AT_CUT = 'job_open_at_cut'


def _runtime_sibling(message: Any) -> dict[str, Any] | None:
    """The runtime's typed account of one tool result: a ``tool`` message whose content is a JSON object carrying
    :data:`RUNTIME_SIBLING`. Prose, a result capped past parsing, and every other message are None."""
    if not isinstance(message, dict) or message.get('type') != 'tool':
        return None
    content = message.get('content')
    if isinstance(content, list):
        content = flatten(content)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return None
    sibling = content.get(RUNTIME_SIBLING) if isinstance(content, dict) else None
    return sibling if isinstance(sibling, dict) else None


def open_jobs(messages: Collection[Any]) -> list[str]:
    """The tools whose JOB is still open after ``messages``, in start order.

    ``workflow_start`` answers ``mode: job, status: running``, which opens one; a reveal of the finished job
    (``mode: job`` with any other status) closes the earliest open one. A poll that found the job still running
    carries no ``mode`` and closes nothing. The sibling names no job id, so starts and reveals pair by order.
    """
    names: dict[str, str] = {}
    started: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get('tool_calls') or []:
            if isinstance(call, dict) and call.get('id') and call.get('name'):
                names[str(call['id'])] = str(call['name'])
        sibling = _runtime_sibling(message)
        if sibling is None or sibling.get('mode') != 'job':
            continue
        if sibling.get('status') == 'running':
            started.append(str(message.get('name') or names.get(str(message.get('tool_call_id'))) or 'unknown tool'))
        elif started:
            started.pop(0)
    return started


#: Where a harness whose agent runs its own tool loop reports its calls: ``agent_state.tool_trail``, a list of
#: ``{tool, args, call_id, result, status}`` entries in execution order (the SDK's ``AGENT_STATE_TOOL_TRAIL_KEY``).
#: Its chain then holds no ``tool`` message, so the runtime sibling rides the entry's ``result`` instead.
TOOL_TRAIL_KEY = 'tool_trail'


def _with_trail(messages: list[Any], agent_state: Any, seen_calls: set[str]) -> list[Any]:
    """One stretch of history as :func:`open_jobs` reads it: its messages, then each ``tool_trail`` entry with a ``tool``
    name as the tool result it reported. An entry whose ``call_id`` is a chain tool message's ``tool_call_id`` is
    skipped — a harness reporting both lanes is not read twice — and so is one whose ``call_id`` an earlier stretch
    already reported: a harness that keeps its trail in graph state re-reports the seed's (or every earlier turn's) calls."""
    trail = agent_state.get(TOOL_TRAIL_KEY) if isinstance(agent_state, dict) else None
    if not isinstance(trail, list):
        return messages
    in_chain = {str(m['tool_call_id']) for m in messages if isinstance(m, dict) and m.get('type') == 'tool' and m.get('tool_call_id')}
    results: list[dict[str, Any]] = []
    for entry in trail:
        if not isinstance(entry, dict) or not isinstance(entry.get('tool'), str) or not entry['tool']:
            continue
        call_id = entry.get('call_id')
        if isinstance(call_id, str) and call_id:
            if call_id in seen_calls or call_id in in_chain:
                continue
            seen_calls.add(call_id)
        results.append({'type': 'tool', 'name': entry['tool'], 'content': entry.get('result')})
    return [*messages, *results]


def replay_caveat(messages: Collection[Any]) -> dict[str, Any] | None:
    """``{reason: job_open_at_cut, jobs: [...]}`` when the pinned history leaves a job open, else None."""
    jobs = open_jobs(messages)
    return {'reason': JOB_OPEN_AT_CUT, 'jobs': jobs} if jobs else None


def cut_caveat(transcript: dict[str, Any], cut: int) -> dict[str, Any] | None:
    """:func:`replay_caveat` over the history a pin cut at ``cut`` seeds: the resume seed, then turns ``0..cut-1`` —
    each one's chain and the ``agent_state.tool_trail`` entries it does not already hold (:func:`_with_trail`)."""
    seed = transcript.get('resume_seed')
    seen_calls: set[str] = set()
    messages = list(_seed_messages(seed)) if isinstance(seed, dict) and isinstance(seed.get('messages'), list) else []
    messages = _with_trail(messages, seed.get('agent_state') if isinstance(seed, dict) else None, seen_calls)
    rows = [r for r in transcript.get('turns') or [] if isinstance(r, dict) and _is_int(r.get('turn_idx')) and r['turn_idx'] < cut]
    for row in sorted(rows, key=lambda r: r['turn_idx']):
        chain = row['turn_messages'] if isinstance(row.get('turn_messages'), list) else []
        messages.extend(_with_trail(chain, row.get('agent_state'), seen_calls))
    return replay_caveat(messages)


def pin_caveat(folder: Path) -> dict[str, Any] | None:
    """:func:`replay_caveat` over a folder's ``resume_pin.json`` — its chain and the ``agent_state.tool_trail`` it carries,
    which is the last pinned turn's only; None when it has no readable pin."""
    try:
        pin = json.loads((folder / PIN_FILENAME).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    messages = pin.get('messages') if isinstance(pin, dict) else None
    return replay_caveat(_with_trail(messages, pin.get('agent_state'), set())) if isinstance(messages, list) else None


#: The paragraph's **Probe** line (``**Probe.**``, ``**Probe:**`` or a ``- probe:`` bullet).
_PROBE_LINE = re.compile(r'^[ \t]*(?:[-*+][ \t]+)?[*_]{0,2}probe[*_]{0,2}[ \t]*[.:][*_]{0,2}(.*)$', re.IGNORECASE | re.MULTILINE)


def probe_policy(roles_text: str) -> str | None:
    """The policy the paragraph's **Probe** line names — its first backticked name, as :func:`parse_findings` reads a probe."""
    line = _PROBE_LINE.search(roles_text)
    name = _BACKTICKED.search(line.group(1)) if line else None
    return name.group(1) if name else None


def refuse_unjudged_cut(transcript: dict[str, Any], policy: str | None, cut: int, *, where: str) -> None:
    """Refuse a cut the probe policy's audited verdict contradicts: it names violated turns and not this one.

    The reply kept at the cut is what the eval set's owner must fail, so it has to be the reply the
    verdict found. Only an explicit mismatch refuses: a verdict that found nothing (a false green an
    audit confirmed — the defect a regression exists to pin), names no turn, or no probe policy at
    all leaves the analyst's cut standing.
    """
    turns = violated_turns(transcript, policy) if policy else []
    if turns and cut not in turns:
        named = ', '.join(str(turn) for turn in turns)
        raise Refused(
            f'{where}: turn {cut} is not a turn {policy!r} was found violated on (it names turn{"s" if len(turns) > 1 else ""} {named}), '
            f'so the reply at turn {cut} is not the one its verdict found. Cut at the turn it names.'
        )


#: The findings store beside the findings file (``record_finding``): each row's typed ``evidence`` names its own turns.
FINDINGS_STORE_FILENAME = 'agent_findings.jsonl'


def _store_rows(workdir: Path) -> dict[str, dict[str, Any]]:
    """The findings store's rows by id; none when it is absent or unreadable."""
    import record_finding  # noqa: PLC0415 - sibling: the findings store

    try:
        return record_finding.read_rows(workdir / 'artifacts' / FINDINGS_STORE_FILENAME)
    except (OSError, ValueError):
        return {}


def evidence_turns(row: Mapping[str, Any] | None, run: str) -> dict[str, list[int]]:
    """``{item: [turns]}``: the turns a findings-store row's ``evidence`` quotes in ``run``, items in the run's own spelling
    (``red_cells.normalize_item``). A row with no evidence for that run names none."""
    import red_cells  # noqa: PLC0415

    found: dict[str, set[int]] = {}
    for entry in (row or {}).get('evidence') or []:
        if isinstance(entry, dict) and entry.get('run') == run and isinstance(entry.get('item'), str) and _is_int(entry.get('turn_idx')):
            found.setdefault(red_cells.normalize_item(entry['item']), set()).add(entry['turn_idx'])
    return {item: sorted(turns) for item, turns in found.items()}


def own_turns(workdir: Path, run_dir: Path | None, item: str, finding_id: str, *, run: str | None = None) -> tuple[list[int], set[int]] | None:
    """``(evidence turns, own turns)``: THE turns a cut may sit on to be the finding's own in one conversation, which the
    write guard (:func:`refuse_off_evidence_cut`) and the hollow test (:func:`hollow_reason`) both read. None when the
    findings store quotes no evidence for the finding in that run (``run``, else ``run_dir``'s name) and item: there is
    nothing to hold a cut against. The own turns are:

    - every turn its evidence quotes there;
    - every turn the run's attribution links to the finding there (the store quotes a subset of them);
    - for a whole-conversation link (``turn: null``) to the finding there, every turn that check's verdict names and the
      audit's quote turn: where :func:`_own_pool` cuts such a cell.

    The run's links are read off ``run_dir``; without one (or unreadable) the evidence turns alone are the own turns."""
    import red_cells  # noqa: PLC0415

    item = red_cells.normalize_item(item)
    run = run or (run_dir.name if run_dir is not None else None)
    evidence = evidence_turns(_store_rows(workdir).get(finding_id), run).get(item) if run else None
    if not evidence:
        return None
    own = set(evidence)
    if run_dir is None or run_dir.name != run:
        return evidence, own
    try:
        reading = RunReading(run_dir)
    except (OSError, ValueError):
        return evidence, own
    for (at, policy), entries in reading.links.items():
        if at != item:
            continue
        for turn, to in entries:
            if to != finding_id:
                continue
            if turn is not None:
                own |= {t for t in (turn if isinstance(turn, list) else [turn]) if _is_int(t)}
                continue
            directory = reading.items.get(item)
            own |= set((_judged_turns(directory, policy) or []) if directory is not None else [])
            if (quote := reading.quote_turn(item, policy)) is not None:
                own.add(quote)
    return evidence, own


def refuse_off_evidence_cut(issue_from: Path, cut: int, roles_text: str, regressions_dir: Path) -> None:
    """Refuse a cut that is none of the turns the paragraph's finding was recorded on in this conversation.

    Refused only when the findings store has evidence for the SAME run and item, and the cut is none of the finding's own
    turns there (:func:`own_turns`). No finding id, no store, or an item without evidence leaves the cut standing."""
    found = findings_file._FINDING_ID.search(roles_text)
    if found is None:
        return
    run_dir, item = run_of(issue_from)
    said = own_turns(regressions_dir.parent, run_dir, item, found.group(1))
    if said is None or cut in said[1]:
        return
    turns = said[0]
    raise Refused(
        f'cut_off_evidence: {issue_from}: turn {cut} is not a turn finding {found.group(1)!r} was recorded on in this conversation '
        f'(evidence turns {", ".join(map(str, turns))}), so the reply kept there may be another issue\'s. Cut at one of its own turns.'
    )


def document_inputs(transcript: dict[str, Any], workdir: Path) -> list[str]:
    """The typed document fields (``.document_fields``) the run handed the agent with its opener.

    A run since RT55 S1 carries the opener's document as a ``file`` part on the first turn the user speaks on — turn 0,
    or turn 1 when the host opened (``<field>.<ext>``, so the field is the filename's stem); a run recorded before it
    carried the value under the field in
    ``injected_inputs.initial_agent_context``. Either record refuses.
    """
    path = workdir / DOCUMENT_FIELDS_FILENAME
    entries = _read_json(path) if path.is_file() else []
    fields = [entry.get('field') for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
    injected = transcript.get('injected_inputs')
    context = injected.get('initial_agent_context') if isinstance(injected, dict) else None
    context = context if isinstance(context, dict) else {}
    turns = transcript.get('turns')
    # The FIRST TURN THE USER SPOKE ON: turn 0, or turn 1 on a run the host opened (turn 0 is the agent's greeting, and
    # the opener's documents ride the turn her first line does).
    first = next((t for t in turns if isinstance(t, dict) and t.get('user_message') is not None), None) if isinstance(turns, list) else None
    opener = first.get('user_messages') if first is not None else None
    parted = {
        PurePosixPath(str(part['file'].get('filename') or '')).stem
        for part in (opener if isinstance(opener, list) else [])
        if isinstance(part, dict) and part.get('type') == 'file' and isinstance(part.get('file'), dict) and part['file'].get('file_data')
    }
    return [field for field in dict.fromkeys(fields) if isinstance(field, str) and (field in parted or context.get(field) not in (None, '', [], {}))]


def write_folder(
    regressions_dir: Path,
    *,
    slug: str,
    roles_text: str,
    first_talk_from: Path | None,
    issue_from: Path | None,
    cut_turn: int | None,
    trigger: str | None = None,
) -> Path:
    """Create ``regression_<slug>`` with the issue paragraph, and the opener when there is no seed.

    The paragraph goes in FIRST and stays first: the converter appends its resume section to an
    existing ``ROLES.md`` and never overwrites what is above it, so writing the paragraph before
    the conversion is what makes the folder read as an issue that carries a conversation rather
    than a conversation that happens to carry a note.

    An existing folder is refused rather than replaced. A folder present means the issue already
    has a regression, and re-recording it over a new run would silently swap the evidence that
    earlier runs under the same name replayed. Retiring is deleting the folder, which is the
    operator's move.

    ``issue_from`` + ``cut_turn`` name the transcript and the turn the issue is on; its user message and
    agent reply are written to ``issue_turn.json`` — refused when the paragraph's probe policy has a
    recorded verdict that names other turns (:func:`refuse_unjudged_cut`). The opener-only shape is turn
    0 by definition, and carries the opener's text only, so it is refused when the run handed the agent
    a document with that opener (:func:`document_inputs`).

    An opener with a later ``cut_turn`` is the opener-rooted shape, written only when the bench manifest
    declares the harness cannot resume (:func:`resume_unsupported` of the workdir, ``regressions_dir``'s
    parent) or the history before the cut changed a world no pin restores (:func:`world_written_before_cut`,
    which also refuses the resumed shape at that cut); anywhere else it is refused, as the resumed shape records that cut. Its replay plays the whole
    conversation from the opener, so the paragraph must state the user move that exposed the issue on a
    ``**Trigger.**`` line — ``trigger`` writes it — or the scenario built from it has no reason to reach the issue.
    """
    require_slug(slug)
    if not roles_text.strip():
        raise Refused('the ROLES.md issue paragraph is empty — the paragraph is the only place the issue is stated.')
    opening = next(line for line in roles_text.splitlines() if line.strip())
    if not opening.strip().startswith(ISSUE_HEADING):
        raise Refused(
            f'the ROLES.md paragraph opens on {opening.strip()[:60]!r}, not on {ISSUE_HEADING!r}. The heading is what '
            'the folder is read by: the converter appends its resume section below the paragraph, and a folder whose '
            'first heading is the resume section states a conversation and no issue.'
        )
    folder = regressions_dir / f'{FOLDER_PREFIX}{slug}'
    if folder.exists():
        raise Refused(
            f'{folder} already exists, so this issue already has a regression. A folder\'s presence IS its status: '
            'read it to check it covers this issue, and delete it to retire the regression before recording another.'
        )
    if trigger is not None and trigger.strip():
        if _TRIGGER_RE.search(roles_text):
            raise Refused('the paragraph already states its trigger on a **Trigger.** line; pass --trigger or write the line, not both.')
        roles_text = roles_text.rstrip('\n') + '\n\n' + TRIGGER_LINE.format(trigger=' '.join(trigger.split())) + '\n'
    rooted = first_talk_from is not None and cut_turn is not None and cut_turn != 0
    if rooted and resume_unsupported(regressions_dir.parent) is None and not world_written_before_cut(_read_json(first_talk_from), cut_turn):
        raise Refused(f'the opener-only shape is an issue on turn 0, and --cut-turn is {cut_turn}. Record it as the resumed shape instead.')
    if first_talk_from is None and issue_from is not None and cut_turn is not None and (lost := world_written_before_cut(_read_json(issue_from), cut_turn)):
        raise Refused(
            f'{issue_from}: {WORLD_WRITTEN_BEFORE_CUT} (turns {", ".join(map(str, lost))}): the history before turn {cut_turn} changed '
            'a world its pin cannot restore, so a resumed replay answers against the world before that history. Record the '
            'opener-rooted shape: pass the same transcript as --first-talk-from, with --trigger.'
        )
    if rooted and not _TRIGGER_RE.search(roles_text):
        raise Refused(
            'an issue after the opening turn recorded from its opener is the opener-rooted shape: its replay plays '
            'the whole conversation from the recorded opener, and reaches the issue only when the user makes the move '
            "that exposed it. Pass --trigger with that move (a finding's `trigger:` line), or write a **Trigger.** line."
        )

    recorded = None
    if issue_from is not None:
        if cut_turn is None:
            raise Refused('--issue-from needs --cut-turn: the turn the issue is on is the turn whose reply is kept.')
        source = read_transcript(issue_from)
        recorded = issue_turn(source, cut_turn, where=str(issue_from))
        refuse_unjudged_cut(source, probe_policy(roles_text), cut_turn, where=str(issue_from))
        refuse_off_evidence_cut(issue_from, cut_turn, roles_text, regressions_dir)
        # An opener folder seeds nothing, so no job the recorded history started rides into its replay.
        if first_talk_from is None and (caveat := cut_caveat(source, cut_turn)) is not None:
            print(f'NOTE: {issue_from}: {JOB_OPEN_AT_CUT} ({", ".join(caveat["jobs"])}): a job started before turn {cut_turn} had not '
                  'finished by it, and the replay starts without it. Recorded as is; the run report marks the regression.', file=sys.stderr)
    session = None
    if issue_from is not None and first_talk_from is None and cut_turn is not None:
        resumed_from = _read_json(issue_from)
        if documents := handed_documents_before(resumed_from, cut_turn):
            raise Refused(
                f'{issue_from}: {HISTORY_DOCUMENT} ({", ".join(documents)}, before turn {cut_turn}). The pin keeps the file\'s name, '
                'never the file, so the replay would answer without it. Record the issue at or before that turn, or from another conversation.'
            )
        session = cli_session_to_carry(resumed_from, cut_turn, workdir=regressions_dir.parent, where=str(issue_from))

    opener = None
    if first_talk_from is not None:
        transcript = _read_json(first_talk_from)
        seed = resume_seed(transcript, where=str(first_talk_from), remedy='Record the issue from a different scenario.')
        if seed is not None and not _seed_messages(seed):
            raise _state_only_refusal(str(first_talk_from), 'Record the issue from a different scenario.')
        if seed is not None:
            raise Refused(
                f'{first_talk_from} is a resumed scenario: its turn 0 answers the conversation it resumed, and a turn-0 '
                'folder holds the opener with no pin, so that history would be lost. Record it as the resumed shape — '
                'convert with --cut-turn 0, which keeps the resume seed as the history.'
            )
        messages = transcript.get('user_messages')
        if not isinstance(messages, list) or not messages:
            raise Refused(f'{first_talk_from} records no user message, so there is no opener to reproduce.')
        opener = flatten(messages[0].get('content') if isinstance(messages[0], dict) else messages[0]).strip()
        if not opener:
            raise Refused(
                f'{first_talk_from}: the opening user message carries no text, so the turn-0 shape has nothing to '
                'record. Record the issue from a scenario whose opener is text, or cut at a later turn.'
            )
        documents = document_inputs(transcript, regressions_dir.parent)
        if documents:
            raise Refused(
                f'{first_talk_from}: the opener came with a document input ({", ".join(documents)}), and the '
                f'{SHAPE_OPENER_ROOTED if rooted else SHAPE_OPENER_ONLY} folder carries the opener text alone, so its replay would hand the agent a different document or none. '
                'Record the issue from a scenario whose opener carries no document, or record nothing.'
            )

    folder.mkdir(parents=True)
    (folder / ROLES_FILENAME).write_text(roles_text.rstrip('\n') + '\n', encoding='utf-8')
    if opener is not None:
        (folder / FIRST_TALK_FILENAME).write_text(opener + '\n', encoding='utf-8')
    if recorded is not None:
        (folder / ISSUE_TURN_FILENAME).write_text(issue_turn_text(recorded), encoding='utf-8')
    if session is not None:
        # Copied as the harness recorded it: scrubbed by the SDK's credential redaction when it left the CLI.
        (folder / CLI_SESSION_DIRNAME).mkdir()
        shutil.copyfile(session, folder / CLI_SESSION_DIRNAME / session.name)
    return folder


def finding_paragraph(roles_text: str, *, slug: str, finding_id: str, regressions_dir: Path | None = None) -> str:
    """The issue paragraph of a regression that records a confirmed finding, ending on its id line.

    Both sources write it: the suite build at gate exit, and ``/simulation-fixes`` for an issue it
    seeded from the findings file. The slug is checked against the finding's own, because the name
    is how the other source finds this folder: a folder under any other name makes the same issue
    look uncovered, and it is recorded twice. The ``finding id:`` line is what tells this folder
    apart from another finding's whose id cuts to the same slug (:func:`finding_slug`).
    """
    expected = finding_slug(finding_id, regressions_dir)
    if slug != expected:
        raise Refused(
            f'finding {finding_id!r} is recorded as regression_{expected}, not regression_{slug}. The name is how a '
            'later record of the same finding finds this folder; pass --slug ' + expected + '.'
        )
    recorded = findings_file._FINDING_ID.search(roles_text)
    if recorded is not None and recorded.group(1) != finding_id:
        raise Refused(
            f'the paragraph records finding {recorded.group(1)!r} on its finding id line, not {finding_id!r}. '
            'Write the paragraph for this finding.'
        )
    text = roles_text.rstrip('\n')
    if recorded is None:
        text += '\n\n' + FINDING_ID_LINE.format(finding_id=finding_id)
    return text + '\n'


def suite_build_paragraph(roles_text: str, *, slug: str, finding_id: str, regressions_dir: Path | None = None) -> str:
    """:func:`finding_paragraph`, closed by the suite-build line: confirmed, no fix accepted yet."""
    text = finding_paragraph(roles_text, slug=slug, finding_id=finding_id, regressions_dir=regressions_dir).rstrip('\n')
    if SUITE_BUILD_LINE not in text:
        text += '\n\n' + SUITE_BUILD_LINE
    return text + '\n'


# ── carrying a trace scenario's world facts ───────────────────────────────────

#: A fence opener: three or more backticks with no backtick after them, or three or more tildes.
_FENCE_OPEN = re.compile(r'^ {0,3}(?:(`{3,})[^`]*|(~{3,}).*)$')
_FENCE_CLOSE = re.compile(r'^ {0,3}(`{3,}|~{3,})[ \t]*$')
_ATX_HEADING = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$')

CARRIED = 'carried'
ALREADY_CARRIED = 'already carried'
NOT_CARRIED = 'not carried'

#: The line a carried section gets under its heading. The converter's intro counts turns as the
#: production trace does ("from turn N — the turn this scenario replays live"), which is the
#: regression's own count only when its cut falls at the seed boundary.
CARRIED_NOTE = (
    "*Carried from trace scenario `{folder}`: the turn numbers in this section are the production trace's, "
    "not this regression's.*"
)


def _scan(lines: list[str]) -> tuple[list[tuple[int, int, str]], int | None]:
    """Every ATX heading outside a fenced code block as ``(line index, depth, text)``, and the index
    of the line that opens a fence left unclosed at the end, or ``None``.

    The converter fences every tool value it copies into the world facts, sized so no value can
    close its fence, precisely so a tool result cannot forge a heading. Reading headings without
    tracking fences would hand that forgery back.
    """
    found: list[tuple[int, int, str]] = []
    fence = ''
    opened = 0
    for index, line in enumerate(lines):
        if fence:
            close = _FENCE_CLOSE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                fence = ''
            continue
        opening = _FENCE_OPEN.match(line)
        if opening:
            fence, opened = opening.group(1) or opening.group(2), index
            continue
        heading = _ATX_HEADING.match(line)
        if heading:
            found.append((index, len(heading.group(1)), heading.group(2)))
    return found, (opened if fence else None)


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    return _scan(lines)[0]


def unclosed_fence(text: str) -> int | None:
    """The 1-based line of a code fence that never closes, or ``None``.

    Such a fence turns every line after it into fenced text, headings included, so a reader that
    finds no heading there cannot tell an absent section from a hidden one.
    """
    opened = _scan(text.splitlines(keepends=True))[1]
    return None if opened is None else opened + 1


def world_facts_section(roles_text: str) -> str | None:
    """A ``ROLES.md``'s world-facts section — its heading line to the end of the file — or ``None``.

    The converter always writes the section last, so everything from its heading on is the
    section. The LAST such heading is taken: a paragraph above it may name the section, and none
    can come after it.
    """
    lines = roles_text.splitlines(keepends=True)
    starts = [index for index, _depth, text in _headings(lines) if text == WORLD_FACTS_HEADING]
    return ''.join(lines[starts[-1]:]) if starts else None


def _reroot(section: str, depth: int) -> str:
    """``section`` with its heading at ``depth`` and every heading under it moved by the same step.

    Headings inside fences are tool text and stay as they are.
    """
    lines = section.splitlines(keepends=True)
    found = _headings(lines)
    shift = depth - found[0][1]
    if shift == 0:
        return section
    for index, level, _text in found:
        line = lines[index]
        start = line.index('#')
        lines[index] = line[:start] + '#' * (level + shift) + line[start + level:]
    return ''.join(lines)


def carry_world_facts(source_roles: Path, regression: Path) -> tuple[str, str]:
    """Append the world facts of a trace scenario's ``ROLES.md`` to its regression's ``ROLES.md``.

    The converter writes no world facts for a run (``plurai-run``): a run's tool results came from
    the mock, and world facts describe what production returned. So a regression recorded from a
    trace scenario's run gets them from the trace scenario's own folder, copied verbatim, as the
    LAST section of its ``ROLES.md``, with one line under the heading (:data:`CARRIED_NOTE`) saying
    whose turn numbers it uses. Run after the conversion, never before: the converter appends too,
    and the section is only last when nothing is appended after it.

    **Heading depth.** The section takes the depth of the regression's ``Provenance`` heading —
    the converter's last heading, whose sibling it writes the world facts as: ``###`` inside the
    ``--into`` merge the record step uses, ``##`` in a standalone conversion. A section from a
    folder of the other depth is re-rooted, its entries one level beneath; nothing else changes.

    Returns ``(status, message)``. :data:`CARRIED` names the file written. :data:`ALREADY_CARRIED`
    means the regression has the section, so a second run appends nothing. :data:`NOT_CARRIED`
    means the trace scenario's ``ROLES.md`` is gone or holds no section: the regression stays as
    it is and the message is the warning, because a record is never refused for missing world
    facts. :class:`Refused` for a regression that is not a finished resumed folder with the
    converter's resume section, since appending there would put the section where the next
    conversion writes past it. Writes nothing outside the regression folder.
    """
    if not regression.is_dir():
        raise Refused(f'{regression} is not a folder — name the regression folder the record step wrote.')
    if folder_shape(regression) != SHAPE_RESUMED:
        raise Refused(
            f'{regression.name} is the {folder_shape(regression)} shape: it resumes nothing, so there is no live leg for world facts to '
            'constrain. World facts ride only a regression recorded from a resumed scenario.'
        )
    roles_path = regression / ROLES_FILENAME
    try:
        text = roles_path.read_text(encoding='utf-8')
    except OSError as exc:
        raise Refused(f'{roles_path} could not be read ({type(exc).__name__}: {exc}).') from exc
    headings = _headings(text.splitlines(keepends=True))
    if any(heading == WORLD_FACTS_HEADING for _index, _depth, heading in headings):
        return ALREADY_CARRIED, f'{roles_path} already carries "{WORLD_FACTS_HEADING}"; nothing was appended.'
    provenance = [depth for _index, depth, heading in headings if heading == PROVENANCE_HEADING]
    if not provenance:
        if (line := unclosed_fence(text)) is not None:
            raise Refused(
                f'{roles_path} line {line} opens a code fence that never closes, so every heading after it — the '
                f'converter\'s "{PROVENANCE_HEADING}" among them — reads as fenced text. Close that fence in the issue '
                'paragraph, then carry the world facts again.'
            )
        raise Refused(
            f'{roles_path} holds no converter resume section (no "{PROVENANCE_HEADING}" heading). Carry the world facts '
            'after the conversion into the folder, so they stay the last section of the file.'
        )

    try:
        source_text = source_roles.read_text(encoding='utf-8')
    except OSError as exc:
        return NOT_CARRIED, (
            f'{source_roles} could not be read ({type(exc).__name__}) — the trace scenario folder is gone, so the '
            'regression is recorded without its world facts.'
        )
    section = world_facts_section(source_text)
    if section is None:
        hidden = unclosed_fence(source_text)
        return NOT_CARRIED, (
            f'{source_roles} holds no "{WORLD_FACTS_HEADING}" section'
            + (f' outside the code fence its line {hidden} opens and never closes' if hidden is not None else '')
            + ', so the regression is recorded without world facts.'
        )
    heading, _newline, rest = _reroot(section, provenance[-1]).partition('\n')
    note = CARRIED_NOTE.format(folder=source_roles.parent.name)
    carried = f'{heading}\n\n{note}\n{rest}'
    roles_path.write_text(text.rstrip('\n') + '\n\n' + carried.rstrip('\n') + '\n', encoding='utf-8')
    return CARRIED, str(roles_path)


# ── reading the directory back ────────────────────────────────────────────────


def _issue_turn_index(folder: Path) -> int:
    """The ``turn_index`` the folder's ``issue_turn.json`` records; 0 when it records none or cannot be read."""
    try:
        index = json.loads((folder / ISSUE_TURN_FILENAME).read_text(encoding='utf-8')).get('turn_index')
    except (OSError, ValueError, AttributeError):
        return 0
    return index if _is_int(index) and index > 0 else 0


def folder_shape(folder: Path) -> str:
    """What the folder replays, decided by the files in it: a pin is the resumed shape; an opener is the
    opener-only shape, or the opener-rooted one when its ``issue_turn.json`` is a later turn.

    A folder offering both a pin and an opener is refused here for the reason the backend refuses
    it: the two are competing statements about one channel, so honouring either reproduces
    something nobody recorded.
    """
    pinned = (folder / PIN_FILENAME).is_file()
    opener = (folder / FIRST_TALK_FILENAME).is_file()
    if pinned and opener:
        raise Refused(
            f'{folder.name} carries both {PIN_FILENAME} and {FIRST_TALK_FILENAME}, and the backend refuses a folder '
            'that states its opener twice. A regression is one or the other: a cut conversation, or a turn-0 opener.'
        )
    if pinned:
        return SHAPE_RESUMED
    if opener:
        return SHAPE_OPENER_ROOTED if _issue_turn_index(folder) else SHAPE_OPENER_ONLY
    raise Refused(
        f'{folder.name} carries neither {PIN_FILENAME} nor {FIRST_TALK_FILENAME}, so it replays nothing. Finish the '
        'record (run the converter into it) or delete the folder.'
    )


def folders(directory: Path, *, prefix: str = FOLDER_PREFIX) -> list[Path]:
    """Every folder under ``directory`` whose name carries ``prefix``, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(d for d in directory.iterdir() if d.is_dir() and d.name.startswith(prefix))


def overview_text(regressions_dir: Path) -> str:
    """The directory's ``BUNDLE.md``, rewritten from what is on disk.

    Regenerated rather than appended to, and rewritten after every conversion, because the
    converter writes its own overview for a directory of converted traces — accurate for the
    folders it just wrote and silent about every opener-only folder beside them. The final batch
    uploads its own merged overview (``final_bundle.py``), built from :func:`overview_section`,
    so this file describes the directory to whoever opens it.

    A folder ``folder_shape`` refuses is refused here too, before anything is written: an overview
    that describes a folder as replaying nothing is the description of a set the backend builds
    anyway, with a cold-authored scenario under that folder's name. The folder is finished or
    deleted first; the overview is never the place that records it as unfinished.
    """
    return overview_section(folders(regressions_dir))


def overview_section(present: list[Path], *, level: int = 1) -> str:
    """The regressions part of a bundle overview: what a regression is, then one row per folder.

    ``level`` is the heading depth, so the same text heads the directory's own ``BUNDLE.md`` and
    sits as one section of the final batch's merged overview. Raises :class:`Refused` on a folder
    ``folder_shape`` refuses, for the reason :func:`overview_text` gives.
    """
    rows = []
    for folder in present:
        shape = folder_shape(folder)
        replays = {
            SHAPE_RESUMED: 'continues its recorded conversation from the pin',
            SHAPE_OPENER_ONLY: f'opens on `{FIRST_TALK_FILENAME}` verbatim, no history',
            SHAPE_OPENER_ROOTED: f'opens on `{FIRST_TALK_FILENAME}` verbatim and plays the whole conversation live to its trigger',
        }[shape]
        rows.append(f'| `{folder.name}` | {shape} | {replays} |')

    body = '\n'.join(rows) if rows else '| _(none)_ | | |'
    return f"""{'#' * level} Regressions — one folder per confirmed agent issue

{len(present)} regression(s). Each folder replays ONE issue this agent was confirmed to have, so
that the next build of this suite keeps a scenario reproducing it and a relapse is caught rather
than rediscovered. The folder name is the issue slug and is the scenario's name in the delivered
set. Its `{ISSUE_TURN_FILENAME}` is the reply the issue was confirmed on: the eval set names a check that
must fail it, and the backend replays it against that check before the suite is used.

Each folder carries AGENT EVIDENCE ONLY: the `{ROLES_FILENAME}` paragraph stating the issue, the
`{ISSUE_TURN_FILENAME}` recorded turn, and either a `{PIN_FILENAME}` cut before the issue's turn or a
`{FIRST_TALK_FILENAME}` holding the recorded opener (opener-rooted: the issue is on a later turn, on a harness
that cannot resume). The recorded turn may keep the world through it;
that grades the reply at the gate and is never staged for the build.
The build's world state, persona, scenario prose and evals are deliberately absent — every build authors them
afresh from the current pipeline, so a regression never freezes a product of the toolchain whose
defect this loop may later fix.

| Folder | Shape | Replays |
| --- | --- | --- |
{body}
"""


def _metadata_block(envelope: Any) -> dict[str, Any]:
    """The set's ``dataset_metadata``, wherever the caller cut the envelope.

    ``get_artifact_meta`` answers with a Job envelope carrying ``detail.dataset_metadata``, and
    the REST twin returns the block at the top level, so both are accepted — and so is the block
    itself, which is what an operator pastes.

    A shape none of those match is REFUSED rather than read as an empty block. The two are
    indistinguishable downstream and they mean opposite things: an empty block says every folder
    shipped, an unrecognised one says nothing was read at all.
    """
    if not isinstance(envelope, dict):
        raise Refused('the metadata is not a JSON object, so nothing in it says what the set carries.')
    detail = envelope.get('detail')
    for node in (envelope, detail if isinstance(detail, dict) else None):
        if node is not None and isinstance(node.get(METADATA_KEY), dict):
            return node[METADATA_KEY]
    if SHIPPED_KEY in envelope or ATTEMPTED_KEY in envelope:
        return envelope
    raise Refused(
        f'no {METADATA_KEY!r} in this envelope and no {SHIPPED_KEY!r} at its top level, so nothing here says what the '
        f'set carries. Pass the answer of get_artifact_meta(<the final scenario_set_id>), its {"detail"!r} block, or '
        f'the {METADATA_KEY} block itself. A shape this cannot read is never reported as "every regression shipped".'
    )


def _names(block: dict[str, Any], key: str) -> list[str]:
    value = block.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Refused(f'{key} is {type(value).__name__}, not a list of folder names — this metadata cannot be read.')
    return value


def missing_folders(envelope: Any, on_disk: list[str], *, prefix: str) -> list[dict[str, str]]:
    """The folders named ``prefix*`` this set was asked for and does not carry, each with its reason.

    Two sources, because either alone leaves a folder unaccounted for. The backend's own
    ``bundle_folders_attempted`` names the folders it was handed and could not author — the only
    record of them, since such a folder does not fail the walk — and ``ATTEMPTED_REASONS_KEY``
    says why, per folder. A folder in ``on_disk`` that appears in NEITHER backend list was never
    handed over at all (:data:`REASON_NOT_HANDED_OVER`): the bundle was uploaded before it was
    written, or the id was not passed to the final batch.

    One bundle can carry several kinds — the final batch merges regressions and trace scenarios —
    so the attempted list is split by prefix, and each kind reaches the report under its own key.
    The attempted names come first and in the backend's order, then the folders only disk knows.
    Returns ``[{folder, reason}]``.
    """
    block = _metadata_block(envelope)
    attempted = _names(block, ATTEMPTED_KEY)
    reasons = block.get(ATTEMPTED_REASONS_KEY)
    reasons = reasons if isinstance(reasons, dict) else {}
    out = [
        {'folder': name, 'reason': str(reasons.get(name) or '').strip() or REASON_UNKNOWN}
        for name in dict.fromkeys(attempted)
        if name.startswith(prefix)
    ]
    accounted = set(attempted) | set(_names(block, SHIPPED_KEY))
    out += [
        {'folder': name, 'reason': REASON_NOT_HANDED_OVER}
        for name in dict.fromkeys(on_disk)
        if name.startswith(prefix) and name not in accounted
    ]
    return out


# ── the suite build's confirmed findings ──────────────────────────────────────

#: The gate's report of agent defects, under the artifacts dir. `/analyze-simulation` appends to it
#: as the quality loop classifies failures, and arming the next build prunes its confirmed sections.
FINDINGS_FILENAME = 'agent_findings.md'

STATUS_CONFIRMED = 'confirmed'
STATUS_POTENTIAL = 'potential'
STATUS_RETIRED = 'retired'
#: A section whose `status:` line reads as none of the three. It may be a confirmed finding in a
#: spelling this parser does not know, so it is never recorded and always named in the report. A
#: section with no `status:` line at all reads as confirmed, which is the findings file's own rule.
STATUS_UNREADABLE = 'unreadable'

#: How many regressions one suite build records from its findings. Every build re-authors every
#: folder, so each one recorded here is a session every later build pays for.
AUTO_CAP = 6

#: The line a regression recorded at suite build carries in its issue paragraph. Nothing
#: machine-reads it; it tells a reader the issue was confirmed by the gate and no fix was accepted.
SUITE_BUILD_LINE = 'Recorded at suite build from a confirmed finding; no fix accepted yet.'

#: The line naming the finding a suite-build regression records. :func:`finding_slug` reads it to
#: tell this finding's folder from another one whose id cuts to the same slug.
FINDING_ID_LINE = '**Finding id:** `{finding_id}`'

#: Rank orders, most urgent first. A value outside them ranks after every known one.
SEVERITY_ORDER = ('critical', 'high', 'medium', 'low')
CONFIDENCE_ORDER = ('high', 'medium', 'low')

#: Why the plan recorded nothing at all. Each is a whole-build condition, so the report line says it
#: once rather than per finding.
NOTHING_STATIC_ONLY = 'static-only'
NOTHING_OPTED_OUT = 'opted-out'

#: Why a finding is skipped when none of its ``seen in:`` conversations holds a user turn — a
#: pipeline's run has none. A record cuts at a user turn, so this is permanent, and it is said
#: before any bench reason the same finding also meets.
NO_CONVERSATIONAL_TURN = 'no conversational turn to record'

_SECTION_HEADING = re.compile(r'^##[ \t]+(?:(\d+)[.)][ \t]*)?(.+?)[ \t]*$')
#: One `- **key:** value` bullet. The authoring skills bold the key with the colon inside or outside
#: the emphasis, so both spellings read.
_FIELD = re.compile(r'^[ \t]*[-*+][ \t]+[*_]{0,2}([A-Za-z][A-Za-z _-]*?)[ \t]*[*_]{0,2}[ \t]*:[ \t]*[*_]{0,2}[ \t]*(.*?)[ \t]*$')
_CONFIRMED = re.compile(r'^[*_`]*confirmed\b', re.IGNORECASE)
_BACKTICKED = re.compile(r'`([^`\s]+)`')
#: A scenario name opens on a letter, so a turn number or a count in the prose is never read as one.
_SCENARIO_TOKEN = re.compile(r'[A-Za-z][A-Za-z0-9_.-]*')


class Finding(NamedTuple):
    """One `## N.` section of the findings file, reduced to what a record needs."""

    number: int | None
    title: str
    status: str
    finding_id: str | None
    severity: str | None
    confidence: str | None
    policy: str | None
    seen_in: tuple[str, ...]
    run: str | None
    #: Whether the section carries its ``why it is the agent:`` line — the critic's classification,
    #: without which nothing says the failure is not the bench's.
    agent_reason: bool
    #: The section's ``trigger:`` line: the user move that made the agent fail. An opener-rooted folder states it.
    trigger: str | None = None


def _fields(section: str) -> dict[str, str]:
    """The section's bullet fields, keyed by lowercased name, continuation lines joined on."""
    fields: dict[str, str] = {}
    key: str | None = None
    for line in section.splitlines()[1:]:
        match = _FIELD.match(line)
        if match:
            key = ' '.join(match.group(1).lower().split())
            fields.setdefault(key, match.group(2))
        elif key is not None and line[:1] in (' ', '\t') and line.strip():
            fields[key] = f'{fields[key]} {line.strip()}'
        else:
            key = None
    return fields


#: What separates two scenarios in a ``seen in:`` list, outside parentheses: a comma, a semicolon,
#: or the word ``and``.
_ITEM_SEPARATOR = re.compile(r'[,;]|\s+and\s+', re.IGNORECASE)


def _top_level_items(value: str) -> list[str]:
    """``value`` split on the separators that sit outside parentheses.

    A parenthetical is the author's note on one scenario (``scenario_01 (turns 2 and 4)``), so a
    separator inside it splits nothing.
    """
    items, depth, start, index = [], 0, 0, 0
    while index < len(value):
        char = value[index]
        if char == '(':
            depth += 1
        elif char == ')' and depth > 0:
            depth -= 1
        elif depth == 0 and (separator := _ITEM_SEPARATOR.match(value, index)):
            items.append(value[start:index])
            start = index = separator.end()
            continue
        index += 1
    items.append(value[start:])
    return [item.strip() for item in items if item.strip()]


def _seen_in(value: str) -> tuple[str, ...]:
    """The scenario names a ``seen in:`` field lists, in order, each once.

    The findings file is hand-authored: a name comes bare, in backticks or in emphasis, and the
    list is joined by commas, semicolons or ``and``. Each item's name is its first token once the
    markup around it is gone; what follows it (a turn, a note) is prose.
    """
    names: list[str] = []
    for item in _top_level_items(value):
        token = _SCENARIO_TOKEN.match(item.lstrip('*_` \t'))
        name = token.group(0).rstrip('*_.-') if token else ''
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _status(section: str, fields: dict[str, str]) -> str:
    # The tier markers are read with the prune's own patterns, so a section the prune keeps as
    # potential is never read here as anything else.
    if findings_file._RETIRED.search(section):
        return STATUS_RETIRED
    if findings_file._POTENTIAL.search(section):
        return STATUS_POTENTIAL
    # A section with no status line predates the field, and the findings file's own rule reads it
    # as confirmed. A status line in a spelling nobody knows is another matter: it is never recorded.
    if 'status' not in fields or _CONFIRMED.match(fields['status']):
        return STATUS_CONFIRMED
    return STATUS_UNREADABLE


def parse_findings(text: str) -> list[Finding]:
    """Every `## ` section of an ``agent_findings.md``, in file order.

    The finding id is read with the prune's own pattern, which is written against the bytes the
    authoring skills emit. The probe's policy is its first backticked name: the probe line opens
    on the eval policy that would fail if the defect recurred, and anything after it is prose.
    """
    _, *sections = findings_file._split_sections(text)
    found = []
    for section in sections:
        heading = _SECTION_HEADING.match(section.splitlines()[0])
        if heading is None:
            continue
        fields = _fields(section)
        ident = findings_file._FINDING_ID.search(section)
        policy = _BACKTICKED.search(fields.get('probe', ''))
        seen_in = _seen_in(fields.get('seen in', ''))
        found.append(Finding(
            number=int(heading.group(1)) if heading.group(1) else None,
            title=heading.group(2),
            status=_status(section, fields),
            finding_id=ident.group(1) if ident else None,
            severity=fields.get('severity', '').strip('*_` ').lower() or None,
            confidence=fields.get('confidence', '').strip('*_` ').lower() or None,
            policy=policy.group(1) if policy else None,
            seen_in=seen_in,
            run=fields.get('run') or None,
            agent_reason=bool(fields.get('why it is the agent', '').strip()),
            trigger=fields.get('trigger', '').strip() or None,
        ))
    return found


def _cut_to(slug: str, limit: int) -> str:
    """``slug`` cut on a word boundary to at most ``limit`` characters, the way the backend cuts one."""
    if len(slug) <= limit:
        return slug
    head = slug[:limit + 1]
    return (head.rsplit('_', 1)[0] if '_' in head else slug[:limit]).strip('_')


def recorded_finding(folder: Path) -> str | None:
    """The finding id a regression folder's ``ROLES.md`` records on its ``finding id:`` line, or None.

    Read with the findings file's own pattern, so the line the suite build writes and the line a
    findings section carries are one spelling.
    """
    try:
        text = (folder / ROLES_FILENAME).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return None
    found = findings_file._FINDING_ID.search(text)
    return found.group(1) if found else None


def finding_slug(finding_id: str, regressions_dir: Path | None = None) -> str:
    """The issue slug a finding's stable id names, or a refusal when none survives.

    ``agent.shortfall_unreported`` becomes ``shortfall_unreported``: the ``agent.`` namespace is
    what every finding in the file shares, so it carries nothing. Both callers derive the slug here
    — the suite build recording the finding, and ``/simulation-fixes`` mapping the same finding
    onto the folders at acceptance — so the folder one of them finds is the folder the other wrote.
    A slug past the scenario-name limit is cut on a word boundary, the way the backend cuts one.

    Two ids can cut to one slug (``…_digital_goods_only`` and ``…_digital_goods_bundles``). With
    ``regressions_dir``, a folder under the slug whose ``finding id:`` line records ANOTHER finding
    is that finding's, not this one's: this id takes ``<slug>_2``, ``<slug>_3``… — the first name
    no other finding holds. A folder that records no id is taken as this finding's, which is how
    a fix-time record under the derived name is read. Both callers pass the
    directory, so both meet the same folders and derive the same name.
    """
    bare = finding_id[len('agent.'):] if finding_id.lower().startswith('agent.') else finding_id
    limit = SCENARIO_NAME_MAX - len(FOLDER_PREFIX)
    base = _cut_to(re.sub(r'[^a-z0-9]+', '_', bare.lower()).strip('_'), limit)
    if not base:
        raise Refused(f'finding id {finding_id!r} holds no letter or digit to name a folder by.')
    slug, number = base, 1
    while regressions_dir is not None:
        owner = recorded_finding(regressions_dir / f'{FOLDER_PREFIX}{slug}')
        if owner is None or owner == finding_id:
            break
        number += 1
        suffix = f'_{number}'
        slug = _cut_to(base, limit - len(suffix)) + suffix
    return require_slug(slug)


def find_confirmed(findings_path: Path, finding_id: str) -> Finding:
    """The confirmed finding with this id, or a refusal naming what the file holds instead."""
    try:
        text = findings_path.read_text(encoding='utf-8')
    except (OSError, ValueError) as exc:
        raise Refused(f'{findings_path} could not be read ({type(exc).__name__}: {exc}).') from exc
    matches = [f for f in parse_findings(text) if f.finding_id == finding_id]
    if not matches:
        raise Refused(f'{findings_path} carries no finding with id {finding_id!r}. Check the id against the file.')
    finding = matches[0]
    if finding.status != STATUS_CONFIRMED:
        raise Refused(
            f'finding {finding_id!r} is {finding.status}, not confirmed. Only a finding a critic reproduced against '
            'the real ground truth is recorded; a potential one waits for a scored run that catches the behaviour.'
        )
    if not finding.agent_reason:
        raise Refused(
            f'finding {finding_id!r} carries no `why it is the agent:` line, so nothing says the failure is the '
            "agent's rather than the bench's. Add the critic's reasoning to the section, or record nothing."
        )
    return finding


def covering_folders(regressions_dir: Path, finding_id: str, slug: str) -> list[str]:
    """Every folder that records this finding, the derived name first.

    The derived name is the match, and it is what the suite build writes. A folder recorded under
    another slug still covers the finding when its ``ROLES.md`` names the finding id in backticks.
    """
    named = regressions_dir / f'{FOLDER_PREFIX}{slug}'
    found = [named.name] if named.is_dir() else []
    for folder in folders(regressions_dir):
        roles = folder / ROLES_FILENAME
        try:
            if folder.name not in found and f'`{finding_id}`' in roles.read_text(encoding='utf-8'):
                found.append(folder.name)
        except (OSError, ValueError):
            continue
    return found


def covered_by(regressions_dir: Path, finding_id: str, slug: str) -> str | None:
    """The folder that already records this finding (:func:`covering_folders`' first), or None."""
    return next(iter(covering_folders(regressions_dir, finding_id, slug)), None)


#: `build_run_json.REGRESSION_MEASURES_NOTHING`: a folder whose valid replays of the scored run never measured its owner.
MEASURES_NOTHING = 'measures_nothing'


def _scored_regression_rows(workdir: Path) -> list[dict[str, Any]]:
    """The scored run's ``run.json`` ``regressions`` rows that name a folder; none when it cannot be read."""
    import stage_landed  # noqa: PLC0415 - sibling, stdlib only: THE scored-run reader

    found = stage_landed.scored_run_json(workdir)
    try:
        run = json.loads(found.read_text(encoding='utf-8')) if found is not None else None
    except (OSError, ValueError):
        return []
    rows = run.get('regressions') if isinstance(run, dict) else None
    return [r for r in rows if isinstance(r, dict) and r.get('folder')] if isinstance(rows, list) else []


def measures_nothing_folders(workdir: Path) -> set[str]:
    """The regression folders the scored run's ``run.json`` rows mark ``measures_nothing``: they pin no issue."""
    return {str(r['folder']) for r in _scored_regression_rows(workdir) if r.get('status') == MEASURES_NOTHING}


#: Why a folder's replays cannot measure its issue (:func:`hollow_reason`). Such a folder is dropped from the kept set
#: (:func:`drop`), frees its cap slot, and its finding is planned again.
HOLLOW_MEASURED_NOTHING = 'measured_nothing'
HOLLOW_RESTORE_REDUCED = 'restore_reduced'
HOLLOW_OFF_EVIDENCE = 'off_evidence'
HOLLOW_HISTORY_DOCUMENT = 'history_document'
#: Where the record step keeps each folder's pick (``$ART/regression_picks/<folder>.json``): its run, item, cut and shape.
PICKS_DIRNAME = 'regression_picks'


def hollow_reason(workdir: Path, folder: str, row: Mapping[str, Any]) -> str | None:
    """Why the scored run's ``run.json`` row says this folder cannot measure its issue, or None. First hit wins:

    1. ``measured_nothing``: the row's status is :data:`MEASURES_NOTHING`;
    2. ``restore_reduced``: every valid replay resumed with less state than the recording;
    3. only when its reading caught nothing on BOTH axes: ``off_evidence`` — its pick's cut is none of its finding's own
       turns in that run and item (:func:`own_turns`, the write guard's same reading: it was cut on another finding's
       red); or ``history_document`` — its pick resumed after a turn that handed over a document the replay could not carry.

    A folder that caught its issue is never hollow, wherever it was cut; a missing pick or store gives signal 3 nothing.
    A pick with no ``run`` (recorded before picks named it) reads its run off its transcript's path (:func:`run_of`)."""
    if row.get('status') == MEASURES_NOTHING:
        return HOLLOW_MEASURED_NOTHING
    valid, reduced = row.get('valid'), row.get('restore_reduced')
    if _is_int(valid) and valid > 0 and _is_int(reduced) and reduced >= valid:
        return HOLLOW_RESTORE_REDUCED
    reading = row.get('reading')
    if not isinstance(reading, Mapping) or not all(
            isinstance(reading.get(axis), Mapping) and reading[axis].get('caught') == 0 for axis in (CLEAR_AT_CUT, CLEAR_ANYWHERE)):
        return None
    try:
        pick = json.loads((workdir / 'artifacts' / PICKS_DIRNAME / f'{folder}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(pick, dict) or not _is_int(pick.get('cut_turn')):
        return None
    cut = pick['cut_turn']
    transcript = Path(str(pick['transcript'])) if pick.get('transcript') else None
    if transcript is not None and not transcript.is_absolute():
        transcript = workdir / transcript
    run_dir, item = run_of(transcript) if transcript is not None else (None, None)
    run, item = pick.get('run') or (run_dir.name if run_dir is not None else None), pick.get('item') or item
    fid = recorded_finding(workdir / REGRESSIONS_DIRNAME / folder)
    if run and item and fid:
        said = own_turns(workdir, run_dir, str(item), fid, run=str(run))
        if said is not None and cut not in said[1]:
            return HOLLOW_OFF_EVIDENCE
    if pick.get('shape') == SHAPE_RESUMED and transcript is not None:
        try:
            source = json.loads(transcript.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            source = None
        if isinstance(source, dict) and handed_documents_before(source, cut):
            return HOLLOW_HISTORY_DOCUMENT
    return None


def hollow_folders(workdir: Path) -> set[str]:
    """The folders the scored run's ``run.json`` marks hollow (its ``hollow`` reason, or status :data:`MEASURES_NOTHING`):
    they guard no issue and hold no cap slot."""
    return {str(r['folder']) for r in _scored_regression_rows(workdir) if r.get('hollow') or r.get('status') == MEASURES_NOTHING}


def replay_readings(workdir: Path) -> dict[str, dict[str, Any]]:
    """``{folder: {'axis', 'caught', 'measured'}}``: each folder's reading in the scored run on the axis that clears it
    (``run.json`` ``reading.axis``: ``at_cut``, its recorded turn, or ``anywhere``). A row with no such reading is left out."""
    out: dict[str, dict[str, Any]] = {}
    for row in _scored_regression_rows(workdir):
        reading = row.get('reading') if isinstance(row.get('reading'), dict) else {}
        axis = reading.get('axis')
        said = reading.get(axis) if isinstance(axis, str) else None
        if isinstance(said, dict) and _is_int(said.get('caught')) and _is_int(said.get('measured')):
            out[str(row['folder'])] = {'axis': axis, 'caught': said['caught'], 'measured': said['measured']}
    return out


def unmeasured_folders(workdir: Path) -> set[str]:
    """The folders whose scored-run replays measured nothing on their clear axis (:func:`replay_readings`): an inconclusive
    or reduced restore that ``build_run_json`` does not mark ``measures_nothing``. The planner's reader alone — what a folder
    is "kept, not yet measured" by. :func:`carried_criticals` and :func:`build_spent` read the typed status only, so a
    merely inconclusive folder still covers a critical finding and still holds its cap slot."""
    return {folder for folder, said in replay_readings(workdir).items() if said['measured'] == 0}


def held_back_folders(workdir: Path) -> set[str]:
    """The folders the build's final bundle held back (``artifacts/final_bundle.json`` ``held_back``): recorded, never
    replayed. A wording fact only — such a folder still covers its finding; none when the file cannot be read."""
    try:
        entries = json.loads((workdir / 'artifacts' / 'final_bundle.json').read_text(encoding='utf-8')).get('held_back')
    except (OSError, ValueError, AttributeError):
        return set()
    return {str(e['folder']) for e in entries if isinstance(e, dict) and e.get('folder')} if isinstance(entries, list) else set()


#: Beside the findings file: the critical confirmed findings an arm pruned that no regression folder replays, which
#: the next final draw pins (one `complication_dealt` slot each). Internal: no page, guard or verdict reads it.
CARRIED_CRITICALS_FILENAME = 'carried_criticals.json'
CARRIED_CRITICALS_SCHEMA = 'plurai-carried-criticals/1'
_TOP_KEY = re.compile(r'^([A-Za-z0-9_.-]+):[ \t]*(?:#.*)?$')
_TIER = re.compile(r'^[ \t]+verdict_tier:[ \t]*["\']?([A-Za-z_]+)')


def catastrophic_checks(policies_config: Path) -> set[str] | None:
    """The policies whose own ``verdict_tier`` is ``catastrophic``, read line by line from the block YAML (the arm runs on
    a bare interpreter, with no PyYAML); None when the file names no tier at all."""
    try:
        lines = policies_config.read_text(encoding='utf-8').splitlines()
    except (OSError, ValueError):
        return None
    tiers: dict[str, str] = {}
    name: str | None = None
    indent: int | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        top = _TOP_KEY.match(line)
        if top or line[:1] not in ' \t':
            name, indent = (top.group(1) if top else None), None
            continue
        lead = len(line) - len(line.lstrip(' \t'))
        indent = lead if indent is None else indent
        tier = _TIER.match(line)
        if name is not None and tier and lead == indent:
            tiers.setdefault(name, tier.group(1))
    return {n for n, t in tiers.items() if t == 'catastrophic'} if tiers else None


def carried_criticals(workdir: Path) -> tuple[list[dict[str, Any]], str | None]:
    """``(entries, warning)``: each confirmed build finding (not the scan's) that is CRITICAL — ``severity: critical``, or a
    probe or ``policy:`` subject whose ``verdict_tier`` is ``catastrophic`` — and that no regression folder covers (a folder the
    scored run marks ``measures_nothing`` covers nothing)."""
    import record_finding  # noqa: PLC0415

    art = workdir / 'artifacts'
    rows = record_finding.read_rows(art / 'agent_findings.jsonl')
    try:
        sections = {f.finding_id: f for f in parse_findings((art / 'agent_findings.md').read_text(encoding='utf-8')) if f.finding_id}
    except (OSError, ValueError):
        sections = {}
    config = workdir / 'policies_config.yaml'
    catastrophic = catastrophic_checks(config) if config.is_file() else set()
    warning = None
    if catastrophic is None:
        catastrophic, warning = set(), f'{config} names no readable verdict_tier, so only a `severity: critical` finding could be carried'
    regressions_dir = workdir / REGRESSIONS_DIRNAME
    nothing = hollow_folders(workdir)
    carried: list[dict[str, Any]] = []
    for fid, row in rows.items():
        if row.get('status') != STATUS_CONFIRMED or str(row.get('found_during') or '').split(' ')[0] == 'scan':
            continue
        section = sections.get(fid)
        policies = {s[len('policy:'):] for s in record_finding.subjects_of(row) if s.startswith('policy:')}
        policies |= {section.policy} if section and section.policy else set()
        if _rank(row.get('severity'), SEVERITY_ORDER) != 0 and not policies & catastrophic:
            continue
        try:
            slug = finding_slug(fid, regressions_dir)
        except Refused:
            slug = ''
        if any(name not in nothing for name in covering_folders(regressions_dir, fid, slug)):  # a folder measuring nothing covers nothing
            continue
        source = row.get('source') if isinstance(row.get('source'), dict) else {}
        carried.append({'id': fid, 'title': row.get('title'), 'trigger': section.trigger if section else None,
                        'policies': sorted(policies & catastrophic) or sorted(policies), 'from_run': source.get('run')})
    return carried, warning


def _rank(value: str | None, order: tuple[str, ...]) -> int:
    word = (value or '').split()[0] if value and value.split() else ''
    return order.index(word) if word in order else len(order)


def _severity_of(finding: Finding | None) -> int:
    """THE severity rank a plan orders and evicts by (:data:`SEVERITY_ORDER`, most urgent 0): the finding's current
    section's, read at the page's severity once a scored final run exists (:func:`at_page_severity`). An unknown or
    missing severity ranks after every known one (``len(SEVERITY_ORDER)``)."""
    return _rank(finding.severity if finding is not None else None, SEVERITY_ORDER)


def at_page_severity(findings: Sequence[Finding], workdir: Path) -> list[Finding]:
    """``findings`` each at the severity the page shows it at (``scored_findings.page_severity``) once a scored final run
    exists — the store keeps the written one, the page its findings' derived one — so the cap ranks and evicts by what the
    customer reads; unchanged without a scored final run, or when that reading fails (said on stderr)."""
    import scored_findings  # noqa: PLC0415 - THE page projection

    try:
        page = scored_findings.page_severity(workdir)
    except (OSError, ValueError) as exc:
        print(f'regressions: the page severities could not be read ({type(exc).__name__}: {exc}) — ranked by the written ones', file=sys.stderr)
        page = {}
    return [f._replace(severity=page[f.finding_id]) if f.finding_id in page else f for f in findings]


def _finding_row(finding: Finding) -> dict[str, Any]:
    return {
        'finding_id': finding.finding_id,
        'number': finding.number,
        'title': finding.title,
        'severity': finding.severity,
        'confidence': finding.confidence,
        'policy': finding.policy,
        'seen_in': list(finding.seen_in),
        'trigger': finding.trigger,
    }


#: Why a finding has no regression from a plan, typed, so the fix plan's line is chosen from it (:func:`fixes_line`), never
#: re-derived from the operator's reason text. Every skipped row carries one as ``kind``.
SKIP_UNREPLAYABLE = 'unreplayable'
SKIP_BLOCKED = 'blocked'
SKIP_COVERED = 'covered'
SKIP_CANNOT_MEASURE = 'cannot_measure'
SKIP_OVER_CAP = 'over_cap'
SKIP_ELSEWHERE = 'elsewhere'
SKIP_NOT_REPRODUCED = 'not_reproduced'
SKIP_DEFERRED = 'deferred'
SKIP_UNRECORDABLE = 'unrecordable'
#: ``scored_findings.finding_outcome``'s typed reasons no failed check carries the finding in the run.
SKIP_READ_ONLY = 'read_only'
SKIP_REACHED_HANDLED = 'reached_handled'
SKIP_NOT_TESTED = 'not_tested'
SKIP_NOT_SET_UP = 'not_set_up'
#: Its folder replayed in the scored run and measured nothing (:func:`measures_nothing_folders`, or nothing on its clear
#: axis: :func:`unmeasured_folders`): kept, never re-recorded.
SKIP_UNMEASURED = 'unmeasured'
#: Every conversation it could be recorded from opened with a handed-over document, and its replay would start from that
#: opener (:class:`OpenerDocument`): the write step would refuse it, so the plan never promises it.
SKIP_OPENER_DOCUMENT = 'opener_document'
#: Every conversation it could be recorded from resumes after a turn that handed over a document (:class:`HistoryDocument`).
SKIP_HISTORY_DOCUMENT = 'history_document'
#: ``scored_findings.finding_outcome`` (``READ_ONLY`` / ``REACHED_HANDLED`` / ``NOT_TESTED`` / ``NOT_SET_UP``) → the skip
#: kind of a finding no check of the run can be cut for: what turns "not reproduced" into what the run actually showed (its
#: words: :data:`FIXES_LINES`).
OUTCOME_KINDS = {'read_only': SKIP_READ_ONLY, 'reached_handled': SKIP_REACHED_HANDLED, 'not_tested': SKIP_NOT_TESTED,
                 'not_set_up': SKIP_NOT_SET_UP}

#: The reason a section with no typed row and no ``seen in:`` scenario is skipped: the record chooses among those only.
NO_SEEN_IN = 'its `seen in:` line names no scenario to record the conversation from'

#: The fix plan's bound (``auto-plan --acceptance``): each record is a pick, a write, a conversion and one backend confirm,
#: and each folder kept replays in every later run, so one fix plan records at most :data:`AUTO_CAP`.
OVER_BOUND_LINE = f'waits — a fix plan records at most {AUTO_CAP} replays; the next `/simulation-fixes` records it'
#: A build plan's ``over_cap`` row: the build keeps at most :data:`AUTO_CAP`, filled by severity across the build.
#: ``/simulation-fixes`` has its own cap and refusals, so the line says it CAN record it, never that it will.
BUILD_CAP_LINE = f'waits — this build keeps at most {AUTO_CAP} replays, highest severity first; `/simulation-fixes` can record it when you accept its fix'
#: A fix plan's record row (:func:`fixes_line`): the folder is written when its fix is accepted. One state, one wording,
#: on every surface that names it (fixes.md, the chat, the page's over-cap rows).
ON_ACCEPTANCE_LINE = 'recorded on acceptance'
#: A build plan row whose covering folder gives its place to a more severe issue's (``evicted``).
EVICTED_LINE = (f'waits — its replay was removed to make room for a more severe issue (this build keeps at most {AUTO_CAP}); '
                '`/simulation-fixes` can record it again when you accept its fix')
#: The customer's sentence for a finding whose earlier folder was dropped (``removed``: :data:`DROPPED_FILENAME`'s kind).
REMOVED_NOTES = {
    'hollow': 'its earlier replay was removed because it could not reach the moment the issue happened',
    'evicted': 'its earlier replay was removed to make room for a more severe issue',
}

#: Where every ``auto-report`` of one suite build adds the folders its record step wrote, beside the findings file.
#: ``auto-plan`` counts the ones still on disk against the build's cap, whatever plan file wrote them: a gate exit that
#: re-plans after removing its plan file still records at most :data:`AUTO_CAP` in all. A new build's arm clears it
#: (``open_epoch.py``, with the findings prune); a resumed build keeps it.
BUILD_LEDGER_FILENAME = 'auto_regressions.build.json'
BUILD_LEDGER_SCHEMA = 'auto-regressions-build/1'


class RunReading:
    """A run's own typed record of whose red each conversation shows, read once per plan.

    ``items`` is every conversation over every trial (:func:`run_conversations`); ``links`` is its finding attribution
    (``scored_findings.ATTRIBUTION``, its standing links: ``scored_findings.standing_links``): per violation unit
    ``(item, policy, turn)``, the one finding it shows; the audit's current violation units give a whole-conversation
    red's ``quote_turn``. No file is required: a run with none of them has no typed record, and its findings plan from
    their names."""

    def __init__(self, run_dir: Path) -> None:
        import scored_findings  # noqa: PLC0415 - sibling: THE reader of the attribution and the audit units

        self.run_dir, self.name = run_dir, run_dir.name
        self.items = run_conversations(run_dir)
        self.doc = scored_findings.read_attribution(run_dir) or {}
        self._units: dict[Any, dict] = scored_findings.current_units(scored_findings._audit(run_dir), self.items)  # noqa: SLF001
        self.standing = scored_findings.standing_links(self.doc, self._units)
        self.links: dict[tuple[str, str], list[tuple[Any, str]]] = {}
        for (item, policy, _), link in self.standing.items():
            self.links.setdefault((item, policy), []).append((link.get('turn'), str(link.get('to') or '')))
        self.linked = {to for entries in self.links.values() for _, to in entries}

    def candidates(self, finding: Finding) -> list[str]:
        """The checks a replay of the finding may be owned by: its probe, then each other check a standing link ties to
        it — one with a turn-linked red first, then one whose red (its cut, :func:`_own_pool`) falls at or before the
        finding's earliest linked turn (a later red can be a knock-on of it: a check that the conversation contradicts
        itself fails only because the first answer was wrong), then the one the run ties to the fewest distinct findings
        (a check carrying only this finding's reds is never failed on replay by another's), then by link count, then by
        name — never a scenario-goal grader, never a run record. With no link naming a turn, no red is ordered by turn."""
        import scored_findings  # noqa: PLC0415

        counts: dict[str, int] = {}
        turned: set[str] = set()
        linked_turns: list[int] = []
        for (_, policy), entries in self.links.items():
            for turn, to in entries:
                if to != finding.finding_id:
                    continue
                turns = [t for t in (turn if isinstance(turn, list) else [turn]) if _is_int(t)]
                linked_turns += turns
                # A run record (`scored_findings.TOOL_PREFIX`) is no check: a replay never owns one.
                if policy not in (self.doc.get('target_graders') or ()) and not policy.startswith(scored_findings.TOOL_PREFIX):
                    counts[policy] = counts.get(policy, 0) + 1
                    turned |= {policy} if turns else set()
        owners = {p: len({to for (_, q), entries in self.links.items() if q == p for _, to in entries}) for p in counts}
        earliest = min(linked_turns, default=None)
        later = {p: earliest is not None and min(_own_pool(self, finding, None, p).cuts.values(), default=earliest + 1) > earliest
                 for p in set(counts) - {finding.policy}}
        rest = sorted(later, key=lambda p: (p not in turned, later[p], owners[p], -counts[p], p))
        return [*([finding.policy] if finding.policy else []), *rest]

    def outcome(self, finding_id: str) -> str | None:
        """``scored_findings.finding_outcome`` of this run: the page's and the planner's one reading of a finding."""
        import scored_findings  # noqa: PLC0415

        return scored_findings.finding_outcome(self.doc, self.standing, finding_id)

    def quote_turn(self, item: str, policy: str) -> int | None:
        """The audited quote turn of the conversation's whole-conversation violation of ``policy``, or None."""
        import scored_findings  # noqa: PLC0415

        turn = (self._units.get((item, policy, scored_findings._turn_key(None))) or {}).get('quote_turn')  # noqa: SLF001
        return turn if _is_int(turn) else None


#: Where a replay reading's owner came from (:func:`replay_reading`): a check this run's — or, stored in the folder's
#: ``replays.json`` (:func:`store_linked_owners`), an earlier run's — standing links tie to the folder's own finding on its
#: own replays; else the eval set's ledger; else the folder's ROLES.md probe (the ledger read and naming none, or never read).
OWNER_LINKED, OWNER_LEDGER, OWNER_PROBE_UNOWNED, OWNER_PROBE_NO_LEDGER = 'linked', 'ledger', 'probe_unowned', 'probe_no_ledger'
#: Where ``replays.json`` keeps the linked owner (:func:`store_linked_owners`) — beside, never over, the record-time
#: ``owner`` its ``k`` / ``n`` / rate / band were measured on.
LINKED_OWNER_KEY, LINKED_RUN_KEY = 'linked_owner', 'linked_run'


def _linked_checks(run: RunReading, scenario: str, finding_id: str | None) -> list[str]:
    """The checks ``run``'s standing links tie to ``finding_id`` on ``scenario``'s own replays, most specific first: one with a
    turn-linked red, then the one the run ties to the fewest distinct findings, then by link count, then by name (the
    order of :meth:`RunReading.candidates`) — never a scenario-goal grader, never a run record."""
    import red_cells  # noqa: PLC0415
    import scored_findings  # noqa: PLC0415

    graders = set(run.doc.get('target_graders') or ())
    counts: dict[str, int] = {}
    turned: set[str] = set()
    for (item, policy), entries in run.links.items():
        if not finding_id or red_cells.item_scenario(item) != scenario or policy in graders or policy.startswith(scored_findings.TOOL_PREFIX):
            continue
        for turn, to in entries:
            if to == finding_id:
                counts[policy] = counts.get(policy, 0) + 1
                turned |= {policy} if turn is not None else set()
    spread = {p: len({to for (_, q), entries in run.links.items() if q == p for _, to in entries}) for p in counts}
    return sorted(counts, key=lambda p: (p not in turned, spread[p], -counts[p], p))


def linked_owner(run: RunReading | None, folder: Path, *, prefer: Collection[str | None] = ()) -> str | None:
    """The check that owns ``folder``'s replays by THIS run's typed record: the first of ``prefer`` (the stored linked owner,
    then the probe) among :func:`_linked_checks`, else its most specific one; None with no run or no such link."""
    checks = _linked_checks(run, folder.name, recorded_finding(folder)) if run is not None else []
    return next((p for p in prefer if p in checks), checks[0] if checks else None)


def _replay_label(caught: int, measured: int, sdk_label: Any) -> str:
    """The label one axis of a reading earns: nothing measured is ``not_applicable`` (the SDK's ``deferred`` or ``insufficient``
    kept), one measured replay ``insufficient``, else consistent pass / fail or inconsistent."""
    if not measured:
        return sdk_label if sdk_label in ('deferred', 'insufficient') else 'not_applicable'
    if measured < 2:
        return 'insufficient'
    return 'consistent_pass' if not caught else 'consistent_fail' if caught == measured else 'inconsistent'


def replay_reading(entry: Mapping[str, Any], *, folder: Path | None, dirs: Collection[str], owners: Mapping[str, str], ledger_read: bool,
                   run: RunReading | None) -> dict[str, Any]:
    """THE reading of one regression folder's replays: every surface that says "caught k of N" says this one.

    ``entry`` is the folder's SDK ``regression_replays`` entry over the population the caller chose — the run's own,
    audited replays when it carried the folder (``run`` is then its :class:`RunReading`, its standing links on hand), else
    the regression-only step's (``run`` None: nothing audited or attributed them); ``dirs`` lists that population's trial
    dirs in ``by_trial`` order. The owner is :func:`linked_owner`, else the link a run before stored in ``replays.json``,
    else the ledger's, else the ROLES.md probe. Per axis: *measured* is a failed or passed replay less the passes whose
    premise never arose (``trigger_absent``) and, AT THE CUT of a whole replay, the replays that resumed with less state
    than the recording (``restore_reduced``: a single hop already reads those inconclusive); *caught* is a failed replay
    unless a standing link on it names another finding or ``none`` — measured, never caught. ``by_trial`` missing (a cut
    block), every failed replay is caught."""
    text = (folder / ROLES_FILENAME).read_text(encoding='utf-8') if folder is not None and (folder / ROLES_FILENAME).is_file() else ''
    probe = probe_policy(text) if text else None
    stored = confirm_record(folder) if folder is not None else None
    stored_link = stored.get(LINKED_OWNER_KEY) if stored else None
    linked = linked_owner(run, folder, prefer=(stored_link, probe)) if folder is not None else None
    finding = recorded_finding(folder) if folder is not None else None
    if linked or stored_link:
        owner, source = linked or stored_link, OWNER_LINKED
    elif folder is not None and owners.get(folder.name):
        owner, source = owners[folder.name], OWNER_LEDGER
    else:
        owner, source = probe, (OWNER_PROBE_UNOWNED if ledger_read else OWNER_PROBE_NO_LEDGER) if probe else None
    cells = (entry.get('policies') or {}).get(owner) if owner else None
    items = [f'{folder.name}/{sub}' if sub else folder.name for sub in dirs] if folder is not None else []
    whole = entry.get('replay_mode') != SINGLE_HOP
    # The axis a clear verdict reads: the one the folder pins (`repin`), else the SDK's, else the cut.
    axis = pinned_axis(stored) or (entry.get('clear_axis') if entry.get('clear_axis') in (CLEAR_AT_CUT, CLEAR_ANYWHERE) else CLEAR_AT_CUT)
    reading: dict[str, Any] = {'owner': owner, 'owner_source': source, 'axis': axis, 'audited': run is not None,
                               'attributed': run is not None and folder is not None and any(i.partition('/')[0] == folder.name for i, _ in run.links)}
    if source == OWNER_LINKED and not linked:
        reading['owner_run'] = stored.get(LINKED_RUN_KEY) if stored else None
    for axis in (CLEAR_AT_CUT, CLEAR_ANYWHERE):
        cell = (cells or {}).get(axis) if isinstance(cells, dict) else None
        cell = cell if isinstance(cell, dict) else {}
        failed, passed = int(cell.get('failed') or 0), int(cell.get('passed') or 0)
        measured = failed + passed - int(cell.get('trigger_absent') or 0)
        by = cell.get('by_trial')
        caught = failed
        if run is not None and isinstance(by, list) and len(by) == len(items):
            caught = sum(1 for item, outcome in zip(items, by) if outcome == 'failed'
                         and (not (links := run.links.get((item, owner), [])) or any(to == finding for _, to in links)))
        # A red linked to another finding (or `none`) measured another issue: neither caught nor clean.
        reduced = (int(entry.get('restore_reduced') or 0) if axis == CLEAR_AT_CUT and whole else 0) + (failed - caught)
        measured = max(0, measured - reduced)
        caught = min(caught, measured)
        reading[axis] = {'caught': caught, 'measured': measured, 'drawn': int(entry.get('valid') or 0) + len(entry.get('invalid') or []),
                         'label': _replay_label(caught, measured, cell.get('label'))}
    return reading


def scored_reading(run_dir: Path, scenario: str, workdir: Path) -> dict[str, Any] | None:
    """:func:`replay_reading` of one folder over ``run_dir``'s OWN replays, read exactly as ``build_run_json.regression_rows``
    reads them (the run's block refolded over its corrected verdicts, its trial dirs, the ledger, its standing links) —
    for a reader that runs before ``run.json`` is rebuilt. None when the run did not replay the folder."""
    run_scripts = str(Path(__file__).resolve().parent.parent / 'skills' / 'simulation-run' / 'scripts')
    if run_scripts not in sys.path:
        sys.path.insert(0, run_scripts)
    import build_run_json  # noqa: PLC0415 - the run.json builder: the one block reader and refold

    entry = ((build_run_json.regression_replays_block(run_dir, None) or {}).get('regressions') or {}).get(scenario)
    if not isinstance(entry, dict):
        return None
    owners, ledger_read = build_run_json._regression_owners(workdir)  # noqa: SLF001 - its one owners reader
    return replay_reading(entry, folder=workdir / REGRESSIONS_DIRNAME / scenario, dirs=build_run_json.trial_dirs(run_dir), owners=owners,
                          ledger_read=ledger_read, run=RunReading(run_dir))


def store_linked_owners(regressions_dir: Path, run: RunReading) -> list[str]:
    """Write into each replayed folder's ``replays.json`` the check ``run``'s standing links tie to its own finding on its own
    replays (:func:`linked_owner`) as :data:`LINKED_OWNER_KEY` + :data:`LINKED_RUN_KEY` — the record-time ``owner`` and the
    rate measured on it are left as they are — unless the stored linked owner is one of those checks (an owner never moves
    while its link holds). A later run that leaves no red to link — a fixed agent —
    still reads that check, never a probe that passed before any fix. A folder with no confirm record is left as is."""
    written: list[str] = []
    for scenario in sorted({item.partition('/')[0] for item, _ in run.links if item.startswith(FOLDER_PREFIX)}):
        folder = regressions_dir / scenario
        record = confirm_record(folder)
        if record is None:
            continue
        stored = record.get(LINKED_OWNER_KEY)
        owner = linked_owner(run, folder, prefer=(stored,))
        if owner is None or owner == stored:
            continue
        record.update({LINKED_OWNER_KEY: owner, LINKED_RUN_KEY: run.name})
        (folder / REPLAYS_FILENAME).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        written.append(scenario)
    return written


class Pool(NamedTuple):
    """The conversations one finding may be recorded from in one run (:func:`finding_pool`)."""

    #: ``{item: directory}``, trial 1 first.
    items: dict[str, Path]
    #: The finding's own cut in each item, when its typed record names one.
    cuts: dict[str, int]
    #: The items whose cut is the audit's quote turn of a whole-conversation red (:data:`CANNOT_MEASURE_AT_CUT`).
    quote_cut: tuple[str, ...]
    #: Read off the run's attribution links, rather than off scenario names.
    typed: bool
    #: Why the pool is empty, and its :data:`SKIP_UNRECORDABLE`-style kind.
    reason: str | None = None
    kind: str | None = None


def _run_named(value: str | None) -> str:
    """The run folder a section's ``run:`` line names (``runs/qual-1``, bare or in backticks), or ''."""
    words = (value or '').split()
    return words[0].strip('`*_').rstrip('/').rsplit('/', 1)[-1] if words else ''


def _judged_turns(directory: Path, policy: str) -> list[int] | None:
    """The turns the policy's current verdict names violated there; None when its verdicts cannot be read."""
    try:
        return violated_turns(read_transcript(directory / TRANSCRIPT_FILENAME), policy)
    except Refused:
        return None


def _own_pool(reading: RunReading, finding: Finding, scored_items: Collection[str] | None, policy: str | None) -> Pool:
    """The conversations where this finding OWNS its cell of ``policy`` (its probe, or another check its links name) —
    an attribution link ``(item, policy, turn) → finding`` — each cut at its own first linked turn the verdict names,
    never at the check's first red, which may be another finding's. An own whole-conversation cell (``turn: null``) is
    cut at the judge's turn, else at its audited quote turn (:data:`CANNOT_MEASURE_AT_CUT` decides the shape).
    ``scored_items`` (the row's ``scored_run.items``) bounds them."""
    import red_cells  # noqa: PLC0415

    fid, policy = finding.finding_id or '', policy or ''
    allowed = None if scored_items is None else {red_cells.normalize_item(str(item)) for item in scored_items}
    items: dict[str, Path] = {}
    cuts: dict[str, int] = {}
    quoted: list[str] = []
    whole_without_turn = False
    for item, directory in reading.items.items():
        turns = [turn for turn, to in reading.links.get((item, policy), ()) if to == fid]
        if not turns or (allowed is not None and item not in allowed):
            continue
        ints = sorted({t for turn in turns for t in (turn if isinstance(turn, list) else [turn]) if _is_int(t)})
        if ints:
            judged = _judged_turns(directory, policy)
            own = [t for t in ints if not judged or t in judged]
            if not own:
                continue  # its own turns are not the ones the verdict names: the replay's owner would not fail them
            cuts[item] = own[0]
        elif any(turn is None for turn in turns):
            judged = _judged_turns(directory, policy)
            quote = reading.quote_turn(item, policy) if not judged else None
            if judged:
                cuts[item] = judged[0]
            elif quote is not None:
                cuts[item] = quote
                quoted.append(item)
            else:
                whole_without_turn = True
                continue
        else:
            continue
        items[item] = directory
    if items:
        return Pool(items, cuts, tuple(quoted), typed=True)
    if whole_without_turn:
        return Pool({}, {}, (), typed=True, reason=CANNOT_MEASURE_AT_CUT, kind=SKIP_CANNOT_MEASURE)
    return Pool({}, {}, (), typed=True, reason=f'this run did not reproduce it on its probe check `{policy}`', kind=SKIP_NOT_REPRODUCED)


def _named_pool(reading: RunReading, names: Collection[str]) -> Pool:
    """Every conversation, over every trial, of the scenarios ``names`` spells (:func:`_names_scenario`)."""
    if not names:
        return Pool({}, {}, (), typed=False, reason=NO_SEEN_IN, kind=SKIP_UNRECORDABLE)
    items = {item: d for item, d in reading.items.items() if any(_names_scenario(item.partition('/')[0], name) for name in names)}
    if not items:
        return Pool({}, {}, (), typed=False, reason='none of the scenarios it was seen in is in this run', kind=SKIP_ELSEWHERE)
    return Pool(items, {}, (), typed=False)


#: Why a finding's own conversations hold no cut: its check is red there only on other turns than its evidence quotes.
OFF_EVIDENCE_REDS = "its check's reds in this run are on other turns than its evidence"


def _evidence_pool(reading: RunReading, row: Mapping[str, Any] | None, policy: str | None, names: Collection[str]) -> Pool:
    """The conversations the finding's typed evidence quotes in this run (``row['evidence']``), each cut at its own turn —
    never at the check's first red, which may be another finding's. Where the check names violated turns there, the cut is
    the first evidence turn among them (an item with none is left out); where it names none (it grades the conversation
    whole), the cut is the first evidence turn and the item is a quote cut (:data:`CANNOT_MEASURE_AT_CUT` decides its
    shape). A row with no evidence for this run (a legacy row, a hand-written section) plans from ``names``."""
    evidence = evidence_turns(row, reading.name)
    if not evidence:
        return _named_pool(reading, names)
    items: dict[str, Path] = {}
    cuts: dict[str, int] = {}
    quoted: list[str] = []
    present = False
    for item, turns in sorted(evidence.items(), key=lambda entry: item_order(entry[0])):
        directory = reading.items.get(item)
        if directory is None:
            continue
        present = True
        judged = _judged_turns(directory, policy) if policy else []
        if judged:
            own = [turn for turn in turns if turn in judged]
            if not own:
                continue  # its own turns are not the ones the verdict names: the replay's owner would not fail them
            cuts[item] = own[0]
        else:
            cuts[item] = turns[0]
            if judged is not None:
                quoted.append(item)
        items[item] = directory
    if items:
        return Pool(items, cuts, tuple(quoted), typed=False)
    if not present:
        return Pool({}, {}, (), typed=False, reason='none of the conversations it was seen in is in this run', kind=SKIP_ELSEWHERE)
    return Pool({}, {}, (), typed=False, reason=OFF_EVIDENCE_REDS, kind=SKIP_NOT_REPRODUCED)


def finding_pool(reading: RunReading, finding: Finding, row: Mapping[str, Any] | None, policy: str | None = None) -> Pool:
    """The one candidate source of the plan and the pick: which conversations of ``reading``'s run may record the
    finding, and where each is cut. Rules, in order:

    1. Its typed row's ``scored_run`` is this run — or, whatever its row says, the run's attribution links it: the
       items where it owns its probe cell (:func:`_own_pool`), over every trial, regression folders included.
    2. Its origin is this run (the row's ``source.run``, else the section's ``run:`` line): the conversations its typed
       ``evidence`` quotes here, each cut at its own turn (:func:`_evidence_pool`); with none, ``source.scenarios``, else
       its ``seen in:`` names, over every trial. These are this run's own names (the gate exit's path).
    3. An origin or a ``scored_run`` naming ANOTHER run: none — a scenario name never crosses runs (a qualification
       ``scenario_01`` is not the final set's, and a ``regression_*`` folder replays in every build).
    4. A hand-written section with no typed row: its ``seen in:`` names, over every trial (its evidence, if a row has some).

    ``policy`` is the check the cells are read on (rules 1): the probe when None.
    """
    row = row if isinstance(row, Mapping) else {}
    scored = row.get('scored_run') if isinstance(row.get('scored_run'), Mapping) else {}
    source = row.get('source') if isinstance(row.get('source'), Mapping) else {}
    origin = str(source.get('run') or '') or _run_named(finding.run)
    policy = policy or finding.policy
    if scored.get('run') == reading.name:
        return _own_pool(reading, finding, [str(item) for item in scored.get('items') or []], policy)
    if finding.finding_id in reading.linked:
        return _own_pool(reading, finding, None, policy)  # this run's attribution links it: its own cells, never "seen in another run"
    if origin == reading.name:
        return _evidence_pool(reading, row, policy, [str(name) for name in source.get('scenarios') or []] or finding.seen_in)
    if origin or scored.get('run'):
        return Pool({}, {}, (), typed=True, reason=f'seen in runs/{origin or scored.get("run")}; this run did not reproduce it', kind=SKIP_ELSEWHERE)
    return _evidence_pool(reading, row, policy, finding.seen_in)


def _without_a_turn(dirs: Collection[Path]) -> bool:
    """Whether the finding's conversations are in the run and none of them holds a user turn.

    Read from the live transcript: a re-score replaces verdicts, never turns, so an unreadable one
    beside a scenario does not stop the plan."""
    return bool(dirs) and all(user_turn_count(_read_json(d / TRANSCRIPT_FILENAME)) == 0 for d in dirs)


def _said_scenario(item: str) -> str:
    """A conversation's scenario as the customer reads it: ``scenario_02``, never the run directory's set id or its trial."""
    import scored_findings  # noqa: PLC0415

    return scored_findings._FOLDER_ID.sub(lambda m: m.group('name'), item.partition('/')[0])  # noqa: SLF001


def _uncuttable(pool: Pool, policy: str, bench_scenarios: Collection[str], bench_cells: Collection[str]) -> tuple[str, str, dict[str, Any] | None] | None:
    """``(reason, kind, blocked_by)`` when no conversation of the pool is one the record's ``pick`` can cut from, or
    ``None`` when one is: unbenched for ``policy`` (:func:`bench_touches`), with that policy violated in the run."""
    def touched(name: str) -> bool:
        return bench_touches(name, policy, bench_scenarios, bench_cells, flag='--bench-cell')

    candidates, unreadable = _violating(list(pool.items.items()), policy, touched)
    if any(not touched(name) for name, _ in candidates):
        return None
    if unreadable:
        return (f'the re-score beside {", ".join(unreadable)} could not be read; repair or remove it to record from that scenario',
                SKIP_UNRECORDABLE, None)
    if candidates:
        whole = {_said_scenario(name) for name, _ in candidates if any(_names_scenario(name.partition('/')[0], s) for s in bench_scenarios)}
        cells = {_said_scenario(name) for name, _ in candidates} - whole
        return (f'every scenario it was seen in with `{policy}` violated is one a bench issue the gate left open touches', SKIP_BLOCKED,
                {'scenarios': sorted(whole), 'cells': sorted(f'{name}:{policy}' for name in cells)})
    if pool.typed:
        return f'no conversation where it owns its check `{policy}` has it violated in the run', SKIP_NOT_REPRODUCED, None
    return f'no scenario it was seen in has `{policy}` violated in the run', SKIP_NOT_REPRODUCED, None


def plan_auto_regressions(
    findings_text: str | None,
    regressions_dir: Path,
    *,
    run_dir: Path | None,
    bench_scenarios: Collection[str] = (),
    bench_cells: Collection[str] = (),
    bench_policies: Collection[str] = (),
    harness_wide: Collection[str] = (),
    accepted_harness: Collection[str] = (),
    cap: int = AUTO_CAP,
    spent: int = 0,
    opted_out: bool = False,
    static_only: bool = False,
    rows: Mapping[str, Mapping[str, Any]] | None = None,
    acceptance: bool = False,
    incumbents: Collection[str] = (),
) -> dict[str, Any]:
    """Which confirmed findings the suite build records at gate exit, and why each other one is not.

    ``run_dir`` is the run the record cuts from. Every finding of an agent whose bench manifest declares it a
    pipeline (:func:`runs_without_a_conversation`), and one whose conversations there hold no user turn, is
    skipped as :data:`NO_CONVERSATIONAL_TURN`, the cause no later run of the same agent removes, so it is named ahead of
    any bench reason. ``spent`` is the folders this build already recorded (its ledger, :data:`BUILD_LEDGER_FILENAME`):
    they count against ``cap``, so a build records at most ``cap`` in all.

    ``rows`` is the findings store (``agent_findings.jsonl``): each finding's conversations are :func:`finding_pool`'s,
    over every trial — its own probe cells in the run its typed record names, each cut at its own turn — and the plan
    runs the record step's own :func:`choose` on them, so a row it records is one the pick records, and each record row
    carries ``items`` and ``cut_from`` (``{item: turn}``) for ``pick --plan``. The plan names its ``run``.

    The gate's report stands in for ``fixes.md`` in the clean-run gate: a finding whose policy a
    bench issue touches is skipped, and an open harness-wide one skips them all, since it reaches
    every conversation. ``bench_scenarios`` drops a whole scenario (its seed, premise or ground truth is wrong);
    ``bench_cells`` (``<scenario>:<policy>``, or ``<scenario>/trial_<N>:<policy>`` for one trial) drops that policy's
    verdict there only, so a finding on another policy seen in the same scenario still records. ``accepted_harness``
    names harness gaps the gate ACCEPTED in a landing it ruled
    valid: they are listed in the bench note and skip nothing. The clean re-run an open issue waits for
    never comes for an accepted gap, so treating one as open would block every record for good; what an
    accepted gap is known to reach arrives as ``bench_scenarios`` / ``bench_policies``. A bench issue on some of its scenarios is not a skip: ``bench.scenarios``
    goes to the record step, whose ``pick`` cuts from the finding's other conversations. A finding is
    skipped when none of those others has its probe policy violated in the run — the conversations
    ``pick`` accepts (:func:`violating_candidates`) — so it takes no place under the cap. The rest are
    ranked by severity, then confidence, then file order, and the first ``cap`` not already
    covered by a folder are recorded. A finding with no probe policy is skipped too: the policy is
    what chooses the conversation and the cut, and a guessed cut replays a conversation the issue
    may not be in. So is a hand-written one whose ``seen in:`` names no scenario, since the record chooses only
    among the scenarios the finding names. Every skipped row carries its typed ``kind`` (``SKIP_*``) and a blocked one
    its ``blocked_by`` (``scenarios`` / ``cells`` / ``policy`` / ``harness_wide``), which :func:`fixes_line` words.

    The record rows are spread across source conversations (D3): a finding with more than one source scenario tries,
    within each check, the sources no earlier record row of this plan cut and that are no other finding's only source,
    then the unused ones, then its whole pool (whose reason is reported when nothing records). Each record row names its
    ``source`` scenario, and its ``items`` are the pool it was chosen from, so ``pick --plan`` reproduces the choice.

    ``acceptance`` plans ``/simulation-fixes``' records instead: no build ledger — every recordable finding is a
    ``record`` row, in rank order, and each row past the first ``cap`` is marked ``past_cap``: the fix plan says it waits
    (:data:`OVER_BOUND_LINE`) and its pick refuses it (:func:`pick_from_plan`), so what fixes.md says is what is recorded.

    The build's cap is filled by severity across the build: ``incumbents`` are the folders ``spent`` counts (:func:`build_incumbents`).
    When the cap is full, a finding evicts the weakest incumbent of STRICTLY lower severity (:func:`_severity_of`; then
    lower confidence, then one that caught nothing in the scored run, then the latest recorded): its record row names it
    under ``evicts``, and the evicted finding's ``covered`` row becomes ``over_cap`` with ``evicted`` / ``evicted_by``.
    Equal severity never evicts, an unknown-severity finding evicts nothing, and an unknown-severity incumbent goes first.
    The folder moves out only once the evicting folder is written (``auto-report``). A finding whose earlier folder was
    dropped after this run (:func:`dropped_records`) carries ``removed``, so its line says so.
    """
    cells = sorted({'{}:{}'.format(*parse_cell(cell, flag='--bench-cell')) for cell in bench_cells})
    bench = {
        'scenarios': sorted(set(bench_scenarios)),
        'cells': cells,
        'policies': sorted(set(bench_policies)),
        'harness_wide': list(dict.fromkeys(harness_wide)),
        'accepted_harness': list(dict.fromkeys(accepted_harness)),
    }
    plan: dict[str, Any] = {
        'cap': cap, 'bench': bench, 'record': [], 'skipped': [], 'nothing_because': None, **({'spent': spent} if spent else {}),
        # The workdir's harness-wide resume gap (``regressions_dir`` sits in the workdir): the report line states it.
        'harness_resume': resume_unsupported(regressions_dir.parent),
        # The run the rows' items and cuts are this run's: `pick --plan` refuses any other.
        **({'run': run_dir.name} if run_dir is not None else {}),
        **({'acceptance': True} if acceptance else {}),
    }
    if static_only:
        plan['nothing_because'] = NOTHING_STATIC_ONLY
        return plan
    if opted_out:
        plan['nothing_because'] = NOTHING_OPTED_OUT
        return plan

    findings = at_page_severity(parse_findings(findings_text or ''), regressions_dir.parent)
    for finding in findings:
        if finding.status == STATUS_UNREADABLE:
            plan['skipped'].append({**_finding_row(finding), 'reason': 'its `status:` line does not read `confirmed`', 'kind': SKIP_UNRECORDABLE})
    confirmed = [f for f in findings if f.status == STATUS_CONFIRMED]
    confirmed.sort(key=lambda f: (
        _severity_of(f),
        _rank(f.confidence, CONFIDENCE_ORDER),
        f.number if f.number is not None else sys.maxsize,
    ))

    taken: dict[str, str] = {}
    workdir = regressions_dir.parent
    pipeline = runs_without_a_conversation(workdir)
    reading = RunReading(run_dir) if run_dir is not None else None
    resume_gap = plan['harness_resume']
    # A folder whose replays measured nothing on its clear axis, or that is hollow and not yet dropped, guards nothing yet
    # (kept, worded "not yet measured"); once dropped it is off disk and its finding plans again.
    nothing, held = hollow_folders(workdir) | unmeasured_folders(workdir), held_back_folders(workdir)
    sections = {f.finding_id: f for f in findings if f.finding_id}
    evicted: dict[str, str] = {}
    removed = {} if run_dir is None else {
        str(r['finding_id']): {'folder': r.get('folder'), 'kind': r.get('kind')}
        for r in dropped_records(workdir / 'artifacts' / DROPPED_DIRNAME) if r.get('run') == run_dir.name and r.get('finding_id')}
    readings = replay_readings(workdir)
    pools = [finding_pool(reading, f, (rows or {}).get(f.finding_id or '')) if reading is not None else None for f in confirmed]
    # Spread (D3): a scenario that is the ONLY source of a finding still to record is reserved for it, so a finding with
    # other sources records elsewhere first; and a scenario an earlier record row of this plan already cut is used last.
    sole = {next(iter(sources)) for f, pool in zip(confirmed, pools) if len(sources := _sources(pool)) == 1 and not _guarded(regressions_dir, f, nothing)}
    used: set[str] = set()
    for finding, pool in zip(confirmed, pools):
        row = _finding_row(finding)

        def skip(reason: str, kind: str = SKIP_UNRECORDABLE, row: dict[str, Any] = row, **extra: Any) -> None:
            plan['skipped'].append({**row, 'reason': reason, 'kind': kind, **extra})

        typed_row = (rows or {}).get(finding.finding_id or '')
        if pool is not None and (pipeline or _without_a_turn(list(pool.items.values()))):
            skip(NO_CONVERSATIONAL_TURN, SKIP_UNREPLAYABLE)
            continue
        if bench['harness_wide']:
            skip('a harness-wide bench issue is open', SKIP_BLOCKED, blocked_by={'harness_wide': bench['harness_wide']})
            continue
        if finding.finding_id is None:
            skip('it carries no finding id to name the folder by')
            continue
        try:
            slug = finding_slug(finding.finding_id, regressions_dir)
        except Refused as exc:
            skip(str(exc))
            continue
        row['slug'] = slug
        row['folder'] = f'{FOLDER_PREFIX}{slug}'
        # Covered first: a folder that already guards the issue is its regression, whatever this run can cut. One the
        # scored run marks measuring nothing guards nothing, yet is kept, never recorded twice.
        covering = covering_folders(regressions_dir, finding.finding_id, slug)
        guarding = [name for name in covering if name not in nothing]
        if guarding:
            row['covered_by'] = guarding[0]
            said = {'reading': readings[guarding[0]]} if guarding[0] in readings else {}
            skip(f'already covered by `{guarding[0]}`', SKIP_COVERED, **({'held_back': True} if guarding[0] in held else {}), **said)
            continue
        if covering:
            skip(f'`{covering[0]}` replayed in the scored run and measured nothing', SKIP_UNMEASURED, recorded=covering[0])
            continue

        picked: dict[str, Any] = {}

        def attempt(policy: str | None, pool: Pool | None, finding: Finding = finding, picked: dict[str, Any] = picked) -> tuple[str, str, dict[str, Any]] | None:
            """Why ``policy``'s pool records nothing, or None when the record step's own choice records from it."""
            if policy is None:
                return 'its probe names no eval policy to choose the conversation and the cut by', SKIP_UNRECORDABLE, {}
            if (pool.reason == NO_SEEN_IN) if pool is not None else not finding.seen_in:
                return NO_SEEN_IN, SKIP_UNRECORDABLE, {}
            if policy in bench['policies']:
                return f'a bench issue the gate left open touches `{policy}`', SKIP_BLOCKED, {'blocked_by': {'policy': policy}}
            if pool is None or run_dir is None:
                return None
            if pool.reason is not None:
                return pool.reason, pool.kind or SKIP_UNRECORDABLE, {}
            uncuttable = _uncuttable(pool, policy, bench['scenarios'], bench['cells'])
            if uncuttable is not None:
                return uncuttable[0], uncuttable[1], {'blocked_by': uncuttable[2]} if uncuttable[2] else {}
            try:  # the record step's own choice, on the same conversations and cuts: what the plan records, the pick records
                picked.update(choose(run_dir, policy=policy, scenario=None, cut_turn=None, resume_gap=resume_gap, workdir=workdir,
                                     exclude=bench['scenarios'], exclude_cells=bench['cells'], pool=pool.items, cuts=pool.cuts,
                                     quote_cut=pool.quote_cut))
            except CannotMeasure as exc:
                return str(exc), SKIP_CANNOT_MEASURE, {}
            except OpenerDocument as exc:
                return str(exc), SKIP_OPENER_DOCUMENT, {}
            except HistoryDocument as exc:
                return str(exc), SKIP_HISTORY_DOCUMENT, {}
            except Refused as exc:
                return f'its pick refuses: {" ".join(str(exc).split())}', SKIP_UNRECORDABLE, {}
            return None

        # Every check its standing links tie to it, the probe first: the first that records wins (a link is the reader's
        # typed "this red is this finding"). None records: the first reason other than "not reproduced", else the run's
        # typed reading of the finding.
        failures, chosen = [], None
        for policy in (reading.candidates(finding) if reading is not None and pool is not None and pool.typed else [finding.policy]) or [None]:
            candidate = pool if policy == finding.policy else finding_pool(reading, finding, typed_row, policy) if reading is not None else None
            # Within each check: an unused source no other finding depends on, then an unused one, then the whole pool. Only
            # the whole pool's failure is reported, as before.
            for narrowed in _spread(candidate, used, sole):
                failure = attempt(policy, narrowed)
                if failure is None:
                    chosen = (policy, narrowed)
                    break
            if chosen is not None:
                break
            failures.append(failure)
        if chosen is None:
            reason, kind, extra = next((f for f in failures if f[1] != SKIP_NOT_REPRODUCED), failures[0])
            outcome = reading.outcome(finding.finding_id) if reading is not None and kind == SKIP_NOT_REPRODUCED else None
            if outcome in OUTCOME_KINDS:
                kind = OUTCOME_KINDS[outcome]
                reason = FIXES_LINES[kind].removeprefix('n/a — ')
            skip(reason, kind, **extra)
            continue
        if not finding.agent_reason:
            skip('it carries no `why it is the agent:` line')
            continue
        row['policy'], pool = chosen
        if pool is not None and run_dir is not None:
            row.update(items=list(pool.items), cut_from=pool.cuts, **({'quote_cut': list(pool.quote_cut)} if pool.quote_cut else {}))
        if slug in taken:
            # The other finding's folder does not exist yet, so the suffix this one takes is only
            # known once it does: the next build derives it from the folder on disk.
            skip(f'its id derives the same folder name as `{taken[slug]}`, recorded first; the next build records it under a suffixed name',
                 SKIP_DEFERRED)
            continue
        if not acceptance and len(plan['record']) + spent - len(evicted) >= cap:
            victim = _weakest_incumbent(finding, incumbents, evicted, sections, regressions_dir, readings)
            if victim is None:
                skip(f'over the cap of {cap} new per build', SKIP_OVER_CAP)
                continue
            row['evicts'], evicted[victim] = victim, finding.finding_id
        if acceptance and len(plan['record']) >= cap:
            row['past_cap'] = True  # the fix plan says it waits, and its pick refuses it (:func:`pick_from_plan`)
        elif picked.get('item'):
            row['source'] = _scenario_of(str(picked['item']))
            used.add(row['source'])
        taken[slug] = finding.finding_id
        plan['record'].append(row)
    for row in plan['skipped']:
        if row.get('covered_by') in evicted:
            folder = row.pop('covered_by')
            for key in ('held_back', 'reading'):
                row.pop(key, None)
            row.update(kind=SKIP_OVER_CAP, evicted=folder, evicted_by=evicted[folder],
                       reason=f'`{folder}` gives its place to `{evicted[folder]}`, a more severe issue (cap of {cap} per build)')
    for row in [*plan['record'], *plan['skipped']]:
        if row.get('finding_id') in removed and not row.get('covered_by'):
            row['removed'] = removed[row['finding_id']]
    return plan


def _weakest_incumbent(finding: Finding, incumbents: Collection[str], taken: Collection[str], sections: Mapping[str, Finding],
                       regressions_dir: Path, readings: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The incumbent folder ``finding`` evicts when the build's cap is full, or None: the weakest of those whose finding's
    severity is STRICTLY lower (:func:`_severity_of` of its current section) — by severity, then confidence, then one that
    caught nothing in the scored run, then the latest recorded. An unknown-severity finding evicts nothing."""
    rank = _severity_of(finding)
    if rank >= len(SEVERITY_ORDER):
        return None
    weaker = []
    for order, name in enumerate(incumbents):
        if name in taken or not (regressions_dir / name).is_dir():
            continue
        fid = recorded_finding(regressions_dir / name)
        section = sections.get(fid) if fid else None
        severity = _severity_of(section)
        if severity <= rank:
            continue
        said = readings.get(name)
        quiet = isinstance(said, Mapping) and said.get('caught') == 0
        weaker.append(((severity, _rank(section.confidence if section else None, CONFIDENCE_ORDER), quiet, order), name))
    return max(weaker)[1] if weaker else None


def _guarded(regressions_dir: Path, finding: Finding, nothing: Collection[str]) -> bool:
    """Whether a folder on disk that measures something already covers ``finding`` (the plan skips it as covered)."""
    if finding.finding_id is None:
        return False
    try:
        slug = finding_slug(finding.finding_id, regressions_dir)
    except Refused:
        return False
    return any(name not in nothing for name in covering_folders(regressions_dir, finding.finding_id, slug))


def _scenario_of(item: str) -> str:
    """A conversation's source scenario: its item without the ``/trial_N`` part."""
    return item.partition('/')[0]


def _sources(pool: Pool | None) -> set[str]:
    """The source scenarios of a pool's conversations."""
    return {_scenario_of(item) for item in pool.items} if pool is not None else set()


def _narrow(pool: Pool, keep: Collection[str]) -> Pool:
    """``pool`` cut to the conversations of the ``keep`` scenarios, each with its own cut."""
    items = {item: d for item, d in pool.items.items() if _scenario_of(item) in keep}
    return pool._replace(items=items, cuts={k: v for k, v in pool.cuts.items() if k in items}, quote_cut=tuple(k for k in pool.quote_cut if k in items))


def _spread(pool: Pool | None, used: Collection[str], sole: Collection[str]) -> list[Pool | None]:
    """The pools to try for one check, narrowest first: the sources neither used by an earlier record row of the plan nor
    the only source of some finding; then the unused ones; then the whole pool, which is always last and decides the
    reason when nothing records. A pool with one source (or none) is tried whole."""
    sources = _sources(pool)
    if pool is None or len(sources) < 2:
        return [pool]
    tiers = [sources - set(used) - set(sole), sources - set(used)]
    out = [_narrow(pool, keep) for n, keep in enumerate(tiers) if keep and keep != sources and (n == 0 or keep != tiers[0])]
    return [*out, pool]


def pick_from_plan(run_dir: Path, plan: Any, finding_id: str, *, resume_gap: str | None, regressions_dir: Path) -> dict[str, Any]:
    """``pick --plan <plan> --finding <id>``: the plan row's own choice — its probe, its conversations (``items``), its
    cuts (``cut_from``, ``quote_cut``) and the plan's bench block — through :func:`choose` (its workdir is
    ``regressions_dir``'s parent), so the conversation and the cut recorded are the ones the plan counted. A plan made from another run, or one that does not record the finding,
    is refused, and so is a fix plan's row marked ``past_cap`` (:data:`OVER_BOUND_LINE`): the plan's bound is its
    first ``cap`` rows, whichever of them the user accepts."""
    if not isinstance(plan, dict):
        raise Refused('--plan is not a plan written by auto-plan.')
    if plan.get('run') != run_dir.name:
        raise Refused(
            f'the plan was made from runs/{plan.get("run") or "(no run)"}, not {run_dir}: its conversations and cuts are that '
            "run's. Pick from the run the plan names, or plan again from this one."
        )
    row = next((r for r in plan.get('record') or [] if isinstance(r, dict) and r.get('finding_id') == finding_id), None)
    if row is None:
        skipped = next((r for r in plan.get('skipped') or [] if isinstance(r, dict) and r.get('finding_id') == finding_id), None)
        raise Refused(f'the plan does not record {finding_id}' + (f': {skipped.get("reason")}.' if skipped else '; plan again or name another finding.'))
    if not isinstance(row.get('items'), list):
        raise Refused(f'the plan row for {finding_id} carries no conversations (a plan from before items and cuts were planned): plan again.')
    if row.get('past_cap'):
        raise Refused(f'{OVER_BOUND_LINE} (the fix plan marks it past its bound of {plan.get("cap") or AUTO_CAP}).')
    on_disk = run_conversations(run_dir)
    bench = plan.get('bench') if isinstance(plan.get('bench'), dict) else {}
    # `probe`: the check the row records on (the probe, or another check the run's links tie to the finding): the
    # ROLES.md **Probe** line names it, so the folder's owner is the check the plan counted.
    return {'probe': row.get('policy'), **choose(
        run_dir, policy=row.get('policy'), scenario=None, cut_turn=None, resume_gap=resume_gap, workdir=regressions_dir.parent,
        exclude=bench.get('scenarios') or (),
        exclude_cells=bench.get('cells') or (), pool={item: on_disk[item] for item in row['items'] if item in on_disk},
        cuts={str(k): v for k, v in (row.get('cut_from') or {}).items() if _is_int(v)}, quote_cut=row.get('quote_cut') or (),
    )}


def audit_bench_cells(run_dir: Path, cells: Collection[str]) -> list[str]:
    """``--bench-cells-from-audit``: each bare ``<scenario>:<policy>`` cell narrowed to the conversations of that scenario
    whose audited unit of that policy the run's own red-cell audit last ruled ``FALSE RED``, one item cell each
    (``<scenario>/trial_<N>:<policy>``, trial 1 as ``…/trial_1``), so a false red on one trial never benches another
    trial's true red. A cell the audit rules no false red under, and an item cell, stay as given."""
    import red_cell_audit  # noqa: PLC0415
    import red_cells  # noqa: PLC0415
    import scored_findings  # noqa: PLC0415

    audit = scored_findings._audit(run_dir)  # noqa: SLF001
    if any(r.get(red_cell_audit.THREAD_ID) for r in audit):  # a superseded attempt's row rules no conversation of the run
        audit = red_cells.current_rows(red_cells.Run(run_dir), audit)
    last: dict[Any, dict] = {}
    for audit_row in audit:
        last[scored_findings.unit_of(audit_row)] = audit_row
    false_red: dict[str, set[str]] = {}
    for (item, policy, _), audit_row in last.items():
        if red_cell_audit.verdict_of(audit_row) == 'FALSE RED':
            false_red.setdefault(policy, set()).add(item)
    out: list[str] = []
    for cell in cells:
        scenario, policy = parse_cell(cell, flag='--bench-cell')
        hits = [] if '/' in scenario else sorted((i for i in false_red.get(policy, ()) if _names_scenario(i.partition('/')[0], scenario)), key=item_order)
        out += [f'{item if "/" in item else f"{item}/trial_1"}:{policy}' for item in hits] or [f'{scenario}:{policy}']
    return out


def ledger_folders(path: Path) -> list[str]:
    """The folders a build's ledger (:data:`BUILD_LEDGER_FILENAME`) lists; an absent ledger lists none. An unreadable one
    is said on stderr and lists none: the plan is never blocked on it."""
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'WARNING: {path} could not be read ({type(exc).__name__}: {exc}); this build\'s earlier records are not counted against its cap.', file=sys.stderr)
        return []
    folders = doc.get('folders') if isinstance(doc, dict) else None
    return [str(f) for f in folders if isinstance(f, str) and f] if isinstance(folders, list) else []


def add_to_ledger(path: Path, folders: Collection[str]) -> None:
    """Add folders to the build's ledger (written whole, atomically); nothing new writes nothing."""
    have = ledger_folders(path)
    merged = list(dict.fromkeys([*have, *folders]))
    if merged == have:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(json.dumps({'schema': BUILD_LEDGER_SCHEMA, 'folders': merged}, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def build_incumbents(ledger: Path, regressions_dir: Path, counted_plan: Path | None = None) -> list[str]:
    """The folders this build already recorded and still counts, in ledger order: its ledger's still on disk, less each
    one the scored run marks hollow (:func:`hollow_folders`: typed replay evidence, so a fresh folder frees no slot).
    ``--counted-plan`` (an earlier plan file of this build) adds its written folders to the ledger first, so the alias
    and the ledger never disagree."""
    if counted_plan is not None:
        plan = _read_json(counted_plan)
        if not isinstance(plan, dict):
            raise Refused(f'{counted_plan} is not a plan written by auto-plan.')
        add_to_ledger(ledger, [str(r['folder']) for r in plan.get('record') or [] if isinstance(r, dict) and r.get('folder')
                               and (regressions_dir / str(r['folder'])).is_dir()])
    nothing = hollow_folders(regressions_dir.parent)
    return [folder for folder in dict.fromkeys(ledger_folders(ledger)) if (regressions_dir / folder).is_dir() and folder not in nothing]


def build_spent(ledger: Path, regressions_dir: Path, counted_plan: Path | None = None) -> int:
    """How many folders this build already recorded and still counts against its cap (:func:`build_incumbents`)."""
    return len(build_incumbents(ledger, regressions_dir, counted_plan))


#: The fix plan's ``Regression:`` line for each typed skip (:func:`fixes_line`); a blocked one names what blocks it.
FIXES_LINES = {
    SKIP_UNREPLAYABLE: 'n/a — cannot be replayed (this agent runs without a user conversation; its check tracks it)',
    SKIP_CANNOT_MEASURE: 'n/a — its check grades the whole conversation, so a replay cut mid-conversation cannot measure it',
    SKIP_OPENER_DOCUMENT: f'n/a — {OPENER_DOCUMENT.replace("its conversation opened", "its conversations open")}',
    SKIP_HISTORY_DOCUMENT: f'n/a — {HISTORY_DOCUMENT}',
    SKIP_OVER_CAP: BUILD_CAP_LINE,
    SKIP_ELSEWHERE: 'n/a — seen in another run; this run did not reproduce it',
    SKIP_NOT_REPRODUCED: 'n/a — this run did not reproduce it',
    SKIP_DEFERRED: 'waits — the next `/simulation-fixes` records it under its own folder name',
    SKIP_UNRECORDABLE: 'n/a — no conversation in this run can be replayed for it',
    SKIP_READ_ONLY: "n/a — seen in the agent's replies in the scored run, but no failed check there is tied to it",
    SKIP_REACHED_HANDLED: ('n/a — no failure to record: where its situation came up in the scored run the agent handled it; '
                           'none of those was a full replay of the conversation it was found in, so the finding stands'),
    SKIP_NOT_TESTED: 'n/a — not tested in the scored run: its situation never came up',
    SKIP_NOT_SET_UP: 'n/a — not tested in the scored run: no conversation there was set up for its situation',
}
#: A folder that is on disk but has not measured its issue yet: replayed with nothing measured, or held back from the run.
NOT_YET_MEASURED = 'recorded, not yet measured'


def _blocked_what(blocked: Mapping[str, Any]) -> str:
    """What a blocked finding waits on, in the fix plan's words: every scenario, the check, or the scenarios."""
    if blocked.get('harness_wide'):
        return 'every scenario'
    if blocked.get('policy'):
        return f'the `{blocked["policy"]}` check'
    names = list(dict.fromkeys([*(blocked.get('scenarios') or []), *(str(c).partition(':')[0] for c in blocked.get('cells') or [])]))
    return ' and '.join(filter(None, [', '.join(names[:-1]), names[-1]])) if names else 'every scenario'


#: A covering folder the scored run did not replay because it was recorded after that run (``unplayed``): it still covers.
RECORDED_AFTER = 'recorded after the scored run; the next run replays it'


def did_not_recur(reading: Any) -> str | None:
    """What a covering folder's scored-run reading says when it measured and caught nothing (:func:`replay_readings`):
    "it did not recur at its turn in its N replays in this run" (at its turn: the ``at_cut`` axis). Still kept (D1): an
    intermittent folder is normal, and ``not_pinned`` retires one only after enough clean replays. None otherwise."""
    if not isinstance(reading, Mapping) or reading.get('caught') != 0 or not _is_int(reading.get('measured')) or reading['measured'] < 1:
        return None
    n = reading['measured']
    where = ' at its turn' if reading.get('axis') == 'at_cut' else ''
    return f'it did not recur{where} in its {n} {"replay" if n == 1 else "replays"} in this run'


def fixes_line(row: Mapping[str, Any]) -> str:
    """The fix plan's ``Regression:`` line for one plan row, from its typed fields alone (never the word "bench"). A
    finding whose earlier folder was dropped (``removed``) says so in the same line (:data:`REMOVED_NOTES`)."""
    if row.get('covered_by'):
        quiet = did_not_recur(row.get('reading'))
        said = (NOT_YET_MEASURED if row.get('held_back') else RECORDED_AFTER if row.get('unplayed')
                else f'recorded; {quiet}' if quiet else 'already recorded')
        return f'`{row["covered_by"]}` — {said}'
    if row.get('kind') == SKIP_UNMEASURED and row.get('recorded'):
        return f'`{row["recorded"]}` — {NOT_YET_MEASURED}'
    if row.get('evicted'):
        return EVICTED_LINE
    note = REMOVED_NOTES.get(str((row.get('removed') or {}).get('kind'))) if isinstance(row.get('removed'), Mapping) else None
    return f'{_fixes_line(row)}; {note}' if note else _fixes_line(row)


def _fixes_line(row: Mapping[str, Any]) -> str:
    if row.get('past_cap'):
        return OVER_BOUND_LINE
    if 'reason' not in row:
        return f'`{row["folder"]}` — {ON_ACCEPTANCE_LINE}'
    kind = row.get('kind') or (SKIP_UNREPLAYABLE if row.get('reason') == NO_CONVERSATIONAL_TURN else SKIP_UNRECORDABLE)
    if kind == SKIP_BLOCKED:
        return f'waits for a re-run with the test setup of {_blocked_what(row.get("blocked_by") or {})} fixed'
    return FIXES_LINES.get(kind, FIXES_LINES[SKIP_UNRECORDABLE])


def fixes_lines(plan: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(finding id, its Regression: line)`` for every row of a fix plan that names a finding, record rows first."""
    return [(str(row['finding_id']), fixes_line(row)) for key in ('record', 'skipped') for row in plan.get(key) or []
            if isinstance(row, dict) and row.get('finding_id')]


def _issues(count: int, more: bool = False) -> str:
    """``N [more] confirmed issue(s) has/have no regression yet`` — the count's own agreement."""
    noun = 'issue has' if count == 1 else 'issues have'
    return f'{count} {"more " if more else ""}confirmed {noun} no regression yet'


def _unreplayable(count: int, more: bool = False) -> str:
    """The sentence for confirmed issues skipped as :data:`NO_CONVERSATIONAL_TURN`: a state no later run changes, so it
    names no pending step."""
    one = count == 1
    return (f'{count} {"more " if more else ""}confirmed {"issue" if one else "issues"} cannot be replayed, because this agent runs '
            f'without a user conversation; {"its check tracks it" if one else "their checks track them"}.')


#: The plan keys ``auto-report`` keeps its ``--refused`` / ``--rare`` in, so a later ``auto-report`` of the same plan
#: (the hand-off's, after the final run confirmed more findings) needs only ``--findings``.
PLAN_REFUSED_KEY = 'refused'
PLAN_RARE_KEY = 'rare'

#: The report line's sentence for a harness whose manifest declares it cannot resume (the plan's ``harness_resume``).
RESUME_GAP_LINE = (
    'This harness cannot resume a recorded conversation, so each regression replays its conversation from the '
    'recorded opening message and is read over the whole replay.'
)


def regression_tally(plan: Mapping[str, Any], refused: Mapping[str, str] | None = None, rare: Collection[str] = (), *,
                     confirmed: Collection[str | None], on_page: Collection[str | None], pipeline: bool) -> dict[str, Any]:
    """The ONE count of what the gate's findings became, which the chat line (:func:`auto_report_line`) and the page's copy
    (:func:`page_copy`) both render, so their numbers agree. ``confirmed`` names the issues the page reads confirmed, and
    ``on_page`` every planned issue the page still shows (:func:`page_issues`: a confirmed finding the copy demotes to seen
    once is still shown, ranked and diagnosed), folds applied. A folder counts as kept when its issue is on the page; an
    issue counts as without a regression only when the page reads it confirmed:

    - ``recorded``: the plan's record rows this build wrote (``refused`` / ``rare`` are not written) whose issue is shown;
    - ``dropped``: the folders written for an issue the page does not show (named, never counted);
    - ``waiting``: ``{issue: folder}`` on disk that has not measured it yet (measured nothing, or held back);
    - ``covering``: ``{issue: [folders]}`` an earlier folder already covers;
    - ``missing``: the issues with no regression yet; ``unreplayable``: those whose conversations hold no user turn (on a
      ``pipeline``, every issue), which no later run can replay.

    Each issue is counted once, in one bucket."""
    unwritten = set(refused or {}) | set(rare)
    written = [row for row in plan.get('record', []) if row.get('finding_id') not in unwritten]
    shown = set(confirmed) | set(on_page)
    recorded = [row for row in written if row.get('finding_id') in shown]
    # Rows the page folds into one id (`cmd_auto_report`) are one issue: counted once, every folder still named.
    recorded_ids = dict.fromkeys(row.get('finding_id') for row in recorded)
    dropped = list(dict.fromkeys(str(row['folder']) for row in written if row.get('finding_id') not in shown))
    # A folder on disk that has not measured its issue yet (it measured nothing in the scored run, or the bundle held it
    # back) is neither "already has one" nor "no regression yet": counted apart, by its folder.
    waiting: dict[Any, str] = {}
    for row in plan.get('skipped', []):
        folder = row.get('recorded') if row.get('kind') == SKIP_UNMEASURED else row.get('covered_by') if row.get('held_back') else None
        if folder and row.get('finding_id') in shown and row.get('finding_id') not in recorded_ids:
            waiting.setdefault(row.get('finding_id'), folder)
    # A confirmed issue a folder from before this build covers is counted too, by its folder(s).
    covering: dict[Any, list[str]] = {}
    for row in plan.get('skipped', []):
        if row.get('covered_by') and row.get('finding_id') in shown and row.get('finding_id') not in {*recorded_ids, *waiting}:
            covering.setdefault(row.get('finding_id'), []).append(row['covered_by'])
    has_one = set(recorded_ids) | set(waiting) | {row.get('finding_id') for row in plan.get('skipped', []) if row.get('covered_by')}
    # An issue whose conversations hold no user turn can never be replayed: counted apart, with no pending step. On a
    # pipeline (`pipeline`: no conversation of the scored run holds a user turn) that is every confirmed issue, whichever
    # run the plan was made from.
    no_turn = {row.get('finding_id') for row in plan.get('skipped', []) if row.get('reason') == NO_CONVERSATIONAL_TURN}
    no_turn |= set(confirmed) if pipeline else set()
    without = [finding_id for finding_id in confirmed if finding_id is None or finding_id not in has_one]
    unreplayable = [finding_id for finding_id in without if finding_id is not None and finding_id in no_turn]
    missing = [finding_id for finding_id in without if finding_id is None or finding_id not in no_turn]
    return {'written': written, 'recorded': recorded, 'dropped': dropped, 'waiting': waiting, 'covering': covering,
            'missing': missing, 'unreplayable': unreplayable}


#: How the line names apart an issue the page shows confirmed while calibrating (on the page, not confirmed there): the
#: count line's phrase (``scored_findings.NOT_SEEN_AGAIN``), never a frequency it may not have.
NOT_SEEN_AGAIN = 'not seen again in the scored run'


def _recorded_from(n: int, seen_once: int) -> str:
    """``N recorded from confirmed findings``, naming apart the ones from findings the page shows confirmed while
    calibrating (:data:`NOT_SEEN_AGAIN`)."""
    if not seen_once:
        return f'{n} recorded from confirmed findings'
    once = f'{"a finding" if seen_once == 1 else "findings"} {NOT_SEEN_AGAIN}'
    if seen_once == n:
        return f'{n} recorded from {once}'
    sure = n - seen_once
    return f'{n} recorded ({sure} from {"a confirmed finding" if sure == 1 else "confirmed findings"}, {seen_once} from {once})'


def _issue_kinds(n: int, seen_once: int, *, more: bool) -> str:
    """``N [more] confirmed issue(s)``, or the issues the page shows confirmed while calibrating named apart
    (``2 more issues (1 confirmed, 1 not seen again in the scored run)``)."""
    more_ = 'more ' if more else ''
    noun = 'issue' if n == 1 else 'issues'
    if not seen_once:
        return f'{n} {more_}confirmed {noun}'
    if seen_once == n:
        return f'{n} {more_}{noun} {NOT_SEEN_AGAIN}'
    return f'{n} {more_}{noun} ({n - seen_once} confirmed, {seen_once} {NOT_SEEN_AGAIN})'


def auto_report_line(plan: dict[str, Any], refused: dict[str, str] | None = None, rare: Collection[str] = (), *, confirmed: Collection[str | None],
                     pipeline: bool = False, on_page: Collection[str | None] | None = None) -> str:
    """:func:`_auto_report_line`, then one sentence naming each earlier replay the plan's rows say was removed (evicted for a
    more severe issue, or dropped because it could not reach its issue: :func:`removed_sentence`), so the tally the reader
    sees never counts a folder that is gone without saying so."""
    line = _auto_report_line(plan, refused, rare, confirmed=confirmed, pipeline=pipeline, on_page=on_page)
    said = removed_sentence(plan) if plan.get('nothing_because') is None else ''
    return f'{line} {said}' if said else line


def _auto_report_line(plan: dict[str, Any], refused: dict[str, str] | None = None, rare: Collection[str] = (), *, confirmed: Collection[str | None],
                      pipeline: bool = False, on_page: Collection[str | None] | None = None) -> str:
    """The suite report's one line about what the gate's findings became — a RESULT, counted, never reasoned.

    ``confirmed`` is every finding the page's copy of the findings file (``scored_findings.copy_for_page``) reads
    ``confirmed`` NOW, at report time (a record step may demote one after the plan was written; a scored final run
    leaves out what it did not reproduce and confirms what it promoted); ``on_page`` adds the planned findings the copy
    still shows demoted (:func:`page_issues`; ``cmd_auto_report`` passes it, None reads ``confirmed`` alone), whose folders
    are kept, never "not among the confirmed findings". The counts are :func:`regression_tally`'s, which the page's copy
    (:func:`page_copy`) renders too. The line names the folders written and COUNTS the confirmed
    issues that have no regression yet; why each one has none is bench or harness mechanics, worded against a
    qualification run the customer never sees, so it goes to :func:`auto_report_reasons` (stderr), never here. The one
    reason counted apart is :data:`NO_CONVERSATIONAL_TURN`: no later run can replay such an issue, so it is said as
    :func:`_unreplayable`, never as a regression still to come.
    ``refused`` maps a planned finding id to the refusal ``/record-regression`` ended with; ``rare`` names the
    planned findings whose record ended rare at the cut (the folder was written, then deleted). A written folder whose
    finding ``confirmed`` does not hold (the scored run did not reproduce it) guards none of the issues counted, so it is
    named as not counted, never counted: the line's numbers and the page's are one count. A plan whose
    ``harness_resume`` is set adds :data:`RESUME_GAP_LINE`: what a regression is on that harness.
    """
    nothing = plan.get('nothing_because')
    if nothing == NOTHING_STATIC_ONLY:
        return 'Regressions: none recorded — a static-only suite has no qualification run to cut a conversation from.'
    if nothing == NOTHING_OPTED_OUT:
        return 'Regressions: none recorded — recording from confirmed findings is off for this build (`--no-auto-regressions`).'

    tally = regression_tally(plan, refused, rare, confirmed=confirmed, on_page=confirmed if on_page is None else on_page, pipeline=pipeline)
    written, recorded, waiting, covering = tally['written'], tally['recorded'], tally['waiting'], tally['covering']
    recorded_ids = dict.fromkeys(row.get('finding_id') for row in recorded)
    dropped = [f'`{name}`' for name in tally['dropped']]
    missing, unreplayable = len(tally['missing']), len(tally['unreplayable'])
    after = {row['covered_by'] for row in plan.get('skipped', []) if row.get('covered_by') and row.get('unplayed')}
    # A covering folder that measured and caught nothing in the scored run is still kept (D1), and says so by its reading.
    quiet = {row['covered_by']: q for row in plan.get('skipped', []) if row.get('covered_by') and (q := did_not_recur(row.get('reading')))}
    folders = ', '.join(f'`{name}`' + (f' ({RECORDED_AFTER})' if name in after else f' ({quiet[name]})' if name in quiet else '')
                        for name in dict.fromkeys(name for names in covering.values() for name in names))
    # An issue the page shows confirmed while calibrating (on the page, not confirmed there) is kept but never called confirmed: the line's
    # "confirmed" counts are the page's.
    sure = set(confirmed)
    once = lambda ids: sum(1 for i in ids if i not in sure)  # noqa: E731
    kept = (f' {_issue_kinds(len(covering), once(covering), more=bool(recorded))} '
            f'{"already has a regression" if len(covering) == 1 else "already have one"} — {folders}.') if covering else ''
    if waiting:
        kept += (f' {_issue_kinds(len(waiting), once(waiting), more=bool(recorded or covering))} {"has a regression" if len(waiting) == 1 else "have one"} '
                 f'{NOT_YET_MEASURED} — ' + ', '.join(f'`{name}`' for name in dict.fromkeys(waiting.values())) + '.')
    head = f'Regressions: {_recorded_from(len(recorded_ids), once(recorded_ids))}'
    # A harness that cannot resume replays every regression from its recorded opener: said once, whatever the counts.
    gap = f' {RESUME_GAP_LINE}' if plan.get('harness_resume') and (written or confirmed) else ''
    cannot = f' {_unreplayable(unreplayable, more=bool(recorded_ids or covering or missing))}' if unreplayable else ''
    said, one = '', len(dropped) == 1
    if dropped:
        named = dropped[0] if one else f'{", ".join(dropped[:-1])} and {dropped[-1]}'
        said = (f'{named} {"was" if one else "were"}{" also" if recorded else ""} recorded, but {"its issue is" if one else "their issues are"} '
                'not among the confirmed findings')
    if not recorded:
        stays = 'stays' if missing == 1 else 'stay'
        if said:
            rest = (f'{kept} {_issues(missing, more=bool(kept))} and {stays} in Agent findings for `/simulation-fixes`.{cannot}' if missing
                    else f'{kept}{cannot}' if unreplayable or waiting
                    else ' Every confirmed issue already has a regression.' if confirmed else '')
            return f'Regressions: none counted — {said}.{rest}{gap}'
        if missing:
            joined = f'.{kept} {_issues(missing, more=True)}' if kept else f'; {_issues(missing)}'
            return f'{head}{joined} and {stays} in Agent findings for `/simulation-fixes`.{cannot}{gap}'
        if unreplayable or waiting:
            return f'{head}.{kept}{cannot}{gap}'
        return f'{head} — every confirmed issue already has a regression.{gap}' if confirmed else f'{head} — the gate confirmed no agent finding.'
    tail = (f' {said}, so {"it is" if one else "they are"} not counted here.' if said else '') + gap
    line = f'{head} — ' + ', '.join(f'`{name}`' for name in dict.fromkeys(row['folder'] for row in recorded)) + '.'
    if not missing:
        return line + kept + cannot + tail
    stays = 'it stays' if missing == 1 else 'they stay'
    return f'{line}{kept} {_issues(missing, more=True)}; {stays} in Agent findings for `/simulation-fixes`.{cannot}{tail}'


def auto_report_reasons(plan: dict[str, Any], refused: dict[str, str] | None = None, rare: Collection[str] = ()) -> list[tuple[str, str]]:
    """``(finding, why it has no regression from this build)`` for every planned or skipped finding not written.

    The operator's half of :func:`auto_report_line`: printed to stderr, never into the customer's line."""
    refused = dict(refused or {})
    rare_why = f'the issue did not happen again at its recorded turn in {CONFIRM_SAMPLES} replays, so no regression pins it'
    refused.update({finding_id: rare_why for finding_id in rare if finding_id not in refused})
    return [
        (row.get('finding_id') or row.get('title'), refused[row['finding_id']])
        for row in plan.get('record', []) if row.get('finding_id') in refused
    ] + [(row.get('finding_id') or row.get('title'), row['reason']) for row in plan.get('skipped', [])]


# ── the record-time replay rate ────────────────────────────────────────────────

#: ``replays.json``'s schema tag. A reader that does not know it treats the file as absent.
CONFIRM_SCHEMA = 'regression-confirm-v1'
#: How many replies the record step samples at the cut, and the share of them a reliable pin is caught on.
CONFIRM_SAMPLES = 5
RELIABLE_CAUGHT = 4
#: What k caught of n measured samples makes of a folder.
BAND_RELIABLE = 'reliable'
BAND_INTERMITTENT = 'intermittent'
BAND_SEEN_ONCE = 'seen_once'
BAND_RARE_AT_CUT = 'rare_at_cut'
#: The exit code of a confirm that found the issue does not recur at the cut: the folder is gone.
EXIT_RARE_AT_CUT = 3
#: The confirm record a rare-at-cut folder leaves beside its pick, once the folder itself is deleted.
CONFIRM_SUFFIX = '.confirm.json'
#: The files ``score_sampled_replies`` reads a folder from (the backend's ``_FOLDER_FILES``).
SCORED_FOLDER_FILES = (ROLES_FILENAME, PIN_FILENAME, FIRST_TALK_FILENAME, ISSUE_TURN_FILENAME)
#: Why a folder is seen once when nothing was scored: no probe policy names its owner, or no sample was valid.
SEEN_ONCE_NO_OWNER = 'no_probe_policy'
SEEN_ONCE_NO_VALID_SAMPLE = 'no_valid_sample'
SEEN_ONCE_NONE_MEASURED = 'no_sample_measured'

#: the backend's per-reply outcome vocabulary for ``score_sampled_replies`` — read from the vendored contract.
_SAMPLED_DEFAULT = {'tool': 'score_sampled_replies', 'outcomes': ['caught', 'missed', 'not_measured'], 'controls': ['clean', 'history_red'], 'max_replies': 10}


def _sampled_contract() -> dict[str, Any]:
    try:
        import plurai_contracts  # noqa: PLC0415 - beside this file; absent only when the file was copied out
    except ImportError:
        return _SAMPLED_DEFAULT
    block = plurai_contracts.contract('sampled_reply_outcomes', default=_SAMPLED_DEFAULT)
    return block if isinstance(block, dict) and isinstance(block.get('outcomes'), list) else _SAMPLED_DEFAULT


def classify(k: int, n: int) -> str:
    """The band ``k`` caught of ``n`` measured samples earns.

    ``n`` counts measured samples only — a sample that raised, called a tool, or that the owner could not
    measure counts nowhere. With all :data:`CONFIRM_SAMPLES` measured: at least :data:`RELIABLE_CAUGHT` caught
    is reliable, some caught is intermittent, none is rare at the cut. Fewer measured: some caught is
    intermittent, none is seen once — too few to call the cut rare.
    """
    if n >= CONFIRM_SAMPLES:
        if k * CONFIRM_SAMPLES >= RELIABLE_CAUGHT * n:
            return BAND_RELIABLE
        return BAND_INTERMITTENT if k else BAND_RARE_AT_CUT
    return BAND_INTERMITTENT if k else BAND_SEEN_ONCE


def _samples(path: Path) -> dict[str, Any]:
    doc = _read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get('valid'), list):
        raise Refused(f'{path} is not a samples file `verify_smoke.py --sample-cut` wrote.')
    return doc


def sample_payload(folder: Path, samples: dict[str, Any], *, eval_set_id: str | None = None, owner: str | None = None) -> dict[str, Any]:
    """The ``score_sampled_replies`` arguments for a folder's valid samples: ``eval_set_id`` (when given),
    ``owner``, ``folder_files`` and ``replies``. The owner is the eval set's ledger owner when the caller
    has one, else the paragraph's **Probe** policy — at record time the ledger does not yet name one."""
    roles = folder / ROLES_FILENAME
    owner = owner or (probe_policy(roles.read_text(encoding='utf-8')) if roles.is_file() else None)
    if owner is None:
        raise Refused(f'{folder.name}: its ROLES.md names no **Probe** policy, so no check owns the replies. Confirm it --seen-once {SEEN_ONCE_NO_OWNER}.')
    replies = [{'reply': str(s.get('reply') or ''), 'turn_messages': s.get('turn_messages') or []} for s in samples['valid'] if isinstance(s, dict)]
    if not replies:
        raise Refused(f'{folder.name}: no valid sample to score. Confirm it with --samples alone; it is seen once.')
    files = {name: (folder / name).read_text(encoding='utf-8') for name in SCORED_FOLDER_FILES if (folder / name).is_file()}
    return {**({'eval_set_id': eval_set_id} if eval_set_id else {}), 'owner': owner, 'folder_files': files, 'replies': replies}


def recut_candidates(run_dir: Path, policy: str, *, exclude: tuple[str, int] | None) -> list[dict[str, Any]]:
    """The source run's other ``(item, turn)`` cells, over every trial, the policy's recorded verdict names violated —
    where the issue might be pinned instead (``scenario`` is the item, :func:`run_conversations`). Suggested to the
    operator; never cut automatically."""
    candidates, _ = _violating(list(run_conversations(run_dir).items()), policy, lambda _name: False)
    return [
        {'scenario': name, 'turn': turn}
        for name, d in candidates
        for turn in violated_turns(read_transcript(d / TRANSCRIPT_FILENAME), policy)
        if exclude is None or (name, turn) != exclude
    ]


def run_of(transcript: Path) -> tuple[Path, str]:
    """``(run dir, item)`` of a conversation's transcript: ``<run>/<scenario>/``, ``<run>/trial_<N>/<scenario>/`` or
    ``<run>/<scenario>/stability_retry/`` (:func:`run_conversations`' layout)."""
    import red_cells  # noqa: PLC0415

    item_dir = transcript.parent
    if item_dir.name == red_cells.RETRY_DIRNAME:
        return item_dir.parent.parent, f'{item_dir.parent.name}/{item_dir.name}'
    if red_cells._TRIAL_DIR.fullmatch(item_dir.parent.name) and int(item_dir.parent.name.split('_')[1]) >= 2:  # noqa: SLF001
        return item_dir.parent.parent, f'{item_dir.name}/{item_dir.parent.name}'
    return item_dir.parent, item_dir.name


def _git_history(folder: Path) -> str | None:
    """Why ``folder`` predates this recording in git — tracked, staged or with a commit touching it — or None.

    A folder with no repository around it, or one git does not know, has no history to lose. A git that
    cannot be asked is read as history, never as its absence: the cost of guessing wrong is a deleted
    regression another build already relies on."""
    def ask(*args: str) -> subprocess.CompletedProcess | None:
        try:
            # `core.fsmonitor` off: the repository's own config could name any command for it.
            return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(folder.parent), *args], capture_output=True, text=True, **utf8_child_text(), timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None

    inside = ask('rev-parse', '--is-inside-work-tree')
    if inside is None:
        return 'git could not be asked whether the folder is tracked'
    if inside.returncode != 0 or inside.stdout.strip() != 'true':
        return None
    tracked = ask('ls-files', '--', folder.name)
    if tracked is None or tracked.returncode != 0:
        return 'git could not list the folder'
    if tracked.stdout.strip():
        return 'it is tracked in git'
    logged = ask('log', '-1', '--format=%H', '--', folder.name)
    if logged is not None and logged.returncode == 0 and logged.stdout.strip():
        return f'a commit ({logged.stdout.strip()[:12]}) touches it'
    return None


def confirm(
    folder: Path,
    *,
    samples: dict[str, Any] | None,
    scored: dict[str, Any] | None,
    seen_once: str | None,
    picks_dir: Path | None,
) -> tuple[int, dict[str, Any]]:
    """Classify the folder on k caught of n measured samples, and keep the result.

    Reliable, intermittent and seen once write ``replays.json`` into the folder — local only, never
    uploaded — with the SDK's interval and clean-replay count for k of n, which ``verify_smoke.py`` put
    in the samples file. Rare at the cut deletes the folder, rewrites ``BUNDLE.md``, and leaves a confirm
    record beside the pick naming the cells to re-cut from; exit :data:`EXIT_RARE_AT_CUT`.
    """
    if not (folder / ROLES_FILENAME).is_file():
        raise Refused(f'{folder} is not a regression folder (no {ROLES_FILENAME}).')
    if (folder / REPLAYS_FILENAME).exists():
        raise Refused(
            f'{folder.name} already holds {REPLAYS_FILENAME}: it was confirmed when it was recorded, and that rate is the '
            '"before" every later replay is measured against. confirm runs once, right after the record step writes the folder.'
        )
    roles_text = (folder / ROLES_FILENAME).read_text(encoding='utf-8')
    owner = scored.get('owner') if isinstance(scored, dict) and scored.get('owner') else probe_policy(roles_text)
    reason = seen_once or (samples or {}).get('seen_once_reason')
    k: int | None = None
    n = 0
    if reason is None and scored is None:
        if (samples or {}).get('valid'):
            raise Refused('the samples have valid replies and nothing scored them: pass --scored with the score_sampled_replies result.')
        reason = SEEN_ONCE_NO_VALID_SAMPLE
    if reason is None and scored is not None:
        contract = _sampled_contract()
        caught, missed, unmeasured = contract['outcomes'][:3]
        outcomes = [row for row in scored.get('outcomes') or [] if isinstance(row, dict)]
        k = sum(1 for row in outcomes if row.get('outcome') == caught)
        n = k + sum(1 for row in outcomes if row.get('outcome') == missed)
        if n == 0:
            reasons = sorted({str(row.get('reason')) for row in outcomes if row.get('outcome') == unmeasured and row.get('reason')})
            if scored.get('control') in contract['controls'][1:]:  # the pinned turns alone already fail the owner
                reasons.insert(0, f'control {scored["control"]}')
            reason = f'{SEEN_ONCE_NONE_MEASURED}: {"; ".join(reasons)}' if reasons else SEEN_ONCE_NONE_MEASURED
    band = BAND_SEEN_ONCE if reason is not None else classify(k or 0, n)
    rate = ((samples or {}).get('rates') or {}).get(f'{k}/{n}') if k is not None and n else None
    if isinstance(rate, dict) and scored is not None:
        rate = {**rate, 'not_measured': len([row for row in scored.get('outcomes') or [] if isinstance(row, dict)]) - n}
    record: dict[str, Any] = {
        'schema': CONFIRM_SCHEMA, 'owner': owner, 'k': k, 'n': n, 'band': band, 'reason': reason,
        'wilson95_failed': (rate or {}).get('wilson95_failed'), 'clean_replays_to_clear': (rate or {}).get('clean_replays_to_clear'),
        # The SDK's own record-time rate (`plurai.trials.record_time_rate`), verbatim, when one was computed.
        **({'rate': rate} if isinstance(rate, dict) else {}),
    }
    if band != BAND_RARE_AT_CUT:
        (folder / REPLAYS_FILENAME).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return 0, record
    if picks_dir is None:
        raise Refused('the issue did not recur at the cut, and the confirm record needs --picks-dir to land beside the pick.')
    # Deleted only when THIS recording wrote it: no earlier confirm (refused above), no git history, and the
    # pick this record saved beside it. Anything else is a regression a build may already carry.
    pick_path = picks_dir / f'{folder.name}.json'
    history = _git_history(folder)
    if history is not None or not pick_path.is_file():
        why = history or f'no pick ({pick_path.name}) says this record chose it'
        raise Refused(
            f'{folder.name} was caught in 0 of {n} replays at its cut, but it is not deleted: {why}, so it predates this '
            'recording. Delete it by hand if the issue no longer pins there; its check carries the issue meanwhile.'
        )
    pick = _read_json(pick_path)
    source = Path(str(pick.get('transcript'))) if isinstance(pick, dict) and pick.get('transcript') else None
    cut = pick.get('cut_turn') if isinstance(pick, dict) and _is_int(pick.get('cut_turn')) else None
    run_dir, item = run_of(source) if source is not None else (None, None)
    exclude = (item, cut) if item is not None and cut is not None else None
    record['folder'] = folder.name
    record['finding_id'] = recorded_finding(folder)
    record['recut_candidates'] = recut_candidates(run_dir, owner, exclude=exclude) if run_dir and owner and run_dir.is_dir() else []
    picks_dir.mkdir(parents=True, exist_ok=True)
    (picks_dir / f'{folder.name}{CONFIRM_SUFFIX}').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    regressions_dir = folder.parent
    shutil.rmtree(folder)
    try:
        (regressions_dir / OVERVIEW_FILENAME).write_text(overview_text(regressions_dir), encoding='utf-8')
    except Refused as exc:
        print(f'NOTE: {OVERVIEW_FILENAME} was not rewritten: {exc}', file=sys.stderr)
    return EXIT_RARE_AT_CUT, record


#: The most replays one regression-only step runs; a before that needs more is reported, not chased.
REPLAY_TRIALS_MAX = 10
#: The run row's cell a clear verdict reads, named by the SDK's ``regression_replays`` (``clear_axis``): the cut, for
#: a resumed replay; the whole replay, for one played from its opener. A row that does not name one reads the cut.
CLEAR_AT_CUT, CLEAR_ANYWHERE = 'at_cut', 'anywhere'


def clear_cell(row: dict[str, Any]) -> Any:
    """The cell of a run row (or the SDK's per-policy block) its clear verdict reads: ``row[row['clear_axis']]``."""
    axis = row.get('clear_axis')
    return row.get(axis if axis in (CLEAR_AT_CUT, CLEAR_ANYWHERE) else CLEAR_AT_CUT)


def confirm_record(folder: Path) -> dict[str, Any] | None:
    """The folder's ``replays.json`` confirm record, or None when absent, unreadable or another schema."""
    path = folder / REPLAYS_FILENAME
    try:
        record = _read_json(path) if path.is_file() else None
    except Refused:
        return None
    return record if isinstance(record, dict) and record.get('schema') == CONFIRM_SCHEMA else None


def pinned_axis(record: Any) -> str | None:
    """The clear axis a folder's confirm record PINS (``repin`` wrote it), or None: the SDK's axis then stands."""
    axis = record.get('clear_axis') if isinstance(record, dict) and record.get('schema') == CONFIRM_SCHEMA else None
    return axis if axis in (CLEAR_AT_CUT, CLEAR_ANYWHERE) else None


def _decides(cell: Any) -> tuple[int, int]:
    """``(decided, seen)`` of one axis cell: a pass or a fail decides; an inconclusive, a guard N/A or a cell graded
    under its owner (``deferred``) does not."""
    if not isinstance(cell, dict):
        return 0, 0
    decided = sum(int(cell.get(k) or 0) for k in ('passed', 'failed'))
    return decided, decided + sum(int(cell.get(k) or 0) for k in ('inconclusive', 'na', 'deferred'))


def repin_candidates(summary: Any, *, regressions_dir: Path, owners: dict[str, str]) -> list[dict[str, Any]]:
    """The regressions whose owner cannot decide on the axis they clear on, and can on the other one.

    A replay clears on ``at_cut`` unless its folder pins otherwise; an owner whose violation shows only across the
    conversation (an absence, a promise broken later) is inconclusive or N/A at the cut on every replay, so the pin
    measures nothing however often it runs. Such a folder — the at-cut cell decided on under half its replays, the
    anywhere cell on at least half — is re-pinned on ``anywhere``. ``[{folder, owner, at_cut, anywhere}]``, each
    ``"<decided>/<seen>"``; a folder with no confirm record, or one already pinned, is not a candidate."""
    block = summary.get('regression_replays') if isinstance(summary, dict) else None
    rows = block.get('regressions') if isinstance(block, dict) else None
    out: list[dict[str, Any]] = []
    for scenario, entry in sorted((rows or {}).items()):
        if not isinstance(entry, dict):
            continue
        record = confirm_record(regressions_dir / scenario)
        if record is None or pinned_axis(record) is not None:
            continue
        if entry.get('clear_axis', CLEAR_AT_CUT) != CLEAR_AT_CUT:
            continue
        owner = regression_owner(regressions_dir / scenario, owners)
        cells = (entry.get('policies') or {}).get(owner) if owner else None
        if not isinstance(cells, dict):
            continue
        (cut, cut_seen), (anywhere, any_seen) = _decides(cells.get(CLEAR_AT_CUT)), _decides(cells.get(CLEAR_ANYWHERE))
        if cut_seen and 2 * cut < cut_seen and anywhere and 2 * anywhere >= any_seen:
            out.append({'folder': scenario, 'owner': owner, CLEAR_AT_CUT: f'{cut}/{cut_seen}', CLEAR_ANYWHERE: f'{anywhere}/{any_seen}'})
    return out


def repin(candidates: Collection[dict[str, Any]], *, regressions_dir: Path, source: Path) -> list[str]:
    """Pin each candidate's confirm record on ``anywhere`` (``clear_axis`` + why), in place. Returns the folders written.
    The record names the run it was decided on relative to the workdir (``regressions_dir``'s parent): it is
    committed beside the suite, and a path into one machine's home is meaningless anywhere else."""
    run = Path(os.path.relpath(source.resolve(), regressions_dir.resolve().parent)).as_posix()
    written: list[str] = []
    for row in candidates:
        path = regressions_dir / str(row['folder']) / REPLAYS_FILENAME
        record = confirm_record(path.parent)
        if record is None:
            continue
        record['clear_axis'] = CLEAR_ANYWHERE
        record['repinned'] = {'from': CLEAR_AT_CUT, 'decided': {CLEAR_AT_CUT: row[CLEAR_AT_CUT], CLEAR_ANYWHERE: row[CLEAR_ANYWHERE]}, 'run': run}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        written.append(str(row['folder']))
    return written


#: ``replay-trials --decide``: the replays a fix's verify runs to DECIDE fixed / not (the SDK's 80% count, which
#: carries its own floor of 3), never more than :data:`DECIDE_TRIALS_MAX`.
DECIDE_TRIALS_MIN, DECIDE_TRIALS_MAX = 3, 5


def _before_count(cell: Any, key: str) -> Any:
    """``key`` of a before cell; the decision count falls back to the retirement count on an older SDK's cell."""
    return cell.get(key if key in cell else 'clean_replays_to_clear') if isinstance(cell, dict) else None


def replay_trials(carried: Collection[str], *, before: dict[str, Any] | None, regressions_dir: Path, decide: bool = False) -> int:
    """How many replays the regression-only step runs: the most clean replays any carried folder's "before"
    needs to clear, clamped to 5..10. The before is the folder's row in the previous run's ``run.json`` — its
    clear-axis cell (:func:`clear_cell`) — else its record-time ``replays.json``; a before with no count asks for the floor.

    ``decide`` sizes a fix's verify instead: the clean draws the decision asks for (``clean_replays_to_decide``,
    else ``clean_replays_to_clear``) over the folders that ask for at most ``REPLAY_TRIALS_MAX``, clamped to
    3..5; a folder that asks for more is intermittent and is not sized for."""
    key = 'clean_replays_to_decide' if decide else 'clean_replays_to_clear'
    rows = {row.get('folder'): row for row in (before or {}).get('regressions') or [] if isinstance(row, dict)}
    needed: list[int] = []
    for name in carried:
        count = _before_count(clear_cell(rows.get(name) or {}), key)
        if count is None:
            recorded = _read_json(regressions_dir / name / REPLAYS_FILENAME) if (regressions_dir / name / REPLAYS_FILENAME).is_file() else None
            if isinstance(recorded, dict) and recorded.get('schema') == CONFIRM_SCHEMA:
                count = _before_count(recorded | (recorded.get('rate') if isinstance(recorded.get('rate'), dict) else {}), key)
        if _is_int(count):
            needed.append(count)
    if decide:
        sized = [count for count in needed if count <= REPLAY_TRIALS_MAX]
        return min(DECIDE_TRIALS_MAX, max(DECIDE_TRIALS_MIN, *sized)) if sized else DECIDE_TRIALS_MIN
    return min(REPLAY_TRIALS_MAX, max(CONFIRM_SAMPLES, *needed)) if needed else CONFIRM_SAMPLES


#: How a carried regression is replayed: only its issue turn (the default), or whole conversations.
SINGLE_HOP, FULL_REPLAY = 'single_hop', 'full'
#: The owners file ``ledger-owners`` writes: the eval set's ledger owner of each recorded regression.
REGRESSION_OWNERS_SCHEMA = 'regression-owners-v1'


def regression_owner(folder: Path, owners: dict[str, str]) -> str | None:
    """The check that owns one regression: the eval set's ledger owner, else the folder's ROLES.md probe."""
    if owners.get(folder.name):
        return owners[folder.name]
    roles = folder / ROLES_FILENAME
    return probe_policy(roles.read_text(encoding='utf-8')) if roles.is_file() else None


def read_owners(path: Path | None) -> dict[str, str]:
    """``{folder: ledger owner}`` from the saved owners file; ``{}`` when absent or unreadable (every folder takes its probe)."""
    doc = _read_json(path) if path is not None and path.is_file() else None
    if not isinstance(doc, dict) or doc.get('schema') != REGRESSION_OWNERS_SCHEMA or not isinstance(doc.get('owners'), dict):
        return {}
    return {str(k): str(v) for k, v in doc['owners'].items() if isinstance(v, str) and v.strip()}


_SOURCE_REGRESSION_DEFAULT = 'regression'


def _regression_rows(payload: Any) -> list[dict[str, Any]]:
    """The ``source: regression`` rows of an eval set's ``detail.obligations`` ledger, in ledger order."""
    try:
        import plurai_contracts  # noqa: PLC0415 - beside this file; absent only when the file was copied out

        source = plurai_contracts.contract('obligation_source_regression', default=_SOURCE_REGRESSION_DEFAULT)
    except ImportError:
        source = _SOURCE_REGRESSION_DEFAULT
    detail = payload.get('detail') if isinstance(payload, dict) else None
    rows = detail.get('obligations') if isinstance(detail, dict) else None
    return [row for row in rows if isinstance(row, dict) and row.get('source') == source] if isinstance(rows, list) else []


def ledger_owners(payload: Any) -> dict[str, Any]:
    """``{schema, eval_set_id, owners: {folder: owner}}`` from an eval set's ``job_status`` payload.

    The owner of a recorded regression is the ``owner`` of its ``detail.obligations`` ledger row
    (``source: regression``, ``contract_ref: <folder>``), which an edit commit keeps current. A row
    with no owner (a declined one) is left out, and its folder takes its ROLES.md probe.
    """
    owners: dict[str, str] = {}
    for row in _regression_rows(payload):
        folder, owner = str(row.get('contract_ref') or '').strip('*` '), row.get('owner')
        if folder and isinstance(owner, str) and owner.strip():
            owners[folder] = owner.strip()
    eval_set_id = payload.get('id') if isinstance(payload, dict) else None
    return {'schema': REGRESSION_OWNERS_SCHEMA, 'eval_set_id': eval_set_id, 'owners': owners}


def declined_regressions(payload: Any) -> list[dict[str, str]]:
    """``[{folder, reason}]`` — the recorded regressions whose ledger row names no owner and carries a ``declined_reason``.

    Read off the same ``detail.obligations`` rows as :func:`ledger_owners`: a declined row is on the record and no check
    grades its reply, so the issue it reproduces is ungraded by the suite. Ledger order, one entry per folder."""
    out: dict[str, str] = {}
    for row in _regression_rows(payload):
        folder, owner, reason = str(row.get('contract_ref') or '').strip('*` '), row.get('owner'), row.get('declined_reason')
        if folder and not (isinstance(owner, str) and owner.strip()) and isinstance(reason, str) and reason.strip():
            out.setdefault(folder, ' '.join(reason.split()))
    return [{'folder': folder, 'reason': reason} for folder, reason in out.items()]


def needs_full_replay(policy: Any) -> bool:
    """Whether an owner can only be measured on whole conversations, read off its TYPED declaration (never its
    name): an END-TO-END check whose violation is an absence (\"never escalated\") cannot be established on a
    conversation cut after one turn, and one that grades the scenario's goal is not run on a cut at all."""
    if not isinstance(policy, dict) or policy.get('evaluation_scope') != 'end_to_end':
        return False
    return policy.get('violation_is_absence') is True or policy.get('grades_scenario_target') is True


#: The ``--replay-plan`` file the final run's launch passes the runner (the SDK's ``REPLAY_PLAN_SCHEMA``, mirrored by value).
REPLAY_PLAN_SCHEMA = 'replay-plan-v1'
#: The record-time reason the harness reported restoring less than the pin (``verify_smoke.SEEN_ONCE_STATE_DROPPED``).
SEEN_ONCE_STATE_DROPPED = 'harness_dropped_resume_state'
#: The final stage's seed-resume probe of every resumed folder (``verify_smoke.py --seed-resume --fidelity-out``).
SEED_FIDELITY_SCHEMA = 'seed-resume-fidelity-v1'
#: Why a folder is played whole, per the typed fact that decided it (the plan file's ``why``).
WHOLE_OPENER, WHOLE_ANYWHERE, WHOLE_OWNER, WHOLE_STATE_LOST = 'opener', 'pinned_anywhere', 'owner_needs_whole', 'resume_state_lost'


def _whole_by_shape(folder: Path) -> str | None:
    """A folder played from its opener is read over the whole conversation (the SDK's ``anywhere`` axis), so a hop that
    ends on the opener would shrink it to one turn; a folder pinned on ``anywhere`` (:func:`repin`) clears there too."""
    if pinned_axis(confirm_record(folder)) == CLEAR_ANYWHERE:
        return WHOLE_ANYWHERE
    try:
        return WHOLE_OPENER if folder_shape(folder) in (SHAPE_OPENER_ONLY, SHAPE_OPENER_ROOTED) else None
    except Refused:
        return None


def state_lost_folders(regressions_dir: Path, *, before: Any = None, seed_fidelity: Any = None) -> set[str]:
    """The folders whose harness met their cut with less state than the recording had, off three typed facts: the
    record-time sample's reason (``harness_dropped_resume_state``), the final stage's seed-resume probe
    (``seed_fidelity``) and the previous run's own count (``before``: its ``run.json`` rows, ``restore_reduced``)."""
    lost: set[str] = set()
    for folder in folders(regressions_dir):
        reason = (confirm_record(folder) or {}).get('reason')
        if isinstance(reason, str) and reason.startswith(SEEN_ONCE_STATE_DROPPED):
            lost.add(folder.name)
    if isinstance(seed_fidelity, dict) and seed_fidelity.get('schema') == SEED_FIDELITY_SCHEMA:
        lost.update(str(name) for name in seed_fidelity.get('restore_reduced') or [] if isinstance(name, str))
    for row in (before or {}).get('regressions') or [] if isinstance(before, dict) else []:
        if isinstance(row, dict) and _is_int(row.get('restore_reduced')) and row['restore_reduced'] > 0 and row.get('folder'):
            lost.add(str(row['folder']))
    return lost


def replay_routes(
    names: Collection[str], *, regressions_dir: Path, owners: dict[str, str], policies: dict[str, Any], state_lost: Collection[str] = ()
) -> dict[str, str | None]:
    """``{folder: why it is played whole, or None for a single hop}``, from typed facts only: the folder's shape and
    pinned axis (:func:`_whole_by_shape`), its owner's declaration (:func:`needs_full_replay`), and a harness that met
    the cut with less state than the recording had (:func:`state_lost_folders`)."""
    routes: dict[str, str | None] = {}
    for name in names:
        owner = regression_owner(regressions_dir / name, owners)
        routes[name] = (
            _whole_by_shape(regressions_dir / name)
            or (WHOLE_OWNER if owner and needs_full_replay(policies.get(owner)) else None)
            or (WHOLE_STATE_LOST if name in state_lost else None)
        )
    return routes


def replay_plan(
    carried: Collection[str], *, regressions_dir: Path, owners: dict[str, str], policies: dict[str, Any], state_lost: Collection[str] = ()
) -> dict[str, list[str]]:
    """``{single_hop: [...], full: [...]}`` for the folders: single hop unless :func:`replay_routes` names why it is whole."""
    routes = replay_routes(carried, regressions_dir=regressions_dir, owners=owners, policies=policies, state_lost=state_lost)
    return {SINGLE_HOP: [name for name, why in routes.items() if why is None], FULL_REPLAY: [name for name, why in routes.items() if why is not None]}


def replay_plan_file(routes: dict[str, str | None]) -> dict[str, Any]:
    """The runner's ``--replay-plan`` document for ``routes``: both lists, and why each whole folder is whole."""
    return {
        'schema': REPLAY_PLAN_SCHEMA,
        SINGLE_HOP: sorted(name for name, why in routes.items() if why is None),
        FULL_REPLAY: sorted(name for name, why in routes.items() if why is not None),
        'why': {name: why for name, why in sorted(routes.items()) if why is not None},
    }


#: Where :func:`retire` moves a folder whose replays cleared, under ``$ART``; the record of why lands inside it.
RETIRED_FILENAME = 'retired.json'
RETIRED_SCHEMA = 'regression-retired-v1'
#: The clean replays each folder has gathered across successive scored runs since its defect was last caught
#: (``$ART/regression_clean_draws.json``): a 3-trial run alone never reaches the 5 a 3/3 "before" asks for.
CLEAN_DRAWS_SCHEMA = 'regression-clean-draws-v1'
CLEAN_DRAWS_FILENAME = 'regression_clean_draws.json'
#: A scenario row whose result is one of these measured nothing a retirement may count.
_UNMEASURED_RESULTS = frozenset({'error', 'errored', 'incomplete', 'not_valid', 'not_evaluated'})


def _needed(cell: Any) -> int | None:
    count = cell.get('clean_replays_to_clear') if isinstance(cell, dict) else None
    return count if _is_int(count) and count > 0 else None


def _run_measured_whole(run: Any) -> str | None:
    """Why this run.json can retire nothing at all (aborted, or scenarios that never ran), else None."""
    if not isinstance(run, dict) or not isinstance(run.get('regressions'), list):
        return 'it carries no regression rows'
    if run.get('aborted'):
        return 'the run was stopped before it finished'
    if (run.get('totals') or {}).get('missing_scenarios'):
        return 'scenarios of the set never ran'
    return None


def _clean_this_run(row: dict[str, Any], scenario_row: dict[str, Any] | None) -> int | None:
    """The clean draws this row adds, or None when it may add none: the row must be a pinned regression with no invalid
    replay, no state loss on either axis and no failed or refused tool call (the SDK's sums, else its scenario's run.json
    row), and its clear cell must have caught nothing. A hop whose premise never arose (``trigger_absent``) is no clean
    draw."""
    if row.get('status') != 'pinned' or row.get('invalid') or (_is_int(row.get('restore_reduced')) and row['restore_reduced'] > 0):
        return None
    tool_rows = [row, scenario_row or {}]
    if any(int(r.get(key) or 0) for r in tool_rows for key in ('tool_errors', 'tool_refusals')):
        return None
    if scenario_row is not None and str(scenario_row.get('result') or '') in _UNMEASURED_RESULTS:
        return None
    cell = clear_cell(row)
    if not isinstance(cell, dict) or int(cell.get('failed') or 0):
        return None
    # A red the reading did not catch (linked to another finding, or `none`) is still a red on the owner: never a clean run.
    if int((cell.get('sdk') or {}).get('failed') or 0):
        return None
    import red_cells  # noqa: PLC0415, F401 - puts the run scripts on sys.path
    from build_run_json import measured_replays  # noqa: PLC0415 - the one "measured" rule

    clean = measured_replays(cell)  # nothing failed: every measured replay is a clean draw
    return clean if clean > 0 else None


def update_clean_draws(run: Any, *, run_id: str, before: Any, regressions_dir: Path, ledger: Any) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    """``(ledger, candidates, skipped)``: this run's rows folded into the per-folder clean-draw ledger, and the folders
    it now retires on the SDK's own clear verdict, fix or no fix — at least as many clean draws, gathered across scored
    runs since the defect was last caught, as its before asks for. A row that caught the defect resets the folder to 0
    and its own ``clean_replays_to_clear`` becomes the before; otherwise the before is the ledger's, else the previous
    run's row, else the folder's record-time ``replays.json``. A run already folded in, or one that measured nothing
    whole (``skipped`` says why), changes nothing. No age-out and no cap."""
    book: dict[str, Any] = ledger if isinstance(ledger, dict) and ledger.get('schema') == CLEAN_DRAWS_SCHEMA else {'schema': CLEAN_DRAWS_SCHEMA, 'folders': {}}
    folders_book: dict[str, Any] = book.setdefault('folders', {})
    if (skipped := _run_measured_whole(run)) is not None:
        return book, [], skipped
    prior = {r.get('folder'): r for r in (before or {}).get('regressions') or [] if isinstance(r, dict)} if isinstance(before, dict) else {}
    scenarios = {str(r.get('id')): r for r in run.get('scenarios') or [] if isinstance(r, dict)}
    out: list[dict[str, Any]] = []
    for row in run['regressions']:
        name = row.get('folder') if isinstance(row, dict) else None
        if not isinstance(name, str) or not (regressions_dir / name).is_dir():
            continue
        entry = folders_book.setdefault(name, {'clean': 0, 'needed': None, 'runs': []})
        if run_id in entry['runs']:
            continue
        entry['runs'].append(run_id)
        # Clean draws count on ONE check: a run read on another owner starts the count again, never adds a draw of a check
        # that may never have measured the defect.
        if row.get('owner') and entry.get('owner') not in (None, row['owner']):
            entry['clean'], entry['needed'] = 0, None
        if row.get('owner'):
            entry['owner'] = row['owner']
        cell = clear_cell(row)
        if isinstance(cell, dict) and int(cell.get('failed') or 0):
            entry['clean'], entry['needed'] = 0, _needed(cell) or entry.get('needed')
            continue
        added = _clean_this_run(row, scenarios.get(str(row.get('scenario') or name)))
        if added is None:
            continue
        entry['clean'] += added
        needed = entry.get('needed') or _needed(clear_cell(prior.get(name) or {})) or _needed(confirm_record(regressions_dir / name))
        entry['needed'] = needed
        if needed is not None and entry['clean'] >= needed:
            axis = row.get('clear_axis') if row.get('clear_axis') in (CLEAR_AT_CUT, CLEAR_ANYWHERE) else CLEAR_AT_CUT
            out.append({'folder': name, 'axis': axis, 'clean': entry['clean'], 'needed': needed, 'runs': list(entry['runs'])})
    return book, out, None


def retire(candidates: Collection[dict[str, Any]], *, regressions_dir: Path, retired_dir: Path, run: Path, run_id: str) -> list[str]:
    """Move each candidate folder out of ``regressions_dir`` into ``retired_dir`` (local, beside the run artifacts),
    with a ``retired.json`` naming the run and the clean draws that retired it, and rewrite ``BUNDLE.md``. An earlier
    retired copy under that name is kept: this one takes the run id as a suffix. Returns the folders moved."""
    moved: list[str] = []
    for row in candidates:
        target = _move_out(regressions_dir / str(row['folder']), retired_dir, run_id)
        if target is None:
            continue
        record = {'schema': RETIRED_SCHEMA, 'run': run.as_posix(), **row}
        (target / RETIRED_FILENAME).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        moved.append(str(row['folder']))
    if moved:
        _rewrite_overview(regressions_dir)
    return moved


def _move_out(source: Path, to_dir: Path, run_id: str) -> Path | None:
    """Move a folder out of the regressions directory into ``to_dir``; an earlier copy under that name is kept and this one
    takes the run id as a suffix, then a counter, until the name is free (never moved INTO an earlier copy). None when the
    folder is not on disk (already moved)."""
    if not source.is_dir():
        return None
    to_dir.mkdir(parents=True, exist_ok=True)
    target = to_dir / source.name
    if target.exists():
        target = to_dir / f'{source.name}.{run_id}'
    n = 2
    while target.exists():
        target, n = to_dir / f'{source.name}.{run_id}.{n}', n + 1
    shutil.move(str(source), str(target))
    return target


def _rewrite_overview(regressions_dir: Path) -> None:
    try:
        (regressions_dir / OVERVIEW_FILENAME).write_text(overview_text(regressions_dir), encoding='utf-8')
    except Refused as exc:
        print(f'NOTE: {OVERVIEW_FILENAME} was not rewritten: {exc}', file=sys.stderr)


#: Where a folder dropped from the kept set moves, under ``$ART`` (gitignored, local): a hollow one (:func:`hollow_reason`)
#: and one a more severe finding evicted from the build's cap. Not ``retired_regressions/``: a retired name is filtered out of
#: every later run (:func:`retired_names`), and a dropped finding is recorded again under the same name.
DROPPED_DIRNAME = 'dropped_regressions'
DROPPED_FILENAME = 'dropped.json'
DROPPED_SCHEMA = 'regression-dropped-v1'
DROP_HOLLOW, DROP_EVICTED = 'hollow', 'evicted'


def drop(entries: Collection[Mapping[str, Any]], *, regressions_dir: Path, dropped_dir: Path, run_name: str, clean_draws: Path | None) -> list[str]:
    """Move each ``{folder, kind, reason}`` folder into ``dropped_dir`` with a ``dropped.json`` naming the run, the folder, its
    finding, the kind (:data:`DROP_HOLLOW` / :data:`DROP_EVICTED`) and the reason; clear each dropped folder's entry from the
    clean-draw ledger at ``clean_draws`` (:func:`forget_clean_draws`: its finding is recorded again under the same name, and
    that folder starts at zero); and rewrite ``BUNDLE.md``. A folder no longer on disk is skipped, so a second call moves
    nothing, while the ledger and ``BUNDLE.md`` are still brought in line (a call that stopped half way is finished).
    Reversible: move the folder back. Returns the folders moved."""
    moved: list[str] = []
    for entry in entries:
        name = str(entry['folder'])
        finding_id = recorded_finding(regressions_dir / name)
        target = _move_out(regressions_dir / name, dropped_dir, run_name)
        if target is None:
            continue
        record = {'schema': DROPPED_SCHEMA, 'run': run_name, 'folder': name, 'finding_id': finding_id,
                  'kind': entry.get('kind'), 'reason': entry.get('reason')}
        (target / DROPPED_FILENAME).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        moved.append(name)
    if entries:
        forget_clean_draws(clean_draws, [str(entry['folder']) for entry in entries if not (regressions_dir / str(entry['folder'])).is_dir()])
        if regressions_dir.is_dir():
            _rewrite_overview(regressions_dir)
    return moved


def forget_clean_draws(ledger: Path | None, names: Collection[str]) -> None:
    """Remove each named folder's entry from the clean-draw ledger (:func:`update_clean_draws`' book), so a folder recorded
    again under that name never inherits the clean draws of the one dropped. Only folders no longer on disk are named here.
    A ledger that is absent, unreadable or names none of them is left as it is."""
    if ledger is None or not names or not ledger.is_file():
        return
    try:
        book = json.loads(ledger.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        print(f'WARNING: {ledger} could not be read ({exc}); the clean draws of {", ".join(names)} were not cleared.', file=sys.stderr)
        return
    folders_book = book.get('folders') if isinstance(book, dict) else None
    if not isinstance(folders_book, dict) or not any(name in folders_book for name in names):
        return
    for name in names:
        folders_book.pop(name, None)
    try:
        ledger.write_text(json.dumps(book, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError as exc:
        print(f'WARNING: {ledger} could not be written ({exc}); the clean draws of {", ".join(names)} were not cleared.', file=sys.stderr)


def dropped_records(dropped_dir: Path) -> list[dict[str, Any]]:
    """Every ``dropped.json`` under ``dropped_dir``; none when it is absent. An unreadable one is skipped."""
    if not dropped_dir.is_dir():
        return []
    out = []
    for folder in sorted(dropped_dir.iterdir()):
        try:
            record = json.loads((folder / DROPPED_FILENAME).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get('schema') == DROPPED_SCHEMA:
            out.append(record)
    return out


def hollow_rows(run: Any) -> list[dict[str, Any]]:
    """The ``{folder, kind, reason}`` drops a run.json asks for: each regression row it marks hollow. None from a run that
    could retire nothing (:func:`_run_measured_whole`: stopped, or scenarios that never ran)."""
    if _run_measured_whole(run) is not None:
        return []
    return [{'folder': str(r['folder']), 'kind': DROP_HOLLOW, 'reason': r['hollow']}
            for r in run['regressions'] if isinstance(r, dict) and r.get('folder') and r.get('hollow')]


def retired_names(retired_dir: Path) -> set[str]:
    """The regression folders retired under ``retired_dir`` (a suffixed copy names its folder once)."""
    if not retired_dir.is_dir():
        return set()
    return {d.name.split('.', 1)[0] for d in retired_dir.iterdir() if d.is_dir() and d.name.startswith(FOLDER_PREFIX) and (d / RETIRED_FILENAME).is_file()}


# ── commands ──────────────────────────────────────────────────────────────────


def cmd_pick(args: argparse.Namespace) -> int:
    # Removed before choosing, so a refusal leaves no earlier pick behind to be read as this one.
    if args.out is not None:
        args.out.unlink(missing_ok=True)
    if args.plan is not None or args.finding is not None:
        if args.plan is None or args.finding is None:
            raise Refused('--plan and --finding go together: the plan row of that finding is the whole choice.')
        hand = [flag for flag, value in (('--policy', args.policy), ('--scenario', args.scenario), ('--cut-turn', args.cut_turn),
                                         ('--seen-in', args.seen_in), ('--exclude-scenario', args.exclude_scenario),
                                         ('--exclude-cell', args.exclude_cell)) if value not in (None, [])]
        if hand:
            raise Refused(f'{", ".join(hand)} cannot be combined with --plan: the plan row names the policy, the conversations, '
                          'the cuts and the bench. Pick by hand without --plan, or plan again.')
        plan = _read_json(args.plan)
        row = next((r for r in (plan.get('record') or [] if isinstance(plan, dict) else []) if isinstance(r, dict) and r.get('finding_id') == args.finding), {})
        if args.out is not None and isinstance(plan, dict) and not plan.get('acceptance') and row.get('folder'):
            # A build's record step: counted now, so a build that dies before its auto-report still caps its re-plan.
            # The ledger counts a folder only while it is on disk, so a refused or rare-at-cut record costs nothing.
            add_to_ledger(args.workdir / 'artifacts' / BUILD_LEDGER_FILENAME, [str(row['folder'])])
        picked = pick_from_plan(args.run_dir, plan, args.finding, resume_gap=resume_unsupported(args.workdir),
                                regressions_dir=args.workdir / REGRESSIONS_DIRNAME)
    else:
        picked = choose(
            args.run_dir, policy=args.policy, scenario=args.scenario, cut_turn=args.cut_turn, exclude=args.exclude_scenario,
            exclude_cells=args.exclude_cell,
            within=args.seen_in, resume_gap=resume_unsupported(args.workdir), workdir=args.workdir,
        )
    text = json.dumps(picked, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0


def cmd_ledger_owners(args: argparse.Namespace) -> int:
    try:
        raw = sys.stdin.read() if str(args.payload) == '-' else args.payload.read_text(encoding='utf-8')
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise Refused(f'the job_status payload could not be read as JSON ({type(exc).__name__}: {exc}).') from exc
    if args.declined:
        declined = declined_regressions(payload)
        print(json.dumps({'declined_regressions': declined} if declined else {}, ensure_ascii=False))
        return 0
    print(json.dumps(ledger_owners(payload), indent=2))
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    roles_text = sys.stdin.read() if str(args.roles_file) == '-' else args.roles_file.read_text(encoding='utf-8')
    if args.from_finding is not None and args.finding_id is not None:
        raise Refused('--from-finding already records the finding id; pass one or the other.')
    if args.from_finding is not None:
        roles_text = suite_build_paragraph(
            roles_text, slug=args.slug, finding_id=args.from_finding, regressions_dir=args.regressions_dir,
        )
    elif args.finding_id is not None:
        roles_text = finding_paragraph(roles_text, slug=args.slug, finding_id=args.finding_id, regressions_dir=args.regressions_dir)
    folder = write_folder(
        args.regressions_dir,
        slug=args.slug,
        roles_text=roles_text,
        first_talk_from=args.first_talk_from,
        issue_from=args.issue_from,
        cut_turn=args.cut_turn,
        trigger=args.trigger,
    )
    print(folder)
    return 0


def cmd_carry_world_facts(args: argparse.Namespace) -> int:
    status, message = carry_world_facts(args.source, args.into)
    if status == NOT_CARRIED:
        print(f'WARNING: {message}', file=sys.stderr)
    else:
        print(message)
    return 0


def cmd_overview(args: argparse.Namespace) -> int:
    args.regressions_dir.mkdir(parents=True, exist_ok=True)
    path = args.regressions_dir / OVERVIEW_FILENAME
    path.write_text(overview_text(args.regressions_dir), encoding='utf-8')
    print(path)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    present = folders(args.regressions_dir)
    if not present:
        print(f'No regressions under {args.regressions_dir}.')
        return 0

    shipped: set[str] | None = None
    if args.shipped_from is not None:
        raw = sys.stdin.read() if str(args.shipped_from) == '-' else args.shipped_from.read_text(encoding='utf-8')
        shipped = {line.strip() for line in raw.splitlines() if line.strip()}

    # An unfinished folder is kept out of the carried/missing accounting altogether. "Carried" is
    # decided by name, and the backend names its cold-authored scenario by the folder's slug — so
    # the name IS in the shipped list, and counting it would report the one folder that reproduces
    # nothing as the one that is covered. It is reported through the refusal below instead.
    missing: list[str] = []
    unfinished: list[str] = []
    rows = []
    for folder in present:
        try:
            shape = folder_shape(folder)
        except Refused as exc:
            print(f'INCOMPLETE  {folder.name}: {exc}', file=sys.stderr)
            unfinished.append(folder.name)
            rows.append(f'{folder.name}\tINCOMPLETE')
            continue
        carried = ''
        if shipped is not None:
            if folder.name in shipped:
                carried = '  carried'
            else:
                carried = '  NOT IN SET'
                missing.append(folder.name)
        rows.append(f'{folder.name}\t{shape}{carried}')
    print('\n'.join(rows))

    if missing:
        print(
            f'\n{len(missing)} regression(s) on disk that this scenario set does not carry: {", ".join(missing)}. '
            'A regression reaches a set only through the final batch, so these are covered from the next suite build '
            'on. Until then their issues are unguarded by this run.',
        )
    if unfinished:
        raise Refused(
            f'{len(unfinished)} folder(s) replay nothing: {", ".join(unfinished)}. The backend does not refuse such a '
            'folder — it authors a scenario for it cold from ROLES.md, under the folder\'s name, so the set carries a '
            'scenario by the regression\'s name that reproduces nothing. Finish the record or delete the folder before '
            'the next suite build; it is never counted as carried.'
        )
    return 1 if missing else 0


def cmd_finding(args: argparse.Namespace) -> int:
    finding = find_confirmed(args.findings, args.finding_id)
    slug = finding_slug(args.finding_id, args.regressions_dir)
    print(json.dumps({**finding._asdict(), 'seen_in': list(finding.seen_in), 'slug': slug}, indent=2))
    return 0


def cmd_finding_slug(args: argparse.Namespace) -> int:
    print(finding_slug(args.finding_id, args.regressions_dir))
    return 0


def cmd_auto_plan(args: argparse.Namespace) -> int:
    text = None
    if not (args.static_only or args.no_auto_regressions):
        if not (args.run or '').strip() or not Path(args.run).is_dir() or not scenario_dirs(Path(args.run)):
            raise Refused(
                f'--run {args.run!r} is not a run directory holding scenario transcripts — name the qualification run the '
                'record cuts from, or plan with --static-only when no run is on record.'
            )
        try:
            text = args.findings.read_text(encoding='utf-8')
        except FileNotFoundError:
            text = ''
        except (OSError, ValueError) as exc:
            raise Refused(f'{args.findings} could not be read ({type(exc).__name__}: {exc}).') from exc
    import record_finding  # noqa: PLC0415 - sibling: the findings store

    run_dir = Path(args.run) if args.run else None
    bench = {'scenarios': list(args.bench_scenario), 'cells': list(args.bench_cell), 'policies': list(args.bench_policy),
             'harness_wide': list(args.harness_wide), 'accepted_harness': list(args.accepted_harness)}
    if args.bench_from is not None:
        # The suite's own post-final plan: its bench block is this run's, read from typed data rather than re-passed by hand.
        source = _read_json(args.bench_from)
        if not isinstance(source, dict) or not isinstance(source.get('bench'), dict):
            raise Refused(f'--bench-from {args.bench_from} is not a plan written by auto-plan.')
        if run_dir is None or source.get('run') != run_dir.name:
            raise Refused(f'--bench-from {args.bench_from} was planned from runs/{source.get("run") or "(no run)"}, not --run {args.run}: its bench '
                          "block is that run's. Pass this run's diagnosed bench flags instead.")
        bench = {key: [*(source['bench'].get(key) or []), *bench[key]] for key in bench}
    if args.bench_cells_from_audit and run_dir is not None:
        bench['cells'] = audit_bench_cells(run_dir, bench['cells'])
    try:
        rows = record_finding.read_rows(args.findings.parent / 'agent_findings.jsonl')
    except (OSError, ValueError) as exc:
        print(f'WARNING: the findings store beside {args.findings} could not be read ({exc}); every finding plans from its section.', file=sys.stderr)
        rows = {}
    if args.acceptance and args.counted_plan is not None:
        raise Refused('--counted-plan counts a suite build\'s folders; a fix plan (--acceptance) has its own bound and no build ledger.')
    incumbents = [] if args.acceptance else build_incumbents(args.findings.parent / BUILD_LEDGER_FILENAME, args.regressions_dir, args.counted_plan)
    plan = plan_auto_regressions(
        text,
        args.regressions_dir,
        run_dir=run_dir,
        bench_scenarios=bench['scenarios'],
        bench_cells=bench['cells'],
        bench_policies=bench['policies'],
        harness_wide=bench['harness_wide'],
        accepted_harness=bench['accepted_harness'],
        cap=args.cap,
        spent=len(incumbents),
        opted_out=args.no_auto_regressions,
        static_only=args.static_only,
        rows=rows,
        acceptance=args.acceptance,
        incumbents=incumbents,
    )
    print(json.dumps(plan, indent=2))
    return 0


def cmd_fixes_lines(args: argparse.Namespace) -> int:
    plan = _read_json(args.plan)
    if not isinstance(plan, dict):
        raise Refused(f'{args.plan} is not a plan written by auto-plan.')
    import scored_findings  # noqa: PLC0415 - the scored-run reader

    _mark_unplayed(plan, scored_findings.scored_run_dir(args.plan.resolve().parent.parent))
    for finding_id, line in fixes_lines(plan):
        print(f'{finding_id}\t{line}')
    return 0


def _spent(plan_path: Path | None, regressions_dir: Path) -> int:
    """How many folders one earlier plan file wrote: its ``record`` rows whose folder is on disk. ``auto-plan`` counts the
    build's ledger instead (:func:`build_spent`), which ``--counted-plan`` feeds."""
    if plan_path is None:
        return 0
    plan = _read_json(plan_path)
    if not isinstance(plan, dict):
        raise Refused(f'{plan_path} is not a plan written by auto-plan.')
    folders = {str(row.get('folder') or '') for row in plan.get('record') or [] if isinstance(row, dict)}
    return sum(1 for folder in folders if folder and (regressions_dir / folder).is_dir())


def page_issues(workdir: Path, findings_text: str | None) -> tuple[list[str | None], list[str | None]]:
    """``(confirmed, on_page)``: the sections of the page's copy of the findings file (``scored_findings.copy_for_page``)
    that read confirmed, and those plus every one it still shows whose store row is confirmed — a finding the copy shows
    confirmed while calibrating is still ranked and diagnosed, and the gate planned it, so its folder is
    kept, never "recorded, but not among the confirmed findings" (:func:`regression_tally`). A plain potential is neither."""
    import record_finding  # noqa: PLC0415 - sibling: the findings store
    import scored_findings  # noqa: PLC0415 - THE page projection (publish_report.page_findings reads the same)

    page_md, _ = scored_findings.copy_for_page(workdir, findings_text, None)
    try:
        rows = record_finding.read_rows(workdir / 'artifacts' / 'agent_findings.jsonl')
    except (OSError, ValueError):
        rows = {}
    sections = parse_findings(page_md or '')
    return ([finding.finding_id for finding in sections if finding.status == STATUS_CONFIRMED],
            [finding.finding_id for finding in sections if finding.status == STATUS_CONFIRMED
             or (finding.status == STATUS_POTENTIAL and (rows.get(finding.finding_id or '') or {}).get('status') == STATUS_CONFIRMED)])


def _shown(plan: Mapping[str, Any], workdir: Path, unwritten: Collection[str]) -> dict[str, Any]:
    """The plan as the page counts it: each row under the id the page folds it into (``scored_findings.page_folds``: a
    finding folded into another is that one's defect, so its folder guards the id the page shows), ``unwritten`` record rows
    (refused, rare) left out, and each row covered by a folder the scored run did not replay marked ``unplayed``."""
    import scored_findings  # noqa: PLC0415

    folds = scored_findings.page_folds(workdir)
    shown = {**plan, **{key: [{**row, 'finding_id': folds.get(row.get('finding_id'), row.get('finding_id'))}
                              for row in plan.get(key) or [] if isinstance(row, dict) and not (key == 'record' and row.get('finding_id') in unwritten)]
                        for key in ('record', 'skipped')}}
    # An eviction that did not happen (its evicting folder was never written) leaves the folder covering its finding.
    for row in shown['skipped']:
        if row.get('evicted') and (workdir / REGRESSIONS_DIRNAME / str(row['evicted'])).is_dir():
            row.update(covered_by=row.pop('evicted'), kind=SKIP_COVERED)
            row.pop('evicted_by', None)
    _mark_unplayed(shown, scored_findings.scored_run_dir(workdir))
    return shown


def _pipeline(workdir: Path) -> bool:
    """Whether no conversation of the scored run holds a user turn."""
    import scored_findings  # noqa: PLC0415

    run_dir = scored_findings.scored_run_dir(workdir)
    return run_dir is not None and _without_a_turn(list(run_conversations(run_dir).values()))


AUTO_PLAN_FILENAME = 'auto_regressions.json'
#: ``/simulation-fixes``' plan (``auto-plan --acceptance``), whose lines ``fixes.md`` quotes.
FIX_PLAN_FILENAME = 'fixes_regressions.json'


def fresh_fix_plan(workdir: Path) -> dict[str, Any] | None:
    """The fix plan when it speaks for the build plan's run: made from the scored run, and no older than ``auto_regressions.json``
    (``$ART`` outlives a build, so an earlier build's fix plan is ignored). None otherwise, or when either cannot be read."""
    import stage_landed  # noqa: PLC0415 - sibling, stdlib only: THE scored-run reader

    art = workdir / 'artifacts'
    try:
        plan = json.loads((art / FIX_PLAN_FILENAME).read_text(encoding='utf-8'))
        fresh = (art / FIX_PLAN_FILENAME).stat().st_mtime >= (art / AUTO_PLAN_FILENAME).stat().st_mtime
    except (OSError, ValueError):
        return None
    scored = stage_landed.scored_run_json(workdir)
    if not isinstance(plan, dict) or not fresh or scored is None or plan.get('run') != scored.parent.name:
        return None
    return plan


def page_copy(workdir: Path) -> str | None:
    """The page's copy of ``artifacts/auto_regressions.json`` (the gate's plan): its "Kept as regressions" and "gaps not
    kept" lists rendered from :func:`regression_tally`, the count the chat line says, in the page schema's keys alone.

    - ``record``: one row per issue that has a regression — recorded by this build, covered by an earlier folder, or
      recorded and not yet measured — with ``folder`` its first folder (one row per issue after the page's folds);
    - ``skipped``: one row per issue with no regression yet (or that cannot be replayed), ``folder`` null, ``reason`` the
      fix plan's own customer wording (:func:`fixes_line`, code marks dropped), or null when the plan holds no such row
      (a refused or rare record, an issue confirmed after the plan was made). A row the build left over its cap reads the
      fresh fix plan's line for it instead (:func:`fresh_fix_plan`: its record or skipped row), as ``fixes.md`` does.

    A folder written for an issue the page does not show (``dropped``), and a refused or rare record row, is in neither
    list, as the chat names but never counts it. The plan's operator reasons never reach the page. None when the plan
    cannot be read; an empty copy for a static-only or opted-out build."""
    path = workdir / 'artifacts' / AUTO_PLAN_FILENAME
    try:
        plan = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(plan, dict):
        return None
    out: dict[str, list[dict[str, Any]]] = {'record': [], 'skipped': []}
    if plan.get('nothing_because') is None:
        refused = plan.get(PLAN_REFUSED_KEY) if isinstance(plan.get(PLAN_REFUSED_KEY), dict) else {}
        rare = plan.get(PLAN_RARE_KEY) if isinstance(plan.get(PLAN_RARE_KEY), list) else []
        unwritten = {*map(str, refused), *map(str, rare)}
        try:
            text = (workdir / 'artifacts' / FINDINGS_FILENAME).read_text(encoding='utf-8')
        except (OSError, ValueError):
            text = ''
        confirmed, issues = page_issues(workdir, text)
        shown = _shown(plan, workdir, unwritten)
        tally = regression_tally(shown, confirmed=confirmed, on_page=issues, pipeline=_pipeline(workdir))
        rows = {}
        for row in [*shown.get('record', []), *shown.get('skipped', [])]:
            rows.setdefault(row.get('finding_id'), row)  # the plan's first row for the issue: its record row, else its skip
        folders: dict[Any, str] = {row.get('finding_id'): str(row['folder']) for row in reversed(tally['recorded'])}
        folders.update({fid: names[0] for fid, names in tally['covering'].items()})
        folders.update(tally['waiting'])
        sections = {f.finding_id: f for f in parse_findings(text) if f.finding_id}
        for fid in dict.fromkeys(fid for fid in issues if fid in folders):
            row = rows.get(fid) or {}
            section = sections.get(fid)
            out['record'].append({'finding_id': fid, 'number': section.number if section is not None else row.get('number'), 'title': _title(row, section),
                                  'severity': row.get('severity'), 'confidence': row.get('confidence'), 'policy': row.get('policy'),
                                  'folder': folders[fid]})
        import scored_findings  # noqa: PLC0415

        folds = scored_findings.page_folds(workdir)
        rare_shown = {folds.get(str(r), str(r)) for r in rare}
        fix_rows: dict[Any, Mapping[str, Any]] = {}
        fix_plan = fresh_fix_plan(workdir) or {}
        # The fix plan's row for the issue, record or skipped (its first, as `fixes.md` reads it): one state on both surfaces.
        for fix_row in [*(fix_plan.get('record') or []), *(fix_plan.get('skipped') or [])]:
            if isinstance(fix_row, dict) and fix_row.get('finding_id'):
                fix_rows.setdefault(folds.get(fix_row['finding_id'], fix_row['finding_id']), fix_row)
        for fid in [*tally['missing'], *tally['unreplayable']]:
            row = rows.get(fid) if fid is not None else None
            # One state: a finding the build left over its cap reads the fresh fix plan's own line (fixes.md's), when it has one.
            overlay = fix_rows.get(fid) if row is not None and row.get('kind') == SKIP_OVER_CAP else None
            said = (fixes_line(overlay).replace('`', '') if overlay is not None
                    else fixes_line(row).replace('`', '') if row is not None and fid not in unwritten and 'reason' in row else None)
            if row is None and fid in tally['unreplayable']:
                said = FIXES_LINES[SKIP_UNREPLAYABLE]
            if said is None and fid in rare_shown:  # a refused record stays null: its refusal is operator text
                said = f'n/a — the issue did not happen again at its recorded turn in {CONFIRM_SAMPLES} replays, so no regression pins it yet'
            out['skipped'].append({'finding_id': fid, 'title': _title(row or {}, sections.get(fid or '')),
                                   'severity': (row or {}).get('severity'), 'folder': None, 'reason': said})
    return json.dumps(out, indent=2) + '\n'


def _title(row: Mapping[str, Any], section: Finding | None) -> str:
    """The page section's title (the issue's primary, after folds), else the plan row's, else its id."""
    return str((section.title if section is not None else '') or row.get('title') or row.get('finding_id') or '')


def cmd_auto_report(args: argparse.Namespace) -> int:
    plan = _read_json(args.plan)
    if not isinstance(plan, dict):
        raise Refused(f'{args.plan} is not a plan written by auto-plan.')
    refused: dict[str, str] = {}
    for item in args.refused:
        finding_id, sep, reason = item.partition('=')
        if not sep or not finding_id.strip() or not reason.strip():
            raise Refused(f'--refused {item!r} is not <finding id>=<reason>.')
        refused[finding_id.strip()] = reason.strip()
    planned = {row.get('finding_id') for row in plan.get('record', [])}
    stray = sorted((set(refused) | set(args.rare)) - planned)
    if stray:
        raise Refused(f'--refused/--rare name {", ".join(stray)}, which the plan did not record. Name a finding from its record list.')
    # The record step's outcomes ride in the plan, so the hand-off re-renders the line with --findings alone.
    kept_refused = plan.get(PLAN_REFUSED_KEY) if isinstance(plan.get(PLAN_REFUSED_KEY), dict) else {}
    kept_rare = plan.get(PLAN_RARE_KEY) if isinstance(plan.get(PLAN_RARE_KEY), list) else []
    refused = {**{str(k): str(v) for k, v in kept_refused.items()}, **refused}
    rare = sorted({*map(str, kept_rare), *args.rare})
    if args.refused or args.rare:
        plan[PLAN_REFUSED_KEY], plan[PLAN_RARE_KEY] = refused, rare
        try:
            args.plan.write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
        except OSError as exc:
            raise Refused(f'{args.plan} could not be written ({type(exc).__name__}: {exc}).') from exc
    workdir = args.findings.resolve().parent.parent
    if not plan.get('acceptance'):
        # The build's ledger: every folder this build's record step wrote, whichever plan file listed it (`auto-plan` counts them).
        try:
            add_to_ledger(args.findings.parent / BUILD_LEDGER_FILENAME,
                          [str(row['folder']) for row in plan.get('record') or [] if isinstance(row, dict) and row.get('folder')
                           and row.get('finding_id') not in refused and row.get('finding_id') not in rare])
        except OSError as exc:
            print(f'WARNING: the build ledger beside {args.findings} could not be written ({exc}); a re-plan of this build may record past its cap.',
                  file=sys.stderr)
        # An eviction the plan named happens only once the evicting folder is on disk: a refused or rare record moves nothing.
        for name in evict(plan, regressions_dir=workdir / REGRESSIONS_DIRNAME, dropped_dir=args.findings.parent / DROPPED_DIRNAME,
                          clean_draws=args.findings.parent / CLEAN_DRAWS_FILENAME, unwritten={*refused, *rare}):
            print(f'evicted: `{name}` gave its place to a more severe finding (moved to {DROPPED_DIRNAME}/)', file=sys.stderr)
    try:
        text = args.findings.read_text(encoding='utf-8')
    except FileNotFoundError:
        text = ''
    except (OSError, ValueError) as exc:
        raise Refused(f'{args.findings} could not be read ({type(exc).__name__}: {exc}).') from exc
    import scored_findings  # noqa: PLC0415 - THE page projection (publish_report.page_findings reads the same)

    # Counted off the copy the page shows ($ART/agent_findings.md → $WORKDIR): unchanged until a scored final run exists.
    # The page's own "Kept as regressions" list is the same count (`page_copy`).
    confirmed, on_page = page_issues(workdir, text)
    for name, why in auto_report_reasons(plan, refused, rare):
        print(f'not recorded: `{name}` — {why}', file=sys.stderr)
    shown = _shown(plan, workdir, {*refused, *rare})
    run_dir = scored_findings.scored_run_dir(workdir)
    if run_dir is not None and plan.get('run') and _run_named(str(plan['run'])) != run_dir.name:
        print(f'note: the plan was made from {plan["run"]}, not the scored run {run_dir.name}', file=sys.stderr)
    print(auto_report_line(shown, confirmed=confirmed, pipeline=_pipeline(workdir), on_page=on_page))
    return 0


def evict(plan: Mapping[str, Any], *, regressions_dir: Path, dropped_dir: Path, clean_draws: Path | None, unwritten: Collection[str] = ()) -> list[str]:
    """Move out each folder a build plan's record row ``evicts`` (:func:`plan_auto_regressions`), once that row's own folder is
    on disk; a refused or rare record (``unwritten``) moves nothing. Idempotent: a moved folder is not moved again. Its
    clean draws are cleared (:func:`drop`)."""
    entries = [{'folder': str(row['evicts']), 'kind': DROP_EVICTED, 'reason': f'a more severe finding ({row.get("finding_id")}) took its place'}
               for row in plan.get('record') or [] if isinstance(row, dict) and row.get('evicts') and row.get('folder')
               and row.get('finding_id') not in unwritten and (regressions_dir / str(row['folder'])).is_dir()]
    return drop(entries, regressions_dir=regressions_dir, dropped_dir=dropped_dir, run_name=_run_named(str(plan.get('run') or '')) or 'unknown',
                clean_draws=clean_draws)


def removed_folders(plan: Mapping[str, Any]) -> dict[str, str]:
    """``{folder: kind}`` of every earlier folder the plan's rows say was dropped and NOT recorded again: an eviction that
    happened (``evicted``) and a skipped finding's ``removed`` folder. A record row's ``removed`` folder is recorded again
    (:func:`rerecorded_folders`), so it is kept, never said removed."""
    out: dict[str, str] = {}
    for row in plan.get('skipped') or []:
        if not isinstance(row, dict):
            continue
        if row.get('evicted'):
            out.setdefault(str(row['evicted']), DROP_EVICTED)
        removed = row.get('removed')
        if isinstance(removed, Mapping) and removed.get('folder') and removed.get('kind') in REMOVED_NOTES:
            out.setdefault(str(removed['folder']), str(removed['kind']))
    return out


def rerecorded_folders(plan: Mapping[str, Any]) -> dict[str, str]:
    """``{folder: kind}`` of every record row whose finding's earlier folder was dropped (``removed``): its new folder, and
    why the earlier one went."""
    out: dict[str, str] = {}
    for row in plan.get('record') or []:
        removed = row.get('removed') if isinstance(row, dict) else None
        if isinstance(removed, Mapping) and removed.get('kind') in REMOVED_NOTES and row.get('folder'):
            out.setdefault(str(row['folder']), str(removed['kind']))
    return out


def removed_sentence(plan: Mapping[str, Any]) -> str:
    """The customer's sentences about the earlier replays this plan's rows say were removed, or ''. A finding recorded
    again is said as recorded again (its folder is kept), never as removed."""
    gone, again = removed_folders(plan), rerecorded_folders(plan)
    why = {DROP_EVICTED: 'a more severe issue took its place', DROP_HOLLOW: 'it could not reach the moment its issue happened'}
    said = []
    if len(gone) == 1:
        name, kind = next(iter(gone.items()))
        said.append(f'`{name}` was removed from the kept replays: {why[kind]}.')
    elif gone:
        said.append(f'{len(gone)} earlier replays were removed from the kept replays: '
                    + ', '.join(f'`{name}` ({why[kind]})' for name, kind in gone.items()) + '.')
    said += [f'`{name}` was recorded again: {REMOVED_NOTES[kind]}.' for name, kind in again.items()]
    return ' '.join(said)


def _mark_unplayed(plan: Mapping[str, Any], run_dir: Path | None) -> None:
    """Mark ``unplayed`` each plan row covered by a folder the scored run did not replay although it replayed regressions:
    the folder was recorded after that run. It still covers its finding; only its line says so (:data:`RECORDED_AFTER`)."""
    summary = _read_json(run_dir / 'run_summary.json') if run_dir is not None and (run_dir / 'run_summary.json').is_file() else None
    names = summary.get('scenarios') if isinstance(summary, dict) else None
    played = {str(s) for s in names if str(s).startswith(FOLDER_PREFIX)} if isinstance(names, dict) else set()
    for row in (plan.get('skipped') or []) if played else []:
        if isinstance(row, dict) and row.get('covered_by') and row['covered_by'] not in played:
            row['unplayed'] = True


def _json_arg(path: Path) -> Any:
    if str(path) == '-':
        try:
            return json.loads(sys.stdin.read())
        except ValueError as exc:
            raise Refused(f'stdin is not JSON ({exc}).') from exc
    return _read_json(path)


def cmd_sample_payload(args: argparse.Namespace) -> int:
    print(json.dumps(sample_payload(args.folder, _samples(args.samples), eval_set_id=args.eval_set_id, owner=args.owner), ensure_ascii=False))
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    if args.scored is not None and args.seen_once is not None:
        raise Refused('--scored and --seen-once are two different answers; pass one.')
    scored = _json_arg(args.scored) if args.scored is not None else None
    if scored is not None and not isinstance(scored, dict):
        raise Refused(f'{args.scored} is not a score_sampled_replies result.')
    samples = _samples(args.samples) if args.samples is not None else None
    if samples is None and args.seen_once is None:
        raise Refused('confirm needs --samples (what verify_smoke.py --sample-cut wrote), or --seen-once <reason> when nothing was sampled.')
    code, record = confirm(args.folder, samples=samples, scored=scored, seen_once=args.seen_once, picks_dir=args.picks_dir)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if code == EXIT_RARE_AT_CUT:
        print(
            f'NOT PINNED: {args.folder.name} was caught in 0 of {record["n"]} replays at its cut, so it is deleted; '
            f'{len(record.get("recut_candidates") or [])} other cell(s) of the source run fail its check — re-cut from one of them '
            'by hand if the issue matters, or let its check carry it.',
            file=sys.stderr,
        )
    return code


def cmd_replay_trials(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if str(args.carried_from) == '-' else args.carried_from.read_text(encoding='utf-8')
    carried = [line.strip() for line in raw.splitlines() if line.strip()]
    before = _read_json(args.before) if args.before is not None and args.before.is_file() else None
    print(replay_trials(carried, before=before if isinstance(before, dict) else None, regressions_dir=args.regressions_dir, decide=args.decide))
    return 0


def _carried(path: Path) -> list[str]:
    raw = sys.stdin.read() if str(path) == '-' else path.read_text(encoding='utf-8')
    return [line.strip() for line in raw.splitlines() if line.strip()]


def cmd_replay_plan(args: argparse.Namespace) -> int:
    import yaml  # noqa: PLC0415 - only this verb reads the policy set

    policies = yaml.safe_load(args.policies.read_text(encoding='utf-8')) if args.policies.is_file() else None
    names = _carried(args.carried_from) if args.carried_from is not None else [folder.name for folder in folders(args.regressions_dir)]
    lost = state_lost_folders(args.regressions_dir, before=_optional_json(args.before), seed_fidelity=_optional_json(args.seed_fidelity))
    routes = replay_routes(
        names, regressions_dir=args.regressions_dir, owners=read_owners(args.owners), policies=policies if isinstance(policies, dict) else {}, state_lost=lost
    )
    plan = replay_plan_file(routes)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({SINGLE_HOP: plan[SINGLE_HOP], FULL_REPLAY: plan[FULL_REPLAY]}))
    return 0


def _optional_json(path: Path | None) -> Any:
    """The JSON at ``path``; None when no path was given, the file is absent, or it cannot be read."""
    if path is None or not path.is_file():
        return None
    try:
        return _read_json(path)
    except Refused:
        return None


def cmd_retire(args: argparse.Namespace) -> int:
    run = _optional_json(args.run)
    run_id = str((run or {}).get('run_name') or args.run.parent.name) if isinstance(run, dict) else args.run.parent.name
    # A hollow folder is dropped first, so it never counts clean draws it could not have measured.
    dropped_dir = args.dropped_dir or args.retired_dir.parent / DROPPED_DIRNAME
    hollow = {row['folder']: row for row in hollow_rows(run)}
    for name in drop(list(hollow.values()), regressions_dir=args.regressions_dir, dropped_dir=dropped_dir, run_name=args.run.parent.name,
                     clean_draws=args.ledger):
        print(f'{name}: dropped — its replays could not measure its issue ({hollow[name]["reason"]}); its finding is planned again')
    book, candidates, skipped = update_clean_draws(
        run, run_id=run_id, before=_optional_json(args.before), regressions_dir=args.regressions_dir, ledger=_optional_json(args.ledger)
    )
    if skipped is not None:
        print(f'[regressions] no regression retired from {args.run}: {skipped}.')
        return 0
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    args.ledger.write_text(json.dumps(book, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name in retire(candidates, regressions_dir=args.regressions_dir, retired_dir=args.retired_dir, run=args.run, run_id=run_id):
        row = next(r for r in candidates if r['folder'] == name)
        print(f"{name}: retired — not caught in {row['clean']} clean replays on its {row['axis']} cell across {len(row['runs'])} run(s); its before asked for {row['needed']}")
    return 0


def cmd_runnable(args: argparse.Namespace) -> int:
    retired = retired_names(args.retired_dir)
    for line in sys.stdin.read().splitlines():
        if line.strip() and line.strip() not in retired:
            print(line.strip())
    return 0


def cmd_repin(args: argparse.Namespace) -> int:
    candidates = repin_candidates(_read_json(args.summary), regressions_dir=args.regressions_dir, owners=read_owners(args.owners))
    for name in repin(candidates, regressions_dir=args.regressions_dir, source=args.summary):
        print(name)
    return 0


REGRESSIONS_DIR_HELP = (
    'the regressions directory: a folder under the derived slug that records another finding id makes this one '
    'take the next suffixed slug'
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='regressions.py', description=SUMMARY)
    sub = parser.add_subparsers(dest='command', required=True)

    pick = sub.add_parser('pick', help='choose the scenario and the cut for one issue, out of a finished run')
    pick.add_argument('run_dir', type=Path, help='the run output directory holding the scenario directories')
    pick.add_argument('--policy', help='the failed policy naming the issue; its judge supplies the cut when it is turn-scoped')
    pick.add_argument('--scenario', help='record from THIS scenario instead of the shortest-seed one (exact name or unambiguous substring)')
    pick.add_argument('--cut-turn', type=int, help='the turn the issue is ON, overriding whatever the judge says')
    pick.add_argument(
        '--workdir', required=True, type=Path,
        help='the simulation workdir; its bench_manifest.json says whether the harness can resume a recorded conversation',
    )
    pick.add_argument(
        '--out', type=Path,
        help='also write the JSON here; removed first, so a refusal leaves no earlier pick behind',
    )
    pick.add_argument(
        '--exclude-scenario', action='append', default=[],
        help='a scenario a bench issue touches; it is never chosen and is listed under refused (repeatable)',
    )
    pick.add_argument(
        '--exclude-cell', action='append', default=[], metavar='SCENARIO:POLICY',
        help="a scenario+policy cell a bench issue touches (<scenario>/trial_<N>:<policy> for one trial); the scenario is "
        'left out only when --policy is that policy (repeatable)',
    )
    pick.add_argument(
        '--seen-in', action='append', default=None,
        help="a scenario the issue was seen in — a finding's `seen in:` entry; the choice is made among these only "
        'and is refused when none of them holds a conversation to record (repeatable)',
    )
    pick.add_argument(
        '--plan', type=Path,
        help="an auto-plan JSON of THIS run: with --finding, its record row is the choice — the policy, the conversations "
        'over every trial, each one\'s own cut and the bench — so the pick records what the plan counted',
    )
    pick.add_argument('--finding', metavar='FINDING_ID', help='the finding whose --plan row to pick from')
    pick.set_defaults(func=cmd_pick)

    write = sub.add_parser('write', help='create regression_<slug> with its issue paragraph')
    write.add_argument('regressions_dir', type=Path, help='the regressions directory (created if absent)')
    write.add_argument('--slug', required=True, help='the issue slug; the folder is regression_<slug>')
    write.add_argument('--roles-file', required=True, type=Path, help='file holding the ROLES.md issue paragraph, or - for stdin')
    write.add_argument(
        '--first-talk-from', type=Path,
        help='the run transcript to lift the opener from — no pin: the turn-0 shape, or opener-rooted on a harness that cannot resume',
    )
    write.add_argument('--issue-from', type=Path, required=True, help='the run transcript the issue is in; its turn at --cut-turn is kept as issue_turn.json')
    write.add_argument('--cut-turn', type=int, required=True, help='the turn the issue is ON (0 for the opener-only shape)')
    write.add_argument(
        '--trigger',
        help="the user move that exposed the issue (a finding's `trigger:` line), written as the paragraph's **Trigger.** "
        'line; required for the opener-rooted shape (--first-talk-from with a later --cut-turn, on a harness that cannot resume)',
    )
    write.add_argument(
        '--from-finding', metavar='FINDING_ID',
        help='the issue is this confirmed finding, recorded at suite build: the slug must be the one its id derives, '
        'and the paragraph gains the suite-build line',
    )
    write.add_argument(
        '--finding-id', metavar='FINDING_ID',
        help='the issue is this confirmed finding, recorded at fix time: the slug must be the one its id derives, '
        'and the paragraph gains the finding id line',
    )
    write.set_defaults(func=cmd_write)

    one = sub.add_parser(
        'finding', help='read one confirmed finding out of agent_findings.md, with the slug its id derives',
        description='Exit 2 when the file holds no finding with that id, or holds it as anything but confirmed.',
    )
    one.add_argument('findings', type=Path, help='the agent_findings.md the suite build wrote')
    one.add_argument('finding_id', help='the stable id, e.g. agent.shortfall_unreported')
    one.add_argument('--regressions-dir', type=Path, help=REGRESSIONS_DIR_HELP)
    one.set_defaults(func=cmd_finding)

    slug_of = sub.add_parser('finding-slug', help="print the issue slug a finding's stable id derives")
    slug_of.add_argument('finding_id')
    slug_of.add_argument('--regressions-dir', type=Path, help=REGRESSIONS_DIR_HELP)
    slug_of.set_defaults(func=cmd_finding_slug)

    plan = sub.add_parser(
        'auto-plan',
        help='choose the confirmed findings the suite build records at gate exit, and why each other one is not',
        description=f'Ranks by severity, then confidence, and records at most --cap (default {AUTO_CAP}) new folders. '
        'A finding whose conversations in --run hold no user turn is skipped as such. The bench flags carry what the '
        'gate left open: a finding whose policy a bench issue touches is skipped, and so is one with no scenario '
        "outside the bench that has its policy violated in --run (the ones the record step's pick accepts); any "
        'harness-wide issue skips them all; an accepted harness gap is listed and skips nothing. A missing findings file plans nothing.',
    )
    plan.add_argument('findings', type=Path, help='the agent_findings.md the suite build wrote')
    plan.add_argument('--regressions-dir', required=True, type=Path, help='the regressions directory, read for folders that already cover a finding')
    plan.add_argument('--run', help='the qualification run the record cuts from (required unless --static-only or --no-auto-regressions)')
    plan.add_argument('--bench-scenario', action='append', default=[], help="a scenario whose seed, premise or ground truth is wrong; every policy's verdict in it is dropped (repeatable)")
    plan.add_argument(
        '--bench-cell', action='append', default=[], metavar='SCENARIO:POLICY',
        help='one scenario+policy cell a bench issue touches (a prompt or rubric false red); only that policy is dropped in that scenario, '
        'or in one trial of it as <scenario>/trial_<N>:<policy> (repeatable)',
    )
    plan.add_argument('--bench-cells-from-audit', action='store_true',
                      help="narrow each scenario --bench-cell to the run's own FALSE RED audit items of that policy, one trial each")
    plan.add_argument('--bench-from', type=Path,
                      help="take the bench block of this auto-plan JSON (the suite's post-final plan); refused unless it was planned from --run")
    plan.add_argument('--acceptance', action='store_true',
                      help=f"plan /simulation-fixes' records: no build ledger, and at most {AUTO_CAP} per fix plan (the rest wait for the next one)")
    plan.add_argument('--bench-policy', action='append', default=[], help='a policy a bench issue the gate left open touches (repeatable)')
    plan.add_argument('--harness-wide', action='append', default=[], help='the title of an open harness-wide bench issue (repeatable)')
    plan.add_argument('--accepted-harness', action='append', default=[],
                      help='the title of a harness gap the gate accepted in a landing it ruled valid: listed in the plan, skips nothing (repeatable)')
    plan.add_argument('--cap', type=int, default=AUTO_CAP, help=argparse.SUPPRESS)
    plan.add_argument('--counted-plan', type=Path,
                      help=f"this build's earlier plan: its written folders join the build's ledger ({BUILD_LEDGER_FILENAME} beside the "
                      'findings file, which every auto-report adds to), and the ledger\'s folders on disk count against the cap')
    plan.add_argument('--no-auto-regressions', action='store_true', help='the operator turned recording off for this build')
    plan.add_argument('--static-only', action='store_true', help='the suite has no qualification run to record from')
    plan.set_defaults(func=cmd_auto_plan)

    report = sub.add_parser('auto-report', help="print the suite report's one line about what the findings became")
    report.add_argument('plan', type=Path, help='the JSON auto-plan printed')
    report.add_argument(
        '--findings', type=Path, required=True,
        help="the build's agent_findings.md, re-read now: the line counts the issues the page shows confirmed at report time",
    )
    report.add_argument(
        '--refused', action='append', default=[], metavar='FINDING_ID=REASON',
        help='a planned finding whose record refused, with the refusal in a few words (repeatable; kept in the plan)',
    )
    report.add_argument(
        '--rare', action='append', default=[], metavar='FINDING_ID',
        help='a planned finding whose record ended not pinned at its cut (confirm exit 3) (repeatable; kept in the plan)',
    )
    report.set_defaults(func=cmd_auto_report)

    lines = sub.add_parser(
        'fixes-lines', help="print each fix-plan finding's Regression: line from an auto-plan JSON, one `<finding id>\\t<line>` per row",
    )
    lines.add_argument('plan', type=Path, help='the JSON auto-plan --acceptance printed ($ART/fixes_regressions.json)')
    lines.set_defaults(func=cmd_fixes_lines)

    payload = sub.add_parser(
        'sample-payload', help='print the score_sampled_replies arguments for the valid samples drawn at a folder\'s cut',
        description='The owner is --owner, else the ROLES.md Probe policy. Exit 2 when neither names one, or when no sample is valid.',
    )
    payload.add_argument('folder', type=Path, help='the regression folder the record step wrote')
    payload.add_argument('samples', type=Path, help='the JSON verify_smoke.py --sample-cut wrote')
    payload.add_argument('--eval-set-id', help='the eval set to score against; included in the printed arguments')
    payload.add_argument('--owner', help="the eval set ledger's owner for this folder, when it has one; the ROLES.md probe otherwise")
    payload.set_defaults(func=cmd_sample_payload)

    confirmed = sub.add_parser(
        'confirm', help="classify a folder on how often its issue recurred at the cut, and keep the result",
        description=f'k caught of n measured: with {CONFIRM_SAMPLES} measured, {RELIABLE_CAUGHT}+ reliable, 1+ intermittent, 0 rare at the cut; '
        f'fewer measured, 1+ intermittent, 0 seen once. Every band but rare writes {REPLAYS_FILENAME} into the folder (local only). '
        f'Rare at the cut deletes the folder, rewrites {OVERVIEW_FILENAME}, writes <picks-dir>/<folder>{CONFIRM_SUFFIX} with the '
        f're-cut candidates, and exits {EXIT_RARE_AT_CUT}.',
    )
    confirmed.add_argument('folder', type=Path, help='the regression folder the record step wrote')
    confirmed.add_argument('--samples', type=Path, help='the JSON verify_smoke.py --sample-cut wrote')
    confirmed.add_argument('--scored', type=Path, help='the score_sampled_replies result, or - for stdin')
    confirmed.add_argument('--seen-once', metavar='REASON', help='nothing was scored; keep the folder as seen once, for this typed reason')
    confirmed.add_argument('--picks-dir', type=Path, help="where the record's pick lives ($ART/regression_picks); the confirm record of a rare folder lands there")
    confirmed.set_defaults(func=cmd_confirm)

    trials = sub.add_parser(
        'replay-trials', help='print how many replays the regression-only step runs (5 to 10; 3 to 5 with --decide)',
        description='The most clean replays any carried folder needs to clear its "before" — its row in the previous run.json, '
        f'else its {REPLAYS_FILENAME} — clamped to {CONFIRM_SAMPLES}..{REPLAY_TRIALS_MAX}.',
    )
    trials.add_argument('regressions_dir', type=Path)
    trials.add_argument('--carried-from', required=True, type=Path, help='file holding the carried folder names, one per line, or - for stdin')
    trials.add_argument('--before', type=Path, help="the previous run's run.json; absent or missing reads every before from replays.json")
    trials.add_argument('--decide', action='store_true',
                        help=f"size a fix's verify: the clean replays its fixed / not-fixed decision asks for, {DECIDE_TRIALS_MIN} to {DECIDE_TRIALS_MAX}")
    trials.set_defaults(func=cmd_replay_trials)

    plan = sub.add_parser(
        'replay-plan',
        help='print {single_hop: [...], full: [...]}: which folders replay only their issue turn, and which need whole conversations',
        description='Every folder on disk unless --carried-from names them. Whole: a folder played from its opener or pinned on '
        '`anywhere`, an owner that needs whole conversations, a harness that met the cut with less state than the recording '
        "had. --out writes the runner's --replay-plan file.",
    )
    plan.add_argument('regressions_dir', type=Path)
    plan.add_argument('--carried-from', type=Path, help='file holding the folder names, one per line, or - for stdin; absent, every folder on disk')
    plan.add_argument('--owners', type=Path, help='$ART/regression_owners.json; absent, every folder takes its ROLES.md probe')
    plan.add_argument('--policies', required=True, type=Path, help="the workspace's policies_config.yaml")
    plan.add_argument('--before', type=Path, help="the previous run's run.json ($ART/regression_before.json): its rows' restore_reduced")
    plan.add_argument('--seed-fidelity', type=Path, help="the final stage's seed-resume probe ($ART/seed_resume_fidelity.json)")
    plan.add_argument('--out', type=Path, help="write the runner's --replay-plan file here")
    plan.set_defaults(func=cmd_replay_plan)

    retire_cmd = sub.add_parser(
        'retire', help="move out every regression this run cleared on the SDK's own clear verdict, fix or no fix",
        description="A folder retires when it gathered, across scored runs since its defect was last caught, at least the "
        "clean replays its before asks for; a run that was stopped or lost scenarios retires nothing, and a replay that "
        'was invalid, lost state, met a failed or refused tool call, or whose premise never arose adds none. No age-out, '
        'no cap. The folder moves under --retired-dir with a retired.json; BUNDLE.md is rewritten. First, every folder the '
        'run.json marks hollow (its replays could not measure its issue) moves under --dropped-dir with a dropped.json.',
    )
    retire_cmd.add_argument('run', type=Path, help="this run's run.json")
    retire_cmd.add_argument('--before', type=Path, help="the previous run's run.json ($ART/regression_before.json)")
    retire_cmd.add_argument('--regressions-dir', required=True, type=Path)
    retire_cmd.add_argument('--retired-dir', required=True, type=Path, help='where a retired folder moves ($ART/retired_regressions)')
    retire_cmd.add_argument('--ledger', required=True, type=Path, help="the clean replays gathered per folder across scored runs ($ART/regression_clean_draws.json)")
    retire_cmd.add_argument('--dropped-dir', type=Path,
                            help=f'where a hollow folder moves (default: {DROPPED_DIRNAME}/ beside --retired-dir); its finding is planned again')
    retire_cmd.set_defaults(func=cmd_retire)

    runnable = sub.add_parser('runnable', help='print the scenario ids on stdin, one per line, less every retired regression')
    runnable.add_argument('--retired-dir', required=True, type=Path, help='$ART/retired_regressions')
    runnable.set_defaults(func=cmd_runnable)

    repin_cmd = sub.add_parser(
        'repin', help="pin on `anywhere` every regression whose owner decided on under half its replays at the cut and on at "
                      "least half across the conversation; print each folder pinned",
    )
    repin_cmd.add_argument('summary', type=Path, help="a run's run_summary.json (its regression_replays block)")
    repin_cmd.add_argument('--regressions-dir', required=True, type=Path)
    repin_cmd.add_argument('--owners', type=Path, help='$ART/regression_owners.json; absent, every folder takes its ROLES.md probe')
    repin_cmd.set_defaults(func=cmd_repin)

    ledger = sub.add_parser(
        'ledger-owners',
        help="print the owners file from an eval set's job_status: the ledger owner of each recorded regression",
    )
    ledger.add_argument('payload', type=Path, help="the eval set's job_status JSON — a path, or - for stdin")
    ledger.add_argument(
        '--declined',
        action='store_true',
        help='print only `{"declined_regressions": [{folder, reason}, …]}` for suite.json (the rows declined on the record), or `{}` when none is',
    )
    ledger.set_defaults(func=cmd_ledger_owners)

    carry = sub.add_parser(
        'carry-world-facts',
        help="append a trace scenario's world facts to the ROLES.md of the regression recorded from its run",
        description=f'Run after the conversion into the regression folder. The section is copied from its "{WORLD_FACTS_HEADING}" '
        f'heading to the end of the file and appended last, at the depth of the regression\'s "{PROVENANCE_HEADING}" heading. '
        'Exit 0 when appended, when the regression already carries it, and — with a WARNING — when the trace scenario '
        'folder is gone or holds no section. Exit 2 when the regression is not a converted resumed folder.',
    )
    carry.add_argument('--from', dest='source', required=True, type=Path, help="the trace scenario's ROLES.md")
    carry.add_argument('--into', required=True, type=Path, help='the regression folder the record step wrote')
    carry.set_defaults(func=cmd_carry_world_facts)

    unfinished_note = (
        'A folder with neither resume_pin.json nor first_talk.txt exits 2: the backend authors a scenario for such a '
        'folder cold from ROLES.md, under its name, so this is the last place it can stop a build.'
    )
    overview = sub.add_parser(
        'overview', help="rewrite the directory's BUNDLE.md from what is on disk", description=unfinished_note,
    )
    overview.add_argument('regressions_dir', type=Path)
    overview.set_defaults(func=cmd_overview)

    listing = sub.add_parser(
        'list',
        help='enumerate the regression folders and their shapes',
        description=f'Exit 0 all present, 1 with --shipped-from when a folder is not in the set. {unfinished_note} '
        'Such a folder is never counted as carried, whatever the shipped list says.',
    )
    listing.add_argument('regressions_dir', type=Path)
    listing.add_argument('--shipped-from', type=Path, help='file holding the scenario names a set shipped, one per line, or - for stdin')
    listing.set_defaults(func=cmd_list)
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
