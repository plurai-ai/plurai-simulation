#!/usr/bin/env python3
"""Resolve WHICH `plurai` CLI an ingest skill should invoke — print its path on stdout.

Claude Code's Bash tool runs a fresh shell; the client's venv — the one the SDK is
installed in — is not necessarily on its PATH. A bare `plurai` therefore either misses
(command not found) or — worse — resolves to a global/stale install that shadows the
client's pinned SDK and runs a different anonymizer/wire-contract version while
printing a perfectly normal-looking manifest. This resolver makes the choice explicit
and verifiable:

    PLURAI_BIN="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --ask)"
    "$PLURAI_BIN" ingest-corpus ...

Search order (the repo tiers anchor on the project root, so the answer is the same from the repo
root and from any directory inside it — see `_project_root`; the FIRST tier whose binary exists is
AUTHORITATIVE — a stale install there is an error to fix in place, never a reason to
silently run some other install from a later tier):
  1. $VIRTUAL_ENV/bin/plurai — the venv the client ACTIVATED before launching Claude
     Code (inherited by the Bash tool). This is "the client's venv" wherever it lives
     (custom path, an activated poetry, pipenv or hatch venv).
  2. $CONDA_PREFIX/bin/plurai — the active conda environment (conda sets no $VIRTUAL_ENV); never conda's `base`.
  Tiers 1 and 2 are skipped, with a stderr line naming what was skipped, for an environment outside the project folder
  when the folder holds a venv of its own: it was activated for another project.
  3. <project root>/.venv/bin/plurai — the repo-root default (uv- and pip-managed projects, and the
     layout the plugin's own setup skills create).
  On native Windows each venv tier looks for `Scripts/plurai.exe` first, then `bin/plurai`.
  4. Any other repo-root venv (`.venv*` / `venv*` glob, sorted) — covers `.venv-plurai`
     and similar conventional names; without this tier such repos fell through to a
     global/stale PATH install, the exact shadowing this resolver exists to prevent.
  5. The venv poetry (`poetry.lock`) or pipenv (`Pipfile`) keeps outside the project, asked of the tool only when no
     earlier tier holds a runner. hatch is never asked (see `_TOOL_VENVS`).
  6. `plurai` on PATH — global/pipx installs, accepted with a stderr note.

The winner must pass a capability probe — `<candidate> --help` exits 0 and lists the
`ingest-*` subcommands — so a pre-ingest SDK generation is rejected with upgrade
guidance instead of failing mid-flow. stdout carries ONLY the resolved absolute path
(capture it verbatim); all diagnostics go to stderr. Non-zero exit = nothing usable; 3 = a runner
was found and REFUSED (the client pin, a release outside the range `_sdk_in_range` names, or a file no installed
`plurai-simulation-sdk` distribution lists) — never fall back. `EXIT_RUNNER_REFUSED` names that code.

A runner older than `PINNED_SDK_VERSION` in a project environment of its own is offered the install, when the caller passes
`--ask` (the skills do; the scripts that relay the resolver's stderr mid-flow do not): the refusal's stderr
carries the one question for the caller to ask (`_ask_text`), whose ask the same call has already sanctioned
(`_sanction_blocker`) so the ask tool is the caller's next action, and `--install-pin [--require ...]` installs exactly the
pin into that runner's environment once the answer is yes. The caller's own refusal exit stays 3 either way, and a run
declared headless gets the stop text alone. A project environment that holds no runner at all is offered the same install
under the question `MISSING_SDK_QUESTION`. A runner of a later minor, an unreadable version and an environment that is not a venv or
conda environment of the project get the stop text and no question: nothing downgrades or writes on a keypress.

A runner older than the pin that also lacks a subcommand named with `--require` is offered the same install (`--ask`) or
gets it (`--install-pin`): it lacks the subcommand because it is old, and the release the plugin needs has it. A runner at or
past the pin that lacks one is a broken install and keeps the exit 1 with `UPGRADE_HINT`, as does a call with neither flag.

A project that keeps its dependencies in a lock or a requirements file gets the SDK through it (`_locked_project`), after
the same question, which names the files the install changes. A uv project is written with `uv add --dev` and a Python
marker (`pyproject.toml`, `uv.lock`); a Poetry project with `poetry add --group dev` (`pyproject.toml`, `poetry.lock`; Poetry through `uvx`, at a fixed
release from a fixed index with uv's configuration off, when `poetry` is not on PATH), and both files are put back when it fails; a requirements project gets the pin
installed into its environment, and a requirements file that already lists the SDK gets that line set to the pin (a line is
never added). Where the plugin cannot write (no `poetry` or `uvx`, no `pyproject.toml` beside `poetry.lock`, a requirements
file with hashes) the project gets the stop text and no question. `--install-line` is the fallback for an SDK too old to
probe: it prints the line for a Poetry or requirements project and exits `INSTALL_MANUAL`. Any other project keeps the
install into its environment.

TRUST MODEL
  The runner is looked up from the working directory and its callers run it with
  `PLURAI_TOKEN` in the environment, so what the lookup finds receives the bearer. Two
  rules keep that to the SDK:

  * A candidate is probed (`--help`, `capabilities`, `--version`) in an environment without the
    bearer, and only after it passes the next rule.
  * A path is handed out only when a `plurai_simulation_sdk-*.dist-info/RECORD` in the candidate's own environment
    lists that exact file with the hash it has now (see `_unverified_reason`). A script placed in a
    workspace venv has no row, and a script edited after installation has a hash its row does not.
    Nothing in the environment or on a command line relaxes this: a runner that is not an installed
    distribution (a wrapper, a relocated venv) is not handed the bearer. Install the SDK into that
    environment instead.

  What this does not do: it is not a boundary against someone who can already write inside the
  runner's venv, because they can write the `RECORD` too. The workspace venv is the customer's own
  environment for their own agent and is trusted to that extent; the checks stop a file that
  arrived some other way (a repository checkout, a model-written file) from being mistaken for it.
  A runner a command line NAMES (`trace_scenarios.py --plurai`, `client_pin.py --plurai`) must be one
  of the files this resolver chooses among and must pass the same check (`explicit_runner_refusal`).

Stdlib-only, POSIX-only — same constraints as hooks/ (the other plugin-shipped runtime
scripts). Deliberately does NOT probe the anonymizer: under an anonymized-posture env
(`anonymize: true` in plurai.config.yaml), `plurai ingest-*` is fail-closed on that with
actionable stderr the skills' troubleshooting tables already map; under the raw default
posture no anonymizer is required at all.
"""

import base64
import csv
import functools
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'hooks'))
import _platform  # noqa: E402
try:
    from _stdio import utf8_child_text
except ImportError:  # a copy run outside the plugin decodes its children as the platform does
    def utf8_child_text() -> dict:
        return {}

#: The SDK build this plugin release was tested with, as published on the package index under `SDK_DISTRIBUTION`. One
#: constant, bumped by every plugin release; an SDK release precedes the plugin release that consumes it. The
#: resolver accepts this release and every later release of its `major.minor` (`_sdk_in_range`): a project's lock decides
#: which SDK it runs, and a patch release keeps what the skills call. An older SDK lacks it, and another minor is a line
#: nobody tested the skills against. A release is `X.Y.Z`; a pre-release (`X.Y.ZrcN`, `X.Y.Z.devN`) orders below it. A
#: rehearsal build of the plugin pins one pre-release, which admits its own later builds and the release.
PINNED_SDK_VERSION = '1.1.0'

#: The PyPI distribution that carries the `plurai` command line.
SDK_DISTRIBUTION = 'plurai-simulation-sdk'

#: An extra's name as the SDK declares them: lowercase words joined by hyphens.
_EXTRA_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


#: Every SDK old enough to matter has this; extra names are ADDED to it via --require.
_DEFAULT_REQUIRED = ('ingest-corpus',)

#: Every verb that sends content to the backend's object store. The backend takes that content only
#: as raw parts, and an SDK that predates the parts-only upload lists these same verbs, so its
#: command list cannot tell it from a current one: `plurai capabilities` names the behaviour in
#: `features` instead. The feature is required only for a verb the caller names with `--require`,
#: never for the default probe, so a skill that only runs simulations does not need it.
UPLOAD_VERBS = frozenset({'stash-doc', 'ingest-bundle', 'ingest-corpus', 'ingest-descriptions', 'ingest-doc-examples', 'ingest-run', 'ingest-run-report', 'ingest-traces'})
UPLOAD_FEATURE = 'parts-only-upload'


def _venv_runners(venv):
    """Where a venv's `plurai` can be, on every platform: `bin/plurai`, then a Windows venv's `Scripts\\plurai.exe`."""
    return [os.path.join(venv, 'bin', 'plurai'), os.path.join(venv, 'Scripts', 'plurai.exe')]


def _venv_runner(venv):
    """The venv's `plurai`: the first of its places that exists, else this platform's own spelling (for the message)."""
    places = _venv_runners(venv)
    return next((place for place in places if os.access(place, os.X_OK)), places[1] if _platform.WINDOWS else places[0])


def _is_venv(path):
    """A directory that is a Python virtualenv: it carries `pyvenv.cfg`, or a `plurai` runner."""
    return os.path.isfile(os.path.join(path, 'pyvenv.cfg')) or any(os.access(place, os.X_OK) for place in _venv_runners(path))


def _holds_venv(directory):
    """True when ``directory`` has a conventional repo venv (`.venv*` / `venv*`) directly inside it."""
    return any(_is_venv(match) for pattern in ('.venv*', 'venv*') for match in glob.glob(os.path.join(directory, pattern)))


def _project_root():
    """The directory the repo venv tiers are read from, independent of the shell's cwd.

    The Bash tool's cwd is wherever the model last `cd`ed (typically into `plurai_sim/`), so a
    lookup relative to it misses the project's `.venv` and the caller degrades without saying why.
    The root is the nearest directory at or above the cwd that holds a repo venv. The search ends
    at the project boundary, the first directory (cwd included) holding `.git` or `plurai_sim/`,
    because a venv above the project belongs to something else. With no venv in that span the root
    is the boundary itself, so the failure message names the path a customer would create; with no
    boundary at all it is the cwd.
    """
    start = os.path.abspath(os.getcwd())
    searched = []
    directory = start
    while True:
        searched.append(directory)
        if os.path.exists(os.path.join(directory, '.git')) or os.path.isdir(os.path.join(directory, 'plurai_sim')):
            return next((d for d in searched if _holds_venv(d)), directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            return start
        directory = parent


def _inside(path, root):
    """Whether ``path`` is ``root`` or under it, links resolved."""
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


#: The skipped environments already named on stderr, so a process that looks twice says it once.
_SKIPS_TOLD = set()

_ACTIVATED_LABELS = {'VIRTUAL_ENV': 'activated venv ($VIRTUAL_ENV)', 'CONDA_PREFIX': 'active conda env ($CONDA_PREFIX)'}


def _activated_envs(root):
    """``(path, variable)`` of the activated environments the tiers start with: `$VIRTUAL_ENV`, then the active conda
    environment.

    One outside the project folder is skipped when the folder holds a venv of its own: it was activated for another
    project in the shell the harness started from, and the agent runs from the project's own venv. A skip is said on
    stderr, naming the environment skipped."""
    kept = []
    for path, variable in ((os.environ.get('VIRTUAL_ENV') or '', 'VIRTUAL_ENV'), (_conda_env() or '', 'CONDA_PREFIX')):
        if not path:
            continue
        if not _inside(path, root) and _holds_venv(root):
            if path not in _SKIPS_TOLD:
                _SKIPS_TOLD.add(path)
                print(
                    f'resolve_plurai: skipped the {_ACTIVATED_LABELS[variable]} {path}: it is outside the project {root}, '
                    'which has a venv of its own.',
                    file=sys.stderr,
                )
            continue
        kept.append((path, variable))
    return kept


def _child_env():
    """The environment `$PLURAI_CHILD_PYTHON` (the agent's interpreter) belongs to, or ``None``: `<env>/bin/python`,
    `<env>\\Scripts\\python.exe`, or a conda environment's root `python.exe`. The path is not resolved: a venv's
    interpreter links to its base Python, which is not where the agent's packages live."""
    child = os.environ.get('PLURAI_CHILD_PYTHON') or ''
    if not child:
        return None
    parent = os.path.dirname(os.path.abspath(child))
    env = os.path.dirname(parent) if os.path.basename(parent) in ('bin', 'Scripts') else parent
    return env if _is_env(env) else None


def _environment_dirs(root):
    """(directory, tier-label) of every environment the project may hold, in tier order: the activated ones first
    (`_activated_envs`), then the agent interpreter's (`_child_env`), then `.venv`, then the other conventional names.
    Without the interpreter's tier, an agent venv that is neither activated nor in the folder leaves the install a
    bare `pip install`, which lands in whatever Python owns the first `pip` on PATH.

    The glob tier covers repo-local venvs under any conventional name (`.venv-plurai`,
    `venv`, `.venv312`, …): without it, a repo whose venv isn't literally `.venv`
    falls through to a global/stale PATH install — the silent version-skew this
    resolver exists to prevent. Sorted for determinism when several exist. Both the lookup (`_candidates`) and the
    install (`_target_venv`) read this order, so an install lands where the next lookup looks.
    """
    places = [(path, _ACTIVATED_LABELS[variable]) for path, variable in _activated_envs(root)]
    child = _child_env()
    if child and child not in (path for path, _ in places):
        places.append((child, "agent interpreter's env ($PLURAI_CHILD_PYTHON)"))
    places.append((os.path.join(root, '.venv'), 'repo .venv'))
    for pattern in ('.venv*', 'venv*'):
        for venv_path in sorted(glob.glob(os.path.join(root, pattern))):
            if venv_path not in (path for path, _ in places):
                places.append((venv_path, f'repo {os.path.basename(venv_path)}'))
    return places


def _candidates():
    """(path, tier-label) per tier, the environments of `_environment_dirs` first. PATH entry only if `which` hits."""
    root = _project_root()
    tiers = [(_venv_runner(path), label) for path, label in _environment_dirs(root)]
    # Asking poetry or pipenv costs a subprocess, so they are asked only when no earlier tier holds a runner.
    if not any(os.access(path, os.X_OK) for path, _ in tiers):
        tool = _tool_venv(root)
        if tool:
            tiers.append((_venv_runner(tool), 'the venv poetry or pipenv names'))
    path_hit = shutil.which('plurai')
    if path_hit:
        tiers.append((path_hit, 'PATH'))
    return tiers


#: The bearer. Callers run the resolved runner with it in the environment; the resolver never does.
_BEARER_ENV = 'PLURAI_TOKEN'

#: The installed distribution's metadata directory starts with its name as installers normalize it.
_DIST_INFO_PREFIX = SDK_DISTRIBUTION.replace('-', '_')


def _probe_env():
    """The environment a candidate is probed in: ours without the bearer.

    A probe runs a file the resolver has not finished vetting, so it gets nothing it could send
    anywhere. The resolved runner receives the bearer later, from its caller, once it is chosen.
    """
    return {name: value for name, value in os.environ.items() if name != _BEARER_ENV}


def _record_hash(data):
    return 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode('ascii')


def _unverified_reason(candidate):
    """Why no installed `plurai-simulation-sdk` distribution vouches for ``candidate``, or None when one does.

    An installer writes each console script's path, hash and size into the `RECORD` of the
    distribution it installed, in the environment the script lives in. A runner is verified when
    the `plurai_simulation_sdk-*.dist-info/RECORD` beside its own interpreter's `site-packages` lists exactly this
    file, with exactly this content. A file dropped into a workspace venv has no such row, and a
    file edited after installation has a hash its row does not. This is a check on how the file got
    there, not a boundary: someone who can rewrite both the script and the `RECORD` in the same
    venv can pass it, which is why the trust model (module docstring) names the workspace as trusted.
    """
    real = os.path.realpath(candidate)
    try:
        with open(real, 'rb') as handle:
            digest = _record_hash(handle.read())
    except OSError as exc:
        return f'{candidate} cannot be read ({exc})'
    environment = os.path.dirname(os.path.dirname(real))
    site_dirs = sorted(
        glob.glob(os.path.join(environment, 'lib*', 'python*', 'site-packages'))
        + glob.glob(os.path.join(environment, 'lib*', 'python*', 'dist-packages'))  # a distribution's own python
        + glob.glob(os.path.join(environment, 'Lib', 'site-packages'))
    )
    listed = False
    for site in site_dirs:
        for record in glob.glob(os.path.join(site, f'{_DIST_INFO_PREFIX}-*.dist-info', 'RECORD')):
            try:
                with open(record, newline='', encoding='utf-8') as handle:
                    rows = list(csv.reader(handle))
            except (OSError, UnicodeDecodeError, csv.Error):
                continue
            for row in rows:
                if len(row) >= 2 and os.path.normpath(os.path.join(site, row[0])) == real:
                    listed = True
                    if row[1] == digest:
                        return None
    if listed:
        return f'{real} does not match the hash its installed `{SDK_DISTRIBUTION}` distribution recorded (it changed after installation)'
    return f'no installed `{SDK_DISTRIBUTION}` distribution lists {real} (it is not a console script an installer put in {environment})'


def explicit_runner_refusal(named):
    """Why a runner a command line NAMED (`--plurai <path>`) may not be run, or None.

    The skills pass the path this resolver printed, so a named runner has to be one of the files
    the resolver chooses among and has to verify as an installed `plurai`. Anything else is a path
    chosen by whoever wrote the command, and running it runs their program with the caller's
    environment, bearer included. A bare name is read as the caller's shell would read it, on PATH.
    """
    located = shutil.which(named) if os.sep not in named else named
    if not located or not os.path.exists(located):
        return None  # nothing to run; the caller's own "cannot run" handling reports it
    real = os.path.realpath(located)
    chosen_among = {os.path.realpath(path) for path, _label in _candidates()}
    if real not in chosen_among:
        return (
            f'{named} is not a runner `resolve_plurai.py` chooses among (the activated venv, the project venv, PATH). '
            'Pass the path it prints.'
        )
    unverified = _unverified_reason(os.path.abspath(located))
    if unverified:
        return f'{unverified}. Reinstall the SDK into that environment.'
    return None


def _missing(candidate, required=_DEFAULT_REQUIRED, features=()):
    """The names in ``required`` and ``features`` that ``candidate`` does not provide, in that order.

    Empty means the candidate qualifies: `<candidate> --help` exits 0 and lists EVERY subcommand in
    ``required``, and its `plurai capabilities` lists every name in ``features``. A candidate whose
    `--help` does not run provides nothing that can be checked, so every name is missing.

    Callers needing a newer subcommand pass it (``--require ingest-run``): an old install
    then fails HERE with the upgrade hint, instead of later with argparse's bare
    "invalid choice" — observed live: the score_run by-reference path died mid-quality-loop
    on a CLI that probed fine for ingest-corpus but predated ingest-run.

    A grouped verb (``--require "job wait"``) is checked against `plurai capabilities`, which
    lists each one as ``"<group> <verb>"``. Top-level `--help` names only the group, so a build
    that has ``job status`` but not ``job wait`` would pass a help-text check. Its group must
    still be in `--help`.

    A name in ``features`` (an upload verb's `parts-only-upload`) is read from the same
    `capabilities` answer: a build that predates the key, or has no `capabilities`, offers none."""
    everything = [*required, *features]
    try:
        proc = subprocess.run(  # noqa: S603 - probing the candidate binary is the whole point
            [candidate, '--help'],
            capture_output=True,
            text=True, **utf8_child_text(),
            timeout=60,
            env=_probe_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f'resolve_plurai: {candidate} --help failed to run: {exc}', file=sys.stderr)
        return everything
    if proc.returncode != 0:
        return everything
    missing = [cmd for cmd in required if cmd.split()[0] not in proc.stdout]
    grouped = [cmd for cmd in required if len(cmd.split()) > 1 and cmd not in missing]
    if not grouped and not features:
        return missing
    capabilities = _capabilities(candidate)
    commands = capabilities.get('commands')
    listed = {' '.join(str(name).split()) for name in commands} if isinstance(commands, list) else set()
    missing.extend(cmd for cmd in grouped if cmd not in listed)
    offered = capabilities.get('features')
    missing.extend(name for name in features if not (isinstance(offered, list) and name in offered))
    return [name for name in everything if name in missing]


@functools.cache
def _capabilities(candidate):
    """`<candidate> capabilities` as a dict, or ``{}`` on any failure (an SDK that predates the
    subcommand, a crash, output that is not a JSON object). Cached per path: the capability
    probe and the identity line read the same answer, and each call starts an interpreter."""
    try:
        proc = subprocess.run(  # noqa: S603 - same probe posture as _missing above
            [candidate, 'capabilities'],
            capture_output=True,
            text=True, **utf8_child_text(),
            timeout=60,
            check=False,
            env=_probe_env(),
        )
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def sdk_install_args(extras=()):
    """`"plurai-simulation-sdk[<extras>]==<pin>"`: the one install fact every skill and runtime message defers to.

    The requirement is the exact build `PINNED_SDK_VERSION` with the extras a skill needs. It names no index: pip and uv
    read theirs from the environment, so a machine set up for the private dev channel resolves the pin there and any
    other machine resolves it on PyPI. An exact `==` pin selects a pre-release without an extra flag."""
    extra = f'[{",".join(extras)}]' if extras else ''
    return f'"{SDK_DISTRIBUTION}{extra}=={PINNED_SDK_VERSION}"'


#: The line `--install-line` prints and the skills point to.
INSTALL_LINE = f'pip install {sdk_install_args()}'

UPGRADE_HINT = (
    "install the SDK in the client's venv (the one the agent uses) at the one release this plugin was tested "
    f'with: `{INSTALL_LINE}` inside it (the same requirement after `.venv/bin/pip install` for a repo .venv, '
    '`uv pip install` for a uv venv, `uv tool install` for a project with no venv) — then re-run this '
    'resolver. Envs whose anonymization posture is anonymized (`anonymize: true` in plurai.config.yaml) '
    f'need the extra: `pip install {sdk_install_args(("anonymize",))}`.'
)


#: A version is a STANDALONE dotted-numeric token: preceded by start-of-string, whitespace or a `v`, so a digit pair
#: inside an interpreter path such as `python3.13` is never read as one. It is taken
#: with whatever release suffix follows it (`1.0.0rc1`, `1.0.0.dev3`, `1.0.0+local`), so a pre-release or a local build
#: of the pinned number is not mistaken for the release itself.
_VERSION_TEXT = re.compile(r'(?:^|(?<=\s)|(?<=v))(\d+\.\d+(?:\.\d+)?[0-9A-Za-z.+!_-]*)')


def _version_text(text):
    """The first standalone version token of a `--version` line, suffix included, or None."""
    match = _VERSION_TEXT.search(text or '')
    return match[1] if match else None


def _version_key(text):
    """The handshake's order key (`_client_handshake.version_key`) of the first standalone version token of a `--version`
    line, or None.

    The one ordering the install offer and the backend's floor both use, so they agree on which release is older. A token
    the handshake cannot place (`1.0`, `1.0.0+local`) or no token at all yields None, which is never "plainly older": a
    version we cannot order is not evidence of a version we can.
    """
    _use_sibling_modules()
    try:
        import _client_handshake  # noqa: PLC0415 - sibling script; the one owner of the version ordering
    except Exception:  # noqa: BLE001 - an ordering that is not there places nothing
        return None
    return _client_handshake.version_key(_version_text(text))


def _sdk_in_range(version):
    """Whether ``version`` is a release this plugin accepts: the pin's `major.minor`, at or above the pin.

    Ordered by the handshake's key, so a pre-release is below its release and `1.1.0.dev405`, `1.1.0-dev.0` and `1.1.0rc1`
    compare as pins and floors do. A version the handshake cannot place (no version, `1.1`, a local build) is outside the
    range: nothing is accepted that cannot be ordered."""
    have, need = _version_key(version), _version_key(PINNED_SDK_VERSION)
    return have is not None and need is not None and have[:2] == need[:2] and have >= need


def _pin_line():
    """The pin's `major.minor` (`1.0`), the release line the plugin accepts."""
    return '.'.join(PINNED_SDK_VERSION.split('.')[:2])


def _version(candidate):
    """The resolved SDK's own version string, or None. `--version` landed after 0.3.0, so a
    missing subcommand — or any failure at all — is SILENCE, never an error: the resolve has
    already succeeded, and this line exists only to make version skew visible in run logs."""
    try:
        proc = subprocess.run(  # noqa: S603 - same probe posture as _missing above
            [candidate, '--version'],
            capture_output=True,
            text=True, **utf8_child_text(),
            timeout=60,
            env=_probe_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout.strip() or proc.stderr.strip()).splitlines()
    return out[0].strip() if out and out[0].strip() else None


def _identity(candidate):
    """The resolved SDK's OWN identity, from `plurai capabilities`, or None.

    ONE identity for one binary. This script and `client_pin.py` must describe the same file the
    same way; one that resolved a COMMIT while the other read DIST METADATA would report two
    identities for one binary, because a legacy editable install leaves the dist metadata at an
    older version than the tree it points at.

    `capabilities` is the machine-readable surface and it carries `identity` (commit + dirty for
    a checkout, dist version + content hash for a wheel). None on any failure — an older SDK
    whose `capabilities` predates the key, or no subcommand at all — and None is SILENCE, never
    a finding: the resolve already succeeded.
    """
    identity = _capabilities(candidate).get('identity')
    return identity if isinstance(identity, dict) else None


def _runner_version(path):
    """``(identity, version)`` of the runner at ``path``. The identity's own version decides when it states one; the
    `--version` string (dist metadata, which a stale editable install can leave behind) only when it does not."""
    identity = _identity(path)
    stated = identity.get('version') if identity else None
    return identity, str(stated) if stated and _version_text(str(stated)) is not None else _version(path)


def _identity_line(path, identity):
    """The one-line rendering of a resolved runner's identity — the SAME sentence `client_pin`
    prints, so a reader comparing two lines is comparing two facts and not two spellings."""
    if not identity:
        return None
    if identity.get('source') == 'git' and identity.get('commit'):
        dirty = '-dirty' if identity.get('dirty') else ''
        return f'resolve_plurai: {path} is plurai {identity.get("version")} @ {str(identity["commit"])[:12]}{dirty}'
    if identity.get('source') == 'dist' and identity.get('file_hash'):
        return f'resolve_plurai: {path} is plurai {identity.get("version")} dist:{identity["file_hash"]}'
    return None


def _version_notice(path, version):
    """The ONE line the run log gets about runner-vs-plugin version skew. `resolve` refuses a runner whose
    version is outside the range (`_pin_refusal`) before printing this, so the lines below are the cases that remain.

    A pure function of (path, version string) so each case is testable without a runner on disk. They are
    genuinely three, and collapsing any two of them is how a version fact stops being usable: the pinned
    release (a plain fact), UNPARSEABLE (we could not check, which is not evidence of health) and ABSENT (a
    probe also fails on a timeout or a non-zero exit, so the line does not say which).
    """
    if version is None:
        return (
            f'resolve_plurai: {path} reported no version (`--version` failed or is absent) — '
            f'this plugin release is tested with {PINNED_SDK_VERSION} and later releases of its {_pin_line()} line.'
        )
    if _version_text(version) is None:
        return f'resolve_plurai: {path} ({version}) — version UNPARSEABLE, so it could not be checked against the {PINNED_SDK_VERSION} this plugin release is tested with.'
    return f'resolve_plurai: {path} ({version})'


#: Exit code when the resolver REFUSES the runner it found: the client pin forbids it, or it reports a release
#: outside the range of `_sdk_in_range`. Distinct from 1 ("nothing usable") on purpose: the run
#: skill's resolver line falls back to a PATH runner on 1, and must not on 3 — falling back to
#: another copy of a refused runner is precisely the stale-client run this exists to prevent.
EXIT_RUNNER_REFUSED = 3


class _Refusal(NamedTuple):
    """A runner refused for its release: ``printed`` is everything the resolver prints on stderr, and ``said`` the part a
    customer reads and a headless run driver files (the stop lines, or the whole sentence when it has none).
    ``sanctioned`` is ``printed`` for a question whose ask the resolver sanctioned itself (:func:`_sanction_blocker`); it is
    empty for a refusal that asks nothing."""

    printed: str
    said: str
    sanctioned: str = ''


def _pin_stop(path, version, ask=False, resolve_args=()):
    """The :class:`_Refusal` when ``path`` reports a release other than the pin, or ``None``.

    A version that cannot be read or parsed is not another version: `_version_notice` names it instead. The text is
    the diagnostic line, then what the caller does with it: the stop text (`_stop_text`) to say as it stands or, only
    when ``ask`` is set and the runner is an older one that may be offered the install, the question (`_ask_text`)
    with the stop text for the answer that declines it. ``ask`` belongs to the command line a skill runs; a caller that
    imports this module gets the stop text. An older release and one of another minor are refused alike and the same command
    fixes both; only an older one is ever offered the install.
    """
    stated = _version_text(version) if version else None
    if stated is None or _sdk_in_range(stated):
        return None
    head = (
        f'resolve_plurai: REFUSED — {path} is plurai {stated}; this plugin release works with the {_pin_line()} releases from '
        f'{PINNED_SDK_VERSION}, which is what it was tested with.'
    )
    install = _runner_install(path)
    if install.unsafe:
        said = _unquotable_message(install.unsafe).removeprefix('resolve_plurai: ')
        return _Refusal(f'{head} {said}', f'{head} {said}')
    stop = _stop_for(install.command, install.project)
    if ask and _older_target(path, stated) is not None:
        question = _question_text(stated, _environment_label(install.environment), install.tool, _writes(install.project))
        return _Refusal(
            f'{head}\n{_ask_text(question, stop, resolve_args)}', stop, f'{head}\n{_ask_text(question, stop, resolve_args, sanctioned=True)}'
        )
    return _Refusal(f'{head} Say the next lines to the user, verbatim and alone, and run nothing after them:\n{stop}', stop)


def _pin_refusal(path, version, ask=False, resolve_args=()):
    """The text :func:`_pin_stop` prints, or ``None``: the operator text for a runner that is not the pin."""
    stop = _pin_stop(path, version, ask, resolve_args)
    return None if stop is None else stop.printed


#: Lock files that make a project's own dependencies the owner of what its environment holds.
_LOCK_FILES = ('uv.lock', 'poetry.lock', 'Pipfile.lock', 'pdm.lock')

#: The question, as the caller asks it: a header, a text and two options, in that order.
SDK_QUESTION_HEADER = 'Plurai SDK'
SDK_QUESTION_OPTIONS = ('Install it', 'Not now')

#: The lock writers whose files the plugin changes, each with the two files a customer is told about.
_LOCK_TOOLS = {'uv': 'pyproject.toml and uv.lock', 'poetry': 'pyproject.toml and poetry.lock'}


def _names(files):
    """File names as a sentence reads them: `a`, `a and b`, `a, b and c` (sorted)."""
    names = sorted(os.path.basename(path) for path in files)
    return names[0] if len(names) == 1 else f'{", ".join(names[:-1])} and {names[-1]}'


def _changes(tool, files=()):
    """The sentence that tells a customer what adding the SDK to a project's lock or requirements file does."""
    if tool == 'requirements':
        return f'It changes {_names(files)}, can change other packages in the environment, and Plurai commands wait until it is done.'
    return f'It changes {_LOCK_TOOLS[tool]}, can change other packages in the lock, and Plurai commands wait until it is done.'


#: The question for a uv project whose dependencies hold no SDK. `harness-setup` asks the same words.
LOCK_SDK_QUESTION = f'Plurai needs its SDK in this project. Add it to the dev dependencies now? {_changes("uv")}'

#: The same for a Poetry project.
POETRY_SDK_QUESTION = f'Plurai needs its SDK in this project. Add it to the dev dependencies now? {_changes("poetry")}'


def requirements_sdk_question(files):
    """The question for a requirements project whose files list the SDK that no environment holds: it names the files."""
    return f'Plurai needs its SDK in this project. Install it now? {_changes("requirements", files)}'

#: The question for a project environment that holds no SDK at all. `harness-setup` asks the same words, so a customer meets one
#: question however the SDK turns out to be missing.
MISSING_SDK_QUESTION = 'Plurai needs its SDK in this project. Install it now?'


def _runner_python(path):
    """The interpreter beside a runner: the one its environment installs with."""
    return os.path.join(os.path.dirname(path), 'python.exe' if path.lower().endswith('.exe') else 'python')


def _shebang_python(path):
    """The absolute interpreter a console script starts with (`#!/…/python3`), or ``None`` when it names no Python."""
    try:
        with open(os.path.realpath(path), 'rb') as handle:
            first = handle.readline(512).decode('utf-8', 'replace').strip()
    except OSError:
        return None
    interpreter = first[2:].strip() if first.startswith('#!') else ''
    if os.path.isabs(interpreter) and os.path.basename(interpreter).startswith('python') and os.access(interpreter, os.X_OK):
        return interpreter
    return None


class _Install(NamedTuple):
    """Where a runner lives and the one command that puts the pin there (see :func:`_runner_install`). ``tool`` is the lock
    writer the command belongs to (:class:`_Locked`), or ``None`` for an installer of the environment itself."""

    environment: object
    python: str
    command: object
    unsafe: object
    tool: object = None
    project: object = None


def _runner_install(path):
    """The :class:`_Install` of the runner at ``path``: ``(environment, interpreter, command, unsafe)``, where it lives and the one command that
    puts the pin there. ``environment`` is a venv or conda environment of its own, else ``None``; ``unsafe`` is the first
    path the shell would act on, which no command can carry (``command`` is then ``None``); ``tool`` is the lock writer of a
    project that keeps a lock (:func:`_locked_project`).

    The command follows how the runner was installed, because a command for any other place leaves this runner as it is:
    the environment's own installer for a venv or conda environment (`python -m pip`, or `uv pip --python` where there
    is no pip), `uv tool install --force` for a uv tool and `pipx install --force` for a pipx application, which the
    runner on PATH reaches through a link into their own venv, and the interpreter the script starts with
    (`<python> -m pip install`) for a script installed into some other Python. A runner none of these describe gets the
    environment's own installer as for a venv, or the project's lock writer where the project keeps a lock."""
    spec = sdk_install_args()
    real = os.path.realpath(path)
    environment, python = os.path.dirname(os.path.dirname(path)), _runner_python(path)
    command, named = None, [environment, python]
    if not _is_env(environment):
        owner = os.path.dirname(os.path.dirname(real))
        parts = real.split(os.sep)
        if _is_env(owner) and 'tools' in parts and 'uv' in parts:
            environment, command, named = None, f'{_uv() or "uv"} tool install --force {spec}', []
        elif _is_env(owner) and 'venvs' in parts and 'pipx' in parts:
            environment, command, named = None, f'pipx install --force {spec}', []
        elif (interpreter := _shebang_python(path)) is not None:
            environment, python = None, interpreter
            command, named = f'"{interpreter}" -m pip install {spec}', [interpreter]
    if unsafe := _unquotable(*named):
        return _Install(environment, python, None, unsafe)
    if command:
        return _Install(environment, python, command, None)
    command, project = _venv_install(environment, python=python)
    return _Install(environment, python, command, None, project.tool if project else None, project)


def _environment_label(environment):
    """How a customer names an environment: relative to the project folder when it sits inside it (`.venv`), else its path."""
    root = _project_root()
    return os.path.relpath(environment, root) if _inside(environment, root) else environment


def _version_below_pin(stated):
    """Whether ``stated`` is a release plainly before the pin: both parse in the handshake's ordering and it is lower.

    A build the ordering cannot place (a local build, a suffix it has no rank for) is not plainly older, so it is never
    offered the install."""
    have, need = _version_key(stated), _version_key(PINNED_SDK_VERSION)
    return have is not None and need is not None and have < need


def _older_than_pin(path):
    """Whether the runner at ``path`` reports a release plainly before the pin, by the same reading `resolve` checks the pin with."""
    _, version = _runner_version(path)
    return bool(version) and _version_below_pin(_version_text(version))


def _headless():
    """Whether no person answers: a run driver declared headless, or one that cannot be read (`_run_driver.nobody_answers`,
    which fails closed: a question nobody can answer is worse than a stop with the command)."""
    try:
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path (top of this file)
    except Exception:  # noqa: BLE001 - a run driver that cannot be loaded cannot be asked, and is read as headless
        return True
    return _run_driver.nobody_answers()


def _older_target(path, stated):
    """``(environment, interpreter)`` when the runner at ``path`` is plainly older than the pin, a person can answer, and its
    environment is a venv or conda environment with its own interpreter, else ``None``. The one test behind the question
    and behind the install, so nothing is installed that was not asked."""
    if not _version_below_pin(stated) or _headless():
        return None
    install = _runner_install(path)
    if install.unsafe or not install.environment or not _is_env(install.environment) or not os.access(install.python, os.X_OK):
        return None
    if _manual(install.project):
        return None
    return install.environment, install.python


def _missing_target():
    """``(environment, interpreter)`` when the project has an environment of its own that holds no runner, a person can answer
    and the SDK can go into it (its interpreter runs, its path is safe to quote, its Python is one the SDK installs on),
    else ``None``. The one test behind the question for a missing SDK and behind the install that answers it."""
    if _headless():
        return None
    if _manual(_locked_project()):
        return None
    environment = _target_venv()
    if not environment or not _is_env(environment) or _unquotable(environment):
        return None
    python = _venv_python(environment)
    version = _venv_python_version(environment)
    if _unquotable(python) or not os.access(python, os.X_OK) or (version is not None and version < SDK_MIN_PYTHON):
        return None
    return environment, python


def _has_lock_file():
    """Whether the project keeps a lock file: its own dependencies then decide what a sync leaves in the environment."""
    root = _project_root()
    return any(os.path.isfile(os.path.join(root, name)) for name in _LOCK_FILES)


def _stop_text(command, locked=None, tool=None, files=None):
    """What the customer reads when the SDK is not the release this plugin needs and nothing is installed: the stop line,
    for a project with a lock file (``locked``, read from the project when not given) how to keep it, and last the one
    command, so that nothing follows the line the customer copies.

    ``tool`` is the lock writer the command belongs to (`_Locked`): its command already adds the SDK to the project's own
    dependencies, so only a requirements file says where the line goes. ``files`` is for a requirements project the plugin
    writes: the files that list the SDK, whose line the customer sets by hand beside the command (``()`` says nothing); with
    no ``files`` the command is the requirement line itself, which the customer adds to the file."""
    lines = [f'Plurai commands need {SDK_DISTRIBUTION} {PINNED_SDK_VERSION} in this project. Install it when ready, then run the command again:']
    if tool == 'requirements' and files:
        lines.append(f'This project lists {SDK_DISTRIBUTION} in {_names(files)}; change that line to {SDK_DISTRIBUTION}=={PINNED_SDK_VERSION} too.')
    elif tool == 'requirements' and files is None:
        lines.append('This project keeps its dependencies in requirements.txt; add this line to it, then install the file.')
    elif tool is None and (_has_lock_file() if locked is None else locked):
        lines.append(f"This project has a lock file; adding {SDK_DISTRIBUTION} {PINNED_SDK_VERSION} to the project's own dependencies keeps it there.")
    lines.append(command)
    return '\n'.join(lines)


def _question_text(stated, label, tool=None, files=()):
    """The install question's text, naming both releases and the environment, or for a project that keeps its dependencies in a
    lock or a requirements file the files the install changes (``files``: the requirements files whose line is set)."""
    if tool in _LOCK_TOOLS:
        return (
            f'This plugin release needs {SDK_DISTRIBUTION} {PINNED_SDK_VERSION}; this project has the older {stated}. '
            f'Add {PINNED_SDK_VERSION} to the dev dependencies now? {_changes(tool)}'
        )
    if tool == 'requirements' and files:
        return (
            f'This plugin release needs {SDK_DISTRIBUTION} {PINNED_SDK_VERSION}; this project has the older {stated}. '
            f'Install {PINNED_SDK_VERSION} into {label} now? {_changes(tool, files)}'
        )
    return (
        f'This plugin release needs {SDK_DISTRIBUTION} {PINNED_SDK_VERSION}; this project has the older {stated}. '
        f'Install {PINNED_SDK_VERSION} into {label} now? It may change other packages there, and Plurai commands wait until it is done.'
    )


def _installed_line(label):
    return f'Installed {SDK_DISTRIBUTION} {PINNED_SDK_VERSION} into {label}.'


def _install_failed_line(label):
    return f'Plurai could not install {SDK_DISTRIBUTION} {PINNED_SDK_VERSION} into {label}.'


#: What `flow-window.sh sanction blocker` prints when it wrote the one-shot sanction: the ask is allowed.
_SANCTIONED_LINE = 'flow-window: one question allowed (blocker)'
#: One local script; a sanction that takes longer is not coming.
_SANCTION_TIMEOUT = 30


def _sanction_blocker():
    """Whether `flow-window.sh sanction blocker` ran and allowed the one question, so the caller's next action can be the ask.

    The script is the one writer of the sanction, so this runs it; nothing else writes the marker. It prints the allowance
    only where a person answers, which is where the question is printed. Anything else (no shell to run it, another line)
    returns ``False`` and the printed text tells the caller to run the sanction itself. The child runs without the bearer."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flow-window.sh')
    env = _probe_env()
    bash = _platform.git_bash(env)
    if bash is None:
        return False
    try:
        done = subprocess.run(
            [bash, script, 'sanction', 'blocker'], capture_output=True, text=True, check=False, timeout=_SANCTION_TIMEOUT, env=env,
            **utf8_child_text(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.startswith(_SANCTIONED_LINE)


def _ask_text(question, stop, resolve_args, sanctioned=False):
    """What the caller reads to ask the one install question (``question``, as the ask tool takes it): what each answer does,
    and the stop text for `Not now` or for a session that cannot ask or whose ask is denied. With ``sanctioned`` the resolver
    has written the ask's sanction already, so the ask tool is the caller's next action; otherwise the caller writes it first."""
    yes = ' '.join(['python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resolve_plurai.py" --install-pin', *(shlex.quote(arg) for arg in resolve_args)])
    lead = (
        'Ask the user this one question, once, through the blocker gate. This call has already sanctioned it, '
        'so the ask tool is your next action, with no text before it: call it with exactly:\n'
        if sanctioned
        else 'Ask the user this one question, once, through the blocker gate: run '
        '`bash "${CLAUDE_PLUGIN_ROOT}/scripts/flow-window.sh" sanction blocker` first, then call the ask tool with exactly:\n'
    )
    return (
        lead + f'header: {SDK_QUESTION_HEADER}\n'
        f'question: {question}\n'
        f'options: {" | ".join(SDK_QUESTION_OPTIONS)}\n'
        f'On `{SDK_QUESTION_OPTIONS[0]}`: run `{yes}`, tell the user its last line, then run the command that stopped again from its start.\n'
        f'On `{SDK_QUESTION_OPTIONS[1]}`, or when the question is denied or cannot be asked: say the next lines to the user, '
        'verbatim and alone, and run nothing after them:\n'
        f'{stop}'
    )


#: How long the install may run. An index that is slow is not a reason to leave the command waiting forever.
_INSTALL_TIMEOUT = 900


def _install_pin(path, target):
    """Install exactly the pin into ``target``, an environment's ``(directory, interpreter)``, and say so: exit 0 and the
    `Installed` line on stdout when the runner then reports the pin, else exit `EXIT_RUNNER_REFUSED` and, on stderr, one
    sentence and the stop text whose command the customer can run to see the installer's own error. ``path`` is the runner
    being replaced; ``None`` for an environment that holds none, where the runner is looked for once the install is done.

    The installer runs without the bearer, like every other child of this script that is not the SDK."""
    environment, python = target
    label = _environment_label(environment)
    command, project = _venv_install(environment, python=python, running=True)
    shown, _ = _venv_install(environment, python=python)
    files = _lock_files(project)
    saved = _Restore(files)
    output = ''
    try:
        if project is not None and project.tool == 'requirements':
            _set_requirement_lines(files)
        done = subprocess.run(  # noqa: S602 - the line this resolver composes, from paths `_older_target` found safe to quote
            command, shell=True, capture_output=True, text=True, check=False, timeout=_INSTALL_TIMEOUT, env=_probe_env(),
            cwd=project.directory if project is not None and project.tool == 'poetry' else None, **utf8_child_text(),
        )
        installed, output = done.returncode == 0, f'{done.stdout or ""}\n{done.stderr or ""}'
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        installed = False
    if installed:
        _capabilities.cache_clear()
        runner = path or _venv_runner(environment)
        installed = os.access(runner, os.X_OK) and _version_text(_runner_version(runner)[1]) == PINNED_SDK_VERSION
    if not installed:
        saved.put_back()
        print(_install_failed_text(label, project, shown, output), file=sys.stderr)
        return EXIT_RUNNER_REFUSED
    print(_installed_line(label))
    return 0


def _install_failed_text(label, project, command, output):
    """What the customer reads when the install did not leave the pin in place: the failure line, for a project the plugin
    writes the files that were put back (and for Poetry its own reason), then the stop text with the line to run by hand."""
    lines = [_install_failed_line(label)]
    if project is not None and project.tool == 'poetry' and not project.manual:
        if reason := _solver_reason(output):
            lines.append(f'Poetry said:\n{reason}')
        lines.append('pyproject.toml and poetry.lock are as they were.')
    elif project is not None and project.tool == 'requirements' and project.files and not project.manual:
        lines.append(f'{_names(project.files)} {"is as it was" if len(project.files) == 1 else "are as they were"}.')
    lines.append(_stop_for(command, project))
    return '\n'.join(lines)


def _venv_config(venv):
    """The `key = value` lines of the venv's `pyvenv.cfg`, or ``{}`` when it has none."""
    try:
        with open(os.path.join(venv, 'pyvenv.cfg'), encoding='utf-8') as cfg:
            return {key.strip(): value.strip() for key, sep, value in (line.partition('=') for line in cfg) if sep}
    except OSError:
        return {}


def _has_pip(venv):
    """Whether pip is installed in the environment. uv makes venvs without it, and so does `venv --without-pip`."""
    return bool(glob.glob(os.path.join(venv, 'lib', 'python*', 'site-packages', 'pip'))) or os.path.isdir(os.path.join(venv, 'Lib', 'site-packages', 'pip'))


def _venv_python(venv):
    """The environment's interpreter: `bin/python`, or `Scripts\\python.exe` on Windows (`python.exe` at a conda
    environment's root)."""
    if _platform.WINDOWS and os.path.isdir(os.path.join(venv, 'conda-meta')):
        return os.path.join(venv, 'python.exe')
    if os.path.isdir(os.path.join(venv, 'Scripts')) or (_platform.WINDOWS and not os.path.isdir(os.path.join(venv, 'bin'))):
        return os.path.join(venv, 'Scripts', 'python.exe')
    return os.path.join(venv, 'bin', 'python')


def runner_python(runner):
    """The interpreter of the environment a resolved `plurai` runner is installed in: the one that imports the SDK.

    A runner reached through a link (pipx and `uv tool` put one in `~/.local/bin`) is followed to the environment that holds it."""
    return _venv_python(os.path.dirname(os.path.dirname(os.path.realpath(runner))))


#: Characters a printed command cannot carry inside double quotes without the shell acting on them. A skill runs the
#: command it is given unchanged, and a folder name comes from the repository, so a path holding one is refused, never
#: quoted. A backslash is a path separator on Windows, so it is refused only elsewhere.
_SHELL_ACTIVE = re.compile(r'["`$!\r\n]' if _platform.WINDOWS else r'["`$!\\\r\n]')


def _unquotable(*paths):
    """The first of ``paths`` that holds a character `_SHELL_ACTIVE` refuses, or ``None``."""
    return next((path for path in paths if _SHELL_ACTIVE.search(path)), None)


def _unquotable_message(path):
    return (
        f'resolve_plurai: {path!r} holds a character the shell would act on (" ` $ ! a backslash or a line break), so '
        'no command is printed for it. Rename the folder, then run the setup again.'
    )


#: Where uv's own installer puts `uv`. A harness started outside a login shell (a desktop app) may not have it on PATH.
_UV_HOMES = ('~/.local/bin', '~/.cargo/bin')


def _uv_home_tool(tool):
    """``tool`` (`uv` or `uvx`) as a printed command names it: the bare word when it is on PATH, else the quoted path of the
    one in `_UV_HOMES`, else ``None``. A path the shell would act on is not printed."""
    if shutil.which(tool):
        return tool
    for home in _UV_HOMES:
        for name in (f'{tool}.exe', tool) if _platform.WINDOWS else (tool,):
            path = os.path.join(os.path.expanduser(home), name)
            if os.path.isfile(path) and os.access(path, os.X_OK) and not _unquotable(path):
                return f'"{path}"'
    return None


def _uv():
    """`uv` as a printed command names it, or ``None`` (see :func:`_uv_home_tool`)."""
    return _uv_home_tool('uv')


#: The Poetry release `uvx` fetches when `poetry` is not on PATH, and the index it comes from. The repository is untrusted
#: and `uvx` reads the `uv.toml` and `pyproject.toml` of the folder it runs in (an index, a source), so the tool itself is
#: fetched from a fixed place with that configuration off (`--no-config`) and one release nobody else chose. Poetry then runs
#: in the project and reads the project's own sources for the SDK, as it does for the customer. Moving this release is a
#: plugin release of its own. A Poetry judges a lock written by a newer Poetry stale (`[dependency-groups]`, a supplemental
#: source), and its `add` then fails or rewrites the lock, so the release stays at or above the current Poetry and the
#: project's own Poetry version is never read to choose it.
POETRY_VIA_UVX = '2.5.1'
POETRY_TOOL_INDEX = 'https://pypi.org/simple'


def _poetry():
    """`poetry` as a printed command names it: the bare word when it is on PATH, else Poetry through `uvx` (fetched from
    `POETRY_TOOL_INDEX` at `POETRY_VIA_UVX`, with no project configuration) where uv's tool runner is on hand, else ``None``.
    ``uvx --default-index`` needs uv 0.4.23 or newer; an older uv fails the add, and the files are put back."""
    if shutil.which('poetry'):
        return 'poetry'
    uvx = _uv_home_tool('uvx')
    return f'{uvx} --no-config --default-index {POETRY_TOOL_INDEX} poetry@{POETRY_VIA_UVX}' if uvx else None


def _install_into(venv, spec, python=None):
    """The command that installs ``spec`` into ``venv`` with that venv's own interpreter (``python``, else found).

    The installer follows what the environment holds, not the tool that made it: `python -m pip` where pip is
    installed; `uv pip --python` where it is not, or, with no uv to name, pip from `ensurepip` first. Naming the
    interpreter makes the install land where the resolver looks next, whatever venv the shell has activated."""
    python = python or _venv_python(venv)
    if _has_pip(venv):
        return f'"{python}" -m pip install {spec}'
    uv = _uv()
    return f'{uv} pip install --python "{python}" {spec}' if uv else f'"{python}" -m ensurepip && "{python}" -m pip install {spec}'


#: The oldest Python the SDK installs on.
SDK_MIN_PYTHON = (3, 12)


class _Locked(NamedTuple):
    """A project that keeps its dependencies in a lock or a requirements file: ``tool`` is `uv`, `poetry` or `requirements`;
    ``directory`` is where the dependency is added (uv: the folder of the nearest `pyproject.toml`; else where the file is).
    ``files`` are the requirements files that list the SDK, whose line is set to the pin. ``manual`` is set where the plugin
    cannot write the project and only prints the line."""

    tool: str
    directory: str
    files: tuple = ()
    manual: bool = False


def _manual(project):
    """Whether ``project`` is one the plugin only prints the line for."""
    return project is not None and project.manual


def _writes(project):
    """The requirements files the plugin sets a line in for ``project``, as the install question names them."""
    return project.files if project is not None and project.tool == 'requirements' and not project.manual else ()


#: A requirement on the SDK in a requirements file: the name (any spelling pip reads as the same), its extras, then the rest.
_SDK_REQUIREMENT = re.compile(r'(?P<head>\s*plurai[-_.]+simulation[-_.]+sdk\s*(?:\[[^\]]*\])?)(?P<tail>.*)', re.IGNORECASE | re.DOTALL)
_REQUIREMENT_INCLUDE = re.compile(r'\s*(?:-r|--requirement)[\s=]*(\S+)')


def _updated_requirement(line):
    """``line`` with its version range on the SDK replaced by `==<pin>` (extras, marker, comment and line ending kept), or
    ``None`` when the line is no requirement on a release of the SDK: another package, or a requirement that names a source."""
    match = _SDK_REQUIREMENT.fullmatch(line)
    if not match:
        return None
    tail = match.group('tail')
    if tail.strip() and tail.lstrip()[0] not in '=<>~!;#':
        return None
    cut = next((i for i, char in enumerate(tail) if char in ';#'), len(tail))
    spec, rest = tail[:cut], tail[cut:]
    return f"{match.group('head').rstrip()}=={PINNED_SDK_VERSION}{spec[len(spec.rstrip()):]}{rest}"


def _requirements_listing(directory):
    """``(files, hashed)``: the requirements files of ``directory`` (`requirements*.txt` and what they include with `-r`,
    inside the folder) that list the SDK, sorted, and whether any of them carries hashes."""
    seen, listed, hashed = set(), [], False

    def visit(path):
        nonlocal hashed
        real = os.path.realpath(path)
        if real in seen or not os.path.isfile(real) or not _inside(real, os.path.realpath(directory)):
            return
        seen.add(real)
        try:
            with open(real, encoding='utf-8', newline='') as handle:
                lines = handle.read().splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            return
        for line in lines:
            if include := _REQUIREMENT_INCLUDE.match(line):
                visit(os.path.join(os.path.dirname(real), include.group(1)))
        if any(_updated_requirement(line) is not None for line in lines):
            listed.append(real)
            hashed = hashed or any('--hash' in line for line in lines)

    for path in sorted(glob.glob(os.path.join(directory, 'requirements*.txt'))):
        visit(path)
    return tuple(sorted(listed)), hashed


def _project_span():
    """The directories from the cwd up to the project boundary, nearest first.

    The boundary is the first directory holding `.git`: a workspace member holds `plurai_sim/` below the lock that
    belongs to it, so `plurai_sim/` ends the walk only where no `.git` is above. Without any boundary the span is the cwd
    alone, so a lock in some parent folder of an unrelated tree is never read."""
    start = os.path.abspath(os.getcwd())
    span, directory, sim = [], start, None
    while True:
        span.append(directory)
        if sim is None and os.path.isdir(os.path.join(directory, 'plurai_sim')):
            sim = len(span)
        if os.path.exists(os.path.join(directory, '.git')):
            return span
        parent = os.path.dirname(directory)
        if parent == directory:
            return span[:sim] if sim else [start]
        directory = parent


def _locked_project():
    """The :class:`_Locked` of the project at the cwd, or ``None`` when it keeps no lock the plugin can write for.

    * `uv.lock` at or above the agent, with a `pyproject.toml` at or below it and `uv` on hand: uv. A workspace member
      adds to its own `pyproject.toml` and the lock above it.
    * `poetry.lock`: poetry, which the plugin runs (`poetry`, else Poetry through `uvx`, `_poetry`) when a `pyproject.toml` lies beside the lock;
      else the line is printed for the user.
    * `requirements.txt` and no lock: the requirements file, whose listed SDK line the plugin sets and whose environment gets
      the pin; a file with hashes is printed for the user.

    A `uv.lock` with no `pyproject.toml` to add to, or with no `uv`, is no uv project: the venv install stays."""
    span = _project_span()
    for index, directory in enumerate(span):
        if os.path.isfile(os.path.join(directory, 'uv.lock')):
            project = next((d for d in span[: index + 1] if os.path.isfile(os.path.join(d, 'pyproject.toml'))), None)
            if project is not None and _uv() and not _unquotable(project):
                return _Locked('uv', project)
            break
    for tool, name in (('poetry', 'poetry.lock'), ('requirements', 'requirements.txt')):
        for directory in span:
            if os.path.isfile(os.path.join(directory, name)) and not (tool == 'requirements' and _has_lock_file()):
                if tool == 'poetry':
                    return _Locked(tool, directory, manual=not (os.path.isfile(os.path.join(directory, 'pyproject.toml')) and _poetry()))
                files, hashed = _requirements_listing(directory)
                return _Locked(tool, directory, files, manual=hashed)
    return None


def sdk_lock_requirement(extras=(), quote='"'):
    """`plurai-simulation-sdk[<extras>]==<pin> ; python_version >= "3.12"`: the requirement a project's dependencies list,
    with the marker's value in ``quote`` (a shell line that wraps the requirement in double quotes passes `'`). The marker
    is there because a project whose `requires-python` reaches below the SDK's Python cannot resolve the SDK without it."""
    extra = f'[{",".join(extras)}]' if extras else ''
    return f'{SDK_DISTRIBUTION}{extra}=={PINNED_SDK_VERSION} ; python_version >= {quote}{SDK_MIN_PYTHON[0]}.{SDK_MIN_PYTHON[1]}{quote}'


def _lock_command(project, extras=(), running=False):
    """The line that adds the SDK to ``project``'s own dependencies. The plugin runs uv's and Poetry's after a yes (``running``
    adds Poetry's `--no-plugins`, because the repository is untrusted); a requirements file takes the requirement itself."""
    extras, single = sorted(set(extras)), "'"
    if project.tool == 'uv':
        return f'{_uv()} add --dev --directory "{project.directory}" "{sdk_lock_requirement(extras, quote=single)}"'
    if project.tool == 'poetry':
        return f'{"poetry" if project.manual else _poetry() or "poetry"}{" --no-plugins" if running else ""} add --group dev "{sdk_lock_requirement(extras, quote=single)}"'
    return sdk_lock_requirement(extras)


def _venv_install(venv, extras=(), python=None, running=False):
    """``(command, project)``: the command that puts the pin (with ``extras``) into the project's environment, and the
    :class:`_Locked` project it belongs to, or ``None`` for the environment's own installer.

    A project that keeps a lock gets the SDK through it, so the lock holds the SDK and a later sync does not drop it
    (:func:`_locked_project`); any other project, and a requirements project the plugin writes (whose file the caller sets
    beside the install), gets `pip` or `uv pip` into ``venv``."""
    project = _locked_project()
    if project is not None and (project.tool != 'requirements' or project.manual):
        return _lock_command(project, extras, running), project
    return _install_into(venv, sdk_install_args(sorted(set(extras))), python), project


def _stop_for(command, project):
    """The stop text for ``command``, the install of ``project`` (a :class:`_Locked`, or ``None``)."""
    if project is None:
        return _stop_text(command)
    if project.tool == 'requirements' and not project.manual:
        return _stop_text(command, tool='requirements', files=project.files)
    return _stop_text(command, tool=project.tool)


def _lock_files(project):
    """The files the plugin changes for ``project`` and puts back when the install fails."""
    if project is None or project.manual:
        return ()
    if project.tool == 'poetry':
        return tuple(os.path.join(project.directory, name) for name in ('pyproject.toml', 'poetry.lock'))
    return project.files if project.tool == 'requirements' else ()


class _Restore:
    """The bytes of ``paths`` as they are now, to put back (a file that did not exist is removed)."""

    def __init__(self, paths):
        self.saved = {}
        for path in paths:
            try:
                with open(path, 'rb') as handle:
                    self.saved[path] = handle.read()
            except OSError:
                self.saved[path] = None

    def put_back(self):
        for path, data in self.saved.items():
            try:
                if data is None:
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    with open(path, 'wb') as handle:
                        handle.write(data)
            except OSError as exc:  # a file that cannot be put back is said, never left silently half-edited
                print(f'resolve_plurai: could not put {path} back ({type(exc).__name__}: {exc}).', file=sys.stderr)


def _set_requirement_lines(files):
    """Set the SDK line of every file in ``files`` to the pin; the rest of each file is written as it was read."""
    for path in files:
        with open(path, encoding='utf-8', newline='') as handle:
            lines = handle.read().splitlines(keepends=True)
        text = ''.join(_updated_requirement(line) or line for line in lines)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)


_ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')


def _solver_reason(output):
    """Poetry's own account of why it could not add the SDK, from its output: the solver's lines (`Because …` to the end,
    at most 8), else the last error line, else ``''``."""
    lines = [line.rstrip() for line in _ANSI.sub('', output).splitlines() if line.strip()]
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith('Because')), None)
    if start is not None:
        return '\n'.join(line[:240] for line in lines[start : start + 8])
    return _install_error(output) or ''


def _venv_python_version(venv):
    """``(major, minor)`` from the venv's `pyvenv.cfg` (`version_info` from uv, `version` from venv), or from a conda
    environment's `conda-meta/python-<version>-….json`, or ``None``."""
    config = _venv_config(venv)
    match = re.match(r'(\d+)\.(\d+)', config.get('version_info') or config.get('version') or '')
    if not match:
        records = sorted(glob.glob(os.path.join(venv, 'conda-meta', 'python-3.*.json')))
        match = re.match(r'python-(\d+)\.(\d+)', os.path.basename(records[-1])) if records else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def _conda_env():
    """The active conda environment's prefix, or ``None``. Conda sets `CONDA_PREFIX`, not `VIRTUAL_ENV`. Its `base`
    environment (the one holding `condabin/`) is conda's own and never takes the SDK."""
    prefix = os.environ.get('CONDA_PREFIX') or ''
    if not prefix or not os.path.isdir(os.path.join(prefix, 'conda-meta')) or os.path.isdir(os.path.join(prefix, 'condabin')):
        return None
    return prefix


def _is_env(path):
    """A Python environment the SDK can go into: a venv (`pyvenv.cfg`) or a conda environment (`conda-meta/`)."""
    return os.path.isfile(os.path.join(path, 'pyvenv.cfg')) or os.path.isdir(os.path.join(path, 'conda-meta'))


#: The commands that print the venv a project tool keeps outside the project, each with the file that marks the tool.
#: The repository is untrusted: poetry runs `.pth` files from the project's `.poetry/plugins/` unless `--no-plugins` is
#: given, and pipenv loads the project's `.env` unless told not to. hatch is not asked: `hatch env find` first installs
#: the packages the project's own configuration lists.
_TOOL_VENVS = (
    ('poetry.lock', ('poetry', '--no-plugins', 'env', 'info', '--path')),
    ('Pipfile', ('pipenv', '--venv')),
)


def _tool_venv(root):
    """The venv poetry or pipenv keeps for the project at ``root``, or ``None`` when neither marks it, is installed or names one."""
    for marker, command in _TOOL_VENVS:
        if not os.path.isfile(os.path.join(root, marker)) or not shutil.which(command[0]):
            continue
        try:
            done = subprocess.run(
                list(command),
                cwd=root,
                capture_output=True,
                text=True,
                **utf8_child_text(),
                timeout=30,
                # Both tools are Python programs: PYTHONIOENCODING makes them write the path the way it is read here.
                env={**os.environ, 'PIPENV_DONT_LOAD_ENV': '1', 'PIPENV_VERBOSITY': '-1', 'PYTHONIOENCODING': 'utf-8'},
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        lines = done.stdout.strip().splitlines() if done.returncode == 0 else []
        path = lines[-1].strip() if lines else ''
        if os.path.isabs(path) and os.path.isfile(os.path.join(path, 'pyvenv.cfg')):
            return path
    return None


def _target_venv():
    """The environment the SDK belongs in, or ``None``: the first real one of `_environment_dirs`, else the tool's venv.

    The SDK goes into the project's environment, because the agent's `simulate.py` imports it beside the agent's
    own dependencies."""
    root = _project_root()
    return next((place for place, _ in _environment_dirs(root) if _is_env(place)), None) or _tool_venv(root)


#: Files that mark a Python project, with the tool that keeps its environment and how a shell activates it.
_PROJECT_MARKERS = (
    ('poetry.lock', 'poetry', 'eval "$(poetry env activate)"'),
    ('Pipfile', 'pipenv', 'pipenv shell'),
    ('hatch.toml', 'hatch', 'hatch shell'),
    ('environment.yml', 'conda', 'conda activate <env>'),
    ('environment.yaml', 'conda', 'conda activate <env>'),
    ('pyproject.toml', None, None),
    ('setup.py', None, None),
    ('setup.cfg', None, None),
)


def _python_project(root):
    """``(tool, activation)`` when ``root`` is a Python project (``(None, None)`` for a plain one), else ``None``."""
    found = None
    for marker, tool, activation in _PROJECT_MARKERS:
        if os.path.isfile(os.path.join(root, marker)):
            if tool:
                return tool, activation
            found = found or (None, None)
    try:
        with open(os.path.join(root, 'pyproject.toml'), encoding='utf-8') as pyproject:
            text = pyproject.read()
    except OSError:
        text = ''
    for section, tool, activation in (('[tool.poetry', 'poetry', 'eval "$(poetry env activate)"'), ('[tool.hatch', 'hatch', 'hatch shell')):
        if section in text:
            return tool, activation
    return found or ((None, None) if glob.glob(os.path.join(root, 'requirements*.txt')) else None)


def _use_sibling_modules():
    """Put this script's own directory on `sys.path`, once.

    The wrappers run this file under `python3 -I`, which drops the script directory from the path, so
    the sibling scripts (`client_pin`, `harness_fingerprint`) would not import. The pin check that
    needs them fails closed, so a missing path entry would refuse every run once a pin exists.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def _pin_file_present():
    """Does a `.client_pin.json` sit in the workdir this resolution runs for? Decided without
    client_pin.py, so the answer survives that module failing to import."""
    _use_sibling_modules()
    try:
        import harness_fingerprint  # noqa: PLC0415 - sibling script; the one owner of the workdir cascade
        workdir = harness_fingerprint.find_workdir(None)
    except Exception:  # noqa: BLE001 - a broken cascade must not turn into "no pin"
        env = os.environ.get('WORKDIR') or os.environ.get('PLURAI_DIR')
        workdir = Path(env) if env else None
    return workdir is not None and (workdir / '.client_pin.json').exists()


def _client_pin_refusal(path):
    """The client pin's verdict on this resolved binary — None when no pin exists or it matches.

    Checked HERE, in the one place every ingest and every scored run obtains its runner, so a
    stale client cannot get a path out of this script: the plugin half is the checkout this file
    runs from, the SDK half is read from the resolved binary's own interpreter (see client_pin.py).

    FAILS CLOSED. This script runs under whatever `python3` the skill snippet found, and the
    check module may not import there (an older interpreter, a broken sibling). While a pin file
    EXISTS that is a refusal, not a pass: a pin that stopped biting whenever the check could not
    run would be bypassed on exactly the machines it was written for.
    """
    _use_sibling_modules()
    try:
        import client_pin  # noqa: PLC0415 - sibling script under scripts/
    except Exception as exc:  # noqa: BLE001 - ImportError, and a SyntaxError on an interpreter too old for the module
        if _pin_file_present():
            return (
                f'client_pin: REFUSED — a .client_pin.json exists for this workdir and the check cannot run under '
                f'{sys.executable}: client_pin.py did not import ({type(exc).__name__}: {exc}). Fix what that error names '
                '(a file missing beside resolve_plurai.py, or an interpreter too old for the module), or delete the pin '
                'to declare that any client is acceptable.'
            )
        print(f'resolve_plurai: client_pin is not importable ({exc}) — no pin file found, nothing to check', file=sys.stderr)
        return None
    try:
        return client_pin.enforce(Path(path))
    except Exception as exc:  # noqa: BLE001 - same rule: a check that crashed is a refusal while a pin exists
        if _pin_file_present():
            return f'client_pin: REFUSED — the pin check crashed ({type(exc).__name__}: {exc}); a pin exists, so this is a refusal, not a pass.'
        return None


def resolve(required=_DEFAULT_REQUIRED, *, enforce_pin=True, features=(), ask=False, resolve_args=(), install_pin=False, on_refusal=None):
    """`(path, exit_code)` — the resolved absolute path (exit 0), or None with the code to exit.

    Every diagnostic goes to stderr here; `main` prints the path. Split out so `client_pin.py
    check` can resolve the same runner WITHOUT the pin enforcing itself recursively.

    ``ask`` puts the install question into the refusal of an older runner, for the skill that runs this script and can ask
    it; ``resolve_args`` are the arguments that skill gave `main`, which the question repeats so its yes finds the same
    runner. A caller that imports this function gets the stop text alone. ``install_pin`` carries out the install the
    question offers: the result is then `(None, 0)` once the pin is installed, and the refusal otherwise.

    ``on_refusal(text)`` is called with the part of a release-mismatch stop that a customer reads, after it is printed. The
    command line passes the function that files it for a headless run driver; a script that imports this function decides
    for itself what to say and files nothing. The developer's client-pin stop is never passed on: it is not the backend's floor.
    """
    tiers = _candidates()
    existing = [(path, label) for path, label in tiers if os.access(path, os.X_OK)]
    if not existing:
        target = _missing_target() if ask or install_pin else None
        if target is not None:
            return None, _refuse_missing(target, install_pin, resolve_args)
        checked = ', '.join(f'{label}: {path}' for path, label in tiers) or 'no tiers resolvable'
        print(
            f'resolve_plurai: no `plurai` found ({checked}) — {UPGRADE_HINT}',
            file=sys.stderr,
        )
        return None, 1

    # Tiers 1-2 (activated venv / repo .venv) and PATH are authoritative: a stale
    # install there is an error to fix in place, never a reason to silently run a
    # later tier. The glob tier is a HEURISTIC over sibling repo venvs — among
    # several, take the first that PASSES the capability probe instead of
    # hard-failing on an alphabetically-first stale one (.venv-old must not block
    # a current .venv-plurai).
    path = label = lacks = None
    for cand_path, cand_label in existing:
        cand_abs = os.path.abspath(cand_path)
        # Vetted before it is run, because running it is what a bad candidate wants.
        unverified = _unverified_reason(cand_abs)
        if unverified:
            if cand_label.startswith('repo ') and cand_label != 'repo .venv':
                print(f'resolve_plurai: skipping {cand_abs} ({cand_label}) — {unverified}; trying next candidate', file=sys.stderr)
                continue
            print(
                f'resolve_plurai: REFUSED — {unverified}. A runner is handed the bearer, so it must be one an installer '
                'put there. Reinstall the SDK into that environment.',
                file=sys.stderr,
            )
            return None, EXIT_RUNNER_REFUSED
        missing = _missing(cand_abs, required=required, features=features)
        if not missing:
            path, label = cand_abs, cand_label
            break
        is_glob_tier = cand_label.startswith('repo ') and cand_label != 'repo .venv'
        if not is_glob_tier:
            if (ask or install_pin) and _older_than_pin(cand_abs):
                # An old SDK lacks the verb because it is old: the pin refusal below asks for (or installs) the release
                # that has it. A runner at or past the pin that lacks one is a broken install, which the hint covers.
                path, label, lacks = cand_abs, cand_label, missing
                break
            print(
                f'resolve_plurai: {cand_abs} ({cand_label}) does not provide {", ".join(missing)} '
                f'(SDK too old) — {UPGRADE_HINT}',
                file=sys.stderr,
            )
            return None, 1
        print(f'resolve_plurai: skipping {cand_abs} ({cand_label}) — capability probe failed; trying next candidate', file=sys.stderr)
    if path is None:
        print(f'resolve_plurai: every candidate failed verification or the capability probe — {UPGRADE_HINT}', file=sys.stderr)
        return None, 1

    shadowed = [
        f'{other} ({other_label})'
        for other, other_label in existing
        if os.path.realpath(other) != os.path.realpath(path)
    ]
    note = f" (NOTE: shadows {'; '.join(shadowed)})" if shadowed else ''
    print(f'resolve_plurai: using {label}: {path}{note}', file=sys.stderr)
    if enforce_pin:
        refusal = _client_pin_refusal(path)
        if refusal:
            print(refusal, file=sys.stderr)
            return None, EXIT_RUNNER_REFUSED
    # THE IDENTITY FIRST, because it is the fact: which files this binary is running. Its version
    # decides the pin check when it states one; the `--version` string (dist metadata, which a stale
    # editable install can leave behind) only when it does not.
    identity, version = _runner_version(path)
    if (line := _identity_line(path, identity)) is not None:
        print(line, file=sys.stderr)
    refusal = _pin_stop(path, version, ask or install_pin, resolve_args)
    if refusal is not None:
        return None, _refuse_pin(path, version, refusal, install_pin, on_refusal)
    if lacks:
        print(f'resolve_plurai: {path} ({label}) does not provide {", ".join(lacks)} (SDK too old) — {UPGRADE_HINT}', file=sys.stderr)
        return None, 1
    print(_version_notice(path, version), file=sys.stderr)
    return path, 0


def _refuse_pin(path, version, refusal, install_pin, on_refusal=None):
    """Carry out the refusal ``refusal`` of a runner that is not the pin: the install when it was asked for and is allowed
    (exit 0 once it holds), else the refusal text on stderr and `EXIT_RUNNER_REFUSED`."""
    target = _older_target(path, _version_text(version)) if install_pin else None
    if target is not None:
        return _install_pin(path, target)
    print(refusal.sanctioned if refusal.sanctioned and _sanction_blocker() else refusal.printed, file=sys.stderr)
    if on_refusal is not None:
        on_refusal(refusal.said)
    return EXIT_RUNNER_REFUSED


def _refuse_missing(target, install_pin, resolve_args):
    """The answer to a project environment that holds no runner: the install when it was asked for (exit 0 once the pin
    is there), else the question and its stop text on stderr and `EXIT_RUNNER_REFUSED`. Nothing is installed on a refusal."""
    if install_pin:
        return _install_pin(None, target)
    environment, python = target
    command, project = _venv_install(environment, python=python)
    stop = _stop_for(command, project)
    head = f'resolve_plurai: REFUSED — no `plurai` is installed in {_environment_label(environment)}, where the SDK of this project belongs.'
    question = _missing_question(project)
    print(f'{head}\n{_ask_text(question, stop, resolve_args, sanctioned=_sanction_blocker())}', file=sys.stderr)
    return EXIT_RUNNER_REFUSED


def _missing_question(project):
    """The question for a project environment that holds no SDK: the files the install changes, when the plugin writes any."""
    if project is None or project.manual:
        return MISSING_SDK_QUESTION
    if project.tool == 'uv':
        return LOCK_SDK_QUESTION
    if project.tool == 'poetry':
        return POETRY_SDK_QUESTION
    return requirements_sdk_question(project.files) if project.files else MISSING_SDK_QUESTION


def _record_pin_refusal(text):
    """File ``text`` as the run record's version refusal, which a headless run driver files and an interactive one does not."""
    try:
        import _client_handshake  # noqa: PLC0415 - sibling script
        import _run_driver  # noqa: PLC0415 - hooks/ is on sys.path (top of this file)
    except Exception as exc:  # noqa: BLE001 - the stop itself is already printed; a record that cannot be made is said
        print(f'resolve_plurai: the refusal could not be recorded for the run driver ({type(exc).__name__}: {exc}).', file=sys.stderr)
        return
    _client_handshake.record_refusal(_run_driver.SOURCE_SDK_RESOLVER, text)


def _leave_support_reference(code):
    """Record that no usable `plurai` was found, so an install that failed before the SDK could run still has a
    reference to quote in an issue. Nothing here may change the exit code, so every failure is dropped."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'hooks'))
        import _support_refs

        ref = _support_refs.record('install_failure', f'no usable plurai command was found (exit {code})')
    except Exception:  # noqa: BLE001 - see the docstring
        return
    if ref:
        print(f'resolve_plurai: support reference: {ref} (it is listed by `plurai diagnose` and /plurai-diagnose; quote it when you contact support)', file=sys.stderr)


#: `--venv` exits (`--install-line` shares 4 for a venv path it cannot print): the venv is usable, there is none (stdout is the command that creates one), or the SDK cannot go
#: into one (stderr says why), or a Python project's own environment was not found (stdout is the command that creates
#: `.venv`, stderr how to start the harness from the project's environment instead).
VENV_FOUND, VENV_NONE, VENV_UNUSABLE, VENV_ASK = 0, 1, 4, 5

#: `--install-line` exit: the line is for the user to run (a Poetry or requirements project keeps its own dependencies); stdout
#: is the line, stderr says where it goes, and nothing is to be run.
INSTALL_MANUAL = 6

#: Where a project's own dependencies live, by the tool whose line is printed.
_MANUAL_FILES = {'poetry': 'poetry.lock', 'requirements': 'requirements.txt'}


def _manual_sentence(tool):
    """What the customer reads beside a line that is theirs to run: where the project keeps its dependencies and what to do."""
    return (
        f'This project keeps its dependencies in {_MANUAL_FILES[tool]}, so the plugin will not run it for you: '
        'add the line to the project, install, then run the command again.'
    )


def _install_command(extras, through_lock=True):
    """``(line, None)``: the install line for the pinned release with ``extras``, ``(line, INSTALL_MANUAL)`` when the line
    is the user's to run, or ``(None, VENV_UNUSABLE)`` (stderr says why).

    A project that keeps a lock gets the line that writes it (:func:`_locked_project`), which is asked first. The extractor
    install is never asked (`_install`), so it passes ``through_lock=False`` and installs into the environment."""
    project = _locked_project() if through_lock else None
    if project is not None:
        return _lock_command(project, extras), None if project.tool == 'uv' else INSTALL_MANUAL
    spec = sdk_install_args(sorted(set(extras)))
    venv = _target_venv()
    if venv and _unquotable(_venv_python(venv)):
        print(_unquotable_message(venv), file=sys.stderr)
        return None, VENV_UNUSABLE
    return (_install_into(venv, spec) if venv else f'pip install {spec}'), None


def _print_install_line(args):
    """`--install-line [--extra NAME ...]`: the install line for the pinned release and the extras a skill needs."""
    extras = []
    rest = list(args)
    while rest:
        if rest[0] != '--extra' or len(rest) < 2 or not _EXTRA_NAME.fullmatch(rest[1]):
            print(f'resolve_plurai: --install-line takes only `--extra <name>` (lowercase words joined by hyphens), got {rest}', file=sys.stderr)
            return 64
        extras.append(rest[1])
        rest = rest[2:]
    line, code = _install_command(extras)
    if line is None:
        return code
    print(line)
    if code == INSTALL_MANUAL:
        print(f'resolve_plurai: {_manual_sentence(_locked_project().tool)}', file=sys.stderr)
        return code
    return 0


#: The marker `--install --record <dir>` leaves in that dir while the corpus extractor is not installed; the
#: no-build closing reads its `error` (`simulate_plan._grounding_sentences`).
EXTRACTOR_MISSING = '.extractor_missing'


def _install_error(stderr):
    """The install's error line: the last `ERROR:`/`error:` line of its stderr, else its last other non-empty line.
    pip's `[notice]` lines (its self-version check, printed after a failure) are never the error."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip() and not ln.strip().startswith('[notice]')]
    return next((ln for ln in reversed(lines) if ln.lower().startswith('error:')), lines[-1] if lines else None)


def _install(args):
    """`--install --extra extract [--extra <driver>] --record <artifacts dir>`: run the install line into the project's
    environment (never through a lock, which only a question may change), then record its outcome from the real exit code —
    success removes `<dir>/.extractor_missing`, failure writes `{extras, exit, error}` (:func:`_install_error`). The outcome
    line is printed last; the exit is the install's."""
    extras, record, rest = [], None, list(args)
    while rest:
        if rest[0] == '--extra' and len(rest) > 1 and _EXTRA_NAME.fullmatch(rest[1]):
            extras.append(rest[1])
        elif rest[0] == '--record' and len(rest) > 1 and rest[1] and record is None:
            record = rest[1]
        else:
            print(f'resolve_plurai: --install takes `--extra <name>` and one `--record <dir>`, got {rest}', file=sys.stderr)
            return 64
        rest = rest[2:]
    if 'extract' not in extras or record is None or not os.path.isdir(record):
        print(f'resolve_plurai: --install needs `--extra extract` and `--record` naming an existing directory (got {record!r})', file=sys.stderr)
        return 64
    marker = os.path.join(record, EXTRACTOR_MISSING)
    line, code = _install_command(extras, through_lock=False)
    stderr = ''
    if line is not None:
        try:
            proc = subprocess.run(line, shell=True, capture_output=True, text=True, check=False, **utf8_child_text())  # noqa: S602 - the line this resolver composes
            code, stderr = proc.returncode, proc.stderr or ''
            sys.stdout.write(proc.stdout or '')
            sys.stderr.write(stderr)
        except OSError as exc:
            code, stderr = 127, f'{type(exc).__name__}: {exc}'
    sys.stdout.flush()
    sys.stderr.flush()
    if code == 0:
        if os.path.exists(marker):
            os.remove(marker)
        print('extractor: installed')
        return 0
    error = _install_error(stderr) or f'the install exited {code}'
    with open(marker, 'w', encoding='utf-8') as fh:
        json.dump({'extras': sorted(set(extras)), 'exit': code, 'error': error}, fh, ensure_ascii=False)
        fh.write('\n')
    print(f'extractor: NOT installed — {error}')
    return code


def _print_venv():
    """`--venv`: the project venv the SDK installs into, or the command that creates `.venv` when there is none."""
    floor = '.'.join(map(str, SDK_MIN_PYTHON))
    venv = _target_venv()
    if venv and _unquotable(venv):
        print(_unquotable_message(venv), file=sys.stderr)
        return VENV_UNUSABLE
    if venv:
        version = _venv_python_version(venv)
        if version is not None and version < SDK_MIN_PYTHON:
            print(
                f'resolve_plurai: {venv} runs Python {version[0]}.{version[1]}; the SDK needs Python {floor} or newer. '
                f'Recreate it with Python {floor} or newer (the agent\'s dependencies go back in with it), or activate one that is.',
                file=sys.stderr,
            )
            return VENV_UNUSABLE
        print(venv)
        return VENV_FOUND
    root = _project_root()
    target = os.path.join(root, '.venv')
    unsafe = _unquotable(target, sys.executable)
    if unsafe:
        print(_unquotable_message(unsafe), file=sys.stderr)
        return VENV_UNUSABLE
    uv = _uv()
    if uv:
        create = f'{uv} venv --python {floor} "{target}"'
    elif sys.version_info[:2] >= SDK_MIN_PYTHON:
        create = f'"{sys.executable}" -m venv "{target}"'
    else:
        print(
            f'resolve_plurai: no project venv, and no Python {floor} or newer to create one ({sys.executable} is '
            f'{sys.version_info[0]}.{sys.version_info[1]}). Install uv or Python {floor}, then run the setup again.',
            file=sys.stderr,
        )
        return VENV_UNUSABLE
    print(create)
    project = _python_project(root)
    if project is None:
        return VENV_NONE
    tool, activation = project
    print(
        f'resolve_plurai: {root} is a Python project{f" managed by {tool}" if tool else ""}, and none of its environments '
        'was found: no $VIRTUAL_ENV, no active conda environment, no venv in the project folder, none named by poetry or '
        "pipenv. A new .venv would not hold the agent's dependencies, which simulate.py imports. Either start the harness "
        f'from a shell where the agent\'s environment is active ({activation or "activate it"}) and run the setup again, '
        "or create .venv with the command on stdout and install the agent's dependencies into it.",
        file=sys.stderr,
    )
    return VENV_ASK


def _require_args(argv):
    """``((required, features), None)`` for `--require <subcommand> ...` / `--require=a,b`, or ``(None, exit_code)`` with the
    reason on stderr.

    A misspelled flag (`--required`), the `=` form with no names and a bare `--require` are refused rather than read as
    the default probe, because the default probe lets an SDK that lacks the subcommand a skill needs through, and the
    failure then comes late. A grouped verb is one quoted argument (`--require "job wait"`); an upload verb
    (UPLOAD_VERBS) also requires the `parts-only-upload` feature of `plurai capabilities`.
    """
    if not argv:
        return (_DEFAULT_REQUIRED, ()), None
    if argv[0].startswith('--require='):
        names = tuple(n for n in argv[0].split('=', 1)[1].split(',') if n)
    elif argv[0] == '--require':
        names = tuple(argv[1:])
    else:
        print(f'resolve_plurai: unrecognized arguments {argv} (expected: --require <subcommand> [...])', file=sys.stderr)
        return None, 64
    if not names:
        print('resolve_plurai: --require needs at least one subcommand name', file=sys.stderr)
        return None, 64
    return ((*_DEFAULT_REQUIRED, *names), (UPLOAD_FEATURE,) if UPLOAD_VERBS.intersection(names) else ()), None


def main():
    # `--require <subcommand> ...` narrows the capability probe; skills whose flow depends
    # on a newer subcommand (score_run by-reference needs `ingest-run`) pass it so a
    # predating install fails HERE with the upgrade hint, not later with a bare argparse
    # "invalid choice".
    argv = sys.argv[1:]
    if argv[:1] == ['--install-line']:
        return _print_install_line(argv[1:])
    if argv[:1] == ['--install']:
        return _install(argv[1:])
    if argv == ['--venv']:
        return _print_venv()
    if argv[:1] in (['--help'], ['-h']):
        print(
            'usage: resolve_plurai.py [--ask] [--require <subcommand> ...] | --install-line [--extra <name> ...]\n'
            '       | --install --extra extract [--extra <name> ...] --record <dir> | --install-pin [--require <subcommand> ...] | --venv\n'
            'Prints the path of the `plurai` CLI to run (diagnostics on stderr).\n'
            f'Install the SDK: {INSTALL_LINE}'
        )
        return 0
    # `--ask` is the skills' flag: only a caller that scripts the question for the user passes it. The scripts that run the
    # resolver mid-flow and relay its stderr pass none, and get the stop lines alone.
    ask = '--ask' in argv
    argv = [arg for arg in argv if arg != '--ask']
    install_pin = argv[:1] == ['--install-pin']
    rest = argv[1:] if install_pin else argv
    parsed, code = _require_args(rest)
    if parsed is None:
        return code
    required, features = parsed
    path, code = resolve(required, features=features, ask=ask, resolve_args=tuple(rest), install_pin=install_pin, on_refusal=_record_pin_refusal)
    if install_pin:
        return code  # the install prints its own line; a runner that already holds the pin has nothing to install
    if path is None:
        _leave_support_reference(code)
        return code
    print(path)
    return 0


if __name__ == '__main__':
    _use_sibling_modules()  # the wrappers run this under `python3 -I`
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
