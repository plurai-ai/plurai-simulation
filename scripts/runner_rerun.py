#!/usr/bin/env python3
"""Print the command that re-runs a recorded run into another directory, or refuse when the recording is stale.

    runner_rerun.py --workdir "$WORKDIR" --run <run-name> --out "$ART/runs/<run-name>-verify" [--only <scenario-id> ...]

`run_stage.sh` records the runner command of every run in `<workdir>/artifacts/runs/.<run-name>.runner_command`, quoted
the way `shlex` reads it and led by `PLURAI_OBSERVABILITY=<value>` when the run had one. This prints that command with
`--out` replaced, behind the run's launcher (`run_start.py --`, which starts it and returns at once; the run is then
waited on with `run_wait.sh`), ready to run, and above it the `export` line for the observability value.

The recording names the scenario set, the eval set and the mock service the run used. `/edit-scenarios`,
`/edit-evals`, a regeneration and a redeploy change those in the workdir, and a re-run of the old command would then
score against sets, or talk to a mock, that the workdir no longer holds, so a recorded value that differs from the
workdir's current one is a refusal.

`--only` keeps the named scenarios and drops the other `--scenario-id` flags: a subset re-run, which proves only
that those scenarios now pass.

Exit codes: 0 the command · 1 no usable recording for this run · 2 stale ids or mock URL, or an `--only` id the run
did not have.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

#: Each flag the recording carries as a value, the workdir file that holds the value now, and the refusal's label.
CHECKED_FLAGS = (
    ("--scenario-set-id", ".scenario_set_id", "stale-ids"),
    ("--eval-set-id", ".eval_set_id", "stale-ids"),
    ("--mock-service-url", ".mock_service_url", "stale-mock"),
)
OBSERVABILITY = "PLURAI_OBSERVABILITY="


def recording_path(workdir: Path, run: str) -> Path:
    return workdir / "artifacts" / "runs" / f".{run}.runner_command"


def _value(args: list[str], flag: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(flag + "="):
            return arg[len(flag) + 1 :]
    return None


def _without(args: list[str], flag: str) -> list[str]:
    """``args`` with ``flag`` and its value removed, in both spellings."""
    kept: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == flag:
            skip = True
        elif not arg.startswith(flag + "="):
            kept.append(arg)
    return kept


def _scenario_ids(args: list[str]) -> list[str]:
    ids: list[str] = []
    for index, arg in enumerate(args):
        if arg == "--scenario-id" and index + 1 < len(args):
            ids.append(args[index + 1])
        elif arg.startswith("--scenario-id="):
            ids.append(arg[len("--scenario-id=") :])
    return ids


def _only(args: list[str], keep: list[str]) -> list[str]:
    """``args`` with every ``--scenario-id`` that is not in ``keep`` dropped, in both spellings."""
    rebuilt: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--scenario-id" and index + 1 < len(args):
            if args[index + 1] in keep:
                rebuilt += args[index : index + 2]
            index += 2
            continue
        if arg.startswith("--scenario-id="):
            if arg[len("--scenario-id=") :] in keep:
                rebuilt.append(arg)
            index += 1
            continue
        rebuilt.append(arg)
        index += 1
    return rebuilt


def _current(workdir: Path, name: str) -> str:
    path = workdir / name
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def rerun(workdir: Path, run: str, out: str, only: list[str]) -> tuple[int, str]:
    """``(exit code, text)``: the command lines on 0, the reason otherwise."""
    path = recording_path(workdir, run)
    if not path.is_file():
        return 1, f"no recorded runner command for run {run}: ask the user to run /simulation-run instead"
    text = path.read_text(encoding="utf-8")
    if "$'" in text:
        return 1, f"the recording for run {run} uses a quoting nothing reads back: ask the user to run /simulation-run instead"
    try:
        args = shlex.split(text)
    except ValueError:
        args = []
    exports = [f"export {OBSERVABILITY}{shlex.quote(arg[len(OBSERVABILITY) :])}" for arg in args[:1] if arg.startswith(OBSERVABILITY)]
    args = args[len(exports) :]
    if not args:
        return 1, f"the recording for run {run} does not parse: ask the user to run /simulation-run instead"
    refusals: dict[str, list[str]] = {}
    for flag, filename, label in CHECKED_FLAGS:
        recorded, current = _value(args, flag) or "", _current(workdir, filename)
        if recorded != current:
            refusals.setdefault(label, []).append(
                f"{flag}: the run used {recorded or 'none'}, the workdir holds {current or 'none'} now"
            )
    if refusals:
        reason = "; ".join(f"STOP({label}): " + ", ".join(items) for label, items in refusals.items())
        return 2, reason + ": ask the user to run /simulation-run instead"
    if only:
        unknown = [name for name in only if name not in _scenario_ids(args)]
        if unknown:
            return 2, "STOP(unknown-scenario): the run did not have " + ", ".join(unknown)
        args = _only(args, only)
    command = shlex.join(["python3", str(Path(__file__).resolve().parent / "run_start.py"), "--", *_without(args, "--out"), "--out", out])
    return 0, "\n".join([*exports, command])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", default=[])
    options = parser.parse_args(argv)
    code, text = rerun(options.workdir, options.run, options.out, options.only)
    print(text, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main(sys.argv[1:]))
