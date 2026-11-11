#!/usr/bin/env python3
"""Drive the product's own order with nobody at the terminal — the checked-in headless driver.

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/headless_driver.py" --agent <path> [--phases scan,suite,run]

Every overnight round so far has been driven by a hand-written loop, re-derived each time. This is
that loop, checked in beside the scripts it composes, so a CI pipeline, a nightly suite or a remote
agent runs the same sequence a human runs: **scan → traces → suite → run → fixes**, each phase one
`claude -p` turn (continued with `--resume`) inside its own flow window.

IT COMPOSES, IT DOES NOT REIMPLEMENT
------------------------------------
* ``scripts/flow-window.sh`` owns arming and closing. Headless arming is the SAME arm — the driver
  runs the script rather than writing markers, because a raw marker write is the shape the
  auto-mode permission classifier denies (see ``hooks/_workdir.py``).
* ``scripts/poll_job.sh`` owns every blocking wait on a backend job. Its header records why: the
  sleep lives INSIDE the script and in the FOREGROUND (the old recipe's background wait overlapped
  its own poll — "~25 polls burning ~75 tool calls"), it prints the SERVER's ``elapsed_seconds``,
  and **its exit code is the result**. So this driver never pipes it, never captures it, and never
  re-rolls a poll of its own; a driver that did would re-acquire all three bugs at once.
* ``hooks/_run_driver.py`` owns the declaration and the typed findings. The driver exports the
  accepted env alias (``PLURAI_SIM_NONINTERACTIVE=1``) so a workspace that has not yet declared
  ``workspace.run_driver: headless`` still runs headless — and an explicit
  ``workspace.run_driver: interactive`` still WINS, which is the precedence the whole feature rests
  on and the reason this is an export and not a flag the driver invents.

WHAT IT WATCHES — AND WHAT IT REFUSES TO READ AS PROGRESS
---------------------------------------------------------
``$ART/loop_log.md`` is **completion-only**: an entry appears when something FINISHES, so silence
means "running", not "stuck", and a driver that treated quiet as progress (or as death) would be
wrong in both directions. This one never reads it as liveness. It watches exactly three things:

1. the **exit code** of each ``poll_job.sh`` wait it makes;
2. the phase's own **completion artifact** appearing on disk (``_PHASES`` below names one per
   phase — the file the next phase would read);
3. its own **wall-clock bound** per phase.

and on (3) it **fails loudly**: the timeout is the only signal that separates "still running" from
"stalled on a question nobody answered", so it exits non-zero, names the phase, the bound and the
artifact that never appeared, and stops the round. A driver that returned quietly on a timeout
would report a stall as a finished run.

It also stops loudly on a typed ``run_driver_input_required`` finding: that is the hook saying a gate
had no declared default and it refused to invent one. Continuing past it would be the round
answering its own question.

And it stops on a typed ``run_driver_version_refused`` finding: the backend check or the resolver refused the
plugin's or the SDK's version and filed the text it printed. The turn that said that text exits 0, so without the
finding the driver relaunches turns until its bound and reports a stall. The text goes to this log verbatim.

And on a typed ``run_driver_halted`` finding: the Stop hook let a stop text through (the contact hand-off, the capacity
or unavailable stop, a credit stop) and filed it. The turn that said it exits 0, so the driver ends the phase with the
text in the log and a non-zero exit, and relaunches nothing: a person reads the text and decides.

WHAT IT WILL START, AND WITH WHAT FLAGS
---------------------------------------
Each turn starts a nested `claude -p` with this process's environment, bearer included. The binary
cannot be named: it is the `claude` on the `PATH` of that environment, which must sit outside the
agent's own tree and answer `--version` as Claude Code, or the phase ends as a launch failure
(exit 4) with the reason on the log. `--claude-arg` takes only the flags in `_CLAUDE_ARG_FLAGS` (model, turn and spend limits,
effort, a permission mode that still asks or denies); anything else is a usage error, so no flag on
this command line can switch the nested session's permission checks off.

EXIT CODES
  0  every requested phase completed
  2  a phase outlived its wall-clock bound (STALLED — nothing completed it)
  3  a phase filed a `run_driver_input_required` finding (the round needs an operator)
  4  a phase's launcher exited non-zero with no completion artifact
  5  a phase filed a `run_driver_halted` finding (a stop text: capacity, credit or contact; the text is in the log)
  7  a phase filed a `run_driver_version_refused` finding (Plurai refused the plugin or the SDK; the text is in the log)
 64  usage error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))

import _client_handshake  # noqa: E402 - scripts/ is this file's own directory, first on sys.path
import _command_run  # noqa: E402
import _run_driver  # noqa: E402
from sdk_exit_codes import OK as JOB_DONE  # noqa: E402 - scripts/ is this file's own directory, first on sys.path
from sdk_exit_codes import STILL_RUNNING  # noqa: E402
from sdk_exit_codes import USAGE as EXIT_USAGE  # noqa: E402
import _platform  # noqa: E402 - hooks/ is on sys.path as of the line above
from flow_arm import COMMANDS  # noqa: E402 - hooks/ is on sys.path as of the line above
from _stdio import utf8_child_text  # noqa: E402 - sibling script: UTF-8 children on Windows

#: Seconds a phase may run before the bound fails it. DEFAULTS, overridable per phase with
#: `--budget <phase>=<seconds>`: the numbers are what each skill's own polling guidance quotes for
#: its heaviest job, plus room for the turns around it. They are a STALL detector, not a schedule
#: — a phase that finishes in a tenth of its bound is the normal case.
_DEFAULT_BUDGETS = {"scan": 2700, "traces": 3600, "suite": 14400, "run": 5400, "fixes": 3600}


class Phase:
    """One command of the product's order: what to invoke, which window it arms, what it leaves.

    ``completion`` is a path RELATIVE to the workdir (``plurai_sim/``) or, when it starts with
    ``@``, to the run's ``$OUT``. It is the file the NEXT phase reads — which is what makes it a
    completion signal rather than a progress guess.

    ``window`` is DERIVED, never passed: ``flow_arm.COMMANDS`` is the one table mapping a command
    to its flow-window marker, and `flow_autoarm`'s docstring states the rule for it — *"One
    command table, imported from `flow_arm` (`COMMANDS`) — a second copy of the six names is
    exactly the drift `FLOW_DRIVERS`' lockstep tests exist to prevent."* A hand copy here would
    survive a rename, and an unarmed phase runs with both guards fail-open, silently. A command
    the table does not know raises at IMPORT, not four hours later at a stalled bound.
    """

    __slots__ = ("name", "command", "window", "completion")

    def __init__(self, name: str, command: str, completion: tuple[str, ...]) -> None:
        self.name = name
        self.command = command
        self.window = COMMANDS[command.lstrip("/")]
        self.completion = completion

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Phase({self.name})"


#: THE ORDER, and nothing else decides it.
_PHASES: tuple[Phase, ...] = (
    Phase("scan", "/simulation-scan-agent", ("plurai.config.yaml", "simulation_prd.md")),
    Phase("traces", "/analyze-traces", (".trace_brief_id",)),
    Phase("suite", "/simulation-generate-suite", (".scenario_set_id", ".eval_set_id")),
    Phase("run", "/simulation-run", ("@run.json",)),
    # `artifacts/fixes.md`, WORKDIR-relative and not `@`: `simulation-fixes/SKILL.md` writes
    # `$ART/fixes.md` and `resolve-workdir.sh` sets `ART="$WORKDIR/artifacts"`, while `$OUT` is
    # `$ART/runs/<run-name>`. Waiting under `$OUT` waited for a file nothing there writes.
    Phase("fixes", "/simulation-fixes", ("artifacts/fixes.md",)),
)
PHASE_NAMES = tuple(p.name for p in _PHASES)
#: The command the nested session runs: looked up on `PATH`, never named by an argument or a variable.
CLAUDE_COMMAND = "claude"
#: What `--phases` means when it is not given: the four every round runs. `traces` is opt-in — it
#: needs a customer's observability credentials, which is a prerequisite, not a decision.
DEFAULT_PHASES = ("scan", "suite", "run", "fixes")

#: The in-flight job handle a phase can leave behind when its turn ends before the backend job
#: does. Waiting on it is what `poll_job.sh` is for; see `_wait_for_job`.
JOB_MARKER = ".scenario_job_id"

EXIT_OK = 0
EXIT_STALLED = 2
EXIT_INPUT_REQUIRED = 3
EXIT_LAUNCH_FAILED = 4
EXIT_HALTED = 5
EXIT_VERSION_REFUSED = _client_handshake.EXIT_VERSION_REFUSED


def phase_by_name(name: str) -> Phase | None:
    return next((p for p in _PHASES if p.name == name), None)


def _usage(message: str) -> SystemExit:
    """A usage error the way the banner documents it: the message on stderr, exit ``64``.

    `SystemExit(<str>)` prints the string and exits **1** — CPython's documented behaviour, and
    the same defect `scripts/dev/vendor_scenario_variables_schema.py` records having removed. Exit 1
    here is indistinguishable from a phase that failed, so a CI wrapper branching on 64 reads a
    malformed `--budget` as a broken round.
    """
    print(f"headless_driver: {message}", file=sys.stderr)
    return SystemExit(EXIT_USAGE)


def parse_budgets(pairs: list[str] | None) -> dict[str, int]:
    """`['run=900']` → `{'run': 900}`, merged over the defaults. Raises SystemExit on nonsense."""
    budgets = dict(_DEFAULT_BUDGETS)
    for pair in pairs or []:
        name, _, raw = pair.partition("=")
        if phase_by_name(name.strip()) is None:
            raise _usage(f"--budget: {name.strip()!r} is not a phase ({', '.join(PHASE_NAMES)})")
        try:
            seconds = int(raw)
        except ValueError:
            raise _usage(f"--budget {name.strip()}: {raw!r} is not a number of seconds") from None
        if seconds <= 0:
            raise _usage(f"--budget {name.strip()}: must be positive, got {seconds}")
        budgets[name.strip()] = seconds
    return budgets


def completion_paths(phase: Phase, workdir: Path, out: Path | None) -> list[Path]:
    """The artifacts whose presence ENDS this phase. A `@`-prefixed entry lives under `$OUT`."""
    paths: list[Path] = []
    for name in phase.completion:
        if name.startswith("@"):
            if out is not None:
                paths.append(out / name[1:])
        else:
            paths.append(workdir / name)
    return paths


def phase_complete(phase: Phase, workdir: Path, out: Path | None) -> bool:
    """Every named artifact exists. ALL of them, not any: a scan that wrote the config and died
    before the PRD left the next phase reading half a bench."""
    paths = completion_paths(phase, workdir, out)
    return bool(paths) and all(p.exists() for p in paths)


#: What ``--claude-arg`` may pass to the nested ``claude -p``, each flag with the pattern its value
#: must match (``None``: a switch with no value). The nested session carries the caller's environment
#: and runs the whole product loop unattended, so a flag that changes what it may do (permissions,
#: tool grants, settings, MCP servers, added directories, a replaced system prompt) is not tunable
#: from a command line; only limits and the choice of model are. ``--permission-mode`` takes the
#: modes that still ask or deny, never the ones that skip the gates.
_MODEL_NAME = r"[A-Za-z0-9][A-Za-z0-9._:\[\]/-]*"
_CLAUDE_ARG_FLAGS: dict[str, str | None] = {
    "--model": _MODEL_NAME,
    "--fallback-model": _MODEL_NAME,
    "--max-turns": r"\d+",
    "--max-budget-usd": r"\d+(?:\.\d+)?",
    "--effort": r"[a-z]+",
    "--permission-mode": r"default|plan|acceptEdits|dontAsk",
    "--verbose": None,
}


def validate_claude_args(tokens: list[str]) -> list[str]:
    """``tokens`` unchanged when every one is an allowed flag or its value, else a usage error.

    A flag and its value may be one token (``--model=opus``) or two (``--model`` then ``opus``);
    both spellings are checked against the same pattern.
    """
    allowed = ", ".join(_CLAUDE_ARG_FLAGS)
    pending: str | None = None
    for token in tokens:
        if pending is not None:
            if not re.fullmatch(_CLAUDE_ARG_FLAGS[pending] or "", token, re.ASCII):
                raise _usage(f"--claude-arg: {token!r} is not a value {pending} accepts here")
            pending = None
            continue
        flag, equals, value = token.partition("=")
        if flag not in _CLAUDE_ARG_FLAGS:
            raise _usage(f"--claude-arg {token!r} is not one of the flags --claude-arg may pass ({allowed})")
        pattern = _CLAUDE_ARG_FLAGS[flag]
        if pattern is None:
            if equals:
                raise _usage(f"--claude-arg {token!r}: {flag} takes no value")
        elif equals:
            if not re.fullmatch(pattern, value, re.ASCII):
                raise _usage(f"--claude-arg {token!r}: {value!r} is not a value {flag} accepts here")
        else:
            pending = flag
    if pending is not None:
        raise _usage(f"--claude-arg {pending} needs a value")
    return tokens


def _within(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` or below it, comparing files by identity.

    A string prefix test misses a volume that ignores letter case (`/Agent/bin` is `/agent/bin`), so
    each ancestor of ``path`` is compared with ``root`` as a file. An unreadable ``root`` counts as
    containing everything: the answer is "do not run it".
    """
    try:
        target = os.stat(root)
    except OSError:
        return True
    current = path
    while True:
        try:
            if os.path.samestat(os.stat(current), target):
                return True
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def resolve_claude(claude_bin: str, agent: Path, env: dict[str, str]) -> tuple[str | None, str | None]:
    """``(path, None)`` of the `claude` to start, or ``(None, why not)``.

    The path is absolute and is the file that was checked, and the run starts that exact string: a
    name resolved here (against this process's working directory) and started there (in the agent's
    directory) could otherwise mean two files. The name is looked up on the ``PATH`` of the
    environment the session starts with.

    It is started with that environment, bearer included, so it has to be what it says it is: an
    executable named ``claude`` that is not part of the agent's own tree (a file the agent's
    repository carries is not the operator's install) and that identifies itself as Claude Code.
    The identification runs without the bearer.
    """
    found = shutil.which(claude_bin, path=env.get("PATH", os.defpath))
    if not found:
        return None, f"{claude_bin!r} is not an executable file or a command on PATH"
    resolved = os.path.abspath(found)
    if os.path.basename(resolved) != "claude":
        return None, f"{resolved} is not named `claude`"
    for candidate in (resolved, os.path.realpath(resolved)):
        if _within(candidate, str(agent)):
            return None, f"{resolved} is inside the agent tree {agent}, so it is not the operator's install"
    probe_env = {name: value for name, value in env.items() if name != "PLURAI_TOKEN"}
    try:
        done = subprocess.run([resolved, "--version"], capture_output=True, text=True, **utf8_child_text(), timeout=30, env=probe_env, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{resolved} --version did not run ({type(exc).__name__})"
    if "Claude Code" not in done.stdout:
        return None, f"{resolved} --version does not identify itself as Claude Code"
    return resolved, None


def launch_argv(phase: Phase, agent: Path, claude_bin: str, extra: list[str], resume: str | None) -> list[str]:
    """The exact command line one turn of a phase runs. Built here so `--dry-run` can print it."""
    argv = [claude_bin, "-p", phase.command, "--output-format", "json"]
    if resume:
        argv += ["--resume", resume]
    return argv + list(extra)


def _session_id(stdout: str) -> str | None:
    """The session to `--resume`, from `--output-format json`. None when it cannot be read — the
    next turn then starts fresh and SAYS so, rather than resuming a session id it invented."""
    try:
        doc = json.loads(stdout)
    except ValueError:
        return None
    value = doc.get("session_id") if isinstance(doc, dict) else None
    return str(value) if isinstance(value, str) and value.strip() else None


def _bash() -> str | None:
    """The ``bash`` to run a plugin script with (``_platform.git_bash``): ``bash`` off Windows, Git Bash on it
    (a bare ``bash`` there can start the WSL launcher), None when Windows has none."""
    found = _platform.git_bash()
    if found is None:
        print(f"headless_driver: {_platform.GIT_BASH_MISSING}", file=sys.stderr)
    return found


def _run_flow_window(action: str, name: str, agent: Path, env: dict[str, str]) -> None:
    """Arm or close through the OWNER of those markers. Best-effort: the hooks arm too, and a
    driver that hard-failed here would stop a round over a window that is already armed.

    A close runs as a phase ends, after the stop line and the plugin's refusal text are in the log. The owner's own lines
    (`resolve-workdir: …`, `flow-window: closed …`) are kept out of it, so the refusal's last line, the update command
    (the contact hand-off when the refusal is not about the version), stays the last line of the stop block."""
    bash = _bash()
    if bash is None:
        return
    quiet = {"capture_output": True} if action == "close" else {}
    subprocess.run(
        [bash, str(REPO / "scripts" / "flow-window.sh"), action, name],
        cwd=str(agent), env=env, check=False, **quiet,
    )


def _wait_for_job(job_id: str, remaining: int, agent: Path, env: dict[str, str]) -> int:
    """Block on a backend job through `poll_job.sh`. Returns ITS exit code, verbatim.

    NEVER piped, NEVER captured: the script's header says why — its exit code is the result, and a
    pipeline reports the last command's, so `poll_job.sh <id> | tail` exits 0 on a job that FAILED.
    stdout and stderr go straight to this driver's own, where the round's log keeps them.
    """
    budget = max(30, min(540, remaining))
    bash = _bash()
    if bash is None:
        return 127
    return subprocess.run(
        [bash, str(REPO / "scripts" / "poll_job.sh"), job_id, str(budget)],
        cwd=str(agent), env=env, check=False,
    ).returncode


def _in_flight_job(workdir: Path) -> str | None:
    try:
        value = (workdir / JOB_MARKER).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return value or None


def _findings(workdir: Path, session_id: str | None, *, since: float | None, record: Path | None = None) -> list[dict]:
    """Every typed `run_driver_input_required` this phase filed, ONE row per gate (:func:`_filed` states the rules)."""
    return _filed(_run_driver.INPUT_REQUIRED, workdir, session_id, since=since, record=record)


def _refusals(workdir: Path, session_id: str | None, *, since: float | None, record: Path | None = None) -> list[dict]:
    """Every typed `run_driver_version_refused` this phase filed, ONE row per refusal text."""
    return _filed(_run_driver.VERSION_REFUSED, workdir, session_id, since=since, record=record)


def _halts(workdir: Path, session_id: str | None, *, since: float | None, record: Path | None = None) -> list[dict]:
    """Every typed `run_driver_halted` this phase filed, ONE row per stop text: a row whose text another row carries whole
    (the script that stopped files the text, the Stop hook files it again with its done lines) is dropped."""
    rows = _filed(_run_driver.HALTED, workdir, session_id, since=since, record=record)
    return [row for row in rows if not any(row is not other and str(row.get("text")) in str(other.get("text")) for other in rows)]


def _filed(kind: str, workdir: Path, session_id: str | None, *, since: float | None, record: Path | None = None) -> list[dict]:
    """Every finding of ``kind`` this phase filed, ONE row per identity.

    ``since`` is the phase's launch on the wall clock the rows are stamped with (``time.time()``);
    a row stamped before it was filed by an EARLIER phase or run. The durable copy in `art` outlives
    its session on purpose, since a later session's run.json must see a stop, but the driver's
    question is narrower: did THIS phase stop? A stale row answering yes ends the phase after the first
    turn of a phase whose operator has since fixed the cause. A finding filed by this phase is stamped
    after the launch, so it still ends the round; a row with no stamp counts (fail closed).
    ``None`` counts every row.

    ``session_id`` is the one the turn reported back: this driver LAUNCHES the session, so its own
    environment carries none, and a run whose window was session-keyed would otherwise file its
    findings into a directory the driver never looks in, reporting "no findings" for a phase that
    stopped on one.

    ``record`` is the phase's own record directory (:data:`_run_driver.DRIVER_RECORD_ENV`), the one base the model
    cannot take away by closing its flow window.

    A stopper files the same finding into the flow dir, `art` and ``record`` (see `_run_driver.record_durably`),
    and `marker_bases` reads them all. Rows are deduplicated on identity minus `at` (each write stamps its
    own `time.time()`, so the two copies never compare equal as whole dicts): one row per finding, not
    one row per base it happens to land in.
    """
    bases = _run_driver.marker_bases(workdir, session_id, record)
    seen: set[tuple[object, ...]] = set()
    out: list[dict] = []
    for finding in _run_driver.read_findings(bases):
        if finding.get("kind") != kind:
            continue
        at = finding.get("at")
        if since is not None and isinstance(at, (int, float)) and at < since:
            continue
        identity = tuple(sorted((k, v) for k, v in finding.items() if k != "at"))
        if identity in seen:
            continue
        seen.add(identity)
        out.append(finding)
    return out


def _stop_with_text(phase: Phase, findings: list[dict], lead: str, no_text: str, code: int) -> int:
    """End ``phase`` on typed ``findings`` that carry the text the customer reads: it goes to the log verbatim. Returns ``code``."""
    _say(f"STOP — {phase.name}: {lead} The text it gave follows.")
    for finding in findings:
        print(finding.get("text") or no_text, flush=True)
    return code


def _say(line: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] headless_driver: {line}", flush=True)


def run_phase(
    phase: Phase,
    *,
    agent: Path,
    workdir: Path,
    out: Path | None,
    budget: int,
    launch,
    env: dict[str, str],
    now=time.time,
) -> int:
    """Drive ONE phase to its completion artifact, or fail loudly at its bound.

    ``launch(resume) -> (returncode, stdout)`` is injected so the bound, the findings stop and the
    resume loop are drivable without a `claude` binary — the plugin's CI has no such environment,
    and a timeout test that needed one would not exist.
    """
    deadline = now() + budget
    # The WALL clock, not ``now``: findings are stamped with ``time.time()`` (see ``_findings``),
    # and ``now`` is the injectable bound clock.
    launched = time.time()
    inherited = env.get(_command_run.ENV_VAR)
    _run_flow_window("arm", phase.window, agent, env)
    # The phase is one command. Its id was minted by the arm into the run's own directory (this process has no
    # session id), and every session the phase starts inherits it through the environment, so the whole phase
    # is one group on the backend whatever session ids the nested turns get.
    run_id = _command_run.read_file(workdir)
    if run_id:
        env[_command_run.ENV_VAR] = run_id
    # The findings this phase's sessions file for the driver to read. Its own directory, not the model's flow window
    # (closed in the turn that files a refusal) or the workdir (absent until the scan maps the project).
    record = Path(tempfile.mkdtemp(prefix="plurai-driver-record-"))
    env[_run_driver.DRIVER_RECORD_ENV] = str(record)
    resume: str | None = None
    turns = 0
    try:
        while True:
            remaining = int(deadline - now())
            if remaining <= 0:
                break
            turns += 1
            _say(f"{phase.name}: turn {turns}{' (resume)' if resume else ''}, {remaining}s left of {budget}s")
            code, stdout = launch(resume)
            resume = _session_id(stdout) or resume

            refused = _refusals(workdir, resume, since=launched, record=record)
            if refused:
                return _stop_with_text(phase, refused, "Plurai refused the version of the plugin or the SDK this round runs.",
                                       "(the refusal filed no text)", EXIT_VERSION_REFUSED)

            halted = _halts(workdir, resume, since=launched, record=record)
            if halted:
                return _stop_with_text(phase, halted, "Plurai stopped this round.", "(the halt filed no text)", EXIT_HALTED)

            pending = _findings(workdir, resume, since=launched, record=record)
            if pending:
                for finding in pending:
                    _say(f"STOP — {phase.name} filed run_driver_input_required at gate "
                         f"'{finding.get('gate')}': {finding.get('needs')}")
                _say("This round needs a run driver. Declare a default for that gate, supply the "
                     "prerequisite, or run this phase interactively. NOTHING was guessed.")
                return EXIT_INPUT_REQUIRED

            if phase_complete(phase, workdir, out):
                _say(f"{phase.name}: complete after {turns} turn(s).")
                return EXIT_OK

            job = _in_flight_job(workdir)
            if job is not None:
                _say(f"{phase.name}: job {job} still in flight — blocking on poll_job.sh")
                # The exit code IS the result (STILL_RUNNING = budget spent, job healthy → keep waiting).
                job_code = _wait_for_job(job, int(deadline - now()), agent, env)
                _say(f"{phase.name}: poll_job.sh exited {job_code}")
                # `poll_job.sh` printed the plugin's text before it exited with the refusal code; the row it filed says the same.
                if job_code == EXIT_VERSION_REFUSED or _refusals(workdir, resume, since=launched, record=record):
                    _say(f"STOP — {phase.name}: Plurai refused the version of the plugin or the SDK this round runs, "
                         "while the job was awaited.")
                    return EXIT_VERSION_REFUSED
                if job_code not in (JOB_DONE, STILL_RUNNING):
                    _say(f"{phase.name}: that job is not going to finish — stopping the phase.")
                    return EXIT_LAUNCH_FAILED
                continue

            if code != 0:
                _say(f"{phase.name}: the turn exited {code} with no completion artifact and no job in "
                     "flight — nothing left to wait for.")
                return EXIT_LAUNCH_FAILED
        # THE BOUND. Loud, and specific about what never appeared: a quiet return here would
        # report a stall as a finished phase, which is the failure this driver exists to remove.
        missing = [str(p) for p in completion_paths(phase, workdir, out) if not p.exists()]
        _say(f"STALLED — {phase.name} outlived its {budget}s bound after {turns} turn(s). "
             f"Never appeared: {', '.join(missing) or '(no completion artifact declared)'}. "
             "loop_log silence is NOT progress, so this bound is the only thing that could tell "
             "a slow phase from one waiting on a question nobody answered.")
        return EXIT_STALLED
    finally:
        env.pop(_run_driver.DRIVER_RECORD_ENV, None)
        shutil.rmtree(record, ignore_errors=True)
        env.pop(_command_run.ENV_VAR, None)
        if inherited:
            env[_command_run.ENV_VAR] = inherited
        _run_flow_window("close", phase.window, agent, env)


def _launcher(phase: Phase, agent: Path, claude_bin: str, extra: list[str], env: dict[str, str], deadline: float):
    """A `launch(resume)` bound to one phase — the real one, spawning `claude -p`."""

    verdict: list[tuple[str | None, str | None]] = []  # resolved once per phase, not once per turn

    def launch(resume: str | None) -> tuple[int, str]:
        if not verdict:
            verdict.append(resolve_claude(claude_bin, agent, env))
        resolved, refusal = verdict[0]
        if resolved is None:
            _say(f"{phase.name}: REFUSED to start the nested session — {refusal}")
            return (126, "")
        argv = launch_argv(phase, agent, resolved, extra, resume)
        _say(f"$ {' '.join(argv)}")
        timeout = max(1, int(deadline - time.time()))
        try:
            proc = subprocess.run(
                argv, cwd=str(agent), env=env, check=False, timeout=timeout,
                stdout=subprocess.PIPE, stderr=None,
            )
        except subprocess.TimeoutExpired:
            # Not an error yet — the phase's own bound above is what decides. Say it happened.
            _say(f"{phase.name}: the turn hit the phase's remaining wall clock ({timeout}s).")
            return (124, "")
        text = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        if text:
            print(text, flush=True)
        return (proc.returncode, text)

    return launch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", required=True, help="the agent repo to drive (its cwd for every phase)")
    ap.add_argument("--workdir", help="the plurai_sim dir; defaults to <agent>/plurai_sim")
    ap.add_argument("--out", help="the run dir the run phase writes into (its completion artifact)")
    ap.add_argument("--phases", default=",".join(DEFAULT_PHASES),
                    help=f"comma-separated, in order. Known: {', '.join(PHASE_NAMES)}")
    ap.add_argument("--budget", action="append", metavar="PHASE=SECONDS",
                    help="override a phase's wall-clock bound (the STALL detector, not a schedule)")
    ap.add_argument("--claude-arg", action="append", default=[],
                    help=f"extra flag for every `claude -p` turn (repeatable); only {', '.join(_CLAUDE_ARG_FLAGS)} "
                         "are accepted, a flag and its value as one argument (--model=opus) or two")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan — phases, bounds, command lines — and change nothing")
    args = ap.parse_args(argv)
    args.claude_arg = validate_claude_args(args.claude_arg)

    agent = Path(args.agent).expanduser().resolve()
    if not agent.is_dir():
        print(f"headless_driver: --agent {agent} is not a directory", file=sys.stderr)
        return EXIT_USAGE
    workdir = Path(args.workdir).expanduser().resolve() if args.workdir else agent / "plurai_sim"
    out = Path(args.out).expanduser().resolve() if args.out else None
    budgets = parse_budgets(args.budget)

    names = [n.strip() for n in args.phases.split(",") if n.strip()]
    unknown = [n for n in names if phase_by_name(n) is None]
    if unknown:
        print(f"headless_driver: unknown phase(s) {', '.join(unknown)} — known: {', '.join(PHASE_NAMES)}",
              file=sys.stderr)
        return EXIT_USAGE
    phases = [phase_by_name(n) for n in names]

    env = dict(os.environ)
    # The ACCEPTED ALIAS, not a second switch: `hooks/_run_driver.py` reads it as a declaration
    # of `headless`, and an explicit `workspace.run_driver: interactive` still overrides it — so a
    # workspace that deliberately wants a human is not un-interactived by this driver.
    env["PLURAI_SIM_NONINTERACTIVE"] = "1"
    env.setdefault("PLURAI_AGENT_PATH", str(agent))

    if args.dry_run:
        for phase in phases:
            assert phase is not None
            print(f"{phase.name}: bound {budgets[phase.name]}s · window {phase.window} · "
                  f"completes on {', '.join(str(p) for p in completion_paths(phase, workdir, out)) or '(none)'}")
            print(f"  $ {' '.join(launch_argv(phase, agent, CLAUDE_COMMAND, args.claude_arg, None))}")
        return EXIT_OK

    for phase in phases:
        assert phase is not None
        budget = budgets[phase.name]
        deadline = time.time() + budget
        code = run_phase(
            phase, agent=agent, workdir=workdir, out=out, budget=budget,
            launch=_launcher(phase, agent, CLAUDE_COMMAND, args.claude_arg, env, deadline), env=env,
        )
        if code != EXIT_OK:
            _say(f"round STOPPED at phase '{phase.name}' (exit {code}).")
            return code
    _say(f"round complete: {', '.join(names)}.")
    return EXIT_OK


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
