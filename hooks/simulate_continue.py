#!/usr/bin/env python3
"""The /simulate run's continuation: its plan file, its one writer, and the decision report_guard asks.

A LIBRARY PLUS A CLI, NOT A REGISTERED HOOK. Claude Code runs the hooks of one Stop event in
parallel, so a second Stop hook would push "next stage" while ``report_guard`` blocked the same
message for a malformed hand-off — the hand-off would escape validation — and it would race
``report_guard``'s window close. So ``report_guard.main()`` calls :func:`decide` at exactly two
points, and blocks with what it returns only where it would itself have allowed the stop:

* ``event="handoff"`` — right after a clean, anchored hand-off closed window command C. A failure
  hand-off HALTS the run. Otherwise, when C's stage is in the plan, ``done`` records it — only when
  it LANDED (``scripts/stage_landed.py``, the one predicate the page's stage state reads too): its
  proof file (:data:`STAGE_PROOF`) was written since the stage began, and the report's files for it
  exist. A hand-off that did not land halts too: a stage is never advanced past unverified, and
  never re-run blindly.
* ``event="idle"`` — a turn end with no window armed (between stages, after a present-only run).
  A failure hand-off here HALTS the run too: a stage's error-posture exit closes its own window
  before it renders, so its failure hand-off reaches this event, not ``handoff`` — and pushing
  "continue with stage <same stage>" over it re-rendered the failure two or three times (RT37 live).

A halt is TYPED where it is made (``halt: {kind, at}`` beside ``halted``): ``capacity`` and ``credit`` (the backend's
stop sentences, ``report_guard._stop_text_kind``; ``capacity`` also holds the unavailable stop and ``credit-unavailable``,
which prints the contact text), ``contact`` (an issue on our side), ``proof`` (a landing nothing on
disk proves; ``proof_halt`` too), ``untyped`` (a bare failure, or a plan written before kinds). What lifts each:

* ``capacity`` / ``credit`` (:data:`LIFTABLE`): the first message a person types after the halt (``flow_arm`` ->
  :func:`record_user_turn`). That message's turn is the run's, not the user's floor, as after the account's usage
  limit: its turn end continues the run (the lift records the message's digest, read back from the turn's own prompt
  on every harness). The same message tells the model the run goes on at once (:func:`lift_context`, answered by
  ``flow_arm``), since the stop text it printed names a time to wait. ``resume`` lifts one only when a person typed
  after the halt.
* ``contact``: never by a message. The first message a person types after it is recorded on the halt
  (``halt.typed_at``, :func:`typed_after_halt`), which ``halt_gate`` reads: the job-start gate holds only until a
  person speaks; the halt holds until the run is directed.
* every kind: a landing of the halted stage (its hand-off with fresh proof, ``done``, limited mode's ``handoff``), a
  ``rewind``, a ``skip``. ``resume`` never resets a build: a suite that stopped is finished where it stopped.

A lift keeps ``last_halt`` and never resets ``stalls``: a backend that refuses again halts again, and the stall bound
still stands the run down.

It BLOCKS only when every one of these holds: the plan is live (``_run_driver.autonomous_plan``)
and not ``halted``; stages remain; the turn does not answer a message the user TYPED after the
plan's last write (a user who said stop, or asked a side question, gets the floor — feedback that
routes to a stage rewrites the plan through ``rewind`` and the run resumes; the first message after
the account's usage limit stopped the run, or after a capacity or credit halt, is the run going on); the run is not
``paused`` (an Esc, recorded by :func:`record_interrupt` at the first turn end after it: the turns after
it are started by background completions, and they never advance the plan — only ``rewind`` /
``resume`` / ``end``, which /simulate runs on the user's next message, lift it); and, for
``idle``, no ask is outstanding and no work is in flight. The last stage's landing earns ONE closing block, which
is exempt from the "live" condition only. Three consecutive ``idle`` blocks with no stage landing
stand the run down (``finished``, ``stalled_at``) — never ``report_guard``'s strike counter.

The account's usage limit PAUSES the run (:class:`_UsageLimit`): while the harness's newest usage-limit note since
the plan began says the limit was reached, nothing is pushed, no stall is counted, nothing stands the run down, and
an unproven landing (the wrap-up the harness asks for) halts nothing. The first turn end after the harness lifts the
limit continues the run, and a lift newer than the plan's last write keeps a plan older than the TTL live. A limit that
refuses a turn outright (an API error, no grace) ends it where no Stop hook runs, so nothing is pushed or counted then.
Under either shape, the first message a person types after the limit stopped the run resumes it: that turn is the run's,
not the user's floor, so its stage's hand-off continues the run (and keeps a plan older than the TTL live).

Fail-open everywhere: any error is "no block".

``end`` is the user's stop, so the writer holds it: while a stage is unfinished it exits 3 unless a message a person
typed since the run was last directed (``begin``, ``rewind``, ``resume`` each consume the record) asked for it. ``flow_arm`` records when a person last typed to the run
(:data:`USER_TURN_MARKER`, :func:`record_user_turn`); a typed message that answered a gate (``ask_answered``) is the
run's own question being met, not a stop. A finished chain, a halted run, an Esc pause and a plan nobody wrote for
a day need no message, except a run halted by a stop the model printed (:func:`record_printed_stop`): it ends only on the
user's own message, so ending it for the stop itself cannot lift the job-start gate.

CLI (behind ``scripts/flow-window.sh simulate …``, the classifier-safe single script):

  simulate_continue.py begin <stage>[,<stage>...] --flowdir D [--workdir W]
  simulate_continue.py done <stage>     --flowdir D [--workdir W]   exit 3 unless the stage landed
  simulate_continue.py rewind <stage>   --flowdir D [--workdir W]   no plan in this session: begins one from <stage>
  simulate_continue.py resume           --flowdir D [--workdir W]   reopens a run stalls stood down; an ended or complete run stays closed
  simulate_continue.py end              --flowdir D [--workdir W]   exit 3 while a stage is unfinished and no user stop is recorded
  simulate_continue.py next             --flowdir D [--workdir W]   prints how the run goes on; writes nothing
  simulate_continue.py handoff <command> --flowdir D [--workdir W]  ``done`` for <command>'s stage, then ``next``

``next`` and ``handoff`` are limited mode's (``_workdir.limited_mode``): with no Stop hook, nothing calls :func:`decide`, so the
model asks for the next step itself right after a hand-off (``flow-window.sh handoff <command>``). ``next`` reads the
plan and says what :func:`decide` would block with, with none of its writes: no stall count, no ``closing_sent``, no
halt, and the record of the user's last message is left for ``end``; its continuation tells the model to run
``handoff`` after the next stage's hand-off, in the same turn, where :func:`decide`'s tells it to end the turn. ``handoff``
acts only for ``simulate`` or the command of the stage the run is at (any other command ran on its own, and resumes no
plan it finds): it records that stage as ``done`` exactly as ``done`` does (exit 3 and the missing-proof line when it did
not land), then prints ``next``. In limited mode ``end`` is never refused: the record it reads is written by a hook
that does not run there.

``rewind`` to the suite or any stage before it also resets an unfinished suite build on record
(``bench_manifest.reset_suite``), so the suite the run redoes is a new build, not the old one resumed. ``resume`` never
resets anything: the run it continues picks its stage up where it stopped.

Exit 0 done; 1 the plan could not be written; 3 refused (no proof, no plan, not a stage of this
run); 64 usage.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HOOKS = Path(__file__).resolve().parent
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import _run_driver  # noqa: E402 - hooks/ is on sys.path above
from _harness import transcript  # noqa: E402 - hooks/ is on sys.path above

_SCRIPTS = _HOOKS.parent / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.append(str(_SCRIPTS))

from grounding_files import BOUNDARIES  # noqa: E402 - scripts/ is on sys.path above
from stage_landed import STAGE_PROOF, _not_handed_off, proof_fresh, scored_run_json, stage_gaps, stage_landed  # noqa: E402, F401 - scripts/ is on sys.path above

PLAN_MARKER = _run_driver.PLAN_MARKER
SCHEMA = _run_driver.PLAN_SCHEMA
#: Every stage a plan may hold, in the only order a run takes them.
STAGES = ('scan', 'traces', 'suite', 'run', 'fixes')
#: Flow-window command -> the stage its validated hand-off completes.
STAGE_OF_COMMAND = {'scan-agent': 'scan', 'traces': 'traces', 'generate-suite': 'suite', 'run': 'run', 'fixes': 'fixes'}
#: The stages a user may ask to leave out. The rest are what a simulation is: skipping one leaves nothing to report on.
OPTIONAL_STAGES = ('traces',)
#: Consecutive idle blocks with no stage landing before the run stands down.
STALL_LIMIT = 3
#: What a halt can be (``plan['halt']['kind']``): see the module docstring for what lifts each.
HALT_KINDS = (_run_driver.STOP_CAPACITY, _run_driver.STOP_CREDIT, _run_driver.STOP_CONTACT, 'proof', 'untyped')
#: The halts a person's next typed message lifts: a backend stop the user waits out or pays past.
LIFTABLE = (_run_driver.STOP_CAPACITY, _run_driver.STOP_CREDIT)
_SKILL = _HOOKS.parent / 'skills' / 'simulate' / 'SKILL.md'


def feedback_routes() -> list[list[str]]:
    """``[[label, invocation], …]`` from THE table, ``scripts/simulate_plan.py`` ``FEEDBACK_ROUTES``, loaded
    by path (one copy). Written into every plan at ``begin`` and named in every block reason, because
    /simulate's own text may be compacted out of context mid-run and the model cannot re-invoke it.
    Empty when the table cannot be loaded — the reason then points at the skill file instead."""
    try:
        import importlib.util  # noqa: PLC0415

        spec = importlib.util.spec_from_file_location('_simulate_plan_routes', _SCRIPTS / 'simulate_plan.py')
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[spec.name] = module  # type: ignore[union-attr]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return [[str(row[0]), str(row[1])] for row in module.FEEDBACK_ROUTES]
    except Exception:  # noqa: BLE001 - a missing table costs the reason its routes, never the run
        return []


def reset_suite_build(workdir: Path | None, why: str) -> str | None:
    """Set aside an unfinished suite build on record (``scripts/bench_manifest.py`` ``reset_suite``, the
    phase record's one writer), so the next suite run rebuilds instead of resuming it. The line it says,
    or None: nothing to reset, or the record could not be reached — the suite's own resume reports that."""
    if workdir is None:
        return None
    try:
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        import bench_manifest  # noqa: PLC0415 - scripts/ is on sys.path above

        return bench_manifest.reset_suite(workdir, why)
    except Exception:  # noqa: BLE001 - the rewind itself already landed
        return None


# ── time ──────────────────────────────────────────────────────────────────────────────────────


def now_iso() -> str:
    """Now, UTC, to the second. Truncation only ever makes a later file look newer than ``since``."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


# ── the stage proofs ──────────────────────────────────────────────────────────────────────────
# STAGE_PROOF, scored_run_json and the landing predicate live in scripts/stage_landed.py, beside the page's reader.


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _since(stage: str, plan: dict) -> float | None:
    return _epoch((plan.get('since') or {}).get(stage)) if isinstance(plan.get('since'), dict) else None


def _existing_dir(candidate: object) -> bool:
    """Is ``candidate`` a workdir that is there: a local path (the plan is a file a command can write, so on Windows a
    UNC path is never touched — checked FIRST) naming a directory that exists."""
    import _platform  # noqa: PLC0415 - hooks/ is on sys.path above

    try:
        return (bool(candidate) and _platform.is_local_drive_path(str(candidate), within=_platform.cwd_roots())
                and Path(str(candidate)).is_dir())
    except (OSError, ValueError):
        return False


def _named_agent() -> Path | None:
    """The agent the run named (``PLURAI_AGENT_PATH``, else ``$FLOWDIR/.agent_path`` — ``_workdir``'s reading), resolved
    as the hooks' resolver resolves it, when it exists: a module file or its directory. None when nothing is named."""
    try:
        import _platform  # noqa: PLC0415 - hooks/ is on sys.path above
        import _workdir  # noqa: PLC0415 - hooks/ is on sys.path above

        named = _workdir._named_agent()  # noqa: SLF001 - the one reading of the named agent
        if not named:
            return None
        path = Path(named).expanduser().absolute()  # a relative name is the cwd's, as `_agent_scope` anchors it
        # A file a command can write names it: on Windows never followed to another host (UNC), as `_agent_scope` does.
        if not _platform.is_local_drive_path(path, within=(Path.cwd().resolve(),)):
            return None
        path = path.resolve()
        return path if path.exists() else None
    except Exception:  # noqa: BLE001 - nothing named is a fact, never an error
        return None


def plan_workdir(plan: dict, given: str | Path | None = None) -> Path | None:
    """Where the stage proofs are read: the caller's resolved ``$WORKDIR``, else the one ``begin``
    recorded — each only when it is a directory that exists — else the hooks' own resolution, from the
    agent the run named first, then from the cwd.

    A path that does not exist proves nothing: an agent in a repo subfolder, begun before its scan made
    ``<agent>/plurai_sim/``, had the repo root's ``plurai_sim`` recorded, which never comes to exist."""
    for candidate in (given, plan.get('workdir')):
        if _existing_dir(candidate):
            return Path(str(candidate))
    try:
        from _workdir import resolve_workdir  # noqa: PLC0415 - hooks/ is on sys.path above

        named = _named_agent()
        if named is not None:
            found = resolve_workdir(named if named.is_dir() else named.parent)
            if _existing_dir(found):
                return found
        return resolve_workdir()
    except Exception:  # noqa: BLE001 - no workdir proves nothing
        return None


def proof_holds(stage: str, plan: dict, workdir: Path | None) -> bool:
    """Did ``stage`` land since ``since[stage]`` (``stage_landed``)? False on anything unreadable."""
    since = _since(stage, plan)
    return since is not None and workdir is not None and stage_landed(stage, workdir, since)


def proof_written(stage: str, plan: dict, workdir: Path | None) -> bool:
    """Was ``stage``'s proof file written since ``since[stage]``: the stage ran, landed or not."""
    return proof_fresh(stage, workdir, _since(stage, plan))


# ── the one writer ────────────────────────────────────────────────────────────────────────────


#: The plan fields the page's stage states are drawn from: a write that moves one is a transition.
_SHOWN = ('stages', 'done', 'halted', 'finished', 'since', 'started_at')


def write_plan(path: Path, plan: dict) -> None:
    """Atomically replace the plan. Raises OSError; never creates the directory (no phantom workdir).
    A transition (:data:`_SHOWN` moved) also rebuilds ``flow_progress.json`` (:func:`refresh_progress`)."""
    before = _read_json(path)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        tmp.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if before is None or any(before.get(key) != plan.get(key) for key in _SHOWN):
        refresh_progress(path.parent, plan)


def refresh_progress(flowdir: Path, plan: dict) -> None:
    """Rebuild the local ``flow_progress.json`` from the plan just written (``publish_report.refresh_progress``),
    so a halt, a landing, a rewind or the end shows in the file without waiting for the next page fill.
    Fail-open: the plan write already landed, and the next fill rewrites the file anyway."""
    try:
        workdir = plan_workdir(plan)
        if workdir is None:
            return
        import publish_report  # noqa: PLC0415 - scripts/ is on sys.path above

        publish_report.refresh_progress(workdir, flowdir)
    except Exception:  # noqa: BLE001 - never breaks the plan write
        return


# ── the halt ──────────────────────────────────────────────────────────────────────────────────


def _halt_kind(failure: object) -> str:
    """The kind a failure hand-off halts with: ``report_guard._stop_text_kind``'s answer, else ``untyped`` (a bare
    ``True``, or anything else no kind names) — never lifted by typing."""
    return failure if isinstance(failure, str) and failure in HALT_KINDS else 'untyped'


def halt_kind(plan: dict) -> str | None:
    """The kind of the plan's halt, or None when it is not halted. A plan with no ``halt`` record (written before
    kinds) is ``proof`` when its ``proof_halt`` names the halted stage, else ``untyped``."""
    halted = plan.get('halted')
    if not halted:
        return None
    record = plan.get('halt')
    kind = record.get('kind') if isinstance(record, dict) else None
    if kind in HALT_KINDS:
        return str(kind)
    return 'proof' if plan.get('proof_halt') == halted else 'untyped'


def typed_after_halt(plan: dict) -> bool:
    """Whether a person typed a message since the plan's halt was made (:func:`record_user_turn` stamps ``typed_at``
    on a contact halt). ``halt_gate`` opens on it; the halt itself stays until the run is directed."""
    record = plan.get('halt') if plan.get('halted') and isinstance(plan.get('halt'), dict) else None
    return bool(record and record.get('typed_at'))


def record_printed_stop(bases: list[Path], kind: str, args: list[str]) -> bool:
    """Halt the run's plan with ``kind`` because ``scripts/stop_message.py`` just printed that stop (``args`` are its
    arguments, kept so a later turn end can name the exact command). Only a plan with a stage still owed takes it: a
    stop printed with no run or after the last stage changes nothing. Over a halt no one has typed after, it changes
    nothing either; over a contact halt a person typed after (:func:`typed_after_halt`), a job ran through the open
    gate and stopped, so the new stop replaces the halt and the gate holds until the next typed message. Returns
    whether it recorded. Never raises: the stop's text is printed either way."""
    try:
        path = _run_driver.find_plan(bases)
        plan = _run_driver.read_plan(path)
        if path is None or plan is None or plan.get('finished') is True or plan.get('halted') and not typed_after_halt(plan):
            return False
        remaining = _run_driver.remaining_stages(plan)
        if kind not in LIFTABLE and kind != _run_driver.STOP_CONTACT or not remaining:
            return False
        _set_halt(plan, remaining[0], kind)
        plan['halt']['stop_args'] = list(args)
        write_plan(path, plan)
        return True
    except Exception:  # noqa: BLE001 - recording is best-effort
        return False


def printed_stop_args(plan: dict) -> list[str] | None:
    """The ``stop_message.py`` arguments of the stop that halted ``plan``, when the halt was recorded as that stop was
    printed (:func:`record_printed_stop`), else None: a halt made from a final message has no command to name."""
    record = plan.get('halt') if plan.get('halted') and isinstance(plan.get('halt'), dict) else None
    args = record.get('stop_args') if record else None
    return args if isinstance(args, list) and all(isinstance(a, str) for a in args) else None


def _set_halt(plan: dict, stage: str, kind: str) -> None:
    """Halt the plan at ``stage`` with ``kind``: ``halted`` as every reader reads it, plus ``halt = {kind, at}``. A
    ``proof`` halt also writes ``proof_halt`` (the field ``next_reason`` and an older reader key on). The record is new
    each time, so a contact halt carries no ``typed_at`` until a person types after this halt."""
    plan['halted'] = stage
    plan['halt'] = {'kind': kind, 'at': now_iso()}
    if kind == 'proof':
        plan['proof_halt'] = stage


def _clear_halt(plan: dict, *, by: str | None = None) -> None:
    """Lift the plan's halt, keeping what it was in ``last_halt`` (``by``: the digest of the typed message that lifted
    it, which the turn that message started reads back). ``stalls`` is never touched: a backend that refuses again
    halts again, and the stall bound still stands a run down that nobody moves on."""
    halted = plan.get('halted')
    if halted:
        record = plan.get('halt') if isinstance(plan.get('halt'), dict) else {}
        plan['last_halt'] = {'stage': halted, 'kind': halt_kind(plan), 'at': record.get('at'), 'lifted_at': now_iso(), 'by': by}
    plan['halted'] = None
    plan.pop('halt', None)
    plan.pop('proof_halt', None)


def prompt_digest(text: str) -> str:
    """The digest of what a person typed (``flow_arm.typed_text``, whitespace folded): the hook that lifts a halt and the
    transcript reader of the turn it starts each see the same message, spelled by their own harness."""
    import hashlib  # noqa: PLC0415

    from flow_arm import typed_text  # noqa: PLC0415 - hooks/ is on sys.path above

    return hashlib.sha256(' '.join(typed_text(text).split()).encode('utf-8')).hexdigest()


def _lifted_by(plan: dict) -> str | None:
    last = plan.get('last_halt')
    by = last.get('by') if isinstance(last, dict) else None
    return by if isinstance(by, str) and by else None


def _own_prompt_lifted(stop: Any, guard: Any, by: str) -> bool:
    """Was the turn ending now started by the typed message that lifted the halt (digest ``by``)? The turn's OWN prompt
    only (``transcript.prompts_on_thread``'s first, which every harness's reader yields): no tail walk, no ordering
    between the hook's plan write and the transcript's stamp. Anything unreadable is False."""
    typed = getattr(guard, 'human_typed', None)
    if typed is None:
        return False
    prompts = transcript.prompts_on_thread(str(getattr(stop, 'harness', '') or ''), str(getattr(stop, 'transcript_path', '') or ''),
                                           str(getattr(stop, 'session_id', '') or ''))
    if not prompts:
        return False
    own = prompts[0]
    return not own.injected and not own.interrupted and bool(typed(own.text)) and prompt_digest(own.text) == by


def new_plan(stages: list[str], *, workdir: str | None) -> dict:
    started = now_iso()
    return {
        'schema': SCHEMA,
        'started_at': started,
        'stages': stages,
        'done': [],
        'since': {stage: started for stage in stages},
        'stalls': 0,
        'halted': None,
        'finished': False,
        'closing_sent': False,
        'routes': feedback_routes(),
        # Only a workdir that is there: before its scan, an agent in a repo subfolder resolves to the repo root's.
        'workdir': workdir if _existing_dir(workdir) else None,
    }


# ── what report_guard asks ────────────────────────────────────────────────────────────────────


def _agent_path(workdir: Path | None) -> str:
    """The agent the scan runs on: the one the run named (its module file or directory, as /simulate's own stage table
    reads "the path the user typed"), else the one the workdir belongs to (a repo-root agent's, also before its scan)."""
    named = _named_agent()
    if named is not None:
        return str(named)
    if workdir is not None:
        for parent in (workdir, *workdir.parents):
            if parent.name == 'plurai_sim':
                return str(parent.parent)
    return '<agent path>'


#: Where ``scripts/simulate_plan.py traces --connect`` records the trace source: the workdir's state,
#: else — a project with no workdir yet — the pending record beside the plan.
_STATE_FILES = ('.simulate_state.json', '.simulate_state_pending.json')


def _traces_args(workdir: Path | None, flowdir: Path | None) -> str:
    for base, name in ((workdir, _STATE_FILES[0]), (flowdir, _STATE_FILES[1])):
        state = _read_json(base / name) if base is not None else None
        traces = state.get('traces') if state is not None else None
        provider = traces.get('provider') if isinstance(traces, dict) else None
        project = traces.get('project') if isinstance(traces, dict) else None
        if isinstance(provider, str) and provider and isinstance(project, str) and project:
            return f'--source {provider} --project {project}'
    return '--source <the provider the user connected> --project <their project>'


def invocation(stage: str, plan: dict, workdir: Path | None, flowdir: Path | None = None) -> str:
    """How the run continues into ``stage`` — one line, so a compacted context still carries it."""
    if stage == 'scan':
        return f'/simulation-scan-agent {_agent_path(workdir)} --force'
    if stage == 'traces':
        return f'/analyze-traces {_traces_args(workdir, flowdir)}'
    if stage == 'suite':
        return '/simulation-generate-suite'
    if stage == 'run':
        since = plan.get('started_at') or '<started_at>'
        return (f'`python3 {_word("simulate_plan.py")} run-stage "$WORKDIR" --since {since}` first: on `run`, '
                f'/simulation-run; on `present`, the /simulate skill\'s present-only run step (the run-summary line, the '
                f'overview block with `--step run --as simulate`, then `flow-window.sh simulate done run`)')
    return '/simulation-fixes'


def _routes(plan: dict) -> str:
    routes = plan.get('routes') if isinstance(plan.get('routes'), list) and plan.get('routes') else feedback_routes()
    parts = [f'{r[0]} -> {r[1]}' for r in routes if isinstance(r, list) and len(r) == 2]
    return '; '.join(parts) or f'see {_SKILL}'


def boundary_step(stage: str) -> str:
    """The clause that sends the run through the /simulate skill's stage boundary first, or '' before a stage that has none.
    Written into the reason because /simulate's own text may be compacted out of context mid-run."""
    if stage not in BOUNDARIES:
        return ''
    return (f'first run the "Stage boundary" step of {_SKILL} for {stage} (it adopts a newer settled grounding version, or prints '
            'ADOPT=none and changes nothing). Write no text before the stage\'s invocation: the boundary step and the stage '
            'start are silent. Then ')


#: The flow-window command whose hand-off ends each stage (``STAGE_OF_COMMAND`` turned around).
COMMAND_OF_STAGE = {stage: command for command, stage in STAGE_OF_COMMAND.items()}


def _after_handoff(stage: str, limited: bool) -> str:
    """What the turn does once the stage hands off: with hooks, it ends there and the Stop hook continues the run; in
    limited mode (no Stop hook) the model asks for the next step itself, in the same turn."""
    if not limited:
        return 'end your turn on its hand-off'
    flow_window = f'bash {_word("flow-window.sh")}'
    present = f'; on `present`, after `simulate done run`, run `{flow_window} simulate next` instead' if stage == 'run' else ''
    return (f'right after its hand-off, run `{flow_window} handoff {COMMAND_OF_STAGE.get(stage, stage)}` and do what it '
            f'prints, in the same turn{present}')


def continue_reason(stage: str, plan: dict, workdir: Path | None, flowdir: Path | None = None, *, limited: bool = False) -> str:
    """The line that sends the run into ``stage``. ``limited``: the wording of limited mode (``next`` and ``handoff``)."""
    return (f'The simulation continues with stage {stage}: {boundary_step(stage)}invoke {invocation(stage, plan, workdir, flowdir)} now '
            f'({_after_handoff(stage, limited)}). Feedback routes: {_routes(plan)}; a route that redoes a stage runs `flow-window.sh simulate '
            f'rewind <that stage>` BEFORE its invocation. The /simulate steps are in {_SKILL}; re-read them if they are no longer in context.')


#: The remedy for a suite build that stopped before its hand-off: finishing it, never re-recording its anchor.
UNFINISHED_SUITE_REMEDY = 'the suite build has not finished yet — run /simulate and choose Finish the suite to pick it up where it stopped.'


def _word(script: str) -> str:
    """``scripts/<script>`` as one quoted word for a command the text tells the model to run (``plugin_root.shell_path``)."""
    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path above

    return shell_path(_SCRIPTS / script)


def missing_proof_reason(stage: str, workdir: Path | None) -> str:
    """The line a halt on a landing nothing proves says: what is missing and what records it. A suite anchor recorded
    before the build handed off (`stage_landed._not_handed_off`) is lifted only by finishing that build."""
    if stage == 'suite' and workdir is not None and _not_handed_off(stage, workdir):
        return f'The /simulate run stopped at stage suite: {UNFINISHED_SUITE_REMEDY} Say in ONE line what is missing.'
    try:
        finder = STAGE_PROOF.get(stage)
        proof = finder(workdir) if finder is not None and workdir is not None else None
    except OSError:
        proof = None
    missing = f'`{proof}`' if proof is not None else f"the proof file of stage {stage}"
    how = (f'Once it is written, `bash {_word("flow-window.sh")} simulate done {stage}` lifts the halt and the run '
           'continues.')
    if stage == 'suite':
        how = (f'`lane_pin.py anchor` records it, also for a suite built without a run: run '
               f'`python3 {_word("lane_pin.py")} anchor` and use the rung it prints, then '
               f'`bash {_word("flow-window.sh")} simulate done suite` — the run continues from there.')
    return (f'The /simulate run stopped at stage {stage}: {missing} was not written since the stage began, so the run '
            f'does not advance past it. {how} Say in ONE line what is missing.')


def unrecorded_reason(stage: str) -> str:
    """The line a halt on a stage that wrote its file without a recorded hand-off says: what happened and what lifts it."""
    flow_window = f'bash {_word("flow-window.sh")}'
    return (f'The /simulate run stopped at stage {stage}: it wrote its file but no hand-off recorded it, so the run neither '
            f'advances past it nor runs it again. If the stage is complete, `{flow_window} simulate done {stage}` records it and '
            f'the run continues; to redo it, run `{flow_window} simulate rewind {stage}`. Say in ONE line which applies.')


#: The closing block's opening words (:func:`is_closing_reason`).
CLOSING_REASON_LEAD = 'The simulation is complete: '


def is_closing_reason(reason: str) -> bool:
    """Is ``reason`` the closing block? It ends a finished run and is no fault, so ``report_guard`` sends it where the
    user sees feedback, not an error."""
    return reason.startswith(CLOSING_REASON_LEAD)


def closing_reason(plan: dict) -> str:
    since = plan.get('started_at') or '<started_at>'
    from plugin_root import shell_path  # noqa: PLC0415 - hooks/ is on sys.path above

    try:
        workdir = plan_workdir(plan)
    except Exception:  # noqa: BLE001 - the shell resolves it instead
        workdir = None
    # The workdir `begin` recorded, spelled as one word: no cwd resolution and no shell variable stand between the
    # closing turn and its message. Only a plan with none falls back to resolving it in the shell.
    where = shell_path(workdir) if workdir is not None else '"$WORKDIR"'
    first = '' if workdir is not None else f'source {_word("resolve-workdir.sh")} && '
    return (f'{CLOSING_REASON_LEAD}run `{first}python3 {_word("simulate_plan.py")} closing {where} --since {since}`, '
            f'then `bash {_word("flow-window.sh")} simulate end`; then your last message is exactly what `closing` printed, '
            'verbatim — nothing before or after it (it states the outcome, the links, the defaults the run took and the '
            'next command). No further stage.')


def end_page_steps() -> str:
    """The run's one publish when it ends with no hand-off to carry it: the user's stop (``simulate end`` while a stage is
    left) and a limited-mode halt (``simulate next``). Fill ``--step end``, publish its ``report`` exactly as given, run
    ``done``; a refused fill whose ``retry`` is true is repaired once and filled again, and any other fill that does not
    publish only says its ``say`` lines and is never filled again."""
    setup = f'source {_word("resolve-workdir.sh")} && source {_word("resolve-python.sh")} && "$PLURAI_PY" {_word("publish_report.py")}'
    return (f'Then publish the overview page once: run `{setup} fill "$WORKDIR" --step end --flowdir "$FLOWDIR"`. When its '
            '`publish` is true, publish the file at `report` with the Artifact tool, passing `report` as `file_path` character '
            'for character, with `icon: "report"` and, when `page` is not null, `url` set to `page` (if that publish is refused '
            'because this conversation has not read the page, `read` it once and publish again; never pass `force`, never '
            f'`Read` the file); then run `{setup} done "$WORKDIR" --step end --flowdir "$FLOWDIR" --url "<the URL the Artifact '
            'tool returned, or - when the publish failed>"` and end with the line(s) it prints, verbatim. When `publish` is '
            'false and `retry` is true, repair once and run the same fill again: `internal_text`, rewrite each line it names '
            'as it says; `numbers`, re-render (`render_run_report.py`, then `release_verdict.py render`); `fill_refused` or '
            '`garbled`, nothing to rewrite. When `publish` is false and `retry` is false, end with its `say` lines, '
            'verbatim, if it has any: never fill again and never run `done`.')


def _ask_outstanding(guard, bases: list[Path]) -> bool:
    return bool(guard.ask_outstanding(bases))


#: Beside the plan: its mtime is the last turn end that read the transcript for an Esc. Never the plan's
#: own mtime — a plan write inside a turn (`simulate done run`) would move that past the Esc unread.
SEEN_MARKER = '.plurai_simulate_seen'


#: Beside the plan: the epoch a person last typed a message to the run, written by ``flow_arm``. `end` reads it.
USER_TURN_MARKER = '.plurai_simulate_user'


def record_user_turn(bases: list[Path], prompt: str) -> str | None:
    """Note that a person typed ``prompt`` to the run that holds a plan in ``bases`` (``flow_arm``, UserPromptSubmit).

    Only a typed message counts: what the harness or this plugin wrote (a heartbeat, a task completion, a flow
    command) is refused by the same predicate ``report_guard`` stands down on. A typed message also lifts a capacity
    or credit halt (:data:`LIFTABLE`), recording its digest so the turn it starts continues the run; the return value
    is then what that turn is told (:func:`lift_context`), else None. A contact halt is not lifted: the first typed
    message is stamped on it (``typed_at``) for ``halt_gate``, which then lets jobs start while the run stays stopped.
    Never raises."""
    try:
        path = _run_driver.find_plan(bases)
        plan = _run_driver.read_plan(path)
        if path is None or plan is None or plan.get('finished') is True:
            return None
        import report_guard as guard  # noqa: PLC0415 - the turn-end predicates' one home

        if guard.human_typed(prompt):
            (path.parent / USER_TURN_MARKER).write_text(repr(time.time()), encoding='utf-8')
            stage = plan.get('halted')
            if halt_kind(plan) in LIFTABLE and isinstance(stage, str):
                _clear_halt(plan, by=prompt_digest(prompt))
                write_plan(path, plan)
                return lift_context(stage, plan, plan_workdir(plan), path.parent)
            if halt_kind(plan) == _run_driver.STOP_CONTACT and isinstance(plan.get('halt'), dict) and not typed_after_halt(plan):
                plan['halt']['typed_at'] = now_iso()
                write_plan(path, plan)
    except Exception:  # noqa: BLE001 - an unrecorded message leaves `end` refused and the job-start gate shut, never a broken prompt
        return None
    return None


def lift_context(stage: str, plan: dict, workdir: Path | None, flowdir: Path | None = None) -> str:
    """What the message that lifted a capacity or credit halt at ``stage`` tells the model. The stop text it printed
    names a time to wait, and nothing else says the message ended the halt. Any typed message lifts, so what follows
    depends on what it asks; going on is `resume`, never `rewind`, which sets the unfinished suite build aside."""
    flow_window = f"bash {_word('flow-window.sh')}"
    return (f"The /simulate run's stop at stage {stage} is lifted by this message. If it asks to go on, or asks for nothing "
            f"else, continue with stage {stage} at once, without waiting and without repeating the stop text: run "
            f"`{flow_window} simulate resume`, then invoke {invocation(stage, plan, workdir, flowdir)}. If it asks to stop, run "
            f"`{flow_window} simulate end`. If it asks a question, answer it, then wait for the user.")


def _consume_user_turn(bases: list[Path]) -> None:
    """The user's message is answered by the write that directs the run: its record is spent."""
    for base in bases:
        try:
            (base / USER_TURN_MARKER).unlink()
        except OSError:
            pass


def _typed_at(bases: list[Path]) -> float | None:
    for base in bases:
        try:
            return float((base / USER_TURN_MARKER).read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            continue
    return None


def _limited(bases: list[Path]) -> bool:
    """Is this session in limited mode (no Stop hook, so no record of a typed message to lift a refusal)?"""
    try:
        import _workdir  # noqa: PLC0415 - hooks/ is on sys.path above

        return bool(_workdir.limited_mode(bases))
    except Exception:  # noqa: BLE001 - the refusal stands
        return False


def end_refusal(plan: dict, path: Path, bases: list[Path]) -> str | None:
    """Why ``end`` must not run now, or None when it may.

    It may when nothing is owed (no stage left, a halt, an Esc pause, a plan nobody wrote for a day) or when a person
    typed a message since the run was last directed (those writes delete the record) that did not answer a gate. In
    limited mode (``_workdir.limited_mode``) it always may: the record of a typed message is ``flow_arm``'s, a hook that does
    not run there, so a refusal could never be lifted."""
    remaining = _run_driver.remaining_stages(plan)
    if plan.get('finished') is True or not remaining or plan.get('paused'):
        return None
    if plan.get('halted'):
        # A stop the model printed halted the run, and its text is the turn's last message. Ending the run for it would
        # finish the plan, which no longer gates a job start. Only a person's own message to stop ends a halted run.
        if printed_stop_args(plan) is None or _typed_at(bases) is not None or _limited(bases):
            return None
        return ('flow-window: the run is already stopped, and the stop text is the last message of this turn: say it and run '
                'nothing else. The run ends when the user asks it to.')
    try:
        import _workdir  # noqa: PLC0415 - hooks/ is on sys.path above

        if _workdir.limited_mode(bases):
            return None
    except Exception:  # noqa: BLE001 - the refusal below stands
        pass
    try:
        if time.time() - path.stat().st_mtime >= _run_driver.PLAN_TTL_SECONDS:
            return None
    except OSError:
        return None
    typed = _typed_at(bases)
    if typed is not None:
        import report_guard as guard  # noqa: PLC0415 - the gate-answer window's one home

        answered = guard.gate_answered_near(bases, typed)
        if not answered:
            return None
    return (
        f"flow-window: refusing to end the run — it still owes {', '.join(remaining)} and no message of the user's asked to stop. "
        "A way forward the user chose at a question (\"continue without it\", \"static-only\") is not a stop. "
        f"Finish stage {remaining[0]} on its hand-off; the run goes on from there."
    )


def stood_down(plan: dict) -> bool:
    """Did turns that never moved the run on stand it down (``stalled_at``), with stages still owed and no ``end``?
    That is the one finished plan ``resume`` reopens: nobody chose to stop it, and nothing it owes was done."""
    return (plan.get('finished') is True and bool(plan.get('stalled_at')) and not plan.get('ended_at')
            and bool(_run_driver.remaining_stages(plan)))


def resume_closed(plan: dict) -> str | None:
    """What ``resume`` says of a plan it leaves closed, or None when the run goes on (or reopens).

    A run that finished every stage, or that was ended, stays closed: nothing is written, and the line says so plainly.
    It exits 0 like any answered message: after a completed run, a side question or an edit still ends on ``resume``."""
    if not _run_driver.remaining_stages(plan):
        return 'every stage of this run is done, so nothing is left to continue'
    if plan.get('finished') is not True or stood_down(plan):
        return None
    return 'this run has ended, so it stays ended and nothing continues by itself'


def resume_line(plan: dict, *, reopened: bool) -> str:
    """What ``resume`` says of the plan it just wrote: never "the run continues" for a run that does not, and never a
    ``rewind`` of the suite (that sets the build aside; a suite that stopped is finished where it stopped)."""
    halted = plan.get('halted')
    if halted:
        kind = halt_kind(plan)
        if kind == 'proof':
            return (f'the run stays stopped at stage {halted} until its file is written; `flow-window.sh simulate done '
                    f'{halted}` then records it and the run goes on')
        if kind in LIFTABLE:
            return (f'the run stays stopped at stage {halted}: the backend refused it, and the next message the user types '
                    'continues it from where it stopped')
        if halted == 'suite':
            return ('the run stays stopped at stage suite and does not go on by itself. Say in ONE line that the user can run '
                    '/simulate and choose Finish the suite to pick the build up where it stopped')
        return (f'the run stays stopped at stage {halted} and does not go on by itself; `flow-window.sh simulate rewind '
                f'{halted}` runs that stage again when the user asks for it')
    remaining = _run_driver.remaining_stages(plan)
    if reopened:
        return f'the run had stood down and is open again; it continues with stage {remaining[0]}'
    last = plan.get('last_halt')
    if remaining and isinstance(last, dict) and last.get('stage') == remaining[0]:
        return f'the run continues with stage {remaining[0]}; a suite build that stopped picks up where it stopped'
    return 'the run continues'


def _interrupt_after(stop: Any, since: float) -> float | None:
    """The newest Esc on the conversation's thread written after ``since`` (epoch), reading newest-first and
    stopping at the first prompt at or before it — so a turn end reads only the rows of the turn it ends.
    None when there is none, and for a harness whose transcript records no Esc."""
    for prompt in transcript.prompts_on_thread(stop.harness, stop.transcript_path, stop.session_id):
        if prompt.epoch is None:
            continue
        if prompt.epoch <= since:
            return None
        if prompt.interrupted:
            return prompt.epoch
    return None


def record_interrupt(stop: Any, bases: list[Path]) -> None:
    """At EVERY turn end of a /simulate run (report_guard calls it first, window or not): an Esc since the
    last turn end pauses the run — ``paused`` in the plan, until ``rewind`` / ``resume`` / ``end``.

    Esc fires no hook, so the first turn end after it is the earliest moment it can be seen: usually a
    turn a background completion started. Reading only the rows since the last turn end (``SEEN_MARKER``)
    keeps it cheap and never depends on the Esc still being in a long tail at a stage's hand-off. An Esc
    from before the user last directed the run (``acted_at``: ``begin``, ``rewind``, ``resume``) is
    already answered. Never raises."""
    try:
        path = _run_driver.find_plan(bases)
        plan = _run_driver.read_plan(path)
        if path is None or plan is None or plan.get('finished') is True:
            return
        seen = path.parent / SEEN_MARKER
        floors = [_epoch(plan.get('acted_at')), _epoch(plan.get('started_at'))]
        try:
            floors.append(seen.stat().st_mtime)
        except OSError:
            pass
        since = max((f for f in floors if f is not None), default=0.0)
        found = _interrupt_after(stop, since)
        try:
            seen.touch()
        except OSError:
            pass
        if found is not None and not plan.get('paused'):
            plan['paused'] = datetime.fromtimestamp(found, timezone.utc).isoformat()
            write_plan(path, plan)
    except Exception:  # noqa: BLE001 - a missed Esc is today's behaviour; a raise would break the guard
        return


def decide(stop: Any, bases: list[Path], event: str, *, command: str | None = None, failure: bool | str | None = None,
           guard=None) -> str | None:
    """The block reason that continues the run, or None to let the turn end. Never raises.

    ``stop`` is the Stop event ``report_guard`` is handling, handed to its ``user_spoke_since`` as it is.
    ``failure`` is the hand-off's failure kind (``report_guard._stop_text_kind``), None for none; a bare ``True`` halts
    ``untyped``. ``guard`` is the ``report_guard`` module (its public turn-end predicates); None imports it.
    """
    try:
        reason = _decide(stop, bases, event, command=command, failure=failure, guard=guard)
    except Exception:  # noqa: BLE001 - fail open: a broken continuation must never wedge a turn
        reason = None
    if failure:
        halt_page(stop, bases, getattr(stop, 'last_message', None))
    elif event == 'handoff' and _run_driver.read_plan(_run_driver.find_plan(bases)) is None:
        halt_page(stop, bases, None)  # a direct call's flow ended: whatever still reads running is stale
    if reason is None:
        return None
    try:
        import session_glossary  # noqa: PLC0415 - the command spelling's one home

        return session_glossary.respell_commands(reason, getattr(stop, 'harness', None) or 'claude')
    except Exception:  # noqa: BLE001 - the block stands; only its spelling is lost
        return reason


def halt_page(stop: Any, bases: list[Path], reason: str | None) -> None:
    """A flow that stopped (a stop hand-off, a turn a usage cap killed, or any direct-call hand-off that left a stage
    running) halts ``flow_progress.json``'s running stage, with ``reason``'s first sentence or the stage's default
    (``publish_report.halt_progress``): a direct call has no plan, so no plan halt rewrites the file. In a ``/simulate``
    run the plan halt already did, and nothing is left running. Never raises."""
    _update_page(stop, bases, 'halt_progress', reason)


def wait_page(stop: Any, bases: list[Path], waiting: bool, *, workdir: Path | None = None) -> None:
    """The page's running step says the flow waits for the user's answer (``waiting``), or says again what it said
    before (``publish_report.wait_progress``). On when a question reaches the user (``ask_guard``) or a turn ends on
    it (``report_guard``); off when an ask's result comes back, answered or not (``ask_answered``), when a person types
    (``flow_arm``), and at session start and end (``session_pin``, ``flow_cleanup``). ``workdir`` is the caller's
    already resolved one, used when no /simulate plan names it. Never raises."""
    _update_page(stop, bases, 'wait_progress', waiting, workdir)


def _update_page(stop: Any, bases: list[Path], update: str, value: Any, resolved: Path | None = None) -> None:
    """``publish_report.<update>(workdir, flowdir, value)`` on the ``flow_progress.json`` the page reads: the plan's
    workdir, else ``resolved``, else the one the cwd resolves to (nothing when there is none), and the session's flow
    dir. Never raises."""
    try:
        import _workdir  # noqa: PLC0415 - hooks/ is on sys.path above
        import publish_report  # noqa: PLC0415 - scripts/ is on sys.path above

        plan = _run_driver.read_plan(_run_driver.find_plan(bases))
        workdir = plan_workdir(plan) if plan is not None else resolved or _workdir.resolve_workdir()
        if workdir is not None:
            getattr(publish_report, update)(workdir, _workdir.flow_dir({'session_id': getattr(stop, 'session_id', None)}), value)
    except Exception:  # noqa: BLE001 - the page catches up on the next fill
        return


class _UsageLimit:
    """The account's usage limit as the turn ending now sees it, read from the transcript at most once.

    Reached (:meth:`holds`) when the harness's newest usage-limit note is not a lift and was written after the plan
    began: the harness itself tells the model to stop, so the turns that end then are a pause, never a stall. Only a
    lift (``release``) ends it — a ``resume`` during the limit does not, or the push the model cannot follow would come
    back. A lift newer than the plan's last write (:meth:`lifted_after`) also means a plan older than the TTL is live
    again: it was paused, not abandoned. A limit that refuses the turn outright (no grace, an API error) ends it through
    the harness's failure event, where no Stop hook runs: nothing is pushed or counted, and it never holds at a turn end.
    Under either shape the FIRST message a person types after the limit stopped the session (:meth:`resumed`) is the run
    going on, not the user taking the floor. ``stop`` may carry no transcript (a unit test's dict): no evidence, no
    limit."""

    def __init__(self, stop: Any) -> None:
        self._stop = stop
        self._read = False
        self._note: tuple[str, float | None] | None = None
        self._reached: float | None = None
        self._resumed: bool | None = None

    def _state(self) -> None:
        if not self._read:
            self._read = True
            self._note, self._reached = transcript.usage_limit_state(self._harness(), self._path())

    def _harness(self) -> str:
        return str(getattr(self._stop, 'harness', '') or '')

    def _path(self) -> str:
        return str(getattr(self._stop, 'transcript_path', '') or '')

    def note(self) -> tuple[str, float | None] | None:
        self._state()
        return self._note

    def holds(self, plan: dict) -> bool:
        return transcript.limit_in_force(self.note(), _epoch(plan.get('started_at')) or 0.0)

    def lifted_after(self, epoch: float) -> bool:
        note = self.note()
        return note is not None and note[0] in transcript.USAGE_LIMIT_LIFTED and note[1] is not None and note[1] > epoch

    def resumed(self, plan: dict, guard: Any) -> bool:
        """Was the turn ending now started by the first message a person typed after the limit last stopped this run?

        The limit's moment (a ``wrap_up`` note, or a refused turn) must fall after the plan began, and the thread must be
        read all the way back to it (``transcript.prompts_until``): the turn's own prompt is typed (``guard.human_typed``),
        and no other typed message or Esc lies between. Anything unread or unplaceable is False — the user keeps the
        floor, as before. Read once."""
        if self._resumed is None:
            try:
                self._resumed = self._resumed_now(plan, guard)
            except Exception:  # noqa: BLE001 - no evidence: the user keeps the floor
                self._resumed = False
        return self._resumed

    def _resumed_now(self, plan: dict, guard: Any) -> bool:
        self._state()
        typed = getattr(guard, 'human_typed', None)
        reached = self._reached
        if typed is None or reached is None or reached <= (_epoch(plan.get('started_at')) or 0.0):
            return False
        prompts, whole = transcript.prompts_until(self._harness(), self._path(), reached,
                                                  str(getattr(self._stop, 'session_id', '') or ''))
        if not whole or not prompts:
            return False
        own, earlier = prompts[0], prompts[1:]
        if own.injected or own.interrupted or not typed(own.text):
            return False
        return not any(p.interrupted or (not p.injected and typed(p.text)) for p in earlier)


def _decide(stop: Any, bases: list[Path], event: str, *, command: str | None, failure: bool | str | None, guard) -> str | None:
    path = _run_driver.find_plan(bases)
    plan = _run_driver.read_plan(path)
    if path is None or plan is None or plan.get('finished') is True:
        return None
    if guard is None:
        import report_guard as guard  # noqa: PLC0415 - the turn-end predicates' one home
    # Read BEFORE any write below: "since the plan's last write" is about the plan as it stood.
    spoke = guard.user_spoke_since(stop, path)
    limit = _UsageLimit(stop)
    if spoke and limit.resumed(plan, guard):
        # The first message a person typed after the account's usage limit stopped the run is the run going on: its
        # stage's hand-off continues into the next stage. The next message they type gets the floor as always.
        spoke = False
    lifted_by = _lifted_by(plan) if spoke else None
    if lifted_by is not None and _own_prompt_lifted(stop, guard, lifted_by):
        # The typed message that lifted a capacity or credit halt (`record_user_turn`) is the run going on, exactly as
        # after the usage limit: this turn is the run's. Its digest is spent in a plan write now, so the plan's last
        # write is newer than that message and the next message a person types gets the floor.
        spoke = False
        plan['last_halt'].pop('by', None)
        write_plan(path, plan)
    try:
        written = path.stat().st_mtime
    except OSError:
        return None
    # A limit can outlast the TTL (a weekly or monthly one): its lift, or the message that resumed the run, means the
    # plan was paused, not abandoned.
    live = (time.time() - written < _run_driver.PLAN_TTL_SECONDS or limit.lifted_after(written)
            or limit.resumed(plan, guard))
    workdir = plan_workdir(plan)
    changed = False

    if event == 'idle' and failure and not spoke:
        # The failure hand-off of the stage in progress, rendered after that stage closed its own
        # window (an error-posture exit): the run stops here exactly as an anchored failure does.
        # Not on a turn answering the user — that turn is theirs, and the plan is left as it stands.
        if not plan.get('halted'):
            remaining = _run_driver.remaining_stages(plan)
            _set_halt(plan, remaining[0] if remaining else 'unknown', _halt_kind(failure))
            write_plan(path, plan)
        return None

    if event == 'handoff':
        stage = STAGE_OF_COMMAND.get(command or '')
        if failure:
            if not plan.get('halted'):
                _set_halt(plan, stage or command or 'unknown', _halt_kind(failure))
                changed = True
            if changed:
                write_plan(path, plan)
            return None
        if stage in plan['stages'] and stage not in plan['done']:
            if not proof_holds(stage, plan, workdir):
                if limit.holds(plan):
                    # The account's usage limit told the model to stop and say what is done: that wrap-up is not the
                    # stage's hand-off. Nothing halts; once the limit lifts, the run goes on with this stage.
                    return None
                already = bool(plan.get('halted'))
                if not already:
                    # A landing nothing on disk proves: stop, never advance or re-run. A `proof` halt is lifted
                    # by `simulate done <stage>` once the proof is written.
                    _set_halt(plan, str(stage), 'proof')
                    write_plan(path, plan)
                # No block over a turn that answers the user, or over a paused run: an Esc is the user
                # directing the run, and the block's instruction ('record it and carry on') would override it.
                return None if already or spoke or plan.get('paused') else missing_proof_reason(str(stage), workdir)
            plan['done'].append(stage)
            plan['stalls'] = 0
            if plan.get('halted') == stage:
                # Fresh proof of the stage is the strongest evidence that its stop is over, whatever its kind.
                _clear_halt(plan)
            if not _existing_dir(plan.get('workdir')):
                # `begin` ran before the agent's workdir existed (its scan makes it): the stage's fresh proof was just
                # read there, so it is this run's workdir — recorded for every later reader of the plan.
                plan['workdir'] = str(workdir)
            changed = True

    def _allow() -> None:
        if changed:
            write_plan(path, plan)
        return None

    # Under the account's usage limit the run pauses: a landing is still recorded above, but nothing is pushed, no
    # stall is counted and the plan never stands down — the model was told by the harness to stop.
    if plan.get('halted') or plan.get('paused') or spoke or limit.holds(plan):
        return _allow()
    if event == 'idle' and (_ask_outstanding(guard, bases) or guard.pending_backed(bases)):
        return _allow()
    remaining = _run_driver.remaining_stages(plan)
    if not remaining:
        if plan.get('closing_sent'):
            return _allow()
        plan['closing_sent'] = True
        write_plan(path, plan)
        return closing_reason(plan)
    if not live:
        return _allow()
    nxt = remaining[0]
    if event == 'idle':
        if nxt != 'run' and proof_written(nxt, plan, workdir):
            # The stage produced its artifact but no validated hand-off recorded it (its window
            # stood down, or it ran past its hand-off): halt rather than advance or re-run it. A `proof`
            # halt is lifted by `simulate done <stage>` once the proof holds.
            _set_halt(plan, nxt, 'proof')
            write_plan(path, plan)
            return unrecorded_reason(nxt)
        if int(plan.get('stalls') or 0) >= STALL_LIMIT:
            plan['finished'] = True
            plan['stalled_at'] = now_iso()
            write_plan(path, plan)
            return None
        plan['stalls'] = int(plan.get('stalls') or 0) + 1
        changed = True
    if changed:
        write_plan(path, plan)
    return continue_reason(nxt, plan, workdir, path.parent)


def next_reason(plan: dict, path: Path, workdir: Path | None) -> str:
    """How the run goes on from ``plan`` as it stands: what :func:`decide` would block with at a hand-off, with none of
    its writes. A halt says what lifts it, a run with every stage done gets the closing, a plan nobody wrote for a day
    and a finished one say that nothing continues."""
    halted = plan.get('halted')
    if plan.get('finished') is True:
        return 'flow-window: simulate — the run has ended; nothing continues by itself.'
    if halted:
        stage = str(halted)
        if plan.get('proof_halt') == stage and stage in STAGES:
            return unrecorded_reason(stage) if proof_written(stage, plan, workdir) else missing_proof_reason(stage, workdir)
        return (f'The /simulate run stopped at stage {stage}: nothing continues by itself. Say in ONE line where it stopped; '
                'the user\'s next message decides what runs. ' + end_page_steps())
    remaining = _run_driver.remaining_stages(plan)
    if not remaining:
        return closing_reason(plan)
    try:
        live = time.time() - path.stat().st_mtime < _run_driver.PLAN_TTL_SECONDS
    except OSError:
        live = False
    if not live:
        return 'flow-window: simulate — the run was last directed more than a day ago; nothing continues by itself.'
    nxt = remaining[0]
    if nxt != 'run' and proof_written(nxt, plan, workdir):
        # As decide's idle branch: the stage wrote its file but no hand-off recorded it, so invoking it again would redo
        # a finished stage. Only the words: the halt decide writes is left to `simulate done` / `rewind`.
        return unrecorded_reason(nxt)
    return continue_reason(nxt, plan, workdir, path.parent, limited=True)


def _spelled(text: str) -> str:
    """``text`` with the commands spelled the way this shell's harness types them (``session_glossary.respell_commands``)."""
    try:
        import _workdir  # noqa: PLC0415 - hooks/ is on sys.path above
        import session_glossary  # noqa: PLC0415 - the command spelling's one home

        return session_glossary.respell_commands(text, _workdir.harness())
    except Exception:  # noqa: BLE001 - only the spelling is lost
        return text


# ── the CLI ───────────────────────────────────────────────────────────────────────────────────


def _usage() -> int:
    print('usage: simulate_continue.py begin <stage>[,<stage>...] | done|skip|rewind <stage> | resume | end | next | handoff <command>'
          '  --flowdir <dir> [--workdir <dir>]', file=sys.stderr)
    return 64


#: The flow-window commands a hand-off can name: the stages' and the ones that are no stage of a run.
HANDOFF_COMMANDS = (*STAGE_OF_COMMAND, 'simulate', 'analyze')


def _parse(argv: list[str]) -> tuple[list[str], dict[str, str]] | None:
    positional: list[str] = []
    options: dict[str, str] = {}
    it = iter(argv)
    for arg in it:
        if arg in ('--flowdir', '--workdir'):
            value = next(it, None)
            if value is None or arg[2:] in options:
                return None
            options[arg[2:]] = value
        elif arg.startswith('--'):
            return None
        else:
            positional.append(arg)
    return positional, options


def _stages(spec: str) -> list[str] | None:
    names = [s.strip() for s in spec.split(',')]
    if not names or any(n not in STAGES for n in names) or len(set(names)) != len(names):
        return None
    return sorted(names, key=STAGES.index)


def main(argv: list[str]) -> int:
    parsed = _parse(argv)
    if parsed is None or not parsed[0]:
        return _usage()
    (action, *args), options = parsed
    flowdir = options.get('flowdir')
    workdir = options.get('workdir') or None
    if not flowdir:
        return _usage()
    arity = {'begin': 1, 'done': 1, 'skip': 1, 'rewind': 1, 'resume': 0, 'end': 0, 'next': 0, 'handoff': 1}
    if action not in arity or len(args) != arity[action]:
        return _usage()
    if action in ('next', 'handoff'):
        return _next_or_handoff(action, args, Path(flowdir), workdir)
    if action == 'begin':
        stages = _stages(args[0])
        if stages is None:
            print(f"flow-window: stages must be a comma list of {','.join(STAGES)} (each once).", file=sys.stderr)
            return 64
        path = Path(flowdir) / PLAN_MARKER
        _consume_user_turn([Path(flowdir)] + ([Path(workdir)] if workdir else []))
        try:
            write_plan(path, new_plan(stages, workdir=workdir))
        except OSError as exc:
            print(f'flow-window: could not write {path} ({exc}).', file=sys.stderr)
            return 1
        print(f"flow-window: simulate run planned — {','.join(stages)}.")
        return 0
    if action in ('done', 'skip', 'rewind') and args[0] not in STAGES:
        print(f"flow-window: '{args[0]}' is not a stage ({','.join(STAGES)}).", file=sys.stderr)
        return 64
    if action == 'skip' and args[0] not in OPTIONAL_STAGES:
        print(f"flow-window: stage '{args[0]}' cannot be skipped; only {','.join(OPTIONAL_STAGES)} can.", file=sys.stderr)
        return 3
    bases = [Path(flowdir)] + ([Path(workdir)] if workdir else [])
    path = _run_driver.find_plan(bases)
    plan = _run_driver.read_plan(path)
    began = reopened = stopped_early = False
    if path is None or plan is None:
        if action == 'end':
            print('flow-window: no simulate run to end.')
            return 0
        if action != 'rewind':
            print('flow-window: no /simulate run in this session — nothing to change.', file=sys.stderr)
            return 3
        # Feedback in a session that holds no plan (a new session): the run begins at the stage the
        # feedback routes to, with every later stage a run takes. Traces only when that IS the stage.
        path = Path(flowdir) / PLAN_MARKER
        plan = new_plan([s for s in STAGES[STAGES.index(args[0]):] if s != 'traces' or s == args[0]], workdir=workdir)
        began = True
    if action == 'resume' and (closed := resume_closed(plan)) is not None:
        print(f'flow-window: simulate — {closed}.')
        return 0
    if action == 'end':
        refusal = end_refusal(plan, path, bases)
        if refusal:
            print(refusal, file=sys.stderr)
            return 3
    if action == 'resume' and halt_kind(plan) in LIFTABLE:
        # A capacity or credit halt is lifted by a message a person typed after it (`record_user_turn` lifts it at the
        # prompt already; this is that lift when the prompt hook did not run). A `resume` the model runs on its own, in
        # a background completion's turn, lifts nothing: read BEFORE the record is spent below.
        typed, halted_at = _typed_at(bases), _epoch((plan.get('halt') or {}).get('at'))
        if typed is not None and halted_at is not None and typed > halted_at:
            _clear_halt(plan)
    if action in ('rewind', 'resume', 'end'):
        _consume_user_turn(bases)
    if _existing_dir(workdir):
        plan['workdir'] = workdir  # a path that is not there never replaces one that is
    if action in ('rewind', 'resume', 'end'):
        # The user's message is being acted on: an Esc pause ends here, and an Esc before now is answered.
        plan.pop('paused', None)
        plan['acted_at'] = datetime.now(timezone.utc).isoformat()
    if action == 'done':
        stage = args[0]
        if stage not in plan['stages']:
            print(f"flow-window: '{stage}' is not a stage of this run ({','.join(plan['stages'])}).", file=sys.stderr)
            return 3
        landed = stage not in plan['done']
        if landed:
            where = plan_workdir(plan, workdir)
            if not proof_holds(stage, plan, where):
                gaps = stage_gaps(stage, where, _since(stage, plan)) if where is not None else []
                named = ', '.join(f'{rel} ({writer})' for rel, writer in gaps) or 'its files'
                print(f"flow-window: refusing to record '{stage}' as done — not written since it began: {named}.", file=sys.stderr)
                return 3
            plan['done'].append(stage)
            plan['stalls'] = 0
        if plan.get('halted') == stage and (landed or halt_kind(plan) == 'proof'):
            _clear_halt(plan)  # the stage's fresh proof is on disk now: its stop is over, whatever its kind
    elif action == 'skip':
        stage = args[0]
        if stage not in plan['stages']:
            print(f"flow-window: '{stage}' is not a stage of this run ({','.join(plan['stages'])}).", file=sys.stderr)
            return 3
        # The record is the skip itself: the plan has this one writer, and the stage's artifact is never
        # stood in for, because later stages read it as the output of the stage that was not run.
        if stage not in plan['done']:
            plan['done'].append(stage)
        plan['skipped'] = sorted({*(plan.get('skipped') or []), stage}, key=STAGES.index)
        plan['stalls'] = 0
        if plan.get('halted') == stage:
            _clear_halt(plan)
    elif action == 'rewind':
        stage = args[0]
        if stage not in plan['stages']:
            plan['stages'] = sorted([*plan['stages'], stage], key=STAGES.index)
        later = [s for s in plan['stages'] if STAGES.index(s) >= STAGES.index(stage)]
        # A stage the user skipped stays skipped through a rewind to an earlier stage; only a rewind
        # that names it asks for it again.
        rerun = [s for s in later if s == stage or s not in (plan.get('skipped') or [])]
        plan['done'] = [s for s in plan['done'] if s not in rerun]
        plan['skipped'] = [s for s in plan.get('skipped') or [] if s not in rerun]
        since = plan['since'] if isinstance(plan.get('since'), dict) else {}
        stamp = now_iso()
        since.update({s: stamp for s in rerun})
        plan['since'] = since
        _clear_halt(plan)
        plan.update({'finished': False, 'closing_sent': False, 'stalls': 0})
        plan.pop('stalled_at', None)
        plan.pop('ended_at', None)
    elif action == 'end':
        # A stop while a stage is left: no hand-off of the run's end will publish its page, so this one does. A run whose
        # every stage is done already published from its last hand-off (the closing runs `end` too), and a halted run from
        # the hand-off (or the stop) that halted it: publishing again would be the run's second publish.
        stopped_early = bool(_run_driver.remaining_stages(plan)) and not plan.get('halted')
        plan['finished'] = True
        plan['ended_at'] = now_iso()
    elif action == 'resume' and stood_down(plan):
        # Turns that did not move the run on stood it down; the user's message to go on reopens it, so the next
        # turn end carries it into its remaining stages again.
        plan.update({'finished': False, 'stalls': 0, 'closing_sent': False})
        plan.pop('stalled_at', None)
        reopened = True
    try:
        write_plan(path, plan)
    except OSError as exc:
        print(f'flow-window: could not write {path} ({exc}).', file=sys.stderr)
        return 1
    said = {
        'done': f"recorded '{args[0] if args else ''}' as done",
        'skip': f"recorded '{args[0] if args else ''}' as skipped at the user's request — the run goes on without it",
        'rewind': f"rewound to '{args[0] if args else ''}' — it and every later stage the user did not skip run again",
        'resume': 'the run continues',
        'end': 'the run has ended; nothing continues by itself',
    }[action]
    if began:
        said = f"no run in this session, so one begins at '{args[0]}' — {','.join(plan['stages'])}"
    if action == 'resume':
        said = resume_line(plan, reopened=reopened)
    print(f'flow-window: simulate — {said}.')
    if action == 'end' and stopped_early:
        print(_spelled(end_page_steps()))
    if action == 'rewind' and STAGES.index(args[0]) <= STAGES.index('suite'):
        reset = reset_suite_build(plan_workdir(plan, workdir), f'/simulate feedback redoes the {args[0]}')
        if reset:
            print(f'flow-window: {reset}')
    return 0


def _next_or_handoff(action: str, args: list[str], flowdir: Path, workdir: str | None) -> int:
    """``next`` (read-only) and ``handoff <command>`` (``done`` for the command's stage, then ``next``)."""
    if action == 'handoff' and args[0] not in HANDOFF_COMMANDS:
        print(f"flow-window: '{args[0]}' is not a flow-window command name ({'|'.join(HANDOFF_COMMANDS)}).", file=sys.stderr)
        return 64
    bases = [flowdir] + ([Path(workdir)] if workdir else [])
    path = _run_driver.find_plan(bases)
    plan = _run_driver.read_plan(path)
    if path is None or plan is None:
        if action == 'handoff':
            return 0  # a command run on its own: nothing continues after its hand-off
        print('flow-window: no /simulate run in this session — nothing to continue.', file=sys.stderr)
        return 3
    if action == 'handoff' and plan.get('finished') is True:
        return 0  # a run that ended earlier in the session: this command ran on its own
    stage = STAGE_OF_COMMAND.get(args[0]) if action == 'handoff' else None
    if action == 'handoff' and args[0] != 'simulate':
        remaining = _run_driver.remaining_stages(plan)
        if not remaining or stage != remaining[0]:
            return 0  # not the stage the run is at: a command run on its own resumes no plan it finds
    where = plan_workdir(plan, workdir)
    if stage in plan['stages'] and stage not in plan['done']:
        if not proof_holds(stage, plan, where):
            print(_spelled(missing_proof_reason(stage, where)))
            return 3
        plan['done'].append(stage)
        plan['stalls'] = 0
        if plan.get('halted') == stage:
            _clear_halt(plan)  # as `done`: the stage's fresh proof ends its stop, whatever its kind
        if workdir:
            plan['workdir'] = workdir
        try:
            write_plan(path, plan)
        except OSError as exc:
            print(f'flow-window: could not write {path} ({exc}).', file=sys.stderr)
            return 1
        print(f"flow-window: simulate — recorded '{stage}' as done.")
    print(_spelled(next_reason(plan, path, where)))
    return 0


if __name__ == '__main__':
    from _utf8_stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
