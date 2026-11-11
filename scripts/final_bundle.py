#!/usr/bin/env python3
"""The delivered batch's one additional bundle: regressions and trace scenarios, merged and staged.

``plurai ingest-bundle`` takes ONE directory and ``generate_scenarios_start`` ONE
``additional_bundle_id``, so the two directories whose folders reach a set only through the
delivered batch — ``regressions/`` and ``trace_scenarios/`` — ride it as one bundle. The delivered
batch is the suite's final batch, or, on a static-only suite, its Step 1 batch.

Verbs:

``stage``          copy every finished regression and trace scenario into one clean directory
                   with one ``BUNDLE.md``, holding trace scenarios back where they cannot run.
``missing``        read the delivered set's metadata and name, per kind, the folders it was asked
                   for and does not carry, each with why.
``target-graded``  whether the eval set grades a scenario's target at all, and if not, which
                   delivered folders are therefore ungraded on the concern they exist for.

Every folder that resumes from a pin — a resumed regression, and every trace scenario — is first
Init'd by the harness with its own seed (``verify_smoke.py --seed-resume``, no model call). A folder
whose seed the harness refuses is held back as ``harness_cannot_replay`` with the child's text; a
check that reaches no verdict stops the stage. Four more hold-backs apply to trace scenarios only:

* **The posture is anonymized.** A trace scenario's pin and world facts are a production
  conversation, verbatim, and the brief-driven mode writes them only under the raw posture. The
  posture can change after the folders were written, and a second env or a clone can declare its
  own, so the stage reads ``anonymize:`` again (``trace_scenarios.anonymized``) and holds every
  trace scenario back under ``anonymize: true``. Nothing verbatim is uploaded from such an env.
* **The folder is unfinished.** ``trace_scenarios.py write`` creates ``ROLES.md`` before the
  conversion, so a conversion that failed leaves a folder with no pin. It is held back and the
  rest ships. An unfinished regression still refuses the whole stage: a regression is a confirmed
  issue, and leaving it out is the operator's call.
* **There is no harness to ask.** A workdir with no ``simulate.py`` holds every pinned folder
  back, regressions included, with no spawn.
* **The cap.** The backend refuses a job whose ``count + folders`` exceeds
  ``scenario_job_max_count``. Trace scenarios are held back last-first until the rest fits;
  regressions alone over the cap refuse the stage, because retiring a regression is the
  operator's call.

Exit codes: 0 success; 1 ``missing`` named a lost folder, or ``target-graded`` found folders
ungraded on their target — stdout is then the value for ``suite.json``; 2 a refusal.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _platform  # noqa: E402
import regressions  # noqa: E402
import trace_scenarios  # noqa: E402
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

Refused = regressions.Refused

SUMMARY = "The delivered batch's one additional bundle: regressions and trace scenarios, merged and staged."

#: The walk count of the delivered batch and the backend's default ``scenario_job_max_count``.
#: ``trace_scenarios.py`` owns both, because the brief-driven mode preselects against the same cap
#: this stage enforces.
WALK_COUNT = trace_scenarios.WALK_COUNT
SCENARIO_JOB_MAX_COUNT = trace_scenarios.SCENARIO_JOB_MAX_COUNT

HELD_BACK_POSTURE = 'anonymized_posture'
HELD_BACK_UNFINISHED = 'unfinished'
HELD_BACK_HARNESS = 'harness_cannot_replay'
HELD_BACK_CAP = 'over_cap'

#: What an out-dir may already hold for staging to clear it: a previous stage's own output.
_STAGED_OVERVIEW = regressions.OVERVIEW_FILENAME

#: The strings the backend reads as a ``grades_scenario_target`` declaration of true. Any other string
#: is false or undeclared, and either grades no target.
_DECLARED_TRUE = frozenset({'true', 'yes', 'on', '1'})


# ── staging ───────────────────────────────────────────────────────────────────


def _slug_key(name: str) -> str:
    """The name the backend's slugifier would give a folder, for the duplicate check."""
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


def _require_shippable_names(names: list[str]) -> None:
    """Refuse a folder whose name would not ship as itself, and two that would ship as one.

    The backend names each folder's scenario by the folder, slugified, and cuts it at
    ``SCENARIO_NAME_MAX``. A name that changes on the way ships under another name, and every
    membership check afterwards reads the folder as absent; two that slugify alike are refused
    after the upload.
    """
    problems: list[str] = []
    seen: dict[str, str] = {}
    for name in names:
        if not regressions.SLUG_RE.match(name):
            problems.append(f'{name} is not lowercase ascii words joined by single underscores')
        elif len(name) > regressions.SCENARIO_NAME_MAX:
            problems.append(f'{name} is {len(name)} characters, over the {regressions.SCENARIO_NAME_MAX} a scenario name keeps')
        key = _slug_key(name)
        if key in seen:
            problems.append(f'{seen[key]} and {name} ship under one name')
        seen.setdefault(key, name)
    if problems:
        raise Refused(
            'these folders would not ship under their own names: ' + '; '.join(problems) + '. Rename or delete them '
            '— a folder is named by its slug, and renaming it retires the old one.'
        )


def _require_safe_out(out: Path, workdir: Path, sources: list[Path]) -> None:
    """Refuse an out-dir whose clearing would delete anything a stage did not write.

    A source folder or the workdir is refused by path; any other directory by its contents, which
    may be only a previous stage's ``BUNDLE.md`` and prefixed folders, plus dotfiles a file manager
    leaves behind — so ``--out "$ART"`` is refused before it can take the suite's artifacts with it.
    """
    target = out.resolve()
    clash = next(
        (
            path
            for path in (workdir.resolve(), *(source.resolve() for source in sources))
            if path.is_relative_to(target) or (path != workdir.resolve() and target.is_relative_to(path))
        ),
        None,
    )
    if clash is not None:
        raise Refused(
            f'--out {out} is, holds or sits inside {clash}. Staging clears the out-dir before it copies, so it must be '
            'a directory of its own — the suite stages into "$ART/final_bundle".'
        )
    if target.exists() and not target.is_dir():
        raise Refused(f'--out {out} exists and is not a directory.')
    if target.is_dir():
        foreign = sorted(
            entry.name
            for entry in target.iterdir()
            if not (entry.name == _STAGED_OVERVIEW and entry.is_file())
            and not (entry.is_file() and entry.name.startswith('.'))
            and not (entry.is_dir() and entry.name.startswith((regressions.FOLDER_PREFIX, trace_scenarios.FOLDER_PREFIX)))
        )
        if foreign:
            raise Refused(
                f'--out {out} holds {", ".join(foreign[:5])}{" …" if len(foreign) > 5 else ""}, which no stage wrote. '
                'Staging clears the out-dir before it copies, so it must be a directory of its own — the suite stages '
                'into "$ART/final_bundle".'
            )


#: Asks the harness about pinned folders: ``{folder name: None (accepted) | the refusal text}``.
#: Raises :class:`Refused` when it reaches no verdict.
SeedCheck = Callable[[Path, list[Path]], dict[str, Optional[str]]]  # evaluated at runtime: `|` on types is 3.10+


def interpreter_argv(env: dict[str, str] | None = None) -> list[str]:
    """How to start ``$PLURAI_PY`` (the SDK's interpreter), else this one.

    Windows starts only executables: a ``$PLURAI_PY`` that is a file but not one (no ``MZ``; a script with a ``#!``
    line, which a POSIX kernel would hand to its interpreter) fails with WinError 193, so there it is run by this
    interpreter instead.
    """
    named = (os.environ if env is None else env).get('PLURAI_PY') or ''
    if not named:
        return [sys.executable]
    if not _platform.WINDOWS or not os.path.isfile(named):
        return [named]
    with open(named, 'rb') as handle:
        return [named] if handle.read(2) == b'MZ' else [sys.executable, named]


#: Where the seed check records the folders whose Init restored less than the pin, under the workdir's artifacts:
#: ``regressions.py replay-plan --seed-fidelity`` plays those whole in the final run.
SEED_FIDELITY_FILENAME = 'seed_resume_fidelity.json'


def seed_fidelity_path(workdir: Path) -> Path:
    """Where the seed check records the folders restored with less state than the pin: ``$ART``."""
    return workdir / 'artifacts' / SEED_FIDELITY_FILENAME


def check_seeds_with_smoke(harness: Path, folders: list[Path], *, fidelity_out: Path | None = None) -> dict[str, str | None]:
    """One ``verify_smoke.py --seed-resume`` over ``folders``, run by ``$PLURAI_PY`` (the SDK's interpreter). With
    ``fidelity_out`` it also records there the folders whose seed was taken with less state than the pin."""
    smoke = Path(__file__).resolve().parent.parent / 'skills' / 'simulation-setup-python' / 'scripts' / 'verify_smoke.py'
    fidelity = ['--fidelity-out', str(fidelity_out)] if fidelity_out is not None else []
    argv = [*interpreter_argv(), str(smoke), str(harness), '--seed-resume', *map(str, folders), *fidelity]
    done = subprocess.run(argv, capture_output=True, text=True, **utf8_child_text(), check=False)
    lines = done.stdout.strip().splitlines()
    try:
        verdicts = json.loads(lines[-1]) if done.returncode in (0, 1) and lines else None
    except ValueError:
        verdicts = None
    if not isinstance(verdicts, dict):
        tail = (lines or done.stderr.strip().splitlines() or ['no output'])[-1]
        raise Refused(
            f'the harness could not be asked whether it takes the pinned folders\' seeds (verify_smoke exit {done.returncode}): '
            f'{tail[:500]}. Every final scenario Inits the same way, so nothing was staged: fix what it names and stage again.'
        )
    return verdicts


def stage(
    workdir: Path,
    out: Path,
    *,
    check_seeds: SeedCheck,
    harness: Path | None = None,
    count: int = WALK_COUNT,
    max_count: int = SCENARIO_JOB_MAX_COUNT,
) -> dict[str, Any]:
    """Stage the merged bundle into ``out`` and return what rode and what was held back.

    Everything is checked before ``out`` is touched: an unfinished regression, a name that would
    not ship as itself, regressions alone over the cap, and a seed check that reaches no verdict
    each refuse the whole stage, so a stage refused there leaves the previous one as it was. A
    filesystem error while copying is refused too, after ``out`` was cleared; re-running the stage
    is the remedy.
    """
    regressions_dir = workdir / regressions.REGRESSIONS_DIRNAME
    traces_dir = workdir / trace_scenarios.TRACE_SCENARIOS_DIRNAME
    # An earlier build's seed record never outlives this stage: the check below writes this one's, or none.
    seed_fidelity_path(workdir).unlink(missing_ok=True)
    regression_folders = regressions.folders(regressions_dir)
    trace_folders = trace_scenarios.folders(traces_dir)

    unfinished: list[str] = []
    resumed: list[Path] = []
    for folder in regression_folders:
        try:
            if regressions.folder_shape(folder) == regressions.SHAPE_RESUMED:
                resumed.append(folder)
        except Refused as exc:
            unfinished.append(str(exc))
    if unfinished:
        raise Refused(
            f'{len(unfinished)} regression folder(s) replay nothing, and the backend authors such a folder cold from '
            'ROLES.md under its name. Finish or delete each before the delivered batch: ' + ' | '.join(unfinished)
        )

    held_back: list[dict[str, str]] = []
    kept: list[Path] = []
    for folder in trace_folders:
        try:
            trace_scenarios.folder_shape(folder)
        except Refused as exc:
            held_back.append({'folder': folder.name, 'reason': HELD_BACK_UNFINISHED, 'detail': str(exc)})
            continue
        kept.append(folder)

    _require_shippable_names([folder.name for folder in regression_folders + kept])

    if trace_scenarios.room(regression_count=len(regression_folders), trace_scenario_count=0, count=count, max_count=max_count) < 0:
        raise Refused(
            f'{len(regression_folders)} regression(s) beside a walk of {count} exceed scenario_job_max_count={max_count}, '
            'and a regression is never dropped to fit. Retire the regressions whose issues are fixed and verified '
            '(delete the folder), or ask the backend operator to raise the cap.'
        )

    if kept and trace_scenarios.anonymized(workdir):
        detail = (
            'plurai.config.yaml declares anonymize: true, and a trace scenario carries a production conversation '
            'verbatim, so none is uploaded from this env. Set the raw posture to ship them, or retire them with '
            '`trace_scenarios.py retire --slug <slug>`.'
        )
        held_back += [{'folder': folder.name, 'reason': HELD_BACK_POSTURE, 'detail': detail} for folder in kept]
        kept = []
    pinned = [*resumed, *kept]
    if pinned:
        refused = _refused_seeds(workdir, harness, pinned, check_seeds)
        held_back += [{'folder': name, 'reason': HELD_BACK_HARNESS, 'detail': detail} for name, detail in sorted(refused.items())]
        regression_folders = [folder for folder in regression_folders if folder.name not in refused]
        kept = [folder for folder in kept if folder.name not in refused]
    over = -trace_scenarios.room(
        regression_count=len(regression_folders), trace_scenario_count=len(kept), count=count, max_count=max_count,
    )
    if over > 0:
        detail = (
            f'{count} walk scenarios plus {len(regression_folders)} regression(s) and {len(kept)} trace scenario(s) exceed '
            f'scenario_job_max_count={max_count}; trace scenarios are held back last-first until the rest fits.'
        )
        held_back += [{'folder': folder.name, 'reason': HELD_BACK_CAP, 'detail': detail} for folder in kept[-over:]]
        kept = kept[:-over]

    _require_safe_out(out, workdir, [regressions_dir, traces_dir])
    overview = overview_text(regression_folders, kept) if regression_folders or kept else None
    try:
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        for folder in regression_folders + kept:
            # A recorded CLI session is the local harness's replay input, and the record-time replay rate is a
            # local measurement; neither leaves the machine (the SDK's bundle_units skips both as well).
            shutil.copytree(folder, out / folder.name, ignore=shutil.ignore_patterns(regressions.CLI_SESSION_DIRNAME, regressions.REPLAYS_FILENAME))
        if overview is not None:
            (out / regressions.OVERVIEW_FILENAME).write_text(overview, encoding='utf-8')
    except OSError as exc:
        raise Refused(
            f'staging into {out} failed ({type(exc).__name__}: {exc}). The directory is incomplete: fix the cause and run '
            'the stage again before uploading anything from it.'
        ) from exc

    return {
        'folders': [folder.name for folder in regression_folders + kept],
        'regressions': [folder.name for folder in regression_folders],
        'trace_scenarios': [folder.name for folder in kept],
        'held_back': held_back,
    }


def _refused_seeds(workdir: Path, harness: Path | None, pinned: list[Path], check_seeds: SeedCheck) -> dict[str, str]:
    """``{folder name: why}`` for every pinned folder the harness does not take the seed of."""
    if (gap := trace_scenarios.harness_gap(workdir, harness)) is not None:
        return {folder.name: gap for folder in pinned}
    verdicts = check_seeds(harness or workdir / trace_scenarios.PYTHON_HARNESS, pinned)
    unanswered = [folder.name for folder in pinned if folder.name not in verdicts]
    if unanswered:
        raise Refused(f'the seed check answered nothing for {", ".join(unanswered)}, so nothing was staged; stage again.')
    return {name: text for name, text in verdicts.items() if text is not None}


def overview_text(regression_folders: list[Path], trace_folders: list[Path]) -> str:
    """The merged bundle's ``BUNDLE.md``: one statement for the set, then one section per kind.

    It reaches the backend as the bundle's own description, so it describes exactly the folders
    that ride — a section for a kind with no folder staged is left out.
    """
    total = len(regression_folders) + len(trace_folders)
    parts = [
        f"""# Regressions and trace scenarios — the folders this batch carries beside the walk

{total} folder(s): {len(regression_folders)} regression(s) and {len(trace_folders)} trace scenario(s). Each folder is
authored as ONE scenario beside the walk and named by its folder. No eval is authored per folder:
each scenario is graded on the target its authoring session derives from the folder's
`{regressions.ROLES_FILENAME}` paragraph, so that paragraph defines what the scenario is for.
"""
    ]
    if regression_folders:
        parts.append(regressions.overview_section(regression_folders, level=2))
    if trace_folders:
        parts.append(trace_scenarios.overview_section(trace_folders, level=2))
    return '\n'.join(parts)


# ── what the delivered set lost ───────────────────────────────────────────────


def missing(envelope: Any, workdir: Path, *, held_back: set[str] | None = None, staged: Collection[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """The ``suite.json`` keys naming the folders this set was asked for and does not carry.

    One attempted list comes back for the merged bundle; it is split by prefix so each kind
    reaches the report under its own key, each folder as ``{folder, reason}`` — the backend's
    reason, or ``not_handed_over`` for an asked-for folder the backend names in neither of its lists.
    A folder the stage held back is left out: the report states it with the stage's reason
    instead. ``staged`` (the stage's ``folders``) is what this set was asked for: a folder on disk
    the stage never carried — one recorded after it, from this set's own final run, for the next
    build — is not this set's loss. Without it, every folder on disk counts as asked for. A key is
    present only when its list is non-empty.
    """
    held = held_back or set()

    def asked(names: list[str]) -> list[str]:
        return [name for name in names if name not in held and (staged is None or name in staged)]

    on_disk_regressions = asked([folder.name for folder in regressions.folders(workdir / regressions.REGRESSIONS_DIRNAME)])
    on_disk_traces = asked([folder.name for folder in trace_scenarios.folders(workdir / trace_scenarios.TRACE_SCENARIOS_DIRNAME)])
    out: dict[str, list[dict[str, str]]] = {}
    lost_regressions = regressions.missing_folders(envelope, on_disk_regressions, prefix=regressions.FOLDER_PREFIX)
    lost_traces = regressions.missing_folders(envelope, on_disk_traces, prefix=trace_scenarios.FOLDER_PREFIX)
    lost_traces = [row for row in lost_traces if row['folder'] not in held]
    if lost_regressions:
        out['missing_regressions'] = lost_regressions
    if lost_traces:
        out['missing_trace_scenarios'] = lost_traces
    return out


# ── is anything grading their target ─────────────────────────────────────────


def _declares_target_grading(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _DECLARED_TRUE
    return value is not None and bool(value)


def target_graders(policies: Any) -> list[str]:
    """The ``llm_as_judge`` policies that declare ``grades_scenario_target``.

    Only such a judge receives a scenario's target at scoring time, so a set without one grades
    no scenario on its target — and a regression or a trace scenario is judged on the concern it
    exists for only through its target.
    """
    if not isinstance(policies, dict) or not policies:
        raise Refused('the eval mirror is not a non-empty mapping of <policy_name> → <policy>, so nothing in it can be read.')
    return [
        str(name)
        for name, body in policies.items()
        if isinstance(body, dict)
        and body.get('type') == 'llm_as_judge'
        and _declares_target_grading(body.get('grades_scenario_target'))
    ]


def target_graded(policies: Any, envelope: Any) -> dict[str, list[str]]:
    """``target_graders``, and the delivered folders ungraded on their target when there are none."""
    graders = target_graders(policies)
    block = regressions._metadata_block(envelope)
    shipped = regressions._names(block, regressions.SHIPPED_KEY)
    folders = [name for name in shipped if name.startswith((regressions.FOLDER_PREFIX, trace_scenarios.FOLDER_PREFIX))]
    return {'target_graders': graders, 'ungraded_on_target': [] if graders else folders}


# ── commands ──────────────────────────────────────────────────────────────────


def _read_json_arg(path: Path, what: str) -> Any:
    raw = sys.stdin.read() if str(path) == '-' else path.read_text(encoding='utf-8')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Refused(f'{what} is not JSON ({exc}).') from exc


def cmd_stage(args: argparse.Namespace) -> int:
    check = functools.partial(check_seeds_with_smoke, fidelity_out=seed_fidelity_path(args.workdir))
    staged = stage(args.workdir, args.out, check_seeds=check, harness=args.harness, count=args.count, max_count=args.max_count)
    print(json.dumps(staged, indent=2))
    return 0


def _staged_folders(path: Path) -> set[str]:
    """The ``folders`` of the JSON ``stage`` printed: what the delivered set was asked to carry."""
    staged = _read_json_arg(path, '--staged')
    folders = staged.get('folders') if isinstance(staged, dict) else None
    if not isinstance(folders, list):
        raise Refused(f'--staged {path} carries no folders list — pass the JSON `final_bundle.py stage` printed.')
    return {str(name) for name in folders}


def cmd_missing(args: argparse.Namespace) -> int:
    envelope = _read_json_arg(args.metadata, 'the metadata')
    held = set(trace_scenarios.held_back_from(args.staged, flag='--staged')) if args.staged is not None else set()
    keys = missing(envelope, args.workdir, held_back=held, staged=_staged_folders(args.staged) if args.staged is not None else None)
    if not keys:
        print(
            'Every folder this set was asked for reached it. Leave missing_regressions and missing_trace_scenarios out '
            'of suite.json.',
            file=sys.stderr,
        )
        return 0
    print(json.dumps(keys))
    return 1


def cmd_target_graded(args: argparse.Namespace) -> int:
    import yaml  # noqa: PLC0415 - only this verb reads YAML; stage and missing run on a bare interpreter

    try:
        policies = yaml.safe_load(args.policies.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f'{args.policies} could not be read as YAML ({type(exc).__name__}: {exc}).') from exc
    result = target_graded(policies, _read_json_arg(args.meta, 'the metadata'))
    print(json.dumps(result))
    return 1 if result['ungraded_on_target'] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='final_bundle.py', description=SUMMARY)
    sub = parser.add_subparsers(dest='command', required=True)

    staging = sub.add_parser(
        'stage',
        help='stage regressions/ and trace_scenarios/ as one bundle into a clean directory',
        description='Prints JSON {folders, regressions, trace_scenarios, held_back}. An empty `folders` means no bundle '
        'rides: skip the upload and pass no additional_bundle_id. Exit 2 names an unfinished folder, a name that would '
        'not ship as itself, or regressions alone over the cap; nothing is written then.',
    )
    staging.add_argument('--workdir', required=True, type=Path, help='the plurai_sim workdir holding both directories')
    staging.add_argument('--out', required=True, type=Path, help='the staging directory, cleared first ("$ART/final_bundle")')
    staging.add_argument('--harness', type=Path, help="the harness whose Init is asked each pinned folder's seed (default: <workdir>/simulate.py)")
    staging.add_argument('--count', type=int, default=WALK_COUNT, help='the walk count the folders ride beside')
    staging.add_argument('--max-count', type=int, default=SCENARIO_JOB_MAX_COUNT, help="the backend's scenario_job_max_count")
    staging.set_defaults(func=cmd_stage)

    absent = sub.add_parser(
        'missing',
        help='name, per kind, the folders a delivered set was asked for and does not carry',
        description='Exit 0 nothing missing — write neither key. Exit 1 stdout is a JSON object whose keys '
        '(missing_regressions, missing_trace_scenarios), each a list of {folder, reason}, go into suite.json as they '
        f'are; reason is the backend\'s bundle_folders_attempted_reasons entry, {regressions.REASON_UNKNOWN} when it '
        f'names none, or {regressions.REASON_NOT_HANDED_OVER} for a folder the backend was never given. Exit 2 the '
        'metadata could not be read, which is never reported as nothing missing.',
    )
    absent.add_argument('metadata', type=Path, help='get_artifact_meta answer (or its detail / dataset_metadata block), or -')
    absent.add_argument('--workdir', required=True, type=Path, help='the plurai_sim workdir, so a folder never handed over is named')
    absent.add_argument('--staged', type=Path, help='the JSON `stage` printed, so a held-back folder is not reported as lost')
    absent.set_defaults(func=cmd_missing)

    graded = sub.add_parser(
        'target-graded',
        help="whether any llm_as_judge policy declares grades_scenario_target, and which delivered folders are ungraded if none",
        description='Prints JSON {target_graders, ungraded_on_target}. Exit 0 nothing ungraded — write no key. Exit 1 '
        'ungraded_on_target is the value for suite.json. Exit 2 a file could not be read.',
    )
    graded.add_argument('policies', type=Path, help='the eval mirror, $WORKDIR/policies_config.yaml')
    graded.add_argument('--meta', required=True, type=Path, help='the delivered set\'s get_artifact_meta answer, or -')
    graded.set_defaults(func=cmd_target_graded)
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
