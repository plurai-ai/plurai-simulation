#!/usr/bin/env python3
"""The CI verdict of a local run: the block `plurai ci run` would report for this `run.json`, appended to `analytics.md`.

    ci_verdict.py <run.json> [--into <analytics.md>]

The outcome of every scenario × policy and the heading that counts them come from the SDK's `plurai.report`, the module
`plurai ci run` renders through, so a local run and a CI run read one `run.json` to one verdict. This script holds no outcome
rule: it finds an interpreter that imports the SDK, asks it to read the file, and places the answer. What `ci.yaml`
adds in CI (overrides, the infra retry, the quarantine cap) is not applied here; the block says so.

The SDK lives in the project's agent environment, not in the interpreter that runs the plugin's scripts. When this
interpreter cannot import `plurai.report`, the script runs itself (`--render`) under the interpreter of the `plurai` runner
that `resolve_plurai.py` vouches for. When no interpreter can, or the SDK refuses the file (an SDK without `plurai.report`, a
single-sample run with no `trials` block, an unknown schema), the block is one line that says why. The rest of
`analytics.md` is never touched, and the exit code is 0 whatever the verdict: the verdict is text for the report, not a gate.

Placing the block: it sits between two marker comments at the end of `analytics.md`, and a second call replaces the first.
`--into` names the file; without it the block is printed and nothing is written.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BEGIN = '<!-- plurai-ci-verdict:begin -->'
END = '<!-- plurai-ci-verdict:end -->'

#: How long the SDK interpreter has to read a run.json and render it.
RENDER_TIMEOUT_SECONDS = 120


def render(run_json: dict[str, Any]) -> dict[str, Any]:
    """`{'ok': True, 'exit_code', 'heading', 'report'}` for `run_json`, or `{'ok': False, 'why'}`. Needs `plurai` in this interpreter.

    The verdict is `plurai.ci.verdict` and the report `plurai.report.render_report`, over one reading of the file.
    """
    try:
        from plurai.ci import verdict  # noqa: PLC0415 - the SDK is absent from the interpreter that runs the plugin's scripts
        from plurai.report import ReportOptions, RunJsonError, read_run, render_report  # noqa: PLC0415
    except ImportError as exc:
        return {'ok': False, 'why': f'the installed SDK has no plurai.report ({exc}); install the SDK build that ships it'}
    try:
        run = read_run(run_json)
    except RunJsonError as exc:
        return {'ok': False, 'why': f'the SDK cannot read this run.json: {exc}'}
    # A local report is the user's own file, so the judges' words are shown.
    options = ReportOptions(include_content=True)
    try:
        answer = verdict(run, options)
        report = render_report(run, options)
    except Exception as exc:  # noqa: BLE001 - the verdict is an addition to the report; whatever the SDK raises becomes the one line
        return {'ok': False, 'why': f'the SDK failed on this run.json ({type(exc).__name__})'}
    return {'ok': True, 'exit_code': answer.exit_code, 'heading': answer.heading, 'report': report}


def _sdk_python() -> tuple[str | None, str]:
    """`(interpreter, '')` of the environment the vouched `plurai` runner is installed in, else `(None, why)`."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import resolve_plurai  # noqa: PLC0415 - sibling script; only the path that needs another interpreter loads it

    diagnostics = io.StringIO()
    with contextlib.redirect_stderr(diagnostics):
        runner, _code = resolve_plurai.resolve()
    if runner is None:
        lines = [line.strip() for line in diagnostics.getvalue().splitlines() if line.strip()]
        return None, lines[-1].removeprefix('resolve_plurai: ') if lines else 'no `plurai` SDK was found'
    python = resolve_plurai.runner_python(runner)
    if not os.access(python, os.X_OK):
        return None, f'the SDK environment of {runner} has no interpreter at {python}'
    return python, ''


def _render_elsewhere(python: str, run_json: Path) -> dict[str, Any]:
    """`render` run by `python`, the interpreter the SDK is installed under; the file is read there, not here."""
    # The child reads one file and needs no credential: it gets the path to find its own tools and nothing else.
    env = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT') if name in os.environ}
    try:
        done = subprocess.run(
            [python, '-I', str(Path(__file__).resolve()), '--render', str(run_json)],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=RENDER_TIMEOUT_SECONDS, env=env, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {'ok': False, 'why': f'the SDK interpreter did not run ({type(exc).__name__})'}
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        tail = done.stderr.strip().splitlines()[-1:] or ['no output']
        return {'ok': False, 'why': f'the SDK interpreter gave no answer ({tail[0][:200]})'}
    return answer if isinstance(answer, dict) and isinstance(answer.get('ok'), bool) else {'ok': False, 'why': 'the SDK interpreter gave an unreadable answer'}


def _load(run_json: Path) -> tuple[dict[str, Any] | None, str]:
    """`(document, '')` of the JSON object in the file, else `(None, why)`."""
    try:
        document = json.loads(run_json.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return None, f'run.json could not be read ({type(exc).__name__})'
    return (document, '') if isinstance(document, dict) else (None, 'run.json is not a JSON object')


def _importable() -> bool:
    try:
        import plurai.report  # noqa: F401, PLC0415 - a probe; the import is the question
    except ImportError:
        return False
    return True


def answer_for(run_json: Path) -> dict[str, Any]:
    """The SDK's answer for the file at `run_json` (see `render`).

    An interpreter that already imports `plurai.report` is the project's agent environment (the plugin's scripts run under it
    when it is the one on the path), so it answers in process. Any other interpreter asks the environment of the runner that
    `resolve_plurai.py` vouches for."""
    if _importable():
        document, why = _load(run_json)
        return render(document) if document is not None else {'ok': False, 'why': why}
    python, why = _sdk_python()
    if python is None:
        return {'ok': False, 'why': why}
    return _render_elsewhere(python, run_json)


def block_for(answer: dict[str, Any]) -> str:
    """The marked block for an answer: the verdict and the report it was drawn from, or the one line that says why there is none."""
    if not answer.get('ok'):
        why = str(answer.get('why') or 'the SDK gave no reason').strip().rstrip('.')
        return f'{BEGIN}\nCI verdict: not available. {why[:1].upper()}{why[1:]}.\n{END}\n'
    return (
        f'{BEGIN}\n'
        f'## CI verdict\n\n'
        f'The rules `plurai ci run` applies to this `run.json`, without the overrides, infra retry and quarantine cap of `ci.yaml`. '
        f'A CI run would exit {answer["exit_code"]}.\n\n'
        f'{str(answer["report"]).rstrip()}\n'
        f'{END}\n'
    )


def place(text: str, block: str) -> str:
    """`text` with `block` at its end, in place of an earlier block when `text` holds one. A block cut off before its end marker is dropped to the end of `text`."""
    start = text.find(BEGIN)
    if start == -1:
        return text.rstrip('\n') + '\n\n' + block
    end = text.find(END, start)
    rest = text[end + len(END):].lstrip('\n') if end != -1 else ''
    return text[:start].rstrip('\n') + '\n\n' + block + (('\n' + rest) if rest else '')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run_json', type=Path)
    parser.add_argument('--into', type=Path, help='the analytics.md the block is placed in')
    parser.add_argument('--render', action='store_true', help='internal: print the SDK answer as JSON (runs under the SDK interpreter)')
    args = parser.parse_args(argv)
    if args.render:
        document, why = _load(args.run_json)
        print(json.dumps(render(document) if document is not None else {'ok': False, 'why': why}, ensure_ascii=True))
        return 0
    block = block_for(answer_for(args.run_json))
    if args.into is not None and args.into.is_file():
        args.into.write_text(place(args.into.read_text(encoding='utf-8'), block), encoding='utf-8')
    print(block, end='')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # `python -I` puts no script directory on the path
    from _stdio import utf8_stdio  # noqa: PLC0415 - sibling script: UTF-8 standard streams on Windows

    utf8_stdio()
    raise SystemExit(main())
