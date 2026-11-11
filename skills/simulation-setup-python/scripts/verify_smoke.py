"""Smoke-test that simulate.py loads + responds to an Init envelope.

Step 9e's static verifiers (verify_simulate.py, verify_config.py) catch
~80% of contract bugs but can't detect:

  * Missing Phase 1 ``sys.modules`` stubs — production deps not in the
    the runner's environment (``RestrictedPython``, ``supabase``,
    ``opentelemetry``, etc.). Each manifests as ``No module named 'X'``
    when the child imports the agent.
  * Package-name shadowing — naming the customer's simulation directory
    ``plurai/`` makes ``import plurai`` resolve to it instead of the SDK,
    breaking ``plurai.configure(...)`` (see Step 8.4's "DIRECTORY NAMING"
    warning).
  * IPC handshake failures — a stray ``print(...)`` to stdout breaks
    the JSONL channel before the first envelope round-trips.

This script spawns simulate.py via the exact same ``plurai._subprocess.ChildProcess``
the production runner uses, sends one minimal ``Init`` envelope, and
classifies what happens:

  * **import_ok**: child loaded the module and responded with ``InitOk``.
    The contract surface is reachable. Real ``plurai-run-simulation``
    can take over from here.
  * **missing_stub**: child crashed with ``No module named 'X'``. The
    fix is to add ``sys.modules.setdefault("X", MagicMock())`` to
    simulate.py's Phase 1 stubs block. The script names X for you.
  * **agent_interpreter**: the same crash, but X is the agent's OWN dependency
    (declared by its project, or installed in one of its venvs) — the child ran
    under the wrong interpreter. The hint names the one to use
    (``PLURAI_CHILD_PYTHON=<path>``); never stub it, nor ``plurai`` itself. Exits 2.
  * **package_shadow**: child crashed with ``module 'plurai' has no
    attribute 'configure'`` (or similar). The customer's simulation
    directory shadows the SDK package — rename per Step 8.4.
  * **ipc_handshake**: child wrote non-JSON to stdout. There's a
    ``print(...)`` somewhere in the import path that needs ``file=sys.stderr``.
  * **syntax_error**: simulate.py / helpers don't parse.
  * **other**: the failure didn't match a known pattern — the script
    prints the child's full stderr so the customer can debug.

Usage::

    source "${CLAUDE_PLUGIN_ROOT}/scripts/resolve-python.sh"
    "$PLURAI_PY" "${CLAUDE_PLUGIN_ROOT}/skills/simulation-setup-python/scripts/verify_smoke.py" plurai_sim/simulate.py

Under ``--pipeline`` it drives ONE ``invoke_pipeline`` pass per ``agent.smoke_inputs`` entry of the
``bench_manifest.json`` beside simulate.py, each on a fresh child Init'd with that world on the
local mock: exit 0 all passed, 1 the harness raised (its typed exception is the verdict), 3 the
agent raised (by the SDK's own owner rule; an SDK without it reads every raise as 1), 2 no verdict.
An agent-typed raise the generated harness owns — its innermost own frame is in the harness, or it
names a class the harness defines — is the harness's, exit 1 (``_harness_owned_raise``).

Under ``--seed-world <json>`` it Inits once with the env's complete world (``detail.seed_probe_world``
of the env job) on the local mock: exit 0 when ``seed_state`` accepts it or the harness defines none
(not_applicable) or the bench declares its tools intercepted at the agent's host (not_applicable,
nothing run), 1 when ``seed_state`` raises over a world grounded on a smoke scenario and the Init smoke's
envelope (an empty world) Inits clean (the child's exception verbatim, routed to /edit-env), 2 when that
envelope raises too (a run prerequisite, not the world), when it raises over a world from the schema alone
or over the grounded world's perturbed twin (both advisory), or the backend published no world. An accepted world is
then READ BACK (``plurai.world.seed_landing.seed_readback`` over the Init's post-seed read): a seed not read back as seeded is
the advisory ``seed_readback_gap`` (exit 1 until repaired or overruled), no read path is NO VERDICT (exit 2, never OK),
and an SDK without the comparator (``plurai.world.seed_landing``, or the older ``plurai.seed_landing``) is NO VERDICT before any Init. An empty world Init'd before and after the
probe catches a probe row ``reset()`` did not clear (``reset_leftover``). A DIRECTORY of drawn scenario worlds is read
back one world at a time, the same way.

Under ``--seed-resume <folder>...`` it Inits once with no seed (the control), then once per folder with
the resume seed its ``resume_pin.json`` becomes (the pin's messages minus the trailing user message,
its ``agent_state`` and ``resume_point``); no model call. Every Init gets the turn smoke's envelope —
the first ``agent.smoke_inputs`` world, its inputs merged over the request block, else ``{}`` — so the
seed is the only thing the control and a folder's Init differ by. The last stdout line is JSON
``{folder: null | the child's refusal}``: exit 0 all accepted, 1 any refused, 2 the control raised.

Under ``--sample-cut <folder> --samples N --from-transcript <transcript.json>`` it re-runs the agent at a
regression folder's cut N times, each on a fresh child on the local mock with no world (one model call
each), so the record step can measure how often the issue recurs there. A folder that holds a CLI
session, whose recorded cut turn called a tool, on a harness that is not a ``.py`` file, on a bench whose tools are
intercepted at the agent's host, or on an SDK without the replay-rate functions is "seen once" and nothing
is driven. The last stdout line is JSON ``{valid, invalid, seen_once_reason, rates}``; exit 0 with it, 2
when the folder cannot be read.

With ``--entry-symbol`` / ``--agent-language`` / ``--witness-out`` it writes the ``agent.entry``
witness ``{state, reason}``: ``not_determinable`` without asking the SDK for an ``external`` or
non-Python entry or an SDK without the witness, else what the SDK's last reply carried. The
smoke's own exit code is unchanged.

Under ``--turn`` it also CONFIRMS the config's own declarations against the real harness: each
message content part kind ``agent.io.input`` names gets one probe turn carrying that part, and
``agent.io.may_open`` gets the empty opener. A declaration the probe cannot confirm is refused
here rather than on a paid run — by name, and attributed: a harness that raises on the part is
exit 1, a build of ours that cannot carry it at all is exit 2. Its world is the first
``agent.smoke_inputs`` entry of ``bench_manifest.json`` when declared (its inputs merged over the
request block), else ``{}``. It also fails (exit 1) when its turns report tool-trail entries whose dispatch
did not fail and EVERY one lacks a ``result``, and — on a Python harness that defines ``seed_state`` and does not
declare ``harness.resume.mode: unsupported`` — when Init refuses a canned ``type``-keyed resume seed (no model call).
The remedy is restoring every seed message. Only where the conversation lives in a session the harness cannot write,
declare ``harness.resume.mode: unsupported``; a harness that records its session and resumes it is not one, and one
whose turns report that recording (``cli_session``) is not offered the canned seed, which cannot carry it.

Every mode first refuses, exit 1 and before any child is driven, a harness that references an SDK
attribute or module (``plurai.<name>``, ``from plurai.<module> import <name>``) the child's installed
SDK does not have — naming each one and the upgrade. A reference inside a ``try`` that catches its
absence, or feature-detected with ``hasattr`` / ``getattr(..., default)``, is not required.

The Init smoke (neither flag) also refuses, exit 1, a ``world_snapshot`` that reaches a reader whose
package, in the child's interpreter, lacks an extra that a distribution simulate.py imports at module
scope requires it with (``markitdown[docx]``). It then Inits ``_SIBLING_CHILDREN`` children at once — the final
run's ``--trials`` — each seeding the smoke world plus a probe row only it owns, and reads each child's world back
after all seeded. Fewer would miss a resource whose host fallback seats exactly one extra process (a bind that falls
back from one address to the wildcard). Each outcome against what the config declares is a WARNING (exit 0):
``sibling_init_failed`` (a fixed port, a lock), ``sibling_cross_talk`` (one shared store), ``exclusive_unneeded``
(``harness.single_instance`` declared, and the siblings ran isolated); a declaration a collision confirms reports
nothing. One shared ``external`` world and ``slots: 1`` are not probed: the siblings would write the one store.

On a bench whose ``bench_manifest.json`` declares ``harness.tool_interception.at: agent_host``, the Init
smoke, ``--turn`` and ``--pipeline`` drive no child — an Init there opens the hosted route outside any run —
and print ``~ smoke: not_applicable`` naming the manifest's ``route_smoke``. They check the request block
statically instead: every key must be named in the harness source, else a ``request_key_unread`` finding
(a lead that holds nothing, so the exit stays 0) whose line comes last. ``--sample-cut`` there is seen once
(``agent_host_route``), never sampled; ``--seed-resume`` is NO VERDICT (exit 2), since its caller would read a
skip as the harness accepting every seed. Neither drives a child.

Every failure is a gate finding (``scripts/gate_ledger.py``, id ``plugin.smoke.<Category | SmokeGate>``),
printed as a ``finding:`` line before the summary and written raw to ``--findings-out``. A failing run
exits 1 only while one HOLDS — a security or invariant finding, or an advisory one neither repaired nor
overruled in ``<workdir>/artifacts/.gate_overrules.jsonl``; with nothing holding it exits 3 when the agent
raised, 4 when a turn came back silent with no owner the smoke could determine, and 0 otherwise. A finding is
fingerprinted on typed facts (the gate, the exception class, the declaration); the text it saw is ``observed``.

Exits 0 on import_ok, 1 on a classified failure that holds, 2 on NO VERDICT — a script error, a gate
ledger that cannot be read, an absent SDK, an ``agent_interpreter`` verdict (a child interpreter that cannot import the agent), a channel this build cannot observe (a render bench on an SDK with no ``agent_actions``; a
declared input part this toolchain has no probe for, or one the transport could not carry), or,
under ``--turn``, a render or ``agent.io`` declaration that could not be READ at all. The 1/2 split is the
toolchain's error posture: 1 has a finding about the bench to read, 2 has a prerequisite to supply
and nothing was measured, and `bench_manifest.py` records the second as `not_run` rather than as a
failure of the customer's agent.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import enum
import functools
import hashlib
import importlib
import inspect
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any, NamedTuple

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # below 3.11: the same str-valued enum, interpolating as its value (`f'{member}'`), as enum.StrEnum does

    class StrEnum(str, enum.Enum):
        def __str__(self) -> str:
            return str(self.value)

        def __format__(self, spec: str) -> str:
            return format(str(self.value), spec)

# The SDK's child-driver (`plurai._subprocess.ChildProcess`) is imported INSIDE `_run_smoke`, not
# here. It is needed only to drive a child, and importing it at module scope made the whole module
# unimportable without the SDK installed — which is the state of CI, where the pip line is
# `pytest pyyaml`. The regression tests beside this file then skipped as a module, all of them,
# including the one whose docstring calls it THE regression; they passed locally only because a
# sibling SDK checkout happened to be adjacent. A dependency belongs at the call site that needs
# it when everything else in the file is testable without it.


class _StderrCaptureHandler(logging.Handler):
    """Buffer ``[plurai.child]`` log lines so we can read the child's
    actual error message when the parent only raised "exited with code N".

    ChildProcess routes the child's stderr to a Python logger rather than
    exposing it as an attribute. We attach this handler before starting
    the child, then read ``buffer`` after the failure to extract the
    "No module named X" / shadowing / IPC-pollution diagnostic that
    lives in the child's stderr.
    """

    def __init__(self) -> None:
        super().__init__()
        self.buffer: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(record.getMessage())
        except Exception:  # noqa: BLE001 — never let logging crash the verifier
            pass


_STUB_RE = re.compile(r"No module named '(?P<name>[A-Za-z0-9_.]+)'")
_SHADOW_RE = re.compile(
    r"module ['\"]plurai['\"] has no attribute ['\"](configure|mock|apply_mocks)"
)
_IPC_RE = re.compile(r'Child wrote non-JSON to stdout', re.IGNORECASE)
_SYNTAX_RE = re.compile(r'SyntaxError|does not parse')


class Category(StrEnum):
    """Every smoke verdict ``_classify`` can return — the closed set, declared.

    The SECOND SHAPE of the same contract. ``verify_config.py`` / ``verify_simulate.py`` emit
    many ``Finding`` records per run, each carrying a ``code``; this script emits ONE
    classification per run, as the first element of a returned tuple. The registry is declared
    the same way in both, so the coverage sweep is not fitted to the ``Finding`` shape.

    RESTATED, NOT IMPORTED: each verifier declares its own closed set, and the members they share
    are pinned equal by a test rather than an import — as ``_INPUT_PART_KINDS`` is restated here
    rather than imported from ``verify_config.py``.

    ``StrEnum`` (the shim above it below 3.11), never a bare ``class Category(str, enum.Enum)``: ``_format_failure`` interpolates
    the verdict into the operator's last line, and a ``str``-mixin enum formats as ``Category.X``.

    """

    AGENT_INTERPRETER = 'agent_interpreter'  # `No module named 'X'` where X is the agent's OWN dependency — wrong interpreter, never a stub
    IPC_HANDSHAKE = 'ipc_handshake'  # the child wrote non-JSON to stdout, breaking the channel
    MISSING_STUB = 'missing_stub'  # `No module named 'X'` during the agent import — X needs a Phase 1 stub
    #: MIRRORS THE SDK'S WIRE `ErrorKind` LITERAL (`plurai/_protocol.py`). This
    #: spelling is not ours to rename: it is matched against the child's own error text below.
    MOCK_UNREACHABLE = 'mock_unreachable'  # the child tried to reach a deployed mock; smoke runs mock_mode='local'
    OTHER = 'other'  # no known pattern matched — the child's full stderr is printed instead
    PACKAGE_SHADOW = 'package_shadow'  # the simulation directory is named `plurai/` and shadows the SDK
    SYNTAX_ERROR = 'syntax_error'  # simulate.py or a split helper does not parse


class SmokeGate(StrEnum):
    """Every smoke failure that is not an Init classification (:class:`Category`) — the rest of the closed
    set the ``plugin.smoke.`` family of ``scripts/gate_ledger.py`` names. Its catalogue decides each one's
    authority; this file never does.
    """

    AGENT_RAISED = 'agent_raised'  # the agent's own code raised (the SDK's owner rule): the run's own signal
    HARNESS_RAISED = 'harness_raised'  # invoke_pipeline raised, owned by the harness or by no SDK rule
    INVOKE_NOT_AWAITABLE = 'invoke_not_awaitable'  # the SDK awaited invoke_agent's return and it is not awaitable
    TURN_SILENT = 'turn_silent'  # turn 1 delivered neither speech nor an action
    RENDER_SILENT = 'render_silent'  # the render probe's turn delivered neither speech nor an action
    INPUT_PART_REFUSED = 'input_part_refused'  # the harness raised on a declared part kind, or the config omits a kind a handover needs
    MAY_OPEN_REFUSED = 'may_open_refused'  # the harness raised on the empty opener `agent.io.may_open` declares
    MAY_OPEN_SILENT = 'may_open_silent'  # driven with no user message, the agent said nothing
    REQUEST_KEY_UNREAD = 'request_key_unread'  # the harness never read a request key it was handed
    READER_EXTRA_MISSING = 'reader_extra_missing'  # a reader the world snapshot reaches lacks an extra in the child
    SDK_API_GAP = 'sdk_api_gap'  # the harness references an SDK name the child's SDK does not have
    SEED_WORLD_REFUSED = 'seed_world_refused'  # seed_state refused the env's complete, smoke-grounded world
    SEED_READBACK_GAP = 'seed_readback_gap'  # seed_state accepted the world, but the post-seed read does not return it as seeded
    CONFIRMED_STORE_SEED_READBACK_GAP = 'confirmed_store_seed_readback_gap'  # the same, on a bench with a confirmed disposable copy: hard
    WORLD_READ_RAISED = 'world_read_raised'  # the Init's post-seed read failed (a crash marker): every run would compare against no world
    RESET_LEFTOVER = 'reset_leftover'  # a probe-world row reset() did not clear: an empty Init after the probe still reads it
    SEED_RESUME_REFUSED = 'seed_resume_refused'  # seed_state refused a regression folder's resume seed
    TRAIL_RESULT_MISSING = 'trail_result_missing'  # every tool-trail entry whose dispatch did not fail carries no result
    TRAIL_TOOL_UNDECLARED = 'trail_tool_undeclared'  # a trail row's `tool` is the framework's wire name of exactly one declared key
    TRAIL_TOOL_UNTYPED = 'trail_tool_untyped'  # the framework ran (`status: ok`) a function a closed surface does not declare: a warning
    CANNED_RESUME_REFUSED = 'canned_resume_refused'  # seed_state refused (or restored none of) a canned type-keyed resume seed
    TOOL_HISTORY_DROPPED = 'tool_history_dropped'  # a tool result did not carry to the next turn, or a seed with one was refused: a warning
    CHANNEL_REFUSED = 'channel_refused'  # the harness raised on a turn carrying a planted `agent.io.channels` input
    DOCUMENT_UNDELIVERED = 'document_undelivered'  # the agent could not quote the planted request-block document on turn 0
    SIBLING_INIT_FAILED = 'sibling_init_failed'  # Init alone passed, the final run's trials Init'd at once did not: a resource one process holds
    SIBLING_CROSS_TALK = 'sibling_cross_talk'  # siblings Init'd, and one read back a sibling's probe row or lost its own: one shared store
    EXCLUSIVE_UNNEEDED = 'exclusive_unneeded'  # the config declares what one process holds, and siblings ran isolated: it serializes for nothing


#: The failures a passing smoke carries as warnings: minted as findings that hold nothing (`gate_ledger.NON_HOLDING`),
#: printed above the run's own OK line, never the line a caller records.
_WARNING_GATES: frozenset[SmokeGate] = frozenset(
    {
        SmokeGate.TRAIL_TOOL_UNTYPED, SmokeGate.SIBLING_INIT_FAILED, SmokeGate.SIBLING_CROSS_TALK, SmokeGate.EXCLUSIVE_UNNEEDED,
        SmokeGate.TRAIL_RESULT_MISSING, SmokeGate.TOOL_HISTORY_DROPPED,
    }
)


class SmokeFailure(NamedTuple):
    """One failure behind a failing verdict: its gate, the stable statement its fingerprint covers (typed
    facts — the gate, the exception class, the declaration — never an exception's text), the file it is
    about, and what it saw (the text, which moves from run to run and is never fingerprinted)."""

    gate: Category | SmokeGate
    message: str
    file: str
    observed: dict[str, str]


class Verdict(tuple):
    """``(exit_code, summary)`` — what every smoke mode returns and its callers unpack — plus the
    :class:`SmokeFailure` s behind it: one or more on a failing verdict, none otherwise."""

    failures: tuple[SmokeFailure, ...]

    def __new__(cls, exit_code: int, summary: str, failures: Iterable[SmokeFailure]) -> Verdict:
        verdict = super().__new__(cls, (exit_code, summary))
        verdict.failures = tuple(failures)
        return verdict


def _failed(exit_code: int, summary: str, gate: Category | SmokeGate, *, message: str, file: str, observed: dict[str, str]) -> Verdict:
    """A failing verdict carrying its one failure."""
    return Verdict(exit_code, summary, [SmokeFailure(gate, message, file, observed)])


def _with_measured(verdict: Verdict | tuple[int, str], measured: list[SmokeFailure], notes: list[str]) -> Verdict | tuple[int, str]:
    """``verdict`` carrying the failures a run RECORDED and drove on past (a silent turn, an unread key): they
    never stop the probes and worlds after them, so a non-holding one cannot leave the rest unrun and the
    run read verified. A passing verdict becomes failing on them; a no-verdict one (exit 2) stays one."""
    if not measured:
        return verdict
    measured = sorted(measured, key=lambda f: f.gate in _WARNING_GATES)  # a warning is never the line a caller records
    code, summary = verdict
    if code == 0:
        # The first failure's message is the verdict and ENDS the summary whatever precedes it: `_standalone_last_line`
        # returns a one-line message untouched, which dropped the verdict when a single note (or none) preceded it.
        return Verdict(1, '\n'.join([*notes, *(f.message for f in measured[1:]), measured[0].message]), measured)
    if code == 2:
        return verdict
    return Verdict(code, summary, [*measured, *getattr(verdict, 'failures', ())])


_DIST_NAME_RE = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')
_DECLARATION_GLOBS = ('pyproject.toml', 'requirements*.txt', 'uv.lock')


def _norm_dist(name: str) -> str:
    """PEP 503 normalization, so `langchain-core` matches the import name `langchain_core`."""
    return re.sub(r'[-_.]+', '-', name).lower()


@functools.lru_cache(maxsize=1)
def _note_no_tomllib() -> None:
    """Say once, on stderr, that this interpreter cannot read the TOML dependency declarations."""
    print(
        f'verify_smoke: Python {sys.version_info.major}.{sys.version_info.minor} has no tomllib (3.11+), so '
        f'pyproject.toml / uv.lock were not read for declared dependencies; run under the interpreter the SDK runs on (3.12+).',
        file=sys.stderr,
    )


def _declared_by(root: Path, dist: str) -> str | None:
    """The file under ``root`` that declares distribution ``dist`` (normalized), or None.

    Stdlib only — this file runs under the agent's own interpreter. Reads
    ``pyproject.toml`` ([project] dependencies + optional-dependencies, poetry's table),
    ``requirements*.txt`` and ``uv.lock`` package names. Unreadable files testify to nothing.

    ``tomllib`` is 3.11+. Below that only ``requirements*.txt`` is read, and stderr says the
    TOML declarations went unread — the SDK itself needs 3.12, so this is a misrouted interpreter.
    """
    if sys.version_info >= (3, 11):
        import tomllib  # noqa: PLC0415 — only the classification of a missing module needs it
    else:
        tomllib = None
        _note_no_tomllib()

    for pattern in _DECLARATION_GLOBS:
        for path in sorted(root.glob(pattern)):
            names: list[str] = []
            if tomllib is None and path.suffix != '.txt':
                continue
            try:
                if path.suffix == '.txt':
                    names = [line for line in path.read_text(encoding='utf-8').splitlines() if not line.lstrip().startswith(('#', '-'))]
                else:
                    data = tomllib.loads(path.read_text(encoding='utf-8'))
                    if path.name == 'uv.lock':
                        names = [p.get('name', '') for p in data.get('package', [])]
                    else:
                        project = data.get('project', {})
                        names = list(project.get('dependencies', []))
                        for group in project.get('optional-dependencies', {}).values():
                            names.extend(group)
                        names.extend(data.get('tool', {}).get('poetry', {}).get('dependencies', {}))
            except (OSError, UnicodeDecodeError, ValueError, AttributeError, TypeError):  # TOMLDecodeError is a ValueError
                continue
            for spec in names:
                m = _DIST_NAME_RE.match(str(spec))
                if m and _norm_dist(m.group(1)) == dist:
                    return path.name
    return None


def _agent_interpreters(roots: tuple[Path, ...], failed: str) -> list[Path]:
    """The agent's own venv interpreters under ``roots`` — every ``.venv*`` / ``venv*`` — other
    than ``failed``, the one the child just ran under. Paths are compared UNRESOLVED: a venv's
    python is a symlink to its base interpreter, and resolving it would collapse every venv."""
    found: list[Path] = []
    for root in roots:
        for pattern in ('.venv*', 'venv*'):
            for venv in sorted(root.glob(pattern)):
                for rel in ('bin/python', 'Scripts/python.exe'):
                    python = venv / rel
                    if python.is_file() and os.path.abspath(python) != failed and python not in found:
                        found.append(python)
    return found


def _has_module(python: Path, module: str) -> bool:
    """Whether ``python`` can find ``module``, asked in a subprocess. ``-I``: the interpreter's
    installed packages only — never this process's cwd or PYTHONPATH."""
    code = 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)'
    try:
        return subprocess.run([str(python), '-I', '-c', code, module], capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _agent_dependency_hint(name: str, simulate_path: Path | None) -> str | None:
    """The AGENT_INTERPRETER hint when missing module ``name`` is the agent's own dependency —
    declared by its project, or importable from one of its venvs — else None (a stub candidate)."""
    if simulate_path is None:
        return None
    top = name.split('.')[0]
    roots = (simulate_path.parent.parent, simulate_path.parent)
    failed = os.path.abspath(os.environ.get('PLURAI_CHILD_PYTHON') or sys.executable)
    declared = next((f for f in (_declared_by(r, _norm_dist(top)) for r in roots) if f), None)
    with_dep = [p for p in _agent_interpreters(roots, failed) if _has_module(p, top)]
    # The child imports the SDK too: prefer a venv that can, else say what it still lacks.
    has_it = next((p for p in with_dep if _has_module(p, 'plurai')), None)
    sdk_gap = '' if has_it or not with_dep else ' and make plurai importable there'
    has_it = has_it or next(iter(with_dep), None)
    if declared is None and has_it is None:
        return None
    why = f'declared in the agent\'s {declared}' if declared else f'installed in {has_it}'
    fix = (
        f'run the smoke with PLURAI_CHILD_PYTHON={has_it}{sdk_gap}'
        if has_it
        else "install the agent's dependencies into its venv and run with PLURAI_CHILD_PYTHON=<that venv's python>"
    )
    return (
        f'`{top}` is the agent\'s own dependency ({why}) but is absent from the interpreter that ran '
        f'the child ({failed}): {fix}. Never stub an agent dependency — a MagicMock\'d `{top}` hollows '
        f'the agent under test.'
    )


def _classify(stderr: str, init_error: object | None = None, simulate_path: Path | None = None) -> tuple[Category, str]:
    """Return ``(category, hint)`` for the smoke failure.

    ``category`` is a member of ``Category`` — the declared closed set above, never a literal.
    ``hint`` is the actionable one-liner: which module to stub, etc.

    ``init_error`` (when provided) is appended to the search corpus —
    pass an InitError-like object with a ``.message`` attribute, or None.
    ``simulate_path`` locates the agent project whose declared deps / venvs decide whether a
    missing module is the agent's own (AGENT_INTERPRETER) or a stub candidate (MISSING_STUB).
    """
    err_text = stderr + ('\n' + getattr(init_error, 'message', '') if init_error else '')

    if 'mock_mode' in err_text and ('Could not reach' in err_text or Category.MOCK_UNREACHABLE in err_text):
        return (Category.MOCK_UNREACHABLE, 'the smoke test must not probe a deployed mock — pass mock_mode="local" (this script does; a custom caller hit the connect path)')
    m = _STUB_RE.search(err_text)
    if m:
        name = m.group('name')
        if name.split('.')[0] == 'plurai':
            child = os.path.abspath(os.environ.get('PLURAI_CHILD_PYTHON') or sys.executable)
            return (Category.AGENT_INTERPRETER, (
                f'`{name}` is the plurai SDK, not importable in the child interpreter ({child}): set '
                f'PLURAI_CHILD_PYTHON to an interpreter that imports both the agent and plurai. Never stub the SDK.'
            ))
        agent_dep = _agent_dependency_hint(name, simulate_path)
        if agent_dep is not None:
            return (Category.AGENT_INTERPRETER, agent_dep)
        return (Category.MISSING_STUB, (
            f'add `sys.modules.setdefault("{name}", MagicMock())` to '
            f'simulate.py\'s Phase 1 stubs block — only for a dependency behind a mocked surface. '
            f'If the module is a package '
            f'with sub-imports, also stub the submodules you see in subsequent '
            f'runs (or install a meta_path finder — see Step 8.4 prose).'
        ))

    if _SHADOW_RE.search(err_text):
        return (Category.PACKAGE_SHADOW, (
            'the customer\'s simulation directory shadows the `plurai` SDK '
            'package. Rename the directory from `plurai/` to `plurai_sim/` '
            '(or any name that doesn\'t collide). See Step 8.4 "DIRECTORY '
            'NAMING" warning.'
        ))

    if _IPC_RE.search(err_text):
        return (Category.IPC_HANDSHAKE, (
            'a print(...) statement on the import path wrote to stdout, '
            'breaking the IPC handshake. Add file=sys.stderr to every '
            'print() that fires during module import.'
        ))

    if _SYNTAX_RE.search(err_text):
        return (Category.SYNTAX_ERROR, (
            'simulate.py or a helper (_storage.py / _mocks.py) does not '
            'parse. Run verify_simulate.py + verify_config.py first to get '
            'the file + line, then fix the syntax error.'
        ))

    return (Category.OTHER, (
        'failure didn\'t match a known pattern. Full child stderr printed '
        'below — examine for the root cause.'
    ))


class RenderSurface(NamedTuple):
    """What the config declares this agent renders — and, when nothing could be read, WHY.

    Two states used to be one. ``except Exception: return ()`` made "this agent declares no render
    surface" indistinguishable from "the declaration could not be read at all" (an older SDK with
    no reader, a reader that raised), and the second silently skips the render probe on exactly
    the benches whose only output channel is the one being skipped.
    """

    verbs: tuple[str, ...]
    unreadable: str | None = None


#: The root key that declares what the agent can be HANDED. Owned by the plugin's
#: `verify_config.py` (`_AGENT_IO_KEY` / `_AGENT_IO_KNOWN_KEYS` / `_INPUT_PART_KINDS`) — which
#: gates its SHAPE at authoring time; this file turns the same declaration into probe turns
#: against the real child. The two vocabularies are pinned equal by
#: `tests/test_smoke_io_probe.py`, not by an import.
_AGENT_IO_KEY = 'agent.io'
_INPUT_PART_KINDS = ('text', 'image_url', 'file')

#: What a declaration that carries `may_open:` but no `input:` accepts. The SDK's own
#: `plurai.agent_io.DEFAULT_INPUT_PARTS`, mirrored: a bench declaring only that it opens the
#: conversation has said nothing about its parts, and the runner hands it text. Read as `()` here,
#: the note printed `input=[]` at a bench the SDK drives with text — a declaration misreported to
#: the one person who could correct it.
_DEFAULT_INPUT_PARTS = ('text',)

#: What each probe part CARRIES. Content the agent can answer from, so a probe turn that works
#: looks like a turn rather than like noise — and one fact per part, so a reply proves the part
#: was read rather than merely tolerated.
_PROBE_PART_TEXT = 'Here is an attachment. What does it say? Answer in one short sentence.'
_PROBE_FILE_MARKER = 'COBALT-41'
_PROBE_FILE_BODY = f'Plurbo smoke attachment. The passphrase is {_PROBE_FILE_MARKER}.\n'
#: A 1x1 transparent PNG, inline. The smallest thing that is honestly an image, and reaching no
#: host: a probe that fetches a URL measures the network, not the harness.
_PROBE_IMAGE_DATA_URI = (
    'data:image/png;base64,'
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
)

#: Where a workspace declaration is looked for, relative to each root — the SDK's own two rungs
#: (`plurai.ingest.workspace_config.CONFIG_CANDIDATE_RELPATHS`), restated rather than imported for
#: the reason above.
_CONFIG_RELPATHS = (Path('plurai.config.yaml'), Path('plurai_sim') / 'plurai.config.yaml')


class AgentIO(NamedTuple):
    """What `agent.io:` declares this agent can be handed — and, when nothing could be read, WHY.

    Three states, kept apart for `RenderSurface`'s reason: ``source is None`` and no
    ``unreadable`` means NOTHING WAS DECLARED (the default bench: text turns, the user speaks
    first) and nothing extra is probed or said; ``source`` set means a declaration to confirm;
    ``unreadable`` means a config exists and could not testify, which under ``--turn`` is a NO
    VERDICT rather than a pass, because "declares nothing" and "nobody could read it" are
    different facts about what this run measured.
    """

    input_parts: tuple[str, ...] = ()
    may_open: bool = False
    source: str | None = None
    unreadable: str | None = None
    #: The key that made ``may_open`` true, as the config spells it — what a remedy tells the author to drop.
    opener_key: str = 'may_open: true'
    #: Kinds in ``input_parts`` that the config does NOT declare: the scenario set hands over documents
    #: (a `.document_fields` entry), so the smoke reports the gap instead of probing them.
    undeclared_handover: tuple[str, ...] = ()
    #: Whether a config declares ``shape: pipeline``, read off the documents `agent.io` is read from: a pipeline
    #: bench has no turn to carry a part. The runner's own `strip().lower()` reading.
    pipeline_shape: bool = False
    #: The declared `agent.io.channels` (C1) entries well-formed enough to plant into; `verify_config` owns their shape.
    channels: tuple[dict, ...] = ()


class RequestBlock(NamedTuple):
    """The `inject: request` values a REAL scenario would hand this harness on turn 1, and where
    they were read from — or, when none were, why not. ``source`` is always a sentence."""

    values: dict
    source: str
    #: The seeded world of the slot the values were read from (its ``initial_world_state.yaml``, ``{}`` when the
    #: slot has none) — the store a run Inits that slot with. ``None`` when the values are not a slot's.
    world: dict | None = None


def _real_request_block(simulate_path: Path) -> RequestBlock:
    """A real slot's `agent_context.yaml`, PAIRED with that slot's own seeded world.

    Param 1 is the block. Every `inject: request` value is spread into the turn-1 payload, and
    a harness that cannot ACCEPT one of those keys dies on turn 1 of every scenario — a harness
    that spreads the payload straight into a typed agent state fails hard on any key that state
    does not declare, and an empty `{}` here cannot provoke it. A smoke that inits with
    two empty dicts therefore certifies a bench against an envelope no run will ever send.

    So read a fetched slot's own block: ``<workspace>/artifacts/scenarios/<slot>/agent_context.yaml``,
    the client-side layout `fetch_slot_bundle.py` writes. The FIRST slot in sorted
    order that carries a non-empty mapping wins — slots of one set share a schema, so any of them
    exercises the same keys, and sorting makes the choice reproducible between runs.

    Param 2 is that SAME slot's `initial_world_state.yaml` (`{}` when the slot carries none, as a
    run Inits it): a block names records — a thread, an account — that only its own world seeds, so
    a block beside an empty world is an envelope no run sends, and a harness that resolves the
    named record refuses it. A slot whose world file cannot be read is skipped, not paired with `{}`.

    When nothing is fetched yet — the ordinary state at Step 9f, before any scenario set exists —
    this falls back to `{}` and SAYS SO. That is the point of returning the reason rather than the
    dict alone: `{}` from a workspace with no slots and `{}` from a slot whose file was unreadable
    are different facts about what this run measured, and the second must not read as the first.
    """
    for root in (simulate_path.parent, simulate_path.parent.parent):
        slots_dir = root / 'artifacts' / 'scenarios'
        if not slots_dir.is_dir():
            continue
        candidates = sorted(p for p in slots_dir.glob('*/agent_context.yaml') if p.is_file())
        if not candidates:
            return RequestBlock({}, (
                f'no slot under {slots_dir} carries an agent_context.yaml, so the request block is '
                f'{{}} — fetch a scenario set first (/simulation-generate-suite Step 5, which runs '
                f'skills/simulation-generate-suite/scripts/fetch_slot_bundle.py) to smoke the '
                f'keys a real run will spread into turn 1'
            ))
        for path in candidates:
            try:
                import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml

                parsed = yaml.safe_load(path.read_text(encoding='utf-8'))
            except Exception as exc:  # noqa: BLE001 — an unreadable slot is reported, not swallowed
                return RequestBlock({}, f'{path} could not be read ({type(exc).__name__}: {exc}), so the request block is {{}}')
            if not (isinstance(parsed, dict) and parsed):
                continue
            world_path = path.parent / 'initial_world_state.yaml'
            try:
                world = (yaml.safe_load(world_path.read_text(encoding='utf-8')) or {}) if world_path.is_file() else {}
            except Exception as exc:  # noqa: BLE001 — an unreadable world is reported, not swallowed
                return RequestBlock({}, f'{world_path} could not be read ({type(exc).__name__}: {exc}), so the request block is {{}}')
            if not isinstance(world, dict):
                return RequestBlock({}, f'{world_path} is not a mapping, so the request block is {{}}')
            return RequestBlock(parsed, f'the real slot block at {path}', world)
        return RequestBlock({}, f'every agent_context.yaml under {slots_dir} is empty, so the request block is {{}}')
    return RequestBlock({}, (
        f'this workspace has no artifacts/scenarios/ directory beside {simulate_path.name}, so the '
        f'request block is {{}} — no scenario set has been fetched yet'
    ))


def _init_request_block(simulate_path: Path) -> tuple[RequestBlock, frozenset[str]]:
    """``(the Init smoke's request block, the store-only field names)``: a real slot's block, else one planted
    value per declared request key."""
    request_block = _real_request_block(simulate_path)
    planted, store_only = _planted_request_block(simulate_path)
    if not request_block.values and planted:
        request_block = RequestBlock(planted, 'values planted for every declared request key (no scenario slot is fetched yet)')
    return request_block, store_only


#: `.document_fields` format -> the media type the backend folds a generated binary document as; a text
#: format (`txt`, `html`) folds inline as the text itself.
_DOCUMENT_MEDIA_TYPES = {'pdf': 'application/pdf', 'image': 'image/png', 'email': 'image/png'}
#: A declared field `type:` prefix -> a well-typed planted value. Only the READ is measured, so the
#: value need only be one the harness can accept; an untyped or string field gets a named marker.
_TYPED_PLANTS: tuple[tuple[str, Callable[[], object]], ...] = (('list', list), ('dict', dict), ('int', int), ('float', float), ('bool', bool))


def _planted_request_block(simulate_path: Path) -> tuple[dict, frozenset[str]]:
    """``(one planted value per DECLARED request key, the store-only field names)`` when no slot is fetched.

    The request keys are every ``agent_context.fields`` entry of ``plurai.config.yaml`` whose
    ``inject`` includes ``request`` (or is absent: the default) and every ``.document_fields[].field``,
    in the shape the backend folds it. A store-only field rides the seeded world, so it is never planted.
    """
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception:  # noqa: BLE001 — no reader, nothing declared to plant
        return {}, frozenset()
    planted: dict = {}
    store_only: set[str] = set()
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            try:
                document = yaml.safe_load((root / relpath).read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001 — absent or unreadable: verify_config owns its shape
                continue
            fields = ((document or {}).get('agent_context') or {}).get('fields') if isinstance(document, dict) else None
            for name, spec in (fields.items() if isinstance(fields, dict) else ()):
                inject = spec.get('inject') if isinstance(spec, dict) else None
                channels = {inject} if isinstance(inject, str) else set(inject) if isinstance(inject, list) else {'request'}
                if 'request' in channels:
                    kind = str(spec.get('type') or '').lower() if isinstance(spec, dict) else ''
                    planted[str(name)] = next((make() for k, make in _TYPED_PLANTS if kind.startswith(k)), f'plurbo-smoke-{name}')
                else:
                    store_only.add(str(name))
    return planted, frozenset(store_only - set(planted))


#: The reference code a planted TEXT document carries, one per `.document_fields` entry (`QUARTZ-17`, `QUARTZ-18`, …):
#: the turn smoke asks the agent to quote it, so a reply carrying it proves the document reached the agent. Never
#: `_PROBE_FILE_MARKER`, so the file-part probe's echo can never satisfy it.
_DOCUMENT_TOKEN = 'QUARTZ-{n}'
#: Formats whose plant carries no readable token (the backend folds none of them as plain text), so delivery is not measured.
_OFFICE_DOCUMENT_FORMATS = frozenset({'docx', 'xlsx', 'pptx'})
#: The office formats' media types, as the backend's `AUTHORED_FORMATS` declares them.
_OFFICE_MEDIA_TYPES = {
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF 1.4 that shows ``text`` (ASCII; PDF string specials escaped), with a byte-exact xref table."""
    escaped = text.encode('ascii', 'replace').replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
    stream = b'BT /F1 12 Tf 72 720 Td (' + escaped + b') Tj ET'
    objects = (
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
    )
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return bytes(out)


class DocumentPlant(NamedTuple):
    """One `.document_fields` entry's `file` part for the opener's turn; ``token`` is ``None`` for a binary or office format."""

    field: str
    value: dict
    format: str
    token: str | None


def _document_plants(simulate_path: Path) -> tuple[DocumentPlant, ...]:
    """One plant per `.document_fields` entry, in the ONE shape the backend hands a document over in: a `file`
    content part `{'filename': '<field>.<ext>', 'file_data': 'data:<mime>;base64,…'}`, ridden on the OPENER's turn —
    never an `agent_context` value. A text format's document carries its own reference code."""
    import base64  # noqa: PLC0415 — this builder is the only caller

    try:
        entries = json.loads((simulate_path.parent / '.document_fields').read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001 — no document field declared
        entries = []
    plants: list[DocumentPlant] = []
    for index, entry in enumerate(entries if isinstance(entries, list) else ()):
        name = str(entry.get('field') or '') if isinstance(entry, dict) else ''
        if not name:
            continue
        marker = f'Plurbo smoke document for {name}.'
        formats = entry.get('formats') if isinstance(entry.get('formats'), list) and entry.get('formats') else [entry.get('format') or 'txt']
        fmt = str(formats[0]).lower()
        mime = _DOCUMENT_MEDIA_TYPES.get(fmt)
        token = None if mime or fmt in _OFFICE_DOCUMENT_FORMATS else _DOCUMENT_TOKEN.format(n=17 + index)
        body = f'{marker} Its reference code is {token}.' if token else marker
        if mime:
            extension, media = ('png' if mime == 'image/png' else fmt), mime
        else:
            extension = fmt
            media = _OFFICE_MEDIA_TYPES.get(fmt) or mimetypes.guess_type(f'document.{fmt}')[0] or 'text/plain'
        if media == 'image/png':
            # A real PNG: a model provider decodes an image part, and text bytes labelled image/png are refused
            # (Gemini answered 400 "Unable to process input image" and the smoke failed on its own plant).
            uri = _PROBE_IMAGE_DATA_URI
        elif media == 'application/pdf':
            # A real PDF, for the same reason (Bedrock answered 400 "The PDF specified was not valid").
            uri = f'data:{media};base64,{base64.b64encode(_minimal_pdf(body)).decode("ascii")}'
        else:
            uri = f'data:{media}{";charset=utf-8" if token else ""};base64,{base64.b64encode(body.encode("utf-8")).decode("ascii")}'
        plants.append(DocumentPlant(name, {'type': 'file', 'file': {'filename': f'{name}.{extension}', 'file_data': uri}}, fmt, token))
    return tuple(plants)


def _declared_render_surface(simulate_path: Path) -> RenderSurface:
    """The verbs ``plurai.config.yaml`` declares the agent renders.

    Read through the SDK's own reader so the key has ONE meaning across the toolchain. A read that
    FAILS still degrades to no verbs — this is a smoke test, not the gate: the plugin's
    ``verify_config.py`` owns the key's shape, and a broken declaration must not turn a working
    setup into a FAILED one. It does not degrade to silence, though: ``unreadable`` carries the
    reason, and under ``--turn`` it is a NO VERDICT (exit 2), because the probe it silently skips
    is the only live gate a mockless bench has. "Could not read the surface" is not "no surface".
    """
    try:
        from plurai.render_actions import discover_render_surface  # noqa: PLC0415 — optional on an older SDK

        surface = discover_render_surface([simulate_path.parent, simulate_path.parent.parent])
    except Exception as exc:  # noqa: BLE001 — an unreadable declaration is reported, not swallowed
        return RenderSurface((), f'{type(exc).__name__}: {exc}')
    return RenderSurface(tuple(surface.verbs) if surface is not None else ())


#: The part kind a generated scenario hands a document over as; `verify_config._HANDOVER_PART_KIND`, mirrored.
_HANDOVER_PART_KIND = 'file'


def _hands_over_documents(simulate_path: Path) -> bool:
    """Whether a `.document_fields` entry sits beside ``simulate_path``: scenario generation authors documents and hands them over."""
    try:
        entries = json.loads((simulate_path.parent / '.document_fields').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(entries, list) and bool(entries)


def _declared_io(simulate_path: Path) -> AgentIO:
    """The input surface this run probes: the `agent.io:` declaration plus what a document handover needs.

    A scenario set that hands over documents sends them as `file` parts. The kind is added to the
    probed surface when the config does not declare it, and `undeclared_handover` carries it so the
    smoke reports the missing declaration instead of sending a turn the SDK would refuse. A pipeline
    bench has no turn to carry a part and an unreadable declaration stays unreadable: neither is
    given the extra kind.
    """
    io = _read_agent_io(simulate_path)
    if io.unreadable or _HANDOVER_PART_KIND in io.input_parts or not _hands_over_documents(simulate_path):
        return io
    if io.pipeline_shape:
        return io
    base = io.input_parts or _DEFAULT_INPUT_PARTS
    return io._replace(input_parts=(*base, _HANDOVER_PART_KIND), undeclared_handover=(_HANDOVER_PART_KIND,))


def _read_agent_io(simulate_path: Path) -> AgentIO:
    """Read `agent.io:` off the workspace config, at the two rungs every declaration is read at.

    Read HERE rather than through the SDK, unlike ``_declared_render_surface``. The reason is not
    taste: this declaration's shape is gated by the plugin's own ``verify_config.py``, and —
    decisively — an SDK build
    that predates the reader must still get its declaration PROBED. Routing through an absent
    reader would have skipped every probe on exactly the builds where the empty opener is not yet
    survivable, and reported ✓. Whether the SDK can CARRY a declared part is answered by the probe
    itself, not by an import check.

    A config that exists and cannot testify is ``unreadable`` with the reason, never silence, and
    two configs that declare different things are a conflict rather than a precedence puzzle —
    the same fail-closed posture the SDK's own discovery takes.
    """
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception as exc:  # noqa: BLE001 — pragma: no cover — pyyaml is a CI dependency
        return AgentIO(unreadable=f'pyyaml is not importable ({type(exc).__name__}: {exc})')

    seen: set[Path] = set()
    declared: list[AgentIO] = []
    pipeline = False
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            path = root / relpath
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                document = yaml.safe_load(path.read_text(encoding='utf-8'))
            except Exception as exc:  # noqa: BLE001 — an unreadable config is reported, not swallowed
                return AgentIO(unreadable=f'{path} could not be read ({type(exc).__name__}: {exc})')
            if isinstance(document, dict) and str(document.get('shape') or '').strip().lower() == 'pipeline':
                pipeline = True
            if not isinstance(document, dict) or _AGENT_IO_KEY not in document:
                continue
            # A BARE `agent.io:` is `{}` — the defaults, declared — because that is what the
            # consumer does with it (`plurai.agent_io.parse_agent_io`: `if raw is None: raw = {}`).
            # Reading YAML's `None` as "unreadable" gave exit 2 / no verdict on a bench that runs.
            block = document[_AGENT_IO_KEY]
            if block is None:
                block = {}
            if not isinstance(block, dict):
                return AgentIO(unreadable=f'{path} declares `{_AGENT_IO_KEY}` as {type(block).__name__}, not a mapping')
            if 'input' not in block:
                # An ABSENT `input:` is the default surface, not an empty one — `input: []` is the
                # empty one, and the two are different declarations.
                parts_tuple = _DEFAULT_INPUT_PARTS
            else:
                parts = block['input']
                if not isinstance(parts, list) or any(not isinstance(kind, str) for kind in parts):
                    return AgentIO(unreadable=f'{path} declares `{_AGENT_IO_KEY}.input` as {type(parts).__name__}, not a list of part names')
                parts_tuple = tuple(k.strip() for k in parts if k.strip())
            may_open = block.get('may_open', False)
            if not isinstance(may_open, bool):
                return AgentIO(unreadable=f'{path} declares `{_AGENT_IO_KEY}.may_open` as {may_open!r}, not a boolean')
            # `opens: always` implies `may_open`, as the SDK's reader has it: every scenario opens empty.
            opens = block.get('opens') == 'always'
            declared_channels = block.get('channels')
            channels = tuple(
                c for c in (declared_channels if isinstance(declared_channels, list) else [])
                if isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k].strip() for k in ('name', 'kind', 'field')) and c['kind'] in _CHANNEL_PROBES
            )
            declared.append(AgentIO(parts_tuple, may_open or opens, str(path), opener_key='opens: always' if opens else 'may_open: true', channels=channels))
    if not declared:
        return AgentIO(pipeline_shape=pipeline)
    if len({(frozenset(d.input_parts), d.may_open) for d in declared}) > 1:
        sources = '; '.join(f'{d.source} → input={list(d.input_parts)} may_open={d.may_open}' for d in declared)
        return AgentIO(unreadable=f'conflicting `{_AGENT_IO_KEY}` declarations ({sources})')
    return declared[0]._replace(pipeline_shape=pipeline)


def _file_part() -> dict[str, object]:
    """A document handed to the turn, inline — the OpenAI ``file`` content part."""
    import base64  # noqa: PLC0415 — this builder is the only caller

    return {
        'type': 'file',
        'file': {
            'filename': 'plurbo-smoke.txt',
            'file_data': 'data:text/plain;base64,' + base64.b64encode(_PROBE_FILE_BODY.encode('utf-8')).decode('ascii'),
        },
    }


#: DECLARED KIND -> the content part the probe sends for it. A TABLE, deliberately, and at module
#: scope so a test can take an entry away and watch the kind get refused: the declared word either
#: selects a builder or selects nothing. A chain of `if 'file' in kind` would resolve `file_v2` to
#: the file part and certify a bench against a shape it was never handed — the fall-through this
#: shape exists to make impossible. Every part is SELF-CONTAINED (a data: URI, no host to reach),
#: because a probe that needs the network measures the network.
_PROBE_PART_BUILDERS: dict[str, Callable[[], dict[str, object]]] = {
    'text': lambda: {'type': 'text', 'text': _PROBE_PART_TEXT},
    'image_url': lambda: {'type': 'image_url', 'image_url': {'url': _PROBE_IMAGE_DATA_URI}},
    'file': _file_part,
}

#: KIND -> the token a harness that actually READ the part would echo. Only some parts can carry
#: one: a marker is a fact hidden in the payload, and a 1x1 image has nowhere to hide one. Where
#: there IS a marker, an echo is the difference between "the part reached invoke_agent" and "the
#: agent read it" — a harness that silently drops a declared part is otherwise indistinguishable
#: from one that handled it, which is why the probe prompt asks the agent to say what it got.
_PROBE_PART_MARKERS: dict[str, str] = {'file': _PROBE_FILE_MARKER}


#: CHANNEL KIND -> what the smoke plants through a declared `agent.io.channels` entry (C1), as the scenario's
#: `turn_inputs` value or `between_turn_actions` mutation. A table for `_PROBE_PART_BUILDERS`' reason: a kind selects a
#: plant or selects nothing. File items and created rows carry `_entity_type`, which the SDK requires of a new row.
_PROBE_ENTITY_TYPE = 'plurbo_smoke'
_CHANNEL_PROBES: dict[str, Callable[[dict], dict[str, object]]] = {
    'file_by_reference': lambda ch: {'value': {'entity_id': 'plurbo-smoke-file', '_entity_type': _PROBE_ENTITY_TYPE,
                                               'filename': 'plurbo-smoke.txt', 'content': _PROBE_FILE_BODY}},
    'structured_context': lambda ch: {'value': {'plurbo_probe': _PROBE_FILE_MARKER}},
    'host_injected_message': lambda ch: {'value': {'role': 'system', 'content': f'Host note for this turn: {_PROBE_FILE_MARKER}.'}},
    'between_turn_action': lambda ch: {'mutation': {'create': {'_entity_type': _PROBE_ENTITY_TYPE, 'plurbo_probe': _PROBE_FILE_MARKER}}},
}


class ChannelTurn(NamedTuple):
    """One channel-probe turn: the channels probed on it, and the mode-selector channels pinned to a production task
    type (``{channel: value}``) so an input reachable only on that type is probed ON it."""

    channels: tuple[dict, ...]
    pins: dict[str, str]


def _selector_needs(simulate_path: Path, channels: Iterable[dict]) -> tuple[dict[str, tuple[str, str]], dict[str, list[str]]]:
    """``(needs, values)`` from the ``bench_manifest.json`` beside simulate.py: each declared channel an input reachable
    only on one production task type is modelled by -> ``(selector channel, value)``, and each selector channel -> its
    production values (C3 ``switches[kind=selector]``, `agent_inventory.py`). Absent or unreadable is ``({}, {})``."""
    try:
        doc = json.loads((simulate_path.parent / _MANIFEST_FILENAME).read_text(encoding='utf-8'))
        switches = doc['surfaces']['production_tools'].get('switches') or []
        inputs = doc['surfaces'].get('inputs') or []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}, {}
    declared = {c['name']: c for c in channels}

    def channel_of(name: str) -> str | None:
        """The declared channel modelling input ``name``: its entry's `channel`, else the one named or fielded so."""
        named = next((str(e['channel']) for e in inputs if isinstance(e, dict) and e.get('name') == name and e.get('channel')), None)
        if named is not None:
            return named if named in declared else None
        return next((c for c, ch in declared.items() if name in (c, ch['field'])), None)

    needs: dict[str, tuple[str, str]] = {}
    values: dict[str, list[str]] = {}
    for switch in (s for s in switches if isinstance(s, dict) and s.get('kind') == 'selector'):
        selector = channel_of(str(switch.get('field')))
        if selector is None:
            continue
        values[selector] = [str(v.get('value')) for v in switch.get('values') or [] if isinstance(v, dict)]
        for value, names in (switch.get('selects') or {}).items():
            for name in names if value in values[selector] else ():
                if (channel := channel_of(str(name))) is not None and channel != selector:
                    needs.setdefault(channel, (selector, value))
    return needs, values


def _channel_turns(channels: Iterable[dict], needs: dict[str, tuple[str, str]], values: dict[str, list[str]]) -> list[ChannelTurn]:
    """The probe turns: every channel reachable on any task type together, then one turn per production task type a
    channel is reachable only on, its selector pinned to that type. A selector no channel needs is pinned to a
    production value on the first turn — never sent a probe payload, which no host would hand the agent."""
    groups: dict[tuple[str, str] | None, list[dict]] = {None: []}
    for channel in channels:
        if channel['name'] not in values:
            groups.setdefault(needs.get(channel['name']), []).append(channel)
    used = {key[0] for key in groups if key is not None}
    turns = [ChannelTurn(tuple(groups.pop(None)), {s: v[0] for s, v in values.items() if s not in used and v})]
    turns += [ChannelTurn(tuple(members), {key[0]: key[1]}) for key, members in groups.items() if key is not None]
    return [t for t in turns if t.channels or t.pins]


def _channel_plan(turns: list[ChannelTurn], turn: int) -> dict[str, list[dict]]:
    """The scenario-metadata lists that plant each probe turn's channels from ``turn`` (0-based) on: a per-turn input on
    its turn, a between-turn action after the turn before it, a pinned selector as that turn's input."""
    plan: dict[str, list[dict]] = {'turn_inputs': [], 'between_turn_actions': []}
    for at, probe_turn in enumerate(turns, start=turn):
        for channel in probe_turn.channels:
            probe = _CHANNEL_PROBES[channel['kind']](channel)
            if 'mutation' in probe:
                plan['between_turn_actions'].append({'after_turn': at - 1, 'channel': channel['name'], **probe})
            else:
                plan['turn_inputs'].append({'turn': at, 'channel': channel['name'], **probe})
        plan['turn_inputs'] += [{'turn': at, 'channel': name, 'value': value} for name, value in probe_turn.pins.items()]
    return {key: rows for key, rows in plan.items() if rows}


def _probe_part(kind: str) -> dict[str, object]:
    """The message content part the smoke sends to confirm a declared ``kind``.

    Raises :class:`KeyError` — naming the kind — when the table has no entry for it. The caller
    turns that into a NO VERDICT that names the kind, never into a text turn.
    """
    return _PROBE_PART_BUILDERS[kind]()


def _sdk_carriable_kinds() -> frozenset[str] | None:
    """The content-part kinds the INSTALLED plurai can put on a turn — or ``None`` when this build
    has no such reader and only the probe itself can find out.

    Read through the SDK's own constant (`plurai.agent_io.INPUT_PART_KINDS`), which is the
    version-free way to ask "can this build carry it?" — the equivalent of the
    ``hasattr(reply, 'agent_actions')`` question the render probe asks, and for the same reason: a
    declaration this build cannot carry is a fact about the build, not a finding about the bench,
    and sending the turn anyway would blame the harness for our own gap.

    ``None`` — not an empty set. An older SDK with no `agent_io` module has not said it carries
    nothing; it has said nothing, and the probe still runs, with `_probe_owner` attributing
    whatever comes back.
    """
    try:
        from plurai.agent_io import INPUT_PART_KINDS  # noqa: PLC0415 — optional on an older SDK

        return frozenset(INPUT_PART_KINDS)
    except Exception:  # noqa: BLE001 — an SDK with no reader is a state, not a failure
        return None


#: The SDK exception that means the child DIED rather than reported a fault — `plurai._protocol`'s
#: `HarnessChildExited`. Restated rather than imported, and pinned to the SDK by `tests/test_smoke_io_probe.py`
#: so a rename over there reds here instead of silently re-attributing every child death.
_SDK_CHILD_EXIT_ERROR = 'HarnessChildExited'


def _sdk_child_exit_error_name() -> str | None:
    """The INSTALLED plurai's own name for the child-death error — or ``None`` when no SDK is
    importable and only the restated constant above can answer.

    Read through the SDK rather than matched against our copy, for `_sdk_carriable_kinds`' reason
    one screen up: `_probe_owner` decides whether the customer is told *"your harness is broken"*
    or *"this is our toolchain"*, and a renamed class silently sends every child death to the
    default — which is `toolchain`, so a genuine harness ImportError would be reported as a
    plugin bug with nothing saying the attribution was guessed.
    """
    try:
        from plurai import _protocol  # noqa: PLC0415 — optional; absent on a bench with no SDK
    except Exception:  # noqa: BLE001 — an SDK that is not here is a state, not a failure
        return None
    found = getattr(_protocol, _SDK_CHILD_EXIT_ERROR, None)
    return getattr(found, '__name__', None)


#: The SDK's owner rule over a raise's typed ``kind`` (`plurai._protocol.is_harness_owned`), by NAME:
#: asked, never restated, so the smoke and the runner cannot disagree about who owns a raise.
_SDK_HARNESS_OWNER_RULE = 'is_harness_owned'
#: The smoke's exit when the AGENT raised (its typed class is not a harness kind). `bench_manifest.py`
#: records it `failed` with `owner: agent`; exit 1 stays the harness's.
AGENT_RAISED_EXIT = 3
#: The smoke's exit when a turn came back silent and nothing held: the agent, the harness or the SDK may own
#: it, and the smoke cannot tell which. `bench_manifest.py` records it `failed` with `owner: undetermined`.
OWNER_UNDETERMINED_EXIT = 4

#: The failures the run measures rather than refuses on, by the exit that names their owner once nothing else
#: holds: the agent's own raise (the SDK's owner rule decided it), or a silent turn no owner was found for.
_RUN_MEASURED: dict[SmokeGate, int] = {
    SmokeGate.AGENT_RAISED: AGENT_RAISED_EXIT,
    SmokeGate.TURN_SILENT: OWNER_UNDETERMINED_EXIT,
    SmokeGate.RENDER_SILENT: OWNER_UNDETERMINED_EXIT,
    SmokeGate.MAY_OPEN_SILENT: OWNER_UNDETERMINED_EXIT,
}


#: The SDK's kind for a provider that refused the agent's own call (`plurai._protocol.PROVIDER_INFRA_KIND`), by
#: NAME: the infrastructure's, never the agent's and never the harness's, so the smoke reaches no verdict.
_SDK_PROVIDER_INFRA_KIND = 'PROVIDER_INFRA_KIND'


def _raise_owner(exc: BaseException) -> str | None:
    """``'harness'`` / ``'infra'`` / ``'agent'`` by the installed SDK's own rule, or ``None`` on an SDK without it."""
    try:
        from plurai import _protocol  # noqa: PLC0415 — optional; absent on a bench with no SDK
    except Exception:  # noqa: BLE001 — an SDK that is not here is a state, not a failure
        return None
    rule = getattr(_protocol, _SDK_HARNESS_OWNER_RULE, None)
    if not callable(rule):
        return None
    kind = getattr(type(exc), 'kind', None)
    infra = getattr(_protocol, _SDK_PROVIDER_INFRA_KIND, None)
    if infra is not None and kind == infra:
        return 'infra'
    return 'harness' if rule(kind) else 'agent'


#: The header of a traceback the child printed (CPython's own rendering), and one of its frames.
_TB_HEADER = 'Traceback (most recent call last):'
_TB_FRAME_RE = re.compile(r'^\s*File "(?P<path>[^"]+)", line \d+, in ')
#: A frame in an interpreter's library tree (the stdlib, site-/dist-packages): it raised on its caller's behalf.
_LIBRARY_FRAME_RE = re.compile(r'[\\/](?:site-packages|dist-packages)[\\/]|[\\/]lib[\\/]python\d+(?:\.\d+)?[\\/]', re.IGNORECASE)
_IDENTIFIER_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
#: The prefix the SDK's stderr tee puts on every child line it logs (`plurai._subprocess._read_stderr`).
_CHILD_LINE_PREFIX = '[plurai.child] '


def _own_traceback_frames(stderr_lines: Iterable[str], exc_class: str) -> list[str] | None:
    """The frame paths, outermost first, of the LAST traceback the child printed — iff that traceback's
    summary line is the reported raise's own class; ``None`` when the child printed no such traceback."""
    lines = [str(line).removeprefix(_CHILD_LINE_PREFIX) for line in stderr_lines]
    starts = [i for i, line in enumerate(lines) if line.strip() == _TB_HEADER]
    if not starts:
        return None
    frames: list[str] = []
    for line in lines[starts[-1] + 1 :]:
        if found := _TB_FRAME_RE.match(line):
            frames.append(found['path'])
        elif line.strip() and not line[:1].isspace():
            head = line.split(':', 1)[0].strip()
            return frames if frames and head.rsplit('.', 1)[-1] == exc_class.rsplit('.', 1)[-1] else None
    return None


def _harness_classes(simulate_path: Path) -> frozenset[str]:
    """Every class the generated harness defines: simulate.py and the local helpers it imports."""
    names: set[str] = set()
    for path in _harness_files(simulate_path):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, ValueError):
            continue
        names.update(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return frozenset(names)


def _harness_owned_raise(exc: BaseException, stderr_lines: Iterable[str], simulate_path: Path) -> str | None:
    """Why the GENERATED HARNESS owns a raise the SDK typed the agent's — or ``None`` when nothing says so.

    The SDK reads only the raise's innermost frames, and its pipeline path reads none, so two harness
    defects reach the smoke typed as the agent's own:

    * the innermost frame outside the stdlib, the installed libraries and the SDK is in the harness's
      directory (``simulate.py`` or a helper beside it), read off the traceback the child printed for
      this raise — the frames that called INTO the harness (the outer ``invoke_*`` hop) never count;
    * the raise names a class a harness file defines: a harness class rebound into an agent module runs
      in the agent's frames, so a library raising over that class's own definition (pydantic's
      ``class-not-fully-defined`` over a forward reference) shows no harness frame at all.
    """
    exc_class = str(getattr(exc, 'child_exc_class', None) or '')
    harness_dir = os.path.realpath(simulate_path.parent)
    frames = _own_traceback_frames(stderr_lines, exc_class) if exc_class else None
    for path in reversed(frames or []):
        if path.startswith('<'):
            continue  # <frozen importlib._bootstrap>, <string>: no file to own it
        resolved = os.path.realpath(path if os.path.isabs(path) else simulate_path.parent.parent / path)
        if resolved.startswith(harness_dir + os.sep):
            return f'its innermost own frame is in {Path(resolved).relative_to(harness_dir)}'
        if _LIBRARY_FRAME_RE.search(path) or 'plurai' in Path(path).parts[:-1]:
            continue
        break
    named = sorted(set(_IDENTIFIER_RE.findall(str(getattr(exc, 'child_message', None) or ''))) & _harness_classes(simulate_path))
    if named:
        return f'it names {", ".join(named)}, a class the harness defines'
    return None


def _attributed_owner(exc: BaseException, stderr_lines: Iterable[str], simulate_path: Path | None) -> tuple[str | None, str | None]:
    """``(owner, why)``: the SDK's owner rule (:func:`_raise_owner`), with an agent-typed raise the
    harness owns (:func:`_harness_owned_raise`) handed back to the harness, and why."""
    owner = _raise_owner(exc)
    if owner == 'agent' and simulate_path is not None and (why := _harness_owned_raise(exc, stderr_lines, simulate_path)):
        return 'harness', why
    return owner, None


def _provider_refused(exc_class: str, message: str) -> str:
    """The NO VERDICT hint for an `infra`-owned raise. Names no owner's raise: it is neither the agent's nor the harness's."""
    return (
        f"the agent's provider refused its call ({exc_class}: {message}) — infrastructure, not the agent or the "
        'harness: re-run once the provider recovers, or fix the key / model / sign-in it names'
    )


def _probe_owner(exc_class_name: str, message: str, stderr_text: str, simulate_path: Path) -> str:
    """``'bench'`` or ``'toolchain'`` — WHOSE failure a probe turn just hit.

    The split is the difference between exit 1 and exit 2, and getting it backwards is the
    failure this file's prose warns about twice already: naming the customer's simulate.py for a
    defect of ours sent an SDK bug into a customer's harness. The evidence, in order:

    * ``child reported …`` — the child CAUGHT this inside its dispatch loop and reported it as a
      typed error. Something the harness ran raised. That is the bench.
    * the child DIED (``HarnessChildExited`` / "exited with code N before responding") — ambiguous
      from the parent, so read the traceback it left on stderr: a TRACEBACK FRAME in the
      customer's own harness is the bench; a traceback with only ``plurai`` frames is ours (the
      empty opener escapes the child's dispatch ``try`` on an SDK build without the opener fix, and exits the process before anything is reported).
    * anything else — a refusal at the SDK boundary, a driver crash — is ours.

    A FRAME, not a mention. The buffer this reads is the SDK's own ``plurai._subprocess`` log, and
    the first thing that logger writes is ``[plurai] starting child: <simulate_path> pid=N``
    (``_subprocess.py`` ``start()``). Testing "is the path in the text" therefore answered YES for
    every death the moment the child was started, which made this arm say ``bench`` always and the
    no-verdict below unreachable in a real run — a defect of OURS filed against the customer's
    harness, on the exact turn this file's prose says never to do that. ``File "<path>"`` is the
    CPython traceback's own rendering and nothing else emits it.

    Helpers count: a harness split into ``_storage.py`` / ``_mocks.py`` beside simulate.py is a
    first-class shape here (``verify_config._discover_sources``), and a crash in one of those is
    the bench just as much.

    The DEFAULT is ``toolchain``, which is the conservative direction: a prerequisite reported as
    no-verdict costs a re-run, a defect of ours reported as a finding costs the customer time
    editing a harness that was right.
    """
    if 'child reported' in message:
        return 'bench'
    # The SDK's own name when it is importable, our restated copy when it is not — never the bare
    # literal, which is how a rename over there turns every child death into `toolchain`.
    child_exited = _sdk_child_exit_error_name() or _SDK_CHILD_EXIT_ERROR
    died = exc_class_name == child_exited or 'exited with code' in message or 'before responding' in message
    if died:
        corpus = message + '\n' + stderr_text
        frames = (f'File "{simulate_path}"', f'File "{simulate_path.parent}/')
        if any(frame in corpus for frame in frames):
            return 'bench'
    return 'toolchain'


class _EntryWitness:
    """The `agent.entry` witness this smoke writes to `--witness-out`: `{state, reason}`.

    Decided HERE, without asking the SDK, whenever the entry cannot be seen from the harness
    process: an `external` entry, a non-Python agent, or an installed SDK whose `ChildProcess.init`
    takes no `entry_witness`. Otherwise the ref is armed and the SDK's replies decide.
    """

    _RANK = {'witnessed': 0, 'not_witnessed': 1}

    def __init__(self, symbol: dict, language: str | None) -> None:
        self.ref: str | None = None
        self.decided: dict[str, str] | None = None
        self.observed: list[dict] = []
        if symbol.get('executes') != 'in_harness':
            self.decided = self._blind(f'the entry runs outside the harness interpreter: {symbol.get("why") or "no reason declared"}')
        elif language != 'python':
            self.decided = self._blind(f'a {language or "undeclared-language"} agent cannot be observed from the Python harness')
        else:
            self.ref = str(symbol.get('ref') or '')

    @staticmethod
    def _blind(reason: str) -> dict[str, str]:
        return {'state': 'not_determinable', 'reason': reason}

    def init_kwargs(self, init: Callable[..., object]) -> dict[str, str]:
        """`{'entry_witness': ref}` for an SDK that takes it; `{}` (and decided) for one that does not."""
        if self.ref is None:
            return {}
        if 'entry_witness' not in inspect.signature(init).parameters:
            self.decided, self.ref = self._blind('the installed SDK predates the entry witness'), None
            return {}
        return {'entry_witness': self.ref}

    def observe(self, reply: Any) -> Any:
        found = getattr(reply, 'entry_witness', None)
        if self.ref is not None and isinstance(found, dict):
            self.observed.append(found)
        return reply

    def outcome(self) -> dict[str, str]:
        if self.decided is not None:
            return self.decided
        if not self.observed:
            return self._blind('the smoke returned no witness')
        return min(self.observed, key=lambda w: self._RANK.get(str(w.get('state')), 2))


def _turn_delivered_something(reply: object) -> bool:
    """Did this turn deliver ANYTHING — speech, OR an action?

    An agent whose output is a typed action list legitimately acts WITHOUT speaking. Testing
    ``ai_text`` alone failed such an agent's very first turn as "the agent returned an empty
    reply", and told the customer to fix their agent for a turn their agent had performed
    correctly. ``agent_actions`` is the SDK's typed channel for the same turn; an SDK
    without it has no such attribute and the reply text alone is read.
    """
    if str(getattr(reply, 'ai_text', '') or '').strip():
        return True
    return bool(getattr(reply, 'agent_actions', None))


#: Set to ``1`` to drive the render probe turn again. Off by default: the two conversational turns stay, and a
#: render channel nothing can observe is found by the qualification run instead (one global setting).
RENDER_PROBE_ENV = 'PLURAI_SMOKE_RENDER_PROBE'


def render_probe_enabled() -> bool:
    """Whether the ``--turn`` smoke drives the render probe turn (:data:`RENDER_PROBE_ENV` is ``1``)."""
    return os.environ.get(RENDER_PROBE_ENV, '').strip() == '1'


def _empty_first_turn_message(render_verbs: tuple[str, ...]) -> str:
    """What to say when turn 1 delivered neither speech nor an action.

    Deliberately does NOT open with "fix simulate.py". The same symptom has three very different
    owners, and naming only the customer's file sent an SDK defect into the customer's harness
    once already: an agent that really said nothing, a harness returning its reply somewhere the
    SDK does not read, and the SDK itself failing to read a shape the harness does return. The
    message routes on the evidence the verifier actually has.
    """
    lines = [
        '✗ turn smoke: the agent delivered neither speech nor an action on turn 1.',
        '  Three different things look like this — check them in this order:',
        '  1. THE AGENT said nothing. Call invoke_agent directly with the same payload; if it',
        '     returns nothing readable either, the agent is the answer and simulate.py is fine.',
        '  2. THE HARNESS returns its reply where the SDK does not read it: invoke_agent must',
        '     return the message list under `messages` (or a plain string), not a bare state',
        '     dict whose reply lives under some other key.',
        '  3. THE SDK could not read a shape invoke_agent DOES return. If invoke_agent returns a',
        '     message list and the reply is visibly in it, this is an SDK defect — file it',
        '     against plurai-sdk (surface: sdk); do not rewrite the harness around it.',
    ]
    if render_verbs:
        lines += [
            f'  NOTE: plurai.config.yaml declares a render surface ({", ".join(render_verbs)}), so this',
            '  agent may answer by RENDERING rather than by speaking. A render-only turn is a real',
            '  turn: the SDK carries it as `agent_actions` beside an empty reply. If your SDK has no',
            '  `agent_actions` on its turn envelope it is too old — upgrade it rather than',
            '  painting the visuals into the reply text, which manufactures words the agent never said.',
        ]
    return '\n'.join(lines)


def _standalone_last_line(message: str, recap: str) -> str:
    """``message`` with ``recap`` as its final line, so the TAIL can be read on its own.

    The consumer of every verdict here reads one line of it. ``bench_manifest.execute`` records
    ``tail[-1][:400]`` as the check's evidence — that is what lands in ``verification.json``, in
    the run line an operator scans, and in the hand-off. Every multi-line diagnosis in this file
    ends mid-list, so what was recorded for a real failed render probe was:

        failed  smoke_turn  ( painting the visuals into the reply text, which manufactures
        words the agent never said.)

    — the last clause of a numbered NOTE: no subject, no verdict, no bench, and a leading indent
    where a sentence's start should be, sitting exactly where a reader looks for the cause. The
    fix is not to shorten the diagnosis (the operator reading the terminal needs all of it) but
    to make sure whatever ends it says what happened. A single-line message is already its own
    tail and is returned untouched.
    """
    return message if len(message.strip().splitlines()) < 2 else f'{message}\n{recap}'


def _read_parts_delivered(recorded: list[str] | None, measured: list[SmokeFailure], notes: list[str]) -> list[SmokeFailure]:
    """``measured`` without the quote check's failure for each document whose `file` part the harness READ.

    The SDK's read record (``unread_parts``) is the delivery measure: an agent that stores an upload as records
    (a dataset, a world row) never quotes its reference code back, and that is not a document left behind. The
    quote check stands only when the part went unread, or when the SDK keeps no read record (``None``).
    """
    if recorded is None:
        return measured
    unread = set(recorded)
    kept: list[SmokeFailure] = []
    for failure in measured:
        field = failure.observed.get('field') if failure.gate == SmokeGate.DOCUMENT_UNDELIVERED else None
        if field is not None and str(field) not in unread:
            notes.append(f'✓ document delivery: the harness read the `{field}` part it was handed (the agent did not quote it).')
            continue
        kept.append(failure)
    return kept


def _unread_parts(recorded: list[str] | None, measured: list[SmokeFailure], *, file: str) -> SmokeFailure | None:
    """The document-delivery failure for the handed-over ``file`` parts the harness never read (the SDK's
    ``FinalizeResponse.unread_parts``, by field), or ``None`` when it read every one, the SDK records none, or the
    token check already named each of them. A part is never a request key: its remedy is the turn's parts."""
    named = {str(f.observed.get('field')) for f in measured if f.gate == SmokeGate.DOCUMENT_UNDELIVERED}
    unread = sorted(set(recorded or ()) - named)
    if not unread:
        return None
    message = (
        f'✗ document delivery: the harness never read the `file` part(s) handed over for {", ".join(f"`{u}`" for u in unread)} — every '
        f'document the user hands over arrives as a `file` part on the turn she hands it over, the opener included. Read '
        f'every turn with `turn_documents(content)` (`references/document_input.py`) and hand the agent each Document.'
    )
    return SmokeFailure(SmokeGate.DOCUMENT_UNDELIVERED, message, file, {'fields': ', '.join(unread)})


def _input_delivery(
    simulate_path: Path, keys: Iterable[object], recorded: list[str] | None, store_only: frozenset[str], *, lane: str, preempted: bool,
    documents: frozenset[str] = frozenset(),
) -> tuple[str, str | None]:
    """``(note, failing recap or None)``: the ONE statement of whether the harness read its request keys.

    ``recorded`` is the child's read record (``ChildProcess.unread_inputs`` after ``finalize``). A key
    still unread when the pass RAISED was never given its chance, so that is ``not_run``, never ✗.
    ``documents`` are the planted document fields: a read (a copy reads every key) is not delivery, so the ✓ line
    never names one — the document delivery line speaks for it.
    """
    unread = sorted(set(recorded or ()) - store_only)
    if recorded is None:
        return ('⚠ input delivery: no read record (this plurai build keeps none, or a mock handler can serve the keys on '
                'demand), so an injected key the harness drops is not reported.', None)
    if unread and preempted:
        return (f'~ input delivery: not_run — the pass raised before the harness read {", ".join(unread)}.', None)
    if unread:
        remedy = '; '.join(f'serve `{key}` from `seed_state` param 1 or {lane}' for key in unread)
        return ('\n'.join([
            f'✗ input delivery: {simulate_path.name} never read {", ".join(unread)}: {remedy}.',
            '  A read made only through a C serializer (orjson, ujson, msgspec, rapidjson) or in a forked child is not seen: '
            'read the key in Python (`initial_agent_context.get(...)` / `payload[...]`) before handing it on.',
        ]), f'✗ input delivery: the harness never read {", ".join(unread)} — {remedy}.')
    if documents:
        read = sorted(set(map(str, keys)) - documents)
        if not read:
            return ('~ input delivery: every request key is a planted document — the document delivery line speaks for it.', None)
        return (f'✓ input delivery: the harness read every non-document request key ({", ".join(read)}).', None)
    return (f'✓ input delivery: the harness read every request key ({", ".join(sorted(map(str, keys)))}).', None)


async def _run_smoke(simulate_path: Path, *, drive_turn: bool = False, witness: _EntryWitness | None = None) -> tuple[int, str]:
    """Drive a single Init exchange. Returns ``(exit_code, summary)``.

    Uses :class:`plurai._subprocess.ChildProcess.init` — the same call
    plurai-run-simulation's runner uses. Three failure paths get
    classified into actionable hints:

    - ``OrchestratorAdapterError``: child responded with InitError;
      the exception message carries the customer's traceback verbatim.
    - ``RuntimeError`` with "exited with code N before responding to init":
      child crashed during simulate.py import (most common — usually a
      missing Phase 1 stub or package shadow).
    - ``RuntimeError`` with "Child wrote non-JSON to stdout": print()
      on the import path broke the IPC handshake.
    """
    # THE call site the SDK is needed at, and the only one. `main` turns an absent SDK into its
    # own exit-2 verdict; every other function in this file is pure and testable without it.
    from plurai._subprocess import (  # noqa: PLC0415 — deferred so the module imports without the SDK
        ChildProcess,
        OrchestratorAdapterError,
    )

    # What the config says this agent RENDERS. Read up front: it decides how an empty turn 1 is
    # explained, and whether the render probe below runs at all.
    surface = _declared_render_surface(simulate_path)
    render_verbs = surface.verbs
    render_probed = bool(render_verbs) and render_probe_enabled()
    # What the config says this agent can be HANDED. Read up front for the same reason: an
    # unreadable declaration is a fact about this bench whichever mode read it, and the probes
    # below are planned from it.
    io = _declared_io(simulate_path)
    notes: list[str] = []
    #: Failures the run measures (a silent turn) recorded on the way: every later probe still runs.
    measured: list[SmokeFailure] = []
    if surface.unreadable:
        # NOT silence. The probe below is skipped either way, and a reader has to be able to tell
        # "this agent renders nothing" from "nobody could read what it renders".
        notes.append(
            f'⚠ render surface: plurai.config.yaml\'s render declaration could not be READ '
            f'({surface.unreadable}), so this run cannot tell a bench that declares no render surface from one '
            f'whose declaration is unreadable. The render probe did not run.'
        )
    if io.unreadable:
        notes.append(
            f'⚠ input surface: plurai.config.yaml\'s `{_AGENT_IO_KEY}` declaration could not be READ '
            f'({io.unreadable}), so this run cannot tell a bench that declares nothing about its input from one '
            f'whose declaration is unreadable. No input probe was planned.'
        )
    elif io.source is not None:
        declared_parts = [kind for kind in io.input_parts if kind not in io.undeclared_handover]
        notes.append(
            f'✓ input surface: {io.source} declares input={declared_parts or "[]"}, '
            f'may_open={str(io.may_open).lower()}.'
        )
    if io.undeclared_handover:
        notes.append(
            f'⚠ input surface: a `.document_fields` entry has scenario generation hand this agent documents as '
            f'{" / ".join(f"`{k}`" for k in io.undeclared_handover)} parts, but `{_AGENT_IO_KEY}.input` does not declare '
            f'them, and the SDK fails such a scenario as `harness_undeclared_input`. Declare them '
            f'(verify_config reports `handover_kind_not_declared`).'
        )

    # The turn-1 `request` values, read before the child starts so the reason rides EVERY
    # verdict — including the failing ones, where "which envelope was this?" is the first
    # question asked of a child that died during init.
    request_block, store_only = _init_request_block(simulate_path)
    # The WORLD beside it. A fetched slot's block rides with that slot's own seeded world in every mode —
    # the pair a run Inits that slot with. Otherwise, under --turn, the first declared `agent.smoke_inputs`
    # entry, its inputs merged OVER the request block so a key the entry does not name is still delivered
    # (and still checked). Without either the store is `{}` — right for a bench that declares no world; a bench
    # that seeds one (mocked tools, or a harness-owned store) whose turn 1 opens a record by a request key fails in
    # it, and the failure line names this remedy.
    world: dict = request_block.world or {}
    world_remedy: str | None = None
    if request_block.world is not None:
        notes.append(f'✓ world: the same slot\'s initial_world_state ({len(world.get("entities") or {})} entities).')
    elif drive_turn:
        if (smoke := _smoke_envelope(simulate_path, request_block)) is not None:
            world, request_block = smoke
            notes.append(f'✓ world: agent.smoke_inputs[0] ({len(world.get("entities") or {})} entities).')
        elif _declares_seeded_world(simulate_path):
            world_remedy = (
                f'this bench seeds a world but declares no agent.smoke_inputs, so the turn ran in an EMPTY world — if turn 1 '
                f'opens a record by a request key, declare the minimal world it resolves in as agent.smoke_inputs '
                f'in {_MANIFEST_FILENAME}'
            )
    # The document plant always wins under --turn: a fetched slot or a smoke_inputs value can never hide the measurement.
    # The documents ride the OPENER as `file` parts, never the request block: a harness that reads a handed-over
    # document off the payload or `seed_state` is handed nothing here, exactly as in a scored run.
    documents = _document_plants(simulate_path) if drive_turn and _HANDOVER_PART_KIND in io.input_parts else ()
    tokens = {plant.field: plant.token for plant in documents if plant.token}
    if documents:
        # A fetched slot or `agent.smoke_inputs` value under a document field is withheld too, as a scored run withholds it.
        handed = {plant.field for plant in documents}
        request_block = request_block._replace(values={k: v for k, v in request_block.values.items() if k not in handed})
    notes.append(
        f'✓ request block: {", ".join(sorted(map(str, request_block.values)))} from {request_block.source}.'
        if request_block.values
        else f'⚠ request block: EMPTY — {request_block.source}. A key this agent cannot accept would not be reported.'
    )

    # Install a handler on the SDK's subprocess logger BEFORE starting the
    # child so we capture the child's stderr (which ChildProcess routes
    # there instead of exposing as an attribute).
    capture = _StderrCaptureHandler()
    capture.setLevel(logging.DEBUG)
    sdk_logger = logging.getLogger('plurai._subprocess')
    sdk_logger.addHandler(capture)
    sdk_logger.setLevel(logging.DEBUG)

    # The channel probe's turn follows every other probe turn; its plan rides Init, where the SDK reads a scenario's.
    channel_turn = 2 + render_probed + sum(k != 'text' and k not in io.undeclared_handover for k in io.input_parts) + io.may_open
    channel_turns = _channel_turns(io.channels, *_selector_needs(simulate_path, io.channels)) if drive_turn and io.channels and not io.unreadable else []
    channel_plan = _channel_plan(channel_turns, channel_turn)
    child = ChildProcess(simulate_path)
    witness_kwargs = witness.init_kwargs(ChildProcess.init) if witness is not None else {}
    records_session = False
    try:
        async with child:
            if witness_kwargs:
                # The witness is cumulative since Init, so every reply is observed and the last decides.
                driven = child.user_turn
                child.user_turn = lambda *a, **k: _observed(witness, driven(*a, **k))  # type: ignore[method-assign]
            await child.init(
                thread_id='smoke-test',
                mock_service_url='http://localhost:8199',
                # 'local' = in-process mock session. The default 'connect' PROBES the URL and
                # raises when nothing listens — and at this step nothing does (env-gen deploys
                # the mock later), so every smoke run failed as category=other and the SKILL's
                # stub-loop chased phantom modules. Smoke tests LOADING, not mock reachability.
                mock_mode='local',
                # Param 2 is the STORE: a fetched slot's own world, else the declared smoke world
                # under --turn, else `{}` (this step runs before env-gen deploys anything; on an
                # all-real bench the store stays empty for the whole run). Param 1 carries the
                # `inject: request` values — those arrive on every real turn 1, so an empty one
                # smokes an envelope no run sends.
                initial_world_state=world,
                initial_agent_context=request_block.values,
                scenario_metadata={'scenario_name': 'smoke', **channel_plan},
                **witness_kwargs,
            )
            if (raised := _world_read_raised(child, simulate_path)) is not None:
                notes.append(f'✗ world read: the world could not be read after seed_state: {raised.observed["read_error"]}')
                measured.append(raised)
            if drive_turn:
                # One REAL turn (costs one provider call — only run when the
                # provider key is present, i.e. the setup smoke). Init alone
                # cannot catch turn-1-only crashes: the async/sync invoke_agent
                # mismatch killed 3/3 scenarios of a live bench while the
                # Init-only smoke passed clean.
                # The tool clause gives turn 2 a value only a kept tool result can answer: one agent model step and one
                # tool run inside this turn, no added turn. Read only: the smoke never asks for a live write.
                opener = (
                    'Hello — my name is Plurbo Verifystate. Please greet me. Then use one of your tools to look something up '
                    "(read only, change nothing) and tell me which tool you used; no need to repeat what it returned."
                )
                if tokens:
                    opener += ' Also, what reference code is written in the document I shared with you?'
                # Turn 0 carries the handed-over documents as `file` parts beside her words, the shape a scenario that
                # hands one over with the opener sends; a later turn's part is the input probe below.
                opener_content = [{'type': 'text', 'text': opener}, *(plant.value for plant in documents)] if documents else opener
                reply = await child.user_turn(0, opener_content)
                if not _turn_delivered_something(reply):
                    silent = (
                        '✗ turn smoke: turn 1 delivered neither speech nor an action — one symptom, three possible '
                        'owners (the agent, the harness, the SDK), listed above in the order to check them.'
                    )
                    notes.append(_empty_first_turn_message(render_verbs))
                    measured.append(SmokeFailure(SmokeGate.TURN_SILENT, silent, simulate_path.name, {}))
                # SECOND turn — the statefulness probe. The payload contract
                # sends only the NEW user message per turn; a harness that rebuilds
                # history from payload['messages'] runs STATELESS, every turn sees
                # [system, user], and nothing else in the toolchain flags it (a full
                # qualification pass ran that way live: run 'completed', every
                # memory/journey/continuity verdict meaningless). A harness with
                # working thread-keyed history trivially answers this; a stateless
                # one cannot.
                said1 = str(getattr(reply, 'ai_text', '') or '')
                carry = _carry_values(_trail_returned_entries([getattr(reply, 'agent_state', None)]), said=f'{opener}\n{said1}')
                ask2 = 'What is the name I told you one message ago? Answer with just the name.'
                if carry is not None:
                    # The agent picks which returned entries to quote: an id its policy hides is never the one asked for.
                    ask2 = (
                        'What is the name I told you one message ago? Then, without calling any tool, quote two or three '
                        f'entries your `{carry.tool}` call returned, exactly as it returned them.'
                    )
                reply2 = await child.user_turn(1, ask2)
                records_session = any(_records_cli_session(getattr(r, 'agent_state', None)) for r in (reply, reply2))
                name_echoed = 'plurbo' in str(getattr(reply2, 'ai_text', '') or '').lower()
                _record_tool_carry(carry, reply2, simulate_path.name, notes, measured, name_echoed=name_echoed)
                if not name_echoed:
                    # WARN, never fail: this is a heuristic on free text, and it has real
                    # false positives — an agent replying in a non-Latin script (this same
                    # release makes non-English suites first-class), a guardrailed agent
                    # that declines off-task chit-chat, or a deliberately delta-shaped
                    # harness. A suspected defect must not block a setup that works.
                    #
                    # A NOTE on stdout, not a print to stderr. `bench_manifest.select_evidence`
                    # reads STDERR FIRST on a non-zero exit, so this advisory — written before
                    # the failing returns below — was recorded, truncated mid-sentence, as the
                    # evidence for a DIFFERENT check's failure. Nothing here is this run's
                    # verdict; the verdict is the summary's last line, and that is on stdout.
                    notes.append(
                        '⚠ turn smoke: STATELESS HARNESS suspected — turn 2 did not echo a name '
                        'given on turn 1. The SDK sends only the NEW user message per turn '
                        "(payload['messages'] carries that turn's user side only); a harness must persist history keyed on "
                        "config['configurable']['thread_id'] (LangGraph: compile with a checkpointer; "
                        'framework-free: keep a thread-keyed message store) — without it every '
                        'memory/continuity verdict in the suite is meaningless. This check is a '
                        'heuristic: a non-Latin-script reply, a guardrailed refusal, or a deliberately '
                        'delta-shaped agent can trip it too, so VERIFY before acting. Turn-2 reply: '
                        f'{str(getattr(reply2, "ai_text", ""))[:200]!r}'
                    )
                if tokens and _turn_delivered_something(reply):
                    # A silent turn 0 has no document verdict: TURN_SILENT already fails it.
                    said = f'{getattr(reply, "ai_text", "") or ""}\n{getattr(reply2, "ai_text", "") or ""}'.lower()
                    for field, token in tokens.items():
                        if token.lower() in said:
                            notes.append(f'✓ document delivery: the agent quoted the planted `{field}` document — it reached the agent.')
                            continue
                        undelivered = (
                            f'✗ document delivery: the `{field}` document handed over as a `file` part on the opener never reached '
                            f'the agent — it could not quote it when asked. Every handed-over document arrives as a `file` part on '
                            f'the turn she hands it over, the opener included, never as a payload key or `seed_state` value: read '
                            f'every turn with `turn_documents(content)` (`references/document_input.py`) and hand the agent each '
                            f'Document the way the product does (its upload channel, or a planted file named by path).'
                        )
                        measured.append(SmokeFailure(SmokeGate.DOCUMENT_UNDELIVERED, undelivered, simulate_path.name, {'field': field}))
                notes.extend(
                    f'~ document delivery: not measured for a {plant.format} document (the plant carries no readable token)'
                    for plant in documents
                    if plant.token is None
                )
                returned = _trail_returned_entries(getattr(r, 'agent_state', None) for r in (reply, reply2))
                if not returned:
                    notes.append(
                        _trail_lane_unwritten(simulate_path, (reply, reply2))
                        or '~ trail results: not exercised — the smoke turns reported no tool call that returned.'
                    )
                elif all(entry.get('result') is None for entry in returned):
                    measured.append(SmokeFailure(SmokeGate.TRAIL_RESULT_MISSING, _TRAIL_RESULT_MISSING, simulate_path.name, {'entries': str(len(returned))}))
                else:
                    notes.append(f'✓ trail results: {sum(e.get("result") is not None for e in returned)} of {len(returned)} returned tool calls carry their result.')
                _record_trail_names(simulate_path, returned, render_verbs, measured, notes)
                if surface.unreadable:
                    # THE FOURTH no-verdict state. `RenderSurface` split "declares no render
                    # surface" from "nobody could read the declaration" precisely because the
                    # probe below is skipped on both — and then the exit code collapsed them
                    # right back: a `⚠` note and exit 0, i.e. "✓ smoke OK … completed two real
                    # agent turns" over a bench whose only live channel was never probed and may
                    # not even have been known about. `bench_manifest validate` refuses a config
                    # this broken, so it bites exactly on the direct invocation Step 9f
                    # prescribes, where nothing else is looking.
                    #
                    # `--turn` only. The Init-only smoke never probes the render channel even on
                    # a perfectly readable declaration, so an unreadable one changes nothing it
                    # measured or claimed; it keeps its exit 0 and carries the ⚠ note.
                    return (2, '\n'.join([
                        *notes,
                        '✗ render probe: NO VERDICT — the render declaration could not be read, so this run cannot '
                        'tell a bench that renders nothing from one whose ONLY output channel is a render verb. The '
                        'probe was never planned; turns 1 and 2 ran and nothing about an action channel was measured. '
                        'Fix the declaration in plurai.config.yaml (verify_config.py owns its shape), or upgrade '
                        'plurai if this build has no render-surface reader, then re-run. Not a finding about '
                        'simulate.py: there is nothing to stub and nothing to fix there.',
                    ]))
                if render_verbs and not render_probed:
                    if not hasattr(reply, 'agent_actions'):
                        # The probe turn is off, but its SDK check needs no turn: turn 1's envelope answers it.
                        return (2, '\n'.join([
                            *notes,
                            f'✗ render probe: NO VERDICT — this SDK build has no `agent_actions` on its turn '
                            f'envelope, so a render-only turn cannot be observed at all: it reaches the backend as '
                            f'an empty reply and is graded as the agent saying nothing. Nothing about this bench\'s '
                            f'live channel was measured, and nothing here is a finding about simulate.py. Upgrade '
                            f'the plurai SDK rather than painting {render_verbs[0]} output into the reply text, '
                            f'which manufactures words the agent never said.',
                        ]))
                    notes.append(
                        f'~ render probe: render_channel: unprobed — {render_verbs[0]} was not asked for; the qualification run '
                        f'reads the action channel. Set {RENDER_PROBE_ENV}=1 to probe it here.'
                    )
                if render_probed:
                    # THIRD turn — the RENDER probe, driven only when the config declares a render
                    # surface. An agent that answers by rendering has one turn shape nothing else
                    # in this toolchain exercises: speech empty, the answer in the action channel.
                    # Every consumer downstream (the simulated user, the judges, the deterministic
                    # checks) reads that turn through `agent_actions`, so a setup that cannot
                    # produce it produces a suite whose render turns are all graded as silence.
                    reply3 = await child.user_turn(2, f'Please DISPLAY that on screen using {render_verbs[0]} rather than describing it in words.')
                    if not hasattr(reply3, 'agent_actions'):
                        # This check runs BEFORE `_turn_delivered_something`, and the order matters. It returns
                        # NO VERDICT (exit 2), not a pass with a warning: this bench's only live output channel
                        # is unmeasurable on this SDK, and the caller reads the EXIT CODE. At 0 the manifest
                        # would record `smoke_turn: verified` over a turn nothing could observe.
                        #
                        # A render-ONLY turn on such an SDK has an empty `ai_text` and no action channel to read,
                        # so the emptiness branch would fire first, exit 1 saying "the turn delivered neither
                        # speech nor an action", and send the customer to fix a `simulate.py` that is correct,
                        # for a fact about their SDK build.
                        return (2, '\n'.join([
                            *notes,
                            f'✗ render probe: NO VERDICT — this SDK build has no `agent_actions` on its turn '
                            f'envelope, so a render-only turn cannot be observed at all: it reaches the backend as '
                            f'an empty reply and is graded as the agent saying nothing. Nothing about this bench\'s '
                            f'live channel was measured, and nothing here is a finding about simulate.py. Upgrade '
                            f'the plurai SDK rather than painting {render_verbs[0]} output into the reply text, '
                            f'which manufactures words the agent never said.',
                        ]))
                    actions = list(reply3.agent_actions or [])
                    if not _turn_delivered_something(reply3):
                        silent = (
                            f'✗ render probe: asked for {render_verbs[0]}; turn 3 delivered neither speech nor an action, '
                            f'and this SDK CAN carry one (`agent_actions` is present and empty) — the three possible owners '
                            f'are listed above in the order to check them.'
                        )
                        notes += [
                            f'✗ render probe: asked for {render_verbs[0]}, the turn delivered neither speech nor an action.',
                            _empty_first_turn_message(render_verbs),
                        ]
                        measured.append(SmokeFailure(SmokeGate.RENDER_SILENT, silent, simulate_path.name, {}))
                    elif not actions:
                        notes.append(
                            f'⚠ render probe: the agent answered in words rather than calling {render_verbs[0]}. '
                            f'That may be correct for this prompt — but if the agent renders in production, the '
                            f'action channel is not reaching the SDK and every render turn of the suite will be '
                            f'graded on speech alone.'
                        )
                    else:
                        notes.append(f'✓ render probe: the action channel carries {", ".join(sorted({str(a.get("name")) for a in actions}))}.')
                if io.unreadable:
                    # The render branch's rule, for the other declaration: skipping a probe in
                    # silence certifies a channel nobody looked at. `--turn` only — the Init-only
                    # smoke plans no probes, so an unreadable declaration changes nothing it
                    # claimed and keeps its exit 0 with the ⚠ note.
                    return (2, '\n'.join([
                        *notes,
                        f'✗ input probe: NO VERDICT — the `{_AGENT_IO_KEY}` declaration could not be read, so this '
                        f'run cannot tell a bench that takes plain text turns from one that declares a file or an '
                        f'empty opener nothing probed. No input probe was planned; turns 1 and 2 ran and nothing '
                        f'about an input channel was measured. Fix the declaration in plurai.config.yaml '
                        f'(verify_config.py owns its shape) and re-run. Not a finding about simulate.py.',
                    ]))
                # --- the declared INPUT surface, confirmed through the real child ---
                # A declaration is a claim about THIS harness, and the only thing that can settle
                # it is handing the harness the part. One turn per declared non-text kind: `text`
                # is what turns 1 and 2 already sent, in the exact shape a scored run sends it
                # (a bare string), so re-sending it as a parts list would probe a shape no run
                # uses and cost a provider call to do it.
                io_turn = 3 if render_probed else 2
                carriable = _sdk_carriable_kinds()
                for kind in io.input_parts:
                    if kind == 'text':
                        notes.append('✓ input probe: `text` — confirmed by turns 1 and 2, which are exactly the shape a scored run sends.')
                        continue
                    if kind in io.undeclared_handover:
                        # NOT SENT. The SDK child refuses a part kind `agent.io.input` does not declare, as an
                        # empty scored turn, before `invoke_agent` runs: a probe would measure the declaration,
                        # and read as a harness that accepted the part and dropped it.
                        undeclared = (
                            f'✗ input probe: a `.document_fields` entry has scenario generation hand this agent {kind!r} '
                            f'parts, but `{_AGENT_IO_KEY}.input` does not declare {kind!r}, so the SDK refuses every such '
                            f'turn before invoke_agent runs (`harness_undeclared_input`). Nothing was sent: the harness was '
                            f'never asked to read one. Declare `{_AGENT_IO_KEY}: {{input: [text, {kind}]}}` at the root of '
                            f'plurai.config.yaml (verify_config reports `handover_kind_not_declared`) and re-run to probe the read, '
                            f'or remove the `.document_fields` entry if nothing hands this agent a document.'
                        )
                        return _with_measured(_failed(
                            1, _standalone_last_line('\n'.join([*notes, undeclared]), undeclared), SmokeGate.INPUT_PART_REFUSED,
                            message=undeclared, file='plurai.config.yaml', observed={'kind': kind},
                        ), measured, notes)
                    if carriable is not None and kind not in carriable:
                        # ASKED BEFORE THE TURN, for the render probe's reason: sending it anyway
                        # produces a failure that looks exactly like a harness defect, and the
                        # customer is sent to fix a simulate.py that is correct.
                        return (2, '\n'.join([
                            *notes,
                            f'✗ input probe: NO VERDICT — `{_AGENT_IO_KEY}.input` declares {kind!r}, which THIS plurai build '
                            f'cannot put on a turn (it carries {sorted(carriable)}). Nothing was sent, so nothing about this '
                            f'bench\'s input channel was measured and nothing here is a finding about {simulate_path.name}. '
                            f'Upgrade plurai, or fix the declaration if {kind!r} is not a kind this agent really takes.',
                        ]))
                    try:
                        part = _probe_part(kind)
                    except KeyError:
                        # Refused BY NAME, never resolved to the nearest thing. Exit 2: the
                        # declaration may be perfectly true of the agent — what is missing is a
                        # probe for it, which is a fact about this toolchain.
                        return (2, '\n'.join([
                            *notes,
                            f'✗ input probe: NO VERDICT — `{_AGENT_IO_KEY}.input` declares {kind!r}, which this build of '
                            f'the setup toolchain has no probe for (it knows {list(_INPUT_PART_KINDS)}). Nothing was sent '
                            f'and nothing about this bench\'s input channel was measured. Fix the declaration, or '
                            f'upgrade the plugin if {kind!r} is a part kind it should know.',
                        ]))
                    try:
                        io_reply = await child.user_turn(io_turn, [_probe_part('text'), part])
                    except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
                        owner = _probe_owner(type(exc).__name__, str(exc), '\n'.join(capture.buffer), simulate_path)
                        if owner == 'bench':
                            refused = (
                                f'✗ input probe: {simulate_path.name} raised on a declared {kind!r} part — fix invoke_agent\'s '
                                f'handling of a parts-list message content, or drop {kind!r} from `{_AGENT_IO_KEY}.input`.'
                            )
                            return _with_measured(_failed(1, _standalone_last_line('\n'.join([
                                *notes,
                                f'✗ input probe: your plurai.config.yaml declares `{_AGENT_IO_KEY}.input` includes {kind!r}, '
                                f'but {simulate_path.name} could not serve a turn carrying one — invoke_agent was handed a '
                                f'message whose content is a PARTS LIST '
                                f'([{{"type": "text", ...}}, {{"type": "{part.get("type")}", ...}}]) and raised '
                                f'({type(exc).__name__}: {str(exc)[:300]}). Every scenario that hands this agent a {kind} '
                                f'would die on that turn. Fix ONE of the two: make invoke_agent read a list-valued '
                                f'message content (LangChain carries it as `HumanMessage.content`; take the text parts '
                                f'and hand the rest to whatever attachment channel the agent already has; '
                                f'`references/document_input.py` decodes the part), or remove '
                                f'{kind!r} from `{_AGENT_IO_KEY}.input` — this agent takes text turns, and the scenario '
                                f'set will never attach one.',
                            ]), refused), SmokeGate.INPUT_PART_REFUSED, message=refused, file=simulate_path.name,
                                observed={'error': f'{type(exc).__name__}: {str(exc)[:300]}'}), measured, notes)
                        return (2, '\n'.join([
                            *notes,
                            f'✗ input probe: NO VERDICT — a declared {kind!r} part could not be carried to the harness at '
                            f'all ({type(exc).__name__}: {str(exc)[:300]}). The failure is on OUR side of the boundary, not '
                            f'in {simulate_path.name}: nothing about this bench\'s input channel was measured and there is '
                            f'nothing to fix there. Upgrade plurai and re-run.',
                        ]))
                    io_turn += 1
                    marker = _PROBE_PART_MARKERS.get(kind)
                    echoed = bool(marker) and marker in str(getattr(io_reply, 'ai_text', '') or '')
                    if echoed:
                        # THE ONLY externally visible proof that the part was READ rather than
                        # merely tolerated: the agent said back something that was only inside it.
                        notes.append(f'✓ input probe: the agent READ the {kind} part — it echoed a marker carried only inside it.')
                    elif _turn_delivered_something(io_reply):
                        # `~`, NOT `✓`: only `file` carries a marker, so an `image_url` declaration
                        # can never reach the strong verdict above and would otherwise always skim
                        # as confirmed. The prose was already honest; the glyph is what is read.
                        notes.append(
                            f'~ input probe: the harness served a turn carrying a {kind} part and answered it. '
                            f'A harness that silently DROPS a declared part looks like this too — have invoke_agent say what '
                            f'it received if you want the stronger verdict.'
                        )
                    else:
                        notes.append(
                            f'⚠ input probe: the harness ACCEPTED a {kind} part (it did not raise) but answered with neither '
                            f'speech nor an action, so this run confirms the part reaches invoke_agent and NOT that the agent '
                            f'reads it. Check that the {kind} reaches the model.'
                        )
                if io.may_open:
                    # THE EMPTY OPENER. An agent that greets first is driven with no user message
                    # on turn 0 of every scenario, and a harness that indexes payload['messages']
                    # raises on it — after the set has been paid for. `None`, not `[]`: `[]` is a
                    # message that says nothing, this is NO message.
                    try:
                        open_reply = await child.user_turn(io_turn, None)
                    except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
                        owner = _probe_owner(type(exc).__name__, str(exc), '\n'.join(capture.buffer), simulate_path)
                        if owner == 'bench':
                            refused = (
                                f'✗ may_open probe: {simulate_path.name} raised on {{\'messages\': []}} — make invoke_agent '
                                f'serve an empty opener, or drop `{_AGENT_IO_KEY}.{io.opener_key}`.'
                            )
                            return _with_measured(_failed(1, _standalone_last_line('\n'.join([
                                *notes,
                                f'✗ may_open probe: your plurai.config.yaml declares `{_AGENT_IO_KEY}.{io.opener_key}`, but '
                                f'{simulate_path.name} raised on the empty opener — invoke_agent was called with '
                                f'{{\'messages\': []}} and could not serve it ({type(exc).__name__}: {str(exc)[:300]}). An '
                                f'agent that opens the conversation is driven with no user message on turn 0, so every '
                                f'scenario of this bench would die on its first turn. Fix ONE of the two: make invoke_agent '
                                f'handle an empty payload[\'messages\'] (greet, then wait), or remove `{io.opener_key}` — this '
                                f'agent does not open, and the scenario set will always supply the first turn.',
                            ]), refused), SmokeGate.MAY_OPEN_REFUSED, message=refused, file=simulate_path.name,
                                observed={'error': f'{type(exc).__name__}: {str(exc)[:300]}'}), measured, notes)
                        return (2, '\n'.join([
                            *notes,
                            f'✗ may_open probe: NO VERDICT — the empty opener never reached the harness '
                            f'({type(exc).__name__}: {str(exc)[:300]}). On an older SDK the child builds its '
                            f'turn-0 payload OUTSIDE its dispatch try, so a no-message turn kills the child process before '
                            f'anything is reported — which is what this looks like. Nothing about {simulate_path.name} was '
                            f'measured and there is nothing to fix there. Upgrade plurai and re-run.',
                        ]))
                    if not _turn_delivered_something(open_reply):
                        silent = (
                            f'✗ may_open probe: the agent said nothing when driven with no user message, which is turn 0 of '
                            f'every scenario — make it greet, or drop `{_AGENT_IO_KEY}.{io.opener_key}`.'
                        )
                        notes += [
                            f'✗ may_open probe: `{_AGENT_IO_KEY}.{io.opener_key}` says this agent OPENS the conversation, but '
                            f'driven with no user message it delivered neither speech nor an action. That is turn 0 of every '
                            f'scenario this bench will ever run, and a silent one is scored as an agent that said nothing.',
                            _empty_first_turn_message(render_verbs),
                        ]
                        measured.append(SmokeFailure(SmokeGate.MAY_OPEN_SILENT, silent, simulate_path.name, {}))
                    else:
                        notes.append('✓ may_open probe: the agent opened the conversation with no user message.')
                if channel_plan:
                    failure = await _probe_channels(child, channel_turns, channel_turn, capture, simulate_path, notes)
                    if failure is not None:
                        measured.append(failure)
                handed_parts = bool(documents) or _HANDOVER_PART_KIND in io.input_parts
                if request_block.values or handed_parts:
                    # Every request key rode Init and turn 1, and every handed-over document rode a turn as a `file`
                    # part; the SDK records which of each the harness READ — the two apart (`unread_inputs`, `unread_parts`).
                    await child.finalize()
                    measured[:] = _read_parts_delivered(getattr(child, 'unread_parts', None), measured, notes)
                    if (unread_parts := _unread_parts(getattr(child, 'unread_parts', None), measured, file=simulate_path.name)) is not None:
                        measured.append(unread_parts)
                if request_block.values:
                    note, failing = _input_delivery(
                        simulate_path, request_block.values, getattr(child, 'unread_inputs', None), store_only, lane='the turn-1 payload', preempted=False,
                        documents=frozenset(plant.field for plant in documents),
                    )
                    if failing is not None:
                        return _with_measured(_failed(
                            1, _standalone_last_line('\n'.join([*notes, note]), failing), SmokeGate.REQUEST_KEY_UNREAD, message=failing, file=simulate_path.name, observed={},
                        ), measured, notes)
                    notes.append(note)
    except TypeError as exc:
        if 'await' in str(exc):
            awaited = (
                "✗ turn smoke: invoke_agent's return was awaited but is not awaitable. "
                'Your installed plurai SDK awaits unconditionally (fixed in newer SDKs): '
                'either make invoke_agent `async def` or update the plurai package.'
            )
            return _failed(1, awaited, SmokeGate.INVOKE_NOT_AWAITABLE, message=awaited, file=simulate_path.name, observed={})
        raise
    # `notes` rides the FAILING verdicts too: "which envelope was this?" is the first question
    # asked of a child that died during init, and the request block is half the answer.
    except OrchestratorAdapterError as exc:
        failure = _format_failure(str(exc), capture.buffer, source='adapter_error', notes=notes, simulate_path=simulate_path, exc=exc)
        return _with_remedy(_with_measured(failure, measured, notes), world_remedy)
    except RuntimeError as exc:
        failure = _format_failure(str(exc), capture.buffer, source='runtime_error', notes=notes, simulate_path=simulate_path)
        return _with_remedy(_with_measured(failure, measured, notes), world_remedy)
    finally:
        sdk_logger.removeHandler(capture)
    if drive_turn and records_session and _offers_resume(simulate_path):
        notes.append(_RESUME_SEED_NOT_PROBED)
    elif drive_turn and _offers_resume(simulate_path) and 'resume_seed' in inspect.signature(ChildProcess.init).parameters:
        exc, _stderr = await _seed_init(simulate_path, world, request_block.values, 'smoke-canned-resume', resume_seed=_CANNED_RESUME_SEED)
        if exc is None:
            notes.append('✓ resume seed: seed_state accepted a canned `type`-keyed resume seed holding a tool call and its result.')
        else:
            # Refused: offer the same conversation without its tool exchange, once. A harness that accepts the first never gets here.
            plain, _stderr = await _seed_init(
                simulate_path, world, request_block.values, 'smoke-canned-resume-plain', resume_seed=_CANNED_RESUME_SEED_PLAIN
            )
            if plain is None:
                notes.append(f'⚠ resume seed: accepted without its tool call and result, refused with them: {_one_line(exc, 600)}')
                measured.append(SmokeFailure(
                    SmokeGate.TOOL_HISTORY_DROPPED, _TOOL_HISTORY_UNSEEDABLE, simulate_path.name, {'path': 'resume', 'refusal': _one_line(exc, 600)}
                ))
            else:
                notes.append(f'✗ resume seed: {_one_line(plain, 600)}')
                measured.append(SmokeFailure(SmokeGate.CANNED_RESUME_REFUSED, _CANNED_RESUME_REFUSED, simulate_path.name, {'refusal': _one_line(plain, 600)}))
    if not drive_turn and not _siblings_decided(simulate_path) and (declared := _sibling_declaration(simulate_path)) is not None:
        observed = await _sibling_probe(simulate_path, world, request_block.values, declared)
        if (sibling := _sibling_finding(declared, observed, simulate_path.name)) is not None:
            if observed.detail:
                notes.append(f'  sibling probe: {observed.detail}')
            measured.append(sibling)
    if any(f.gate not in _WARNING_GATES for f in measured):
        return _with_remedy(_with_measured((0, ''), measured, notes), world_remedy)
    warned = [f.message for f in measured]
    if drive_turn:
        ok = '✓ smoke OK — simulate.py loaded, responded to Init, and completed two real agent turns'
        return Verdict(0, _standalone_last_line('\n'.join([ok, *notes, *warned]), ok), measured)
    # `notes` rides the Init-only path too: an unreadable render declaration is a fact about this
    # bench whichever mode read it, and dropping it here made the Init smoke the one place the
    # surface could go unread in silence.
    ok_init = '✓ smoke OK — simulate.py loaded and responded to Init'
    return Verdict(0, _standalone_last_line('\n'.join([ok_init, *notes, *warned]), ok_init), measured)


_WORLD_READ_RAISED = (
    "✗ world read: the Init's post-seed read failed, so every run would compare its seed against no world and score turns "
    'over a world nobody read — fix what the read raised (world_snapshot), via the harness lane of /analyze-simulation'
)


def _world_read_raised(child: Any, simulate_path: Path) -> SmokeFailure | None:
    """The advisory ``world_read_raised`` when the Init's post-seed read carries a crash marker (the child answers
    ``InitOk`` either way and says so only on stderr). None on a clean read, no read, or an SDK without the comparator."""
    if (landing := _seed_landing()) is None:
        return None
    readback = landing.seed_readback(post_seed_world_state=getattr(child, 'post_seed_world_state', None), scenario_seed=None)
    if readback.verdict != 'read_failed':
        return None
    return SmokeFailure(SmokeGate.WORLD_READ_RAISED, _WORLD_READ_RAISED, simulate_path.name, {'read_error': _one_line(readback.read_error, 450)})


#: A trail entry's ``status`` naming a FAILED dispatch: the plugin's trail contract (``build_run_json._TRAIL_FAILED_STATUSES``),
#: restated because this file runs standing alone. A failed dispatch has no result to record.
_TRAIL_FAILED_STATUSES = frozenset({'error', 'unavailable', 'failed', 'failure', 'refused', 'not_dispatchable'})
_TRAIL_RESULT_MISSING = (
    '⚠ trail results: every tool-trail entry whose dispatch did not fail carries no `result`, so every judge would grade '
    "the tool calls blind — record each tool's returned value as the entry's `result` at the tool wrapper"
)
#: The resume seed the turn smoke offers a harness that can resume: ``type``-keyed messages with one tool exchange
#: (``tool_calls`` on the ai message, ``tool_call_id`` on the tool result), the shape resumed regressions and trace
#: scenarios reach Init in. ``_CANNED_RESUME_SEED_PLAIN`` is the same conversation without the exchange, offered only
#: when the first is refused, so a harness that restores no conversation at all stays ``canned_resume_refused``.
_CANNED_RESUME_SEED = {
    'messages': [
        {'type': 'human', 'content': 'Hello, I need help with my request.', 'id': 'smoke-resume-h0'},
        {'type': 'ai', 'content': '', 'id': 'smoke-resume-a0', 'tool_calls': [{'name': 'lookup', 'args': {'query': 'my request'}, 'id': 'smoke-call-0'}]},
        {'type': 'tool', 'content': '{"status": "found"}', 'id': 'smoke-resume-t0', 'tool_call_id': 'smoke-call-0'},
        {'type': 'ai', 'content': 'I found your request. What can I do for you?', 'id': 'smoke-resume-a1'},
    ],
    'agent_state': {},
}
_CANNED_RESUME_SEED_PLAIN = {
    'messages': [
        {'type': 'human', 'content': 'Hello, I need help with my request.', 'id': 'smoke-resume-h0'},
        {'type': 'ai', 'content': 'Of course, what can I do for you?', 'id': 'smoke-resume-a0'},
    ],
    'agent_state': {},
}
_TOOL_HISTORY_UNSEEDABLE = (
    '⚠ tool history: seed_state refused a resume seed holding a tool call and its result, and accepted it without them, '
    'so regressions with tool calls cannot replay on this harness — restore each seed message whole, its `tool_calls` '
    'and `tool_call_id` included (references/resumed-seed-state.md); a harness whose memory cannot hold tool messages '
    'keeps this warning'
)
_TOOL_HISTORY_DROPPED = (
    '⚠ tool history: on turn 2 the agent could not give back a value its own tool returned on turn 1, so the harness '
    'likely keeps only the user and final assistant messages between turns and every later turn loses earlier tool '
    'calls and results — keep every message a turn produces in the thread history, tool calls and results included '
    '(bridge: the shape production stores, or `turnMessages(result)` from runtime/adapters/ai_sdk.mjs; LangGraph: a '
    'checkpointer). A heuristic on free text (a paraphrased value trips it): verify before acting'
)
class _Carry(NamedTuple):
    """What turn 2 asks back: the tool, the values its result returned that the conversation never said (``fresh``: only a
    kept tool result can give one back) and the ones turn 1 already said (``said``: text history can give those back)."""

    tool: str
    fresh: tuple[str, ...]
    said: tuple[str, ...]


def _carry_candidate(value: str) -> bool:
    """A returned value a reply can quote: 6-64 characters, a letter, and a digit, an uppercase letter or a space. A
    lowercase one-word value (`active`, `pending`) is an enum ordinary prose holds by chance; a date or a timestamp is
    written many ways."""
    return (6 <= len(value) <= 64 and any(c.isalpha() for c in value) and any(c.isdigit() or c.isupper() or c.isspace() for c in value)
            and not _is_timestamp(value))


def _carry_proof(value: str) -> bool:
    """A candidate whose quote proves the tool result was kept: it holds a digit or a space. A one-word value of letters
    alone, capitalised or not (`UNREAD`, `Active`, `Newsletters`), can open an ordinary sentence by chance, so its quote
    measures nothing either way."""
    return any(c.isdigit() or c.isspace() for c in value)


def _quoted(value: str, text: str) -> bool:
    """``value`` in ``text`` as a whole: never inside a longer word or number."""
    return re.search(rf'(?<!\w){re.escape(value)}(?!\w)', text) is not None


def _keyed_strings(value: object, depth: int = 0) -> Iterable[tuple[str, str]]:
    """Every ``(key, string)`` pair in a decoded tool result, depth first, in the result's own order."""
    if depth > 6:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                yield str(key), item
            else:
                yield from _keyed_strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value[:50]:
            yield from _keyed_strings(item, depth + 1)


def _is_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return True


def _carry_values(entries: Iterable[dict], *, said: str) -> _Carry | None:
    """The first returned turn-1 tool result holding a value (:func:`_carry_candidate`) that the conversation never showed
    (``said``: the opener and the turn-1 reply) and the call's own args do not carry: its tool, those values, and the ones
    turn 1 already said."""
    for entry in entries:
        result = entry.get('result')
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        args = json.dumps(entry.get('args'), default=str)
        values = list(dict.fromkeys(v for _, v in _keyed_strings(result) if _carry_candidate(v) and v not in args))
        fresh = tuple(v for v in values if v not in said)
        if fresh:
            return _Carry(str(entry.get('tool')), fresh, tuple(v for v in values if v in said))
    return None


def _record_tool_carry(carry: _Carry | None, reply2: object, file: str, notes: list[str], measured: list[SmokeFailure], *,
                       name_echoed: bool) -> None:
    """Turn 2's tool-history verdict, by exact value membership: a value only the tool result held, quoted, that holds a
    digit or a space (✓, :func:`_carry_proof`); the tool called again, only one-word or already-said values quoted, or
    turn 2 lost the name too (not measured — STATELESS covers that); no value to ask (not exercised); else, reply 2 holds
    none of the returned values, the ``tool_history_dropped`` warning."""
    if carry is None:
        notes.append('~ tool history: not exercised — turn 1 returned no tool value to ask back.')
        return
    state = getattr(reply2, 'agent_state', None)
    trail = state.get('tool_trail') if isinstance(state, dict) else None
    if isinstance(trail, list) and any(isinstance(e, dict) and e.get('tool') == carry.tool for e in trail):
        notes.append(f'~ tool history: not measured — the agent called `{carry.tool}` again on turn 2 instead of answering from its history.')
        return
    said2 = str(getattr(reply2, 'ai_text', '') or '')
    if any(_quoted(v, said2) for v in carry.fresh if _carry_proof(v)):
        notes.append(f'✓ tool history: the agent gave back a value its `{carry.tool}` call returned on turn 1.')
        return
    if any(_quoted(v, said2) for v in carry.fresh):
        notes.append('~ tool history: not measured — turn 2 gave back only one-word values, which a reply can hold by chance.')
        return
    if any(_quoted(v, said2) for v in carry.said):
        notes.append('~ tool history: not measured — turn 2 gave back only values the turn-1 reply already said.')
        return
    if not name_echoed:
        notes.append('~ tool history: not measured — turn 2 did not echo the name either.')
        return
    notes.append(f'  tool history: asked for entries from `{carry.tool}` (e.g. {list(carry.fresh[:3])!r}); turn-2 reply: {said2[:200]!r}')
    measured.append(SmokeFailure(SmokeGate.TOOL_HISTORY_DROPPED, _TOOL_HISTORY_DROPPED, file, {'path': 'live', 'tool': carry.tool}))
_CANNED_RESUME_REFUSED = (
    "✗ resume seed: seed_state refused a canned `type`-keyed resume seed — read each seed message's `type` and restore "
    'every one (references/resumed-seed-state.md). Only where the conversation lives in a session the harness cannot '
    'write, declare harness.resume.mode: unsupported; a harness that records its session and resumes it is not one'
)
_RESUME_SEED_NOT_PROBED = (
    '~ resume seed: not probed with a canned seed — this harness resumes the CLI session it records each turn '
    "(`cli_session`), which a canned seed cannot carry; a recorded regression's replay checks it."
)


def _records_cli_session(state: object) -> bool:
    """Whether a turn's ``agent_state`` reports a recorded CLI session (a non-empty ``cli_session`` mapping): the key
    ``regressions.cli_session_to_carry`` resumes a replay from, measured off the harness's own reply."""
    return isinstance(state, dict) and isinstance(state.get('cli_session'), dict) and bool(state['cli_session'])


async def _probe_channels(child: Any, turns: list[ChannelTurn], turn: int, capture: Any, simulate_path: Path, notes: list[str]) -> SmokeFailure | None:
    """The channel-probe turns (``_channel_turns``) from ``turn`` on, each carrying its planted probes through the
    declared `agent.io.channels` entries (C1) — an input reachable only on one production task type on that type.

    The harness raising on one is the finding (a channel every scenario of its kind plants into); a build that does
    not apply a plan, or a world that refused the write, is no verdict about ``simulate.py`` and is said, not charged.
    The SDK hands each value under the channel's ``field`` and the typed records in ``payload['channel_inputs']``; a
    `between_turn_action` (and a file item's write) reaches a harness-owned world through ``apply_user_action``.
    """
    if getattr(child, 'applies_channel_inputs', None) is not True:
        names = ', '.join(f'{c["name"]} ({c["kind"]})' for t in turns for c in t.channels)
        notes.append(f'~ channel probe: NO VERDICT — this plurai build does not apply a scenario\'s channel inputs, so {names} '
                     'could not be planted; a scenario that plants one is refused on it. Upgrade plurai and re-run.')
        return None
    for at, probe_turn in enumerate(turns, start=turn):
        names = ', '.join([*(f'{c["name"]} ({c["kind"]})' for c in probe_turn.channels), *(f'{k} = {v!r}' for k, v in probe_turn.pins.items())])
        try:
            reply = await child.user_turn(at, 'Please go on with what I have shared so far.')
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            if _probe_owner(type(exc).__name__, str(exc), '\n'.join(capture.buffer), simulate_path) != 'bench':
                notes.append(f'~ channel probe: NO VERDICT — the planted turn never reached the harness ({type(exc).__name__}: {str(exc)[:300]}).')
                return None
            refused = (f'✗ channel probe: {simulate_path.name} raised on a turn carrying {names} — make invoke_agent (and apply_user_action, '
                       'for a write) take each channel\'s field the way the agent\'s request does, or drop the channel from agent.io.channels')
            notes.append(f'{refused} ({type(exc).__name__}: {str(exc)[:300]}).')
            return SmokeFailure(SmokeGate.CHANNEL_REFUSED, refused, simulate_path.name, {'error': f'{type(exc).__name__}: {str(exc)[:300]}'})
        results = getattr(reply, 'channel_results', None) or []
        unapplied = [f'{r.get("channel")}: {r.get("status")}' for r in results if isinstance(r, dict) and r.get('status') != 'applied']
        if unapplied:
            notes.append(f'~ channel probe: the harness served the planted turn, but {"; ".join(unapplied)} — the smoke world refused or '
                         'could not take the write, so arrival is unconfirmed for those.')
        else:
            notes.append(f'✓ channel probe: the harness served a turn carrying {names}.')
    return None


def _trail_returned_entries(agent_states: Iterable[object]) -> list[dict]:
    """The ``tool_trail`` entries across ``agent_states`` whose dispatch did not fail."""
    return [
        entry
        for state in agent_states
        if isinstance(state, dict) and isinstance(state.get('tool_trail'), list)
        for entry in state['tool_trail']
        if isinstance(entry, dict) and str(entry.get('status') or '').strip().lower() not in _TRAIL_FAILED_STATUSES
    ]


def _trail_lane_unwritten(simulate_path: Path, replies: Iterable[object]) -> str | None:
    """The advisory ``TRAIL_LANE_UNWRITTEN`` note when the chain shows a call to a tool `plurai.config.yaml` declares on
    ``observation_lane: tool_trail`` and no turn wrote ``agent_state.tool_trail``: the lane was never written, which an
    empty trail alone cannot tell from "no tool ran". ``None`` otherwise. It never fails the smoke."""
    replies = list(replies)
    if any(_reports_tool_trail(getattr(r, 'agent_state', None)) for r in replies):
        return None
    chain = [m for r in replies for m in (getattr(r, 'full_messages', None) or []) if isinstance(m, dict)]
    names = (c.get('name') or (c.get('function') or {}).get('name') for m in chain for c in (m.get('tool_calls') or []) if isinstance(c, dict))
    called = {str(n) for n in names if n is not None}
    unwritten = sorted(called & _trail_lane_tools(simulate_path))
    if not unwritten:
        return None
    return (
        f'⚠ trail lane unwritten (TRAIL_LANE_UNWRITTEN): the smoke turns called {", ".join(f"`{n}`" for n in unwritten)}, declared on '
        "`observation_lane: tool_trail`, and no turn wrote `agent_state['tool_trail']`, so the run sees none of those calls. "
        'Record each call of a trail-lane tool on `tool_trail` (`tool`/`args`/`call_id`/`status`/`result`), or declare the lane '
        'its calls really ride. Advisory: the smoke verdict is unchanged.'
    )


#: CrewAI's ``sanitize_tool_name`` (``crewai/utilities/string_utils.py``), RESTATED because this file runs standing
#: alone: the name CrewAI's tool events carry for a tool. A framework fact, pinned by vectors measured on crewai 1.15.28
#: (the plugin's test vectors). The recipe in ``references/framework-patterns.md`` falls
#: back to this same body when crewai has no such function.
_WIRE_QUOTES = re.compile(r'[\'\"]+')
_WIRE_CAMEL_LOWER_UPPER = re.compile(r'([a-z])([A-Z])')
_WIRE_CAMEL_UPPER_LOWER = re.compile(r'([A-Z]+)([A-Z][a-z])')
_WIRE_DISALLOWED = re.compile(r'[^a-zA-Z0-9]+')
_WIRE_UNDERSCORES = re.compile(r'_+')
_WIRE_NAME_MAX = 64


def _crewai_wire_name(name: str) -> str:
    """The name CrewAI's trail events carry for the tool named ``name`` (see :data:`_WIRE_QUOTES`)."""
    out = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    out = _WIRE_CAMEL_LOWER_UPPER.sub(r'\1_\2', _WIRE_CAMEL_UPPER_LOWER.sub(r'\1_\2', out)).lower()
    out = _WIRE_UNDERSCORES.sub('_', _WIRE_DISALLOWED.sub('_', _WIRE_QUOTES.sub('', out))).strip('_')
    if len(out) > _WIRE_NAME_MAX:
        suffix = '_' + hashlib.sha256(out.encode()).hexdigest()[:8]
        out = out[: _WIRE_NAME_MAX - len(suffix)].rstrip('_') + suffix
    return out


def _declared_trail_names(simulate_path: Path) -> tuple[frozenset[str], bool] | None:
    """``(names, closed)`` per the first readable `plurai.config.yaml`: the names a trail row may carry (its ``tools``
    keys or list names, and its ``agent.native_tools``), and whether it declares ``render_actions`` over a non-empty
    ``tools`` ∪ render verbs — the closed surface the backend charges an undeclared call on. ``None`` when no config could
    be read: verify_config owns its shape."""
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception:  # noqa: BLE001 — no reader, nothing declared
        return None
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            try:
                document = yaml.safe_load((root / relpath).read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001 — absent or unreadable
                continue
            if not isinstance(document, dict):
                continue
            tools = document.get('tools')
            names = {str(n) for n in tools} if isinstance(tools, dict) else set()
            if isinstance(tools, list):
                names |= {str(t if isinstance(t, str) else t.get('name')) for t in tools if isinstance(t, str) or (isinstance(t, dict) and t.get('name'))}
            # Closed as the backend reads it: `render_actions` declared over a surface that is not empty.
            render = document.get('render_actions')
            closed = 'render_actions' in document and bool(names or (render if isinstance(render, list) else ()))
            agent = document.get('agent')
            native = agent.get('native_tools') if isinstance(agent, dict) else None
            names |= {str(n) for n in native} if isinstance(native, list) else set()
            names |= _builtins_to_record((root / relpath).parent / 'artifacts' / 'agent_topology.json', document)
            return frozenset(names), closed
    return None


def _builtins_to_record(topology_path: Path, document: dict) -> set[str]:
    """The CLI built-ins ``record_native_tools.py`` writes to ``agent.native_tools`` from the map beside the config (the
    recorder's own predicate, ``host_effects.recorded_native_tools``, narrowed to the rows the CLI runs itself:
    ``runs: subprocess`` and not MCP-served, the confiner's reach), so a trail row naming one is declared before the
    scan has recorded it. Empty when the map or the plugin's scripts cannot be read, or off the runtime it records for."""
    try:
        topology = json.loads(topology_path.read_text(encoding='utf-8'))
        _plugin_scripts_on_path()
        from host_effects import outcome_for, recorded_native_tools  # noqa: PLC0415 — the recorder's and the confiner's own predicates

        recorded = {str(n) for n in recorded_native_tools(topology, document) or ()} if isinstance(topology, dict) else set()
        return {str(t['name']) for t in topology.get('tools') or () if isinstance(t, dict) and str(t.get('name')) in recorded
                and outcome_for(t, runtime='claude_agent_sdk') == 'confine'} if recorded else set()
    except Exception:  # noqa: BLE001 — absent or unreadable: today's behaviour
        return set()


def _name_letters(name: str) -> str:
    """``name`` lower-cased with every character but a letter or a digit dropped: two names equal here differ only in
    case and separators (``createDraft`` / ``Create Draft``), a naming mismatch and never a missing tool."""
    return re.sub(r'[^a-z0-9]', '', name.lower())


def _trail_names_undeclared(simulate_path: Path, entries: Iterable[dict], render_verbs: Iterable[str]) -> tuple[list[SmokeFailure], str | None]:
    """``(the failures, the advisory note)`` for trail rows whose ``tool`` is no declared name.

    Every name the trail may carry is a ``tools`` key, a render verb, an ``agent.native_tools`` entry or the SDK's
    interrupt act. A row naming anything else is:

    * the TRAIL_TOOL_UNDECLARED HOLD (overrulable, never hard) when its name is the CrewAI wire name of EXACTLY ONE
      declared name — the deterministic one-line fix, named in the message;
    * the TRAIL_TOOL_UNTYPED WARNING (it holds nothing) when the framework ran it (a row with ``status: ok``), the config
      declares ``render_actions`` (a closed surface, where the backend charges the call as invented) and no declared name
      differs from it only in case and separators: a function the runtime adds on the agent's behalf that the scan never
      typed;
    * the ADVISORY note otherwise (several declared names sanitize to it, a case/separator variant of one, an open
      surface, or a status other than ``ok``).

    ``([], None)`` when the config cannot be read or every name is declared. Both failures can stand on one trail.
    """
    read = _declared_trail_names(simulate_path)
    if read is None:
        return [], None
    declared, closed = read
    try:
        from plurai._protocol import INTERRUPT_ACT  # noqa: PLC0415 — the SDK's own act name; optional on an older SDK

        reserved = frozenset({str(INTERRUPT_ACT)})
    except Exception:  # noqa: BLE001 — no act name to allow
        reserved = frozenset()
    entries = list(entries)
    surface = declared | set(render_verbs)
    off = sorted({str(e['tool']) for e in entries if isinstance(e.get('tool'), str) and e['tool'] not in surface | reserved})
    if not off:
        return [], None
    keys_of = {name: sorted(k for k in surface if _crewai_wire_name(k) == name) for name in off}
    held = {name: keys[0] for name, keys in keys_of.items() if len(keys) == 1}
    ran = {str(e['tool']) for e in entries if isinstance(e.get('tool'), str) and str(e.get('status') or '').strip().lower() == 'ok'}
    variants = {_name_letters(k) for k in surface}
    untyped = [n for n in off if closed and n not in held and not keys_of[n] and n in ran and _name_letters(n) not in variants]
    failures: list[SmokeFailure] = []
    if untyped:
        names = ', '.join(f'`{n}`' for n in untyped)
        message = (
            f'⚠ trail tool untyped (TRAIL_TOOL_UNTYPED): the tool trail records {names} run by the framework (`status: ok`), and '
            'no `tools` key, render verb or `agent.native_tools` entry declares it; with `render_actions` declared the surface is '
            'closed, so the backend charges every such call as one the agent invented. A function the runtime adds on the '
            "agent's behalf (a memory tool, a delegation or transfer call) is typed like any other tool: first its row on the map "
            '— add it to artifacts/agent_topology.json as a live tool with `writes`, `effects` and `runs` typed (scan Step 2a) '
            'and re-render the map (scan Step 2b) — then its `tools.<name>` entry in plurai.config.yaml: `mode: real`, '
            '`observation_lane: tool_trail`, its descriptor citing the framework source that adds it. If no framework code adds '
            'it, the trail row\'s status is wrong: the harness records `not_dispatchable`, never `ok`, for a call the framework '
            'did not run. Warning: the smoke verdict is unchanged'
        )
        failures.append(SmokeFailure(SmokeGate.TRAIL_TOOL_UNTYPED, message, simulate_path.name, {'names': ', '.join(untyped)}))
    if held:
        pairs = ', '.join(f'`{name}` for the declared `{key}`' for name, key in sorted(held.items()))
        message = (
            f'✗ trail tool names: the tool trail records {pairs} — a trail row\'s `tool` is the plurai.config.yaml `tools` key, '
            "never the framework's wire name, so every lookup by the key misses it; map each back to its key where the trail "
            'row is built (framework-patterns.md, "The trail row\'s tool name"), or, where the agent\'s message lane '
            'carries that name too, declare the tool under it instead'
        )
        failures.insert(0, SmokeFailure(SmokeGate.TRAIL_TOOL_UNDECLARED, message, simulate_path.name, {'names': ', '.join(sorted(held))}))
    rest = [name for name in off if name not in held and name not in untyped]
    if not rest:
        return failures, None
    shared = [f'`{n}` (the wire name of {" and ".join(f"`{k}`" for k in keys_of[n])})' for n in rest if keys_of[n]]
    unknown = [f'`{n}`' for n in rest if not keys_of[n]]
    note = (
        f'⚠ trail tool unresolved (TRAIL_TOOL_UNRESOLVED): the tool trail records {", ".join([*unknown, *shared])}, which no '
        '`tools` key, render verb or `agent.native_tools` entry names. Record the declared key as the row\'s `tool`, or declare '
        'the tool under the name the trail carries. Advisory: the smoke verdict is unchanged.'
    )
    return failures, note


def _record_trail_names(simulate_path: Path, entries: Iterable[dict], render_verbs: Iterable[str], measured: list[SmokeFailure], notes: list[str]) -> None:
    """Add :func:`_trail_names_undeclared`'s failures to ``measured`` and its note to ``notes``, once each per smoke."""
    failures, note = _trail_names_undeclared(simulate_path, entries, render_verbs)
    for failure in failures:
        if failure.message not in {f.message for f in measured}:
            measured.append(failure)
    if note is not None and note not in notes:
        notes.append(note)


def _trail_lane_tools(simulate_path: Path) -> frozenset[str]:
    """The tools `plurai.config.yaml` declares on ``observation_lane: tool_trail``; none when it cannot be read."""
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception:  # noqa: BLE001 — no reader, nothing declared
        return frozenset()
    names: set[str] = set()
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            try:
                document = yaml.safe_load((root / relpath).read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001 — absent or unreadable: verify_config owns its shape
                continue
            tools = document.get('tools') if isinstance(document, dict) else None
            for name, spec in tools.items() if isinstance(tools, dict) else ():
                if isinstance(spec, dict) and spec.get('observation_lane') == 'tool_trail':
                    names.add(str(name))
    return frozenset(names)


def _offers_resume(simulate_path: Path) -> bool:
    """Whether a resume seed can reach this harness: a Python harness binding ``seed_state`` whose manifest does not
    declare ``harness.resume.mode: unsupported``. A harness file that cannot be read offers nothing."""
    try:
        return simulate_path.suffix == '.py' and _declares_seed_state(simulate_path) and not _resume_unsupported(simulate_path)
    except OSError:
        return False


def _with_remedy(verdict: Verdict, remedy: str | None) -> Verdict:
    """``verdict`` with ``remedy`` appended to its LAST line — the one line a caller records. Not on a
    NO VERDICT (exit 2: an interpreter or a provider that refused): no world would have changed that."""
    code, summary = verdict
    return Verdict(code, f'{summary} — {remedy}', verdict.failures) if remedy and code != 2 else verdict


def _declares_seeded_world(simulate_path: Path) -> bool:
    """Whether the ``plurai.config.yaml`` beside simulate.py declares a world the scenario seeds: any ``mode: mock`` tool,
    or harness-owned world types (``world_state.owner: agent`` / ``external`` with ``entity_types``, or ``owned_types``)."""
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception:  # noqa: BLE001 — no reader, nothing declared
        return False
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            try:
                document = yaml.safe_load((root / relpath).read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001 — absent or unreadable: verify_config owns its shape
                continue
            tools = (document or {}).get('tools') if isinstance(document, dict) else None
            world = (document or {}).get('world_state') if isinstance(document, dict) else None
            world = world if isinstance(world, dict) else {}
            harness_world = bool(world.get('owned_types')) or (
                str(world.get('owner') or '').strip().lower() in ('agent', 'external') and bool(world.get('entity_types'))
            )
            return harness_world or any(isinstance(t, dict) and t.get('mode') == 'mock' for t in (tools.values() if isinstance(tools, dict) else ()))
    return False


class _SiblingDeclaration(NamedTuple):
    """What the config says about sibling agent processes on this host: what only one of them may hold (each
    ``harness.single_instance`` cite), and the world copies a probe child each takes one
    of (``world_state.slots`` of 2 or more, else ``None``), and the entity types the harness's own store declares
    (``world_state.owned_types`` / ``entity_types``), for a probe row when the smoke world seeds none."""

    exclusive: tuple[str, ...]
    slots: int | None
    world_types: tuple[str, ...] = ()


def _sibling_declaration(simulate_path: Path) -> _SiblingDeclaration | None:
    """The config's :class:`_SiblingDeclaration`, or ``None`` when the sibling probe must not run: no YAML reader (an
    unread declaration is never probed as an absent one), ONE shared ``external`` copy, or ``slots: 1`` on any owner
    (three resets and seeds would write the one store the bench declares only one process may hold)."""
    try:
        import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    except Exception:  # noqa: BLE001 — no reader: nothing can be told apart
        return None
    exclusive: list[str] = []
    slots: int | None = None
    types: list[str] = []
    read: set[Path] = set()
    for root in (simulate_path.parent, simulate_path.parent.parent):
        for relpath in _CONFIG_RELPATHS:
            # `plurai_sim/plurai.config.yaml` is reached from both roots: one file is one declaration.
            if (path := (root / relpath).resolve()) in read:
                continue
            read.add(path)
            try:
                document = yaml.safe_load(path.read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001 — absent or unreadable: verify_config owns its shape
                continue
            document = document if isinstance(document, dict) else {}
            harness = document.get('harness') if isinstance(document.get('harness'), dict) else {}
            world = document.get('world_state') if isinstance(document.get('world_state'), dict) else {}
            count = world.get('slots') if isinstance(world.get('slots'), int) and not isinstance(world.get('slots'), bool) else None
            if count == 1 or (str(world.get('owner') or '').strip().lower() == 'external' and count is None):
                return None
            cites = harness.get('single_instance')
            exclusive += [f'harness.single_instance {cite!r}' for cite in (cites if isinstance(cites, list) else [cites] if cites else [])]
            if count is not None and count >= 2:
                slots = count
            for key in ('owned_types', 'entity_types'):
                declared = world.get(key)
                types += [t for t in (declared if isinstance(declared, list) else list(declared) if isinstance(declared, dict) else []) if isinstance(t, str)]
    return _SiblingDeclaration(tuple(dict.fromkeys(exclusive)), slots, tuple(dict.fromkeys(types)))


def _siblings_decided(simulate_path: Path) -> bool:
    """Whether the sibling probe must not run on this bench (:func:`_sibling_declaration` is ``None``)."""
    return _sibling_declaration(simulate_path) is None


#: How many children the sibling probe Inits at once: the final scored run's ``--trials=3`` (simulation-run SKILL.md),
#: which run side by side even at ``--concurrency 1`` — the narrowest a final run ever is. Pinned by a test.
_SIBLING_CHILDREN = 3

#: Appended to the first seeded entity's id for sibling ``k``'s own probe row (``<id>-plurai-sib<k>``).
_SIBLING_MARK = '-plurai-sib'

_SIBLING_REMEDY = (
    'a run starts trials side by side, and this harness Inits clean alone but not beside a sibling: something one agent '
    'process holds (a fixed listen port, a lock file). Hand each child its own through the config key, env var or argument '
    'that sets it — or, when a runner you may not edit passes the config inline, by wrapping the call it makes '
    '(example_simulate.py PHASE 1c `free_loopback_port`)'
)

_CROSS_TALK_REMEDY = (
    'siblings Init\'d, each seeded its own probe row, and one read back a sibling\'s row or lost its own: they share one '
    'store, so the final run\'s trials overwrite each other. Partition it per conversation — key every record seed_state '
    'creates by `plurai.active_session_id()`, or give each child its own account or namespace (seed-state-contract '
    '"partition before rewriting"; example_simulate.py PHASE 1c); else declare its copies as `world_state.slots`'
)

_EXCLUSIVE_UNNEEDED_REMEDY = (
    'siblings Init\'d, seeded and read back their own rows side by side, so the declaration serializes a resource they did '
    'not collide on: the final run then holds every conversation behind one agent process. Drop it, unless the resource is '
    'taken only after Init (on a turn)'
)


class _SiblingObservation(NamedTuple):
    """What the sibling probe saw: ``kind`` is ``init_failed`` (one child's Init raised beside its siblings),
    ``cross_talk`` (a child read a sibling's probe row or lost its own), ``isolated`` (each read back its own and no
    sibling's), or ``unmeasured``; ``exc_class`` + ``detail`` name the first raise or the cross-talk."""

    kind: str
    exc_class: str = ''
    detail: str = ''


#: A copied row's display-name fields: a store that keeps names unique (a registry, a unique index) refuses a second
#: row under the first one's name, so the probe row carries a generated one.
_NAME_FIELDS = frozenset({'name', 'title', 'label', 'display_name', 'friendly_name'})


def _marked_worlds(world: dict, width: int, world_types: tuple[str, ...] = ()) -> list[tuple[dict, str]] | None:
    """One ``(world, mark)`` per probe child: the smoke world plus a copy of one of its entities — the first of a declared
    store type (``world_types``), else the first — under the id ``<id>-plurai-sib<k>``. Fields that held the id hold
    the mark and its name fields (:data:`_NAME_FIELDS`, ``*_name``) carry the suffix too, so the copy is a distinct row
    of the same type. Every sibling seeds the same scenario rows, as the final run's trials do, plus one row only it
    owns. A world with no entity gets a bare row of the first declared store type instead (``probe-plurai-sib<k>``).
    ``None`` with neither."""
    entities = world.get('entities') if isinstance(world, dict) else None
    if not isinstance(entities, dict) or not entities:
        if not world_types:
            return None
        entities = {'probe': {'_entity_type': world_types[0], 'id': 'probe'}}
        world = {**world, 'entities': {}}
    typed = [(eid, row) for eid, row in entities.items() if isinstance(row, dict) and row.get('_entity_type') in world_types]
    first_id, first = typed[0] if typed else next(iter(entities.items()))
    if not isinstance(first_id, str) or not isinstance(first, dict):
        return None
    marked = []
    for k in range(width):
        suffix = f'{_SIBLING_MARK}{k}'
        mark = f'{first_id}{suffix}'
        row = {
            field: mark if value == first_id else f'{value}{suffix}' if isinstance(value, str) and _is_name_field(field) else value
            for field, value in first.items()
        }
        marked.append(({**world, 'entities': {**world['entities'], mark: row}}, mark))
    return marked


def _is_name_field(field: object) -> bool:
    return isinstance(field, str) and (field.lower() in _NAME_FIELDS or field.lower().endswith('_name'))


def _slot_envs(simulate_path: Path, width: int) -> list[dict[str, str]] | None:
    """Each probe child's environment on a slotted bench: the runner's launch environment plus ``PLURAI_WORLD_SLOT=<k>``.
    ``None`` on an SDK without that environment builder (the copies cannot be told apart)."""
    try:
        from plurai.child_env import launch_environment  # noqa: PLC0415 — deferred, as in _run_smoke
    except Exception:  # noqa: BLE001 — an older SDK: unmeasured, never a guess
        return None
    return [launch_environment(simulate_path, extra={'PLURAI_WORLD_SLOT': str(k)}) for k in range(width)]


async def _read_world(child: Any) -> str | None:
    """The child's world as ``finalize`` reads it (``world_snapshot``), serialized; ``None`` when it could not be read."""
    try:
        result = await child.finalize()
    except Exception:  # noqa: BLE001 — a read that raised measures nothing here
        return None
    world = result[0] if isinstance(result, tuple) and result else None
    return None if world is None else json.dumps(world, default=str, sort_keys=True)


async def _sibling_children(
    simulate_path: Path, worlds: list[dict], values: dict, envs: list[dict[str, str]] | None, *, read_back: bool
) -> tuple[tuple[str, str] | None, list[str | None]]:
    """Init one child per world at once, as a final run's parallel trials do, all held until all answered; then, when
    ``read_back``, read each child's world after every sibling seeded. ``((class, error), [])`` of the first that
    failed, its stderr tail appended; else ``(None, the read-backs)``."""
    import contextlib  # noqa: PLC0415

    from plurai._subprocess import ChildProcess  # noqa: PLC0415 — deferred, as in _run_smoke

    capture = _StderrCaptureHandler()
    sdk_logger = logging.getLogger('plurai._subprocess')
    sdk_logger.addHandler(capture)
    sdk_logger.setLevel(logging.DEBUG)
    snapshots: list[str | None] = []
    try:
        async with contextlib.AsyncExitStack() as stack:
            children = [
                await stack.enter_async_context(ChildProcess(simulate_path) if envs is None else ChildProcess(simulate_path, env=envs[n]))
                for n in range(len(worlds))
            ]
            inits = (
                child.init(
                    thread_id=f'smoke-sibling-{n}', mock_service_url='http://localhost:8199', mock_mode='local',
                    initial_world_state=worlds[n], initial_agent_context=values,
                    scenario_metadata={'scenario_name': 'smoke'},
                )
                for n, child in enumerate(children)
            )
            errors = [r for r in await asyncio.gather(*inits, return_exceptions=True) if isinstance(r, Exception)]
            if not errors and read_back:
                snapshots = [await _read_world(child) for child in children]
    except Exception as exc:  # noqa: BLE001 — a child that could not start is a sibling failure too
        errors = [exc]
    finally:
        sdk_logger.removeHandler(capture)
    if not errors:
        return None, snapshots
    tail = [line for line in capture.buffer if line.strip()][-5:]
    return (getattr(errors[0], 'child_exc_class', None) or type(errors[0]).__name__, _one_line(' | '.join([str(errors[0]), *tail]), 600)), []


def _read_back_observation(snapshots: list[str | None], marks: list[str]) -> _SiblingObservation:
    """Each child's read-back against the probe rows: one that holds a sibling's row, or lost its own while a sibling
    kept theirs, is cross-talk; all holding their own and none a sibling's is isolation; anything else (an unread world,
    a harness that seeds or reads no copied row) measured nothing."""
    if any(snapshot is None for snapshot in snapshots):
        return _SiblingObservation('unmeasured')
    # A store may keep the probe id in its own spelling (`Downstairs-plurai-sib0` -> `downstairs_plurai_sib0`): both
    # sides are compared as lowercase letters and digits only.
    snapshots = [_squeezed(snapshot) for snapshot in snapshots]
    marks = [_squeezed(mark) for mark in marks]
    own = [mark in snapshot for snapshot, mark in zip(snapshots, marks) if snapshot is not None]
    for k, snapshot in enumerate(snapshots):
        read = [j for j, mark in enumerate(marks) if j != k and snapshot is not None and mark in snapshot]
        if read:
            return _SiblingObservation('cross_talk', detail=f'sibling {k} read back sibling {read[0]}\'s probe row')
    if all(own):
        return _SiblingObservation('isolated')
    if any(own):
        return _SiblingObservation('cross_talk', detail=f'sibling {own.index(False)} lost its own probe row while a sibling kept theirs')
    return _SiblingObservation('unmeasured')


def _squeezed(text: str | None) -> str | None:
    return None if text is None else re.sub(r'[^a-z0-9]', '', text.lower())


async def _sibling_probe(simulate_path: Path, world: dict, values: dict, declared: _SiblingDeclaration) -> _SiblingObservation:
    """Init + seed + read-back, :data:`_SIBLING_CHILDREN` children at once (one per world copy on a slotted bench, at
    most that many), each seeding the smoke world plus a probe row only it owns (:func:`_marked_worlds`). When the
    marked siblings raise, one child Inits ALONE on its marked world: alone clean, the raise was a collision; alone
    refused, the harness refuses the extra row, and the plain siblings decide instead."""
    width = min(_SIBLING_CHILDREN, declared.slots) if declared.slots else _SIBLING_CHILDREN
    envs = _slot_envs(simulate_path, width) if declared.slots else None
    if declared.slots and envs is None:
        return _SiblingObservation('unmeasured')
    marked = _marked_worlds(world, width, declared.world_types)
    worlds = [w for w, _ in marked] if marked is not None else [world] * width
    failure, snapshots = await _sibling_children(simulate_path, worlds, values, envs, read_back=marked is not None)
    if failure is not None and marked is not None:
        alone, _ = await _sibling_children(simulate_path, worlds[:1], values, envs[:1] if envs else None, read_back=False)
        if alone is not None:
            failure, _ = await _sibling_children(simulate_path, [world] * width, values, envs, read_back=False)
            if failure is None:
                return _SiblingObservation('unmeasured')
    if failure is not None:
        return _SiblingObservation('init_failed', *failure)
    if marked is None:
        return _SiblingObservation('unmeasured')
    return _read_back_observation(snapshots, [mark for _, mark in marked])


def _sibling_finding(declared: _SiblingDeclaration, observed: _SiblingObservation, file: str) -> SmokeFailure | None:
    """The declared x observed outcome, a warning or nothing:

    * declared exclusive and collided (Init or cross-talk): the declaration is confirmed — nothing;
    * declared exclusive and isolated: ``exclusive_unneeded``;
    * declared world copies (``slots`` 2+) that collided: ``sibling_cross_talk`` — the copies are not separate;
    * undeclared and an Init collided: ``sibling_init_failed``; undeclared cross-talk: ``sibling_cross_talk``;
    * isolated and undeclared, or unmeasured: nothing.
    """
    collided = observed.kind in ('init_failed', 'cross_talk')
    if declared.exclusive:
        if observed.kind != 'isolated':
            return None
        names = ', '.join(declared.exclusive)
        line = f'⚠ sibling isolation: {_SIBLING_CHILDREN} children ran isolated beside {names} — {_EXCLUSIVE_UNNEEDED_REMEDY}.'
        return SmokeFailure(SmokeGate.EXCLUSIVE_UNNEEDED, line, file, {'declared': names})
    if not collided:
        return None
    if observed.kind == 'init_failed' and not declared.slots:
        line = f"⚠ sibling init: {_SIBLING_CHILDREN} children Init'd at once and one raised {observed.exc_class} — {_SIBLING_REMEDY}."
        return SmokeFailure(SmokeGate.SIBLING_INIT_FAILED, line, file, {'child_error': observed.detail})
    where = f'on {declared.slots} declared world copies (one PLURAI_WORLD_SLOT each)' if declared.slots else 'side by side'
    how = f'one Init raised {observed.exc_class}' if observed.kind == 'init_failed' else 'one read back what was not its own'
    line = f'⚠ sibling cross-talk: children {where}: {how} — {_CROSS_TALK_REMEDY}.'
    return SmokeFailure(SmokeGate.SIBLING_CROSS_TALK, line, file, {'observed': observed.detail})


async def _observed(witness: _EntryWitness, turn: Awaitable[Any]) -> Any:
    return witness.observe(await turn)


#: The pipeline's own request lane, named in its delivery remedy, and its line when no read record could be taken.
_PIPELINE_LANE = 'the invoke_pipeline inputs'
_UNRECORDED_DELIVERY = '~ input delivery: not_run — the child answered no Finalize, so no read record was taken.'
#: Where `--pipeline` reads its worlds: the manifest `bench_manifest.py` keeps beside simulate.py.
_MANIFEST_FILENAME = 'bench_manifest.json'
_PIPELINE_TIMEOUT_S = 600.0


def _smoke_inputs(simulate_path: Path) -> tuple[list[dict] | None, str]:
    """``(entries, source)`` — or ``(None, why)`` when no typed ``agent.smoke_inputs`` can be read."""
    path = simulate_path.parent / _MANIFEST_FILENAME
    try:
        entries = json.loads(path.read_text(encoding='utf-8'))['agent']['smoke_inputs']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f'{path} carries no readable agent.smoke_inputs ({type(exc).__name__}: {exc})'
    if not isinstance(entries, list) or not entries or not all(
        isinstance(e, dict) and isinstance(e.get('world'), dict) and isinstance(e.get('inputs'), dict) for e in entries
    ):
        return None, f'{path}: agent.smoke_inputs is not a non-empty list of {{world, inputs}} objects'
    return entries, str(path)


def _smoke_envelope(simulate_path: Path, request_block: RequestBlock) -> tuple[dict, RequestBlock] | None:
    """``(world, request block)`` of the first declared ``agent.smoke_inputs`` entry — its inputs merged OVER
    ``request_block``, so a key the entry does not name is still delivered — or None when none is declared.
    The world a mocked harness's own turn 1 opens in: the turn smoke and the seed-resume probe both Init there."""
    entries, _why = _smoke_inputs(simulate_path)
    if not entries:
        return None
    merged = RequestBlock({**request_block.values, **entries[0]['inputs']}, f'{request_block.source}, with agent.smoke_inputs[0] inputs merged over it')
    return entries[0]['world'], merged


async def _run_pipeline_smoke(simulate_path: Path, witness: _EntryWitness | None = None) -> tuple[int, str]:
    """One ``invoke_pipeline`` per declared world, each on its own child: the world is an Init fact.

    ``mock_mode='local'`` for the ``_run_smoke`` reason, and the inputs ride Init AND the invoke,
    exactly as the SDK runner hands them (``orchestrator.py``'s one-shot hop). The first world that
    raises is the verdict, carried by the child's own exception class, never by prose.
    """
    if simulate_path.suffix != '.py':
        return (2, f'✗ pipeline smoke: NO VERDICT — this build drives invoke_pipeline only through a Python harness; {simulate_path.name} was not run.')
    entries, source = _smoke_inputs(simulate_path)
    if entries is None:
        return (2, f'✗ pipeline smoke: NO VERDICT — {source}. Nothing was run.')
    from plurai._protocol import AgentTurnTimeout, HarnessChildExited  # noqa: PLC0415 — the reaped-child types, deferred likewise
    from plurai._subprocess import (  # noqa: PLC0415 — deferred, as in _run_smoke
        ChildProcess,
        OrchestratorAdapterError,
    )

    # Every request key rides Init and the invoke; the SDK records which ones the harness READ, and a
    # returned PipelineError leaves the child alive, so the record is taken whichever way the pass ended.
    store_only = _planted_request_block(simulate_path)[1]
    render_verbs = _declared_render_surface(simulate_path).verbs
    notes: list[str] = []
    #: An unread key is recorded and the remaining worlds still run: a non-holding finding never cuts the pass.
    measured: list[SmokeFailure] = []
    capture = _StderrCaptureHandler()
    capture.setLevel(logging.DEBUG)
    sdk_logger = logging.getLogger('plurai._subprocess')
    sdk_logger.addHandler(capture)
    sdk_logger.setLevel(logging.DEBUG)
    try:
        for index, entry in enumerate(entries, start=1):
            where = f'world {index} of {len(entries)} ({len(entry["world"].get("entities") or {})} entities)'
            keys = sorted(set(map(str, entry['inputs'])) - store_only)
            try:
                async with ChildProcess(simulate_path) as child:
                    await child.init(
                        thread_id=f'smoke-pipeline-{index}',
                        mock_service_url='http://localhost:8199',
                        mock_mode='local',
                        initial_world_state=entry['world'],
                        initial_agent_context=entry['inputs'],
                        scenario_metadata={'scenario_name': 'smoke'},
                        **(witness.init_kwargs(ChildProcess.init) if witness is not None else {}),
                    )
                    try:
                        result = await child.pipeline_invoke(entry['inputs'], timeout=_PIPELINE_TIMEOUT_S)
                    except (AgentTurnTimeout, HarnessChildExited):
                        if keys:  # reaped: a dead child answers no Finalize
                            notes.append(_UNRECORDED_DELIVERY)
                        raise
                    except OrchestratorAdapterError:
                        if keys:
                            try:
                                await child.finalize()
                            except (OrchestratorAdapterError, RuntimeError):
                                notes.append(_UNRECORDED_DELIVERY)
                            else:
                                notes.append(_input_delivery(
                                    simulate_path, keys, getattr(child, 'unread_inputs', None), store_only, lane=_PIPELINE_LANE, preempted=True,
                                )[0])
                        raise
                    if witness is not None:
                        witness.observe(result)
                    _record_trail_names(simulate_path, _trail_returned_entries([getattr(result, 'outputs', None)]), render_verbs, measured, notes)
                    if keys:
                        await child.finalize()
                        note, failing = _input_delivery(
                            simulate_path, keys, getattr(child, 'unread_inputs', None), store_only, lane=_PIPELINE_LANE, preempted=False,
                        )
                        if failing is not None:
                            notes.append(f'{note} ({where})')
                            measured.append(SmokeFailure(SmokeGate.REQUEST_KEY_UNREAD, failing, simulate_path.name, {'where': where}))
                        elif note not in notes:
                            notes.append(note)
            except OrchestratorAdapterError as exc:
                if getattr(exc, 'timed_out', False) or getattr(exc, 'budget_seconds', None) is not None:
                    verdict = f'✗ pipeline smoke: NO VERDICT — {where} did not finish within its budget ({str(exc)[:200]}).'
                    return (2, '\n'.join([*notes, verdict]))
                exc_class = getattr(exc, 'child_exc_class', None)
                if not exc_class:
                    return _with_measured(_format_failure(str(exc), capture.buffer, source=f'adapter_error, {where}', notes=notes, simulate_path=simulate_path), measured, notes)
                message = str(getattr(exc, 'child_message', None) or exc)[:300]
                owner, why = _attributed_owner(exc, capture.buffer, simulate_path)
                if owner == 'infra':
                    return (2, '\n'.join([*notes, f'✗ pipeline smoke: NO VERDICT — {where}: {_provider_refused(exc_class, message)}']))
                subject = 'invoke_pipeline' if owner is None else f'the {owner}'
                verdict = f'✗ pipeline smoke FAILED — {where}: {subject} raised {exc_class}{f" ({why})" if why else ""}: {message}'
                observed = {'where': where, 'error': message, **({'owner_evidence': why} if why else {})}
                raised = {'message': f'{subject} raised {exc_class}', 'file': simulate_path.name, 'observed': observed}
                if owner == 'agent':
                    return _with_measured(_failed(AGENT_RAISED_EXIT, '\n'.join([*notes, verdict]), SmokeGate.AGENT_RAISED, **raised), measured, notes)
                return _with_measured(_failed(1, '\n'.join([*notes, verdict]), SmokeGate.HARNESS_RAISED, **raised), measured, notes)
            except RuntimeError as exc:
                return _with_measured(_format_failure(str(exc), capture.buffer, source=f'runtime_error, {where}', notes=notes, simulate_path=simulate_path), measured, notes)
    finally:
        sdk_logger.removeHandler(capture)
    if any(f.gate not in _WARNING_GATES for f in measured):
        return _with_measured((0, ''), measured, notes)
    ok = f'✓ pipeline smoke OK — invoke_pipeline completed {len(entries)} world(s) on the local mock'
    if not any(e['world'].get('entities') for e in entries):
        ok += ' (⚠ every declared world is empty, so a pipeline that skips its work on empty input was never exercised past that branch)'
    return Verdict(0, _standalone_last_line('\n'.join([ok, *notes, *(f.message for f in measured)]), ok), measured)


#: Where the seed-world verdict routes a failure: the env's schema and the harness's read path disagree.
_SEED_WORLD_REMEDY = 'route this error text to /edit-env: a world scenario generation may produce is one the harness cannot seed'


def _declares_seed_state(simulate_path: Path) -> bool:
    """Whether simulate.py may bind ``seed_state`` at module scope: a def, an assignment, an import of it,
    or a ``from x import *`` (the SDK finds a star-imported hook by ``getattr``, so it cannot be ruled out)."""
    tree = ast.parse(simulate_path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'seed_state':
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any((alias.asname or alias.name) in ('seed_state', '*') for alias in node.names):
            return True
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == 'seed_state' for t in targets):
            return True
    return False


async def _seed_init(
    simulate_path: Path, world_state: dict, agent_context: dict, thread_id: str, resume_seed: dict | None = None, reads: list | None = None,
    reports: list | None = None,
) -> tuple[BaseException | None, list[str]]:
    """One Init on the local mock: ``(the raise, or None; the child's stderr lines)``. An accepted Init appends the
    child's post-seed read (``ChildProcess.post_seed_world_state``; ``None`` when there is no read path) to ``reads``,
    and Init's own answer (the harness's seed report, its resume fidelity) to ``reports``."""
    from plurai._subprocess import ChildProcess, OrchestratorAdapterError  # noqa: PLC0415 — deferred, as in _run_smoke

    capture = _StderrCaptureHandler()
    capture.setLevel(logging.DEBUG)
    sdk_logger = logging.getLogger('plurai._subprocess')
    sdk_logger.addHandler(capture)
    sdk_logger.setLevel(logging.DEBUG)
    try:
        async with ChildProcess(simulate_path) as child:
            report = await child.init(
                thread_id=thread_id,
                mock_service_url='http://localhost:8199',
                mock_mode='local',
                initial_world_state=world_state,
                initial_agent_context=agent_context,
                scenario_metadata={'scenario_name': 'smoke'},
                **({'resume_seed': resume_seed} if resume_seed is not None else {}),
            )
            if reports is not None:
                reports.append(report)
            if reads is not None:
                reads.append(getattr(child, 'post_seed_world_state', None))
    except (OrchestratorAdapterError, RuntimeError) as exc:
        return exc, list(capture.buffer)
    finally:
        sdk_logger.removeHandler(capture)
    return None, list(capture.buffer)


def _one_line(text: object, limit: int) -> str:
    """``text`` on one line, its TAIL kept: a load failure's exception line comes after its frames."""
    flat = ' '.join(str(text).split())
    return flat if len(flat) <= limit else '…' + flat[-limit:]


def _host_interception(simulate_path: Path) -> dict | None:
    """The ``harness.tool_interception`` block of the ``bench_manifest.json`` beside simulate.py iff it
    declares ``at: agent_host``; absent or unreadable means the harness intercepts. Restates
    ``bench_manifest.host_interception``, since this file runs standing alone."""
    try:
        doc = json.loads((simulate_path.parent / _MANIFEST_FILENAME).read_text(encoding='utf-8'))
        block = (doc.get('harness') or {}).get('tool_interception')
    except (OSError, ValueError, AttributeError):
        return None
    return block if isinstance(block, dict) and block.get('at') == 'agent_host' else None


def _resume_unsupported(simulate_path: Path) -> bool:
    """Whether the ``bench_manifest.json`` beside simulate.py declares ``harness.resume.mode: unsupported`` on a dialog
    bench; absent or unreadable means it can resume. Restates ``regressions.resume_unsupported``, since this file runs
    standing alone."""
    try:
        doc = json.loads((simulate_path.parent / _MANIFEST_FILENAME).read_text(encoding='utf-8'))
        dialog = str((doc.get('agent') or {}).get('dispatch_shape') or 'dialog') == 'dialog'
        resume = (doc.get('harness') or {}).get('resume')
    except (OSError, ValueError, AttributeError):
        return False
    return dialog and isinstance(resume, dict) and resume.get('mode') == 'unsupported'


def _host_route_line(hosted: dict, label: str, why_not_here: str, *, refused: bool = False) -> str:
    """The one line every mode prints on a host-intercepted bench, naming the route's own smoke last: ``not_applicable``,
    or ``NO VERDICT`` (``refused``) for a mode whose caller would read a skip as the harness's answer."""
    return (
        f"{f'✗ {label}: NO VERDICT' if refused else f'~ {label}: not_applicable'} — the mocked tools are intercepted at the agent's host "
        f"({hosted.get('why') or 'the manifest declares no `why`'}): {why_not_here}. This route's own smoke owns it: "
        f"{hosted.get('route_smoke') or 'the manifest declares no `route_smoke`'}"
    )


def _harness_string_constants(simulate_path: Path) -> frozenset[str] | None:
    """Every string literal in simulate.py and the local helpers it imports; None when simulate.py does not parse."""
    names: set[str] = set()
    for path in _harness_files(simulate_path):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, ValueError):
            if path == simulate_path:
                return None
            continue
        names.update(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str))
    return frozenset(names)


async def _run_host_route(simulate_path: Path, hosted: dict) -> Verdict | tuple[int, str]:
    """The Init smoke (and ``--turn`` / ``--pipeline``) on a bench whose tools are intercepted at the agent's host.

    An Init there opens the hosted route — a session outside any run, metered when the route is up, and
    refused by the harness when it is down — so no child is driven. What the step can still answer is
    whether the request block a real scenario carries reaches the agent: on this route the harness forwards
    only the keys it maps, so each key of the block (a fetched slot's, else one per declared request key)
    must be NAMED in the harness source. A key it never names is ``request_key_unread`` (a lead, holding
    nothing, as under ``--turn``); a simulate.py that does not parse is its ``syntax_error``.
    """
    skipped = _host_route_line(hosted, 'smoke', 'an Init here would open that route outside any run, so no child was driven')
    if simulate_path.suffix != '.py':
        return (0, '\n'.join([f'⚠ request block (static): not checked — this build reads only a Python harness, not {simulate_path.name}.', skipped]))
    request_block, store_only = _init_request_block(simulate_path)
    if not request_block.values:
        return (0, '\n'.join([f'⚠ request block (static): EMPTY — {request_block.source}.', skipped]))
    named = _harness_string_constants(simulate_path)
    if named is None:
        broken = f'✗ smoke FAILED — {simulate_path.name} does not parse, so nothing it declares can run on any route.'
        return _failed(1, '\n'.join([skipped, broken]), Category.SYNTAX_ERROR, message=broken, file=simulate_path.name, observed={})
    keys = sorted(map(str, request_block.values))
    unnamed = [key for key in keys if key not in named and key not in store_only]
    if not unnamed:
        return (0, '\n'.join([f'✓ request block (static): {simulate_path.name} names every key ({", ".join(keys)}) from {request_block.source}.', skipped]))
    failing = (
        f'✗ request block (static): {simulate_path.name} never names {", ".join(unnamed)} (from {request_block.source}), so on this '
        f'route the agent is never handed it — map each key to what the harness sets on the hosted session, or drop it from agent_context.'
    )
    return _failed(1, '\n'.join([skipped, failing]), SmokeGate.REQUEST_KEY_UNREAD, message=failing, file=simulate_path.name, observed={})


async def _run_seed_world(simulate_path: Path, world_path: Path) -> tuple[int, str]:
    """Init the harness with the env's complete world; ``seed_state`` must accept it.

    The world is ``EnvDetail.seed_probe_world``: every field the env's schema declares, each row list
    holding a row with every declared row field. A raise inside ``seed_state`` is exit 1, the child's
    exception verbatim, when the world completes a smoke scenario (``grounded_on``); over a world from
    the schema alone it is exit 2, advisory: such a world can break a link only prose declares.

    A grounded world that passes is Init'd once more as its ``perturbed_world_state`` twin (every row
    value replaced by the schema's own): a raise there is exit 2, advisory, never a block. It exists so
    a base value the harness COERCES cannot hide a type mismatch every generated scenario would hit.

    A harness with no ``seed_state``, an env with no world schema (``null``), or a bench whose tools are
    intercepted at the agent's host (its world reaches the agent through the mock service, and an Init
    would open that route) is not_applicable (exit 0), with nothing run; a world the backend could not
    build, or a harness that did not load, is NO VERDICT (exit 2).

    A grounded refusal blocks only when the Init smoke's own envelope (its request block and that block's
    slot world, else an empty one) Inits clean: a raise that envelope repeats is a missing run prerequisite, not the world (exit 2).
    """
    if simulate_path.suffix != '.py':
        return (2, f'✗ seed-world: NO VERDICT — this build drives seed_state only through a Python harness; {simulate_path.name} was not run.')
    if world_path.is_dir():
        return await _run_seed_worlds(simulate_path, world_path)
    try:
        world = json.loads(world_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return (2, f'✗ seed-world: NO VERDICT — {world_path} is not readable JSON ({type(exc).__name__}: {exc}). Nothing was run.')
    if world is None:
        return (0, '~ seed-world: not_applicable — the env has no world schema, so no world reaches the harness at Init.')
    if not isinstance(world, dict) or world.get('error') or not isinstance(world.get('initial_world_state'), dict):
        why = world.get('error') if isinstance(world, dict) and world.get('error') else 'it carries no initial_world_state'
        return (2, f'✗ seed-world: NO VERDICT — the backend published no world to seed ({why}). Nothing was run.')
    if not _declares_seed_state(simulate_path):
        return (0, f'~ seed-world: not_applicable — {simulate_path.name} defines no seed_state, so no world reaches the harness at Init.')
    # Decided before any Init, the empty-world control included: on this route an Init opens the hosted session.
    if (hosted := _host_interception(simulate_path)) is not None:
        return (0, _host_route_line(hosted, 'seed-world', 'the world reaches the agent through the mock service, not seed_state, and an Init here would open that route'))
    if (landing := _seed_landing()) is None:
        return (2, _NO_COMPARATOR)
    try:
        from plurai._protocol import HarnessChildExited  # noqa: PLC0415 — the reaped-child type, deferred likewise
    except ImportError:  # an SDK that predates it types no child death
        HarnessChildExited = ()  # type: ignore[assignment,misc]  # noqa: N806

    def unloaded(exc: BaseException) -> str | None:
        """The NO VERDICT line when the child never answered Init: seed_state was never reached."""
        category, hint = _classify(str(exc), None, simulate_path)
        if not isinstance(exc, HarnessChildExited) and category == Category.OTHER:
            return None
        why = hint if category != Category.OTHER else 'the child died before answering Init — run the Init smoke (no flag) and fix what it names'
        return f'✗ seed-world: NO VERDICT — the harness did not load, so seed_state never ran (category={category}: {why}). Child error: {_one_line(exc, 400)}'

    source = str(world.get('source') or 'the env')
    context = world.get('initial_agent_context') or {}
    entities = world['initial_world_state'].get('entities') or {}
    # D5's baseline: what an EMPTY world reads before the probe world was ever seeded. Its raise is not this step's verdict.
    before: list = []
    await _seed_init(simulate_path, {}, context, 'smoke-seed-world-reset-before', reads=before)
    reads: list = []
    exc, stderr = await _seed_init(simulate_path, world['initial_world_state'], context, 'smoke-seed-world', reads=reads)
    if exc is not None:
        if (line := unloaded(exc)) is not None:
            return (2, line)
        tail = [f'    {line[:200]}' for line in stderr[-5:]]
        stderr_tail = ['  child stderr (tail):', *tail] if tail else []
        if not world.get('grounded_on'):
            # A world from the schema alone can break a link only prose declares, which every real world keeps.
            head = f'~ seed-world: NO VERDICT (advisory) — seed_state refused a world built from the schema alone ({source})'
            recap = f'{head}: {_one_line(exc, 450)} — a schema-alone world is not trusted to block; read the error'
            return (2, _standalone_last_line('\n'.join([f'{head}: {exc}', *stderr_tail]), recap))
        # The control: the Init smoke's own envelope, its request block with that block's slot world (else an
        # empty one). A raise it repeats is not about this world, and the Init smoke (no flag) names it.
        block = _init_request_block(simulate_path)[0]
        control, _ = await _seed_init(simulate_path, block.world or {}, block.values, 'smoke-seed-world-control')
        if control is not None:
            control_world = "the fetched slot's own world" if block.world else 'an empty world'
            head = f'✗ seed-world: NO VERDICT — Init fails on {control_world} too'
            recap = (
                f'{head} ({_one_line(control, 300)}), so the refusal is not about this world: a run prerequisite is missing. '
                f'Fix what the Init smoke (no flag) names, then re-run this step.'
            )
            return (2, '\n'.join([f'{head}; the complete world ({source}) raised: {exc}', *stderr_tail, recap]))
        head = f'✗ seed-world FAILED — seed_state refused the complete world ({source})'
        recap = f'{head}: {_one_line(exc, 450)} — {_SEED_WORLD_REMEDY}'
        return _failed(
            1, _standalone_last_line('\n'.join([f'{head}: {exc}', *stderr_tail]), recap), SmokeGate.SEED_WORLD_REFUSED,
            message=f'seed_state refused the complete world ({source}) with {getattr(exc, "child_exc_class", None) or type(exc).__name__}',
            file=simulate_path.name, observed={'error': _one_line(exc, 450)},
        )
    accepted = f'✓ seed accepted — seed_state accepted the complete world ({source}; {len(entities)} entities)'
    readback = landing.seed_readback(post_seed_world_state=reads[0] if reads else None, scenario_seed=world['initial_world_state'])
    if readback.verdict == 'no_read_path':
        return (2, '\n'.join([accepted, _NO_READ_PATH]))
    leftover = await _reset_leftover(simulate_path, landing, before, entities, context) if readback.verdict != 'read_failed' else None
    if readback.verdict != 'landed':
        lines = [accepted]
        gap, recap = _readback_failure(readback, f'the complete world ({source})', world.get('completed_fields'), simulate_path, lines)
        lines.extend([leftover.message] if leftover else [])
        return Verdict(1, _standalone_last_line('\n'.join(lines), recap), [gap, *([leftover] if leftover else [])])
    ok = f'✓ seed read-back OK — {readback.line()}'
    if leftover is not None:
        return Verdict(1, '\n'.join([accepted, ok, leftover.message]), [leftover])
    twin = world.get('perturbed_world_state')
    if not world.get('grounded_on') or not isinstance(twin, dict):
        return (0, f'{accepted}\n{ok}')
    exc, _ = await _seed_init(simulate_path, twin, context, 'smoke-seed-world-perturbed')
    if exc is None:
        return (0, f'{accepted}; its perturbed twin (the schema\'s own row values) was accepted too\n{ok}')
    advisory = (
        f'~ seed-world perturbed: NO VERDICT (advisory) — seed_state accepted the smoke-grounded world but refused the same '
        f'world with the schema\'s own row values: {_one_line(exc, 500)}. A smoke value the harness coerces may be hiding a '
        f'row type the schema declares otherwise; read the error before trusting the rows.'
    )
    return (2, '\n'.join([accepted, ok, advisory]))


def _scenario_files(files: dict) -> tuple[dict, dict | None]:
    """``({initial_world_state, initial_agent_context}, resume_seed)`` off a raw ``get_scenario`` payload's ``files``: its
    ``initial_world_state.yaml``, its ``agent_context.yaml`` whole (``{}`` when absent) and its ``resume_seed.yaml`` (None
    when absent), each YAML-parsed. Raises ValueError naming the file that does not parse to a mapping, or the
    ``resume_seed`` this SDK's Init cannot take."""
    import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml
    from plurai._subprocess import ChildProcess  # noqa: PLC0415 — deferred, as in _seed_init

    parsed: dict[str, dict | None] = {}
    for name in ('initial_world_state.yaml', 'agent_context.yaml', 'resume_seed.yaml'):
        text = files.get(name)
        try:
            value = yaml.safe_load(text) if isinstance(text, str) else None
        except yaml.YAMLError as exc:
            raise ValueError(f'its files[{name!r}] is not readable YAML ({type(exc).__name__})') from exc
        if (value is not None and not isinstance(value, dict)) or (value is None and name == 'initial_world_state.yaml'):
            raise ValueError(f'its files[{name!r}] is not a mapping')
        parsed[name] = value
    if parsed['resume_seed.yaml'] is not None and 'resume_seed' not in inspect.signature(ChildProcess.init).parameters:
        raise ValueError('it carries a resume_seed.yaml this plurai build\'s Init cannot take, so its world would be read back without it')
    return {'initial_world_state': parsed['initial_world_state.yaml'], 'initial_agent_context': parsed['agent_context.yaml'] or {}}, parsed['resume_seed.yaml']


async def _run_seed_worlds(simulate_path: Path, folder: Path) -> Verdict | tuple[int, str]:
    """``--seed-world <dir>``: each ``*.json`` in ``folder`` is one drawn scenario's world (its ``initial_world_state``
    and ``initial_agent_context``, top level or under ``injected_inputs``; or a raw ``get_scenario`` payload, bare or
    as the sole value of a ``{"result": …}`` envelope, whose ``files`` carry them as YAML: :func:`_scenario_files`),
    Init'd once and read back. No model is called.

    Run over the drawn FINAL set before the final run, it finds the seeds a run would void while nothing is paid for. A
    world the harness refuses is ``seed_world_refused``; one not read back as seeded is ``seed_readback_gap`` (both
    advisory and holding). A world with no read path, or no world at all, is NO VERDICT (exit 2) when no other verdict
    stands."""
    if not _declares_seed_state(simulate_path):
        return (0, f'~ seed-world: not_applicable — {simulate_path.name} defines no seed_state, so no world reaches the harness at Init.')
    if (hosted := _host_interception(simulate_path)) is not None:
        return (0, _host_route_line(hosted, 'seed-world', 'the world reaches the agent through the mock service, not seed_state, and an Init here would open that route'))
    if (landing := _seed_landing()) is None:
        return (2, _NO_COMPARATOR)
    lines: list[str] = []
    failures: list[SmokeFailure] = []
    recaps: list[str] = []
    landed = 0
    unjudged = 0
    for path in sorted(folder.glob('*.json')):
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            lines.append(f'~ {path.name}: NO VERDICT — not readable JSON ({type(exc).__name__})')
            unjudged += 1
            continue
        if isinstance(doc, dict) and list(doc) == ['result'] and isinstance(doc['result'], dict):
            doc = doc['result']  # the MCP tool-result envelope a session writes get_scenario's JSON in, as returned
        carrier = doc.get('injected_inputs') if isinstance(doc, dict) and isinstance(doc.get('injected_inputs'), dict) else doc
        world = carrier.get('initial_world_state') if isinstance(carrier, dict) else None
        resume_seed = None
        files = doc.get('files') if isinstance(doc, dict) else None
        if not isinstance(world, dict) and isinstance(files, dict) and isinstance(files.get('initial_world_state.yaml'), str):
            try:
                carrier, resume_seed = _scenario_files(files)
            except ValueError as exc:
                lines.append(f'~ {path.name}: NO VERDICT — {exc}')
                unjudged += 1
                continue
            world = carrier['initial_world_state']
        if not isinstance(world, dict) or ('agent_context' in carrier and 'initial_agent_context' not in carrier):
            why = 'it carries no initial_world_state' if not isinstance(world, dict) else 'it names agent_context, which Init never reads'
            lines.append(f'~ {path.name}: NO VERDICT — {why}; {_RAW_PAYLOAD}')
            unjudged += 1
            continue
        reads: list = []
        exc, _ = await _seed_init(simulate_path, world, carrier.get('initial_agent_context') or {}, f'smoke-seed-world-{path.stem}',
                                  resume_seed=resume_seed, reads=reads)
        if exc is not None:
            line = f'✗ {path.name}: seed_state refused this drawn world: {_one_line(exc, 300)}'
            lines.append(line)
            recaps.append(line)
            refused = f'seed_state refused the drawn world {path.name} with {getattr(exc, "child_exc_class", None) or type(exc).__name__}'
            failures.append(SmokeFailure(SmokeGate.SEED_WORLD_REFUSED, refused, simulate_path.name, {'error': _one_line(exc, 450)}))
            continue
        readback = landing.seed_readback(post_seed_world_state=reads[0] if reads else None, scenario_seed=world)
        if readback.verdict == 'landed':
            landed += 1
            lines.append(f'✓ {path.name}: seed accepted and read back as seeded ({readback.rows_seeded} rows, {readback.fields_seeded} fields)')
        elif readback.verdict == 'no_read_path':
            unjudged += 1
            lines.append(f'~ {path.name}: seed accepted; read-back NO VERDICT — no read path')
        else:
            gap, recap = _readback_failure(readback, f'the drawn world {path.name}', None, simulate_path, lines)
            failures.append(gap)
            recaps.append(recap)
    total = landed + unjudged + len(failures)
    if failures:
        recap = f'✗ seed read-back: {len(failures)} of {total} drawn world(s) not seeded or not read back as seeded — {recaps[0]}'
        return Verdict(1, _standalone_last_line('\n'.join(lines), recap), failures)
    if unjudged or not total:
        return (2, '\n'.join([*lines, f'~ seed read-back: NO VERDICT — {landed} of {total} drawn world(s) read back as seeded; the rest could not be judged.']))
    return (0, '\n'.join([*lines, f'✓ seed read-back OK — all {total} drawn world(s) were accepted and read back as seeded.']))


#: The one remedy for a drawn world the read-back cannot take: the payload as ``get_scenario`` returned it, which carries
#: every file Init takes (:func:`_scenario_files`) — a reshaped copy loses some and is never guessed at.
_RAW_PAYLOAD = 'write the get_scenario JSON as returned, never reshaped'

#: The SDK module every reader of a post-seed read prints its verdict from (vendored from the backend; SDK >= 0.28.19).
_NO_COMPARATOR = (
    '✗ seed-world: NO VERDICT — this plurai build has no `plurai.world.seed_landing` / `plurai.seed_landing` (the runner\'s seed-vs-read comparator, '
    'SDK 0.28.19 or newer), so no read-back can be compared. Nothing was run; upgrade the SDK and re-run this step.'
)
_NO_READ_PATH = (
    '~ seed read-back: NO VERDICT — no read path: the harness returns no world after seed_state (a world it owns with no '
    '`world_snapshot`), so nothing seeded can be compared. This is never a pass.'
)


def _seed_landing() -> Any:
    """The bench SDK's comparator, or None on a build that predates it. Never a copy: one comparator.

    ``plurai.world.seed_landing`` (its home since the SDK grouped its world modules), else ``plurai.seed_landing`` (the
    older path, kept by the SDK as a one-release shim)."""
    for name in ('plurai.world.seed_landing', 'plurai.seed_landing'):
        try:
            return importlib.import_module(name)  # deferred, as every SDK import in this file
        except ImportError:
            continue
    return None


def _readback_failure(readback: Any, what: str, completed_fields: Any, simulate_path: Path, lines: list[str]) -> tuple[SmokeFailure, str]:
    """The advisory ``seed_readback_gap`` of a read-back that is not ``landed``, its lines appended to ``lines``, and its recap.

    Each class names its fix: a row or field the read lacks is the read path's (widen ``world_snapshot``), a field
    read back changed is the write's (``seed_state`` did not apply it), a failed read is its exception. A subject the
    schema completion added (``completed_fields``) voids any scenario that seeds it; any other, scenarios like the
    one the world came from."""
    if readback.verdict == 'read_failed':
        head = f'✗ seed read-back: the world could not be read after seed_state over {what}: {_one_line(readback.read_error, 450)}'
        lines.append(head)
        message = f'the post-seed read over {what} failed, so no seeded row can be compared'
        recap = f'{head} — fix the exception the read raised (world_snapshot), via the harness lane of /analyze-simulation'
        return SmokeFailure(SmokeGate.SEED_READBACK_GAP, message, simulate_path.name, {'read_error': _one_line(readback.read_error, 450)}), recap
    unread = [*readback.rows_missing, *readback.fields_not_read]
    if unread:
        lines.append(f'  not read back (the read never returns them; make world_snapshot return them): {", ".join(unread)}')
    if readback.fields_changed:
        lines.append(f'  read back changed (seed_state did not apply them): {", ".join(readback.fields_changed)}')
    completed = {str(path) for path in completed_fields or ()}
    anywhere = [subject for subject in readback.subjects if subject in completed]
    alike = [subject for subject in readback.subjects if subject not in completed]
    if anywhere:
        lines.append(f'  voids any scenario that seeds it: {", ".join(anywhere)}')
    if alike:
        lines.append(f'  will void scenarios like the one this world came from: {", ".join(alike)}')
    message = f'seed_state accepted {what} but the post-seed read does not return it as seeded: {", ".join(sorted(readback.subjects))}'
    recap = (
        f'✗ seed read-back: {len(readback.subjects)} seeded subject(s) not read back as seeded — {readback.line()} — '
        f'route to the harness lane of /analyze-simulation (one repair round), or overrule with the reason'
    )
    return SmokeFailure(SmokeGate.SEED_READBACK_GAP, message, simulate_path.name, {'subjects': ', '.join(readback.subjects)}), recap


def _read_ids(landing: Any, read: Any) -> set[str] | None:
    """The row ids a post-seed read returns; None when there is no readable world."""
    if read is None or landing.seed_readback(post_seed_world_state=read, scenario_seed=None).verdict != 'landed':
        return None
    entities = landing.normalize_final_world_state(read).get('entities')
    return {str(key) for key in entities} if isinstance(entities, dict) else None


async def _reset_leftover(simulate_path: Path, landing: Any, before: list, entities: dict, context: dict) -> SmokeFailure | None:
    """D5: Init an empty world again after the probe world. A probe-world id the empty world reads now and did not read
    before the probe was seeded is a row ``reset()`` failed to clear: a store that outlives the child (disk, a DB) would
    carry it into the next scenario. None when either empty read is unreadable (nothing to compare) or nothing is left."""
    first = _read_ids(landing, before[0]) if before else None
    if first is None:
        return None
    after: list = []
    exc, _ = await _seed_init(simulate_path, {}, context, 'smoke-seed-world-reset-after', reads=after)
    third = _read_ids(landing, after[0]) if exc is None and after else None
    if third is None:
        return None
    left = sorted((third - first) & {str(key) for key in entities})
    if not left:
        return None
    line = (
        f'✗ reset leftover: an empty world Init\'d after the probe world still reads {", ".join(left)} — reset() did not clear '
        f'what seed_state wrote, so every scenario after the first starts on the previous one\'s rows; make reset() clear that store'
    )
    return SmokeFailure(SmokeGate.RESET_LEFTOVER, line, simulate_path.name, {'rows': ', '.join(left)})


def _seed_of(folder: Path) -> dict:
    """The resume seed a folder's pin becomes, by the harness contract: the pin's messages minus the
    trailing user message (it is the scenario's opener, said live), its ``agent_state``, and its
    ``resume_point`` when it names one."""
    pin = json.loads((folder / 'resume_pin.json').read_text(encoding='utf-8'))
    messages = list(pin.get('messages') or [])
    if messages and isinstance(messages[-1], dict) and messages[-1].get('type') == 'human':
        messages.pop()
    seed = {'messages': messages, 'agent_state': pin.get('agent_state') or {}}
    if pin.get('resume_point') is not None:
        seed['resume_point'] = pin['resume_point']
    return seed


#: The ``--fidelity-out`` record of a seed-resume probe: the folders whose Init accepted the seed and restored less than
#: the pin (the SDK's ``restore_reduced``), which the final run then plays whole (``regressions.py replay-plan``).
SEED_FIDELITY_SCHEMA = 'seed-resume-fidelity-v1'


async def _run_seed_resume(simulate_path: Path, folders: list[Path], fidelity_out: Path | None = None) -> tuple[int, str]:
    """Init the harness once with no seed (the control), then once per folder with its pin's seed.

    No model call: Init is ``reset`` + ``seed_state`` only. The verdict is the harness's own raise,
    never its words: a folder whose Init raises is refused with the child's text, the rest are
    accepted. A control that raises reaches no verdict (exit 2) — the seed is not the cause then.
    The LAST stdout line is JSON ``{folder: null | refusal text}``. Exit 0 all accepted, 1 any
    refused, 2 no verdict. A bench whose tools are intercepted at the agent's host is no verdict too, with
    no child driven: an Init there opens the hosted route, and nothing else can answer for the harness.

    Every Init — the control and each folder's — gets the turn smoke's envelope: a fetched slot's request
    block with that slot's own world, else ``_smoke_envelope`` (the first ``agent.smoke_inputs`` world and
    its inputs over the request block), else ``{}`` and the request block. A ``seed_state`` that only resumes INTO a record the context names
    (opens the conversation's project) returns before reading the world when no seed is given, so in an
    empty world the control would pass and every seed be refused: the probe would judge its own world, not the pin.
    """
    if simulate_path.suffix != '.py':
        return (2, f'✗ seed-resume: NO VERDICT — this build drives seed_state only through a Python harness; {simulate_path.name} was not run.')
    # Refused, not skipped: the caller reads exit 0/1 and its JSON as the harness's answer, so a skip would ship unasked seeds.
    if (hosted := _host_interception(simulate_path)) is not None:
        return (2, _host_route_line(hosted, 'seed-resume', 'every Init here, the control included, would open that route outside any run, so no seed was asked', refused=True))
    request_block = _init_request_block(simulate_path)[0]
    world: dict = request_block.world or {}
    if request_block.world is None and (smoke := _smoke_envelope(simulate_path, request_block)) is not None:
        world, request_block = smoke
    where = f'world: {len(world.get("entities") or {})} entities, request block from {request_block.source}'
    context = request_block.values
    exc, _ = await _seed_init(simulate_path, world, context, 'smoke-seed-resume-control')
    if exc is not None:
        return (2, f'✗ seed-resume: NO VERDICT — Init with no seed already raised ({where}), so no folder can be judged by its seed: {_one_line(exc, 450)}')
    try:
        from plurai.trials import restore_reduced  # noqa: PLC0415 — an SDK without it records no fidelity
    except ImportError:
        restore_reduced = None
    verdicts: dict[str, str | None] = {}
    reduced: list[str] = []
    for folder in folders:
        try:
            seed = _seed_of(folder)
        except (OSError, ValueError) as exc:
            verdicts[folder.name] = f'{folder.name}/resume_pin.json could not be read ({type(exc).__name__}: {exc})'
            continue
        reports: list = []
        exc, _ = await _seed_init(simulate_path, world, context, f'smoke-seed-resume-{folder.name}', resume_seed=seed, reports=reports)
        verdicts[folder.name] = None if exc is None else _one_line(exc, 600)
        if exc is None and restore_reduced is not None and reports and restore_reduced({'resume_fidelity': reports[0], 'resume_seed': seed}):
            reduced.append(folder.name)
    if fidelity_out is not None and restore_reduced is not None:
        fidelity_out.parent.mkdir(parents=True, exist_ok=True)
        fidelity_out.write_text(json.dumps({'schema': SEED_FIDELITY_SCHEMA, 'restore_reduced': sorted(reduced)}) + '\n', encoding='utf-8')
    refused = [
        SmokeFailure(SmokeGate.SEED_RESUME_REFUSED, f'seed_state refused the resume seed of {name}', f'{name}/resume_pin.json', {'refusal': text})
        for name, text in sorted(verdicts.items())
        if text is not None
    ]
    lines = [f'{"✗" if verdicts[name] else "✓"} seed-resume {name}: {verdicts[name] or "accepted"}' for name in sorted(verdicts)]
    lines += [f'~ seed-resume {name}: accepted, and restored less than the pin — its final-run replay is played whole' for name in sorted(reduced)]
    summary = [f'~ seed-resume envelope — {where}', *lines, json.dumps(verdicts, ensure_ascii=False, sort_keys=True)]
    return Verdict(1 if refused else 0, '\n'.join(summary), refused)


#: Why a regression folder is kept as "seen once" and never sampled — each a typed fact about the
#: folder, the harness or the installed SDK. A recorded CLI session resumes only inside its own CLI;
#: a cut turn that called a tool (in its chain, or on the source turn's ``agent_state.tool_trail``) needs the
#: world it called into, which this local Init does not hold;
#: only a Python harness is driven here; an SDK without the replay-rate functions cannot compute the rate.
SEEN_ONCE_CLI_SESSION = 'cli_session'
SEEN_ONCE_CUT_TOOL_CALL = 'cut_turn_tool_call'
SEEN_ONCE_NOT_PYTHON = 'not_python_harness'
SEEN_ONCE_OLD_SDK = 'sdk_lacks_replay_rates'
#: Every sample raised in Init with the folder's resume seed while an Init without it succeeded: the harness
#: refused the pin, so no reply was drawn.
SEEN_ONCE_CANNOT_RESUME = 'harness_cannot_resume'
#: The harness's ``seed_state`` reported it restored less than the pin (``plurai.trials.restore_reduced``: a dropped
#: state key, never only the turn's report keys): the agent would meet the cut without the state it had there, so no
#: reply is drawn.
SEEN_ONCE_STATE_DROPPED = 'harness_dropped_resume_state'
#: The bench's tools are intercepted at the agent's host (``harness.tool_interception.at: agent_host``): each sample's
#: Init would open that hosted route outside any run, so none is drawn.
SEEN_ONCE_HOST_ROUTE = 'agent_host_route'


#: Why one sample measured nothing: its reply called a tool (it needs a world), or the child raised.
SAMPLE_NEEDS_WORLD = 'needs_world'
SAMPLE_RAISED = 'raised'
#: How many replies one ``score_sampled_replies`` call takes.
SAMPLES_MAX = 10


def _carries_tool_call(messages: object) -> bool:
    """Whether a message chain holds a tool call or a tool result — read off the typed keys only."""
    return isinstance(messages, list) and any(
        isinstance(m, dict) and (bool(m.get('tool_calls')) or m.get('type') == 'tool' or m.get('role') == 'tool') for m in messages
    )


def _reports_tool_trail(agent_state: object) -> bool:
    """Whether a turn's ``agent_state`` reports a call on ``tool_trail`` — the channel a harness whose agent runs its own
    tool loop reports its calls on (the SDK's ``AGENT_STATE_TOOL_TRAIL_KEY``), leaving no tool message in the chain."""
    return isinstance(agent_state, dict) and bool(agent_state.get('tool_trail'))


def _trail_identity(entry: dict) -> tuple:
    """One trail entry's identity: its ``call_id``, else its shape (tool, args, result). Mirrors by value the SDK's
    ``tool_call_recovery._trail_identity`` — this runs before the SDK is imported, so a seen-once folder needs none."""
    call_id = entry.get('call_id')
    if isinstance(call_id, str) and call_id:
        return ('id', call_id)
    return ('shape', entry.get('tool'), *(json.dumps(entry.get(k), sort_keys=True, default=str, ensure_ascii=False) for k in ('args', 'result')))


def _new_trail(agent_state: object, before: object) -> dict:
    """``agent_state`` with the ``tool_trail`` entries ``before``'s trail already carried removed, each identity at most
    as often as ``before`` held it — the SDK's ``strip_seeded_trail`` rule. A harness that keeps its trail in graph
    state re-reports the earlier turn's (or the seed's) calls, and those are not the turn's own."""
    if not isinstance(agent_state, dict) or not isinstance(agent_state.get('tool_trail'), list):
        return agent_state if isinstance(agent_state, dict) else {}
    prior = before.get('tool_trail') if isinstance(before, dict) else None
    budget = Counter(_trail_identity(e) for e in prior if isinstance(e, dict)) if isinstance(prior, list) else Counter()
    kept = []
    for entry in agent_state['tool_trail']:
        identity = _trail_identity(entry) if isinstance(entry, dict) else None
        if identity is not None and budget[identity] > 0:
            budget[identity] -= 1
            continue
        kept.append(entry)
    return {**agent_state, 'tool_trail': kept}


def _sample_rates(n_max: int, record_time_rate: Callable[[list[str]], dict]) -> dict[str, dict]:
    """``{"k/n": <the SDK's record-time rate for k caught of n>}`` for every n up to ``n_max``: the SDK's
    numbers for whatever count the scoring returns, computed here because this is where the SDK is
    importable — ``regressions.py confirm`` runs on any python3 and only looks its count up."""
    return {f'{k}/{n}': record_time_rate(['caught'] * k + ['missed'] * (n - k)) for n in range(1, n_max + 1) for k in range(n + 1)}


async def _run_sample_cut(simulate_path: Path, folder: Path, samples: int, transcript: Path) -> tuple[int, str]:
    """Re-run the agent at a regression folder's cut ``samples`` times, each on a fresh child.

    Each sample Inits on the local mock with the source run's recorded ``initial_world_state`` and
    ``initial_agent_context`` (the pair the recording ran in, so a record the context names resolves) and the
    folder's resume seed (none for an opener-only folder), then drives turn 0 with the pin's trailing user
    message, or ``first_talk.txt``. A sample whose agent side calls a tool — in its message chain or on its
    ``agent_state.tool_trail`` — needs the world it called into and is invalid (``needs_world``); one that raises
    is invalid (``raised``). A folder whose recording this cannot reproduce, or a bench whose tools are intercepted
    at the agent's host (``agent_host_route``), is "seen once" with a typed reason, and nothing is driven — an opener
    folder whose issue is on a later turn (opener-rooted) among them, after the typed reasons above it, as
    ``harness_cannot_resume``; a resumed folder
    whose every sample raised in Init, while one Init without the seed succeeds, is seen once too
    (``harness_cannot_resume``), its raises kept under ``invalid``; so is one whose harness reports it restored less
    than the pin (``harness_dropped_resume_state``), on the first Init. A trail entry the pin (or, for the recorded
    cut turn, the turn before it) already carried is a re-report, not a call. The LAST stdout line is
    JSON ``{valid: [{reply, turn_messages}], invalid: [{reason}], seen_once_reason, rates}``. Exit 0 with
    that line; 2 when the folder or the source transcript cannot be read.
    """

    def result(valid: list, invalid: list, seen_once: str | None, rates: dict | None = None) -> str:
        return json.dumps({'valid': valid, 'invalid': invalid, 'seen_once_reason': seen_once, 'rates': rates or {}}, ensure_ascii=False)

    def seen_once(reason: str, why: str, *preface: str) -> tuple[int, str]:
        return (0, '\n'.join([*preface, f'~ sample-cut {folder.name}: seen once ({reason}) — {why}; nothing was sampled.', result([], [], reason)]))

    try:
        issue = json.loads((folder / 'issue_turn.json').read_text(encoding='utf-8'))
        source = json.loads(transcript.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return (2, f'✗ sample-cut: NO VERDICT — {_one_line(exc, 300)}')
    if (hosted := _host_interception(simulate_path)) is not None:
        route = _host_route_line(hosted, 'sample-cut', 'each sample Inits a fresh child, which here would open that route outside any run')
        return seen_once(SEEN_ONCE_HOST_ROUTE, "the bench intercepts its tools at the agent's host, so no sample can be drawn here", route)
    if (folder / 'cli_session').is_dir():
        return seen_once(SEEN_ONCE_CLI_SESSION, 'the folder resumes a recorded CLI session, which only its own CLI replays')
    cut = issue.get('turn_index') if isinstance(issue, dict) else None
    rows = source.get('turns') if isinstance(source, dict) else None

    def state_at(turn: object) -> object:
        row = next((r for r in rows if isinstance(r, dict) and r.get('turn_idx') == turn), None) if isinstance(rows, list) else None
        return (row or {}).get('agent_state')

    # The cut turn's own calls: its trail minus what the turn before it (or, at turn 0, the resume seed) already reported.
    recorded_seed = source.get('resume_seed') if isinstance(source, dict) else None
    before = state_at(cut - 1) if isinstance(cut, int) and cut > 0 else (recorded_seed or {}).get('agent_state') if isinstance(recorded_seed, dict) else None
    cut_trail = _new_trail(state_at(cut), before) if isinstance(cut, int) else {}
    if _carries_tool_call(issue.get('turn_messages') if isinstance(issue, dict) else None) or _reports_tool_trail(cut_trail):
        return seen_once(SEEN_ONCE_CUT_TOOL_CALL, 'the recorded cut turn called a tool, and a reply sampled here would have no world to call')
    if simulate_path.suffix != '.py':
        return seen_once(SEEN_ONCE_NOT_PYTHON, f'this build drives only a Python harness; {simulate_path.name} was not run')
    if (folder / 'first_talk.txt').is_file() and isinstance(cut, int) and cut > 0:
        # Opener-rooted: no pin reaches the issue turn, and a reply drawn on the opener is not a reply at that turn.
        return seen_once(SEEN_ONCE_CANNOT_RESUME, f'the folder replays from its opener and its issue is on turn {cut}, which no pin resumes to')
    import plurai  # noqa: F401, PLC0415 — an absent SDK is main's exit 2, not a seen-once folder
    try:
        from plurai.orchestrator.orchestrator import _accept_agent_chain, _seed_baseline  # noqa: PLC0415
        from plurai.run_artifacts import REGRESSION_REPLAYS_FILENAME  # noqa: F401, PLC0415 — the feature's marker
        from plurai.observability.tool_call_recovery import strip_seeded_trail  # noqa: PLC0415
        from plurai.trials import record_time_rate  # noqa: PLC0415
        from plurai.trials import restore_reduced  # noqa: PLC0415
    except ImportError:
        return seen_once(SEEN_ONCE_OLD_SDK, 'the installed SDK has no replay-rate functions')
    from plurai._subprocess import ChildProcess  # noqa: PLC0415 — deferred, as in _run_smoke

    # Newer SDKs take the seeded-prefix length as a required keyword; older ones do not accept it.
    takes_seeded = 'seeded' in inspect.signature(_accept_agent_chain).parameters

    def chain_extra(seeded: int) -> dict[str, int]:
        return {'seeded': seeded} if takes_seeded else {}

    seed: dict | None = None
    if (folder / 'resume_pin.json').is_file():
        pin = json.loads((folder / 'resume_pin.json').read_text(encoding='utf-8'))
        last = (pin.get('messages') or [None])[-1]
        if not (isinstance(last, dict) and last.get('type') == 'human' and last.get('content')):
            return (2, f'✗ sample-cut: NO VERDICT — {folder.name}/resume_pin.json does not end on the user message the cut turn opens with.')
        opener: object = last['content']
        seed = _seed_of(folder)
    elif (folder / 'first_talk.txt').is_file():
        opener = (folder / 'first_talk.txt').read_text(encoding='utf-8').strip()
    else:
        return (2, f'✗ sample-cut: NO VERDICT — {folder.name} carries neither resume_pin.json nor first_talk.txt.')
    injected = source.get('injected_inputs') if isinstance(source, dict) else None
    recorded = injected if isinstance(injected, dict) else {}
    world = recorded.get('initial_world_state') if isinstance(recorded.get('initial_world_state'), dict) else {}
    context = recorded.get('initial_agent_context') if isinstance(recorded.get('initial_agent_context'), dict) else {}
    valid: list[dict] = []
    invalid: list[dict] = []
    init_raised = 0
    for index in range(samples):
        thread = f'sample-cut-{folder.name}-{index}'
        in_init = False  # True only while Init runs: a child that never started is not a harness refusing the seed
        try:
            async with ChildProcess(simulate_path) as child:
                in_init = True
                report = await child.init(
                    thread_id=thread,
                    mock_service_url='http://localhost:8199',
                    mock_mode='local',
                    initial_world_state=world,
                    initial_agent_context=context,
                    scenario_metadata={'scenario_name': folder.name},
                    **({'resume_seed': seed} if seed is not None else {}),
                )
                in_init = False
                if seed is not None and restore_reduced({'resume_fidelity': report, 'resume_seed': seed}):
                    why = f'the harness restored less than the pin ({_one_line(json.dumps(report, default=str), 200)}), so a reply drawn here is no reading of the cut'
                    return seen_once(SEEN_ONCE_STATE_DROPPED, why)
                response = await child.user_turn(0, opener)  # type: ignore[arg-type]
            baseline = _seed_baseline(seed, thread_id=thread)
            agent_side, _, _ = _accept_agent_chain(
                list(response.full_messages or []), sent=opener, raw_seen=baseline, thread_id=thread, **chain_extra(len(baseline)),
            )
        except Exception as exc:  # noqa: BLE001 — a sample that raised measured nothing; the rest still stand
            invalid.append({'reason': SAMPLE_RAISED, 'detail': _one_line(exc, 300)})
            if in_init:
                init_raised += 1
            continue
        # A harness that restores the pin's agent_state re-reports its trail: only the sample's own calls need a world.
        fresh, _ = strip_seeded_trail([response.agent_state], (seed or {}).get('agent_state'))
        if _carries_tool_call(agent_side) or _reports_tool_trail(fresh[0]):
            invalid.append({'reason': SAMPLE_NEEDS_WORLD})
            continue
        valid.append({'reply': str(response.ai_text or ''), 'turn_messages': agent_side})
    control: BaseException | None = None
    if seed is not None and samples and init_raised == samples:
        # Attributed to the seed only when Init without it succeeds, as --seed-resume does (no model call).
        try:
            control, _ = await _seed_init(simulate_path, world, context, f'sample-cut-{folder.name}-control')
        except Exception as exc:  # noqa: BLE001 — a control that raised at all attributes nothing to the seed
            control = exc
    if seed is not None and samples and init_raised == samples and control is None:
        why = f'every one of {samples} sample(s) raised in Init with the pin\'s resume seed ({invalid[0]["detail"]}); nothing was sampled.'
        return (0, '\n'.join([f'~ sample-cut {folder.name}: seen once ({SEEN_ONCE_CANNOT_RESUME}) — {why}', result([], invalid, SEEN_ONCE_CANNOT_RESUME)]))
    head = f'✓ sample-cut {folder.name}: {len(valid)} of {samples} sample(s) valid' + (
        f' ({", ".join(sorted({entry["reason"] for entry in invalid}))} on the rest)' if invalid else ''
    )
    return (0, '\n'.join([head, result(valid, invalid, None, _sample_rates(len(valid), record_time_rate))]))


def _local_module_imports(simulate_path: Path, modules: set[str]) -> set[str]:
    """``modules`` plus the absolute imports of each one that is a file (or package) beside simulate.py,
    and of the local files those import in turn."""
    found, parsed, todo = set(modules), set(), list(modules)
    while todo:
        local = simulate_path.parent / todo.pop()
        for path in sorted(local.rglob('*.py')) if local.is_dir() else [local.with_suffix('.py')]:
            if path in parsed or not path.is_file():
                continue
            parsed.add(path)
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except (OSError, SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {a.name.split('.')[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = {node.module.split('.')[0]}
                else:
                    continue
                todo.extend(names - found)
                found |= names
    return found


def _snapshot_imports(simulate_path: Path) -> tuple[set[str], set[str]]:
    """Top-level modules ``world_snapshot`` reaches — imports in it and in the local functions it calls,
    module-scope imports whose names they use, and the imports of the local helper files among those —
    and the top-level modules simulate.py imports at module scope."""
    try:
        tree = ast.parse(simulate_path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return set(), set()
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bound: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            bound.update({(a.asname or a.name).split('.')[0]: a.name.split('.')[0] for a in node.names})
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            bound.update({a.asname or a.name: node.module.split('.')[0] for a in node.names})
    modules: set[str] = set()
    seen: set[str] = set()
    todo = ['world_snapshot']
    while todo:
        name = todo.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Import):
                modules.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                modules.add(node.module.split('.')[0])
            elif isinstance(node, ast.Name):
                todo.append(node.id)
                if node.id in bound:
                    modules.add(bound[node.id])
    return _local_module_imports(simulate_path, modules), set(bound.values())


#: Run in the CHILD interpreter: per reader module's distribution, each wanted extra whose
#: ``Requires-Dist; extra == ...`` distributions are not all installed — each requirement's marker
#: evaluated with that extra requested (without ``packaging``, one with any other clause is skipped).
#: An extra is wanted when a CARRIER — the distribution of a module simulate.py imports at module
#: scope — names it on the reader in a base requirement (markers evaluated with no extra requested; a
#: marked requirement is skipped when ``packaging`` is absent). The packages' own metadata is the table;
#: a config value that happens to spell an extra's name (an ``enum:`` member) is never an input.
_READER_EXTRAS_PROBE = r"""
import importlib.metadata as md, json, re, sys
norm = lambda s: re.sub(r'[-_.]+', '-', s).lower()
try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None
def carried(req):
    if Requirement is None:
        head = re.fullmatch(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?[^;]*', req)
        return (head.group(1), [e for e in re.split(r'\s*,\s*', (head.group(2) or '').strip()) if e]) if head else None
    parsed = Requirement(req)
    if parsed.marker is not None and not parsed.marker.evaluate({'extra': ''}):
        return None
    return parsed.name, parsed.extras
EXTRA = r'extra\s*==\s*[\'"]([^\'"]+)[\'"]'
def applies(req, marker, extra):
    named = [x for x in re.findall(EXTRA, marker) if norm(x) == extra]
    if not named:
        return False
    if Requirement is None:
        return not re.sub(r'[\s()]|\band\b|\bor\b', '', re.sub(EXTRA, '', marker))
    return Requirement(req).marker.evaluate({'extra': named[0]})
owners, gaps, by = md.packages_distributions(), [], {}
for module in json.loads(sys.argv[2]):
    for carrier in set(owners.get(module, ())):
        for req in md.requires(carrier) or []:
            try:
                hit = carried(req)
            except Exception:
                hit = None
            for extra in hit[1] if hit else ():
                by.setdefault((norm(hit[0]), norm(extra)), set()).add(carrier)
for module in json.loads(sys.argv[1]):
    for dist in sorted(set(owners.get(module, ()))):
        for extra in sorted({e for d, e in by if d == norm(dist)}):
            missing = []
            for req in md.requires(dist) or []:
                name, _, marker = req.partition(';')
                try:
                    if not applies(req, marker, extra):
                        continue
                except Exception:
                    continue
                name = re.match(r'\s*([A-Za-z0-9][A-Za-z0-9._-]*)', name).group(1)
                try:
                    md.distribution(name)
                except md.PackageNotFoundError:
                    missing.append(name)
            if missing:
                gaps.append({'dist': dist, 'extra': extra, 'missing': missing, 'by': sorted(by.get((norm(dist), extra), ()))})
print(json.dumps(gaps))
"""


def _reader_extras_gap(simulate_path: Path) -> str | None:
    """The refusal line when a module ``world_snapshot`` reaches lacks, in the child's interpreter, an
    extra its package is wanted with by a module-scope import's distribution, else None."""
    modules, carriers = _snapshot_imports(simulate_path)
    if not modules:
        return None
    python = os.environ.get('PLURAI_CHILD_PYTHON') or sys.executable
    try:
        done = subprocess.run(
            [python, '-c', _READER_EXTRAS_PROBE, *(json.dumps(sorted(v)) for v in (modules, carriers))],
            cwd=simulate_path.parent.parent, capture_output=True, text=True, timeout=60, check=False,
        )
        gaps = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    if not gaps:
        return None
    by_dist: dict[str, list[str]] = {}
    for gap in gaps:
        by_dist.setdefault(gap['dist'], []).append(gap['extra'])
    needs = '; '.join(f"{g['dist']}[{g['extra']}] needs {', '.join(g['missing'])}" for g in gaps)
    install = ' '.join(f'"{dist}[{",".join(extras)}]"' for dist, extras in sorted(by_dist.items()))
    carriers_hit = sorted({carrier for g in gaps for carrier in g['by']})
    reinstall = f', or reinstall {", ".join(carriers_hit)} with its dependencies' if carriers_hit else ''
    return (
        f'✗ smoke FAILED — the world snapshot cannot read {", ".join(sorted({g["extra"] for g in gaps}))}: '
        f'{needs} — not installed in {python}. Install {install} into that interpreter{reinstall}.'
    )


def _sdk_install_line() -> str:
    """The SDK install line, read from `scripts/resolve_plurai.py` — its one source."""
    _plugin_scripts_on_path()
    import resolve_plurai  # noqa: PLC0415 - the plugin's own resolver, resolved from this plugin's root

    return resolve_plurai.INSTALL_LINE

#: Exception classes a ``try`` may catch around an SDK reference for the reference to count as
#: feature-detected rather than required.
_GUARD_EXCEPTIONS = frozenset({'ImportError', 'ModuleNotFoundError', 'AttributeError', 'Exception', 'BaseException'})


def _harness_files(simulate_path: Path) -> list[Path]:
    """simulate.py plus every local helper file it reaches through imports (the same walk
    ``_local_module_imports`` does), never a local directory named like the SDK."""
    try:
        tree = ast.parse(simulate_path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return [simulate_path]
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots.add(node.module.split('.')[0])
    files = [simulate_path]
    for name in sorted(_local_module_imports(simulate_path, roots - {'plurai'}) - {'plurai'}):
        local = simulate_path.parent / name
        for path in sorted(local.rglob('*.py')) if local.is_dir() else [local.with_suffix('.py')]:
            if path.is_file() and path not in files:
                files.append(path)
    return files


def _guarded(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether ``node`` sits in a ``try`` body whose handlers catch a missing name or module."""
    child, parent = node, parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.Try) and child in parent.body:
            for handler in parent.handlers:
                caught = handler.type
                names = caught.elts if isinstance(caught, ast.Tuple) else [caught] if caught is not None else []
                if caught is None or any(isinstance(n, ast.Name) and n.id in _GUARD_EXCEPTIONS for n in names):
                    return True
        child, parent = parent, parents.get(parent)
    return False


def _sdk_references(source: str) -> set[str]:
    """Every dotted SDK path a harness file REQUIRES: each ``import plurai…`` / ``from plurai… import
    name``, and each attribute read off a name those bind (``plurai.file_row``), as ``plurai.<path>``.

    Not required, so never listed: a reference inside a ``try`` that catches ``ImportError`` /
    ``AttributeError`` (or broader), and a name the file feature-detects with ``hasattr(<sdk path>,
    '<name>')`` or ``getattr(<sdk path>, '<name>', <default>)`` — both already handle an SDK without it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    bound: dict[str, str] = {}
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] == 'plurai':
                    bound[alias.asname or 'plurai'] = alias.name if alias.asname else 'plurai'
                    if not _guarded(node, parents):
                        refs.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module and node.module.split('.')[0] == 'plurai':
            for alias in node.names:
                if alias.name == '*':
                    continue
                bound[alias.asname or alias.name] = f'{node.module}.{alias.name}'
                if not _guarded(node, parents):
                    refs.add(f'{node.module}.{alias.name}')

    def dotted(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Name):
            return bound.get(expr.id)
        if isinstance(expr, ast.Attribute):
            base = dotted(expr.value)
            return f'{base}.{expr.attr}' if base else None
        return None

    detected: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'hasattr', 'getattr'}
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
            and (node.func.id == 'hasattr' or len(node.args) >= 3)
            and (base := dotted(node.args[0]))
        ):
            detected.add(f'{base}.{node.args[1].value}')
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
            and not isinstance(parents.get(node), ast.Attribute) and not _guarded(node, parents)
        ):
            path = dotted(node)
            if path and not any(path == d or path.startswith(f'{d}.') for d in detected):
                refs.add(path)
    return refs


#: Run in the CHILD interpreter: for each dotted SDK path, the first prefix the installed SDK does not
#: have — an attribute, else an importable submodule. The walk stops at the first value that is not a
#: module: past it, an attribute is that value's own, not the SDK's surface. A submodule that exists but
#: fails to import is not reported here — the Init reports it with its own traceback.
_SDK_API_PROBE = r"""
import importlib, importlib.metadata as md, json, sys, types
try:
    import plurai
except Exception as exc:
    print(json.dumps({'unimportable': f'{type(exc).__name__}: {exc}'}))
    raise SystemExit(0)
def absent(path):
    obj, name = plurai, 'plurai'
    for part in path.split('.')[1:]:
        if not isinstance(obj, types.ModuleType):
            return None
        name = f'{name}.{part}'
        try:
            obj = getattr(obj, part)
            continue
        except AttributeError:
            pass
        except Exception:
            return None
        try:
            obj = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                return name
            return None
        except Exception:
            return None
    return None
try:
    version = md.version('plurai')
except Exception:
    version = str(getattr(plurai, '__version__', 'unknown version'))
missing = sorted({m for m in map(absent, json.loads(sys.argv[1])) if m})
print(json.dumps({'file': getattr(plurai, '__file__', None), 'version': version, 'missing': missing}))
"""


def _sdk_api_gap(simulate_path: Path) -> str | None:
    """The refusal line when the harness requires an SDK attribute or module the child's installed
    SDK does not have, else None. A capability check, not a version floor: two builds can carry one
    version string and differ in what they export. No verdict (an SDK the child cannot import, or one
    that resolves to a directory beside the harness) is left to the Init, which classifies it."""
    refs = set().union(*(_sdk_references(path.read_text(encoding='utf-8', errors='replace')) for path in _harness_files(simulate_path)))
    if not refs:
        return None
    python = os.environ.get('PLURAI_CHILD_PYTHON') or sys.executable
    try:
        done = subprocess.run(
            [python, '-c', _SDK_API_PROBE, json.dumps(sorted(refs))],
            cwd=simulate_path.parent.parent, capture_output=True, text=True, timeout=60, check=False,
        )
        found = json.loads(done.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    sdk_file = found.get('file')
    if not found.get('missing') or not sdk_file:
        return None
    sdk_dir = Path(sdk_file).resolve().parent
    if sdk_dir in {(root / 'plurai').resolve() for root in (simulate_path.parent, simulate_path.parent.parent)}:
        return None  # a directory named like the SDK shadows it: the Init's `package_shadow`, not a missing API
    names = ', '.join(found['missing'])
    return (
        f'✗ smoke FAILED — the harness uses {names}, which the SDK at {sdk_dir} ({found["version"]}) does not have. '
        f'Upgrade the SDK in {python} (`{_sdk_install_line()}` inside it; scripts/resolve_plurai.py names the uv '
        f'and repo-.venv forms), then re-run this smoke.'
    )


#: The typed raises whose OWN message can name a module the child could not import — the only
#: ones that may yield AGENT_INTERPRETER / MISSING_STUB.
_IMPORT_RAISES = ('builtins.ModuleNotFoundError', 'builtins.ImportError')
#: The categories a typed raise of any class may keep; every other one names a harness file.
_TYPED_ANY_CLASS = (Category.MOCK_UNREACHABLE,)


def _format_failure(
    err_text: str, stderr_lines: list[str], *, source: str, notes: list[str] | None = None,
    simulate_path: Path | None = None, exc: BaseException | None = None,
) -> tuple[int, str]:
    exc_class = getattr(exc, 'child_exc_class', None)
    owner = None
    if exc is not None and exc_class:
        # The child REPORTED a typed raise: its class and its own message are the cause. The stderr
        # buffer is never CLASSIFIED for it — startup noise there is not the cause of a raise the child
        # caught; only the traceback whose summary is this raise's own class is read, for its owner.
        own = str(getattr(exc, 'child_message', None) or err_text)
        category, hint = _classify(own, init_error=None, simulate_path=simulate_path)
        owner, why = _attributed_owner(exc, stderr_lines, simulate_path)
        if owner == 'infra' or (exc_class not in _IMPORT_RAISES and category not in _TYPED_ANY_CLASS):
            category = Category.OTHER
        if owner == 'infra':
            hint = _provider_refused(exc_class, own[:300])
        elif category == Category.OTHER:
            hint = f'{f"the {owner}" if owner else "the child"} raised {exc_class}{f" ({why})" if why else ""}: {own[:300]}'
    else:
        combined = err_text + '\n' + '\n'.join(stderr_lines)
        category, hint = _classify(combined, init_error=None, simulate_path=simulate_path)
    summary = [
        *(notes or []),
        f'✗ smoke FAILED — category={category} (from {source})',
        f'  hint: {hint}',
        f'  child error: {err_text[:400]}',
    ]
    if stderr_lines:
        summary.append('  child stderr (tail):')
        for line in stderr_lines[-5:]:
            summary.append(f'    {line[:200]}')
    # The recap carries the HINT, not just the headline: the one line a caller records is the one
    # line that has to say what to do. Ending on a raw stderr line recorded a fragment of the
    # customer's traceback as this check's whole verdict.
    # An interpreter that cannot import the agent is a prerequisite (exit 2 → `not_run`), not a finding.
    # So is a provider that refused the agent's call (`infra`): no owner is blamed.
    # An agent-owned typed raise is the agent's (exit 3, as `--pipeline`); a missing stub stays the harness's.
    recap = f'✗ smoke FAILED — category={category}: {hint}'
    file = simulate_path.name if simulate_path is not None else 'simulate.py'
    if category == Category.AGENT_INTERPRETER or owner == 'infra':
        return Verdict(2, _standalone_last_line('\n'.join(summary), recap), [])
    observed = {'hint': hint, 'child_error': err_text[:300]}
    if owner == 'agent' and category != Category.MISSING_STUB:
        return _failed(
            AGENT_RAISED_EXIT, _standalone_last_line('\n'.join(summary), recap), SmokeGate.AGENT_RAISED,
            message=f'the agent raised {exc_class}', file=file, observed=observed,
        )
    # Fingerprinted on typed facts only: a classified hint names no exception text, an unmatched failure is
    # its raise's class (or where it came from), and the child's own words ride `observed`.
    if category != Category.OTHER:
        stable = hint
    elif exc_class:
        stable = f'{f"the {owner}" if owner else "the child"} raised {exc_class}'
    else:
        stable = f'category={category} ({source})'
    return _failed(1, _standalone_last_line('\n'.join(summary), recap), category, message=stable, file=file, observed=observed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Smoke-test that simulate.py loads + responds to Init (add --turn to also drive real agent turns: two always, plus one per declaration in plurai.config.yaml the run can confirm).',
    )
    parser.add_argument('simulate_path', type=Path, help='Path to simulate.py')
    parser.add_argument(
        '--turn',
        action='store_true',
        help='Also drive real agent turns (needs the provider key; one LLM call each) — two always, turn 2 probing that the harness carries history across turns (the stateless-harness check), plus one turn per declaration the config makes that this run can confirm: the render surface (only with PLURAI_SMOKE_RENDER_PROBE=1), each non-text `agent.io.input` part kind, and `agent.io.may_open`.',
    )
    parser.add_argument(
        '--pipeline',
        action='store_true',
        help='Instead drive one invoke_pipeline pass per `agent.smoke_inputs` world of the bench_manifest.json beside simulate.py, on the local mock (needs the provider key).',
    )
    parser.add_argument(
        '--seed-world',
        type=Path,
        metavar='JSON',
        help="Instead Init once with the env's complete world (the env job's `detail.seed_probe_world`, saved as JSON), require the harness's seed_state "
        'to accept it, and read it back as seeded. A DIRECTORY instead reads back each drawn scenario world in it (Init only, no model).',
    )
    parser.add_argument(
        '--seed-resume',
        type=Path,
        nargs='+',
        metavar='FOLDER',
        help="Instead Init once with no seed, then once per folder with the resume seed its resume_pin.json becomes; no model call. "
        'The last stdout line is JSON {folder: null | refusal text}.',
    )
    parser.add_argument(
        '--fidelity-out',
        type=Path,
        help='with --seed-resume: write {"schema": "seed-resume-fidelity-v1", "restore_reduced": [folder]} here — the folders '
        'whose Init accepted the seed and restored less than the pin',
    )
    parser.add_argument(
        '--sample-cut',
        type=Path,
        metavar='FOLDER',
        help='Instead re-run the agent at a regression folder\'s cut --samples times, each on a fresh child on the local mock '
        '(one model call each). The last stdout line is JSON {valid: [{reply, turn_messages}], invalid: [{reason}], '
        'seen_once_reason, rates}.',
    )
    parser.add_argument('--samples', type=int, default=5, help=f'how many replies --sample-cut draws (1 to {SAMPLES_MAX}; default 5)')
    parser.add_argument('--from-transcript', type=Path, help="with --sample-cut: the source run's transcript.json the folder was cut from")
    parser.add_argument('--samples-out', type=Path, help='with --sample-cut: also write the JSON line here')
    parser.add_argument('--entry-symbol', type=json.loads, help="the manifest's agent.entry_symbol, as JSON: the entry this smoke witnesses")
    parser.add_argument('--agent-language', help="the manifest's agent.language; only a python agent can be witnessed")
    parser.add_argument('--witness-out', type=Path, help='write {"agent.entry": {state, reason}} here (needs --entry-symbol)')
    parser.add_argument('--findings-out', type=Path, help='write every gate finding this run minted, as JSON, to this path (never printed on stdout)')
    args = parser.parse_args(argv)
    if args.turn and args.pipeline:
        parser.error('--turn drives invoke_agent and --pipeline drives invoke_pipeline; a harness has one shape, pass one')
    if args.seed_world and (args.turn or args.pipeline or args.witness_out):
        parser.error('--seed-world drives Init alone; run it on its own')
    if args.seed_resume and (args.turn or args.pipeline or args.seed_world or args.witness_out):
        parser.error('--seed-resume drives Init alone; run it on its own')
    if args.sample_cut and (args.turn or args.pipeline or args.seed_world or args.seed_resume or args.witness_out):
        parser.error('--sample-cut drives its own children; run it on its own')
    if args.sample_cut and args.from_transcript is None:
        parser.error("--sample-cut needs --from-transcript: the source run's agent context is what each sample Inits with")
    if args.sample_cut and not 1 <= args.samples <= SAMPLES_MAX:
        parser.error(f'--samples takes 1 to {SAMPLES_MAX}')
    if args.witness_out and not isinstance(args.entry_symbol, dict):
        parser.error('--witness-out needs --entry-symbol: a witness of no declared entry would be a verdict about nothing')
    witness = _EntryWitness(args.entry_symbol, args.agent_language) if args.witness_out else None

    if not args.simulate_path.is_file():
        print(f'verify_smoke: not a file: {args.simulate_path}', file=sys.stderr)
        return 2

    try:
        # Before any child is driven: a harness calling an SDK API the installed SDK lacks would
        # otherwise die mid-run, at the first call that reaches it.
        if (api_gap := _sdk_api_gap(args.simulate_path.resolve())) is not None:
            verdict = _failed(1, api_gap, SmokeGate.SDK_API_GAP, message=api_gap, file=args.simulate_path.name, observed={})
        else:
            verdict = asyncio.run(_drive(args, witness))
        exit_code, summary = verdict
        if args.sample_cut and args.samples_out is not None and exit_code == 0:
            args.samples_out.parent.mkdir(parents=True, exist_ok=True)
            args.samples_out.write_text(summary.splitlines()[-1] + '\n', encoding='utf-8')
        if exit_code == 0 and _mode(args) == 'init' and (gap := _reader_extras_gap(args.simulate_path.resolve())):
            verdict = _failed(1, f'{summary}\n{gap}', SmokeGate.READER_EXTRA_MISSING, message=gap, file=args.simulate_path.name, observed={})
    except ImportError as exc:
        # The SDK is a PREREQUISITE, not a finding about the customer's harness. Exit 2 (no
        # verdict) so `bench_manifest` records `not_run` naming what was missing, rather than
        # reporting the bench as broken.
        print(
            f'verify_smoke: the plurai SDK is not importable in this interpreter ({exc}). NOTHING was '
            'smoke-tested. Install/point at the SDK (scripts/resolve_plurai.py resolves the runner) and re-run.',
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — show full failure if our driver crashes
        print(f'verify_smoke: driver crashed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    finally:
        if witness is not None:
            args.witness_out.write_text(json.dumps({'agent.entry': witness.outcome()}) + '\n', encoding='utf-8')

    exit_code, summary = verdict
    if exit_code != 2:
        try:
            exit_code, lines = _settle(verdict, args.simulate_path, mode=_mode(args), findings_out=args.findings_out)
        except (ImportError, OSError, ValueError) as exc:
            # No verdict, never a finding about the bench: the ledger (or the plugin's finding contract) was unreadable.
            print(summary)
            print(f'verify_smoke: the gate ledger could not be read or the findings written ({type(exc).__name__}: {exc})', file=sys.stderr)
            return 2
        # Before the summary: its LAST line is what a caller records (and, for --seed-resume / --sample-cut, parses).
        for line in lines:
            print(line)
    # A failing run of the agent itself: name a shell variable it may need and the child did not get (advisory only).
    if exit_code != 0 and _mode(args) in ('init', 'turn', 'pipeline') and (env_gap := _child_env_gap(args.simulate_path.resolve(), summary)):
        print(env_gap)
    print(summary)
    return exit_code


def _drive(args: argparse.Namespace, witness: _EntryWitness | None) -> Coroutine[Any, Any, tuple[int, str]]:
    """The one mode the flags select, ready to run."""
    simulate = args.simulate_path.resolve()
    if args.sample_cut:
        return _run_sample_cut(simulate, args.sample_cut.resolve(), args.samples, args.from_transcript.resolve())
    if args.seed_resume:
        return _run_seed_resume(simulate, [folder.resolve() for folder in args.seed_resume], args.fidelity_out)
    if args.seed_world:
        return _run_seed_world(simulate, args.seed_world)
    if (hosted := _host_interception(simulate)) is not None:
        return _run_host_route(simulate, hosted)
    if args.pipeline:
        return _run_pipeline_smoke(simulate, witness)
    return _run_smoke(simulate, drive_turn=args.turn, witness=witness)


def _mode(args: argparse.Namespace) -> str:
    for mode in ('sample_cut', 'seed_resume', 'seed_world'):
        if getattr(args, mode):
            return mode
    if _host_interception(args.simulate_path.resolve()) is not None:
        return 'host_route'
    for mode in ('pipeline', 'turn'):
        if getattr(args, mode):
            return mode
    return 'init'


#: Every gate a mode evaluates when it runs to its end — what a passing run may call an overrule of
#: stale. A failing run stops at its failure, so it evaluated only the gates that failed.
_MODE_GATES: dict[str, frozenset[Category | SmokeGate]] = {
    'init': frozenset({
        *(c for c in Category if c != Category.AGENT_INTERPRETER), SmokeGate.SDK_API_GAP, SmokeGate.AGENT_RAISED, SmokeGate.READER_EXTRA_MISSING,
        SmokeGate.SIBLING_INIT_FAILED, SmokeGate.SIBLING_CROSS_TALK, SmokeGate.EXCLUSIVE_UNNEEDED, SmokeGate.WORLD_READ_RAISED,
    }),
    'turn': frozenset({
        *(c for c in Category if c != Category.AGENT_INTERPRETER), SmokeGate.SDK_API_GAP, SmokeGate.AGENT_RAISED, SmokeGate.INVOKE_NOT_AWAITABLE,
        SmokeGate.TURN_SILENT, SmokeGate.RENDER_SILENT, SmokeGate.INPUT_PART_REFUSED, SmokeGate.MAY_OPEN_REFUSED, SmokeGate.MAY_OPEN_SILENT,
        SmokeGate.REQUEST_KEY_UNREAD, SmokeGate.TRAIL_RESULT_MISSING, SmokeGate.CANNED_RESUME_REFUSED, SmokeGate.CHANNEL_REFUSED,
        SmokeGate.DOCUMENT_UNDELIVERED, SmokeGate.WORLD_READ_RAISED, SmokeGate.TRAIL_TOOL_UNDECLARED, SmokeGate.TRAIL_TOOL_UNTYPED,
        SmokeGate.TOOL_HISTORY_DROPPED,
    }),
    'pipeline': frozenset({
        *(c for c in Category if c != Category.AGENT_INTERPRETER), SmokeGate.SDK_API_GAP, SmokeGate.AGENT_RAISED, SmokeGate.HARNESS_RAISED,
        SmokeGate.REQUEST_KEY_UNREAD, SmokeGate.TRAIL_TOOL_UNDECLARED, SmokeGate.TRAIL_TOOL_UNTYPED,
    }),
    'host_route': frozenset({SmokeGate.SDK_API_GAP, SmokeGate.REQUEST_KEY_UNREAD}),
    'seed_world': frozenset({SmokeGate.SDK_API_GAP, SmokeGate.SEED_WORLD_REFUSED, SmokeGate.SEED_READBACK_GAP, SmokeGate.RESET_LEFTOVER}),
    'seed_resume': frozenset({SmokeGate.SDK_API_GAP, SmokeGate.SEED_RESUME_REFUSED}),
    'sample_cut': frozenset({SmokeGate.SDK_API_GAP}),
}

#: Where a failure's remedy lives, by gate; every other gate's fix is the line the smoke printed.
_REMEDY: dict[Category | SmokeGate, str] = {
    SmokeGate.SEED_WORLD_REFUSED: 'decide whether the env schema (/edit-env) or seed_state is wrong, per generate-mock-env',
    SmokeGate.SEED_READBACK_GAP: 'not read back: make world_snapshot return it; read back changed: make seed_state apply it (harness lane, one round)',
    SmokeGate.CONFIRMED_STORE_SEED_READBACK_GAP: 'the confirmed store must read back as seeded: make world_snapshot return it, or seed_state apply it',
    SmokeGate.WORLD_READ_RAISED: 'fix what the post-seed read raised (world_snapshot), via the harness lane of /analyze-simulation',
    SmokeGate.RESET_LEFTOVER: 'make reset() clear the store seed_state writes, so no scenario starts on the previous one\'s rows',
    SmokeGate.SEED_RESUME_REFUSED: "make seed_state accept the folder's resume seed, or repair its resume_pin.json",
    SmokeGate.SIBLING_INIT_FAILED: _SIBLING_REMEDY,
    SmokeGate.SIBLING_CROSS_TALK: _CROSS_TALK_REMEDY,
    SmokeGate.EXCLUSIVE_UNNEEDED: _EXCLUSIVE_UNNEEDED_REMEDY,
}


def _settle(verdict: Verdict | tuple[int, str], simulate_path: Path, *, mode: str, findings_out: Path | None) -> tuple[int, list[str]]:
    """Each failure of ``verdict`` as a gate finding, settled against the plugin's ledger beside this workdir.

    The catalogue in ``scripts/gate_ledger.py`` decides each finding's authority. The exit is 1 while one
    HOLDS (hard, or advisory and neither repaired nor overruled in ``<workdir>/artifacts/.gate_overrules.jsonl``);
    else, for a failure the run measures (:data:`_RUN_MEASURED`), the exit naming its owner — the agent's
    raise or an undetermined one; else 0. Returns that exit and the ``finding:`` lines to print. A verdict
    whose exit is 1 or 3 carries its failures, always.
    """
    code, _summary = verdict
    failures: tuple[SmokeFailure, ...] = getattr(verdict, 'failures', ())
    if code in (1, AGENT_RAISED_EXIT) and not failures:
        raise ValueError(f'a failing smoke verdict (exit {code}) carries no failure to mint a finding from')
    _plugin_scripts_on_path()
    import gate_ledger  # noqa: PLC0415 - the plugin's one finding contract, resolved from this plugin's root

    failures = tuple(_on_confirmed_store(failure, simulate_path) for failure in failures)
    minted = [
        gate_ledger.make_finding(
            f'plugin.smoke.{failure.gate}',
            subject=simulate_path.name,
            evidence={'code': str(failure.gate), 'file': failure.file, 'message': failure.message},
            observed=failure.observed,
            remedy=_REMEDY.get(failure.gate, 'the fix the smoke names (simulation-setup-python Step 9f)'),
            draft_ref=None,
        )
        for failure in failures
    ]
    # A run that stopped at a failure evaluated only what failed; one that passed (warnings at most) evaluated its mode.
    gates = {failure.gate for failure in failures} if any(f.gate not in _WARNING_GATES for f in failures) else _MODE_GATES[mode]
    applied, lines = gate_ledger.settle(
        minted, art=simulate_path.resolve().parent / 'artifacts', evaluated=tuple(f'plugin.smoke.{gate}' for gate in sorted(gates)), findings_out=findings_out,
    )
    owners = [_RUN_MEASURED[failure.gate] for failure in failures if failure.gate in _RUN_MEASURED]
    exit_code = 1 if applied.holding else owners[0] if owners else 0
    return exit_code, lines


def _on_confirmed_store(failure: SmokeFailure, simulate_path: Path) -> SmokeFailure:
    """A seed read-back gap on a bench whose `world_state.confirmation` holds a `disposable_copy`: the harness seeds that
    live store itself, so a seed it does not read back is hard (`confirmed_store_seed_readback_gap`)."""
    if failure.gate != SmokeGate.SEED_READBACK_GAP:
        return failure
    for rung in (simulate_path.parent, simulate_path.parent.parent):
        try:
            import yaml  # noqa: PLC0415 — deferred; this file is testable without pyyaml

            world = (yaml.safe_load((rung / 'plurai.config.yaml').read_text(encoding='utf-8')) or {}).get('world_state')
        except Exception:  # noqa: BLE001, S112 — an unreadable config confirms nothing
            continue
        entries = world.get('confirmation') if isinstance(world, dict) else None
        if isinstance(entries, list) and any(isinstance(e, dict) and e.get('answer') == 'disposable_copy' for e in entries):
            return failure._replace(gate=SmokeGate.CONFIRMED_STORE_SEED_READBACK_GAP)
        return failure
    return failure


#: The model-provider variable families an agent signs in through. The agent child receives only the SDK's allow-list and
#: the names `plurai.config.yaml` declares under `agent.env:` (simulation-setup Step 8), so one of these set in the shell
#: and absent in the child is named on a failing smoke.
_PROVIDER_FAMILIES = frozenset({
    'OPENAI', 'AZURE', 'GOOGLE', 'GEMINI', 'VERTEX', 'VERTEXAI', 'MISTRAL', 'COHERE', 'GROQ', 'TOGETHER', 'DEEPSEEK', 'XAI',
    'OPENROUTER', 'FIREWORKS', 'PERPLEXITY', 'HUGGINGFACE', 'HF', 'OLLAMA', 'LITELLM', 'VOYAGE', 'BEDROCK',
})
#: An environment-variable-shaped word in a failure's text (`OPENAI_API_KEY`, `GOOGLE__API_KEY`).
_ENV_WORD = re.compile(r'\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b')
#: The SDK's own settings families: a credential under them is withheld from the child on purpose, never advised.
_SDK_OWN_PREFIXES = ('PLURAI_', 'NETWORK__')
_ENV_GAP_NAMES_MAX = 8


def _child_env_gap(simulate_path: Path, failure_text: str, environ: dict[str, str] | None = None) -> str | None:
    """One advisory line naming the variables set in this shell that the agent child does not receive and the agent
    may need — a model-provider family's, or one the failure's own text names — else None.

    The child's environment is the SDK's (`plurai.child_env`): its allow-list plus the config's declared `agent.env`
    names. Names only, never values. An SDK without that module, or a config it refuses, says nothing here."""
    environ = dict(os.environ) if environ is None else environ
    try:
        from plurai.child_env import child_environment, declared_env  # noqa: PLC0415 — the SDK's own rule, nothing re-derived

        child = child_environment(environ, declared=declared_env(simulate_path))
    except Exception:  # noqa: BLE001 — an older SDK or a refused config: the smoke's own verdict stands alone
        return None
    named = set(_ENV_WORD.findall(failure_text or ''))
    gap = sorted(
        name for name, value in environ.items()
        if value and name not in child and not name.startswith(_SDK_OWN_PREFIXES)
        and (name in named or name.split('_', 1)[0] in _PROVIDER_FAMILIES)
    )
    if not gap:
        return None
    one = len(gap) == 1
    shown = ', '.join(gap[:_ENV_GAP_NAMES_MAX]) + (f' and {len(gap) - _ENV_GAP_NAMES_MAX} more' if len(gap) > _ENV_GAP_NAMES_MAX else '')
    return (f'~ agent.env: {shown} {"is" if one else "are"} set in this shell, and the agent child does not receive '
            f'{"it" if one else "them"}: the child gets only the SDK\'s allow-list and the names plurai.config.yaml declares under '
            f'`agent.env:`. If the agent reads {"it" if one else "them"}, add {"it" if one else "them"} there (simulation-setup '
            'Step 8) and run this smoke again.')


def _plugin_scripts_on_path() -> None:
    path = str(Path(__file__).resolve().parents[3] / 'scripts')
    if path not in sys.path:
        sys.path.insert(0, path)


if __name__ == '__main__':
    _plugin_scripts_on_path()
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
