#!/usr/bin/env python3
"""Open a new evidence epoch for a build — the ONE place that decides, and prunes.

Called by whichever writer STAMPS the flow window first: `hooks/flow_autoarm.py` (Skill-tool
entry), `hooks/flow_arm.py` (typed entry) or `scripts/flow-window.sh arm`. A same-name or driven
arm stamps nothing and never opens an epoch, so the prune runs once per entry; a same-name arm is a
resume and calls `--resume`, which retires only that command's STOP rows.

ONE typed decision for every command: `new`, `kept` or `undecided`. A new build must not present
the previous build's CONFIRMED findings or run driver record as its own, so on `new` the stamp
prunes `agent_findings.md` back to its `potential` sections and the confirmed ones of the stages
it does not re-derive (`prune_agent_findings.py`, `RESETS_FINDINGS`), and retires the command's
durable run driver record rows (`hooks/_run_driver.retire_durable`). A new build retires its
rows; a resumed build keeps them, except its STOP rows (it re-reaches any gate still unmet). For `generate-suite` the typed phase record decides what "new"
means: no record, a build that handed off (`bench_manifest.handed_off`: `report landed`, then at most the run's top-up), or a
reset (`bench_manifest.RESET_PHASE`) last is a new build; any other last entry is an unfinished build, and resuming it — across sessions too — keeps both. A record that cannot be read
keeps them and warns. A `scan-agent` arm over an open attempt `simulate_plan.py scan-resume` resumes is `kept` the same
way. Every other command is `new` whenever its workdir resolves.

A re-arm of a build that HANDED OFF is a resume, not a new build, when the workspace still names the scenario set
and the eval set that build's phase records carry (`RESUME_IDS`, read off the records via `bench_manifest.SUITE_IDS`,
never off the markers alone): the same sets are the same evidence, and a run driver resuming a build that stopped
after its hand-off (an outage, a re-run of the final stage) must not lose its confirmed findings to the prune. It
keeps them, marks each confirmed row the prune would have dropped `run_stale` (`record_finding.py`), so nothing
shows it as reproduced until the final audit re-records it on this build's run, and retires only the STOP rows.

Every entry with a workdir also un-halts the page stage its command owns (`publish_report.resume_progress`): a page a
stopped flow left halted reads running again when that flow resumes. While a /simulate plan is live, it also starts the
page's watcher (`publish_report.spawn_page_watch`), so a run resumed through a flow entry gets one.

Every stamp also mints the command's **command run id** into the flow directory (``_command_run``): the id the
chokepoints put on every backend job start. It is the inherited id (``PLURAI_COMMAND_RUN_ID``, handed down by a headless
run driver) when there is one, else a fresh one. A resume keeps the id its window has, and mints only when the window has
none. A driven sub-command stamps nothing, so it shares its driver's id. A command that is not a flow opens a command scope
(``open_scope``): the same mint with a ``.plurai_command_scope`` marker that opens the hook gate and no guards.

Usage: open_epoch.py [--flowdir <dir>] <command> <workdir>            prints the decision line; exit 0 (warnings on stderr), 64 usage.
       open_epoch.py [--flowdir <dir>] --resume <command> <workdir>   a same-name re-arm: retire the command's STOP rows; exit 0.
       open_epoch.py --flowdir <dir> --scope|--typed-scope <command>  a non-flow command starts (the user typed it, or a skill chained it): open its command scope; prints the id.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_HOOKS = str(_HERE.parent / 'hooks')
if _HOOKS not in sys.path:
    sys.path.append(_HOOKS)

#: The commands that re-derive CONFIRMED findings -> the `found during:` stages whose confirmed rows
#: their arm KEEPS. The scan confirms what it reproduced by running the agent and nothing after it
#: re-derives that; generate-evals appends only `potential` and /simulation-fixes only READS the file.
RESETS_FINDINGS: dict[str, tuple[str, ...]] = {'scan-agent': (), 'generate-suite': ('scan',), 'analyze': ('scan',)}


#: The ids a re-arm must still name for a handed-off build to be resumed rather than replaced.
RESUME_IDS: tuple[str, ...] = ('scenario_set', 'eval_set')


def handed_off_ids(phases: list) -> dict[str, str]:
    """The ids the handed-off build's phase records carry — the last one recorded per kind since the last reset —
    or `{}` when the record's build did not hand off."""
    from bench_manifest import RESET_PHASE, handed_off  # noqa: PLC0415 - scripts/ is on sys.path above

    records = [p for p in phases if isinstance(p, dict)]
    if not handed_off(records):
        return {}
    cut = max((i for i, p in enumerate(records) if p.get('phase') == RESET_PHASE), default=-1)
    ids: dict[str, str] = {}
    for record in records[cut + 1 :]:
        recorded = record.get('ids')
        for kind, value in recorded.items() if isinstance(recorded, dict) else ():
            if isinstance(value, str) and value.strip():
                ids[kind] = value.strip()
    return ids


def _resumed_handoff(workdir: Path) -> str | None:
    """Why this re-arm resumes the handed-off build (its set ids are the workspace's), or None."""
    from bench_manifest import SUITE_IDS, _marker, _suite_state  # noqa: PLC0415

    doc, _unreadable = _suite_state(workdir)
    if doc is None:
        return None
    ids = handed_off_ids(doc['phases'])
    if not all(ids.get(kind) and _marker(workdir, SUITE_IDS[kind]) == ids[kind] for kind in RESUME_IDS):
        return None
    named = ' and '.join(f'{kind.replace("_", " ")} {ids[kind]}' for kind in RESUME_IDS)
    return f'its phase record ends at a hand-off whose {named} the workspace still names — a resume of that build'


def mark_run_stale(findings: Path, keep_stages: tuple[str, ...]) -> int:
    """Set `run_stale` on every confirmed row a new build would have pruned; the number of rows it newly marks."""
    import record_finding  # noqa: PLC0415

    store = record_finding.store_path(findings)
    rows = record_finding.read_rows(store)
    marked = 0
    for row in rows.values():
        stage = str(row.get('found_during') or '').split(' ')[0]
        if row.get('status') == 'confirmed' and stage not in keep_stages and row.get('run_stale') is not True:
            row['run_stale'] = True
            marked += 1
    if marked:
        record_finding.write_rows(store, rows)
    return marked


def _unfinished(workdir: Path) -> tuple[str | None, bool]:
    """(why the findings are kept, whether that is a warning) — `(None, False)` for a new build."""
    from bench_manifest import _suite_state, open_build  # noqa: PLC0415 - scripts/ is on sys.path above

    doc, unreadable = _suite_state(workdir)
    if doc is None:
        return f'its phase record cannot be read ({unreadable})', True
    last = open_build(doc['phases'])
    if last is None:
        return None, False
    return f"its phase record ends at '{last.get('phase')} {last.get('status')}' — an unfinished build resumes", False


#: Beside the flow window: the stamping hook's decision, which the Step-0 arm prints (hook stderr
#: never reaches the model) — or, when the hook could not name the agent, completes.
RECORD = '.epoch_opened'
UNDECIDED = 'undecided'


def _resume_progress(command: str, workdir: Path) -> None:
    """The flow entry un-halts the page stage this command owns (`publish_report.resume_progress`). Never raises."""
    try:
        import publish_report  # noqa: PLC0415

        publish_report.resume_progress(workdir, command)
    except Exception as exc:  # noqa: BLE001 - the page catches up at its next fill
        print(f"WARNING(open_epoch): armed '{command}' but its halted page stage could not be resumed ({exc}).", file=sys.stderr)


def _watch_page(workdir: Path, flowdir: Path | None) -> None:
    """The live plan's page gets its watcher (`publish_report.spawn_page_watch`). Never raises, prints nothing: stdout here is the decision line."""
    try:
        import publish_report  # noqa: PLC0415

        publish_report.spawn_page_watch(workdir, flowdir)
    except Exception:  # noqa: BLE001, S110 - the page catches up at its next fill
        pass


def _mint(command: str, flowdir: Path | None, *, resume: bool) -> None:
    """Give the command its run id in ``flowdir``. A resume keeps the window's own. Never raises, never silent."""
    if flowdir is None:
        return
    try:
        import _command_run  # noqa: PLC0415 - hooks/ is on sys.path above

        if resume:
            _command_run.keep_or_open(flowdir)
        else:
            _command_run.open_id(flowdir)
            # A flow supersedes a command scope left by an earlier command.
            try:
                (flowdir / _command_run.SCOPE_MARKER).unlink()
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001 - the id is a label; the window still stands without it
        print(f"WARNING(open_epoch): armed '{command}' but its command run id could not be written to {flowdir} ({exc}) — "
              "its backend jobs will not be grouped.", file=sys.stderr)


def open_scope(command: str, flowdir: Path, *, typed: bool = False) -> str | None:
    """A command that is not a flow starts: open its command scope and return its run id, or None when it could not be written.

    A flow window standing in ``flowdir`` is this command's driver: no scope is opened and the driver's id stands, so
    the jobs of both group together. So does a scope already open when a skill chains another through the Skill tool.
    A command the user types is a new command whatever stands, and a flow that starts later replaces the scope.
    """
    import _command_run  # noqa: PLC0415 - hooks/ is on sys.path above

    if (flowdir / FLOW_MARKER).is_file():
        return _command_run.keep_or_open(flowdir)
    if not typed and (flowdir / _command_run.SCOPE_MARKER).is_file():
        return _command_run.keep_or_open(flowdir)
    try:
        run_id = _command_run.open_id(flowdir)
        (flowdir / _command_run.SCOPE_MARKER).write_text(command, encoding="utf-8")
    except OSError as exc:
        print(f"WARNING(open_epoch): could not open the command scope of '{command}' in {flowdir} ({exc}) — its backend jobs will not be grouped.",
              file=sys.stderr)
        return None
    return run_id


def _handoffs(workdir: Path) -> int | None:
    """How many builds the suite's phase record has handed off (`report landed`); None when it cannot be read."""
    from bench_manifest import HANDOFF_PHASE, _suite_state  # noqa: PLC0415 - scripts/ is on sys.path above

    doc, _unreadable = _suite_state(workdir)
    if doc is None:
        return None
    return sum(1 for p in doc['phases'] if isinstance(p, dict) and (p.get('phase'), p.get('status')) == (HANDOFF_PHASE, 'landed'))


def carry_criticals(workdir: Path) -> str:
    """Rewrite ``artifacts/carried_criticals.json`` from the confirmed build findings this arm is about to prune: each
    critical one no regression folder replays (``regressions.carried_criticals``), for the next final draw to pin. The file
    is stamped with the hand-off count it was carried at (`carried_at_handoff`): an arm with no such row to prune leaves an
    earlier arm's file standing only while no build has handed off since (a scan arm, then the suite's), and deletes it
    otherwise, so a carry lasts one build. Returns the sentence the decision line adds; never raises — a failure is said, loudly."""
    import record_finding  # noqa: PLC0415
    import regressions  # noqa: PLC0415

    path = workdir / 'artifacts' / regressions.CARRIED_CRITICALS_FILENAME
    try:
        handoffs = _handoffs(workdir)
        rows = record_finding.read_rows(workdir / 'artifacts' / 'agent_findings.jsonl')
        if not any(r.get('status') == 'confirmed' and str(r.get('found_during') or '').split(' ')[0] != 'scan' for r in rows.values()):
            if handoffs is not None and path.is_file():
                try:
                    stamp = json.loads(path.read_text(encoding='utf-8')).get('carried_at_handoff')
                except (OSError, ValueError, AttributeError):
                    stamp = None
                if stamp != handoffs:  # carried for a build that has since handed off (or never stamped): spent
                    path.unlink(missing_ok=True)
            return ''
        carried, warning = regressions.carried_criticals(workdir)
        if carried:
            tmp = path.with_name(f'.{path.name}.tmp')
            tmp.write_text(json.dumps({'schema': regressions.CARRIED_CRITICALS_SCHEMA, 'carried_at_handoff': handoffs,
                                       'carried': carried}, indent=2) + '\n', encoding='utf-8')
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - the prune still runs; the lost carry is said
        print(f'WARNING(open_epoch): the critical findings no regression replays could NOT be carried ({type(exc).__name__}: {exc}).',
              file=sys.stderr)
        return ' The critical findings no regression replays could NOT be carried.'
    if warning:
        print(f'WARNING(open_epoch): {warning}.', file=sys.stderr)
    return (f' Carried {len(carried)} critical finding(s) no regression replays into this build (to pin in its final draw).'
            if carried else '')


#: The handed-off build's confirmed findings, saved by the first pruning arm after its hand-off: the next scan's
#: vocabulary carry reads them (the prune leaves only potentials in the store).
PREVIOUS_FINDINGS_FILENAME = 'previous_findings.json'
PREVIOUS_FINDINGS_SCHEMA = 'plurai-previous-findings/1'
PREVIOUS_VARIABLES_FILENAME = 'previous_scenario_variables.yaml'


def save_previous_findings(workdir: Path) -> None:
    """Before a new build's prune, write ``artifacts/previous_findings.json``: every confirmed row (with its section's
    ``trigger``), stamped with the hand-off count (``taken_at_handoff``) and the handed-off build's scenario set, and
    byte-copy that build's ``scenario_variables.yaml`` to ``artifacts/previous_scenario_variables.yaml`` beside it. Nothing
    when no build handed off. An arm at the same hand-off keeps the file the first one wrote (it holds the rows from
    before that prune); a later hand-off replaces it, so it lasts one build. Stdlib only; never raises, warns once."""
    import record_finding  # noqa: PLC0415
    import regressions  # noqa: PLC0415
    from bench_manifest import HANDOFF_PHASE, _suite_state  # noqa: PLC0415

    art = workdir / 'artifacts'
    path = art / PREVIOUS_FINDINGS_FILENAME
    try:
        doc, unreadable = _suite_state(workdir)
        if doc is None:
            print(f'WARNING(open_epoch): the previous build\'s findings were NOT saved for the next scan: its phase record {unreadable}.',
                  file=sys.stderr)
            return
        records = [p for p in doc['phases'] if isinstance(p, dict)]
        marks = [i for i, p in enumerate(records) if (p.get('phase'), p.get('status')) == (HANDOFF_PHASE, 'landed')]
        if not marks:
            return
        if path.is_file():
            try:
                if json.loads(path.read_text(encoding='utf-8')).get('taken_at_handoff') == len(marks):
                    return
            except (OSError, ValueError, AttributeError):
                pass
        rows = record_finding.read_rows(art / 'agent_findings.jsonl')
        md = art / 'agent_findings.md'
        triggers = {f.finding_id: f.trigger for f in regressions.parse_findings(md.read_text(encoding='utf-8') if md.is_file() else '')
                    if f.finding_id and f.trigger}
        saved = [{**row, **({'trigger': triggers[fid]} if fid in triggers else {})}
                 for fid, row in rows.items() if row.get('status') == 'confirmed']
        tmp = path.with_name(f'.{path.name}.tmp')
        tmp.write_text(json.dumps({'schema': PREVIOUS_FINDINGS_SCHEMA, 'taken_at_handoff': len(marks),
                                   'scenario_set': handed_off_ids(records[:marks[-1] + 1]).get('scenario_set'), 'rows': saved},
                                  indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        tmp.replace(path)
        # The handed-off vocabulary, byte for byte beside its findings (same stamp): the scan rewrites
        # scenario_variables.yaml, and a carry re-run after that must still read the previous pool.
        variables, kept = workdir / 'scenario_variables.yaml', art / PREVIOUS_VARIABLES_FILENAME
        if variables.is_file():
            shutil.copyfile(variables, kept)
        else:
            kept.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - the prune still runs; the next scan carries nothing, said
        print(f'WARNING(open_epoch): the previous build\'s findings were NOT saved for the next scan ({type(exc).__name__}: {exc}).',
              file=sys.stderr)


def _unfinished_scan(workdir: Path) -> str | None:
    """Why a scan arm resumes rather than starts a build: an open attempt `simulate_plan.py scan-resume` resumes. None
    when there is none, or when it cannot be read (the arm then opens a new epoch, as it always did)."""
    try:
        import simulate_plan  # noqa: PLC0415 - scripts/ is on sys.path above

        attempt = simulate_plan.resumable_attempt(workdir)
    except Exception as exc:  # noqa: BLE001 - a new epoch is what an arm did before scans resumed
        print(f'WARNING(open_epoch): the open scan attempt could not be read ({exc}); this arm opens a new epoch.', file=sys.stderr)
        return None
    return f"an unfinished scan resumes (its attempt opened {attempt.get('at')})" if attempt is not None else None


def _keep(command: str, workdir: Path, why: str, warn: bool, stale: str) -> str:
    """A resumed build: keep its findings and its run driver record, retire only its STOP rows."""
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path above

    stops = _run_driver.retire_durable(workdir / 'artifacts', command, (_run_driver.INPUT_REQUIRED,))
    print(f"{'WARNING' if warn else 'NOTE'}(open_epoch): agent_findings.md and the run driver record kept — {why}.{stale}", file=sys.stderr)
    return (f"{command} kept: agent findings and this build's run driver record kept — {why}.{stale}"
            + (f' Its {stops} earlier STOP row(s) were retired: the resumed build files a fresh one at any gate still unmet.'
               if stops else ''))


def open_epoch(command: str, workdir: Path | None, flowdir: Path | None = None) -> str:
    """Decide, and on `new` prune and retire; return the ONE line a reader is told ("" when a new
    build cleared nothing, so there is nothing to say). A workdir that
    is not a directory is REFUSED, loudly, as undecided, and returns here with only the run id minted. Any other entry
    un-halts its page stage and starts the page's watcher, and gets its run id in ``flowdir`` too."""
    _mint(command, flowdir, resume=False)
    if workdir is None or not workdir.is_dir():
        print(f"WARNING(open_epoch): armed '{command}' but {workdir or '(nothing)'} is not an agent workdir, so "
              "agent_findings.md and the run driver record were NOT touched — the Step-0 arm, which resolves the agent, "
              "decides it.", file=sys.stderr)
        return f'{command} {UNDECIDED}: evidence epoch not decided yet — no agent workdir resolved.'
    _resume_progress(command, workdir)
    _watch_page(workdir, flowdir)
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path above

    if command == 'generate-suite':
        stale = ''
        try:
            why, warn = _unfinished(workdir)
        except Exception as exc:  # noqa: BLE001 - no verdict keeps the file, loudly
            why, warn = f'its phase record could not be consulted ({exc})', True
        if not why:
            try:
                why = _resumed_handoff(workdir)
            except Exception as exc:  # noqa: BLE001 - no verdict keeps the file, loudly
                why, warn = f'its phase record could not be consulted ({exc})', True
            if why and not warn:
                try:
                    marked = mark_run_stale(workdir / 'artifacts' / 'agent_findings.md', RESETS_FINDINGS[command])
                    stale = (f' {marked} confirmed finding(s) are marked run-stale until the final audit re-records them.'
                             if marked else '')
                except (OSError, ValueError) as exc:
                    warn = True
                    stale = f' Its confirmed findings could NOT be marked run-stale ({type(exc).__name__}: {exc}).'
        if why:
            return _keep(command, workdir, why, warn, stale)
        import regressions  # noqa: PLC0415
        # A new build's cap counts only its own folders: its ledger starts empty (a resume, kept above, keeps it).
        (workdir / 'artifacts' / regressions.BUILD_LEDGER_FILENAME).unlink(missing_ok=True)
    if command == 'scan-agent' and (why := _unfinished_scan(workdir)):
        return _keep(command, workdir, why, False, '')
    retired = _run_driver.retire_durable(workdir / 'artifacts', command, None)
    _run_driver.retire_scope(workdir / 'artifacts', command)  # a new scan proposes its own scope; a resume (kept) keeps it
    if command not in RESETS_FINDINGS:
        return f'{command} new: new build — retired {retired} run driver record row(s) from the previous build.' if retired else ''
    import prune_agent_findings  # noqa: PLC0415

    save_previous_findings(workdir)
    carried = carry_criticals(workdir)
    findings = workdir / 'artifacts' / 'agent_findings.md'
    try:
        before = findings.read_text(encoding='utf-8')
    except (OSError, ValueError):
        before = ''
    # On ANY failure the file is kept: a stale confirmed finding is loud, a lost potential one is not.
    if prune_agent_findings.main(['prune_agent_findings.py', str(findings), ','.join(RESETS_FINDINGS[command])]) != 0:
        print(f"WARNING(open_epoch): armed '{command}' but agent_findings.md could not be pruned, so it was left intact — "
              "it may still carry the PREVIOUS build's confirmed findings. Read it before presenting anything from it.",
              file=sys.stderr)
        return f'{command} kept: agent_findings.md could not be pruned and was left intact.'
    dropped = prune_agent_findings.prune(before, keep_stages=RESETS_FINDINGS[command])[2] if before else 0
    if not dropped and not retired and not carried:
        return ''  # nothing was cleared or retired: there is nothing to say
    return (f'{command} new: new evidence epoch — cleared {dropped} confirmed agent finding(s) and retired {retired} '
            f'run driver record row(s) from the previous build.{carried}')


def resume(command: str, workdir: Path | None, flowdir: Path | None = None) -> int:
    """A same-name re-arm is a resume: keep the decisions, retire this command's STOP rows, un-halt its page stage and
    start the page's watcher. A workdir that is not a directory does neither of the last two."""
    import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path above

    _mint(command, flowdir, resume=True)
    if workdir is None:
        return 0
    if workdir.is_dir():
        _resume_progress(command, workdir)
        _watch_page(workdir, flowdir)
    return _run_driver.retire_durable(workdir / 'artifacts', command, (_run_driver.INPUT_REQUIRED,))


def record(flowdir: Path, line: str | None) -> None:
    """Write the decision beside the window for the Step-0 arm. Never raises."""
    if line is None:
        return
    try:
        (flowdir / RECORD).write_text(line, encoding='utf-8')
    except OSError:
        pass


FLOW_MARKER = '.plurai_flow_active'


def main(argv: list[str]) -> int:
    """`open_epoch.py [--flowdir <dir>] <command> <workdir>` — decide now, print the line (the script's stamp)."""
    flowdir: Path | None = None
    if len(argv) >= 3 and argv[1] == '--flowdir':
        flowdir = Path(argv[2]) if argv[2] else None
        argv = [argv[0], *argv[3:]]
    if len(argv) == 3 and argv[1] in ('--scope', '--typed-scope'):
        if flowdir is None:
            print('usage: open_epoch.py --flowdir <dir> --scope <command>', file=sys.stderr)
            return 64
        run_id = open_scope(argv[2], flowdir, typed=argv[1] == '--typed-scope')
        if run_id:
            print(run_id)
        return 0
    if len(argv) == 4 and argv[1] == '--resume':
        stops = resume(argv[2], Path(argv[3]) if argv[3] else None, flowdir)
        if stops:
            print(f'flow-window: resumed — {stops} earlier STOP row(s) retired; any gate still unmet files a fresh one.')
        return 0
    if len(argv) != 3:
        print('usage: open_epoch.py [--flowdir <dir>] <command> <workdir>', file=sys.stderr)
        return 64
    workdir = Path(argv[2]) if argv[2] else None
    # This arm runs only for a RESOLVED root (flow-window.sh refuses the rest), so a workdir that
    # does not exist yet is a first scan: no findings file, nothing to prune. The command still gets its run id.
    if workdir is not None and not workdir.exists():
        _mint(argv[1], flowdir, resume=False)
        line = None
    else:
        line = open_epoch(argv[1], workdir, flowdir)
    if line:
        print(f'flow-window: {line.split(": ", 1)[1]}')
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv))
