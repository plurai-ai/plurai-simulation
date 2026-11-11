#!/usr/bin/env python3
"""Witness each mocked tool on the deployed mock: run the bench's own contract tests and record, per tool,
whether a test that called it passed. `bench_manifest.py`'s builtin `mock_contract`.

    mock_contract_check.py <workdir>/test_mock_contract.py --witness-out <path> --tools <name,name,...> --witness-dir <dir> [--deadline-s <s>] [--answers-out <path>] [--backend-dir <dir>]
    mock_contract_check.py preflight <test file> [<test file> ...]

PREFLIGHT, before a generated test file ships: each file compiles and pytest collects it, one session per file, in
this interpreter (run it under `tool_python.py mock_contract`, the gate's own) — no mock is called. Exit 1 names each
file that does not, with the error; 2 pytest is not importable. A file beside a failed witness fetch
(`fetch_env_file.py`'s record) is named as not checked, never as a problem.

The tests run in-process against the URL in `.mock_service_url`, and every `POST .../mock/execute` they send
through `requests` is recorded under the test that sent it, so a verdict is about the tool the call named. Beside
them run the world's WITNESS tests env-gen ships (C4: `test_mock_witness.py`, its helper and `mock_witness.json`,
one directory): a write is visible to the reads that serve it, a seeded row is returned by its reader. Which tools a
witness test is about is read from the manifest's typed entries (`tools`, `write_tool`, `read_tool`), never from its name. Each file runs in its OWN pytest
session: a file that fails to import stops only its own session. Per mocked tool, the witness file holds
`{state, reason, test}` keyed `surfaces.tools[<name>]`, `test` the node id that decided it:

    witnessed         a contract or witness test called it and passed, and every witness test naming it passed;
                      `read_after` names each tool a passing test called after it (tool -> that test's node id)
    not_witnessed     its test file failed to collect (a generated file that does not import tests nothing: charged
                      to every tool the file names — the manifest's entries, a quoted tool name — else to every tool),
                      or a test failed and is charged to it: a failed witness test to every tool its entry lists in
                      `tools` (an entry without `tools`: a read-your-writes test to its write tool, a seeded test to its
                      read tool), any other test to the LAST tool it called — execution stops at the failed assert, so
                      that is the response being checked; the reason carries the failed test's first `E ` line
    not_determinable  no test calls it (`cause: untested` — the one a waiver may lift), every test that did failed on
                      another tool, a witness test naming it did not pass — or the world shipped no witness manifest,
                      or `fetch_env_file.py` could not bring its files (the reason names them), which leaves EVERY tool here: the contract tests alone never witness a tool. `cause:
                      mock_target_unresolved`: a test about it failed because the mock behind `.mock_service_url` did
                      not answer (no response, or the proxy's 401/403/407/410/502/503/504) — the run lost its target,
                      which says nothing about the tool's answer; the reason carries the request and status

Past `--deadline-s` the run stops after the test in flight, skips the files not yet started, and still writes the
verdicts: a tool no test that ran reached reads not_determinable, the reason saying it was not reached within the budget.

`--answers-out` also keeps what the mock answered: per tool, the first answers that differ, each with the test that got
it, as `mock_shape_probe.py` reads them against the agent's own types. An error answer is not kept. The file is removed
when the run starts, and with it the probe's read of it, so an earlier run's answers, or a read of them, never stand in for
this one's.

A `drive: real_reader` entry is no test (its reader is the agent's own code): it never holds a tool here. Its pair is
`real_reader_probe.py`'s, which `bench_manifest.py` merges onto this verdict.

The bearer goes only where `probe_mock.py` sends it: to the bound backend's origin.

`--backend-dir` holds what the backend's own last run of this world left (`fetch_env_file.py`: `contract_report.json`,
`contract_answers.json` and its copy of the contract tests). That run is ADOPTED in place of a re-run when it measured
exactly the deployed mock: the report's `tree_digest` and `source_id` equal the env's `served_tree` (`GET /jobs/<env>`),
it records each test's tool calls, its answers file names the same tree, and its contract tests are this workdir's
byte for byte. The same verdicts are then read off its per-test outcomes (its contract and witness tests only). Any
other case (an older backend, an older report, another tree or build, an edited local file, a fetch that failed)
re-runs the tests here as before, and says why on stderr.

A bench whose setup writes no contract file (a hosted agent) is decided by the witness tests alone: a MISSING contract
file is no collection error, and only a tool no file exercises stays undecided.

Exit codes: 0 verdicts written · 2 no verdict — no mock URL, a `.mock_service_url` naming another env than `.env_id`
(`mock_target_unresolved`), neither a contract file nor the world's witness tests,
pytest or requests is not importable, or no test passed and every file collected (an unreachable mock or a refused
token errors every test).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from fetch_env_file import backend_getter, gap_reason
from mock_shape_probe import RESULT as SHAPE_READ
from probe_mock import _bearer_for
from real_reader_probe import DRIVE as REAL_READER_DRIVE

WITNESSED, NOT_WITNESSED, NOT_DETERMINABLE = 'witnessed', 'not_witnessed', 'not_determinable'
#: The `cause` of a not_determinable tool the gate ran over and no test exercises (`bench_manifest`'s `unwitnessed`).
UNTESTED = 'untested'
#: The world's witness tests and their typed list, as env-gen ships them (the backend's mock-witness module).
WITNESS_TESTS, WITNESS_MANIFEST, WITNESS_SCHEMA = 'test_mock_witness.py', 'mock_witness.json', 'plurai-mock-witness/1'
#: How much of a failed test's first assertion line a verdict reason carries.
EVIDENCE_CHARS = 200
#: The `cause` of a not_determinable tool whose test failed because the deployed mock did not answer: never its answer.
MOCK_TARGET_UNRESOLVED = 'mock_target_unresolved'
#: Why no tool is witnessed when the world's witness tests are absent; a failed fetch of them names itself instead.
NO_WITNESS = 'the world shipped no mock witness tests, and the contract tests alone witness no tool'
#: Statuses the bearer proxy in front of a deployed mock answers in place of the mock: the bearer was refused, the env is
#: unknown or not deployed (410), or its process is unreachable or restarting (502/503/504). A mock's own answer is never one.
UNREACHED_STATUSES = frozenset({401, 403, 407, 410, 502, 503, 504})
#: Why a tool holds no verdict when the run stopped at `--deadline-s` before any test reached it.
OUT_OF_BUDGET = "not reached within the gate's budget"


def witness_manifest(directory: Path | None) -> list[dict[str, Any]] | None:
    """The manifest's `tests` entries, or None when the world shipped no readable manifest of this schema."""
    try:
        data = json.loads((directory / WITNESS_MANIFEST).read_text(encoding='utf-8')) if directory else None
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('schema') != WITNESS_SCHEMA or not isinstance(data.get('tests'), list):
        return None
    return [t for t in data['tests'] if isinstance(t, dict) and t.get('test') and entry_tools(t)]


def entry_tools(entry: dict[str, Any]) -> list[str]:
    """Every tool a manifest test exercises: its `tools` (a sequence, frame or agreement test calls several) and its typed
    write / read tool. An empty name (a frame test's `read_tool: ''`) is none."""
    names = [*(entry.get('tools') if isinstance(entry.get('tools'), list) else []), entry.get('write_tool'), entry.get('read_tool')]
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n))


def charged_tools(entry: dict[str, Any]) -> list[str]:
    """The tools a FAILED manifest test is charged to: every one it exercises when it lists `tools`; an entry without
    `tools`, its write tool (a read-your-writes test), else its read tool (a seeded test)."""
    if isinstance(entry.get('tools'), list) and entry['tools']:
        return entry_tools(entry)
    return [str(entry.get('write_tool') or entry.get('read_tool'))]


#: How many answers per tool `--answers-out` keeps, and the longest it keeps: this is a sample to read types against.
ANSWERS_PER_TOOL = 20
ANSWER_CHARS = 200_000
ANSWERS_SCHEMA = 'plurai-mock-answers/1'


class Recorder:
    """A pytest plugin: the mocked tools each test called, in order; the tests that passed; per failed test, the
    last tool it called before failing (None when it failed before calling any) and its first `E ` line; per test,
    the first request the mock did not answer; per test that raised something other than a failed check on an answer,
    the exception's type; and, per test file (by name), why it failed to collect."""

    def __init__(self) -> None:
        self.nodeid: str | None = None
        self.calls: dict[str, list[str]] = {}
        self.passed: set[str] = set()
        self.failed: dict[str, str | None] = {}
        self.evidence: dict[str, str] = {}
        self.unreached: dict[str, str] = {}
        self.errored: dict[str, str] = {}
        self.uncollected: dict[str, str] = {}
        self.deadline: float | None = None  # time.monotonic() past which no further test starts
        self.cut = False
        self.session: Any = None
        self.answers: dict[str, list[dict[str, Any]]] = {}

    def out_of_time(self) -> bool:
        self.cut = self.cut or (self.deadline is not None and time.monotonic() >= self.deadline)
        return self.cut

    def pytest_sessionstart(self, session: Any) -> None:
        self.session = session

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            lines = [re.sub(r'^E\s+', '', line.strip()) for line in str(report.longrepr).splitlines() if line.strip()]
            self.uncollected.setdefault(Path(report.nodeid.split('::')[0]).name, (lines[-1] if lines else 'collection error')[:300])

    def session_ended(self, path: Path, code: int) -> None:
        """A session that stopped before any of its tests reported (a conftest that does not import, a usage error)
        is that file failing to collect, said with pytest's exit code."""
        ran = any(Path(n.split('::')[0]).name == path.name for n in {*self.passed, *self.failed})
        if code in (2, 3, 4) and not ran:
            self.uncollected.setdefault(path.name, f'pytest exited {code} before running a test')

    def record(self, body: Any) -> None:
        if self.nodeid is not None and isinstance(body, dict):
            self.calls.setdefault(self.nodeid, []).append(str(body.get('tool')))

    def keep_answer(self, body: Any, resp: Any) -> None:
        """The answer to one `/mock/execute`, read as the SDK reads it: an envelope's `content`, else the response without its
        `_plurai_*` keys. Never raises: a test's verdict is not this record's to change."""
        with contextlib.suppress(Exception):
            tool = str(body.get('tool'))
            result = resp.json()
            response = result.get('response', result) if isinstance(result, dict) else result
            if isinstance(response, dict) and response.get('status') == 'error':
                return
            content = response
            if isinstance(response, dict):
                content = response['content'] if 'content' in response else {k: v for k, v in response.items() if not str(k).startswith('_plurai_')}
            kept = self.answers.setdefault(tool, [])
            spelled = json.dumps(content, sort_keys=True, default=str)
            if len(kept) < ANSWERS_PER_TOOL and len(spelled) <= ANSWER_CHARS and all(json.dumps(a['content'], sort_keys=True, default=str) != spelled for a in kept):
                kept.append({'test': self.nodeid, 'content': content})

    def not_answered(self, why: str) -> None:
        if self.nodeid is not None:
            self.unreached.setdefault(self.nodeid, why[:EVIDENCE_CHARS])

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        self.nodeid = nodeid

    def pytest_runtest_logfinish(self, nodeid: str, location: Any) -> None:
        self.nodeid = None
        if self.out_of_time() and self.session is not None:
            self.session.shouldstop = OUT_OF_BUDGET

    def pytest_exception_interact(self, node: Any, call: Any, report: Any) -> None:
        """A test that raised before any check on an answer failed (a helper's AttributeError, a fixture's KeyError)
        said nothing about a tool's answer. A failed `assert` or `pytest.fail` did."""
        import pytest  # noqa: PLC0415 - loaded already: this hook runs inside a session

        exc = getattr(getattr(call, 'excinfo', None), 'value', None)
        if getattr(report, 'failed', False) and exc is not None and not isinstance(exc, (AssertionError, pytest.fail.Exception)):
            self.errored.setdefault(report.nodeid, type(exc).__name__)

    def pytest_runtest_logreport(self, report: Any) -> None:
        # Any phase: a fixture error fails its test as surely as an assert does.
        if report.failed:
            self.failed.setdefault(report.nodeid, (self.calls.get(report.nodeid) or [None])[-1])
            if first := next((line.strip() for line in str(report.longrepr).splitlines() if line.startswith('E ')), None):
                self.evidence.setdefault(report.nodeid, re.sub(r'^E\s+', '', first)[:EVIDENCE_CHARS])
        elif report.when == 'call' and report.passed:
            self.passed.add(report.nodeid)


def _witness_nodes(recorder: Recorder, manifest: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], list[str]]]:
    """Per manifest test name: (its entry, the node ids of `test_mock_witness.py` that ran it — a parametrized one has several)."""
    ran: dict[str, list[str]] = {}
    for nodeid in {*recorder.passed, *recorder.failed, *recorder.calls}:
        file, _, name = nodeid.partition('::')
        if file.endswith(WITNESS_TESTS):
            ran.setdefault(name.split('[')[0], []).append(nodeid)
    return {str(t['test']): (t, sorted(ran.get(str(t['test']), []))) for t in manifest}


def file_tools(path: Path, tools: list[str], manifest: list[dict[str, Any]] | None) -> list[str]:
    """The tools a test file is about: the manifest's typed entries for the world's witness file, plus every tool its
    source names as a quoted string; every tool when it names none (a file that cannot be read names none)."""
    named = {name for t in manifest or [] for name in entry_tools(t)} if path.name == WITNESS_TESTS else set()
    try:
        source = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        source = ''
    named |= {tool for tool in tools if re.search(rf'[\'"]{re.escape(tool)}[\'"]', source)}
    return [tool for tool in tools if tool in named] or list(tools)


def uncollected_tools(recorder: Recorder, files: list[Path], tools: list[str], manifest: list[dict[str, Any]] | None) -> dict[str, tuple[str, str]]:
    """Per tool: (the first file that failed to collect and names it, why)."""
    out: dict[str, tuple[str, str]] = {}
    for path in files:
        if path.name in recorder.uncollected:
            for tool in file_tools(path, tools, manifest):
                out.setdefault(tool, (path.name, recorder.uncollected[path.name]))
    return out


def verdicts(recorder: Recorder, tools: list[str], manifest: list[dict[str, Any]] | None = None,
             uncollected: dict[str, tuple[str, str]] | None = None, *, no_manifest: str = NO_WITNESS) -> dict[str, dict[str, str]]:
    passed = recorder.passed - set(recorder.failed)
    witness = _witness_nodes(recorder, manifest or [])
    # A failed witness test is charged by its typed entry (every tool in its `tools`); any other to the last tool it called.
    by_node = {nodeid: entry for entry, nodes in witness.values() for nodeid in nodes}
    # A test the mock never answered is set apart: it says the run lost its target, never what a tool answered.
    about = {nodeid: (charged_tools(by_node[nodeid]) if nodeid in by_node else [last]) for nodeid, last in recorder.failed.items()}
    # A test that errored before checking an answer charges no tool.
    charged_to = {nodeid: tools_ for nodeid, tools_ in about.items() if nodeid not in recorder.unreached and nodeid not in recorder.errored}
    unreached_by = {nodeid: tools_ for nodeid, tools_ in about.items() if nodeid in recorder.unreached}
    out = {}
    for tool in tools:
        naming = [(name, nodes) for name, (entry, nodes) in sorted(witness.items())
                  if tool in entry_tools(entry) and entry.get('drive') != REAL_READER_DRIVE]
        callers = {nodeid for nodeid, called in recorder.calls.items() if tool in called} | {n for _name, nodes in naming for n in nodes}
        charged = sorted(nodeid for nodeid, tools_charged in charged_to.items() if tool in tools_charged)
        lost = sorted(nodeid for nodeid, tools_about in unreached_by.items() if tool in tools_about)
        unproven = [name for name, nodes in naming if not nodes or any(n not in passed for n in nodes)]
        if tool in (uncollected or {}):
            file, why = (uncollected or {})[tool]
            verdict = {'state': NOT_WITNESSED, 'reason': f'{file} failed to collect, so none of its tests ran: {why}', 'test': file}
        elif charged:
            why = recorder.evidence.get(charged[0])
            verdict = {'state': NOT_WITNESSED, 'reason': f'{charged[0]} failed on its answer' + (f': {why}' if why else ''), 'test': charged[0]}
        elif lost:
            verdict = {'state': NOT_DETERMINABLE, 'cause': MOCK_TARGET_UNRESOLVED, 'test': lost[0],
                       'reason': f'{MOCK_TARGET_UNRESOLVED}: the deployed mock did not answer {lost[0]} ({recorder.unreached[lost[0]]}) — '
                                 f'not the tool\'s answer; re-run the gate once the mock answers'}
        elif (errored := sorted(n for n in callers if n in recorder.errored)) and not any(n in passed for n in callers):
            verdict = {'state': NOT_DETERMINABLE, 'test': errored[0],
                       'reason': f"a contract test raised {recorder.errored[errored[0]]} before checking this tool's answer"}
        elif manifest is None:
            verdict = {'state': NOT_DETERMINABLE, 'reason': no_manifest}
        elif unproven:
            outcome = OUT_OF_BUDGET if recorder.cut and not dict(naming)[unproven[0]] else 'did not pass'
            verdict = {'state': NOT_DETERMINABLE, 'reason': f'its witness test {unproven[0]} {outcome}', 'test': unproven[0]}
        elif ran := sorted(nodeid for nodeid in callers if nodeid in passed):
            verdict = {'state': WITNESSED, 'reason': f'{len(ran)} contract or witness test(s) called it and passed', 'test': ran[0]}
            # The tools a passing test called AFTER its first call of this one: the gate reads a read-back off it.
            after: dict[str, str] = {}
            for nodeid in ran:
                called = recorder.calls.get(nodeid) or []
                for later in called[called.index(tool) + 1:] if tool in called else []:
                    after.setdefault(later, nodeid)
            if after:
                verdict['read_after'] = dict(sorted(after.items()))
        elif callers:
            verdict = {'state': NOT_DETERMINABLE, 'reason': 'every contract test that called it failed on another tool'}
        elif recorder.cut:
            verdict = {'state': NOT_DETERMINABLE, 'reason': f'{OUT_OF_BUDGET}: no test that ran called it'}
        else:
            verdict = {'state': NOT_DETERMINABLE, 'reason': 'no contract test calls it', 'cause': UNTESTED}
        out[f'surfaces.tools[{tool}]'] = verdict
    return out


#: The backend's files for one world, as `fetch_env_file.py` lands them in `--backend-dir`.
BACKEND_REPORT, BACKEND_ANSWERS, BACKEND_CONTRACT = 'contract_report.json', 'contract_answers.json', 'test_mock_contract.py'
#: Where the backend's run keeps the two test files the gate runs (node ids are relative to its `simulation_env/`).
BACKEND_TESTS = {'contract': 'tests/test_mock_contract.py', 'witness': f'tests/{WITNESS_TESTS}'}
#: What a test the backend's runtime could not answer raised, as a verdict reason names it.
BACKEND_NOT_RUN = "a runtime fault on the backend's verification host"


def served_tree(env_id: str) -> dict[str, Any] | None:
    """The env's `detail.served_tree` (`{tree_digest, source_id}`), or None: no answer, or a backend that predates it."""
    from urllib.parse import quote  # noqa: PLC0415

    if not env_id:
        return None
    base, get, refused = backend_getter('mock_contract_check')
    if refused:
        return None
    status, body, _ = get(f'{base}/jobs/{quote(env_id, safe="")}')
    try:
        served = (json.loads(body).get('detail') or {}).get('served_tree') if status == 200 else None
    except (ValueError, AttributeError):
        return None
    return served if isinstance(served, dict) and served.get('tree_digest') and served.get('source_id') else None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def adopt_backend_run(backend_dir: Path, served: dict[str, Any] | None, *, contract: Path, witness: Path | None,
                      workdir: Path) -> tuple[Recorder | None, dict[str, Any] | None, str]:
    """(the recorder the backend's run fills, its answers, '') when that run measured exactly the deployed mock;
    (None, None, why not) otherwise — the caller then re-runs the tests here."""
    # A file the fetch could not bring is absent here (the fetch removes a stale copy), and reads as not that run's.
    report, answers = _read_json(backend_dir / BACKEND_REPORT), _read_json(backend_dir / BACKEND_ANSWERS)
    if not isinstance(report, dict):
        return None, None, "the backend's contract report did not reach this machine"
    if not isinstance(report.get('calls'), dict):
        return None, None, "the backend's contract report predates per-test tool calls (an older backend)"
    if served is None:
        return None, None, 'the backend names no tree its live mock serves (an older backend, or the mock is not deployed)'
    if (report.get('tree_digest'), report.get('source_id')) != (served['tree_digest'], served['source_id']):
        return None, None, "the backend's last run measured another tree or build than the deployed mock"
    if report.get('cut') and not report.get('timed_out') and not report.get('passed'):
        return None, None, "the backend's last run was cut before it ran a test"
    if not isinstance(answers, dict) or answers.get('tree_digest') != report['tree_digest'] or not isinstance(answers.get('tools'), dict):
        return None, None, "the backend's answers are not that run's"
    local = contract.read_bytes() if contract.is_file() else None
    theirs = (backend_dir / BACKEND_CONTRACT).read_bytes() if (backend_dir / BACKEND_CONTRACT).is_file() else None
    if local is not None and local != theirs:
        return None, None, "this workdir's contract tests are not the ones the backend ran"
    if any(f.get('nodeid') == 'pytest' for f in report.get('failed') or [] if isinstance(f, dict)):
        return None, None, "the backend's pytest exited red with no test row saying why"

    def rel(path: Path) -> str:
        try:
            return path.relative_to(workdir).as_posix()
        except ValueError:
            return path.name
    # The backend's node ids, as this run would have named the same tests; any other file of its run is not this gate's.
    local_file = {BACKEND_TESTS['contract']: rel(contract) if local is not None else None, BACKEND_TESTS['witness']: rel(witness) if witness else None}

    def local_id(nodeid: str) -> str | None:
        file, sep, rest = str(nodeid).partition('::')
        return f'{local_file[file]}{sep}{rest}' if local_file.get(file) else None

    recorder = Recorder()
    calls = {n: [str(t) for t in tools] for nodeid, tools in report['calls'].items() if (n := local_id(nodeid)) and isinstance(tools, list)}
    recorder.calls = calls
    recorder.passed = {n for nodeid in report.get('passed') or [] if (n := local_id(nodeid))}
    for row in report.get('failed') or []:
        nodeid = str(row.get('nodeid') or '') if isinstance(row, dict) else ''
        if '::' not in nodeid and local_file.get(nodeid):  # a file that failed to collect
            lines = [line.strip() for line in str(row.get('output') or '').splitlines() if line.strip()]
            recorder.uncollected.setdefault(Path(local_file[nodeid]).name, (lines[-1] if lines else 'collection error')[:300])
        elif n := local_id(nodeid):
            recorder.failed[n] = (calls.get(n) or [None])[-1]
            # The first `E ` line, as a re-run reads it off the report; else the exception's own message.
            first = next((re.sub(r'^E\s+', '', line.strip()) for line in str(row.get('output') or '').splitlines() if line.startswith('E ')), '')
            first = first or next((line.strip() for line in str(row.get('message') or '').splitlines() if line.strip()), '')
            if first:
                recorder.evidence[n] = first[:EVIDENCE_CHARS]
    recorder.errored = {n: str(kind) for nodeid, kind in (report.get('errored') or {}).items() if (n := local_id(nodeid)) and n in recorder.failed}
    for row in report.get('not_run') or []:
        if isinstance(row, dict) and (n := local_id(str(row.get('nodeid') or ''))):
            recorder.failed.setdefault(n, (calls.get(n) or [None])[-1])
            recorder.errored.setdefault(n, BACKEND_NOT_RUN)
    recorder.cut = bool(report.get('cut'))
    kept = {str(tool): [{**a, 'test': local_id(str(a.get('test'))) or a.get('test')} for a in rows if isinstance(a, dict)]
            for tool, rows in answers['tools'].items() if isinstance(rows, list)}
    return recorder, kept, ''


def _collect(path: Path, workdir: Path, recorder: Recorder, *extra: str) -> None:
    """One pytest session over one file. Neither the repo's addopts nor a conftest above the workdir changes it."""
    import pytest  # noqa: PLC0415

    code = pytest.main([str(path), *extra, '-q', '-p', 'no:cacheprovider', '--rootdir', str(workdir), '--confcutdir', str(workdir), '-o', 'addopts='],
                       plugins=[recorder])
    recorder.session_ended(path, int(code))


def preflight(files: list[Path]) -> list[str]:
    """`<file>: <why>` for each file that does not compile or that pytest cannot collect."""
    problems: list[str] = []
    os.environ.setdefault('PLURAI_MOCK_URL', 'http://127.0.0.1:9/')  # read at import by the contract template; never called
    for path in files:
        recorder = Recorder()  # per file: the recorder keys by file name, and two files may share one
        try:
            source = path.read_text(encoding='utf-8')
        except OSError as exc:
            problems.append(f'{path.name}: cannot be read: {exc.strerror or exc}')
            continue
        try:
            compile(source, str(path), 'exec')
        except (SyntaxError, ValueError) as exc:
            problems.append(f'{path.name}: does not compile: {type(exc).__name__}: {exc}')
            continue
        recorder = Recorder()  # per file: two generated files may share a name
        _collect(path, path.parent, recorder, '--collect-only')
        if path.name in recorder.uncollected:
            problems.append(f'{path.name}: pytest cannot collect it: {recorder.uncollected[path.name]}')
    return problems


def main_preflight(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog='mock_contract_check.py preflight', description='compile + collect generated test files')
    parser.add_argument('files', nargs='+')
    args = parser.parse_args(argv)
    try:
        import pytest  # noqa: PLC0415, F401
    except ImportError as exc:
        print(f'mock contract preflight: {exc.name or exc} is not importable in this interpreter', file=sys.stderr)
        return 2
    if sys.path and Path(sys.path[0] or '.').resolve() == Path(__file__).resolve().parent:
        del sys.path[0]
    files = [Path(f).resolve() for f in args.files]
    # A file the witness fetch could not bring is the gate's recorded coverage gap, never a defect of the file.
    for path in [f for f in files if gap_reason(f.parent)]:
        files.remove(path)
        print(f'mock contract preflight: {path.name}: not checked — {gap_reason(path.parent)}', file=sys.stderr)
    problems = preflight(files)
    for line in problems:
        print(f'mock contract preflight: {line}', file=sys.stderr)
    print(f'mock contract preflight: {len(files) - len(problems)} of {len(files)} file(s) compile and collect')
    return 1 if problems else 0


def _no_verdict(why: str) -> int:
    print(f'mock contract: no verdict — {why}', file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['preflight']:
        return main_preflight(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('contract', help='the bench\'s test_mock_contract.py, beside .mock_service_url')
    parser.add_argument('--witness-out', required=True)
    parser.add_argument('--tools', required=True, help='the mocked tools to report on, comma-separated')
    parser.add_argument('--witness-dir', help=f'the directory holding the world\'s {WITNESS_TESTS}, its helper and {WITNESS_MANIFEST}')
    parser.add_argument('--deadline-s', type=float, help='seconds after which no further test starts; the verdicts are still written')
    parser.add_argument('--answers-out', help='where to keep what the mock answered, per tool (`mock_shape_probe.py` reads it)')
    parser.add_argument('--backend-dir', help="the backend's last run of this world (`fetch_env_file.py`), adopted when it measured the deployed mock")
    args = parser.parse_args(argv)
    if args.answers_out:
        Path(args.answers_out).unlink(missing_ok=True)
        Path(args.answers_out).with_name(SHAPE_READ.name).unlink(missing_ok=True)
    contract = Path(args.contract).resolve()
    workdir = contract.parent
    url_file = workdir / '.mock_service_url'
    url = url_file.read_text(encoding='utf-8').strip() if url_file.is_file() else ''
    if not url:
        return _no_verdict('there is no deployed mock (.mock_service_url)')
    env_file = workdir / '.env_id'
    env_id = env_file.read_text(encoding='utf-8').strip() if env_file.is_file() else ''
    if env_id and (named := re.search(r'/mock/([^/?#]+)', url)) and named.group(1) != env_id:
        return _no_verdict(f'{MOCK_TARGET_UNRESOLVED}: .mock_service_url names env {named.group(1)}, .env_id is {env_id} — redeploy this world first')
    try:
        import pytest  # noqa: PLC0415, F401 - importable, or no verdict
        import requests  # noqa: PLC0415
    except ImportError as exc:
        return _no_verdict(f'{exc.name or exc} is not importable in this interpreter')

    os.environ['PLURAI_MOCK_URL'] = url
    token = _bearer_for(url)
    if token:
        os.environ['PLURAI_TOKEN'] = token
    else:
        os.environ.pop('PLURAI_TOKEN', None)
    os.environ.pop('PYTEST_ADDOPTS', None)
    recorder = Recorder()
    recorder.deadline = time.monotonic() + args.deadline_s if args.deadline_s else None
    send = requests.sessions.Session.request

    base = url.rstrip('/')

    def recording(session: Any, method: str, url: str, *rest: Any, **kwargs: Any) -> Any:
        path = str(url).split('?')[0].rstrip('/')
        if path.endswith('/mock/execute'):
            recorder.record(kwargs.get('json'))
        target = path.startswith(base)
        # The witness helper copy a stored env carries reads a pre-rename token name and sends no bearer: supply ours.
        if target and token and not any(str(k).lower() == 'authorization' for k in kwargs.get('headers') or {}):
            kwargs['headers'] = {**(kwargs.get('headers') or {}), 'Authorization': f'Bearer {token}'}
        try:
            resp = send(session, method, url, *rest, **kwargs)
        except Exception as exc:
            if target:  # no answer at all: refused, reset, timed out
                recorder.not_answered(f'{str(method).upper()} {path[len(base):] or "/"}: {type(exc).__name__}')
            raise
        if path.endswith('/mock/execute') and getattr(resp, 'status_code', 500) < 400:
            recorder.keep_answer(kwargs.get('json'), resp)
        if target and getattr(resp, 'status_code', None) in UNREACHED_STATUSES:
            code = ''
            with contextlib.suppress(Exception):
                code = str(resp.json().get('code') or '')
            recorder.not_answered(f'{str(method).upper()} {path[len(base):] or "/"} -> HTTP {resp.status_code}' + (f' {code}' if code else ''))
        return resp

    requests.sessions.Session.request = recording
    # This plugin's own scripts never shadow a module the customer's tests import.
    if sys.path and Path(sys.path[0] or '.').resolve() == Path(__file__).resolve().parent:
        del sys.path[0]
    directory = Path(args.witness_dir).resolve() if args.witness_dir else None
    # A witness file the fetch could not bring is a coverage gap, never a test: the others alone do not run.
    gap = gap_reason(directory)
    manifest = None if gap else witness_manifest(directory)
    files = [*([contract] if contract.is_file() else []),
             *([directory / WITNESS_TESTS] if manifest and directory and (directory / WITNESS_TESTS).is_file() else [])]
    if not files:
        return _no_verdict(gap or f'there is neither a test_mock_contract.py nor the world\'s {WITNESS_TESTS} with its {WITNESS_MANIFEST}')
    adopted, adopted_answers = None, None
    if args.backend_dir:
        witness = directory / WITNESS_TESTS if directory and directory / WITNESS_TESTS in files else None
        backend = Path(args.backend_dir).resolve()
        served = served_tree(env_id) if (backend / BACKEND_REPORT).is_file() else None
        adopted, adopted_answers, why = adopt_backend_run(backend, served, contract=contract, witness=witness, workdir=workdir)
        if adopted is None:
            print(f'mock contract: re-running the tests on the deployed mock — {why}', file=sys.stderr)
    if adopted is not None:
        recorder = adopted
    else:
        try:
            for path in files:  # one session per file: a file that fails to collect stops only its own
                if recorder.out_of_time():
                    break
                _collect(path, workdir, recorder)
        except Exception as exc:  # noqa: BLE001 - whatever stopped the run, it reached no verdict
            return _no_verdict(f'pytest stopped: {type(exc).__name__}: {exc}')
    if not recorder.passed - set(recorder.failed) and not recorder.uncollected:
        return _no_verdict('no contract or witness test passed — the mock was unreachable, refused the token, or failed every test')

    tools = [t for t in args.tools.split(',') if t]
    found = verdicts(recorder, tools, manifest, uncollected_tools(recorder, files, tools, manifest), no_manifest=gap or NO_WITNESS)
    out = Path(args.witness_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if args.answers_out:
        env_file = workdir / '.env_id'
        env_id = env_file.read_text(encoding='utf-8').strip() if env_file.is_file() else ''
        answers = Path(args.answers_out)
        answers.parent.mkdir(parents=True, exist_ok=True)
        kept = adopted_answers if adopted is not None else recorder.answers
        answers.write_text(json.dumps({'schema': ANSWERS_SCHEMA, 'env_id': env_id, 'tools': kept}, indent=1, sort_keys=True, default=str) + '\n', encoding='utf-8')
    counts = {state: sum(1 for v in found.values() if v['state'] == state) for state in (WITNESSED, NOT_WITNESSED, NOT_DETERMINABLE)}
    lost = sum(1 for v in found.values() if v.get('cause') == MOCK_TARGET_UNRESOLVED)
    print(f'mock contract: {counts[WITNESSED]} of {len(found)} mocked tool(s) witnessed, {counts[NOT_WITNESSED]} not witnessed, '
          f'{counts[NOT_DETERMINABLE]} not determinable' + (f' ({lost} because the mock stopped answering: {MOCK_TARGET_UNRESOLVED})' if lost else '')
          + (f'; the run stopped at its {args.deadline_s:g}s budget' if recorder.cut and adopted is None else '')
          + ("; read off the backend's own run of the deployed tree, not re-run" + ('; that run was cut at its budget' if recorder.cut else '')
             if adopted is not None else ''))
    return 0


if __name__ == '__main__':
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
