#!/usr/bin/env python3
"""Render, check and print what /simulation-ci writes: the workflow file of each agent, ``ci.yaml`` and the lines the user
runs. The model never writes this YAML; this script does, from the templates in ``../templates``.

    ci_render.py discover [--repo R]
    ci_render.py render   [--repo R] [--agent REL_SIM ...] [--name REL_SIM=NAME ...] [--env ALIAS] [--smoke [REL_SIM=]a,b ...]
                          [--catalog catalog.json] [--oidc aws|azure|gcp] [--sync-env] [--force] [--plurai BIN]
    ci_render.py check    [--repo R] [--agent REL_SIM ...]
    ci_render.py lines    [--repo R] [--agent REL_SIM ...]

One workflow file per agent, ``.github/workflows/plurai-<name>.yml``, from one template by one function: no matrix. A rendered
file has a managed block, marked with a ``rev``, and user-owned regions (the triggers, the wall bound, the install steps, the
``env:`` block and the cloud-credentials step). A re-render regenerates the managed text and keeps each user-owned region as the file
holds it; ``check`` reports any difference between a file and what a re-render would write. A managed block that was edited by hand
(its text differs from what the current template renders for the revision it carries) is not overwritten: ``render`` prints
``REFUSED-DRIFT`` with the diff, writes nothing and exits 1, unless ``--force``. A file at the workflow's path whose first line is
not the marker is not ours: ``render`` prints ``REFUSED-UNMANAGED`` and exits 1 the same way. A file of an older revision is
upgraded without ``--force``. Two things are refused with no override, because the action would refuse the run: ``pull_request_target``
in the triggers (``FORBIDDEN-TRIGGER``) and anything but ``timeout-minutes`` in the wall-bound region (``FORBIDDEN-WALL-BOUND``).
``ci.yaml`` is written once and never replaced, it is the user's file; a confirmed ``--smoke`` list is the one thing a re-render
writes into it, through the same line editor as ``ci_edit.py smoke`` (comments kept).

The triggers are the user's choice, made in the file. The first render writes ``workflow_dispatch`` (a ``selection`` input, ``smoke``
by default) and, below it, the other four events as commented lines ready to uncomment; a re-render keeps the block as the file holds
it. The script reads no trigger option and prints nothing that depends on which events are on.

The workflow carries no ``plurai ci run`` flag and no trigger-to-selection mapping: the action owns both.

Without a url the workflow's run uses production.

Exit codes: 0 done (``check``: in step), 1 ``check`` found drift, a missing env line or a forbidden shape (``render``: it refused a
file it does not own), 2 a usage problem or an unreadable file, 65 ``plurai ci validate`` refused what was written, 7 and 13 the
validator's version and credit refusals, passed through. A refused render puts back every file it wrote. A run whose validator
was not given prints ``NOT-VALIDATED`` and exits 0: the files are written and unchecked.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_common as common
from ci_common import Agent

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'
ACTION_REF_ENV = 'PLURAI_CI_ACTION_REF'
DEFAULT_ACTION_REF = 'v1'
DEV_URL_ENV = 'PLURAI_CI_DEV_URL'
_REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]*')

#: Third-party actions the install steps use, pinned by commit with the release in a comment.
SETUP_UV = 'astral-sh/setup-uv@18d369b23da25a81e9458561248e1aafff6a70fa # v10.3.1'
SETUP_PYTHON = 'actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0'
PYTHON_VERSION = '3.12'

TRIGGERS_LABEL = 'triggers'
WALL_BOUND_LABEL = 'wall bound'
ENV_LABEL = 'the names in plurai.config.yaml agent.env'
_TIMEOUT_LINE = re.compile(r'timeout-minutes:\s*\d+\s*(?:#.*)?')
#: An ``env:`` line, with or without a trailing comment.
_ENV_LINE = re.compile(r'^\s*env:\s*(?:#.*)?$')
#: A one-line ``on:`` (``on: [push]``, ``on: {push: {}}``, ``on: push``).
_INLINE_ON = re.compile(r'^on:[ \t]*(?P<value>[^\s#].*?)\s*$')

_GITHUB_COMMAND_FILE = "a GitHub Actions workflow-command file: what the agent writes there changes the job's later steps"
_GITLAB_JOB_JWT = "GitLab's signed job identity token"

#: The ``agent.env`` names a CI run refuses, each with why: the SDK's own map (``plurai.child_env``), name for name, because the
#: plugin refuses a name where it is declared and the SDK refuses the same name when the run starts. A repository declares
#: ``agent.env`` and a pull request can change it, so these are the names that would hand the agent a CI credential, a token
#: endpoint, or a file that steers the job. A platform's other variables (``CI_ENVIRONMENT``, ``GITHUB_REPOSITORY``,
#: ``RUNNER_TEMP``) carry no credential and are an app's to pass on, unless they read as one (:data:`_CI_PLATFORM_PREFIXES`).
#: Keys are upper case; matching ignores case.
RESERVED_AGENT_ENV: Mapping[str, str] = {
    'GITHUB_TOKEN': "the GitHub Actions job's repository token",
    'GH_TOKEN': 'the GitHub CLI token',
    'GH_ENTERPRISE_TOKEN': 'the GitHub CLI token for a GitHub Enterprise host',
    'GITHUB_ENTERPRISE_TOKEN': 'the GitHub CLI token for a GitHub Enterprise host',
    'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'the GitHub Actions credential that mints OIDC tokens for cloud sign-in',
    'ACTIONS_ID_TOKEN_REQUEST_URL': 'the GitHub Actions endpoint that mints OIDC tokens for cloud sign-in',
    'ACTIONS_RUNTIME_TOKEN': "the GitHub Actions runner's own token",
    'ACTIONS_RUNTIME_URL': "a GitHub Actions runner service endpoint, used with the runner's own token",
    'ACTIONS_RESULTS_URL': "a GitHub Actions runner service endpoint, used with the runner's own token",
    'ACTIONS_CACHE_URL': "a GitHub Actions runner service endpoint, used with the runner's own token",
    'GITHUB_ENV': _GITHUB_COMMAND_FILE,
    'GITHUB_PATH': _GITHUB_COMMAND_FILE,
    'GITHUB_OUTPUT': _GITHUB_COMMAND_FILE,
    'GITHUB_STATE': _GITHUB_COMMAND_FILE,
    'GITHUB_STEP_SUMMARY': _GITHUB_COMMAND_FILE,
    'CI_JOB_TOKEN': "GitLab's job token",
    'CI_BUILD_TOKEN': "GitLab's job token, under its older name",
    'CI_JOB_JWT': _GITLAB_JOB_JWT,
    'CI_JOB_JWT_V1': _GITLAB_JOB_JWT,
    'CI_JOB_JWT_V2': _GITLAB_JOB_JWT,
    'CI_REPOSITORY_URL': "GitLab's clone URL, which carries the job token",
    'CI_REGISTRY_PASSWORD': "GitLab's container registry password",
    'CI_DEPLOY_PASSWORD': "GitLab's deploy token",
    'CI_DEPENDENCY_PROXY_PASSWORD': "GitLab's dependency proxy password",
    'SYSTEM_ACCESSTOKEN': "Azure Pipelines' job token",
    'BUILDKITE_AGENT_ACCESS_TOKEN': "Buildkite's job token",
    'BUILDKITE_AGENT_TOKEN': "Buildkite's agent registration token",
    'CIRCLE_OIDC_TOKEN': "CircleCI's OIDC token",
    'CIRCLE_OIDC_TOKEN_V2': "CircleCI's OIDC token",
    'BITBUCKET_STEP_OIDC_TOKEN': "Bitbucket Pipelines' OIDC token",
    'BUILDKITE_AGENT_JOB_API_SOCKET': "Buildkite's job API socket, which changes the job's environment",
}

#: The prefixes of the CI platforms' own variables. Under them a name that reads as a credential is reserved too, so a token a
#: platform adds later, or one the map misses, is refused rather than passed on.
_CI_PLATFORM_PREFIXES: tuple[str, ...] = (
    'GITHUB_', 'GH_', 'ACTIONS_', 'RUNNER_', 'CI_', 'BUILDKITE_', 'CIRCLE_', 'BITBUCKET_', 'TRAVIS_', 'JENKINS_', 'SYSTEM_',
)

#: A name segment that marks a CI credential: the common credential words and the token kinds CI platforms name.
_CI_CREDENTIAL_SEGMENT = re.compile(r'(?:^|_)(?:TOKENS?|KEYS?|SECRETS?|PASSWORDS?|CREDENTIALS?|JWT|ACCESSTOKEN|OIDC)(?:_|$)')

_CI_CREDENTIAL_REASON = 'a name under a CI platform prefix that reads as a credential'

#: Plurai's own control: the backend pair, the run origin and the SDK's settings. The runner sets what the agent gets under it, so a
#: declaration cannot add to it.
RESERVED_AGENT_ENV_PREFIX = 'PLURAI_'

_PLURAI_REASON = 'a Plurai control variable; the runner sets the ones the agent gets'

RESERVED_FIX = 'remove it from agent.env in plurai.config.yaml, and give the agent a variable name of its own if it needs a key.'


def reserved_agent_env_reason(name: str) -> str | None:
    """Why a CI run refuses ``name`` as a declared ``agent.env`` name; ``None`` when it may be declared. The SDK's rule, in its order."""
    upper = name.upper()
    if upper.startswith(RESERVED_AGENT_ENV_PREFIX):
        return _PLURAI_REASON
    if (reason := RESERVED_AGENT_ENV.get(upper)) is not None:
        return reason
    if upper.startswith(_CI_PLATFORM_PREFIXES) and _CI_CREDENTIAL_SEGMENT.search(upper):
        return _CI_CREDENTIAL_REASON
    return None


def reserved_why(name: str) -> str:
    """Why ``name`` is refused: the variable and the reason. Only for a name :func:`reserved_agent_env_reason` reserves."""
    return f'agent.env declares {name}, which is reserved ({reserved_agent_env_reason(name)}), so it is refused and never reaches the agent.'


def reserved_message(name: str) -> str:
    """The whole refusal for a reserved ``agent.env`` name: the variable, why it is reserved and what to do."""
    return f'{reserved_why(name)} Fix: {RESERVED_FIX}'


def reserved_names(names: Iterable[str]) -> list[str]:
    """The reserved names among ``names``, each once."""
    return list(dict.fromkeys(name for name in names if reserved_agent_env_reason(name) is not None))


def _refuse_reserved(agents: Sequence[Agent]) -> None:
    """Raise ``RenderError`` for the first reserved ``agent.env`` name of any of ``agents``, before anything is written or printed."""
    for agent in agents:
        if reserved := reserved_names(common.agent_env(common.read_config(agent.sim_dir))):
            raise RenderError(f'{agent.rel_sim}: {reserved_message(reserved[0])}')


_REGION_START = re.compile(r'^\s*# --- user-owned: (?P<label>.+?) ---\s*$')
_REGION_END = re.compile(r'^\s*# --- end user-owned ---\s*$')
_PLACEHOLDER_LINE = re.compile(r'^\{\{(?P<key>[a-z_]+)\}\}$')
_REV = re.compile(r'^# plurai:managed rev=(?P<rev>\d+)\b')

#: The cloud-credentials step of each provider: its name, the pinned action, and the inputs with the placeholder that names each value.
OIDC_STEPS = {
    'aws': (
        'Bedrock',
        'aws-actions/configure-aws-credentials@e1253824e5c10ff9df46874f81ed3ec929e19cfd # v6.3.0',
        (('role-to-assume', 'role arn'), ('aws-region', 'region')),
    ),
    'azure': (
        'Azure',
        'azure/login@a641126d1b8aa4d1fa005f4f92df94a3a4c4c906 # v3.1.0',
        (('client-id', 'client id'), ('tenant-id', 'tenant id'), ('subscription-id', 'subscription id')),
    ),
    'gcp': (
        'Vertex',
        'google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093 # v3.0.0',
        (('workload_identity_provider', 'provider resource name'), ('service_account', 'service account email')),
    ),
}


class RenderError(Exception):
    """A usage problem or a file this script cannot read; the message names the fix."""


@dataclass(frozen=True)
class Region:
    label: str
    start: str
    body: tuple[str, ...]
    end: str


def action_ref() -> tuple[str, bool]:
    """``(ref, replaced)``: the ref of our actions, the ``v1`` release tag unless ``PLURAI_CI_ACTION_REF`` names another branch,
    tag or commit, and whether it did."""
    value = os.environ.get(ACTION_REF_ENV, '').strip()
    if not value:
        return DEFAULT_ACTION_REF, False
    if not _REF.fullmatch(value) or '..' in value:
        raise RenderError(f'{ACTION_REF_ENV} is not a branch, tag or commit name: {value!r}')
    return value, True


def dev_url() -> bool:
    """Whether ``PLURAI_CI_DEV_URL`` is set: the workflow then passes the action the backend URL the repository variable
    ``PLURAI_URL`` holds, instead of using production."""
    return bool(os.environ.get(DEV_URL_ENV, '').strip())


def trigger_lines(branch: str) -> list[str]:
    """The body of the user-owned triggers block: ``workflow_dispatch`` and the four other events as commented lines. Removing the
    leading ``# `` of a line turns it on, and every note is a trailing comment, so the result is valid YAML."""
    return [
        '  workflow_dispatch:',
        '    inputs:',
        '      selection:',
        '        type: choice',
        '        options: [smoke, full]',
        '        default: smoke',
        '  # pull_request:          # runs the smoke list on each PR push; each run spends credits.',
        '  #                        # Needed before the check can be required in branch protection.',
        '  # merge_group:           # only with pull_request, when the check is required and the repo uses a merge queue.',
        '  # push:                  # runs the full suite on each push to the default branch; each run spends credits.',
        '  #                        # After a suite change merges, run this workflow on the default branch (Actions > Run workflow),',
        '  #                        # or turn on `push`, so the next pull request has a main run to compare with.',
        f'  #   branches: [{branch}]',
        '  # schedule:              # a nightly full run; each run spends credits. In a public repository GitHub turns a schedule off',
        '  #   - cron: "0 3 * * *"  # after 60 days without repository activity. A failure notifies whoever last edited this cron line.',
    ]


def workflow_events(text: str) -> tuple[str, ...]:
    """The events a workflow file's ``on:`` names; empty when the text is not a workflow."""
    try:
        document = common.yaml_module().safe_load(text)
    except Exception:  # noqa: BLE001 - a file that does not parse names no events
        return ()
    if not isinstance(document, dict):
        return ()
    on = document.get(True, document.get('on'))
    if isinstance(on, dict):
        return tuple(str(key) for key in on)
    if isinstance(on, list):
        return tuple(str(item) for item in on)
    return (on,) if isinstance(on, str) else ()


def _wrap_bare_triggers(text: str) -> str:
    """``text`` with its ``on:`` body inside the user-owned triggers markers when it has none, so a file without the block keeps the
    events it names. A file with the block, or with no ``on:`` mapping, is returned as it is."""
    if any(isinstance(part, Region) and part.label == TRIGGERS_LABEL for part in _split(text)):
        return text
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == 'on:'), None)
    if start is None:
        return _wrap_inline_triggers(text, lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace() and not lines[i].startswith('#')), len(lines))
    return '\n'.join([*lines[: start + 1], f'  # --- user-owned: {TRIGGERS_LABEL} ---', *lines[start + 1 : end], '  # --- end user-owned ---', *lines[end:]]) + '\n'


def _inline_events(value: str) -> list[str] | None:
    """The block-style body of a one-line ``on:`` value, one event per line; ``None`` when the value is not YAML this can read."""
    try:
        parsed = common.yaml_module().safe_load(value)
    except Exception:  # noqa: BLE001 - a value that does not parse is left as the file holds it
        return None
    if isinstance(parsed, str):
        return [f'  {parsed}:']
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return [f'  {item}:' for item in parsed]
    if isinstance(parsed, dict):
        dumped = common.yaml_module().safe_dump(parsed, default_flow_style=False, sort_keys=False).splitlines()
        return [f'  {re.sub(r": null$", ":", line)}' for line in dumped]
    return None


def _wrap_inline_triggers(text: str, lines: list[str]) -> str:
    """``text`` with a one-line ``on:`` rewritten as a block inside the triggers markers, so its events survive a re-render."""
    for index, line in enumerate(lines):
        match = _INLINE_ON.match(line)
        if match is None:
            continue
        body = _inline_events(match['value'])
        if body is None:
            return text
        return '\n'.join([*lines[:index], 'on:', f'  # --- user-owned: {TRIGGERS_LABEL} ---', *body, '  # --- end user-owned ---', *lines[index + 1 :]]) + '\n'
    return text


def _split(text: str) -> list[str | Region]:
    """``text`` as plain lines and user-owned regions, in order."""
    parts: list[str | Region] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = _REGION_START.match(lines[index])
        if not match:
            parts.append(lines[index])
            index += 1
            continue
        end = next((i for i in range(index + 1, len(lines)) if _REGION_END.match(lines[i])), None)
        if end is None:
            raise RenderError(f'the user-owned region "{match["label"]}" has no end marker "# --- end user-owned ---"; add it back')
        parts.append(Region(match['label'], lines[index], tuple(lines[index + 1 : end]), lines[end]))
        index = end + 1
    return parts


def regions(text: str) -> dict[str, Region]:
    return {part.label: part for part in _split(text) if isinstance(part, Region)}


def managed_rev(text: str) -> int | None:
    first = text.splitlines()[0] if text.strip() else ''
    match = _REV.match(first)
    return int(match['rev']) if match else None


def _indent(lines: Iterable[str], pad: str) -> list[str]:
    return [f'{pad}{line}' if line else line for line in lines]


def _pyproject_says(directory: Path, question: Callable[[str], bool]) -> bool:
    """Whether the ``pyproject.toml`` in ``directory`` answers ``question`` with yes; no file, or one that cannot be read, is no."""
    try:
        return question((directory / 'pyproject.toml').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError):
        return False


def detect_install(agent: Agent) -> list[str]:
    """The default install steps of the agent's user-owned region, from the lockfile the agent's directory holds.

    The action runs the agent with the nearest ``.venv`` above ``workdir``, else ``$VIRTUAL_ENV``, else ``python3``. uv and Poetry
    steps therefore build an in-project ``.venv`` with the dev group, where the plugin adds the SDK: the lock's SDK is then in the
    interpreter, and the action uses it as is when it is in the action's range, so a lock bump inside the minor needs no action
    release. The dev group flag is written only when the project's ``pyproject.toml`` defines that group: uv and Poetry both
    fail on a group that is not there. A requirements install goes into the interpreter ``setup-python`` puts on ``PATH``. The steps
    carry no ``--no-dev`` or ``--without dev``: each wins over the flag that adds the dev group back."""
    directory = agent.sim_dir.parent
    where = agent.rel_agent
    workdir = [] if where == '.' else [f'        working-directory: {where}']
    uv_root = next((d for d in (directory, agent.repo) if (d / 'uv.lock').is_file()), None)
    if uv_root is not None:
        target = '.' if uv_root == agent.repo else where
        if _pyproject_says(uv_root, common.uv_has_dev_group):
            return [
                f'      - uses: {SETUP_UV}',
                '        with:',
                f'          python-version: "{PYTHON_VERSION}"',
                '      # the action finds .venv in the project folder; the dev group holds the SDK the lock names, which the action uses',
                f'      - run: uv sync --directory {target} --no-default-groups --group dev',
            ]
        return [
            f'      - uses: {SETUP_UV}',
            '        with:',
            f'          python-version: "{PYTHON_VERSION}"',
            '      # the action finds .venv in the project folder; the project has no dev group, so the default sync installs what it declares',
            f'      - run: uv sync --directory {target}',
        ]
    if (directory / 'poetry.lock').is_file():
        has_dev = _pyproject_says(directory, common.poetry_has_dev_group)
        return [
            f'      - uses: {SETUP_PYTHON}',
            '        with:',
            f'          python-version: "{PYTHON_VERSION}"',
            '      # an in-project .venv is the one the action finds; the root is not installed (an app need not be a package)'
            + (',' if has_dev else ''),
            *(['      # and the dev group holds the SDK the lock names, which the action uses'] if has_dev else []),
            f'      - run: pip install poetry && poetry install --no-root{" --with dev" if has_dev else ""}',
            *workdir,
            '        env:',
            '          POETRY_VIRTUALENVS_IN_PROJECT: "true"',
        ]
    if (directory / 'requirements.txt').is_file():
        return [
            f'      - uses: {SETUP_PYTHON}',
            '        with:',
            f'          python-version: "{PYTHON_VERSION}"',
            '      - run: pip install -r requirements.txt',
            *workdir,
        ]
    if (directory / 'package-lock.json').is_file():
        return ['      - run: npm ci', *workdir]
    return ["      # install your agent's dependencies here: the action installs the Plurai SDK when your project has none"]


def _passable(name: str) -> bool:
    """Whether ``name`` may get an ``env:`` line: shaped like a variable and not reserved."""
    return bool(common.VARIABLE_NAME.fullmatch(name)) and reserved_agent_env_reason(name) is None


def _env_lines(names: Sequence[str]) -> list[str]:
    usable = [name for name in dict.fromkeys(names) if _passable(name)]
    if not usable:
        return ['        # env: no names are declared under agent.env in plurai.config.yaml']
    return ['        env:', *(f'          {name}: ${{{{ secrets.{name} }}}}' for name in usable)]


def _timeout_lines() -> list[str]:
    return [
        "    # timeout-minutes: <minutes>   # the run's wall bound; set it from the duration of a first run.",
        '    # Hitting it ends the run as "not measured: the run was cancelled".',
    ]


def _cloud_lines(kind: str) -> list[str]:
    provider, action, inputs = OIDC_STEPS[kind]
    return [
        f'      # {provider}: sign in with GitHub OIDC instead of a stored key. Fill in the values the placeholders name.',
        f'      - uses: {action}',
        '        with:',
        *(f'          {name}: <{placeholder}>' for name, placeholder in inputs),
    ]


def _expand(template: str, values: dict[str, list[str] | str]) -> str:
    out: list[str] = []
    for line in template.splitlines():
        match = _PLACEHOLDER_LINE.match(line)
        if match and isinstance(values.get(match['key']), list):
            out.extend(values[match['key']])  # type: ignore[arg-type]
            continue
        for key, value in values.items():
            if isinstance(value, str):
                line = line.replace('{{' + key + '}}', value)
        out.append(line)
    return '\n'.join(out) + '\n'


def _existing_cloud(old: dict[str, Region]) -> str | None:
    """The cloud provider of the cloud-credentials region a file already holds, so a re-render keeps it."""
    region = old.get('cloud credentials')
    if region is None:
        return None
    text = ' '.join(region.body)
    return next((kind for kind, (_provider, action, _inputs) in OIDC_STEPS.items() if action.split('@')[0] in text), 'aws')


def render_workflow(
    agent: Agent,
    names: Sequence[str],
    *,
    existing: str | None = None,
    oidc: str | None = None,
    sync_env: bool = False,
    branch: str = 'main',
) -> str:
    """The workflow file of ``agent``: managed text from the template, user-owned regions from ``existing`` when it holds them
    (the triggers among them)."""
    ref, dev = action_ref()
    old = regions(_wrap_bare_triggers(existing)) if existing else {}
    cloud = oidc or _existing_cloud(old)
    cloud_region: list[str] = []
    if cloud:
        cloud_region = [
            '      # --- user-owned: cloud credentials ---',
            *_cloud_lines(cloud),
            '      # --- end user-owned ---',
        ]
    id_token = ['      id-token: write     # ' + ('a ref other than the release tag signs in with the job\'s OIDC token' if dev and not cloud else 'the GitHub OIDC cloud-credentials step')] if (dev or cloud) else []
    values: dict[str, list[str] | str] = {
        'rev': str(common.MANAGED_REV),
        'agent': agent.name,
        'branch': branch,
        'ref': ref,
        'workdir': agent.rel_sim,
        'id_token': id_token,
        'triggers': trigger_lines(branch),
        'url': ['          url: ${{ vars.PLURAI_URL }}            # the backend the repository variable names'] if dev_url() else [],
        'timeout': _timeout_lines(),
        'cloud_region': cloud_region,
        'install': detect_install(agent),
        'env': _env_lines(names),
    }
    text = _expand((TEMPLATES / 'workflow.yml.tmpl').read_text(encoding='utf-8'), values)
    merged: list[str] = []
    known: set[str] = set()
    for part in _split(text):
        if isinstance(part, str):
            merged.append(part)
            continue
        known.add(part.label)
        kept = old.get(part.label)
        body = list(kept.body if kept else part.body)
        if sync_env and part.label.startswith(ENV_LABEL):
            body = _sync_env_body(body, names)
        merged.extend([part.start, *body, part.end])
    unknown = [label for label in old if label not in known]
    if unknown:
        raise RenderError(
            f'the file holds the user-owned region "{unknown[0]}", which the template has no place for, so a re-render would delete it; '
            'move its content into a region the template has, or remove its markers'
        )
    return '\n'.join(merged) + '\n'


def sync_env_region(text: str, names: Sequence[str]) -> str:
    """``text`` with an ``env:`` line for each name the env region lacks, and every other line as it is. The managed block, and
    any hand edit in it, is not rendered again."""
    out: list[str] = []
    for part in _split(text):
        if isinstance(part, str):
            out.append(part)
        elif part.label.startswith(ENV_LABEL):
            out.extend([part.start, *_sync_env_body(list(part.body), names), part.end])
        else:
            out.extend([part.start, *part.body, part.end])
    return '\n'.join(out) + '\n'


def _sync_env_body(body: list[str], names: Sequence[str]) -> list[str]:
    """``body`` with an ``env:`` line for each declared name it lacks; the user's own lines stay as they are."""
    present = set(re.findall(r'^\s+([A-Za-z_][A-Za-z0-9_]*):', '\n'.join(body), re.MULTILINE))
    missing = [name for name in dict.fromkeys(names) if name not in present and _passable(name)]
    if not missing:
        return body
    if not any(_ENV_LINE.match(line) for line in body):
        body = [*(line for line in body if not line.strip().startswith('# env: no names')), '        env:']
    return [*body, *(f'          {name}: ${{{{ secrets.{name} }}}}' for name in missing)]


def render_ci_yaml(env_alias: str, smoke: Sequence[str]) -> str:
    """``ci.yaml`` with its five keys, the comments and the confirmed smoke list. Values go through the YAML emitter, so a name
    that reads as another type (``true``, ``123``) is written as a string; a name outside the scenario pattern is refused."""
    if not common.ENV_ALIAS.fullmatch(env_alias):
        raise RenderError(f'the env alias {env_alias!r} is not a name: use letters, digits, ".", "_" and "-"')
    bad = [name for name in smoke if not common.SCENARIO_NAME.fullmatch(name)]
    if bad:
        raise RenderError(f'{bad[0]!r} is not a scenario name: use letters, digits, ".", "_" and "-", starting with a letter or digit')
    dump = common.yaml_module().safe_dump
    env_line = dump({'env': env_alias}, default_flow_style=False).strip().ljust(30) + '# alias of a directory under envs/'
    comment = '# scenario names a PR runs; regression_* scenarios are always added'
    if smoke:
        first, *rest = dump({'smoke': list(smoke)}, default_flow_style=False).splitlines()
        smoke_lines = [first.ljust(30) + comment, *(f'  {line}' for line in rest)]
    else:
        smoke_lines = ['smoke: []'.ljust(30) + comment]
    return _expand((TEMPLATES / 'ci.yaml.tmpl').read_text(encoding='utf-8'), {'env': [env_line], 'smoke': smoke_lines})


def forbidden_lines(workflow: str, text: str) -> list[str]:
    """What a workflow file holds that the action refuses or that widens the token: ``pull_request_target`` in the triggers, and any
    line but ``timeout-minutes`` in the wall-bound region (a job-level ``permissions`` or ``if`` there would change who may use the
    job's token). No flag overrides these."""
    found: list[str] = []
    if 'pull_request_target' in workflow_events(text):
        found.append(
            f'FORBIDDEN-TRIGGER {workflow}: the triggers block names pull_request_target, which runs with write access on a fork\'s code. '
            'The action refuses it (exit 65). Remove it from the triggers block.'
        )
    wall = regions(text).get(WALL_BOUND_LABEL)
    for line in wall.body if wall else ():
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and not _TIMEOUT_LINE.fullmatch(stripped):
            found.append(
                f'FORBIDDEN-WALL-BOUND {workflow}: the wall-bound region holds {stripped[:80]!r}. It may hold timeout-minutes and comments only; '
                'a job-level key there (permissions, if, environment) changes who can use the job\'s token. Remove it.'
            )
    return found


def drift(agent: Agent, existing: str, names: Sequence[str], *, branch: str = 'main') -> list[str]:
    """What a re-render would change in ``existing``, as diff lines; empty when the file is in step."""
    wanted = render_workflow(agent, names, existing=existing, branch=branch)
    if wanted == existing:
        return []
    return list(difflib.unified_diff(existing.splitlines(), wanted.splitlines(), 'in the file', 're-render', lineterm='', n=1))


# --------------------------------------------------------------------------- the lines the user runs

def provider_first(names: Sequence[str], provider_names: Iterable[str]) -> list[str]:
    wanted = set(provider_names)
    ordered = list(dict.fromkeys(names))
    return [n for n in ordered if n in wanted] + [n for n in ordered if n not in wanted]


def secret_lines(agent: Agent, names: Sequence[str], repo_arg: str) -> list[str]:
    usable = [name for name in names if _passable(name)]
    return [f'gh secret set {shlex.quote(name)}{repo_arg}' for name in provider_first(usable, _provider_names(agent))]


def _provider_names(agent: Agent) -> tuple[str, ...]:
    """The env names the bench manifest says carry the agent's model-provider key; empty when there is no manifest."""
    sys.path.insert(0, str(common.SCRIPTS))
    sys.path.insert(0, str(common.HOOKS))
    try:
        import bench_manifest as bm
        doc = bm._read_json(bm.manifest_path(agent.sim_dir))
        if not isinstance(doc, dict) or not isinstance(doc.get('harness'), dict):
            return ()
        return tuple(bm.resolved_provider(doc, agent.sim_dir)[1])
    except Exception:  # noqa: BLE001 - an unreadable manifest only loses the ordering
        return ()


def start_lines(agent: Agent) -> list[str]:
    """How a manual run of ``agent`` starts, and where the other triggers are turned on."""
    return [
        f'How a run of {agent.name} starts:',
        '',
        f'- Manually: the Actions tab, the workflow "Plurai simulation ({agent.name})", "Run workflow", or:',
        f'    gh workflow run plurai-{agent.name}.yml --ref <branch> -f selection=smoke',
        '  GitHub lists a manual workflow, and `gh workflow run` finds it, only once the workflow file is on the default branch.',
        '  A manual run is smoke unless you pick full. Every run spends credits.',
    ]


#: What the user hears about main's baseline: main runs only when asked, so a suite change that merges leaves the next pull request
#: with no "vs main" comparison until main has run on the new suite.
MAIN_BASELINE = (
    'After a suite change merges, run the workflow on the default branch (Actions > Run workflow), or turn on the `push` trigger: '
    'main never runs on its own, so until it has, the next pull request has no "vs main" comparison on the new suite.'
)


def printed_lines(agents: Sequence[Agent], repo: Path, *, slug: str | None = None) -> str:
    """The text of Step 5: the token line, the secret lines, how a run starts and the options the user hears. No value appears in it."""
    owner_repo = slug or '/'.join(common.repo_slug(repo) or ('<owner>', '<repo>'))
    quoted = shlex.quote(owner_repo)
    out = [
        '## Token and secrets (run these yourself; the skill never runs them and never sees a value)',
        '',
        'Sign in, then mint the CI key and write it into the repository secret PLURAI_TOKEN:',
        '',
        '    plurai login',
        f'    plurai ci token --github {quoted}',
        '',
        'The key has full access to your Plurai organisation, and every run of a workflow in this repository uses it.',
        'Write access to the repository is the boundary. A team that wants a gate before the key is used makes the job wait',
        'on a GitHub Environment with required reviewers.',
        '',
    ]
    for agent in agents:
        out += [*start_lines(agent), '']
    out += [
        'To run on pull requests, the merge queue, pushes or a schedule, uncomment those lines in the triggers block; each run spends credits.',
        '',
        MAIN_BASELINE,
        '',
    ]
    shown: list[str] = []
    for agent in agents:
        names = common.agent_env(common.read_config(agent.sim_dir))
        lines = secret_lines(agent, names, f' --repo {quoted}')
        if lines:
            out += [f'Provider and agent secrets for {agent.name} (the provider key first):', '', *(f'    {line}' for line in lines), '']
            shown += names
    if shown:
        out += [
            'Use a provider key made for CI alone, with a low spend limit: every run of a workflow in this repository can read it.',
            'For Bedrock, Azure or Vertex, sign in with GitHub OIDC (a cloud-credentials step in the workflow) instead of a stored key.',
            '',
        ]
    out += ['Options:', '']
    url_env = ' PLURAI_URL="$PLURAI_URL"' if dev_url() else ''
    for agent in agents:
        names = [n for n in common.agent_env(common.read_config(agent.sim_dir)) if _passable(n)]
        carried = ''.join(f' {name}="${name}"' for name in names)
        out += [
            f'- A local dry run of {agent.name} in a clean environment, before the push ({"token and URL come" if url_env else "the token comes"} from your shell):',
            (
                f'    cd {shlex.quote(agent.rel_sim)} && env -i HOME="$HOME" PATH="$PATH"{url_env} PLURAI_TOKEN="$PLURAI_TOKEN"{carried}'
                f' PLURAI_CI_OUT_DIR="$(mktemp -d)" plurai ci run --repo {quoted} --selection smoke'
            ),
        ]
    out.append('')
    if dev_url():
        out += ['Also set the repository variable PLURAI_URL to the backend URL (Settings > Secrets and variables > Actions > Variables).', '']
    out += [
        'The suite changes a run makes show in its job summary, and in the comment of a pull request; `plurai suite diff`',
        'prints the same locally.',
        '',
        'Until the secrets above are set, a manual run is not measured: an empty name declared under agent.env shows as',
        '"not measured: <NAME> empty", and a missing token as "not measured: no secrets (PLURAI_TOKEN is not set)".',
    ]
    return '\n'.join(out) + '\n'


# --------------------------------------------------------------------------- command line

def _select(agents: list[Agent], wanted: Sequence[str], renames: Sequence[str]) -> list[Agent]:
    mapping = {}
    for item in renames:
        rel, _, name = item.partition('=')
        if not rel or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
            raise RenderError(f'--name takes REL_SIM=NAME with a lower-case name, got {item!r}')
        mapping[rel] = name
    chosen = [a for a in agents if not wanted or a.rel_sim in wanted]
    missing = sorted(set(wanted) - {a.rel_sim for a in agents})
    if missing:
        raise RenderError(f'no plurai_sim/ directory at {", ".join(missing)}; the agents here are {", ".join(a.rel_sim for a in agents) or "none"}')
    chosen = [Agent(mapping.get(a.rel_sim, a.name), a.repo, a.sim_dir) for a in chosen]
    unsafe = [a.rel_sim for a in chosen if not a.safe]
    if unsafe:
        raise RenderError(
            f'the directory {unsafe[0]!r} has characters that are not allowed in a workflow file or a command line '
            '(letters, digits, ".", "_", "-" and "/" are; no leading "-", no ".."). fix: rename the directory'
            + (', or pick the other agents with --agent.' if len(chosen) > 1 else '.')
        )
    if not chosen:
        raise RenderError('no plurai_sim/ directory found; set up the simulation first with /simulation-scan-agent')
    clash = common.duplicate_names(chosen)
    if clash:
        name, paths = next(iter(clash.items()))
        raise RenderError(f'{" and ".join(paths)} would both render plurai-{name}.yml; name one with --name {paths[0]}=<name>')
    return chosen


def _smoke_map(values: Sequence[str], agents: Sequence[Agent]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for value in values:
        rel, sep, names = value.partition('=')
        if not sep:
            if len(agents) != 1:
                raise RenderError('--smoke needs REL_SIM=a,b when more than one agent is rendered')
            rel, names = agents[0].rel_sim, value
        out[rel] = [n.strip() for n in names.split(',') if n.strip()]
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    repo = common.repo_root(Path(args.repo))
    agents = common.discover_agents(repo)
    for agent in agents:
        print(json.dumps({'name': agent.name, 'sim_dir': agent.rel_sim, 'workflow': agent.workflow, 'has_simulate': agent.has_simulate, 'safe': agent.safe}))
    clash = common.duplicate_names(agents)
    for name, paths in clash.items():
        print(f'NAME-CLASH {name}: {", ".join(paths)}', file=sys.stderr)
    if not agents:
        print('no plurai_sim/ directory found', file=sys.stderr)
    return 0


def _hand_edits(agent: Agent, repo: Path, branch: str) -> list[str]:
    """The diff lines where a managed block differs from what the current template renders. The difference is a hand edit, or an input the
    template reads (the action ref, the backend URL setting, the default branch) that is not what the file was rendered with. A file of an older
    revision, or one this script does not manage, differs by design and reports nothing; a file of a newer revision is compared like
    a current one, so a re-render never silently downgrades it."""
    path = repo / agent.workflow
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    rev = managed_rev(text)
    if rev is None or rev < common.MANAGED_REV:
        return []
    return drift(agent, _wrap_bare_triggers(text), common.agent_env(common.read_config(agent.sim_dir)), branch=branch)


def _check_values(agents: Sequence[Agent], smoke: dict[str, list[str]], args: argparse.Namespace, regressions: Sequence[str] = ()) -> None:
    """Refuse every value that would reach a file before anything is written. A confirmed smoke list applies to an existing ``ci.yaml``
    only when it is not empty: an empty one leaves the file's list as it is."""
    import ci_edit  # noqa: PLC0415 - ci_edit imports this module: loaded on demand to break the cycle

    for agent in agents:
        names = smoke.get(agent.rel_sim, [])
        if (agent.sim_dir / common.CI_YAML).is_file():
            if names and names != ci_edit.current_smoke(agent):
                try:
                    ci_edit.smoke_lines(agent, names, regressions)
                except ci_edit.EditError as error:
                    raise RenderError(str(error)) from error
        else:
            render_ci_yaml(args.env or common.pick_env_alias(agent.sim_dir) or common.DEFAULT_ENV_ALIAS, names)


def catalog_regressions(path: Path | None) -> list[str]:
    """The ``regression_*`` scenario names of a catalog file, which every smoke run holds besides its smoke list; none without a catalog."""
    if path is None:
        return []
    import ci_smoke  # noqa: PLC0415 - only a command that was given a catalog needs it

    return sorted(name for name in ci_smoke.read_catalog(path) if name.startswith(common.REGRESSION_PREFIX))


def _unmanaged(agent: Agent, repo: Path) -> bool:
    """Whether a file already sits at the agent's workflow path that this script did not write: its first line is not the marker."""
    path = repo / agent.workflow
    return path.is_file() and managed_rev(path.read_text(encoding='utf-8')) is None


def _restore_workflow(path: Path, before: str | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
        for directory in (path.parent, path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break
    else:
        path.write_text(before, encoding='utf-8')


def cmd_render(args: argparse.Namespace) -> int:
    repo = common.repo_root(Path(args.repo))
    agents = _select(common.discover_agents(repo), args.agent, args.name)
    smoke = _smoke_map(args.smoke, agents)
    _refuse_reserved(agents)
    _check_values(agents, smoke, args, catalog_regressions(args.catalog))
    branch = common.default_branch(repo)
    forbidden = [line for agent in agents if (repo / agent.workflow).is_file() for line in forbidden_lines(agent.workflow, (repo / agent.workflow).read_text(encoding='utf-8'))]
    if forbidden:
        print('\n'.join(forbidden))
        print('Nothing was written. No flag overrides this: remove what is named above from the file by hand.')
        return 1
    if not args.force:
        unmanaged = [agent for agent in agents if _unmanaged(agent, repo)]
        for agent in unmanaged:
            print(f'REFUSED-UNMANAGED {agent.workflow}: the first line is not a "# plurai:managed rev=N" marker, so this script did not write the file, and a render would replace all of it.')
        drifted = {agent: changes for agent in agents if (changes := _hand_edits(agent, repo, branch))}
        for agent, changes in drifted.items():
            print(f'REFUSED-DRIFT {agent.workflow}: the managed block differs from what the current template renders (a hand edit, or another action ref, backend URL or default branch), and a re-render would undo it:')
            print('\n'.join(f'    {line}' for line in changes[:60]))
        if drifted or unmanaged:
            print('Nothing was written. Move the edit into a user-owned region (the triggers, the install steps, the env block), or re-run with --force to overwrite it.')
            return 1
    import ci_edit  # noqa: PLC0415 - see _check_values

    regressions = catalog_regressions(args.catalog)
    code = 0
    for agent in agents:
        config = common.read_config(agent.sim_dir)
        names = common.agent_env(config)
        for name in common.rejected_env(config):
            print(f'SKIPPED {name[:60]!r}: not a variable name, so it gets no env line; fix it under agent.env in {common.CONFIG_NAME}')
        path = repo / agent.workflow
        existing = path.read_text(encoding='utf-8') if path.is_file() else None
        text = render_workflow(agent, names, existing=existing, oidc=args.oidc, sync_env=args.sync_env, branch=branch)
        workflow_changed = existing != text
        if workflow_changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        print(f'{"UPDATED" if existing and workflow_changed else "KEPT" if existing else "WROTE"} {agent.workflow}')
        if not existing:
            print(f'TRIGGERS {agent.workflow}: {", ".join(workflow_events(text))}')
        ci_path = agent.sim_dir / common.CI_YAML
        ci_rel = ci_path.relative_to(repo).as_posix()
        ci_before: str | None = None
        ci_created = not ci_path.is_file()
        if not ci_created:
            confirmed = smoke.get(agent.rel_sim, [])
            if confirmed and confirmed != ci_edit.current_smoke(agent):
                was = ci_edit.current_smoke(agent)
                ci_before = ci_path.read_text(encoding='utf-8')
                ci_edit.write_lines(ci_path, ci_edit.smoke_lines(agent, confirmed, regressions))
                print(f'SMOKE-SET {ci_rel}: {", ".join(confirmed)} (was: {", ".join(was) or "empty"}); its comments are kept')
            else:
                print(f'KEPT {ci_rel} (yours; edit it with /simulation-ci or by hand)')
        else:
            alias = args.env or common.pick_env_alias(agent.sim_dir) or common.DEFAULT_ENV_ALIAS
            ci_path.write_text(render_ci_yaml(alias, smoke.get(agent.rel_sim, [])), encoding='utf-8')
            print(f'WROTE {ci_rel}')
        status, message = common.validate(agent.sim_dir, args.plurai)
        if status in (0, -1):
            print(f'VALIDATED {agent.rel_sim}' if status == 0 else common.unchecked_line(agent.rel_sim, message))
            note = ci_edit.cap_note(agent, ci_edit.current_smoke(agent), regressions)
            if note:
                print(note)
        else:
            if ci_created:
                ci_path.unlink(missing_ok=True)
                print(f'REMOVED {ci_rel}')
            elif ci_before is not None:
                ci_path.write_text(ci_before, encoding='utf-8')
                print(f'RESTORED {ci_rel}')
            if workflow_changed:
                _restore_workflow(path, existing)
                print(f'{"REMOVED" if existing is None else "RESTORED"} {agent.workflow}')
            if status in common.VALIDATE_PASSTHROUGH:
                print(message)
                return status
            print(f'REFUSED {agent.rel_sim}: {message}')
            code = 65
    return code


def cmd_check(args: argparse.Namespace) -> int:
    repo = common.repo_root(Path(args.repo))
    agents = _select(common.discover_agents(repo), args.agent, args.name)
    _refuse_reserved(agents)
    branch = common.default_branch(repo)
    code = 0
    for agent in agents:
        path = repo / agent.workflow
        if not path.is_file():
            print(f'MISSING {agent.workflow}: render it with /simulation-ci')
            code = 1
            continue
        text = path.read_text(encoding='utf-8')
        names = common.agent_env(common.read_config(agent.sim_dir))
        rev = managed_rev(text)
        if rev is None:
            print(f'NOT-MANAGED {agent.workflow}: the first line is not a "# plurai:managed rev=N" marker; re-render to take it over')
            code = 1
            continue
        if rev < common.MANAGED_REV:
            print(f'STALE {agent.workflow}: managed block rev {rev}, current rev {common.MANAGED_REV}; re-render it')
        changes = drift(agent, text, names, branch=branch)
        bad = forbidden_lines(agent.workflow, text)
        if changes:
            code = 1
            print(f'DRIFT {agent.workflow}: the managed block differs from a re-render (a hand edit or a newer template):')
            print('\n'.join(f'    {line}' for line in changes))
        elif not bad:
            print(f'IN-STEP {agent.workflow}')
        if bad:
            code = 1
            print('\n'.join(bad))
        present = set(re.findall(r'\$\{\{\s*(?:secrets|vars)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}', text))
        for name in names:
            if name not in present and _passable(name):
                code = 1
                print(f'ENV-MISSING {agent.workflow}: agent.env declares {name}, which the env block does not pass; re-render with --sync-env')
    return code


def cmd_lines(args: argparse.Namespace) -> int:
    repo = common.repo_root(Path(args.repo))
    agents = _select(common.discover_agents(repo), args.agent, args.name)
    _refuse_reserved(agents)
    sys.stdout.write(printed_lines(agents, repo))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__.split('\n\n')[0] if __doc__ else None)
    sub = top.add_subparsers(dest='command', required=True)
    for name, func in (('discover', cmd_discover), ('render', cmd_render), ('check', cmd_check), ('lines', cmd_lines)):
        p = sub.add_parser(name)
        p.add_argument('--repo', default='.')
        if name != 'discover':
            p.add_argument('--agent', action='append', default=[], metavar='REL_SIM', help='a plurai_sim/ directory relative to the repository root; default all')
            p.add_argument('--name', action='append', default=[], metavar='REL_SIM=NAME', help='the name of an agent whose directory name another agent shares')
        if name == 'render':
            p.add_argument('--env', default=None, help='the ci.yaml env alias (default: the one under envs/)')
            p.add_argument('--smoke', action='append', default=[], metavar='[REL_SIM=]a,b', help='the confirmed smoke list of a new ci.yaml')
            p.add_argument('--catalog', type=Path, default=None, help='the scenario catalog JSON: its regression_* scenarios join every smoke run, so they count toward its size')
            p.add_argument('--oidc', choices=sorted(OIDC_STEPS), default=None, help='render the cloud-credentials region and id-token: write')
            p.add_argument('--sync-env', action='store_true', help='add an env line for each agent.env name the block lacks')
            p.add_argument('--plurai', default=None, help='the plurai runner; runs `plurai ci validate` after writing')
            p.add_argument('--force', action='store_true', help='re-render a managed block that was edited by hand, losing the edit')
        p.set_defaults(func=func)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RenderError, ValueError) as error:
        print(f'simulation-ci: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.path.insert(0, str(common.SCRIPTS))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
