"""What the /simulation-ci scripts share: the repository's agents, the repo slug, the config readers and the one validator call.

Stdlib and PyYAML only, like every runtime script of the plugin; ``tomllib`` is used when the interpreter has it (3.11 and newer).
Nothing here reads a secret value: the readers return names.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.10: the readers below fall back to line patterns
    tomllib = None  # type: ignore[assignment]

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'

sys.path.insert(0, str(SCRIPTS))

from _stdio import utf8_child_text  # noqa: E402 - scripts/_stdio.py, found through the path entry above

HOOKS = Path(__file__).resolve().parents[3] / 'hooks'

#: The managed-block revision the rendered workflow carries. The simulation action warns when a workflow's revision is older
#: than the one its release expects, so the two numbers move together.
MANAGED_REV = 1

#: The smoke run size (threads: scenarios × trials) the proposal keeps to. It is a provisional default, not a limit: a smoke list,
#: ``trials`` or a set of regression scenarios that makes a larger run is allowed, and the render, the edits and the CI readiness
#: check say that the run costs more threads. No other number in the skill sizes a run.
SMOKE_CAP_THREADS = 12

#: The longest ``plurai ci validate`` may run before it counts as refused.
VALIDATE_TIMEOUT_SECONDS = 300
#: The exit code ``validate`` returns when the validator did not finish in time.
VALIDATE_TIMED_OUT = 124
#: Validator exits that are not a verdict on the file: the plugin's version refusal and the credit refusal. A script ends with
#: the same code, so the skill's error posture can act on it.
VALIDATE_PASSTHROUGH = (7, 13)

DEFAULT_TRIALS = 3
#: A cell with fewer valid trials is "not measured", so a smaller `trials` can never end green.
MIN_TRIALS = 2
DEFAULT_ENV_ALIAS = 'default'
REGRESSION_PREFIX = 'regression_'
SIM_DIR = 'plurai_sim'
CONFIG_NAME = 'plurai.config.yaml'
CI_YAML = 'ci.yaml'
SIMULATE_FILE = 'simulate.py'
#: Directories a discovery walk never enters: dependencies and tooling state, which can hold a copy of another project.
SKIP_DIRS = frozenset({'.git', 'node_modules', '.venv', 'venv', 'site-packages', '__pycache__', '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build'})

_NAME_CHARS = re.compile(r'[^a-z0-9]+')
_SLUG = re.compile(r'(?:github\.com[:/])(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$')
VARIABLE_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
#: What a scenario name or an env alias may be: it ends up in ci.yaml, so it is a word, not YAML.
SCENARIO_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
ENV_ALIAS = SCENARIO_NAME
_SAFE_PATH = re.compile(r'[A-Za-z0-9._/-]+')


def safe_directory(rel: str) -> bool:
    """Whether a directory path may be written into a workflow file and a printed command: letters, digits, ``.``, ``_``, ``-`` and
    ``/``, relative, with no empty or ``..`` component and no component that starts with ``-``."""
    if not _SAFE_PATH.fullmatch(rel) or rel.startswith('/'):
        return False
    return all(part and part != '..' and not part.startswith('-') for part in rel.split('/'))


@dataclass(frozen=True)
class Agent:
    """One ``plurai_sim/`` directory: an agent that can get its own workflow file."""

    name: str
    repo: Path
    sim_dir: Path

    @property
    def rel_sim(self) -> str:
        """The simulation directory relative to the repository root, with forward slashes."""
        return self.sim_dir.relative_to(self.repo).as_posix()

    @property
    def rel_agent(self) -> str:
        """The agent's own directory (the parent of ``plurai_sim/``), relative to the repository root."""
        return self.sim_dir.parent.relative_to(self.repo).as_posix()

    @property
    def safe(self) -> bool:
        """Whether the agent's directories may reach YAML and a command line."""
        return safe_directory(self.rel_sim) and safe_directory(self.rel_agent)

    @property
    def workflow(self) -> str:
        return f'.github/workflows/plurai-{self.name}.yml'

    @property
    def has_simulate(self) -> bool:
        return (self.sim_dir / SIMULATE_FILE).is_file()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, **utf8_child_text(), check=False, timeout=60)


def repo_root(start: Path) -> Path:
    """The repository's top level, or ``start`` when it is not inside a git work tree."""
    try:
        done = git(start, 'rev-parse', '--show-toplevel')
    except (OSError, subprocess.SubprocessError):
        return start.resolve()
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else start.resolve()


def repo_slug(repo: Path) -> tuple[str, str] | None:
    """``(owner, repo)`` of the ``origin`` remote when it is a GitHub URL, else ``None``."""
    try:
        done = git(repo, 'remote', 'get-url', 'origin')
    except (OSError, subprocess.SubprocessError):
        return None
    match = _SLUG.search(done.stdout.strip()) if done.returncode == 0 else None
    return (match['owner'], match['repo']) if match else None


def default_branch(repo: Path) -> str:
    """The branch ``push`` triggers on: the remote's default branch when git knows it, else a local ``main`` or ``master``, else ``main``."""
    try:
        done = git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
        name = done.stdout.strip().removeprefix('origin/') if done.returncode == 0 else ''
        if _SAFE_PATH.fullmatch(name):
            return name
        for candidate in ('main', 'master'):
            if git(repo, 'rev-parse', '--verify', '-q', f'refs/heads/{candidate}').returncode == 0:
                return candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return 'main'


def agent_name(sim_dir: Path, repo: Path) -> str:
    """The name a workflow file and its concurrency groups carry: the agent's directory name, or the repository's for an agent at the root."""
    agent_dir = sim_dir.parent
    base = repo.name if agent_dir == repo else agent_dir.name
    return _NAME_CHARS.sub('-', base.lower()).strip('-') or 'agent'


def discover_agents(repo: Path) -> list[Agent]:
    """Every ``plurai_sim/`` directory under ``repo``, sorted by path. Directories that are dependencies or tool state are skipped."""
    found: list[Path] = []
    for directory, subdirs, _files in os.walk(repo):
        subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS)
        if Path(directory).name == SIM_DIR:
            found.append(Path(directory))
            subdirs[:] = []
    return [Agent(agent_name(path, repo), repo, path) for path in sorted(found)]


def duplicate_names(agents: Iterable[Agent]) -> dict[str, list[str]]:
    """Agent names two directories share, with the directories: each agent needs its own workflow file, so a shared name needs ``--name``."""
    seen: dict[str, list[str]] = {}
    for agent in agents:
        seen.setdefault(agent.name, []).append(agent.rel_sim)
    return {name: paths for name, paths in seen.items() if len(paths) > 1}


def yaml_module() -> Any:
    try:
        import yaml
    except ImportError:
        sys.exit('simulation-ci: PyYAML is not installed for this interpreter; run the script through "$PLURAI_PY".')
    return yaml


def read_yaml(path: Path) -> Any:
    """The parsed YAML of ``path``; ``None`` for a missing or empty file. A file that is not YAML raises ``ValueError`` naming it."""
    if not path.is_file():
        return None
    try:
        return yaml_module().safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f'{path} cannot be read: {error}') from error
    except Exception as error:
        raise ValueError(f'{path} is not valid YAML; fix the syntax') from error


def config_paths(sim_dir: Path) -> list[Path]:
    """The ``plurai.config.yaml`` files that can declare for ``sim_dir``, in the SDK's reading order: the agent directory, then ``plurai_sim/``."""
    return [path for path in (sim_dir.parent / CONFIG_NAME, sim_dir / CONFIG_NAME) if path.is_file()]


def read_config(sim_dir: Path) -> dict[str, Any]:
    """The merged top-level keys of the agent's config files: later files win for a key, ``agent.env`` lists join."""
    merged: dict[str, Any] = {}
    env: list[str] = []
    for path in config_paths(sim_dir):
        document = read_yaml(path)
        if isinstance(document, Mapping):
            raw = document.get('agent.env')
            if isinstance(raw, list):
                env.extend(raw)
            merged.update(document)
    merged['agent.env'] = list({repr(name): name for name in env}.values())
    return merged


def agent_env(config: Mapping[str, Any]) -> list[str]:
    """The ``agent.env`` names that are shaped like variable names. Anything else would reach ``${{ secrets.NAME }}`` and a
    ``gh secret set`` line as text, so it is dropped here; :func:`rejected_env` lists what was dropped."""
    names = config.get('agent.env')
    return [name for name in names if isinstance(name, str) and VARIABLE_NAME.fullmatch(name)] if isinstance(names, list) else []


def rejected_env(config: Mapping[str, Any]) -> list[str]:
    names = config.get('agent.env')
    return [str(name) for name in names if not (isinstance(name, str) and VARIABLE_NAME.fullmatch(name))] if isinstance(names, list) else []


def validate(sim_dir: Path, plurai_bin: str | None, *, online: bool = False) -> tuple[int, str]:
    """Run ``plurai ci validate`` from ``sim_dir`` through ``sdk_call.py``; return its exit code and its text (stderr then stdout).

    ``(-1, reason)`` when there is no runner to call. The SDK's version refusal reads as the plugin's text and exits 7. A validator
    that does not finish within ``VALIDATE_TIMEOUT_SECONDS``, or cannot be started, returns ``(VALIDATE_TIMED_OUT, reason)``.
    """
    if not plurai_bin:
        return -1, 'no plurai runner was given'
    argv = [sys.executable, str(SCRIPTS / 'sdk_call.py'), plurai_bin, 'ci', 'validate', *(['--online'] if online else [])]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, **utf8_child_text(), check=False, cwd=sim_dir, timeout=VALIDATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return VALIDATE_TIMED_OUT, f'plurai ci validate did not finish in {VALIDATE_TIMEOUT_SECONDS} seconds'
    except OSError as error:
        return VALIDATE_TIMED_OUT, f'plurai ci validate could not be started ({type(error).__name__})'
    return done.returncode, '\n'.join(part.strip() for part in (done.stderr, done.stdout) if part.strip())


def unchecked_line(rel_sim: str, reason: str) -> str:
    """The line a script prints when ``plurai ci validate`` did not run: the file is written and nothing has checked it."""
    return f'NOT-VALIDATED {rel_sim}: {reason}. This file is unchecked: run `plurai ci validate` from {rel_sim} before relying on it.'


def env_aliases(sim_dir: Path) -> list[str]:
    """The env aliases saved under ``envs/``, each a directory with its manifest."""
    root = sim_dir / 'envs'
    return sorted(path.name for path in root.iterdir() if (path / 'plurai.env.yaml').is_file()) if root.is_dir() else []


def pick_env_alias(sim_dir: Path) -> str | None:
    aliases = env_aliases(sim_dir)
    return DEFAULT_ENV_ALIAS if DEFAULT_ENV_ALIAS in aliases else (aliases[0] if aliases else None)


def tracked(repo: Path, path: Path) -> bool | None:
    """Whether git tracks ``path``: ``None`` outside a repository."""
    try:
        if git(repo, 'rev-parse', '--git-dir').returncode != 0:
            return None
        return git(repo, 'ls-files', '--error-unmatch', '--', str(path)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def ignored(repo: Path, path: Path) -> bool:
    try:
        return git(repo, 'check-ignore', '-q', '--', str(path)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def smoke_scenarios(names: Iterable[str], regressions: Iterable[str] = ()) -> int:
    """The scenarios a smoke run holds: the smoke list and every regression scenario, each once."""
    return len({*names, *regressions})


def smoke_over_cap(names: Iterable[str], regressions: Iterable[str], trials: int) -> bool:
    """Whether a smoke run of these names, the regression scenarios and ``trials`` holds more threads than the default size."""
    return smoke_scenarios(names, regressions) * trials > SMOKE_CAP_THREADS


def smoke_cap_note(names: Iterable[str], regressions: Iterable[str], trials: int) -> str | None:
    """The note for a smoke run over the default size, or ``None`` when it is within it. The run is allowed: the note says what it costs."""
    held = smoke_scenarios(names, regressions)
    if held * trials <= SMOKE_CAP_THREADS:
        return None
    return (
        f'a smoke run of {held} scenarios (the smoke list and every regression_* scenario) x {trials} trials = {held * trials} threads is over {SMOKE_CAP_THREADS}, '
        f'the size the proposal keeps to. {SMOKE_CAP_THREADS} is a provisional default, not a limit: the run goes ahead and costs more threads.'
    )


# --------------------------------------------------------------------------- TOML, with and without tomllib

def toml_loads(text: str) -> dict[str, Any] | None:
    """The parsed TOML ``text``; ``None`` when it is not TOML or this interpreter has no TOML parser."""
    if tomllib is None:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None


def _table(text: str, header: str) -> list[str]:
    """The lines of the table ``[header]`` (or of every ``[[header]]`` entry's own table), up to the next table line."""
    lines = text.splitlines()
    body: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            inside = stripped.rstrip() == f'[{header}]'
            continue
        if inside:
            body.append(line)
    return body


def lock_packages(text: str) -> list[dict[str, str]]:
    """The ``name`` and ``version`` of each ``[[package]]`` of a ``uv.lock`` or ``poetry.lock``, read as TOML when the interpreter
    can and by line otherwise."""
    document = toml_loads(text)
    if document is not None:
        return [{key: str(package[key]) for key in ('name', 'version') if key in package} for package in document.get('package', []) if isinstance(package, dict)]
    packages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            current = {} if stripped == '[[package]]' else None
            if current is not None:
                packages.append(current)
            continue
        match = re.match(r'(name|version)\s*=\s*["\']([^"\']*)["\']', stripped)
        if match and current is not None:
            current[match[1]] = match[2]
    return packages


def requires_python(text: str) -> str | None:
    """The ``project.requires-python`` specifier of a ``pyproject.toml``, or ``None``."""
    document = toml_loads(text)
    if document is not None:
        spec = document.get('project', {}).get('requires-python') if isinstance(document.get('project'), dict) else None
        return spec if isinstance(spec, str) else None
    for line in _table(text, 'project'):
        match = re.match(r'\s*requires-python\s*=\s*["\']([^"\']*)["\']', line)
        if match:
            return match[1]
    return None


def uv_has_dev_group(text: str) -> bool:
    """Whether a ``pyproject.toml`` defines the ``dev`` dependency group ``uv sync --group dev`` installs: a ``dev`` entry of
    ``[dependency-groups]``, or ``dev-dependencies`` of ``[tool.uv]``."""
    groups = any(re.match(r'\s*["\']?dev["\']?\s*=', line) for line in _table(text, 'dependency-groups'))
    legacy = any(re.match(r'\s*dev-dependencies\s*=', line) for line in _table(text, 'tool.uv'))
    return groups or legacy


def poetry_has_dev_group(text: str) -> bool:
    """Whether a ``pyproject.toml`` defines the Poetry ``dev`` group ``poetry install --with dev`` names."""
    return bool(re.search(r'^\s*\[tool\.poetry\.(?:group\.dev(?:\.dependencies)?|dev-dependencies)\]', text, re.MULTILINE))
