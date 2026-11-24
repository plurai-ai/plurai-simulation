#!/usr/bin/env python3
"""Re-entry edits of /simulation-ci: change ``ci.yaml`` and ``agent.env`` structurally, keeping the user's comments.

    ci_edit.py state    [--repo R] [--plurai BIN]
    ci_edit.py pending  [--repo R]
    ci_edit.py smoke    [--repo R] --agent REL_SIM  a,b,c [--catalog catalog.json]
    ci_edit.py trials   [--repo R] --agent REL_SIM  N     [--catalog catalog.json]
    ci_edit.py tags     [--repo R] --agent REL_SIM  catastrophic,failing,regression
    ci_edit.py env      [--repo R] --agent REL_SIM  ALIAS
    ci_edit.py override add    --agent REL_SIM [--scenario S] [--policy P] --reason TEXT [--expires YYYY-MM-DD] [--report run.json]
    ci_edit.py override change --agent REL_SIM --index N [--reason TEXT] [--expires YYYY-MM-DD|none]
    ci_edit.py override remove --agent REL_SIM --index N
    ci_edit.py agent-env add   --agent REL_SIM NAME [NAME ...]
    ci_edit.py github-names    [--repo R] --agent REL_SIM
    ci_edit.py remove-agent    [--repo R] --agent REL_SIM

Every edit is written by this script, never by the model: it reads the file as lines, changes the one key it was asked to change, and
leaves every other line, comment and blank as it was. Then it runs ``plurai ci validate`` (``--plurai BIN``); a refusal puts the
file back as it was and exits 65. Values that reach the file are checked against a pattern first and written through the YAML
emitter.

Overrides (``ignore`` rules) are CI-only and are offered only when a report exists: a run directory under ``artifacts/runs/`` or a
downloaded CI ``run.json`` given with ``--report``.

A run whose validator was not given prints ``NOT-VALIDATED``: the edit is written and unchecked.

``--catalog`` names the scenario catalog (the JSON of ``ci_smoke.py``): its ``regression_*`` scenarios join every smoke run, so they
count beside the smoke list toward the size of a run (``scenarios x trials`` threads). A run over the default size of 12 threads is
allowed: the edit prints a ``NOTE`` that it costs more threads.

Exit codes: 0 done, 2 a usage problem or a value outside its pattern, 65 ``plurai ci validate`` refused the edit or did not finish,
7 and 13 the validator's version and credit refusals, passed through with the files put back.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_common as common
import ci_readiness as readiness
import ci_render as render
from ci_common import Agent

KNOWN_TAGS = ('catastrophic', 'failing', 'standard', 'regression')
_TOP_KEY = re.compile(r'^(?P<key>[A-Za-z_][A-Za-z0-9_.]*):(?P<rest>.*)$')
_COMMENT_TAIL = re.compile(r'^(?P<value>.*?)(?P<gap>\s+)(?P<comment>#.*)$')
_SELECTOR = re.compile(r'[A-Za-z0-9_.*?-]{1,128}')
_FLOW_LIST = re.compile(r'^\s*\[(?P<items>.*)\]\s*$')


class EditError(Exception):
    """A value outside its pattern, or a file that cannot be edited; the message names the fix."""


# --------------------------------------------------------------------------- line-level structure

def scalar(value: object) -> str:
    """One YAML scalar on one line, written by the emitter."""
    text = common.yaml_module().safe_dump(value, default_flow_style=True, width=10**6)
    return text.splitlines()[0]


def _split_comment(rest: str) -> tuple[str, str, str]:
    """``(value, gap, comment)`` of the text after ``key:``; the comment is empty when there is none."""
    match = _COMMENT_TAIL.match(rest)
    if match and not (match['value'].strip().startswith(("'", '"')) and match['value'].count(match['value'].strip()[0]) % 2):
        return match['value'].strip(), match['gap'], match['comment']
    return rest.strip(), '', ''


def find_block(lines: list[str], key: str, start: int = 0, indent: int = 0) -> tuple[int, int] | None:
    """``(first, end)`` of the block of ``key`` at ``indent``: its line and the lines after it that are indented deeper, comments
    and blanks inside it included. ``end`` is exclusive."""
    pattern = re.compile(rf'^{" " * indent}{re.escape(key)}:(.*)$')
    for index in range(start, len(lines)):
        if pattern.match(lines[index]):
            end = index + 1
            while end < len(lines):
                line = lines[end]
                if line.strip() == '' or len(line) - len(line.lstrip()) > indent:
                    end += 1
                else:
                    break
            while end > index + 1 and lines[end - 1].strip() == '':
                end -= 1
            return index, end
    return None


def set_scalar(lines: list[str], key: str, value: object, *, indent: int = 0) -> list[str]:
    """``lines`` with the value of ``key`` replaced, its comment kept."""
    found = find_block(lines, key, indent=indent)
    if found is None:
        raise EditError(f'{key} is not in the file; add the key by hand first, or re-create the file with /simulation-ci')
    first = found[0]
    rest = lines[first].split(':', 1)[1]
    _value, gap, comment = _split_comment(rest)
    out = list(lines)
    pad = ' ' * indent
    out[first] = _compose(f'{pad}{key}: {scalar(value)}', gap, comment)
    return out


def _compose(head: str, gap: str, comment: str) -> str:
    if not comment:
        return head
    return head + (' ' * max(2, 30 - len(head)) if len(gap) >= 2 else '  ') + comment


def set_flow_list(lines: list[str], key: str, items: Sequence[str], *, indent: int = 0) -> list[str]:
    found = find_block(lines, key, indent=indent)
    if found is None:
        raise EditError(f'{key} is not in the file')
    first, _end = found
    _value, gap, comment = _split_comment(lines[first].split(':', 1)[1])
    out = list(lines)
    out[first] = _compose(f'{" " * indent}{key}: [{", ".join(scalar(i) for i in items)}]', gap, comment)
    return out


def set_block_list(lines: list[str], key: str, items: Sequence[str], *, indent: int = 0) -> list[str]:
    """``lines`` with the list under ``key`` replaced by ``items`` in block style. An item that was already there keeps its own line
    (and any comment on it); comment lines above an item stay with it; a list that becomes empty is written ``key: []``."""
    found = find_block(lines, key, indent=indent)
    if found is None:
        raise EditError(f'{key} is not in the file')
    first, end = found
    _value, gap, comment = _split_comment(lines[first].split(':', 1)[1])
    pad = ' ' * indent
    old: dict[str, tuple[list[str], str]] = {}
    lead: list[str] = []
    for line in lines[first + 1 : end]:
        match = re.match(rf'^{pad}\s*- (?P<name>[^#]*?)\s*(#.*)?$', line)
        if match and not line.strip().startswith('#'):
            name = match['name'].strip().strip('\'"')
            old[name] = (lead, line)
            lead = []
        else:
            lead.append(line)
    new_lines: list[str] = []
    for item in items:
        kept = old.get(item)
        if kept:
            new_lines += [*(x for x in kept[0] if x.strip()), kept[1]]
        else:
            new_lines.append(f'{pad}  - {scalar(item)}')
    trailing = [x for x in lead if x.strip()]  # comments after the last item belong to the list as a whole
    head = _compose(f'{pad}{key}:' if items else f'{pad}{key}: []', gap, comment)
    return [*lines[:first], head, *new_lines, *trailing, *lines[end:]]


# --------------------------------------------------------------------------- overrides (a list of mappings)

def _block_overrides(lines: list[str]) -> list[str]:
    """``lines`` with a flow-style ``overrides: [{...}, ...]`` written as a block list, so the line editors below see every rule."""
    found = find_block(lines, 'overrides')
    if found is None:
        return lines
    first, end = found
    value, gap, comment = _split_comment(lines[first].split(':', 1)[1])
    if not value.startswith('[') or value == '[]':
        return lines
    try:
        rules = common.yaml_module().safe_load(value)
    except Exception as error:  # noqa: BLE001 - a flow list that does not parse cannot be edited
        raise EditError('overrides in ci.yaml is not valid YAML; fix the syntax by hand first') from error
    rows: list[str] = []
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict) or not rule:
            raise EditError('overrides in ci.yaml holds an entry that is not a mapping; fix it by hand first')
        pairs = [f'{key}: {scalar(item)}' for key, item in rule.items()]
        rows += [f'  - {pairs[0]}', *(f'    {pair}' for pair in pairs[1:])]
    return [*lines[:first], _compose('overrides:', gap, comment), *rows, *lines[first + 1 : end], *lines[end:]]


def _override_ranges(lines: list[str]) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    found = find_block(lines, 'overrides')
    if found is None:
        raise EditError('overrides is not in ci.yaml; re-create the file with /simulation-ci')
    first, end = found
    starts = [i for i in range(first + 1, end) if re.match(r'^\s*- ', lines[i])]
    ranges = []
    for n, start in enumerate(starts):
        stop = starts[n + 1] if n + 1 < len(starts) else end
        # comment lines directly above the next item belong to it
        while stop > start + 1 and lines[stop - 1].lstrip().startswith('#') and n + 1 < len(starts):
            stop -= 1
        ranges.append((start, stop))
    return (first, end), ranges


def override_lines(scenario: str | None, policy: str | None, reason: str, expires: str | None) -> list[str]:
    if not scenario and not policy:
        raise EditError('an override needs a scenario or a policy (exact or a `*` glob)')
    for label, value in (('scenario', scenario), ('policy', policy)):
        if value is not None and not _SELECTOR.fullmatch(value):
            raise EditError(f'the {label} {value!r} is not a name or a glob: use letters, digits, ".", "_", "-", "*" and "?"')
    if not reason.strip() or '\n' in reason or len(reason) > 300 or any(ord(c) < 32 for c in reason):
        raise EditError('the reason is required: one line of text, at most 300 characters')
    rows: list[tuple[str, object]] = []
    if scenario:
        rows.append(('scenario', scenario))
    if policy:
        rows.append(('policy', policy))
    rows += [('ignore', True), ('reason', reason.strip())]
    out = [f'  - {rows[0][0]}: {scalar(rows[0][1])}', *(f'    {k}: {scalar(v)}' for k, v in rows[1:])]
    if expires:
        out.append(f'    expires: {_date(expires)}')
    return out


def _date(text: str) -> str:
    try:
        date.fromisoformat(text)
    except ValueError as error:
        raise EditError(f'expires {text!r} is not a date: write YYYY-MM-DD') from error
    return text


def add_override(lines: list[str], new: list[str]) -> list[str]:
    lines = _block_overrides(lines)
    (first, end), _ranges = _override_ranges(lines)
    _value, gap, comment = _split_comment(lines[first].split(':', 1)[1])
    head = _compose('overrides:', gap, comment)
    body = [x for x in lines[first + 1 : end]]
    return [*lines[:first], head, *body, *new, *lines[end:]]


def remove_override(lines: list[str], index: int) -> list[str]:
    lines = _block_overrides(lines)
    (first, end), ranges = _override_ranges(lines)
    if not 1 <= index <= len(ranges):
        raise EditError(f'there is no override {index}; ci.yaml has {len(ranges)}')
    start, stop = ranges[index - 1]
    body = [*lines[first + 1 : start], *lines[stop:end]]
    if len(ranges) == 1:  # the list is empty again: write the default, and keep the comment lines that were in the block
        _value, gap, comment = _split_comment(lines[first].split(':', 1)[1])
        notes = [line for line in body if line.lstrip().startswith('#')]
        return [*lines[:first], _compose('overrides: []', gap, comment), *notes, *lines[end:]]
    return [*lines[: first + 1], *body, *lines[end:]]


def change_override(lines: list[str], index: int, reason: str | None, expires: str | None) -> list[str]:
    lines = _block_overrides(lines)
    _block, ranges = _override_ranges(lines)
    if not 1 <= index <= len(ranges):
        raise EditError(f'there is no override {index}; ci.yaml has {len(ranges)}')
    start, stop = ranges[index - 1]
    item = list(lines[start:stop])
    if reason is not None:
        if not reason.strip() or '\n' in reason or len(reason) > 300 or any(ord(c) < 32 for c in reason):
            raise EditError('the reason is required: one line of text, at most 300 characters')
        item = _set_field(item, 'reason', scalar(reason.strip()))
    if expires is not None:
        item = _drop_field(item, 'expires') if expires == 'none' else _set_field(item, 'expires', _date(expires))
    return [*lines[:start], *item, *lines[stop:]]


def _field_index(item: list[str], name: str) -> int | None:
    return next((i for i, line in enumerate(item) if re.match(rf'^\s*(?:- )?{name}:', line)), None)


def _set_field(item: list[str], name: str, value: str) -> list[str]:
    at = _field_index(item, name)
    out = list(item)
    if at is None:
        return [*out, f'    {name}: {value}']
    prefix = re.match(r'^(\s*(?:- )?)', out[at])[0]  # type: ignore[index]
    _v, gap, comment = _split_comment(out[at].split(':', 1)[1])
    out[at] = _compose(f'{prefix}{name}: {value}', gap, comment)
    return out


def _drop_field(item: list[str], name: str) -> list[str]:
    at = _field_index(item, name)
    return item if at is None else [*item[:at], *item[at + 1 :]]


# --------------------------------------------------------------------------- the files

def _read(path: Path) -> list[str]:
    if not path.is_file():
        raise EditError(f'{path} does not exist; render it first with /simulation-ci')
    return path.read_text(encoding='utf-8').splitlines()


def write_lines(path: Path, lines: list[str]) -> None:
    """Write ``lines`` to a YAML file, refusing a result that does not parse."""
    _write(path, lines)


def _write(path: Path, lines: list[str]) -> None:
    text = '\n'.join(lines) + '\n'
    try:
        common.yaml_module().safe_load(text)
    except Exception as error:
        raise EditError(f'the edit would leave {path.name} unreadable; nothing was written') from error
    path.write_text(text, encoding='utf-8')


def report_exists(sim_dir: Path, explicit: Path | None = None) -> bool:
    if explicit is not None:
        return explicit.is_file()
    runs = sim_dir / 'artifacts' / 'runs'
    return runs.is_dir() and any(runs.glob('*/run.json'))


def _edit_ci_yaml(agent: Agent, plurai_bin: str | None, change: Callable[[list[str]], list[str]]) -> int:
    path = agent.sim_dir / common.CI_YAML
    before = path.read_text(encoding='utf-8') if path.is_file() else None
    _write(path, change(_read(path)))
    return _validate_or_restore(agent, path, before, plurai_bin)


def _validate_or_restore(agent: Agent, path: Path, before: str | None, plurai_bin: str | None) -> int:
    status, message = common.validate(agent.sim_dir, plurai_bin)
    if status == 0:
        print(f'EDITED {path.relative_to(agent.repo).as_posix()}')
        print(f'VALIDATED {agent.rel_sim}')
        return 0
    if status == -1:
        print(f'EDITED {path.relative_to(agent.repo).as_posix()}')
        print(common.unchecked_line(agent.rel_sim, message))
        return 0
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(before, encoding='utf-8')
    if status in common.VALIDATE_PASSTHROUGH:
        print(message)
        return status
    print(f'REFUSED {agent.rel_sim}: {message}')
    print(f'RESTORED {path.relative_to(agent.repo).as_posix()}')
    return 65


# --------------------------------------------------------------------------- agent.env

def add_agent_env(lines: list[str], names: Sequence[str]) -> list[str]:
    found = find_block(lines, 'agent.env')
    if found is None:
        return [*lines, f'agent.env: [{", ".join(names)}]']
    first, end = found
    value, _gap, _comment = _split_comment(lines[first].split(':', 1)[1])
    if value.startswith('['):
        current = [x.strip().strip('\'"') for x in (_FLOW_LIST.match(value) or re.match(r'(?P<items>)', '')).group('items').split(',') if x.strip()]  # type: ignore[union-attr]
        return set_flow_list(lines, 'agent.env', [*current, *(n for n in names if n not in current)])
    current = [m['name'].strip().strip('\'"') for m in (re.match(r'^\s*- (?P<name>[^#]*?)\s*(#.*)?$', x) for x in lines[first + 1 : end]) if m]
    return set_block_list(lines, 'agent.env', [*current, *(n for n in names if n not in current)])


def _config_file(agent: Agent) -> Path:
    """The config file that declares ``agent.env``: a file that merely mentions the key in a comment does not."""
    for path in common.config_paths(agent.sim_dir):
        document = common.read_yaml(path)
        if isinstance(document, dict) and 'agent.env' in document:
            return path
    return agent.sim_dir / common.CONFIG_NAME


def smoke_lines(agent: Agent, names: Sequence[str], regressions: Sequence[str] = (), *, catalog_given: bool = False) -> list[str]:
    """The lines of ``ci.yaml`` with its smoke list set to ``names``, every comment kept; a name outside the pattern and an empty list
    with no regression to run raise ``EditError``. An empty list is valid beside regressions, since they join every run; without a
    catalog none are known. A list that makes a run over the default size is allowed: ``cap_note`` words what it costs."""
    bad = [name for name in names if not common.SCENARIO_NAME.fullmatch(name)]
    if bad:
        raise EditError(f'{bad[0]!r} is not a scenario name: use letters, digits, ".", "_" and "-", starting with a letter or digit')
    if not names and not regressions:
        reason = 'the catalog has no regression_* scenario' if catalog_given else 'no --catalog shows a regression_* scenario to run instead'
        raise EditError(f'nothing selected: an empty smoke list leaves the PR run with no scenario ({reason}), and a run that selects nothing is '
                        '"not measured". Name at least one scenario, pass --catalog when the scenario set has regression_* scenarios, '
                        'or leave the list as it is.')
    return set_block_list(_read(agent.sim_dir / common.CI_YAML), 'smoke', names)


def current_smoke(agent: Agent) -> list[str]:
    """The names the smoke list of ``ci.yaml`` holds now."""
    document = common.read_yaml(agent.sim_dir / common.CI_YAML)
    smoke = document.get('smoke') if isinstance(document, dict) else None
    return [str(name) for name in smoke or []]


def cap_note(agent: Agent, names: Sequence[str], regressions: Sequence[str] = (), trials: int | None = None) -> str | None:
    """``NOTE ...`` when a smoke run of ``names``, the ``regressions`` and ``trials`` (the file's own when not given) is over the
    default size, else ``None``. It is information: nothing is refused for it."""
    note = common.smoke_cap_note(names, regressions, current_trials(agent) if trials is None else trials)
    return f'NOTE {agent.rel_sim}: {note}' if note else None


def _with_note(code: int, note: str | None) -> int:
    """``code``, after printing ``note`` when the edit it describes was kept (exit 0)."""
    if code == 0 and note:
        print(note)
    return code


def current_trials(agent: Agent) -> int:
    document = common.read_yaml(agent.sim_dir / common.CI_YAML)
    trials = document.get('trials') if isinstance(document, dict) else None
    return trials if isinstance(trials, int) and not isinstance(trials, bool) and trials >= 1 else common.DEFAULT_TRIALS


def _put_back(path: Path, before: str | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(before, encoding='utf-8')


def cmd_agent_env_add(agent: Agent, names: Sequence[str], plurai_bin: str | None = None) -> int:
    """Add names to ``agent.env`` and give the workflow's env region a line for each: only that region of the workflow is changed, so a hand
    edit of the managed block stays as the file holds it. A step that fails puts both files back."""
    for name in names:
        if not common.VARIABLE_NAME.fullmatch(name):
            raise EditError(f'{name!r} is not an environment variable name')
        if render.reserved_agent_env_reason(name) is not None:
            raise EditError(render.reserved_message(name))
    path = _config_file(agent)
    config_before = path.read_text(encoding='utf-8') if path.is_file() else None
    workflow = agent.repo / agent.workflow
    workflow_before = workflow.read_text(encoding='utf-8') if workflow.is_file() else None
    synced = False
    try:
        _write(path, add_agent_env(config_before.splitlines() if config_before is not None else [], names))
        if workflow_before is not None:
            updated = render.sync_env_region(workflow_before, common.agent_env(common.read_config(agent.sim_dir)))
            if updated != workflow_before:
                workflow.write_text(updated, encoding='utf-8')
                synced = True
    except (EditError, render.RenderError, ValueError, OSError):
        _put_back(path, config_before)
        _put_back(workflow, workflow_before)
        raise
    print(f'EDITED {path.relative_to(agent.repo).as_posix()}: agent.env now lists {", ".join(common.agent_env(common.read_config(agent.sim_dir)))}')
    if synced:
        print(f'SYNCED {agent.workflow}: an env line for each new name')
    status, message = common.validate(agent.sim_dir, plurai_bin)
    if status == 0:
        print(f'VALIDATED {agent.rel_sim}')
        return 0
    if status == -1:
        print(common.unchecked_line(agent.rel_sim, message))
        return 0
    _put_back(path, config_before)
    _put_back(workflow, workflow_before)
    if status in common.VALIDATE_PASSTHROUGH:
        print(message)
        return status
    print(f'REFUSED {agent.rel_sim}: {message}')
    print(f'RESTORED {path.relative_to(agent.repo).as_posix()} and {agent.workflow}')
    return 65


def run_gh(args: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(['gh', *args], capture_output=True, text=True, **common.utf8_child_text(), check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return 127, type(error).__name__
    return done.returncode, done.stdout


def github_names(agent: Agent, gh: Callable[[Sequence[str]], tuple[int, str]] = run_gh) -> tuple[list[str], list[str]] | None:
    """``(missing, present)`` among the names the workflow needs: ``PLURAI_TOKEN`` and ``agent.env`` as secrets or variables,
    ``PLURAI_URL`` as a variable when the workflow was rendered to read its backend URL from it. Names only; ``None`` when ``gh`` cannot list them.
    A reserved ``agent.env`` name raises ``EditError``: it is refused, not left out of the list."""
    declared = common.agent_env(common.read_config(agent.sim_dir))
    if reserved := render.reserved_names(declared):
        raise EditError(render.reserved_message(reserved[0]))
    slug = common.repo_slug(agent.repo)
    scope = ['--repo', '/'.join(slug)] if slug else []
    sec_code, sec = gh(['secret', 'list', '--json', 'name', *scope])
    var_code, var = gh(['variable', 'list', '--json', 'name', *scope])
    if sec_code or var_code:
        return None
    try:
        secrets = {row['name'] for row in json.loads(sec)}
        variables = {row['name'] for row in json.loads(var)}
    except (ValueError, KeyError, TypeError):
        return None
    wanted = [('PLURAI_TOKEN', secrets), *((n, secrets | variables) for n in declared)]
    missing = [name for name, where in wanted if name not in where]
    present = [name for name, where in wanted if name in where]
    if render.dev_url() and 'PLURAI_URL' not in variables:
        missing.append('PLURAI_URL (a repository variable)')
    return missing, present


# --------------------------------------------------------------------------- the state

def state(repo: Path, plurai_bin: str | None = None) -> list[dict[str, object]]:
    """One row per agent. A configured agent's row carries the readiness check's ``FIX`` lines, because a branch CI refused sends
    the user back here and the menu alone would hide why; its Dependabot offer follows whether ``pull_request`` is on."""
    rows = []
    for agent in common.discover_agents(repo):
        workflow_path = repo / agent.workflow
        workflow = workflow_path.is_file()
        ci_yaml = (agent.sim_dir / common.CI_YAML).is_file()
        report = report_exists(agent.sim_dir)
        events = list(render.workflow_events(workflow_path.read_text(encoding='utf-8'))) if workflow else []
        offers: list[str] = []
        fixes: list[str] = []
        if workflow and ci_yaml:
            # A workflow that does not parse names no events, and says nothing about pull_request either way.
            waits = bool(events) and 'pull_request' not in events
            dependabot = 'dependabot secrets (only useful once pull_request is uncommented)' if waits else 'dependabot secrets'
            offers = ['smoke list', 'trials', 'fail_on.tags', 'agent.env names', dependabot, 're-render the managed block', 'readiness check', 'remove this agent']
            if report:
                offers.insert(3, 'overrides')
            fixes = [finding.line() for finding in readiness.check_agent(agent, plurai_bin=plurai_bin) if finding.severity == 'fix']
        rows.append({
            'agent': agent.name, 'sim_dir': agent.rel_sim, 'workflow': workflow, 'ci_yaml': ci_yaml, 'report': report, 'configured': workflow and ci_yaml,
            'events': events, 'pull_request': 'pull_request' in events, 'fixes': fixes, 'offers': offers,
        })
    return rows


#: Where every file /simulation-ci writes lives: each simulation directory, the per-agent workflows (another workflow is the
#: user's, so it never joins the commit this skill proposes) and each agent's own ``plurai.config.yaml``, which can hold ``agent.env``.
_PENDING_PATHS = (':(glob)**/plurai_sim/**', ':(glob).github/workflows/plurai-*.yml')


def _pending_paths(repo: Path) -> list[str]:
    configs = [f':(literal){(agent.sim_dir.parent / common.CONFIG_NAME).relative_to(repo).as_posix()}' for agent in common.discover_agents(repo)]
    return [*_PENDING_PATHS, *configs]


def pending(repo: Path) -> list[tuple[str, str]]:
    """The changed files under every ``plurai_sim/``, in each agent's own ``plurai.config.yaml`` and in the ``.github/workflows/plurai-*.yml`` files, as ``git status`` reports them: (``XY``, path), by path.

    The files to commit come from git, never from a count of what the session wrote: an id fix can add a marker the user never
    named, and a re-render leaves earlier edits pending. A rename reads ``old -> new``.
    """
    result = common.git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--', *_pending_paths(repo))
    if result.returncode != 0:
        raise EditError(f'git status failed: {result.stderr.strip()}')
    entries = result.stdout.split('\0')
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if 'R' in code or 'C' in code:
            path = f'{entries[index]} -> {path}'
            index += 1
        found.append((code, path))
    return sorted(found, key=lambda item: item[1])


# --------------------------------------------------------------------------- command line

def _agent(args: argparse.Namespace) -> Agent:
    repo = common.repo_root(Path(args.repo))
    matches = [a for a in common.discover_agents(repo) if a.rel_sim == args.agent]
    if not matches:
        raise EditError(f'no plurai_sim/ directory at {args.agent!r}')
    if not matches[0].safe:
        raise EditError(f'the directory {args.agent!r} has characters that are not allowed in a workflow file or a command line; rename it')
    return matches[0]


def _list_arg(text: str, pattern: re.Pattern[str], what: str) -> list[str]:
    names = [n.strip() for n in text.split(',') if n.strip()]
    for name in names:
        if not pattern.fullmatch(name):
            raise EditError(f'{name!r} is not a {what}: use letters, digits, ".", "_" and "-", starting with a letter or digit')
    return list(dict.fromkeys(names))


def run(args: argparse.Namespace) -> int:
    if args.command == 'state':
        for row in state(common.repo_root(Path(args.repo)), args.plurai):
            print(json.dumps(row))
        return 0
    if args.command == 'pending':
        files = pending(common.repo_root(Path(args.repo)))
        for code, path in files:
            print(f'{code} {path}')
        if not files:
            print('PENDING none: no change under plurai_sim/ or in .github/workflows/plurai-*.yml')
        return 0
    agent = _agent(args)
    plurai = args.plurai
    regressions = render.catalog_regressions(getattr(args, 'catalog', None))
    if args.command == 'smoke':
        names = _list_arg(args.value, common.SCENARIO_NAME, 'scenario name')
        lines = smoke_lines(agent, names, regressions, catalog_given=getattr(args, 'catalog', None) is not None)
        return _with_note(_edit_ci_yaml(agent, plurai, lambda _lines: lines), cap_note(agent, names, regressions))
    if args.command == 'trials':
        if not re.fullmatch(r'[0-9]{1,3}', args.value):
            raise EditError('trials is a whole number of at least 2')
        if int(args.value) < common.MIN_TRIALS:
            raise EditError(f'trials {args.value} cannot pass: a cell needs at least {common.MIN_TRIALS} valid trials to be measured, so the check would always end "not measured". Use {common.MIN_TRIALS} or more.')
        note = cap_note(agent, current_smoke(agent), regressions, int(args.value))
        return _with_note(_edit_ci_yaml(agent, plurai, lambda lines: set_scalar(lines, 'trials', int(args.value))), note)
    if args.command == 'tags':
        tags = [t for t in args.value.split(',') if t]
        bad = [t for t in tags if t not in KNOWN_TAGS]
        if bad or not tags:
            raise EditError(f'the tags are {", ".join(KNOWN_TAGS)}; got {bad[0] if bad else "none"!r}')
        return _edit_ci_yaml(agent, plurai, lambda lines: set_flow_list(lines, 'tags', list(dict.fromkeys(tags)), indent=2))
    if args.command == 'env':
        if not common.ENV_ALIAS.fullmatch(args.value):
            raise EditError(f'the env alias {args.value!r} is not a name')
        return _edit_ci_yaml(agent, plurai, lambda lines: set_scalar(lines, 'env', args.value))
    if args.command == 'override':
        if args.action == 'add' and not report_exists(agent.sim_dir, args.report):
            raise EditError('overrides are offered once a report exists: run the suite first, or pass a downloaded CI run.json with --report')
        if args.action == 'add':
            new = override_lines(args.scenario, args.policy, args.reason, args.expires)
            return _edit_ci_yaml(agent, plurai, lambda lines: add_override(lines, new))
        if args.action == 'change':
            return _edit_ci_yaml(agent, plurai, lambda lines: change_override(lines, args.index, args.reason, args.expires))
        return _edit_ci_yaml(agent, plurai, lambda lines: remove_override(lines, args.index))
    if args.command == 'agent-env':
        return cmd_agent_env_add(agent, args.names, plurai)
    if args.command == 'github-names':
        result = github_names(agent)
        if result is None:
            print('UNKNOWN: gh could not list the secret and variable names; run `gh auth status`, then this again')
            return 0
        missing, present = result
        for name in present:
            print(f'PRESENT {name}')
        for name in missing:
            print(f'MISSING {name}')
        return 0
    if args.command == 'remove-agent':
        path = agent.repo / agent.workflow
        if not path.is_file():
            raise EditError(f'{agent.workflow} does not exist')
        path.unlink()
        print(f'REMOVED {agent.workflow}')
        print(f'BRANCH-PROTECTION: remove the required check "Plurai simulation ({agent.name})" from the protected branch settings; with no workflow behind it, it stays "expected" and blocks every merge.')
        print(f'KEPT {agent.rel_sim}/ (your files; delete it by hand if the agent is gone)')
        return 0
    raise EditError(f'unknown command {args.command}')


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__.split('\n\n')[0] if __doc__ else None)
    sub = top.add_subparsers(dest='command', required=True)

    def common_args(p: argparse.ArgumentParser, agent: bool = True) -> None:
        p.add_argument('--repo', default='.')
        p.add_argument('--plurai', default=None, help='the plurai runner; runs `plurai ci validate` after the edit')
        if agent:
            p.add_argument('--agent', required=True, metavar='REL_SIM')

    common_args(sub.add_parser('state'), agent=False)
    common_args(sub.add_parser('pending'), agent=False)
    for name in ('smoke', 'trials', 'tags', 'env'):
        p = sub.add_parser(name)
        common_args(p)
        p.add_argument('value')
        if name in ('smoke', 'trials'):
            p.add_argument('--catalog', type=Path, default=None, help='the scenario catalog JSON: its regression_* scenarios join every smoke run, so they count toward its size')
    override = sub.add_parser('override')
    override_sub = override.add_subparsers(dest='action', required=True)
    for action in ('add', 'change', 'remove'):
        p = override_sub.add_parser(action)
        common_args(p)
        if action == 'add':
            p.add_argument('--scenario', default=None)
            p.add_argument('--policy', default=None)
            p.add_argument('--reason', required=True)
            p.add_argument('--expires', default=None)
            p.add_argument('--report', type=Path, default=None, help='a downloaded CI run.json, when no local run exists')
        else:
            p.add_argument('--index', type=int, required=True, help='1-based position in overrides')
        if action == 'change':
            p.add_argument('--reason', default=None)
            p.add_argument('--expires', default=None, help='YYYY-MM-DD, or none to drop it')
    p = sub.add_parser('agent-env')
    agent_env_sub = p.add_subparsers(dest='action', required=True)
    add = agent_env_sub.add_parser('add')
    common_args(add)
    add.add_argument('names', nargs='+')
    for name in ('github-names', 'remove-agent'):
        common_args(sub.add_parser(name))
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (EditError, render.RenderError, ValueError) as error:
        print(f'simulation-ci: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.path.insert(0, str(common.SCRIPTS))
    from _stdio import utf8_stdio

    utf8_stdio()
    raise SystemExit(main())
