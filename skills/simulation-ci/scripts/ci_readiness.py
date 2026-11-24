#!/usr/bin/env python3
"""The readiness check of /simulation-ci: what keeps an agent from a first CI run, each finding with one concrete fix.

    ci_readiness.py check [--repo R] [--agent REL_SIM ...] [--catalog catalog.json] [--smoke a,b] [--trials N]
                          [--plurai BIN] [--json]
    ci_readiness.py agent-env [--repo R] [--agent REL_SIM ...]

A finding is ``fix`` (CI cannot run until it is done), ``advise`` (a recommendation or something this check could not decide) or
``info`` (a fact the user hears: the run size as a thread count, the ingestion posture). A ready agent has no ``fix`` and no
``advise``; it still prints its ``info`` lines. Exit 0 when nothing needs a fix, 1 otherwise. ``READY`` is printed only for an agent
that ``plurai ci validate`` accepted: with no runner given, or one that did not finish, the agent is ``NOT READY``.

Secrets: this script reads variable NAMES only. It never reads a value of the environment, of ``.env`` or of any credential, and
it prints no figure about credit or cost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_common as common
import ci_smoke
from ci_common import Agent
from ci_render import (
    MAIN_BASELINE,
    RESERVED_FIX,
    reserved_agent_env_reason,
    reserved_names,
    reserved_why,
)

PACKAGE = 'plurai-simulation-sdk'
PYTHON_FLOOR = (3, 12)
_LOCKS = ('uv.lock', 'poetry.lock')
_REQUIREMENT = re.compile(r'^\s*plurai[-_.]simulation[-_.]sdk\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)', re.IGNORECASE | re.MULTILINE)
_DIST_INFO = re.compile(r'plurai_simulation_sdk-(.+)\.dist-info')
#: Names a process needs to run: they reach the agent without a declaration, so they are never proposed for ``agent.env``.
_BASE_NAMES = frozenset({'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TERM', 'LANG', 'LANGUAGE', 'TZ', 'TMPDIR', 'TEMP', 'TMP', 'VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME', 'PYTHONUTF8', 'PYTHONUNBUFFERED'})
_CLOUD = {'bedrock': 'aws', 'azure': 'azure', 'google': 'gcp', 'gemini': 'gcp'}
#: The names an Anthropic-on-the-Claude-CLI agent can sign in with besides a local login: an API key or a cloud route.
_CLI_NON_LOGIN_NAMES = frozenset({'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'AWS_ACCESS_KEY_ID', 'AWS_PROFILE'})


@dataclass(frozen=True)
class Finding:
    agent: str
    severity: str
    code: str
    message: str
    fix: str = ''

    def line(self) -> str:
        label = {'fix': 'FIX', 'advise': 'ADVISE', 'info': 'INFO'}[self.severity]
        return f'{label} [{self.agent}] {self.code}: {self.message}' + (f' Fix: {self.fix}' if self.fix else '')


def _fix(agent: Agent, code: str, message: str, fix: str) -> Finding:
    return Finding(agent.name, 'fix', code, message, fix)


def _advise(agent: Agent, code: str, message: str, fix: str = '') -> Finding:
    return Finding(agent.name, 'advise', code, message, fix)


def _info(agent: Agent, code: str, message: str, fix: str = '') -> Finding:
    return Finding(agent.name, 'info', code, message, fix)


# --------------------------------------------------------------------------- python and lockfiles

def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r'\d+', text)[:3])


def _pad(version: tuple[int, ...], size: int = 3) -> tuple[int, ...]:
    return (*version, *([0] * (size - len(version))))[:size]


def specifier_allows(spec: str, candidate: tuple[int, ...]) -> bool:
    """Whether the ``requires-python`` specifier ``spec`` admits ``candidate`` (major, minor). Unreadable clauses admit."""
    for clause in (c.strip() for c in spec.split(',') if c.strip()):
        match = re.fullmatch(r'(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9.]*?)(\.\*)?', clause)
        if not match:
            continue
        op, raw, star = match.group(1), _version(match.group(2)), bool(match.group(3))
        size = max(len(raw), 3)
        have, want = _pad(candidate, size), _pad(raw, size)
        if op == '>=' and not have >= want:
            return False
        if op == '>' and not have > want:
            return False
        if op == '<=' and not have <= want:
            return False
        if op == '<' and not have < want:
            return False
        if op in ('==', '!=') and star:
            hit = candidate[: len(raw)] == raw[: len(candidate)] if len(raw) <= len(candidate) else _pad(candidate, len(raw)) == raw
            if hit != (op == '=='):
                return False
        elif op == '==' and have != want or op == '!=' and have == want:
            return False
        elif op == '~=':
            prefix = raw[:-1] if len(raw) > 1 else raw
            if not have >= want or candidate[: len(prefix)] != prefix[: len(candidate)]:
                return False
    return True


def python_findings(agent: Agent, repo: Path) -> list[Finding]:
    found: list[Finding] = []
    for directory in _roots(agent, repo):
        cfg = directory / '.venv' / 'pyvenv.cfg'
        if cfg.is_file():
            match = re.search(r'^version(?:_info)?\s*=\s*([\d.]+)', cfg.read_text(encoding='utf-8', errors='replace'), re.MULTILINE)
            if match and _version(match.group(1))[:2] < PYTHON_FLOOR:
                found.append(_fix(agent, 'python-version', f'the project environment {_rel(cfg.parent, repo)} runs Python {match.group(1)}, and the Plurai SDK needs 3.12 or newer.',
                                  'recreate the environment on Python 3.12 or newer (for uv: `uv venv --python 3.12`), and set the workflow\'s Python to the same.'))
        pin = directory / '.python-version'
        if pin.is_file():
            text = pin.read_text(encoding='utf-8', errors='replace').strip().splitlines()[0:1]
            if text and re.match(r'\d', text[0]) and _version(text[0])[:2] < PYTHON_FLOOR:
                found.append(_fix(agent, 'python-version', f'{_rel(pin, repo)} pins Python {text[0]}, and the Plurai SDK needs 3.12 or newer.', 'raise it to 3.12 or newer.'))
        pyproject = directory / 'pyproject.toml'
        if pyproject.is_file():
            try:
                spec = common.requires_python(pyproject.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError):
                spec = None
            if isinstance(spec, str) and not any(specifier_allows(spec, (3, minor)) for minor in (12, 13, 14)):
                found.append(_fix(agent, 'python-version', f'{_rel(pyproject, repo)} says `requires-python = "{spec}"`, which excludes Python 3.12 and newer.',
                                  'allow Python 3.12 or newer (for example `>=3.12`).'))
        if directory == repo:
            break
    return found


def _roots(agent: Agent, repo: Path) -> list[Path]:
    roots: list[Path] = []
    for directory in (agent.sim_dir.parent, *agent.sim_dir.parent.parents):
        roots.append(directory)
        if directory == repo:
            break
    return roots


def _rel(path: Path, repo: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _sdk_versions(directory: Path) -> Iterable[tuple[Path, str]]:
    for name in _LOCKS:
        path = directory / name
        if path.is_file():
            try:
                packages = common.lock_packages(path.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError):
                continue
            for package in packages:
                if re.sub(r'[-_.]+', '-', str(package.get('name', ''))).lower() == PACKAGE and 'version' in package:
                    yield path, str(package['version'])
    for path in sorted(directory.glob('requirements*.txt')):
        for match in _REQUIREMENT.finditer(path.read_text(encoding='utf-8', errors='replace')):
            yield path, match.group(1)
    venv = directory / '.venv'
    for site in (*(venv / 'lib').glob('python*/site-packages'), venv / 'Lib' / 'site-packages'):
        for dist in site.glob('plurai_simulation_sdk-*.dist-info'):
            match = _DIST_INFO.fullmatch(dist.name)
            if match:
                yield venv, match.group(1)


def _minor(version: str) -> tuple[int, int] | None:
    match = re.match(r'(\d+)\.(\d+)', version)
    return (int(match[1]), int(match[2])) if match else None


def sdk_pin_findings(agent: Agent, repo: Path, pin: str) -> list[Finding]:
    """A finding for each SDK the project lists or has installed whose ``major.minor`` is not the pin's.

    The lock decides which SDK runs, and a release of the pin's minor is in range for the plugin and the CI action alike. Minors
    are cut together, so the minor is the action's rule too, and it needs no read of the action's release."""
    found: list[Finding] = []
    want = _minor(pin)
    line = '.'.join(pin.split('.')[:2])
    for directory in _roots(agent, repo):
        for path, version in _sdk_versions(directory):
            if _minor(version) != want:
                where = f'the project environment {_rel(path, repo)}' if path.name == '.venv' else _rel(path, repo)
                found.append(_fix(agent, 'sdk-pin', f'{where} holds {PACKAGE} {version}, and this plugin release works with the {line} releases from {pin}. The CI action takes the same line, so CI would refuse it.',
                                  f'move it to a {line} release (for uv: `uv add --dev "{_sdk_requirement()}"`), or update the plugin when the project is on a newer line.'))
        if directory == repo:
            break
    return found


def lockfile_findings(agent: Agent, repo: Path) -> list[Finding]:
    directory = agent.sim_dir.parent
    for name in ('uv.lock', 'poetry.lock', 'Pipfile.lock', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock'):
        if (directory / name).is_file() or (repo / name).is_file():
            return []
    for path in sorted(directory.glob('requirements*.txt')):
        lines = [x.strip() for x in path.read_text(encoding='utf-8', errors='replace').splitlines() if x.strip() and not x.lstrip().startswith(('#', '-'))]
        if lines and all('==' in line or ' @ ' in line for line in lines):
            return []
        return [_fix(agent, 'lockfile', f'{_rel(path, repo)} lists dependencies without exact versions, so CI installs whatever is newest that day.',
                     'pin every line with `==` (for example `pip freeze > requirements.txt`), or lock with uv or poetry.')]
    return [_fix(agent, 'lockfile', f'no lockfile next to {agent.rel_agent}/ (uv.lock, poetry.lock, a pinned requirements.txt or a package lock), so CI cannot install the agent\'s dependencies the way you do.',
                 'create one (`uv lock`, `poetry lock` or `pip freeze > requirements.txt`) and commit it.')]


# --------------------------------------------------------------------------- the committed suite

#: The suite ids CI reads from the env folder ci.yaml names: the kind, the marker, the manifest key and the skill that writes them.
_SUITE_IDS = (
    ('scenarios', '.scenario_set_id', 'scenario_set_id', 'run /simulation-generate-suite'),
    ('evals', '.eval_set_id', 'eval_set_id', 'ask me to generate the evals'),
)


def _read_id() -> Any:
    """The id reader: its scan decides which copies of a suite id exist and in which order CI reads them."""
    sys.path.insert(0, str(common.SCRIPTS))
    import read_id  # noqa: PLC0415 - found through the path entry above

    return read_id


def _shown(where: str, repo: Path) -> str:
    """A copy's ``where`` from the reader, with the repository prefix taken off."""
    return where.replace(f'{repo}{os.sep}', '')


def suite_files_findings(agent: Agent, repo: Path, ci_env: str | None, ci_yaml: bool = False) -> list[Finding]:
    """The findings about the committed suite: its files, its env, and the copies of each id as ``read_id`` reads them.

    The env folder is the one ``ci.yaml`` names, ``default`` when ``ci.yaml`` has no ``env`` key (the reader's rule), and, while there
    is no ``ci.yaml`` yet, the one a render will write into it.
    """
    read_id = _read_id()
    found: list[Finding] = []
    sim = agent.sim_dir
    if not agent.has_simulate:
        found.append(_fix(agent, 'no-simulate', f'{agent.rel_sim}/ has no simulate.py.', 'run /simulation-scan-agent in this agent first; it sets the simulation up.'))
    if not common.config_paths(sim):
        found.append(_fix(agent, 'no-config', f'{agent.rel_sim}/ has no plurai.config.yaml.', 'run /simulation-scan-agent in this agent first; it sets the simulation up.'))
    aliases = common.env_aliases(sim)
    wanted = ci_env or (common.DEFAULT_ENV_ALIAS if ci_yaml else common.pick_env_alias(sim))
    folder = wanted if wanted in aliases else None
    workdir = sim / 'envs' / folder if folder else sim
    for kind, marker, key, skill in _SUITE_IDS:
        copies, conflicted = read_id.scan(workdir, kind)
        for where in conflicted:
            found.append(_fix(agent, 'id-conflict', f'{_shown(where, repo)} holds an unresolved merge conflict, so it names no id.',
                              f'{read_id.CONFLICT_ADVICE}, then commit the file.'))
        if not copies:
            if not conflicted:
                where = f' and envs/{folder}/ holds no copy of it' if folder else ''
                found.append(_fix(agent, 'no-id-file', f'{agent.rel_sim}/{marker} is missing{where}.', f'{skill} so the suite exists, then commit the file.'))
            continue
        for copy in copies:
            if not copy.where.startswith('`') and common.ignored(repo, Path(copy.where)):
                found.append(_fix(agent, 'id-file-ignored', f'{_shown(copy.where, repo)} is ignored by git, so a fresh clone cannot find the suite.', 'remove it from .gitignore and commit it.'))
        values = list(dict.fromkeys(copy.value for copy in copies))
        if len(values) > 1:
            held = ', '.join(f'{_shown(copy.where, repo)} holds {copy.value}' for copy in copies)
            command = f'"$PLURAI_PY" "${{CLAUDE_PLUGIN_ROOT}}/scripts/write_id_file.py" set --kind {kind} <id> --workdir {shlex.quote(str(workdir))}'
            found.append(_fix(agent, 'id-copies-disagree', f'the copies of `{key}` disagree: {held}. CI reads {copies[0].value} and `plurai ci validate` refuses the mix.',
                              f'ask the user which id the suite should test ({" or ".join(values)}), then write it into every copy: `{command}`, '
                              'and commit the files it names.'))
    if folder is None:
        if (sim / '.env_id').is_file() and not aliases:
            found.append(_fix(agent, 'bare-env-layout', f'{agent.rel_sim}/ keeps its env in the bare layout (.env_id), which is gitignored, so a fresh clone cannot find its mock.',
                              'ask me to save the env under the alias `default`; that moves it to envs/default/plurai.env.yaml. Then commit that folder.'))
        else:
            found.append(_fix(agent, 'no-env', f'{agent.rel_sim}/envs/{wanted or "<alias>"}/plurai.env.yaml does not exist, so CI has no mock environment to deploy.',
                              'ask me to save the env under an alias (for example `default`), then commit the folder.'))
    else:
        manifest = sim / 'envs' / wanted / 'plurai.env.yaml'
        if common.ignored(repo, manifest):
            found.append(_fix(agent, 'env-ignored', f'{_rel(manifest, repo)} is ignored by git.', 'remove it from .gitignore and commit it.'))
    return found


def trials_findings(agent: Agent, ci_doc: Mapping[str, Any]) -> list[Finding]:
    """``trials`` below the minimum can never end green: a cell with fewer valid trials is not measured."""
    trials = ci_doc.get('trials')
    if isinstance(trials, bool) or not isinstance(trials, int) or trials >= common.MIN_TRIALS:
        return []
    return [_fix(agent, 'trials-too-low', f'ci.yaml sets trials: {trials}, and a cell needs at least {common.MIN_TRIALS} valid trials to be measured, so the check would always end "not measured".',
                 f'set it with `ci_edit.py trials --agent {agent.rel_sim} {common.MIN_TRIALS}` (or more).')]


def id_files_findings(agent: Agent) -> list[Finding]:
    """A warning, never a block, when an id file differs from ``HEAD``: CI runs the committed ids, so the change has to be committed."""
    sys.path.insert(0, str(common.SCRIPTS))
    import id_changes

    changed = id_changes.changed(agent.sim_dir)
    if changed is None:
        return [_advise(agent, 'id-files-unchecked', f'whether an id file of {agent.rel_sim} differs from HEAD could not be checked: git gave no HEAD to compare with (not a work tree, or no commit yet).',
                        'commit the id files, and run this check again from a git work tree: CI runs the committed ids.')]
    moved = changed
    if not moved:
        return []
    held = '; '.join(f'{change.where}: {change.old or id_changes.NONE} -> {change.new or id_changes.NONE}' for change in moved)
    return [_advise(agent, 'id-files-uncommitted', f'{len(moved)} id file(s) differ from HEAD ({held}); a push without them runs the suite HEAD names, not this one.',
                    'commit the id files together with the change that moved them, or restore them with `git checkout` when the change is not wanted. '
                    + MAIN_BASELINE)]


_OVER_DEFAULT = (f'The proposal keeps a smoke run to {common.SMOKE_CAP_THREADS} threads, a provisional default and not a limit: the run goes ahead and costs more threads.')


def smoke_findings(agent: Agent, smoke: Sequence[str], catalog: Mapping[str, int] | None, trials: int) -> list[Finding]:
    found: list[Finding] = []
    if catalog is not None:
        unknown = ci_smoke.unknown_names(smoke, catalog)
        if unknown:
            found.append(_fix(agent, 'smoke-unknown', f'the smoke list names {", ".join(unknown)}, which the committed scenario set does not have.',
                              'pick names from the committed set (the proposal lists them), or commit the set that has them.'))
        regressions = [n for n in catalog if n.startswith(common.REGRESSION_PREFIX) and n not in smoke]
        scenarios = len(set(smoke)) + len(regressions)
        threads = scenarios * trials
        note = (f'a pull request runs {len(set(smoke))} smoke and {len(regressions)} regression scenarios x {trials} trials = {threads} threads '
                f'(a full run: {len(catalog)} scenarios x {trials} trials = {len(catalog) * trials} threads).')
        if threads > common.SMOKE_CAP_THREADS:
            found.append(_advise(agent, 'smoke-over-default', f'{note} {_OVER_DEFAULT}',
                                 'keep it if the run is meant to be that big; otherwise shorten the smoke list or lower trials (the proposal fits).'))
        else:
            found.append(_info(agent, 'run-size', note))
    else:
        if common.smoke_over_cap(smoke, (), trials):
            found.append(_advise(agent, 'smoke-over-default', f'the smoke list names {len(set(smoke))} scenarios x {trials} trials = {len(set(smoke)) * trials} threads, and every regression_* scenario adds to it. {_OVER_DEFAULT}',
                                 f'keep it if the run is meant to be that big; otherwise shorten the list or lower trials: `ci_edit.py smoke --agent {agent.rel_sim} a,b` (the proposal fits).'))
        else:
            found.append(_info(agent, 'run-size', f'a pull request runs {len(set(smoke))} smoke scenarios plus every regression_* scenario, x {trials} trials, one thread each.'))
        if smoke:
            found.append(_info(agent, 'smoke-unchecked', 'the smoke names were not compared with the committed set: no catalog was given.',
                                 'the online check (`plurai ci validate --online`) or the first CI run refuses a name the set lacks.'))
    return found


# --------------------------------------------------------------------------- the agent's declarations

def posture_findings(agent: Agent, config: Mapping[str, Any]) -> list[Finding]:
    """``posture``: what an ingested transcript can hold. A CI run reads no ``anonymize``, so a key the config leaves
    undeclared reads as ``false`` (raw) and raises no question."""
    if config.get('anonymize') is True:
        return [_info(agent, 'posture', 'ingestion posture: anonymized. A transcript holds scrubbed text: names, emails and ids from your data are replaced before they leave this machine.')]
    return [_info(agent, 'posture', 'ingestion posture: raw. A transcript can hold the real names, emails and order details of your data, and the agent\'s replies to them. '
                  'With `include_content: auto`, transcripts are uploaded as a workflow artifact for private repositories only.')]


def env_names(path: Path | None) -> list[str]:
    """The variable NAMES an env file sets. Values are never read into a variable."""
    if path is None or not path.is_file():
        return []
    names = re.findall(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', path.read_text(encoding='utf-8', errors='replace'), re.MULTILINE)
    return list(dict.fromkeys(names))


def _manifest(agent: Agent) -> dict[str, Any] | None:
    sys.path.insert(0, str(common.SCRIPTS))
    sys.path.insert(0, str(common.HOOKS))
    import bench_manifest as bm

    doc = bm._read_json(bm.manifest_path(agent.sim_dir))
    return doc if isinstance(doc, dict) and isinstance(doc.get('harness'), dict) else None


def propose_env(agent: Agent, config: Mapping[str, Any]) -> tuple[list[tuple[str, str]], str | None]:
    """``(proposals, oidc)``: names the agent reads that ``agent.env`` lacks, each with why, and the cloud provider whose
    OIDC step replaces a stored key (``aws``, ``azure``, ``gcp``) or ``None``. Names only."""
    declared = set(common.agent_env(config))
    proposals: dict[str, str] = {}
    oidc: str | None = None
    doc = _manifest(agent)
    env_file: Path | None = None
    if doc is not None:
        sys.path.insert(0, str(common.SCRIPTS))
        import bench_manifest as bm

        provider, names = bm.resolved_provider(doc, agent.sim_dir)
        env_file = bm.env_file_path(doc, agent.sim_dir)
        oidc = _CLOUD.get(provider)
        in_file = set(env_names(env_file))
        if names and oidc is None:
            chosen = next((n for n in names if n in in_file), names[0])
            proposals.setdefault(chosen, f'the model-provider key of the bench manifest ({provider})')
    else:
        env_file = next((p for p in (agent.sim_dir.parent / '.env', agent.sim_dir / '.env') if p.is_file()), None)
    for name in env_names(env_file):
        if name not in _BASE_NAMES and reserved_agent_env_reason(name) is None:
            proposals.setdefault(name, 'set in the agent\'s .env')
    tools = config.get('tools')
    if isinstance(tools, Mapping):
        for tool, body in tools.items():
            if isinstance(body, Mapping) and body.get('mode') in ('real', 'observe'):
                for name in body.get('credentials') or ():
                    if isinstance(name, str) and common.VARIABLE_NAME.fullmatch(name):
                        proposals.setdefault(name, f'a credential of the {body["mode"]} tool {tool}')
    return [(name, why) for name, why in proposals.items() if name not in declared], oidc


def agent_env_findings(agent: Agent, config: Mapping[str, Any]) -> list[Finding]:
    found: list[Finding] = []
    for name in common.rejected_env(config):
        found.append(_fix(agent, 'agent-env-invalid', f'agent.env lists {name[:60]!r}, which is not an environment variable name, so it is not passed to the agent.',
                          'fix or remove it under agent.env in plurai.config.yaml.'))
    for name in reserved_names(common.agent_env(config)):
        found.append(_fix(agent, 'agent-env-reserved', reserved_why(name), RESERVED_FIX))
    proposals, oidc = propose_env(agent, config)
    if proposals:
        listed = '; '.join(f'{name} ({why})' for name, why in proposals)
        found.append(_advise(agent, 'agent-env-missing', f'the agent probably reads names that agent.env does not declare, so the CI job will not pass them: {listed}.',
                             'confirm which to add; the edit script adds the names you keep under agent.env, then the workflow gets an env line for each.'))
    if oidc:
        found.append(_advise(agent, 'cloud-provider', f'the agent signs in to a cloud provider ({oidc}); a stored key in a secret is the weaker route.',
                             f'render the cloud-credentials step with `--oidc {oidc}` and give the role the access it needs.'))
    doc = _manifest(agent)
    if doc is not None:
        sys.path.insert(0, str(common.SCRIPTS))
        import bench_manifest as bm

        if bm._runs_on_claude_cli(doc) and not (set(common.agent_env(config)) & _CLI_NON_LOGIN_NAMES):
            found.append(_fix(agent, 'claude-cli-login', 'the agent signs in through a local `claude` login. A login lives on your machine and cannot run in CI.',
                              'give it an API key (declare ANTHROPIC_API_KEY under agent.env and set that secret) or Bedrock, and run the agent with it.'))
    return found


# --------------------------------------------------------------------------- backend and validator

def validator_findings(agent: Agent, plurai_bin: str | None, environ: Mapping[str, str]) -> list[Finding]:
    found: list[Finding] = []
    if not plurai_bin:
        return [_fix(agent, 'validator-not-run', '`plurai ci validate` did not run: no plurai runner was given, so nothing here has been checked against the rules CI applies.',
                     'resolve the runner (Step 0 of /simulation-ci) and run this check again with `--plurai "$PLURAI_BIN"`; a stop of the resolver is a stop of this check.')]
    status, text = common.validate(agent.sim_dir, plurai_bin)
    if status != 0:
        found.append(_fix(agent, 'validate-refused', f'`plurai ci validate` refused this agent (exit {status}): {text}', 'make the change the message names, then run the check again.'))
        return found
    if environ.get('PLURAI_TOKEN'):
        status, text = common.validate(agent.sim_dir, plurai_bin, online=True)
        if status == 0:
            found.append(_info(agent, 'online', 'every committed id exists on the backend and the ids link within their lineage.'))
        else:
            found.append(_fix(agent, 'online-refused', f'the backend does not agree with the committed ids (`plurai ci validate --online` exit {status}): {text}',
                              're-run the skill that wrote the id (/simulation-generate-suite) so the files name ids that exist, and commit them.'))
    else:
        found.append(_info(agent, 'online-unchecked', 'whether the committed ids exist on the backend was not checked here: it needs the CI token, which this skill never holds.',
                             'after `plurai ci token`, run `plurai ci validate --online` with PLURAI_TOKEN set, or let the first CI run check it ("not measured: suite" if an id is missing).'))
    return found


# --------------------------------------------------------------------------- the check

def check_agent(
    agent: Agent,
    *,
    smoke: Sequence[str] | None = None,
    catalog: Mapping[str, int] | None = None,
    trials: int | None = None,
    plurai_bin: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[Finding]:
    """All the findings for ``agent``, in the order a reader fixes them.

    ``plurai ci validate`` runs last and only when no other finding needs a fix: its refusals would repeat them, and a ``READY``
    verdict then means the validator accepted the agent.
    """
    repo = agent.repo
    config = common.read_config(agent.sim_dir)
    ci_path = agent.sim_dir / common.CI_YAML
    ci_doc = common.read_yaml(ci_path) if ci_path.is_file() else None
    ci_doc = ci_doc if isinstance(ci_doc, Mapping) else {}
    ci_smoke_list = [str(n) for n in ci_doc.get('smoke') or []]
    effective_trials = trials or (ci_doc.get('trials') if isinstance(ci_doc.get('trials'), int) else common.DEFAULT_TRIALS)
    found: list[Finding] = []
    if not agent.safe:
        return [_fix(agent, 'unsafe-directory', f'the directory {agent.rel_sim!r} has characters that are not allowed in a workflow file or a command line.',
                     'rename it to letters, digits, ".", "_" and "-".')]
    found += suite_files_findings(agent, repo, ci_doc.get('env') if isinstance(ci_doc.get('env'), str) else None, ci_path.is_file())
    found += lockfile_findings(agent, repo)
    found += sdk_pin_findings(agent, repo, _pinned_sdk())
    found += python_findings(agent, repo)
    found += trials_findings(agent, ci_doc)
    found += id_files_findings(agent)
    found += smoke_findings(agent, list(smoke) if smoke is not None else ci_smoke_list, catalog, int(effective_trials))
    found += posture_findings(agent, config)
    found += agent_env_findings(agent, config)
    if not any(f.severity == 'fix' for f in found):
        found += validator_findings(agent, plurai_bin, os.environ if environ is None else environ)
    return found


def _sdk_requirement() -> str:
    """The requirement a project's dependencies list for the SDK: the pin, with the Python marker."""
    sys.path.insert(0, str(common.SCRIPTS))
    import resolve_plurai

    return resolve_plurai.sdk_lock_requirement(quote="'")


def _pinned_sdk() -> str:
    """The SDK build this plugin release was tested with; its minor is the line a project's SDK has to be on."""
    sys.path.insert(0, str(common.SCRIPTS))
    import resolve_plurai

    return resolve_plurai.PINNED_SDK_VERSION


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0] if __doc__ else None)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'agent-env'):
        p = sub.add_parser(name)
        p.add_argument('--repo', default='.')
        p.add_argument('--agent', action='append', default=[], metavar='REL_SIM')
    check = sub.choices['check']
    check.add_argument('--catalog', type=Path, default=None)
    check.add_argument('--smoke', default=None, help='the proposed smoke list, a,b (default: the one in ci.yaml)')
    check.add_argument('--trials', type=int, default=None)
    check.add_argument('--plurai', default=None)
    check.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    repo = common.repo_root(Path(args.repo))
    agents = [a for a in common.discover_agents(repo) if not args.agent or a.rel_sim in args.agent]
    if not agents:
        print('simulation-ci: no plurai_sim/ directory found; set up the simulation first with /simulation-scan-agent', file=sys.stderr)
        return 2
    try:
        if args.command == 'agent-env':
            for agent in agents:
                proposals, oidc = propose_env(agent, common.read_config(agent.sim_dir))
                print(json.dumps({'agent': agent.name, 'propose': [{'name': n, 'why': w} for n, w in proposals], 'oidc': oidc}))
            return 0
        catalog = ci_smoke.read_catalog(args.catalog) if args.catalog else None
        smoke = [n for n in args.smoke.split(',') if n] if args.smoke is not None else None
        findings: list[Finding] = []
        for agent in agents:
            findings += check_agent(agent, smoke=smoke, catalog=catalog, trials=args.trials, plurai_bin=args.plurai)
    except ValueError as error:
        print(f'simulation-ci: {error}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(f) for f in findings]))
    else:
        for finding in findings:
            print(finding.line())
        blocked = {f.agent for f in findings if f.severity == 'fix'}
        for agent in agents:
            print(f'{"NOT READY" if agent.name in blocked else "READY"} {agent.name}')
    return 1 if any(f.severity == 'fix' for f in findings) else 0


if __name__ == '__main__':
    sys.path.insert(0, str(common.SCRIPTS))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
